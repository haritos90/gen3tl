import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from gen3tl.cli import lang as cli_lang, main as cli
from gen3tl.lang import renamecheck as rc
from gen3tl.lang import termbase as tb
from gen3tl.check.entrycheck import entry_problems
from gen3tl.lang.langpack import LangPack
from gen3tl.check.layout import Limit, Measure
from gen3tl.catalog.po import PoEntry, read, write
from project_fixture import workspace_project, select_language

def setUpModule():
    select_language("ru")

try:
    import pymorphy3  # noqa: F401
    HAVE_MORPH = True
except ImportError:
    HAVE_MORPH = False

CM = LangPack.load("ru", "unbound").charmap()

M = Measure(lambda b: 6, {})   # 6 px per character

def forms(sing: list[str], plur: list[str]) -> dict[str, str]:
    """A form table from the six singular and six plural cases."""
    out = {f"{c}.sing": f for c, f in zip(tb.CASES, sing)}
    out.update({f"{c}.plur": f for c, f in zip(tb.CASES, plur)})
    return out

GIM = forms(["гим", "гима", "гиму", "гим", "гимом", "гиме"], ["гимы", "гимов", "гимам", "гимы", "гимами", "гимах"])

STADION = forms(["стадион", "стадиона", "стадиону", "стадион", "стадионом", "стадионе"],
                ["стадионы", "стадионов", "стадионам", "стадионы", "стадионами", "стадионах"])

ARENA = forms(["арена", "арены", "арене", "арену", "ареной", "арене"],
              ["арены", "арен", "аренам", "арены", "аренами", "аренах"])

GIM_LEADER = forms(["гим-лидер", "гим-лидера", "гим-лидеру", "гим-лидера", "гим-лидером", "гим-лидере"],
                   ["гим-лидеры", "гим-лидеров", "гим-лидерам", "гим-лидеров", "гим-лидерами", "гим-лидерах"])

LEADER = forms(["лидер стадиона", "лидера стадиона", "лидеру стадиона", "лидера стадиона", "лидером стадиона",
                "лидере стадиона"],
               ["лидеры стадиона", "лидеров стадиона", "лидерам стадиона", "лидеров стадиона",
                "лидерами стадиона", "лидерах стадиона"])

GOLUBKI = forms(["голубки", "голубков", "голубкам", "голубков", "голубками", "голубках"],
                ["голубки", "голубков", "голубкам", "голубков", "голубками", "голубках"])   # plural only

PARA = forms(["молодая пара", "молодой пары", "молодой паре", "молодую пару", "молодой парой", "молодой паре"],
             ["молодые пары", "молодых пар", "молодым парам", "молодые пары", "молодыми парами", "молодых парах"])

DVOYNYA = forms(["двойня", "двойни", "двойне", "двойню", "двойней", "двойне"],
                ["двойни", "двоен", "двойням", "двойни", "двойнями", "двойнях"])

BLIZ = forms(["близняшки", "близняшек", "близняшкам", "близняшек", "близняшками", "близняшках"],
             ["близняшки", "близняшек", "близняшкам", "близняшек", "близняшками", "близняшках"])   # plural only

ELIXIR = tb.Term("item:elixir", "item", "Elixir", "Эликсир", label="Эл.",
                 forms={"gent.sing": "Эликсира"})

MAX_ELIXIR = tb.Term("item:max-elixir", "item", "Max Elixir", "Эликсир макс", label="Эл. макс",
                     forms={"gent.sing": "Эликсира макс"})

NECTAR = {"nomn.sing": "Нектар", "gent.sing": "Нектара", "label": "Нект."}

def term(tid: str, english: str, translation: str, table: dict) -> tb.Term:
    return tb.Term(tid, "term", english, translation, declension="manual", forms=dict(table))

def run(catalog: dict, terms: list[tb.Term], renames: list[rc.Rename], morph=None) -> dict:
    """The report of a rename in a synthetic catalog."""
    changes = rc.apply_renames({sid: x[1] for sid, x in catalog.items()}, renames)
    rows = {r.old.id: tb.Term(r.old.id, r.old.category, r.old.source_text, r.translation, forms=r.forms) for r in renames}
    m0, m1 = tb.TermMatcher(terms), tb.TermMatcher([rows.get(t.id, t) for t in terms])
    before, after = {}, {}
    for sid, c in changes.items():
        english, text, lim = catalog[sid]
        before[sid] = entry_problems(english, text, lim, M, CM, matcher=m0)[1]
        after[sid] = entry_problems(english, c["after"], lim, M, CM, matcher=m1)[1]
    return rc.build_report(renames, changes, {k: x[0] for k, x in catalog.items()},
                           {k: "a.po" for k in catalog}, before, after, morph=morph)

class ProblemTest(unittest.TestCase):
    def test_kind_leaves_out_numbers_and_excerpts(self):
        self.assertEqual(rc.problem_key("line too wide: 96 > 80 px: 'Стадион Антисиса'"),
                         rc.problem_key("line too wide: 72 > 60 px: 'Гим'"))
        self.assertEqual(rc.problem_key("glossary term 'Gym' -> 'гим' not found (warning)"),
                         rc.problem_key("glossary term 'Gym' -> 'стадион' not found (warning)"))
        self.assertNotEqual(rc.problem_key("glossary term 'Gym' -> 'гим' not found (warning)"),
                            rc.problem_key("glossary term 'Gym Leader' -> 'гим' not found (warning)"))

    def test_added_pairs_each_before_message_once(self):
        before = ["line too wide: 70 > 60 px: 'а'"]
        after = ["line too wide: 80 > 60 px: 'б'", "line too wide: 90 > 60 px: 'в'", "x (warning)"]
        self.assertEqual(rc.added_problems(before, after),
                         [{"message": after[1], "severity": "error"},
                          {"message": "x (warning)", "severity": "warning"}])

class RenameReportTest(unittest.TestCase):
    def test_width_overflow_is_blocked(self):
        gym = term("term:gym", "Gym", "гим", GIM)
        catalog = {"s1": ("Antisis Gym", "Гим Антисиса", Limit("field", 80, 1)),     # 72 px, then 96
                   "s2": ("Gym", "Гим", Limit("single", 100, 1)),
                   "s3": ("Gym", "Гим и очень длинная подпись", Limit("single", 60, 1))}   # too wide already
        rep = run(catalog, [gym], [rc.Rename(gym, "стадион", STADION)])
        by_id = {e["id"]: e for e in rep["entries"]}
        self.assertEqual(by_id["s1"]["after"], "Стадион Антисиса")
        self.assertEqual([p["severity"] for p in by_id["s1"]["added"]], ["error"])
        self.assertTrue(by_id["s1"]["added"][0]["message"].startswith("line too wide: 96 > 80 px"))
        self.assertEqual(by_id["s2"]["added"], [])
        self.assertEqual(by_id["s3"]["added"], [])   # wider, but not new
        s = rep["summary"]
        self.assertEqual((s["changed"], s["errors"], s["error_lines"], s["blocked"]), (3, 1, 1, True))
        self.assertIn("Result: blocked", rc.markdown(rep))

    def test_gender_change_lists_the_lines_to_check(self):
        gym = term("term:gym", "Gym", "стадион", STADION)
        catalog = {"s1": ("This Gym is closed. It opens tomorrow.", "Этот стадион закрыт.\\pОн откроется завтра.",
                          Limit("dialogue", 208, 2)),
                   "s2": ("The Gyms are closed.", "Стадионы закрыты.", Limit("dialogue", 208, 2)),
                   "s3": ("Quiet in the Gym.", "В стадионе тихо.", Limit("dialogue", 208, 2))}
        for morph in [None] + ([tb.morph()] if HAVE_MORPH else []):
            rep = run(catalog, [gym], [rc.Rename(gym, "арена", ARENA)], morph)
            g = rep["renames"][0]["grammar"]
            self.assertEqual(g["changes"], [{"property": "gender", "old": "masc", "new": "femn"},
                                            {"property": "declension", "old": "2", "new": "1"}])
            by_id = {e["id"]: e for e in rep["entries"]}
            self.assertEqual(by_id["s1"]["after"], "Этот арена закрыт.\\pОн откроется завтра.")
            self.assertEqual(by_id["s1"]["agreement"],
                             [{"term": "term:gym", "form": "nomn.sing", "match": "стадион",
                               "words": ["Этот", "закрыт", "Он"]}])
            self.assertEqual(by_id["s2"]["agreement"], [])   # the plural shows no gender
            self.assertEqual(by_id["s3"]["agreement"], [])
            self.assertEqual(rep["summary"]["agreement_lines"], 1)
            self.assertIn("| s1 | term:gym | nomn.sing | стадион | Этот, закрыт, Он |", rc.markdown(rep))

    def test_plural_to_singular_lists_the_plural_words(self):
        # with and without the morphology library
        couple = term("trainer-class:young-couple", "Young Couple", "голубки", GOLUBKI)
        catalog = {"s1": ("Strong Young Couple want to battle!", "Сильные голубки вызвали тебя на бой!",
                          Limit("dialogue", 208, 2)),
                   "s2": ("Young Couple wants to battle!", "Голубки вызвала тебя на бой!", Limit("dialogue", 208, 2)),
                   "s3": ("These Young Couple are ready. They wait.", "Эти голубки готовы.\\pОни рядом.",
                          Limit("dialogue", 208, 2))}
        for morph in [None] + ([tb.morph()] if HAVE_MORPH else []):
            rep = run(catalog, [couple], [rc.Rename(couple, "молодая пара", PARA)], morph)
            g = rep["renames"][0]["grammar"]
            self.assertEqual(g["changes"], [{"property": "number", "old": "plur", "new": "sing"},
                                            {"property": "declension", "old": "2", "new": "1"},
                                            {"property": "animacy", "old": "anim", "new": "inan"}])
            self.assertIs(type(g["new"]["gender"]), str)
            by_id = {e["id"]: e for e in rep["entries"]}
            self.assertEqual(by_id["s1"]["after"], "Сильные молодая пара вызвали тебя на бой!")
            self.assertEqual([a["words"] for a in by_id["s1"]["agreement"]], [["Сильные", "вызвали"]])
            self.assertEqual(by_id["s2"]["agreement"], [])   # the singular verb becomes correct
            self.assertEqual([a["words"] for a in by_id["s3"]["agreement"]], [["Эти", "готовы", "Они"]])
            self.assertEqual(rep["summary"]["agreement_lines"], 2)
            self.assertIn("number plur -> sing", rc.markdown(rep))

    def test_singular_to_plural_lists_the_singular_words(self):
        # with and without the morphology library
        twins = term("trainer-class:twins", "Twins", "двойня", DVOYNYA)
        catalog = {"t1": ("Twins want to battle!", "Двойня вызвала тебя на бой!", Limit("dialogue", 208, 2)),
                   "t2": ("Twins want to battle!", "Двойня вызвали тебя на бой!", Limit("dialogue", 208, 2)),
                   "t3": ("These Twins are ready. They wait.", "Эта двойня готова.\\pОна рядом.",
                          Limit("dialogue", 208, 2))}
        for morph in [None] + ([tb.morph()] if HAVE_MORPH else []):
            rep = run(catalog, [twins], [rc.Rename(twins, "близняшки", BLIZ)], morph)
            g = rep["renames"][0]["grammar"]
            self.assertEqual(g["changes"], [{"property": "number", "old": "sing", "new": "plur"},
                                            {"property": "declension", "old": "1", "new": "2"},
                                            {"property": "animacy", "old": "inan", "new": "anim"}])
            self.assertIs(type(g["old"]["gender"]), str)
            by_id = {e["id"]: e for e in rep["entries"]}
            self.assertEqual(by_id["t1"]["after"], "Близняшки вызвала тебя на бой!")
            self.assertEqual([a["words"] for a in by_id["t1"]["agreement"]], [["вызвала"]])
            self.assertEqual(by_id["t2"]["agreement"], [])   # the plural verb becomes correct
            self.assertEqual([a["words"] for a in by_id["t3"]["agreement"]], [["Эта", "готова", "Она"]])
            self.assertEqual(rep["summary"]["agreement_lines"], 2)

    def test_singular_to_plural_reports_present_tense_verbs(self):
        # «ждёт» agrees with the singular term only
        twins = term("trainer-class:twins", "Twins", "двойня", DVOYNYA)
        catalog = {"t1": ("Twins wait for you.", "Двойня ждёт тебя.", Limit("dialogue", 208, 2)),
                   "t2": ("Twins wait for you.", "Двойня ждут тебя.", Limit("dialogue", 208, 2))}
        for morph in [None] + ([tb.morph()] if HAVE_MORPH else []):
            rep = run(catalog, [twins], [rc.Rename(twins, "близняшки", BLIZ)], morph)
            by_id = {e["id"]: e for e in rep["entries"]}
            self.assertEqual(by_id["t1"]["after"], "Близняшки ждёт тебя.")
            self.assertEqual([a["words"] for a in by_id["t1"]["agreement"]], [["ждёт"]])
            self.assertEqual(by_id["t2"]["agreement"], [])   # the plural verb becomes correct
            self.assertEqual(rep["summary"]["agreement_lines"], 1)

    def test_plural_to_singular_reports_present_tense_verbs(self):
        # «ждут» agrees with the plural term only
        couple = term("trainer-class:young-couple", "Young Couple", "голубки", GOLUBKI)
        catalog = {"s1": ("Young Couple wait for you.", "Голубки ждут тебя.", Limit("dialogue", 208, 2)),
                   "s2": ("Young Couple wait for you.", "Голубки ждёт тебя.", Limit("dialogue", 208, 2))}
        for morph in [None] + ([tb.morph()] if HAVE_MORPH else []):
            rep = run(catalog, [couple], [rc.Rename(couple, "молодая пара", PARA)], morph)
            by_id = {e["id"]: e for e in rep["entries"]}
            self.assertEqual([a["words"] for a in by_id["s1"]["agreement"]], [["ждут"]])
            self.assertEqual(by_id["s2"]["agreement"], [])   # the singular verb becomes correct
            self.assertEqual(rep["summary"]["agreement_lines"], 1)

    def test_a_present_tense_verb_of_the_same_number_is_not_reported(self):
        # a gender change alone leaves the number alone
        gym = term("term:gym", "Gym", "гим", GIM)
        catalog = {"s1": ("The Gym waits.", "Гим ждёт.", Limit("dialogue", 208, 2)),
                   "s2": ("The Gyms wait.", "Гимы ждут.", Limit("dialogue", 208, 2))}
        for morph in [None] + ([tb.morph()] if HAVE_MORPH else []):
            rep = run(catalog, [gym], [rc.Rename(gym, "арена", ARENA)], morph)
            self.assertEqual([c["property"] for c in rep["renames"][0]["grammar"]["changes"]],
                             ["gender", "declension"])
            self.assertEqual([e["agreement"] for e in rep["entries"]], [[], []])

    def test_rename_that_adds_nothing_is_clean(self):
        gym = term("term:gym", "Gym", "гим", GIM)
        catalog = {"s1": ("Welcome to the Gym!", "Добро пожаловать в гим!", Limit("dialogue", 208, 2)),
                   "s2": ("Leave the Gym.", "Уход из гима.", Limit("single", 120, 1))}
        rep = run(catalog, [gym], [rc.Rename(gym, "стадион", STADION)])
        self.assertEqual([e["after"] for e in rep["entries"]], ["Добро пожаловать в стадион!", "Уход из стадиона."])
        self.assertEqual(rep["renames"][0]["grammar"]["changes"], [])
        s = rep["summary"]
        self.assertEqual((s["errors"], s["warnings"], s["agreement_lines"], s["blocked"]), (0, 0, 0, False))
        self.assertIn("Result: no new error.", rc.markdown(rep))

    def test_a_longer_term_built_on_the_old_one(self):
        gym, leader = term("term:gym", "Gym", "гим", GIM), term("term:gym-leader", "Gym Leader", "гим-лидер",
                                                                  GIM_LEADER)
        catalog = {"s1": ("The Gym Leader waits.", "Гим-лидер ждёт.", Limit("dialogue", 208, 2))}
        rep = run(catalog, [gym, leader], [rc.Rename(gym, "стадион", STADION)])
        self.assertEqual(rep["entries"][0]["after"], "Стадион-лидер ждёт.")
        self.assertEqual([p["message"] for p in rep["entries"][0]["added"]],
                         ["glossary term 'Gym Leader' -> 'гим-лидер' not found (warning)"])
        rep = run(catalog, [gym, leader], [rc.Rename(leader, "лидер стадиона", LEADER),
                                           rc.Rename(gym, "стадион", STADION)])   # the longer term first
        self.assertEqual(rep["entries"][0]["after"], "Лидер стадиона ждёт.")
        self.assertEqual(rep["entries"][0]["terms"], ["term:gym-leader"])
        self.assertEqual(rep["entries"][0]["added"], [])

    def test_report_format(self):
        gym = term("term:gym", "Gym", "гим", GIM)
        rep = run({"s1": ("Antisis Gym", "Гим Антисиса", Limit("field", 80, 1))}, [gym],
                  [rc.Rename(gym, "стадион", STADION)])
        rep = json.loads(json.dumps(rep, ensure_ascii=False))
        self.assertEqual((rep["format"], rep["version"]), (rc.FORMAT, rc.VERSION))
        self.assertEqual(set(rep), {"format", "version", "renames", "summary", "entries"})
        self.assertEqual(set(rep["renames"][0]), {"term", "source_text", "old", "new", "forms", "lines", "grammar"})
        self.assertEqual(set(rep["summary"]), {"changed", "limit_checked", "errors", "error_lines", "warnings",
                                               "warning_lines", "agreement_lines", "ambiguous_lines", "blocked"})
        e = rep["entries"][0]
        self.assertEqual(set(e), {"id", "file", "source_text", "before", "after", "changed", "terms", "ambiguous",
                                  "added", "agreement"})
        self.assertEqual(set(e["added"][0]), {"message", "severity"})   # the editor check's shape
        md = rc.markdown(rep)
        for title in ("# Term rename check", "## New errors", "## New warnings", "## Agreement to check",
                      "## Ambiguous forms", "## Changed lines"):
            self.assertIn(title, md)
        self.assertIn("| s1 | Гим Антисиса | Стадион Антисиса | 1 |", md)

@unittest.skipUnless(HAVE_MORPH, "pymorphy3 not installed (run with .venv/bin/python)")
class CommandTest(unittest.TestCase):
    def test_lang_rename_preserves_enclosing_terms_unless_named(self):
        for pairs, expected in (
            (["item:elixir", "Нектар"], "Эликсир макс"),
            (["item:elixir", "Нектар", "item:max-elixir", "Нектар макс"], "Нектар макс"),
            (["item:max-elixir", "Нектар макс", "item:elixir", "Нектар"], "Нектар макс"),
        ):
            with self.subTest(pairs=pairs), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                po, glossary = root / "po", root / "glossary.tsv"
                write(po / "a.po", [PoEntry("s1", "Elixir", "Эликсир"),
                                     PoEntry("s2", "Max Elixir", "Эликсир макс"),
                                     PoEntry("s3", "Elixir and Max Elixir", "Эликсир и Эликсир макс")],
                      {"Project-Id-Version": "game", "Language": "ru"})
                tb.save(glossary, [ELIXIR, MAX_ELIXIR])
                replacement = root / "replacement.tsv"
                tb.save(replacement, [tb.Term("item:elixir", "item", "Elixir", "Нектар", forms=NECTAR),
                                      tb.Term("item:max-elixir", "item", "Max Elixir", "Нектар макс",
                                              forms={k: f"{v} макс" for k, v in NECTAR.items()})])
                argv = ["--project-root", str(root), "--profile", str(workspace_project(root)),
                        "lang", "rename", "--replacement-glossary", str(replacement),
                        "--glossary", str(glossary), "--po-root", str(po), "--index", str(root / "index.tsv"),
                        *pairs, "--apply"]
                with mock.patch.object(cli_lang, "_rename_checks", return_value=({}, {}, {})), \
                        redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(argv), 0)
                entries = read(po / "a.po")[1]
                self.assertEqual([e.msgstr for e in entries], ["Нектар", expected, f"Нектар и {expected}"])
                self.assertFalse(any(e.fuzzy for e in entries))   # approved before the rename
                self.assertEqual([t.translation for t in tb.load(glossary)], ["Нектар", expected])

    def test_a_rename_keeps_the_state_of_each_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            po, glossary = root / "po", root / "glossary.tsv"
            write(po / "a.po", [PoEntry("s1", "Elixir", "Эликсир"),
                                 PoEntry("s2", "An Elixir", "Эликсир", flags=["fuzzy"]),
                                 PoEntry("s3", "Elixir?", "")],
                  {"Project-Id-Version": "game", "Language": "ru"})
            tb.save(glossary, [ELIXIR])
            replacement = root / "replacement.tsv"
            tb.save(replacement, [tb.Term("item:elixir", "item", "Elixir", "Нектар", forms=NECTAR)])
            argv = ["--project-root", str(root), "--profile", str(workspace_project(root)),
                    "lang", "rename", "--replacement-glossary", str(replacement),
                    "--glossary", str(glossary), "--po-root", str(po), "--index", str(root / "index.tsv"),
                    "item:elixir", "Нектар", "--apply"]
            with mock.patch.object(cli_lang, "_rename_checks", return_value=({}, {}, {})), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(argv), 0)
            self.assertEqual([(e.msgstr, e.flags) for e in read(po / "a.po")[1]],
                             [("Нектар", []), ("Нектар", ["fuzzy"]), ("", [])])

    def test_apply_refuses_a_rename_that_adds_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            po, g = root / "po", root / "glossary.tsv"
            po.mkdir()
            write(po / "a.po", [PoEntry("s1", "Antisis Gym", "Гим Антисиса")],
                      {"Project-Id-Version": "game", "Language": "ru"})
            tb.save(g, [term("term:gym", "Gym", "гим", GIM)])
            replacement = root / "replacement.tsv"
            tb.save(replacement, [term("term:gym", "Gym", "стадион", STADION)])
            argv = ["--project-root", str(root), "--profile", str(workspace_project(root)),
                    "lang", "rename", "--replacement-glossary", str(replacement), "--glossary", str(g), "--po-root", str(po), "--index", str(root / "index.tsv"),
                    "term:gym", "стадион", "--apply"]

            def checks(args, catalog, terms, renames, changes):
                return {"s1": []}, {"s1": ["line too wide: 96 > 80 px: 'Стадион Антисиса'"]}, {}
            with mock.patch.object(cli_lang, "_rename_checks", checks):
                with redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(cli.main(argv), 1)
                self.assertIn("status       blocked", out.getvalue())
                self.assertEqual(read(po / "a.po")[1][0].msgstr, "Гим Антисиса")
                self.assertEqual(tb.load(g)[0].translation, "гим")
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(argv + ["--allow-errors"]), 0)
            e = read(po / "a.po")[1][0]
            self.assertEqual((e.msgstr, e.fuzzy), ("Стадион Антисиса", False))
            self.assertIn("adds: line too wide", e.translator[-1])
            self.assertNotIn("term rename", "\n".join(e.translator))
            row = tb.load(g)[0]
            self.assertEqual((row.translation, row.variants), ("стадион", "гим"))

SEEL = forms(["Сил", "Сила", "Силу", "Сила", "Силом", "Силе"], ["Силы", "Силов", "Силам", "Силов", "Силами", "Силах"])

NERPA = forms(["Нерпа", "Нерпы", "Нерпе", "Нерпу", "Нерпой", "Нерпе"], ["Нерпы", "Нерп", "Нерпам", "Нерп", "Нерпами",
                                                                      "Нерпах"])

def seel() -> tb.Term:
    return tb.Term("species:seel", "species", "Seel", "Сил", declension="manual", forms=dict(SEEL))

class SenseRenameTest(unittest.TestCase):
    TEXTS = {"s1": "Сил здесь.", "s2": "Не хватает сил.", "s3": "Силы на исходе."}
    ENGLISH = {"s1": "Seel is here.", "s2": "Not enough power.", "s3": "Out of power."}

    def test_enclosing_terms_protect_only_their_own_occurrences(self):
        senses = tb.Senses([ELIXIR, MAX_ELIXIR])
        rename = rc.Rename(ELIXIR, "Нектар", NECTAR, include=frozenset({"s1"}))
        for before, after in (
            ("Эликсир макс", "Эликсир макс"),
            ("Эликсир и Эликсир макс, Эликсир.", "Нектар и Эликсир макс, Нектар."),
            ("Нет Эликсира макс и Эликсира.", "Нет Эликсира макс и Нектара."),
            ("Эл. макс и Эл.", "Эл. макс и Нект."),
            ("ЭЛИКСИР МАКС и ЭЛИКСИР", "ЭЛИКСИР МАКС и НЕКТАР"),
            ("Эликсир максимум", "Нектар максимум"),
            ("{COLOR 06}Эликсир макс{COLOR 08} и Эликсир", "{COLOR 06}Эликсир макс{COLOR 08} и Нектар"),
            *((f"Эликсир{code}макс и Эликсир", f"Эликсир{code}макс и Нектар")
              for code in (r"\n", r"\l", r"\p")),
        ):
            for english in (None, {"s1": "Elixir and Max Elixir"}):
                with self.subTest(before=before, english=english):
                    changes = rc.apply_renames({"s1": before}, [rename], english, senses)
                    self.assertEqual(changes.get("s1", {}).get("after", before), after)
                    if before == after:
                        self.assertEqual(changes, {})

    def test_enclosing_forms_can_start_before_the_match_and_overlap(self):
        terms = [ELIXIR, tb.Term("item:super-elixir", "item", "Super Elixir", "Супер Эликсир"),
                 tb.Term("item:double-elixir", "item", "Double Elixir", "Эликсир Эликсир")]
        text = "Супер Эликсир, Эликсир Эликсир Эликсир; Эликсир."
        changes = rc.apply_renames({"s1": text}, [rc.Rename(ELIXIR, "Нектар", NECTAR)],
                                   {"s1": "Elixir"}, tb.Senses(terms))
        self.assertEqual(changes["s1"]["after"], "Супер Эликсир, Эликсир Эликсир Эликсир; Нектар.")

    def test_only_the_uses_are_renamed(self):
        senses = tb.Senses([seel()])
        r = rc.Rename(seel(), "Нерпа", NERPA)
        changes = rc.apply_renames(self.TEXTS, [r], self.ENGLISH, senses)
        self.assertEqual({k: c["after"] for k, c in changes.items()}, {"s1": "Нерпа здесь."})
        self.assertEqual(rc.left_out(self.TEXTS, [r], self.ENGLISH, senses),
                         {"s2": [{"term": "species:seel", "match": "сил"}],
                          "s3": [{"term": "species:seel", "match": "Силы"}]})
        r = rc.Rename(seel(), "Нерпа", NERPA, include=frozenset({"s3"}))   # explicitly included
        changes = rc.apply_renames(self.TEXTS, [r], self.ENGLISH, senses)
        self.assertEqual({k: c["after"] for k, c in changes.items()}, {"s1": "Нерпа здесь.", "s3": "Нерпы на исходе."})
        self.assertEqual(list(rc.left_out(self.TEXTS, [r], self.ENGLISH, senses)), ["s2"])
        self.assertEqual(len(rc.apply_renames(self.TEXTS, [r])), 3)   # without the English: every form

    def test_agreement_of_the_renamed_occurrences_only(self):
        r = rc.Rename(seel(), "Нерпа", NERPA)
        texts = {"s1": "Сил пришёл. Нет сил, он устал."}
        english = {"s1": "Seel came. No strength, he is tired."}
        senses = tb.Senses([seel()])
        changes = rc.apply_renames(texts, [r], english, senses)
        self.assertEqual(changes["s1"]["after"], "Нерпа пришёл. Нет сил, он устал.")
        rep = rc.build_report([r], changes, english, {"s1": "a.po"}, {"s1": []}, {"s1": []}, senses=senses)
        self.assertEqual(rep["entries"][0]["agreement"],
                         [{"term": "species:seel", "form": "nomn.sing", "match": "Сил", "words": ["пришёл", "он"]}])
        rep = rc.build_report([r], changes, english, {"s1": "a.po"}, {"s1": []}, {"s1": []})
        self.assertEqual(len(rep["entries"][0]["agreement"]), 2)   # without the Senses: «сил» too

    @unittest.skipUnless(HAVE_MORPH, "pymorphy3 not installed (run with .venv/bin/python)")
    def test_term_rename_leaves_form_only_lines_unless_included(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            po, g = root / "po", root / "glossary.tsv"
            po.mkdir()
            write(po / "a.po", [PoEntry(k, self.ENGLISH[k], v) for k, v in self.TEXTS.items()],
                      {"Project-Id-Version": "game", "Language": "ru"})
            tb.save(g, [seel()])
            replacement = root / "replacement.tsv"
            tb.save(replacement, [tb.Term("species:seel", "species", "Seel", "Нерпа", forms=NERPA)])
            argv = ["--project-root", str(root), "--profile", str(workspace_project(root)),
                    "lang", "rename", "--replacement-glossary", str(replacement), "--glossary", str(g), "--po-root", str(po), "--index", str(root / "index.tsv"),
                    "species:seel", "Нерпа", "--apply"]

            def checks(args, catalog, terms, renames, changes):
                return {k: [] for k in changes}, {k: [] for k in changes}, {}
            with mock.patch.object(cli_lang, "_rename_checks", checks):
                with redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(cli.main(argv + ["--include", "s3"]), 0)
            self.assertIn("entry        s2\nstatus       excluded\nmatch        сил", out.getvalue())
            self.assertIn("excluded     1 lines", out.getvalue())
            self.assertEqual([e.msgstr for e in read(po / "a.po")[1]], ["Нерпа здесь.", "Не хватает сил.",
                                                                        "Нерпы на исходе."])
            index = (root / "index.tsv").read_text(encoding="utf-8").splitlines()
            self.assertEqual(index[0].split("\t"), ["term", "string", "form", "text", "source_names_term", "use",
                                                     "file"])
