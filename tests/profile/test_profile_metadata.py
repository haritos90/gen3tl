"""The discovery metadata file: grouped window bindings, repeated texts once, exact reading."""

import json
import tempfile
import unittest
from pathlib import Path

from gen3tl.profile import metadata, schema
from gen3tl.profile.profile import source_files
from profile_schema_support import GENERATED, write_dir

FLOW = "Argument flow reaches the printer; saved registers follow the call convention."
MISSING = "No literal value reaches a fingerprinted printer argument."


def consumer(function: str, call: int) -> dict:
    return {"function": function, "slot": "call:4:r2", "tier": "shape",
            "evidence": {"method": "literal-load", "call": call, "assumption": FLOW}}


def run_metadata() -> dict:
    first, second, third = consumer("aa", 0x100), consumer("bb", 0x200), consumer("cc", 0x300)
    audit = [
        {"id": "s0000010", "offset": 0x10, "consumers": [first], "unresolved": []},
        {"id": "s0000020", "offset": 0x20, "consumers": [],
         "unresolved": [{"at": 0x40, "kind": "literal-pool", "reasons": [MISSING]}]},
        {"id": "s0000030", "offset": 0x30, "consumers": [second, third], "unresolved": []},
        {"id": "s0000040", "offset": 0x40, "consumers": [third], "unresolved": []},
        {"id": "s0000050", "offset": 0x50, "consumers": [],
         "unresolved": [{"at": 0x44, "kind": "literal-pool", "reasons": [MISSING, "Only here."]}]},
    ]
    bindings = [
        {"string": "s0000010", "window": "field-message", "consumers": [first]},
        {"string": "s0000030", "window": "battle-message", "consumers": [third]},
        {"string": "s0000040", "window": "field-message", "consumers": [third]},
    ]
    return {"identity": {"sha1": "00" * 20, "size": 4096},
            "families": [{"name": "fixture", "ratio": 1.0, "selected": True}],
            "scripts": {"handlers": [{"opcode": 1, "assumptions": ["arguments-only"], "assumption": "arguments-only",
                                      "reasons": ["no derived length"]},
                                     {"opcode": 2, "assumption": "arguments-only", "reasons": ["no derived length"]}]},
            "windows": {"bindings": bindings, "unresolved": [{"function": "dd", "reason": "Ambiguous."}],
                        "audit": {"strings": audit, "resolved": 3}},
            "comparison": {"available": False}}


class MetadataFileTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / schema.DISCOVERY

    def store(self, document: dict) -> Path:
        self.path.write_text(metadata.encode(document), encoding="utf-8")
        return self.path

    def refused(self, document: dict, message: str) -> None:
        self.path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(metadata.MetadataError, message):
            metadata.read(self.path)

    def test_reading_returns_the_metadata_in_its_order_and_types(self):
        expected = run_metadata()
        found = metadata.read(self.store(expected))
        self.assertEqual(json.dumps(found), json.dumps(expected))
        self.assertIs(type(found["families"][0]["ratio"]), float)

    def test_bindings_stand_under_their_window_and_name_their_consumers_by_position(self):
        stored = json.loads(metadata.encode(run_metadata()))
        self.assertEqual(stored["windows"]["bindings"], [
            {"window": "field-message", "strings": ["s0000010", "s0000040"]},
            {"window": "battle-message", "strings": ["s0000030"], "consumers": {"s0000030": [1]}}])
        self.assertEqual(list(stored), ["schema", "texts", "identity", "families", "scripts", "windows", "comparison"])

    def test_repeated_prose_is_stored_once_and_codes_and_single_texts_stay(self):
        text = metadata.encode(run_metadata())
        stored = json.loads(text)
        self.assertEqual(stored["texts"], ["no derived length", FLOW, MISSING])
        self.assertEqual(text.count(FLOW), 1)
        handler = stored["scripts"]["handlers"][0]
        self.assertEqual((handler["assumption"], handler["assumptions"], handler["reasons"]),
                         ("arguments-only", ["arguments-only"], [0]))
        audit = stored["windows"]["audit"]["strings"]
        self.assertEqual(audit[0]["consumers"][0]["evidence"]["assumption"], 1)
        self.assertEqual(audit[4]["unresolved"][0]["reasons"], [2, "Only here."])
        self.assertEqual(stored["windows"]["unresolved"][0]["reason"], "Ambiguous.")

    def test_records_stand_one_per_line(self):
        text = metadata.encode(run_metadata())
        lines = [line.strip().removesuffix(",") for line in text.splitlines()]
        records = json.loads(text)["windows"]["audit"]["strings"]
        for record in records:
            self.assertIn(json.dumps(record, ensure_ascii=False, separators=(",", ":")), lines)
        self.assertIn('"identity": {"sha1":"' + "00" * 20 + '","size":4096}', lines)
        self.assertTrue(text.endswith("}\n"))

    def test_writing_refuses_ambiguous_documents(self):
        for document, message in (({"schema": 1}, "reserved"), ({"texts": []}, "reserved"),
                                  ({"scripts": {"reason": 4}}, "holds a number")):
            with self.subTest(document=document), self.assertRaisesRegex(ValueError, message):
                metadata.encode(document)
        document = run_metadata()
        document["windows"]["bindings"][0]["consumers"] = [consumer("ee", 0x500)]
        with self.assertRaisesRegex(ValueError, "outside the audit"):
            metadata.encode(document)

    def test_reading_refuses_damaged_files(self):
        stored = json.loads(metadata.encode(run_metadata()))
        self.refused({**stored, "schema": "2.0.0"}, "needs schema 1.0.0")
        self.refused({**stored, "texts": [1]}, "must list texts")
        self.refused({**stored, "scripts": {"reason": 9}}, "unknown text 9")
        for group, message in (({"window": "w", "strings": ["s0000099"]}, "unknown or repeated"),
                               ({"window": "w", "strings": ["s0000010"]}, "unknown or repeated"),
                               ({"window": "w", "strings": ["s0000020"], "consumers": {"s0000020": [0]}},
                                "invalid consumer positions"),
                               ({"window": "w", "strings": "s0000020"}, "invalid window binding group")):
            windows = {**stored["windows"], "bindings": [*stored["windows"]["bindings"], group]}
            with self.subTest(group=group):
                self.refused({**stored, "windows": windows}, message)
        self.path.write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(metadata.MetadataError, "cannot read discovery metadata"):
            metadata.read(self.path)

    def test_the_loader_joins_the_file_to_the_profile_header(self):
        profile = write_dir(self.root)
        self.assertEqual(schema.read(profile).header, {"schema": 1})
        self.assertNotIn(profile / schema.DISCOVERY, source_files(profile))
        (profile / schema.DISCOVERY).write_text(metadata.encode(run_metadata()), encoding="utf-8")
        header = schema.read(profile).header
        self.assertEqual(json.dumps(header), json.dumps({"schema": 1, **run_metadata()}))
        self.assertIn(profile / schema.DISCOVERY, source_files(profile))
        (profile / schema.GENERATED).write_text(GENERATED.replace("schema = 1\n", 'schema = 1\ncodec = "x"\n', 1),
                                                encoding="utf-8")
        (profile / schema.DISCOVERY).write_text(metadata.encode({"codec": "y"}), encoding="utf-8")
        with self.assertRaisesRegex(schema.ProfileError, "codec also in"):
            schema.read(profile)
        (profile / schema.DISCOVERY).write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(schema.ProfileError, "needs schema"):
            schema.read(profile)


if __name__ == "__main__":
    unittest.main()
