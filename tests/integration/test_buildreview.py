import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from gen3tl.cli.build import build
from gen3tl.catalog.catalog import load_catalog
from gen3tl.catalog.batch import approve_batch, import_batch
from gen3tl.catalog.po import PoEntry, write
from gen3tl.core.rom import Rom
from gen3tl.lang.langpack import LangPack
from gen3tl.profile.profile import Profile
from gen3tl.web.editorimport import import_export, read_export
from gen3tl.web.editorrules import catalog_preview, rules_data
from test_languagerules_check import CM, original_values, plural_profile


class BuildReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.po = self.root / "po/maps/icicle-cave.po"
        self.sign = PoEntry("s1F72BD8", "Trainer Tips!", "Советы тренерам!", flags=["fuzzy"])
        write(self.po, [self.sign], {})
        self.args = argparse.Namespace(profile="game", po_root=self.root / "po", rom=None,
                                       source_as_translation=False,
                                       pseudo=None, stress_growth=None, out=self.root / "game.gba")

    def test_translated_entries_reach_rom_loading_in_every_mode(self):
        entries = [self.sign, PoEntry("off", "Suffix", flags=["empty", "fuzzy"]),
                   PoEntry("draft", "Untranslated", flags=["fuzzy"])]
        write(self.po, entries, {})
        for name, value in ((None, None), ("source_as_translation", True), ("pseudo", 0), ("stress_growth", .25)):
            with self.subTest(mode=name):
                args = argparse.Namespace(**vars(self.args))
                if name:
                    setattr(args, name, value)
                profile = Mock()
                profile.load_rom.side_effect = FileNotFoundError("test ROM boundary")
                with patch("gen3tl.cli.build.Profile.by_name", return_value=profile), \
                        patch("gen3tl.build.textprefix.original_limits", return_value=profile):
                    with self.assertRaisesRegex(FileNotFoundError, "test ROM boundary"):
                        build(args)
                profile.load_rom.assert_called_once_with(None)

    def test_the_build_has_no_option_for_translated_entries(self):
        from gen3tl.cli.main import parser
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser()[0].parse_args(["build", "rom", "--include-fuzzy"])


class BuildRulesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        data = plural_profile()
        data["font_style"] = {"style": "fixture", "cell_height": 14}
        data["font"] = [{"name": "main", "glyphs": 0x1000, "widths": 0xA000, "count": 512}]
        base = Rom(bytes(0x10000))
        base.write(0xA000, bytes([6]) * 512)
        entries, originals, self.entries = [], [], {}
        for table in data["table"]:
            table["pad"] = 255
            key = f"table:{table['name']}/0"
            value = original_values()[table["name"].rsplit("-", 1)[0]]
            base.write(table["base"], value)
            text = CM.decode(value + b"\xff")[0]
            originals.append(PoEntry(key, text))
            self.entries[key] = PoEntry(key, key, text)
            entries.append(dict(id=key, offset=table["base"], length=1, kind="fixed", category="fixed",
                                mode="field", text=text, table=table["name"], index=0,
                                flags=["unterminated"], refs=[]))
        base.write(0x200, CM.encode("A"))
        data["table"].append(dict(name="label", base=0x200, count=1, stride=2, field_length=2))
        key = "table:label/0"
        originals.append(PoEntry(key, CM.decode(base.read(0x200, 2))[0]))
        self.entries[key] = PoEntry(key, key, "A")
        entries.append(dict(id=key, offset=0x200, length=2, kind="fixed", category="fixed",
                            mode="field", text="A", table="label", index=0, flags=[], refs=[]))
        self.base = self.root / "base.gba"
        base.save(self.base)
        self.original = bytes(base.data)
        data["rom"] = {"sha1": base.sha1()}
        self.profile = Profile(data, self.root / "fixture.toml")
        (self.root / "inventory.json").write_text(json.dumps({"entries": entries}))
        self.source_text = self.root / "catalog.pot"
        write(self.source_text, originals, {})
        self.args = argparse.Namespace(
            profile="fixture", lang="xx", po_root=self.root / "po", rom=self.base,
            project=argparse.Namespace(build=self.root, snapshot=self.root / "snapshot.json"),
            source_as_translation=False, pseudo=None, stress_growth=None,
            no_font=True, no_keyboard=True, no_graphics=True, ignore_width=False, reuse_slots=False,
            out=self.root / "game.gba", default_build=self.root / "default.gba")

    def run_build(self):
        write(self.args.po_root / "operands.po", list(self.entries.values()), {"X-Source": "key-only"})
        output = io.StringIO()
        with patch("gen3tl.cli.build.Profile.by_name", return_value=self.profile), \
                patch("gen3tl.lang.langpack.LangPack.load", return_value=LangPack("xx")), \
                patch("gen3tl.catalog.extract.path", return_value=self.source_text), \
                patch("gen3tl.build.release.load", return_value="1.0.0"), \
                patch("gen3tl.cli.build._context_records", return_value=None), \
                redirect_stdout(output), redirect_stderr(output):
            result = build(self.args)
        return result, output.getvalue()

    def reject_operand(self):
        for routine in ("normal", "trade"):
            self.entries[f"table:ordinary-{routine}/0"].msgstr = "a"

    def test_rejected_operands_refuse_build_before_any_rom_write(self):
        self.reject_operand()
        with patch.object(Rom, "write", side_effect=AssertionError("unexpected ROM write")):
            result, output = self.run_build()
        self.assertEqual(result, 1, output)
        for routine in ("normal", "trade"):
            self.assertIn(f"table:ordinary-{routine}/0", output)
        self.assertIn("unsupported arithmetic operand", output)
        self.assertNotIn("verified     passed", output)
        for suffix in (".gba", ".project.json", ".report.md"):
            self.assertFalse(self.args.out.with_suffix(suffix).exists())
        self.assertEqual(self.base.read_bytes(), self.original)

    def test_rejected_operands_preserve_existing_outputs(self):
        self.reject_operand()
        paths = [self.args.out.with_suffix(suffix) for suffix in (".gba", ".project.json", ".report.md")]
        for path in paths:
            path.write_bytes(b"previous build")
        result, output = self.run_build()
        self.assertEqual(result, 1, output)
        for path in paths:
            self.assertEqual(path.read_bytes(), b"previous build")

    def test_original_and_off_operands_build(self):
        for off in (False, True):
            with self.subTest(off=off):
                if off:
                    for routine in ("normal", "trade"):
                        entry = self.entries[f"table:ordinary-{routine}/0"]
                        entry.msgstr, entry.flags = "", ["empty"]
                result, output = self.run_build()
                self.assertEqual(result, 0, output)
                expected = bytearray(self.original)
                if off:
                    expected[64] = expected[128] = 255
                self.assertEqual(self.args.out.read_bytes(), bytes(expected))

    def test_source_mode_validates_original_operands(self):
        self.reject_operand()
        self.args.source_as_translation = True
        result, output = self.run_build()
        self.assertEqual(result, 0, output)
        self.assertEqual(self.args.out.read_bytes(), self.original)

    def test_catalog_without_operands_builds(self):
        key = "table:label/0"
        self.entries = {key: self.entries[key]}
        result, output = self.run_build()
        self.assertEqual(result, 0, output)
        self.assertEqual(self.args.out.read_bytes(), self.original)

    def test_translated_and_approved_entries_are_built_and_counted(self):
        label = self.entries["table:label/0"]
        label.msgstr, label.flags = "B", ["fuzzy"]
        result, output = self.run_build()
        self.assertEqual(result, 0, output)
        expected = bytearray(self.original)
        expected[0x200:0x202] = CM.encode("B")
        self.assertEqual(self.args.out.read_bytes(), bytes(expected))
        approved = len(self.entries) - 1
        report = self.args.out.with_suffix(".report.md").read_text(encoding="utf-8")
        for row in (f"| catalog: approved | {approved} |", "| catalog: translated | 1 |",
                    f"| catalog: included | {approved + 1} |"):
            self.assertIn(row, report)
        self.assertNotIn("fuzzy", report)
        for lines in (f"catalog      approved\ncount        {approved} entries\n",
                      "catalog      translated\ncount        1 entries\n",
                      f"catalog      included\ncount        {approved + 1} entries\n"):
            self.assertIn(lines, output)

    def test_source_mode_counts_no_catalog_state(self):
        self.entries["table:label/0"].flags = ["fuzzy"]
        self.args.source_as_translation = True
        result, output = self.run_build()
        self.assertEqual(result, 0, output)
        self.assertIn("catalog      included\n", output)
        for state in ("approved", "translated"):
            self.assertNotIn(f"catalog      {state}\n", output)

    def test_grouped_preview_survives_import_review_and_rom_build(self):
        self.profile.data["language_rules"][0]["examples"] = ["table:label/0"]
        rule = self.profile.language_rules[0]
        presets = lambda choice: {operand["id"]: {"choice": choice} for operand in rule["operands"]}
        scenarios = [(presets("off"), "Berry"), (presets("original"), "Berries"),
                     ({"lower": {"choice": "custom", "text": "u"}}, "Berrues"),
                     (presets("original"), "Berries")]
        for index, (selections, expected_name) in enumerate(scenarios):
            with self.subTest(selections=selections):
                rows = [{"id": key, "translation": entry.msgstr, "flags": entry.flags,
                         "source_text": CM.decode(self.original[table["base"]:table["base"] + 1] + b"\xff")[0]}
                        for table in self.profile.get("table", [])
                        for key in [f"table:{table['name']}/0"] for entry in [self.entries[key]]]
                model = rules_data(self.profile, rows)["rules"][0]
                report = catalog_preview(model, rows, [], {"name": "Berry", "operands": selections}, CM)
                self.assertTrue(report["valid"], report["proposed"])
                self.assertEqual([row["text"] for row in report["proposed"]["results"]], [expected_name] * 2)
                self.assertEqual({row["id"] for row in report["changes"]}, {
                    binding["entry"] for operand in rule["operands"] if operand["id"] in selections
                    for binding in operand["bindings"]})
                write(self.args.po_root / "operands.po", list(self.entries.values()), {"X-Source": "key-only"})
                changes = [change for change in report["changes"]
                           if (change["translation"], change["state"]) !=
                           (self.entries[change["id"]].msgstr, self.entries[change["id"]].state)]
                export = self.root / "edits.json"
                export.write_text(json.dumps({"profile": self.profile.name, "language": self.args.lang,
                                              "rom_sha1": self.profile.sha1, "entries": [
                    {**change, "base": self.entries[change["id"]].msgstr,
                     "base_state": self.entries[change["id"]].state} for change in changes]}))
                batches = self.root / f"review-{index}"
                plan = self.root / "plan.json"
                plan.write_text(json.dumps([{"name": "operands", "kind": "translate",
                                             "ids": [row["id"] for row in changes]}]))
                with patch("gen3tl.catalog.extract.path", return_value=self.source_text):
                    catalog = load_catalog(self.args.po_root)
                    data = read_export(export, self.profile.name, self.args.lang, self.profile.sha1, catalog)
                    before = (self.args.po_root / "operands.po").read_bytes()
                    result = import_export(data, self.args.po_root, batches, plan, {})
                    self.assertEqual((self.args.po_root / "operands.po").read_bytes(), before)
                    for batch in result["batches"]:
                        import_batch(batch["name"], self.args.po_root, batches)
                    self.entries = {key: entry for key, (_, entry) in load_catalog(self.args.po_root).items()}
                    if any(entry.fuzzy and entry.state != "original" for entry in self.entries.values()):
                        status, output = self.run_build()
                        self.assertEqual(status, 0, output)
                        self.assertIn("catalog      translated\n", output)
                    for batch in result["batches"]:
                        for review in ("accuracy", "proofreading", "editorial"):
                            (batches / batch["name"] / f"review-{review}-1.json").write_text("[]")
                        approve_batch(batch["name"], self.args.po_root, batches)
                    self.entries = {key: entry for key, (_, entry) in load_catalog(self.args.po_root).items()}
                status, output = self.run_build()
                self.assertEqual(status, 0, output)
                expected = bytearray(self.original)
                for field in report["proposed"]["fields"]:
                    self.assertFalse(self.entries[field["entry"]].fuzzy)
                    binding = next(binding for operand in model["operands"] for binding in operand["bindings"]
                                   if binding["entry"] == field["entry"])
                    expected[binding["offset"]:binding["offset"] + binding["byte_budget"]] = bytes(field["bytes"])
                self.assertEqual(self.args.out.read_bytes(), bytes(expected))
                self.assertEqual(self.base.read_bytes(), self.original)


if __name__ == "__main__":
    unittest.main()
