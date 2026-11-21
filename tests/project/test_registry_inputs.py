"""Registry inputs outside the profile library."""
import io
import json
import shutil
import tempfile
import unittest
from argparse import Namespace
from contextlib import chdir, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from gen3tl.core.rom import RomError
from gen3tl.cli.main import main, project_defaults
from gen3tl.data import pokedextexts
from gen3tl.profile.profile import Profile, profile_paths
from gen3tl.project import ProjectError, Registry, registry_scope
from gen3tl.web import editordata, sitecatalog
from project_fixture import write_policy


class RegistryInputsTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.registry = self.root / "registry"
        self.registry.mkdir()
        self.library = self.root / "library"
        self.library.mkdir()
        self.enterContext(patch("gen3tl.profile.profile.PROFILE_DIR", self.library))
        self.profile = self.root / "external.toml"
        self.profile.write_text('[rom]\nname = "External game"\nsha1 = "' + '1' * 40 + '"\n'
                                'source_language = "en"\n[engine]\nfamily = "test"\n'
                                'label = "External engine"\n[support]\nstage = "catalog"\n'
                                '[landing]\nintro = "External title"\n')
        self.dataset = self.root / "dataset"
        self.dataset.mkdir()
        (self.dataset / "pokedex.json").write_text('{"species":[]}')
        (self.dataset / "learnsets.json").write_text('{"learnsets":[]}')
        self.entries = []
        for code in ("de", "fr"):
            workspace = self.registry / code
            workspace.mkdir()
            write_policy(workspace, Profile.load(self.profile), code)
            self.entries.append(dict(profile="external", language=code, root=code, policy="policy.toml",
                                     profile_path="../external.toml", dataset_path="../dataset"))
        self.write_registry()

    def write_registry(self):
        text = '[defaults]\nprofile = "external"\nlanguage = "de"\n'
        for row in self.entries:
            text += '\n[[project]]\n' + ''.join(f'{key} = {json.dumps(value)}\n' for key, value in row.items())
        (self.registry / "projects.toml").write_text(text)

    def test_inputs_are_relative_to_registry_from_another_directory(self):
        with chdir(self.library), registry_scope(self.registry):
            registry = Registry.load(self.registry)
            for project in registry.projects:
                self.assertEqual(project.profile_path, self.profile)
                self.assertEqual(project.load_profile().name, "external")
                self.assertEqual(project.dataset_path, self.dataset)
            self.assertEqual(pokedextexts.tracked_dataset("external"), self.dataset / "pokedex.json")
            self.assertEqual(profile_paths(), [])
            with self.assertRaisesRegex(RomError, "no profile"):
                Profile.by_name("external")

    def test_missing_declared_inputs_never_fall_back(self):
        shutil.copyfile(self.profile, self.library / self.profile.name)
        for field, value in (("profile_path", "missing.toml"), ("dataset_path", "missing")):
            with self.subTest(field=field):
                original = self.entries[0][field]
                self.entries[0][field] = value
                self.write_registry()
                with self.assertRaisesRegex(ProjectError, "missing " + field):
                    Registry.load(self.registry)
                self.entries[0][field] = original

    def test_declared_dataset_requires_its_document(self):
        (self.dataset / "pokedex.json").unlink()
        with self.assertRaisesRegex(ProjectError, "missing dataset_path"):
            Registry.load(self.registry)

    def test_input_types_and_absolute_paths_are_rejected(self):
        for field in ("profile_path", "dataset_path"):
            for value in ("", " ", 1, [], str(self.profile)):
                with self.subTest(field=field, value=value):
                    original = self.entries[0][field]
                    self.entries[0][field] = value
                    self.write_registry()
                    with self.assertRaisesRegex(ProjectError, "nonempty relative path"):
                        Registry.load(self.registry)
                    self.entries[0][field] = original

    def test_profile_identity_must_match(self):
        other = self.root / "other.toml"
        shutil.copyfile(self.profile, other)
        self.entries[0]["profile_path"] = "../other.toml"
        self.write_registry()
        with self.assertRaisesRegex(ProjectError, "profile is other"):
            Registry.load(self.registry)

    def test_languages_must_agree_including_absent_fields(self):
        for field in ("profile_path", "dataset_path"):
            original = self.entries[1].pop(field)
            self.write_registry()
            with self.subTest(field=field), self.assertRaisesRegex(ProjectError, "languages disagree"):
                Registry.load(self.registry)
            self.entries[1][field] = original
        self.entries[1]["profile_path"] = ".././external.toml"
        self.write_registry()
        self.assertEqual(len(Registry.load(self.registry).projects), 2)

    def test_explicit_profile_directory(self):
        directory = self.root / "external"
        directory.mkdir()
        (directory / "profile.toml").write_text('[discovery]\nschema = 1\n')
        (directory / "overlay.toml").write_bytes(self.profile.read_bytes())
        for row in self.entries:
            row["profile_path"] = "../external"
        self.write_registry()
        self.assertEqual(Registry.load(self.registry).projects[0].load_profile().name, "external")

    def test_absent_fields_preserve_library_and_dataset_resolution(self):
        shutil.copyfile(self.profile, self.library / self.profile.name)
        for row in self.entries:
            row.pop("profile_path")
            row.pop("dataset_path")
        self.write_registry()
        with registry_scope(self.registry):
            registry = Registry.load(self.registry)
            self.assertEqual(registry.projects[0].load_profile().path, self.library / self.profile.name)
            self.assertEqual(pokedextexts.dataset_dir("external"), pokedextexts.default_dataset_dir("external"))

    def test_manifest_uses_declared_metadata_and_copies_dataset(self):
        out = self.root / "editor"
        with registry_scope(self.registry):
            doc = editordata.write_languages(out, toolkit="0.0.0")
            self.assertIn("External title", sitecatalog.registry_keys())
        profile = next(row for row in doc["profiles"] if row["id"] == "external")
        self.assertEqual(profile["name"], "External game")
        self.assertEqual(profile["engine_label"], "External engine")
        self.assertEqual(profile["sha1"], '1' * 40)
        self.assertEqual(profile["source_language"], "en")
        for filename in ("pokedex.json", "learnsets.json"):
            self.assertEqual((out / "pokedex/external" / filename).read_bytes(),
                             (self.dataset / filename).read_bytes())

    def test_declared_dataset_never_selects_generation_output(self):
        with registry_scope(self.registry):
            project = Registry.load(self.registry).projects[0]
            args = Namespace(cmd="data pokedex", out=None)
            project_defaults(args, project)
            self.assertEqual(Path(args.out), project.build / "pokedex" / project.profile)
            self.assertNotEqual(pokedextexts.default_dataset_dir("external"), self.dataset)
        before = {p.name: p.read_bytes() for p in self.dataset.iterdir()}
        with redirect_stdout(io.StringIO()), patch("gen3tl.cli.main.COMMANDS", {"catalog tm": lambda args: 0}):
            self.assertEqual(main(["--project-root", str(self.registry), "--profile", str(self.profile),
                                   "--lang", "de", "catalog", "tm"]), 0)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.dataset.iterdir()})
