import unittest

from gen3tl.build import keyboard as K
from gen3tl.core.charmap import Charmap
from gen3tl.build.inject import BuildResult, DiffRegistry, FreeSpace, verify
from gen3tl.build.graphics import pixels_to_tiles
from gen3tl.check.render import indexed_png
from gen3tl.core.rom import Rom, ptr_bytes

CM = Charmap()

IMM = [0x100, 0x110]

OPENING_IMM = [0x120, 0x130]

TABLES = [  # name, literal, base, entry, index, text
    ("keys", 0x200, 0x1000, 8, "keyboard", False),
    ("columns", 0x204, 0x1020, 1, "keyboard", False),
    ("rows", 0x208, 0x1040, 4, "keyboard", True),
    ("page_keyboard", 0x20C, 0x1050, 1, "page-keyboard", False),
    ("next_keyboard", 0x210, 0x1054, 1, "next-keyboard", False),
    ("next_label", 0x214, 0x1058, 1, "next-label", False),
]

LABEL_TABLES = [
    ("label_palette", 0x218, 0x1060, 2, "label-palette", False),
    ("label_tile", 0x21C, 0x1066, 2, "label-tile", False),
    ("label_subsprites", 0x224, 0x1090, 8, "label-subsprites", False),
    ("sprite_sheets", 0x220, 0x1070, 8, "sprite-sheets", False),
]

RESERVED = (0x3000, 0x3400)

def base_image() -> Rom:
    d = bytearray(b"\x00" * 0x3000 + b"\xff" * 0x1000)
    for at in IMM:
        d[at:at + 2] = b"\x03\x21"
    for at, value in zip(OPENING_IMM, (1, 0)):
        d[at:at + 2] = bytes([value, 0x21])
    for _, lit, base, *_ in TABLES + LABEL_TABLES:
        d[lit:lit + 4] = ptr_bytes(base)
    d[0x1000:0x1018] = CM.encode("abcdefgh", False) + CM.encode("ABCDEFGH", False) + CM.encode("01234567", False)
    d[0x1020:0x1023] = bytes([8, 8, 6])
    for i, text in enumerate(("a b", "A B", "0 1")):
        s = 0x1800 + 0x10 * i
        d[s:s + 4] = CM.encode(text)
        d[0x1040 + 4 * i:0x1044 + 4 * i] = ptr_bytes(s)
    d[0x1050:0x1053] = bytes([2, 1, 0])     # page -> keyboard (others, upper, lower)
    d[0x1054:0x1057] = bytes([1, 0, 2])     # page -> next keyboard
    d[0x1058:0x105B] = bytes([0, 2, 1])     # page -> label of the next page
    d[0x1060:0x1066] = bytes([10, 0, 11, 0, 12, 0])
    d[0x1066:0x106C] = bytes([20, 0, 21, 0, 22, 0])
    for i, tag in enumerate((20, 21, 22)):
        at, image = 0x1070 + 8 * i, 0x1900 + 0x60 * i
        d[at:at + 8] = ptr_bytes(image) + bytes([96, 0, tag, 0])
        d[image:image + 96] = bytes([i + 1]) * 96
    for i in range(3):
        d[0x1090 + 8 * i:0x1098 + 8 * i] = ptr_bytes(0x1C00) + bytes([192, 0, 0, 0])
    return Rom(d)

def config(keyboards, pages) -> dict:
    declarations = TABLES + (LABEL_TABLES if any("label" in p for p in pages) else [])
    return {"reserved": list(RESERVED), "base_pages": 3, "first_page": 1, "page_count": IMM,
            "opening_keyboards": [1, 0], "opening_keyboard_offsets": OPENING_IMM,
            "keyboards": keyboards, "pages": pages,
            "table": [{"name": n, "refs": [lit], "base": b, "entry": e, "index": ix, "text": tx}
                      for n, lit, b, e, ix, tx in declarations]}

IDENTITY = config([{"name": "lower", "like": 0}, {"name": "upper", "like": 1}, {"name": "others", "like": 2}],
                  [{"name": "others", "keyboard": 2, "look": 0}, {"name": "upper", "keyboard": 1, "look": 1},
                   {"name": "lower", "keyboard": 0, "look": 2}])

FIVE = config(IDENTITY["keyboards"] + [{"name": "LATIN", "like": 1, "base": True},
                                       {"name": "latin", "like": 0, "base": True}],
              IDENTITY["pages"] + [{"name": "LATIN", "keyboard": 3, "look": 1},
                                   {"name": "latin", "keyboard": 4, "look": 2}])

class KeyboardTest(unittest.TestCase):
    def test_identity_pages_reproduce_the_base_tables(self):
        base = base_image()
        patch = K.plan(IDENTITY, base, base)
        for off, blob, what in patch.writes:
            if what.endswith(" text"):
                continue
            t = next(t for t in IDENTITY["table"] if t["name"] == what)
            self.assertEqual(blob, base.read(t["base"], len(blob)), what)

    def test_five_pages(self):
        base = base_image()
        rom = base.copy()
        rom.write(0x1000, b"\x4c" * 8)   # lower keys as translated through the catalog
        patch = K.plan(FIVE, base, rom)
        K.apply(rom, patch)
        self.assertEqual([rom.u8(a) for a in IMM], [5, 5])
        tab = {n: rom.ptr_at(lit) for n, lit, *_ in TABLES}
        self.assertTrue(all(RESERVED[0] <= v < RESERVED[1] for v in tab.values()))
        self.assertEqual(rom.read(tab["keys"], 40), b"\x4c" * 8 + base.read(0x1008, 16) + base.read(0x1008, 8)
                         + base.read(0x1000, 8))
        self.assertEqual(rom.read(tab["columns"], 5), bytes([8, 8, 6, 8, 8]))
        self.assertEqual(rom.read(tab["page_keyboard"], 5), bytes([2, 1, 0, 3, 4]))
        self.assertEqual(rom.read(tab["next_keyboard"], 5), bytes([1, 0, 3, 4, 2]))
        self.assertEqual(rom.read(tab["next_label"], 5), bytes([0, 2, 0, 2, 1]))
        rows = [rom.ptr_at(tab["rows"] + 4 * k) for k in range(5)]
        self.assertEqual(rows[:3], [0x1800, 0x1810, 0x1820])            # catalog rows stay
        for k, like in ((3, 1), (4, 0)):                                 # English rows copied
            self.assertTrue(RESERVED[0] <= rows[k] < RESERVED[1])
            self.assertEqual(rom.read(rows[k], 4), base.read(0x1800 + 0x10 * like, 4))
        self.assertEqual(K.verify(rom, FIVE, patch), [])
        declared = set(IMM) | {a for _, lit, *_ in TABLES for a in range(lit, lit + 4)}
        written = {a for off, blob, _ in patch.writes for a in range(off, off + len(blob))}
        self.assertEqual(patch.allowed, declared | written)
        self.assertTrue(written <= set(range(*RESERVED)))
        res = BuildResult(allowed=set(range(0x1000, 0x1008)))
        guard = DiffRegistry()
        guard.add("keyboard", patch.allowed)
        self.assertEqual(verify(rom, base, res, {}, set(), guard), [])
        self.assertTrue(any("diff guard" in f for f in verify(rom, base, res, {}, set())))
        rom.data[tab["columns"]] = 9
        self.assertTrue(any("columns" in f for f in K.verify(rom, FIVE, patch)))

    def test_pages_can_have_their_own_label_graphics(self):
        base = base_image()
        rom = base.copy()
        rom.write(0x1070, ptr_bytes(0x1A00))   # a translated base sheet stays translated
        pages = [dict(p) for p in FIVE["pages"]]
        pages[3]["label"], pages[4]["label"] = "upper.png", "lower.png"
        labelled = config(FIVE["keyboards"], pages)
        upper = [[6 if 4 <= x < 12 and 1 <= y < 7 else 1 for x in range(24)] for y in range(8)]
        lower = [[2 if 12 <= x < 20 and 2 <= y < 6 else 3 for x in range(24)] for y in range(8)]
        palette = [(i, i, i) for i in range(16)]
        labelled["_label_data"] = {"LATIN": indexed_png(upper, palette, 4).hex(),
                                   "latin": indexed_png(lower, palette, 4).hex()}
        patch = K.plan(labelled, base, rom)
        K.apply(rom, patch)
        tab = {n: rom.ptr_at(lit) for n, lit, *_ in TABLES + LABEL_TABLES}
        self.assertTrue(all(RESERVED[0] <= v < RESERVED[1] for v in tab.values()))
        self.assertEqual(rom.read(tab["next_label"], 5), bytes([0, 2, 3, 4, 1]))
        self.assertEqual(rom.read(tab["label_palette"], 10), bytes([10, 0, 11, 0, 12, 0, 10, 0, 12, 0]))
        self.assertEqual(rom.read(tab["label_tile"], 10), bytes([20, 0, 21, 0, 22, 0, 23, 0, 24, 0]))
        self.assertEqual(rom.read(tab["label_subsprites"], 40),
                         base.read(0x1090, 24) + base.read(0x1090, 8) + base.read(0x10A0, 8))
        sheets = rom.read(tab["sprite_sheets"], 48)
        self.assertEqual(rom.ptr_at(tab["sprite_sheets"]), 0x1A00)
        self.assertEqual(sheets[40:], bytes(8))
        for i, (tag, pixels) in enumerate(((23, upper), (24, lower))):
            entry = tab["sprite_sheets"] + (3 + i) * 8
            self.assertEqual(rom.read(entry + 4, 4), bytes([96, 0, tag, 0]))
            self.assertEqual(rom.read(rom.ptr_at(entry), 96), pixels_to_tiles(pixels, 3, 4, 3))
        self.assertEqual(K.verify(rom, labelled, patch), [])
        labelled["_label_data"]["LATIN"] = indexed_png(upper[:7], palette, 4).hex()
        with self.assertRaisesRegex(K.KeyboardError, "LATIN: label must be an indexed 24x8 PNG"):
            K.plan(labelled, base, rom)

    def test_a_keyboard_bounds_its_own_columns(self):
        base = base_image()
        rom = base.copy()
        bounded = config([{"name": "lower", "like": 0, "columns": 2}] + FIVE["keyboards"][1:4]
                         + [{"name": "latin", "like": 0, "base": True, "columns": 8}], FIVE["pages"])
        patch = K.plan(bounded, base, rom)
        K.apply(rom, patch)
        self.assertEqual(rom.read(rom.ptr_at(0x204), 5), bytes([2, 8, 6, 8, 8]))
        self.assertEqual(K.verify(rom, bounded, patch), [])
        wide = config([dict(k, columns=7) if k["like"] == 2 else k for k in FIVE["keyboards"]], FIVE["pages"])
        with self.assertRaisesRegex(K.KeyboardError, "others: columns = 7 exceeds the 6 of base keyboard 2"):
            K.plan(wide, base, base)

    def test_the_opening_pages_use_the_keyboards_the_screen_prints(self):
        base = base_image()
        others, upper, lower, latin_upper, latin_lower = FIVE["pages"]
        for pages in ([others, latin_upper, latin_lower, upper, lower],   # other keyboards
                      [others, lower, upper, latin_upper, latin_lower]):  # the pair swapped
            with self.subTest(pages=[p["name"] for p in pages]), self.assertRaisesRegex(
                    K.KeyboardError, "opening pages 1 and 2 use keyboards (3 and 4|0 and 1); the naming screen prints"):
                K.plan(config(FIVE["keyboards"], pages), base, base.copy())
        K.check_opening(FIVE)
        with self.assertRaisesRegex(K.KeyboardError, "opening pages"):
            K.check_opening(config(FIVE["keyboards"], [upper]))

    def test_the_opening_pages_use_the_looks_the_fade_in_draws(self):
        base = base_image()
        for slot, look in ((1, 2), (2, 1)):
            pages = [dict(p) for p in FIVE["pages"]]
            pages[slot]["look"] = look
            with self.subTest(slot=slot), self.assertRaisesRegex(
                    K.KeyboardError, f"opening page {slot}: keyboard .* uses look {look}; "
                    f"the fade-in draws base page look {slot}"):
                K.plan(config(FIVE["keyboards"], pages), base, base.copy())

    def test_opening_looks_follow_base_pages_when_the_layout_wraps(self):
        kb = config(IDENTITY["keyboards"], [IDENTITY["pages"][2], IDENTITY["pages"][1]])
        self.assertEqual(K.opening_pages(kb), [{"page": 1, "keyboard": 1, "look": 1},
                                              {"page": 0, "keyboard": 0, "look": 2}])
        K.check_opening(kb)
        kb["first_page"] = 2
        kb["pages"] = [{"name": "lower", "keyboard": 0, "look": 0}, IDENTITY["pages"][0],
                       {"name": "upper", "keyboard": 1, "look": 2}]
        self.assertEqual(K.opening_pages(kb), [{"page": 2, "keyboard": 1, "look": 2},
                                              {"page": 0, "keyboard": 0, "look": 0}])
        K.check_opening(kb)

    def test_base_mismatch_is_refused(self):
        base = base_image()
        base.write(0x204, ptr_bytes(0x1021))
        with self.assertRaises(K.KeyboardError):
            K.plan(FIVE, base, base)

    def test_opening_keyboards_follow_the_profile(self):
        base = base_image()
        kb = dict(FIVE, opening_keyboards=[0, 1], pages=[dict(p) for p in FIVE["pages"]])
        kb["pages"][1]["keyboard"], kb["pages"][2]["keyboard"] = 0, 1
        for at, value in zip(OPENING_IMM, kb["opening_keyboards"]):
            base.write(at, bytes([value, 0x21]))
        self.assertEqual([p["keyboard"] for p in K.opening_pages(kb)], [0, 1])
        K.plan(kb, base, base.copy())

    def test_opening_facts_need_matching_immediate_offsets(self):
        for field, values in (("opening_keyboards", [None, [], [True], [256]]),
                              ("opening_keyboard_offsets", [None, [], [1], [True, 2], [-1, 2]])):
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaisesRegex(K.KeyboardError, field):
                    K.check_opening(dict(FIVE, **{field: value}))

    def test_opening_keyboard_immediate_mismatch_is_refused(self):
        base = base_image()
        base.write(OPENING_IMM[1], b"\x02\x21")
        self.assertEqual(K.check_base(FIVE, base),
                         ["keyboard: opening keyboard 0x130 is not movs rX,#0 (02 21)"])
        base.write(OPENING_IMM[0], b"\x01\x28")
        self.assertIn("keyboard: opening keyboard 0x120 is not movs rX,#1 (01 28)", K.check_base(FIVE, base))

    def test_tables_must_fit_the_reserved_range(self):
        base = base_image()
        small = dict(FIVE, reserved=[0x3000, 0x3020])
        with self.assertRaises(K.KeyboardError):
            K.plan(small, base, base)

class ReservedSpaceTest(unittest.TestCase):
    def test_reserved_range_is_cut_out_of_a_run(self):
        data = b"\x00" * 0x100 + b"\xff" * 0x1000 + b"\x00" * 0x100
        whole = FreeSpace.scan(data)
        self.assertEqual([(r.start, r.end) for r in whole.regions], [(0x110, 0x10F0)])
        cut = FreeSpace.scan(data, reserved=[(0x800, 0x900)])
        self.assertEqual([(r.start, r.end) for r in cut.regions], [(0x110, 0x800), (0x900, 0x10F0)])
        tail = FreeSpace.scan(data, reserved=[(0x1000, 0x1200)])
        self.assertEqual([(r.start, r.end) for r in tail.regions], [(0x110, 0x1000)])
