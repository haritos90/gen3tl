import struct
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from gen3tl.core.rom import ROM_BASE, Rom
from gen3tl.core.thumb import ThumbError
from gen3tl.discover import table_core, table_readers, table_sources
from gen3tl.families.fingerprints import Fingerprint

class TradeReaderReachabilityTest(unittest.TestCase):
    def fixture(self, start=0x100, base=0x400):
        rom = Rom(b"\xff" * 2048)
        rom.write(start, struct.pack("<HH", 0xB500, 0xBD00))
        rom.write(start + 8, struct.pack("<8H", 0xB500, 0x2100, 0x2200, 0x2300,
                                          0x480B, 0x8F01, 0x8982, 0xBD00))
        rom.put_u32(start + 64, ROM_BASE + base)
        for index in range(3):
            rom.write(base + index * 60 + 12, struct.pack("<H", index + 1))
            rom.write(base + index * 60 + 56, struct.pack("<H", index + 4))
        row = Fingerprint.bootstrap(rom.data, start, start + 24, "trade", "table-reader").document()
        row["analysis"] = {"start": 8, "end": 24}
        knowledge = {"functions": [row], "trades": {"reader": "trade", "offset": 16,
                                                     "wanted": 18, "species": 20,
            "unreachable_reason": "The reader entry redirects before the retained table loads."}}
        return rom, knowledge

    def discover(self, rom, knowledge):
        readers = table_readers.Readers(rom, knowledge)
        self.assertEqual(len(readers.matches["trade"]), 1)
        result = table_core.Result()
        table_sources._trades(rom, result, knowledge, readers, {"species": SimpleNamespace(count=10)})
        return result

    def test_reachable_trade_loads_follow_relocated_entry_and_table(self):
        for start, base in ((0x100, 0x400), (0x240, 0x600)):
            with self.subTest(start=start, base=base):
                rom, knowledge = self.fixture(start, base)
                result = self.discover(rom, knowledge)
                self.assertEqual(result.issues, [])
                self.assertEqual(result.facts[0].value, {"base": base, "stride": 60, "count": 3,
                                                       "species": 12, "wanted": 56})
                self.assertIn(start + 8, result.facts[0].evidence.code_sites)

    def test_hooked_trade_entry_does_not_promote_retained_loads(self):
        for start, base in ((0x100, 0x400), (0x240, 0x600)):
            with self.subTest(start=start, base=base):
                rom, knowledge = self.fixture(start, base)
                rom.write(start + 8, struct.pack("<HHI", 0x4B00, 0x4718, ROM_BASE + 0x381))
                rom.write(0x380, struct.pack("<H", 0x4770))
                result = self.discover(rom, knowledge)
                self.assertEqual(result.facts, [])
                self.assertEqual(result.data, {})
                self.assertEqual(result.issues[0].id, "pokedex.sources.trades")
                self.assertEqual(result.issues[0].status, "omitted")
                self.assertEqual(result.issues[0].evidence.method, "semantic-reader+entry-reachability")
                self.assertEqual(result.issues[0].evidence.anchors, (start + 8,))
                self.assertIn(knowledge["trades"]["unreachable_reason"], result.issues[0].evidence.sentence)

    def test_unreachable_loads_without_a_declared_reason_remain_unresolved(self):
        for reason in (None, "", " ", 1):
            with self.subTest(reason=reason):
                rom, knowledge = self.fixture()
                knowledge["trades"]["unreachable_reason"] = reason
                rom.write(0x108, struct.pack("<HHI", 0x4B00, 0x4718, ROM_BASE + 0x381))
                result = self.discover(rom, knowledge)
                self.assertEqual(result.facts, [])
                self.assertEqual(result.issues[0].status, "unresolved")

    def test_failed_walk_remains_unresolved_despite_a_declared_reason(self):
        rom, knowledge = self.fixture()
        with patch.object(table_sources, "walk", side_effect=ThumbError("invalid instruction")):
            result = self.discover(rom, knowledge)
        self.assertEqual(result.facts, [])
        self.assertEqual(result.issues[0].status, "unresolved")

    def test_missing_reader_remains_unresolved_despite_a_declared_reason(self):
        rom, knowledge = self.fixture()
        readers = table_readers.Readers(rom, knowledge)
        readers.matches["trade"] = ()
        result = table_core.Result()
        table_sources._trades(rom, result, knowledge, readers, {"species": SimpleNamespace(count=10)})
        self.assertEqual(result.facts, [])
        self.assertEqual(result.issues[0].status, "unresolved")

    def test_a_branch_bypassing_one_field_load_refuses_the_table(self):
        rom, knowledge = self.fixture()
        rom.write(0x112, struct.pack("<H", 0xE000))
        result = self.discover(rom, knowledge)
        self.assertEqual(result.facts, [])
        self.assertEqual(result.issues[0].status, "unresolved")
        self.assertIn("not reachable", result.issues[0].evidence.sentence)
