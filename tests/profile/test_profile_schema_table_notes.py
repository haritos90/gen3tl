from test_roots import TOOLKIT_ROOT
"""Profile schema table notes checks and fixtures."""

import sys
from pathlib import Path
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.cli.main import main
from gen3tl.core import notes
from gen3tl.profile import profile, schema, tsv
from gen3tl.profile.profile import Profile
from project_fixture import SAMPLE, literal
from profile_schema_support import HEADER, write_dir


class TableNotesTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "projects.toml").write_text("", encoding="utf-8")
        self.path = write_dir(self.root, "game", HEADER, None)
        self.overlay = self.path / "overlay"
        (self.overlay / "reach").mkdir(parents=True)
        source = profile.PROFILE_DIR / f"{SAMPLE}.toml"
        (self.overlay / "identity.toml").write_bytes(source.read_bytes())
        self.order = self.overlay / "order.txt"
        self.order.write_text("identity.toml\nreach/never-shown.tsv\treach.never_shown\n", encoding="utf-8")
        self.table = self.overlay / "reach/never-shown.tsv"
        self.sidecar = self.table.with_suffix(".notes.toml")
        self.rows = [{"ids": ["entry:2", "entry:1"]}, {"ids": ["entry:3"], "evidence": "Unused branch."}]
        self.fact_id = "reach.never_shown[ids=entry:2,entry:1]"
        self.note = self.make_note(self.fact_id, self.rows[0])
        self.write_rows()
        self.write_notes([self.note])

    def make_note(self, fact_id, value, identity="row-proof"):
        return {"id": identity, "subject": {"kind": "profile_fact", "id": fact_id},
                "scope": {"kind": "shared"}, "status": "unreviewed", "facts": ["No reachable reader."],
                "binding": {"kind": "profile_fact", "id": fact_id,
                            "value_sha256": schema.value_digest(value), "field": "evidence"}}

    def write_rows(self):
        tsv.write(self.table, self.rows, schema.OVERLAY_TABLES["reach/never-shown.tsv"][1])

    def write_notes(self, records):
        notes.write_notes(self.sidecar, {"schema": notes.SCHEMA, "note": records})

    def assert_refusal(self, message):
        with self.assertRaises(schema.ProfileError) as caught:
            schema.load(self.path)
        self.assertEqual(len(str(caught.exception).splitlines()), 1)
        self.assertIn(message, str(caught.exception))
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = main(["--project-root", str(self.root), "--profile", str(self.path),
                           "verify", "profile", "--record", str(self.root / "proof")])
        self.assertNotEqual(status, 0)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual([line.split()[0] for line in err.getvalue().splitlines()], ["error", "result"])
        self.assertIn(message, err.getvalue())

    def test_sidecar_binds_rows_and_preserves_records_and_inputs(self):
        self.note.update(guidance=["Keep the exclusion."], reason=["The branch is unused."],
                         evidence=[{"method": "lookup", "verification": "recorded"}])
        second = self.make_note(self.fact_id, self.rows[0], "second-proof")
        second["facts"] = ["An independent path agrees."]
        self.write_notes([self.note, second])
        before = {file: file.read_bytes() for file in (self.table, self.sidecar)}
        sources = schema.read(self.path)
        found = {fact.id: fact for fact in schema.overlay_facts(sources)}
        prose = "Keep the exclusion.\nNo reachable reader.\nThe branch is unused.\nAn independent path agrees."
        self.assertEqual(found[self.fact_id].note_records, (self.note, second))
        self.assertEqual(found[self.fact_id].evidence.sentence, prose)
        self.assertEqual(schema.load(self.path)["reach"]["never_shown"],
                         [dict(self.rows[0], evidence=prose), self.rows[1]])
        self.assertEqual(schema.overlay_facts(sources), schema.overlay_facts(sources))
        self.assertNotIn("evidence", sources.overlay["reach"]["never_shown"][0])
        inputs = [self.order, self.overlay / "identity.toml", self.table, self.sidecar]
        self.assertEqual(schema.overlay_files(self.path), inputs)
        self.assertEqual(sources.overlay_files, inputs)
        self.assertEqual(profile.source_files(self.path), [self.path / "profile.toml", *inputs])
        self.assertEqual(profile.locate(str(self.sidecar)), self.path)
        self.assertEqual({file: file.read_bytes() for file in before}, before)
        self.assertNotIn("author", found[self.fact_id].note_records[0])
        self.assertNotIn("created", found[self.fact_id].note_records[0])

    def test_row_reordering_keeps_fact_bindings(self):
        self.rows.reverse()
        self.write_rows()
        self.assertEqual(schema.load(self.path)["reach"]["never_shown"][1]["evidence"], self.note["facts"][0])

    def test_editor_receives_shared_notes_for_each_language(self):
        from gen3tl.web import editordata

        loaded = Profile.load(self.path)
        for code in ("xx", "yy"):
            with self.subTest(language=code):
                document = {"language": code, "entries": []}
                editordata.attach_notes(document, loaded, {})
                self.assertEqual(document["notes"], {"schema": notes.SCHEMA, "note": [self.note]})

    def language_file(self, records, code="ru"):
        path = self.root / code / "notes" / "profile.toml"
        notes.write_notes(path, {"schema": notes.SCHEMA, "note": records})
        return path

    def language_note(self, identity="ru-proof", code="ru"):
        return {**deepcopy(self.note), "id": identity, "scope": {"kind": "language", "language": code},
                "facts": ["The translated line measures 90 px."], "related_notes": [self.note["id"]]}

    def test_language_notes_follow_shared_prose_in_their_language_only(self):
        from gen3tl.web import editordata

        ru = self.language_note()
        hidden = {**self.language_note("ru-hidden"), "status": "proposed", "facts": ["Not shown."]}
        path = self.language_file([ru, hidden])
        by_fact = schema.language_notes(self.path, path, "ru")
        self.assertEqual(by_fact, {self.fact_id: [ru, hidden]})
        data = schema.load(self.path)
        shared = deepcopy(data)
        schema.add_language_prose(data, by_fact, str(path))
        self.assertEqual(data["reach"]["never_shown"][0]["evidence"],
                         "No reachable reader.\nThe translated line measures 90 px.")
        self.assertTrue(notes.has_prose(data["reach"]["never_shown"][0]["evidence"], single_line=True))
        self.assertEqual(schema.value_digest(data), schema.value_digest(shared))
        self.assertEqual(schema.load(self.path), shared)
        loaded = Profile.load(self.path)
        for code, expected in (("ru", [self.note, ru, hidden]), ("es", [self.note])):
            with self.subTest(language=code):
                document = {"language": code, "entries": []}
                editordata.attach_notes(document, loaded, {}, folder=self.root / code, wording=())
                self.assertEqual(document["notes"]["note"], expected)

    def test_language_notes_are_refused_when_they_do_not_match_the_profile(self):
        stale = self.language_note()
        stale["binding"] = {**stale["binding"], "value_sha256": "0" * 64}
        unknown = self.language_note()
        unknown["subject"] = {**unknown["subject"], "id": "reach.never_shown[ids=entry:9]"}
        unknown["binding"] = {**unknown["binding"], "id": "reach.never_shown[ids=entry:9]"}
        cases = (("ru", self.language_note(code="es"), "expected a profile fact note of language ru"),
                 ("ru", {**self.language_note(), "scope": {"kind": "shared"}}, "expected a profile fact note"),
                 ("ru", stale, "stale note binding"),
                 ("ru", unknown, "unknown profile fact"),
                 ("ru", {key: value for key, value in self.language_note(self.note["id"]).items() if key != "related_notes"}, "duplicate note"))
        for code, record, message in cases:
            with self.subTest(message=message):
                path = self.language_file([record])
                with self.assertRaisesRegex(schema.ProfileError, message):
                    schema.language_notes(self.path, path, code)

    def test_stale_binding_is_refused(self):
        self.note["binding"]["value_sha256"] = "0" * 64
        self.write_notes([self.note])
        self.assert_refusal("stale note binding")

    def test_edited_legacy_prose_is_refused(self):
        self.rows[0]["evidence"] = "Edited legacy proof."
        self.write_rows()
        self.assert_refusal("legacy prose and structured notes share field evidence")

    def test_duplicate_note_ids_are_refused_within_sidecar(self):
        text = self.sidecar.read_text(encoding="utf-8")
        self.sidecar.write_text(text + text[text.index("[[note]]"):], encoding="utf-8")
        self.assert_refusal("duplicate note row-proof")

    def test_duplicate_note_ids_are_refused_across_sidecar_and_embedded_notes(self):
        note = self.make_note("rom.size", 2048)
        record = {"id": "rom.size", "notes_schema": notes.SCHEMA, "notes": [note]}
        with (self.overlay / "identity.toml").open("a", encoding="utf-8") as stream:
            stream.write(f"\n[[fact]]\n" + "\n".join(f"{key} = {literal(value)}" for key, value in record.items()))
        self.assert_refusal("duplicate note row-proof")

    def test_subject_must_belong_to_this_table(self):
        for fact_id in ("rom.size", "reach.never_shown[ids=missing]", "reach.never_shown"):
            with self.subTest(fact_id=fact_id):
                self.write_notes([self.make_note(fact_id, self.rows[0])])
                self.assert_refusal("subject names no row fact")

    def test_scope_and_original_prose_field_are_required(self):
        for field in (None, "note", "/evidence", "ids"):
            with self.subTest(field=field):
                note = deepcopy(self.note)
                note["binding"].pop("field")
                if field is not None:
                    note["binding"]["field"] = field
                self.write_notes([note])
                self.assert_refusal("note binding requires field evidence")
        self.note["scope"] = {"kind": "language", "language": "xx"}
        self.write_notes([self.note])
        self.assert_refusal("notes require this profile fact and shared scope")

    def test_status_projection_matches_embedded_notes(self):
        for status in notes.STATUSES:
            with self.subTest(status=status):
                self.note["status"] = status
                self.write_notes([self.note])
                if status not in ("unreviewed", "accepted"):
                    with self.assertRaisesRegex(schema.ProfileError, "evidence needs a sentence"):
                        schema.overlay_facts(schema.read(self.path))
                    continue
                fact = next(fact for fact in schema.overlay_facts(schema.read(self.path)) if fact.id == self.fact_id)
                self.assertEqual(fact.note_records, (self.note,))
                self.assertTrue(notes.has_prose(fact.value["evidence"]))
                self.assertEqual(fact.value["evidence"], self.note["facts"][0])

    def test_empty_collection_retains_legacy_evidence(self):
        self.rows[0]["evidence"] = "Legacy proof."
        self.write_rows()
        self.write_notes([])
        self.assertEqual(schema.load(self.path)["reach"]["never_shown"], self.rows)

    def test_orphan_and_separately_listed_sidecars_are_refused(self):
        with self.order.open("a", encoding="utf-8") as stream:
            stream.write("reach/never-shown.notes.toml\n")
        self.assert_refusal("note files load through their sibling TSV table")
        self.table.unlink()
        self.order.write_text("identity.toml\n", encoding="utf-8")
        self.assert_refusal("unlisted files: 'reach/never-shown.notes.toml'")

    def test_sidecar_cannot_escape_overlay(self):
        outside = self.root / "outside.toml"
        self.sidecar.rename(outside)
        self.sidecar.symlink_to(outside)
        self.assert_refusal("invalid note file")

    def test_schema_validation_reaches_sidecar(self):
        self.sidecar.write_text('schema = "0.0.0"\nnote = []\n', encoding="utf-8")
        self.assert_refusal("unsupported schema")

    def test_window_notes_bind_measures_and_detect_edits(self):
        folder = self.overlay / "layout"
        folder.mkdir()
        windows = folder / "windows.tsv"
        strings = folder / "window-strings.tsv"
        row = {"name": "menu-status", "width": 32, "style": "small"}
        for file, rows in ((windows, [row]), (strings, [{"string": "items:0", "window": "menu-status"}])):
            tsv.write(file, rows, schema.OVERLAY_TABLES[file.relative_to(self.overlay).as_posix()][1])
        with self.order.open("a", encoding="utf-8") as stream:
            stream.write("layout/windows.tsv\tlayout.window\nlayout/window-strings.tsv\tlayout.window_strings\n")
        note = self.make_note("layout.window[name=menu-status]", row, "window-proof")
        notes.write_notes(windows.with_suffix(".notes.toml"), {"schema": notes.SCHEMA, "note": [note]})
        layout = schema.load(self.path)["layout"]
        self.assertEqual(layout["window"], [dict(row, evidence=note["facts"][0])])
        self.assertEqual(layout["window_width"], {"items:0": 32})
        self.assertEqual(layout["window_style"], {"items:0": "small"})
        note["id"] = self.note["id"]
        notes.write_notes(windows.with_suffix(".notes.toml"), {"schema": notes.SCHEMA, "note": [note]})
        self.assert_refusal("duplicate note row-proof")
        note["id"] = "window-proof"
        notes.write_notes(windows.with_suffix(".notes.toml"), {"schema": notes.SCHEMA, "note": [note]})
        tsv.write(windows, [dict(row, width=33)], schema.OVERLAY_TABLES["layout/windows.tsv"][1])
        self.assert_refusal("stale note binding")
        tsv.write(windows, [row], schema.OVERLAY_TABLES["layout/windows.tsv"][1])
        notes.write_notes(strings.with_suffix(".notes.toml"), {"schema": notes.SCHEMA, "note": [note]})
        self.assert_refusal("subject names no row fact")
