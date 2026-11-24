import json
import unittest

from gen3tl.web import editorcheck
from gen3tl.check.entrycheck import entry_problems
from gen3tl.lang.langpack import load_styles
from gen3tl.check.layout import Measure
from gen3tl.check.provenance import formatted
from editorcheck_support import EditorCheckTest, PROF

class ShadowCheckTest(unittest.TestCase):
    """The shadow rule in the browser check matches check batch."""

    STYLES = {"unbound": ("font0", 0x1000, 0x9000, 6, "16x16"), "unbound-small": ("small", 0xA000, 0xE000, 5, "8x16")}
    Q = 0xCB

    @classmethod
    def setUpClass(cls):
        from gen3tl.cli.common import _shadow_end
        from gen3tl.build.font import INK, SHADOW, FontTable, encode_glyph
        from gen3tl.core.rom import Rom
        EditorCheckTest.setUpClass.__func__(cls)   # the same document
        data = bytearray(0xE200)
        fonts = {}
        for style, (name, glyphs, widths, w, fmt) in cls.STYLES.items():
            f = fonts[style] = FontTable(name, glyphs, widths, 512, style, fmt)
            for b in range(512):
                px = [[0] * 16 for _ in range(16)]
                for y in range(2, 10):
                    px[y][w - 2], px[y][w - 1] = INK, INK if b == cls.Q else SHADOW
                data[glyphs + b * f.size:glyphs + (b + 1) * f.size] = encode_glyph(px, fmt)
                data[widths + b] = w
        rom = Rom(bytes(data))
        packs = load_styles("ru", PROF)
        slots = {s: packs[s].glyph_slots(rom, f) for s, f in fonts.items()}
        width = {s: (lambda b, s=s: slots[s][b].width if b in slots[s] else fonts[s].width(rom, b)) for s in fonts}
        ends = {s: _shadow_end(slots[s], f, rom) for s, f in fonts.items()}
        # Keep the metrics consistent across checks.
        cls.measure = Measure(width["unbound"], PROF["layout"]["placeholders"], styles=width,
                              shadow_end=ends["unbound"], shadow_styles=ends)
        cls.measures = {s: (Measure(width[s], {}, shadow_end=ends[s]), cls.cm) for s in fonts}
        # browser check: data offsets, image widths and cells
        header = json.loads(json.dumps({k: v for k, v in cls.doc.items() if k != "entries"}))
        for style, (name, glyphs, widths, _, _) in cls.STYLES.items():
            header["fonts"]["styles"][style]["rom"] = {"font": name, "glyphs": glyphs, "widths": widths, "count": 512}
        cls.header = header
        cls.latin = editorcheck.latin_widths(header["fonts"], bytes(data))
        cls.cells = editorcheck.latin_cells(header["fonts"], bytes(data))
        cls.browser = editorcheck.Checker(header, cls.latin, PROF["qa"], "1.2.3", cls.cells)

    def both(self, window: dict, text: str, checker=None) -> list[str]:
        """The browser check's messages as their English text, asserted equal to check batch's."""
        e = {"id": "s0000900", "source_text": "Desert", "window": window}
        r = (checker or self.browser).check(e, text)
        _, probs = entry_problems("Desert", text, editorcheck.limit_of(window), self.measure, self.cm,
                                  set(PROF["qa"]["optional_tokens"]), PROF["qa"]["forbidden"],
                                  window.get("other", []), self.measures)
        msgs = [p["message"] for p in r["problems"]]
        if checker is None:
            self.assertEqual(msgs, probs)
        return [formatted(m) for m in msgs]

    def test_cells(self):
        self.assertEqual({s: len(c) for s, c in self.cells.items()},
                         {"unbound": 512 * 0x40, "unbound-small": 512 * 0x20})
        table = editorcheck.shadow_table(self.header["fonts"]["styles"]["unbound"], self.latin["unbound"],
                                         self.cells["unbound"])
        self.assertEqual((table[self.Q], table[0xD5], table[0x93]), (False, True, True))   # Q, a, а (copies a)
        self.assertFalse(any(editorcheck.shadow_table(self.header["fonts"]["styles"]["unbound"],
                                                      self.latin["unbound"])[b] for b in (0xD5, 0x93)))

    def test_one_pixel_over(self):
        # я is pack art, а a ROM copy
        for text, ok in (("Пустыня", True), ("Вода", True), ("Тест Q", False)):
            w = self.measure.width(text, self.cm)
            with self.subTest(text=text):
                self.assertEqual(self.both({"kind": "single", "width": w, "lines": 1}, text), [])
                self.assertEqual(self.both({"kind": "single", "width": w - 1, "lines": 1}, text) == [], ok)
                self.assertEqual(self.both({"kind": "single", "width": w - 2, "lines": 1}, text),
                                 [f"line too wide: {w} > {w - 2} px: {text!r}"])

    def test_line_flag(self):
        # the lines tell a shadow fit, for the page's bars
        for text, ok in (("Пустыня", True), ("Вода", True), ("Тест Q", False)):
            w = self.measure.width(text, self.cm)
            for px, flag in ((w, False), (w - 1, ok), (w - 2, False)):
                with self.subTest(text=text, px=px):
                    r = self.browser.check({"id": "s0000900", "source_text": "Desert",
                                            "window": {"kind": "single", "width": px, "lines": 1}}, text)
                    self.assertEqual(r["lines"], [[{"text": text, "width": w, "shadow": flag}]])
        keys = self.browser.check({"id": "s0000900", "source_text": "Desert",
                                   "window": {"kind": "single", "width": 10, "lines": 1, "keys": 8}}, "abc")
        self.assertNotIn("shadow", keys["lines"][0][0])

    def test_other_style_window(self):
        for text, ok in (("Пустыня", True), ("Вода", True), ("Тест Q", False)):
            w = self.measures["unbound-small"][0].width(text, self.cm)
            for px, fits in ((w - 1, ok), (w - 2, False)):
                window = {"kind": "field", "width": 200, "lines": 1,
                          "other": [{"font": "unbound-small", "width": px, "where": "box"}]}
                with self.subTest(text=text, px=px):
                    self.assertEqual(self.both(window, text) == [], fits)

    def test_without_cells(self):
        # no ROM glyph ends in shadow; a copied letter fails
        checker = editorcheck.Checker(self.header, self.latin, PROF["qa"], "1.2.3")
        for text, ok in (("Пустыня", True), ("Вода", False)):
            w = self.measure.width(text, self.cm)
            with self.subTest(text=text):
                self.assertEqual(self.both({"kind": "single", "width": w - 1, "lines": 1}, text, checker) == [], ok)
