import io
import json
import tempfile
import tomllib
import unittest
from contextlib import chdir, redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from project_fixture import CATALOG_STAGE, write_policy
import gen3tl
from gen3tl.build.graphics import graphics_text, inventory_rows, preview_views
from gen3tl.core.graphics import write_preview_views
from gen3tl.catalog import guidance, source
from gen3tl.catalog.catalog import load_catalog
from gen3tl.catalog.po import PoEntry, file_label, write
from gen3tl.catalog.layout import init, without_notes
from gen3tl.cli import common
from gen3tl.cli.main import main, project_defaults
from gen3tl.profile.profile import Profile
from gen3tl.project import Manifest, ProjectError, Registry

SHA1 = "0" * 40

ENGLISH = "{PLAYER} found an item!"

NOTE = {"id": "s1", "source_sha256": source.digest(ENGLISH), "text": "PLAYER names the finder.",
        "evidence": "The acquisition script fills PLAYER."}

COLUMNS = ("id", "screen", "source_text", "category", "reason", "rom_offset", "compressed", "size_bytes",
           "tiles", "palette", "pointer_at", "seen_on_screen", "image")

def profile_text(notes=True, graphics=True) -> str:
    text = f'[rom]\nname = "Game"\nsha1 = "{SHA1}"\n'
    if graphics:
        text += '\n[graphics]\ninventory = "gfx/inventory.tsv"\nprevious = 1\npreviews = "gfx/previews.json"\n'
    if notes:
        text += "\n# about the note\n[[translator_notes]]\n" + "".join(
            f"{k} = {json.dumps(v)}\n" for k, v in NOTE.items()) + "\n[support]\nstage = \"translation\"\n"
    return text

class LayoutTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.profile_path = self.root / "game.toml"
        self.profile_path.write_text(profile_text(), encoding="utf-8")
        (self.root / "gfx").mkdir()
        row = dict.fromkeys(COLUMNS, "")
        row.update(id="G01", source_text="PRESS START", category="translate", reason="title")
        (self.root / "gfx/inventory.tsv").write_text("\t".join(COLUMNS) + "\n" + "\t".join(row.values()) + "\n",
                                                     encoding="utf-8")
        views = {"version": 1, "views": {"G01": [{"label": "Title", "map": {"offset": 16, "size": 64, "width": 32},
                                                  "cells": [[0, 0, 1, 0], [8, 0, 2, 1]], "palettes": ["raw:0x10"],
                                                  "transparent": True}]}}
        (self.root / "gfx/previews.json").write_text(json.dumps(views), encoding="utf-8")
        self.views = views
        other = self.root / "other"
        (other / "translation/po").mkdir(parents=True)
        other_profile = self.root / "other.toml"
        other_profile.write_text(f'[rom]\nsha1 = "{"1" * 40}"\n', encoding="utf-8")
        write_policy(other / "translation", Profile.load(other_profile), "ru")
        (self.root / "projects.toml").write_text(
            '[defaults]\nprofile = "other"\nlanguage = "ru"\n\n[[project]]\nprofile = "other"\nlanguage = "ru"\n'
            'root = "other"\npolicy = "translation/policy.toml"\n', encoding="utf-8")
        self.game = self.root / "game"

    def add_language(self, code="ru", notes_in_profile=False):
        folder = self.game / "translation" / code
        write(folder / "po/a.po", [PoEntry("s1", ENGLISH), PoEntry("s2", "Other")],
              {"Project-Id-Version": "game", "Language": code})
        write_policy(folder, Profile.load(self.profile_path), code)
        with (self.root / "projects.toml").open("a", encoding="utf-8") as stream:
            stream.write(f'\n[[project]]\nprofile = "game"\nlanguage = "{code}"\nroot = "game"\n'
                         f'policy = "translation/{code}/policy.toml"\nstage = "translation"\n')
        manifest = Manifest.read(self.game / "project.toml")
        (self.game / "project.toml").write_text(
            Manifest(manifest.path, manifest.profile, manifest.rom_sha1, manifest.toolkit,
                     manifest.languages + (code,), manifest.default_language or code).text(), encoding="utf-8")
        return Registry.load(self.root).resolve("game", code)

    def laid(self):
        return init(Profile.load(self.profile_path), self.game, Registry.load(self.root))

    def test_init_lays_out_a_translation_stage_profile(self):
        self.game.mkdir()
        (self.game / "gfx").symlink_to(self.root / "gfx")
        laid = self.laid()
        manifest = Manifest.read(self.game / "project.toml")
        self.assertEqual((manifest.profile, manifest.rom_sha1, manifest.languages), ("game", SHA1, ()))
        for folder in ("translation", "source/snapshot", "source/graphics/views", "data/pokedex", "releases", "site"):
            self.assertTrue((self.game / folder).is_dir(), folder)
        self.assertEqual(guidance.read_notes(self.game / "source/notes.toml"), [NOTE])
        self.assertEqual((laid.notes, laid.graphics), (1, 1))
        moved = tomllib.loads(self.profile_path.read_text(encoding="utf-8"))
        self.assertNotIn("translator_notes", moved)
        self.assertEqual(moved["graphics"], {"previous": 1})
        self.assertNotIn("about the note", self.profile_path.read_text(encoding="utf-8"))
        graphics = self.game / "source/graphics.toml"
        self.assertEqual(inventory_rows(graphics), inventory_rows(self.root / "gfx/inventory.tsv"))
        views = self.game / "source" / "graphics" / "views"
        self.assertEqual(preview_views(views), self.views["views"])
        self.assertNotIn("views", tomllib.loads(graphics.read_text(encoding="utf-8")))
        self.assertEqual((views / "G01.json").read_text(encoding="utf-8").count("\n"), 1)
        again = self.laid()   # kept, not rewritten
        self.assertEqual(again.written, [])
        self.assertEqual(guidance.read_notes(self.game / "source/notes.toml"), [NOTE])

    def test_effective_profile_selects_external_views_and_legacy_fallback(self):
        self.profile_path.write_text(profile_text(notes=False, graphics=False), encoding="utf-8")
        self.laid()
        project = self.add_language()
        section = project.effective(Profile.load(self.profile_path))["graphics"]
        self.assertEqual(section["previews"], project.graphics_views.relative_to(self.game).as_posix())
        project.graphics_views.rmdir()
        section = project.effective(Profile.load(self.profile_path))["graphics"]
        self.assertEqual(section["previews"], project.graphics.relative_to(self.game).as_posix())

    def test_init_moves_notes_and_graphics_from_ordered_fragments(self):
        directory = self.root / "profiles" / "game"
        overlay = directory / "overlay"
        overlay.mkdir(parents=True)
        generated = directory / "profile.toml"
        generated.write_text('[discovery]\nschema=1\n')
        order = overlay / "order.txt"
        order.write_text("identity.toml\ngraphics.toml\nnotes.toml\n")
        identity, rest = profile_text().split('[graphics]', 1)
        graphics, notes = rest.split('# about the note', 1)
        (overlay / "identity.toml").write_text(identity)
        (overlay / "graphics.toml").write_text('[graphics]' + graphics)
        (overlay / "notes.toml").write_text('# about the note' + notes)
        before = {path: path.read_bytes() for path in (generated, order, overlay / "identity.toml")}
        self.profile_path = directory
        self.game.mkdir()
        (self.game / "gfx").symlink_to(self.root / "gfx")
        laid = self.laid()
        self.assertEqual((laid.notes, laid.graphics), (1, 1))
        self.assertEqual(guidance.read_notes(self.game / "source" / "notes.toml"), [NOTE])
        self.assertNotIn("translator_notes", Profile.load(directory).data)
        self.assertEqual(Profile.load(directory)["graphics"], {"previous": 1})
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertEqual(set(laid.profile_files), {overlay / "graphics.toml", overlay / "notes.toml"})

    def test_init_reads_a_view_directory_and_preserves_note_containers(self):
        from gen3tl.core.graphics import graphics_document
        rows = [{"id": "G01", "notes_schema": "3.0.0", "notes": []}]
        inventory = self.root / "gfx" / "inventory.toml"
        inventory.write_text(graphics_text(rows), encoding="utf-8")
        write_preview_views(self.root / "gfx" / "views", self.views["views"])
        text = profile_text().replace("gfx/inventory.tsv", "gfx/inventory.toml")
        self.profile_path.write_text(text.replace("gfx/previews.json", "gfx/views"), encoding="utf-8")
        self.game.mkdir()
        (self.game / "gfx").symlink_to(self.root / "gfx")
        self.laid()
        self.assertEqual(graphics_document(self.game / "source" / "graphics.toml")["graphic"], rows)
        self.assertEqual(preview_views(self.game / "source" / "graphics" / "views"), self.views["views"])

    def test_init_refuses_other_stages_and_identities(self):
        self.profile_path.write_text(profile_text(False, False) + '\n[support]\nstage = "catalog"\n',
                                     encoding="utf-8")
        with self.assertRaisesRegex(ProjectError, "translation-stage"):
            self.laid()
        self.profile_path.write_text(profile_text(False, False), encoding="utf-8")
        self.game.mkdir()
        (self.game / "project.toml").write_text(Manifest(self.game / "project.toml", "game", "2" * 40, "1.0.0",
                                                         (), "").text(), encoding="utf-8")
        with self.assertRaisesRegex(ProjectError, "belongs to"):
            self.laid()

    def test_registry_reads_language_folders_and_shared_source(self):
        self.profile_path.write_text(profile_text(notes=False, graphics=False), encoding="utf-8")
        self.laid()
        guidance.write_notes(self.game / "source/notes.toml", [NOTE])
        project = self.add_language("ru")
        self.assertTrue(project.game)
        self.assertEqual(project.translation, self.game / "translation/ru")
        self.assertEqual(project.catalog, self.game / "translation/ru/po")
        self.assertEqual(project.build, self.game / "build")
        self.assertEqual(project.snapshot, self.game / "source/snapshot/ru.snapshot.json")
        self.assertEqual(project.golden, self.game / "translation/ru/golden.json")
        self.assertEqual(project.dataset, self.game / "data/pokedex")
        self.assertEqual((project.releases, project.release_notes, project.site),
                         (self.game / "releases", self.game / "build/release-notes.md", self.game / "site"))
        for cmd, field, path in (("build patcher-data", "out", project.releases), ("build patch", "notes", project.release_notes),
                                 ("web assemble", "out", project.site)):
            args = SimpleNamespace(cmd=cmd, **{field: None})
            project_defaults(args, project)
            self.assertEqual(getattr(args, field), str(path), cmd)
        second = self.add_language("de")
        registry = Registry.load(self.root)
        registry.protect(project, [project.build / "inventory.json"])   # the languages share build/
        with self.assertRaisesRegex(ProjectError, "belongs to"):
            registry.protect(project, [second.catalog / "a.po"])
        selected = project.effective(Profile.load(self.profile_path))
        self.assertEqual(selected.notes_path, self.game / "source/notes.toml")
        self.assertEqual(selected["graphics"]["inventory"], "source/graphics.toml")
        catalog = load_catalog(project.catalog, selected)
        self.assertEqual(guidance.entry_notes(selected, catalog),
                         {"s1": [{"text": NOTE["text"], "evidence": NOTE["evidence"]}]})

    def test_registry_rejects_inconsistent_game_projects(self):
        self.profile_path.write_text(profile_text(notes=False, graphics=False), encoding="utf-8")
        self.laid()
        self.add_language("ru")
        manifest = (self.game / "project.toml").read_text(encoding="utf-8")
        (self.game / "project.toml").write_text(manifest.replace('["ru"]', '["ru", "fr"]'), encoding="utf-8")
        with self.assertRaisesRegex(ProjectError, "languages differ"):
            Registry.load(self.root)
        (self.game / "project.toml").write_text(manifest.replace('profile = "game"', 'profile = "else"'),
                                                encoding="utf-8")
        with self.assertRaisesRegex(ProjectError, "not a language of"):
            Registry.load(self.root)
        (self.game / "project.toml").write_text(manifest)
        self.profile_path.write_text(profile_text(graphics=False), encoding="utf-8")
        project = Registry.load(self.root).resolve("game", "ru")
        with self.assertRaisesRegex(ProjectError, "move the translator notes"):
            project.effective(Profile.load(self.profile_path))

    def test_project_resolves_site_labels_without_mutating_profile_facts(self):
        self.profile_path.write_text(profile_text(notes=False, graphics=False), encoding="utf-8")
        self.laid()
        project = self.add_language()
        profile = Profile.load(self.profile_path)
        bindings = {"strings": {"Name": "s0000001"}, "symbols": {}}
        profile.data["pokedex"] = {"labels": bindings}
        path = project.pokedex_labels
        self.assertEqual(path, project.source / "pokedex-labels.pot")
        write(path, [PoEntry("Label", "Label", extracted=["Site label."])], {})
        resolved = project.effective(profile)["pokedex"]["labels"]
        self.assertEqual(resolved, dict(bindings, unmapped={"Label": "Site label."}))
        self.assertNotIn("unmapped", profile["pokedex"]["labels"])
        write(path, [PoEntry("Label", "Label")], {})
        with self.assertRaisesRegex(ValueError, "has no reason"):
            project.effective(profile)

    def test_golden_inputs_follow_the_layout(self):
        from gen3tl.verify.golden import input_paths, output_paths, snapshot_name, sprites_name
        self.profile_path.write_text(profile_text(notes=False, graphics=False), encoding="utf-8")
        self.laid()
        guidance.write_notes(self.game / "source/notes.toml", [NOTE])
        project = self.add_language("ru")
        view = write_preview_views(project.graphics_views, {"G01": [{"width": 8}]})[0]
        write(project.pokedex_labels, [PoEntry("Label", "Label", extracted=["Site label."])], {})
        for name in ("release.toml", "glossary.tsv", "rules.toml"):
            (project.translation / name).write_text('version = "1.0.0"\n' if name == "release.toml" else "",
                                                    encoding="utf-8")
        paths = input_paths(SimpleNamespace(project=project, profile=str(self.profile_path), lang="ru"))
        for path in (project.policy, project.release, project.catalog / "a.po", project.glossary,
                     project.notes, project.graphics, project.pokedex_labels, view, project.translation / "rules.toml"):
            self.assertIn(path.resolve(), paths)
        self.assertEqual(snapshot_name(project), "source/snapshot/ru.snapshot.json")
        self.assertEqual(sprites_name(project), "data/pokedex/sprites")
        work = self.root / "golden-work"
        part = work / "build/editor" / project.profile / "shared" / ("0" * 64 + ".json")   # a game part
        part.parent.mkdir(parents=True)
        part.write_text("{}", encoding="utf-8")
        outputs = output_paths(SimpleNamespace(project=project, default_build="fixture.gba"), work)
        for path in (project.snapshot, project.snapshot.with_name("shared.json")):
            relative = path.relative_to(project.root)
            self.assertEqual(outputs[relative.as_posix()], work / relative)
        self.assertEqual(outputs[f"build/editor/{project.profile}/shared"], part.parent)   # a tree

    def test_wording_defaults_select_the_canonical_container(self):
        from gen3tl.cli.main import project_defaults
        self.profile_path.write_text(profile_text(notes=False, graphics=False), encoding="utf-8")
        self.laid()
        project = self.add_language()
        args = SimpleNamespace(cmd="web data", rules=None)
        project_defaults(args, project)
        self.assertEqual(Path(args.rules), project.translation / "rules.tsv")
        (project.translation / "rules.toml").write_text('schema = "1.0.0"\nrule = []\n')
        args.rules = None
        project_defaults(args, project)
        self.assertEqual(Path(args.rules), project.translation / "rules.toml")
        args.rules = "chosen.tsv"
        project_defaults(args, project)
        self.assertEqual(args.rules, "chosen.tsv")

class PinTest(unittest.TestCase):
    """A game project runs with the toolkit its project.toml pins, at the version it names."""

    VERSION = "1.4.2"   # the running toolkit

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.profile_path = self.root / "game.toml"
        self.profile_path.write_text(profile_text(notes=False, graphics=False), encoding="utf-8")
        (self.root / "translation/ru").mkdir(parents=True)
        write_policy(self.root / "translation/ru", Profile.load(self.profile_path), "ru")
        (self.root / "projects.toml").write_text(
            '[defaults]\nprofile = "game"\nlanguage = "ru"\n\n[[project]]\nprofile = "game"\nlanguage = "ru"\n'
            'root = "."\npolicy = "translation/ru/policy.toml"\n',
            encoding="utf-8")
        for name, value in (("__version__", self.VERSION), ("INSTALLED", True)):   # an installed release
            hook = patch.object(gen3tl, name, value)
            hook.start()
            self.addCleanup(hook.stop)

    def run_cli(self, version, *argv, profile=None):
        manifest = self.root / "project.toml"
        manifest.write_text(Manifest(manifest, "game", SHA1, version, ("ru",), "ru").text(), encoding="utf-8")
        handler = Mock(return_value=0)
        err = io.StringIO()
        with patch.dict(common.COMMANDS, {"rom identify": handler}), redirect_stderr(err), redirect_stdout(io.StringIO()):
            try:
                code = main(["--project-root", str(self.root), "--profile", str(profile or self.profile_path), *argv])
            except SystemExit as end:
                code = end.code
        return code, handler.call_count, err.getvalue()

    def pin(self, version, reason, root=None):
        manifest = (root or self.root) / "project.toml"
        message = f"{manifest}: pins toolkit {version}, running {self.VERSION} ({reason})"
        output = f"pin          {message}\n"
        if reason == "--ignore-pin overrides":
            output += f"error        {message}\nresult       failed: {message}\n"
        return output

    def test_the_pinned_version_runs(self):
        self.assertEqual(self.run_cli(self.VERSION, "rom", "identify"), (0, 1, ""))

    def test_a_newer_patch_runs_with_a_pin_line(self):
        for version in ("1.4.0", "1.4.1"):
            with self.subTest(version=version):
                self.assertEqual(self.run_cli(version, "rom", "identify"), (0, 1, self.pin(version, "newer patch")))

    def test_another_major_or_minor_version_or_an_older_patch_stops(self):
        for version in ("1.3.9", "1.5.0", "0.4.2", "2.4.2", "1.4.3"):
            with self.subTest(version=version):
                self.assertEqual(self.run_cli(version, "rom", "identify"),
                                 (2, 0, self.pin(version, "--ignore-pin overrides")))

    def test_a_checkout_runs_another_version_with_a_pin_line(self):
        with patch.object(gen3tl, "INSTALLED", False):   # a checkout or an editable install
            for version, reason in (("1.3.9", "checkout"), ("1.4.3", "checkout"), ("1.4.1", "newer patch")):
                with self.subTest(version=version):
                    self.assertEqual(self.run_cli(version, "rom", "identify"), (0, 1, self.pin(version, reason)))

    def test_the_override_runs_another_version(self):
        for argv in (("--ignore-pin", "rom", "identify"), ("rom", "identify", "--ignore-pin")):
            with self.subTest(argv=argv):
                self.assertEqual(self.run_cli("0.0.1", *argv), (0, 1, ""))

    def test_only_the_selected_project_is_checked(self):
        other = self.root / "other"
        profile = self.root / "other.toml"
        profile.write_text(profile_text(notes=False, graphics=False), encoding="utf-8")
        (other / "translation/ru").mkdir(parents=True)
        write_policy(other / "translation/ru", Profile.load(profile), "ru")
        (other / "project.toml").write_text(Manifest(other / "project.toml", "other", SHA1, "0.0.1", ("ru",),
                                                     "ru").text(), encoding="utf-8")
        with (self.root / "projects.toml").open("a", encoding="utf-8") as stream:
            stream.write('\n[[project]]\nprofile = "other"\nlanguage = "ru"\nroot = "other"\n'
                         'policy = "translation/ru/policy.toml"\n')
        self.assertEqual(self.run_cli(self.VERSION, "rom", "identify"), (0, 1, ""))
        self.assertEqual(self.run_cli(self.VERSION, "rom", "identify", profile=profile),
                         (2, 0, self.pin("0.0.1", "--ignore-pin overrides", other)))

    def test_the_fixture_registry_hides_a_project_above_the_clone(self):
        manifest = self.root / "project.toml"
        manifest.write_text(Manifest(manifest, "game", SHA1, "0.0.1", ("ru",), "ru").text(), encoding="utf-8")
        clone = self.root / "gen3tl"
        clone.mkdir()
        handler = Mock(return_value=0)
        argv = ["--profile", str(self.profile_path), "rom", "identify"]
        with patch.dict(common.COMMANDS, {"rom identify": handler}), chdir(clone), redirect_stdout(io.StringIO()), \
                redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit) as end:   # the registry found upward
                main(argv)
            self.assertEqual((end.exception.code, handler.call_count), (2, 0))
            self.assertEqual(main(["--project-root", str(CATALOG_STAGE), *argv]), 0)
        self.assertIn("pins toolkit 0.0.1", err.getvalue())
        self.assertEqual(handler.call_count, 1)

class DocumentTest(unittest.TestCase):
    def test_file_labels_do_not_depend_on_the_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for catalog in (root / "translation/po", root / "translation/ru/po"):
                self.assertEqual(file_label(catalog / "maps/route.po", catalog), "translation/po/maps/route.po")

    def test_notes_round_trip_with_escapes(self):
        rows = [dict(NOTE, text='Ünïcode — "hi"\\nthen\ttab')]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notes.toml"
            guidance.write_notes(path, rows)
            self.assertEqual(guidance.read_notes(path), rows)

    def test_graphics_document_keeps_rows_and_view_order(self):
        rows = [{"id": "G02", "category": "keep", "reason": "logo"}, {"id": "G01", "category": "translate",
                                                                      "reason": ""}]
        previews = {"version": 1, "views": {"G02": [{"width": 8, "map": {"offset": 1}, "background": "black"}],
                                            "G01": [{"cells": [[0, 0, 1, 0]]}, {"rects": [[0, 0, 8, 8, 0, 0]]}]}}
        manifest = Manifest(Path("project.toml"), "game", SHA1, "1.0.0", (), "").text()
        self.assertTrue(manifest.startswith("# Game project: base ROM profile, pinned toolkit, languages.\n\nschema = 1\n"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "graphics.toml"
            text = graphics_text(rows, previews)
            self.assertTrue(text.startswith("# Graphics with text: the inventory rows and their preview layouts.\n"))
            self.assertNotIn("docs/", text)
            path.write_text(text, encoding="utf-8")
            self.assertEqual(json.dumps(inventory_rows(path)), json.dumps(rows))
            self.assertEqual(json.dumps(preview_views(path)), json.dumps(previews["views"]))

    def test_without_notes_keeps_neighbouring_comments(self):
        text = ('a = 1\n\n[[translator_notes]]\nid = "x"\n\n# next table\n[[b]]\nc = 2\n\n'
                '[[translator_notes]]\nid = "y"\n')
        self.assertEqual(without_notes(text), 'a = 1\n\n# next table\n[[b]]\nc = 2\n')
