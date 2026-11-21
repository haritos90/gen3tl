import struct
import unittest

from gen3tl.core.rom import Rom
from gen3tl.discover import table_core, table_readers
from gen3tl.families.fingerprints import Fingerprint

def words(*values):
    return struct.pack("<" + "H" * len(values), *values)

def call(at, target):
    offset = (target - at - 4) & 0x7FFFFF
    return words(0xF000 | offset >> 12, 0xF800 | (offset >> 1 & 0x7FF))

class KeyboardLayoutTest(unittest.TestCase):
    def fixture(self):
        rom = Rom(b"\xff" * 2048)
        rom.write(0x100, words(0x2100) + call(0x102, 0x300) + words(0x2101) + call(0x108, 0x300)
                  + words(0x4770))
        for start, instruction in ((0x180, 0x2103), (0x1C0, 0x2203)):
            rom.write(start, words(instruction) + call(start + 2, 0x310) + words(0x4770))
        rom.write(0x300, words(0x2000, 0x4770))
        functions = [Fingerprint.bootstrap(rom.data, start, end, name, "table-reader").document()
                     for start, end, name in ((0x100, 0x10E, "keyboard-opening"),
                                              (0x180, 0x188, "keyboard-page-count"),
                                              (0x1C0, 0x1C8, "keyboard-label-count"),
                                              (0x300, 0x304, "keyboard-print"))]
        knowledge = {"functions": functions, "keyboard_layout": {
            "reserved_bytes": 64,
            "immediates": {
                "opening_keyboard_offsets": {"callee": "keyboard-print", "sites": [
                    {"reader": "keyboard-opening", "offset": 6},
                    {"reader": "keyboard-opening", "offset": 0},
                ]},
                "page_count": {"sites": [
                    {"reader": "keyboard-page-count", "offset": 0},
                    {"reader": "keyboard-label-count", "offset": 0},
                ]},
            },
        }}
        return rom, knowledge

    def discover(self, rom, knowledge):
        result = table_core.Result()
        table_readers._keyboard_layout(rom, result, knowledge, table_readers.Readers(rom, knowledge))
        return result

    def test_native_immediates_and_erased_tail_are_discovered(self):
        rom, knowledge = self.fixture()
        result = self.discover(rom, knowledge)
        self.assertEqual(result.issues, [])
        self.assertEqual(result.data["keyboard"], {
            "reserved": [1984, 2048],
            "opening_keyboard_offsets": [0x106, 0x100],
            "page_count": [0x180, 0x1C0],
        })
        self.assertEqual([fact.id for fact in result.facts], ["keyboard"])
        self.assertTrue(result.facts[0].evidence.anchors)
        self.assertTrue(result.facts[0].evidence.method)

    def test_inconsistent_counts_and_wrong_opening_call_are_refused(self):
        rom, knowledge = self.fixture()
        rom.write(0x1C0, words(0x2204))
        result = self.discover(rom, knowledge)
        self.assertNotIn("page_count", result.data.get("keyboard", {}))
        rom, knowledge = self.fixture()
        rom.write(0x102, call(0x102, 0x314))
        result = self.discover(rom, knowledge)
        self.assertNotIn("opening_keyboard_offsets", result.data.get("keyboard", {}))

    def test_partially_occupied_reserved_tail_is_refused(self):
        rom, knowledge = self.fixture()
        rom.write(2000, b"\x00")
        result = self.discover(rom, knowledge)
        self.assertNotIn("reserved", result.data.get("keyboard", {}))
        self.assertIn("keyboard.reserved", [fact.id for fact in result.issues])
