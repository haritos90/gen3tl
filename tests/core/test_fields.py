import copy
import unittest
from pathlib import Path

from gen3tl.core.charmap import Charmap
from gen3tl.core.fields import long_name_fields
from gen3tl.build.inject import inject, verify
from gen3tl.core.rom import Rom

TABLE = {"name": "names", "base": 0x100, "stride": 20, "count": 2, "field_length": 14,
         "long_name_pointers": True,
         "long_name_fields": [{"index": 0, "length": 18, "evidence": "Consumers accept seventeen characters."}]}

CM = Charmap()

class PointerFieldTest(unittest.TestCase):
    def image(self):
        data = bytearray(b"\0" * 0x1000 + b"\xff" * 0x1000)
        data[0x100:0x10E] = CM.encode("Short").ljust(14, b"\0")
        data[0x114:0x122] = CM.encode("Other").ljust(14, b"\0")
        entries = [{"id": f"names:{i}", "offset": 0x100 + i * 20, "length": 14,
                    "kind": "fixed", "table": "names", "index": i, "flags": [], "refs": []}
                   for i in range(2)]
        return Rom(data), {"entries": entries}

    def test_opt_in_conversion_preserves_surrounding_record(self):
        base, inv = self.image()
        rom = base.copy()
        text = CM.encode("Seventeen letters")
        result = inject(rom, base, inv, {"names:0": text}, {"names": TABLE})
        placed, = result.placements
        self.assertEqual((placed.id, placed.mode, placed.pointers), ("names:0", "field-pointer", [0x100]))
        self.assertEqual(rom.ptr_at(0x100), placed.new)
        self.assertEqual(bytes(rom.data[placed.new:placed.new + len(text)]), text)
        self.assertEqual(rom.data[0x104:0x128], base.data[0x104:0x128])
        self.assertEqual(verify(rom, base, result, {"names:0": text}, set()), [])

    def test_unapproved_or_oversized_fields_stay_unchanged(self):
        for sid, text in [("names:1", "Seventeen letters"), ("names:0", "Eighteen letters!!")]:
            with self.subTest(sid=sid):
                base, inv = self.image()
                rom = base.copy()
                result = inject(rom, base, inv, {sid: CM.encode(text)}, {"names": TABLE})
                self.assertEqual(len(result.skipped), 1)
                self.assertEqual(rom.data, base.data)

    def test_short_translation_stays_inline(self):
        base, inv = self.image()
        rom = base.copy()
        result = inject(rom, base, inv, {"names:0": CM.encode("Small")}, {"names": TABLE})
        self.assertEqual(result.placements[0].mode, "field")
        self.assertIsNone(rom.ptr_at(0x100))
        self.assertEqual(rom.data[0x114:0x128], base.data[0x114:0x128])

    def test_inventory_must_match_declared_record(self):
        base, inv = self.image()
        inv["entries"][0]["offset"] += 1
        rom = base.copy()
        result = inject(rom, base, inv, {"names:0": CM.encode("Seventeen letters")}, {"names": TABLE})
        self.assertIn("invalid inline field", result.failures[0])
        self.assertEqual(rom.data, base.data)

    def test_catalog_describes_both_storage_limits(self):
        from gen3tl.catalog.catalog import build_entries
        from gen3tl.discover.inventory import build
        from gen3tl.profile.profile import Profile
        base, _ = self.image()
        profile = Profile({"table": [TABLE]}, Path("test.toml"))
        inventory = build(profile, base)
        entries = {entry["id"]: entry for entry in inventory["entries"]}
        self.assertEqual(entries["names:0"]["length"], 14)
        self.assertEqual(entries["names:0"]["mode_evidence"]["pointer_length"], 18)
        catalog = build_entries(inventory, {}, CM, lambda _: 6, "test")["tables/names"]
        self.assertIn("inline field: 13 characters; long-name pointer: 17 characters max (table names, index 0)",
                      catalog[0].extracted)
        self.assertIn("fixed field: 13 characters max (table names, index 1)", catalog[1].extracted)

    def test_declarations_require_evidenced_pointer_support(self):
        bad = []
        for key, value in [("long_name_pointers", False), ("terminated", False), ("field_length", 3)]:
            table = copy.deepcopy(TABLE)
            table[key] = value
            bad.append(table)
        for key, value in [("index", 2), ("index", True), ("length", 14), ("evidence", "")]:
            table = copy.deepcopy(TABLE)
            table["long_name_fields"][0][key] = value
            bad.append(table)
        duplicate = copy.deepcopy(TABLE)
        duplicate["long_name_fields"] *= 2
        bad.append(duplicate)
        for table in bad:
            with self.subTest(table=table), self.assertRaises(ValueError):
                long_name_fields(table)

    def test_qa_uses_only_declared_consumer_capacity(self):
        from gen3tl.discover.inventory import build
        from gen3tl.check.layout import Measure
        from gen3tl.catalog.po import PoEntry
        from gen3tl.profile.profile import Profile
        from gen3tl.check.qa import compute_limits
        base, _ = self.image()
        profile = Profile({"table": [TABLE]}, Path("test.toml"))
        inventory = build(profile, base)
        path = Path("tables/names.po")
        catalog = {e["id"]: (path, PoEntry(e["id"], e["text"], "")) for e in inventory["entries"]}
        limits = compute_limits(profile, inventory, catalog, Measure(lambda _: 6, {}), CM)
        self.assertEqual(limits["names:0"].chars, 17)
        self.assertEqual(limits["names:1"].chars, 13)
        self.assertEqual(limits["names:0"].sources["chars"]["rule"], {"code": "table_long_name", "table": "names", "bytes": 18})
