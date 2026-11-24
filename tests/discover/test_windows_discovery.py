"""Window family contracts, generation and unchanged measurement controls."""

import json
import tempfile
import unittest
from pathlib import Path

from gen3tl.core.charmap import Charmap
from gen3tl.core.rom import ROM_BASE, Rom
from gen3tl.discover import emission, windows
from gen3tl.discover.window_consumers import signature
from gen3tl.families.windows import COLUMNS, Windows, load
from gen3tl.profile import schema, tsv
from test_window_consumers import call, words


class WindowsDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.rom = Rom(b"\xff" * 2048)
        self.rom.write(0x100, words(0xB500, 0x4A03) + call(0x104, 0x200) + words(0xBD00))
        self.rom.put_u32(0x110, ROM_BASE + 0x400)
        self.rom.write(0x200, words(0xB500, 0x6002, 0xBD00))
        self.rom.write(0x400, Charmap().encode("Sample"))
        printer, branches = signature(self.rom, 0x200)
        consumer, edges = signature(self.rom, 0x100)
        self.window = {"function": consumer.sha256, "slot": "call:4:r2", "name": "sample-menu",
                       "where": "Sample menu", "width": 88, "lines": 2, "evidence": "Measured menu interior."}
        self.knowledge = Windows([self.window], [dict(consumer.document(), branches=edges)],
                                 [dict(printer.document(), branches=branches, text_argument=2)])

    def test_generation_keeps_absent_measures_absent_and_retains_evidence(self):
        result = windows.discover(self.rom, self.knowledge)
        layout = result.data["layout"]
        self.assertEqual(layout["window_width"], {"s0000400": 88})
        self.assertEqual(layout["window_lines"], {"s0000400": 2})
        self.assertNotIn("window_style", layout)
        self.assertNotIn("window_box", layout)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "profile.toml").write_text(emission.document(result.data, result.facts))
            loaded = schema.load(path)
            self.assertEqual(loaded["layout"]["window"][0]["evidence"], self.window["evidence"])
            self.assertEqual(loaded["layout"]["window_width"], layout["window_width"])

    def test_missing_and_ambiguous_consumers_produce_no_measurement(self):
        self.rom.write(0x100, words(0xB510))
        result = windows.discover(self.rom, self.knowledge)
        self.assertEqual(result.data, {})
        self.assertTrue(result.metadata["unresolved"])
        self.rom.write(0x100, words(0xB500))
        self.rom.write(0x300, self.rom.data[0x100:0x114])
        self.assertEqual(windows.discover(self.rom, self.knowledge).data, {})

    def test_a_changed_printer_does_not_supply_a_window(self):
        self.rom.write(0x202, words(0x6001))
        result = windows.discover(self.rom, self.knowledge)
        self.assertEqual(result.data, {})
        self.assertEqual(result.metadata["audit"]["resolved"], 0)

    def test_behavior_bindings_keep_the_original_window_record(self):
        self.window.update(newline_scroll_after=1, prompt_arrow_width=10, box="dialogue")
        result = windows.discover(self.rom, self.knowledge)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "profile.toml").write_text(emission.document(result.data, result.facts))
            loaded = schema.load(path)["layout"]
            self.assertEqual(loaded["window_binding"]["s0000400"], loaded["window"][0])
            self.assertEqual(loaded["window_newline_scroll_after"], {"s0000400": 1})

    def test_overlay_residue_and_generated_measurements_load_together(self):
        result = windows.discover(self.rom, self.knowledge)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "profile.toml").write_text(emission.document(result.data, result.facts))
            (path / "overlay.toml").write_text('[layout.window_width]\nmanual = 60\n')
            loaded = schema.load(path)["layout"]
            self.assertEqual(loaded["window_width"], {"manual": 60, "s0000400": 88})
            self.assertNotIn("unresolved", loaded["window_width"])

    def test_relocation_binds_the_new_string_without_an_instance_offset(self):
        before = windows.discover(self.rom, self.knowledge).data["layout"]
        self.rom.write(0x300, self.rom.data[0x100:0x114])
        self.rom.write(0x100, b"\xff" * 20)
        self.rom.write(0x304, call(0x304, 0x200))
        self.rom.write(0x500, Charmap().encode("Moved"))
        self.rom.put_u32(0x310, ROM_BASE + 0x500)
        after = windows.discover(self.rom, self.knowledge).data["layout"]
        self.assertEqual(after["window"], before["window"])
        self.assertEqual(after["window_width"], {"s0000500": 88})

    def test_conflicting_consumers_do_not_pick_an_arbitrary_width(self):
        row = {"function": self.window["function"], "slot": self.window["slot"]}
        second = dict(row, slot="call:8:r2")
        audit = {"strings": [{"id": "sample", "consumers": [row, second]}]}
        other = dict(self.window, slot=second["slot"], width=80)
        bindings, unresolved = windows.bind(audit, [self.window, other])
        self.assertEqual(bindings, [])
        self.assertEqual(unresolved[0]["string"], "sample")
        other["width"] = 88
        self.assertEqual(len(windows.bind(audit, [self.window, other])[0]), 1)

    def write_family(self, directory, rows=None, functions=None):
        directory = Path(directory)
        tsv.write(directory / "windows.tsv", rows or self.knowledge.rows, COLUMNS)
        (directory / "window-functions.json").write_text(json.dumps({"schema": 1, "family": directory.name,
            "printers": self.knowledge.printers, "consumers": functions or self.knowledge.functions}))
        return load(directory)

    def test_family_loader_checks_slots_fingerprints_and_repeated_window_values(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self.write_family(directory).rows, self.knowledge.rows)
            for change in ({"slot": "call:5:r2"}, {"slot": "call:4000:r2"}, {"function": "0" * 64},
                           {"name": "Invalid"}):
                with self.subTest(change=change), self.assertRaises(schema.ProfileError):
                    self.write_family(directory, [dict(self.window, **change)])
            missing = {key: value for key, value in self.window.items() if key != "evidence"}
            with self.assertRaises(schema.ProfileError):
                self.write_family(directory, [missing])
            with self.assertRaises(schema.ProfileError):
                self.write_family(directory, [self.window, self.window])
            other = dict(self.window, slot="call:6:r2", width=80)
            with self.assertRaises(schema.ProfileError):
                self.write_family(directory, [self.window, other])

    def test_family_without_windows_has_no_implicit_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load(Path(directory)).rows, [])


if __name__ == "__main__":
    unittest.main()
