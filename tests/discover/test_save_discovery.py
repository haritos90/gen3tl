import copy
import struct
import unittest
from types import SimpleNamespace

from gen3tl.core.charmap import Charmap
from gen3tl.core.rom import ROM_BASE, Rom
from gen3tl.discover import foundations, save
from gen3tl.verify.savefile import Layout, SaveFile, blank_save

def words(*values):
    return struct.pack("<" + "H" * len(values), *values)

def literal(rom, site, pool, value):
    displacement = pool - ((site + 4) & ~3)
    assert displacement >= 0 and displacement % 4 == 0 and displacement < 1024
    rom.write(site, words(0x4800 | displacement // 4))
    rom.put_u32(pool, value)

def pool(site):
    return ((site + 4) & ~3) + 0x80

def pooled(rom, site, value):
    literal(rom, site, pool(site), value)

def immediate(rom, site, mnemonic, value):
    encodings = {"mov": 0x2000, "cmp": 0x2800, "lsl": 0x0000, "add": 0x3000,
                 "ldrh": 0x8800, "strh": 0x8000}
    encoded = value << 6 if mnemonic == "lsl" else value
    if mnemonic in ("ldrh", "strh"):
        encoded = (value // 2) << 6
    rom.write(site, words(encodings[mnemonic] | encoded))

class SaveLayoutTest(unittest.TestCase):
    def fixture(self):
        family = copy.deepcopy(foundations.families()[0])
        config = family.anchors["save"]
        rom = Rom(b"\xff" * 0x10000)
        header = family.anchors["identity"]["header_offset"]
        for name, value in {"saveblock2_size": 0x100, "saveblock1_size": 0x30D0,
                            "party_count": 0x34, "party_offset": 0x38,
                            "trainer_id": 0xA, "player_name": 0}.items():
            rom.put_u32(header + config["header"][name], value)
        rom.write(header + config["header"]["player_name_length"], b"\x07")
        starts = {"save-block-addresses": 0x1000, "save-parasite": 0x1200, "save-extra": 0x1300,
                  "save-egg-name": 0x1400, "save-box-name": 0x1800, "save-preset-name": 0x1900,
                  "save-rival-naming": 0x2000, "save-daycare-store": 0x2100,
                  "save-mail-clear": 0x2200, "save-mail-record-clear": 0x2300,
                  "save-link-records": 0x2400, "save-link-record-clear": 0x2500,
                  "save-link-record-search": 0x2600, "save-box-name-pointer": 0x2700,
                  "save-hall-sectors": 0x2800, "save-hall-signature": 0x2900}
        readers = SimpleNamespace(matches={name: (start,) for name, start in starts.items()})
        bases = {"saveblock2": 0x02010000, "saveblock1": 0x02011000, "storage": 0x02012000}
        for index, (name, descriptor) in enumerate(config["block_pools"].items()):
            literal(rom, starts[descriptor["reader"]] + descriptor["offset"], 0x1100 + index * 4, bases[name])
        literal(rom, starts["save-parasite"] + config["parasite_pool"]["offset"], 0x1300, 0x02013000)
        literal(rom, starts["save-extra"] + config["extra_pool"]["offset"], 0x1400, 0x02014000)
        for offset, sector in zip(config["extra_sector_offsets"], (30, 31)):
            rom.write(starts["save-extra"] + offset, words(0x2000 | sector))
        cm = Charmap()
        for index, (name, descriptor) in enumerate(config["name_pools"].items()):
            target = 0x5000 + index * 0x100
            rom.write(target, cm.encode(descriptor["text"]))
            pool = {"egg": 0x1700, "box": 0x1900, "preset": 0x1A00}[name]
            literal(rom, starts[descriptor["reader"]] + descriptor["offset"], pool, ROM_BASE + target)

        descriptor = config["rival"]
        start = starts[descriptor["reader"]]
        pooled(rom, start + descriptor["offset"], 0x40)
        immediate(rom, start + descriptor["add"], "add", 0)
        immediate(rom, start + descriptor["screen"], "mov", descriptor["kind"])

        descriptor = config["box_names"]
        start = starts[descriptor["reader"]]
        pooled(rom, start + descriptor["offset"], ROM_BASE + 0x7000)
        for index in range(sum(row["count"] for row in config["box_ranges"])):
            rom.put_u32(0x7000 + descriptor["pointers"] + index * 4, bases["storage"] + 0x2000 + index * 9)

        descriptor = config["mail"]
        start = starts[descriptor["reader"]]
        pooled(rom, start + descriptor["offset"], 0x100)
        for offset, value in zip(descriptor["stride"], (3, 0, 2)):
            immediate(rom, start + offset, "add" if offset == descriptor["stride"][1] else "lsl", value)
        immediate(rom, start + descriptor["count"], "cmp", 15)
        record = descriptor["record"]
        start = starts[record["reader"]]
        for key, value in (("author", 0x12), ("trainer_id", 0x1A), ("item", 0x20), ("author_count", 7)):
            immediate(rom, start + record[key], "strh" if key == "item" else "cmp" if key == "author_count" else "add", value)

        descriptor = config["daycare_mail"]
        start = starts[descriptor["reader"]]
        for name, value in (("mail", 0x50), ("ot_name", 0x74), ("nickname", 0x7C)):
            immediate(rom, start + descriptor["fields"][name], "add", value)

        descriptor = config["link_records"]
        start = starts[descriptor["reader"]]
        pooled(rom, start + descriptor["offset"], 0x80)
        immediate(rom, start + descriptor["add"], "add", 0)
        start = starts[descriptor["clearer"]]
        immediate(rom, start + descriptor["count"], "mov", 4)
        immediate(rom, start + descriptor["stride"], "add", 16)
        immediate(rom, starts[descriptor["search"]] + descriptor["trainer_id"], "ldrh", 8)

        descriptor = config["hall_of_fame"]
        start = starts[descriptor["reader"]]
        for offset, value in zip(descriptor["sectors"], (28, 29)):
            immediate(rom, start + offset, "mov", value)
        immediate(rom, start + descriptor["size"], "mov", 0xF8)
        immediate(rom, start + descriptor["shift"], "lsl", 4)
        pooled(rom, starts[descriptor["signature_reader"]] + descriptor["signature"], 0x01121998)
        tables = {"table": [{"name": "species", "base": 0x6000, "stride": 11, "count": 4}]}
        return rom, family, tables, readers, bases

    def test_header_and_native_pools_build_a_readable_save(self):
        rom, family, tables, readers, bases = self.fixture()
        result = save.run(rom, family, tables, readers)
        self.assertEqual(result.issues, [])
        data = result.data["save"]
        self.assertEqual(data["party"]["count"], bases["saveblock1"] + 0x34)
        self.assertEqual(data["party"]["base"], bases["saveblock1"] + 0x38)
        self.assertEqual(data["player"], {"name": bases["saveblock2"], "size": 8, "limit": 7,
                                         "trainer_id": bases["saveblock2"] + 0xA})
        self.assertIn("save.player", [fact.id for fact in result.facts])
        self.assertNotIn("save.player.name", [fact.id for fact in result.facts])
        self.assertEqual(data["names"]["species_table"], 0x6000)
        self.assertEqual(data["extra"], {"ram": 0x02014000, "sectors": [30, 31]})
        self.assertEqual(data["mon_names"], family.anchors["save"]["mon_names"])
        self.assertEqual(data["rival"], {"name": bases["saveblock1"] + 0x40, "size": 8, "limit": 7})
        self.assertEqual(data["box_names"]["addresses"],
                         [bases["storage"] + 0x2000 + index * 9 for index in range(25)])
        self.assertEqual(data["mail"], {"base": bases["saveblock1"] + 0x100, "count": 16, "stride": 36,
                                       "author": 0x12, "author_size": 8, "author_limit": 7, "author_pad": 6,
                                       "trainer_id": 0x1A, "item": 0x20})
        self.assertEqual(data["daycare_mail"], {"mail": 0x50, "ot_name": 0x74, "ot_name_size": 8,
                                               "nickname": 0x7C, "nickname_size": 11})
        self.assertEqual(data["link_records"], {"base": bases["saveblock2"] + 0x80, "count": 5, "stride": 16,
                                               "name_size": 8, "limit": 7, "trainer_id": 8})
        self.assertEqual(data["hall_of_fame"]["sectors"], [28, 29])
        self.assertEqual(data["hall_of_fame"]["data_size"], 0xF80)
        self.assertEqual(data["hall_of_fame"]["signature"], 0x01121998)
        self.assertEqual(data["egg_hatch"], {"step_counter": data["daycare"]["base"] + 0x11A,
                                            "cycles": 0x29, "eggs_waiting": 0x02013000 + 0x2F4})
        layout = Layout.from_dict(data)
        document = SaveFile(blank_save(layout), layout)
        slot = document.slots[0]
        document.write(slot, layout.player["name"], b"A\xff".ljust(8, b"\xff"))
        document.write(slot, layout.player["trainer_id"], struct.pack("<I", 0x12345678))
        document.write(slot, layout.party["count"], b"\x01")
        document.write(slot, layout.rival["name"], b"R\xff".ljust(8, b"\xff"))
        document.write(slot, layout.box_names["addresses"][0], b"B\xff".ljust(9, b"\xff"))
        document.fix_checksums()
        reread = SaveFile(document.to_bytes("sav"), layout)
        slot = reread.slots[0]
        self.assertEqual(reread.read(slot, layout.player["name"], 8), b"A\xff".ljust(8, b"\xff"))
        self.assertEqual(struct.unpack("<I", reread.read(slot, layout.player["trainer_id"], 4))[0], 0x12345678)
        self.assertEqual(reread.read(slot, layout.party["count"], 1), b"\x01")
        self.assertEqual(reread.read(slot, layout.rival["name"], 8), b"R\xff".ljust(8, b"\xff"))
        self.assertEqual(reread.read(slot, layout.box_names["addresses"][0], 9), b"B\xff".ljust(9, b"\xff"))

    def test_inconsistent_name_field_evidence_is_refused(self):
        for section in ("rival", "box_names", "mail", "daycare_mail", "link_records",
                        "hall_of_fame", "egg_hatch", "mon_names"):
            with self.subTest(section=section):
                rom, family, tables, readers, _ = self.fixture()
                config = family.anchors["save"]
                if section == "rival":
                    descriptor = config["rival"]
                    rom.put_u32(pool(readers.matches[descriptor["reader"]][0] + descriptor["offset"]), 0x30D0)
                elif section == "box_names":
                    descriptor = config["box_names"]
                    rom.put_u32(0x7000 + descriptor["pointers"], 0x03000000)
                elif section == "mail":
                    descriptor = config["mail"]
                    rom.write(readers.matches[descriptor["reader"]][0] + descriptor["stride"][1], words(0x4770))
                elif section == "daycare_mail":
                    descriptor = config["daycare_mail"]
                    immediate(rom, readers.matches[descriptor["reader"]][0] + descriptor["fields"]["nickname"],
                              "add", 0x70)
                elif section == "link_records":
                    descriptor = config["link_records"]
                    immediate(rom, readers.matches[descriptor["search"]][0] + descriptor["trainer_id"], "ldrh", 20)
                elif section == "hall_of_fame":
                    descriptor = config["hall_of_fame"]
                    immediate(rom, readers.matches[descriptor["reader"]][0] + descriptor["sectors"][0], "mov", 32)
                elif section == "egg_hatch":
                    config["egg_hatch"]["step_from_daycare"] = 0x1000
                else:
                    config["mon_names"]["nickname_size"] = 20
                result = save.run(rom, family, tables, readers)
                self.assertNotIn(section, result.data["save"])
                self.assertIn("save." + section, [fact.id for fact in result.issues])

    def test_non_ram_block_pointer_is_refused(self):
        rom, family, tables, readers, _ = self.fixture()
        rom.put_u32(0x1100, 0x03000000)
        result = save.run(rom, family, tables, readers)
        self.assertNotIn("blocks", result.data["save"])
        self.assertNotIn("party", result.data["save"])
        self.assertIn("save.blocks", [fact.id for fact in result.issues])
