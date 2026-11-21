"""Positive and negative controls for generic menu reader summaries."""

import copy
import unittest
from unittest.mock import patch

from gen3tl.core.rom import ROM_BASE, Rom
from gen3tl.discover.window_consumers import Consumers, signature
from gen3tl.discover.table_windows import discover
from gen3tl.families.windows import Windows
from gen3tl.discover.window_printers import Printers
from gen3tl.discover.window_readers import Readers, substitute
from test_window_consumers import call, words


class TableReaderSummariesTest(unittest.TestCase):
    def setUp(self):
        self.rom = Rom(b'\xff' * 4096)
        self.rom.write(0x200, words(0xB500, 0x6002, 0xBD00))
        f, b = signature(self.rom, 0x200)
        self.seed = dict(f.document(), branches=b, text_argument=2, window_argument=0)
        self.rom.write(0x300, words(0xB570, 0xB083, 0x9E07, 0x9D08, 0x2400, 0x42B4,
                                  0xD207, 0x00E0, 0x1940, 0x6802)
                       + call(0x314, 0x200) + words(0x3401, 0x42B4, 0xD3F7, 0xB003, 0xBD70))
        self.rom.write(0x100, words(0xB500, 0xB082, 0x480E, 0x9001, 0x2003, 0x9000)
                       + call(0x10C, 0x300) + words(0xB002, 0xBD00))
        self.rom.put_u32(0x140, ROM_BASE + 0x700)
        for i in range(3):
            self.rom.put_u32(0x700 + i * 8, ROM_BASE + 0x800 + i * 4)
            self.rom.write(0x800 + i * 4, bytes([0xBB + i, 0xFF]))

    def readers(self, templates=()):
        result = Readers(self.rom, Printers(self.rom, [self.seed]), templates)
        result.derive()
        return result

    def test_loop_stride_count_and_caller_table_are_derived(self):
        result = self.readers()
        rule, = result.rules[0x300]
        self.assertEqual((rule['base'], rule['stride'], rule['field']), ({'parameter': 5}, 8, 0))
        row, = result.calls
        self.assertEqual((row['base'], row['count']), ({'constant': ROM_BASE + 0x700}, {'constant': 3}))
        consumers = Consumers(self.rom, [self.seed])
        linked = consumers.reader_bindings()
        self.assertEqual(set(linked), {0x700, 0x708, 0x710})
        self.assertEqual(linked[0x700][0][1]['slot'], 'call:12:s1:table')

    def test_nonlinear_index_and_nonunit_increment_are_rejected(self):
        self.rom.write(0x30E, words(0x4360))
        self.assertFalse(self.readers().calls)
        self.setUp()
        self.rom.write(0x318, words(0x3402))
        self.assertFalse(self.readers().calls)

    def test_index_candidate_requires_the_effective_call_target(self):
        self.rom.write(0x10C, call(0x10C, 0x600))
        with patch('gen3tl.discover.window_readers.call_index', return_value={0x300: [0x10C]}):
            self.assertFalse(self.readers().calls)

    def test_wrong_pointer_field_or_invalid_text_rejects_the_entire_table(self):
        self.rom.put_u32(0x708, 0)
        self.assertFalse(Consumers(self.rom, [self.seed]).reader_bindings())
        self.setUp()
        self.rom.write(0x804, b'\xff')
        self.assertFalse(Consumers(self.rom, [self.seed]).reader_bindings())

    def test_template_contract_checks_constructor_and_renderer_calls(self):
        self.rom.write(0x500, words(0xB500, 0xB082, 0x6801, 0x9101, 0x8881, 0x9100, 0x7980)
                       + call(0x50E, 0x300) + words(0xB002, 0xBD00))
        self.rom.write(0x100, words(0xB500, 0x480F) + call(0x104, 0x500) + words(0xBD00))
        self.rom.put_u32(0x140, ROM_BASE + 0x900)
        self.rom.put_u32(0x900, ROM_BASE + 0x700)
        self.rom.write(0x904, bytes([3, 0, 4, 8]))
        f, b = signature(self.rom, 0x500)
        rf, rb = signature(self.rom, 0x300)
        row = dict(f.document(), name='template', branches=b, reader_call=14,
                   reader=dict(rf.document(), branches=rb), render_call=20, render_argument=2,
                   template_argument=0, items_field=0, count_field=4, count_width=2,
                   window_field=6, x_field=7, stride=8, text_field=0, text_read=18,
                   evidence='The template supplies item pointers, count and window.')
        result = self.readers([row])
        calls = [call for call in result.calls if 'template_evidence' in call]
        self.assertTrue(calls)
        self.assertEqual((calls[0]['count'], calls[0]['window']), ({'constant': 3}, {'constant': 4}))
        row.update(header_x_field=8, id_field=4, header_id=0xFFFFFFFD)
        self.rom.write(0x908, bytes([12]))
        self.rom.put_u32(0x70C, 0xFFFFFFFD)
        bindings = Consumers(self.rom, [self.seed], [row]).reader_bindings()
        self.assertTrue(any(consumer['evidence']['x'] == {'constant': 12} for _, consumer in bindings[0x708]))
        self.assertTrue(any(consumer['evidence']['x'] == {'constant': 8} for _, consumer in bindings[0x700]))
        changed = copy.deepcopy(row)
        changed['reader']['sha256'] = '0' * 64
        self.assertFalse(any('template_evidence' in rule for rule in self.readers([changed]).rules.get(0x500, [])))
        self.rom.write(0x314, call(0x314, 0x600))
        self.assertFalse(any('template_evidence' in rule for rule in self.readers([row]).rules.get(0x500, [])))

    def test_counted_table_window_keeps_measures_and_rejects_bad_extent(self):
        f, b = signature(self.rom, 0x100)
        function = dict(f.document(), branches=b)
        rule = {'function': f.sha256, 'slot': 'call:12:s1:table', 'extent': 'reader-count',
                'name': 'menu', 'width': 88, 'where': 'Menu choices.'}
        knowledge = Windows([], [function], [self.seed], table_rows=[rule])
        result = discover(self.rom, knowledge)
        row, = result.data['layout']['table_window']
        self.assertEqual((row['base'], row['count'], row['stride'], row['width']), (0x700, 3, 8, 88))
        self.rom.write(0x108, words(0x2004))
        result = discover(self.rom, knowledge)
        self.assertFalse(result.data)
        self.assertTrue(result.metadata['unresolved'])

    def test_copied_stack_template_preserves_packed_fields_and_invalidates_on_escape(self):
        self.rom.write(0x500, words(0xB500, 0xB082, 0x6801, 0x9101, 0x8881, 0x9100, 0x7980)
                       + call(0x50E, 0x300) + words(0xB002, 0xBD00))
        self.rom.write(0x100, words(0xB530, 0xB084, 0x4C0E, 0x466D, 0xCC03, 0xC503, 0x4668)
                       + call(0x10E, 0x500) + words(0xB004, 0xBD30))
        self.rom.put_u32(0x140, ROM_BASE + 0x900)
        self.rom.put_u32(0x900, ROM_BASE + 0x700)
        self.rom.write(0x904, bytes([3, 0, 4, 8]))
        f, b = signature(self.rom, 0x500)
        rf, rb = signature(self.rom, 0x300)
        row = dict(f.document(), name='template', branches=b, reader_call=14,
                   reader=dict(rf.document(), branches=rb), render_call=20, render_argument=2,
                   template_argument=0, items_field=0, count_field=4, count_width=2,
                   window_field=6, x_field=7, stride=8, text_field=0, text_read=18,
                   evidence='The template supplies item pointers, count and window.')
        result = self.readers([row])
        found = [call for call in result.calls if call['caller'] == 0x100]
        self.assertTrue(found)
        self.assertEqual((found[0]['count'], found[0]['window']), ({'constant': 3}, {'constant': 4}))
        self.rom.write(0x10E, call(0x10E, 0x600) + words(0x4668)
                       + call(0x114, 0x500) + words(0xB004, 0xBD30))
        self.assertFalse([call for call in self.readers([row]).calls if call['caller'] == 0x100])

    def test_record_projection_reads_its_own_rom_fields(self):
        expr = {'load': 2, 'address': {'sum': [{'constant': 4}, {'scale': 1, 'value': {'parameter': 0}}]}}
        self.rom.write(0x904, bytes([3, 0]))
        self.assertEqual(substitute(self.rom, expr, [{'constant': ROM_BASE + 0x900}]), {'constant': 3})
        self.assertIn('load', substitute(self.rom, expr, [{'constant': 0x02000000}]))
        self.assertIn('load', substitute(self.rom, expr, [{'constant': ROM_BASE + 0x901}]))

    def test_relocated_table_retains_the_consumer_slot(self):
        first = Consumers(self.rom, [self.seed]).reader_bindings()[0x700][0][1]
        self.rom.write(0x780, self.rom.data[0x700:0x718])
        self.rom.put_u32(0x140, ROM_BASE + 0x780)
        later = Consumers(self.rom, [self.seed]).reader_bindings()[0x780][0][1]
        self.assertEqual((first['function'], first['slot']), (later['function'], later['slot']))
        self.assertEqual(later['evidence']['base'], 0x780)


if __name__ == '__main__':
    unittest.main()
