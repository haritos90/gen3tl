from test_roots import TOOLKIT_ROOT
"""Savefile names checks and fixtures."""

import sys
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.verify.savefile import EOS, _decode, _has_target_letters, renamed, standalone_source
from savefile_support import setUpModule


def field(name: bytes, rest: bytes = b"") -> bytes:
    return (name + bytes([EOS]) + rest + bytes([EOS]) * 10)[:10]


class RenameTest(unittest.TestCase):
    def test_longer_and_shorter_names(self):
        self.assertEqual(renamed(field(b"ONIX"), b"ONIX", b"ONIKS"), field(b"ONIKS"))
        self.assertEqual(renamed(field(b"ONIKS"), b"ONIKS", b"ONIX"), field(b"ONIX"))
        self.assertEqual(renamed(field(b"ABCDEFGHIJ"), b"ABCDEFGHIJ", b"KLMNOPQRST"), b"KLMNOPQRST")
        self.assertEqual(renamed(b"ABCDEFGHIJ", b"ABCDEFGHIJ", b"XY"), field(b"XY"))

    def test_bytes_past_both_names_are_kept(self):
        f = b"AB\xff\xff\xff\xff\x12\x34\x56\x78"
        out = renamed(f, b"AB", b"CDE")
        self.assertEqual(out, b"CDE\xff\xff\xff\x12\x34\x56\x78")
        self.assertEqual(renamed(out, b"CDE", b"AB"), f)


class ReportScriptTest(unittest.TestCase):
    """Converted names print in the script of their ROM."""
    TABLE = {0xC2: "H", 0xC9: "O", 0x37: "и", 0x38: "д"}   # 0x37/0x38: Cyrillic-only slots
    SHARED = {0xC2: "Н", 0xC9: "О"}                          # Cyrillic letters on Latin look-alike bytes

    def test_decode_both_directions(self):
        ru_names, en_names = [bytes([0xC2, 0x37, 0x38])], [bytes([0xC2, 0xC9])]
        self.assertTrue(_has_target_letters(ru_names, self.TABLE))
        self.assertFalse(_has_target_letters(en_names, self.TABLE))
        self.assertEqual(_decode(ru_names[0], self.TABLE, self.SHARED), "Нид")
        self.assertEqual(_decode(en_names[0], self.TABLE, None), "HO")

    def test_standalone_embeds_both_tables(self):
        src = standalone_source({"size": 1}, self.TABLE, self.SHARED)
        compile(src, "converter.py", "exec")
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "converter.py"
            script.write_text(src, encoding="utf-8")
            result = subprocess.run([sys.executable, "-I", str(script)], cwd=tmp,
                                    capture_output=True, text=True)
            help_result = subprocess.run([sys.executable, "-I", str(script), "--help"], cwd=tmp,
                                         capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertTrue(result.stderr.startswith("error        "), result.stderr)
        self.assertEqual(len(result.stderr.splitlines()), 1)
        self.assertNotIn("hint", result.stderr)
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("standalone use prints no hint lines.", help_result.stdout)
        self.assertIn('CHARMAP_TARGET = {"194": "Н", "201": "О"}', src)
        self.assertIn("main(charmap=CHARMAP, charmap_target=CHARMAP_TARGET)", src)
