"""String field rules do not consume manual classifier answers."""

import struct
import unittest

from gen3tl.core.charmap import Charmap
from gen3tl.core.rom import ROM_BASE, Rom
from gen3tl.discover.scan import Found, Ref, pointer_index
from gen3tl.discover.string_facts import exclude_targets, include_targets, table_fields


class StringFactsTest(unittest.TestCase):
    def setUp(self):
        self.rom = Rom(b"\xff" * 2048)
        self.cm = Charmap()

    def label(self, at, pointer, text):
        self.rom.write(at, self.cm.encode(text))
        self.rom.put_u32(pointer, ROM_BASE + at)

    def includes(self, data, found=None):
        return include_targets(self.rom, data, found or {}, pointer_index(self.rom))

    def test_description_field_requires_nonblank_decodable_text(self):
        data = {"pokedex": {"items": {"base": 0x100, "stride": 16, "count": 2,
                                       "fields": {"description": {"offset": 8, "width": 4}}}}}
        self.label(0x400, 0x108, "X")
        self.label(0x420, 0x118, "   ")
        row, = self.includes(data)
        self.assertEqual((row["offset"], row["classifier"]), (0x400, "item-description-field"))
        self.assertFalse(self.includes(data, {0x400: Found(0x400, 0x402)}))

    def test_map_name_does_not_cut_a_control_operand(self):
        data = {"pokedex": {"map_sections": {"base": 0x100, "stride": 4, "count": 2}}}
        self.label(0x400, 0x100, "X")
        self.label(0x420, 0x104, "Y")
        self.rom.data[0x41F] = 0xF7
        self.assertEqual([row["offset"] for row in self.includes(data)], [0x400])
        rows = include_targets(self.rom, data, {}, pointer_index(self.rom), used_sections={1})
        self.assertEqual([row["offset"] for row in rows], [0x420])

    def test_keyboard_rows_require_discovered_count_and_text_binding(self):
        data = {"table": [{"name": "naming_keyboard", "count": 2}],
                "keyboard": {"table": [{"name": "rows", "base": 0x100, "entry": 16,
                                         "text": True, "refs": [0x80]}]}}
        self.label(0x400, 0x100, "A B C")
        self.label(0x420, 0x104, "1 2 3")
        self.assertEqual(len(self.includes(data)), 2)
        data["keyboard"]["table"][0]["text"] = False
        self.assertFalse(self.includes(data))

    def test_menu_rule_requires_alphanumeric_label_and_three_rows(self):
        for i, label in enumerate(("1F", "2F", "Exit")):
            self.label(0x400 + i * 16, 0x100 + i * 8, label)
            self.rom.put_u32(0x104 + i * 8, 0)
        self.assertEqual(len(self.includes({})), 2)
        self.rom.put_u32(0x114, 1)
        self.assertFalse(self.includes({}))

    def test_binary_rule_preserves_unverified_scan_candidates(self):
        self.rom.write(0x300, struct.pack("<HHIII", 0, 0, 11025 * 1024, 0, 128))
        self.rom.write(0x100, bytes([0, 60, 0, 0]))
        self.rom.put_u32(0x104, ROM_BASE + 0x300)
        found = {0x320: Found(0x320, 0x324, [Ref(0x40, "pool")]),
                 0x330: Found(0x330, 0x334, [Ref(0x50, "unverified")])}
        row, = exclude_targets(self.rom, {}, found, pointer_index(self.rom))
        self.assertEqual(row["offset"], 0x320)
        self.rom.put_u32(0x30C, 8)
        self.assertFalse(exclude_targets(self.rom, {}, found, pointer_index(self.rom)))

    def test_fill_requires_reader_binding_and_fill_bytes(self):
        self.rom.write(0x400, b"\xee\xdd\xff")
        data = {"keyboard": {"table": [{"name": "fill", "base": 0x400, "refs": [0x80]}]}}
        found = {0x400: Found(0x400, 0x403, [Ref(0x80, "pool")])}
        self.assertEqual(exclude_targets(self.rom, data, found, {})[0]["classifier"], "keyboard-fill-bytes")
        self.rom.data[0x400] = 0xBB
        self.assertFalse(exclude_targets(self.rom, data, found, {}))

    def test_font_allocation_requires_the_complete_candidate_span(self):
        data = {"font": [{"glyphs": 0x400, "count": 1, "format": "8x16"}]}
        found = {0x410: Found(0x410, 0x418, [Ref(0x80, "pool")])}
        self.assertEqual(exclude_targets(self.rom, data, found, {})[0]["classifier"], "font-glyph-payload")
        found[0x410].end = 0x421
        self.assertFalse(exclude_targets(self.rom, data, found, {}))

    def test_native_formatted_text_requires_a_returning_literal_reader(self):
        self.label(0x400, 0x108, "…\\nHello")
        self.rom.write(0x100, struct.pack("<3H", 0xB500, 0x4801, 0xBD00))
        self.assertEqual(self.includes({})[0]["classifier"], "native-formatted-text")
        self.rom.write(0x104, b"\xff\xff")
        self.assertFalse(self.includes({}))

    def test_word_template_requires_the_complete_command_prefix(self):
        self.label(0x400, 0x105, "{PLAYER}’s")
        self.rom.write(0x100, b"\xfa\x00\xe0\x03\x02")
        self.assertEqual(self.includes({})[0]["classifier"], "script-word-template")
        self.rom.data[0x104] = 3
        self.assertFalse(self.includes({}))

    def test_manual_answers_never_seed_an_outcome(self):
        self.label(0x400, 0x100, "X")
        data = {"strings": {"include": [0x400], "exclude": ["s0000400"],
                            "computed": [{"pointer": 0x100, "ids": ["s0000400"]}]}}
        self.assertEqual(self.includes(data), self.includes({}))
        self.assertFalse(self.includes(data))

    def test_ordinal_word_records_require_complete_permutation(self):
        for index, label in enumerate(("YES", "NO", "!", "A")):
            self.label(0x400 + index * 16, 0x100 + index * 12, label)
            self.rom.put_u32(0x104 + index * 12, index)
            self.rom.put_u32(0x108 + index * 12, 1)
        found = {0x400, 0x410}
        fields = table_fields(self.rom, found, pointer_index(self.rom))
        self.assertEqual(len(fields), 4)
        self.rom.put_u32(0x110, 0)
        self.assertFalse(table_fields(self.rom, found, pointer_index(self.rom)))

    def test_alphabet_grid_requires_case_pair_and_equal_cell_counts(self):
        for index, label in enumerate(("ABCDE", "FGHIJ", "abcde", "fghij", "01234")):
            self.label(0x400 + index * 16, 0x100 + index * 4, label)
        fields = table_fields(self.rom, set(), pointer_index(self.rom))
        self.assertEqual(len(fields), 5)
        self.rom.write(0x440, self.cm.encode("0123"))
        self.assertFalse(table_fields(self.rom, set(), pointer_index(self.rom)))

    def test_positioned_alphabet_requires_every_letter_once(self):
        for index, label in enumerate(("ABCDEF", "GHIJKL", "MNOPQRS", "TUVWXYZ")):
            self.label(0x400 + index * 32, 0x100 + index * 4, "{CLEAR 0B}" + label + ("more" if index == 0 else ""))
        fields = table_fields(self.rom, set(), pointer_index(self.rom))
        self.assertEqual(len(fields), 4)
        self.rom.write(0x460, self.cm.encode("{CLEAR 0B}TUVWXY"))
        self.assertFalse(table_fields(self.rom, set(), pointer_index(self.rom)))

    def test_template_and_comparison_labels_require_text_majority(self):
        for index, label in enumerate(("First", "Second", "{PLAYER}’s", "3 v 3")):
            self.label(0x400 + index * 32, 0x100 + index * 4, label)
        fields = table_fields(self.rom, {0x400, 0x420}, pointer_index(self.rom))
        self.assertEqual({row[1] for row in fields}, {"placeholder-template-field", "numeric-comparison-label"})
        self.assertFalse(table_fields(self.rom, {0x400}, pointer_index(self.rom)))

    def test_numbered_labels_require_constant_stride_and_successive_values(self):
        for index, label in enumerate(("L1", "L2", "L3")):
            self.label(0x400 + index * 16, 0x100 + index * 12, label)
        fields = table_fields(self.rom, set(), pointer_index(self.rom))
        self.assertEqual(len(fields), 3)
        self.rom.write(0x420, self.cm.encode("L4"))
        self.assertFalse(table_fields(self.rom, set(), pointer_index(self.rom)))
