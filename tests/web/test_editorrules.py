"""Stable engine rule models for both editor source paths."""

from copy import deepcopy
import json
from pathlib import Path
import unittest

from project_fixture import rom_evidence
from gen3tl.catalog.keys import Aliases
from gen3tl.profile.profile import Profile
from gen3tl.web.editorrules import catalog_preview, rules_data
from gen3tl.core.charmap import Charmap
from gen3tl.check.languagerules import RuleError
from gen3tl.web.editorcheck import Checker
from gen3tl.web.editorsnapshot import configuration
from test_languagerules import fixture
from test_languagerules_check import plural_profile, original_values, single_rule


def policy_fixture():
    return {
        "rom": {"sha1": "ab" * 20},
        "code_replacement": [{"name": "units", "routine": {"words": ["unit"]},
                              "calls": [{"guard": rom_evidence(12, "abcd")}]}],
        "text_prefixes": [{"name": "marker", "pointer": 16, "source": rom_evidence(32, "a1ff")}],
        "text_controls": {"droppable": {"article": {"ids": ["s0000010", "s0000020"],
                                                   "tokens": ["{STR_VAR_1}"]}}},
        "language_rules": [{"id": name, "title": name, "kind": "policy",
                            "description": "Declared capability.", "evidence": "Synthetic binding.",
                            "related": [], "choices": ["original", "enabled"],
                            "policies": [{"kind": kind, "name": name, **fields}]}
                           for name, kind, fields in [("units", "code_replacement", {"fields": ["unit"]}),
                                                      ("marker", "text_prefix", {}),
                                                      ("article", "droppable", {})]],
    }


class RuleDataTest(unittest.TestCase):
    def test_empty_profile_and_json_round_trip(self):
        self.assertEqual(rules_data({}, []), {"schema": "1.0.0", "rules": []})
        data = rules_data(fixture(), [])
        self.assertEqual(data, json.loads(json.dumps(data)))

    def test_grouped_bindings_and_catalog_states_do_not_copy_text(self):
        prof = Profile(fixture(), Path("fixture.toml"))
        entries = [{"id": "table:ending/1", "translation": "", "flags": ["empty", "fuzzy"],
                    "status": "fuzzy", "reach": {"class": "never shown"}},
                   {"id": "table:copy/1", "translation": "", "flags": [], "status": "untranslated"},
                   {"id": "table:names/1", "translation": "Translated example", "status": "approved",
                    "source_text": "Original example"}]
        before = deepcopy((prof.data, entries))
        model = rules_data(prof, entries)["rules"][0]
        self.assertEqual([row["offset"] for row in model["operands"][0]["bindings"]], [37, 69])
        self.assertEqual(model["operands"][0]["choices"], ["original", "off"])
        self.assertEqual([row["buffer_bytes"] for row in model["routines"]], [20, 32])
        self.assertEqual(model["branches"], prof.language_rules[0]["branches"])
        sources = {row["entry"]: row for row in model["sources"]}
        self.assertEqual(sources["table:ending/1"]["state"], "empty")
        self.assertEqual(sources["table:ending/1"]["status"], "fuzzy")
        self.assertEqual(sources["table:ending/1"]["reach"], {"class": "never shown"})
        self.assertEqual(sources["table:copy/1"]["state"], "original")
        self.assertEqual(sources["table:names/1"]["state"], "text")
        self.assertFalse(sources["hash:01234567"]["available"])
        self.assertIsNone(sources["hash:01234567"]["state"])
        self.assertNotIn("Original example", json.dumps(model))
        self.assertNotIn("Translated example", json.dumps(model))
        self.assertEqual((prof.data, entries), before)
        del entries[-1]["source_text"]
        self.assertEqual(rules_data(prof, entries)["rules"][0], model)

    def test_fingerprints_bind_storage_operations_and_references(self):
        original = fixture()
        fingerprint = rules_data(original, [])["rules"][0]["capability_sha256"]
        self.assertEqual(len(fingerprint), 64)
        for change in (lambda p: p["table"][0].update(base=80),
                       lambda p: p["language_rules"][0]["routines"][0].update(buffer_bytes=21),
                       lambda p: p["language_rules"][0]["branches"][0]["when"][0].update(value=3)):
            data = deepcopy(original)
            change(data)
            self.assertNotEqual(rules_data(data, [])["rules"][0]["capability_sha256"], fingerprint)
        reordered = dict(reversed(list(original.items())))
        self.assertEqual(rules_data(reordered, [])["rules"][0]["capability_sha256"], fingerprint)

    def test_policy_values_are_separate_from_capability_fingerprints(self):
        prof = policy_fixture()
        original = rules_data(prof, [])["rules"]
        prof["selected_code_replacements"] = {"units": {"unit": "m"}}
        prof["selected_text_prefixes"] = [{"name": "marker"}]
        prof["qa"] = {"droppable": [{"ids": ["s0000010"], "tokens": ["{STR_VAR_1}"]}]}
        current = rules_data(prof, [])["rules"]
        self.assertEqual([row["capability_sha256"] for row in original],
                         [row["capability_sha256"] for row in current])
        self.assertTrue(all(row["policies"][0]["current"]["enabled"] for row in current))
        self.assertEqual(current[0]["policies"][0]["current"]["fields"], {"unit": "m"})
        self.assertEqual(current[0]["policies"][0]["capability"]["calls"],
                         [{"offset": 12, "byte_budget": 2}])
        self.assertNotIn("abcd", json.dumps(current))
        self.assertEqual(current[1]["policies"][0]["capability"],
                         {"pointer": 16, "source": 32, "limits": {}})
        self.assertEqual(current[2]["policies"][0]["current"]["entries"], ["s0000010"])
        prof["qa"]["droppable"][0]["tokens"] = ["{STR_VAR_2}"]
        self.assertFalse(rules_data(prof, [])["rules"][2]["policies"][0]["current"]["enabled"])
        prof["code_replacement"][0]["calls"][0]["guard"] = rom_evidence(12, "cdef")
        self.assertNotEqual(rules_data(prof, [])["rules"][0]["capability_sha256"],
                            original[0]["capability_sha256"])
        prof["text_prefixes"][0]["source"] = rom_evidence(32, "a2ff")
        self.assertNotEqual(rules_data(prof, [])["rules"][1]["capability_sha256"],
                            original[1]["capability_sha256"])

    def test_read_only_fill_sources_survive_snapshot_inventory(self):
        prof = {"language_rules": [{"id": "fills", "title": "Engine-filled text", "kind": "engine_fills",
                                    "description": "Runtime sources.", "evidence": "Synthetic fills.",
                                    "related": [], "limit_status": "unverified",
                                    "references": [{"section": "layout.code_fill"},
                                                   {"section": "context.native_fill"}]}],
                "layout": {"code_fill": [{"name": "fill", "ids": ["s0000010"],
                                          "slots": {"STR_VAR_1": {"strings": ["s0000020"]}}}]},
                "context": {"native_fill": [{"name": "native", "base": 64, "count": 2,
                                             "exclude": [1], "variants": [["s0000030"]]}]},
                "policy_unverified": {"code_fill": ["fill"]}}
        aliases = Aliases([(f"hash:{n:08x}", n * 16, f"s{n * 16:07X}") for n in (1, 2, 3)])
        live = {"entries": [{"id": "s0000020", "text": "Original text", "refs": [{"at": 64}]},
                            {"id": "excluded", "refs": [{"at": 68}]}]}
        offline = {"entries": [{"id": "s0000020", "refs": [[64, "table"]]},
                               {"id": "excluded", "refs": [[68, "table"]]}]}
        doc = rules_data(prof, [], key=aliases.key, inv=live)
        self.assertEqual(doc, rules_data(prof, [], key=aliases.key, inv=offline))
        rule = doc["rules"][0]
        self.assertTrue(rule["read_only"])
        self.assertTrue(rule["collapsed"])
        self.assertEqual(rule["choices"], [])
        code, native = [ref["declarations"][0] for ref in rule["references"]]
        self.assertTrue(code["unverified"])
        self.assertEqual(code["hosts"], ["hash:00000001"])
        self.assertEqual(code["sources"], ["hash:00000002"])
        self.assertEqual(native["sources"], ["hash:00000002", "hash:00000003"])
        self.assertNotIn("Original text", json.dumps(doc))
        self.assertNotIn("excluded", json.dumps(doc))

    def test_keyboard_declaration_has_stable_field_order(self):
        keyboard = {"table": [], "chat": {}, "evidence": "Keyboard reader.", "page_count": 2,
                    "reserved": [], "opening_keyboards": [], "base_pages": 3, "labels": {}, "first_page": 1,
                    "row_bindings": {}, "labels_evidence": "Label reader.", "opening_keyboard_offsets": []}
        prof = {"keyboard": keyboard,
                "language_rules": [{"id": "keyboard", "title": "Keyboard", "kind": "engine_fills",
                                    "description": "Keyboard declarations.", "evidence": "Synthetic keyboard.",
                                    "related": [], "references": [{"section": "keyboard"}]}]}
        facts = rules_data(prof, [])["rules"][0]["references"][0]["declarations"][0]["facts"]
        self.assertEqual(list(facts), ["base_pages", "first_page", "opening_keyboards", "reserved",
                                       "opening_keyboard_offsets", "page_count", "labels", "labels_evidence",
                                       "evidence", "row_bindings", "chat", "table"])

    def test_exported_facts_drop_source_text_field_names(self):
        prof = {"language_rules": [{"id": "fills", "title": "Engine-filled text", "kind": "engine_fills",
                                    "description": "Runtime sources.", "evidence": "Synthetic fills.",
                                    "related": [], "limit_status": "unverified",
                                    "references": [{"section": "layout.code_fill"}]}],
                "layout": {"code_fill": [{"name": "fill", "ids": ["s0000010"],
                                          "keyboards": [{"page": 0, "source_text": "Original text"}],
                                          "slots": {"STR_VAR_1": {"strings": ["s0000020"]}}}]}}
        aliases = Aliases([(f"hash:{n:08x}", n * 16, f"s{n * 16:07X}") for n in (1, 2)])
        facts = rules_data(prof, [], key=aliases.key, inv={"entries": []})["rules"][0]["references"][0]["declarations"][0]["facts"]
        self.assertEqual(facts["keyboards"], [{"page": 0}])

    def test_snapshot_configuration_binds_rule_capabilities_and_selections(self):
        prof = policy_fixture()
        baseline = configuration(prof)
        for section, value in (("selected_code_replacements", {"units": {"unit": "m"}}),
                               ("code_replacement", []), ("language_rules", []),
                               ("text_prefixes", []), ("text_controls", {}),
                               ("selected_text_prefixes", [{"name": "marker"}])):
            with self.subTest(section=section):
                self.assertNotEqual(configuration({**prof, section: value}), baseline)


class CatalogPreviewTest(unittest.TestCase):
    def setUp(self):
        self.cm = Charmap()
        self.data = plural_profile()
        self.rows = [{"id": binding["entry"], "translation": "", "source_text": self.cm.decode(
            original_values()[operand["id"]] + b"\xff")[0], "note": "kept"}
            for operand in self.data["language_rules"][0]["operands"] for binding in operand["bindings"]]
        self.rows.append({"id": "table:names/0", "source_text": "Berry", "translation": "BERRY"})

    def preview(self, request=None, drafts=None, cm=None, glyphs=None):
        model = rules_data(self.data, self.rows)["rules"][0]
        return catalog_preview(model, self.rows, drafts or [], request or {"example": "table:names/0"},
                               cm or self.cm, glyphs)

    def off(self):
        return {operand["id"]: {"choice": "off"} for operand in self.data["language_rules"][0]["operands"]}

    def test_three_states_and_grouped_off_keep_replacement_letters(self):
        before = deepcopy((self.data, self.rows))
        report = self.preview({"operands": self.off(), "example": "table:names/0"})
        self.assertTrue(report["valid"])
        self.assertEqual([r["text"] for r in report["original"]["results"]], ["Berries"] * 2)
        self.assertEqual([r["text"] for r in report["current"]["results"]], ["BERRIES"] * 2)
        self.assertEqual([r["text"] for r in report["proposed"]["results"]], ["BERRY"] * 2)
        self.assertEqual(len(report["changes"]), 10)
        self.assertEqual([r["translation"] for r in report["changes"]], ["", "", "y", "y", "", "", "Y", "Y", "", ""])
        self.assertEqual([r["used"] for r in report["proposed"]["results"]], [8, 8])
        self.assertTrue(all((r["used"], r["limit"]) == (1, 1) for r in report["proposed"]["fields"]))
        self.assertEqual((self.data, self.rows), before)

    def test_drafts_and_example_edits_only_change_proposed(self):
        drafts = [{"id": "table:names/0", "translation": "Max", "state": "text"}]
        report = self.preview(drafts=drafts)
        self.assertEqual(report["proposed"]["results"][0]["text"], "Maxes")
        self.assertEqual(report["current"]["results"][0]["text"], "BERRIES")
        for quantity, expected in ((0, "Berry"), (1, "Berry"), (2, "Berries")):
            self.assertEqual(self.preview({"name": "Berry", "quantity": quantity})["proposed"]["results"][0]["text"], expected)

    def test_custom_bytes_are_checked_with_the_selected_encoding(self):
        request = {"name": "Berry", "operands": {"lower": {"choice": "custom", "text": "Ω"}}}
        report = self.preview(request, cm=Charmap({"Ω": 0x37}))
        self.assertTrue(report["valid"])
        self.assertEqual(report["proposed"]["results"][0]["text"], "BerrΩes")
        field = next(r for r in report["proposed"]["fields"] if r["operand"] == "lower")
        self.assertEqual((field["bytes"], field["used"]), ([0x37], 1))
        self.assertFalse(self.preview(request, cm=Charmap({"Ω": 0x37}), glyphs=set(range(0x38, 256)))["valid"])

    def test_unsafe_custom_arithmetic_controls_and_overflow_cannot_apply(self):
        for operand, value in (("ordinary", "a"), ("lower", "ab"), ("lower", "{PLAYER}"), ("lower", "")):
            report = self.preview({"operands": {operand: {"choice": "custom", "text": value}}})
            self.assertFalse(report["valid"], (operand, value))
            self.assertEqual(report["changes"], [])
        self.rows[-1]["translation"] = "a" * 17 + "y"
        report = self.preview({"operands": self.off(), "name": "short"})
        self.assertFalse(report["valid"])
        self.assertIn("buffer overflow: 21/20", " ".join(report["proposed"]["problems"]))

    def test_unavailable_originals_are_not_invented_and_off_works_offline(self):
        for row in self.rows:
            del row["source_text"]
        report = self.preview({"operands": self.off(), "example": "table:names/0"})
        self.assertTrue(report["valid"])
        self.assertTrue(report["original"]["unavailable"])
        self.assertIsNone(report["original"]["results"][0]["text"])
        request = {"operands": {key: {"choice": "original"} for key in self.off()}}
        self.assertFalse(self.preview(request)["valid"])

    def test_mismatched_routines_and_read_only_entries_block_apply(self):
        self.rows[0].update(flags=["empty"])
        self.assertIn("routine copies differ", " ".join(self.preview()["proposed"]["problems"]))
        self.assertTrue(self.preview({"operands": self.off()})["valid"])
        self.rows[0]["owner"] = "pack"
        self.assertFalse(self.preview({"operands": self.off()})["valid"])

    def test_terminated_literal_counts_spaces_and_terminator(self):
        self.data = single_rule(size=4, terminated=True, custom=(1, 3))
        self.data["language_rules"][0]["examples"] = []
        self.rows = [{"id": "table:field/0", "source_text": "’s ", "translation": ""}]
        request = {"name": "Name", "operands": {"ending": {"choice": "custom", "text": ": "}}}
        report = self.preview(request)
        self.assertTrue(report["valid"])
        self.assertEqual(report["proposed"]["fields"][0]["used"], 3)
        self.assertEqual(report["proposed"]["results"][0]["text"], "Name: ")
        request["operands"]["ending"]["text"] = "four"
        report = self.preview(request)
        self.assertFalse(report["valid"])
        self.assertEqual(report["proposed"]["fields"][0]["used"], 5)

    def test_fixed_prefix_length_does_not_offer_off(self):
        self.data = single_rule(size=4, terminated=True, custom=(3, 3), off=False)
        rule = self.data["language_rules"][0]
        rule["examples"] = []
        rule["branches"][0]["operations"] = [{"op": "strip_prefix", "operand": "ending", "count": 3}]
        self.rows = [{"id": "table:field/0", "source_text": "ABC", "translation": ""}]
        report = self.preview({"name": "ABCName", "operands": {"ending": {"choice": "custom", "text": "ABC"}}})
        self.assertEqual(report["proposed"]["results"][0]["text"], "Name")
        self.assertFalse(self.preview({"operands": {"ending": {"choice": "off"}}})["valid"])

    def test_request_validation_and_checker_json_integration(self):
        for request in ({"quantity": -1}, {"quantity": 1.5}, {"quantity": True}, {"example": "absent"},
                        {"operands": {"unknown": {"choice": "off"}}}):
            with self.assertRaises(RuleError):
                self.preview(request)
        doc = {"fonts": {"main": "main", "styles": {"main": {"pack": {}, "format": "16x16"}}},
               "charmap": {}, "placeholders": {"widths": {}, "default": 60},
               "engine_rules": rules_data(self.data, self.rows)}
        checker = Checker(doc, {"main": bytes([6] * 256)})
        output = checker.rule_preview_json("plurals", json.dumps(self.rows), "[]", '{"name": "Berry"}')
        self.assertEqual(json.loads(output)["proposed"]["results"][0]["text"], "Berries")


if __name__ == "__main__":
    unittest.main()
