import copy
import unittest

from gen3tl.data import pokedexforms as forms
from gen3tl.data.pokedextexts import referenced

def block():
    return {"evidence": "synthetic name pointers and type fields", "labels": [
        {"species": 1, "text": "species:1", "full": True},
        {"species": 2, "text": "s0000100"}],
        "type_families": [{"base": 3, "dex": 10, "count": 2, "type_table": "types", "evidence": "synthetic"}]}

def records():
    return [{"slot": 1}, {"slot": 2}, {"slot": 3, "base_species": 3, "dex": 10, "types": [0, 0]},
            {"slot": 4, "base_species": 3, "dex": 10, "types": [23, 23]}, {"slot": 5}]

class FormNamesTest(unittest.TestCase):
    def test_declared_names_and_type_families_keep_unknowns_explicit(self):
        rows = records()
        self.assertEqual(forms.check(block(), 6), [])
        forms.apply(block(), rows)
        self.assertEqual([r["form_name"] for r in rows], ["species:1", "s0000100", "types:0", "types:23", None])
        self.assertEqual([r["form_name_full"] for r in rows], [True, False, False, False, False])
        self.assertEqual(referenced(rows), {"species:1", "s0000100", "types:0", "types:23"})

    def test_type_family_can_keep_its_default_base_name_plain(self):
        b = block()
        b["type_families"][0]["label_base"] = False
        rows = records()
        self.assertEqual(forms.check(b, 6), [])
        forms.apply(b, rows)
        self.assertEqual([r["form_name"] for r in rows[2:4]], [None, "types:23"])
        b["type_families"][0]["label_base"] = "no"
        self.assertIn("form_names type family label_base: boolean", forms.check(b, 6))

    def test_wrong_family_count_type_or_overlapping_labels_fail(self):
        for mutation, pattern in [
            (lambda rows: rows.pop(3), "membership differs"),
            (lambda rows: rows[3].update(types=[1, 2]), "not single-type"),
            (lambda rows: rows[3].update(dex=11), "membership differs"),
        ]:
            rows = records()
            mutation(rows)
            with self.assertRaisesRegex(ValueError, pattern):
                forms.apply(block(), rows)
        b = block()
        b["labels"].append({"species": 3, "text": "types:1"})
        with self.assertRaisesRegex(ValueError, "overlapping label"):
            forms.apply(b, records())

    def test_bad_references_duplicates_and_shapes_report_errors(self):
        for value in (None, [], "invalid"):
            self.assertTrue(forms.check(value, 6))
        b = block()
        b["labels"].append(copy.deepcopy(b["labels"][0]))
        self.assertIn("duplicate species", " ".join(forms.check(b, 6)))
        b["labels"][0]["text"] = "Invented form"
        b["labels"][0]["full"] = "yes"
        self.assertIn("catalog reference", " ".join(forms.check(b, 6)))
        self.assertIn("boolean", " ".join(forms.check(b, 6)))

    def test_gender_symbol_requires_the_declared_gender_byte(self):
        b, rows = block(), records()
        b["labels"][1].update(text="page:Female form", expected_gender=254)
        with self.assertRaisesRegex(ValueError, "species 2 gender differs"):
            forms.apply(b, rows)
        rows[1]["gender_ratio"] = 254
        forms.apply(b, rows)
        self.assertEqual(rows[1]["form_name"], "page:Female form")
        b["labels"][1]["expected_gender"] = "254"
        self.assertIn("expected_gender", " ".join(forms.check(b, 6)))


class FormBrowsingTest(unittest.TestCase):
    def setUp(self):
        self.policy = {"compare": list(forms.COMPARISONS), "preserve_kinds": ["mega"],
                       "preserve_methods": [99], "battle_only": [6], "evidence": "Synthetic gameplay records."}
        base = {"slot": 1, "base_species": 1, "types": [1, 1], "base_stats": {"hp": 50, "attack": 60},
                "abilities": [2], "ability_details": [{"id": 2, "regular": True, "hidden": False}],
                "learnset": [{"method": 0, "move": 3, "level": 5, "index": 0, "offset": "0x100"}],
                "evolution": []}
        self.rows = [dict(copy.deepcopy(base), slot=slot) for slot in range(1, 9)]
        self.rows[0]["evolution"] = [{"method": 98, "parameter": 1, "target": 7, "kind": "mega"},
                                     {"method": 99, "parameter": 1, "target": 8}]

    def test_each_gameplay_difference_lists_a_form_with_its_reason(self):
        self.rows[1]["types"] = [1, 2]
        self.rows[2]["base_stats"]["attack"] = 70
        self.rows[3]["ability_details"][0]["hidden"] = True
        self.rows[4]["learnset"][0]["level"] = 6
        self.rows[5]["base_stats"]["hp"] = 100
        self.rows[6]["base_stats"]["hp"] = 100
        self.rows[7]["base_stats"]["hp"] = 100
        result = forms.classify(self.policy, self.rows)
        self.assertNotIn(1, result)
        for slot, field in enumerate(forms.COMPARISONS, 2):
            self.assertEqual(result[slot], {"separate": True, "reason": "gameplay_difference", "differences": [field]})
        self.assertEqual(result[6]["reason"], "battle_only")
        for slot in (7, 8):
            self.assertEqual(result[slot]["reason"], "preserved_transformation")
            self.assertFalse(result[slot]["separate"])

    def test_cosmetics_and_storage_differences_remain_grouped(self):
        self.rows[1].update(form_name="page:Spotted", sprite={"front": "different.png"}, gender_ratio=254,
                            types=[1], evolution=[{"method": 4, "target": 90}])
        self.rows[1]["learnset"][0].update(offset="0x200", index=100, species=2)
        self.rows[1]["learnset"] *= 2
        result = forms.classify(self.policy, self.rows)
        self.assertEqual(result[2], {"separate": False, "reason": "same_gameplay", "differences": []})

    def test_effective_inherited_learnsets_exclude_storage_and_keep_level_up(self):
        self.rows[0]["learnset"].append({"method": 1, "move": 4, "level": None})
        self.rows[1]["learnset_species"] = 1
        result = forms.classify(self.policy, self.rows)
        self.assertEqual(result[2]["differences"], [])
        self.rows[1]["learnset"][0]["move"] = 5
        self.assertEqual(forms.classify(self.policy, self.rows)[2]["differences"], ["learnset"])

    def test_missing_traits_or_base_cannot_silently_classify_forms(self):
        for key in forms.COMPARISONS:
            with self.subTest(key=key):
                rows = copy.deepcopy(self.rows)
                rows[1][key] = None
                if key == "abilities":
                    rows[1]["ability_details"] = None
                with self.assertRaisesRegex(ValueError, "missing " + key):
                    forms.classify(self.policy, rows)
        self.rows[1]["base_species"] = 100
        with self.assertRaisesRegex(ValueError, "base species 100 missing"):
            forms.classify(self.policy, self.rows)

    def test_invalid_browsing_declarations_fail_validation(self):
        self.assertEqual(forms.check_browsing(self.policy, 9), [])
        for value in (None, [], "invalid"):
            self.assertTrue(forms.check_browsing(value, 9))
        for key, value in (("compare", ["sprite"]), ("compare", None), ("battle_only", [99]),
                           ("battle_only", [6, 6]), ("battle_only", [{}]), ("preserve_methods", "99"),
                           ("preserve_kinds", [1]), ("evidence", "")):
            with self.subTest(key=key, value=value):
                self.assertTrue(forms.check_browsing(dict(self.policy, **{key: value}), 9))
