from test_roots import TEST_ROOT, TOOLKIT_ROOT
"""Termbase notes checks and fixtures."""

import sys
from pathlib import Path
import csv
import copy
import tempfile
import unittest

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.lang import termbase as tb
from gen3tl.lang import language
from gen3tl.core import notes as structured
from gen3tl import TOOLKIT_ROOT
from termbase_support import setUpModule


class GlossaryNotesTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enterContext(language.use(language.Language("zz", {}, self.root)))
        self.glossary = self.root / "glossary.tsv"
        self.notes_path = self.root / "notes" / "terms.toml"
        self.note = structured.read_notes(TEST_ROOT / "fixtures" / "notes.toml")["note"][0]
        self.term = tb.Term("move:sample", "move", "Sample", "Target", label="Label", gender="female",
                            declension="manual", forms={"nomn.sing": "Target", "gent.sing": "Targets"},
                            sources="reference, own", variants="Alternative @ reference", confidence="high",
                            address="formal", status="reviewed", note_ids=(self.note["id"],))

    def store(self, records=None):
        structured.write_notes(self.notes_path, {"schema": structured.SCHEMA,
                                                 "note": records if records is not None else [self.note]})
        tb.save(self.glossary, [self.term])
        return tb.load(self.glossary)[0]

    def test_reference_round_trip_preserves_fields_and_canonical_notes(self):
        self.note.update(facts=["The name identifies a move."],
                         author="contributor", created="2026-11-11", reviewed_by="reviewer",
                         reviewed_at="2026-11-11T10:00:00Z")
        row = self.store()
        original = self.glossary.read_bytes(), self.notes_path.read_bytes()
        for column in tb.COLUMNS:
            if column != "notes":
                self.assertEqual(getattr(row, column), getattr(self.term, column), column)
        self.assertEqual(row.note_records, (self.note,))
        for value in (self.note["guidance"][0], self.note["facts"][0], self.note["reason"][0],
                      self.note["sources"][0]["address"], self.note["sources"][0]["quote"],
                      "accepted", "contributor", "2026-11-11"):
            self.assertIn(value, row.notes)
        tb.save(self.glossary, [row])
        self.assertEqual((self.glossary.read_bytes(), self.notes_path.read_bytes()), original)
        self.assertEqual(tb.load(self.glossary), [row])
        with self.glossary.open(encoding="utf-8", newline="") as stream:
            stored = next(csv.DictReader(stream, delimiter="\t"))
        self.assertEqual(stored["notes"], "")
        self.assertEqual(stored["note_ids"], self.note["id"])

    def test_identity_survives_wording_changes_and_reference_order(self):
        first = dict(self.note, id="first", status="unreviewed")
        second = dict(self.note, id="second", guidance=["Another instruction."])
        self.term.note_ids = ("second", "first")
        row = self.store([first, second])
        self.assertEqual([record["id"] for record in row.note_records], ["second", "first"])
        self.assertLess(row.notes.index("Another instruction."), row.notes.index(first["guidance"][0]))
        second["guidance"] = ["Reworded instruction."]
        structured.write_notes(self.notes_path, {"schema": structured.SCHEMA, "note": [second, first]})
        updated = tb.load(self.glossary)[0]
        self.assertEqual(updated.note_ids, row.note_ids)
        self.assertIn("Reworded instruction.", updated.notes)
        self.assertNotIn("Another instruction.", updated.notes)
        before = self.glossary.read_bytes()
        with self.assertRaisesRegex(ValueError, "structured notes changed"):
            tb.save(self.glossary, [row])
        self.assertEqual(self.glossary.read_bytes(), before)
        tb.save(self.glossary, [updated])

    def test_inactive_notes_stay_stored_without_becoming_guidance(self):
        records = [dict(self.note, id=status, status=status, guidance=[status + " instruction."])
                   for status in structured.STATUSES]
        self.term.note_ids = tuple(record["id"] for record in records)
        row = self.store(records)
        for status in structured.STATUSES:
            self.assertEqual(status + " instruction." in row.notes, status in ("unreviewed", "accepted"))
        self.assertEqual(row.note_records, tuple(records))
        self.assertTrue(all("author" not in record and "created" not in record for record in row.note_records))
        tb.save(self.glossary, [row])
        self.assertEqual(structured.read_notes(self.notes_path)["note"], records)

    def test_notes_preserve_operational_fields_in_every_language_pack(self):
        packs = sorted((TOOLKIT_ROOT / "assets" / "lang").glob("*/language.json"))
        self.assertTrue(packs)
        for pack in packs:
            with self.subTest(language=pack.parent.name), language.use(language.load(pack.parent.name)):
                self.note["scope"]["language"] = language.current().code
                row = self.store()
                for column in tb.COLUMNS:
                    if column != "notes":
                        self.assertEqual(getattr(row, column), getattr(self.term, column), column)
                self.assertEqual(row.all_forms(), self.term.all_forms())
                self.assertEqual(tb.generate_forms(row), tb.generate_forms(self.term))
                self.assertEqual(tb.find_forms("Target", row), tb.find_forms("Target", self.term))
                self.assertEqual(row.note_records[0]["scope"], self.note["scope"])

    def test_invalid_references_and_scopes_leave_glossary_untouched(self):
        self.store()
        before = self.glossary.read_bytes()
        cases = [
            (dict(self.note, subject={"kind": "term", "id": "move:missing"}), "unknown term"),
            (dict(self.note, subject={"kind": "research", "id": self.term.id}), "term subject"),
            (dict(self.note, scope={"kind": "shared"}), "language scope"),
            (dict(self.note, scope={"kind": "language", "language": "other"}), "language scope"),
            (dict(self.note, id="different"), "unknown note"),
        ]
        for record, message in cases:
            with self.subTest(message=message):
                structured.write_notes(self.notes_path, {"schema": structured.SCHEMA, "note": [record]})
                with self.assertRaisesRegex(ValueError, message):
                    tb.load(self.glossary)
                with self.assertRaisesRegex(ValueError, message):
                    tb.save(self.glossary, [self.term])
                self.assertEqual(self.glossary.read_bytes(), before)

    def test_missing_duplicate_or_wrong_term_references_are_rejected(self):
        row = self.store()
        before = self.glossary.read_bytes()
        for references in (("missing",), (self.note["id"], self.note["id"]), ("bad|id",), ("",), ()):
            invalid = copy.deepcopy(row)
            invalid.note_ids = references
            with self.subTest(references=references), self.assertRaises(ValueError):
                tb.save(self.glossary, [invalid])
            self.assertEqual(self.glossary.read_bytes(), before)
        other = tb.Term("move:other", "move", "Other", "Another", note_ids=row.note_ids)
        with self.assertRaisesRegex(ValueError, "different term"):
            tb.save(self.glossary, [self.term, other])
        with self.assertRaisesRegex(ValueError, "ambiguous term"):
            tb.save(self.glossary, [self.term, copy.deepcopy(self.term)])
        self.notes_path.unlink()
        with self.assertRaisesRegex(ValueError, "unknown note"):
            tb.load(self.glossary)

    def test_legacy_prose_cannot_overwrite_structured_notes(self):
        row = self.store()
        before = self.glossary.read_bytes(), self.notes_path.read_bytes()
        for prose in ("Replacement text", ""):
            changed = copy.deepcopy(row)
            changed.notes = prose
            with self.subTest(prose=prose), self.assertRaisesRegex(ValueError, "structured notes"):
                tb.save(self.glossary, [changed])
            self.assertEqual((self.glossary.read_bytes(), self.notes_path.read_bytes()), before)
        with self.glossary.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream, delimiter="\t")
            fields, rows = reader.fieldnames, list(reader)
        rows[0]["notes"] = "Competing prose"
        with self.glossary.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)
        with self.assertRaisesRegex(ValueError, "both legacy and structured notes"):
            tb.load(self.glossary)


class SaveTest(unittest.TestCase):
    def test_rows_with_quotes_round_trip(self):
        # leading and inner quote characters
        lead = tb.Term("t-a", "term", "A", "А", notes='"Black Horizon": gang | full form')
        inner = tb.Term("t-b", "term", "B", "Б", notes='does not match "National Dex".')
        with tempfile.TemporaryDirectory() as tmp:
            g = Path(tmp) / "g.tsv"
            tb.save(g, [lead, inner])
            first = g.read_bytes()
            self.assertEqual([t.notes for t in tb.load(g)], [lead.notes, inner.notes])
            tb.save(g, tb.load(g))
            self.assertEqual(g.read_bytes(), first)
