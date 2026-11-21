import unittest
from pathlib import Path

from gen3tl.web import editorkeyboard
from gen3tl.catalog.po import PoEntry
from gen3tl.check.layout import Limit, Measure
from gen3tl.core.charmap import Charmap
from gen3tl.core.rom import Rom
from gen3tl.profile.profile import Profile
from project_fixture import shared_profiles

def setUpModule():
    unittest.enterModuleContext(shared_profiles())

TABLES = [{"name": "keys", "base": 0x1000, "entry": 16, "index": "keyboard", "refs": [0x200]},
          {"name": "rows", "base": 0x1040, "entry": 8, "index": "keyboard", "refs": [0x204], "text": True}]

KB = {"base_pages": 3, "first_page": 1, "opening_keyboards": [1, 0],
      "opening_keyboard_offsets": [0x120, 0x130], "table": TABLES,
      "keyboards": [{"name": "pack lower", "like": 0}, {"name": "base upper", "like": 1, "base": True}],
      "pages": [{"name": "pack lower", "keyboard": 0, "look": 2}, {"name": "base upper", "keyboard": 1, "look": 1}]}

PROF = Profile({"rom": {"sha1": "ab" * 20}, "font_style": {"style": "main"}, "keyboard": KB,
                "table": [{"name": "key_rows", "base": 0x1000, "stride": 8, "count": 6}]}, Path("test.toml"))

def fixed(n):
    return {"id": f"key_rows:{n}", "kind": "fixed", "table": "key_rows", "index": n, "refs": []}

def pointer(sid, at):
    return {"id": sid, "kind": "pointer", "table": None, "index": None, "refs": [{"at": at, "kind": "table"}]}

INV = {"entries": [fixed(n) for n in range(6)]
       + [pointer(f"s{0x2000 + 0x10 * n:07X}", 0x1040 + 4 * n) for n in range(6)]
       + [pointer("s0003000", 0x900)]}

CATALOG = {sid: (Path("x.po"), PoEntry(sid, english, translation)) for sid, english, translation in (
    ("key_rows:0", "abcdef .", "бВab    "),
    ("key_rows:1", "ghijkl ,", ""),
    ("key_rows:2", "ABCDEF .", "бббббббб"),
    ("s0002000", "{CLEAR 0B}a{CLEAR 06}b", "{CLEAR 0B}б{CLEAR 06}В{CLEAR_TO 30}a{SKIP 40}b"),
    ("s0002010", "{CLEAR 0B}g", ""),
    ("s0002020", "{CLEAR 0B}A{CLEAR 06}B", "{CLEAR 0B}б"),
)}

class Pack:
    """Language pack stand-in: one drawn glyph, one copied letter."""

    def glyph_slots(self):
        return {0x42: None}

    def copy_slots(self):
        return {0x43: 0xBC}

CM = Charmap({"б": 0x42, "В": 0x43})

MEASURE = Measure(lambda b: 5 if b == 0x42 else 6, {})

CURSOR = [[0, 12, 24, 56, 68, 80, 92, 123], [0, 12, 24], [0, 22, 44, 66, 88, 110]]

def data(prof=PROF, inv=INV, limits=None, cursor=CURSOR):
    limits = {"key_rows:0": Limit("field", 0, 1, keys=8)} if limits is None else limits
    return editorkeyboard.keyboard_data(prof, CATALOG, inv, limits, MEASURE, CM, Pack(), key=lambda sid: f"k/{sid}",
                                        cursor=cursor)

class SyntheticTest(unittest.TestCase):
    def test_chat_rows_require_five_encodable_keys_within_pixel_and_byte_limits(self):
        chat = {"columns": 5, "column_width": 8}
        check = lambda text, capacity=10, measure=MEASURE: editorkeyboard.chat_problems(text, chat, capacity, measure, CM)
        self.assertEqual(check("{F9 F1}AbВ "), [])
        self.assertEqual(check("AbВ  "), [])
        self.assertEqual(check("AbВ   "), ["expected 5 keys, got 6", "row width 48 > 40 px"])
        self.assertEqual(check("AbВ "), ["expected 5 keys, got 4"])
        self.assertEqual(check("{F9 F1}AbВ ", 5), ["in-place overflow: 6 > 5 bytes"])
        self.assertEqual(check("AAAAA", measure=Measure(lambda _: 9, {})), ["row width 45 > 40 px"])
        self.assertEqual(check("{PLAYER}AAAA"), ["not a keyboard glyph: {PLAYER}"])
        self.assertEqual(check("{COLOR 01}AAAA"), ["not a keyboard glyph: {COLOR 01}"])

    def test_rows_follow_the_keys_table_and_the_row_pointers(self):
        where = editorkeyboard.row_entries(KB, PROF["table"], INV)
        self.assertEqual(where[(0, 0, "keys")], "key_rows:0")
        self.assertEqual(where[(1, 1, "keys")], "key_rows:3")
        self.assertEqual(where[(0, 1, "display")], "s0002010")
        self.assertEqual(where[(2, 0, "display")], "s0002040")
        self.assertNotIn("s0003000", where.values())
        self.assertEqual(len(where), 12)

    def test_document_pages_window_and_order(self):
        doc = data()
        self.assertEqual(doc["first_page"], 1)
        self.assertEqual(doc["opening"], [{"page": 1, "keyboard": 1, "look": 1},
                                         {"page": 0, "keyboard": 0, "look": 2}])
        self.assertEqual(doc["style"], "main")
        self.assertEqual(doc["window"], {"width": 152, "height": 64, "row_height": 16, "top": 1,
                                         "cursor": {"left": 8, "width": 12}})
        self.assertEqual([p["name"] for p in doc["pages"]], ["pack lower", "base upper"])
        self.assertEqual([(k["like"], k["base"], k["columns"]) for k in doc["keyboards"]],
                         [(0, False, 8), (1, True, 3)])
        self.assertEqual([k["columns"] for k in data(cursor=None)["keyboards"]], [8, 8])   # "ABCDEF ."
        # pack columns bound row limits, not the base English
        bounded = Profile({**PROF.data, "keyboard": {**KB, "keyboards": [
            {"name": "pack lower", "like": 0, "columns": 2}, KB["keyboards"][1],
            {"name": "base lower", "like": 0, "base": True}]}}, Path("test.toml"))
        self.assertEqual([(k["columns"], k["column_x"])
                          for k in data(bounded, limits={"key_rows:0": Limit("field", 0, 1, keys=2)})["keyboards"]],
                         [(2, CURSOR[0][:2]), (3, CURSOR[1]), (8, CURSOR[0])])
        # without column_x the English copy keeps 8 keys
        self.assertEqual([k["columns"] for k in data(bounded, limits={"key_rows:0": Limit("field", 0, 1, keys=2)},
                                                     cursor=None)["keyboards"]], [2, 8, 8])
        self.assertEqual([k["column_x"] for k in doc["keyboards"]], CURSOR[:2])
        self.assertEqual([k["column_x"] for k in data(cursor=None)["keyboards"]], [None, None])
        self.assertIsNone(doc["labels"])
        labelled = Profile({**PROF.data, "keyboard": {**KB, "labels": ["G3", "G1", "G2"]}}, Path("test.toml"))
        self.assertEqual(data(labelled)["labels"], ["G3", "G1", "G2"])

    def test_cursor_columns_read_the_count_and_x_tables_of_the_rom(self):
        tables = TABLES + [{"name": "columns", "base": 0x1100, "entry": 1, "index": "keyboard", "refs": [0x208]},
                           {"name": "column_x", "base": 0x1103, "entry": 8, "index": "keyboard", "refs": [0x20C]}]
        rom = Rom(bytes(0x1200))
        rom.data[0x1100:0x1103] = bytes([8, 3, 6])
        rom.data[0x1103:0x111B] = bytes([0, 12, 24, 56, 68, 80, 92, 123, 0, 12, 24, 9, 9, 9, 9, 9,
                                         0, 22, 44, 66, 88, 110, 0, 0])
        prof = Profile({**PROF.data, "keyboard": {**KB, "table": tables}}, Path("test.toml"))
        self.assertEqual(editorkeyboard.cursor_columns(prof, rom), CURSOR)
        self.assertIsNone(editorkeyboard.cursor_columns(PROF, rom))

    def test_pack_keyboard_takes_the_translation_and_names_each_glyph_source(self):
        row = data()["keyboards"][0]["rows"][0]
        self.assertEqual(row["keys"], {"id": "k/key_rows:0", "text": "бВab    ", "name": None})
        self.assertEqual(row["display"]["id"], "k/s0002000")
        keys = [c["key"] for c in row["cells"]]
        self.assertEqual(keys[0], {"char": "б", "code": "42", "source": "glyph"})
        self.assertEqual(keys[1], {"char": "В", "code": "43", "source": "copy", "copy": "BC"})
        self.assertEqual(keys[2], {"char": "a", "code": "D5", "source": "base"})
        self.assertEqual(keys[7], {"char": " ", "code": "00", "source": "base"})
        glyphs = [c["glyph"] for c in row["cells"]]
        self.assertEqual([(g["char"], g["x"], g["width"]) for g in glyphs[:4]],
                         [("б", 11, 5), ("В", 22, 6), ("a", 48, 6), ("b", 64, 6)])
        self.assertIsNone(glyphs[4])
        untranslated = data()["keyboards"][0]["rows"][1]
        self.assertIsNone(untranslated["keys"]["text"])
        self.assertEqual(untranslated["cells"], [])

    def test_english_keyboard_shows_the_base_rows(self):
        row = data()["keyboards"][1]["rows"][0]
        self.assertIsNone(row["keys"]["text"])
        self.assertIsNone(row["display"]["text"])
        self.assertEqual(row["cells"], [])
        self.assertEqual(data()["keyboards"][1]["rows"][1]["keys"], {"id": "k/key_rows:3", "text": None, "name": None})
        bound = Profile({**PROF.data, "keyboard": {**KB, "row_bindings": {"lower_keys_1": "key_rows:0",
                                                                        "lower_display_1": "s0002000"}}},
                        Path("test.toml"))
        first = data(bound)["keyboards"][0]["rows"][0]
        self.assertEqual([first["keys"]["name"], first["display"]["name"]], ["lower_keys_1", "lower_display_1"])

    def test_unknown_characters_have_no_code(self):
        cell = editorkeyboard.key_cell("ж", CM, {0x42}, {0x43: 0xBC})
        self.assertEqual(cell, {"char": "ж", "code": None, "source": "none"})
        self.assertEqual(editorkeyboard.printed("{COLOR 02}ж", MEASURE, CM),
                         [{"char": "ж", "code": None, "x": 0, "width": 0}])

    def test_no_keyboard_no_section(self):
        self.assertIsNone(data(Profile({"rom": {"sha1": "ab" * 20}}, Path("test.toml"))))
        self.assertIsNone(data(inv={"entries": []}))
        no_rows = Profile({**PROF.data, "keyboard": {**KB, "table": TABLES[:1]}}, Path("test.toml"))
        self.assertIsNone(data(no_rows))
