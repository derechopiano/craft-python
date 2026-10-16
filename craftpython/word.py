"""`doc` in WordCraft: the body, paragraphs, ranges and fonts (WordApi's objects through the bridge).

Word's API has no ids; WordCraft hands paragraphs and ranges opaque ones (`p:…`, `r:…`) that keep
naming the same text until paragraphs come or go before them. So read `doc.paragraphs` again after
inserting or deleting paragraphs.
"""

from .bridge import Base, BridgeError

DOC = [{"p": "document"}]
BODY = DOC + [{"p": "body"}]
PARAS = BODY + [{"p": "paragraphs"}]

# Python names → WordApi's.
FONT_PROPS = {
    "bold": "bold", "italic": "italic", "underline": "underline", "size": "size", "name": "name",
    "color": "color", "highlight_color": "highlightColor", "strike_through": "strikeThrough",
    "double_strike_through": "doubleStrikeThrough", "subscript": "subscript", "superscript": "superscript",
}
PARA_PROPS = {
    "text": "text", "style": "style", "style_built_in": "styleBuiltIn", "alignment": "alignment",
    "first_line_indent": "firstLineIndent", "left_indent": "leftIndent", "right_indent": "rightIndent",
    "line_spacing": "lineSpacing", "space_after": "spaceAfter", "space_before": "spaceBefore",
    "is_list_item": "isListItem",
}
LISTED = ["text", "style", "alignment"]


def _location(loc, allowed):
    loc = str(loc).strip().lower()
    for a in allowed:
        if a.lower() == loc:
            return a
    raise ValueError("location is one of " + ", ".join(allowed))


class Font:
    """`bold`, `italic`, `underline` ("None", "Single", "Double"…), `size`, `name`, `color`,
    `highlight_color` (a highlight name, "#RRGGBB" or None), `strike_through`, `subscript`,
    `superscript`. Reads give None where the text is mixed."""

    def __init__(self, bridge, path):
        object.__setattr__(self, "_b", bridge)
        object.__setattr__(self, "_path", path + [{"p": "font"}])

    def __getattr__(self, name):
        prop = FONT_PROPS.get(name)
        if prop is None:
            raise AttributeError(name)
        return self._b.load(self._path, [prop]).get(prop)

    def __setattr__(self, name, value):
        prop = FONT_PROPS.get(name)
        if prop is None:
            raise AttributeError("Font has no property {!r}".format(name))
        self._b.set(self._path, prop, value)

    def set(self, **props):
        """Several at once: `font.set(bold=True, size=14)` (one round trip)."""
        with self._b.batch():
            for k, v in props.items():
                setattr(self, k, v)

    def get(self):
        """All of them, as a dict."""
        got = self._b.load(self._path, list(FONT_PROPS.values()))
        return {k: got.get(v) for k, v in FONT_PROPS.items()}

    def __dir__(self):
        return sorted(FONT_PROPS) + ["get", "set"]


class Range:
    """A stretch of the body's text: `text`, `font`, `style`, `insert_text`, `delete`, `select`."""

    def __init__(self, bridge, rid, text=None):
        self._b = bridge
        self.id = rid
        self._text = text

    @property
    def _path(self):
        return DOC + [{"m": "getRangeById", "a": [self.id]}]

    @property
    def text(self):
        return self._b.load(self._path, ["text"]).get("text")

    @text.setter
    def text(self, value):
        self.insert_text(str(value), "Replace")

    @property
    def style(self):
        return self._b.load(self._path, ["style"]).get("style")

    @style.setter
    def style(self, name):
        self._b.set(self._path, "style", str(name))

    @property
    def is_empty(self):
        return bool(self._b.load(self._path, ["isEmpty"]).get("isEmpty"))

    @property
    def font(self):
        return Font(self._b, self._path)

    @property
    def paragraphs(self):
        """The paragraphs the range touches."""
        return _paragraphs(self._b, self._path + [{"p": "paragraphs"}])

    def insert_text(self, text, location="Replace"):
        """Inserts `text` ("Replace", "Start", "End", "Before", "After"); the inserted text's range."""
        loc = _location(location, ["Replace", "Start", "End", "Before", "After"])
        got = self._b.call(self._path, "insertText", [str(text), loc]) or {}
        return Range(self._b, got.get("id"), str(text)) if got.get("id") else None

    def insert_paragraph(self, text, location="After"):
        loc = _location(location, ["Before", "After"])
        return _insert_paragraphs(self._b, self._path, text, loc)

    def insert_break(self, kind="Page", location="After"):
        self._b.call(self._path, "insertBreak", [kind, _location(location, ["Before", "After"])], result=False)

    def delete(self):
        self._b.call(self._path, "delete", result=False)

    def select(self, mode="Select"):
        self._b.call(self._path, "select", [_location(mode, ["Select", "Start", "End"])], result=False)

    def __repr__(self):
        shown = self._text if self._text is not None else self.id
        return "<Range {!r}>".format(shown)


class Paragraph:
    """One paragraph: `text` (setting it replaces the text), `style`, `alignment` ("Left",
    "Centered", "Right", "Justified"), indents and spacing in points, `font`; `insert_paragraph`,
    `insert_text`, `delete`, `select`. Values read when the list was made are kept until you
    change them here or call `refresh()`."""

    def __init__(self, bridge, pid, values=None):
        object.__setattr__(self, "_b", bridge)
        object.__setattr__(self, "id", pid)
        object.__setattr__(self, "_cache", dict(values or {}))

    @property
    def _path(self):
        return PARAS + [{"m": "getById", "a": [self.id]}]

    def refresh(self):
        got = self._b.load(self._path, list(PARA_PROPS.values()))
        self._cache.clear()
        self._cache.update(got)
        return self

    def __getattr__(self, name):
        prop = PARA_PROPS.get(name)
        if prop is None:
            raise AttributeError(name)
        if prop not in self._cache:
            self._cache.update(self._b.load(self._path, [prop] if prop not in LISTED else LISTED))
        return self._cache.get(prop)

    def __setattr__(self, name, value):
        prop = PARA_PROPS.get(name)
        if prop is None or prop in ("isListItem",):
            raise AttributeError("Paragraph has no settable property {!r}".format(name))
        if prop == "text":
            self._b.call(self._path, "insertText", [str(value), "Replace"], result=False)
            self._cache["text"] = str(value)
            return
        self._b.set(self._path, prop, value)
        self._cache.pop(prop, None)
        if prop == "styleBuiltIn":
            self._cache.pop("style", None)

    @property
    def font(self):
        return Font(self._b, self._path)

    @property
    def range(self):
        got = self._b.load(self._path + [{"m": "getRange", "a": ["Whole"]}], ["id", "text"])
        return Range(self._b, got.get("id"), got.get("text"))

    def insert_paragraph(self, text, location="After"):
        """A new paragraph "Before" or "After" this one (several for text with line breaks)."""
        return _insert_paragraphs(self._b, self._path, text, _location(location, ["Before", "After"]))

    def insert_text(self, text, location="End"):
        loc = _location(location, ["Start", "End", "Replace"])
        got = self._b.call(self._path, "insertText", [str(text), loc]) or {}
        self._cache.pop("text", None)
        return Range(self._b, got.get("id"), str(text)) if got.get("id") else None

    def insert_break(self, kind="Page", location="After"):
        self._b.call(self._path, "insertBreak", [kind, _location(location, ["Before", "After"])], result=False)

    def delete(self):
        self._b.call(self._path, "delete", result=False)

    def select(self, mode="Select"):
        self._b.call(self._path, "select", [_location(mode, ["Select", "Start", "End"])], result=False)

    def __repr__(self):
        t = self._cache.get("text")
        return "<Paragraph {}>".format(repr(t[:40] + ("…" if len(t) > 40 else "")) if isinstance(t, str) else self.id)


def _paragraphs(bridge, path):
    items = bridge.load(path, ["items"] + LISTED).get("items") or []
    return [Paragraph(bridge, i.get("id"), {k: i.get(k) for k in LISTED if k in i}) for i in items if isinstance(i, dict)]


def _insert_paragraphs(bridge, path, text, location):
    """Inserts one paragraph per line of `text` at `location` of `path`; the last one inserted."""
    lines = str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    last = None
    for i, line in enumerate(lines):
        if i == 0:
            # The first line goes where asked; each next one after the line before it, in order.
            got = bridge.call(path, "insertParagraph", [line, location]) or {}
            last = got.get("id")
        else:
            got = bridge.call(PARAS + [{"m": "getById", "a": [last]}], "insertParagraph", [line, "After"]) or {}
            last = got.get("id")
    return Paragraph(bridge, last, {"text": lines[-1]}) if last else None


class Body:
    """The main text: `text`, `paragraphs`, `font`, `style`; `insert_paragraph`, `insert_text`, `clear`."""

    def __init__(self, bridge):
        self._b = bridge

    @property
    def text(self):
        return self._b.load(BODY, ["text"]).get("text")

    @property
    def paragraphs(self):
        return _paragraphs(self._b, PARAS)

    @property
    def style(self):
        return self._b.load(BODY, ["style"]).get("style")

    @style.setter
    def style(self, name):
        self._b.set(BODY, "style", str(name))

    @property
    def font(self):
        return Font(self._b, BODY)

    def insert_paragraph(self, text, location="End"):
        """Adds a paragraph at the "Start" or "End" (one per line of `text`); the last one added."""
        return _insert_paragraphs(self._b, BODY, text, _location(location, ["Start", "End"]))

    def insert_text(self, text, location="End"):
        loc = _location(location, ["Start", "End", "Replace"])
        got = self._b.call(BODY, "insertText", [str(text), loc]) or {}
        return Range(self._b, got.get("id"), str(text)) if got.get("id") else None

    def insert_break(self, kind="Page", location="End"):
        self._b.call(BODY, "insertBreak", [kind, _location(location, ["Start", "End"])], result=False)

    def clear(self):
        self._b.call(BODY, "clear", result=False)

    def search(self, text, match_case=False, whole_word=False):
        got = self._b.call(BODY, "search", [str(text), {"matchCase": bool(match_case), "matchWholeWord": bool(whole_word)}]) or {}
        return [Range(self._b, i.get("id"), i.get("text")) for i in got.get("items") or [] if isinstance(i, dict)]


class Properties:
    """The document's `title`, `subject`, `author`, `keywords`, `comments` (read and write)."""

    _names = ("title", "subject", "author", "keywords", "comments")

    def __init__(self, bridge):
        object.__setattr__(self, "_b", bridge)

    def __getattr__(self, name):
        if name not in self._names:
            raise AttributeError(name)
        return self._b.load(DOC + [{"p": "properties"}], [name]).get(name)

    def __setattr__(self, name, value):
        if name not in self._names:
            raise AttributeError("no document property {!r}".format(name))
        self._b.set(DOC + [{"p": "properties"}], name, str(value))


class Document(Base):
    """The document a script runs in (`doc` in WordCraft)."""

    @property
    def body(self):
        return Body(self._b)

    @property
    def paragraphs(self):
        return self.body.paragraphs

    @property
    def text(self):
        return self.body.text

    @property
    def name(self):
        return self._b.load(DOC, ["name"]).get("name")

    @property
    def properties(self):
        return Properties(self._b)

    @property
    def selection(self):
        """The selected text as a Range (its `text`, `insert_text`, `font`…)."""
        try:
            got = self._b.load(DOC + [{"m": "getSelection"}], ["id", "text"])
        except BridgeError as e:
            raise BridgeError(e.code, e.message + " (select text in the document's body)") from None
        return Range(self._b, got.get("id"), got.get("text"))

    def search(self, text, match_case=False, whole_word=False):
        """Every match of `text` in the body, as Ranges."""
        return self.body.search(text, match_case, whole_word)

    def __repr__(self):
        return "<Document>"
