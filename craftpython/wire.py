"""The protocol's transport: JSON-RPC 2.0, one message per line, on standard input and output.

At start-up the add-in keeps private copies of its standard input and output for the protocol and
points file descriptors 0 and 1 elsewhere (0 at the null device, 1 at standard error, which the
app writes to its log). So a program a script starts, or C code that prints, can neither read the
app's messages nor break the add-in's answers.
"""

import json
import math
import os
import sys
import threading

# The longest line read (the apps write at most 64 MiB).
MAX_LINE = 64 << 20


class HostGone(Exception):
    """The app closed the add-in's standard input."""


class HostError(Exception):
    """The app answered one of the add-in's requests with an error."""

    def __init__(self, code, message, data=None):
        Exception.__init__(self, message)
        self.code = code
        self.message = message
        self.data = data


def log(text):
    """A line in the app's log (standard error)."""
    err = sys.__stderr__ or sys.stderr  # never a script's captured output
    try:
        err.write(str(text)[:4000] + "\n")
        err.flush()
    except Exception:  # a closed log must not stop the add-in
        pass


def _clean(v, depth=0):
    """`v` with what JSON can't hold made plain: NaN and infinities become null, other objects text."""
    if depth > 200:
        return None
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, (str, int, bool)) or v is None:
        return v
    if isinstance(v, dict):
        return {str(k): _clean(x, depth + 1) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x, depth + 1) for x in v]
    return str(v)


def dumps(msg):
    """One message as UTF-8 bytes (no newline)."""
    try:
        text = json.dumps(msg, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (ValueError, TypeError, RecursionError):
        text = json.dumps(_clean(msg), ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return text.encode("utf-8", "replace")


def _streams():
    try:
        in_fd = os.dup(0)
        out_fd = os.dup(1)
    except OSError:
        return sys.stdin.buffer, sys.stdout.buffer
    inp = os.fdopen(in_fd, "rb", buffering=1 << 16)
    out = os.fdopen(out_fd, "wb", buffering=1 << 16)
    try:
        null = os.open(os.devnull, os.O_RDONLY)
        os.dup2(null, 0)
        os.close(null)
    except OSError:
        pass
    try:
        sys.stdout.flush()
        os.dup2(2, 1)
    except (OSError, ValueError):
        pass
    return inp, out


class Channel:
    """The add-in's end of the conversation with the app."""

    def __init__(self, inp=None, out=None):
        if inp is None or out is None:
            inp, out = _streams()
        self._in = inp
        self._out = out
        self._lock = threading.Lock()
        self._next_id = 1
        self.shutdown = False

    def send(self, msg):
        data = dumps(msg) + b"\n"
        with self._lock:
            self._out.write(data)
            self._out.flush()

    def read(self):
        """The next message (a dict), or None for a line that isn't one. Raises HostGone at the end."""
        line = self._in.readline(MAX_LINE + 1)
        if not line:
            raise HostGone()
        if len(line) > MAX_LINE and not line.endswith(b"\n"):
            while True:  # the rest of an over-long line
                rest = self._in.readline(1 << 20)
                if not rest or rest.endswith(b"\n"):
                    break
            log("ignored a message longer than 64 MiB")
            return None
        text = line.decode("utf-8", "replace").strip()
        if not text:
            return None
        try:
            msg = json.loads(text)
        except Exception:  # broken JSON, or nested too deep
            log("not JSON: " + text[:200])
            return None
        return msg if isinstance(msg, dict) else None

    def request(self, method, params):
        """Asks the app something while it waits for one of the add-in's answers (protocol 1.1)."""
        rid = self._next_id
        self._next_id += 1
        self.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            msg = self.read()
            if msg is None:
                continue
            if "method" in msg:
                if msg.get("method") == "shutdown":
                    self.shutdown = True
                elif "id" in msg:
                    # The app is waiting for us; it doesn't ask anything meanwhile.
                    self.send({"jsonrpc": "2.0", "id": msg["id"],
                               "error": {"code": -32000, "message": "busy"}})
                continue
            if msg.get("id") != rid:
                continue  # a late answer to a request given up on
            err = msg.get("error")
            if err is not None:
                if not isinstance(err, dict):
                    err = {"message": str(err)}
                raise HostError(err.get("code"), str(err.get("message") or "the app refused"), err.get("data"))
            return msg.get("result")
