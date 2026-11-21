import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from gen3tl.cli import main as cli
from gen3tl.core.charmap import Charmap
from gen3tl.discover.discovery import inspect, publish_catalog, source_catalog
from gen3tl.catalog.po import read
from gen3tl.profile.profile import Profile
from gen3tl.core.rom import Rom, ptr_bytes
from project_fixture import CATALOG_STAGE, SAMPLE, sample_rom

class DiscoveryTest(unittest.TestCase):
    def profile(self):
        return Profile({"support": {"stage": "discovery"}, "engine": {"family": "emerald"}},
                       Path("test-game.toml"))

    def test_candidates_never_claim_verified_references(self):
        rom = Rom(b"\xff" * 1024)
        rom.write(100, Charmap().encode("NEW GAME"))
        rom.write(16, ptr_bytes(100))
        rom.write(200, bytes([1, 2, 3, 255]))
        rom.write(32, ptr_bytes(200))
        with patch("gen3tl.discover.scan.classify_refs", side_effect=AssertionError("engine classifier called")):
            doc = inspect(self.profile(), rom)
        self.assertEqual(doc["status"], "unverified-candidates")
        self.assertNotIn("entries", doc)
        self.assertEqual(doc["candidates"], [
            {"id": "s0000064", "offset": 100, "length": 9, "text": "NEW GAME", "references": [16]}])

    def test_guard_precedes_every_unsupported_handler(self):
        prof = self.profile()
        for command in cli.COMMANDS:
            if command not in ("rom identify", "rom decode", "rom evidence", "discover run", "discover candidates", "discover graphics", "discover flag", "catalog source", "data probe", "discover check",
                               "discover convert", "check commit-message"):   # the message check reads no profile
                self.assertIsNotNone(prof.command_problem(command), command)
        for argv in (["build", "rom"], ["discover", "inventory"], ["discover", "context"], ["web", "data"],
                     ["rom", "decode", "64", "--built", "missing.gba"], ["rom", "decode", "64", "--mode", "battle"]):
            with self.subTest(argv=argv), patch.object(Profile, "by_name", return_value=prof):
                with patch.dict(cli.COMMANDS, {" ".join(argv[:2]): unittest.mock.Mock()}):
                    with redirect_stderr(io.StringIO()) as error, self.assertRaises(SystemExit) as ex:
                        cli.main(["--project-root", str(CATALOG_STAGE), *argv])
                    self.assertEqual(ex.exception.code, 2)
                    self.assertIn("discovery profile", error.getvalue())
                    cli.COMMANDS[" ".join(argv[:2])].assert_not_called()

    def test_discovery_isolated_from_translation_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "build").mkdir()
            sentinel = root / "build/inventory.json"
            sentinel.write_text("unchanged")
            previous = Path.cwd()
            try:
                os.chdir(root)
                for name in ("game-one", "game-two"):
                    prof = self.profile()
                    prof.path = Path(name + ".toml")
                    with patch.object(Profile, "by_name", return_value=prof), \
                            patch.object(Profile, "load_rom", return_value=Rom(b"\xff" * 64)), \
                            redirect_stdout(io.StringIO()):
                        self.assertEqual(cli.main(["discover", "candidates"]), 0)
                    doc = json.loads((root / "build/discovery" / name / "candidates.json").read_text())
                    self.assertEqual(doc["profile"], name)
                self.assertEqual(sentinel.read_text(), "unchanged")
            finally:
                os.chdir(previous)


    def assert_rom_evidence(self, prof, rom):
        if "sha256" in prof["rom"]:
            self.assertEqual(hashlib.sha256(rom.data).hexdigest(), prof["rom"]["sha256"])
        probe = prof.get("probe", {}).get("menu")
        if probe:
            self.assertEqual(Charmap().decode(rom.data, probe["offset"])[0], probe["text"])
            for at in probe["pointers"]:
                self.assertEqual(rom.ptr_at(at), probe["offset"])

    def test_the_sample_profile_and_its_rom_evidence(self):
        prof = Profile.by_name(SAMPLE)
        self.assertEqual(prof["support"]["stage"], "catalog")
        for command in ("rom identify", "rom decode", "rom evidence", "discover run", "catalog source"):
            self.assertIsNone(prof.command_problem(command))
        rom = Rom(sample_rom())
        self.assertEqual((rom.sha1(), len(rom)), (prof.sha1, prof["rom"]["size"]))
        self.assert_rom_evidence(prof, rom)


    def test_source_templates_are_reproducible_and_untranslated(self):
        rom = Rom(b"\xff" * 1024)
        rom.write(100, Charmap().encode("NEW GAME"))
        rom.write(16, ptr_bytes(100))
        doc = inspect(self.profile(), rom)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second = root / "first", root / "second"
            source_catalog(doc, first)
            source_catalog(doc, second)
            for name in ("source.pot", "catalog.json"):
                self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())
            header, entries = read(first / "source.pot")
            self.assertEqual(header["Language"], "")
            self.assertEqual(header["X-ROM-SHA1"], rom.sha1())
            self.assertEqual([(e.msgctxt, e.msgid, e.msgstr, e.fuzzy) for e in entries],
                             [("s0000064", "NEW GAME", "", True)])
            self.assertEqual(entries[0].references, ["0x0000064"])
            self.assertIn("pointer candidates: 0x0000010", entries[0].extracted)
            publish_catalog(first, root / "editor")
            self.assertTrue((root / "editor/sources/test-game/source.pot").exists())
            (first / "source.pot").write_text("changed")
            with self.assertRaisesRegex(ValueError, "checksum"):
                publish_catalog(first, root / "editor")

    def test_source_command_does_not_activate_a_translation_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(Profile, "by_name", return_value=self.profile()), \
                    patch.object(Profile, "load_rom", return_value=Rom(b"\xff" * 64)), \
                    patch("gen3tl.project.Project.activate", side_effect=AssertionError("translation project")), \
                    patch("gen3tl.lang.langpack.LangPack.load", side_effect=AssertionError("language pack")), \
                    redirect_stdout(io.StringIO()):
                argv = ["--project-root", str(CATALOG_STAGE), "catalog", "source", "--out", str(root)]
                self.assertEqual(cli.main(argv), 0)
            self.assertEqual(read(root / "source.pot")[1], [])
            self.assertIsNone(json.loads((root / "catalog.json").read_text())["target_language"])
