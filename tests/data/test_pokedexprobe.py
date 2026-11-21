from test_roots import TOOLKIT_ROOT
"""The Pokedex probe and the profile skeleton."""

import contextlib
import io
import struct
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.data import pokedexdata as pd  # noqa: E402
from gen3tl.data import pokedexprobe as probe  # noqa: E402
from gen3tl.cli import data as cli  # noqa: E402
from gen3tl.core.charmap import Charmap  # noqa: E402
from gen3tl.core.rom import Rom  # noqa: E402
from gen3tl.cli.main import main  # noqa: E402
from gen3tl.profile.profile import Profile  # noqa: E402
from gen3tl.project import Registry  # noqa: E402
from project_fixture import CATALOG_STAGE, write_policy  # noqa: E402

CM = Charmap()
NAMES = 0x1000      # 11-byte slots
STATS = 0x2000      # stride 28, FireRed stat order
MAP = 0x4000        # u16 slot -> dex


def rom_bytes(count: int = 200) -> bytes:
    data = bytearray(b"\xff" * 0x40000)
    data[0xA0:0xAC] = b"POKEMON EMER"
    data[0xAC:0xB0] = b"BPEE"
    for slot in range(count):
        at = NAMES + slot * 11
        if slot == 0:
            blob = CM.encode("??????????")
        else:
            blob = CM.encode("Bulbasaur" if slot == 1 else "Ivysaur" if slot == 2
                             else "Venusaur" if slot == 3 else f"MONMON{slot:04d}")
        data[at:at + len(blob)] = blob
    for slot in range(count):
        at = STATS + slot * 28
        if slot == 1:
            struct.pack_into("<6B", data, at, 45, 49, 49, 65, 65, 45)
            data[at + 6], data[at + 7] = 12, 3
        elif slot == 2:
            struct.pack_into("<6B", data, at, 60, 62, 63, 80, 80, 60)
            data[at + 6], data[at + 7] = 12, 3
        elif slot > 0:
            struct.pack_into("<6B", data, at, 50, 60, 70, 80, 90, 100)
            data[at + 6], data[at + 7] = 0, 1
    for slot in range(count):
        struct.pack_into("<H", data, MAP + slot * 2, slot)
    return bytes(data)


class ProbeTest(unittest.TestCase):
    def setUp(self):
        self.rom = Rom(rom_bytes(), Path("synthetic.gba"))
        self.doc = probe.probe(self.rom)

    def test_reports_header_engine_and_encoding(self):
        self.assertEqual(self.doc["rom"]["game_code"], "BPEE")
        self.assertEqual(self.doc["engine"]["family"], "emerald")
        self.assertEqual(self.doc["charmap"]["verdict"], "western-gen3")
        self.assertIn("Bulbasaur", self.doc["charmap"]["evidence"])

    def test_reports_the_species_count_from_the_name_run(self):
        self.assertEqual(self.doc["species_count"], 200)
        names = [c for c in self.doc["candidates"] if c["kind"] == "species names"]
        self.assertEqual(len(names), 1)
        self.assertEqual((names[0]["base"], names[0]["stride"], names[0]["count"], names[0]["slot1"]),
                         (NAMES, 11, 200, "Bulbasaur"))

    def test_reports_the_stats_run_with_its_engine_order(self):
        stats = [c for c in self.doc["candidates"] if c["kind"] == "species info / base stats"]
        self.assertTrue(any(c["base"] == STATS and c["stride"] == 28 and c["order"] == "firered"
                            and c["count"] == 200 for c in stats))

    def test_reports_the_dex_map_run(self):
        maps = [c for c in self.doc["candidates"] if c["kind"] == "species to national dex map"]
        self.assertTrue(any(c["base"] == MAP and c["stride"] == 2 and c["count"] == 200 for c in maps))

    def test_lists_what_remains_unproven(self):
        self.assertEqual(self.doc["unknown"], ["dex entry text", "evolution records",
                                               "sprite and palette tables"])

    def test_the_command_prints_and_writes_nothing(self):
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "synthetic.gba"
            path.write_bytes(rom_bytes())
            args = mock.Mock(rom=str(path))
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.pokedex_probe(args), 0)
            before = {p.name for p in Path(tmp).iterdir()}
        text = out.getvalue()
        self.assertIn("engine       emerald", text)
        self.assertIn(f"{NAMES:#x}", text)
        self.assertEqual(before, {"synthetic.gba"})   # the probe never edits anything


class SkeletonTest(unittest.TestCase):
    def setUp(self):
        self.data = rom_bytes()
        self.rom = Rom(self.data, Path("synthetic.gba"))
        self.doc = probe.probe(self.rom)

    def test_the_skeleton_round_trip_rejects_until_proven(self):
        text = probe.skeleton("synthetic.gba", self.doc)
        parsed = tomllib.loads(text)
        self.assertEqual(parsed["rom"]["sha1"], self.rom.sha1())
        self.assertEqual(parsed["engine"]["family"], "emerald")
        block = parsed["pokedex"]
        self.assertEqual(block["schema"], 1)
        self.assertEqual(block["name_table"], "species")
        # the candidate sits in the block with an empty proof
        self.assertEqual(block["species"]["base"], STATS)
        self.assertEqual(block["species"]["stride"], 28)
        problems = pd.check(block)
        self.assertIn("[pokedex.species] evidence", problems)
        self.assertIn("[pokedex.species] fields", problems)
        # filling the proofs makes the block drive a dataset
        block["species"]["evidence"] = "synthetic fixture: Bulbasaur anchor at slot 1"
        block["species"]["fields"] = {"hp": 0, "attack": 1, "defense": 2, "speed": 3,
                                      "sp_attack": 4, "sp_defense": 5, "type1": 6, "type2": 7}
        self.assertEqual(pd.check(block), [])
        doc = pd.build(block, self.data, {"id": "synthetic", "name": "Synthetic"})
        self.assertEqual(doc["species"][0]["slot"], 1)
        self.assertEqual(doc["species"][0]["types"], [12, 3])

    def test_the_command_writes_once_and_refuses_to_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom_path = Path(tmp) / "synthetic.gba"
            rom_path.write_bytes(self.data)
            out_path = Path(tmp) / "skeleton.toml"
            args = mock.Mock(rom=str(rom_path), out=str(out_path))
            self.assertEqual(cli.pokedex_profile(args), 0)
            self.assertTrue(out_path.exists())
            self.assertIn("[pokedex]", out_path.read_text(encoding="utf-8"))
            self.assertEqual(cli.pokedex_profile(args), 1)   # never overwrites


class ProfileRomTest(unittest.TestCase):
    def test_probe_and_skeleton_use_profile_rom_or_explicit_override(self):
        language = Registry.load(CATALOG_STAGE).default_language
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            default = root / "base.gba"
            default.write_bytes(rom_bytes())
            override = root / "override.gba"
            override.write_bytes(rom_bytes(180))
            profile = root / "game.toml"
            profile.write_text(f'[rom]\npath = "base.gba"\nsha1 = "{Rom.load(default).sha1()}"\n',
                               encoding="utf-8")
            write_policy(root, Profile.load(profile), language)
            (root / "projects.toml").write_text(
                f'[defaults]\nprofile = "game"\nlanguage = "{language}"\n[[project]]\nprofile = "game"\n'
                f'language = "{language}"\nroot = "."\npolicy = "policy.toml"\n', encoding="utf-8")
            selection = ["--project-root", str(root), "--profile", str(profile)]
            for command in ("probe", "profile"):
                for path, extra in ((default, []), (override, ["--rom", str(override)])):
                    out = root / f"{command}-{path.stem}.toml"
                    args = [*selection, "data", command, *extra]
                    if command == "profile":
                        args.extend(["--out", str(out)])
                    output = io.StringIO()
                    with self.subTest(command=command, rom=path.name), contextlib.redirect_stdout(output):
                        self.assertEqual(main(args), 0)
                    if command == "probe":
                        self.assertIn(f"rom          {path}", output.getvalue())
                    else:
                        self.assertEqual(tomllib.loads(out.read_text())["rom"]["path"], str(path))
            default.write_bytes(override.read_bytes())
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main([*selection, "data", "probe"]), 1)


if __name__ == "__main__":
    unittest.main()
