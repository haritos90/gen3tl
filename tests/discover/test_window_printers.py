"""Controls for transitive printer argument summaries."""

import unittest

from gen3tl.core.rom import ROM_BASE, Rom
from gen3tl.discover.window_consumers import Consumers, signature
from gen3tl.discover.references import literal_word
from gen3tl.discover.emission import literal
from gen3tl.discover.window_flow import parameter, trace
from gen3tl.discover.window_printers import Printers
from test_window_consumers import call, words


class PrinterSummariesTest(unittest.TestCase):
    def setUp(self):
        self.rom = Rom(b"\xff" * 4096)
        self.rom.write(0x200, words(0xB500, 0x6002, 0xBD00))
        fingerprint, branches = signature(self.rom, 0x200)
        self.seed = dict(fingerprint.document(), branches=branches, text_argument=2,
                         window_argument=0, font_argument=1, x_argument=3)

    def test_transitive_parameter_and_constant_window(self):
        self.rom.write(0x300, words(0xB500, 0x1C0A, 0x2003) + call(0x306, 0x200) + words(0xBD00))
        self.rom.write(0x400, words(0xB500, 0x1C01) + call(0x404, 0x300) + words(0xBD00))
        printers = Printers(self.rom, [self.seed])
        first, second = printers.get(0x300), printers.get(0x400)
        self.assertEqual((first['text_argument'], first['window']), (1, {'constant': 3}))
        self.assertEqual((second['text_argument'], second['window']), (0, {'constant': 3}))
        self.assertEqual(second['depth'], 2)
        self.assertIn('text_argument', literal(printers.get(0x200)['summaries']))

    def test_overwritten_pointer_and_unmatched_callee_are_rejected(self):
        self.rom.write(0x300, words(0xB500, 0x2200) + call(0x304, 0x200) + words(0xBD00))
        self.assertIsNone(Printers(self.rom, [self.seed]).get(0x300))
        self.rom.write(0x202, words(0x6001))
        self.assertIsNone(Printers(self.rom, [self.seed]).get(0x200))

    def test_stack_text_argument_and_local_frame_survive_unrelated_call(self):
        self.rom.write(0x300, words(0xB510, 0xB082, 0x9A04, 0x9200)
                       + call(0x308, 0x600) + words(0x9A00)
                       + call(0x30E, 0x200) + words(0xB002, 0xBD10))
        row = Printers(self.rom, [self.seed]).get(0x300)
        self.assertEqual(row['text_argument'], 4)
        self.rom.write(0x308, words(0x4668, 0x46C0))
        self.rom.write(0x30C, call(0x30C, 0x600) + words(0x9A00)
                       + call(0x312, 0x200) + words(0xB002, 0xBD10))
        self.assertIsNone(Printers(self.rom, [self.seed]).get(0x300))

    def test_template_printer_derives_text_from_stored_field(self):
        seed = dict(self.seed, text_argument=0, text_field=0, window_argument=0, window_field=4)
        self.rom.write(0x300, words(0xB500, 0xB082, 0x9100, 0x4668, 0x2205, 0x7102)
                       + call(0x30C, 0x200) + words(0xB002, 0xBD00))
        row = Printers(self.rom, [seed]).get(0x300)
        self.assertEqual((row['text_argument'], row['window']), (1, {'constant': 5}))
        self.rom.write(0x304, words(0x9200))
        self.assertEqual(Printers(self.rom, [seed]).get(0x300)['text_argument'], 2)

    def test_narrowed_text_is_not_forwarded_as_a_pointer(self):
        self.rom.write(0x300, words(0xB500, 0x0602, 0x0E12)
                       + call(0x306, 0x200) + words(0xBD00))
        self.assertIsNone(Printers(self.rom, [self.seed]).get(0x300))

    def test_window_narrowing_retains_parameter_and_constant(self):
        self.rom.write(0x300, words(0xB500, 0x0600, 0x0E00)
                       + call(0x306, 0x200) + words(0xBD00))
        row = Printers(self.rom, [self.seed]).get(0x300)
        self.assertEqual(row['window'], {'narrow': 8, 'value': {'parameter': 0}})
        self.rom.write(0x302, words(0x2007, 0x46C0))
        self.assertEqual(Printers(self.rom, [self.seed]).get(0x300)['window'], {'constant': 7})

    def test_register_thunk_requires_a_preserved_thumb_target(self):
        self.rom.write(0x100, words(0xB510, 0x4C03, 0x4A03)
                       + call(0x106, 0x500) + words(0xBD10))
        self.rom.write(0x500, words(0x4720))
        self.rom.put_u32(0x110, ROM_BASE + 0x201)
        self.rom.put_u32(0x114, ROM_BASE + 0x700)
        string = {'id': 'sample', 'offset': 0x700, 'references': [{
            'at': 0x114, 'status': 'reference', 'kind': 'literal-pool',
            'evidence': [literal_word(self.rom, 0x114)]}]}
        result = Consumers(self.rom, [self.seed]).audit([string])
        row, = result['strings'][0]['consumers']
        self.assertEqual(row['evidence']['target'], 0x200)
        self.assertEqual(row['evidence']['redirect']['thunk'], 0x500)
        self.rom.put_u32(0x110, ROM_BASE + 0x200)
        self.assertEqual(Consumers(self.rom, [self.seed]).audit([string])['resolved'], 0)
        self.rom.put_u32(0x110, ROM_BASE + 0x201)
        self.rom.write(0x102, words(0x2400))
        self.assertEqual(Consumers(self.rom, [self.seed]).audit([string])['resolved'], 0)

    def test_unknown_recursive_calls_do_not_invent_printers(self):
        self.rom.write(0x300, words(0xB500) + call(0x302, 0x300) + words(0xBD00))
        self.assertIsNone(Printers(self.rom, [self.seed]).get(0x300))

    def test_local_stack_arguments_are_read_at_the_call(self):
        self.rom.write(0x300, words(0xB500, 0xB081, 0x9000)
                       + call(0x306, 0x200) + words(0xB001, 0xBD00))
        flow = trace(self.rom, 0x300, 0x30E)
        self.assertEqual(parameter(flow.calls[0].arguments[4]), 0)
        self.rom.write(0x304, words(0x9100))
        self.assertEqual(parameter(trace(self.rom, 0x300, 0x30E).calls[0].arguments[4]), 1)


if __name__ == '__main__':
    unittest.main()
