"""Synthetic fixtures for the continuation table units."""

import struct
import unittest

from gen3tl.core.rom import ROM_BASE, Rom
from gen3tl.discover import table_sources, table_trainers


def words(*values):
    return struct.pack("<" + "H" * len(values), *values)


class PartyLayoutTest(unittest.TestCase):
    def fixture(self):
        rom = Rom(b"\xff" * 0x2000)
        base = 0x400
        # Trainer 0: flags 0, size 2, party at 0x800
        rom.write(base + 0, bytes([0, 0, 0, 0]))
        rom.write(base + 32, bytes([1]))
        rom.put_u32(base + 36, ROM_BASE + 0x800)
        rom.write(0x800, struct.pack("<HHHH", 0, 10, 19, 0))
        # Trainer 1: flags 3, size 1, party at 0x900
        rom.write(base + 40, bytes([3, 0, 0, 0]))
        rom.write(base + 40 + 32, bytes([1]))
        rom.put_u32(base + 40 + 36, ROM_BASE + 0x900)
        rom.write(0x900, struct.pack("<HHHHHHHH", 0, 19, 844, 601, 800, 800, 800, 800))
        return rom, base

    def test_flag_dependent_layout_is_unique(self):
        rom, base = self.fixture()
        domains = {"species": 1294, "moves": 923, "items": 729}
        layout = table_trainers._party_layout(rom, base, 40, 2, domains)
        self.assertEqual(layout, {"iv": 0, "level": 2, "species": 4, "held_item": 6,
                                  "moves": 6, "moves_with_item": 8, "move_count": 4,
                                  "custom_moves_flag": 1, "held_item_flag": 2,
                                  "default_stride": 8, "custom_stride": 16})

    def test_ambiguous_level_domain_is_refused(self):
        rom, base = self.fixture()
        # the level field also looks like a species id
        rom.write(0x800 + 2, struct.pack("<H", 800))
        rom.write(0x800 + 10, struct.pack("<H", 900))
        domains = {"species": 1294, "moves": 923, "items": 729}
        self.assertIsNone(table_trainers._party_layout(rom, base, 40, 2, domains))


class StreamWidthTest(unittest.TestCase):
    def test_helper_width_counts_stream_byte_loads(self):
        rom = Rom(b"\xff" * 0x200)
        rom.write(0x100, words(0x6802, 0x7810, 0x1c52, 0x7811, 0x4770))   # two ldrb
        rom.write(0x120, words(0xB510, 0x6806, 0x7830, 0x1c76, 0x7831,
                               0x1c76, 0x7832, 0x1c76, 0x7833, 0x4770))    # four ldrb
        self.assertEqual(table_sources._helper_width(rom, 0x100), 2)
        self.assertEqual(table_sources._helper_width(rom, 0x120), 4)
        self.assertIsNone(table_sources._helper_width(rom, 0x180))


if __name__ == "__main__":
    unittest.main()
