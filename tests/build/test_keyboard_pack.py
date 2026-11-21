import json
import tempfile
import unittest
from pathlib import Path

from gen3tl.catalog import batch
from gen3tl.build import keyboard
from gen3tl.catalog.catalog import load_catalog
from gen3tl.check.render import indexed_png
from gen3tl.catalog.po import PoEntry, read, write
from gen3tl.profile.profile import EFFECTIVE_PROFILE, Profile
from project_fixture import literal

class KeyboardPackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = {"base_pages": 3, "first_page": 1, "opening_keyboards": [1, 0],
                     "opening_keyboard_offsets": [0x120, 0x130], "table": [],
                     "row_bindings": {"lower_keys_1": "keys:0", "lower_display_1": "s100"}}
        self.doc = {"schema": "2.0.0", "language": "xx",
                    "keyboards": [{"name": "custom", "like": 0}],
                    "pages": [{"name": "custom", "keyboard": 0, "look": 1}],
                    "rows": {"lower_keys_1": "abcdef .", "lower_display_1": "a b c"}}
        self.path = self.root / "xx/keyboard.toml"
        self.path.parent.mkdir()

    def load(self, doc=None, filename="keyboard.toml"):
        self.path.write_text("\n".join(f"{k} = {literal(v)}" for k, v in (doc or self.doc).items()))
        return keyboard.load_layout("xx", filename, self.base, self.root)

    def profile(self):
        return Profile({"rom": {"sha1": "0" * 40}, "font": [], "keyboard": {**self.base, **self.load()}},
                       self.root / "other-game.toml")

    def test_synthetic_layout_has_no_russian_or_game_fallback(self):
        self.assertEqual(self.load({**self.doc, "keyboards": [{"name": "custom", "like": 0, "columns": 2}]})
                         ["keyboards"], [{"name": "custom", "like": 0, "columns": 2}])
        profile = self.profile()
        self.assertEqual(keyboard.owned_entries(profile), {"keys:0": "abcdef .", "s100": "a b c"})
        self.assertEqual(profile["keyboard"]["pages"][0]["name"], "custom")
        self.path.unlink()
        with self.assertRaisesRegex(keyboard.KeyboardError, "cannot read"):
            keyboard.load_layout("xx", "keyboard.toml", self.base, self.root)
        with self.assertRaisesRegex(keyboard.KeyboardError, "effective language"):
            keyboard.owned_entries(Profile({"keyboard": self.base}, self.root / "raw.toml"))

    def test_invalid_identity_paths_and_rom_overrides_are_refused(self):
        for update in ({"language": "ru"}, {"schema": "1.0.0"}, {"schema": "9.0.0"}, {"reserved": [1, 2]},
                       {"rows": {}}, {"rows": {"lower_keys_1": "a", "lower_display_1": 2}},
                       {"keyboards": [{"name": "bad", "like": True}]},
                       {"keyboards": [{"name": "custom", "like": 0, "columns": 0}]},
                       {"keyboards": [{"name": "custom", "like": 0, "columns": True}]},
                       {"keyboards": [{"name": "custom", "like": 0, "columns": "2"}]},
                       {"pages": [{"name": "bad", "keyboard": 4, "look": 1}]}):
            with self.subTest(update=update), self.assertRaises(keyboard.KeyboardError):
                self.load({**self.doc, **update})
        for filename in (None, "../keyboard.toml", str(self.path)):
            with self.subTest(filename=filename), self.assertRaises(keyboard.KeyboardError):
                self.load(filename=filename)
        self.base["row_bindings"]["lower_display_1"] = "keys:0"
        with self.assertRaisesRegex(keyboard.KeyboardError, "unique ROM bindings"):
            self.load()

    def test_page_label_images_load_from_inside_the_pack(self):
        self.base["table"] = [{"index": index, "entry": entry} for index, entry in
                              (("next-label", 1), ("label-palette", 2), ("label-tile", 2),
                               ("label-subsprites", 8), ("sprite-sheets", 8))]
        image = self.path.parent / "label.png"
        data = indexed_png([[0] * 24 for _ in range(8)], [(0, 0, 0)], 4)
        image.write_bytes(data)
        doc = {**self.doc, "pages": [{"name": "custom", "keyboard": 0, "look": 1, "label": "label.png"}]}
        self.assertEqual(self.load(doc)["_label_data"], {"custom": data.hex()})
        doc["pages"][0]["label"] = "../label.png"
        with self.assertRaisesRegex(keyboard.KeyboardError, "inside the language pack"):
            self.load(doc)
        doc["pages"][0]["label"] = "missing.png"
        with self.assertRaisesRegex(keyboard.KeyboardError, "cannot read"):
            self.load(doc)

    def test_an_editor_edit_writes_its_rows_and_page_order_only(self):
        pages = [{"name": "custom", "keyboard": 0, "look": 2}, {"name": "upper", "keyboard": 1, "look": 1},
                 {"name": "second", "keyboard": 0, "look": 2}]
        text = ('schema = "2.0.0"\nlanguage = "xx"\n# the layout\n'
                '"keyboards" = [{ "name" = "custom", "like" = 0 }, { "name" = "upper", "like" = 1 }]\n'
                '"pages" = [\n  { "name" = "custom", "keyboard" = 0, "look" = 2 },\n'
                '  { "name" = "upper", "keyboard" = 1, "look" = 1 },\n'
                '  { "name" = "second", "keyboard" = 0, "look" = 2 },\n]\n\n[rows]\n'
                'lower_keys_1 = "abcdef ."   # keys\nlower_display_1 = "{CLEAR 0B}a b c"\n')
        self.path.write_text(text, encoding="utf-8")
        edit = {"rows": {"lower_keys_1": 'x"cdef .', "lower_display_1": "{CLEAR 0C}x b c"},
                "pages": ["second", "upper", "custom"],
                "base": {"rows": {"lower_keys_1": "abcdef .", "lower_display_1": "{CLEAR 0B}a b c"},
                         "pages": ["custom", "upper", "second"]}}
        english = {"lower_display_1": "{CLEAR 0B}a{CLEAR 06}b", "lower_keys_1": "abcdef ."}
        planned = keyboard.apply_edit("xx", "keyboard.toml", self.base, edit, {"lower_keys_1": "abcdef ."},
                                      self.root, write=False)
        self.assertEqual((planned["rows"], planned["pages"]), (["lower_display_1", "lower_keys_1"], True))
        self.assertEqual(self.path.read_text(encoding="utf-8"), text)   # a plan writes nothing
        with self.assertRaisesRegex(keyboard.KeyboardError, "placeholders differ"):
            keyboard.apply_edit("xx", "keyboard.toml", self.base, edit, english, self.root)
        english["lower_display_1"] = "{CLEAR 0B}a b c"
        keyboard.apply_edit("xx", "keyboard.toml", self.base, edit, english, self.root)
        layout = keyboard.load_layout("xx", "keyboard.toml", self.base, self.root)
        self.assertEqual(layout["rows"], edit["rows"])
        self.assertEqual(layout["pages"], pages[::-1])
        written = self.path.read_text(encoding="utf-8")
        custom, second = '  { "name" = "custom", "keyboard" = 0, "look" = 2 },\n', '  { "name" = "second", "keyboard" = 0, "look" = 2 },\n'
        upper = '  { "name" = "upper", "keyboard" = 1, "look" = 1 },\n'
        self.assertEqual(written, text.replace('"abcdef ."', '"x\\"cdef ."').replace("0B}a b c", "0C}x b c")
                         .replace(custom + upper + second, second + upper + custom))
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["keyboard.toml"])

        def bad_edit(change):
            edit = {"rows": dict(layout["rows"]), "pages": ["custom", "upper", "second"],
                    "base": {"rows": dict(layout["rows"]), "pages": ["second", "upper", "custom"]}}
            change(edit)
            return edit

        for change, message in ((lambda e: e["base"]["rows"].update(lower_keys_1="other"), "changed after the edit"),
                                (lambda e: e["base"].update(pages=["second", "upper", "custom", "x"]),
                                 "page order changed"),
                                (lambda e: e.update(pages=["second", "second", "upper"]), "every page once"),
                                (lambda e: e.update(pages=["upper", "custom", "second"]),
                                 "opening pages 1 and 2 use keyboards 0 and 0"),
                                (lambda e: (e["rows"].update(other="x"), e["base"]["rows"].update(other="x")),
                                 "no row other"),
                                (lambda e: e["base"]["rows"].pop("lower_keys_1"), "name the same rows"),
                                (lambda e: e.pop("pages"), "base order"),
                                (lambda e: (e.update(rows={}, base={"rows": {}}), e.pop("pages")), "changes nothing")):
            with self.subTest(message=message), self.assertRaisesRegex(keyboard.KeyboardError, message):
                keyboard.apply_edit("xx", "keyboard.toml", self.base, bad_edit(change), None, self.root)
        self.assertEqual(self.path.read_text(encoding="utf-8"), written)
        one_line = 'pages = [{ "name" = "custom", "keyboard" = 0, "look" = 1 }, { "name" = "b[]", "keyboard" = 0, "look" = 1 }]'
        self.assertEqual(keyboard.edit_layout(one_line, {}, [{"name": "b[]", "keyboard": 0, "look": 1},
                                                             {"name": "custom", "keyboard": 0, "look": 1}]),
                         'pages = [{ "name" = "b[]", "keyboard" = 0, "look" = 1 }, '
                         '{ "name" = "custom", "keyboard" = 0, "look" = 1 }]')

    def test_an_editor_edit_reads_the_profile_opening_keyboards(self):
        self.base["opening_keyboards"] = [0, 1]
        self.load({**self.doc, "keyboards": [{"name": "lower", "like": 0}, {"name": "upper", "like": 1}],
                   "pages": [{"name": "other", "keyboard": 0, "look": 0},
                             {"name": "lower", "keyboard": 0, "look": 1},
                             {"name": "upper", "keyboard": 1, "look": 2}]})
        self.path.write_text(self.path.read_text().split("\nrows = ")[0]
                             + '\n[rows]\nlower_keys_1 = "abcdef ."\nlower_display_1 = "a b c"\n')
        edit = {"rows": {"lower_keys_1": "xbcdef ."}, "base": {"rows": {"lower_keys_1": "abcdef ."}}}
        planned = keyboard.apply_edit("xx", "keyboard.toml", self.base, edit, root=self.root, write=False)
        self.assertEqual(planned["rows"], ["lower_keys_1"])
        self.base["opening_keyboards"] = [1, 0]
        with self.assertRaisesRegex(keyboard.KeyboardError, "opening pages 1 and 2 use keyboards 0 and 1"):
            keyboard.apply_edit("xx", "keyboard.toml", self.base, edit, root=self.root, write=False)

    def test_an_editor_edit_refuses_wrong_opening_looks_without_writing(self):
        for slot in (1, 2):
            pages = [{"name": "other", "keyboard": 1 if slot == 1 else 0, "look": 0},
                     {"name": "upper", "keyboard": 1, "look": 1},
                     {"name": "lower", "keyboard": 0, "look": 2}]
            self.load({**self.doc, "keyboards": [{"name": "lower", "like": 0}, {"name": "upper", "like": 1}],
                       "pages": pages})
            original = self.path.read_bytes()
            names = [p["name"] for p in pages]
            moved = list(names)
            moved[0], moved[slot] = moved[slot], moved[0]
            edit = {"pages": moved, "base": {"pages": names}}
            for write in (False, True):
                with self.subTest(slot=slot, write=write), self.assertRaisesRegex(
                        keyboard.KeyboardError, f"opening page {slot}: keyboard .* uses look 0; "
                        f"the fade-in draws base page look {slot}"):
                    keyboard.apply_edit("xx", "keyboard.toml", self.base, edit, root=self.root, write=write)
                self.assertEqual(self.path.read_bytes(), original)

    def test_catalog_has_one_canonical_target_and_retains_source_notes(self):
        profile = self.profile()
        path = self.root / "po/keys.po"
        write(path, [PoEntry("keys:0", "original", translator=["source: keyboard evidence"])], {})
        catalog = load_catalog(path.parent, profile)
        entry = catalog["keys:0"][1]
        self.assertEqual((entry.msgstr, entry.owner, entry.source_note),
                         ("abcdef .", "keyboard", "keyboard evidence"))
        write(path, [entry], {})
        self.assertEqual(read(path)[1][0].msgstr, "")
        self.assertEqual(read(path)[1][0].source_note, entry.source_note)
        for value, flags in (("abcdef .", []), ("", ["fuzzy"]), ("", ["empty"])):
            write(path, [PoEntry("keys:0", "original", value, flags=flags)], {})
            with self.subTest(value=value, flags=flags), self.assertRaisesRegex(keyboard.KeyboardError, "conflicts"):
                load_catalog(path.parent, profile)


    def test_batch_rejects_owned_rows_before_writing_any_destination(self):
        profile = self.profile()
        path = self.root / "translation/po/keys.po"
        write(path, [PoEntry("ordinary", "Other"), PoEntry("keys:0", "Original")], {})
        before = path.read_bytes()
        directory = self.root / "batches/draft"
        directory.mkdir(parents=True)
        (directory / "source.json").write_text(json.dumps([
            {"id": sid, "file": str(path)} for sid in ("ordinary", "keys:0")]))
        (directory / "translation.json").write_text(json.dumps({"ordinary": "valid", "keys:0": "invalid"}))
        token = EFFECTIVE_PROFILE.set(profile)
        try:
            with self.assertRaisesRegex(keyboard.KeyboardError, "read-only"):
                batch.import_batch("draft", path.parent, directory.parent)
        finally:
            EFFECTIVE_PROFILE.reset(token)
        self.assertEqual(path.read_bytes(), before)

    def test_term_rename_rejects_owned_rows_before_catalog_or_glossary_writes(self):
        from argparse import Namespace
        from unittest.mock import patch

        from gen3tl.cli import lang as cli
        from gen3tl.lang import language, termbase
        profile = self.profile()
        path = self.root / "po/keyboard.po"
        write(path, [PoEntry("keys:0", "Alphabet")], {})
        glossary, replacement = self.root / "glossary.tsv", self.root / "replacement.tsv"
        index = self.root / "index.tsv"
        index.write_text("existing index")
        with language.use("fr"):
            old = termbase.Term("term:alphabet", "term", "Alphabet", "abcdef",
                                declension="manual", forms={"nomn.sing": "abcdef"})
            new = termbase.Term("term:alphabet", "term", "Alphabet", "ghijkl",
                                declension="manual", forms={"nomn.sing": "ghijkl"})
            termbase.save(glossary, [old])
            termbase.save(replacement, [new])
            before = {p: p.read_bytes() for p in (path, glossary, replacement, index)}
            args = Namespace(pairs=["term:alphabet", "ghijkl"], glossary=str(glossary),
                             replacement_glossary=str(replacement), include=[], po_root=str(path.parent),
                             report=None, apply=True, allow_errors=False, index=str(index))
            token = EFFECTIVE_PROFILE.set(profile)
            try:
                with patch.object(cli, "_rename_checks") as checks:
                    with self.assertRaisesRegex(keyboard.KeyboardError, "read-only"):
                        cli.term_rename(args)
                    checks.assert_not_called()
            finally:
                EFFECTIVE_PROFILE.reset(token)
            self.assertEqual({p: p.read_bytes() for p in before}, before)
