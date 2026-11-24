import ast
import sys
from pathlib import Path
import json
import re
import subprocess
import tempfile
import unittest
import zipfile

from gen3tl import TOOLKIT_ROOT
from gen3tl.web import editorcheck
from editorcheck_support import PROF

class BundleTest(unittest.TestCase):
    """The bundle: the modules the check imports and the profile rules."""

    def test_bundle_currency_follows_toolkit_version(self):
        from gen3tl import __version__
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "checker.zip"
            self.assertFalse(editorcheck.bundle_current(archive))
            archive.write_bytes(b"incomplete")
            self.assertFalse(editorcheck.bundle_current(archive))
            with zipfile.ZipFile(archive, "w"):
                pass
            self.assertFalse(editorcheck.bundle_current(archive))
            initializer = (Path(editorcheck.__file__).parents[1] / "__init__.py").read_bytes()
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("gen3tl/__init__.py", initializer.replace(__version__.encode(), b"0.0.0"))
            self.assertFalse(editorcheck.bundle_current(archive))
            editorcheck.write_bundle(archive, {})
            self.assertTrue(editorcheck.bundle_current(archive))

    def test_bundle_holds_exactly_the_modules_the_page_glue_imports(self):
        package = Path(editorcheck.__file__).parents[1]
        files = {path.relative_to(package).as_posix() for path in package.rglob("*.py")}

        def paths(name):
            # importing a module runs its packages too
            parts = name.split(".")
            if parts[0] != "gen3tl":
                return []
            stems = ["/".join(parts[1:end]) for end in range(1, len(parts) + 1)]
            return [file for stem in stems for file in (f"{stem}.py", f"{stem}/__init__.py".lstrip("/"))
                    if file in files]

        def imports(path):
            # function-level imports run in the page too
            here = ["gen3tl", *Path(path).parent.parts]
            for node in ast.walk(ast.parse((package / path).read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    yield from (alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    head = here[:len(here) + 1 - node.level] if node.level else []
                    module = ".".join(head + [node.module] if node.module else head)
                    yield module
                    yield from (f"{module}.{alias.name}" for alias in node.names)

        glue = "\n".join(path.read_text(encoding="utf-8") for path in (TOOLKIT_ROOT / "web/js").glob("*.js"))
        queue = [f"{module}.{name.strip()}" for module, names in re.findall(r"from (gen3tl[\w.]*) import ([\w, ]+)", glue)
                 for name in names.split(",")]
        reached = set()
        while queue:
            for path in paths(queue.pop()):
                if path not in reached:
                    reached.add(path)
                    queue.extend(imports(path))
        self.assertIn("web/editorcheck.py", reached)
        self.assertEqual(sorted(reached - set(editorcheck.BUNDLE)), [])
        self.assertEqual(sorted(set(editorcheck.BUNDLE) - reached), [])

    def test_rule_models_and_evaluator_run_in_isolated_bundle(self):
        from test_languagerules import fixture
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "checker.zip"
            editorcheck.write_bundle(archive, {})
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(root)
            (root / "rule.json").write_text(json.dumps(fixture()))
            code = '''import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from gen3tl.web.editorrules import rules_data
from gen3tl.check.languagerules import evaluate
data = json.loads((Path(sys.argv[1]) / "rule.json").read_text())
rule = rules_data(data, [])["rules"][0]
result = evaluate(rule, bytes([187, 255]), {"suffix": bytes([255])}, routine="main", quantity=2)
assert result.result == bytes([187, 255])
assert result.stored == bytes([187, 255, 24])
assert not result.problems
'''
            run = subprocess.run([sys.executable, "-I", "-c", code, tmp], capture_output=True, text=True)
            self.assertEqual((run.returncode, run.stdout, run.stderr), (0, "", ""))


    def test_flat_runtime_profile_loads_without_family_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "checker.zip"
            editorcheck.write_bundle(archive, PROF["qa"])
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(root)
            profile = root / "runtime.toml"
            profile.write_text('[rom]\nsha1 = "' + '0' * 40 + '"\n[save]\nsector_size = 4096\n')
            code = '''import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from gen3tl.profile.profile import Profile
profile = Profile.load(Path(sys.argv[1]) / "runtime.toml")
from gen3tl.families import knowledge
assert profile.data == {"rom": {"sha1": "0" * 40}, "save": {"sector_size": 4096}}
assert not knowledge.FAMILY_DIR.exists()
assert Path(knowledge.__file__).is_relative_to(Path(sys.argv[1]))
print("merged profile loaded")
'''
            result = subprocess.run([sys.executable, "-I", "-c", code, tmp],
                                    capture_output=True, text=True)
            self.assertEqual((result.returncode, result.stdout, result.stderr),
                             (0, "merged profile loaded\n", ""))
