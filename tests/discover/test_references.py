"""Positive and negative controls for reference evidence."""

import struct
import unittest
from unittest.mock import patch

from gen3tl.core.charmap import Charmap
from gen3tl.core.graphics import lz77_compress
from gen3tl.core.rom import ROM_BASE, Rom
from gen3tl.discover.references import (Spans, audit, binary_spans, classify, instruction_word,
                                      compressed_sequence, inventory_classifications, literal_word, overlapping_words,
                                      text_record, text_tables, trampoline_instructions, unused_literal)
from gen3tl.discover.scan import pointer_index


class ReferencesTest(unittest.TestCase):
    def setUp(self):
        self.rom = Rom(b"\xff" * 2048)

    def test_compressed_payload_requires_complete_referenced_block(self):
        blob = lz77_compress(b"abcd" * 16)
        self.rom.write(0x100, blob)
        self.rom.put_u32(0x40, ROM_BASE + 0x100)
        rows = binary_spans(self.rom, pointer_index(self.rom))
        self.assertEqual(rows[0]["end"], 0x100 + len(blob))
        self.rom.write(0x104, b"\xff\xff\xff")
        self.assertEqual(binary_spans(self.rom, pointer_index(self.rom)), [])

    def test_wave_bounds_require_tone_record_and_valid_loop(self):
        self.rom.write(0x100, struct.pack("<HHIII", 0, 0x4000, 11025 * 1024, 8, 128))
        self.rom.write(0x40, bytes([0, 60, 0, 0xDA]))
        self.rom.put_u32(0x44, ROM_BASE + 0x100)
        row, = binary_spans(self.rom, pointer_index(self.rom))
        self.assertEqual((row["kind"], row["start"], row["end"]), ("pcm", 0x110, 0x190))
        self.rom.put_u32(0x108, 128)
        self.assertFalse(binary_spans(self.rom, pointer_index(self.rom)))

    def test_compressed_wave_bounds_exclude_partial_block(self):
        self.rom.write(0x100, struct.pack("<HHIII", 1, 0, 11025 * 1024, 0, 130))
        self.rom.write(0x40, bytes([32, 60, 0, 0]))
        self.rom.put_u32(0x44, ROM_BASE + 0x100)
        row, = binary_spans(self.rom, pointer_index(self.rom))
        self.assertEqual((row["kind"], row["end"]), ("dpcm", 0x110 + 66))
        self.assertFalse(Spans([row]).containing(row["end"] - 3))

    def test_instructions_require_returning_control_flow(self):
        self.rom.write(0x100, struct.pack("<4H", 0xB500, 0x7800, 0x0844, 0xBD00))
        self.assertEqual(instruction_word(self.rom, 0x102)["function"], 0x100)
        other = Rom(bytes(self.rom.data))
        other.write(0x106, b"\xff\xff")
        self.assertIsNone(instruction_word(other, 0x102))

    def test_pool_requires_loaded_word_outside_instructions(self):
        self.rom.write(0x100, struct.pack("<3H", 0xB500, 0x4801, 0xBD00))
        self.rom.put_u32(0x108, ROM_BASE + 0x400)
        self.assertEqual(literal_word(self.rom, 0x108)["loads"][0]["load"], 0x102)
        self.assertIsNone(instruction_word(self.rom, 0x108))
        self.assertIsNone(literal_word(self.rom, 0x10C))

    def test_unused_pool_requires_skipped_siblings_without_readers(self):
        self.rom.write(0x100, struct.pack("<4H", 0x4803, 0x4904, 0xE00C, 0x46C0))
        self.rom.put_u32(0x10C, ROM_BASE + 0x400)
        self.assertEqual(unused_literal(self.rom, 0x10C, {})["branch"], 0x104)
        self.assertIsNone(unused_literal(self.rom, 0x10C, {0x10C: [0x40]}))
        for instruction in (0x4803, 0xA003):
            self.rom.write(0xFE, struct.pack("<H", instruction))
            self.assertIsNone(unused_literal(self.rom, 0x10C, {}))
        self.rom.write(0xFE, b"\xff\xff")
        self.rom.put_u32(0xE0, 0xE59F0024)
        self.assertIsNone(unused_literal(self.rom, 0x10C, {}))
        self.rom.put_u32(0xE0, 0xFFFFFFFF)
        self.rom.write(0x104, b"\xc0\x46")
        self.assertIsNone(unused_literal(self.rom, 0x10C, {}))

    def test_unreferenced_compressed_block_requires_complete_sequence(self):
        start = 0x100
        for _ in range(3):
            blob = lz77_compress(bytes(range(64)))
            self.rom.write(start, blob)
            start = (start + len(blob) + 3) & ~3
        self.assertEqual(compressed_sequence(self.rom, 0x110)["start"], 0x100)
        self.rom.write(start - len(blob) + 4, b"\xff\xff\xff")
        self.assertIsNone(compressed_sequence(self.rom, 0x110))

    def test_instruction_hook_requires_register_match_and_return(self):
        self.rom.write(0x40, struct.pack("<2H", 0x4800, 0x4700))
        self.rom.put_u32(0x44, ROM_BASE + 0x101)
        self.rom.write(0x100, struct.pack("<4H", 0x7800, 0x0844, 0x4800, 0x4700))
        self.rom.put_u32(0x108, ROM_BASE + 0x201)
        self.rom.write(0x200, struct.pack("<2H", 0xBC02, 0x4708))
        self.assertEqual(trampoline_instructions(self.rom, 0x100, pointer_index(self.rom))["returns"], [0x202])
        self.rom.write(0x200, b"\xff\xff")
        self.assertIsNone(trampoline_instructions(self.rom, 0x100, pointer_index(self.rom)))
        self.rom.write(0x200, struct.pack("<2H", 0xBC02, 0x4708))
        self.rom.write(0x42, struct.pack("<H", 0x4708))
        self.assertIsNone(trampoline_instructions(self.rom, 0x100, pointer_index(self.rom)))

    def test_unaligned_window_spans_distinct_pointer_fields(self):
        for at in (0x100, 0x104, 0x108):
            self.rom.put_u32(at, ROM_BASE + 0x300)
        self.assertEqual(overlapping_words(self.rom, 0x103)["stride"], 4)
        self.assertIsNone(overlapping_words(self.rom, 0x104))
        self.rom.put_u32(0x104, 0)
        self.assertIsNone(overlapping_words(self.rom, 0x103))

    def test_short_menu_labels_require_sibling_records(self):
        for index, label in enumerate(("1F", "2F", "Exit")):
            self.rom.write(0x300 + index * 16, Charmap().encode(label))
            self.rom.put_u32(0x100 + index * 8, ROM_BASE + 0x300 + index * 16)
            self.rom.put_u32(0x104 + index * 8, 0)
        self.assertEqual(text_record(self.rom, 0x108)["stride"], 8)
        self.rom.put_u32(0x104, 1)
        self.assertIsNone(text_record(self.rom, 0x108))

    def test_strided_field_requires_three_text_targets(self):
        index = {0x400: [0x100, 0x10C, 0x118], 0x500: [0x200, 0x20C]}
        row, = text_tables(index, {0x400, 0x500})
        self.assertEqual((row["start"], row["stride"], row["rows"]), (0x100, 12, 3))
        self.assertFalse(text_tables(index, {0x500}))

    def test_script_operand_is_not_arbitrary_command_membership(self):
        kwargs = dict(binary=Spans([]), text=Spans([]), operands={0x101: {"opcode": 0x67}},
                      commands=Spans([{"start": 0x100, "end": 0x109}]))
        self.assertEqual(classify(self.rom, 0x101, **kwargs)["status"], "reference")
        self.assertEqual(classify(self.rom, 0x102, **kwargs)["status"], "unclassified")

    def test_overlapping_intervals_keep_all_containing_evidence(self):
        spans = Spans([{"start": 10, "end": 100}, {"start": 20, "end": 30}])
        self.assertEqual(len(spans.containing(25)), 2)
        self.assertEqual(len(spans.containing(50)), 1)
        self.assertFalse(spans.containing(99))

    def test_audit_requires_independent_evidence_before_relocation(self):
        self.rom.put_u32(0x101, ROM_BASE + 0x400)
        self.rom.write(0x400, Charmap().encode("Hello!"))
        inventory = {"rom_sha1": self.rom.sha1(), "entries": [
            {"id": "label", "offset": 0x400, "length": 7,
             "refs": [{"at": 0x101, "kind": "script:message"}]}]}
        for operands, expected in (({}, "in-place-only"), ({0x101: {"opcode": 0x67}}, "classified")):
            with patch("gen3tl.discover.references.script_operands", return_value=(operands, [])):
                result = audit(self.rom, inventory, {})
            row, = result["strings"]
            self.assertEqual(row["relocation"], expected)
            self.assertEqual(row["pointer_index_count"], 1)
            self.assertEqual(row["count_equal"], True)
            self.assertEqual(row["unclassified"], [0x101] if not operands else [])

    def test_inventory_duplicate_position_keeps_verified_evidence(self):
        refs = [{"at": 0x101, "kind": "unverified"}, {"at": 0x101, "kind": "table:names"}]
        entry = {"id": "label", "offset": 0x400, "length": 7, "refs": refs}
        inventory_classifications(self.rom, {"entries": [entry]}, {}, {})
        self.assertEqual(entry["refs"], refs)
        self.assertEqual(entry["relocation"], {"status": "classified", "unclassified": []})
        fact, = entry["reference_classification"]
        self.assertEqual((fact["at"], fact["status"], fact["kind"]), (0x101, "reference", "table:names"))
