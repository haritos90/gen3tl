import copy
import tempfile
import unittest
from pathlib import Path

from gen3tl.check import qa
from gen3tl.core.charmap import Charmap
from gen3tl.check.layout import Measure
from gen3tl.catalog.po import PoEntry, write

class ItemWindowTest(unittest.TestCase):
    def setUp(self):
        self.prof = {"font_style": {"style": "main"}, "table": [
            {"name": "items", "base": 0x100, "stride": 20, "count": 4, "field_length": 14,
             "windows": [
                 {"style": "main", "width": 92, "strict": True, "chars": 15,
                  "exclude_indices": [2], "where": "held summary"},
                 {"style": "main", "width": 104, "strict": True, "chars": 17,
                  "indices": [2], "where": "registered key item"},
                 {"style": "main", "width": 71, "strict": True, "chars": 12,
                  "indices": [3], "where": "berry pouch"}]}]}
        # entries carry their source text, as inventory.build writes them
        self.inv = {"entries": [
            {"id": "items:0", "offset": 0x100, "length": 14, "kind": "fixed", "table": "items",
             "index": 0, "category": "table:items", "refs": [], "flags": [], "text": "Strawberry Sweet"},
            *[{"id": f"s{i}", "offset": 0x200 + i * 32, "length": 20, "kind": "pointer",
               "category": "data-table", "refs": [{"at": 0x100 + i * 20, "kind": "table:items"}],
               "flags": [], "text": "Strawberry Sweet"} for i in range(1, 4)]]}
        self.cm = Charmap()
        self.measure = Measure(lambda _: 6, {})
        self.catalog = {e["id"]: (Path("items.po"), PoEntry(e["id"], "Strawberry Sweet", "Short"))
                        for e in self.inv["entries"]}

    def test_fixed_and_pointer_names_share_consumer_bounds(self):
        limits = qa.compute_limits(self.prof, self.inv, self.catalog, self.measure, self.cm)
        self.assertEqual({sid: (v.width, v.chars) for sid, v in limits.items()},
                         {"items:0": (92, 13), "s1": (92, 15), "s2": (104, 17), "s3": (71, 12)})
        self.assertEqual(limits["s1"].sources["chars"]["rule"],
                         {"code": "table_buffer", "table": "items", "where": "held summary"})

    def test_pointer_aliases_take_the_narrowest_consumer(self):
        self.inv["entries"][1]["refs"].append({"at": 0x128, "kind": "table:items"})
        limits = qa.compute_limits(self.prof, self.inv, self.catalog, self.measure, self.cm)
        self.assertEqual((limits["s1"].width, limits["s1"].chars), (92, 15))

    def test_explicit_narrower_window_still_binds(self):
        self.prof["layout"] = {"window_width": {"s1": 60}}
        limits = qa.compute_limits(self.prof, self.inv, self.catalog, self.measure, self.cm)
        self.assertEqual(limits["s1"].width, 60)

    def test_pointer_names_in_other_fonts_are_checked(self):
        self.prof["table"][0]["windows"].append({"style": "small", "width": 20, "where": "small item box"})
        windows = qa.Windows(self.prof, self.inv, {"small": (self.measure, self.cm)})
        self.assertEqual(windows.other("s1"), [{"font": "small", "width": 20, "where": "small item box"}])
        self.assertTrue(windows.problems("s1", "ABCDE"))

    def test_source_overflow_warns_but_translation_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write(root / "items.po", [PoEntry("s1", "Strawberry Sweet", "Strawberry Sweet")], {})
            source = qa.run(self.prof, self.inv, root, self.cm, self.measure, set(),
                            source_as_translation=True)
            translated = qa.run(self.prof, self.inv, root, self.cm, self.measure, set())
        self.assertEqual(source.errors, 0)
        self.assertTrue(any("field overflow" in f.message for f in source.findings))
        self.assertTrue(any(f.severity == "error" and "field overflow" in f.message
                            for f in translated.findings))

    def test_source_cannot_overflow_its_declared_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write(root / "items.po", [PoEntry("items:0", "Strawberry Sweet", "")], {})
            source = qa.run(self.prof, self.inv, root, self.cm, self.measure, set(),
                            source_as_translation=True)
        self.assertTrue(any(f.severity == "error" and "field overflow" in f.message
                            for f in source.findings))

    def test_invalid_record_reference_is_rejected(self):
        for at in (0x101, 0x150):
            inv = copy.deepcopy(self.inv)
            inv["entries"][1]["refs"][0]["at"] = at
            with self.subTest(at=at), self.assertRaisesRegex(ValueError, "invalid name-table"):
                qa.Windows(self.prof, inv)

    def test_invalid_selection_or_capacity_is_rejected(self):
        for key, value in (("indices", [4]), ("exclude_indices", [-1]), ("chars", 0), ("strict", "yes")):
            prof = copy.deepcopy(self.prof)
            prof["table"][0]["windows"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                qa.Windows(prof, self.inv)
