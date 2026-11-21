import sys
import tempfile
import unittest
from pathlib import Path

from test_roots import TOOLKIT_ROOT

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.lang.fill import fill_tables, labels_from_catalog  # noqa: E402
from gen3tl.catalog.po import PoEntry, read, write  # noqa: E402
from gen3tl.lang.termbase import Term  # noqa: E402
from project_fixture import select_language  # noqa: E402


def setUpModule():
    select_language("ru")


def term(cat, en, ru, label="", status="approved", tid=None):
    return Term(id=tid or f"{cat}:{en.lower()}", category=cat, source_text=en, translation=ru, label=label, status=status)


class FillTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.po = Path(self.tmp.name) / "po"

    def tearDown(self):
        self.tmp.cleanup()

    def entries(self, name):
        return {e.msgctxt: e for e in read(self.po / "tables" / f"{name}.po")[1]}

    def test_fill_label_blank_missing_and_keep(self):
        write(self.po / "tables" / "species.po", [
            PoEntry("species:0", "??????????"),
            PoEntry("species:1", "Jigglypuff", flags=["fuzzy"]),
            PoEntry("species:2", "   "),
            PoEntry("species:3", "Pikachu", "Пика"),
            PoEntry("species:4", "Missingno"),
            PoEntry("species:5", "Bulbasaur"),
        ], {})
        terms = [term("species", "Jigglypuff", "Джигглипафф", label="Джиглипафф"),
                 term("species", "Pikachu", "Пикачу"),
                 term("species", "Bulbasaur", "Бульбазавр", status="proposed")]
        reps = fill_tables([{"name": "species", "glossary": ["species"]}], self.po, terms)
        r = reps[0]
        self.assertEqual((r.filled, r.blank, r.kept), (1, 1, 1))
        self.assertEqual([m.split("\t")[1] for m in r.missing], ["??????????", "Missingno", "Bulbasaur"])
        es = self.entries("species")
        self.assertEqual(es["species:1"].msgstr, "Джиглипафф")  # field form
        self.assertFalse(es["species:1"].fuzzy)
        self.assertIn("filled from glossary species:jigglypuff", es["species:1"].translator)
        self.assertEqual(es["species:2"].msgstr, "   ")       # blank keeps its bytes
        self.assertEqual(es["species:3"].msgstr, "Пика")      # existing translation kept
        self.assertEqual(es["species:5"].msgstr, "")          # only approved terms fill

    def test_overwrite_and_category_order(self):
        write(self.po / "tables" / "trainer_names.po", [
            PoEntry("trainer_names:0", "Grunt", "Old"),
            PoEntry("trainer_names:1", "Jax"),
        ], {})
        terms = [term("speaker", "Grunt", "Агент"), term("trainer-class", "Grunt", "Боевик"),
                 term("character", "Jax", "Джекс"), term("speaker", "Jax", "Не тот")]
        tables = [{"name": "trainer_names", "glossary": ["character", "speaker", "trainer-class"]}]
        fill_tables(tables, self.po, terms, overwrite=True)
        es = self.entries("trainer_names")
        self.assertEqual(es["trainer_names:0"].msgstr, "Агент")
        self.assertEqual(es["trainer_names:1"].msgstr, "Джекс")

    def test_table_without_glossary_or_po_is_skipped(self):
        self.assertEqual(fill_tables([{"name": "moves"}, {"name": "items", "glossary": ["item"]}], self.po, []), [])

    def test_personal_name_never_falls_back_to_generic_role(self):
        write(self.po / "tables/trainer_names.po", [PoEntry("trainer_names:1", "Guy")], {})
        tables = [{"name": "trainer_names", "glossary": ["character"]}]
        generic = term("speaker", "Guy", "Мужчина")
        report = fill_tables(tables, self.po, [generic])[0]
        self.assertEqual(report.missing, ["trainer_names:1\tGuy"])
        self.assertEqual(self.entries("trainer_names")["trainer_names:1"].msgstr, "")
        fill_tables(tables, self.po, [generic, term("character", "Guy", "Гай")])
        self.assertEqual(self.entries("trainer_names")["trainer_names:1"].msgstr, "Гай")

    def test_reviewed_role_binding_survives_refill_and_new_homonym(self):
        write(self.po / "tables/trainer_names.po", [
            PoEntry("trainer_names:1", "Scientist", "Учёная",
                    translator=["source: Role context", "filled from glossary speaker:scientist~2"]),
            PoEntry("trainer_names:2", "Scientist", "Учёный",
                    translator=["filled from glossary speaker:scientist"]),
        ], {})
        terms = [term("speaker", "Scientist", "Учёный"),
                 term("speaker", "Scientist", "Учёная", tid="speaker:scientist~2"),
                 term("character", "Scientist", "Сайентист")]
        tables = [{"name": "trainer_names", "glossary": ["character"]}]
        fill_tables(tables, self.po, terms, overwrite=True)
        es = self.entries("trainer_names")
        self.assertEqual(es["trainer_names:1"].msgstr, "Учёная")
        self.assertEqual(es["trainer_names:2"].msgstr, "Учёный")
        self.assertEqual(es["trainer_names:1"].source_note, "Role context")

    def test_ambiguous_or_stale_bindings_fail_before_write(self):
        tables = [{"name": "trainer_names", "glossary": ["character"]}]
        terms = [term("character", "Robin", "Робин"),
                 term("character", "Robin", "Робин", tid="character:robin~2"),
                 term("speaker", "Guy", "Мужчина")]
        cases = [([], "ambiguous"),
                 (["filled from glossary speaker:guy"], "source differs"),
                 (["filled from glossary character:missing"], "not an approved"),
                 (["filled from glossary character:robin", "filled from glossary character:robin~2"], "multiple")]
        for comments, error in cases:
            with self.subTest(comments=comments):
                path = self.po / "tables/trainer_names.po"
                write(path, [PoEntry("trainer_names:1", "Robin", translator=comments)], {})
                before = path.read_bytes()
                with self.assertRaisesRegex(ValueError, error):
                    fill_tables(tables, self.po, terms, overwrite=True)
                self.assertEqual(path.read_bytes(), before)

    def test_labels_from_catalog(self):
        write(self.po / "tables" / "species.po", [
            PoEntry("species:1", "Jigglypuff"), PoEntry("species:2", "Pikachu"), PoEntry("species:3", "Eevee")], {})
        terms = [term("species", "Jigglypuff", "Джигглипафф", label="Джиглипафф"),
                 term("species", "Pikachu", "Пикачу", label="Пикач"), term("species", "Eevee", "Иви")]
        fill_tables([{"name": "species", "glossary": ["species"]}], self.po, terms)
        hdr, es = read(self.po / "tables" / "species.po")
        for e in es:
            e.msgstr = {"species:1": "Джиглипаф", "species:2": "Пикачу"}.get(e.msgctxt, e.msgstr)
        write(self.po / "tables" / "species.po", es, hdr)
        changes = labels_from_catalog(self.po, terms, ["species:1", "species:2", "species:3"])
        self.assertEqual(sorted(changes), [("species:jigglypuff", "Джиглипафф", "Джиглипаф"),
                                           ("species:pikachu", "Пикач", "")])   # the full name needs no label
        self.assertEqual(terms[0].label, "Джиглипаф")


if __name__ == "__main__":
    unittest.main()
