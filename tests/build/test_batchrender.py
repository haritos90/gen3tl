import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_roots import TOOLKIT_ROOT

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.cli import check as cli
from gen3tl.core.charmap import Charmap
from gen3tl.build.font import INK, encode_glyph
from gen3tl.build.graphics import read_png_image
from gen3tl.catalog.po import PoEntry, write
from gen3tl.profile.profile import Profile
from gen3tl.check.render import PALETTE, draw_text


class BatchRenderTest(unittest.TestCase):
    def render_batch(self, english, translated):
        with tempfile.TemporaryDirectory() as tmp, contextlib.chdir(tmp):
            root = Path(tmp)
            glyph = encode_glyph([[INK if x < 6 else 0 for x in range(16)] for _ in range(16)])
            rom = glyph * 512 + bytes([6] * 512)
            rom_path = root / "base.gba"
            rom_path.write_bytes(rom)
            prof = Profile({
                "rom": {"sha1": hashlib.sha1(rom).hexdigest()},
                "font": [{"name": "main", "glyphs": 0, "widths": 0x8000, "count": 512}],
                "font_style": {"style": "unbound"},
                "layout": {"dialogue_width": 208, "placeholders": {"STR_VAR_1": 60, "STR_VAR_2": 30}},
            }, root / "test.toml")
            current = translated["translation"] if isinstance(translated, dict) else translated
            entries = [PoEntry("s0000100", english, current), PoEntry("s0000200", "XY", "Ляляля")]
            write(Path("translation/po/map.po"), entries, {})
            Path("translation/release.toml").write_text('version = "0.1.0"\n')
            inventory = {"entries": [
                {"id": e.msgctxt, "text": e.msgid, "offset": int(e.msgctxt[1:], 16),
                 "length": 40, "kind": "pointer", "category": "script", "mode": "field", "flags": [],
                 "refs": [{"at": 0x50, "kind": "script:loadpointer"}]} for e in entries
            ]}
            Path("build").mkdir()
            Path("build/inventory.json").write_text(json.dumps(inventory))
            ctx = {"strings": {"s0000100": {"slots": {
                "{STR_VAR_1}": {"fills": [["string", "s0000200"]], "uses": 1, "unfilled": 0},
            }}}}
            Path("build/context.json").write_text(json.dumps(ctx))
            batch = Path("translation/batches/probe")
            batch.mkdir(parents=True)
            (batch / "source.json").write_text(json.dumps([{"id": "s0000100", "source_text": english}]))
            (batch / "translation.json").write_text(json.dumps({"s0000100": translated}))
            project = SimpleNamespace(batches=Path("translation/batches"), glossary=Path("translation/glossary.tsv"),
                                      build=Path("build"))
            args = SimpleNamespace(profile="test", rom=str(rom_path), lang="ru", po_root="translation/po",
                                   name="probe", ids=[], limit=1, scale=1, project=project)
            output = io.StringIO()
            with patch.object(Profile, "by_name", return_value=prof), contextlib.redirect_stdout(output):
                with patch("gen3tl.check.render.draw_text", wraps=draw_text) as drawing:
                    cli.batch_render(args)
            image = read_png_image(Path("build/screens/probe-boxes.png")).rgb
            return image, drawing.call_args_list[1:], output.getvalue()

    def test_english_uses_source_width_and_profile_fallback(self):
        image, (source_draw, translation_draw), _ = self.render_batch(
            "A{STR_VAR_1}B{STR_VAR_2}Z", "А{STR_VAR_1}Б{STR_VAR_2}В")
        cm = Charmap()
        first, second = (cm.encode(t, terminate=False) for t in ("{STR_VAR_1}", "{STR_VAR_2}"))
        self.assertEqual(source_draw.kwargs["slots"], {first: 12, second: 30})
        self.assertNotEqual(translation_draw.kwargs["slots"][first], 12)
        self.assertEqual(translation_draw.kwargs["slots"][second], 30)
        self.assertEqual(image[20][71], PALETTE[INK])
        self.assertEqual(image[20][72], PALETTE["paper"])

    def test_overflow_is_marked_and_cannot_contaminate_other_column(self):
        source = "A" * 100 + r"\nZ"
        short, _, _ = self.render_batch(r"A\nZ", r"А\nБ")
        overflow, calls, output = self.render_batch(source, r"А\nБ")
        self.assertEqual([row[240:] for row in short], [row[240:] for row in overflow])
        self.assertEqual(calls[0].args[4], Charmap().encode(source))
        self.assertIn("language     English\noverflow     384 px\nwidth        600 px\nlimit        216 px", output)
        self.assertEqual(overflow[20][228], PALETTE["overflow"])
        self.assertEqual(overflow[36][228], PALETTE["frame"])
        self.assertTrue(all(px == PALETTE["paper"] for row in overflow for px in row[229:240]))

        both, _, output = self.render_batch(source, "А" * 100)
        self.assertIn("language     ru\noverflow     384 px", output)
        self.assertEqual(both[20][464], PALETTE["overflow"])
        self.assertTrue(all(px == PALETTE["paper"] for row in both for px in row[465:]))

    def test_caption_of_an_id_the_font_cannot_spell(self):
        """An id with '_' is drawn with '-', not refused."""
        cm = Charmap()
        self.assertFalse(cm.can_encode("_"))
        self.assertEqual(cli._caption("types_short:0", cm), cm.encode("types-short:0"))
        self.assertEqual(cli._caption("s0000100", cm), cm.encode("s0000100"))

    def test_explicit_states_render_original_and_off_separately(self):
        for state, expected in (("original", "ABC"), ("empty", "")):
            with self.subTest(state=state):
                explicit, _, _ = self.render_batch("ABC", {"translation": "", "state": state})
                legacy, _, _ = self.render_batch("ABC", expected)
                self.assertEqual(explicit, legacy)


if __name__ == "__main__":
    unittest.main()
