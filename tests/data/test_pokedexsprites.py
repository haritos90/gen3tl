from test_roots import TOOLKIT_ROOT
"""The Pokedex sprite extraction and manifest."""

import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.data import pokedexdata as pd  # noqa: E402
from gen3tl.data import pokedexsprites as ps  # noqa: E402
from gen3tl.build.graphics import lz77_compress  # noqa: E402
from gen3tl.check.render import read_png_image  # noqa: E402

FRONT = 0x100        # 8-byte rows: {u32 data, u16 size, u16 tag = slot}
BACK = 0x200
PALETTES = 0x300
SHINY = 0x400
FRONT_SPRITE = 0x500     # 8x8 4bpp tiles: 32 bytes
BACK_SPRITE = 0x600
PAL_A = 0x700        # 32-byte palettes
PAL_B = 0x800
PAL_S = 0x900


def sprite_tiles(indices: list[int]) -> bytes:
    """An 8x8 4bpp tile row of `indices`."""
    out = bytearray()
    for y in range(8):
        for x in range(0, 8, 2):
            out.append(indices[y * 8 + x] | (indices[y * 8 + x + 1] << 4))
    return bytes(out)


def palette_bytes(colour: int) -> bytes:
    out = bytearray()
    for i in range(16):
        out += struct.pack("<H", colour if i else 0)
    return bytes(out)


def rom_bytes() -> bytes:
    data = bytearray(0x1000)
    sprite_a = sprite_tiles([1, 2, 3] * 22)       # first two rows filled
    sprite_b = sprite_tiles([2, 1] * 32)
    for at, blob in ((FRONT_SPRITE, sprite_a), (BACK_SPRITE, sprite_b),
                     (PAL_A, palette_bytes(0x7C00)), (PAL_B, palette_bytes(0x03E0)),
                     (PAL_S, palette_bytes(0x001F))):
        packed = lz77_compress(blob)
        data[at:at + len(packed)] = packed
    for table, targets in ((FRONT, (FRONT_SPRITE, FRONT_SPRITE)), (BACK, (BACK_SPRITE, BACK_SPRITE))):
        for slot, target in enumerate(targets):
            struct.pack_into("<IHH", data, table + slot * 8, 0x08000000 + target, len(lz77_compress(
                sprite_a if target == FRONT_SPRITE else sprite_b)), slot)
    for table, targets in ((PALETTES, (PAL_A, PAL_B)), (SHINY, (PAL_S, PAL_S))):
        for slot, target in enumerate(targets):
            struct.pack_into("<IHH", data, table + slot * 8, 0x08000000 + target, slot, 0)
    return bytes(data)


def block() -> dict:
    table = lambda base: {"base": base, "stride": 8, "count": 2}   # noqa: E731
    return {
        "schema": 1, "name_table": "species",
        "species": {"base": 0x0, "stride": 28, "count": 2, "first": 1, "evidence": "synthetic",
                    "fields": {"hp": 0}},
        "sprites": {
            "evidence": "synthetic fixture: tag = slot on every table",
            "front": {**table(FRONT), "size": 4, "tag": 6},
            "back": {**table(BACK), "size": 4, "tag": 6},
            "palettes": {**table(PALETTES), "tag": 4},
            "shiny_palettes": {**table(SHINY), "tag": 4},
        },
    }


class SpriteBuildTest(unittest.TestCase):
    def test_malformed_trainer_tables_report_configuration_errors(self):
        b = {"stride": 40, "fields": {"picture": 3}, "pictures": {
            "side": 64, "evidence": "synthetic", "front": {"base": 0, "count": 1, "stride": 8},
            "palettes": {"base": 8, "count": 1, "stride": 8}}}
        for stride in ("8", [], None):
            with self.subTest(stride=stride):
                b["pictures"]["front"]["stride"] = stride
                self.assertIn("pictures front stride", ps.check_trainers(b))
        b["pictures"]["front"] = []
        self.assertIn("pictures front: a table", ps.check_trainers(b))
        b["pictures"] = "invalid"
        self.assertEqual(ps.check_trainers(b), ["pictures: a table"])

    def test_trainer_pictures_preserve_palette_provenance_and_declared_canvas(self):
        from gen3tl.check.render import png_image
        b = block()
        b["trainers"] = {"stride": 40, "fields": {"picture": 3}, "pictures": {
            "side": 8, "evidence": "synthetic trainer picture tables",
            "front": {"base": 0xB00, "stride": 8, "count": 2}, "palettes": b["sprites"]["palettes"]}}
        data = bytearray(rom_bytes())
        packed = lz77_compress(sprite_tiles([1, 2, 3] * 22) + bytes(32))
        data[0xA00:0xA00 + len(packed)] = packed
        struct.pack_into("<IHHIHH", data, 0xB00, 0x08000A00, 64, 0, 0x08000000 + BACK_SPRITE, 32, 1)
        doc = ps.build(b, data)
        self.assertEqual(len(doc["trainers"]), 2)
        first, second = doc["trainers"]
        image = png_image(first["png"])
        self.assertEqual((image.width, image.height), (8, 8))
        self.assertEqual(image.indices[0][:3], [1, 2, 3])
        self.assertEqual(image.palette[1], (0, 0, 248))
        self.assertEqual(png_image(second["png"]).palette[1], (0, 248, 0))
        self.assertEqual(first["front"], {"record": "0x000b00", "data": "0x000a00"})
        self.assertEqual(first["palettes"], {"record": "0x000300", "data": "0x000700"})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            path, _ = ps.write(out, doc)
            manifest = json.loads(path.read_text())
            self.assertEqual(manifest["trainers"][0]["image"], "trainers/0.png")
            self.assertEqual((out / "trainers/0.png").read_bytes(), first["png"])
            self.assertEqual(ps.verify(out, b, doc["rom_sha1"]), [])
            (out / "trainers/1.png").unlink()
            self.assertIn("trainer picture missing", ps.verify(out, b, doc["rom_sha1"])[0])
        struct.pack_into("<I", data, PALETTES, 0)
        with self.assertRaisesRegex(ValueError, "trainer picture 0 palettes: invalid pointer"):
            ps.build(b, data)
        b["trainers"]["pictures"]["side"] = 7
        self.assertIn("side", " ".join(ps.check(b)))

    def test_build_renders_every_variant_with_a_transparent_colour(self):
        from gen3tl.check.render import png_image
        doc = ps.build(block(), rom_bytes())
        self.assertEqual((doc["count"], doc["rom_sha1"]),
                         (2, __import__("hashlib").sha1(rom_bytes()).hexdigest()))
        first = next(s for s in doc["slots"] if s["slot"] == 1)
        self.assertEqual(set(first["variants"]), {"front", "back", "shiny", "back_shiny"})
        img = png_image(first["variants"]["front"])
        self.assertEqual((img.width, img.height), (8, 8))
        self.assertEqual(img.indices[0][:3], [1, 2, 3])
        self.assertEqual(img.indices, ps._sprite_bytes(rom_bytes(), 0x08000000 + FRONT_SPRITE, "front")[0])
        self.assertEqual(png_image(first["variants"]["back"]).indices,
                         ps._sprite_bytes(rom_bytes(), 0x08000000 + BACK_SPRITE, "back")[0])
        png = first["variants"]["front"]
        at = 8
        chunks = {}
        while at < len(png):
            size = struct.unpack_from(">I", png, at)[0]
            chunks[png[at + 4:at + 8]] = png[at + 8:at + 8 + size]
            at += size + 12
        self.assertEqual(chunks[b"tRNS"], bytes([0] + [255] * 15))
        # the shiny variants share pixels but change palettes
        shiny = png_image(first["variants"]["shiny"])
        back_shiny = png_image(first["variants"]["back_shiny"])
        self.assertEqual(shiny.indices, img.indices)
        self.assertNotEqual(shiny.palette, img.palette)
        self.assertEqual(back_shiny.indices, png_image(first["variants"]["back"]).indices)
        self.assertNotEqual(back_shiny.palette, png_image(first["variants"]["back"]).palette)

    def test_each_table_keeps_its_orientation_with_both_palettes(self):
        from gen3tl.check.render import png_image
        b = block()
        b["species"]["first"] = 0
        doc = ps.build(b, rom_bytes())
        front_pixels = [([1, 2, 3] * 22)[i:i + 8] for i in range(0, 64, 8)]
        back_pixels = [[2, 1] * 4 for _ in range(8)]
        for slot in doc["slots"]:
            normal_colour = (0, 0, 248) if slot["slot"] == 0 else (0, 248, 0)
            front, back = front_pixels, back_pixels
            for variant, expected in (("front", front), ("back", back),
                                      ("shiny", front), ("back_shiny", back)):
                with self.subTest(slot=slot["slot"], variant=variant):
                    image = png_image(slot["variants"][variant])
                    self.assertEqual(image.indices, expected)
                    colour = (248, 0, 0) if "shiny" in variant else normal_colour
                    self.assertEqual(image.palette[1:4], [colour] * 3)

    def test_a_rebuild_is_byte_identical(self):
        data = rom_bytes()
        one, two = ps.build(block(), data), ps.build(block(), data)
        self.assertEqual([s["variants"] for s in one["slots"]],
                         [s["variants"] for s in two["slots"]])

    def test_write_lays_out_the_manifest_by_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            manifest_path, size = ps.write(out, ps.build(block(), rom_bytes()))
            self.assertGreater(size, 0)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["count"], 2)
            self.assertEqual(manifest["files"]["1"]["front"], "sprites/1-front.png")
            self.assertEqual(manifest["files"]["1"]["back_shiny"], "sprites/1-back-shiny.png")
            for variant in ("front", "back", "shiny", "back_shiny"):
                self.assertTrue((out / manifest["files"]["1"][variant]).exists())
            self.assertEqual(ps.verify(out, block(), manifest["rom"]["sha1"]), [])

    def test_verify_names_stale_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            ps.write(out, ps.build(block(), rom_bytes()))
            self.assertIn("rom sha1", ps.verify(out, block(), "0" * 40)[0])
            (out / "sprites" / "1-front.png").unlink()
            self.assertIn("files missing", ps.verify(out, block(), __import__("hashlib")
                                                     .sha1(rom_bytes()).hexdigest())[0])

    def test_check_names_the_missing_parts(self):
        b = block()
        self.assertEqual(ps.check(b), [])
        del b["sprites"]["shiny_palettes"]
        problems = ps.check(b)
        self.assertIn("[pokedex.sprites] shiny_palettes", problems)
        b["sprites"]["shiny_palettes"] = {"base": 0, "stride": 8, "count": 2}
        b["sprites"]["evidence"] = ""
        self.assertIn("[pokedex.sprites] evidence", ps.check(b))

    def test_the_dataset_records_the_sprite_files(self):
        doc = pd.build(block(), rom_bytes(), {"id": "synthetic"})
        self.assertEqual(doc["species"][0]["sprite"],
                         {"front": "sprites/1-front.png", "back": "sprites/1-back.png",
                          "shiny": "sprites/1-shiny.png", "back_shiny": "sprites/1-back-shiny.png"})


if __name__ == "__main__":
    unittest.main()
