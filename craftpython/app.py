"""The add-in's main loop: answers the app's requests (initialize, functions/call, commands/run,
events/*), one at a time, and never stops for a bad message or a bug in a command."""

import builtins
import traceback

from . import PLUGIN_ID, VERSION, functions, runner
from .commands import COMMANDS, Ctx, PluginError
from .functions import FunctionError
from .trust import Trust
from .wire import Channel, HostGone, log

PROTOCOL_VERSION = 1


class Unknown(Exception):
    """An unknown method, command or function (JSON-RPC -32601)."""


class App:
    def __init__(self, channel):
        self.channel = channel
        self.host = "gridcraft"
        self.host_name = ""
        self.host_version = ""
        self.features = set()
        self.language = "en"
        self.trust = Trust()
        # The workspace scripts, the console and PY.EVAL share (like a Python prompt's globals).
        self.workspace = {"__name__": "__console__", "__builtins__": builtins, "__doc__": None}
        self.last_code = ""
        self.last_file = ""
        self.last_output = "show"
        self.last_console = ""
        self.transcript = []

    def has(self, feature):
        return feature in self.features

    def log(self, text):
        log(text)

    # ------------------------------------------------------------------ requests

    def handle(self, method, params):
        if method == "initialize":
            return self.initialize(params)
        if method == "functions/call":
            return functions.call(params.get("name", ""), params.get("args", []), self)
        if method == "commands/run":
            return self.run_command(params)
        if method.startswith("events/"):
            return self.event(method[len("events/"):], params)
        raise Unknown("unknown method " + method[:100])

    def initialize(self, params):
        host = params.get("host")
        host = host if isinstance(host, dict) else {}
        self.host = str(host.get("id") or host.get("name") or "gridcraft").lower()[:64]
        self.host_name = str(host.get("name") or "")[:64]
        self.host_version = str(host.get("version") or "")[:64]
        feats = host.get("features")
        self.features = {str(f) for f in feats} if isinstance(feats, list) else set()
        self.language = str(host.get("language") or "en")[:32]
        return {"protocolVersion": PROTOCOL_VERSION, "name": "Python", "version": VERSION, "id": PLUGIN_ID}

    def run_command(self, params):
        name = params.get("command")
        cmd = COMMANDS.get(name) if isinstance(name, str) else None
        if cmd is None:
            raise Unknown("unknown command")
        ctx = Ctx(self, params.get("params"), params.get("context"))
        if ctx.known:
            # Formulas follow the document the last command ran in.
            self.trust.set_active(ctx.path, ctx.name)
        reply = cmd(self, ctx)
        return reply if isinstance(reply, dict) else {}

    def event(self, name, params):
        path = params.get("path") if isinstance(params.get("path"), str) else None
        doc_name = params.get("name") if isinstance(params.get("name"), str) else None
        if name == "documentOpened":
            self.trust.set_active(path, doc_name)
        elif name == "documentSaved":
            was_new = self.trust.known and self.trust.is_new(self.trust.path, self.trust.name)
            self.trust.set_active(path, doc_name)
            if was_new and path and not self.trust.trusted(path, doc_name):
                return {"message": "Python: {} isn't trusted yet (Python > Trust This {})".format(
                    doc_name or path, "Workbook" if self.host == "gridcraft" else "Document" if self.host == "wordcraft" else "Presentation")}
        elif name == "documentClosed":
            self.trust.forget(path)
        return {}

    def answer(self, msg):
        rid = msg.get("id")
        method = msg.get("method")
        params = msg.get("params")
        params = params if isinstance(params, dict) else {}
        try:
            reply = {"jsonrpc": "2.0", "id": rid, "result": self.handle(method, params)}
        except HostGone:
            raise
        except Unknown as e:
            reply = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": str(e)}}
        except (PluginError, FunctionError) as e:
            reply = {"jsonrpc": "2.0", "id": rid, "error": {"code": 1, "message": str(e)[:4000]}}
        except runner.ScriptTimeout:
            reply = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": "stopped: it took too long"}}
        except (Exception, KeyboardInterrupt) as e:  # a bug here must not stop the add-in
            log("error in {}: {}".format(method, "".join(traceback.format_exception(type(e), e, e.__traceback__))[-3000:]))
            reply = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": "internal error: {!r}".format(e)[:500]}}
        self.channel.send(reply)


def main():
    runner.install()
    app = App(Channel())
    while not app.channel.shutdown:
        try:
            msg = app.channel.read()
            if msg is None:
                continue
            method = msg.get("method")
            if not isinstance(method, str):
                continue  # a late answer to one of our requests
            if "id" not in msg:
                if method == "shutdown":
                    break
                continue  # other notifications
            app.answer(msg)
            if method == "shutdown":
                break
        except HostGone:
            break
        except (BrokenPipeError, ConnectionError):
            break
        except (runner.ScriptTimeout, KeyboardInterrupt):
            continue  # a late interruption
        except Exception as e:  # never stop for one message
            log("error: {!r}".format(e))
