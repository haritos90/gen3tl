"""Byte evaluation uses synthetic profiles and explicit encoded operands."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gen3tl.catalog.po import PoEntry
from gen3tl.check import languagerules as rules, qa
from gen3tl.check.layout import Measure
from gen3tl.core.charmap import Charmap
from gen3tl.profile.languagerules import load


CM = Charmap()


def byte(text):
    return CM.encode(text, terminate=False)[0]


def plural_profile():
    tables, operands = [], []
    for index, (name, original, off, custom) in enumerate([
        ("ordinary", "s", "", False), ("lower", "i", "y", True),
        ("lower-ending", "e", "", False), ("upper", "I", "Y", True),
        ("upper-ending", "E", "", False),
    ]):
        bindings = []
        for routine, base in (("normal", 64), ("trade", 128)):
            table = f"{name}-{routine}"
            tables.append({"name": table, "base": base + index, "count": 1,
                           "stride": 1, "field_length": 1, "terminated": False})
            bindings.append({"table": table, "index": 0, "routine": routine,
                             "entry": f"table:{table}/0"})
        operand = {"id": name, "bindings": bindings, "choices": ["original", "off"],
                   "off": [byte(off) if off else 255]}
        if custom:
            operand.update(choices=["original", "off", "custom"], custom_bytes=[1, 1])
        else:
            operand["allowed_bytes"] = [byte(original), 255]
        operands.append(operand)
    branches = []
    for letter, replacement, ending, delta in [
        ("y", "lower", "lower-ending", 38), ("x", None, "lower-ending", 38),
        ("Y", "upper", "upper-ending", 64), ("X", None, "upper-ending", 64),
    ]:
        operations = []
        if replacement:
            operations.append({"op": "write", "at": -1, "bytes": [{"operand": replacement}]})
        operations.append({"op": "write", "at": 0, "bytes": [
            {"operand": ending}, {"operand": ending, "add": 14}, {"operand": ending, "add": delta}]})
        branches.append({"id": letter, "when": [{"op": "quantity_gt", "value": 1},
                                                {"op": "last_in", "bytes": [byte(letter)]}],
                         "operations": operations})
    branches.append({"id": "ordinary", "when": [{"op": "quantity_gt", "value": 1},
                                               {"op": "last_not_in", "bytes": list(CM.encode("sSyYxX", False))}],
                     "operations": [{"op": "write", "at": 0,
                                     "bytes": [{"operand": "ordinary"}, {"operand": "ordinary", "add": 24}]}]})
    return {"table": tables, "language_rules": [{
        "id": "plurals", "kind": "catalog", "title": "Name endings",
        "description": "Fixed final-byte branches.", "evidence": "Synthetic byte operations.",
        "related": [], "examples": ["table:names/0"], "terminator": 255,
        "operands": operands, "branches": branches,
        "routines": [{"id": "normal", "at": 16, "buffer_bytes": 20},
                     {"id": "trade", "at": 32, "buffer_bytes": 32}],
    }]}


def original_values():
    return {name: CM.encode(text, False) for name, text in
            (("ordinary", "s"), ("lower", "i"), ("lower-ending", "e"),
             ("upper", "I"), ("upper-ending", "E"))}


def single_rule(*, size=1, terminated=False, off=True, custom=(1, 1)):
    data = plural_profile()
    data["table"] = [{"name": "field", "base": 8, "count": 1, "stride": size,
                      "field_length": size, "terminated": terminated}]
    rule = data["language_rules"][0]
    rule["routines"] = [{"id": "main", "at": 16, "buffer_bytes": 13}]
    operand = {"id": "ending", "choices": ["original", "custom"], "custom_bytes": list(custom),
               "bindings": [{"table": "field", "index": 0, "entry": "table:field/0", "routine": "main"}]}
    if off:
        operand["choices"].append("off")
        operand["off"] = [255]
    rule["operands"] = [operand]
    rule["branches"] = [{"id": "all", "when": [], "operations": [
        {"op": "append", "operand": "ending"} if terminated else
        {"op": "write", "at": 0, "bytes": [{"operand": "ending"}, 255]}]}]
    return data


class EvaluationTest(unittest.TestCase):
    def setUp(self):
        self.rule = load(plural_profile())[0]
        self.values = original_values()

    def evaluate(self, text, **kwargs):
        return rules.evaluate(self.rule, CM.encode(text), self.values, **kwargs)

    def test_every_branch_in_both_routines(self):
        for routine in ("normal", "trade"):
            for text, expected, branch in [("Tonic", "Tonics", "ordinary"), ("Berry", "Berries", "y"),
                                            ("Box", "Boxes", "x"), ("KEY", "KEIES", "Y"),
                                            ("BOX", "BOXES", "X"), ("Lens", "Lens", None),
                                            ("LENS", "LENS", None)]:
                with self.subTest(routine=routine, text=text):
                    result = self.evaluate(text, routine=routine, quantity=2)
                    self.assertEqual(result.result, CM.encode(expected))
                    self.assertEqual(result.branch, branch)
                    self.assertFalse(result.problems)
                    for count in (0, 1):
                        self.assertEqual(self.evaluate(text, routine=routine, quantity=count).result, CM.encode(text))

    def test_off_preserves_replaced_letters_and_counts_later_stores(self):
        self.values = {operand["id"]: bytes(operand["off"]) for operand in self.rule["operands"]}
        for routine in ("normal", "trade"):
            for text, tail in [("Tonic", [255, 23]), ("Berry", [255, 13, 37]),
                               ("Box", [255, 13, 37]), ("KEY", [255, 13, 63]), ("BOX", [255, 13, 63])]:
                with self.subTest(routine=routine, text=text):
                    result = self.evaluate(text, routine=routine, quantity=2)
                    self.assertEqual(result.result, CM.encode(text))
                    self.assertEqual(result.stored[-len(tail):], bytes(tail))
                    self.assertEqual(result.used_bytes, len(text) + len(tail))

    def test_exact_buffer_limits_include_invisible_writes(self):
        for off in (False, True):
            if off:
                self.values = {operand["id"]: bytes(operand["off"]) for operand in self.rule["operands"]}
            for text in ("a" * 19, "a" * 17 + "y"):
                with self.subTest(off=off, text=text):
                    self.assertEqual(self.evaluate(text[1:], routine="normal", quantity=2).used_bytes, 20)
                    result = self.evaluate(text, routine="normal", quantity=2)
                    self.assertIn("buffer overflow: 21/20 bytes", result.problems)
                    self.assertFalse(self.evaluate(text, routine="trade", quantity=2).problems)

    def test_all_operands_checked_even_in_an_inactive_branch(self):
        for name, value in [("ordinary", b"\xe8"), ("lower-ending", b"\xd8"),
                            ("upper-ending", b"\xbe"), ("lower", b"\xff"),
                            ("upper", b"\xfd"), ("lower", b"\xd5\xd5")]:
            with self.subTest(name=name, value=value), self.assertRaises(rules.RuleError):
                rules.evaluate(self.rule, CM.encode("Lens"), {**self.values, name: value}, routine="trade")

    def test_fixed_byte_comparison_ignores_visual_similarity(self):
        cm = Charmap({"λ": 0x30})
        self.values["lower"] = b"\x30"
        result = rules.evaluate(self.rule, cm.encode("Berry"), self.values, routine="normal", quantity=2, cm=cm)
        self.assertEqual(result.result, cm.encode("Berrλes"))
        result = rules.evaluate(self.rule, cm.encode("Berrλ"), self.values, routine="normal", quantity=2, cm=cm)
        self.assertEqual(result.branch, "ordinary")

    def test_missing_computed_terminator_is_reported(self):
        self.rule["branches"][-1]["operations"][0]["bytes"][1]["add"] = 23
        result = self.evaluate("Tonic", routine="normal", quantity=2)
        self.assertIn("output missing terminator", result.problems)

    def test_invalid_inputs_and_identities_rejected(self):
        for kwargs in ({}, {"routine": "missing"}, {"routine": "normal", "quantity": -1},
                       {"routine": "normal", "quantity": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(rules.RuleError):
                self.evaluate("Tonic", **kwargs)
        with self.assertRaisesRegex(rules.RuleError, "identities"):
            rules.evaluate(self.rule, CM.encode("Tonic"), {}, routine="normal")
        with self.assertRaisesRegex(rules.RuleError, "terminator"):
            rules.evaluate(self.rule, b"\xd5", self.values, routine="normal")

    def test_unknown_operations_and_underflow_rejected(self):
        self.rule["branches"] = [{"id": "all", "when": [], "operations": [{"op": "execute"}]}]
        with self.assertRaisesRegex(rules.RuleError, "unknown operation"):
            self.evaluate("Tonic", routine="normal")
        self.rule["branches"][0]["operations"] = [{"op": "write", "at": -1, "bytes": [255]}]
        with self.assertRaisesRegex(rules.RuleError, "outside"):
            self.evaluate("", routine="normal")


class OtherEndingsTest(unittest.TestCase):
    def test_quantity_conditions_and_empty_output(self):
        for op, expected in (("quantity_ne", ["s", "", "s"]), ("quantity_gt", ["", "", "s"])):
            data = single_rule()
            declaration = data["language_rules"][0]
            declaration["output"] = "empty"
            declaration["branches"][0]["when"] = [{"op": op, "value": 1}]
            rule = load(data)[0]
            for count in (0, 1, 2):
                result = rules.evaluate(rule, CM.encode("Host"), {"ending": b"\xe7"}, quantity=count)
                self.assertEqual(result.result, CM.encode(expected[count]))

    def test_popup_counts_all_writes_after_first_empty_operand(self):
        data = single_rule()
        rule = data["language_rules"][0]
        second = deepcopy(rule["operands"][0])
        second["id"] = "second"
        second["bindings"][0].update(index=1, entry="table:field/1")
        data["table"][0]["count"] = 2
        rule["operands"].append(second)
        rule["branches"] = [
            {"id": "short", "when": [{"op": "last_in", "bytes": [byte("s"), byte("S")]}],
             "operations": [{"op": "write", "at": 0, "bytes": [{"operand": "ending"}, 255]}]},
            {"id": "full", "when": [], "operations": [{"op": "write", "at": 0,
                "bytes": [{"operand": "ending"}, {"operand": "second"}, 255]}]},
        ]
        rule = load(data)[0]
        for first in (byte("’"), 255):
            values = {"ending": bytes([first]), "second": b"\xe7"}
            for text, used in (("a" * 10, 13), ("a" * 10 + "s", 13), ("a" * 11, 14)):
                result = rules.evaluate(rule, CM.encode(text), values)
                self.assertEqual(result.used_bytes, used)
                self.assertEqual(bool(result.problems), used > 13)
                if first == 255:
                    self.assertEqual(result.result, CM.encode(text))

    def test_temporary_stores_are_separate_from_append_copy(self):
        data = single_rule()
        rule = data["language_rules"][0]
        rule["operands"][0].update(choices=["original", "off"], allowed_bytes=[180, 255])
        del rule["operands"][0]["custom_bytes"]
        rule["branches"][0]["operations"] = [{"op": "append", "buffer_bytes": 3,
            "bytes": [{"operand": "ending"}, 0, {"operand": "ending", "add": 75}]}]
        rule = load(data)[0]
        for value, suffix, temporary in ((180, "’ ", b"\xb4\0\xff"), (255, "", b"\xff\0\x4a")):
            result = rules.evaluate(rule, CM.encode("Lens"), {"ending": bytes([value])})
            self.assertEqual(result.result, CM.encode("Lens" + suffix))
            self.assertEqual(result.used_bytes, len("Lens" + suffix) + 1)
            self.assertEqual(result.temporary, (temporary,))

    def test_append_requires_terminated_source_and_reports_unknown_limits(self):
        data = single_rule()
        declaration = data["language_rules"][0]
        del declaration["routines"][0]["buffer_bytes"]
        declaration["branches"][0]["operations"] = [{"op": "append", "buffer_bytes": 2,
            "bytes": [{"operand": "ending"}, 0]}]
        rule = load(data)[0]
        result = rules.evaluate(rule, CM.encode("Name"), {"ending": b"\xe7"})
        self.assertIsNone(result.buffer_bytes)
        self.assertIn("append source or destination missing terminator", result.problems)
        self.assertEqual(result.temporary, (b"\xe7\0",))
        self.assertEqual(result.result, CM.encode("Name"))

    def test_literal_budget_and_stripping_count(self):
        rule = load(single_rule(size=4, terminated=True, custom=(1, 3)))[0]
        operand = rule["operands"][0]
        for text in ("", ": ", "abc"):
            value = rules.encode_operand(operand, text, 255, CM)
            result = rules.evaluate(rule, CM.encode("Name"), {"ending": value})
            self.assertEqual(result.result, CM.encode("Name" + text))
        with self.assertRaises(rules.RuleError):
            rules.encode_operand(operand, "abcd", 255, CM)
        data = single_rule(size=4, terminated=True, off=False, custom=(3, 3))
        data["language_rules"][0]["branches"][0]["operations"] = [
            {"op": "strip_prefix", "operand": "ending", "count": 3}]
        rule = load(data)[0]
        for text, expected in (("A: Name", "Name"), ("B: Name", "B: Name")):
            result = rules.evaluate(rule, CM.encode(text), {"ending": CM.encode("A: ")})
            self.assertEqual(result.result, CM.encode(expected))
        for text in ("", "A:", "A:  "):
            with self.assertRaises(rules.RuleError):
                rules.evaluate(rule, CM.encode("Name"), {"ending": CM.encode(text)})

    def test_literal_only_encoding_and_glyph_availability(self):
        operand = load(single_rule())[0]["operands"][0]
        for text in ("{PLAYER}", "{FF}", "{D5}", "\\n", "ab", "\n", "☃"):
            with self.subTest(text=text), self.assertRaises(rules.RuleError):
                rules.encode_operand(operand, text, 255, CM)
        cm = Charmap({"λ": 0x30})
        with self.assertRaisesRegex(rules.RuleError, "unavailable glyph"):
            rules.encode_operand(operand, "λ", 255, cm, glyphs=set(CM.decode_table))
        self.assertEqual(rules.encode_operand(operand, "λ", 255, cm, glyphs={0x30}), b"\x30")

    def test_first_byte_conditions_inspect_input_when_output_empty(self):
        data = single_rule()
        rule = data["language_rules"][0]
        rule["output"] = "empty"
        rule["branches"][0]["when"] = [{"op": "nonempty"}, {"op": "first_in", "bytes": [byte("A")]}]
        rule = load(data)[0]
        for text, expected in (("Aname", "n"), ("aname", ""), ("", "")):
            result = rules.evaluate(rule, CM.encode(text), {"ending": CM.encode("n", False)})
            self.assertEqual(result.result, CM.encode(expected))


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.data = plural_profile()
        self.catalog = {}
        for operand in self.data["language_rules"][0]["operands"]:
            for binding in operand["bindings"]:
                key = binding["entry"]
                original = CM.decode(original_values()[operand["id"]] + b"\xff")[0]
                self.catalog[key] = (Path("operands.po"), PoEntry(key, original))
        key = "table:names/0"
        self.catalog[key] = (Path("names.po"), PoEntry(key, "Berry"))

    def test_original_and_off_catalog_states(self):
        self.assertEqual(rules.catalog_problems(self.data, self.catalog, CM), [])
        for operand in self.data["language_rules"][0]["operands"]:
            for binding in operand["bindings"]:
                entry = self.catalog[binding["entry"]][1]
                entry.msgstr = CM.decode(bytes(operand["off"]) + b"\xff")[0]
                if not entry.msgstr:
                    entry.flags = ["empty"]
        self.assertEqual(rules.catalog_problems(self.data, self.catalog, CM), [])

    def test_a_catalog_without_any_operand_entry_skips_the_rule(self):
        names = {key: value for key, value in self.catalog.items() if key == "table:names/0"}
        self.assertEqual(rules.catalog_problems(self.data, names, CM), [])
        partial = dict(self.catalog)
        missing = self.data["language_rules"][0]["operands"][0]["bindings"][0]["entry"]
        del partial[missing]
        self.assertTrue(any("missing operand entry" in p.message
                            for p in rules.catalog_problems(self.data, partial, CM)))

    def test_original_only_operand_uses_the_source_baseline(self):
        data = single_rule()
        operand = data["language_rules"][0]["operands"][0]
        operand["choices"] = ["original", "off"]
        del operand["custom_bytes"]
        key = "table:field/0"
        catalog = {key: (Path("field.po"), PoEntry(key, "s"))}
        self.assertEqual(rules.catalog_problems(data, catalog, CM), [])
        catalog[key][1].msgstr = "a"
        self.assertTrue(any("unsupported operand" in p.message for p in rules.catalog_problems(data, catalog, CM)))

    def test_aliases_grouped_mismatch_and_empty_replacement(self):
        key = "table:ordinary-trade/0"
        path, entry = self.catalog.pop(key)
        entry.flags = ["empty"]
        self.catalog["s0000100"] = (path, entry)
        problems = rules.catalog_problems(self.data, self.catalog, CM)
        self.assertEqual({p.entry for p in problems}, {"table:ordinary-normal/0", "s0000100"})
        self.assertTrue(all("copies differ" in p.message for p in problems))
        self.catalog["table:lower-normal/0"][1].flags = ["empty"]
        self.assertTrue(any("custom byte count" in p.message
                            for p in rules.catalog_problems(self.data, self.catalog, CM)))

    def test_key_only_unavailable_original_is_explicit(self):
        entry = self.catalog["table:ordinary-normal/0"][1]
        entry.msgid, entry.keyed = entry.msgctxt, True
        problems = rules.catalog_problems(self.data, self.catalog, CM)
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].severity, "warning")
        self.assertIn("original operand unavailable", problems[0].message)
        entry.flags = ["empty"]
        self.assertTrue(any("copies differ" in p.message
                            for p in rules.catalog_problems(self.data, self.catalog, CM)))

    def test_example_buffer_overflow_is_attached_to_name(self):
        self.catalog["table:names/0"][1].msgstr = "a" * 17 + "y"
        problems = rules.catalog_problems(self.data, self.catalog, CM)
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].entry, "table:names/0")
        self.assertIn("plurals/normal: buffer overflow: 21/20", problems[0].message)

    def test_qa_reports_invalid_intentional_empty_before_skipping_blank_text(self):
        key = "table:lower-normal/0"
        self.catalog[key][1].flags = ["empty"]
        with tempfile.TemporaryDirectory() as tmp, patch.object(qa, "load_catalog", return_value=self.catalog):
            gate = qa.run(self.data, {"entries": []}, Path(tmp), CM, Measure(lambda b: 6, {}), set())
        errors = [finding for finding in gate.findings if "engine language rule" in finding.message]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, key)


if __name__ == "__main__":
    unittest.main()
