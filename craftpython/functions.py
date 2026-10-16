"""GridCraft's PY.* functions.

The fixed helpers (PY.VERSION, PY.TWICE, PY.MEDIAN, PY.STATS, PY.REGEX, PY.SPLIT) run no code from
the workbook and always work. PY.EVAL runs the workbook's Python, so only in trusted workbooks.
"""

import re
import statistics
import sys

from . import VERSION
from .values import error, first_error, from_wire, to_result


class FunctionError(Exception):
    """Fails the call with this message (the cell shows #VALUE!, the app logs the message)."""


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
            if isinstance(x, dict) and "error" in x:
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


def fn_version(args, ctx):
    return "Python add-in {} on Python {}".format(VERSION, sys.version.split()[0])


def fn_twice(args, ctx):
    x = args[0]
    if isinstance(x, list):
        return [[v * 2 if isinstance(v, (int, float)) and not isinstance(v, bool) else error("#VALUE!")
                 for v in (row if isinstance(row, list) else [row])] for row in x]
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return x * 2
    return error("#VALUE!")


def fn_median(args, ctx):
    xs = numbers(args)
    if not xs:
        return error("#NUM!")
    return statistics.median(xs)


def fn_stats(args, ctx):
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


def fn_regex(args, ctx):
    """PY.REGEX(text, pattern, [group]): the first match, or #N/A."""
    s, pattern = text(args[0]), text(args[1])
    group = args[2] if len(args) > 2 and args[2] is not None else 0
    if not isinstance(group, (int, float)) or isinstance(group, bool):
        return error("#VALUE!")
    if len(pattern) > 10000 or len(s) > 1000000:
        return error("#VALUE!")
    try:
        m = re.search(pattern, s)
    except (re.error, RecursionError, OverflowError):
        return error("#VALUE!")
    if m is None:
        return error("#N/A")
    try:
        found = m.group(int(group))
    except (IndexError, OverflowError):
        return error("#REF!")
    return found if found is not None else ""


def fn_split(args, ctx):
    """PY.SPLIT(text, [separator]): the parts, spilled across a row."""
    s = text(args[0])
    sep = text(args[1]) if len(args) > 1 and args[1] is not None else None
    parts = s.split(sep) if sep else s.split()
    return [parts] if parts else error("#N/A")


# Seconds PY.EVAL may run (the manifest gives the app 30).
EVAL_SECONDS = 25

OFF_MESSAGE = "Python is turned off for this workbook: Python > Trust This Workbook"


def fn_eval(args, ctx):
    """PY.EVAL(code, [x], [y], [z], ...): the value of Python code; trusted workbooks only."""
    from . import runner

    if not ctx.trust.functions_trusted():
        raise FunctionError(OFF_MESSAGE)
    code = args[0] if args else None
    if not isinstance(code, str) or not code.strip():
        return error("#VALUE!")
    rest = args[1:]
    for a in rest:
        e = first_error(a)
        if e is not None:
            return e
    values = [from_wire(a) for a in rest]
    namespace = dict(ctx.workspace)
    namespace["args"] = values
    for name, v in zip(("x", "y", "z"), values):
        namespace[name] = v
    for name in ("x", "y", "z"):
        namespace.setdefault(name, None)
    namespace.pop("doc", None)
    seconds = runner.limit("CRAFT_PYTHON_EVAL_SECONDS", EVAL_SECONDS)
    out = runner.run(code, namespace, seconds, filename="<PY.EVAL>")
    if out.output:
        ctx.log("PY.EVAL printed: " + out.output[:2000])
    if not out.ok:
        raise FunctionError("Python error: " + (out.error_line or "failed"))
    return to_result(out.value)


FUNCTIONS = {
    "PY.VERSION": fn_version,
    "PY.TWICE": fn_twice,
    "PY.MEDIAN": fn_median,
    "PY.STATS": fn_stats,
    "PY.REGEX": fn_regex,
    "PY.SPLIT": fn_split,
    "PY.EVAL": fn_eval,
}


def call(name, args, ctx):
    fn = FUNCTIONS.get(str(name).upper())
    if fn is None:
        raise LookupError("unknown function")
    if not isinstance(args, list):
        args = [args]
    try:
        return {"value": fn(args, ctx)}
    except ValueError as e:
        code = str(e)
        return {"value": error(code if code.startswith("#") else "#VALUE!")}
    except (IndexError, TypeError, statistics.StatisticsError, OverflowError, ZeroDivisionError):
        return {"value": error("#VALUE!")}
