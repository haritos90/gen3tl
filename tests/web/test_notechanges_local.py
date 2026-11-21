from test_roots import TOOLKIT_ROOT
"""Notechanges local checks and fixtures."""

import sys
from pathlib import Path
import json
import unittest
from unittest.mock import patch

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.catalog.catalog import load_catalog
from gen3tl.catalog.po import read
from gen3tl.catalog.source import digest
from gen3tl.core import notes
from gen3tl.project import Registry
from gen3tl.web import editorimport, editorsave
import notechanges_support
from notechanges_support import change


def notechanges_envelopes(project) -> list[Path]:
    """Files of the dropped proposal envelope directory."""
    return sorted((project.translation / "notes" / "proposals").glob("*"))


class NoteSubmissionTest(notechanges_support.NoteSubmissionTest, unittest.TestCase):
    def test_local_save_applies_note_only_changes_and_refreshes_the_view(self):
        store, editor = self.local_store()
        before = self.po.read_bytes()
        result = store.save(self.local_document([change(self.note)]))
        self.assertEqual((result["saved"], result["notes"]), (0, 1))
        self.assertEqual(self.po.read_bytes(), before)
        record = self.stored()["note:example"]
        self.assertEqual((record["author"], record["status"]), (editorsave.LOCAL_AUTHOR, "proposed"))
        self.assertNotIn("previous", json.loads((editor / "index.json").read_text()))
        self.assertFalse(notechanges_envelopes(self.project))
        with self.assertRaisesRegex(ValueError, "stale note base"):
            store.save(self.local_document([change(self.note)]))

    def test_local_save_rolls_back_notes_with_a_failed_publication(self):
        store, _ = self.local_store()
        text = [{"id": "key", "source_text": "Example.", "base": "Old", "translation": "New"}]
        result = store.save(self.local_document([change(self.note)], text))
        self.assertEqual((result["saved"], result["notes"]), (1, 1))
        entry = read(self.po)[1][0]
        self.assertEqual((entry.msgstr, entry.fuzzy), ("New", False))   # approved before the save
        notes_after = self.entries.read_bytes()
        failing = self.local_document([change({**self.note, "id": "note:other"})],
                                      [{"id": "key", "source_text": "Example.", "base": "New", "translation": "Other"}])
        replace = editorsave.os.replace

        def fail_view(source, destination):
            if Path(source).name == "view" and Path(source).parent.name.startswith(".editor-save-"):
                raise OSError("publication failed")
            return replace(source, destination)

        with patch.object(editorsave.os, "replace", side_effect=fail_view), self.assertRaises(OSError):
            store.save(failing)
        self.assertEqual(read(self.po)[1][0].msgstr, "New")
        self.assertEqual(self.entries.read_bytes(), notes_after)

    def test_local_save_refuses_invalid_notes_before_any_write(self):
        store, editor = self.local_store()
        stale = {**self.note, "binding": {**self.note["binding"], "source_sha256": "0" * 64}}
        legacy = self.note_for("entry", "legacy", binding={"kind": "entry", "id": "legacy",
                                                           "source_sha256": digest("Legacy.")})
        text = [{"id": "key", "source_text": "Example.", "base": "Old", "translation": "New"}]
        for note, reason in ((stale, "stale note binding"), (legacy, "legacy note prose"),
                             ({**self.note, "guidance": ["a\rb"]}, "control character")):
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                store.save(self.local_document([change(note)], text))
        self.assertEqual(self.po.read_bytes(), self.original[0])
        self.assertFalse(self.entries.exists())
        self.assertEqual((editor / "index.json").read_text(), '{"previous":true}')

    def test_local_import_applies_note_only_export(self):
        self.export([change(self.note)])
        status, out = self.local_import("--dry-run")
        self.assertEqual(status, 0, out)
        self.assertIn("1 changes, status proposed, planned", out)
        self.assertFalse(self.entries.exists())
        status, out = self.local_import("--source", "contributor")
        self.assertEqual(status, 0, out)
        self.assertIn(self.entries.relative_to(self.root).as_posix(), out)
        self.assertEqual(self.stored()["note:example"]["author"], "contributor")
        self.assertEqual((self.po.read_bytes(), self.project.policy.read_bytes()), self.original)
        self.assertFalse(notechanges_envelopes(self.project))
        before = self.entries.read_bytes()
        status, out = self.local_import("--source", "maintainer")
        self.assertEqual(status, 1, out)
        self.assertIn("stale note base", out)
        self.assertEqual(self.entries.read_bytes(), before)

    def test_local_import_applies_edits_and_removals(self):
        base, _ = self.published()
        proposed = {**base, "status": "proposed", "guidance": ["Updated guidance."]}
        self.export([change(proposed, base)])
        status, out = self.local_import()
        self.assertEqual(status, 0, out)
        self.assertEqual(self.stored()["note:example"], proposed)
        self.export([change(proposed, proposed, "remove")])
        status, out = self.local_import()
        self.assertEqual(status, 0, out)
        self.assertEqual(self.stored(), {})
        self.assertEqual(self.po.read_bytes(), self.original[0])

    def test_local_mixed_import_applies_notes_and_seeds_text_review(self):
        self.export([change(self.note)], [{"id": "key", "translation": "New", "note": "Legacy context"}])
        status, out = self.local_import()
        self.assertEqual(status, 0, out)
        self.assertIn("batch", out)
        self.assertIn("1 changes, status proposed", out)
        batches = list(self.project.batches.glob("align-editor-*/translation.json"))
        self.assertEqual(len(batches), 1)
        self.assertEqual(json.loads(batches[0].read_text()), {"key": "New"})
        self.assertIn("Legacy context", (batches[0].parent / "translator-notes.md").read_text())
        self.assertIn("note:example", self.stored())
        self.assertEqual((self.po.read_bytes(), self.project.policy.read_bytes()), self.original)

    def test_local_import_rejects_invalid_notes_before_text_writes(self):
        stale = {**self.note, "binding": {**self.note["binding"], "source_sha256": "0" * 64}}
        for note, reason in ((stale, "stale note binding"), ({**self.note, "id": "note:a b"}, "letters, digits")):
            with self.subTest(reason=reason):
                self.export([change(note)], [{"id": "key", "translation": "New"}])
                status, out = self.local_import()
                self.assertEqual(status, 1, out)
                self.assertIn(reason, out)
        self.path.write_text('{"profile": "sample", "language": "xx", "entries": [], "x": ' + "[" * 200000
                             + "]" * 200000 + "}")
        status, out = self.local_import()
        self.assertEqual(status, 1, out)
        self.assertIn("nests too deeply", out)
        self.assertFalse(self.project.batches.exists())
        self.assertFalse(self.entries.exists())
        self.assertEqual((self.po.read_bytes(), self.project.policy.read_bytes()), self.original)

    def test_import_and_site_routes_cannot_discard_note_payloads(self):
        self.export([change(self.note)], [{"id": "key", "translation": "New"}])
        with self.project.activate(self.prof), self.assertRaisesRegex(ValueError, "submission review path"):
            editorimport.read_export(self.path, "sample", "xx", self.raw.sha1,
                                     load_catalog(self.project.catalog, english=False))
        self.export([change(self.note)], profile="site")
        with self.assertRaisesRegex(ValueError, "site submission"):
            self.apply()

    def test_note_identity_cannot_collide_with_another_language(self):
        from gen3tl.project.registry import Project

        peer = Project("sample", "yy", self.root, self.root, game=True)
        other = {**self.note, "scope": {"kind": "language", "language": "yy"}}
        notes.write_notes(peer.translation / "notes" / "entries.toml", {"schema": notes.SCHEMA, "note": [other]})
        self.export([change(self.note)])
        with patch.object(Registry, "load", return_value=type("Peers", (), {"projects": [self.project, peer]})()), \
                self.assertRaisesRegex(ValueError, "another language"):
            self.apply()
