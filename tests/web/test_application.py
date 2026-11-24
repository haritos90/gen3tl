"""Application assembly publishes only interface data."""

import contextlib
import html
import importlib.util
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

from test_roots import TOOLKIT_ROOT
from gen3tl import __version__
from gen3tl.cli.web import site_assemble, site_registry
from gen3tl.project import registry_scope
from gen3tl.web import editordata, editorstore, sitecatalog, siteshell


class ApplicationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.registry = self.root / "registry.json"
        self.doc = {"schema": 1, "games": [], "include": ["https://game.test/data/registry.json"]}
        self.registry.write_text(json.dumps(self.doc), encoding="utf-8")

    def assemble(self, **options):
        args = SimpleNamespace(registry_file=str(self.registry), out=str(self.root / "site"),
                               public_base="https://site.test/app", **options)
        with registry_scope(self.root), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(site_assemble(args), 0)
        return Path(args.out)

    def test_include_validates_addresses_without_fetching(self):
        for include in [[], ["../game/registry.json", "https://game.test/data/registry.json", "http://localhost/data/registry.json"]]:
            doc = {**self.doc, "include": include}
            self.assertEqual(editordata.validate_registry(doc), doc)
        for include in [None, "registry.json", [1], [""], ["//game.test/data"], ["file:///data"], ["https://"], ["../a b"]]:
            with self.subTest(include=include), self.assertRaisesRegex(ValueError, "invalid include"):
                editordata.validate_registry({**self.doc, "include": include})

    def test_registry_rewrite_preserves_include(self):
        (self.root / "languages.json").write_text(json.dumps({"profiles": [], "languages": []}), encoding="utf-8")
        args = SimpleNamespace(data=str(self.root), out=None, data_url="./", game=[], repository=None)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(site_registry(args), 0)
        self.assertEqual(json.loads(self.registry.read_text()), self.doc)

    def test_tracked_application_registry_lists_game_sites_without_game_metadata(self):
        doc = editordata.validate_registry(json.loads((TOOLKIT_ROOT / ".github/site-registry.json").read_text()))
        self.assertEqual(doc["games"], [])
        self.assertTrue(doc["include"])
        self.assertEqual(len(set(doc["include"])), len(doc["include"]))
        for address in doc["include"]:
            self.assertRegex(address, r"^https://[^/]+/.+/registry\.json$")
        self.assertEqual(set(doc), {"schema", "games", "include", "site"})

    def test_tracked_application_registry_lists_the_licences_of_the_guide_footer(self):
        def links(region):
            found = re.findall(r'<a href="([^"]*)"[^>]*>([^<]*)</a>', region)
            self.assertEqual(len(found), region.count("<a "), region)
            return [(html.unescape(url), html.unescape(label)) for url, label in found]

        data = self.root / "data"
        data.mkdir()
        shutil.copyfile(TOOLKIT_ROOT / ".github/site-registry.json", data / "registry.json")   # as the build writes it
        footer = links(siteshell.render("footer", "index.html", siteshell.WEB, siteshell.registry_licenses(data)))
        guide = links(siteshell.render("footer-static", "guide.html", siteshell.WEB))
        self.assertTrue(guide)
        self.assertEqual(footer, guide)

    def test_application_contains_exact_interface_files_and_readable_editor_parts(self):
        out = self.assemble()
        data = (out / "data").resolve()
        expected = {"registry.json", "languages.json", "site/catalogs.json", "site/startup.js"}
        for code in sitecatalog.catalogs():
            expected.update({f"site/{code}.json", f"site/{code}/index.json"})
            index = data / f"site/{code}/index.json"
            expected.update(path.relative_to(data).as_posix() for path in editorstore.referenced(index))
            doc = json.loads(index.read_text())
            expected.update(f"site/{code}/{chunk['path']}" for chunk in doc["chunks"])
            self.assertEqual(doc["profile"], "site")
        actual = {path.relative_to(data).as_posix() for path in data.rglob("*") if path.is_file()}
        self.assertEqual(actual, expected)
        manifest = json.loads((data / "languages.json").read_text())
        self.assertEqual([row["id"] for row in manifest["profiles"]], ["site"])
        self.assertNotIn("bundle", manifest)
        self.assertEqual(json.loads((data / "registry.json").read_text()), self.doc)
        self.assertFalse((out / "share").exists())
        self.assertFalse(list(out.rglob("*.zip")))

    def test_application_pages_list_the_interface_catalogs_before_their_startup(self):
        out = self.assemble()
        meta = f'<meta name="site-catalogs" content="{" ".join(sitecatalog.catalogs())}">'
        pages = sorted(out.glob("*.html"))
        self.assertGreater(len(pages), 10)
        for page in pages:
            document = page.read_text(encoding="utf-8")
            self.assertEqual(document.count('name="site-catalogs"'), 1, page.name)
            self.assertLess(document.index(meta), document.index("js/theme.js"), page.name)

    def test_application_manifest_lists_the_preview_picture_with_the_scripts(self):
        folder = self.assemble() / "assets" / __version__
        files = {path.relative_to(folder).as_posix() for path in folder.rglob("*") if path.is_file()} - {"files.json"}
        manifest = json.loads((folder / "files.json").read_text())
        self.assertEqual(manifest["version"], __version__)
        self.assertEqual(set(manifest["files"]), files)
        self.assertEqual(len([name for name in files if name.startswith("previews/")]), 1)
        self.assertIn("js/patcher/rom-patcher.wasm", files)

    def test_application_cli_runs_in_toolkit_checkout_without_a_project(self):
        import os
        env = {key: value for key, value in os.environ.items() if not key.startswith("GEN3TL_")}
        env["PYTHONPATH"] = str(TOOLKIT_ROOT / "src")
        out = self.root / "cli"
        result = subprocess.run([sys.executable, "-m", "gen3tl", "web", "assemble", "--registry",
                                 ".github/site-registry.json", "--out", str(out), "--public-base", "https://site.test/app"],
                                cwd=TOOLKIT_ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((out / "data/site/ru/index.json").is_file())

    def test_application_refuses_invalid_registry_before_writing(self):
        self.registry.write_text('{"schema": 2, "games": []}', encoding="utf-8")
        out = self.root / "site"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(site_assemble(SimpleNamespace(registry_file=str(self.registry), out=str(out))), 1)
        self.assertFalse(out.exists())

    def test_data_and_registry_options_are_mutually_exclusive(self):
        from gen3tl.cli.main import parser
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser()[0].parse_args(["web", "assemble", "--data", "data", "--registry", "registry.json"])

    def test_publication_guard_rejects_game_data_and_binary_payloads(self):
        out = self.assemble()
        spec = importlib.util.spec_from_file_location("application_check", TOOLKIT_ROOT / ".github/scripts/site-check.py")
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        self.assertGreater(guard.check(out), 0)
        for name, content in [("data/game/index.json", b"{}"), ("share/index.html", b"page"),
                              ("sample.sav", b"save"), ("sample.gba", b"rom"), ("sample.zip", b"archive"),
                              ("sample.ups", b"patch"), ("hidden.txt", b"PK\x03\x04archive"),
                              ("hidden.txt", b"UPS1patch")]:
            path = out / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            with self.subTest(name=name, content=content), self.assertRaises(ValueError):
                guard.check(out)
            path.unlink()
            if name.startswith("share/"):
                path.parent.rmdir()
