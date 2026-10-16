"""The add-in's commands: Python scripts, the console, trust, About, and the text and number tools."""

import json
import os
import platform
import re
import reprlib
import statistics
import sys

from . import VERSION, runner
from .bridge import Bridge, BridgeError
from .functions import fn_stats
from .trust import data_dir, document_of, trust_all, trust_file
from .values import ValueError_, from_wire, is_error, offset, to_rows

# Seconds a script or a console line may run (the apps give a command 60 s of silence).
SCRIPT_SECONDS = 50
# A message dialog holds 4,000 characters.
MAX_PROMPT = 3900
# Largest script file read.
MAX_FILE = 4 << 20


class PluginError(Exception):
    """Fails the command with this message (nothing it did is kept)."""


# ------------------------------------------------------------------------------ the app's words

NOUN = {"gridcraft": "workbook", "wordcraft": "document", "deckcraft": "presentation"}
APP_NAME = {"gridcraft": "GridCraft", "wordcraft": "WordCraft", "deckcraft": "DeckCraft"}


def noun(app):
    return NOUN.get(app.host, "document")


def off_message(app):
    return "Python is turned off for this {0}: Python > Trust This {1}".format(noun(app), noun(app).title())


# ------------------------------------------------------------------------------ dialogs

def clip(text, limit=MAX_PROMPT):
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit - 30].rstrip() + "\n… (" + str(len(text) - limit + 30) + " more characters)"


def message(title, prompt, buttons=None, ok=None, command=None, params=None):
    d = {"kind": "message", "title": title[:200], "prompt": clip(prompt)}
    if buttons:
        d["buttons"] = buttons[:4]
    if ok:
        d["okLabel"] = ok
    if command:
        d["command"] = command
        d["params"] = params or {}
    return d


def needs_dialogs(app, what):
    if not app.has("dialogs"):
        raise PluginError("This version of {} can't show the add-in's dialogs: {}".format(
            APP_NAME.get(app.host, "the app"), what))


_repr = reprlib.Repr()
_repr.maxlevel = 4
_repr.maxlist = _repr.maxtuple = _repr.maxset = _repr.maxfrozenset = _repr.maxdeque = 100
_repr.maxdict = 50
_repr.maxstring = 2000
_repr.maxother = 2000
_repr.maxlong = 400


def show(value):
    try:
        return _repr.repr(value)
    except Exception as e:  # a broken __repr__
        return "<{} (repr failed: {})>".format(type(value).__name__, e)


def jsonable(value):
    """The value for `plugins.run`'s caller: itself when it is plain JSON, else its repr."""
    try:
        text = json.dumps(value, allow_nan=False)
        if len(text) <= 1 << 20:
            return value
    except (TypeError, ValueError, RecursionError, OverflowError):
        pass
    return show(value)


# ------------------------------------------------------------------------------ the command's context

class Ctx:
    def __init__(self, app, params, context):
        self.app = app
        self.params = params if isinstance(params, dict) else {}
        self.context = context if isinstance(context, dict) else {}
        fields = self.params.get("fields")
        self.fields = fields if isinstance(fields, dict) else None
        self.known, self.path, self.name = document_of(self.context)
        self.console_line = None

    def get(self, key, default=None):
        """A value from the dialog's fields, else from the params."""
        if self.fields is not None and key in self.fields:
            return self.fields.get(key)
        return self.params.get(key, default)

    @property
    def interactive(self):
        """Run from one of the add-in's dialogs (the person is there to read a traceback)."""
        return self.fields is not None or bool(self.params.get("dialog"))

    def trusted(self):
        return self.known and self.app.trust.trusted(self.path, self.name) or trust_all()

    @property
    def title(self):
        return self.name or ("this " + noun(self.app))


def make_doc(app, bridge, context):
    if app.host == "wordcraft":
        from .word import Document
        return Document(bridge, context)
    if app.host == "deckcraft":
        from .deck import Presentation
        return Presentation(bridge, context)
    from .grid import Workbook
    return Workbook(bridge, context)


def selection_value(app, context):
    if app.host == "wordcraft":
        sel = context.get("selection")
        return sel.get("text") if isinstance(sel, dict) else None
    if app.host == "deckcraft":
        return context.get("text")
    v = context.get("values")
    return from_wire(v) if isinstance(v, list) else None


# ------------------------------------------------------------------------------ running Python

def multiline(app):
    feats = app.features
    return "multiline" in feats or "dialogs.multiline" in feats or "multilineFields" in feats


def one_line_hint(app):
    if multiline(app):
        return ""
    return ("\n\nThe box is one line in this version of {}: separate statements with ; "
            "(import math; math.pi * 2), or put a longer script in a .py file.").format(APP_NAME.get(app.host, "the app"))


def read_script(ctx, path):
    path = os.path.expanduser(str(path).strip().strip('"'))
    if not os.path.isabs(path):
        base = os.path.dirname(ctx.path) if ctx.path else os.getcwd()
        path = os.path.join(base, path)
    path = os.path.abspath(path)
    try:
        if os.path.getsize(path) > MAX_FILE:
            raise PluginError("{} is larger than 4 MB".format(path))
        with open(path, "rb") as fh:
            data = fh.read(MAX_FILE + 1)
    except OSError as e:
        raise PluginError("Can't read {}: {}".format(path, e.strerror or e))
    try:
        return data.decode("utf-8-sig"), path
    except UnicodeDecodeError:
        raise PluginError("{} isn't UTF-8 text".format(path))


def run_python(app, ctx, code, filename, output=None):
    """Runs code in the shared workspace with `doc`; (outcome, bridge)."""
    bridge = Bridge(app.channel, app.host)
    doc = make_doc(app, bridge, ctx.context)
    ns = app.workspace
    ns["doc"] = doc
    ns["x"] = selection_value(app, ctx.context)
    seconds = runner.limit("CRAFT_PYTHON_SCRIPT_SECONDS", SCRIPT_SECONDS)
    t = ctx.params.get("timeout")
    if isinstance(t, (int, float)) and not isinstance(t, bool) and t == t:
        seconds = min(max(float(t), 0.1), seconds)
    try:
        out = runner.run(code, ns, seconds, filename=filename)
        if out.ok:
            try:
                bridge.flush()
                if output in ("below", "sheet") and out.has_value and out.value is not None:
                    out.written = write_value(doc, out.value, output)
            except (BridgeError, ValueError, KeyError, IndexError) as e:
                out.error = out.error_line = "{}: {}".format(type(e).__name__, e)
    finally:
        bridge.close()
    out.changed = bridge.writes > 0
    return out, bridge


def write_value(doc, value, where):
    """Writes a script's value into the workbook: below the selection or on a new sheet."""
    try:
        rows = to_rows(value, empty=None)
    except ValueError_:
        raise ValueError("the value has more than 1,000,000 cells")
    if where == "sheet":
        names = {s.name.lower() for s in doc.sheets}
        name = "Python Output"
        n = 2
        while name.lower() in names:
            name = "Python Output {}".format(n)
            n += 1
        target = doc.add_sheet(name).range("A1")
    else:
        sel = doc.selection
        target = sel.cell(sel.row_count + 1, 0)
    target.values = rows
    written = target._grown or target
    return written.address


def describe(out, code_line=None):
    """The text a run shows: printed output, then the value or the traceback."""
    tail = []
    if out.error:
        tail.append(out.error)
        if out.changed:
            tail.append("\nChanges made before the error stay: Undo takes back the whole run.")
    elif out.has_value and out.value is not None:
        tail.append(show(out.value))
    if getattr(out, "written", None):
        tail.append("Written at " + out.written)
    tail_text = "\n".join(tail)
    head = []
    if code_line:
        head.append(">>> " + code_line)
    if out.output:
        head.append(out.output.rstrip("\n"))
    if out.truncated:
        head.append("[more output was left out]")
    head_text = "\n".join(head)
    room = MAX_PROMPT - len(tail_text) - 2
    if room < 200:
        tail_text = tail_text[-(MAX_PROMPT - 400):]
        room = 200
    if len(head_text) > room:
        head_text = head_text[:room - 40].rstrip() + "\n… (output cut: see result.output)"
    return (head_text + ("\n" if head_text and tail_text else "") + tail_text) or "Done (no output)."


def result_of(out):
    r = {"output": out.output, "seconds": round(out.seconds, 3)}
    if out.truncated:
        r["truncated"] = True
    if out.has_value:
        r["value"] = jsonable(out.value)
    if out.error:
        r["error"] = out.error
    if getattr(out, "written", None):
        r["written"] = out.written
    return r


AGAIN = {"runScript": "Edit Script…", "console": "Again…", "runFile": "Run a File…"}


def finish(app, ctx, out, bridge, title, again):
    """The command's answer for a run."""
    if not out.ok and not ctx.interactive:
        # Programmatic runs are all or nothing: the app takes back what the script changed.
        raise PluginError("Python: " + clip(out.error or "failed", 3000))
    reply = {"result": result_of(out)}
    if bridge.commands:
        reply["commands"] = bridge.commands
    if out.ok:
        reply["message"] = "Python: ran in {:.2f} s".format(out.seconds)
    else:
        reply["message"] = "Python: " + (out.error_line or "failed")
    shows = out.output or not out.ok or (out.has_value and out.value is not None) or again == "console"
    if ctx.interactive and app.has("dialogs") and shows:
        reply["dialog"] = message(title, describe(out, ctx.console_line),
                                  buttons=[{"label": AGAIN.get(again, "Again…"), "command": again,
                                            "params": {"edit": True}}])
    return reply


def offer_trust(app, ctx, then):
    """Untrusted: a dialog that offers to trust the document and go on (programmatic runs fail)."""
    if not ctx.known:
        raise PluginError("This version of {} doesn't say which {} is open, so Python stays off (it needs 0.7 or later).".format(
            APP_NAME.get(app.host, "the app"), noun(app)))
    if not app.has("dialogs"):
        raise PluginError(off_message(app))
    return {
        "message": off_message(app),
        "dialog": message(
            "Trust {}?".format(ctx.title),
            risk_text(app, ctx) + "\n\nTrust it and go on?",
            ok="Trust and Continue", command="trustConfirm",
            params={"path": ctx.path, "name": ctx.name, "then": then}),
    }


def risk_text(app, ctx):
    n = noun(app)
    extra = ""
    if app.host == "gridcraft":
        extra = (" PY.EVAL formulas in it run on every recalculation, and a workbook someone sends you can "
                 "carry them.")
    where = ctx.path or "{} (not saved yet: trusted until the add-in stops)".format(ctx.title)
    return ("Python code can do anything you can: read, change or delete your files, send them over the "
            "internet, start programs.{} Trust only {}s you know.\n\n{}").format(extra, n, where)


def _given(v):
    return isinstance(v, str) and bool(v.strip())


def cmd_run_script(app, ctx):
    code = ctx.get("code")
    path = ctx.get("file") or ctx.get("path")
    output = ctx.get("output") if app.host == "gridcraft" else None
    if output not in (None, "", "show", "below", "sheet"):
        raise PluginError("output is \"show\", \"below\" or \"sheet\"")
    if output:
        app.last_output = output
    if _given(code):
        app.last_code = code
    if ctx.params.get("edit") or not (_given(code) or _given(path)):
        # Asking for the code: an untrusted document is offered trust first.
        if not ctx.trusted() and ctx.known and app.has("dialogs"):
            return offer_trust(app, ctx, "runScript")
        return script_form(app, "Type some Python, or give a .py file to run." if ctx.fields is not None else "")
    if not ctx.trusted():
        if ctx.interactive:
            return offer_trust(app, ctx, "runScript")
        raise PluginError(off_message(app) if ctx.known else "The app didn't say which {} is open, so Python stays off.".format(noun(app)))
    if _given(path):
        code, filename = read_script(ctx, path)
        app.last_file = filename
    else:
        filename = "<script>"
    out, bridge = run_python(app, ctx, code, filename, output or None)
    return finish(app, ctx, out, bridge, "Python Script", "runScript")


def script_form(app, problem=""):
    needs_dialogs(app, "give the code in the params: {\"code\": \"...\"} or {\"file\": \"/path/script.py\"}")
    n = noun(app)
    prompt = ("`doc` is this {0}, `x` the selection. print() output and the last line's value show "
              "after the run; all its changes are one undo step.").format(n)
    if problem:
        prompt = problem + "\n\n" + prompt
    fields = [
        {"id": "code", "label": "Python", "kind": "text", "value": app.last_code, "multiline": True},
        {"id": "file", "label": "Or a .py file", "kind": "text", "value": ""},
    ]
    if app.host == "gridcraft":
        fields.append({"id": "output", "label": "The last line's value", "kind": "choice",
                       "value": app.last_output or "show",
                       "options": [{"id": "show", "label": "Show it"},
                                   {"id": "below", "label": "Write it below the selection"},
                                   {"id": "sheet", "label": "Write it on a new sheet"}]})
    return {"dialog": {"kind": "form", "title": "Run Python Script", "prompt": clip(prompt + one_line_hint(app)),
                       "fields": fields, "command": "runScript", "params": {}, "okLabel": "Run"}}


def cmd_run_file(app, ctx):
    path = ctx.get("path") or ctx.get("file")
    if ctx.params.get("edit") or not _given(path):
        if not ctx.trusted() and ctx.known and app.has("dialogs"):
            return offer_trust(app, ctx, "runFile")
        needs_dialogs(app, "give the file in the params: {\"path\": \"/path/script.py\"}")
        prompt = "The .py file to run (`doc` is this {}, `x` the selection).".format(noun(app))
        if ctx.fields is not None:
            prompt = "Give the path of a .py file.\n\n" + prompt
        return {"dialog": {"kind": "form", "title": "Run Python File", "prompt": prompt,
                           "fields": [{"id": "path", "label": "File", "kind": "text", "value": app.last_file}],
                           "command": "runFile", "params": {}, "okLabel": "Run"}}
    if not ctx.trusted():
        app.last_file = str(path)
        if ctx.interactive:
            return offer_trust(app, ctx, "runFile")
        raise PluginError(off_message(app))
    code, filename = read_script(ctx, path)
    app.last_file = filename
    out, bridge = run_python(app, ctx, code, filename)
    return finish(app, ctx, out, bridge, "Python: " + os.path.basename(filename), "runFile")


def cmd_console(app, ctx):
    code = ctx.get("code")
    if ctx.params.get("edit") and ctx.fields is None:
        code = None
    if not _given(code):
        if not ctx.trusted() and ctx.known and app.has("dialogs"):
            return offer_trust(app, ctx, "console")
        needs_dialogs(app, "give the code in the params: {\"code\": \"...\"}")
        transcript = "\n".join(app.transcript)[-1500:]
        prompt = ("Python runs here and keeps what you define (functions, values, imports) until the add-in "
                  "stops; scripts and PY.EVAL see it too. `doc` is this {}.").format(noun(app))
        if transcript:
            prompt = transcript + "\n\n" + prompt
        pending, app.last_console = app.last_console, ""
        return {"dialog": {"kind": "form", "title": "Python Console", "prompt": clip(prompt + one_line_hint(app)),
                           "fields": [{"id": "code", "label": ">>>", "kind": "text", "value": pending, "multiline": True}],
                           "command": "console", "params": {}, "okLabel": "Run"}}
    if not ctx.trusted():
        if ctx.interactive:
            app.last_console = code
            return offer_trust(app, ctx, "console")
        raise PluginError(off_message(app))
    ctx.console_line = code if "\n" not in code else code.split("\n", 1)[0] + " …"
    out, bridge = run_python(app, ctx, code, "<console>")
    app.transcript.append(">>> " + ctx.console_line)
    app.transcript.append(describe(out)[:600])
    del app.transcript[:-40]
    return finish(app, ctx, out, bridge, "Python Console", "console")


# ------------------------------------------------------------------------------ trust

def cmd_trust(app, ctx):
    if not ctx.known:
        raise PluginError("This version of {} doesn't say which {} is open; Python needs 0.7 or later.".format(
            APP_NAME.get(app.host, "the app"), noun(app)))
    if ctx.trusted():
        why = " (a new {} is trusted until it is saved)".format(noun(app)) if app.trust.is_new(ctx.path, ctx.name) else ""
        return {"message": "{} is already trusted{}".format(ctx.title, why), "result": {"trusted": True}}
    needs_dialogs(app, "run trustConfirm with {\"path\", \"name\"} of the active " + noun(app))
    return {"dialog": message("Trust {}?".format(ctx.title), risk_text(app, ctx), ok="Trust",
                              command="trustConfirm", params={"path": ctx.path, "name": ctx.name}),
            "result": {"trusted": False}}


def cmd_trust_confirm(app, ctx):
    if not ctx.known:
        raise PluginError("The app didn't say which {} is open.".format(noun(app)))
    want_path, want_name = ctx.params.get("path"), ctx.params.get("name")
    from .trust import norm
    same = (norm(want_path) == norm(ctx.path)) if (want_path and ctx.path) else (not want_path and not ctx.path and want_name == ctx.name)
    if not same:
        raise PluginError("The active {} isn't the one to trust any more: run Trust again.".format(noun(app)))
    if ctx.path:
        try:
            app.trust.add(ctx.path)
        except OSError as e:
            raise PluginError("Can't write {}: {}".format(trust_file(), e.strerror or e))
    elif not app.trust.is_new(ctx.path, ctx.name) and ctx.name:
        app.trust.session.add(ctx.name)
    app.trust.set_active(ctx.path, ctx.name)
    msg = "{} is trusted".format(ctx.title)
    if app.host == "gridcraft":
        msg += "; Formulas > Calculate Now (F9) runs its Python formulas"
    then = ctx.params.get("then")
    if then in ("runScript", "runFile", "console") and app.has("dialogs"):
        # Back to what was asked, now that Python is on.
        nxt = COMMANDS[then](app, Ctx(app, {}, ctx.context))
        nxt["message"] = msg
        return nxt
    return {"message": msg, "result": {"trusted": True}}


def cmd_untrust(app, ctx):
    if not ctx.known:
        raise PluginError("The app didn't say which {} is open.".format(noun(app)))
    if ctx.path:
        try:
            app.trust.remove([ctx.path])
        except OSError as e:
            raise PluginError("Can't write {}: {}".format(trust_file(), e.strerror or e))
        msg = "Python is off for {}".format(ctx.title)
    elif ctx.name in app.trust.session:
        app.trust.session.discard(ctx.name)
        msg = "Python is off for {}".format(ctx.title)
    elif app.trust.is_new(ctx.path, ctx.name):
        msg = "A new {0} is trusted until it's saved; once saved, it is off until you trust its file".format(noun(app))
    else:
        msg = "{} wasn't trusted".format(ctx.title)
    if trust_all():
        msg += " (but CRAFT_PYTHON_TRUST=all trusts everything)"
    return {"message": msg, "result": {"trusted": ctx.trusted()}}


def cmd_trusted(app, ctx):
    paths = app.trust.paths()
    result = {"documents": paths, "dataDir": data_dir()}
    if not app.has("dialogs"):
        return {"result": result, "message": "{} trusted documents".format(len(paths))}
    if not paths:
        return {"result": result, "dialog": message("Trusted Documents", "No document is trusted yet.\n\nThe list is " + trust_file())}
    items = [{"id": p, "label": os.path.basename(p) or p, "detail": os.path.dirname(p)[:256],
              "icon": "lock"} for p in paths[:10000]]
    return {"result": result, "dialog": {
        "kind": "list", "title": "Trusted Documents", "multi": True, "items": items,
        "prompt": "Python runs in these files (for GridCraft, WordCraft and DeckCraft). Select some to remove.",
        "command": "untrustPaths", "params": {}, "okLabel": "Remove"}}


def cmd_untrust_paths(app, ctx):
    ids = ctx.params.get("ids")
    if ids is None and ctx.params.get("id"):
        ids = [ctx.params.get("id")]
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise PluginError("ids is a list of paths")
    try:
        app.trust.remove(ids)
    except OSError as e:
        raise PluginError("Can't write {}: {}".format(trust_file(), e.strerror or e))
    return {"message": "Removed {} trusted document(s)".format(len(ids)), "result": {"documents": app.trust.paths()}}


# ------------------------------------------------------------------------------ About

def cmd_about(app, ctx):
    info = {
        "addin": VERSION,
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "platform": platform.platform(),
        "app": "{} {}".format(app.host_name, app.host_version).strip(),
        "features": sorted(app.features),
        "dataDir": data_dir(),
        "trusted": ctx.trusted() if ctx.known else None,
        "trustAll": trust_all(),
    }
    lines = [
        "Python add-in {}".format(VERSION),
        "Python {} ({})".format(info["python"], sys.executable or "?"),
        info["platform"],
        "",
        "{}: {}".format(info["app"] or app.host, ", ".join(info["features"]) or "no 1.2 features"),
        "Python in this {}: {}".format(noun(app), "on" if info["trusted"] else (
            "off (Python > Trust This {})".format(noun(app).title()) if ctx.known else "unknown (the app didn't say which is open)")),
        "Trusted list: " + trust_file(),
    ]
    if info["trustAll"]:
        lines.append("CRAFT_PYTHON_TRUST=all: every document is trusted")
    lines.append("")
    lines.append("Standard library only; packages you installed for this Python can be imported too.")
    reply = {"result": info, "message": "Python add-in {} on Python {}".format(VERSION, info["python"])}
    if app.has("dialogs"):
        reply["dialog"] = message("About Python", "\n".join(lines))
    return reply


# ------------------------------------------------------------------------------ text and number tools (0.1, 0.2)

def _cells(context):
    rng, values = context.get("selection"), context.get("values")
    if not isinstance(rng, str) or not isinstance(values, list):
        raise PluginError("Select some cells first (at most 100,000).")
    return rng, values


def map_text(app, ctx, how, done):
    if app.host == "wordcraft":
        sel = ctx.context.get("selection")
        text = (sel.get("text") if isinstance(sel, dict) else None) or ""
        if not text:
            raise PluginError("Select some text first.")
        new = "\n".join(how(line) for line in text.split("\n"))
        return {"commands": [{"command": "text.insert", "params": {"text": new, "raw": True}}],
                "message": "{} the selection".format(done)}
    if app.host != "gridcraft":
        raise PluginError("This command works in GridCraft and WordCraft.")
    rng, values = _cells(ctx.context)
    changed = 0
    out = []
    for row in values:
        new_row = []
        for v in (row if isinstance(row, list) else [row]):
            if isinstance(v, str):
                w = how(v)
                changed += w != v
                new_row.append(w)
            else:
                new_row.append(v)
        out.append(new_row)
    return {"commands": [{"command": "range.setValues", "params": {"range": rng, "values": out}}],
            "message": "{} {} cell(s)".format(done, changed)}


def cmd_title_case(app, ctx):
    return map_text(app, ctx, str.title, "Title-cased")


def cmd_trim(app, ctx):
    return map_text(app, ctx, lambda s: " ".join(s.split()), "Trimmed")


def cmd_stats_table(app, ctx):
    """Writes a statistics table for the selection to the right of it."""
    rng, values = _cells(ctx.context)
    try:
        rows = fn_stats([values], None)
    except ValueError as e:
        raise PluginError("The selection holds an error: {}".format(e))
    if is_error(rows):
        raise PluginError("The selection holds no numbers.")
    width = max((len(r) if isinstance(r, list) else 1 for r in values), default=1)
    active = ctx.context.get("active")
    try:
        target = offset(active if isinstance(active, str) else rng.split(":")[0], width + 1)
    except ValueError as e:
        raise PluginError(str(e))
    return {"commands": [{"command": "range.setValues", "params": {"range": target, "values": rows}},
                         {"command": "home.bold", "params": {"range": target}}],
            "message": "Statistics of {} numbers written at {}".format(rows[0][1], target)}


def cmd_fill_fibonacci(app, ctx):
    count = ctx.params.get("count", 10)
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 1000:
        raise PluginError("count must be a whole number from 1 to 1000")
    a, b, rows = 0, 1, []
    for _ in range(count):
        rows.append([a])
        a, b = b, a + b
    start = ctx.context.get("active") if isinstance(ctx.context.get("active"), str) else "A1"
    return {"commands": [{"command": "range.setValues", "params": {"range": start, "values": rows}}],
            "message": "Wrote {} Fibonacci numbers".format(count)}


def cmd_text_stats(app, ctx):
    """Words, sentences and word lengths of the selection, or of the paragraph (WordCraft, DeckCraft)."""
    c = ctx.context
    sel = c.get("selection")
    para = c.get("paragraph")
    text = (sel.get("text") if isinstance(sel, dict) else None) or (para.get("text") if isinstance(para, dict) else None) \
        or (c.get("text") if isinstance(c.get("text"), str) else None) or ""
    words = re.findall(r"[^\W\d_]+(?:['’-][^\W\d_]+)*", text)
    if not words:
        raise PluginError("There are no words here.")
    sentences = max(1, len([s for s in re.split(r"[.!?]+", text) if s.strip()]))
    lengths = [len(w) for w in words]
    mean = statistics.mean(lengths)
    return {
        "message": "{} words, {} sentences, {:.1f} letters a word, {:.1f} words a sentence".format(
            len(words), sentences, mean, len(words) / sentences),
        "result": {"words": len(words), "sentences": sentences, "meanWordLength": mean,
                   "medianWordLength": statistics.median(lengths), "longest": max(words, key=len)},
    }


COMMANDS = {
    "runScript": cmd_run_script,
    "runFile": cmd_run_file,
    "console": cmd_console,
    "trust": cmd_trust,
    "trustConfirm": cmd_trust_confirm,
    "untrust": cmd_untrust,
    "trusted": cmd_trusted,
    "untrustPaths": cmd_untrust_paths,
    "about": cmd_about,
    "textStats": cmd_text_stats,
    "titleCase": cmd_title_case,
    "trim": cmd_trim,
    "statsTable": cmd_stats_table,
    "fillFibonacci": cmd_fill_fibonacci,
}
