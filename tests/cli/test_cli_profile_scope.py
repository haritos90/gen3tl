"""Game-independent command dispatch and missing-profile controls."""

from contextlib import chdir, redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gen3tl.cli.main import main
from gen3tl.profile import profile
from project_fixture import workspace_project


class ProfileScopeTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        (self.root / "projects.toml").write_text("", encoding="utf-8")
        self.enterContext(chdir(self.root))
        self.enterContext(patch.object(profile, "PROFILE_DIR", self.root / "profiles"))
        self.out, self.err = io.StringIO(), io.StringIO()
        self.enterContext(redirect_stdout(self.out))
        self.enterContext(redirect_stderr(self.err))

    def test_shared_commands_do_not_select_a_profile(self):
        (self.root / "message").write_text("fix(core): preserve the parsed value\n", encoding="utf-8")
        commands = (("check", "commit-message", "message"),
                    ("web", "site-shell", "--web", "missing"),
                    ("web", "registry"), ("web", "server", "status", "--page", "site"),
                    ("web", "import", "missing"),
                    ("web", "submission", "--event", "missing", "--out", "answer"),
                    ("web", "site-catalog", "--approve", "xx"))
        with patch.object(profile.Profile, "by_name", side_effect=AssertionError("profile selected")) as select:
            for command in commands:
                with self.subTest(command=command):
                    self.assertIn(main(list(command)), (0, 1))
            select.assert_not_called()

    def test_site_catalog_writes_without_a_profile(self):
        with patch("gen3tl.cli.web._fixture_data", return_value=[]), \
                patch.object(profile.Profile, "by_name", side_effect=AssertionError("profile selected")):
            self.assertEqual(main(["web", "site-catalog", "--data", "data"]), 0)
        manifest = json.loads((self.root / "data/languages.json").read_text())
        self.assertTrue(manifest["languages"])
        self.assertEqual({row["profile"] for row in manifest["languages"]}, {"site"})

    def test_shared_command_retains_explicit_project_selection(self):
        selected = workspace_project(self.root, language="xx")
        with patch.object(profile.Profile, "by_name", side_effect=AssertionError("profile selected")), \
                patch.dict("gen3tl.cli.main.COMMANDS", {"web site-catalog": lambda args:
                           self.assertEqual(args.project.profile, selected.stem) or 0}):
            self.assertEqual(main(["--profile", str(selected), "web", "site-catalog"]), 0)

    def test_help_and_invalid_arguments_do_not_select_a_profile(self):
        with patch.object(profile.Profile, "by_name", side_effect=AssertionError("profile selected")):
            for command, status in ((["--help"], 0), (["web", "registry", "--help"], 0),
                                    (["web", "import", "--help"], 0),
                                    (["web", "unknown"], 2),
                                    (["verify", "profile", "--record", "unused", "--set"], 1)):
                with self.subTest(command=command):
                    try:
                        result = main(command)
                    except SystemExit as exc:
                        result = exc.code
                    self.assertEqual(result, status)
        self.assertIn("--set requires --compare", self.err.getvalue())
        self.assertFalse((self.root / "unused").exists())

    def test_game_command_still_requires_a_profile(self):
        self.assertEqual(main(["rom", "identify"]), 1)
        self.assertIn("select --profile explicitly; no unique default profile", self.err.getvalue())

    def test_explicit_unknown_profile_still_fails_for_game_command(self):
        self.assertEqual(main(["--profile", "missing", "rom", "identify"]), 2)
        self.assertIn("profile", self.err.getvalue())

    def test_site_child_process_receives_only_available_selection(self):
        (self.root / "projects.toml").unlink()
        with patch("gen3tl.cli.web._server_pid", return_value=None), \
                patch("socket.create_connection", side_effect=OSError), \
                patch("subprocess.Popen") as spawn:
            spawn.return_value.pid = 123
            spawn.return_value.poll.return_value = 1
            self.assertEqual(main(["web", "server", "start", "--page", "site", "--port", "8123"]), 1)
        command = spawn.call_args.args[0]
        self.assertNotIn("--profile", command)
        self.assertNotIn("--lang", command)
        self.assertNotIn("--project-root", command)
        self.assertTrue(all(isinstance(value, str) for value in command))
