from test_roots import TEST_ROOT
"""Common notes preserve evidence and reject invalid or stale records."""

import copy
import json
from pathlib import Path
import tempfile
import tomllib
import unittest

from gen3tl.core import notes

FIXTURE = TEST_ROOT / "fixtures" / "notes.toml"


class NotesTest(unittest.TestCase):
    def test_bound_prose_preserves_text_without_legacy_presence_constraints(self):
        for text in ("First assertion.\nSecond assertion.", "First assertion.\rSecond assertion."):
            with self.subTest(text=text):
                value = notes.BoundProse(text)
                self.assertTrue(notes.has_prose(value, single_line=True))
                self.assertEqual(str(value), text)
                self.assertEqual(bool(value), bool(text))
                self.assertEqual(json.loads(json.dumps(value)), text)
                self.assertIsInstance(copy.deepcopy(value), notes.BoundProse)
                self.assertFalse(notes.has_prose(str(value), single_line=True))
        for value in (None, "", "  ", [], 1, False, notes.BoundProse(""), notes.BoundProse(" \n")):
            self.assertFalse(notes.has_prose(value))
        self.assertTrue(notes.has_prose("Legacy proof.", single_line=True))
        self.assertTrue(notes.has_prose("Two\nlines."))
        self.assertFalse(notes.has_prose("Two\rlines.", single_line=True))

    def setUp(self):
        self.document = notes.read_notes(FIXTURE)
        self.term, self.research, self.profile, self.entry, self.graphic, self.rule, self.catalog = self.document["note"]

    def assert_invalid(self, note, field, value, message):
        candidate = copy.deepcopy(note)
        candidate[field] = value
        with self.assertRaisesRegex(notes.NoteError, message):
            notes.validate_note(candidate)

    def test_all_note_kinds_round_trip_without_inventing_metadata(self):
        original = tomllib.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(self.document, original)
        self.assertEqual({note["subject"]["kind"] for note in original["note"]}, set(notes.SUBJECT_KINDS))
        self.assertEqual(len(original["note"]), 7)
        snapshot = copy.deepcopy(original)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "nested" / "notes.toml"
            notes.write_notes(path, original)
            restored = notes.read_notes(path)
        self.assertEqual(restored, original)
        self.assertEqual(original, snapshot)
        self.assertEqual(notes.notes_text(restored), notes.notes_text(original))
        self.assertEqual(json.loads(json.dumps(restored)), original)
        self.assertNotIn("author", restored["note"][0])
        self.assertNotIn("retrieved", restored["note"][1]["sources"][0])
        self.assertNotIn("captures", restored["note"][2]["evidence"][0])

    def test_exact_strings_and_numeric_types_survive_toml(self):
        self.term["sources"][0]["quote"] = '  "Quoted"\\path\n\t\b\f\r\0\x1f\x7f é e\u0301 🐾  '
        self.profile["subject"]["id"] = 'table[name="label with spaces"]'
        self.profile["binding"]["id"] = self.profile["subject"]["id"]
        restored = tomllib.loads(notes.notes_text(self.document))
        self.assertEqual(restored, self.document)
        self.assertIs(type(restored["note"][4]["evidence"][0]["measurements"][0]["value"]), int)
        self.assertIs(type(restored["note"][4]["evidence"][0]["measurements"][1]["value"]), float)

    def test_only_supported_version_and_collection_shape_are_accepted(self):
        for version in (None, 1, True, "1", "1.0", "01.0.0", "1.0.1", "1.0.0-beta", "1.0.0", []):
            with self.subTest(version=version), self.assertRaisesRegex(notes.NoteError, "schema"):
                notes.validate(dict(self.document, schema=version))
        invalid = [None, [], {}, {"schema": notes.SCHEMA}, dict(self.document, extra=1),
                   dict(self.document, note={}), dict(self.document, note=[None])]
        for document in invalid:
            with self.subTest(document=document), self.assertRaises(notes.NoteError):
                notes.validate(document)
        empty = {"schema": notes.SCHEMA, "note": []}
        self.assertEqual(tomllib.loads(notes.notes_text(empty)), empty)

    def test_required_fields_and_nonempty_prose(self):
        for field in ("id", "subject", "scope", "status"):
            candidate = dict(self.term)
            del candidate[field]
            with self.subTest(field=field), self.assertRaisesRegex(notes.NoteError, "missing fields"):
                notes.validate_note(candidate)
        for field, value in (("id", " "), ("id", 4), ("extra", "unknown"),
                             ("guidance", []), ("facts", "sentence"), ("reason", [""]),
                             ("facts", [False]), ("facts", ["bad\ud800"])):
            with self.subTest(field=field, value=value):
                self.assert_invalid(self.term, field, value, "note")
        candidate = {key: value for key, value in self.term.items() if key not in notes.PROSE_FIELDS}
        with self.assertRaisesRegex(notes.NoteError, "guidance, facts or reason"):
            notes.validate_note(candidate)
        for field in notes.PROSE_FIELDS:
            with self.subTest(field=field):
                notes.validate_note(dict(candidate, **{field: ["One assertion."]}))

    def test_prose_free_references_require_a_derived_summary(self):
        reference = {"id": "generated:one", "subject": {"kind": "term", "id": "term:one"},
                     "scope": {"kind": "language", "language": "xx"}, "status": "unreviewed",
                     "evidence": [{"method": "research-assembly", "verification": "recorded"}],
                     "related_subjects": [{"kind": "research", "id": "finding:one"}]}
        notes.validate_note(reference)
        notes.validate_note(dict(reference, assembly_fields=["role"]))
        self.assert_invalid(reference, "assembly_fields", [], "assembly_fields")
        self.assert_invalid(reference, "assembly_fields", ["role", "role"], "duplicate")
        self.assert_invalid(reference, "assembly_fields", ["other"], "assembly_fields")
        citation = dict(reference, evidence=[{"method": "research-citation", "verification": "recorded"}],
                        citation={"label": "Archive", "columns": {"name": "attested"}})
        notes.validate_note(citation)
        self.assert_invalid(reference, "related_subjects", [{"kind": "term", "id": "other"}], "prose|facts")
        self.assert_invalid(reference, "evidence", [{"method": "other", "verification": "recorded"}], "prose|facts")
        self.assert_invalid(reference, "citation", {"label": "Archive"}, "citation")
        self.assert_invalid(citation, "citation", {"label": ""}, "label")
        self.assert_invalid(citation, "citation", {"label": "Archive", "columns": {"extra": "x"}}, "columns")
        self.assert_invalid(citation, "citation", {"label": "Archive", "columns": {"name": ""}}, "columns")

    def test_identity_is_independent_of_subject_and_wording(self):
        changed = copy.deepcopy(self.entry)
        changed["id"] = "another-note"
        changed["guidance"] = ["Another instruction for the same target."]
        notes.validate({"schema": notes.SCHEMA, "note": [self.entry, changed]})
        changed["id"] = self.entry["id"]
        with self.assertRaisesRegex(notes.NoteError, "duplicate note"):
            notes.validate({"schema": notes.SCHEMA, "note": [self.entry, changed]})

    def test_scopes_and_subjects_are_explicit(self):
        for scope in ({}, "shared", {"kind": "shared", "language": "zz"},
                      {"kind": "language"}, {"kind": "language", "language": ""},
                      {"kind": "language", "language": "zz//region"}, {"kind": "all"}):
            with self.subTest(scope=scope):
                self.assert_invalid(self.term, "scope", scope, "scope")
        for scope in ({"kind": "shared"}, {"kind": "language", "language": "zz-Latn-001"}):
            notes.validate_note(dict(self.term, scope=scope))
        for subject in ({}, {"kind": "entry"}, {"kind": "other", "id": "x"},
                        {"kind": "term", "id": ""}, {"kind": "term", "id": "x", "line": 1}):
            with self.subTest(subject=subject):
                self.assert_invalid(self.term, "subject", subject, "subject")

    def test_bindings_are_required_typed_and_checked_for_staleness(self):
        for note in (self.entry, self.profile, self.graphic):
            candidate = copy.deepcopy(note)
            del candidate["binding"]
            with self.subTest(kind=note["subject"]["kind"]):
                with self.assertRaisesRegex(notes.NoteError, "binding required"):
                    notes.validate_note(candidate)
                notes.validate_note(note, expected_binding=copy.deepcopy(note["binding"]))
                with self.assertRaisesRegex(notes.NoteError, "stale binding"):
                    notes.validate_note(note, expected_binding=dict(note["binding"], id="another"))
                self.assert_invalid(note, "binding", dict(note["binding"], id="another"), "differs from subject")
                self.assert_invalid(note, "binding", dict(note["binding"], kind="term"), "differs from subject")
                self.assert_invalid(note, "binding", dict(note["binding"], extra=1), "unknown fields")
        for note, field in ((self.entry, "source_sha256"), (self.profile, "value_sha256")):
            for value in (None, "a" * 63, "A" * 64, "z" * 64):
                self.assert_invalid(note, "binding", dict(note["binding"], **{field: value}), "digest")
            current = dict(note["binding"], **{field: "f" * 64})
            with self.assertRaisesRegex(notes.NoteError, "stale binding"):
                notes.validate_note(note, expected_binding=current)
        self.assert_invalid(self.term, "binding", self.entry["binding"], "not defined")
        self.assert_invalid(self.profile, "binding", dict(self.profile["binding"], field=""), "field")

    def test_sources_keep_separate_fields_and_explicit_verification(self):
        source = self.term["sources"][0]
        for field in ("kind", "name", "address", "verification"):
            candidate = dict(source)
            del candidate[field]
            self.assert_invalid(self.term, "sources", [candidate], "missing fields")
        for field, value in (("kind", "unknown"), ("name", ""), ("address", None),
                             ("verification", "accepted"), ("relation", "preferred"),
                             ("locator", []), ("quoted_term", ""), ("quote", 1),
                             ("retrieved", "2026-02-30"), ("retrieved", "20261110"),
                             ("retrieved", "2026-11-10T00:00:00Z"), ("extra", "no")):
            with self.subTest(field=field, value=value):
                self.assert_invalid(self.term, "sources", [dict(source, **{field: value})], "sources")
        for kind in notes.SOURCE_KINDS:
            notes.validate_note(dict(self.term, sources=[dict(source, kind=kind)]))
        for relation in notes.SOURCE_RELATIONS:
            notes.validate_note(dict(self.term, sources=[dict(source, relation=relation)]))

    def test_accepted_term_guidance_requires_a_resolved_source(self):
        candidate = copy.deepcopy(self.term)
        del candidate["sources"]
        candidate["evidence"] = self.research["evidence"]
        with self.assertRaisesRegex(notes.NoteError, "requires a source"):
            notes.validate_note(candidate)
        for status in ("unreviewed", "proposed", "needs_review", "superseded"):
            notes.validate_note(dict(candidate, status=status))
        source = self.term["sources"][0]
        self.assert_invalid(candidate, "sources", [dict(source, verification="unresolved")], "requires a source")
        for verification in ("recorded", "checked"):
            notes.validate_note(dict(candidate, sources=[dict(source, verification=verification)]))

    def test_evidence_locations_do_not_conflate_addresses(self):
        evidence = self.profile["evidence"][0]
        for location in ({"kind": "rom_offset", "value": -1}, {"kind": "cpu_address", "value": 2**32},
                         {"kind": "rom_offset", "value": True}, {"kind": "cpu_address", "value": "0x100"},
                         {"kind": "rom_offset", "value": 1.0}, {"kind": "address", "value": 0x100},
                         {"kind": "symbol", "value": 42}, {"kind": "file", "value": ""},
                         {"kind": "rom_offset", "value": 0, "label": ""}):
            with self.subTest(location=location):
                self.assert_invalid(self.profile, "evidence", [dict(evidence, locations=[location])], "locations")
        locations = [{"kind": kind, "value": 0x100} for kind in ("rom_offset", "cpu_address")]
        self.profile["evidence"][0]["locations"] = locations
        restored = tomllib.loads(notes.notes_text(self.document))
        self.assertEqual(restored["note"][2]["evidence"][0]["locations"], locations)

    def test_measurements_reject_boolean_nonfinite_and_fractional_counts(self):
        evidence = self.research["evidence"][0]
        for value, unit in ((True, "pixels"), (float("nan"), "pixels"), (float("inf"), "pixels"),
                            (float("-inf"), "pixels"), ("12", "pixels"), (-1, "count"), (1.5, "count")):
            measurement = {"name": "width", "value": value, "unit": unit}
            with self.subTest(value=value, unit=unit):
                self.assert_invalid(self.research, "evidence", [dict(evidence, measurements=[measurement])], "value")

    def test_nested_evidence_and_reference_validation(self):
        evidence = self.graphic["evidence"][0]
        for field, value in (("method", ""), ("verification", "accepted"), ("locations", []),
                             ("measurements", [{"name": "count", "value": 2}]),
                             ("captures", [{"address": "x", "sha256": "bad"}]),
                             ("captures", [{"address": ""}]), ("captures", [{"address": "x", "extra": 1}]),
                             ("related_subjects", [{"kind": "other", "id": "x"}]), ("extra", "x")):
            with self.subTest(field=field):
                self.assert_invalid(self.graphic, "evidence", [dict(evidence, **{field: value})], "evidence")
        for value in ([], "id", [""], ["other", "other"], [self.term["id"]]):
            self.assert_invalid(self.term, "related_notes", value, "related_notes")
        self.assert_invalid(self.term, "related_subjects", [{"kind": "term"}], "related_subjects")
        notes.validate_note(dict(self.term, related_notes=["note-in-another-file"]))

    def test_review_and_attribution_remain_independent(self):
        for status in notes.STATUSES:
            notes.validate_note(dict(self.entry, status=status))
        self.assert_invalid(self.entry, "status", "verified", "status")
        notes.validate_note(self.entry)
        with self.assertRaisesRegex(notes.NoteError, "author and created"):
            notes.validate_note(self.entry, new=True)
        notes.validate_note(self.graphic, new=True)
        new = {"schema": notes.SCHEMA, "note": [self.graphic]}
        notes.validate(new, new=True)
        for field in ("author", "created"):
            candidate = dict(self.graphic)
            del candidate[field]
            with self.assertRaisesRegex(notes.NoteError, "author and created"):
                notes.validate({"schema": notes.SCHEMA, "note": [candidate]}, new=True)
        for field, value in (("author", ""), ("reviewed_by", 1), ("created", "2026-02-30"),
                             ("updated", "2026-11-11T10:00:00"), ("reviewed_at", "yesterday")):
            self.assert_invalid(self.graphic, field, value, field)
        for value in ("2026-11-11", "2026-11-11T10:00:00.123Z", "2026-11-11T10:00:00-04:00"):
            notes.validate_note(dict(self.graphic, created=value), new=True)

    def test_invalid_files_report_path_and_writes_preserve_existing_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "notes.toml"
            for text in ('[[note]', 'schema = "1.0.0"\nnote = []'):
                path.write_text(text)
                with self.assertRaisesRegex(notes.NoteError, "notes.toml"):
                    notes.read_notes(path)
            path.write_text("unchanged")
            with self.assertRaises(notes.NoteError):
                notes.write_notes(path, dict(self.document, schema="1.0.0"))
            self.assertEqual(path.read_text(), "unchanged")
            missing = path.parent / "absent" / "notes.toml"
            with self.assertRaises(notes.NoteError):
                notes.write_notes(missing, {})
            self.assertFalse(missing.parent.exists())
            with self.assertRaises(FileNotFoundError):
                notes.read_notes(missing)


if __name__ == "__main__":
    unittest.main()
