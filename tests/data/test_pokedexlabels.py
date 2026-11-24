import re
import tempfile
import unittest
from pathlib import Path

from gen3tl.data import pokedexdata, pokedexlabels, pokedextexts

BLOCK = {
    "strings": {"Base stats": "s0419C2A", "Rock Smash": "moves:249"},
    "symbols": {"Total": "Σ", "Cat.": ""},
    "unmapped": {"Catch rate": "the game never shows a catch rate"},
}

def page_markup_keys(html):
    site = re.compile(r'<head>.*?</head>|<header class="mast"[^>]*>.*?</header>'
                      r'|<footer class="foot"[^>]*>.*?</footer>', re.S)
    markup = re.compile(r'data-ui(?:-placeholder|-title|-label)?="([^"]+)"')
    tags = re.findall(r"<[a-z][^>]*>", site.sub("", html))
    marked = [tag for tag in tags if re.search(r"\sdata-ui-site[\s=>]", tag)]
    keys = set(markup.findall("".join(tag for tag in tags if tag not in marked)))
    chrome = set(markup.findall("".join(site.findall(html)) + "".join(marked)))
    return keys, chrome

class LabelsBlockTest(unittest.TestCase):
    def template(self, entries):
        from gen3tl.catalog.po import write
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "labels.pot"
        write(path, entries, {"Content-Type": "text/plain; charset=UTF-8"})
        return path

    def test_template_preserves_order_whitespace_and_reasons(self):
        from gen3tl.catalog.po import PoEntry
        reasons = {" Last ": 'Reason with "quotes" and \\ escapes.', "First": "Line one.\nLine two."}
        path = self.template([PoEntry(key, key, extracted=reason.split("\n")) for key, reason in reasons.items()])
        bindings = {"strings": {"Name": "s0000001"}, "symbols": {"Total": "Σ"}}
        loaded = pokedexlabels.load(bindings, path)
        self.assertEqual(list(loaded["unmapped"].items()), list(reasons.items()))
        self.assertNotIn("unmapped", bindings)
        self.assertEqual(pokedexlabels.build(loaded), {" Last ": None, "First": None,
                                                     "Name": "s0000001", "Total": "Σ"})

    def test_template_refuses_missing_and_blank_reasons(self):
        from gen3tl.catalog.po import PoEntry
        for extracted in ([], [""], [" ", "\t"]):
            with self.subTest(extracted=extracted):
                path = self.template([PoEntry("Label", "Label", extracted=extracted)])
                with self.assertRaisesRegex(ValueError, "'Label' has no reason"):
                    pokedexlabels.load({"strings": {}, "symbols": {}}, path)

    def test_template_refuses_duplicates_and_conflicting_bindings(self):
        from gen3tl.catalog.po import PoEntry
        entry = PoEntry("Label", "Label", extracted=["Site label."])
        with self.assertRaisesRegex(ValueError, "duplicate label"):
            pokedexlabels.load({"strings": {}, "symbols": {}}, self.template([entry, entry]))
        path = self.template([entry])
        with self.assertRaisesRegex(ValueError, "both strings and unmapped"):
            pokedexlabels.load({"strings": {"Label": "s0000001"}, "symbols": {}}, path)
        with self.assertRaisesRegex(ValueError, "also declared in the profile"):
            pokedexlabels.load(BLOCK, path)

    def test_template_refuses_invalid_keys_and_translations(self):
        from gen3tl.catalog.po import PoEntry
        for context, key, translation in ((" ", " ", ""), ("Other", "Label", ""), ("Label", "Label", "Text")):
            with self.subTest(context=context, key=key, translation=translation):
                path = self.template([PoEntry(context, key, translation, extracted=["Site label."])])
                with self.assertRaisesRegex(ValueError, "matching keys and an empty translation"):
                    pokedexlabels.load({"strings": {}, "symbols": {}}, path)

    def registered_template(self):
        from gen3tl import __version__
        from gen3tl.project import MANIFEST, REGISTRY, Manifest, Registry
        from gen3tl.catalog.po import PoEntry, write
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        manifest = root / MANIFEST
        manifest.write_text(Manifest(manifest, "fixture", "0" * 40, __version__, ("xx",), "xx").text())
        (root / REGISTRY).write_text(
            '[defaults]\nprofile = "fixture"\nlanguage = "xx"\n'
            '[[project]]\nprofile = "fixture"\nlanguage = "xx"\nroot = "."\npolicy = "policy.toml"\n')
        template = Registry.load(root).resolve("fixture").pokedex_labels
        write(template, [PoEntry(key, key, extracted=[reason]) for key, reason in BLOCK["unmapped"].items()], {})
        return root, template

    def test_raw_dataset_uses_tracked_template_without_language_inputs(self):
        from gen3tl.project import registry_scope
        from test_pokedexdata import block, rom_bytes
        root, template = self.registered_template()
        bindings = {name: value for name, value in BLOCK.items() if name != "unmapped"}
        raw = block(labels=bindings)
        expected = pokedexdata.build(block(labels=BLOCK), rom_bytes(), {"id": "fixture"})
        with registry_scope(root):
            actual = pokedexdata.build(raw, rom_bytes(), {"id": "fixture"})
            self.assertEqual(actual, expected)
            self.assertEqual(pokedexdata.verify_doc(actual, raw, "fixture", actual["rom"]["sha1"]), [])
            self.assertNotIn("unmapped", raw["labels"])
            template.unlink()
            self.assertIn("unmapped: a table", " ".join(
                pokedexdata.verify_doc(actual, raw, "fixture", actual["rom"]["sha1"])))

    def test_raw_dataset_refuses_template_without_a_reason(self):
        from gen3tl.catalog.po import PoEntry, write
        from gen3tl.project import registry_scope
        from test_pokedexdata import block, rom_bytes
        root, template = self.registered_template()
        raw = block(labels={name: value for name, value in BLOCK.items() if name != "unmapped"})
        write(template, [PoEntry("Label", "Label")], {})
        with registry_scope(root):
            with self.assertRaisesRegex(ValueError, "'Label' has no reason"):
                pokedexdata.build(raw, rom_bytes(), {"id": "fixture"})
            self.assertIn("'Label' has no reason", " ".join(
                pokedexdata.verify_doc({}, raw, "fixture", "0" * 40)))

    def test_template_resolution_keeps_other_profiles_and_explicit_tables(self):
        from gen3tl.project import registry_scope
        root, _ = self.registered_template()
        raw = {"labels": {"strings": {}, "symbols": {}}}
        explicit = {"labels": dict(BLOCK, unmapped={"Label": ""})}
        with registry_scope(root):
            self.assertIs(pokedexlabels.resolve(raw, "other"), raw)
            self.assertIs(pokedexlabels.resolve(explicit, "fixture"), explicit)
            self.assertIn("unmapped: 'Label' has no reason", pokedexlabels.check(explicit["labels"]))

    def test_page_markup_separates_shared_mastheads_from_game_labels(self):
        for attributes in ("", ' data-site-mast="" data-site-menu-licenses=""'):
            with self.subTest(attributes=attributes):
                keys, chrome = page_markup_keys(
                    '<head><title data-ui="Page title">Page title</title></head>'
                    f'<header class="mast"{attributes}>'
                    '<nav data-ui-label="Site"><a data-ui="Home">Home</a></nav></header>'
                    '<main><p data-ui="Base stats">Base stats</p>'
                    '<p data-ui="Home">Home</p><p data-ui="New label">New label</p>'
                    '<button data-ui="List" data-ui-site>List</button></main>'
                    '<footer class="foot" data-site-footer=""><div class="wrap">'
                    '<a data-ui="Licence" data-ui-site="">Licence</a></div></footer>')
                self.assertEqual(keys, {"Base stats", "Home", "New label"})
                self.assertEqual(chrome, {"Page title", "Site", "Home", "List", "Licence"})

    def test_build_merges_the_three_tables_sorted_with_unmapped_as_none(self):
        self.assertEqual(pokedexlabels.build(BLOCK), {
            "Base stats": "s0419C2A", "Cat.": "", "Catch rate": None, "Rock Smash": "moves:249", "Total": "Σ"})

    def test_check_names_bad_ids_missing_reasons_and_double_keys(self):
        self.assertEqual(pokedexlabels.check(BLOCK), [])
        bad = {"strings": {"Type": "ТИП", "HP": "s04169C2"}, "symbols": {"HP": "♥"}, "unmapped": {"Price": " "}}
        self.assertEqual(pokedexlabels.check(bad), [
            "strings: 'Type' is not a catalog id", "unmapped: 'Price' has no reason", "'HP' is in both strings and symbols"])
        self.assertEqual(pokedexlabels.check({"strings": {}}), ["symbols: a table of label keys", "unmapped: a table of label keys"])
        # fragment rule after "@": first word, word around a placeholder
        specs = {"PP": "s0416FC7@^", "Mega {species}": "s083001C@B_COPY_VAR_3", "Flinch chance": "s03FB914@B_ATK_NAME_WITH_PREFIX+"}
        self.assertEqual(pokedexlabels.check({"strings": specs, "symbols": {}, "unmapped": {}}), [])
        self.assertEqual(pokedexlabels.check({"strings": {"X": "s0416FC7@", "Y": "s0416FC7@x"}, "symbols": {}, "unmapped": {}}),
                         ["strings: 'X' is not a catalog id", "strings: 'Y' is not a catalog id"])
        self.assertEqual(pokedextexts.referenced({"labels": specs}), {"s0416FC7", "s083001C", "s03FB914"})

    def test_the_dataset_carries_the_labels_and_the_texts_pick_their_ids_up(self):
        from test_pokedexdata import block, rom_bytes
        doc = pokedexdata.build(block(labels=BLOCK), rom_bytes(), {"id": "test"})
        self.assertEqual(doc["labels"], pokedexlabels.build(BLOCK))
        self.assertTrue({"s0419C2A", "moves:249"} <= pokedextexts.referenced(doc))   # symbols are not ids
        self.assertNotIn("Σ", pokedextexts.referenced(doc))
        stale = dict(doc, labels={"Base stats": "s0419C2A"})
        problems = pokedexdata.verify_doc(stale, block(labels=BLOCK), "test", doc["rom"]["sha1"])
        self.assertIn("labels: regenerate the dataset with gen3tl data pokedex (the page labels changed)", problems)
        self.assertEqual(pokedexdata.check(block(labels={"strings": {"X": "nope"}, "symbols": {}, "unmapped": {}})),
                         ["[pokedex.labels] strings: 'X' is not a catalog id"])
