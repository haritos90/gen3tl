from test_roots import TOOLKIT_ROOT
"""Pokedextrainers records checks and fixtures."""

import sys
from pathlib import Path
import struct
import copy
import unittest

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.data import pokedexdata as pd
from gen3tl.data import pokedextrainers as pt
from pokedextrainers_support import BASE, PARTY, SCRIPTS, block, map_block, map_profile, map_rom, rom_bytes


class TrainerTest(unittest.TestCase):
    def test_difficulty_presentation_preserves_engine_values_and_validates_the_menu(self):
        difficulty = {"variable": 0x4000, "settings": ["standard", "relaxed"],
                      "selections": [], "evidence": "synthetic selection",
                      "presentation": {"default": 0, "order": [1, 0],
                                       "labels": ["Standard", "Relaxed"],
                                       "evidence": "synthetic initializer"}}
        self.assertEqual(pt.check_difficulty(difficulty, 6), [])
        for key, value in (("default", 2), ("default", True), ("order", [1, 1]),
                           ("order", [1, False]), ("labels", ["Standard"]),
                           ("evidence", "")):
            with self.subTest(key=key, value=value):
                invalid = copy.deepcopy(difficulty)
                invalid["presentation"][key] = value
                self.assertTrue(pt.check_difficulty(invalid, 6))
        trainers = map_block()
        trainers["difficulty"] = difficulty
        b = {"name_table": "species", "trainers": trainers,
             "species": {"base": 0, "stride": 1, "count": 1294, "first": 1,
                         "evidence": "synthetic species slots", "fields": {}}}
        doc = pd.build(b, map_rom(), map_profile())
        self.assertEqual(doc["trainer_difficulty"], {
            "settings": ["standard", "relaxed"], "default": 0, "order": [1, 0],
            "labels": ["Standard", "Relaxed"]})
        self.assertEqual(pd.verify_doc(doc, b, "demo", doc["rom"]["sha1"]), [])
        doc["trainer_difficulty"]["default"] = 1
        self.assertIn("trainer_difficulty: regenerate the dataset with gen3tl data pokedex",
                      pd.verify_doc(doc, b, "demo", doc["rom"]["sha1"]))
        del trainers["difficulty"]["presentation"]
        self.assertIsNone(pt.difficulty_presentation(trainers))

    def test_picture_indexes_select_art_independently_of_trainer_class(self):
        b = block()
        b["fields"]["picture"] = 3
        b["pictures"] = {"side": 64, "evidence": "synthetic picture records",
                         "front": {"base": 0, "stride": 8, "count": 3},
                         "palettes": {"base": 32, "stride": 8, "count": 3}}
        data = bytearray(rom_bytes())
        data[BASE + 3], data[BASE + 40 + 3] = 2, 2
        rows = pt.build(b, data, 1294, 729, 923)
        self.assertNotEqual(rows[0]["class"], rows[1]["class"])
        self.assertEqual((rows[0]["picture"], rows[1]["picture"]), (2, 2))
        self.assertEqual((rows[0]["sprite"], rows[1]["sprite"]), ("trainers/2.png", "trainers/2.png"))
        self.assertEqual(rows[4]["sprite"], "trainers/0.png")
        data[BASE + 3] = 3
        with self.assertRaisesRegex(ValueError, "trainer 0: invalid picture 3"):
            pt.build(b, data, 1294, 729, 923)
        del b["fields"]["picture"]
        self.assertIn("fields picture", " ".join(pt.check(b)))

    def test_every_party_variant_and_empty_records(self):
        trainers = pt.build(block(), rom_bytes(), 1294, 729, 923)
        self.assertEqual(len(trainers), 6)
        self.assertEqual(sum(len(t["party"]) for t in trainers), 6)
        first = trainers[0]
        self.assertEqual((first["name"], first["class"], first["party_flags"]),
                         ("trainer_names:0", "trainer_classes:1", 0))
        self.assertEqual((first["class_id"], first["ai_flags"]), (1, 0))
        self.assertEqual((trainers[1]["class_id"], trainers[1]["ai_flags"]), (2, 0x1020304))
        self.assertEqual(first["party"][0],
                         {"index": 0, "offset": "0x000400", "species": 19, "level": 10,
                          "iv": 0, "item": None, "moves": None})
        self.assertEqual(trainers[1]["class"], "s0000900")
        self.assertEqual(trainers[1]["party"][0]["moves"], [644, 675, 157, 637])
        self.assertIsNone(trainers[1]["party"][0]["item"])
        self.assertEqual(trainers[2]["party"][0]["item"], 142)
        self.assertIsNone(trainers[2]["party"][0]["moves"])
        self.assertEqual(trainers[3]["party"][0]["item"], 495)
        self.assertEqual(trainers[3]["party"][0]["moves"], [89, 350, 433, 68])
        self.assertEqual(trainers[4]["party"], [])
        self.assertIsNone(trainers[4]["party_offset"])
        self.assertEqual(trainers[5]["party"][0]["species"], 0)
        self.assertEqual(trainers[5]["party"][0]["level"], 0)

    def test_average_level_uses_real_members_without_rounding(self):
        data = bytearray(rom_bytes())
        struct.pack_into("<H", data, PARTY + 8 + 2, 11)
        trainers = pt.build(block(), data, 1294, 729, 923)
        self.assertEqual([row["average_level"] for row in trainers],
                         [10.5, 41, 61, 62, None, None])
        data[PARTY + 8:PARTY + 16] = bytes(8)
        trainers = pt.build(block(), data, 1294, 729, 923)
        self.assertEqual(trainers[0]["party_size"], 2)
        self.assertEqual(trainers[0]["average_level"], 10)

    def test_difficulty_selections_mark_the_records_each_setting_uses(self):
        b = block()
        b["difficulty"] = {
            "variable": 0x50DF, "settings": ["difficult", "vanilla", "expert", "insane"],
            "evidence": "synthetic stub", "selections": [
                {"script": 0x100, "stub": 0x200, "selector": 1, "records": [0, 1, 2, 3]},
                {"script": 0x110, "records": [4, 4, 4, 5]},
            ]}
        trainers = pt.build(b, rom_bytes(), 1294, 729, 923)
        self.assertEqual([t["difficulty"] for t in trainers],
                         [["difficult"], ["vanilla"], ["expert"], ["insane"], ["difficult", "vanilla", "expert"], ["insane"]])
        self.assertEqual([t["encounters"] for t in trainers], [[0], [0], [0], [0], [1], [1]])
        self.assertTrue(all("base" not in t for t in trainers))
        self.assertEqual([t["average_level"] for t in trainers], [10, 41, 61, 62, None, None])
        plain = pt.build(block(), rom_bytes(), 1294, 729, 923)
        self.assertEqual({t["difficulty"] for t in plain}, {None})
        self.assertEqual({tuple(t["encounters"]) for t in plain}, {()})
        self.assertEqual(pt.check(b), [])
        b["difficulty"]["selections"][0]["native"] = "assumed"
        self.assertIn("difficulty selections[0]: native status", pt.check(b))
        del b["difficulty"]["selections"][0]["native"]
        b["difficulty"]["selections"].append({"script": 0x120, "records": [0, 1, 2]})
        b["difficulty"]["selections"].append({"records": [0, 1, 2, 9]})
        del b["difficulty"]["evidence"]
        self.assertEqual(pt.check(b), ["difficulty evidence", "difficulty selections[2]: one record per setting",
                                       "difficulty selections[3]: a record id out of range",
                                       "difficulty selections[3]: script"])

    def test_rebuild_is_identical(self):
        self.assertEqual(pt.build(block(), rom_bytes(), 1294, 729, 923),
                         pt.build(block(), rom_bytes(), 1294, 729, 923))
        args = (map_block(), map_rom(), 1294, 729, 923, map_profile())
        self.assertEqual(pt.build(*args), pt.build(*args))

    def test_kinds_use_classes_selections_and_all_maps_with_role_precedence(self):
        b = map_block()
        b["kinds"] = {"league_classes": [2], "story_classes": [3], "story_records": [4],
                      "paired_classes": [4, 6], "special_maps": ["0.0"],
                      "evidence": "synthetic trainer roles"}
        trainers = pt.build(b, map_rom(), 1294, 729, 923, map_profile())
        self.assertEqual([t["kind"] for t in trainers],
                         ["world", "league", "story", "special", "story", "paired"])
        self.assertEqual(trainers[1]["class"], "s0000900")
        b["difficulty"] = {"variable": 0x4000, "settings": ["normal", "hard"],
                           "selections": [{"script": 0x100, "records": [0, 1]}],
                           "evidence": "synthetic difficulty selection"}
        trainers = pt.build(b, map_rom(), 1294, 729, 923, map_profile())
        self.assertEqual([t["kind"] for t in trainers[:2]], ["story", "league"])
        b["kinds"]["partner_records"] = [0]
        trainers = pt.build(b, map_rom(), 1294, 729, 923, map_profile())
        self.assertEqual(trainers[0]["kind"], "partners")
        b["kinds"] = {"evidence": "synthetic training rooms"}
        trainers = pt.build(b, map_rom(), 1294, 729, 923, map_profile())
        self.assertEqual(trainers[3]["kind"], "world")
        del b["difficulty"]
        trainers = pt.build(b, map_rom(), 1294, 729, 923, map_profile())
        self.assertEqual(trainers[1]["kind"], "special")
        self.assertEqual(trainers[1]["map"], ["0.0", "0.1"])

    def test_kind_facts_require_evidence_and_valid_class_record_and_map_ids(self):
        b = block()
        b["kinds"] = {"league_classes": [107], "story_classes": "2", "story_records": [6],
                      "partner_records": [-1], "special_maps": ["h1234"]}
        self.assertEqual(pt.check(b), ["kinds evidence", "kinds league_classes: a list of valid ids",
                                      "kinds story_classes: a list of valid ids", "kinds story_records: a list of valid ids",
                                      "kinds partner_records: a list of valid ids",
                                      "kinds special_maps: a list of runtime map ids",
                                      "kinds special_maps: needs [pokedex.trainers.maps]"])
        b = map_block()
        b["kinds"] = {"special_maps": ["1.0"], "evidence": "synthetic facility"}
        with self.assertRaisesRegex(ValueError, "kinds special_maps 1.0: no trainerbattle"):
            pt.build(b, map_rom(), 1294, 729, 923, map_profile())

    def test_map_scripts_keep_every_runtime_map_with_matching_sections(self):
        trainers = pt.build(map_block(), map_rom(), 1294, 729, 923, map_profile())
        self.assertEqual(pt.battle_maps(map_rom(), map_profile()), {1: {"0.1": 0x5A, "0.0": 0x5E}, 3: {"0.0": 0x5E}})
        self.assertEqual([(t["map"], t["map_section"], t["ev_training"]) for t in trainers], [
            ([], [], False), (["0.0", "0.1"], [0x5E, 0x5A], True), ([], [], False), (["0.0"], [0x5E], False),
            ([], [], False), ([], [], False)])
        # without an engine table the bank walk names the maps
        plain = pt.build(map_block(("0.0",)), map_rom(), 1294, 729, 923, map_profile(engine=False))
        self.assertEqual([(t["map"], t["map_section"], t["ev_training"]) for t in plain][1:4], [
            (["0.0", "0.1"], [0x5A, 0x5E], True), ([], [], False), (["0.1"], [0x5E], False)])
        # an undeclared reader leaves every record without a map
        self.assertEqual([(t["map"], t["map_section"], t["ev_training"])
                          for t in pt.build(block(), map_rom(), 1294, 729, 923)], [([], [], False)] * 6)

    def test_training_rooms_need_the_map_reader_and_a_battle(self):
        self.assertEqual(pt.check(map_block()), [])
        b = map_block()
        del b["maps"]
        b["ev_training"] = {"maps": ["7.3", "h0752338"]}
        self.assertEqual(pt.check(b), ["ev_training: needs [pokedex.trainers.maps]", "ev_training evidence",
                                       "ev_training maps: a list of runtime map ids"])
        self.assertIn("maps evidence", pt.check({**block(), "maps": {}}))
        with self.assertRaisesRegex(ValueError, "ev_training maps 0.5, 2.0: no trainerbattle"):
            pt.build(map_block(("2.0", "0.1", "0.5")), map_rom(), 1294, 729, 923, map_profile())
        with self.assertRaisesRegex(ValueError, "no \\[maps\\] block"):
            pt.build(map_block(), map_rom(), 1294, 729, 923, {"id": "demo"})

    def test_shared_selection_scripts_reached_by_calls_and_branches_keep_all_maps(self):
        data = bytearray(map_rom())
        selection = SCRIPTS + 0x80
        struct.pack_into("<BHHB", data, selection, 0x16, 0x8000, 1, 0x03)
        for offset, prefix in ((0x20, b"\x04"), (0x40, b"\x06\x01")):
            at = SCRIPTS + offset
            battle = bytes(data[at:at + 11])
            call = prefix + struct.pack("<I", 0x08000000 + selection)
            data[at:at + len(call) + len(battle)] = call + battle
        b = map_block()
        b["difficulty"] = {"variable": 0x50DF, "settings": ["normal", "hard"],
                           "evidence": "synthetic shared selection", "selections": [
                               {"script": selection, "records": [0, 2]},
                               {"script": selection, "records": [4, 5]},
                           ]}
        trainers = pt.build(b, bytes(data), 1294, 729, 923, map_profile())
        for index in (0, 1, 2, 4, 5):
            self.assertEqual(trainers[index]["map"], ["0.0", "0.1"])
            self.assertEqual(trainers[index]["map_section"], [0x5E, 0x5A])
        self.assertEqual(trainers[3]["map"], ["0.0"])

    def test_map_order_is_numeric(self):
        trainers = [{"id": 1}]
        pt.annotate_maps(trainers, {1: {"10.1": 90, "2.11": 92, "2.2": 91}}, None)
        self.assertEqual(trainers[0]["map"], ["2.2", "2.11", "10.1"])
        self.assertEqual(trainers[0]["map_section"], [91, 92, 90])

    def test_the_dataset_carries_the_maps_and_verifies_them(self):
        b = {"name_table": "species", "trainers": map_block(),
             "species": {"base": 0, "stride": 1, "count": 1294, "first": 1,
                         "evidence": "synthetic species slots", "fields": {}}}
        doc = pd.build(b, map_rom(), map_profile())
        self.assertEqual(doc["trainers"][1]["map"], ["0.0", "0.1"])
        self.assertEqual(doc["provenance"]["trainer_ev_training"]["evidence"], "synthetic rebattle room")
        self.assertEqual(pd.verify_doc(doc, b, "demo", doc["rom"]["sha1"]), [])
        for maps, sections in (("0.0", 94), (None, None), (["0.0"], []),
                               (["0.0"], ["94"]), (["0.0"], [256]),
                               (["h1234"], [94]), (["0.0", "0.0"], [94, 94]),
                               (["0.1", "0.0"], [90, 94])):
            with self.subTest(maps=maps, sections=sections):
                doc["trainers"][1].update(map=maps, map_section=sections)
                self.assertIn("trainer maps: regenerate the dataset with gen3tl data pokedex",
                              pd.verify_doc(doc, b, "demo", doc["rom"]["sha1"]))
        doc["trainers"][1].update(map=["0.0", "0.1"], map_section=[94, 90])
        del doc["trainers"][3]["ev_training"]
        self.assertIn("trainer maps: regenerate the dataset with gen3tl data pokedex",
                      pd.verify_doc(doc, b, "demo", doc["rom"]["sha1"]))

    def test_the_dataset_carries_one_valid_kind_and_its_evidence(self):
        trainers = map_block()
        trainers["kinds"] = {"league_classes": [2], "evidence": "synthetic trainer roles"}
        b = {"name_table": "species", "trainers": trainers,
             "species": {"base": 0, "stride": 1, "count": 1294, "first": 1,
                         "evidence": "synthetic species slots", "fields": {}}}
        doc = pd.build(b, map_rom(), map_profile())
        self.assertEqual(doc["provenance"]["trainer_kinds"]["evidence"], "synthetic trainer roles")
        self.assertTrue(all(row["kind"] in pt.KINDS for row in doc["trainers"]))
        self.assertEqual(pd.verify_doc(doc, b, "demo", doc["rom"]["sha1"]), [])
        for invalid in (None, "unknown", ["league", "story"]):
            doc["trainers"][1]["kind"] = invalid
            self.assertIn("trainer kinds: regenerate the dataset with gen3tl data pokedex",
                          pd.verify_doc(doc, b, "demo", doc["rom"]["sha1"]))

    def test_check_names_the_missing_layouts(self):
        b = block()
        del b["fields"]["party"]
        del b["party"]["moves_with_item"]
        self.assertIn("fields party", pt.check(b))
        self.assertIn("party moves_with_item", pt.check(b))

    def test_invalid_flags_pointers_and_placeholders_are_refused(self):
        data = bytearray(rom_bytes())
        data[BASE] = 4
        with self.assertRaisesRegex(ValueError, "unknown party flags"):
            pt.build(block(), bytes(data), 1294, 729, 923)
        data = bytearray(rom_bytes())
        struct.pack_into("<I", data, BASE + 36, 0)
        with self.assertRaisesRegex(ValueError, "invalid party pointer"):
            pt.build(block(), bytes(data), 1294, 729, 923)
        data = bytearray(rom_bytes())
        struct.pack_into("<H", data, PARTY + 0x40, 1)
        with self.assertRaisesRegex(ValueError, "invalid empty member"):
            pt.build(block(), bytes(data), 1294, 729, 923)
