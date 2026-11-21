from test_roots import TOOLKIT_ROOT
"""Pokedexmoves data checks and fixtures."""

import sys
from pathlib import Path
import copy
import hashlib
import struct
import unittest

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))
from project_fixture import rom_evidence
from gen3tl.data import pokedexdata as pd
from gen3tl.data import pokedexmoves as pm
from pokedexmoves_support import BASE, DESCRIPTIONS, move_block, rom_bytes


def item_power_fixture():
    data = bytearray(rom_bytes())
    data[0x300:0x310] = bytes.fromhex("0500005006000b5a07001764fefefefe")
    table = {"base": 0x300, "stride": 4, "count": 3, "terminator": 0xFEFE,
             "fields": {"item": {"offset": 0, "width": 2}, "type": 2, "power": 3},
             "exclude": [6], "sha256": hashlib.sha256(data[0x300:0x310]).hexdigest()}
    return {"kind": "computed", "formula": "page:power:test", "evidence": "Synthetic item table reader", "item_tiers": table}, data


class MoveDataTest(unittest.TestCase):
    def test_effect_override_preserves_raw_id_and_checks_script_and_table(self):
        block = move_block()
        block["effect_overrides"] = {"1": {
            "effect": 0, "key": "defense_up_3", "evidence": "Synthetic move branch",
            "rom": [rom_evidence(0x300, "00")],
        }}
        self.assertEqual(pm.check(block), [])
        records = pm.build(block, rom_bytes())
        self.assertEqual((records[0]["effect_key"], records[1]["effect_key"]), ("hit", "defense_up_3"))
        self.assertEqual(records[1]["effect"], 0)
        data = bytearray(rom_bytes())
        data[0x300] = 1
        with self.assertRaisesRegex(ValueError, "move 1 effect evidence differs"):
            pm.build(block, data)
        data = bytearray(rom_bytes())
        data[BASE + 12] = 43
        with self.assertRaisesRegex(ValueError, "move 1 effect differs from override"):
            pm.build(block, data)

    def test_effect_overrides_require_valid_identity_key_and_evidence(self):
        rule = {"effect": 0, "key": "defense_up_3", "evidence": "Synthetic move branch",
                "rom": [rom_evidence(0x300, "00")]}
        for field, value in (("effect", True), ("effect", 99), ("key", "bad key"),
                             ("evidence", " "), ("rom", []), ("rom", [rom_evidence(-1, "00")]),
                             ("rom", [dict(rom_evidence(0, "00"), sha256="invalid")]), ("rom", [None])):
            with self.subTest(field=field, value=value):
                block = move_block()
                block["effect_overrides"] = {"1": {**rule, field: value}}
                self.assertIn(f"effect_overrides 1 {field}", pm.check(block))
        for number in ("-1", "01", "4", "x"):
            block = move_block()
            block["effect_overrides"] = {number: rule}
            self.assertIn(f"effect_overrides {number} id", pm.check(block))

    def test_dataset_verification_rejects_stale_effect_keys(self):
        block = {"name_table": "species",
                 "species": {"base": 0, "stride": 1, "count": 1, "first": 0,
                             "evidence": "synthetic", "fields": {"hp": 0}},
                 "moves": move_block()}
        block["moves"]["effect_overrides"] = {"1": {
            "effect": 0, "key": "defense_up_3", "evidence": "Synthetic move branch",
            "rom": [rom_evidence(0x300, "00")],
        }}
        doc = pd.build(block, rom_bytes(), {"id": "test"})
        doc["moves"][1]["effect_key"] = "hit"
        self.assertTrue(any("move 1 effect rule" in problem
                            for problem in pd.verify_doc(doc, block, "test", doc["rom"]["sha1"])))

    def test_power_classification_is_declared_independently_of_stored_power(self):
        block = move_block()
        block["power"] = {
            "1": {"kind": "fixed", "formula": "page:power:modifier", "evidence": "Synthetic modifier"},
            "2": {"kind": "computed", "formula": "page:power:computed", "evidence": "Synthetic formula"},
            "3": {"kind": "fixed", "base": 160, "formula": "page:power:constant", "evidence": "Synthetic constant"},
        }
        data = bytearray(rom_bytes())
        data[BASE + 12 + 1] = 1
        self.assertEqual(pm.check(block), [])
        records = pm.build(block, data)
        self.assertEqual((records[1]["power"], records[1]["power_kind"]), (1, "fixed"))
        self.assertEqual((records[2]["power"], records[2]["power_kind"]), (50, "computed"))
        self.assertEqual((records[3]["power"], records[3]["power_base"]), (60, 160))
        self.assertNotIn("power_kind", records[0])
        for kind in (None, "", "variable", True):
            block["power"]["1"]["kind"] = kind
            self.assertIn("power 1 kind", pm.check(block))
        for base in (None, 0, -1, True, "160", 160.5):
            block["power"]["3"]["base"] = base
            self.assertIn("power 3 base", pm.check(block))
        block["power"]["2"]["base"] = 160
        self.assertIn("power 2 base", pm.check(block))

    def test_item_power_reads_rom_fields_and_rejects_changed_or_truncated_tables(self):
        power, data = item_power_fixture()
        block = move_block()
        before = pm.build(block, data)
        block["power"] = {"1": power}
        self.assertEqual(pm.check(block), [])
        after = pm.build(block, data)
        self.assertEqual(after[1].pop("power_items"),
                         [{"item": 5, "type": 0, "power": 80}, {"item": 7, "type": 23, "power": 100}])
        after[1].pop("power_formula")
        after[1].pop("power_kind")
        self.assertEqual(after, before)
        for at in (0x300, 0x302, 0x303, 0x30C):
            changed = bytearray(data)
            changed[at] ^= 1
            with self.assertRaisesRegex(ValueError, "item power table evidence differs"):
                pm.build(block, changed)
        with self.assertRaisesRegex(ValueError, "item power table truncated"):
            pm._item_tiers(power["item_tiers"], data[:0x30F])

    def test_item_power_declarations_and_rows_are_validated(self):
        power, data = item_power_fixture()
        valid = power["item_tiers"]
        for key, value in (("base", -1), ("count", 0), ("count", True), ("stride", 0),
                           ("sha256", "invalid"), ("terminator", None), ("fields", None),
                           ("fields", dict(valid["fields"], power=4)),
                           ("fields", dict(valid["fields"], item={"offset": 0, "width": 2, "signed": True})),
                           ("exclude", "6"), ("exclude", [6, 6]), ("exclude", [True]), ("exclude", [{}])):
            with self.subTest(key=key, value=value):
                block = move_block()
                block["power"] = {"1": dict(power, item_tiers=dict(valid, **{key: value}))}
                self.assertTrue(any(p.startswith("power 1 item_tiers") for p in pm.check(block)))
        for table, message in ((dict(valid, terminator=0), "terminator differs"),
                               (dict(valid, exclude=[8]), "unknown exclusions")):
            with self.assertRaisesRegex(ValueError, message):
                pm._item_tiers(table, data)
        for at, value in ((0x304, 5), (0x303, 0)):
            changed = bytearray(data)
            changed[at] = value
            table = dict(valid, sha256=hashlib.sha256(changed[0x300:0x310]).hexdigest())
            with self.assertRaisesRegex(ValueError, "invalid rows"):
                pm._item_tiers(table, changed)

    def test_mechanics_export_only_page_keys_and_require_matching_rom(self):
        block = move_block()
        before = pm.build(block, rom_bytes())
        item = {"summary": "page:mechanics:demo:summary",
                "rows": [{"condition": "page:mechanics:demo:condition", "effect": "page:mechanics:demo:effect",
                          "notes": ["page:mechanics:demo:note"]}], "evidence": "Synthetic native reader",
                "rom": [rom_evidence(BASE + 12, "0028")]}
        block["mechanics"] = {"1": item}
        self.assertEqual(pm.check(block), [])
        after = pm.build(block, rom_bytes())
        mechanics = after[1].pop("mechanics")
        self.assertEqual(after, before)
        self.assertEqual(mechanics, {key: item[key] for key in ("summary", "rows")})
        item["rom"][0]["sha256"] = hashlib.sha256(bytes.fromhex("ff28")).hexdigest()
        with self.assertRaisesRegex(ValueError, "move 1 mechanics evidence differs"):
            pm.build(block, rom_bytes())

    def test_mechanics_reject_malformed_ids_rows_keys_and_proofs(self):
        valid = {"summary": "page:mechanics:demo:summary",
                 "rows": [{"condition": "page:mechanics:demo:condition", "effect": "page:mechanics:demo:effect"}],
                 "evidence": "Synthetic native reader", "rom": [rom_evidence(0, "00")]}
        for number in ("-1", "4", "01", "²", "name"):
            block = move_block()
            block["mechanics"] = {number: valid}
            self.assertIn(f"mechanics id {number}", pm.check(block))
        for field, value in (("summary", "Literal English"), ("rows", []), ("rows", "rows"),
                             ("rows", [None]), ("rows", [["page:mechanics:demo:condition"]]),
                             ("rows", [["page:mechanics:demo:condition", "English effect"]]),
                             ("rows", [dict(valid["rows"][0], notes="note")]),
                             ("rows", [dict(valid["rows"][0], notes=[None])]),
                             *[("evidence", value) for value in ("", " \t\n", False, 123, {}, None)],
                             ("rom", []), ("rom", [None]), ("rom", [rom_evidence(True, "00")]),
                             ("rom", [dict(rom_evidence(0, "00"), sha256="invalid")])):
            block = move_block()
            block["mechanics"] = {"1": dict(valid, **{field: value})}
            self.assertIn(f"mechanics 1 {field}", pm.check(block), (field, value))
        block["mechanics"] = {"1": dict(valid, rows=valid["rows"] * 2)}
        self.assertIn("mechanics 1 rows duplicates", pm.check(block))
        block["mechanics"] = {"1": None}
        self.assertIn("mechanics 1 table", pm.check(block))
        block["mechanics"] = []
        self.assertIn("mechanics table", pm.check(block))

    def environment_fixture(self):
        data = bytearray(rom_bytes())
        data[0x300:0x318] = bytes.fromhex("0d050000020000000c010000030000000041000003000000")
        table = {"base": 0x300, "stride": 8, "count": 3, "sha256": hashlib.sha256(data[0x300:0x318]).hexdigest(),
                 "fields": {"type": 0, "effect": 1, "move": {"offset": 4, "width": 2}},
                 "effects": {"5": "page:mechanics:demo:paralysis", "1": "page:mechanics:demo:sleep",
                             "65": "page:mechanics:demo:user"},
                 "rows": [{"index": 2, "environment": "page:mechanics:demo:place", "where": "page:mechanics:demo:where",
                           "place": 7, "sections": [7, 9], "maps": [{"map": "1.9", "section": 4}], "scripts": [3]},
                          {"index": 0, "environment": "page:mechanics:demo:first", "where": "page:mechanics:demo:any"}],
                 "evidence": "Synthetic environment readers", "rom": [rom_evidence(0x300, "0d05")]}
        block = move_block()
        block["environments"] = table
        block["mechanics"] = {str(move): {
            "summary": "page:mechanics:demo:summary", "environments": column, "evidence": "Synthetic reader",
            "rows": [{"condition": "page:mechanics:demo:condition", "effect": "page:mechanics:demo:effect"}],
            "rom": [rom_evidence(BASE, "00")]} for move, column in ((1, "effect"), (2, "move"), (3, "type"))}
        return block, bytes(data)

    def test_environment_columns_follow_declared_rows_and_rom_values(self):
        block, data = self.environment_fixture()
        self.assertEqual(pm.check(block), [])
        records = pm.build(block, data)
        place = {"environment": "page:mechanics:demo:place", "where": "page:mechanics:demo:where", "place": 7,
                 "sections": [7, 9], "maps": [{"map": "1.9", "section": 4}]}
        first = {"environment": "page:mechanics:demo:first", "where": "page:mechanics:demo:any"}
        self.assertEqual(records[1]["mechanics"]["environments"], {"column": "effect", "rows": [
            dict(place, value="page:mechanics:demo:user"), dict(first, value="page:mechanics:demo:paralysis")]})
        self.assertEqual(records[2]["mechanics"]["environments"]["rows"][1]["value"], 2)
        self.assertEqual([row["value"] for row in records[3]["mechanics"]["environments"]["rows"]], [0, 13])
        self.assertNotIn("mechanics", records[0])
        changed = bytearray(data)
        changed[0x309] = 2
        with self.assertRaisesRegex(ValueError, "environment table evidence differs"):
            pm.build(block, bytes(changed))
        table = block["environments"]
        for at, value, message in ((0x311, 9, "effect 9 has no key"), (0x304, 0, "move 0 is invalid")):
            changed = bytearray(data)
            changed[at] = value
            table["sha256"] = hashlib.sha256(changed[0x300:0x318]).hexdigest()
            table["rom"] = [rom_evidence(0x300, changed[0x300:0x302].hex())]
            with self.assertRaisesRegex(ValueError, message):
                pm.build(block, bytes(changed))
        block, data = self.environment_fixture()
        block["environments"]["rom"][0]["sha256"] = hashlib.sha256(bytes.fromhex("ff05")).hexdigest()
        with self.assertRaisesRegex(ValueError, "environment evidence differs at 0x300"):
            pm.build(block, data)

    def test_environment_declarations_reject_malformed_tables_rows_and_columns(self):
        block, _ = self.environment_fixture()
        valid = block["environments"]
        row = valid["rows"][0]
        cases = (("base", -1), ("count", 0), ("stride", "8"), ("sha256", "00"), ("fields", {"type": 0, "effect": 1}),
                 ("fields", {"type": 0, "effect": 1, "move": {"offset": 7, "width": 2}}),
                 ("effects", {}), ("effects", {"x": "page:mechanics:demo:sleep"}), ("effects", {"1": "Sleep"}),
                 ("rows", []), ("rows", [dict(row, index=3)]), ("rows", [dict(row, environment="Place")]),
                 ("rows", [dict(row, sections=[-1])]), ("rows", [dict(row, sections=[7, 7])]),
                 ("rows", [dict(row, maps=[{"map": "1.x", "section": 4}])]),
                 ("rows", [dict(row, maps=[{"map": "1.9"}])]), ("rows", [dict(row, scripts=[3, 3])]),
                 ("rows", [dict(row, scripts=[-1])]), ("rows", [dict(row, scripts=3)]),
                 ("rows", [dict(row, map_codes=[0x10000])]), ("rows", [dict(row, map_codes=[0x4F01, 0x4F01])]),
                 ("rows", [dict(row, map_codes=[0x4F01])]), ("rows", [dict(row, map_codes=3)]),
                 ("rows", [dict(row, place="7")]), ("evidence", " "), ("rom", []),
                 ("environment_row", None), ("environment_row", 3),
                 ("scenes", {"field": 27, "base": 0, "stride": 4, "count": 8}),
                 ("scenes", {"field": 27, "base": 0, "stride": 1, "count": 8, "default": 9}),
                 ("scripts", {"variable": 0x39}), ("scripts", {"variable": 0x4039, "ops": [22]}),
                 ("scripts", {"variable": 0x4039, "persistence": "Persists"}))
        valid = dict(valid, environment_row=1, scenes={"field": 27, "base": 0, "stride": 4, "count": 8, "default": 9},
                     scripts={"variable": 0x4039, "persistence": "page:mechanics:demo:persistence"})
        block["environments"] = valid
        self.assertEqual(pm.check(block), [])
        for field, value in cases:
            block, _ = self.environment_fixture()
            block["environments"] = dict(valid, **{field: value})
            problems = pm.check(block)
            self.assertTrue(any(problem.startswith(f"environments {field}") for problem in problems), (field, value, problems))
        block, _ = self.environment_fixture()
        block["environments"]["rows"] = [row, dict(row)]
        self.assertIn("environments rows duplicates", pm.check(block))
        block["mechanics"]["1"]["environments"] = "anim"
        self.assertIn("mechanics 1 environments", pm.check(block))
        block, _ = self.environment_fixture()
        del block["environments"]
        self.assertIn("mechanics 1 environments", pm.check(block))

    def scene_fixture(self, value=2, op=0x16):
        """Three maps: a scene, a scene its transition script overrides, a person setting the variable."""
        data = bytearray(0x1000)
        ptr = lambda at, target: struct.pack_into("<I", data, at, 0x08000000 + target)   # noqa: E731
        groups, bank, headers, events, objects, scripts, transition = 0x400, 0x410, 0x500, 0x600, 0x680, 0x700, 0x740
        ptr(groups, bank)
        for n in range(3):
            header = headers + 0x20 * n
            ptr(bank + 4 * n, header)
            ptr(header, 0xF00)
            ptr(header + 4, events + 0x10 * n)
            data[header + 20] = 40 + n
            data[header + 27] = 1 if n < 2 else 0
        ptr(headers + 0x20 + 8, transition)
        data[transition] = 3
        ptr(transition + 1, scripts)
        data[events + 0x20] = 1
        ptr(events + 0x24, objects)
        ptr(objects + 16, scripts + 0x10)
        data[scripts:scripts + 6] = struct.pack("<BHH", 0x16, 0x4039, value) + b"\x02"
        data[scripts + 0x10:scripts + 0x1B] = (struct.pack("<BHH", op, 0x4039, 2)
                                               + struct.pack("<BHH", 0x16, 0x4039, 0) + b"\x02")
        data[0x800:0x804] = bytes([1, 1, 0, 0])
        table = {"environment_row": 1, "scenes": {"field": 27, "base": 0x800, "stride": 4, "count": 1, "default": 0},
                 "scripts": {"variable": 0x4039},
                 "rows": [{"index": 0, "scripts": [2]}, {"index": 2}, {"index": 3}]}
        profile = {"maps": {"groups": groups, "mapsec_names": 0xE00, "mapsec_first": 0, "mapsec_count": 256}}
        maps = {f"5.{n}": {"map_header": f"0x{headers + 0x20 * n:06x}", "map_section": 40 + n} for n in range(3)}
        maps["6.1"] = dict(maps["5.1"])
        return table, bytes(data), profile, maps

    def test_scenes_and_script_writes_choose_rows_with_their_maps(self):
        table, data, profile, maps = self.scene_fixture()
        both = [{"map": "5.1", "section": 41}, {"map": "6.1", "section": 41}, {"map": "5.2", "section": 42}]
        self.assertEqual(pm.environment_maps(table, data, profile, maps), {
            0: {"script_maps": both}, 2: {"scene_maps": [{"map": "5.0", "section": 40}]},
            3: {"also_script_maps": both}})
        table["rows"] = [{"index": 0, "scripts": [7]}, {"index": 2}]
        with self.assertRaisesRegex(ValueError, "value 2 at 0x0000700 has no row"):
            pm.environment_maps(table, data, profile, maps)
        table, data, profile, maps = self.scene_fixture(value=0)
        self.assertEqual(pm.environment_maps(table, data, profile, maps)[2]["scene_maps"],
                         [{"map": "5.0", "section": 40}, {"map": "5.1", "section": 41}, {"map": "6.1", "section": 41}])
        table, data, profile, maps = self.scene_fixture(op=0x19)
        with self.assertRaisesRegex(ValueError, "copyvar at 0x0000710 has no literal value"):
            pm.environment_maps(table, data, profile, maps)

    def test_literal_map_codes_resolve_to_verified_runtime_maps(self):
        table = {"rows": [{"index": 23, "map_codes": [0x4F01]}]}
        maps = {"1.79": {"map_header": "0x34fa9c", "map_section": 137}}
        self.assertEqual(pm.environment_maps(table, b"", {}, maps),
                         {23: {"map_code_maps": [{"map": "1.79", "section": 137}]}})
        table["rows"][0]["map_codes"] = [0x4F02]
        with self.assertRaisesRegex(ValueError, "map code 0x4F02 has no verified map"):
            pm.environment_maps(table, b"", {}, maps)

    def test_environment_rows_carry_the_maps_of_their_places(self):
        block, data = self.environment_fixture()
        places = {2: {"scene_maps": [{"map": "5.0", "section": 40}]}}
        rows = pm.build(block, data, places)[1]["mechanics"]["environments"]["rows"]
        self.assertEqual(rows[0]["scene_maps"], [{"map": "5.0", "section": 40}])
        self.assertNotIn("scene_maps", rows[1])
        block["environments"].update(environment_row=1, scripts={
            "variable": 0x4039, "persistence": "page:mechanics:demo:persistence"})
        records = pm.build(block, data)
        for move in (1, 2, 3):
            self.assertEqual(records[move]["mechanics"]["environments"]["script_persistence"],
                             "page:mechanics:demo:persistence")

    def test_power_tiers_preserve_conditions_and_require_valid_rows(self):
        block = move_block()
        item = {"kind": "computed", "formula": "page:power:test", "evidence": "Synthetic formula reader",
                "tiers": [{"condition": "page:power:cond:light", "power": 20},
                          {"condition": "page:power:cond:heavy", "power": 40}]}
        block["power"] = {"1": item}
        self.assertEqual(pm.check(block), [])
        records = pm.build(block, rom_bytes())
        self.assertEqual(records[1]["power_tiers"],
                         [["page:power:cond:light", 20], ["page:power:cond:heavy", 40]])
        self.assertNotIn("power_tiers", records[2])
        valid = item["tiers"][0]
        for tiers in ([], "tiers", [None], [["page:power:cond:light", 20]],
                      [{"condition": "light", "power": 20}],
                      [{"condition": valid["condition"]}],
                      *[[dict(valid, power=value)] for value in (0, -1, True, "20", 20.5)]):
            with self.subTest(tiers=tiers):
                item["tiers"] = tiers
                self.assertIn("power 1 tiers", pm.check(block))
        item["tiers"] = [valid, valid]
        self.assertIn("power 1 tiers duplicates", pm.check(block))

    def test_formula_fields_require_evidence_and_preserve_weight_tiers(self):
        block = move_block()
        block["power"] = {"1": {"kind": "computed", "formula": "page:power:test", "evidence": "Synthetic formula reader",
                                "weight_tiers": [[0, 20], [100, 40]],
                                "rom": [rom_evidence(0, "00")]}}
        self.assertEqual(pm.check(block), [])
        records = pm.build(block, rom_bytes())
        self.assertEqual(records[1]["power_formula"], "page:power:test")
        self.assertEqual(records[1]["power_weight"], [[0, 20], [100, 40]])
        self.assertNotIn("power_formula", records[2])
        block["power"]["1"]["rom"][0]["sha256"] = hashlib.sha256(bytes.fromhex("ff")).hexdigest()
        with self.assertRaisesRegex(ValueError, "power evidence differs"):
            pm.build(block, rom_bytes())
        block["power"]["1"]["evidence"] = ""
        block["power"]["1"]["weight_tiers"] = [[100, 40], [0, 20]]
        self.assertIn("power 1 evidence", pm.check(block))
        self.assertIn("power 1 weight_tiers order", pm.check(block))

    def test_extracts_numeric_fields_and_description_ids(self):
        records = pm.build(move_block(), rom_bytes())
        self.assertEqual(len(records), 4)
        first = records[1]
        self.assertEqual(first["id"], 1)
        self.assertEqual(first["name"], "moves:1")
        self.assertEqual(first["description"], "s0000340")
        self.assertEqual(first["offset"], f"0x{BASE + 12:06x}")
        self.assertEqual((first["power"], first["type"], first["accuracy"], first["pp"]),
                         (40, 0, 100, 35))
        self.assertEqual((first["effect"], first["target"], first["priority"]), (0, 0, 0))
        self.assertEqual((first["flags"], first["z_move_power"], first["damage_category"]), (0x13, 100, 0))
        self.assertEqual((first["target_key"], first["effect_key"]), ("selected", "hit"))
        self.assertEqual(first["flag_keys"], ["makes_contact", "protect_affected", "mirror_move_affected"])
        self.assertEqual(first["list_keys"], ["punching"])
        self.assertEqual(records[2]["list_keys"], ["punching", "sound"])
        self.assertEqual(records[3]["list_keys"], ["sound"])
        self.assertIsNone(records[2]["description"])
        self.assertIsNone(records[3]["description"])
        self.assertEqual(records[3]["priority"], -6)
        self.assertEqual(records[3]["damage_category"], 2)
        self.assertEqual(pm.build(move_block(), rom_bytes()), records)

    def test_extracts_reader_pairs_for_every_flag_and_list(self):
        readers = pm.build_readers(move_block())
        self.assertEqual(len(readers), 7)
        self.assertEqual(readers["makes_contact"],
                         {"readers": {"abilities": [100, 217], "items": [578]}})
        self.assertEqual(readers["punching"], {"readers": {"abilities": [93], "items": []}})
        self.assertEqual(readers["sound"],
                         {"readers": {"abilities": [43, 218], "items": [709]}})
        self.assertEqual(readers["protect_affected"], {"readers": {"abilities": [], "items": []}})
        self.assertEqual(pm.build_readers(move_block()), readers)

    def test_rejects_invalid_reader_pairs(self):
        block = move_block()
        block["readers"].append({"key": "unknown", "evidence": ""})
        block["readers"].append({"key": "punching", "abilities": [93, 93], "evidence": "duplicate"})
        problems = pm.check(block)
        self.assertIn("readers 3 evidence", problems)
        self.assertIn("readers 3 key unknown", problems)
        self.assertIn("readers 4 abilities duplicates", problems)

    def test_rejects_missing_proof_and_fields(self):
        block = move_block()
        del block["evidence"]
        del block["fields"]["power"]
        del block["descriptions"]["evidence"]
        self.assertEqual(pm.check(block), ["evidence", "fields power", "descriptions evidence"])
        del block["targets"]["evidence"]
        del block["lists"][0]["base"]
        self.assertEqual(pm.check(block)[-2:], ["targets evidence", "lists 0 base"])

    def test_rejects_unnamed_values_bits_and_list_ids(self):
        block = move_block()
        del block["effects"]["43"]
        with self.assertRaisesRegex(ValueError, "move 2 effect 43"):
            pm.build(block, rom_bytes())
        block = move_block()
        del block["flag_bits"]["5"]
        with self.assertRaisesRegex(ValueError, "move 3 flags"):
            pm.build(block, rom_bytes())
        block = move_block()
        data = bytearray(rom_bytes())
        data[0x380:0x382] = (4).to_bytes(2, "little")
        with self.assertRaisesRegex(ValueError, "invalid id 4"):
            pm.build(block, bytes(data))

    def test_integrates_with_the_dataset_and_verification(self):
        block = {
            "name_table": "species",
            "species": {"base": 0, "stride": 1, "count": 1, "first": 0,
                        "evidence": "synthetic", "fields": {"hp": 0}},
            "moves": move_block(),
        }
        data = rom_bytes()
        doc = pd.build(block, data, {"id": "demo"})
        self.assertEqual(len(doc["moves"]), 4)
        self.assertEqual(doc["move_readers"]["punching"], {"readers": {"abilities": [93], "items": []}})
        self.assertEqual(doc["provenance"]["moves"]["offset"], f"0x{BASE:06x}")
        self.assertEqual(doc["provenance"]["move_descriptions"]["offset"], f"0x{DESCRIPTIONS:06x}")
        self.assertEqual(pd.verify_doc(doc, block, "demo", doc["rom"]["sha1"]), [])
        broken = dict(doc)
        del broken["moves"]
        self.assertIn("moves", pd.verify_doc(broken, block, "demo", doc["rom"]["sha1"])[0])

    def test_dataset_rejects_missing_or_changed_power_classification(self):
        block = {"name_table": "species",
                 "species": {"base": 0, "stride": 1, "count": 1, "first": 0,
                             "evidence": "synthetic", "fields": {"hp": 0}},
                 "moves": move_block()}
        block["moves"]["power"] = {
            "1": {"kind": "computed", "formula": "page:power:computed", "evidence": "Synthetic formula"},
            "2": {"kind": "fixed", "base": 160, "formula": "page:power:constant", "evidence": "Synthetic constant"},
        }
        doc = pd.build(block, rom_bytes(), {"id": "demo"})
        self.assertEqual(pd.verify_doc(doc, block, "demo", doc["rom"]["sha1"]), [])
        for number, key, value in ((1, "power_kind", None), (1, "power_kind", "fixed"),
                                   (2, "power_kind", "computed"), (2, "power_base", 10)):
            changed = copy.deepcopy(doc)
            changed["moves"][number][key] = value
            self.assertIn(f"move {number} power rule: regenerate the dataset with gen3tl data pokedex",
                          pd.verify_doc(changed, block, "demo", doc["rom"]["sha1"]))

    def test_dataset_carries_item_power_provenance_and_checks_missing_rows(self):
        power, data = item_power_fixture()
        block = {"name_table": "species",
                 "species": {"base": 0, "stride": 1, "count": 1, "first": 0,
                             "evidence": "synthetic", "fields": {"hp": 0}}, "moves": move_block()}
        block["moves"]["power"] = {"1": power}
        doc = pd.build(block, data, {"id": "demo"})
        self.assertEqual(doc["provenance"]["move_item_power"]["1"],
                         {"offset": "0x000300", "evidence": power["evidence"],
                          "sha256": power["item_tiers"]["sha256"]})
        self.assertEqual(pd.verify_doc(doc, block, "demo", doc["rom"]["sha1"]), [])
        for rows in (None, [], [{"item": 5}, None],
                     [{"item": 5, "type": 0, "power": 80}] * 2,
                     [{"item": 5, "type": 0, "power": 80}, {"item": 6, "type": 11, "power": 90}]):
            doc["moves"][1]["power_items"] = rows
            self.assertTrue(any("item power" in p for p in pd.verify_doc(doc, block, "demo", doc["rom"]["sha1"])))

    def test_dataset_carries_environment_provenance_and_checks_stale_rows(self):
        moves, data = self.environment_fixture()
        block = {"name_table": "species",
                 "species": {"base": 0, "stride": 1, "count": 1, "first": 0,
                             "evidence": "synthetic", "fields": {"hp": 0}}, "moves": moves}
        doc = pd.build(block, data, {"id": "demo"})
        table = moves["environments"]
        self.assertEqual(doc["provenance"]["move_environments"],
                         {"offset": "0x000300", "evidence": table["evidence"], "sha256": table["sha256"]})
        self.assertEqual(pd.verify_doc(doc, block, "demo", doc["rom"]["sha1"]), [])
        stale = copy.deepcopy(doc)
        stale["moves"][2]["mechanics"]["environments"]["rows"].pop()
        self.assertIn("move 2 environments: regenerate the dataset with gen3tl data pokedex",
                      pd.verify_doc(stale, block, "demo", doc["rom"]["sha1"]))
        stale = copy.deepcopy(doc)
        stale["moves"][1]["mechanics"]["environments"]["rows"][0]["value"] = 65
        self.assertIn("move 1 environments: regenerate the dataset with gen3tl data pokedex",
                      pd.verify_doc(stale, block, "demo", doc["rom"]["sha1"]))
        stale = copy.deepcopy(doc)
        stale["moves"][1]["mechanics"]["environments"]["rows"][0]["scene_maps"] = [{"map": "5.0"}]
        self.assertIn("move 1 environments: regenerate the dataset with gen3tl data pokedex",
                      pd.verify_doc(stale, block, "demo", doc["rom"]["sha1"]))
        stale = copy.deepcopy(doc)
        stale["moves"][1]["mechanics"]["environments"]["rows"][0]["scene_maps"] = [{"map": "5.0", "section": 40}]
        self.assertIn("move 1 environments: regenerate the dataset with gen3tl data pokedex",
                      pd.verify_doc(stale, block, "demo", doc["rom"]["sha1"]))
        stale = copy.deepcopy(doc)
        del stale["moves"][3]["mechanics"]
        self.assertIn("move 3 environments: regenerate the dataset with gen3tl data pokedex",
                      pd.verify_doc(stale, block, "demo", doc["rom"]["sha1"]))
        stale = copy.deepcopy(doc)
        stale["moves"][1]["mechanics"]["environments"]["rows"][0]["map_code_maps"] = [
            {"map": "1.79", "section": 137}]
        self.assertIn("move 1 environments: regenerate the dataset with gen3tl data pokedex",
                      pd.verify_doc(stale, block, "demo", doc["rom"]["sha1"]))
        stale = copy.deepcopy(doc)
        stale["moves"][1]["mechanics"]["environments"]["script_persistence"] = "page:mechanics:demo:persistence"
        self.assertIn("move 1 environments: regenerate the dataset with gen3tl data pokedex",
                      pd.verify_doc(stale, block, "demo", doc["rom"]["sha1"]))
        moves["environments"].update(environment_row=1, scripts={"variable": 0x4039})
        del moves["environments"]["rows"][0]["maps"]
        moves["environments"]["rows"][0]["map_codes"] = [0x4F01]
        self.assertIn("[pokedex.moves.environments] encounters context required for map codes, scenes and scripts",
                      pd.check(block))
