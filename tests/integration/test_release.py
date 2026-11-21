import json
import hashlib
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from project_fixture import write_policy
from gen3tl.check import qa
from gen3tl.build import release
from gen3tl import __version__
from gen3tl.__main__ import main
from gen3tl.lang.langpack import LangPack
from gen3tl.check.layout import Limit, Measure, check, layout
from gen3tl.catalog.po import PoEntry, write
from gen3tl.profile.profile import Profile

PACK = LangPack.load("ru", "unbound")

CM = PACK.charmap()

M6 = Measure(lambda b: 6, {})   # every glyph 6 px

def entry(sid, text, category="script", ref="script:loadpointer"):
    return {"id": sid, "offset": int(sid[1:], 16), "length": len(text) + 1, "kind": "pointer",
            "category": category, "mode": "field", "text": text, "table": None, "index": None,
            "refs": [{"at": 0x10, "kind": ref}], "flags": []}

def release_file(root: Path, version: str) -> Path:
    path = root / "translation" / "release.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'version = "{version}"\n', encoding="utf-8")
    return path

class ReleaseNamesTest(unittest.TestCase):
    """The names of a release follow from the game, the language and the version."""

    def test_a_version_below_one_shows_in_the_output_name(self):
        for version in ("0.0.0", "0.1.0", "0.2.18", "0.10.3"):
            with self.subTest(version=version):
                self.assertEqual(release.release_names("Game 1.0", "de", version),
                                 (f"Game 1.0 [DE v{version} by gen3tl]",
                                  "Game 1.0 (Previous versions) [DE by gen3tl].zip", "DE by gen3tl"))

    def test_the_first_major_version_and_later_ones_name_the_language_alone(self):
        for version in ("1.0.0", "1.0.7", "2.3.4", "10.0.0"):
            with self.subTest(version=version):
                names = release.release_names("Game 1.0", "ru", version)
                self.assertEqual(names.output, "Game 1.0 [RU by gen3tl]")
                self.assertEqual(names.archive, "Game 1.0 (Previous versions) [RU by gen3tl].zip")
                self.assertEqual(names.label, "RU by gen3tl")

    def test_a_code_with_a_region_is_marked_whole(self):
        self.assertEqual(release.release_names("Game", "pt-BR", "0.3.1"),
                         ("Game [PT-BR v0.3.1 by gen3tl]", "Game (Previous versions) [PT-BR by gen3tl].zip",
                          "PT-BR by gen3tl"))
        self.assertEqual(release.release_names("Game", "pt-BR", "1.0.0").output, "Game [PT-BR by gen3tl]")

    def test_an_invalid_version_names_nothing(self):
        for version in ("0.1", "v0.1.0", "01.0.0", "", None):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "invalid translation version"):
                release.release_names("Game", "ru", version)

    def test_the_title_is_the_rom_name_else_the_profile_name(self):
        self.assertEqual(release.game_title(Profile({"rom": {"name": "Game 1.0"}}, Path("game-1.0.toml"))), "Game 1.0")
        self.assertEqual(release.game_title(Profile({}, Path("game-1.0.toml"))), "game-1.0")
        self.assertEqual(release.game_title(Profile({"rom": {"name": "Game/1.0"}}, Path("game-1.0.toml"))), "game-1.0")

    def test_a_build_takes_the_version_of_its_release_file(self):
        profile = Profile({"rom": {"name": "Game 1.0"}}, Path("game-1.0.toml"))
        with tempfile.TemporaryDirectory() as tmp:
            path = release_file(Path(tmp), "0.1.0")
            self.assertEqual(release.build_names(profile, "fr", path).output, "Game 1.0 [FR v0.1.0 by gen3tl]")
            release_file(Path(tmp), "1.0.0")
            self.assertEqual(release.build_names(profile, "fr", path),
                             ("Game 1.0 [FR by gen3tl]", "Game 1.0 (Previous versions) [FR by gen3tl].zip",
                              "FR by gen3tl"))

    def test_a_workspace_without_a_release_file_keeps_its_own_names(self):
        profile = Profile({"rom": {"name": "Game 1.0"}}, Path("game-1.0.toml"))
        with tempfile.TemporaryDirectory() as tmp:
            for path in (None, Path(tmp) / "translation/release.toml"):
                with self.subTest(path=path):
                    self.assertEqual(release.build_names(profile, "fr", path),
                                     ("game-1.0-fr", "Game 1.0-fr-archive.zip", "fr"))
        self.assertEqual(release.build_names(profile, None, None).output, "game-1.0")


class ReleaseFileTest(unittest.TestCase):
    def test_a_release_file_with_a_name_is_refused(self):
        reason = "the release names follow from the game, the language and the version"
        with tempfile.TemporaryDirectory() as tmp:
            path = release_file(Path(tmp), "0.1.0")
            for key, value in (("output_name", "Game [French translation]"),
                               ("archive_name", "Game (Previous versions).zip"), ("release_label", "French")):
                with self.subTest(key=key):
                    path.write_text(f'version = "0.1.0"\n{key} = "{value}"\n', encoding="utf-8")
                    with self.assertRaises(ValueError) as refused:
                        release.load(path)
                    self.assertEqual(str(refused.exception), f"{path}: remove {key}; {reason}")
                    with self.assertRaisesRegex(ValueError, f"remove {key}; "):
                        release.build_names(Profile({}, Path("game.toml")), "fr", path)
            path.write_text('release_label = "French"\nversion = "0.1.0"\noutput_name = "Game"\n', encoding="utf-8")
            with self.assertRaises(ValueError) as refused:
                release.load(path)
            self.assertEqual(str(refused.exception), f"{path}: remove output_name, release_label; {reason}")

    def test_load_and_expand(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(release.load(release_file(Path(tmp), "0.1.0")), "0.1.0")
            for bad in ("0.1", "01.2.3", "v0.1.0", "0.1.0-rc1"):
                with self.assertRaises(ValueError):
                    release.load(release_file(Path(tmp), bad))
        self.assertEqual(release.expand("Русская версия @VERSION@", "0.1.0"), "Русская версия 0.1.0")
        self.assertEqual(release.expand("Без версии", "0.1.0"), "Без версии")
        self.assertEqual(release.expand("@VERSION@", None), "@VERSION@")   # no version: left as written


class BuildHistoryTest(unittest.TestCase):
    def identity(self, data):
        return {"profile": "game", "language": "ru", "rom_sha1": "base",
                "built_sha1": hashlib.sha1(data).hexdigest()}

    def test_versions_are_retained_without_touching_saves(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "game-ru.gba"
            save = latest.with_suffix(".sav")
            save.write_bytes(b"existing gameplay")
            first = release.preserve_build(b"first", latest, "0.1.0", self.identity(b"first"), "Initial")
            second = release.preserve_build(b"second", latest, "0.1.1", self.identity(b"second"), "Fix")
            self.assertEqual((first.name, first.read_bytes()), ("game-ru-0.1.0.gba", b"first"))
            self.assertEqual(second.read_bytes(), latest.read_bytes())
            self.assertEqual(save.read_bytes(), b"existing gameplay")
            rows = release.history(Path(tmp) / "translation-history.json")["versions"]
            self.assertEqual([r["gameplay"] for r in rows], ["not recorded", "not recorded"])
            self.assertEqual(json.loads(latest.with_suffix(".project.json").read_text())["version"], "0.1.1")

    def test_version_collision_preserves_latest_and_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "game-ru.gba"
            ledger = Path(tmp) / "translation-history.json"
            first = release.preserve_build(b"first", latest, "0.1.0", self.identity(b"first"))
            before = ledger.read_bytes()
            with self.assertRaisesRegex(ValueError, "different bytes"):
                release.preserve_build(b"changed", latest, "0.1.0", self.identity(b"changed"))
            self.assertEqual((first.read_bytes(), latest.read_bytes()), (b"first", b"first"))
            self.assertEqual(ledger.read_bytes(), before)
            ledger.unlink()
            with self.assertRaisesRegex(ValueError, "different bytes"):
                release.preserve_build(b"changed", latest, "0.1.0", self.identity(b"changed"))
            self.assertEqual(latest.read_bytes(), b"first")

    def test_repeat_build_preserves_explicit_playtest_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "game-ru.gba"
            ledger = Path(tmp) / "translation-history.json"
            release.preserve_build(b"first", latest, "0.1.0", self.identity(b"first"))
            release.record_playtest(ledger, "game", "ru", "0.1.0", "launched", "Opened in emulator")
            release.record_playtest(ledger, "game", "ru", "0.1.0", "issues reported", "Menu clipping")
            release.preserve_build(b"first", latest, "0.1.0", self.identity(b"first"))
            rows = release.history(ledger)["versions"]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["gameplay"], "issues reported")
            self.assertEqual([e["status"] for e in rows[0]["playtests"]], ["launched", "issues reported"])
            with self.assertRaises(ValueError):
                release.record_playtest(ledger, "other", "ru", "0.1.0", "checked", "Battle")
            with self.assertRaises(ValueError):
                release.record_playtest(ledger, "game", "ru", "0.1.0", "checked", "")

    def build(self, latest, version, name=None, keep=release.KEEP):
        data = f"build {version}".encode()
        return release.preserve_build(data, latest, version, self.identity(data), name=name, keep=keep)

    def test_newest_two_stay_and_older_versions_share_one_archive(self):
        """Names follow the ROM; versions sort numerically."""
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "game-ru.gba"
            for version in ("0.1.8", "0.1.9", "0.1.10", "0.1.11"):
                self.build(latest, version, "Game 1.0-ru")
            self.assertEqual(sorted(p.name for p in Path(tmp).glob("*.gba")),
                             ["Game 1.0-ru-0.1.10.gba", "Game 1.0-ru-0.1.11.gba", "game-ru.gba"])
            with zipfile.ZipFile(Path(tmp) / "Game 1.0-ru-archive.zip") as zf:
                self.assertEqual(sorted(zf.namelist()),
                                 ["Game 1.0-ru-0.1.8.gba", "Game 1.0-ru-0.1.8.project.json",
                                  "Game 1.0-ru-0.1.9.gba", "Game 1.0-ru-0.1.9.project.json"])
                self.assertEqual(zf.read("Game 1.0-ru-0.1.9.gba"), b"build 0.1.9")
            rows = {r["version"]: r for r in release.history(Path(tmp) / "translation-history.json")["versions"]}
            self.assertEqual(rows["0.1.8"]["archive"], "Game 1.0-ru-archive.zip")
            self.assertEqual(rows["0.1.8"]["file"], "Game 1.0-ru-0.1.8.gba")
            self.assertNotIn("archive", rows["0.1.11"])
            self.assertEqual(latest.read_bytes(), b"build 0.1.11")

    def test_earlier_names_take_the_rom_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "game-ru.gba"
            for version in ("0.1.0", "0.1.1", "0.1.2"):
                self.build(latest, version, keep=5)
            latest.with_name("game-ru-0.1.1.report.md").write_text("report")
            self.build(latest, "0.1.3", "Game 1.0-ru")
            names = sorted(p.name for p in Path(tmp).iterdir())
            self.assertNotIn("game-ru-0.1.2.gba", names)
            self.assertIn("Game 1.0-ru-0.1.2.gba", names)
            self.assertIn("Game 1.0-ru-0.1.2.project.json", names)
            with zipfile.ZipFile(Path(tmp) / "Game 1.0-ru-archive.zip") as zf:
                self.assertEqual(zf.read("Game 1.0-ru-0.1.1.report.md"), b"report")
                self.assertEqual(zf.read("Game 1.0-ru-0.1.0.gba"), b"build 0.1.0")
            rows = {r["version"]: r["file"] for r in release.history(Path(tmp) / "translation-history.json")["versions"]}
            self.assertEqual(rows["0.1.2"], "Game 1.0-ru-0.1.2.gba")

    def test_archived_builds_are_deltas_from_the_base(self):
        """An archived ROM is a UPS delta that reproduces it exactly."""
        from gen3tl.core import ups
        base = bytes(range(256)) * 64
        def rom(version):
            return base[:100] + version.encode() + base[100 + len(version):]
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "game-ru.gba"
            archive = Path(tmp) / "Game 1.0-ru-archive.zip"
            release.preserve_build(rom("0.1.0"), latest, "0.1.0", self.identity(rom("0.1.0")), name="Game 1.0-ru")
            with zipfile.ZipFile(archive, "w") as zf:   # an older archive holding a full ROM
                zf.writestr("Game 1.0-ru-0.0.9.gba", rom("0.0.9"))
            for version in ("0.1.1", "0.1.2"):
                data = rom(version)
                release.preserve_build(data, latest, version, self.identity(data), name="Game 1.0-ru", base=base)
            with zipfile.ZipFile(archive) as zf:
                self.assertEqual(sorted(n for n in zf.namelist() if not n.endswith(".json")),
                                 ["Game 1.0-ru-0.0.9.ups", "Game 1.0-ru-0.1.0.ups"])
                self.assertEqual(ups.apply(base, zf.read("Game 1.0-ru-0.1.0.ups")), rom("0.1.0"))
                self.assertEqual(ups.apply(base, zf.read("Game 1.0-ru-0.0.9.ups")), rom("0.0.9"))
            rows = {r["version"]: r for r in release.history(Path(tmp) / "translation-history.json")["versions"]}
            self.assertEqual(rows["0.1.0"]["delta"], "Game 1.0-ru-0.1.0.ups")
            (Path(tmp) / "translation-history.json").unlink()
            with self.assertRaisesRegex(ValueError, "different bytes"):
                release.preserve_build(b"other", latest, "0.1.0", self.identity(b"other"),
                                       name="Game 1.0-ru", base=base)

    def test_patch_files_follow_the_retained_builds(self):
        """A patch stays a file only beside its retained build."""
        base = bytes(range(256)) * 64
        def rom(version):
            return base[:100] + version.encode() + base[100 + len(version):]
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "game-ru.gba"
            (Path(tmp) / "Game 1.0-ru-0.0.5.ups").write_bytes(release._delta(base, rom("0.0.5")))
            for version in ("0.1.0", "0.1.1", "0.1.2"):
                data = rom(version)
                built = release.preserve_build(data, latest, version, self.identity(data), name="Game 1.0-ru", base=base)
                patch = release.write_patch(built.with_suffix(".ups"), base, data)
                release.archive_patches(patch, "Game 1.0-ru", ["game-ru"])
            self.assertEqual(sorted(p.name for p in Path(tmp).glob("*.ups")),
                             ["Game 1.0-ru-0.1.1.ups", "Game 1.0-ru-0.1.2.ups"])
            with zipfile.ZipFile(Path(tmp) / "Game 1.0-ru-archive.zip") as zf:
                self.assertIn("Game 1.0-ru-0.0.5.ups", zf.namelist())
                self.assertIn("Game 1.0-ru-0.1.0.ups", zf.namelist())
            with self.assertRaisesRegex(ValueError, "different patch"):
                release.write_patch(Path(tmp) / "Game 1.0-ru-0.1.2.ups", base, rom("0.1.9"))

    def test_archived_versions_keep_their_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "game-ru.gba"
            for version in ("0.1.0", "0.1.1", "0.1.2"):
                self.build(latest, version, "Game 1.0-ru")
            (Path(tmp) / "translation-history.json").unlink()
            with self.assertRaisesRegex(ValueError, "different bytes"):
                release.preserve_build(b"other", latest, "0.1.0", self.identity(b"other"), name="Game 1.0-ru")
            self.assertEqual(latest.read_bytes(), b"build 0.1.2")
            self.build(latest, "0.1.0", "Game 1.0-ru")
            self.assertFalse((Path(tmp) / "Game 1.0-ru-0.1.0.gba").exists())

class CurrentReleaseTest(unittest.TestCase):
    def test_archive_roundtrip_replacement_and_collision(self):
        from gen3tl.core import ups
        base = b"base ROM" * 100
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            latest = root / "Game [Translation].gba"
            latest.with_suffix(".sav").write_bytes(b"gameplay")
            name = "Game-ru"
            archive_name = "Game (Previous versions) [Translation].zip"
            for version in ("0.1.9", "0.1.10"):
                data = base + version.encode()
                identity = dict(profile="game", language="ru", rom_sha1=hashlib.sha1(base).hexdigest(),
                                built_sha1=hashlib.sha1(data).hexdigest())
                release.publish_build(data, latest, version, identity, f"Change {version}", name, base,
                                      ["game-ru"], archive_name)
                self.assertEqual(latest.read_bytes(), data)
                self.assertEqual(ups.apply(base, latest.with_suffix(".ups").read_bytes()), data)
            self.assertEqual([p.name for p in root.glob("*.gba")], [latest.name])
            self.assertEqual([p.name for p in root.glob("*.ups")], [latest.with_suffix(".ups").name])
            self.assertEqual([p.name for p in root.glob("*.project.json")], [latest.with_suffix(".project.json").name])
            self.assertEqual(latest.with_suffix(".sav").read_bytes(), b"gameplay")
            with zipfile.ZipFile(root / archive_name) as zf:
                self.assertIsNone(zf.testzip())
                for version in ("0.1.9", "0.1.10"):
                    self.assertEqual(ups.apply(base, zf.read(f"{name}-{version}.ups")), base + version.encode())
                    self.assertEqual(json.loads(zf.read(f"{name}-{version}.project.json"))["version"], version)
            notes = (root / "release-notes.md").read_text()
            self.assertEqual(release.NOTE_HEADING.findall(notes), [("ru", "0.1.10"), ("ru", "0.1.9")])
            for version in ("0.1.9", "0.1.10"):
                self.assertIn(f"Change {version}", notes)
            before = {p: p.read_bytes() for p in root.iterdir()}
            identity["built_sha1"] = hashlib.sha1(b"different").hexdigest()
            with self.assertRaisesRegex(ValueError, "different bytes"):
                release.publish_build(b"different", latest, "0.1.10", identity, "", name, base, [], archive_name)
            for path, value in before.items():
                self.assertEqual(path.read_bytes(), value)
            (root / "translation-history.json").unlink()
            with self.assertRaisesRegex(ValueError, "different bytes"):
                release.publish_build(b"different", latest, "0.1.9", identity, "", name, base, [], archive_name)
            with self.assertRaisesRegex(ValueError, "different bytes"):
                release.publish_patch(latest.with_suffix(".ups"), base, b"different", "0.1.9", name, [], archive_name)
            self.assertEqual(latest.read_bytes(), base + b"0.1.10")

    def test_notes_follow_the_path_a_caller_names(self):
        base = b"base ROM" * 100
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            latest, notes = root / "build/Game.gba", root / "notes/release-notes.md"
            data = base + b"0.1.0"
            identity = dict(profile="game", language="ru", rom_sha1=hashlib.sha1(base).hexdigest(),
                            built_sha1=hashlib.sha1(data).hexdigest())
            release.publish_build(data, latest, "0.1.0", identity, "Translated the menu.", "Game-ru", base, [],
                                  notes=notes)
            self.assertIn("Translated the menu.", notes.read_text())
            release.record_playtest(latest.with_name("translation-history.json"), "game", "ru", "0.1.0",
                                    "launched", "Opened the title screen", notes)
            self.assertNotIn("title screen", notes.read_text())
            self.assertFalse((root / "build/release-notes.md").exists())

    def test_translation_notes_exclude_operations_and_preserve_manual_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "release-notes.md"
            path.write_text("# Game\n\nVersion: 0.1.8\n\nLegacy instructions\n")
            row = dict(version="0.1.10", profile="game", language="ru", rom_sha1="base", built_sha1="built",
                       file="current.gba", changes="Internal task-123", evidence="Observed", playtests=[],
                       release_label="RU by gen3tl", release_notes=["Translated the menu."],
                       patch_sha256="a" * 64)
            records = {"versions": [row]}
            release.update_notes(path, records)
            text = path.read_text().replace("- Translated the menu.", "- Revised the menu wording.\n- Fixed text wrapping.")
            path.write_text(text)
            row["playtests"] = [dict(at="today", status="checked", note="Menu works")]
            for _ in range(2):
                release.update_notes(path, records)
            text = path.read_text()
            self.assertEqual(release.NOTE_HEADING.findall(text), [("RU by gen3tl", "0.1.10")])
            for content in ("Revised the menu wording.", "Fixed text wrapping.", "a" * 64):
                self.assertEqual(text.count(content), 1)
            for content in ("task-123", "Observed", "Menu works", "Legacy instructions", "current.gba"):
                self.assertNotIn(content, text)

    def test_languages_share_versions_without_merging(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "release-notes.md"
            for label in ("RU by gen3tl", "FR by gen3tl"):
                for version in ("0.1.9", "0.1.10", "0.1.10"):
                    release.update_notes(path, label=label, version=version, changes=f"Updated {label} dialogue.",
                                         patch_sha256="a" * 64)
            headings = release.NOTE_HEADING.findall(path.read_text())
            self.assertEqual(len(headings), 4)
            self.assertEqual([v for _, v in headings], ["0.1.10", "0.1.10", "0.1.9", "0.1.9"])
            self.assertEqual({label for label, _ in headings}, {"RU by gen3tl", "FR by gen3tl"})

    def test_workflow_references_are_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "release-notes.md"
            path.write_text("# Release notes\n")
            for changes in ("Fixed menu (task-123).", "decision-9", "Release milestone", "QA passed",
                            "Confirmed through notify-owner", "Asked in q-a1b2c3"):
                with self.assertRaisesRegex(ValueError, "workflow references"):
                    release.update_notes(path, label="ru", version="0.1.0", changes=changes)
                self.assertEqual(path.read_text(), "# Release notes\n")
        self.assertEqual(release.public_changes("Renamed the shop owner."), ["Renamed the shop owner."])

class TokenInLayoutAndQaTest(unittest.TestCase):
    """The token is replaced before layout and the checks: widths are those of the version."""

    def gate(self, prof, inv, entries, version):
        with tempfile.TemporaryDirectory() as tmp:
            write(Path(tmp) / "a.po", entries, {})
            return qa.run(prof, inv, Path(tmp), CM, M6, set(PACK.glyph_slots()), version=version)

    def test_width_is_measured_with_the_version(self):
        # a single-line label 20 characters wide in English: 120 px
        inv = {"entries": [entry("s0000100", "Version label here!!", "code", "pool")]}
        es = [PoEntry("s0000100", "Version label here!!", "Русская версия @VERSION@")]
        clean = self.gate({}, inv, es, "0.1.0")                 # 20 characters: fits
        self.assertEqual([f.message for f in clean.findings], [])
        wide = self.gate({}, inv, es, "10.20.30")               # 23 characters: 138 px
        self.assertEqual([f.message.split(":")[0] for f in wide.findings], ["line too wide"])
        raw = self.gate({}, inv, es, None)                      # the token itself never encodes
        self.assertTrue(any(f.message.startswith("not encodable") for f in raw.findings))

    def test_token_is_not_a_placeholder(self):
        inv = {"entries": [entry("s0000100", "Hi, {PLAYER}!")]}
        es = [PoEntry("s0000100", "Hi, {PLAYER}!", "Привет, {PLAYER}! Версия @VERSION@.")]
        gate = self.gate({"layout": {"dialogue_width": 208}}, inv, es, "0.1.0")
        self.assertFalse([f for f in gate.findings if "placeholders" in f.message])
        self.assertEqual(gate.errors, 0)

    def test_full_page_window_with_measured_lines(self):
        english = "\\n".join(f"English line {i} of the full page" for i in range(1, 9))   # 8 lines, 192 px
        # 30 characters a line: re-wrap cannot join two lines
        russian = "\\n".join(f"Строка {i} полной страницы, ещё." for i in range(1, 9)) + "\\nРусская версия @VERSION@"
        inv = {"entries": [entry("s0000100", english)]}
        es = [PoEntry("s0000100", english, russian)]
        prof = {"layout": {"dialogue_width": 208}}
        over = self.gate(prof, inv, es, "0.1.0")
        self.assertIn("too many lines: 9 > 8", [f.message for f in over.findings])
        prof["layout"]["window_lines"] = {"s0000100": 10}   # measured in game
        limits = qa.compute_limits(prof, inv, {"s0000100": (Path("a.po"), es[0])}, M6, CM)
        self.assertEqual((limits["s0000100"].kind, limits["s0000100"].lines), ("multiline", 10))
        self.assertEqual(self.gate(prof, inv, es, "0.1.0").findings, [])
        # layout keeps translator lines and measures the version
        text = release.expand(russian, "0.1.0")
        lim = Limit("multiline", M6.width("Строка 1 полной страницы, ещё.", CM), 10)   # 180 px
        self.assertEqual(layout(text, lim, M6, CM), text)
        self.assertEqual(check(text, lim, M6, CM), [])
        longer = release.expand(russian, "1000000.2000000.3")   # the label line 192 px
        laid = layout(longer, lim, M6, CM)                      # re-wrapped: the version moves to a 10th line
        self.assertTrue(laid.endswith("\\nРусская версия\\n1000000.2000000.3"))
        self.assertEqual(check(laid, lim, M6, CM), [])
        nine = Limit("multiline", lim.width, 9)
        self.assertEqual(check(layout(longer, nine, M6, CM), nine, M6, CM), ["too many lines: 10 > 9"])

class ReleaseProject:
    """A game project in a git repository with one committed release."""

    VERSION = "0.2.15"
    LABEL = "RU by gen3tl"

    def setUp(self):
        from gen3tl.project import Manifest
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cwd = os.getcwd()
        os.chdir(self.root)
        profile = self.root / "game.toml"
        profile.write_text('[rom]\nsha1 = "' + "0" * 40 + '"\n', encoding="utf-8")
        (self.root / "project.toml").write_text(
            Manifest(self.root / "project.toml", "game", "0" * 40, __version__, ("ru",), "ru").text(),
            encoding="utf-8")
        (self.root / "projects.toml").write_text(
            '[defaults]\nprofile = "game"\nlanguage = "ru"\n\n[[project]]\nprofile = "game"\n'
            'language = "ru"\nroot = "."\npolicy = "translation/ru/policy.toml"\n', encoding="utf-8")
        (self.root / "translation/ru").mkdir(parents=True)
        write_policy(self.root / "translation/ru", Profile.load(profile), "ru")
        (self.root / "translation/ru/release.toml").write_text(f'version = "{self.VERSION}"\n', encoding="utf-8")
        from gen3tl.core import ups
        patch_data = ups.create(b"sample game", b"sample translated game")
        row = {"profile": "game", "profile_name": "Game", "language": "ru", "version": self.VERSION,
               "source_sha1": "0" * 40, "source_size": 1, "result_sha1": "0" * 40, "result_size": 1,
               "patch": f"game/ru/{self.VERSION}.ups", "patch_sha256": hashlib.sha256(patch_data).hexdigest(),
               "patch_size": len(patch_data),
               "output": f"game-ru-{self.VERSION}.gba",
               "release_tag": f"ru-v{self.VERSION}", "release_asset": f"game-ru-{self.VERSION}.ups"}
        self.notes = self.root / "releases" / f"game/ru/{self.VERSION}.notes.json"
        self.notes.parent.mkdir(parents=True)
        self.write_notes(self.LABEL, self.VERSION)
        row["notes"] = f"game/ru/{self.VERSION}.notes.json"
        row["notes_sha256"] = hashlib.sha256(self.notes.read_bytes()).hexdigest()
        (self.root / "releases/releases.json").write_text(
            json.dumps({"schema": 1, "releases": [row]}, indent=2) + "\n", encoding="utf-8")
        patch = self.root / "build" / "patches" / row["patch"]
        patch.parent.mkdir(parents=True)
        patch.write_bytes(patch_data)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Test"], check=True)
        (self.root / ".gitignore").write_text("build/\n")
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "release"], check=True)

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def write_notes(self, label, version):
        self.notes.write_text(json.dumps({"schema": 1, "profile": "game", "language": "ru", "version": version,
                                          "release_label": label, "notes": ["Translated the menu."]}) + "\n",
                              encoding="utf-8")


class BuildTagTest(ReleaseProject, unittest.TestCase):
    """The release tag command reads the file, the release files and the commit."""

    def tag(self, *argv):
        with mock.patch("builtins.print") as output:
            code = main(["--project-root", str(self.root), "--profile", str(self.root / "game.toml"),
                         "build", "tag", *argv])
        return code, [str(call.args[0]) for call in output.call_args_list]

    def test_prints_the_tag_and_commit_without_creating_it(self):
        tracked = subprocess.run(["git", "-C", str(self.root), "ls-files", "*.ups"],
                                 capture_output=True, text=True, check=True).stdout
        self.assertEqual(tracked, "")
        head = subprocess.run(["git", "-C", str(self.root), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
        code, lines = self.tag()
        self.assertEqual(code, 0)
        self.assertEqual([line.split() for line in lines if line.split()[:1] == ["tag"]],
                         [["tag", f"ru-v{self.VERSION}"]])
        self.assertEqual([line.split() for line in lines if line.split()[:1] == ["commit"]],
                         [["commit", head]])
        tags = subprocess.run(["git", "-C", str(self.root), "tag", "--list"],
                              capture_output=True, text=True, check=True).stdout
        self.assertEqual(tags, "")

    def test_invalid_version_is_refused(self):
        (self.root / "translation/ru/release.toml").write_text('version = "0.2"\n', encoding="utf-8")
        code, lines = self.tag()
        self.assertEqual(code, 1)
        self.assertTrue(any(line.startswith("error") for line in lines))

    def test_a_release_file_with_a_name_is_refused(self):
        for key in ("output_name", "archive_name", "release_label"):
            with self.subTest(key=key):
                (self.root / "translation/ru/release.toml").write_text(
                    f'version = "{self.VERSION}"\n{key} = "Game"\n', encoding="utf-8")
                code, lines = self.tag()
                self.assertEqual(code, 1)
                self.assertTrue(any(line.startswith("error") and line.endswith(
                    f"release.toml: remove {key}; the release names follow from the game, the language "
                    "and the version") for line in lines), lines)

    def test_missing_release_file_is_refused(self):
        (self.root / "build/patches/game/ru" / f"{self.VERSION}.ups").unlink()
        code, lines = self.tag()
        self.assertEqual(code, 1)
        self.assertTrue(any("missing release file" in line for line in lines))

    def test_corrupt_stored_patch_is_refused(self):
        (self.root / "build/patches/game/ru" / f"{self.VERSION}.ups").write_bytes(b"corrupt")
        code, lines = self.tag()
        self.assertEqual(code, 1)
        self.assertTrue(any("differs from its manifest" in line for line in lines))

    def test_notes_of_another_release_are_refused(self):
        for label, version in ((self.LABEL, "9.9.9"), ("other", self.VERSION), ("ru", self.VERSION)):
            with self.subTest(label=label, version=version):
                self.write_notes(label, version)
                subprocess.run(["git", "-C", str(self.root), "commit", "-qam", "notes"], check=True)
                code, lines = self.tag()
                self.assertEqual(code, 1)
                self.assertTrue(any("do not carry" in line for line in lines))

    def test_release_files_outside_the_current_commit_are_refused(self):
        self.write_notes(self.LABEL, self.VERSION)
        self.notes.write_text(self.notes.read_text() + "\n", encoding="utf-8")
        code, lines = self.tag()
        self.assertEqual(code, 1)
        self.assertTrue(any("not in the current commit" in line for line in lines))
        subprocess.run(["git", "-C", str(self.root), "checkout", "-q", "--", "."], check=True)
        manifest = self.root / "releases/releases.json"
        manifest.write_text(manifest.read_text() + "\n", encoding="utf-8")
        code, lines = self.tag()
        self.assertEqual(code, 1)
        self.assertTrue(any("releases.json: not in the current commit" in line for line in lines))

    def test_no_cumulative_notes_file_is_needed(self):
        self.assertFalse((self.root / "releases/release-notes.md").exists())
        self.assertEqual(self.tag()[0], 0)

    def test_existing_tag_is_refused(self):
        subprocess.run(["git", "-C", str(self.root), "tag", f"ru-v{self.VERSION}"], check=True)
        code, lines = self.tag()
        self.assertEqual(code, 1)
        self.assertTrue(any("already exists" in line for line in lines))


class ReleaseNotesTest(ReleaseProject, unittest.TestCase):
    """The release text of a tag lists the commits of its language since the previous tag."""

    def commit(self, name, text, subject):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", name], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", subject], check=True)
        return subprocess.run(["git", "-C", str(self.root), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()

    def release(self, version, names=""):
        return self.commit("translation/ru/release.toml", f'version = "{version}"\n{names}',
                           f"chore(release): release translation {version}")

    def text_of(self, tag, *argv):
        out = self.root / "build/release-text"
        with mock.patch("builtins.print") as output:
            code = main(["--project-root", str(self.root), "--profile", str(self.root / "game.toml"), "--lang", "ru",
                         "build", "release-notes", "--tag", tag, "--out", str(out), *argv])
        lines = dict(str(call.args[0]).split(None, 1) for call in output.call_args_list)
        body = (out / "body.md").read_text(encoding="utf-8") if code == 0 else ""
        title = (out / "title.txt").read_text(encoding="utf-8") if code == 0 else ""
        return code, lines, title, body

    def test_the_first_tag_lists_every_commit_of_the_language(self):
        first = subprocess.run(["git", "-C", str(self.root), "rev-parse", "HEAD"],
                               capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["git", "-C", str(self.root), "tag", f"ru-v{self.VERSION}"], check=True)
        code, lines, title, body = self.text_of(f"ru-v{self.VERSION}")
        self.assertEqual(code, 0, lines)
        self.assertEqual((lines["previous"], lines["commits"]), ("none", "1"))
        self.assertEqual(title, f"[{self.LABEL}] {self.VERSION}\n")
        self.assertEqual(body.splitlines()[:3], ["Changes of the Russian translation:", "", f"- release (`{first[:7]}`)"])
        self.assertTrue(body.endswith("UPS SHA-256: `" + json.loads(
            (self.root / "releases/releases.json").read_text())["releases"][0]["patch_sha256"] + "`\n"))

    def test_a_later_tag_lists_the_commits_since_the_previous_tag_of_its_language(self):
        subprocess.run(["git", "-C", str(self.root), "tag", f"ru-v{self.VERSION}"], check=True)
        one = self.commit("translation/ru/glossary.tsv", "one\n", "fix(translation): apply editor submission (#12)")
        self.commit("translation/de/glossary.tsv", "eins\n", "fix(translation): correct a German line")
        self.release("0.2.16")
        subprocess.run(["git", "-C", str(self.root), "tag", "de-v9.0.0"], check=True)
        two = self.commit("translation/ru/glossary.tsv", "two\n", "fix(translation): thank @someone for a line")
        self.commit("web/readme.txt", "text\n", "docs(readme): describe the project")
        three = self.release("0.2.17")
        subprocess.run(["git", "-C", str(self.root), "tag", "ru-v0.2.17"], check=True)
        code, lines, title, body = self.text_of("ru-v0.2.17", "--repository", "account/name")
        self.assertEqual(code, 0, lines)
        self.assertEqual((lines["previous"], lines["commits"]), (f"ru-v{self.VERSION}", "4"))
        self.assertEqual(title, f"[{self.LABEL}] 0.2.17\n")
        rows = body.splitlines()
        self.assertEqual(rows[0], f"Changes of the Russian translation since `ru-v{self.VERSION}`:")
        self.assertEqual(rows[2], f"- fix(translation): apply editor submission (#12) "
                                  f"([`{one[:7]}`](https://github.com/account/name/commit/{one}))")
        self.assertEqual(rows[3].split(" (")[0], "- chore(release): release translation 0.2.16")
        self.assertIn(two[:7], rows[4])
        self.assertNotIn("@someone", body)
        self.assertIn("@\u200bsomeone", rows[4])
        self.assertIn(three[:7], rows[5])
        self.assertEqual(len(rows), 6)   # no manifest row of this version, so no hash line
        self.assertNotIn("German", body)
        self.assertNotIn("describe the project", body)

    def test_a_tag_of_a_release_file_with_names_takes_the_derived_label(self):
        self.release("0.2.16", 'output_name = "Game [Translated]"\nrelease_label = "Russian by Example"\n')
        subprocess.run(["git", "-C", str(self.root), "tag", "ru-v0.2.16"], check=True)
        self.release("1.0.0")
        subprocess.run(["git", "-C", str(self.root), "tag", "ru-v1.0.0"], check=True)
        for tag, version in (("ru-v0.2.16", "0.2.16"), ("ru-v1.0.0", "1.0.0")):
            with self.subTest(tag=tag):
                code, lines, title, _ = self.text_of(tag)
                self.assertEqual(code, 0, lines)
                self.assertEqual(title, f"[{self.LABEL}] {version}\n")

    def test_a_tag_on_a_commit_of_another_version_is_refused(self):
        subprocess.run(["git", "-C", str(self.root), "tag", f"ru-v{self.VERSION}"], check=True)
        subprocess.run(["git", "-C", str(self.root), "tag", "de-v0.0.1"], check=True)
        code, lines, _, body = self.text_of(f"ru-v{self.VERSION}")
        self.assertEqual((code, lines["previous"]), (0, "none"))   # a tag of another language is no previous tag
        self.release("0.2.16")
        subprocess.run(["git", "-C", str(self.root), "tag", "ru-v0.2.16"], check=True)
        subprocess.run(["git", "-C", str(self.root), "tag", "ru-v0.2.20", f"ru-v{self.VERSION}"], check=True)
        code, lines, _, body = self.text_of("ru-v0.2.20")
        self.assertEqual(code, 1)   # the tagged commit states another version
        self.assertIn("states version", lines["error"])

    def test_wrong_tags_are_refused(self):
        subprocess.run(["git", "-C", str(self.root), "tag", f"ru-v{self.VERSION}"], check=True)
        for tag, reason in (("v1.0.0", "not a release tag"), ("de-v1.0.0", "not a tag of the language ru"),
                            ("ru-v7.7.7", "git rev-parse")):
            with self.subTest(tag=tag):
                code, lines, _, _ = self.text_of(tag)
                self.assertEqual(code, 1)
                self.assertIn(reason, lines["error"])
        code, lines, _, _ = self.text_of(f"ru-v{self.VERSION}", "--repository", "not a repository")
        self.assertEqual(code, 1)
        self.assertIn("not a repository", lines["error"])
