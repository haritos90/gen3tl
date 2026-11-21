from test_roots import TOOLKIT_ROOT
"""Item pickups read from map events and their scripts."""

import copy
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.data import pokedexdata as pd  # noqa: E402
from gen3tl.data import pokedexpickups as pp  # noqa: E402

GROUPS, BANK, ENGINE, ENGINE_BANK, HEADER = 0x100, 0x110, 0x120, 0x130, 0x140
EVENTS, OBJECTS, COORDS, BGS, MAPSCRIPTS, SCRIPTS = 0x200, 0x220, 0x400, 0x440, 0x480, 0x500
XOR, SKIP = 0x1234, 0x9F0


def code(*commands: bytes) -> bytes:
    return b"".join(commands)


def setvar(op: int, var: int, value: int) -> bytes:
    return struct.pack("<BHH", op, var, value)


def give(item: int, quantity: int = 1, std: int = 0) -> bytes:
    return setvar(0x1A, 0x8000, item) + setvar(0x1A, 0x8001, quantity) + bytes([0x09, std])


def rom() -> bytes:
    """One map: item balls, gifts of every event kind, a hidden item and the cases left out."""
    data = bytearray(0x1000)
    ptr = lambda at, target: struct.pack_into("<I", data, at, 0x08000000 + target)   # noqa: E731
    ptr(GROUPS, BANK)
    ptr(BANK, HEADER)
    struct.pack_into("<I", data, ENGINE, (0x08000000 + ENGINE_BANK) ^ XOR)
    ptr(ENGINE_BANK, HEADER)
    ptr(HEADER, 0x180)
    ptr(HEADER + 4, EVENTS)
    ptr(HEADER + 8, MAPSCRIPTS)
    data[HEADER + 20] = 0x63
    scripts = [
        give(1, 2, std=1) + b"\x02",                                              # item ball
        give(1, 2, std=1) + b"\x02",                                              # borrowed object
        give(3) + b"\x02",                                                        # gift
        code(bytes([0x8F, 3, 0]), struct.pack("<BHH", 0x21, 0x800D, 0),
             bytes([0x06, 1]) + struct.pack("<I", 0x08000000 + SCRIPTS + 0x300), b"\x02"),   # random branch
        b"\x02",                                                                  # declared script
        code(setvar(0x19, 0x8000, 0x4050), setvar(0x1A, 0x8001, 1), bytes([0x09, 0]), b"\x02"),   # variable
        code(bytes([0x92]) + struct.pack("<IB", 500, 0), give(9), b"\x02"),      # purchase
    ]
    data[EVENTS:EVENTS + 4] = bytes([len(scripts), 0, 1, 2])
    ptr(EVENTS + 4, OBJECTS)
    ptr(EVENTS + 12, COORDS)
    ptr(EVENTS + 16, BGS)
    for index, script in enumerate(scripts):
        at, target = OBJECTS + 24 * index, SCRIPTS + 0x40 * index
        data[at] = index + 1
        data[at + 2] = 255 if index == 1 else 0
        struct.pack_into("<hh", data, at + 4, 10 + index, 20 + index)
        ptr(at + 16, SKIP if index == 4 else target)
        data[target:target + len(script)] = script
    data[SCRIPTS + 0x300:SCRIPTS + 0x300 + 13] = give(8) + b"\x02"
    data[SKIP] = 0x02
    struct.pack_into("<HH", data, COORDS, 3, 4)                                   # trigger
    ptr(COORDS + 12, 0x900)
    data[0x900:0x90E] = code(setvar(0x16, 0x8000, 6), setvar(0x16, 0x8001, 1), bytes([0x09, 1]), b"\x02")
    struct.pack_into("<HHBBHHBB", data, BGS, 5, 6, 0, 7, 0, 4, 9, 0x81)           # hidden item 4
    struct.pack_into("<HHBB", data, BGS + 12, 7, 8, 0, 0)                         # sign
    ptr(BGS + 12 + 8, 0x940)
    data[0x940:0x946] = struct.pack("<BHH", 0x44, 5, 1) + b"\x02"                  # additem 5, 1
    data[MAPSCRIPTS] = 1
    ptr(MAPSCRIPTS + 1, 0x980)
    data[0x980:0x98E] = give(7) + b"\x02"
    return bytes(data)


def block() -> dict:
    return {
        "evidence": "synthetic events", "item_var": 0x8000, "quantity_var": 0x8001, "result_var": 0x800D,
        "ground_std": 1, "gift_std": [0, 1], "additem": 0x44,
        "hidden": {"kind": 7, "item": 8, "quantity": 11, "mask": 0x7F},
        "routines": [0x23, 0x25, 0x26, 0x8F], "purchases": [0x92, 0x95], "skip_scripts": [SKIP],
        "points": [{"offset": OBJECTS, "description": "Corner", "description_evidence": "owner-description",
                    "related_sections": [0x64]}],
    }


def profile() -> dict:
    return {"id": "demo", "maps": {"groups": GROUPS, "mapsec_names": 0xF00, "mapsec_first": 0, "mapsec_count": 256,
                                   "engine": {"groups": ENGINE, "count": 1, "bank_xor": XOR}}}


class PickupTest(unittest.TestCase):
    def test_every_event_kind_gives_its_pickups_and_the_rest_is_counted(self):
        out, counts = pp.build(block(), rom(), profile(), 20, {"0.0"})
        place = {"map": "0.0", "map_section": 0x63, "runtime_map": "0.0"}
        self.assertEqual(out[1], [{"kind": "ground", **place, "quantity": 2, "x": 10, "y": 20,
                                   "description": "Corner", "description_evidence": "owner-description",
                                   "related_sections": [0x64]}])
        self.assertEqual(out[3], [{"kind": "gift", **place, "quantity": 1, "x": 12, "y": 22, "script": "0x0000580"}])
        self.assertEqual(out[4], [{"kind": "hidden", **place, "quantity": 1, "x": 5, "y": 6}])
        self.assertEqual(out[5], [{"kind": "gift", **place, "quantity": 1, "x": 7, "y": 8, "script": "0x0000940"}])
        self.assertEqual(out[6], [{"kind": "gift", **place, "quantity": 1, "x": 3, "y": 4, "script": "0x0000900"}])
        self.assertEqual(out[7], [{"kind": "gift", **place, "quantity": 1, "script": "0x0000980"}])
        self.assertNotIn(8, out)
        self.assertNotIn(9, out)
        self.assertEqual(counts, {"shown": {"gift": 4, "ground": 1, "hidden": 1}, "left_out": {
            "bought with money or coins": 1, "branch decided by a routine": 1, "connected-map object": 1,
            "declared script kept out": 1, "item or quantity in a variable": 1}})

    def test_dataset_pickups_resolve_map_headers_without_name_rules(self):
        configured = {"items": {"count": 20, "sources": {"pickups": block()}}}
        found, resolved, named = pd.build_pickups(configured, rom(), profile(), set())
        self.assertEqual(found[1][0]["runtime_map"], "0.0")
        self.assertEqual(resolved, [{
            "map": "0.0", "runtime_map": "0.0", "map_section": 0x63, "map_header": "0x000140", "map_type": 0,
        }])
        self.assertEqual(named, [])

    def test_values_survive_branches_and_calls_clear_them(self):
        data = bytearray(rom())
        target = SCRIPTS + 0x40 * 2
        script = code(setvar(0x1A, 0x8000, 3), setvar(0x1A, 0x8001, 1),
                      bytes([0x04]) + struct.pack("<I", 0x08000000 + 0x9C0), bytes([0x09, 0]), b"\x02")
        data[target:target + len(script)] = script
        data[0x9C0] = 0x03
        out, counts = pp.build(block(), bytes(data), profile(), 20)
        self.assertNotIn(3, out)
        self.assertEqual(counts["left_out"]["item or quantity in a variable"], 2)
        self.assertEqual(out[1][0]["runtime_map"], "0.0")

    def test_invalid_declarations_and_unmatched_points_fail(self):
        self.assertEqual(pp.check(block()), [])
        self.assertEqual(pp.check(None), ["block"])
        broken = copy.deepcopy(block())
        broken.update(evidence="", item_var=1, ground_std=300, gift_std=[], additem=-1, routines=[], purchases=None,
                      hidden={"kind": 7}, skip_scripts=[0])
        broken["points"].append({"offset": 1, "description": ""})
        self.assertEqual(pp.check(broken), ["evidence", "item_var", "ground_std", "gift_std", "additem", "hidden",
                                            "routines", "purchases", "skip_scripts", "points[1]"])
        with self.assertRaisesRegex(ValueError, "invalid pickup configuration: evidence"):
            pp.build({**block(), "evidence": ""}, rom(), profile(), 20)
        moved = copy.deepcopy(block())
        moved["points"][0]["offset"] = OBJECTS + 24
        with self.assertRaisesRegex(ValueError, "pickup points name no pickup: 0x0000238"):
            pp.build(moved, rom(), profile(), 20)

    def test_the_dataset_orders_shops_pickups_and_mining(self):
        items = {
            "base": 0xC00, "stride": 4, "count": 20, "evidence": "synthetic items",
            "fields": {"price": 0, "held_effect": 1, "hold_effect_param": 2, "pocket": 3, "battle_usage": 3,
                       "description": {"offset": 0, "width": 4}},
            "sources": {"evidence": "synthetic shops", "pickups": block(),
                        "shops": [{"map": "0.0", "map_section": 0x63, "offset": 0x10, "list": 0x20, "items": [3]}]},
        }
        doc = pd.build({"name_table": "species", "items": items,
                        "species": {"base": 0, "stride": 1, "count": 1, "first": 0, "evidence": "synthetic",
                                    "fields": {"hp": 0}}}, rom(), profile())
        self.assertEqual([source["kind"] for source in doc["items"][3]["sources"]], ["shop", "gift"])
        self.assertEqual(doc["provenance"]["item_pickups"], {"offset": "", "evidence": "synthetic events"})

    def test_dataset_groups_branch_rewards_across_trigger_positions(self):
        data = bytearray(rom())
        script = code(bytes([0x06, 1]) + struct.pack("<I", 0x08000A00), give(3, 2), b"\x02")
        data[0x580:0x580 + len(script)] = script
        data[0xA00:0xA0D] = give(5, 3) + b"\x02"
        struct.pack_into("<I", data, COORDS + 12, 0x08000580)
        items = {
            "base": 0xC00, "stride": 4, "count": 20, "evidence": "synthetic items",
            "fields": {"price": 0, "held_effect": 1, "hold_effect_param": 2, "pocket": 3, "battle_usage": 3,
                       "description": {"offset": 0, "width": 4}},
            "sources": {"evidence": "synthetic events", "pickups": block(), "shops": []},
        }
        doc = pd.build({"name_table": "species", "items": items,
                        "species": {"base": 0, "stride": 1, "count": 1, "first": 0, "evidence": "synthetic",
                                    "fields": {"hp": 0}}}, bytes(data), profile())
        source = doc["items"][3]["sources"][0]
        self.assertEqual(source["script"], "0x0000580")
        self.assertEqual(source["choices"], [{"item": 3, "quantity": 2}, {"item": 5, "quantity": 3}])
        self.assertEqual(source["positions"], [{"x": 3, "y": 4}, {"x": 12, "y": 22}])
        self.assertIn(source, doc["items"][5]["sources"])
        self.assertEqual(len(doc["items"][3]["sources"]), 1)
        self.assertEqual(len(doc["items"][5]["sources"]), 2)


if __name__ == "__main__":
    unittest.main()
