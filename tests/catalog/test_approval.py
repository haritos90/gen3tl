"""Approval of the translated entries that a played release holds unchanged."""

import io
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from project_fixture import workspace_project
from gen3tl.catalog.approval import approve_played
from gen3tl.catalog.po import PoEntry, read, render, write
from gen3tl.cli import main as cli

HEADER = {"Project-Id-Version": "game", "Language": "ru"}
TAGGED = {
    "a.po": [PoEntry("equal", "Equal", "Равно", flags=["fuzzy"]),
             PoEntry("blank", "Blank", "", flags=["empty", "fuzzy"]),
             PoEntry("changed", "Changed", "Старый", flags=["fuzzy"]),
             PoEntry("approved", "Approved", "Готово"),
             PoEntry("demoted", "Demoted", "Текст"),
             PoEntry("untranslated", "Untranslated", ""),
             PoEntry("flagged", "Flagged", "", flags=["fuzzy"]),
             PoEntry("moved", "Moved", "Перенос", flags=["fuzzy"])],
    "maps/c.po": [PoEntry("steady", "Steady", "Ровно")],
}
CURRENT = {
    "a.po": [PoEntry("equal", "Equal", "Равно", flags=["fuzzy"]),
             PoEntry("blank", "Blank", "", flags=["empty", "fuzzy"]),
             PoEntry("changed", "Changed", "Новый", flags=["fuzzy"]),
             PoEntry("approved", "Approved", "Готово"),
             PoEntry("demoted", "Demoted", "Текст", flags=["fuzzy"]),
             PoEntry("untranslated", "Untranslated", ""),
             PoEntry("flagged", "Flagged", "", flags=["fuzzy"])],
    "b.po": [PoEntry("moved", "Moved", "Перенос", flags=["fuzzy"]),
             PoEntry("missing", "Missing", "Новая", flags=["fuzzy"])],
    "maps/c.po": [PoEntry("steady", "Steady", "Ровно")],
}
APPROVED = {"equal", "blank", "moved"}


class PlayedVersionTest(unittest.TestCase):
    """A project in a git repository: the release 1.2.0 tagged, then edited."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.profile = workspace_project(self.root)
        self.po = self.root / "translation/po"
        self.git("init", "-q")
        self.store(TAGGED)
        self.commit("release")
        self.git("tag", "ru-v1.2.0")
        self.store(CURRENT)
        self.commit("later work")
        self.before = self.files()

    def git(self, *argv):
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                        "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *argv],
                       check=True, capture_output=True)

    def commit(self, subject):
        self.git("add", ".")
        self.git("commit", "-qm", subject)

    def store(self, files):
        for name, entries in files.items():
            write(self.po / name, [PoEntry(e.msgctxt, e.msgid, e.msgstr, flags=list(e.flags)) for e in entries],
                  HEADER)

    def files(self):
        return {p.relative_to(self.po).as_posix(): p.read_bytes() for p in sorted(self.po.rglob("*.po"))}

    def approve(self, *argv):
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            status = cli.main(["--project-root", str(self.root), "--profile", str(self.profile),
                               "catalog", "approve", *argv])
        return status, output.getvalue()

    def flags(self):
        return {e.msgctxt: e.flags for p in sorted(self.po.rglob("*.po")) for e in read(p)[1]}

    def test_entries_the_tag_holds_unchanged_become_approved(self):
        status, output = self.approve("--played", "1.2.0")
        self.assertEqual(status, 0, output)
        self.assertEqual(output, "tag          ru-v1.2.0\napproved     3 entries\nkept         3 entries\n"
                                 "files        2 changed\nstatus       applied\n")
        self.assertEqual(self.flags(), {
            "equal": [], "blank": ["empty"], "moved": [],                       # the tag holds them unchanged
            "changed": ["fuzzy"], "demoted": ["fuzzy"], "missing": ["fuzzy"],   # they differ from the tag
            "approved": [], "steady": [], "untranslated": [], "flagged": ["fuzzy"]})
        after = self.files()
        self.assertEqual(after["maps/c.po"], self.before["maps/c.po"])
        for name in ("a.po", "b.po"):   # the writer's form of the same entries without the flag
            entries = [PoEntry(e.msgctxt, e.msgid, e.msgstr,
                               flags=[f for f in e.flags if f != "fuzzy" or e.msgctxt not in APPROVED])
                       for e in CURRENT[name]]
            self.assertEqual(after[name], render(self.po / name, entries, HEADER).encode("utf-8"), name)
            self.assertNotEqual(after[name], self.before[name])

    def test_a_second_run_changes_nothing(self):
        self.assertEqual(self.approve("--played", "1.2.0")[0], 0)
        applied = self.files()
        status, output = self.approve("--played", "1.2.0")
        self.assertEqual(status, 0, output)
        self.assertEqual(output, "tag          ru-v1.2.0\napproved     0 entries\nkept         3 entries\n"
                                 "files        0 changed\nstatus       unchanged\n")
        self.assertEqual(self.files(), applied)

    def test_a_dry_run_reports_the_counts_and_writes_nothing(self):
        status, output = self.approve("--played", "1.2.0", "--dry-run")
        self.assertEqual(status, 0, output)
        self.assertEqual(output, "tag          ru-v1.2.0\napproved     3 entries\nkept         3 entries\n"
                                 "files        2 changed\nstatus       planned\n")
        self.assertEqual(self.files(), self.before)

    def test_an_annotated_tag_is_read_like_a_plain_one(self):
        self.git("tag", "-a", "-m", "release", "ru-v1.2.1", "ru-v1.2.0")
        status, output = self.approve("--played", "1.2.1", "--dry-run")
        self.assertEqual(status, 0, output)
        self.assertIn("tag          ru-v1.2.1\napproved     3 entries\n", output)

    def test_a_missing_tag_and_an_invalid_version_are_errors(self):
        self.git("tag", "de-v2.0.0")
        for version, reason in (("2.0.0", "tag ru-v2.0.0 does not exist"),
                                ("1.2", "1.2: not a version <major>.<minor>.<patch>"),
                                ("1.2.0:a.po", "1.2.0:a.po: not a version <major>.<minor>.<patch>")):
            with self.subTest(version=version):
                status, output = self.approve("--played", version)
                self.assertEqual(status, 1, output)
                self.assertIn(f"error        {reason}\n", output)
                self.assertEqual(self.files(), self.before)

    def test_files_outside_a_repository_are_an_error(self):
        shutil.rmtree(self.root / ".git")
        with mock.patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": str(self.root.parent)}):
            status, output = self.approve("--played", "1.2.0")
            self.assertEqual(status, 1, output)
            self.assertIn(f"error        {self.root}: not a git repository\n", output)
            with self.assertRaisesRegex(ValueError, "not a git repository"):
                approve_played(self.root, self.po, "ru", "1.2.0")
        self.assertEqual(self.files(), self.before)

    def test_a_catalog_outside_the_repository_is_an_error(self):
        with tempfile.TemporaryDirectory() as other:
            with self.assertRaisesRegex(ValueError, "not inside the git repository"):
                approve_played(self.root, Path(other).resolve(), "ru", "1.2.0")

    def test_the_command_states_its_help(self):
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output), self.assertRaises(SystemExit):
            cli.parser()[0].parse_args(["catalog", "approve", "--help"])
        for text in ("Mark approved the translated entries that a played version holds unchanged.",
                     "version whose release tag <code>-v<version> was playtested", "report the counts"):
            self.assertIn(text, output.getvalue())


if __name__ == "__main__":
    unittest.main()
