"""The discovery metadata file: grouped window bindings, repeated texts and keys once, exact reading."""

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


def named(collection: dict) -> list[dict]:
    """The rows of a stored collection under their keys, nulls kept."""
    return [dict(zip(collection["keys"], row)) for row in collection["rows"]]


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

    def store_text(self, document: dict) -> Path:
        self.path.write_text(json.dumps(document), encoding="utf-8")
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
        self.assertEqual(named(stored["windows"]["bindings"]), [
            {"window": "field-message", "strings": ["s0000010", "s0000040"], "consumers": None},
            {"window": "battle-message", "strings": ["s0000030"], "consumers": {"s0000030": [1]}}])
        self.assertEqual(list(stored), ["schema", "texts", "identity", "families", "scripts", "windows", "comparison"])

    def test_repeated_prose_is_stored_once_and_codes_and_single_texts_stay(self):
        text = metadata.encode(run_metadata())
        stored = json.loads(text)
        self.assertEqual(stored["texts"], ["no derived length", FLOW, MISSING])
        self.assertEqual(text.count(FLOW), 1)
        handler = named(stored["scripts"]["handlers"])[0]
        self.assertEqual((handler["assumption"], handler["assumptions"], handler["reasons"]),
                         ("arguments-only", ["arguments-only"], [0]))
        audit = named(stored["windows"]["audit"]["strings"])
        self.assertEqual(audit[0]["consumers"][0]["evidence"]["assumption"], 1)
        self.assertEqual(audit[4]["unresolved"][0]["reasons"], [2, "Only here."])
        self.assertEqual(stored["windows"]["unresolved"][0]["reason"], "Ambiguous.")

    def test_a_collection_stores_its_keys_once_and_a_row_per_record(self):
        roots = [{"map": "1.0", "event": "npc", "script": 0x10, "local_id": 1, "tier": "shape", "method": "pointer"},
                 {"map": "1.0", "event": "sign", "script": 0x20, "tier": "shape", "method": "pointer"},
                 {"map": "1.1", "event": "npc", "script": 0x30, "local_id": 2, "tier": "shape", "method": "pointer"}]
        single, nested = [{"map": "2.0", "event": "npc"}], [[{"map": "3.0"}, {"map": "3.1"}]]
        document = {"maps": {"roots": roots, "single": single, "nested": nested},
                    "strings": {"outcomes": [{"name": "a", "length": 1, "anchors": []},
                                             {"name": "b", "anchors": [], "length": 2}],
                                "cleared": [{"name": "c", "value": None}, {"name": "d", "value": 1}]},
                    "scripts": {"handlers": [{"opcode": 1, "paths": [{"length": 1}, {"length": 2}]},
                                             {"opcode": 2, "paths": []}]}}
        text = metadata.encode(document)
        stored = json.loads(text)
        self.assertEqual(stored["maps"]["roots"], {
            "keys": ["map", "event", "script", "local_id", "tier", "method"],
            "rows": [["1.0", "npc", 0x10, 1, "shape", "pointer"], ["1.0", "sign", 0x20, None, "shape", "pointer"],
                     ["1.1", "npc", 0x30, 2, "shape", "pointer"]]})
        self.assertEqual(text.count('"method"'), 1)
        # one record, a list of lists, two key orders and a null stay records
        self.assertEqual((stored["maps"]["single"], stored["maps"]["nested"]), (single, nested))
        self.assertEqual(stored["strings"], document["strings"])
        # records inside a row keep their keys
        self.assertEqual(stored["scripts"]["handlers"]["rows"][0], [1, [{"length": 1}, {"length": 2}]])
        self.assertEqual(json.dumps(metadata.read(self.store(document))), json.dumps(document))

    def test_records_rows_and_texts_stand_one_per_line(self):
        text = metadata.encode(run_metadata())
        lines = [line.strip().removesuffix(",") for line in text.splitlines()]
        audit = json.loads(text)["windows"]["audit"]["strings"]
        self.assertIn('"keys": ["id","offset","consumers","unresolved"]', lines)
        for row in audit["rows"]:
            self.assertIn(json.dumps(row, ensure_ascii=False, separators=(",", ":")), lines)
        self.assertIn(json.dumps(FLOW), lines)
        self.assertIn('"identity": {"sha1":"' + "00" * 20 + '","size":4096}', lines)
        self.assertTrue(text.endswith("}\n"))

    def test_writing_refuses_ambiguous_documents(self):
        for document, message in (({"schema": 1}, "reserved"), ({"texts": []}, "reserved"),
                                  ({"scripts": {"keys": [], "rows": []}}, "reserved"),
                                  ({"scripts": {"reason": 4}}, "holds a number")):
            with self.subTest(document=document), self.assertRaisesRegex(ValueError, message):
                metadata.encode(document)
        document = run_metadata()
        document["windows"]["bindings"][0]["consumers"] = [consumer("ee", 0x500)]
        with self.assertRaisesRegex(ValueError, "outside the audit"):
            metadata.encode(document)

    def test_reading_refuses_damaged_files(self):
        stored = json.loads(metadata.encode(run_metadata()))
        self.refused({**stored, "schema": "3.0.0"}, "needs schema 2.0.0")
        self.refused({**stored, "texts": [1]}, "must list texts")
        self.refused({**stored, "scripts": {"reason": 9}}, "unknown text 9")
        for collection in ({"keys": ["a", "a"], "rows": [[1, 2]]}, {"keys": ["a", 1], "rows": []},
                           {"keys": ["a", "b"], "rows": [[1]]}, {"keys": ["a"], "rows": {"a": 1}}):
            with self.subTest(collection=collection):
                self.refused({**stored, "scripts": {"handlers": collection}}, "a collection needs distinct keys")
        for group, message in (({"window": "w", "strings": ["s0000099"]}, "unknown or repeated"),
                               ({"window": "w", "strings": ["s0000010"]}, "unknown or repeated"),
                               ({"window": "w", "strings": ["s0000020"], "consumers": {"s0000020": [0]}},
                                "invalid consumer positions"),
                               ({"window": "w", "strings": "s0000020"}, "invalid window binding group")):
            bindings = [{key: value for key, value in row.items() if value is not None}
                        for row in named(stored["windows"]["bindings"])]
            windows = {**stored["windows"], "bindings": [*bindings, group]}
            with self.subTest(group=group):
                self.refused({**stored, "windows": windows}, message)
        self.path.write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(metadata.MetadataError, "cannot read discovery metadata"):
            metadata.read(self.path)

    def test_the_earlier_schema_keeps_the_keys_of_every_record(self):
        document = {"maps": {"roots": [{"map": "1.0", "reason": 0}, {"map": "1.1", "reason": 0}],
                             "grid": {"keys": ["a"], "rows": [[1]]}}}
        found = metadata.read(self.store_text({"schema": metadata.KEYED, "texts": ["Text once."], **document}))
        self.assertEqual(found, {"maps": {"roots": [{"map": "1.0", "reason": "Text once."},
                                                    {"map": "1.1", "reason": "Text once."}],
                                          "grid": {"keys": ["a"], "rows": [[1]]}}})

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
