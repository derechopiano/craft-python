"""WordCraft: `doc` on a fake document."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fakehost import Word  # noqa: E402
from harness import Plugin, error, result  # noqa: E402

NEW = {"document": {"path": None, "name": None, "dirty": False}}


class WordScripts(unittest.TestCase):
    def setUp(self):
        self.w = Word(["Title here", "The first paragraph mentions Python.", "And python again."])
        self.w.sel = (1, 4, 1, 9)  # "first"
        self.p = Plugin("wordcraft", self.w)

    def tearDown(self):
        self.p.close()

    def script(self, code, **params):
        params["code"] = code
        ctx = self.w.context()
        return self.p.run("runScript", params, context=ctx)

    def test_paragraphs(self):
        code = "\n".join([
            "ps = doc.paragraphs",
            "ps[0].style = 'Heading 1'",
            "ps[0].alignment = 'Centered'",
            "[(p.text, p.style, p.alignment) for p in ps]",
        ])
        v = result(self.script(code))["result"]["value"]
        self.assertEqual(v[0], ["Title here", "Heading 1", "Centered"])
        self.assertEqual(v[1], ["The first paragraph mentions Python.", "Normal", "Left"])
        self.assertEqual(self.w.paras[0]["style"], "Heading 1")
        self.assertIn("alignment is", error(self.script("doc.paragraphs[0].alignment = 'Sideways'")))
        self.assertEqual(self.w.paras[0]["alignment"], "Centered")

    def test_insert_and_body_text(self):
        code = "\n".join([
            "p = doc.body.insert_paragraph('Line one\\nLine two', 'End')",
            "p.font.bold = True",
            "q = doc.body.insert_paragraph('Top', 'Start')",
            "doc.paragraphs[1].insert_paragraph('After the title', 'After')",
            "doc.paragraphs[-1].insert_text('!', 'End')",
            "doc.body.text",
        ])
        v = result(self.script(code))["result"]["value"]
        self.assertEqual(v.split("\n"), ["Top", "Title here", "After the title", "The first paragraph mentions Python.",
                                         "And python again.", "Line one", "Line two!"])
        self.assertTrue(self.w.paras[6]["font"]["bold"])

    def test_selection_search_font(self):
        code = "\n".join([
            "s = doc.selection",
            "before = s.text",
            "s.insert_text('second')",
            "hits = doc.search('python')",
            "for h in hits:",
            "    h.font.color = 'red'",
            "    h.font.set(bold=True, size=14)",
            "exact = doc.search('Python', match_case=True)",
            "(before, x, doc.paragraphs[1].text, [h.text for h in hits], len(exact), doc.paragraphs[2].font.get()['bold'])",
        ])
        v = result(self.script(code))["result"]["value"]
        self.assertEqual(v[:5], ["first", "first", "The second paragraph mentions Python.", ["Python", "python"], 1])
        self.assertTrue(v[5])
        self.assertEqual(self.w.paras[1]["font"]["color"], "red")

    def test_properties_and_errors(self):
        code = "doc.properties.title = 'Report'\n(doc.properties.title, doc.name, doc.document['path'])"
        self.assertEqual(result(self.script(code))["result"]["value"], ["Report", "Document1", None])
        msg = error(self.script("doc.paragraphs[0].text = 'changed'\ndoc.paragraphs[0].insert_text('x', 'Middle')"))
        self.assertIn("location is one of", msg)
        self.assertEqual(self.w.paras[0]["text"], "Title here")  # rolled back
        code = "\n".join([
            "from craftpython.bridge import BridgeError",
            "try:",
            "    doc.paragraphs[0].insert_paragraph('a\\u2028b')",
            "    doc.sync([{'op': 'call', 'path': [{'p': 'document'}, {'p': 'body'}], 'method': 'insertParagraph', 'args': ['x\\ny', 'End']}])",
            "except BridgeError as e:",
            "    r = e.code",
            "r",
        ])
        self.assertEqual(result(self.script(code))["result"]["value"], "InvalidArgument")

    def test_delete_and_clear(self):
        result(self.script("doc.paragraphs[2].delete()"))
        self.assertEqual(len(self.w.paras), 2)
        result(self.script("doc.body.clear()"))
        self.assertEqual([p["text"] for p in self.w.paras], [""])

    def test_ribbon(self):
        d = result(self.p.run("runScript", context=NEW))["dialog"]
        self.assertEqual([f["id"] for f in d["fields"]], ["code", "file"])  # no "output" outside GridCraft
        self.assertIn("this document", d["prompt"])


if __name__ == "__main__":
    unittest.main()
