import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project_fixture import literal, write_policy
from gen3tl.check import qa
from gen3tl.core.notes import BoundProse, has_prose
from gen3tl.check.policy import PolicyError, effective
from gen3tl.profile.profile import Profile
from gen3tl.project import Registry, ProjectError

class PolicyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.path = self.root / "test.toml"
        self.base = {
            "rom": {"sha1": "0" * 40},
            "font": [],
            "keyboard": {"base_pages": 3, "first_page": 1, "table": []},
            "text_controls": {"droppable": {"article": {"ids": ["s1"], "tokens": ["{STR_VAR_2}"], "evidence": "source article"}}},
            "layout": {"window_width": {"physical": 208}},
            "policy_slots": {"window_width": ["budget"], "placeholders": ["PLAYER"]},
            "graphics": {"edit_facts": {"G1": {"max_tiles": 5, "evidence": "ROM capacity"}},
                         "tilemap": [{"name": "map", "size": 8, "width": 2, "offset": 32}]},
        }
        self.path.write_text("\n".join(f"{k} = {literal(v)}" for k, v in self.base.items()))
        self.raw = Profile.load(self.path)
        from gen3tl.build.keyboard import load_layout
        self.pack_root = self.root / "assets/lang"
        self.loader = patch("gen3tl.build.keyboard.load_layout",
                            side_effect=lambda code, name, base: load_layout(code, name, base, self.pack_root))
        self.loader.start()
        self.addCleanup(self.loader.stop)
        for code in ("ru", "fr"):
            directory = self.pack_root / code
            directory.mkdir(parents=True)
            layout = {"schema": "2.0.0", "language": code,
                      "keyboards": [{"name": code, "like": 0}],
                      "pages": [{"name": code, "keyboard": 0, "look": 1}], "rows": {}}
            (directory / "keyboard.toml").write_text("\n".join(f"{k} = {literal(v)}" for k, v in layout.items()))
        self.policy = {"keyboard": {"layout": "keyboard.toml"},
                       "qa": {"forbidden": {"  ": "double space"}, "droppable": ["article"]},
                       "layout": {"window_width": {"budget": 42}, "placeholders": {"PLAYER": 42}}}

    def merged(self, policy=None, language="ru"):
        write_policy(self.root, self.raw, language, **(self.policy if policy is None else policy))
        return effective(self.raw, language, self.root / "policy.toml", self.root)

    def test_merge_keeps_rom_facts_and_does_not_mutate_raw(self):
        selected = self.merged()
        self.assertEqual(selected["keyboard"]["base_pages"], 3)
        self.assertEqual(selected["layout"]["window_width"], {"physical": 208, "budget": 42})
        self.assertEqual(selected.project_root, self.root)
        self.assertEqual(selected.language, "ru")
        self.assertEqual(self.raw.data, self.base)
        selected["keyboard"]["base_pages"] = 9
        self.assertEqual(self.raw["keyboard"]["base_pages"], 3)

    def test_identity_mismatches_and_missing_policy_fail(self):
        self.merged()
        path = self.root / "policy.toml"
        good = path.read_text()
        for old, new in [('"ru"', '"fr"'), ('"test"', '"other"'), ('"2.0.0"', '"1.0.0"'),
                         ('"' + '0' * 40 + '"', '"' + '1' * 40 + '"')]:
            with self.subTest(new=new):
                path.write_text(good.replace(old, new))
                with self.assertRaisesRegex(PolicyError, "identity"):
                    effective(self.raw, "ru", path, self.root)
        path.unlink()
        with self.assertRaisesRegex(PolicyError, "cannot read language policy"):
            effective(self.raw, "ru", path, self.root)

    def test_rom_addresses_geometry_and_unknown_fields_cannot_be_overridden(self):
        cases = [{"rom": {"sha1": "1" * 40}}, {"keyboard": {"reserved": [0, 100]}},
                 {"layout": {"window_width": {"physical": 1, "budget": 42}, "placeholders": {"PLAYER": 42}}},
                 {"graphics": {"tilemap": [{"name": "map", "offset": 64}]}},
                 {"qa": {"addable": ["unverified"]}}]
        for extra in cases:
            with self.subTest(extra=extra), self.assertRaises(PolicyError):
                self.merged({**self.policy, **extra})

    def test_required_language_budgets_and_keyboard_pages_have_no_fallback(self):
        for key in ("keyboard", "layout"):
            policy = copy.deepcopy(self.policy)
            policy.pop(key)
            with self.subTest(key=key), self.assertRaises(PolicyError):
                self.merged(policy)
        self.raw.data["qa"] = {"optional_tokens": ["{PK}"]}
        with self.assertRaisesRegex(PolicyError, "embedded language rules"):
            self.merged()

    def test_declared_slots_can_omit_numbers_and_overrides_require_reasons(self):
        self.raw.data["layout"]["slot_source"] = {"PLAYER": "player-name"}
        policy = copy.deepcopy(self.policy)
        policy["layout"]["placeholders"] = {}
        policy["layout"]["unverified"] = {"placeholders": ["PLAYER"]}
        selected = self.merged(policy)
        self.assertEqual(selected["policy_unverified"]["placeholders"], [])
        override = {"width": 45, "source": "player-name", "reason": "Printer reserves three pixels"}
        policy["layout"]["slot_override"] = {"PLAYER": override}
        self.assertEqual(self.merged(policy)["layout"]["slot_override"]["PLAYER"], override)
        for change in ({"reason": ""}, {"source": "unknown"}, {"width": True}):
            policy["layout"]["slot_override"]["PLAYER"] = {**override, **change}
            with self.subTest(change=change), self.assertRaises(PolicyError):
                self.merged(policy)

    def test_token_permission_selects_entries_from_rom_capability(self):
        self.raw.data["text_controls"]["addable"] = {
            "center": {"ids": ["one", "two"], "tokens": ["{CLEAR_TO}"], "evidence": "printer"}}
        policy = copy.deepcopy(self.policy)
        policy["qa"]["addable"] = [{"capability": "center", "ids": ["two"]}]
        selected = self.merged(policy)
        self.assertEqual(selected["qa"]["addable"][0]["ids"], ["two"])
        self.assertEqual(self.raw["text_controls"]["addable"]["center"]["ids"], ["one", "two"])
        policy["qa"]["addable"][0]["ids"] = ["foreign"]
        with self.assertRaisesRegex(PolicyError, "outside the ROM capability"):
            self.merged(policy)

    def test_repeated_name_dropping_stays_scoped_to_selected_entries(self):
        self.raw.data["text_controls"]["droppable"]["repeated-name"] = {
            "ids": ["one", "two"], "tokens": ["{PLAYER}"],
            "evidence": "The source repeats the name; the translation names it once."}
        policy = copy.deepcopy(self.policy)
        policy["qa"]["droppable"].append("repeated-name")
        drop = qa.droppable_tokens(self.merged(policy)["qa"])
        translated = "{PLAYER} waits with {RIVAL}."
        for sid, count in (("one", 3), ("two", 2)):
            source = "{PLAYER} " * count + "{RIVAL}"
            with self.subTest(entry=sid):
                self.assertIsNone(qa.placeholders_differ(source, translated, drop[sid]))
                self.assertIsNotNone(qa.placeholders_differ(source, "{PLAYER} waits.", drop[sid]))
                self.assertIsNotNone(qa.placeholders_differ(source, translated, drop.get("other", set())))
                unselected = qa.droppable_tokens(self.merged(language="fr")["qa"])
                self.assertIsNotNone(qa.placeholders_differ(source, translated, unselected.get(sid, set())))


    def test_policy_width_provenance_keeps_character_geometry_measured(self):
        rule = {"name": "example", "base": 32, "policy_width": True, "evidence": "reader"}
        self.assertEqual(qa.window_source(rule)["kind"], qa.PROFILE)
        self.assertEqual(qa.window_source(rule, "chars")["kind"], qa.MEASURED)
        self.assertNotIn("unverified", qa.window_source(rule))
        self.assertTrue(qa.window_source(rule, estimates=frozenset({"example"}))["unverified"])
        self.assertNotIn("unverified", qa.window_source(rule, "chars", frozenset({"example"})))

    def test_unverified_marks_name_configured_slots_only(self):
        self.assertEqual(self.merged()["policy_unverified"], {})
        policy = copy.deepcopy(self.policy)
        policy["layout"]["label_min_width"] = 60
        policy["layout"]["unverified"] = {"window_width": ["budget"], "placeholders": ["PLAYER"],
                                          "label_min_width": True}
        selected = self.merged(policy)
        self.assertEqual(selected["policy_unverified"],
                         {"window_width": ["budget"], "placeholders": ["PLAYER"], "label_min_width": True})
        self.assertEqual(qa.unverified_slots(selected, "window_width"), frozenset({"budget"}))
        self.assertEqual(qa.unverified_slots(selected, "code_fill"), frozenset())
        self.assertNotIn("policy_unverified", self.raw.data)
        for marks in ({"window_width": ["physical"]}, {"window_width": ["budget", "budget"]},
                      {"target_width": True}, {"label_min_width": 1}, {"geometry": []}, {"placeholders": "PLAYER"}):
            bad = copy.deepcopy(policy)
            bad["layout"]["unverified"] = marks
            with self.subTest(marks=marks), self.assertRaises(PolicyError):
                self.merged(bad)

    def test_graphics_policy_respects_capacity_and_map_geometry(self):
        policy = copy.deepcopy(self.policy)
        policy["graphics"] = {"edit": [{"id": "G1", "png": "translation/one.png", "tiles": 5,
                                         "form": "areas", "areas": [[0, 0, 8, 8]]}],
                              "tilemap": [{"name": "map", "cells": [{"row": 1, "column": 1, "tile": 4}]}]}
        merged = self.merged(policy)
        self.assertEqual(merged["graphics"]["edit"][0]["evidence"], "ROM capacity")
        self.assertEqual(merged["graphics"]["tilemap"][0]["offset"], 32)
        self.raw.data["graphics"]["edit_facts"]["G1"]["evidence"] = BoundProse("Measured capacity.\nVerified tiles.")
        projected = self.merged(policy)["graphics"]["edit"][0]["evidence"]
        self.assertTrue(has_prose(projected))
        self.assertEqual(projected, "Measured capacity.\nVerified tiles.")
        self.raw.data["graphics"]["edit_facts"]["G1"]["evidence"] = BoundProse("")
        self.assertNotIn("evidence", self.merged(policy)["graphics"]["edit"][0])
        self.raw.data["graphics"]["edit_facts"]["G1"]["evidence"] = "ROM capacity"
        for field, value in (("tiles", 6), ("png", "../other.png")):
            bad = copy.deepcopy(policy)
            bad["graphics"]["edit"][0][field] = value
            with self.subTest(field=field), self.assertRaises(PolicyError):
                self.merged(bad)
        policy["graphics"]["tilemap"][0]["cells"][0]["row"] = 2
        with self.assertRaises(PolicyError):
            self.merged(policy)


    def test_labels_use_workspace_assets_and_inactive_maps_are_omitted(self):
        policy = copy.deepcopy(self.policy)
        policy["graphics"] = {"edit": [{"id": "G1", "label": "translation/glyphs.json",
                                         "form": "areas", "areas": [], "tiles": 5}]}
        selected = self.merged(policy)
        self.assertEqual(selected["graphics"]["tilemap"], [])
        self.assertEqual(selected["graphics"]["edit"][0]["label"], "translation/glyphs.json")
        for extra in ({"label": "../outside.json"}, {"png": "also.png"}, {"label": ""}):
            invalid = copy.deepcopy(policy)
            invalid["graphics"]["edit"][0].update(extra)
            with self.subTest(extra=extra), self.assertRaises(PolicyError):
                self.merged(invalid)

    def test_registry_requires_workspace_relative_policy(self):
        text = '[defaults]\nprofile="test"\nlanguage="ru"\n[[project]]\nprofile="test"\nlanguage="ru"\nroot="."\n'
        for extra in ("", 'policy="../foreign.toml"\n'):
            (self.root / "projects.toml").write_text(text + extra)
            with self.assertRaises(ProjectError):
                Registry.load(self.root)
