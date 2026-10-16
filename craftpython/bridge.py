"""The app's object model through `host/sync` (protocol 1.1): the Office bridge's actions.

Each action names an object by its path from the document's root (`{"p": property}`,
`{"m": method, "a": args}`) and loads properties, sets one, or calls a method. A `doc` operation
is one `host/sync`; inside `with doc.batch():` writes wait and go together with the next read (or
at the end of the block). Everything a script changes is part of its command's one undo step.
"""

import contextlib
import threading

from . import runner
from .wire import HostError

# Most actions in one host/sync (the apps' limit).
MAX_ACTIONS = 10000
# Methods that only read.
READS = {"search", "getSelectedData"}


class BridgeError(Exception):
    """The app refused an action: `code` (ItemNotFound, InvalidArgument…) and its message."""

    def __init__(self, code, message, action=None):
        Exception.__init__(self, "{}: {}".format(code, message) if code else message)
        self.code = code
        self.message = message
        self.action = action


class Bridge:
    def __init__(self, channel, host_id):
        self.channel = channel
        self.host = host_id
        self.active = True
        self.queue = []
        self.batching = 0
        self.syncs = 0
        self.writes = 0
        # Engine commands to run after the script, in the same undo step (`doc.command`).
        self.commands = []

    def close(self):
        self.active = False
        self.queue = []

    def _sync(self, actions):
        if not self.active:
            raise RuntimeError("`doc` works only while the script that got it runs")
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("`doc` works only from the script's own thread")
        results = []
        for i in range(0, len(actions), MAX_ACTIONS):
            chunk = actions[i:i + MAX_ACTIONS]
            self.syncs += 1
            try:
                with runner.waiting_for_host():
                    answer = self.channel.request("host/sync", {"actions": chunk})
            except HostError as e:
                data = e.data if isinstance(e.data, dict) else {}
                action = data.get("action")
                if isinstance(action, int):
                    action += i
                raise BridgeError(data.get("code") or "", data.get("message") or e.message, action) from None
            got = answer.get("results") if isinstance(answer, dict) else None
            if not isinstance(got, list):
                raise BridgeError("GeneralException", "the app answered host/sync without results")
            results.extend(got)
        return results

    def run(self, actions):
        """Sends the waiting writes and `actions` together; the results of `actions`."""
        waiting = self.queue
        self.queue = []
        if not waiting and not actions:
            return []
        results = self._sync(waiting + list(actions))
        return results[len(waiting):]

    def flush(self):
        if self.queue:
            self.run([])

    def write(self, action):
        self.writes += 1
        if self.batching:
            if len(self.queue) >= 100000:
                self.flush()
            self.queue.append(action)
        else:
            self.run([action])

    def load(self, path, props):
        r = self.run([{"op": "load", "path": path, "props": list(props)}])
        return r[0] if r and isinstance(r[0], dict) else {}

    def set(self, path, prop, value):
        self.write({"op": "set", "path": path, "prop": prop, "value": value})

    def call(self, path, method, args=(), result=True):
        """Calls a method; with `result` False it may wait in a batch (no answer is needed)."""
        action = {"op": "call", "path": path, "method": method, "args": list(args)}
        if method not in READS:
            self.writes += 1
        if not result:
            self.write(action)
            return None
        r = self.run([action])
        return r[0] if r else None

    @contextlib.contextmanager
    def batch(self):
        self.batching += 1
        ok = False
        try:
            yield
            ok = True
        finally:
            self.batching -= 1
            if self.batching == 0:
                if ok:
                    self.flush()
                else:
                    self.queue = []  # the block failed: its writes never happened

    def command(self, command_id, params=None):
        if not isinstance(command_id, str) or not command_id:
            raise ValueError("a command id is text, such as \"home.bold\"")
        if command_id.startswith("plugins."):
            raise ValueError("add-in commands can't run plugins.* commands")
        if params is not None and not isinstance(params, dict):
            raise ValueError("params is a dict")
        if len(self.commands) >= 10000:
            raise ValueError("at most 10,000 commands")
        self.commands.append({"command": command_id, "params": params or {}})

    def log(self, text):
        with runner.waiting_for_host():
            self.channel.request("host/log", {"text": str(text)[:4000]})


class Base:
    """Shared by the three apps' `doc`."""

    def __init__(self, bridge, context):
        self._b = bridge
        self._context = context if isinstance(context, dict) else {}

    @property
    def app(self):
        """`gridcraft`, `wordcraft` or `deckcraft`."""
        return self._b.host

    @property
    def document(self):
        """The document's file: `{"path", "name", "dirty"}` (path None when never saved)."""
        d = self._context.get("document")
        return dict(d) if isinstance(d, dict) else {"path": None, "name": None, "dirty": None}

    def batch(self):
        """`with doc.batch():` sends the writes inside together (fewer round trips)."""
        return self._b.batch()

    def command(self, command_id, params=None):
        """Queues an app command (`"home.bold", {"range": "A1"}`) to run after the script, in the
        same undo step. See `gridcraft-cli commands` (or the app's) for the ids."""
        self._b.command(command_id, params)

    def log(self, text):
        """Writes a line to the app's log."""
        self._b.log(text)

    def sync(self, actions):
        """Sends raw bridge actions (see the app's docs/officejs.md); their results."""
        if not isinstance(actions, list):
            raise ValueError("actions is a list")
        self._b.writes += 1
        return self._b.run(actions)
