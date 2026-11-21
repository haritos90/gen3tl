import unittest

from gen3tl.check.layout import SpriteMeasure

def table(name, base, count=4):
    return {"name": name, "base": base, "stride": 13, "count": count, "field_offset": 0, "field_length": 13}

class SpriteMeasureTest(unittest.TestCase):
    """The cursor's travel: the advance per byte, or each byte's step where a pack tile is drawn."""

    def test_steps_apply_only_to_text_with_a_pack_tile(self):
        table = [b % 3 for b in range(256)]
        table[0x80] = 7   # a pack tile
        steps = [3] * 256
        steps[0x00] = 2
        m = SpriteMeasure(4, 5, table, steps)
        self.assertEqual(m.bytes_width(b"\x00\x01\x02"), 12)   # the original routine
        self.assertEqual(m.bytes_width(b"\x80\x00\x01"), 8)
        self.assertEqual(SpriteMeasure(4, 5, table).bytes_width(b"\x80\x00"), 8)   # no step table
        again = SpriteMeasure.from_data(m.data())
        self.assertEqual([again.bytes_width(d) for d in (b"\x00", b"\x80\x00")], [4, 5])
        self.assertNotIn("steps", SpriteMeasure(4, 5, table).data())
