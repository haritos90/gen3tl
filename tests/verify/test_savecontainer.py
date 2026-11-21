import gzip
import hashlib
import struct
import unittest
from pathlib import Path

from test_roots import TEST_ROOT, TOOLKIT_ROOT

ROOT = TOOLKIT_ROOT

from gen3tl.verify.savefile import SaveError, gsv_checksum, unwrap
from project_fixture import select_language

FIXTURES = TEST_ROOT / "fixtures" / "saves"

def setUpModule():
    select_language("ru")

def ccitt_false(data: bytes) -> int:
    """Bitwise reference independent of the converter's standard-library implementation."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ (0x1021 if crc & 0x8000 else 0)) & 0xFFFF
    return crc

def fixture(name: str) -> bytes:
    return gzip.decompress((FIXTURES / f"{name}.gz").read_bytes())

class ContainerTest(unittest.TestCase):
    RAW = fixture("raw.sav")


    def test_gsv_standard_crc_vector(self):
        self.assertEqual(gsv_checksum(b"123456789"), 0x29B1)
        self.assertEqual(ccitt_false(b"123456789"), 0x29B1)

    def test_gsv_authentic_file_checksum_and_edit(self):
        data = fixture("gameshark-sp-authentic.gsv")
        self.assertEqual(hashlib.sha256(data).hexdigest(),
                         "01bd246dc51d03cc300432d5fa36ebdfe939075f3f3c04c91fff6a2e32952d8a")
        held, payload = unwrap(data, 512)
        self.assertEqual(struct.unpack_from("<III", data, 0x20), (0xC831, 512, 0))
        self.assertEqual(ccitt_false(payload), 0xC831)
        self.assertEqual(held.wrap(payload), data)
        changed = bytes([payload[0] ^ 1]) + payload[1:]
        result = held.wrap(changed)
        self.assertEqual(result[:0x20], data[:0x20])
        self.assertEqual(result[0x22:0x430], data[0x22:0x430])
        self.assertEqual(result[0x430:], changed)
        self.assertEqual(struct.unpack_from("<H", result, 0x20)[0], ccitt_false(changed))
        self.assertNotEqual(result[0x20:0x22], data[0x20:0x22])


    def test_gsv_public_inconsistent_size_file_refuses_edits(self):
        data = fixture("gameshark-sp-inconsistent.gsv")
        self.assertEqual(hashlib.sha256(data).hexdigest(),
                         "88d397d997a682490fed20c73a9e0ad506e995de28e609a88d9d0eeba118a39c")
        held, flash = unwrap(data, 32768)
        self.assertEqual(held.wrap(flash), data)
        self.assertEqual(struct.unpack_from("<I", data, 0x24)[0], 32768)
        self.assertEqual(len(data) - 0x430, 65540)
        with self.assertRaisesRegex(SaveError, "payload size differs"):
            held.wrap(bytes([flash[0] ^ 1]) + flash[1:])
