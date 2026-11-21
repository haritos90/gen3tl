import base64
import json
import struct

import test_editorgfx
from test_editorgfx import AREAS, edited_t1
from graphics_support import LZ_AT, PAL_AT
from gen3tl.web.editorgfx import Pictures
from gen3tl.build.graphics import GraphicsError, area_form_png, lz77_compress

def pixels(view):
    raw = base64.b64decode(view["pixels"])
    return [list(raw[i:i + view["width"]]) for i in range(0, len(raw), view["width"])]

class CompositionTest(test_editorgfx.Fixture):
    def test_tile_placement_flips_and_blit(self):
        data = self.data()
        data["previews"] = {"T1": [{"label": "Labels", "width": 24, "height": 16,
                                     "cells": [[0, 0, 0, 0], [8, 0, 0, 1], [16, 0, 0, 2]],
                                     "rects": [[1, 2, 7, 5, 2, 9]]}]}
        got = pixels(Pictures(data, bytes(self.base.data)).preview("T1")[0])
        tile = [r[:8] for r in self.sheet.pixels[:8]]
        for y in range(8):
            self.assertEqual(got[y], tile[y] + tile[y][::-1] + tile[7 - y])
        for y in range(5):
            self.assertEqual(got[9 + y][2:9], self.sheet.pixels[2 + y][1:8])
        self.assertEqual(got[8], [0] * 24)

    def test_map_edit_uses_appended_tiles_and_upload(self):
        raw = struct.pack("<4H", 0, 1, 2, 3)
        block = lz77_compress(raw)
        self.base.data[0x6000:0x6000 + len(block)] = block
        count = self.sheet.graphic.tiles + 1
        edited = edited_t1(self.sheet, count)
        png = area_form_png(self.sheet, edited, AREAS, count)
        data = self.data()
        data["edits"] = {"T1": {"areas": AREAS, "tiles": count, "png": base64.b64encode(png).decode()}}
        data["previews"] = {"T1": [{"label": "Header", "width": 16, "height": 8,
                                     "map": {"offset": 0x6000, "size": 8, "width": 2, "origin": [0, 0]}}]}
        data["tilemap_edits"] = [{"offset": 0x6000, "width": 2,
                                  "cells": [{"row": 0, "column": 1, "tile": count - 1, "hflip": True}]}]
        pictures = Pictures(data, bytes(self.base.data))
        original = pixels(pictures.preview("T1")[0])
        project = pixels(pictures.preview("T1", edited=True)[0])
        upload = json.loads(pictures.preview_json("T1", base64.b64encode(png).decode(),
                                                 json.dumps(AREAS), count, True))
        for y in range(8):
            self.assertEqual(original[y], self.sheet.pixels[y][:16])
            self.assertEqual(project[y], self.sheet.pixels[y][:8] + self.sheet.pixels[y][:8][::-1])
        self.assertEqual(pixels(upload[0]), project)
        self.assertNotEqual(project, original)

    def test_missing_rom_and_bad_geometry_fail(self):
        data = self.data()
        data["previews"] = {"T1": [{"label": "bad", "width": 8, "height": 8,
                                     "rects": [[10000, 0, 8, 8, 0, 0]]}]}
        with self.assertRaisesRegex(GraphicsError, "no ROM"):
            Pictures(data).preview("T1")
        with self.assertRaisesRegex(GraphicsError, "outside"):
            Pictures(data, bytes(self.base.data)).preview("T1")

    def test_full_map_includes_every_row_and_edited_edge(self):
        block = lz77_compress(struct.pack("<4H", 0, 1, 2, 3))
        self.base.data[0x6000:0x6000 + len(block)] = block
        data = self.data()
        data["previews"] = {"T1": [{"label": "Complete", "map": {
            "offset": 0x6000, "size": 8, "width": 2, "full": True}}]}
        data["tilemap_edits"] = [{"offset": 0x6000, "width": 2,
                                  "cells": [{"row": 1, "column": 1, "tile": 0}]}]
        png = area_form_png(self.sheet, self.sheet.pixels, AREAS)
        pictures = Pictures(data, bytes(self.base.data))
        original = pictures.preview("T1")[0]
        edited = pictures.preview("T1", png, AREAS, edited=True)[0]
        self.assertEqual((original["width"], original["height"]), (16, 16))
        got = pixels(edited)
        for y in range(8):
            self.assertEqual(got[y], self.sheet.pixels[y][:16])
            self.assertEqual(got[8 + y][8:], self.sheet.pixels[y][:8])
        self.assertNotEqual(original["pixels"], edited["pixels"])

    def test_full_map_rejects_crop_and_incomplete_rows(self):
        for extra, size in [({"width": 8}, 8), ({}, 6)]:
            with self.subTest(extra=extra, size=size):
                data = self.data()
                data["previews"] = {"T1": [{"label": "bad", **extra, "map": {
                    "offset": 0x6000, "size": size, "width": 2, "full": True}}]}
                with self.assertRaisesRegex(GraphicsError, "crop|geometry"):
                    Pictures(data, bytes(self.base.data)).preview("T1")

    def test_layers_preserve_transparency_and_palette_banks(self):
        raw = b"\x11" * 32 + b"\x02" + bytes(31) + self.lz_data[64:]
        block = lz77_compress(raw)
        self.base.data[LZ_AT:LZ_AT + len(block)] = block
        self.base.data[0x7000:0x7020] = bytes(32)
        data = self.data()
        view = {"label": "Layers", "width": 8, "height": 8, "transparent": True,
                "palettes": [f"raw:0x{PAL_AT:X}", "raw:0x7000"],
                "cells": [[0, 0, 0, 0, 0], [0, 0, 1, 0, 1]]}
        data["previews"] = {"T1": [view]}
        result = Pictures(data, bytes(self.base.data)).preview("T1")[0]
        self.assertEqual(pixels(result)[0], [18] + [1] * 7)
        self.assertTrue(all(row == [1] * 8 for row in pixels(result)[1:]))
        self.assertEqual(result["palette"][18], "#000000")
        self.assertNotEqual(result["palette"][2], result["palette"][18])
        view["cells"][1][4] = 2
        with self.assertRaisesRegex(GraphicsError, "missing preview palette"):
            Pictures(data, bytes(self.base.data)).preview("T1")
