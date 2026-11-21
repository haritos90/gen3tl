from test_roots import TOOLKIT_ROOT
"""Scripts crossref checks and fixtures."""

import sys
from pathlib import Path
import copy
import json
import struct
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.core.rom import ROM_BASE, Rom
from gen3tl.discover import scripts
from gen3tl.discover.maps import CMD_LEN
from gen3tl.families.knowledge import load_script, source_files
from gen3tl.profile.schema import ProfileError


class CrossReferenceTest(unittest.TestCase):
    def run_crossref(self, data, handlers, starts, **kwargs):
        return scripts.cross_reference(Rom(bytes(data)), handlers, starts,
            end_ops=kwargs.pop("end_ops", {2}), code_offsets=kwargs.pop("code_offsets", {}),
            text_offsets=kwargs.pop("text_offsets", {}), **kwargs)

    def test_complete_walk_uses_only_derived_lengths(self):
        data = bytearray(0x200)
        data[0x100:0x106] = bytes([16]) + struct.pack("<I", ROM_BASE + 0x180) + bytes([2])
        handlers = [scripts.Handler(16, 0, "shape", (5,)), scripts.Handler(2, 0, "fixed", (1,))]
        with patch.dict(CMD_LEN, {16: 99}, clear=True):
            result = self.run_crossref(data, handlers, [0x100], text_offsets={16: (1,)})
        self.assertEqual(result["confirmed_roots"], 1)
        self.assertTrue(all(h.status == "crossref" and h.tier == "crossref" for h in handlers))
        data[0x101:0x105] = bytes(4)
        handlers = [scripts.Handler(16, 0, "shape", (5,)), scripts.Handler(2, 0, "fixed", (1,))]
        self.run_crossref(data, handlers, [0x100], text_offsets={16: (1,)})
        self.assertEqual((handlers[0].status, handlers[0].lengths), ("unresolved", ()))
        self.assertEqual(handlers[0].crossref["status"], "contradiction")

    def test_incomplete_roots_cannot_hide_other_occurrences(self):
        data = bytearray(0x200)
        data[0x100:0x102] = bytes([16, 2])
        data[0x110:0x113] = bytes([99, 0, 16])
        handlers = [scripts.Handler(16, 0, "shape", (1,)), scripts.Handler(2, 0, "fixed", (1,))]
        self.run_crossref(data, handlers, [0x100, 0x110],
                         occurrences={0x100: [0x100, 0x101], 0x110: [0x110, 0x112]})
        self.assertEqual(handlers[0].status, "shape")
        self.assertEqual(handlers[0].crossref["blocked"], 1)
        self.assertEqual(handlers[1].status, "crossref")
        handlers = [scripts.Handler(16, 0, "shape", (1,)), scripts.Handler(2, 0, "fixed", (1,))]
        self.run_crossref(data, handlers, [0x100, 0x110])
        self.assertTrue(all(h.status != "crossref" for h in handlers))

    def test_branch_targets_must_end_without_cycles(self):
        data = bytearray(0x200)
        data[0x100:0x106] = bytes([4]) + struct.pack("<I", ROM_BASE + 0x120) + bytes([2])
        data[0x120] = 2
        handlers = [scripts.Handler(4, 0, "shape", (5,)), scripts.Handler(2, 0, "fixed", (1,))]
        self.run_crossref(data, handlers, [0x100], code_offsets={4: 1})
        self.assertEqual(handlers[0].status, "crossref")
        data[0x101:0x105] = struct.pack("<I", ROM_BASE + 0x100)
        handlers = [scripts.Handler(4, 0, "shape", (5,)), scripts.Handler(2, 0, "fixed", (1,))]
        self.run_crossref(data, handlers, [0x100], code_offsets={4: 1})
        self.assertEqual(handlers[0].status, "shape")
        self.assertEqual(handlers[0].crossref["status"], "incomplete")

    def test_invalid_successor_is_a_contradiction(self):
        data = bytearray(0x200)
        data[0x100:0x103] = bytes([16, 255, 2])
        handlers = [scripts.Handler(16, 0, "shape", (1,)), scripts.Handler(2, 0, "fixed", (1,))]
        self.run_crossref(data, handlers, [0x100])
        self.assertEqual((handlers[0].status, handlers[0].lengths), ("unresolved", ()))
        handlers = [scripts.Handler(16, 0, "shape", (2,)), scripts.Handler(2, 0, "fixed", (1,))]
        self.run_crossref(data, handlers, [0x100])
        self.assertEqual(handlers[0].status, "crossref")

    def test_invalid_roots_do_not_contradict_lengths(self):
        data = bytearray(0x200)
        data[1:4] = bytes([0, 0, 255])
        data[0x100:0x103] = bytes([0, 0, 255])
        data[0x110:0x112] = bytes([0, 2])
        handlers = [scripts.Handler(0, 0, "fixed", (1,)), scripts.Handler(2, 0, "fixed", (1,))]
        rules = {"rom_header_size": 0xC0, "padding_opcode": 0, "erased_byte": 255}
        result = self.run_crossref(data, handlers, [1, 0x100, 0x110, 0x300], rules=rules,
            occurrences={1: [1, 2], 0x100: [0x100, 0x101], 0x110: [0x110, 0x111]})
        self.assertEqual((result["invalid_roots"], result["confirmed_roots"]), (3, 1))
        self.assertEqual(handlers[0].status, "crossref")
        handler = scripts.Handler(0, 0, "fixed", (1,))
        self.run_crossref(data, [handler], [1], rules=rules, occurrences={1: [1, 2]})
        self.assertEqual((handler.status, handler.crossref["status"]), ("fixed", "unobserved"))
        data[0x100:0x103] = bytes([16, 0, 255])
        handlers = [scripts.Handler(16, 0, "fixed", (1,)), scripts.Handler(0, 0, "fixed", (1,))]
        result = self.run_crossref(data, handlers, [0x100], rules=rules)
        self.assertEqual(result["invalid_roots"], 0)
        self.assertEqual(handlers[1].crossref["status"], "contradiction")

    def test_null_termination_requires_declared_operand_role(self):
        data = bytearray(0x200)
        data[0x100] = 5
        rules = {"null_ends": {"5": [1]}}
        for value, status in ((0, "crossref"), (1, "unresolved"), (ROM_BASE + 0x300, "unresolved")):
            with self.subTest(value=value):
                data[0x101:0x105] = struct.pack("<I", value)
                handler = scripts.Handler(5, 0, "fixed", (5,))
                self.run_crossref(data, [handler], [0x100], rules=rules, end_ops={5}, code_offsets={5: 1})
                self.assertEqual(handler.status, status)
        data[0x101:0x105] = bytes(4)
        handler = scripts.Handler(5, 0, "fixed", (5,))
        self.run_crossref(data, [handler], [0x100], end_ops={5}, code_offsets={5: 1})
        self.assertEqual(handler.crossref["status"], "contradiction")

    def test_runtime_text_keeps_length_but_requires_runtime_evidence(self):
        data = bytearray(0x200)
        data[0x100], data[0x105] = 16, 2
        rules = {"runtime_text": {"16": [1]}, "runtime_ranges": [[0x02000000, 0x02040000]]}
        for value, expected in ((0, "incomplete"), (0x02000100, "incomplete"),
                                (0x02040000, "contradiction"), (1, "contradiction"),
                                (ROM_BASE + 0x180, "confirmed")):
            with self.subTest(value=value):
                data[0x101:0x105] = struct.pack("<I", value)
                handlers = [scripts.Handler(16, 0, "fixed", (5,)), scripts.Handler(2, 0, "fixed", (1,))]
                self.run_crossref(data, handlers, [0x100], rules=rules, text_offsets={16: [1]})
                self.assertEqual(handlers[0].crossref["status"], expected)
                if expected == "incomplete":
                    self.assertEqual((handlers[0].status, handlers[0].lengths), ("fixed", (5,)))

    def test_pointer_width_contradiction_withholds_the_length(self):
        data = bytearray(0x200)
        data[0x100:0x103] = bytes([16, 0, 2])
        handlers = [scripts.Handler(16, 0, "shape", (2,)), scripts.Handler(2, 0, "fixed", (1,))]
        self.run_crossref(data, handlers, [0x100], text_offsets={16: (1,)})
        self.assertEqual(handlers[0].lengths, ())
        self.assertEqual(handlers[0].crossref["derived_lengths"], [2])


class FamilyKnowledgeTest(unittest.TestCase):
    def test_fixed_semantics_and_compatibility_defaults_are_separate(self):
        knowledge = load_script("firered-cfru")
        self.assertNotIn("92", knowledge.discovery["fixed_lengths"])
        self.assertNotIn("121", knowledge.discovery["fixed_lengths"])
        self.assertNotIn("182", knowledge.discovery["fixed_lengths"])
        self.assertEqual(knowledge.discovery["defaults"]["command_lengths"]["121"], 15)
        self.assertEqual(knowledge.data["script"]["command_lengths"]["182"], 6)
        self.assertEqual(knowledge.data["script"]["text_ptr_at"]["103"], [1])
        self.assertEqual(knowledge.data["script"]["code_ptr_at"]["4"], 1)
        self.assertIn(2, knowledge.data["script"]["end_ops"])
        self.assertIn("compatibility", knowledge.discovery["defaults"]["provenance"].lower())
        self.assertIn("trainerbattle_text_offsets", knowledge.data["script"])
        engine = Path(knowledge.path).with_name("engine.toml")
        self.assertIn(knowledge.path, source_files(engine))

    def test_malformed_knowledge_is_rejected(self):
        original = json.loads(Path("families/firered-cfru/script.json").read_text())
        for mutate in (
                lambda doc: doc["fixed_lengths"].update({"92": 1}),
                lambda doc: doc["text_offsets"].update({"15": [6]}),
                lambda doc: doc.update(version="invalid"),
                lambda doc: doc["discovery"]["bios"]["calls"]["6"].update(writes=[4]),
                lambda doc: doc["discovery"]["validation"].update(runtime_ranges=[[4, 3]]),
                lambda doc: doc["discovery"]["validation"].update(null_ends={"5": [6]}),
                lambda doc: doc.pop("discovery")):
            with tempfile.TemporaryDirectory() as temporary, self.subTest(mutate=mutate):
                root = Path(temporary)
                target = root / "firered-cfru"
                target.mkdir()
                document = copy.deepcopy(original)
                mutate(document)
                (target / "script.json").write_text(json.dumps(document))
                with self.assertRaises(ProfileError):
                    load_script("firered-cfru", root)
