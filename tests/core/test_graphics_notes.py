from test_roots import TOOLKIT_ROOT
"""Graphics notes checks and fixtures."""

import sys
from pathlib import Path
import json
import tempfile
import unittest
from copy import deepcopy

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.build.graphics import (
    GraphicsConfig, GraphicsError, inject_graphics, verify_graphics, graphic_notes,
    graphics_document, graphics_text, inventory_rows, preview_views,
)
from gen3tl.build.inject import FreeSpace
from gen3tl.check.render import write_indexed_png
from gen3tl.core.notes import write_notes
from gen3tl.discover.graphics import write_flags
from graphics_support import config, fixture


class PreviewFilesTest(unittest.TestCase):
    def test_round_trip_preserves_order_and_writes_one_line(self):
        from gen3tl.core.graphics import write_preview_views
        views = {"T2": [{"map": {"width": 8, "offset": 16}, "label": "Map"}],
                 "T1": [{"label": "Layout", "cells": [[8, 0, 2, 1], [0, 0, 1, 0]], "transparent": True},
                        {"label": "Second view", "rects": [[0, 0, 8, 8, 0, 0]]}]}
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "views"
            written = write_preview_views(directory, views)
            self.assertEqual([p.name for p in written], ["T1.json", "T2.json"])
            expected = {gid: views[gid] for gid in sorted(views)}
            self.assertEqual(json.dumps(preview_views(directory)), json.dumps(expected))
            before = {p: p.read_bytes() for p in written}
            for gid, records in views.items():
                self.assertEqual(before[directory / f"{gid}.json"],
                                 (json.dumps(records, ensure_ascii=False, separators=(",", ":")) + "\n").encode())
            write_preview_views(directory, preview_views(directory))
            self.assertEqual(before, {p: p.read_bytes() for p in written})

    def test_invalid_ids_and_shapes_are_rejected_before_writing(self):
        from gen3tl.core.graphics import write_preview_views
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "views"
            for views in ({"../escape": []}, {"T1": {}}, {"T1": [1]}):
                with self.subTest(views=views), self.assertRaises(GraphicsError):
                    write_preview_views(directory, views)
                self.assertFalse(directory.exists())
            directory.mkdir()
            path = directory / "T1.json"
            for text in ("{", "{}", "[1]"):
                path.write_text(text, encoding="utf-8")
                with self.subTest(text=text), self.assertRaises(GraphicsError):
                    preview_views(directory)


class GraphicsNotesTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.base, *_ = fixture(self.root)
        self.rows = inventory_rows(self.root / "inv.tsv")
        self.path = self.root / "graphics.toml"
        self.notes_path = self.root / "notes" / "graphics.toml"
        self.previews = {"version": 1, "views": {"T1": [{"label": "Whole sheet", "width": 32}]}}

    def note(self, nid="artwork", gid="T1", language=None):
        return {"id": nid, "subject": {"kind": "graphic", "id": gid},
                "scope": {"kind": "language", "language": language} if language else {"kind": "shared"},
                "binding": {"kind": "graphic", "id": gid}, "facts": ["The label occupies eight tiles."],
                "status": "unreviewed",
                "sources": [{"kind": "rom", "name": "Original artwork", "address": "base-image",
                             "verification": "recorded"}],
                "evidence": [{"method": "measurement", "verification": "recorded",
                              "measurements": [{"name": "width", "value": 64, "unit": "px"}]}]}

    def store(self, records=None):
        if records is not None:
            self.rows[0].update(notes_schema="3.0.0", notes=records)
        self.path.write_text(graphics_text(self.rows, self.previews), encoding="utf-8")

    def language_notes(self, records, language="xy"):
        write_notes(self.notes_path, {"schema": "3.0.0", "note": records})
        return graphic_notes(self.path, notes_path=self.notes_path, language=language)

    def test_legacy_rows_and_previews_remain_exact(self):
        self.rows[0]["reason"] = "  Existing reason\nwith exact spacing.  "
        self.store()
        self.assertEqual(inventory_rows(self.path), self.rows)
        self.assertEqual(preview_views(self.path), self.previews["views"])
        self.assertEqual(graphic_notes(self.path), {"T1": [], "T2": [], "T3": []})
        before = self.path.read_bytes()
        doc = graphics_document(self.path)
        self.assertEqual(graphics_text(doc["graphic"], doc).encode(), before)
        self.path.write_text(before.decode().replace('version = "2.0.0"', 'version = 1', 1))
        with self.assertRaisesRegex(GraphicsError, "unsupported graphics version; expected 2.0.0"):
            graphics_document(self.path)

    def test_scoped_records_round_trip_without_attribution(self):
        shared = self.note()
        layout = self.note("layout", language="xy")
        layout["related_notes"] = [shared["id"]]
        layout["guidance"] = ["Keep the translated label centered."]
        self.store([shared])
        found = self.language_notes([layout])
        self.assertEqual(found["T1"], [shared, layout])
        self.assertEqual(graphic_notes(self.path)["T1"], [shared])
        doc = graphics_document(self.path)
        self.path.write_text(graphics_text(doc["graphic"], doc), encoding="utf-8")
        self.assertEqual(graphics_document(self.path), doc)
        self.assertEqual(preview_views(self.path), self.previews["views"])
        self.assertNotIn("author", found["T1"][0])
        self.assertNotIn("created", found["T1"][0])
        row = inventory_rows(self.path)[0]
        self.assertTrue(all(isinstance(value, str) for value in row.values()))
        self.assertIn(shared["facts"][0], row["reason"])
        self.assertIn("status: unreviewed", row["reason"])
        self.assertIn("Original artwork", row["reason"])
        self.assertIn("width", row["reason"])
        self.assertNotIn(layout["guidance"][0], row["reason"])

    def test_nonactive_notes_remain_stored(self):
        records = [self.note(status) for status in ("unreviewed", "accepted", "proposed",
                                                   "needs_review", "superseded")]
        for record in records:
            record["status"] = record["id"]
            record["facts"] = [f"Content for {record['id']}."]
        self.store(records)
        self.assertEqual(graphic_notes(self.path)["T1"], records)
        reason = inventory_rows(self.path)[0]["reason"]
        for record in records:
            self.assertEqual(record["facts"][0] in reason, record["status"] in ("unreviewed", "accepted"))

    def test_shared_container_rejects_wrong_scope_and_target(self):
        for record in (self.note(language="xy"), self.note(gid="T2")):
            with self.subTest(record=record), self.assertRaises(GraphicsError):
                self.store([record])
        record = self.note()
        record["binding"]["id"] = "T2"
        with self.assertRaises(GraphicsError):
            self.store([record])

    def test_schema_duplicates_and_legacy_conflicts_are_rejected(self):
        self.store([self.note()])
        valid = deepcopy(self.rows)
        invalid = []
        for key, value in (("notes_schema", "1.0.0"), ("reason", "Independent prose"), ("notes", {})):
            rows = deepcopy(valid)
            rows[0][key] = value
            invalid.append(rows)
        rows = deepcopy(valid)
        del rows[0]["notes_schema"]
        invalid.append(rows)
        invalid.append([*deepcopy(valid), deepcopy(valid[0])])
        rows = deepcopy(valid)
        rows[1].update(notes_schema="3.0.0", notes=[self.note(gid="T2")])
        invalid.append(rows)
        for rows in invalid:
            with self.subTest(rows=rows), self.assertRaises(GraphicsError):
                graphics_text(rows, self.previews)
        self.path.write_text(self.path.read_text().replace('notes_schema = "3.0.0"',
                                                        'notes_schema = "1.0.0"'), encoding="utf-8")
        with self.assertRaises(GraphicsError):
            graphics_document(self.path)

    def test_language_collection_rejects_scope_target_and_identity_conflicts(self):
        self.store([self.note()])
        for record in (self.note("layout"), self.note("layout", language="zz"),
                       self.note("layout", gid="absent", language="xy"), self.note(language="xy")):
            with self.subTest(record=record), self.assertRaises(GraphicsError):
                self.language_notes([record])
        record = self.note("layout", language="xy")
        record["subject"]["kind"] = "term"
        del record["binding"]
        with self.assertRaises(GraphicsError):
            self.language_notes([record])

    def test_language_only_migration_and_optional_collection(self):
        self.store([])
        self.assertEqual(graphic_notes(self.path, notes_path=self.notes_path, language="xy")["T1"], [])
        layout = self.note("layout", language="xy")
        self.assertEqual(self.language_notes([layout])["T1"], [layout])
        with self.assertRaisesRegex(GraphicsError, "selected language"):
            graphic_notes(self.path, notes_path=self.notes_path)
        for reason in ("", "Independent prose"):
            self.rows[0].pop("notes_schema", None)
            self.rows[0].pop("notes", None)
            self.rows[0]["reason"] = reason
            self.store()
            with self.assertRaises(GraphicsError):
                self.language_notes([layout])

    def test_legacy_flag_edits_cannot_overwrite_migrated_notes(self):
        for records in ([], [self.note()]):
            self.store(records)
            before = self.path.read_bytes()
            with self.assertRaisesRegex(ValueError, "edit the structured notes"):
                write_flags(self.path, [{"id": "T2", "category": "keep", "reason": "Updated legacy reason"},
                                        {"id": "T1", "category": "keep", "reason": "Replacement"}])
            self.assertEqual(self.path.read_bytes(), before)
            write_flags(self.path, [{"id": "T2", "category": "keep", "reason": "Updated legacy reason"}])
            self.assertEqual(graphics_document(self.path)["graphic"][0]["notes"], records)

    def test_note_changes_leave_injected_bytes_unchanged(self):
        cfg = config(self.root)
        sheet = cfg.sheet(self.base, cfg.graphic("T1"))
        pixels = deepcopy(sheet.pixels)
        pixels[1][1] = 4
        write_indexed_png(self.root / "label.png", pixels, sheet.palette, 4)
        section = {"inventory": str(self.path), "sheet_width": {"T1": 4},
                   "edit": [{"id": "T1", "png": "label.png", "areas": [[0, 0, 8, 8]]}]}
        outputs, placements = [], []
        for records in (None, [self.note()]):
            self.store(records)
            cfg = GraphicsConfig(section, self.root)
            rom = self.base.copy()
            result = inject_graphics(rom, self.base, cfg, FreeSpace.scan(bytes(self.base.data)))
            self.assertEqual(result.failures, [])
            self.assertEqual(verify_graphics(rom, result), [])
            outputs.append(bytes(rom.data))
            placements.append(result.placements)
        self.assertEqual(outputs[0], outputs[1])
        self.assertNotEqual(outputs[0], bytes(self.base.data))
        self.assertEqual(placements[0], placements[1])
