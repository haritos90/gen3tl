import unittest

from gen3tl.data import pokedexdata as pd
from gen3tl.data import pokedexlearnsets as pl

def learnset_block() -> dict:
    return {
        "level_up": {"base": 0x100, "count": 3, "evidence": "synthetic pointers"},
        "tmhm": {"base": 0x300, "stride": 16, "count": 3,
                 "move_base": 0x400, "move_count": 128, "evidence": "synthetic TM bits"},
        "egg": {"base": 0x500, "evidence": "synthetic egg markers"},
        "tutor": {"base": 0x600, "stride": 20, "count": 3,
                  "move_base": 0x700, "move_count": 146, "evidence": "synthetic tutor bits"},
    }

def rom_bytes() -> bytes:
    data = bytearray(0x900)
    for species, at in ((0, 0x200), (1, 0x210), (2, 0x220)):
        data[0x100 + species * 4:0x104 + species * 4] = (0x08000000 + at).to_bytes(4, "little")
        data[at:at + 3] = (33).to_bytes(2, "little") + bytes([1])
        data[at + 3:at + 6] = (45).to_bytes(2, "little") + bytes([3])
        data[at + 6:at + 9] = bytes([0, 0, 0xFF])
    for index, move in ((0, 264), (1, 337), (39, 332), (127, 392)):
        data[0x400 + index * 2:0x402 + index * 2] = move.to_bytes(2, "little")
    data[0x310:0x314] = (1).to_bytes(4, "little")
    data[0x314:0x318] = (1 << (39 % 32)).to_bytes(4, "little")
    data[0x31C:0x320] = (1 << (127 % 32)).to_bytes(4, "little")
    data[0x320:0x324] = (2).to_bytes(4, "little")
    data[0x500:0x502] = (20001).to_bytes(2, "little")
    data[0x502:0x504] = (130).to_bytes(2, "little")
    data[0x504:0x506] = (204).to_bytes(2, "little")
    data[0x506:0x508] = (20002).to_bytes(2, "little")
    data[0x508:0x50A] = (15).to_bytes(2, "little")
    data[0x50A:0x50C] = (0xFFFF).to_bytes(2, "little")
    for index, move in ((0, 75), (17, 270), (32, 337), (145, 731)):
        data[0x700 + index * 2:0x702 + index * 2] = move.to_bytes(2, "little")
    data[0x614:0x618] = (1 << 17).to_bytes(4, "little")
    data[0x618:0x61C] = (1).to_bytes(4, "little")
    data[0x624:0x628] = (1 << (145 - 128)).to_bytes(4, "little")
    data[0x628:0x62C] = (1).to_bytes(4, "little")
    return bytes(data)

class LearnsetDataTest(unittest.TestCase):
    def test_all_four_methods_decode(self):
        block = learnset_block()
        records = pl.build(block, rom_bytes(), 3)
        self.assertEqual([r["method"] for r in records[1]], [0, 0, 1, 1, 1, 2, 2, 3, 3, 3])
        self.assertEqual(records[1][0], {
            "species": 1, "move": 33, "method": 0, "index": None, "level": 1, "offset": "0x000210"})
        self.assertEqual([r["move"] for r in records[1][2:5]], [264, 332, 392])
        self.assertEqual([r["move"] for r in records[1][5:7]], [130, 204])
        self.assertEqual([r["index"] for r in records[1][7:]], [17, 32, 145])
        self.assertEqual([r["move"] for r in records[2]], [33, 45, 337, 15, 75])
        self.assertEqual(pl.build(block, rom_bytes(), 3), records)

    def test_missing_evidence_is_named(self):
        block = learnset_block()
        for table in block.values():
            del table["evidence"]
        self.assertEqual(pl.check(block),
                         ["level_up evidence", "tmhm evidence", "egg evidence", "tutor evidence"])

    def test_integrates_with_species_records_and_verification(self):
        block = {
            "name_table": "species",
            "species": {"base": 0, "stride": 1, "count": 3, "first": 1,
                        "evidence": "synthetic", "fields": {"hp": 0}},
            "learnsets": learnset_block(),
        }
        doc = pd.build(block, rom_bytes(), {"id": "demo"})
        self.assertEqual(len(doc["species"][0]["learnset"]), 10)
        self.assertEqual(len(doc["species"][1]["learnset"]), 5)
        self.assertEqual(doc["provenance"]["level_up"]["offset"], "0x000100")
        self.assertEqual(doc["provenance"]["tmhm"]["offset"], "0x000300")
        self.assertEqual(doc["provenance"]["egg"]["offset"], "0x000500")
        self.assertEqual(doc["provenance"]["tutor"]["offset"], "0x000600")
        self.assertEqual(pd.verify_doc(doc, block, "demo", doc["rom"]["sha1"]), [])
        broken = dict(doc)
        broken["species"] = [dict(record) for record in doc["species"]]
        del broken["species"][0]["learnset"]
        self.assertIn("learnset", pd.verify_doc(broken, block, "demo", doc["rom"]["sha1"])[0])

class FormResolutionTest(unittest.TestCase):
    def test_only_an_unambiguous_reversion_changes_the_species(self):
        block = {"form_reversions": {"methods": [253, 254], "evidence": "synthetic"}}
        mega = [{"method": 254, "parameter": 0, "target": 94}]
        gmax = [{"method": 253, "parameter": 0, "target": 94}]
        forward = [{"method": 254, "parameter": 454, "target": 877}]
        ambiguous = [{"method": 254, "parameter": 0, "target": 1079},
                     {"method": 254, "parameter": 0, "target": 1080}]
        self.assertEqual(pl.resolve_species(block, 877, mega), 94)
        self.assertEqual(pl.resolve_species(block, 1267, gmax), 94)
        self.assertEqual(pl.resolve_species(block, 94, forward), 94)
        self.assertEqual(pl.resolve_species(block, 1081, ambiguous), 1081)
        self.assertEqual(pl.resolve_species(block, 1020, []), 1020)
