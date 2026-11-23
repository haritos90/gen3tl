"""Bounded native writes replace earlier script buffer values."""

import copy
import unittest
from pathlib import Path
from unittest.mock import patch

from gen3tl.discover import context, maps
from gen3tl.check import qa
from gen3tl.core.charmap import Charmap
from gen3tl.check.layout import Measure
from gen3tl.catalog.po import PoEntry
from gen3tl.core.rom import Rom, ptr_bytes


class NativeFillTest(unittest.TestCase):
    def setUp(self):
        self.rule = {"name": "choices", "address": 0x501, "slot": 0, "base": 0x800,
                     "stride": 4, "count": 2, "exclude": [0],
                     "variants": [["s0000900", "s0000B00"]], "evidence": "verified callback"}
        self.prof = {"maps": {}, "context": {"native_fill": [self.rule]}}
        rows = [(0x900, "Base", 0x800), (0xA00, "Long", 0x804), (0xB00, " tail", 0x808),
                (0xC00, "Stale", 0x102), (0xD00, "{STR_VAR_1}!", 0x10C)]
        self.inv = {"entries": [{"id": f"s{at:07X}", "offset": at, "kind": "pointer", "text": text,
                                 "refs": [{"at": ref, "kind": "pool"}]} for at, text, ref in rows]}
        self.cat = {e["id"]: (Path("fixture.po"), PoEntry(e["id"], e["text"], "")) for e in self.inv["entries"]}
        self.cm, self.measure = Charmap(), Measure(lambda _: 6, {"STR_VAR_1": 60})

    def test_callback_replaces_earlier_string_in_context(self):
        rom = Rom(bytes([255]) * 0x1000)
        rom.write(0x100, b"\x85\x00" + ptr_bytes(0xC00) + b"\x23" + ptr_bytes(0x501)
                  + b"\x67" + ptr_bytes(0xD00) + b"\x02")
        walker = maps.Walker(rom, self.prof)
        walker.sink = walker.ctx.side_uses
        walker.walk_script(0x100, "", "", "buffer", None, None)
        with patch("gen3tl.discover.context.Walker", return_value=walker), \
                patch.object(walker, "walk_all", return_value=walker.ctx), \
                patch.object(walker, "walk_structural", return_value=walker.ctx), \
                patch.object(walker, "walk_buffer_starts"):
            ctx = context.build_context(rom, self.prof, self.inv)
        info = ctx["strings"]["s0000D00"]["slots"]["{STR_VAR_1}"]
        self.assertEqual(info, {"fills": [["native", "choices"]], "uses": 1, "unfilled": 0})
        # the one-string table source, never the stale word
        self.assertEqual(ctx["strings"]["s0000D00"]["receives"], [["s0000A00", "{STR_VAR_1}"]])

    def test_concatenated_variants_follow_targets_and_draft_overrides(self):
        ctx = {"strings": {"s0000D00": {"slots": {"{STR_VAR_1}": {
            "fills": [["native", "choices"]], "uses": 1, "unfilled": 0}}}}}
        def widths(overrides=None):
            return qa.slot_widths(self.prof, self.inv, ctx, self.cat, self.measure, self.cm,
                                  overrides)["s0000D00"]["STR_VAR_1"]
        self.assertEqual((widths().translated, widths().source_width), (54, 54))
        self.cat["s0000900"][1].msgstr = "X"
        self.assertEqual((widths().translated, widths().source_width), (36, 54))
        self.assertEqual(widths({"s0000900": ""}).translated, 54)
        self.assertEqual(widths({"s0000B00": " ten chars"}).translated, 66)
        self.cat["s0000B00"][1].flags = ["empty"]
        self.assertEqual(widths().translated, 24)
        self.assertEqual(widths().source, {"code": "slot_native", "table": "choices", "ids": ["s0000A00"]})
        del self.cat["s0000900"]
        self.assertEqual(widths().translated, 60)
        self.assertEqual(widths().details["state"], "recorded")
        self.assertIn("missing from catalog", widths().details["unavailable"])

    def test_unrelated_context_does_not_require_source_entries(self):
        ctx = {"strings": {"absent": {"slots": {"{STR_VAR_1}": {
            "fills": [["native", "choices"]], "uses": 1, "unfilled": 0}}}}}
        catalog = {"s0000D00": self.cat["s0000D00"]}
        result = qa.slot_widths(self.prof, self.inv, ctx, catalog, self.measure, self.cm)
        self.assertEqual(result["s0000D00"]["STR_VAR_1"].translated, 60)

    def test_incomplete_or_ambiguous_source_tables_fail(self):
        self.assertEqual(maps.native_fill_sources(self.rule, self.inv),
                         [("s0000A00",), ("s0000900", "s0000B00")])
        for change in ("missing", "duplicate", "variant"):
            inv = copy.deepcopy(self.inv)
            if change == "missing": inv["entries"][1]["refs"] = []
            elif change == "duplicate": inv["entries"][0]["refs"].append({"at": 0x804})
            else: inv["entries"].pop(2)
            with self.subTest(change=change), self.assertRaises(ValueError):
                maps.native_fill_sources(self.rule, inv)

    def test_invalid_declarations_fail(self):
        for change in ({"address": 0x500}, {"slot": 3}, {"count": 0}, {"exclude": [2]},
                       {"variants": [["invalid"]]}, {"evidence": ""}, {"stride": 2}):
            prof = copy.deepcopy(self.prof)
            prof["context"]["native_fill"][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                maps.native_fill_rules(prof)

    def test_special_replaces_earlier_string_and_ends_the_flow(self):
        # special 0xEA style rule
        rule = dict(self.rule, special=0xEA)
        del rule["address"]
        prof = {"maps": {}, "context": {"native_fill": [rule]}}
        self.assertEqual(list(maps.native_fill_rules(prof)), [("special", 0xEA)])
        rom = Rom(bytes([255]) * 0x1000)
        rom.write(0x100, b"\x85\x00" + ptr_bytes(0xC00) + b"\x25\xEA\x00"
                  + b"\x67" + ptr_bytes(0xD00) + b"\x02")
        walker = maps.Walker(rom, prof)
        self.assertEqual(walker.flow(0x106, 0), (frozenset(), False))
        self.assertEqual(walker.flow(0x106, 1), (frozenset({0xD00}), False))
        walker.sink = walker.ctx.side_uses
        walker.walk_script(0x100, "", "", "buffer", None, None)
        with patch("gen3tl.discover.context.Walker", return_value=walker), \
                patch.object(walker, "walk_all", return_value=walker.ctx), \
                patch.object(walker, "walk_structural", return_value=walker.ctx), \
                patch.object(walker, "walk_buffer_starts"):
            ctx = context.build_context(rom, prof, self.inv)
        info = ctx["strings"]["s0000D00"]["slots"]["{STR_VAR_1}"]
        self.assertEqual(info, {"fills": [["native", "choices"]], "uses": 1, "unfilled": 0})

    def test_invalid_special_declarations_fail(self):
        # one key, a 16-bit id
        for change in ({"special": 0x10000}, {"special": -1}, {"special": "0xEA"}, {"special": 0xEA, "address": 0x501}):
            rule = dict(self.rule, **change)
            if "address" not in change:
                del rule["address"]
            with self.subTest(change=change), self.assertRaises(ValueError):
                maps.native_fill_rules({"context": {"native_fill": [rule]}})


if __name__ == "__main__":
    unittest.main()
