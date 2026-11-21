from test_roots import TOOLKIT_ROOT
"""Notechanges boundaries checks and fixtures."""

import sys
from pathlib import Path
import ast
import re
import unittest

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))


from test_roots import TOOLKIT_ROOT as ROOT


class ModuleBoundaryTest(unittest.TestCase):
    def test_note_modules_import_and_define_only_what_they_use(self):
        sources = {path: path.read_text(encoding="utf-8") for path in (ROOT / "src/gen3tl").rglob("*.py")}
        for name in ("web/notechanges.py", "web/submission.py", "check/prcheck.py"):
            path = ROOT / "src/gen3tl" / name
            tree = ast.parse(sources[path])
            used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
                node.value.id for node in ast.walk(tree)
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)}
            for node in tree.body:
                if isinstance(node, (ast.Import, ast.ImportFrom)) and getattr(node, "module", "") != "__future__":
                    for alias in node.names:
                        bound = (alias.asname or alias.name).split(".")[0]
                        with self.subTest(module=name, imported=bound):
                            self.assertIn(bound, used)
        tree = ast.parse(sources[ROOT / "src/gen3tl/web/notechanges.py"])
        others = "\n".join(text for path, text in sources.items() if path.name != "notechanges.py")
        own = sources[ROOT / "src/gen3tl/web/notechanges.py"]
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                with self.subTest(function=node.name):
                    self.assertTrue(re.search(rf"\b{node.name}\(", others)
                                    or len(re.findall(rf"\b{node.name}\(", own)) > 1)
