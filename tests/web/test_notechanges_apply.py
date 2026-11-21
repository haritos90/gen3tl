from test_roots import TOOLKIT_ROOT
"""Notechanges apply checks and fixtures."""

import sys
from pathlib import Path
import json
import os
import unittest
from copy import deepcopy
from unittest.mock import patch

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.catalog import entrynotes
from gen3tl.catalog.catalog import load_catalog
from gen3tl.catalog.po import write
from gen3tl.catalog.source import digest
from gen3tl.core import notes
from gen3tl.web import notechanges
import notechanges_support
from notechanges_support import change


class NoteSubmissionTest(notechanges_support.NoteSubmissionTest, unittest.TestCase):
    def test_note_only_dry_run_and_application_write_only_the_note_file(self):
        self.export([change(self.note)], notes="Reviewer message")
        checked = self.apply(check=True)
        self.assertEqual(len(checked.note_changes), 1)
        self.assertFalse(self.entries.exists())
        applied = self.apply()
        self.assertEqual(checked.files, applied.files)
        self.assertEqual(applied.files, [self.entries.relative_to(self.root)])
        self.assertEqual((self.po.read_bytes(), self.project.policy.read_bytes()), self.original)
        record = self.stored()["note:example"]
        self.assertEqual((record["author"], record["created"], record["status"]),
                         ("contributor", "2026-01-02T03:04:05Z", "proposed"))
        self.assertNotIn("Reviewer message", self.entries.read_text())
        # A proposed note is stored, not projected as guidance.
        with self.project.activate(self.prof):
            self.assertEqual(load_catalog(self.project.catalog, english=False)["key"][1].source_note, "")
        # Golden inputs: the canonical note file and nothing else.
        written = sorted(path for path in self.project.translation.rglob("*") if path.is_file()
                         and not path.is_relative_to(self.project.catalog))
        self.assertEqual(written, [self.entries])
        self.assertEqual(applied.changed, [])
        self.assertIsNone(applied.index)

    def test_edits_and_removals_apply_to_the_note_file(self):
        base, path = self.published()
        self.export([change({**self.note, "guidance": ["Updated guidance."]}, base)])
        self.apply()
        record = self.stored()["note:example"]
        self.assertEqual((record["guidance"], record["status"]), (["Updated guidance."], "proposed"))
        self.assertNotIn("author", record)
        self.assertNotIn("created", record)
        self.export([change(record, record, "remove")])
        self.apply()
        self.assertEqual(self.stored(), {})
        self.assertEqual(self.po.read_bytes(), self.original[0])

    def test_same_stamp_writes_and_rollbacks_refresh_retained_notes(self):
        for rollback in (False, True):
            with self.subTest(rollback=rollback):
                base, path = self.published()
                entrynotes.load(path, self.project.language)
                before = path.stat()
                proposed = deepcopy(base)
                proposed["guidance"] = ["x" * len(base["guidance"][0])]
                application = notechanges.Application(texts={path: notes.notes_text({
                    "schema": notes.SCHEMA, "note": [proposed]})})
                write_text, write_bytes = Path.write_text, Path.write_bytes

                def same_stamp(write):
                    def wrapped(file, *args, **kwargs):
                        result = write(file, *args, **kwargs)
                        if file == path:
                            os.utime(file, ns=(before.st_atime_ns, before.st_mtime_ns))
                        return result
                    return wrapped

                inventory = notechanges.inventory

                def inspect(*args):
                    result = inventory(*args)
                    self.assertEqual(result[1][base["id"]], proposed)
                    self.assertEqual(path.stat().st_size, before.st_size)
                    self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)
                    if rollback:
                        raise ValueError("reload failed")
                    return result

                with self.project.activate(self.prof), \
                        patch.object(Path, "write_text", same_stamp(write_text)), \
                        patch.object(Path, "write_bytes", same_stamp(write_bytes)), \
                        patch.object(notechanges, "inventory", side_effect=inspect):
                    if rollback:
                        with self.assertRaisesRegex(ValueError, "reload failed"):
                            application.write(self.project, self.prof)
                    else:
                        application.write(self.project, self.prof)
                self.assertEqual(entrynotes.load(path, self.project.language),
                                 {"key": [base if rollback else proposed]})

    def test_invalid_batch_does_not_write_valid_text_or_other_notes(self):
        invalid = deepcopy(self.note)
        invalid["id"] = "note:invalid"
        invalid["binding"]["source_sha256"] = "0" * 64
        self.export([change(self.note), change(invalid)], [{"id": "key", "translation": "New"}])
        with self.assertRaisesRegex(ValueError, "stale note binding"):
            self.apply()
        self.assertEqual((self.po.read_bytes(), self.project.policy.read_bytes()), self.original)
        self.assertFalse(self.entries.exists())

    def test_invalid_text_or_rule_cannot_store_valid_note(self):
        for entries, extra in (([{"id": "key", "base": "Stale", "translation": "New"}], {}),
                               ([], {"engine_rules": [{"id": "unknown"}]})):
            with self.subTest(extra=extra):
                self.export([change(self.note)], entries, **extra)
                with self.assertRaises(ValueError):
                    self.apply()
                self.assertFalse(self.entries.exists())
                self.assertEqual((self.po.read_bytes(), self.project.policy.read_bytes()), self.original)

    def test_stale_base_cannot_be_hidden_by_recomputed_revision(self):
        base, _ = self.published()
        for stale in ({**base, "guidance": ["Old guidance."]}, None):
            self.export([change(self.note, stale)])
            with self.assertRaisesRegex(ValueError, "stale note base"):
                self.apply()
        self.export([change(self.note, base)])
        document = json.loads(self.path.read_text())
        document["note_changes"][0]["base_revision"] = "null"
        self.path.write_text(json.dumps(document))
        with self.assertRaisesRegex(ValueError, "stale note base"):
            self.apply()

    def test_unknown_subject_scope_schema_and_client_destinations_are_rejected(self):
        changes = [change({**self.note, "scope": {"kind": "language", "language": "yy"}}),
                   {**change(self.note), "path": "../outside.toml"},
                   change({**self.note, "subject": {"kind": "entry", "id": "missing"},
                           "binding": {**self.note["binding"], "id": "missing"}})]
        for candidate in changes:
            with self.subTest(candidate=candidate):
                self.export([candidate])
                with self.assertRaises(ValueError):
                    self.apply()
        for extra in ({"note_changes_schema": "1.0.0"}, {"note_changes": None},
                      {"note_changes": [change(self.note), change(self.note)]}):
            self.export([change(self.note)], **extra)
            with self.assertRaises(ValueError):
                self.apply()
        self.assertFalse(self.entries.exists())

    def test_review_and_attribution_cannot_be_forged(self):
        for fields in ({"status": "accepted"}, {"author": "someone"}, {"created": "2026-01-01"},
                       {"reviewed_by": "someone"}, {"updated": "2026-01-01"}):
            with self.subTest(fields=fields):
                self.export([change({**self.note, **fields})])
                with self.assertRaises(ValueError):
                    self.apply()
        base, _ = self.published()
        self.export([change({**self.note, "author": "someone"}, base)])
        with self.assertRaisesRegex(ValueError, "existing note attribution"):
            self.apply()

    def test_new_web_citations_need_dates_and_unresolved_verification(self):
        source = {"kind": "web", "name": "Reference", "address": "https://example.test/term",
                  "verification": "unresolved", "retrieved": "2026-01-01"}
        for fields in ({"retrieved": "9999-01-01"}, {"address": "file:///private/file"},
                       {"verification": "checked"}, {"retrieved": None}):
            candidate = {**source, **fields}
            if candidate.get("retrieved") is None:
                candidate.pop("retrieved", None)
            self.export([change({**self.note, "sources": [candidate]})])
            with self.assertRaises(ValueError):
                self.apply()
        self.export([change({**self.note, "sources": [source]})])
        self.apply()
        self.assertEqual(self.stored()["note:example"]["sources"], [source])

    def test_unchanged_historical_citation_does_not_need_a_date(self):
        base, path = self.published()
        base["sources"] = [{"kind": "web", "name": "Old reference", "address": "https://example.test/old",
                            "verification": "recorded"}]
        notes.write_notes(path, {"schema": notes.SCHEMA, "note": [base]})
        self.export([change({**base, "status": "proposed", "guidance": ["Updated guidance."]}, base)])
        self.apply()
        self.assertEqual(self.stored()["note:example"]["sources"], base["sources"])

    def test_each_note_kind_applies_to_its_note_file(self):
        candidates = self.containers()
        self.export([change(note) for note in candidates])
        glossary = self.project.glossary.read_bytes()
        result = self.apply()
        files = {self.root / path for path in result.files}
        research = self.project.translation / "research"
        self.assertEqual(files, {self.project.glossary, self.project.translation / "notes/terms.toml",
                                 research / "sample.tsv", research / "sample.notes.toml",
                                 self.project.translation / "rules.toml", self.project.graphics,
                                 self.project.translation / "notes/graphics.toml", self.project.notes})
        with self.project.activate(self.prof):
            _, current = notechanges.inventory(self.project, self.prof,
                                               load_catalog(self.project.catalog, english=False))
            from gen3tl.lang import termbase
            term = termbase.load(self.project.glossary)[0]
        for note in candidates:
            self.assertEqual(current[note["id"]]["guidance"], note["guidance"])
            self.assertEqual(current[note["id"]]["author"], "contributor")
        self.assertEqual(term.note_ids, ("note:term-term:one",))
        self.assertNotEqual(self.project.glossary.read_bytes(), glossary)
        self.assertIn("note:rule-old", current)
        self.assertIn("Legacy guidance.", self.project.notes.read_text())
        self.assertEqual((self.po.read_bytes(), self.project.policy.read_bytes()), self.original)

    def test_legacy_subjects_and_profile_facts_are_refused(self):
        from gen3tl.profile import schema

        candidates = self.containers(legacy=True)
        legacy_entry = self.note_for("entry", "legacy", binding={"kind": "entry", "id": "legacy",
                                                                 "source_sha256": digest("Legacy.")})
        shared_legacy = {**legacy_entry, "id": "note:shared-legacy", "scope": {"kind": "shared"}}
        fact = schema.overlay_facts(schema.flat(self.raw.path))[0]
        profile = self.note_for("profile_fact", fact.id, {"kind": "shared"},
                                {"kind": "profile_fact", "id": fact.id, "value_sha256": schema.value_digest(fact.value),
                                 "field": "evidence"})
        language_profile = {**profile, "id": "note:language-profile", "scope": {"kind": "language", "language": "xx"}}
        files = lambda: {path: path.read_bytes() for path in self.root.rglob("*")
                         if path.is_file() and path != self.path}
        before = files()
        for note in [*candidates[:5], legacy_entry, shared_legacy, profile, language_profile]:
            with self.subTest(note=note["id"]):
                self.export([change(note)])
                with self.assertRaisesRegex(ValueError, "legacy note prose|profile fact notes"):
                    self.apply()
        self.assertEqual(files(), before)
        notes.write_notes(self.project.profile_notes, {"schema": notes.SCHEMA, "note": [language_profile]})
        with self.project.activate(self.prof):
            _, current = notechanges.inventory(self.project, self.prof,
                                               load_catalog(self.project.catalog, english=False))
        self.assertEqual(current[language_profile["id"]], language_profile)
        self.assertIn(self.project.profile_notes.resolve(), notechanges._containers(self.project, self.prof))

    def test_term_edit_and_term_note_share_one_glossary_write(self):
        candidates = self.containers()
        (self.project.translation / "term-index.tsv").write_text("string\tterm\tsource_names_term\n")
        self.export([change(candidates[0])],
                    terms=[{"id": "term:one", "base": {"sources": ""}, "changes": {"sources": "ref:one"}}])
        result = self.apply()
        self.assertEqual(len(result.terms), 1)
        with self.project.activate(self.prof):
            from gen3tl.lang import termbase
            term = termbase.load(self.project.glossary)[0]
        self.assertEqual((term.sources, term.note_ids), ("ref:one", ("note:term-term:one",)))
        self.assertEqual(len(result.files), len(set(result.files)))

    def test_removing_a_generated_reference_preserves_the_editable_cell(self):
        from gen3tl.lang import research, termbase

        folder = self.project.translation / "research"
        folder.mkdir(parents=True)
        finding = research.Row({"source_text": "Example", "name": "Target", "record_id": "finding:one", "note_ids": ""})
        with self.project.activate(self.prof):
            research.save(folder / "findings.tsv", research.Table(tuple(finding.values), [finding]))
            term = termbase.Term("term:one", "term", "Example", "Target", notes="Independent guidance.")
            reference = research.term_note(term.id, finding, "research-citation", [], citation={"label": "Archive"})
            termbase.attach_notes(term, [reference], findings={finding.record_id: finding})
            termbase.save(self.project.glossary, [term], write_notes=True)
        self.export([change(reference, reference, "remove")])
        self.apply()
        with self.project.activate(self.prof):
            loaded = termbase.load(self.project.glossary)[0]
        self.assertEqual(loaded.note_ids, ())
        self.assertEqual(loaded.editable_notes, "Independent guidance.")

    def test_scope_change_moves_the_note_between_note_files(self):
        self.containers()
        base, _ = self.published()
        moved = {**self.note, "scope": {"kind": "shared"}}
        self.export([change(moved, base)])
        self.apply()
        self.assertEqual(self.stored(), {})
        self.assertIn("note:example", self.project.notes.read_text())

    def test_shared_observations_require_reproducible_sources(self):
        self.containers()
        note = {**self.note, "scope": {"kind": "shared"}, "facts": ["The menu uses this label."]}
        self.export([change(note)])
        with self.assertRaisesRegex(ValueError, "game observations"):
            self.apply()
        note["sources"] = [{"kind": "rom", "name": "Base ROM", "address": self.raw.sha1,
                            "locator": "Main menu", "verification": "unresolved"}]
        self.export([change(note)])
        self.apply()
        from gen3tl.catalog import guidance
        stored = {row["id"]: row for row in guidance.read_notes(self.project.notes)}
        self.assertEqual(stored["note:example"]["scope"], {"kind": "shared"})
