import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import chdir, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from project_fixture import CATALOG_STAGE, SAMPLE, sample_rom, write_policy
from gen3tl.cli import main as cli
from gen3tl.catalog.po import PoEntry, write
from gen3tl.profile.profile import Profile
from gen3tl.project import ProjectError, Registry, build_scope, find_registry

class ProjectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.profiles = {}
        for name in ("first", "second"):
            path = self.root / f"{name}.toml"
            path.write_text('[rom]\nsha1 = "' + "0" * 40 + '"\n')
            self.profiles[name] = path
        self.entries = [("first", "de"), ("first", "fr"), ("second", "de")]
        lines = ['[defaults]', 'profile = "first"', 'language = "de"']
        for name, language in self.entries:
            key = f"{name}-{language}"
            workspace = self.root / key
            workspace.mkdir()
            write(workspace / "translation/po/a.po", [PoEntry("one", "Source", key)],
                  {"Project-Id-Version": name, "Language": language})
            lines += ['[[project]]', f'profile = "{name}"', f'language = "{language}"', f'root = "{key}"', 'policy = "policy.toml"']
            write_policy(workspace, Profile.load(self.profiles[name]), language)
        (self.root / "projects.toml").write_text("\n".join(lines))

    def run_cli(self, name="first", language="de", *extra):
        with redirect_stdout(io.StringIO()):
            return cli.main(["--project-root", str(self.root), "--profile", str(self.profiles[name]),
                             "catalog", "tm", "--lang", language, *extra])

    def test_each_pair_writes_only_its_own_build(self):
        previous = Path.cwd()
        for name, language in self.entries:
            self.run_cli(name, language)
        registry = Registry.load(self.root)
        for project in registry.projects:
            self.assertTrue((project.build / "tm-report.md").exists())
            self.assertEqual(project.editor, self.root / "build/editor" / project.profile / project.language)
        self.assertEqual(Path.cwd(), previous)
        self.assertFalse((self.root / "build/tm-report.md").exists())

    def test_registry_carries_optional_site_metadata(self):
        self.assertIsNone(Registry.load(self.root).site)
        with (self.root / "projects.toml").open("a", encoding="utf-8") as stream:
            stream.write('\n[site]\nname = "Example site"\nrepository = "example/toolkit"\n')
        self.assertEqual(Registry.load(self.root).site, {"name": "Example site", "repository": "example/toolkit"})

    def test_a_build_scope_moves_every_project_build(self):
        work = self.root / "work/build"
        projects = Registry.load(self.root).projects
        with build_scope(work):
            self.assertEqual({project.build for project in projects}, {work})
        self.assertEqual([project.build for project in projects], [project.root / "build" for project in projects])

    def test_unregistered_selection_never_calls_handler(self):
        with patch.dict(cli.COMMANDS, {"catalog tm": unittest.mock.Mock()}):
            with redirect_stderr(io.StringIO()) as error, self.assertRaises(SystemExit):
                self.run_cli(language="es")
            self.assertIn("unconfigured translation project first/es", error.getvalue())
            self.assertIn("valid        de, fr\n", error.getvalue())
            self.assertTrue(error.getvalue().splitlines()[-1].startswith("result       failed: "))
            cli.COMMANDS["catalog tm"].assert_not_called()
        self.assertFalse((self.root / "first-de/build").exists())

    def test_wrong_catalog_or_inventory_fails_before_writes(self):
        registry = Registry.load(self.root)
        project = registry.resolve("first", "de")
        catalog = project.catalog / "a.po"
        good = catalog.read_text()
        catalog.write_text(good.replace("Language: de", "Language: fr"))
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.run_cli()
        catalog.write_text(good)
        project.build.mkdir()
        (project.build / "inventory.json").write_text(json.dumps({"profile": "second", "rom_sha1": "0" * 40}))
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.run_cli()
        self.assertFalse((project.build / "tm-report.md").exists())

    def test_explicit_catalog_in_another_project_is_refused(self):
        other = self.root / "first-fr/translation/po"
        with redirect_stderr(io.StringIO()) as error, self.assertRaises(SystemExit):
            self.run_cli("first", "de", "--po-root", str(other))
        self.assertIn("belongs to first/fr", error.getvalue())

    def test_caller_relative_input_and_scoped_assets(self):
        registry = Registry.load(self.root)
        project = registry.resolve("first", "de")
        previous = Path.cwd()
        with project.activate():
            self.assertEqual(Path.cwd(), project.root)
            self.assertEqual(Profile.load(self.profiles["first"]).project_root, project.root)
        self.assertEqual(Path.cwd(), previous)
        self.assertEqual(Profile.load(self.profiles["first"]).project_root, previous)
        with self.assertRaisesRegex(RuntimeError, "stop"):
            with project.activate():
                raise RuntimeError("stop")
        self.assertEqual(Path.cwd(), previous)
        try:
            os.chdir(self.root)
            self.run_cli("first", "de", "--po-root", "first-de/translation/po")
        finally:
            os.chdir(previous)

    def test_duplicate_workspaces_are_rejected(self):
        path = self.root / "projects.toml"
        path.write_text(path.read_text().replace('root = "first-fr"', 'root = "first-de"'))
        with self.assertRaisesRegex(ProjectError, "shared workspace"):
            Registry.load(self.root)

    def test_unsupported_project_stages_are_rejected(self):
        path = self.root / "projects.toml"
        path.write_text(path.read_text().replace('policy = "policy.toml"', 'policy = "policy.toml"\nstage = "groundwork"', 1))
        with self.assertRaisesRegex(ProjectError, "unsupported project stage 'groundwork'"):
            Registry.load(self.root)

    def test_global_and_trailing_language_selection_match(self):
        observed = []
        batch = Registry.load(self.root).resolve("first", "fr").batches / "sample"
        batch.mkdir(parents=True)
        (batch / "source.json").write_text("[]")
        def handler(args):
            observed.append((args.lang, args.project.key, Path.cwd()))
        with patch.dict(cli.COMMANDS, {"catalog batch-status": handler}):
            base = ["--project-root", str(self.root), "--profile", str(self.profiles["first"])]
            cli.main(base + ["--lang", "fr", "catalog", "batch-status", "sample"])
            cli.main(base + ["catalog", "batch-status", "sample", "--lang", "fr"])
        self.assertEqual(observed[0], observed[1])
        self.assertEqual(observed[0][1], "first/fr")

    def test_unknown_batches_list_only_the_selected_projects_batches(self):
        registry = Registry.load(self.root)
        for language, name in (("de", "known"), ("fr", "other")):
            batch = registry.resolve("first", language).batches / name
            batch.mkdir(parents=True)
            (batch / "source.json").write_text("[]")
        for group, command in (("catalog", "batch-status"), ("catalog", "batch-import"),
                               ("catalog", "batch-approve"), ("check", "batch"), ("check", "render")):
            output = io.StringIO()
            with self.subTest(command=command), redirect_stdout(output), redirect_stderr(output):
                status = cli.main(["--project-root", str(self.root), "--profile", str(self.profiles["first"]),
                                   "--lang", "de", group, command, "absent"])
            self.assertEqual(status, 2)
            self.assertIn("valid        known\n", output.getvalue())
            self.assertNotIn("other", output.getvalue())
            self.assertNotIn("None findings", output.getvalue())
            self.assertEqual(output.getvalue().splitlines()[-1], "result       failed: unknown batch: absent")

    def test_missing_batch_root_lists_no_valid_names(self):
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            status = cli.main(["--project-root", str(self.root), "--profile", str(self.profiles["first"]),
                               "catalog", "batch-status", "absent"])
        self.assertEqual(status, 2)
        self.assertIn("valid        none\n", output.getvalue())

    def test_unknown_export_plan_name_fails_before_creating_a_batch(self):
        project = Registry.load(self.root).resolve("first", "de")
        plan = project.root / "plan.json"
        plan.write_text(json.dumps([{"name": "planned", "ids": []}]))
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            status = cli.main(["--project-root", str(self.root), "--profile", str(self.profiles["first"]),
                               "catalog", "batch-export", "absent", "--plan", str(plan)])
        self.assertEqual(status, 2)
        self.assertIn("valid        planned\n", output.getvalue())
        self.assertEqual(output.getvalue().splitlines()[-1], "result       failed: unknown planned batch: absent")
        self.assertFalse(project.batches.exists())

    def test_missing_identities_are_rejected(self):
        project = Registry.load(self.root).resolve("first", "de")
        write(project.catalog / "a.po", [], {"Project-Id-Version": "first"})
        with self.assertRaisesRegex(ProjectError, "Language None"):
            project.validate(Profile.load(self.profiles["first"]))
        write(project.catalog / "a.po", [], {"Project-Id-Version": "first", "Language": "de"})
        project.build.mkdir()
        (project.build / "inventory.json").write_text(json.dumps({"profile": "first"}), encoding="utf-8")
        with self.assertRaisesRegex(ProjectError, "rom_sha1 None"):
            project.validate(Profile.load(self.profiles["first"]))

    def test_build_identity_rejects_wrong_language_and_modified_bytes(self):
        project = Registry.load(self.root).resolve("first", "de")
        project.build.mkdir()
        path = project.build / "first-de.gba"
        path.write_bytes(b"verified ROM")
        profile = Profile.load(self.profiles["first"])
        with self.assertRaisesRegex(ProjectError, "missing build identity"):
            project.validate_built(profile, path)
        doc = {"profile": "first", "language": "de", "rom_sha1": "0" * 40,
               "built_sha1": hashlib.sha1(path.read_bytes()).hexdigest()}
        manifest = path.with_suffix(".project.json")
        manifest.write_text(json.dumps(doc))
        project.validate_built(profile, path)
        path.write_bytes(b"modified ROM")
        with self.assertRaisesRegex(ProjectError, "build identity"):
            project.validate_built(profile, path)
        path.write_bytes(b"verified ROM")
        doc["language"] = "fr"
        manifest.write_text(json.dumps(doc))
        with self.assertRaisesRegex(ProjectError, "build identity"):
            project.validate_built(profile, path)

class RegistryLookupTest(unittest.TestCase):
    def test_an_explicit_empty_registry_is_a_neutral_fixture(self):
        from gen3tl.core.rom import Rom
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            registry, elsewhere, work = (root / name for name in ("registry", "elsewhere", "work"))
            registry.mkdir()
            elsewhere.mkdir()
            (registry / "projects.toml").write_text("", encoding="utf-8")
            self.assertEqual(Registry.load(registry), Registry(registry, "", "", ()))
            profile = Profile({"support": {"stage": "discovery"}}, registry / "fixture.toml")
            with chdir(elsewhere), build_scope(work), redirect_stdout(io.StringIO()), \
                    patch.object(Profile, "by_name", return_value=profile), \
                    patch.object(Profile, "load_rom", return_value=Rom(b"\xff" * 64)):
                self.assertEqual(cli.main(["--project-root", str(registry), "discover", "candidates"]), 0)
            self.assertTrue((work / "discovery/fixture/candidates.json").is_file())
            self.assertFalse((registry / "build").exists())
            self.assertEqual(list(elsewhere.iterdir()), [])

    def test_the_working_directory_selects_the_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            nested = root / "a" / "b"
            nested.mkdir(parents=True)
            previous = Path.cwd()
            try:
                os.chdir(nested)
                self.assertEqual(find_registry(), nested)
                empty = Registry.load()   # commands without a project still run
                self.assertEqual((empty.root, empty.projects, empty.default_profile), (nested, (), ""))
                with self.assertRaisesRegex(ProjectError, "cannot read project registry"):
                    Registry.load(nested)   # a named root needs its registry
                (root / "projects.toml").write_text("")
                self.assertEqual(find_registry(), root)
            finally:
                os.chdir(previous)

class CatalogStageFixtureTest(unittest.TestCase):
    def test_every_fixture_workspace_validates_against_its_profile(self):
        registry = Registry.load(CATALOG_STAGE)
        self.assertEqual([project.key for project in registry.projects], [f"{SAMPLE}/ru"])
        for project in registry.projects:
            with self.subTest(project=project.key):
                profile = Profile.by_name(project.profile)
                self.assertEqual(profile["support"]["stage"], "catalog")
                self.assertFalse(project.game)
                project.validate(profile)
                self.assertTrue((project.translation / "catalog.json").is_file())

    def test_the_fixture_is_what_the_commands_write_from_its_rom(self):
        from gen3tl.catalog.draftcatalog import prepare
        from gen3tl.core.rom import Rom
        from gen3tl.discover.discovery import inspect, source_catalog
        profile = Profile.by_name(SAMPLE)
        project = Registry.load(CATALOG_STAGE).resolve(profile, "ru")
        rom = Rom(sample_rom())
        with tempfile.TemporaryDirectory() as tmp:
            sources, workspace = Path(tmp) / "sources", Path(tmp) / "translation"
            sources.mkdir()
            source_catalog(inspect(profile, rom), sources)
            prepare(profile, rom, "ru", workspace)
            expected = CATALOG_STAGE / "translation/sources" / SAMPLE
            for name in ("source.pot", "catalog.json"):
                self.assertEqual((sources / name).read_bytes(), (expected / name).read_bytes(), name)
            written = sorted(path.relative_to(workspace).as_posix() for path in workspace.rglob("*") if path.is_file())
            self.assertEqual(written, ["catalog.json", "po/source/code.po", "po/source/data-table.po",
                                       "po/source/script.po"])
            for name in written:
                self.assertEqual((workspace / name).read_bytes(), (project.translation / name).read_bytes(), name)
