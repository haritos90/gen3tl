from test_roots import TOOLKIT_ROOT
"""Pokedextrainerivs moves checks and fixtures."""

import sys
from pathlib import Path
import copy
import json
import unittest

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.data import pokedextrainerivs as iv
from pokedextrainerivs_support import member, scaled_rule, species_rule, trainer, uniform


class DefaultMoveBandsTest(unittest.TestCase):
    def test_bands_preserve_rom_order_and_deduplicate_after_selection(self):
        learnsets = {1: [{"method": 0, "level": level, "move": move} for level, move in
                        [(1, 9), (1, 8), (1, 7), (10, 6), (20, 7), (20, 5), (100, 4)]]}
        learnsets[1].append({"method": 1, "level": 30, "move": 99})
        bands = iv.default_move_bands(learnsets, 1)
        self.assertEqual(bands, [
            {"level": 1, "last": 9, "moves": [9, 8, 7]},
            {"level": 10, "last": 19, "moves": [9, 8, 7, 6]},
            {"level": 20, "last": 99, "moves": [7, 6, 5]},
            {"level": 100, "moves": [6, 7, 5, 4]}])
        self.assertTrue(iv._move_bands(bands, {"level": 20, "moves": [7, 6, 5]}))

    def test_unchanging_and_missing_learnsets_remain_distinct(self):
        self.assertEqual(iv.default_move_bands({1: []}, 1), [{"level": 1, "moves": []}])
        with self.assertRaisesRegex(iv.TrainerIvError, "default moves unavailable"):
            iv.default_move_bands({}, 1)

    def test_bands_clip_both_ends_of_the_buildable_range(self):
        learnsets = {1: [{"method": 0, "level": level, "move": move}
                        for level, move in [(1, 1), (20, 2), (98, 3)]]}
        bands = iv.default_move_bands(learnsets, 1, 15, 97)
        self.assertEqual(bands, [{"level": 15, "last": 19, "moves": [1]},
                                 {"level": 20, "last": 97, "moves": [1, 2]}])
        ranges = {"0": {"min": 15, "max": 97}, "1": {"min": 15, "max": 15}}
        listed = {"level": 15, "moves": [1]}
        self.assertTrue(iv._move_bands(bands, listed, ranges))
        self.assertTrue(iv._level_ranges(ranges, listed, {0, 1}))
        bands[-1].pop("last")
        self.assertFalse(iv._move_bands(bands, listed, ranges))
        for bad in (None, {}, {"0": {"min": 15, "max": 14}}, {"0": {"min": 15, "max": 101}},
                    {"0": {"min": True, "max": 15}}, {"0": {"min": 1, "max": 15}}):
            self.assertFalse(iv._level_ranges(bad, listed, {0}))

    def test_incomplete_overlapping_and_noncompact_bands_fail(self):
        for bands in ([], [{"level": 2, "moves": [1]}], [{"level": 1, "last": 99, "moves": [1]}],
                      [{"level": 1, "last": 20, "moves": [1]}, {"level": 20, "moves": [2]}],
                      [{"level": 1, "last": 20, "moves": [1]}, {"level": 21, "moves": [1]}],
                      [{"level": 1, "moves": [1, 1]}], [{"level": 1, "last": 101, "moves": [1]}],
                      [{"level": True, "moves": [1]}], [{"level": 1, "moves": [2]}]):
            with self.subTest(bands=bands):
                self.assertFalse(iv._move_bands(bands, {"level": 10, "moves": [1]}))
        self.assertFalse(iv._move_bands([{"level": 1, "moves": [1]}], {"level": None, "moves": [1]}))


class PartyWritesTest(unittest.TestCase):
    def setUp(self):
        self.rule = {"id": "branch", "status": "known", "native": "confirmed", "stage": "adjustment",
                     "evidence": "Bounded branch and write bytes", "rom": [{"offset": 0, "size": 2, "sha256": "0" * 64}],
                     "when": {"trainers": [1], "species": [19], "difficulties": [2, 3], "spreads": [1, 141]},
                     "moves": [{"slot": 1, "move": 9}], "item": 0}
        self.block = {"difficulties": ["a", "b", "c", "d"], "write_rules": [self.rule]}
        self.mon = {**member(spread=1, moves=[1, 2, 3, 4]), "item": 7}

    def compile(self, species=19, species_write=None):
        class Spread:
            def spread_options(self, trainer, member, species, difficulty, progress):
                return [(member["iv"], {})]
        return iv.PartyWrites(self.block, Spread()).changes(
            trainer(), self.mon, species, lambda level: (self.mon["moves"], self.mon["item"]),
            range(1, 101), [0, 1, 2, 3], species_write)

    def test_writes_require_species_difficulty_and_eligible_spread(self):
        self.assertEqual(iv.check_write_rules(self.block), [])
        self.assertEqual(self.compile(), [{"level": 1, "difficulties": [2, 3], "when": {},
                                          "moves": [1, 9, 3, 4], "item": 0, "rules": ["branch"]}])
        self.assertEqual(self.compile(species=20), [])
        self.mon["iv"] = 142
        self.assertEqual(self.compile(), [])

    def test_bound_notes_keep_compiled_party_writes(self):
        from project_fixture import with_profile_notes

        original = copy.deepcopy(self.block)
        expected = self.compile()
        for status in ("unreviewed", "accepted"):
            with self.subTest(status=status):
                self.block = with_profile_notes(original, status)
                self.assertEqual(iv.check_write_rules(self.block), [])
                self.assertEqual(json.loads(json.dumps(self.compile())), expected)
        original["write_rules"][0]["evidence"] = ""
        self.assertTrue(iv.check_write_rules(original))

    def test_level_and_flag_predicates_preserve_zero_items(self):
        self.rule["when"].update(level_min=31, level_max=39, flags={"0x100": 0})
        rows = self.compile()
        self.assertEqual(rows, [{"level": 31, "last": 39, "difficulties": [2, 3], "when": {"0x100": [0]},
                                 "moves": [1, 9, 3, 4], "item": 0, "rules": ["branch"]}])

    def test_substitution_uses_first_match_and_leaves_absent_moves(self):
        self.assertEqual(iv.apply_moves([2, 2, 3, 4], [
            {"from_move": 2, "move": 9}, {"from_move": 5, "move": 8}], 50), [9, 2, 3, 4])
        self.rule["moves"] = [{"from_move": 5, "move": 9}]
        del self.rule["item"]
        self.assertEqual(self.compile(), [])

    def test_species_branch_has_precedence_over_baseline_writes(self):
        rows = self.compile(species_write={"item": 8, "moves": [{"slot": 1, "move": 6}]})
        self.assertTrue(all(row["moves"] == [1, 6, 3, 4] and row["item"] == 8 for row in rows))

    def test_unknown_evidence_does_not_generate_changes(self):
        self.rule.update(status="unknown", searched="Selector inputs inspected", members=[
            {"trainer": 1, "slot": 0, "fields": ["item"]}])
        self.rule.pop("moves")
        self.rule.pop("item")
        self.assertEqual(iv.check_write_rules(self.block), [])
        self.assertEqual(self.compile(), [])

    def test_generated_conditions_reject_overlap_and_unproved_rules(self):
        rows = self.compile()
        self.assertTrue(iv._write_changes(rows, {"branch"}, {0, 1, 2, 3}))
        self.assertFalse(iv._write_changes(rows + rows, {"branch"}, {0, 1, 2, 3}))
        self.assertFalse(iv._write_changes(rows, {"other"}, {0, 1, 2, 3}))
        rows[0]["when"] = {"0x100": [2]}
        self.assertFalse(iv._write_changes(rows, {"branch"}, {0, 1, 2, 3}))

    def test_unbounded_or_unsupported_rules_fail_validation(self):
        for changes in ({"stage": "final"}, {"rom": []}, {"item": -1}, {"moves": [{"slot": 4, "move": 1}]},
                        {"moves": [{"slot": 0, "from_move": 1, "move": 2}]}):
            block = copy.deepcopy(self.block)
            block["write_rules"][0].update(changes)
            self.assertTrue(iv.check_write_rules(block), changes)
        for changes in ({"level_min": 101}, {"flags": {"0x100": 2}}, {"unknown": 1}, {"difficulties": [4]}):
            block = copy.deepcopy(self.block)
            block["write_rules"][0]["when"].update(changes)
            self.assertTrue(iv.check_write_rules(block), changes)


class FakeIvs:
    """IVs that follow the presence of one move."""

    def resolve(self, trainer, member, *, difficulty, side, species, level, moves, progress):
        return {"status": "resolved", "ivs": [31 if 99 in moves else 10] * 6}


class FakeEvs:
    """EVs that record the species and level each context reads."""

    def __init__(self):
        self.block = {"adjustments": [{"id": "late", "trainers": [5], "operation": "set", "level_min": 40}]}
        self.calls = []

    def contexts(self, trainer, member, species_options, side, moves_of, predicates, difficulties, grant):
        self.calls.append((species_options, member["level"], grant, predicates))
        return {str(day): [252 if member["level"] >= 40 else 0, 0, 0, 0, 0, grant] for day in difficulties}


class OutcomesTest(unittest.TestCase):
    def setUp(self):
        level = species_rule("level", "trainer_level", trainer=5, difficulties=[2, 3], spreads=[1, 3], changes=[
            {"species": 10, "target": 11, "level_above": 30, "item": 9,
             "moves": [{"slot": 0, "move": 7, "level_above": 35}, {"slot": 1, "move": 8}]}])
        self.block = {"species_rules": [level, scaled_rule()]}
        self.learnsets = {2: [{"method": 0, "level": 1, "move": 20}, {"method": 0, "level": 25, "move": 21},
                              {"method": 0, "level": 30, "move": 99}],
                          1: [{"method": 0, "level": 1, "move": 30}]}
        self.evs = FakeEvs()
        conditions = {"before": {"when": [{"0x100": 0}]}}
        self.outcomes = iv.Outcomes(self.block, FakeIvs(), self.evs, self.learnsets, [{}], conditions)

    def test_created_species_take_default_moves_ivs_and_evs_by_built_level(self):
        mon = {"index": 0, "species": 1, "level": 10, "moves": None, "item": 3}
        change = {"species": 2, "rule": "scaled", "level": 20, "last": 44, "difficulties": [0, 2], "from": 1}
        out = self.outcomes.details({"id": 4}, mon, change, 1, [{"species": 1, "condition": "before"}])
        self.assertEqual(out["evs"], {"0": [0, 0, 0, 0, 0, 2], "2": [0, 0, 0, 0, 0, 2]})
        self.assertEqual(out["bands"], [
            {"level": 20, "last": 24, "moves": [20], "ivs": {"0": uniform(10), "2": uniform(10)}},
            {"level": 25, "last": 29, "moves": [20, 21], "ivs": {"0": uniform(10), "2": uniform(10)}},
            {"level": 30, "moves": [20, 21, 99], "ivs": {"0": uniform(31), "2": uniform(31)}}])
        self.assertNotIn("item", out)
        self.assertEqual({call[0][0] for call in self.evs.calls}, {2})
        self.assertEqual(self.evs.calls[0][3], {2: [{"0x100": 0}]})

    def test_trainer_branches_write_item_and_slots_and_read_the_built_level(self):
        mon = {"index": 0, "species": 10, "level": 22, "moves": [1, 2, 3, 4], "item": 3}
        change = {"species": 11, "rule": "level", "level": 31, "difficulties": [2, 3]}
        out = self.outcomes.details({"id": 5}, mon, change, 1, None)
        self.assertEqual((out["item"], out["ivs"]), (9, {"2": uniform(10), "3": uniform(10)}))
        self.assertEqual([(band["level"], band.get("last"), band["moves"], band["evs"]["2"][0])
                          for band in out["bands"]], [(31, 35, [1, 8, 3, 4], 0), (36, 39, [7, 8, 3, 4], 0),
                                                      (40, None, [7, 8, 3, 4], 252)])
        self.assertEqual({(call[0][0], call[2]) for call in self.evs.calls}, {(10, 11)})

    def test_members_without_a_built_level_keep_one_listed_level(self):
        self.block["species_rules"].append(species_rule("form", "held_item_form", item_flag=2, forms=[]))
        outcomes = iv.Outcomes(self.block, FakeIvs(), None, self.learnsets, [{}], {})
        mon = {"index": 0, "species": 1, "level": 12, "moves": None, "item": 3}
        out = outcomes.details({"id": 4}, mon, {"species": 2, "rule": "form", "difficulties": [1]}, 0, None)
        self.assertEqual(out, {"moves": [30], "ivs": {"1": uniform(10)}})
