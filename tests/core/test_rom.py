import sys
import hashlib
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from test_roots import TOOLKIT_ROOT

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.core.rom import (Rom, RomError, RomEvidence, field_spec, pointer_at, ptr_bytes,  # noqa: E402
                             rom_offset, value_at)
from gen3tl.cli.main import main
from project_fixture import rom_evidence


class RomTest(unittest.TestCase):
    def test_sha1_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.gba"
            p.write_bytes(b"\x00" * 64)
            rom = Rom.load(p)
            Rom.load(p, rom.sha1())
            with self.assertRaises(RomError):
                Rom.load(p, "0" * 40)

    def test_pointers(self):
        rom = Rom(b"\x00" * 16)
        rom.write(4, ptr_bytes(8))
        self.assertEqual(rom.ptr_at(4), 8)
        self.assertIsNone(rom.ptr_at(0))
        rom.write(8, (0x08000000 + 64).to_bytes(4, "little"))
        self.assertIsNone(rom.ptr_at(8))  # outside the 16-byte image
        rom.write(8, ptr_bytes(12))
        self.assertEqual(rom.ptr_at(8), 12)
        for off in (-8, 13, 16):   # the same bytes from the end, cut off, past the image
            with self.subTest(off=off):
                self.assertIsNone(rom.ptr_at(off))
        with self.assertRaises(RomError):
            rom.write(14, b"\x00\x00\x00")

    def test_reads_stay_inside_the_image(self):
        rom = Rom(bytes(range(8)))
        self.assertEqual((rom.u8(7), rom.u16(6), rom.u32(4), rom.read(6, 2), rom.read(8, 0)),
                         (7, 0x0706, 0x07060504, b"\x06\x07", b""))
        reads = {"u8": (rom.u8, 1), "u16": (rom.u16, 2), "u32": (rom.u32, 4),
                 "read": (lambda off: rom.read(off, 3), 3)}
        for name, (read, width) in reads.items():
            for off in (-1, -width, 9 - width):   # from the end, cut off at the end
                with self.subTest(read=name, off=off), self.assertRaisesRegex(IndexError, "leaves the ROM"):
                    read(off)
        with self.assertRaises(IndexError):
            rom.read(4, -2)   # a negative size reads from the end too

    def test_bounded_values_and_pointers(self):
        data = b"\xfe\xff" + ptr_bytes(8) + (0x08000000 + 64).to_bytes(4, "little") + b"\x00" * 6
        self.assertEqual((value_at(data, 0, 2), value_at(data, 0, 2, signed=True)), (0xFFFE, -2))
        self.assertEqual(value_at(data, 2), 0x08000008)
        for at, width in ((-1, 1), (15, 2), (0, 0)):
            with self.subTest(at=at, width=width):
                self.assertIsNone(value_at(data, at, width))
        self.assertEqual(pointer_at(data, 2), 8)
        for at in (-4, 0, 6, 13):   # outside, not a pointer, past the image, cut off
            with self.subTest(at=at):
                self.assertIsNone(pointer_at(data, at))

    def test_window_offsets(self):
        self.assertEqual(rom_offset(0x08000010), 0x10)
        self.assertEqual(rom_offset(0x09FFFFFF), 0x1FFFFFF)   # the window, not one image
        for value in (None, 0, 0x07FFFFFF, 0x0A000000):
            with self.subTest(value=value):
                self.assertIsNone(rom_offset(value))

    def test_field_specs(self):
        for spec, expected in ((3, (3, 1, False)), ({"offset": 2}, (2, 1, False)),
                               ({"offset": 4, "width": 2, "signed": True}, (4, 2, True))):
            with self.subTest(spec=spec):
                self.assertEqual(field_spec(spec), expected)


class RomEvidenceTest(unittest.TestCase):
    def test_every_changed_byte_and_truncated_span_fails(self):
        data = bytes(range(32))
        evidence = RomEvidence.parse(rom_evidence(8, data[8:24]))
        self.assertEqual(evidence.read(data), data[8:24])
        for offset in range(8, 24):
            with self.subTest(offset=offset):
                changed = bytearray(data)
                changed[offset] ^= 1
                self.assertFalse(evidence.matches(changed))
                with self.assertRaisesRegex(ValueError, "evidence differs"):
                    evidence.read(changed)
        self.assertFalse(evidence.matches(data[:23]))

    def test_invalid_descriptors_fail(self):
        valid = rom_evidence(8, b"synthetic")
        cases = [{}, {"offset": 8, "bytes": "00"}, dict(valid, extra=1)]
        for key, values in (("offset", (-1, True, "8", 1 << 25)),
                            ("size", (0, -1, False, "9", 1 << 25)),
                            ("sha256", (None, 4, "g" * 64, "a" * 63, "A" * 64))):
            cases.extend(dict(valid, **{key: value}) for value in values)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                RomEvidence.parse(value)

    def test_command_reads_evidence_and_guard_without_a_language_project(self):
        data = bytes(range(32))
        digest = hashlib.sha256(data[8:16]).hexdigest()
        descriptor = f'offset = 8, size = 8, sha256 = "{digest}"'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rom, profile = root / "base.gba", root / "sample.toml"
            rom.write_bytes(data)
            profile.write_text('[rom]\nsha1 = "' + hashlib.sha1(data).hexdigest() + '"\n'
                               '[support]\nstage = "discovery"\n'
                               '[[span]]\n' + descriptor.replace(', ', '\n') + '\n'
                               '[[proof]]\nname = "sample"\nrom = [{ ' + descriptor + ' }]\n'
                               'guard = { ' + descriptor + ' }\n', encoding="utf-8")
            for fact, field in (("proof[name=sample]", "rom.0"), ("proof[name=sample]", "guard"),
                                ("span[offset=0x8]", None)):
                out = io.StringIO()
                with self.subTest(field=field), redirect_stdout(out):
                    result = main(["--profile", str(profile), "--rom", str(rom),
                                   "rom", "evidence", fact, *(["--field", field] if field else [])])
                self.assertIn(result, (None, 0))
                self.assertIn("bytes        " + data[8:16].hex(), out.getvalue())
                self.assertIn("verified     passed", out.getvalue())
            for fact, field, reason in (("missing", "guard", "unknown profile fact"),
                                        ("proof[name=sample]", "rom.9", "unknown evidence field")):
                with self.subTest(fact=fact, field=field), redirect_stdout(io.StringIO()), \
                        redirect_stderr(io.StringIO()) as err:
                    result = main(["--profile", str(profile), "--rom", str(rom),
                                   "rom", "evidence", fact, "--field", field])
                self.assertEqual(result, 1)
                self.assertIn(reason, err.getvalue())
            profile.write_text(profile.read_text().replace(digest, "0" * 64))
            with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
                result = main(["--profile", str(profile), "--rom", str(rom),
                               "rom", "evidence", "proof[name=sample]", "--field", "guard"])
            self.assertEqual(result, 1)
            self.assertNotIn("bytes        ", out.getvalue())
            self.assertIn("ROM evidence differs", err.getvalue())


if __name__ == "__main__":
    unittest.main()
