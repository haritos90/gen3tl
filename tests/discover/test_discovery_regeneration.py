"""Regeneration refreshes named inputs and detects output drift."""

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gen3tl.core.rom import Rom
from gen3tl.discover import regeneration
from gen3tl.project import build_root


class RegenerationTest(unittest.TestCase):
    def test_each_output_must_match_tracked_and_previous_bytes(self):
        expected = {"profile.toml": b"profile", "report.md": b"report"}
        self.assertEqual(regeneration.differences(expected, expected, expected), [])
        for name in expected:
            changed = {**expected, name: b"changed"}
            self.assertEqual(regeneration.differences(expected, changed, expected),
                             ["tracked:" + name, "repeat:" + name])

    def test_stale_studies_are_not_used_and_tracked_outputs_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = {"profile.toml": b"profile", "report.md": b"report"}
            for name, content in expected.items():
                (root / name).write_bytes(content)
            (root / "inventory.json").write_text("stale")
            rom = Rom(b"\xff" * 16)
            prof = Mock(path=root, name="fixture")
            prof.load_rom.return_value = rom
            prof.get.return_value = None

            class Project:
                @property
                def build(self):
                    return build_root(root)

            args = SimpleNamespace(profile="fixture", project=Project(), rom=None, work_dir=root / "scratch")
            order = []

            def refresh(name):
                def invoke(current):
                    order.append(name)
                    (current.project.build / (name + ".json")).write_text('{"fresh": true}')
                return invoke

            def emit(result, output, name, reference):
                for name, content in expected.items():
                    (output / name).write_bytes(content)

            result = SimpleNamespace(issues=[])
            output = io.StringIO()
            with patch("gen3tl.discover.regeneration.Profile.by_name", return_value=prof), \
                    patch("gen3tl.cli.discover.inventory", side_effect=refresh("inventory")), \
                    patch("gen3tl.cli.discover.context", side_effect=refresh("context")), \
                    patch("gen3tl.cli.discover.reach", side_effect=refresh("reach")), \
                    patch("gen3tl.discover.foundations.run", return_value=result) as discover, \
                    patch("gen3tl.discover.observations.apply") as observe, \
                    patch("gen3tl.discover.emission.write", side_effect=emit), \
                    patch("gen3tl.profile.schema.overlay_files", return_value=[]), redirect_stdout(output):
                self.assertEqual(regeneration.run(args), 0)
            self.assertEqual(order, ["inventory", "context", "reach"] * 2)
            self.assertEqual(discover.call_count, 2)
            self.assertEqual(observe.call_args_list, [unittest.mock.call(rom, result, root)] * 2)
            self.assertTrue(all(call.kwargs["inventory"] == {"fresh": True} for call in discover.call_args_list))
            self.assertEqual((root / "inventory.json").read_text(), "stale")
            self.assertEqual({name: (root / name).read_bytes() for name in expected}, expected)
            self.assertEqual(output.getvalue().count("sha256"), 6)
