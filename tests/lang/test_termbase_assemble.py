from test_roots import TOOLKIT_ROOT
"""Termbase assemble checks and fixtures."""

import sys
from pathlib import Path
import csv
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.lang import termbase as tb
from gen3tl.lang import glossary_assemble, language, research
from gen3tl.cli.lang import glossary_assemble as assemble_command
from gen3tl.core import notes as structured
from gen3tl import TOOLKIT_ROOT
from termbase_support import HAVE_MORPH, setUpModule


class AssembleTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def assemble(self, rows, stem="general"):
        with (self.root / f"{stem}.tsv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]), delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)
        return glossary_assemble.assemble(self.root, only=stem)

    def test_explicit_declension_overrides_spelling_defaults(self):
        rows = [
            {"source_text": "Fixed", "chosen": "Камень", "notes": "", "declension": " fixed "},
            {"source_text": "Automatic", "chosen": "Мими", "notes": "Indeclinable", "declension": "auto"},
            {"source_text": "Manual", "chosen": "Камень", "notes": "Does not decline", "declension": "manual"},
        ]
        terms, problems = self.assemble(rows, "species")
        self.assertEqual(problems, [])
        self.assertEqual([term.declension for term in terms], ["fixed", "auto", "manual"])

    def test_missing_and_blank_declension_ignore_notes(self):
        rows = [{"source_text": str(i), "chosen": "Камень", "notes": note}
                for i, note in enumerate(("Indeclinable", "Does not decline", "не склоняется", ""))]
        for explicit in (False, True):
            with self.subTest(explicit=explicit):
                if explicit:
                    rows = [dict(row, declension="") for row in rows]
                terms, problems = self.assemble(rows)
                self.assertEqual(problems, [])
                self.assertEqual([term.declension for term in terms], ["auto"] * len(rows))
                self.assertEqual([term.notes for term in terms], [row["notes"] for row in rows])

    def test_spelling_and_category_defaults_are_preserved(self):
        for category, translated, expected in (("term", "-", "fixed"),
                                                ("term", "Камень", "auto"),
                                                ("species", "Мими", "fixed"),
                                                ("character", "Мими", "fixed"),
                                                ("item", "Мими", "auto")):
            with self.subTest(category=category, translated=translated):
                self.assertEqual(glossary_assemble.declension_for(category, translated), expected)

    def test_manual_pack_accepts_explicit_grammar(self):
        rows = [{"source_text": str(i), "chosen": "Target", "notes": "Indeclinable", "declension": value}
                for i, value in enumerate(("", "auto", "fixed", "manual"))]
        with language.use(language.Language("fixture", {}, self.root)):
            terms, problems = self.assemble(rows)
            self.assertEqual(problems, [])
            self.assertEqual([term.declension for term in terms], ["manual", "auto", "fixed", "manual"])
            terms, problems = self.assemble([{"source_text": "Source", "chosen": "Target", "notes": ""}])
            self.assertEqual(problems, [])
            self.assertEqual(terms[0].declension, "manual")

    def test_invalid_declension_is_not_inferred(self):
        with self.assertRaisesRegex(ValueError, "general: Source: invalid declension 'indeclinable'"):
            self.assemble([{"source_text": "Source", "chosen": "Камень", "notes": "",
                            "declension": "indeclinable"}])

    def test_existing_notes_and_metadata_remain_readable(self):
        note = 'A recorded name; "quoted term".\nA second line.'
        terms, problems = self.assemble([{
            "source_text": "Example", "chosen": "Мими", "notes": note, "declension": "fixed",
            "gender": "female", "role": "Recorded role", "evidence": "Recorded evidence",
            "address_to_player": "formal", "sources": "reference", "variants": "Alternative",
            "confidence": "medium", "fits": "no", "short_form": "Ми",
        }], "characters")
        self.assertEqual(problems, [])
        self.assertEqual(terms, [tb.Term(
            "character:example", "character", "Example", "Мими", label="Ми", gender="female",
            declension="fixed", sources="reference", variants="Alternative", confidence="medium",
            address="formal", notes=note + "; role: Recorded role; evidence: Recorded evidence",
            status="proposed")])

    @unittest.skipUnless(HAVE_MORPH, "pymorphy3 not installed (run with .venv/bin/python)")
    def test_rewording_notes_preserves_grammar_and_forms(self):
        rows = [
            {"source_text": "Fixed", "chosen": "Камень", "notes": "Indeclinable", "declension": "fixed"},
            {"source_text": "Automatic", "chosen": "Камень", "notes": "A recorded term", "declension": "auto"},
        ]
        before, problems = self.assemble(rows)
        self.assertEqual(problems, [])
        rows[0]["notes"] = "A rewritten description."
        rows[1]["notes"] = "The quoted alternative does not decline."
        after, problems = self.assemble(rows)
        self.assertEqual(problems, [])
        for old, new in zip(before, after):
            self.assertEqual(old.declension, new.declension)
            self.assertEqual(tb.generate_forms(old), tb.generate_forms(new))
            self.assertNotEqual(old.notes, new.notes)
        self.assertEqual(set(tb.generate_forms(after[0]).values()), {"Камень"})
        self.assertEqual(tb.generate_forms(after[1])["gent.sing"], "Камня")


class StructuredAssembleTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enterContext(language.use(language.Language("fixture", {}, self.root)))
        self.folder = self.root / "research"
        self.folder.mkdir()
        self.path = self.folder / "characters.tsv"
        self.glossary = self.root / "glossary.tsv"
        self.note = {
            "id": "research-note:first", "subject": {"kind": "research", "id": "finding:first"},
            "scope": {"kind": "language", "language": "fixture"},
            "facts": ["Canonical research wording."], "status": "unreviewed",
        }
        self.values = {
            "source_text": "Example", "chosen": "Target", "notes": "", "declension": "fixed",
            "gender": "female", "role": "Recorded role", "evidence": "Recorded evidence",
            "sources": "reference", "variants": "Alternative @ other", "confidence": "high",
            "label": "Compact", "address_to_player": "formal", "record_id": "finding:first",
            "note_ids": "research-note:first",
        }

    def store(self):
        structured.write_notes(self.path.with_suffix(".notes.toml"), {
            "schema": structured.SCHEMA, "note": [self.note] if self.values["note_ids"] else []})
        research.save(self.path, research.Table(tuple(self.values), [research.Row(dict(self.values))]))

    def test_structured_research_preserves_operational_fields_and_saves_references(self):
        self.values.update(note_ids="", notes="Canonical research wording.")
        self.store()
        legacy, problems = glossary_assemble.assemble(self.folder)
        self.assertEqual(problems, [])
        self.values.update(note_ids="research-note:first", notes="")
        self.store()
        terms, problems = glossary_assemble.assemble(self.folder)
        self.assertEqual(problems, [])
        for column in tb.COLUMNS:
            if column not in ("notes", "note_ids"):
                self.assertEqual(getattr(terms[0], column), getattr(legacy[0], column), column)
        tb.save(self.glossary, terms, write_notes=True)
        loaded = tb.load(self.glossary)
        self.assertEqual(loaded, terms)
        for text in ("Canonical research wording.", "Recorded role", "Recorded evidence"):
            self.assertIn(text, loaded[0].notes)
        self.assertEqual(loaded[0].related_note_records, (self.note,))
        generated = structured.read_notes(self.root / "notes" / "terms.toml")["note"]
        self.assertEqual(generated[0]["related_notes"], [self.note["id"]])
        self.assertNotIn("facts", generated[0])
        self.assertNotIn(self.note["facts"][0], (self.root / "notes" / "terms.toml").read_text())

    def test_research_cells_refresh_without_rewriting_term_notes(self):
        self.store()
        terms, _ = glossary_assemble.assemble(self.folder)
        tb.save(self.glossary, terms, write_notes=True)
        before = self.glossary.read_bytes(), (self.root / "notes" / "terms.toml").read_bytes()
        self.values.update(role="Changed role", evidence="Changed evidence")
        self.store()
        with self.assertRaisesRegex(ValueError, "research notes changed"):
            tb.save(self.glossary, terms, write_notes=True)
        updated = tb.load(self.glossary)
        self.assertTrue(updated[0].notes.startswith("role: Changed role\nevidence: Changed evidence\n"))
        self.assertNotIn("Recorded role", updated[0].notes)
        tb.save(self.glossary, updated, write_notes=True)
        self.assertEqual((self.glossary.read_bytes(), (self.root / "notes" / "terms.toml").read_bytes()), before)

    def test_command_preserves_independent_notes_and_is_idempotent(self):
        self.store()
        existing = tb.Term("character:example", "character", "Example", "Old", notes="Independent wording.")
        other = tb.Term("term:other", "term", "Other", "Another", notes="Other guidance.")
        tb.save(self.glossary, [existing, other])
        args = SimpleNamespace(research=str(self.folder), glossary=str(self.glossary), only="characters")
        with redirect_stdout(io.StringIO()):
            assemble_command(args)
        first = tb.load(self.glossary)
        self.assertEqual(first[1], other)
        self.assertEqual(first[0].translation, "Target")
        self.assertIn("Independent wording.", first[0].notes)
        before = self.glossary.read_bytes(), (self.root / "notes" / "terms.toml").read_bytes()
        with redirect_stdout(io.StringIO()):
            assemble_command(args)
        self.assertEqual((self.glossary.read_bytes(), (self.root / "notes" / "terms.toml").read_bytes()), before)
        first[0].status = "approved"
        tb.save(self.glossary, first)
        self.values["chosen"] = "Unreviewed replacement"
        self.store()
        with redirect_stdout(io.StringIO()):
            assemble_command(args)
        self.assertEqual(tb.load(self.glossary), first)

    def test_status_and_wording_changes_preserve_identity_and_grammar(self):
        self.store()
        before, _ = glossary_assemble.assemble(self.folder)
        for status in structured.STATUSES:
            with self.subTest(status=status):
                self.note.update(status=status, facts=["Changed research wording."])
                self.store()
                after, _ = glossary_assemble.assemble(self.folder)
                self.assertEqual(after[0].note_ids, before[0].note_ids)
                self.assertEqual(after[0].declension, before[0].declension)
                self.assertEqual(tb.generate_forms(after[0]), tb.generate_forms(before[0]))
                self.assertEqual("Changed research wording." in after[0].notes, status in ("unreviewed", "accepted"))

    def test_broken_research_references_prevent_glossary_writes(self):
        self.store()
        terms, _ = glossary_assemble.assemble(self.folder)
        tb.save(self.glossary, terms, write_notes=True)
        before = self.glossary.read_bytes(), (self.root / "notes" / "terms.toml").read_bytes()
        self.path.with_suffix(".notes.toml").unlink()
        args = SimpleNamespace(research=str(self.folder), glossary=str(self.glossary), only="characters")
        with self.assertRaisesRegex(ValueError, "unknown note"):
            assemble_command(args)
        self.assertEqual((self.glossary.read_bytes(), (self.root / "notes" / "terms.toml").read_bytes()), before)

    def test_references_resolve_in_every_registered_language_pack(self):
        packs = sorted((TOOLKIT_ROOT / "assets" / "lang").glob("*/language.json"))
        self.assertTrue(packs)
        for pack in packs:
            with self.subTest(language=pack.parent.name), language.use(language.load(pack.parent.name)):
                self.note["scope"]["language"] = language.current().code
                self.store()
                terms, problems = glossary_assemble.assemble(self.folder)
                self.assertEqual(problems, [])
                tb.save(self.glossary, terms, write_notes=True)
                self.assertEqual(tb.load(self.glossary), terms)
                self.assertEqual(terms[0].translation, self.values["chosen"])
                self.assertEqual(terms[0].declension, self.values["declension"])
