from test_roots import TOOLKIT_ROOT
"""Relocated native readers, literal writers and finite selector fixtures."""

import struct
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.core.rom import ROM_BASE, Rom
from gen3tl.core.thumb import ThumbError
from gen3tl.discover import table_core as core, table_readers as readers, table_selectors as selectors
from gen3tl.families.fingerprints import Fingerprint


def words(*values):
    return struct.pack("<" + "H" * len(values), *values)


def call(at, target):
    distance = (target - at - 4) & 0x7FFFFF
    return words(0xF000 | distance >> 12, 0xF800 | (distance >> 1 & 0x7FF))


def signature(rom, start, end, name):
    return Fingerprint.bootstrap(rom.data, start, end, name, "table-reader").document()


class ReaderRoleTest(unittest.TestCase):
    def fixture(self):
        rom = Rom(b"\xff" * 2048)
        for start, base in ((0x100, 0x400), (0x140, 0x500)):
            rom.write(start, words(0x4801, 0x4770))
            rom.put_u32(start + 8, ROM_BASE + base)
        rom.write(0x80, call(0x80, 0x100) + words(0x4770))
        knowledge = {"functions": [signature(rom, 0x100, 0x104, "reader"),
                                    signature(rom, 0x80, 0x86, "caller")]}
        return rom, knowledge

    def test_an_ambiguous_reader_needs_an_actual_call_link(self):
        rom, knowledge = self.fixture()
        descriptor = {"reader": "reader", "offset": 0}
        self.assertIsNone(readers.Readers(rom, knowledge).load(descriptor))
        knowledge["constraints"] = [{"reader": "reader", "caller": "caller", "offset": 0}]
        actual = readers.Readers(rom, knowledge).load(descriptor)
        self.assertEqual((actual["base"], actual["pool"], actual["site"]), (0x400, 0x108, 0x100))
        rom.write(0x80, call(0x80, 0x180))
        self.assertIsNone(readers.Readers(rom, knowledge).load(descriptor))

    def test_pool_and_function_relocation_preserve_the_derived_binding(self):
        rom, knowledge = self.fixture()
        knowledge["constraints"] = [{"reader": "reader", "caller": "caller", "offset": 0}]
        moved = Rom(b"\xff" * 2048)
        moved.write(0x200, words(0x4802, 0x4770))
        moved.put_u32(0x20C, ROM_BASE + 0x600)
        moved.write(0xA0, call(0xA0, 0x200) + words(0x4770))
        actual = readers.Readers(moved, knowledge).load({"reader": "reader", "offset": 0})
        self.assertEqual((actual["base"], actual["pool"], actual["site"]), (0x600, 0x20C, 0x200))


class LiteralWriterTest(unittest.TestCase):
    def test_immediate_records_follow_native_stores_and_instruction_spacing(self):
        for start in (0x100, 0x240):
            with self.subTest(start=start):
                rom = Rom(b"\xff" * 2048)
                rom.write(start, words(0x23DD, 0x7003, 0x23D9, 0x7043, 0x4770))
                knowledge = {"functions": [signature(rom, start, start + 10, "writer")], "patches": [
                    {"name": "suffix", "reader": "writer", "offsets": [0, 4], "method": "immediate"}]}
                result = core.Result()
                readers._patch_tables(rom, result, knowledge, readers.Readers(rom, knowledge))
                self.assertEqual(result.issues, [])
                self.assertEqual(result.facts[0].value, {"name": "suffix", "base": start, "stride": 4,
                    "count": 2, "field_offset": 0, "field_length": 1, "terminated": False})
                self.assertIn(start + 2, result.facts[0].evidence.code_sites)

    def test_character_immediates_without_the_matching_store_are_refused(self):
        rom = Rom(b"\xff" * 2048)
        rom.write(0x100, words(0x23DD, 0x7004, 0x4770))
        knowledge = {"functions": [signature(rom, 0x100, 0x106, "writer")], "patches": [
            {"name": "suffix", "reader": "writer", "offsets": [0], "method": "immediate"}]}
        result = core.Result()
        readers._patch_tables(rom, result, knowledge, readers.Readers(rom, knowledge))
        self.assertEqual(result.facts, [])
        self.assertEqual(result.issues[0].status, "unresolved")


class SentinelReaderTest(unittest.TestCase):
    def fixture(self, start=0x100, base=0x400):
        rom = Rom(b"\xff" * 2048)
        rom.write(start, words(0x4907, 0x4B08, 0x880A, 0x429A, 0xD001, 0x3102, 0xE7FA, 0x4770))
        rom.put_u32(start + 32, ROM_BASE + base)
        rom.put_u32(start + 36, 0xFEFE)
        rom.write(base, words(1, 4, 7, 0xFEFE))
        knowledge = {"functions": [signature(rom, start, start + 16, "scanner")], "move_lists": [
            {"key": "demo", "reader": "scanner", "offset": 0}]}
        return rom, knowledge

    def run_scan(self, rom, knowledge):
        result = core.Result()
        readers._move_lists(rom, result, {"move_lists": {"demo": [1, 4, 7]}}, knowledge,
                            readers.Readers(rom, knowledge), {"moves": SimpleNamespace(count=10)})
        return result

    def test_the_terminator_comes_from_the_native_comparison(self):
        for start, base in ((0x100, 0x400), (0x200, 0x500)):
            rom, knowledge = self.fixture(start, base)
            result = self.run_scan(rom, knowledge)
            self.assertEqual(result.issues, [])
            self.assertEqual(result.facts[0].value, {"key": "demo", "base": base, "terminator": 0xFEFE})
            self.assertIn(start + 6, result.facts[0].evidence.code_sites)
            rom.write(base + 6, words(0xFFFF))
            self.assertEqual(self.run_scan(rom, knowledge).facts, [])

    def test_two_valid_arrays_with_the_same_anchors_remain_ambiguous(self):
        rom, knowledge = self.fixture()
        rom.write(0x200, rom.read(0x100, 16))
        rom.put_u32(0x220, ROM_BASE + 0x500)
        rom.put_u32(0x224, 0xFEFE)
        rom.write(0x500, words(1, 4, 7, 0xFEFE))
        result = self.run_scan(rom, knowledge)
        self.assertEqual(result.facts, [])
        self.assertEqual(dict(result.issues[0].evidence.counts)["candidates"], 2)


class SelectorTest(unittest.TestCase):
    def fixture(self, start=0):
        rom = Rom(bytes(1024))
        rom.write(start, words(0xB500, 0x281D, 0xD801, 0x2000, 0xE000, 0x2001, 0xBC02, 0x4708))
        return rom

    def test_all_residue_inputs_determine_rates_after_relocation(self):
        for start in (0, 0x100):
            rom = self.fixture(start)
            first, rates, sites = selectors.distribution(rom.data, start, start + 16, start + 2, 0)
            self.assertEqual((first, rates), (0, [30, 70]))
            self.assertIn(start + 10, sites)
            self.assertEqual(selectors.evaluate(rom.data, start, start + 16, start + 2, 0, 29)[0], 0)
            self.assertEqual(selectors.evaluate(rom.data, start, start + 16, start + 2, 0, 30)[0], 1)

    def test_unsupported_memory_flags_and_nonconsecutive_slots_do_not_produce_rates(self):
        rom = self.fixture()
        rom.write(6, words(0x7800))
        with self.assertRaisesRegex(ThumbError, "unsupported selector operation"):
            selectors.distribution(rom.data, 0, 16, 2, 0)
        rom = self.fixture()
        rom.write(10, words(0x2002))
        with self.assertRaisesRegex(ThumbError, "not consecutive"):
            selectors.distribution(rom.data, 0, 16, 2, 0)
        rom = Rom(words(0xB500, 0x2800, 0x2101, 0xD800, 0x2000, 0xBC02, 0x4708))
        with self.assertRaisesRegex(ThumbError, "unknown flags"):
            selectors.evaluate(rom.data, 0, len(rom), 2, 0, 1)

    def test_instruction_limits_stop_a_native_loop(self):
        rom = Rom(words(0xB500, 0x2800, 0xE7FE))
        with self.assertRaisesRegex(ThumbError, "instruction limit"):
            selectors.evaluate(rom.data, 0, len(rom), 2, 0, 1, limit=12)


class ShapeEvidenceTest(unittest.TestCase):
    def test_signed_extension_requires_the_loaded_byte_to_survive(self):
        rom = Rom(words(0x79C4, 0x0624, 0x1620, 0x4770))
        self.assertEqual(core.sign_extension(rom, 0, len(rom)), (0, 2, 4))
        self.assertEqual(core.sign_extension(rom, 0, 4), ())
        rom = Rom(words(0x79C4, 0x2400, 0x0624, 0x1620, 0x4770))
        self.assertEqual(core.sign_extension(rom, 0, len(rom)), ())

    def test_sprite_palette_aliases_preserve_the_complete_slot_domain(self):
        rom = Rom(b"\xff" * 2048)
        for index in range(40):
            rom.write(0x200 + index * 8, struct.pack("<IHH", ROM_BASE + 0x100, index, 0))
        rom.write(0x200 + 35 * 8 + 4, words(1))
        self.assertEqual(core.sprite_layout(rom, 0x200, 40, True),
                         {"base": 0x200, "stride": 8, "count": 40, "tag": 4})
        rom.put_u32(0x200 + 39 * 8, ROM_BASE + len(rom))
        self.assertIsNone(core.sprite_layout(rom, 0x200, 40, True))


if __name__ == "__main__":
    unittest.main()
