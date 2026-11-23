import copy
import hashlib
import unittest
from pathlib import Path

from gen3tl.check import qa
from gen3tl.check.layout import Measure
from gen3tl.catalog.po import PoEntry
from gen3tl.core.rom import Rom
from qa_support import PACK, scripted


class SourceFillTest(unittest.TestCase):
    def setUp(self):
        self.cm = PACK.charmap()
        raw = self.cm.encode("AA")
        self.rom = Rom(bytes(0x2000))
        self.rom.write(0x400, raw)
        self.rom.write(0x1000, bytes([5]) * 256)
        self.slot = {"strings": ["s0000200", "s0000300"], "font": "main",
                     "originals": [{"id": "s0000400", "length": len(raw),
                                    "sha256": hashlib.sha256(raw).hexdigest()}]}
        self.prof = {"font_style": {"style": "main"}, "font": [
            {"name": "main", "glyphs": 0, "widths": 0x1000, "count": 256}],
            "layout": {"window_width": {"s0000100": 60}, "code_fill": [
                {"ids": ["s0000100"], "slots": {"STR_VAR_1": self.slot}, "evidence": "verified table"}]}}
        self.inv = {"entries": [scripted("s0000100", "{STR_VAR_1}!"),
                                scripted("s0000200", "A"), scripted("s0000300", "AAAAA")]}
        self.catalog = {row["id"]: (Path("test.po"), PoEntry(row["id"], row["text"], "Б"))
                        for row in self.inv["entries"]}
        self.m = Measure(lambda _: 6, {"STR_VAR_1": 60}, styles={"main": lambda _: 6})

    def limits(self, **kwargs):
        return qa.compute_limits(self.prof, self.inv, self.catalog, self.m, self.cm, rom=self.rom, **kwargs)

    def test_catalog_and_original_widths_have_separate_maxima(self):
        selected = self.limits()["s0000100"]
        english = self.limits(english_slots=True)["s0000100"]
        self.assertEqual(selected.placeholders, {"STR_VAR_1": 12})
        self.assertEqual(english.placeholders, {"STR_VAR_1": 30})
        self.assertEqual(selected.slot_sources["STR_VAR_1"],
                         {"code": "slot_code_fill", "fill": {"code": "slot_widest_source", "id": "s0000400"}})
        self.assertIsNone(self.limits()["s0000200"].placeholders)

    def test_overlapping_rules_keep_both_language_maxima(self):
        self.prof["layout"]["code_fill"].append({"ids": ["s0000100"], "evidence": "another caller",
            "slots": {"STR_VAR_1": {"width": 40, "source_width": 5}}})
        self.assertEqual(self.limits()["s0000100"].placeholders, {"STR_VAR_1": 40})
        self.assertEqual(self.limits(english_slots=True)["s0000100"].placeholders, {"STR_VAR_1": 30})
        self.prof["layout"]["code_fill"].reverse()
        self.assertEqual(self.limits(english_slots=True)["s0000100"].placeholders, {"STR_VAR_1": 30})

    def test_draft_empty_and_untranslated_sources_are_live(self):
        self.assertEqual(self.limits(overrides={"s0000200": "Б" * 8})["s0000100"].placeholders,
                         {"STR_VAR_1": 48})
        self.catalog["s0000300"][1].msgstr = ""
        self.assertEqual(self.limits()["s0000100"].placeholders, {"STR_VAR_1": 30})
        self.catalog["s0000300"][1].flags.append("empty")
        self.assertEqual(self.limits()["s0000100"].placeholders, {"STR_VAR_1": 12})

    def test_an_unencodable_source_draft_refuses_measurement(self):
        with self.assertRaisesRegex(ValueError, "unmeasurable draft"):
            self.limits(overrides={"s0000300": "Б @"})

    def test_explicit_font_measures_every_candidate(self):
        self.m.styles["main"] = lambda _: 3
        self.assertEqual(self.limits()["s0000100"].placeholders, {"STR_VAR_1": 6})
        self.assertEqual(self.limits(english_slots=True)["s0000100"].placeholders, {"STR_VAR_1": 15})
        self.slot["font"] = "missing"
        with self.assertRaisesRegex(ValueError, "unknown font"):
            self.limits()

    def test_fixed_table_sources_follow_translations_and_drafts(self):
        for old, new in (("s0000200", "labels:0"), ("s0000300", "labels:1")):
            path, entry = self.catalog.pop(old)
            entry.msgctxt = new
            self.catalog[new] = (path, entry)
        self.slot["strings"] = ["labels:0", "labels:1"]
        self.catalog["labels:1"][1].msgstr = "Б" * 7
        self.assertEqual(self.limits()["s0000100"].placeholders, {"STR_VAR_1": 42})
        self.assertEqual(self.limits(english_slots=True)["s0000100"].placeholders,
                         {"STR_VAR_1": 30})
        self.assertEqual(self.limits(overrides={"labels:0": "Б" * 9})["s0000100"].placeholders,
                         {"STR_VAR_1": 54})
        del self.catalog["labels:1"]
        with self.assertRaisesRegex(ValueError, "missing from catalog"):
            self.limits()

    def test_missing_catalog_and_new_catalog_original_fail_closed(self):
        del self.catalog["s0000200"]
        with self.assertRaisesRegex(ValueError, "missing from catalog"):
            self.limits()
        self.catalog["s0000200"] = (Path("test.po"), PoEntry("s0000200", "A", ""))
        self.catalog["s0000400"] = (Path("test.po"), PoEntry("s0000400", "AA", ""))
        with self.assertRaisesRegex(ValueError, "now has a catalog entry"):
            self.limits()

    def test_original_bytes_and_terminator_are_verified(self):
        self.rom.data[0x400] ^= 1
        with self.assertRaisesRegex(ValueError, "bytes differ"):
            self.limits()
        for raw in (b"\xbb\xff\xff", b"\xbb\xfc\xff", b"\xbb\xbb\xbb"):
            self.rom.write(0x400, raw)
            self.slot["originals"][0]["sha256"] = hashlib.sha256(raw).hexdigest()
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, "bytes differ"):
                self.limits()

    def test_typed_fact_sets_and_histograms_are_strict(self):
        good = qa.fill_originals(self.prof, self.catalog, self.rom)
        self.assertEqual(good["s0000400"]["width"], 10)
        self.assertEqual(good["s0000400"]["glyph_counts"], [[187, 2]])
        for patch in ({"glyph_counts": [[187, 1]]}, {"glyph_counts": [[187, 1], [187, 1]]},
                      {"glyph_counts": [[True, 2]]}, {"font": "other"}, {"width": True}, {"length": 4}):
            bad = copy.deepcopy(good)
            bad["s0000400"].update(patch)
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                qa.fill_originals(self.prof, self.catalog, facts=bad)
        for bad in ({}, {**good, "s0000500": good["s0000400"]}):
            with self.assertRaises(ValueError):
                qa.fill_originals(self.prof, self.catalog, facts=bad)
        with self.assertRaisesRegex(ValueError, "verified ROM facts"):
            qa.fill_originals(self.prof, self.catalog)

    def test_unrelated_catalog_subset_does_not_require_missing_sources(self):
        self.catalog = {"s0000300": self.catalog["s0000300"]}
        self.assertEqual(set(self.limits()), {"s0000300"})
        self.assertEqual(qa.fill_originals(self.prof, self.catalog), {})

    def test_bad_source_rules_are_rejected(self):
        for patch in ({"strings": []}, {"strings": ["s0000200", "s0000200"]},
                      {"strings": [{}]}, {"strings": [None]}, {"strings": ["bad"]},
                      {"strings": ["labels:-1"]}, {"strings": ["labels:1/2"]},
                      {"strings": ["labels:1", "labels:1"]},
                      {"width": 5}, {"digits": 1}, {"source_width": 20}, {"extra": 0}, {"font": None}):
            rule = copy.deepcopy(self.prof["layout"]["code_fill"][0])
            rule["slots"]["STR_VAR_1"].update(patch)
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                qa.code_fills({"code_fill": [rule]})


if __name__ == "__main__":
    unittest.main()
