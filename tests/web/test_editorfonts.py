import copy
import re
import tempfile
import unittest
from pathlib import Path

from test_roots import TOOLKIT_ROOT
from gen3tl.build import spritefont
from gen3tl.check import render
from gen3tl.lang.langpack import LangPack
from gen3tl.web import editorfonts
from project_fixture import rom_evidence

ROOT = TOOLKIT_ROOT

def light_accent() -> str:
    """The shared accent token of the light theme."""
    css = (ROOT / "web" / "site.css").read_text(encoding="utf-8")
    root = re.search(r":root\s*\{([^}]*)\}", css, re.S).group(1)
    return re.search(r"--accent:\s*(#[0-9a-fA-F]{6})\s*;", root).group(1)

class NeutralPackTest(unittest.TestCase):
    """Any pack and profile: one drawn letter, one copied, a sprite font with an advance."""

    PROF = {
        "font_style": {"style": "main", "cell_height": 14},
        "font": [{"name": "f0", "glyphs": 0x100, "widths": 0x8100, "count": 256, "codes": [1]}],
        "sprite_font": [{"name": "hud", "style": "hud", "cell_rows": 8, "sheet": 0x400, "tiles": 3,
                         "pointers": [0x200], "order": "AB ", "aliases": {":": " "}, "fallback": 0,
                         "ink": 1, "shadow": 12, "background": 0, "advance": 5,
                         "lookup": {"guard": rom_evidence(0x100, "13002b331b061b0e192b14d8"),
                                    "code": "014b9b5c5b0105e0"},
                         "evidence": "A routine draws one tile per character."}],
    }

    def write_pack(self, root: Path) -> None:
        (root / "xx" / "fonts").mkdir(parents=True)
        (root / "xx" / "charmap.tsv").write_text("char\tbyte\tsource\nQ\t37\tglyph\nW\t87\tcopy:BB\n",
                                                 encoding="utf-8")
        (root / "xx" / "fonts" / "main.txt").write_text("glyph Q width 3\n" + "_#_\n" * 14, encoding="utf-8")
        (root / "xx" / "fonts" / "hud.txt").write_text("glyph Q width 5\n" + "s###s\n" * 8, encoding="utf-8")

    def test_sheets_of_a_temporary_pack(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_pack(root)
            data = editorfonts.font_sheets(self.PROF, "xx", root)
            self.assertEqual(data["pack"], "xx")
            self.assertEqual(data["styles"]["main"]["art"], {"source": "xx/fonts/main.txt", "path": "fonts/main.txt"})
            font, hud = data["sheets"]
            glyphs = {g["code"]: g for g in font["glyphs"]}
            self.assertEqual(glyphs["37"], {"code": "37", "tile": 0x37, "char": "Q", "source": "added", "width": 3})
            self.assertEqual(glyphs["87"], {"code": "87", "tile": 0x87, "char": "W", "source": "copied", "from": "BB"})
            self.assertEqual(font["codes"], ["01"])
            by_char = {g["char"]: g for g in hud["glyphs"]}
            self.assertEqual(by_char["Q"], {"code": "37", "tile": 3, "char": "Q", "source": "added", "width": 5})
            self.assertEqual(by_char["W"], {"code": "87", "tile": 0, "char": "W", "source": "copied", "from": "BB",
                                            "width": 5})
            self.assertEqual(hud["art"], {"3": ["s###s"] * 8})
            self.assertEqual(hud["count"], 4)
            out = root / "out"
            (out / "fonts").mkdir(parents=True)
            (out / "fonts" / "old.txt").write_text("", encoding="utf-8")
            written = editorfonts.write_art(out, "xx", data, root)
            self.assertEqual(sorted(p.name for p in written), ["hud.txt", "main.txt"])
            self.assertEqual(sorted(p.name for p in (out / "fonts").iterdir()), ["hud.txt", "main.txt"])
            self.assertEqual((out / "fonts" / "main.txt").read_bytes(), (root / "xx/fonts/main.txt").read_bytes())

    def test_a_style_without_art_links_nothing(self):
        prof = copy.deepcopy(self.PROF)
        del prof["sprite_font"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "xx").mkdir()
            (root / "xx" / "charmap.tsv").write_text("char\tbyte\tsource\nA\tBB\tbase\n", encoding="utf-8")
            data = editorfonts.font_sheets(prof, "xx", root)
            self.assertIsNone(data["styles"]["main"]["art"])
            self.assertEqual(editorfonts.write_art(root / "out", "xx", data, root), [])
            self.assertEqual([g["source"] for g in data["sheets"][0]["glyphs"]
                              if g["code"] == "BB"], ["base"])

class SheetTest(unittest.TestCase):
    """The tile sheet of a sprite font: the command's image and the Fonts view's."""

    def test_layout(self):
        self.assertEqual(render.sheet_layout(4), (42, 12, [(2, 2), (12, 2), (22, 2), (32, 2)]))
        w, h, at = render.sheet_layout(161)
        self.assertEqual((w, h, at[16], at[160]), (162, 112, (2, 12), (2, 102)))

    def test_pack_tiles_are_framed(self):
        tiles = bytes([0x10, 0x0C, 0x05, 0]) + bytes(28)   # ink, background, outline, other
        cv = render.sprite_sheet(tiles * 2, 1, ink=1, shadow=12, background=0)
        colours = render.SHEET_COLORS
        self.assertEqual((cv.w, cv.h), (22, 12))
        self.assertEqual(cv.px[2][2:10], [colours[k] for k in ("field", "ink", "shadow", "field", "other", "field",
                                                                "field", "field")])
        self.assertEqual((cv.px[1][1], cv.px[1][11], cv.px[10][20], cv.px[11][21]),
                         (colours["gap"], colours["mark"], colours["mark"], colours["gap"]))
        self.assertEqual(cv.px[2][12:20], cv.px[2][2:10])

    def test_pack_frames_use_the_light_theme_accent(self):
        accent = light_accent()
        self.assertEqual("#%02x%02x%02x" % render.SHEET_COLORS["mark"], accent)


MAIN_Q = ["_#s"] * 13 + ["__s"]

SLOT = ["#s"] * 13 + ["s_"]

HUD_Q = ["s###s"] * 8

class GlyphImportTest(unittest.TestCase):
    """Glyph edits of an export written into the pack's art files."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        fonts = self.root / "xx" / "fonts"
        fonts.mkdir(parents=True)
        (self.root / "xx" / "charmap.tsv").write_text("char\tbyte\tsource\nQ\t37\tglyph\nW\t87\tcopy:BB\n",
                                                      encoding="utf-8")
        self.main = fonts / "main.txt"
        self.main.write_text("# main art\n\nglyph Q width 3\n" + "\n".join(MAIN_Q) + "\n\n# extended\nslot 105 width 2\n"
                             + "\n".join(SLOT) + "\n", encoding="utf-8")
        self.hud = fonts / "hud.txt"
        self.hud.write_text("glyph Q width 5\n" + "\n".join(HUD_Q) + "\n", encoding="utf-8")
        self.main.chmod(0o644)

    def edits(self, records, dry_run=False):
        found = editorfonts.glyph_edits(records, NeutralPackTest.PROF, "xx", self.root)
        return found, editorfonts.apply_glyph_edits(found, dry_run)

    def test_redrawn_glyphs_replace_their_blocks(self):
        q = [r + "_" for r in MAIN_Q]
        q[5] = "##s_"
        slot = list(SLOT)
        slot[2] = "ss"
        hud = ["s#s#s"] * 6 + ["ssss"] * 2
        before = self.main.read_text(encoding="utf-8")
        found, written = self.edits([
            {"style": "main", "char": "Q", "width": 4, "rows": q, "base": {"width": 3, "rows": MAIN_Q}},
            {"style": "main", "slot": "105", "width": 2, "rows": slot, "base": {"width": 2, "rows": SLOT}},
            {"style": "hud", "char": "Q", "width": 4, "rows": [r[:4] for r in hud], "base": {"width": 5, "rows": HUD_Q}}])
        self.assertEqual([g.label for g in found], ["Q", "slot 105", "Q"])
        self.assertEqual(written, [self.main, self.hud])
        self.assertEqual(self.main.read_text(encoding="utf-8"),
                         before.replace("glyph Q width 3\n" + "\n".join(MAIN_Q), "glyph Q width 4\n" + "\n".join(q))
                         .replace("slot 105 width 2\n" + "\n".join(SLOT), "slot 105 width 2\n" + "\n".join(slot)))
        self.assertEqual(self.main.stat().st_mode & 0o777, 0o644)
        pack = LangPack.load("xx", "main", self.root)
        self.assertEqual((pack.glyphs["Q"].width, editorfonts.art_rows(pack.glyphs["Q"], 0, 14)), (4, q))
        hud_pack = LangPack.load("xx", "hud", self.root, rows=8)
        self.assertEqual(editorfonts.art_rows(hud_pack.glyphs["Q"], 0, 8), [r[:4] for r in hud])

    def test_dry_run_and_unchanged_glyphs_write_nothing(self):
        before = self.main.read_bytes()
        q = [r + "_" for r in MAIN_Q]
        found, written = self.edits([{"style": "main", "char": "Q", "width": 4, "rows": q,
                                      "base": {"width": 3, "rows": MAIN_Q}}], dry_run=True)
        self.assertEqual(([g.changed for g in found], written), ([True], []))
        found, written = self.edits([{"style": "main", "char": "Q", "width": 3, "rows": MAIN_Q,
                                      "base": {"width": 3, "rows": MAIN_Q}}])
        self.assertEqual(([g.changed for g in found], written), ([False], []))
        self.assertEqual(self.main.read_bytes(), before)

    def test_a_copied_letter_drawn_anew_becomes_a_drawn_glyph(self):
        w_main = ["_#s"] * 13 + ["___"]
        w_hud = ["s#s"] * 8
        copied = {"source": "copy:BB"}
        main = {"style": "main", "char": "W", "width": 3, "rows": w_main, "base": copied}
        with self.assertRaisesRegex(ValueError, "glyph W: a drawn letter needs art in every style; draw it in hud too"):
            self.edits([main])
        charmap = self.root / "xx" / "charmap.tsv"
        texts = {p: p.read_bytes() for p in (self.main, self.hud, charmap)}
        found, written = self.edits([main, {"style": "hud", "char": "W", "width": 3, "rows": w_hud, "base": copied}],
                                    dry_run=True)
        self.assertEqual([(g.label, g.code, g.copy, g.new, g.changed) for g in found],
                         [("W", 0x87, 0xBB, True, True), ("W", 0x87, None, True, True)])
        self.assertEqual((written, {p: p.read_bytes() for p in texts}), ([], texts))
        found, written = self.edits([main, {"style": "hud", "char": "W", "width": 3, "rows": w_hud, "base": copied}])
        self.assertEqual(written, [self.main, self.hud, charmap])
        # after Q, the letter before it in the charmap
        self.assertEqual(self.main.read_text(encoding="utf-8"), texts[self.main].decode().replace(
            "\n\n# extended", "\n\nglyph W width 3\n" + "\n".join(w_main) + "\n\n# extended"))
        self.assertEqual(charmap.read_text(encoding="utf-8"), texts[charmap].decode().replace("copy:BB", "glyph"))
        pack = LangPack.load("xx", "main", self.root)
        self.assertEqual((pack.sources["W"], editorfonts.art_rows(pack.glyphs["W"], 0, 14)), ("glyph", w_main))
        self.assertEqual(editorfonts.art_rows(LangPack.load("xx", "hud", self.root, rows=8).glyphs["W"], 0, 8), w_hud)
        with self.assertRaisesRegex(ValueError, "art changed after the edit"):   # no longer a copy
            self.edits([main])

    def test_refused_edits_name_the_reason(self):
        base = {"width": 3, "rows": MAIN_Q}
        good = {"style": "main", "char": "Q", "width": 3, "rows": MAIN_Q, "base": base}
        last_ink = MAIN_Q[:13] + ["_#s"]
        cases = [
            ({**good, "base": {"width": 3, "rows": ["___"] * 14}}, "art changed after the edit"),
            ({**good, "style": "nope"}, "draws no font in this style"),
            ({**good, "char": "W"}, "draws no such glyph"),
            ({**good, "width": 17, "rows": [r + "_" * 14 for r in MAIN_Q]}, "width must be 1..16"),
            ({**good, "rows": MAIN_Q[:13]}, "14 rows of 3 pixels"),
            ({**good, "rows": ["_#x"] + MAIN_Q[1:]}, "each _, # or s"),
            ({**good, "rows": last_ink}, "ink on the last cell row"),
            ({**good, "slot": "105"}, "char or slot"),
            ({**good, "slot": None, "char": None}, "char or slot"),
            ({**good, "char": "W", "base": {"source": "copy:BC"}}, "art changed after the edit"),   # another source
            ({**good, "base": {"source": "copy:BB"}}, "art changed after the edit"),   # Q is drawn
            ({**good, "char": "W", "base": {"source": "glyph"}}, "names its source as copy:<byte>"),
            ({**good, "char": "W", "base": {"source": "copy:BB", "width": 3}}, "names its source as copy:<byte>"),
            ({"style": "hud", "char": "Q", "width": 9, "rows": [r + "____" for r in HUD_Q],
              "base": {"width": 5, "rows": HUD_Q}}, "width must be 1..8"),
        ]
        for record, reason in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                self.edits([record])
        with self.assertRaisesRegex(ValueError, "edited twice"):
            self.edits([good, good])
        with self.assertRaisesRegex(ValueError, "must be a list"):
            self.edits({"style": "main"})
        found, _ = self.edits([{"style": "hud", "char": "Q", "width": 5, "rows": HUD_Q[:7] + ["s###s"],
                                "base": {"width": 5, "rows": HUD_Q}}])
        self.assertFalse(found[0].changed)   # the outlined style keeps no shadow rules

    def test_sprite_copy_creates_art_without_changing_main_font_sources(self):
        charmap = self.root / "xx" / "charmap.tsv"
        before = charmap.read_bytes(), self.main.read_bytes()
        record = {"style": "hud", "char": "W", "width": 3, "rows": ["s#s"] * 8,
                  "base": {"source": "copy:BB"}}
        found, written = self.edits([record])
        self.assertEqual(written, [self.hud])
        self.assertIsNone(found[0].copy)
        self.assertEqual((charmap.read_bytes(), self.main.read_bytes()), before)
        pack = LangPack.load("xx", "hud", self.root, rows=8)
        self.assertEqual(editorfonts.art_rows(pack.glyphs["W"], 0, 8), record["rows"])
        font = spritefont.from_profile(NeutralPackTest.PROF)[0]
        planned = spritefont.plan(font, editorfonts.BlankBase(), pack)
        self.assertEqual((planned.table[0x87], planned.added["W"], planned.copied), (4, 4, {}))
        with self.assertRaisesRegex(ValueError, "art changed after the edit"):
            self.edits([record])

    def test_new_slot_creates_a_missing_style_file(self):
        self.main.unlink()
        self.hud.unlink()
        self.main.parent.rmdir()
        (self.root / "xx" / "charmap.tsv").write_text("char\tbyte\tsource\nA\tBB\tbase\n", encoding="utf-8")
        record = {"style": "main", "slot": "034", "width": 3, "rows": MAIN_Q, "base": {"source": "rom"}}
        _, written = self.edits([record], dry_run=True)
        self.assertEqual(written, [])
        self.assertFalse(self.main.parent.exists())
        _, written = self.edits([record])
        self.assertEqual(written, [self.main])
        self.assertEqual(self.main.read_text(encoding="utf-8"), "slot 034 width 3\n" + "\n".join(MAIN_Q) + "\n")
        self.assertEqual(self.main.stat().st_mode & 0o777, 0o644)
        pack = LangPack.load("xx", "main", self.root)
        self.assertEqual(editorfonts.art_rows(pack.glyphs[0x34], 0, 14), MAIN_Q)

    def test_new_slot_refuses_existing_art_invalid_indices_and_sprite_styles(self):
        record = {"style": "main", "slot": "034", "width": 3, "rows": MAIN_Q, "base": {"source": "rom"}}
        for change, reason in [({"slot": "BB"}, "original letter declared as base"), ({"slot": "200"}, "named base glyph"),
                               ({"slot": "-1"}, "named base glyph"), ({"slot": "100"}, "outside the profile"),
                               ({"style": "hud"}, "main font"), ({"base": {"source": "rom", "rows": []}}, "ROM slot")]:
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, reason):
                self.edits([{**record, **change}])
        self.edits([record])
        with self.assertRaisesRegex(ValueError, "art changed after the edit"):
            self.edits([record])

    def test_base_letter_slot_import_keeps_mapping_and_rejects_stale_art(self):
        charmap = self.root / "xx" / "charmap.tsv"
        charmap.write_text(charmap.read_text() + "A\tBB\tbase\n")
        before = charmap.read_bytes(), self.hud.read_bytes()
        record = {"style": "main", "slot": "0BB", "width": 3, "rows": MAIN_Q, "base": {"source": "rom"}}
        _, written = self.edits([record])
        self.assertEqual(written, [self.main])
        self.assertEqual((charmap.read_bytes(), self.hud.read_bytes()), before)
        pack = LangPack.load("xx", "main", self.root)
        self.assertEqual(pack.sources["A"], "base")
        self.assertEqual(editorfonts.art_rows(pack.glyphs[0xBB], 0, 14), MAIN_Q)
        for stale in (record, {**record, "base": {"width": 3, "rows": ["___"] * 14}}):
            with self.subTest(stale=stale), self.assertRaisesRegex(ValueError, "art changed after the edit"):
                self.edits([stale])
        current = {**record, "rows": [r + "_" for r in MAIN_Q], "width": 4,
                   "base": {"width": 3, "rows": MAIN_Q}}
        self.edits([current])
        charmap.write_bytes(before[0].replace(b"A\tBB\tbase\n", b""))
        with self.assertRaisesRegex(ValueError, "original letter declared as base"):
            self.edits([current])
        charmap.write_bytes(before[0])
        for slot in ("00", "A1", "37", "F8", "F9", "FC", "FE", "FF"):
            with self.subTest(slot=slot), self.assertRaisesRegex(ValueError, "ROM slot"):
                self.edits([{**record, "slot": slot}])
        with self.assertRaisesRegex(ValueError, "main font"):
            self.edits([{**record, "style": "hud"}])
        with self.assertRaisesRegex(ValueError, "requires explicit slot art"):
            self.edits([{k: v for k, v in {**record, "char": "A"}.items() if k != "slot"}])
        self.hud.write_text(self.hud.read_text() + "\nslot 0BB width 3\n" + "_#s\n" * 8)
        hud = LangPack.load("xx", "hud", self.root, rows=8)
        with self.assertRaisesRegex(spritefont.SpriteFontError, "require a main font"):
            spritefont.plan(spritefont.from_profile(NeutralPackTest.PROF)[0], editorfonts.BlankBase(), hud)

class AdvanceTest(unittest.TestCase):
    def test_advance_is_optional_and_bounded(self):
        entry = copy.deepcopy(NeutralPackTest.PROF["sprite_font"][0])
        self.assertEqual(spritefont.parse(entry).advance, 5)
        del entry["advance"]
        self.assertEqual(spritefont.parse(entry).advance, 8)   # one tile per character
        for bad in (0, 9, "4"):
            with self.subTest(bad=bad), self.assertRaises(spritefont.SpriteFontError):
                spritefont.parse({**entry, "advance": bad})
        with self.assertRaises(spritefont.SpriteFontError):
            spritefont.parse({**entry, "kerning": 1})
