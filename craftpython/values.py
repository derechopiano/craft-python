"""Cell values between the apps and Python, and A1 addresses."""

import datetime
import decimal
import fractions
import itertools
import math
import re

# Most cells a function's arguments or result, or one write, may hold (the apps' own limit).
MAX_CELLS = 1000000
# Longest text in a cell.
MAX_TEXT = 32767


class CellError(str):
    """A cell's error value (`#N/A`, `#DIV/0!`…): text that knows it's an error."""

    @property
    def code(self):
        return str(self)

    def __repr__(self):
        return "CellError({})".format(str.__repr__(self))


class ValueError_(ValueError):
    """A value too large for the cells."""


def is_error(v):
    return isinstance(v, dict) and "error" in v


def error(code):
    return {"error": code}


# ---------------------------------------------------------------------------- from the app

def from_wire(v):
    """A value the app sent as Python: errors become CellError, rows stay lists of lists."""
    if isinstance(v, dict):
        if "error" in v:
            return CellError(str(v.get("error")))
        return v
    if isinstance(v, list):
        return [from_wire(x) for x in v]
    if isinstance(v, float) and v.is_integer() and abs(v) < 2 ** 53:
        return int(v)  # spreadsheet numbers are all doubles: whole ones read as int
    return v


def first_error(v):
    """The first error value in an argument, or None."""
    if is_error(v):
        return v
    if isinstance(v, list):
        for row in v:
            for x in (row if isinstance(row, list) else [row]):
                if is_error(x):
                    return x
    return None


# ---------------------------------------------------------------------------- to the app

def _is_rowlike(x):
    return isinstance(x, (list, tuple)) or (hasattr(x, "tolist") and not isinstance(x, (str, bytes)))


def cell(v, empty=""):
    """One Python value as one cell value (`empty` for None)."""
    if v is None:
        return empty
    if isinstance(v, CellError):
        return error(str(v))
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        if abs(v) < 2 ** 53:
            return v
        try:
            f = float(v)
        except OverflowError:
            return error("#NUM!")
        return f if math.isfinite(f) else error("#NUM!")
    if isinstance(v, float):
        return v if math.isfinite(v) else error("#NUM!")
    if isinstance(v, str):
        return v[:MAX_TEXT]
    if isinstance(v, (decimal.Decimal, fractions.Fraction)):
        try:
            f = float(v)
        except (OverflowError, ValueError):
            return error("#NUM!")
        return f if math.isfinite(f) else error("#NUM!")
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat(sep=" ") if isinstance(v, datetime.datetime) else v.isoformat()
    if hasattr(v, "item") and callable(v.item) and not hasattr(v, "__len__"):
        try:  # a numpy scalar
            return cell(v.item(), empty)
        except Exception:
            pass
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).decode("utf-8", "replace")[:MAX_TEXT]
    try:
        return str(v)[:MAX_TEXT]
    except Exception:
        return error("#VALUE!")


def _take(it, n):
    return list(itertools.islice(it, n))


def _iterable(v):
    return not isinstance(v, (str, bytes, bytearray, type)) and hasattr(v, "__iter__")


def to_rows(v, empty=""):
    """A Python value as rows of cells: a list of lists is rows, a flat list a column, a dict
    two columns (keys, values). Raises ValueError_ when it holds more than MAX_CELLS cells."""
    if hasattr(v, "tolist") and not isinstance(v, (str, bytes)):
        try:
            v = v.tolist()
        except Exception:
            pass
    if isinstance(v, dict):
        items = _take(iter(v.items()), MAX_CELLS + 1)
        if 2 * len(items) > MAX_CELLS:
            raise ValueError_("more than 1,000,000 cells")
        return [[cell(k, empty), cell(x, empty)] for k, x in items]
    if isinstance(v, (set, frozenset)):
        try:
            v = sorted(v)
        except TypeError:
            v = sorted(v, key=repr)
    if not _iterable(v):
        return [[cell(v, empty)]]
    try:
        items = _take(iter(v), MAX_CELLS + 1)
    except TypeError:
        return [[cell(v, empty)]]
    if len(items) > MAX_CELLS:
        raise ValueError_("more than 1,000,000 cells")
    if items and all(_is_rowlike(x) for x in items):
        rows = []
        total = 0
        for x in items:
            if hasattr(x, "tolist") and not isinstance(x, (list, tuple)):
                x = x.tolist()
            row = [cell(c, empty) if not _is_rowlike(c) else cell(str(c), empty) for c in _take(iter(x), MAX_CELLS + 1)]
            total += len(row)
            if total > MAX_CELLS:
                raise ValueError_("more than 1,000,000 cells")
            rows.append(row)
        width = max((len(r) for r in rows), default=0)
        if width == 0:
            return [[cell(None, empty)]]
        return [r + [cell(None, empty)] * (width - len(r)) for r in rows]
    if not items:
        return [[cell(None, empty)]]
    return [[cell(x, empty)] for x in items]


def to_result(v):
    """A function's result: one value, or rows that spill. Over a million cells is #NUM!."""
    if v is None or isinstance(v, (str, bytes, bool, int, float, decimal.Decimal, fractions.Fraction,
                                   datetime.date, datetime.time, CellError)):
        return cell(v)
    if not _iterable(v) and not hasattr(v, "tolist"):
        return cell(v)
    try:
        rows = to_rows(v)
    except ValueError_:
        return error("#NUM!")
    return rows


# ---------------------------------------------------------------------------- A1 addresses

_CELL = re.compile(r"^\$?([A-Za-z]{1,3})\$?([0-9]{1,7})$")


def col_index(letters):
    n = 0
    for ch in letters.upper():
        n = n * 26 + ord(ch) - 64
    return n - 1


def col_letters(i):
    n = i + 1
    out = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def parse_cell(a):
    """`B3` as (row, col) from 0, or None."""
    m = _CELL.match(a.strip())
    if not m:
        return None
    row = int(m.group(2)) - 1
    if row < 0:
        return None
    return row, col_index(m.group(1))


def parse_range(a):
    """`A1:B2` or `B3` as (r1, c1, r2, c2) from 0, or None (whole rows or columns, names…)."""
    parts = a.strip().split(":")
    if len(parts) == 1:
        c = parse_cell(parts[0])
        return None if c is None else (c[0], c[1], c[0], c[1])
    if len(parts) != 2:
        return None
    a1, b1 = parse_cell(parts[0]), parse_cell(parts[1])
    if a1 is None or b1 is None:
        return None
    return min(a1[0], b1[0]), min(a1[1], b1[1]), max(a1[0], b1[0]), max(a1[1], b1[1])


def format_range(r1, c1, r2, c2):
    a = col_letters(c1) + str(r1 + 1)
    if (r1, c1) == (r2, c2):
        return a
    return a + ":" + col_letters(c2) + str(r2 + 1)


def split_address(address):
    """`'My Sheet'!A1:B2` as ("My Sheet", "A1:B2"); no sheet gives (None, address)."""
    address = address.strip()
    if "!" not in address:
        return None, address
    sheet, _, ref = address.rpartition("!")
    if sheet.startswith("'") and sheet.endswith("'") and len(sheet) >= 2:
        sheet = sheet[1:-1].replace("''", "'")
    return sheet, ref


def offset(cell_address, cols):
    """Moves an A1 address `cols` columns to the right (sheet prefix kept)."""
    sheet, _, addr = cell_address.rpartition("!")
    c = parse_cell(addr)
    if c is None:
        raise ValueError("Can't read the address " + cell_address)
    return (sheet + "!" if sheet else "") + col_letters(c[1] + cols) + str(c[0] + 1)
