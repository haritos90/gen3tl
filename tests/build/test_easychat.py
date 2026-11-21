import copy
import hashlib
import unittest
from pathlib import Path

from gen3tl.build import easychat as E
from gen3tl.build.inject import BuildResult, DiffRegistry, diff_positions, verify
from gen3tl.core.rom import ROM_BASE, Rom, ptr_bytes
from gen3tl.lang.langpack import LangPack
from project_fixture import rom_evidence

CM = LangPack.load("ru").charmap()

GROUPS, WORDS, TEXTS, NAMES, LISTS, RECORDS, KEYS = 0x100, 0x200, 0x400, 0x800, 0xC00, 0xD00, 0xD80

TABLES = {"names": {"name": "names", "base": NAMES, "stride": 8, "count": 3, "field_length": 8}}

BUTTONS = ["АБВГД", "ЕЁЖЗИЙ", "КЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ"]

FOLD = {"ё": "е", "Ё": "Е"}

def w(group: int, index: int) -> int:
    return group << 9 | index

BASE_LISTS = [
    [(w(1, 1),)],
    [(w(0, 0), w(2, 0)), (w(1, 4),), (w(1, 5),)],
    [(w(1, 0),), (w(0, 1), w(1, 6))],
    [(w(1, 2),), (w(1, 3),), (w(0, 2),)],
]

ENGLISH = {"names": ["Abra", "Kadabra", "Fir"], "words": ["RUN", "!", "FIR", "HEDGEHOG", "AS", "AHA", "OK"]}

RUSSIAN = {"names": ["Абра", "Кадабра", "Ёлка"], "words": ["БЕГ", "!", "ЕЛЬ", "ёж", "КАК", "АГА", "OK"]}

def image() -> Rom:
    data = bytearray(0x1000) + b"\xff" * 0x1000
    for g, at in ((0, NAMES), (1, WORDS), (2, NAMES)):   # {words, u16 count, u16 enabled}
        data[GROUPS + 8 * g:GROUPS + 8 * g + 4] = ptr_bytes(at)
    blobs = [E._encode(entries) for entries in BASE_LISTS]
    at = LISTS
    for n, b in enumerate(blobs):
        data[at:at + len(b)] = b
        data[RECORDS + 8 * n:RECORDS + 8 * n + 8] = ptr_bytes(at) + (len(b) // 2).to_bytes(4, "little")
        at += len(b)
    data[KEYS:KEYS + 4] = bytes([1, 2, 3, 0])
    rom = Rom(data)
    write_texts(rom, ENGLISH, TEXTS)
    return rom

def write_texts(rom: Rom, texts: dict, at: int) -> None:
    for i, name in enumerate(texts["names"]):
        rom.write(NAMES + 8 * i, CM.encode(name).ljust(8, b"\xff"))
    for i, word in enumerate(texts["words"]):
        rom.write(at + 16 * i, CM.encode(word))
        rom.write(WORDS + 12 * i, ptr_bytes(at + 16 * i))

def entry(base: Rom) -> dict:
    end = LISTS + sum(len(E._encode(e)) for e in BASE_LISTS)
    return {"records": {"at": RECORDS, "count": 4, "sha1": hashlib.sha1(base.read(RECORDS, 32)).hexdigest()},
            "lists": {"at": LISTS, "end": end, "sha1": hashlib.sha1(base.read(LISTS, end - LISTS)).hexdigest()},
            "groups": {"at": GROUPS, "count": 3}, "names": [{"groups": [0, 2], "table": "names"}],
            "limit": 270, "keys": rom_evidence(KEYS, "01020300"), "rows": ["s0000001", "s0000002"],
            "evidence": "A screen lists the words of record n under button n."}

def translated(base: Rom) -> Rom:
    """The base with the Russian words, relocated as the text injection does."""
    rom = base.copy()
    write_texts(rom, RUSSIAN, 0x1000)
    return rom

class AlphabetTest(unittest.TestCase):
    def setUp(self):
        self.base = image()
        self.alpha = E.parse(entry(self.base))
        self.rom = translated(self.base)

    def plan(self, buttons=BUTTONS):
        return E.plan(self.alpha, self.base, self.rom, TABLES, CM, buttons, FOLD)

    def test_base_lists_parse_with_alternatives(self):
        self.assertEqual(E.read_lists(self.base, self.alpha), BASE_LISTS)
        self.assertEqual(E.word_text(self.base, self.alpha, TABLES, CM, w(2, 0)), "Abra")
        self.assertEqual(E.word_text(self.rom, self.alpha, TABLES, CM, w(1, 3)), "ёж")

    def test_words_as_in_the_base_change_nothing(self):
        self.assertEqual(E.changed(self.alpha, self.base, self.base, TABLES, CM), 0)
        self.assertEqual(E.changed(self.alpha, self.base, self.rom, TABLES, CM), 9)   # all but '!' and 'OK'

    def test_every_word_is_filed_once_under_its_initial(self):
        p = self.plan()
        self.assertEqual(p.lists, [
            [(w(1, 1),), (w(1, 6),)],                  # '!', and the Latin 'OK' with no button
            [(w(0, 0), w(2, 0)), (w(1, 5),), (w(1, 0),)],   # Абра, АГА, БЕГ
            [(w(1, 3),), (w(0, 2),), (w(1, 2),)],      # ёж, Ёлка, ЕЛЬ: case and Ё folded
            [(w(0, 1),), (w(1, 4),)],                  # Кадабра, КАК
        ])
        words = [x for entries in p.lists for e in entries for x in e]
        self.assertEqual(sorted(words), sorted(x for entries in BASE_LISTS for e in entries for x in e))

    def test_alternatives_stay_while_their_texts_agree(self):
        p = self.plan()
        self.assertIn((w(0, 0), w(2, 0)), p.lists[1])           # Абра twice: one entry
        self.assertNotIn((w(0, 1), w(1, 6)), p.lists[3])        # Кадабра and OK: two entries
        self.assertEqual(len(p.blob), 2 * (2 + 4 + 2 + 3 + 2))

    def test_identical_texts_keep_one_list_entry(self):
        self.rom.write(0x1000 + 16 * 4, CM.encode("БЕГ"))
        p = self.plan()
        matches = [e for e in p.lists[1] if p.texts[e[0]] == "БЕГ"]
        self.assertEqual(matches, [(w(1, 4),)])
        self.assertNotIn(w(1, 0), [x for entries in p.lists for e in entries for x in e])

    def test_records_hold_the_lists_back_to_back(self):
        p = self.plan()
        rom = self.rom.copy()
        res = E.apply(rom, p)
        at = LISTS
        for n, entries in enumerate(p.lists):
            size = len(E._encode(entries)) // 2
            self.assertEqual((rom.u32(RECORDS + 8 * n), rom.u32(RECORDS + 8 * n + 4)), (ROM_BASE + at, size))
            at += 2 * size
        self.assertEqual(at, LISTS + len(p.blob))
        self.assertEqual(E.read_lists(rom, self.alpha), p.lists)
        self.assertEqual(E.verify(rom, p, res), [])

    def test_guard_class_is_exactly_the_written_bytes(self):
        p = self.plan()
        rom = self.rom.copy()
        res = E.apply(rom, p)
        self.assertEqual(res.allowed, set(range(LISTS, LISTS + len(p.blob))) | set(range(RECORDS, RECORDS + 32)))
        self.assertTrue(set(diff_positions(rom.data, self.rom.data)) <= res.allowed)
        texts = set(diff_positions(self.rom.data, self.base.data))
        text_class = BuildResult(allowed=texts)
        guard = DiffRegistry()
        res.register(guard)
        self.assertEqual(verify(rom, self.base, text_class, {}, set(), guard), [])
        self.assertTrue(any("diff guard" in f for f in verify(rom, self.base, text_class, {}, set())))
        clash = BuildResult(allowed=texts | {RECORDS})
        self.assertTrue(any("easy-chat alphabet bytes are also text bytes" in f
                            for f in verify(rom, self.base, clash, {}, set(), guard)))

    def test_bytes_taken_by_another_class_write_nothing(self):
        rom = self.rom.copy()
        res = E.apply(rom, self.plan(), {RECORDS + 4})
        self.assertEqual((len(res.failures), res.allowed, res.written), (1, set(), False))
        self.assertEqual(bytes(rom.data), bytes(self.rom.data))

    def test_read_back_finds_a_changed_list(self):
        p = self.plan()
        rom = self.rom.copy()
        res = E.apply(rom, p)
        rom.write(LISTS, w(1, 2).to_bytes(2, "little"))
        self.assertTrue(E.verify(rom, p, res))

    def test_stale_base_and_full_buttons_are_refused(self):
        base = self.base.copy()
        base.write(LISTS, b"\x00\x00")
        with self.assertRaisesRegex(E.EasyChatError, "declared lists"):
            E.plan(self.alpha, base, self.rom, TABLES, CM, BUTTONS, FOLD)
        self.alpha.limit = 2
        with self.assertRaisesRegex(E.EasyChatError, "record 1 lists 3 words, more than 2"):
            self.plan()

    def test_pack_buttons_are_checked(self):
        for buttons in (BUTTONS[:2], ["АБ", "ВГ", "ГД"], ["аб", "В", "Г"], ["А", "", "Б"], "АБВ"):
            with self.subTest(buttons=buttons), self.assertRaises(E.EasyChatError):
                self.plan(buttons)

    def test_pack_metadata_takes_strings_of_letters(self):
        import json
        import tempfile

        from gen3tl.lang import language
        with tempfile.TemporaryDirectory() as tmp:
            for code, buttons in (("xa", ["АБ", "В"]), ("xb", "АБВ"), ("xc", ["А", ""])):
                (Path(tmp) / code).mkdir()
                (Path(tmp) / code / "language.json").write_text(json.dumps(
                    {"schema": "2.0.0", "code": code, "easy_chat_alphabet": buttons}), encoding="utf-8")
            self.assertEqual(language.load("xa", Path(tmp)).data["easy_chat_alphabet"], ["АБ", "В"])
            for code in ("xb", "xc"):
                with self.subTest(code=code), self.assertRaises(ValueError):
                    language.load(code, Path(tmp))

    def test_invalid_entries_are_rejected(self):
        good = entry(self.base)
        cases = [("records", dict(good["records"], at=RECORDS + 2)), ("records", {"at": RECORDS, "count": 4}),
                 ("lists", dict(good["lists"], end=LISTS)), ("lists", dict(good["lists"], sha1="X" * 40)),
                 ("lists", dict(good["lists"], at=RECORDS, end=RECORDS + 8)),
                 ("names", [{"groups": [0], "table": "names"}, {"groups": [0], "table": "other"}]),
                 ("names", [{"groups": [3], "table": "names"}]), ("keys", rom_evidence(KEYS, "010203")),
                 ("keys", rom_evidence(KEYS, "0102030004")), ("rows", ["s1", "s1"]), ("limit", 0),
                 ("evidence", " ")]
        for key, value in cases:
            bad = copy.deepcopy(good)
            bad[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(E.EasyChatError):
                E.parse(bad)
        with self.assertRaises(E.EasyChatError):
            E.parse({k: v for k, v in good.items() if k != "rows"})

    def test_key_coverage_is_checked_from_verified_rom_bytes(self):
        for raw in (b"\x01\x02\x03\x04", b"\x01\x02\x02\x00"):
            with self.subTest(raw=raw):
                base = self.base.copy()
                base.write(KEYS, raw)
                declaration = entry(base)
                declaration["keys"] = rom_evidence(KEYS, raw)
                alpha = E.parse(declaration)
                self.assertIn("keys must reach every record", E.stale(base, alpha))
