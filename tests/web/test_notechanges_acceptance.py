from test_roots import TOOLKIT_ROOT
"""Notechanges acceptance checks and fixtures."""

import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from project_fixture import literal, write_policy
from gen3tl.web import snapshotstore as snapshot_store
from gen3tl.catalog.catalog import load_catalog
from gen3tl.catalog.po import read, write
from gen3tl.catalog.source import digest
from gen3tl.check import prcheck
from gen3tl.core import notes
from gen3tl.profile.profile import Profile
from gen3tl.web import notechanges
import notechanges_support
from notechanges_support import change


class NoteSubmissionTest(notechanges_support.NoteSubmissionTest, unittest.TestCase):
    def test_notes_and_engine_rules_share_one_validated_submission(self):
        from test_policyrules import fixture, proposal
        from gen3tl.web.editorrules import rules_data

        self.raw = Profile(fixture(), self.raw.path)
        self.raw.data.pop("selected_code_replacements")
        self.raw.path.write_text("\n".join(f"{key} = {literal(value)}" for key, value in self.raw.data.items()))
        write_policy(self.root, self.raw, "xx", code_replacements={"units": {"unit": "m"}})
        self.prof = self.project.effective(self.raw)
        rule = proposal(rules_data(self.prof, [])["rules"][1], {"enabled": True})
        self.export([change(self.note)], engine_rules=[rule])
        result = self.apply()
        self.assertEqual(len(result.engine_rules), 1)
        self.assertEqual(len(result.note_changes), 1)
        self.assertIn(self.project.policy.relative_to(self.root), result.files)
        self.assertEqual(self.stored()["note:example"]["guidance"], self.note["guidance"])
        self.assertEqual(self.po.read_bytes(), self.original[0])

    def test_pull_request_check_validates_the_applied_note_files(self):
        self.export([change(self.note)])
        self.apply()
        with patch.object(prcheck, "_project_checks", side_effect=AssertionError("No game inputs changed")):
            code, report = self.check([self.entries])
            self.assertEqual(code, 0, report)
            self.assertIn("1 note changes validated; review required", report)
            for expected in ("## Note changes", "| `note:example` | `add` | `entry/key` | `language: xx` | `proposed` |",
                             "| guidance | `no current note` | `[\"Preserve the context.\"]` |",
                             "does not authenticate it"):
                self.assertIn(expected, report)
            snapshot_store.write(self.project.snapshot, {"source": {"key": [digest("Changed.")],
                                                                    "legacy": [digest("Legacy.")]}})
            code, report = self.check([self.entries])
            self.assertEqual(code, 1)
            self.assertIn("stale note binding", report)

    def test_reviewer_acceptance_passes_the_check_and_becomes_guidance(self):
        term = self.containers()[0]
        self.export([change(self.note), change(term)])
        self.apply()
        entries, terms = self.entries, self.project.translation / "notes" / "terms.toml"
        accepted = {"status": "accepted", "reviewed_by": "reviewer", "reviewed_at": "2026-01-03"}
        record = {**self.stored()["note:example"], **accepted}
        notes.write_notes(entries, {"schema": notes.SCHEMA, "note": [record]})
        unverified = {**self.stored(terms)[term["id"]], **accepted}
        notes.write_notes(terms, {"schema": notes.SCHEMA, "note": [{**unverified, "guidance": ["Pending."],
                                                                     "status": "proposed"}]})
        terms.write_text(terms.read_text().replace('status = "proposed"', 'status = "accepted"'))
        code, report = self.check([entries, terms])
        self.assertEqual(code, 1)
        self.assertIn("accepted term guidance requires a source", report)
        checked = {**unverified, "sources": [{**source, "verification": "checked"} for source in unverified["sources"]]}
        notes.write_notes(terms, {"schema": notes.SCHEMA, "note": [checked]})
        code, report = self.check([entries, terms])
        self.assertEqual(code, 0, report)
        self.assertIn("| status | `no current note` | `accepted` |", report)
        self.assertIn('`--checked-source NOTE INDEX`', report)
        with self.project.activate(self.prof):
            self.assertIn("Preserve the context.", load_catalog(self.project.catalog, english=False)["key"][1].source_note)
            from gen3tl.lang import termbase
            self.assertIn("Preserve the context.", termbase.load(self.project.glossary)[0].notes)
        header, rows = read(self.po)
        write(self.po, rows, header)
        self.assertIn("# source: Preserve the context.", self.po.read_text())

    def test_pull_request_check_marks_hand_written_attribution(self):
        forged = {**self.note, "author": "maintainer", "created": "2026-01-01T00:00:00Z",
                  "status": "accepted", "reviewed_by": "maintainer", "reviewed_at": "2026-01-01"}
        notes.write_notes(self.entries, {"schema": notes.SCHEMA, "note": [forged]})
        code, report = self.check([self.entries])
        self.assertEqual(code, 0, report)
        self.assertIn("this check does not authenticate it", report)
        self.assertIn("| author | `no current note` | `maintainer` |", report)
        self.assertNotIn("Submitted by", report)

    def test_pull_request_check_refuses_dangling_references(self):
        base, _ = self.published()
        related = {**self.note, "id": "note:related", "related_notes": ["note:example"], "status": "accepted"}
        notes.write_notes(self.entries, {"schema": notes.SCHEMA, "note": [base, related]})
        before = {self.entries: self.entries.read_bytes()}
        notes.write_notes(self.entries, {"schema": notes.SCHEMA, "note": [related]})
        code, report = self.check([self.entries], before)
        self.assertEqual(code, 1)
        self.assertIn("relates to removed note", report)
        notes.write_notes(self.entries, {"schema": notes.SCHEMA, "note": [{**related, "related_notes": ["note:gone"]}]})
        code, report = self.check([self.entries], before)
        self.assertEqual(code, 1)
        self.assertIn("unknown related note", report)

    def test_review_output_exposes_all_operations_provenance_and_capture_metadata(self):
        added = self.capture_change()
        added["message"] = "Check the comparison."
        added["proposed"].update(author="contributor", created="2026-01-02T03:04:05Z",
            sources=[{"kind": "publication", "name": "Reference", "address": "ref:one",
                      "quote": "Exact quotation", "quoted_term": "Term", "verification": "unresolved"}])
        base, _ = self.published()
        base["sources"] = [{"kind": "project", "name": "Historical reference", "address": "ref:old",
                            "verification": "recorded"}]
        edited = change({**base, "status": "proposed", "guidance": ["Updated guidance."]}, base)
        removed = change(base, base, "remove")
        changes = [added, edited, removed]
        body = notechanges.provenance(changes, submitter="contributor", submitted="2026-01-02T03:04:05Z",
                                      sha256="ab" * 32, files=["translation/xx/notes/entries.toml"])
        text, cut = notechanges.review([{**item, "file": "translation/xx/notes/entries.toml"} for item in changes])
        self.assertFalse(cut)
        for expected in ("| `note:example` | `add` | `entry/key` | `language: xx` | `proposed` |",
                         "| `note:example` | `edit` | `entry/key` | `language: xx` | `proposed` |",
                         "| `note:example` | `remove` | `entry/key` | `language: xx` | `removal` |",
                         "| Submitted by | `contributor`, the issue author |",
                         "| Submitted at | `2026-01-02T03:04:05Z` |", "Reviewer messages", "Check the comparison.",
                         "Evidence captures", "capture.png", "image/png", "original", "proposed", "Main menu",
                         added["attachments"][0]["sha256"]):
            self.assertIn(expected, body)
        for expected in ("Exact quotation", "quoted_term", "not recorded", "Updated guidance.",
                         "Historical reference", "| status | `accepted` | `removed` |"):
            self.assertIn(expected, text)
        for attachment in added["attachments"]:
            self.assertNotIn(attachment["data"], body + text)
