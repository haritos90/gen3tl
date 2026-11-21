from test_roots import TOOLKIT_ROOT
"""Submission locate checks and fixtures."""

import sys
from pathlib import Path
import io
import unittest
from unittest.mock import patch

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.web import submission
from submission_support import asset, form


class LocateTest(unittest.TestCase):
    URL = "https://github.com/user-attachments/files/123/gen3tl-xx-edits.json"

    def test_form_fields(self):
        self.assertEqual(submission.locate(form('{"a": 1}')), ('{"a": 1}', None))
        self.assertEqual(submission.locate(form(attachment=f"[gen3tl-xx-edits.json]({self.URL})")), (None, self.URL))
        self.assertEqual(submission.locate(form()), (None, None))
        nested = form('```json\n{"a": 1}\n```')
        self.assertEqual(submission.locate(nested), ('{"a": 1}', None))
        older = "https://github.com/owner/repo/files/9/export.json"
        self.assertEqual(submission.locate(form(attachment=older)), (None, older))

    def test_body_without_form_headings(self):
        self.assertEqual(submission.locate('Edits:\n\n```json\n{"a": 1}\n```\n'), ('{"a": 1}', None))
        self.assertEqual(submission.locate(f"See {self.URL}"), (None, self.URL))

    def test_only_attachment_links_are_followed(self):
        self.assertEqual(submission.locate(form(attachment="https://example.org/files/1/x.json")), (None, None))
        with self.assertRaisesRegex(submission.Invalid, "no editor export found"):
            submission.document(form(attachment="https://example.org/files/1/x.json"))

    def test_picture_links(self):
        a, b = asset(1), asset(2)
        field = f'![T1]({a})\r\n<img width="64" alt="T2" src="{b}" />\r\n{a}\r\n{self.URL} https://example.org/assets/x'
        self.assertEqual(submission.picture_links(form('{"a": 1}', pictures=field)), [a, b])
        self.assertEqual(submission.picture_links(form('{"a": 1}')), [])
        older = "https://github.com/owner/repo/assets/12345/00000003-0000-4000-8000-000000000000"
        self.assertEqual(submission.picture_links(f'```json\n{{}}\n```\n![x]({older})'), [older])
        self.assertEqual(submission.picture_links(f"### Editor export\n\n```json\n{{}}\n```\n\n![x]({a})"), [])

    def test_inline_and_attached_together_are_refused(self):
        with self.assertRaisesRegex(submission.Invalid, "keep one of them"):
            submission.document(form('{"a": 1}', self.URL), fetch=lambda url: b"{}")

    def test_download_limit(self):
        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with patch("urllib.request.urlopen", return_value=Response(b"x" * (submission.LIMIT + 1))):
            with self.assertRaisesRegex(submission.Invalid, "exceeds 5 MiB"):
                submission.download(self.URL)
        with patch("urllib.request.urlopen", side_effect=OSError("HTTP Error 404")):
            with self.assertRaisesRegex(submission.Invalid, "cannot be downloaded"):
                submission.download(self.URL)
        with patch("urllib.request.urlopen", return_value=Response(b"x" * (submission.PICTURE_LIMIT + 1))):
            with self.assertRaisesRegex(submission.Invalid, "^an attached picture exceeds 64 KiB$"):
                submission.download(asset(1), submission.PICTURE_LIMIT, "an attached picture")
