import struct
import unittest

from project_fixture import rom_evidence
from gen3tl.data import pokedexdata as pd
from gen3tl.data import pokedexsources as ps

GIFT, STARTER, SPECIAL, TRAINER = 0x100, 0x10F, 0x11E, 0x121

EGG, LITERAL_EGG, LINKED, OUTSIDE = 0x400, 0x410, 0x610, 0x800

TRADES = 0xA00

def command(data: bytearray, at: int, species: int, level: int = 1, item: int = 0, egg: bool = False):
    if egg:
        struct.pack_into("<BH", data, at, 0x7A, species)
        data[at + 3] = 0x02
        return
    struct.pack_into("<BHBHIIIB", data, at, 0x79, species, level, item, 0, 0, 0, 0)

def rom_bytes() -> bytes:
    data = bytearray(0x2000)
    command(data, GIFT, 1, level=5, item=2)
    command(data, STARTER, 0x4001, level=15, item=0x8004)
    struct.pack_into("<BH", data, SPECIAL, 0x25, 0xFE)
    struct.pack_into("<BBHH", data, TRAINER, 0x5C, 3, 0x017A, 0)
    struct.pack_into("<I", data, TRAINER + 6, 0x08001700)
    data[TRAINER + 10] = 0x02
    data[TRAINER + 11:EGG] = b"\xff" * (EGG - (TRAINER + 11))

    command(data, EGG, 0x800D, egg=True)
    data[EGG + 4:0x40F] = b"\xff" * (0x40F - (EGG + 4))
    command(data, LITERAL_EGG, 5, egg=True)
    command(data, 0x420, 6, level=8)
    struct.pack_into("<I", data, 0x426, 2)

    data[0x600] = 0x04
    struct.pack_into("<I", data, 0x601, 0x08000000 + LINKED)
    data[0x605] = 0x03
    data[0x606:LINKED] = b"\xff" * (LINKED - 0x606)
    command(data, LINKED, 0x4001, level=30)
    command(data, OUTSIDE, 7, level=40)

    for index, (species, wanted) in enumerate(((8, 9), (10, 11))):
        at = TRADES + index * 60
        struct.pack_into("<H", data, at + 12, species)
        struct.pack_into("<H", data, at + 56, wanted)

    struct.pack_into("<I", data, 0x1000, 0x08001100)
    struct.pack_into("<I", data, 0x1100, 0x08001200)
    struct.pack_into("<IIII", data, 0x1200, 0x08001300, 0x08001400, 0, 0)
    data[0x1200 + 20] = 0x5A
    struct.pack_into("<IIIIII", data, 0x1300, 1, 1, 0x08001500, 0x08001500, 0x08001500, 0x08001500)
    data[0x1400] = 1
    struct.pack_into("<IIII", data, 0x1404, 0x08001500, 0, 0, 0)
    struct.pack_into("<I", data, 0x1500 + 16, 0x08000100)
    struct.pack_into("<I", data, 0x1600, 0x08001700)
    data[0x1700] = 0xFF
    return bytes(data)

def profile() -> dict:
    return {
        "id": "demo",
        "maps": {"groups": 0x1000, "mapsec_names": 0x1600, "mapsec_first": 0, "mapsec_count": 256},
    }

def block() -> dict:
    return {
        "evidence": "synthetic map and bounded standalone scripts",
        "regions": [{"start": 0x400, "end": 0x500}],
        "linked_regions": [{"start": 0x600, "end": 0x700}],
        "commands": {"table": 0x15F9B4, "gift": 0x79, "egg": 0x7A,
                     "evidence": "synthetic command table and operand layouts"},
        "trades": {"base": TRADES, "stride": 60, "count": 2, "species": 12, "wanted": 56,
                   "command": 0xFE, "evidence": "synthetic trade records"},
        "choices": [
            {"offset": STARTER, "kind": "starter", "species": [3, 4], "item": 0,
             "evidence": "synthetic starter choices"},
            {"offset": EGG, "species": [1, 2], "map_section": 0x9B,
             "evidence": "synthetic random egg choices"},
        ],
    }

class SourceTest(unittest.TestCase):
    def test_all_source_kinds_contexts_and_offsets(self):
        rows = ps.build(block(), rom_bytes(), profile(), 16, 16)
        self.assertEqual(len(rows), 9)
        gift = next(row for row in rows if row["offset"] == "0x0000100")
        self.assertEqual((gift["kind"], gift["species"], gift["level"], gift["item"]), ("gift", 1, 5, 2))
        self.assertEqual((gift["map"], gift["map_section"], gift["script"]), ("0.0", 0x5A, "0x0000100"))
        starters = [row for row in rows if row["kind"] == "starter"]
        self.assertEqual([row["species"] for row in starters], [3, 4])
        self.assertTrue(all(row["level"] == 15 and row["item"] == 0 for row in starters))
        eggs = [row for row in rows if row["offset"] == "0x0000400"]
        self.assertEqual([row["species"] for row in eggs], [1, 2])
        self.assertTrue(all(row["map_section"] == 0x9B and row["script"] == "0x0000400" for row in eggs))
        linked = next(row for row in rows if row["offset"] == "0x0000610")
        self.assertIsNone(linked["species"])
        self.assertEqual(linked["species_variable"], 0x4001)
        self.assertEqual(linked["level"], 30)
        trades = [row for row in rows if row["kind"] == "trade"]
        self.assertEqual([(row["species"], row["wanted"]) for row in trades], [(8, 9), (10, 11)])
        self.assertEqual([row["offset"] for row in trades], ["0x0000A00", "0x0000A3C"])
        self.assertTrue(all(row["script"] == "0x000011E" for row in trades))

    def bound_fixture(self):
        b, data = block(), bytearray(rom_bytes())
        t = b["trades"]
        t["original_trainer"] = 40
        t["context"] = {"groups": 0x1000, "engine_groups": 0x1000, "engine_count": 1, "bank_xor": 0}
        t["bindings"] = []
        data[0x1400] = 2
        for index in range(2):
            root = 0x1800 + index * 16
            code = bytes((0x16, 4, 0x80, index, 0, 0x25, 0xFE, 0, 2))
            data[root:root + len(code)] = code
            struct.pack_into("<I", data, 0x1500 + index * 24 + 16, 0x08000000 + root)
            struct.pack_into("<I", data, TRADES + index * 60 + 40, 0x08001A00 + index * 16)
            t["bindings"].append({"index": index, "map": "0.0", "map_header": 0x1200,
                                  "roots": [root], "script": root, "guard": rom_evidence(root, code),
                                  "selector": {"offset": root + 3, "width": 2},
                                  "evidence": "synthetic selector and object",
                                  "availability": "visiting_trader"})
        t["bindings"][0].update(speaker="s0001A00", speaker_offset=0x1A42,
                                speaker_evidence="synthetic dialogue name box")
        data[0x1A40:0x1A42] = b"\x0f\x00"
        struct.pack_into("<I", data, 0x1A42, 0x08001A00)
        return b, data

    def test_trade_bindings_keep_row_identity_and_actual_context(self):
        b, data = self.bound_fixture()
        rows = ps._bound_trades(b["trades"], bytes(data), 16)
        self.assertEqual([(r["trade_index"], r["species"], r["wanted"], r["script"], r["map_section"])
                          for r in rows], [(0, 8, 9, "0x0001800", 90), (1, 10, 11, "0x0001810", 90)])
        self.assertEqual([r["original_trainer"] for r in rows], ["s0001A00", "s0001A10"])
        struct.pack_into("<H", data, TRADES + 60 + 12, 8)
        struct.pack_into("<H", data, TRADES + 60 + 56, 9)
        rows = ps._bound_trades(b["trades"], bytes(data), 16)
        self.assertEqual([r["original_trainer"] for r in rows], ["s0001A00", "s0001A10"])
        self.assertTrue(all(r["availability"] == "visiting_trader" for r in rows))
        self.assertEqual([r.get("speaker") for r in rows], ["s0001A00", None])
        self.assertTrue(all("partner" not in r for r in rows))

    def test_trade_bindings_fail_when_runtime_or_selector_evidence_changes(self):
        for address, value, message in ((0x1100, 0x08001300, "runtime map"),
                                        (0x1510, 0x08000100, "NPC script"),
                                        (0x1800, 0, "selector bytes"),
                                        (TRADES + 40, 0xFFFFFFFF, "original trainer pointer")):
            with self.subTest(address=address):
                b, data = self.bound_fixture()
                struct.pack_into("<I", data, address, value)
                with self.assertRaisesRegex(ValueError, message):
                    ps._bound_trades(b["trades"], bytes(data), 16)
        b, data = self.bound_fixture()
        b["trades"]["bindings"][0]["index"] = 1
        with self.assertRaisesRegex(ValueError, "selector index differs"):
            ps._bound_trades(b["trades"], bytes(data), 16)
        b, data = self.bound_fixture()
        b["trades"]["proofs"] = [{"offset": 0x1800, "size": 9, "sha256": "0" * 64}]
        with self.assertRaisesRegex(ValueError, "ROM bytes differ"):
            ps._bound_trades(b["trades"], bytes(data), 16)

    def test_trade_speaker_requires_its_name_box_pointer(self):
        b, data = self.bound_fixture()
        struct.pack_into("<I", data, 0x1A42, 0x08001A10)
        with self.assertRaisesRegex(ValueError, "speaker name box differs"):
            ps._bound_trades(b["trades"], bytes(data), 16)
        b, data = self.bound_fixture()
        del b["trades"]["bindings"][0]["speaker_evidence"]
        self.assertIn("trades bindings[0] speaker", ps.check(b))

    def test_unbound_rows_are_not_inferred_from_table_membership(self):
        b, data = self.bound_fixture()
        b["trades"]["bindings"] = b["trades"]["bindings"][:1]
        data[TRADES + 60:TRADES + 120] = bytes(60)
        self.assertEqual(len(ps._bound_trades(b["trades"], bytes(data), 16)), 1)

    def test_trade_binding_configuration_requires_context_and_evidence(self):
        b, _ = self.bound_fixture()
        self.assertEqual(ps.check(b), [])
        b["trades"]["bindings"][0]["index"] = 2
        self.assertIn("trades bindings[0]", ps.check(b))
        del b["trades"]["context"]["engine_groups"]
        self.assertIn("trades context", ps.check(b))

    def test_native_operands_outside_regions_and_bad_tails_are_rejected(self):
        rows = ps.build(block(), rom_bytes(), profile(), 16, 16)
        offsets = {row["offset"] for row in rows}
        self.assertNotIn("0x0000123", offsets)   # trainer id, not giveegg
        self.assertNotIn("0x0000420", offsets)   # invalid gift trailing argument
        self.assertNotIn("0x0000800", offsets)   # outside every configured region

    def test_rebuild_is_identical(self):
        args = (block(), rom_bytes(), profile(), 16, 16)
        self.assertEqual(ps.build(*args), ps.build(*args))

    def test_check_names_missing_evidence_and_duplicate_choices(self):
        b = block()
        del b["commands"]["evidence"]
        del b["trades"]["wanted"]
        b["choices"].append(b["choices"][0])
        problems = ps.check(b)
        self.assertIn("commands evidence", problems)
        self.assertIn("trades wanted", problems)
        self.assertIn("choices[2] duplicate offset", problems)

    def test_invalid_trade_species_is_refused(self):
        data = bytearray(rom_bytes())
        struct.pack_into("<H", data, TRADES + 12, 99)
        with self.assertRaisesRegex(ValueError, "trade 0: invalid species"):
            ps.build(block(), bytes(data), profile(), 16, 16)

    def test_dataset_provenance_and_verification(self):
        b = {
            "name_table": "species",
            "species": {"base": 0x1800, "stride": 1, "count": 16, "first": 1,
                        "evidence": "synthetic species slots", "fields": {}},
            "sources": block(),
        }
        doc = pd.build(b, rom_bytes(), profile())
        self.assertEqual(len(doc["sources"]), 9)
        self.assertEqual(doc["provenance"]["source_commands"]["offset"], "0x15f9b4")
        self.assertEqual(doc["provenance"]["source_trades"]["offset"], "0x000a00")
        self.assertEqual(pd.verify_doc(doc, b, "demo", doc["rom"]["sha1"]), [])
        del doc["sources"]
        self.assertIn("sources: regenerate", " ".join(pd.verify_doc(doc, b, "demo", doc["rom"]["sha1"])))
