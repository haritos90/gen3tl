from test_roots import TOOLKIT_ROOT
"""Notechanges legacy checks and fixtures."""

import sys
from pathlib import Path
import json
import unittest
from copy import deepcopy

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.catalog.catalog import load_catalog
from gen3tl.catalog.source import digest
from gen3tl.core import notes
from gen3tl.web import notechanges
import notechanges_support
from notechanges_support import change


class NoteSubmissionTest(notechanges_support.NoteSubmissionTest, unittest.TestCase):
    def test_legacy_replacement_loads_exports_and_validates(self):
        from gen3tl.catalog import guidance
        from gen3tl.web.editordata import attach_notes

        proposal = self.legacy_proposal()
        self.export([proposal], note_changes_schema=notechanges.LEGACY_SCHEMA)
        before = self.project.notes.read_bytes()
        self.apply(check=True)
        self.assertEqual(self.project.notes.read_bytes(), before)
        result = self.apply()
        rows = guidance.read_notes(self.project.notes)
        self.assertEqual(rows, [proposal["proposed"]])
        self.assertNotIn("author", rows[0])
        notes.validate({"schema": notes.SCHEMA, "note": rows})
        with self.project.activate(self.prof):
            catalog = load_catalog(self.project.catalog, english=False)
            doc = {"language": "xx", "entries": [{"id": "key"}, {"id": "legacy"}]}
            attach_notes(doc, self.prof, catalog, folder=self.project.translation,
                         digests={"key": digest("Example."), "legacy": digest("Legacy.")})
        self.assertEqual(doc["entries"][0]["note_ids"], [rows[0]["id"]])
        self.assertEqual(doc["notes"]["note"], rows)
        notes.validate(doc["notes"])
        self.assertIn("Legacy guidance.", notechanges.review(result.note_changes)[0])
        self.assertIn("Recorded location.", notechanges.review(result.note_changes)[0])
        code, report = self.check([self.project.notes], {self.project.notes: before})
        self.assertEqual(code, 0, report)
        self.assertIn("Legacy guidance.", report)
        self.export([change({**rows[0], "guidance": ["Updated guidance."]}, rows[0])])
        self.apply()
        self.assertEqual(guidance.read_notes(self.project.notes)[0]["guidance"], ["Updated guidance."])
        self.assertEqual((self.po.read_bytes(), self.project.policy.read_bytes()), self.original)

    def test_legacy_retirement_keeps_other_records(self):
        from gen3tl.catalog import guidance

        proposal = self.legacy_proposal("remove")
        other = {**proposal["base"], "id": "legacy", "source_sha256": digest("Legacy.")}
        guidance.write_notes(self.project.notes, [proposal["base"], other])
        self.export([proposal], note_changes_schema=notechanges.LEGACY_SCHEMA)
        result = self.apply()
        self.assertEqual(guidance.read_notes(self.project.notes), [other])
        self.assertIn("removal", notechanges.review(result.note_changes)[0])
        self.assertEqual((self.po.read_bytes(), self.project.policy.read_bytes()), self.original)

    def test_invalid_legacy_changes_write_nothing(self):
        proposal = self.legacy_proposal()
        before = self.project.notes.read_bytes()
        stale = deepcopy(proposal)
        stale["base"]["text"] = "Stale guidance."
        stale["base_revision"] = json.dumps(stale["base"])
        malformed = deepcopy(proposal)
        malformed["proposed"]["guidance"] = []
        uncited = deepcopy(proposal)
        del uncited["proposed"]["sources"][0]["locator"]
        other = deepcopy(proposal)
        for record in (other, other["proposed"]):
            record["subject"]["id"] = "legacy"
            record["binding"].update(id="legacy", source_sha256=digest("Legacy."))
        duplicate = deepcopy(proposal)
        duplicate["id"] = duplicate["proposed"]["id"] = "note:other"
        bad_base = deepcopy(proposal)
        bad_base["base"]["status"] = "unreviewed"
        for changes, reason in (([stale], "stale note base"), ([malformed], "nonempty array"),
                                ([uncited], "ROM or build reference"), ([other], "another entry"),
                                ([proposal, duplicate], "duplicate note base"), ([bad_base], "expected id")):
            with self.subTest(reason=reason):
                self.export(changes, [{"id": "key", "base": "Old", "translation": "New"}],
                            note_changes_schema=notechanges.LEGACY_SCHEMA)
                with self.assertRaisesRegex(ValueError, reason):
                    self.apply()
                self.assertEqual(self.project.notes.read_bytes(), before)
                self.assertEqual((self.po.read_bytes(), self.project.policy.read_bytes()), self.original)

    def test_legacy_replacement_requires_new_schema_and_independent_identity(self):
        proposal = self.legacy_proposal()
        self.export([proposal])
        with self.assertRaisesRegex(ValueError, "missing fields"):
            self.apply()
        proposal["id"] = proposal["proposed"]["id"] = proposal["base"]["id"]
        self.export([proposal], note_changes_schema=notechanges.LEGACY_SCHEMA)
        with self.assertRaisesRegex(ValueError, "independent note identity"):
            self.apply()

    def test_legacy_replacement_refuses_an_existing_note_identity(self):
        proposal = self.legacy_proposal()
        self.published()
        before = self.project.notes.read_bytes(), self.entries.read_bytes()
        self.export([proposal], note_changes_schema=notechanges.LEGACY_SCHEMA)
        with self.assertRaisesRegex(ValueError, "replacement note identity already exists"):
            self.apply()
        self.assertEqual((self.project.notes.read_bytes(), self.entries.read_bytes()), before)

    def test_legacy_replacement_can_move_to_language_notes(self):
        from gen3tl.catalog import guidance

        proposal = self.legacy_proposal()
        proposal["scope"] = proposal["proposed"]["scope"] = {"kind": "language", "language": "xx"}
        self.export([proposal], note_changes_schema=notechanges.LEGACY_SCHEMA)
        self.apply()
        self.assertEqual(guidance.read_notes(self.project.notes), [])
        self.assertEqual(self.stored()[proposal["id"]], proposal["proposed"])
