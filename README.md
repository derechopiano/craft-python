# Python for GridCraft, WordCraft and DeckCraft

A Python scripting add-in for the craft apps: [GridCraft](https://github.com/storytold/gridcraft)
(spreadsheets), [WordCraft](https://github.com/storytold/wordcraft) (documents) and
[DeckCraft](https://github.com/storytold/deckcraft) (presentations). In each of them:

- **Run Script…** runs Python that reads and changes the open document through `doc`;
- **Console…** evaluates Python in a workspace that keeps what you define;
- **Run File…** runs a `.py` file;
- in GridCraft, **`PY.EVAL`** puts Python in formulas, next to ready-made helpers (`PY.STATS`,
  `PY.REGEX`…).

All a script's changes are **one undo step**. It is written with Python's standard library only,
so it needs nothing but `python3` (3.8 or later); packages you installed for that Python can be
imported by your scripts too.

## Install

In the app, open **File › Options › Add-ins** (GridCraft also has **Automate › Add-ins**), paste

```
sirmmo/gridcraft-python-plugin
```

in **Install from GitHub** and confirm. The app takes the latest release's
`craft-python-<version>.zip` (the same zip on every platform) and loads it; a **Python** tab
appears on the ribbon. To use a copy of this folder instead, **Load** it from the Add-ins window,
or start the app with `--plugin <folder>`.

From the command line:

```sh
gridcraft-cli run --cmd 'plugins.install={"url":"sirmmo/gridcraft-python-plugin","dir":"'"$HOME"'/plugins"}'
gridcraft-cli --plugin ~/plugins/io.github.sirmmo.python eval '=PY.STATS(1,2,3,4)'
```

### Requirements

- **GridCraft, WordCraft or DeckCraft 0.7 or later** (plugin protocol 1.2: dialogs and
  `context.document`). The manifest says so (`requires`), so an older app lists the add-in as
  blocked with the reason instead of failing later.
- **Python 3.8 or later** as `python3` on the `PATH` the app sees.
  - **Linux, BSD:** your distribution's `python3`.
  - **macOS:** `python3` comes with the Command Line Tools (the first run may offer to install
    them), or from [python.org](https://www.python.org/downloads/) or Homebrew. An app started from
    the Dock doesn't see your shell's `PATH`: if the add-in can't start, set `run.command` in
    `plugin.json` to the full path (`/opt/homebrew/bin/python3`).
  - **Windows:** install Python from [python.org](https://www.python.org/downloads/windows/) and
    tick *Add python.exe to PATH*; the installer's `python3` alias is the Microsoft Store stub, so
    set `run.command` in `plugin.json` to `python` or `py` if `python3` opens the Store.

**About** (Python tab) shows the add-in and Python versions, the interpreter, and whether Python
is on for the open document.

## Examples

### GridCraft

Select a table with a header row, then **Python › Run Script…**:

```python
rows = doc.selection.records()                 # [{"name": "apple", "qty": 3}, ...]
total = sum(r["qty"] for r in rows)
out = doc.add_sheet("Summary")
out.write("A1", [["items", "total"], [len(rows), total]])
out.range("A1:B1").format.font.bold = True
out.range("A1:B1").format.fill.color = "lightblue"
f"{len(rows)} rows, {total} in all"            # the last line's value is shown
```

Clean up a column, and colour negatives:

```python
sheet = doc.active_sheet
col = sheet.range("C2:C200")
col.values = [[v.strip().title() if isinstance(v, str) else v for v in row] for row in col.values]
for i, (v,) in enumerate(sheet.range("D2:D200").values):
    if isinstance(v, (int, float)) and v < 0:
        sheet.cell(i + 1, 3).format.font.color = "red"
```

Write many cells quickly (one round trip):

```python
with doc.batch():
    for i in range(1, 101):
        doc.active_sheet.cell(i - 1, 0).value = i * i
```

Formulas (in a trusted workbook):

```
=PY.EVAL("x * 2", A1)                                  a number doubled
=PY.EVAL("[[v * 2 for v in r] for r in x]", A1:B10)    a range doubled (spills)
=PY.EVAL("import statistics; statistics.pstdev(v for r in x for v in r)", A1:A100)
=PY.EVAL("sorted({v for r in x for v in r if v})", C2:C500)   unique values, sorted (spills down)
=PY.EVAL("x.split('@')[1]", B2)                        the domain of an e-mail address
```

Text in a formula is in double quotes, so write Python strings in single quotes (`'@'`), or double
the quotes.

### WordCraft

```python
for p in doc.paragraphs:
    if p.text.startswith("Note:"):
        p.style = "Quote"
        p.font.italic = True
doc.body.insert_paragraph("Generated on 2026-10-16\nby a Python script", "End")
len(doc.search("TODO"))
```

Replace the selected text, upper-cased:

```python
s = doc.selection
s.insert_text(s.text.upper())
```

### DeckCraft

```python
slide = doc.slides.add()
title = slide.shapes.add_text_box("Quarterly results", left=40, top=30, width=640, height=60)
title.font.set(size=32, bold=True, color="navy")
for i, label in enumerate(["North", "South", "East"]):
    box = slide.shapes.add_shape("rectangle", left=60 + i * 210, top=140, width=180, height=120)
    box.fill.color = ["#4E79A7", "#F28E2B", "#59A14F"][i]
    box.text = label
[s.title for s in doc.slides]
```

## The `doc` API

`doc` is the open document. Each read or write is one request to the app (`host/sync`, the same
object model the app's Office add-ins use); `with doc.batch():` sends the writes inside together.
When the app refuses something, the script gets a `BridgeError` (`e.code`: `ItemNotFound`,
`InvalidArgument`…; `e.message`: the app's words), an ordinary exception you can catch.

In every app:

| | |
|---|---|
| `doc.app` | `"gridcraft"`, `"wordcraft"` or `"deckcraft"` |
| `doc.document` | `{"path", "name", "dirty"}` of the file (`path` is None for a new document) |
| `doc.batch()` | `with doc.batch(): ...` sends the writes inside in one request (with the next read, or at the end of the block); if the block raises, its writes are dropped |
| `doc.command(id, params)` | Queues one of the app's commands (`"home.bold", {"range": "A1"}`) to run after the script, in the same undo step |
| `doc.sync(actions)` | Raw bridge actions (`[{"op": "load", "path": [...], "props": [...]}]`), see the app's `docs/officejs.md` |
| `doc.log(text)` | A line in the app's log |
| `x` | The selection: GridCraft its values (rows), WordCraft and DeckCraft its text |

### GridCraft

| | |
|---|---|
| `doc.sheets` | The worksheets, in order |
| `doc.sheet(name)`, `doc[name]` | A worksheet by name (any case); `KeyError` when there's none |
| `doc.active_sheet` | The active worksheet |
| `doc.add_sheet(name=None)` | A new worksheet at the end (the active one stays) |
| `doc.selection`, `doc.active_cell` | The selected range, the active cell (Ranges) |
| `doc.range("Sheet2!A1:C3")` | A range (the active sheet when no sheet is named) |
| `doc.name` | The workbook's name |
| `sheet.name` (settable), `sheet.position` | |
| `sheet.range("A1:C10")`, `sheet["A1"]`, `sheet.cell(row, col)` | Ranges (`cell` counts from 0) |
| `sheet.used_range`, `sheet.read(address=None)` | The used range; its values |
| `sheet.write("B2", rows)` | Writes rows (or a column from a flat list) from that cell on; the range written |
| `sheet.activate()`, `sheet.delete()` | |
| `range.values` | Rows of values (get and set). Numbers, text, `True`/`False`, `None` (empty); errors read as `CellError("#DIV/0!")`, a `str`. Setting: rows the range's size, one value (fills it), a flat list (a row or a column, by the range's shape), or anything at a single cell, which grows to fit |
| `range.value` | The top-left cell's value (get and set) |
| `range.formulas` | Formulas (`"=A1*2"`) or values (get and set; text starting with `=` is a formula) |
| `range.text` | The values as shown (formatted) |
| `range.number_format` | `"0.00%"` (get and set) |
| `range.format.fill.color` | `"#RRGGBB"` or a colour name (`"yellow"`); `"none"` or `.clear()` takes it off |
| `range.format.font` | `.bold`, `.italic`, `.underline` (`"None"`, `"Single"`, `"Double"`), `.strikethrough`, `.color`, `.size`, `.name` |
| `range.address`, `.row_count`, `.column_count`, `.shape` | `"Sheet1!A1:B2"`, sizes |
| `range.cell(r, c)`, `.offset(rows, cols)`, `.resize(rows, cols)`, `.row(i)`, `.column(i)`, `.used_range` | Other ranges |
| `range.clear("all" \| "contents" \| "formats")`, `range.select()` | |
| `range.records()` | Rows as dicts keyed by the first row (no pandas needed) |
| `range.columns()`, `range.numbers()` | The values column by column; the numbers in it |

Whole numbers read as `int` (a spreadsheet keeps every number as a double). Dates are numbers
(days since 1899-12-30): `datetime.date(1899, 12, 30) + datetime.timedelta(days=v)`.

### WordCraft

| | |
|---|---|
| `doc.paragraphs` | The body's paragraphs |
| `doc.body.text`, `doc.text` | The body's text (paragraphs joined by `\n`) |
| `doc.body.insert_paragraph(text, "End" \| "Start")` | Adds a paragraph (one per line of `text`); the last one added |
| `doc.body.insert_text(text, "End" \| "Start" \| "Replace")`, `doc.body.clear()` | |
| `doc.body.style`, `doc.body.font` | All paragraphs' style; the body's font |
| `doc.selection` | The selection as a Range: `.text`, `.insert_text(text, "Replace")`, `.font`, `.style`, `.paragraphs`, `.insert_paragraph(text, "After")`, `.delete()`, `.select()` |
| `doc.search(text, match_case=False, whole_word=False)` | Every match, as Ranges |
| `doc.properties` | `.title`, `.subject`, `.author`, `.keywords`, `.comments` (get and set) |
| `doc.name` | The file's name |
| `paragraph.text` | Its text; setting it replaces the text |
| `paragraph.style`, `.alignment` | `"Heading 1"`; `"Left"`, `"Centered"`, `"Right"`, `"Justified"` (get and set) |
| `paragraph.first_line_indent`, `.left_indent`, `.right_indent`, `.space_before`, `.space_after`, `.line_spacing` | Points (get and set); `.is_list_item` |
| `paragraph.font` | `.bold`, `.italic`, `.underline`, `.size`, `.name`, `.color`, `.highlight_color` (a highlight name such as `"Yellow"`, or None), `.strike_through`, `.subscript`, `.superscript`; `font.set(bold=True, size=14)`, `font.get()`. None where the text is mixed |
| `paragraph.insert_paragraph(text, "After" \| "Before")`, `.insert_text(text, "End")`, `.delete()`, `.select()`, `.range` | |

A paragraph object names its paragraph by position: after inserting or deleting paragraphs before
it, read `doc.paragraphs` again. Values shown by a paragraph object are read when the list is made;
`p.refresh()` reads them again.

### DeckCraft

| | |
|---|---|
| `doc.slides` | The slides: `len`, `[i]`, iteration, `.index(slide)`, `.add(layout=None, index=None)` (a layout's id or name; `index` from 0) |
| `doc.add_slide(...)` | The same as `doc.slides.add(...)` |
| `doc.current_slide` | The slide shown when the script started |
| `doc.selected_slides`, `doc.selected_shapes` | |
| `doc.title`, `doc.layouts` | The presentation's title; `[(id, name)]` of the layouts |
| `slide.id`, `slide.index`, `slide.layout`, `slide.title` | `title`: the text of its first shape with text |
| `slide.shapes` | `len`, `[i]`, iteration; `.add_text_box(text, left, top, width, height)`, `.add_shape(kind, left, top, width, height)` (`"rectangle"`, `"ellipse"`, `"triangle"`, `"roundRectangle"`, `"star5"`… the OOXML preset names), `.add_line(...)` |
| `slide.delete()`, `slide.select()` | |
| `shape.id`, `.name` (settable), `.type` | |
| `shape.left`, `.top`, `.width`, `.height` | Points (get and set); `shape.move(left, top)`, `shape.resize(width, height)` |
| `shape.text` | Its text (get and set); None for a picture or a line |
| `shape.font` | `.bold`, `.italic`, `.underline`, `.size`, `.name`, `.color`; `font.set(...)` |
| `shape.fill` | `.color` (`"#RRGGBB"` or a colour name), `.transparency` (0–1), `.type`, `.clear()` |
| `shape.line` | `.color`, `.weight`, `.visible`, `.dash_style` |
| `shape.delete()` | |

Slides and shapes keep their ids while the presentation is open, so objects in variables stay
good when others come and go.

## Commands

On the **Python** tab of each app (and Run Script in the right-click menu):

| Command | Does |
|---|---|
| **Run Script…** (`runScript`) | A form for Python code (or a `.py` file). `doc` is the document, `x` the selection. What it prints and the last line's value show in a dialog afterwards (long output is cut); an exception shows its traceback. In GridCraft the value can instead be written below the selection or on a new sheet (**Python Output**). Trusted documents only |
| **Run File…** (`runFile`) | Runs a `.py` file (a relative path is relative to the document's folder). Trusted documents only |
| **Console…** (`console`) | Runs a line (or a block) and shows the result; **Again…** asks for the next one. Functions, values and imports you define stay until the app stops the add-in, and scripts and `PY.EVAL` see them. Trusted documents only |
| **Trust This Workbook/Document/Presentation** (`trust`) | Turns Python on for the document, after a dialog that spells out the risk |
| **Stop Trusting** (`untrust`) | Turns it off again |
| **Trusted …s…** (`trusted`) | The list of trusted files; select some and **Remove** them |
| **About** (`about`) | Versions, the interpreter, and whether Python is on here |

GridCraft keeps the 0.1 commands (**Title Case**, **Trim Spaces**, **Statistics**,
**Fibonacci**), WordCraft **Title Case**, **Trim Spaces** and **Text Statistics**, DeckCraft
**Text Statistics** (Review tab).

The form's code box is one line in the apps' current dialogs: separate statements with `;`
(`import math; math.pi * 2`), or put a longer script in a file. The add-in asks for a multi-line
box (`"multiline": true`), so apps that draw one get it.

**Timing:** a script or a console line may run 50 seconds, a `PY.EVAL` 25; then it is stopped
with a message. Code stuck in one long call into C is stopped when that call returns; if it never
does, the app gives up on the add-in after a minute and starts it again.

**Errors.** Run from a dialog, a script that raises shows the traceback, and what it changed before
the error stays (Undo takes the whole run back). Run by an agent or the CLI with `code` in the
params, a failing script fails the command and the app takes back everything it changed.

Agents and scripts run the commands with `plugins.run`; with `code` (or `file`, `path`) in the
params no dialog opens:

```sh
gridcraft-cli --plugin ~/plugins/io.github.sirmmo.python run --in data.xlsx \
  --cmd 'selection.set={"range":"A1:C20"}' \
  --cmd 'plugins.run={"plugin":"io.github.sirmmo.python","command":"runScript","params":{"code":"[[sum(c) for c in zip(*x[1:])]]","output":"below"}}' \
  --out data.xlsx
```

| Command | Params | Result |
|---|---|---|
| `runScript` | `{code}` or `{file}`; `output`: `"show"` (default), `"below"`, `"sheet"` (GridCraft); `timeout`: seconds (at most 50) | `{output, value, seconds, truncated?, written?}` |
| `runFile` | `{path}` | the same |
| `console` | `{code}` | the same |
| `trust`, `untrust`, `trusted`, `about` | `{}` | |
| `trustConfirm` | `{path, name}` of the active document: trusts it without asking (the OK of Trust) | |
| `untrustPaths` | `{ids: [path, …]}` | |

## Functions (GridCraft)

| Function | Returns |
|---|---|
| `PY.EVAL(code, [x], [y], [z], …)` | The value of Python `code` (an expression, or statements whose last line is one), with the arguments as `x`, `y`, `z` and all of them as `args`. A range is a list of rows; one cell is its value. **Trusted workbooks only** |
| `PY.VERSION()` | The add-in and Python versions |
| `PY.TWICE(value)` | A number doubled, or each number of a range (spills) |
| `PY.MEDIAN(number1, …)` | The median of the numbers (text and empty cells are ignored) |
| `PY.STATS(number1, …)` | A two-column table that spills: count, sum, mean, median, min, max, stdev |
| `PY.REGEX(text, pattern, [group])` | The first match of a Python regular expression (or a group of it); `#N/A` when nothing matches |
| `PY.SPLIT(text, [separator])` | The parts of the text, spilled across a row (default: split on whitespace) |

What `PY.EVAL` returns becomes cells: a number, text or logical is one cell; `None` an empty
text; a list a column, a list of lists rows (ragged rows padded), a dict two columns (keys,
values), a set a sorted column; `Decimal` and `Fraction` numbers; dates ISO text; NaN and infinities
`#NUM!`; anything else its `str()`. More than a million cells is `#NUM!`. A Python error shows
`#VALUE!` and its message goes to the app's log (`Python error: NameError: name 'z' is not
defined`), as does what the code prints. Results are kept for the same arguments (the function
isn't volatile); errors aren't, so **Formulas › Calculate Now** runs them again after you trust
the workbook or define a function in the console.

## Trust

Python code can do anything you can: read, change or delete your files, send them over the
internet, start programs. A workbook someone sends you can carry `=PY.EVAL("…")` formulas that run
on every recalculation. So `PY.EVAL`, Run Script, Run File and the Console work only in **trusted
documents**; the fixed functions (`PY.STATS`, `PY.REGEX`…) run no code from the workbook and always
work.

- **Trust This …** asks first, then adds the document's file (its absolute path) to
  `trusted.json` in the add-in's data folder: `~/.local/share/craft-python` on Linux and BSD
  (`$XDG_DATA_HOME/craft-python` when set), `~/Library/Application Support/craft-python` on macOS,
  `%APPDATA%\craft-python` on Windows (`CRAFT_PYTHON_DATA` overrides it). The list is shared by
  GridCraft, WordCraft and DeckCraft and survives updates of the add-in. A file moved or renamed
  must be trusted again.
- **A new document** that was never saved (Book1, Document1, Presentation1) is trusted while
  unsaved: you're the one writing in it. Once saved, its file is a new document to trust (the
  status bar says so). Other unsaved documents (an imported file, a sample) can be trusted until
  the add-in stops.
- Running a script in an untrusted document offers to trust it first; then the form comes back.
  An untrusted workbook's `PY.EVAL` cells show `#VALUE!`, and the log says `Python is turned off
  for this workbook: Python > Trust This Workbook`.
- `CRAFT_PYTHON_TRUST=all` in the environment trusts every document: for scripts and CI that
  evaluate their own files, never for opening other people's.

**What formulas can't see.** GridCraft doesn't tell an add-in which workbook a formula is in, so
`PY.EVAL` follows the workbook the app last told the add-in about: the document of the last Python
command, and the documentOpened, documentSaved and documentClosed events. Opening a workbook
calculates it before the app says it opened, so **don't open workbooks you don't trust while a
trusted one is open**, or start a fresh GridCraft for them. With several windows, running any
Python command (About) in a workbook tells the add-in which one you're in. Results already
calculated stay in their cells after Stop Trusting until their inputs change.

Trust is about documents, not about the add-in: like any add-in, this one runs with your
permissions. Install add-ins only from people you trust.

## How it works

The app starts `python3 main.py` the first time one of the add-in's functions or commands is used
(or a document event arrives) and keeps it running for the session, one process per app. They talk
JSON-RPC, one message per line, on standard input and output (protocol 1.2:
[docs/plugins.md](https://github.com/storytold/gridcraft/blob/main/docs/plugins.md)). While a
command runs, `doc` sends `host/sync` requests (protocol 1.1) with the Office bridge's actions
(GridCraft's, WordCraft's and DeckCraft's `docs/officejs.md`); the app runs them at once, inside the
command's undo step, and answers.

The add-in keeps private copies of its standard input and output and points file descriptors 0
and 1 elsewhere, so a program a script starts can't read the app's messages or break the add-in's
answers; a script's `print()` and `sys.stderr` are captured (up to 200,000 characters), and
`input()` gets end of file. A bad message, a bug, an exception, `sys.exit()` or a time-out never
stops the process.

Tests: `python3 -m unittest discover -s tests` (the standard library only: the add-in driven over
standard input and output as each app drives it, against small in-memory models of the three
apps' bridges, with the trust flow, time limits and malformed input), and
`GRIDCRAFT_CLI=/path/to/gridcraft-cli sh tests/e2e.sh` (through GridCraft itself).

## Licence

MIT
