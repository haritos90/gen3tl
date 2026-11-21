from test_roots import TOOLKIT_ROOT
"""Scripts handlers checks and fixtures."""

import sys
from pathlib import Path
import struct
import unittest

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.core.rom import ROM_BASE
from gen3tl.discover import scripts
from gen3tl.discover.accessors import analyze, script_reader
from gen3tl.families.knowledge import load_script
from scripts_support import words


def call(at, target):
    offset = (target - at - 4) & 0x7FFFFF
    return words(0xF000 | offset >> 12, 0xF800 | (offset >> 1 & 0x7FF))


def halfword_reader():
    return words(0x1C03, 0x689A, 0x7810, 0x3201, 0x609A, 0x7811,
                 0x0209, 0x4308, 0x3201, 0x609A, 0x4770)


class HandlerTest(unittest.TestCase):
    def setUp(self):
        self.data = bytearray(0x400)
        self.data[0x40:0x40 + 22] = halfword_reader()
        self.reader = script_reader(analyze(self.data, 0x40, 0x56))
        self.readers = {0x40: self.reader}

    def test_readers_and_wrappers_require_complete_shapes(self):
        byte_reader = words(0x6881, 0x1C02, 0x7808, 0x3101, 0x6091, 0x4770)
        self.data[0x80:0x8C] = byte_reader
        self.data[0x100:0x108] = words(0xB500) + call(0x102, 0x40) + words(0xBD00)
        for target, width in ((0x40, 2), (0x80, 1), (0x100, 2)):
            with self.subTest(target=target):
                reader = scripts._reader_shape(self.data, target)
                self.assertIsNotNone(reader)
                self.assertEqual(reader.width, width)
        self.assertIsNone(scripts._reader_shape(self.data, 0x40, max_span=20))
        self.assertIsNone(scripts._reader_shape(self.data, 0x40, max_states=1))
        self.assertIsNone(scripts._reader_shape(self.data, 0x100, max_depth=0))
        self.assertIsNone(scripts._reader_shape(self.data, 0x100, max_states=13))
        for tail in (words(0x2000, 0xBD00), words(0x6008, 0xBD00),
                     call(0x106, 0x300) + words(0xBD00)):
            with self.subTest(tail=tail.hex()):
                body = words(0xB500) + call(0x102, 0x40) + tail
                self.data[0x100:0x100 + len(body)] = body
                self.assertIsNone(scripts._reader_shape(self.data, 0x100))
        self.data[0x40 + 14:0x40 + 16] = words(0x4301)
        self.assertIsNone(scripts._reader_shape(self.data, 0x40))

    def test_reader_wrappers_are_discovered_without_addresses(self):
        self.data[0x200:0x208] = words(0xB500) + call(0x202, 0x40) + words(0xBD00)
        self.data[0x100:0x108] = words(0xB500) + call(0x102, 0x200) + words(0xBD00)
        handler = scripts.infer_handler(self.data, 0, 0x100, {})
        self.assertEqual((handler.status, handler.lengths), ("fixed", (3,)))
        self.assertEqual([(read.offset, read.width) for read in handler.reads], [(0, 2)])
        self.data[0x206:0x20A] = words(0x6008, 0xBD00)
        handler = scripts.infer_handler(self.data, 0, 0x100, {})
        self.assertEqual((handler.status, handler.lengths), ("shape", (3,)))
        self.assertTrue(handler.assumptions)

    def test_reader_calls_invalidate_released_stack_slots(self):
        reader = words(0xB510, 0x6881, 0x1C02, 0x7808, 0x3101, 0x6091, 0xBD10)
        self.data[0x300:0x300 + len(reader)] = reader
        self.data[0x200:0x208] = words(0xB500) + call(0x202, 0x300) + words(0xBD00)
        self.assertEqual(scripts._reader_shape(self.data, 0x200).width, 1)
        wrapper = words(0xB500, 0xB081, 0x4671, 0x9100, 0xB001)
        wrapper += call(0x20A, 0x300) + words(0xB081, 0xBD00)
        self.data[0x200:0x200 + len(wrapper)] = wrapper
        self.assertIsNone(scripts._reader_shape(self.data, 0x200))
        self.data[0x100:0x108] = words(0xB500) + call(0x102, 0x200) + words(0xBD00)
        handler = scripts.infer_handler(self.data, 0, 0x100, {})
        self.assertEqual(handler.status, "unresolved")

    def test_word_reader_proves_value_and_advance_together(self):
        body = words(0xB510, 0x1C03, 0x689A, 0x7810, 0x7851, 0x0209,
                     0x4308, 0x7891, 0x0409, 0x4308, 0x78D1, 0x0609,
                     0x4308, 0x3204, 0x609A, 0xBD10)
        self.data[0x200:0x200 + len(body)] = body
        self.data[0x100:0x108] = words(0xB500) + call(0x102, 0x200) + words(0xBD00)
        reader = scripts._reader_shape(self.data, 0x200)
        self.assertEqual(reader.width, 4)
        handler = scripts.infer_handler(self.data, 0, 0x100, {})
        self.assertEqual((handler.status, handler.lengths), ("fixed", (5,)))
        self.assertEqual([(r.offset, r.width) for r in handler.reads], [(0, 4)])
        for offset, replacement in ((26, 0x3203), (22, 0x0409), (28, 0x60DA)):
            with self.subTest(offset=offset):
                self.data[0x200:0x200 + len(body)] = body
                self.data[0x200 + offset:0x202 + offset] = words(replacement)
                self.assertIsNone(scripts._reader_shape(self.data, 0x200))

    def test_reader_stack_writes_cannot_escape_its_frame(self):
        body = words(0xB402, 0x6881, 0x1C02, 0x7808, 0x3101, 0x6091, 0xBC02, 0x4770)
        self.data[0x200:0x200 + len(body)] = body
        self.assertEqual(scripts._reader_shape(self.data, 0x200).width, 1)
        body = words(0xB001, 0xB402, 0x6881, 0x1C02, 0x7808, 0x3101,
                     0x6091, 0xBC02, 0xB081, 0x4770)
        self.data[0x200:0x200 + len(body)] = body
        self.assertIsNone(scripts._reader_shape(self.data, 0x200))
        self.data[0x100:0x108] = words(0xB500) + call(0x102, 0x200) + words(0xBD00)
        handler = scripts.infer_handler(self.data, 0, 0x100, {})
        self.assertEqual(handler.status, "unresolved")

    def test_readonly_summary_abstracts_data_but_checks_all_stores(self):
        self.data[0x100:0x10A] = words(0xB510, 0x1C04) + call(0x104, 0x200) + words(0xBD10)
        body = words(0x0049, 0x2900, 0xD1FC, 0x4770)
        self.data[0x200:0x208] = body
        handler = scripts.infer_handler(self.data, 0, 0x100, {}, max_helper_states=40)
        self.assertEqual((handler.status, handler.lengths), ("fixed", (1,)))
        for store in (0x6081, 0x6001):
            with self.subTest(store=store):
                self.data[0x206:0x20A] = words(store, 0x4770)
                handler = scripts.infer_handler(self.data, 0, 0x100, {}, max_helper_states=40)
                self.assertEqual(handler.status, "unresolved")
        self.data[0x200:0x208] = body
        handler = scripts.infer_handler(self.data, 0, 0x100, {}, max_helper_states=1)
        self.assertEqual(handler.status, "unresolved")
        self.assertTrue(any("state limit" in reason for reason in handler.reasons))

    def test_context_copy_to_a_callee_does_not_hide_writes(self):
        self.data[0x100:0x108] = words(0xB500) + call(0x102, 0x200) + words(0xBD00)
        body = words(0xB500, 0x1C02, 0x3208, 0x2000) + call(0x208, 0x300) + words(0xBD00)
        self.data[0x200:0x200 + len(body)] = body
        self.data[0x300:0x302] = words(0x4770)
        handler = scripts.infer_handler(self.data, 0, 0x100, {})
        self.assertEqual((handler.status, handler.lengths), ("fixed", (1,)))
        self.data[0x300:0x304] = words(0x6011, 0x4770)
        handler = scripts.infer_handler(self.data, 0, 0x100, {})
        self.assertEqual(handler.status, "unresolved")

    def test_helper_returns_to_its_own_caller(self):
        self.data[0x100:0x108] = words(0xB500) + call(0x102, 0x200) + words(0xBD00)
        self.data[0x200:0x208] = words(0xB500) + call(0x202, 0x300) + words(0xBD00)
        self.data[0x300:0x302] = words(0x4770)
        handler = scripts.infer_handler(self.data, 0, 0x100, {})
        self.assertEqual((handler.status, handler.lengths), ("fixed", (1,)))
        self.data[0x200:0x206] = call(0x200, 0x300) + words(0x4770)
        handler = scripts.infer_handler(self.data, 0, 0x100, {})
        self.assertEqual(handler.status, "unresolved")

    def test_callee_paths_must_agree_and_indirect_calls_remain_unknown(self):
        self.data[0x100:0x108] = words(0xB500) + call(0x102, 0x200) + words(0xBD00)
        body = words(0x6881, 0xD001, 0x3102, 0xE000, 0x3102, 0x6081, 0x4770)
        self.data[0x200:0x200 + len(body)] = body
        handler = scripts.infer_handler(self.data, 0, 0x100, {})
        self.assertEqual((handler.status, handler.lengths), ("fixed", (3,)))
        self.data[0x208:0x20A] = words(0x3104)
        handler = scripts.infer_handler(self.data, 0, 0x100, {})
        self.assertEqual((handler.status, handler.lengths), ("variable", (3, 5)))
        self.data[0x200:0x202] = words(0x4718)
        handler = scripts.infer_handler(self.data, 0, 0x100, {})
        self.assertEqual(handler.status, "unresolved")
        self.assertTrue(any("indirect branch" in reason for reason in handler.reasons))

    def test_direct_read_and_skip_only_advance(self):
        self.data[0x100:0x108] = words(0xB500) + call(0x102, 0x40) + words(0xBD00)
        handler = scripts.infer_handler(self.data, 0, 0x100, self.readers)
        self.assertEqual((handler.status, handler.lengths), ("fixed", (3,)))
        self.assertEqual([(read.offset, read.width) for read in handler.reads], [(0, 2)])
        self.data[0x100:0x10A] = words(0x6881, 0x3104, 0x6081, 0x2000, 0x4770)
        handler = scripts.infer_handler(self.data, 1, 0x100, self.readers)
        self.assertEqual((handler.status, handler.lengths, handler.reads), ("fixed", (5,), ()))

    def test_branch_lengths_and_indirect_control_stay_explicit(self):
        self.data[0x100:0x10C] = (words(0xD003, 0xB500) + call(0x104, 0x40)
                                  + words(0xBD00, 0x4770))
        handler = scripts.infer_handler(self.data, 0, 0x100, self.readers)
        self.assertEqual((handler.status, handler.lengths), ("variable", (1, 3)))
        self.data[0x100:0x102] = words(0x4708)
        handler = scripts.infer_handler(self.data, 1, 0x100, self.readers)
        self.assertEqual(handler.status, "unresolved")
        self.assertIn("indirect branch", handler.reasons[0])

    def test_bounded_helper_effects_and_opaque_calls(self):
        self.data[0x100:0x10A] = words(0xB510, 0x1C04) + call(0x104, 0x200) + words(0xBD10)
        for helper, length in ((words(0x6881, 0x3104, 0x6081, 0x4770), 5),
                               (words(0x4770), 1)):
            with self.subTest(helper=helper.hex()):
                self.data[0x200:0x208] = helper.ljust(8, b"\0")
                handler = scripts.infer_handler(self.data, 0, 0x100, self.readers)
                self.assertEqual((handler.status, handler.lengths), ("fixed", (length,)))
        self.data[0x200:0x208] = words(0xDF00, 0x4770).ljust(8, b"\0")
        handler = scripts.infer_handler(self.data, 0, 0x100, self.readers)
        self.assertEqual((handler.status, handler.lengths), ("unresolved", ()))
        self.assertTrue(any("unresolved software call" in reason for reason in handler.reasons))

    def test_store_ranges_and_unknown_aliases_are_conservative(self):
        bodies = (
            words(0x1842, 0x320C, 0x6013, 0x4770),
            words(0x7809, 0x1842, 0x6013, 0x4770),
            words(0x7809, 0x1842, 0x3208, 0x6881, 0x3104, 0x6011, 0x4770),
            words(0x6088, 0x4770),
            words(0xB500, 0x6008, 0xBD00),
            words(0x7809, 0x07C9, 0x1842, 0x3264, 0x6013, 0x4770),
        )
        for body in bodies:
            with self.subTest(body=body.hex()):
                self.data[0x100:0x100 + len(body)] = body
                handler = scripts.infer_handler(self.data, 0, 0x100, self.readers)
                expected = ("shape", (1,)) if body in (words(0x6088, 0x4770),
                    words(0xB500, 0x6008, 0xBD00)) else ("unresolved", ())
                self.assertEqual((handler.status, handler.lengths), expected)
                if handler.status == "shape":
                    self.assertTrue(handler.assumptions)
        for body in (words(0x1C02, 0x320C, 0x6013, 0x2000, 0x4770),
                     words(0x7809, 0x1842, 0x3264, 0x6013, 0x2000, 0x4770)):
            with self.subTest(body=body.hex()):
                self.data[0x100:0x100 + len(body)] = body
                handler = scripts.infer_handler(self.data, 0, 0x100, self.readers)
                self.assertEqual((handler.status, handler.lengths), ("fixed", (1,)))

    def test_opaque_pointer_writes_invalidate_stream(self):
        for instruction in (0x6082, 0x8102, 0xC00E):
            with self.subTest(instruction=hex(instruction)):
                self.data[0x100:0x104] = words(instruction, 0x4770)
                handler = scripts.infer_handler(self.data, 0, 0x100, self.readers)
                self.assertEqual((handler.status, handler.lengths), ("unresolved", ()))
                self.assertTrue(any("next script pointer is opaque" in reason for reason in handler.reasons))

    def test_computed_context_pointer_accesses_are_classified(self):
        body = words(0x1C02, 0x3208, 0x6811, 0x3104, 0x6011, 0x2000, 0x4770)
        self.data[0x100:0x100 + len(body)] = body
        handler = scripts.infer_handler(self.data, 0, 0x100, self.readers)
        self.assertEqual((handler.status, handler.lengths), ("fixed", (5,)))
        body = words(0x1C02, 0x3208, 0x6011, 0x2000, 0x4770)
        self.data[0x100:0x100 + len(body)] = body
        handler = scripts.infer_handler(self.data, 0, 0x100, self.readers)
        self.assertEqual((handler.status, handler.lengths), ("unresolved", ()))
        self.assertTrue(any("next script pointer is opaque" in reason for reason in handler.reasons))

    def test_mov_pc_uses_proved_return_provenance(self):
        self.data[0x100:0x104] = words(0x46F7)
        handler = scripts.infer_handler(self.data, 0, 0x100, self.readers)
        self.assertEqual((handler.status, handler.lengths), ("fixed", (1,)))
        self.data[0x100:0x104] = words(0x4687)
        handler = scripts.infer_handler(self.data, 0, 0x100, self.readers)
        self.assertEqual((handler.status, handler.lengths), ("unresolved", ()))
        self.assertTrue(any("unresolved indirect branch" in reason for reason in handler.reasons))

    def test_return_requires_stack_provenance(self):
        self.data[0x100:0x108] = words(0x6881, 0xB402, 0xBC01, 0x4700)
        handler = scripts.infer_handler(self.data, 0, 0x100, self.readers)
        self.assertEqual((handler.status, handler.lengths), ("unresolved", ()))
        self.assertTrue(any("unresolved indirect branch" in reason for reason in handler.reasons))
        for body in (words(0xB500, 0xBC01, 0x4700), words(0xB510, 0xBD10)):
            with self.subTest(body=body.hex()):
                self.data[0x100:0x108] = body.ljust(8, b"\0")
                handler = scripts.infer_handler(self.data, 0, 0x100, self.readers)
                self.assertEqual((handler.status, handler.lengths), ("fixed", (1,)))
        self.data[0x100:0x104] = words(0xB402, 0xBD00)
        handler = scripts.infer_handler(self.data, 0, 0x100, self.readers)
        self.assertEqual(handler.status, "unresolved")

    def test_reader_requires_context_pointer(self):
        self.data[0x100:0x10C] = words(0xB500, 0x2000) + call(0x104, 0x40) + words(0xBD00)
        handler = scripts.infer_handler(self.data, 0, 0x100, self.readers)
        self.assertEqual((handler.status, handler.lengths), ("shape", (1,)))
        self.assertTrue(handler.assumptions)

    def test_analysis_limit_does_not_publish_fixed_coverage(self):
        self.data[0x100:0x108] = words(0xB500) + call(0x102, 0x40) + words(0xBD00)
        handler = scripts.infer_handler(self.data, 0, 0x100, self.readers, max_states=1)
        self.assertEqual(handler.status, "unresolved")
        self.assertIn("state limit", handler.reasons[0])

    def test_shape_stores_keep_context_aliases_explicit(self):
        self.data[0x100:0x104] = words(0x600A, 0x4770)
        handler = scripts.infer_handler(self.data, 0, 0x100, {})
        self.assertEqual((handler.status, handler.lengths), ("shape", (1,)))
        self.assertIn("noncontext-stores-do-not-alias-context", handler.assumptions)
        self.assertIn(0x100, handler.assumption_sites)
        self.data[0x100:0x106] = words(0x1C01, 0x608A, 0x4770)
        self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}).status, "unresolved")

    def test_escaped_context_copies_reach_callees(self):
        for store, load in ((0x6008, 0x680A), (0x9000, 0x9A00)):
            with self.subTest(store=store):
                body = words(0xB500, 0xB081, store, 0x2000) + call(0x108, 0x200)
                body += words(0xB001, 0xBD00)
                self.data[0x100:0x100 + len(body)] = body
                self.data[0x200:0x206] = words(load, 0x6093, 0x4770)
                self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}).status, "unresolved")
                self.data[0x200:0x206] = words(0x2000, 0x4770, 0x4770)
                self.assertIn(scripts.infer_handler(self.data, 0, 0x100, {}).status, ("fixed", "shape"))

    def test_preserved_registers_are_not_call_arguments(self):
        body = words(0xB510, 0x1C04, 0x2000) + call(0x106, 0x200) + words(0xBD10)
        self.data[0x100:0x100 + len(body)] = body
        self.data[0x200:0x208] = words(0xB500) + call(0x202, 0x300) + words(0xBD00)
        self.data[0x300:0x304] = words(0x60A1, 0x4770)
        handler = scripts.infer_handler(self.data, 0, 0x100, {})
        self.assertEqual(handler.status, "shape")
        self.assertEqual(handler.calls[0]["site"], 0x106)
        self.assertNotIn(0x200, handler.code_sites)
        self.data[0x300:0x304] = words(0x2000, 0x4770)
        self.assertIn(scripts.infer_handler(self.data, 0, 0x100, {}).status, ("fixed", "shape"))

    def test_argument_free_calls_skip_unknown_targets_and_limits(self):
        for target in (0x200, 0x300):
            with self.subTest(target=target):
                body = words(0xB510, 0x1C04, 0x2000) + call(0x106, target)
                body += words(0x1C20, 0x6881, 0x3102, 0x6081, 0xBD10)
                self.data[0x100:0x100 + len(body)] = body
                self.data[0x200:0x202] = words(0xE7FE)
                self.data[0x300:0x302] = words(0x4718)
                handler = scripts.infer_handler(self.data, 0, 0x100, {}, max_helper_states=1)
                self.assertEqual((handler.status, handler.lengths), ("shape", (3,)))
                self.assertIn("arguments-only-preserved-registers-and-frame", handler.assumptions)
                self.assertNotIn(target, handler.code_sites)
                self.data[0x104:0x106] = words(0x1C22)
                self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {},
                    max_helper_states=1).status, "unresolved")

    def test_saved_frame_and_outgoing_arguments_are_distinct(self):
        body = words(0xB501, 0x2000) + call(0x104, 0x300) + words(0xBD01)
        self.data[0x100:0x100 + len(body)] = body
        self.data[0x300:0x302] = words(0x4718)
        handler = scripts.infer_handler(self.data, 0, 0x100, {})
        self.assertEqual((handler.status, handler.lengths), ("shape", (1,)))
        for middle in (words(0xB081, 0x9000, 0x2000),
                       words(0xB081, 0x9000, 0x2000, 0x4669)):
            with self.subTest(middle=middle.hex()):
                body = words(0xB500) + middle
                body += call(0x100 + len(body), 0x300) + words(0xB001, 0xBD00)
                self.data[0x100:0x100 + len(body)] = body
                self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}).status, "unresolved")

    def test_pushed_stack_arguments_are_not_saved_frames(self):
        self.data[0x300:0x302] = words(0x4718)
        for push in (0xB401, 0xB403):
            with self.subTest(push=push):
                body = words(0xB500, push, 0x2000) + call(0x106, 0x300)
                body += words(0xB001 if push == 0xB401 else 0xB002, 0xBD00)
                self.data[0x100:0x100 + len(body)] = body
                self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}).status, "unresolved")
                body = words(0xB500, 0x2000, push) + call(0x106, 0x300) + body[10:]
                self.data[0x100:0x100 + len(body)] = body
                self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}).status, "shape")

        body = words(0xB510, 0x1C04, 0xB410, 0x2000) + call(0x108, 0x300)
        body += words(0xBC10, 0xBD10)
        self.data[0x100:0x100 + len(body)] = body
        self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}).status, "unresolved")

    def test_indirect_tail_call_requires_no_context_arguments(self):
        self.data[0x100:0x104] = words(0x2000, 0x4718)
        handler = scripts.infer_handler(self.data, 0, 0x100, {})
        self.assertEqual((handler.status, handler.lengths), ("shape", (1,)))
        self.assertIsNone(handler.calls[0]["target"])
        self.data[0x100:0x104] = words(0x1C02, 0x4718)
        self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}).status, "unresolved")

    def test_multiple_loads_preserve_context_provenance(self):
        self.data[0x100:0x106] = words(0xC90C, 0x2000, 0x4770)
        handler = scripts.infer_handler(self.data, 0, 0x100, {})
        self.assertEqual((handler.status, handler.lengths), ("fixed", (1,)))
        self.data[0x100:0x106] = words(0xC80C, 0x2000, 0x4770)
        self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}).status, "unresolved")
        body = words(0xB500, 0xB081, 0x9000, 0x4669, 0xC904, 0x6093, 0xB001, 0xBD00)
        self.data[0x100:0x100 + len(body)] = body
        self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}).status, "unresolved")
        self.data[0x104:0x106] = words(0x9300)
        self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}).status, "shape")
        self.data[0x100:0x104] = words(0xC906, 0x4770)
        self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}).status, "unresolved")

    def test_bios_interfaces_preserve_context_except_destinations(self):
        bios = load_script("firered-cfru").discovery["bios"]
        for number in (6, 7, 8, 11, 12, 16, 17, 18, 19, 20, 21, 22, 23, 24):
            with self.subTest(number=number):
                body = words(0xB510, 0x1C04, 0xDF00 | number, 0x1C20,
                             0x6881, 0x3102, 0x6081, 0xBD10)
                self.data[0x100:0x100 + len(body)] = body
                handler = scripts.infer_handler(self.data, 0, 0x100, {}, bios=bios)
                self.assertEqual((handler.status, handler.lengths), ("shape", (3,)))
                self.assertEqual(handler.calls[0]["bios"], number)
                if number in (6, 7, 8):
                    self.data[0x104:0x106] = words(0xDF00)
                else:
                    self.data[0x100:0x100 + len(body) + 2] = body[:4] + words(0x1C01) + body[4:]
                self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}, bios=bios).status, "unresolved")
        self.data[0x100:0x108] = words(0xB500, 0x4669, 0xDF0B, 0xBD00)
        self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}, bios=bios).status, "unresolved")
        self.data[0x100:0x108] = words(0xB500, 0xDF08, 0x6081, 0xBD00)
        self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}, bios=bios).status, "unresolved")

    def test_literal_call_thunk_requires_a_thumb_target(self):
        body = words(0xB500, 0x4B07) + call(0x104, 0x300) + words(0xBD00)
        self.data[0x100:0x100 + len(body)] = body
        self.data[0x120:0x124] = struct.pack("<I", ROM_BASE + 0x41)
        self.data[0x300:0x302] = words(0x4718)
        self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}).lengths, (3,))
        self.data[0x120:0x124] = struct.pack("<I", ROM_BASE + 0x40)
        self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}).status, "unresolved")

    def test_redirects_require_known_sources_and_no_later_reads(self):
        self.data[0x100:0x106] = words(0x2100, 0x6081, 0x4770)
        handler = scripts.infer_handler(self.data, 0, 0x100, {})
        self.assertEqual((handler.status, handler.lengths), ("shape", (1,)))
        self.assertEqual(handler.redirects[0]["source"], "null")
        self.data[0x100:0x10A] = words(0x2100, 0x6081, 0x6881, 0x7809, 0x4770)
        handler = scripts.infer_handler(self.data, 0, 0x100, {})
        self.assertEqual(handler.status, "unresolved")
        self.assertTrue(any("read after script redirect" in reason for reason in handler.reasons))
        self.data[0x100:0x104] = words(0x6081, 0x4770)
        self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}).status, "unresolved")

    def test_variable_paths_include_branch_conditions(self):
        body = words(0x2900, 0xD003, 0xB500) + call(0x106, 0x40) + words(0xBD00, 0x4770)
        self.data[0x100:0x100 + len(body)] = body
        handler = scripts.infer_handler(self.data, 0, 0x100, {})
        self.assertEqual(handler.status, "variable")
        self.assertEqual({p["conditions"][0]["taken"] for p in handler.paths}, {False, True})
        self.data[0x100:0x102] = words(0x2100)
        handler = scripts.infer_handler(self.data, 0, 0x100, {})
        self.assertEqual(handler.status, "variable")

    def test_bounded_jump_tables_follow_all_targets(self):
        body = words(0x7809, 0x2901, 0xD807, 0x0089, 0x4A06, 0x5889, 0x468F)
        self.data[0x100:0x100 + len(body)] = body
        self.data[0x114:0x116] = words(0x4770)
        self.data[0x124:0x128] = struct.pack("<I", ROM_BASE + 0x180)
        self.data[0x180:0x188] = struct.pack("<II", ROM_BASE + 0x200, ROM_BASE + 0x210)
        self.data[0x200:0x202] = words(0x4770)
        self.data[0x210:0x212] = words(0x4770)
        handler = scripts.infer_handler(self.data, 0, 0x100, {})
        self.assertEqual((handler.status, handler.lengths), ("fixed", (1,)))
        self.assertTrue({0x200, 0x210} <= set(handler.code_sites))
        self.data[0x102:0x104] = words(0x46C0)
        handler = scripts.infer_handler(self.data, 0, 0x100, {})
        self.assertEqual(handler.status, "unresolved")
        self.assertTrue(any("indirect branch" in reason for reason in handler.reasons))
    def test_switch_helpers_require_shape_and_index_bounds(self):
        helper = words(0xB402, 0x4671, 0x0849, 0x0049, 0x5C09, 0x0049, 0x448E, 0xBC02, 0x4770)
        self.data[0x300:0x300 + len(helper)] = helper
        self.data[0x100:0x108] = words(0xB500, 0x2001) + call(0x104, 0x300)
        self.data[0x108:0x10A] = bytes([2, 3])
        self.data[0x10C:0x110] = words(0xBD00, 0xBD00)
        handler = scripts.infer_handler(self.data, 0, 0x100, {})
        self.assertEqual((handler.status, handler.lengths), ("fixed", (1,)))
        self.assertIn(0x10E, handler.code_sites)
        self.data[0x102:0x104] = words(0x1C08)
        handler = scripts.infer_handler(self.data, 0, 0x100, {})
        self.assertEqual(handler.status, "unresolved")
        self.assertTrue(any("jump table bounds" in reason for reason in handler.reasons))
        self.data[0x100:0x10A] = words(0xB510, 0x1C04, 0x2001) + call(0x106, 0x300)
        self.data[0x10A:0x10C] = bytes([2, 3])
        self.data[0x10E:0x112] = words(0xBD10, 0xBD10)
        self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}).status, "fixed")
        self.data[0x308:0x30A] = words(0x5C11)
        self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}).status, "unresolved")

    def test_affine_pointer_guards_bound_jump_tables(self):
        body = words(0x7809, 0x0089, 0x4A08, 0x1889, 0x4B08, 0x4299,
                     0xD203, 0x6809, 0x468F)
        self.data[0x100:0x100 + len(body)] = body
        self.data[0x116:0x118] = words(0x4770)
        self.data[0x128:0x130] = struct.pack("<II", ROM_BASE + 0x180, ROM_BASE + 0x188)
        self.data[0x180:0x188] = struct.pack("<II", ROM_BASE + 0x200, ROM_BASE + 0x210)
        self.data[0x200:0x202] = words(0x4770)
        self.data[0x210:0x212] = words(0x4770)
        handler = scripts.infer_handler(self.data, 0, 0x100, {})
        self.assertEqual((handler.status, handler.lengths), ("fixed", (1,)))
        self.assertTrue({0x200, 0x210} <= set(handler.code_sites))
        self.data[0x10A:0x10C] = words(0x429A)
        self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}).status, "unresolved")

    def test_constant_rom_loads_require_in_range_targets(self):
        self.data[0x100:0x106] = words(0x4907, 0x6809, 0x468F)
        self.data[0x120:0x124] = struct.pack("<I", ROM_BASE + 0x180)
        self.data[0x180:0x184] = struct.pack("<I", ROM_BASE + 0x200)
        self.data[0x200:0x202] = words(0x4770)
        self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}).status, "fixed")
        self.data[0x180:0x184] = struct.pack("<I", ROM_BASE + len(self.data))
        self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}).status, "unresolved")

    def test_context_arithmetic_cannot_erase_alias_provenance(self):
        self.data[0x100:0x106] = words(0x4048, 0x6082, 0x4770)
        self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}).status, "unresolved")
        self.data[0x100:0x106] = words(0x4051, 0x608A, 0x4770)
        self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}).status, "shape")

    def test_readonly_return_values_keep_context_derivation(self):
        body = words(0xB500) + call(0x102, 0x200) + words(0x6001, 0xBD00)
        self.data[0x100:0x100 + len(body)] = body
        self.data[0x200:0x204] = words(0x3008, 0x4770)
        self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}).status, "unresolved")
        self.data[0x200:0x204] = words(0x300C, 0x4770)
        self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}).status, "fixed")
        for helper in (words(0x1840, 0x4770), words(0x4048, 0x3008, 0x4770)):
            with self.subTest(helper=helper.hex()):
                self.data[0x200:0x200 + len(helper)] = helper
                self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}).status, "unresolved")
        self.data[0x200:0x204] = words(0x1C08, 0x4770)
        self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}).status, "shape")

    def test_partial_stack_copies_cannot_hide_context_aliases(self):
        body = words(0xB500, 0xB081, 0x4669, 0x8008, 0x2000) + call(0x10A, 0x200)
        body += words(0xB001, 0xBD00)
        self.data[0x100:0x100 + len(body)] = body
        self.data[0x200:0x206] = words(0x880A, 0x6093, 0x4770)
        self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}).status, "unresolved")
        self.data[0x200:0x206] = words(0x2000, 0x4770, 0x4770)
        self.assertIn(scripts.infer_handler(self.data, 0, 0x100, {}).status, ("fixed", "shape"))

    def test_no_effect_summary_retains_partial_stack_provenance(self):
        self.data[0x100:0x108] = words(0xB500) + call(0x102, 0x200) + words(0xBD00)
        for load in (words(0x680A, 0xB001), words(0xBC04)):
            for store in (0x8008, 0x6008):
                with self.subTest(load=load.hex(), store=store):
                    body = words(0xB500, 0xB081, 0x4669, store, 0x2000)
                    body += load + words(0x6093, 0xBD00)
                    self.data[0x200:0x200 + len(body)] = body
                    self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}).status, "unresolved")
                    self.data[0x206:0x208] = words(0x800B)
                    self.assertIn(scripts.infer_handler(self.data, 0, 0x100, {}).status, ("fixed", "shape"))
        body = words(0xB500, 0xB081, 0x4669, 0x6008, 0x800B, 0x2000,
                     0x680A, 0x6093, 0xB001, 0xBD00)
        self.data[0x200:0x200 + len(body)] = body
        self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}).status, "unresolved")
        self.data[0x208:0x20A] = words(0x600B)
        self.assertIn(scripts.infer_handler(self.data, 0, 0x100, {}).status, ("fixed", "shape"))

    def test_relative_redirect_requires_a_consumed_operand(self):
        body = words(0xB510, 0x1C04) + call(0x104, 0x200) + words(0x1A80, 0x60A0, 0xBD10)
        self.data[0x100:0x100 + len(body)] = body
        reader = words(0xB510, 0x1C03, 0x689A, 0x7810, 0x7851, 0x0209,
                       0x4308, 0x7891, 0x0409, 0x4308, 0x78D1, 0x0609,
                       0x4308, 0x3204, 0x609A, 0xBD10)
        self.data[0x200:0x200 + len(reader)] = reader
        handler = scripts.infer_handler(self.data, 0, 0x100, {})
        self.assertEqual((handler.status, handler.lengths), ("shape", (5,)))
        self.data[0x200:0x206] = words(0x6881, 0x6808, 0x4770)
        self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}).status, "unresolved")
        self.data[0x200:0x206] = words(0x1C08, 0x4770, 0x4770)
        self.assertEqual(scripts.infer_handler(self.data, 0, 0x100, {}).status, "unresolved")

    def test_cached_summaries_keep_their_own_assumptions(self):
        self.data[0x100:0x108] = words(0xB500) + call(0x102, 0x200) + words(0xBD00)
        self.data[0x200:0x204] = words(0x600A, 0x4770)
        cache = {}
        shape = scripts._Shape(20000)
        shape.add(0x200)
        first = scripts.infer_handler(self.data, 0, 0x100, {}, cache=cache, shape=shape)
        second = scripts.infer_handler(self.data, 0, 0x100, {}, cache=cache)
        self.assertEqual((first.status, second.status), ("shape", "shape"))
        self.assertIn(0x200, second.assumption_sites)
