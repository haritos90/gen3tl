"""Controls for finite argument joins at control-flow entries."""

import unittest

from gen3tl.core.rom import Rom, ROM_BASE
from gen3tl.discover.accessors import Expression
from gen3tl.discover.window_merge import Joins
from gen3tl.discover.window_flow import trace
from test_window_consumers import words, call


class WindowMergeTest(unittest.TestCase):
    def test_join_keeps_small_register_value_sets(self):
        joins = Joins(cap=2)
        registers = tuple(Expression(n) for n in range(16))
        joins.enter(0x100, registers, (), ())
        other = list(registers)
        other[2] = Expression(99)
        states = joins.enter(0x100, tuple(other), (), ())
        self.assertEqual({regs[2].constant for regs, _, _ in states}, {2, 99})
        self.assertFalse(joins.enter(0x100, tuple(other), (), ()))

    def test_widening_is_stable_after_the_cap(self):
        joins = Joins(cap=2)
        for value in range(5):
            states = joins.enter(0x100, (Expression(value),) * 16, (), ())
        self.assertFalse(states)
        self.assertTrue(joins.widened)
        self.assertTrue(joins.rows[0x100]['r', 0][0].terms)

    def test_missing_stack_value_remains_a_possible_unknown(self):
        joins = Joins()
        registers = (Expression(),) * 16
        key = ((-4 & 0xFFFFFFFF, (('sp', 1),)), 4)
        joins.enter(0x100, registers, ((key, Expression(7)),), ())
        states = joins.enter(0x100, registers, (), ())
        self.assertTrue(any(not saved for _, saved, _ in states))
        self.assertTrue(any(dict(saved).get(key) == Expression(7) for _, saved, _ in states))

    def test_loop_reaches_a_finite_join_and_preserves_text(self):
        rom = Rom(b'\xff' * 2048)
        rom.write(0x100, words(0xB500, 0x4A05, 0x2000, 0x3001, 0x2809, 0xD1FC)
                  + call(0x10C, 0x300) + words(0xBD00))
        rom.put_u32(0x118, ROM_BASE + 0x500)
        flow = trace(rom, 0x100, 0x112, merge=True)
        self.assertTrue(any(c.target == 0x300 and c.arguments[2].constant == ROM_BASE + 0x500
                            for c in flow.calls))
        self.assertTrue(any(kind == 'merge-widen' for _, kind in flow.stops))

    def test_diamond_preserves_both_literal_texts(self):
        rom = Rom(b'\xff' * 2048)
        rom.write(0x100, words(0xB500, 0x2800, 0xD001, 0x4A04, 0xE000, 0x4A04)
                  + call(0x10C, 0x300) + words(0xBD00))
        rom.put_u32(0x118, ROM_BASE + 0x500)
        rom.put_u32(0x11C, ROM_BASE + 0x600)
        flow = trace(rom, 0x100, 0x112, merge=True)
        self.assertEqual({c.arguments[2].constant for c in flow.calls}, {ROM_BASE + 0x500, ROM_BASE + 0x600})


if __name__ == '__main__':
    unittest.main()
