from test_roots import TOOLKIT_ROOT
"""Graphics discovery candidates checks and fixtures."""

import sys
from pathlib import Path
import json
import struct
import tempfile
import unittest

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.build import graphics as build_graphics
from gen3tl.build.graphics import GREY, lz77_compress, palette_colours, tiles_to_pixels
from gen3tl.check.render import read_png_image
from gen3tl.core.rom import Rom, ptr_bytes
from gen3tl.discover import graphics as gfx
from graphics_discovery_support import bl_bytes, candidate, ldr_literal, probe_palette, rom_with_lz77, words


class BuildCompatibilityTest(unittest.TestCase):
    def test_lz77_constants_remain_build_exports(self):
        self.assertEqual((build_graphics.LZ77_TYPE, build_graphics.LZ77_WINDOW, build_graphics.LZ77_MIN,
                          build_graphics.LZ77_MAX, build_graphics.LZ77_MIN_DISP),
                         (0x10, 0x1000, 3, 18, 2))


class CandidateScanTest(unittest.TestCase):
    def test_true_false_and_bounds_candidates(self):
        rom = rom_with_lz77(bytes(range(128)), 0x1000, 0x200)
        rom.data[0x3000:0x3004] = bytes([0x10, 0xFF, 0xFF, 0xFF])   # oversized
        rom.data[0x208:0x20C] = ptr_bytes(0x3000)
        rom.data[0x4000:0x4003] = bytes([0x10, 2, 0, 0])            # truncated header
        rom.data[0x20C:0x210] = ptr_bytes(0x4000)
        rom.data[0x5000] = 0x11                                      # a pointer to a non-LZ77 target
        rom.data[0x210:0x214] = ptr_bytes(0x5000)
        rom.data[0x6000] = 0x10                                      # unreferenced LZ77-shaped byte
        result = gfx.scan(rom)
        self.assertEqual([c.offset for c in result["candidates"]], [0x1000])
        self.assertEqual({r["offset"] for r in result["rejected"]}, {0x3000, 0x4000})
        self.assertEqual(result["stats"]["referenced_lz77_headers"], 3)
        self.assertGreaterEqual(result["stats"]["pointer_targets"], 4)   # compressed bytes may add accidental pointers
        candidate = result["candidates"][0]
        self.assertEqual(candidate.decoded_size, 128)
        self.assertEqual(candidate.block_length, len(lz77_compress(bytes(range(128)))))
        self.assertEqual(candidate.reference_kinds(), ["aligned"])
        self.assertFalse(candidate.relocation_ambiguous())

    def test_reference_classification(self):
        data = bytes(range(128))
        rom = rom_with_lz77(data, 0x1000, 0x300)   # the aligned, no-neighbour ref
        rom.write(0x210, ptr_bytes(0x1000))        # a data-table ref with a neighbour
        rom.write(0x214, ptr_bytes(0x1000))
        rom.write(0x2F0, words(0x4803))            # ldr r0, [pc, #12] -> pool word at 0x300
        rom.write(0x221, ptr_bytes(0x1000))        # an unaligned ref
        c = candidate(rom, 0x1000)
        kinds = {r.kind for r in c.refs}
        self.assertEqual(kinds, {"pool", "table", "unaligned"})
        self.assertTrue(c.relocation_ambiguous())
        pool = next(r for r in c.refs if r.kind == "pool")
        self.assertEqual(pool.at, 0x300)
        unaligned = next(r for r in c.refs if r.kind == "unaligned")
        self.assertEqual(unaligned.at, 0x221)

    def test_deterministic_scan(self):
        rom = rom_with_lz77(bytes(range(256)), 0x1000, 0x200)
        first = gfx.scan(rom)
        second = gfx.scan(rom)
        self.assertEqual([c.to_dict() for c in first["candidates"]],
                         [c.to_dict() for c in second["candidates"]])
        self.assertEqual(first["rejected"], second["rejected"])
        self.assertEqual(first["stats"], second["stats"])


class RenderTest(unittest.TestCase):
    def test_indexed_png_roundtrip_for_4bpp_and_8bpp(self):
        data = bytes(range(128))
        rom = rom_with_lz77(data, 0x1000, 0x200)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            result = gfx.scan(rom)
            rendered = gfx.render_candidates(rom, result, out)
            by_mode = {r["mode"]: r for r in rendered}
            for mode, bpp, tiles in (("tile-sheet-4bpp", 4, 4), ("tile-sheet-8bpp", 8, 2)):
                name = f"g0001000.png" if mode == "tile-sheet-4bpp" else "g0001000-8bpp.png"
                wt = min(16, tiles)
                img = read_png_image(out / "png" / name)
                self.assertIsNotNone(img.indices)
                self.assertEqual(img.indices, tiles_to_pixels(data, tiles, bpp, wt))
                self.assertEqual(img.palette, GREY if bpp == 4 else [(i, i, i) for i in range(256)])
                self.assertEqual(by_mode[mode]["width"], wt * 8)
                self.assertEqual(by_mode[mode]["height"], 8)
                self.assertEqual(by_mode[mode]["palette"], "grey (palette not located)")
                self.assertEqual(by_mode[mode]["palette_source"], "missing")
                self.assertIsNone(by_mode[mode]["palette_choice"])

    def test_bytes_mode_for_non_tile_size(self):
        data = bytes([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        rom = rom_with_lz77(data, 0x1000, 0x200)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            result = gfx.scan(rom)
            rendered = gfx.render_candidates(rom, result, out)
            self.assertEqual(rendered[0]["mode"], "bytes")
            img = read_png_image(out / "png" / "g0001000.png")
            self.assertEqual(img.width, 16)
            self.assertEqual(img.height, 1)
            self.assertEqual(img.palette, [(i, i, i) for i in range(256)])


class PaletteChoiceTest(unittest.TestCase):
    def variant(self, spec, method, site, bpp=4, tier="candidate"):
        return {"spec": spec, "bpp": bpp, "bank": 0, "tier": tier,
                "evidence": {"method": method, "graphic_load": site}}

    def test_choice_order_method_alternatives_and_site(self):
        c = gfx.Candidate(0x1000, 40, 128)
        c.palettes = [self.variant("raw:0x0003000", "code-window-candidate", 0x100),
                      self.variant("raw:0x0003020", "descriptor-triple-candidate", 0x500)]
        self.assertEqual(c.palette_choice(4)["spec"], "raw:0x0003020")

        c.palettes = [self.variant("raw:0x0003000", "code-window-candidate", 0x200),
                      self.variant("raw:0x0003020", "code-window-candidate", 0x200),
                      self.variant("raw:0x0003040", "code-window-candidate", 0x400)]
        self.assertEqual(c.palette_choice(4)["spec"], "raw:0x0003040")

        c.palettes = [self.variant("raw:0x0003000", "code-window-candidate", 0x500),
                      self.variant("raw:0x0003020", "code-window-candidate", 0x300)]
        self.assertEqual(c.palette_choice(4)["spec"], "raw:0x0003020")

    def result_with_variants(self):
        rom = rom_with_lz77(bytes(range(128)), 0x1000, 0x200)
        rom.write(0x3000, probe_palette(1))
        rom.write(0x3020, probe_palette(2))
        result = gfx.scan(rom)
        c = result["candidates"][0]
        c.palettes = [self.variant("raw:0x0003000", "code-window-candidate", 0x500),
                      self.variant("raw:0x0003020", "code-window-candidate", 0x300)]
        result["stats"]["palette_selected"] = 1
        result["stats"]["palette_grey"] = 0
        result["stats"]["palette_variants"] = 2
        row = {"id": "G01", "rom_offset": "0x1000",
               "compressed": f"yes (LZ77, {c.block_length} bytes)", "size_bytes": "128",
               "tiles": "4", "palette": "raw:0x0003000 (as loaded)"}
        return rom, result, row

    def test_bytes_use_a_4bpp_choice_for_all_indices(self):
        rom = rom_with_lz77(bytes(range(10)), 0x1000, 0x200)
        rom.write(0x3000, probe_palette(1))
        result = gfx.scan(rom)
        result["candidates"][0].palettes = [
            self.variant("raw:0x0003000", "code-window-candidate", 0x300)]
        with tempfile.TemporaryDirectory() as tmp:
            rendered = gfx.render_candidates(rom, result, Path(tmp))
            self.assertEqual(rendered[0]["palette"], "raw:0x0003000")
            self.assertEqual(rendered[0]["palette_bpp"], 4)
            img = read_png_image(Path(tmp) / "png/g0001000.png")
            self.assertEqual(img.palette, palette_colours(rom, "raw:0x0003000", 4)[0] * 16)

    def test_catalogue_changes_do_not_change_choice(self):
        rom, result, row = self.result_with_variants()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = gfx.write(result, rom, root / "first", rows=[row], render=False)
            changed = {**row, "palette": "raw:0x0003020 (as loaded)"}
            second = gfx.write(result, rom, root / "second", rows=[changed], render=False)
            self.assertEqual(first["candidates"][0]["palette_choice"],
                             second["candidates"][0]["palette_choice"])
            self.assertEqual(first["candidates"][0]["palette"], "raw:0x0003020")
            self.assertEqual(first["recall"]["palette_comparison"][0]["status"], "different")
            self.assertEqual(second["recall"]["palette_comparison"][0]["status"], "matched")

    def test_deterministic_index_report_and_pictures(self):
        rom, result, row = self.result_with_variants()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gfx.write(result, rom, root / "first", rows=[row])
            gfx.write(result, rom, root / "second", rows=[row])
            first = {p.relative_to(root / "first"): p.read_bytes()
                     for p in (root / "first").rglob("*") if p.is_file()}
            second = {p.relative_to(root / "second"): p.read_bytes()
                     for p in (root / "second").rglob("*") if p.is_file()}
            self.assertEqual(first, second)
            doc = json.loads(first[Path("index.json")])
            choice = doc["candidates"][0]["palette_choice"]
            self.assertEqual((choice["spec"], choice["tier"]), ("raw:0x0003020", "candidate"))
            self.assertEqual([p["spec"] for p in doc["candidates"][0]["palette_variants_not_chosen"]],
                             ["raw:0x0003000"])
            img = read_png_image(root / "first/png/g0001000.png")
            self.assertEqual(img.palette, palette_colours(rom, "raw:0x0003020", 4)[0])


class EvidencedTilemapTest(unittest.TestCase):
    def rom(self, bank=0):
        rom = Rom(b"\xff" * 0x10000)
        rom.write(0x1000, lz77_compress(bytes(range(128))))
        entries = [(i % 4) | ((i % 2) << 10) | ((i % 3 == 2) << 11) | (bank << 12) for i in range(640)]
        rom.write(0x2000, lz77_compress(words(*entries)))
        rom.write(0x3000, bytes(range(32)))
        rom.write(0x200, ptr_bytes(0x1000) + ptr_bytes(0x2000) + ptr_bytes(0x3000))
        rom.write(0x20C, (0x06000000).to_bytes(4, "little"))
        rom.write(0x210, (0x06008000).to_bytes(4, "little"))
        rom.write(0x214, (0x05000000).to_bytes(4, "little"))
        rom.write(0x218, (0x04000008).to_bytes(4, "little"))
        rom.write(0x21C, (0x1000).to_bytes(4, "little"))
        # LZ77 sheet/map sources and VRAM destinations.
        # Palette source, palette RAM, sixteen halfwords.
        code = (words(0x483F, 0x4942) + bl_bytes(0x104, 0x300) +
                words(0x483E, 0x4941) + bl_bytes(0x10C, 0x300) +
                words(0x483D, 0x4940, 0x2210) + bl_bytes(0x116, 0x306) +
                words(ldr_literal(0x11A, 3, 0x218), ldr_literal(0x11C, 4, 0x21C), 0x801C, 0x4770))
        rom.write(0x100, code)
        rom.write(0x300, words(0xDF12, 0x4770))   # swi 0x12 (LZ77Vram); bx lr
        rom.write(0x306, words(0xDF0B, 0x4770))   # swi 0x0B (CpuSet); bx lr
        return rom

    def test_palette_and_composed_tilemap_from_code_evidence(self):
        rom = self.rom()
        result = gfx.scan(rom)
        sheet = candidate(rom, 0x1000)
        tilemap = candidate(rom, 0x2000)
        self.assertEqual(sheet.palette_for(4)["spec"], "raw:0x0003000")
        self.assertEqual(len(tilemap.tilemaps), 1)
        association = tilemap.tilemaps[0]
        self.assertEqual((association["width"], association["height"], association["sheet_offset"]),
                         (32, 20, 0x1000))
        with tempfile.TemporaryDirectory() as tmp:
            rendered = gfx.render_candidates(rom, result, Path(tmp), limit=3)
            record = next(r for r in rendered if r["mode"] == "tilemap")
            self.assertEqual((record["width"], record["height"], record["palette_source"]),
                             (256, 160, "rom-evidence"))
            self.assertEqual(record["palette"], sheet.palette_choice(4)["spec"])
            self.assertEqual(record["palette_choice"]["spec"], sheet.palette_choice(4)["spec"])
            img = read_png_image(Path(tmp) / "png/g0002000-map-0.png")
            self.assertEqual(img.indices[0][0], 0)
            self.assertEqual(img.indices[0][8], 2)
            self.assertEqual(img.indices[0][16], 12)
            self.assertEqual(img.palette[:16], palette_colours(rom, "raw:0x3000", 4)[0])

    def test_missing_palette_bank_and_bare_pointer_do_not_compose(self):
        result = gfx.scan(self.rom(bank=1))
        tilemap = next(c for c in result["candidates"] if c.offset == 0x2000)
        self.assertEqual(tilemap.tilemaps, [])
        bare = rom_with_lz77(bytes(1280), 0x1000, 0x200)
        found = candidate(bare, 0x1000)
        self.assertEqual(found.tilemaps, [])
        self.assertEqual(found.palettes, [])


class RecallTest(unittest.TestCase):
    def rows(self):
        block = len(lz77_compress(bytes(range(128))))
        return [{"id": "G01", "rom_offset": "0x1000", "compressed": f"yes (LZ77, {block} bytes)",
                 "size_bytes": "128", "tiles": "4", "palette": "not found"},
                {"id": "G02", "rom_offset": "0x3000", "compressed": "no", "size_bytes": "128",
                 "tiles": "4", "palette": "not found"}]

    def result(self):
        rom = rom_with_lz77(bytes(range(128)), 0x1000, 0x200)
        for at, target in ((0x400, 0x3000), (0x408, 0x3080), (0x410, 0x3100)):
            rom.write(at, ptr_bytes(target) + struct.pack("<I", 128))
            rom.write(target, bytes(128))
        return gfx.scan(rom)

    def test_recall_matches_lz77_and_raw_candidates(self):
        rec = gfx.recall(self.result(), self.rows())
        self.assertTrue(rec["complete"])
        self.assertEqual(len(rec["matched"]), 2)
        self.assertEqual(len(rec["raw"]), 1)
        self.assertTrue(all(m["size_matches"] and m["block_matches"] and m["compression_matches"]
                            for m in rec["matched"]))
        self.assertEqual(rec["raw"][0]["size_evidence"]["method"], "pointer-size-table")
        self.assertEqual(rec["missing"], [])

    def test_missing_size_and_block_mismatches_fail_completeness(self):
        rows = self.rows()
        rows[0]["compressed"] = "yes (LZ77, 999 bytes)"
        rows[1]["size_bytes"] = "64"
        rec = gfx.recall(self.result(), rows)
        self.assertFalse(rec["complete"])
        self.assertFalse(rec["matched"][0]["block_matches"])
        self.assertFalse(rec["matched"][1]["size_matches"])
        rec = gfx.recall(self.result(), rows + [{"id": "G99", "rom_offset": "0x5000",
                                                "compressed": "no", "size_bytes": "32", "tiles": "1"}])
        self.assertFalse(rec["complete"])
        self.assertEqual(rec["missing"], [{"id": "G99", "offset": 0x5000}])
