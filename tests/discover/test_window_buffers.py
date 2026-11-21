"""Controls for copied and expanded text reaching a printer."""

import unittest

from gen3tl.core.rom import ROM_BASE, Rom
from gen3tl.discover.references import literal_word
from gen3tl.discover.window_buffers import Transfers, copy_loop, expand_loop
from gen3tl.discover.window_consumers import Consumers, signature
from gen3tl.discover.window_flow import trace
from gen3tl.discover.window_printers import Printers
from test_window_consumers import call, words


class WindowBuffersTest(unittest.TestCase):
    def setUp(self):
        self.rom = Rom(b'\xff' * 4096)
        self.rom.write(0x500, words(0xB500, 0x1C03, 0xE002, 0x701A, 0x3301, 0x3101,
                                   0x780A, 0x1C10, 0x28FF, 0xD1F8, 0x20FF, 0x7018,
                                   0x1C18, 0xBC02, 0x4708))
        self.rom.write(0x600, words(0xB500, 0x6002, 0xBD00))
        f, branches = signature(self.rom, 0x600)
        self.printer = dict(f.document(), branches=branches, text_argument=2, window_argument=0)
        self.rom.write(0x100, words(0xB510, 0x4C07, 0x4907, 0x1C20)
                       + call(0x108, 0x500) + words(0x1C22)
                       + call(0x10E, 0x600) + words(0xBD10))
        self.rom.put_u32(0x120, 0x02000100)
        self.rom.put_u32(0x124, ROM_BASE + 0x700)

    def audit(self):
        ref = {'at': 0x124, 'status': 'reference', 'kind': 'literal-pool',
               'evidence': [literal_word(self.rom, 0x124)]}
        return Consumers(self.rom, [self.printer]).audit([
            {'id': 'sample', 'offset': 0x700, 'references': [ref]}])

    def test_unit_copy_loop_requires_terminator_and_source_advancement(self):
        self.assertEqual(copy_loop(self.rom, 0x500)['source'], 1)
        for at, changed in ((0x510, 0x2800), (0x50A, 0x3102), (0x508, 0x3302), (0x506, 0x801A)):
            with self.subTest(at=at):
                original = self.rom.read(at, 2)
                self.rom.write(at, words(changed))
                self.assertIsNone(copy_loop(self.rom, 0x500))
                self.rom.write(at, original)

    def test_buffer_is_consumed_only_after_copying(self):
        row, = self.audit()['strings'][0]['consumers']
        self.assertEqual(row['evidence']['buffer_trace'][0]['call'], 0x108)
        self.assertEqual(row['evidence']['call'], 0x10E)
        self.rom.write(0x108, call(0x108, 0x600))
        self.rom.write(0x10E, call(0x10E, 0x500))
        self.assertEqual(self.audit()['resolved'], 0)

    def test_mutating_call_or_store_invalidates_buffer_provenance(self):
        for middle in (words(0x1C20) + call(0x10E, 0x800), words(0x2000, 0x7020, 0x46C0)):
            with self.subTest(middle=middle):
                self.rom.write(0x10C, middle + words(0x1C22) + call(0x114, 0x600) + words(0xBD10))
                self.assertEqual(self.audit()['resolved'], 0)

    def test_a_copy_without_a_printer_remains_unresolved(self):
        self.rom.write(0x10C, words(0xBD10))
        self.assertEqual(self.audit()['resolved'], 0)

    def test_transitive_printer_preserves_source_parameter(self):
        self.rom.write(0x300, words(0xB510, 0x1C01, 0x4C06, 0x1C20)
                       + call(0x308, 0x500) + words(0x1C22, 0x2003)
                       + call(0x310, 0x600) + words(0xBD10))
        self.rom.put_u32(0x320, 0x02000100)
        row = Printers(self.rom, [self.printer]).get(0x300)
        self.assertEqual((row['text_argument'], row['window']), (0, {'constant': 3}))
        self.assertTrue(row['summaries'][0]['buffers'])

    def test_fixed_buffer_wrapper_requires_an_unmodified_destination(self):
        self.rom.write(0x300, words(0xB500, 0x1C01, 0x4802)
                       + call(0x306, 0x500) + words(0xBD00))
        self.rom.put_u32(0x310, 0x02000100)
        row = Transfers(self.rom).get(0x300)
        self.assertEqual((row['source'], row['destination_constant']), (0, 0x02000100))
        self.rom.write(0x30A, words(0x6001, 0xBD00))
        self.assertIsNone(Transfers(self.rom).get(0x300))

    def test_indexed_source_reaches_a_printer_through_a_buffer(self):
        self.rom.write(0x100, words(0xB530, 0x0080, 0x4C06, 0x5821, 0x4D06, 0x1C28)
                       + call(0x10C, 0x500) + words(0x1C2A)
                       + call(0x112, 0x600) + words(0xBD30))
        self.rom.put_u32(0x120, ROM_BASE + 0x800)
        self.rom.put_u32(0x124, 0x02000100)
        self.rom.put_u32(0x804, ROM_BASE + 0x700)
        string = {'id': 'sample', 'offset': 0x700, 'references': [{
            'at': 0x804, 'kind': 'table', 'status': 'reference',
            'evidence': [{'start': 0x800, 'end': 0x80C, 'stride': 4, 'fields': [0]}]}]}
        result = Consumers(self.rom, [self.printer]).audit([string])
        row, = result['strings'][0]['consumers']
        self.assertEqual(row['evidence']['read'], 0x106)
        self.assertEqual(row['evidence']['buffer_trace'][0]['call'], 0x10C)
        self.rom.write(0x506, words(0x801A))
        self.assertEqual(Consumers(self.rom, [self.printer]).audit([string])['resolved'], 0)

    def expander(self):
        self.rom.write(0x500, words(0xB530, 0x1C04, 0x1C0D, 0x782A, 0x3501, 0x1C10,
                                   0x38FA, 0x2805, 0xD826, 0x0080, 0x4902, 0x1840, 0x6800, 0x4687))
        self.rom.put_u32(0x520, ROM_BASE + 0x540)
        for n, at in enumerate((0x560, 0x560, 0x580, 0x590, 0x560, 0x568)):
            self.rom.put_u32(0x540 + n * 4, ROM_BASE + at)
        self.rom.write(0x560, words(0x7022, 0x3401, 0xE7CF))
        self.rom.write(0x568, words(0x20FF, 0x7020, 0x1C20, 0xBC30, 0xBC02, 0x4708))
        self.rom.write(0x580, words(0xE7EE))
        self.rom.write(0x590, words(0xE7E6))

    def test_codec_dispatch_requires_guard_and_terminating_copy_path(self):
        self.expander()
        self.assertEqual(expand_loop(self.rom, 0x500)['source'], 1)
        self.assertEqual(self.audit()['resolved'], 1)
        for at, changed in ((0x510, 0xD926), (0x568, 0x2000), (0x508, 0x3502), (0x56E, 0xBC10)):
            with self.subTest(at=at):
                original = self.rom.read(at, 2)
                self.rom.write(at, words(changed))
                self.assertIsNone(expand_loop(self.rom, 0x500))
                self.rom.write(at, original)


if __name__ == '__main__':
    unittest.main()
