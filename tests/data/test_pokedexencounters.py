from test_roots import TOOLKIT_ROOT
"""Wild encounter extraction and slot percentages."""

import copy
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.data import pokedexencounters as pe  # noqa: E402

DAY, NIGHT, MORNING, EVENING = 0x100, 0x200, 0x300, 0x320
LAND, LAND_MONS, FISH, FISH_MONS = 0x400, 0x500, 0x600, 0x700
GROUPS, BANK, HEADER, ENGINE, ENGINE_BANK = 0x1000, 0x1100, 0x1200, 0x1400, 0x1500


def rom_bytes() -> bytes:
    data = bytearray(0x2000)
    for at, fishing in ((DAY, True), (NIGHT, False)):
        struct.pack_into("<BBHIIII", data, at, 0, 2, 0, 0x08000000 + LAND, 0, 0,
                         0x08000000 + FISH if fishing else 0)
        data[at + 20] = 0xFF
    data[MORNING] = data[EVENING] = 0xFF
    struct.pack_into("<B3xI", data, LAND, 15, 0x08000000 + LAND_MONS)
    struct.pack_into("<B3xI", data, FISH, 10, 0x08000000 + FISH_MONS)
    for slot in range(12):
        struct.pack_into("<BBH", data, LAND_MONS + slot * 4, 5 + slot, 10 + slot, slot + 1)
    for slot in range(10):
        struct.pack_into("<BBH", data, FISH_MONS + slot * 4, 10, 15, 129)
    struct.pack_into("<I", data, GROUPS, 0x08000000 + BANK)
    struct.pack_into("<I", data, 0x1300, 0x08000100)
    struct.pack_into("<I", data, 0x1304, 0x08000100)
    for number in range(3):
        header = HEADER if number == 2 else 0x1300
        struct.pack_into("<I", data, BANK + number * 4, 0x08000000 + header)
        struct.pack_into("<I", data, ENGINE_BANK + number * 4, 0x08000000 + header)
    struct.pack_into("<I", data, HEADER, 0x08000100)
    struct.pack_into("<I", data, HEADER + 4, 0x08000100)
    data[HEADER + 20] = 0x58
    struct.pack_into("<I", data, ENGINE, 0x08000000 + ENGINE_BANK)
    return bytes(data)


def block() -> dict:
    return {
        "evidence": "synthetic headers and rate accessors",
        "headers": {
            "stride": 20,
            "day": {"base": DAY, "count": 1},
            "morning": {"base": MORNING, "count": 0},
            "evening": {"base": EVENING, "count": 0},
            "night": {"base": NIGHT, "count": 1},
        },
        "methods": {
            "land": {"pointer": 4, "slots": 12,
                     "rates": [20, 20, 10, 10, 10, 10, 5, 5, 4, 4, 1, 1]},
            "water": {"pointer": 8, "slots": 5, "rates": [60, 30, 5, 4, 1]},
            "rock_smash": {"pointer": 12, "slots": 5, "rates": [60, 30, 5, 4, 1]},
            "fishing": {"pointer": 16, "slots": 10},
        },
        "fishing": {
            "old": {"first": 0, "rates": [70, 30]},
            "good": {"first": 2, "rates": [60, 20, 20]},
            "super": {"first": 5, "rates": [40, 40, 15, 4, 1]},
        },
        "context": {"groups": GROUPS, "engine_groups": ENGINE, "engine_count": 1,
                    "evidence": "synthetic legacy and runtime map banks"},
        "unavailable": [
            {"kind": "gWildDataSwitch", "reason": "runtime-only-override"},
            {"kind": "swarm", "reason": "dynamic-hourly-species-replacement-not-a-static-header"},
        ],
    }


class EncounterTest(unittest.TestCase):
    def test_every_method_variant_and_rate_is_extracted(self):
        doc = pe.build(block(), rom_bytes(), 1294)
        records = doc["records"]
        self.assertEqual(len(records), 34)
        self.assertEqual(len([r for r in records if r["variant"] == "day"]), 22)
        self.assertEqual(len([r for r in records if r["variant"] == "night"]), 12)
        first = records[0]
        self.assertEqual((first["map"], first["runtime_map"], first["map_section"]),
                         ("0.2", "0.2", 0x58))
        self.assertEqual((first["map_header"], first["map_error"], first["percentage"]),
                         ("0x001200", None, 20))
        self.assertEqual([r["percentage"] for r in records[:12]], block()["methods"]["land"]["rates"])
        self.assertEqual((first["min_level"], first["max_level"], first["species"], first["encounter_rate"]),
                         (5, 10, 1, 15))
        self.assertEqual((first["variant"], first["variants"]), ("day", ["day", "morning", "evening"]))
        night = [r for r in records if r["variant"] == "night"]
        self.assertTrue(all(r["variants"] == ["night"] for r in night))
        fishing = [r for r in records if r["method"] == "fishing"]
        self.assertTrue(all(r["variant"] == "day" for r in fishing))
        self.assertTrue(all(r["variants"] == ["day", "morning", "evening", "night"] for r in fishing))
        self.assertEqual(len(fishing), 10)
        self.assertEqual([r["percentage"] for r in fishing], [70, 30, 60, 20, 20, 40, 40, 15, 4, 1])
        self.assertEqual([r["rod"] for r in fishing],
                         ["old", "old", "good", "good", "good", "super", "super", "super", "super", "super"])
        self.assertEqual(fishing[0]["encounter_rate"], 10)
        self.assertEqual(doc["unavailable"], block()["unavailable"])

    def test_rebuild_is_identical(self):
        self.assertEqual(pe.build(block(), rom_bytes(), 1294), pe.build(block(), rom_bytes(), 1294))

    def test_runtime_switch_and_hourly_swarm_stay_unavailable(self):
        doc = pe.build(block(), rom_bytes(), 1294)
        self.assertEqual(doc["unavailable"], [
            {"kind": "gWildDataSwitch", "reason": "runtime-only-override"},
            {"kind": "swarm", "reason": "dynamic-hourly-species-replacement-not-a-static-header"},
        ])

    def test_runtime_bank_filler_does_not_hide_later_maps(self):
        data = bytearray(rom_bytes())
        struct.pack_into("<I", data, ENGINE_BANK + 4, 0xF7F7F7F7)
        headers = pe._runtime_headers(bytes(data), block()["context"])
        self.assertEqual(list(headers), ["0.0", "0.2"])
        doc = pe.build(block(), bytes(data), 1294)
        self.assertEqual((doc["records"][0]["map"], doc["records"][0]["map_section"]), ("0.2", 0x58))
        self.assertNotIn({"kind": "map", "id": "0.2", "reason": "runtime-map-unindexed"}, doc["unavailable"])

    def test_an_invalid_runtime_header_is_unavailable_not_a_playable_map(self):
        data = bytearray(rom_bytes())
        struct.pack_into("<BBHIIII", data, DAY, 0, 1, 0, 0x08000000 + LAND, 0, 0, 0)
        struct.pack_into("<I", data, ENGINE_BANK + 4, 0xF7F7F7F7)
        doc = pe.build(block(), bytes(data), 1294)
        self.assertIn({"kind": "map", "id": "0.1", "reason": "runtime-map-invalid"}, doc["unavailable"])
        self.assertIsNone(doc["records"][0]["map"])
        self.assertEqual(doc["records"][0]["map_error"], "runtime-map-invalid")

    def test_an_unindexed_runtime_map_is_explicit(self):
        data = bytearray(rom_bytes())
        struct.pack_into("<BBHIIII", data, 0x180, 9, 9, 0, 0x08000000 + LAND, 0, 0, 0)
        data[0x180 + 20] = 0xFF
        b = block()
        b["headers"]["day"]["base"] = 0x180
        doc = pe.build(b, bytes(data), 1294)
        self.assertIn({"kind": "map", "id": "9.9", "reason": "runtime-map-unindexed"}, doc["unavailable"])
        self.assertIsNone(doc["records"][0]["map"])
        self.assertEqual(doc["records"][0]["map_error"], "runtime-map-unindexed")

    def test_check_names_the_missing_evidence_and_rates(self):
        b = block()
        del b["evidence"]
        b["methods"]["land"]["rates"] = []
        del b["fishing"]["good"]
        problems = pe.check(b)
        self.assertIn("evidence", problems)
        self.assertIn("methods land rates", problems)
        self.assertIn("fishing good", problems)

    def test_invalid_species_and_early_terminators_are_refused(self):
        with self.assertRaisesRegex(ValueError, "invalid species"):
            pe.build(block(), rom_bytes(), 1)
        data = bytearray(rom_bytes())
        data[DAY] = 0xFF
        with self.assertRaisesRegex(ValueError, "ends before"):
            pe.build(block(), bytes(data), 1294)


def trigger_fixture():
    data = bytearray(rom_bytes())
    struct.pack_into("<I", data, DAY + 8, pe.ROM_BASE + LAND)
    struct.pack_into("<I", data, HEADER, pe.ROM_BASE + 0x1600)
    struct.pack_into("<6I", data, 0x1600, 2, 1, pe.ROM_BASE + 0x1700,
                     pe.ROM_BASE + 0x1700, pe.ROM_BASE + 0x1800, pe.ROM_BASE + 0x1820)
    struct.pack_into("<2H", data, 0x1700, 0, 2)
    struct.pack_into("<I", data, 0x1814, pe.ROM_BASE + 0x1900)
    struct.pack_into("<I", data, 0x1834, pe.ROM_BASE + 0x1940)
    struct.pack_into("<I", data, 0x1900, 2 << 24 | 8)
    struct.pack_into("<I", data, 0x1940, 2 << 24 | 21)
    config = block()
    config["trigger_tiles"] = {"primary_metatiles": 2, "water_walk_behaviors": [8],
                               "water_surf_behaviors": [21], "required_methods": ["water"],
                               "evidence": "Synthetic attributes and movement rules."}
    config["method_labels"] = [{
        "maps": ["0.2"], "method": "water", "labels": ["Meadow", "Surfing"],
        "triggers": [{"encounter": 2, "behaviors": [8], "movement": "walk"},
                     {"encounter": 2, "behaviors": [21], "movement": "surf"}],
        "evidence": "Synthetic walking and surfing tiles share a table.",
    }]
    return config, data


class TriggerTest(unittest.TestCase):
    def test_primary_and_secondary_tiles_label_one_table_without_changing_slots(self):
        config, data = trigger_fixture()
        rows = pe.build(config, data, 1294)["records"]
        water = [row for row in rows if row["method"] == "water"]
        self.assertEqual(len(water), 5)
        self.assertTrue(all(row["method_labels"] == ["Meadow", "Surfing"] for row in water))
        self.assertEqual(sum(row["percentage"] for row in water), 100)
        self.assertTrue(all(row["variants"] == list(pe.VARIANTS) for row in water))
        self.assertTrue(all("method_labels" not in row for row in rows if row["method"] != "water"))
        original = pe.build(block(), rom_bytes(), 1294)["records"]
        self.assertEqual([row for row in rows if row["method"] != "water"], original)

    def test_land_can_name_a_walking_trigger(self):
        config, data = trigger_fixture()
        struct.pack_into("<I", data, 0x1900, 1 << 24)
        config["method_labels"][0].update(method="land", labels=["Meadow"], triggers=[
            {"encounter": 1, "behaviors": [0], "movement": "walk"}])
        config["trigger_tiles"]["required_methods"] = []
        rows = pe.build(config, data, 1294)["records"]
        self.assertTrue(all(row["method_labels"] == ["Meadow"] for row in rows if row["method"] == "land"))

    def test_labels_require_evidence_unique_tables_and_valid_triggers(self):
        valid, _ = trigger_fixture()
        for value in (None, {}, "invalid"):
            self.assertEqual(pe.check(dict(block(), method_labels=value)), ["method_labels"])
        for field, value in (("maps", []), ("maps", ["bad"]), ("method", "fishing"),
                             ("labels", []), ("labels", [None]), ("evidence", ""),
                             ("triggers", [])):
            with self.subTest(field=field, value=value):
                config = copy.deepcopy(valid)
                config["method_labels"][0][field] = value
                self.assertTrue(pe.check(config))
        for field, value in (("encounter", 1), ("movement", "surf"), ("behaviors", [21]),
                             ("behaviors", [512]), ("behaviors", [True]), ("behaviors", [])):
            with self.subTest(field=field, value=value):
                config = copy.deepcopy(valid)
                config["method_labels"][0]["triggers"][0][field] = value
                self.assertTrue(pe.check(config))
        valid["method_labels"].append(copy.deepcopy(valid["method_labels"][0]))
        self.assertTrue(any("duplicate" in problem for problem in pe.check(valid)))

    def test_a_profile_can_disable_water_walking(self):
        config, data = trigger_fixture()
        config["trigger_tiles"]["water_walk_behaviors"] = []
        self.assertTrue(any("movement behaviors" in problem for problem in pe.check(config)))
        config["method_labels"][0]["labels"].pop(0)
        config["method_labels"][0]["triggers"].pop(0)
        self.assertEqual(pe.check(config), [])
        pe.build(config, data, 1294)

    def test_wrong_flags_and_land_priority_cannot_prove_water_triggers(self):
        for encounter in (0, 1, 3):
            with self.subTest(encounter=encounter):
                config, data = trigger_fixture()
                struct.pack_into("<I", data, 0x1900, encounter << 24 | 8)
                with self.assertRaisesRegex(ValueError, "trigger is absent"):
                    pe.build(config, data, 1294)

    def test_shared_table_cannot_silently_lose_a_trigger(self):
        config, data = trigger_fixture()
        config["method_labels"][0]["labels"].pop()
        config["method_labels"][0]["triggers"].pop()
        with self.assertRaisesRegex(ValueError, "undeclared trigger"):
            pe.build(config, data, 1294)

    def test_missing_declaration_or_table_is_refused(self):
        config, data = trigger_fixture()
        config["method_labels"] = []
        with self.assertRaisesRegex(ValueError, "missing method label"):
            pe.build(config, data, 1294)
        config, data = trigger_fixture()
        config["method_labels"][0]["maps"].append("0.1")
        with self.assertRaisesRegex(ValueError, "declared table is absent"):
            pe.build(config, data, 1294)

    def test_invalid_attribute_pointer_or_truncated_table_is_refused(self):
        for pointer, error in ((0, "invalid metatile attribute pointer"),
                               (pe.ROM_BASE + 0x1FFF, "attributes exceed ROM")):
            config, data = trigger_fixture()
            struct.pack_into("<I", data, 0x1834, pointer)
            with self.assertRaisesRegex(ValueError, error):
                pe.build(config, data, 1294)


if __name__ == "__main__":
    unittest.main()
