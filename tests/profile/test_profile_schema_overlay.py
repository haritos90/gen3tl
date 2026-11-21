from test_roots import TOOLKIT_ROOT
"""Profile schema overlay checks and fixtures."""

import sys
from pathlib import Path
import io
import re
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.cli.main import main
from gen3tl.core.rom import RomError
from gen3tl.profile import profile, schema
from gen3tl.profile.profile import Profile
from project_fixture import SAMPLE
from profile_schema_support import GENERATED, HEADER, OVERLAY, write_dir


class OverlayDirectoryTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "projects.toml").write_text("", encoding="utf-8")

    def directory(self, fragments, name="game", generated=HEADER):
        path = write_dir(self.root, name, generated, None)
        overlay = path / "overlay"
        overlay.mkdir()
        for filename, text in fragments.items():
            file = overlay / filename
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(text, encoding="utf-8")
        (overlay / "order.txt").write_text("\n".join(fragments) + "\n", encoding="utf-8")
        return path

    def test_sample_directory_is_byte_equal_to_the_single_file(self):
        from gen3tl.verify import profile as proof

        source = profile.PROFILE_DIR / f"{SAMPLE}.toml"
        identity, rest = source.read_text(encoding="utf-8").split("[engine]", 1)
        engine, probe = ("[engine]" + rest).split("[probe.menu]", 1)
        path = self.directory({"z-identity.toml": identity, "engine.toml": engine,
                               "probe/menu.toml": "[probe.menu]" + probe})
        single = write_dir(self.root, "single", HEADER, source.read_text(encoding="utf-8"))
        self.assertEqual(schema.read(path).overlay, schema.read(single).overlay)
        self.assertEqual(proof.record(single, self.root / "before"), proof.record(path, self.root / "after"))

    def test_nested_tables_keep_order_and_generated_precedence(self):
        fragments = {"z-first.toml": OVERLAY + '\n[layout.window_width]\n"items:2" = 10\n',
                     "layout/second.toml": '[layout.window_width]\n"items:1" = 20\n'
                                           '[layout.window_lines]\n"items:1" = 2\n'}
        path = self.directory(fragments, generated=GENERATED)
        single = write_dir(self.root, "single", GENERATED, "\n".join(fragments.values()).replace(
            '[layout.window_width]\n"items:1"', '"items:1"'))
        self.assertEqual(repr(schema.load(path)), repr(schema.load(single)))
        self.assertEqual([f.id for f in schema.overlay_facts(schema.read(path))],
                         [f.id for f in schema.overlay_facts(schema.read(single))])
        self.assertEqual(list(schema.load(path)["layout"]["window_width"]), ["items:2", "items:1"])

    def test_sources_and_paths_select_the_containing_profile(self):
        path = self.directory({"z.toml": '[rom]\nsha1 = "ab"\n', "nested/a.toml": '[rom]\nsize = 8\n'})
        files = [path / name for name in ("profile.toml", "overlay/order.txt", "overlay/z.toml",
                                         "overlay/nested/a.toml")]
        self.assertEqual(profile.source_files(path), files)
        for selected in [path, path / "overlay", *files]:
            with self.subTest(selected=selected):
                self.assertEqual(profile.locate(str(selected)), path)
                self.assertEqual(Profile.by_name(str(selected)).sha1, "ab")
        with self.assertRaisesRegex(RomError, "no profile"):
            profile.locate(str(path / "overlay/missing.toml"))

    def test_invalid_overlays_fail_with_one_error_line(self):
        for name in ("duplicate", "array", "type", "unlisted", "missing", "both", "no-order",
                     "repeated", "outside", "reserved"):
            with self.subTest(name=name):
                path = self.directory({"first.toml": '[rom]\nsha1 = "ab"\n',
                                       "second.toml": '[rom]\nsize = 8\n'}, name)
                overlay = path / "overlay"
                first, second, order = (overlay / file for file in ("first.toml", "second.toml", "order.txt"))
                if name == "duplicate":
                    second.write_text('[rom]\nsha1 = "ab"\n', encoding="utf-8")
                    message = "duplicate overlay key rom.sha1"
                elif name == "array":
                    first.write_text('[[font]]\nname = "a"\n', encoding="utf-8")
                    second.write_text('[[font]]\nname = "b"\n', encoding="utf-8")
                    message = "duplicate overlay key font"
                elif name == "type":
                    second.write_text('rom = "ab"\n', encoding="utf-8")
                    message = "duplicate overlay key rom"
                elif name == "unlisted":
                    order.write_text("first.toml\n", encoding="utf-8")
                    message = "unlisted files: 'second.toml'"
                elif name == "missing":
                    second.unlink()
                    message = "missing file 'second.toml'"
                elif name == "both":
                    (path / "overlay.toml").write_text("", encoding="utf-8")
                    message = "both overlay.toml and overlay/ exist"
                elif name == "no-order":
                    order.unlink()
                    message = "missing order.txt"
                elif name == "repeated":
                    order.write_text("first.toml\nfirst.toml\nsecond.toml\n", encoding="utf-8")
                    message = "repeated file 'first.toml'"
                elif name == "outside":
                    order.write_text("../profile.toml\n", encoding="utf-8")
                    message = "invalid overlay file '../profile.toml'"
                else:
                    second.write_text(HEADER, encoding="utf-8")
                    message = "[discovery] is reserved"
                with self.assertRaisesRegex(schema.ProfileError, re.escape(message)):
                    schema.load(path)
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    status = main(["--project-root", str(self.root), "--profile", str(path),
                                   "verify", "profile", "--record", str(self.root / "proof")])
                self.assertNotEqual(status, 0)
                self.assertEqual(out.getvalue(), "")
                lines = err.getvalue().splitlines()
                self.assertEqual([line.split()[0] for line in lines], ["error", "result"])
                self.assertIn(message, err.getvalue())
