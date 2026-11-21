from test_roots import TOOLKIT_ROOT
"""Graphics discovery flagging checks and fixtures."""

import sys
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.build.graphics import palette_colours
from gen3tl.build.graphics import GraphicsConfig, graphics_document, inventory_of, inventory_rows
from gen3tl.discover import graphics as gfx
import graphics_discovery_support
from graphics_discovery_support import rom_with_lz77


class FlaggingTest(graphics_discovery_support.FlaggingTest, unittest.TestCase):
    def test_flagging_preserves_separate_views(self):
        from gen3tl.core.graphics import preview_views, write_preview_views
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "graphics.toml"
            path.write_text(self.document().split("[[views.", 1)[0], encoding="utf-8")
            directory = root / "graphics" / "views"
            views = {"G01": [{"label": "Complete tilemap", "width": 32, "height": 32,
                               "cells": [[0, 0, 0, 0]]}]}
            files = write_preview_views(directory, views)
            before = {p: p.read_bytes() for p in files}
            self.assertEqual(gfx.write_flags(path, [{"id": "G01", "category": "translate", "reason": "Text"}]),
                             ["G01"])
            self.assertEqual(inventory_rows(path)[0]["category"], "translate")
            self.assertEqual(preview_views(directory), views)
            self.assertEqual(before, {p: p.read_bytes() for p in files})
            self.assertNotIn("views", path.read_text(encoding="utf-8"))

    def test_flagging_writes_only_flag_fields_and_build_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "graphics.toml"
            before = self.document()
            path.write_text(before, encoding="utf-8")
            updated = gfx.write_flags(path, [{"id": "g0001000", "category": "translate",
                                              "reason": "new reason"}])
            self.assertEqual(updated, ["G01"])
            after = path.read_text(encoding="utf-8")
            self.assertNotIn('category = "keep"', after)
            self.assertNotIn('reason = "old reason"', after)
            self.assertIn('category = "translate"', after)
            self.assertIn('reason = "new reason"', after)
            self.assertEqual(after.replace('category = "translate"', 'category = "keep"')
                                 .replace('reason = "new reason"', 'reason = "old reason"'), before)
            g = inventory_of(inventory_rows(path))["G01"]
            self.assertEqual(g.category, "translate")

    def test_flagging_refuses_unknown_duplicate_and_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "graphics.toml"
            path.write_text(self.document(), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "no \\[\\[graphic\\]\\] row"):
                gfx.write_flags(path, [{"id": "G99", "category": "keep", "reason": "x"}])
            with self.assertRaisesRegex(ValueError, "category"):
                gfx.write_flags(path, [{"id": "G01", "category": "drop", "reason": "x"}])
            with self.assertRaisesRegex(ValueError, "reason"):
                gfx.write_flags(path, [{"id": "G01", "category": "keep", "reason": " "}])
            dup = Path(tmp) / "dup.toml"
            dup.write_text('version = "2.0.0"\n\n[[graphic]]\nid = "G04"\nrom_offset = "0x1000"\n'
                           'category = "keep"\nreason = "a"\n\n[[graphic]]\nid = "G05"\n'
                           'rom_offset = "0x1000"\ncategory = "keep"\nreason = "b"\n',
                           encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "G04, G05"):
                gfx.write_flags(dup, [{"id": "g0001000", "category": "keep", "reason": "x"}])

    def test_new_candidate_becomes_build_record_with_unsafe_relocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "graphics.toml"
            path.write_text(self.document(), encoding="utf-8")
            before = graphics_document(path)
            rom = self.proved_raw_rom()
            doc = gfx.write(gfx.scan(rom), rom, Path(tmp) / "discovery", render=False)
            updated = gfx.write_flags(path, [{"id": "g0002000", "category": "translate",
                                              "reason": "newly discovered text"}], doc, rom)
            self.assertEqual(updated, ["g0002000"])
            after = graphics_document(path)
            self.assertEqual(after["views"], before["views"])
            self.assertEqual(after["graphic"][0], before["graphic"][0])
            cfg = GraphicsConfig({"inventory": str(path)}, Path(tmp))
            graphic = cfg.graphic("g0002000")
            self.assertEqual((graphic.offset, graphic.size, graphic.tiles, graphic.bpp), (0x2000, 128, 4, 4))
            self.assertEqual(graphic.pointers, [0x200, 0x206, 0x400, 0x708])
            self.assertFalse(graphic.pointers_complete)
            sheet = cfg.sheet(rom, graphic)
            self.assertEqual((sheet.width, sheet.height), (32, 8))
            self.assertEqual(sheet.palette, palette_colours(rom, "raw:0x3000", 4)[0])

    def test_new_candidate_without_palette_is_transactionally_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "graphics.toml"
            before = self.document()
            path.write_text(before, encoding="utf-8")
            rom = rom_with_lz77(bytes(range(96)), 0x2000, 0x200)
            doc = gfx.write(gfx.scan(rom), rom, Path(tmp) / "discovery", render=False)
            record = doc["candidates"][0]
            record.update(palette="raw:0x0003000", palette_source="rom-evidence", palette_bpp=4,
                          palette_variants=[{"spec": "raw:0x0003000", "bpp": 4, "bank": 0,
                                             "tier": "consumer", "evidence": {"method": "forged"}}])
            with self.assertRaisesRegex(ValueError, "palette"):
                gfx.write_flags(path, [{"id": "g0002000", "category": "translate", "reason": "text"}], doc, rom)
            self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_claimed_palette_source_cannot_replace_a_proof(self):
        record = self.discovery_record()
        record["palette_variants"] = [{"spec": "raw:0x3000", "bpp": 4, "bank": 0, "tier": "candidate"}]
        with self.assertRaisesRegex(ValueError, "ROM-evidenced palette"):
            gfx._candidate_row(record, "keep", "untrusted")
