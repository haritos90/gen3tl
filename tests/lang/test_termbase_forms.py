from test_roots import TOOLKIT_ROOT
"""Termbase forms checks and fixtures."""

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.lang import termbase as tb
from termbase_support import HAVE_MORPH, SEEL, setUpModule, table


@unittest.skipUnless(HAVE_MORPH, "pymorphy3 not installed (run with .venv/bin/python)")
class FormsTest(unittest.TestCase):
    def test_phrase_inflection(self):
        f = tb.generate_forms(tb.Term("i", "item", "Fire Stone", "Огненный камень"))
        self.assertEqual(f["gent.sing"], "Огненного камня")
        self.assertEqual(f["accs.sing"], "Огненный камень")      # inanimate: accusative = nominative
        self.assertEqual(f["ablt.sing"], "Огненным камнем")
        f = tb.generate_forms(tb.Term("m", "move", "Swords Dance", "Танец мечей"))
        self.assertEqual(f["ablt.sing"], "Танцем мечей")          # genitive dependent unchanged
        f = tb.generate_forms(tb.Term("p", "species", "Pikachu", "Пикачу", declension="fixed"))
        self.assertEqual(set(f.values()), {"Пикачу"})

    def test_category_rules(self):
        cases = [
            ("term", "гим-лидер", "", "accs.sing", "гим-лидера"),            # animacy from the last hyphen part
            ("proper-noun", "Чувак-рыбак", "", "ablt.sing", "Чуваком-рыбаком"),  # two nouns decline together
            ("speaker", "Полицейский", "", "gent.sing", "Полицейского"),      # substantivised adjective
            ("species", "Мак", "", "gent.sing", "Мака"),                      # name, not the abbreviation MAC
            ("species", "Бульбазавр", "", "accs.sing", "Бульбазавра"),        # animate name
            ("species", "Пикачу", "", "datv.sing", "Пикачу"),                 # vowel ending: indeclinable
            ("species", "Яйцо", "", "gent.sing", "Яйца"),                     # common noun keeps the dictionary
            ("character", "Остина", "female", "gent.sing", "Остины"),          # first name, not a surname
            ("character", "Ивори", "female", "datv.sing", "Ивори"),
            ("move", "Плеть-лиана", "", "gent.sing", "Плети-лианы"),
            ("move", "Ядовитая пыль", "", "accs.sing", "Ядовитую пыль"),      # feminine accusative
            ("move", "Огненный кулак", "", "accs.sing", "Огненный кулак"),    # object name: inanimate
            ("item", "Фиги-ягода", "", "datv.sing", "Фиги-ягоде"),            # berry prefix invariant
            ("trainer-class", "Топ-тренер", "", "gent.sing", "Топ-тренера"),  # loanword prefix invariant
            ("location", "город Дехара", "", "loct.sing", "городе Дехара"),   # descriptor declines, name stays
            ("term", "покемон", "", "accs.sing", "покемона"),
            ("nature", "Робкий", "", "gent.sing", "Робкого"),                 # adjective-only name
        ]
        for cat, ru, gender, key, want in cases:
            got = tb.generate_forms(tb.Term("x", cat, "", ru, gender=gender)).get(key)
            self.assertEqual(got, want, f"{cat} {ru} {key}")
        self.assertNotIn("nomn.plur", tb.generate_forms(tb.Term("x", "character", "", "Марлон")))

    def test_rename_keeps_case_and_flags_ambiguity(self):
        old = tb.Term("m", "move", "Swords Dance", "Танец мечей")
        old.forms = tb.generate_forms(old)
        new = tb.generate_forms(tb.Term("m", "move", "", "Пляска клинков"))
        text, n, amb = tb.rename_in_text("С Танцем мечей. {COLOR 06}Танец мечей{COLOR 08}!", old, new)
        self.assertEqual(n, 2)
        self.assertIn("С Пляской клинков.", text)
        self.assertIn("{COLOR 06}Пляска клинков{COLOR 08}", text)
        self.assertTrue(amb)  # nominative/accusative of the old term coincide
        self.assertNotIn("мечей", text)  # no old form left behind

    def test_rename_keeps_common_nouns_lowercase_in_prose(self):
        # label, sentence start, prose, hyphen, proper name
        old = tb.Term("c", "trainer-class", "Camper", "Турист", gender="male")
        old.forms = tb.generate_forms(old)
        new = tb.generate_forms(tb.Term("c", "trainer-class", "", "Походник", gender="male"))
        for text, want in (("{COLOR 02}Турист{COLOR 08} Том", "{COLOR 02}Походник{COLOR 08} Том"),   # label
                           ("Туристы любят лес.", "Походники любят лес."),                          # sentence start
                           ("Я видел туриста у костра.", "Я видел походника у костра."),             # prose
                           ("ТУРИСТ ТОМ", "ПОХОДНИК ТОМ")):
            with self.subTest(text=text):
                self.assertEqual(tb.rename_in_text(text, old, new)[0], want)
        boy = tb.Term("b", "trainer-class", "Ninja Boy", "Мальчик-ниндзя", gender="male")
        boy.forms = tb.generate_forms(boy)
        kid = tb.generate_forms(tb.Term("b", "trainer-class", "", "Юный-Ниндзя", gender="male"))
        self.assertEqual(tb.rename_in_text("Это мальчик-ниндзя.", boy, kid)[0], "Это юный-ниндзя.")   # per part
        # a proper name keeps its capital, as before
        tim = tb.Term("t", "character", "Tim", "Тим", gender="male")
        tim.forms = tb.generate_forms(tim)
        jim = tb.generate_forms(tb.Term("t", "character", "", "Джим", gender="male"))
        self.assertEqual(tb.rename_in_text("спроси тима.", tim, jim)[0], "спроси Джима.")
        self.assertEqual(tb._match_case("тренер", "Тренер покемонов"), "Тренер покемонов")   # lower off: unchanged
        self.assertEqual(tb._match_case("тренер", "Тренер покемонов", True), "тренер покемонов")


class RenameCodesTest(unittest.TestCase):
    SEAL = table(["Тюлень", "Тюленя", "Тюленю", "Тюленя", "Тюленем", "Тюлене"],
                 ["Тюлени", "Тюленей", "Тюленям", "Тюленей", "Тюленями", "Тюленях"])

    def test_a_form_after_a_line_break_code(self):
        for code in ("\\p", "\\n", "\\l"):
            text = f"Нет.{code}Сил тут.{code}Где Сил?"
            want = (f"Нет.{code}Тюлень тут.{code}Где Тюлень?", 2)
            self.assertEqual(tb.rename_in_text(text, SEEL, self.SEAL)[:2], want, code)
            self.assertEqual(tb.rename_in_text(text, SEEL, self.SEAL, f"No.{code}Seel is here.")[:2], want, code)
            self.assertEqual(len(tb.find_forms(text, SEEL)), 2, code)   # the index reads the same

    def test_no_match_after_a_letter_or_another_code(self):
        text = "xСил \\PСил \\vСил {COLOR 02}Сил"
        self.assertEqual(tb.rename_in_text(text, SEEL, self.SEAL)[:2], ("xСил \\PСил \\vСил {COLOR 02}Тюлень", 1))
        self.assertEqual(len(tb.find_forms(text, SEEL)), 1)

    def test_latin_initial_never_consumes_an_escape_letter(self):
        for letter in "nlp":
            form = f"{letter.upper()}-прибор"
            old = tb.Term("item:fixture", "item", "Device", form,
                          forms={"nomn.sing": form})
            replacement = {"nomn.sing": "Датчик"}
            escaped = f"\\{letter}-прибор"
            for english in (None, "Use the Device."):
                self.assertEqual(tb.rename_in_text(escaped, old, replacement, english), (escaped, 0, []))
                text = f"\\{letter}{form}"
                self.assertEqual(tb.rename_in_text(text, old, replacement, english),
                                 (f"\\{letter}Датчик", 1, []))

    def test_multiword_forms_keep_each_break_code(self):
        old = tb.Term("location:bellin", "location", "Bellin Town", "деревня Беллин",
                      forms={"gent.sing": "деревни Беллин"})
        forms = {"gent.sing": "посёлка Сосновый"}
        for code in ("\\n", "\\l", "\\p"):
            text = f"Из {{COLOR 06}}деревни{code}Беллин{{COLOR 04}}."
            expected = (f"Из {{COLOR 06}}посёлка{code}Сосновый{{COLOR 04}}.", 1, [])
            self.assertEqual(len(tb.find_forms(text, old)), 1)
            self.assertEqual(tb.rename_in_text(text, old, forms), expected)
            self.assertEqual(tb.rename_in_text(text, old, forms, "From Bellin Town."), expected)

    def test_multiword_breaks_preserve_case_and_senses(self):
        old = tb.Term("term:critical-hit", "term", "critical hit", "критическое попадание",
                      forms={"gent.sing": "критического попадания"})
        forms = {"gent.sing": "точного удара"}
        self.assertEqual(tb.rename_in_text("критического\\nпопадания", old, forms, "A great blow."),
                         ("точного\\nудара", 1, []))
        self.assertEqual(tb.rename_in_text("КРИТИЧЕСКОГО\\lПОПАДАНИЯ", old, forms),
                         ("ТОЧНОГО\\lУДАРА", 1, []))
        self.assertEqual(tb.rename_in_text("критического\\Pпопадания", old, forms)[1], 0)

    def test_multiple_breaks_keep_word_boundaries(self):
        old = tb.Term("location:lab", "location", "Dream Lab", "Лаборатория исследования снов",
                      forms={"nomn.sing": "Лаборатория исследования снов"})
        forms = {"nomn.sing": "Центр изучения ночных снов"}
        self.assertEqual(tb.rename_in_text("Лаборатория\\nисследования\\pснов", old, forms),
                         ("Центр\\nизучения\\pночных снов", 1, []))

    def test_fewer_words_refuse_an_unplaceable_break(self):
        old = tb.Term("location:lab", "location", "Dream Lab", "Лаборатория исследования снов",
                      forms={"nomn.sing": "Лаборатория исследования снов"})
        forms = {"nomn.sing": "Центр снов"}
        for code in ("\\n", "\\l", "\\p"):
            with self.assertRaisesRegex(ValueError, "replacement has fewer word boundaries"):
                tb.rename_in_text(f"Лаборатория исследования{code}снов", old, forms)
            self.assertEqual(tb.rename_in_text(f"Лаборатория{code}исследования снов", old, forms),
                             (f"Центр{code}снов", 1, []))
        self.assertEqual(tb.rename_in_text("Лаборатория исследования снов", old, forms),
                         ("Центр снов", 1, []))
