from pathlib import Path
import csv
import tempfile
import unittest

from gen3tl.web import editordata
from gen3tl.check import qa
from gen3tl.lang.langpack import load_styles
from gen3tl.check.layout import Measure
from gen3tl.catalog.po import PoEntry, write
from gen3tl.lang.termbase import Term
from gen3tl.lang import language
from gen3tl.core import notes as structured
from editordata_support import PROF, setUpModule

class ResearchSubjectsTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(language.use(language.Language("fixture", {}, Path("."))))

    def test_generated_term_references_keep_only_cell_prose_editable(self):
        from gen3tl.lang import research, termbase
        term = Term("term:one", "term", "Example", "Target", notes="Independent guidance.")
        row = research.Row({"record_id": "finding:first", "source_text": "Example", "name": "Target",
                            "source": "reference", "dub": "Episode 1"})
        reference = research.term_note(term.id, row, "research-citation", [], citation={"label": "Archive"})
        termbase.attach_notes(term, [reference], findings={row.record_id: row})
        before = editordata.term_data(term)
        self.assertEqual(before["notes"], "Independent guidance.")
        self.assertEqual(before["note_ids"], [reference["id"]])
        self.assertNotIn("Archive", before["notes"])
        doc = {"schema": editordata.SCHEMA, "language": language.current().code, "entries": []}
        editordata.attach_notes(doc, PROF, {}, terms=[term], wording=[])
        index, _ = editordata.split(doc, None, {})
        self.assertEqual(index["notes"]["summaries"][reference["id"]],
                         ["Archive: Target (reference; Episode 1); gender not shown"])
        self.assertNotIn("facts", index["notes"]["note"][0])
        self.assertEqual(editordata.term_data(term), before)
        term.notes_cell = ""
        term.refresh_notes()
        self.assertEqual(editordata.term_data(term)["notes"], "")

    def test_research_subjects_retain_table_and_row_names_in_split_index(self):
        code = language.current().code
        note = {"id": "note:finding", "subject": {"kind": "research", "id": "research:one"},
                "scope": {"kind": "language", "language": code}, "status": "unreviewed",
                "guidance": ["Prefer the attested name."], "facts": ["The source uses this spelling."]}
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            research = folder / "research"
            research.mkdir()
            rows = [("moves.tsv", {"record_id": "research:one", "note_ids": note["id"], "source_text": "Twin Tap"}),
                    ("roles.tsv", {"record_id": "research:two", "note_ids": "", "subject": "Night Crew"}),
                    ("legacy.tsv", {"source_text": "Old name", "notes": "Legacy statement."})]
            for filename, row in rows:
                with (research / filename).open("w", encoding="utf-8", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=list(row), delimiter="\t")
                    writer.writeheader()
                    writer.writerow(row)
            structured.write_notes(research / "moves.notes.toml", {"schema": structured.SCHEMA, "note": [note]})
            before = {path.name: path.read_bytes() for path in research.iterdir()}
            doc = {"schema": editordata.SCHEMA, "language": code, "entries": []}
            editordata.attach_notes(doc, PROF, {}, folder=folder, wording=[])
            index, chunks = editordata.split(doc, None, {})
            self.assertEqual(index["research"], {
                "research:one": {"table": "moves.tsv", "name": "Twin Tap"},
                "research:two": {"table": "roles.tsv", "name": "Night Crew"},
            })
            self.assertIn(note, index["notes"]["note"])
            self.assertEqual(chunks, [])
            self.assertEqual({path.name: path.read_bytes() for path in research.iterdir()}, before)

class NothingToTranslateTest(unittest.TestCase):
    """An entry without English has nothing to translate."""

    PO = [PoEntry("s0000100", "Hi.", "Привет."),                       # approved
          PoEntry("s0000200", "", ""),                                 # an unused slot
          PoEntry("s0000300", "Wait.", ""),                            # untranslated
          PoEntry("s0000400", "", "", flags=["fuzzy"]),                # flagged: not done
          PoEntry("s0000500", "", "Слот.")]                            # a translation of nothing

    def test_status_progress_and_qa(self):
        from gen3tl.catalog import progress as pg
        from gen3tl.catalog.catalog import nothing_to_translate
        self.assertEqual([editordata.status(e) for e in self.PO],
                         ["approved", "nothing", "untranslated", "untranslated", "approved"])
        self.assertEqual([nothing_to_translate(e) for e in self.PO], [False, True, False, False, False])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "po"
            write(root / "maps" / "town.po", self.PO, {})
            rows = pg.count(root, {}, set())
            self.assertEqual([(r.approved, r.total) for r in rows], [(2, 4)])   # nothing is left out
            inv = {"entries": [{"id": e.msgctxt, "offset": int(e.msgctxt[1:], 16), "length": 8, "kind": "pointer",
                                "category": "script", "mode": "field", "text": e.msgid, "table": None,
                                "index": None, "refs": [{"at": 0x10, "kind": "script:loadpointer"}], "flags": []}
                               for e in self.PO]}
            cm = load_styles("ru", PROF)["unbound"].charmap()
            gate = qa.run({"layout": {"dialogue_width": 208}}, inv, root, cm, Measure(lambda b: 6, {}), set())
        self.assertEqual(gate.checked, 2)   # nothing to check in the slots
        self.assertFalse([f for f in gate.findings if f.id in ("s0000200", "s0000400")])

    def test_no_problems_in_the_split_form(self):
        class Checker:
            seen = []

            def check(self, e, text):
                self.seen.append(e["id"])
                return {"problems": []}
        doc = {"entries": [{"id": sid, "status": st, "translation": ""} for sid, st in (
            ("s0000100", "approved"), ("s0000200", "nothing"), ("s0000300", "untranslated"), ("s0000500", "fuzzy"))]}
        ck = Checker()
        self.assertEqual(set(editordata.data_problems(doc, ck)), {"s0000100", "s0000500"})
        self.assertEqual(ck.seen, ["s0000100", "s0000500"])


class ExplanationTest(unittest.TestCase):
    """Inserted widths, slot words and reach studies are coded explanations."""

    def test_inserted_widths_name_their_source_by_code(self):
        from gen3tl.check.layout import Limit
        from gen3tl.check.provenance import code
        cm = load_styles("ru", PROF)["unbound"].charmap()
        measure = Measure(lambda b: 6, {"PLAYER": 42, "STR_VAR_2": 60})
        word = code("slot_word", id="s0000200", text="'Зелье'")
        lim = Limit("single", 100, 1, placeholders={"STR_VAR_1": 30, "STR_VAR_3": 12},
                    slot_sources={"STR_VAR_1": word})
        rows = editordata.inserted("{STR_VAR_1} {STR_VAR_3} {PLAYER} {STR_VAR_2} {B_BUFF1}", lim, measure, cm,
                                   frozenset({"STR_VAR_2"}))
        self.assertEqual([(row["token"], row["source"]) for row in rows], [
            ("{STR_VAR_1}", word), ("{STR_VAR_3}", code("slot_context")), ("{PLAYER}", code("slot_profile")),
            ("{STR_VAR_2}", code("slot_profile_estimate")), ("{B_BUFF1}", code("slot_default"))])

    def test_the_word_a_slot_source_names(self):
        from gen3tl.check.provenance import code
        word = code("slot_word", id="s0000200", text="'Зелье'")
        apostrophe = code("slot_word", id="table:species/83", text=repr("Фарфетч'д"))   # a repr in double quotes
        for source, named in ((word, "s0000200"), (code("slot_untranslated", id="items:3"), "items:3"),
                              (code("slot_empty", id="hash:0000abcd"), "hash:0000abcd"),
                              (code("slot_record", kind="item", word=word), "s0000200"),
                              (code("slot_table", table="items", word=word), "s0000200"),
                              (code("slot_reasons", source=word, reasons=[code("slot_no_fill")]), "s0000200"),
                              (apostrophe, "table:species/83"),
                              (code("slot_table", table="species", word=apostrophe), "table:species/83"),
                              (code("slot_record", kind="species", word=apostrophe), "table:species/83"),
                              (code("slot_record", kind="std_text", word=word), None),
                              (code("slot_number", value=5), None), (code("slot_profile"), None),
                              (code("slot_word", id="the bag", text="'x'"), None)):
            with self.subTest(source=source):
                self.assertEqual(editordata.slot_word(source), named)

    def test_keys_replace_inventory_ids_in_every_text_of_an_explanation(self):
        from gen3tl.catalog.keys import Aliases
        from gen3tl.check.provenance import code
        aliases = Aliases([("hash:00000001", 0x10, "s0000010"), ("table:items/3", None, "items:3")])
        source = code("slot_reasons", source=code("slot_table", table="items", word=code(
            "slot_word", id="items:3", text="'s0000010'")), reasons=[code("slot_unnamed", kinds=["s0000010"])])
        self.assertEqual(editordata.rekeyed(source, aliases), code("slot_reasons", source=code(
            "slot_table", table="items", word=code("slot_word", id="table:items/3", text="'hash:00000001'")),
            reasons=[code("slot_unnamed", kinds=["hash:00000001"])]))
        self.assertEqual(source["source"]["word"]["id"], "items:3")   # the source stays as it was

    def test_a_reach_study_is_coded_with_its_root(self):
        study = {"strings": {"s0000100": {"class": "reachable", "evidence": [{"kind": "pool", "at": "0x10"}]},
                             "s0000200": {"class": "possibly reachable"}}}
        self.assertEqual(editordata.reach_data("s0000100", [], study), {"class": "reachable", "source": "reach study",
                         "evidence": {"code": "reach_study_root", "class": "reachable", "root": "pool 0x10"}})
        self.assertEqual(editordata.reach_data("s0000200", [], study)["evidence"],
                         {"code": "reach_study", "class": "possibly reachable"})
        self.assertEqual(editordata.reach_data("s0000300", [], study),
                         {"class": "reachable", "source": "not studied", "evidence": ""})
