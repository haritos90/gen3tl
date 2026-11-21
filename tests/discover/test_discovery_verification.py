"""Passing and failing controls for discovery verification."""

import copy
import unittest
from unittest.mock import patch

from gen3tl.core.charmap import Charmap, CharmapError
from gen3tl.core.rom import ROM_BASE, Rom
from gen3tl.discover import verification
from gen3tl.verify.savefile import SaveFile
import test_save_discovery as save_controls
from gen3tl.discover import save


class VerificationTest(unittest.TestCase):
    def test_decode_back_checks_controls_and_unterminated_fields(self):
        raw = Charmap().encode("Name{PLAYER}\\n")
        rom = Rom(raw)
        self.assertEqual(verification.decode_back(rom, 0), ("Name{PLAYER}\\n", len(raw)))
        self.assertEqual(verification.decode_back(rom, 0, len(raw) - 1, terminated=False)[1], len(raw) - 1)
        with self.assertRaises(CharmapError):
            verification.decode_back(rom, 0, len(raw) - 1)
        with patch.object(Charmap, "encode", return_value=b"wrong\xff"):
            with self.assertRaisesRegex(ValueError, "decode-back differs"):
                verification.decode_back(rom, 0)

    def test_pointer_census_refuses_missing_duplicate_and_wrong_positions(self):
        rom = Rom(b"\xff" * 128)
        for at in (0, 4):
            rom.put_u32(at, ROM_BASE + 64)
        index = {64: [0, 4]}
        self.assertEqual(verification.references(rom, 64, [4, 0], index), 2)
        for positions in ([0], [0, 0], [0, 8]):
            with self.subTest(positions=positions), self.assertRaisesRegex(ValueError, "census differs"):
                verification.references(rom, 64, positions, index)
        rom.put_u32(4, ROM_BASE + 65)
        with self.assertRaisesRegex(ValueError, "census differs"):
            verification.references(rom, 64, [0, 4], index)

    def test_row_domain_requires_count_bijection_and_decodable_fields(self):
        rom = Rom(Charmap().encode("One").ljust(8, b"\xff") + Charmap().encode("Two").ljust(8, b"\xff"))
        table = {"base": 0, "stride": 8, "count": 2, "field_offset": 0, "field_length": 8}
        self.assertEqual(verification.name_rows(rom, table, 2), 2)
        with self.assertRaisesRegex(ValueError, "row count"):
            verification.name_rows(rom, table, 3)
        self.assertEqual(verification.bijection([1, 0], range(2)), 2)
        for ids in ([0, 0], [0], [0, 2]):
            with self.assertRaisesRegex(ValueError, "bijection"):
                verification.bijection(ids, range(2))
        rom.write(8, b"\xbb" * 8)
        with self.assertRaises(CharmapError):
            verification.name_rows(rom, table, 2)

    def test_indirect_name_fields_round_trip(self):
        rom = Rom(b"\xff" * 128)
        rom.put_u32(0, ROM_BASE + 64)
        rom.write(64, Charmap().encode("Long name"))
        table = {"base": 0, "stride": 8, "count": 1, "field_offset": 0,
                 "field_length": 8, "long_name_pointers": True}
        self.assertEqual(verification.name_rows(rom, table, 1), 1)
        rom.write(64, b"\xbb" * 64)
        with self.assertRaises(CharmapError):
            verification.name_rows(rom, table, 1)

    def test_save_round_trip_checks_both_slots_rotations_and_overlap(self):
        rom, family, tables, readers, _ = save_controls.SaveLayoutTest().fixture()
        data = save.run(rom, family, tables, readers).data["save"]
        for index, block in enumerate(data["blocks"].values()):
            block["ram"] = 0x02000000 + index * 0x10000
        data["parasite"]["ram"] = 0x02030000
        data["extra"]["ram"] = 0x02040000
        self.assertEqual(verification.save_round_trip(data), 28)
        bad = copy.deepcopy(data)
        bad["blocks"]["saveblock1"]["ram"] = bad["blocks"]["saveblock2"]["ram"]
        with self.assertRaisesRegex(ValueError, "overlapping"):
            verification.save_round_trip(bad)
        with patch.object(SaveFile, "read", return_value=b"wrong"):
            with self.assertRaisesRegex(ValueError, "RAM mapping differs"):
                verification.save_round_trip(data)


if __name__ == "__main__":
    unittest.main()
