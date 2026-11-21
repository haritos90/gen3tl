"""Controls for bounded consumer switch traversal."""

import unittest

from gen3tl.core.rom import ROM_BASE, Rom
from gen3tl.discover.window_switches import dispatch, walk
from gen3tl.discover.window_functions import returning_body
from gen3tl.discover.window_consumers import signature, matches
from gen3tl.families.fingerprints import find
from test_window_consumers import words


class WindowSwitchesTest(unittest.TestCase):
    def setUp(self):
        self.rom = Rom(b'\xff' * 2048)
        self.rom.write(0x100, words(0xB500, 0x2801, 0xD812, 0x0080, 0x4903,
                                    0x1840, 0x6800, 0x4687))
        self.rom.put_u32(0x118, ROM_BASE + 0x11C)
        self.rom.put_u32(0x11C, ROM_BASE + 0x124)
        self.rom.put_u32(0x120, ROM_BASE + 0x128)
        self.rom.write(0x124, words(0x2001, 0xE002, 0x2002, 0xE000, 0x2000, 0xBD00))

    def test_bounded_cases_return_without_decoding_table(self):
        table = dispatch(self.rom.data, 0x10E, 0x100, 0x130)
        self.assertEqual(table['targets'], (0x124, 0x128))
        body, reason = returning_body(self.rom, 0x100, span=0x30)
        self.assertIsNone(reason)
        self.assertIn(0x128, {i.address for i in body})
        self.assertNotIn(0x11C, {i.address for i in body})

    def test_missing_bound_and_external_cases_are_refused(self):
        self.rom.write(0x102, words(0x46C0))
        self.assertIsNone(dispatch(self.rom.data, 0x10E, 0x100, 0x130))
        self.assertFalse(returning_body(self.rom, 0x100, span=0x30)[0])
        self.rom.write(0x102, words(0x2801))
        self.rom.put_u32(0x120, ROM_BASE + 0x150)
        self.assertIsNone(dispatch(self.rom.data, 0x10E, 0x100, 0x130))
        self.assertFalse(returning_body(self.rom, 0x100, span=0x30)[0])

    def test_table_entry_cannot_point_into_table_or_nonreturning_code(self):
        for target in (0x11C, 0x12A):
            with self.subTest(target=target):
                self.rom.put_u32(0x120, ROM_BASE + target)
                self.rom.write(0x12A, words(0x4708))
                self.assertFalse(returning_body(self.rom, 0x100, span=0x30)[0])

    def test_case_code_and_destinations_participate_in_signature(self):
        f, branches = signature(self.rom, 0x100)
        row = dict(f.document(), branches=branches)
        self.assertTrue(matches(self.rom, 0x100, row))
        self.assertEqual(find(self.rom.data, (f,), walker=walk)[f.name], (0x100,))
        self.rom.write(0x128, words(0x2003))
        self.assertFalse(matches(self.rom, 0x100, row))

    def test_case_order_is_part_of_the_contract(self):
        f, branches = signature(self.rom, 0x100)
        self.rom.put_u32(0x11C, ROM_BASE + 0x128)
        self.rom.put_u32(0x120, ROM_BASE + 0x124)
        self.assertFalse(matches(self.rom, 0x100, dict(f.document(), branches=branches)))


if __name__ == '__main__':
    unittest.main()
