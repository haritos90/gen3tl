"""Acceptance validates proposals and refreshes their derived views together."""

import contextlib
import io
import json
import subprocess
import unittest
from unittest.mock import patch

import notechanges_support as fixtures
import test_editorsnapshot as snapshots
from project_fixture import literal, write_policy
from gen3tl.web import snapshotstore as snapshot_store
from gen3tl.catalog.catalog import load_catalog
from gen3tl.catalog.po import KEY_ONLY, SOURCE_FIELD, read, write
from gen3tl.catalog.source import digest
from gen3tl.check import noteaccept
from gen3tl.cli.main import main
from gen3tl.core import notes
from gen3tl.profile.profile import Profile
from gen3tl.project import Registry
from gen3tl.project.manifest import Manifest
from gen3tl.web import notechanges


class AcceptanceTest(unittest.TestCase):
    setUp = fixtures.NoteSubmissionTest.setUp
    export = fixtures.NoteSubmissionTest.export
    apply = fixtures.NoteSubmissionTest.apply
    stored = fixtures.NoteSubmissionTest.stored
    containers = fixtures.NoteSubmissionTest.containers
    note_for = fixtures.NoteSubmissionTest.note_for
    legacy_proposal = fixtures.NoteSubmissionTest.legacy_proposal

    def prepare(self, ids=None, **kwargs):
        with self.project.activate(self.prof):
            return noteaccept.prepare(self.project, self.prof, ids or [], reviewer="reviewer",
                                      date="2026-01-03", **kwargs)

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--project-root", str(self.root), "--profile", str(self.raw.path),
                         "check", "accept-notes", *args])
        return code, out.getvalue() + err.getvalue()

    def files(self):
        return {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()
                and ".git" not in path.relative_to(self.root).parts}

    def submit(self, records=None):
        self.export([fixtures.change(record) for record in (records or [self.note])])
        self.apply()

    def test_input_replaces_or_retires_legacy_notes_and_reports_both(self):
        from gen3tl.catalog import guidance

        for operation in ("edit", "remove"):
            with self.subTest(operation=operation):
                proposal = self.legacy_proposal(operation)
                self.export([proposal], note_changes_schema=notechanges.LEGACY_SCHEMA)
                before = self.files()
                args = ["--input", str(self.path), "--reviewer", "reviewer", "--date", "2026-01-03"]
                code, output = self.cli(*args, "--dry-run")
                self.assertEqual(code, 0, output)
                self.assertEqual(self.files(), before)
                for label in ("legacy base", "removed", "replacement", proposal["id"]):
                    self.assertIn(label, output)
                with patch("gen3tl.cli.web.editor_data", return_value=1):
                    code, output = self.cli(*args)
                self.assertNotEqual(code, 0, output)
                self.assertEqual(self.files(), before)

                def refresh(options):
                    from pathlib import Path
                    out = Path(options.out)
                    out.mkdir(parents=True)
                    (out / "index.json").write_text("{}")
                    return 0

                with patch("gen3tl.cli.web.editor_data", side_effect=refresh):
                    code, output = self.cli(*args)
                self.assertEqual(code, 0, output)
                rows = guidance.read_notes(self.project.notes)
                if operation == "remove":
                    self.assertEqual(rows, [])
                else:
                    expected = {**proposal["proposed"], "status": "accepted", "reviewed_by": "reviewer",
                                "reviewed_at": "2026-01-03"}
                    self.assertEqual(rows, [expected])
                self.assertEqual((self.po.read_bytes(), self.project.policy.read_bytes()), self.original)

    def test_input_refuses_invalid_or_mixed_changes_without_writing(self):
        proposal = self.legacy_proposal()
        invalid = fixtures.deepcopy(proposal)
        del invalid["proposed"]["sources"]
        for changes, entries in (([invalid], []), ([proposal], [{"id": "key", "base": "Old", "translation": "New"}])):
            self.export(changes, entries, note_changes_schema=notechanges.LEGACY_SCHEMA)
            before = self.files()
            code, output = self.cli("--input", str(self.path), "--reviewer", "reviewer", "--date", "2026-01-03")
            self.assertNotEqual(code, 0, output)
            self.assertEqual(self.files(), before)
        with self.assertRaisesRegex(ValueError, "exclusively"):
            self.prepare(["note:example"], export=self.path)

    def test_dry_run_reports_review_and_sources_without_writing(self):
        term = self.containers()[0]
        self.submit([self.note, term])
        before = self.files()
        code, output = self.cli(self.note["id"], term["id"], "--reviewer", "reviewer", "--date", "2026-01-03",
                                "--checked-source", term["id"], "1", "--dry-run")
        self.assertEqual(code, 0, output)
        for text in ("reviewer", "2026-01-03", "ref:one", "unresolved", "checked", "comments", "editor data",
                     "dry-run", self.note["id"], term["id"]):
            self.assertIn(text, output)
        self.assertEqual(self.files(), before)

    def test_all_contributed_containers_and_selected_citations_are_accepted(self):
        records = [self.note, *self.containers()]
        term = records[1]
        term["sources"].append({**term["sources"][0], "name": "Other", "address": "ref:two"})
        self.submit(records)
        accepted = self.prepare([row["id"] for row in records], checked=[(term["id"], "1")])
        with self.project.activate(self.prof):
            accepted.write(self.project, self.prof, lambda peer, stage: [])
            _, current = notechanges.inventory(self.project, self.prof,
                                               load_catalog(self.project.catalog, english=False))
        for row in records:
            stored = current[row["id"]]
            self.assertEqual((stored["status"], stored["reviewed_by"], stored["reviewed_at"]),
                             ("accepted", "reviewer", "2026-01-03"))
            self.assertEqual(stored["author"], "contributor")
            self.assertEqual(stored["created"], "2026-01-02T03:04:05Z")
        self.assertEqual([source["verification"] for source in current[term["id"]]["sources"]],
                         ["checked", "unresolved"])
        self.assertIn("# source: Preserve the context.", self.po.read_text())
        self.assertIn('msgid "key"', self.po.read_text())
        self.assertNotIn("Example.", self.po.read_text())
        self.assertIn("source: Historical reference", self.po.read_text())

    def test_invalid_selection_and_citations_leave_all_inputs_unchanged(self):
        term = self.containers()[0]
        self.submit([self.note, term])
        before = self.files()
        cases = [([], {}), ([self.note["id"]], {"base": "HEAD"}), (["missing"], {}),
                 ([self.note["id"]] * 2, {}), ([term["id"]], {}),
                 ([term["id"]], {"checked": [(term["id"], "0")]}),
                 ([term["id"]], {"checked": [(term["id"], "2")]}),
                 ([term["id"]], {"checked": [(term["id"], "invalid")]}),
                 ([self.note["id"]], {"checked": [(term["id"], "1")]}),
                 ([term["id"]], {"checked": [(term["id"], "1")] * 2})]
        for ids, options in cases:
            with self.subTest(ids=ids, options=options), self.assertRaises(ValueError):
                self.prepare(ids, **options)
            self.assertEqual(self.files(), before)
        for reviewer, date in (("", "2026-01-03"), ("reviewer", "2026-02-30"), ("reviewer\x1b", "2026-01-03")):
            code, output = self.cli(self.note["id"], "--reviewer", reviewer, "--date", date)
            self.assertNotEqual(code, 0, output)
            self.assertIn("failed", output)
            self.assertEqual(self.files(), before)

    def test_stale_binding_unknown_subject_and_related_note_are_refused(self):
        self.submit()
        original = self.stored()[self.note["id"]]
        variants = [dict(original, binding={**original["binding"], "source_sha256": "0" * 64}),
                    dict(original, subject={"kind": "entry", "id": "missing"},
                         binding={**original["binding"], "id": "missing"}),
                    dict(original, related_notes=["missing"]), dict(original, status="accepted")]
        for record in variants:
            notes.write_notes(self.entries, {"schema": notes.SCHEMA, "note": [record]})
            before = self.files()
            with self.assertRaises(ValueError):
                self.prepare([self.note["id"]])
            self.assertEqual(self.files(), before)

    def test_base_selects_changed_proposals_and_refuses_invalid_revisions(self):
        self.submit()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        def git(*args):
            return subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)
        git("add", ".")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "fixture")
        unchanged = self.stored()[self.note["id"]]
        added = {**unchanged, "id": "note:added"}
        accepted = {**unchanged, "id": "note:accepted", "status": "accepted"}
        notes.write_notes(self.entries, {"schema": notes.SCHEMA, "note": [unchanged, added, accepted]})
        self.assertEqual([change["id"] for change in self.prepare(base="HEAD").changes], ["note:added"])
        updated = {**unchanged, "guidance": ["Changed guidance."]}
        notes.write_notes(self.entries, {"schema": notes.SCHEMA, "note": [updated, added, accepted]})
        self.assertEqual([change["id"] for change in self.prepare(base="HEAD").changes],
                         ["note:added", self.note["id"]])
        before = self.files()
        with self.assertRaisesRegex(ValueError, "Git read failed"):
            self.prepare(base="missing-ref")
        self.assertEqual(self.files(), before)
        notes.write_notes(self.entries, {"schema": notes.SCHEMA, "note": [unchanged]})
        code, output = self.cli("--base", "HEAD", "--reviewer", "reviewer", "--date", "2026-01-03")
        self.assertEqual(code, 0, output)
        self.assertIn("unchanged", output)

    def test_failed_refresh_restores_notes_and_catalogs(self):
        self.submit()
        before = self.files()
        accepted = self.prepare([self.note["id"]])
        def fail(peer, stage):
            self.assertEqual(self.stored()[self.note["id"]]["status"], "accepted")
            self.assertIn("# source: Preserve the context.", self.po.read_text())
            raise ValueError("refresh failed")
        with self.project.activate(self.prof), self.assertRaisesRegex(ValueError, "refresh failed"):
            accepted.write(self.project, self.prof, fail)
        self.assertEqual(self.files(), before)

    def test_shared_guidance_refreshes_each_registered_language(self):
        shared = self.containers()[-1]
        self.submit([shared])
        manifest = Manifest(self.root / "project.toml", self.raw.name, self.raw.sha1,
                            fixtures.__version__, ("xx", "yy"), "xx")
        manifest.path.write_text(manifest.text())
        (self.root / "policy-yy.toml").write_text(self.project.policy.read_text().replace('"xx"', '"yy"'))
        registry = self.root / "projects.toml"
        registry.write_text(registry.read_text() + '\n[[project]]\nprofile="sample"\nlanguage="yy"\n'
                            'root="."\npolicy="policy-yy.toml"\n')
        peer = Registry.load(self.root).projects[1]
        peer.catalog.mkdir(parents=True)
        (peer.catalog / self.po.name).write_bytes(self.po.read_bytes())
        snapshot_store.write(peer.snapshot, snapshot_store.load(self.project.snapshot))
        accepted = self.prepare([shared["id"]])
        refreshed = []
        def refresh(peer, stage):
            from gen3tl.catalog.guidance import entry_notes
            effective = peer.effective(self.raw)
            with peer.activate(effective):
                catalog = load_catalog(peer.catalog, english=False)
                guidance = entry_notes(effective, catalog, {"key": shared["binding"]["source_sha256"],
                                                           "legacy": fixtures.digest("Legacy.")})
                self.assertEqual(guidance["key"][0]["text"], "Preserve the context.")
            refreshed.append(peer.language)
            return []
        with self.project.activate(self.prof):
            accepted.write(self.project, self.prof, refresh)
        self.assertEqual(refreshed, ["xx", "yy"])

    def test_failed_publication_restores_previous_editor(self):
        self.submit()
        self.project.editor.mkdir(parents=True)
        (self.project.editor / "index.json").write_text("old")
        before = self.files()
        accepted = self.prepare([self.note["id"]])
        def refresh(peer, stage):
            stage.mkdir(parents=True)
            (stage / "index.json").write_text("new")
            return [(stage, peer.editor), (stage / "absent", peer.editor.parent / "other")]
        with self.project.activate(self.prof), self.assertRaises(OSError):
            accepted.write(self.project, self.prof, refresh)
        self.assertEqual(self.files(), before)

    def test_help_lists_every_option(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
            main(["check", "accept-notes", "--help"])
        self.assertEqual(stopped.exception.code, 0)
        for option in ("usage", "NOTE", "--base", "--input", "--reviewer", "--date", "--checked-source", "--dry-run"):
            self.assertIn(option, output.getvalue())


class EditorRefreshTest(unittest.TestCase):
    setUp = snapshots.SnapshotTest.setUp

    def test_command_refreshes_split_and_existing_single_data_without_rom(self):
        language = self.doc["language"]
        self.prof.path.write_text("\n".join(f"{key} = {literal(value)}" for key, value in self.prof.data.items()))
        write_policy(self.root, self.prof, language=language)
        (self.root / "projects.toml").write_text(
            f'[defaults]\nprofile="test"\nlanguage="{language}"\n[[project]]\nprofile="test"\n'
            f'language="{language}"\nroot="."\npolicy="policy.toml"\n')
        project = Registry.load(self.root).projects[0]
        project.release.write_text('version="1.2.3"\n')
        effective = project.effective(self.prof)
        document = snapshots.snapshot.extract(effective, language, self.rom, self.inv, self.ctx,
                                               self.reach, self.catalog, self.po)
        snapshots.snapshot.write(project.snapshot, document)
        sid, (po_path, entry) = next(iter(self.catalog.items()))
        record = {"id": "note:review", "subject": {"kind": "entry", "id": sid},
                  "scope": {"kind": "language", "language": language},
                  "binding": {"kind": "entry", "id": sid, "source_sha256": digest(entry.msgid)},
                  "status": "proposed", "guidance": ["Preserve the context."]}
        notes_path = project.translation / "notes" / "entries.toml"
        notes.write_notes(notes_path, {"schema": notes.SCHEMA, "note": [record]})
        for path in self.po.rglob("*.po"):
            header, entries = read(path)
            write(path, entries, {**header, SOURCE_FIELD: KEY_ONLY})
        single = project.build / "editor" / f"{language}.json"
        single.parent.mkdir(parents=True)
        single.write_text("old")
        output = io.StringIO()
        with patch.object(Profile, "load_rom", side_effect=AssertionError("unexpected ROM read")), \
                contextlib.redirect_stdout(output):
            code = main(["--project-root", str(self.root), "--profile", str(self.prof.path),
                         "check", "accept-notes", record["id"], "--reviewer", "reviewer", "--date", "2026-01-03"])
        self.assertEqual(code, 0, output.getvalue())
        self.assertIn("# source: Preserve the context.", po_path.read_text())
        single_doc = json.loads(single.read_text())
        row = next(row for row in single_doc["entries"] if row["id"] == sid)
        # Entries link note records instead of flattened copies.
        self.assertNotIn("note", row)
        self.assertIn(record["id"], row["note_ids"])
        self.assertEqual(single_doc["notes"]["note"][0]["status"], "accepted")
        self.assertEqual(single_doc["notes"]["note"][0]["guidance"], ["Preserve the context."])
        index = json.loads((project.editor / "index.json").read_text())
        self.assertEqual(index["notes"]["note"][0]["reviewed_by"], "reviewer")
        self.assertFalse(list(project.build.glob(".note-accept-*")))


if __name__ == "__main__":
    unittest.main()
