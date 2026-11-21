import unittest
from pathlib import Path

from gen3tl.catalog.catalog import build_entries
from gen3tl.core.charmap import Charmap
from gen3tl.discover.inventory import build, report, text_modes
from gen3tl.profile.profile import Profile
from gen3tl.core.rom import Rom, ptr_bytes

CM = Charmap()

class ClassifiedStringTest(unittest.TestCase):
    def setUp(self):
        data = bytearray(b"\xff" * 0x20000)
        for offset, text in ((0x12001, "1"), (0x13001, "Hello!")):
            raw = CM.encode(text)
            data[offset:offset + len(raw)] = raw
        data[0x801:0x807] = b"\x0f\x00" + ptr_bytes(0x13001)
        self.rom = Rom(data)
        self.rows = [{"name": "short-label", "action": "include", "offset": 0x12001,
                      "classifier": "text-field"},
                     {"name": "binary-data", "action": "exclude", "offset": 0x13001,
                      "classifier": "bounded-payload"}]

    def inventory(self, strings):
        return build(Profile({"strings": strings}, Path("test.toml")), self.rom)

    def test_outcomes_replace_declarations_without_changing_inventory(self):
        manual = {"include": [0x12001], "exclude": ["s0013001"]}
        expected = self.inventory(manual)
        self.assertEqual(self.inventory({"classified": self.rows}), expected)
        self.assertEqual(self.inventory(dict(manual, classified=self.rows)), expected)
        self.assertEqual(self.inventory({"classified": [dict(row, evidence="A typed reader binds the field.")
                                                        for row in self.rows]}), expected)
        self.assertEqual([row["offset"] for row in expected["entries"]], [0x12001])
        self.assertEqual(expected["excluded"], ["s0013001"])

    def test_invalid_outcomes_fail_before_scanning(self):
        for rows in (None, {}, [None], [dict(self.rows[0], offset=True)],
                     [dict(self.rows[0], offset=-1)], [dict(self.rows[0], offset=len(self.rom))],
                     [dict(self.rows[0], classifier="")], [dict(self.rows[0], action="relocate")],
                     [dict(self.rows[0], extra=True)]):
            with self.subTest(rows=rows), self.assertRaisesRegex(ValueError, "strings.classified"):
                self.inventory({"classified": rows})

class SourceGlyphFallbackTest(unittest.TestCase):
    def test_missing_stale_and_skipped_replacements_remain_unsafe(self):
        from gen3tl.discover.inventory import untranslated_slot_usage
        from gen3tl.catalog.po import PoEntry
        sid, original = "s0459434", "{39}ñ{39}ñ{0A}ÂÉ+"
        inv = {"entries": [{"id": sid, "text": original, "flags": []}]}
        translated = PoEntry(sid, original, "Круговой обмен")
        cat = {sid: (Path("test.po"), translated)}
        check = lambda catalog, included: untranslated_slot_usage(inv, {0x39}, CM, catalog, included)
        self.assertTrue(check({}, {sid}))
        self.assertTrue(check(cat, set()))
        self.assertEqual(check(cat, {sid}), [])
        translated.set_fuzzy(True)   # translated, awaiting approval: built alike
        self.assertEqual(check(cat, {sid}), [])
        self.assertEqual(check(cat, None), [])
        translated.msgid = "Different source"
        self.assertTrue(check(cat, {sid}))

class DeclaredPlaceholderModeTest(unittest.TestCase):
    def setUp(self):
        self.data = bytearray(b"\xff" * 0x20000)
        self.sid, self.offset = "s0012001", 0x12001
        raw = CM.encode("Hello {PLAYER}! {FD 07}")
        self.data[self.offset:self.offset + len(raw)] = raw
        self.data[0x801:0x807] = b"\x0f\x00" + ptr_bytes(self.offset)
        self.data[0x9001:0x9005] = ptr_bytes(self.offset)
        self.refs = [{"at": 0x803, "kind": "script:loadpointer"},
                     {"at": 0x9001, "kind": "unverified"}]
        self.rule = {"id": self.sid, "mode": "field", "refs": self.refs,
                     "evidence": "The second match lies inside sample data."}

    def inventory(self, rules):
        return build(Profile({"strings": {"text_mode": rules}}, Path("test.toml")), Rom(self.data))

    def test_declaration_changes_names_preserving_bytes_and_references(self):
        before = next(e for e in self.inventory([])["entries"] if e["id"] == self.sid)
        after = next(e for e in self.inventory([self.rule])["entries"] if e["id"] == self.sid)
        self.assertEqual(before["mode"], "battle")
        self.assertEqual(after["mode"], "field")
        self.assertEqual(after["text"], "Hello {PLAYER}! {FD 07}")
        self.assertEqual(CM.encode(before["text"]), CM.encode(after["text"]))
        self.assertEqual(after["refs"], before["refs"])
        self.assertEqual(after["flags"], before["flags"])
        self.assertEqual(after["mode_evidence"]["inferred_rule"], "mixed-references")
        self.assertEqual(after["mode_evidence"]["rule"], "profile-text-mode")
        self.assertEqual(after["mode_evidence"]["evidence"], self.rule["evidence"])

    def test_unclassified_reference_holds_relocation_without_changing_kinds(self):
        entry = next(e for e in self.inventory([])["entries"] if e["id"] == self.sid)
        self.assertEqual(entry["refs"], self.refs)
        self.assertEqual(entry["relocation"], {"status": "held", "unclassified": [0x9001]})
        self.assertEqual([ref["status"] for ref in entry["reference_classification"]],
                         ["reference", "unclassified"])
        self.assertIn(f"| {self.sid} | 0x9001 |", report(self.inventory([])))

    def test_unread_reference_is_classified_without_changing_existing_fields(self):
        before = self.inventory([])
        profile = Profile({"coverage": {"unread_reference": [
            {"at": 0x9001, "evidence": "The menu has no callers."}]}}, Path("test.toml"))
        after = build(profile, Rom(self.data))
        for old, new in zip(before["entries"], after["entries"]):
            self.assertEqual({k: v for k, v in old.items() if k not in ("relocation", "reference_classification")},
                             {k: v for k, v in new.items() if k not in ("relocation", "reference_classification")})
        entry = next(e for e in after["entries"] if e["id"] == self.sid)
        self.assertEqual(entry["relocation"], {"status": "classified", "unclassified": []})
        self.assertEqual(entry["reference_classification"][1]["status"], "unread")

    def test_added_removed_and_reclassified_references_fail(self):
        for refs in (self.refs[:1], self.refs + [{"at": 0x8000, "kind": "table"}],
                     [self.refs[0], {"at": 0x9001, "kind": "table"}]):
            with self.subTest(refs=refs), self.assertRaisesRegex(ValueError, "reference set changed"):
                self.inventory([dict(self.rule, refs=refs)])

    def test_reference_order_does_not_change_the_set(self):
        self.assertEqual(self.inventory([self.rule]),
                         self.inventory([dict(self.rule, refs=list(reversed(self.refs)))]))

    def test_missing_target_fails(self):
        with self.assertRaisesRegex(ValueError, "missing from inventory"):
            self.inventory([dict(self.rule, id="s0012010")])

    def test_invalid_declarations_fail(self):
        cases = [None, {}, [None], [dict(self.rule, extra=True)],
                 [dict(self.rule, id="items:1")], [dict(self.rule, mode="other")],
                 [dict(self.rule, evidence="")], [dict(self.rule, evidence="two\nlines")],
                 [dict(self.rule, refs=[])], [dict(self.rule, refs=None)],
                 [dict(self.rule, refs=[{"at": True, "kind": "table"}])],
                 [dict(self.rule, refs=[{"at": -1, "kind": "table"}])],
                 [dict(self.rule, refs=[{"at": 0, "kind": ""}])],
                 [dict(self.rule, refs=[self.refs[0], self.refs[0]])],
                 [self.rule, self.rule]]
        for rules in cases:
            with self.subTest(rules=rules), self.assertRaises(ValueError):
                text_modes(rules)

class PlaceholderModeTest(unittest.TestCase):
    def setUp(self):
        self.data = bytearray(b"\xff" * 0x20000)
        self.prof = Profile({}, Path("test.toml"))
        self.target = 0x12001

    def put(self, at, raw):
        self.data[at:at + len(raw)] = raw

    def text(self, at, text):
        self.put(at, CM.encode(text))

    def entries(self):
        return {e["offset"]: e for e in build(self.prof, Rom(self.data))["entries"]}

    def battle_region(self):
        for i in range(5):
            at = 0x11001 + 0x100 * i
            self.text(at, "Hello {B_ATK_NAME_WITH_PREFIX}!")
            self.put(0x4000 + 4 * i, ptr_bytes(at))

    def assert_roundtrip(self, entry):
        raw = self.data[entry["offset"]:entry["offset"] + entry["length"]]
        self.assertEqual(CM.encode(entry["text"]), raw)
        self.assertEqual(CM.decode(raw, mode=entry["mode"])[0], entry["text"])

    def test_field_commands_override_battle_region(self):
        self.battle_region()
        commands = {
            "loadpointer": b"\x0f\x00",
            "message": b"\x67",
            "bufferstring": b"\x85\x07",
            "trainerbattle": b"\x5c\x03\x00\x00\x00\x00",
            "autoscroll": b"\x9b",
            "vmessage": b"\xbd",
        }
        for i, prefix in enumerate(commands.values()):
            at = self.target + 0x100 * i
            self.text(at, "Hello {PLAYER}!")
            self.put(0x801 + 0x20 * i, prefix + ptr_bytes(at))
        entries = self.entries()
        for i, (kind, prefix) in enumerate(commands.items()):
            with self.subTest(kind=kind):
                e = entries[self.target + 0x100 * i]
                self.assertEqual(e["refs"], [{"at": 0x801 + 0x20 * i + len(prefix),
                                              "kind": "script:" + kind}])
                self.assertEqual(e["mode"], "field")
                self.assertEqual(e["text"], "Hello {PLAYER}!")
                self.assertEqual(e["mode_evidence"]["rule"], "script-text-only")
                self.assertEqual(e["mode_evidence"]["fallback_mode"], "battle")
                self.assertIn("battle-region", e["mode_evidence"]["fallback_reasons"])
                self.assert_roundtrip(e)

    def test_bufferstring_reaches_buffer_0x0a(self):
        # Big Mo's {FD 0C} buffer
        self.text(self.target, "And that goes for you!")
        self.put(0x801, b"\x85\x0a" + ptr_bytes(self.target))
        self.text(self.target + 0x100, "Not a script, really.")
        self.put(0x901, b"\x85\x0b" + ptr_bytes(self.target + 0x100))
        entries = self.entries()
        self.assertEqual(entries[self.target]["refs"], [{"at": 0x803, "kind": "script:bufferstring"}])
        self.assertNotEqual(entries[self.target + 0x100]["refs"][0]["kind"], "script:bufferstring")

    def test_field_commands_override_extended_placeholder_heuristic(self):
        text = "Hello {PLAYER}!\\nYour {STR_VAR_1} has {FD 0C}{FD 07}."
        self.text(self.target, text)
        self.put(0x801, b"\x0f\x00" + ptr_bytes(self.target))
        self.put(0x901, b"\x67" + ptr_bytes(self.target))
        e = self.entries()[self.target]
        self.assertEqual(e["mode"], "field")
        self.assertEqual(e["text"], text)
        self.assertEqual(e["mode_evidence"]["reference_kinds"],
                         ["script:loadpointer", "script:message"])
        self.assertEqual(e["mode_evidence"]["fallback_reasons"], ["placeholder-bytes"])
        self.assert_roundtrip(e)

    def test_mixed_references_keep_battle_fallback(self):
        self.battle_region()
        self.text(self.target, "Hello {PLAYER}!")
        self.put(0x801, b"\x0f\x00" + ptr_bytes(self.target))
        for kind, at in (("table", 0x4014), ("aligned", 0x8000), ("unverified", 0x9001)):
            with self.subTest(kind=kind):
                for pos in (0x4014, 0x8000, 0x9001):
                    self.put(pos, b"\xff" * 4)
                self.put(at, ptr_bytes(self.target))
                e = self.entries()[self.target]
                self.assertEqual({r["kind"] for r in e["refs"]}, {"script:loadpointer", kind})
                self.assertEqual(e["mode"], "battle")
                self.assertEqual(e["mode_evidence"]["rule"], "mixed-references")
                self.assert_roundtrip(e)

    def test_mixed_and_unknown_references_keep_field_fallback(self):
        self.text(self.target, "Hello {PLAYER}!")
        self.put(0x9001, ptr_bytes(self.target))
        e = self.entries()[self.target]
        self.assertEqual(e["mode"], "field")
        self.assertEqual(e["mode_evidence"]["rule"], "reference-fallback")
        self.put(0x801, b"\x0f\x00" + ptr_bytes(self.target))
        e = self.entries()[self.target]
        self.assertEqual(e["mode"], "field")
        self.assertEqual(e["mode_evidence"]["rule"], "mixed-references")

    def test_battle_script_table_and_code_keep_battle_names(self):
        self.battle_region()
        refs = {
            "script:setword": (0x801, b"\xfa\x00\x00\x03\x02"),
            "table": (0x4014, b""),
            "pool": (0x8008, b""),
        }
        self.put(0x8000, b"\x01\x48")  # THUMB ldr r0, [pc, #4]
        for i, (at, prefix) in enumerate(refs.values()):
            target = self.target + 0x100 * i
            self.text(target, "Hello {B_BUFF2}!")
            self.put(at, prefix + ptr_bytes(target))
        entries = self.entries()
        for i, kind in enumerate(refs):
            with self.subTest(kind=kind):
                e = entries[self.target + 0x100 * i]
                self.assertEqual([r["kind"] for r in e["refs"]], [kind])
                self.assertEqual(e["mode"], "battle")
                self.assertEqual(e["text"], "Hello {B_BUFF2}!")
                self.assertEqual(e["mode_evidence"]["rule"], "reference-fallback")
                self.assert_roundtrip(e)

    def test_chain_is_not_a_field_text_command(self):
        # a chain can follow setword; the prefix is no evidence
        self.text(self.target, "Hello {B_ATK_NAME_WITH_PREFIX}!")
        self.text(self.target + 0x100, "Hello {B_PLAYER_MON2_NAME}!")
        self.put(0x801, b"\xfa\x00\x00\x03\x02" + ptr_bytes(self.target)
                 + ptr_bytes(self.target + 0x100))
        e = self.entries()[self.target + 0x100]
        self.assertEqual([r["kind"] for r in e["refs"]], ["script:chain"])
        self.assertEqual(e["mode"], "battle")
        self.assertEqual(e["mode_evidence"]["rule"], "reference-fallback")

    def test_pointer_run_and_battle_script_fallback_without_region_threshold(self):
        self.text(self.target, "Hello {B_ATK_NAME_WITH_PREFIX}!")
        self.text(self.target + 0x100, "Hello {B_BUFF2}!")
        self.text(self.target + 0x200, "Hello {B_BUFF2}!")
        self.put(0x4000, ptr_bytes(self.target) + ptr_bytes(self.target + 0x100))
        self.put(0x801, b"\xfa\x00\x00\x03\x02" + ptr_bytes(self.target + 0x200))
        entries = self.entries()
        for delta, reason in ((0x100, "pointer-run"), (0x200, "battle-script")):
            with self.subTest(reason=reason):
                e = entries[self.target + delta]
                self.assertEqual(e["mode"], "battle")
                self.assertEqual(e["mode_evidence"]["fallback_reasons"], [reason])
                self.assert_roundtrip(e)

    def test_unreferenced_include_keeps_fallback(self):
        self.text(self.target, "Hello {FD 0C}!")
        self.prof = Profile({"strings": {"include": [self.target]}}, Path("test.toml"))
        e = self.entries()[self.target]
        self.assertEqual(e["refs"], [])
        self.assertEqual(e["mode"], "battle")
        self.assertEqual(e["mode_evidence"]["rule"], "reference-fallback")

    def test_fixed_field_records_profile_evidence(self):
        self.text(self.target, "Hello")
        self.prof = Profile({"table": [{"name": "names", "base": self.target, "stride": 8,
                                       "count": 1, "field_length": 8}]}, Path("test.toml"))
        e = self.entries()[self.target]
        self.assertEqual(e["mode"], "field")
        self.assertEqual(e["mode_evidence"], {"rule": "fixed-field", "table": "names"})

    def test_field_override_does_not_change_other_entries_region_evidence(self):
        # region seeds survive when their modes become field
        for i in range(5):
            at = 0x11001 + 0x100 * i
            self.text(at, "Hello {FD 0C}!")
            self.put(0x801 + 0x20 * i, b"\x0f\x00" + ptr_bytes(at))
        self.text(self.target, "Hello {B_BUFF2}!")
        self.put(0x9001, ptr_bytes(self.target))
        entries = self.entries()
        self.assertTrue(all(entries[0x11001 + 0x100 * i]["mode"] == "field" for i in range(5)))
        self.assertEqual(entries[self.target]["mode"], "battle")
        self.assertEqual(entries[self.target]["mode_evidence"]["battle_region_count"], 5)

    def test_profile_pointer_reference_prevents_field_override(self):
        self.text(self.target, "Hello {FD 0C}!")
        self.put(0x801, b"\x0f\x00" + ptr_bytes(self.target))
        self.put(0x8000, ptr_bytes(self.target))
        self.prof = Profile({"table": [{"name": "names", "base": 0x8000, "stride": 4,
                                       "count": 1, "field_length": 4,
                                       "long_name_pointers": True}]}, Path("test.toml"))
        e = self.entries()[self.target]
        self.assertIn("table:names", [r["kind"] for r in e["refs"]])
        self.assertEqual(e["mode"], "battle")
        self.assertEqual(e["mode_evidence"]["rule"], "mixed-references")

    def test_report_counts_fallbacks_with_placeholders(self):
        self.text(self.target, "Hello {FD 0C}!")
        self.put(0x9001, ptr_bytes(self.target))
        inv = build(self.prof, Rom(self.data))
        self.assertIn("| reference-fallback | 1 |", report(inv))

class PointerTableTest(unittest.TestCase):
    # credit records {heading, names, flag}, 12 bytes.
    # as at 0x1FB0DB0.
    PAGES = {0x2001: "\\n\\nDirector\\n\\n\\n\\n", 0x2101: "\\n\\n\\n Skeli\\n\\n\\n",
             0x2201: "Map Design\\n\\n\\n\\n\\n\\n", 0x2301: "\\n Skeli\\n Nex\\n"}
    END = 0x2400  # the end record points at an empty string

    def setUp(self):
        self.data = bytearray(b"\xff" * 0x4000)
        for at, text in self.PAGES.items():
            blob = CM.encode(text)
            self.data[at:at + len(blob)] = blob
        heads, names = [0x2001, 0x2201, self.END], [0x2101, 0x2301, self.END]
        for i, (h, n) in enumerate(zip(heads, names)):
            self.data[0x800 + 12 * i:0x80C + 12 * i] = ptr_bytes(h) + ptr_bytes(n) + b"\x00" * 4
        self.table = {"name": "credits", "base": 0x800, "stride": 12, "count": 3, "fields": [0, 4],
                      "note": "keep the names", "evidence": "test table"}

    def build(self, tables):
        return build(Profile({"strings": {"pointer_table": tables}}, Path("test.toml")), Rom(self.data))

    def test_scan_misses_pages_that_open_with_an_empty_line(self):
        found = {e["offset"] for e in self.build([])["entries"]}
        self.assertEqual(found, {0x2201})

    def test_declared_table_catalogues_every_page(self):
        inv = self.build([self.table])
        entries = {e["offset"]: e for e in inv["entries"]}
        self.assertEqual(set(entries), set(self.PAGES))  # the empty end string is no entry
        for i, (at, text) in enumerate(self.PAGES.items()):
            with self.subTest(offset=at):
                e = entries[at]
                self.assertEqual((e["text"], e["table"], e["index"]), (text, "credits", i // 2))
                self.assertEqual(e["refs"], [{"at": 0x800 + 12 * (i // 2) + 4 * (i % 2), "kind": "table"}])
                self.assertEqual((e["category"], e["mode"], e["flags"]), ("data-table", "field", []))
        self.assertEqual(inv["pointer_tables"], [{k: v for k, v in self.table.items() if k != "evidence"}])

    def test_declared_table_is_one_catalog_group_with_its_note(self):
        groups = build_entries(self.build([self.table]), {"strings": {}}, CM, lambda b: 6, "test")
        self.assertEqual(list(groups), ["system/credits"])
        self.assertEqual([p.msgctxt for p in groups["system/credits"]], [f"s{at:07X}" for at in self.PAGES])
        self.assertIn("pointer table credits, record 1: keep the names", groups["system/credits"][2].extracted)

    def test_multiline_table_notes_fit_extracted_comment_lines(self):
        self.table["note"] = "Keep the names.\nTranslate the headings."
        groups = build_entries(self.build([self.table]), {"strings": {}}, CM, lambda b: 6, "test")
        self.assertIn("pointer table credits, record 1: Keep the names. Translate the headings.",
                      groups["system/credits"][2].extracted)


class PokedexEntrySpeciesTest(unittest.TestCase):
    """Pointer table texts tie to their dex species ids."""

    def setUp(self):
        self.data = bytearray(b"\xff" * 0x4000)
        for at, text in ((0x2001, "Bulbasaur entry"), (0x2101, "Shared entry"), (0x2201, "Unused entry")):
            blob = CM.encode(text)
            self.data[at:at + len(blob)] = blob
        for i, target in enumerate((0x2201, 0x2001, 0x2101, 0x2101)):   # record i is dex i; 2 and 3 share
            self.data[0x800 + 4 * i:0x804 + 4 * i] = ptr_bytes(target)
        # slot -> dex; slots 2 and 3 are dex 2 forms
        for slot, dex in ((1, 1), (2, 2), (3, 2), (4, 3)):
            self.data[0x600 + slot * 2:0x602 + slot * 2] = dex.to_bytes(2, "little")
        self.table = {"name": "pokedex_entries", "base": 0x800, "stride": 4, "count": 4, "fields": [0],
                      "species_dex_map": True, "note": "dex entry", "evidence": "test"}
        self.prof = Profile({"strings": {"pointer_table": [self.table]},
                             "pokedex": {"species": {"count": 5, "first": 1},
                                         "dex": {"map": {"base": 0x600, "stride": 2, "width": 2}}}},
                            Path("test.toml"))

    def test_every_record_names_its_species_ids(self):
        inv = build(self.prof, Rom(self.data))
        entries = {e["offset"]: e for e in inv["entries"]}
        self.assertEqual(entries[0x2001]["species"], ["species:1"])
        self.assertEqual(entries[0x2101]["species"], ["species:2", "species:3", "species:4"])
        self.assertNotIn("species", entries[0x2201])   # unreferenced text: no table tie

    def test_the_catalog_group_writes_the_species_reference(self):
        inv = build(self.prof, Rom(self.data))
        groups = build_entries(inv, {"strings": {}}, CM, lambda b: 6, "test")
        self.assertEqual(list(groups), ["system/pokedex-entries"])
        lines = {p.msgid: p.extracted for p in groups["system/pokedex-entries"]}
        self.assertIn("pointer table pokedex_entries, record 1: dex entry", lines["Bulbasaur entry"])
        self.assertIn("species: species:1", lines["Bulbasaur entry"])
        self.assertIn("species: species:2, species:3, species:4", lines["Shared entry"])
        self.assertNotIn("species:", lines["Unused entry"])

    def test_species_slot_names_each_record_its_slot(self):
        prof = Profile({"strings": {"pointer_table": [{k: v for k, v in self.table.items()
                                                        if k != "species_dex_map"}
                                                       | {"species_slot": True}]},
                        "pokedex": self.prof["pokedex"]}, Path("test.toml"))
        inv = build(prof, Rom(self.data))
        entries = {e["offset"]: e for e in inv["entries"]}
        self.assertEqual(entries[0x2001]["species"], ["species:1"])
        self.assertEqual(entries[0x2101]["species"], ["species:2", "species:3"])

    def test_a_declared_pointer_table_proves_entries_the_scan_misses(self):
        data = bytearray(b"\xff" * 0x8000)
        blob = CM.encode("Bulbasaur entry")
        data[0x4001:0x4001 + len(blob)] = blob
        data[0x1000:0x1004] = ptr_bytes(0x4001)   # an isolated pointer: no run
        prof = Profile({"strings": {"pointer_table": [{"name": "pokedex_entries", "base": 0x1000,
                                                        "stride": 152, "count": 1, "fields": [0],
                                                        "species_slot": True, "evidence": "test"}]}},
                        Path("test.toml"))
        inv = build(prof, Rom(data))
        e = next(x for x in inv["entries"] if x["id"] == "s0004001")
        self.assertEqual((e["category"], e["flags"]), ("data-table", []))
        self.assertEqual((e["table"], e["index"]), ("pokedex_entries", 0))
        self.assertEqual(e["species"], ["species:0"])

    def test_species_dex_map_needs_the_pokedex_blocks(self):
        prof = Profile({"strings": {"pointer_table": [self.table]}}, Path("test.toml"))
        with self.assertRaises(ValueError) as cm:
            build(prof, Rom(self.data))
        self.assertIn("[pokedex.dex.map]", str(cm.exception))

class SaveLinkTest(unittest.TestCase):
    """Save and link strings keep one flag; the catalog note says they are translated like all other text."""
    NOTE = "save-link: copied into save or link data; translated like all other text"

    def test_every_profile_key_marks_the_strings(self):
        data = bytearray(b"\xff" * 0x4000)
        for i, text in enumerate(("Box 1", "Egg", "Ace", "Hello")):
            blob = CM.encode(text)
            data[0x2001 + 0x100 * i:0x2001 + 0x100 * i + len(blob)] = blob
            data[0x800 + 4 * i:0x804 + 4 * i] = ptr_bytes(0x2001 + 0x100 * i)
        blob = CM.encode("Pikachu")
        data[0x3001:0x3001 + len(blob)] = blob
        prof = Profile({"table": [{"name": "species", "base": 0x3001, "stride": 11, "count": 1, "field_length": 11,
                                   "save_link": True, "save_link_latin": False}],
                        "strings": {"pointer_table": [{"name": "t", "base": 0x800, "stride": 4, "count": 4,
                                                       "fields": [0]}],
                                    "save_link": ["s0002001"], "save_link_letters": ["s0002101"],
                                    "save_link_defaults": ["s0002201"]}}, Path("test.toml"))
        from gen3tl.lang.language import save_link_keys
        inv = build(prof, Rom(data), save_link_keys=save_link_keys("ru"))
        flags = {e["id"]: e["flags"] for e in inv["entries"]}
        self.assertEqual(flags, {"s0002001": ["save-link"], "s0002101": ["save-link"], "s0002201": ["save-link"],
                                 "s0002301": [], "species:0": ["save-link"]})
        notes = {p.msgctxt: p.extracted for g in build_entries(inv, {"strings": {}}, CM, lambda b: 6, "test").values()
                 for p in g}
        for sid in ("s0002001", "s0002101", "s0002201", "species:0"):
            with self.subTest(sid=sid):
                self.assertIn(self.NOTE, notes[sid])
                self.assertFalse([n for n in notes[sid] if "Latin" in n])
        self.assertFalse([n for n in notes["s0002301"] if n.startswith("save-link")])
        for code in ("es", "de", "fr"):
            with self.subTest(code=code):
                other = build(prof, Rom(data), save_link_keys=save_link_keys(code))
                flags = {e["id"]: e["flags"] for e in other["entries"]}
                self.assertEqual(flags["s0002101"], ["save-link"])
                self.assertEqual(flags["s0002001"], ["save-link"])
                self.assertEqual(flags["s0002201"], ["save-link"])

class ShortLabelIncludeTest(unittest.TestCase):
    """A short code string is rejected unless the include list names it."""

    def setUp(self):
        self.data = bytearray(b"\xff" * 0x4000)
        blob = CM.encode(" A-Z")
        self.data[0x2001:0x2001 + len(blob)] = blob
        self.data[0xFF8:0xFFA] = b"\x01\x49"   # ldr r1, [pc, #4]: the word at 0x1000
        self.data[0x1000:0x1004] = ptr_bytes(0x2001)

    def entries(self, prof):
        return {e["offset"]: e for e in build(Profile(prof, Path("test.toml")), Rom(self.data))["entries"]}

    def test_scan_rejects_the_suffix(self):
        self.assertNotIn(0x2001, self.entries({}))

    def test_include_catalogues_it_with_its_pool_ref(self):
        e = self.entries({"strings": {"include": [0x2001]}})[0x2001]
        self.assertEqual((e["text"], e["refs"], e["category"]), (" A-Z", [{"at": 0x1000, "kind": "pool"}], "code"))


class TableBaseTest(unittest.TestCase):
    """A pool word holding the base of an indexed table."""
    NAMES = {0x2000: "Cheri", 0x2010: "Chesto", 0x2020: "Pecha"}

    def setUp(self):
        self.data = bytearray(b"\xff" * 0x4000)
        for i, (at, text) in enumerate(self.NAMES.items()):
            blob = CM.encode(text)
            self.data[at:at + len(blob)] = blob
            self.data[at + 8:at + 12] = b"\x02\x14\x00\x03"   # record data
            self.data[at + 12:at + 15] = CM.encode(f"T{i}")   # a second label
        self.data[0xFF8:0xFFA] = b"\x01\x49"   # ldr r1, [pc, #4]: the word at 0x1000
        self.data[0x1000:0x1004] = ptr_bytes(0x2000)
        self.decl = {"pointers": [0x1000], "id": "s0002000", "stride": 0x10, "count": 3,
                     "evidence": "test table"}

    def entries(self, decls):
        prof = Profile({"strings": {"table_base": decls}}, Path("test.toml"))
        return {e["offset"]: e for e in build(prof, Rom(self.data))["entries"]}

    def test_scan_sees_a_pooled_string(self):
        entries = self.entries([])
        self.assertNotIn(0x2010, entries)
        self.assertEqual(entries[0x2000]["refs"], [{"at": 0x1000, "kind": "pool"}])

    def test_every_member_is_catalogued_in_place(self):
        entries = self.entries([self.decl])
        for at, text in self.NAMES.items():
            with self.subTest(offset=at):
                e = entries[at]
                self.assertEqual((e["text"], e["refs"]), (text, [{"at": 0x1000, "kind": "table-base"}]))
                self.assertEqual((e["category"], e["flags"]), ("code", ["in-place-only"]))
        notes = {p.msgctxt: p.extracted for g in build_entries({"entries": list(entries.values())},
                                                               {"strings": {}}, CM, lambda b: 6, "test").values()
                 for p in g}
        self.assertIn("in place only: 6 bytes max", notes["s0002010"])

    def test_offsets_and_fields_name_the_members(self):
        decl = dict(self.decl, offsets=[0x0, 0x20])
        self.assertEqual({o for o, e in self.entries([decl]).items() if o in self.NAMES}, {0x2000, 0x2020})
        decl = dict(self.decl, count=2, fields=[0, 0xC])
        e = self.entries([decl])
        self.assertEqual([e[o]["text"] for o in (0x2000, 0x200C, 0x2010, 0x201C)], ["Cheri", "T0", "Chesto", "T1"])
        self.assertEqual((e[0x201C]["refs"], 0x202C in e), ([{"at": 0x1000, "kind": "table-base"}], False))

    def test_word_not_holding_the_base_fails(self):
        for bad in (dict(self.decl, pointers=[0x1004]), dict(self.decl, evidence=""),
                    dict(self.decl, offsets=[0x10])):
            with self.subTest(decl=bad), self.assertRaises(ValueError):
                self.entries([bad])

    # a declared field length is the slot.
    # berries: 7-byte name field, English up to 6 bytes.
    def test_declared_field_length_is_the_slot(self):
        decl = dict(self.decl, length=8, length_evidence="name[8]")
        entries = self.entries([decl])
        self.assertEqual([entries[at]["length"] for at in self.NAMES], [8, 8, 8])
        self.assertEqual([e["length"] for e in self.entries([self.decl]).values()], [6, 7, 6])   # undeclared
        notes = {p.msgctxt: p.extracted for g in build_entries({"entries": list(entries.values())},
                                                               {"strings": {}}, CM, lambda b: 6, "test").values()
                 for p in g}
        self.assertIn("in place only: 7 bytes max", notes["s0002000"])
        decl = dict(self.decl, count=2, fields=[0, 0xC], length=[8, 4], length_evidence="name, label")
        entries = self.entries([decl])
        self.assertEqual([entries[o]["length"] for o in (0x2000, 0x200C, 0x2010, 0x201C)], [8, 4, 8, 4])

    def test_misdeclared_field_length_fails(self):
        from gen3tl.discover.scan import table_base_problems
        from project_fixture import with_profile_notes
        ok = dict(self.decl, length=8, length_evidence="name[8]")
        self.assertEqual(table_base_problems(Rom(self.data), ok), [])
        self.assertEqual(table_base_problems(Rom(self.data), with_profile_notes(ok)), [])
        for bad, why in ((dict(ok, length=6), "does not end within its 6-byte field"),   # 'Chesto' is 7
                         (dict(ok, length=0x11), "runs into 0x2010"),
                         (dict(ok, length_evidence=""), "no evidence for the field length"),
                         (dict(ok, count=2, fields=[0, 0xC], length=[8]), "1 field lengths for 2 fields"),
                         (dict(ok, count=2, fields=[0, 0xC], length=[0xD, 4]), "runs into 0x200c"),
                         (dict(ok, count=2, fields=[0, 0xC], length=[8, 5]), "runs into 0x2010")):
            with self.subTest(decl=bad):
                self.assertTrue(any(why in p for p in table_base_problems(Rom(self.data), bad)),
                                table_base_problems(Rom(self.data), bad))
                with self.assertRaises(ValueError):
                    self.entries([bad])
