"""Positive and negative controls for reference-to-printer bindings."""

import copy
import struct
import unittest

from gen3tl.core.rom import ROM_BASE, Rom
from gen3tl.discover.references import literal_word
from gen3tl.discover.window_consumers import Consumers, matches, signature


def words(*values):
    return struct.pack("<" + "H" * len(values), *values)


def call(at, target):
    offset = (target - at - 4) & 0x7FFFFF
    return words(0xF000 | offset >> 12, 0xF800 | (offset >> 1 & 0x7FF))


class WindowConsumersTest(unittest.TestCase):
    def setUp(self):
        self.rom = Rom(b"\xff" * 2048)
        self.rom.write(0x100, words(0xB500, 0x4A03) + call(0x104, 0x200) + words(0xBD00))
        self.rom.put_u32(0x110, ROM_BASE + 0x400)
        self.rom.write(0x200, words(0xB500, 0x6002, 0xBD00))
        fingerprint, branches = signature(self.rom, 0x200)
        self.printer = dict(fingerprint.document(), branches=branches, text_argument=2)
        self.reference = {"at": 0x110, "status": "reference", "kind": "literal-pool",
                          "evidence": [literal_word(self.rom, 0x110)]}
        self.string = {"id": "sample", "offset": 0x400, "references": [self.reference]}

    def audit(self, string=None):
        return Consumers(self.rom, [self.printer]).audit([string or self.string])

    def test_literal_value_must_reach_the_printer_argument(self):
        result = self.audit()
        row, = result["strings"][0]["consumers"]
        self.assertEqual((row["slot"], row["tier"]), ("call:4:r2", "shape"))
        self.assertEqual((result["resolved"], result["unresolved"]), (1, 0))
        self.assertIn("procedure-call", row["evidence"]["assumption"])
        self.assertEqual(self.audit(dict(self.string, offset=0x401))["resolved"], 0)

    def test_overwritten_argument_is_not_a_consumer(self):
        self.rom.write(0x104, words(0x2200) + call(0x106, 0x200) + words(0xBD00))
        self.assertEqual(self.audit()["resolved"], 0)

    def test_non_printer_calls_do_not_create_windows(self):
        self.rom.write(0x202, words(0x6001))
        self.assertEqual(self.audit()["resolved"], 0)

    def test_pointer_and_branch_target_relocation_preserves_the_consumer_key(self):
        before = self.audit()["strings"][0]["consumers"][0]
        self.rom.write(0x300, self.rom.data[0x100:0x114])
        self.rom.write(0x304, call(0x304, 0x600))
        self.rom.write(0x600, self.rom.data[0x200:0x206])
        self.rom.put_u32(0x310, ROM_BASE + 0x500)
        reference = dict(self.reference, at=0x310, evidence=[literal_word(self.rom, 0x310)])
        after = self.audit(dict(self.string, offset=0x500, references=[reference]))["strings"][0]["consumers"][0]
        self.assertEqual((before["function"], before["slot"]), (after["function"], after["slot"]))

    def test_local_branch_destinations_are_checked_beside_masked_hashes(self):
        self.rom.write(0x200, words(0xB500, 0x2800, 0xD000, 0x6002, 0xBD00))
        fingerprint, branches = signature(self.rom, 0x200)
        contract = dict(fingerprint.document(), branches=branches)
        self.assertTrue(matches(self.rom, 0x200, contract))
        changed = copy.deepcopy(contract)
        changed["branches"][0][1] += 2
        self.assertFalse(matches(self.rom, 0x200, changed))

    def test_unknown_and_non_reference_words_remain_separate(self):
        self.rom.put_u32(0x114, ROM_BASE + 0x400)
        for kind in ("table", "script", "unclassified"):
            with self.subTest(kind=kind):
                reference = dict(self.reference, at=0x114, kind=kind, evidence=[])
                if kind == "table":
                    reference["evidence"] = [{"start": 0x114, "end": 0x118, "stride": 4, "fields": [0]}]
                result = self.audit(dict(self.string, references=[reference]))
                self.assertEqual(result["resolved"], 0)
                self.assertTrue(result["strings"][0]["unresolved"])
        reference = dict(self.reference, status="non-reference", kind="pcm")
        self.assertEqual(self.audit(dict(self.string, references=[reference]))["strings"][0]["consumers"], [])

    def test_missing_reference_has_an_explicit_reason(self):
        row = self.audit(dict(self.string, references=[]))["strings"][0]
        self.assertEqual(row["unresolved"][0]["kind"], "none")

    def test_reference_origin_is_required_even_for_equal_pointer_values(self):
        reference = copy.deepcopy(self.reference)
        reference["at"] = 0x114
        self.assertEqual(self.audit(dict(self.string, references=[reference]))["resolved"], 0)

    def test_multiple_calls_are_retained(self):
        self.rom.write(0x108, words(0x4A01) + call(0x10A, 0x200) + words(0xBD00))
        self.reference["evidence"] = [literal_word(self.rom, 0x110)]
        self.assertEqual(self.audit()["multiple"], 1)

    def test_indexed_read_must_reach_the_printer_from_the_declared_field(self):
        self.rom.write(0x100, words(0xB500, 0x0089, 0x4803, 0x5842)
                       + call(0x108, 0x200) + words(0xBD00))
        self.rom.put_u32(0x114, ROM_BASE + 0x500)
        self.rom.put_u32(0x504, ROM_BASE + 0x400)
        reference = {"at": 0x504, "kind": "table", "status": "reference", "evidence": [
            {"start": 0x500, "end": 0x50C, "stride": 4, "fields": [0]}]}
        string = dict(self.string, references=[reference])
        row, = self.audit(string)["strings"][0]["consumers"]
        self.assertEqual((row["slot"], row["kind"]), ("call:8:r2:read:6", "table"))
        reference["evidence"][0]["stride"] = 8
        self.assertEqual(self.audit(string)["resolved"], 0)
        reference["evidence"][0]["stride"] = 4
        self.rom.put_u32(0x504, ROM_BASE + 0x404)
        self.assertEqual(self.audit(string)["resolved"], 0)


if __name__ == "__main__":
    unittest.main()
