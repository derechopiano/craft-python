"""GridCraft and WordCraft Python add-in: a test of the plugin protocol (version 1).

GridCraft starts this program and talks to it over standard input and output:
one JSON-RPC 2.0 message per line. See https://github.com/storytold/gridcraft/blob/main/docs/plugins.md

Only the Python standard library is used, so it runs with any python3 >= 3.8.
"""

import json
import re
import statistics
import sys

PROTOCOL_VERSION = 1

# The app that started us ("gridcraft", "wordcraft"), from `initialize`: the same commands work
# on cells in GridCraft and on the selected text in WordCraft.
HOST = "gridcraft"


class PluginError(Exception):
    """Fails a command with this message (functions give #VALUE! instead)."""


# --- values -----------------------------------------------------------------

def is_error(v):
    return isinstance(v, dict) and "error" in v


def error(code):
    return {"error": code}


def flatten(v):
    """A reference arrives as rows of values; a single value stays itself."""
    if isinstance(v, list):
        for row in v:
            if isinstance(row, list):
                for x in row:
                    yield x
            else:
                yield row
    else:
        yield v


def numbers(args):
    """The numbers in the arguments, ignoring text, logicals and empty cells, like SUM."""
    out = []
    for a in args:
        for x in flatten(a):
            if is_error(x):
                raise ValueError(x["error"])
            if isinstance(x, (int, float)) and not isinstance(x, bool):
                out.append(x)
    return out


def text(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (list, dict)):
        raise ValueError("#VALUE!")
    return str(v)


# --- functions --------------------------------------------------------------

def fn_version(args):
    return "Python Tools 0.2.0 on Python " + sys.version.split()[0]


def fn_twice(args):
    x = args[0]
    if isinstance(x, list):
        return [[v * 2 if isinstance(v, (int, float)) and not isinstance(v, bool) else error("#VALUE!")
                 for v in (row if isinstance(row, list) else [row])] for row in x]
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return x * 2
    return error("#VALUE!")


def fn_median(args):
    xs = numbers(args)
    if not xs:
        return error("#NUM!")
    return statistics.median(xs)


def fn_stats(args):
    """A two-column table that spills: label, value."""
    xs = numbers(args)
    if not xs:
        return error("#NUM!")
    rows = [
        ["count", len(xs)],
        ["sum", sum(xs)],
        ["mean", statistics.mean(xs)],
        ["median", statistics.median(xs)],
        ["min", min(xs)],
        ["max", max(xs)],
    ]
    if len(xs) > 1:
        rows.append(["stdev", statistics.stdev(xs)])
    return rows


def fn_regex(args):
    """PY.REGEX(text, pattern, [group]): the first match, or #N/A."""
    s, pattern = text(args[0]), text(args[1])
    group = args[2] if len(args) > 2 and args[2] is not None else 0
    if not isinstance(group, (int, float)) or isinstance(group, bool):
        return error("#VALUE!")
    try:
        m = re.search(pattern, s)
    except re.error:
        return error("#VALUE!")
    if m is None:
        return error("#N/A")
    try:
        found = m.group(int(group))
    except IndexError:
        return error("#REF!")
    return found if found is not None else ""


def fn_split(args):
    """PY.SPLIT(text, [separator]): the parts, spilled across a row."""
    s = text(args[0])
    sep = text(args[1]) if len(args) > 1 and args[1] is not None else None
    parts = s.split(sep) if sep else s.split()
    return [parts] if parts else error("#N/A")


FUNCTIONS = {
    "PY.VERSION": fn_version,
    "PY.TWICE": fn_twice,
    "PY.MEDIAN": fn_median,
    "PY.STATS": fn_stats,
    "PY.REGEX": fn_regex,
    "PY.SPLIT": fn_split,
}


# --- commands ---------------------------------------------------------------

def selection(context):
    rng, values = context.get("selection"), context.get("values")
    if not rng or values is None:
        raise PluginError("Select some cells first (at most 100,000).")
    return rng, values


def map_text(context, how, done):
    if HOST == "wordcraft":
        text = (context.get("selection") or {}).get("text") or ""
        if not text:
            raise PluginError("Select some text first.")
        new = "\n".join(how(line) for line in text.split("\n"))
        return {
            "commands": [{"command": "text.insert", "params": {"text": new, "raw": True}}],
            "message": "{} the selection".format(done),
        }
    rng, values = selection(context)
    changed = 0
    out = []
    for row in values:
        new_row = []
        for v in row:
            if isinstance(v, str):
                w = how(v)
                changed += w != v
                new_row.append(w)
            else:
                new_row.append(v)
        out.append(new_row)
    return {
        "commands": [{"command": "range.setValues", "params": {"range": rng, "values": out}}],
        "message": "{} {} cell(s)".format(done, changed),
    }


def cmd_title_case(params, context):
    return map_text(context, str.title, "Title-cased")


def cmd_trim(params, context):
    return map_text(context, lambda s: " ".join(s.split()), "Trimmed")


def cmd_stats_table(params, context):
    """Writes a statistics table for the selection to the right of it."""
    rng, values = selection(context)
    try:
        rows = fn_stats([values])
    except ValueError as e:
        raise PluginError("The selection holds an error: {}".format(e))
    if is_error(rows):
        raise PluginError("The selection holds no numbers.")
    width = max((len(r) for r in values), default=1)
    target = offset(context.get("active") or rng.split(":")[0], width + 1)
    return {
        "commands": [
            {"command": "range.setValues", "params": {"range": target, "values": rows}},
            {"command": "home.bold", "params": {"range": target}},
        ],
        "message": "Statistics of {} numbers written at {}".format(rows[0][1], target),
    }


def offset(cell, cols):
    """Moves an A1 address `cols` columns to the right (sheet prefix kept)."""
    sheet, _, addr = cell.rpartition("!")
    m = re.match(r"^\$?([A-Za-z]+)\$?(\d+)$", addr)
    if not m:
        raise PluginError("Can't read the address " + cell)
    n = 0
    for ch in m.group(1).upper():
        n = n * 26 + ord(ch) - 64
    n += cols
    letters = ""
    while n:
        n, r = divmod(n - 1, 26)
        letters = chr(65 + r) + letters
    return (sheet + "!" if sheet else "") + letters + m.group(2)


def cmd_fill_fibonacci(params, context):
    count = params.get("count", 10)
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 1000:
        raise PluginError("count must be a whole number from 1 to 1000")
    a, b, rows = 0, 1, []
    for _ in range(count):
        rows.append([a])
        a, b = b, a + b
    start = context.get("active") or "A1"
    return {
        "commands": [{"command": "range.setValues", "params": {"range": start, "values": rows}}],
        "message": "Wrote {} Fibonacci numbers".format(count),
    }


def cmd_text_stats(params, context):
    """Words, sentences and word lengths of the selection, or of the paragraph (WordCraft)."""
    text = (context.get("selection") or {}).get("text") or (context.get("paragraph") or {}).get("text") or ""
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
    "textStats": cmd_text_stats,
    "titleCase": cmd_title_case,
    "trim": cmd_trim,
    "statsTable": cmd_stats_table,
    "fillFibonacci": cmd_fill_fibonacci,
}


# --- protocol ---------------------------------------------------------------

def handle(method, params):
    if method == "initialize":
        global HOST
        host = params.get("host") or {}
        HOST = str(host.get("id") or host.get("name") or "gridcraft").lower()
        return {"protocolVersion": PROTOCOL_VERSION}
    if method == "functions/call":
        fn = FUNCTIONS.get(str(params.get("name", "")).upper())
        if fn is None:
            raise LookupError("unknown function")
        try:
            return {"value": fn(params.get("args", []))}
        except ValueError as e:
            code = str(e)
            return {"value": error(code if code.startswith("#") else "#VALUE!")}
    if method == "commands/run":
        cmd = COMMANDS.get(params.get("command"))
        if cmd is None:
            raise LookupError("unknown command")
        return cmd(params.get("params") or {}, params.get("context") or {})
    raise LookupError("unknown method " + method)


def reply(msg):
    sys.stdout.write(json.dumps(msg, allow_nan=False) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            print("not JSON: " + line[:200], file=sys.stderr)
            continue
        if "id" not in msg:  # notifications; `shutdown` ends the plugin
            if msg.get("method") == "shutdown":
                break
            continue
        try:
            result = handle(msg.get("method", ""), msg.get("params") or {})
            reply({"jsonrpc": "2.0", "id": msg["id"], "result": result})
        except LookupError as e:
            reply({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": str(e)}})
        except PluginError as e:
            reply({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": 1, "message": str(e)}})
        except Exception as e:  # a bug here must not stop the plugin
            print("error: {!r}".format(e), file=sys.stderr)
            reply({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32603, "message": "internal error"}})


if __name__ == "__main__":
    main()
