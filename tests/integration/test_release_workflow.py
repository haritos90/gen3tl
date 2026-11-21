import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_roots import TOOLKIT_ROOT

WORKFLOW = TOOLKIT_ROOT / ".github/workflows/release.yml"
STEP = "Write the body from the commits since the previous tag"


def step_script(text: str, name: str) -> str:
    """The shell of the named step, without the indentation of its block."""
    block = re.search(rf"- name: {re.escape(name)}\n(?:.*\n)*?        run: \|\n((?:(?:          .*)?\n)+)", text)
    return "".join(line[10:] if line.strip() else "\n" for line in block.group(1).splitlines(keepends=True))


@unittest.skipUnless(WORKFLOW.is_file() and shutil.which("git") and shutil.which("bash"), "needs the workflow, git and bash")
class ReleaseWorkflowTest(unittest.TestCase):
    """The release of a toolkit tag lists the commits since the previous toolkit tag."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
                    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
                    "RUNNER_TEMP": self.tmp.name, "REPOSITORY": "example/toolkit"}
        self.git("init", "-q", "-b", "main")
        self.script = step_script(WORKFLOW.read_text(encoding="utf-8"), STEP)

    def git(self, *argv: str) -> str:
        return subprocess.run(["git", "-C", str(self.repo), *argv], env=self.env, check=True,
                              capture_output=True, text=True).stdout

    def commit(self, subject: str, tag: str | None = None) -> str:
        (self.repo / "file").write_text(subject, encoding="utf-8")
        self.git("add", "file")
        self.git("commit", "-q", "-m", subject)
        if tag:
            self.git("tag", tag)
        return self.git("rev-parse", "HEAD").strip()

    def body(self, tag: str) -> list[str]:
        subprocess.run(["bash", "-euo", "pipefail", "-c", self.script], cwd=self.repo, check=True,
                       env={**self.env, "TAG": tag}, capture_output=True, text=True)
        return (Path(self.tmp.name) / "body.md").read_text(encoding="utf-8").splitlines()

    def test_a_later_tag_lists_the_commits_since_the_previous_toolkit_tag(self):
        self.commit("feat(core): add the first part", "v1.0.0")
        self.commit("chore(core): follow up", "v1.2.0")   # before v1.10.0 in version order alone
        second = self.commit("fix(core): repair the first part")
        self.git("tag", "ru-v9.9.9")   # a tag of another kind
        third = self.commit("feat(web): add a page", "v1.10.0")
        lines = self.body("v1.10.0")
        self.assertEqual(lines, [
            "Changes since `v1.2.0`:", "",
            f"- fix(core): repair the first part ([`{second[:7]}`](https://github.com/example/toolkit/commit/{second}))",
            f"- feat(web): add a page ([`{third[:7]}`](https://github.com/example/toolkit/commit/{third}))"])

    def test_the_first_tag_lists_every_commit(self):
        first = self.commit("feat(core): add the first part")
        second = self.commit("docs(readme): describe it", "v0.1.0")
        self.assertEqual(self.body("v0.1.0"), [
            "Changes:", "",
            f"- feat(core): add the first part ([`{first[:7]}`](https://github.com/example/toolkit/commit/{first}))",
            f"- docs(readme): describe it ([`{second[:7]}`](https://github.com/example/toolkit/commit/{second}))"])

    def test_the_workflow_runs_for_toolkit_tags_and_keeps_a_written_body(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("    tags: ['v*']", text)
        self.assertIn("contents: write", text)
        self.assertEqual(text.count("if: steps.found.outputs.state != 'written'"), 3)
        self.assertIn('--title "gen3tl ${TAG#v}"', text)


if __name__ == "__main__":
    unittest.main()
