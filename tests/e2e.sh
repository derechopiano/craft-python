#!/bin/sh
# End to end through GridCraft itself: loads the add-in in gridcraft-cli and checks the functions,
# the trust flow, scripts that edit the workbook through `doc` (host/sync), one undo step, the
# console and errors.
#
#     GRIDCRAFT_CLI=/path/to/gridcraft-cli sh tests/e2e.sh
#
# Build the CLI from a GridCraft checkout (0.7 or later) with `cargo build -p gridcraft-cli`.
set -u
CLI="${GRIDCRAFT_CLI:-gridcraft-cli}"
DIR=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
export CRAFT_PYTHON_DATA="$WORK/data"
unset CRAFT_PYTHON_TRUST
cd "$WORK" || exit 1
fails=0
n=0
expect() { # name, got, want
  n=$((n + 1))
  if [ "$2" != "$3" ]; then
    echo "FAIL: $1: got [$2], want [$3]"
    fails=$((fails + 1))
  fi
}
contains() { # name, got, part
  n=$((n + 1))
  case "$2" in
    *"$3"*) ;;
    *) echo "FAIL: $1: [$2] doesn't hold [$3]"; fails=$((fails + 1)) ;;
  esac
}
last() { tail -n 1 | sed 's/[[:space:]]*$//'; }
run() { "$CLI" --plugin "$DIR" "$@" 2>&1; }
P='plugins.run={"plugin":"io.github.sirmmo.python","command":'

# Functions.
expect "PY.STATS needs no trust" "$(run eval '=INDEX(PY.STATS(1,2,3,4),2,2)' | last)" "10"
expect "PY.VERSION" "$(run eval '=PY.VERSION()' | last | cut -c1-20)" "Python add-in 0.3.0 "
expect "PY.REGEX" "$(run eval '=PY.REGEX("order 1234","\d+")' | last)" "1234"
expect "PY.EVAL is off in an unknown workbook" "$(run eval '=PY.EVAL("1+1")' | last)" "#VALUE!"
expect "CRAFT_PYTHON_TRUST=all" "$(env CRAFT_PYTHON_TRUST=all "$CLI" --plugin "$DIR" eval '=PY.EVAL("sum(v for r in x for v in r)",{1,2;3,4})' 2>&1 | last)" "10"

# A new workbook (Book1) is trusted once a command has told the add-in about it.
out=$(run run --cmd 'cell.set={"cell":"A1","input":"=PY.EVAL(\"6*7\")"}' \
  --cmd "${P}\"about\"}" --cmd 'formulas.calculateNow={}' --print A1)
expect "Book1: PY.EVAL on after a command" "$(printf '%s\n' "$out" | last)" "42"

# A script edits the workbook through doc (host/sync): sums, a fill, a new sheet.
code="s = doc.selection\\nd = doc.active_sheet\\nd.range('D1').values = [[sum(r) for r in x]]\\nd.range('D1:E1').format.fill.color = 'yellow'\\nd.range('D1').format.font.bold = True\\no = doc.add_sheet('Out')\\no.write('A1', [['n', 'square']] + [[i, i * i] for i in range(1, 4)])\\nprint(s.address)\\n(d.range('D1').format.fill.color, d.range('D1').format.font.bold, [sh.name for sh in doc.sheets])"
out=$(run run --cmd 'range.setValues={"range":"A1","values":[[1,2],[3,4]]}' --cmd 'selection.set={"range":"A1:B2"}' \
  --cmd "${P}\"runScript\",\"params\":{\"code\":\"$code\"}}" --print D1:E1 --out "$WORK/script.xlsx")
expect "runScript writes through doc" "$(printf '%s\n' "$out" | last | tr -s ' ')" "3 7"
contains "runScript's value" "$out" '"value":["#FFFF00",true,["Sheet1","Out"]]'
contains "runScript's output" "$out" '"output":"Sheet1!A1:B2\n"'
expect "the new sheet" "$("$CLI" cat "$WORK/script.xlsx" --sheet Out --range A4:B4 --csv 2>&1 | last)" "3,9"

# All of it is one undo step.
out=$(run run --cmd 'range.setValues={"range":"A1","values":[[1,2],[3,4]]}' --cmd 'selection.set={"range":"A1:B2"}' \
  --cmd "${P}\"runScript\",\"params\":{\"code\":\"doc.active_sheet.range('D1').values = [[1, 2]]\\ndoc.active_sheet.range('D2').value = 'x'\"}}" \
  --cmd 'edit.undo={}' --print A1:E2)
expect "one undo step" "$(printf '%s\n' "$out" | tail -n 2 | tr -s ' ' | tr '\n' '|')" "1 2|3 4|"

# The value written below the selection.
out=$(run run --cmd 'range.setValues={"range":"A1","values":[[1,2],[3,4]]}' --cmd 'selection.set={"range":"A1:B2"}' \
  --cmd "${P}\"runScript\",\"params\":{\"code\":\"[[sum(c) for c in zip(*x)]]\",\"output\":\"below\"}}" --print A4:B4)
expect "output below" "$(printf '%s\n' "$out" | last | tr -s ' ')" "4 6"

# An error: a programmatic run fails with the traceback (and the app takes back its changes).
out=$(run run --cmd 'range.setValues={"range":"A1","values":[[1]]}' \
  --cmd "${P}\"runScript\",\"params\":{\"code\":\"doc.active_sheet.range('A1').value = 99\\n1 / 0\"}}" --print A1)
contains "an error fails the command" "$out" "ZeroDivisionError: division by zero"
contains "with the script's line" "$out" 'File "<script>", line 2, in <module>'
# From the dialog it shows the traceback; what ran before the error stays, as one undo step.
out=$(run run --cmd 'range.setValues={"range":"A1","values":[[1]]}' \
  --cmd "${P}\"runScript\",\"params\":{\"fields\":{\"code\":\"doc.active_sheet.range('A1').value = 99\\n1 / 0\",\"file\":\"\"}}}" --print A1)
contains "the dialog shows the traceback" "$out" '"kind":"message"'
expect "changes before the error stay" "$(printf '%s\n' "$out" | last)" "99"
out=$(run run --cmd 'range.setValues={"range":"A1","values":[[1]]}' \
  --cmd "${P}\"runScript\",\"params\":{\"fields\":{\"code\":\"doc.active_sheet.range('A1').value = 99\\n1 / 0\",\"file\":\"\"}}}" \
  --cmd 'edit.undo={}' --print A1)
expect "and Undo takes them back" "$(printf '%s\n' "$out" | last)" "1"

# The console keeps definitions for formulas.
out=$(run run --cmd "${P}\"console\",\"params\":{\"code\":\"def triple(v):\\n    return v * 3\"}}" \
  --cmd 'cell.set={"cell":"B1","input":"=PY.EVAL(\"triple(x)\",14)"}' --print B1)
expect "console definitions reach PY.EVAL" "$(printf '%s\n' "$out" | last)" "42"

# A saved workbook: off until trusted, on afterwards, also when opened again.
run run --cmd 'range.setValues={"range":"A1","values":[[1,10],[2,20]]}' \
  --cmd 'cell.set={"cell":"C1","input":"=PY.EVAL(\"sum(v for r in x for v in r)\",A1:B2)"}' --out "$WORK/book.xlsx" >/dev/null
expect "saved workbook: off" "$(run run --in "$WORK/book.xlsx" --cmd 'formulas.calculateNow={}' --print C1 | last)" "#VALUE!"
contains "scripts: off" "$(run run --in "$WORK/book.xlsx" --cmd "${P}\"runScript\",\"params\":{\"code\":\"1\"}}")" "Python is turned off for this workbook"
contains "trust asks first" "$(run run --in "$WORK/book.xlsx" --cmd "${P}\"trust\"}" | last)" '"command":"trustConfirm"'
expect "trusted" "$(run run --in "$WORK/book.xlsx" --cmd "${P}\"trustConfirm\",\"params\":{\"path\":\"$WORK/book.xlsx\",\"name\":\"book.xlsx\"}}" --cmd 'formulas.calculateNow={}' --print C1 | last)" "33"
expect "trusted, opened again" "$(run run --cmd "file.open={\"path\":\"$WORK/book.xlsx\"}" --cmd 'formulas.calculateNow={}' --print C1 | last)" "33"

echo "$n end-to-end checks, $fails failed"
[ "$fails" -eq 0 ]
