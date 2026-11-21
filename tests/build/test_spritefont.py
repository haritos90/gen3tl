import copy
import unittest
from types import SimpleNamespace

from gen3tl.build import spritefont as S
from gen3tl.build.font import BACKGROUND, INK, OUTSIDE, SHADOW
from gen3tl.build.inject import BuildResult, DiffRegistry, FreeSpace, verify
from gen3tl.core.rom import Rom, ptr_bytes
from project_fixture import rom_evidence

SHEET, POINTER, AT, CALL = 0x400, 0x200, 0x100, 0x180

ORIGINAL = bytes.fromhex("13002b331b061b0e192b14d8")

CODE = bytes.fromhex("014b9b5c5b0105e0")

ENTRY = {"name": "hud", "style": "outlined", "cell_rows": 8, "sheet": SHEET, "tiles": 4,
         "pointers": [POINTER], "order": "0AB ", "aliases": {":": " "}, "fallback": 0,
         "ink": 1, "shadow": 12, "background": 0,
         "lookup": {"guard": rom_evidence(AT, ORIGINAL.hex()), "code": CODE.hex()},
         "evidence": "A routine draws one tile per character."}

def base_image() -> Rom:
    data = bytearray(0x1000) + b"\xff" * 0x1000
    data[AT:AT + len(ORIGINAL)] = ORIGINAL
    data[POINTER:POINTER + 4] = ptr_bytes(SHEET)
    data[SHEET:SHEET + 4 * 32] = bytes(range(128))
    return Rom(data)

def cell(ink_at: tuple[int, int]) -> list[list[int]]:
    px = [[OUTSIDE] * 16 for _ in range(16)]
    for y in range(8):
        for x in range(5):
            px[y][x] = BACKGROUND
    px[0][0] = SHADOW
    px[ink_at[1]][ink_at[0]] = INK
    return px

def pack():
    """Two letters: one with art, one copying the base A."""
    return SimpleNamespace(chars={"Ж": 0x3A, "А": 0x87, "Ё": 0x8A},
                           sources={"Ж": "glyph", "А": "copy", "Ё": "copy"},
                           copies={"А": 0xBB, "Ё": 0x08},
                           glyphs={"Ж": SimpleNamespace(width=5, pixels=cell((2, 3))),
                                   "Ё": SimpleNamespace(width=5, pixels=cell((1, 1)))})

class SpriteFontTest(unittest.TestCase):
    def setUp(self):
        self.base = base_image()
        self.font = S.parse(copy.deepcopy(ENTRY))

    def test_base_table_reproduces_the_code_choice(self):
        table = S.base_table(self.font)
        self.assertEqual((table[0xA1], table[0xBB], table[0xBC], table[0x00], table[0xF0]), (0, 1, 2, 3, 3))
        self.assertEqual(table[0x3A], 0)   # every other byte: the fallback tile
        self.assertEqual(len(table), 256)

    def test_localized_descriptions_leave_sprite_measurements_unchanged(self):
        window = {"table": "moves", "width": 64, "where": "move panel"}
        pair = {"width": 111, "where": "paired names", "encounters": ["land"],
                "learnsets": ["level_up"], "evidence": "Canvas width 111"}
        for parse, value, text in ((S._windows, [window], "панель приёма"),
                                   (S._pair, pair, "парные названия")):
            translated = copy.deepcopy(value)
            fact = translated[0] if isinstance(translated, list) else translated
            fact["where_ru"] = text
            self.assertEqual(parse(value, "fixture"), parse(translated, "fixture"))
            fact["where_ru"] = 123
            with self.assertRaisesRegex(S.SpriteFontError, "translated descriptions"):
                parse(translated, "fixture")

    def test_plan_adds_drawn_letters_and_reuses_copied_ones(self):
        p = S.plan(self.font, self.base, pack())
        self.assertEqual(p.added, {"Ж": 4, "Ё": 5})
        self.assertEqual(p.copied, {"А": 1})
        self.assertEqual((p.table[0x3A], p.table[0x87], p.table[0x8A]), (4, 1, 5))
        self.assertEqual(p.sheet[:128], self.base.read(SHEET, 128))
        tile = p.sheet[128:160]
        self.assertEqual(tile[0] & 0xF, 12)          # outline
        self.assertEqual(tile[3 * 4 + 1] & 0xF, 1)   # ink at (2, 3)
        self.assertEqual(tile[4 * 4 + 1], 0)         # background is transparent

    def test_letter_without_art_or_drawn_base_fails(self):
        letters = pack()
        del letters.glyphs["Ё"]
        with self.assertRaisesRegex(S.SpriteFontError, "Ё"):
            S.plan(self.font, self.base, letters)

    def test_apply_writes_sheet_table_pointer_and_lookup(self):
        p = S.plan(self.font, self.base, pack())
        rom = self.base.copy()
        res = S.apply(rom, self.base, [p], FreeSpace.scan(bytes(self.base.data)))
        self.assertEqual(res.failures, [])
        pl = res.placements[0]
        self.assertEqual(rom.u32(POINTER), 0x08000000 + pl.sheet)
        self.assertEqual(rom.read(pl.sheet, len(p.sheet)), p.sheet)
        self.assertEqual(rom.read(pl.table, 256), p.table)
        self.assertEqual(rom.read(AT, 12), CODE + ptr_bytes(pl.table))
        self.assertEqual((pl.sheet % 4, pl.table % 4), (0, 0))
        self.assertEqual(S.verify(rom, self.base, [p], res), [])
        guard = DiffRegistry()
        res.register(guard)
        self.assertEqual(verify(rom, self.base, BuildResult(), {}, set(), guard), [])
        self.assertTrue(any("diff guard" in f for f in verify(rom, self.base, BuildResult(), {}, set())))
        rom.write(pl.table + 0xBB, b"\x00")
        self.assertTrue(any("base tile" in f for f in S.verify(rom, self.base, [p], res)))

    def test_stale_code_pointer_or_overlap_writes_nothing(self):
        p = S.plan(self.font, self.base, pack())
        for case in ("code", "pointer", "overlap"):
            with self.subTest(case=case):
                base = self.base.copy()
                taken = set()
                if case == "code":
                    base.write(AT, b"\x00\x00")
                elif case == "pointer":
                    base.write(POINTER, ptr_bytes(SHEET + 32))
                else:
                    taken = {AT + 2}
                rom = base.copy()
                res = S.apply(rom, base, [p], FreeSpace.scan(bytes(base.data)), taken)
                self.assertEqual(len(res.failures), 1)
                self.assertEqual(bytes(rom.data), bytes(base.data))

    def test_invalid_entries_are_rejected(self):
        for key, value in (("order", "0AB C"), ("aliases", {":": "Z"}), ("fallback", 4),
                           ("order", "0Аб"), ("pointers", [POINTER + 2]),
                           ("lookup", {"guard": rom_evidence(AT + 2, ORIGINAL.hex()), "code": CODE.hex()}),
                           ("lookup", {"guard": rom_evidence(AT, ORIGINAL.hex()), "code": CODE.hex() + "00"})):
            with self.subTest(key=key, value=value):
                entry = copy.deepcopy(ENTRY)
                entry[key] = value
                with self.assertRaises(S.SpriteFontError):
                    S.parse(entry)
        entry = copy.deepcopy(ENTRY)
        entry["extra"] = 1
        with self.assertRaises(S.SpriteFontError):
            S.parse(entry)

    def stepped(self) -> dict:
        entry = copy.deepcopy(ENTRY)
        entry.update(advance=4, shared=1, advances={" ": 2},
                     routine={"code": "c046c046", "words": ["table", "advance", "tiles", "step"]},
                     calls=[{"guard": rom_evidence(CALL, "f0b5442204002000"), "code": "004b1847", "entry": 2}])
        return entry

    def test_step_table_routine_and_calls_are_written_and_checked(self):
        base = base_image()
        base.write(CALL, bytes.fromhex("f0b5442204002000"))
        font = S.parse(self.stepped())
        p = S.plan(font, base, pack())
        self.assertEqual((p.advances[0xA1], p.advances[0x00], p.advances[0x3A], p.advances[0x8A], p.advances[0x87]),
                         (4, 2, 4, 4, 4))   # 5-wide art shares a column; copied A steps as A
        self.assertEqual(S.text_width(p, bytes([0xA1, 0x00, 0xBB])), 13)     # base text: the routine's step
        self.assertEqual(S.text_width(p, bytes([0x3A, 0x00, 0xBB])), 11)     # with an added glyph: per byte
        rom = base.copy()
        res = S.apply(rom, base, [p], FreeSpace.scan(bytes(base.data)))
        self.assertEqual(res.failures, [])
        pl = res.placements[0]
        self.assertEqual(rom.read(pl.routine, 20), bytes.fromhex("c046c046") + ptr_bytes(pl.table)
                         + ptr_bytes(pl.advances) + (4).to_bytes(4, "little") + (4).to_bytes(4, "little"))
        self.assertEqual(rom.read(CALL, 8), bytes.fromhex("004b1847") + (0x08000000 + pl.routine + 3).to_bytes(4, "little"))
        self.assertEqual(S.verify(rom, base, [p], res), [])
        guard = DiffRegistry()
        res.register(guard)
        self.assertEqual(verify(rom, base, BuildResult(), {}, set(), guard), [])
        rom.write(pl.advances + 0xA1, b"\x05")
        self.assertTrue(any("changed its step" in f for f in S.verify(rom, base, [p], res)))
        rom.write(CALL + 4, b"\x00")
        self.assertTrue(any("does not reach the routine" in f for f in S.verify(rom, base, [p], res)))

    def test_patches_replace_code_in_place(self):
        base = base_image()
        base.write(CALL, bytes.fromhex("60216021"))
        entry = copy.deepcopy(ENTRY)
        entry["patches"] = [{"guard": rom_evidence(CALL, "6021"), "code": "5a21"},
                            {"guard": rom_evidence(CALL + 2, "6021"), "code": "5a21"}]
        font = S.parse(entry)
        p = S.plan(font, base, pack())
        rom = base.copy()
        res = S.apply(rom, base, [p], FreeSpace.scan(bytes(base.data)))
        self.assertEqual((res.failures, rom.read(CALL, 4)), ([], bytes.fromhex("5a215a21")))
        self.assertEqual(res.placements[0].patches, [CALL, CALL + 2])
        self.assertEqual(S.verify(rom, base, [p], res), [])
        guard = DiffRegistry()
        res.register(guard)
        self.assertEqual(verify(rom, base, BuildResult(), {}, set(), guard), [])
        rom.write(CALL, bytes.fromhex("6021"))
        self.assertTrue(any("patch at" in f for f in S.verify(rom, base, [p], res)))
        stale = base.copy()
        stale.write(CALL, b"\x00\x00")
        self.assertEqual(len(S.apply(stale.copy(), stale, [p], FreeSpace.scan(bytes(stale.data))).failures), 1)
        for bad in ([], [{"guard": rom_evidence(CALL, "6021"), "code": "5a"}],
                    [{"guard": rom_evidence(AT + 2, "6021"), "code": "5a21"}]):
            with self.subTest(bad=bad), self.assertRaises(S.SpriteFontError):
                S.parse({**entry, "patches": bad})

    def test_invalid_step_entries_are_rejected(self):
        cases = [("advance", 0), ("advances", {"Ж": 3}), ("routine", {"code": "c046", "words": []}),
                 ("routine", {"code": "c046c046", "words": ["palette"]}),
                 ("calls", [{"guard": rom_evidence(CALL, "f0b5442204002000"), "code": "004b1847", "entry": 3}]),
                 ("calls", [{"guard": rom_evidence(AT + 4, "f0b5442204002000"), "code": "004b1847", "entry": 0}]),
                 ("calls", [])]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                entry = self.stepped()
                entry[key] = value
                with self.assertRaises(S.SpriteFontError):
                    S.parse(entry)
        entry = self.stepped()
        del entry["shared"]
        with self.assertRaises(S.SpriteFontError):
            S.parse(entry)
