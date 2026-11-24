import hashlib
import io
import json
import subprocess
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from gen3tl import __version__
from gen3tl.catalog.po import PoEntry, write
from gen3tl.cli import main as cli
from gen3tl.core import ups
from gen3tl.core.rom import Rom, ptr_bytes
from gen3tl.profile.profile import Profile, TOOLKIT_ROOT
from gen3tl.project import Manifest, build_scope
from gen3tl.verify import golden
from project_fixture import write_policy

def manifest():
    return {
        "schema": golden.SCHEMA,
        "profile": "game",
        "language": "xx",
        "inputs": {
            "base_sha1": "a" * 40,
            "tracked_sha256": "b" * 64,
            "toolkit_version": "1.2.3",
            "translation_version": "4.5.6",
        },
        "outputs": {"build/game.gba": "0" * 64, "build/inventory.json": "1" * 64},
        "identifiers": {"build/inventory.json": "4" * 64},
    }

class GoldenManifestTest(unittest.TestCase):
    def test_manifest_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "golden.json"
            golden.write(path, manifest())
            self.assertEqual(golden.load(path), manifest())
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema"], golden.SCHEMA)

    def test_comparison_reports_every_output_difference(self):
        expected = manifest()
        actual = {"build/game.gba": "2" * 64, "build/context.json": "3" * 64}
        differences = golden.compare(expected, actual)
        report = golden.report(differences)
        self.assertIn("build/game.gba", report)
        self.assertIn("build/inventory.json: missing", report)
        self.assertIn("build/context.json: not recorded", report)
        self.assertEqual(len(differences), 3)

    def test_changed_inputs_are_notes_not_differences(self):
        expected = manifest()
        identity = dict(expected["inputs"], base_sha1="c" * 40, toolkit_version="1.2.4")
        notes = golden.identity_notes(expected, identity)
        self.assertEqual([line.split(":")[0] for line in notes], ["inputs.base_sha1", "inputs.toolkit_version"])
        self.assertEqual(golden.identity_notes(expected, dict(expected["inputs"])), [])
        self.assertEqual(golden.compare(expected, dict(expected["outputs"])), [])

    def test_identifier_hashes_cover_json_outputs_only(self):
        expected = manifest()
        changed = dict(expected["outputs"], **{"build/inventory.json": "5" * 64})
        self.assertEqual(golden.compare(expected, changed, {"build/inventory.json": "4" * 64}), [])
        self.assertEqual(len(golden.compare(expected, changed, {"build/inventory.json": "6" * 64})), 1)
        rom = dict(expected["outputs"], **{"build/game.gba": "5" * 64})
        self.assertEqual(len(golden.compare(expected, rom, {"build/game.gba": "0" * 64})), 1)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "golden.json"
            golden.write(path, dict(expected, identifiers={"build/other.json": "4" * 64}))
            with self.assertRaises(golden.GoldenError):
                golden.load(path)

    def test_generation_pins_a_placeholder_version(self):
        self.assertRegex(golden.GOLDEN_VERSION, r"^\d+\.\d+\.\d+$")
        self.assertNotEqual(golden.GOLDEN_VERSION, golden.__version__)

    def test_pokedex_inventory_and_keyed_references_compare_equally(self):
        from gen3tl.catalog.keys import Aliases
        aliases = Aliases([("table:species/1", 0x100, "species:1"),
                           ("table:pokedex_entries/1", 0x444CB2, "s0444CB2")])
        dataset = {"profile": {"id": "game"}, "rom": {"sha1": "a" * 40},
                   "species": [{"slot": 1, "name": "species:1", "entry": ["s0444CB2"]}],
                   "labels": {"Entry": "s0444CB2@^", "Unknown": "s0000999"}}
        texts = {"schema": 1, "profile": "game", "language": "ru",
                 "texts": {"species:1": ["Bulbasaur", "Бульбазавр"], "s0444CB2": ["A seed", "Семя"]}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, doc in (("pokedex.json", dataset), ("pokedex-texts.json", texts)):
                with self.subTest(name=name):
                    original, keyed = root / name, root / f"keyed-{name}"
                    original.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
                    keyed.write_text(aliases.rekey_text(original.read_text(encoding="utf-8")), encoding="utf-8")
                    self.assertEqual(golden.canonical(original, aliases), golden.canonical(keyed, aliases))
                    expected = {"outputs": {name: golden.file_hash(original)},
                                "identifiers": golden.identifier_hashes({name: original}, aliases)}
                    self.assertEqual(golden.compare(expected, {name: golden.file_hash(keyed)},
                                                    golden.identifier_hashes({name: keyed}, aliases)), [])
                    changed = json.loads(keyed.read_text(encoding="utf-8"))
                    if name == "pokedex-texts.json":
                        changed["texts"]["table:species/1"][1] = "Другой перевод"
                    else:
                        changed["species"][0]["slot"] = 2
                    keyed.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
                    self.assertNotEqual(golden.canonical(original, aliases), golden.canonical(keyed, aliases))
                    self.assertEqual(len(golden.compare(expected, {name: golden.file_hash(keyed)},
                                                        golden.identifier_hashes({name: keyed}, aliases))), 1)

    def test_input_checksum_has_stable_path_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "b").write_bytes(b"second")
            (root / "a").write_bytes(b"first")
            paths = [root / "b", root / "a"]
            self.assertEqual(golden.input_checksum(root, paths), golden.input_checksum(root, reversed(paths)))
            self.assertNotEqual(golden.input_checksum(root, paths), golden.input_checksum(root, [root / "a"]))

    def test_identity_inputs_are_the_tracked_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source, note = root / "pack/agreement.py", root / "pack/notes.txt"
            generated = [root / "pack/__pycache__/agreement.cpython-314.pyc", root / "pack/.DS_Store"]
            for path in (source, note, *generated):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(path.name.encode())
            files = [source, *generated, note]
            # without Git, the generated names are left out
            self.assertEqual(golden.tracked_inputs(files, root), [source, note])
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "add", "pack/agreement.py"], check=True)
            self.assertEqual(golden.tracked_inputs(files, root), [source])
            checksum = golden.input_checksum(root, golden.tracked_inputs(files, root))
            generated[0].write_bytes(b"recompiled")
            self.assertEqual(golden.input_checksum(root, golden.tracked_inputs(files, root)), checksum)

    def test_the_batch_plan_is_no_input_outside_a_checkout(self):
        from types import SimpleNamespace

        from gen3tl.project import Manifest, Registry
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            self.assertIsNone(golden.tracked_files(root))   # selected by name, not by Git
            profile = root / "game.toml"
            profile.write_text('[rom]\nsha1 = "' + "0" * 40 + '"\n', encoding="utf-8")
            (root / "translation/ru").mkdir(parents=True)
            write_policy(root / "translation/ru", Profile.load(profile), "ru")
            (root / "translation/ru/release.toml").write_text('version = "1.0.0"\n', encoding="utf-8")
            manifest = root / "project.toml"
            manifest.write_text(Manifest(manifest, "game", "0" * 40, "1.0.0", ("ru",), "ru").text(), encoding="utf-8")
            (root / "projects.toml").write_text(
                '[defaults]\nprofile = "game"\nlanguage = "ru"\n\n[[project]]\nprofile = "game"\nlanguage = "ru"\n'
                'root = "."\npolicy = "translation/ru/policy.toml"\nbatches = "translation/batches"\n',
                encoding="utf-8")
            project = Registry.load(root).resolve("game", "ru")
            plan = root / "translation/batches/plan.json"
            plan.parent.mkdir(parents=True)
            plan.write_text("[]\n", encoding="utf-8")
            self.assertEqual(project.batches / "plan.json", plan)
            note_files = [project.translation / name for name in
                          ("notes/terms.toml", "notes/entries.toml", "notes/graphics.toml",
                           "research/example.notes.toml", "research/example.tsv")]
            for path in note_files:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture", encoding="utf-8")
            paths = golden.input_paths(SimpleNamespace(project=project, profile=str(profile), lang="ru"))
            self.assertIn(project.policy, paths)
            self.assertTrue(set(note_files) <= set(paths))
            self.assertNotIn(plan, paths)
            # named inputs make a batch-path exclusion redundant
            self.assertEqual([path for path in paths if path.is_relative_to(project.batches)], [])
            directory = root / "game"
            overlay = directory / "overlay"
            overlay.mkdir(parents=True)
            generated, order, fragment = directory / "profile.toml", overlay / "order.txt", overlay / "identity.toml"
            generated.write_text('[discovery]\nschema = 1\n', encoding="utf-8")
            order.write_text("identity.toml\n", encoding="utf-8")
            fragment.write_bytes(profile.read_bytes())
            table = overlay / "reach/never-shown.tsv"
            table.parent.mkdir()
            table.write_text("ids\tevidence\nentry:1\tUnused branch.\n", encoding="utf-8")
            order.write_text("identity.toml\nreach/never-shown.tsv\treach.never_shown\n", encoding="utf-8")
            args = SimpleNamespace(project=project, profile=str(directory), lang="ru")
            paths = golden.input_paths(args)
            self.assertTrue({generated, order, fragment, table} <= set(paths))
            checksum = golden.input_checksum(root, paths, TOOLKIT_ROOT)
            table.write_text("ids\tevidence\nentry:2\tUnused branch.\n", encoding="utf-8")
            self.assertNotEqual(golden.input_checksum(root, paths, TOOLKIT_ROOT), checksum)
            args.profile = str(profile)
            with patch("gen3tl.profile.profile.profile_paths", return_value=[directory]):
                paths = golden.input_paths(args)
            self.assertTrue({generated, order, fragment, table} <= set(paths))

    def test_toolkit_bytecode_is_no_input(self):
        pack = TOOLKIT_ROOT / "assets/lang/ru"
        with tempfile.TemporaryDirectory() as tmp:
            paths = [pack / "language.json", pack / "__pycache__/agreement.cpython-314.pyc", pack / ".DS_Store"]
            self.assertEqual(golden.tracked_inputs(paths, Path(tmp), TOOLKIT_ROOT), [paths[0].resolve()])

class GoldenTreeTest(unittest.TestCase):
    """A tree of outputs is recorded as one hash; a failure names its differing files."""

    def setUp(self):
        from gen3tl.catalog.keys import Aliases
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tree = self.root / "sprites"
        self.store = self.root / "listings"
        store = patch.object(golden, "listing_store", return_value=self.store)
        store.start()
        self.addCleanup(store.stop)
        self.aliases = Aliases([("table:species/1", 0x100, "species:1")])
        for name, data in (("manifest.json", b'{"species:1": 1}'), ("front/1.png", b"one"),
                           ("front/2.png", b"two"), ("back/1.png", b"back")):
            (self.tree / name).parent.mkdir(parents=True, exist_ok=True)
            (self.tree / name).write_bytes(data)

    def recorded(self):
        rows = golden.tree_listing(self.tree, self.aliases)
        golden.keep_listings({"sprites": rows})
        return {"outputs": {"sprites": golden.listing_digest(rows)},
                "identifiers": {"sprites": golden.listing_digest(rows, 2)}}

    def current(self):
        rows = golden.tree_listing(self.tree, self.aliases)
        return rows, {"sprites": golden.listing_digest(rows)}, {"sprites": golden.listing_digest(rows, 2)}

    def test_the_tree_hash_is_the_hash_of_its_sha256sum_listing(self):
        rows = golden.tree_listing(self.tree, self.aliases)
        self.assertEqual([row[0] for row in rows], ["back/1.png", "front/1.png", "front/2.png", "manifest.json"])
        listing = "".join(f"{golden.file_hash(self.tree / name)}  {name}\n" for name, _, _ in rows)
        self.assertEqual(golden.listing_digest(rows), hashlib.sha256(listing.encode()).hexdigest())
        self.assertEqual(rows[1][2], rows[1][1])   # a picture has no canonical form
        self.assertEqual(rows[3][2], golden.canonical_hash(self.tree / "manifest.json", self.aliases))
        (self.tree / "front/2.png").write_bytes(b"other")
        self.assertNotEqual(golden.listing_digest(golden.tree_listing(self.tree, self.aliases)),
                            golden.listing_digest(rows))

    def test_a_failure_names_the_changed_added_and_missing_files(self):
        manifest = self.recorded()
        (self.tree / "front/2.png").write_bytes(b"other")
        (self.tree / "front/3.png").write_bytes(b"three")
        (self.tree / "back/1.png").unlink()
        # keyed references compare equal
        (self.tree / "manifest.json").write_text('{"table:species/1": 1}', encoding="utf-8")
        rows, outputs, identifiers = self.current()
        self.assertEqual(golden.compare(manifest, outputs, identifiers),
                         [f"sprites: expected {manifest['outputs']['sprites']}, got {outputs['sprites']}"])
        self.assertEqual(golden.file_differences(manifest, "sprites", rows),
                         [("missing", "sprites/back/1.png"), ("changed", "sprites/front/2.png"),
                          ("added", "sprites/front/3.png")])

    def test_a_tree_whose_canonical_hash_matches_passes(self):
        manifest = self.recorded()
        (self.tree / "manifest.json").write_text('{"table:species/1": 1}', encoding="utf-8")
        rows, outputs, identifiers = self.current()
        self.assertNotEqual(outputs, manifest["outputs"])
        self.assertEqual(golden.differences(manifest, outputs, identifiers), {})
        self.assertEqual(golden.identifier_only(manifest, outputs), ["sprites"])

    def test_without_the_recorded_listing_the_failure_says_so(self):
        manifest = self.recorded()
        (self.tree / "front/2.png").write_bytes(b"other")
        rows, _, _ = self.current()
        # a listing that does not reproduce its name is not used
        kept = self.store / f"{manifest['outputs']['sprites']}.json"
        kept.write_text(json.dumps(rows), encoding="utf-8")
        found = golden.file_differences(manifest, "sprites", rows)
        self.assertEqual([label for label, _ in found], ["hint"])
        self.assertIn("sprites: no listing of the recorded tree", found[0][1])
        kept.unlink()
        self.assertEqual(golden.file_differences(manifest, "sprites", rows), found)
        self.assertEqual(golden.file_differences(manifest, "sprites", None), [])   # a file output names itself

    def test_the_verification_prints_the_files_of_each_differing_tree(self):
        from types import SimpleNamespace
        recorded = self.recorded()
        inputs = manifest()["inputs"]
        recorded.update(schema=golden.SCHEMA, profile="game", language="xx", inputs=inputs)
        recorded["outputs"]["build/game.gba"] = "0" * 64
        path = self.root / "golden.json"
        golden.write(path, recorded)
        (self.tree / "front/1.png").write_bytes(b"changed")
        rows, outputs, identifiers = self.current()
        outputs["build/game.gba"] = "0" * 64
        args = SimpleNamespace(project=SimpleNamespace(golden=path), record=False, work_dir=None)
        output = io.StringIO()
        with patch.object(golden, "identity", return_value=inputs), \
                patch.object(golden, "generate", return_value=(outputs, identifiers, {"sprites": rows})), \
                redirect_stdout(output):
            self.assertEqual(golden.run(args), 1)
        lines = output.getvalue().splitlines()
        self.assertEqual(lines[0], "verified     failed")
        self.assertTrue(lines[1].startswith("error        sprites: expected "))
        self.assertEqual(lines[2:], ["changed      sprites/front/1.png"])


class BuiltProject:
    """A game project whose default build is a release."""

    def setUp(self):
        from gen3tl.build import release
        self.release = release
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        raw = bytearray(0x10000)
        raw[0xA000:0xA200] = bytes([6]) * 512
        raw[0xC000:0xC280] = b"\xff" * 640
        raw[0xE000:] = b"\xff" * 0x2000
        raw[0x300:0x306] = b"Hello\x00"
        raw[0x200:0x204] = ptr_bytes(0x300)
        base = Rom(raw)
        base.save(self.root / "base.gba")
        self.prof = self.root / "p.toml"
        self.prof.write_text(
            f'[rom]\nname = "test"\npath = "{self.root / "base.gba"}"\nsha1 = "{base.sha1()}"\n'
            '[font_style]\nstyle = "unbound"\ncell_height = 14\n'
            '[[font]]\nname = "main"\nglyphs = 0x1000\nwidths = 0xA000\ncount = 512\n'
            '[layout.box_width]\ndialogue = 208\n')
        (self.root / "projects.toml").write_text(
            '[defaults]\nprofile = "p"\nlanguage = "ru"\n'
            '[[project]]\nprofile = "p"\nlanguage = "ru"\nroot = "."\n'
            'policy = "translation/ru/policy.toml"\n', encoding="utf-8")
        (self.root / "project.toml").write_text(
            Manifest(self.root / "project.toml", "p", base.sha1(), __version__, ("ru",), "ru").text(),
            encoding="utf-8")
        translation = self.root / "translation/ru"
        translation.mkdir(parents=True)
        write_policy(translation, Profile.load(self.prof), "ru")
        write(translation / "po/a.po", [PoEntry("s0000300", "Hello", "A longer message")],
              {"Project-Id-Version": "p", "Language": "ru"})
        (translation / "release.toml").write_text('version = "0.1.0"\n')
        entry = dict(id="s0000300", offset=0x300, length=6, kind="pointer", category="script",
                     mode="field", text="Hello", table=None, index=None, flags=[],
                     refs=[dict(at=0x200, kind="script:loadpointer")])
        inventory = json.dumps({"profile": "p", "rom_sha1": base.sha1(), "entries": [entry]})
        (self.root / "build").mkdir()
        (self.root / "build/inventory.json").write_text(inventory)
        (self.root / "releases").mkdir()
        self.work = self.root / "work"
        (self.work / "build").mkdir(parents=True)
        (self.work / "build/inventory.json").write_text(inventory)

    def run_build(self):
        output = io.StringIO()
        try:
            with redirect_stdout(output), redirect_stderr(output):
                return cli.main(["--project-root", str(self.root), "--profile", str(self.prof),
                                 "build", "rom"])
        finally:
            self.report = output.getvalue()


class ScopedBuildPublishTest(BuiltProject, unittest.TestCase):
    """A scoped build is a workspace run, not a release."""

    def releases(self):
        return {path.relative_to(self.root): path.read_bytes()
                for path in (self.root / "releases").rglob("*") if path.is_file()}

    def test_default_build_publishes_and_scoped_build_does_not(self):
        with patch("gen3tl.build.release.publish_build", wraps=self.release.publish_build) as publish:
            self.assertEqual(self.run_build(), 0)
        self.assertEqual(publish.call_count, 1)
        self.assertTrue((self.root / "build/release-notes.md").is_file())
        self.assertFalse((self.root / "releases/release-notes.md").exists())
        before = self.releases()
        with patch("gen3tl.build.release.publish_build") as publish, build_scope(self.work / "build"):
            self.assertEqual(self.run_build(), 0)
        publish.assert_not_called()
        self.assertEqual(self.releases(), before)
        self.assertTrue((self.work / "build" / "test [RU v0.1.0 by gen3tl].gba").is_file())


class ReleaseBuildNamesTest(BuiltProject, unittest.TestCase):
    """The files of a release build follow from the game, the language and the version."""

    ARCHIVE = "test (Previous versions) [RU by gen3tl].zip"

    def release_version(self, version):
        (self.root / "translation/ru/release.toml").write_text(f'version = "{version}"\n')

    def members(self):
        with zipfile.ZipFile(self.root / "build" / self.ARCHIVE) as archive:
            return sorted(archive.namelist())

    def rows(self):
        return self.release.history(self.root / "build/translation-history.json")["versions"]

    def test_the_version_leaves_the_output_name_at_the_first_major_version(self):
        build = self.root / "build"
        self.assertEqual(self.run_build(), 0)
        for suffix in (".gba", ".ups", ".project.json"):
            self.assertTrue((build / f"test [RU v0.1.0 by gen3tl]{suffix}").is_file(), suffix)
        self.assertEqual(self.members(), ["test [RU by gen3tl]-0.1.0.project.json", "test [RU by gen3tl]-0.1.0.ups"])
        self.release_version("1.0.0")
        self.assertEqual(self.run_build(), 0)
        for suffix in (".gba", ".ups", ".project.json"):
            self.assertTrue((build / f"test [RU by gen3tl]{suffix}").is_file(), suffix)
            self.assertFalse((build / f"test [RU v0.1.0 by gen3tl]{suffix}").exists(), suffix)
        self.assertEqual(json.loads((build / "test [RU by gen3tl].project.json").read_text())["version"], "1.0.0")
        self.assertEqual([name for name in self.members() if name.endswith(".ups")],
                         ["test [RU by gen3tl]-0.1.0.ups", "test [RU by gen3tl]-1.0.0.ups"])
        self.assertEqual([path.name for path in build.glob("*.zip")], [self.ARCHIVE])
        self.assertEqual(self.release.NOTE_HEADING.findall((build / "release-notes.md").read_text()),
                         [("RU by gen3tl", "1.0.0"), ("RU by gen3tl", "0.1.0")])
        self.assertEqual([(row["version"], row["release_label"], row["archive"]) for row in self.rows()],
                         [("0.1.0", "RU by gen3tl", self.ARCHIVE), ("1.0.0", "RU by gen3tl", self.ARCHIVE)])

    def test_an_archive_renamed_by_hand_takes_the_next_builds(self):
        build, base = self.root / "build", (self.root / "base.gba").read_bytes()
        self.assertEqual(self.run_build(), 0)
        data = (build / "test [RU v0.1.0 by gen3tl].gba").read_bytes()
        identity = json.loads((build / "test [RU v0.1.0 by gen3tl].project.json").read_text())
        for path in build.iterdir():
            if path.name != "inventory.json":
                path.unlink()
        # the same build under names of its own
        self.release.publish_build(data, build / "Test [Translated].gba", "0.1.0", identity, "", "Test [Translation]",
                                   base, [], "Test (Earlier versions).zip", "Translation")
        (build / "Test (Earlier versions).zip").rename(build / self.ARCHIVE)
        catalog = self.root / "translation/ru/po/a.po"
        kept = catalog.read_bytes()
        write(catalog, [PoEntry("s0000300", "Hello", "Another message")], {"Project-Id-Version": "p", "Language": "ru"})
        self.assertEqual(self.run_build(), 1)
        self.assertIn("translation 0.1.0 already identifies different bytes; raise the version", self.report)
        self.assertFalse((build / "test [RU v0.1.0 by gen3tl].gba").exists())
        catalog.write_bytes(kept)
        self.assertEqual(self.run_build(), 0)
        self.assertEqual((build / "test [RU v0.1.0 by gen3tl].gba").read_bytes(), data)
        self.assertEqual([name for name in self.members() if name.endswith(".ups")],
                         ["Test [Translation]-0.1.0.ups", "test [RU by gen3tl]-0.1.0.ups"])
        with zipfile.ZipFile(build / self.ARCHIVE) as archive:
            for name in ("Test [Translation]-0.1.0.ups", "test [RU by gen3tl]-0.1.0.ups"):
                self.assertEqual(ups.apply(base, archive.read(name)), data)
        self.assertEqual([path.name for path in build.glob("*.zip")], [self.ARCHIVE])
        self.assertEqual([(row["version"], row["release_label"], row["archive"], row["delta"]) for row in self.rows()],
                         [("0.1.0", "RU by gen3tl", self.ARCHIVE, "test [RU by gen3tl]-0.1.0.ups")])
        self.release_version("0.1.1")
        self.assertEqual(self.run_build(), 0)
        self.assertEqual([name for name in self.members() if name.endswith(".ups")],
                         ["Test [Translation]-0.1.0.ups", "test [RU by gen3tl]-0.1.0.ups",
                          "test [RU by gen3tl]-0.1.1.ups"])
        self.assertEqual(sorted(path.name for path in build.glob("test [RU *")),
                         [f"test [RU v0.1.1 by gen3tl]{suffix}" for suffix in (".gba", ".project.json", ".ups")])

    def test_files_the_archive_cannot_reproduce_stay(self):
        build = self.root / "build"
        self.assertEqual(self.run_build(), 0)
        stale = build / "test [RU v0.0.9 by gen3tl].gba"
        stale.write_bytes((build / "test [RU v0.1.0 by gen3tl].gba").read_bytes())
        stale.with_name(stale.stem + ".project.json").write_text('{"version": "0.0.9"}\n')
        other = build / "test [FR v0.1.0 by gen3tl].gba"
        other.write_bytes(b"another language")
        self.release_version("0.1.1")
        self.assertEqual(self.run_build(), 0)
        self.assertFalse((build / "test [RU v0.1.0 by gen3tl].gba").exists())
        self.assertTrue(stale.is_file())
        self.assertTrue(other.is_file())
        self.assertEqual(self.release.superseded_outputs(build, "test", "ru", "test [RU v0.1.1 by gen3tl]"),
                         ["test [RU v0.0.9 by gen3tl]"])
