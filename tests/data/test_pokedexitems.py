from test_roots import TOOLKIT_ROOT
"""Numeric item and TM/HM extraction."""
import hashlib
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))
from project_fixture import rom_evidence
from gen3tl.data import pokedexdata as pd  # noqa: E402
from gen3tl.data import pokedexitems as pi  # noqa: E402

BASE = 0x100
TM_BASE = 0x300


def item_block() -> dict:
    return {
        "base": BASE, "stride": 44, "count": 3, "evidence": "synthetic item records",
        "fields": {
            "price": {"offset": 16, "width": 2}, "held_effect": 18,
            "hold_effect_param": 19, "description": {"offset": 20, "width": 4},
            "pocket": 26, "battle_usage": 32,
        },
        "tm_moves": {"base": TM_BASE, "stride": 2, "count": 3,
                     "ranges": [[0, 1, 0], [1, 1, 2], [2, 1, 1]],
                     "evidence": "synthetic TM/HM ranges"},
    }


def rom_bytes() -> bytes:
    data = bytearray(0x400)
    for item, (price, pocket, battle) in enumerate(((200, 3, 0), (0, 2, 0), (3000, 4, 1))):
        at = BASE + item * 44
        data[at + 16:at + 18] = price.to_bytes(2, "little")
        data[at + 18], data[at + 19], data[at + 26], data[at + 32] = 7, 2, pocket, battle
        data[at + 20:at + 24] = (0x08000300 + item * 0x40).to_bytes(4, "little")
    for index, move in enumerate((264, 19, 337)):
        data[TM_BASE + index * 2:TM_BASE + index * 2 + 2] = move.to_bytes(2, "little")
    return bytes(data)


def sale_rule() -> dict:
    return {"divisor": 2, "rounding": "floor", "minimum_price": 1, "pockets": [1, 2, 3, 5],
            "evidence": "synthetic shop price and pocket checks"}


class ItemDataTest(unittest.TestCase):
    def test_extracts_base_sell_prices_only_for_declared_eligible_items(self):
        block = item_block()
        block["sell_price"] = sale_rule()
        self.assertEqual(pi.check(block), [])
        records = pi.build(block, rom_bytes())
        self.assertEqual([r["sell_price"] for r in records], [100, None, None])
        self.assertEqual([r["price"] for r in records], [200, 0, 3000])
        for price, pocket, expected in ((201, 1, 100), (1, 3, 0), (20, 5, 10),
                                        (3000, 2, 1500), (20, 0, None), (65535, 3, 32767),
                                        (0, 1, None), (3000, 4, None)):
            with self.subTest(price=price, pocket=pocket):
                data = bytearray(rom_bytes())
                data[BASE + 16:BASE + 18] = price.to_bytes(2, "little")
                data[BASE + 26] = pocket
                self.assertEqual(pi.build(block, bytes(data))[0]["sell_price"], expected)
        block["sell_price"].update(divisor=3, minimum_price=100, pockets=[4])
        self.assertEqual([r["sell_price"] for r in pi.build(block, rom_bytes())], [None, None, 1000])

    def test_missing_sale_rule_or_unreadable_fields_do_not_invent_prices(self):
        self.assertTrue(all("sell_price" not in r for r in pi.build(item_block(), rom_bytes())))
        block = item_block()
        block["sell_price"] = sale_rule()
        for end in (BASE + 17, BASE + 26):
            with self.subTest(end=end):
                self.assertTrue(all(r["sell_price"] is None for r in pi.build(block, rom_bytes()[:end])))

    def test_sale_rules_require_evidence_and_supported_arithmetic(self):
        for key, values in (("evidence", ["", " "]), ("divisor", [0, -1, True, 2.5, "2"]),
                            ("minimum_price", [-1, False, "1"]), ("rounding", ["ceil", None]),
                            ("pockets", [[], "3", [False], [-1], [256]])):
            for value in values:
                with self.subTest(key=key, value=value):
                    block = item_block()
                    block["sell_price"] = dict(sale_rule(), **{key: value})
                    self.assertEqual(pi.check(block), [f"sell_price {key}"])
        block = item_block()
        block["sell_price"] = None
        self.assertEqual(pi.check(block), ["sell_price block"])

    def test_dataset_retains_sale_evidence_and_rejects_missing_or_wrong_prices(self):
        from test_pokedexdata import block
        items = item_block()
        items["sell_price"] = sale_rule()
        profile = block(items=items)
        doc = pd.build(profile, rom_bytes(), {"id": "demo"})
        self.assertEqual(doc["provenance"]["item_sell_price"],
                         {"offset": "", "evidence": sale_rule()["evidence"]})
        self.assertEqual(pd.verify_doc(doc, profile, "demo", doc["rom"]["sha1"]), [])
        for index, value in ((0, None), (0, 101), (0, True), (1, 0), (2, 1500)):
            with self.subTest(index=index, value=value):
                stale = dict(doc, items=[dict(r) for r in doc["items"]])
                stale["items"][index]["sell_price"] = value
                self.assertIn("item sell prices: regenerate the dataset with gen3tl data pokedex",
                              pd.verify_doc(stale, profile, "demo", doc["rom"]["sha1"]))
        del doc["items"][1]["sell_price"]
        self.assertIn("item sell prices: regenerate the dataset with gen3tl data pokedex",
                      pd.verify_doc(doc, profile, "demo", doc["rom"]["sha1"]))

    def test_extracts_numeric_fields_descriptions_and_tm_moves(self):
        records = pi.build(item_block(), rom_bytes())
        self.assertEqual(len(records), 3)
        first = records[0]
        self.assertEqual(first["name"], "items:0")
        self.assertEqual(first["description"], "s0000300")
        self.assertEqual((first["price"], first["held_effect"], first["hold_effect_param"]),
                         (200, 7, 2))
        self.assertEqual((first["pocket"], first["battle_usage"]), (3, 0))
        self.assertEqual((first["tm_index"], first["taught_move"]), (0, 264))
        self.assertEqual((records[1]["tm_index"], records[1]["taught_move"]), (2, 337))
        self.assertEqual((records[2]["tm_index"], records[2]["taught_move"]), (1, 19))
        self.assertEqual(records[2]["pocket"], 4)
        self.assertEqual(pi.build(item_block(), rom_bytes()), records)

    def test_resolves_long_name_pointers_to_catalog_ids(self):
        block = item_block()
        block["long_name_pointers"] = True
        data = bytearray(rom_bytes())
        data[BASE + 44:BASE + 48] = (0x08EB9170).to_bytes(4, "little")
        records = pi.build(block, bytes(data))
        self.assertEqual(records[0]["name"], "items:0")
        self.assertEqual(records[1]["name"], "s0EB9170")
        self.assertEqual(records[2]["name"], "items:2")

    def test_items_between_tm_ranges_have_no_taught_move(self):
        block = item_block()
        block["count"] = 4
        records = pi.build(block, rom_bytes())
        self.assertIsNone(records[3]["tm_index"])
        self.assertIsNone(records[3]["taught_move"])

    def test_names_effects_without_changing_the_numeric_facts(self):
        block = item_block()
        block["held_effects"] = {
            "evidence": "synthetic effect constants",
            "names": {"7": "Restore {n} PP"},
        }
        block["battle_usages"] = {
            "evidence": "synthetic battle functions", "field": {"offset": 36, "width": 4},
            "names": {"0x08000101": "Healing or status cure", "0x08000121": "Restore PP"},
        }
        data = bytearray(rom_bytes())
        for item, pointer in enumerate((0x08000101, 0x08000121, 0x08000141)):
            data[BASE + item * 44 + 32] = 1
            data[BASE + item * 44 + 36:BASE + item * 44 + 40] = pointer.to_bytes(4, "little")
        data[BASE + 88 + 18] = 255
        first, second, unknown = pi.build(block, bytes(data))
        self.assertEqual(pi.check(block), [])
        self.assertEqual((first["held_effect"], first["hold_effect_param"], first["held_effect_key"]),
                         (7, 2, "Restore {n} PP"))
        self.assertEqual(first["battle_usage"], second["battle_usage"])
        self.assertEqual(first["battle_usage_key"], "Healing or status cure")
        self.assertEqual(second["battle_usage_key"], "Restore PP")
        self.assertEqual(unknown["battle_use_function"], 0x08000141)
        self.assertNotIn("held_effect_key", unknown)
        self.assertNotIn("battle_usage_key", unknown)

    def test_resolves_type_parameters_variants_and_guarded_item_overrides(self):
        block = item_block()
        block["held_effects"] = {
            "evidence": "synthetic parameter meanings",
            "names": {"7": "Boost moves of type: {type}"}, "type_parameters": [7],
            "variants": {"7": {"2": "Boost Speed; lock one move"}},
            "overrides": {"1": {"effect": 7, "key": "Enable a species-specific Z-Move"},
                          "2": {"effect": 99, "key": "Wrong effect"}},
        }
        data = bytearray(rom_bytes())
        data[BASE + 19] = 23
        first, overridden, variant = pi.build(block, bytes(data))
        self.assertEqual(pi.check(block), [])
        self.assertEqual(first["held_effect_param_name"], "types:23")
        self.assertEqual(first["held_effect_key"], "Boost moves of type: {type}")
        self.assertEqual(overridden["held_effect_key"], "Enable a species-specific Z-Move")
        self.assertNotIn("held_effect_param_name", overridden)
        self.assertEqual(variant["held_effect_key"], "Boost Speed; lock one move")

    def amount_block(self):
        block = item_block()
        block["held_effects"] = {
            "evidence": "synthetic parameter meanings", "names": {"7": "Restore {n} PP"},
            "variants": {"7": {"3": "Boost Speed; lock one move"}},
            "overrides": {"1": {"effect": 7, "key": "Restore a quarter of maximum HP"}},
            "amounts": {
                "7": {"key": "Restore {n} PP at 1/{d} PP", "values": {"d": 4},
                      "evidence": "synthetic threshold", "rom": [rom_evidence(BASE + 18, "0702")]},
                "7.3": {"key": "Boost Speed: ×{x}; lock one move", "values": {"x": 1.5},
                        "evidence": "synthetic multiplier", "rom": [rom_evidence(BASE + 16, "c800")]},
            },
        }
        data = bytearray(rom_bytes())
        data[BASE + 2 * 44 + 19] = 3
        return block, bytes(data)

    def test_amounts_follow_effect_and_parameter_and_skip_item_overrides(self):
        block, data = self.amount_block()
        self.assertEqual(pi.check(block), [])
        first, overridden, variant = pi.build(block, data)
        self.assertEqual(first["held_effect_amount"], {"key": "Restore {n} PP at 1/{d} PP", "values": {"d": 4}})
        self.assertEqual(first["held_effect_key"], "Restore {n} PP")
        self.assertNotIn("held_effect_amount", overridden)
        block["held_effects"]["amounts"]["item.1"] = {"key": "Restore a quarter of maximum HP at 1/{d} HP", "values": {"d": 2},
                                                     "evidence": "synthetic item check", "rom": [rom_evidence(BASE + 16, "c8")]}
        self.assertEqual(pi.check(block), [])
        self.assertEqual(pi.build(block, data)[1]["held_effect_amount"]["values"], {"d": 2})
        del block["held_effects"]["amounts"]["item.1"]
        self.assertEqual(variant["held_effect_amount"]["values"], {"x": 1.5})
        self.assertEqual(variant["held_effect_key"], "Boost Speed; lock one move")
        block["held_effects"]["amounts"]["7"]["rom"][0]["sha256"] = hashlib.sha256(bytes.fromhex("0703")).hexdigest()
        with self.assertRaisesRegex(ValueError, "held effect 7 amount evidence differs"):
            pi.build(block, data)

    def test_amounts_reject_malformed_ids_labels_values_and_proofs(self):
        valid = {"key": "Boost: ×{x}", "values": {"x": 1.5}, "evidence": "synthetic", "rom": [rom_evidence(0, "00")]}
        cases = (("x", valid, "id"), ("256", valid, "id"), ("item.x", valid, "id"), ("7.01", valid, "id"), ("7", None, "table"),
                 ("7", dict(valid, values={}), "key"), ("7", dict(valid, values={"x": 0}), "values"),
                 ("7", dict(valid, values={"x": True}), "values"), ("7", dict(valid, values={"n": 2}), "values"),
                 ("7", dict(valid, key="Boost"), "key"), ("7", dict(valid, key="Boost {y}"), "key"),
                 ("7", dict(valid, evidence=" "), "evidence"), ("7", dict(valid, rom=[]), "rom"),
                 ("7", dict(valid, rom=[dict(rom_evidence(0, "00"), sha256="invalid")]), "rom"),
                 *(("7", dict(valid, conditions=value), "conditions")
                   for value in (None, "Only in battle", {}, [None], [" "], ["At {n} HP"])))
        for number, spec, problem in cases:
            block = item_block()
            block["held_effects"] = {"evidence": "synthetic", "names": {"7": "Boost"}, "amounts": {number: spec}}
            self.assertIn(f"held_effects amounts {number} {problem}", pi.check(block), (number, spec))

    def test_amount_conditions_keep_precedence_and_require_matching_rom_proofs(self):
        block, data = self.amount_block()
        spec = block["held_effects"]["amounts"]["7"]
        spec.update(key="Double the holder's stat.", values={},
                    conditions=["No bonus for a scaled holder.", "An unevolved holder keeps the bonus."])
        self.assertEqual(pi.check(block), [])
        first, overridden, variant = pi.build(block, data)
        self.assertEqual(first["held_effect_amount"], {
            "key": spec["key"], "values": {}, "conditions": spec["conditions"]})
        self.assertNotIn("held_effect_amount", overridden)
        self.assertNotIn("conditions", variant["held_effect_amount"])
        block["held_effects"]["amounts"]["item.1"] = dict(spec, conditions=["Only at night."])
        self.assertEqual(pi.build(block, data)[1]["held_effect_amount"]["conditions"], ["Only at night."])
        changed = bytearray(data)
        changed[BASE + 19] ^= 1
        with self.assertRaisesRegex(ValueError, "held effect 7 amount evidence differs"):
            pi.build(block, bytes(changed))

    def test_dataset_rejects_missing_reordered_and_stale_conditions(self):
        from test_pokedexdata import block as profile_block
        items, data = self.amount_block()
        conditions = ["Only in weight-based battles.", "Heavier holders move faster."]
        items["held_effects"]["amounts"]["7"]["conditions"] = conditions
        profile = profile_block(items=items)
        doc = pd.build(profile, data, {"id": "demo"})
        self.assertEqual(pd.verify_doc(doc, profile, "demo", doc["rom"]["sha1"]), [])
        for replacement in (None, [], conditions[::-1], ["Always active."]):
            with self.subTest(conditions=replacement):
                stale = dict(doc, items=[dict(row) for row in doc["items"]])
                amount = dict(stale["items"][0]["held_effect_amount"])
                if replacement is None:
                    del amount["conditions"]
                else:
                    amount["conditions"] = replacement
                stale["items"][0]["held_effect_amount"] = amount
                self.assertIn("item held effect amounts: regenerate the dataset with gen3tl data pokedex",
                              pd.verify_doc(stale, profile, "demo", doc["rom"]["sha1"]))

    def test_dataset_rejects_missing_or_stale_amounts(self):
        from test_pokedexdata import block as profile_block
        items, data = self.amount_block()
        profile = profile_block(items=items)
        doc = pd.build(profile, data, {"id": "demo"})
        self.assertEqual(pd.verify_doc(doc, profile, "demo", doc["rom"]["sha1"]), [])
        for index, value in ((0, None), (1, {"key": "Restore {n} PP at 1/{d} PP", "values": {"d": 4}}),
                             (2, {"key": "Boost Speed: ×{x}; lock one move", "values": {"x": 2}})):
            stale = dict(doc, items=[dict(r) for r in doc["items"]])
            stale["items"][index]["held_effect_amount"] = value
            self.assertIn("item held effect amounts: regenerate the dataset with gen3tl data pokedex",
                          pd.verify_doc(stale, profile, "demo", doc["rom"]["sha1"]))

    def level_block(self):
        block, data = self.amount_block()
        amount = block["held_effects"]["amounts"]["7.3"]
        amount.update(levels=[{"level": level, "values": {"x": 1 + level / 2}} for level in range(1, 4)],
                      upgrade_sources=[{"key": "Win the training battle.", "map_sections": [8, 9]}],
                      conditions=["Only in battle."])
        amount["rom"].append(rom_evidence(0x380, "00"))
        return block, data

    def test_levels_keep_variant_precedence_conditions_and_proofs(self):
        block, data = self.level_block()
        self.assertEqual(pi.check(block), [])
        first, overridden, upgraded = pi.build(block, data)
        self.assertNotIn("levels", first["held_effect_amount"])
        self.assertNotIn("held_effect_amount", overridden)
        amount = upgraded["held_effect_amount"]
        self.assertEqual(amount["levels"], [{"level": 1, "values": {"x": 1.5}},
                                          {"level": 2, "values": {"x": 2}},
                                          {"level": 3, "values": {"x": 2.5}}])
        self.assertEqual(amount["conditions"], ["Only in battle."])
        self.assertEqual(amount["upgrade_sources"], [{"key": "Win the training battle.", "map_sections": [8, 9]}])
        changed = bytearray(data)
        changed[0x380] = 1
        with self.assertRaisesRegex(ValueError, "held effect 7.3 amount evidence differs"):
            pi.build(block, bytes(changed))

    def test_levels_reject_gaps_bad_values_and_incomplete_sources(self):
        cases = {
            "levels": [None, [], [None], [{"level": True, "values": {"x": 1.5}}],
                       [{"level": 0, "values": {"x": 1.5}}], [{"level": 1, "values": {}}],
                       [{"level": 1, "values": {"x": 2}}], [{"level": 1, "values": {"x": float("inf")}}],
                       [{"level": 1, "values": {"x": True}}],
                       [{"level": 1, "values": {"x": 1.5}}, {"level": 3, "values": {"x": 2}}]],
            "upgrade_sources": [None, [], [None], [{"key": " ", "map_sections": [8]}],
                                [{"key": "At {n}", "map_sections": [8]}], [{"key": "Win", "map_sections": []}],
                                [{"key": "Win", "map_sections": [-1]}], [{"key": "Win", "map_sections": [True]}],
                                [{"key": "Win", "map_sections": [8, 8]}]],
        }
        for field, replacements in cases.items():
            for replacement in replacements:
                with self.subTest(field=field, value=replacement):
                    block, _ = self.level_block()
                    block["held_effects"]["amounts"]["7.3"][field] = replacement
                    self.assertIn(f"held_effects amounts 7.3 {field}", pi.check(block))

    def test_dataset_rejects_missing_reordered_and_stale_levels_and_sources(self):
        from test_pokedexdata import block as profile_block
        items, data = self.level_block()
        profile = profile_block(items=items)
        doc = pd.build(profile, data, {"id": "demo"})
        self.assertEqual(pd.verify_doc(doc, profile, "demo", doc["rom"]["sha1"]), [])
        for field in ("levels", "upgrade_sources"):
            for change in ("remove", "empty", "change"):
                stale = copy.deepcopy(doc)
                amount = stale["items"][2]["held_effect_amount"]
                if change == "remove":
                    del amount[field]
                elif change == "empty":
                    amount[field] = []
                elif field == "levels":
                    amount[field].reverse()
                else:
                    amount[field][0]["map_sections"] = [10]
                self.assertIn("item held effect amounts: regenerate the dataset with gen3tl data pokedex",
                              pd.verify_doc(stale, profile, "demo", doc["rom"]["sha1"]))

    def test_does_not_name_an_unusable_battle_function(self):
        block = item_block()
        block["battle_usages"] = {
            "evidence": "synthetic battle functions", "field": {"offset": 36, "width": 4},
            "names": {"0x08000101": "Restore PP"},
        }
        data = bytearray(rom_bytes())
        data[BASE + 36:BASE + 40] = (0x08000101).to_bytes(4, "little")
        self.assertNotIn("battle_usage_key", pi.build(block, bytes(data))[0])

    def test_rejects_unproven_or_invalid_effect_mappings(self):
        cases = [
            ("held_effects", None, "block"),
            ("held_effects", {"names": {}}, "evidence"),
            ("held_effects", {"evidence": "synthetic", "names": {"256": "Unknown"}}, "names"),
            ("held_effects", {"evidence": "synthetic", "names": {"7": " "}}, "names"),
            ("held_effects", {"evidence": "synthetic", "names": {}, "type_parameters": [True]}, "type_parameters"),
            ("held_effects", {"evidence": "synthetic", "names": {}, "variants": {"7": {"x": "PP"}}}, "variants"),
            ("held_effects", {"evidence": "synthetic", "names": {}, "overrides": {"1": {"key": "PP"}}}, "overrides"),
            ("battle_usages", {"evidence": "synthetic", "names": {"0": "PP"}, "field": {"offset": 36, "width": 4}}, "names"),
            ("battle_usages", {"evidence": "synthetic", "names": {}, "field": {"offset": 42, "width": 4}}, "field"),
        ]
        for name, facts, problem in cases:
            with self.subTest(name=name, facts=facts):
                block = item_block()
                block[name] = facts
                self.assertEqual(pi.check(block), [f"{name} {problem}"])

    def test_rejects_missing_proof_and_incomplete_tm_ranges(self):
        block = item_block()
        del block["evidence"]
        del block["fields"]["price"]
        block["tm_moves"]["ranges"].pop()
        self.assertEqual(pi.check(block),
                         ["evidence", "fields price", "tm_moves ranges cover"])

    def test_exchange_rewards_link_costs_without_relabeling_other_gifts(self):
        place = {"map": "0.0", "map_section": 7}
        use = {"kind": "exchange", "takes": [{"item": 1, "quantity": 4}, {"item": 2, "quantity": 3}],
               "gives": [{"item": 3, "quantity": 2}], "speaker": "s0000100",
               "places": [{**place, "scripts": ["0x0000500"]}]}
        gift = {"kind": "gift", **place, "script": "0x0000500", "quantity": 2, "x": 1, "y": 2}
        other = {**gift, "script": "0x0000600"}
        elsewhere = {**gift, "map": "0.1"}
        rows = [{"id": 1, "uses": [use]}, {"id": 2, "uses": [use]},
                {"id": 3, "sources": [gift, other, elsewhere]}]
        pi.join_rewards(rows)
        sources = rows[2]["sources"]
        self.assertEqual(len(sources), 3)
        self.assertIn(other, sources)
        self.assertIn(elsewhere, sources)
        exchange = next(source for source in sources if source["kind"] == "exchange")
        self.assertEqual(exchange, {**gift, "kind": "exchange", "takes": use["takes"],
                                    "gives": use["gives"], "speaker": use["speaker"]})

    def test_exchange_tables_and_variable_rewards_become_sources(self):
        use = {"kind": "exchange", "takes": [{"item": 1, "quantity": None}],
               "gives": [{"item": 2}, {"item": 3}], "choice": True,
               "places": [{"map": "0.0", "map_section": 7, "scripts": ["0x0000500"]}]}
        rows = [{"id": 1, "uses": [use]}, {"id": 2}, {"id": 3}]
        pi.join_rewards(rows)
        expected = {"kind": "exchange", "map": "0.0", "map_section": 7, "choice": True,
                    "takes": use["takes"], "gives": use["gives"]}
        self.assertEqual(rows[1]["sources"], [expected])
        self.assertEqual(rows[2]["sources"], [expected])

    def test_prize_scripts_group_choices_quantities_and_all_positions(self):
        place = {"kind": "gift", "map": "0.0", "map_section": 7, "runtime_map": "0.0",
                 "script": "0x0000500"}
        rows = [{"id": 1, "sources": [{**place, "quantity": 2, "x": 1, "y": 2},
                                      {**place, "quantity": 3, "x": 1, "y": 2},
                                      {**place, "quantity": 2, "x": 2, "y": 2}]},
                {"id": 2, "sources": [{**place, "quantity": 1, "x": 1, "y": 2}]},
                {"id": 3, "sources": [{**place, "script": "0x0000600", "quantity": 1}]}]
        original = copy.deepcopy(rows[2])
        pi.join_rewards(rows)
        expected = {**place, "choices": [{"item": 1, "quantity": 2}, {"item": 1, "quantity": 3},
                                         {"item": 2, "quantity": 1}],
                    "positions": [{"x": 1, "y": 2}, {"x": 2, "y": 2}]}
        self.assertEqual(rows[0]["sources"], [expected])
        self.assertEqual(rows[1]["sources"], [expected])
        self.assertEqual(rows[2], original)

    def test_carries_deduplicated_shop_sources_with_provenance(self):
        items = item_block()
        items["sources"] = {
            "evidence": "synthetic shop and map events",
            "shops": [{"map": "2.3", "map_section": 90, "offset": 0x500, "list": 0x600,
                       "items": [1, 2]},
                      {"map": "2.3", "map_section": 90, "offset": 0x501, "list": 0x601,
                       "items": [1, 2]}],
        }
        self.assertEqual(pi.check(items), [])
        records = pi.build(items, rom_bytes())
        self.assertEqual(records[0]["sources"], [])
        for record in records[1:]:
            self.assertEqual(record["sources"], [{"kind": "shop", "map": "2.3", "map_section": 90}])
        block = {
            "name_table": "species",
            "species": {"base": 0, "stride": 1, "count": 1, "first": 0,
                        "evidence": "synthetic", "fields": {"hp": 0}},
            "items": items,
        }
        doc = pd.build(block, rom_bytes(), {"id": "demo"})
        self.assertEqual(doc["provenance"]["item_sources"],
                         {"offset": "", "evidence": "synthetic shop and map events"})
        self.assertEqual(pd.verify_doc(doc, block, "demo", doc["rom"]["sha1"]), [])
        stale = dict(doc, items=[dict(row) for row in doc["items"]])
        del stale["items"][0]["sources"]
        self.assertIn("item sources: regenerate the dataset with gen3tl data pokedex",
                      pd.verify_doc(stale, block, "demo", doc["rom"]["sha1"]))

    def test_computes_and_deduplicates_mining_chances_per_draw_table(self):
        items = item_block()
        items["count"] = 4
        items["mining"] = {
            "base": 0x380, "stride": 24, "count": 3,
            "item": {"offset": 0, "width": 2}, "tiers": [[1, 3]],
            "draws": [0x3D0, 0x3DA, 0x3E4], "draw_count": 10,
            "evidence": "synthetic mining draw tables",
        }
        data = bytearray(rom_bytes())
        for index in range(3):
            data[0x380 + index * 24:0x382 + index * 24] = (index + 1).to_bytes(2, "little")
        data[0x3D0:0x3DA] = bytes([0, 0, 0, 1, 1, 1, 1, 1, 1, 1])
        data[0x3DA:0x3E4] = bytes([0, 0, 1, 1, 1, 1, 1, 1, 1, 1])
        data[0x3E4:0x3EE] = data[0x3D0:0x3DA]
        self.assertEqual(pi.check(items), [])
        records = pi.build(items, bytes(data))
        self.assertEqual(records[0]["sources"], [])
        self.assertEqual(records[1]["sources"], [
            {"kind": "mining", "chance": 30.0}, {"kind": "mining", "chance": 20.0}])
        for record in records[2:]:
            self.assertEqual(record["sources"], [
                {"kind": "mining", "chance": 35.0}, {"kind": "mining", "chance": 40.0}])
        items["mining"] = dict(items["mining"], draws=[])
        self.assertEqual(pi.check(items), ["mining draws"])

    def test_rejects_unproven_or_malformed_item_source_facts(self):
        items = item_block()
        items["sources"] = {"shops": []}
        self.assertEqual(pi.check(items), ["sources evidence"])
        items["sources"] = {
            "evidence": "synthetic",
            "shops": [{"map": "2.3", "map_section": 90, "offset": 0x500, "list": 0x600,
                       "items": [3]}],
            "pickups": {"evidence": ""},
        }
        self.assertEqual(pi.check(items)[:3], ["sources shops[0]", "sources pickups evidence",
                                               "sources pickups item_var"])

    def test_integrates_with_the_dataset_and_verification(self):
        block = {
            "name_table": "species",
            "species": {"base": 0, "stride": 1, "count": 1, "first": 0,
                        "evidence": "synthetic", "fields": {"hp": 0}},
            "items": item_block(),
        }
        doc = pd.build(block, rom_bytes(), {"id": "demo"})
        self.assertEqual(len(doc["items"]), 3)
        self.assertEqual(doc["provenance"]["items"]["offset"], f"0x{BASE:06x}")
        self.assertEqual(doc["provenance"]["tm_moves"]["offset"], f"0x{TM_BASE:06x}")
        self.assertEqual(pd.verify_doc(doc, block, "demo", doc["rom"]["sha1"]), [])
        broken = dict(doc)
        del broken["items"]
        self.assertIn("items", pd.verify_doc(broken, block, "demo", doc["rom"]["sha1"])[0])


if __name__ == "__main__":
    unittest.main()
