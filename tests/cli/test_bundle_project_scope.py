"""Explicit project save layouts accompany the browser check bundle."""

from argparse import Namespace
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from gen3tl import __version__
from gen3tl.cli.web import editor_bundle
from gen3tl.profile import profile
from gen3tl.project import Manifest, registry_scope
from gen3tl.web.editorcheck import write_bundle
from profile_schema_support import write_dir


class BundleProjectScopeTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.game = self.root / "game"
        self.game.mkdir()
        manifest = self.game / "project.toml"
        manifest.write_text(Manifest(manifest, "example-game", "0" * 40,
                                     __version__, (), "").text(), encoding="utf-8")
        self.path = write_dir(self.game, "profile", overlay='[save]\nsector_size = 4096\n')
        self.enterContext(patch.object(profile, "PROFILE_DIR", self.root / "library"))
        self.enterContext(registry_scope(self.root))

    def layouts(self, selected=None):
        archive = self.root / "checker.zip"
        write_bundle(archive, {}, profile=selected)
        with zipfile.ZipFile(archive) as bundle:
            return json.loads(bundle.read("save_layouts.json"))

    def test_explicit_project_profile_supplies_its_layout_outside_scope(self):
        selected = profile.Profile.load(self.path)
        self.assertEqual(self.layouts(selected), {"example-game": {"sector_size": 4096}})

    def test_unselected_project_is_not_inferred_from_output(self):
        self.assertEqual(self.layouts(), {})

    def test_scoped_project_profile_is_enumerated_without_library(self):
        with registry_scope(self.game):
            self.assertEqual(self.layouts(), {"example-game": {"sector_size": 4096}})

    def test_profile_without_save_layout_adds_no_record(self):
        selected = profile.Profile({"rom": {"sha1": "0" * 40}}, self.path)
        self.assertEqual(self.layouts(selected), {})

    def test_command_preserves_the_base_layout_without_language_evidence(self):
        selected = profile.Profile.load(self.path)
        selected.data["save"]["evidence"] = "Language-specific observation."
        archive = self.root / "checker.zip"
        args = Namespace(profile=str(self.path), out=str(archive))
        with patch.object(profile.Profile, "by_name", return_value=selected), redirect_stdout(io.StringIO()):
            editor_bundle(args)
        with zipfile.ZipFile(archive) as bundle:
            self.assertEqual(json.loads(bundle.read("save_layouts.json")),
                             {"example-game": {"sector_size": 4096}})
