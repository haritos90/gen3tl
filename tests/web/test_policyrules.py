from copy import deepcopy
import json
import contextlib
import io
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from gen3tl.catalog.po import KEY_ONLY, SOURCE_FIELD, PoEntry, read, write
from gen3tl.check.layout import Measure
from gen3tl.check.policy import PolicyError, effective, patch_rules
from gen3tl.core.charmap import Charmap
from gen3tl.profile.languagerules import load
from gen3tl.profile.profile import Profile
from gen3tl.profile.schema import ProfileError
from gen3tl.project import Registry
from gen3tl.web.editorimport import read_export
from gen3tl.web.editorcheck import Checker
from gen3tl.web.editorrules import rules_data
from gen3tl.web.policyrules import patches, preview
from gen3tl.web import submission
from project_fixture import literal, rom_evidence, write_policy
from test_codereplace import FALLBACK

def fixture():
    return {
        "rom": {"sha1": "ab" * 20},
        "font_style": {"style": "main"},
        "code_replacement": [deepcopy(FALLBACK)],
        "text_prefixes": [{"name": "marker", "pointer": 32, "source": rom_evidence(64, "a1ff"),
                           "evidence": "Dedicated pointer.",
                           "limits": {"table": "names", "where": "list", "original_width": 40,
                                      "original_chars": 8, "width": 48, "chars": 10}}],
        "table": [{"name": "names", "windows": [{"where": "list", "indices": [0], "width": 40, "chars": 8,
                                                  "evidence": "Measured list."}]}],
        "layout": {"window_width": {"names:0": 40}},
        "language_rules": [
            {"id": "units", "kind": "policy", "title": "Units", "description": "Unit conversion.",
             "related": [], "evidence": "Verified printer.", "choices": ["original", "enabled"],
             "policies": [{"kind": "code_replacement", "name": "units", "fields": ["unit", "pool"],
                           "field_limits": {"unit": {"bytes": 4, "pixels": 18},
                                            "pool": {"bytes": 4, "pixels": 18}}}]},
            {"id": "marker", "kind": "policy", "title": "Marker", "description": "Number prefix.",
             "related": [], "evidence": "Verified pointer.", "choices": ["original", "off"],
             "policies": [{"kind": "text_prefix", "name": "marker"}]},
        ],
        "selected_code_replacements": {"units": {"unit": "m"}},
    }

def proposal(model, value):
    return {"id": model["id"], "capability_sha256": model["capability_sha256"],
            "base": deepcopy(model["policies"][0]["current"]), "value": value}

class PolicyRuleTest(unittest.TestCase):
    def setUp(self):
        self.data = fixture()
        self.models = rules_data(self.data, [])["rules"]
        self.cm = Charmap()
        self.context = self.cm, set(self.cm.decode_table), Measure(lambda _: 6, {})

    def test_presets_and_custom_fields_resolve_to_named_policy_patches(self):
        before = deepcopy(self.data)
        records = [proposal(self.models[0], {"enabled": True, "fields": {"unit": "cm"}}),
                   proposal(self.models[1], {"enabled": True})]
        resolved = patches(self.data, records, *self.context)
        self.assertEqual([(p["kind"], p["name"]) for p in resolved],
                         [("code_replacement", "units"), ("text_prefix", "marker")])
        report = preview(self.models[0], {"value": records[0]["value"]}, *self.context)
        self.assertTrue(report["valid"])
        self.assertEqual(report["fields"][0]["used"], 3)
        self.assertEqual(report["fields"][0]["pixels"], 12)
        self.assertEqual(report["changes"], [records[0]])
        self.assertEqual(self.data, before)
        self.assertEqual(patches(self.data, [proposal(self.models[0], records[0]["base"])]), [])

    def test_custom_units_reject_empty_tokens_bytes_pixels_and_missing_glyphs(self):
        cases = [({}, "required"), ({"unit": ""}, "nonempty"), ({"unit": " "}, "nonempty"),
                 ({"unit": "{PLAYER}"}, "literal"), ({"unit": "\\n"}, "literal"),
                 ({"unit": "abcd"}, "byte budget"), ({"unit": "a", "address": "b"}, "declared")]
        for fields, message in cases:
            with self.subTest(fields=fields), self.assertRaisesRegex(PolicyError, message):
                patches(self.data, [proposal(self.models[0], {"enabled": True, "fields": fields})], *self.context)
        record = proposal(self.models[0], {"enabled": True, "fields": {"unit": "abc"}})
        with self.assertRaisesRegex(PolicyError, "pixel budget"):
            patches(self.data, [record], self.cm, self.context[1], Measure(lambda _: 7, {}))
        with self.assertRaisesRegex(PolicyError, "glyph"):
            patches(self.data, [record], self.cm, set(), self.context[2])
        with self.assertRaisesRegex(PolicyError, "measurements"):
            patches(self.data, [record])
        self.assertTrue(preview(self.models[0], {"value": record["value"]}, *self.context)["valid"])

    def test_unverified_limits_allow_existing_presets_only(self):
        self.data["language_rules"][0]["limit_status"] = "unverified"
        model = rules_data(self.data, [])["rules"][0]
        self.assertTrue(preview(model, {"value": {"enabled": False, "fields": {}}})["valid"])
        self.assertTrue(preview(model, {})["valid"])
        self.assertFalse(preview(model, {"value": {"enabled": True, "fields": {"unit": "cm"}}},
                                 *self.context)["valid"])
        del self.data["language_rules"][0]["limit_status"]
        del self.data["language_rules"][0]["policies"][0]["field_limits"]
        model = rules_data(self.data, [])["rules"][0]
        with self.assertRaisesRegex(PolicyError, "unverified"):
            patches(self.data, [proposal(model, {"enabled": True, "fields": {"unit": "cm"}})], *self.context)

    def test_fallback_is_omitted_and_original_mode_carries_no_fields(self):
        record = proposal(self.models[0], {"enabled": True, "fields": {"unit": "m", "pool": "kg"}})
        self.assertEqual(patches(self.data, [record], *self.context)[0]["value"], record["value"])
        record["value"] = {"enabled": False, "fields": {"unit": "m"}}
        with self.assertRaisesRegex(PolicyError, "original units"):
            patches(self.data, [record])
        self.assertEqual(self.models[0]["policies"][0]["capability"]["fallbacks"], {"pool": {"pointer": 512}})

    def test_untrusted_patch_shapes_and_stale_baselines_are_rejected(self):
        record = proposal(self.models[1], {"enabled": True})
        cases = [None, {}, {**record, "offset": 32}, {**record, "id": "unknown"},
                 {**record, "capability_sha256": "0" * 64}, {**record, "base": {"enabled": True}},
                 {**record, "value": {"enabled": 1}}, {**record, "value": {"enabled": True, "bytes": [255]}}]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(PolicyError):
                patches(self.data, [value])
        for records in ({}, [record, record]):
            with self.assertRaises(PolicyError):
                patches(self.data, records)
        self.data["text_prefixes"][0]["pointer"] += 4
        with self.assertRaisesRegex(PolicyError, "capability changed"):
            patches(self.data, [record])

    def test_overlapping_capabilities_and_read_only_rules_are_rejected(self):
        duplicate = deepcopy(self.data["language_rules"][1])
        duplicate["id"] = "second"
        self.data["language_rules"].append(duplicate)
        models = rules_data(self.data, [])["rules"]
        with self.assertRaisesRegex(PolicyError, "overlap"):
            patches(self.data, [proposal(model, {"enabled": True}) for model in models[1:]])
        self.data["language_rules"][0].update(kind="catalog_text", policies=[], choices=[])
        model = rules_data(self.data, [])["rules"][0]
        with self.assertRaisesRegex(PolicyError, "no supported"):
            patches(self.data, [{"id": model["id"]}])

    def test_metadata_requires_positive_byte_and_pixel_limits(self):
        for limit in ({"bytes": 1, "pixels": 18}, {"bytes": 4}, {"bytes": 4, "pixels": 0},
                      {"bytes": True, "pixels": 18}, {"bytes": 4, "pixels": 18, "offset": 0}):
            self.data["language_rules"][0]["policies"][0]["field_limits"]["unit"] = limit
            with self.subTest(limit=limit), self.assertRaises(ProfileError):
                load(self.data)

    def test_browser_checker_uses_the_policy_validator(self):
        doc = {"fonts": {"main": "main", "styles": {"main": {"pack": {}, "format": "16x16"}}},
               "charmap": {}, "placeholders": {"widths": {}, "default": 60},
               "engine_rules": rules_data(self.data, [])}
        checker = Checker(doc, {"main": bytes([6] * 256)})
        request = {"value": {"enabled": True, "fields": {"unit": "cm"}}}
        report = json.loads(checker.rule_preview_json("units", "[]", "[]", json.dumps(request)))
        self.assertTrue(report["valid"])
        self.assertEqual(report["fields"][0]["used"], 3)
        request["draft"] = proposal(self.models[0], request["value"])
        request["draft"]["base"]["fields"]["unit"] = "stale"
        report = json.loads(checker.rule_preview_json("units", "[]", "[]", json.dumps(request)))
        self.assertFalse(report["valid"])
        self.assertEqual(report["changes"], [])

    def test_toml_updates_keep_unrelated_values_and_comments(self):
        text = '''# Project settings.
text_prefixes = [
  "other",
]
[policy]
version = "2.0.0"
[code_replacements.units]
unit = "m"
# Independent capability.
[code_replacements.other]
field = "kept"
[qa]
droppable = ["keep"]
'''
        records = [proposal(self.models[0], {"enabled": False, "fields": {}}),
                   proposal(self.models[1], {"enabled": True})]
        resolved = patches(self.data, records)
        out = patch_rules(text, resolved)
        expected = tomllib.loads(text)
        expected["code_replacements"].pop("units")
        expected["text_prefixes"].append("marker")
        self.assertEqual(tomllib.loads(out), expected)
        self.assertIn('# Independent capability.', out)
        self.assertIn('[qa]\ndroppable = ["keep"]', out)
        with self.assertRaisesRegex(PolicyError, "changed after"):
            patch_rules(out, resolved)
        with self.assertRaisesRegex(PolicyError, "duplicate"):
            patch_rules(text, [resolved[1], resolved[1]])

    def test_absent_and_inline_sections_round_trip(self):
        self.data["selected_code_replacements"] = {}
        model = rules_data(self.data, [])["rules"][0]
        resolved = patches(self.data, [proposal(model, {"enabled": True, "fields": {"unit": "cm"}})], *self.context)
        text = '# Other values.\n[policy]\nversion = "2.0.0"\n'
        out = patch_rules(text, resolved)
        self.assertEqual(tomllib.loads(out)["code_replacements"], {"units": {"unit": "cm"}})
        off = {**resolved[0], "base": resolved[0]["value"], "value": {"enabled": False, "fields": {}}}
        self.assertEqual(tomllib.loads(patch_rules(out, [off]))["code_replacements"], {})

class PolicySubmissionTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.raw = Profile(fixture(), self.root / "sample.toml")
        self.raw.data.pop("selected_code_replacements")
        self.raw.path.write_text("\n".join(f"{key} = {literal(value)}" for key, value in self.raw.data.items()))
        write_policy(self.root, self.raw, "xx", code_replacements={"units": {"unit": "m"}})
        (self.root / "projects.toml").write_text('[defaults]\nprofile="sample"\nlanguage="xx"\n'
                                                '[[project]]\nprofile="sample"\nlanguage="xx"\nroot="."\npolicy="policy.toml"\n')
        self.registry = Registry.load(self.root)
        self.project = self.registry.projects[0]
        self.catalog_path = self.project.catalog / "entries.po"
        write(self.catalog_path, [PoEntry("key", "key", "Old")],
              {SOURCE_FIELD: KEY_ONLY, "Project-Id-Version": "sample", "Language": "xx"})
        self.policy = self.root / "policy.toml"
        self.prof = effective(self.raw, "xx", self.policy, self.root)
        self.models = rules_data(self.prof, [])["rules"]
        self.path = self.root / "export.json"

    def export(self, records, entries=None, **extra):
        self.path.write_text(json.dumps({"profile": "sample", "language": "xx", "rom_sha1": self.prof.sha1,
                                        "entries": entries or [], "engine_rules": records, **extra}))

    def apply(self, check=False):
        with patch.object(Profile, "by_name", return_value=self.raw):
            return submission.apply(self.path, self.registry, check=check)

    def test_import_rejects_local_rule_writes_and_validates_carried_rules(self):
        record = proposal(self.models[1], {"enabled": True})
        self.export([record])
        with self.assertRaisesRegex(ValueError, "submission review"):
            read_export(self.path, "sample", "xx", self.prof.sha1, {})
        result = read_export(self.path, "sample", "xx", self.prof.sha1, {},
                             carried=("engine_rules",), rule_profile=self.prof)
        self.assertEqual(result["rule_patches"][0]["name"], "marker")
        record["base"]["enabled"] = True
        self.export([record])
        with self.assertRaises(PolicyError):
            read_export(self.path, "sample", "xx", self.prof.sha1, {},
                        carried=("engine_rules",), rule_profile=self.prof)

    def test_rule_only_submission_checks_without_writing_then_updates_policy(self):
        self.export([proposal(self.models[0], {"enabled": False, "fields": {}}),
                     proposal(self.models[1], {"enabled": True})])
        before = self.policy.read_bytes(), self.catalog_path.read_bytes()
        result = self.apply(check=True)
        self.assertEqual(result.engine_rules, ["units", "marker"])
        self.assertEqual(result.files, [Path("policy.toml")])
        self.assertEqual((self.policy.read_bytes(), self.catalog_path.read_bytes()), before)
        self.apply()
        selected = effective(self.raw, "xx", self.policy, self.root)
        self.assertEqual(selected["selected_code_replacements"], {})
        self.assertEqual(selected["selected_text_prefixes"][0]["name"], "marker")
        self.assertEqual(self.catalog_path.read_bytes(), before[1])
        with self.assertRaisesRegex(ValueError, "changed"):
            self.apply()

    def test_policy_and_catalog_change_together_after_full_validation(self):
        record = proposal(self.models[1], {"enabled": True})
        self.export([record], [{"id": "key", "base": "Old", "translation": "New"}])
        result = self.apply()
        self.assertEqual(result.changed, ["key"])
        self.assertEqual(result.engine_rules, ["marker"])
        entry = read(self.catalog_path)[1][0]
        self.assertEqual(entry.msgstr, "New")
        self.assertFalse(entry.fuzzy)   # approved before the submission

    def test_policy_report_shows_combined_selections_and_approval_gate(self):
        self.export([proposal(self.models[0], {"enabled": False, "fields": {}}),
                     proposal(self.models[1], {"enabled": True})])
        result = self.apply(check=True)
        body = submission.pull_request(7, result)
        for text in ("Engine language rules", "Original", "Current", "Proposed", "unit", "enabled",
                     "Approve the combined catalog and policy behavior", "before merge"):
            self.assertIn(text, body)

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)

    def base(self):
        self.git("init", "-q")
        self.git("add", ".")
        self.git("-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base")

    def check_pr(self):
        from gen3tl.check.prcheck import run
        out = self.root / "report"
        with patch.object(Profile, "by_name", return_value=Profile.load(self.raw.path)), \
                patch.object(Profile, "load_rom", side_effect=AssertionError("ROM access forbidden")), \
                contextlib.redirect_stdout(io.StringIO()):
            result = run("HEAD", out, self.registry)
        return result, (out / "report.md").read_text()

    def test_policy_only_pr_reports_behavior_even_without_measurements(self):
        self.base()
        self.policy.write_text(patch_rules(self.policy.read_text(), [
            {"kind": "text_prefix", "name": "marker", "base": {"enabled": False}, "value": {"enabled": True}}]))
        result, report = self.check_pr()
        self.assertEqual(result, 1)
        self.assertIn("Engine language rules", report)
        self.assertIn("Original | Current | Proposed", report)
        self.assertIn("No translation changes", report)
        self.assertIn("No such file", report)

    def test_metadata_only_pr_and_removed_rules_cannot_pass_silently(self):
        self.base()
        for rules in ([{**rule, "description": "Changed behavior."} for rule in self.raw.data["language_rules"]], []):
            data = {**self.raw.data, "language_rules": rules}
            self.raw.path.write_text("\n".join(f"{key} = {literal(value)}" for key, value in data.items()))
            result, report = self.check_pr()
            self.assertEqual(result, 1)
            self.assertIn("Rule capability changed" if rules else "Rule declaration added or removed", report)

    def test_state_only_change_is_detected_with_no_text_difference(self):
        from gen3tl.check.prcheck import changes
        self.catalog_path.write_text(self.catalog_path.read_text().replace('msgstr "Old"', 'msgstr ""'))
        self.base()
        header, entries = read(self.catalog_path)
        entries[0].set_translation("", "empty")
        write(self.catalog_path, entries, header)
        with patch.object(Profile, "by_name", return_value=self.raw):
            selected = changes(self.registry, "HEAD")
        self.assertEqual(len(selected), 1)
        self.assertEqual((selected[0].before, selected[0].after), ("", ""))
        self.assertEqual((selected[0].before_state, selected[0].after_state), ("original", "empty"))


    def test_invalid_rule_does_not_write_valid_catalog_edit(self):
        record = proposal(self.models[1], {"enabled": True})
        record["address"] = 32
        self.export([record], [{"id": "key", "base": "Old", "translation": "New"}])
        before = self.policy.read_bytes(), self.catalog_path.read_bytes()
        with self.assertRaises(PolicyError):
            self.apply()
        self.assertEqual((self.policy.read_bytes(), self.catalog_path.read_bytes()), before)

    def test_custom_submission_requires_current_measurements(self):
        self.export([proposal(self.models[0], {"enabled": True, "fields": {"unit": "cm"}})])
        before = self.policy.read_bytes()
        with self.assertRaisesRegex(ValueError, "current editor snapshot"):
            self.apply()
        self.assertEqual(self.policy.read_bytes(), before)

    def test_site_and_noop_submissions_cannot_ignore_rules(self):
        self.export([proposal(self.models[1], {"enabled": False})])
        with self.assertRaisesRegex(ValueError, "nothing to submit"):
            self.apply()
        self.export([proposal(self.models[1], {"enabled": True})], profile="site")
        with self.assertRaisesRegex(ValueError, "text entries only"):
            self.apply()
