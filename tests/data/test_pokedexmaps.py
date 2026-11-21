from test_roots import TOOLKIT_ROOT
"""Area map rendering and manifest."""

import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.data import pokedexmaps as pm  # noqa: E402
from gen3tl.build.graphics import lz77_compress  # noqa: E402
from gen3tl.check.render import read_png_image  # noqa: E402

HEADER, LAYOUT, BLOCKS = 0x100, 0x120, 0x140
PRIMARY, SECONDARY = 0x160, 0x180
P_TILES, P_PALS, P_METAS = 0x200, 0x300, 0x500
S_TILES, S_PALS, S_METAS = 0x600, 0x700, 0x900
RED, GREEN, BLUE = 0x001F, 0x03E0, 0x7C00


def tile(colour: int, corner: int | None = None) -> bytes:
    """An 8x8 4bpp tile of one colour; `corner` colours the top-left pixel."""
    pixels = [colour] * 64
    if corner is not None:
        pixels[0] = corner
    return bytes(pixels[i] | pixels[i + 1] << 4 for i in range(0, 64, 2))


def palettes(first: tuple, rest: int) -> bytes:
    """16 palettes: colour 0 black, colour 1 `first[p]` for the listed ones, else `rest`."""
    out = bytearray()
    for p in range(16):
        colours = [0] * 16
        colours[1] = first[p] if p < len(first) else rest
        colours[2] = rest
        out += struct.pack("<16H", *colours)
    return bytes(out)


def rom_bytes() -> bytes:
    data = bytearray(0x1000)
    struct.pack_into("<I", data, HEADER, 0x08000000 + LAYOUT)
    struct.pack_into("<IIIIII", data, LAYOUT, 2, 1, 0x08000000 + 0xF00, 0x08000000 + BLOCKS,
                     0x08000000 + PRIMARY, 0x08000000 + SECONDARY)
    struct.pack_into("<HH", data, BLOCKS, 0 | 0xC00, 1)    # metatile 0 (primary), metatile 1 (secondary)
    # primary tileset: blank, red and blue-cornered tiles
    packed = lz77_compress(tile(0) + tile(1) + tile(0, corner=2))
    data[P_TILES:P_TILES + len(packed)] = packed
    struct.pack_into("<BBHIII", data, PRIMARY, 1, 0, 0, 0x08000000 + P_TILES, 0x08000000 + P_PALS,
                     0x08000000 + P_METAS)
    data[P_PALS:P_PALS + 512] = palettes((RED,), BLUE)
    # metatile 0: bottom quarters of tile 1, top blank
    struct.pack_into("<8H", data, P_METAS, 1, 1, 1, 1, 0, 0, 0, 0)
    # secondary tileset: raw, one green tile in palette 7
    data[S_TILES:S_TILES + 32] = tile(1)
    struct.pack_into("<BBHIII", data, SECONDARY, 0, 1, 0, 0x08000000 + S_TILES, 0x08000000 + S_PALS,
                     0x08000000 + S_METAS)
    data[S_PALS:S_PALS + 512] = palettes((0, 0, 0, 0, 0, 0, 0, GREEN), BLUE)
    # secondary metatile 0: tile 0 palette 7, primary corner flipped
    struct.pack_into("<8H", data, S_METAS, 3 | 7 << 12, 3 | 7 << 12, 3 | 7 << 12, 3 | 7 << 12,
                     2 | 1 << 10 | 1 << 11, 0, 0, 0)
    return bytes(data)


def block() -> dict:
    return {
        "schema": 1, "name_table": "species",
        "species": {"base": 0x0, "stride": 28, "count": 2, "evidence": "synthetic", "fields": {"hp": 0}},
        "encounters": {"evidence": "synthetic"},
        "maps": {"primary_tiles": 3, "primary_metatiles": 1, "primary_palettes": 7, "palettes": 13,
                 "evidence": "synthetic layout: two blocks, one tileset each"},
    }


class MapTest(unittest.TestCase):
    def test_render_layers_the_metatiles_of_both_tilesets(self):
        image, palette, lay = pm.render(rom_bytes(), HEADER, block()["maps"])
        self.assertEqual((lay["width"], lay["height"]), (2, 1))
        self.assertEqual((len(image), len(image[0])), (16, 32))
        self.assertEqual(image[0][1], 1)                 # primary palette 0, red
        self.assertEqual(image[0][0], 1)
        self.assertEqual(palette[1], (255, 0, 0))
        self.assertEqual(palette[2], (0, 0, 255))
        self.assertEqual(image[0][16], 7 * 16 + 1)       # secondary palette 7, green
        self.assertEqual(palette[7 * 16 + 1], (0, 255, 0))
        # the flipped corner covers only one quarter pixel
        self.assertEqual(image[7][23], 2)
        self.assertEqual(image[0][17], 7 * 16 + 1)

    def test_write_and_verify_round_trip(self):
        data = rom_bytes()
        doc = pm.build(block(), data, 2, found={"3.69": HEADER})
        self.assertEqual([record["image"] for record in doc["maps"]], ["maps/3.69.png"])
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            manifest, size = pm.write(out, doc)
            self.assertEqual(manifest, out / "maps" / "manifest.json")
            listed = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(listed["count"], 1)
            self.assertEqual(listed["files"]["3.69"]["width"], 2)
            self.assertEqual(pm.verify(out, block(), doc["rom_sha1"]), [])
            self.assertIn("rom sha1", pm.verify(out, block(), "0" * 40)[0])
            png = read_png_image(out / "maps" / "3.69.png")
            self.assertEqual((png.width, png.height), (32, 16))
            self.assertEqual(png.rgb[0][1], (255, 0, 0))
            self.assertEqual(png.rgb[0][16], (0, 255, 0))
            (out / "maps" / "3.69.png").unlink()
            self.assertIn("files missing", pm.verify(out, block(), doc["rom_sha1"])[0])
        self.assertEqual(pm.build(block(), data, 2, found={"3.69": HEADER})["maps"][0]["png"],
                         doc["maps"][0]["png"])

    def test_index_lists_sizes_without_rendering(self):
        records = pm.index(block(), rom_bytes(), 2, found={"3.69": HEADER, "3.7": HEADER})
        self.assertEqual([r["runtime_map"] for r in records], ["3.7", "3.69"])
        self.assertEqual(records[0], {"runtime_map": "3.7", "map_header": "0x000100", "width": 2, "height": 1,
                                      "image": "maps/3.7.png"})

    def test_check_names_the_missing_constants_and_invalid_layouts(self):
        b = block()
        del b["maps"]["palettes"]
        b["maps"]["evidence"] = ""
        self.assertEqual(pm.check(b), ["[pokedex.maps] palettes", "[pokedex.maps] evidence"])
        self.assertEqual(pm.check({}), ["[pokedex] block"])
        data = bytearray(rom_bytes())
        struct.pack_into("<I", data, LAYOUT, 0)
        with self.assertRaisesRegex(ValueError, "invalid layout size"):
            pm.render(bytes(data), HEADER, block()["maps"])


if __name__ == "__main__":
    unittest.main()
