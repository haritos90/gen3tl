"""Coverage ignores declared configuration without hiding ROM disagreements."""

import unittest

from gen3tl.profile import check, configuration


class ConfigurationCoverageTest(unittest.TestCase):
    def test_only_declared_keys_are_ignored(self):
        original = {"base": 16, "entry": 8, "index": [2, 1, 0], "name": "keys"}
        generated = {"base": 16, "entry": 8, "name": "keys"}
        actual = configuration.value("keyboard.table[name=keys]", "keyboard.table", original)
        self.assertEqual(check.compare(actual, generated), ("reproduced", ()))
        self.assertEqual(check.compare(actual, {**generated, "base": 20}), ("contradicts", ("base",)))
        self.assertEqual(configuration.value("table[name=keys]", "table", original), original)
        self.assertEqual(check.compare({**actual, "unknown": 1}, generated), ("extends", ("unknown",)))

    def test_configuration_has_no_rom_denominator(self):
        for identifier, section in (("keyboard.chat.rows", "keyboard.chat"),
                                    ("keyboard.row_bindings.lower_1", "keyboard.row_bindings")):
            self.assertIsNone(configuration.value(identifier, section, 3))
        self.assertEqual(configuration.value("keyboard.chatty", "keyboard", 3), 3)
        section = check.Section("keyboard.chat", "keyboard", facts=5)
        section.counts["configuration"] = 5
        self.assertEqual((section.covered, section.rom_facts), (0, 0))

    def test_mixed_keyboard_fact_keeps_rom_keys(self):
        value = {"labels": [1], "reserved": [16, 32], "page_count": [64]}
        actual = configuration.value("keyboard", "keyboard", value)
        self.assertEqual(actual, {"reserved": [16, 32], "page_count": [64]})
        self.assertEqual(check.compare(actual, {"reserved": [16, 32]}), ("extends", ("page_count",)))
