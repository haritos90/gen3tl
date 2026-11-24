import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from test_roots import TOOLKIT_ROOT

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.catalog import keys  # noqa: E402
from gen3tl.catalog.catalog import build_entries, export, load_catalog  # noqa: E402
from gen3tl.catalog.po import PoEntry, read, write  # noqa: E402
from gen3tl.catalog.rekey import migrate, rekey_comment, rekey_po, rekey_profile  # noqa: E402
from gen3tl.core.charmap import Charmap  # noqa: E402
from gen3tl.core.rom import ROM_BASE  # noqa: E402
from gen3tl.catalog.batch import import_batch  # noqa: E402
from gen3tl.profile.profile import EFFECTIVE_PROFILE, Profile  # noqa: E402
from gen3tl.project import REGISTRY_ROOT  # noqa: E402
from gen3tl.web.editorimport import read_export  # noqa: E402
from gen3tl.verify import golden  # noqa: E402
from gen3tl.web.editordata import context, keyed_rules  # noqa: E402

SHA1 = "c" * 40


def entry(sid, offset, text, category="script", kind="pointer", table=None, index=None, refs=None, flags=()):
    return {"id": sid, "offset": offset, "length": len(text) + 1, "kind": kind, "category": category,
            "mode": "field", "text": text, "refs": refs or [], "table": table, "index": index, "flags": list(flags)}


def inventory():
    return {"pointer_tables": [{"name": "credits", "base": 0x100, "stride": 12, "count": 2, "fields": [0, 4]}],
            "entries": [
                entry("s0001000", 0x1000, "Hello there!"),
                entry("s0001100", 0x1100, "A sign."),
                entry("s0001200", 0x1200, "Unused text."),
                entry("species:1", 0x2000, "BULBASAUR", "table:species", "fixed", "species", 1),
                entry("s0003000", 0x3000, "Heading", "data-table", table="credits", index=1,
                      refs=[{"at": 0x100 + 12, "kind": "table"}]),
                entry("s0003100", 0x3100, "Names", "data-table", table="credits", index=1,
                      refs=[{"at": 0x100 + 12 + 4, "kind": "table"}]),
                entry("s0004000", 0x4000, "Hidden.", flags=["unverified"]),
            ]}


def context_doc():
    use = {"map": "3.4", "event": "npc", "script": "0x500", "order": 2}
    return {"maps": {"3.4": {"header": "0x600", "engine_maps": ["3.4"]},
                     "h0000700": {"header": "0x700", "engine_maps": ["1.2"]}},
            "strings": {"s0001000": {"uses": [use, dict(use, order=0, event="sign", script="0x510")]},
                        "s0001100": {"uses": [{"map": "h0000700", "event": "trigger", "script": "0x520",
                                               "order": 1}]}}}


def events(header):
    return {0x600: [("npc", 0, 0x500), ("sign", 3, 0x510)], 0x700: [("trigger", 1, 0x520)]}[header]


def rows(aliases):
    return {i: k for k, _, i in aliases.rows}


class AssignTest(unittest.TestCase):
    def test_forms_and_order(self):
        got = rows(keys.assign(inventory(), context_doc(), events=events))
        self.assertEqual(got["species:1"], "table:species/1")
        self.assertEqual(got["s0003000"], "table:credits/1/0")
        self.assertEqual(got["s0003100"], "table:credits/1/1")
        # the npc sorts before the sign
        self.assertEqual(got["s0001000"], "map:3.4/npc:0/2")
        # a structural header names its lowest engine map
        self.assertEqual(got["s0001100"], "map:1.2/trigger:1/1")
        self.assertEqual(got["s0001200"], keys.text_hash("Unused text.", "s0001200"))
        self.assertNotIn("s0004000", got)

    def test_stable_unique_and_kept(self):
        first = keys.assign(inventory(), context_doc(), events=events)
        self.assertEqual(first.rows, keys.assign(inventory(), context_doc(), events=events).rows)
        self.assertEqual(len({k for k, _, _ in first.rows}), len(first.rows))
        self.assertTrue(all(keys.is_key(k) for k, _, _ in first.rows))
        known = keys.Aliases([("hash:00000001", 0x1000, "s0001000")])
        again = rows(keys.assign(inventory(), context_doc(), events=events, known=known))
        self.assertEqual(again["s0001000"], "hash:00000001")

    def test_taken_hash_is_salted(self):
        inv = inventory()
        taken = keys.text_hash("Unused text.", "s0001200")
        known = keys.Aliases([(taken, 0x1000, "s0001000")])
        got = rows(keys.assign(inv, {}, known=known))
        self.assertEqual(got["s0001200"], keys.text_hash("Unused text.", "s0001200", 1))

    def test_rom_tables(self):
        data = bytearray(0x5000)
        for at, target in ((0x40, 0x1100), (0x48, 0x1200), (0x7C, 0x1000)):
            data[at:at + 4] = (target + ROM_BASE).to_bytes(4, "little")
        prof = {"pokedex": {"moves": {"name_table": "moves", "descriptions": {"base": 0x40, "count": 3}},
                            "items": {"name_table": "items", "base": 0x60, "stride": 8, "count": 4,
                                      "fields": {"description": {"offset": 4, "width": 4}}}},
                "keys": {"battle": {"base": 0x40, "first": 1, "count": 3}}}
        got = rows(keys.assign(inventory(), {}, prof, bytes(data)))
        self.assertEqual(got["s0001100"], "table:moves/0/description")
        self.assertEqual(got["s0001200"], "table:moves/2/description")
        self.assertEqual(got["s0001000"], "table:items/3/description")
        prof["pokedex"] = {}
        got = rows(keys.assign(inventory(), {}, prof, bytes(data)))
        self.assertEqual((got["s0001200"], got["s0001100"]), ("battle:2", keys.text_hash("A sign.", "s0001100")))


class AliasTest(unittest.TestCase):
    table = keys.Aliases([("map:3.4/npc:0/2", 0x1000, "s0001000"), ("table:species/1", 0x2000, "species:1")])

    def test_resolution(self):
        a = self.table
        self.assertEqual(a.entry_id("map:3.4/npc:0/2"), "s0001000")
        self.assertEqual(a.entry_id("s0001000"), "s0001000")
        self.assertEqual(a.key("species:1"), "table:species/1")
        self.assertEqual(a.key("other"), "other")
        self.assertEqual(a.resolve("0x1000"), "map:3.4/npc:0/2")
        self.assertEqual((a.entry_id("0x2000"), a.key("0x2000")), ("species:1", "table:species/1"))
        self.assertIsNone(a.resolve("s0009999"))
        self.assertEqual(keys.EMPTY.entry_id("x"), "x")
        with self.assertRaises(ValueError):
            keys.Aliases([("hash:00000001", 1, "s0000001"), ("hash:00000001", 2, "s0000002")])

    def test_file_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = keys.alias_path(SHA1, Path(tmp))
            keys.write(path, self.table)
            self.assertEqual(path.read_text().splitlines()[1], "map:3.4/npc:0/2\t0x0001000\ts0001000")
            self.assertEqual(keys.read(path).rows, self.table.rows)
            self.assertEqual(keys.load(SHA1, Path(tmp)).rows, self.table.rows)
            self.assertIs(keys.load(SHA1, Path(tmp) / "none"), keys.EMPTY)
            path.write_text("id\tkey\n")
            with self.assertRaises(ValueError):
                keys.read(path)

    def test_text_tokens(self):
        a = self.table
        self.assertEqual(a.rekey_text("see s0001000 and species:1."), "see map:3.4/npc:0/2 and table:species/1.")
        for kept in ("japanese-s0001000", "s0001000-s0002000", "move:1-arrows"):
            self.assertEqual(a.rekey_text(kept), kept)
        self.assertEqual(a.unkey_text("prev map:3.4/npc:0/2: Hi"), "prev s0001000: Hi")


class MigrationTest(unittest.TestCase):
    table = keys.Aliases([("map:3.4/npc:0/2", 0x1000, "s0001000"), ("hash:0a0b0c0d", 0x1100, "s0001100"),
                          ("table:species/1", 0x2000, "species:1")])

    def test_comments(self):
        a = self.table
        self.assertEqual(rekey_comment("prev s0001100: A sign.", a), "prev hash:0a0b0c0d: A sign.")
        self.assertEqual(rekey_comment("inserted as {STR_VAR_1} into s0001000: Hello", a),
                         "inserted as {STR_VAR_1} into map:3.4/npc:0/2: Hello")
        self.assertEqual(rekey_comment("{STR_VAR_2} may be species:1: 'x'", a),
                         "{STR_VAR_2} may be table:species/1: 'x'")
        self.assertEqual(rekey_comment("species: species:1, species:9", a), "species: table:species/1, species:9")
        never = "reach: never shown (review: like s0001100)"
        self.assertEqual(rekey_comment(never, a), never)

    def test_po_and_profile_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            po = Path(tmp) / "a.po"
            write(po, [PoEntry("s0001000", "Hello", "Привет", extracted=["next s0001100: A sign."],
                               translator=["source: like s0001100"], references=["0x0001000"])], {"Language": "ru"})
            self.assertEqual(rekey_po(po, self.table), 1)
            _, (e,) = read(po)
            self.assertEqual((e.msgctxt, e.extracted, e.translator, e.references),
                             ("map:3.4/npc:0/2", ["next hash:0a0b0c0d: A sign."], ["source: like hash:0a0b0c0d"],
                              ["0x0001000"]))
            self.assertEqual(rekey_po(po, self.table), 0)
            profile = Path(tmp) / "overlay.toml"
            profile.write_text('[reach]\nids = ["s0001000"]\n\n[[translator_notes]]\nid = "s0001000"\n'
                               'evidence = "like s0001100"\n\n[policy_slots]\n"window_width" = ["s0001100"]\n')
            self.assertTrue(rekey_profile(profile, self.table))
            self.assertEqual(profile.read_text(),
                             '[reach]\nids = ["s0001000"]\n\n[[translator_notes]]\nid = "map:3.4/npc:0/2"\n'
                             'evidence = "like hash:0a0b0c0d"\n\n[policy_slots]\n"window_width" = ["hash:0a0b0c0d"]\n')
            self.assertFalse(rekey_profile(profile, self.table))

    def test_migrate_project(self):
        class Project:
            def __init__(self, root):
                self.translation, self.catalog = root, root / "po"
                self.notes = self.graphics = None

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root / "po/a.po", [PoEntry("s0001000", "Hello"), PoEntry("s0009999", "Lost")], {})
            (root / "rules.tsv").write_text("ids\tsource_text\trule\tsource\ns0001000\t\tKeep s0001100.\tx\n")
            (root / "batches/b").mkdir(parents=True)
            (root / "batches/b/translation.json").write_text('{"s0001000": "Привет"}')
            (root / "pokedex-aliases.toml").write_text('[species]\n"species:1" = {}\n')
            prof = Profile({"rom": {"sha1": SHA1}}, root / "flat.toml")
            (root / "flat.toml").write_text("")
            report = migrate(prof, self.table, [Project(root)])
            self.assertEqual(report["entries"], 1)
            self.assertEqual(report["unknown"], [f"{root / 'po/a.po'}: s0009999"])
            self.assertEqual((root / "rules.tsv").read_text().splitlines()[1],
                             "map:3.4/npc:0/2\t\tKeep hash:0a0b0c0d.\tx")
            self.assertEqual(json.loads((root / "batches/b/translation.json").read_text()),
                             {"map:3.4/npc:0/2": "Привет"})
            self.assertIn('"species:1"', (root / "pokedex-aliases.toml").read_text())
            again = migrate(prof, self.table, [Project(root)], dry_run=True)
            self.assertEqual((again["entries"], again["files"]), (0, []))

            from gen3tl.check import rules
            (root / "rules.tsv").unlink()
            row = {"id": "rule:one", "ids": ["s0001000"], "notes": [{
                "id": "note:one", "subject": {"kind": "wording_rule", "id": "rule:one"},
                "scope": {"kind": "language", "language": "test"}, "status": "unreviewed",
                "guidance": ["Keep the name."],
                "related_subjects": [{"kind": "entry", "id": "s0001100"}],
            }]}
            rules.write_list(root / "rules.toml", [row], code="test")
            report = migrate(prof, self.table, [Project(root)])
            self.assertIn(str(root / "rules.toml"), report["files"])
            loaded, = rules.load_list(root / "rules.toml", code="test")
            self.assertEqual(loaded["id"], "rule:one")
            self.assertEqual(loaded["ids"], ["map:3.4/npc:0/2"])
            note, = loaded["notes"]
            self.assertEqual(note["id"], "note:one")
            self.assertEqual(note["subject"], row["notes"][0]["subject"])
            self.assertEqual(note["related_subjects"], [{"kind": "entry", "id": "hash:0a0b0c0d"}])
            self.assertEqual(migrate(prof, self.table, [Project(root)], dry_run=True)["files"], [])

    def test_migrate_ordered_overlay_preserves_generated_and_other_sections(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generated = root / "profile.toml"
            generated.write_text('[policy_slots]\nwindow_width = ["s0001000"]\n')
            overlay = root / "overlay"
            overlay.mkdir()
            order = overlay / "order.txt"
            order.write_text("reach.toml\nnotes.toml\nslots.toml\n")
            reach, notes, slots = (overlay / name for name in ("reach.toml", "notes.toml", "slots.toml"))
            reach.write_text('[reach]\nids = ["s0001000"]\n')
            notes.write_text('[[translator_notes]]\nid = "s0001000"\n')
            slots.write_text('[policy_slots]\nwindow_width = ["s0001100"]\n')
            before = {path: path.read_bytes() for path in (generated, order, reach, notes, slots)}
            planned = migrate(root, self.table, [], dry_run=True)
            self.assertEqual(planned["files"], [str(notes), str(slots)])
            self.assertEqual({path: path.read_bytes() for path in before}, before)
            self.assertEqual(migrate(root, self.table, [])["files"], planned["files"])
            self.assertIn('id = "map:3.4/npc:0/2"', notes.read_text())
            self.assertIn('["hash:0a0b0c0d"]', slots.read_text())
            for path in (generated, order, reach):
                self.assertEqual(path.read_bytes(), before[path])
            self.assertEqual(migrate(root, self.table, [])["files"], [])


class CatalogTest(unittest.TestCase):
    table = MigrationTest.table

    def test_build_entries_name_entries_by_key(self):
        inv = {"entries": [entry("s0001000", 0x1000, "Hello {STR_VAR_1}"), entry("s0001100", 0x1100, "A sign.")]}
        ctx = {"strings": {"s0001000": {"uses": [], "receives": [["s0001100", "{STR_VAR_1}"]]}}}
        groups = build_entries(inv, ctx, Charmap(), lambda b: 6, "test", aliases=self.table)
        first, second = [e for es in groups.values() for e in es]
        self.assertEqual((first.msgctxt, first.references), ("map:3.4/npc:0/2", ["0x0001000"]))
        self.assertIn("{STR_VAR_1} may be hash:0a0b0c0d: 'A sign.'", first.extracted)
        self.assertIn("prev map:3.4/npc:0/2: Hello {STR_VAR_1}", second.extracted)

    def test_export_moves_a_legacy_catalog_and_load_resolves(self):
        inv = {"entries": [entry("s0001000", 0x1000, "Hello"), entry("s0001100", 0x1100, "A sign.")]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "po"
            write(root / "script/region-0000000.po", [PoEntry("s0001000", "Hello", "Привет")], {})
            export(inv, {"strings": {}}, Charmap(), lambda b: 6, root, "test", aliases=self.table)
            _, es = read(root / "script/region-0000000.po")
            self.assertEqual([(e.msgctxt, e.msgstr) for e in es],
                             [("map:3.4/npc:0/2", "Привет"), ("hash:0a0b0c0d", "")])
            keys.write(keys.alias_path(SHA1, Path(tmp)), self.table)
            token = REGISTRY_ROOT.set(Path(tmp))
            try:
                catalog = load_catalog(root, Profile({"rom": {"sha1": SHA1}}, Path(tmp) / "p.toml"))
            finally:
                REGISTRY_ROOT.reset(token)
            self.assertEqual(sorted(catalog), ["s0001000", "s0001100"])
            self.assertEqual(catalog["s0001000"][1].msgctxt, "map:3.4/npc:0/2")


class EditorDataTest(unittest.TestCase):
    table = MigrationTest.table

    def test_context_and_rules_name_entries_by_key(self):
        catalog = {"s0001100": (Path("a.po"), PoEntry("hash:0a0b0c0d", "A sign.", "Знак"))}
        ctx = context(["{STR_VAR_1} may be s0001100: 'A sign.'", "inserted as {STR_VAR_2} into hash:0a0b0c0d: A"],
                      catalog, self.table)
        self.assertEqual(ctx["words"], [{"slot": "{STR_VAR_1}", "id": "hash:0a0b0c0d", "source_text": "A sign.",
                                         "translation": "Знак"}])
        self.assertEqual(ctx["hosts"], [{"slot": "{STR_VAR_2}", "id": "hash:0a0b0c0d"}])
        droppable = [{"ids": ["s0001000"], "tokens": ["{X}"], "evidence": "Proof."}]
        rules = keyed_rules({"droppable": droppable, "optional_tokens": []}, self.table)
        self.assertEqual(rules["droppable"][0]["ids"], ["map:3.4/npc:0/2"])


class EitherFormTest(unittest.TestCase):
    table = MigrationTest.table

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        keys.write(keys.alias_path(SHA1, self.root), self.table)
        self.tokens = (REGISTRY_ROOT.set(self.root),
                       EFFECTIVE_PROFILE.set(Profile({"rom": {"sha1": SHA1}}, self.root / "p.toml")))

    def tearDown(self):
        REGISTRY_ROOT.reset(self.tokens[0])
        EFFECTIVE_PROFILE.reset(self.tokens[1])
        self.tmp.cleanup()

    def test_batch_import_takes_keys_and_inventory_ids(self):
        po = self.root / "po/a.po"
        write(po, [PoEntry("map:3.4/npc:0/2", "Hello"), PoEntry("hash:0a0b0c0d", "A sign.")], {})
        batch = self.root / "batches/b"
        batch.mkdir(parents=True)
        (batch / "source.json").write_text(json.dumps([{"id": "s0001000", "file": str(po)},
                                                       {"id": "hash:0a0b0c0d", "file": str(po)}]))
        (batch / "translation.json").write_text(json.dumps({"map:3.4/npc:0/2": "Привет", "s0001100": "Знак"}))
        result = import_batch("b", self.root / "po", self.root / "batches")
        self.assertEqual((result["changed"], result["missing"]), (2, []))
        _, es = read(po)
        self.assertEqual([(e.msgctxt, e.msgstr, e.fuzzy) for e in es],
                         [("map:3.4/npc:0/2", "Привет", True), ("hash:0a0b0c0d", "Знак", True)])

    def test_editor_export_ids_resolve_to_keys(self):
        catalog = {"s0001000": (Path("a.po"), PoEntry("map:3.4/npc:0/2", "Hello", "Привет"))}
        path = self.root / "export.json"
        path.write_text(json.dumps({"profile": "p", "language": "ru",
                                    "entries": [{"id": "s0001000", "translation": "Здравствуй"}]}))
        data = read_export(path, "p", "ru", SHA1, catalog)
        self.assertEqual(data["document"]["entries"][0]["id"], "map:3.4/npc:0/2")
        entry = catalog["s0001000"][1]
        self.assertEqual(data["baseline"], {entry.msgctxt: (entry.msgid, entry.msgstr, "text")})


class GoldenMappingTest(unittest.TestCase):
    table = MigrationTest.table

    def test_identifier_only_differences_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new, other = Path(tmp) / "old.json", Path(tmp) / "new.json", Path(tmp) / "other.json"
            old.write_text(json.dumps({"list": [{"id": "s0001000", "c": "prev s0001100: A"}],
                                       "slots": {"species:1": 1}}))
            new.write_text(json.dumps({"slots": {"table:species/1": 1},
                                       "list": [{"c": "prev hash:0a0b0c0d: A", "id": "map:3.4/npc:0/2"}]}))
            other.write_text(json.dumps({"slots": {"table:species/1": 2},
                                         "list": [{"c": "prev hash:0a0b0c0d: A", "id": "map:3.4/npc:0/2"}]}))
            self.assertEqual(golden.canonical(old, self.table), golden.canonical(new, self.table))
            manifest = {"outputs": {"x.json": golden.file_hash(old)},
                        "identifiers": golden.identifier_hashes({"x.json": old}, keys.EMPTY)}
            for path, passes in ((new, True), (other, False)):
                outputs = {"x.json": golden.file_hash(path)}
                found = golden.compare(manifest, outputs, golden.identifier_hashes({"x.json": path}, self.table))
                self.assertEqual(found == [], passes)
            self.assertEqual(golden.identifier_only(manifest, {"x.json": golden.file_hash(new)}), ["x.json"])
            digest = hashlib.sha256(golden.canonical(old, keys.EMPTY)).hexdigest()
            self.assertEqual(manifest["identifiers"]["x.json"], digest)


if __name__ == "__main__":
    unittest.main()
