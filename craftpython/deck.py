"""`doc` in DeckCraft: slides, shapes, text, fills (PowerPointApi's objects through the bridge).

Slides and shapes have ids that stay theirs while the presentation is open, so objects kept in
variables stay good after other slides or shapes come and go.
"""

from .bridge import Base, BridgeError

PRES = [{"p": "presentation"}]
SLIDES = PRES + [{"p": "slides"}]
SHAPE_PROPS = ["name", "type", "left", "top", "width", "height"]


def _box(left, top, width, height):
    box = {}
    for k, v in (("left", left), ("top", top), ("width", width), ("height", height)):
        if v is not None:
            box[k] = float(v)
    return box


class _Props:
    _names = {}

    def __init__(self, bridge, path):
        object.__setattr__(self, "_b", bridge)
        object.__setattr__(self, "_path", path)

    def __getattr__(self, name):
        prop = self._names.get(name)
        if prop is None:
            raise AttributeError(name)
        return self._b.load(self._path, [prop]).get(prop)

    def __setattr__(self, name, value):
        prop = self._names.get(name)
        if prop is None:
            raise AttributeError("{} has no property {!r}".format(type(self).__name__, name))
        self._b.set(self._path, prop, value)

    def set(self, **props):
        """Several at once (one round trip)."""
        with self._b.batch():
            for k, v in props.items():
                setattr(self, k, v)

    def __dir__(self):
        return sorted(self._names) + ["set"]


class Font(_Props):
    """A shape's text font: `bold`, `italic`, `underline` ("none", "single", "double"…), `size`,
    `name`, `color` (None when mixed)."""

    _names = {"bold": "bold", "italic": "italic", "underline": "underline", "size": "size", "name": "name",
              "color": "color"}


class Fill(_Props):
    """`color` ("#RRGGBB" or a colour name; None without a solid fill), `transparency` (0-1),
    `type`; `clear()`."""

    _names = {"color": "foregroundColor", "transparency": "transparency", "type": "type"}

    def __setattr__(self, name, value):
        if name == "color":
            self._b.call(self._path, "setSolidColor", [value], result=False)
            return
        if name == "type":
            raise AttributeError("set a color, or clear() the fill")
        _Props.__setattr__(self, name, value)

    def clear(self):
        self._b.call(self._path, "clear", result=False)


class Line(_Props):
    """The outline: `color`, `weight` (points), `visible`, `dash_style`."""

    _names = {"color": "color", "weight": "weight", "visible": "visible", "dash_style": "dashStyle"}


class Shape:
    """A shape on a slide: `id`, `name`, `type`, `left`/`top`/`width`/`height` (points), `text`,
    `font`, `fill`, `line`; `move`, `resize`, `delete`. Position, size and name are read when the
    shape list was made; setting them here keeps them current."""

    def __init__(self, bridge, slide_id, shape_id, values=None):
        object.__setattr__(self, "_b", bridge)
        object.__setattr__(self, "slide_id", str(slide_id))
        object.__setattr__(self, "id", str(shape_id))
        object.__setattr__(self, "_cache", dict(values or {}))

    @property
    def _path(self):
        return SLIDES + [{"m": "getItem", "a": [self.slide_id]}, {"p": "shapes"}, {"m": "getItem", "a": [self.id]}]

    def __getattr__(self, name):
        if name not in SHAPE_PROPS:
            raise AttributeError(name)
        if name not in self._cache:
            self._cache.update(self._b.load(self._path, SHAPE_PROPS))
        return self._cache.get(name)

    def __setattr__(self, name, value):
        if name == "text":
            self._b.set(self._text_path(), "text", str(value))
            return
        if name not in ("name", "left", "top", "width", "height"):
            raise AttributeError("Shape has no settable property {!r}".format(name))
        if name != "name":
            value = float(value)
        self._b.set(self._path, name, value)
        self._cache[name] = value

    def refresh(self):
        self._cache.clear()
        self._cache.update(self._b.load(self._path, SHAPE_PROPS))
        return self

    def _text_path(self):
        return self._path + [{"p": "textFrame"}, {"p": "textRange"}]

    @property
    def has_text_frame(self):
        try:
            self._b.load(self._path + [{"p": "textFrame"}], ["hasText"])
            return True
        except BridgeError:
            return False

    @property
    def text(self):
        """The shape's text (paragraphs joined by "\\n"), or None for a shape without text (a picture, a line)."""
        try:
            return self._b.load(self._text_path(), ["text"]).get("text")
        except BridgeError as e:
            if "text frame" in (e.message or ""):
                return None
            raise

    @property
    def font(self):
        return Font(self._b, self._text_path() + [{"p": "font"}])

    @property
    def fill(self):
        return Fill(self._b, self._path + [{"p": "fill"}])

    @property
    def line(self):
        return Line(self._b, self._path + [{"p": "lineFormat"}])

    def move(self, left, top):
        with self._b.batch():
            self.left = left
            self.top = top

    def resize(self, width, height):
        with self._b.batch():
            self.width = width
            self.height = height

    def delete(self):
        self._b.call(self._path, "delete", result=False)

    def __repr__(self):
        name = self._cache.get("name")
        return "<Shape {} {}>".format(self.id, repr(name) if name else "")


class Shapes:
    """A slide's shapes: a list (`len`, `[i]`, iteration) plus `add_text_box`, `add_shape`, `add_line`."""

    def __init__(self, bridge, slide_id, selected=False):
        self._b = bridge
        self._slide = str(slide_id)
        self._selected = selected

    @property
    def _path(self):
        if self._selected:
            return PRES + [{"m": "getSelectedShapes"}]
        return SLIDES + [{"m": "getItem", "a": [self._slide]}, {"p": "shapes"}]

    def _list(self):
        items = self._b.load(self._path, ["items"] + SHAPE_PROPS).get("items") or []
        return [Shape(self._b, self._slide, i.get("id"), {k: i.get(k) for k in SHAPE_PROPS if k in i})
                for i in items if isinstance(i, dict)]

    def __iter__(self):
        return iter(self._list())

    def __len__(self):
        return int(self._b.load(self._path, ["count"]).get("count") or 0)

    def __getitem__(self, i):
        return self._list()[i]

    def _add(self, method, first, left, top, width, height):
        got = self._b.call(SLIDES + [{"m": "getItem", "a": [self._slide]}, {"p": "shapes"}], method,
                           [first, _box(left, top, width, height)]) or {}
        return Shape(self._b, self._slide, got.get("id"))

    def add_text_box(self, text, left=100, top=100, width=200, height=100):
        """A text box (points from the slide's top-left); the new Shape."""
        return self._add("addTextBox", str(text), left, top, width, height)

    def add_shape(self, kind="rectangle", left=100, top=100, width=200, height=100):
        """A geometric shape: "rectangle", "ellipse", "triangle", "roundRectangle", "star5"… (the
        OOXML preset names); the new Shape."""
        return self._add("addGeometricShape", str(kind), left, top, width, height)

    def add_line(self, left=100, top=100, width=200, height=0):
        return self._add("addLine", "straight", left, top, width, height)

    def __repr__(self):
        return "<Shapes of slide {}>".format(self._slide)


class Slide:
    """A slide: `id`, `index` (from 0), `shapes`, `layout`; `delete`, `select`."""

    def __init__(self, bridge, slide_id, index=None):
        self._b = bridge
        self.id = str(slide_id)
        self._index = index

    @property
    def _path(self):
        return SLIDES + [{"m": "getItem", "a": [self.id]}]

    @property
    def index(self):
        return self._b.load(self._path, ["index"]).get("index")

    @property
    def shapes(self):
        return Shapes(self._b, self.id)

    @property
    def layout(self):
        """The slide's layout name."""
        return self._b.load(self._path + [{"p": "layout"}], ["name"]).get("name")

    @property
    def title(self):
        """The text of the slide's first shape that has any, a quick look at what it's about."""
        for sh in self.shapes:
            t = sh.text
            if t:
                return t
        return ""

    def delete(self):
        self._b.call(self._path, "delete", result=False)

    def select(self):
        self._b.call(PRES, "setSelectedSlides", [[self.id]], result=False)

    def __eq__(self, other):
        return isinstance(other, Slide) and other.id == self.id

    def __hash__(self):
        return hash(self.id)

    def __repr__(self):
        return "<Slide {}>".format(self.id)


class Slides:
    """The slides: a list (`len`, `[i]`, iteration, `index(slide)`) plus `add`."""

    def __init__(self, bridge, selected=False):
        self._b = bridge
        self._selected = selected

    @property
    def _path(self):
        return PRES + [{"m": "getSelectedSlides"}] if self._selected else SLIDES

    def _list(self):
        items = self._b.load(self._path, ["items"]).get("items") or []
        return [Slide(self._b, i.get("id"), n) for n, i in enumerate(items) if isinstance(i, dict)]

    def __iter__(self):
        return iter(self._list())

    def __len__(self):
        return int(self._b.load(self._path, ["count"]).get("count") or 0)

    def __getitem__(self, i):
        return self._list()[i]

    def index(self, slide):
        ids = [s.id for s in self._list()]
        return ids.index(slide.id if isinstance(slide, Slide) else str(slide))

    def add(self, layout=None, index=None):
        """A new slide (after the last, or at `index` from 0), with the layout of that id or name; the new Slide."""
        opts = {}
        if layout is not None:
            opts["layoutId"] = self._layout_id(layout)
        if index is not None:
            opts["index"] = int(index)
        got = self._b.call(SLIDES, "add", [opts]) or {}
        return Slide(self._b, got.get("id"))

    def _layout_id(self, layout):
        text = str(layout)
        if text.isdigit():
            return text
        masters = self._b.load(PRES + [{"p": "slideMasters"}], ["items"]).get("items") or []
        for mi in range(len(masters)):
            layouts = self._b.load(PRES + [{"p": "slideMasters"}, {"m": "getItemAt", "a": [mi]}, {"p": "layouts"}],
                                   ["items"]).get("items") or []
            for l in layouts:
                if isinstance(l, dict) and str(l.get("name", "")).lower() == text.lower():
                    return str(l.get("id"))
        raise KeyError("no layout called {!r}".format(text))

    def __repr__(self):
        return "<Slides>"


class Presentation(Base):
    """The presentation a script runs in (`doc` in DeckCraft)."""

    @property
    def title(self):
        return self._b.load(PRES, ["title"]).get("title")

    @property
    def slides(self):
        return Slides(self._b)

    def add_slide(self, layout=None, index=None):
        return Slides(self._b).add(layout, index)

    @property
    def selected_slides(self):
        return Slides(self._b, selected=True)._list()

    @property
    def current_slide(self):
        """The slide being shown when the script started (or the first selected one)."""
        sid = self._context.get("slideId")
        if sid:
            return Slide(self._b, sid)
        sel = self.selected_slides
        return sel[0] if sel else None

    @property
    def selected_shapes(self):
        """The selected shapes of the current slide."""
        cur = self.current_slide
        if cur is None:
            return []
        return Shapes(self._b, cur.id, selected=True)._list()

    @property
    def layouts(self):
        """[(id, name)] of the first slide master's layouts."""
        got = self._b.load(PRES + [{"p": "slideMasters"}, {"m": "getItemAt", "a": [0]}, {"p": "layouts"}], ["items"])
        return [(str(l.get("id")), l.get("name")) for l in got.get("items") or [] if isinstance(l, dict)]

    def __repr__(self):
        return "<Presentation>"
