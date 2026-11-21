"""Engine rule metadata rejects unsafe or unresolved declarations."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from gen3tl.profile.languagerules import load
from gen3tl.profile.profile import Profile
from gen3tl.profile.schema import ProfileError


def fixture():
    return {
        "table": [
            {"name": "ending", "base": 32, "stride": 4, "count": 2,
             "field_offset": 1, "field_length": 1, "terminated": False, "pad": 255},
            {"name": "copy", "base": 64, "stride": 4, "count": 2,
             "field_offset": 1, "field_length": 1, "terminated": False, "pad": 255},
        ],
        "language_rules": [{
            "id": "plural", "title": "Count ending", "kind": "catalog",
            "description": "Two routines share the same fixed byte arithmetic.",
            "evidence": "Synthetic immediate stores with separate buffers.",
            "related": ["hash:01234567"], "examples": ["table:names/1"],
            "terminator": 255, "choices": ["original", "off"],
            "routines": [{"id": "main", "at": 16, "buffer_bytes": 20},
                         {"id": "copy", "at": 48, "buffer_bytes": 32}],
            "operands": [{
                "id": "suffix", "choices": ["original", "off"],
                "off": [255], "allowed_bytes": [230, 255],
                "bindings": [{"table": "ending", "index": 1, "entry": "table:ending/1", "routine": "main"},
                             {"table": "copy", "index": 1, "entry": "table:copy/1", "routine": "copy"}],
            }],
            "branches": [{"id": "plural", "when": [{"op": "quantity_gt", "value": 1}],
                          "operations": [{"op": "write", "at": 0,
                                          "bytes": [{"operand": "suffix"}, {"operand": "suffix", "add": 25}]}]}],
        }],
    }


class RuleMetadataTest(unittest.TestCase):
    def test_undeclared_profiles_remain_supported(self):
        self.assertEqual(Profile({}, Path("fixture.toml")).language_rules, ())

    def test_grouped_bindings_derive_offsets_and_budgets(self):
        data = fixture()
        before = deepcopy(data)
        rules = load(data)
        bindings = rules[0]["operands"][0]["bindings"]
        self.assertEqual([b["offset"] for b in bindings], [37, 69])
        self.assertEqual([b["byte_budget"] for b in bindings], [1, 1])
        self.assertEqual([b["terminated"] for b in bindings], [False, False])
        self.assertEqual(data, before)
        rules[0]["branches"][0]["operations"][0]["bytes"].append(0)
        self.assertEqual(data, before)

    def test_unknown_operation_rejected_by_profile_constructor(self):
        data = fixture()
        data["language_rules"][0]["branches"][0]["operations"][0]["op"] = "patch"
        with self.assertRaisesRegex(ProfileError, "unknown operation"):
            Profile(data, Path("fixture.toml"))

    def test_unknown_operation_rejected_from_flat_file_and_overlay(self):
        body = '''[[language_rules]]
id = "bad"
title = "Invalid rule"
description = "Unknown operation."
kind = "policy"
related = []
evidence = "Synthetic operation."
branches = [{id = "all", when = [], operations = [{op = "execute"}]}]
'''
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "flat.toml").write_text(body)
            (root / "profile.toml").write_text("[discovery]\nschema = 1\n")
            (root / "overlay.toml").write_text(body)
            for path in (root / "flat.toml", root):
                with self.subTest(path=path), self.assertRaisesRegex(ProfileError, "unknown operation"):
                    Profile.load(path)

    def test_invalid_operations_and_conditions(self):
        cases = [
            ("operations", [{"op": "write", "at": 0, "bytes": [{"operand": "missing"}]}], "unknown operand"),
            ("operations", [{"op": "write", "at": 0, "bytes": [256]}], "invalid integer"),
            ("operations", [{"op": "write", "at": 0, "bytes": [True]}], "invalid integer"),
            ("operations", [{"op": "write", "at": 0, "bytes": [{"operand": "suffix", "add": -1}]}], "invalid integer"),
            ("operations", [{"op": "write", "at": 0, "bytes": [255], "address": 99}], "unknown keys"),
            ("operations", [{"op": "append", "operand": "suffix", "bytes": [255]}], "one byte source"),
            ("operations", [{"op": "append", "operand": "suffix"}], "terminated field"),
            ("operations", [{"op": "append", "bytes": [1, 255], "buffer_bytes": 1}], "temporary buffer"),
            ("when", [{"op": "unicode_lowercase"}], "unknown condition"),
            ("when", [{"op": "last_in", "bytes": [256]}], "invalid integer"),
            ("when", [{"op": "quantity_gt", "value": True}], "invalid integer"),
        ]
        for field, value, error in cases:
            with self.subTest(value=value), self.assertRaisesRegex(ProfileError, error):
                data = fixture()
                data["language_rules"][0]["branches"][0][field] = value
                load(data)

    def test_grouped_operands_require_matching_storage_and_every_routine(self):
        cases = [("table", "missing", "unknown or ambiguous"), ("index", 2, "outside"),
                 ("routine", "missing", "unknown routine")]
        for field, value, error in cases:
            with self.subTest(field=field), self.assertRaisesRegex(ProfileError, error):
                data = fixture()
                data["language_rules"][0]["operands"][0]["bindings"][0][field] = value
                load(data)
        data = fixture()
        data["table"][1]["field_length"] = 2
        with self.assertRaisesRegex(ProfileError, "different storage"):
            load(data)
        data = fixture()
        data["language_rules"][0]["operands"][0]["bindings"].pop()
        with self.assertRaisesRegex(ProfileError, "every routine"):
            load(data)

    def test_presets_and_custom_fields_are_explicit(self):
        data = fixture()
        operand = data["language_rules"][0]["operands"][0]
        operand["off"] = [237]
        with self.assertRaisesRegex(ProfileError, "outside allowed"):
            load(data)
        del operand["allowed_bytes"]
        self.assertEqual(load(data)[0]["operands"][0]["off"], [237])
        operand["choices"].append("custom")
        with self.assertRaisesRegex(ProfileError, "needs byte limits"):
            load(data)
        operand["custom_bytes"] = [1, 2]
        with self.assertRaisesRegex(ProfileError, "invalid integer"):
            load(data)
        operand["custom_bytes"] = [1, 1]
        self.assertEqual(load(data)[0]["operands"][0]["custom_bytes"], [1, 1])
        del operand["off"]
        with self.assertRaisesRegex(ProfileError, "off needs explicit"):
            load(data)

    def test_literal_capacity_reserves_terminator_and_strip_count(self):
        data = fixture()
        rule = data["language_rules"][0]
        for table in data["table"]:
            table.update(field_length=6, terminated=True)
        operand = rule["operands"][0]
        operand.update(choices=["original", "custom"], custom_bytes=[5, 5])
        del operand["off"], operand["allowed_bytes"]
        rule["branches"][0]["operations"] = [{"op": "strip_prefix", "operand": "suffix", "count": 5}]
        self.assertEqual(load(data)[0]["operands"][0]["bindings"][0]["byte_budget"], 6)
        operand["custom_bytes"] = [1, 5]
        with self.assertRaisesRegex(ProfileError, "exact custom length"):
            load(data)
        operand["custom_bytes"] = [6, 6]
        with self.assertRaisesRegex(ProfileError, "invalid integer"):
            load(data)

    def test_duplicate_rules_and_overlapping_storage_rejected(self):
        data = fixture()
        data["language_rules"].append(deepcopy(data["language_rules"][0]))
        with self.assertRaisesRegex(ProfileError, "duplicate rule"):
            load(data)
        data["language_rules"][1]["id"] = "other"
        with self.assertRaisesRegex(ProfileError, "overlapping"):
            load(data)

    def test_branch_identity_and_output_initialization_are_explicit(self):
        data = fixture()
        rule = data["language_rules"][0]
        rule["output"] = "empty"
        self.assertEqual(load(data)[0]["output"], "empty")
        rule["output"] = "arbitrary"
        with self.assertRaisesRegex(ProfileError, "unknown output"):
            load(data)
        rule["output"] = "input"
        rule["branches"].append(deepcopy(rule["branches"][0]))
        with self.assertRaisesRegex(ProfileError, "duplicate branch"):
            load(data)

    def test_policy_capabilities_and_fields_resolve(self):
        data = {
            "code_replacement": [{"name": "units", "routine": {"words": ["unit"]}}],
            "text_prefixes": [{"name": "marker"}],
            "text_controls": {"droppable": {"article": {"ids": ["hash:12345678"]}}},
            "language_rules": [{"id": "format", "title": "Format", "description": "Selected capabilities.",
                                "kind": "policy", "related": ["hash:12345678"], "evidence": "Declared sources.",
                                "policies": [{"kind": "code_replacement", "name": "units", "fields": ["unit"]},
                                             {"kind": "text_prefix", "name": "marker"},
                                             {"kind": "droppable", "name": "article"}]}],
        }
        self.assertEqual(len(load(data)[0]["policies"]), 3)
        policy = data["language_rules"][0]["policies"][0]
        for field, value, error in [("kind", "raw_patch", "unknown policy kind"),
                                    ("name", "missing", "unknown or ambiguous"),
                                    ("fields", ["unknown"], "unknown policy fields")]:
            with self.subTest(field=field), self.assertRaisesRegex(ProfileError, error):
                changed = deepcopy(data)
                changed["language_rules"][0]["policies"][0][field] = value
                load(changed)
        self.assertEqual(policy["name"], "units")

    def test_runtime_fill_groups_are_read_only_references(self):
        data = {"layout": {"code_fill": [{"name": "counter", "ids": ["hash:12345678"]}]},
                "context": {"native_fill": [{"name": "names", "slot": 0}]},
                "language_rules": [{"id": "fills", "title": "Engine-filled text", "kind": "engine_fills",
                                    "description": "Runtime values.", "related": [], "evidence": "Declared fills.",
                                    "references": [{"section": "layout.code_fill"},
                                                   {"section": "context.native_fill", "name": "names"}]}]}
        self.assertEqual(len(load(data)[0]["references"]), 2)
        data["language_rules"][0]["choices"] = ["off"]
        with self.assertRaisesRegex(ProfileError, "read-only"):
            load(data)
        del data["language_rules"][0]["choices"]
        data["language_rules"][0]["references"][0]["section"] = "arbitrary"
        with self.assertRaisesRegex(ProfileError, "unknown reference"):
            load(data)


if __name__ == "__main__":
    unittest.main()
