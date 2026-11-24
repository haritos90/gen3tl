from test_roots import TOOLKIT_ROOT
"""Editordata labels checks and fixtures."""

import sys
from pathlib import Path
import json
import tempfile
import unittest

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.web import editordata
from gen3tl.catalog import keys
from gen3tl.catalog.po import PoEntry, write
from gen3tl.profile.profile import Profile
from gen3tl.project import Registry, registry_scope
from project_fixture import workspace_project
from editordata_support import entry, setUpModule


class ProfileLabelsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.prof = Profile.load(workspace_project(self.root, "aa"))
        peer = self.root / "peer"
        peer.mkdir()
        workspace_project(peer, "bb")
        with (self.root / "projects.toml").open("a", encoding="utf-8") as stream:
            stream.write('\n[[project]]\nprofile = "game"\nlanguage = "bb"\n'
                         'root = "peer"\npolicy = "policy.toml"\n')
        self.enterContext(registry_scope(self.root))
        self.paths = editordata.profile_label_inputs(self.prof)
        self.prof.data["layout"] = {"table_window": [
            {"name": "menu", "where": "message s0000100"},
            {"name": "menu", "where": "second message"},
        ]}

    def test_contexts_follow_profile_order_and_share_display_languages(self):
        self.prof.data["table"] = [{"name": "names", "windows": [{"where": "name box"}]}]
        self.prof.data["sprite_font"] = [{"name": "hud", "pair": {"where": "paired box"}}]
        rows = [PoEntry("sprite_font[name=hud].pair.where", "paired box", "paired label"),
                PoEntry("table[name=names].windows[#0].where", "name box", "name label"),
                PoEntry("layout.table_window[name=menu#2].where", "second message", "second label"),
                PoEntry("layout.table_window[name=menu].where", "message s0000100", "label s0000100")]
        write(self.paths["aa"], rows, {})
        write(self.paths["bb"], [PoEntry(rows[-1].msgctxt, rows[-1].msgid, "peer label")], {})
        aliases = keys.Aliases([("menu:message", 0x100, "s0000100")])
        texts = editordata.where_texts(self.prof, aliases)
        self.assertEqual(list(texts), ["message s0000100", "message menu:message", "second message",
                                      "name box", "paired box"])
        self.assertEqual(texts["message s0000100"], {"aa": "label s0000100", "bb": "peer label"})
        self.assertEqual(texts["message menu:message"], {"aa": "label menu:message", "bb": "peer label"})
        self.assertEqual(texts["second message"], {"aa": "second label"})
        self.assertEqual(texts["name box"], {"aa": "name label"})
        self.assertEqual(texts["paired box"], {"aa": "paired label"})

    def test_a_fill_source_label_only_the_profile_defines_is_translated_by_its_field(self):
        self.prof.data["layout"]["fill_source"] = {
            "cured-berry": {"label": "Frostbite-curing berry"}, "old-name": {"label": "Renamed label"}}
        write(self.paths["aa"], [
            PoEntry("layout.fill_source.cured-berry.label", "Frostbite-curing berry", "berry label"),
            PoEntry("layout.fill_source.old-name.label", "Old label", "stale label"),
            PoEntry("layout.fill_source.cured-berry.where", "Frostbite-curing berry", "other field"),
        ], {})
        texts = editordata.where_texts(self.prof, keys.EMPTY)
        self.assertEqual(texts, {"Frostbite-curing berry": {"aa": "berry label"}})

    def test_missing_empty_and_changed_sources_keep_english_fallback(self):
        self.assertEqual(editordata.where_texts(self.prof, keys.EMPTY), {})
        write(self.paths["aa"], [
            PoEntry("layout.table_window[name=menu].where", "old message", "stale label"),
            PoEntry("layout.table_window[name=menu#2].where", "second message"),
            PoEntry("layout.table_window[name=missing].where", "second message", "unbound label"),
        ], {})
        self.assertEqual(editordata.where_texts(self.prof, keys.EMPTY), {})

    def test_data_key_tracks_creation_and_edits_in_other_display_languages(self):
        from gen3tl.web.editorsave import data_key
        project = Registry.load(self.root).resolve(self.prof, "aa")
        self.assertEqual(self.paths["aa"], project.profile_labels)
        inputs = editordata.editor_inputs(project, self.prof, project.language)
        self.assertTrue(set(self.paths.values()) <= set(inputs))
        before = data_key(project.catalog, self.prof.path, "1.0.0", inputs)
        path = self.paths["bb"]
        entry = PoEntry("layout.table_window[name=menu].where", "message s0000100", "label")
        write(path, [entry], {})
        created = data_key(project.catalog, self.prof.path, "1.0.0", inputs)
        self.assertNotEqual(before, created)
        entry.msgstr = "updated label"
        write(path, [entry], {})
        self.assertNotEqual(created, data_key(project.catalog, self.prof.path, "1.0.0", inputs))

    def test_editor_generation_and_startup_use_the_same_inputs(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch

        from gen3tl.cli.web import editor
        from gen3tl.web import editorsave

        project = Registry.load(self.root).resolve(self.prof, "aa")
        args = SimpleNamespace(project=project, profile=self.prof.name, lang=project.language,
                               data=self.root / "editor", po_root=project.catalog, port=0)
        target = args.data / self.prof.name / project.language
        server = Mock(server_address=("127.0.0.1", 0))

        def generate(command, **kwargs):
            out = Path(command[command.index("--out") + 1])
            out.mkdir()
            (out / "index.json").write_text(json.dumps({"schema": editordata.SCHEMA}))
            return SimpleNamespace(returncode=0)

        with project.activate(), \
                patch("gen3tl.cli.web.Profile.by_name", return_value=self.prof), \
                patch.object(editordata, "toolkit_fingerprint", return_value="toolkit"), \
                patch.object(editordata, "write_languages"), patch.object(editordata, "write_keys"), \
                patch("gen3tl.web.editorcheck.bundle_current", return_value=True), \
                patch.object(editordata, "editor_inputs", wraps=editordata.editor_inputs) as inputs, \
                patch.object(editorsave, "data_key", wraps=editorsave.data_key) as data_key, \
                patch.object(editorsave, "local_server", return_value=server), \
                patch("subprocess.run", side_effect=generate) as generated:
            expected = editordata.editor_inputs(project, self.prof, project.language)
            inputs.reset_mock()
            self.assertEqual(editor(args), 0)
            stamp = (target / editorsave.STAMP).read_text()
            self.assertEqual(editor(args), 0)
            self.assertEqual(inputs.call_count, 2)
            self.assertEqual(data_key.call_count, 4)
            for call in data_key.call_args_list:
                self.assertEqual(call.args[3], expected)
            self.assertEqual(stamp, data_key(project.catalog.resolve(), self.prof.path, "toolkit", expected))
            self.assertEqual(generated.call_count, 1)
            self.assertNotIn("refresh", vars(server))

    def test_unregistered_profile_and_missing_registry_have_no_catalogs(self):
        other = Profile({}, self.root / "other.toml")
        self.assertEqual(editordata.profile_label_inputs(other), {})
        with registry_scope(self.root / "absent"):
            self.assertEqual(editordata.profile_label_inputs(self.prof), {})
