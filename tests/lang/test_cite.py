import sys
import copy
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from test_roots import TOOLKIT_ROOT

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.lang.cite import merge  # noqa: E402
from gen3tl.lang.termbase import Term  # noqa: E402
from gen3tl.lang import cite, language, research, termbase as tb, termedit  # noqa: E402
from gen3tl.core import notes as structured  # noqa: E402
from gen3tl.cli.lang import glossary_cite  # noqa: E402
from project_fixture import select_language  # noqa: E402


def setUpModule():
    select_language("ru")


def row(en, name, src, dub="AG001 (season 6)", gender="", ev=""):
    return {"source_text": en, "name": name, "source": src, "dub": dub, "gender": gender, "evidence": ev}


class CiteTest(unittest.TestCase):
    def test_legacy_citation_keeps_its_position_and_independent_notes(self):
        rows = [row("Example", "Target", "reference")]
        term = Term("species:example", "species", "Example", "Target",
                    sources="reference",
                    notes="First observation. | Archive: Target (reference; AG001); gender not shown"
                          " | Last observation.")
        original = copy.deepcopy(term)
        for _ in range(2):
            merge([term], rows, "species", "Archive")
            self.assertEqual(term, original)

    def test_legacy_citation_refreshes_in_place_and_removes_duplicate_summaries(self):
        term = Term("species:example", "species", "Example", "Target",
                    notes="First observation. | Archive: Previous summary. | Last observation."
                          " | Archive: Duplicate summary.")
        merge([term], [row("Example", "Target", "reference")], "species", "Archive")
        self.assertEqual(term.notes,
                         "First observation. | Archive: Target (reference; AG001); gender not shown"
                         " | Last observation.")

    def test_sources_variants_notes_idempotent(self):
        t = Term(id="species:psyduck", category="species", source_text="Psyduck", translation="Псайдак",
                 sources="go, tcg", variants="Психоутка @ bulba", notes="Official GO name.")
        forms = [Term(id="species:darmanitan", category="species", source_text="Darmanitan", translation="Дарманитан"),
                 Term(id="species:darmanitan-z", category="species", source_text="Darmanitan", translation="Дарманитан")]
        rows = [row("Psyduck", "Псайдак", "ptv", gender="m", ev="«Псайдак устал»"),
                row("Psyduck", "Психоутка", "fandom-ep", dub="DP087 (dub)"),
                row("Psyduck", "Псайдака (oblique form)", "ptv"),
                row("Psyduck", "Псайдаки (plural)", "ptv"),
                row("Darmanitan", "Дарманитан", "ptv"),
                row("Missingno", "Мисинго", "ptv")]
        for _ in range(2):
            s = merge([t] + forms, rows, "species", "TV")
        self.assertEqual(t.sources, "go, tcg, ptv")
        self.assertEqual(t.variants, "Психоутка @ bulba, fandom-ep")
        self.assertEqual(t.notes, "Official GO name. | TV: Псайдак (ptv; AG001); Психоутка (fandom-ep; DP087); "
                                  "gender m ×1 (ptv AG001 «Псайдак устал»)")
        self.assertTrue(all("ptv" in f.sources for f in forms))
        self.assertEqual(s.unknown, ["Missingno"])


class StructuredCiteTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enterContext(language.use(language.Language("fixture", {}, self.root)))
        self.path = self.root / "research" / "findings.tsv"
        self.path.parent.mkdir()
        self.glossary = self.root / "glossary.tsv"
        self.values = dict(row("Example", "Target", "reference", gender="m", ev="Exact quotation."),
                           notes="", record_id="finding:first", note_ids="research-note:first")
        self.note = {
            "id": "research-note:first", "subject": {"kind": "research", "id": "finding:first"},
            "scope": {"kind": "language", "language": "fixture"},
            "facts": ["The research records this spelling."], "status": "unreviewed",
            "sources": [{"kind": "web", "name": "Reference", "address": "https://example.test/#quote",
                         "locator": "Episode 1", "quote": "Exact quotation.", "verification": "recorded"}],
        }
        self.term = Term("species:example", "species", "Example", "Target", label="Compact",
                         gender="female", declension="fixed", forms={"nomn.sing": "Target"},
                         notes="Independent guidance. | Archive: Old summary. | Another observation.", status="approved")

    def store(self, values=None, notes=None):
        values = values if values is not None else [self.values]
        structured.write_notes(self.path.with_suffix(".notes.toml"), {
            "schema": structured.SCHEMA, "note": notes if notes is not None else [self.note]})
        research.save(self.path, research.Table(tuple(values[0]), [research.Row(v) for v in values]))
        return cite.load(self.path)

    def test_merge_preserves_fields_and_canonical_research_without_copying_prose(self):
        rows = self.store()
        source = self.path.with_suffix(".notes.toml").read_bytes()
        legacy = copy.deepcopy(self.term)
        merge([legacy], [row("Example", "Target", "reference", gender="m", ev="Exact quotation.")],
              "species", "Archive")
        merge([self.term], rows, "species", "Archive")
        for column in tb.COLUMNS:
            if column not in ("notes", "note_ids"):
                self.assertEqual(getattr(self.term, column), getattr(legacy, column), column)
        self.assertEqual(self.term.editable_notes, "Independent guidance. | Another observation.")
        self.assertIn(self.note["facts"][0], self.term.notes)
        tb.save(self.glossary, [self.term], write_notes=True)
        self.assertEqual(tb.load(self.glossary), [self.term])
        stored = structured.read_notes(self.root / "notes" / "terms.toml")["note"]
        self.assertEqual(len(stored), 1)
        self.assertNotIn(self.note["facts"][0], (self.root / "notes" / "terms.toml").read_text())
        self.assertEqual(self.path.with_suffix(".notes.toml").read_bytes(), source)
        self.assertTrue(all("author" not in note and "created" not in note for note in stored))

    def test_changed_label_and_repeated_findings_keep_stable_independent_references(self):
        second = dict(self.values, record_id="finding:second", note_ids="research-note:second")
        second_note = dict(self.note, id="research-note:second", subject={"kind": "research", "id": "finding:second"})
        rows = self.store([self.values, second], [self.note, second_note])
        merge([self.term], rows, "species", "Archive")
        identities = self.term.note_ids
        merge([self.term], list(reversed(rows)), "species", "Renamed")
        self.assertEqual(self.term.note_ids, identities)
        self.assertEqual(len(identities), 2)
        self.assertEqual(self.term.editable_notes, "Independent guidance. | Another observation.")
        self.assertTrue(all(note["citation"]["label"] == "Renamed" and "facts" not in note
                            for note in self.term.note_records))
        self.assertIn("Renamed: Target", self.term.notes)
        tb.save(self.glossary, [self.term], write_notes=True)
        before = self.glossary.read_bytes(), (self.root / "notes" / "terms.toml").read_bytes()
        merge([self.term], rows, "species", "Renamed")
        tb.save(self.glossary, [self.term], write_notes=True)
        self.assertEqual((self.glossary.read_bytes(), (self.root / "notes" / "terms.toml").read_bytes()), before)

    def test_research_edits_refresh_projection_and_reject_stale_saves(self):
        merge([self.term], self.store(), "species", "Archive")
        tb.save(self.glossary, [self.term], write_notes=True)
        self.note["facts"] = ["Reworded research finding."]
        self.store()
        before = self.glossary.read_bytes(), (self.root / "notes" / "terms.toml").read_bytes()
        for write_notes in (False, True):
            with self.assertRaisesRegex(ValueError, "research notes changed"):
                tb.save(self.glossary, [self.term], write_notes=write_notes)
        updated = tb.load(self.glossary)[0]
        self.assertEqual(updated.note_ids, self.term.note_ids)
        self.assertIn("Reworded research finding.", updated.notes)
        tb.save(self.glossary, [updated])
        self.assertEqual((self.glossary.read_bytes(), (self.root / "notes" / "terms.toml").read_bytes()), before)

    def test_cli_round_trip_and_inactive_research_notes(self):
        self.note.update(status="proposed", facts=["Unreviewed proposed wording."])
        self.store()
        tb.save(self.glossary, [self.term])
        args = SimpleNamespace(table=str(self.path), columns=None, glossary=str(self.glossary),
                               category="species", label="Archive")
        for _ in range(2):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(glossary_cite(args), 0)
        loaded = tb.load(self.glossary)[0]
        self.assertNotIn("Unreviewed proposed wording.", loaded.notes)
        self.assertEqual(loaded.related_note_records, (self.note,))

    def test_cli_reproduces_stored_citations_and_notes_byte_for_byte(self):
        merge([self.term], self.store(), "species", "Archive")
        tb.save(self.glossary, [self.term], write_notes=True)
        paths = (self.glossary, self.root / "notes" / "terms.toml",
                 self.path, self.path.with_suffix(".notes.toml"))
        before = [path.read_bytes() for path in paths]
        args = SimpleNamespace(table=str(self.path), columns=None, glossary=str(self.glossary),
                               category="species", label="Archive")
        for _ in range(2):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(glossary_cite(args), 0)
            self.assertEqual([path.read_bytes() for path in paths], before)

    def test_legacy_findings_need_identities_when_term_is_migrated(self):
        merge([self.term], self.store(), "species", "Archive")
        before = copy.deepcopy(self.term)
        with self.assertRaisesRegex(ValueError, "persistent research identities"):
            merge([self.term], [row("Example", "Alternative", "other")], "species", "Archive")
        self.assertEqual(self.term, before)

    def test_column_mapping_keeps_research_identity(self):
        values = dict(self.values)
        values["attested"] = values.pop("name")
        self.store([values])
        rows = cite.load(self.path, {"name": "attested"})
        self.assertEqual(rows[0]["name"], "Target")
        self.assertEqual(rows[0]["finding"].record_id, self.values["record_id"])
        merge([self.term], rows, "species", "Archive")
        tb.save(self.glossary, [self.term], write_notes=True)
        stored = (self.root / "notes" / "terms.toml").read_bytes()
        values.update(attested="Replacement", evidence="Changed quotation.")
        self.store([values])
        updated = tb.load(self.glossary)[0]
        self.assertIn("Archive: Replacement", updated.notes)
        self.assertIn("Changed quotation.", updated.notes)
        self.assertNotIn("Exact quotation.", updated.note_records[-1].get("facts", []))
        self.assertEqual(updated.translation, self.term.translation)
        self.assertEqual(updated.forms, self.term.forms)
        tb.save(self.glossary, [updated], write_notes=True)
        self.assertEqual((self.root / "notes" / "terms.toml").read_bytes(), stored)

    def test_independent_notes_cannot_be_overwritten_or_dropped_during_generated_saves(self):
        note = {"id": "term:independent", "subject": {"kind": "term", "id": self.term.id},
                "scope": {"kind": "language", "language": "fixture"},
                "facts": ["Independent guidance."], "status": "unreviewed"}
        self.term.notes = ""
        tb.attach_notes(self.term, [note])
        merge([self.term], self.store(), "species", "Archive")
        tb.save(self.glossary, [self.term], write_notes=True)
        path = self.root / "notes" / "terms.toml"
        document = structured.read_notes(path)
        document["note"][0]["facts"] = ["Updated independent guidance."]
        structured.write_notes(path, document)
        before = self.glossary.read_bytes(), path.read_bytes()
        with self.assertRaisesRegex(ValueError, "structured notes changed"):
            tb.save(self.glossary, [self.term], write_notes=True)
        with self.assertRaisesRegex(ValueError, "unreferenced notes"):
            tb.save(self.glossary, [], write_notes=True)
        self.assertEqual((self.glossary.read_bytes(), path.read_bytes()), before)

    def test_label_removal_preserves_editable_cells_and_reruns(self):
        rows = self.store()
        for before, after in (
            ("Summary. | Archive: Old summary. | Last observation.", "Summary. | Last observation."),
            ("Archive: Old summary.", ""),
            ("Independent guidance.", "Independent guidance."),
            ("First. |  | Last.", "First. |  | Last."),
            ("First. |  | Archive: Old summary. | Last.", "First. |  | Last."),
        ):
            with self.subTest(notes=before):
                term = copy.deepcopy(self.term)
                term.notes = before
                merge([term], rows, "species", "Archive")
                self.assertEqual(term.editable_notes, after)
                self.assertTrue(all(research.is_reference(note) for note in term.note_records))
                tb.save(self.glossary, [term], write_notes=True)
                first = self.glossary.read_bytes(), (self.root / "notes" / "terms.toml").read_bytes()
                loaded = tb.load(self.glossary)[0]
                self.assertEqual(loaded.editable_notes, after)
                merge([loaded], rows, "species", "Archive")
                tb.save(self.glossary, [loaded], write_notes=True)
                self.assertEqual((self.glossary.read_bytes(), (self.root / "notes" / "terms.toml").read_bytes()), first)
                edited, renames, refused = termedit.apply_changes([loaded], [{"id": loaded.id,
                    "base": {"notes": after}, "changes": {"notes": "Edited guidance."}}])
                self.assertEqual((renames, refused), ([], []))
                self.assertEqual(termedit.values(edited[0])["notes"], "Edited guidance.")
                tb.save(self.glossary, edited)
                self.assertEqual(tb.load(self.glossary)[0].editable_notes, "Edited guidance.")

    def test_independent_structured_prose_retains_the_cell_before_replacing_it(self):
        merge([self.term], self.store(), "species", "Archive")
        cell = self.term.editable_notes
        note = {"id": "term:independent", "subject": {"kind": "term", "id": self.term.id},
                "scope": {"kind": "language", "language": "fixture"},
                "facts": ["Independent structured guidance."], "status": "unreviewed"}
        tb.attach_notes(self.term, [note])
        self.assertIsNone(self.term.editable_notes)
        legacy = [record for record in self.term.note_records if record["id"].startswith("term-legacy:")]
        self.assertEqual([record["facts"] for record in legacy], [[cell]])
        tb.save(self.glossary, [self.term], write_notes=True)
        self.assertEqual(tb.load(self.glossary), [self.term])

    def test_unknown_or_changed_research_references_are_rejected(self):
        merge([self.term], self.store(), "species", "Archive")
        tb.save(self.glossary, [self.term], write_notes=True)
        self.values["record_id"] = "finding:changed"
        self.note["subject"]["id"] = "finding:changed"
        self.store()
        with self.assertRaisesRegex(ValueError, "unknown research record"):
            tb.load(self.glossary)
        self.values["record_id"] = "finding:first"
        self.note["subject"]["id"] = "finding:first"
        self.values["note_ids"] = "research-note:changed"
        self.note["id"] = "research-note:changed"
        self.store()
        with self.assertRaisesRegex(ValueError, "research references changed"):
            tb.load(self.glossary)


if __name__ == "__main__":
    unittest.main()
