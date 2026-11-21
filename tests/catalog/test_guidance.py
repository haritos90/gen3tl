import copy
import json
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from test_roots import TEST_ROOT
from gen3tl.catalog import guidance, keys, source
from gen3tl.core import notes as structured
from gen3tl.web import editordata
from gen3tl.core.charmap import Charmap
from gen3tl.catalog.po import PoEntry
from gen3tl.profile.profile import Profile

class GuidanceTest(unittest.TestCase):
    def setUp(self):
        self.source_text = "{PLAYER} found an item!"
        self.note = {"id": "s1", "source_sha256": source.digest(self.source_text),
                     "text": "PLAYER names the finder, not the item.",
                     "evidence": "The acquisition script fills PLAYER."}
        self.profile = Profile({"rom": {"sha1": "a" * 40}, "layout": {}, "font": [],
                                "translator_notes": [self.note]}, Path("example.toml"))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.notes_path = Path(temporary.name) / "notes.toml"

    def catalog(self, translation=""):
        return {"s1": (Path("a.po"), PoEntry("s1", self.source_text, translation,
                    translator=["source: Current wording reference"])),
                "s2": (Path("a.po"), PoEntry("s2", "Another message"))}

    def structured_note(self):
        return {"id": "finder-placeholder", "subject": {"kind": "entry", "id": "s1"},
                "scope": {"kind": "shared"},
                "binding": {"kind": "entry", "id": "s1", "source_sha256": self.note["source_sha256"]},
                "guidance": [self.note["text"]], "status": "unreviewed",
                "evidence": [{"method": self.note["evidence"], "verification": "recorded"}]}

    def set_structured(self, rows):
        guidance.write_notes(self.notes_path, {"schema": structured.SCHEMA, "note": rows})
        self.profile.notes_path = self.notes_path


    def test_partial_catalog_does_not_attach_note_to_matching_text_elsewhere(self):
        catalog = {"s2": (Path("a.po"), PoEntry("s2", self.source_text))}
        self.assertEqual(guidance.entry_notes(self.profile, catalog), {})


    def test_digest_notes_keep_rom_english_available_without_publishing_it(self):
        from gen3tl.core.rom import Rom

        catalog = self.catalog()
        cm = Charmap()
        raw = cm.encode(self.source_text)
        rom = Rom(bytes(0x100) + raw)
        inventory = {"entries": [{"id": "s1", "offset": 0x100, "length": len(raw),
                                   "mode": "field", "kind": "pointer", "flags": []}]}
        facts = {"source": {"s1": [source.digest(self.source_text)]}}
        document = editordata.source_data(self.profile, catalog, inventory, facts)
        texts, missing = source.read(bytes(rom.data), document["rows"])
        self.assertEqual((texts, missing), ({"s1": self.source_text}, []))
        self.assertNotIn(self.source_text, str(document))
        self.assertNotIn(self.source_text, guidance.notes_text([self.note]))
        catalog["s1"][1].msgid = "s1"
        catalog["s1"][1].keyed = True
        self.assertEqual(guidance.entry_notes(self.profile, catalog, {"s1": source.digest(texts["s1"])}),
                         {"s1": [{"text": self.note["text"], "evidence": self.note["evidence"]}]})
        for digests in ({}, {"s1": source.digest("Different source")}):
            with self.subTest(digests=digests), self.assertRaisesRegex(ValueError, "source differs"):
                guidance.entry_notes(self.profile, catalog, digests)

    def test_invalid_and_duplicate_notes_fail_clearly(self):
        invalid = [None, {}, [None], [dict(self.note, extra="wrong")],
                   [{key: value for key, value in self.note.items() if key != "source_sha256"}],
                   [dict(self.note, source_sha256="invalid")], [dict(self.note, text=" ")], [dict(self.note, evidence=42)],
                   [self.note, copy.deepcopy(self.note)]]
        for value in invalid:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "translator_notes"):
                self.profile.data["translator_notes"] = value
                guidance.entry_notes(self.profile, self.catalog())

    def test_structured_notes_round_trip_without_losing_fields(self):
        fixtures = structured.read_notes(TEST_ROOT / "fixtures" / "notes.toml")["note"]
        row = self.structured_note()
        row.update(facts=["The name identifies the finder."], reason=["The item has a separate name."],
                   sources=fixtures[0]["sources"], evidence=fixtures[4]["evidence"],
                   related_notes=["another-note"], related_subjects=[{"kind": "entry", "id": "s2"}],
                   author="contributor", created="2026-11-11", updated="2026-11-11T10:00:00Z",
                   reviewed_by="reviewer", reviewed_at="2026-11-11")
        original = copy.deepcopy(row)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "notes.toml"
            guidance.write_notes(path, [row])
            self.assertEqual(guidance.read_notes(path), [original])
            structured.validate(tomllib.loads(path.read_text()))
            self.profile.notes_path = path
            selected = guidance.entry_notes(self.profile, self.catalog())["s1"][0]
        self.assertEqual(row, original)
        self.assertEqual(selected["text"], "\n".join(row[key][0] for key in structured.PROSE_FIELDS))
        for value in (row["sources"][0]["address"], row["sources"][0]["quote"],
                      "width", "80", "pixels", "captures/label.png", "contributor",
                      "2026-11-11", "another-note", "s2", "unreviewed"):
            self.assertIn(value, selected["evidence"])

    def test_mixed_file_preserves_order_and_unsigned_migration(self):
        row = self.structured_note()
        legacy = dict(self.note, id="s2", source_sha256=source.digest("Another message"))
        rows = [legacy, row]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "notes.toml"
            guidance.write_notes(path, rows)
            self.assertEqual(guidance.read_notes(path), rows)
            self.profile.notes_path = path
            selected = guidance.entry_notes(self.profile, self.catalog())
        self.assertEqual(selected["s2"], [{"text": legacy["text"], "evidence": legacy["evidence"]}])
        self.assertEqual(selected["s1"][0]["text"], row["guidance"][0])
        self.assertNotIn("author", rows[1])
        self.assertNotIn("created", rows[1])
        legacy_text = guidance.HEADER + "\n[[note]]\n" + "".join(
            f"{field} = {guidance.toml_string(self.note[field])}\n" for field in guidance.FIELDS)
        self.assertEqual(guidance.notes_text([self.note]), legacy_text)


    def test_multiple_note_identities_can_target_one_aliased_entry(self):
        row = self.structured_note()
        row["id"] = "battle:1"
        row["subject"]["id"] = row["binding"]["id"] = "battle:2"
        second = copy.deepcopy(row)
        second.update(id="second-note", guidance=["Preserve the item name."], status="accepted")
        self.set_structured([row, second])
        aliases = keys.Aliases([("battle:1", 0, "s2"), ("battle:2", 4, "s1")])
        with patch.object(keys, "for_profile", return_value=aliases):
            selected = guidance.entry_notes(self.profile, self.catalog())
            self.assertEqual(set(selected), {"s1"})
            self.assertEqual([note["text"] for note in selected["s1"]],
                             [row["guidance"][0], second["guidance"][0]])
            self.assertEqual(row["id"], "battle:1")
            second["id"] = row["id"]
            with self.assertRaisesRegex(ValueError, "duplicate note"):
                self.set_structured([row, second])
                guidance.entry_notes(self.profile, self.catalog())
            second["id"] = "second-note"
            self.set_structured([row, self.note])
            with self.assertRaisesRegex(ValueError, "duplicate entry s1"):
                guidance.entry_notes(self.profile, self.catalog())


    def test_inactive_notes_are_validated_without_becoming_guidance(self):
        row = self.structured_note()
        for status in structured.STATUSES:
            row["status"] = status
            self.set_structured([row])
            selected = guidance.entry_notes(self.profile, self.catalog())
            self.assertEqual(bool(selected), status in ("accepted", "unreviewed"))
        row["status"] = "proposed"
        row["binding"]["source_sha256"] = "f" * 64
        self.set_structured([row])
        with self.assertRaisesRegex(ValueError, "source differs"):
            guidance.entry_notes(self.profile, self.catalog())

    def test_structured_scope_shape_and_version_are_enforced(self):
        row = self.structured_note()
        invalid = [dict(row, scope={"kind": "language", "language": "zz"}),
                   dict(row, subject={"kind": "term", "id": "s1"}),
                   dict(row, binding=dict(row["binding"], id="s2")),
                   dict(row, binding=dict(row["binding"], source_sha256="bad")),
                   dict(row, text="Duplicate prose"), dict(row, status="unknown"),
                   dict(row, guidance=[]), dict(row, evidence="Old evidence")]
        for candidate in invalid:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                self.set_structured([candidate])
                guidance.entry_notes(self.profile, {})
        self.profile.data["translator_notes"] = [row]
        with self.assertRaisesRegex(ValueError, "require schema"):
            guidance.entry_notes(self.profile, self.catalog())
        for version in ("1.0", "1.0.0", 1):
            self.notes_path.write_text(f"schema = {json.dumps(version)}\nnote = []\n")
            self.profile.notes_path = self.notes_path
            with self.assertRaisesRegex(ValueError, "schema"):
                guidance.entry_notes(self.profile, self.catalog())

    def test_structured_file_errors_keep_path_and_leave_existing_files(self):
        row = self.structured_note()
        text = structured.notes_text({"schema": structured.SCHEMA, "note": [row]})
        invalid = [text.replace('schema = "3.0.0"\n', ""),
                   text.replace('schema = "3.0.0"', 'schema = "1.0.0"'),
                   text.replace('kind = "shared"', 'kind = "language", language = "zz"'),
                   'schema = "1.0.0"\nnote = {}', 'schema = "1.0.0"', '[[note]']
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "notes.toml"
            self.assertEqual(guidance.read_notes(path), [])
            for content in invalid:
                with self.subTest(content=content):
                    path.write_text(content)
                    with self.assertRaisesRegex(ValueError, "notes.toml"):
                        guidance.read_notes(path)
            path.write_text("unchanged")
            with self.assertRaises(ValueError):
                guidance.write_notes(path, [dict(row, guidance=[])])
            self.assertEqual(path.read_text(), "unchanged")
            empty = {"schema": structured.SCHEMA, "note": []}
            guidance.write_notes(path, empty)
            self.assertEqual(tomllib.loads(path.read_text()), empty)
            self.assertEqual(guidance.read_notes(path), [])
