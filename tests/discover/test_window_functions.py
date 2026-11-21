"""Controls for pointer-seeded executable ownership."""

import unittest

from gen3tl.core.rom import ROM_BASE, Rom
from gen3tl.discover.window_functions import Functions, function_index, returning_body
from gen3tl.discover.window_consumers import Consumers, signature
from gen3tl.families.fingerprints import find
from test_window_consumers import call, words


class WindowFunctionsTest(unittest.TestCase):
    def setUp(self):
        self.rom = Rom(b'\xff' * 16384)

    def entry(self, at, target):
        self.rom.put_u32(at, ROM_BASE + target + 1)

    def test_callback_leaf_owns_its_literal(self):
        self.entry(0x80, 0x200)
        self.rom.write(0x200, words(0x4801, 0x4770))
        self.rom.put_u32(0x208, ROM_BASE + 0x300)
        code = Functions(self.rom)
        proof = code.literal(0x208)
        self.assertEqual([(r['function'], r['load']) for r in proof['loads']], [(0x200, 0x200)])
        self.assertIn(('pointer', 0x80), code.sources[0x200])
        self.rom.put_u32(0x80, ROM_BASE + 0x200)
        self.assertIsNone(Functions(self.rom).literal(0x208))

    def test_discovered_calls_add_leaf_entries(self):
        self.entry(0x80, 0x100)
        self.rom.write(0x100, words(0xB500) + call(0x102, 0x200) + words(0xBD00))
        self.rom.write(0x200, words(0x4801, 0x4770))
        self.rom.put_u32(0x208, ROM_BASE + 0x300)
        code = Functions(self.rom)
        self.assertEqual(code.literal(0x208)['loads'][0]['function'], 0x200)
        self.assertIn(('call', 0x102), code.sources[0x200])

    def test_shared_index_preserves_bytes_and_rejects_stale_ownership(self):
        self.entry(0x80, 0x200)
        self.rom.write(0x200, words(0x4801, 0x4770))
        original = self.rom.copy()
        code = function_index(self.rom)
        self.assertIs(code, function_index(original))
        self.rom.write(0x202, words(0xFFFF))
        self.assertIsNone(function_index(self.rom).literal(0x208))
        self.assertIsNotNone(code.literal(0x208))

    def test_nonreturning_and_indirect_entries_are_refused(self):
        for body in (words(0x4801, 0xE7FE), words(0x4801, 0x4708), words(0x4801, 0xBD00),
                     words(0x4801, 0xFFFF), words(0xB500) + call(0x202, 0x300) + words(0x4770)):
            with self.subTest(body=body):
                self.entry(0x80, 0x200)
                self.rom.write(0x200, body)
                self.assertIsNone(Functions(self.rom).literal(0x208))
                self.assertFalse(returning_body(self.rom, 0x200)[0])

    def test_one_return_does_not_hide_an_invalid_path(self):
        self.rom.write(0x200, words(0xD001, 0x4770, 0x46C0, 0xFFFF))
        self.assertFalse(returning_body(self.rom, 0x200)[0])
        self.rom.write(0x206, words(0x4708))
        self.assertFalse(returning_body(self.rom, 0x200)[0])

    def test_long_function_fingerprint_matches_after_relocation(self):
        self.rom.write(0x100, words(0xB500, 0xE3FF))
        self.rom.write(0x904, words(0xE3FF))
        self.rom.write(0x1106, words(0xE07B))
        self.rom.write(0x1200, words(0xBD00))
        fingerprint, branches = signature(self.rom, 0x100)
        self.assertGreater(fingerprint.length, 4096)
        self.assertEqual(len(branches), 3)
        moved = Rom(b'\xff' * 32768)
        moved.write(0x2000, self.rom.read(0x100, fingerprint.length))
        self.assertEqual(find(moved.data, (fingerprint,))[fingerprint.name], (0x2000,))

    def test_literal_evidence_survives_table_classification(self):
        self.rom.write(0x200, words(0xB500, 0x6002, 0xBD00))
        fingerprint, branches = signature(self.rom, 0x200)
        seed = dict(fingerprint.document(), branches=branches, text_argument=2)
        self.rom.write(0x100, words(0xB500, 0x4A02) + call(0x104, 0x200) + words(0xBD00))
        self.rom.put_u32(0x10C, ROM_BASE + 0x700)
        string = {'id': 'sample', 'offset': 0x700, 'references': [{
            'at': 0x10C, 'status': 'reference', 'kind': 'table',
            'evidence': [{'start': 0x10C, 'end': 0x118, 'stride': 4, 'fields': [0]}]}]}
        result = Consumers(self.rom, [seed]).audit([string])
        self.assertEqual(result['resolved'], 1)
        self.assertEqual(result['strings'][0]['consumers'][0]['kind'], 'literal-pool')
        self.rom.write(0x102, words(0x2200))
        self.assertEqual(Consumers(self.rom, [seed]).audit([string])['resolved'], 0)


if __name__ == '__main__':
    unittest.main()
