"""The Pokedex page's texts file."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gen3tl.web import editordata
from gen3tl.data import pokedextexts
from gen3tl.project import registry_scope


def row(sid, english, translation):
    return {"id": sid, "chunk": 0, "status": "approved", "source_text": english, "translation": translation,
            "kind": None, "maps": [], "reach": "reachable", "problems": []}


DATASET = {
    "species": [{"slot": 1, "name": "species:1", "category": "pokedex_categories:1", "entry": ["s0444CB2"],
                 "types": [12, 3]}],
    "map_sections": [{"id": 88, "name": "s0720F5C"}],
    "moves": [{"id": 1, "name": "moves:1", "description": "s0484871"}],
}
ROWS = [row("species:1", "Bulbasaur", "Бульбазавр"), row("species:2", "Ivysaur", ""),
        row("pokedex_categories:1", "Seed", "Семя"), row("types:12", "Grass", "Трава"),
        row("s0444CB2", "An entry", "Запись"), row("s0720F5C", "Route 1", ""), row("s0484871", "A move", ""),
        row("s0000001", "Dialogue", "Диалог"), row("maps:3", "A map", "Карта"), row("items:5", "Potion", "Зелье")]
INDEX = {"schema": 1, "profile": "test", "language": "ru", "list": ROWS, "chunks": []}
# key-only rows: the key replaces the English
KEYED = [{**r, "source_text": r["id"]} for r in ROWS]


class PokedexTextsTest(unittest.TestCase):
    def test_selects_the_families_whole_and_every_named_id(self):
        texts = pokedextexts.select(DATASET, ROWS)
        self.assertEqual(list(texts), ["items:5", "pokedex_categories:1", "s0444CB2", "s0484871", "s0720F5C",
                                       "species:1", "species:2", "types:12"])
        self.assertEqual(texts["species:1"], ["Bulbasaur", "Бульбазавр"])
        self.assertEqual(texts["species:2"], ["Ivysaur", ""])   # the family, not only the named slot
        self.assertNotIn("s0000001", texts)   # dialogue the dataset never names
        self.assertNotIn("maps:3", texts)   # a family the page does not show
        self.assertEqual(pokedextexts.referenced(DATASET),
                         {"species:1", "pokedex_categories:1", "s0444CB2", "s0720F5C", "moves:1", "s0484871"})

    def test_key_only_rows_take_the_english_by_key_or_none(self):
        texts = pokedextexts.select(DATASET, KEYED)
        self.assertEqual((texts["species:1"], texts["s0444CB2"]), (["", "Бульбазавр"], ["", "Запись"]))
        english = {r["id"]: r["source_text"] for r in ROWS}
        self.assertEqual(pokedextexts.select(DATASET, KEYED, english=lambda: english),
                         pokedextexts.select(DATASET, ROWS))
        self.assertEqual(pokedextexts.select(DATASET, [{**r, "source_text": None} for r in ROWS])["types:12"],
                         ["", "Трава"])

    def test_keyed_language_catalogs_share_the_rom_inventory_references(self):
        from gen3tl.catalog.keys import Aliases
        aliases = Aliases([("table:species/1", 0x100, "species:1"),
                           ("table:pokedex_entries/1", 0x444CB2, "s0444CB2")])
        for language, translated in (("ru", "Бульбазавр"), ("fr", "Bulbizarre")):
            with self.subTest(language=language):
                rows = [{**r, "id": aliases.key(r["id"]),
                         "translation": translated if r["id"] == "species:1" else ""} for r in ROWS]
                selected = pokedextexts.select(DATASET, rows, aliases)
                self.assertEqual(selected["species:1"], ["Bulbasaur", translated])
                self.assertEqual(selected["s0444CB2"], ["An entry", ""])
                self.assertNotIn("table:species/1", selected)
                self.assertNotIn("table:pokedex_entries/1", selected)
        self.assertEqual(DATASET["species"][0]["name"], "species:1")

    def test_the_english_source_catalog_gives_the_same_document(self):
        from gen3tl.catalog import extract
        from gen3tl.catalog.po import PoEntry, write as write_po
        with tempfile.TemporaryDirectory() as tmp:
            pot = Path(tmp) / "catalog.pot"
            write_po(pot, [PoEntry(r["id"], r["source_text"]) for r in ROWS],
                     {"Project-Id-Version": "test", "X-ROM-SHA1": "ab" * 20}, [])
            keyed = {**INDEX, "list": KEYED, "rom": {"sha1": "ab" * 20}}
            with patch.object(extract, "path", return_value=pot):
                doc = pokedextexts.document(keyed, DATASET, "pokedex/test/pokedex.json")
            self.assertEqual(doc, pokedextexts.document({**INDEX, "rom": {"sha1": "ab" * 20}}, DATASET,
                                                        "pokedex/test/pokedex.json"))
            with patch.object(extract, "path", return_value=Path(tmp) / "missing.pot"):
                doc = pokedextexts.document(keyed, DATASET, "pokedex/test/pokedex.json")
            self.assertEqual({english for english, _ in doc["texts"].values()}, {""})
            self.assertEqual(doc["texts"]["species:1"], ["", "Бульбазавр"])
            other = {**keyed, "rom": {"sha1": "cd" * 20}}   # the English of another ROM
            with patch.object(extract, "path", return_value=pot), self.assertRaisesRegex(ValueError, "another ROM"):
                pokedextexts.document(other, DATASET, "pokedex/test/pokedex.json")

    def test_document_carries_identity_and_draft(self):
        doc = pokedextexts.document(INDEX, DATASET, "pokedex/test/pokedex.json")
        self.assertEqual((doc["schema"], doc["profile"], doc["language"], doc["dataset"], doc["draft"]),
                         (1, "test", "ru", "pokedex/test/pokedex.json", False))
        self.assertNotIn("stage", doc)
        draft = pokedextexts.document({**INDEX, "capabilities": {"check": "tokens"}},
                                      DATASET, "pokedex/test/pokedex.json")
        self.assertEqual(draft["draft"], True)
        self.assertNotIn("stage", draft)

    def test_write_is_deterministic_and_needs_a_dataset(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "pokedex.json"
            source.write_text(json.dumps(DATASET), encoding="utf-8")
            out = Path(tmp) / "test" / "ru"
            first = pokedextexts.write(out, INDEX, source)
            self.assertEqual(first, out / "pokedex-texts.json")
            data = first.read_bytes()
            self.assertEqual(pokedextexts.write(out, INDEX, source).read_bytes(), data)
            doc = json.loads(data.decode("utf-8"))
            self.assertEqual(doc["texts"]["s0720F5C"], ["Route 1", ""])
            self.assertEqual(list(doc), sorted(doc))   # sorted keys, byte-identical rebuilds
            self.assertNotIn("page", doc)
            # the Pokédex label catalog rides along as `page`
            from gen3tl.catalog.po import PoEntry, write as write_po
            po = Path(tmp) / "pokedex-labels.po"
            write_po(po, [PoEntry("page:PP", "PP", "ОС"), PoEntry("page:Catch rate", "Catch rate", ""),
                          PoEntry("page:power:return", "Power from friendship", "Сила по дружбе"),
                          PoEntry("page:power:flail", "Power from remaining HP", ""),
                          PoEntry("page:mechanics:demo:condition", "Current user type", ""),
                          PoEntry("page:mechanics:demo:effect", "User gains Attack", "Повышается атака")], {"Language": "ru"})
            with_page = json.loads(pokedextexts.write(out, INDEX, source, page_catalog=po).read_text(encoding="utf-8"))
            self.assertEqual(with_page["page"], {"PP": "ОС",
                                               "page:power:return": ["Power from friendship", "Сила по дружбе"],
                                               "page:power:flail": ["Power from remaining HP", ""],
                                               "page:mechanics:demo:condition": ["Current user type", ""],
                                               "page:mechanics:demo:effect": ["User gains Attack", "Повышается атака"]})
            self.assertEqual(pokedextexts.page_labels(Path(tmp) / "missing.po"), {})
            self.assertIsNone(pokedextexts.write(out, {**INDEX, "profile": "no-such-profile"}))

    def test_manifest_lists_the_texts_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            editordata.write_split(root / "test" / "ru", {**INDEX, "list": []}, [], key_only=False)
            editordata.write_split(root / "test" / "de", {**INDEX, "language": "de", "list": []}, [], key_only=False)
            source = root / "pokedex.json"
            source.write_text(json.dumps(DATASET), encoding="utf-8")
            pokedextexts.write(root / "test" / "ru", INDEX, source)
            with registry_scope(root):
                doc = editordata.write_languages(root, toolkit="0.0.0")
            langs = {lang["code"]: lang for lang in doc["languages"]}
            self.assertEqual(langs["ru"]["texts"], "test/ru/pokedex-texts.json")
            self.assertNotIn("texts", langs["de"])

    def test_aliases_export_without_changing_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            aliases = root / "pokedex-aliases.toml"
            aliases.write_text('''schema = "2.0.0"
profile = "test"
language = "ru"
[species."species:1"]
source_sha256 = "41c74c2ea9f2bfb184f86939de5b97aa399b3e5cc8b83494250763777bed89f4"
aliases = ["Бульбасар", "Бульба"]
[species."species:2"]
source_sha256 = "eae3df9d3eb4a5e2c6c403dcd6c6a42ac0a6ac2ca64be5745ab285a602945494"
aliases = ["Ивисаур"]
''', encoding="utf-8")
            dataset = {**DATASET, "species": [*DATASET["species"], {"slot": 2, "name": "species:2"}]}
            source = root / "pokedex.json"
            source.write_text(json.dumps(dataset))
            out = root / "out"
            first = pokedextexts.write(out, INDEX, source, aliases_file=aliases).read_bytes()
            doc = json.loads(first)
            self.assertEqual(doc["search_aliases"], {"species:1": ["Бульбасар", "Бульба"], "species:2": ["Ивисаур"]})
            self.assertEqual(doc["texts"]["species:1"], ["Bulbasaur", "Бульбазавр"])
            self.assertEqual(first, pokedextexts.write(out, INDEX, source, aliases_file=aliases).read_bytes())
            other = pokedextexts.write(out, {**INDEX, "language": "de"}, source)
            self.assertNotIn("search_aliases", json.loads(other.read_bytes()))
            self.assertEqual(pokedextexts.search_aliases(root / "missing.toml", INDEX, dataset), {})

    def test_aliases_of_key_only_rows_are_judged_by_the_snapshot_digest(self):
        from gen3tl.catalog.source import digest
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "aliases.toml"
            path.write_text('schema = "2.0.0"\nprofile = "test"\nlanguage = "ru"\n[species."species:1"]\n'
                            'source_sha256 = "41c74c2ea9f2bfb184f86939de5b97aa399b3e5cc8b83494250763777bed89f4"\naliases = ["Бульбасар"]\n', encoding="utf-8")
            keyed = {**INDEX, "list": KEYED}
            for digests, error in (({"species:1": digest("Bulbasaur")}, None),
                                   ({"species:1": digest("Ivysaur")}, "source name changed"),
                                   ({}, "source name unknown without the English or the snapshot")):
                with self.subTest(error=error), patch.object(pokedextexts, "source_digests", return_value=digests):
                    if error is None:
                        self.assertEqual(pokedextexts.search_aliases(path, keyed, DATASET),
                                         {"species:1": ["Бульбасар"]})
                    else:
                        with self.assertRaisesRegex(ValueError, error):
                            pokedextexts.search_aliases(path, keyed, DATASET)

    def test_aliases_reject_stale_and_malformed_metadata(self):
        valid = '''schema = "2.0.0"
profile = "test"
language = "ru"
[species."species:1"]
source_sha256 = "41c74c2ea9f2bfb184f86939de5b97aa399b3e5cc8b83494250763777bed89f4"
aliases = ["Бульбасар"]
'''
        cases = [
            ('profile = "test"', 'profile = "other"', "do not match"),
            ('language = "ru"', 'language = "de"', "do not match"),
            ('schema = "2.0.0"', 'schema = 2', "do not match"),
            ('species:1', 'species:999', "unknown species"),
            ('species:1', 'moves:1', "unknown species"),
            ('source_sha256 = "41c74c2ea9f2bfb184f86939de5b97aa399b3e5cc8b83494250763777bed89f4"', 'source_sha256 = "eae3df9d3eb4a5e2c6c403dcd6c6a42ac0a6ac2ca64be5745ab285a602945494"', "source name changed"),
            ('["Бульбасар"]', '[]', "nonempty alias strings"),
            ('["Бульбасар"]', '[" "]', "nonempty alias strings"),
            ('["Бульбасар"]', '[1]', "nonempty alias strings"),
            ('["Бульбасар"]', '"Бульбасар"', "nonempty alias strings"),
            ('["Бульбасар"]', '["Бульбасар", "БУЛЬБАСАР"]', "duplicate aliases"),
            ('["Бульбасар"]', '["Бульба\\nсар"]', "single-line text"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "aliases.toml"
            for old, new, error in cases:
                with self.subTest(new=new):
                    path.write_text(valid.replace(old, new), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, error):
                        pokedextexts.search_aliases(path, INDEX, DATASET)


class SyncTreeTest(unittest.TestCase):
    def test_copies_only_missing_or_different_files(self):
        """write_languages syncs the sprite tree instead of copying it whole."""
        with tempfile.TemporaryDirectory() as tmp:
            source, destination = Path(tmp) / "a", Path(tmp) / "b"
            (source / "icons").mkdir(parents=True)
            (source / "icons" / "1.png").write_bytes(b"one")
            (source / "manifest.json").write_bytes(b"{}")
            self.assertEqual(editordata.sync_tree(source, destination), 2)
            self.assertEqual((destination / "icons" / "1.png").read_bytes(), b"one")
            self.assertEqual(editordata.sync_tree(source, destination), 0)
            (source / "icons" / "1.png").write_bytes(b"three")
            self.assertEqual(editordata.sync_tree(source, destination), 1)
            self.assertEqual((destination / "icons" / "1.png").read_bytes(), b"three")


if __name__ == "__main__":
    unittest.main()
