import copy
import unittest

from gen3tl.build import codereplace as C
from gen3tl.build.inject import BuildResult, DiffRegistry, FreeSpace, verify
from gen3tl.core.charmap import Charmap
from gen3tl.core.rom import Rom
from project_fixture import rom_evidence

AT = 0x100

PROLOGUE = bytes.fromhex("f0b557464e464546e0b48ab0")

ORIGINAL = PROLOGUE + bytes(range(1, 21))

STUB = bytes.fromhex("f0b5014c2047c046")

CODE = bytes.fromhex("c046c046c046c046")

ALT_CODE = bytes.fromhex("c046c04600470047")

ENTRY = {"name": "units", "evidence": "A printer converts the stored value in code.",
         "routines": [{"guard": rom_evidence(AT, ORIGINAL.hex()), "where": "value printer"}],
         "routine": {"code": CODE.hex(), "words": ["unit"]},
         "calls": [{"guard": rom_evidence(AT, PROLOGUE.hex()), "code": STUB.hex(), "entry": 4}]}

FALLBACK = {**ENTRY, "routine": {**ENTRY["routine"], "words": ["unit", "pool"], "fallback": {"pool": 0x200}}}

VARIANT = {**ENTRY, "routine": {**ENTRY["routine"], "words": ["unit", "pool"],
            "fallback": {"pool": {"at": 0x200, "code": ALT_CODE.hex(), "words": ["unit"]}}}}

def base_image() -> Rom:
    data = bytearray(0x1000) + b"\xff" * 0x1000
    data[AT:AT + len(ORIGINAL)] = ORIGINAL
    return Rom(data)

def selected(texts=None) -> dict:
    data = {"code_replacement": [copy.deepcopy(ENTRY)]}
    C.configure(data, {"units": texts or {"unit": "kg"}})
    return data

def selected_fallback(texts=None) -> dict:
    data = {"code_replacement": [copy.deepcopy(FALLBACK)]}
    C.configure(data, {"units": texts or {"unit": "kg"}})
    return data

def selected_variant(texts=None) -> dict:
    data = {"code_replacement": [copy.deepcopy(VARIANT)]}
    C.configure(data, {"units": texts or {"unit": "kg"}})
    return data

class DeclarationTest(unittest.TestCase):
    def test_parse_reads_routines_code_words_and_calls(self):
        rep = C.parse(copy.deepcopy(ENTRY))
        self.assertEqual((rep.name, rep.code, rep.words), ("units", CODE, ["unit"]))
        self.assertEqual([(r.at, r.end, r.where) for r in rep.routines], [(AT, AT + 32, "value printer")])
        self.assertEqual([(c.at, c.guard.read(base_image().data), c.code, c.entry) for c in rep.calls], [(AT, PROLOGUE, STUB, 4)])
        self.assertEqual(rep.calls[0].written(0x800), STUB + (0x08000805).to_bytes(4, "little"))

    def test_parse_reads_fallback_pointers(self):
        rep = C.parse(copy.deepcopy(FALLBACK))
        self.assertEqual((rep.words, rep.fallbacks), (["unit", "pool"], {"pool": 0x200}))
        for fallback in ({"other": 0x200}, {"pool": -1}, {"pool": C.ROM_END}, {"pool": "0x200"}):
            with self.subTest(fallback=fallback):
                entry = copy.deepcopy(FALLBACK)
                entry["routine"]["fallback"] = fallback
                with self.assertRaises(C.CodeReplacementError):
                    C.parse(entry)

    def test_parse_reads_a_fallback_variant(self):
        rep = C.parse(copy.deepcopy(VARIANT))
        self.assertEqual((rep.words, rep.fallbacks),
                         (["unit", "pool"], {"pool": {"at": 0x200, "code": ALT_CODE, "words": ["unit"]}}))
        for fallback in ({"at": 0x200, "code": ALT_CODE.hex()}, {"at": 0x200, "words": ["unit"]},
                         {"at": -1, "code": ALT_CODE.hex(), "words": ["unit"]},
                         {"at": 0x200, "code": "c046", "words": ["unit"]},
                         {"at": 0x200, "code": ALT_CODE.hex(), "words": ["unit", "unit"]},
                         {"at": 0x200, "code": ALT_CODE.hex(), "words": ["pool"]},
                         {"at": 0x200, "code": ALT_CODE.hex(), "words": ["unit"], "extra": 1}):
            with self.subTest(fallback=fallback):
                entry = copy.deepcopy(VARIANT)
                entry["routine"]["fallback"] = {"pool": fallback}
                with self.assertRaises(C.CodeReplacementError):
                    C.parse(entry)

    def test_invalid_entries_are_rejected(self):
        call = ENTRY["calls"][0]
        cases = [("extra", 1), ("routines", []),
                 ("routines", [{"guard": rom_evidence(AT, ""), "where": "value printer"}]),
                 ("routines", [{"guard": rom_evidence(AT, ORIGINAL.hex()), "where": " "}]),
                 ("routines", ENTRY["routines"] + [{"guard": rom_evidence(AT + 30, "0000"), "where": "next"}]),
                 ("routine", {"code": "c046", "words": ["unit"]}),
                 ("routine", {"code": CODE.hex(), "words": ["unit", "unit"]}),
                 ("routine", {"code": CODE.hex(), "words": ["Unit text"]}),
                 ("calls", []),
                 ("calls", [dict(call, guard=rom_evidence(AT + 2, PROLOGUE))]),
                 ("calls", [dict(call, entry=len(CODE))]),
                 ("calls", [dict(call, entry=3)]),
                 ("calls", [dict(call, code=STUB.hex() + "c046")]),
                 ("calls", [call, call]),
                 ("calls", [dict(call, guard=rom_evidence(0x400, PROLOGUE))]),
                 ("evidence", "")]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                entry = copy.deepcopy(ENTRY)
                entry[key] = value
                with self.assertRaises(C.CodeReplacementError):
                    C.parse(entry)

    def test_a_routine_no_call_enters_is_rejected(self):
        entry = copy.deepcopy(ENTRY)
        entry["routines"].append({"guard": rom_evidence(0x200, "f0b5"), "where": "unused"})
        with self.assertRaisesRegex(C.CodeReplacementError, "0x200"):
            C.parse(entry)

    def test_names_are_distinct(self):
        with self.assertRaises(C.CodeReplacementError):
            C.from_profile({"code_replacement": [copy.deepcopy(ENTRY), copy.deepcopy(ENTRY)]})

class SelectionTest(unittest.TestCase):
    def test_policy_selects_a_declared_replacement_with_one_text_per_word(self):
        self.assertEqual(selected()[C.SELECTED], {"units": {"unit": "kg"}})
        data = {"code_replacement": [copy.deepcopy(ENTRY)]}
        C.configure(data, {})
        self.assertEqual(data[C.SELECTED], {})
        for choice in ({"other": {"unit": "kg"}}, {"units": {}}, {"units": {"unit": "kg", "more": "m"}},
                       {"units": {"unit": " "}}, {"units": "kg"}, ["units"]):
            with self.subTest(choice=choice), self.assertRaises(ValueError):
                C.configure({"code_replacement": [copy.deepcopy(ENTRY)]}, choice)

    def test_plan_encodes_the_texts_and_leaves_unselected_ones_out(self):
        plans = C.plan(selected(), Charmap())
        self.assertEqual([(p.replacement.name, p.texts) for p in plans], [("units", [Charmap().encode("kg")])])
        self.assertEqual(C.plan({"code_replacement": [copy.deepcopy(ENTRY)]}, Charmap()), [])
        with self.assertRaises(C.CodeReplacementError):
            C.plan(selected({"unit": "{UNKNOWN}"}), Charmap())

    def test_a_fallback_word_may_stay_unselected(self):
        self.assertEqual(selected_fallback()[C.SELECTED], {"units": {"unit": "kg"}})
        self.assertEqual(selected_fallback({"unit": "kg", "pool": "lbs"})[C.SELECTED],
                         {"units": {"unit": "kg", "pool": "lbs"}})
        for choice in ({"units": {}}, {"units": {"pool": "lbs"}}, {"units": {"unit": "kg", "extra": "x"}}):
            with self.subTest(choice=choice), self.assertRaises(ValueError):
                C.configure({"code_replacement": [copy.deepcopy(FALLBACK)]}, choice)

    def test_plan_writes_the_policy_text_or_the_fallback_pointer(self):
        plan = C.plan(selected_fallback(), Charmap())[0]
        text = Charmap().encode("kg")
        self.assertEqual(plan.texts, [text, 0x200])
        self.assertEqual(plan.size, len(CODE) + 8 + len(text))
        blob = plan.blob(0x800)
        text_at = 0x800 + len(CODE) + 8
        self.assertEqual(blob, CODE + (0x08000000 + text_at).to_bytes(4, "little")
                         + (0x08000200).to_bytes(4, "little") + text)
        plan = C.plan(selected_fallback({"unit": "kg", "pool": "lbs"}), Charmap())[0]
        self.assertEqual(plan.texts, [text, Charmap().encode("lbs")])

    def test_plan_writes_the_variant_without_the_fallback_text(self):
        plan = C.plan(selected_variant(), Charmap())[0]
        self.assertEqual((plan.replacement.code, plan.replacement.words, plan.texts),
                         (ALT_CODE, ["unit"], [Charmap().encode("kg")]))
        plan = C.plan(selected_variant({"unit": "kg", "pool": "lbs"}), Charmap())[0]
        self.assertEqual((plan.replacement.code, plan.replacement.words, plan.texts),
                         (CODE, ["unit", "pool"], [Charmap().encode("kg"), Charmap().encode("lbs")]))


class WriterTest(unittest.TestCase):
    def setUp(self):
        self.base = base_image()
        self.plans = C.plan(selected(), Charmap())

    def test_apply_writes_code_word_text_and_call(self):
        rom = self.base.copy()
        res = C.apply(rom, self.base, self.plans, FreeSpace.scan(bytes(self.base.data)))
        self.assertEqual(res.failures, [])
        pl = res.placements[0]
        text = Charmap().encode("kg")
        self.assertEqual((pl.routine % 4, pl.size, pl.calls), (0, len(CODE) + 4 + len(text), [AT]))
        self.assertEqual(rom.read(pl.routine, len(CODE)), CODE)
        self.assertEqual(rom.u32(pl.routine + len(CODE)), 0x08000000 + pl.routine + len(CODE) + 4)
        self.assertEqual(rom.read(pl.routine + len(CODE) + 4, len(text)), text)
        self.assertEqual(rom.read(AT, 12), STUB + (0x08000000 + pl.routine + 4 + 1).to_bytes(4, "little"))
        self.assertEqual(rom.read(AT + 12, 20), ORIGINAL[12:])
        self.assertEqual(C.verify(rom, self.plans, res), [])
        self.assertEqual(len(res.allowed), pl.size + 12)
        self.assertEqual(C.report_lines(res)[-1],
                         f"- units: routine at {pl.routine:#x} ({pl.size} bytes), calls at {AT:#x}")
        rom.write(AT + 8, b"\x00\x00\x00\x00")
        self.assertTrue(any("does not reach" in f for f in C.verify(rom, self.plans, res)))

    def test_apply_writes_a_fallback_pointer_word(self):
        base = self.base.copy()
        base.write(0x200, (0x08000400).to_bytes(4, "little"))
        rom = base.copy()
        plans = C.plan(selected_fallback(), Charmap())
        res = C.apply(rom, base, plans, FreeSpace.scan(bytes(base.data)))
        self.assertEqual(res.failures, [])
        pl = res.placements[0]
        self.assertEqual(rom.u32(pl.routine + len(CODE) + 4), 0x08000400)
        self.assertEqual(C.verify(rom, plans, res), [])

    def test_a_routine_the_rom_does_not_hold_or_taken_bytes_write_nothing(self):
        for case in ("routine", "call", "taken"):
            with self.subTest(case=case):
                base, taken = self.base.copy(), set()
                if case == "routine":
                    base.write(AT + 31, b"\xee")    # outside the call span
                elif case == "call":
                    base.write(AT + 2, b"\xee")
                else:
                    taken = {AT + 11}
                rom = base.copy()
                res = C.apply(rom, base, self.plans, FreeSpace.scan(bytes(base.data)), taken)
                self.assertEqual(len(res.failures), 1)
                self.assertEqual((res.placements, res.allowed), ([], set()))
                self.assertEqual(bytes(rom.data), bytes(base.data))

    def test_call_digest_is_checked_independently_of_its_routine(self):
        data = selected()
        data["code_replacement"][0]["calls"][0]["guard"] = rom_evidence(AT, PROLOGUE[:-1] + b"\x00")
        plans = C.plan(data, Charmap())
        rom = self.base.copy()
        result = C.apply(rom, self.base, plans, FreeSpace.scan(bytes(self.base.data)))
        self.assertEqual(len(result.failures), 1)
        self.assertEqual(result.placements, [])
        self.assertEqual(rom.data, self.base.data)

    def test_diff_guard_allows_the_class_and_rejects_shared_bytes(self):
        rom = self.base.copy()
        res = C.apply(rom, self.base, self.plans, FreeSpace.scan(bytes(self.base.data)))
        guard = DiffRegistry()
        res.register(guard)
        self.assertEqual(verify(rom, self.base, BuildResult(), {}, set(), guard), [])
        named = DiffRegistry()
        C.Result().register(named)
        outside = verify(rom, self.base, BuildResult(), {}, set(), named)
        self.assertTrue(any("outside" in f and "code replacement" in f for f in outside))
        for name, spans in (("text", None), ("sprite font", {AT}), ("graphics", {AT}), ("keyboard", {AT})):
            with self.subTest(name=name):
                text = BuildResult()
                if name == "text":
                    text.allowed = {AT}
                guard = DiffRegistry()
                if name == "sprite font":
                    guard.add("sprite font", spans)
                elif name == "graphics":
                    guard.add("graphics", spans)
                elif name == "keyboard":
                    guard.add("keyboard", spans)
                res.register(guard)
                fails = verify(rom, self.base, text, {}, set(), guard)
                self.assertIn(f"diff guard: 1 code replacement bytes are also {name} bytes, first at {AT:#x}",
                              fails)
