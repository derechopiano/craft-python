"""Tiny in-memory stand-ins for the three apps' object models, answering `host/sync` the way their
Office bridges do (paths of `{"p"}` / `{"m", "a"}` steps; load, set, call; all or nothing). Enough
of each to test how the add-in's `doc` API maps onto the bridge, not the apps themselves."""

import copy
import re


class Fail(Exception):
    def __init__(self, code, message):
        Exception.__init__(self, message)
        self.code = code
        self.message = message


def invalid(msg):
    return Fail("InvalidArgument", msg)


class Model:
    """Shared: snapshots for the one-step rollback, sync with the bridge's error shape."""

    def __init__(self):
        self.syncs = 0
        self.actions = 0

    def snapshot(self):
        return copy.deepcopy(self.state())

    def restore(self, snap):
        self.set_state(copy.deepcopy(snap))

    def sync(self, actions):
        """(results, None) or (None, error data)."""
        self.syncs += 1
        self.actions += len(actions)
        before = self.snapshot()
        results = []
        for i, a in enumerate(actions):
            a = self.with_results(a, results)
            try:
                op = a.get("op")
                obj = self.resolve(a.get("path") or [])
                if op == "load":
                    results.append(self.load(obj, a.get("props") or []))
                elif op == "set":
                    self.set(obj, a.get("prop"), a.get("value"))
                    results.append(None)
                elif op == "call":
                    results.append(self.call(obj, a.get("method"), a.get("args") or []))
                else:
                    raise invalid("unknown op")
            except Fail as e:
                self.restore(before)
                return None, {"code": e.code, "message": e.message, "action": i}
        return results, None

    def with_results(self, a, results):
        def fix(v):
            if isinstance(v, dict) and set(v) == {"result"} and isinstance(v["result"], int):
                r = results[v["result"]] if v["result"] < len(results) else None
                return (r or {}).get("id", "")
            return v
        a = dict(a)
        a["path"] = [dict(s, a=[fix(x) for x in s.get("a", [])]) if "m" in s else s for s in a.get("path") or []]
        return a

    def command(self, cmd, params):
        """An engine command from a reply's `commands` (the few the tests use)."""
        raise invalid("the fake app doesn't run " + cmd)


# ============================================================================ GridCraft

def col_index(letters):
    n = 0
    for ch in letters.upper():
        n = n * 26 + ord(ch) - 64
    return n - 1


def col_letters(i):
    n, out = i + 1, ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def parse_range(a):
    a = a.replace("$", "").strip()
    if "!" in a:
        a = a.rpartition("!")[2]
    parts = a.split(":")
    cells = []
    for p in parts:
        m = re.match(r"^([A-Za-z]+)([0-9]+)$", p)
        if not m:
            raise invalid("`{}` isn't a range address".format(a))
        cells.append((int(m.group(2)) - 1, col_index(m.group(1))))
    if len(cells) == 1:
        cells.append(cells[0])
    (r1, c1), (r2, c2) = cells
    return min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2)


def fmt(r):
    r1, c1, r2, c2 = r
    a = col_letters(c1) + str(r1 + 1)
    return a if (r1, c1) == (r2, c2) else a + ":" + col_letters(c2) + str(r2 + 1)


class Grid(Model):
    def __init__(self):
        Model.__init__(self)
        self.sheets = [{"name": "Sheet1", "cells": {}, "style": {}}]
        self.active = 0
        self.sel = (0, 0, 0, 0)
        self.name = "Book1"

    def state(self):
        return {"sheets": self.sheets, "active": self.active, "sel": self.sel}

    def set_state(self, s):
        self.sheets, self.active, self.sel = s["sheets"], s["active"], s["sel"]

    # helpers for tests
    def put(self, sheet, addr, rows):
        r1, c1, _, _ = parse_range(addr)
        for i, row in enumerate(rows):
            for j, v in enumerate(row):
                self.sheets[sheet]["cells"][(r1 + i, c1 + j)] = v

    def get(self, sheet, addr):
        r1, c1, r2, c2 = parse_range(addr)
        return [[self.sheets[sheet]["cells"].get((r, c)) for c in range(c1, c2 + 1)] for r in range(r1, r2 + 1)]

    def sheet_index(self, name):
        for i, s in enumerate(self.sheets):
            if s["name"].lower() == str(name).lower():
                return i
        raise Fail("ItemNotFound", "no worksheet `{}`".format(name))

    def used(self, i):
        cells = self.sheets[i]["cells"]
        if not cells:
            return (0, 0, 0, 0)
        rs = [k[0] for k in cells]
        cs = [k[1] for k in cells]
        return min(rs), min(cs), max(rs), max(cs)

    def resolve(self, path):
        obj = ("root",)
        for st in path:
            p, m, a = st.get("p"), st.get("m"), st.get("a", [])
            k = obj[0]
            if k == "root" and p == "workbook":
                obj = ("wb",)
            elif k == "wb" and p == "worksheets":
                obj = ("sheets",)
            elif k == "wb" and m in ("getSelectedRange", "getActiveCell"):
                r = self.sel if m == "getSelectedRange" else (self.sel[0], self.sel[1], self.sel[0], self.sel[1])
                obj = ("range", self.active, r)
            elif k == "sheets" and m == "getActiveWorksheet":
                obj = ("sheet", self.active)
            elif k == "sheets" and m == "getItem":
                obj = ("sheet", self.sheet_index(a[0]))
            elif k == "sheet" and m == "getRange":
                obj = ("range", obj[1], parse_range(a[0]) if a and a[0] else self.used(obj[1]))
            elif k == "sheet" and m == "getUsedRange":
                obj = ("range", obj[1], self.used(obj[1]))
            elif k == "range" and m == "getUsedRange":
                obj = ("range", obj[1], self.used(obj[1]))
            elif k == "range" and p == "format":
                obj = ("format",) + obj[1:]
            elif k == "format" and p in ("font", "fill"):
                obj = (p,) + obj[1:]
            else:
                raise Fail("NotImplemented", "{} has no {}".format(k, p or m))
        return obj

    def load(self, obj, props):
        k = obj[0]
        out = {}
        if k == "range":
            i, r = obj[1], obj[2]
            cells = self.sheets[i]["cells"]
            for p in props or ["address", "values"]:
                if p == "address":
                    out[p] = "{}!{}".format(self.sheets[i]["name"], fmt(r))
                elif p in ("values", "formulas", "text"):
                    rows = []
                    for rr in range(r[0], r[2] + 1):
                        row = []
                        for cc in range(r[1], r[3] + 1):
                            v = cells.get((rr, cc))
                            if p == "values" and isinstance(v, str) and v.startswith("="):
                                v = "(formula)"
                            if p == "text":
                                v = "" if v is None else str(v)
                            row.append(v)
                        rows.append(row)
                    out[p] = rows
                elif p == "rowCount":
                    out[p] = r[2] - r[0] + 1
                elif p == "columnCount":
                    out[p] = r[3] - r[1] + 1
                elif p == "rowIndex":
                    out[p] = r[0]
                elif p == "columnIndex":
                    out[p] = r[1]
                else:
                    raise Fail("NotImplemented", "Range." + p)
        elif k in ("font", "fill"):
            st = self.sheets[obj[1]]["style"].get((obj[2][0], obj[2][1]), {})
            for p in props:
                if k == "fill":
                    out[p] = st.get("fill", "#FFFFFF")
                else:
                    out[p] = st.get("font." + p, False if p in ("bold", "italic") else None)
        elif k == "sheet":
            for p in props:
                out[p] = self.sheets[obj[1]]["name"] if p in ("name", "id") else obj[1]
        elif k == "sheets":
            out["items"] = [{"name": s["name"], "id": s["name"], "position": n} for n, s in enumerate(self.sheets)]
        elif k == "wb":
            out["name"] = self.name
        else:
            raise invalid("nothing to load here")
        return out

    def set(self, obj, prop, value):
        k = obj[0]
        if k == "range" and prop in ("values", "formulas"):
            i, r = obj[1], obj[2]
            h, w = r[2] - r[0] + 1, r[3] - r[1] + 1
            if isinstance(value, list):
                if len(value) != h or any(not isinstance(row, list) or len(row) != w for row in value):
                    raise invalid("the values are not {} rows of {} columns, the range's size".format(h, w))
                rows = value
            else:
                rows = [[value] * w for _ in range(h)]
            for dr, row in enumerate(rows):
                for dc, v in enumerate(row):
                    if v is None:
                        self.sheets[i]["cells"].pop((r[0] + dr, r[1] + dc), None)
                    else:
                        self.sheets[i]["cells"][(r[0] + dr, r[1] + dc)] = v
        elif k in ("font", "fill"):
            i, r = obj[1], obj[2]
            if k == "fill" and not isinstance(value, str):
                raise invalid("a colour is text")
            for rr in range(r[0], r[2] + 1):
                for cc in range(r[1], r[3] + 1):
                    st = self.sheets[i]["style"].setdefault((rr, cc), {})
                    if k == "fill":
                        st["fill"] = "#FFFFFF" if value == "none" else value.upper() if value.startswith("#") else value
                    else:
                        st["font." + prop] = value
        elif k == "sheet" and prop == "name":
            self.sheets[obj[1]]["name"] = value
        else:
            raise Fail("NotImplemented", "{}.{} can't be set".format(k, prop))

    def call(self, obj, method, args):
        k = obj[0]
        if k == "range" and method == "clear":
            i, r = obj[1], obj[2]
            for rr in range(r[0], r[2] + 1):
                for cc in range(r[1], r[3] + 1):
                    if args[0] in ("All", "Contents"):
                        self.sheets[i]["cells"].pop((rr, cc), None)
            return None
        if k == "range" and method == "select":
            self.active, self.sel = obj[1], obj[2]
            return None
        if k == "sheets" and method == "add":
            name = args[0] if args else "Sheet{}".format(len(self.sheets) + 1)
            if any(s["name"].lower() == name.lower() for s in self.sheets):
                raise invalid("a sheet is called " + name)
            self.sheets.append({"name": name, "cells": {}, "style": {}})
            return {"name": name}
        if k == "sheet" and method == "delete":
            del self.sheets[obj[1]]
            self.active = 0
            return None
        if k == "sheet" and method == "activate":
            self.active = obj[1]
            return None
        raise Fail("NotImplemented", "{}.{}()".format(k, method))

    def command(self, cmd, params):
        if cmd == "range.setValues":
            sheet, addr = self.active, params["range"]
            if "!" in addr:
                sheet = self.sheet_index(addr.rpartition("!")[0])
            self.put(sheet, addr, params["values"])
        elif cmd == "home.bold":
            r = parse_range(params["range"])
            self.sheets[self.active]["style"].setdefault((r[0], r[1]), {})["font.bold"] = True
        else:
            Model.command(self, cmd, params)

    def context(self, path=None, name="Book1"):
        """`commands/run`'s context, as GridCraft builds it."""
        sel = fmt(self.sel)
        return {"workbook": name, "sheet": self.sheets[self.active]["name"], "active": fmt(self.sel[:2] * 2),
                "selection": sel, "values": self.get(self.active, sel),
                "document": {"path": path, "name": name, "dirty": False}}


# ============================================================================ WordCraft

class Word(Model):
    def __init__(self, paragraphs=("",)):
        Model.__init__(self)
        self.paras = [{"text": t, "style": "Normal", "alignment": "Left", "font": {}} for t in paragraphs]
        self.sel = (0, 0, 0, 0)  # para, off, para, off
        self.props = {"title": "", "author": ""}

    def state(self):
        return {"paras": self.paras, "sel": self.sel, "props": self.props}

    def set_state(self, s):
        self.paras, self.sel, self.props = s["paras"], s["sel"], s["props"]

    def para(self, i):
        if not 0 <= i < len(self.paras):
            raise Fail("ItemNotFound", "there's no such paragraph")
        return self.paras[i]

    def parse_id(self, rid):
        m = re.match(r"^p:(\d+)$", str(rid))
        if m:
            self.para(int(m.group(1)))
            return ("para", int(m.group(1)))
        m = re.match(r"^r:(\d+):(\d+)-(\d+):(\d+)$", str(rid))
        if m:
            a, b, c, d = map(int, m.groups())
            self.para(a)
            self.para(c)
            return ("range", (a, b, c, d))
        raise Fail("ItemNotFound", "`{}` names nothing in the document".format(rid))

    def whole(self):
        return (0, 0, len(self.paras) - 1, len(self.paras[-1]["text"]))

    def resolve(self, path):
        obj = ("root",)
        for st in path:
            p, m, a = st.get("p"), st.get("m"), st.get("a", [])
            k = obj[0]
            if k == "root" and p == "document":
                obj = ("doc",)
            elif k == "doc" and p == "body":
                obj = ("body",)
            elif k == "doc" and p == "properties":
                obj = ("props",)
            elif k in ("doc", "body") and p == "paragraphs":
                obj = ("paras", None)
            elif k == "doc" and m == "getSelection":
                obj = ("range", self.sel)
            elif k == "doc" and m == "getRangeById":
                obj = self.parse_id(a[0] if a else "")
            elif k == "body" and p == "font":
                obj = ("font", self.whole())
            elif k == "paras" and m == "getById":
                got = self.parse_id(a[0] if a else "")
                if got[0] != "para":
                    raise Fail("ItemNotFound", "there's no such paragraph")
                obj = got
            elif k == "paras" and m == "getItemAt":
                self.para(a[0])
                obj = ("para", a[0])
            elif k == "para" and p == "font":
                obj = ("font", (obj[1], 0, obj[1], len(self.para(obj[1])["text"])))
            elif k == "para" and m == "getRange":
                obj = ("range", (obj[1], 0, obj[1], len(self.para(obj[1])["text"])))
            elif k == "range" and p == "font":
                obj = ("font", obj[1])
            elif k == "range" and p == "paragraphs":
                obj = ("paras", obj[1])
            else:
                raise invalid("{} has no {}".format(k, p or m))
        return obj

    def text_of(self, r):
        a, b, c, d = r
        out = []
        for i in range(a, c + 1):
            t = self.paras[i]["text"]
            out.append(t[(b if i == a else 0):(d if i == c else len(t))])
        return "\n".join(out)

    def load(self, obj, props):
        k = obj[0]
        out = {}
        for p in props:
            if k == "body" and p == "text":
                out[p] = self.text_of(self.whole())
            elif k == "paras" and p == "items":
                rng = obj[1] or self.whole()
                rest = [x for x in props if x not in ("items", "count")]
                out[p] = [dict(self.load(("para", i), rest), id="p:{}".format(i)) for i in range(rng[0], rng[2] + 1)]
            elif k == "paras" and p == "count":
                out[p] = len(self.paras)
            elif k == "paras":
                continue
            elif k == "para":
                para = self.para(obj[1])
                if p == "id":
                    out[p] = "p:{}".format(obj[1])
                elif p in ("text", "style", "alignment"):
                    out[p] = para[p]
                else:
                    out[p] = para.get(p, 0)
            elif k == "range":
                if p == "id":
                    out[p] = "r:{}:{}-{}:{}".format(*obj[1])
                elif p == "text":
                    out[p] = self.text_of(obj[1])
                elif p == "isEmpty":
                    out[p] = obj[1][0:2] == obj[1][2:4]
                elif p == "style":
                    out[p] = self.paras[obj[1][0]]["style"]
                else:
                    raise invalid("Range has no property `{}`".format(p))
            elif k == "font":
                vals = [self.paras[i]["font"].get(p) for i in range(obj[1][0], obj[1][2] + 1)]
                out[p] = vals[0] if all(v == vals[0] for v in vals) else None
            elif k == "props":
                out[p] = self.props.get(p, "")
            elif k == "doc" and p == "name":
                out[p] = "Document1"
            else:
                raise invalid("{} has no property `{}`".format(k, p))
        return out

    def set(self, obj, prop, value):
        k = obj[0]
        if k == "para" and prop in ("style", "alignment"):
            if prop == "alignment" and value not in ("Left", "Centered", "Right", "Justified"):
                raise invalid("alignment is Left, Centered, Right or Justified")
            self.para(obj[1])[prop] = value
        elif k == "para":
            self.para(obj[1])[prop] = value
        elif k == "font":
            for i in range(obj[1][0], obj[1][2] + 1):
                self.paras[i]["font"][prop] = value
        elif k == "props":
            self.props[prop] = value
        else:
            raise invalid("{}.{} can't be set".format(k, prop))

    def insert_para(self, at, text):
        if "\n" in text:
            raise invalid("a paragraph's text has no line breaks (insert one paragraph at a time)")
        self.paras.insert(at, {"text": text, "style": "Normal", "alignment": "Left", "font": {}})
        return {"id": "p:{}".format(at)}

    def insert_text(self, r, text, loc):
        a, b, c, d = r
        if loc in ("Start", "Before"):
            c, d = a, b
        elif loc in ("End", "After"):
            a, b = c, d
        first, last = self.paras[a]["text"][:b], self.paras[c]["text"][d:]
        del self.paras[a + 1:c + 1]
        self.paras[a]["text"] = first + text + last
        return {"id": "r:{}:{}-{}:{}".format(a, b, a, b + len(text)), "text": text}

    def call(self, obj, method, args):
        k = obj[0]
        if k == "body" and method == "insertParagraph":
            return self.insert_para(0 if args[1] == "Start" else len(self.paras), args[0])
        if k == "para" and method == "insertParagraph":
            return self.insert_para(obj[1] + (1 if args[1] == "After" else 0), args[0])
        if k == "range" and method == "insertParagraph":
            return self.insert_para(obj[1][2] + 1 if args[1] == "After" else obj[1][0], args[0])
        if method == "insertText":
            r = {"body": self.whole, "para": lambda: (obj[1], 0, obj[1], len(self.para(obj[1])["text"])),
                 "range": lambda: obj[1]}[k]()
            return self.insert_text(r, args[0], args[1] if len(args) > 1 else "Replace")
        if k == "para" and method == "delete":
            if len(self.paras) > 1:
                del self.paras[obj[1]]
            else:
                self.paras[0]["text"] = ""
            return None
        if k == "body" and method == "search":
            items = []
            for i, p in enumerate(self.paras):
                for m in re.finditer(re.escape(args[0]), p["text"], 0 if (args[1] or {}).get("matchCase") else re.I):
                    items.append({"id": "r:{}:{}-{}:{}".format(i, m.start(), i, m.end()), "text": m.group(0)})
            return {"items": items}
        if k == "body" and method == "clear":
            self.paras = [{"text": "", "style": "Normal", "alignment": "Left", "font": {}}]
            return None
        if method == "select":
            return None
        raise Fail("NotImplemented", "{}.{}()".format(k, method))

    def command(self, cmd, params):
        if cmd == "text.insert":
            self.insert_text(self.sel, params["text"], "Replace")
        else:
            Model.command(self, cmd, params)

    def context(self, path=None, name=None):
        sel = self.text_of(self.sel)
        return {"document": {"path": path, "name": name, "dirty": False},
                "selection": {"text": sel, "empty": not sel}, "paragraph": {"text": self.paras[self.sel[2]]["text"]}}


# ============================================================================ DeckCraft

class Deck(Model):
    def __init__(self, slides=1):
        Model.__init__(self)
        self.next = 300
        self.slides = []
        for _ in range(slides):
            self.slides.append({"id": self.new_id(), "shapes": []})
        self.current = 0
        self.selected_shapes = []
        self.selected_slides = []

    def new_id(self):
        self.next += 1
        return str(self.next)

    def state(self):
        return {"next": self.next, "slides": self.slides, "current": self.current, "ss": self.selected_shapes,
                "sl": self.selected_slides}

    def set_state(self, s):
        self.next, self.slides, self.current = s["next"], s["slides"], s["current"]
        self.selected_shapes, self.selected_slides = s["ss"], s["sl"]

    def slide(self, sid):
        for s in self.slides:
            if s["id"] == str(sid):
                return s
        raise Fail("ItemNotFound", "there's no slide {}".format(sid))

    def shape(self, sid, shid):
        for sh in self.slide(sid)["shapes"]:
            if sh["id"] == str(shid):
                return sh
        raise Fail("ItemNotFound", "there's no shape {} on slide {}".format(shid, sid))

    def resolve(self, path):
        obj = ("root",)
        for st in path:
            p, m, a = st.get("p"), st.get("m"), st.get("a", [])
            k = obj[0]
            if k == "root" and p == "presentation":
                obj = ("pres",)
            elif k == "pres" and p == "slides":
                obj = ("slides", False)
            elif k == "pres" and m == "getSelectedSlides":
                obj = ("slides", True)
            elif k == "pres" and m == "getSelectedShapes":
                obj = ("shapes", self.slides[self.current]["id"], True)
            elif k == "slides" and m == "getItem":
                obj = ("slide", self.slide(a[0])["id"])
            elif k == "slides" and m == "getItemAt":
                ids = self.slide_ids(obj[1])
                if not 0 <= a[0] < len(ids):
                    raise Fail("ItemNotFound", "there's no slide at index {}".format(a[0]))
                obj = ("slide", ids[a[0]])
            elif k == "slide" and p == "shapes":
                obj = ("shapes", obj[1], False)
            elif k == "shapes" and m == "getItem":
                self.shape(obj[1], a[0])
                obj = ("shape", obj[1], str(a[0]))
            elif k == "shape" and p == "textFrame":
                if self.shape(obj[1], obj[2])["text"] is None:
                    raise invalid("shape {} has no text frame".format(obj[2]))
                obj = ("frame",) + obj[1:]
            elif k == "frame" and p == "textRange":
                obj = ("text",) + obj[1:]
            elif k == "text" and p == "font":
                obj = ("font",) + obj[1:]
            elif k == "shape" and p in ("fill", "lineFormat"):
                obj = (p,) + obj[1:]
            else:
                raise invalid("{} has no {}".format(k, p or m))
        return obj

    def slide_ids(self, selected):
        if selected:
            return list(self.selected_slides) or [self.slides[self.current]["id"]]
        return [s["id"] for s in self.slides]

    def shape_ids(self, sid, selected):
        if selected:
            return list(self.selected_shapes)
        return [sh["id"] for sh in self.slide(sid)["shapes"]]

    def load(self, obj, props):
        k = obj[0]
        out = {}
        for p in props:
            if k == "slides" and p == "items":
                out[p] = [{"id": i} for i in self.slide_ids(obj[1])]
            elif k == "slides" and p == "count":
                out[p] = len(self.slide_ids(obj[1]))
            elif k == "shapes" and p == "items":
                rest = [x for x in props if x not in ("items", "count")]
                out[p] = [dict(self.load(("shape", obj[1], i), rest), id=i) for i in self.shape_ids(obj[1], obj[2])]
            elif k == "shapes" and p == "count":
                out[p] = len(self.shape_ids(obj[1], obj[2]))
            elif k in ("slides", "shapes"):
                continue
            elif k == "slide" and p == "id":
                out[p] = obj[1]
            elif k == "slide" and p == "index":
                out[p] = [s["id"] for s in self.slides].index(obj[1])
            elif k == "shape":
                sh = self.shape(obj[1], obj[2])
                if p not in ("id", "name", "type", "left", "top", "width", "height"):
                    raise invalid("Shape has no property `{}`".format(p))
                out[p] = sh[p]
            elif k == "text" and p == "text":
                out[p] = self.shape(obj[1], obj[2])["text"]
            elif k == "font":
                out[p] = self.shape(obj[1], obj[2])["font"].get(p)
            elif k == "fill":
                sh = self.shape(obj[1], obj[2])
                out[p] = {"foregroundColor": sh["fill"], "type": "solid" if sh["fill"] else "noFill",
                          "transparency": 0.0}.get(p)
            elif k == "pres" and p == "title":
                out[p] = "Presentation1"
            else:
                raise invalid("{} has no property `{}`".format(k, p))
        return out

    def set(self, obj, prop, value):
        k = obj[0]
        if k == "shape" and prop in ("name", "left", "top", "width", "height"):
            self.shape(obj[1], obj[2])[prop] = value
        elif k == "text" and prop == "text":
            self.shape(obj[1], obj[2])["text"] = value
        elif k == "font":
            self.shape(obj[1], obj[2])["font"][prop] = value
        elif k == "fill" and prop == "foregroundColor":
            self.shape(obj[1], obj[2])["fill"] = value
        else:
            raise invalid("{}.{} can't be set".format(k, prop))

    def add_shape(self, sid, kind, text, box):
        sh = {"id": self.new_id(), "name": "{} {}".format(kind, self.next), "type": kind,
              "left": box.get("left", 100), "top": box.get("top", 100), "width": box.get("width", 200),
              "height": box.get("height", 100), "text": text, "font": {}, "fill": None}
        self.slide(sid)["shapes"].append(sh)
        return {"id": sh["id"]}

    def call(self, obj, method, args):
        k = obj[0]
        if k == "slides" and method == "add":
            o = args[0] if args else {}
            s = {"id": self.new_id(), "shapes": []}
            at = o.get("index", len(self.slides)) if isinstance(o, dict) else len(self.slides)
            self.slides.insert(at, s)
            return {"id": s["id"]}
        if k == "slide" and method == "delete":
            self.slides.remove(self.slide(obj[1]))
            self.current = 0
            return None
        if k == "shapes" and method == "addTextBox":
            return self.add_shape(obj[1], "textBox", args[0], args[1] if len(args) > 1 else {})
        if k == "shapes" and method == "addGeometricShape":
            if args[0] not in ("rectangle", "ellipse", "triangle"):
                raise invalid("`{}` isn't a shape type".format(args[0]))
            return self.add_shape(obj[1], args[0], "", args[1] if len(args) > 1 else {})
        if k == "shapes" and method == "addLine":
            return self.add_shape(obj[1], "line", None, args[1] if len(args) > 1 else {})
        if k == "shape" and method == "delete":
            sl = self.slide(obj[1])
            sl["shapes"] = [s for s in sl["shapes"] if s["id"] != obj[2]]
            return None
        if k == "fill" and method == "setSolidColor":
            self.shape(obj[1], obj[2])["fill"] = args[0]
            return None
        if k == "fill" and method == "clear":
            self.shape(obj[1], obj[2])["fill"] = None
            return None
        if k == "pres" and method == "setSelectedSlides":
            self.selected_slides = [str(x) for x in args[0]]
            return None
        raise Fail("NotImplemented", "{}.{}()".format(k, method))

    def context(self, path=None, name="Presentation1"):
        cur = self.slides[self.current] if self.slides else None
        return {"document": {"path": path, "name": name, "dirty": False}, "presentation": name,
                "slide": self.current, "slideId": cur["id"] if cur else None, "slides": len(self.slides),
                "selection": list(self.selected_shapes), "text": None}
