"""DeckCraft: `doc` on a fake presentation."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fakehost import Deck  # noqa: E402
from harness import Plugin, error, result  # noqa: E402


class DeckScripts(unittest.TestCase):
    def setUp(self):
        self.d = Deck(slides=2)
        self.p = Plugin("deckcraft", self.d)

    def tearDown(self):
        self.p.close()

    def script(self, code, **params):
        params["code"] = code
        return self.p.run("runScript", params)

    def test_slides(self):
        code = "\n".join([
            "s = doc.slides.add()",
            "t = doc.add_slide(index=0)",
            "ids = [x.id for x in doc.slides]",
            "doc.slides[1].delete()",
            "(len(doc.slides), ids, s.index, t.index, doc.slides.index(s), doc.title)",
        ])
        v = result(self.script(code))["result"]["value"]
        self.assertEqual(v[0], 3)
        self.assertEqual(v[1], ["304", "301", "302", "303"])
        self.assertEqual(v[2:], [2, 0, 2, "Presentation1"])

    def test_shapes(self):
        code = "\n".join([
            "slide = doc.current_slide",
            "box = slide.shapes.add_text_box('Hello', left=50, top=40, width=300, height=60)",
            "rect = slide.shapes.add_shape('ellipse', 10, 10, 80, 80)",
            "line = slide.shapes.add_line()",
            "box.text = 'Hello, slides'",
            "box.font.bold = True",
            "box.font.set(size=28, color='navy')",
            "rect.fill.color = 'yellow'",
            "rect.move(200, 220)",
            "rect.resize(120, 90)",
            "box.name = 'Greeting'",
            "shapes = slide.shapes",
            "[(s.name, s.type, s.left, s.top, s.width, s.height, s.text) for s in shapes] + [(rect.fill.color, len(shapes))]",
        ])
        v = result(self.script(code))["result"]["value"]
        self.assertEqual(v[0], ["Greeting", "textBox", 50.0, 40.0, 300.0, 60.0, "Hello, slides"])
        self.assertEqual(v[1][1:6], ["ellipse", 200.0, 220.0, 120.0, 90.0])
        self.assertEqual(v[2][1], "line")
        self.assertIsNone(v[2][6])  # a line has no text frame
        self.assertEqual(v[3], ["yellow", 3])
        sh = self.d.slides[0]["shapes"][0]
        self.assertEqual(sh["font"], {"bold": True, "size": 28, "color": "navy"})

    def test_selection(self):
        sid = self.d.slides[1]["id"]
        self.d.current = 1
        result(self.script("doc.current_slide.shapes.add_text_box('a'); doc.current_slide.shapes.add_text_box('b')"))
        self.d.selected_shapes = [self.d.slides[1]["shapes"][1]["id"]]
        self.d.selected_slides = [sid]
        code = "([s.id for s in doc.selected_slides], [s.text for s in doc.selected_shapes], doc.current_slide.id)"
        v = result(self.script(code))["result"]["value"]
        self.assertEqual(v, [[sid], ["b"], sid])
        result(self.script("doc.slides[0].select()"))
        self.assertEqual(self.d.selected_slides, [self.d.slides[0]["id"]])

    def test_errors(self):
        msg = error(self.script("doc.current_slide.shapes.add_text_box('kept?')\ndoc.current_slide.shapes.add_shape('blob')"))
        self.assertIn("BridgeError: InvalidArgument: `blob` isn't a shape type", msg)
        self.assertEqual(self.d.slides[0]["shapes"], [])  # all or nothing
        r = result(self.p.run("runScript", {"fields": {"code": "doc.slides[9]"}}))
        self.assertIn("IndexError", r["dialog"]["prompt"])

    def test_old_text_commands_say_where_they_work(self):
        self.assertIn("GridCraft and WordCraft", error(self.p.run("trim")))


if __name__ == "__main__":
    unittest.main()
