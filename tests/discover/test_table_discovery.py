import copy
import struct
import unittest
from types import SimpleNamespace

from gen3tl.core.charmap import Charmap
from gen3tl.core.rom import ROM_BASE, Rom
from gen3tl.discover import table_scope, tables
from gen3tl.profile import check, schema

ANCHORS = [{"index": 1, "text": "Bulbasaur"}, {"index": 2, "text": "Ivysaur"},
           {"index": 3, "text": "Venusaur"}]

def call(at, target):
    offset = (target - at - 4) & 0x7FFFFF
    return struct.pack("<HH", 0xF000 | offset >> 12, 0xF800 | (offset >> 1 & 0x7FF))

def fixture(base=0x400, stride=11, entry=0x220, reader_stride=None):
    rom = Rom(b"\xff" * 4096)
    rom.put_u32(0x144, ROM_BASE + base)
    for index, text in enumerate(["??????", "Bulbasaur", "Ivysaur", "Venusaur"]):
        rom.write(base + index * stride, Charmap().encode(text).ljust(stride, b"\xff"))
    rom.write(0x200, call(0x200, entry))
    pool = (entry + 32) & ~3
    load = 0x4900 | ((pool - ((entry + 10) & ~3)) // 4)
    rom.write(entry, struct.pack("<7H", 0xB500, 0x2200 | (reader_stride or stride), 0x4350,
                                 load, 0x1808, 0x7800, 0xBD00))
    rom.put_u32(pool, ROM_BASE + base)
    family = SimpleNamespace(anchors={"identity": {"header_offset": 0x100},
        "tables": {"header": {"species": 0x44}, "names": {"species": ANCHORS}}})
    return rom, family

class TableEvidenceTest(unittest.TestCase):
    def test_header_anchor_and_accessor_evidence_follow_relocated_tables_and_code(self):
        for base, stride, entry in [(0x400, 11, 0x220), (0x600, 12, 0x280)]:
            with self.subTest(base=base, stride=stride):
                rom, family = fixture(base, stride, entry)
                result = tables.run(rom, family)
                self.assertEqual(result.issues, [])
                self.assertEqual(len(result.facts), 1)
                fact = result.facts[0]
                self.assertEqual(fact.value, {"name": "species", "base": base, "stride": stride,
                                             "count": 4, "field_offset": 0, "field_length": stride})
                self.assertEqual(fact.section, "table")
                self.assertEqual(fact.tier, "crossref")
                methods = result.metadata["methods"]
                self.assertEqual(methods["header-block"][0]["site"], 0x144)
                self.assertEqual(methods["anchor-stride"][0]["anchors"], [base + i * stride for i in (1, 2, 3)])
                self.assertTrue(methods["accessor"][0]["readers"])
                self.assertIn(0x144, fact.evidence.anchors)
                self.assertTrue(any(entry <= site < entry + 14 for site in fact.evidence.code_sites))

    def test_names_without_linked_readers_are_not_promoted(self):
        rom, family = fixture()
        rom.write(0x200, b"\xff" * 4)
        result = tables.run(rom, family)
        self.assertEqual(result.facts, [])
        self.assertEqual(result.issues[0].evidence.method, "accessor")
        self.assertEqual(result.issues[0].status, "unresolved")
        self.assertIsNone(result.issues[0].tier)

    def extra_names(self, rom, reason=None):
        declaration = {"anchors": ANCHORS}
        if reason is not None:
            declaration["candidate_reason"] = reason
        family = SimpleNamespace(anchors={"identity": {"header_offset": 0x100},
            "tables": {"extra_names": {"demo": declaration}}})
        return tables.run(rom, family)

    def test_unindexed_names_need_a_declared_candidate_reason(self):
        for reason in (None, "", "Native indexing is not confirmed for this copy."):
            with self.subTest(reason=reason):
                rom, _ = fixture()
                rom.write(0x200, b"\xff" * 4)
                result = self.extra_names(rom, reason)
                self.assertEqual(result.facts, [])
                self.assertNotIn("table", result.data)
                issue, = result.issues
                self.assertEqual(issue.status, "candidate" if reason else "unresolved")
                self.assertEqual(issue.id, "table[name=demo]")
                self.assertIsNone(issue.tier)
                self.assertEqual(issue.evidence.anchors, (0x40B, 0x416, 0x421))
                self.assertEqual(dict(issue.evidence.counts),
                                 {"base": 0x400, "stride": 11, "rows": 4, "readers": 0})
                if reason:
                    self.assertEqual(issue.evidence.sentence, reason)

    def test_declared_candidates_still_require_unique_complete_names(self):
        for ambiguous in (False, True):
            with self.subTest(ambiguous=ambiguous):
                rom, _ = fixture()
                if ambiguous:
                    rom.write(0x600, rom.data[0x400:0x42C])
                else:
                    rom.write(0x416, b"\xbb" * 11)
                result = self.extra_names(rom, "Native indexing is not confirmed for this copy.")
                self.assertEqual(result.facts, [])
                issue, = result.issues
                self.assertEqual(issue.status, "unresolved")
                self.assertEqual(issue.evidence.method, "global-name-anchors")

    def test_declared_candidates_with_indexed_readers_are_verified(self):
        rom, _ = fixture()
        result = self.extra_names(rom, "Native indexing is not confirmed for this copy.")
        self.assertEqual(result.issues, [])
        fact, = result.facts
        self.assertEqual((fact.id, fact.status, fact.tier), ("table[name=demo]", "verified", "crossref"))
        self.assertTrue(fact.evidence.code_sites)

    def test_changed_stride_requires_the_reader_to_agree(self):
        rom, family = fixture(stride=12, reader_stride=11)
        result = tables.run(rom, family)
        self.assertEqual(result.facts, [])
        self.assertIn("confirmed linked indexed reader", result.issues[0].evidence.sentence)

    def test_bad_header_and_unterminated_anchor_fail_before_emission(self):
        rom, family = fixture()
        rom.put_u32(0x144, ROM_BASE + len(rom) + 4)
        result = tables.run(rom, family)
        self.assertEqual(result.facts, [])
        self.assertEqual(result.issues[0].evidence.method, "header-block")
        rom, family = fixture()
        rom.write(0x400 + 22, b"\xbb" * 11)
        self.assertEqual(tables.run(rom, family).facts, [])

    def test_two_anchor_strides_stay_ambiguous(self):
        rom = Rom(b"\xff" * 4096)
        for offset in (0, 8, 16):
            rom.write(0x400 + offset, Charmap().encode("A"))
        self.assertIsNone(tables.name_stride(rom, 0x400, [{"index": 1, "text": "A"}]))

    def test_item_row_ids_preserve_intentionally_empty_name_rows(self):
        rom = Rom(b"\xff" * 8192)
        labels = ["Item", "Master Ball", "Ultra Ball", "Great Ball"]
        for index in range(75):
            at = 0x400 + index * 44
            rom.write(at, Charmap().encode(labels[index] if index < 4 else "Item").ljust(14, b"\xff"))
            rom.write(at + 14, struct.pack("<H", index))
        rom.write(0x400 + 54 * 44, b"\xff")
        rom.write(0x400 + 55 * 44 + 14, struct.pack("<H", 0))
        rom.write(0x400 + 56 * 44 + 14, struct.pack("<H", 42))
        binding = tables.item_names(rom, 0x400, [{"index": i, "text": labels[i]} for i in (1, 2, 3)])
        self.assertIsNotNone(binding)
        self.assertEqual((binding.stride, binding.length, binding.count), (44, 14, 75))


class StructuralScopeTest(unittest.TestCase):
    def test_manual_fields_do_not_change_membership_or_raw_coverage(self):
        value = {"name": "demo", "base": 0x400, "stride": 11, "count": 4,
                 "windows": [{"width": 60}], "pad": 255}
        expected = schema.Fact("table[name=demo]", "table", "table", value)
        actual = schema.Fact(expected.id, "table", "table", {k: v for k, v in value.items() if k not in ("pad", "windows")},
                             source="tables/1.0.0", tier="crossref")
        report = table_scope.compare([expected], [actual])
        self.assertEqual(report["facts"][0]["outcome"], "reproduced")
        self.assertEqual(report["sections"]["table"]["structural"], 1)
        self.assertEqual(check.compare(value, actual.value), ("extends", ("windows", "pad")))
        changed = copy.deepcopy(expected)
        changed.value["windows"] = [{"width": 999}]
        changed.tier = "ingame"
        self.assertEqual(table_scope.classify(changed), table_scope.classify(expected))
        actual.value["count"] = 3
        self.assertEqual(table_scope.compare([expected], [actual])["facts"][0]["outcome"], "unresolved")
        self.assertTrue(table_scope.classify(expected)["included"])

    def test_family_defaults_are_not_independent_rom_discovery(self):
        expected = schema.Fact("pokedex.abilities", "pokedex.abilities", "table", {"base": 0x400, "count": 4})
        actual = schema.Fact(expected.id, expected.section, "table", expected.value, scope="family", source="tables/1.0.0")
        self.assertEqual(table_scope.compare([expected], [actual])["facts"][0]["outcome"], "unresolved")
        actual.scope, actual.source = "instance", "overlay"
        self.assertEqual(table_scope.compare([expected], [actual])["facts"][0]["outcome"], "unresolved")

    def test_child_claims_join_into_prose_free_field_maps(self):
        expected = schema.Fact("pokedex.dex.records.fields", "pokedex.dex", "table",
                               {"category": 0, "height": 6})
        actual = [schema.Fact("pokedex.dex.records.fields.category", "pokedex.dex", "table", 0,
                              source="tables/1.0.0", tier="crossref"),
                  schema.Fact("pokedex.dex.records.fields.height", "pokedex.dex", "table", 6,
                              source="tables/1.0.0", tier="crossref")]
        report = table_scope.compare([expected], actual)
        self.assertEqual(report["facts"][0]["outcome"], "reproduced")
        self.assertEqual(report["facts"][0]["tier"], "crossref")
        actual[1].value = 7
        self.assertEqual(table_scope.compare([expected], actual)["facts"][0]["outcome"], "unresolved")
