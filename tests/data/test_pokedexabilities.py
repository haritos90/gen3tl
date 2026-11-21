from test_roots import TOOLKIT_ROOT
"""Ability identity and description extraction."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))
from project_fixture import rom_evidence
from gen3tl.data import pokedexabilities as pa  # noqa: E402
from gen3tl.data import pokedexdata as pd  # noqa: E402

BASE = 0x100
DESCRIPTIONS = 0x200


def ability_block() -> dict:
    return {
        "base": BASE, "stride": 17, "count": 4, "evidence": "synthetic name records",
        "descriptions": {"base": DESCRIPTIONS, "stride": 4, "count": 2,
                         "evidence": "synthetic description pointers"},
    }


def rom_bytes() -> bytes:
    data = bytearray(0x400)
    data[DESCRIPTIONS:DESCRIPTIONS + 4] = (0x08000300).to_bytes(4, "little")
    data[DESCRIPTIONS + 4:DESCRIPTIONS + 8] = (0x08000340).to_bytes(4, "little")
    return bytes(data)


class AbilityDataTest(unittest.TestCase):
    def test_extracts_ids_offsets_and_description_links(self):
        records = pa.build(ability_block(), rom_bytes())
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0], {
            "id": 0, "offset": f"0x{BASE:06x}", "name": "abilities:0", "description": "s0000300"})
        self.assertEqual(records[1]["description"], "s0000340")
        self.assertEqual(records[1]["offset"], f"0x{BASE + 17:06x}")
        self.assertEqual([row["id"] for row in records], [0, 1])
        self.assertEqual(pa.build(ability_block(), rom_bytes()), records)

    def test_rejects_missing_proof(self):
        block = ability_block()
        del block["evidence"]
        del block["descriptions"]["evidence"]
        self.assertEqual(pa.check(block), ["evidence", "descriptions evidence"])

    def test_integrates_with_the_dataset_and_verification(self):
        block = {
            "name_table": "species",
            "species": {"base": 0, "stride": 1, "count": 1, "first": 0,
                        "evidence": "synthetic", "fields": {"hp": 0}},
            "abilities": ability_block(),
        }
        doc = pd.build(block, rom_bytes(), {"id": "demo"})
        self.assertEqual(len(doc["abilities"]), 2)
        self.assertEqual(doc["provenance"]["abilities"]["offset"], f"0x{BASE:06x}")
        self.assertEqual(doc["provenance"]["ability_descriptions"]["offset"], f"0x{DESCRIPTIONS:06x}")
        self.assertEqual(pd.verify_doc(doc, block, "demo", doc["rom"]["sha1"]), [])
        broken = dict(doc)
        del broken["abilities"]
        self.assertIn("abilities", pd.verify_doc(broken, block, "demo", doc["rom"]["sha1"])[0])

    def presentation(self):
        block = ability_block()
        block["presentation"] = {
            "evidence": "Synthetic species selectors.", "rom": [rom_evidence(0, "00")],
            "names": [
                {"ability": 1, "value": 2, "dex": [20], "evidence": "National number match."},
                {"ability": 1, "value": 3, "types": [8], "evidence": "Original type match."},
            ],
            "descriptions": [
                {"ability": 1, "value": 0x380, "species": [10], "evidence": "Raw species match."},
            ],
        }
        return block

    def test_selectors_preserve_identity_roles_and_independent_fallbacks(self):
        block = self.presentation()
        abilities = pa.build(block, rom_bytes())
        self.assertEqual(abilities[1]["alternate_names"], ["abilities:2", "abilities:3"])
        species = [{"slot": slot, "dex": dex, "types": types,
                    "ability_details": [{"id": 1, "hidden": hidden, "regular": not hidden}]}
                   for slot, dex, types, hidden in [(10, 20, [8], True), (11, 20, [], False),
                                                     (12, 21, [8], False), (13, 21, [1], True)]]
        pa.apply(block, species, abilities)
        self.assertEqual([(s["ability_details"][0]["name"], s["ability_details"][0]["description"])
                          for s in species], [
            ("abilities:2", "s0000380"), ("abilities:2", "s0000340"),
            ("abilities:3", "s0000340"), ("abilities:1", "s0000340")])
        self.assertTrue(species[0]["ability_details"][0]["hidden"])
        self.assertEqual(species[0]["ability_details"][0]["id"], 1)
        self.assertEqual(pa.verify(block, species, abilities), [])
        species[0]["ability_details"][0]["name"] = "abilities:1"
        self.assertEqual(pa.verify(block, species, abilities), ["ability presentation: regenerate species slot 10"])

    def test_selectors_reject_missing_evidence_invalid_ids_and_changed_rom(self):
        block = self.presentation()
        self.assertEqual(pa.check(block), [])
        del block["presentation"]["names"][0]["evidence"]
        block["presentation"]["names"][1]["value"] = 4
        block["presentation"]["descriptions"][0]["species"] = []
        self.assertEqual(pa.check(block), ["presentation names 0", "presentation names 1 value",
                                          "presentation descriptions 0 species"])
        with self.assertRaisesRegex(ValueError, "ROM mismatch"):
            pa.build(self.presentation(), b"\x01" + rom_bytes()[1:])

    def test_unknown_ability_ids_fail_instead_of_hiding_bad_slots(self):
        with self.assertRaisesRegex(ValueError, "species 10: unknown ability 3"):
            pa.apply(self.presentation(), [{"slot": 10, "ability_details": [{"id": 3}]}],
                     pa.build(self.presentation(), rom_bytes()))

    def test_dataset_integrates_presentation_and_rejects_stale_text(self):
        data = bytearray(rom_bytes())
        data[0x40] = 1
        block = {"name_table": "species", "abilities": self.presentation(),
                 "species": {"base": 0x40, "stride": 1, "count": 1, "first": 0,
                             "fields": {"ability1": 0}, "evidence": "Synthetic species slots."}}
        block["abilities"]["presentation"]["names"][0] = {
            "ability": 1, "value": 2, "species": [0], "evidence": "Synthetic raw species match."}
        doc = pd.build(block, bytes(data), {"id": "demo"})
        self.assertEqual(doc["species"][0]["ability_details"][0]["name"], "abilities:2")
        self.assertEqual(pd.verify_doc(doc, block, "demo", doc["rom"]["sha1"]), [])
        doc["species"][0]["ability_details"][0]["name"] = "abilities:1"
        self.assertIn("ability presentation: regenerate species slot 0",
                      pd.verify_doc(doc, block, "demo", doc["rom"]["sha1"]))

    def test_description_free_profiles_keep_their_identity_records(self):
        block = ability_block()
        del block["descriptions"]
        self.assertEqual(len(pa.build(block, rom_bytes())), 4)

    def test_preserved_form_abilities_follow_sources_instead_of_target_slots(self):
        block = self.presentation()
        block["presentation"]["inherit_forms"] = {
            "method": 42, "parameter_min": 1, "evidence": "Synthetic ability-preserving transformation."}
        abilities = pa.build(block, rom_bytes())
        source = {"slot": 10, "dex": 20, "ability_details": [{"id": 1, "regular": False, "hidden": True}],
                  "evolution": [{"method": 42, "parameter": 1, "target": 11}]}
        target = {"slot": 11, "dex": 20, "ability_details": [{"id": 0, "regular": True, "hidden": False}],
                  "evolution": [{"method": 42, "parameter": 0, "target": 10}]}
        species = [source, target]
        pa.apply(block, species, abilities)
        self.assertEqual(target["abilities"], [1])
        self.assertEqual(target["ability_details"], [{"id": 1, "regular": False, "hidden": True,
                                                     "sources": [10], "name": "abilities:2", "description": "s0000340"}])
        self.assertEqual(target["stored_ability_details"][0]["id"], 0)
        self.assertNotIn("stored_ability_details", source)
        self.assertEqual(pa.verify(block, species, abilities), [])
        target["ability_details"][0]["hidden"] = False
        self.assertIn("ability inheritance: regenerate species slot 11", pa.verify(block, species, abilities))


if __name__ == "__main__":
    unittest.main()
