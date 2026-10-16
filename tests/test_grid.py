"""GridCraft: Run Script, the console and `doc` on a fake workbook; the runner's limits."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fakehost import Grid  # noqa: E402
from harness import Plugin, error, result  # noqa: E402


class GridScripts(unittest.TestCase):
    def setUp(self):
        self.g = Grid()
        self.g.sheets.append({"name": "Data", "cells": {}, "style": {}})
        self.g.put(1, "A1", [["name", "qty"], ["apple", 3], ["pear", 5]])
        self.g.put(0, "B2", [[1, 2], [3, 4]])
        self.g.sel = (1, 1, 2, 2)  # B2:C3
        self.p = Plugin("gridcraft", self.g)

    def tearDown(self):
        self.p.close()

    def script(self, code, **params):
        params["code"] = code
        return self.p.run("runScript", params)

    def test_read_and_write(self):
        code = "\n".join([
            "names = [s.name for s in doc.sheets]",
            "d = doc.sheet('data')",
            "rows = d.range('A1:B3').values",
            "d.range('C1').values = [['total'], [rows[1][1] * 2], [rows[2][1] * 2]]",
            "d['D1:D2'].formulas = [['=C2+1'], ['=C3+1']]",
            "d.range('A1:B1').format.fill.color = 'yellow'",
            "d.range('A1').format.font.bold = True",
            "print(names, x)",
            "(d.range('A1').format.font.bold, d.range('A1').format.fill.color, d.read('D1:D2'), d.range('D1:D2').formulas)",
        ])
        r = result(self.script(code))
        self.assertEqual(r["result"]["output"], "['Sheet1', 'Data'] [[1, 2], [3, 4]]\n")
        self.assertEqual(r["result"]["value"], [True, "yellow", [["(formula)"], ["(formula)"]], [["=C2+1"], ["=C3+1"]]])
        self.assertEqual(self.g.get(1, "C1:C3"), [["total"], [6], [10]])
        self.assertEqual(self.g.sheets[1]["style"][(0, 1)]["fill"], "yellow")
        self.assertNotIn("dialog", r)  # programmatic: no dialog
        self.assertEqual(r["message"][:13], "Python: ran i")

    def test_selection_and_helpers(self):
        code = "\n".join([
            "s = doc.selection",
            "t = doc.sheet('Data').range('A1:B3')",
            "(s.address, s.shape, s.values, t.records(), t.columns(), t.numbers(), doc.active_cell.address,",
            " doc.active_sheet.name, doc.sheet('Data').used_range.address, s.offset(1, 1).address, s.resize(1, 3).address,",
            " s.cell(1, 1).address, s.row(1).address, s.column(0).address, s.value)",
        ])
        v = result(self.script(code))["result"]["value"]
        self.assertEqual(v[0], "Sheet1!B2:C3")
        self.assertEqual(v[1], [2, 2])
        self.assertEqual(v[2], [[1, 2], [3, 4]])
        self.assertEqual(v[3], [{"name": "apple", "qty": 3}, {"name": "pear", "qty": 5}])
        self.assertEqual(v[4], [["name", "apple", "pear"], ["qty", 3, 5]])
        self.assertEqual(v[5], [3, 5])
        self.assertEqual(v[6:], ["Sheet1!B2", "Sheet1", "Data!A1:B3", "Sheet1!C3:D4", "Sheet1!B2:D2", "Sheet1!C3",
                                 "Sheet1!B3:C3", "Sheet1!B2:B3", 1])

    def test_write_grows_and_shapes(self):
        code = "\n".join([
            "sh = doc.add_sheet('Out')",
            "r = sh.write('B2', [[1, 2, 3], [4, 5]])",
            "sh.range('E1').values = ['a', 'b', 'c']",          # a flat list goes down a column
            "sh.range('A10:C10').values = ['x', 'y', 'z']",     # into a row
            "sh.range('A12:B13').values = 7",                   # one value fills
            "sh.range('A20').value = None",
            "r.address",
        ])
        r = result(self.script(code))
        self.assertEqual(r["result"]["value"], "Out!B2:D3")
        out = self.g.sheet_index("Out")
        self.assertEqual(self.g.get(out, "B2:D3"), [[1, 2, 3], [4, 5, None]])
        self.assertEqual(self.g.get(out, "E1:E3"), [["a"], ["b"], ["c"]])
        self.assertEqual(self.g.get(out, "A10:C10"), [["x", "y", "z"]])
        self.assertEqual(self.g.get(out, "A12:B13"), [[7, 7], [7, 7]])
        msg = error(self.script("doc.sheet('Out').range('A1:B2').values = [[1, 2, 3]]"))
        self.assertIn("don't fit", msg)

    def test_batch_is_one_sync(self):
        self.g.syncs = 0
        code = "\n".join([
            "d = doc.sheet('Data')",
            "with doc.batch():",
            "    for i in range(50):",
            "        d.cell(i, 5).value = i",
            "        d.cell(i, 5).format.font.italic = True",
            "d.range('F50').value",
        ])
        r = result(self.script(code))
        self.assertEqual(r["result"]["value"], 49)
        # sheet('Data') check, the batch flushed with the read, nothing else
        self.assertLessEqual(self.g.syncs, 3)

    def test_error_programmatic_rolls_back(self):
        code = "doc.sheet('Data').range('A1').value = 'changed'\ndoc.sheet('Nope')"
        msg = error(self.script(code))
        self.assertIn("KeyError: 'Nope'", msg)
        self.assertIn("line 2", msg)
        self.assertEqual(self.g.get(1, "A1"), [["name"]])  # the app took the step back

    def test_error_interactive_shows_traceback(self):
        code = "doc.sheet('Data').range('A1').value = 'changed'\ndef f():\n    return 1 / 0\nf()"
        r = result(self.p.run("runScript", {"fields": {"code": code, "file": "", "output": "show"}}))
        d = r["dialog"]
        self.assertEqual(d["kind"], "message")
        self.assertIn("ZeroDivisionError: division by zero", d["prompt"])
        self.assertIn("in f", d["prompt"])
        self.assertIn("Undo", d["prompt"])
        self.assertNotIn("craftpython", d["prompt"])
        self.assertEqual(d["buttons"][0]["command"], "runScript")
        self.assertEqual(self.g.get(1, "A1"), [["changed"]])  # kept: one undo step

    def test_bridge_error_is_an_exception(self):
        code = "\n".join([
            "try:",
            "    doc.sheet('Data').range('A1').format.fill.color = 5",
            "except Exception as e:",
            "    out = (type(e).__name__, e.code, e.message)",
            "out",
        ])
        v = result(self.script(code))["result"]["value"]
        self.assertEqual(v, ["BridgeError", "InvalidArgument", "a colour is text"])

    def test_output_below_and_sheet(self):
        r = result(self.script("[[sum(r) for r in x]]", output="below"))
        self.assertEqual(r["result"]["written"], "Sheet1!B5:C5")
        self.assertEqual(self.g.get(0, "B5:C5"), [[3, 7]])
        r = result(self.script("{'a': 1, 'b': 2}", output="sheet"))
        self.assertEqual(r["result"]["written"], "'Python Output'!A1:B2")
        r = result(self.script("42", output="sheet"))
        self.assertEqual(r["result"]["written"], "'Python Output 2'!A1")

    def test_form_and_dialog_flow(self):
        r = result(self.p.run("runScript"))
        d = r["dialog"]
        self.assertEqual((d["kind"], d["command"]), ("form", "runScript"))
        self.assertEqual([f["id"] for f in d["fields"]], ["code", "file", "output"])
        self.assertTrue(d["fields"][0]["multiline"])
        self.assertIn("one line", d["prompt"])  # this host doesn't say it has multi-line fields
        r = result(self.p.run("runScript", {"fields": {"code": "print('hi')\n6 * 7", "file": "", "output": "show"}}))
        self.assertEqual(r["dialog"]["prompt"], "hi\n42")
        # Edit Script… brings the code back
        r = result(self.p.run("runScript", {"edit": True}))
        self.assertEqual(r["dialog"]["fields"][0]["value"], "print('hi')\n6 * 7")
        # nothing printed and no value: no dialog
        r = result(self.p.run("runScript", {"fields": {"code": "a = 1", "file": "", "output": "show"}}))
        self.assertNotIn("dialog", r)
        # empty form: asked again
        r = result(self.p.run("runScript", {"fields": {"code": " ", "file": ""}}))
        self.assertIn("Type some Python", r["dialog"]["prompt"])

    def test_multiline_feature(self):
        p = Plugin("gridcraft", Grid(), features=["dialogs", "document", "dialogs.multiline"])
        try:
            self.assertNotIn("one line", result(p.run("runScript"))["dialog"]["prompt"])
        finally:
            p.close()

    def test_run_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.py")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("# -*- coding: utf-8 -*-\nimport math\nprint('π ≈', round(math.pi, 2))\ndef g():\n    raise ValueError('bad')\n")
            r = result(self.p.run("runFile", {"path": path}))
            self.assertEqual(r["result"]["output"], "π ≈ 3.14\n")
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("g()\n")
            r = result(self.p.run("runFile", {"fields": {"path": path}}))
            self.assertIn('s.py", line 5, in g', r["dialog"]["prompt"])
            self.assertIn("raise ValueError('bad')", r["dialog"]["prompt"])
            self.assertIn("Can't read", error(self.p.run("runFile", {"path": os.path.join(d, "none.py")})))
            msg = error(self.p.run("runScript", {"file": path, "code": "ignored"}))
            self.assertEqual(msg.splitlines()[-1], "ValueError: bad")
        r = result(self.p.run("runFile"))
        self.assertEqual(r["dialog"]["fields"][0]["value"], path)

    def test_console_keeps_definitions(self):
        r = result(self.p.run("console", {"code": "def triple(v):\n    return v * 3"}))
        self.assertEqual(r["result"]["output"], "")
        r = result(self.p.run("console", {"fields": {"code": "triple(14)"}}))
        self.assertEqual(r["dialog"]["prompt"], ">>> triple(14)\n42")
        self.assertEqual(r["dialog"]["buttons"][0]["label"], "Again…")
        # the formula sees it too (Book1 is new, so trusted)
        self.assertEqual(result(self.p.call("PY.EVAL", "triple(x)", 5))["value"], 15)
        r = result(self.p.run("console"))
        self.assertIn(">>> triple(14)", r["dialog"]["prompt"])
        self.assertEqual(r["dialog"]["fields"][0]["id"], "code")

    def test_doc_commands(self):
        r = result(self.script("doc.command('home.bold', {'range': 'B2'})"))
        self.assertEqual(r["commands"], [{"command": "home.bold", "params": {"range": "B2"}}])
        self.assertTrue(self.g.sheets[0]["style"][(1, 1)]["font.bold"])
        self.assertIn("plugins.*", error(self.script("doc.command('plugins.run', {})")))

    def test_doc_after_the_script(self):
        result(self.script("keep = doc"))
        msg = error(self.p.run("console", {"code": "keep.sheets"}))
        self.assertIn("only while the script that got it runs", msg)


class Limits(unittest.TestCase):
    def setUp(self):
        self.p = Plugin("gridcraft", Grid(), env={"CRAFT_PYTHON_EVAL_SECONDS": "1"})

    def tearDown(self):
        self.p.close()

    def run_code(self, code, **params):
        params["code"] = code
        return self.p.run("runScript", params)

    def test_timeouts(self):
        for code in ["while True: pass", "import time\ntime.sleep(30)",
                     "while True:\n    try:\n        sum(range(10**6))\n    except Exception:\n        pass"]:
            msg = error(self.run_code(code, timeout=1))
            self.assertIn("longer than 1 seconds", msg, code)
        self.assertEqual(result(self.run_code("1 + 1"))["result"]["value"], 2)
        self.assertEqual(error(self.p.call("PY.EVAL", "while True: pass")), "Python error: Stopped after 1 seconds")
        self.assertEqual(result(self.p.call("PY.EVAL", "2 ** 10"))["value"], 1024)

    def test_output_bounded(self):
        r = result(self.run_code("print('x' * 300000)\nfor i in range(10): print(i)"))
        self.assertTrue(r["result"]["truncated"])
        self.assertEqual(len(r["result"]["output"]), 200000)
        r = result(self.p.run("runScript", {"fields": {"code": "print('y' * 10000)"}}))
        self.assertLess(len(r["dialog"]["prompt"]), 4000)

    def test_hostile_scripts(self):
        r = result(self.run_code("import os, sys\nos.write(1, b'junk on stdout\\n')\nprint('ok', file=sys.stderr)\nsys.stdout.write('w')"))
        self.assertEqual(r["result"]["output"], "ok\nw")
        self.assertIn("EOFError", error(self.run_code("input('?')")))
        self.assertEqual(result(self.run_code("import sys\nsys.exit()"))["result"]["output"], "")
        self.assertIn("SystemExit: 3", error(self.run_code("raise SystemExit(3)")))
        self.assertIn("RecursionError", error(self.run_code("def f(): return f()\nf()")))
        self.assertIn("SyntaxError", error(self.run_code("def (:")))
        self.assertIn("KeyboardInterrupt", error(self.run_code("raise KeyboardInterrupt")))
        r = result(self.run_code("import subprocess, sys\nsubprocess.run([sys.executable, '-c', 'import sys; print(sys.stdin.read()); print(\"child\")'])"))
        self.assertEqual(r["result"]["error"] if "error" in r["result"] else None, None)
        r = result(self.run_code("class Bad:\n    def __repr__(self): raise ValueError('no')\nBad()"))
        self.assertTrue(r["result"]["value"].startswith("<Bad instance"))
        r = result(self.run_code("float('nan')"))
        self.assertEqual(r["result"]["value"], "nan")
        # still answering
        self.assertEqual(result(self.p.call("PY.TWICE", 2))["value"], 4)


if __name__ == "__main__":
    unittest.main()
