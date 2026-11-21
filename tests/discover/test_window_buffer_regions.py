"""Controls for buffer preservation across disjoint stores."""

import unittest

from gen3tl.core.rom import ROM_BASE, Rom
from gen3tl.discover.window_flow import trace
from test_window_consumers import call, words


class WindowBufferRegionsTest(unittest.TestCase):
    def setUp(self):
        self.rom = Rom(b'\xff' * 4096)
        self.rom.write(0x100, words(0xB510, 0x480B, 0x490C) + call(0x106, 0x500)
                       + words(0x4C0C, 0x2300, 0x7023, 0x4A07)
                       + call(0x112, 0x600) + words(0xBD10))
        self.rom.put_u32(0x130, 0x02001000)
        self.rom.put_u32(0x134, ROM_BASE + 0x700)
        self.rom.put_u32(0x13C, 0x03000100)

    def copied(self):
        flow = trace(self.rom, 0x100, 0x118, transfers={0x500: {'source': 1, 'destination': 0}})
        printed = next(c for c in flow.calls if c.target == 0x600)
        return flow.sources.get(printed, {}).get(2, ())

    def test_other_ram_bank_and_preceding_store_preserve_source(self):
        self.assertTrue(self.copied())
        self.rom.put_u32(0x13C, 0x02000FFF)
        self.assertTrue(self.copied())

    def test_same_bank_following_store_and_mirror_are_unresolved(self):
        for address in (0x02001000, 0x02001001, 0x02041000):
            with self.subTest(address=address):
                self.rom.put_u32(0x13C, address)
                self.assertFalse(self.copied())

    def test_unknown_store_still_invalidates_source(self):
        self.rom.write(0x10A, words(0x1C04))
        self.assertFalse(self.copied())

    def test_stack_offsets_compare_as_signed_displacements(self):
        self.rom.write(0x100, words(0xB500, 0xB084, 0x4668, 0x490A)
                       + call(0x108, 0x500) + words(0xAC05, 0x2300, 0x7023, 0x466A)
                       + call(0x114, 0x600) + words(0xB004, 0xBD00))
        self.rom.put_u32(0x130, ROM_BASE + 0x700)
        flow = trace(self.rom, 0x100, 0x11C, transfers={0x500: {'source': 1, 'destination': 0}})
        printed = next(c for c in flow.calls if c.target == 0x600)
        self.assertFalse(flow.sources.get(printed, {}).get(2))


if __name__ == '__main__':
    unittest.main()
