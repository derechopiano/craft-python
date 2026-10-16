"""`doc` in GridCraft: the workbook, its sheets and ranges (ExcelApi's objects through the bridge)."""

from .bridge import Base, BridgeError
from .values import (MAX_CELLS, CellError, from_wire, cell, format_range, parse_range, split_address,
                     to_rows, ValueError_)

WB = [{"p": "workbook"}]


def _quote(name):
    plain = name and all(ch.isalnum() or ch == "_" for ch in name) and not name[0].isdigit()
    return name if plain else "'" + name.replace("'", "''") + "'"


def _input(v):
    """A Python value as a cell input for range.setValues (None empties the cell)."""
    if v is None:
        return None
    if isinstance(v, CellError):
        return str(v)
    out = cell(v, None)
    if isinstance(out, dict):  # #NUM! for NaN and infinities
        return out.get("error")
    return out


def _grid(values, height, width):
    """`values` as rows; a single cell's range grows to fit them."""
    if isinstance(values, (str, bytes, bool, int, float)) or values is None or isinstance(values, CellError):
        return None, _input(values)
    try:
        rows = to_rows(values, empty=None)
    except ValueError_:
        raise ValueError("at most 1,000,000 cells at a time")
    flat = not any(isinstance(x, (list, tuple)) for x in values) if isinstance(values, (list, tuple)) else False
    if flat and height == 1 and width > 1:  # a flat list into a row
        rows = [[r[0] for r in rows]]
    rows = [[_input_cell(c) for c in r] for r in rows]
    return rows, None


def _input_cell(c):
    if isinstance(c, dict) and "error" in c:
        return c.get("error")
    return c


class Workbook(Base):
    """The workbook a script runs in (`doc` in GridCraft)."""

    @property
    def name(self):
        return self._b.load(WB, ["name"]).get("name")

    @property
    def sheets(self):
        """Every worksheet, in order."""
        items = self._b.load(WB + [{"p": "worksheets"}], ["items"]).get("items") or []
        return [Sheet(self, i.get("name")) for i in items if isinstance(i, dict)]

    def sheet(self, name):
        """The worksheet called `name` (any case); KeyError when there's none."""
        try:
            got = self._b.load(WB + [{"p": "worksheets"}, {"m": "getItem", "a": [str(name)]}], ["name"])
        except BridgeError as e:
            if e.code == "ItemNotFound":
                raise KeyError(name) from None
            raise
        return Sheet(self, got.get("name", str(name)))

    def __getitem__(self, name):
        return self.sheet(name)

    @property
    def active_sheet(self):
        got = self._b.load(WB + [{"p": "worksheets"}, {"m": "getActiveWorksheet"}], ["name"])
        return Sheet(self, got.get("name"))

    def add_sheet(self, name=None):
        """A new worksheet at the end (named `name`, else the next SheetN); the active one stays."""
        args = [name] if name else []
        got = self._b.call(WB + [{"p": "worksheets"}], "add", args) or {}
        return Sheet(self, got.get("name") or name)

    def _bound(self, step):
        got = self._b.load(WB + [step], ["address"])
        sheet, ref = split_address(got.get("address") or "")
        return Range(Sheet(self, sheet), ref)

    @property
    def selection(self):
        """The selected range (the last one of a multiple selection)."""
        return self._bound({"m": "getSelectedRange"})

    @property
    def active_cell(self):
        return self._bound({"m": "getActiveCell"})

    def range(self, address):
        """`"Sheet2!A1:B3"`, or `"A1:B3"` on the active sheet."""
        sheet, ref = split_address(str(address))
        return (self.sheet(sheet) if sheet else self.active_sheet).range(ref)

    def __repr__(self):
        return "<Workbook>"


class Sheet:
    def __init__(self, book, name):
        self._book = book
        self._b = book._b
        self._name = name

    @property
    def _path(self):
        return WB + [{"p": "worksheets"}, {"m": "getItem", "a": [self._name]}]

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, new):
        self._b.set(self._path, "name", str(new))
        self._name = str(new)

    @property
    def position(self):
        return self._b.load(self._path, ["position"]).get("position")

    def range(self, address):
        """A range of this sheet: `"A1"`, `"A1:C10"`, `"B:B"`."""
        sheet, ref = split_address(str(address))
        if sheet is not None and sheet.lower() != (self._name or "").lower():
            return self._book.sheet(sheet).range(ref)
        return Range(self, ref)

    __getitem__ = range

    def cell(self, row, column):
        """The cell at (row, column), counted from 0."""
        return Range(self, format_range(int(row), int(column), int(row), int(column)))

    @property
    def used_range(self):
        got = self._b.load(self._path + [{"m": "getUsedRange"}], ["address"])
        return Range(self, split_address(got.get("address") or "A1")[1])

    def read(self, address=None):
        """The values of `address` (default: the used range) as rows."""
        return (self.range(address) if address else self.used_range).values

    def write(self, address, values):
        """Writes rows (or a column from a flat list) from the cell `address` on; the range written."""
        r = self.range(address).cell(0, 0) if parse_range(str(address)) else self.range(address)
        r.values = values
        return r._grown if r._grown is not None else r

    def activate(self):
        self._b.call(self._path, "activate", result=False)

    def delete(self):
        self._b.call(self._path, "delete", result=False)

    def __repr__(self):
        return "<Sheet {!r}>".format(self._name)


class _Props:
    """A format object's properties, read and written through the bridge."""

    _names = {}

    def __init__(self, rng, step):
        object.__setattr__(self, "_range", rng)
        object.__setattr__(self, "_step", step)

    def _path(self):
        return self._range._path + [{"p": "format"}, {"p": self._step}]

    def __getattr__(self, name):
        prop = self._names.get(name)
        if prop is None:
            raise AttributeError(name)
        return self._range._b.load(self._path(), [prop]).get(prop)

    def __setattr__(self, name, value):
        prop = self._names.get(name)
        if prop is None:
            raise AttributeError("{} has no property {!r}".format(type(self).__name__, name))
        self._range._b.set(self._path(), prop, value)

    def __dir__(self):
        return sorted(self._names)


class Fill(_Props):
    """`color`: "#RRGGBB" or a colour name ("yellow"); `"none"` or `clear()` takes it off."""

    _names = {"color": "color"}

    def clear(self):
        self.color = "none"


class Font(_Props):
    """`bold`, `italic`, `underline` ("None", "Single", "Double"), `strikethrough`, `color`, `size`, `name`."""

    _names = {"bold": "bold", "italic": "italic", "underline": "underline", "strikethrough": "strikethrough",
              "color": "color", "size": "size", "name": "name"}


class Format:
    def __init__(self, rng):
        self.fill = Fill(rng, "fill")
        self.font = Font(rng, "font")


class Range:
    """Cells of one sheet. Values are lists of rows; errors read as CellError."""

    def __init__(self, sheet, address):
        self._sheet = sheet
        self._b = sheet._b
        self._address = address.replace("$", "").upper() if address else ""
        self._grown = None

    @property
    def _path(self):
        return self._sheet._path + [{"m": "getRange", "a": [self._address]}]

    @property
    def sheet(self):
        return self._sheet

    @property
    def address(self):
        """`Sheet1!A1:B2`."""
        return _quote(self._sheet.name) + "!" + self._address

    def _box(self):
        box = parse_range(self._address)
        if box is None:  # whole rows or columns: ask the app
            got = self._b.load(self._path, ["rowIndex", "columnIndex", "rowCount", "columnCount"])
            r, c = int(got.get("rowIndex", 0)), int(got.get("columnIndex", 0))
            return r, c, r + int(got.get("rowCount", 1)) - 1, c + int(got.get("columnCount", 1)) - 1
        return box

    @property
    def row_count(self):
        r1, _, r2, _ = self._box()
        return r2 - r1 + 1

    @property
    def column_count(self):
        _, c1, _, c2 = self._box()
        return c2 - c1 + 1

    @property
    def shape(self):
        """(rows, columns)."""
        r1, c1, r2, c2 = self._box()
        return r2 - r1 + 1, c2 - c1 + 1

    def _load(self, prop):
        return from_wire(self._b.load(self._path, [prop]).get(prop))

    # ---------------------------------------------------------------- contents

    @property
    def values(self):
        return self._load("values")

    @values.setter
    def values(self, values):
        self._write("values", values)

    @property
    def value(self):
        """The top-left cell's value."""
        rows = self.cell(0, 0).values
        return rows[0][0] if rows and rows[0] else None

    @value.setter
    def value(self, v):
        self.cell(0, 0).values = [[v]]

    @property
    def formulas(self):
        """Each cell's formula (`"=A1*2"`), or its value when it has none."""
        return self._load("formulas")

    @formulas.setter
    def formulas(self, values):
        self._write("formulas", values)

    @property
    def text(self):
        """The values as shown (formatted)."""
        return self._load("text")

    @property
    def number_format(self):
        return self._load("numberFormat")

    @number_format.setter
    def number_format(self, code):
        if isinstance(code, (list, tuple)):
            code = [list(r) if isinstance(r, (list, tuple)) else [r] for r in code]
        self._b.set(self._path, "numberFormat", code)

    def _write(self, prop, values):
        r1, c1, r2, c2 = self._box()
        h, w = r2 - r1 + 1, c2 - c1 + 1
        rows, single = _grid(values, h, w)
        if rows is None:
            if h * w > MAX_CELLS:
                raise ValueError("at most 1,000,000 cells at a time")
            self._b.set(self._path, prop, single)
            return
        if not rows or not rows[0]:
            return
        rh, rw = len(rows), len(rows[0])
        if (rh, rw) != (h, w):
            if (h, w) == (1, 1):
                # A cell: the values go from it on.
                target = Range(self._sheet, format_range(r1, c1, r1 + rh - 1, c1 + rw - 1))
                self._grown = target
                self._b.set(target._path, prop, rows)
                return
            if rh == w and rw == 1 and h == 1:  # a column given for a row
                rows = [[r[0] for r in rows]]
            else:
                raise ValueError("{} rows of {} values don't fit {} ({} x {})".format(rh, rw, self.address, h, w))
        self._b.set(self._path, prop, rows)

    def clear(self, what="all"):
        """Clears "all", "contents" or "formats"."""
        kind = {"all": "All", "contents": "Contents", "formats": "Formats"}.get(str(what).lower())
        if kind is None:
            raise ValueError("clear takes \"all\", \"contents\" or \"formats\"")
        self._b.call(self._path, "clear", [kind], result=False)

    def select(self):
        self._b.call(self._path, "select", result=False)

    # ---------------------------------------------------------------- formats

    @property
    def format(self):
        """`.format.fill.color`, `.format.font.bold`…"""
        return Format(self)

    @property
    def font(self):
        return Font(self, "font")

    @property
    def fill(self):
        return Fill(self, "fill")

    # ---------------------------------------------------------------- other ranges

    def cell(self, row, column):
        """The cell at (row, column) from this range's top-left, counted from 0."""
        r1, c1, _, _ = self._box()
        r, c = r1 + int(row), c1 + int(column)
        if r < 0 or c < 0:
            raise IndexError("that cell would be off the sheet")
        return Range(self._sheet, format_range(r, c, r, c))

    def offset(self, rows, columns):
        """The same size, moved."""
        r1, c1, r2, c2 = self._box()
        rows, columns = int(rows), int(columns)
        if r1 + rows < 0 or c1 + columns < 0:
            raise IndexError("that range would be off the sheet")
        return Range(self._sheet, format_range(r1 + rows, c1 + columns, r2 + rows, c2 + columns))

    def resize(self, rows, columns):
        """The same top-left, `rows` x `columns`."""
        r1, c1, _, _ = self._box()
        if int(rows) < 1 or int(columns) < 1:
            raise ValueError("a range has at least one row and one column")
        return Range(self._sheet, format_range(r1, c1, r1 + int(rows) - 1, c1 + int(columns) - 1))

    def row(self, i):
        r1, c1, _, c2 = self._box()
        return Range(self._sheet, format_range(r1 + int(i), c1, r1 + int(i), c2))

    def column(self, i):
        r1, c1, r2, _ = self._box()
        return Range(self._sheet, format_range(r1, c1 + int(i), r2, c1 + int(i)))

    @property
    def used_range(self):
        got = self._b.load(self._path + [{"m": "getUsedRange"}], ["address"])
        return Range(self._sheet, split_address(got.get("address") or self._address)[1])

    # ---------------------------------------------------------------- helpers (no pandas needed)

    def records(self):
        """The rows as dicts, keyed by the first row (the header)."""
        rows = self.values
        if not rows:
            return []
        head = [str(h) if h is not None else "column{}".format(i + 1) for i, h in enumerate(rows[0])]
        return [dict(zip(head, r)) for r in rows[1:]]

    def columns(self):
        """The values column by column."""
        rows = self.values
        return [list(c) for c in zip(*rows)] if rows else []

    def numbers(self):
        """The numbers in the range, row by row (text, logicals and empty cells left out)."""
        return [x for r in self.values for x in r if isinstance(x, (int, float)) and not isinstance(x, bool)]

    def __iter__(self):
        return iter(self.values)

    def __repr__(self):
        return "<Range {}>".format(self.address)
