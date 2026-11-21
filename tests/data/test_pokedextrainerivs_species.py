from test_roots import TOOLKIT_ROOT
"""Pokedextrainerivs species checks and fixtures."""

import sys
from pathlib import Path
import copy
import unittest

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.data import pokedextrainerivs as iv
from pokedextrainerivs_support import member, scaled_rule, species_rule, trainer


class SpeciesConditionsTest(unittest.TestCase):
    def setUp(self):
        self.block = {"species_conditions": [
            {"id": "original", "label": "Original condition", "status": "known", "native": "unconfirmed",
             "when": [{"0x100": 0}, {"0x100": 1, "0x101": 1}], "evidence": "Selector and setter bytes",
             "rom": [{"offset": 0, "size": 4, "sha256": "0" * 64}]},
            {"id": "replacement", "label": "Replacement condition", "status": "known", "native": "unconfirmed",
             "when": [{"0x100": 1, "0x101": 0}], "evidence": "Selector and setter bytes",
             "rom": [{"offset": 0, "size": 4, "sha256": "0" * 64}]},
        ], "species_variants": [{"trainer": 1, "slot": 0, "species": [10, 20], "outcomes": [
            {"species": 10, "condition": "original"}, {"species": 20, "condition": "replacement"}]}]}

    def test_complementary_conditions_cover_all_flag_combinations(self):
        self.assertEqual(iv.check_species_conditions(self.block), [])

    def test_missing_or_overlapping_states_fail(self):
        for states in ([{"0x100": 0}], [{"0x100": 0}, {"0x100": 1}]):
            with self.subTest(states=states):
                block = copy.deepcopy(self.block)
                block["species_conditions"][0]["when"] = states
                self.assertIn("species variant predicates: complete disjoint coverage required",
                              iv.check_species_conditions(block))

    def test_every_species_requires_a_referenced_condition(self):
        for change in ("missing", "unreferenced", "duplicate"):
            with self.subTest(change=change):
                block = copy.deepcopy(self.block)
                rows = block["species_variants"][0]["outcomes"]
                if change == "missing":
                    rows.pop()
                elif change == "unreferenced":
                    rows[0]["condition"] = "absent"
                else:
                    rows[1]["species"] = rows[0]["species"]
                self.assertTrue(iv.check_species_conditions(block))

    def test_unknown_causes_require_the_search_record(self):
        row = self.block["species_conditions"][1]
        row["status"] = "unknown"
        self.assertIn("species condition replacement: evidence", iv.check_species_conditions(self.block))
        row["searched"] = "Selector, script setters and literal references"
        self.assertEqual(iv.check_species_conditions(self.block), [])

    def test_missing_rom_proof_fails(self):
        self.block["species_conditions"][0]["rom"] = []
        self.assertIn("species condition original: ROM proofs", iv.check_species_conditions(self.block))


CORES = {"offset": 0x40, "count": 3}


def minior_rule(personality, **fields):
    shared = {"species": [30, 31, 32, 33], "cores": CORES, "spreads": [1, 3], "ability_offset": 1, "ability_max": 4}
    if personality == "fixed":
        shared.update(names={"offset": 0x60, "stride": 2}, trainer_name={"offset": 4, "size": 4},
                      gender={"offset": 2, "shift": 7}, gender_classes=[9], values={"male": 0x88, "female": 0x78})
    return species_rule(f"core-{personality}", "minior_core", personality=personality, **{**shared, **fields})


class FakeResolver:
    def __init__(self, spreads):
        self.spreads = spreads

    def spread_options(self, trainer, member, species, difficulty, progress):
        return [(member["iv"] & 0xFF, {})]


class SpeciesRulesTest(unittest.TestCase):
    def setUp(self):
        self.block = {"difficulties": ["a", "b", "c", "d"], "species_rules": [
            species_rule("level", "trainer_level", trainer=5, difficulties=[2, 3], spreads=[1, 3], opponent=7,
                         changes=[{"species": 10, "target": 11, "level_above": 30}]),
            species_rule("form", "held_item_form", item_flag=2, forms=[
                {"species": [20], "hold_effect": 0x57, "table": {"offset": 0x20, "count": 4}, "otherwise": 20,
                 "ability": 167, "unsupported_effects": [0x82]},
                {"species": [21], "hold_effect": 0x5A, "target": 21, "otherwise": 22, "keep_sections": [0x97]},
                {"species": [23], "hold_effect": 0x56, "params": [[10, 24]], "otherwise": 23}]),
            minior_rule("random"), minior_rule("fixed")]}
        data = bytearray(0x100)
        data[0x20:0x28] = b"\x00\x00\x00\x00\x19\x00\x00\x00"
        data[0x40:0x46] = bytes([31, 0, 32, 0, 33, 0])
        data[0x10:0x18] = bytes([0, 0, 0x80, 0, 0x01, 0x02, 0xFF, 0])
        data[0x60 + 2 * 30:0x60 + 2 * 31] = bytes([0x05, 0xFF])
        self.data = bytes(data)
        self.items = [{"id": 1, "held_effect": 0x57, "hold_effect_param": 2},
                      {"id": 2, "held_effect": 0x82, "hold_effect_param": 0},
                      {"id": 3, "held_effect": 0x56, "hold_effect_param": 10}]
        spreads = [[0, 0], [0, 4], [0, 5], [0, 0]]
        self.rules = iv.SpeciesRules(self.block, self.data, FakeResolver(spreads), self.items, {20: [167]}, set())

    def changes(self, trainer_id, species, spread=1, item=0, flags=2, **trainer_fields):
        mon = {"index": 0, "species": species, "level": 5, "iv": spread, "item": item}
        return self.rules.changes({"id": trainer_id, "party_flags": flags, "class_id": 1, "offset": "0x10",
                                   "party": [mon], **trainer_fields}, mon)

    def test_complete_rules_pass_the_declaration_check(self):
        self.assertEqual(iv.check_species_rules(self.block), [])

    def test_incomplete_rules_fail(self):
        for index, field in ((0, "changes"), (1, "item_flag"), (2, "cores"), (0, "spreads"), (3, "values")):
            with self.subTest(field=field):
                block = copy.deepcopy(self.block)
                block["species_rules"][index].pop(field)
                self.assertTrue(iv.check_species_rules(block))
        block = copy.deepcopy(self.block)
        block["species_rules"][3]["spreads"] = [1, 2]
        self.assertIn("species rules: random and fixed cores share their inputs", iv.check_species_rules(block))
        block = copy.deepcopy(self.block)
        block["species_rules"][1]["forms"][1]["species"] = [20]
        self.assertIn("species rule form: one form entry per species", iv.check_species_rules(block))
        for stage in (None, "battle"):
            with self.subTest(stage=stage):
                block = copy.deepcopy(self.block)
                block["species_rules"][0]["stage"] = stage
                self.assertIn("species rule level: evidence", iv.check_species_rules(block))

    def test_trainer_branches_declare_their_item_and_move_slot_writes(self):
        change = self.block["species_rules"][0]["changes"][0]
        change.update(item=9, moves=[{"slot": 0, "move": 7, "level_above": 35}, {"slot": 3, "move": 8}])
        self.assertEqual(iv.check_species_rules(self.block), [])
        for bad in ({"item": 0}, {"moves": [{"slot": 4, "move": 7}]}, {"moves": [{"slot": 0}]},
                    {"moves": [{"slot": 0, "move": 7, "level_above": 0}]}):
            with self.subTest(bad=bad):
                block = copy.deepcopy(self.block)
                block["species_rules"][0]["changes"][0].update(bad)
                self.assertIn("species rule level: trainer changes", iv.check_species_rules(block))

    def test_each_stage_names_the_species_later_steps_read(self):
        self.assertEqual(iv.stage_species("create", 1, 2), (2, 2, 2))
        self.assertEqual(iv.stage_species("held_item", 1, 2), (2, 2, 1))
        self.assertEqual(iv.stage_species("adjustment", 1, 2), (1, 2, 1))
        self.assertEqual(iv.stage_species("final", 1, 2), (1, 1, 1))

    def test_trainer_level_rows_name_the_target_level_and_gated_difficulties(self):
        self.assertEqual(self.changes(5, 10), [
            {"species": 11, "rule": "level", "level": 31, "difficulties": [2, 3]}])
        self.assertEqual(self.changes(5, 10, spread=0), [])
        self.assertEqual(self.changes(6, 10), [])
        self.assertEqual(self.rules.counts["level"], {"members": 2, "changed": 1})

    def test_held_items_select_forms_by_table_target_and_parameter(self):
        self.assertEqual(self.changes(1, 20, item=1), [
            {"species": 25, "rule": "form", "difficulties": [0, 1, 2, 3]}])
        self.assertEqual(self.changes(1, 20, item=1, flags=1), [])
        self.assertEqual(self.changes(1, 23, item=3)[0]["species"], 24)
        self.assertEqual(self.changes(1, 23), [])
        for species, item, message in ((20, 2, "unmodeled form item"), (21, 0, "map-dependent form")):
            with self.subTest(species=species), self.assertRaisesRegex(iv.TrainerIvError, message):
                self.changes(1, species, item=item)
        self.rules.abilities[20] = [167, 1]
        with self.assertRaisesRegex(iv.TrainerIvError, "form ability unresolved"):
            self.changes(1, 20, item=1)

    def test_minior_cores_are_random_after_the_nature_step_else_fixed(self):
        self.assertEqual([row["species"] for row in self.changes(1, 33, spread=1)], [31, 32, 33])
        self.assertEqual({row["rule"] for row in self.changes(1, 33, spread=1)}, {"core-random"})
        # Name bytes 1+2, species bytes 5:
        # (0x88 ^ 2) + (8 << 8) is 0x88A,
        # core 0x88A % 3.
        fixed = self.changes(1, 30, spread=2)
        self.assertEqual(fixed, [{"species": [31, 32, 33][0x88A % 3], "rule": "core-fixed", "difficulties": [0, 1, 2, 3]}])
        with self.assertRaisesRegex(iv.TrainerIvError, "partner personality"):
            self.changes(1, 30, spread=0, kind="partners")


class ScaledEvolutionTest(unittest.TestCase):
    def setUp(self):
        self.block = {"difficulties": ["a", "b", "c", "d"], "species_rules": [scaled_rule()]}
        data = bytearray(0x100)
        data[0x08:0x0C] = (0x080000A0).to_bytes(4, "little")
        # Species 1 levels into 2 at 20.
        # Species 2 takes an item into 3.
        data[0xB0:0xB6] = bytes([4, 0, 20, 0, 2, 0])
        data[0xC0:0xC6] = bytes([7, 0, 99, 0, 3, 0])
        data[0xE0:0xE6] = bytes([0, 0, 15, 0, 30, 0])
        self.data = bytes(data)

    def rows(self, commands, class_id=1, flags=0, levels=(10, 12), settings=None, options=None, role="opponent",
             item=None):
        party = [{"index": index, "species": 1, "level": level, "item": item} for index, level in enumerate(levels)]
        encounters = [{"variants": [{"record": 4, "role": role, "settings": settings}],
                       "commands": [{"script": f"0x{index:X}", "mode": mode} for index, mode in enumerate(commands)]}]
        rules = iv.SpeciesRules(self.block, self.data, FakeResolver([]), [], {}, set(), encounters, options)
        trainer = {"id": 4, "class_id": class_id, "party_flags": flags, "party": party, "map_section": [3]}
        return rules.changes(trainer, party[1])

    def test_complete_rule_passes_and_incomplete_rules_fail(self):
        self.assertEqual(iv.check_species_rules(self.block), [])
        for field in ("modes", "evolutions", "methods", "badge_levels", "labels", "custom_moves_flag", "keep_items",
                      "party_level_penalties", "raised_level_penalty"):
            with self.subTest(field=field):
                block = copy.deepcopy(self.block)
                block["species_rules"][0].pop(field)
                self.assertEqual(iv.check_species_rules(block), ["species rule scaled: scaled evolution inputs"])
        block = copy.deepcopy(self.block)
        block["species_rules"][0]["battles"][0]["command"] = 5
        self.assertTrue(iv.check_species_rules(block))

    def test_level_bounds_follow_party_size_offsets_and_difficulty(self):
        rules = iv.SpeciesRules(self.block, self.data, FakeResolver([]), [], {}, set(), [])
        for size, maximum in enumerate([97, 95, 94, 93, 92, 92], 1):
            party = [member(level=15) for _ in range(size)]
            battle = dict(trainer(), party=party, party_size=size)
            ranges = rules.level_ranges(battle, party[0])
            self.assertEqual(ranges, {"0": {"min": 15, "max": maximum}, "1": {"min": 15, "max": 15},
                                      "2": {"min": 15, "max": maximum}, "3": {"min": 15, "max": 98}})
        party = [member(level=15), member(level=17)]
        battle = dict(trainer(), party=party, party_size=2)
        self.assertEqual(rules.level_ranges(battle, party[1])["0"], {"min": 17, "max": 97})
        self.assertEqual(rules.level_ranges(battle, party[1])["3"], {"min": 17, "max": 100})
        for fields in ({"class_id": 2}, {"party_flags": 1}):
            self.assertEqual(rules.level_ranges(dict(battle, **fields), party[0])["0"], {"min": 15, "max": 100})
        for fields in ({"id": 9}, {"map_section": [0x89]}):
            self.assertEqual(rules.level_ranges(dict(battle, **fields), party[0])["1"], {"min": 15, "max": 95})
        party[0]["level"] = party[1]["level"] = 100
        self.assertTrue(all(row == {"min": 100, "max": 100} for row in rules.level_ranges(battle, party[0]).values()))

    def test_generic_parties_need_the_badge_minimum_outside_insane(self):
        self.assertEqual(self.rows([13]), [
            {"species": 2, "rule": "scaled", "level": 20, "difficulties": [0, 2], "last": 44, "badges": 1},
            {"species": 3, "rule": "scaled", "level": 45, "difficulties": [0, 2], "badges": 1},
            {"species": 2, "rule": "scaled", "level": 20, "difficulties": [3], "last": 44},
            {"species": 3, "rule": "scaled", "level": 45, "difficulties": [3]}])
        self.assertEqual(self.rows([13], levels=(15, 16))[0]["badges"], 2)
        self.assertEqual([row["difficulties"] for row in self.rows([13], levels=(30, 31))], [[3], [3]])

    def test_bosses_and_custom_move_parties_evolve_on_any_raise(self):
        for fields in ({"class_id": 2}, {"flags": 1}):
            with self.subTest(fields=fields):
                rows = self.rows([13], **fields)
                self.assertEqual([(row["species"], row["level"], row["difficulties"]) for row in rows],
                                 [(2, 20, [0, 2, 3]), (3, 45, [0, 2, 3])])
                self.assertFalse(any("badges" in row for row in rows))

    def test_a_kept_item_blocks_the_evolution(self):
        self.assertEqual(self.rows([13], flags=2, item=7), [])
        self.assertEqual(len(self.rows([13], flags=2, item=8)), 4)

    def test_listed_level_above_the_threshold_needs_a_raise(self):
        self.assertEqual([row["level"] for row in self.rows([13], class_id=2, levels=(10, 25))], [26, 45])

    def test_battle_modes_partners_and_served_settings(self):
        self.assertEqual(self.rows([0]), [])
        self.assertEqual(self.rows([13], role="partner"), [])
        self.assertEqual({row["battle"] for row in self.rows([0, 5])}, {"rematch"})
        with self.assertRaisesRegex(iv.TrainerIvError, "battle condition undeclared"):
            self.rows([0, 13])
        self.assertEqual({tuple(row["difficulties"]) for row in self.rows([13], settings=["d"])}, {(3,)})
        self.assertEqual(self.rows([13], settings=["b"]), [])
        self.block["species_rules"][0]["unscaled_sections"] = [3]
        with self.assertRaisesRegex(iv.TrainerIvError, "scaling exception not modeled"):
            self.rows([13], settings=["b"])

    def test_species_variants_evolve_from_the_selected_species(self):
        rows = self.rows([13], class_id=2, options={(4, 1): [1, 2]})
        self.assertEqual([(row["from"], row["species"], row["level"]) for row in rows],
                         [(1, 2, 20), (1, 3, 45), (2, 3, 45)])
        self.assertEqual(rows[0]["last"], 44)
