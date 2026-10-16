"""Trust: scripts and PY.EVAL run only in trusted documents; the fixed functions always."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fakehost import Deck, Grid, Word  # noqa: E402
from harness import Plugin, error, result  # noqa: E402


def doc(path=None, name=None):
    return {"document": {"path": path, "name": name, "dirty": False}}


class GridTrust(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.book = os.path.join(self.dir.name, "book.xlsx")
        self.g = Grid()
        self.p = Plugin("gridcraft", self.g, data=os.path.join(self.dir.name, "data"))

    def tearDown(self):
        self.p.close()
        self.dir.cleanup()

    def ctx(self, path=None, name="Book1"):
        return self.g.context(path, name)

    def test_eval_off_until_told(self):
        self.assertIn("turned off", error(self.p.call("PY.EVAL", "1+1")))
        self.assertEqual(result(self.p.call("PY.MEDIAN", 1, 2, 3))["value"], 2)  # fixed helpers always work
        result(self.p.run("about", context=self.ctx()))                         # a command in Book1
        self.assertEqual(result(self.p.call("PY.EVAL", "1+1"))["value"], 2)

    def test_saved_workbook(self):
        ctx = self.ctx(self.book, "book.xlsx")
        result(self.p.run("about", context=ctx))
        self.assertIn("turned off", error(self.p.call("PY.EVAL", "1+1")))
        # programmatic scripts fail; the form's run offers trust
        self.assertIn("turned off", error(self.p.run("runScript", {"code": "1"}, context=ctx)))
        r = result(self.p.run("runScript", {"fields": {"code": "40 + 2", "file": ""}}, context=ctx))
        d = r["dialog"]
        self.assertEqual((d["command"], d["params"]["then"], d["params"]["path"]), ("trustConfirm", "runScript", self.book))
        self.assertIn("anything you can", d["prompt"])
        # the ribbon button asks for trust first
        self.assertEqual(result(self.p.run("runScript", context=ctx))["dialog"]["command"], "trustConfirm")
        # trust asks; trustConfirm trusts and goes back to the form with the code
        d = result(self.p.run("trust", context=ctx))["dialog"]
        self.assertEqual(d["params"], {"path": self.book, "name": "book.xlsx"})
        r = result(self.p.run("trustConfirm", {"path": self.book, "name": "book.xlsx", "then": "runScript"}, context=ctx))
        self.assertIn("is trusted", r["message"])
        self.assertEqual(r["dialog"]["fields"][0]["value"], "40 + 2")
        self.assertEqual(result(self.p.call("PY.EVAL", "x*2", 21))["value"], 42)
        self.assertEqual(result(self.p.run("runScript", {"code": "6*7"}, context=ctx))["result"]["value"], 42)
        with open(os.path.join(self.p.data, "trusted.json"), encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved["documents"], [os.path.normcase(os.path.abspath(self.book))])
        # another process (another app) sees it
        w = Plugin("wordcraft", Word(), data=self.p.data)
        try:
            self.assertEqual(result(w.run("runScript", {"code": "'ok'"}, context=doc(self.book, "book.xlsx")))["result"]["value"], "ok")
        finally:
            w.close()
        # the list, removing
        r = result(self.p.run("trusted", context=ctx))
        self.assertEqual(r["dialog"]["kind"], "list")
        self.assertEqual(r["dialog"]["items"][0]["label"], "book.xlsx")
        result(self.p.run("untrustPaths", {"ids": [r["dialog"]["items"][0]["id"]]}, context=ctx))
        self.assertIn("turned off", error(self.p.call("PY.EVAL", "1")))
        self.assertIn("No document", result(self.p.run("trusted", context=ctx))["dialog"]["prompt"])
        # untrust
        result(self.p.run("trustConfirm", {"path": self.book, "name": "book.xlsx"}, context=ctx))
        self.assertIn("Python is off", result(self.p.run("untrust", context=ctx))["message"])
        self.assertIn("turned off", error(self.p.call("PY.EVAL", "1")))

    def test_trust_confirm_checks_the_document(self):
        other = os.path.join(self.dir.name, "other.xlsx")
        msg = error(self.p.run("trustConfirm", {"path": other, "name": "other.xlsx"}, context=self.ctx(self.book, "book.xlsx")))
        self.assertIn("isn't the one", msg)

    def test_unsaved_imports_trusted_for_the_session(self):
        ctx = self.ctx(None, "data.ods")
        result(self.p.run("about", context=ctx))
        self.assertIn("turned off", error(self.p.call("PY.EVAL", "1")))
        result(self.p.run("trustConfirm", {"path": None, "name": "data.ods"}, context=ctx))
        self.assertEqual(result(self.p.call("PY.EVAL", "1"))["value"], 1)

    def test_events(self):
        result(self.p.run("trustConfirm", {"path": self.book, "name": "book.xlsx"}, context=self.ctx(self.book, "book.xlsx")))
        other = os.path.join(self.dir.name, "other.xlsx")
        self.assertEqual(result(self.p.event("documentOpened", path=other, name="other.xlsx")), {})
        self.assertIn("turned off", error(self.p.call("PY.EVAL", "1")))
        result(self.p.event("documentOpened", path=self.book, name="book.xlsx"))
        self.assertEqual(result(self.p.call("PY.EVAL", "1"))["value"], 1)
        result(self.p.event("documentClosed", path=self.book, name="book.xlsx"))
        self.assertIn("turned off", error(self.p.call("PY.EVAL", "1")))
        # Book1 saved under a new name: no longer trusted, and the app is told
        result(self.p.run("about", context=self.ctx()))
        r = result(self.p.event("documentSaved", path=other, name="other.xlsx", format="xlsx"))
        self.assertIn("isn't trusted yet", r["message"])
        self.assertIn("turned off", error(self.p.call("PY.EVAL", "1")))

    def test_trust_all(self):
        p = Plugin("gridcraft", Grid(), env={"CRAFT_PYTHON_TRUST": "all"})
        try:
            self.assertEqual(result(p.call("PY.EVAL", "'yes'"))["value"], "yes")
        finally:
            p.close()

    def test_no_document_in_context(self):
        """An app before 0.7 doesn't send context.document: Python stays off."""
        self.assertIn("didn't say", error(self.p.run("runScript", {"code": "1"}, context={"workbook": "Book1"})))
        self.assertIn("0.7", error(self.p.run("trust", context={})))


class OtherApps(unittest.TestCase):
    def test_new_documents_are_trusted(self):
        for app, model, ctx in [("wordcraft", Word(), doc(None, None)),
                                ("deckcraft", Deck(), doc(None, "Presentation1"))]:
            p = Plugin(app, model)
            try:
                self.assertEqual(result(p.run("runScript", {"code": "doc.app"}, context=ctx))["result"]["value"], app)
                saved = doc("/tmp/x." + ("docx" if app == "wordcraft" else "pptx"), "x")
                self.assertIn("turned off for this " + ("document" if app == "wordcraft" else "presentation"),
                              error(p.run("runScript", {"code": "1"}, context=saved)))
                self.assertIn("turned off", error(p.run("console", {"code": "1"}, context=saved)))
                d = result(p.run("console", context=saved))["dialog"]
                self.assertEqual(d["params"]["then"], "console")
            finally:
                p.close()


if __name__ == "__main__":
    unittest.main()
