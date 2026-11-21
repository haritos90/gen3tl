from test_roots import TOOLKIT_ROOT
"""Graphics tiles checks and fixtures."""

import sys
from pathlib import Path
import random
import tempfile
import unittest

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.build.graphics import (
    Cell, GraphicsConfig, GraphicsError, lz77_compress, lz77_decompress, inject_graphics,
    pixel_check, pointer_problem, report_lines, verify_graphics,
)
from gen3tl.build.inject import BuildResult, FreeSpace, verify
from gen3tl.check.render import read_png_image, write_indexed_png
from gen3tl.core.rom import Rom, ptr_bytes
from gen3tl.core.notes import BoundProse
from graphics_support import LZ_AT, appended_sheet, config, fixture, graphics_guard, literal_lz77, tiles


TM_AT, TM_PTR, TM_SIZE = 0x6000, 0x208, 1280     # synthetic 32x20 tilemap and its pointer


def tilemap_data(zero: bool = False) -> bytes:
    """32x20 text background entries: tile (row * 32 + column) % 16, palette bank 2 (or all zero)."""
    if zero:
        return bytes(TM_SIZE)
    return b"".join((((r * 32 + c) % 16) | 0x2000).to_bytes(2, "little") for r in range(20) for c in range(32))


def with_tilemap(base: Rom, data: bytes, literal: bool) -> tuple[Rom, int]:
    """A copy of `base` with an LZ77 tilemap at TM_AT and its pointer at TM_PTR; returns it and the block length."""
    rom = base.copy()
    blob = literal_lz77(data) if literal else lz77_compress(data)
    rom.data[TM_AT:TM_AT + len(blob)] = blob
    rom.data[TM_PTR:TM_PTR + 4] = ptr_bytes(TM_AT)
    return rom, len(blob)


def tm_entry(cells, **kw) -> dict:
    return dict({"name": "map", "offset": TM_AT, "size": TM_SIZE, "pointers": [TM_PTR], "cells": cells}, **kw)


def tm_config(tmp: Path, tilemaps=(), edits=(), widths=None) -> GraphicsConfig:
    return GraphicsConfig({"inventory": "inv.tsv", "sheet_width": widths or {"T1": 4},
                           "edit": list(edits), "tilemap": list(tilemaps)}, tmp)


class Lz77Test(unittest.TestCase):
    def test_known_stream(self):
        # literals A, B, then a 4-byte copy from 2 back
        self.assertEqual(lz77_decompress(bytes([0x10, 6, 0, 0, 0x20, 0x41, 0x42, 0x10, 0x01])), (b"ABABAB", 9))

    def test_round_trip(self):
        rnd = random.Random(7)
        samples = [b"", b"A", b"\x00" * 5000, bytes(rnd.randrange(256) for _ in range(3000)),
                   tiles(64, 3), bytes(rnd.choice(b"\x00\x11\x12") for _ in range(9000))]
        for data in samples:
            blob = lz77_compress(data)
            self.assertEqual(lz77_decompress(blob), (data, len(blob)))
            # back references reach at least 2 bytes back
            i, n = 4, 0
            while n < len(data):
                flags = blob[i]
                i += 1
                for bit in range(8):
                    if n >= len(data):
                        break
                    if flags & (0x80 >> bit):
                        self.assertGreaterEqual(((blob[i] & 0xF) << 8 | blob[i + 1]) + 1, 2)
                        n += (blob[i] >> 4) + 3
                        i += 2
                    else:
                        n += 1
                        i += 1

    def test_compresses_repeats(self):
        self.assertLess(len(lz77_compress(b"\x00" * 4096)), 4096 // 8)


class AppendTilesTest(unittest.TestCase):
    """A graphics edit with a tile count above the inventory's appends tiles."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmpdir.name)
        self.base, self.lz_data, _, self.slot = fixture(self.tmp)

    def tearDown(self):
        self.tmpdir.cleanup()

    def edit(self, png, tiles, gid="T1", evidence="test: the added tiles overwrite nothing"):
        e = {"id": gid, "png": str(png), "areas": [], "tiles": tiles}
        return dict(e, evidence=evidence) if evidence else e

    def test_bound_evidence_keeps_appended_tiles_identical(self):
        sheet = config(self.tmp).sheet(self.base, config(self.tmp).graphic("T1"))
        png = self.tmp / "appended.png"
        write_indexed_png(png, appended_sheet(sheet, 1), sheet.palette, 4)
        outputs = []
        for evidence in ("Legacy proof.", BoundProse("First proof.\nSecond proof.")):
            with self.subTest(evidence=evidence):
                edit = dict(self.edit(png, 17), evidence=evidence)
                cfg = tm_config(self.tmp, edits=[edit])
                rom = self.base.copy()
                result = inject_graphics(rom, self.base, cfg, FreeSpace.scan(bytes(self.base.data)))
                self.assertEqual(result.failures, [])
                self.assertEqual(verify_graphics(rom, result), [])
                outputs.append(bytes(rom.data))
        self.assertEqual(outputs, [outputs[0]] * len(outputs))

    def test_pixel_check_in_padding(self):
        cfg = tm_config(self.tmp, widths={"T1": 5})       # 16 tiles at 5 per row: 4 padding tiles
        sheet = cfg.sheet(self.base, cfg.graphic("T1"))
        self.assertEqual((sheet.width, sheet.height), (40, 32))
        px = appended_sheet(sheet, 1)
        write_indexed_png(self.tmp / "a.png", px, sheet.palette, 4)
        img = read_png_image(self.tmp / "a.png")
        r = pixel_check(sheet, img, [], 17)
        self.assertEqual((r.findings, r.changed_inside, r.changed_appended), ([], 0, 64))
        self.assertIn("past the last tile (the graphic has 16 tiles)", pixel_check(sheet, img, []).findings[0])
        px[24][16] = 1                                    # tile 17: padding after the new count
        write_indexed_png(self.tmp / "b.png", px, sheet.palette, 4)
        r = pixel_check(sheet, read_png_image(self.tmp / "b.png"), [], 17)
        self.assertEqual(r.findings, ["pixel 16,24: index 0 -> 1 past the last tile (the graphic has 17 tiles)"])
        self.assertIn("below the original", pixel_check(sheet, img, [], 15).findings[0])

    def test_in_place(self):
        # literals: 17 tiles fit the slot
        tmp = self.tmp / "lit"
        tmp.mkdir()
        base, lz_data, _, slot = fixture(tmp, literal=True)
        cfg = tm_config(tmp, widths={"T1": 5})
        sheet = cfg.sheet(base, cfg.graphic("T1"))
        px = appended_sheet(sheet, 1)
        write_indexed_png(tmp / "a.png", px, sheet.palette, 4)
        rom = base.copy()
        res = inject_graphics(rom, base, tm_config(tmp, edits=[self.edit(tmp / "a.png", 17)], widths={"T1": 5}),
                              FreeSpace.scan(bytes(base.data)))
        p = res.placements[0]
        self.assertEqual((res.failures, p.mode, p.new, p.pointers, p.base_tiles, p.tiles),
                         ([], "in-place", LZ_AT, [], 16, 17))
        data = lz77_decompress(rom.data, LZ_AT)[0]
        self.assertEqual(len(data), 17 * 32)
        self.assertEqual(data[:16 * 32], lz_data)
        self.assertEqual(data, sheet.encode(px, 17))
        self.assertEqual(verify_graphics(rom, res), [])
        self.assertEqual(verify(rom, base, BuildResult(), {}, set(), graphics_guard(res)), [])
        self.assertIn("16 + 1 appended", "\n".join(report_lines(res)))

    def test_added_row_relocates(self):
        # 16 tiles fill the sheet; tile 16 outgrows the slot
        cfg = config(self.tmp)
        sheet = cfg.sheet(self.base, cfg.graphic("T1"))
        write_indexed_png(self.tmp / "same.png", sheet.pixels, sheet.palette, 4)
        r = pixel_check(sheet, read_png_image(self.tmp / "same.png"), [], 17)
        self.assertEqual(r.findings, ["dimensions 32x32 differ from the 17-tile sheet's 32x40"])
        rnd = random.Random(3)
        px = appended_sheet(sheet, 1, tweak=False)
        for y in range(32, 40):
            px[y][0:8] = [rnd.randrange(16) for _ in range(8)]   # incompressible new tile
        write_indexed_png(self.tmp / "a.png", px, sheet.palette, 4)
        rom = self.base.copy()
        res = inject_graphics(rom, self.base, tm_config(self.tmp, edits=[self.edit(self.tmp / "a.png", 17)]),
                              FreeSpace.scan(bytes(self.base.data)))
        p = res.placements[0]
        self.assertEqual((res.failures, p.mode, p.pointers, p.tiles), ([], "relocated", [0x200], 17))
        self.assertGreater(p.length, self.slot)
        self.assertEqual((p.new % 4, rom.ptr_at(0x200)), (0, p.new))
        self.assertEqual(rom.read(LZ_AT, self.slot), self.base.read(LZ_AT, self.slot))   # old slot kept
        data = lz77_decompress(rom.data, p.new)[0]
        self.assertEqual((len(data), data[:16 * 32]), (17 * 32, self.lz_data))
        self.assertEqual(verify_graphics(rom, res), [])
        self.assertEqual(verify(rom, self.base, BuildResult(), {}, set(), graphics_guard(res)), [])

    def test_refused(self):
        cfg = tm_config(self.tmp, widths={"T1": 5})
        sheet = cfg.sheet(self.base, cfg.graphic("T1"))
        write_indexed_png(self.tmp / "a.png", appended_sheet(sheet, 1), sheet.palette, 4)
        raw = cfg.sheet(self.base, cfg.graphic("T2"))
        write_indexed_png(self.tmp / "raw.png", raw.pixels, raw.palette, 4)
        cases = [
            (self.edit(self.tmp / "a.png", 15), "below the original 16"),
            (self.edit(self.tmp / "a.png", 17, evidence=""), "need evidence"),
            (self.edit(self.tmp / "raw.png", 5, gid="T2"), "only be appended to an LZ77 graphic"),
        ]
        for e, why in cases:
            rom = self.base.copy()
            res = inject_graphics(rom, self.base, tm_config(self.tmp, edits=[e], widths={"T1": 5}),
                                  FreeSpace.scan(bytes(self.base.data)))
            self.assertEqual(len(res.failures), 1, why)
            self.assertIn(why, res.failures[0])
            self.assertEqual(rom.data, self.base.data)
        with self.assertRaisesRegex(GraphicsError, "integer"):
            tm_config(self.tmp, edits=[self.edit(self.tmp / "a.png", "17")])


class TilemapTest(unittest.TestCase):
    """[[graphics.tilemap]] changes cells of an LZ77 tilemap at the same decoded size."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmpdir.name)
        self.rom0, _, _, _ = fixture(self.tmp)

    def tearDown(self):
        self.tmpdir.cleanup()

    def build(self, base, entries, edits=()):
        rom = base.copy()
        res = inject_graphics(rom, base, tm_config(self.tmp, entries, edits), FreeSpace.scan(bytes(base.data)))
        return rom, res

    @staticmethod
    def entries(rom, at):
        data = lz77_decompress(rom.data, at)[0]
        return [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)]

    def test_cell(self):
        c = Cell(0, 0, tile=0x1D1, hflip=True, palette=3)
        self.assertEqual(c.apply(0x5C08), 0x3DD1)        # vflip kept, hflip set, palette 5 -> 3
        self.assertEqual(Cell(0, 0, vflip=False).apply(0x5C08), 0x5408)
        self.assertEqual(Cell(0, 0, hflip=False, palette=0).apply(0x5C08), 0x0808)

    def test_bound_placement_notes_keep_graphics_and_reports(self):
        from project_fixture import with_profile_notes

        base, _ = with_tilemap(self.rom0, tilemap_data(), literal=True)
        placement = tm_entry([{"row": 0, "column": 8, "tile": 9}],
                             evidence="Measured placement.\nVerified tilemap.")
        expected, _ = self.build(base, [placement])
        for status in ("unreviewed", "accepted"):
            with self.subTest(status=status):
                migrated = with_profile_notes(placement, status)
                rom, result = self.build(base, [migrated])
                self.assertEqual(result.failures, [])
                self.assertEqual(verify_graphics(rom, result), [])
                self.assertEqual(bytes(rom.data), bytes(expected.data))
                self.assertIn("| map | in-place |", "\n".join(report_lines(result)))

    def test_in_place(self):
        base, slot = with_tilemap(self.rom0, tilemap_data(), literal=True)
        rom, res = self.build(base, [tm_entry([{"row": 0, "column": 8, "tile": 9, "hflip": True},
                                               {"row": 19, "column": 31, "palette": 7, "vflip": True}])])
        t = res.tilemaps[0]
        self.assertEqual((res.failures, t.mode, t.new, t.pointers, t.slot), ([], "in-place", TM_AT, [], slot))
        self.assertLess(t.length, slot)
        self.assertEqual(t.changes, [(0, 8, 0x2008, 0x2409), (19, 31, 0x200F, 0x780F)])
        got, was = self.entries(rom, TM_AT), self.entries(base, TM_AT)
        self.assertEqual(len(got), TM_SIZE // 2)
        self.assertEqual([i for i in range(len(got)) if got[i] != was[i]], [8, 639])
        self.assertEqual((got[8], got[639]), (0x2409, 0x780F))
        self.assertEqual(verify_graphics(rom, res), [])
        self.assertEqual(verify(rom, base, BuildResult(), {}, set(), graphics_guard(res)), [])
        changed = [i for i in range(len(rom.data)) if rom.data[i] != base.data[i]]
        self.assertTrue(changed and TM_AT <= min(changed) and max(changed) < TM_AT + t.length)
        self.assertIn("| map | in-place |", "\n".join(report_lines(res)))

    def test_unchanged_cell_writes_nothing(self):
        base, _ = with_tilemap(self.rom0, tilemap_data(), literal=True)
        rom, res = self.build(base, [tm_entry([{"row": 0, "column": 8, "tile": 8, "palette": 2}])])
        self.assertEqual((res.failures, res.tilemaps[0].mode, res.allowed), ([], "same", set()))
        self.assertEqual(rom.data, base.data)

    def test_relocated_with_pointer_rewrite(self):
        # an all-zero map compresses small; two cells outgrow it
        base, slot = with_tilemap(self.rom0, tilemap_data(zero=True), literal=False)
        rom, res = self.build(base, [tm_entry([{"row": 0, "column": 8, "tile": 9},
                                               {"row": 10, "column": 3, "palette": 15}])])
        t = res.tilemaps[0]
        self.assertEqual((res.failures, t.mode, t.pointers), ([], "relocated", [TM_PTR]))
        self.assertGreater(t.length, slot)
        self.assertGreaterEqual(t.new, 0x10000)
        self.assertEqual((t.new % 4, rom.ptr_at(TM_PTR)), (0, t.new))
        self.assertEqual(rom.read(TM_AT, slot), base.read(TM_AT, slot))          # old block kept
        got = self.entries(rom, t.new)
        self.assertEqual(len(got), TM_SIZE // 2)
        self.assertEqual({i: v for i, v in enumerate(got) if v}, {8: 9, 10 * 32 + 3: 0xF000})
        self.assertEqual(verify_graphics(rom, res), [])
        self.assertEqual(verify(rom, base, BuildResult(), {}, set(), graphics_guard(res)), [])
        report = "\n".join(report_lines(res))
        self.assertIn("(0, 8): 0x0000 -> 0x0009; (10, 3): 0x0000 -> 0xF000", report)
        self.assertIn(f"0x{TM_PTR:07X}", report)
        # a stale pointer after the build fails verification
        rom.data[TM_PTR:TM_PTR + 4] = ptr_bytes(TM_AT)
        self.assertIn(f"pointer at {TM_PTR:#x} does not point", verify_graphics(rom, res)[0])

    def test_relocation_needs_verified_pointers(self):
        base, _ = with_tilemap(self.rom0, tilemap_data(zero=True), literal=False)
        cells = [{"row": 0, "column": 8, "tile": 9}, {"row": 10, "column": 3, "palette": 15}]
        extra = base.copy()
        extra.data[0x30C:0x310] = ptr_bytes(TM_AT)       # a reference the profile does not list
        cases = [(extra, tm_entry(cells), "differ from the profile's"),
                 (base, tm_entry(cells, pointers=[]), "holds no pointer"),
                 (base, tm_entry(cells, pointers=[0x20C]), "does not hold")]
        for b, entry, why in cases:
            rom, res = self.build(b, [entry])
            self.assertEqual(len(res.failures), 1, why)
            self.assertIn("cannot be relocated", res.failures[0])
            self.assertIn(why, res.failures[0])
            self.assertEqual(rom.data, b.data)
        self.assertEqual(pointer_problem(base, TM_AT, [TM_PTR], "the profile's"), "")

    def test_size_check(self):
        base, _ = with_tilemap(self.rom0, tilemap_data(zero=True), literal=False)
        rom, res = self.build(base, [tm_entry([{"row": 0, "column": 8, "tile": 9}], size=TM_SIZE + 64)])
        self.assertEqual(res.failures, [f"tilemap map: the LZ77 block at {TM_AT:#x} decodes to {TM_SIZE} bytes; "
                                        f"the profile declares {TM_SIZE + 64}"])
        self.assertEqual(rom.data, base.data)
        # verification decodes the written block
        rom, res = self.build(base, [tm_entry([{"row": 0, "column": 8, "tile": 9}])])
        self.assertEqual(verify_graphics(rom, res), [])
        t = res.tilemaps[0]
        short = lz77_compress(t.data[:-2])
        rom.data[t.new:t.new + len(short)] = short
        self.assertEqual(verify_graphics(rom, res),
                         [f"tilemap map: the block at {t.new:#x} decodes to {TM_SIZE - 2} bytes, "
                          f"the base ROM's to {TM_SIZE}"])

    def test_tile_index_against_the_graphic(self):
        base, _ = with_tilemap(self.rom0, tilemap_data(), literal=True)
        cell = [{"row": 0, "column": 8, "tile": 16}]
        rom, res = self.build(base, [tm_entry(cell, graphic="T1")])
        self.assertEqual(res.failures, ["tilemap map: cell (0, 8) places tile 16; T1 has 16 tiles"])
        self.assertEqual(rom.data, base.data)
        # with T1 grown to 17 tiles the cell is valid
        cfg = tm_config(self.tmp, widths={"T1": 5})
        sheet = cfg.sheet(base, cfg.graphic("T1"))
        write_indexed_png(self.tmp / "a.png", appended_sheet(sheet, 1), sheet.palette, 4)
        edit = {"id": "T1", "png": str(self.tmp / "a.png"), "areas": [], "tiles": 17, "evidence": "test"}
        rom = base.copy()
        res = inject_graphics(rom, base, tm_config(self.tmp, [tm_entry(cell, graphic="T1")], [edit], {"T1": 5}),
                              FreeSpace.scan(bytes(base.data)))
        self.assertEqual(res.failures, [])
        self.assertEqual(self.entries(rom, res.tilemaps[0].new)[8], 0x2010)
        self.assertEqual(verify_graphics(rom, res), [])

    def test_two_entries_for_one_tilemap(self):
        base, _ = with_tilemap(self.rom0, tilemap_data(), literal=True)
        rom, res = self.build(base, [tm_entry([{"row": 0, "column": 8, "tile": 9}]),
                                     tm_entry([{"row": 1, "column": 8, "tile": 9}], name="again")])
        self.assertEqual(res.failures, ["tilemap again: same ROM tilemap as map; list all its cells in one entry"])

    def test_parse_errors(self):
        good = {"row": 0, "column": 8, "tile": 9}
        bad = [
            ({"name": "m", "offset": TM_AT, "size": TM_SIZE, "cells": [good]}, "needs"),
            (tm_entry([good], colour=1), "unknown key"),
            (tm_entry([good], size=TM_SIZE + 2), "whole number"),
            (tm_entry([]), "non-empty"),
            (tm_entry([{"row": 20, "column": 0, "tile": 1}]), "row 20"),
            (tm_entry([{"row": 0, "column": 32, "tile": 1}]), "column 32"),
            (tm_entry([good, dict(good, tile=10)]), "listed twice"),
            (tm_entry([{"row": 0, "column": 0, "palette": 16}]), "palette 16"),
            (tm_entry([{"row": 0, "column": 0, "tile": 1024}]), "tile 1024"),
            (tm_entry([{"row": 0, "column": 0, "hflip": 1}]), "not true or false"),
            (tm_entry([{"row": 0, "column": 0}]), "needs row, column and tile"),
            (tm_entry([dict(good, flip=True)]), "unknown key"),
        ]
        for entry, why in bad:
            with self.assertRaisesRegex(GraphicsError, why):
                tm_config(self.tmp, [entry])
        cfg = tm_config(self.tmp, [tm_entry([good], width=16, size=640, graphic="T1", evidence="e")])
        m = cfg.tilemaps[0]
        self.assertEqual((m.width, m.rows, m.graphic, m.cells), (16, 20, "T1", [Cell(0, 8, tile=9)]))
