from test_roots import TOOLKIT_ROOT
"""Profile schema check checks and fixtures."""

import sys
from pathlib import Path
import io
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.cli.main import main
from gen3tl.profile import check, schema
from project_fixture import CATALOG_STAGE
from profile_schema_support import HEADER, write_dir, write_fact_dir


class CheckTest(unittest.TestCase):
    def test_classifier_coverage_preserves_proof_and_retired_entry_counts(self):
        generated = HEADER + '''
[[strings.classified]]
name = "label"
action = "include"
offset = 32
classifier = "text-field"

[[strings.classified]]
name = "payload"
action = "exclude"
offset = 48
classifier = "bounded-payload"

[[fact]]
id = "strings.classified[name=label]"
tier = "shape"
source = "strings 1"
evidence = {method = "text-field", anchors = [16], sentence = "A text field targets the label."}

[[fact]]
id = "strings.classified[name=payload]"
tier = "crossref"
source = "strings 1"
evidence = {method = "bounded-payload", anchors = [40], sentence = "A binary reader bounds the payload."}
'''
        for overlay, reproduced in (('[strings]\ninclude = [32, 64]\nexclude = ["s0000030"]\n', 2),
                                     ('[strings]\ninclude = [64]\n', 0)):
            with tempfile.TemporaryDirectory() as tmp:
                result = check.run(write_dir(Path(tmp), generated=generated, overlay=overlay))
            self.assertEqual([(row.name, row.facts, row.covered) for row in result.sections],
                             [("strings.include", 2, 1), ("strings.exclude", 1, 1)])
            self.assertEqual(sum(row.outcome == "reproduced" for row in result.comparisons), reproduced)
            first, second = result.generated
            self.assertEqual((first.id, first.tier, first.evidence.anchors),
                             ("strings.include[0x20]", "shape", (16,)))
            self.assertEqual((second.id, second.tier, second.evidence.anchors),
                             ("strings.exclude[s0000030]", "crossref", (40,)))

    def test_tiers_count_each_source_layer_without_changing_coverage(self):
        result = check.run(write_dir(self.tmp))
        before = [(s.name, s.facts, s.covered) for s in result.sections]
        result.sources.family_facts = [
            schema.Fact("font[name=main]", "font", "font", {}, scope="family", tier="crossref"),
            schema.Fact("engine.family", "engine", "family", "example", scope="family", tier="shape"),
        ]
        self.assertEqual(check.tier_counts(result), {
            "family": {"shape": 1, "crossref": 1, "ingame": 0, "unstated": 0},
            "generated": {"shape": 2, "crossref": 4, "ingame": 0, "unstated": 0},
            "overlay": {"shape": 0, "crossref": 0, "ingame": 1, "unstated": 6},
        })
        report = check.text(result, "example", listing=False)
        self.assertIn("| family | 1 | 1 | 0 | 0 | 2 |", report)
        self.assertIn("| generated | 2 | 4 | 0 | 0 | 6 |", report)
        self.assertIn("| overlay | 0 | 0 | 1 | 6 | 7 |", report)
        self.assertEqual([(s.name, s.facts, s.covered) for s in result.sections], before)
        family_only = check.tier_counts(result, ["engine"])
        self.assertEqual(family_only["family"]["shape"], 1)
        self.assertEqual(sum(family_only["generated"].values()), 0)
        self.assertIn("| family | 1 | 0 | 0 | 0 | 1 |", check.text(result, "example", ["engine"]))
        exact = check.tier_counts(result, ["strings.include"])
        self.assertEqual(exact, check.tier_counts(result, ["strings"]))
        self.assertEqual(exact["generated"]["crossref"], 2)
        self.assertEqual(exact["overlay"]["unstated"], 2)
        self.assertTrue(all(not any(row.values()) for row in check.tier_counts(result, ["absent"]).values()))

    def test_tier_summary_is_printed_by_summary_command(self):
        path = write_dir(self.tmp)
        out = io.StringIO()
        with redirect_stdout(out), patch("sys.stderr", io.StringIO()):
            status = main(["--project-root", str(CATALOG_STAGE), "--profile", str(path), "discover", "check",
                           "--summary", "--section", "strings"])
        self.assertEqual(status, 1)
        self.assertIn("tier source  generated", out.getvalue())
        self.assertIn("crossref     2 facts", out.getvalue())
        self.assertIn("unstated     2 facts", out.getvalue())
        self.assertNotIn("status       manual", out.getvalue())

    def test_unknown_section_filters_fail_with_valid_names(self):
        path = write_dir(self.tmp)
        for selection in (["absent"], ["strings", "absent"]):
            output = io.StringIO()
            with self.subTest(selection=selection), redirect_stdout(output), patch("sys.stderr", output):
                status = main(["--project-root", str(CATALOG_STAGE), "--profile", str(path), "discover", "check",
                               *[token for name in selection for token in ("--section", name)]])
            self.assertEqual(status, 2)
            valid = next(row for row in output.getvalue().splitlines() if row.startswith("valid "))
            self.assertIn("strings", valid)
            self.assertIn("strings.include", valid)
            self.assertNotIn("0 facts", output.getvalue())
            self.assertEqual(output.getvalue().splitlines()[-1], "result       failed: unknown section: absent")

    def test_tiers_do_not_count_synthetic_granularity_conflicts(self):
        path = write_fact_dir(self.tmp, "tier-conflict", "[keyboard]\ncols = 10\nrows = 4\n",
                              '[keyboard]\ncols = 11\nrows = 4\nevidence = "Measured."\n', ())
        result = check.run(path)
        self.assertEqual(len(result.conflicts), 1)
        counts = check.tier_counts(result)
        self.assertEqual(sum(counts["generated"].values()), 2)
        self.assertEqual(sum(counts["overlay"].values()), 1)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_outcomes_and_coverage_per_section(self):
        result = check.run(write_dir(self.tmp))
        outcomes = {c.fact.id: (c.outcome, c.fields) for c in result.comparisons}
        self.assertEqual(outcomes, {
            "rom.name": ("manual", ()), "rom.size": ("reproduced", ()),
            "font[name=main]": ("contradicts", ("count",)), "font[name=small]": ("manual", ()),
            "keyboard": ("extends", ("rows",)),
            "strings.include[0x20]": ("reproduced", ()), "strings.include[0x30]": ("manual", ())})
        rows = {row.name: row for row in result.sections}
        rom = rows["rom"]
        self.assertEqual((rom.facts, rom.generated, rom.overlay, rom.covered), (3, 2, 2, 2))
        self.assertEqual((rows["font"].facts, rows["font"].covered), (2, 0))
        self.assertEqual((rows["keyboard"].facts, rows["keyboard"].covered), (1, 0))
        self.assertEqual((rows["strings.include"].facts, rows["strings.include"].covered), (3, 2))
        self.assertEqual([c.fact.id for c in result.contradictions], ["font[name=main]"])
        report = check.text(result, "game-1.2.3")
        self.assertIn("| rom | identity | 3 | 2 | 2 | 1 | 0 | 0 | 0 | 67% |", report)
        self.assertIn("## Granularity differences (0)", report)
        self.assertIn("- `font[name=main]`: count: generated 8, overlay 9", report)
        self.assertIn("2 overlay facts are reproduced by discovery and can leave the overlay.", report)
        self.assertIn("| manual | font | ingame | `font[name=small]` | pool at 0x64C4 |", report)
        self.assertNotIn("## Overlay facts", check.text(result, "game-1.2.3", listing=False))

    def test_one_table_against_generated_fields_is_a_granularity_difference(self):
        records = "".join(f'''[[fact]]
id = "keyboard.{name}"
tier = "shape"
source = "keyboard"
evidence = {{sentence = "Discovered {name}."}}
''' for name in ("cols", "rows"))
        path = write_dir(self.tmp, "one-to-many", HEADER + "[keyboard]\ncols = 10\nrows = 4\n" + records,
                         '[keyboard]\ncols = 10\nrows = 4\nevidence = "Measured layout."\n')
        result = check.run(path)
        self.assertEqual([(c.fact.id, c.outcome) for c in result.comparisons], [("keyboard", "granularity")])
        comparison = result.comparisons[0]
        self.assertIsNone(comparison.generated)
        self.assertEqual([f.id for f in comparison.related], ["keyboard.cols", "keyboard.rows"])
        self.assertEqual(result.contradictions, [])
        self.assertEqual(len(result.granularities), 1)
        relation = result.granularities[0]
        self.assertEqual([f.id for f in relation.generated], ["keyboard.cols", "keyboard.rows"])
        self.assertEqual([f.id for f in relation.overlay], ["keyboard"])
        row = result.sections[0]
        self.assertEqual((row.facts, row.generated, row.overlay, row.covered), (3, 2, 1, 0))
        self.assertEqual((row.counts["granularity"], row.counts["contradicts"]), (1, 0))
        report = check.text(result, "one-to-many")
        self.assertIn("| keyboard | keyboard | 3 | 2 | 1 | 0 | 0 | 1 | 0 | 0% |", report)
        self.assertIn("## Granularity differences (1)", report)
        self.assertIn("- generated `keyboard.cols`, `keyboard.rows`; overlay `keyboard`", report)
        self.assertIn("## Contradictions (0)", report)
        out = io.StringIO()
        with redirect_stdout(out), patch("sys.stderr", io.StringIO()):
            status = main(["--project-root", str(CATALOG_STAGE), "--profile", str(path), "discover", "check",
                           "--summary"])
        self.assertEqual(status, 0)
        self.assertIn("granularity  1 facts", out.getvalue())
        self.assertIn("fact         keyboard", out.getvalue())
        self.assertIn("status       granularity", out.getvalue())
        self.assertIn("generated    keyboard.cols, keyboard.rows", out.getvalue())
        self.assertIn("contradicts  0 facts", out.getvalue())

    def test_generated_table_against_overlay_fields_is_a_granularity_difference(self):
        generated = HEADER + '''[keyboard]
cols = 10
rows = 4
[[fact]]
id = "keyboard"
tier = "shape"
source = "keyboard"
evidence = {sentence = "Discovered layout."}
'''
        path = write_dir(self.tmp, "many-to-one", generated, "[keyboard]\ncols = 10\nrows = 4\n")
        result = check.run(path)
        self.assertEqual([(c.fact.id, c.outcome) for c in result.comparisons],
                         [("keyboard.cols", "granularity"), ("keyboard.rows", "granularity")])
        self.assertEqual(result.contradictions, [])
        self.assertEqual(len(result.granularities), 1)
        relation = result.granularities[0]
        self.assertEqual([f.id for f in relation.generated], ["keyboard"])
        self.assertEqual([f.id for f in relation.overlay], ["keyboard.cols", "keyboard.rows"])
        self.assertTrue(all([f.id for f in c.related] == ["keyboard"] for c in result.comparisons))
        row = result.sections[0]
        self.assertEqual((row.facts, row.generated, row.overlay, row.covered), (3, 1, 2, 0))
        self.assertEqual((row.counts["granularity"], row.counts["contradicts"]), (2, 0))
        report = check.text(result, "many-to-one")
        self.assertIn("| keyboard | keyboard | 3 | 1 | 2 | 0 | 0 | 2 | 0 | 0% |", report)
        self.assertIn("## Granularity differences (1)", report)
        self.assertIn("- generated `keyboard`; overlay `keyboard.cols`, `keyboard.rows`", report)
        self.assertIn("## Contradictions (0)", report)
        out = io.StringIO()
        with redirect_stdout(out), patch("sys.stderr", io.StringIO()):
            status = main(["--project-root", str(CATALOG_STAGE), "--profile", str(path), "discover", "check",
                           "--summary"])
        self.assertEqual(status, 0)
        self.assertEqual(out.getvalue().count("status       granularity"), 2)
        self.assertIn("fact         keyboard.cols", out.getvalue())
        self.assertIn("fact         keyboard.rows", out.getvalue())
        self.assertEqual(out.getvalue().count("generated    keyboard\n"), 2)

    def test_granularity_value_conflicts_are_separate_contradictions(self):
        body = "[keyboard]\ncols = 10\nrows = 4\n"
        cases = (
            ("split-generated", (), '[keyboard]\ncols = 11\nrows = 4\nevidence = "Measured."\n',
             ["keyboard.cols", "keyboard.rows"], ["keyboard"], 1),
            ("coarse-generated", ("keyboard",), "[keyboard]\ncols = 11\nrows = 4\n",
             ["keyboard"], ["keyboard.cols", "keyboard.rows"], 2),
        )
        for name, named, overlay_text, generated_ids, overlay_ids, granular_count in cases:
            with self.subTest(direction=name):
                path = write_fact_dir(self.tmp, name, body, overlay_text, named)
                result = check.run(path)
                self.assertEqual([(c.fact.id, c.outcome) for c in result.comparisons],
                                 list(zip(overlay_ids, ["granularity"] * granular_count)))
                self.assertEqual(len(result.granularities), 1)
                relation = result.granularities[0]
                self.assertEqual([f.id for f in relation.generated], generated_ids)
                self.assertEqual([f.id for f in relation.overlay], overlay_ids)
                self.assertEqual(len(result.conflicts), 1)
                conflict = result.conflicts[0]
                self.assertEqual((conflict.fact.id, conflict.generated.id, conflict.fields,
                                  conflict.generated.value, conflict.fact.value),
                                 ("keyboard.cols", "keyboard.cols", ("value",), 10, 11))
                self.assertEqual([c.fact.id for c in result.contradictions], ["keyboard.cols"])
                row = result.sections[0]
                self.assertEqual((row.counts["granularity"], row.counts["contradicts"], row.covered),
                                 (granular_count, 1, 0))
                report = check.text(result, name)
                generated = ", ".join(f"`{fact_id}`" for fact_id in generated_ids)
                overlay = ", ".join(f"`{fact_id}`" for fact_id in overlay_ids)
                self.assertIn(f"- generated {generated}; overlay {overlay}", report)
                self.assertIn("## Contradictions (1)", report)
                self.assertIn("- `keyboard.cols`: value: generated 10, overlay 11", report)
                out = io.StringIO()
                with redirect_stdout(out), patch("sys.stderr", io.StringIO()):
                    status = main(["--project-root", str(CATALOG_STAGE), "--profile", str(path), "discover", "check",
                                   "--summary"])
                self.assertEqual(status, 1)
                self.assertEqual(out.getvalue().count("status       granularity"), granular_count)
                self.assertIn("fact         keyboard.cols", out.getvalue())
                self.assertIn("status       contradicts", out.getvalue())
                self.assertIn("field        value", out.getvalue())
                self.assertIn("generated    10\n", out.getvalue())
                self.assertIn("overlay      11\n", out.getvalue())

    def test_granularity_conflicts_keep_nested_and_quoted_canonical_ids(self):
        nested = "[keyboard.layout]\ncols = 10\nrows = 4\n"
        quoted = '[keyboard]\n"a.b" = 10\nrows = 4\n'
        cases = (
            ("nested-split", nested, (), "[keyboard.layout]\ncols = 11\nrows = 4\nevidence = 'Measured.'\n",
             "keyboard.layout.cols"),
            ("nested-coarse", nested, ("keyboard.layout",), "[keyboard.layout]\ncols = 11\nrows = 4\n",
             "keyboard.layout.cols"),
            ("quoted-split", quoted, (), '[keyboard]\n"a.b" = 11\nrows = 4\nevidence = "Measured."\n',
             'keyboard."a.b"'),
            ("quoted-coarse", quoted, ("keyboard",), '[keyboard]\n"a.b" = 11\nrows = 4\n', 'keyboard."a.b"'),
        )
        for name, body, named, overlay, conflict_id in cases:
            with self.subTest(case=name):
                result = check.run(write_fact_dir(self.tmp, name, body, overlay, named))
                self.assertEqual(len(result.granularities), 1)
                self.assertEqual(len(result.conflicts), 1)
                conflict = result.conflicts[0]
                self.assertEqual((conflict.fact.id, conflict.generated.id, conflict.fields,
                                  conflict.generated.value, conflict.fact.value),
                                 (conflict_id, conflict_id, ("value",), 10, 11))
                self.assertIn(f"- `{conflict_id}`: value: generated 10, overlay 11", check.text(result, name))

    def test_granularity_ignores_missing_fields_and_unrelated_siblings(self):
        cases = (
            ("missing-split", "[keyboard]\ncols = 10\nrows = 4\n", (),
             '[keyboard]\ncols = 10\nrows = 4\nextra = 99\nevidence = "Measured."\n'
             '[keyboard_other]\ncols = 11\nrows = 5\nevidence = "Other."\n'),
            ("missing-coarse", "[keyboard]\ncols = 10\nrows = 4\nextra = 99\n", ("keyboard",),
             "[keyboard]\ncols = 10\nrows = 4\n[keyboard_other]\ncols = 11\nrows = 5\n"),
        )
        for name, body, named, overlay in cases:
            with self.subTest(direction=name):
                path = write_fact_dir(self.tmp, name, body, overlay, named)
                result = check.run(path)
                self.assertEqual(len(result.granularities), 1)
                self.assertEqual(result.conflicts, [])
                self.assertEqual(result.contradictions, [])
                self.assertTrue(all(0 <= row.covered <= row.facts for row in result.sections))
                unrelated = [c for c in result.comparisons if c.fact.section == "keyboard_other"]
                self.assertTrue(unrelated)
                self.assertTrue(all(c.outcome == "manual" for c in unrelated))
                out = io.StringIO()
                with redirect_stdout(out), patch("sys.stderr", io.StringIO()):
                    status = main(["--project-root", str(CATALOG_STAGE), "--profile", str(path), "discover", "check",
                                   "--summary"])
                self.assertEqual(status, 0)
                self.assertNotIn("status       contradicts", out.getvalue())

    def test_granularity_and_exact_contradictions_are_independent(self):
        body = "[keyboard]\ncols = 10\nrows = 4\n[rom]\nsize = 16\n"
        cases = (
            ("mixed-split", (), '[keyboard]\ncols = 11\nrows = 4\nevidence = "Measured."\n[rom]\nsize = 17\n'),
            ("mixed-coarse", ("keyboard",), "[keyboard]\ncols = 11\nrows = 4\n[rom]\nsize = 17\n"),
        )
        for name, named, overlay in cases:
            with self.subTest(direction=name):
                path = write_fact_dir(self.tmp, name, body, overlay, named)
                result = check.run(path)
                self.assertEqual(len(result.granularities), 1)
                exact = next(c for c in result.comparisons if c.fact.id == "rom.size")
                self.assertEqual((exact.outcome, exact.fields, exact.generated.value, exact.fact.value),
                                 ("contradicts", ("value",), 16, 17))
                self.assertEqual([c.fact.id for c in result.contradictions], ["rom.size", "keyboard.cols"])
                report = check.text(result, name)
                self.assertIn("## Contradictions (2)", report)
                self.assertIn("- `rom.size`: value: generated 16, overlay 17", report)
                self.assertIn("- `keyboard.cols`: value: generated 10, overlay 11", report)
                out = io.StringIO()
                with redirect_stdout(out), patch("sys.stderr", io.StringIO()):
                    status = main(["--project-root", str(CATALOG_STAGE), "--profile", str(path), "discover", "check",
                                   "--summary"])
                self.assertEqual(status, 1)

    def test_exact_contradictions_and_unrelated_siblings_are_not_granularity(self):
        generated = HEADER + '''[keyboard]
cols = 10
rows = 4
[[fact]]
id = "keyboard"
tier = "shape"
source = "keyboard"
evidence = {sentence = "Discovered layout."}
'''
        overlay = '''[keyboard]
cols = 11
rows = 4
evidence = "Measured layout."
[keyboard_extra]
cols = 10
rows = 4
evidence = "A separate layout."
'''
        result = check.run(write_dir(self.tmp, "contradiction", generated, overlay))
        outcomes = {c.fact.id: (c.outcome, c.fields) for c in result.comparisons}
        self.assertEqual(outcomes, {"keyboard": ("contradicts", ("cols",)), "keyboard_extra": ("manual", ())})
        self.assertEqual(result.granularities, [])
        self.assertEqual([c.fact.id for c in result.contradictions], ["keyboard"])
        report = check.text(result, "contradiction")
        self.assertIn("## Granularity differences (0)", report)
        self.assertIn("## Contradictions (1)", report)
        self.assertIn("- `keyboard`: cols: generated 10, overlay 11", report)

    def test_values_compare_by_type(self):
        self.assertEqual(check.compare(1, True), ("contradicts", ("value",)))
        self.assertEqual(check.compare({"a": 1, "evidence": "x"}, {"a": 1, "evidence": "y"}), ("reproduced", ()))

    def test_command_fails_on_a_contradiction(self):
        path = write_dir(self.tmp)
        out = io.StringIO()
        with redirect_stdout(out), patch("sys.stderr", io.StringIO()):
            self.assertEqual(main(["--project-root", str(CATALOG_STAGE), "--profile", str(path), "discover", "check",
                                   "--summary"]), 1)
        self.assertIn("contradicts  1 facts", out.getvalue())
        self.assertNotIn("status       manual", out.getvalue())

    def test_flat_profile_is_all_manual(self):
        path = self.tmp / "flat.toml"
        path.write_text('[rom]\nsha1 = "ab"\n[[table]]\nname = "t"\nevidence = "e"\n')
        result = check.run(path)
        self.assertEqual([(c.fact.id, c.outcome) for c in result.comparisons],
                         [("rom.sha1", "manual"), ("table[name=t]", "manual")])
        self.assertIn("Flat profile: no generated profile", check.text(result, "flat"))

    def test_report_counts_kinds_and_names_unresolved_items(self):
        item = schema.Fact("table[name=moves]", "table", "table", {}, status="unresolved",
                           evidence=schema.Evidence("no accessor found"))
        text = check.report("game", [], [], [item])
        self.assertIn("| table | 0 | 0 | 0 | 1 |", text)
        self.assertIn("| `table[name=moves]` | table | no accessor found |", text)
