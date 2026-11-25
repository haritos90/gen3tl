"""A game's interface keys come from the explicitly selected project scope."""

from contextlib import chdir
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gen3tl import __version__
from gen3tl.profile import profile
from gen3tl.project import Manifest, registry_scope
from gen3tl.web.sitecatalog import game_catalogs
from profile_schema_support import write_dir


def declared(root=None):
    """The game keys of each language project of a scope."""
    return {project.key: set(keys) for project, keys in game_catalogs(root)}


class SiteCatalogScopeTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.outer, self.game, self.empty = [self.root / name for name in ("outer", "game", "empty")]
        for root in (self.outer, self.game, self.empty):
            root.mkdir()
            (root / "projects.toml").write_text("", encoding="utf-8")
        for root, identity, key in ((self.outer, "outer-game", "Outer key"),
                                    (self.game, "selected-game", "Selected key")):
            manifest = root / "project.toml"
            manifest.write_text(Manifest(manifest, identity, "0" * 40, __version__, ("xx",), "xx").text(),
                                encoding="utf-8")
            (root / "projects.toml").write_text(
                f'[defaults]\nprofile = "{identity}"\nlanguage = "xx"\n'
                f'[[project]]\nprofile = "{identity}"\nlanguage = "xx"\n'
                'root = "."\npolicy = "policy.toml"\n'
                '[site]\nlicenses = [{label_key = "License key", url = "license.txt"}]\n', encoding="utf-8")
            write_dir(root, "profile", overlay=f'[landing]\neditor = "{key}"\n')
        self.enterContext(chdir(self.outer))
        self.enterContext(patch.object(profile, "PROFILE_DIR", self.root / "library"))

    def test_explicit_scope_collects_only_its_registered_keys(self):
        self.assertEqual(declared(self.game), {"selected-game/xx": {"Selected key"}})
        self.assertEqual(declared(), {"outer-game/xx": {"Outer key"}})

    def test_empty_explicit_scope_does_not_inherit_current_game(self):
        self.assertEqual(declared(self.empty), {})

    def test_context_scope_matches_explicit_root(self):
        with registry_scope(self.game):
            self.assertEqual(declared(), declared(self.game))
            self.assertEqual(declared(), {"selected-game/xx": {"Selected key"}})
