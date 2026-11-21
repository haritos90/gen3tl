import base64
import copy
import json
import tempfile
import unittest
from pathlib import Path

from graphics_support import fixture
from gen3tl.web.editorgfx import Pictures, graphics_data
from gen3tl.build.graphiclabel import image
from gen3tl.build.graphics import GraphicsConfig, GraphicsError, inject_graphics, verify_graphics
from gen3tl.build.inject import FreeSpace


class GraphicLabelTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base, _, _, _ = fixture(self.root)
        (self.root / "translation").mkdir()
        self.release = self.root / "translation/release.toml"
        self.release.write_text('version = "0.1.0"\n')
        glyph = {"width": 3, "height": 3, "rows": ["###", "#.#", "###"], "colours": {"#": 2, ".": 0}}
        self.spec = {"schema": "1.0.0", "text": "@VERSION@", "width": 64, "height": 8,
                     "start_tile": 16, "background": 0, "gap": 1, "top": 1, "align": "right",
                     "glyphs": {c: copy.deepcopy(glyph) for c in "0123456789."}}
        self.spec["glyphs"]["1"] = {"width": 3, "height": 3, "parts": [[1, 1, 3, 3, 0, 0]]}
        facts = {k: self.spec.pop(k) for k in ("width", "height", "start_tile", "background")}
        facts["glyphs"] = {"1": self.spec["glyphs"].pop("1")}
        (self.root / "label.json").write_text(json.dumps(self.spec))
        self.section = {"inventory": "inv.tsv", "sheet_width": {"T1": 4}, "label_facts": {"T1": facts}, "edit": [
            {"id": "T1", "label": "label.json", "form": "areas", "areas": [], "tiles": 24,
             "evidence": "Synthetic appended tiles"}]}

    def test_version_changes_build_and_rom_only_editor_identically(self):
        results = []
        for version in ("0.1.0", "1.0.1"):
            self.release.write_text(f'version = "{version}"\n')
            cfg = GraphicsConfig(self.section, self.root)
            built = self.base.copy()
            result = inject_graphics(built, self.base, cfg, FreeSpace.scan(self.base.data))
            self.assertEqual(result.failures, [])
            self.assertEqual(verify_graphics(built, result), [])
            data = graphics_data(self.section, self.root)
            self.assertNotIn("png", data["edits"]["T1"])
            self.assertEqual(data["edits"]["T1"]["label"]["text"], version)
            pictures = Pictures(data, self.base.data)
            edited = pictures.edited("T1")
            self.assertEqual(edited["findings"], [])
            raw = base64.b64decode(edited["pixels"])
            sheet = cfg.sheet(self.base, cfg.graphic("T1"))
            rows = [list(raw[i:i + sheet.width]) for i in range(0, len(raw), sheet.width)]
            self.assertEqual(sheet.encode(rows, 24), result.placements[0].data)
            self.assertEqual(rows[:sheet.height], sheet.pixels)
            results.append(built.data)
        self.assertNotEqual(*results)

    def test_invalid_labels_fail_before_injection(self):
        cfg = GraphicsConfig(self.section, self.root)
        sheet = cfg.sheet(self.base, cfg.graphic("T1"))
        original = cfg.edits[0].label
        for change in ({"text": "8" * 100}, {"text": "X"}, {"text": "@VERSION@"},
                       {"width": 63}, {"start_tile": 15}, {"top": 8}, {"background": 16}):
            with self.subTest(change=change), self.assertRaises(GraphicsError):
                image(sheet, dict(original, **change), 24)
        malformed = copy.deepcopy(original)
        malformed["glyphs"]["1"]["parts"][0][0] = sheet.width
        with self.assertRaisesRegex(GraphicsError, "bounds"):
            image(sheet, malformed, 24)

    def test_asset_source_and_version_are_mandatory(self):
        section = copy.deepcopy(self.section)
        section["edit"][0]["png"] = "also.png"
        with self.assertRaisesRegex(GraphicsError, "one image source"):
            GraphicsConfig(section, self.root)
        self.release.write_text('version = "unfinished"\n')
        with self.assertRaises(ValueError):
            GraphicsConfig(self.section, self.root)

    def test_label_assets_cannot_override_source_geometry(self):
        for change in ({"width": 88}, {"glyphs": {"1": {"parts": [[0, 0, 1, 1, 0, 0]]}}}):
            (self.root / "label.json").write_text(json.dumps(dict(self.spec, **change)))
            with self.subTest(change=change), self.assertRaises(GraphicsError):
                GraphicsConfig(self.section, self.root)

    def test_malformed_containers_report_graphics_errors(self):
        cfg = GraphicsConfig(self.section, self.root)
        sheet = cfg.sheet(self.base, cfg.graphic("T1"))
        spec = cfg.edits[0].label
        for asset in ([], None, {**self.spec, "glyphs": []}):
            (self.root / "label.json").write_text(json.dumps(asset))
            with self.subTest(asset=asset), self.assertRaises(GraphicsError):
                GraphicsConfig(self.section, self.root)
        for field in ("parts", "rows", "colours"):
            changed = copy.deepcopy(spec)
            changed["glyphs"]["1" if field == "parts" else "0"][field] = None
            with self.subTest(field=field), self.assertRaises(GraphicsError):
                image(sheet, changed, 24)
        for invalid in (None, {**spec, "glyphs": []}, {**spec, "glyphs": {"0": None, "1": None, ".": None}}):
            with self.subTest(invalid=invalid), self.assertRaises(GraphicsError):
                image(sheet, invalid, 24)


if __name__ == "__main__":
    unittest.main()
