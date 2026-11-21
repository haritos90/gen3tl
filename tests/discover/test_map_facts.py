import struct
import unittest

from gen3tl.core.rom import ROM_BASE, Rom, ptr_bytes
from gen3tl.discover.map_facts import discover, reader_tables, shared_table
from gen3tl.discover.scan import pointer_index
from gen3tl.families.fingerprints import Fingerprint

def image():
    data = bytearray(b"\xff" * 0x1000)
    code = struct.pack("<8H", 0x4B03, 0x0080, 0x58C2, 0x4B03,
                       0x0089, 0x4053, 0x58C8, 0x4770)
    data[0x100:0x110] = code
    data[0x110:0x118] = ptr_bytes(0x380) + struct.pack("<I", 0x1234)
    for number, bank in enumerate((0x300, 0x304)):
        data[0x380 + number * 4:0x384 + number * 4] = struct.pack("<I", (ROM_BASE + bank) ^ 0x1234)
        data[0x3A0 + number * 4:0x3A4 + number * 4] = ptr_bytes(bank)
        data[bank:bank + 4] = ptr_bytes(0x400 + number * 28)
    for header in (0x400, 0x41C):
        data[header:header + 28] = ptr_bytes(0x800) + ptr_bytes(0x700) + bytes(20)
    data[0x700:0x714] = bytes([1, 0, 0, 0]) + ptr_bytes(0x740) + bytes(12)
    data[0x740:0x758] = bytes(16) + ptr_bytes(0x900) + bytes(4)
    data[0x800:0x818] = struct.pack("<II", 10, 10) + ptr_bytes(0xA00) * 4
    data[0x900] = 2
    signature = Fingerprint.bootstrap(data, 0x100, 0x110, "header", "maps")
    return Rom(data), {"readers": [signature.document()]}

class MapsTest(unittest.TestCase):
    def test_literal_mask_banks_and_event_roots(self):
        rom, knowledge = image()
        result = discover(rom, knowledge, "firered-cfru")
        self.assertEqual(result.data["maps"], {"groups": 0x3A0,
            "engine": {"groups": 0x380, "count": 2, "bank_xor": 0x1234}})
        self.assertFalse(result.issues)
        self.assertEqual(result.metadata["counts"], {"headers": 2, "roots": 2, "unique_roots": 1})
        self.assertEqual([row["script"] for row in result.metadata["roots"]], [0x900, 0x900])
        self.assertTrue(all(row.evidence.code_sites for row in result.facts))

    def test_changed_reader_and_invalid_bank_are_withheld(self):
        rom, knowledge = image()
        rom.data[0x10A:0x10C] = struct.pack("<H", 0x405B)
        self.assertFalse(reader_tables(rom, knowledge))
        rom, knowledge = image()
        rom.data[0x380:0x384] = bytes(4)
        result = discover(rom, knowledge, "firered-cfru")
        self.assertFalse(result.data)
        self.assertEqual(result.issues[0].status, "unresolved")

    def test_equally_supported_plain_tables_are_ambiguous(self):
        rom, _ = image()
        rom.data[0x3C0:0x3C8] = rom.data[0x3A0:0x3A8]
        self.assertIsNone(shared_table(rom, [0x300, 0x304], {0x400, 0x41C}, pointer_index(rom)))
