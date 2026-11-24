import dataclasses
import json
import tempfile
import unittest
from pathlib import Path

import editordata_support as fx
from gen3tl.web import editordata
from gen3tl.lang import language, termedit
from gen3tl.lang import termbase as tb
from gen3tl.core import notes as structured
from gen3tl.web.editorcheck import BUNDLE
from gen3tl.catalog.po import PoEntry
from test_renamecheck import ELIXIR, MAX_ELIXIR, NECTAR, GIM, STADION, forms
from project_fixture import select_language

def setUpModule():
    select_language("ru")

def gym(**kw) -> tb.Term:
    return tb.Term("term:gym", "term", "Gym", "гим", declension="manual", forms=dict(GIM), **kw)

class RecordTest(unittest.TestCase):
    """The export record and its application to the glossary."""

    def test_rename_of(self):
        old = gym(label="Гим")
        self.assertIsNone(termedit.rename_of(old, termedit.changed(old, {"notes": "x", "sources": "y"})))
        r = termedit.rename_of(old, termedit.changed(old, {"translation": "стадион", "forms": STADION}))
        self.assertEqual((r.old, r.translation, r.gender), (old, "стадион", ""))
        self.assertEqual(r.forms, {**STADION, "label": "Гим"})
        r = termedit.rename_of(old, termedit.changed(old, {"translation": "стадион", "forms": STADION, "label": ""}))
        self.assertEqual(r.forms["label"], "стадион")   # the old label becomes the translation
        r = termedit.rename_of(old, termedit.changed(old, {"gender": "female"}))
        self.assertEqual((r.forms, r.gender), (old.all_forms(), "female"))

    def test_apply_changes(self):
        ether = tb.Term("item:ether", "item", "Ether", "Эфир")
        terms = [gym(sources="game-text", variants="Former @ game-text"), ether]
        rec = {"id": "term:gym", "source_text": "Gym",
               "base": {"translation": "гим", "forms": GIM, "notes": "", "sources": "game-text"},
               "changes": {"translation": "стадион", "forms": STADION, "notes": "Gym: стадион",
                           "sources": "game-text, own"}}
        out, renames, refused = termedit.apply_changes(terms, [rec, {
            "id": "item:ether", "base": {"sources": ""}, "changes": {"sources": "bulbapedia"}}])
        self.assertEqual(refused, [])
        self.assertEqual((out[0].translation, out[0].forms, out[0].notes), ("стадион", STADION, "Gym: стадион"))
        self.assertEqual(out[0].variants, "Former @ game-text | гим @ game-text, own")
        self.assertNotIn("replaced by rename", out[0].variants)
        self.assertEqual(out[1].sources, "bulbapedia")
        self.assertEqual(terms[0].translation, "гим")   # the input rows stay
        self.assertEqual(terms[0].variants, "Former @ game-text")
        self.assertEqual([(r.old.translation, r.translation) for r in renames], [("гим", "стадион")])   # notes: no rename
        self.assertEqual(renames[0].old.all_forms(), gym().all_forms())

    def test_the_record_carries_the_included_entries(self):
        # form-only occurrences renamed only where included
        rec = {"id": "term:gym", "base": {"translation": "гим", "forms": GIM},
               "changes": {"translation": "стадион", "forms": STADION}, "include": ["s2", "s1"]}
        out, renames, refused = termedit.apply_changes([gym()], [rec])
        self.assertEqual((refused, renames[0].include), ([], frozenset({"s1", "s2"})))
        out, renames, refused = termedit.apply_changes([gym()], [{**rec, "include": None}])
        self.assertEqual(renames[0].include, frozenset())

    def test_rename_records_leave_enclosing_terms_unchanged(self):
        rec = {"id": ELIXIR.id, "base": {"translation": "Эликсир", "forms": ELIXIR.forms, "label": "Эл."},
               "changes": {"translation": "Нектар", "forms": NECTAR, "label": "Нект."}, "include": ["s1"]}
        terms = [ELIXIR, MAX_ELIXIR]
        out, renames, refused = termedit.apply_changes(terms, [rec])
        self.assertEqual(refused, [])
        self.assertEqual([t.translation for t in out], ["Нектар", "Эликсир макс"])
        self.assertEqual(out[1], MAX_ELIXIR)
        changes = termedit.apply_renames({"s1": "Эликсир макс и Эликсир"}, renames,
                                         {"s1": "Max Elixir and Elixir"}, tb.Senses(terms))
        self.assertEqual(changes["s1"]["after"], "Эликсир макс и Нектар")

    def test_refused_records(self):
        terms = [gym()]
        cases = [
            ({"id": "term:nope", "base": {}, "changes": {"notes": "x"}}, "term:nope: no such term in the glossary"),
            ({"id": "term:gym", "base": {"translation": "гимн"}, "changes": {"translation": "стадион"}},
             "term:gym: translation is 'гим' in the glossary, the edit was made on 'гимн'"),
            ({"id": "term:gym", "base": {}, "changes": {"notes": "x"}}, "term:gym: notes has no base value"),
            ({"id": "term:gym", "base": {"source_text": "Gym"}, "changes": {"source_text": "Gyms"}},
             "term:gym: source_text cannot be edited"),
        ]
        for rec, why in cases:
            out, renames, refused = termedit.apply_changes(terms, [rec])
            self.assertEqual((refused, renames, out), ([why], [], terms), why)

    def test_applied_rows_save_to_the_glossary(self):
        # the editor export's record, as store.termRecord writes it
        rec = json.loads(json.dumps({
            "id": "term:gym", "source_text": "Gym",
            "base": {"translation": "гим", "label": "", "declension": "auto", "forms": GIM},
            "changes": {"translation": "стадион", "label": "Стад.", "declension": "manual",
                        "forms": {**STADION, "nomn.plur": "стадионы"}}}, ensure_ascii=False))
        terms = [tb.Term("term:gym", "term", "Gym", "гим", forms=dict(GIM))]   # declension auto
        out, renames, refused = termedit.apply_changes(terms, [rec])
        self.assertEqual(refused, [])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "glossary.tsv"
            tb.save(path, out)
            back = tb.load(path)[0]
        self.assertEqual((back.translation, back.label, back.declension, back.forms),
                         ("стадион", "Стад.", "manual", STADION))
        self.assertEqual(renames[0].forms["label"], "Стад.")

    def test_note_only_edit_preserves_other_fields_and_legacy_format(self):
        old = gym(notes="Recorded context", sources="reference", status="reviewed", label="Short")
        record = {"id": old.id, "base": {"notes": old.notes, "sources": old.sources},
                  "changes": {"notes": "Revised context", "sources": "reference, context"}}
        rows, renames, refused = termedit.apply_changes([old], [record])
        self.assertEqual((renames, refused), ([], []))
        for column in tb.COLUMNS:
            if column not in record["changes"]:
                self.assertEqual(getattr(rows[0], column), getattr(old, column), column)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "glossary.tsv"
            tb.save(path, rows)
            self.assertEqual(tb.load(path), rows)
            self.assertNotIn("note_ids", path.read_text(encoding="utf-8").splitlines()[0])
            self.assertFalse((path.parent / "notes").exists())
        self.assertEqual((old.notes, old.sources), ("Recorded context", "reference"))
        self.assertEqual(termedit.apply_changes(rows, [record])[0], rows)
        self.assertTrue(termedit.apply_changes(rows, [record])[2])

    def test_migrated_note_is_readable_and_protected_during_term_edits(self):
        term = gym(note_ids=("term-context",), status="reviewed")
        note = {"id": "term-context", "subject": {"kind": "term", "id": term.id},
                "scope": {"kind": "language", "language": language.current().code},
                "facts": ["Recorded context"], "status": "unreviewed"}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "glossary.tsv"
            notes_path = path.parent / "notes" / "terms.toml"
            structured.write_notes(notes_path, {"schema": structured.SCHEMA, "note": [note]})
            original = notes_path.read_bytes()
            tb.save(path, [term])
            old = tb.load(path)[0]
            self.assertIn("Recorded context", termedit.values(old)["notes"])
            data = editordata.term_data(old)
            # exports link the note record instead of its text
            self.assertNotIn("notes", data)
            self.assertEqual(data["note_ids"], ["term-context"])
            record = {"id": old.id, "base": {"notes": old.notes}, "changes": {"notes": "Replacement"}}
            rows, renames, refused = termedit.apply_changes([old], [record])
            self.assertEqual((rows, renames), ([old], []))
            self.assertIn("edit structured notes", refused[0])
            with self.assertRaisesRegex(ValueError, "edit structured notes"):
                termedit.changed(old, record["changes"])
            rows, renames, refused = termedit.apply_changes([old], [{
                "id": old.id, "base": {"sources": old.sources}, "changes": {"sources": "reference"}}])
            self.assertEqual((renames, refused), ([], []))
            tb.save(path, rows)
            self.assertEqual(tb.load(path)[0].note_records, (note,))
            self.assertEqual(notes_path.read_bytes(), original)
            renamed = termedit.changed(rows[0], {"translation": "Another name"})
            tb.save(path, [renamed])
            self.assertEqual(tb.load(path)[0].note_ids, old.note_ids)
            self.assertEqual(notes_path.read_bytes(), original)

    def test_migrated_rows_preserve_fields_without_projected_notes(self):
        original = gym(notes="First line.\nSecond line.", note_ids=("term-context",), sources="reference",
                       variants="Alternative", confidence="checked", address="formal", status="reviewed")
        row = {"id": original.id, **editordata.term_data(original), "note_ids": list(original.note_ids),
               **{key: getattr(original, key) for key in ("declension", "sources", "variants", "confidence")}}
        for projected in (True, False):
            with self.subTest(projected=projected):
                data = {key: value for key, value in row.items() if projected or key != "notes"}
                term = termedit.term_row(data)
                for field in dataclasses.fields(original):
                    if field.name != "notes":
                        self.assertEqual(getattr(term, field.name), getattr(original, field.name), field.name)
                values = termedit.values(term)
                self.assertEqual({k: v for k, v in values.items() if k != "notes"},
                                 {k: v for k, v in termedit.values(original).items() if k != "notes"})
                changed, renames, refused = termedit.apply_changes([term], [{"id": term.id,
                    "base": {"sources": "reference"}, "changes": {"sources": "context"}}])
                self.assertEqual((renames, refused), ([], []))
                self.assertEqual(changed, [dataclasses.replace(term, sources="context")])
                with self.assertRaisesRegex(ValueError, "edit structured notes"):
                    termedit.changed(term, {"notes": "Legacy replacement"})

    def test_stale_migrated_note_drafts_are_refused_without_losing_source_guards(self):
        original = gym(notes="First line.\nSecond line.", note_ids=("term-context",), sources="reference")
        for notes in (original.notes, ""):
            with self.subTest(notes=notes):
                term = dataclasses.replace(original, notes=notes)
                for record, reason in (({"base": {"notes": "Old projection", "sources": "reference"},
                                        "changes": {"notes": "Saved note edit", "sources": "context"}},
                                       "edit structured notes"),
                                       ({"base": {"sources": "outdated"}, "changes": {"sources": "context"}},
                                        "edit was made on 'outdated'")):
                    rows, renames, refused = termedit.apply_changes([term], [{"id": term.id, **record}])
                    self.assertEqual((rows, renames), ([term], []))
                    self.assertIn(reason, refused[0])

from editorstore_support import decoded

class TermsDataTest(unittest.TestCase):
    """terms.json: the terms, the entries that use them and the slots their words fill."""

    TERMS = [tb.Term("move:x", "move", "-", "-", declension="fixed", forms={k: "-" for k in forms(["-"] * 6, ["-"] * 6)}),
             tb.Term("move:pound", "move", "Pound", "Тяжёлый удар",
                     forms={"nomn.sing": "Тяжёлый удар", "gent.sing": "Тяжёлого удара"}),
             tb.Term("item:bag", "item", "Bag", "Сумка")]
    CATALOG = {
        "s0000001": (Path("a.po"), PoEntry("s0000001", "Use Pound - now!", "Применяй Тяжёлый удар - сейчас!")),
        "s0000002": (Path("a.po"), PoEntry("s0000002", "No Pound in {STR_VAR_1}", "Нет тяжёлого удара в {STR_VAR_1}")),
        "s0000003": (Path("a.po"), PoEntry("s0000003", "Pound", "")),
    }
    DOC = {"entries": [
        {"id": "s0000001", "terms": ["move:pound"], "context": {"words": []}, "window": None},
        {"id": "s0000002", "terms": ["move:pound"],
         "context": {"words": [{"slot": "{STR_VAR_1}", "id": "s0000004", "source_text": "Bag", "translation": "Сумка"}]},
         "window": {"slot_sources": {"STR_VAR_1": {"code": "slot_record", "kind": "item", "word": {
             "code": "slot_word", "id": "items:26", "text": "'Сумка'"}}}}},
        {"id": "s0000003", "terms": ["move:pound"], "context": {"words": []},
         "window": {"slot_sources": {"STR_VAR_1": {"code": "slot_record_number", "digits": 2, "value": 10}}}},
    ]}

    def test_terms_and_uses(self):
        data = json.loads(json.dumps(editordata.terms_data(self.TERMS, self.DOC, self.CATALOG), ensure_ascii=False))
        self.assertEqual(set(data), {"schema", "terms", "slots"})
        self.assertEqual([t["id"] for t in data["terms"]], ["move:x", "move:pound", "item:bag"])   # glossary order
        pound = data["terms"][1]
        self.assertEqual(set(pound), {"id", "category", "source_text", "translation", "label", "gender", "declension",
                                      "forms", "sources", "variants", "confidence", "address", "notes", "status",
                                      "in_source", "in_translation", "form_only", "shortened"})
        # a lower-case phrase is a form-only use
        self.assertEqual((pound["in_source"], pound["in_translation"], pound["form_only"]),
                         (["s0000001", "s0000002", "s0000003"], ["s0000001"], ["s0000002"]))
        self.assertEqual((data["terms"][0]["in_translation"], data["terms"][0]["form_only"]), ([], []))   # the dash
        # the words a line's slot receives, in generation order
        self.assertEqual(data["slots"], {"groups": [], "lines": [
            {"line": "s0000002", "slot": "STR_VAR_1", "words": ["s0000004", "items:26"]}]})

    def test_a_slot_word_fills_its_line_in_either_quote_form(self):
        # an apostrophe puts the repr in double quotes
        for text in ("Сумка", "Фарфетч'д"):
            with self.subTest(text=text):
                doc = json.loads(json.dumps(self.DOC))
                doc["entries"][1]["window"]["slot_sources"]["STR_VAR_1"]["word"]["text"] = repr(text)
                data = editordata.terms_data(self.TERMS, doc, self.CATALOG)
                self.assertEqual(data["slots"]["lines"], [
                    {"line": "s0000002", "slot": "STR_VAR_1", "words": ["s0000004", "items:26"]}])

    def test_structured_dependencies_preserve_script_sources(self):
        for dependencies in (["items:26"], ["items:27"], ["items:26", "items:27"]):
            with self.subTest(dependencies=dependencies):
                doc = json.loads(json.dumps(self.DOC))
                doc["entries"][1]["window"]["slot_details"] = {
                    "STR_VAR_1": {"dependencies": ["s0000004", *dependencies]}}
                data = editordata.terms_data(self.TERMS, doc, self.CATALOG)
                self.assertEqual(data["slots"], {"groups": [["s0000004", *dependencies]], "lines": [
                    {"line": "s0000002", "slot": "STR_VAR_1", "words": ["s0000004", "items:26"]},
                    {"line": "s0000002", "slot": "STR_VAR_1", "group": 0}]})

    def test_same_spelled_rows_list_their_own_entries(self):
        # item and noun share the English
        item = tb.Term("item:poke-ball", "item", "Poké Ball", "Покебол", forms={"nomn.sing": "Покебол"})
        noun = tb.Term("term:poke-ball", "term", "Poké Ball", "покебол", forms={"nomn.sing": "покебол"})
        catalog = {"s0000001": (Path("a.po"), PoEntry("s0000001", "Buy a Poké Ball.", "Купи Покебол.")),
                   "s0000002": (Path("a.po"), PoEntry("s0000002", "Throw a Poké Ball!", "Бросай покебол!")),
                   "s0000003": (Path("a.po"), PoEntry("s0000003", "A Poké Ball.", "Шар."))}
        doc = {"entries": [{"id": k, "terms": ["item:poke-ball", "term:poke-ball"], "context": {"words": []},
                            "window": None} for k in catalog]}
        data = editordata.terms_data([item, noun], doc, catalog)
        got = {t["id"]: (t["in_source"], t["in_translation"], t["form_only"]) for t in data["terms"]}
        self.assertEqual(got, {"item:poke-ball": (["s0000001", "s0000003"], ["s0000001"], []),
                               "term:poke-ball": (["s0000002", "s0000003"], ["s0000002"], [])})

    def test_shortened_uses_are_the_label_shown_for_the_full_form(self):
        # a full term whose cut label the catalog shows
        kiss = tb.Term("move:sweet-kiss", "move", "Sweet Kiss", "Сладкий поцелуй", label="Сладкий поц.",
                       forms={"nomn.sing": "Сладкий поцелуй"})
        same = tb.Term("item:bag", "item", "Bag", "Сумка", label="Сумка", forms={"nomn.sing": "Сумка"})
        catalog = {"moves:186": (Path("m.po"), PoEntry("moves:186", "Sweet Kiss", "Сладкий поц.")),
                   "s0000001": (Path("a.po"), PoEntry("s0000001", "Use Sweet Kiss!", "Сладкий поцелуй!")),
                   "s0000002": (Path("a.po"), PoEntry("s0000002", "A Bag.", "Сумка."))}
        doc = {"entries": [{"id": k, "file": "x.po", "status": "approved", "source_text": "", "translation": "",
                            "terms": [], "context": {"words": [], "maps": []}, "window": None,
                            "reach": {"class": "reachable"}} for k in catalog]}
        data = editordata.terms_data([kiss, same], doc, catalog)
        got = {t["id"]: (t["in_translation"], t["shortened"]) for t in data["terms"]}
        self.assertEqual(got, {"move:sweet-kiss": (["moves:186", "s0000001"], ["moves:186"]),
                               "item:bag": (["s0000002"], [])})   # a label equal to the translation is no cut
        rows = {r["id"]: r for r in editordata.split(doc, None, {}, {}, data)[0]["list"]}
        self.assertEqual(rows["moves:186"]["shortened"], ["move:sweet-kiss"])
        self.assertNotIn("shortened", rows["s0000001"])

    def test_split_names_and_writes_the_file(self):
        data = editordata.terms_data(self.TERMS, self.DOC, self.CATALOG)
        doc = {"entries": [], "glossary": {}}
        index, chunks = editordata.split(doc, None, {}, {}, data)
        self.assertEqual(index["terms"], {"path": "terms.json", "count": 3})
        self.assertIsNone(editordata.split(doc, None, {}, {})[0]["terms"])
        with tempfile.TemporaryDirectory() as tmp:
            editordata.write_split(Path(tmp) / "game" / "xx", index, chunks, data)
            written = decoded(Path(tmp) / "game" / "xx")["terms"]
            self.assertEqual(written["terms"], json.loads(json.dumps(data["terms"], ensure_ascii=False)))
            self.assertEqual(written["slots"], {"s0000004": [["s0000002", "STR_VAR_1"]],
                                                "items:26": [["s0000002", "STR_VAR_1"]]})
        self.assertIn("lang/termedit.py", BUNDLE)
        self.assertIn("lang/renamecheck.py", BUNDLE)
