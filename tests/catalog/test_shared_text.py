import unittest
from pathlib import Path

from gen3tl.build.inject import inject, verify
from gen3tl.catalog import keys
from gen3tl.catalog.catalog import build_entries
from gen3tl.catalog.po import key_only
from gen3tl.core.charmap import Charmap
from gen3tl.core.rom import Rom, ptr_bytes
from gen3tl.discover.inventory import build, split_rules
from gen3tl.discover.context import split_uses
from gen3tl.profile.profile import Profile

CM = Charmap()

LABEL = 0x12001

SITES = (0x803, 0x903, 0xA03)   # three scripts read the label

def image():
    data = bytearray(b"\xff" * 0x20000)
    data[0:0x800] = b"\x00" * 0x800
    raw = CM.encode("Grunt")
    data[LABEL:LABEL + len(raw)] = raw
    for at in SITES:
        data[at - 2:at + 4] = b"\x0f\x00" + ptr_bytes(LABEL)
    return Rom(data)

RULE = {"id": "s0012001", "group": "team-b", "refs": [0xA03, 0x903],
        "evidence": "The two later scripts belong to the other team."}

def inventory(rules):
    return build(Profile({"strings": {"split": rules}}, Path("test.toml")), image())

def entry(inv, sid):
    return next(e for e in inv["entries"] if e["id"] == sid)

class InventoryTest(unittest.TestCase):
    def test_without_declaration_nothing_changes(self):
        inv = inventory([])
        self.assertEqual([e["id"] for e in inv["entries"]], ["s0012001"])
        self.assertEqual(len(entry(inv, "s0012001")["refs"]), 3)

    def test_group_moves_its_references_into_a_copy(self):
        inv = inventory([RULE])
        self.assertEqual([e["id"] for e in inv["entries"]], ["s0012001", "s0012001-team-b"])
        original, copy = inv["entries"]
        self.assertEqual([r["at"] for r in original["refs"]], [0x803])
        self.assertEqual([r["at"] for r in copy["refs"]], [0x903, 0xA03])
        self.assertEqual(copy["flags"], ["copy"])
        for k in ("offset", "length", "text", "mode", "category"):
            self.assertEqual(copy[k], original[k])

    def test_unknown_reference_is_named(self):
        with self.assertRaisesRegex(ValueError, r"references not in the inventory: 0xb03"):
            inventory([dict(RULE, refs=[0x903, 0xB03])])

    def test_invalid_declarations_fail(self):
        for rules in ([dict(RULE, refs=list(SITES))], [dict(RULE, id="s0012002")]):
            with self.subTest(rules=rules), self.assertRaises(ValueError):
                inventory(rules)
        for rules in ([dict(RULE, group="Team B")], [dict(RULE, refs=[])], [dict(RULE, extra=1)],
                      [RULE, RULE], [RULE, dict(RULE, group="team-c")]):
            with self.subTest(rules=rules), self.assertRaises(ValueError):
                split_rules(rules)

def use(at, order):
    return {"map": "3.4", "map_name": "Town", "event": "npc", "order": order, "script": f"{at - 2:#x}", "at": at,
            "sprite": None, "textcolor_gender": None, "box": None}

class StudyTest(unittest.TestCase):
    def setUp(self):
        self.inv = inventory([RULE])
        self.ctx = {"maps": {}, "strings": {"s0012001": {
            "uses": [use(at, i) for i, at in enumerate(SITES)], "inferred_map": None, "speakers": []}}}
        split_uses(self.ctx["strings"], self.inv)

    def test_context_uses_follow_the_references(self):
        uses = {sid: [u["at"] for u in r["uses"]] for sid, r in self.ctx["strings"].items()}
        self.assertEqual(uses, {"s0012001": [0x803], "s0012001-team-b": [0x903, 0xA03]})

    def test_copy_takes_its_own_key_and_the_offset_names_the_original(self):
        aliases = keys.assign(self.inv, self.ctx)
        self.assertEqual(len(aliases), 2)
        self.assertNotEqual(aliases.key("s0012001"), aliases.key("s0012001-team-b"))
        self.assertEqual(aliases.entry_id("0x12001"), "s0012001")

    def test_copy_takes_its_first_free_map_slot(self):
        self.ctx["maps"] = {"3.4": {"header": "0x10", "engine_maps": ["3.4"]}}
        events = lambda h: [("npc", n, at - 2) for n, at in enumerate(SITES)]
        aliases = keys.assign(self.inv, self.ctx, events=events)
        self.assertEqual(aliases.key("s0012001-team-b"), "map:3.4/npc:1/1")
        known = keys.Aliases([("map:3.4/npc:1/1", 0x12001, "s0012001")])
        aliases = keys.assign(self.inv, self.ctx, events=events, known=known)
        self.assertEqual(aliases.key("s0012001-team-b"), "map:3.4/npc:2/2")

    def test_catalog_carries_a_second_entry_with_the_same_english(self):
        groups = build_entries(self.inv, self.ctx, CM, lambda b: 6, "test")
        es = {e.msgctxt: e for g in groups.values() for e in g}
        self.assertEqual(set(es), {"s0012001", "s0012001-team-b"})
        self.assertEqual(es["s0012001"].msgid, es["s0012001-team-b"].msgid)
        self.assertIn("group team-b reads s0012001-team-b", es["s0012001"].extracted)
        self.assertIn("group team-b of s0012001: own translation for these uses", es["s0012001-team-b"].extracted)
        self.assertEqual(key_only(es["s0012001-team-b"]).extracted[1:2],
                         ["group team-b of s0012001: own translation for these uses"])

class BuildTest(unittest.TestCase):
    def setUp(self):
        self.base = image()
        self.inv = inventory([RULE])

    def run_build(self, texts):
        rom = self.base.copy()
        res = inject(rom, self.base, self.inv, texts, {})
        self.assertEqual(verify(rom, self.base, res, texts, set()), [])
        return rom

    def test_group_reads_its_own_copy(self):
        rom = self.run_build({"s0012001": CM.encode("Agent"), "s0012001-team-b": CM.encode("Adept")})
        self.assertEqual(CM.decode(rom.data, rom.ptr_at(0x803))[0], "Agent")
        self.assertEqual(rom.ptr_at(0x803), LABEL)
        for at in (0x903, 0xA03):
            self.assertNotEqual(rom.ptr_at(at), LABEL)
            self.assertEqual(CM.decode(rom.data, rom.ptr_at(at))[0], "Adept")

    def test_copy_moves_no_other_text(self):
        texts = {"s0012001": CM.encode("Agent" * 4)}
        before = self.run_build(texts)
        after = self.run_build(dict(texts, **{"s0012001-team-b": CM.encode("Adept")}))
        self.assertEqual(after.ptr_at(0x803), before.ptr_at(0x803))
        changed = [i for i in range(len(after.data)) if after.data[i] != before.data[i]]
        copy = after.ptr_at(0x903)
        allowed = set(range(copy, copy + 6)) | {i for at in (0x903, 0xA03) for i in range(at, at + 4)}
        self.assertTrue(changed and set(changed) <= allowed)

    def test_same_translation_still_gets_a_copy(self):
        rom = self.run_build({"s0012001": CM.encode("Agent"), "s0012001-team-b": CM.encode("Agent")})
        self.assertNotEqual(rom.ptr_at(0x903), LABEL)
        self.assertEqual(CM.decode(rom.data, rom.ptr_at(0x903))[0], "Agent")

    def test_untranslated_group_follows_the_original(self):
        rom = self.run_build({"s0012001": CM.encode("Agent" * 3)})
        moved = rom.ptr_at(0x803)
        self.assertNotEqual(moved, LABEL)
        self.assertEqual([rom.ptr_at(at) for at in SITES], [moved] * 3)

    def test_without_declaration_the_build_is_unchanged(self):
        texts = {"s0012001": CM.encode("Agent")}
        plain = self.base.copy()
        inject(plain, self.base, inventory([]), texts, {})
        rom = self.base.copy()
        inject(rom, self.base, self.inv, texts, {})
        self.assertEqual(bytes(plain.data), bytes(rom.data))
