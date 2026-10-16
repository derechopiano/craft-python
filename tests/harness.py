"""Drives main.py over standard input and output as the craft apps do.

`Plugin(app, model)` starts `python main.py`, sends `initialize` with the app's `host.id` and
`host.features`, and answers the add-in's own requests (`host/sync` from the fake model,
`host/log`, `host/message`) while it waits for an answer. A command is one step: when the add-in
answers with an error, the model goes back to how it was (as the apps do); a good answer's
`commands` are run on the model."""

import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "main.py")
FEATURES = ["dialogs", "open", "events", "document", "push", "collab"]


class Plugin:
    def __init__(self, app="gridcraft", model=None, features=FEATURES, env=None, data=None, initialize=True):
        self.app = app
        self.model = model
        self.data = data or tempfile.mkdtemp(prefix="craft-python-test-")
        self._own_data = data is None
        e = dict(os.environ)
        e.pop("CRAFT_PYTHON_TRUST", None)
        e["CRAFT_PYTHON_DATA"] = self.data
        e["CRAFT_HOST"] = app
        e.update(env or {})
        self.proc = subprocess.Popen([sys.executable, MAIN], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, cwd=ROOT, env=e)
        self.lines = queue.Queue()
        self.stderr = []
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()
        self.next_id = 1
        self.host_requests = []
        if initialize:
            r = self.request("initialize", {"protocolVersion": 1, "host": {
                "id": app, "name": app.title(), "version": "0.7.0", "protocol": "2.0", "features": features,
                "language": "en"}, "plugin": {"id": "io.github.sirmmo.python", "dir": ROOT}})
            assert "result" in r, r

    def _read(self):
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def _read_err(self):
        for line in self.proc.stderr:
            self.stderr.append(line.decode("utf-8", "replace"))

    def send_raw(self, data):
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def send(self, msg):
        self.send_raw(json.dumps(msg).encode("utf-8") + b"\n")

    def next_line(self, timeout=30):
        line = self.lines.get(timeout=timeout)
        if line is None:
            raise EOFError("the add-in exited: " + "".join(self.stderr[-5:]))
        return line

    def request(self, method, params=None, timeout=30):
        rid = self.next_id
        self.next_id += 1
        self.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        return self.wait(rid, timeout)

    def wait(self, rid, timeout=30):
        while True:
            line = self.next_line(timeout)
            msg = json.loads(line)
            if "method" in msg:
                self.host_requests.append(msg)
                self.answer_host(msg)
                continue
            if msg.get("id") == rid:
                return msg

    def answer_host(self, msg):
        method, params = msg.get("method"), msg.get("params") or {}
        if "id" not in msg:
            return
        if method == "host/sync" and self.model is not None:
            results, err = self.model.sync(params.get("actions") or [])
            if err is not None:
                reply = {"jsonrpc": "2.0", "id": msg["id"],
                         "error": {"code": -32000, "message": err["message"], "data": err}}
            else:
                reply = {"jsonrpc": "2.0", "id": msg["id"], "result": {"results": results}}
        elif method in ("host/log",):
            reply = {"jsonrpc": "2.0", "id": msg["id"], "result": {}}
        elif method == "host/message":
            reply = {"jsonrpc": "2.0", "id": msg["id"], "result": {"result": 1}}
        else:
            reply = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "unknown"}}
        self.send(reply)

    def run(self, command, params=None, context=None, timeout=60):
        """`commands/run` as one step; the add-in's answer (rolled back on an error)."""
        snap = self.model.snapshot() if self.model is not None else None
        if context is None:
            context = self.model.context() if self.model is not None else {}
        r = self.request("commands/run", {"command": command, "params": params or {}, "context": context}, timeout)
        if self.model is not None:
            if "error" in r:
                self.model.restore(snap)
            else:
                for c in r["result"].get("commands") or []:
                    self.model.command(c["command"], c.get("params") or {})
        return r

    def call(self, name, *args):
        return self.request("functions/call", {"name": name, "args": list(args)})

    def event(self, event, **params):
        return self.request("events/" + event, params)

    def close(self):
        try:
            self.send({"jsonrpc": "2.0", "method": "shutdown"})
        except (BrokenPipeError, OSError, ValueError):
            pass
        try:
            self.proc.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        for f in (self.proc.stdout, self.proc.stderr):
            try:
                f.close()
            except Exception:
                pass
        if self._own_data:
            shutil.rmtree(self.data, ignore_errors=True)


def result(r):
    assert "result" in r, r
    return r["result"]


def error(r):
    assert "error" in r, r
    return r["error"]["message"]
