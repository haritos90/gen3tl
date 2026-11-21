import json
import tempfile
import unittest
from pathlib import Path

from gen3tl.web import editordata, editorstore
from gen3tl.lang import language
from gen3tl.project import registry_scope
import editordata_support
from editordata_support import setUpModule  # noqa: F401
from editorstore_support import decoded

class StoreTest(editordata_support.SplitTest, unittest.TestCase):
    """Game parts once, each language what differs."""

    def setUp(self):
        self.enterContext(language.use(self.doc["language"]))
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(registry_scope(self.root))

    def write(self, name, index=None):
        out = self.root / "game" / name
        editordata.write_split(out, index or self.index, self.chunks,
                               source={"profile": "game", "rom_sha1": "ab" * 20, "rows": {"s0000100": [1, 2, "text", "plain"]}})
        return out

    def parts(self):
        return sorted(path.name for path in (self.root / "game" / editorstore.PARTS).iterdir())

    def test_languages_with_equal_facts_share_their_game_parts(self):
        first = self.write("ru")
        parts = self.parts()
        self.assertEqual(len(parts), len(self.chunks) + 2)   # the index part, the source rows, one per chunk
        second = self.write("copy")
        self.assertEqual(self.parts(), parts)
        stored = [json.loads((out / "index.json").read_text(encoding="utf-8")) for out in (first, second)]
        self.assertEqual(stored[0]["shared"], stored[1]["shared"])
        self.assertEqual([c["part"] for c in stored[0]["chunks"]], [c["part"] for c in stored[1]["chunks"]])
        for name in parts:   # named by their content
            data = (self.root / "game" / editorstore.PARTS / name).read_bytes()
            self.assertEqual(name, editorstore.hashlib.sha256(data).hexdigest() + ".json")
        self.assertEqual(decoded(second)["source"], {"schema": editordata.SCHEMA, "profile": "game",
                                                     "rom_sha1": "ab" * 20, "rows": {"s0000100": [1, 2, "text", "plain"]}})

    def test_stored_rows_and_entries_omit_their_defaults(self):
        stored = json.loads((self.write("ru") / "index.json").read_text(encoding="utf-8"))
        game = json.loads((self.root / "game" / "ru" / stored["shared"]).read_text(encoding="utf-8"))
        for row, entry in zip(stored["list"], (e for c in self.chunks for e in c["entries"])):
            self.assertEqual("status" in row, entry["status"] != editorstore.default_status(entry["translation"]))
            self.assertEqual("translation" in row, bool(entry["translation"]))
            self.assertNotIn("id", row)
        self.assertEqual([row["id"] for row in game["list"]], [row["id"] for row in self.index["list"]])
        self.assertEqual(game["messages"], editorstore.provenance.MESSAGES)
        chunk = json.loads((self.root / "game" / "ru" / "entries" / "0.json").read_text(encoding="utf-8"))
        for own in chunk["entries"]:
            self.assertFalse({"id", "file", "status", "translation", "window", "reach"} & own.keys())
            self.assertTrue(all(value not in ([], "") for value in own.values()))

    def test_parts_no_index_names_are_removed(self):
        self.write("ru")
        parts = set(self.parts())
        changed = json.loads(json.dumps(self.index))
        changed["where_texts"] = {"summary": {"ru": "сводка"}}
        self.write("ru", changed)
        written = set(self.parts())
        self.assertEqual(len(written - parts), 1)   # a new index part
        removed = [path.name for path in editorstore.prune(self.root)]
        self.assertEqual(len(removed), 1)
        self.assertIn(removed[0], parts)
        self.assertEqual(set(self.parts()), written - set(removed))
        self.assertEqual(decoded(self.root / "game" / "ru")["index"]["where_texts"], {"summary": {"ru": "сводка"}})

    def test_staged_parts_are_copied_once(self):
        staged = self.root / "stage" / "view"
        editordata.write_split(staged, self.index, self.chunks)
        target = self.root / "served"
        editorstore.copy_parts(staged.parent / editorstore.PARTS, target)
        copied = sorted(path.name for path in target.iterdir())
        self.assertEqual(copied, sorted(path.name for path in (staged.parent / editorstore.PARTS).iterdir()))
        before = {path.name: path.stat().st_mtime_ns for path in target.iterdir()}
        editorstore.copy_parts(staged.parent / editorstore.PARTS, target)
        self.assertEqual({path.name: path.stat().st_mtime_ns for path in target.iterdir()}, before)

    def test_identity_reads_the_envelope_without_decoding(self):
        out = self.write("ru")
        self.assertEqual(editorstore.identity(out / "index.json"),
                         {"schema": editordata.SCHEMA, "profile": self.index["profile"], "language": "ru",
                          "rom": self.index["rom"], "progress": editordata.progress_of(self.index["list"]),
                          "entries": len(self.index["list"])})
