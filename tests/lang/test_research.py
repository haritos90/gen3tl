import copy
import csv
import tempfile
import unittest
from pathlib import Path

from test_roots import TEST_ROOT
from gen3tl import TOOLKIT_ROOT
from gen3tl.core import notes as structured
from gen3tl.lang import language, research


class ResearchTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enterContext(language.use(language.Language("fixture", {}, self.root)))
        self.path = self.root / "findings.tsv"
        self.notes_path = self.path.with_suffix(".notes.toml")
        self.values = {
            "source_text": "Repeated name", "chosen": "Chosen form", "variants": "Other @ reference",
            "sources": "reference, other", "url": "https://example.test/page?edition=2#quote",
            "retrieved": "2026-10-18", "episode": "Episode 14", "gender": "unknown",
            "gender_evidence": '  The exact "quotation".\nAnother line\twith a tab.  ',
            "notes": "", "extra": "An unfamiliar column", "record_id": "finding:first", "note_ids": "first",
        }
        self.note = copy.deepcopy(structured.read_notes(
            TEST_ROOT / "fixtures" / "notes.toml")["note"][1])
        self.note.update(id="first", subject={"kind": "research", "id": "finding:first"},
                         scope={"kind": "language", "language": "fixture"}, status="unreviewed")
        self.note["sources"][0].update(quote=self.values["gender_evidence"],
                                        retrieved=self.values["retrieved"], quoted_term="Exact spelling")

    def store(self, rows=None, records=None):
        rows = rows if rows is not None else [self.values]
        structured.write_notes(self.notes_path, {"schema": structured.SCHEMA,
                                                 "note": records if records is not None else [self.note]})
        table = research.Table(tuple(rows[0]), [research.Row(dict(row)) for row in rows])
        research.save(self.path, table)
        return research.load(self.path)

    def test_columns_quotes_sources_and_notes_round_trip(self):
        table = self.store()
        before = self.path.read_bytes(), self.notes_path.read_bytes()
        self.assertEqual(table.columns, tuple(self.values))
        self.assertEqual(table.rows[0].values, self.values)
        self.assertEqual(table.rows[0].record_id, "finding:first")
        self.assertEqual(table.rows[0].note_ids, ("first",))
        self.assertEqual(table.rows[0].note_records, (self.note,))
        research.save(self.path, table)
        self.assertEqual((self.path.read_bytes(), self.notes_path.read_bytes()), before)
        self.assertEqual(research.load(self.path), table)

    def test_repeated_findings_stay_distinct_after_reordering_and_editing(self):
        second = dict(self.values, record_id="finding:second", note_ids="second")
        second_note = dict(self.note, id="second", subject={"kind": "research", "id": "finding:second"})
        table = self.store([self.values, second], [self.note, second_note])
        table.rows.reverse()
        table.rows[0].values["source_text"] = "Reworded name"
        research.save(self.path, table)
        loaded = research.load(self.path)
        self.assertEqual([row.record_id for row in loaded.rows], ["finding:second", "finding:first"])
        self.assertEqual([row.note_ids for row in loaded.rows], [("second",), ("first",)])
        self.assertEqual(loaded.rows[0].note_records, (second_note,))
        self.assertEqual(loaded.rows[1].note_records, (self.note,))

    def test_legacy_rows_keep_prose_and_need_no_identity(self):
        values = {key: value for key, value in self.values.items() if key not in research.IDENTITY_COLUMNS}
        values["notes"] = '  Legacy "notes".\nNext line.  '
        table = self.store([values, values], [])
        self.notes_path.unlink()
        before = self.path.read_bytes()
        research.save(self.path, research.load(self.path))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.notes_path.exists())
        self.assertEqual([row.values for row in table.rows], [values, values])
        self.assertTrue(all(not row.record_id and not row.note_ids and not row.note_records for row in table.rows))

    def test_identified_legacy_rows_keep_their_original_notes(self):
        values = dict(self.values, note_ids="", notes="Existing guidance.")
        table = self.store([values], [])
        before = self.path.read_bytes()
        research.save(self.path, table)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(table.rows[0].values, values)
        self.assertEqual(table.rows[0].note_records, ())

    def test_note_order_status_and_attribution_are_preserved(self):
        records = [dict(self.note, id=status, status=status) for status in structured.STATUSES]
        values = dict(self.values, note_ids="|".join(reversed(structured.STATUSES)))
        table = self.store([values], records)
        self.assertEqual(table.rows[0].note_records, tuple(reversed(records)))
        self.assertTrue(all("author" not in record and "created" not in record
                            for record in table.rows[0].note_records))
        research.save(self.path, table)
        self.assertEqual(structured.read_notes(self.notes_path)["note"], records)

    def test_new_note_attribution_round_trips(self):
        self.note.update(author="contributor", created="2026-11-11T10:00:00Z", status="proposed")
        structured.validate_note(self.note, new=True)
        table = self.store()
        research.save(self.path, table)
        self.assertEqual(research.load(self.path).rows[0].note_records, (self.note,))

    def test_note_wording_changes_keep_identity_and_reject_stale_saves(self):
        table = self.store()
        self.note["facts"] = ["Reworded assertion."]
        structured.write_notes(self.notes_path, {"schema": structured.SCHEMA, "note": [self.note]})
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "structured notes changed"):
            research.save(self.path, table)
        self.assertEqual(self.path.read_bytes(), before)
        updated = research.load(self.path)
        self.assertEqual(updated.rows[0].record_id, table.rows[0].record_id)
        self.assertEqual(updated.rows[0].note_ids, table.rows[0].note_ids)
        self.assertEqual(updated.rows[0].note_records, (self.note,))
        research.save(self.path, updated)
        self.assertEqual(self.path.read_bytes(), before)

    def test_changed_in_memory_records_cannot_overwrite_canonical_notes(self):
        table = self.store()
        before = self.path.read_bytes(), self.notes_path.read_bytes()
        table.rows[0].note_records[0]["facts"] = ["Changed locally."]
        with self.assertRaisesRegex(ValueError, "structured notes changed"):
            research.save(self.path, table)
        self.assertEqual((self.path.read_bytes(), self.notes_path.read_bytes()), before)

    def test_wrong_subject_scope_or_unknown_target_is_rejected(self):
        table = self.store()
        before = self.path.read_bytes()
        cases = [
            (dict(self.note, subject={"kind": "term", "id": "finding:first"}), "research subject"),
            (dict(self.note, subject={"kind": "research", "id": "missing"}), "unknown research record"),
            (dict(self.note, scope={"kind": "shared"}), "language scope"),
            (dict(self.note, scope={"kind": "language", "language": "other"}), "language scope"),
            (dict(self.note, id="missing"), "unknown note"),
        ]
        for record, message in cases:
            with self.subTest(message=message):
                structured.write_notes(self.notes_path, {"schema": structured.SCHEMA, "note": [record]})
                with self.assertRaisesRegex(ValueError, message):
                    research.load(self.path)
                with self.assertRaisesRegex(ValueError, message):
                    research.save(self.path, table)
                self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_identity_and_reference_changes_leave_table_untouched(self):
        table = self.store()
        before = self.path.read_bytes()
        for key, value in (("record_id", ""), ("record_id", " spaced"), ("record_id", "bad|id"),
                           ("note_ids", "missing"), ("note_ids", "first|first"), ("note_ids", " first"),
                           ("note_ids", "first|"), ("note_ids", "first\n"), ("note_ids", ""),
                           ("notes", "Competing prose.")):
            changed = copy.deepcopy(table)
            changed.rows[0].values[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                research.save(self.path, changed)
            self.assertEqual(self.path.read_bytes(), before)

    def test_duplicate_rows_and_cross_row_references_are_rejected(self):
        table = self.store()
        before = self.path.read_bytes()
        table.rows.append(copy.deepcopy(table.rows[0]))
        with self.assertRaisesRegex(ValueError, "duplicate record_id"):
            research.save(self.path, table)
        table.rows[-1].values["record_id"] = "finding:second"
        with self.assertRaisesRegex(ValueError, "different research record"):
            research.save(self.path, table)
        self.assertEqual(self.path.read_bytes(), before)

    def test_missing_collection_and_unreferenced_notes_are_rejected(self):
        self.store()
        self.notes_path.unlink()
        with self.assertRaisesRegex(ValueError, "unknown note"):
            research.load(self.path)
        second = dict(self.note, id="second")
        structured.write_notes(self.notes_path, {"schema": structured.SCHEMA, "note": [self.note, second]})
        with self.assertRaisesRegex(ValueError, "unreferenced notes"):
            research.load(self.path)

    def test_loader_rejects_competing_legacy_prose(self):
        self.store()
        with self.path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=tuple(self.values), delimiter="\t")
            writer.writeheader()
            writer.writerow(dict(self.values, notes="Competing prose."))
        with self.assertRaisesRegex(ValueError, "both legacy and structured notes"):
            research.load(self.path)

    def test_malformed_tables_cannot_drop_columns_or_cells(self):
        for text in ("", "source_text\tsource_text\nA\tB\n", "source_text\t\nA\tB\n",
                     "source_text\tnotes\nA\n", "english\nA\tB\n",
                     "source_text\trecord_id\nA\tfirst\n", "source_text\tnote_ids\nA\tfirst\n"):
            with self.subTest(text=text):
                self.path.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    research.load(self.path)

    def test_registered_language_scopes_preserve_columns(self):
        packs = sorted((TOOLKIT_ROOT / "assets" / "lang").glob("*/language.json"))
        self.assertTrue(packs)
        for pack in packs:
            with self.subTest(language=pack.parent.name), language.use(language.load(pack.parent.name)):
                self.note["scope"]["language"] = language.current().code
                table = self.store()
                self.assertEqual(table.rows[0].values, self.values)
                self.assertEqual(table.rows[0].note_records, (self.note,))

    def test_project_index_rejects_duplicate_record_and_note_ids(self):
        self.store()
        second = self.root / "second.tsv"
        second.write_bytes(self.path.read_bytes())
        second.with_suffix(".notes.toml").write_bytes(self.notes_path.read_bytes())
        with self.assertRaisesRegex(ValueError, "duplicate record_id"):
            research.index(self.root)
        self.values["record_id"] = "finding:second"
        self.note["subject"]["id"] = "finding:second"
        self.store()
        with self.assertRaisesRegex(ValueError, "duplicate note"):
            research.index(self.root)

    def test_project_index_keeps_identity_when_table_moves(self):
        table = self.store()
        second = self.root / "renamed.tsv"
        self.path.rename(second)
        self.notes_path.rename(second.with_suffix(".notes.toml"))
        self.assertEqual(research.index(self.root), {self.values["record_id"]: table.rows[0]})

    def test_assembly_removes_only_equal_copies_and_preserves_other_facts(self):
        self.values.update(role="  Recorded role  ", evidence="Recorded evidence")
        row = self.store().rows[0]
        copied = ["role: Recorded role", "evidence: Recorded evidence"]
        independent = ["role: Independent statement", "Another fact."]
        note = research.term_note("term:example", row, "research-assembly", copied + independent)
        self.assertEqual(note["facts"], independent)
        self.assertEqual(note["assembly_fields"], ["role", "evidence"])
        self.assertEqual(research.term_facts(note, row), copied + independent)
        empty = research.term_note("term:example", row, "research-assembly", copied)
        self.assertNotIn("facts", empty)
        self.assertEqual(note["id"], empty["id"])
        self.assertEqual(research.term_facts(empty, row), copied)
        reviewed = research.term_note("term:example", row, "research-assembly", independent)
        self.assertNotIn("assembly_fields", reviewed)
        self.assertEqual(research.term_facts(reviewed, row), independent)
        row.values["role"] = "Changed role"
        self.assertEqual(research.term_facts(note, row), ["role: Changed role", copied[1], *independent])


if __name__ == "__main__":
    unittest.main()
