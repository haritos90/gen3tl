import hashlib
import copy
import contextlib
import io
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from types import SimpleNamespace

from gen3tl.cli import data as cli
from gen3tl.data import pokedexdata as pd
from project_fixture import rom_evidence, with_profile_notes

BASE = 0x100          # species table, stride 28

STRIDE = 28

DEX_BASE = 0x400      # dex records, stride 36, indexed by national dex

NAT_BASE = 0x300      # slot -> national dex, u16

EVO_BASE = 0x600      # evolution blocks, 128 bytes per slot, 8-byte records

PTR_BASE = 0x900      # pointer-mode evolution lists

def rom_bytes() -> bytes:
    """A ROM-shaped buffer: three species slots, a natdex map, dex records and evolution data."""
    data = bytearray(0x1000)
    mono = [(45, 49, 49, 45, 65, 65), (60, 62, 63, 60, 80, 80), (0, 0, 0, 0, 0, 0)]
    for slot, (hp, atk, df, spe, spa, spd) in enumerate(mono):
        at = BASE + slot * STRIDE
        struct.pack_into("<6B", data, at, hp, atk, df, spe, spa, spd)
        data[at + 6], data[at + 7] = (12, 3) if slot == 1 else (0, 2)     # types: 0 is Normal
        data[at + 8], data[at + 9] = 45, 64                                # catch rate, exp yield
        data[at + 22] = 3                                                  # growth
        data[at + 24], data[at + 25] = 65, 0                               # abilities
        struct.pack_into("<I", data, at + 12, 0x08000900)                  # pointer evolution
    struct.pack_into("<3H", data, NAT_BASE, 0, 1, 2)                       # natdex map
    for dex, (height, weight) in enumerate(((7, 69), (10, 130), (20, 220))):
        at = DEX_BASE + dex * 36
        data[at:at + 6] = b"Seed\xff\x00"                                  # inline category
        struct.pack_into("<H", data, at + 12, height)
        struct.pack_into("<H", data, at + 14, weight)
        struct.pack_into("<I", data, at + 16, 0x08000300 + dex * 0x40)      # description pointer
    for slot in (1, 2):
        at = EVO_BASE + slot * 128
        struct.pack_into("<4H", data, at, 4, 16, slot + 1, 0)               # level 16 -> next
    struct.pack_into("<4H", data, PTR_BASE, 7, 139, 136, 0)                 # item evolution
    struct.pack_into("<4H", data, PTR_BASE + 8, 2, 0, 134, 0)
    struct.pack_into("<4H", data, PTR_BASE + 16, 0xFFFF, 0, 0, 0)           # terminator
    return bytes(data)

def block(evolution=None, **overrides) -> dict:
    b = {
        "schema": 1,
        "name_table": "species",
        "species": {
            "base": BASE, "stride": STRIDE, "count": 3, "first": 1,
            "evidence": "synthetic fixture: six stat bytes at each stride",
            "fields": {
                "hp": 0, "attack": 1, "defense": 2, "speed": 3, "sp_attack": 4, "sp_defense": 5,
                "type1": 6, "type2": 7, "catch_rate": 8, "base_exp": 9, "growth": 22,
                "ability1": 24, "ability2": 25,
            },
        },
        "dex": {
            "index": "dex",
            "category_table": "pokedex_categories",
            "map": {"base": NAT_BASE, "stride": 2, "width": 2},
            "records": {
                "base": DEX_BASE, "stride": 36, "evidence": "one 36-byte record per dex number",
                "fields": {"category": 0, "height": 12, "weight": 14,
                           "description": {"offset": 16, "width": 4}},
            },
        },
    }
    if evolution is not None:
        b["evolution"] = evolution
    b.update(overrides)
    return b

U16 = lambda n: {"offset": n, "width": 2}   # noqa: E731 - the engine stores these as u16

FIXED = {"index": "slot", "base": EVO_BASE, "stride": 128, "slots": 16, "record": 8, "stop": 0,
         "evidence": "one 128-byte block per slot",
         "fields": {"method": U16(0), "parameter": U16(2), "target": U16(4), "extra": U16(6)}}

POINTER = {"index": "pointer", "pointer": 12, "record": 8, "stop": 0xFFFF, "count": 4,
           "evidence": "a pointer at species+12 leads to 8-byte records",
           "fields": {"method": U16(0), "parameter": U16(2), "target": U16(4)}}

class DatasetTest(unittest.TestCase):
    def test_bound_notes_survive_dataset_roundtrip(self):
        configured = block(FIXED)
        configured["species"]["evidence"] = "Measured stats.\nVerified strides."
        data = rom_bytes()
        baseline = pd.build(configured, data, {"id": "test"})
        for status in ("unreviewed", "accepted"):
            with self.subTest(status=status):
                migrated = with_profile_notes(configured, status)
                doc = pd.build(migrated, data, {"id": "test"})
                self.assertEqual(doc["species"], baseline["species"])
                with tempfile.TemporaryDirectory() as tmp:
                    pd.write(Path(tmp), doc, split=True)
                    restored = pd.read(Path(tmp) / "pokedex.json")
                    self.assertEqual(pd.verify_doc(restored, migrated, "test", doc["rom"]["sha1"]), [])
                self.assertEqual(doc["provenance"]["species"]["evidence"],
                                 configured["species"]["evidence"])
        configured["species"]["evidence"] = ""
        self.assertTrue(pd.check(configured))

    def test_form_classification_uses_inherited_abilities(self):
        data = bytearray(rom_bytes())
        data[BASE + 2 * STRIDE + 24] = 64
        struct.pack_into("<H", data, NAT_BASE + 4, 1)
        struct.pack_into("<4H", data, EVO_BASE + 128, 42, 1, 2, 0)
        struct.pack_into("<4H", data, EVO_BASE + 256, 42, 0, 1, 0)
        for ability in (64, 65):
            struct.pack_into("<I", data, 0xA00 + ability * 4, 0x08000E00)
        abilities = {"base": 0xC00, "stride": 1, "count": 66, "evidence": "Synthetic names.",
                     "descriptions": {"base": 0xA00, "count": 66, "evidence": "Synthetic descriptions."},
                     "presentation": {"evidence": "Synthetic inheritance.", "rom": [rom_evidence(0, "00")],
                                      "inherit_forms": {"method": 42, "parameter_min": 1,
                                                        "evidence": "Preserved battle ability."}}}
        rule = {"compare": ["abilities"], "preserve_methods": [42], "evidence": "Synthetic forms."}
        configured = block(FIXED, abilities=abilities, species_browsing={"forms": rule})
        doc = pd.build(configured, bytes(data), {"id": "test"})
        self.assertEqual(doc["species"][1]["abilities"], [65])
        self.assertEqual(doc["species"][1]["form_browsing"]["differences"], [])
        self.assertEqual(pd.verify_doc(doc, configured, "test", doc["rom"]["sha1"]), [])

    def test_form_browsing_generation_roundtrip_and_stale_classification(self):
        data = bytearray(rom_bytes())
        struct.pack_into("<H", data, NAT_BASE + 4, 1)
        rule = {"compare": ["types", "base_stats", "abilities"], "evidence": "Synthetic forms."}
        configured = block(species_browsing={"forms": rule})
        doc = pd.build(configured, bytes(data), {"id": "test"})
        self.assertEqual(doc["species_browsing"], {"forms": rule})
        self.assertNotIn("form_browsing", doc["species"][0])
        form = doc["species"][1]
        self.assertEqual(form["base_species"], 1)
        self.assertTrue(form["form_browsing"]["separate"])
        self.assertEqual(form["form_browsing"]["differences"], ["types", "base_stats"])
        self.assertEqual(pd.verify_doc(doc, configured, "test", doc["rom"]["sha1"]), [])
        with tempfile.TemporaryDirectory() as temp:
            for split in (False, True):
                pd.write(Path(temp), doc, split=split)
                restored = pd.read(Path(temp) / "pokedex.json")
                self.assertEqual(restored["species"], doc["species"])
                self.assertEqual(pd.verify_doc(restored, configured, "test", doc["rom"]["sha1"]), [])
        form["form_browsing"]["separate"] = False
        self.assertIn("form_browsing: regenerate species slot 2",
                      pd.verify_doc(doc, configured, "test", doc["rom"]["sha1"]))
        for invalid in (None, [], "invalid", {"compare": ["learnset"], "evidence": "Missing table."}):
            self.assertTrue(pd.check(block(species_browsing={"forms": invalid})))

    def test_encounter_label_inventory_and_exported_rows_are_checked(self):
        from test_pokedexencounters import trigger_fixture
        from gen3tl.data import pokedexencounters

        encounters, data = trigger_fixture()
        labels = {"strings": {"Meadow": "s0000100", "Surfing": "s0000200"},
                  "symbols": {}, "unmapped": {}}
        config = block(encounters=encounters, labels=labels)
        self.assertEqual(pd.check(config), [])
        doc = pd.build(block(), rom_bytes(), {"id": "test"})
        doc.update(profile_block=pd.block_fingerprint(config), labels=labels["strings"],
                   encounters=pokedexencounters.build(encounters, data, 1294)["records"],
                   encounter_unavailable=encounters["unavailable"])
        verify = lambda value: pd.verify_doc(value, config, "test", doc["rom"]["sha1"])
        self.assertEqual(verify(doc), [])
        row = next(row for row in doc["encounters"] if row["method"] == "water")
        row["method_labels"] = ["Surfing"]
        self.assertIn("method_labels: regenerate the dataset with gen3tl data pokedex", verify(doc))
        del row["method_labels"]
        self.assertIn("method_labels: regenerate the dataset with gen3tl data pokedex", verify(doc))
        del labels["strings"]["Meadow"]
        self.assertIn("[pokedex.encounters] unknown method label Meadow", pd.check(config))

    def test_other_browsing_rules_preserve_records_and_guard_exports(self):
        from pokedexmoves_support import move_block
        from test_pokedexitems import item_block

        data = rom_bytes()
        tables = {"moves": move_block(), "items": item_block(),
                  "abilities": {"base": 0xA00, "stride": 17, "count": 3,
                                "evidence": "Synthetic ability names."}}
        tables["moves"].pop("lists")
        tables["moves"].pop("readers")
        tables["moves"]["base"] = 0xA00
        for kind, table in tables.items():
            with self.subTest(kind=kind):
                key = f"{kind}_browsing"
                at, size = table["base"], table["stride"]
                rule = {"status": "unreleased", "evidence": "Verified empty placeholder.",
                        "rom": [{"offset": at, "size": size,
                                 "sha256": hashlib.sha256(data[at:at + size]).hexdigest()}]}
                configured = block(**{kind: table, key: {"0": rule}})
                raw = pd.build(block(**{kind: table}), data, {"id": "test"})
                doc = pd.build(configured, data, {"id": "test"})
                self.assertEqual(doc[kind], raw[kind])
                self.assertNotIn(key, raw)
                self.assertEqual(doc[key], {"0": "unreleased"})
                verify = lambda value: pd.verify_doc(value, configured, "test", doc["rom"]["sha1"])
                self.assertEqual(verify(doc), [])
                for stale in ({}, {"0": "released"}, {"1": "unreleased"}):
                    self.assertIn(f"{key}: regenerate the dataset with gen3tl data pokedex",
                                  verify(dict(doc, **{key: stale})))
                self.assertIn(f"{key}: a classified {kind} record is missing",
                              verify(dict(doc, **{kind: doc[kind][1:]})))
                for number in ("-1", "01", str(table["count"]), "invalid", "1"):
                    self.assertTrue(pd.check(block(**{kind: table, key: {number: rule}})), number)
                for field, value in (("status", "hidden"), ("evidence", ""), ("rom", [])):
                    changed = dict(rule, **{field: value})
                    self.assertTrue(pd.check(block(**{kind: table, key: {"0": changed}})), field)
                for field, value in (("offset", at + 1), ("size", size - 1), ("sha256", "invalid")):
                    changed = copy.deepcopy(rule)
                    changed["rom"][0][field] = value
                    self.assertTrue(pd.check(block(**{kind: table, key: {"0": changed}})), field)
                modified = bytearray(data)
                modified[at] ^= 1
                with self.assertRaisesRegex(pd.PokedexError, f"{key} 0: ROM span differs"):
                    pd.build(configured, modified, {"id": "test"})
                with tempfile.TemporaryDirectory() as tmp:
                    for split in (False, True):
                        path = pd.write(Path(tmp) / str(split), doc, split=split)[0]
                        self.assertEqual(verify(json.loads(path.read_text())), [])
                        self.assertEqual(pd.read(path)[kind], raw[kind])
                        self.assertEqual(pd.read(path)[key], {"0": "unreleased"})

    def test_browsing_status_preserves_raw_records_and_checks_exported_metadata(self):
        data = rom_bytes()
        rule = {"status": "unreleased", "evidence": "Verified release classification.",
                "rom": [{"offset": BASE + STRIDE, "size": STRIDE,
                         "sha256": hashlib.sha256(data[BASE + STRIDE:BASE + 2 * STRIDE]).hexdigest()}]}
        configured = block(species_browsing={"1": rule})
        raw = pd.build(block(), data, {"id": "test"})
        doc = pd.build(configured, data, {"id": "test"})
        self.assertEqual(doc["species"], raw["species"])
        self.assertNotIn("species_browsing", raw)
        self.assertEqual(doc["species_browsing"], {"1": "unreleased"})
        self.assertEqual(pd.verify_doc(doc, configured, "test", doc["rom"]["sha1"]), [])
        for stale in ({}, {"1": "released"}, {"2": "unreleased"}):
            changed = dict(doc, species_browsing=stale)
            self.assertTrue(any("species_browsing" in issue for issue in pd.verify_doc(changed, configured, "test", doc["rom"]["sha1"])))
        missing = dict(doc, species=doc["species"][1:])
        self.assertIn("species_browsing: a classified species record is missing",
                      pd.verify_doc(missing, configured, "test", doc["rom"]["sha1"]))
        self.assertIn("rom sha1", pd.verify_doc(doc, configured, "test", "0" * 40)[0])
        changed = copy.deepcopy(configured)
        changed["species_browsing"]["1"]["evidence"] = "Different proof."
        self.assertIn("profile_block", pd.verify_doc(doc, changed, "test", doc["rom"]["sha1"])[0])
        with tempfile.TemporaryDirectory() as tmp:
            for split in (False, True):
                path = pd.write(Path(tmp) / str(split), doc, split=split)[0]
                index = json.loads(path.read_text())
                self.assertEqual(pd.verify_doc(index, configured, "test", doc["rom"]["sha1"]), [])
                self.assertEqual(pd.read(path)["species"], doc["species"])
                self.assertEqual(pd.read(path)["species_browsing"], {"1": "unreleased"})
                if split:
                    del index["files"]["1"]
                    self.assertIn("species_browsing: a classified species record is missing",
                                  pd.verify_doc(index, configured, "test", doc["rom"]["sha1"]))

    def test_browsing_declarations_require_status_evidence_and_exact_record_span(self):
        data = rom_bytes()
        rule = {"status": "unreleased", "evidence": "Verified release classification.",
                "rom": [{"offset": BASE + STRIDE, "size": STRIDE,
                         "sha256": hashlib.sha256(data[BASE + STRIDE:BASE + 2 * STRIDE]).hexdigest()}]}
        for key, value in [("status", "hidden"), ("evidence", ""), ("rom", [])]:
            changed = dict(rule, **{key: value})
            self.assertTrue(pd.check(block(species_browsing={"1": changed})))
        for number in ("0", "01", "3", "wrong", "2"):
            self.assertTrue(pd.check(block(species_browsing={number: rule})), number)
        for key, value in [("offset", BASE), ("size", STRIDE - 1), ("sha256", "invalid")]:
            changed = copy.deepcopy(rule)
            changed["rom"][0][key] = value
            self.assertTrue(pd.check(block(species_browsing={"1": changed})), key)
        configured = block(species_browsing={"1": rule})
        modified = bytearray(data)
        modified[BASE + STRIDE] ^= 1
        with self.assertRaisesRegex(pd.PokedexError, "ROM span differs"):
            pd.build(configured, modified, {"id": "test"})
        changed = copy.deepcopy(rule)
        changed["rom"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(pd.PokedexError, "ROM span differs"):
            pd.build(block(species_browsing={"1": changed}), data, {"id": "test"})

    def test_profile_kinds_classify_forward_relations_without_changing_raw_edges(self):
        ev = copy.deepcopy(FIXED)
        ev["form_kinds"] = {"mega": {"method": 254, "parameter_min": 1, "extras": [0, 2],
                                       "evidence": "Verified transformation subtypes."}}
        data = bytearray(rom_bytes())
        rows = [(254, 42, 2, 0), (254, 0, 1, 0), (254, 559, 3, 2),
                (254, 277, 4, 1), (254, 445, 5, 3), (253, 0, 6, 0), (254, 42, 7, 99)]
        for index, row in enumerate(rows):
            struct.pack_into("<4H", data, EVO_BASE + 128 + index * 8, *row)
        configured = block(ev)
        self.assertEqual(pd.check(configured), [])
        edges = pd._evolution(configured, data, 1, BASE + STRIDE)
        self.assertEqual([edge.get("kind") for edge in edges],
                         ["mega", None, "mega", None, None, None, None])
        for actual, expected in zip(edges, rows):
            self.assertEqual(tuple(actual[key] for key in ("method", "parameter", "target", "extra")), expected)
        ev["form_kinds"]["conflict"] = dict(ev["form_kinds"]["mega"])
        with self.assertRaisesRegex(ValueError, "ambiguous evolution kind"):
            pd._evolution(configured, data, 1, BASE + STRIDE)

    def test_form_kind_rules_require_complete_typed_reader_metadata(self):
        valid = {"method": 254, "parameter_min": 1, "extras": [0, 2], "evidence": "Reader proof."}
        for key, value in (("method", True), ("parameter_min", -1), ("extras", []),
                           ("extras", [0, 0]), ("extras", [False]), ("extras", [[0]]),
                           ("evidence", False), ("evidence", " \n")):
            ev = copy.deepcopy(FIXED)
            ev["form_kinds"] = {"mega": dict(valid, **{key: value})}
            self.assertIn("[pokedex.evolution.form_kinds] mega", pd.check(block(ev)))
        ev = copy.deepcopy(FIXED)
        ev["form_kinds"] = {"mega": None}
        self.assertIn("[pokedex.evolution.form_kinds] mega", pd.check(block(ev)))
        ev["form_kinds"] = []
        self.assertIn("[pokedex.evolution] form_kinds", pd.check(block(ev)))
        ev["form_kinds"] = {"mega": valid}
        ev["fields"].pop("extra")
        self.assertIn("[pokedex.evolution.form_kinds] fields", pd.check(block(ev)))

    def test_parameter_kinds_annotate_move_requirements_without_changing_raw_edges(self):
        ev = copy.deepcopy(FIXED)
        ev["parameter_kinds"] = {"move": {"method": 254, "parameter_min": 1, "extras": [2],
                                           "evidence": "Native code compares the parameter with known moves."}}
        data = bytearray(rom_bytes())
        rows = [(254, 559, 911, 2), (254, 447, 870, 0), (7, 91, 2, 0),
                (254, 277, 4, 1), (254, 0, 911, 2)]
        for index, row in enumerate(rows):
            struct.pack_into("<4H", data, EVO_BASE + 128 + index * 8, *row)
        configured = block(ev)
        self.assertEqual(pd.check(configured), [])
        edges = pd._evolution(configured, data, 1, BASE + STRIDE)
        self.assertEqual([edge.get("parameter_kind") for edge in edges], ["move", None, None, None, None])
        self.assertNotIn("parameter_kind", edges[1])
        for actual, expected in zip(edges, rows):
            self.assertEqual(tuple(actual[key] for key in ("method", "parameter", "target", "extra")), expected)
        ev["parameter_kinds"]["other"] = dict(ev["parameter_kinds"]["move"])
        with self.assertRaisesRegex(ValueError, "ambiguous evolution parameter kind"):
            pd._evolution(configured, data, 1, BASE + STRIDE)

    def test_parameter_kind_rules_require_complete_typed_reader_metadata(self):
        valid = {"method": 254, "parameter_min": 1, "extras": [2], "evidence": "Reader proof."}
        for key, value in (("method", True), ("parameter_min", -1), ("extras", []),
                           ("extras", [0, 0]), ("extras", [False]), ("extras", [[0]]),
                           ("evidence", False), ("evidence", " \n")):
            ev = copy.deepcopy(FIXED)
            ev["parameter_kinds"] = {"move": dict(valid, **{key: value})}
            self.assertIn("[pokedex.evolution.parameter_kinds] move", pd.check(block(ev)))
        ev = copy.deepcopy(FIXED)
        ev["parameter_kinds"] = {"move": None}
        self.assertIn("[pokedex.evolution.parameter_kinds] move", pd.check(block(ev)))
        ev["parameter_kinds"] = []
        self.assertIn("[pokedex.evolution] parameter_kinds", pd.check(block(ev)))
        ev["parameter_kinds"] = {"move": valid}
        ev["fields"].pop("extra")
        self.assertIn("[pokedex.evolution.parameter_kinds] fields", pd.check(block(ev)))

    def test_records_hold_ids_and_numbers_only(self):
        doc = pd.build(block(FIXED), rom_bytes(), {"id": "demo", "name": "Demo ROM"})
        self.assertEqual(doc["format"], pd.FORMAT)
        self.assertEqual(doc["rom"]["sha1"], hashlib.sha1(rom_bytes()).hexdigest())
        self.assertEqual(doc["missing"], [])                   # every block is carried or fed by the dex
        first = doc["species"][0]
        self.assertEqual((first["slot"], first["dex"]), (1, 1))
        self.assertEqual(first["offset"], f"0x{BASE + STRIDE:06x}")
        self.assertEqual(first["name"], "species:1")           # an id, not a name
        self.assertEqual(first["types"], [12, 3])
        self.assertEqual(doc["species"][1]["types"], [0, 2])       # type id 0 survives
        self.assertEqual(first["base_stats"]["hp"], 60)
        self.assertEqual(first["abilities"], [65])
        self.assertEqual((first["height"], first["weight"]), (10, 130))   # from the dex record
        self.assertEqual(first["entry"], ["s0000340"])                     # the dex description
        self.assertEqual(first["category"], "pokedex_categories:1")        # inline text: the table id
        self.assertEqual(first["category_offset"], f"0x{DEX_BASE + 36:06x}")
        self.assertIsNone(first["sprite"])

    def test_charizard_has_its_distinct_regular_and_hidden_abilities(self):
        data = bytearray(rom_bytes())
        at = BASE + STRIDE
        data[at + 24], data[at + 25], data[at + 26] = 66, 66, 96
        b = block(FIXED)
        b["species"]["fields"]["hidden_ability"] = 26
        doc = pd.build(b, bytes(data), {"id": "demo"})
        self.assertEqual(doc["species"][0]["abilities"], [66, 96])
        self.assertEqual(doc["species"][0]["ability_details"], [
            {"id": 66, "regular": True, "hidden": False},
            {"id": 96, "regular": False, "hidden": True},
        ])
        self.assertEqual(pd.verify_doc(doc, b, "demo", hashlib.sha1(bytes(data)).hexdigest()), [])

    def test_the_same_ability_can_have_regular_and_hidden_roles(self):
        data = bytearray(rom_bytes())
        at = BASE + STRIDE
        data[at + 24], data[at + 25], data[at + 26] = 66, 66, 66
        b = block(FIXED)
        b["species"]["fields"]["hidden_ability"] = 26
        doc = pd.build(b, bytes(data), {"id": "demo"})
        self.assertEqual(doc["species"][0]["ability_details"],
                         [{"id": 66, "regular": True, "hidden": True}])

    def test_ev_yields_take_two_bits_per_stat(self):
        data = bytearray(rom_bytes())
        struct.pack_into("<H", data, BASE + STRIDE + 10, 0x0480)   # Speed 2, Sp. Def 1
        struct.pack_into("<H", data, BASE + 2 * STRIDE + 10, 0x0E3F)   # 3, 3, 3, 0, 2, 3
        b = block(FIXED)
        b["species"]["ev_yield"] = {"offset": 10, "evidence": "synthetic two-bit yields"}
        doc = pd.build(b, bytes(data), {"id": "demo"})
        first, second = doc["species"]
        self.assertEqual(first["ev_yield"], {"hp": 0, "attack": 0, "defense": 0, "speed": 2,
                                             "sp_attack": 0, "sp_defense": 1})
        self.assertEqual(second["ev_yield"], {"hp": 3, "attack": 3, "defense": 3, "speed": 0,
                                              "sp_attack": 2, "sp_defense": 3})
        self.assertEqual(doc["provenance"]["ev_yield"]["offset"], f"0x{BASE + 10:06x}")
        sha1 = hashlib.sha1(bytes(data)).hexdigest()
        self.assertEqual(pd.verify_doc(doc, b, "demo", sha1), [])
        del doc["species"][1]["ev_yield"]
        self.assertIn("ev_yield: regenerate species slot 2", pd.verify_doc(doc, b, "demo", sha1))
        self.assertIsNone(pd.build(block(FIXED), bytes(data), {"id": "demo"})["species"][0]["ev_yield"])
        b["species"]["ev_yield"] = {}
        self.assertEqual(pd.check(b)[-2:], ["[pokedex.species.ev_yield] offset", "[pokedex.species.ev_yield] evidence"])

    def test_egg_groups_friendship_and_form_base_species(self):
        data = bytearray(rom_bytes())
        at = BASE + STRIDE
        data[at + 18], data[at + 20], data[at + 21] = 50, 1, 7
        data[at + STRIDE + 18], data[at + STRIDE + 20], data[at + STRIDE + 21] = 70, 2, 8
        struct.pack_into("<H", data, NAT_BASE + 4, 1)
        b = block(FIXED)
        b["species"]["fields"].update({"base_friendship": 18, "egg_group1": 20, "egg_group2": 21})
        doc = pd.build(b, bytes(data), {"id": "demo"})
        first, second = doc["species"]
        self.assertEqual((first["egg_groups"], first["base_friendship"]), ([1, 7], 50))
        self.assertEqual((second["egg_groups"], second["base_friendship"]), ([2, 8], 70))
        self.assertEqual((first["base_species"], second["base_species"]), (1, 1))

    def test_map_sections_hold_section_numbers_and_name_ids(self):
        data = bytearray(rom_bytes())
        struct.pack_into("<I", data, 0xB00, 0x08000300)
        b = block(FIXED)
        b["map_sections"] = {"base": 0xB00, "stride": 4, "first": 0x58, "count": 1,
                             "evidence": "synthetic map section pointer"}
        doc = pd.build(b, bytes(data), {"id": "demo"})
        self.assertEqual(doc["map_sections"], [{"id": 0x58, "name": "s0000300"}])
        self.assertEqual(doc["provenance"]["map_sections"]["offset"], "0x000b00")
        self.assertEqual(pd.verify_doc(doc, b, "demo", hashlib.sha1(bytes(data)).hexdigest()), [])

    def test_the_document_fingerprints_the_profile_block(self):
        doc = pd.build(block(FIXED), rom_bytes(), {"id": "demo"})
        self.assertEqual(doc["profile_block"], pd.block_fingerprint(block(FIXED)))
        changed = block(FIXED)
        changed["species"]["stride"] = 32
        self.assertNotEqual(doc["profile_block"], pd.block_fingerprint(changed))

    def test_verify_doc_accepts_the_current_block_and_names_stale_parts(self):
        rom = rom_bytes()
        b = block(FIXED)
        doc = pd.build(b, rom, {"id": "demo", "name": "Demo ROM"})
        self.assertEqual(pd.verify_doc(doc, b, "demo", hashlib.sha1(rom).hexdigest()), [])
        changed = block(FIXED)
        changed["species"]["base"] = BASE + 4
        problems = pd.verify_doc(doc, changed, "demo", hashlib.sha1(rom).hexdigest())
        self.assertIn("profile_block", problems[0])
        self.assertIn("rom sha1", pd.verify_doc(doc, b, "demo", "0" * 40)[0])
        self.assertIn("profile", pd.verify_doc(doc, b, "other", hashlib.sha1(rom).hexdigest())[0])

    def test_a_species_record_can_carry_the_inline_category(self):
        data = bytearray(rom_bytes())
        at = BASE + 28 + 31
        data[at:at + 5] = b"Seed\xff"
        b = block(FIXED)
        b.pop("dex")
        b["species"]["category_table"] = "pokedex_categories"
        b["species"]["category_field"] = 31
        doc = pd.build(b, bytes(data), {"id": "demo"})
        first = doc["species"][0]
        self.assertEqual(first["category"], "pokedex_categories:1")
        self.assertEqual(first["category_offset"], f"0x{at:06x}")
        self.assertIn("dex", doc["missing"])
        self.assertIsNone(first["entry"])

    def test_evolution_block_per_slot(self):
        doc = pd.build(block(FIXED), rom_bytes(), {"id": "demo"})
        self.assertEqual(doc["species"][0]["evolution"],
                         [{"method": 4, "parameter": 16, "target": 2, "extra": 0}])

    def test_evolution_pointer_list(self):
        doc = pd.build(block(POINTER), rom_bytes(), {"id": "demo"})
        first = doc["species"][0]
        self.assertEqual([e["method"] for e in first["evolution"]], [7, 2])   # stops at 0xFFFF
        self.assertEqual(first["evolution"][0]["parameter"], 139)

    def test_named_tile_evolution_in_both_record_shapes(self):
        for evolution, at in ((FIXED, EVO_BASE + 128), (POINTER, PTR_BASE)):
            with self.subTest(index=evolution["index"]):
                data = bytearray(rom_bytes())
                struct.pack_into("<4H", data, at, 34, 96, 2, 23)
                ev = {**evolution, "tile_methods": [34],
                      "fields": {**evolution["fields"], "extra": U16(6)}}
                tiles = {"evidence": "synthetic terrain mapping", "names": {"23": "shallow water"}}
                doc = pd.build(block(ev, tile_behaviors=tiles), bytes(data), {"id": "demo"})
                self.assertEqual(doc["species"][0]["evolution"][0],
                                 {"method": 34, "parameter": 96, "target": 2,
                                  "extra": 23, "tile": "shallow water"})

    def test_unnamed_tile_and_unrelated_methods_preserve_raw_extra(self):
        tiles = {"evidence": "synthetic terrain mapping", "names": {"23": "shallow water"}}
        for method, extra, facts in ((34, 99, {"tile_behaviors": tiles}), (34, 23, {}),
                                     (28, 23, {"tile_behaviors": tiles})):
            with self.subTest(method=method, extra=extra, named=bool(facts)):
                data = bytearray(rom_bytes())
                struct.pack_into("<4H", data, EVO_BASE + 128, method, 96, 2, extra)
                doc = pd.build(block({**FIXED, "tile_methods": [34]}, **facts),
                               bytes(data), {"id": "demo"})
                self.assertEqual(doc["species"][0]["evolution"][0],
                                 {"method": method, "parameter": 96, "target": 2, "extra": extra})

    def test_tile_names_require_evidence_and_byte_keys(self):
        self.assertIn("[pokedex.tile_behaviors] evidence", pd.check(
            block(FIXED, tile_behaviors={"names": {"23": "shallow water"}})))
        for names in ({"256": "water"}, {"unknown": "water"}, {"23": ""}, {"23": 23}, None):
            with self.subTest(names=names):
                self.assertIn("[pokedex.tile_behaviors] names", pd.check(
                    block(FIXED, tile_behaviors={"evidence": "synthetic terrain mapping", "names": names})))
        self.assertIn("[pokedex.evolution] tile_methods", pd.check(block({**FIXED, "tile_methods": "34"})))

    def test_missing_blocks_are_null_and_named(self):
        doc = pd.build(block(), rom_bytes(), {"id": "demo"})
        self.assertEqual(doc["missing"], ["evolution"])
        self.assertIsNone(doc["species"][0]["evolution"])
        self.assertEqual(doc["species"][0]["entry"], ["s0000340"])

    def test_a_block_that_cannot_drive_a_dataset_is_refused(self):
        self.assertEqual(pd.check(None), ["[pokedex] block"])
        self.assertEqual(pd.check({"schema": 1}), ["[pokedex.species]", "[pokedex] name_table"])
        partial = {"name_table": "species", "species": {"base": 0, "stride": 1}}
        problems = pd.check(partial)
        self.assertIn("[pokedex.species] count", problems)
        self.assertIn("[pokedex.species] fields", problems)
        species = {"base": 0, "stride": 1, "count": 1, "evidence": "x", "fields": {}}
        self.assertIn("[pokedex.dex.records]",
                      pd.check({"name_table": "s", "species": species, "dex": {}}))
        self.assertIn("[pokedex.evolution] pointer", pd.check({
            "name_table": "s", "species": species,
            "evolution": {"index": "pointer", "fields": {"method": 0}}}))
        with self.assertRaises(pd.PokedexError) as caught:
            pd.build({"name_table": "species"}, rom_bytes(), {"id": "demo"})
        self.assertIn("[pokedex.species]", str(caught.exception))

    def test_rebuild_is_byte_identical(self):
        from gen3tl import __version__
        from gen3tl.web.editordata import pinned_toolkit_version

        rom = rom_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            for split in (False, True):
                baseline = None
                for stamp in (__version__, f"{__version__}+123abcd", f"{__version__}+456def0.dirty"):
                    with self.subTest(split=split, stamp=stamp), pinned_toolkit_version(stamp):
                        out = Path(tmp) / str(split) / stamp
                        written = pd.write(out, pd.build(block(FIXED), rom, {"id": "demo"}), split=split)
                        content = {path.relative_to(out): path.read_bytes() for path in written}
                        if baseline is None:
                            baseline = content
                        self.assertEqual(content, baseline)
                        index = json.loads((out / "pokedex.json").read_text(encoding="utf-8"))
                        self.assertEqual(index["toolkit"], __version__)
                        self.assertEqual(index["count"], 2)
                        back = pd.read(out / "pokedex.json")
                        self.assertEqual(back["species"][0]["name"], "species:1")
                        self.assertEqual(pd.verify_doc(back, block(FIXED), "demo", back["rom"]["sha1"]), [])

    def test_toolkit_version_override_is_preserved(self):
        from gen3tl.web.editordata import pinned_toolkit_version

        with pinned_toolkit_version("0.0.0"):
            doc = pd.build(block(FIXED), rom_bytes(), {"id": "demo"})
        self.assertEqual(doc["toolkit"], "0.0.0")

    def test_learnsets_go_to_their_own_file_and_come_back(self):
        """pokedex.json names learnsets.json; read() inlines the rows; rebuilds match."""
        rows = [{"species": 1, "move": 33, "method": 0, "index": None, "level": 1, "offset": "0x000210"},
                {"species": 1, "move": 264, "method": 1, "index": 0, "level": None, "offset": "0x000300"}]
        doc = pd.build(block(FIXED), rom_bytes(), {"id": "demo"})
        doc["species"][0]["learnset"] = rows
        doc["species"][1]["learnset"] = []
        with tempfile.TemporaryDirectory() as tmp:
            one, two = Path(tmp) / "one", Path(tmp) / "two"
            a, b = pd.write(one, doc), pd.write(two, doc)
            self.assertEqual([p.name for p in a], ["pokedex.json", "learnsets.json"])
            for left, right in zip(a, b):
                self.assertEqual(left.read_bytes(), right.read_bytes(), left.name)
            index = json.loads(a[0].read_text(encoding="utf-8"))
            self.assertEqual(index["learnsets"], "learnsets.json")
            self.assertTrue(all("learnset" not in record for record in index["species"]))
            learnsets = json.loads(a[1].read_text(encoding="utf-8"))
            self.assertEqual(learnsets["format"], "gen3tl-pokedex-learnsets")
            self.assertEqual(learnsets["profile_block"], index["profile_block"])
            self.assertEqual(learnsets["columns"], ["method", "move", "level", "index", "offset"])
            self.assertEqual(learnsets["species"]["1"], [[0, 33, 1, None, "0x000210"], [1, 264, None, 0, "0x000300"]])
            self.assertEqual(learnsets["species"]["2"], [])
            back = pd.read(a[0])
            self.assertEqual(back["species"][0]["learnset"], rows)
            self.assertEqual(back["species"][1]["learnset"], [])
            self.assertNotIn("learnsets", back)
            # the on-disk form verifies as the inlined one does
            self.assertEqual(pd.verify_doc(index, block(FIXED), "demo", doc["rom"]["sha1"]),
                             pd.verify_doc(back, block(FIXED), "demo", doc["rom"]["sha1"]))
            # a split write keeps the learnsets beside the index
            split = pd.write(Path(tmp) / "split", doc, split=True)
            self.assertEqual(split[-1].name, "learnsets.json")
            self.assertEqual(pd.read(split[0])["species"][0]["learnset"], rows)
            # a learnsets file of another dataset is refused
            other = json.loads(a[1].read_text(encoding="utf-8"))
            other["profile_block"] = "0" * 64
            a[1].write_text(pd.dump(other), encoding="utf-8")
            with self.assertRaises(pd.PokedexError):
                pd.read(a[0])

    def test_flat_dataset_inlines_the_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            written = pd.write(Path(tmp), pd.build(block(FIXED), rom_bytes(), {"id": "demo"}))
            self.assertEqual(len(written), 1)
            index = json.loads(written[0].read_text(encoding="utf-8"))
            self.assertEqual((index["count"], len(index["species"])), (2, 2))

class CommandTest(unittest.TestCase):
    def test_verify_directory_and_legacy_index_preserve_the_dataset(self):
        data = rom_bytes()
        configured = block(FIXED)
        doc = pd.build(configured, data, {"id": "test"})
        prof = cli.Profile({"rom": {"sha1": doc["rom"]["sha1"]}, "pokedex": configured}, Path("test.toml"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for split in (False, True):
                folder = root / str(split)
                index = pd.write(folder, doc, split=split)[0]
                before = {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}
                for target in (str(folder), str(index), ""):
                    args = SimpleNamespace(profile="test", verify=target, out=str(folder), rom=None)
                    with self.subTest(split=split, target=target), \
                            mock.patch.object(cli.Profile, "by_name", return_value=prof), \
                            mock.patch.object(prof, "load_rom", side_effect=AssertionError("ROM read")), \
                            contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(cli.pokedex_data(args), 0)
                    self.assertEqual({p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}, before)
                stale = json.loads(index.read_text())
                stale["profile_block"] = "stale"
                index.write_text(json.dumps(stale), encoding="utf-8")
                with mock.patch.object(cli.Profile, "by_name", return_value=prof), \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.pokedex_data(args), 1)

    def test_map_command_adds_resolved_pickup_areas_and_reports_missing_aliases(self):
        configured = block(FIXED, encounters={"evidence": "synthetic"}, maps={
            "primary_tiles": 1, "primary_metatiles": 1, "primary_palettes": 7, "palettes": 13,
            "evidence": "synthetic maps",
        }, items={"count": 20, "sources": {"pickups": {"evidence": "synthetic pickups"}}})
        prof = cli.Profile({"rom": {"sha1": "0" * 40}, "pokedex": configured}, Path("test.toml"))
        args = SimpleNamespace(profile="test", verify=None, out="assets", rom=None)
        resolved = [{"runtime_map": "2.2", "map_header": "0x000200"},
                    {"map": "h0000300", "map_header": "0x000300"}]
        with mock.patch.object(cli.Profile, "by_name", return_value=prof), \
                mock.patch.object(prof, "load_rom", return_value=SimpleNamespace(data=b"")), \
                mock.patch("gen3tl.data.pokedexmaps.areas", return_value={"1.1": 0x100}), \
                mock.patch("gen3tl.data.pokedexdata.build_pickups", return_value=({}, resolved, [])), \
                mock.patch("gen3tl.data.pokedexmaps.build", return_value={"maps": []}) as build, \
                mock.patch("gen3tl.data.pokedexmaps.write", return_value=(Path("manifest"), 0)), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.pokedex_maps(args), 0)
        self.assertEqual(build.call_args.args[3], {"1.1": 0x100, "2.2": 0x200})
        self.assertIn("unrenderable h0000300 0x000300: no runtime map alias", output.getvalue())

    def test_asset_dry_runs_verify_the_selected_output_without_loading_roms(self):
        prof = cli.Profile({"rom": {"sha1": "0" * 40}, "pokedex": {}}, Path("test.toml"))
        for name in ("sprites", "icons", "maps"):
            handler = getattr(cli, f"pokedex_{name}")
            for verify in ("assets", ""):
                args = SimpleNamespace(profile="test", verify=verify, out="assets", rom=None)
                with self.subTest(name=name, verify=verify), \
                        mock.patch.object(cli.Profile, "by_name", return_value=prof), \
                        mock.patch.object(prof, "load_rom", side_effect=AssertionError("ROM read")), \
                        mock.patch(f"gen3tl.data.pokedex{name}.verify", return_value=[]) as check, \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(handler(args), 0)
                check.assert_called_once_with(Path("assets"), {}, prof.sha1)

    def test_a_profile_without_the_block_is_refused(self):
        """A profile without [pokedex] must exit 1, name the fields and write nothing."""
        class Bare:
            name = "bare-hack"
            path = Path("profiles/bare-hack.toml")

            def get(self, key, default=None):
                return None

            def load_rom(self, path=None):       # must never be reached
                raise AssertionError("the ROM was loaded before the block was checked")

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "never"
            args = mock.Mock(out=str(out), split=False, verify=None, profile="bare-hack", rom=None)
            with mock.patch.object(cli.Profile, "by_name", return_value=Bare()):
                self.assertEqual(cli.pokedex_data(args), 1)
            self.assertFalse(out.exists())
