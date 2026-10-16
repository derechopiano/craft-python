"""Running people's Python: time limits, captured output, the value of the last line, tracebacks.

A time limit interrupts the script from a watchdog thread: it raises `ScriptTimeout` in the main
thread (through SIGINT, which only this add-in's handler sees). `ScriptTimeout` derives from
BaseException, so `except Exception:` in a script doesn't swallow it, and the watchdog keeps
interrupting every quarter second until the script has stopped. While the script waits for the
app (`doc` calls), the interruption waits too. Code stuck inside one long call into C (a huge
`10 ** 10 ** 8`, `time.sleep(600)` on Windows) is stopped when that call returns; if it never does,
the app gives up on the add-in and starts it again.
"""

import ast
import io
import linecache
import os
import signal
import sys
import threading
import time
import traceback

# Most characters of printed output kept per run.
MAX_OUTPUT = 200000

_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
_lock = threading.Lock()
_current = [None]  # the running Deadline


def limit(env, default):
    """Seconds from the environment variable `env` (shorter only), else `default`."""
    try:
        v = float(os.environ.get(env, ""))
    except ValueError:
        return default
    return min(max(v, 0.1), default) if v == v else default


class ScriptTimeout(BaseException):
    """The script ran past its time."""


class Deadline:
    """`with Deadline(seconds):` stops the code inside after `seconds`."""

    def __init__(self, seconds):
        self.seconds = float(seconds)
        self.fired = False
        self.paused = 0
        self._done = threading.Event()
        self._thread = None

    def __enter__(self):
        with _lock:
            _current[0] = self
        self._thread = threading.Thread(target=self._watch, name="craft-python-deadline", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        with _lock:
            self._done.set()
            if _current[0] is self:
                _current[0] = None
        return False

    def _watch(self):
        if self._done.wait(self.seconds):
            return
        while True:
            with _lock:
                if self._done.is_set():
                    return
                self.fired = True
                if not self.paused:
                    _interrupt()
            if self._done.wait(0.25):
                return


class waiting_for_host:
    """While the add-in waits for the app, a time limit doesn't interrupt (the protocol stays whole)."""

    def __enter__(self):
        with _lock:
            d = _current[0]
            if d is not None:
                d.paused += 1
        self._d = d
        return self

    def __exit__(self, *exc):
        if self._d is not None:
            with _lock:
                self._d.paused -= 1
        return False


def _interrupt():
    try:
        if hasattr(signal, "pthread_kill"):
            # A real signal to the main thread also ends a sleep or a blocking read there.
            signal.pthread_kill(threading.main_thread().ident, signal.SIGINT)
        else:
            import _thread
            _thread.interrupt_main()
    except Exception:
        pass


def _on_sigint(signum, frame):
    d = _current[0]
    if d is not None and d.fired and not d.paused:
        raise ScriptTimeout()
    # Otherwise: a Ctrl+C meant for the app, or a late interruption. Ignored.


def install():
    """Takes over SIGINT (the main thread only)."""
    try:
        signal.signal(signal.SIGINT, _on_sigint)
    except (ValueError, OSError, AttributeError):
        pass


class Capture(io.TextIOBase):
    """What a script prints, up to `limit` characters."""

    def __init__(self, limit=MAX_OUTPUT):
        io.TextIOBase.__init__(self)
        self.limit = limit
        self.parts = []
        self.size = 0
        self.truncated = False

    @property
    def encoding(self):
        return "utf-8"

    def writable(self):
        return True

    def isatty(self):
        return False

    def write(self, s):
        if not isinstance(s, str):
            raise TypeError("write() argument must be str, not " + type(s).__name__)
        room = self.limit - self.size
        if room > 0:
            part = s[:room]
            self.parts.append(part)
            self.size += len(part)
        if len(s) > max(room, 0):
            self.truncated = True
        return len(s)

    def getvalue(self):
        return "".join(self.parts)


class Outcome:
    """What a run gave: `value` (of the last line, when it is an expression), `output`, `error`."""

    def __init__(self):
        self.value = None
        self.has_value = False
        self.output = ""
        self.truncated = False
        self.error = None        # the traceback, as text
        self.error_line = None   # its last line ("ZeroDivisionError: division by zero")
        self.exception = None
        self.timed_out = False
        self.seconds = 0.0
        self.written = None      # where Run Script wrote the value (GridCraft)
        self.changed = False     # whether it sent the app any change

    @property
    def ok(self):
        return self.error is None


_MAIN = os.path.join(os.path.dirname(_PACKAGE_DIR), "main.py")


def compile_code(code, filename="<script>"):
    """(statements, last expression or None): the last line's value is shown, as in a console."""
    # The source for tracebacks (formatted right after the run, so one name per kind is enough).
    linecache.cache[filename] = (len(code), None, [l + "\n" for l in code.splitlines()], filename)
    tree = ast.parse(code, filename, "exec")
    last = None
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last = ast.Expression(tree.body.pop().value)
        ast.fix_missing_locations(last)
    body = compile(tree, filename, "exec")
    return body, (compile(last, filename, "eval") if last is not None else None)


def _user_frames(tb):
    """The traceback's frames that are the script's own (not this add-in's)."""
    def mine(f):
        if f.filename.startswith("<"):
            return False
        path = os.path.abspath(f.filename)
        return path.startswith(_PACKAGE_DIR + os.sep) or path == _MAIN

    return [f for f in traceback.extract_tb(tb) if not mine(f)]


def format_exception(exc):
    if isinstance(exc, SyntaxError):
        lines = traceback.format_exception_only(type(exc), exc)
        return "".join(lines).rstrip(), lines[-1].strip() if lines else "SyntaxError"
    frames = _user_frames(exc.__traceback__)
    out = []
    if frames:
        out.append("Traceback (most recent call last):\n")
        out.extend(traceback.format_list(frames))
    only = traceback.format_exception_only(type(exc), exc)
    out.extend(only)
    return "".join(out).rstrip(), (only[-1].strip() if only else type(exc).__name__)


def run(code, namespace, seconds, filename="<script>"):
    """Runs `code` in `namespace` for at most `seconds`; never raises (but HostGone)."""
    from .wire import HostGone

    out = Outcome()
    capture = Capture()
    saved = (sys.stdout, sys.stderr, sys.stdin)
    start = time.monotonic()
    try:
        try:
            body, last = compile_code(code, filename)
        except (SyntaxError, ValueError, OverflowError, MemoryError, RecursionError) as e:
            out.error, out.error_line = format_exception(e)
            out.exception = e
            return out
        sys.stdout = capture
        sys.stderr = capture
        sys.stdin = io.StringIO("")
        with Deadline(seconds) as d:
            try:
                exec(body, namespace)
                if last is not None:
                    out.value = eval(last, namespace)
                    out.has_value = True
            except ScriptTimeout:
                out.timed_out = True
                out.error = "The script ran longer than {:g} seconds and was stopped.".format(seconds)
                out.error_line = "Stopped after {:g} seconds".format(seconds)
            except HostGone:
                raise
            except SystemExit as e:
                if e.code not in (None, 0):
                    out.error = "SystemExit: {}".format(e.code)
                    out.error_line = out.error
            except BaseException as e:  # the script's own errors, KeyboardInterrupt included
                out.exception = e
                out.error, out.error_line = format_exception(e)
            if d.fired and out.error is None:
                out.timed_out = True
        # A late interruption, if any, lands here.
        try:
            time.sleep(0)
        except ScriptTimeout:
            pass
    except ScriptTimeout:
        out.timed_out = True
        out.error = "The script ran longer than {:g} seconds and was stopped.".format(seconds)
        out.error_line = "Stopped after {:g} seconds".format(seconds)
    finally:
        sys.stdout, sys.stderr, sys.stdin = saved
        out.output = capture.getvalue()
        out.truncated = capture.truncated
        out.seconds = time.monotonic() - start
    return out
