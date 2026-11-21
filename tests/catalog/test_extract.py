import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from test_roots import TOOLKIT_ROOT

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from project_fixture import write_policy  # noqa: E402
from gen3tl.web import snapshotstore as snapshot_store
from gen3tl import __version__  # noqa: E402
from gen3tl.catalog import extract, keys, source  # noqa: E402
from gen3tl.catalog.po import KEY_ONLY, SOURCE_FIELD, PoEntry, read, write  # noqa: E402
from gen3tl.cli.main import main  # noqa: E402
from gen3tl.core.charmap import Charmap  # noqa: E402
from gen3tl.core.rom import Rom, ptr_bytes  # noqa: E402
from gen3tl.discover import inventory  # noqa: E402
from gen3tl.profile.profile import Profile  # noqa: E402
from gen3tl.project import Manifest, Registry  # noqa: E402
from gen3tl.web.editorcheck import write_bundle  # noqa: E402

CM = Charmap()
TEXTS = {0x12001: "Hello {PLAYER}!", 0x12101: "Where are you going?\\nCome back!"}
NAMES = ("POTION", "ETHER")   # a fixed table of 8-byte fields at NAMES_AT
NAMES_AT = 0x15000
WIDTHS = 0x10000
ROOT = TOOLKIT_ROOT


def fixture_rom() -> bytes:
    """Two script texts, each behind a script command, a name table and a font of 6 px glyphs."""
    data = bytearray(b"\xff" * 0x20000)
    for (at, text), (command, prefix) in zip(TEXTS.items(), ((0x801, b"\x0f\x00"), (0x901, b"\x67"))):
        blob = CM.encode(text)
        data[at:at + len(blob)] = blob
        data[command:command + len(prefix) + 4] = prefix + ptr_bytes(at)
    for i, name in enumerate(NAMES):
        blob = CM.encode(name)
        data[NAMES_AT + 8 * i:NAMES_AT + 8 * i + len(blob)] = blob
    data[WIDTHS:WIDTHS + 256] = b"\x06" * 256
    return bytes(data)


def have_pyodide() -> bool:
    """Node with the pyodide package (NODE_PATH may name its node_modules)."""
    if shutil.which("node") is None:
        return False
    return subprocess.run(["node", "-e", "require.resolve('pyodide')"], capture_output=True).returncode == 0


# the bundle's reader alone on sys.path
DRIVER = """
import json, sys
sys.path.insert(0, sys.argv[1])
from gen3tl.catalog import source
with open(sys.argv[2], "rb") as fh:
    rom = fh.read()
with open(sys.argv[3], encoding="utf-8") as fh:
    rows = json.load(fh)
texts, missing = source.read(rom, rows)
print(json.dumps({"file": source.__file__, "texts": texts, "missing": missing}, sort_keys=True))
"""
# Page reader: web/js/check.js under Node with Pyodide
NODE = """
import fs from "node:fs";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
const require = createRequire(import.meta.url);
const { loadPyodide } = require("pyodide");
const [check, zip, rom, rows] = process.argv.slice(2);
const { Check, englishOf } = await import(pathToFileURL(check));
const py = await loadPyodide();
const reader = new Check(py, new Uint8Array(fs.readFileSync(zip)));
const source = { profile: "game", rows: JSON.parse(fs.readFileSync(rows, "utf8")) };
const out = await englishOf(reader, new Uint8Array(fs.readFileSync(rom)), source);
console.log(JSON.stringify({ texts: out.texts, missing: out.missing }));
"""


class ExtractTest(unittest.TestCase):
    """catalog extract on a game project of the fixture ROM."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = root = Path(tmp.name).resolve()
        rom = fixture_rom()
        (root / "game.gba").write_bytes(rom)
        sha1 = hashlib.sha1(rom).hexdigest()
        self.profile_path = root / "game.toml"
        self.profile_path.write_text(
            f'[rom]\nname = "Game"\nsha1 = "{sha1}"\npath = "game.gba"\n\n[support]\nstage = "translation"\n\n'
            f'[font_style]\nstyle = "main"\n\n[[font]]\nname = "main"\nglyphs = 0x14000\nwidths = {WIDTHS:#x}\n'
            f'count = 256\n\n[[table]]\nname = "items"\nbase = {NAMES_AT:#x}\nstride = 8\ncount = {len(NAMES)}\n'
            'field_length = 8\n', encoding="utf-8")
        self.profile = Profile.load(self.profile_path)
        (root / "translation/ru/po").mkdir(parents=True)
        write_policy(root / "translation/ru", self.profile, "ru")
        (root / "projects.toml").write_text(
            '[defaults]\nprofile = "game"\nlanguage = "ru"\n\n[[project]]\nprofile = "game"\nlanguage = "ru"\n'
            'root = "."\npolicy = "translation/ru/policy.toml"\n', encoding="utf-8")
        manifest = root / "project.toml"
        manifest.write_text(Manifest(manifest, "game", sha1, __version__, ("ru",), "ru").text(), encoding="utf-8")
        build = root / "build"
        build.mkdir()
        identity = {"profile": self.profile.name, "rom_sha1": sha1}
        inventory.save(inventory.build(self.profile, Rom(rom)), build / "inventory.json")
        (build / "context.json").write_text(json.dumps({**identity, "strings": {}}), encoding="utf-8")
        self.reach = {**identity, "strings": {}}
        (build / "reach.json").write_text(json.dumps(self.reach), encoding="utf-8")

    def run_cli(self, *argv, command="extract"):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            code = main(["--project-root", str(self.root), "--profile", str(self.profile_path),
                         "catalog", command, *argv])
        return code, dict(line.split(None, 1) for line in out.getvalue().splitlines())

    def test_writes_the_source_catalog_deterministically_and_reports_changes(self):
        pot = self.root / "build/catalog.pot"
        code, report = self.run_cli()
        self.assertEqual((code, report["status"], report["entries"], report["added"]),
                         (0, "written", "4 entries", "4 entries"))
        header, entries = read(pot)
        self.assertEqual((header["Project-Id-Version"], header["Language"], header["X-ROM-SHA1"]),
                         ("game", "", self.profile.sha1))
        self.assertEqual({e.msgctxt: e.msgid for e in entries},
                         {**{f"s{at:07X}": t for at, t in TEXTS.items()}, "items:0": "POTION", "items:1": "ETHER"})
        self.assertEqual([e.references for e in entries if e.msgctxt.startswith("s")], [["0x0012001"], ["0x0012101"]])
        second = next(e for e in entries if e.msgctxt == "s0012101")
        self.assertIn("original lines: 2, widths px: 120, 60", second.extracted)
        self.assertTrue(all(not e.msgstr and not e.translator for e in entries))
        first = pot.read_bytes()
        code, report = self.run_cli()
        self.assertEqual((code, report["status"], report["changed"]), (0, "unchanged", "0 entries"))
        self.assertEqual(pot.read_bytes(), first)
        # a changed study changes the comments of its entry
        self.reach["strings"]["s0012001"] = {"class": "unreachable", "group": "script"}
        (self.root / "build/reach.json").write_text(json.dumps(self.reach), encoding="utf-8")
        code, report = self.run_cli()
        self.assertEqual((code, report["status"], report["changed"], report["added"], report["removed"]),
                         (0, "updated", "1 entries", "0 entries", "0 entries"))
        entry = next(e for e in read(pot)[1] if e.msgctxt == "s0012001")
        self.assertEqual(entry.extracted[0], "reach: never shown (reach study: unreachable script text)")

    def test_the_module_matches_the_command(self):
        rom = self.profile.load_rom(self.root / "game.gba")
        entries = extract.entries(self.profile, rom, self.root / "build", self.root)
        out = self.root / "other.pot"
        extract.write(out, entries, self.profile)
        self.assertEqual(self.run_cli("--out", str(out))[1]["status"], "unchanged")

    def test_key_only_rewrites_what_the_english_source_catalog_restores(self):
        self.run_cli()
        _, source = read(self.root / "build/catalog.pot")
        po = self.root / "translation/ru/po/script.po"
        full = [PoEntry(e.msgctxt, e.msgid, "Перевод", list(e.extracted), ["source: a note"], list(e.references))
                for e in source]
        write(po, full, {"Project-Id-Version": "game", "Language": "ru"})
        code, report = self.run_cli(command="key-only")
        self.assertEqual((code, report["status"], report["files"], report["entries"]),
                         (0, "rewritten", "1 files", "4 entries"))
        header, keyed = read(po)
        self.assertEqual(header[SOURCE_FIELD], KEY_ONLY)
        self.assertTrue(all(e.keyed and e.msgid == e.msgctxt for e in keyed))
        self.assertNotIn("Come back", po.read_text(encoding="utf-8"))
        extract.english(keyed, source)
        self.assertEqual(keyed, full)
        self.assertEqual(self.run_cli(command="key-only")[1]["status"], "unchanged")
        # English the ROM does not return is refused
        other = self.root / "translation/ru/po/other.po"
        write(other, [PoEntry("s0012001", "Hello, {PLAYER}!", "Привет")], {"Project-Id-Version": "game",
                                                                          "Language": "ru"})
        before = other.read_bytes()
        code, report = self.run_cli(command="key-only")
        self.assertEqual((code, report["entry"], report["error"]),
                         (1, "s0012001", "1 entries differ from the English source catalog"))
        self.assertEqual(other.read_bytes(), before)

    def source_rows(self) -> tuple[dict, list]:
        """The rows of source.json for the fixture's extraction, and the extraction."""
        inv = json.loads((self.root / "build/inventory.json").read_text(encoding="utf-8"))
        by_id = {e["id"]: e for e in inv["entries"]}
        found = extract.entries(self.profile, self.profile.load_rom(self.root / "game.gba"), self.root / "build",
                                self.root)
        return {e.msgctxt: source.row(by_id[e.msgctxt]) + [source.digest(e.msgid)] for e in found}, found

    def test_the_bundle_reads_the_english_from_the_original_and_a_patched_rom(self):
        rows, found = self.source_rows()
        (self.root / "rows.json").write_text(json.dumps(rows), encoding="utf-8")
        write_bundle(self.root / "bundle.zip", {})
        with zipfile.ZipFile(self.root / "bundle.zip") as z:
            z.extractall(self.root / "bundle")
        patched = bytearray((self.root / "game.gba").read_bytes())
        patched[0x12001:0x12001 + 6] = CM.encode("Privet", terminate=False)   # written in place
        (self.root / "patched.gba").write_bytes(patched)
        english = {e.msgctxt: e.msgid for e in found}
        for rom, missing in (("game.gba", []), ("patched.gba", ["s0012001"])):
            with self.subTest(rom=rom):
                run = subprocess.run([sys.executable, "-I", "-c", DRIVER, str(self.root / "bundle"),
                                      str(self.root / rom), str(self.root / "rows.json")],
                                     capture_output=True, text=True)
                self.assertEqual(run.returncode, 0, run.stderr)
                out = json.loads(run.stdout)
                self.assertTrue(out["file"].startswith(str(self.root / "bundle")))
                self.assertEqual(out["missing"], missing)
                self.assertEqual(out["texts"], {k: v for k, v in english.items() if k not in missing})
                if have_pyodide():   # the page's reader in Node gives the same
                    (self.root / "run.mjs").write_text(NODE, encoding="utf-8")
                    node = subprocess.run(["node", str(self.root / "run.mjs"), str(ROOT / "web/js/check.js"),
                                           str(self.root / "bundle.zip"), str(self.root / rom),
                                           str(self.root / "rows.json")], capture_output=True, text=True,
                                          timeout=600)
                    self.assertEqual(node.returncode, 0, node.stderr[-2000:])
                    self.assertEqual(json.loads(node.stdout), {"texts": out["texts"], "missing": missing})

    def test_regeneration_refuses_an_english_other_than_the_snapshots(self):
        rows, found = self.source_rows()
        project = Registry.load(self.root).resolve("game", "ru")
        digests = {key: row[4] for key, row in rows.items()}
        project.snapshot.parent.mkdir(parents=True)
        snapshot_store.write(project.snapshot, {"source": {k: [d] for k, d in digests.items()}})
        rom = self.profile.load_rom(self.root / "game.gba")
        self.assertEqual(extract.regenerate(self.profile, rom, project), found)
        digests["s0012101"] = source.digest("Where are you going?")
        snapshot_store.write(project.snapshot, {"source": {k: [d] for k, d in digests.items()}})
        with self.assertRaisesRegex(ValueError, "the English of 1 entries differs from the snapshot digests: "
                                                "s0012101; the ROM, the profile or the studies differ"):
            extract.regenerate(self.profile, rom, project)
        self.assertEqual(extract.verify(found[:1], {e.msgctxt: source.digest(e.msgid) for e in found},
                                        keys.EMPTY), sorted(e.msgctxt for e in found[1:]))

    def test_a_missing_study_is_one_error_line(self):
        (self.root / "build/context.json").unlink()
        code, report = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn("run gen3tl discover context first", report["error"])
        self.assertFalse((self.root / "build/catalog.pot").exists())


if __name__ == "__main__":
    unittest.main()
