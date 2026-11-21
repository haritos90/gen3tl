import tempfile
import unittest
from pathlib import Path

from gen3tl.lang.langpack import LangPack, LangPackError

class BaseMappingTest(unittest.TestCase):
    def test_mixed_base_and_new_mappings_keep_their_ownership(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / "xx").mkdir()
            (root / "xx/charmap.tsv").write_text("char\tbyte\tsource\nA\tBB\tbase\nÄ\tF1\tbase\nБ\t37\tglyph\n")
            pack = LangPack.load("xx", root=root)
            self.assertEqual(pack.charmap().encode("AÄБ", False), bytes([0xBB, 0xF1, 0x37]))
            self.assertEqual(pack.allocated_slots(), {0x37})
            self.assertEqual(pack.copy_slots(), {})
            for row in ("A\tBC\tbase", "Б\tBB\tbase", "A\t37\tbase", "A\tBB\tglyph", "A\tBB\tbase\nA\tBB\tbase"):
                (root / "xx/charmap.tsv").write_text("char\tbyte\tsource\n" + row + "\n")
                with self.subTest(row=row), self.assertRaises(LangPackError):
                    LangPack.load("xx", root=root)

    def test_base_glyph_bounds_width_and_presence_are_checked(self):
        pack = LangPack("xx", chars={"A": 0xBB}, sources={"A": "base"})
        class Font:
            name, fmt, count = "fixture", "8x16", 256
            def width(self, rom, byte): return 6
            def glyph(self, rom, byte): return [[1]]
        font = Font()
        self.assertEqual(pack.base_problems(None, [font]), [])
        font.count = 10
        self.assertIn("outside", pack.base_problems(None, [font])[0])
        font.count = 256
        font.width = lambda *_: 9
        self.assertIn("invalid width", pack.base_problems(None, [font])[0])
        font.width = lambda *_: 6
        font.glyph = lambda *_: [[0]]
        self.assertIn("blank", pack.base_problems(None, [font])[0])
