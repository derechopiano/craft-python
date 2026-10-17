"""Python for GridCraft, WordCraft and DeckCraft: an add-in (craft plugin protocol 1.2).

The app starts this program and talks to it over standard input and output: one JSON-RPC 2.0
message per line. See https://github.com/storytold/gridcraft/blob/main/docs/plugins.md

Only the Python standard library is used, so it runs with any python3 >= 3.8.
"""

import os
import sys

if sys.version_info < (3, 8):
    sys.stderr.write("The Python add-in needs Python 3.8 or later (this is {})\n".format(sys.version.split()[0]))
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from craftpython.app import main  # noqa: E402

if __name__ == "__main__":
    if "--workbook-macros" in sys.argv:
        from craftpython.workbook_macros import run
        run()
    else:
        main()
