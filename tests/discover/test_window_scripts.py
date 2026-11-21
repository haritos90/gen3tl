"""Script word-reader and native printer continuation controls."""

import unittest
from unittest.mock import patch

from gen3tl.core.charmap import Charmap
from gen3tl.core.rom import ROM_BASE, Rom
from gen3tl.discover.window_consumers import Consumers, signature
from gen3tl.discover.windows import discover
from gen3tl.families.windows import Windows
from test_window_consumers import call, words


class WindowScriptsTest(unittest.TestCase):
    def setUp(self):
        self.rom = Rom(b'\xff' * 4096)
        self.rom.write(0x200, words(0xB510, 0x1C03, 0x689A, 0x7810, 0x7851, 0x0209,
                                   0x4308, 0x7891, 0x0409, 0x4308, 0x78D1, 0x0609,
                                   0x4308, 0x3204, 0x609A, 0xBD10))
        self.rom.write(0x300, words(0xB500, 0x6002, 0xBD00))
        f, branches = signature(self.rom, 0x300)
        self.seed = dict(f.document(), branches=branches, text_argument=2, window_argument=0)
        self.rom.write(0x100, words(0xB500) + call(0x102, 0x200) + words(0x1C02, 0x2003)
                       + call(0x10A, 0x300) + words(0xBD00))
        self.rom.put_u32(0x80, ROM_BASE + 0x101)
        self.script = {'command_table': 0x80, 'command_table_end': 0x84}
        self.rom.write(0x400, bytes([0]))
        self.rom.put_u32(0x401, ROM_BASE + 0x700)
        self.rom.write(0x700, Charmap().encode('Sample'))
        self.proof = {'command': 0x400, 'field': 1, 'opcode': 0, 'length': 5, 'roots': [0x400]}
        self.reference = {'at': 0x401, 'kind': 'script', 'status': 'reference', 'evidence': [self.proof]}
        self.string = {'id': 's0000700', 'offset': 0x700, 'references': [self.reference]}

    def consumers(self):
        return Consumers(self.rom, [self.seed], script=self.script)

    def test_complete_word_reader_reaches_a_fingerprinted_printer(self):
        row, = self.consumers().audit([self.string])['strings'][0]['consumers']
        self.assertEqual((row['kind'], row['slot']), ('script', 'call:10:r2'))
        self.assertEqual((row['evidence']['read'], row['evidence']['call']), (0x102, 0x10A))

    def test_invalid_word_reader_does_not_supply_a_text_value(self):
        for at, replacement in ((0x21A, 0x3203), (0x216, 0x0409), (0x21C, 0x60DA)):
            with self.subTest(at=at):
                original = self.rom.read(at, 2)
                self.rom.write(at, words(replacement))
                self.assertEqual(self.consumers().audit([self.string])['resolved'], 0)
                self.rom.write(at, original)

    def test_command_boundary_field_length_and_pointer_are_required(self):
        for key, value in (('command', 0x402), ('field', 2), ('length', 6), ('opcode', 1)):
            with self.subTest(key=key):
                original = self.proof[key]
                self.proof[key] = value
                self.assertEqual(self.consumers().audit([self.string])['resolved'], 0)
                self.proof[key] = original
        self.rom.put_u32(0x401, ROM_BASE + 0x702)
        self.assertEqual(self.consumers().audit([self.string])['resolved'], 0)

    def test_stored_operand_remains_unresolved_with_its_instruction(self):
        self.rom.write(0x106, words(0x6020, 0xBD00))
        result = self.consumers().audit([self.string])
        self.assertEqual(result['resolved'], 0)
        stop = next(s for s in result['strings'][0]['unresolved'][0]['stops'] if s['kind'] == 'stored-operand')
        self.assertEqual(stop['site'], 0x106)

    def test_overwritten_result_and_unmatched_printer_are_rejected(self):
        self.rom.write(0x106, words(0x2200))
        self.assertEqual(self.consumers().audit([self.string])['resolved'], 0)
        self.rom.write(0x106, words(0x1C02))
        self.rom.write(0x302, words(0x6001))
        self.assertEqual(self.consumers().audit([self.string])['resolved'], 0)

    def test_window_generation_joins_only_the_declared_consumer_slot(self):
        f, branches = signature(self.rom, 0x100)
        function = dict(f.document(), branches=branches)
        row = {'name': 'message', 'function': f.sha256, 'slot': 'call:10:r2',
               'width': 72, 'evidence': 'The printer uses the measured message window.'}
        knowledge = Windows([row], [function], [self.seed])
        with patch('gen3tl.discover.references.script_operands', return_value=({0x401: self.proof}, [])):
            result = discover(self.rom, knowledge, profile={'script': self.script})
        self.assertEqual(result.data['layout']['window_width'], {'s0000700': 72})
        row['slot'] = 'call:2:r2'
        with patch('gen3tl.discover.references.script_operands', return_value=({0x401: self.proof}, [])):
            result = discover(self.rom, knowledge, profile={'script': self.script})
        self.assertNotIn('layout', result.data)


if __name__ == '__main__':
    unittest.main()
