import copy
import unittest

from gen3tl.discover import flow_facts, reach


class FlowFactsTest(unittest.TestCase):
    def setUp(self):
        self.inventory = {"entries": [{"id": "line", "offset": 0x100}]}

    def test_context_projection_keeps_sources_and_unknown_paths(self):
        row = {"uses": [{"at": 0x80, "trainer": {"gender": "female"}}],
               "speakers": [{"colour": "04", "gender": "female"}],
               "inferred_map": None, "identity": {"speaker": {"sources": []}},
               "slots": {"STR_VAR_1": {"fills": [["string", "word"]], "unfilled": 1}}}
        study = {"stats": {"maps": 1}, "strings": {"line": row}}
        result = flow_facts.collect("context", study, self.inventory, {})
        self.assertEqual(flow_facts.project(result), study)
        item, = result.facts
        self.assertEqual(item.tier, "shape")
        self.assertEqual(item.evidence.anchors, (0x100,))
        self.assertEqual(item.evidence.code_sites, (0x80,))
        self.assertEqual(dict(item.evidence.counts)["trainers"], 1)
        self.assertIn("assumptions", item.evidence.sentence)
        row["slots"].clear()
        self.assertTrue(flow_facts.project(result)["strings"]["line"]["slots"])

    def test_runtime_observation_never_enters_static_facts(self):
        study = {"stats": {}, "strings": {"line": {"class": "unreachable", "evidence": []}}}
        profile = {"reach": {"observed": [{"ids": ["line"], "at": 0x40,
                                          "evidence": "Verified runtime display."}]}}
        result = flow_facts.collect("reach", study, self.inventory, profile)
        before = copy.deepcopy(flow_facts.document(result))
        merged = reach.render(result, profile, self.inventory)
        self.assertEqual(merged["strings"]["line"]["class"], "reachable")
        self.assertEqual(merged["strings"]["line"]["static_class"], "unreachable")
        self.assertEqual(before, flow_facts.document(result))
        self.assertFalse(any("observed" in row["id"] for row in result.metadata["inputs"]))
        self.assertEqual(reach.render(result, {}, self.inventory), study)

    def test_unknown_observation_and_duplicate_projection_are_rejected(self):
        result = flow_facts.collect("reach", {"strings": {"line": {"class": "unreachable"}}},
                                    self.inventory, {})
        with self.assertRaisesRegex(ValueError, "absent from inventory"):
            reach.render(result, {"reach": {"observed": [{"ids": ["unknown"], "at": 4,
                                                        "evidence": "Runtime display."}]}}, self.inventory)
        result.facts.append(result.facts[0])
        with self.assertRaisesRegex(ValueError, "invalid study fact"):
            flow_facts.project(result)


if __name__ == "__main__":
    unittest.main()
