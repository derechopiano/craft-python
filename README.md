# Python Tools for GridCraft and WordCraft

An add-in for [GridCraft](https://github.com/storytold/gridcraft) and
[WordCraft](https://github.com/storytold/wordcraft) written in Python, using only the standard
library. One `plugin.json` serves both apps: `apps` names them, `ui` is GridCraft's ribbon and
menus, `uiFor.wordcraft` is WordCraft's, and `main.py` answers each app with its own commands
(it reads `host.id` from `initialize`). It's a test of GridCraft's plugin system: custom functions, commands, a ribbon
tab and right-click menu items, all from one `plugin.json` and one `main.py`.

## Install

In GridCraft, open **File › Options › Add-ins**, paste

```
https://github.com/sirmmo/gridcraft-python-plugin
```

in **Install from GitHub** and confirm. It needs `python3` (3.8 or later) on your `PATH`; on macOS
the first run may ask to install the Command Line Tools.

From the command line:

```sh
gridcraft-cli run --cmd 'plugins.install={"url":"sirmmo/gridcraft-python-plugin","dir":"'"$HOME"'/plugins"}'
gridcraft-cli --plugin ~/plugins/io.github.sirmmo.python eval '=PY.STATS(1,2,3,4)'
```

## Functions

| Function | Returns |
|---|---|
| `PY.VERSION()` | The plugin and Python versions |
| `PY.TWICE(value)` | A number doubled, or each number of a range (spills) |
| `PY.MEDIAN(number1, …)` | The median of the numbers (text and empty cells are ignored) |
| `PY.STATS(number1, …)` | A two-column table that spills: count, sum, mean, median, min, max, stdev |
| `PY.REGEX(text, pattern, [group])` | The first match of a Python regular expression (or a group of it); `#N/A` when nothing matches |
| `PY.SPLIT(text, [separator])` | The parts of the text, spilled across a row (default: split on whitespace) |

## In WordCraft

On the **Python** tab, in **Review › Python** and in the right-click menu: **Title Case** and
**Trim Spaces** rewrite the selected text (one undoable step), and **Text Statistics** shows the
words, sentences and average word length of the selection (or the paragraph) in the status bar.
Install it from **File › Options › Add-ins** with the same address.

## Commands (GridCraft)

On the **Python** tab, in **Data › Python** and in the right-click menus:

| Command | Does |
|---|---|
| `titleCase` | Title-cases the text in the selection (also on the column header menu) |
| `trim` | Removes extra spaces from the text in the selection |
| `statsTable` | Writes a statistics table to the right of the selection |
| `fillFibonacci` `{count}` | Fills Fibonacci numbers down from the active cell (10 by default) |

Each command is one undoable step. Agents run them with
`plugins.run {"plugin": "io.github.sirmmo.python", "command": "statsTable"}`.

## How it works

GridCraft starts `python3 main.py` and sends it JSON-RPC messages, one per line, on standard
input; the plugin answers on standard output. Functions return values; commands return the
GridCraft commands to run (here `range.setValues` and `home.bold`). See
[docs/plugins.md](https://github.com/storytold/gridcraft/blob/main/docs/plugins.md).

## Licence

MIT
