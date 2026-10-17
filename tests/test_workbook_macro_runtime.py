"""Worker failure and bounded-transport checks using disposable Python children."""
import sys
import os
import time
import unittest

from craftpython import workbook_projects as runtime


class WorkerTransport(unittest.TestCase):
    def start(self, source):
        child = runtime.WorkerProcess([sys.executable, '-u', '-c', source])
        self.addCleanup(self.stop, child)
        return child

    @staticmethod
    def stop(child):
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
        child.close_pipes()

    def test_stuck_worker_has_a_deadline(self):
        child = self.start('import time; time.sleep(60)')
        with self.assertRaisesRegex(RuntimeError, 'time limit'):
            runtime.receive(child, time.monotonic() + 0.1)

    def test_messages_do_not_extend_deadline(self):
        child = self.start('while True:\n print(\'{"kind":"output"}\', flush=True)')
        deadline = time.monotonic() + 0.3
        count = 0
        with self.assertRaisesRegex(RuntimeError, 'time limit'):
            while True:
                runtime.receive(child, deadline)
                count += 1
        self.assertGreater(count, 0)

    def test_startup_error_is_reported_and_bounded(self):
        child = self.start("import sys; sys.stderr.write('x'*100000+'missing dependency'); sys.exit(1)")
        with self.assertRaisesRegex(RuntimeError, 'missing dependency') as caught:
            runtime.receive(child, time.monotonic() + 5)
        self.assertLess(len(str(caught.exception)), 8400)

    def test_oversized_message_is_rejected(self):
        child = self.start("import sys; sys.stdout.write('x'*(16*1024*1024+1)); sys.stdout.flush()")
        with self.assertRaisesRegex(RuntimeError, 'Oversized'):
            runtime.receive(child, time.monotonic() + 5)

    def test_failed_startup_discards_worker(self):
        # Environment setup errors must not retain a process or a temporary module directory.
        import tempfile
        project = {'metadata': '# /// script\n# dependencies = []\n# ///\n', 'modules': {}}
        runtime.close_worker()
        with tempfile.TemporaryDirectory() as cache:
            runtime.configure(lambda *_: {'documentId': 1, 'project': project},
                              uv_executable=cache + '/missing-uv-executable', cache=cache)
            runtime.environment_key = None
            with self.assertRaises(OSError):
                runtime.handle('macros/run', {'name': 'analysis.run'})
            self.assertIsNone(runtime.worker)
            self.assertIsNone(runtime.module_dir)

    def test_infinite_macro_is_stopped_and_next_run_recovers(self):
        import hashlib
        import pathlib
        import tempfile
        from unittest.mock import patch
        project = {'metadata': '# /// script\n# dependencies = []\n# ///\n',
                   'modules': {'analysis': 'def stuck():\n    while True:\n        pass\ndef good():\n    pass\n'}}
        runtime.close_worker()
        self.addCleanup(runtime.close_worker)
        with tempfile.TemporaryDirectory() as cache:
            runtime.configure(lambda *_: {'documentId': 1, 'project': project}, cache=cache)
            # Reuse this already available interpreter; this test must not invoke uv or download anything.
            bootstrap = pathlib.Path(runtime.__file__).with_name('macro_worker.py').read_text(encoding='utf-8')
            runtime.environment_key = hashlib.sha256((project['metadata'] + '\n' + bootstrap).encode()).hexdigest()
            runtime.environment_python = sys.executable
            with patch.object(runtime, 'RUN_SECONDS', 1):
                with self.assertRaisesRegex(RuntimeError, 'time limit'):
                    runtime.handle('macros/run', {'name': 'analysis.stuck'})
            self.assertIsNone(runtime.worker)
            self.assertIsNone(runtime.module_dir)
            self.assertEqual(runtime.handle('macros/run', {'name': 'analysis.good'}), {})
            runtime.close_worker()


    def test_workbook_write_uses_protocol_literal_values(self):
        import hashlib
        import pathlib
        import tempfile
        project = {'metadata': '# /// script\n# dependencies = []\n# ///\n',
                   'modules': {'analysis': "import gridcraft as gc\ndef write_text():\n    gc.active_workbook().write('A1', [['00123', '=not a formula', '1/2']])\n"}}
        calls = []
        def host(method, params):
            if method == 'host/pythonProject/get':
                return {'documentId': 1, 'project': project}
            calls.append((method, params))
            return {}
        runtime.close_worker()
        self.addCleanup(runtime.close_worker)
        with tempfile.TemporaryDirectory() as cache:
            runtime.configure(host, cache=cache)
            bootstrap = pathlib.Path(runtime.__file__).with_name('macro_worker.py').read_text(encoding='utf-8')
            runtime.environment_key = hashlib.sha256((project['metadata'] + '\n' + bootstrap).encode()).hexdigest()
            runtime.environment_python = sys.executable
            runtime.handle('macros/run', {'name': 'analysis.write_text'})
            self.assertEqual(calls, [('host/engine.execute', {'command': 'range.setValues', 'params': {
                'range': 'A1', 'values': [['00123', '=not a formula', '1/2']], 'literal': True}})])
            runtime.close_worker()


class IdleWorker(unittest.TestCase):
    def setUp(self):
        import hashlib
        import pathlib
        import tempfile
        runtime.close_worker()
        self.cache = tempfile.TemporaryDirectory()
        self.addCleanup(self.cache.cleanup)
        self.addCleanup(runtime.close_worker)
        self.project = {'metadata': '# /// script\n# dependencies = []\n# ///\n',
                        'modules': {'analysis': 'import time\ndef quick():\n    pass\ndef slow():\n    time.sleep(0.3)\n'}}
        runtime.configure(lambda *_: {'documentId': 1, 'project': self.project}, cache=self.cache.name)
        bootstrap = pathlib.Path(runtime.__file__).with_name('macro_worker.py').read_text(encoding='utf-8')
        runtime.environment_key = hashlib.sha256((self.project['metadata'] + '\n' + bootstrap).encode()).hexdigest()
        runtime.environment_python = sys.executable

    def test_idle_worker_releases_process_and_module_directory(self):
        from unittest.mock import patch
        with patch.object(runtime, 'IDLE_SECONDS', 0.1):
            runtime.handle('macros/run', {'name': 'analysis.quick'})
            child = runtime.worker
            self.assertIsNotNone(child)
            deadline = time.monotonic() + 5
            while runtime.worker is not None and time.monotonic() < deadline:
                time.sleep(0.02)
            with runtime._runtime_lock:  # Wait for the retirement callback to finish cleanup.
                self.assertIsNone(runtime.worker)
                self.assertIsNotNone(child.wait(timeout=5))
                self.assertIsNone(runtime.module_dir)

    def test_idle_deadline_does_not_interrupt_active_macro(self):
        from unittest.mock import patch
        with patch.object(runtime, 'IDLE_SECONDS', 0.1):
            runtime.handle('macros/run', {'name': 'analysis.quick'})
            child = runtime.worker
            self.assertEqual(runtime.handle('macros/run', {'name': 'analysis.slow'}), {})
            self.assertIs(runtime.worker, child)
            self.assertIsNone(child.poll())


@unittest.skipUnless(os.name == 'nt', 'Windows job-object lifetime checks')
class WindowsWorkerTree(unittest.TestCase):
    @staticmethod
    def spawn_source(exit_parent=False):
        return ("import sys, subprocess, json, time\n"
                "sys.stdin.readline()\n"
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                "print(json.dumps({'pid': child.pid}), flush=True)\n" +
                ("sys.exit(0)\n" if exit_parent else "time.sleep(60)\n"))

    def watch(self, pid):
        # Keep an actual process handle: never terminate by a possibly reused PID.
        import ctypes
        from ctypes import wintypes as w
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        kernel.OpenProcess.restype = w.HANDLE
        kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        kernel.WaitForSingleObject.restype = w.DWORD
        kernel.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
        kernel.TerminateProcess.restype = w.BOOL
        kernel.CloseHandle.argtypes = [w.HANDLE]
        kernel.CloseHandle.restype = w.BOOL
        handle = kernel.OpenProcess(0x100000 | 0x1, False, pid)
        self.assertTrue(handle, 'fixture descendant should still be alive')
        def cleanup():
            if kernel.WaitForSingleObject(handle, 0) == 258:
                kernel.TerminateProcess(handle, 1)
                kernel.WaitForSingleObject(handle, 5000)
            kernel.CloseHandle(handle)
        self.addCleanup(cleanup)
        return lambda ms: kernel.WaitForSingleObject(handle, ms)

    def test_killing_worker_also_kills_its_descendant(self):
        child = runtime.WorkerProcess([sys.executable, '-u', '-c', self.spawn_source()])
        self.addCleanup(WorkerTransport.stop, child)
        child.stdin.write('\n'); child.stdin.flush()
        pid = runtime.receive(child, time.monotonic() + 5)['pid']
        wait = self.watch(pid)
        self.assertEqual(wait(0), 258)
        child.kill()
        self.assertEqual(wait(5000), 0, 'descendant survived worker termination')

    def test_normal_worker_exit_does_not_leave_descendant_holding_pipes(self):
        child = runtime.WorkerProcess([sys.executable, '-u', '-c', self.spawn_source(True)])
        self.addCleanup(WorkerTransport.stop, child)
        child.stdin.write('\n'); child.stdin.flush()
        wait = self.watch(runtime.receive(child, time.monotonic() + 5)['pid'])
        self.assertEqual(child.wait(timeout=5), 0)
        self.assertEqual(wait(0), 258, 'fixture must outlive its parent for this check')
        started = time.monotonic()
        child.close_pipes()
        self.assertEqual(wait(5000), 0)
        self.assertLess(time.monotonic() - started, 3, 'inherited pipes blocked cleanup')

    def test_provider_crash_closes_owned_worker_tree(self):
        import json
        import queue
        import subprocess
        import threading
        source = ("from craftpython import workbook_projects as runtime\nimport sys, json, time\n"
                  "child = runtime.WorkerProcess([sys.executable, '-u', '-c', " + repr(self.spawn_source()) + "])\n"
                  "child.stdin.write('\\n'); child.stdin.flush()\n"
                  "print(json.dumps(runtime.receive(child, time.monotonic()+5)), flush=True)\n"
                  "time.sleep(60)\n")
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        owner = subprocess.Popen([sys.executable, '-u', '-c', source], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, **options)
        lines = queue.Queue(maxsize=1)
        reader = threading.Thread(target=lambda: lines.put(owner.stdout.readline()), daemon=True)
        reader.start()
        try:
            wait = self.watch(json.loads(lines.get(timeout=5))['pid'])
            self.assertEqual(wait(0), 258)
            owner.kill()  # Kill only the provider, not its subprocess tree ourselves.
            owner.wait(timeout=5)
            self.assertEqual(wait(5000), 0, 'provider crash left an orphan worker descendant')
        finally:
            if owner.poll() is None:
                owner.kill()
            owner.wait(timeout=5)
            reader.join(timeout=1)
            owner.stdout.close(); owner.stderr.close()


@unittest.skipUnless(os.name == 'posix', 'POSIX process-group ownership')
class PosixWorkerTree(unittest.TestCase):
    spawn_source = staticmethod(WindowsWorkerTree.spawn_source)
    test_killing_worker_also_kills_its_descendant = WindowsWorkerTree.test_killing_worker_also_kills_its_descendant
    test_normal_worker_exit_does_not_leave_descendant_holding_pipes = WindowsWorkerTree.test_normal_worker_exit_does_not_leave_descendant_holding_pipes
    test_provider_crash_closes_owned_worker_tree = WindowsWorkerTree.test_provider_crash_closes_owned_worker_tree

    def watch(self, pid):
        import pathlib

        def alive():
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return False
            # Linux can leave a killed orphan as a zombie briefly before init reaps it.
            # It is already stopped and has released its pipes. Other POSIX hosts reap it.
            try:
                state = pathlib.Path('/proc/{}/stat'.format(pid)).read_text().rsplit(')', 1)[1].split()[0]
                if state == 'Z':
                    return False
            except (OSError, IndexError):
                pass
            return True

        self.assertTrue(alive(), 'fixture descendant should still be alive')
        group = os.getpgid(pid)
        self.assertNotEqual(group, os.getpgrp(), 'fixture must own a separate process group')

        def cleanup():
            try:
                if alive() and os.getpgid(pid) == group:
                    os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass

        import signal
        self.addCleanup(cleanup)

        def wait(ms):
            deadline = time.monotonic() + ms / 1000
            while alive():
                if time.monotonic() >= deadline:
                    return 258
                time.sleep(0.01)
            return 0

        return wait


class DownloadPolicy(unittest.TestCase):
    def test_downloads_are_off_by_default_and_source_builds_stay_off(self):
        import pathlib
        from unittest.mock import patch
        with patch.object(runtime, 'CACHE_DIR', pathlib.Path('cache')):
            offline = runtime.uv_command('script.py')
            online = runtime.uv_command('script.py', True)
        self.assertIn('--offline', offline)
        self.assertIn('--no-python-downloads', offline)
        self.assertNotIn('--offline', online)
        self.assertNotIn('--no-python-downloads', online)
        for command in (offline, online):
            for flag in ('--no-build', '--no-config', '--no-env-file', '--no-project'):
                self.assertIn(flag, command)

    def test_only_the_hosts_top_level_boolean_can_grant_downloads(self):
        from unittest.mock import patch
        for flag in (None, False, 'true', True):
            result = {'documentId': 1, 'project': {'metadata': '', 'modules': {}, 'allowDownloads': True}}
            if flag is not None: result['allowDownloads'] = flag
            with patch.object(runtime, 'HOST_CALL', lambda *_: result):
                runtime.project()
            self.assertEqual(runtime.ALLOW_DOWNLOADS, flag is True)
        runtime.ALLOW_DOWNLOADS = False


if __name__ == '__main__':
    unittest.main()
