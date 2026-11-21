import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gen3tl.profile.profile import Profile, source_files
from gen3tl.profile import convert, schema
from gen3tl.families import knowledge

class FamilyLayerTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.family = self.root / "families/test-engine/engine.toml"
        self.family.parent.mkdir(parents=True)
        self.family.write_text('''[family]
schema = 1
name = "test-engine"
version = "1.0.0"
sources = ["Engine text interface"]

[rom]
engine = "frlg"

[[font]]
name = "main"
count = 512
codes = [1]

[[fact]]
id = "rom.engine"
scope = "family"
tier = "crossref"
source = "test-engine/engine.toml 1.0.0"
evidence = {method = "engine-source", sentence = "The engine uses the FRLG text interface."}

[[fact]]
id = "font[name=main]"
scope = "family"
tier = "crossref"
source = "test-engine/engine.toml 1.0.0"
evidence = {method = "engine-source", sentence = "The printer selects the standard glyph count."}
''')
        self.raw_family = self.family.read_text()
        self.directory = self.root / "game"
        self.directory.mkdir()
        (self.directory / "profile.toml").write_text('''[discovery]
schema = 1

[[font]]
name = "main"
count = 256

[[fact]]
id = "font[name=main]"
tier = "crossref"
source = "font scanner 1"
evidence = {method = "accessor", sentence = "The instance printer selects fewer glyphs."}
''')
        (self.directory / "overlay.toml").write_text('''[family]
name = "test-engine"

[engine]
family = "test-engine"

[rom]
sha1 = "00112233445566778899aabbccddeeff00112233"

[[font]]
name = "main"
glyphs = 256
widths = 512
count = 128
evidence = "The instance uses the first part of both tables."

[[font]]
name = "extra"
count = 8
''')
        self.patch = patch.object(knowledge, "FAMILY_DIR", self.family.parent.parent)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_explicit_defaults_then_generated_then_overlay(self):
        prof = Profile.load(self.directory)
        self.assertEqual(prof["rom"], {"engine": "frlg", "sha1": "00112233445566778899aabbccddeeff00112233"})
        self.assertEqual(prof["font"], [
            {"name": "main", "count": 128, "codes": [1], "glyphs": 256, "widths": 512,
             "evidence": "The instance uses the first part of both tables."},
            {"name": "extra", "count": 8}])
        self.assertNotIn("family", prof.data)
        self.assertNotIn("fact", prof.data)
        self.assertNotIn("discovery", prof.data)
        (self.directory / "overlay.toml").write_text('[family]\nname = "test-engine"\n')
        self.assertEqual(Profile.load(self.directory)["font"][0]["count"], 256)
        (self.directory / "profile.toml").write_text('[discovery]\nschema = 1\n')
        self.assertEqual(Profile.load(self.directory)["font"][0]["count"], 512)

    def test_flat_binding_and_conversion_preserve_effective_values(self):
        path = self.root / "flat.toml"
        path.write_text('[family]\nname = "test-engine"\n[rom]\nsha1 = "ab"\n')
        before = Profile.load(path).data
        converted = convert.convert(path)
        self.assertEqual(repr(Profile.load(converted).data), repr(before))
        self.assertEqual(source_files(converted), [self.family, converted / "profile.toml",
                                                 converted / "overlay/order.txt", converted / "overlay/facts.toml"])

    def test_legacy_identity_does_not_require_family_knowledge(self):
        path = self.root / "flat.toml"
        path.write_text('[engine]\nfamily = "unknown"\n')
        self.assertEqual(Profile.load(path).data, {"engine": {"family": "unknown"}})
        self.assertEqual(source_files(path), [path])

    def test_overlay_directory_keeps_family_inputs_and_precedence(self):
        before = Profile.load(self.directory).data
        single = self.directory / "overlay.toml"
        identity, fonts = single.read_text(encoding="utf-8").split("[[font]]", 1)
        overlay = self.directory / "overlay"
        overlay.mkdir()
        (overlay / "identity.toml").write_text(identity, encoding="utf-8")
        (overlay / "fonts.toml").write_text("[[font]]" + fonts, encoding="utf-8")
        (overlay / "order.txt").write_text("identity.toml\nfonts.toml\n", encoding="utf-8")
        single.unlink()
        self.assertEqual(repr(Profile.load(self.directory).data), repr(before))
        self.assertEqual(source_files(self.directory), [self.family, self.directory / "profile.toml",
                         overlay / "order.txt", overlay / "identity.toml", overlay / "fonts.toml"])

    def test_source_files_include_family_and_keep_provenance_separate(self):
        self.assertEqual(source_files(self.directory),
                         [self.family, self.directory / "profile.toml", self.directory / "overlay.toml"])
        sources = schema.read(self.directory)
        self.assertEqual(sources.family_file, self.family)
        self.assertEqual([f.id for f in sources.family_facts], ["rom.engine", "font[name=main]"])
        self.assertTrue(all(f.scope == "family" and f.evidence and f.tier == "crossref" for f in sources.family_facts))
        self.assertEqual(len(schema.generated_facts(sources)), 1)
        self.assertNotIn("evidence", sources.family_data["font"][0])

    def test_loads_do_not_share_mutable_family_defaults(self):
        before = copy.deepcopy(Profile.load(self.directory).data)
        first = Profile.load(self.directory)
        first["font"][0]["codes"].append(9)
        first["rom"]["engine"] = "changed"
        self.assertEqual(Profile.load(self.directory).data, before)

    def test_missing_invalid_and_conflicting_bindings_fail(self):
        cases = [('[family]\nname = "missing"\n', "cannot read"),
                 ('[family]\nname = "../outside"\n', "identifier"),
                 ('[family]\nname = 1\n', "identifier"),
                 ('[family]\nname = "test-engine"\nfile = "other"\n', "only a name"),
                 ('[family]\nname = "test-engine"\n[engine]\nfamily = "different"\n', "engine.family")]
        for text, message in cases:
            with self.subTest(text=text):
                (self.directory / "overlay.toml").write_text(text)
                with self.assertRaisesRegex(schema.ProfileError, message):
                    Profile.load(self.directory)
        (self.directory / "profile.toml").write_text('[discovery]\nschema = 1\n[family]\nname = "different"\n')
        (self.directory / "overlay.toml").write_text('[family]\nname = "test-engine"\n')
        with self.assertRaisesRegex(schema.ProfileError, "declarations differ"):
            Profile.load(self.directory)

    def test_unverified_and_malformed_family_facts_fail(self):
        cases = [('scope = "family"', 'scope = "instance"', "scope"),
                 ('tier = "crossref"', 'status = "candidate"', "verified"),
                 ('source = "test-engine/engine.toml 1.0.0"', 'source = "overlay"', "source"),
                 ('version = "1.0.0"', 'version = "01.0.0"', "version"),
                 ('schema = 1', 'schema = true', "schema"),
                 ('id = "rom.engine"', 'id = "unknown"', "no fact"),
                 ('count = 512', 'count = 512\nevidence = "inline"', "fact records")]
        for old, new, message in cases:
            with self.subTest(new=new):
                self.family.write_text(self.raw_family.replace(old, new, 1))
                with self.assertRaisesRegex(schema.ProfileError, message):
                    Profile.load(self.directory)
        self.family.write_text(self.raw_family[:self.raw_family.index('[[fact]]')])
        with self.assertRaisesRegex(schema.ProfileError, "records"):
            Profile.load(self.directory)

class EngineFamilyFactsTest(unittest.TestCase):
    def test_firered_cfru_defaults_carry_no_instance_addresses(self):
        family = knowledge.load("firered-cfru")
        self.assertEqual(set(family.data), {"rom", "font", "keyboard"})
        self.assertEqual(family.data["rom"], {"engine": "frlg"})
        self.assertEqual(family.data["keyboard"], {"base_pages": 3, "first_page": 1, "opening_keyboards": [1, 0]})
        for font in family.data["font"]:
            self.assertLessEqual(set(font), {"name", "count", "codes", "format"})
            self.assertEqual(font["count"], 512)
        self.assertEqual(family.data["font"][-1]["format"], "8x16")
        self.assertTrue(all(f.scope == "family" and f.evidence and f.tier == "crossref" for f in family.facts))
        self.assertNotRegex(family.path.read_text(), r"0x[0-9A-Fa-f]{5,}")
