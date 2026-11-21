"""Synthetic language setup does not add a production default."""

from contextlib import chdir
from pathlib import Path
import tempfile
import unittest

from gen3tl.core import notes
from gen3tl.lang import language


class FixtureLanguageScopeTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        (self.root / "projects.toml").write_text("", encoding="utf-8")
        self.enterContext(chdir(self.root))
        self.note = {"id": "note:one", "subject": {"kind": "research", "id": "finding:one"},
                     "scope": {"kind": "language", "language": ""}, "status": "unreviewed",
                     "guidance": ["Preserve the quoted spelling."]}

    def test_explicit_fixture_language_validates_and_restores_context(self):
        self.assertEqual(language.current().code, "")
        with language.use(language.Language("fixture", {}, self.root)):
            self.note["scope"]["language"] = language.current().code
            self.assertIsNone(notes.validate_note(self.note))
        self.assertEqual(language.current().code, "")

    def test_missing_registry_language_remains_invalid(self):
        self.note["scope"]["language"] = language.current().code
        with self.assertRaisesRegex(notes.NoteError, "expected a language identifier"):
            notes.validate_note(self.note)
