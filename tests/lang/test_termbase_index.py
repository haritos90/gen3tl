from test_roots import TOOLKIT_ROOT
"""Termbase index checks and fixtures."""

import sys
from pathlib import Path
import json
import tempfile
import unittest

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.lang import termbase as tb
from gen3tl.catalog.catalog import load_catalog
from gen3tl.catalog.po import PoEntry, write
from termbase_support import SEEL, setUpModule, table


class IndexTest(unittest.TestCase):
    def test_repeated_matches_keep_distinct_rows_in_order(self):
        stone = tb.Term("item:stone", "item", "Stone", "Stone",
                        forms={"nomn.sing": "Stone", "nomn.plur": "Stones"})
        place = tb.Term("location:stone", "location", "Stone", "Stone")
        catalog = {"s2": (Path("b.po"), PoEntry("s2", "Stone", "Stone Stone")),
                   "s1": (Path("a.po"), PoEntry("s1", "Stone", "Stone, Stones, Stone, stone."))}
        rows = tb.build_index([stone, place], catalog)
        self.assertEqual([(r["string"], r["term"], r["form"], r["text"], r["use"]) for r in rows], [
            ("s1", stone.id, "nomn.sing", "Stone", "yes"),
            ("s1", stone.id, "nomn.plur", "Stones", "yes"),
            ("s1", stone.id, "nomn.sing", "stone", "form only"),
            ("s1", place.id, "nomn.sing", "Stone", "yes"),
            ("s1", place.id, "nomn.sing", "stone", "form only"),
            ("s2", stone.id, "nomn.sing", "Stone", "yes"),
            ("s2", place.id, "nomn.sing", "Stone", "yes"),
        ])

    def test_repeated_spelling_keeps_different_senses(self):
        stone = tb.Term("item:stone", "item", "Stone", "Stone")
        catalog = {"s1": (Path("a.po"), PoEntry("s1", "Wait.", "Stone and Stone, Stone."))}
        rows = tb.build_index([stone], catalog)
        self.assertEqual([(r["text"], r["use"]) for r in rows],
                         [("Stone", "form only"), ("Stone", "yes")])

    def test_repeated_punctuation_preserves_matches_and_offline_facts(self):
        dash = tb.Term("term:dash", "term", "-", "-")
        entry = PoEntry("s1", "-", "---")
        catalog = {entry.msgctxt: (Path("a.po"), entry)}
        rows = tb.build_index([dash], catalog)
        self.assertEqual(len(rows), 1)
        self.assertEqual([pos for _, _, pos in tb.find_forms(entry.msgstr, dash)], [0, 1, 2])
        self.assertEqual(tb.rename_in_text(entry.msgstr, dash, {"nomn.sing": "?"})[:2], ("???", 3))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "index.tsv"
            tb.save_index(path, rows * 3)
            before = tb.index_names(path)
            tb.save_index(path, rows)
            self.assertEqual(tb.index_names(path), before)
            entry.msgid = entry.msgctxt
            entry.keyed = True
            self.assertEqual(tb.build_index([dash], catalog, tb.told_by(path, str)), rows)

    def test_index_and_missing_terms(self):
        t = tb.Term("i-potion", "item", "Potion", "Зелье",
                    forms={"nomn.sing": "Зелье", "gent.sing": "Зелья", "ablt.sing": "Зельем"})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root / "a.po", [PoEntry("s1", "Use a Potion!", "Используй Зелье!"),
                                  PoEntry("s2", "No Potion here.", "Тут ничего нет."),
                                  PoEntry("s3", "Hello.", "Привет.")], {})
            rows = tb.build_index([t], load_catalog(root))
            self.assertEqual([(r["term"], r["string"], r["form"]) for r in rows], [("i-potion", "s1", "nomn.sing")])
            m = tb.TermMatcher([t])
            self.assertEqual([x.id for x in m.missing("No Potion here.", "Тут ничего нет.")], ["i-potion"])
            self.assertEqual(m.missing("Use a Potion!", "Используй Зелье!"), [])
            g = root / "g.tsv"
            tb.save(g, [t])
            self.assertEqual(tb.load(g)[0].forms["ablt.sing"], "Зельем")

    def test_index_prefilter_keeps_every_match(self):
        # non-letter-led forms, multi-word forms, ё in the text
        floor = tb.Term("t-b1", "term", "B1F", "-1 эт.")
        road = tb.Term("l-road", "location", "Trial Road", "Дорога испытаний",
                       forms={"nomn.sing": "Дорога испытаний", "loct.sing": "Дороге испытаний"})
        ice = tb.Term("t-ice", "term", "ice", "лёд", forms={"nomn.sing": "лёд", "gent.sing": "льда"})
        dots = tb.Term("t-dots", "term", "...", "…")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root / "a.po", [PoEntry("s1", "B1F", "-1 эт."),
                                  PoEntry("s2", "On Trial Road.", "На {COLOR 06}Дороге испытаний{COLOR 08}."),
                                  PoEntry("s3", "Ice!", "Лед! Ни капли льда."),
                                  PoEntry("s4", "...Roads.", "… Дороги.")], {})
            rows = tb.build_index([floor, road, ice, dots], load_catalog(root))
            self.assertEqual([(r["term"], r["string"], r["form"]) for r in rows],
                             [("t-b1", "s1", "nomn.sing"), ("l-road", "s2", "loct.sing"),
                              ("t-ice", "s3", "nomn.sing"), ("t-ice", "s3", "gent.sing"),
                              ("t-dots", "s4", "nomn.sing")])


class EnglishPluralsTest(unittest.TestCase):
    def test_homonyms_are_alternatives_until_identity_is_bound(self):
        person = tb.Term("character:guy", "character", "Guy", "Гай")
        role = tb.Term("speaker:guy", "speaker", "Guy", "Мужчина")
        matcher = tb.TermMatcher([person, role])
        self.assertEqual(matcher.missing("Guy", "Гай"), [])
        self.assertEqual(matcher.missing("Guy", "Мужчина"), [])
        self.assertEqual(matcher.missing("Guy", "Рыбак"), [person, role])
        self.assertEqual(matcher.only([person.id]).missing("Guy", "Мужчина"), [person])
        self.assertEqual(matcher.only([role.id]).missing("Guy", "Гай"), [role])

    def test_regular_plurals_keep_literal_and_case_sensitive_matching(self):
        cases = [("Red Shard", "Red Shards"), ("Sitrus Berry", "Sitrus Berries"),
                 ("Box", "Boxes"), ("Bus", "Buses"), ("Patch", "Patches"),
                 ("Brush", "Brushes"), ("Toy", "Toys"), ("BERRY", "BERRIES")]
        for singular, plural in cases:
            term = tb.Term("item:example", "item", singular, "Предмет")
            matcher = tb.TermMatcher([term])
            with self.subTest(singular=singular):
                for source in (singular, plural):
                    self.assertEqual(matcher.in_source(source), [term])
                    self.assertTrue(tb.english_present(source.lower(), term))
                self.assertEqual(matcher.in_source(plural.lower()), [])
                self.assertEqual(matcher.in_source(plural + "word"), [])
                self.assertEqual(matcher.in_source("prefix" + plural), [])

    def test_plural_matching_preserves_longest_names_and_exclusions(self):
        shard = tb.Term("item:shard", "item", "Shard", "Осколок")
        red = tb.Term("item:red-shard", "item", "Red Shard", "Алый осколок")
        dex = tb.Term("dex:mouse", "dex-category", "Mouse", "Мышь")
        short = tb.Term("term:hp", "term", "HP", "ОЖ")
        surf = tb.Term("move:surf", "move", "Surf", "Сёрф")
        matcher = tb.TermMatcher([shard, red, dex, short, surf])
        self.assertEqual(matcher.in_source("Red Shards, Shards."), [red, shard])
        self.assertEqual(matcher.in_source("Red\\lShards"), [red])
        self.assertEqual(matcher.in_source("Mouses HPs Surfing"), [])
        self.assertFalse(tb.english_present("Surfing", surf))
        self.assertTrue(tb.Senses([surf]).names("Surfing", surf))

    def test_irregular_and_incorrect_plurals_are_not_inferred(self):
        berry = tb.Term("item:berry", "item", "Berry", "Ягода")
        child = tb.Term("term:child", "term", "Child", "Ребёнок")
        matcher = tb.TermMatcher([berry, child])
        for text in ("Berrys", "Berried", "Children"):
            self.assertEqual(matcher.in_source(text), [])
            self.assertFalse(any(tb.english_present(text, t) for t in (berry, child)))

    def test_plural_occurrences_reach_index_and_batch_context(self):
        from gen3tl.catalog.batch import export_batch
        shard = tb.Term("item:red-shard", "item", "Red Shard", "Алый осколок",
                        forms={"nomn.plur": "Алые осколки"})
        berry = tb.Term("item:sitrus-berry", "item", "Sitrus Berry", "Ягода Ситрус",
                        forms={"nomn.plur": "Ягоды Ситрус"})
        terms = [shard, berry]
        source = "Red Shards and Sitrus Berries."
        translation = "Алые осколки и Ягоды Ситрус."
        matcher = tb.TermMatcher(terms)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root / "po/a.po", [PoEntry("s1", source, translation)], {})
            rows = tb.build_index(terms, load_catalog(root / "po"))
            self.assertEqual([(r["term"], r["source_names_term"], r["use"]) for r in rows],
                             [(shard.id, "yes", "yes"), (berry.id, "yes", "yes")])
            batch = export_batch("plural", ["s1"], root / "po", {}, matcher, root=root / "batches")
            context = json.loads((batch / "source.json").read_text())[0]
            self.assertEqual([t["id"] for t in context["glossary"]], [shard.id, berry.id])
            # the index tells whether the English names the term
            write(root / "named/a.po", [PoEntry("s1", "Two Sitrus Berried trees.", "Ягоды Ситрус")], {})
            rows = tb.build_index(terms, load_catalog(root / "named"))
            self.assertEqual([(r["term"], r["source_names_term"]) for r in rows], [(berry.id, "yes")])
            self.assertFalse(tb.english_present("Two Sitrus Berried trees.", berry))
            tb.save_index(root / "index.tsv", rows)
            self.assertEqual(tb.index_names(root / "index.tsv"), {("s1", berry.id): True})
            # without English: given facts, else unknown
            catalog = load_catalog(root / "named")
            for _, e in catalog.values():
                e.keyed = True
            for told, want in ((True, ("yes", "yes")), (False, ("no", "form only")), (None, ("unknown", "unknown"))):
                rows = tb.build_index(terms, catalog, lambda sid, term: told)
                self.assertEqual([(r["source_names_term"], r["use"]) for r in rows], [want], told)
            self.assertIsNone(tb.told_by(root / "missing.tsv", str)("s1", berry.id))
            self.assertTrue(tb.told_by(root / "index.tsv", str)("s1", berry.id))
        self.assertEqual(matcher.missing(source, translation), [])
        self.assertEqual(matcher.missing(source, "Алые осколки."), [berry])
        self.assertEqual(tb.missing_terms(terms, source, "Алые осколки."), [berry])


BALL = tb.Term("item:poke-ball", "item", "Poké Ball", "Покебол", declension="manual",
               forms=table(["Покебол", "Покебола", "Покеболу", "Покебол", "Покеболом", "Покеболе"],
                           ["Покеболы", "Покеболов", "Покеболам", "Покеболы", "Покеболами", "Покеболах"]))


BALL_NOUN = tb.Term("term:poke-ball", "term", "Poké Ball", "покебол", declension="manual",
                    forms={k: v.lower() for k, v in BALL.forms.items()})


POKEMON = tb.Term("term:pokemon", "term", "Pokémon", "покемон", declension="manual",
                  forms=table(["покемон", "покемона", "покемону", "покемона", "покемоном", "покемоне"],
                              ["покемоны", "покемонов", "покемонам", "покемонов", "покемонами", "покемонах"]))


PROTECT = tb.Term("move:protect", "move", "Protect", "Защита", forms={"nomn.sing": "Защита", "accs.sing": "Защиту"})


DEFENSE = tb.Term("term:defense", "term", "Defense", "Защита", forms={"nomn.sing": "Защита", "accs.sing": "Защиту"})


class SensesTest(unittest.TestCase):
    """A form is a use of its term only where the English names it or the spelling marks it."""

    def index(self, terms, entries):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root / "a.po", [PoEntry(sid, en, ru) for sid, en, ru in entries], {})
            rows = tb.build_index(terms, load_catalog(root))
        return [(r["term"], r["string"], r["text"], r["use"]) for r in rows]

    def test_a_name_spelled_like_a_noun(self):
        rows = self.index([SEEL], [
            ("s1", "Seel used Strength.", "Сил тратит силу."),            # English names Seel
            ("s2", "It is full of power!", "Силы ему не занимать!"),      # sentence start: the noun
            ("s3", "Use Water Pledge.", "Применяй приём Сила воды."),     # a capital in mid-sentence
            ("s4", "Not enough power.", "Не хватает сил."),               # lower case
            ("s5", "Two SEELS!", "Два Сила!")])                           # the plural names it
        self.assertEqual(rows, [("species:seel", "s1", "Сил", "yes"), ("species:seel", "s1", "силу", "form only"),
                                ("species:seel", "s2", "Силы", "form only"),
                                ("species:seel", "s3", "Сила", "form only"),
                                ("species:seel", "s4", "сил", "form only"), ("species:seel", "s5", "Сила", "yes")])

    def test_same_spelled_rows_keep_their_senses(self):
        rows = self.index([BALL, BALL_NOUN], [
            ("s1", "Buy a Poké Ball.", "Купи Покебол."),                   # the item name
            ("s2", "A Ball that works well at night.", "Ночной покебол."),  # the common noun
            ("s3", "Poké Balls", "Покеболы"),                            # a start: either
            ("s4", "Threw a Ball.", "Брошен Покебол.")])                  # the item, by its capital
        self.assertEqual(rows, [("item:poke-ball", "s1", "Покебол", "yes"),
                                ("term:poke-ball", "s1", "Покебол", "other row"),
                                ("item:poke-ball", "s2", "покебол", "other row"),
                                ("term:poke-ball", "s2", "покебол", "yes"),
                                ("item:poke-ball", "s3", "Покеболы", "yes"),
                                ("term:poke-ball", "s3", "Покеболы", "yes"),
                                ("item:poke-ball", "s4", "Покебол", "yes"),
                                ("term:poke-ball", "s4", "Покебол", "other row")])

    def test_a_common_noun_counts_by_its_lower_case(self):
        rows = self.index([POKEMON], [
            ("s1", "{STR_VAR_1} hatched!", "Вылупился покемон {STR_VAR_1}!"),
            ("s2", "{COLOR 02}The {STR_VAR_1} is asleep.", "{COLOR 02}Покемон {STR_VAR_1} спит."),   # a start
            ("s3", "Hi.\\pThe {STR_VAR_1} waits.", "Привет.\\p- Покемон {STR_VAR_1} ждёт."),       # a new box
            ("s4", "Go to the League.", "Иди в Лигу Покемонов.")])        # a capital in mid-sentence
        self.assertEqual([r[1:] for r in rows], [("s1", "покемон", "yes"), ("s2", "Покемон", "yes"),
                                                 ("s3", "Покемон", "yes"), ("s4", "Покемонов", "form only")])

    def test_the_english_tells_same_spelled_names_apart(self):
        rows = self.index([PROTECT, DEFENSE], [("s1", "Protect blocks it.", "Его блокирует Защита."),
                                               ("s2", "Defense fell.", "Падает Защита.")])
        self.assertEqual(rows, [("move:protect", "s1", "Защита", "yes"), ("term:defense", "s1", "Защита", "form only"),
                                ("move:protect", "s2", "Защита", "form only"), ("term:defense", "s2", "Защита", "yes")])

    def test_english_names(self):
        surf = tb.Term("move:surf", "move", "Surf", "Сёрф")
        berry = tb.Term("item:berry", "item", "Berry", "Ягода")
        trainer = tb.Term("trainer-class:pkmn-trainer", "trainer-class", "{PK}{MN} Trainer", "Тренер покемонов")
        hiker = tb.Term("trainer-class:hiker~2", "trainer-class", "Hiker", "Турист")
        senses = tb.Senses([BALL, surf, trainer, tb.Term("trainer-class:hiker", "trainer-class", "HIKER", "Турист"),
                            hiker, berry])
        cases = [("Poke Balls", BALL, True), ("POKé\\lBALL", BALL, True), ("Poké Ballet", BALL, False),
                 ("Surfing west", surf, True), ("Surfer", surf, False), ("{PK}{MN} Trainer Red", trainer, True),
                 ("Pokémon Trainer", trainer, False), ("Two Berries", berry, True),
                 ("A hiker's hat", hiker, True)]
        for msgid, t, want in cases:
            self.assertEqual(senses.names(msgid, t), want, msgid)

    def test_sentence_start(self):
        for before, want in [("", True), ("Привет. ", True), ("Да!” ", True), ("Ну…¶", True), ("Слева - ", False),
                             ("раздел “", False), ("“", True), ("Мой ", False), ("Внимание: ", False)]:
            self.assertEqual(tb.sentence_start(before), want, before)

    def test_rename_renames_the_uses_only(self):
        new = table(["Тюлень", "Тюленя", "Тюленю", "Тюленя", "Тюленем", "Тюлене"],
                    ["Тюлени", "Тюленей", "Тюленям", "Тюленей", "Тюленями", "Тюленях"])
        text = "Сил тратит силу, нет сил."
        self.assertEqual(tb.rename_in_text(text, SEEL, new, "Seel used Strength.")[:2],
                         ("Тюлень тратит силу, нет сил.", 1))
        self.assertEqual(tb.rename_in_text(text, SEEL, new, "Seel used Strength.", include=True)[:2],
                         ("Тюлень тратит Тюленю, нет Тюлень.", 3))   # included: every form
        self.assertEqual(tb.rename_in_text(text, SEEL, new)[1], 3)   # no English: every form, as before
        # what English tells where it is absent: the index
        for english, named in (("Seel used Strength.", True), ("Out of power.", False)):
            self.assertEqual(tb.rename_in_text(text, SEEL, new, named=named),
                             tb.rename_in_text(text, SEEL, new, english), english)
        self.assertEqual(tb.rename_in_text("Силы на исходе.", SEEL, new, "Out of power.")[1], 0)
        nouns = {k: v.replace("покебол", "покешар") for k, v in BALL_NOUN.forms.items()}
        senses = tb.Senses([BALL, BALL_NOUN])
        self.assertEqual(tb.rename_in_text("Купи Покебол и брось покебол.", BALL_NOUN, nouns, "Buy a Poké Ball.",
                                           senses, include=True)[0],
                         "Купи Покебол и брось покешар.")   # the item's spelling is never the noun's
