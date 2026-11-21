from test_roots import TOOLKIT_ROOT
"""Type-effectiveness extraction."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.data import pokedextypes as pt  # noqa: E402


class TypeEffectivenessTest(unittest.TestCase):
    def test_every_pair_preserves_the_raw_multiplier(self):
        records = pt.build({"base": 0, "count": 2, "evidence": "synthetic"}, bytes([0, 1, 5, 20]))
        self.assertEqual(records, [
            {"attack": 0, "defense": 0, "multiplier": 0},
            {"attack": 0, "defense": 1, "multiplier": 1},
            {"attack": 1, "defense": 0, "multiplier": 5},
            {"attack": 1, "defense": 1, "multiplier": 20},
        ])

    def test_rebuild_is_identical(self):
        block = {"base": 0, "count": 2, "evidence": "synthetic"}
        self.assertEqual(pt.build(block, bytes([0, 1, 5, 20])), pt.build(block, bytes([0, 1, 5, 20])))

    def test_invalid_values_and_short_tables_are_refused(self):
        with self.assertRaisesRegex(ValueError, "invalid multiplier 3"):
            pt.build({"base": 0, "count": 2, "evidence": "synthetic"}, bytes([0, 1, 5, 3]))
        with self.assertRaisesRegex(ValueError, "exceeds ROM"):
            pt.build({"base": 0, "count": 2, "evidence": "synthetic"}, bytes(3))

    def test_check_names_missing_evidence(self):
        self.assertIn("evidence", pt.check({"base": 0, "count": 24}))


if __name__ == "__main__":
    unittest.main()


class TypeIconTest(unittest.TestCase):
    """The colour of a type from its icon."""

    SHEET, PALETTE, TABLE = 0x100, 0x3000, 0x3100

    def rom(self) -> bytes:
        data = bytearray(0x3200)
        # two 32 x 12 icons on a 16-tile sheet
        for tile in tuple(range(8)) + tuple(range(16, 24)):
            for y in range(8):
                for x in range(4):
                    row = (tile // 16) * 8 + y
                    fill = 3 if tile % 16 < 4 or row >= 6 else 7
                    value = 0 if row >= 12 else (14 if x == 0 and y == 0 else fill)   # one outline pixel
                    data[self.SHEET + tile * 32 + y * 4 + x] = value | value << 4
        for i, colour in enumerate([0x0000, 0x001F, 0x03E0, 0x7C00, 0x4210, 0x0210, 0x1084, 0x7C1F,
                                    0x1084, 0x2108, 0x318C, 0x4210, 0x5294, 0x6318, 0x0842, 0x7FFF]):
            data[self.PALETTE + 2 * i:self.PALETTE + 2 * i + 2] = colour.to_bytes(2, "little")
        for icon, (w, h, offset) in enumerate([(12, 12, 0), (32, 12, 0), (32, 12, 4), (42, 12, 8)]):
            data[self.TABLE + 4 * icon:self.TABLE + 4 * icon + 4] = bytes([w, h]) + offset.to_bytes(2, "little")
        return bytes(data)

    def block(self) -> dict:
        return {"sheet": self.SHEET, "tiles": 32, "tiles_per_row": 16, "palette": self.PALETTE,
                "table": self.TABLE, "count": 4, "evidence": "synthetic icons"}

    def test_colours_average_the_icon_fill(self):
        colours = pt.colours(self.block(), self.rom(), 3)
        self.assertEqual(colours, [{"id": 0, "icon": "0x0000", "colour": "#0000ff"},
                                   {"id": 1, "icon": "0x0004", "colour": "#8000ff"}])   # type 2 has a 42 px icon
        self.assertEqual(pt.colours(self.block(), self.rom(), 3), colours)

    def test_check_and_bounds(self):
        b = self.block()
        del b["palette"]
        b["evidence"] = ""
        self.assertEqual(pt.check_icons(b), ["palette", "evidence"])
        b = self.block()
        b["sheet"] = 0x3100
        with self.assertRaisesRegex(ValueError, "exceeds ROM"):
            pt.colours(b, self.rom(), 3)
