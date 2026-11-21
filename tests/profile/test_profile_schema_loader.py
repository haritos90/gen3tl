from test_roots import TOOLKIT_ROOT
"""Profile schema loader checks and fixtures."""

import sys
from contextlib import chdir
from pathlib import Path
import shutil
import tempfile
import tomllib
import unittest
from unittest.mock import patch

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.core.rom import RomError
from gen3tl.profile import check, convert, profile, schema
from gen3tl.profile.profile import Profile
from profile_schema_support import GENERATED, HEADER, OVERLAY, write_dir, write_fact_dir


FLAT = '''# a small flat profile
[rom]
sha1 = "00112233445566778899aabbccddeeff00112233"
size = 0x1000

[[font]]
name = "main"
glyphs = 0x100
count = 8
evidence = "pool at 0x6594"

[[font]]
name = "small"
glyphs = 0x300
evidence = "pool at 0x64C4"

[keyboard]
base = 0x400
pages = 2
rows = 4
evidence = "page reader at 0x8000"

[strings]
include = [0x10, 0x20]

[save]
[[save.boxes]]
base = 1
evidence = "box pointer table"
[[save.boxes]]
base = 2
'''


class FactIdTest(unittest.TestCase):
    def test_ids_name_paths_elements_and_entries(self):
        data = {"rom": {"sha1": "ab"}, "font": [{"name": "a"}, {"name": "a"}, {"glyphs": 1}],
                "layout": {"window_width": {"items:1": 4, "a b": 5}},
                "strings": {"include": [0x10], "exclude": ["s1"]},
                "save": {"party": {"base": 1, "evidence": "e", "fields": {"hp": 0}}}}
        found = {f.id: f for f in schema.facts(data)}
        self.assertEqual(list(found), ["rom.sha1", "font[name=a]", "font[name=a#2]", "font[#2]",
                                       'layout.window_width."items:1"', 'layout.window_width."a b"',
                                       "strings.include[0x10]", "strings.exclude[s1]", "save.party",
                                       "save.party.fields.hp"])
        self.assertEqual(found["save.party"].value, {"base": 1, "evidence": "e"})
        self.assertEqual(found["save.party.fields.hp"].section, "save.party")
        self.assertEqual(found['layout.window_width."items:1"'].kind, "window")
        self.assertEqual(found["rom.sha1"].kind, "identity")
        self.assertEqual(schema.kind_of("strings.pointer_table"), "pointer-table")
        self.assertEqual(schema.kind_of("unknown_table"), "unclassified")

    def test_identity_prefers_names_and_renders_numbers_in_hex(self):
        self.assertEqual(schema.element_ids([{"at": 0x1A8DD8, "ids": ["s1", "s2"]}, {"ids": ["s1", "s2"]},
                                             {"key": "contact", "base": 3}]),
                         ["at=0x1a8dd8", "ids=s1,s2", "key=contact"])


class MergeTest(unittest.TestCase):
    def test_overlay_over_generated(self):
        generated = {"rom": {"sha1": "ab", "size": 1}, "font": [{"name": "a", "count": 1, "glyphs": 2},
                                                                {"name": "b", "count": 3}],
                     "strings": {"include": [1, 2]}, "save": {"boxes": [{"base": 1}, {"base": 2}]}}
        overlay = {"rom": {"size": 2, "name": "x"}, "font": [{"name": "c", "count": 5}, {"name": "a", "count": 4}],
                   "strings": {"include": [2, 3]}, "save": {"boxes": [{"base": 9}]}, "reach": {"default": "r"}}
        merged = schema.merge(generated, overlay)
        self.assertEqual(merged["rom"], {"size": 2, "name": "x", "sha1": "ab"})
        self.assertEqual(list(merged["rom"]), ["size", "name", "sha1"])
        self.assertEqual(merged["font"], [{"name": "c", "count": 5}, {"name": "a", "count": 4, "glyphs": 2},
                                          {"name": "b", "count": 3}])
        self.assertEqual(merged["strings"]["include"], [1, 2, 3])
        # elements without identity pair by position
        self.assertEqual(merged["save"]["boxes"], [{"base": 9}, {"base": 2}])
        self.assertEqual(list(merged), ["rom", "font", "strings", "save", "reach"])
        self.assertEqual(generated["rom"], {"sha1": "ab", "size": 1})

    def test_overlay_key_order_is_independent_of_generated_order(self):
        overlay = {"rom": {"size": 2, "name": "x"},
                   "pokedex": {"encounters": {"methods": {"land": 1, "water": 2, "rock_smash": 3, "fishing": 4}}}}
        generated = {"pokedex": {"encounters": {"methods": {"land": 0, "fishing": 0, "water": 0, "rock_smash": 0},
                                               "headers": {"stride": 20}}},
                     "rom": {"sha1": "ab", "size": 1}}
        regenerated = {"rom": {"size": 1, "sha1": "ab"},
                       "pokedex": {"encounters": {"headers": {"stride": 20},
                                                "methods": {"fishing": 0, "rock_smash": 0, "water": 0, "land": 0}}}}
        first, second = schema.merge(generated, overlay), schema.merge(regenerated, overlay)
        self.assertEqual(list(first), ["rom", "pokedex"])
        self.assertEqual(list(first["rom"]), ["size", "name", "sha1"])
        self.assertEqual(list(first["pokedex"]["encounters"]), ["methods", "headers"])
        self.assertEqual(list(first["pokedex"]["encounters"]["methods"]), ["land", "water", "rock_smash", "fishing"])
        self.assertEqual(repr(second), repr(first))

    def test_empty_generated_profile_keeps_the_overlay_as_is(self):
        overlay = tomllib.loads(OVERLAY)
        self.assertEqual(repr(schema.merge({}, overlay)), repr(overlay))


class LoaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_external_references_stay_outside_values_and_survive_conversion(self):
        reference = '\n[[external_reference]]\nkey = "engine-code"\nname = "Engine code"\naddress = "https://example.org/engine"\n'
        expected = [{"key": "engine-code", "name": "Engine code", "address": "https://example.org/engine"}]
        path = write_dir(self.tmp, overlay=OVERLAY + reference)
        sources = schema.read(path)
        self.assertEqual(sources.external_references, expected)
        self.assertNotIn("external_reference", sources.overlay)
        plain = write_dir(self.tmp, "plain")
        self.assertEqual(repr(Profile.load(path).data), repr(Profile.load(plain).data))
        self.assertEqual([f.id for f in schema.overlay_facts(sources)],
                         [f.id for f in schema.overlay_facts(schema.read(plain))])
        flat = self.tmp / "flat.toml"
        flat.write_text(FLAT + reference)
        before = repr(Profile.load(flat).data)
        self.assertEqual(schema.flat(flat).external_references, expected)
        converted = convert.convert(flat)
        self.assertEqual(repr(Profile.load(converted).data), before)
        self.assertEqual(schema.read(converted).external_references, expected)

    def test_external_references_merge_layers_and_fragments_as_metadata(self):
        first = '\n[[external_reference]]\nkey = "base"\nname = "Base"\naddress = "https://example.org/base"\n'
        second = first.replace('"base"', '"overlay"')
        path = write_dir(self.tmp, generated=GENERATED + first, overlay=None)
        folder = path / "overlay"
        folder.mkdir()
        (folder / "order.txt").write_text("facts.toml\nreferences.toml\n")
        (folder / "facts.toml").write_text(OVERLAY)
        (folder / "references.toml").write_text(second)
        sources = schema.read(path)
        self.assertEqual([r["key"] for r in sources.external_references], ["base", "overlay"])
        self.assertNotIn("external_reference", Profile.load(path).data)
        (folder / "references.toml").write_text(first)
        with self.assertRaisesRegex(RomError, "duplicate external reference key base"):
            Profile.load(path)

    def test_invalid_external_references_fail_in_flat_and_directory_profiles(self):
        record = '\n[[external_reference]]\nkey = "engine"\nname = "Engine"\naddress = "https://example.org/engine"\n'
        cases = {
            "table": record.replace("[[external_reference]]", "[external_reference]"),
            "scalar": 'external_reference = "engine"\n',
            "element": 'external_reference = [1]\n',
            "missing": record.replace('name = "Engine"\n', ''),
            "unknown": record + 'locator = "src/text.c"\n',
            "empty": record.replace('"Engine"', '" "'),
            "type": record.replace('"Engine"', '1'),
            "uppercase": record.replace('"engine"', '"Engine"'),
            "separator": record.replace('"engine"', '"engine--code"'),
            "duplicate": record + record,
            "scheme": record.replace('https:', 'file:'),
            "host": record.replace('https://example.org/engine', 'https:///engine'),
            "whitespace": record.replace('/engine"', '/engine code"'),
            "malformed": record.replace('example.org', '[host'),
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                flat = self.tmp / f"{name}.toml"
                flat.write_text(text + FLAT)
                directory = write_dir(self.tmp, name, overlay=text + OVERLAY)
                for path in (flat, directory):
                    with self.assertRaisesRegex(RomError, "external_reference|external reference"):
                        Profile.load(path)

    def test_directory_merges_overlay_over_generated(self):
        prof = Profile.load(write_dir(self.tmp))
        self.assertEqual(prof.name, "game-1.2.3")
        self.assertEqual(prof.sha1, "00112233445566778899aabbccddeeff00112233")
        self.assertEqual(prof["rom"], {"sha1": "00112233445566778899aabbccddeeff00112233", "size": 0x1000,
                                       "name": "Example"})
        self.assertEqual(prof["font"], [
            {"name": "main", "glyphs": 0x100, "widths": 0x200, "count": 9,
             "evidence": "the renderer reads nine glyphs"},
            {"name": "small", "glyphs": 0x300, "evidence": "pool at 0x64C4"}])
        self.assertEqual(prof["keyboard"]["rows"], 4)
        self.assertEqual(prof["strings"]["include"], [0x10, 0x20, 0x30])
        self.assertNotIn("fact", prof.data)
        self.assertNotIn("discovery", prof.data)

    def test_generated_evidence_reaches_consumers_as_prose(self):
        prof = Profile.load(write_dir(self.tmp, overlay=None))
        self.assertEqual(prof["font"][0]["evidence"], "Pool at 0x6594 pairs the glyphs with the widths.")
        self.assertEqual(prof["keyboard"]["evidence"], "The page reader indexes 0x400.")
        facts = {f.id: f for f in schema.generated_facts(schema.read(self.tmp / "game-1.2.3"))}
        font = facts["font[name=main]"]
        self.assertEqual((font.kind, font.scope, font.tier, font.status, font.source),
                         ("font", "instance", "crossref", "verified", "fonts 1"))
        self.assertEqual(font.evidence.code_sites, (0x6594,))
        self.assertEqual(font.evidence.counts, (("pairs", 1),))

    def test_overlay_fact_controls_generated_consumer_evidence(self):
        overlay = '''[keyboard]
base = 0x400
rows = 4

[[fact]]
id = "keyboard"
tier = "crossref"
evidence = {sentence = "Manual keyboard proof."}
'''
        path = write_fact_dir(self.tmp, "overlay-evidence", "[keyboard]\nbase = 0x400\npages = 2\n",
                              overlay, named=("keyboard",))
        prof = Profile.load(path)
        self.assertEqual(prof["keyboard"], {"base": 0x400, "pages": 2, "rows": 4})

    def test_invalid_directories_are_refused(self):
        record = '\n[[fact]]\nid = "rom.size"\ntier = "shape"\nsource = "identity 1"\nevidence = {sentence = "s"}\n'
        cases = {
            "header": ("[rom]\nsize = 1\n", None, "schema = 1"),
            "unrecorded": (HEADER + "[rom]\nsize = 1\n", None, "needs a [[fact]] record"),
            "unknown": (HEADER + "[rom]\nsize = 1\n" + record + record.replace("rom.size", "rom.name"), None,
                        "records name no fact"),
            "twice": (HEADER + "[rom]\nsize = 1\n" + record + record, None, "recorded twice"),
            "candidate": (HEADER + "[rom]\nsize = 1\n" + record.replace('tier', 'status = "candidate"\ntier'), None,
                          "only verified facts"),
            "tier": (HEADER + "[rom]\nsize = 1\n" + record.replace('"shape"', '"guess"'), None, "tier is one of"),
            "untiered": (HEADER + "[rom]\nsize = 1\n" + record.replace('tier = "shape"\n', ''), None,
                         "needs its tier, source and evidence"),
            "sentence": (HEADER + "[rom]\nsize = 1\n" + record.replace('sentence = "s"', 'method = "m"'), None,
                         "needs a sentence"),
            "prose": (HEADER + "[[font]]\nname = 'a'\nevidence = 'x'\n" + record.replace("rom.size", "font[name=a]"),
                      None, "evidence belongs in its record"),
            "reserved": (HEADER, "[discovery]\nschema = 1\n", "reserved"),
            "source": (HEADER, '[rom]\nsize = 1\n[[fact]]\nid = "rom.size"\nsource = "fonts 1"\n', "source overlay"),
        }
        for name, (generated, overlay, message) in cases.items():
            with self.subTest(name=name):
                path = write_dir(self.tmp, name, generated, overlay)
                with self.assertRaises(RomError) as raised:
                    Profile.load(path)
                self.assertIn(message, str(raised.exception))

    def test_overlay_is_optional_and_records_state_tiers(self):
        prof = Profile.load(write_dir(self.tmp, "only", HEADER, None))
        self.assertEqual(prof.data, {})
        facts = {f.id: f for f in schema.overlay_facts(schema.read(write_dir(self.tmp)))}
        self.assertEqual(facts["font[name=small]"].tier, "ingame")
        self.assertIsNone(facts["font[name=main]"].tier)
        self.assertEqual(facts["font[name=main]"].evidence.sentence, "the renderer reads nine glyphs")
        self.assertEqual(facts["keyboard"].source, "overlay")

    def test_names_select_flat_files_and_directories(self):
        write_dir(self.tmp, "game")
        (self.tmp / "flat.toml").write_text("[rom]\nsha1 = 'ab'\n[project]\ndefault = true\n")
        with chdir(self.tmp), patch.object(profile, "PROFILE_DIR", self.tmp):
            self.assertEqual(profile.profile_paths(), [self.tmp / "flat.toml", self.tmp / "game"])
            self.assertEqual(Profile.by_name("game").path, self.tmp / "game")
            self.assertEqual(Profile.by_name("flat").path, self.tmp / "flat.toml")
            self.assertEqual(Profile.by_name(None).name, "flat")
            self.assertEqual(Profile.by_name(str(self.tmp / "game" / "overlay.toml")).path, self.tmp / "game")
            self.assertEqual(Profile.by_name(str(self.tmp / "game")).name, "game")
            self.assertEqual(profile.source_files(self.tmp / "game"),
                             [self.tmp / "game/profile.toml", self.tmp / "game/overlay.toml"])
            (self.tmp / "game.toml").write_text("[rom]\nsha1 = 'ab'\n")
            with self.assertRaisesRegex(RomError, "keep one"):
                Profile.by_name("game")


class ConverterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_round_trip_of_the_synthetic_flat_profile(self):
        source = self.tmp / "game.toml"
        source.write_text(FLAT, encoding="utf-8")
        target = convert.convert(source)
        self.assertEqual(target, self.tmp / "game")
        self.assertFalse(source.exists())
        self.assertEqual(sorted(p.name for p in target.iterdir()), ["overlay", "profile.toml", "report.md"])
        self.assertEqual((target / "overlay/facts.toml").read_bytes(), convert.OVERLAY_HEADER + FLAT.encode())
        self.assertEqual((target / "overlay/order.txt").read_text(), "facts.toml\n")
        self.assertEqual((target / "profile.toml").read_text(encoding="utf-8"), schema.GENERATED_TEXT)
        self.assertEqual((target / "report.md").read_text(encoding="utf-8"), check.report(target.name, []))
        flat = tomllib.loads(FLAT)
        merged = Profile.load(target)
        expected = {f.id: f.value for f in schema.facts(flat)}
        actual = {f.id: f.value for f in schema.facts(merged.data)}
        self.assertEqual(list(actual), list(expected))
        for fact_id, value in expected.items():
            with self.subTest(fact=fact_id):
                self.assertTrue(check.same(actual[fact_id], value))
        self.assertEqual(repr(merged.data), repr(flat))
        source.write_text(FLAT, encoding="utf-8")
        with self.assertRaisesRegex(RomError, "exists"):
            convert.convert(source)

    def test_refusals_leave_the_flat_profile(self):
        reserved = self.tmp / "reserved.toml"
        reserved.write_text("[[fact]]\nid = 'x'\n")
        with self.assertRaisesRegex(RomError, "reserved"):
            convert.convert(reserved)
        source = self.tmp / "game.toml"
        source.write_text("[rom]\nsha1 = 'ab'\n")
        with patch.object(schema, "load", return_value={}), self.assertRaisesRegex(RomError, "differs"):
            convert.convert(source)
        self.assertTrue(source.exists())
        self.assertFalse((self.tmp / "game").exists())
