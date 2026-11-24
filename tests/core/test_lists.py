import unittest

from gen3tl.core.lists import append_unique


class AppendUniqueTest(unittest.TestCase):
    def test_equal_values_stay_once_in_first_order(self):
        items = []
        for value in ("b", "a", "b", {"k": 1}, {"k": 1}, "a"):
            append_unique(items, value)
        self.assertEqual(items, ["b", "a", {"k": 1}])


if __name__ == "__main__":
    unittest.main()
