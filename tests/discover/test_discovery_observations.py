"""Recorded runtime evidence is bound to ROM, facts, scenarios and samples."""

import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch
from unittest.mock import Mock

from gen3tl.core.charmap import Charmap
from gen3tl.core.rom import Rom
from gen3tl.discover import observations
from gen3tl.discover.foundations import Result, evidence, fact


class ObservationsTest(unittest.TestCase):
    def test_scenario_observer_receives_live_game_and_closes_on_failure(self):
        from gen3tl.verify.headless import run_scenario
        game = Mock()
        observer = Mock()
        with patch("gen3tl.verify.headless.Gba", return_value=game):
            run_scenario(Path("fixture.gba"), "shot names", Path("shots"), "fixture", observer=observer)
        observer.assert_called_once_with(game, "names")
        game.close.assert_called_once()
        game.reset_mock()
        observer.side_effect = ValueError("bad sample")
        with patch("gen3tl.verify.headless.Gba", return_value=game), self.assertRaises(ValueError):
            run_scenario(Path("fixture.gba"), "shot names", Path("shots"), "fixture", observer=observer)
        game.close.assert_called_once()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scenario = self.root / "sample.txt"
        self.scenario.write_text("wait 1\nshot names\n")
        self.rom = Rom(Charmap().encode("Name").ljust(64, b"\xff"))
        value = {"name": "names", "base": 0, "stride": 8, "count": 1,
                 "field_offset": 0, "field_length": 8}
        self.fact = fact("table[name=names]", "table", value, evidence("Indexed name field.", "fixture"))
        self.sample = {"fact": self.fact.id, "scenario": str(self.scenario), "shot": "names",
                       "row": 0, "ram": 0x02000000}

    def produce(self, sample=None, fact_=None, wrong=False):
        sample, proof = sample or self.sample, fact_ or self.fact
        rom = self.rom

        class Game:
            def ewram(self):
                return (b"wrong" if wrong else rom.read(0, 5)) + b"\0" * 100

        def run(*args, observer, **kwargs):
            observer(Game(), "names")

        with patch("gen3tl.verify.headless.run_scenario", side_effect=run):
            return observations.produce(self.rom, Path("fixture.gba"), [proof],
                {"schema": "1.0.0", "samples": [sample]}, self.root / "observations.json")

    def test_actual_read_upgrades_only_the_sampled_fact(self):
        document = self.produce()
        other = fact("rom.size", "identity", 64, evidence("Input size.", "file-length"), "shape")
        result = Result({}, [self.fact, other], {})
        observations.verify(self.rom, result, document)
        self.assertEqual([item.tier for item in result.facts], ["ingame", "shape"])
        self.assertEqual(dict(result.facts[0].evidence.counts)["ingame_samples"], 1)
        self.assertEqual(json.loads((self.root / "observations.json").read_text()), document)
        row = document["observations"][0]
        self.assertEqual(document["schema"], "2.0.0")
        self.assertEqual(row["predicted"], observations.digest(self.rom.read(0, 5)))
        self.assertEqual(set(row["observed"]), {"size", "sha256"})
        self.assertIn("discriminating | ingame | none", "\n".join(observations.report_lines(
            result.metadata["observations"]["samples"])))

    def test_mismatched_observation_is_saved_but_never_promoted(self):
        document = self.produce(wrong=True)
        self.assertNotEqual(document["observations"][0]["predicted"], document["observations"][0]["observed"])
        with self.assertRaisesRegex(ValueError, "sample differs"):
            observations.verify(self.rom, Result({}, [self.fact], {}), document)

    def test_stale_rom_fact_scenario_prediction_and_read_are_refused(self):
        document = self.produce()
        for field, value in (("value_sha256", "0" * 64), ("predicted", "00"),
                             ("observed", "00"), ("read", "framebuffer")):
            bad = copy.deepcopy(document)
            bad["observations"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                observations.verify(self.rom, Result({}, [self.fact], {}), bad)
        bad = copy.deepcopy(document)
        bad["rom_sha1"] = "0" * 40
        with self.assertRaises(ValueError):
            observations.verify(self.rom, Result({}, [self.fact], {}), bad)
        self.scenario.write_text("wait 2\nshot names\n")
        with self.assertRaisesRegex(ValueError, "stale scenario"):
            observations.verify(self.rom, Result({}, [self.fact], {}), document)

    def test_missing_and_duplicate_samples_are_refused(self):
        document = self.produce()
        document["observations"] *= 2
        with self.assertRaisesRegex(ValueError, "duplicate"):
            observations.verify(self.rom, Result({}, [self.fact], {}), document)
        document["observations"] = []
        with self.assertRaises(ValueError):
            observations.verify(self.rom, Result({}, [self.fact], {}), document)

    def test_each_digest_and_size_is_checked_independently(self):
        document = self.produce()
        for domain in ("predicted", "observed"):
            for key, value in (("sha256", "0" * 64), ("size", 4), ("size", True)):
                changed = copy.deepcopy(document)
                changed["observations"][0][domain][key] = value
                with self.subTest(domain=domain, key=key), self.assertRaisesRegex(ValueError, "sample differs"):
                    observations.verify(self.rom, Result({}, [self.fact], {}), changed)

    def test_shared_prediction_names_unsampled_partner_and_promotes_neither(self):
        document = self.produce()
        partner = fact("table[name=other]", "table", {**self.fact.value, "name": "other"}, self.fact.evidence)
        result = Result({}, [self.fact, partner], {})
        reports = observations.verify(self.rom, result, document)
        self.assertEqual([item.tier for item in result.facts], ["crossref", "crossref"])
        self.assertEqual(reports[0]["shared"], [partner.id])
        self.assertEqual(reports[0]["status"], "shared")
        self.assertIn(partner.id, "\n".join(observations.report_lines(reports)))
        self.assertEqual(reports[0]["size"], 5)

    def test_comparison_uses_same_row_and_separate_sections(self):
        document = self.produce()
        self.rom.write(8, Charmap().encode("Else"))
        document["rom_sha1"] = self.rom.sha1()
        partner = fact("table[name=other]", "table", {**self.fact.value, "base": 8}, self.fact.evidence)
        keys = fact("keyboard.table[name=keys]", "table", {"base": 0, "entry": 8}, self.fact.evidence)
        result = Result({}, [self.fact, partner, keys], {})
        reports = observations.verify(self.rom, result, document)
        self.assertEqual(reports[0]["status"], "discriminating")
        self.assertEqual([item.tier for item in result.facts], ["ingame", "crossref", "crossref"])

    def test_legacy_conversion_checks_rom_prediction_and_scenario_inputs(self):
        fixture = self.root / "fixture.sav"
        fixture.write_bytes(b"synthetic save")
        self.scenario.write_text(f"savefile {fixture}\nshot names\n")
        document = self.produce()
        legacy = copy.deepcopy(document)
        legacy["schema"] = "1.0.0"
        row = legacy["observations"][0]
        row["predicted"] = row["observed"] = self.rom.read(0, 5).hex()
        steps, _ = observations.scenario_sources(self.scenario)
        files = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in (self.scenario, fixture)}
        row["scenario"] = {"path": str(self.scenario),
                           "sha256": hashlib.sha256(json.dumps([steps, files], sort_keys=True).encode()).hexdigest()}
        with self.assertRaisesRegex(ValueError, "discover observe --convert"):
            observations.verify(self.rom, Result({}, [self.fact], {}), legacy)
        self.assertEqual(observations.convert(self.rom, [self.fact], legacy), document)
        changed = copy.deepcopy(document)
        changed["observations"][0]["scenario"]["inputs"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "stale scenario"):
            observations.verify(self.rom, Result({}, [self.fact], {}), changed)
        for key in ("predicted", "observed"):
            bad = copy.deepcopy(legacy)
            bad["observations"][0][key] = "00"
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "sample differs"):
                observations.convert(self.rom, [self.fact], bad)
        fixture.write_bytes(b"changed save")
        with self.assertRaisesRegex(ValueError, "stale legacy"):
            observations.convert(self.rom, [self.fact], legacy)
        fixture.unlink()
        with patch("gen3tl.verify.headless.run_scenario", side_effect=AssertionError("emulator called")):
            observations.verify(self.rom, Result({}, [self.fact], {}), document)

    def test_default_record_requires_matching_tracked_plan(self):
        document = self.produce()
        directory = self.root / "observations"
        directory.mkdir()
        (directory / "observations.json").write_text(json.dumps(document))
        plan = {"schema": "1.0.0", "samples": [self.sample]}
        (directory / "samples.json").write_text(json.dumps(plan))
        result = Result({}, [self.fact], {})
        observations.apply(self.rom, result, self.root)
        self.assertEqual(result.facts[0].tier, "ingame")
        plan["samples"][0]["row"] = 1
        (directory / "samples.json").write_text(json.dumps(plan))
        with self.assertRaisesRegex(ValueError, "sample plan differs"):
            observations.apply(self.rom, Result({}, [self.fact], {}), self.root)

    def test_sample_command_reads_bytes_from_rom(self):
        from gen3tl.cli.rom import evidence
        plan = self.root / "samples.json"
        plan.write_text(json.dumps({"schema": "1.0.0", "samples": [self.sample]}))
        args = SimpleNamespace(profile="fixture", rom=None, fact=self.fact.id, sample=0, samples=str(plan))
        profile = Mock(path=self.root)
        profile.load_rom.return_value = self.rom
        with patch("gen3tl.cli.rom.Profile.by_name", return_value=profile), \
                patch("gen3tl.profile.schema.read"), \
                patch("gen3tl.profile.schema.generated_facts", return_value=[self.fact]), \
                redirect_stdout(io.StringIO()) as out:
            self.assertEqual(evidence(args), 0)
        self.assertIn("bytes        " + self.rom.read(0, 5).hex(), out.getvalue())

    def test_keyboard_bounds_and_framebuffer_prediction(self):
        keys = fact("keyboard.table[name=keys]", "table", {"base": 0, "entry": 8},
                    evidence("Indexed keyboard bytes.", "fixture"))
        sample = {"offset": 0, "length": 4}
        self.assertEqual(observations.prediction(self.rom, keys, sample), self.rom.read(0, 4))
        with self.assertRaisesRegex(ValueError, "leaves keyboard"):
            observations.prediction(self.rom, keys, {"offset": 7, "length": 2})
        font = fact("font[name=main]", "font", {"name": "main", "glyphs": 0, "widths": 32,
                    "count": 1, "codes": [0]}, evidence("Glyph bytes.", "fixture"))
        sample = {"glyph": 0, "x": 0, "y": 0, "palette": [[0, 0, 0], [255, 255, 255],
                                                                 [128, 128, 128], [0, 0, 0]]}
        self.rom.write(0, b"\x55" * 64)
        predicted = observations.prediction(self.rom, font, sample)
        self.assertEqual(predicted, b"\xff" * (16 * 16 * 3))
        sample["glyph"] = 1
        with self.assertRaisesRegex(ValueError, "outside font"):
            observations.prediction(self.rom, font, sample)

    def test_font_sharing_reports_pixels_and_compares_all_fonts(self):
        self.rom.write(0, b"\x55" * 64)
        value = {"name": "main", "glyphs": 0, "widths": 32, "count": 1, "codes": [0]}
        font = fact("font[name=main]", "font", value, self.fact.evidence)
        other = fact("font[name=other]", "font", {**value, "name": "other"}, self.fact.evidence)
        sample = {"fact": font.id, "scenario": str(self.scenario), "shot": "names", "glyph": 0,
                  "x": 0, "y": 0, "palette": [[0, 0, 0], [255, 255, 255], [128, 128, 128], [0, 0, 0]]}
        game = Mock()
        game.pixels.return_value = [[[255, 255, 255]] * 240] * 160
        with patch("gen3tl.verify.headless.run_scenario",
                   side_effect=lambda *args, observer, **kwargs: observer(game, "names")):
            document = observations.produce(self.rom, Path("fixture.gba"), [font, other],
                {"schema": "1.0.0", "samples": [sample]}, self.root / "observations.json")
        result = Result({}, [font, other], {})
        report, = observations.verify(self.rom, result, document)
        self.assertEqual((report["foreground_pixels"], report["size"], report["shared"]), (256, 768, [other.id]))
        self.assertEqual([item.tier for item in result.facts], ["crossref", "crossref"])

    def test_keyboard_sharing_includes_unsampled_tables(self):
        keys = fact("keyboard.table[name=keys]", "table", {"base": 0, "entry": 8}, self.fact.evidence)
        other = fact("keyboard.table[name=other]", "table", {"base": 0, "entry": 1}, self.fact.evidence)
        sample = {"fact": keys.id, "scenario": str(self.scenario), "shot": "names",
                  "offset": 0, "length": 1, "ram": 0x02000000}
        document = self.produce(sample, keys)
        result = Result({}, [keys, other], {})
        report, = observations.verify(self.rom, result, document)
        self.assertEqual(report["shared"], [other.id])
        self.assertEqual([item.tier for item in result.facts], ["crossref", "crossref"])
