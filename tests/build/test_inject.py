import copy
import unittest

from gen3tl.core.charmap import Charmap
from gen3tl.build.inject import BuildResult, DiffRegistry, inject, verify
from gen3tl.core.rom import Rom, ptr_bytes

CM = Charmap({"Б": 0x37})

def base_image():
    d = bytearray(b"\x00" * 0x2000 + b"\xff" * 0x2000)
    d[0x100:0x100 + 6] = CM.encode("Hello")
    d[0x200:0x200 + 6] = CM.encode("Bye!!")
    d[0x300:0x304] = ptr_bytes(0x100)   # verified ref (table)
    d[0x310:0x314] = ptr_bytes(0x100)   # unverified ref
    d[0x320:0x324] = ptr_bytes(0x200)
    return Rom(d)

INV = {"entries": [
    {"id": "s0000100", "offset": 0x100, "length": 6, "kind": "pointer", "flags": [],
     "refs": [{"at": 0x300, "kind": "table"}, {"at": 0x310, "kind": "unverified"}]},
    {"id": "s0000200", "offset": 0x200, "length": 6, "kind": "pointer", "flags": [],
     "refs": [{"at": 0x320, "kind": "table"}]},
]}

class InjectTest(unittest.TestCase):
    def test_relocate_inplace_and_guard(self):
        base = base_image()
        rom = base.copy()
        texts = {"s0000100": CM.encode("Б" * 40), "s0000200": CM.encode("Ok")}
        res = inject(rom, base, INV, texts, {}, min_run=256)
        modes = {p.id: p.mode for p in res.placements}
        self.assertEqual(modes, {"s0000200": "in-place"})
        self.assertEqual(res.held, {"s0000100": [0x310]})
        self.assertEqual(res.skipped, [("s0000100", "unclassified references: 0x310")])
        self.assertEqual(rom.ptr_at(0x300), 0x100)
        self.assertEqual(rom.ptr_at(0x310), 0x100)
        self.assertEqual(bytes(rom.data[0x100:0x106]), CM.encode("Hello"))
        self.assertEqual(res.free_used, 0)
        self.assertEqual(verify(rom, base, res, texts, set()), [])
        rom.data[0x500] = 0x42                                  # an illegal change
        fails = verify(rom, base, res, texts, set())
        self.assertTrue(any("diff guard" in f for f in fails))

    def test_classified_references_all_follow_relocated_text(self):
        base = base_image()
        rom = base.copy()
        inv = copy.deepcopy(INV)
        inv["entries"][0]["reference_classification"] = [
            {"at": at, "status": "reference"} for at in (0x300, 0x310)]
        texts = {"s0000100": CM.encode("Б" * 40)}
        res = inject(rom, base, inv, texts, {})
        moved, = res.placements
        self.assertGreaterEqual(moved.new, 0x2000)
        self.assertEqual(moved.pointers, [0x300, 0x310])
        self.assertEqual([rom.ptr_at(at) for at in moved.pointers], [moved.new] * 2)
        self.assertEqual((res.held, res.skipped), ({}, []))
        self.assertEqual(verify(rom, base, res, texts, set()), [])

    def test_non_reference_and_unread_words_are_preserved(self):
        for status in ("non-reference", "unread"):
            with self.subTest(status=status):
                base = base_image()
                rom = base.copy()
                inv = copy.deepcopy(INV)
                inv["entries"][0]["reference_classification"] = [
                    {"at": 0x300, "status": "reference"}, {"at": 0x310, "status": status}]
                texts = {"s0000100": CM.encode("Б" * 40)}
                res = inject(rom, base, inv, texts, {}, reuse_slots=True)
                moved, = res.placements
                self.assertEqual(moved.pointers, [0x300])
                self.assertEqual(moved.kept_refs, [0x310])
                self.assertEqual(rom.ptr_at(0x300), moved.new)
                self.assertEqual(rom.ptr_at(0x310), 0x100)
                self.assertEqual(bytes(rom.data[0x100:0x106]), CM.encode("Hello"))
                self.assertEqual(verify(rom, base, res, texts, set()), [])
                rom.put_u32(0x310, 0)
                self.assertTrue(any("retained word" in error for error in verify(rom, base, res, texts, set())))

    def test_missing_classification_holds_even_a_legacy_verified_position(self):
        base = base_image()
        rom = base.copy()
        inv = copy.deepcopy(INV)
        inv["entries"][0]["reference_classification"] = [{"at": 0x310, "status": "non-reference"}]
        res = inject(rom, base, inv, {"s0000100": CM.encode("Б" * 40)}, {})
        self.assertEqual(res.held, {"s0000100": [0x300]})
        self.assertEqual(rom.data, base.data)

    def test_unclassified_reference_does_not_prevent_a_fitting_write(self):
        base = base_image()
        rom = base.copy()
        texts = {"s0000100": CM.encode("ББ")}
        res = inject(rom, base, INV, texts, {})
        self.assertEqual(res.placements[0].mode, "in-place")
        self.assertEqual(res.held, {})
        self.assertEqual(verify(rom, base, res, texts, set()), [])

def computed_image():
    d = bytearray(b"\x00" * 0x2000 + b"\xff" * 0x2000)
    d[0x100:0x100 + 6] = CM.encode("Trnr!")    # derived: 0x140 - 0x40
    d[0x140:0x140 + 6] = CM.encode("Link!")    # anchor: the pool holds it
    d[0x146:0x146 + 4] = CM.encode("Ok!")
    d[0x400:0x404] = ptr_bytes(0x140)           # pool word the code adds to
    d[0x410:0x414] = ptr_bytes(0x100)           # a plain pool word
    d[0x420:0x424] = ptr_bytes(0x146)
    return Rom(d)

COMPUTED_INV = {"entries": [
    {"id": "s0000100", "offset": 0x100, "length": 6, "kind": "pointer", "flags": [],
     "refs": [{"at": 0x410, "kind": "pool"}]},
    {"id": "s0000140", "offset": 0x140, "length": 6, "kind": "pointer", "flags": [],
     "refs": [{"at": 0x400, "kind": "pool"}]},
    {"id": "s0000146", "offset": 0x146, "length": 4, "kind": "pointer", "flags": [],
     "refs": [{"at": 0x420, "kind": "pool"}]},
]}

GROUP = [{"pointer": 0x400, "ids": ["s0000140", "s0000100"]}]

class ComputedGroupTest(unittest.TestCase):
    def read(self, rom, off):
        return CM.decode(rom.data, off)[0]

    def test_derived_string_follows_relocated_anchor(self):
        base = computed_image()
        rom = base.copy()
        texts = {"s0000140": CM.encode("Б" * 20), "s0000100": CM.encode("Б" * 30 + "!")}
        res = inject(rom, base, COMPUTED_INV, texts, {}, computed=GROUP)
        anchor = rom.ptr_at(0x400)
        self.assertGreaterEqual(anchor, 0x2000)
        self.assertEqual(self.read(rom, anchor), "Б" * 20)
        self.assertEqual(self.read(rom, anchor - 0x40), "Б" * 30 + "!")   # what the code shows
        self.assertEqual(rom.ptr_at(0x410), anchor - 0x40)                # plain ref follows
        self.assertEqual({p.mode for p in res.placements}, {"group"})
        self.assertEqual(verify(rom, base, res, texts, set()), [])
        rom.data[0x400:0x404] = ptr_bytes(anchor + 1)
        self.assertTrue(any("0x400" in f for f in verify(rom, base, res, texts, set())))

    def test_untranslated_member_is_copied(self):
        base = computed_image()
        rom = base.copy()
        texts = {"s0000140": CM.encode("Б" * 20)}
        res = inject(rom, base, COMPUTED_INV, texts, {}, computed=GROUP)
        anchor = rom.ptr_at(0x400)
        self.assertEqual(self.read(rom, anchor - 0x40), "Trnr!")
        self.assertEqual(rom.ptr_at(0x410), 0x100)
        self.assertEqual(verify(rom, base, res, texts, set()), [])

    def test_unclassified_member_holds_the_entire_group(self):
        base = computed_image()
        base.write(0x430, ptr_bytes(0x100))
        rom = base.copy()
        inv = copy.deepcopy(COMPUTED_INV)
        inv["entries"][0]["refs"].append({"at": 0x430, "kind": "unverified"})
        texts = {"s0000140": CM.encode("Б" * 20)}
        res = inject(rom, base, inv, texts, {}, computed=GROUP)
        self.assertEqual(res.held, {"s0000140": [0x430]})
        self.assertEqual(res.placements, [])
        self.assertEqual(rom.data, base.data)
        self.assertEqual(verify(rom, base, res, texts, set()), [])

    def test_member_longer_than_its_gap_stays_english(self):
        base = computed_image()
        rom = base.copy()
        texts = {"s0000140": CM.encode("Б" * 20), "s0000100": CM.encode("Б" * 0x40)}
        res = inject(rom, base, COMPUTED_INV, texts, {}, computed=GROUP)
        anchor = rom.ptr_at(0x400)
        self.assertEqual(self.read(rom, anchor), "Б" * 20)
        self.assertEqual(self.read(rom, anchor - 0x40), "Trnr!")
        self.assertEqual([s for s, _ in res.skipped], ["s0000100"])
        self.assertEqual(verify(rom, base, res, texts, set()), [])

    def test_group_that_fits_stays_in_place(self):
        base = computed_image()
        rom = base.copy()
        texts = {"s0000140": CM.encode("ББ!"), "s0000100": CM.encode("БББ")}
        res = inject(rom, base, COMPUTED_INV, texts, {}, computed=GROUP)
        self.assertEqual(rom.ptr_at(0x400), 0x140)
        self.assertEqual({p.mode for p in res.placements}, {"in-place"})
        self.assertEqual(self.read(rom, 0x100), "БББ")

    def test_group_without_translations_is_left_alone(self):
        base = computed_image()
        rom = base.copy()
        inv = {"entries": COMPUTED_INV["entries"][2:]}     # members not catalogued
        texts = {"s0000146": CM.encode("Б" * 10)}
        res = inject(rom, base, inv, texts, {}, computed=GROUP)
        self.assertEqual(rom.ptr_at(0x400), 0x140)
        self.assertEqual(verify(rom, base, res, texts, set()), [])

    def test_misdeclared_group_fails(self):
        base = computed_image()
        rom = base.copy()
        texts = {"s0000146": CM.encode("Б" * 20)}
        res = inject(rom, base, COMPUTED_INV, texts, {},
                     computed=[{"pointer": 0x400, "ids": ["s0000100", "s0000146"]}])   # 0x400 holds 0x140
        self.assertTrue(any("0x400" in f for f in verify(rom, base, res, texts, set())))

def table_image():
    d = bytearray(b"\x00" * 0x2000 + b"\xff" * 0x2000)
    for at, text in ((0x100, "Cheri"), (0x110, "Lum"), (0x120, "Oran")):
        d[at:at + len(text) + 1] = CM.encode(text)
    d[0x400:0x404] = ptr_bytes(0x100)   # the table base
    d[0x410:0x414] = ptr_bytes(0x100)   # a plain pool word
    return Rom(d)

TABLE_INV = {"entries": [
    {"id": "s0000100", "offset": 0x100, "length": 6, "kind": "pointer", "flags": [],
     "refs": [{"at": 0x400, "kind": "pool"}, {"at": 0x410, "kind": "pool"}]},
    {"id": "s0000110", "offset": 0x110, "length": 4, "kind": "pointer", "flags": [],
     "refs": [{"at": 0x400, "kind": "table-base"}]},
]}

TABLE = [{"pointers": [0x400], "id": "s0000100", "stride": 0x10, "count": 3, "evidence": "test"}]

class TableBaseTest(unittest.TestCase):
    def read(self, rom, off):
        return CM.decode(rom.data, off)[0]

    def test_longer_base_string_stays_english(self):
        base = table_image()
        rom = base.copy()
        texts = {"s0000100": CM.encode("Б" * 20)}
        res = inject(rom, base, TABLE_INV, texts, {}, table_bases=TABLE)
        self.assertEqual((rom.ptr_at(0x400), rom.ptr_at(0x410)), (0x100, 0x100))
        self.assertEqual([self.read(rom, 0x100 + 0x10 * i) for i in range(3)], ["Cheri", "Lum", "Oran"])
        self.assertEqual([s for s, _ in res.skipped], ["s0000100"])
        self.assertIn("table base at 0x400", res.skipped[0][1])
        self.assertEqual(verify(rom, base, res, texts, set()), [])

    def test_member_that_fits_is_written_in_place(self):
        base = table_image()
        rom = base.copy()
        texts = {"s0000100": CM.encode("ББ"), "s0000110": CM.encode("БББ")}
        res = inject(rom, base, TABLE_INV, texts, {}, table_bases=TABLE)
        self.assertEqual({p.mode for p in res.placements}, {"in-place"})
        self.assertEqual([self.read(rom, 0x100 + 0x10 * i) for i in range(3)], ["ББ", "БББ", "Oran"])
        self.assertEqual(rom.ptr_at(0x400), 0x100)
        self.assertEqual(verify(rom, base, res, texts, set()), [])

    def test_stale_inventory_still_keeps_the_layout(self):
        base = table_image()
        rom = base.copy()
        texts = {"s0000110": CM.encode("Б" * 8)}   # the inventory knows no table-base
        inv = {"entries": [dict(TABLE_INV["entries"][1], refs=[{"at": 0x300, "kind": "table"}])]}
        rom.data[0x300:0x304] = base.data[0x300:0x304] = ptr_bytes(0x110)
        res = inject(rom, base, inv, texts, {}, table_bases=TABLE)
        self.assertEqual((rom.ptr_at(0x300), self.read(rom, 0x110)), (0x110, "Lum"))
        self.assertEqual([s for s, _ in res.skipped], ["s0000110"])

    def test_changed_pool_word_fails_verification(self):
        base = table_image()
        rom = base.copy()
        res = inject(rom, base, TABLE_INV, {}, {}, table_bases=TABLE)
        rom.data[0x400:0x404] = ptr_bytes(0x2000)
        self.assertTrue(any("0x400" in f for f in verify(rom, base, res, {}, set())))

    def test_misdeclared_table_fails(self):
        base = table_image()
        rom = base.copy()
        res = inject(rom, base, TABLE_INV, {}, {}, table_bases=[dict(TABLE[0], pointers=[0x404])])
        self.assertTrue(any("0x404" in f for f in verify(rom, base, res, {}, set())))

    # a declared field length is the room
    # - 'Lum' has 4 English bytes, its field 8
    # - longer than the field: English, reported
    def test_declared_field_holds_a_longer_member(self):
        decl = [dict(TABLE[0], length=8, length_evidence="name[8]")]
        base = table_image()
        rom = base.copy()
        texts = {"s0000110": CM.encode("Б" * 7)}
        res = inject(rom, base, TABLE_INV, texts, {}, table_bases=decl)
        self.assertEqual([(p.id, p.mode) for p in res.placements], [("s0000110", "in-place")])
        self.assertEqual([self.read(rom, 0x100 + 0x10 * i) for i in range(3)], ["Cheri", "Б" * 7, "Oran"])
        self.assertEqual((rom.ptr_at(0x400), rom.data[0x118]), (0x100, 0x00))
        self.assertEqual(verify(rom, base, res, texts, set()), [])
        rom = base.copy()
        texts = {"s0000110": CM.encode("Б" * 8)}
        res = inject(rom, base, TABLE_INV, texts, {}, table_bases=decl)
        self.assertEqual(res.skipped, [("s0000110", "table base at 0x400: 9 bytes, slot 8")])
        self.assertEqual(self.read(rom, 0x110), "Lum")

    def test_misdeclared_field_length_fails(self):
        base = table_image()
        rom = base.copy()
        res = inject(rom, base, TABLE_INV, {}, {}, table_bases=[dict(TABLE[0], length=3, length_evidence="x")])
        self.assertTrue(any("0x100 does not end within its 3-byte field" in f
                            for f in verify(rom, base, res, {}, set())))

class FieldBytesTest(unittest.TestCase):
    """Unterminated fields: filled with the table's pad byte."""

    def test_spaces_by_default(self):
        from gen3tl.build.inject import field_bytes
        self.assertEqual(field_bytes(CM.encode("ab"), 4), CM.encode("ab", terminate=False) + b"\x00\x00")

    def test_terminator_pad_ends_the_text(self):
        from gen3tl.build.inject import field_bytes
        self.assertEqual(field_bytes(CM.encode(""), 1, 0xFF), b"\xff")
        self.assertEqual(field_bytes(CM.encode("s"), 1, 0xFF), CM.encode("s", terminate=False))

class DiffRegistryTest(unittest.TestCase):
    """The diff-guard registry: registration, exclusive overlaps and the allowed rule."""

    def test_registration_keeps_order_spans_and_rule(self):
        guard = DiffRegistry()
        guard.add("keyboard", {0x10, 0x11})
        guard.add("graphics", {0x12}, "exclusive")
        self.assertEqual([(c.name, c.rule) for c in guard.classes],
                         [("keyboard", "exclusive"), ("graphics", "exclusive")])
        self.assertEqual(guard.classes[0].spans, {0x10, 0x11})

    def test_two_exclusive_classes_report_the_overlap(self):
        base = base_image()
        guard = DiffRegistry()
        guard.add("keyboard", {0x10, 0x11})
        guard.add("graphics", {0x11, 0x12})
        fails = verify(base.copy(), base, BuildResult(), {}, set(), guard)
        self.assertIn("diff guard: 1 graphics bytes are also keyboard bytes, first at 0x11", fails)

    def test_allowed_rule_skips_overlap_checks(self):
        base = base_image()
        guard = DiffRegistry()
        guard.add("keyboard", {0x10})
        guard.add("overlay", {0x10}, "allowed")
        self.assertEqual(verify(base.copy(), base, BuildResult(), {}, set(), guard), [])

    def test_shared_rule_skips_only_the_text_overlap(self):
        base = base_image()
        guard = DiffRegistry()
        guard.add("keyboard", {0x10})
        guard.add("window", {0x10, 0x20}, "shared")
        fails = verify(base.copy(), base, BuildResult(allowed={0x20}), {}, set(), guard)
        self.assertIn("diff guard: 1 window bytes are also keyboard bytes, first at 0x10", fails)
        self.assertFalse(any("window bytes are also text bytes" in f for f in fails))
