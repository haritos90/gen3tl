from test_roots import TOOLKIT_ROOT
"""Item uses read from event scripts."""

import copy
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))
from project_fixture import rom_evidence
from gen3tl.data import pokedexitems  # noqa: E402
from gen3tl.data import pokedexuses as pu  # noqa: E402

GROUPS, BANK, HEADER, EVENTS, OBJECTS, SCRIPT = 0xA00, 0xA10, 0xA40, 0xB40, 0xC00, 0xD00
NAME_BOX, NAME, TUTORS, BUYERS, TABLE = 0x1101, 0xE00, 0xF00, 0xF40, 0xF80


def rom() -> bytes:
    """One map whose object script names its speaker, checks, gives and takes items."""
    data = bytearray(0x1200)
    ptr = lambda at, target: struct.pack_into("<I", data, at, 0x08000000 + target)   # noqa: E731
    ptr(GROUPS, BANK)
    ptr(BANK, HEADER)
    ptr(HEADER, 0xB00)
    ptr(HEADER + 4, EVENTS)
    data[HEADER + 20] = 0x63
    data[EVENTS] = 1
    ptr(EVENTS + 4, OBJECTS)
    data[OBJECTS] = 1
    ptr(OBJECTS + 16, SCRIPT)
    code = bytearray()
    code += bytes([0x0F, 0]) + struct.pack("<I", 0x08000000 + NAME)            # loadpointer the name
    code += bytes([0x23]) + struct.pack("<I", 0x08000000 + NAME_BOX)           # callasm the name box
    code += struct.pack("<BHH", 0x47, 47, 4)                                    # checkitem 47, 4
    code += struct.pack("<BHH", 0x1A, 0x8000, 219)                              # setorcopyvar 0x8000, 219
    code += struct.pack("<BHH", 0x1A, 0x8001, 1)                                # setorcopyvar 0x8001, 1
    code += bytes([0x09, 0])                                                    # callstd 0
    code += struct.pack("<BHH", 0x45, 47, 4)                                    # removeitem 47, 4
    code += struct.pack("<BHH", 0x45, 46, 4)                                    # removeitem 46, 4
    code += struct.pack("<BHH", 0x16, 0x8005, 1)                                # setvar 0x8005, 1
    code += struct.pack("<BHH", 0x45, 0x800E, 3)                                # removeitem 0x800E, 3
    code += struct.pack("<BHH", 0x45, 195, 0x4001)                              # removeitem 195, 0x4001
    code += bytes([0x23]) + struct.pack("<I", 0x08000000 + 0x1181)              # callasm a buyer
    code += bytes([0x02])                                                       # end
    data[SCRIPT:SCRIPT + len(code)] = code
    struct.pack_into("<HH", data, TUTORS, 7, 8)
    struct.pack_into("<HHI", data, BUYERS, 46, 0, 7000)
    struct.pack_into("<HHI", data, BUYERS + 8, 47, 0, 9000)
    struct.pack_into("<HH", data, TABLE, 94, 92)
    return bytes(data)


def at(name: str) -> int:
    """The script offset of a command of rom()."""
    return SCRIPT + {"check": 11, "give": 16, "take": 28, "take2": 33, "tutor": 38, "domain": 43,
                     "level": 48, "buyer": 53}[name]


def block() -> dict:
    return {
        "evidence": "synthetic uses", "name_box": NAME_BOX,
        "buyer_record": {"stride": 8, "item": 0, "price": 4},
        "buyers": [{"base": BUYERS, "count": 2, "at": at("buyer")}],
        "rows": [
            {"kind": "exchange", "takes": [at("take"), at("take2")], "gives": [{"item": 219, "at": at("give")}]},
            {"kind": "requirement", "takes": [at("check")], "gives": [{"tutor": 1, "at": at("tutor")}]},
            {"kind": "exchange", "takes": [at("domain")], "held_effect": 72, "gives": [{"held_effect": 87}],
             "evidence": "synthetic plates"},
            {"kind": "upgrade", "takes": [at("level")], "quantity": [3, 10], "gives": [{"upgrade": 181}],
             "evidence": "synthetic level cost"},
            {"kind": "exchange", "takes": [at("domain")], "items": [94], "gives": [{"table": TABLE, "count": 2}],
             "evidence": "synthetic choice list"},
        ],
    }


def profile() -> dict:
    return {"id": "demo", "maps": {"groups": GROUPS, "mapsec_names": 0x1000, "mapsec_first": 0, "mapsec_count": 256}}


def items() -> list[dict]:
    rows = [{"id": n, "held_effect": 0, "hold_effect_param": 0} for n in range(300)]
    rows[120].update(held_effect=72, hold_effect_param=5)
    rows[130].update(held_effect=87, hold_effect_param=5)
    return rows


TUTOR = {"move_base": TUTORS, "move_count": 2}


def native_fixture():
    data = bytearray(rom()) + bytearray(0x800)
    records = items()
    records[130]["hold_effect_param"] = 23
    records[131].update(held_effect=87, hold_effect_param=5)
    records[47].update(price=101, pocket=5)
    records[48].update(price=200, pocket=1)
    struct.pack_into("<HH", data, 0x1200, 130, 120)
    data[0x1240 + 23 * 4:0x1240 + 24 * 4] = bytes([6, 2, 0, 0])
    data[0x1240 + 5 * 4:0x1240 + 6 * 4] = bytes([4, 0, 4, 0])
    struct.pack_into("<BxHBx", data, 0x1306, 0, 7, 3)
    struct.pack_into("<BxHBx", data, 0x1346, 1, 8, 2)
    b = block()
    proof = rom_evidence(at("buyer"), data[at("buyer"):at("buyer") + 5].hex())
    common = {"rom": [proof], "evidence": "synthetic native exchange"}
    b["recipes"] = [{**common, "at": at("buyer"), "outputs": {"base": 0x1200, "count": 2},
                      "base": 0x1240, "count": 24, "stride": 4, "items": [48, 49, 50, 51],
                      "index_field": "hold_effect_param"}]
    b["tutors"] = [{**common, "tables": [0x1300, 0x1340], "count": 2, "stride": 6,
                     "tutor": 0, "move": 2, "quantity": 4,
                     "entries": [{"at": at("buyer"), "index": 1, "item": 92}],
                     "conditions": ["Offer varies with the date"]}]
    b["prices"] = [{**common, "at": at("buyer"), "items": [94], "price": 10000},
                   {**common, "at": at("buyer"), "pocket": 5, "range": [47, 48],
                    "price": {"field": "price", "multiplier": 9, "divisor": 10},
                    "conditions": ["Birthday discount active"]}]
    return b, data, records


class ItemUseTest(unittest.TestCase):
    def test_native_tables_preserve_costs_variants_prices_and_locations(self):
        b, data, records = native_fixture()
        b["rows"][0]["conditions"] = ["Once per day; offer depends on story progress"]
        out = pu.build(b, bytes(data), profile(), records, TUTOR)
        self.assertEqual([use["takes"] for use in out[48]], [
            [{"item": 48, "quantity": 6}, {"item": 49, "quantity": 2}],
            [{"item": 48, "quantity": 4}, {"item": 50, "quantity": 4}]])
        self.assertEqual([use["gives"] for use in out[48]], [[{"item": 130, "quantity": 1}],
                                                            [{"item": 120, "quantity": 1}]])
        self.assertEqual(out[49], [out[48][0]])
        self.assertEqual(out[50], [out[48][1]])
        self.assertEqual(out[51], [])
        self.assertEqual([use["gives"] for use in out[92]], [[{"move": 7}], [{"move": 8}]])
        self.assertEqual([use["takes"][0]["quantity"] for use in out[92]], [3, 2])
        for use in out[92]:
            self.assertNotIn("choice", use)
            self.assertEqual(use["conditions"], ["Offer varies with the date"])
            self.assertEqual(use["places"], out[48][0]["places"])
        self.assertEqual(out[94][-1]["gives"], [{"money": 10000}])
        self.assertEqual(out[47][-1]["gives"], [{"money": 90}])
        self.assertEqual(out[47][-1]["conditions"], ["Birthday discount active"])
        for record in records:
            record["uses"] = out[record["id"]]
        pokedexitems.join_rewards(records)
        self.assertEqual(records[130]["sources"][0]["takes"], out[48][0]["takes"])
        self.assertEqual(records[219]["sources"][0]["conditions"], b["rows"][0]["conditions"])

    def test_script_variants_and_variable_species_keep_their_proofs(self):
        b, data, records = native_fixture()
        row = b["rows"][4]
        struct.pack_into("<BHH", data, 0x1400, 0x16, 0x4069, 7)
        row.update(gives=[{"species": 7, "at": 0x1400}],
                   conditions=["Once per day; offer depends on story progress"],
                   rom=[rom_evidence(0x1400, data[0x1400:0x1405].hex())])
        out = pu.build(b, bytes(data), profile(), records, TUTOR)
        self.assertEqual(out[94][0]["gives"], [{"species": 7}])
        self.assertEqual(out[94][0]["conditions"], row["conditions"])
        data[0x1403] = 8
        with self.assertRaisesRegex(ValueError, r"rows\[4\]: ROM evidence differs"):
            pu.build(b, bytes(data), profile(), records, TUTOR)

    def test_native_proofs_and_table_relationships_fail_closed(self):
        cases = [
            (lambda b, d, r: d.__setitem__(at("buyer") + 1, 0), "ROM evidence differs"),
            (lambda b, d, r: r[130].update(hold_effect_param=24), "invalid output or cost index"),
            (lambda b, d, r: d.__setitem__(slice(0x129C, 0x12A0), bytes(4)), "missing costs"),
            (lambda b, d, r: d.__setitem__(0x1308, 9), "move or cost disagrees"),
            (lambda b, d, r: d.__setitem__(0x130A, 0), "move or cost disagrees"),
            (lambda b, d, r: r[47].pop("price"), "missing field"),
            (lambda b, d, r: r[47].update(pocket=1), "empty domain"),
        ]
        for change, message in cases:
            with self.subTest(message=message):
                b, data, records = native_fixture()
                change(b, data, records)
                with self.assertRaisesRegex(ValueError, message):
                    pu.build(b, bytes(data), profile(), records, TUTOR)

    def test_native_declarations_require_bounded_layouts_and_evidence(self):
        cases = [
            ("recipes", lambda r: r.pop("rom")),
            ("recipes", lambda r: r.update(index_field="id")),
            ("recipes", lambda r: r.update(items=[{}])),
            ("recipes", lambda r: r.update(outputs={"base": -1, "count": 1})),
            ("tutors", lambda r: r.update(move=5)),
            ("tutors", lambda r: r["entries"][0].update(index=2)),
            ("tutors", lambda r: r.update(conditions=[None])),
            ("prices", lambda r: r.update(price={"field": "price", "multiplier": 1, "divisor": 0})),
            ("prices", lambda r: r.update(pocket=5)),
            ("prices", lambda r: r.update(evidence="")),
        ]
        for name, change in cases:
            with self.subTest(name=name):
                b, _, _ = native_fixture()
                change(b[name][0])
                self.assertEqual(pu.check(b, 300), [f"{name}[0]"])

    def test_rows_read_items_quantities_places_and_the_named_speaker(self):
        out = pu.build(block(), rom(), profile(), items(), TUTOR)
        place = {"places": [{"map": "0.0", "map_section": 0x63, "scripts": [f"0x{SCRIPT:07X}"]}],
                 "speaker": f"s{NAME:07X}"}
        shell = {"kind": "exchange", "takes": [{"item": 47, "quantity": 4}, {"item": 46, "quantity": 4}],
                 "gives": [{"item": 219, "quantity": 1}], **place}
        self.assertEqual(out[47][0], shell)
        self.assertEqual(out[46][0], shell)
        self.assertEqual(out[47][1], {"kind": "requirement", "takes": [{"item": 47, "quantity": 4}],
                                      "gives": [{"move": 8}], **place})
        self.assertEqual(out[120], [{"kind": "exchange", "takes": [{"item": 120, "quantity": 3}],
                                     "gives": [{"item": 130}], **place}])
        self.assertEqual(out[195], [{"kind": "upgrade", "takes": [{"item": 195, "quantity": [3, 10]}],
                                     "gives": [{"upgrade": 181}], **place}])
        self.assertEqual(out[94], [{"kind": "exchange", "takes": [{"item": 94, "quantity": 3}],
                                    "gives": [{"item": 94}, {"item": 92}], "choice": True, **place}])
        self.assertEqual(out[46][1], {"kind": "exchange", "takes": [{"item": 46, "quantity": None}],
                                      "gives": [{"money": 7000}], **place})
        self.assertEqual(out[47][2]["gives"], [{"money": 9000}])
        self.assertEqual(out[1], [])

    def test_commands_that_disagree_with_the_declaration_fail(self):
        cases = [
            (lambda b: b["rows"][0]["gives"][0].update(item=220), "does not give item 220"),
            (lambda b: b["rows"][0]["gives"][0].update(quantity=2), "gives another quantity"),
            (lambda b: b["rows"][0].update(takes=[at("give")]), "is not checkitem or removeitem"),
            (lambda b: b["rows"][4].pop("items"), "item operand and declared items disagree"),
            (lambda b: b["rows"][3].update(takes=[at("take")]), "has a literal quantity"),
            (lambda b: b["rows"][1].update(kind="exchange"), "an exchange removes no item"),
            (lambda b: b["rows"][0].update(kind="requirement"), "a requirement removes its item"),
            (lambda b: b["rows"][1]["gives"][0].update(at=at("give")), "does not give tutor 1"),
            (lambda b: b["rows"][2].update(held_effect=73), "no item has held effect 73"),
            (lambda b: b["buyers"][0].update(at=at("take")), "not a callasm command"),
        ]
        for change, message in cases:
            with self.subTest(message=message):
                broken = copy.deepcopy(block())
                change(broken)
                with self.assertRaisesRegex(ValueError, message):
                    pu.build(broken, rom(), profile(), items(), TUTOR)

    def test_a_command_no_map_reaches_fails(self):
        data = bytearray(rom())
        data[OBJECTS + 16:OBJECTS + 20] = b"\0\0\0\0"
        with self.assertRaisesRegex(ValueError, "no map reaches"):
            pu.build(block(), bytes(data), profile(), items(), TUTOR)

    def test_two_names_leave_the_speaker_out(self):
        data = bytearray(rom())
        data[EVENTS] = 2
        data[OBJECTS + 24] = 2
        struct.pack_into("<I", data, OBJECTS + 24 + 16, 0x08000000 + 0x1000)
        other = bytes([0x0F, 0]) + struct.pack("<I", 0x08000000 + NAME + 0x10)
        other += bytes([0x23]) + struct.pack("<I", 0x08000000 + NAME_BOX)
        other += bytes([0x05]) + struct.pack("<I", 0x08000000 + at("check"))   # goto the shared check
        data[0x1000:0x1000 + len(other)] = other
        out = pu.build(block(), bytes(data), profile(), items(), TUTOR)
        self.assertNotIn("speaker", out[47][0])
        self.assertEqual(out[47][0]["places"], [{"map": "0.0", "map_section": 0x63,
                                               "scripts": [f"0x{SCRIPT:07X}", "0x0001000"]}])

    def test_check_names_each_invalid_declaration(self):
        self.assertEqual(pu.check(block(), 300), [])
        self.assertEqual(pu.check(None, 300), ["block"])
        broken = copy.deepcopy(block())
        broken.update(evidence="", name_box=-1, buyer_record={"stride": 4, "item": 0, "price": 4})
        broken["buyers"].append({"base": 0, "count": 0, "at": 0})
        broken["rows"] += [
            {"kind": "trade", "takes": [1], "gives": [{"item": 1}]},
            {"kind": "exchange", "takes": [], "gives": [{"item": 1, "at": 2}]},
            {"kind": "exchange", "takes": [1], "gives": [{"item": 1}]},
            {"kind": "exchange", "takes": [1], "gives": [{"item": 300, "at": 2}]},
            {"kind": "exchange", "takes": [1], "gives": [{"upgrade": 1, "at": 2}]},
            {"kind": "exchange", "takes": [1], "items": [1], "held_effect": 3, "gives": [{"item": 1, "at": 2}],
             "evidence": "both domains"},
            {"kind": "exchange", "takes": [1], "gives": [{"item": 1, "money": 2, "at": 2}]},
            {"kind": "exchange", "takes": [1], "quantity": [4, 3], "gives": [{"item": 1, "at": 2}], "evidence": "x"},
        ]
        self.assertEqual(pu.check(broken, 300), [
            "evidence", "name_box", "buyer_record", "buyers[1]", "rows[5]", "rows[6]", "rows[7]", "rows[8]",
            "rows[9]", "rows[10]", "rows[11]", "rows[12]"])
        self.assertEqual(pu.check({"evidence": "x", "name_box": 1,
                                   "buyer_record": {"stride": 8, "item": 0, "price": 4}}, 300), ["rows"])

    def test_the_item_block_reports_use_problems(self):
        item_block = {"base": 0, "stride": 44, "count": 300, "evidence": "synthetic",
                      "fields": {name: 0 for name in (*pokedexitems.FIELDS, "description")},
                      "uses": {**block(), "evidence": ""}}
        self.assertIn("uses evidence", pokedexitems.check(item_block))


if __name__ == "__main__":
    unittest.main()
