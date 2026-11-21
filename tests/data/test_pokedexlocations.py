from test_roots import TOOLKIT_ROOT
"""Verified location points and ordinary map connections."""

import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.data import pokedexlocations as pl  # noqa: E402

HEADER, OTHER, ROUTE = 0x100, 0x300, 0x500
EVENTS, OBJECTS, SCRIPT = 0x140, 0x180, 0x200
CONNECTIONS, ROWS = 0x1C0, 0x1E0


def rom_bytes() -> bytes:
    data = bytearray(0x1000)
    struct.pack_into("<IIII", data, HEADER, 0, 0x08000000 + EVENTS, 0, 0x08000000 + CONNECTIONS)
    data[HEADER + 20] = 142
    struct.pack_into("<BBBBIIII", data, EVENTS, 1, 0, 0, 0, 0x08000000 + OBJECTS, 0, 0, 0)
    struct.pack_into("<BBBBhh", data, OBJECTS, 1, 98, 0, 0, 28, 12)
    struct.pack_into("<I", data, OBJECTS + 16, 0x08000000 + SCRIPT)
    struct.pack_into("<II", data, CONNECTIONS, 2, 0x08000000 + ROWS)
    struct.pack_into("<IiBB2x", data, ROWS, 1, 21, 3, 83)
    struct.pack_into("<IiBB2x", data, ROWS + 12, 2, 0, 3, 75)
    for header, section in ((OTHER, 142), (ROUTE, 106)):
        struct.pack_into("<IIII", data, header, 0, 0, 0, 0x08000000 + 0x380)
        data[header + 20] = section
    struct.pack_into("<II", data, 0x380, 0, 0x08000000 + ROWS)
    return bytes(data)


def block() -> dict:
    return {
        "evidence": "synthetic object and connection layouts",
        "directions": {"1": "south", "2": "north", "3": "west", "4": "east"},
        "maps": [{"runtime_map": "3.82", "points": [{
            "kind": "zygarde_cell", "x": 28, "y": 12, "offset": OBJECTS, "script": SCRIPT,
            "messages": [0x280], "description": "Northeast fenced area",
            "description_evidence": "owner-description-map-image",
        }]}],
    }


def found() -> dict:
    return {
        "3.82": {"map": "h0000100", "map_section": 142, "map_header": f"0x{HEADER:06x}"},
        "3.83": {"map": "h0000300", "map_section": 142, "map_header": f"0x{OTHER:06x}"},
        "3.75": {"map": "h0000500", "map_section": 106, "map_header": f"0x{ROUTE:06x}"},
    }


class LocationTest(unittest.TestCase):
    def test_extracts_validated_points_and_named_connections(self):
        rows = pl.build(block(), rom_bytes(), {}, found())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["points"], [{
            "kind": "zygarde_cell", "x": 28, "y": 12,
            "description": "Northeast fenced area", "description_evidence": "owner-description-map-image",
            "offset": "0x000180", "script": "0x000200", "messages": ["0x000280"],
        }])
        self.assertEqual([(row["to_runtime_map"], row["to_section"], row["direction"], row["alignment"])
                          for row in rows[0]["exits"]],
                         [("3.83", 142, "south", 21), ("3.75", 106, "north", 0)])
        self.assertEqual(pl.build(block(), rom_bytes(), {}, found()), rows)

    def test_rejects_borrowed_objects_and_changed_event_facts(self):
        for at, value in ((OBJECTS + 2, 255), (OBJECTS + 4, 29)):
            with self.subTest(at=at):
                data = bytearray(rom_bytes())
                data[at] = value
                with self.assertRaisesRegex(ValueError, "borrowed|differ"):
                    pl.build(block(), bytes(data), {}, found())

    def test_rejects_borrowed_rom_object_layout(self):
        data = bytearray(rom_bytes())
        data[OBJECTS:OBJECTS + 24] = bytes.fromhex(
            "1863ff005200330002000000510003009e80e7093b100000")
        point = {"x": 82, "y": 51, "offset": OBJECTS, "script": 0x200}
        struct.pack_into("<I", data, OBJECTS + 16, 0x08000200)
        with self.assertRaisesRegex(ValueError, "borrowed"):
            pl._point(bytes(data), point, OBJECTS, 1)

    def test_rate_rules_require_evidence_and_one_threshold_source(self):
        b = block()
        b["encounter_rates"] = {"evidence": "ROM rate check", "land": {
            "multiplier": 16, "divisor": 1600, "label": "Encounter rate: {n}%", "note": "Before modifiers"}}
        self.assertEqual(pl.check(b), [])
        b["encounter_rates"]["land"]["threshold"] = 50
        self.assertEqual(pl.check(b), ["encounter_rates land"])
        del b["encounter_rates"]["evidence"]
        self.assertEqual(pl.check(b), ["encounter_rates evidence", "encounter_rates land"])

    def test_check_requires_evidence_and_valid_unique_maps(self):
        b = block()
        b["evidence"] = ""
        b["maps"].append(b["maps"][0])
        self.assertEqual(pl.check(b), ["evidence", "maps[1] runtime_map"])
        self.assertEqual(pl.check(None), ["block"])


def name_block() -> dict:
    return {"evidence": "synthetic section, map type and warp layouts", "type_kinds": {"8": "interior"}, "maps": []}


def named_rom() -> tuple[bytes, dict]:
    from test_pokedexpickups import profile, rom

    data = bytearray(rom())
    data.extend(bytes(0x1100 - len(data)))
    data[0x140 + 23] = 8
    struct.pack_into("<I", data, 0xF00 + 0x63 * 4, 0x08000F80)
    return bytes(data), profile()


class MapNameTest(unittest.TestCase):
    def test_section_and_declared_sign_names_carry_their_evidence(self):
        data, profile = named_rom()
        sources = [{"map": "0.0", "runtime_map": "0.0", "map_section": 0x63}]
        labels = {"Crater Pokémon Gym": "s1F1EC48@~"}
        resolved = pl.resolve_maps(data, profile, sources)
        self.assertEqual(resolved, [{
            "map": "0.0", "runtime_map": "0.0", "map_section": 0x63, "map_header": "0x000140", "map_type": 8,
        }])
        rows = pl.map_names(name_block(), data, profile, sources, labels, resolved)
        self.assertEqual(rows, [{
            "map": "0.0", "runtime_map": "0.0", "map_section": 0x63, "map_header": "0x000140", "map_type": 8,
            "kind": "interior", "name_rule": "map-section",
            "name_evidence": "Map header 0x000140: named section 99, map type 8",
        }])
        declared = name_block()
        declared["maps"] = [{"runtime_map": "0.0", "name": "Crater Pokémon Gym", "evidence": "synthetic gym sign"}]
        rows = pl.map_names(declared, data, profile, sources, labels)
        self.assertEqual(rows[0]["name"], "Crater Pokémon Gym")
        self.assertEqual(rows[0]["name_rule"], "sign")
        self.assertEqual(rows[0]["name_evidence"], "synthetic gym sign")
        with self.assertRaisesRegex(ValueError, "unknown name label"):
            pl.map_names(declared, data, profile, sources, {})

    def test_unique_named_warp_parent_names_a_map_without_a_section(self):
        data, profile = named_rom()
        data = bytearray(data)
        parent, events, warps = 0x600, 0x680, 0x6A0
        struct.pack_into("<I", data, 0x114, 0x08000000 + parent)
        struct.pack_into("<I", data, 0x134, 0x08000000 + parent)
        struct.pack_into("<II", data, parent, 0x08000180, 0x08000000 + events)
        data[parent + 20], data[parent + 23] = 0x64, 1
        data[events + 1] = 1
        struct.pack_into("<I", data, events + 8, 0x08000000 + warps)
        data[warps + 6], data[warps + 7] = 0, 0
        profile["maps"].update(mapsec_first=0x64, mapsec_count=1)
        struct.pack_into("<I", data, 0xF00, 0x08000F80)
        rows = pl.map_names(name_block(), bytes(data), profile,
                            [{"map": "0.0", "runtime_map": "0.0", "map_section": 0x63}], {})
        self.assertEqual(rows[0]["parent_section"], 0x64)
        self.assertEqual(rows[0]["kind"], "interior")
        self.assertEqual(rows[0]["name_rule"], "warp-parent")
        self.assertIn("0x0006A0", rows[0]["name_evidence"])

    def test_name_declarations_are_validated_and_must_be_used(self):
        self.assertEqual(pl.check_names(name_block()), [])
        broken = name_block()
        broken.update(evidence="", type_kinds={"8": []})
        broken["maps"] = [{"runtime_map": "0.0", "name": "", "kind": [], "evidence": ""}, {}]
        self.assertEqual(pl.check_names(broken),
                         ["names evidence", "names type_kinds", "names.maps[0]", "names.maps[1]"])
        data, profile = named_rom()
        declared = name_block()
        declared["maps"] = [{"runtime_map": "9.9", "name": "Crater Pokémon Gym", "evidence": "synthetic"}]
        with self.assertRaisesRegex(ValueError, "declarations name no pickup map"):
            pl.map_names(declared, data, profile, [], {"Crater Pokémon Gym": "s1F1EC48@~"})


if __name__ == "__main__":
    unittest.main()
