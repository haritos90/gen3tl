from test_roots import TOOLKIT_ROOT
"""Move and currency icons as masks."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.data import pokedexicons as pi  # noqa: E402
from gen3tl.build.font import BACKGROUND, INK, SHADOW, encode_glyph  # noqa: E402
from gen3tl.build.graphics import lz77_compress  # noqa: E402
from gen3tl.check.render import read_png_image  # noqa: E402

BASE = 0x100
FONT_BASE, FONT_WIDTHS = 0x200, 0x2C0


def icon(plate: int, symbol: int, rows: int = 16, width: int = 8) -> bytes:
    """A width x rows 4bpp tile bitmap: a plate with a symbol pixel at (0, 1); the last 4 rows empty."""
    grid = [[0 if y >= rows - 4 else (symbol if (x, y) == (0, 1) else plate) for x in range(width)]
            for y in range(rows)]
    out = bytearray()
    for tile in range((width // 8) * (rows // 8)):
        ox, oy = (tile % (width // 8)) * 8, (tile // (width // 8)) * 8
        for y in range(8):
            for x in range(0, 8, 2):
                out.append(grid[oy + y][ox + x] | grid[oy + y][ox + x + 1] << 4)
    return bytes(out)


def rom_bytes() -> bytes:
    data = bytearray(0x400)
    for index, (plate, symbol) in enumerate(((1, 3), (13, 15), (10, 15))):
        blob = icon(plate, symbol)
        data[BASE + index * len(blob):BASE + (index + 1) * len(blob)] = blob
    glyph = [[BACKGROUND] * 16 for _ in range(16)]
    glyph[2][2] = glyph[2][3] = glyph[3][2] = INK
    glyph[3][3] = SHADOW
    data[FONT_BASE + 0x40:FONT_BASE + 0x80] = encode_glyph(glyph)
    data[FONT_WIDTHS + 1] = 8
    return bytes(data)


def block() -> dict:
    return {"icons": {"split": {"base": BASE, "stride": 64, "width": 8, "height": 16, "symbol": [3, 15, 15],
                                "evidence": "synthetic bitmaps"}}}


def pokedollar_block() -> dict:
    b = block()
    b["icons"]["pokedollar"] = {"font": "font0", "glyph": 1, "evidence": "synthetic font glyph"}
    return b


def fonts() -> list[dict]:
    return [{"name": "font0", "glyphs": FONT_BASE, "widths": FONT_WIDTHS, "count": 2}]


ITEM_TABLE, PIC_A, PAL_A, PIC_B, PAL_B = 0x400, 0x600, 0x800, 0x900, 0xB00


def item_rom() -> bytes:
    """Two items: a compressed picture and palette, and a raw palette; a third row is no icon."""
    data = bytearray(rom_bytes())
    data.extend(bytes(0x1000 - len(data)))
    pic = bytes([0x21] * (9 * 32))                     # every pixel pair: colour 1 then colour 2
    palette = bytes(0) + b"".join(c.to_bytes(2, "little") for c in [0, 0x001F, 0x03E0] + [0] * 13)
    for at, blob in ((PIC_A, lz77_compress(pic)), (PAL_A, lz77_compress(palette)), (PIC_B, lz77_compress(pic))):
        data[at:at + len(blob)] = blob
    data[PAL_B:PAL_B + 32] = palette
    for row, (pic_at, pal_at) in enumerate(((PIC_A, PAL_A), (PIC_B, PAL_B), (0x10, 0x10))):
        data[ITEM_TABLE + 8 * row:ITEM_TABLE + 8 * row + 8] = (0x08000000 + pic_at).to_bytes(4, "little") + (0x08000000 + pal_at).to_bytes(4, "little")
    return bytes(data)


def item_block() -> dict:
    b = block()
    b["icons"]["items"] = {"table": ITEM_TABLE, "count": 3, "evidence": "synthetic rows"}
    return b


class IconTest(unittest.TestCase):
    def test_masks_keep_the_symbol_pixels_only(self):
        doc = pi.build(block(), rom_bytes())
        self.assertEqual([i["category"] for i in doc["icons"]], ["physical", "special", "status"])
        self.assertEqual([(i["width"], i["height"]) for i in doc["icons"]], [(8, 12)] * 3)
        self.assertEqual(pi.index(block()), {"split": ["icons/physical.png", "icons/special.png", "icons/status.png"]})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            manifest, _ = pi.write(out, doc)
            listed = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(listed["files"]["special"]["image"], "icons/special.png")
            png = read_png_image(out / "icons" / "physical.png")
            self.assertEqual((png.width, png.height), (8, 12))
            self.assertEqual(png.indices[1][0], 1)   # the symbol pixel
            self.assertEqual(png.indices[0][0], 0)   # the plate is transparent
            self.assertEqual(pi.verify(out, block(), doc["rom_sha1"]), [])
            self.assertIn("rom sha1", pi.verify(out, block(), "0" * 40)[0])
        self.assertEqual(pi.build(block(), rom_bytes())["icons"][0]["png"], doc["icons"][0]["png"])

    def test_pokedollar_uses_font_ink_within_the_advance_width(self):
        doc = pi.build(pokedollar_block(), rom_bytes(), fonts())
        currency = doc["icons"][-1]
        self.assertEqual((currency["category"], currency["width"], currency["height"]), ("pokedollar", 8, 4))
        self.assertEqual(pi.index(pokedollar_block())["pokedollar"], "icons/pokedollar.png")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            manifest, _ = pi.write(out, doc)
            listed = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(listed["files"]["pokedollar"]["image"], "icons/pokedollar.png")
            png = read_png_image(out / "icons" / "pokedollar.png")
            self.assertEqual(png.indices[2][2], 1)
            self.assertEqual(png.indices[3][3], 0)
            self.assertEqual(pi.verify(out, pokedollar_block(), doc["rom_sha1"]), [])
            (out / "icons" / "pokedollar.png").unlink()
            self.assertIn("files missing", pi.verify(out, pokedollar_block(), doc["rom_sha1"])[0])
        with self.assertRaisesRegex(ValueError, "unique"):
            pi.build(pokedollar_block(), rom_bytes())

    def test_item_icons_decode_compressed_and_raw_palettes(self):
        data = item_rom()
        self.assertEqual(pi.item_index(item_block(), data), {0: "icons/items/0.png", 1: "icons/items/1.png"})
        self.assertEqual(pi.item_index(block(), data), {})
        doc = pi.build(item_block(), data)
        self.assertEqual([item["id"] for item in doc["items"]], [0, 1])
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            manifest, _ = pi.write(out, doc)
            listed = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(listed["items"], {"0": "icons/items/0.png", "1": "icons/items/1.png"})
            png = read_png_image(out / "icons" / "items" / "1.png")
            self.assertEqual((png.width, png.height), (24, 24))
            self.assertEqual(png.rgb[0][0], (255, 0, 0))
            self.assertEqual(png.rgb[0][1], (0, 255, 0))
            self.assertEqual(pi.verify(out, item_block(), doc["rom_sha1"]), [])
            (out / "icons" / "items" / "1.png").unlink()
            self.assertIn("files missing", pi.verify(out, item_block(), doc["rom_sha1"])[0])
        b = item_block()
        del b["icons"]["items"]["count"]
        self.assertIn("[pokedex.icons.items] count", pi.check(b))

    def test_check_and_empty_symbols_are_refused(self):
        b = block()
        del b["icons"]["split"]["symbol"]
        self.assertEqual(pi.check(b), ["[pokedex.icons.split] symbol"])
        self.assertEqual(pi.check({}), ["[pokedex] block"])
        b = block()
        b["icons"]["split"]["symbol"] = [2, 2, 2]
        with self.assertRaisesRegex(ValueError, "draws nothing"):
            pi.build(b, rom_bytes())


if __name__ == "__main__":
    unittest.main()
