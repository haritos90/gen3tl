"""Editor export compatibility across submission and import readers."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gen3tl.catalog.po import PoEntry, write
from gen3tl.cli.web import editor_import
from gen3tl.project import Registry
from gen3tl.web import editorimport, exportenvelope, sitecatalog, submission


class EnvelopeTest(unittest.TestCase):
    def test_fields_and_legacy_defaults(self):
        doc = {"profile": "fixture", "language": "xx"}
        optional = {"entries": [], "pictures": [], "terms": [], "glyphs": [], "keyboard": {},
                    "engine_rules": [], "note_changes_schema": "3.0.0", "note_changes": [],
                    "notes": "Review context", "rom_sha1": "ab" * 20}
        self.assertEqual(exportenvelope.validate(doc), {"version": "4.0.0", **doc})
        self.assertNotIn("version", doc)
        self.assertEqual(exportenvelope.validate({**doc, **optional}), {"version": "4.0.0", **doc, **optional})
        for key in optional:
            with self.subTest(field=key), self.assertRaisesRegex(ValueError, rf"supported 4\.0\.0: {key} must be"):
                exportenvelope.validate({**doc, **optional, key: 12})
        for key in ("profile", "language"):
            with self.subTest(field=key), self.assertRaisesRegex(ValueError, rf"{key} must be a string"):
                exportenvelope.validate({**doc, key: None})
        nullable = {**doc, "keyboard": None, "notes": None}
        self.assertEqual(exportenvelope.validate(nullable), {"version": "4.0.0", **nullable})

    def test_note_fields_require_each_other(self):
        for key, value, missing in (("note_changes", [], "note_changes_schema"),
                                    ("note_changes_schema", "3.0.0", "note_changes")):
            with self.subTest(field=key), self.assertRaisesRegex(ValueError, f"missing required field: {missing}"):
                exportenvelope.validate({"profile": "fixture", "language": "xx", key: value})

    def test_versions_are_semver_and_errors_stay_on_one_line(self):
        doc = {"profile": "fixture", "language": "xx"}
        for version in ("4.0.0", "4.12.3", "4.2.3-rc.1+build.2"):
            with self.subTest(version=version):
                self.assertEqual(exportenvelope.validate({**doc, "version": version})["version"], version)
        for version in (None, 1, True, [], {}, "1", "04.0.0", "4.0.0-01", "4.0.0+", "1.0.0", "3.0.0", "4.0.0\nerror"):
            with self.subTest(version=version), self.assertRaises(ValueError) as caught:
                exportenvelope.validate({**doc, "version": version})
            message = str(caught.exception)
            self.assertIn(json.dumps(version), message)
            self.assertIn("supported 4.0.0", message)
            self.assertEqual(len(message.splitlines()), 1)


class ReaderTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.path = self.root / "export.json"
        self.i18n = self.root / "i18n"
        self.entry = PoEntry("site:Home", "Home", "Home")
        self.catalog = self.i18n / "xx.po"
        write(self.catalog, [self.entry], {"Project-Id-Version": "site", "Language": "xx"})
        self.before = self.catalog.read_bytes()
        hook = patch.object(sitecatalog, "CATALOGS", self.i18n)
        hook.start()
        self.addCleanup(hook.stop)
        self.registry = Registry(self.root, "", "", ())
        self.doc = {"profile": "site", "language": "xx", "entries": [
            {"id": "site:Home", "translation": "Start", "base": "Home"}]}

    def readers(self):
        return {
            "submission.parse": lambda: submission.parse(self.path.read_bytes()),
            "submission.apply": lambda: submission.apply(self.path, self.registry, check=True),
            "editorimport.read_export": lambda: editorimport.read_export(
                self.path, "site", "xx", "0" * 40, {self.entry.msgctxt: (self.catalog, self.entry)}),
            "sitecatalog.read_export": lambda: sitecatalog.read_export(self.path),
            "sitecatalog.apply_export": lambda: sitecatalog.apply_export(
                json.loads(self.path.read_bytes()), write=False),
        }

    def test_versioned_legacy_and_compatible_exports(self):
        for version in (None, "4.0.0", "4.7.2"):
            doc = {**self.doc, **({"version": version} if version else {}), "extension": {"kept": True}}
            self.path.write_text(json.dumps(doc))
            for name, reader in self.readers().items():
                with self.subTest(reader=name, version=version):
                    result = reader()
                    if isinstance(result, dict):
                        result = result.get("document", result)
                        self.assertEqual(result["version"], version or "4.0.0")
                        self.assertEqual(result["extension"], doc["extension"])
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(editor_import(SimpleNamespace(export_file=self.path, dry_run=True)), 0)
            self.assertIn("planned", output.getvalue())
            self.assertEqual(self.catalog.read_bytes(), self.before)

    def test_incompatible_and_missing_fields_stop_every_reader(self):
        major, minor, patch_version = map(int, exportenvelope.VERSION.split("."))
        unsupported = f"{major + 1}.0.0"
        cases = [({**self.doc, "version": unsupported}, unsupported, "unsupported major version")]
        for version in (None, f"{major}.{minor + 1}.{patch_version}"):
            for field in ("profile", "language"):
                doc = {key: value for key, value in self.doc.items() if key != field}
                if version:
                    doc["version"] = version
                cases.append((doc, version or exportenvelope.VERSION, f"missing required field: {field}"))
        for doc, version, reason in cases:
            self.path.write_text(json.dumps(doc))
            for name, reader in self.readers().items():
                with self.subTest(reader=name, version=version, reason=reason), self.assertRaises(ValueError) as caught:
                    reader()
                self.assertIn(f'version "{version}"; supported {exportenvelope.VERSION}: {reason}', str(caught.exception))
                self.assertEqual(len(str(caught.exception).splitlines()), 1)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(editor_import(SimpleNamespace(export_file=self.path, dry_run=False)), 1)
            self.assertEqual(len(output.getvalue().splitlines()), 1)
            self.assertIn(f'version "{version}"; supported {exportenvelope.VERSION}: {reason}', output.getvalue())
            self.assertEqual(self.catalog.read_bytes(), self.before)

    def test_pack_import_validates_before_dispatch(self):
        args = SimpleNamespace(export_file=self.path, profile="fixture", dry_run=True)
        for version in (None, "4.0.0", "1.0.0"):
            for missing in (False, True):
                doc = {"profile": "fixture", "glyphs": [{"char": "A"}]}
                if not missing:
                    doc["language"] = "xx"
                if version:
                    doc["version"] = version
                self.path.write_text(json.dumps(doc))
                valid = version != "1.0.0" and not missing
                with self.subTest(version=version, missing=missing), \
                        patch("gen3tl.cli.web.Profile.by_name") as profile, \
                        patch("gen3tl.cli.web._pack_import", return_value=0) as dispatch, \
                        contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(editor_import(args), 0 if valid else 1)
                    self.assertEqual(dispatch.called, valid)
                    self.assertEqual(profile.called, valid)
                    if not valid:
                        self.assertEqual(len(output.getvalue().splitlines()), 1)
                        self.assertIn(f'version "{version or "4.0.0"}"; supported 4.0.0', output.getvalue())


if __name__ == "__main__":
    unittest.main()
