import io
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gen3tl.catalog.po import PoEntry, write
from gen3tl.verify.headless import HeadlessError, hatch_checks, parse_scenario
from gen3tl.verify.savefixture import catalog_species
from project_fixture import select_language
from gen3tl.cli.verify import shots
from gen3tl.core.terminal import run_status

def setUpModule():
    select_language("ru")

HATCH = """# an egg hatched, the game saved
statefile load e6
giveegg 175             # Togepi
eggcycles 0
stepcounter 254
press DOWN 16 4
repeat 3 press A 4 120
writesave hatched
hatched hatched 1 175
"""

class ParserTest(unittest.TestCase):
    def test_save_options_accept_only_neutral_names(self):
        from gen3tl.cli.main import parser
        command, _ = parser()
        options = ["verify", "ui-shots", "--base-save", "base.sav", "--translated-save", "translated.sav"]
        args = command.parse_args(options)
        self.assertEqual((args.base_save, args.translated_save), ("base.sav", "translated.sav"))
        for old in ("--en-save", "--ru-save"):
            with self.subTest(option=old), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                command.parse_args([*options, old, "other.sav"])

    def test_shots_missing_scenario_never_starts_a_recording(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = SimpleNamespace(profile="fixture", rom=None, scenario=str(root / "absent.txt"))
            output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(output), \
                    patch("gen3tl.cli.verify.Profile.by_name", return_value=Mock()), \
                    patch("subprocess.run") as subprocess_run:
                status = run_status(lambda report: shots(args))
            subprocess_run.assert_not_called()
            self.assertEqual(status, 1)
            self.assertIn("hint         path: existing file or directory", output.getvalue())
            self.assertNotIn("Errno", output.getvalue())
            self.assertEqual(output.getvalue().splitlines()[-1],
                             f"result       failed: missing path: {root / 'absent.txt'}")

    def test_shots_subprocess_failure_reports_a_log_without_raw_errors(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scenario = root / "scenario.txt"
            scenario.write_text("wait 1\n")
            args = SimpleNamespace(profile="fixture", rom=None, scenario=str(scenario), out=str(root / "shots"),
                                   built=str(root / "built.gba"), lang="fixture")
            profile = Mock()
            profile.__getitem__ = Mock(return_value={})
            result = SimpleNamespace(returncode=1, stderr="Traceback\nOSError: raw runtime details\n")
            output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(output), \
                    patch("gen3tl.cli.verify.Profile.by_name", return_value=profile), \
                    patch("subprocess.run", return_value=result) as subprocess_run:
                status = run_status(lambda report: shots(args))
            subprocess_run.assert_called_once()
            self.assertEqual(status, 1)
            self.assertNotIn("Traceback", output.getvalue())
            self.assertNotIn("raw runtime details", output.getvalue())
            self.assertRegex(output.getvalue().splitlines()[-1], r"^result\s+failed: recording failed: \S+, exit 1$")
            logs = list((root / "shots").glob("*-error.log"))
            self.assertEqual(len(logs), 1)
            self.assertEqual(logs[0].read_text(), result.stderr)

    def test_steps_repeats_and_checks(self):
        steps = parse_scenario(HATCH)
        self.assertEqual(steps[:5], [("statefile", ["load", "e6"]), ("giveegg", ["175"]), ("eggcycles", ["0"]),
                                     ("stepcounter", ["254"]), ("press", ["DOWN", "16", "4"])])
        self.assertEqual(steps[5:8], [("press", ["A", "4", "120"])] * 3)
        self.assertEqual(steps[8:], [("writesave", ["hatched"]), ("hatched", ["hatched", "1", "175"])])
        self.assertEqual(hatch_checks(steps), [("hatched", 1, 175)])
        self.assertEqual(hatch_checks(parse_scenario("wait 2\nshot title\n")), [])

    def test_a_bad_line_is_refused_before_a_run(self):
        cases = {"jump 3": "line 1: unknown scenario step 'jump 3'",
                 "wait": "line 1: wait takes 1 argument: 'wait'",
                 "shot a b": "line 1: shot takes 1 argument: 'shot a b'",
                 "press": "line 1: press takes 1 to 3 arguments",
                 "repeat x press A": "line 1: unknown scenario step 'repeat x press A'",
                 "giveegg 175\n\nhatched hatched 1 175": "line 3: hatched takes a save written before",
                 "writesave hatched\nhatched hatched one 175": "line 2: hatched takes a save written before"}
        for script, message in cases.items():
            with self.subTest(script=script):
                with self.assertRaisesRegex(HeadlessError, message):
                    parse_scenario(script)

    def test_breeding_steps_and_includes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "breed.txt").write_text("partymon 1 133 0x01234567 0x02468ACE\n"
                                            "special 0xBB\nwallegg 3000\ngoto 0x1E619AF\n", encoding="utf-8")
            steps = parse_scenario("include breed.txt\neggcycles 0\n", root)
        self.assertEqual(steps, [("partymon", ["1", "133", "0x01234567", "0x02468ACE"]),
                                 ("special", ["0xBB"]), ("wallegg", ["3000"]),
                                 ("goto", ["0x1E619AF"]), ("eggcycles", ["0"])])
        with self.assertRaisesRegex(HeadlessError, "include needs the scenario's directory"):
            parse_scenario("include breed.txt")


class HatchCheckTest(unittest.TestCase):
    """The party slot of a saved game against the species catalog's name."""


    def test_the_species_catalog(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "tables").mkdir()
            write(root / "tables" / "species.po", [PoEntry("table:species/174", "Igglybuff", "Игглибафф"),
                                                   PoEntry("table:species/175", "Togepi", "Тогэпи")], {})
            write(root / "items.po", [PoEntry("table:items/175", "Tonic", "Тоник")], {})
            self.assertEqual(catalog_species(root, 175), ("Togepi", "Тогэпи"))
            write(root / "tables" / "species.po", [PoEntry("table:species/175", "Togepi")], {})
            self.assertEqual(catalog_species(root, 175), ("Togepi", ""))   # untranslated: the English name stays
            with self.assertRaisesRegex(ValueError, "no table:species/176"):
                catalog_species(root, 176)
