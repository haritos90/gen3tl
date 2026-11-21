"""Implicit repository boundaries and explicit registry selection."""

from contextlib import chdir, redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gen3tl.cli.main import main
from gen3tl.project import Registry, find_registry, registry_root, registry_scope


class RegistryBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        (self.root / "projects.toml").write_text('[site]\nname = "Outer site"\n', encoding="utf-8")
        (self.root / ".git").mkdir()
        self.clone = self.root / "nested"
        (self.clone / "subdirectory").mkdir(parents=True)

    def test_clone_and_linked_worktree_do_not_inherit_outer_registry(self):
        marker = self.clone / ".git"
        for linked in (False, True):
            with self.subTest(linked=linked):
                if linked:
                    marker.write_text("gitdir: ../metadata\n", encoding="utf-8")
                else:
                    marker.mkdir()
                with chdir(self.clone / "subdirectory"):
                    self.assertEqual(find_registry(), Path.cwd())
                    self.assertEqual(Registry.load().projects, ())
                    self.assertIsNone(Registry.load().site)
                marker.unlink() if linked else marker.rmdir()

    def test_registry_at_repository_root_remains_visible(self):
        for directory in (self.root, self.clone, self.clone / "subdirectory"):
            with self.subTest(directory=directory), chdir(directory):
                self.assertEqual(find_registry(), self.root)
        (self.clone / ".git").mkdir()
        (self.clone / "projects.toml").write_text('[site]\nname = "Inner site"\n', encoding="utf-8")
        with chdir(self.clone / "subdirectory"):
            self.assertEqual(find_registry(), self.clone)
            self.assertEqual(Registry.load().site, {"name": "Inner site"})

    def test_explicit_root_and_scope_cross_the_boundary(self):
        (self.clone / ".git").mkdir()
        with chdir(self.clone), registry_scope(self.root):
            self.assertEqual(registry_root(), self.root)
            self.assertEqual(Registry.load(self.root).site, {"name": "Outer site"})
            with redirect_stdout(io.StringIO()), patch.dict("gen3tl.cli.main.COMMANDS", {
                    "web registry": lambda args: self.assertEqual(args.registry.root, self.root) or 0}):
                self.assertEqual(main(["--project-root", str(self.root), "web", "registry"]), 0)

    def test_nonrepository_directory_still_inherits_nearest_registry(self):
        with chdir(self.clone / "subdirectory"):
            self.assertEqual(find_registry(), self.root)
            self.assertEqual(Registry.load().site, {"name": "Outer site"})
