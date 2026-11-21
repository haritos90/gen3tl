from test_roots import TOOLKIT_ROOT
"""Pokedextrainers encounters checks and fixtures."""

import sys
from pathlib import Path
import copy
import hashlib
import unittest

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.data import pokedexdata as pd
from gen3tl.data import pokedextrainers as pt
from pokedextrainers_support import SCRIPTS, block, map_block, map_profile, map_rom


class MapResolutionTest(unittest.TestCase):
    def setUp(self):
        self.data = map_rom()
        self.block = map_block()
        self.rule = {"id": "variable-opponent", "script": SCRIPTS + 0x20, "records": [2, 5],
                     "evidence": "Synthetic variable selector and battle operand.", "native": "unconfirmed",
                     "rom": [{"offset": SCRIPTS + 0x20, "size": 11,
                              "sha256": hashlib.sha256(self.data[SCRIPTS + 0x20:SCRIPTS + 0x2B]).hexdigest()}]}
        self.block["maps"].update(resolutions=[self.rule], searched="Synthetic selector and battle scripts.")

    def test_variable_records_inherit_reached_runtime_maps(self):
        rows = pt.build(self.block, self.data, 1294, 729, 923, map_profile())
        for record in self.rule["records"]:
            self.assertEqual(rows[record]["map"], ["0.1"])
            self.assertEqual(rows[record]["map_section"], [90])
        self.assertEqual(pt.map_summary(rows, self.block)["unknown"], [0, 4])

    def test_changed_native_or_script_proof_fails(self):
        data = bytearray(self.data)
        data[SCRIPTS + 0x22] ^= 1
        with self.assertRaisesRegex(ValueError, "trainer maps: ROM span differs"):
            pt.build(self.block, bytes(data), 1294, 729, 923, map_profile())

    def test_unreachable_anchor_fails(self):
        self.rule["script"] = 0xE10
        with self.assertRaisesRegex(ValueError, "trainer maps: unreachable resolution"):
            pt.build(self.block, self.data, 1294, 729, 923, map_profile())

    def test_resolution_requires_records_evidence_and_proof(self):
        for key, value in (("records", [6]), ("records", [2, 2]), ("records", []),
                           ("script", -1), ("rom", []), ("rom", [{}]),
                           ("evidence", ""), ("native", "assumed")):
            with self.subTest(key=key, value=value):
                maps = {**self.block["maps"], "resolutions": [{**self.rule, key: value}]}
                self.assertTrue(pt.check_map_resolutions(maps, 6))
        self.assertTrue(pt.check_map_resolutions({**self.block["maps"], "searched": ""}, 6))
        self.assertTrue(pt.check_map_resolutions({**self.block["maps"], "resolutions": [self.rule] * 2}, 6))

    def test_dataset_checks_map_inventory_and_evidence(self):
        b = {"name_table": "species", "trainers": self.block,
             "species": {"base": 0, "stride": 1, "count": 1294, "first": 1,
                         "evidence": "synthetic species slots", "fields": {}}}
        doc = pd.build(b, self.data, map_profile())
        self.assertEqual(doc["trainer_maps"], pt.map_summary(doc["trainers"], self.block))
        for key, value in (("unknown", []), ("resolutions", []), ("searched", "")):
            broken = copy.deepcopy(doc)
            broken["trainer_maps"][key] = value
            self.assertIn("trainer map evidence: regenerate the dataset with gen3tl data pokedex",
                          pd.verify_doc(broken, b, "demo", doc["rom"]["sha1"]))


class EncounterTest(unittest.TestCase):
    """Selections, declared groups and literal battle operands become encounters."""

    def trainers(self, levels):
        return [{"id": index, "average_level": level} for index, level in enumerate(levels)]

    def participant_block(self):
        return {"count": 6, "difficulty": {"settings": ["normal", "hard"],
            "starter": {"species": [1, 2]}, "selections": [{"script": 0x100, "records": [0, 1]}]},
            "participants": [{"script": 0x100, "commands": [0x120, 0x140], "evidence": "script and loader bytes",
                "rom": [{"offset": 0, "size": 4, "sha256": hashlib.sha256(b"test").hexdigest()}],
                "selectors": [{"role": "second_opponent", "settings": ["normal"], "records": [2]},
                    {"role": "partner", "starter": 1, "records": [3, 4]},
                    {"role": "partner", "starter": 2, "records": [5, 5]}]}]}

    def test_participants_share_the_start_with_conditional_roles(self):
        block = self.participant_block()
        trainers = self.trainers([20] * 6)
        battles = pt.encounters(trainers, block["difficulty"], [],
            {0x120: [("opponent", 2), ("partner", 3)], 0x140: [("partner", 4)]}, block["participants"])
        self.assertEqual(len(battles), 1)
        self.assertEqual(battles[0]["variants"], [
            {"record": 0, "role": "opponent", "settings": ["normal"]},
            {"record": 1, "role": "opponent", "settings": ["hard"]},
            {"record": 2, "role": "second_opponent", "settings": ["normal"]},
            {"record": 3, "role": "partner", "settings": ["normal"], "starter": 1},
            {"record": 4, "role": "partner", "settings": ["hard"], "starter": 1},
            {"record": 5, "role": "partner", "settings": None, "starter": 2}])
        self.assertEqual([row["encounters"] for row in trainers], [[0]] * 6)
        self.assertEqual(trainers[2]["difficulty"], ["normal"])
        self.assertEqual(pt.verify_encounters(battles, trainers, block), [])
        for change in ("settings", "role", "record"):
            broken = copy.deepcopy(battles)
            broken[0]["variants"][2][change] = {"settings": None, "role": "partner", "record": 999}[change]
            self.assertTrue(pt.verify_encounters(broken, trainers, block), change)
        trainers[2]["encounters"] = []
        self.assertTrue(pt.verify_encounters(battles, trainers, block))

    def test_participant_declarations_reject_missing_or_overlapping_evidence(self):
        block = self.participant_block()
        self.assertEqual(pt.check_participants(block), [])
        for key, value in (("script", 123), ("commands", []), ("rom", []), ("evidence", ""),
                           ("native", "assumed"),
                           ("selectors", [{"role": "opponent", "records": [1, 1]}])):
            broken = copy.deepcopy(block)
            broken["participants"][0][key] = value
            self.assertTrue(pt.check_participants(broken), key)
        for selector in ({"role": "partner", "records": [1, 1]},
                         {"role": "partner", "records": [1, 1], "starter": 1},
                         {"role": "partner", "records": [1, 1], "starter": 3},
                         {"role": "partner", "records": [99, 1], "starter": 2}):
            broken = copy.deepcopy(block)
            broken["participants"][0]["selectors"].append(selector)
            self.assertTrue(pt.check_participants(broken), selector)

    def test_participant_byte_proofs_are_enforced(self):
        groups = self.participant_block()["participants"]
        pt.validate_participants(groups, b"test")
        for data in (b"best", b"tes"):
            with self.assertRaisesRegex(ValueError, "ROM span differs"):
                pt.validate_participants(groups, data)

    def test_starters_alternatives_stages_and_roles(self):
        trainers = self.trainers([30, 31, 40, 41, 10, 11, 12, 50, 60, 20, 5, 70])
        difficulty = {"settings": ["normal", "hard"], "selections": [
            {"script": 0x100, "starter": 1, "records": [0, 2]},
            {"script": 0x100, "starter": 2, "records": [1, 3]},
            {"script": 0x200, "records": [7, 7], "alternatives": [{"setting": "hard", "record": 8, "level_above": 39}]},
        ]}
        groups = [{"script": 0x300, "starter": [4, 5, 6], "commands": [0x500, 0x510], "evidence": "branch"},
                  {"script": 0x400, "stages": [{"record": 9}, {"record": 11, "flag": 0x821, "condition": "After"}],
                   "evidence": "stages"}]
        commands = {0x500: [("opponent", 4)], 0x510: [("opponent", 5)], 0x520: [("opponent", 0)],
                    0x530: [("opponent", 10), ("partner", 9)], 0x540: [("partner", 3), ("opponent", 10)]}
        battles = pt.encounters(trainers, difficulty, groups, commands)
        self.assertEqual([[(v["record"], v["role"], v["settings"], v.get("starter"), v.get("level_above"),
                            v.get("condition")) for v in row["variants"]] for row in battles], [
            [(10, "opponent", None, None, None, None)],
            [(4, "opponent", None, 1, None, None), (5, "opponent", None, 2, None, None), (6, "opponent", None, 3, None, None)],
            [(9, "opponent", None, None, None, None)],
            [(0, "opponent", ["normal"], 1, None, None), (2, "opponent", ["hard"], 1, None, None),
             (1, "opponent", ["normal"], 2, None, None), (3, "opponent", ["hard"], 2, None, None)],
            [(0, "opponent", None, None, None, None)],
            [(7, "opponent", None, None, None, None), (8, "opponent", ["hard"], None, 39, None)],
            [(11, "opponent", None, None, None, "After")],
        ])
        self.assertEqual([row["id"] for row in battles], list(range(7)))
        self.assertEqual(trainers[0]["encounters"], [3, 4])
        self.assertEqual((trainers[0]["difficulty"], trainers[2]["difficulty"], trainers[8]["difficulty"]),
                         (None, ["hard"], ["hard"]))
        self.assertEqual(trainers[3]["encounters"], [3])   # a partner with another battle adds none
        self.assertEqual(trainers[9]["encounters"], [2])   # a claimed partner keeps its stage

    def test_encounters_list_their_battle_commands_and_modes(self):
        trainers = self.trainers([30, 31, 40, 41, 10, 11, 12, 50, 60, 20, 5, 70])
        difficulty = {"settings": ["normal", "hard"], "selections": [{"script": 0x100, "records": [0, 2]}]}
        groups = [{"script": 0x300, "records": [4], "commands": [0x500], "evidence": "branch"}]
        commands = {0x500: [("opponent", 4)], 0x520: [("opponent", 10)], 0x530: [("opponent", 10)]}
        modes = {0x500: (13, []), 0x520: (0, []), 0x530: (5, []), 0x105: (3, [0x100]), 0x120: (15, [0x100])}
        battles = pt.encounters(trainers, difficulty, groups, commands, modes=modes)
        self.assertEqual({row["variants"][0]["record"]: row["commands"] for row in battles}, {
            0: [{"script": "0x0000105", "mode": 3}, {"script": "0x0000120", "mode": 15}],
            4: [{"script": "0x0000500", "mode": 13}],
            10: [{"script": "0x0000520", "mode": 0}, {"script": "0x0000530", "mode": 5}]})
        self.assertNotIn("commands", pt.encounters(trainers, difficulty, groups, commands)[0])
        with self.assertRaisesRegex(ValueError, "unreached battle commands"):
            pt.encounters(trainers, difficulty, groups, commands, modes={0x520: (0, [])})

    def test_group_declarations_are_validated(self):
        self.assertEqual(pt.check_encounters([{"script": 1, "records": [2], "evidence": "x"}], 6), [])
        self.assertEqual(pt.check_encounters([
            {"script": 1, "records": [2], "evidence": "x", "native": "assumed"}], 6), ["[0]: native status"])
        self.assertEqual(pt.check_encounters([
            {"script": 1, "evidence": "x"}, {"records": [1]}, {"script": 1, "records": [9], "evidence": "x"},
            {"script": 1, "stages": [{"record": 1, "flag": 0x821}], "evidence": "x"},
            {"script": 1, "starter": [1], "commands": ["0x1"], "evidence": "x"}], 6), [
            "[0]: one of starter, records, stages", "[1]: script and evidence", "[2]: valid record ids",
            "[3]: a stage flag needs its condition label", "[4]: command offsets"])
        self.assertIn("maps roles 10: opponent and partner operand offsets",
                      pt.check({**map_block(), "maps": {"evidence": "x", "roles": {"10": {"second": [4]}}}}))


class TrainerOrderTest(unittest.TestCase):
    def order(self):
        return {"evidence": "Synthetic progress gate", "searched": "Synthetic script branches", "rules": [{
            "id": "progress", "sequence": [{"kind": "battle", "record": 0}, {"kind": "selection", "script": 0x100}],
            "evidence": "The first battle sets the later battle gate", "native": "unconfirmed",
            "rom": [{"offset": 0, "size": 4, "sha256": hashlib.sha256(b"test").hexdigest()}]}]}

    def build(self, order=None):
        trainers = [{"id": index, "average_level": level} for index, level in enumerate([80, 10, 90, 20, 30])]
        difficulty = {"settings": ["normal", "hard"], "selections": [{"script": 0x100, "records": [1, 2]}]}
        battles = pt.encounters(trainers, difficulty, [], {
            0x200: [("opponent", 0)], 0x210: [("opponent", 3)], 0x220: [("opponent", 4)]}, order=order)
        return trainers, battles, difficulty

    def test_story_constraints_override_levels_without_reordering_unknowns(self):
        order = self.order()
        trainers, battles, difficulty = self.build(order)
        self.assertEqual([row["variants"][0]["record"] for row in battles], [3, 4, 0, 1])
        self.assertEqual([row["encounters"] for row in trainers], [[2], [3], [3], [0], [1]])
        self.assertEqual(battles[-1]["order"], {"status": "constrained", "rules": ["progress"]})
        self.assertEqual(pt.order_summary(battles, {"order": order})["unknown"], [0, 1])
        self.assertEqual(pt.verify_encounters(battles, trainers, {"order": order, "difficulty": difficulty}), [])
        plain = self.build()[1]
        self.assertEqual([row["variants"][0]["record"] for row in plain], [1, 3, 4, 0])
        self.assertTrue(all(row["order"] == {"status": "unknown", "rules": []} for row in plain))

    def test_bound_order_notes_survive_technical_export(self):
        import json

        from project_fixture import with_profile_notes

        original = self.order()
        expected = self.build(original)[1]
        for status in ("unreviewed", "accepted"):
            with self.subTest(status=status):
                order = with_profile_notes(original, status)
                self.assertEqual(pt.check_order(order, 5), [])
                trainers, battles, difficulty = self.build(order)
                self.assertEqual(battles, expected)
                restored = json.loads(json.dumps(battles))
                self.assertEqual(pt.verify_encounters(restored, trainers,
                                                     {"order": order, "difficulty": difficulty}), [])
                summary = json.loads(json.dumps(pt.order_summary(battles, {"order": order})))
                self.assertEqual(summary["rules"][0]["evidence"],
                                 original["rules"][0]["evidence"])

    def test_changed_order_and_annotations_are_rejected(self):
        order = self.order()
        trainers, battles, difficulty = self.build(order)
        block = {"order": order, "difficulty": difficulty}
        for key, value in (("status", "unknown"), ("rules", [])):
            broken = copy.deepcopy(battles)
            broken[-1]["order"][key] = value
            self.assertTrue(pt.verify_encounters(broken, trainers, block))
        old_trainers, old_battles, _ = self.build()
        for row in old_battles:
            row["order"] = next(b["order"] for b in battles if b["variants"] == row["variants"])
        self.assertTrue(pt.verify_encounters(old_battles, old_trainers, block))

    def test_rules_reject_missing_evidence_and_invalid_selectors(self):
        order = self.order()
        self.assertEqual(pt.check_order(order, 5), [])
        for key, value in (("id", ""), ("evidence", ""), ("native", "yes"), ("rom", []),
                           ("sequence", []), ("sequence", [False, {}])):
            broken = copy.deepcopy(order)
            broken["rules"][0][key] = value
            self.assertTrue(pt.check_order(broken, 5), key)
        for selector in ({"kind": "battle", "record": 5}, {"kind": "battle", "record": True},
                         {"kind": "selection", "script": -1}, {"kind": "group"},
                         {"kind": "battle", "record": 1, "script": 0}, {"kind": "battle", "record": 1, "rank": 2}):
            broken = copy.deepcopy(order)
            broken["rules"][0]["sequence"][0] = selector
            self.assertTrue(pt.check_order(broken, 5), selector)
        for proof in ({"offset": -1, "size": 4, "sha256": "0" * 64},
                      {"offset": 0, "size": 0, "sha256": "0" * 64},
                      {"offset": 0, "size": 4, "sha256": "x" * 64}):
            broken = copy.deepcopy(order)
            broken["rules"][0]["rom"] = [proof]
            self.assertTrue(pt.check_order(broken, 5))
        for key in ("searched", "evidence"):
            broken = copy.deepcopy(order)
            del broken[key]
            self.assertTrue(pt.check_order(broken, 5))
        order["rules"].append(copy.deepcopy(order["rules"][0]))
        self.assertTrue(pt.check_order(order, 5))

    def test_byte_proofs_missing_matches_repeats_and_cycles_fail_closed(self):
        order = self.order()
        pt.validate_order(order, b"test")
        for data in (b"best", b"tes"):
            with self.assertRaisesRegex(ValueError, "trainer order: ROM span differs"):
                pt.validate_order(order, data)
        for selector in ({"kind": "battle", "record": 2}, {"kind": "battle", "record": 0}):
            broken = copy.deepcopy(order)
            broken["rules"][0]["sequence"][1] = selector
            with self.assertRaisesRegex(ValueError, "selector is not unique"):
                self.build(broken)
        reverse = copy.deepcopy(order["rules"][0])
        reverse["id"] = "reverse"
        reverse["sequence"].reverse()
        order["rules"].append(reverse)
        with self.assertRaisesRegex(ValueError, "cyclic"):
            self.build(order)

    def test_shared_stage_anchors_require_a_record_and_follow_proven_sequence(self):
        order = self.order()
        order["rules"][0]["sequence"] = [{"kind": "group", "script": 0x300, "record": 0},
                                          {"kind": "group", "script": 0x300, "record": 1}]
        trainers = [{"id": 0, "average_level": 80}, {"id": 1, "average_level": 10}]
        groups = [{"script": 0x300, "stages": [{"record": 1}, {"record": 0}]}]
        battles = pt.encounters(trainers, None, groups, {}, order=order)
        self.assertEqual([row["variants"][0]["record"] for row in battles], [0, 1])
        del order["rules"][0]["sequence"][0]["record"]
        with self.assertRaisesRegex(ValueError, "selector is not unique"):
            pt.encounters(trainers, None, groups, {}, order=order)
