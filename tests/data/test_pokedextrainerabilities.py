"""Trainer ability choices, fallback slots and declared overrides."""

import copy
import unittest

from gen3tl.data import pokedextrainerabilities as abilities


def declaration():
    return {
        "schema": 1, "context": "listed-species", "evidence": "Synthetic construction.",
        "difficulties": ["low", "high"],
        "initial": {"name_offset": 4, "name_size": 12, "terminator": 255, "gender_offset": 2,
                    "partner_music_mask": 127, "fixed_gender_classes": [9], "evidence": "Synthetic parity."},
        "spread": {"first": 1, "last": 6, "selector_offset": 2,
                   "modes": ["hidden", "first", "second", "regular", "all"], "evidence": "Synthetic selectors."},
        "fallback": {"second": "first", "hidden": "personality", "evidence": "Synthetic fallback."},
        "spans": [{"offset": 0, "size": 4, "sha256": "a" * 64}],
        "overrides": [],
    }


class AbilityResolverTest(unittest.TestCase):
    def setUp(self):
        self.block = declaration()
        self.spreads = [[0, 0, selector] for selector in (0, 0, 1, 2, 3, 4, 5)]

    def resolve(self, spread, slots=(10, 20, 30), bit=0, difficulty=0):
        return abilities.resolve(self.block, 4, 8, difficulty, bit,
                                 spread if isinstance(spread, list) else [spread], self.spreads, list(slots))

    def override(self, operation, **kwargs):
        self.block["overrides"].append({"trainers": [4], "species": [8], "difficulties": [1],
                                        "operation": operation, "evidence": "Synthetic override.", **kwargs})

    def test_each_spread_mode_and_initial_personality(self):
        for spread, expected in [(0, [10]), (1, [30]), (2, [10]), (3, [20]),
                                 (4, [10, 20]), (5, [10, 20, 30]), (6, [10]), (7, [10])]:
            with self.subTest(spread=spread):
                self.assertEqual(self.resolve(spread), expected)
        self.assertEqual(self.resolve(0, bit=1), [20])
        self.assertEqual(self.resolve(6, bit=1), [20])
        self.assertEqual(self.resolve(2, difficulty=1), [10])

    def test_empty_slots_and_duplicate_ids_collapse(self):
        self.assertEqual(self.resolve(3, slots=(10, 0, 30)), [10])
        self.assertEqual(self.resolve(1, slots=(10, 20, 0)), [10, 20])
        self.assertEqual(self.resolve(1, slots=(10, 0, 0)), [10])
        self.assertEqual(self.resolve(5, slots=(10, 10, 10)), [10])

    def test_progress_spread_alternatives_are_unioned(self):
        self.assertEqual(self.resolve([2, 3]), [10, 20])
        self.assertEqual(self.resolve([1, 2]), [10, 30])
        self.assertEqual(self.resolve([2, 3], slots=(10, 0, 30)), [10])

    def test_hidden_overrides_preserve_personality_fallback(self):
        self.override("hidden")
        self.assertEqual(self.resolve(3, difficulty=0), [20])
        self.assertEqual(self.resolve(3, difficulty=1), [30])
        self.assertEqual(self.resolve(3, slots=(10, 20, 0), difficulty=1), [20])
        self.assertEqual(self.resolve(4, slots=(10, 20, 0), difficulty=1), [10, 20])

    def test_clear_hidden_exposes_random_personality(self):
        self.override("regular")
        self.assertEqual(self.resolve(1, difficulty=1), [10, 20])

    def test_personality_constants_preserve_hidden_and_apply_last(self):
        self.override("personality", bit=1)
        self.assertEqual(self.resolve(2, difficulty=1), [20])
        self.assertEqual(self.resolve(1, difficulty=1), [30])
        self.assertEqual(self.resolve(1, slots=(10, 20, 0), difficulty=1), [20])
        self.assertEqual(self.resolve(0, difficulty=1), [10])

    def test_shiny_keeps_regular_bit_but_can_randomize_hidden_fallback(self):
        self.override("hidden")
        self.override("shiny")
        self.assertEqual(self.resolve(2, slots=(10, 20, 0), difficulty=1), [10, 20])
        self.block["overrides"].pop(0)
        self.assertEqual(self.resolve(2, difficulty=1), [10])

    def test_removed_members_have_no_ability(self):
        self.override("absent")
        self.assertEqual(self.resolve(2, difficulty=1), [])
        self.assertEqual(self.resolve(2, difficulty=0), [10])
        self.assertEqual(self.resolve(0, difficulty=1), [10])
        with self.assertRaisesRegex(ValueError, "unresolved presence"):
            self.resolve([0, 2], difficulty=1)

    def test_rejects_incomplete_inputs_and_missing_rows(self):
        with self.assertRaisesRegex(ValueError, "missing construction"):
            self.resolve([], bit=0)
        with self.assertRaisesRegex(ValueError, "empty first slot"):
            self.resolve(2, slots=(0, 20, 30))
        self.spreads = []
        with self.assertRaisesRegex(ValueError, "missing spread"):
            self.resolve(2)

    def test_declarations_and_dataset_require_complete_evidence_and_contexts(self):
        self.assertEqual(abilities.check(self.block), [])
        broken = copy.deepcopy(self.block)
        broken["spread"]["modes"][0] = "guessed"
        broken["fallback"]["hidden"] = "first"
        self.assertEqual(abilities.check(broken), ["spread modes", "fallback"])
        party = [{"party": [{"abilities": {"0/0": [10], "0/1": [10, 20], "1/0": [], "1/1": [20]}}]}]
        records = [{"id": 10}, {"id": 20}]
        self.assertEqual(abilities.verify(self.block, party, records), [])
        party[0]["party"][0]["abilities"]["1/1"] = [99]
        self.assertEqual(abilities.verify(self.block, party, records), ["trainer abilities: invalid ability alternatives"])
        del party[0]["party"][0]["abilities"]["1/1"]
        self.assertEqual(abilities.verify(self.block, party, records), ["trainer abilities: regenerate difficulty contexts"])
