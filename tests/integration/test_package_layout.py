import argparse
import ast
import io
import json
import re
import sys
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from test_roots import TOOLKIT_ROOT
import gen3tl
from gen3tl.cli import common
from gen3tl.cli.main import help_parsers, main, parser
from gen3tl.profile.profile import Profile
from gen3tl.project import Project, Registry, build_scope
from project_fixture import CATALOG_STAGE

PACKAGE = TOOLKIT_ROOT / "src/gen3tl"

PACKAGES = ("core", "discover", "families", "profile", "catalog", "check", "build", "verify", "data", "lang", "web",
            "cli", "project")

GROUPS = ("rom", "discover", "catalog", "check", "build", "verify", "data", "lang", "web", "project")

FORBIDDEN = ("gen3tl.web", "gen3tl.build", "gen3tl.lang")

RULED = ("core", "discover", "profile")

def imported_modules(module: str, source: str) -> set[str]:
    """Absolute names of every module a source imports, lazy imports included."""
    parts = module.split(".")
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = ".".join(parts[:len(parts) - node.level])
                target = f"{base}.{node.module}" if node.module else base
            else:
                target = node.module
            found.add(target)
            found.update(f"{target}.{alias.name}" for alias in node.names)
    return found

def forbidden_imports(module: str, source: str) -> list[str]:
    return sorted(name for name in imported_modules(module, source)
                  if any(name == rule or name.startswith(rule + ".") for rule in FORBIDDEN))

def module_name(path: Path) -> str:
    return ".".join(("gen3tl",) + path.relative_to(PACKAGE).with_suffix("").parts)

class ImportRuleTest(unittest.TestCase):
    def test_core_discover_and_profile_import_no_web_build_or_lang(self):
        checked = 0
        for package in RULED:
            for path in sorted((PACKAGE / package).rglob("*.py")):
                with self.subTest(path=path.relative_to(PACKAGE).as_posix()):
                    self.assertEqual(forbidden_imports(module_name(path), path.read_text(encoding="utf-8")), [])
                checked += 1
        self.assertGreater(checked, len(RULED))

    def test_guard_resolves_relative_absolute_and_lazy_imports(self):
        module = "gen3tl.core.sample"
        for source in ("from ..web import editordata", "from ..build.font import FontTable",
                       "import gen3tl.lang.termbase", "from .. import lang", "from gen3tl.build import inject",
                       "def f():\n    from ..lang.language import current"):
            with self.subTest(source=source):
                self.assertTrue(forbidden_imports(module, source))
        for source in ("from .rom import Rom", "from ..catalog import po", "from .. import __version__",
                       "from ..profile.profile import Profile", "import json", "from .builder import x",
                       "from ..language_tools import x"):
            with self.subTest(source=source):
                self.assertEqual(forbidden_imports(module, source), [])

class LayoutTest(unittest.TestCase):
    def test_every_module_lives_in_a_package(self):
        self.assertEqual(sorted(p.name for p in PACKAGE.glob("*.py")), ["__init__.py", "__main__.py"])
        for package in PACKAGES:
            with self.subTest(package=package):
                self.assertTrue((PACKAGE / package / "__init__.py").is_file())
        self.assertEqual(sorted(p.name for p in PACKAGE.iterdir() if p.is_dir() and p.name != "__pycache__"),
                         sorted(PACKAGES))

    def test_one_cli_module_per_group(self):
        _, groups = parser()
        self.assertEqual(list(groups), list(GROUPS))
        for group in GROUPS:
            with self.subTest(group=group):
                self.assertTrue((PACKAGE / "cli" / f"{group}.py").is_file())

class PackagingTest(unittest.TestCase):
    """pyproject.toml installs every package and the toolkit root beside them."""

    @classmethod
    def setUpClass(cls):
        cls.doc = tomllib.loads((PACKAGE.parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
        cls.config = cls.doc["tool"]["setuptools"]

    def test_every_package_and_the_toolkit_root(self):
        shared = {"gen3tl.share." + path.replace("/", "."): path for path in ("families", "profiles", "assets/lang", "web")}
        self.assertEqual(sorted(self.config["packages"]),
                         sorted(["gen3tl", *(f"gen3tl.{name}" for name in PACKAGES), *shared]))
        self.assertEqual(self.config["package-dir"], {"": "src", **shared})
        self.assertEqual(self.config["exclude-package-data"]["gen3tl.share.web"], ["test/*"])

    def test_every_file_beside_the_modules(self):
        for path in sorted(PACKAGE.rglob("*")):
            if not path.is_file() or path.suffix in (".py", ".pyc") or path.name.startswith("."):
                continue
            package = ".".join(("gen3tl", *path.parent.relative_to(PACKAGE).parts))
            with self.subTest(path=path.relative_to(PACKAGE).as_posix()):
                self.assertIn(path.name, self.config["package-data"].get(package, []))

    def test_command_and_version(self):
        self.assertEqual(self.doc["project"]["scripts"], {"gen3tl": "gen3tl.__main__:run"})
        self.assertEqual(self.config["dynamic"]["version"], {"attr": "gen3tl.__version__"})
        self.assertEqual((gen3tl.INSTALLED, gen3tl.TOOLKIT_ROOT), (False, PACKAGE.parents[1]))

class CommandGroupTest(unittest.TestCase):
    def test_check_write_modes_preserve_defaults_and_legacy_aliases(self):
        root, _ = parser()
        cases = ((["catalog", "key-only"], "dry_run", False, False, "--dry-run"),
                 (["catalog", "rekey"], "dry_run", False, False, "--dry-run"),
                 (["catalog", "merge", "old", "new"], "dry_run", False, False, "--dry-run"),
                 (["catalog", "remap", "old", "new"], "dry_run", False, False, "--dry-run"),
                 (["verify", "save-convert", "input.sav"], "dry_run", False, False, "--dry-run"),
                 (["verify", "golden"], "record", False, True, "--record"),
                 (["lang", "rename", "term", "replacement"], "apply", False, True, "--apply"),
                 (["web", "snapshot"], "check", False, False, "--check"),
                 (["web", "import", "export.json"], "dry_run", False, False, "--dry-run"),
                 (["web", "submission", "--event", "event.json", "--out", "answer"],
                  "check", False, False, "--check"))
        for argv, dest, default, write_value, legacy in cases:
            with self.subTest(command=argv):
                self.assertEqual(getattr(root.parse_args(argv), dest), default)
                self.assertEqual(getattr(root.parse_args([*argv, "--write"]), dest), write_value)
                self.assertEqual(getattr(root.parse_args([*argv, "--dry-run"]), dest), not write_value)
                canonical = "--write" if legacy in ("--apply", "--record") else "--dry-run"
                self.assertEqual(vars(root.parse_args([*argv, legacy])),
                                 vars(root.parse_args([*argv, canonical])))
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as end:
                    root.parse_args([*argv, "--dry-run", "--write"])
                self.assertEqual(end.exception.code, 2)

    def test_dataset_modes_preserve_directory_verification_and_writing_defaults(self):
        root, _ = parser()
        for name in ("pokedex", "sprites", "icons", "maps"):
            argv = ["data", name]
            with self.subTest(command=argv):
                self.assertIsNone(root.parse_args(argv).verify)
                self.assertIsNone(root.parse_args([*argv, "--write"]).verify)
                self.assertEqual(root.parse_args([*argv, "--verify", "dataset"]).verify, "dataset")
                self.assertEqual(root.parse_args([*argv, "--out", "dataset", "--dry-run"]).verify, "")
                for mode in ("--write", "--dry-run"):
                    with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as end:
                        root.parse_args([*argv, "--verify", "dataset", mode])
                    self.assertEqual(end.exception.code, 2)

    def test_language_selector_is_global_and_keeps_command_local_invocations(self):
        root, _ = parser()
        for current in help_parsers(root):
            if current is not root:
                self.assertNotIn("--lang", current._option_string_actions)
        commands = (["rom", "decode", "100"], ["discover", "inventory"], ["build", "rom"],
                    ["build", "patch"], ["build", "font"], ["build", "font-preview"],
                    ["build", "font-compare"], ["check", "qa"], ["web", "data"],
                    ["verify", "save-convert", "input.sav"])
        language = Registry.load(CATALOG_STAGE).default_language
        selection = ["--project-root", str(CATALOG_STAGE)]
        for command in commands:
            for argv in (["--lang", language, *command], [*command, "--lang", language],
                         [*command, f"--lang={language}"]):
                with self.subTest(argv=argv), patch("gen3tl.cli.main._dispatch", return_value=0) as dispatch:
                    self.assertEqual(main([*selection, *argv]), 0)
                self.assertEqual(dispatch.call_args.args[1].lang, language)

    def test_registry_and_parsers_use_group_command_keys(self):
        root, groups = parser()
        expected = {f"{group} {name}" for group, names in groups.items() for name in names}
        self.assertEqual(set(common.COMMANDS), expected)
        commands = [current for current in help_parsers(root) if current.get_default("cmd")]
        self.assertEqual(len(commands), len(expected))
        for current in commands:
            with self.subTest(command=current.prog):
                key = current.get_default("cmd")
                self.assertEqual(current.prog, f"gen3tl {key}")
                self.assertEqual(common.COMMANDS[key].command_path, key)

    def test_help_lists_the_groups(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as end:
            main(["--help"])
        self.assertEqual(end.exception.code, 0)
        for group in GROUPS:
            self.assertRegex(out.getvalue(), rf"(?m)^command\s+gen3tl {group}\s+\S.+$")
        self.assertNotIn("gen3tl family", out.getvalue())

    def test_bare_groups_list_commands_without_loading_a_project(self):
        _, groups = parser()
        selection = ["--profile", "missing-profile", "--project-root", "missing-project"]
        for group, commands in groups.items():
            for argv in ([group], [*selection, group], [group, *selection]):
                out, err = io.StringIO(), io.StringIO()
                with self.subTest(argv=argv), patch("gen3tl.project.Registry.load") as load, \
                        redirect_stdout(out), redirect_stderr(err), self.assertRaises(SystemExit) as end:
                    main(argv)
                self.assertEqual(end.exception.code, 2)
                load.assert_not_called()
                self.assertEqual(out.getvalue(), "")
                self.assertIn(f"command      gen3tl {group}\n", err.getvalue())
                for command in commands:
                    self.assertRegex(err.getvalue(), rf"(?m)^command\s+gen3tl {group} {command}\s+\S.+$")

    def test_group_options_require_a_command(self):
        for group in ("build", "discover"):
            err = io.StringIO()
            with self.subTest(group=group), patch("gen3tl.project.Registry.load") as load, \
                    redirect_stderr(err), self.assertRaises(SystemExit) as end:
                main([group, "--out", "result"])
            self.assertEqual(end.exception.code, 2)
            load.assert_not_called()

    def test_explicit_group_help_succeeds(self):
        for group in GROUPS:
            for flag in ("-h", "--help"):
                out, err = io.StringIO(), io.StringIO()
                with self.subTest(group=group, flag=flag), redirect_stdout(out), redirect_stderr(err), \
                        self.assertRaises(SystemExit) as end:
                    main([group, flag])
                self.assertEqual(end.exception.code, 0)
                self.assertIn(f"command      gen3tl {group}\n", out.getvalue())
                listed = [" ".join(row.split()[2:4]) for row in out.getvalue().splitlines()
                          if row.startswith("command ")][1:]
                self.assertCountEqual(listed, [key for key in common.COMMANDS if key.startswith(f"{group} ")])
                self.assertEqual(err.getvalue(), "")

    def test_help_lists_option_help(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as end:
            main(["verify", "save-convert", "--help"])
        self.assertEqual(end.exception.code, 0)
        self.assertRegex(out.getvalue(), r"--edit +edit names only, for the ROM the save was made with\n")

    def test_every_command_and_argument_has_help(self):
        root, _ = parser()
        for current in help_parsers(root):
            with self.subTest(command=current.prog):
                self.assertTrue(current.description and current.description.strip())
                text = current.format_help()
                self.assertIn("about        ", text)
                self.assertIn(f"usage        {current.prog}", text)
                self.assertNotRegex(text, r"(?m)^default\s+(True|False)$")
            for action in current._actions:
                if isinstance(action, argparse._SubParsersAction):
                    continue
                with self.subTest(command=current.prog, argument=action.dest):
                    self.assertTrue(action.help and action.help.strip(), "missing argument help")

    def test_help_aligns_blocks_and_describes_positional_cardinality(self):
        p = common.StatusParser(prog="example", description="Inspect records.")
        p.add_argument("records", nargs="+", help="record paths")
        p.add_argument("--long-option-name", metavar=("KEY", "VALUE"), nargs=2, help="replacement pair")
        p.add_argument("--flag", action="store_true", help="enable replacement")
        p.add_argument("--enabled", action="store_false", help="disable replacement")
        p.add_argument("--hidden", help=argparse.SUPPRESS)
        text = p.format_help()
        self.assertIn("RECORDS [RECORDS ...] record paths\n", text)
        self.assertIn("--long-option-name KEY VALUE replacement pair\n", text)
        self.assertNotIn("--hidden", text)
        self.assertNotIn("default      False", text)
        rows = text.splitlines()
        columns = [next(row.index(value) for row in rows if row.endswith(value))
                   for value in ("replacement pair", "enable replacement", "disable replacement")]
        self.assertEqual(len(set(columns)), 1)
        self.assertEqual(sum(row.startswith("default ") for row in rows), 1)

    def test_help_defaults_match_dispatch_without_loading_roms(self):
        registry = Registry.load(CATALOG_STAGE)
        selected = registry.projects[0]
        cases = (("check", "unmeasured", ("out", "po_root")),
                 ("check", "coverage", ("out", "built", "inventory", "context")),
                 ("build", "rom", ("out",)),
                 ("build", "patch", ("out", "built", "notes", "converter")),
                 ("web", "snapshot", ("out",)),
                 ("web", "bundle", ("out",)),
                 ("web", "registry", ("data",)))
        with tempfile.TemporaryDirectory() as tmp, build_scope(Path(tmp) / "generated"):
            for group, command, fields in cases:
                handler_name = f"{group} {command}"
                handler = Mock(return_value=0)
                argv = [group, command, "--project-root", str(CATALOG_STAGE)]
                out = io.StringIO()
                with self.subTest(command=command), patch.dict(common.COMMANDS, {handler_name: handler}), \
                        patch.object(Profile, "load_rom", side_effect=AssertionError("ROM read during help")), \
                        redirect_stdout(out), self.assertRaises(SystemExit) as end:
                    main([*argv, "--help"])
                self.assertEqual(end.exception.code, 0)
                handler.assert_not_called()
                with patch.dict(common.COMMANDS, {handler_name: handler}), \
                        patch.object(Profile, "command_problem", return_value=None), \
                        patch.object(Project, "validate_built"), redirect_stdout(io.StringIO()):
                    self.assertEqual(main(argv), 0)
                args = handler.call_args.args[0]
                defaults = [row.split(None, 1)[1] for row in out.getvalue().splitlines()
                            if row.startswith("default ")]
                for field in fields:
                    self.assertIn(str(getattr(args, field)), defaults, field)
                if handler_name == "check unmeasured":
                    self.assertIn(str(selected.build / "unmeasured-report.md"), defaults)

    def test_help_keeps_missing_context_and_required_inputs_readable(self):
        out = io.StringIO()
        with redirect_stdout(out), patch("gen3tl.project.Registry.load", side_effect=ValueError("missing")), \
                self.assertRaises(SystemExit) as end:
            main(["verify", "story", "--help"])
        self.assertEqual(end.exception.code, 0)
        self.assertIn("about        ", out.getvalue())
        self.assertIn("--presses PRESSES", out.getvalue())
        self.assertRegex(out.getvalue(), r"(?m)^default\s+unresolved$")

    def test_help_selectors_use_the_requested_language_before_or_after_commands(self):
        registry = Registry.load(CATALOG_STAGE)
        with tempfile.TemporaryDirectory() as tmp:
            selected = replace(registry.projects[0], language="sample-language", root=Path(tmp))
            registry = replace(registry, projects=(*registry.projects, selected))
            selection = ["--project-root", str(CATALOG_STAGE), f"--lang={selected.language}"]
            command = ["check", "unmeasured"]
            for argv in ([*selection, *command], [*command, *selection]):
                out = io.StringIO()
                with self.subTest(argv=argv), patch.object(Registry, "load", return_value=registry), \
                        redirect_stdout(out), self.assertRaises(SystemExit) as end:
                    main([*argv, "--help"])
                self.assertEqual(end.exception.code, 0)
                self.assertIn(str(selected.catalog), out.getvalue())
                self.assertIn(str(selected.build / "unmeasured-report.md"), out.getvalue())
                self.assertNotIn(str(registry.projects[0].catalog), out.getvalue())

    def test_batch_export_plan_remains_opt_in(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit):
            main(["--project-root", str(CATALOG_STAGE), "catalog", "batch-export", "--help"])
        self.assertRegex(out.getvalue(), r"--plan \[PLAN\].*\ndefault\s+none\n")

    def test_group_command_dispatches_registered_handler(self):
        handler = Mock(return_value=0)
        err = io.StringIO()
        with patch.dict(common.COMMANDS, {"rom identify": handler}), redirect_stderr(err):
            self.assertEqual(main(["--project-root", str(CATALOG_STAGE), "rom", "identify"]), 0)
        handler.assert_called_once()
        self.assertEqual(handler.call_args.args[0].cmd, "rom identify")
        self.assertEqual(err.getvalue(), "")

class CommandOutputTest(unittest.TestCase):
    def test_table_aligns_records_and_escapes_control_characters(self):
        from gen3tl.core.terminal import table
        output = io.StringIO()
        rows = ((name, count) for name, count in (("short", 2), ("long\nname\tend\r", 120)))
        table(("name", "entries"), rows, file=output)
        lines = output.getvalue().splitlines()
        self.assertEqual([re.split(r" {2,}", row) for row in lines],
                         [["name", "entries"], ["short", "2"], [r"long\nname\tend\r", "120"]])
        self.assertEqual(lines[0].index("entries"), lines[1].index("2"))
        self.assertEqual(lines[0].index("entries"), lines[2].index("120"))
        self.assertTrue(all(row == row.rstrip() for row in lines))
        output = io.StringIO()
        table(("name", "entries"), [], file=output)
        self.assertEqual(output.getvalue(), "name  entries\n")
        for headers, rows in (([], []), (["name"], [["name", 1]]), (["name", "entries"], [["name"]])):
            with self.subTest(headers=headers, rows=rows), self.assertRaisesRegex(ValueError, "column count"):
                table(headers, rows, file=io.StringIO())

    def test_history_tables_keep_versions_and_observations_separate(self):
        from gen3tl.cli.build import build_history
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "translation-history.json"
            first = dict(profile="fixture", language="zz", version="0.1.0", file="Build one.gba",
                         built_sha1="a" * 40, changes=["Menu text"],
                         playtests=[dict(at="2026-01-01", status="launched"),
                                    dict(at="2026-01-02", status="issues reported")])
            second = dict(first, version="0.1.1", file="Build two.gba", delta="Build two.ups",
                          archive="Previous builds.zip", gameplay="checked", playtests=[])
            args = SimpleNamespace(default_build=root / "game.gba", profile="fixture", lang="zz",
                                   mark=None, status=None, note=None)
            ledger.write_text(json.dumps({"versions": [first, second, dict(first, profile="other")]}))
            with patch.object(Profile, "by_name", return_value=SimpleNamespace(name="fixture")), \
                 redirect_stdout(io.StringIO()) as output:
                build_history(args)
            lines = output.getvalue().splitlines()
            self.assertEqual(sum(row.startswith("history ") for row in lines), 1)
            self.assertEqual(sum(row.startswith("status ") for row in lines), 1)
            self.assertEqual(lines[-1], "status       ready")
            self.assertEqual([re.split(r" {2,}", row) for row in lines[2:-1]], [
                ["version", "gameplay", "file", "archive", "sha1"],
                ["0.1.0", "unknown", "Build one.gba", "-", "a" * 40],
                ["0.1.1", "checked", "Build two.ups", "Previous builds.zip", "a" * 40],
                ["version", "date", "gameplay"], ["0.1.0", "2026-01-01", "launched"],
                ["0.1.0", "2026-01-02", "issues-reported"]])
            args.mark, args.status, args.note = "0.1.1", "checked", "Menu checked"
            args.project = SimpleNamespace(release_notes=root / "notes.md")
            with patch.object(Profile, "by_name", return_value=SimpleNamespace(name="fixture")), \
                 redirect_stdout(io.StringIO()) as output:
                build_history(args)
            lines = output.getvalue().splitlines()
            self.assertEqual([row for row in lines if row.startswith("status ")], ["status       recorded"])
            recorded = json.loads(ledger.read_text())["versions"][1]["playtests"]
            self.assertEqual([(row["status"], row["note"]) for row in recorded], [("checked", "Menu checked")])
            ledger.unlink()
            args.mark = args.status = args.note = None
            with patch.object(Profile, "by_name", return_value=SimpleNamespace(name="fixture")), \
                 redirect_stdout(io.StringIO()) as output:
                build_history(args)
            self.assertEqual(output.getvalue().splitlines(), [f"history      {ledger}", "builds       0 builds",
                                                              "version  gameplay  file  archive  sha1",
                                                              "status       ready"])

    def test_rekey_status_combines_alias_and_migration_changes(self):
        from gen3tl.catalog import keys
        from gen3tl.cli.catalog import rekey
        aliases = keys.Aliases([("map:1/npc:1/0", 0x100, "s0000100"),
                                ("table:items/1", 0x200, "items:1")])
        unchanged = dict(entries=0, catalogs=0, files=[], unknown=[])
        cases = [(False, unchanged, "unchanged"), (True, unchanged, "changed"),
                 (False, dict(unchanged, entries=1, catalogs=1), "changed"),
                 (False, dict(unchanged, files=["rules.tsv"]), "changed"),
                 (False, dict(unchanged, unknown=["unknown:key"]), "failed")]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "context.json").write_text(json.dumps(dict(rom_sha1="a" * 40)))
            profile = Mock(name="fixture", sha1="a" * 40)
            profile.load_rom.return_value.data = b""
            project = SimpleNamespace(build=root, registry_root=root)
            registry = SimpleNamespace(root=root, projects=[])
            for dry_run in (False, True):
                for changed, report, status in cases:
                    expected = ("planned" if dry_run else "rewritten") if status == "changed" else status
                    known = keys.Aliases([]) if changed else aliases
                    args = SimpleNamespace(project=project, profile="fixture", rom=None, dry_run=dry_run)
                    with self.subTest(dry_run=dry_run, changed=changed, report=report), \
                         patch.object(Profile, "by_name", return_value=profile), \
                         patch.object(Registry, "load", return_value=registry), \
                         patch("gen3tl.discover.inventory.load", return_value=dict(rom_sha1=profile.sha1)), \
                         patch.object(keys, "for_profile", return_value=known), \
                         patch.object(keys, "assign", return_value=aliases), \
                         patch.object(keys, "rom_events"), patch.object(keys, "write") as written, \
                         patch("gen3tl.catalog.rekey.migrate", return_value=report), \
                         redirect_stdout(io.StringIO()) as output:
                        self.assertEqual(rekey(args), int(bool(report["unknown"])))
                        self.assertEqual(written.call_count, int(changed and not dry_run))
                    lines = output.getvalue().splitlines()
                    self.assertEqual([row for row in lines if row.startswith("status ")],
                                     [f"status       {expected}"])
                    self.assertEqual(lines[2:5], ["form   keys", "map    1", "table  1"])

    def test_values_cannot_create_unlabelled_lines(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            common.line("file", Path("a\nb\rc\td"))
            common.line("error", ValueError("first\nsecond"), file=sys.stderr)
        self.assertEqual(out.getvalue(), "file         a\\nb\\rc\\td\n")
        self.assertEqual(err.getvalue(), "error        first\\nsecond\n")

    def test_argument_errors_use_status_lines(self):
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as end:
            main(["unknown-group"])
        self.assertEqual(end.exception.code, 2)
        self.assertTrue(err.getvalue().startswith("error        "))
        self.assertEqual(len(err.getvalue().splitlines()), 2)
        self.assertIn("choose from", err.getvalue())
        self.assertNotIn("hint", err.getvalue())
        self.assertTrue(err.getvalue().splitlines()[-1].startswith("result       failed: "))

    def test_bad_offsets_fail_before_loading_a_project(self):
        for offset, reason in (("zz", "invalid hexadecimal offset: zz"), ("-1", "negative offset: -1")):
            output = io.StringIO()
            with self.subTest(offset=offset), redirect_stderr(output), \
                    patch.object(Registry, "load") as load, self.assertRaises(SystemExit) as end:
                main(["rom", "decode", offset])
            self.assertEqual(end.exception.code, 2)
            load.assert_not_called()
            self.assertIn(reason, output.getvalue().splitlines()[-1])
            self.assertNotIn("invalid literal", output.getvalue())
            self.assertNotIn("hint", output.getvalue())

    def test_unknown_profile_lists_available_names(self):
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            status = main(["--project-root", str(CATALOG_STAGE), "--profile", "absent-profile", "rom", "identify"])
        self.assertEqual(status, 2)
        self.assertRegex(output.getvalue(), r"(?m)^valid\s+\S+")
        self.assertIn("hint         profile: valid names", output.getvalue())
        self.assertEqual(output.getvalue().splitlines()[-1], "result       failed: unknown profile: absent-profile")

    def test_valid_offsets_keep_the_decoded_output(self):
        from gen3tl.core.rom import Rom
        from gen3tl.core.charmap import Charmap
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output), \
                patch.object(Profile, "load_rom", return_value=Rom(Charmap().encode("Example"))):
            status = main(["--project-root", str(CATALOG_STAGE), "rom", "decode", "0", "0x0"])
        self.assertIsNone(status)
        self.assertEqual(output.getvalue().count("text         Example\n"), 2)
        self.assertNotIn("result", output.getvalue())

    def test_runtime_errors_have_specific_hints_and_a_final_reason(self):
        wrapped = ValueError("cannot read registry: raw errno text")
        wrapped.__cause__ = FileNotFoundError(2, "raw errno text", "registry.toml")
        wrapped_json = ValueError("cannot read manifest: raw parser message: line 1 column 2 (char 1)")
        wrapped_json.__cause__ = json.JSONDecodeError("raw parser message", "{", 1)
        with self.assertRaises(tomllib.TOMLDecodeError) as invalid_toml:
            tomllib.loads("=")
        wrapped_toml = ValueError(f"profile.toml: {invalid_toml.exception}")
        wrapped_toml.__cause__ = invalid_toml.exception
        wrapped_unicode = ValueError("note.id: invalid Unicode")
        wrapped_unicode.__cause__ = UnicodeEncodeError("utf-8", "\ud800", 0, 1, "surrogates not allowed")
        rollback = OSError("rollback failed; recovery files preserved at stage")
        rollback.__cause__ = ValueError("newer catalog edits retained")
        sanitized = ValueError("cannot read registry: missing path: registry.toml")
        sanitized.__cause__ = FileNotFoundError(2, "raw errno text", "registry.toml")
        cases = ((FileNotFoundError(2, "raw errno text", "missing.json"), "missing path: missing.json",
                  "path: existing file or directory"),
                 (IsADirectoryError(21, "raw errno text", "folder"), "expected file: folder", "path: file"),
                 (PermissionError(13, "raw errno text", "locked"), "access denied: locked", "path: permissions"),
                 (ValueError("invalid literal for int() with base 16: 'zz'"), "invalid numeric value", None),
                 (ValueError("invalid manifest schema"), "invalid manifest schema", None),
                 (wrapped, "cannot read registry: missing path: registry.toml", "path: existing file or directory"),
                 (wrapped_json, "cannot read manifest: invalid JSON: line 1, column 2", "input: valid JSON"),
                 (wrapped_toml, "profile.toml: invalid TOML", "input: valid TOML"),
                 (wrapped_unicode, "note.id: invalid Unicode: invalid text encoding", "input: UTF-8 text"),
                 (rollback, "rollback failed; recovery files preserved at stage: newer catalog edits retained", None),
                 (sanitized, "cannot read registry: missing path: registry.toml", "path: existing file or directory"),
                 (json.JSONDecodeError("raw parser message", "{", 1), "invalid JSON: line 1, column 2",
                  "input: valid JSON"),
                 (invalid_toml.exception, "invalid TOML", "input: valid TOML"),
                 (KeyError("private runtime detail"), "internal command error: KeyError", None))
        for error, reason, hint in cases:
            output = io.StringIO()
            with self.subTest(error=type(error)), redirect_stdout(output), redirect_stderr(output), \
                    patch("gen3tl.cli.main._dispatch", side_effect=error):
                status = main(["--project-root", str(CATALOG_STAGE), "rom", "identify"])
            self.assertEqual(status, 1)
            lines = output.getvalue().splitlines()
            self.assertEqual(lines[0], f"error        {reason}")
            if hint:
                self.assertIn(f"hint         {hint}", output.getvalue())
            else:
                self.assertNotIn("hint", output.getvalue())
            self.assertNotIn("raw errno", output.getvalue())
            self.assertNotIn("raw parser message", output.getvalue())
            self.assertNotIn("Traceback", output.getvalue())
            self.assertNotIn("private runtime detail", output.getvalue())
            self.assertEqual(lines[-1], f"result       failed: {reason}")

    def test_returned_failures_retain_the_reason_and_do_not_leak_state(self):
        def failed(*args):
            common.line("error", "unresolved entries")
            common.line("report", "coverage.md")
            return 1

        for handler, expected in ((failed, "unresolved entries"), (lambda *args: 1, "rom identify: checks failed"),
                                  (lambda *args: 0, None)):
            output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(output), \
                    patch("gen3tl.cli.main._dispatch", side_effect=handler):
                status = main(["--project-root", str(CATALOG_STAGE), "rom", "identify"])
            if expected:
                self.assertEqual(status, 1)
                self.assertEqual(output.getvalue().splitlines()[-1], f"result       failed: {expected}")
                self.assertEqual(output.getvalue().count("result       failed:"), 1)
            else:
                self.assertEqual(status, 0)
                self.assertEqual(output.getvalue(), "")

    def test_locally_reported_errors_use_the_shared_formatter(self):
        def failed(*args):
            common.line("error", FileNotFoundError(2, "raw errno text", "missing.json"))
            return 1

        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output), \
                patch("gen3tl.cli.main._dispatch", side_effect=failed):
            status = main(["--project-root", str(CATALOG_STAGE), "rom", "identify"])
        self.assertEqual(status, 1)
        self.assertNotIn("raw errno", output.getvalue())
        self.assertIn("hint         path: existing file or directory", output.getvalue())
        self.assertEqual(output.getvalue().splitlines()[-1], "result       failed: missing path: missing.json")

    def test_explicit_exits_and_interruptions_end_with_a_reason(self):
        for code in (None, 0, 1, 2, "", "stopped"):
            output = io.StringIO()
            with self.subTest(code=code), redirect_stdout(output), redirect_stderr(output), \
                    patch("gen3tl.cli.main._dispatch", side_effect=SystemExit(code)), \
                    self.assertRaises(SystemExit) as end:
                main(["--project-root", str(CATALOG_STAGE), "rom", "identify"])
            self.assertEqual(end.exception.code, code if code is None or isinstance(code, int) else 1)
            if code is None or code == 0:
                self.assertEqual(output.getvalue(), "")
            else:
                self.assertTrue(output.getvalue().splitlines()[-1].startswith("result       failed: "))
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output), \
                patch("gen3tl.cli.main._dispatch", side_effect=KeyboardInterrupt):
            status = main(["--project-root", str(CATALOG_STAGE), "rom", "identify"])
        self.assertEqual(status, 130)
        self.assertEqual(output.getvalue().splitlines()[-1], "result       failed: interrupted")
