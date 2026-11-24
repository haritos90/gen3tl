import copy
import tempfile
import tomllib
import unittest
from pathlib import Path

from gen3tl.check import rules
from gen3tl.check.layout import Limit
from gen3tl.check.provenance import code, formatted
from gen3tl.catalog.po import PoEntry
from gen3tl.profile.profile import Profile

PROF = Profile({
    "table": [
        {"name": "species", "base": 0x5000, "stride": 11, "count": 2, "field_offset": 0, "field_length": 11,
         "save_link": True, "evidence": "names at stride 11"},
        {"name": "possessive", "base": 0x9000, "stride": 14, "count": 2, "field_offset": 0, "field_length": 1,
         "terminated": False, "pad": 0xFF, "evidence": "movs r2, #0xB4 at 0x9000"},
        {"name": "keys", "base": 0x8000, "stride": 8, "count": 1, "field_offset": 0, "field_length": 8,
         "terminated": False, "evidence": "key rows"},
    ],
    "strings": {
        "save_link_defaults": ["s0000600"],
        "computed": [{"pointer": 0x9BDE20, "ids": ["s0000340", "s0000300"], "evidence": "ldr r2 at 0x89BDD40"}],
        "interior": [{"pointer": 0x13255C, "id": "s0000700", "offset": 2, "evidence": "StringAppend + 2"}],
        "pointer_table": [{"name": "credits", "base": 0x1000, "stride": 12, "count": 1, "fields": [0],
                           "note": "names stay as in the English credits", "evidence": "credits records"}],
    },
    "layout": {"code_fill": [{"ids": ["s0000100"], "slots": {"STR_VAR_1": {"digits": 2}}, "where": "count",
                              "evidence": "ConvertIntToDecimalString 2"},
                             {"base": 0x20, "stride": 4, "count": 1, "slots": {"STR_VAR_2": {"width": 30}},
                              "evidence": "table fill"}]},
    "qa": {"droppable": [{"ids": ["s0000100"], "tokens": ["{B_COPY_VAR_2}"], "evidence": "writes 'n'"}]},
}, Path("test.toml"))

def entry(sid, text, kind="pointer", table=None, flags=(), refs=()):
    return {"id": sid, "offset": int(sid[1:], 16) if ":" not in sid else 0x5000, "length": len(text) + 1,
            "kind": kind, "category": "code", "mode": "field", "text": text, "table": table, "index": None,
            "refs": [{"at": at, "kind": "code"} for at in refs], "flags": list(flags)}

INV = {"entries": [
    entry("s0000100", "{STR_VAR_1} Pokémon{B_COPY_VAR_2} {STR_VAR_2}", refs=[0x20]),
    entry("s0000300", "Trainer sent out a Pokémon!"),
    entry("s0000340", "Link foe sent out a Pokémon!"),
    entry("species:1", "Ivysaur", "fixed", "species", ["save-link"]),
    entry("possessive:0", "’", "fixed", "possessive", ["unterminated"]),
    entry("keys:0", "abcdef .", "fixed", "keys", ["unterminated"]),
    entry("s0000600", "Ace", flags=["save-link"]),
    entry("s0000700", "{STR_VAR_1} is selected.", refs=[0x13255C]),
    entry("s0000800", "Lead Programmer", table="credits"),
]}

CATALOG = {e["id"]: (Path("a.po"), PoEntry(e["id"], e["text"], "")) for e in INV["entries"]}

LIMITS = {"s0000700": Limit("single", 120, 1, bytes=21, head="{STR_VAR_1}")}

class ProfileRulesTest(unittest.TestCase):
    """Rules generated from the profile entries that carry evidence."""

    @classmethod
    def setUpClass(cls):
        cls.out = rules.profile_rules(PROF, INV, CATALOG, LIMITS)

    def test_computed_byte_gap(self):
        # the lower block member stays within the gap
        [low] = self.out["s0000300"]
        self.assertEqual(low["source"], code("rule_computed", pointer="0x9BDE20"))
        self.assertEqual(formatted(low["source"]), "profile [[strings.computed]] 0x9BDE20")
        self.assertEqual(low["text"], code("wording_computed", others="s0000340", pointer="0x9BDE20", bytes=63,
                                           next="s0000340"))
        self.assertEqual(formatted(low["text"]), "Moves with s0000340 as one block: code reaches the group by pointer "
                         "arithmetic (pool word 0x9BDE20). At most 63 bytes, the gap to s0000340 less the terminator; "
                         "a longer translation stays English.")
        self.assertEqual(low["evidence"], "ldr r2 at 0x89BDD40")
        [last] = self.out["s0000340"]
        self.assertEqual(formatted(last["text"]), "Moves with s0000300 as one block: code reaches the group by pointer "
                         "arithmetic (pool word 0x9BDE20). The last member: no byte gap binds it.")

    def test_interior_head(self):
        [r] = self.out["s0000700"]
        self.assertEqual(r["text"], code("wording_interior", head="{STR_VAR_1}", offset=2, pointer="0x13255C",
                                         bytes=21))
        self.assertEqual(formatted(r["text"]), "Begins with {STR_VAR_1}, the first 2 bytes of the English; code reads "
                         "the text from byte 2 (pool word 0x13255C); kept in place: at most 21 bytes.")
        self.assertEqual((formatted(r["source"]), r["evidence"]), ("profile [[strings.interior]] 0x13255C", "StringAppend + 2"))

    def test_code_characters(self):
        # unterminated code table with a pad byte
        [r] = self.out["possessive:0"]
        self.assertEqual(r["text"], code("wording_code_char_end_one", n=1))
        self.assertEqual(formatted(r["text"]), "Character code written by game code: at most 1 character; only that "
                                               "byte changes; empty writes 0xFF, the terminator.")
        self.assertEqual(r["source"], code("rule_table", table="possessive"))
        self.assertNotIn("keys:0", self.out)
        # a pad byte other than the terminator, more than one character
        prof = copy.deepcopy(PROF)
        prof["table"][1].update(field_length=2, pad=0x00)
        [r] = rules.code_char_rules(prof, INV)["possessive:0"]
        self.assertEqual(r["text"], code("wording_code_char", n=2, pad="0x00"))
        self.assertEqual(formatted(r["text"]), "Character code written by game code: at most 2 characters; only that "
                                               "byte changes; empty writes 0x00.")

    def test_save_link(self):
        [table] = self.out["species:1"]
        self.assertEqual((formatted(table["source"]), table["evidence"]),
                         ("profile [[table]] species save_link", "names at stride 11"))
        self.assertEqual(table["text"], code("wording_save_link"))
        self.assertTrue(formatted(table["text"]).startswith("Copied into save or link data"))
        [listed] = self.out["s0000600"]
        self.assertEqual((formatted(listed["source"]), listed["evidence"]),
                         ("profile [strings] save_link_defaults", ""))

    def test_declared_source_needs_no_recorded_number(self):
        prof = copy.deepcopy(PROF)
        reader = prof["layout"]["code_fill"][0]
        reader.update(name="reader", slots={"STR_VAR_1": {"source_width": 12}})
        prof["layout"].update(code_slot_source={"reader": {"STR_VAR_1": "name"}},
                              fill_source={"name": {"label": "Species name"}})
        out = rules.code_fill_rules(prof, INV, CATALOG)
        # the label as the profile writes it, shown in lower case
        self.assertEqual(out["s0000100"][0]["text"]["fill"], code("fill_measured", measured="Species name"))
        self.assertEqual(formatted(out["s0000100"][0]["text"]),
                         "The code fills {STR_VAR_1} with text measured from species name (count).")

    def test_code_fill_droppable_and_order(self):
        got = [(formatted(r["text"]), formatted(r["source"])) for r in self.out["s0000100"]]
        self.assertEqual(got, [
            ("The code fills {STR_VAR_1} with a number of 2 digits (count).",
             "profile [[layout.code_fill]] s0000100: count"),
            ("The code fills {STR_VAR_2} with text 30 px wide.", "profile [[layout.code_fill]] 0x20"),
            ("May leave out {B_COPY_VAR_2}: the code fills it with text only the English needs.",
             "profile [[qa.droppable]]")])
        self.assertEqual([r["text"]["code"] for r in self.out["s0000100"]],
                         ["wording_code_fill_where", "wording_code_fill", "wording_droppable"])
        prof = copy.deepcopy(PROF)
        prof["layout"]["code_fill"][0]["slots"]["STR_VAR_1"] = {"digits": 1}
        prof["layout"]["code_fill"][1]["slots"]["STR_VAR_2"] = {"strings": ["s0000300", "s0000340"], "font": "normal"}
        out = rules.code_fill_rules(prof, INV, CATALOG)
        self.assertEqual([formatted(r["text"]) for r in out["s0000100"]], [
            "The code fills {STR_VAR_1} with a number of 1 digit (count).",
            "The code fills {STR_VAR_2} with one of 2 source strings, measured at their widest rendering."])
        prof["layout"]["code_fill"][1]["slots"]["STR_VAR_2"] = {"strings": ["s0000300"], "font": "normal"}
        self.assertEqual(formatted(rules.code_fill_rules(prof, INV, CATALOG)["s0000100"][1]["text"]["fill"]),
                         "one of 1 source string, measured at their widest rendering")

    def test_pointer_table_note(self):
        [r] = self.out["s0000800"]
        self.assertEqual((r["text"], formatted(r["source"]), r["evidence"]),
                         ("names stay as in the English credits", "profile [[strings.pointer_table]] credits",
                          "credits records"))

    def test_every_rule_has_a_source(self):
        for sid, rs in self.out.items():
            for r in rs:
                self.assertEqual(set(r), {"text", "source", "evidence"})
                self.assertTrue(r["text"] and r["source"], sid)

class ContextRulesTest(unittest.TestCase):
    def test_never_shown_and_inserted_words(self):
        reach = {"class": "never shown", "source": "review", "evidence": "review: no caller"}
        ctx = {"hosts": [{"slot": "{STR_VAR_1}", "id": f"s000{n}000"} for n in range(1, 7)],
               "words": [{"slot": "{STR_VAR_2}", "id": "s0000200"}]}
        first, last = rules.context_rules(ctx, reach)
        self.assertEqual(first, [{"text": code("wording_never_shown"), "source": "review",
                                  "evidence": "review: no caller"}])
        self.assertEqual(formatted(first[0]["text"]), "The game never shows this text: it is not translated, and an "
                                                      "approved translation stays.")
        self.assertEqual([r["text"] for r in last], [
            code("wording_hosts_more", slot="{STR_VAR_1}", n=6,
                 entries=["s0001000", "s0002000", "s0003000", "s0004000"], more=2),
            code("wording_words_one", slot="{STR_VAR_2}", n=1, entries=["s0000200"])])
        self.assertEqual([formatted(r["text"]) for r in last], [
            "Inserted as {STR_VAR_1} into 6 texts (s0001000, s0002000, s0003000, s0004000 and 2 more): one form "
            "serves every sentence it fills.",
            "{STR_VAR_2} takes 1 inserted word (s0000200): the sentence is built around their fixed forms."])
        ctx = {"hosts": [{"slot": "{STR_VAR_1}", "id": "s0001000"}],
               "words": [{"slot": "{STR_VAR_2}", "id": f"s000{n}000"} for n in range(1, 5)]}
        self.assertEqual([formatted(r["text"]) for r in rules.context_rules(ctx, {"class": "reachable"})[1]], [
            "Inserted as {STR_VAR_1} into 1 text (s0001000): one form serves every sentence it fills.",
            "{STR_VAR_2} takes 4 inserted words (s0001000, s0002000, s0003000, s0004000): the sentence is built "
            "around their fixed forms."])
        self.assertEqual(rules.context_rules({"hosts": [], "words": []}, {"class": "reachable"}), ([], []))

class RuleListTest(unittest.TestCase):
    """A language's wording rules."""

    def load(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "rules.tsv"
            p.write_text(text, encoding="utf-8")
            return rules.load_list(p)

    def test_ids_and_english(self):
        rows = self.load("ids\tsource_text\trule\tsource\n"
                         "s0000300 s0000340\t\tSingular verb.\tstyle guide 2\n"
                         "\t{STR_VAR_1} is\tKeep the name first.\tstyle guide 4\n")
        self.assertEqual(rows[0], {"ids": ["s0000300", "s0000340"], "source_text": "", "rule": "Singular verb.",
                                   "source": "style guide 2"})
        out = rules.wording_rules(rows, CATALOG)
        self.assertEqual(out["s0000300"], [{"text": "Singular verb.", "source": "style guide 2", "evidence": ""}])
        self.assertEqual(sorted(out), ["s0000300", "s0000340", "s0000700"])
        self.assertEqual(out["s0000700"][0]["source"], "style guide 4")

    def test_refused(self):
        self.assertEqual(rules.load_list(Path("no/such/rules.tsv")), [])
        head = "ids\tsource_text\trule\tsource\n"
        for bad in ("ids\trule\tsource\n", head + "s1\t\t\tdecision-1\n", head + "\t\tA rule.\tdecision-1\n",
                    head + "s1\tPokémon\tA rule.\tdecision-1\n"):
            with self.assertRaises(ValueError):
                self.load(bad)
        with self.assertRaisesRegex(ValueError, "no entry s9999999"):
            rules.wording_rules([{"ids": ["s9999999"], "source_text": "", "rule": "R", "source": "S"}], CATALOG)
        with self.assertRaisesRegex(ValueError, "no English contains"):
            rules.wording_rules([{"ids": [], "source_text": "{FD 99}", "rule": "R", "source": "S"}], CATALOG)

class StructuredRuleListTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "rules.toml"
        self.note = {
            "id": "note:verb", "subject": {"kind": "wording_rule", "id": "rule:verb"},
            "scope": {"kind": "language", "language": "test"}, "status": "unreviewed",
            "guidance": ["Singular verb."], "facts": ["One trainer speaks."],
            "reason": ["The subject is singular."],
            "sources": [{"kind": "project", "name": "Style guide", "address": "style-guide",
                         "locator": "2", "verification": "recorded"}],
            "evidence": [{"method": "lookup", "verification": "recorded",
                          "locations": [{"kind": "symbol", "value": "TRAINER"}]}],
            "related_notes": ["note:trainer"],
            "related_subjects": [{"kind": "entry", "id": "s0000300"}],
        }
        self.row = {"id": "rule:verb", "ids": ["s0000300", "s0000340"], "notes": [self.note]}

    def write(self, rows):
        rules.write_list(self.path, rows, code="test")
        return rules.load_list(self.path, code="test")

    def test_roundtrip_and_stable_identities(self):
        original = copy.deepcopy(self.row)
        self.assertEqual(self.write([self.row]), [original])
        self.assertEqual(self.row, original)
        self.assertIn("[[rule.notes]]", self.path.read_text())
        self.assertNotIn("author", self.path.read_text())
        self.assertNotIn("created", self.path.read_text())
        self.note["guidance"] = ["Use one verb."]
        self.note["author"], self.note["created"] = "Contributor", "2026-01-01"
        other = {"id": "rule:name", "source_text": "is selected", "rule": "Keep the name.", "source": "Guide"}
        loaded = self.write([other, self.row])
        self.assertEqual(loaded, [other, self.row])
        self.assertEqual(loaded[1]["id"], original["id"])
        self.assertEqual(loaded[1]["notes"][0]["id"], original["notes"][0]["id"])

    def test_legacy_text_selectors_and_order_stay_exact(self):
        path = self.root / "old.tsv"
        path.write_text("ids\tsource_text\trule\tsource\n"
                        "s0000300 s0000340\t\tSingular verb.\tStyle guide 2\n"
                        "\t{STR_VAR_1} is\tKeep the name first.\tStyle guide 4\n")
        legacy = rules.load_list(path)
        canonical = [dict(row, id=f"rule:{index}") for index, row in enumerate(legacy)]
        self.assertEqual(self.write(canonical), canonical)
        self.assertEqual(rules.wording_rules(canonical, CATALOG), rules.wording_rules(legacy, CATALOG))

    def test_all_note_details_stay_readable(self):
        shown = rules.wording_rules(self.write([self.row]), CATALOG)
        first = shown["s0000300"][0]
        self.assertEqual(first["text"], "Singular verb.\nOne trainer speaks.\nThe subject is singular.")
        self.assertEqual(first["source"], "Style guide")
        for text in ("unreviewed", "style-guide", "locator: 2", "recorded", "TRAINER", "note:trainer", "s0000300"):
            self.assertIn(text, first["evidence"])
        self.assertEqual(shown["s0000340"], shown["s0000300"])

    def test_status_preserves_migrations_without_activating_proposals(self):
        for status in ("unreviewed", "accepted", "proposed", "needs_review", "superseded"):
            with self.subTest(status=status):
                self.note["status"] = status
                loaded = self.write([self.row])
                self.assertEqual(loaded, [self.row])
                self.assertEqual(bool(rules.wording_rules(loaded, CATALOG)), status in ("unreviewed", "accepted"))

    def test_matching_remains_case_sensitive_and_keyed_aware(self):
        self.row.pop("ids")
        self.row["source_text"] = "Trainer sent"
        self.assertEqual(list(rules.wording_rules(self.write([self.row]), CATALOG)), ["s0000300"])
        self.row["source_text"] = "trainer sent"
        with self.assertRaisesRegex(ValueError, "no English contains"):
            rules.wording_rules(self.write([self.row]), CATALOG)
        keyed = {"entry:first": (Path("a.po"), PoEntry("entry:first", "entry:first", "", keyed=True))}
        self.assertEqual(rules.wording_rules([self.row], keyed), {})
        self.row.pop("source_text")
        self.row["ids"] = ["entry:first"]
        self.assertIn("entry:first", rules.wording_rules(self.write([self.row]), keyed))
        self.row["ids"] = ["missing"]
        with self.assertRaisesRegex(ValueError, "no entry missing"):
            rules.wording_rules(self.write([self.row]), keyed)

    def test_wrong_scope_subject_and_duplicate_identities_are_rejected(self):
        invalid = []
        for scope in ({"kind": "shared"}, {"kind": "language", "language": "other"}):
            invalid.append(dict(self.row, notes=[dict(self.note, scope=scope)]))
        for subject in ({"kind": "wording_rule", "id": "another"}, {"kind": "research", "id": self.row["id"]}):
            invalid.append(dict(self.row, notes=[dict(self.note, subject=subject)]))
        for row in invalid:
            with self.subTest(row=row), self.assertRaises(ValueError):
                self.write([row])
        with self.assertRaisesRegex(ValueError, "duplicate rule"):
            self.write([self.row, self.row])
        second = dict(self.row, id="rule:second", notes=[dict(self.note, subject={"kind": "wording_rule", "id": "rule:second"})])
        with self.assertRaisesRegex(ValueError, "duplicate note"):
            self.write([self.row, second])

    def test_invalid_rows_leave_existing_file_untouched(self):
        self.write([self.row])
        before = self.path.read_bytes()
        for changes in ({"id": ""}, {"id": "two words"}, {"ids": []}, {"ids": "s0000300"},
                        {"ids": ["s0000300", "s0000300"]}, {"source_text": "Trainer"}, {"notes": []},
                        {"notes": {}}, {"notes": [{}]}, {"unexpected": True}, {"rule": "Duplicate prose."},
                        {"source": "Duplicate source"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.write([dict(self.row, **changes)])
            self.assertEqual(self.path.read_bytes(), before)
        for row in ({"id": "rule:x", "ids": ["s0000300"], "rule": "No source"}, None):
            with self.assertRaises(ValueError):
                self.write([row])

    def test_schema_validation_and_empty_list(self):
        for text in ('schema = "1.0.0"\nrule = []\n', 'schema = "1.0.0"\n',
                     'schema = "1.0.0"\nrule = {}\n', 'schema = "1.0.0"\nrule = []\nextra = 1\n'):
            self.path.write_text(text)
            with self.assertRaises(ValueError):
                rules.load_list(self.path, code="test")
        self.assertEqual(self.write([]), [])
        self.assertEqual(tomllib.loads(self.path.read_text()), {"schema": "3.0.0", "rule": []})

    def test_canonical_path_and_legacy_conflict(self):
        legacy = self.root / "rules.tsv"
        self.assertEqual(rules.list_path(self.root), legacy)
        legacy.write_text("ids\tsource_text\trule\tsource\n")
        self.assertEqual(rules.list_path(self.root), legacy)
        self.write([self.row])
        with self.assertRaisesRegex(ValueError, "both legacy and structured"):
            rules.list_path(self.root)
        legacy.unlink()
        self.assertEqual(rules.list_path(self.root), self.path)
