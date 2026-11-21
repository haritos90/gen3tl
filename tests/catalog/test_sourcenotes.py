import json
import os
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gen3tl.catalog import batch, catalog, draftcatalog, entrynotes, keys, merge, po
from gen3tl.catalog.source import digest
from gen3tl.core import notes as structured
from gen3tl.lang import language
from gen3tl.web import editordata, editorimport, editorsave
from gen3tl.check import qa
from gen3tl.core.charmap import Charmap
from gen3tl.check.layout import Measure
from gen3tl.profile.profile import Profile

def inventory():
    return {"entries": [
        {"id": sid, "offset": offset, "length": len(text) + 1, "kind": "pointer",
         "category": "script", "mode": "field", "text": text, "table": None,
         "index": None, "refs": [{"at": offset + 16, "kind": "script:loadpointer"}], "flags": []}
        for sid, offset, text in (("s0000100", 0x100, "Hello"), ("s0000200", 0x200, "Goodbye"))]}

class SourceNoteTest(unittest.TestCase):
    def test_typed_notes_round_trip_without_changing_other_comments(self):
        entry = po.PoEntry("s1", "English", "Translation", extracted=["source: context only"],
                           translator=["General remark", "source: Old", "Source: unrelated case"],
                           flags=["fuzzy"], references=["rom:0x100"], previous_msgid="Earlier")
        note = 'Pokémon GO: "Potion" | reference\\page\nglossary:item:potion\n'
        entry.source_note = note
        self.assertEqual(entry.translator[:2], ["General remark", "Source: unrelated case"])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "notes.po"
            po.write(path, [entry], {"Language": "ru"})
            header, entries = po.read(path)
        self.assertEqual(header["Language"], "ru")
        self.assertEqual(entries[0], entry)
        self.assertEqual(entries[0].source_note, note)
        entries[0].source_note = ""
        self.assertEqual(entries[0].translator, ["General remark", "Source: unrelated case"])
        self.assertEqual(entries[0].extracted, ["source: context only"])

    def test_invalid_note_cannot_break_po_comment_lines(self):
        entry = po.PoEntry("s1", "English", translator=["source: Preserved", "Other"])
        for note in (None, 3, "bad\rmsgid", "bad\vmsgid", "bad\u2028msgid", "bad\0msgid"):
            with self.subTest(note=note):
                with self.assertRaises(ValueError):
                    entry.source_note = note
                self.assertEqual(entry.translator, ["source: Preserved", "Other"])

    def test_batch_import_and_catalog_refresh_preserve_current_note(self):
        inv, context = inventory(), {"strings": {}}
        cm = Charmap()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            po_root, batches = root / "translation/po", root / "batches"
            catalog.export(inv, context, cm, lambda b: 6, po_root, "example", language="fr")
            file, _ = catalog.load_catalog(po_root)["s0000100"]
            header, entries = po.read(file)
            chosen = next(e for e in entries if e.msgctxt == "s0000100")
            chosen.translator = ["General translator remark"]
            chosen.source_note = "Official game, scene 1\nglossary:example"
            po.write(file, entries, header)
            directory = batch.export_batch("review", ["s0000100"], po_root, {}, root=batches)
            snapshot = json.loads((directory / "source.json").read_text())
            self.assertEqual(snapshot[0]["note"], chosen.source_note)
            # Current PO metadata wins over snapshots.
            chosen.source_note = "Official game, corrected scene 2\nglossary:example"
            po.write(file, entries, header)
            (directory / "translation.json").write_text(json.dumps({"s0000100": "Bonjour"}))
            result = batch.import_batch("review", po_root, root=batches)
            self.assertEqual(result["changed"], 1)
            imported = catalog.load_catalog(po_root)["s0000100"][1]
            self.assertEqual(imported.source_note, chosen.source_note)
            self.assertEqual(imported.translator[0], "General translator remark")
            self.assertTrue(imported.fuzzy)
            for review in batch.PASSES:
                (directory / f"review-{review}-1.json").write_text("[]")
            batch.approve_batch("review", po_root, root=batches)
            catalog.export(inv, context, cm, lambda b: 6, po_root, "example", language="fr")
            final = catalog.load_catalog(po_root)["s0000100"][1]
            self.assertEqual((final.msgstr, final.source_note, final.fuzzy), ("Bonjour", chosen.source_note, False))
            # Source refresh retains provenance and flags text.
            inv["entries"][0]["text"] = "Hello again"
            catalog.export(inv, context, cm, lambda b: 6, po_root, "example", language="fr")
            refreshed = catalog.load_catalog(po_root)["s0000100"][1]
            self.assertEqual(refreshed.source_note, chosen.source_note)
            self.assertEqual(refreshed.previous_msgid, "Hello")
            self.assertTrue(refreshed.fuzzy)

    def test_legacy_batch_without_note_keeps_po_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            po_root = root / "po"
            entry = po.PoEntry("s1", "Hello", "Old", translator=["source: Original reference"])
            po.write(po_root / "a.po", [entry], {})
            directory = batch.export_batch("review", ["s1"], po_root, {}, root=root / "batches")
            source = json.loads((directory / "source.json").read_text())
            del source[0]["note"]
            (directory / "source.json").write_text(json.dumps(source))
            (directory / "translation.json").write_text(json.dumps({"s1": "New"}))
            batch.import_batch("review", po_root, root=root / "batches")
            self.assertEqual(catalog.load_catalog(po_root)["s1"][1].source_note, "Original reference")

    def test_qa_lists_sources_without_changing_translation_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entries = [po.PoEntry("s0000100", "Hello", "Hello"), po.PoEntry("s0000200", "Goodbye")]
            po.write(root / "a.po", entries, {})
            args = ({"layout": {"dialogue_width": 208}}, inventory(), root, Charmap(), Measure(lambda b: 6, {}), set())
            before = qa.run(*args)
            entries[0].source_note = "Official | title\nglossary:example"
            entries[1].source_note = "Official | title\nOfficial | title"
            po.write(root / "a.po", entries, {})
            after = qa.run(*args)
        self.assertEqual(before.findings, after.findings)
        self.assertEqual(before.checked, after.checked)
        self.assertEqual(after.checked, 1)
        report = qa.report(after)
        self.assertIn("including untranslated entries", report)
        self.assertIn("| Official \\| title | 2 | s0000100, s0000200 |", report)
        self.assertIn("| glossary:example | 1 | s0000100 |", report)
        self.assertNotIn("Wording sources", qa.report(before))


class StructuredSourceNoteTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enterContext(language.use(language.Language("fixture", {}, self.root)))
        self.po_root = self.root / "po"
        self.file = self.po_root / "maps" / "one.po"
        self.code = language.current().code
        self.header = {"Language": self.code, "Project-Id-Version": "example"}
        self.note_path = entrynotes.path(self.po_root)
        self.addCleanup(entrynotes.invalidate, self.note_path)
        self.note = {"id": "note:entry-one", "subject": {"kind": "entry", "id": "s1"},
                     "scope": {"kind": "language", "language": self.code},
                     "binding": {"kind": "entry", "id": "s1", "source_sha256": digest("Hello")},
                     "reason": ["Recorded wording rationale."], "status": "unreviewed",
                     "sources": [{"kind": "project", "name": "Reference", "address": "reference:key",
                                  "verification": "recorded", "locator": "section 2"}]}
        self.entry = po.PoEntry("s1", "Hello", "Old", translator=["General remark"], flags=["c-format"])
        po.write(self.file, [self.entry], self.header)
        self.store_notes()

    def store_notes(self, *notes):
        structured.write_notes(self.note_path, {"schema": structured.SCHEMA, "note": list(notes) or [self.note]})

    def test_unchanged_collection_is_read_once_for_load_and_projection(self):
        with patch.object(structured, "read_notes", wraps=structured.read_notes) as read:
            for file in (self.note_path, Path(os.path.relpath(self.note_path))):
                self.assertEqual(entrynotes.load(file, self.code), {"s1": [self.note]})
                projected = entrynotes.project([self.entry], file, self.code)[0]
                self.assertIn(self.note["reason"][0], projected.source_note)
            self.assertEqual(read.call_count, 1)

    def test_external_rewrite_reloads_on_size_or_timestamp_change(self):
        for field in ("size", "mtime"):
            with self.subTest(field=field):
                self.store_notes()
                entrynotes.load(self.note_path, self.code)
                before = self.note_path.stat()
                changed = deepcopy(self.note)
                changed["reason"] = ["x" * (len(self.note["reason"][0]) + (field == "size"))]
                self.note_path.write_text(structured.notes_text({"schema": structured.SCHEMA,
                                                                "note": [changed]}), encoding="utf-8")
                os.utime(self.note_path, ns=(before.st_atime_ns, before.st_mtime_ns + (field == "mtime")))
                after = self.note_path.stat()
                self.assertEqual(after.st_size == before.st_size, field == "mtime")
                self.assertEqual(after.st_mtime_ns == before.st_mtime_ns, field == "size")
                self.assertEqual(entrynotes.load(self.note_path, self.code), {"s1": [changed]})

    def test_writer_invalidates_with_unchanged_size_and_timestamp(self):
        entrynotes.load(self.note_path, self.code)
        before = self.note_path.stat()
        changed = deepcopy(self.note)
        changed["reason"] = ["x" * len(self.note["reason"][0])]
        self.store_notes(changed)
        os.utime(self.note_path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(self.note_path.stat().st_size, before.st_size)
        self.assertEqual(entrynotes.load(self.note_path, self.code), {"s1": [changed]})

    def test_read_during_write_does_not_retain_previous_records(self):
        before = self.note_path.stat()
        changed = deepcopy(self.note)
        changed["reason"] = ["x" * len(self.note["reason"][0])]
        write_text = Path.write_text

        def read_first(file, *args, **kwargs):
            if file == self.note_path:
                self.assertEqual(entrynotes.load(file, self.code), {"s1": [self.note]})
            result = write_text(file, *args, **kwargs)
            os.utime(file, ns=(before.st_atime_ns, before.st_mtime_ns))
            return result

        with patch.object(Path, "write_text", read_first):
            self.store_notes(changed)
        self.assertEqual(self.note_path.stat().st_size, before.st_size)
        self.assertEqual(entrynotes.load(self.note_path, self.code), {"s1": [changed]})

    def test_language_and_reloaded_aliases_do_not_reuse_grouped_records(self):
        self.note["subject"]["id"] = self.note["binding"]["id"] = "table:labels/1"
        self.store_notes()
        first = keys.Aliases([("table:labels/1", None, "s1")])
        reloaded = keys.Aliases([("table:labels/1", None, "s2")])
        self.assertEqual(entrynotes.load(self.note_path, self.code, first), {"s1": [self.note]})
        self.assertEqual(entrynotes.load(self.note_path, self.code, reloaded), {"s2": [self.note]})
        with self.assertRaisesRegex(ValueError, "expected language scope"):
            entrynotes.load(self.note_path, self.code + "-other", reloaded)

    def test_returned_records_and_projections_cannot_change_retained_notes(self):
        records = entrynotes.load(self.note_path, self.code)
        records["s1"][0]["reason"].append("Changed copy.")
        records["s1"][0]["sources"][0]["address"] = "changed:key"
        records["s1"].append({})
        records.clear()
        projected = entrynotes.project([self.entry], self.note_path, self.code)[0]
        projected.translator.clear()
        self.assertEqual(entrynotes.load(self.note_path, self.code), {"s1": [self.note]})
        self.assertEqual(self.entry.translator, ["General remark"])

    def test_po_projection_round_trip_and_stale_snapshot(self):
        original = self.note_path.read_bytes()
        header, entries = po.read(self.file)
        note = entries[0].source_note
        self.assertIn("Recorded wording rationale.", note)
        self.assertIn("address: reference:key", note)
        self.assertIn("status: unreviewed", note)
        self.assertEqual(entries[0].translator[0], "General remark")
        with self.assertRaisesRegex(ValueError, "edit structured"):
            entries[0].source_note = "Snapshot edit"
        po.write(self.file, entries, header)
        self.assertEqual(po.read(self.file)[1], entries)
        self.assertEqual(self.note_path.read_bytes(), original)
        self.note["reason"] = ["Current authoritative wording."]
        self.store_notes()
        current = self.note_path.read_bytes()
        entries[0].msgstr = "Changed"
        po.write(self.file, entries, header)
        saved = po.read(self.file)[1][0]
        self.assertIn("Current authoritative wording.", saved.source_note)
        self.assertNotIn("Recorded wording rationale.", saved.source_note)
        self.assertEqual(saved.msgstr, "Changed")
        self.assertEqual(self.note_path.read_bytes(), current)

    def test_custom_catalog_location_keeps_authority_and_safe_comment_lines(self):
        custom = self.root / "catalog"
        file = custom / "one.po"
        self.note["reason"] = ["First\r\nSecond\u2028Third"]
        self.store_notes()
        po.write(file, [self.entry], self.header, notes_path=self.note_path)
        rows = catalog.load_catalog(custom)
        before = self.note_path.read_bytes()
        self.assertIn("First\\u000d\nSecond\\u2028Third", rows["s1"][1].source_note)
        entrynotes.refresh(rows, self.code)
        self.assertEqual(po.read(file)[1][0], rows["s1"][1])
        self.assertEqual(self.note_path.read_bytes(), before)

    def test_catalog_refresh_preserves_notes_and_translation_fields(self):
        inv = inventory()
        self.note["subject"]["id"] = self.note["binding"]["id"] = "s0000100"
        self.store_notes()
        catalog.export(inv, {"strings": {}}, Charmap(), lambda b: 6, self.po_root,
                       "example", language=self.code)
        rows = catalog.load_catalog(self.po_root)
        path, entry = rows["s0000100"]
        header, entries = po.read(path)
        entries[0].msgstr = "Target"
        po.write(path, entries, header)
        before = self.note_path.read_bytes()
        catalog.export(inv, {"strings": {}}, Charmap(), lambda b: 6, self.po_root,
                       "example", language=self.code)
        saved = catalog.load_catalog(self.po_root)["s0000100"][1]
        self.assertEqual(saved.msgstr, "Target")
        self.assertIn("Recorded wording", saved.source_note)
        self.assertEqual(self.note_path.read_bytes(), before)

    def test_rejects_independent_legacy_prose_and_wrong_bindings_before_writing(self):
        original = self.file.read_bytes()
        cases = []
        legacy = deepcopy(self.entry)
        legacy.source_note = "Independently editable comment"
        cases.append((self.note, legacy, "both legacy and structured"))
        for field, value, message in (("scope", {"kind": "shared"}, "language scope"),
                                       ("binding", {**self.note["binding"], "source_sha256": "0" * 64},
                                        "differs from catalog English")):
            note = deepcopy(self.note)
            note[field] = value
            cases.append((note, self.entry, message))
        note = deepcopy(self.note)
        note["subject"] = {"kind": "term", "id": "term:one"}
        del note["binding"]
        cases.append((note, self.entry, "entry subject"))
        for note, entry, message in cases:
            with self.subTest(message=message):
                self.store_notes(note)
                with self.assertRaisesRegex(ValueError, message):
                    po.write(self.file, [entry], self.header)
                self.assertEqual(self.file.read_bytes(), original)

    def test_multiple_notes_statuses_and_complete_records_remain_readable(self):
        records = []
        for status in structured.STATUSES:
            note = deepcopy(self.note)
            note.update(id="note:" + status, status=status, reason=["Reason for " + status])
            records.append(note)
        self.store_notes(*records)
        self.assertEqual(entrynotes.load(self.note_path, self.code)["s1"], records)
        entry = po.read(self.file)[1][0]
        for status in structured.STATUSES:
            self.assertEqual("Reason for " + status in entry.source_note, status in ("accepted", "unreviewed"))
        po.write(self.file, [entry], self.header)
        structured.write_notes(self.note_path, {"schema": structured.SCHEMA, "note": []})
        cleared = po.read(self.file)[1][0]
        self.assertEqual(cleared.translator, ["General remark"])

    def test_aliases_key_only_sources_and_stale_digest(self):
        aliases = keys.Aliases([("table:labels/1", None, "s1")])
        entry = po.PoEntry("table:labels/1", "table:labels/1", "Old", keyed=True)
        projected = entrynotes.project([entry], self.note_path, self.code, aliases, {"s1": digest("Hello")})[0]
        self.assertIn("Recorded wording", projected.source_note)
        with self.assertRaisesRegex(ValueError, "differs from catalog English"):
            entrynotes.project([entry], self.note_path, self.code, aliases, {"s1": digest("Changed")})
        header = {**self.header, po.SOURCE_FIELD: po.KEY_ONLY}
        po.write(self.file, [po.PoEntry("s1", "Hello", "Old")], header)
        loaded = catalog.load_catalog(self.po_root, source=[po.PoEntry("s1", "Hello")])
        self.assertIn("Recorded wording", loaded["s1"][1].source_note)
        with self.assertRaisesRegex(ValueError, "differs from catalog English"):
            catalog.load_catalog(self.po_root, source=[po.PoEntry("s1", "Changed")])
        self.assertIn('msgid "s1"', self.file.read_text())

    def test_batch_export_import_and_approval_use_current_authority(self):
        batches = self.root / "batches"
        directory = batch.export_batch("review", ["s1"], self.po_root, {}, root=batches)
        source = json.loads((directory / "source.json").read_text())
        self.assertIn("Recorded wording", source[0]["note"])
        source[0]["note"] = "Untrusted snapshot"
        (directory / "source.json").write_text(json.dumps(source))
        (directory / "translation.json").write_text(json.dumps({"s1": "New"}))
        self.note["reason"] = ["Updated after batch export."]
        self.store_notes()
        before = self.note_path.read_bytes()
        batch.import_batch("review", self.po_root, root=batches)
        for review in batch.PASSES:
            (directory / f"review-{review}-1.json").write_text("[]")
        batch.approve_batch("review", self.po_root, root=batches)
        entry = po.read(self.file)[1][0]
        self.assertEqual((entry.msgstr, entry.fuzzy), ("New", False))
        self.assertIn("Updated after batch export.", entry.source_note)
        self.assertNotIn("Untrusted snapshot", entry.source_note)
        self.assertEqual(self.note_path.read_bytes(), before)

    def test_editor_import_keeps_snapshot_notes_as_review_context(self):
        export = self.root / "export.json"
        export.write_text(json.dumps({"profile": "example", "language": self.code,
                                     "entries": [{"id": "s1", "translation": "Draft",
                                                  "note": "Untrusted snapshot"}]}))
        data = editorimport.read_export(export, "example", self.code, "a" * 40,
                                        catalog.load_catalog(self.po_root))
        batches, plan = self.root / "batches", self.root / "plan.json"
        plan.write_text("[]")
        before = self.note_path.read_bytes()
        result = editorimport.import_export(data, self.po_root, batches, plan, {})
        name = result["batches"][0]["name"]
        batch.import_batch(name, self.po_root, root=batches)
        self.assertEqual(self.note_path.read_bytes(), before)
        self.assertIn("Recorded wording", po.read(self.file)[1][0].source_note)
        self.assertIn("Untrusted snapshot", (batches / name / "translator-notes.md").read_text())

    def test_editor_import_without_snapshot_notes_preserves_canonical_notes(self):
        export, batches, plan = self.root / "export.json", self.root / "batches", self.root / "plan.json"
        before = self.note_path.read_bytes()
        original = self.file.read_bytes()
        for context in ({}, {"note": "Historical review context"}):
            with self.subTest(context=context):
                export.write_text(json.dumps({"profile": "example", "language": self.code, "entries": [
                    {"id": "s1", "base": "Old", "translation": "Old", **context}]}))
                data = editorimport.read_export(export, "example", self.code, "a" * 40,
                                                catalog.load_catalog(self.po_root))
                result = editorimport.import_export(data, self.po_root, batches, plan, {})
                self.assertEqual(result["batches"], [])
                self.assertFalse(batches.exists())
                self.assertEqual(self.note_path.read_bytes(), before)
                self.assertEqual(self.file.read_bytes(), original)
        export.write_text(json.dumps({"profile": "example", "language": self.code, "entries": [
            {"id": "s1", "base": "Old", "translation": "Draft"}]}))
        data = editorimport.read_export(export, "example", self.code, "a" * 40,
                                        catalog.load_catalog(self.po_root))
        [route] = editorimport.import_export(data, self.po_root, batches, plan, {})["batches"]
        batch.import_batch(route["name"], self.po_root, root=batches)
        self.assertEqual(self.note_path.read_bytes(), before)
        self.assertEqual(po.read(self.file)[1][0].msgstr, "Draft")
        self.assertIn("Recorded wording", po.read(self.file)[1][0].source_note)

    def test_merge_write_refreshes_projection_without_replacing_notes(self):
        source = self.root / "next.pot"
        po.write(source, [po.PoEntry("s1", "Hello")], {})
        result = merge.merge(merge.load(self.po_root), merge.load(source))
        self.note["reason"] = ["Updated after merge planning."]
        self.store_notes()
        before = self.note_path.read_bytes()
        destination = self.root / "merged"
        merge.write(result, destination, notes_path=self.note_path)
        entry = po.read(destination / source.name)[1][0]
        self.assertIn("Updated after merge planning.", entry.source_note)
        self.assertEqual((entry.msgstr, entry.flags), ("Old", ["c-format"]))
        self.assertEqual(self.note_path.read_bytes(), before)


    def test_merge_destination_notes_take_precedence_over_original_collection(self):
        source = self.root / "next.pot"
        po.write(source, [po.PoEntry("s1", "Hello")], {})
        result = merge.merge(merge.load(self.po_root), merge.load(source))
        destination = self.root / "next" / "po"
        current = deepcopy(self.note)
        current["reason"] = ["Destination authority."]
        target_notes = entrynotes.path(destination)
        structured.write_notes(target_notes, {"schema": structured.SCHEMA, "note": [current]})
        before = target_notes.read_bytes()
        merge.write(result, destination, notes_path=self.note_path)
        self.assertIn("Destination authority.", po.read(destination / source.name)[1][0].source_note)
        self.assertEqual(target_notes.read_bytes(), before)

    def test_local_save_stages_notes_and_rejects_concurrent_note_changes(self):
        editor = self.root / "view"
        def refresh(staged, destination):
            self.assertEqual(entrynotes.path(staged).read_bytes(), self.note_path.read_bytes())
            self.assertIn("Recorded wording", catalog.load_catalog(staged)["s1"][1].source_note)
            destination.mkdir()
            (destination / "index.json").write_text(json.dumps({"profile": "example", "language": self.code,
                                                               "rom": {"sha1": "a" * 40}}))
        store = editorsave.DraftStore(self.po_root, editor, "example", self.code, "a" * 40, refresh)
        doc = {"profile": "example", "language": self.code, "rom_sha1": "a" * 40,
               "entries": [{"id": "s1", "base": "Old", "translation": "New"}]}
        before = self.note_path.read_bytes()
        self.assertEqual(store.save(doc)["saved"], 1)
        self.assertEqual(self.note_path.read_bytes(), before)
        self.assertIn("Recorded wording", po.read(self.file)[1][0].source_note)
        original = self.file.read_bytes()
        def changed(staged, destination):
            refresh(staged, destination)
            self.note["reason"] = ["Concurrent note edit."]
            self.store_notes()
        store.refresh = changed
        doc["entries"][0].update(base="New", translation="Another")
        with self.assertRaisesRegex(editorsave.SaveConflict, "source notes changed"):
            store.save(doc)
        self.assertEqual(self.file.read_bytes(), original)
        self.assertEqual(structured.read_notes(self.note_path)["note"][0]["reason"], ["Concurrent note edit."])

class EditorNoteCollectionTest(unittest.TestCase):
    def setUp(self):
        from gen3tl.catalog import guidance
        from gen3tl.build.graphics import graphics_text
        from gen3tl.check import rules
        from gen3tl.lang.termbase import Term
        from gen3tl.profile import schema
        from project_fixture import literal

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enterContext(language.use(language.Language("fixture", {}, self.root)))
        self.folder = self.root / "locale"
        self.code = language.current().code
        self.records = []

        def note(nid, kind, target, shared=False, **fields):
            subject = {"kind": kind, "id": target}
            row = {"id": nid, "subject": subject,
                   "scope": {"kind": "shared"} if shared else {"kind": "language", "language": self.code},
                   "status": "unreviewed", "facts": [f"Recorded {kind} fact."], **fields}
            if kind in ("entry", "graphic", "profile_fact"):
                row["binding"] = dict(subject)
            if kind == "entry":
                row["binding"]["source_sha256"] = digest("Hello")
            self.records.append(row)
            return row

        shared = note("entry-shared", "entry", "s1", True)
        wording = note("entry-wording", "entry", "s1", status="proposed",
                       author="contributor", created="2026-11-11",
                       sources=[{"kind": "project", "name": "Reference", "address": "ref:one",
                                 "verification": "recorded", "quote": "Exact quotation."}])
        term = note("term-note", "term", "term:one", status="needs_review")
        research = note("research-note", "research", "record:one", status="superseded")
        profile = note("profile-note", "profile_fact", "rom.size", True,
                       evidence=[{"method": "measurement", "verification": "recorded",
                                  "locations": [{"kind": "rom_offset", "value": 256}]}])
        profile["binding"].update(value_sha256=schema.value_digest(4096), field="evidence")
        artwork = note("art-note", "graphic", "picture", True)
        layout = note("layout-note", "graphic", "picture", status="accepted")
        rule = note("rule-note", "wording_rule", "rule:one")
        self.rule_rows = [{"id": "rule:one", "ids": ["s1"], "notes": [rule]}]
        self.terms = [Term("term:one", "item", "Hello", "Hello", note_ids=(term["id"],),
                           note_records=(term,), notes="Existing term projection.")]
        self.terms.append(Term("term:legacy", "item", "Goodbye", "Goodbye", notes="Legacy term note."))
        self.shared_path = self.root / "shared.toml"
        guidance.write_notes(self.shared_path, [shared, {"id": "s2", "source_sha256": digest("Goodbye"),
                                                       "text": "Legacy guidance.", "evidence": "Recorded proof."}])
        for name, rows in (("notes/entries.toml", [wording]), ("notes/terms.toml", [term]),
                           ("notes/graphics.toml", [layout]), ("research/example.notes.toml", [research])):
            structured.write_notes(self.folder / name, {"schema": structured.SCHEMA, "note": rows})
        rules.write_list(self.folder / "rules.toml", self.rule_rows, code=self.code)
        self.graphic_path = self.root / "graphics.toml"
        self.graphic_path.write_text(graphics_text([
            {"id": "picture", "reason": "", "notes_schema": structured.SCHEMA, "notes": [artwork]},
            {"id": "legacy-picture", "reason": "Legacy artwork note."}], {}), encoding="utf-8")
        profile_path = self.root / "profile-facts"
        profile_path.mkdir()
        (profile_path / "profile.toml").write_text('[discovery]\nschema = 1\n')
        (profile_path / "overlay.toml").write_text(
            'rom = {sha1 = "' + "a" * 40 + '", size = 4096}\n[[fact]]\nid = "rom.size"\n'
            'notes_schema = "3.0.0"\n[[fact.notes]]\n' +
            "\n".join(f"{key} = {literal(value)}" for key, value in profile.items()), encoding="utf-8")
        self.prof = Profile.load(profile_path)
        self.prof.data["font"] = []
        self.prof.notes_path = self.shared_path
        self.prof._project_root = self.root
        self.prof.data["graphics"] = {"inventory": "graphics.toml"}
        self.catalog = {sid: (self.folder / "po" / "one.po", po.PoEntry(sid, text, text))
                        for sid, text in (("s1", "Hello"), ("s2", "Goodbye"))}
        self.catalog["s2"][1].source_note = "Legacy source note."


    def test_entry_notes_survive_draft_output(self):
        manifest = {"profile": self.prof.name, "language": self.code, "rom_sha1": self.prof.sha1,
                    "source_sha256": draftcatalog.source_digest(entry for _, entry in self.catalog.values())}
        doc = draftcatalog.editor_data(self.prof, self.code, self.catalog, manifest, note_root=self.folder)
        for entry in doc["entries"]:
            self.assertEqual(entry["source_sha256"], digest(self.catalog[entry["id"]][1].msgid))
        self.assertEqual(doc["entries"][0]["note_ids"], ["entry-shared", "entry-wording", "rule-note"])
        self.assertEqual(doc["entries"][1]["note"], "Legacy source note.")


    def test_key_only_entry_links_use_aliases_and_snapshot_digests(self):
        from unittest.mock import patch
        aliases = keys.Aliases([("table:labels/1", None, "s1")])
        self.catalog["s1"][1].msgid = "table:labels/1"
        self.catalog["s1"][1].keyed = True
        doc = {"language": self.code, "entries": [{"id": "table:labels/1"}]}
        with patch.object(keys, "for_profile", return_value=aliases):
            editordata.attach_notes(doc, self.prof, self.catalog, self.terms, folder=self.folder,
                                    wording=[], digests={"s1": digest("Hello")})
            self.assertEqual(doc["entries"][0]["note_ids"], ["entry-shared", "entry-wording"])
            with self.assertRaisesRegex(ValueError, "differs from catalog English"):
                editordata.attach_notes(doc, self.prof, self.catalog, folder=self.folder,
                                        wording=[], digests={"s1": digest("Changed")})


    def test_cache_tracks_note_creation_edit_deletion_and_rule_migration(self):
        inputs = [self.shared_path, self.graphic_path, *editordata.note_inputs(self.folder)]
        key = lambda: editorsave.data_key(self.folder / "po", self.prof.path, "toolkit", inputs)
        paths = [self.shared_path, self.graphic_path, self.prof.path / "overlay.toml",
                 *sorted((self.folder / "notes").glob("*.toml")),
                 self.folder / "research" / "example.notes.toml", self.folder / "rules.toml"]
        for path in paths:
            with self.subTest(path=path.name):
                initial, data = key(), path.read_bytes()
                path.write_bytes(data + b"\n")
                self.assertNotEqual(key(), initial)
                path.unlink()
                self.assertNotEqual(key(), initial)
                path.write_bytes(data)
                self.assertEqual(key(), initial)
        initial = key()
        new_path = self.folder / "research" / "new.notes.toml"
        new_path.write_text('schema = "3.0.0"\nnote = []\n')
        self.assertNotEqual(key(), initial)
        new_path.unlink()
        self.assertEqual(key(), initial)
        current = self.folder / "rules.toml"
        legacy = current.with_suffix(".tsv")
        current.rename(legacy)
        changed = key()
        self.assertNotEqual(changed, initial)
        legacy.write_bytes(legacy.read_bytes() + b"\n")
        self.assertNotEqual(key(), changed)
