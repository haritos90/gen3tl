from test_roots import TOOLKIT_ROOT
"""Notechanges captures checks and fixtures."""

import sys
from pathlib import Path
import re
import unittest
from copy import deepcopy

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.web import notechanges, submission
import notechanges_support
from test_roots import TOOLKIT_ROOT as ROOT


class NoteSubmissionTest(notechanges_support.NoteSubmissionTest, unittest.TestCase):
    def test_evidence_is_preserved_without_creating_replacement_artwork(self):
        candidate = self.capture_change()
        self.export([candidate])
        result = self.apply()
        self.assertEqual(self.stored()["note:example"]["evidence"], candidate["proposed"]["evidence"])
        self.assertNotIn(candidate["attachments"][0]["data"], self.entries.read_text())
        self.assertEqual(len(result.files), 1)
        self.assertFalse(result.pictures)
        self.assertEqual(self.project.policy.read_bytes(), self.original[1])
        for field, value in (("sha256", "0" * 64), ("type", "image/jpeg"), ("size", 4 * 1024 * 1024),
                             ("context", "Other menu"), ("data", "!"), ("role", "failed"), ("id", "a b")):
            bad = deepcopy(candidate)
            bad["attachments"][0][field] = value
            self.export([bad])
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.apply()

    def test_capture_limit_fits_the_export_limit(self):
        encoded = 4 * -(-notechanges.CAPTURE_LIMIT // 3)
        self.assertLess(encoded + 64 * 1024, submission.LIMIT)
        store = (ROOT / "web/js/store.js").read_text(encoding="utf-8")
        found = re.search(r"export const CAPTURE_LIMIT = (\d+) \* 1024 \* 1024;", store)
        self.assertIsNotNone(found)
        self.assertEqual(int(found.group(1)) * 1024 * 1024, notechanges.CAPTURE_LIMIT)
        raw = b"\x89PNG\r\n\x1a\n" + bytes(notechanges.CAPTURE_LIMIT - 8)
        candidate = self.capture_change(raw)
        candidate["attachments"] = candidate["attachments"][:1]
        evidence = candidate["proposed"]["evidence"][0]
        evidence["captures"] = evidence["captures"][:1]
        self.export([candidate])
        self.assertLess(self.path.stat().st_size, submission.LIMIT)
        self.assertEqual(len(self.apply(check=True).note_changes), 1)

    def test_attachment_count_is_bounded(self):
        candidate = self.capture_change()
        extra = [{**candidate["attachments"][1], "id": f"extra{index}"} for index in range(notechanges.ATTACHMENT_LIMIT)]
        candidate["attachments"] += extra
        self.export([candidate])
        with self.assertRaisesRegex(ValueError, "at most"):
            self.apply(check=True)
