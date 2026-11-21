import random
import sys
import unittest
import zlib
from pathlib import Path

from test_roots import TOOLKIT_ROOT

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.core import ups  # noqa: E402


class UpsTest(unittest.TestCase):
    def test_roundtrip(self):
        rnd = random.Random(7)
        src = bytes(rnd.randrange(256) for _ in range(5000))
        tgt = bytearray(src)
        for _ in range(200):
            p = rnd.randrange(len(tgt))
            tgt[p] = rnd.randrange(256)
        tgt[-1] ^= 0xFF  # change at the very end
        tgt[0] ^= 0x01   # and at the start
        patch = ups.create(src, bytes(tgt))
        self.assertEqual(ups.apply(src, patch), bytes(tgt))

    def test_wrong_source_rejected(self):
        patch = ups.create(b"abcdef", b"abXdef")
        with self.assertRaises(ups.UpsError):
            ups.apply(b"abcdeg", patch)

    def test_varint(self):
        for n in (0, 1, 127, 128, 255, 16511, 16512, 2 ** 25, 2 ** 32):
            self.assertEqual(ups._vread(ups._vlen(n), 0)[0], n)

    def test_records_cannot_cross_rom_or_crc_bounds(self):
        source = bytes(range(256))
        for records in (ups._vlen(256) + b"\0", ups._vlen(0) + b"\x01",
                        ups._vlen(255) + b"\x01\x01\0", b"\0", b"\0" * 12):
            data = b"UPS1" + ups._vlen(256) * 2 + records
            data += zlib.crc32(source).to_bytes(4, "little") * 2
            patch = data + zlib.crc32(data).to_bytes(4, "little")
            with self.subTest(records=records):
                with self.assertRaises(ups.UpsError):
                    ups.apply(source, patch)

    def test_header_cannot_read_crc_as_size(self):
        data = b"UPS1\0\0" + b"\x80" * 8
        patch = data + zlib.crc32(data).to_bytes(4, "little")
        with self.assertRaisesRegex(ups.UpsError, "truncated varint"):
            ups.apply(b"a", patch)


if __name__ == "__main__":
    unittest.main()
