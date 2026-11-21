from contextlib import chdir
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from gen3tl import __version__
from gen3tl.core.rom import RomError
from gen3tl.profile import profile
from gen3tl.project import Manifest, Project, registry_scope
from gen3tl.project.scope import ACTIVE_PROJECT
from profile_schema_support import HEADER, OVERLAY, write_dir

class ProjectProfileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.root, self.toolkit = self.tmp / "game", self.tmp / "profiles"
        self.root.mkdir()
        self.toolkit.mkdir()
        self.identifier = "game-1.2.3"
        self.manifest = self.root / "project.toml"
        self.manifest.write_text(Manifest(self.manifest, self.identifier, "0" * 40,
                                          __version__, (), "").text(), encoding="utf-8")
        self.enterContext(chdir(self.root))
        self.enterContext(registry_scope(self.root))
        self.enterContext(patch.object(profile, "PROFILE_DIR", self.toolkit))

    def test_project_profile_is_enumerated_named_and_loaded(self):
        path = write_dir(self.root, "profile")
        self.assertEqual(profile.profile_paths(), [path])
        self.assertEqual(profile.name_of(path), self.identifier)
        self.assertEqual(profile.locate(self.identifier), path)
        loaded = profile.Profile.by_name(self.identifier)
        self.assertEqual((loaded.path, loaded.name), (path, self.identifier))
        self.assertEqual(loaded.data, profile.Profile.load(path).data)

    def test_project_profile_can_supply_the_unique_default(self):
        path = write_dir(self.root, "profile", overlay=OVERLAY + "\n[project]\ndefault = true\n")
        self.assertEqual(profile.Profile.by_name(None).path, path)

    def test_active_project_supplies_its_profile_outside_the_registry(self):
        path = write_dir(self.root, "profile")
        project = Project(self.identifier, "", self.root, self.tmp, game=True)
        token = ACTIVE_PROJECT.set(project)
        self.addCleanup(ACTIVE_PROJECT.reset, token)
        with chdir(self.tmp), registry_scope(self.tmp):
            self.assertEqual(profile.profile_paths(), [path])
            self.assertEqual(profile.locate(self.identifier), path)

    def test_project_lookup_does_not_require_toolkit_profiles(self):
        path = write_dir(self.root, "profile")
        self.toolkit.rmdir()
        self.assertEqual(profile.profile_paths(), [path])
        self.assertEqual(profile.locate(self.identifier), path)

    def test_enumeration_sorts_by_manifest_identity(self):
        path = write_dir(self.root, "profile")
        earlier = write_dir(self.toolkit, "first")
        later = write_dir(self.toolkit, "last")
        self.assertEqual(profile.profile_paths(), [earlier, path, later])
        self.assertEqual(profile.profile_paths(self.toolkit), [earlier, path, later])

    def test_duplicate_flat_or_directory_name_reports_both_paths(self):
        path = write_dir(self.root, "profile")
        for directory in (False, True):
            with self.subTest(directory=directory):
                if directory:
                    duplicate = write_dir(self.toolkit, self.identifier)
                else:
                    duplicate = self.toolkit / (self.identifier + ".toml")
                    duplicate.write_text(OVERLAY, encoding="utf-8")
                with self.assertRaises(RomError) as raised:
                    profile.Profile.by_name(self.identifier)
                self.assertIn(str(path), str(raised.exception))
                self.assertIn(str(duplicate), str(raised.exception))
                self.assertIn("keep one", str(raised.exception))
                self.assertEqual(profile.locate(str(path)), path)
                if directory:
                    shutil.rmtree(duplicate)
                else:
                    duplicate.unlink()

    def test_project_without_profile_retains_toolkit_lookup(self):
        directory = write_dir(self.toolkit, self.identifier)
        flat = self.toolkit / "flat.toml"
        flat.write_text(OVERLAY, encoding="utf-8")
        self.assertEqual(profile.profile_paths(), [flat, directory])
        self.assertEqual(profile.Profile.by_name(self.identifier).path, directory)
        self.assertEqual(profile.Profile.by_name("flat").path, flat)
        with self.assertRaisesRegex(RomError, "no profile"):
            profile.locate("missing")

    def test_no_manifest_retains_toolkit_lookup(self):
        self.manifest.unlink()
        path = write_dir(self.toolkit, self.identifier)
        self.assertEqual(profile.profile_paths(), [path])
        self.assertEqual(profile.locate(self.identifier), path)

    def test_toolkit_aliases_remain_separate_profiles(self):
        source = self.toolkit / "first.toml"
        source.write_text(OVERLAY, encoding="utf-8")
        alias = self.toolkit / "second.toml"
        alias.symlink_to(source)
        self.assertEqual(profile.profile_paths(), [source, alias])
        self.assertEqual(profile.Profile.by_name("second").name, "second")

    def test_toolkit_alias_of_project_profile_is_a_duplicate_name(self):
        path = write_dir(self.root, "profile")
        alias = self.toolkit / self.identifier
        alias.symlink_to(path, target_is_directory=True)
        with self.assertRaises(RomError) as raised:
            profile.locate(self.identifier)
        self.assertIn(str(path), str(raised.exception))
        self.assertIn(str(alias), str(raised.exception))

    def test_project_profile_requires_a_manifest_id(self):
        path = write_dir(self.root, "profile")
        original = self.manifest.read_text(encoding="utf-8")
        for identity in (None, "", "invalid/id"):
            with self.subTest(identity=identity):
                line = f'profile = "{self.identifier}"\n'
                replacement = "" if identity is None else f'profile = "{identity}"\n'
                self.manifest.write_text(original.replace(line, replacement), encoding="utf-8")
                for operation in (lambda: profile.name_of(path), profile.profile_paths,
                                  lambda: profile.locate(self.identifier),
                                  lambda: profile.locate(str(path))):
                    with self.assertRaisesRegex(RomError, "manifest profile id"):
                        operation()

    def test_explicit_project_profile_requires_a_manifest(self):
        path = write_dir(self.root, "profile")
        self.manifest.unlink()
        for operation in (lambda: profile.name_of(path), lambda: profile.locate(str(path))):
            with self.assertRaisesRegex(RomError, "manifest profile id") as raised:
                operation()
            self.assertIn(str(self.manifest), str(raised.exception))

    def test_directory_overlay_and_source_names_survive_a_move(self):
        original = write_dir(self.toolkit, self.identifier, generated=HEADER, overlay=None)
        overlay = original / "overlay"
        overlay.mkdir()
        (overlay / "order.txt").write_text("facts.toml\n", encoding="utf-8")
        (overlay / "facts.toml").write_text(OVERLAY, encoding="utf-8")
        before = profile.Profile.by_name(self.identifier)
        sources = {path.relative_to(original): path.read_bytes() for path in profile.source_files(original)}
        moved = original.rename(self.root / "profile")
        after = profile.Profile.by_name(self.identifier)
        self.assertEqual(after.name, before.name)
        self.assertEqual(repr(after.data), repr(before.data))
        self.assertEqual({path.relative_to(moved): path.read_bytes() for path in profile.source_files(moved)}, sources)
        for selected in (moved, moved / "profile.toml", moved / "overlay", moved / "overlay/facts.toml"):
            self.assertEqual(profile.locate(str(selected)), moved)
