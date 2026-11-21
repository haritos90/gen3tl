import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zlib

from gen3tl.web import patcher
from gen3tl.core import ups
from gen3tl.profile.profile import Profile

class ReleaseFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = bytes(range(256))
        self.target = self.base[:30] + b"translated" + self.base[40:]
        self.profile = Profile({"rom": {"name": "Example Game", "sha1": hashlib.sha1(self.base).hexdigest()}}, self.root / "example.toml")
        self.out = self.root / "site/releases"
        self.built = self.root / "translated.gba"
        self.patch = self.root / "translated.ups"
        self.write_release()

    def write_release(self, version="0.1.17", language="ru", target=None):
        target = self.target if target is None else target
        self.built.write_bytes(target)
        self.patch.write_bytes(ups.create(self.base, target))
        self.identity = {"profile": self.profile.name, "language": language,
                         "rom_sha1": self.profile.sha1, "built_sha1": hashlib.sha1(target).hexdigest(),
                         "version": version}
        self.built.with_suffix(".project.json").write_text(json.dumps(self.identity))

    def prepare(self, language="ru"):
        return patcher.prepare(self.profile, language, self.base, self.built, self.patch, self.out)

class NameTablesTest(ReleaseFixture, unittest.TestCase):
    """The save page's name tables beside each release."""

    def names_of(self, row, target):
        return json.dumps({"version": row["version"], "result": target[30:40].hex()}).encode()

    def test_new_release_publishes_its_name_tables(self):
        row = patcher.prepare(self.profile, "ru", self.base, self.built, self.patch, self.out, self.names_of)
        self.assertEqual(row["names"], "example/ru/0.1.17.names.json")
        data = (self.out / row["names"]).read_bytes()
        self.assertEqual(row["names_sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(json.loads(data)["result"], b"translated".hex())
        self.assertEqual(patcher.validate(json.loads((self.out / "releases.json").read_text())), [row])

    def test_an_older_release_gains_its_tables_once(self):
        row = self.prepare()
        self.assertNotIn("names", row)
        again = patcher.prepare(self.profile, "ru", self.base, self.built, self.patch, self.out, self.names_of)
        self.assertEqual({k: again[k] for k in patcher.FIELDS | patcher.PUBLICATION_FIELDS}, row)
        self.assertIn("names", again)
        before = (self.out / "releases.json").read_bytes()
        self.assertEqual(patcher.prepare(self.profile, "ru", self.base, self.built, self.patch, self.out,
                                         self.names_of), again)
        self.assertEqual((self.out / "releases.json").read_bytes(), before)

    def test_backfill_from_the_published_patches(self):
        self.prepare()
        self.write_release("0.1.18", target=self.base[:30] + b"TRANSLATED" + self.base[40:])
        self.prepare()
        self.assertEqual(patcher.add_names(self.profile, self.base, self.out, self.names_of), ["0.1.17", "0.1.18"])
        rows = patcher.validate(json.loads((self.out / "releases.json").read_text()))
        self.assertEqual([json.loads((self.out / r["names"]).read_text())["result"] for r in rows],
                         [b"translated".hex(), b"TRANSLATED".hex()])
        self.assertEqual(patcher.add_names(self.profile, self.base, self.out, self.names_of), [])
        with self.assertRaisesRegex(ValueError, "base SHA-1"):
            patcher.add_names(self.profile, b"wrong", self.out, self.names_of)

    def test_backfill_refuses_a_patch_that_differs(self):
        row = self.prepare()
        (patcher.patch_store(self.out) / row["patch"]).write_bytes(b"UPS1broken")
        with self.assertRaisesRegex(ValueError, "differs from its manifest"):
            patcher.add_names(self.profile, self.base, self.out, self.names_of)
        self.assertFalse((self.out / "example/ru/0.1.17.names.json").exists())

    def test_manifest_checks_the_name_fields(self):
        row = patcher.prepare(self.profile, "ru", self.base, self.built, self.patch, self.out, self.names_of)
        for change in ({"names": "example/ru/other.names.json"}, {"names_sha256": "x"}):
            with self.assertRaisesRegex(ValueError, "name tables"):
                patcher.validate({"schema": 1, "releases": [dict(row, **change)]})
        half = {k: v for k, v in row.items() if k != "names_sha256"}
        with self.assertRaisesRegex(ValueError, "fields"):
            patcher.validate({"schema": 1, "releases": [half]})

class ReleaseNotesTest(ReleaseFixture, unittest.TestCase):
    """Public notes beside each release."""

    def notes_of(self, row):
        return patcher.notes_document(row, {"release_label": "RU by gen3tl",
                                            "release_notes": [f"Translation changes in {row['version']}."]})

    def test_new_release_publishes_its_notes(self):
        row = patcher.prepare(self.profile, "ru", self.base, self.built, self.patch, self.out,
                              notes_of=self.notes_of)
        self.assertEqual(row["notes"], "example/ru/0.1.17.notes.json")
        data = (self.out / row["notes"]).read_bytes()
        self.assertEqual(row["notes_sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(json.loads(data), {"schema": 1, "profile": "example", "language": "ru",
                                            "version": "0.1.17", "release_label": "RU by gen3tl",
                                            "notes": ["Translation changes in 0.1.17."]})
        self.assertEqual(patcher.validate(json.loads((self.out / "releases.json").read_text())), [row])

    def test_an_older_release_gains_its_notes_once(self):
        row = self.prepare()
        self.assertNotIn("notes", row)
        again = patcher.prepare(self.profile, "ru", self.base, self.built, self.patch, self.out,
                                notes_of=self.notes_of)
        self.assertEqual({k: again[k] for k in patcher.FIELDS | patcher.PUBLICATION_FIELDS}, row)
        self.assertIn("notes", again)
        before = (self.out / "releases.json").read_bytes()
        self.assertEqual(patcher.prepare(self.profile, "ru", self.base, self.built, self.patch, self.out,
                                         notes_of=self.notes_of), again)
        self.assertEqual((self.out / "releases.json").read_bytes(), before)

    def test_backfill_covers_every_registered_version(self):
        self.prepare()
        self.write_release("0.1.18", target=self.base[:30] + b"TRANSLATED" + self.base[40:])
        self.prepare()
        self.assertEqual(patcher.add_notes(self.profile, self.out, self.notes_of), ["0.1.17", "0.1.18"])
        rows = patcher.validate(json.loads((self.out / "releases.json").read_text()))
        self.assertEqual([json.loads((self.out / row["notes"]).read_text())["notes"] for row in rows],
                         [["Translation changes in 0.1.17."], ["Translation changes in 0.1.18."]])
        self.assertEqual(patcher.add_notes(self.profile, self.out, self.notes_of), [])

    def test_manifest_checks_optional_note_fields(self):
        row = patcher.prepare(self.profile, "ru", self.base, self.built, self.patch, self.out,
                              notes_of=self.notes_of)
        for change in ({"notes": "example/ru/other.notes.json"}, {"notes_sha256": "x"}):
            with self.assertRaisesRegex(ValueError, "release notes"):
                patcher.validate({"schema": 1, "releases": [dict(row, **change)]})
        half = {k: v for k, v in row.items() if k != "notes_sha256"}
        with self.assertRaisesRegex(ValueError, "fields"):
            patcher.validate({"schema": 1, "releases": [half]})
        with_names = patcher.prepare(self.profile, "ru", self.base, self.built, self.patch, self.out,
                                     lambda r, target: b"{}", self.notes_of)
        self.assertEqual(patcher.validate({"schema": 1, "releases": [with_names]}), [with_names])

class PatcherDataTest(ReleaseFixture, unittest.TestCase):
    def test_download_filename_changes_without_changing_release_bytes(self):
        row = self.prepare()
        patch = (patcher.patch_store(self.out) / row["patch"]).read_bytes()
        named = patcher.prepare(self.profile, "ru", self.base, self.built, self.patch, self.out,
                                output_of=lambda version: f"Example Game [Translated {version}].gba")
        self.assertEqual(named["output"], "Example Game [Translated 0.1.17].gba")
        self.assertEqual({k: v for k, v in row.items() if k != "output"},
                         {k: v for k, v in named.items() if k != "output"})
        self.assertEqual((patcher.patch_store(self.out) / row["patch"]).read_bytes(), patch)
        self.assertEqual(self.prepare()["output"], named["output"])
        for name in ("../Other.gba", "dir/Other.gba", "dir\\Other.gba", "https://host/Other.gba",
                     "Other\n.gba", "Other\x00.gba", ".gba", " .gba", "Other.ups"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "filenames"):
                patcher.validate({"schema": 1, "releases": [dict(named, output=name)]})

    def test_historical_release_is_idempotent_and_rom_free(self):
        (self.root / "release.toml").write_text('version = "0.1.18"\n')
        row = self.prepare()
        self.assertEqual(row["version"], "0.1.17")
        before = (self.out / "releases.json").read_bytes()
        self.assertEqual(self.prepare(), row)
        self.assertEqual((self.out / "releases.json").read_bytes(), before)
        self.assertEqual(sorted(p.suffix for p in self.out.rglob("*") if p.is_file()), [".json"])
        self.assertEqual(ups.apply(self.base, (patcher.patch_store(self.out) / row["patch"]).read_bytes()), self.target)

    def test_multiple_profiles_languages_and_versions(self):
        self.prepare()
        self.write_release("0.1.18")
        self.prepare()
        self.write_release(language="fr")
        self.prepare("fr")
        self.profile = Profile(self.profile.data, self.root / "other-game.toml")
        self.write_release(language="de")
        self.prepare("de")
        rows = patcher.validate(json.loads((self.out / "releases.json").read_text()))
        self.assertEqual(len(rows), 4)
        self.assertEqual({(r["profile"], r["language"]) for r in rows},
                         {("example", "ru"), ("example", "fr"), ("other-game", "de")})

    def test_identity_mismatches_never_publish(self):
        for key, wrong in (("profile", "other"), ("language", "fr"), ("rom_sha1", "0" * 40),
                           ("built_sha1", "0" * 40), ("version", None)):
            with self.subTest(key=key):
                self.write_release()
                self.identity[key] = wrong
                self.built.with_suffix(".project.json").write_text(json.dumps(self.identity))
                with self.assertRaises(ValueError):
                    self.prepare()
                self.assertFalse(self.out.exists())
        self.built.with_suffix(".project.json").unlink()
        with self.assertRaises(FileNotFoundError):
            self.prepare()

    def test_retained_unsupported_stage_never_publishes(self):
        for marker in ("identity", "profile"):
            with self.subTest(marker=marker):
                self.write_release()
                if marker == "identity":
                    self.identity["stage"] = "groundwork"
                    self.built.with_suffix(".project.json").write_text(json.dumps(self.identity))
                else:
                    self.profile.stage = "groundwork"
                try:
                    with self.assertRaisesRegex(ValueError, "unsupported stage 'groundwork'"):
                        self.prepare()
                finally:
                    if marker == "profile":
                        del self.profile.stage
                self.assertFalse(self.out.exists())
        self.write_release()
        self.identity["stage"] = "translation"
        self.built.with_suffix(".project.json").write_text(json.dumps(self.identity))
        self.profile.stage = "translation"
        self.assertEqual(self.prepare()["version"], "0.1.17")

    def test_unverified_profiles_cannot_publish(self):
        for stage in ("discovery", "catalog"):
            self.profile.data["support"] = {"stage": stage}
            with self.assertRaisesRegex(ValueError, "profile|verified"):
                self.prepare()
        self.assertFalse(self.out.exists())

    def test_wrong_source_and_wrong_patch_never_publish(self):
        with self.assertRaisesRegex(ValueError, "base SHA-1"):
            patcher.prepare(self.profile, "ru", b"wrong", self.built, self.patch, self.out)
        self.patch.write_bytes(ups.create(self.base, b"!" * len(self.target)))
        with self.assertRaisesRegex(ValueError, "result differs"):
            self.prepare()
        self.patch.write_bytes(self.patch.read_bytes()[:-1] + b"!")
        with self.assertRaisesRegex(ValueError, "invalid release UPS"):
            self.prepare()
        self.assertFalse(self.out.exists())

    def test_malicious_ups_size_is_bounded_before_decode(self):
        self.patch.write_bytes(b"UPS1" + ups._vlen(len(self.base)) + ups._vlen(2 ** 50) + b"\0" * 12)
        with mock.patch("gen3tl.core.ups.apply", side_effect=AssertionError("unsafe allocation")):
            with self.assertRaisesRegex(ValueError, "UPS size"):
                self.prepare()

    def test_browser_rejected_records_cannot_be_published(self):
        self.write_release(target=self.base)
        for records in (ups._vlen(256) + b"\0", b"\0", ups._vlen(0) + b"\x01"):
            data = b"UPS1" + ups._vlen(256) * 2 + records
            data += zlib.crc32(self.base).to_bytes(4, "little") * 2
            self.patch.write_bytes(data + zlib.crc32(data).to_bytes(4, "little"))
            with self.subTest(records=records):
                with self.assertRaisesRegex(ValueError, "invalid release UPS"):
                    self.prepare()
                self.assertFalse(self.out.exists())

    def test_profile_cannot_change_base_between_versions(self):
        self.prepare()
        self.base = b"x" * len(self.base)
        self.profile.data["rom"]["sha1"] = hashlib.sha1(self.base).hexdigest()
        self.write_release("0.1.18")
        with self.assertRaisesRegex(ValueError, "inconsistent base identity"):
            self.prepare()
        self.assertEqual(len(json.loads((self.out / "releases.json").read_text())["releases"]), 1)

    def test_conflicting_version_preserves_existing_release(self):
        self.prepare()
        before = {str(p): p.read_bytes() for p in self.out.rglob("*") if p.is_file()}
        self.write_release(target=b"!" * len(self.target))
        with self.assertRaisesRegex(ValueError, "already identifies"):
            self.prepare()
        self.assertEqual({str(p): p.read_bytes() for p in self.out.rglob("*") if p.is_file()}, before)

    def test_corrupt_existing_artifact_is_not_advertised_again(self):
        row = self.prepare()
        (patcher.patch_store(self.out) / row["patch"]).write_bytes(b"corrupt")
        self.write_release("0.1.18")
        with self.assertRaisesRegex(ValueError, "release patch differs"):
            self.prepare()
        self.assertEqual(len(json.loads((self.out / "releases.json").read_text())["releases"]), 1)

    def test_manifest_rejects_duplicate_paths_and_types(self):
        row = self.prepare()
        bad = [{"schema": 1, "releases": [row, row]}, {"schema": 2, "releases": [row]}]
        for key, value in (("patch", "../../rom.gba"), ("output", "https://example.com/rom.gba"),
                           ("profile", "../other"), ("source_size", True), ("result_size", patcher.MAX_SIZE + 1)):
            changed = dict(row, **{key: value})
            bad.append({"schema": 1, "releases": [changed]})
        for document in bad:
            with self.subTest(document=document):
                with self.assertRaises(ValueError):
                    patcher.validate(document)

    def test_cli_uses_explicit_registry_and_shared_output(self):
        from gen3tl.__main__ import main
        registry = self.root / "registry"
        workspace = registry / "projects/example/ru"
        workspace.mkdir(parents=True)
        (registry / "projects.toml").write_text('''[defaults]
profile = "example"
language = "ru"
[[project]]
profile = "example"
language = "ru"
root = "projects/example/ru"
policy = "policy.toml"
''')
        (workspace / "translation").mkdir()
        (workspace / "translation/release.toml").write_text('version = "1.2.0"\n')   # a later version than the build
        (workspace / "policy.toml").write_text(f'''[policy]
version = "2.0.0"
profile = "example"
language = "ru"
rom_sha1 = "{self.profile.sha1}"
''')
        self.profile.path.write_text(f'[rom]\nname = "Example Game"\nsha1 = "{self.profile.sha1}"\n')
        base = self.root / "source.gba"
        base.write_bytes(self.base)
        record = dict(self.identity, release_label="RU by gen3tl", release_notes=["Translated the menus."],
                      patch_sha256=hashlib.sha256(self.patch.read_bytes()).hexdigest())
        (self.root / "translation-history.json").write_text(json.dumps({"versions": [record]}))
        with mock.patch("builtins.print"):
            result = main(["build", "patcher-data", "--project-root", str(registry), "--profile", str(self.profile.path),
                           "--rom", str(base), "--built", str(self.built), "--patch-file", str(self.patch)])
        self.assertEqual(result, 0)
        manifest = registry / "build/editor/releases/releases.json"
        self.assertTrue(manifest.exists())
        row = json.loads(manifest.read_text())["releases"][0]
        self.assertEqual(row["output"], "Example Game [RU v0.1.17 by gen3tl].gba")
        self.assertFalse(patcher.PUBLICATION_FIELDS.intersection(row))
        self.assertEqual((manifest.parent / row["patch"]).read_bytes(), self.patch.read_bytes())
        notes = json.loads((manifest.parent / row["notes"]).read_text())
        self.assertEqual(notes["notes"], ["Translated the menus."])
        self.assertFalse((workspace / "build/editor").exists())

    def test_preview_accepts_only_patcher_data(self):
        from argparse import Namespace

        from gen3tl.cli.web import editor_preview
        self.prepare()
        server = mock.Mock(server_address=("127.0.0.1", 8123))
        with mock.patch("gen3tl.web.editorserve.preview_server", return_value=server), mock.patch("builtins.print"):
            result = editor_preview(Namespace(data=str(self.out.parent), port=8123))
        self.assertEqual(result, 0)
        server.serve_forever.assert_called_once()

class CopyReleasesTest(ReleaseFixture, unittest.TestCase):
    """A game project's releases/ copied into the site data."""

    def setUp(self):
        super().setUp()
        self.data = self.root / "data/releases"
        other = Profile({"rom": {"name": "Other Game", "sha1": self.profile.sha1}}, self.root / "other.toml")
        identity = dict(self.identity, profile="other", language="de")
        self.built.with_suffix(".project.json").write_text(json.dumps(identity))
        patcher.prepare(other, "de", self.base, self.built, self.patch, self.data)   # another profile's row
        self.write_release()

    def rows(self):
        return patcher.validate(json.loads((self.data / "releases.json").read_text()))

    def test_verified_files_join_the_other_profiles_rows(self):
        first = self.prepare()
        self.write_release("0.1.18", target=self.base[:30] + b"TRANSLATED" + self.base[40:])
        second = patcher.prepare(self.profile, "ru", self.base, self.built, self.patch, self.out,
                                 notes_of=lambda row: b'{"notes": []}\n')
        self.assertEqual(patcher.copy_releases(self.out, self.data, "example"), 3)
        self.assertEqual([(r["profile"], r["version"]) for r in self.rows()],
                         [("example", "0.1.17"), ("example", "0.1.18"), ("other", "0.1.17")])
        for row in (first, second):
            self.assertEqual((self.data / row["patch"]).read_bytes(), (patcher.patch_store(self.out) / row["patch"]).read_bytes())
        self.assertTrue((self.data / second["notes"]).is_file())
        before = (self.data / "releases.json").read_bytes()
        self.assertEqual(patcher.copy_releases(self.out, self.data, "example"), 0)
        self.assertEqual((self.data / "releases.json").read_bytes(), before)

    def test_a_file_that_differs_from_its_manifest_is_refused(self):
        first = self.prepare()
        self.write_release("0.1.18", target=self.base[:30] + b"TRANSLATED" + self.base[40:])
        second = self.prepare()
        (patcher.patch_store(self.out) / second["patch"]).write_bytes(b"UPS1broken")
        before = (self.data / "releases.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "differs from its manifest"):
            patcher.copy_releases(self.out, self.data, "example")
        self.assertEqual((self.data / "releases.json").read_bytes(), before)
        self.assertFalse((self.data / first["patch"]).exists())

    def test_without_releases_nothing_is_written(self):
        self.assertEqual(patcher.copy_releases(self.out, self.root / "empty", "example"), 0)
        self.assertFalse((self.root / "empty").exists())


    def test_missing_or_wrong_size_patch_stops_before_copying(self):
        row = self.prepare()
        path = patcher.patch_path(self.out, row)
        data = path.read_bytes()
        path.unlink()
        with self.assertRaisesRegex(ValueError, "missing release file"):
            patcher.copy_releases(self.out, self.data, "example")
        self.assertFalse((self.data / row["patch"]).exists())
        path.write_bytes(data)
        document = json.loads((self.out / "releases.json").read_text())
        document["releases"][0]["patch_size"] += 1
        (self.out / "releases.json").write_text(json.dumps(document))
        with self.assertRaisesRegex(ValueError, "differs from its manifest"):
            patcher.copy_releases(self.out, self.data, "example")
        self.assertFalse((self.data / row["patch"]).exists())

    def test_browser_manifest_keeps_the_existing_contract(self):
        row = self.prepare()
        patcher.copy_releases(self.out, self.data, "example")
        copied = next(r for r in self.rows() if r["profile"] == "example")
        self.assertEqual(copied, patcher.browser_row(row))
        self.assertFalse(patcher.PUBLICATION_FIELDS.intersection(copied))

class FetchPatchesTest(ReleaseFixture, unittest.TestCase):
    def test_downloads_exact_manifest_assets_and_reuses_verified_store(self):
        row = self.prepare()
        path = patcher.patch_path(self.out, row)
        data = path.read_bytes()
        path.unlink()
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = data
        with mock.patch("urllib.request.urlopen", return_value=response) as download:
            self.assertEqual(patcher.fetch_patches(self.out, "owner/project"), 1)
            download.assert_called_once_with(
                "https://github.com/owner/project/releases/download/ru-v0.1.17/example-ru-0.1.17.ups",
                timeout=60)
            response.__enter__.return_value.read.assert_called_once_with(row["patch_size"] + 1)
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(patcher.fetch_patches(self.out, "owner/project"), 0)
            self.assertEqual(download.call_count, 1)
        self.assertFalse(list(self.out.rglob("*.ups")))

    def test_bad_download_or_missing_asset_leaves_store_empty(self):
        from urllib.error import HTTPError
        first = self.prepare()
        self.write_release("0.1.18", target=b"changed")
        second = self.prepare()
        first_path, second_path = (patcher.patch_path(self.out, row) for row in (first, second))
        data = first_path.read_bytes()
        second_data = second_path.read_bytes()
        first_path.unlink()
        second_path.unlink()
        failures = [second_data[:-1], bytes([second_data[0] ^ 1]) + second_data[1:],
                    second_data + b"extra", HTTPError("https://example.invalid", 404, "missing", {}, None)]
        for bad in failures:
            with self.subTest(failure=type(bad).__name__):
                result = bad if isinstance(bad, Exception) else io.BytesIO(bad)
                with mock.patch("urllib.request.urlopen", side_effect=[io.BytesIO(data), result]):
                    with self.assertRaisesRegex(ValueError, "differs from its manifest|download failed"):
                        patcher.fetch_patches(self.out, "owner/project")
                self.assertFalse(first_path.exists())
                self.assertFalse(second_path.exists())

    def test_corrupt_local_patch_is_not_replaced_by_download(self):
        row = self.prepare()
        path = patcher.patch_path(self.out, row)
        path.write_bytes(b"corrupt")
        with mock.patch("urllib.request.urlopen") as download:
            with self.assertRaisesRegex(ValueError, "differs from its manifest"):
                patcher.fetch_patches(self.out, "owner/project")
            download.assert_not_called()
        self.assertEqual(path.read_bytes(), b"corrupt")

    def test_publication_coordinates_are_complete_and_cannot_escape(self):
        row = self.prepare()
        for key, value in (("release_tag", "../tag"), ("release_asset", "../asset.ups"),
                           ("release_asset", "https://example.invalid/asset.ups")):
            with self.subTest(key=key, value=value):
                with self.assertRaisesRegex(ValueError, "publication"):
                    patcher.validate({"schema": 1, "releases": [dict(row, **{key: value})]})
        half = dict(row)
        del half["release_asset"]
        with self.assertRaisesRegex(ValueError, "fields"):
            patcher.validate({"schema": 1, "releases": [half]})
        with mock.patch("urllib.request.urlopen") as download:
            with self.assertRaisesRegex(ValueError, "repository"):
                patcher.fetch_patches(self.out, "https://example.invalid")
            (self.out / "releases.json").write_text(json.dumps(
                {"schema": 1, "releases": [patcher.browser_row(row)]}))
            with self.assertRaisesRegex(ValueError, "missing release publication"):
                patcher.fetch_patches(self.out, "owner/project")
            download.assert_not_called()

    def test_store_link_cannot_escape(self):
        row = self.prepare()
        path = patcher.patch_path(self.out, row)
        path.unlink()
        path.symlink_to(self.patch)
        with self.assertRaisesRegex(ValueError, "regular files"):
            patcher.fetch_patches(self.out, "owner/project")
