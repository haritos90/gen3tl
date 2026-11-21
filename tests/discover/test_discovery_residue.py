"""Named residue preserves unresolved facts and rejects new gaps."""

import unittest
from types import SimpleNamespace

from gen3tl.discover.foundations import evidence, fact
from gen3tl.profile import check, residue


class ResidueTest(unittest.TestCase):
    def test_only_exact_unresolved_reason_is_acknowledged(self):
        issue = fact("script.command[1]", "script-command", None,
                     evidence("Opaque helper target.", "walk"), status="unresolved")
        declaration = {"unresolved": [{"id": issue.id, "reason": issue.evidence.sentence}]}
        self.assertEqual(residue.unresolved([issue], declaration), ([issue], []))
        self.assertEqual(issue.status, "unresolved")
        self.assertIsNone(issue.tier)
        declaration["unresolved"][0]["reason"] = "Different failure."
        self.assertEqual(residue.unresolved([issue], declaration), ([], [issue]))
        self.assertEqual(residue.unresolved([issue], {}), ([], [issue]))

    def test_shortfall_must_name_each_fact_in_its_section(self):
        item = fact("table[name=missing]", "table", {}, evidence("Manual rows.", "manual"))
        item.section = "table"
        result = SimpleNamespace(sections=[check.Section("table", "table", facts=1)],
                                 comparisons=[check.Comparison(item, None, "manual")])
        declared = {"shortfall": [{"section": "table", "facts": [item.id]}]}
        self.assertEqual(residue.missing_coverage(result, declared), [])
        self.assertEqual(residue.missing_coverage(result, {}), [item.id])
        declared["shortfall"][0]["section"] = "font"
        self.assertEqual(residue.missing_coverage(result, declared), [item.id])
