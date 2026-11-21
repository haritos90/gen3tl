import unittest

from gen3tl.core.rom import ROM_BASE
from gen3tl.core.thumb import ThumbError, decode, disassemble, walk
from gen3tl.discover.accessors import analyze, literal_loads, script_reader

def words(*values):
    return b"".join(v.to_bytes(2, "little") for v in values)

class DecoderTest(unittest.TestCase):
    def test_all_nineteen_formats_and_operand_boundaries(self):
        cases = [
            (0x00E9, "lsl", {"rd": 1, "rn": 5, "immediate": 3}),
            (0x0808, "lsr", {"rd": 0, "rn": 1, "immediate": 32}),
            (0x17FF, "asr", {"rd": 7, "rn": 7, "immediate": 31}),
            (0x1B49, "sub", {"rd": 1, "rn": 1, "rm": 5}),
            (0x1FFF, "sub", {"rd": 7, "rn": 7, "immediate": 7}),
            (0x21FF, "mov", {"rd": 1, "rn": None, "immediate": 255}),
            (0x3901, "sub", {"rd": 1, "rn": 1, "immediate": 1}),
            (0x4348, "mul", {"rd": 0, "rn": 0, "rm": 1}),
            (0x46C0, "mov", {"rd": 8, "rm": 8}),
            (0x44FF, "add", {"rd": 15, "rn": 15, "rm": 15}),
            (0x4770, "bx", {"rm": 14}),
            (0x48FF, "ldr", {"rd": 0, "literal": 1024, "width": 4}),
            (0x5088, "str", {"rd": 0, "rn": 1, "rm": 2, "width": 4}),
            (0x5688, "ldrsb", {"width": 1, "signed": True}),
            (0x5E88, "ldrsh", {"width": 2, "signed": True}),
            (0x67FF, "str", {"rd": 7, "rn": 7, "immediate": 124}),
            (0x7FFF, "ldrb", {"immediate": 31, "width": 1}),
            (0x8FFF, "ldrh", {"immediate": 62, "width": 2}),
            (0x9FFF, "ldr", {"rd": 7, "rn": 13, "immediate": 1020}),
            (0xA7FF, "add", {"rd": 7, "rn": 15, "immediate": 1020}),
            (0xAFFF, "add", {"rd": 7, "rn": 13, "immediate": 1020}),
            (0xB0FF, "sub", {"rd": 13, "rn": 13, "immediate": 508}),
            (0xB581, "push", {"registers": (0, 7, 14)}),
            (0xBD81, "pop", {"registers": (0, 7, 15)}),
            (0xC6FF, "stmia", {"rn": 6, "registers": tuple(range(8))}),
            (0xCE01, "ldmia", {"rn": 6, "registers": (0,)}),
            (0xD0FE, "b", {"condition": "eq", "target": 0}),
            (0xDFFF, "swi", {"immediate": 255}),
            (0xE7FE, "b", {"target": 0}),
        ]
        for encoding, mnemonic, fields in cases:
            with self.subTest(encoding=hex(encoding)):
                instruction = decode(words(encoding), 0)
                self.assertEqual(instruction.mnemonic, mnemonic)
                for key, value in fields.items():
                    self.assertEqual(getattr(instruction, key), value, key)
        for operation in range(16):
            self.assertNotEqual(decode(words(0x4000 | operation << 6), 0).mnemonic, "undefined")
        for operation in range(8):
            self.assertEqual(decode(words(0x5000 | operation << 9), 0).width, (4, 2, 1, 1, 4, 2, 1, 2)[operation])

    def test_long_branch_sign_alignment_and_reserved_encodings(self):
        positive = decode(words(0xF000, 0xF802), 0)
        negative = decode(words(0xF7FF, 0xFFFC), 0)
        self.assertEqual((positive.size, positive.target, positive.raw), (4, 8, (0xF000, 0xF802)))
        self.assertEqual(negative.target, -4)
        self.assertEqual(decode(words(0, 0x4800), 2).literal, 4)
        for encoding in (0xDE00, 0xE800, 0xF800, 0x4780, 0xBE00, 0xB400, 0xBC00, 0xC000, 0xC800):
            self.assertEqual(decode(words(encoding), 0).mnemonic, "undefined")
        for data, address in ((b"", 0), (words(0), -2), (words(0), 1), (words(0xF000), 0)):
            with self.assertRaises(ThumbError):
                decode(data, address)
        with self.assertRaises(ThumbError):
            disassemble(words(0xF000, 0xF800), 0, 2)
        with self.assertRaises(ThumbError):
            disassemble(b"", -2, -2)

    def test_local_walk_does_not_decode_pools_or_follow_callees(self):
        data = words(0x4801, 0xD001, 0xF000, 0xF802, 0x4770, 0xDE00)
        self.assertEqual([i.address for i in walk(data, 0, 10)], [0, 2, 4, 8])
        self.assertEqual(walk(words(0xE7FE), 0, 2)[0].successors, (0,))
        self.assertEqual(decode(words(0xBD00), 0).successors, ())
        with self.assertRaises(ThumbError):
            walk(data, 0, 10, limit=1)

class AccessorTest(unittest.TestCase):
    def test_old_analysis_constructors_and_unknown_comparison_operands_remain_valid(self):
        result = analyze(words(0x4288, 0x4770), 0, 4)
        comparison, = result.comparisons
        self.assertEqual(comparison.left.terms, (("r0", 1),))
        self.assertEqual(comparison.right.terms, (("r1", 1),))
        with self.assertRaises(AttributeError):
            comparison.site = 8
        legacy = type(result)(result.start, result.end, result.literals, result.accesses,
                              result.accessors, result.calls, result.straight_line,
                              result.addresses, result.exit_sites)
        self.assertEqual(legacy.comparisons, ())

    def test_comparisons_preserve_operands_without_combining_branch_paths(self):
        code = words(0x2801, 0xD001, 0x2102, 0xE000, 0x2103, 0x4288, 0x4770)
        comparisons = analyze(code, 0, len(code)).comparisons
        self.assertEqual([(row.site, row.operation, row.right.constant) for row in comparisons],
                         [(0, "cmp", 1), (10, "cmp", 2), (10, "cmp", 3)])
        self.assertTrue(all(row.left.terms == (("r0", 1),) for row in comparisons))

    def test_comparison_operands_retain_loaded_word_and_pool_provenance(self):
        code = words(0x4803, 0x8801, 0x4A03, 0x4291, 0x4770).ljust(16, b"\x00")
        code += (ROM_BASE + 24).to_bytes(4, "little") + (65535).to_bytes(4, "little") + (17).to_bytes(2, "little")
        comparison, = analyze(code, 0, 10).comparisons
        self.assertEqual((comparison.site, comparison.left.constant, comparison.right.constant), (6, 17, 65535))
        self.assertIn(2, comparison.left.sites)
        self.assertIn(4, comparison.right.sites)

    def table_code(self, tail=()):
        code = words(0x4803, 0x00E9, 0x1B49, 0x0089, 0x1809, 0x7A88, *tail)
        return code.ljust(16, b"\x00") + (ROM_BASE + 32).to_bytes(4, "little") + bytes(44)

    def test_affine_stride_offset_and_literal_chain(self):
        result = analyze(self.table_code(), 0, 12)
        item, = result.accessors
        self.assertEqual((item.site, item.base, item.indices, item.field_offset, item.width),
                         (10, 32, (("r5", 28),), 10, 1))
        self.assertEqual(item.code_sites, (0, 2, 4, 6, 8, 10))
        self.assertEqual((item.literals[0].pool, item.literals[0].value), (16, ROM_BASE + 32))
        indirect = bytearray(words(0x4803, 0x6800, 0x0049, 0x1840, 0x8800, 0x4770).ljust(16, b"\x00"))
        indirect += (ROM_BASE + 24).to_bytes(4, "little") + bytes(4)
        indirect += (ROM_BASE + 40).to_bytes(4, "little") + bytes(36)
        item, = analyze(indirect, 0, 12).accessors
        self.assertEqual((item.base, item.indices, item.width), (40, (("r1", 2),), 2))
        self.assertEqual(item.literals[0].pool, 24)

    def test_calls_clobber_values_and_branch_paths_do_not_mix(self):
        code = words(0x4803, 0xF000, 0xF800, 0x1840, 0x7800, 0x4770)
        code = code.ljust(16, b"\x00") + (ROM_BASE + 32).to_bytes(4, "little") + bytes(44)
        self.assertEqual(analyze(code, 0, 12).accessors, ())
        code = words(0xD001, 0x4803, 0xE001, 0x2104, 0xE7FF, 0x1840, 0x7800, 0x4770)
        code += (ROM_BASE + 32).to_bytes(4, "little") + bytes(44)
        result = analyze(code, 0, 16)
        self.assertTrue(result.accessors)
        self.assertTrue(all(a.indices == (("r1", 1),) for a in result.accessors))
        self.assertIsNone(script_reader(result))
        result = analyze(words(0xF000, 0xF800, 0xF000, 0xF800), 0, 8)
        self.assertNotEqual(result.calls[1].arguments[0].terms, result.calls[1].arguments[1].terms)

    def test_pc_register_and_address_formation_have_distinct_alignment(self):
        result = analyze(words(0x2000, 0x4678, 0xF000, 0xF800), 0, 8)
        self.assertEqual(result.calls[0].arguments[0].constant, ROM_BASE + 6)
        result = analyze(words(0x2000, 0xA000, 0xF000, 0xF800), 0, 8)
        self.assertEqual(result.calls[0].arguments[0].constant, ROM_BASE + 4)

    def test_unmodelled_operations_and_limits_fail_conservatively(self):
        code = words(0x4803, 0x4008, 0x1840, 0x7800, 0x4770)
        code = code.ljust(16, b"\x00") + (ROM_BASE + 32).to_bytes(4, "little") + bytes(44)
        self.assertEqual(analyze(code, 0, 10).accessors, ())
        with self.assertRaisesRegex(ThumbError, "state limit"):
            analyze(words(0x3001, 0xE7FD), 0, 4, limit=4)
        with self.assertRaisesRegex(ThumbError, "literal leaves ROM"):
            literal_loads(words(0x48FF, 0x4770), 0, 4)

    def test_reader_requires_consecutive_reads_and_pointer_advance(self):
        code = words(0x6801, 0x780A, 0x3101, 0x6001, 0x4770)
        reader = script_reader(analyze(code, 0, len(code)))
        self.assertEqual((reader.entry, reader.width, reader.pointer_offset), (0, 1, 0))
        self.assertIsNone(script_reader(analyze(code, 0, len(code) - 2)))
        self.assertIsNone(script_reader(analyze(words(0x6801, 0x7808, 0x4770), 0, 6)))

    def test_stack_spills_and_partial_stores_preserve_only_valid_values(self):
        result = analyze(words(0x2003, 0x2104, 0xB403, 0xBC03, 0xF000, 0xF800), 0, 12)
        self.assertEqual([a.constant for a in result.calls[0].arguments[:2]], [3, 4])
        code = words(0x20FF, 0x7008, 0x2300, 0x56CA, 0xF000, 0xF800)
        result = analyze(code, 0, len(code))
        self.assertEqual(result.calls[0].arguments[2].constant, 0xFFFFFFFF)
        code = words(0x2003, 0x6008, 0x2204, 0x704A, 0x6808, 0xF000, 0xF800)
        result = analyze(code, 0, len(code))
        self.assertTrue(result.calls[0].arguments[0].terms)

    def test_constant_register_arithmetic_wraps_before_calls_and_spills(self):
        cases = [((0x2003, 0x07C0), 0x80000000),
                 ((0x2001, 0x3802), 0xFFFFFFFF),
                 ((0x2001, 0x3802, 0x3002), 1),
                 ((0x2080, 0x0600, 0x2103, 0x4348), 0x80000000)]
        for body, expected in cases:
            for spill in ((), (0xB401, 0x2000, 0xBC01)):
                with self.subTest(body=body, spill=bool(spill)):
                    code = words(*body, *spill, 0xF000, 0xF800)
                    call, = analyze(code, 0, len(code)).calls
                    self.assertEqual((call.arguments[0].constant, call.arguments[0].terms), (expected, ()))

    def test_unaligned_and_unconstrained_word_forwarding_cannot_prove_a_table(self):
        for ram, expected in ((0x02000000, 1), (0x02000001, 0), (0x02000002, 0), (0x02000003, 0)):
            code = words(0x4805, 0x4906, 0x6008, 0x6808, 0x1880, 0x7800, 0x4770)
            code = code.ljust(24, b"\x00") + (ROM_BASE + 64).to_bytes(4, "little") + ram.to_bytes(4, "little") + bytes(64)
            result = analyze(code, 0, 14)
            self.assertEqual(len(result.accessors), expected, hex(ram))
        code = words(0x4805, 0x6008, 0x6808, 0x1880, 0x7800, 0x4770)
        code = code.ljust(24, b"\x00") + (ROM_BASE + 64).to_bytes(4, "little") + bytes(68)
        self.assertEqual(analyze(code, 0, 12).accessors, ())
        code = words(0x4805, 0x4906, 0x8008, 0x8808, 0xF000, 0xF800)
        code = code.ljust(24, b"\x00") + (0x1234).to_bytes(4, "little") + (0x02000001).to_bytes(4, "little") + bytes(64)
        call, = analyze(code, 0, 12).calls
        self.assertTrue(call.arguments[0].terms)
        code = words(0x20FF, 0x4905, 0x7008, 0x3101, 0x600A, 0x3901, 0x7808, 0xF000, 0xF800)
        code = code.ljust(24, b"\x00") + (0x02000000).to_bytes(4, "little") + bytes(68)
        call, = analyze(code, 0, 18).calls
        self.assertTrue(call.arguments[0].terms)

    def test_immutable_rom_stores_cannot_supply_forwarded_table_pointers(self):
        code = words(0x4805, 0x4906, 0x6008, 0x6808, 0x1880, 0x7800, 0x4770)
        code = code.ljust(24, b"\x00") + (ROM_BASE + 64).to_bytes(4, "little") + (ROM_BASE + 32).to_bytes(4, "little") + bytes(64)
        self.assertEqual(code[32:36], bytes(4))
        result = analyze(code, 0, 14)
        self.assertEqual(result.accessors, ())
        loaded, = [a for a in result.accesses if a.site == 6 and not a.write]
        self.assertEqual((loaded.value.constant, loaded.value.terms), (0, ()))
