import unittest
from pathlib import Path

from gen3tl.core.charmap import Charmap
from gen3tl.discover.inventory import build
from gen3tl.profile.profile import Profile
from gen3tl.core.rom import Rom, ptr_bytes
from gen3tl.discover.scan import discover, is_string_start, is_text

CM = Charmap()

def image(size=0x4000):
    return bytearray(b"\xff" * size)

class StartRulesTest(unittest.TestCase):
    def test_after_terminator_is_start(self):
        d = image()
        d[0x100:0x100] = b""
        blob = b"\xff" + CM.encode("Hello there.")
        d[0x100:0x100 + len(blob)] = blob
        self.assertTrue(is_string_start(d, 0x101))

    def test_one_letter_after_terminator_is_mid_string(self):
        d = image()
        blob = b"\xff" + CM.encode("As it grows.")
        d[0x100:0x100 + len(blob)] = blob
        self.assertFalse(is_string_start(d, 0x102))  # "s it grows."

    def test_after_word_and_space_is_mid_string(self):
        d = image()
        blob = b"\xff" + CM.encode("Please don’t stare.")
        d[0x100:0x100 + len(blob)] = blob
        self.assertFalse(is_string_start(d, 0x101 + len("Please ")))

    def test_after_placeholder_and_space_is_mid_string(self):
        d = image()
        blob = b"\xff" + CM.encode("Win {STR_VAR_2} BP as prize.")
        d[0x100:0x100 + len(blob)] = blob
        t = 0x101 + len(CM.encode("Win {STR_VAR_2} BP ", terminate=False))
        self.assertFalse(is_string_start(d, t))

    def test_after_binary_is_start(self):
        d = image()
        blob = b"\xff\x0f\x00" + ptr_bytes(0x300) + b"\x09\x06\x02" + CM.encode("Hi there!")
        d[0x100:0x100 + len(blob)] = blob
        self.assertTrue(is_string_start(d, 0x100 + len(blob) - len(CM.encode("Hi there!"))))

    def test_movement_prefix_is_not_text(self):
        d = bytearray(b"\x10\xfe" + CM.encode("Mom: Wow!"))
        self.assertFalse(is_text(d, 0, len(d) - 1))
        d = bytearray(CM.encode("Mom: Wow!"))
        self.assertTrue(is_text(d, 0, len(d) - 1))

    def test_unassigned_byte_is_not_text(self):
        d = bytearray(b"\x60\x82" + CM.encode("Huh? My card…"))
        self.assertFalse(is_text(d, 0, len(d) - 1))

    def test_placeholder_before_first_break_is_text(self):
        # s1F41BFB, a first line holding only a name placeholder
        d = bytearray(CM.encode("{PLAYER}!\\nJust the {STR_VAR_1} I was looking for!"))
        self.assertTrue(is_text(d, 0, len(d) - 1))
        d = bytearray(CM.encode("{F7 2E}\\nEl Rock Smash data"))  # 0xF7 and its argument are no word
        self.assertFalse(is_text(d, 0, len(d) - 1))

    def test_letterless_first_line_is_text_for_script_targets(self):
        d = bytearray(CM.encode("……\\pGo away."))
        self.assertFalse(is_text(d, 0, len(d) - 1))
        self.assertTrue(is_text(d, 0, len(d) - 1, scripted=True))
        d = bytearray(b"\x10\xfe" + CM.encode("Mom: Wow!"))
        self.assertFalse(is_text(d, 0, len(d) - 1, scripted=True))  # 0x10 is no start byte

    def test_digits_may_outnumber_letters_for_script_targets(self):
        # s1F0DB60, a coin menu line: 5 letters in 17 non-spaces
        d = bytearray(CM.encode("{FONT 00}2000 Coins{CLEAR_TO 40}¥200,000"))
        self.assertEqual(bytes(d[:3]), b"\xfc\x06\x00")
        self.assertFalse(is_text(d, 0, len(d) - 1))
        self.assertTrue(is_text(d, 0, len(d) - 1, scripted=True))
        d = bytearray(CM.encode("…-75!”Ll"))  # binary run that decodes to punctuation and digits
        self.assertFalse(is_text(d, 0, len(d) - 1))

    def test_centred_caption_is_text_only_for_script_targets(self):
        # s07F208D, including its trailing space
        d = bytearray(CM.encode("              Earlier "))
        self.assertFalse(is_text(d, 0, len(d) - 1))
        self.assertTrue(is_text(d, 0, len(d) - 1, scripted=True))
        # a script reference passes the padding and data guards
        for text in (" " * 17 + "Earlier", " " * 16, " " * 14 + "{60}Earlier"):
            with self.subTest(text=text):
                d = bytearray(CM.encode(text))
                self.assertFalse(is_text(d, 0, len(d) - 1, scripted=True))

class ReferenceKindsTest(unittest.TestCase):
    def setUp(self):
        self.d = image()

    def put(self, off, blob):
        self.d[off:off + len(blob)] = blob

    def kinds(self, found, off):
        return sorted(r.kind for f in found if f.offset == off for r in f.refs)

    def test_directional_signs_require_script_references(self):
        for i, arrow in enumerate(("UP", "DOWN", "LEFT", "RIGHT")):
            t = 0x2001 + i * 0x100
            self.put(t, CM.encode("{" + arrow + "_ARROW} Inn"))
            self.put(0x401 + i * 0x10, b"\x0f\x00" + ptr_bytes(t) + b"\x09\x03")
        self.put(0x2501, CM.encode("{RIGHT_ARROW}yyzz\\nl"))
        self.put(0x800, ptr_bytes(0x2501))
        found, _, _ = discover(Rom(self.d))
        self.assertEqual({f.offset for f in found}, {0x2001, 0x2101, 0x2201, 0x2301})

    def test_directional_sign_interior_is_not_a_separate_string(self):
        text = "Crossroads\\p{LEFT_ARROW} Inn\\n{RIGHT_ARROW} Town"
        self.put(0x2001, CM.encode(text))
        interior = 0x2001 + len(CM.encode("Crossroads\\p", terminate=False))
        self.put(0x401, b"\x0f\x00" + ptr_bytes(0x2001) + b"\x09\x03")
        self.put(0x421, b"\x0f\x00" + ptr_bytes(interior) + b"\x09\x03")
        found, _, _ = discover(Rom(self.d))
        self.assertEqual({f.offset for f in found}, {0x2001})

    def test_directional_sign_keeps_binary_guards(self):
        for text in ("{UP_ARROW}{60}Inn", "{LEFT_ARROW}       ", "{DOWN_ARROW}"):
            with self.subTest(text=text):
                raw = CM.encode(text)
                self.assertFalse(is_text(raw, 0, len(raw) - 1, scripted=True))

    def test_script_and_struct_and_pool(self):
        t1, t2, t3, t4 = 0x2001, 0x2101, 0x2201, 0x2301
        for t, s in ((t1, "Hello there."), (t2, "A trainer appears!"), (t3, "Item one text."), (t4, "Item two text.")):
            self.put(t - 1, b"\xff" + CM.encode(s))
        # loadpointer: 0F 00 <ptr>, unaligned
        self.put(0x401, b"\x0f\x00" + ptr_bytes(t1))
        # trainerbattle type 0: 5C 00 iiii aaaa <intro> <defeat>
        self.put(0x501, b"\x5c\x00\x01\x00\x00\x00" + ptr_bytes(t2) + ptr_bytes(t1))
        # two records of 12 bytes: <ptr> u32 u32
        self.put(0x800, ptr_bytes(t3) + b"\x01\x00\x00\x00\x02\x00\x00\x00")
        self.put(0x80C, ptr_bytes(t4) + b"\x03\x00\x00\x00\x04\x00\x00\x00")
        # THUMB literal pool: ldr r0 at 0xA00 loads 0xA08
        self.put(0xA00, bytes([0x01, 0x48]) + b"\x00\x00\x00\x00\x00\x00")
        self.put(0xA08, ptr_bytes(t1) + b"\x00\x00\x00\x00")
        rom = Rom(self.d)
        found, _, _ = discover(rom)
        self.assertEqual(self.kinds(found, t1), ["pool", "script:loadpointer", "script:trainerbattle"])
        self.assertEqual(self.kinds(found, t2), ["script:trainerbattle"])
        self.assertEqual(self.kinds(found, t3), ["struct"])
        self.assertEqual(self.kinds(found, t4), ["struct"])

    def test_letterless_first_line_found_only_behind_a_script_text_command(self):
        t1, t2, t3 = 0x2001, 0x2101, 0x2201
        self.put(t1 - 1, b"\xff" + CM.encode("{PLAYER}!\\nJust the {STR_VAR_1} I was looking for!"))
        self.put(t2 - 1, b"\xff" + CM.encode("……\\pGo away."))
        self.put(t3 - 1, b"\xff" + CM.encode("……\\pGo away."))
        # base ROM msgbox: loadpointer 0F 00 <ptr>, callstd 09 06
        self.put(0x401, b"\x0f\x00" + ptr_bytes(t1) + b"\x09\x06")
        self.put(0x411, b"\x0f\x00" + ptr_bytes(t2) + b"\x09\x06")
        self.put(0x800, ptr_bytes(t3) + b"\x00\x00\x00\x00")  # aligned word only
        found, _, _ = discover(Rom(self.d))
        self.assertEqual(self.kinds(found, t1), ["script:loadpointer"])
        self.assertEqual(self.kinds(found, t2), ["script:loadpointer"])
        self.assertNotIn(t3, [f.offset for f in found])

    def test_coin_menu_line_found_behind_a_script_text_command(self):
        t1, t2 = 0x2001, 0x2101
        for t in (t1, t2):
            self.put(t - 1, b"\xff" + CM.encode("{FONT 00}2000 Coins{CLEAR_TO 40}¥200,000"))
        # base ROM caption: loadpointer 0F 00 <ptr>, special 0x25
        self.put(0x401, b"\x0f\x00" + ptr_bytes(t1) + b"\x25\x25\x00")
        self.put(0x800, ptr_bytes(t2) + b"\x00\x00\x00\x00")  # aligned word only
        found, _, _ = discover(Rom(self.d))
        self.assertEqual(self.kinds(found, t1), ["script:loadpointer"])
        self.assertNotIn(t2, [f.offset for f in found])

    def test_centred_caption_found_only_behind_a_script_text_command(self):
        # same caption behind loadpointer, a pool and a table
        t1, t2, t3 = 0x2001, 0x2101, 0x2201
        for t in (t1, t2, t3):
            self.put(t - 1, b"\xff" + CM.encode("            16 Years Ago"))
        self.put(0x401, b"\x0f\x00" + ptr_bytes(t1) + b"\x09\x06")
        self.put(0xA00, b"\x01\x48")  # ldr r0, [pc, #4]
        self.put(0xA08, ptr_bytes(t2))
        self.put(0x800, ptr_bytes(t3) + ptr_bytes(t1))
        found, _, _ = discover(Rom(self.d))
        self.assertEqual(self.kinds(found, t1), ["script:loadpointer", "table"])
        self.assertNotIn(t2, [f.offset for f in found])
        self.assertNotIn(t3, [f.offset for f in found])

    def test_profile_trainerbattle_extension(self):
        t = 0x2001
        self.put(t - 1, b"\xff" + CM.encode("Two of us!"))
        # type 0x0C with a 10-byte header (engine extension)
        self.put(0x601, b"\x5c\x0c\x0d\x80\x00\x80\x08\x00\x00\x01" + ptr_bytes(t))
        rom = Rom(self.d)
        found, _, _ = discover(rom)
        self.assertEqual(self.kinds(found, t), ["unverified"])
        found, _, _ = discover(rom, trainerbattle_offsets={0x0C: (10,)})
        self.assertEqual(self.kinds(found, t), ["script:trainerbattle"])

class IncludedLabelsTest(unittest.TestCase):
    def test_included_labels_keep_struct_references(self):
        # floor-menu records hold a text pointer and a zero word.
        d = image()
        targets = (0x2001, 0x2101, 0x2201, 0x2301)
        for i, (t, text) in enumerate(zip(targets, ("5F", "4F", "Exit", "3F"))):
            blob = CM.encode(text)
            d[t:t + len(blob)] = blob
            ref = 0x800 + 8 * i
            d[ref:ref + 8] = ptr_bytes(t) + b"\x00" * 4
            if text != "Exit":
                self.assertFalse(is_text(d, t, t + len(blob) - 1))
        prof = Profile({"strings": {"include": list(targets[:2])}}, Path("test.toml"))
        entries = {e["offset"]: e for e in build(prof, Rom(d))["entries"]}
        self.assertEqual(set(entries), set(targets[:3]))  # 3F has no profile exception
        for i, t in enumerate(targets[:2]):
            with self.subTest(offset=t):
                self.assertEqual(entries[t]["refs"], [{"at": 0x800 + 8 * i, "kind": "struct"}])
                self.assertEqual(entries[t]["category"], "data-table")
                self.assertNotIn("unverified", entries[t]["flags"])


    def test_verified_include_keeps_reference_classification(self):
        # a verified include keeps its other references unchanged.
        d = image()
        t1, t2 = 0x2001, 0x2101
        for t, text in ((t1, "B1F"), (t2, "Exit")):
            blob = CM.encode(text)
            d[t:t + len(blob)] = blob
        d[0x800:0x810] = ptr_bytes(t1) + b"\x00" * 4 + ptr_bytes(t2) + b"\x00" * 4
        d[0xA00:0xA02] = b"\x01\x48"
        d[0xA08:0xA0C] = ptr_bytes(t1)
        prof = Profile({"strings": {"include": [t1]}}, Path("test.toml"))
        entries = {e["offset"]: e for e in build(prof, Rom(d))["entries"]}
        self.assertEqual(entries[t1]["refs"], [{"at": 0x800, "kind": "aligned"},
                                               {"at": 0xA08, "kind": "pool"}])
