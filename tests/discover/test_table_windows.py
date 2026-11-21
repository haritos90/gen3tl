"""Indexed window binding and pointer-run boundary controls."""

import json
import tempfile
import unittest
from pathlib import Path

from gen3tl.core.charmap import Charmap
from gen3tl.core.rom import ROM_BASE, Rom
from gen3tl.discover import emission, table_windows, windows
from gen3tl.discover.window_consumers import signature
from gen3tl.families.windows import COLUMNS, Windows, load
from gen3tl.profile import schema, tsv
from test_window_consumers import call, words


class TableWindowsTest(unittest.TestCase):
    def setUp(self):
        self.rom = Rom(b"\xff" * 2048)
        self.rom.write(0x100, words(0xB500, 0x0089, 0x4803, 0x5842)
                       + call(0x108, 0x200) + words(0xBD00))
        self.rom.put_u32(0x114, ROM_BASE + 0x500)
        self.rom.write(0x200, words(0xB500, 0x6002, 0xBD00))
        for pointer, target, text in ((0x500, 0x400, "First"), (0x504, 0x420, "Second")):
            self.rom.put_u32(pointer, ROM_BASE + target)
            self.rom.write(target, Charmap().encode(text))
        self.rom.put_u32(0x508, 0)
        consumer, branches = signature(self.rom, 0x100)
        printer, edges = signature(self.rom, 0x200)
        self.rule = {"function": consumer.sha256, "slot": "call:8:r2:read:6", "extent": "text-pointer-run",
                     "name": "sample-table", "width": 144, "lines": 2, "where": "Sample table"}
        self.knowledge = Windows([], [dict(consumer.document(), branches=branches)],
                                 [dict(printer.document(), branches=edges, text_argument=2)], table_rows=[self.rule])

    def test_indexed_reader_and_terminated_pointer_run_supply_instance_fields(self):
        result = windows.discover(self.rom, self.knowledge)
        row, = result.data["layout"]["table_window"]
        self.assertEqual(row, {"name": "sample-table", "base": 0x500, "stride": 4, "count": 2,
                               "fields": [0], "width": 144, "lines": 2, "where": "Sample table"})
        self.assertIn("assumed to be one table", result.facts[0].evidence.sentence)

    def test_invalid_text_does_not_shorten_the_table(self):
        self.rom.write(0x420, b"\xff")
        result = table_windows.discover(self.rom, self.knowledge)
        self.assertEqual(result.data, {})
        self.assertIn("invalid text", result.metadata["unresolved"][0]["reason"])

    def test_missing_non_pointer_end_is_unresolved(self):
        with self.assertRaisesRegex(ValueError, "bounded"):
            table_windows.pointer_run(self.rom, 0x500, limit=2)
        self.rom.put_u32(0x500, 0)
        with self.assertRaises(ValueError):
            table_windows.pointer_run(self.rom, 0x500)
        for base in (-4, 0x501, len(self.rom)):
            with self.subTest(base=base), self.assertRaises(ValueError):
                table_windows.pointer_run(self.rom, base)

    def test_relocation_uses_the_matched_readers_own_table_pointer(self):
        self.rom.write(0x300, self.rom.data[0x100:0x118])
        self.rom.write(0x100, b"\xff" * 24)
        self.rom.write(0x308, call(0x308, 0x200))
        self.rom.put_u32(0x314, ROM_BASE + 0x600)
        self.rom.write(0x600, self.rom.data[0x500:0x50C])
        row, = table_windows.discover(self.rom, self.knowledge).data["layout"]["table_window"]
        self.assertEqual((row["base"], row["count"], row["width"]), (0x600, 2, 144))

    def test_ambiguous_reader_does_not_choose_a_table(self):
        self.rom.write(0x300, self.rom.data[0x100:0x118])
        result = table_windows.discover(self.rom, self.knowledge)
        self.assertEqual(result.data, {})
        self.assertIn("ambiguous", result.metadata["unresolved"][0]["reason"])

    def test_mismatched_printer_and_read_slot_do_not_emit_measurements(self):
        self.rom.write(0x202, words(0x6001))
        self.assertEqual(table_windows.discover(self.rom, self.knowledge).data, {})
        self.rom.write(0x202, words(0x6002))
        self.rule["slot"] = "call:8:r2:read:4"
        self.assertEqual(table_windows.discover(self.rom, self.knowledge).data, {})

    def test_existing_identity_row_preserves_display_evidence_and_array_order(self):
        result = table_windows.discover(self.rom, self.knowledge)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "profile.toml").write_text(emission.document(result.data, result.facts, {}))
            (path / "overlay.toml").write_text('[[layout.table_window]]\nname = "sample-table"\n'
                'evidence = "Original measurement."\n[[layout.table_window]]\nname = "manual"\nwidth = 80\n')
            rows = schema.load(path)["layout"]["table_window"]
            self.assertEqual([r["name"] for r in rows], ["sample-table", "manual"])
            self.assertEqual(rows[0]["evidence"], "Original measurement.")
            self.assertEqual(rows[0]["count"], 2)

    def test_family_table_contract_refuses_instance_addresses(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            tsv.write(path / "windows.tsv", [], COLUMNS)
            (path / "window-functions.json").write_text(json.dumps({"schema": 1, "family": path.name,
                "consumers": self.knowledge.functions, "printers": self.knowledge.printers}))
            header = f'schema = 1\nfamily = "{path.name}"\n'
            body = '[[window]]\n' + '\n'.join(f'{key} = {emission.literal(value)}'
                                              for key, value in self.rule.items()) + '\n'
            (path / "table-windows.toml").write_text(header + body)
            self.assertEqual(load(path).table_rows, [self.rule])
            (path / "table-windows.toml").write_text(header + body + 'base = 1280\n')
            with self.assertRaises(schema.ProfileError):
                load(path)


if __name__ == "__main__":
    unittest.main()
