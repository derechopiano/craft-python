"""Which documents may run Python.

Scripts, the console and `PY.EVAL` run only in trusted documents. Trusted documents are a list of
absolute paths in `trusted.json` in the add-in's data folder, shared by GridCraft, WordCraft and
DeckCraft. A new document that was never saved (Book1, Document1, Presentation1) is trusted while
unsaved: you're the one writing in it. Other unsaved documents (an imported file, a sample) can
be trusted until the app stops the add-in.

Formulas don't say which workbook they're in, so `PY.EVAL` follows the document the app last told
the add-in about (a command's `context.document`, or the document events); none yet means
untrusted. `CRAFT_PYTHON_TRUST=all` trusts every document (scripts and CI only).
"""

import json
import os
import re
import sys

from .wire import log

_NEW_DOC = re.compile(r"^(Book|Document|Presentation)[0-9]+$")


def data_dir():
    d = os.environ.get("CRAFT_PYTHON_DATA")
    if d:
        return d
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
        return os.path.join(base, "craft-python")
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "craft-python")
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "craft-python")


def trust_file():
    return os.path.join(data_dir(), "trusted.json")


def norm(path):
    p = os.path.normpath(os.path.abspath(os.path.expanduser(path)))
    return os.path.normcase(p)


def trust_all():
    return (os.environ.get("CRAFT_PYTHON_TRUST") or "").strip().lower() == "all"


class Trust:
    def __init__(self):
        self._paths = []
        self._mtime = None
        self.session = set()  # names of unsaved documents trusted until the add-in stops
        # The document the app last told us about (for formulas): known, path, name.
        self.known = False
        self.path = None
        self.name = None

    # ------------------------------------------------------------------ the list

    def paths(self):
        f = trust_file()
        try:
            mtime = os.stat(f).st_mtime
        except OSError:
            self._paths, self._mtime = [], None
            return []
        if mtime == self._mtime:
            return list(self._paths)
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            docs = data.get("documents") if isinstance(data, dict) else None
            self._paths = [p for p in docs if isinstance(p, str)] if isinstance(docs, list) else []
        except Exception as e:  # unreadable: nothing is trusted
            log("can't read {}: {}".format(f, e))
            self._paths = []
        self._mtime = mtime
        return list(self._paths)

    def save(self, paths):
        d = data_dir()
        os.makedirs(d, exist_ok=True)
        f = trust_file()
        tmp = f + ".tmp"
        unique = []
        for p in paths:
            if p not in unique:
                unique.append(p)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "documents": unique}, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        os.replace(tmp, f)
        self._paths, self._mtime = unique, None

    def add(self, path):
        self.save(self.paths() + [norm(path)])

    def remove(self, paths):
        gone = {norm(p) for p in paths} | set(paths)
        self.save([p for p in self.paths() if p not in gone])

    # ------------------------------------------------------------------ documents

    @staticmethod
    def is_new(path, name):
        """A document that was never saved and that the app made new (not an imported file)."""
        if path:
            return False
        return name is None or bool(_NEW_DOC.match(name))

    def trusted(self, path, name):
        if trust_all():
            return True
        if path:
            return norm(path) in self.paths()
        return self.is_new(path, name) or (name is not None and name in self.session)

    def set_active(self, path, name):
        self.known = True
        self.path = path or None
        self.name = name

    def forget(self, path):
        if self.known and self.path and path and norm(self.path) == norm(path):
            self.known = False
            self.path = self.name = None

    def functions_trusted(self):
        return trust_all() or (self.known and self.trusted(self.path, self.name))


def document_of(context):
    """(known, path, name) of the document a command runs in, from `context.document`."""
    d = context.get("document") if isinstance(context, dict) else None
    if not isinstance(d, dict):
        return False, None, None
    path = d.get("path") if isinstance(d.get("path"), str) and d.get("path") else None
    name = d.get("name") if isinstance(d.get("name"), str) else None
    if name is None and isinstance(context.get("workbook"), str):
        name = context.get("workbook")
    if name is None and path:
        name = os.path.basename(path)
    return True, path, name
