import csv
import unittest

from gen3tl.catalog.po import KEY_ONLY, SOURCE_FIELD
import submission_support
from submission_support import form

class KeyOnlySubmissionTest(submission_support.SubmissionBehavior, unittest.TestCase):
    """The same submissions on key-only catalogs without English."""
    HEADER = {SOURCE_FIELD: KEY_ONLY}
    IMPORT_REFUSAL = "a key-only catalog without its English source catalog"   # web import reads the English

    def test_the_catalogs_hold_no_english(self):
        self.glossary()
        self.assertFalse((self.root / "build/catalog.pot").exists())
        for name in ("map.po", "gym.po"):
            self.assertNotIn("The Gym is open.", (self.po / name).read_text(encoding="utf-8"))
            self.assertNotIn("Fit.", (self.po / name).read_text(encoding="utf-8"))

    def test_the_english_of_an_edit_is_judged_by_its_digest(self):
        record = {"id": "s0000100", "source_text": "Fit.", "base": "Old text", "translation": "New text"}
        self.assertEqual(self.submit(form(self.export([record])), "--check"), 0)
        self.assertEqual(self.submit(form(self.export([{**record, "source_text": "Fit!"}]))), 1)
        self.assertIn("s0000100: editor source_text differs from the current catalog", self.answer("comment.md"))
        (self.root / "translation/editor-inputs.json").unlink()
        self.assertEqual(self.submit(form(self.export([record]))), 1)
        self.assertIn("s0000100: editor source_text cannot be checked without the English source catalog or the snapshot",
                      self.answer("comment.md"))
        self.assertEqual((self.po / "map.po").read_bytes(), self.original)

    def test_the_index_refresh_keeps_its_facts_and_marks_what_needs_the_english(self):
        self.glossary()
        body = form(self.export([{"id": "s0000100", "base": "Old text", "translation": "Gim tut."}]))
        self.assertEqual(self.submit(body), 0)
        with (self.root / "translation/term-index.tsv").open() as stream:
            rows = [(r["string"], r["source_names_term"], r["use"]) for r in csv.DictReader(stream, delimiter="\t")]
        self.assertEqual(rows, [("s0000100", "unknown", "unknown"), ("s0000400", "yes", "yes"),
                                ("s0000500", "no", "form only")])
        # a term edit waits for the index the English gives
        self.assertEqual(self.submit(form(self.export([], terms=[self.GYM]))), 1)
        self.assertIn("the term index does not tell whether the English names term:gym in s0000100",
                      self.answer("comment.md"))

    def test_the_pr_body_lists_the_unknown_rows_the_refresh_wrote(self):
        self.glossary()
        body = form(self.export([{"id": "s0000100", "base": "Old text", "translation": "Gim tut."},
                                 {"id": "s0000200", "base": "Kept text", "translation": "Gim box."}]))
        self.assertEqual(self.submit(body), 0)
        pr = self.answer("pr.md")
        self.assertIn("Unknown term index rows:", pr)
        self.assertIn("- `s0000100` (`term:gym`)", pr)
        self.assertIn("- `s0000200` (`term:gym`)", pr)

    def test_term_edits_need_a_current_term_index(self):
        glossary, before, lines = self.glossary()
        index = self.root / "translation/term-index.tsv"
        rows = index.read_text(encoding="utf-8").splitlines()
        index.write_text("\n".join(r for r in rows if "\ts0000500\t" not in r) + "\n", encoding="utf-8")
        self.assertEqual(self.submit(form(self.export([], terms=[self.GYM]))), 1)
        self.assertIn("the term index does not tell whether the English names term:gym in s0000500; "
                      "regenerate it with the English: gen3tl lang index", self.answer("comment.md"))
        index.unlink()
        self.assertEqual(self.submit(form(self.export([], terms=[self.GYM]))), 1)
        self.assertIn("term edits read the uses of a term with the English or the term index; test/xx has neither "
                      "translation/term-index.tsv nor its English", self.answer("comment.md"))
        self.assertEqual((glossary.read_bytes(), (self.po / "gym.po").read_bytes()), (before, lines))
