"""Optional workbook-project role, using craft-python's existing App and Channel.

Run Script, Console, Run File, PY.EVAL and the other apps keep their ordinary path.
Only --workbook-macros loads this adapter or starts the uv-managed worker.
"""
import argparse

from .app import App, main
from .commands import PluginError
from .wire import HostError
from . import workbook_projects

MACRO_ID = "io.github.sirmmo.python.macros"


class WorkbookMacroApp(App):
    def __init__(self, channel, options):
        super().__init__(channel)
        workbook_projects.configure(channel.request, options.uv_executable,
                                    options.python_interpreter, options.macro_cache)

    def handle(self, method, params):
        if method == "initialize":
            result = super().handle(method, params)
            if self.host != "gridcraft":
                raise PluginError("Workbook Python projects require GridCraft")
            try:
                workbook_projects.handle(method, params)
            except RuntimeError as error:
                raise PluginError(str(error)) from error
            result["id"] = MACRO_ID
            return result
        if method.startswith("macros/"):
            try:
                return workbook_projects.handle(method, params)
            except (RuntimeError, ValueError, SyntaxError, OSError, HostError) as error:
                raise PluginError(str(error)) from error
        return super().handle(method, params)


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workbook-macros", action="store_true")
    parser.add_argument("--uv-executable", default="uv")
    parser.add_argument("--python-interpreter")
    parser.add_argument("--macro-cache")
    options = parser.parse_args()
    try:
        main(lambda channel: WorkbookMacroApp(channel, options))
    finally:
        workbook_projects.close_worker()
