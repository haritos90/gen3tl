from pathlib import Path
import json
import re
import tempfile
import unittest

from gen3tl.web import editordata
from gen3tl.lang import language
from gen3tl.profile.profile import Profile
from gen3tl.core import notes as structured
from gen3tl.project import registry_scope
from project_fixture import SAMPLE
import editordata_support
from editordata_support import TOP_KEYS, games_only, setUpModule
from editorstore_support import decoded

class SplitTest(editordata_support.SplitTest, unittest.TestCase):
    def setUp(self):
        self.enterContext(language.use(self.doc["language"]))
        self.enterContext(registry_scope(Path(self.enterContext(tempfile.TemporaryDirectory()))))

    def test_index(self):
        index = json.loads(json.dumps(self.index, ensure_ascii=False))   # plain JSON
        self.assertEqual(set(index), TOP_KEYS - {"entries"} | {"version", "glyphs", "codec", "chunks", "list",
                                                               "terms"})
        self.assertIsNone(index["terms"])   # split without terms_data
        for key in ("schema", "language", "profile", "rom", "fonts", "charmap", "placeholders", "glossary",
                    "graphics", "map_order"):
            self.assertEqual(index[key], self.doc[key])
        self.assertEqual(index["version"], "1.2.3")

    def test_chunks_per_file(self):
        self.assertEqual([c["file"] for c in self.index["chunks"]],
                         list(dict.fromkeys(e["file"] for e in self.doc["entries"])))
        self.assertEqual([c["path"] for c in self.index["chunks"]], ["entries/0.json", "entries/1.json",
                                                                     "entries/2.json", "entries/3.json"])
        self.assertEqual([e for c in self.chunks for e in c["entries"]], self.doc["entries"])
        for c, n in zip(self.chunks, self.index["chunks"]):
            self.assertEqual((c["file"], len(c["entries"])), (n["file"], n["entries"]))

    def test_list_rows(self):
        rows = self.index["list"]
        self.assertEqual([r["id"] for r in rows], [e["id"] for e in self.doc["entries"]])
        for r, e in zip(rows, self.doc["entries"]):
            self.assertEqual(set(r), {"id", "chunk", "status", "translation", "kind", "reach", "maps", "problems"})
            self.assertEqual(self.chunks[r["chunk"]]["file"], e["file"])
            self.assertEqual((r["status"], r["translation"], r["kind"]),
                             (e["status"], e["translation"], e["window"]["kind"]))
            self.assertEqual(r["reach"], e["reach"]["class"])
            self.assertEqual(r["maps"], e["context"]["maps"])
        by_id = {r["id"]: r for r in rows}
        self.assertEqual(by_id["s0000400"]["problems"], [])   # untranslated: not checked
        self.assertNotIn("s0000400", self.problems)

    def test_problems_are_the_check(self):
        for sid, probs in self.problems.items():
            e = self.by_id[sid]
            self.assertEqual(probs, self.checker.check(e, e["translation"])["problems"])
        # 6 px Latin widths: "Сумка" fits its 76 px window
        self.assertEqual(self.problems["s0000300"], [])
        long = self.checker.check(self.by_id["s0000300"], "Сумка " * 6)["problems"]
        self.assertTrue(any(p["message"]["code"] == "line_wide" for p in long), long)

    def test_glyph_art(self):
        from gen3tl.lang.langpack import parse_art
        self.assertEqual({s: (a["top"], a["rows"]) for s, a in self.art.items()},
                         {"unbound": (0, 14), "unbound-small": (1, 12)})
        for style, a in self.art.items():
            slots = self.packs[style].glyph_slots()
            self.assertEqual(set(a["art"]), {f"{b:02X}" for b in slots})
            self.assertEqual(set(a["art"]), set(self.doc["fonts"]["styles"][style]["pack"]))
            for b, g in slots.items():
                rows = a["art"][f"{b:02X}"]
                head = f"slot {g.char:03X}" if isinstance(g.char, int) else f"glyph {g.char}"
                back = parse_art(f"{head} width {g.width}\n" + "\n".join(rows) + "\n", a["rows"], a["top"])
                self.assertEqual(back[g.char].pixels, g.pixels)

    def test_codec(self):
        from gen3tl.core.charmap import Charmap
        codec = self.index["codec"]
        cm = Charmap()
        for c, b in codec["chars"].items():
            self.assertEqual(cm.encode(c, terminate=False), bytes([int(b, 16)]))
        for n, b in codec["named"].items():
            self.assertEqual(cm.encode("{" + n + "}", terminate=False), bytes([int(b, 16)]))
        for n, (code, k) in codec["controls"].items():
            self.assertEqual(cm.encode("{" + " ".join([n] + ["00"] * k) + "}", terminate=False)[:2],
                             bytes([0xFC, int(code, 16)]))
        for n, b in codec["placeholders"].items():
            self.assertEqual(cm.encode("{" + n + "}", terminate=False), bytes([0xFD, int(b, 16)]))
        self.assertTrue(re.match(codec["zero"], "{COLOR 06}"))
        self.assertFalse(re.match(codec["zero"], "{PLAYER}"))


    def test_write_rejects_source_text_fields(self):
        fields = ("english", "msgid", "source_text", "original_text")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            index = json.loads(json.dumps(self.index))
            index["glossary"] = {"term": {field: "source" for field in fields}}
            index["keyboard"] = {"rows": [{field: "source" for field in fields}]}
            editordata.write_split(root / "valid", index, self.chunks)
            for location in ("index", "chunk"):
                for field in fields:
                    index = json.loads(json.dumps(self.index))
                    chunks = json.loads(json.dumps(self.chunks))
                    if location == "index":
                        index["list"][0]["problems"].append({field: "source"})
                        path = f"index.list[0].problems[0].{field}"
                    else:
                        inserted = chunks[0]["entries"][0]["context"]["inserted"]
                        path = f"entries/0.json.entries[0].context.inserted[{len(inserted)}].{field}"
                        inserted.append({field: "source"})
                    with self.subTest(location=location, field=field), self.assertRaises(ValueError) as caught:
                        editordata.write_split(root / "invalid", index, chunks)
                    self.assertEqual(str(caught.exception), f"{path}: forbidden editor source-text field")
                    self.assertFalse((root / "invalid").exists())

    def test_write_preserves_citation_column_names_without_allowing_source_text(self):
        from gen3tl.lang import research
        finding = research.Row({"record_id": "finding:example"})
        note = research.term_note("term:example", finding, "research-citation", [],
                                  citation={"label": "Reference", "columns": {"source_text": "attested"}})
        index = json.loads(json.dumps(self.index))
        index["notes"] = {"schema": structured.SCHEMA, "note": [note]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            editordata.write_split(root / "valid", index, self.chunks)
            written = json.loads((root / "valid" / "index.json").read_text())
            self.assertEqual(written["notes"], index["notes"])
            for field in ("english", "msgid", "original_text"):
                with self.subTest(field=field):
                    note["citation"]["columns"][field] = "source"
                    with self.assertRaisesRegex(ValueError, "forbidden editor source-text field"):
                        editordata.write_split(root / "invalid", index, self.chunks)
                    del note["citation"]["columns"][field]
            note["citation"]["source_text"] = "Source dialogue."
            with self.assertRaisesRegex(ValueError, "forbidden editor source-text field"):
                editordata.write_split(root / "invalid", index, self.chunks)
            self.assertFalse((root / "invalid").exists())

    def test_written_form_decodes_to_the_split_records(self):
        """The stored files decode in the page (web/js/editorstore.js) to the records split made, keys in order."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "game" / "ru"
            editordata.write_split(out, self.index, self.chunks)
            data = decoded(out)
            stored = json.loads((out / "index.json").read_text(encoding="utf-8"))
        expected = json.loads(json.dumps(self.index, ensure_ascii=False))
        index = data["index"]
        self.assertEqual(index.pop("progress"), editordata.progress_of(expected["list"]))
        self.assertEqual(json.dumps(index, ensure_ascii=False, sort_keys=True),
                         json.dumps(expected, ensure_ascii=False, sort_keys=True))
        self.assertEqual([json.dumps(e, ensure_ascii=False) for c in data["chunks"] for e in c["entries"]],
                         [json.dumps(e, ensure_ascii=False) for c in self.chunks for e in c["entries"]])
        # explanations are codes; every rule text comes from the message table
        rules = [s["rule"] for s in stored["limit_sources"]]
        self.assertTrue(rules and all(isinstance(rule, dict) and "code" in rule for rule in rules))
        self.assertNotIn("measured in game", json.dumps(stored))

    def test_toolkit_fingerprint_follows_the_sources_not_the_commit(self):
        """The key of generated data moves with the code, not with the repository's HEAD."""
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            (package / "a.py").write_text("x = 1\n")
            (package / "__pycache__").mkdir()
            (package / "__pycache__" / "a.pyc").write_bytes(b"\0")
            first = editordata.toolkit_fingerprint(package)
            self.assertEqual(editordata.toolkit_fingerprint(package), first)
            self.assertEqual(len(first), 64)
            (package / "__pycache__" / "a.pyc").write_bytes(b"\1")
            self.assertEqual(editordata.toolkit_fingerprint(package), first)   # caches do not count
            (package / "a.py").write_text("x = 2\n")
            self.assertNotEqual(editordata.toolkit_fingerprint(package), first)
        real = editordata.toolkit_fingerprint()
        self.assertEqual(editordata.toolkit_fingerprint(), real)
        self.assertNotIn("+", real)   # no commit, no dirty flag


    def test_manifest_carries_local_patch_identities(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "fixture.toml"
            path.write_text('[rom]\nname = "Fixture"\nsha1 = "' + 'a' * 40 + '"\nsize = 32\n'
                            'source_locale = "en_US"\n'
                            '[[rom.patch_bases]]\nname = "Clean base"\nsha1 = "' + 'b' * 40 + '"\n'
                            'source = "identity reference"\n', encoding="utf-8")
            with registry_scope(root), patch("gen3tl.profile.profile.profile_paths", return_value=[path]):
                doc = editordata.write_languages(root)
            profile = next(p for p in doc["profiles"] if p["id"] == "fixture")
            self.assertEqual(profile["sha1"], "a" * 40)
            self.assertEqual(profile["size"], 32)
            self.assertEqual(profile["source_locale"], "en_US")
            self.assertEqual(profile["patch_bases"], [{"name": "Clean base", "sha1": "b" * 40}])


    def test_draft_sources_do_not_become_translation_languages(self):
        import hashlib

        from gen3tl.profile.profile import Profile
        prof = Profile.by_name(SAMPLE)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "sources" / prof.name / "catalog.json"
            path.parent.mkdir(parents=True)
            path.with_name("source.pot").write_bytes(b"source")
            source = {"profile": prof.name, "rom_sha1": prof.sha1,
                      "status": "unverified-candidates", "entries": 10,
                      "source_sha256": hashlib.sha256(b"source").hexdigest()}
            path.write_text(json.dumps(source))
            with registry_scope(root):
                doc = editordata.write_languages(root)
            self.assertEqual(games_only(doc["languages"]), [])
            profile = next(p for p in doc["profiles"] if p["id"] == prof.name)
            self.assertEqual(profile["source_catalog"]["entries"], 10)
            self.assertEqual(profile["source_catalog"]["pot"], f"sources/{prof.name}/source.pot")
            source["rom_sha1"] = "wrong"
            path.write_text(json.dumps(source))
            with self.assertRaisesRegex(ValueError, "identity or status"), registry_scope(root):
                editordata.write_languages(root)
