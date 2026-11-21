import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_roots import TEST_ROOT, TOOLKIT_ROOT

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from project_fixture import write_policy
from gen3tl.__main__ import main
from gen3tl.catalog.batch import approve_batch, export_batch, import_batch
from gen3tl.catalog.catalog import load_catalog
from gen3tl.web.editorimport import import_export, read_export
from gen3tl.catalog.po import PoEntry, write
from gen3tl.profile.profile import Profile


class EditorImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.po = self.root / "translation/po"
        self.batches = self.root / "translation/batches"
        self.plan = self.root / "plan.json"
        self.export = self.root / "edits.json"
        write(self.po / "maps/town.po", [
            PoEntry("approved", "Hello", "Old"), PoEntry("pending", "Goodbye"),
            PoEntry("other", "Thanks"), PoEntry("empty", "Unused", "", flags=["empty"])],
              {"Project-Id-Version": "example", "Language": "ru"})
        self.plan.write_text(json.dumps([{"name": "town-1", "kind": "translate", "ids": ["pending", "other"]}]))
        self.catalog = load_catalog(self.po)
        self.doc = {"profile": "example", "language": "ru", "pictures": [], "terms": [], "entries": [
            {"id": "approved", "translation": "New", "note": "Context checked"},
            {"id": "pending", "translation": "Draft", "note": "Another note"}]}

    def data(self):
        self.export.write_text(json.dumps(self.doc))
        return read_export(self.export, "example", "ru", "0" * 40, self.catalog)

    def run_import(self, **kwargs):
        return import_export(self.data(), self.po, self.batches, self.plan, {}, **kwargs)

    def snapshot(self):
        return {p.relative_to(self.batches).as_posix(): p.read_bytes()
                for p in self.batches.rglob("*") if p.is_file()}

    def test_routes_approved_and_pending_without_catalog_changes(self):
        before = (self.po / "maps/town.po").read_bytes()
        result = self.run_import(source="maintainer")
        align, planned = result["batches"]
        self.assertEqual((align["kind"], planned["name"]), ("align", "town-1"))
        self.assertEqual(json.loads((self.batches / "town-1/translation.json").read_text()),
                         {"pending": "Draft", "other": None})
        for batch in result["batches"]:
            directory = self.batches / batch["name"]
            status = json.loads((directory / "status.json").read_text())
            self.assertFalse(status["approved"])
            self.assertNotIn("reviews", status)
            metadata = status["editor_imports"][0]
            self.assertEqual((metadata["source"], metadata["profile"], metadata["language"]),
                             ("maintainer", "example", "ru"))
            self.assertEqual((directory / f"editor-imports/{result['sha256']}.json").read_bytes(),
                             self.export.read_bytes())
            notes = (directory / "translator-notes.md").read_text()
            self.assertIn("independent accuracy, proofreading and editorial", notes)
            self.assertIn(result["sha256"], notes)
        source = json.loads((self.batches / align["name"] / "source.json").read_text())
        self.assertEqual((source[0]["source_text"], source[0]["current"]), ("Hello", "Old"))
        self.assertEqual((self.po / "maps/town.po").read_bytes(), before)

    def test_repeated_import_preserves_review_work(self):
        first = self.run_import()
        directory = self.batches / first["batches"][0]["name"]
        (directory / "review-accuracy-1.json").write_text("[]")
        before = self.snapshot()
        self.assertTrue(all(not b["changed"] for b in self.run_import()["batches"]))
        self.assertEqual(self.snapshot(), before)

    def test_dry_run_validates_but_writes_nothing(self):
        result = self.run_import(dry_run=True)
        self.assertEqual(len(result["batches"]), 2)
        self.assertFalse(self.batches.exists())

    def test_unchanged_exports_need_no_batches_or_plan(self):
        self.plan.unlink()
        before = (self.po / "maps/town.po").read_bytes()
        for notes in ({}, {"note": ""}, {"note": "Historical review context"}):
            with self.subTest(notes=notes):
                self.doc["entries"] = [
                    {"id": sid, "translation": entry.msgstr, "state": entry.state, **notes}
                    for sid, (_, entry) in self.catalog.items()]
                self.assertEqual(self.run_import()["batches"], [])
                self.assertFalse(self.batches.exists())
                self.assertEqual((self.po / "maps/town.po").read_bytes(), before)

    def test_unchanged_entry_still_rejects_a_stale_base_without_notes(self):
        self.doc["entries"] = [{"id": "approved", "translation": "Old", "base": "Stale"}]
        with self.assertRaisesRegex(ValueError, "editor base differs"):
            self.run_import()
        self.assertFalse(self.batches.exists())

    def test_mixed_export_routes_only_text_changes_and_keeps_legacy_context(self):
        self.doc["entries"] = [{"id": "approved", "translation": "New", "note": "Review context"},
                               {"id": "pending", "translation": ""}]
        self.plan.unlink()
        [route] = self.run_import()["batches"]
        self.assertEqual((route["kind"], route["entries"]), ("align", 1))
        directory = self.batches / route["name"]
        self.assertEqual(json.loads((directory / "translation.json").read_text()), {"approved": "New"})
        self.assertIn("Review context", (directory / "translator-notes.md").read_text())

    def test_planned_noop_preserves_existing_drafts_and_rejects_stale_sources(self):
        directory = export_batch("town-1", ["pending", "other"], self.po, {}, root=self.batches)
        self.doc["entries"] = [{"id": "pending", "translation": ""}]
        before = self.snapshot()
        self.assertFalse(self.run_import()["batches"][0]["changed"])
        self.assertEqual(self.snapshot(), before)
        path = directory / "source.json"
        source = json.loads(path.read_text())
        source[0]["source_text"] = "Previous source"
        path.write_text(json.dumps(source))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "snapshot is stale"):
            self.run_import()
        self.assertEqual(self.snapshot(), before)

    def test_mixed_planned_export_does_not_fill_unchanged_drafts(self):
        self.doc["entries"] = [{"id": "pending", "translation": "Draft"},
                               {"id": "other", "translation": "", "note": "Historical context"}]
        [route] = self.run_import()["batches"]
        directory = self.batches / route["name"]
        self.assertEqual(json.loads((directory / "translation.json").read_text()),
                         {"pending": "Draft", "other": None})
        self.assertIn("Historical context", (directory / "translator-notes.md").read_text())

    def test_existing_planned_draft_and_notes_survive(self):
        directory = export_batch("town-1", ["pending", "other"], self.po, {}, root=self.batches)
        (directory / "translation.json").write_text(json.dumps({"pending": None, "other": "Existing draft"}))
        (directory / "translator-notes.md").write_text("## Existing notes\n\nKeep this.\n")
        self.run_import()
        self.assertEqual(json.loads((directory / "translation.json").read_text())["other"], "Existing draft")
        self.assertIn("Keep this.", (directory / "translator-notes.md").read_text())

    def test_conflict_refuses_every_output(self):
        directory = export_batch("town-1", ["pending", "other"], self.po, {}, root=self.batches)
        (directory / "translation.json").write_text(json.dumps({"pending": "Conflicting draft"}))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.run_import()
        self.assertEqual(self.snapshot(), before)

    def test_started_review_cannot_be_replaced(self):
        directory = export_batch("town-1", ["pending", "other"], self.po, {}, root=self.batches)
        (directory / "review-proofreading-1.json").write_text("[]")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "review has started"):
            self.run_import()
        self.assertEqual(self.snapshot(), before)

    def test_stale_batch_source_is_refused(self):
        directory = export_batch("town-1", ["pending", "other"], self.po, {}, root=self.batches)
        path = directory / "source.json"
        source = json.loads(path.read_text())
        source[0]["source_text"] = "Previous English"
        path.write_text(json.dumps(source))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "snapshot is stale"):
            self.run_import()
        self.assertEqual(self.snapshot(), before)

    def test_wrong_identity_unknown_duplicate_and_malformed_entries_fail(self):
        original = json.loads(json.dumps(self.doc))
        cases = [lambda d: d.update(profile="another"), lambda d: d.update(language="de"),
                 lambda d: d.update(rom_sha1="1" * 40), lambda d: d["entries"][0].update(id="missing"),
                 lambda d: d["entries"].append(d["entries"][0]),
                 lambda d: d["entries"][0].update(translation=None),
                 lambda d: d["entries"][0].update(note=[]), lambda d: d.update(entries=[])]
        for change in cases:
            self.doc = json.loads(json.dumps(original))
            change(self.doc)
            with self.subTest(document=self.doc), self.assertRaises(ValueError):
                self.run_import()
            self.assertFalse(self.batches.exists())

    def test_optional_editor_snapshots_detect_stale_data(self):
        self.doc["entries"][0].update(source_text="Hello", base="Old")
        self.run_import(dry_run=True)
        for key in ("source_text", "base"):
            previous = self.doc["entries"][0][key]
            self.doc["entries"][0][key] = "Stale"
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "current catalog"):
                self.run_import()
            self.doc["entries"][0][key] = previous

    def test_nontext_changes_are_not_silently_discarded(self):
        for key in ("pictures", "terms"):
            self.doc[key] = [{"id": "change"}]
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "glyphs and keyboards only"):
                self.run_import()
            self.doc[key] = []

    def test_picture_snapshots_are_checked_before_import(self):
        profile = Profile({"rom": {"sha1": "0" * 40}}, self.root / "example.toml")
        self.doc["pictures"] = [{"id": "T1", "base": "ab" * 32}]
        self.export.write_text(json.dumps(self.doc))
        with self.assertRaisesRegex(ValueError, "T1: editor base differs from the current project picture"):
            read_export(self.export, "example", "ru", profile.sha1, self.catalog, graphics_profile=profile)
        self.assertFalse(self.batches.exists())
        self.doc["pictures"][0]["base"] = None
        self.export.write_text(json.dumps(self.doc))
        with self.assertRaisesRegex(ValueError, "export contains pictures"):
            read_export(self.export, "example", "ru", profile.sha1, self.catalog, graphics_profile=profile)

    def test_a_keyboard_edit_needs_no_text_entries(self):
        edit = {"rows": {"lower_keys_1": "x"}, "base": {"rows": {"lower_keys_1": "a"}}}
        self.doc.update(entries=[], keyboard=edit)
        self.assertEqual(self.data()["document"]["keyboard"], edit)
        self.doc["keyboard"] = ["rows"]
        with self.assertRaisesRegex(ValueError, "keyboard must be an object"):
            self.data()
        del self.doc["keyboard"]
        with self.assertRaisesRegex(ValueError, "nonempty entries list, glyph edits or a keyboard"):
            self.data()

    def test_glyph_edits_need_no_text_entries(self):
        self.doc["entries"] = []
        self.doc["glyphs"] = [{"style": "main", "char": "Б"}]
        self.assertEqual(self.data()["document"]["glyphs"], self.doc["glyphs"])   # editorfonts checks them
        self.doc["glyphs"] = {"style": "main"}
        with self.assertRaisesRegex(ValueError, "glyphs must be a list"):
            self.data()
        self.doc["glyphs"] = []
        with self.assertRaisesRegex(ValueError, "nonempty entries list, glyph edits or a keyboard"):
            self.data()

    def test_duplicate_json_fields_are_rejected(self):
        self.export.write_text('{"profile":"example","profile":"another"}')
        with self.assertRaisesRegex(ValueError, "duplicate JSON field"):
            read_export(self.export, "example", "ru", "0" * 40, self.catalog)

    def test_catalog_change_after_validation_is_rejected(self):
        data = self.data()
        write(self.po / "maps/town.po", [PoEntry("approved", "Hello", "Changed")], {})
        with self.assertRaisesRegex(ValueError, "changed after export validation"):
            import_export(data, self.po, self.batches, self.plan, {})
        self.assertFalse(self.batches.exists())

    def test_existing_batch_symlinks_are_rejected(self):
        directory = export_batch("town-1", ["pending", "other"], self.po, {}, root=self.batches)
        outside = self.root / "outside.json"
        outside.write_text("{}")
        (directory / "translation.json").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "symbolic links"):
            self.run_import()
        self.assertEqual(outside.read_text(), "{}")

    def test_review_created_during_staging_survives(self):
        directory = export_batch("town-1", ["pending", "other"], self.po, {}, root=self.batches)
        from gen3tl.web import editorimport
        prepare = editorimport._prepare

        def reviewed(destination, *args):
            result = prepare(destination, *args)
            if destination.name == "town-1":
                (destination / "review-proofreading-1.json").write_text("[]")
            return result

        with patch("gen3tl.web.editorimport._prepare", side_effect=reviewed):
            with self.assertRaisesRegex(ValueError, "changed during import"):
                self.run_import()
        self.assertEqual((directory / "review-proofreading-1.json").read_text(), "[]")
        self.assertFalse((directory / "translation.json").exists())
        self.assertEqual([p.name for p in self.batches.iterdir()], ["town-1"])

    def test_draft_changed_during_backup_move_survives(self):
        directory = export_batch("town-1", ["pending", "other"], self.po, {}, root=self.batches)
        from gen3tl.web.editorimport import os
        replace = os.replace

        def changed(source, destination):
            result = replace(source, destination)
            if Path(destination).parent.name == ".backup":
                (Path(destination) / "translation.json").write_text('{"pending":"Concurrent draft"}')
            return result

        with patch("gen3tl.web.editorimport.os.replace", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "changed during publication"):
                self.run_import()
        self.assertEqual(json.loads((directory / "translation.json").read_text()),
                         {"pending": "Concurrent draft"})
        self.assertEqual([p.name for p in self.batches.iterdir()], ["town-1"])

    def test_backup_is_a_valid_planned_batch_name(self):
        plan = json.loads(self.plan.read_text())
        plan[0]["name"] = "backup"
        self.plan.write_text(json.dumps(plan))
        result = self.run_import()
        self.assertEqual(result["batches"][1]["name"], "backup")
        self.assertTrue((self.batches / "backup/translation.json").exists())

    def test_unplanned_deferred_and_unsafe_routes_fail(self):
        for plan in ([], [{"name": "town-1", "kind": "deferred", "ids": ["pending"]}],
                     [{"name": "../escape", "kind": "translate", "ids": ["pending"]}]):
            self.plan.write_text(json.dumps(plan))
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                self.run_import()
            self.assertFalse(self.batches.exists())

    def test_empty_approved_translation_uses_alignment(self):
        self.doc["entries"] = [{"id": "empty", "translation": "Filled", "note": "Review context"}]
        result = self.run_import()
        self.assertEqual(result["batches"][0]["kind"], "align")

    def test_blank_to_off_survives_import_and_batch_approval(self):
        self.doc["entries"] = [{"id": "pending", "translation": "", "state": "empty",
                                "base": "", "base_state": "original"}]
        self.plan.write_text(json.dumps([{"name": "town-1", "kind": "translate", "ids": ["pending"]}]))
        self.run_import()
        self.assertEqual(import_batch("town-1", self.po, self.batches)["changed"], 1)
        entry = load_catalog(self.po)["pending"][1]
        self.assertEqual((entry.msgstr, entry.state, entry.fuzzy), ("", "empty", True))
        for review in ("accuracy", "proofreading", "editorial"):
            (self.batches / "town-1" / f"review-{review}-1.json").write_text("[]")
        self.assertEqual(approve_batch("town-1", self.po, self.batches)["cleared_fuzzy"], 1)
        entry = load_catalog(self.po)["pending"][1]
        self.assertEqual((entry.state, entry.fuzzy), ("empty", False))

    def test_grouped_rule_export_keeps_only_changed_states_without_note_snapshots(self):
        self.plan.write_text(json.dumps([{"name": "town-1", "kind": "translate", "ids": ["pending"]}]))
        self.doc["entries"] = [{"id": "empty", "translation": "", "state": "empty",
                                "base": "", "base_state": "empty"},
                               {"id": "pending", "translation": "", "state": "empty",
                                "base": "", "base_state": "original"}]
        [route] = self.run_import()["batches"]
        self.assertEqual((route["name"], route["entries"]), ("town-1", 1))
        directory = self.batches / route["name"]
        self.assertEqual(json.loads((directory / "translation.json").read_text()),
                         {"pending": ""})
        self.assertEqual(import_batch(route["name"], self.po, self.batches)["changed"], 1)
        current = load_catalog(self.po)
        self.assertEqual((current["empty"][1].state, current["empty"][1].fuzzy), ("empty", False))
        self.assertEqual((current["pending"][1].state, current["pending"][1].fuzzy), ("empty", True))

    def test_off_to_original_survives_import_and_approval(self):
        self.doc["entries"] = [{"id": "empty", "translation": "", "state": "original",
                                "base": "", "base_state": "empty"}]
        [batch] = self.run_import()["batches"]
        directory = self.batches / batch["name"]
        self.assertEqual(json.loads((directory / "source.json").read_text())[0]["current_state"], "empty")
        self.assertEqual(import_batch(batch["name"], self.po, self.batches)["changed"], 1)
        entry = load_catalog(self.po)["empty"][1]
        self.assertEqual((entry.msgstr, entry.state, entry.fuzzy), ("", "original", True))
        for review in ("accuracy", "proofreading", "editorial"):
            (directory / f"review-{review}-1.json").write_text("[]")
        self.assertEqual(approve_batch(batch["name"], self.po, self.batches)["cleared_fuzzy"], 1)

    def test_legacy_blank_does_not_create_intentional_empty(self):
        self.doc["entries"] = [{"id": "approved", "translation": ""}]
        [batch] = self.run_import()["batches"]
        import_batch(batch["name"], self.po, self.batches)
        self.assertEqual(load_catalog(self.po)["approved"][1].state, "original")

    def test_state_snapshots_reject_stale_and_invalid_entries(self):
        cases = [{"translation": "", "state": "off"}, {"translation": "x", "state": "empty"},
                 {"translation": "", "state": "text"}, {"translation": "", "state": None},
                 {"translation": "", "base": "", "base_state": "text"},
                 {"translation": "", "base": "", "base_state": "original"},
                 {"translation": "", "base_state": "empty"}]
        for change in cases:
            self.doc["entries"] = [{"id": "empty", **change}]
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.run_import()
            self.assertFalse(self.batches.exists())

    def test_flag_change_after_validation_is_rejected(self):
        self.doc["entries"] = [{"id": "empty", "translation": "x", "state": "text",
                                "base": "", "base_state": "empty"}]
        data = self.data()
        write(self.po / "maps/town.po", [PoEntry("empty", "Unused")], {})
        with self.assertRaisesRegex(ValueError, "changed after export validation"):
            import_export(data, self.po, self.batches, self.plan, {})

    def test_existing_blank_draft_conflicts_with_original_state(self):
        directory = export_batch("town-1", ["pending", "other"], self.po, {}, root=self.batches)
        (directory / "translation.json").write_text(json.dumps({"pending": ""}))
        self.doc["entries"] = [{"id": "pending", "translation": "", "state": "original"}]
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.run_import()

    def test_existing_batch_detects_state_only_source_changes(self):
        export_batch("town-1", ["pending", "other"], self.po, {}, root=self.batches)
        entries = [entry for _, entry in self.catalog.values()]
        self.catalog["pending"][1].flags.append("empty")
        self.catalog["pending"][1].set_fuzzy(True)
        write(self.po / "maps/town.po", entries, {})
        with self.assertRaisesRegex(ValueError, "snapshot is stale"):
            self.run_import()

    def test_publish_failure_restores_all_existing_files(self):
        directory = export_batch("town-1", ["pending", "other"], self.po, {}, root=self.batches)
        before = self.snapshot()
        from gen3tl.web.editorimport import os
        replace = os.replace

        def fail_new_plan(source, destination):
            if Path(source).name == "town-1" and Path(source).parent.name.startswith(".editor-import-"):
                raise OSError("simulated publication failure")
            return replace(source, destination)

        with patch("gen3tl.web.editorimport.os.replace", side_effect=fail_new_plan):
            with self.assertRaisesRegex(OSError, "publication failure"):
                self.run_import()
        self.assertEqual(self.snapshot(), before)

    def test_failed_rollback_keeps_original_backup(self):
        directory = export_batch("town-1", ["pending", "other"], self.po, {}, root=self.batches)
        original = (directory / "source.json").read_bytes()
        from gen3tl.web.editorimport import os
        replace = os.replace

        def fail_install_and_restore(source, destination):
            if Path(destination).name == "town-1" and Path(destination).parent == self.batches.resolve():
                raise OSError("simulated destination failure")
            return replace(source, destination)

        with patch("gen3tl.web.editorimport.os.replace", side_effect=fail_install_and_restore), \
                patch.object(Path, "rename", side_effect=OSError("simulated recovery rename failure")):
            with self.assertRaisesRegex(OSError, "recovery files retained"):
                self.run_import()
        backups = list(self.batches.parent.glob(".editor-import-*/.backup/town-1/source.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)

    def test_interrupted_publication_restores_originals(self):
        export_batch("town-1", ["pending", "other"], self.po, {}, root=self.batches)
        before = self.snapshot()
        from gen3tl.web.editorimport import os
        replace = os.replace

        def interrupt_install(source, destination):
            if Path(source).name == "town-1" and Path(source).parent.name.startswith(".editor-import-"):
                raise KeyboardInterrupt()
            return replace(source, destination)

        with patch("gen3tl.web.editorimport.os.replace", side_effect=interrupt_install):
            with self.assertRaises(KeyboardInterrupt):
                self.run_import()
        self.assertEqual(self.snapshot(), before)

    def test_review_started_during_publication_is_never_deleted(self):
        directory = export_batch("town-1", ["pending", "other"], self.po, {}, root=self.batches)
        from gen3tl.web.editorimport import os
        replace = os.replace

        def reviewed(source, destination):
            result = replace(source, destination)
            if Path(destination).name.startswith("align-editor-"):
                (Path(destination) / "review-accuracy-1.json").write_text("[]")
                (directory / "review-proofreading-1.json").write_text("[]")
            return result

        with patch("gen3tl.web.editorimport.os.replace", side_effect=reviewed):
            with self.assertRaisesRegex(OSError, "recovery files retained"):
                self.run_import()
        self.assertEqual((directory / "review-proofreading-1.json").read_text(), "[]")
        reviews = list(self.batches.glob("align-editor-*/review-accuracy-1.json"))
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0].read_text(), "[]")

    def test_cli_resolves_selected_project_before_import(self):
        profile_path = self.root / "example.toml"
        profile_path.write_text('[rom]\nsha1="' + "0" * 40 + '"\n')
        profile = Profile.load(profile_path)
        write_policy(self.root, profile)
        (self.root / "projects.toml").write_text(
            '[defaults]\nprofile="example"\nlanguage="ru"\n'
            '[[project]]\nprofile="example"\nlanguage="ru"\nroot="."\n'
            'policy="policy.toml"\n')
        self.data()
        args = ["--project-root", str(self.root), "--profile", str(profile_path),
                "web", "import", str(self.export), "--plan", str(self.plan), "--source", "maintainer"]
        with patch("gen3tl.cli.web._limits_and_matcher", return_value=({}, None)):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 0)
        self.assertTrue((self.batches / "town-1/translation.json").exists())
        self.doc["language"] = "de"
        self.export.write_text(json.dumps(self.doc))
        before = self.snapshot()
        with patch("gen3tl.cli.web._limits_and_matcher") as limits:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 1)
            limits.assert_not_called()
        self.assertEqual(self.snapshot(), before)


class SiteImportTest(unittest.TestCase):
    """An export of the site project applies to its catalog: dry run, apply, refusals."""
    FIXTURE = TEST_ROOT / "fixtures" / "site-export.json"

    def setUp(self):
        from gen3tl.web import sitecatalog
        from project_fixture import workspace_project
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.i18n = self.root / "web" / "i18n"
        self.po = self.i18n / "xx.po"
        write(self.po, [
            PoEntry("site:Patch", "Patch", "Патч", extracted=["index.html"]),
            PoEntry("site:Loading {game}…", "Loading {game}…", "", extracted=["js/pick.js"]),
            PoEntry("site:Home", "Home", "Дом", extracted=["index.html, guide.html"],
                    translator=["source: a sample wording"])],
              {"Project-Id-Version": "site", "Language": "xx", "Content-Type": "text/plain; charset=UTF-8"})
        self.profile = workspace_project(self.root)
        hook = patch.object(sitecatalog, "CATALOGS", self.i18n)
        hook.start()
        self.addCleanup(hook.stop)
        self.sitecatalog = sitecatalog

    def run_import(self, export, *options):
        args = ["--project-root", str(self.root), "--profile", str(self.profile), "web", "import", str(export),
                *options]
        with patch("gen3tl.cli.web._limits_and_matcher", side_effect=AssertionError("a game import")), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            status = main(args)
        return status, out.getvalue()

    def test_site_notes_remain_context_and_omission_never_changes_text(self):
        before = self.po.read_bytes()
        home = next(entry.msgstr for entry in self.sitecatalog.load(self.po) if entry.msgctxt == "site:Home")
        for context in ({}, {"note": "Historical context"}):
            with self.subTest(context=context):
                doc = {"profile": "site", "language": "xx", "entries": [
                    {"id": "site:Home", "translation": home, "base": home, **context}]}
                result = self.sitecatalog.apply_export(doc, self.i18n)
                self.assertEqual((result.changed, result.unchanged, result.notes), ([], 1, int(bool(context))))
                self.assertEqual(self.po.read_bytes(), before)
                doc["entries"][0]["base"] = "Stale"
                with self.assertRaisesRegex(ValueError, "editor base differs"):
                    self.sitecatalog.apply_export(doc, self.i18n)
                self.assertEqual(self.po.read_bytes(), before)

    def test_dry_run_plans_and_apply_writes_the_catalog_as_the_sync_keeps_it(self):
        before = self.po.read_bytes()
        status, out = self.run_import(self.FIXTURE, "--dry-run")
        self.assertEqual(status, 0, out)
        self.assertIn(f"catalog      {self.po}\nstatus       planned\nchanged      2 entries\n"
                      "unchanged    1 entries\nnotes        1 entries kept as in the catalog\nreview       required\n", out)
        self.assertEqual(self.po.read_bytes(), before)
        status, out = self.run_import(self.FIXTURE)
        self.assertEqual(status, 0, out)
        self.assertIn("status       applied\nchanged      2 entries\n", out)
        entries = {e.msgid: e for e in self.sitecatalog.load(self.po)}
        self.assertEqual([(key, e.msgstr, e.flags) for key, e in entries.items()],
                         [("Patch", "Патч", []), ("Loading {game}…", "Загрузка {game}…", ["fuzzy"]),
                          ("Home", "Главная", [])])
        self.assertEqual(entries["Home"].source_note, "a sample wording", "a note is not applied")
        self.assertEqual(entries["Home"].extracted, ["index.html, guide.html"])
        applied = self.po.read_bytes()
        web = self.root / "web"
        (web / "index.html").write_text('<a data-ui="Patch">Patch</a><a data-ui="Home">Home</a>', encoding="utf-8")
        (web / "guide.html").write_text('<header class="mast"><a data-ui="Home">Home</a></header>', encoding="utf-8")
        (web / "js").mkdir()
        (web / "js" / "pick.js").write_text('t("Loading {game}…", { game });\n', encoding="utf-8")
        [synced] = self.sitecatalog.sync(self.i18n, web)
        self.assertEqual((synced.added, synced.dropped), ([], []))
        self.assertEqual(self.po.read_bytes(), applied, "the sync keeps what the import wrote")
        status, out = self.run_import(self.FIXTURE)
        self.assertIn("status       unchanged\nchanged      0 entries\nunchanged    3 entries\n", out)
        self.assertEqual(self.po.read_bytes(), applied)

    def test_refusals_leave_the_catalog(self):
        before = self.po.read_bytes()
        fixture = json.loads(self.FIXTURE.read_text(encoding="utf-8"))
        export = self.root / "site.json"

        def entry(**changes):
            return {**fixture, **changes}

        binding = {"kind": "entry", "id": "site:Home", "source_sha256": "a" * 64}
        note = {"id": "note:one", "subject": {"kind": "entry", "id": "site:Home"}, "binding": binding,
                "scope": {"kind": "language", "language": "xx"}, "guidance": ["Keep context."],
                "status": "proposed"}
        proposal = {"operation": "add", "id": note["id"], "subject": note["subject"], "scope": note["scope"],
                    "binding": binding, "base": None, "base_revision": "null", "proposed": note}
        cases = (
            (entry(language="yy"), "no site catalog for 'yy'"),
            (entry(entries=[{"id": "site:Gone", "translation": "x"}]), "site:Gone: not in the site catalog xx.po"),
            (entry(entries=[{"id": "site:Home", "base": "Старое", "translation": "x"}]),
             "site:Home: editor base differs from the current catalog"),
            (entry(entries=[{"id": "site:Loading {game}…", "translation": "Загрузка…"}]),
             "must keep the holes of its key ({game})"),
            (entry(entries=[{"id": "site:Home", "translation": "{home}"}]), "keep the holes of its key (no hole)"),
            (entry(entries=[{"id": "site:Home", "translation": ""}]), "an empty translation"),
            (entry(entries=[{"id": "site:Home", "translation": "a\u0007b"}]), "unsupported control character"),
            (entry(entries=[{"id": "site:Home", "translation": "a"}, {"id": "site:Home", "translation": "b"}]),
             "duplicate editor entry: site:Home"),
            (entry(entries=[]), "nonempty entries list"),
            (entry(pictures=[{"id": "G01"}]), "holds pictures; the site catalog takes text entries only"),
            (entry(note_changes_schema="3.0.0", note_changes=[proposal]),
             "holds note_changes; the site catalog takes text entries only"),
            (entry(keyboard={"rows": {}}), "holds keyboard"),
        )
        for doc, reason in cases:
            with self.subTest(reason=reason):
                export.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
                status, out = self.run_import(export)
                self.assertEqual(status, 1, out)
                self.assertIn(reason, out)
                self.assertEqual(self.po.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
