from test_roots import TOOLKIT_ROOT
"""Pokedexmoves flinch checks and fixtures."""

import sys
from pathlib import Path
import copy
import unittest

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))
from project_fixture import rom_evidence
from gen3tl.data import pokedexdata as pd
from gen3tl.data import pokedexmoves as pm
from pokedexmoves_support import BASE, move_block, rom_bytes


def flinch_fixture():
    block = move_block()
    block["flinch"] = {
        "secondary_comparison": "inclusive", "evidence": "Synthetic secondary reader",
        "rom": [rom_evidence(0, "00")], "conditions": ["page:mechanics:flinch:hit"],
        "rules": [
            {"effects": [0], "moves": [1], "chance": "secondary", "evidence": "Synthetic queued effect",
             "rom": [rom_evidence(BASE + 12, "00")]},
            {"effects": [43], "chance": 100, "conditions": ["page:mechanics:flinch:first_turn"],
             "evidence": "Synthetic certain effect", "rom": [rom_evidence(BASE + 24, "2b")]},
        ],
    }
    return block


class FlinchTest(unittest.TestCase):
    def test_zero_roll_certain_and_absent_paths_are_distinct(self):
        block = flinch_fixture()
        self.assertEqual(pm.check(block), [])
        records = pm.build(block, rom_bytes())
        self.assertEqual([row["flinch_chance"] for row in records], [0, 1, 100, 0])
        self.assertEqual(records[1]["flinch_conditions"], ["page:mechanics:flinch:hit"])
        self.assertEqual(records[2]["flinch_conditions"],
                         ["page:mechanics:flinch:hit", "page:mechanics:flinch:first_turn"])
        self.assertEqual(records[3]["flinch_conditions"], [])
        for stored in (0, 10, 20, 30, 99, 100, 255):
            for comparison in ("inclusive", "exclusive"):
                data = bytearray(rom_bytes())
                data[BASE + 12 + 5] = stored
                block["flinch"]["secondary_comparison"] = comparison
                expected = sum(roll <= stored if comparison == "inclusive" else roll < stored for roll in range(100))
                record = pm.build(block, data)[1]
                self.assertEqual(record["flinch_chance"], expected)
                self.assertEqual(bool(record["flinch_conditions"]), bool(expected))
        self.assertTrue(all("flinch_chance" not in row for row in pm.build(move_block(), rom_bytes())))

    def test_proofs_and_rule_overlap_fail_extraction(self):
        for offset in (0, BASE + 12, BASE + 24):
            data = bytearray(rom_bytes())
            data[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaisesRegex(ValueError, "flinch evidence differs"):
                pm.build(flinch_fixture(), data)
        block = flinch_fixture()
        block["flinch"]["rules"].append(copy.deepcopy(block["flinch"]["rules"][0]))
        with self.assertRaisesRegex(ValueError, "overlapping flinch rules"):
            pm.build(block, rom_bytes())

    def test_malformed_profile_rules_are_rejected(self):
        for field, values in {
            "secondary_comparison": [None, "equal", True], "rules": [None, [], [None]],
            "conditions": [None, "key", ["English"]], "evidence": [None, "", True],
            "rom": [None, [], [None], [rom_evidence(True, "00")], [dict(rom_evidence(0, "00"), sha256="invalid")]],
        }.items():
            for value in values:
                block = flinch_fixture()
                block["flinch"][field] = value
                with self.subTest(field=field, value=value):
                    self.assertTrue(pm.check(block))
        for field, values in {
            "effects": [None, [], [31], [True], [0, 0]], "moves": [None, [], [-1], [4], [True], [1, 1]],
            "chance": [None, 0, 101, True, "1", {}], "conditions": [["bad"], [None]],
            "evidence": [None, ""], "rom": [None, [], [{}]],
        }.items():
            for value in values:
                block = flinch_fixture()
                block["flinch"]["rules"][0][field] = value
                with self.subTest(field=field, value=value):
                    self.assertIn(f"flinch rules 0 {field}", " ".join(pm.check(block)))

    def test_dataset_verification_requires_all_derived_fields(self):
        block = {"name_table": "species",
                 "species": {"base": 0, "stride": 1, "count": 1, "first": 0,
                             "evidence": "synthetic", "fields": {"hp": 0}}, "moves": flinch_fixture()}
        doc = pd.build(block, rom_bytes(), {"id": "demo"})
        self.assertEqual(pd.verify_doc(doc, block, "demo", doc["rom"]["sha1"]), [])
        for number, field, value in ((1, "flinch_chance", 0), (1, "flinch_chance", True),
                                     (1, "flinch_conditions", []), (2, "flinch_chance", 1),
                                     (2, "flinch_conditions", None), (3, "flinch_chance", None),
                                     (3, "flinch_chance", 100)):
            changed = copy.deepcopy(doc)
            if value is None:
                del changed["moves"][number][field]
            else:
                changed["moves"][number][field] = value
            self.assertIn(f"move {number} flinch rule: regenerate the dataset with gen3tl data pokedex",
                          pd.verify_doc(changed, block, "demo", doc["rom"]["sha1"]))
