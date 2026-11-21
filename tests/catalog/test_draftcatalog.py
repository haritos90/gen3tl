import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_roots import TOOLKIT_ROOT

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.catalog.catalog import load_catalog
from gen3tl.core.charmap import Charmap
from gen3tl.catalog.draftcatalog import editor_data, prepare
from gen3tl.catalog.po import read, write
from gen3tl.profile.profile import Profile
from gen3tl.core.rom import Rom, ptr_bytes


class DraftCatalogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.rom = Rom(b"\xff" * 512)
        raw = Charmap().encode("NEW GAME")
        self.rom.write(128, raw)
        self.rom.write(16, ptr_bytes(128))
        self.profile = Profile({"rom": {"sha1": self.rom.sha1()}, "support": {"stage": "catalog"},
                                "probe": {"menu": {"offset": 128, "text": "NEW GAME"}}},
                               Path("example.toml"))
        # entries carry their kind, as inventory.build writes them
        self.entry = {"id": "s0000080", "offset": 128, "length": len(raw), "text": "NEW GAME",
                      "kind": "pointer", "mode": "field", "category": "code", "flags": [],
                      "refs": [{"at": 16, "kind": "pool"}]}

    def prepare(self, entries=None):
        with patch("gen3tl.discover.inventory.build", return_value={"entries": entries or [self.entry]}):
            return prepare(self.profile, self.rom, "ru", self.root)

    def test_empty_reproducible_catalog_and_honest_editor_metadata(self):
        first = self.prepare()
        before = {p.name: p.read_bytes() for p in self.root.rglob("*.po")}
        self.assertEqual(self.prepare(), first)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.rglob("*.po")})
        catalog = load_catalog(self.root / "po")
        self.assertEqual(first["translations"], 0)
        self.assertTrue(all(not e.msgstr and e.fuzzy for _, e in catalog.values()))
        doc = editor_data(self.profile, "ru", catalog, first)
        self.assertFalse(doc["capabilities"]["build"])
        self.assertFalse(doc["capabilities"]["fonts"])
        self.assertEqual(doc["entries"][0]["reach"]["class"], "unknown")
        self.assertIsNone(doc["entries"][0]["window"])
        self.assertIn("native menu probe", " ".join(doc["entries"][0]["context"]["comments"]))

    def test_editor_data_preserves_tokens_without_source_text(self):
        source = "Hello {PLAYER}, {PLAYER}! {COLOR 01}"
        raw = Charmap().encode(source)
        self.rom.write(128, raw)
        self.profile.data["rom"]["sha1"] = self.rom.sha1()
        self.entry.update(text=source, length=len(raw))
        manifest = self.prepare()
        doc = editor_data(self.profile, "ru", load_catalog(self.root / "po"), manifest)
        entry = doc["entries"][0]
        self.assertNotIn("source_text", entry)
        self.assertEqual(entry["tokens"], ["{PLAYER}", "{PLAYER}", "{COLOR 01}"])
        self.assertNotIn(source, json.dumps(doc))

    def test_existing_drafts_survive_regeneration(self):
        self.prepare()
        path = self.root / "po/source/code.po"
        header, entries = read(path)
        entries[0].msgstr = "Draft"
        entries[0].translator = ["Context note"]
        write(path, entries, header)
        self.assertEqual(self.prepare()["translations"], 1)
        _, after = read(path)
        self.assertEqual(after[0].msgstr, "Draft")
        self.assertEqual(after[0].translator, ["Context note"])
        changed = {**self.entry, "flags": ["overlap"]}
        with self.assertRaisesRegex(ValueError, "translated source changed"):
            self.prepare([changed])

    def test_source_identity_and_pointer_mismatches_fail_closed(self):
        self.profile.data["rom"]["sha1"] = "0" * 40
        with self.assertRaisesRegex(ValueError, "identity differs"):
            self.prepare()
        self.profile.data["rom"]["sha1"] = self.rom.sha1()
        bad = {**self.entry, "refs": [{"at": 20, "kind": "pool"}]}
        with self.assertRaisesRegex(ValueError, "references differ"):
            self.prepare([bad])
        manifest = self.prepare()
        catalog = load_catalog(self.root / "po")
        with self.assertRaisesRegex(ValueError, "identity differs"):
            editor_data(self.profile, "de", catalog, manifest)
        catalog[self.entry["id"]][1].msgid = "Different source"
        with self.assertRaisesRegex(ValueError, "source entries differ"):
            editor_data(self.profile, "ru", catalog, manifest)

    def test_uncertain_entries_excluded_and_build_commands_stay_disabled(self):
        unsure = {**self.entry, "id": "uncertain", "flags": ["unverified"]}
        overlap = {**self.entry, "id": "overlap", "flags": ["overlap"]}
        manifest = self.prepare([self.entry, unsure, overlap])
        self.assertEqual(manifest["entries"], 1)
        self.assertEqual(manifest["excluded"], {"overlap": 1, "unverified": 1})
        for command in ("catalog prepare", "web data", "catalog progress", "check po"):
            self.assertIsNone(self.profile.command_problem(command))
        for command in ("build rom", "build patch", "catalog batch-approve", "check qa", "verify save-convert", "catalog export"):
            self.assertIn("verified translation support", self.profile.command_problem(command))


class CatalogExportCliTest(unittest.TestCase):
    def test_cli_split_and_unsplit_keep_source_metadata_without_game_text(self):
        import io
        from contextlib import redirect_stdout

        from gen3tl.cli.main import main
        from project_fixture import CATALOG_STAGE, SAMPLE
        from editorstore_support import decoded
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            args = ["--project-root", str(CATALOG_STAGE), "--profile", SAMPLE, "--lang", "ru", "web", "data"]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(args + ["--out", str(out / "catalog.json")]), 0)
                self.assertEqual(main(args + ["--split", "--out", str(out / "data" / SAMPLE / "ru")]), 0)
            doc = json.loads((out / "catalog.json").read_text())
            directory = out / "data" / SAMPLE / "ru"
            data = decoded(directory)
            index = data["index"]
            entries = [entry for chunk in data["chunks"] for entry in chunk["entries"]]
            self.assertEqual({entry["id"]: entry for entry in entries},
                             {entry["id"]: entry for entry in doc["entries"]})
            self.assertTrue(all("source_text" not in entry and isinstance(entry["tokens"], list) for entry in entries))
            self.assertTrue(any(entry["tokens"] == ["{PLAYER}"] for entry in entries))
            self.assertTrue(all("source_text" not in row for row in index["list"]))
            self.assertEqual(index["capabilities"]["check"], "tokens")


if __name__ == "__main__":
    unittest.main()
