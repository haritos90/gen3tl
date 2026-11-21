import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_roots import TOOLKIT_ROOT

ROOT = TOOLKIT_ROOT

from gen3tl.check import commitmsg

ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
       "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
       "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
       "PYTHONPATH": str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")}

FIFTEEN = " ".join(["word"] * 14)

def run(*args, cwd=None):
    return subprocess.run(args, cwd=cwd, env=ENV, capture_output=True, text=True)

def git(root, *args):
    result = run("git", "-C", str(root), *args)
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout

class RulesTest(unittest.TestCase):
    def assertAccepted(self, message):
        self.assertEqual(commitmsg.problems(message), [], message)

    def assertRejected(self, message, reason):
        found = commitmsg.problems(message)
        self.assertTrue(any(reason in problem for problem in found), (message, found))


    def test_local_record_identifiers_are_rejected(self):
        for message in ("fix(po): correct the plural forms (task-42)\n",
                        "fix(po): correct the plural forms (TASK-12.3)\n",
                        "fix(po): keep commas with move names (tasks 5, 6)\n",
                        "docs(readme): describe the split\n\n- as decision-12 states\n",
                        "docs(readme): describe the split\n\n- see doc-3 and draft-12\n"):
            self.assertRejected(message, "a local record identifier")
        self.assertRejected("fix(po): answer q-a1b2c3\n", "a local request identifier")

    def test_exactly_one_sign_off_trailer_is_accepted(self):
        subject = "fix(po): correct the plural forms\n\n"
        trailer = "Signed-off-by: Name <name@example.invalid>\n"
        self.assertAccepted(subject + trailer)
        self.assertRejected(subject + trailer + trailer, "an authorship trailer")
        self.assertRejected(subject + trailer + "Note: the header keeps its date\n", "an authorship trailer")

    def test_trailers_and_authorship_lines_are_rejected(self):
        subject = "fix(po): correct the plural forms\n\n"
        self.assertRejected(subject + "Co-authored-by: Name <name@example.invalid>\n", "an authorship trailer")
        self.assertRejected(subject + "- Signed-off-by: Name <name@example.invalid>\n", "an authorship trailer")
        self.assertRejected(subject + "Example-Session: https://example.invalid/session_0123456789abcdef\n",
                            "a session identifier")
        self.assertRejected(subject + "\U0001F916 Generated with Example Tool\n", "an authorship line")
        self.assertRejected(subject + "Note: the header keeps its date\n", "a trailer")

    def test_subject_form(self):
        self.assertRejected("feature: show the width\n", "is not 'type(scope): summary'")
        self.assertRejected("feat: show the width\n", "is not 'type(scope): summary'")
        self.assertRejected("build(ci): pin the runner\n", "type 'build' is not one of")
        self.assertRejected("feat(Editor): show the width\n", "scope 'Editor'")
        self.assertRejected("feat(editor): Show the width\n", "lowercase imperative verb")
        self.assertRejected("feat(editor): show the width.\n", "ends with a period")
        self.assertRejected(f"feat(editor): show {FIFTEEN} more\n", "16 words; the limit is 15")
        self.assertRejected("feat(editor):\n", "is not 'type(scope): summary'")
        self.assertRejected("\n\n", "the message is empty")

    def test_body_form(self):
        self.assertRejected("feat(editor): show the width\n- no blank line\n", "a blank line separates")
        self.assertRejected(f"feat(editor): show the width\n\n- {FIFTEEN} more\n", "16 words; a body line")

class CommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, *args):
        return run(sys.executable, "-m", "gen3tl", "check", "commit-message", *args, cwd=self.root)

    def message(self, text):
        path = self.root / "message"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def commit(self, subject):
        git(self.root, "commit", "-q", "--allow-empty", "-m", subject)
        return git(self.root, "rev-parse", "HEAD").strip()


    def test_hook_ignores_comments_and_verbose_diff(self):
        text = ("feat(editor): show the width\n# Please enter the commit message (task-1)\n"
                f"{commitmsg.SCISSORS}\ndiff --git a/x b/x\n+Note: task-2\n")
        self.assertEqual(self.check(self.message(text)).returncode, 0)

    def test_checkout_wrapper_runs_outside_the_checkout(self):
        wrapper = ROOT / ".github/scripts/commit-msg"
        accepted = run(sys.executable, str(wrapper), self.message("feat(editor): show the width\n"), cwd=self.root)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        rejected = run(sys.executable, str(wrapper), self.message("Show the width\n"), cwd=self.root)
        self.assertEqual(rejected.returncode, 1, rejected.stdout)

    def test_hook_accepts_the_merge_message(self):
        git(self.root, "init", "-q", "--initial-branch=main")
        self.commit("chore(repo): start")
        git(self.root, "checkout", "-q", "-b", "side")
        self.commit("feat(editor): add a side change")
        git(self.root, "checkout", "-q", "main")
        self.commit("fix(po): add a main change")
        git(self.root, "merge", "-q", "--no-ff", "--no-commit", "side")
        self.assertEqual(self.check(self.message("Merge branch 'side'\n")).returncode, 0)

    def test_range_checks_every_non_merge_commit(self):
        git(self.root, "init", "-q", "--initial-branch=main")
        base = self.commit("chore(repo): start")
        git(self.root, "checkout", "-q", "-b", "side")
        good = self.commit("feat(editor): add a side change")
        git(self.root, "checkout", "-q", "main")
        self.commit("fix(po): add a main change")
        git(self.root, "merge", "-q", "--no-ff", "-m", "Merge branch 'side'", "side")
        accepted = self.check("--range", f"{base}..HEAD")
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(accepted.stdout.split()[-4:], ["commits", "2", "problems", "0"])
        bad = self.commit("fix(po): correct the plural forms (task-42)")
        rejected = self.check("--range", f"{base}..HEAD")
        self.assertEqual(rejected.returncode, 1)
        self.assertIn(f"{bad[:12]}: line 1: names a local record identifier", rejected.stderr)
        self.assertNotIn(good[:12], rejected.stderr)
        self.assertEqual(self.check("--range", "nowhere..HEAD").returncode, 2)
