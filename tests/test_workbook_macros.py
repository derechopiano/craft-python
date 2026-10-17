"""The optional role reuses the 0.3.0 dispatcher without replacing its script features."""
import argparse
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

from craftpython.app import App, Unknown
from craftpython.commands import PluginError
from craftpython.workbook_macros import WorkbookMacroApp, MACRO_ID

ROOT = pathlib.Path(__file__).resolve().parent.parent
FEATURES = ["pythonProjects", "engineCommands", "literalValues"]


class Channel:
    def __init__(self):
        self.calls = []

    def request(self, method, params):
        self.calls.append((method, params))
        if method == "host/pythonProject/get":
            return {"documentId": 1, "project": {"metadata": "", "modules": {
                "analysis": "raise RuntimeError('discovery must not execute this')\ndef run():\n    pass\n"}}}
        raise AssertionError(method)


class WorkbookMacros(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        options = argparse.Namespace(uv_executable="missing-uv", python_interpreter=None,
                                     macro_cache=self.folder.name)
        self.channel = Channel()
        self.app = WorkbookMacroApp(self.channel, options)

    def test_regular_start_does_not_load_the_macro_runtime(self):
        code = ("import main,sys; "
                "assert 'craftpython.workbook_macros' not in sys.modules; "
                "assert 'craftpython.workbook_projects' not in sys.modules; "
                "assert 'craftpython.process_tree' not in sys.modules")
        subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True, timeout=10,
                       capture_output=True)
        regular = App(Channel())
        with self.assertRaises(Unknown):
            regular.handle("macros/list", {})

    def test_optional_role_reuses_app_dispatch_and_inert_discovery(self):
        info = self.app.handle("initialize", {"host": {"id": "gridcraft", "features": FEATURES}})
        self.assertEqual(info["id"], MACRO_ID)
        self.assertEqual(info["version"], "0.3.0")
        self.assertEqual(self.app.handle("functions/call", {"name": "PY.TWICE", "args": [21]}), {"value": 42})
        listing = self.app.handle("macros/list", {})
        self.assertIn("analysis.run", str(listing))
        self.assertEqual(self.channel.calls[0][0], "host/pythonProject/get")

    def test_unsupported_hosts_and_missing_capabilities_are_refused(self):
        with self.assertRaisesRegex(PluginError, "require GridCraft"):
            self.app.handle("initialize", {"host": {"id": "wordcraft", "features": FEATURES}})
        with self.assertRaisesRegex(PluginError, "host extension"):
            self.app.handle("initialize", {"host": {"id": "gridcraft", "features": []}})

    def test_macro_manifest_cannot_shadow_the_regular_role(self):
        regular = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
        macro = json.loads((ROOT / "plugin-macros.json").read_text(encoding="utf-8"))
        self.assertEqual(regular["apps"], ["gridcraft", "wordcraft", "deckcraft"])
        self.assertNotIn("macros", regular)
        self.assertNotEqual(regular["id"], macro["id"])
        self.assertEqual(macro["version"], regular["version"])
        self.assertEqual((macro["functions"], macro["commands"]), ([], []))
        self.assertEqual(macro["macros"], {"language": "Python", "recording": False, "ribbonCallbacks": False})
