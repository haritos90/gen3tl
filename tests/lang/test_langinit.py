import io
import json
import tempfile
import tomllib
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from test_roots import TOOLKIT_ROOT
from gen3tl.web import snapshotstore as snapshot_store
from gen3tl import __version__
from gen3tl.cli import main as cli
from gen3tl.build import keyboard
from gen3tl.web import editorsnapshot
from gen3tl.lang import language, langinit, langpack
from gen3tl.core.charmap import BASE_CHARS
from gen3tl.lang.langpack import LangPack
from gen3tl.catalog.po import KEY_ONLY, SOURCE_FIELD, PoEntry, read
from gen3tl.check.layout import Limit
from gen3tl.catalog.source import digest
from gen3tl.profile.profile import Profile
from gen3tl.project import Registry, registry_scope
from project_fixture import literal


BINDINGS = {
    "lower_keys_1": "naming_keyboard:0", "lower_keys_2": "naming_keyboard:1",
    "lower_keys_3": "naming_keyboard:2", "lower_keys_4": "naming_keyboard:3",
    "upper_keys_1": "naming_keyboard:4", "upper_keys_2": "naming_keyboard:5",
    "upper_keys_3": "naming_keyboard:6", "upper_keys_4": "naming_keyboard:7",
    "symbols_keys_1": "naming_keyboard:8", "symbols_keys_2": "naming_keyboard:9",
    "symbols_keys_3": "naming_keyboard:10", "symbols_keys_4": "naming_keyboard:11",
    "lower_display_1": "s0000001", "lower_display_2": "s0000002",
    "lower_display_3": "s0000003", "lower_display_4": "s0000004",
    "upper_display_1": "s0000005", "upper_display_2": "s0000006",
    "upper_display_3": "s0000007", "upper_display_4": "s0000008",
    "symbols_display_1": "s0000009", "symbols_display_2": "s000000A",
    "symbols_display_3": "s000000B", "symbols_display_4": "s000000C",
}


def source_row(text, capacity):
    catalog = {"row": (Path("keyboard.po"), PoEntry("row", text))}
    limits = {"row": Limit("field", 6 * len(text), 1, chars=capacity)}
    return editorsnapshot.source_rows(catalog, limits, lambda b: 6)["row"]


class LangInitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.sha1 = "0" * 40
        (self.root / "assets/lang").mkdir(parents=True)
        template_path = TOOLKIT_ROOT / "assets/lang/templates.json"
        (self.root / "assets/lang/templates.json").write_text(template_path.read_text())
        self.profile_path = self.root / "game.toml"
        data = {
            "rom": {"sha1": self.sha1},
            "font": [{"name": "main", "glyphs": 0, "widths": 0, "count": 256}],
            "font_style": {"style": "main", "cell_height": 14},
            "keyboard": {"base_pages": 3, "first_page": 1, "opening_keyboards": [1, 0],
                         "opening_keyboard_offsets": [0x120, 0x130], "table": [], "row_bindings": BINDINGS},
            "layout": {},
            "policy_slots": {"window_width": ["budget"], "placeholders": ["PLAYER"],
                             "code_fill": {}, "table_windows": [], "table_window": []},
            "graphics": {"edit_facts": {}, "tilemap": []},
            "text_controls": {"droppable": {}, "addable": {}},
            "reach": {},
        }
        self.profile_path.write_text("\n".join(f"{key} = {literal(value)}" for key, value in data.items()))
        self.profile = Profile.load(self.profile_path)
        source = self.root / "source"
        (source / "translation").mkdir(parents=True)
        (source / "build").mkdir()
        template = {"layout": {"label_min_width": 60, "target_width": 196, "sentence_break": 0.5,
                               "window_width": {"budget": 42}, "placeholders": {"PLAYER": 42}}}
        (source / "translation/policy.toml").write_text(
            "\n".join(f"{key} = {literal(value)}" for key, value in template.items()))
        (self.root / "projects.toml").write_text(
            '[defaults]\nprofile = "game"\nlanguage = "ru"\n\n'
            '[[project]]\nprofile = "game"\nlanguage = "ru"\nroot = "source"\n'
            'policy = "translation/policy.toml"\nstage = "translation"\n')
        entries = []
        for index, sid in enumerate(BINDINGS.values()):
            entries.append({"id": sid, "offset": index * 16, "length": 8, "kind": "fixed",
                            "category": "fixed", "mode": "field", "table": "keyboard", "index": index,
                            "flags": [], "refs": [], "text": "ABCDEF ."})
        identity = {"profile": "game", "rom_sha1": self.sha1}
        documents = {
            "inventory": {**identity, "rejected_mid_string_targets": 0, "excluded": [],
                          "pointer_tables": [], "entries": entries},
            "context": {**identity, "stats": {}, "strings": {}},
            "reach": {**identity, "stats": {}, "strings": {}},
        }
        for name, document in documents.items():
            (source / "build" / f"{name}.json").write_text(json.dumps(document))
        snapshot = {
            "schema": editorsnapshot.SCHEMA, **identity, "language": "ru", "source_sha256": "source",
            "configuration_sha256": "configuration", "facts_sha256": "facts",
            "fonts": {"main": {"widths": [6] * 256, "shadows": [False] * 256}},
            "inventory": [], "context": {}, "reach": {}, "notes": {},
            "records": {"windows": [], "numbers": {}, "keys": {}, "heads": {}, "cursor": [], "code_originals": {}, "fill_fragments": {}, "fill_alphabet": "Ж"},
            "source": {sid: source_row("ABCDEF .", 7) for sid in BINDINGS.values()},
        }
        snapshot_store.write(source / "translation/editor-inputs.json", snapshot)
        language.load.cache_clear()
        language.registry_default.cache_clear()
        self.addCleanup(language.load.cache_clear)
        self.addCleanup(language.registry_default.cache_clear)
        self.patches = [patch.object(langinit, "TOOLKIT_ROOT", self.root),
                        patch.object(langpack, "TOOLKIT_ROOT", self.root),
                        patch.object(language, "ROOT", self.root)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def run_cli(self, code, *extra):
        with redirect_stdout(io.StringIO()) as output:
            result = cli.main(["--project-root", str(self.root), "--profile", str(self.profile_path),
                               "lang", "init", code, *extra])
        return result, output.getvalue()

    def test_installed_toolkit_refuses_to_write_into_its_pack_directory(self):
        with patch("gen3tl.INSTALLED", True):
            result, output = self.run_cli("it")
        self.assertEqual(result, 1)
        self.assertEqual(output, f"error        {self.root / 'assets/lang'}: the toolkit runs installed; "
                                 "run lang init from a toolkit checkout or pass --packs <dir>\n")
        self.assertFalse((self.root / "assets/lang/it").exists())
        self.assertFalse((self.root / "translation/projects/game/it").exists())

    def test_installed_toolkit_writes_into_the_given_pack_directory(self):
        packs = self.root / "external/packs"
        with patch("gen3tl.INSTALLED", True):
            result, output = self.run_cli("it", "--packs", str(packs))
        self.assertEqual(result, 0, output)
        self.assertTrue((packs / "it/language.json").is_file())
        self.assertFalse((self.root / "assets/lang/it").exists())
        self.assertIn(f"pack         {packs / 'it'}\n", output)

    def test_declared_slots_replace_reference_estimates(self):
        self.profile.data["layout"] = {
            "fill_source": {"player": {"label": "Player name", "font": "main", "evidence": "Verified input",
                                         "value": {"letters": 7, "alphabet": "naming"}}},
            "slot_source": {"PLAYER": "player"}}
        self.profile_path.write_text("\n".join(f"{key} = {literal(value)}" for key, value in self.profile.data.items()))
        self.run_cli("it")
        project = Registry.load(self.root).resolve(self.profile, "it")
        policy = tomllib.loads(project.policy.read_text())
        self.assertEqual(policy["policy"]["version"], "3.0.0")
        self.assertNotIn("PLAYER", policy["layout"]["placeholders"])
        self.assertNotIn("PLAYER", policy["layout"]["unverified"]["placeholders"])
        self.assertEqual(policy["layout"]["unverified"]["window_width"], ["budget"])
        snapshot = snapshot_store.load(project.snapshot)
        self.assertIn("À", snapshot["records"]["fill_alphabet"])
        self.assertNotIn("Ж", snapshot["records"]["fill_alphabet"])

    def test_latin_project_has_complete_policy_catalog_and_snapshot(self):
        source_files = {path: path.read_bytes() for path in (self.root / "source").rglob("*") if path.is_file()}
        result, output = self.run_cli("it")
        self.assertEqual(result, 0, output)
        project = Registry.load(self.root).resolve("game", "it")
        self.assertEqual(project.stage, "translation")
        release = tomllib.loads((project.translation / "release.toml").read_text())
        self.assertEqual(release, {"version": "0.0.0"})
        selected = project.effective(self.profile)
        self.assertEqual(selected["layout"]["window_width"]["budget"], 42)
        self.assertEqual(selected["layout"]["placeholders"]["PLAYER"], 42)
        self.assertEqual(selected["qa"], {"optional_tokens": [], "forbidden": {}, "droppable": [], "addable": []})
        self.assertEqual(selected["graphics"], {"edit_facts": {}, "tilemap": [], "edit": []})
        policy = project.policy.read_text()
        self.assertIn("# REVIEW: Russian reference is 42 px; measure it.", policy)
        header, entries = read(project.catalog / "catalog.po")
        self.assertEqual((header["Project-Id-Version"], header["Language"]), ("game", "it"))
        self.assertEqual(len(entries), len(BINDINGS))
        self.assertTrue(all(not entry.msgstr for entry in entries))
        snapshot = snapshot_store.load(project.translation / "editor-inputs.json")
        self.assertEqual(snapshot["language"], "it")
        self.assertEqual(len(snapshot["inventory"]), len(BINDINGS))
        for row in snapshot["inventory"]:
            self.assertNotIn("text", row)
            self.assertEqual(row["source_sha256"], digest("ABCDEF ."))
        pack = LangPack.load("it", root=self.root / "assets/lang")
        self.assertTrue(all(source == "base" for source in pack.sources.values()))
        self.assertFalse((self.root / "assets/lang/it/fonts").exists())
        self.assertEqual(source_files, {path: path.read_bytes() for path in source_files})

    def test_unverified_inventory_keeps_only_the_source_digest(self):
        path = self.root / "source/build/inventory.json"
        inventory = json.loads(path.read_text())
        inventory["entries"].append({"id": "s0000200", "offset": 0x200, "length": 6,
                                     "kind": "pointer", "category": "unverified", "mode": "field",
                                     "table": None, "index": None, "flags": ["unverified"],
                                     "refs": [], "text": "ABCDE"})
        path.write_text(json.dumps(inventory))
        result, output = self.run_cli("it")
        self.assertEqual(result, 0, output)
        project = Registry.load(self.root).resolve("game", "it")
        snapshot = snapshot_store.load(project.snapshot)
        row = next(row for row in snapshot["inventory"] if row["id"] == "s0000200")
        self.assertEqual(row["source_sha256"], digest("ABCDE"))
        self.assertNotIn("text", row)
        self.assertNotIn("s0000200", snapshot["source"])

    def test_forbidden_reasons_seed_english_placeholders(self):
        with (self.root / "source/translation/policy.toml").open("a", encoding="utf-8") as stream:
            stream.write('\n[qa.forbidden]\n"\\\\.\\\\.\\\\." = "three dots: use the ellipsis character …"\n'
                         '"  " = { reason = "double space", ru = "двойной пробел" }\n')
        result, output = self.run_cli("it")
        self.assertEqual(result, 0, output)
        project = Registry.load(self.root).resolve("game", "it")
        text = project.policy.read_text()
        self.assertIn("# REVIEW: translate each English reason placeholder.", text)
        policy = tomllib.loads(text)
        self.assertEqual(policy["qa"]["forbidden"], {
            "\\.\\.\\.": {"reason": "three dots: use the ellipsis character …",
                           "it": "three dots: use the ellipsis character …"},
            "  ": {"reason": "double space", "it": "double space"},
        })
        selected = project.effective(self.profile)
        self.assertEqual(selected["qa"]["reasons"], {
            "three dots: use the ellipsis character …": {"it": "three dots: use the ellipsis character …"},
            "double space": {"it": "double space"},
        })

    def test_the_pack_opens_on_the_latin_pages(self):
        # the screen prints keyboards 1 and 0 when it opens
        result, output = self.run_cli("it")
        self.assertEqual(result, 0, output)
        layout = tomllib.loads((self.root / "assets/lang/it/keyboard.toml").read_text(encoding="utf-8"))
        self.assertEqual(layout["keyboards"], [
            {"name": "a–z", "like": 0, "base": True}, {"name": "A–Z", "like": 1, "base": True},
            {"name": "symbols", "like": 2}, {"name": "letters uppercase", "like": 1},
            {"name": "letters lowercase", "like": 0}])
        self.assertEqual([(p["name"], p["keyboard"], p["look"]) for p in layout["pages"]], [
            ("symbols", 2, 0), ("A–Z", 1, 1), ("a–z", 0, 2), ("letters uppercase", 3, 1),
            ("letters lowercase", 4, 2)])
        kb = {**self.profile["keyboard"], **layout}
        first = kb["first_page"]
        self.assertEqual([layout["pages"][first]["name"], layout["pages"][first + 1]["name"]], ["A–Z", "a–z"])
        keyboard.check_opening(kb)

    def test_rows_bound_to_other_text_keep_their_english(self):
        bindings = {**BINDINGS, "chat_upper_1": "s000000D"}
        profile = self.profile_path.read_text()
        self.assertIn(literal(BINDINGS), profile)
        self.profile_path.write_text(profile.replace(literal(BINDINGS), literal(bindings)))
        path = self.root / "source/build/inventory.json"
        inventory = json.loads(path.read_text())
        inventory["entries"].append({"id": "s000000D", "offset": 0x200, "length": 6, "kind": "pointer",
                                     "category": "data-table", "mode": "field", "table": None, "index": None,
                                     "flags": [], "refs": [], "text": "ABCDE"})
        path.write_text(json.dumps(inventory))
        snapshot_path = self.root / "source/translation/editor-inputs.json"
        snapshot = snapshot_store.load(snapshot_path)
        snapshot["source"]["s000000D"] = source_row("ABCDE", 5)
        snapshot_store.write(snapshot_path, snapshot)
        result, output = self.run_cli("it")
        self.assertEqual(result, 0, output)
        layout = tomllib.loads((self.root / "assets/lang/it/keyboard.toml").read_text(encoding="utf-8"))
        self.assertEqual(layout["rows"]["chat_upper_1"], "ABCDE")
        self.assertEqual(set(layout["rows"]), set(bindings))
        inventory["entries"].pop()
        path.write_text(json.dumps(inventory))
        result, output = self.run_cli("el")
        self.assertEqual(result, 1)
        self.assertIn("keyboard row chat_upper_1: its bound entry is not in the inventory", output)
        self.assertFalse((self.root / "assets/lang/el").exists())

    def test_a_new_language_gains_its_site_catalog_and_an_existing_one_stays(self):
        from gen3tl.web import sitecatalog
        result, output = self.run_cli("it")
        self.assertEqual(result, 0, output)
        path = self.root / "web/i18n/it.po"
        self.assertIn(f"site         {path}\n", output)
        entries = sitecatalog.load(path)
        with registry_scope(self.root):
            self.assertEqual([e.msgid for e in entries], list(sitecatalog.page_keys()))
        self.assertTrue(all(not e.msgstr for e in entries))
        kept = path.with_name("el.po")
        kept.write_bytes(path.read_bytes().replace(b"Language: it", b"Language: el"))
        before = kept.read_bytes()
        result, output = self.run_cli("el")
        self.assertEqual(result, 0, output)
        self.assertIn("site         unchanged\n", output)
        self.assertEqual(kept.read_bytes(), before)

    def test_new_language_retains_registered_read_inputs(self):
        dataset = self.root / "dataset"
        dataset.mkdir()
        (dataset / "pokedex.json").write_text('{"species":[]}')
        path = self.root / "projects.toml"
        with path.open("a") as stream:
            stream.write(f'profile_path = "{self.profile_path.relative_to(self.root)}"\n'
                         'dataset_path = "dataset"\n')
        result, output = self.run_cli("it")
        self.assertEqual(result, 0, output)
        projects = Registry.load(self.root).projects
        self.assertEqual(len(projects), 2)
        self.assertEqual({p.profile_path for p in projects}, {self.profile_path})
        self.assertEqual({p.dataset_path for p in projects}, {dataset})

    def test_game_project_gains_a_language_folder(self):
        from gen3tl.project import Manifest
        game = self.root / "source"
        (game / "translation/ru").mkdir()
        (game / "translation/policy.toml").rename(game / "translation/ru/policy.toml")
        (game / "source/snapshot").mkdir(parents=True)
        snapshot_store.write(game / "source/snapshot/ru.snapshot.json",
                             snapshot_store.load(game / "translation/editor-inputs.json"))
        (game / "project.toml").write_text(Manifest(game / "project.toml", "game", self.sha1, __version__, ("ru",),
                                                    "ru").text())
        registry = (self.root / "projects.toml").read_text().replace(
            'policy = "translation/policy.toml"', 'policy = "translation/ru/policy.toml"')
        (self.root / "projects.toml").write_text(registry)
        result, output = self.run_cli("it")
        self.assertEqual(result, 0, output)
        project = Registry.load(self.root).resolve("game", "it")
        self.assertTrue(project.game)
        self.assertEqual(project.translation, game / "translation/it")
        self.assertEqual(project.policy, game / "translation/it/policy.toml")
        self.assertEqual(snapshot_store.load(project.snapshot)["language"], "it")
        self.assertEqual(project.snapshot, game / "source/snapshot/it.snapshot.json")
        self.assertEqual(Manifest.read(game / "project.toml").languages, ("ru", "it"))
        self.assertFalse((game / "translation/it/build").exists())
        header, entries = read(project.catalog / "catalog.po")
        self.assertEqual(len(entries), len(BINDINGS))
        # a game project holds the catalog by key
        self.assertEqual(header[SOURCE_FIELD], KEY_ONLY)
        self.assertTrue(all(e.msgid == e.msgctxt and e.keyed for e in entries))

    def test_non_base_letters_are_named_and_have_empty_art_slots(self):
        result, output = self.run_cli("el")
        self.assertEqual(result, 0, output)
        self.assertIn("missing      Α\nunicode      GREEK CAPITAL LETTER ALPHA\nslot", output)
        self.assertIn("missing      ς\nunicode      GREEK SMALL LETTER FINAL SIGMA\nslot", output)
        pack = LangPack.load("el", root=self.root / "assets/lang")
        missing = {char for char, source in pack.sources.items() if source == "glyph"}
        self.assertEqual(missing, set("ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩαβγδεζηθικλμνξοπρςστυφχψω"))
        self.assertTrue(set(missing).isdisjoint(BASE_CHARS.values()))
        art = (self.root / "assets/lang/el/fonts/main.txt").read_text()
        self.assertIn("Empty art slots", art)
        self.assertIn("GREEK CAPITAL LETTER ALPHA", art)
        self.assertNotIn("\nglyph ", art)

    def test_graphics_inputs_are_copied(self):
        graphics = {"inventory": "translation/graphics/inventory.tsv",
                    "previews": "translation/graphics/previews.json", "edit_facts": {}, "tilemap": []}
        lines = [f'"graphics" = {literal(graphics)}' if line.startswith('graphics =') else line
                 for line in self.profile_path.read_text().splitlines()]
        self.profile_path.write_text("\n".join(lines))
        source = self.root / "source/translation/graphics"
        source.mkdir(parents=True)
        (source / "inventory.tsv").write_text("id\n")
        (source / "previews.json").write_text('{"version": 1, "views": {}}')
        self.assertEqual(self.run_cli("it")[0], 0)
        target = self.root / "translation/projects/game/it/translation/graphics"
        self.assertEqual((target / "inventory.tsv").read_text(), "id\n")
        self.assertEqual((target / "previews.json").read_text(), '{"version": 1, "views": {}}')

    def test_existing_registration_pack_and_workspace_are_refused_without_changes(self):
        self.assertEqual(self.run_cli("it")[0], 0)
        before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        result, output = self.run_cli("it")
        self.assertEqual(result, 1)
        self.assertIn("already registered", output)
        self.assertEqual(before, {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()})
        (self.root / "assets/lang/xx").mkdir(parents=True)
        before = (self.root / "projects.toml").read_bytes()
        self.assertEqual(self.run_cli("xx")[0], 1)
        self.assertFalse((self.root / "translation/projects/game/xx").exists())
        self.assertEqual((self.root / "projects.toml").read_bytes(), before)
        (self.root / "translation/projects/game/yy").mkdir(parents=True)
        self.assertEqual(self.run_cli("yy")[0], 1)
        self.assertFalse((self.root / "assets/lang/yy").exists())
        self.assertEqual((self.root / "projects.toml").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
