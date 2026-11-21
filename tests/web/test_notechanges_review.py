from test_roots import TOOLKIT_ROOT
"""Notechanges review checks and fixtures."""

import sys
from pathlib import Path
import contextlib
import io
import json
import re
import unittest
from unittest.mock import patch

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from project_fixture import literal
from gen3tl.web import snapshotstore as snapshot_store
from gen3tl.catalog.po import KEY_ONLY, SOURCE_FIELD, PoEntry, write
from gen3tl.catalog.source import digest
from gen3tl.check import prcheck
from gen3tl.core import notes
from gen3tl.profile.profile import Profile
from gen3tl.web import notechanges, submission
import notechanges_support
from notechanges_support import change


PAYLOAD = "@octocat Fixes #1 ![p](https://evil.test/p.png) [click](https://evil.test) <img src=x> :tada:"


def live(markdown: str) -> str:
    """The Markdown outside fenced blocks and inline code spans."""
    markdown = re.sub(r"(?ms)^(`{3,})[^\n]*\n.*?^\1[ \t]*$", "", markdown)
    return re.sub(r"(`+)(.+?)\1", "", markdown)


class NoteSubmissionTest(notechanges_support.NoteSubmissionTest, unittest.TestCase):
    def test_new_notes_are_attributed_to_the_issue_author(self):
        document = self.export([change(self.note)])
        code, out = self.run_event(document, sender="maintainer", author="contributor")
        self.assertEqual(code, 0)
        self.assertEqual((out / "note-proposals").read_text(), "1\n")
        self.assertEqual((out / "summary").read_text(), "1 note proposal of sample/xx\n")
        self.assertEqual(self.stored()["note:example"]["author"], "contributor")
        body = (out / "pr.md").read_text()
        self.assertIn("| Submitted by | `contributor`, the issue author |", body)
        self.assertIn("applied with status proposed", body)
        self.assertNotIn("maintainer", body)

    def test_deep_nesting_is_refused_with_a_status(self):
        deep = change(self.note)
        deep["base_revision"] = "[" * 200000
        nested = json.dumps(self.export([change(self.note)]))[:-1] + ', "x": ' + "[" * 200000 + "]" * 200000 + "}"
        for document in (self.export([deep]), nested):
            with self.subTest(outer=isinstance(document, str)):
                if isinstance(document, str):
                    self.path.write_text(document)
                    event = self.root / "event.json"
                    event.write_text(json.dumps({"issue": {"number": 7, "user": {"login": "contributor"},
                                                           "body": "### Editor export\n" + document}}))
                    with patch.object(Profile, "by_name", return_value=self.raw), \
                            contextlib.redirect_stdout(io.StringIO()):
                        code = submission.run(event, self.root / "out", self.registry)
                    out = self.root / "out"
                else:
                    code, out = self.run_event(document)
                self.assertEqual(code, 1)
                self.assertEqual((out / "status").read_text(), "invalid\n")
                self.assertIn("Submission not applied", (out / "comment.md").read_text())

    def test_identities_and_control_characters_are_refused(self):
        attachment = self.capture_change()
        attachment["attachments"][0]["name"] = "capture\r## Forged.png"
        cases = [change({**self.note, "id": "note:x\r- [x] Approved by the maintainers\r## Forged heading\r"}),
                 change({**self.note, "id": "note:@octocat"}),
                 change({**self.note, "id": "note:x y"}),
                 change({**self.note, "guidance": ["line\rforged"]}),
                 change({**self.note, "guidance": ["line forged"]}),
                 change({**self.note, "guidance": ["line\x85forged"]}),
                 {**change(self.note), "message": "see\r> quoted block"},
                 attachment]
        for candidate in cases:
            with self.subTest(candidate=candidate["id"][:20]):
                code, out = self.run_event(self.export([candidate]))
                self.assertEqual(code, 1)
                comment = (out / "comment.md").read_text()
                self.assertNotIn("\r", comment)
                self.assertNotIn("Forged", live(comment))
                self.assertNotIn("@octocat", live(comment))
        self.assertFalse(self.entries.exists())

    def test_submitted_text_stays_inert_in_every_markdown_output(self):
        candidate = self.capture_change()
        candidate["proposed"].update(guidance=[PAYLOAD], reason=["`code` | pipe ``` fence"])
        candidate["message"] = PAYLOAD + "\n> quoted\nCloses #2"
        candidate["attachments"][0]["name"] = PAYLOAD
        candidate["attachments"][0]["context"] = candidate["attachments"][1]["context"] = PAYLOAD
        for evidence in candidate["proposed"]["evidence"]:
            for capture in evidence["captures"]:
                capture["locator"] = capture["locator"].replace("Main menu", PAYLOAD)
        code, out = self.run_event(self.export([candidate], notes=PAYLOAD))
        self.assertEqual(code, 0, (out / "comment.md").read_text() if (out / "comment.md").exists() else "")
        _, report = self.check([self.entries])
        outputs = {"pull request": (out / "pr.md").read_text(), "issue answer": (out / "notes.md").read_text(),
                   "check comment": report}
        for name, text in outputs.items():
            with self.subTest(output=name):
                self.assertIn("evil.test", text)
                for marker in ("@octocat", "#1", "#2", "![", "](", "<img", ":tada:", "> quoted"):
                    self.assertNotIn(marker, live(text))
        # A refused change shows crafted field names fenced.
        code, out = self.run_event(self.export([{**change(self.note), PAYLOAD: 1}]))
        self.assertEqual(code, 1)
        comment = (out / "comment.md").read_text()
        self.assertIn("evil.test", comment)
        for marker in ("@octocat", "#1", "![", "](", "<img"):
            self.assertNotIn(marker, live(comment))

    def test_review_tables_keep_their_columns(self):
        candidate = change({**self.note, "guidance": ["a | b", "`x`", "line\nbreak"]})
        text = notechanges.provenance([candidate], submitter="contributor", submitted="2026-01-02T03:04:05Z",
                                      sha256="ab" * 32, files=["translation/xx/notes/entries.toml"])
        review, _ = notechanges.review([{**candidate, "file": "translation/xx/notes/entries.toml"}])
        for line in (text + review).splitlines():
            if line.startswith("| guidance"):
                cells = re.split(r"(?<!\\)\|", line)
                self.assertEqual(len(cells), 5, line)

    def test_review_joins_entry_text_without_persisting_it(self):
        from gen3tl.catalog import extract

        english = "Review source only."
        self.note["binding"]["source_sha256"] = digest(english)
        snapshot_store.write(self.project.snapshot, {"source": {"key": [digest(english)],
                                                                "legacy": [digest("Legacy.")]}})
        extract.write(self.project.build / "catalog.pot", [PoEntry("key", english, "")], self.prof)
        shared = {**self.note, "id": "note:shared", "scope": {"kind": "shared"}}
        document = self.export([change(self.note), change(shared)])
        code, out = self.run_event(document)
        self.assertEqual(code, 0)
        files = [self.root / path for path in (out / "files").read_text().splitlines()]
        code, report = self.check(files)
        self.assertEqual(code, 0, report)
        for text in ((out / "pr.md").read_text(), report):
            self.assertEqual(text.count("English: `Review source only.`"), 2)
            self.assertEqual(text.count("Current translation: `Old`"), 2)
            self.assertIn(digest(english), text)
        for path in [*files, self.po, self.path]:
            self.assertNotIn(english, path.read_text())
        self.assertEqual(self.po.read_bytes(), self.original[0])

    def test_review_keeps_entry_identity_when_english_is_unavailable(self):
        from gen3tl.catalog import extract

        path = self.project.build / "catalog.pot"
        for unavailable in ("missing", "missing key", "another ROM"):
            with self.subTest(unavailable=unavailable):
                if unavailable == "missing":
                    path.unlink(missing_ok=True)
                elif unavailable == "missing key":
                    extract.write(path, [PoEntry("legacy", "Legacy.", "")], self.prof)
                else:
                    write(path, [PoEntry("key", "Wrong source.", "")],
                          {**extract.header(self.prof), "X-ROM-SHA1": "cd" * 20})
                self.entries.unlink(missing_ok=True)
                code, out = self.run_event(self.export([change(self.note)]))
                self.assertEqual(code, 0)
                code, report = self.check([self.entries])
                self.assertEqual(code, 0, report)
                for text in ((out / "pr.md").read_text(), report):
                    self.assertIn("English: `text unavailable`", text)
                    self.assertIn("Current translation: `Old`", text)
                    self.assertIn("`entry/key`", text)
                    self.assertIn(digest("Example."), text)
                    self.assertNotIn("Wrong source.", text)

    def test_review_resolves_term_research_and_wording_text_inertly(self):
        candidates = self.containers()[:3]
        self.project.glossary.write_text("id\tcategory\tsource_text\ttranslation\tnotes\n"
                                         f"term:one\tmisc\tTerm {PAYLOAD}\tChosen {PAYLOAD}\t\n")
        (self.project.translation / "research/sample.tsv").write_text(
            f"record_id\tnote_ids\tnotes\tfinding\nresearch:one\t\t\tFinding {PAYLOAD}\n")
        path = self.project.translation / "rules.toml"
        path.write_text(path.read_text().replace('"Keep context."', literal(f"Wording {PAYLOAD}")))
        before = {file: file.read_bytes() for file in (self.raw.path, self.project.policy, path)}
        code, out = self.run_event(self.export([change(note) for note in candidates]))
        self.assertEqual(code, 0)
        files = [self.root / path for path in (out / "files").read_text().splitlines()]
        with patch.object(prcheck, "_project_checks", return_value=([], 0, "fixture")):
            code, report = self.check(files, before)
        self.assertEqual(code, 0, report)
        for text in ((out / "pr.md").read_text(), report):
            for expected in ("Term: `Term ", "Selected form: `Chosen ", "Research row: `", "Finding ",
                             "Wording rule: `Wording "):
                self.assertIn(expected, text)
            for marker in ("@octocat", "#1", "![", "](", "<img", ":tada:"):
                self.assertNotIn(marker, live(text))

    def test_joined_text_is_inert_and_visibly_truncated_within_review_bounds(self):
        from gen3tl.catalog import extract

        payload = "``` ` | " + PAYLOAD + "\n> quoted " + "long text " * 1000
        self.note["binding"]["source_sha256"] = digest(payload)
        snapshot_store.write(self.project.snapshot, {"source": {"key": [digest(payload)]}})
        extract.write(self.project.build / "catalog.pot", [PoEntry("key", payload, "")], self.prof)
        write(self.po, [PoEntry("key", "key", payload)],
              {"Project-Id-Version": "sample", "Language": "xx", SOURCE_FIELD: KEY_ONLY})
        changes = [change({**self.note, "id": f"note:{index}"}) for index in range(notechanges.CHANGE_LIMIT)]
        self.export(changes)
        result = self.apply(check=True)
        body = notechanges.provenance(result.note_changes, submitter=result.submitter, submitted=result.submitted,
                                      sha256=result.sha256, files=[], subjects=result.note_subjects)
        selected = [{**candidate, "file": "notes/entries.toml"} for candidate in result.note_changes]
        review, cut = notechanges.review(selected, notechanges.REVIEW_LIMIT, subjects=result.note_subjects)
        self.assertTrue(cut)
        self.assertLessEqual(len(review), notechanges.REVIEW_LIMIT)
        self.assertLessEqual(len(body), notechanges.BODY_LIMIT)
        single = submission.pull_request(7, result)
        complete = notechanges.review(selected, subjects=result.note_subjects)[0]
        short = notechanges.provenance(result.note_changes[:1], submitter=result.submitter, submitted=result.submitted,
                                       sha256=result.sha256, files=[], subjects=result.note_subjects)
        for text in (review, complete, short):
            self.assertIn("… [truncated]", text)
            self.assertIn("evil.test", text)
            self.assertNotIn("long text " * 100, text)
            self.assertNotIn("\r", text)
            self.assertNotIn("\u2028", text)
            for marker in ("@octocat", "#1", "![", "](", "<img", ":tada:", "> quoted"):
                self.assertNotIn(marker, live(text))
            for line in text.splitlines():
                if line.startswith("| `note:"):
                    self.assertEqual(len(re.split(r"(?<!\\)\|", line)), 9 if text != short else 8)
        self.assertLess(len(single + review), 65536)

    def test_review_size_is_bounded(self):
        changes = [change({**self.note, "id": f"note:{index}", "guidance": ["g" * notechanges.TEXT_LIMIT] * 3,
                           "reason": ["r" * notechanges.TEXT_LIMIT]})
                   for index in range(notechanges.CHANGE_LIMIT)]
        self.export(changes + [change({**self.note, "id": "note:extra"})])
        with self.assertRaisesRegex(ValueError, "at most 50"):
            self.apply(check=True)
        code, out = self.run_event(self.export(changes))
        self.assertEqual(code, 0)
        _, report = self.check([self.entries])
        body = (out / "pr.md").read_text() + report
        self.assertLess(len(body), 65536)
        self.assertIn("note-review.md", report)
        complete = (self.root / "check/note-review.md").read_text()
        self.assertEqual(complete.count("### Add"), notechanges.CHANGE_LIMIT)
        self.export([change({**self.note, "guidance": ["g" * (notechanges.TEXT_LIMIT + 1)]})])
        with self.assertRaisesRegex(ValueError, "exceeds"):
            self.apply(check=True)

    def test_referenced_notes_cannot_be_removed(self):
        base, _ = self.published()
        related = {**self.note, "id": "note:related", "related_notes": ["note:example"], "status": "accepted"}
        notes.write_notes(self.entries, {"schema": notes.SCHEMA, "note": [base, related]})
        self.export([change(base, base, "remove")])
        with self.assertRaisesRegex(ValueError, "relates to removed note"):
            self.apply()
        notes.write_notes(self.entries, {"schema": notes.SCHEMA, "note": [base]})
        added = {**self.note, "id": "note:new", "related_notes": ["note:example"]}
        self.export([change(base, base, "remove"), change(added)])
        with self.assertRaisesRegex(ValueError, "removed related note"):
            self.apply()
        self.assertEqual(self.stored(), {"note:example": base})
