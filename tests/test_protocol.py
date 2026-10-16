"""The protocol: initialize per app, malformed input, the 0.1/0.2 functions and commands."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fakehost import Grid, Word  # noqa: E402
from harness import Plugin, error, result  # noqa: E402


class Initialize(unittest.TestCase):
    def test_each_app(self):
        for app in ("gridcraft", "wordcraft", "deckcraft"):
            p = Plugin(app)
            try:
                r = p.request("about", {})
                self.assertIn("unknown", error(r))
                info = result(p.run("about", context={"document": {"path": None, "name": None}}))
                self.assertEqual(info["result"]["app"], "{} 0.7.0".format(app.title()))
                self.assertEqual(info["dialog"]["kind"], "message")
            finally:
                p.close()

    def test_without_features(self):
        """An app without protocol 1.2 gets no dialogs: commands that ask say how to pass params."""
        p = Plugin("gridcraft", Grid(), features=[])
        try:
            msg = error(p.run("runScript"))
            self.assertIn("can't show", msg)
            self.assertNotIn("dialog", result(p.run("about")))
        finally:
            p.close()


class Malformed(unittest.TestCase):
    def setUp(self):
        self.p = Plugin("gridcraft", Grid())

    def tearDown(self):
        self.p.close()

    def alive(self):
        self.assertEqual(result(self.p.call("PY.TWICE", 21))["value"], 42)

    def test_garbage_lines(self):
        for raw in [b"garbage\n", b"\n", b"   \n", b"[1,2,3]\n", b"\"text\"\n", b"\xff\xfe\x00\n",
                    b"{\"jsonrpc\":\"2.0\"}\n", b"{\"id\": 99}\n", b"{\"id\": 98, \"result\": {}}\n",
                    b"[" * 100000 + b"\n", b"{\"method\": \"nothing\"}\n", b"{\"method\": 5, \"id\": 97}\n"]:
            self.p.send_raw(raw)
        self.alive()

    def test_bad_requests(self):
        self.assertIn("unknown method", error(self.p.request("nothing/here")))
        self.assertIn("unknown command", error(self.p.request("commands/run", {"command": "nope"})))
        self.assertIn("unknown command", error(self.p.request("commands/run", {"command": ["x"]})))
        self.assertIn("unknown function", error(self.p.request("functions/call", {"name": "PY.NOPE", "args": []})))
        # params of the wrong type
        r = self.p.request("commands/run", {"command": "fillFibonacci", "params": "x", "context": 5})
        self.assertIn("result", r)
        self.p.send({"jsonrpc": "2.0", "id": 50, "method": "commands/run", "params": [1, 2]})
        self.assertIn("error", self.p.wait(50))
        self.assertEqual(result(self.p.request("functions/call", {"name": "PY.TWICE", "args": "notalist"}))["value"],
                         {"error": "#VALUE!"})
        self.alive()

    def test_wrong_arguments(self):
        self.assertEqual(result(self.p.call("PY.REGEX", "abc", "("))["value"], {"error": "#VALUE!"})
        self.assertEqual(result(self.p.call("PY.REGEX", "abc", "b", 5))["value"], {"error": "#REF!"})
        self.assertEqual(result(self.p.call("PY.MEDIAN", "a", None))["value"], {"error": "#NUM!"})
        self.assertEqual(result(self.p.call("PY.MEDIAN", [[1, {"error": "#N/A"}]]))["value"], {"error": "#N/A"})
        self.assertEqual(result(self.p.request("functions/call", {"name": "PY.TWICE", "args": []}))["value"],
                         {"error": "#VALUE!"})
        self.alive()

    def test_big_message(self):
        rows = [[i, i + 0.5] for i in range(200000)]
        r = result(self.p.call("PY.STATS", rows))
        self.assertEqual(r["value"][0], ["count", 400000])

    def test_shutdown(self):
        self.p.send({"jsonrpc": "2.0", "method": "shutdown"})
        self.p.proc.wait(timeout=10)
        self.assertEqual(self.p.proc.returncode, 0)

    def test_eof(self):
        self.p.proc.stdin.close()
        self.p.proc.wait(timeout=10)
        self.assertEqual(self.p.proc.returncode, 0)


class Functions(unittest.TestCase):
    """The fixed helpers need no trust."""

    @classmethod
    def setUpClass(cls):
        cls.p = Plugin("gridcraft")

    @classmethod
    def tearDownClass(cls):
        cls.p.close()

    def v(self, name, *args):
        return result(self.p.call(name, *args))["value"]

    def test_values(self):
        self.assertTrue(self.v("PY.VERSION").startswith("Python add-in 0.3.0 on Python 3."))
        self.assertEqual(self.v("PY.TWICE", [[1, 2], [3, "a"]]), [[2, 4], [6, {"error": "#VALUE!"}]])
        self.assertEqual(self.v("PY.MEDIAN", [[1, 5, 3]], 10), 4)
        self.assertEqual(self.v("PY.STATS", [[1, 2, 3, 4]])[:2], [["count", 4], ["sum", 10]])
        self.assertEqual(self.v("PY.REGEX", "order 1234", r"\d+"), "1234")
        self.assertEqual(self.v("PY.REGEX", "a=1", r"(\w)=(\d)", 2), "1")
        self.assertEqual(self.v("PY.REGEX", "abc", "z"), {"error": "#N/A"})
        self.assertEqual(self.v("PY.SPLIT", "a b  c"), [["a", "b", "c"]])
        self.assertEqual(self.v("PY.SPLIT", "a;b", ";"), [["a", "b"]])
        self.assertEqual(self.v("py.split", ""), {"error": "#N/A"})


class OldCommands(unittest.TestCase):
    def test_gridcraft(self):
        g = Grid()
        g.put(0, "A1", [["  hello   world ", 3], ["foo bar", None]])
        g.sel = (0, 0, 1, 1)
        p = Plugin("gridcraft", g)
        try:
            self.assertEqual(result(p.run("titleCase"))["message"], "Title-cased 2 cell(s)")
            self.assertEqual(g.get(0, "A1:A2"), [["  Hello   World "], ["Foo Bar"]])
            result(p.run("trim"))
            self.assertEqual(g.get(0, "A1"), [["Hello World"]])
            g.put(0, "A1", [[1, 2], [3, 4]])
            r = result(p.run("statsTable"))
            self.assertIn("written at D1", r["message"])
            self.assertEqual(g.get(0, "D1:E2"), [["count", 4], ["sum", 10]])
            g.sel = (0, 6, 0, 6)
            result(p.run("fillFibonacci", {"count": 5}))
            self.assertEqual(g.get(0, "G1:G5"), [[0], [1], [1], [2], [3]])
            self.assertIn("count must be", error(p.run("fillFibonacci", {"count": 0})))
            self.assertIn("Select some cells", error(p.run("trim", context={"document": {"path": None, "name": "Book1"}})))
        finally:
            p.close()

    def test_wordcraft(self):
        w = Word(["the quick  brown fox. It jumps!"])
        w.sel = (0, 0, 0, 9)
        p = Plugin("wordcraft", w)
        try:
            result(p.run("titleCase"))
            self.assertEqual(w.paras[0]["text"], "The Quick  brown fox. It jumps!")
            r = result(p.run("textStats", context={"selection": {"text": ""}, "paragraph": {"text": w.paras[0]["text"]}}))
            self.assertEqual(r["result"]["words"], 6)
            self.assertEqual(r["result"]["sentences"], 2)
        finally:
            p.close()


if __name__ == "__main__":
    unittest.main()
