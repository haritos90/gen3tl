"""Controls for returned text pointers and record fields."""

import unittest

from gen3tl.core.rom import ROM_BASE, Rom
from gen3tl.discover.window_consumers import Consumers, signature
from gen3tl.discover.window_getters import Getters, source_match
from test_window_consumers import words, call


class WindowGettersTest(unittest.TestCase):
    def setUp(self):
        self.rom = Rom(b'\xff' * 4096)
        self.rom.put_u32(0x80, ROM_BASE + 0x101)
        self.rom.write(0x100, words(0xB500) + call(0x102, 0x200) + words(0x1C02)
                       + call(0x108, 0x300) + words(0xBD00))
        self.rom.write(0x300, words(0xB500, 0x6002, 0xBD00))
        f, b = signature(self.rom, 0x300)
        self.seed = dict(f.document(), branches=b, text_argument=2)

    def consumer(self, reference, offset=0x600):
        return Consumers(self.rom, [self.seed]).audit([{
            'id': 'sample', 'offset': offset, 'references': [reference] if reference else []}])

    def test_literal_return_reaches_print_argument(self):
        self.rom.write(0x200, words(0x4801, 0x4770))
        self.rom.put_u32(0x208, ROM_BASE + 0x600)
        reference = {'at': 0x208, 'kind': 'literal-pool', 'status': 'reference',
                     'evidence': [{'loads': [{'function': 0x200, 'load': 0x200}]}]}
        result = self.consumer(reference)
        self.assertEqual(result['resolved'], 1)
        self.assertEqual(result['strings'][0]['consumers'][0]['slot'], 'call:8:r2')
        self.rom.write(0x106, words(0x2200))
        self.assertEqual(self.consumer(reference)['resolved'], 0)

    def test_indexed_pointer_return_keeps_the_table_domain(self):
        self.rom.write(0x200, words(0x0080, 0x4902, 0x5808, 0x4770))
        self.rom.put_u32(0x20C, ROM_BASE + 0x400)
        self.rom.put_u32(0x400, ROM_BASE + 0x600)
        reference = {'at': 0x400, 'kind': 'table', 'status': 'reference', 'evidence': [
            {'start': 0x400, 'end': 0x408, 'stride': 4, 'fields': [0]}]}
        result = self.consumer(reference)
        self.assertEqual(result['resolved'], 1)
        self.assertEqual(result['strings'][0]['consumers'][0]['evidence']['stride'], 4)
        reference['evidence'][0]['stride'] = 8
        self.assertEqual(self.consumer(reference)['resolved'], 0)

    def test_inline_field_return_has_affine_stride(self):
        self.rom.write(0x200, words(0x210C, 0x4348, 0x4902, 0x1840, 0x4770))
        self.rom.put_u32(0x210, ROM_BASE + 0x600)
        self.assertEqual(self.consumer(None, 0x60C)['resolved'], 1)
        self.assertEqual(self.consumer(None, 0x60D)['resolved'], 0)

    def test_literal_tail_thunk_preserves_returned_text(self):
        self.rom.write(0x200, words(0x4801, 0x4770))
        self.rom.put_u32(0x208, ROM_BASE + 0x600)
        self.rom.write(0x180, words(0x4B00, 0x4718))
        self.rom.put_u32(0x184, ROM_BASE + 0x201)
        self.rom.write(0x102, call(0x102, 0x180))
        reference = {'at': 0x208, 'kind': 'literal-pool', 'status': 'reference',
                     'evidence': [{'loads': [{'function': 0x200, 'load': 0x200}]}]}
        self.assertEqual(self.consumer(reference)['resolved'], 1)
        self.rom.put_u32(0x184, ROM_BASE + 0x200)
        self.assertEqual(self.consumer(reference)['resolved'], 0)

    def test_inline_base_can_come_from_a_rom_record_field(self):
        self.rom.write(0x200, words(0x210C, 0x4348, 0x4902, 0x6809, 0x1840, 0x4770))
        self.rom.put_u32(0x210, ROM_BASE + 0x900)
        self.rom.put_u32(0x900, ROM_BASE + 0x600)
        self.assertEqual(self.consumer(None, 0x60C)['resolved'], 1)
        self.rom.put_u32(0x900, 0x02000100)
        self.assertEqual(self.consumer(None, 0x60C)['resolved'], 0)

    def test_nonreturning_and_runtime_results_are_unresolved(self):
        self.rom.write(0x200, words(0xE7FE))
        self.assertIsNone(Getters(self.rom).get(0x200))
        self.rom.write(0x200, words(0x4801, 0x6800, 0x4770))
        self.rom.put_u32(0x208, 0x02000100)
        summary = Getters(self.rom).get(0x200)
        self.assertIsNone(summary)


if __name__ == '__main__':
    unittest.main()
