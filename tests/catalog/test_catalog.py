import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gen3tl.catalog.catalog import export, load_catalog, never_shown, never_shown_evidence, never_shown_tag, tm_report
from gen3tl.core.charmap import Charmap
from gen3tl.core.notes import BoundProse
from gen3tl.catalog.po import PoEntry, read, render, write

def inv_with(text):
    return {"entries": [
        {"id": "s0001000", "offset": 0x1000, "length": len(text) + 1, "kind": "pointer", "category": "script",
         "mode": "field", "text": text, "refs": [{"at": 0x20, "kind": "script:loadpointer"}], "table": None,
         "index": None, "flags": []},
        {"id": "s0002000", "offset": 0x2000, "length": 6, "kind": "pointer", "category": "script",
         "mode": "field", "text": "Hello", "refs": [{"at": 0x30, "kind": "script:loadpointer"}], "table": None,
         "index": None, "flags": []},
    ]}

CTX = {"strings": {"s0001000": {"uses": [{"map_name": "Bellin Town", "event": "npc", "sprite": 1,
                                          "textcolor_gender": "female", "box": "npc", "script": "0x10",
                                          "order": 0}], "inferred_map": None, "speakers": []},
                   "s0002000": {"uses": [], "inferred_map": "Bellin Town", "speakers": []}}}

class PoTest(unittest.TestCase):
    def test_roundtrip_tricky_strings(self):
        tricky = 'Say "hi"!\\nA {COLOR 06}back\\\\slash{COLOR 08} and a very long line ' * 3
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.po"
            e = PoEntry("s1", tricky, "перевод \\p ок", extracted=["note"], flags=["fuzzy"],
                        previous_msgid="old")
            write(p, [e], {"Language": "ru"})
            hdr, es = read(p)
            self.assertEqual(hdr["Language"], "ru")
            self.assertEqual((es[0].msgid, es[0].msgstr, es[0].flags, es[0].previous_msgid),
                             (tricky, "перевод \\p ок", ["fuzzy"], "old"))

    def test_a_change_marks_an_entry_translated_or_keeps_it_approved(self):
        cases = (("approved", "Old", []), ("approved blank", "", ["empty"]), ("translated", "Old", ["fuzzy"]),
                 ("translated blank", "", ["empty", "fuzzy"]), ("untranslated", "", []),
                 ("flagged untranslated", "", ["fuzzy"]))
        for name, text, flags in cases:
            for keep in (False, True):
                with self.subTest(before=name, keep_approved=keep):
                    entry = PoEntry("key", "English", text, flags=list(flags))
                    approved = entry.approved
                    self.assertEqual(approved, name.startswith("approved"))
                    self.assertTrue(entry.set_translation("New", "text", keep_approved=keep))
                    self.assertEqual((entry.msgstr, entry.state), ("New", "text"))
                    self.assertEqual(entry.approved, approved and keep)
                    self.assertEqual(entry.fuzzy, not (approved and keep))
                    self.assertFalse(entry.set_translation("New", "text", keep_approved=keep))
        entry = PoEntry("key", "English", "Old")
        self.assertTrue(entry.set_translation("", "original", keep_approved=True))
        self.assertEqual((entry.state, entry.flags, entry.approved), ("original", [], False))

    def test_read_reloads_an_edited_file_and_returns_independent_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.po"
            write(path, [PoEntry("key", "key", "One")], {"Language": "ru"})
            _, first = read(path)
            first[0].msgstr = "Changed"
            self.assertEqual(read(path)[1][0].msgstr, "One")
            stat = path.stat()
            path.write_text(path.read_text(encoding="utf-8").replace('"One"', '"Two"'), encoding="utf-8")
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1))
            self.assertEqual(read(path)[1][0].msgstr, "Two")

    def test_writer_invalidates_a_file_with_the_same_stamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.po"
            write(path, [PoEntry("key", "key", "One")], {"Language": "ru"})
            self.assertEqual(read(path)[1][0].msgstr, "One")
            stat = path.stat()
            write(path, [PoEntry("key", "key", "Two")], {"Language": "ru"})
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            self.assertEqual(read(path)[1][0].msgstr, "Two")

    def test_read_cache_separates_projects_with_the_same_file_stamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first" / "x.po"
            second = Path(tmp) / "second" / "x.po"
            write(first, [PoEntry("key", "key", "One")], {"Language": "ru"})
            write(second, [PoEntry("key", "key", "Two")], {"Language": "ru"})
            stamp = first.stat().st_mtime_ns
            for path in (first, second):
                os.utime(path, ns=(stamp, stamp))
            self.assertEqual(read(first)[1][0].msgstr, "One")
            self.assertEqual(read(second)[1][0].msgstr, "Two")
            self.assertEqual(read(first)[1][0].msgstr, "One")

    def test_cached_copy_carries_every_field_of_an_entry(self):
        from dataclasses import fields

        from gen3tl.catalog import po
        values = {"msgctxt": "key", "msgid": "key", "msgstr": "Text", "extracted": ["a"], "translator": ["b"],
                  "references": ["c"], "flags": ["empty"], "previous_msgid": "old", "owner": "x", "keyed": True}
        # a new field must reach the copy
        self.assertEqual(set(values), {f.name for f in fields(PoEntry)})
        entry = PoEntry(**values)
        copied = po._copy_entries([entry])[0]
        self.assertEqual(copied, entry)
        for name in ("extracted", "translator", "references", "flags"):
            self.assertIsNot(getattr(copied, name), getattr(entry, name))

class ExportTest(unittest.TestCase):
    def test_check_detects_comment_and_wrapping_drift_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "po"
            args = (inv_with("Hello"), CTX, Charmap(), lambda b: 6, root, "test")
            export(*args)
            file = next(root.rglob("*.po"))
            expected = file.read_bytes()
            self.assertEqual(export(*args, check=True)["changed_files"], [])
            for changed in (expected.replace(b"#. category: script", b"#. category: stale"),
                            expected.replace(b'msgstr ""', b'msgstr ""\n""', 1)):
                with self.subTest(changed=changed):
                    file.write_bytes(changed)
                    stat = file.stat()
                    self.assertEqual(export(*args, check=True)["changed_files"], [str(file)])
                    self.assertEqual(file.read_bytes(), changed)
                    self.assertEqual(file.stat().st_mtime_ns, stat.st_mtime_ns)
            export(*args)
            self.assertEqual(file.read_bytes(), expected)
            self.assertEqual(export(*args, check=True)["changed_files"], [])

    def test_check_detects_missing_and_removable_files_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "po"
            args = (inv_with("Hello"), CTX, Charmap(), lambda b: 6, root, "test")
            stats = export(*args, check=True)
            self.assertEqual(len(stats["changed_files"]), 1)
            self.assertFalse(root.exists())
            export(*args)
            file = next(root.rglob("*.po"))
            old = file.with_name("previous.po")
            file.rename(old)
            dead = root / "obsolete.po"
            write(dead, [], {}, [PoEntry("removed", "Previous", "Earlier")])
            before = {p: p.read_bytes() for p in root.rglob("*.po")}
            self.assertEqual(set(export(*args, check=True)["changed_files"]), {str(file), str(old)})
            self.assertEqual({p: p.read_bytes() for p in root.rglob("*.po")}, before)
            export(*args)
            self.assertFalse(old.exists())
            self.assertEqual(dead.read_bytes(), before[dead])
            self.assertEqual(export(*args, check=True)["changed_files"], [])

    def test_command_returns_failure_for_drift_without_writing_keys(self):
        from gen3tl.cli import catalog as command
        from gen3tl.catalog import keys
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "context.json").write_text(json.dumps(CTX))
            profile = SimpleNamespace(name="fixture", sha1="0" * 40, get=lambda *args: {},
                                      load_rom=lambda *args: SimpleNamespace(data=b""))
            args = SimpleNamespace(profile="fixture", rom=None, lang="fr", check=True, po_root=root / "po",
                                   project=SimpleNamespace(build=root, snapshot=root / "absent.json", game=False))
            font = SimpleNamespace(width=lambda rom, value: 6)
            with patch.object(command.Profile, "by_name", return_value=profile), \
                    patch("gen3tl.build.font.FontTable.from_profile", return_value=[font]), \
                    patch("gen3tl.discover.inventory.load", return_value=inv_with("Hello")), \
                    patch.object(keys, "for_profile", return_value=keys.EMPTY), redirect_stdout(io.StringIO()):
                self.assertEqual(command.export(args), 1)
                self.assertFalse(args.po_root.exists())
                args.check = False
                command.export(args)
                args.check = True
                self.assertEqual(command.export(args), 0)
                aliases = keys.Aliases([("hash:11111111", 0x1000, "s0001000")])
                extended = keys.Aliases([*aliases.rows, ("hash:22222222", 0x2000, "s0002000")])
                with patch.object(keys, "for_profile", return_value=aliases), \
                        patch.object(keys, "assign", return_value=extended), \
                        patch.object(keys, "rom_events", return_value={}), patch.object(keys, "write") as save:
                    self.assertEqual(command.export(args), 1)
                    save.assert_not_called()

    def test_reexport_keeps_translations_and_flags_source_changes(self):
        cm = Charmap()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export(inv_with("Hi there!"), CTX, cm, lambda b: 6, root, "test")
            cat = load_catalog(root)
            self.assertEqual(cat["s0001000"][0].name, "bellin-town.po")
            # translate both entries
            for sid, msg in (("s0001000", "Привет!"), ("s0002000", "Здравствуй")):
                p, _ = cat[sid]
                hdr, es = read(p)
                for e in es:
                    if e.msgctxt == sid:
                        e.msgstr = msg
                write(p, es, hdr)
            # same source: translations kept, not fuzzy
            stats = export(inv_with("Hi there!"), CTX, cm, lambda b: 6, root, "test")
            self.assertEqual(stats["kept"], 2)
            self.assertFalse(load_catalog(root)["s0001000"][1].fuzzy)
            # changed source: kept, marked fuzzy, previous msgid recorded
            stats = export(inv_with("Hi there, friend!"), CTX, cm, lambda b: 6, root, "test")
            e = load_catalog(root)["s0001000"][1]
            self.assertEqual((e.msgstr, e.fuzzy, e.previous_msgid), ("Привет!", True, "Hi there!"))
            self.assertEqual(stats["source_changed"], 1)
            self.assertIn("Distinct English strings: 2", tm_report(root))

    def test_group_that_lost_its_entries_leaves_no_file(self):
        # both strings move to another map.
        # the file goes, the translation follows.
        cm = Charmap()
        moved = {"strings": {"s0001000": {"uses": [dict(CTX["strings"]["s0001000"]["uses"][0], map_name="Dresco Town")],
                                          "inferred_map": None, "speakers": []},
                             "s0002000": {"uses": [], "inferred_map": "Dresco Town", "speakers": []}}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export(inv_with("Hi there!"), CTX, cm, lambda b: 6, root, "test")
            p, _ = load_catalog(root)["s0001000"]
            hdr, es = read(p)
            es[0].msgstr = "Привет!"
            write(p, es, hdr)
            write(root / "maps" / "gone.po", [PoEntry("s0009000", "Removed", "Удалено")], {})
            stats = export(inv_with("Hi there!"), moved, cm, lambda b: 6, root, "test")
            cat = load_catalog(root)
            self.assertFalse(p.exists())
            self.assertEqual(cat["s0001000"][0].name, "dresco-town.po")
            self.assertEqual(cat["s0001000"][1].msgstr, "Привет!")
            self.assertEqual((stats["stale_files_removed"], len(stats["stale_files_kept"])), (1, 1))
            self.assertTrue((root / "maps" / "gone.po").exists())   # holds an entry written nowhere

    def test_widths_leave_trailing_spaces_out(self):
        # 'Hi there!' is 54 px, the padded line 18 px.
        # a closing code adds none.
        # a space before a placeholder counts.
        cm = Charmap()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export(inv_with("Hi there!\\nYes      \\n  Hi {COLOR 02}\\nHi {PLAYER}"), CTX, cm, lambda b: 6,
                   root, "test")
            e = load_catalog(root)["s0001000"][1]
        self.assertIn("original lines: 4, widths px: 54, 18, 24, 18", e.extracted)

REACH = {"strings": {"s0001000": {"class": "unreachable", "group": "script"},
                     "s0002000": {"class": "reachable", "group": "script"},
                     "s0003000": {"class": "unreachable", "group": "system"},
                     "s0004000": {"class": "possibly reachable", "group": "system"}}}

class NeverShownTest(unittest.TestCase):
    """The reach study and reviewed overrides tag the text the game never shows."""

    def test_multiline_note_evidence_survives_catalog_round_trip(self):
        rules = [{"ids": ["s0001000"], "evidence": BoundProse("No caller.\nUnused table.")}]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export(inv_with("Hello"), CTX, Charmap(), lambda b: 6, root, "test",
                   never=never_shown(None, rules))
            entry = load_catalog(root)["s0001000"][1]
        self.assertEqual(never_shown_tag(entry.extracted), "review: No caller. Unused table.")

    def test_sources(self):
        rules = [{"ids": ["s0002000", "s0003000"], "evidence": "handler 0x8126B60 has no reference"}]
        self.assertEqual(never_shown(REACH, rules), {
            "s0001000": "reach study: unreachable script text",
            "s0002000": "review: handler 0x8126B60 has no reference",
            "s0003000": "reach study: unreachable system text; "
                        "review: handler 0x8126B60 has no reference"})
        self.assertEqual(never_shown(None, []), {})
        for bad in ({"ids": ["s0001000"]}, {"ids": [], "evidence": "x"}, {"ids": ["s1"], "evidence": "a\nb"}):
            with self.assertRaises(ValueError):
                never_shown(None, [bad])

    def test_tag(self):
        self.assertEqual(never_shown_tag(["category: script; mode: field",
                                          "reach: never shown (review: dead code (0xD7274))"]),
                         "review: dead code (0xD7274)")
        self.assertIsNone(never_shown_tag(["category: script; mode: field"]))

    def test_evidence_from_the_tag_else_the_study(self):
        study = {"strings": {"s1": {"class": "unreachable", "group": "system"}, "s2": {"class": "reachable"}}}
        self.assertEqual(never_shown_evidence("s1", ["reach: never shown (review: no caller)"], study),
                         "review: no caller")
        self.assertEqual(never_shown_evidence("s1", [], study), "reach study: unreachable system text")
        self.assertIsNone(never_shown_evidence("s2", [], study))
        self.assertIsNone(never_shown_evidence("s1", [], None))

    def test_observed_dialogue_survives_stale_reach_export(self):
        rules = [{"ids": ["s0001000"], "at": 0x100, "evidence": "native defeat recovery"}]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export(inv_with("Hi there!"), CTX, Charmap(), lambda b: 6, root, "test",
                   never=never_shown(REACH, []))
            self.assertIsNotNone(never_shown_tag(load_catalog(root)["s0001000"][1].extracted))
            export(inv_with("Hi there!"), CTX, Charmap(), lambda b: 6, root, "test",
                   never=never_shown(REACH, [], rules))
            self.assertIsNone(never_shown_tag(load_catalog(root)["s0001000"][1].extracted))
        self.assertIn("s0003000", never_shown(REACH, [], rules))
        with self.assertRaisesRegex(ValueError, "both observed and never shown"):
            never_shown(REACH, [{"ids": ["s0001000"], "evidence": "dead script"}], rules)

    def test_observation_requires_evidence_and_unique_ids(self):
        rule = {"ids": ["s0001000"], "at": 0x100, "evidence": "native recovery"}
        for change in ({"evidence": ""}, {"evidence": "a\nb"}, {"at": -1},
                       {"at": "0x100"}, {"ids": "s0001000"}, {"ids": []}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                never_shown(REACH, [], [dict(rule, **change)])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            never_shown(REACH, [], [rule, rule])

    def test_reexport_writes_the_tag_and_keeps_translations_and_comments(self):
        cm = Charmap()
        rules = [{"ids": ["s0002000", "s0009000"], "evidence": "no caller"}]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export(inv_with("Hi there!"), CTX, cm, lambda b: 6, root, "test")
            p, _ = load_catalog(root)["s0001000"]
            hdr, es = read(p)
            for e in es:
                e.msgstr, e.translator = "Привет!", ["source: a glossary row"]
            write(p, es, hdr)
            before = {sid: (e.msgstr, e.flags, e.translator, e.extracted) for sid, (_, e) in load_catalog(root).items()}
            stats = export(inv_with("Hi there!"), CTX, cm, lambda b: 6, root, "test",
                           never=never_shown(REACH, rules))
            self.assertEqual((stats["never_shown"], stats["never_shown_unknown"]), (2, ["s0003000", "s0009000"]))
            cat = load_catalog(root)
            for sid, (msgstr, flags, translator, extracted) in before.items():
                e = cat[sid][1]
                self.assertEqual((e.msgstr, e.flags, e.translator), (msgstr, flags, translator))
                self.assertEqual(e.extracted[1:], extracted)   # the tag comes first
            self.assertEqual(cat["s0001000"][1].extracted[0],
                             "reach: never shown (reach study: unreachable script text)")
            self.assertEqual(never_shown_tag(cat["s0002000"][1].extracted), "review: no caller")
            # a source gone: the tag goes, the rest stays
            export(inv_with("Hi there!"), CTX, cm, lambda b: 6, root, "test", never=never_shown(None, rules))
            cat = load_catalog(root)
            self.assertIsNone(never_shown_tag(cat["s0001000"][1].extracted))
            self.assertEqual((cat["s0001000"][1].msgstr, cat["s0001000"][1].translator),
                             ("Привет!", ["source: a glossary row"]))
            self.assertEqual(cat["s0001000"][1].extracted, before["s0001000"][3])

class WriterTest(unittest.TestCase):
    def test_every_writer_uses_the_exporters_wrapping(self):
        from gen3tl.catalog import batch, entrynotes, merge, po
        from gen3tl.catalog.source import digest
        from gen3tl.cli import lang
        from gen3tl.core import notes
        from gen3tl.lang import language, termbase
        from gen3tl.web import editorimport, editorsave

        targets = ["Bonjour " + "x" * length for length in (58, 59, 60, 67)]
        targets += ['Bonjour "été"!\\n' + r"{PLAYER} chemin\\suite\pEncore\l" * 4,
                    "Bonjour " + "un très long texte " * 12 + "\n\tfin"]
        inv = {"entries": [dict(inv_with("Hello")["entries"][0], id=f"s{i:07X}", offset=i)
                           for i in range(0x1000, 0x1000 + len(targets))]}
        ctx = {"strings": {}}
        with tempfile.TemporaryDirectory() as tmp, language.use("fr"):
            root = Path(tmp)
            catalog = root / "po"
            args = (inv, ctx, Charmap(), lambda b: 6, catalog, "fixture")
            export(*args, language="fr")
            file = next(catalog.rglob("*.po"))
            header, entries = read(file)
            sid = entries[0].msgctxt
            note = {"id": "note:wording", "subject": {"kind": "entry", "id": sid},
                    "scope": {"kind": "language", "language": "fr"},
                    "binding": {"kind": "entry", "id": sid, "source_sha256": digest("Hello")},
                    "reason": ["Recorded wording rationale."], "status": "unreviewed",
                    "sources": [{"kind": "project", "name": "Reference", "address": "reference:key",
                                 "verification": "recorded"}]}
            notes.write_notes(entrynotes.path(catalog), {"schema": notes.SCHEMA, "note": [note]})
            for entry, target in zip(entries, targets):
                entry.msgstr = target
                entry.flags = ["fuzzy"]
                entry.translator = ["Translator remark"]
            dead = [PoEntry("removed", "Earlier " * 20, "Ancien " * 20, previous_msgid="Old " * 30)]
            write(file, entries, header, dead)
            export(*args, language="fr")
            expected = file.read_bytes()
            self.assertEqual(render(file, entries, header).encode(), expected)
            self.assertIn(b'msgstr ""\n"Bonjour ', expected)

            def reset(values=None):
                before = deepcopy(entries)
                for entry, value in zip(before, values or ["Old"] * len(before)):
                    entry.msgstr, entry.flags = value, []
                write(file, before, header, dead)

            def same(writer, wanted=None):
                self.assertEqual(file.read_bytes(), wanted or expected, writer)

            reset()
            batches = root / "batches"
            directory = batch.export_batch("review", [e.msgctxt for e in entries], catalog, {}, root=batches)
            (directory / "translation.json").write_text(json.dumps({e.msgctxt: e.msgstr for e in entries}))
            batch.import_batch("review", catalog, root=batches)
            same("batch import")
            for review in batch.PASSES:
                (directory / f"review-{review}-1.json").write_text("[]")
            batch.approve_batch("review", catalog, root=batches)
            approved = deepcopy(entries)
            for entry in approved:
                entry.set_fuzzy(False)
            kept = render(file, approved, header).encode()   # an approved text stays approved
            same("batch approval", kept)

            reset()
            document = {"profile": "fixture", "language": "fr", "rom_sha1": "0" * 40,
                        "entries": [{"id": e.msgctxt, "base": "Old", "translation": e.msgstr}
                                    for e in entries]}
            def refresh(catalog, destination):
                destination.mkdir()
                (destination / "index.json").write_text(json.dumps(
                    {"profile": "fixture", "language": "fr", "rom": {"sha1": "0" * 40}}))

            store = editorsave.DraftStore(catalog, root / "view", "fixture", "fr", "0" * 40, refresh)
            store.save(document)
            same("editor save", kept)

            reset()
            submitted = root / "edits.json"
            submitted.write_text(json.dumps(document))
            submission = editorimport.read_export(submitted, "fixture", "fr", "0" * 40, load_catalog(catalog))
            plan = root / "plan.json"
            plan.write_text("[]")
            report = editorimport.import_export(submission, catalog, batches, plan, {})
            for result in report["batches"]:
                batch.import_batch(result["name"], catalog, root=batches)
            same("editor import")

            reset([value.replace("Bonjour", "Salut") for value in targets])
            glossary, replacement = root / "glossary.tsv", root / "replacement.tsv"
            termbase.save(glossary, [termbase.Term("term:hello", "term", "Hello", "Salut")])
            termbase.save(replacement, [termbase.Term("term:hello", "term", "Hello", "Bonjour")])
            options = SimpleNamespace(pairs=["term:hello", "Bonjour"], glossary=glossary,
                                      replacement_glossary=replacement, include=[], po_root=catalog,
                                      report=None, apply=True, allow_errors=False, index=root / "index.tsv")
            with patch.object(lang, "_rename_checks", return_value=({}, {}, {})), redirect_stdout(io.StringIO()):
                self.assertEqual(lang.term_rename(options), 0)
            same("term rename", kept)
            file.write_bytes(expected)

            result = merge.Result({file.relative_to(catalog).as_posix(): po.parse(file)})
            file.unlink()
            merge.write(result, catalog)
            same("merge")
            projected = entrynotes.project(entries, entrynotes.path(catalog), "fr")
            write(file, projected, header)
            same("note regeneration")
            note["reason"] = ["Updated wording rationale."]
            notes.write_notes(entrynotes.path(catalog), {"schema": notes.SCHEMA, "note": [note]})
            self.assertEqual(export(*args, language="fr", check=True)["changed_files"], [str(file)])
            same("note check")
            export(*args, language="fr")
            self.assertIn(b"Updated wording rationale.", file.read_bytes())
            self.assertEqual(export(*args, language="fr", check=True)["changed_files"], [])
