"""Experimental workbook-macro extension to Python Tools.

Uses the shared GridCraft macro host. This module is loaded only in explicit
--workbook-macros mode; ordinary functions and WordCraft need no uv runtime.
The host owns UI cancellation and workbook transactions; this module owns the worker tree.
"""

import ast, hashlib, json, os, pathlib, subprocess, sys, tempfile, traceback
import queue
import threading
import time
from .process_tree import ProcessTree

HOST_CALL = None
UV_EXECUTABLE = 'uv'
PYTHON_INTERPRETER = None
CACHE_DIR = None
ALLOW_DOWNLOADS = False
MAX = 16 * 1024 * 1024
STARTUP_SECONDS = 30
RUN_SECONDS = 30
IDLE_SECONDS = 60
_runtime_lock = threading.RLock()
_idle_timer = None
_idle_token = None
worker, worker_key, module_dir, document_id = None, None, None, None
environment_key, environment_python = None, None

def configure(host_call, uv_executable='uv', interpreter=None, cache=None):
    global HOST_CALL, UV_EXECUTABLE, PYTHON_INTERPRETER, CACHE_DIR
    HOST_CALL, UV_EXECUTABLE, PYTHON_INTERPRETER = host_call, uv_executable, interpreter
    if cache is None:
        base = pathlib.Path(os.environ.get('LOCALAPPDATA') or os.environ.get('XDG_CACHE_HOME') or pathlib.Path.home()/'.cache')
        cache = base/'GridCraft'/'PythonMacros'
    CACHE_DIR = pathlib.Path(cache)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

class WorkerProcess:
    """Bounded pipe readers let the calling thread enforce an absolute deadline."""

    def __init__(self, command, **options):
        self.tree = ProcessTree()
        if os.name == 'nt':
            options['creationflags'] = options.get('creationflags', 0) | subprocess.CREATE_NO_WINDOW
        else:
            options['start_new_session'] = True
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', **options)
            # User modules cannot run until the startup handshake and a later run request.
            self.tree.attach(self.process)
        except BaseException:
            self.tree.close()
            if getattr(self, 'process', None) is not None:
                self.process.kill()
                self.process.wait(timeout=5)
                for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                    stream.close()
            raise
        self.stdin, self.stdout = self.process.stdin, self.process.stdout
        self.messages = queue.Queue(maxsize=1)
        self.stopped = threading.Event()
        self.diagnostics = b''
        self.readers = [threading.Thread(target=f, daemon=True)
                        for f in (self._read_messages, self._read_errors)]
        try:
            for reader in self.readers:
                reader.start()
        except BaseException:
            self.kill()
            self.process.wait(timeout=5)
            self.close_pipes()
            raise

    def _read_messages(self):
        try:
            while not self.stopped.is_set():
                line = self.stdout.buffer.readline(MAX + 1)
                message = line if len(line) <= MAX else RuntimeError('Oversized worker message')
                while not self.stopped.is_set():
                    try:
                        self.messages.put(message, timeout=0.1)
                        break
                    except queue.Full:
                        pass
                if not line or isinstance(message, Exception):
                    return
        except (OSError, ValueError) as error:
            try:
                self.messages.put_nowait(RuntimeError(str(error)))
            except queue.Full:
                pass

    def _read_errors(self):
        try:
            while not self.stopped.is_set():
                chunk = os.read(self.process.stderr.fileno(), 4096)
                if not chunk:
                    return
                self.diagnostics = (self.diagnostics + chunk)[-8192:]
        except (OSError, ValueError):
            pass

    def poll(self):
        return self.process.poll()

    def wait(self, timeout=None):
        return self.process.wait(timeout=timeout)

    def kill(self):
        self.tree.close()
        if self.process.poll() is None:
            self.process.kill()

    def close_pipes(self):
        self.tree.close()  # Also close descendants after a normal interpreter exit.
        self.stopped.set()
        for reader in self.readers:
            if reader.ident is not None:
                reader.join(timeout=1)
        for stream in (self.stdin, self.stdout, self.process.stderr):
            stream.close()


def receive(child, deadline):
    left = deadline - time.monotonic()
    if left <= 0:
        raise RuntimeError('Python worker exceeded its time limit')
    try:
        message = child.messages.get(timeout=left)
    except queue.Empty as error:
        raise RuntimeError('Python worker exceeded its time limit') from error
    if isinstance(message, Exception):
        raise message
    if not message:
        child.readers[1].join(timeout=0.1)
        detail = child.diagnostics.decode('utf-8', errors='replace').strip()
        raise RuntimeError('Python worker exited before replying' + (': ' + detail if detail else ''))
    return json.loads(message)

def host(method, params):
    if HOST_CALL is None:
        raise RuntimeError('Macro provider has not been initialized')
    return HOST_CALL(method, params)

def project():
    global document_id, ALLOW_DOWNLOADS
    result = host('host/pythonProject/get', {})
    document_id = result['documentId']
    ALLOW_DOWNLOADS = result.get('allowDownloads') is True
    return result['project'] or {
        'metadata': '# /// script\n# requires-python = ">=3.11"\n# dependencies = []\n# ///\n',
        'modules': {},
    }

def _close_worker():
    global worker, worker_key, module_dir
    if worker is not None:
        child, worker = worker, None
        try:
            # Do not write to a potentially full pipe when stopping a busy macro.
            child.kill()
            child.wait(timeout=5)
        finally:
            child.close_pipes()
    if module_dir is not None:
        module_dir.cleanup()
        module_dir = None
    worker_key = None

def _cancel_idle():
    global _idle_timer, _idle_token
    if _idle_timer is not None:
        _idle_timer.cancel()
    _idle_timer, _idle_token = None, None


def close_worker():
    with _runtime_lock:
        _cancel_idle()
        _close_worker()


def _retire_when_idle(child):
    global _idle_timer, _idle_token
    _cancel_idle()
    token = object()
    _idle_token = token
    def retire():
        with _runtime_lock:
            # A cancelled callback may already be waiting for the lock. Identity alone
            # is insufficient when the same warm interpreter has just run another macro.
            if _idle_token is token and worker is child:
                close_worker()
    _idle_timer = threading.Timer(IDLE_SECONDS, retire)
    _idle_timer.daemon = True
    _idle_timer.start()


def uv_command(script, allow_downloads=False):
    command = [UV_EXECUTABLE, 'run', '--no-config', '--no-project', '--no-env-file',
               '--no-build', '--cache-dir', str(CACHE_DIR/'uv')]
    if not allow_downloads:
        command += ['--offline', '--no-python-downloads']
    if PYTHON_INTERPRETER:
        command += ['--python', PYTHON_INTERPRETER]
    return command + ['--script', str(script), '--prepare']


def get_worker(p, deadline):
    global worker, worker_key, module_dir, environment_key, environment_python
    key = (document_id, hashlib.sha256(json.dumps(p,sort_keys=True).encode()).hexdigest())
    if worker is not None and worker_key == key and worker.poll() is None:
        return worker, pathlib.Path(module_dir.name)
    close_worker()
    folder = CACHE_DIR
    module_dir = tempfile.TemporaryDirectory(prefix='modules-', dir=folder)
    modules = pathlib.Path(module_dir.name)
    for name, source in p['modules'].items():
        (modules / (name+'.py')).write_text(source, encoding='utf-8')
    bootstrap = pathlib.Path(__file__).with_name('macro_worker.py').read_text(encoding='utf-8')
    source = p['metadata']+'\n'+bootstrap
    env_key = hashlib.sha256(source.encode()).hexdigest()
    scripts = folder/'scripts'
    scripts.mkdir(exist_ok=True)
    script = scripts/(env_key+'.py')
    try:
        with script.open('x',encoding='utf-8') as out:
            out.write(source)
    except FileExistsError:
        with script.open('r',encoding='utf-8') as stored:
            if stored.read(len(source)+1) != source:
                raise RuntimeError('Macro bootstrap cache differs; choose another cache folder')
    # Resolve with uv once per environment; source changes need a fresh interpreter,
    # not another environment-resolution subprocess. Never reuse across metadata changes.
    if environment_key == env_key and environment_python and pathlib.Path(environment_python).is_file():
        command = [environment_python, str(script)]
    else:
        command = uv_command(script, ALLOW_DOWNLOADS)
    env = os.environ.copy()
    env.pop('PYTHONPATH', None)
    env.update(PYTHONUTF8='1', PYTHONUNBUFFERED='1')
    deadline = min(deadline, time.monotonic() + STARTUP_SECONDS)
    worker = WorkerProcess(command, cwd=folder, env=env)
    ready = receive(worker, deadline)
    if ready.get('kind') != 'ready' or not isinstance(ready.get('interpreter'),str):
        close_worker()
        raise RuntimeError('Worker did not complete its startup handshake')
    environment_key, environment_python = env_key, ready['interpreter']
    if command[-1] == '--prepare':
        # uv prepares the environment, then exits. Keep only the interpreter alive.
        if worker.wait(timeout=5) != 0:
            close_worker()
            raise RuntimeError('uv environment preparation failed')
        worker.close_pipes()
        worker = WorkerProcess([environment_python, str(script)], cwd=folder, env=env)
        ready = receive(worker, deadline)
        if ready.get('kind') != 'ready':
            close_worker()
            raise RuntimeError('Prepared interpreter failed its startup handshake')
    worker_key = key
    return worker, modules

def _handle(method, params):
    if method == 'initialize':
        features = params['host'].get('features', [])
        if not all(f in features for f in ('pythonProjects', 'engineCommands', 'literalValues')):
            raise RuntimeError('This Python provider requires the Python-project host extension')
        return {'protocolVersion': 1}
    p = project()
    if method == 'macros/modules':
        return {'modules': [{'name': n, 'kind': 'standard', 'source': s} for n,s in p['modules'].items()]}
    if method == 'macros/list':
        macros = []
        for name, source in p['modules'].items():
            for node in ast.parse(source, filename=name+'.py').body:
                if isinstance(node, ast.FunctionDef) and not node.name.startswith('_'):
                    a = node.args
                    if not (a.posonlyargs or a.args or a.kwonlyargs or a.vararg or a.kwarg):
                        macros.append({'name': name+'.'+node.name, 'module': name, 'procedure': node.name})
        return {'macros': macros}
    if method == 'macros/saveModule':
        p['modules'][params['name']] = params['source']
        return host('host/pythonProject/set', {'project': p})
    if method == 'macros/deleteModule':
        p['modules'].pop(params['name'], None)
        return host('host/pythonProject/set', {'project': p})
    if method == 'macros/run':
        # Keep a warm worker for this workbook and exact project; never share module state between books.
        try:
            deadline = time.monotonic() + RUN_SECONDS
            child, modules = get_worker(p, deadline)
            child.stdin.write(json.dumps({'action':'run', 'macro':params['name'], 'modulePath':str(modules)})+'\n')
            child.stdin.flush()
            while True:
                message = receive(child, deadline)
                if message['kind'] == 'command':
                    result = host('host/engine.execute', {'command':message['command'], 'params':message['params']})
                    child.stdin.write(json.dumps({'result':result})+'\n')
                    child.stdin.flush()
                elif message['kind'] == 'output':
                    host('host/log', {'text':message.get('text','')})
                elif message['kind'] == 'failed':
                    raise RuntimeError(message['error'])
                elif message['kind'] == 'done':
                    return {}
                else:
                    raise RuntimeError('Unexpected worker message')
        except BaseException:
            close_worker()
            raise
    raise ValueError('Unsupported method: '+method)


def handle(method, params):
    # Serialize lifecycle changes with idle retirement, including host round trips.
    with _runtime_lock:
        if method == 'macros/run':
            _cancel_idle()
        try:
            return _handle(method, params)
        finally:
            if method == 'macros/run' and worker is not None:
                _retire_when_idle(worker)
