import unittest
from pathlib import Path

from test_roots import TOOLKIT_ROOT
from gen3tl.build.font import decode_glyph, encode_glyph
from gen3tl.lang.langpack import LangPack, LangPackError, format_art, insert_art, parse_art, replace_art, set_source, style_problems

class LangPackTest(unittest.TestCase):
    def setUp(self):
        self.pack = LangPack.load("ru", "unbound")

    def test_full_alphabet_encodes(self):
        cm = self.pack.charmap()
        alphabet = "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя"
        self.assertEqual(len(cm.encode(alphabet, terminate=False)), len(alphabet))

    def test_slots_unique_and_art_complete(self):
        glyph_bytes = [b for c, b in self.pack.chars.items() if self.pack.sources[c] == "glyph"]
        self.assertEqual(len(glyph_bytes), len(set(glyph_bytes)))
        extended = {key for key in self.pack.glyphs if isinstance(key, int)}
        self.assertEqual(set(self.pack.glyph_slots()), set(glyph_bytes) | extended)

    def test_style(self):
        for ch, g in self.pack.glyphs.items():
            self.assertEqual(style_problems(g), [], ch)

    def test_art_roundtrip(self):
        for g in self.pack.glyphs.values():
            again = parse_art(format_art(g))[g.char]
            self.assertEqual(again.pixels, g.pixels)
            self.assertEqual(decode_glyph(encode_glyph(g.pixels)), g.pixels)

class ReplaceArtTest(unittest.TestCase):
    TEXT = ("# a comment\n\nglyph Q width 2\n#s\ns_\n\n# named\nslot 034 width 3\n#s_\ns__\n"
            "glyph W width 1\n#\ns\n")

    def test_one_block_redrawn_the_rest_kept(self):
        text = replace_art(self.TEXT, "Q", 3, ["##s", "ss_"])
        self.assertEqual(text, self.TEXT.replace("glyph Q width 2\n#s\ns_", "glyph Q width 3\n##s\nss_"))
        again = replace_art(text, 0x34, 1, ["#", "s"])
        self.assertIn("\n# named\nslot 034 width 1\n#\ns\nglyph W", again)
        art = parse_art(again, 2)
        self.assertEqual([art["Q"].width, art[0x34].width, art["W"].width], [3, 1, 1])

    def test_a_glyph_without_art_fails(self):
        for key in ("Z", 0x105):
            with self.subTest(key=key), self.assertRaises(LangPackError):
                replace_art(self.TEXT, key, 1, ["#", "s"])

    def test_a_new_block_takes_its_charmap_place(self):
        before = insert_art(self.TEXT, "P", 1, ["#", "s"], later=("Q", "W"))
        self.assertEqual(before, self.TEXT.replace("glyph Q width 2", "glyph P width 1\n#\ns\n\nglyph Q width 2"))
        after = insert_art(self.TEXT, "X", 1, ["#", "s"], later=("Y",))   # after the last glyph block
        self.assertEqual(after, self.TEXT + "\nglyph X width 1\n#\ns\n")
        self.assertEqual(insert_art("# only a comment\n", "X", 1, ["#", "s"]), "# only a comment\n\nglyph X width 1\n#\ns\n")
        self.assertEqual([parse_art(t, 2)[k].width for t, k in ((before, "P"), (after, "X"))], [1, 1])
        self.assertEqual(list(parse_art(before, 2)), ["P", "Q", 0x34, "W"])

    def test_a_charmap_source_changes_alone(self):
        text = "# map\nchar\tbyte\tsource\nА\t87\tcopy:BB\nБ\t37\tglyph\n"
        self.assertEqual(set_source(text, "А", "glyph"), text.replace("copy:BB", "glyph"))
        with self.assertRaises(LangPackError):
            set_source(text, "Я", "glyph")

class ExtendedGlyphTest(unittest.TestCase):
    def test_slot_art_stays_outside_the_character_map(self):
        import tempfile

        from gen3tl.core.charmap import CharmapError
        from gen3tl.web.editordata import fonts_data, glyph_art
        from gen3tl.build.font import FontTable, write_glyphs
        from gen3tl.core.rom import Rom
        text = "slot 105 width 2\n" + "__\n" * 14
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "xx" / "fonts").mkdir(parents=True)
            (root / "xx" / "charmap.tsv").write_text("char\tbyte\tsource\n")
            (root / "xx" / "fonts" / "test.txt").write_text(text)
            pack = LangPack.load("xx", "test", root)
        self.assertEqual(pack.chars, {})
        self.assertEqual(set(pack.glyph_slots()), {0x105})
        self.assertEqual(pack.charmap().encode("{F9 05}"), b"\xF9\x05\xFF")
        with self.assertRaises(CharmapError):
            pack.charmap().encode("Б")
        glyph = pack.glyph_slots()[0x105]
        self.assertEqual(format_art(glyph), text)
        font = FontTable("f", 0, 0x8000, 512, "test")
        rom = Rom(bytes(0x8200))
        changed = write_glyphs(rom, [font], {"test": pack.glyph_slots()})
        self.assertEqual(changed, [(0x105 * 64, 64), (0x8105, 1)])
        self.assertEqual(font.width(rom, 0x105), 2)
        self.assertEqual(font.glyph(rom, 0x105), glyph.pixels)
        with self.assertRaises(ValueError):
            write_glyphs(rom, [FontTable("f", 0, 0x8000, 256, "test")], {"test": pack.glyph_slots()})
        prof = {"font_style": {"style": "test", "cell_height": 14},
                "font": [{"name": "f", "glyphs": 0, "widths": 0x8000, "count": 512}]}
        self.assertEqual(fonts_data(prof, {"test": pack})["styles"]["test"]["pack"], {"105": 2})
        self.assertIn("105", glyph_art(prof, {"test": pack})["test"]["art"])

    def test_invalid_and_duplicate_slots_fail(self):
        rows = "__\n" * 14
        for key in ("FF", "200", "nope"):
            with self.subTest(key), self.assertRaises(ValueError):
                parse_art(f"slot {key} width 2\n" + rows)
        text = "slot 105 width 2\n" + rows
        with self.assertRaises(ValueError):
            parse_art(text + text)

    def test_named_base_slot_preserves_encoding_and_editor_art(self):
        import tempfile

        from gen3tl.web.editordata import fonts_data, glyph_art
        from gen3tl.build.font import FontTable, write_glyphs
        from gen3tl.core.rom import Rom
        text = "slot 034 width 2\n" + "__\n" * 14
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "xx" / "fonts").mkdir(parents=True)
            (root / "xx" / "charmap.tsv").write_text("char\tbyte\tsource\n")
            (root / "xx" / "fonts" / "test.txt").write_text(text)
            pack = LangPack.load("xx", "test", root)
        self.assertEqual(pack.chars, {})
        self.assertEqual(set(pack.glyph_slots()), {0x34})
        self.assertEqual(pack.charmap().encode("{LV}{F9 05}{F8 00}"), b"\x34\xF9\x05\xF8\x00\xFF")
        glyph = pack.glyph_slots()[0x34]
        self.assertEqual(format_art(glyph), text)
        font = FontTable("f", 0, 0x8000, 512, "test")
        rom = Rom(bytes(0x8200))
        self.assertEqual(write_glyphs(rom, [font], {"test": pack.glyph_slots()}),
                         [(0x34 * 64, 64), (0x8034, 1)])
        self.assertEqual(font.glyph(rom, 0x34), glyph.pixels)
        self.assertEqual(font.width(rom, 0x34), 2)
        prof = {"font_style": {"style": "test", "cell_height": 14},
                "font": [{"name": "f", "glyphs": 0, "widths": 0x8000, "count": 512}]}
        self.assertEqual(fonts_data(prof, {"test": pack})["styles"]["test"]["pack"], {"34": 2})
        self.assertIn("34", glyph_art(prof, {"test": pack})["test"]["art"])

    def test_unnamed_nonletters_and_controls_are_not_slot_overrides(self):
        for key in ("00", "37", "A1", "F8", "F9", "FC", "FE", "FF"):
            with self.subTest(key), self.assertRaises(ValueError):
                parse_art(f"slot {key} width 2\n" + "__\n" * 14)

    def test_base_letter_art_requires_an_identity_mapping_and_preserves_text(self):
        import tempfile

        from gen3tl.build.font import FontTable, write_glyphs
        from gen3tl.build.inject import BuildResult, verify
        from gen3tl.core.charmap import Charmap
        from gen3tl.core.rom import Rom
        art = "slot 0BB width 2\n" + "#s\n" * 13 + "s_\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / "xx"
            (directory / "fonts").mkdir(parents=True)
            (directory / "fonts/test.txt").write_text(art)
            charmap = directory / "charmap.tsv"
            for mapping in ("", "B\tBC\tbase\n", "A\t37\tglyph\n"):
                charmap.write_text("char\tbyte\tsource\n" + mapping)
                with self.subTest(mapping=mapping), self.assertRaisesRegex(LangPackError, "original letter declared as base"):
                    LangPack.load("xx", "test", root)
            for mapping in ("A\tBC\tbase\n", "A\tBB\tglyph\n"):
                charmap.write_text("char\tbyte\tsource\n" + mapping)
                with self.subTest(mapping=mapping), self.assertRaises(LangPackError):
                    LangPack.load("xx", "test", root)
            charmap.write_text("char\tbyte\tsource\nA\tBB\tbase\n")
            pack = LangPack.load("xx", "test", root)
            other = LangPack.load("xx", "other", root)
            charmap.write_text(charmap.read_text() + "А\t37\tcopy:BB\n")
            copied = LangPack.load("xx", "test", root)
            (directory / "fonts/test.txt").write_text(art.replace("slot 0BB", "glyph A"))
            with self.assertRaisesRegex(LangPackError, "requires explicit slot art"):
                LangPack.load("xx", "test", root)
        self.assertEqual(pack.sources, {"A": "base"})
        self.assertEqual(pack.allocated_slots(), set())
        self.assertEqual(pack.glyph_slots().keys(), {0xBB})
        self.assertEqual(other.glyph_slots(), {})
        self.assertEqual(pack.charmap().encode("ABC"), Charmap().encode("ABC"))
        font = FontTable("f", 0x100, 0x8100, 256, "test")
        base = Rom(bytes(0x8300))
        base.write(0x40, pack.charmap().encode("ABC"))
        original = [[0] * 16 for _ in range(16)]
        original[0][0] = 1
        base.write(font.glyphs + 0xBB * font.size, encode_glyph(original))
        base.write(font.widths + 0xBB, b"\x03")
        rom = base.copy()
        self.assertEqual(copied.copy_slots(), {0x37: 0xBB})
        spans = write_glyphs(rom, [font], {"test": copied.glyph_slots()}, copied.copy_slots())
        allowed = {at for offset, size in spans for at in range(offset, offset + size)}
        self.assertEqual(font.glyph(rom, 0xBB), pack.glyphs[0xBB].pixels)
        self.assertEqual(font.width(rom, 0xBB), 2)
        self.assertEqual(font.glyph_bytes(rom, 0x37), font.glyph_bytes(base, 0xBB))
        self.assertEqual(font.width(rom, 0x37), 3)
        self.assertEqual(rom.read(0x40, 4), base.read(0x40, 4))
        self.assertEqual(verify(rom, base, BuildResult(), {}, allowed), [])
        rom.write(0x44, b"\x01")
        self.assertTrue(any("outside" in failure for failure in verify(rom, base, BuildResult(), {}, allowed)))

    def test_font_build_requires_blank_characters_but_allows_slot_overrides(self):
        import contextlib
        import io
        import tempfile
        from types import SimpleNamespace
        from unittest.mock import patch

        from gen3tl.cli.build import font_build
        from gen3tl.lang.langpack import Glyph
        from gen3tl.core.rom import Rom
        rom = Rom(bytes(0x8200))
        px = [[0] * 16 for _ in range(16)]
        px[4][0] = 1
        rom.write(0x105 * 64, encode_glyph(px))
        rom.write(0x34 * 64, encode_glyph(px))
        rom.write(0xBB * 64, encode_glyph(px))
        rom.write(0x80BB, b"\x02")
        pack = LangPack("xx", chars={"Б": 0x37, "A": 0xBB}, sources={"Б": "glyph", "A": "base"},
                        glyphs={"Б": Glyph("Б", 2, px), 0x105: Glyph(0x105, 2, px),
                                0x34: Glyph(0x34, 2, px), 0xBB: Glyph(0xBB, 2, px)})

        class ProfileFixture(dict):
            def load_rom(self, path):
                return rom

        prof = ProfileFixture(font_style={"style": "test"},
                              font=[{"name": "f", "glyphs": 0, "widths": 0x8000, "count": 512}])
        args = SimpleNamespace(profile="p", rom=None, lang="xx")
        with tempfile.TemporaryDirectory() as tmp, contextlib.chdir(tmp), contextlib.redirect_stdout(io.StringIO()), \
                patch("gen3tl.cli.build.Profile.by_name", return_value=prof), \
                patch("gen3tl.lang.langpack.LangPack.load", return_value=pack), \
                patch("gen3tl.lang.langpack.load_styles", return_value={"test": pack}):
            args.project = SimpleNamespace(build=Path(tmp) / "build")
            self.assertEqual(font_build(args), 0)
            rom.write(0x37 * 64, encode_glyph(px))
            self.assertEqual(font_build(args), 1)

ROOT = TOOLKIT_ROOT

ALPHABET = "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя"

LOOK_ALIKES = {"А": "A", "В": "B", "Е": "E", "Ё": "Ë", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P",
               "С": "C", "Т": "T", "Х": "X", "а": "a", "е": "e", "ё": "ë", "о": "o", "р": "p", "с": "c",
               "у": "y", "х": "x"}

class OwnByteTest(unittest.TestCase):
    """Every Russian letter has its own byte; look-alikes copy a ROM glyph."""

    def setUp(self):
        self.pack = LangPack.load("ru")

    def test_every_letter_has_its_own_byte(self):
        from gen3tl.core.charmap import BASE_CHARS, NAMED_BYTES
        self.assertEqual(set(self.pack.chars), set(ALPHABET))
        own = list(self.pack.chars.values())
        self.assertEqual(len(own), len(set(own)))
        self.assertFalse(set(own) & (set(BASE_CHARS) | set(NAMED_BYTES)))
        self.assertTrue(all(b < 0xF7 for b in own))

    def test_look_alikes_copy_their_latin_glyph(self):
        from gen3tl.core.charmap import BASE_CHARS
        self.assertEqual({c: BASE_CHARS[s] for c, s in self.pack.copies.items()}, LOOK_ALIKES)
        self.assertEqual({c for c, s in self.pack.sources.items() if s == "copy"}, set(LOOK_ALIKES))
        self.assertEqual(self.pack.copy_slots(), {self.pack.chars[c]: s for c, s in self.pack.copies.items()})
        # art for the other letters only: no ROM art
        for style in ("unbound", "unbound-small"):
            text = (ROOT / "assets" / "lang" / "ru" / "fonts" / f"{style}.txt").read_text(encoding="utf-8")
            drawn = set(parse_art(text, 12, 1) if style == "unbound-small" else parse_art(text))
            self.assertEqual({key for key in drawn if isinstance(key, str)}, set(ALPHABET) - set(LOOK_ALIKES), style)

    def test_slots_in_one_block(self):
        self.assertEqual(sorted(self.pack.copy_slots()), list(range(0x87, 0x9B)))

    def test_loader_refuses_shared_and_bad_rows(self):
        import tempfile
        rows = {"shared byte": "А\tBB\tcopy:BB", "copy of no letter": "А\t87\tcopy:AB",
                "old latin source": "А\tBB\tlatin", "named byte": "Б\t53\tglyph",
                "control byte": "Б\tF9\tglyph", "two letters one byte": "Б\t37\tglyph\nГ\t37\tglyph"}
        for why, row in rows.items():
            with self.subTest(why), tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / "xx").mkdir()
                (Path(tmp) / "xx" / "charmap.tsv").write_text(f"char\tbyte\tsource\n{row}\n", encoding="utf-8")
                with self.assertRaises(ValueError):
                    LangPack.load("xx", root=Path(tmp))

    def test_glyph_slots_read_the_copies_from_a_font(self):
        from gen3tl.build.font import FontTable, encode_glyph as enc
        from gen3tl.core.rom import Rom
        font = FontTable("f", 0x0, 0x8000, 512)
        rom = Rom(bytes(0x8200))
        px = [[(x + y) % 4 for x in range(16)] for y in range(16)]
        rom.write(0xBB * 0x40, enc(px))
        rom.data[0x8000 + 0xBB] = 7
        pack = LangPack.load("ru", "unbound")
        self.assertNotIn(0x87, pack.glyph_slots())
        g = pack.glyph_slots(rom, font)[0x87]
        self.assertEqual((g.char, g.width, g.pixels), ("А", 7, px))
        extended = {key for key in pack.glyphs if isinstance(key, int)}
        self.assertEqual(set(pack.glyph_slots(rom, font)), set(pack.chars.values()) | extended)

    def test_write_glyphs_copies_in_every_font(self):
        from gen3tl.build.font import FontTable, write_glyphs
        from gen3tl.core.rom import Rom
        fonts = [FontTable("main", 0x0, 0x8000, 512, "unbound"), FontTable("small", 0x9000, 0xD000, 512, "s", "8x16")]
        rom = Rom(bytes(0xE000))
        rom.write(0xBB * 0x40, bytes(range(0x40)))
        rom.write(0x9000 + 0xBB * 0x20, bytes(range(0x20, 0x40)))
        rom.data[0x8000 + 0xBB], rom.data[0xD000 + 0xBB] = 6, 5
        changed = write_glyphs(rom, fonts, {"unbound": {}, "s": {}}, {0x87: 0xBB})
        self.assertEqual(rom.read(0x87 * 0x40, 0x40), bytes(range(0x40)))
        self.assertEqual(rom.read(0x9000 + 0x87 * 0x20, 0x20), bytes(range(0x20, 0x40)))
        self.assertEqual((rom.u8(0x8000 + 0x87), rom.u8(0xD000 + 0x87)), (6, 5))
        self.assertEqual(sorted(changed), [(0x87 * 0x40, 0x40), (0x8000 + 0x87, 1), (0x9000 + 0x87 * 0x20, 0x20),
                                           (0xD000 + 0x87, 1)])
        with self.assertRaises(ValueError):   # a source that is itself written
            write_glyphs(rom, fonts, {"unbound": {}, "s": {}}, {0x87: 0xBB, 0x88: 0x87})
