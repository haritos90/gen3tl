import copy
import sys
import tempfile
import unittest
from pathlib import Path

from test_roots import TOOLKIT_ROOT

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.catalog import extract  # noqa: E402
from gen3tl.catalog.catalog import export, load_catalog  # noqa: E402
from gen3tl.catalog.po import (COMMENTS, KEY_ONLY, SOURCE_FIELD, PoEntry, comment_kind, key_only, read,  # noqa: E402
                               write)
from gen3tl.catalog.source import digest  # noqa: E402
from gen3tl.core.charmap import Charmap  # noqa: E402
from gen3tl.profile.profile import Profile  # noqa: E402
from gen3tl.project import ACTIVE_PROJECT, Project, registry_scope  # noqa: E402
from gen3tl.web.editordata import source_data  # noqa: E402

# every comment kind of catalog.build_entries, in its order
FULL = PoEntry("map:3.4/npc:0/1", "Hi {PLAYER}!\\nCome in.", "Привет, {PLAYER}!\\nЗаходи.", extracted=[
    "reach: never shown (review: the door script is dead)", "category: script; mode: field",
    "pointer table credits, record 2: credits page", "species: table:species/1, table:species/2",
    "in place only: 20 bytes max", "save-link: copied into save or link data; translated like all other text",
    "trainer gender: female", "trainer class id: 4",
    "used: Bellin Town, npc, sprite 3, box npc (script 0x500, line 1)", "trainer: Ann (Lass, female)",
    "used: Bellin Town, npc, sprite 3 (script 0x510, line 0)", "used in 5 places",
    "probable map (inferred from neighbours): Bellin Town", "speaker label: Ann (female)",
    "inserted as {STR_VAR_1} into map:3.4/npc:0/2: Ann says hi", "{STR_VAR_1} may be hash:00000001: 'Ann'",
    "original lines: 2, widths px: 60, 48", "prev map:3.4/npc:0/0: Hello.", "next map:3.4/npc:0/2: Bye."],
    translator=["source: the greeting wording"], references=["0x0001000"], flags=["fuzzy"],
    previous_msgid="Hi there!")
QUOTED = ("used: ", "trainer: ", "probable map", "speaker label", "inserted as", "{STR_VAR_1} may be", "prev ", "next ")
HEADER = {"Project-Id-Version": "game", "Language": "ru", SOURCE_FIELD: KEY_ONLY}


def fields(e):
    return (e.msgctxt, e.msgid, e.msgstr, e.extracted, e.translator, e.references, e.flags)


class FormTest(unittest.TestCase):
    def test_the_file_holds_the_key_translation_and_the_comments_without_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.po"
            write(path, [copy.deepcopy(FULL)], HEADER)
            text = path.read_text(encoding="utf-8")
            self.assertIn(f'msgctxt "{FULL.msgctxt}"\nmsgid "{FULL.msgctxt}"\nmsgstr', text)
            for gone in ("Come in.", "Bellin Town", "Ann", "Hello.", "Bye.", "#|", "Hi there!"):
                self.assertNotIn(gone, text)
            header, (entry,) = read(path)
            self.assertEqual(header[SOURCE_FIELD], KEY_ONLY)
            self.assertTrue(entry.keyed)
            self.assertEqual((entry.msgid, entry.msgstr, entry.translator, entry.references, entry.flags,
                              entry.previous_msgid),
                             (FULL.msgctxt, FULL.msgstr, FULL.translator, FULL.references, FULL.flags, None))
            self.assertEqual(entry.extracted, [c for c in FULL.extracted if not c.startswith(QUOTED)])
            # a rewrite keeps the form
            entry.msgstr = "Другое"
            write(path, [entry], header)
            self.assertEqual(read(path)[1], [key_only(entry)])

    def test_an_unknown_comment_counts_as_quoting_the_game(self):
        self.assertEqual(comment_kind("note: anything"), (len(COMMENTS), True))
        self.assertEqual(key_only(PoEntry("k", "x", extracted=["note: anything"])).extracted, [])

    def test_a_full_catalog_is_written_as_before(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.po"
            write(path, [copy.deepcopy(FULL)], {"Language": "ru"})
            _, (entry,) = read(path)
            self.assertEqual((fields(entry), entry.previous_msgid, entry.keyed), (fields(FULL), "Hi there!", False))


class JoinTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.po = self.root / "translation/po"
        other = PoEntry("table:items/1", "Potion", "Зелье", extracted=["category: table:items; mode: field",
                                                                       "fixed field: 13 characters max"])
        self.source = [PoEntry(e.msgctxt, e.msgid, "", list(e.extracted), [], list(e.references))
                       for e in (FULL, other)]
        write(self.po / "maps/bellin-town.po", [copy.deepcopy(FULL)], HEADER)
        write(self.po / "tables/items.po", [copy.deepcopy(other)], HEADER)
        self.full = {e.msgctxt: e for e in (FULL, other)}

    def test_an_entry_takes_its_english_and_comments_in_their_places(self):
        catalog = load_catalog(self.po, source=self.source)
        self.assertEqual(len(catalog), 2)
        for _, e in catalog.values():
            self.assertEqual(fields(e), fields(self.full[e.msgctxt]))
            self.assertFalse(e.keyed)

    def test_the_file_of_the_running_project_is_the_default(self):
        extract.write(self.root / "build/catalog.pot", self.source, type("P", (), {"name": "game", "sha1": "a" * 40}))
        token = ACTIVE_PROJECT.set(Project("game", "ru", self.root, self.root))
        try:
            catalog = load_catalog(self.po)
        finally:
            ACTIVE_PROJECT.reset(token)
        self.assertEqual(fields(catalog["map:3.4/npc:0/1"][1]), fields(FULL))

    def test_without_the_english_a_reader_refuses_or_keeps_the_keys(self):
        with registry_scope(self.root), self.assertRaisesRegex(ValueError, "without its English source catalog"):
            load_catalog(self.po)
        other = self.root / "other/build/catalog.pot"   # another game's
        extract.write(other, self.source, type("P", (), {"name": "other", "sha1": "a" * 40}))
        with registry_scope(other.parents[1]), self.assertRaisesRegex(ValueError, "another ROM"):
            load_catalog(self.po)
        catalog = load_catalog(self.po, english=False)
        self.assertTrue(all(e.keyed and e.msgid == e.msgctxt for _, e in catalog.values()))
        with self.assertRaisesRegex(ValueError, "1 keys missing from the English source catalog: table:items/1"):
            load_catalog(self.po, source=self.source[:1])

    def test_an_english_source_catalog_of_another_rom_is_refused(self):
        pot = self.root / "other.pot"
        extract.write(pot, self.source, type("P", (), {"name": "game", "sha1": "b" * 40}))
        with self.assertRaisesRegex(ValueError, "another ROM"):
            extract.english(read(self.po / "tables/items.po")[1], pot, "a" * 40)


INV = {"entries": [{"id": "s0001000", "offset": 0x1000, "length": 10, "kind": "pointer", "category": "script",
                    "mode": "field", "text": "Hi there!", "refs": [{"at": 0x20, "kind": "script:loadpointer"}],
                    "table": None, "index": None, "flags": []}]}
CTX = {"strings": {"s0001000": {"uses": [], "inferred_map": "Bellin Town", "speakers": []}}}


class ExportTest(unittest.TestCase):
    def test_export_writes_and_keeps_the_form_and_judges_the_english_by_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export(INV, CTX, Charmap(), lambda b: 6, root, "game", language="ru", key_only=True)
            path, = root.rglob("*.po")
            header, (entry,) = read(path)
            self.assertEqual((header[SOURCE_FIELD], entry.msgid, entry.keyed), (KEY_ONLY, "s0001000", True))
            self.assertNotIn("Bellin Town", path.read_text(encoding="utf-8"))
            entry.msgstr = "Привет!"
            write(path, [entry], header)
            seen = {"s0001000": digest("Hi there!")}
            changed = copy.deepcopy(INV)
            changed["entries"][0]["text"] = "Hi there, friend!"
            for inv, digests, fuzzy in ((INV, seen, False), (changed, None, False), (changed, seen, True)):
                with self.subTest(text=inv["entries"][0]["text"], digests=bool(digests)):
                    stats = export(inv, CTX, Charmap(), lambda b: 6, root, "game", language="ru", digests=digests)
                    _, (entry,) = read(path)
                    self.assertEqual((entry.msgstr, entry.fuzzy, entry.previous_msgid, entry.keyed),
                                     ("Привет!", fuzzy, None, True))
                    self.assertEqual(stats["source_changed"], int(fuzzy))
                    entry.set_fuzzy(False)
                    write(path, [entry], header)


class SourceDataTest(unittest.TestCase):
    def test_the_rows_read_each_line_and_carry_its_digest(self):
        prof = Profile({"rom": {"sha1": "a" * 40}}, Path("game.toml"))
        fixed = {**INV["entries"][0], "id": "items:1", "offset": 0x2000, "length": 8, "kind": "fixed",
                 "flags": ["unterminated"]}
        inv = {"entries": [INV["entries"][0], fixed]}
        catalog = {"s0001000": (Path("a.po"), PoEntry("s0001000", "Hi there!")),
                   "items:1": (Path("b.po"), PoEntry("items:1", "POTION"))}
        doc = source_data(prof, catalog, inv)
        self.assertEqual((doc["profile"], doc["rom_sha1"]), ("game", "a" * 40))
        self.assertEqual(doc["rows"], {"s0001000": [0x1000, 10, "field", "pointer", digest("Hi there!")],
                                       "items:1": [0x2000, 8, "field", "unterminated", digest("POTION")]})
        # snapshot digests come first; English is not needed
        catalog["s0001000"][1].keyed = True
        rows = source_data(prof, catalog, inv, {"source": {"s0001000": ["d" * 64], "items:1": ["e" * 64]}})["rows"]
        self.assertEqual({k: r[4] for k, r in rows.items()}, {"s0001000": "d" * 64, "items:1": "e" * 64})
        self.assertEqual(source_data(prof, catalog, inv)["rows"].keys(), {"items:1"})


if __name__ == "__main__":
    unittest.main()
