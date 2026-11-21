from test_roots import TOOLKIT_ROOT
"""Graphics injection checks and fixtures."""

import sys
from pathlib import Path
import contextlib
import csv
import io
import json
import random
import tempfile
import unittest

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.profile.profile import Profile
from project_fixture import write_policy
from gen3tl.__main__ import main
from gen3tl.build.graphics import (
    GraphicsConfig, area_check, area_form_png, area_png, area_size, lz77_decompress,
    inject_graphics, pixel_check, verify_graphics, verify_graphic_sources, write_sheet,
)
from gen3tl.build.inject import BuildResult, FreeSpace, verify
from gen3tl.check.render import png_image, read_png_image, write_indexed_png, write_png
from gen3tl.core.rom import Rom, ptr_bytes
from graphics_support import HEAD, LZ_AT, RAW_AT, config, fixture, graphics_guard


class GraphicsTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmpdir.name)
        self.base, self.lz_data, self.raw_data, self.slot = fixture(self.tmp)

    def tearDown(self):
        self.tmpdir.cleanup()

    def extract(self, gid):
        cfg = config(self.tmp)
        sheet = cfg.sheet(self.base, cfg.graphic(gid))
        png, side = write_sheet(sheet, self.tmp / "edit", self.base.sha1())
        return sheet, png, json.loads(side.read_text(encoding="utf-8"))

    def test_extract(self):
        sheet, png, meta = self.extract("T1")
        self.assertEqual((sheet.width, sheet.height), (32, 32))
        self.assertEqual((meta["compression"], meta["slot_bytes"], meta["tiles"]), ("lz77", self.slot, 16))
        self.assertEqual(meta["pointers"], ["0x0000200"])
        img = read_png_image(png)
        self.assertEqual(img.indices, sheet.pixels)
        self.assertEqual(img.palette[:16], sheet.palette)
        self.assertEqual(sheet.encode(img.indices), self.lz_data)
        _, _, meta2 = self.extract("T2")
        self.assertEqual(meta2["palette"]["source"], "grey (palette not located)")
        with self.assertRaisesRegex(Exception, "no ROM source"):
            config(self.tmp).graphic("T3")

    def test_unedited_round_trip_is_byte_identical(self):
        _, p1, _ = self.extract("T1")
        _, p2, _ = self.extract("T2")
        cfg = config(self.tmp, [("T1", p1, [[0, 0, 8, 8]]), ("T2", p2, [[0, 0, 8, 8]])])
        rom = self.base.copy()
        res = inject_graphics(rom, self.base, cfg, FreeSpace.scan(bytes(self.base.data)))
        self.assertEqual(res.failures, [])
        self.assertEqual([p.mode for p in res.placements], ["same", "same"])
        self.assertEqual(rom.data, self.base.data)

    def test_source_guard_rejects_undeclared_pixel_changes(self):
        cfg = config(self.tmp)
        rom = self.base.copy()
        rom.data[RAW_AT + 8] ^= 0x11
        failures = verify_graphic_sources(rom, self.base, cfg)
        self.assertEqual(len(failures), 1)
        self.assertIn("T2: source overwritten", failures[0])
        self.assertEqual(verify_graphic_sources(rom, self.base, cfg, {RAW_AT + 8}), [])

    def test_build_preserves_ff_graphics_without_graphics_edits(self):
        from gen3tl.core.charmap import Charmap
        from gen3tl.catalog.po import PoEntry, write
        cm = Charmap()
        raw = bytearray(0x10000)
        raw[0xA000:0xA200] = bytes([6]) * 512
        raw[0xC000:0xC280] = b"\xff" * 640
        raw[0xE000:] = b"\xff" * 0x2000
        raw[0x300:0x306] = cm.encode("Hello")
        raw[0x200:0x204] = ptr_bytes(0x300)
        base = Rom(raw)
        path = self.tmp / "base.gba"
        base.save(path)
        prof = self.tmp / "p.toml"
        prof.write_text(
            f'[rom]\nname = "test"\npath = "{path}"\nsha1 = "{base.sha1()}"\n'
            '[font_style]\nstyle = "unbound"\ncell_height = 14\n'
            '[[font]]\nname = "main"\nglyphs = 0x1000\nwidths = 0xA000\ncount = 512\n'
            '[layout.box_width]\ndialogue = 208\n'
            '[graphics]\ninventory = "inv.tsv"\n')
        with (self.tmp / "inv.tsv").open("w") as stream:
            writer = csv.DictWriter(stream, HEAD, delimiter="\t", lineterminator="\n")
            writer.writeheader()
            writer.writerow(dict(id="FRAME", rom_offset="0xC000", compressed="no",
                                 size_bytes="640", tiles="20", category="keep"))
        (self.tmp / "projects.toml").write_text(
            '[defaults]\nprofile = "p"\nlanguage = "ru"\n'
            '[[project]]\nprofile = "p"\nlanguage = "ru"\nroot = "."\n'
            'policy = "policy.toml"\n')
        write_policy(self.tmp, Profile.load(prof))
        (self.tmp / "build").mkdir()
        entry = dict(id="s0000300", offset=0x300, length=6, kind="pointer", category="script",
                     mode="field", text="Hello", table=None, index=None, flags=[],
                     refs=[dict(at=0x200, kind="script:loadpointer")])
        (self.tmp / "build/inventory.json").write_text(
            json.dumps({"profile": "p", "rom_sha1": base.sha1(), "entries": [entry]}))
        write(self.tmp / "translation/po/a.po", [PoEntry("s0000300", "Hello", "A longer message")],
              {"Project-Id-Version": "p", "Language": "ru"})
        (self.tmp / "translation/release.toml").write_text('version = "0.1.0"\n')
        for switches in ([], ["--no-graphics"]):
            with self.subTest(switches=switches), contextlib.redirect_stdout(io.StringIO()):
                result = main(["--project-root", str(self.tmp), "--profile", str(prof),
                               "build", "rom", "--no-font", "--out", str(self.tmp / "result.gba"), *switches])
            self.assertEqual(result, 0)
            built = Rom.load(self.tmp / "result.gba")
            self.assertEqual(built.read(0xC000, 640), base.read(0xC000, 640))
            self.assertGreaterEqual(built.ptr_at(0x200), 0xE000)

    def test_pixel_check(self):
        sheet, png, _ = self.extract("T1")
        img = read_png_image(png)
        area = [(8, 8, 8, 8)]
        px = [row[:] for row in img.indices]
        px[9][9] = 5                                    # inside the area
        write_indexed_png(self.tmp / "in.png", px, img.palette, 4)
        r = pixel_check(sheet, read_png_image(self.tmp / "in.png"), area)
        self.assertEqual((r.findings, r.changed_inside), ([], 1))
        px[20][3] = 5                                   # outside the area
        write_indexed_png(self.tmp / "out.png", px, img.palette, 4)
        r = pixel_check(sheet, read_png_image(self.tmp / "out.png"), area)
        self.assertEqual(len(r.findings), 1)
        self.assertIn("pixel 3,20", r.findings[0])
        self.assertIn("outside the text areas", r.findings[0])
        pal = list(img.palette)
        pal[3] = (1, 2, 3)                              # palette change
        write_indexed_png(self.tmp / "pal.png", img.indices, pal, 4)
        r = pixel_check(sheet, read_png_image(self.tmp / "pal.png"), area)
        self.assertTrue(any("palette entry 3" in f for f in r.findings))
        wide = [row[:] for row in img.indices]
        wide[10][10] = 20                               # index outside the 16 colours
        write_indexed_png(self.tmp / "idx.png", wide, list(img.palette) + [(0, 0, 0)] * 16, 8)
        r = pixel_check(sheet, read_png_image(self.tmp / "idx.png"), area)
        self.assertTrue(any("outside the 16-colour palette" in f for f in r.findings))
        write_indexed_png(self.tmp / "dim.png", [row + [0] for row in img.indices], img.palette, 4)
        r = pixel_check(sheet, read_png_image(self.tmp / "dim.png"), area)
        self.assertIn("dimensions 33x32", r.findings[0])
        # RGB maps back through the palette; foreign colours fail
        rgb = [list(row) for row in img.rgb]
        write_png(self.tmp / "rgb.png", rgb)
        self.assertEqual(pixel_check(sheet, read_png_image(self.tmp / "rgb.png"), area).findings, [])
        rgb[9][9] = (1, 2, 3)
        write_png(self.tmp / "rgb2.png", rgb)
        r = pixel_check(sheet, read_png_image(self.tmp / "rgb2.png"), area)
        self.assertIn("is not in the palette", r.findings[0])

    def test_area_check_without_the_rom(self):
        # area form against inventory geometry, no ROM pixels
        sheet, _, _ = self.extract("T1")
        cfg, area = config(self.tmp), [(8, 8, 8, 8)]
        px = [row[:] for row in sheet.pixels]
        px[9][9] = 5
        form = png_image(area_form_png(sheet, px, area))
        self.assertEqual((area_size(cfg, cfg.graphic("T1")), area_size(cfg, cfg.graphic("T1"), 20)), ((32, 32), (32, 40)))
        self.assertEqual(area_check(cfg, "T1", form, area), [])
        self.assertEqual(area_check(cfg, "T1", form, ["8,8,8,8"]), [])
        stray = [row[:] for row in form.indices]
        stray[20][3] = 5
        self.assertEqual(area_check(cfg, "T1", png_image(area_png(stray, 4)), area),
                         ["pixel 3,20: index 5 outside the text areas of the area form"])
        self.assertIn("text area 30,30,8,8 is empty or leaves the 32x32 sheet",
                      area_check(cfg, "T1", form, [(8, 8, 8, 8), (30, 30, 8, 8)]))
        self.assertIn("dimensions 32x24", area_check(cfg, "T1", png_image(area_png(stray[:24], 4)), area)[0])
        self.assertEqual(area_check(cfg, "T1", png_image(area_png(stray, 4)), area, 12),
                         ["12 tiles is below the original 16; tiles can only be appended"])
        self.assertIn("only be appended to an LZ77 graphic",
                      area_check(cfg, "T2", png_image(area_png([[0] * 32 for _ in range(8)], 4)), [], 5)[0])
        rgb = png_image(area_form_png(sheet, px, area))
        rgb.indices = None
        self.assertEqual(area_check(cfg, "T1", rgb, area), ["the area form is not an indexed PNG"])

    def test_tile_count(self):
        _, p2, _ = self.extract("T2")
        cfg = GraphicsConfig({"inventory": "inv.tsv", "sheet_width": {"T2": 3}}, self.tmp)
        sheet = cfg.sheet(self.base, cfg.graphic("T2"))   # 4 tiles at 3 per row: two padding tiles
        self.assertEqual((sheet.width, sheet.height), (24, 16))
        px = [row[:] for row in sheet.pixels]
        px[12][20] = 1
        write_indexed_png(self.tmp / "pad.png", px, sheet.palette, 4)
        r = pixel_check(sheet, read_png_image(self.tmp / "pad.png"), [(0, 0, 24, 16)])
        self.assertIn("past the last tile", r.findings[0])

    def test_in_place(self):
        # literals: the re-encoded edit fits its slot
        tmp = self.tmp / "lit"
        tmp.mkdir()
        base, lz_data, _, slot = fixture(tmp, literal=True)
        cfg = config(tmp)
        sheet = cfg.sheet(base, cfg.graphic("T1"))
        small = [row[:] for row in sheet.pixels]
        small[9][9] = 4
        write_indexed_png(tmp / "small.png", small, sheet.palette, 4)
        rom = base.copy()
        res = inject_graphics(rom, base, config(tmp, [("T1", tmp / "small.png", [[8, 8, 8, 8]])]),
                              FreeSpace.scan(bytes(base.data)))
        p = res.placements[0]
        self.assertEqual((res.failures, p.mode, p.new, p.pointers), ([], "in-place", LZ_AT, []))
        self.assertLess(p.length, slot)
        self.assertEqual(rom.ptr_at(0x200), LZ_AT)
        self.assertEqual(lz77_decompress(rom.data, LZ_AT)[0], sheet.encode(small))
        self.assertEqual(verify_graphics(rom, res), [])
        self.assertEqual(verify(rom, base, BuildResult(), {}, set(), graphics_guard(res)), [])
        changed = [i for i in range(len(rom.data)) if rom.data[i] != base.data[i]]
        self.assertTrue(changed and LZ_AT <= min(changed) and max(changed) < LZ_AT + p.length)

    def test_relocation(self):
        # shortest form: noise outgrows the slot, goes to free space
        sheet, png, _ = self.extract("T1")
        img = read_png_image(png)
        rnd = random.Random(5)
        noisy = [[rnd.randrange(16) for _ in row] for row in img.indices]
        write_indexed_png(self.tmp / "noisy.png", noisy, img.palette, 4)
        rom = self.base.copy()
        cfg = config(self.tmp, [("T1", self.tmp / "noisy.png", [[0, 0, 32, 32]])])
        res = inject_graphics(rom, self.base, cfg, FreeSpace.scan(bytes(self.base.data)))
        p = res.placements[0]
        self.assertEqual((res.failures, p.mode, p.pointers), ([], "relocated", [0x200]))
        self.assertGreaterEqual(p.new, 0x10000)
        self.assertEqual(p.new % 4, 0)
        self.assertEqual(rom.ptr_at(0x200), p.new)
        self.assertEqual(rom.read(LZ_AT, self.slot), self.base.read(LZ_AT, self.slot))   # old slot kept
        self.assertEqual(lz77_decompress(rom.data, p.new)[0], sheet.encode(noisy))
        self.assertEqual(verify_graphics(rom, res), [])
        # the diff guard: graphics class allowed, anything else fails
        self.assertEqual(verify(rom, self.base, BuildResult(), {}, set(), graphics_guard(res)), [])
        self.assertTrue(any("diff guard" in f for f in verify(rom, self.base, BuildResult(), {}, set())))
        rom.data[0x500] = 0x42
        self.assertTrue(any("diff guard" in f for f in verify(rom, self.base, BuildResult(), {}, set(),
                                                              graphics_guard(res))))

    def test_relocation_needs_verified_pointers(self):
        _, png, _ = self.extract("T1")
        img = read_png_image(png)
        rnd = random.Random(5)
        write_indexed_png(self.tmp / "noisy.png", [[rnd.randrange(16) for _ in r] for r in img.indices],
                          img.palette, 4)
        cfg = config(self.tmp, [("T1", self.tmp / "noisy.png", [[0, 0, 32, 32]])])
        extra = self.base.copy()
        extra.data[0x300:0x304] = ptr_bytes(LZ_AT)     # a reference the inventory does not list
        rom = extra.copy()
        res = inject_graphics(rom, extra, cfg, FreeSpace.scan(bytes(extra.data)))
        self.assertIn("differ from the inventory", res.failures[0])
        self.assertEqual(rom.data, extra.data)
        stale = self.base.copy()
        stale.data[0x200:0x204] = ptr_bytes(0x1004)    # the listed word no longer holds the address
        rom = stale.copy()
        res = inject_graphics(rom, stale, cfg, FreeSpace.scan(bytes(stale.data)))
        self.assertIn("does not hold", res.failures[0])
        self.assertEqual(rom.data, stale.data)

    def test_cli(self):
        rom_path = self.tmp / "base.gba"
        self.base.save(rom_path)
        prof = self.tmp / "p.toml"
        prof.write_text(f'[rom]\nname = "t"\npath = "{rom_path}"\nsha1 = "{self.base.sha1()}"\n\n'
                        f'[graphics]\ninventory = "{self.tmp / "inv.tsv"}"\n', encoding="utf-8")
        out = self.tmp / "x"
        (self.tmp / "projects.toml").write_text('[defaults]\nprofile = "p"\nlanguage = "ru"\n[[project]]\nprofile = "p"\nlanguage = "ru"\nroot = "."\npolicy = "policy.toml"\n', encoding="utf-8")
        write_policy(self.tmp, Profile.load(prof))
        run = lambda *a: main(["--project-root", str(self.tmp), "--profile", str(prof), *a])
        with contextlib.redirect_stdout(io.StringIO()) as log:
            self.assertEqual(run("build", "gfx-extract", "T1", "T2", "--out", str(out)), 0)
            self.assertEqual(run("build", "gfx-check", "T2", str(out / "T2.png"), "--area", "0,0,8,8"), 0)
            img = read_png_image(out / "T2.png")
            px = [row[:] for row in img.indices]
            px[7][30] = 9
            write_indexed_png(self.tmp / "bad.png", px, img.palette, 4)
            self.assertEqual(run("build", "gfx-check", "T2", str(self.tmp / "bad.png"), "--area", "0,0,8,8", "8,0,8,8"), 1)
        self.assertTrue((out / "T1.json").exists())
        self.assertIn("pixel 30,7: index", log.getvalue())

    def test_cli_appended_tiles(self):
        # Appended tiles belong to the editable area.
        rom_path = self.tmp / "base.gba"
        self.base.save(rom_path)
        prof = self.tmp / "p.toml"
        prof.write_text(f'[rom]\nname = "t"\npath = "{rom_path}"\nsha1 = "{self.base.sha1()}"\n\n'
                        f'[graphics]\ninventory = "{self.tmp / "inv.tsv"}"\n', encoding="utf-8")
        out = self.tmp / "x"
        (self.tmp / "projects.toml").write_text('[defaults]\nprofile = "p"\nlanguage = "ru"\n[[project]]\nprofile = "p"\nlanguage = "ru"\nroot = "."\npolicy = "policy.toml"\n', encoding="utf-8")
        write_policy(self.tmp, Profile.load(prof))
        run = lambda *a: main(["--project-root", str(self.tmp), "--profile", str(prof), *a])
        with contextlib.redirect_stdout(io.StringIO()) as log:
            self.assertEqual(run("build", "gfx-extract", "T1", "T2", "--out", str(out)), 0)
            img = read_png_image(out / "T1.png")          # 16 tiles, 16 per row: no padding
            px = [row[:] for row in img.indices] + [[0] * 128 for _ in range(8)]
            for r in range(8):
                px[8 + r][0:8] = img.indices[r][8:16]     # tile 16 = tile 1
            write_indexed_png(self.tmp / "t1.png", px, img.palette, 4)
            self.assertEqual(run("build", "gfx-check", "T1", str(self.tmp / "t1.png"), "--tiles", "17"), 0)
            self.assertEqual(run("build", "gfx-check", "T1", str(self.tmp / "t1.png")), 1)
            self.assertEqual(run("build", "gfx-check", "T2", str(out / "T2.png"), "--tiles", "5"), 2)
        self.assertIn("appended     64 pixels\nareas        0 areas\nfindings     0 findings", log.getvalue())
        self.assertIn("dimensions 128x16 differ from the original 128x8", log.getvalue())
        self.assertIn("only be appended to an LZ77 graphic", log.getvalue())
