"""Release exports retain registry ownership outside their output tree."""

from contextlib import chdir
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gen3tl import __version__
from gen3tl.project import Manifest, Registry, game_roots, registry_scope
from gen3tl.web.editordata import write_languages, write_releases


class ReleaseScopeTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.outer, self.selected = self.root / "outer", self.root / "selected"
        for root in (self.outer, self.selected):
            root.mkdir()
            (root / "projects.toml").write_text("", encoding="utf-8")
        manifest = self.outer / "project.toml"
        manifest.write_text(Manifest(manifest, "outer-game", "0" * 40,
                                     __version__, (), "").text(), encoding="utf-8")
        self.enterContext(chdir(self.outer))

    def test_empty_selected_registry_excludes_current_game(self):
        with registry_scope(self.selected), patch("gen3tl.web.patcher.copy_releases") as copy:
            self.assertEqual(game_roots(), [])
            self.assertEqual(write_releases(self.root / "output"), 0)
            copy.assert_not_called()
        with registry_scope(self.outer):
            self.assertEqual(game_roots(), [self.outer])

    def test_manifest_output_keeps_its_scoped_registry(self):
        output = self.root / "output"
        output.mkdir()
        with registry_scope(self.selected), patch("gen3tl.profile.profile.profile_paths", return_value=[]), \
                patch("gen3tl.web.sitecatalog.write", return_value=[]), \
                patch("gen3tl.web.patcher.copy_releases") as copy:
            document = write_languages(output)
            self.assertEqual(document["profiles"], [])
            copy.assert_not_called()
        self.assertTrue((output / "languages.json").is_file())

    def test_registered_games_supply_releases_from_their_own_roots(self):
        game = self.selected / "game"
        game.mkdir()
        manifest = game / "project.toml"
        manifest.write_text(Manifest(manifest, "selected-game", "1" * 40,
                                     __version__, ("xx",), "xx").text(), encoding="utf-8")
        (self.selected / "projects.toml").write_text(
            '[defaults]\nprofile = "selected-game"\nlanguage = "xx"\n'
            '[[project]]\nprofile = "selected-game"\nlanguage = "xx"\n'
            'root = "game"\npolicy = "policy.toml"\n', encoding="utf-8")
        with patch("gen3tl.web.patcher.copy_releases", return_value=2) as copy:
            self.assertEqual(write_releases(self.root / "output", Registry.load(self.selected)), 2)
            copy.assert_called_once_with(game / "releases", self.root / "output/releases", "selected-game")

    def test_a_game_without_releases_publishes_an_empty_list(self):
        game = self.selected / "game"
        game.mkdir()
        manifest = game / "project.toml"
        manifest.write_text(Manifest(manifest, "selected-game", "1" * 40,
                                     __version__, ("xx",), "xx").text(), encoding="utf-8")
        (self.selected / "projects.toml").write_text(
            '[defaults]\nprofile = "selected-game"\nlanguage = "xx"\n'
            '[[project]]\nprofile = "selected-game"\nlanguage = "xx"\n'
            'root = "game"\npolicy = "policy.toml"\n', encoding="utf-8")
        profile = self.root / "selected-game.toml"
        profile.write_text('[rom]\nname = "Selected game"\nsha1 = "' + "1" * 40 + '"\n', encoding="utf-8")
        output, preview = self.root / "output", self.root / "preview"
        output.mkdir()
        with registry_scope(self.selected), patch("gen3tl.profile.profile.profile_paths", return_value=[profile]), \
                patch("gen3tl.web.sitecatalog.write", return_value=[]):
            document = write_languages(output)
            # a release preview without a manifest gains nothing
            self.assertEqual(write_releases(preview, Registry.load(self.selected)), 0)
        self.assertEqual([row["id"] for row in document["profiles"]], ["selected-game"])
        listed = output / "releases/releases.json"
        self.assertTrue(listed.is_file(), "no release list beside the manifest")
        self.assertEqual(json.loads(listed.read_text(encoding="utf-8")), {"schema": 1, "releases": []})
        self.assertFalse(preview.exists())
