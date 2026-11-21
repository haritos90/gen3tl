from pathlib import Path
import base64
import contextlib
import csv
import hashlib
import io
import json
import tempfile
from unittest.mock import patch

from test_roots import TEST_ROOT
from project_fixture import literal, write_policy
from gen3tl.web import snapshotstore as snapshot_store
from gen3tl.cli import main as cli
from gen3tl.web import submission
from gen3tl.catalog.po import PoEntry, read, write
from gen3tl.catalog.source import digest
from gen3tl.profile.profile import Profile

SITE_EXPORT = TEST_ROOT / "fixtures/site-export.json"

def form(export: str = "", attachment: str = "", pictures: str = "") -> str:
    """An issue body as the submission form renders it."""
    inline = f"```json\n{export}\n```" if export else "_No response_"
    return (f"### Editor export\r\n\r\n{inline}\r\n\r\n### Export file\r\n\r\n{attachment or '_No response_'}"
            f"\r\n\r\n### Pictures\r\n\r\n{pictures or '_No response_'}")

def asset(n: int) -> str:
    """The link of an image attached to an issue."""
    return f"https://github.com/user-attachments/assets/{n:08x}-0000-4000-8000-000000000000"

class SubmissionBehavior:
    HEADER = {}   # the form of the catalogs
    IMPORT_REFUSAL = "T1: editor base differs from the current project picture; stale picture"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.po = self.root / "translation/po"
        entries = [PoEntry("s0000100", "Fit.", "Old text"),
                   PoEntry("s0000200", "Other.", "Kept text", flags=[], translator=["source: a game"]),
                   PoEntry("s0000300", "Blank.", "", flags=["empty"])]
        self.header = {"Project-Id-Version": "test", "Language": "xx",
                       "Content-Type": "text/plain; charset=UTF-8", **self.HEADER}
        write(self.po / "map.po", entries, self.header)
        self.source_text = {}
        self.facts(entries)
        data = {"rom": {"sha1": "ab" * 20}, "font_style": {"style": "main", "cell_height": 14},
                "layout": {"dialogue_width": 60, "placeholders": {}}}
        self.profile = Profile(data, self.root / "test.toml")
        self.profile.path.write_text("\n".join(f"{k} = {literal(v)}" for k, v in data.items()), encoding="utf-8")
        write_policy(self.root, self.profile, language="xx")
        (self.root / "projects.toml").write_text(
            '[defaults]\nprofile="test"\nlanguage="xx"\n[[project]]\nprofile="test"\nlanguage="xx"\n'
            'root="."\npolicy="policy.toml"\n', encoding="utf-8")
        self.out = self.root / "answer"
        self.original = (self.po / "map.po").read_bytes()

    def facts(self, entries):
        """The snapshot digests of a key-only catalog's English."""
        if not self.HEADER:
            return
        self.source_text.update({e.msgctxt: e.msgid for e in entries})
        snapshot = self.root / "translation/editor-inputs.json"
        snapshot_store.write(snapshot, {"source": {sid: [digest(text)] for sid, text in self.source_text.items()}})

    def export(self, entries, **extra):
        return json.dumps({"language": "xx", "profile": "test", "entries": entries, **extra}, ensure_ascii=False)

    def submit(self, body, *options, fetch=None):
        event = self.root / "event.json"
        event.write_text(json.dumps({"action": "opened", "issue": {"number": 5, "body": body,
                                                                   "user": {"login": "contributor"}}}), encoding="utf-8")
        hooks = [patch.object(Profile, "by_name", return_value=self.profile),
                 patch.object(Profile, "load_rom", side_effect=AssertionError("ROM access forbidden"))]
        if fetch:
            hooks.append(patch.object(submission, "download", side_effect=fetch))
        with contextlib.ExitStack() as stack, contextlib.redirect_stdout(io.StringIO()):
            for hook in hooks:
                stack.enter_context(hook)
            return cli.main(["--project-root", str(self.root), "web", "submission", "--event", str(event),
                             "--out", str(self.out), *options])

    def answer(self, name):
        return (self.out / name).read_text(encoding="utf-8")

    def test_state_only_changes_are_applied(self):
        for before, after in (("original", "empty"), ("empty", "original")):
            with self.subTest(before=before, after=after):
                entry = PoEntry("ending", "ending", flags=["empty"] if before == "empty" else [])
                write(self.po / "map.po", [entry], self.header)
                self.facts([entry])
                body = form(self.export([{"id": "ending", "base": "", "base_state": before,
                                         "translation": "", "state": after}]))
                original = (self.po / "map.po").read_bytes()
                self.assertEqual(self.submit(body, "--check"), 0)
                self.assertEqual((self.po / "map.po").read_bytes(), original)
                self.assertEqual(self.submit(body), 0)
                saved = read(self.po / "map.po")[1][0]
                self.assertEqual((saved.msgstr, saved.state, saved.fuzzy), ("", after, before == "original"))
                self.assertEqual(self.submit(body), 1)
                self.assertIn("base state differs", self.answer("comment.md"))

    def test_invalid_states_cannot_change_catalogs(self):
        for change in ({"state": "off"}, {"state": "empty"}, {"state": "text", "translation": ""},
                       {"base": "", "base_state": "text"}):
            with self.subTest(change=change):
                body = form(self.export([{"id": "s0000300", "translation": "new", **change}]))
                self.assertEqual(self.submit(body), 1)
                self.assertEqual((self.po / "map.po").read_bytes(), self.original)

    def test_valid_submission_keeps_approved_texts_and_marks_new_ones_translated(self):
        entries = [PoEntry("s0000100", "Fit.", "Old text"),
                   PoEntry("s0000200", "Other.", "Kept text", translator=["source: a game"]),
                   PoEntry("s0000300", "Blank.", "", flags=["empty"]),
                   PoEntry("s0000400", "Open.", ""),
                   PoEntry("s0000500", "Draft.", "Draft text", flags=["fuzzy"])]
        write(self.po / "map.po", entries, self.header)
        self.facts(entries)
        body = form(self.export([{"id": "s0000100", "base": "Old text", "translation": "New text"},
                                 {"id": "s0000200", "translation": "Kept text", "note": "another note"},
                                 {"id": "s0000300", "base": "", "translation": "Filled"},
                                 {"id": "s0000400", "base": "", "translation": "First text"},
                                 {"id": "s0000500", "base": "Draft text", "translation": "Next draft"}]))
        self.assertEqual(self.submit(body), 0)
        _, entries = read(self.po / "map.po")
        by_id = {entry.msgctxt: entry for entry in entries}
        self.assertEqual((by_id["s0000100"].msgstr, by_id["s0000100"].flags), ("New text", []))
        self.assertEqual((by_id["s0000200"].msgstr, by_id["s0000200"].flags), ("Kept text", []))
        self.assertEqual(by_id["s0000200"].source_note, "a game")
        self.assertEqual((by_id["s0000300"].msgstr, by_id["s0000300"].flags), ("Filled", []))
        self.assertEqual((by_id["s0000400"].msgstr, by_id["s0000400"].flags), ("First text", ["fuzzy"]))
        self.assertEqual((by_id["s0000500"].msgstr, by_id["s0000500"].flags), ("Next draft", ["fuzzy"]))
        self.assertEqual(self.answer("status"), "valid\n")
        self.assertEqual(self.answer("issue"), "5\n")
        self.assertEqual((self.answer("profile"), self.answer("language")), ("test\n", "xx\n"))
        self.assertEqual(self.answer("files"), "translation/po/map.po\n")
        self.assertEqual(self.answer("summary"), "4 changed entries of test/xx\n")
        self.assertEqual(self.answer("title"), "Editor submission #5: test/xx\n")
        self.assertTrue(self.answer("commit.txt").startswith("fix(translation): apply editor submission (#5)\n"))
        self.assertNotIn("-by:", self.answer("commit.txt"))
        pr = self.answer("pr.md")
        self.assertIn("in #5.", pr)
        self.assertIn("Resolves #5", pr)
        self.assertIn("- Changed entries: 4; new translations are translated, approved texts stay approved\n", pr)
        self.assertNotIn("fuzzy", pr)
        self.assertIn("- Unchanged entries in the export: 1", pr)
        self.assertIn("- Source notes that differ from the catalog: 1, kept as in the catalog", pr)
        self.assertEqual(json.loads((self.out / "export.json").read_text())["entries"][0]["id"], "s0000100")

    def test_entry_note_snapshots_never_change_canonical_notes_or_create_text_edits(self):
        from gen3tl.catalog import entrynotes
        from gen3tl.core import notes
        note_path = entrynotes.path(self.po)
        note = {"id": "note:entry", "subject": {"kind": "entry", "id": "s0000100"},
                "scope": {"kind": "language", "language": "xx"},
                "binding": {"kind": "entry", "id": "s0000100", "source_sha256": digest("Fit.")},
                "guidance": ["Current wording guidance."], "status": "unreviewed"}
        notes.write_notes(note_path, {"schema": notes.SCHEMA, "note": [note]})
        before = note_path.read_bytes()
        for context in ({}, {"note": "Historical context"}):
            with self.subTest(context=context):
                entry = {"id": "s0000100", "base": "Old text", "translation": "Old text", **context}
                self.assertEqual(self.submit(form(self.export([entry]))), 1)
                self.assertIn("nothing to submit", self.answer("comment.md"))
                self.assertEqual((self.po / "map.po").read_bytes(), self.original)
                self.assertEqual(note_path.read_bytes(), before)
                self.assertEqual(json.loads((self.out / "export.json").read_text())["entries"], [entry])
                entry["translation"] = "New text"
                self.assertEqual(self.submit(form(self.export([entry]))), 0)
                self.assertEqual(note_path.read_bytes(), before)
                self.assertIn("Current wording guidance.", read(self.po / "map.po")[1][0].source_note)
                (self.po / "map.po").write_bytes(self.original)

    def test_check_mode_and_attachment_leave_the_catalog(self):
        export = self.export([{"id": "s0000100", "translation": "New text"}]).encode()
        url = "https://github.com/user-attachments/files/1/export.json"
        fetched = []
        status = self.submit(form(attachment=f"[export.json]({url})"), "--check",
                             fetch=lambda link: fetched.append(link) or export)
        self.assertEqual(status, 0)
        self.assertEqual(fetched, [url])
        self.assertEqual((self.po / "map.po").read_bytes(), self.original)
        self.assertEqual(self.answer("files"), "translation/po/map.po\n")

    def test_submit_page_documents_are_accepted(self):
        entries = [{"id": "s0000100", "base": "Old text", "translation": "New text"}]
        for extra in ({"notes": "The box clipped."}, {"notes": "The box clipped.", "pictures": [], "terms": []}):
            with self.subTest(keys=sorted(extra)):
                self.assertEqual(self.submit(form(self.export(entries, **extra)), "--check"), 0)
                self.assertEqual(self.answer("status"), "valid\n")
                self.assertEqual((self.po / "map.po").read_bytes(), self.original)

    def test_notes_follow_the_resolves_line(self):
        entries = [{"id": "s0000100", "base": "Old text", "translation": "New text"}]
        notes = "The box clipped.\r\nResolves #9, ```see``` @someone"
        self.assertEqual(self.submit(form(self.export(entries, notes=f"  {notes}\n")), "--check"), 0)
        section = ("Notes for the reviewers:\n\n````text\n"
                   "The box clipped.\nResolves #9, ```see``` @someone\n````\n")
        self.assertTrue(self.answer("pr.md").endswith(f"\n\nResolves #5\n\n{section}"))
        self.assertEqual(self.answer("notes.md"), section)
        for extra in ({}, {"notes": " \r\n "}, {"notes": None}):
            with self.subTest(notes=extra):
                self.assertEqual(self.submit(form(self.export(entries, **extra)), "--check"), 0)
                self.assertTrue(self.answer("pr.md").endswith("\n\nResolves #5\n"))
                self.assertNotIn("Notes for the reviewers", self.answer("pr.md"))
                self.assertEqual(self.answer("notes.md"), "")

    def test_invalid_submissions_are_answered_with_the_reason(self):
        cases = (
            (form(), "no editor export found"),
            (form("{not json"), "not valid JSON"),
            (form("[1]"), "must be a JSON object"),
            (form(self.export([{"id": "s0000100", "translation": "x"}]).replace('"xx"', '"yy"')),
             "test/yy is not a translation project"),
            (form(self.export([{"id": "s0000100", "translation": "x"}], pictures=[{"id": "G01"}])),
             "G01: areas must be a list of [x, y, w, h] integers"),
            (form(self.export([{"id": "s0000100", "translation": "x"}], keyboard={"rows": {}, "base": {"rows": {}}})),
             "holds keyboard; glyph and keyboard edits belong to the toolkit repository (haritos90/gen3tl), "
             "whose submission workflow imports the JSON file"),
            (form(self.export([{"id": "s0000100", "translation": "x"}], glyphs=[{"style": "main", "char": "Б"}])),
             "holds glyphs; glyph and keyboard edits belong to the toolkit repository (haritos90/gen3tl), "
             "whose submission workflow imports the JSON file"),
            (form(self.export([{"id": "s9999999", "translation": "x"}])), "s9999999: not in the selected catalog"),
            (form(self.export([{"id": "s0000100", "base": "Stale", "translation": "x"}])),
             "s0000100: editor base differs from the current catalog"),
            (form(self.export([{"id": "s0000100", "translation": "a\u0007b"}])), "unsupported control character"),
            (form(self.export([{"id": "s0000100", "translation": "Old text"}])), "nothing to submit"),
            (form(self.export([])), "nonempty entries list"),
            (form(self.export([{"id": "s0000100", "translation": "x"}], notes=["x"])), "notes must be a string"),
        )
        for body, reason in cases:
            with self.subTest(reason=reason):
                self.assertEqual(self.submit(body), 1)
                self.assertEqual(self.answer("status"), "invalid\n")
                comment = self.answer("comment.md")
                self.assertTrue(comment.startswith(submission.MARKER))
                self.assertIn(reason, comment)
                self.assertIn("CONTRIBUTING.md", comment)
                self.assertEqual((self.po / "map.po").read_bytes(), self.original)

    # a glossary term and the lines using it
    GLOSSARY = ("id\tcategory\tsource_text\ttranslation\tlabel\tgender\tdeclension\tforms\tsources\tvariants\t"
                "confidence\taddress\tnotes\tstatus\n"
                "term:gym\tterm\tGym\tGim\t\t\tfixed\t\t\t\t\t\t\tapproved\n"
                "item:ether\titem\tEther\tEfir\t\t\tfixed\t\t\t\t\t\t\tapproved\n")
    GYM = {"id": "term:gym", "source_text": "Gym", "base": {"translation": "Gim"}, "changes": {"translation": "Stadium"}}

    def glossary(self):
        """The glossary, a catalog file using term:gym, and a key-only catalog."""
        path = self.root / "translation/glossary.tsv"
        path.write_text(self.GLOSSARY, encoding="utf-8")
        entries = [PoEntry("s0000400", "The Gym is open.", "Gim otkryt."), PoEntry("s0000500", "Come in!", "V Gim!"),
                   PoEntry("s0000600", "Gym leaders wait.", "")]
        write(self.po / "gym.po", entries, self.header)
        self.term_index(path, self.po / "gym.po", entries)
        return path, path.read_bytes(), (self.po / "gym.po").read_bytes()

    def term_index(self, glossary, po, entries):
        """Beside a key-only catalog file, the facts its English gave: digests and term index."""
        if not self.HEADER:
            return
        from gen3tl.lang import language, termbase
        self.facts(entries)
        with language.use("xx"):
            rows = termbase.build_index(termbase.load(glossary), {e.msgctxt: (po, e) for e in entries})
        termbase.save_index(self.root / "translation/term-index.tsv", rows)

    def test_term_edits_rename_their_uses_and_are_listed(self):
        glossary, _, _ = self.glossary()
        body = form(self.export([{"id": "s0000100", "base": "Old text", "translation": "New text"}],
                                terms=[{**self.GYM, "include": ["s0000500"]},
                                       {"id": "item:ether", "base": {"notes": ""}, "changes": {"notes": "an item"}}]))
        self.assertEqual(self.submit(body), 0)
        from gen3tl.lang import language, termbase
        with language.use("xx"):
            rows = {t.id: t for t in termbase.load(glossary)}
        self.assertEqual((rows["term:gym"].translation, rows["item:ether"].notes), ("Stadium", "an item"))
        self.assertEqual(rows["term:gym"].variants, "Gim")
        _, entries = read(self.po / "gym.po")
        by_id = {entry.msgctxt: entry for entry in entries}
        self.assertEqual([(e.msgstr, e.flags) for e in by_id.values()],
                         [("Stadium otkryt.", []), ("V Stadium!", []), ("", [])])
        self.assertEqual(by_id["s0000400"].translator, [])
        self.assertEqual(by_id["s0000500"].translator, [])
        _, entries = read(self.po / "map.po")
        self.assertEqual(entries[0].msgstr, "New text")
        self.assertEqual(self.answer("files"),
                         "translation/po/gym.po\ntranslation/po/map.po\ntranslation/glossary.tsv\n"
                         "translation/term-index.tsv\n")
        self.assertEqual(self.index_rows(), [("term:gym", "s0000400", "Stadium"),
                                             ("term:gym", "s0000500", "Stadium")])
        self.assertEqual(self.answer("summary"), "1 changed entry and 2 term edits of test/xx\n")
        self.assertTrue(self.answer("commit.txt").endswith(
            "\n- Project: test/xx\n- Entries: 1\n- Terms: 2\n"))
        pr = self.answer("pr.md")
        for part in ("- Changed entries: 1; new translations are translated, approved texts stay approved\n",
                     "- Term edits: 2, applied to `translation/glossary.tsv`",
                     "- Lines the term edits rename: 2\n",
                     "Term edits:\n\n- `term:gym` (Gym): translation `Gim` → `Stadium`; renamed lines: 2\n"
                     "- `item:ether` (Ether): notes; renamed lines: 0\n"):
            self.assertIn(part, pr)

    def test_term_renames_keep_the_state_of_their_lines(self):
        glossary, _, _ = self.glossary()
        entries = [PoEntry("s0000400", "The Gym is open.", "Gim otkryt."),
                   PoEntry("s0000500", "Come in!", "V Gim!", flags=["fuzzy"]),
                   PoEntry("s0000600", "Gym leaders wait.", "")]
        write(self.po / "gym.po", entries, self.header)
        self.term_index(glossary, self.po / "gym.po", entries)
        self.assertEqual(self.submit(form(self.export([], terms=[{**self.GYM, "include": ["s0000500"]}]))), 0)
        self.assertEqual([(e.msgstr, e.flags) for e in read(self.po / "gym.po")[1]],
                         [("Stadium otkryt.", []), ("V Stadium!", ["fuzzy"]), ("", [])])
        self.assertIn("- Lines the term edits rename: 2\n", self.answer("pr.md"))

    def test_term_edits_alone_and_checked(self):
        glossary, before, lines = self.glossary()
        index = self.root / "translation/term-index.tsv"
        indexed = index.read_bytes() if index.exists() else None
        self.assertEqual(self.submit(form(self.export([], terms=[self.GYM])), "--check"), 0)
        self.assertEqual((glossary.read_bytes(), (self.po / "gym.po").read_bytes()), (before, lines))
        self.assertEqual(self.answer("files"), "translation/po/gym.po\ntranslation/glossary.tsv\n"
                         "translation/term-index.tsv\n")
        self.assertEqual(index.read_bytes() if index.exists() else None, indexed)
        self.assertEqual(self.answer("summary"), "1 term edit of test/xx\n")
        pr = self.answer("pr.md")
        self.assertNotIn("Changed entries", pr)
        self.assertIn("- `term:gym` (Gym): translation `Gim` → `Stadium`; renamed lines: 1\n", pr)

    def index_rows(self):
        with (self.root / "translation/term-index.tsv").open() as stream:
            return [(r["term"], r["string"], r["text"]) for r in csv.DictReader(stream, delimiter="\t")]

    def test_text_submission_refreshes_the_index_without_term_edits(self):
        self.glossary()
        index = self.root / "translation/term-index.tsv"
        index.write_text("stale\n")
        body = form(self.export([{"id": "s0000400", "base": "Gim otkryt.", "translation": "Closed."}]))
        self.assertEqual(self.submit(body, "--check"), 0)
        self.assertEqual(index.read_text(), "stale\n")
        self.assertEqual(self.submit(body), 0)
        self.assertEqual(self.index_rows(), [("term:gym", "s0000500", "Gim")])
        self.assertEqual(self.answer("index"), "translation/term-index.tsv\n")
        self.assertIn("translation/term-index.tsv\n", self.answer("files"))

    def test_the_pr_body_states_no_unknown_rows(self):
        self.glossary()
        body = form(self.export([{"id": "s0000400", "base": "Gim otkryt.", "translation": "Closed."}]))
        self.assertEqual(self.submit(body), 0)
        self.assertIn("Unknown term index rows: none.", self.answer("pr.md"))

    def test_glossary_only_submission_refreshes_the_index(self):
        self.glossary()
        (self.root / "translation/term-index.tsv").write_text("stale\n")
        record = {"id": "term:gym", "base": {"notes": ""}, "changes": {"notes": "Verified spelling"}}
        before = (self.po / "gym.po").read_bytes()
        self.assertEqual(self.submit(form(self.export([], terms=[record]))), 0)
        self.assertEqual((self.po / "gym.po").read_bytes(), before)
        self.assertEqual(self.index_rows(), [("term:gym", "s0000400", "Gim"),
                                             ("term:gym", "s0000500", "Gim")])


    def test_term_submission_preserves_enclosing_names(self):
        from gen3tl.lang import language, termbase
        glossary = self.root / "translation/glossary.tsv"
        with language.use("xx"):
            termbase.save(glossary, [termbase.Term("item:elixir", "item", "Elixir", "Elixir"),
                                    termbase.Term("item:max-elixir", "item", "Max Elixir", "Elixir max")])
        entries = [PoEntry("s0000400", "Elixir", "Elixir"), PoEntry("s0000500", "Max Elixir", "Elixir max"),
                   PoEntry("s0000600", "Elixir and Max Elixir", "Elixir and Elixir max")]
        write(self.po / "items.po", entries, self.header)
        self.term_index(glossary, self.po / "items.po", entries)
        record = {"id": "item:elixir", "base": {"translation": "Elixir"},
                  "changes": {"translation": "Nectar"}, "include": ["s0000500", "s0000600"]}
        self.assertEqual(self.submit(form(self.export([], terms=[record]))), 0)
        self.assertEqual([(e.msgstr, e.fuzzy) for e in read(self.po / "items.po")[1]],
                         [("Nectar", False), ("Elixir max", False), ("Nectar and Elixir max", False)])
        with language.use("xx"):
            self.assertEqual([t.translation for t in termbase.load(glossary)], ["Nectar", "Elixir max"])

    def test_invalid_term_edits_are_answered_with_the_reason(self):
        gym = self.GYM
        cases = (
            ({**gym, "base": {"translation": "Gym"}},
             "term:gym: translation is 'Gim' in the glossary, the edit was made on 'Gym'"),
            ({**gym, "id": "term:nope"}, "term:nope: no such term in the glossary"),
            ({**gym, "base": {"source_text": "Gym"}, "changes": {"source_text": "Gyms"}}, "term:gym: source_text cannot be edited"),
            ({**gym, "base": {}}, "term:gym: translation has no base value"),
            ({**gym, "changes": ["Stadium"]}, "term:gym: changes must be an object"),
            ({**gym, "changes": {"forms": {"nomn.sing": "a|b"}}, "base": {"forms": {}}},
             "term:gym: unsupported character in forms"),
            ({**gym, "changes": {"translation": "Sta\tdium"}}, "term:gym: unsupported character in translation"),
            ({**gym, "include": ["s9999999"]}, "term:gym: include s9999999 must be an entry of the selected catalog"),
            ({**gym, "base": {"notes": ""}, "changes": {"notes": ""}},
             "every change equals the current catalog, glossary and policy; nothing to submit"),
            ([gym, gym], "duplicate term edit: term:gym"),
        )
        glossary, before, lines = self.glossary()
        for record, reason in cases:
            with self.subTest(reason=reason):
                terms = record if isinstance(record, list) else [record]
                self.assertEqual(self.submit(form(self.export([], terms=terms))), 1)
                self.assertEqual(self.answer("status"), "invalid\n")
                self.assertIn(f"not applied: {reason}.", self.answer("comment.md"))
                self.assertEqual((glossary.read_bytes(), (self.po / "gym.po").read_bytes()), (before, lines))
        glossary.unlink()
        self.assertEqual(self.submit(form(self.export([], terms=[gym]))), 1)
        self.assertIn("test/xx has no glossary; its submissions carry no term edits", self.answer("comment.md"))

    def graphics(self):
        """The profile with the graphics tests' ROM graphics, for gfx-check."""
        from gen3tl.build.graphics import GraphicsConfig
        from graphics_support import fixture as graphics_rom
        rom = graphics_rom(self.root)[0]   # writes inv.tsv
        rom.save(self.root / "base.gba")
        data = {**self.profile.data, "rom": {"sha1": rom.sha1(), "path": "base.gba"},
                "graphics": {"inventory": "inv.tsv", "sheet_width": {"T1": 4}}}
        self.profile = Profile(data, self.root / "test.toml")
        self.profile.path.write_text("\n".join(f"{k} = {literal(v)}" for k, v in data.items()), encoding="utf-8")
        write_policy(self.root, self.profile, language="xx")
        cfg = GraphicsConfig(data["graphics"], self.root)
        return cfg.sheet(rom, cfg.graphic("T1"))

    def picture(self, sheet, areas=([8, 8, 16, 8],), **record):
        """A picture of T1 as the editor exports it: its text area redrawn, in area form."""
        from gen3tl.build.graphics import area_form_png
        px = [row[:] for row in sheet.pixels]
        for x in range(8, 24):
            px[8 + x % 8][x] = 2 if x % 3 else 3
        png = area_form_png(sheet, px, [tuple(a) for a in areas])
        return png, {"id": "T1", "base": None, "areas": [list(a) for a in areas], "tiles": None,
                     "sha256": hashlib.sha256(png).hexdigest(), **record}

    def test_a_picture_becomes_a_graphics_edit_that_gfx_check_passes(self):
        import tomllib

        from gen3tl.build.graphics import apply_area_form
        from gen3tl.check.render import png_image, write_indexed_png
        sheet = self.graphics()
        png, record = self.picture(sheet)
        policy = (self.root / "policy.toml").read_bytes()
        body = form(self.export([], pictures=[record]), pictures=f"![T1]({asset(1)})")
        served = lambda url, *args: png   # noqa: E731
        self.assertEqual(self.submit(body, "--check", fetch=served), 0)
        self.assertEqual(((self.root / "policy.toml").read_bytes(), (self.root / "translation/graphics").exists()),
                         (policy, False))
        self.assertEqual(self.submit(body, fetch=served), 0)
        written = self.root / "translation/graphics/areas/T1.png"
        self.assertEqual(written.read_bytes(), png)
        edits = tomllib.loads((self.root / "policy.toml").read_text(encoding="utf-8"))["graphics"]["edit"]
        self.assertEqual(edits, [{"id": "T1", "png": "translation/graphics/areas/T1.png", "form": "areas",
                                  "areas": [[8, 8, 16, 8]]}])
        self.assertTrue((self.root / "policy.toml").read_bytes().startswith(policy))
        self.assertEqual(self.answer("files"), "policy.toml\ntranslation/graphics/areas/T1.png\n")
        self.assertEqual(self.answer("index"), "\n")
        self.assertEqual(self.answer("summary"), "1 picture of test/xx\n")
        self.assertTrue(self.answer("commit.txt").endswith(
            "- Entries: 0\n- Pictures: 1\n"))
        pr = self.answer("pr.md")
        for part in ("- Pictures: 1, `[[graphics.edit]]` entries of `policy.toml` with their area forms",
                     "Pictures:\n\n- `T1` (LABEL): new edit, 1 area\n", "`build gfx-check` and the build"):
            self.assertIn(part, pr)
        # Validate the reconstructed sheet against the ROM.
        laid, findings = apply_area_form(sheet, png_image(written.read_bytes()), [(8, 8, 16, 8)])
        self.assertEqual(findings, [])
        write_indexed_png(self.root / "edited.png", laid.indices, sheet.palette, 4)
        with patch.object(Profile, "by_name", return_value=self.profile), \
                contextlib.redirect_stdout(io.StringIO()) as log:
            status = cli.main(["--project-root", str(self.root), "build", "gfx-check", "T1", str(self.root / "edited.png"),
                               "--area", "8,8,16,8"])
        self.assertEqual(status, 0, log.getvalue())
        self.assertIn("verified     passed", log.getvalue())
        self.assertIn("findings     0 findings", log.getvalue())
        # the same picture changes nothing; inline needs no attachment
        record["base"] = hashlib.sha256(png).hexdigest()
        body = form(self.export([], pictures=[record]), pictures=f"![T1]({asset(1)})")
        self.assertEqual(self.submit(body, fetch=served), 1)
        self.assertIn("nothing to submit", self.answer("comment.md"))
        changed, again = self.picture(sheet, areas=([8, 8, 16, 8], [0, 0, 4, 4]))
        inline = {**again, "base": record["base"], "png": base64.b64encode(changed).decode(), "sha256": None}
        self.assertEqual(self.submit(form(self.export([], pictures=[inline]))), 0)
        self.assertIn("- `T1` (LABEL): replaces the project's edit, 2 areas\n", self.answer("pr.md"))
        self.assertEqual(written.read_bytes(), changed)

    def test_stale_pictures_refuse_every_change(self):
        from gen3tl.build.graphics import area_png
        sheet = self.graphics()
        png, record = self.picture(sheet)
        record["png"] = base64.b64encode(png).decode()
        self.assertEqual(self.submit(form(self.export([], pictures=[record]))), 0)
        written = self.root / "translation/graphics/areas/T1.png"
        newer = area_png([[0] * 32 for _ in range(32)], 4)
        written.write_bytes(newer)
        policy = (self.root / "policy.toml").read_bytes()
        self.glossary()
        index = self.root / "translation/term-index.tsv"
        index.write_text("unchanged\n")
        for base in (None, hashlib.sha256(png).hexdigest()):
            with self.subTest(base=base):
                body = form(self.export([{"id": "s0000100", "translation": "New text"}],
                                        pictures=[{**record, "base": base}], terms=[self.GYM]))
                self.assertEqual(self.submit(body), 1)
                self.assertIn("T1: editor base differs from the current project picture; stale picture",
                              self.answer("comment.md"))
                self.assertEqual(written.read_bytes(), newer)
                self.assertEqual((self.root / "policy.toml").read_bytes(), policy)
                self.assertEqual((self.po / "map.po").read_bytes(), self.original)
                self.assertEqual((self.root / "translation/glossary.tsv").read_text(), self.GLOSSARY)
                self.assertEqual(index.read_text(), "unchanged\n")
        with contextlib.redirect_stdout(io.StringIO()) as log:
            status = cli.main(["--project-root", str(self.root), "--profile", str(self.profile.path),
                               "web", "import", str(self.out / "export.json")])
        self.assertEqual(status, 1)
        self.assertIn(self.IMPORT_REFUSAL, log.getvalue())
        self.assertEqual(written.read_bytes(), newer)
        record["base"] = hashlib.sha256(newer).hexdigest()
        self.assertEqual(self.submit(form(self.export([], pictures=[record]))), 0)
        self.assertEqual(written.read_bytes(), png)
        self.assertEqual(index.read_text(), "unchanged\n")

    def test_picture_base_uses_the_browser_area_form(self):
        from gen3tl.check.render import write_indexed_png
        from gen3tl.web.editorgfx import graphics_data
        sheet = self.graphics()
        original = self.root / "sheet.png"
        write_indexed_png(original, sheet.pixels, sheet.palette, 4)
        edit = {"id": "T1", "png": "sheet.png", "areas": [[8, 8, 16, 8]]}
        policy = self.root / "policy.toml"
        policy.write_text(submission.policy_with(policy.read_text(), edit))
        graphics = graphics_data({**self.profile.data["graphics"], "edit": [edit]}, self.root)
        base = hashlib.sha256(base64.b64decode(graphics["edits"]["T1"]["png"])).hexdigest()
        self.assertNotEqual(base, hashlib.sha256(original.read_bytes()).hexdigest())
        png, record = self.picture(sheet, base=base)
        record["png"] = base64.b64encode(png).decode()
        status = self.submit(form(self.export([], pictures=[record])))
        self.assertEqual(status, 0, self.answer("comment.md") if status else "")
        self.assertEqual((self.root / "translation/graphics/areas/T1.png").read_bytes(), png)

    def test_invalid_pictures_are_answered_with_the_reason(self):
        from gen3tl.build.graphics import area_png
        sheet = self.graphics()
        png, record = self.picture(sheet)
        stray = [[0] * 32 for _ in range(32)]
        stray[0][0] = 3
        outside = area_png(stray, 4)
        small = area_png([[0] * 32 for _ in range(24)], 4)
        sha = lambda data: hashlib.sha256(data).hexdigest()   # noqa: E731
        one = f"![T1]({asset(1)})"
        cases = (
            ({k: v for k, v in record.items() if k != "base"}, one, "T1: picture base is missing"),
            ({**record, "base": "invalid"}, one, "T1: picture base must be a SHA-256"),
            ({**record, "base": "ab" * 32}, one, "T1: editor base differs from the current project picture"),
            (record, "", "T1: no attachment of the Pictures field has the SHA-256"),
            ({**record, "sha256": sha(b"x")}, one, "T1: no attachment of the Pictures field has the SHA-256"),
            (record, f"{one} ![T2]({asset(2)})", "the Pictures field holds 2 attachments for 1 attached pictures"),
            ({**record, "sha256": sha(outside)}, one,
             "T1: pixel 0,0: index 3 outside the text areas of the area form"),
            ({**record, "sha256": sha(small)}, one, "T1: the picture is not 32x32, the size of its area form"),
            ({**record, "sha256": sha(b"GIF89a")}, one, "T1: the picture is not a PNG file"),
            ({**record, "id": "T9"}, one, "T9 is not in the graphics inventory"),
            ({**record, "id": "T3"}, one, "T3 has no ROM source in the graphics inventory"),
            ({**record, "tiles": 12}, one, "T1: 12 tiles is below the original 16"),
            ({**record, "tiles": 20}, one, "the policy refuses the pictures: graphics.edit.T1: exceeds verified ROM"),
            ({**record, "areas": [[8, 8, 16]]}, one, "T1: areas must be a list of [x, y, w, h] integers"),
            ({**record, "sha256": "AB" * 32}, one, "T1: sha256 must be 64 lowercase hexadecimal digits"),
            ({**record, "sha256": None}, one, "T1: the picture needs its sha256 and an attachment, or its png"),
            ({**record, "sha256": None, "png": "@@"}, "", "T1: png is not base64"),
            ([record, record], one, "duplicate picture: T1"),
        )
        for pictures, field, reason in cases:
            with self.subTest(reason=reason):
                served = {asset(1): {sha(x): x for x in (png, outside, small, b"GIF89a")}.get(
                    (pictures[0] if isinstance(pictures, list) else pictures).get("sha256"), png), asset(2): png}
                body = form(self.export([], pictures=pictures if isinstance(pictures, list) else [pictures]),
                            pictures=field)
                self.assertEqual(self.submit(body, fetch=lambda url, *args: served[url]), 1)
                self.assertIn(reason, self.answer("comment.md"))
                self.assertFalse((self.root / "translation/graphics").exists())
        self.assertNotIn("graphics", (self.root / "policy.toml").read_text(encoding="utf-8"))

    def site_catalog(self, directory):
        """The site catalog xx under `directory`, the catalogs the submission sees."""
        from gen3tl.web import sitecatalog
        path = directory / "xx.po"
        write(path, [PoEntry("site:Patch", "Patch", "Патч", extracted=["index.html"]),
                     PoEntry("site:Loading {game}…", "Loading {game}…", "", extracted=["js/pick.js"]),
                     PoEntry("site:Home", "Home", "Дом", extracted=["index.html"])],
              {"Project-Id-Version": "site", "Language": "xx", "Content-Type": "text/plain; charset=UTF-8"})
        hook = patch.object(sitecatalog, "CATALOGS", directory)
        hook.start()
        self.addCleanup(hook.stop)
        return path

    def test_a_site_submission_writes_the_site_catalog_and_names_it(self):
        site = self.site_catalog(self.root / "web/i18n")
        before = site.read_bytes()
        export = SITE_EXPORT.read_text(encoding="utf-8")
        noted = json.dumps({**json.loads(export), "notes": "Короче."}, ensure_ascii=False)
        self.assertEqual(self.submit(form(noted), "--check"), 0)
        self.assertEqual(site.read_bytes(), before)
        self.assertTrue(self.answer("pr.md").endswith("Resolves #5\n\nNotes for the reviewers:\n\n```text\nКороче.\n```\n"))
        self.assertEqual(self.submit(form(export)), 0)
        _, entries = read(site)
        self.assertEqual([(e.msgid, e.msgstr, e.flags) for e in entries],
                         [("Patch", "Патч", []), ("Loading {game}…", "Загрузка {game}…", ["fuzzy"]),
                          ("Home", "Главная", [])])
        self.assertEqual((self.po / "map.po").read_bytes(), self.original)
        self.assertEqual((self.answer("status"), self.answer("profile"), self.answer("language")),
                         ("valid\n", "site\n", "xx\n"))
        self.assertEqual(self.answer("files"), "web/i18n/xx.po\n")
        self.assertEqual(self.answer("summary"), "2 changed entries of the site catalog web/i18n/xx.po\n")
        self.assertEqual(self.answer("title"), "Editor submission #5: web/i18n/xx.po\n")
        self.assertEqual(self.answer("commit.txt"), "fix(site): apply editor submission (#5)\n\n"
                                                    "- Site catalog: web/i18n/xx.po\n- Entries: 2\n")
        self.assertEqual(self.answer("notes.md"), "")
        pr = self.answer("pr.md")
        for part in ("Site interface texts submitted with the editor in #5.", "- Site catalog: `web/i18n/xx.po`",
                     "- Changed entries: 2; new translations are translated, approved texts stay approved\n",
                     "- Unchanged entries in the export: 1",
                     "- Source notes that differ from the catalog: 1, kept as in the catalog",
                     "the site catalog check renders the catalog", "Resolves #5"):
            self.assertIn(part, pr)
        self.assertNotIn("test/xx", pr)

    def test_site_submissions_are_refused_with_the_reason(self):
        fixture = json.loads(SITE_EXPORT.read_text(encoding="utf-8"))
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        inside = self.root / "web/i18n"
        cases = (
            (inside, {**fixture, "entries": [{"id": "site:Patch", "translation": "Патч"}]}, "nothing to submit"),
            (inside, {**fixture, "entries": [{"id": "site:Loading {game}…", "translation": "Ждите"}]},
             "site:Loading {game}…: the translation must keep the holes of its key ({game})"),
            (inside, {**fixture, "language": "yy"}, "no site catalog for 'yy'"),
            (inside, {**fixture, "terms": [{"id": "t"}]}, "holds terms; a site submission carries text entries only"),
            (Path(outside.name), fixture, "the site catalog xx.po is not part of this repository"),
        )
        for directory, doc, reason in cases:
            with self.subTest(reason=reason):
                site = self.site_catalog(directory)
                before = site.read_bytes()
                self.assertEqual(self.submit(form(json.dumps(doc, ensure_ascii=False))), 1)
                self.assertEqual(self.answer("status"), "invalid\n")
                self.assertIn(reason, self.answer("comment.md"))
                self.assertEqual(site.read_bytes(), before)

    def test_event_without_issue_is_an_error(self):
        event = self.root / "event.json"
        event.write_text(json.dumps({"action": "opened"}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "names no issue"):
            submission.run(event, self.out, None)
