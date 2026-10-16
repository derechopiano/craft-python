"""Value conversions and A1 addresses (in-process)."""

import datetime
import decimal
import fractions
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from craftpython import values as v  # noqa: E402


class Conversions(unittest.TestCase):
    def test_results(self):
        r = v.to_result
        self.assertEqual(r(None), "")
        self.assertEqual(r(True), True)
        self.assertEqual(r(3), 3)
        self.assertEqual(r(2 ** 60), float(2 ** 60))
        self.assertEqual(r(10 ** 400), {"error": "#NUM!"})
        self.assertEqual(r(float("inf")), {"error": "#NUM!"})
        self.assertEqual(r(decimal.Decimal("1.5")), 1.5)
        self.assertEqual(r(fractions.Fraction(1, 4)), 0.25)
        self.assertEqual(r(datetime.date(2024, 1, 5)), "2024-01-05")
        self.assertEqual(r(datetime.datetime(2024, 1, 5, 14, 30)), "2024-01-05 14:30:00")
        self.assertEqual(r("x" * 40000), "x" * 32767)
        self.assertEqual(r([1, 2]), [[1], [2]])
        self.assertEqual(r([[1, 2], [3]]), [[1, 2], [3, ""]])
        self.assertEqual(r((x * x for x in range(3))), [[0], [1], [4]])
        self.assertEqual(r({"a": 1, "b": None}), [["a", 1], ["b", ""]])
        self.assertEqual(r({3, 1, 2}), [[1], [2], [3]])
        self.assertEqual(r([]), [[""]])
        self.assertEqual(r(range(1000001)), {"error": "#NUM!"})
        self.assertEqual(r([[1, [2, 3]]]), [[1, "[2, 3]"]])
        self.assertEqual(r(v.CellError("#N/A")), {"error": "#N/A"})
        self.assertEqual(r(object), "<class 'object'>")
        self.assertEqual(r(dict), "<class 'dict'>")

    def test_from_wire(self):
        self.assertEqual(v.from_wire([[1, {"error": "#DIV/0!"}]]), [[1, "#DIV/0!"]])
        self.assertIsInstance(v.from_wire({"error": "#N/A"}), v.CellError)
        self.assertEqual(v.first_error([[1, {"error": "#N/A"}]]), {"error": "#N/A"})

    def test_addresses(self):
        self.assertEqual(v.parse_range("B3"), (2, 1, 2, 1))
        self.assertEqual(v.parse_range("$C$4:a1"), (0, 0, 3, 2))
        self.assertIsNone(v.parse_range("A:A"))
        self.assertIsNone(v.parse_range("A0"))
        self.assertEqual(v.format_range(0, 26, 9, 27), "AA1:AB10")
        self.assertEqual(v.split_address("'It''s'!A1"), ("It's", "A1"))
        self.assertEqual(v.split_address("A1"), (None, "A1"))
        self.assertEqual(v.offset("Sheet1!Z3", 2), "Sheet1!AB3")
        self.assertEqual(v.col_letters(v.col_index("XFD")), "XFD")


if __name__ == "__main__":
    unittest.main()
