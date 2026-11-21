"""Controls for code-selected table presentation slots."""

import unittest

from gen3tl.core.rom import Rom
from gen3tl.discover.window_readers import presentation_case
from gen3tl.discover.windows import bind
from gen3tl.families.windows import SLOT


class WindowCasesTest(unittest.TestCase):
    def setUp(self):
        self.rom = Rom(b'\0' * 256)
        self.call = {'stride': 8, 'field': 0, 'id_field': 4,
                     'header_id': 0xFFFFFFFD, 'x': {'constant': 12},
                     'header_x': {'constant': 0}}

    def test_header_and_item_follow_the_record_comparison(self):
        self.rom.put_u32(0x44, 0xFFFFFFFD)
        self.rom.put_u32(0x4C, 7)
        header = presentation_case(self.rom, self.call, 0x40)
        item = presentation_case(self.rom, self.call, 0x48)
        self.assertEqual(header['slot'], 'case:4:eq:4294967293')
        self.assertEqual(item['slot'], 'case:4:ne:4294967293')
        self.assertEqual(header['x'], {'constant': 0})
        self.assertEqual(item['x'], {'constant': 12})

    def test_missing_contract_or_outside_field_has_no_case(self):
        for call, at in (({'x': {}}, 0x40), (dict(self.call, id_field=8), 0x40),
                         (self.call, 0xFE)):
            self.assertIsNone(presentation_case(self.rom, call, at))

    def test_only_table_slots_accept_a_presentation_case(self):
        self.assertIsNotNone(SLOT.fullmatch('call:20:r0:table:case:4:eq:4294967293'))
        self.assertIsNotNone(SLOT.fullmatch('call:20:r0:table:case:4:ne:4294967293'))
        for slot in ('call:20:r0:case:4:eq:3', 'call:20:r0:table:case:4:gt:3',
                     'call:20:r0:table:case:-4:eq:3'):
            self.assertIsNone(SLOT.fullmatch(slot))

    def test_qualified_bindings_keep_both_presentations(self):
        rows = [{'function': 'f', 'slot': 'call:20:r0:table:case:4:' + op + ':3',
                 'name': name, 'width': width}
                for op, name, width in [('eq', 'header', 160), ('ne', 'item', 148)]]
        audit = {'strings': [{'id': row['name'], 'consumers': [
            {'function': 'f', 'slot': 'call:20:r0:table'},
            {'function': 'f', 'slot': row['slot']}]} for row in rows]}
        bindings, unresolved = bind(audit, rows)
        self.assertEqual([b['window']['width'] for b in bindings], [160, 148])
        self.assertFalse(unresolved)


if __name__ == '__main__':
    unittest.main()
