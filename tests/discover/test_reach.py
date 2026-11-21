import unittest

from gen3tl.core.charmap import Charmap
from gen3tl.discover.maps import CMD_LEN
from gen3tl.discover.reach import Code, DataStudy, Study, Table, Thumb, apply_code_refs, apply_observations, changes_report, declared_tables, movement_spans, pool_load, ref_command, report, unused_entries
from gen3tl.core.rom import Rom, ptr_bytes
from gen3tl.discover.scan import TRAINERBATTLE_TEXT_OFFSETS, pointer_index

CM = Charmap()

class ObservationTest(unittest.TestCase):
    def test_native_recovery_overrides_static_failure_with_evidence(self):
        prior = [{"kind": "table-orphan", "at": 0x200}]
        records = {"s1": {"class": "unreachable", "evidence": prior},
                   "s2": {"class": "unreachable", "evidence": []}}
        rules = [{"ids": ["s1"], "at": 0x100, "evidence": "native defeat returns home"}]
        apply_observations(records, rules, {"s1", "s2"})
        self.assertEqual(records["s1"]["class"], "reachable")
        self.assertEqual(records["s1"]["static_class"], "unreachable")
        self.assertEqual(records["s1"]["evidence"][:-1], prior)
        self.assertEqual(records["s1"]["evidence"][-1]["kind"], "observed")
        self.assertEqual(records["s2"]["class"], "unreachable")
        with self.assertRaisesRegex(ValueError, "absent from inventory"):
            apply_observations(records, rules, {"s2"})

def msgbox(text: int) -> bytes:
    """loadpointer 0, text; callstd 6"""
    return b"\x0f\x00" + ptr_bytes(text) + b"\x09\x06"

class Image:
    """A fixture ROM: padding 0xFF, scripts, texts, a few THUMB literal pools."""

    def __init__(self, size=0x4000):
        self.d = bytearray(b"\xff" * size)
        self.not_code = bytearray(size)
        self.next_text = 0x3000

    def put(self, off, blob):
        self.d[off:off + len(blob)] = blob

    def text(self, s="Hello."):
        off = self.next_text
        blob = CM.encode(s)
        self.put(off, blob)
        self.not_code[off:off + len(blob)] = b"\x01" * len(blob)
        self.next_text += 0x40
        return off

    def pool(self, at, target):
        """push {lr}; ldr r0, [pc, #4]; pop {pc}; the pool word at `at` + 8 holds the target."""
        self.put(at, b"\x00\xb5\x01\x48\x00\xbd\x00\x00" + ptr_bytes(target))
        return at + 8

    def code(self, cmd_len=None):
        return Code(self.d, cmd_len, None, self.not_code)

    def study(self, **kw):
        return Study(self.d, pointer_index(Rom(self.d)), self.code(kw.pop("cmd_len", None)), **kw)

class StartWalkTest(unittest.TestCase):
    def test_entry_after_padding(self):
        im = Image()
        t = im.text()
        im.put(0x1000, b"\x6a\x5a" + msgbox(t) + b"\x6c\x02")   # lock faceplayer msgbox release end
        st = im.code().starts(0x1002)
        self.assertEqual((st.entry, st.ambiguous), (0x1000, False))
        self.assertEqual(st.starts, [0x1000, 0x1001, 0x1002])

    def test_label_after_goto_is_its_own_start(self):
        im = Image()
        t = im.text()
        # script: goto_if 1 -> label; goto 0x2000; label: msgbox; end
        im.put(0x1000, b"\x06\x01" + ptr_bytes(0x100B) + b"\x05" + ptr_bytes(0x2000) + msgbox(t) + b"\x02")
        st = im.code().starts(0x100B)
        self.assertEqual((st.entry, st.starts, st.ambiguous), (0x100B, [0x100B], False))
        self.assertEqual(im.code().starts(0x1000).entry, 0x1000)

    def test_entry_after_end_and_filler(self):
        im = Image()
        t = im.text()
        im.put(0x1000, b"\x6c\x02" + msgbox(t) + b"\x02")         # end, then the next script
        st = im.code().starts(0x1002)
        self.assertEqual((st.entry, st.ambiguous), (0x1002, False))
        im.put(0x1100, b"\xff\xaa" + msgbox(t) + b"\x02")          # FF AA filler before a script
        st = im.code().starts(0x1102)
        self.assertEqual((st.entry, st.ambiguous), (0x1102, False))
        im.put(0x1200, b"\x6c\x09" + msgbox(t) + b"\x02")         # no boundary after 09 (gotostd's opcode)
        self.assertTrue(im.code().starts(0x1202).ambiguous)

    def test_trainerbattle_length(self):
        im = Image()
        t1, t2, t3 = im.text(), im.text(), im.text()
        # trainerbattle 0, trainer 1: two texts, then msgbox
        im.put(0x1000, b"\x5c\x00\x01\x00\x00\x00" + ptr_bytes(t1) + ptr_bytes(t2) + msgbox(t3) + b"\x02")
        self.assertEqual(im.code().starts(0x100E).entry, 0x1000)
        self.assertEqual(ref_command(im.d, {"at": 0x100A, "kind": "script:trainerbattle"},
                                     dict(TRAINERBATTLE_TEXT_OFFSETS)), 0x1000)

    def test_unknown_length_makes_an_ambiguous_start(self):
        im = Image()
        t = im.text()
        im.put(0x1000, b"\x6a\x8a" + msgbox(t) + b"\x02")          # 0x8A: four bytes in FRLG
        st = im.code().starts(0x1002)
        self.assertTrue(st.ambiguous)
        one = dict(CMD_LEN)
        one[0x8A] = 1                                              # a one-byte engine extension
        st = im.code(one).starts(0x1002)
        self.assertEqual((st.entry, st.ambiguous), (0x1000, False))

    def test_strings_stop_the_walk(self):
        im = Image()
        t = im.text("Hi, Mom!")
        c = t + len(CM.encode("Hi, Mom!"))
        im.put(c, msgbox(t) + b"\x02")                             # a script right after a text
        self.assertEqual(min(im.code().chain(c)), c)
        self.assertEqual(im.code().starts(c).entry, c)

class PointerScanTest(unittest.TestCase):
    def test_pool_word(self):
        im = Image()
        word = im.pool(0x100, 0x1000)
        self.assertEqual(pool_load(im.d, word), 0x102)
        self.assertEqual(im.study().place(word), ("pool", None))

    def test_pool_load_rejects_pointer_run_edges(self):
        for neighbor in (-4, 4):
            with self.subTest(neighbor=neighbor):
                im = Image(0x10000)
                im.put(0x800, ptr_bytes(0x4802))
                im.put(0x800 + neighbor, ptr_bytes(0x3000))
                im.put(0x80C, ptr_bytes(0x1000))
                self.assertIsNone(pool_load(im.d, 0x80C))

    def test_pool_load_continues_after_pointer_data(self):
        im = Image(0x10000)
        im.put(0x7F0, b"\x06\x48")
        im.put(0x800, ptr_bytes(0x4802) + ptr_bytes(0x3000))
        im.put(0x80C, ptr_bytes(0x1000))
        self.assertEqual(pool_load(im.d, 0x80C), 0x7F0)

    def test_pool_load_ignores_out_of_rom_neighbor(self):
        im = Image(0x10000)
        im.put(0x800, ptr_bytes(0x4802) + ptr_bytes(0x10000))
        self.assertEqual(pool_load(im.d, 0x80C), 0x800)

    def test_script_operands(self):
        im = Image()
        t = im.text()
        im.put(0x1000, b"\x05" + ptr_bytes(0x1100))                       # goto
        im.put(0x1010, b"\x06\x01" + ptr_bytes(0x1100) + b"\x02")         # goto_if
        im.put(0x1020, b"\x0f\x00" + ptr_bytes(0x1100) + b"\x23" + ptr_bytes(0x8001) + b"\x02")
        im.put(0x1100, msgbox(t) + b"\x02")
        st = im.study()
        self.assertEqual(st.place(0x1001), ("goto", 0x1000))
        self.assertEqual(st.place(0x1012), ("goto_if", 0x1010))
        self.assertEqual(st.place(0x1022), ("loadpointer", 0x1020))

    def test_applymovement_is_data_not_loadpointer(self):
        im = Image()
        # lock, applymovement, waitmovement, end; "4F 0F 00" reads as loadpointer
        im.put(0x1000, b"\x6a\x4f\x0f\x00" + ptr_bytes(0x1200) + b"\x51\x00\x00\x02")
        im.put(0x1200, b"\x10\x10\xfe")
        self.assertEqual(im.study().place(0x1004), ("data-operand", 0x1001))

    def test_tables_and_records(self):
        im = Image()
        im.put(0x800, ptr_bytes(0x1000) + ptr_bytes(0x1100))              # pointer array
        im.put(0x900, ptr_bytes(0x1200) + b"\x05\x00\x00\x00" + ptr_bytes(0x1300) + b"\x06\x00\x00\x00"
               + ptr_bytes(0x1400) + b"\x07\x00\x00\x00")                 # {script, id} records
        im.put(0x2000, b"\x00\x00\x00\x00" + ptr_bytes(0x1500) + b"\x00\x00\x00\x00")   # lone word
        im.put(0x2101, ptr_bytes(0x1600))                                  # unaligned
        st = im.study()
        self.assertEqual(st.place(0x804)[0], "table")
        self.assertEqual(st.place(0x908)[0], "record")
        self.assertEqual(st.record_stride(0x908), 8)
        self.assertEqual(st.place(0x2004)[0], "aligned")
        self.assertEqual(st.place(0x2101)[0], "unaligned")

    def test_straddle_and_text_are_ignored(self):
        im = Image(0x90000)
        # two aligned pointers: the window at +3 reads 0x08001008
        im.put(0x800, ptr_bytes(0x1234) + ptr_bytes(0x80010))
        t = im.text("ABCD")
        st = im.study()
        self.assertEqual(st.place(0x803)[0], "straddle")
        self.assertEqual(st.place(t)[0], "in-string")

    def test_movement_data(self):
        im = Image()
        # applymovement player: walk, walk, end; a script follows
        im.put(0x1000, b"\x6a\x4f\xff\x00" + ptr_bytes(0x1200) + b"\x51\x00\x00\x02")
        im.put(0x1200, b"\x13\xc5\xc5\x13\xfe\xff\x6a\x02")
        im.put(0x1300, b"\x4f\x00\x90" + ptr_bytes(0x1400))         # object id 0x9000: no applymovement
        im.put(0x1400, b"\x13\x13\xfe")
        self.assertEqual(movement_spans(im.d), {0x1200: 0x1204})

    def test_map_script_tables(self):
        im = Image()
        # map script list {type 2, table}; table {var, value, script}
        im.put(0x900, b"\x0c\x50\x05\x00" + ptr_bytes(0x1000) + b"\x00\x00")
        im.put(0x980, b"\x02" + ptr_bytes(0x900) + b"\x00")
        st = im.study()
        self.assertEqual(st.place(0x904)[0], "map-orphan")       # the list has no pointer
        im.put(0xA00, ptr_bytes(0x980))                           # something points at the list
        self.assertEqual(im.study().place(0x904)[0], "map-table")
        word = im.pool(0x100, 0x1000)                             # 0xBD00 before it: no variable
        self.assertIsNone(im.study().map_table(word))

    def test_orphan_table(self):
        im = Image()
        im.put(0x900, ptr_bytes(0x1200) + ptr_bytes(0x1300))
        st = im.study()
        self.assertEqual(st.evidence(0x904, 0x1300)["kind"], "table-orphan")
        im.put(0x2000, ptr_bytes(0x900))                          # pointed at, but not from code
        self.assertEqual(im.study().evidence(0x904, 0x1300)["kind"], "table-unreferenced")

    def test_table_root_needs_a_literal_pool(self):
        im = Image()
        im.put(0x800, b"\x00" * 8 + ptr_bytes(0x1000) + ptr_bytes(0x1100))   # null entries, then pointers
        im.put(0x900, ptr_bytes(0x1200) + ptr_bytes(0x1300))
        im.pool(0x100, 0x800)                                                  # code points at the table
        st = im.study()
        self.assertEqual(st.table_root(0x80C, 4)["at"], 0x108)
        self.assertIsNone(st.table_root(0x904, 4))
        im.put(0xA00, ptr_bytes(0x900) + ptr_bytes(0x800))                    # a table of tables
        im.pool(0x140, 0xA00)
        st = im.study()
        self.assertEqual(st.table_root(0x904, 4)["via"]["at"], 0x148)
        self.assertIsNone(st.table_root(0x904, 4, depth=0))

class ClassifyTest(unittest.TestCase):
    def setUp(self):
        im = self.im = Image()
        self.t = [im.text(f"Line {i}.") for i in range(10)]
        t = self.t
        # A: a map root loading script D for a callasm
        im.put(0x1000, b"\x0f\x00" + ptr_bytes(0x1300) + b"\x23" + ptr_bytes(0x8001) + b"\x02")
        # B: started from code (literal pool); goes to C
        im.pool(0x100, 0x1100)
        im.put(0x1100, b"\x6a" + b"\x05" + ptr_bytes(0x1200))
        im.put(0x1200, msgbox(t[0]) + b"\x02")                          # C
        im.put(0x1300, msgbox(t[1]) + b"\x02")                          # D
        # E: no pointer; goes to F
        im.put(0x1400, b"\x6a\x05" + ptr_bytes(0x1500))
        im.put(0x1500, msgbox(t[2]) + b"\x02")                          # F
        # G: only an unaligned data word points at it
        im.put(0x2001, ptr_bytes(0x1600) + b"\x11\x22\x33")
        im.put(0x1600, msgbox(t[3]) + b"\x02")
        # H: in a table that code points at.
        # I: in a data-pointed table; L: in none.
        im.put(0x800, ptr_bytes(0x1700) + ptr_bytes(0x1710))
        im.pool(0x140, 0x800)
        im.put(0x1700, msgbox(t[4]) + b"\x02")
        im.put(0x900, ptr_bytes(0x1800) + ptr_bytes(0x1810))
        im.put(0x2200, b"\x00" * 4 + ptr_bytes(0x900) + b"\x00" * 4)
        im.put(0x1800, msgbox(t[5]) + b"\x02")
        im.put(0x940, ptr_bytes(0x1A00) + ptr_bytes(0x1A10))
        im.put(0x1A00, msgbox(t[9]) + b"\x02")
        # J: an unknown command before it, nothing points at it
        im.put(0x18FF, b"\xe5" + msgbox(t[6]) + b"\x02")
        # K: a known table field the inventory read as loadpointer
        im.put(0xA00, b"\x00\x00\x0f\x00" + ptr_bytes(t[7]) + b"\x00\x00\x0f\x00" + ptr_bytes(t[8]))
        im.pool(0x180, 0xA00)
        self.tables = [Table("dex", 0xA00, 8, 0xA10, "profile table", "Pokedex entries")]

    def entry(self, i, at, kind="script:loadpointer"):
        return {"id": f"s{self.t[i]:07X}", "offset": self.t[i], "refs": [{"at": at, "kind": kind}]}

    def test_classes_and_evidence(self):
        st = self.im.study(roots={0x1000: "0.0 npc"}, tables=self.tables)
        entries = [self.entry(0, 0x1202), self.entry(1, 0x1302), self.entry(2, 0x1502), self.entry(3, 0x1602),
                   self.entry(4, 0x1702), self.entry(5, 0x1802), self.entry(6, 0x1902), self.entry(7, 0xA04),
                   self.entry(9, 0x1A02)]
        out = st.classify(entries)
        cls = {e["id"]: out[e["id"]]["class"] for e in entries}
        ids = [e["id"] for e in entries]
        self.assertEqual([cls[i] for i in ids], [
            "reachable",           # C: goto from B, B from a literal pool
            "reachable",           # D: loadpointer in the map root A
            "unreachable",         # F: only the goto in E, which nothing points at
            "possibly reachable",  # G: an unaligned word in data
            "reachable",           # H: a table code points at
            "possibly reachable",  # I: a table only data points at
            "possibly reachable",  # J: ambiguous start
            "reachable",           # K: a field of a known table
            "unreachable",         # L: a table nothing points at
        ])
        c = out[ids[0]]["evidence"]
        self.assertEqual([x["kind"] for x in c], ["goto", "pool"])
        self.assertEqual((c[0]["from"], c[1]["at"]), (0x1101, 0x108))
        self.assertEqual(out[ids[1]]["evidence"][-1]["kind"], "map")
        f = out[ids[2]]["evidence"]
        self.assertEqual([(x["kind"], x["from"], x["from_entry"], x["from_pointers"]) for x in f],
                         [("goto", 0x1401, 0x1400, 0)])
        self.assertEqual(out[ids[5]]["evidence"][-1]["kind"], "table-unreferenced")
        self.assertEqual(out[ids[6]]["evidence"][-1]["kind"], "ambiguous-start")
        self.assertEqual([x["kind"] for x in out[ids[8]]["ignored"]], ["table-orphan"])
        k = out[ids[7]]
        self.assertEqual(k["commands"], [])
        self.assertEqual([(m["table"], m["field"], m["as"]) for m in k["misread"]], [("dex", 4, "table field")])
        self.assertEqual(k["evidence"][0]["kind"], "known-table")

    def test_pointer_into_a_script_is_no_evidence(self):
        self.im.put(0x2101, ptr_bytes(0x1401))     # into E, at its goto: not a script start
        out = self.im.study().classify([self.entry(2, 0x1502)])
        self.assertEqual(out[f"s{self.t[2]:07X}"]["class"], "unreachable")

    def test_misread_pool_word(self):
        im = Image()
        t = im.text()
        # pool word BD 00 reads as vmessage
        im.put(0x100, b"\x00\xb5\x01\x48\x00\xbd\x00\x00" + ptr_bytes(t))
        e = {"id": f"s{t:07X}", "offset": t, "refs": [{"at": 0x108, "kind": "script:vmessage"}]}
        im.put(0x107, b"\xbd")
        out = im.study().classify([e])[e["id"]]
        self.assertEqual((out["class"], out["misread"][0]["as"]), ("reachable", "literal pool word"))

class DataStudyTest(unittest.TestCase):
    """System strings, classed by the code that reads their tables."""

    def setUp(self):
        self.im = Image(0x10000)
        self.t = [self.im.text(f"Text {i}.") for i in range(12)]

    def table(self, at, targets):
        self.im.put(at, b"".join(ptr_bytes(x) for x in targets))

    def classify(self, *idx, **kw):
        st = self.im.study(**kw)
        ents = [{"id": f"s{self.t[i]:07X}", "offset": self.t[i]} for i in idx]
        out = DataStudy(st).classify(ents)
        return [out[e["id"]] for e in ents]

    def test_code_table_and_orphan(self):
        t = self.t
        self.table(0x800, t[0:3])
        self.im.pool(0x400, 0x800)                       # code reads table A
        self.table(0x900, t[3:5])                        # table B: nothing points at it
        self.table(0xA00, t[5:7])
        self.im.put(0x2000, b"\x00" * 4 + ptr_bytes(0xA00) + b"\x00" * 4)   # C: a lone word in data
        a, b, c = self.classify(1, 4, 6)
        self.assertEqual([x["class"] for x in (a, b, c)], ["reachable", "unreachable", "possibly reachable"])
        self.assertEqual([(e["kind"], e["at"]) for e in a["evidence"]], [("table", 0x804), ("pool", 0x408)])
        self.assertEqual((b["evidence"][0]["kind"], b["evidence"][0]["table"]), ("table-orphan", 0x900))
        self.assertEqual(c["evidence"][-1]["kind"], "aligned")

    def test_record_table_chain(self):
        t = self.t
        # 12-byte records {id, text, id}; a table of tables
        self.im.put(0x800, b"".join(bytes([k, 0, 0, 1]) + ptr_bytes(x) + bytes([k, 0, 0, 2]) for k, x in enumerate(t[:4])))
        self.table(0x900, [0x800, 0xC00])
        self.im.pool(0x400, 0x900)
        (r,) = self.classify(2)
        self.assertEqual(r["class"], "reachable")
        self.assertEqual([(e["kind"], e.get("stride")) for e in r["evidence"]],
                         [("table", 12), ("table", 4), ("pool", None)])

    def test_table_data_read_as_ldr_is_no_code(self):
        # pointer 0x08004802 at 0x800: "ldr r0, [pc, #8]" loads 0x80C
        im, t = self.im, self.t
        blob = CM.encode("Blank text.")
        im.put(0x4802, blob)
        im.not_code[0x4802:0x4802 + len(blob)] = b"\x01" * len(blob)
        self.table(0x7FC, [t[0], 0x4802, t[1], t[2], t[3]])
        self.assertIsNone(pool_load(im.d, 0x80C))
        self.assertEqual(im.study().place(0x80C)[0], "table")
        (r,) = self.classify(3)
        self.assertEqual((r["class"], r["evidence"][0]["kind"]), ("unreachable", "table-orphan"))

    def test_pointer_to_text_before_a_table_is_no_table_pointer(self):
        im, t = self.im, self.t
        im.put(0x8F8, b"\x00\x00\xff\xff")               # a blank text, not catalogued
        self.table(0x900, t[0:2])                        # the orphan table right after it
        self.table(0x600, [t[2], t[3], 0x8F8, t[4], t[5]])   # a read table: one field points at the blank
        im.pool(0x400, 0x600)
        live, orphan = self.classify(3, 1)
        self.assertEqual((live["class"], orphan["class"]), ("reachable", "unreachable"))

    def test_section_anchor(self):
        im, t = self.im, self.t
        im.put(0x9A0, bytes(range(1, 0x61)))            # 96 bytes of data before the table
        self.table(0xA00, t[0:3])

        def code(second):
            # ldr r3, =anchor; <second>; bx lr
            im.put(0x400, b"\x01\x4b" + second + b"\x70\x47\x00\x00" + ptr_bytes(0x9A0))
            return self.classify(1)[0]

        r = code(b"\x1d\x6e")                            # ldr r5, [r3, #0x60]
        self.assertEqual((r["class"], r["evidence"][-1]["kind"]), ("reachable", "anchor"))
        self.assertEqual(r["evidence"][-1]["use"], 0x402)
        self.assertEqual(code(b"\x60\x22")["class"], "possibly reachable")   # mov r2, #0x60: a size
        self.assertEqual(code(b"\x1d\x68")["class"], "unreachable")          # ldr r5, [r3, #0]

    def test_rom_header_field(self):
        im, t = self.im, self.t
        im.put(0x14C, ptr_bytes(0x800))
        self.table(0x800, t[0:2])
        self.assertEqual(self.classify(1)[0]["class"], "unreachable")       # the header serves other software
        im.pool(0x200, 0x14C)                                               # code reads that field
        r = self.classify(1)[0]
        self.assertEqual((r["class"], r["evidence"][-1]["kind"]), ("reachable", "rom-header"))

    def test_pointer_inside_the_table(self):
        im, t = self.im, self.t
        # four texts, then a read record {list, title}
        self.table(0x800, t[0:4] + [0x800, t[4]])
        im.pool(0x400, 0x810)
        (r,) = self.classify(1)
        self.assertEqual(r["class"], "reachable")
        self.assertEqual([e["kind"] for e in r["evidence"]], ["table", "inner", "pool"])

    def test_script_text_operand(self):
        im, t = self.im, self.t
        im.put(0x1000, b"\x6a\x85\x0a" + ptr_bytes(t[0]) + b"\x02")   # lock; bufferstring 0x0A; end
        self.assertEqual(self.classify(0)[0]["class"], "unreachable")
        r = self.classify(0, roots={0x1000: "0.0 npc"})[0]
        self.assertEqual(r["class"], "reachable")
        self.assertEqual([e["kind"] for e in r["evidence"]], ["script", "map"])

    def test_report_lists_the_system_classes_and_unread_tables(self):
        strings = {"s1": {"class": "reachable", "group": "system", "evidence": [{"kind": "pool", "at": "0x408"}]},
                   "s2": {"class": "unreachable", "group": "system", "evidence": [
                       {"kind": "table-orphan", "at": "0x904", "table": "0x900", "stride": 4, "first": "0x900",
                        "last": "0x904", "ignored": []}]}}
        res = {"stats": {"strings": 0, "nodes": 0, "map_headers": 0, "maps_walked": 0, "map_roots": 0,
                         "walk_commands": 0, "pointers": 0, "system_tables": 1}, "strings": strings}
        rep = report(res)
        self.assertIn("Strings of the catalog's system group: 2.", rep)
        self.assertNotIn("batch", rep.split("# System string reachability")[1].split("## System method")[0])
        self.assertIn("| 0x900 | 4 | 0x900-0x904 | 1 | s2 | none |", rep)

def hw(*halfwords: int) -> bytes:
    return b"".join(h.to_bytes(2, "little") for h in halfwords)

def bl(at: int, target: int) -> bytes:
    off = target - (at + 4)
    return hw(0xF000 | (off >> 12 & 0x7FF), 0xF800 | (off >> 1 & 0x7FF))

class ThumbTest(unittest.TestCase):
    """A literal pool counts only in code something reaches."""

    def setUp(self):
        self.im = Image(0x10000)
        self.t = [self.im.text(f"Text {i}.") for i in range(8)]

    def root(self, at, func):
        """An aligned function pointer in data."""
        self.im.put(at, ptr_bytes(func | 1))

    def thumb(self):
        return Thumb(self.im.d, pointer_index(Rom(self.im.d)), self.im.not_code)

    def test_code_after_a_hook_jump_is_not_reached(self):
        im, t = self.im, self.t
        im.put(0x1100, hw(0x4900, 0x4708) + ptr_bytes(0x1401))   # ldr r1, [pc]; bx r1: the hook
        dead = im.pool(0x1108, t[0])                              # the body after the jump
        live = im.pool(0x1200, t[1])
        im.put(0x1400, hw(0x4770))                                # the hook: bx lr
        self.root(0x2000, 0x1100)
        self.root(0x2004, 0x1200)
        th = self.thumb()
        self.assertEqual([th.reached[a] for a in (0x1100, 0x1102, 0x110A, 0x1202, 0x1400)], [1, 1, 0, 1, 1])
        self.assertEqual((th.load(dead), th.load(live)), (None, 0x1202))
        st = im.study(thumb=th)
        self.assertEqual((st.place(dead), st.place(live)), (("dead-pool", None), ("pool", None)))
        self.assertEqual(im.study().place(dead), ("pool", None))   # without the walk: every pool
        ents = [{"id": f"s{x:07X}", "offset": x} for x in t[:2]]
        out = DataStudy(st).classify(ents)
        self.assertEqual([out[e["id"]]["class"] for e in ents], ["unreachable", "reachable"])
        self.assertEqual(out[ents[0]["id"]]["evidence"][0]["kind"], "dead-pool")

    def test_a_getter_counts_only_when_called(self):
        im, t = self.im, self.t
        im.put(0x300, hw(0x4800, 0x4770) + ptr_bytes(t[2]))    # ldr r0, [pc]; bx lr
        self.assertFalse(self.thumb().reached[0x300])
        im.put(0x500, hw(0xB500) + bl(0x502, 0x300) + hw(0xBD00))   # push; bl getter; pop {pc}
        self.root(0x2008, 0x500)
        th = self.thumb()
        self.assertEqual((th.reached[0x300], th.load(0x304)), (1, 0x300))

    def test_jump_tables(self):
        im, t = self.im, self.t
        # agbcc: the ldr before mov pc loads the table
        im.put(0x600, hw(0x4902, 0x4687))                       # ldr r1, [pc, #8]; mov pc, r0
        im.put(0x60C, ptr_bytes(0x610) + ptr_bytes(0x620) + ptr_bytes(0x630))
        im.pool(0x620, t[3])
        im.pool(0x630, t[4])
        self.root(0x200C, 0x600)
        # gcc: bl __gnu_thumb1_case_uqi, then byte offsets; cmp #1: two cases
        im.put(0x700, hw(0xB402, 0x4671, 0x0849, 0x0049, 0x5C09, 0x0049, 0x448E, 0xBC02, 0x4770))
        im.put(0x800, hw(0x2801) + bl(0x802, 0x700) + bytes([5, 0x0D]))
        im.pool(0x810, t[5])
        im.pool(0x820, t[6])
        self.root(0x2010, 0x800)
        th = self.thumb()
        self.assertEqual([th.reached[a] for a in (0x622, 0x632, 0x812, 0x822)], [1, 1, 1, 1])
        self.assertEqual(th.reached[0x806], 0)                  # the offsets are no code

    def test_unaligned_roots_need_a_function_start(self):
        im = self.im
        im.put(0x900, hw(0x4B00, 0x8818, 0x4B00, 0x18C0, 0xB510, 0xBD10))   # push 5th
        im.put(0xA00, hw(0xB510, 0xBD10))
        im.put(0x2101, b"\x23" + ptr_bytes(0x901))              # callasm: up to 8 instructions
        im.put(0x2201, b"\x00" + ptr_bytes(0xA01))              # elsewhere: a push in the first 3
        im.put(0x2301, b"\x00" + ptr_bytes(0x901))
        th = self.thumb()
        self.assertEqual((th.reached[0x900], th.reached[0xA00]), (1, 1))
        im.put(0x2101, b"\x00")
        self.assertEqual(self.thumb().reached[0x900], 0)

    def test_declared_code_references_need_a_reached_load(self):
        im, t = self.im, self.t
        word = im.pool(0x200, t[7])
        strings = {"sA": {"class": "unreachable", "evidence": []}, "sB": {"class": "reachable", "evidence": []}}
        refs = [("computed", [word], ["sA", "sB"])]
        apply_code_refs(strings, refs, self.thumb())
        self.assertEqual(strings["sA"]["class"], "unreachable")
        self.root(0x2000, 0x200)
        apply_code_refs(strings, refs, self.thumb())
        self.assertEqual(strings["sA"]["class"], "reachable")
        self.assertEqual(strings["sA"]["evidence"][0], {"kind": "computed", "at": word, "ldr": 0x202})
        self.assertEqual(strings["sB"]["evidence"], [])

    def classify(self, i, declared=None):
        st = self.im.study(thumb=self.thumb())
        e = {"id": f"s{self.t[i]:07X}", "offset": self.t[i]}
        return DataStudy(st, declared).classify([e])[e["id"]]

    def test_a_literal_pool_is_no_table(self):
        im, t = self.im, self.t
        for k in range(3):   # dead getters: ldr r0, [pc]; bx lr; the pool word
            im.put(0x1100 + 8 * k, hw(0x4800, 0x4770) + ptr_bytes(t[k]))
        im.pool(0x1200, 0x10F8)   # live code points just before them
        self.root(0x2000, 0x1200)
        r = self.classify(1)
        self.assertEqual((r["class"], r["evidence"][0]["kind"]), ("unreachable", "dead-pool"))

    def test_a_declared_table_is_read_at_its_base(self):
        im, t = self.im, self.t
        for k in range(3):   # records {id, name, text} from 0x1800
            im.put(0x1800 + 12 * k + 8, ptr_bytes(t[k]))
        im.put(0x17FC, ptr_bytes(0x17F0))   # a neighbour's pointer before them
        im.pool(0x1200, 0x17FC)             # live code reads the neighbour
        self.root(0x2000, 0x1200)
        rule = [{"base": 0x1800, "stride": 12, "count": 3, "fields": [8]}]
        self.assertEqual(self.classify(1)["class"], "reachable")
        self.assertEqual(self.classify(1, rule)["class"], "unreachable")
        im.pool(0x1300, 0x1800)
        self.root(0x2004, 0x1300)
        self.assertEqual(self.classify(1, rule)["class"], "reachable")

    def test_unused_table_entries_give_no_root(self):
        im = self.im
        im.put(0x1100, hw(0xB500, 0xBD00))
        im.put(0x2000, ptr_bytes(0x1101) + ptr_bytes(0x1101 + 0x100))
        im.put(0x1200, hw(0xB500, 0xBD00))
        unused = unused_entries({"reach": {"unused_entries": [{"base": 0x2000, "indexes": [0], "evidence": "x"}]}})
        th = Thumb(im.d, pointer_index(Rom(im.d)), im.not_code, unused)
        self.assertEqual((th.reached[0x1100], th.reached[0x1200]), (0, 1))
        for bad in ({"reach": {"unused_entries": [{"base": 0x2000, "indexes": [0]}]}},
                    {"reach": {"unused_entries": [{"base": 0x2000, "indexes": [], "evidence": "x"}]}}):
            with self.assertRaises(ValueError):
                unused_entries(bad)
        with self.assertRaises(ValueError):
            declared_tables({"reach": {"table": [{"base": 0x1800, "stride": 12, "count": 3}]}})

    def test_report_lists_class_changes(self):
        prev = {"strings": {"s1": {"class": "reachable"}, "s2": {"class": "reachable"}, "s3": {"class": "unreachable"}}}
        res = {"strings": {"s1": {"class": "unreachable", "group": "system",
                                  "evidence": [{"kind": "dead-pool", "at": "0x91ac"}]},
                           "s2": {"class": "reachable", "group": "system", "evidence": []}}}
        lines = changes_report(prev, res)
        self.assertIn("## Class changes: 1", lines)
        self.assertIn("| s1 | system | reachable | unreachable | dead-pool | 0x91ac |", lines)
        self.assertIn("Strings in one run only: 1 (the studied strings differ).", lines)

class StoredStudyTest(unittest.TestCase):
    """A narrower run keeps the stored build/reach.json."""

    def test_fewer_strings_are_refused(self):
        from gen3tl.discover.reach import fewer_strings
        old = {"strings": {"s1": {}, "s2": {}, "s3": {}}}
        self.assertIsNone(fewer_strings({"strings": {"s1": {}, "s2": {}, "s3": {}}}, old))
        self.assertIsNone(fewer_strings({"strings": {"s1": {}}}, None))
        why = fewer_strings({"strings": {"s1": {}}}, old)
        self.assertIn("1 strings, the stored build/reach.json 3", why)
        self.assertIn("--allow-fewer", why)
