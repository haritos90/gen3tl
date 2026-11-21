import hashlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import zipfile
from http.client import HTTPResponse
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from test_roots import TOOLKIT_ROOT
from gen3tl.web import editorcheck, editorsave
from gen3tl import __version__
from gen3tl.cli import main as cli
from gen3tl.catalog import extract
from gen3tl.catalog.draftcatalog import source_digest
from gen3tl.web.editordata import editor_inputs, toolkit_fingerprint
from gen3tl.catalog.po import KEY_ONLY, SOURCE_FIELD, PoEntry, read, write
from gen3tl.profile.profile import Profile
from gen3tl.project import Manifest, Registry, registry_scope
from project_fixture import write_policy

urlopen = build_opener(ProxyHandler({})).open   # the local server, never a proxy


def refreshing(command) -> bool:
    """A subprocess call of the editor data command."""
    return any(command[i:i + 2] == ["web", "data"] for i in range(len(command)))


class LocalDraftTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.catalog, self.editor = self.root / "po", self.root / "view"
        self.header = {"Project-Id-Version": "fixture", "Language": "fr",
                       "Content-Type": "text/plain; charset=UTF-8"}
        self.entry = PoEntry("s1", "Hello", "Bonjour", extracted=["context"],
                             translator=["review note", "source: reference"], references=["0x10"],
                             flags=["c-format"], previous_msgid="Earlier")
        write(self.catalog / "one.po", [self.entry], self.header)
        write(self.catalog / "two.po", [PoEntry("s2", "Goodbye", "Au revoir")], self.header)
        self.editor.mkdir()
        (self.editor / "index.json").write_text('{"previous":true}')
        self.store = editorsave.DraftStore(self.catalog, self.editor, "fixture", "fr", "0" * 40, self.refresh)

    def refresh(self, catalog, destination):
        destination.mkdir()
        doc = {"profile": "fixture", "language": "fr", "rom": {"sha1": "0" * 40},
               "chunks": [{"file": str(catalog / "one.po")}],
               "translations": {e.msgctxt: e.msgstr for p in catalog.glob("*.po") for e in read(p)[1]}}
        (destination / "index.json").write_text(json.dumps(doc))

    def document(self, **change):
        record = {"id": "s1", "source_text": "Hello", "base": "Bonjour", "translation": "Salut"}
        return {"profile": "fixture", "language": "fr", "rom_sha1": "0" * 40,
                "entries": [{**record, **change}]}

    def originals(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def test_save_preserves_metadata_and_updates_preview(self):
        second = (self.catalog / "two.po").read_bytes()
        result = self.store.save(self.document())
        self.assertEqual(result["saved"], 1)
        header, (entry,) = read(self.catalog / "one.po")
        self.assertEqual(header, self.header)
        self.assertEqual(entry.msgstr, "Salut")
        self.assertEqual(entry.flags, ["c-format"])   # approved before, approved after
        for field in ("translator", "extracted", "references", "previous_msgid"):
            self.assertEqual(getattr(entry, field), getattr(self.entry, field))
        self.assertEqual(entry.source_note, "reference")
        self.assertEqual((self.catalog / "two.po").read_bytes(), second)
        self.assertEqual(json.loads((self.editor / "index.json").read_text())["translations"]["s1"], "Salut")
        self.assertEqual(json.loads((self.editor / "index.json").read_text())["chunks"][0]["file"],
                         str((self.catalog / "one.po").resolve()))

    def test_rejects_identity_stale_duplicate_and_invalid_text_without_writes(self):
        before = self.originals()
        docs = [dict(self.document(), profile="different"), self.document(base="stale"),
                self.document(source_text="stale"), self.document(id="unknown"),
                self.document(translation="bad\x00text"), self.document(translation=None),
                dict(self.document(), entries=self.document()["entries"] * 2)]
        for doc in docs:
            with self.subTest(doc=doc), self.assertRaises(ValueError):
                self.store.save(doc)
            self.assertEqual(self.originals(), before)

    def test_refresh_failure_keeps_catalog_and_preview(self):
        before = self.originals()
        def fail(*args):
            raise ValueError("measurement failed")
        self.store.refresh = fail
        with self.assertRaisesRegex(ValueError, "measurement failed"):
            self.store.save(self.document())
        self.assertEqual(self.originals(), before)

    def test_a_translated_or_new_text_is_saved_translated(self):
        for flags, base in ((["fuzzy"], "Bonjour"), ([], ""), (["fuzzy"], "")):
            with self.subTest(flags=flags, base=base):
                write(self.catalog / "one.po", [PoEntry("s1", "Hello", base, flags=list(flags))], self.header)
                self.assertEqual(self.store.save(self.document(base=base))["saved"], 1)
                entry = read(self.catalog / "one.po")[1][0]
                self.assertEqual((entry.msgstr, entry.flags), ("Salut", ["fuzzy"]))

    def test_state_changes_keep_an_approved_entry_approved(self):
        for before, after in (("original", "empty"), ("empty", "original"), ("text", "empty"),
                              ("empty", "text"), ("text", "original"), ("original", "text")):
            with self.subTest(before=before, after=after):
                base = "old" if before == "text" else ""
                target = "new" if after == "text" else ""
                write(self.catalog / "one.po", [PoEntry("s1", "Hello", base,
                      flags=["c-format"] + (["empty"] if before == "empty" else []))], self.header)
                doc = self.document(base=base, base_state=before, translation=target, state=after)
                self.assertEqual(self.store.save(doc)["saved"], 1)
                entry = read(self.catalog / "one.po")[1][0]
                self.assertEqual((entry.msgstr, entry.state, entry.fuzzy), (target, after, before == "original"))
                self.assertIn("c-format", entry.flags)
                self.assertEqual(self.store.save(self.document(base=target, base_state=after,
                                  translation=target, state=after))["saved"], 0)

    def test_stale_blank_state_and_invalid_states_leave_files_untouched(self):
        write(self.catalog / "one.po", [PoEntry("s1", "Hello", flags=["empty"])], self.header)
        before = self.originals()
        with self.assertRaises(editorsave.SaveConflict):
            self.store.save(self.document(base="", base_state="original", translation="", state="original"))
        for change in ({"state": "off"}, {"state": "empty"}, {"state": "text", "translation": ""},
                       {"base_state": "text"}, {"base_state": None}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.store.save(self.document(**{"base": "", **change}))
        self.assertEqual(self.originals(), before)

    def test_off_save_preserves_key_only_catalogs(self):
        header = {**self.header, SOURCE_FIELD: KEY_ONLY}
        write(self.catalog / "one.po", [PoEntry("ending", "ending")], header)
        self.store.source = [PoEntry("ending", "Suffix")]
        self.store.save(self.document(id="ending", source_text="Suffix", base="", base_state="original",
                                      translation="", state="empty"))
        actual_header, (entry,) = read(self.catalog / "one.po")
        self.assertEqual(actual_header, header)
        self.assertEqual((entry.msgctxt, entry.msgid, entry.msgstr), ("ending", "ending", ""))
        self.assertEqual((entry.state, entry.fuzzy, entry.keyed), ("empty", True, True))

    def test_concurrent_edit_is_preserved(self):
        def edit_other(catalog, destination):
            self.refresh(catalog, destination)
            write(self.catalog / "two.po", [PoEntry("s2", "Goodbye", "Changed externally")], self.header)
        self.store.refresh = edit_other
        with self.assertRaises(editorsave.SaveConflict):
            self.store.save(self.document())
        self.assertEqual(read(self.catalog / "one.po")[1][0].msgstr, "Bonjour")
        self.assertEqual(read(self.catalog / "two.po")[1][0].msgstr, "Changed externally")
        self.assertEqual(json.loads((self.editor / "index.json").read_text()), {"previous": True})

    def test_failed_publication_restores_both_files(self):
        before = self.originals()
        replace = editorsave.os.replace
        def fail(source, destination):
            if Path(source).name == "view" and Path(source).parent.name.startswith(".editor-save-"):
                raise OSError("publication failed")
            return replace(source, destination)
        doc = self.document()
        doc["entries"].append({"id": "s2", "source_text": "Goodbye", "base": "Au revoir", "translation": "Adieu"})
        with patch.object(editorsave.os, "replace", side_effect=fail), self.assertRaises(OSError):
            self.store.save(doc)
        self.assertEqual(self.originals(), before)

    def test_empty_flag_is_removed_when_text_returns(self):
        write(self.catalog / "one.po", [PoEntry("s1", "Hello", "", flags=["empty"])], self.header)
        self.store.save(self.document(base=""))
        self.assertEqual(read(self.catalog / "one.po")[1][0].flags, [])

    def test_failed_rollback_retains_recovery_copy(self):
        original = (self.catalog / "one.po").read_bytes()
        replace = editorsave.os.replace
        count = 0
        def fail_after_backup(source, destination):
            nonlocal count
            count += 1
            if count > 1:
                raise OSError("persistent failure")
            return replace(source, destination)
        with patch.object(editorsave.os, "replace", side_effect=fail_after_backup):
            with self.assertRaisesRegex(OSError, "recovery files preserved"):
                self.store.save(self.document())
        copies = list(self.root.glob(".editor-save-*/backup/one.po"))
        self.assertEqual(len(copies), 1)
        self.assertEqual(copies[0].read_bytes(), original)

    def test_rollback_preserves_newer_external_edits(self):
        replace = editorsave.os.replace
        def change_after_install(source, destination):
            result = replace(source, destination)
            if Path(source).parent.name == "po" and ".editor-save-" in str(source):
                write(self.catalog / "one.po", [PoEntry("s1", "Hello", "External review")], self.header)
            return result
        with patch.object(editorsave.os, "replace", side_effect=change_after_install):
            with self.assertRaisesRegex(OSError, "recovery files preserved"):
                self.store.save(self.document())
        self.assertEqual(read(self.catalog / "one.po")[1][0].msgstr, "External review")
        copies = list(self.root.glob(".editor-save-*/backup/one.po"))
        self.assertEqual(len(copies), 1)
        self.assertEqual(read(copies[0])[1][0].msgstr, "Bonjour")

    def test_http_session_and_origin_protect_writes(self):
        web = self.root / "web"
        web.mkdir()
        (web / "index.html").write_text("fixture")
        server = editorsave.local_server(web, self.editor, self.store, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_address[1]}"
        with urlopen(url + "/api/session") as response:
            session = json.load(response)
        headers = {"Content-Type": "application/json", "Origin": url, "X-Editor-Token": session["token"]}
        def send(changes=None, body=None):
            request = Request(url + "/api/save", data=body or json.dumps(self.document()).encode(),
                              headers={**headers, **(changes or {})})
            return urlopen(request)
        for changed in ({"Origin": "https://example.org"}, {"X-Editor-Token": "bad"}, {"Host": "example.org"}):
            with self.subTest(changed=changed), self.assertRaises(HTTPError) as error:
                send(changed)
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
        with self.assertRaises(HTTPError) as error:
            send(body=b'{"profile":"fixture","profile":"other"}')
        self.assertEqual(error.exception.code, 400)
        error.exception.close()
        with send() as response:
            self.assertEqual(json.load(response)["saved"], 1)
        with self.assertRaises(HTTPError) as error:
            send()
        self.assertEqual(error.exception.code, 409)
        error.exception.close()

    def test_request_thread_rejects_pack_owned_rows_without_writes(self):
        self.store.effective_profile = Profile({"keyboard": {
            "row_bindings": {"upper": "s1"}, "rows": {"upper": "ABC"}}}, self.root / "fixture.toml")
        before = self.originals()
        server = editorsave.local_server(self.root, self.editor, self.store, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_address[1]}"
        request = Request(url + "/api/save", data=json.dumps(self.document()).encode(),
                          headers={"Content-Type": "application/json", "Origin": url,
                                   "X-Editor-Token": self.store.token})
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        self.assertEqual(error.exception.code, 400)
        self.assertIn("keyboard entries are read-only", error.exception.read().decode())
        error.exception.close()
        self.assertEqual(self.originals(), before)


class LocalCommandTest(unittest.TestCase):
    def test_data_key_follows_overlay_fragments_and_their_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = root / "game"
            overlay = profile / "overlay"
            (overlay / "nested").mkdir(parents=True)
            (profile / "profile.toml").write_text('[discovery]\nschema = 1\n', encoding="utf-8")
            first, second = overlay / "first.toml", overlay / "nested/second.toml"
            order = overlay / "order.txt"
            first.write_text('[rom]\nsha1 = "ab"\n', encoding="utf-8")
            second.write_text('[rom]\nsize = 8\n', encoding="utf-8")
            order.write_text("first.toml\nnested/second.toml\n", encoding="utf-8")
            key = lambda: editorsave.data_key(root / "po", profile, "fixture")
            before = key()
            second.write_text('[rom]\nsize = 16\n', encoding="utf-8")
            self.assertNotEqual(key(), before)
            before = key()
            order.write_text("nested/second.toml\nfirst.toml\n", encoding="utf-8")
            self.assertNotEqual(key(), before)

    def test_data_key_follows_the_catalog_the_profile_and_the_toolkit(self):
        """The key of the generated data."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog, profile = root / "po", root / "fixture.toml"
            header = {"Project-Id-Version": "fixture", "Language": "fr"}
            write(catalog / "one.po", [PoEntry("s1", "Hello", "Bonjour")], header)
            write(catalog / "two.po", [PoEntry("s2", "Goodbye", "Au revoir")], header)
            profile.write_text("[rom]\n")
            key = editorsave.data_key(catalog, profile, "0.1.0+abc")
            self.assertEqual(editorsave.data_key(catalog, profile, "0.1.0+abc"), key)
            self.assertNotEqual(editorsave.data_key(catalog, profile, "0.1.0+def"), key)
            profile.write_text("[rom]\nname = 'x'\n")
            changed = editorsave.data_key(catalog, profile, "0.1.0+abc")
            self.assertNotEqual(changed, key)
            write(catalog / "two.po", [PoEntry("s2", "Goodbye", "Adieu")], header)
            self.assertNotEqual(editorsave.data_key(catalog, profile, "0.1.0+abc"), changed)
            # every other input counts; a missing one is absent.
            # the toolkit counts by fingerprint.
            snapshot, rules = root / "editor-inputs.json", root / "rules.tsv"
            snapshot.write_text("{}")
            base = editorsave.data_key(catalog, profile, "fp", [snapshot, rules])
            self.assertEqual(editorsave.data_key(catalog, profile, "fp", [snapshot, rules]), base)
            snapshot.write_text('{"fonts": 1}')
            self.assertNotEqual(editorsave.data_key(catalog, profile, "fp", [snapshot, rules]), base)
            rules.write_text("a\tb\n")
            with_rules = editorsave.data_key(catalog, profile, "fp", [snapshot, rules])
            self.assertNotEqual(with_rules, editorsave.data_key(catalog, profile, "fp", [snapshot]))
            graphics = root / "graphics"
            graphics.mkdir()
            (graphics / "a.json").write_text("1")
            with_dir = editorsave.data_key(catalog, profile, "fp", [snapshot, rules, graphics])
            (graphics / "a.json").write_text("2")
            self.assertNotEqual(editorsave.data_key(catalog, profile, "fp", [snapshot, rules, graphics]), with_dir)

    def test_background_refresh_installs_discards_and_keeps(self):
        """A stable catalog's result is installed; a changed one is discarded and redone."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, scratch = root / "view", root / "build"
            scratch.mkdir()
            target.mkdir()
            (target / "index.json").write_text("old")
            calls, lines = [], []
            log = lambda label, value: lines.append((label, str(value)))
            lock = threading.RLock()

            def refresh(catalog, destination):
                calls.append(destination)
                destination.mkdir()
                (destination / "index.json").write_text(f"new {len(calls)}")

            self.assertTrue(editorsave.background_refresh(refresh, root / "po", target, lambda: "k", lock, scratch,
                                                          log=log, after=lambda: lines.append("after")))
            self.assertEqual((target / "index.json").read_text(), "new 1")
            self.assertEqual(lines[0], "after")
            self.assertEqual(lines[1], ("data", "regenerated"))
            self.assertEqual(lines[2][0], "elapsed")
            self.assertRegex(lines[2][1], r"^\d+ s$")
            self.assertEqual(sorted(p.name for p in scratch.iterdir()), [])   # staging cleaned up
            # catalog changed mid-generation: the second result is installed
            keys = iter(["a", "b", "b", "b"])
            calls.clear()
            lines.clear()
            self.assertTrue(editorsave.background_refresh(refresh, root / "po", target, lambda: next(keys), lock, scratch,
                                                          log=log))
            self.assertEqual(len(calls), 2)
            self.assertEqual((target / "index.json").read_text(), "new 2")
            self.assertEqual(lines[0], ("data", "retrying"))
            # a failing generation keeps the previous data
            def broken(catalog, destination):
                raise ValueError("no snapshot")
            lines.clear()
            self.assertFalse(editorsave.background_refresh(broken, root / "po", target, lambda: "k", lock, scratch,
                                                           log=log))
            self.assertEqual((target / "index.json").read_text(), "new 2")
            self.assertEqual(lines, [("data", "failed"), ("error", "no snapshot")])
            # a catalog that keeps changing gives up after the attempts
            self.assertFalse(editorsave.background_refresh(refresh, root / "po", target, iter(range(100)).__next__,
                                                           lock, scratch, log=log, attempts=2))
            self.assertEqual(lines[-1], ("data", "stale"))

    def test_an_unchanged_catalog_is_served_without_a_refresh(self):
        """A second start skips web data; a changed PO file runs it again."""
        self.run_project(False, starts=3)

    def test_old_schema_is_rebuilt_before_serving_with_a_current_stamp(self):
        self.run_project(False, starts=2, old_schema=True)

    def test_selected_project_saves_without_rom_and_refreshes_actual_editor(self):
        self.run_project(False)

    def test_nested_project_can_refresh_shared_editor(self):
        self.run_project(True)

    def test_refresh_writes_only_language_data_and_serves_shared_files(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = root / "fixture.toml"
            profile.write_text('[rom]\nsha1 = "' + "0" * 40 + '"\n[support]\nstage = "catalog"\n')
            write_policy(root, Profile.load(profile), "fr")
            manifest = Manifest(root / "project.toml", "fixture", "0" * 40, __version__, ("fr",), "fr")
            manifest.path.write_text(manifest.text())
            (root / "projects.toml").write_text(
                '[defaults]\nprofile="fixture"\nlanguage="fr"\n'
                '[[project]]\nprofile="fixture"\nlanguage="fr"\nroot="."\npolicy="policy.toml"\n')
            catalog = root / "translation" / manifest.default_language / "po"
            header = {"Project-Id-Version": "fixture", "Language": "fr", SOURCE_FIELD: KEY_ONLY}
            items = [PoEntry("s1", "Hello", "")]
            extract.write(root / "build/catalog.pot", items, Profile.load(profile))
            write(catalog / "source.po", items, header)
            (catalog.parent / "catalog.json").write_text(json.dumps({"profile": "fixture", "language": "fr",
                "rom_sha1": "0" * 40, "source_sha256": source_digest(items)}))
            dataset = root / "data/pokedex"
            (dataset / "sprites").mkdir(parents=True)
            (dataset / "pokedex.json").write_text("{}")
            sprite = b"\x89PNG" + bytes(100)
            (dataset / "sprites/1.png").write_bytes(sprite)
            release = b"UPS1" + bytes(100)
            patch_path = Path("fixture/fr/1.0.0.ups")
            release_file = root / "build/patches" / patch_path
            release_file.parent.mkdir(parents=True)
            release_file.write_bytes(release)
            row = {"profile": "fixture", "profile_name": "Fixture", "language": "fr", "version": "1.0.0",
                   "source_sha1": "0" * 40, "source_size": 100, "result_sha1": "1" * 40, "result_size": 100,
                   "patch": patch_path.as_posix(), "patch_sha256": hashlib.sha256(release).hexdigest(),
                   "patch_size": len(release), "output": "fixture-fr-1.0.0.gba"}
            (root / "releases").mkdir()
            (root / "releases/releases.json").write_text(json.dumps({"schema": 1, "releases": [row]}))
            shared = {Path("releases") / patch_path: release, Path("pokedex/fixture/sprites/1.png"): sprite}
            generated_files = []
            run = subprocess.run

            def measured(command, **kwargs):
                result = run(command, **kwargs)
                if refreshing(command):
                    stage = Path(command[command.index("--out") + 1]).parent
                    generated_files.append({p.relative_to(stage) for p in stage.rglob("*") if p.is_file()})
                return result

            test = self
            previous = {}

            class Server:
                server_address = ("127.0.0.1", 8001)

                def __init__(self, web, data, store, port):
                    self.web, self.data, self.store = web, data, store

                def shared_state(self):
                    paths = [*shared, Path("releases/releases.json"), Path("gen3tl-check.zip")]
                    return {p: ((self.data / p).stat().st_mtime_ns, (self.data / p).stat().st_ino,
                                (self.data / p).read_bytes()) for p in paths}

                def serve_forever(self):
                    if previous:
                        test.assertEqual(self.shared_state(), previous)
                    else:
                        previous.update(self.shared_state())
                        self.store.save({"profile": "fixture", "language": "fr", "rom_sha1": "0" * 40,
                            "entries": [{"id": "s1", "source_text": "Hello", "base": "", "translation": "Salut"}]})
                        test.assertEqual(self.shared_state(), previous)
                    bundle = self.data / "gen3tl-check.zip"
                    test.assertTrue(editorcheck.bundle_current(bundle))
                    manifest = json.loads((self.data / "languages.json").read_text())
                    test.assertEqual(manifest["bundle"], bundle.name)
                    served = {**shared, Path(manifest["bundle"]): bundle.read_bytes()}
                    for path, expected in served.items():
                        request = (f"GET /data/{path.as_posix()} HTTP/1.0\r\nHost: 127.0.0.1:8001\r\n\r\n")
                        connection = Mock()
                        connection.makefile.return_value = io.BytesIO(request.encode())
                        output = io.BytesIO()
                        connection.sendall.side_effect = output.write
                        editorsave.LocalHandler(connection, ("127.0.0.1", 12345), self,
                                                directory=str(self.web), data=self.data, store=self.store)
                        response = HTTPResponse(Mock(makefile=Mock(return_value=io.BytesIO(output.getvalue()))))
                        response.begin()
                        test.assertEqual(response.status, 200)
                        test.assertEqual(response.read(), expected)
                        response.close()

                def server_close(self):
                    pass

            source = str(TOOLKIT_ROOT / "src")
            selection = ["--project-root", str(root), "--profile", str(profile), "--lang", "fr"]
            argv = selection + ["web", "server", "start", "--page", "editor", "--foreground"]
            with patch.object(editorsave, "local_server", Server), \
                    patch.dict(os.environ, {"PYTHONPATH": source}), patch("subprocess.run", side_effect=measured):
                self.assertEqual(cli.main(argv), 0)
                items[0].msgstr = "Salut!"
                write(catalog / "source.po", items, header)
                self.assertEqual(cli.main(argv), 0)
            index = json.loads((root / "build/editor/fixture/fr/index.json").read_text())
            self.assertEqual(index["list"][0]["translation"], "Salut!")
            self.assertEqual(len(generated_files), 3)
            for files in generated_files:
                self.assertIn(Path("view/index.json"), files)
                # the language and the game parts it names
                self.assertTrue(all(p.parts[0] == "view" or p.parent == Path("shared") for p in files), files)
                self.assertFalse(any("releases" in p.parts or "sprites" in p.parts for p in files), files)
            self.assertFalse(any(p.is_symlink() for p in root.rglob("*")))
            self.assertEqual(list((root / "build").glob(".editor-*")), [])
            bundle = root / "build/editor/gen3tl-check.zip"
            with patch("gen3tl.web.editorserve.preview_server") as preview:
                preview.return_value.server_address = ("127.0.0.1", 8001)
                for content in (None, b"separately assembled"):
                    with self.subTest(site_bundle=content):
                        if content is None:
                            bundle.unlink()
                        else:
                            bundle.write_bytes(content)
                        self.assertEqual(cli.main(selection + ["web", "server", "start", "--page", "site",
                                                               "--foreground"]), 0)
                        self.assertEqual(bundle.read_bytes() if bundle.exists() else None, content)

    def run_project(self, nested, starts=1, old_schema=False):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp)
            root = registry / "projects/fixture" if nested else registry
            root.mkdir(parents=True, exist_ok=True)
            profile = root / "fixture.toml"
            profile.write_text('[rom]\nsha1 = "' + "0" * 40 + '"\n[support]\nstage = "catalog"\n')
            write_policy(root, Profile.load(profile), "fr")
            workspace = "projects/fixture" if nested else "."
            registry_text = ('[defaults]\nprofile="fixture"\nlanguage="fr"\n'
                '[[project]]\nprofile="fixture"\nlanguage="fr"\n'
                f'root="{workspace}"\npolicy="policy.toml"\n')
            if nested:
                registry_text += '[[project]]\nprofile="parent"\nlanguage="fr"\nroot="."\npolicy="policy.toml"\n'
            (registry / "projects.toml").write_text(registry_text)
            items = [PoEntry("s1", "Hello", "", translator=["source: fixture"], flags=["fuzzy"])]
            catalog = root / "translation/po"
            write(catalog / "source.po", items, {"Project-Id-Version": "fixture", "Language": "fr"})
            (root / "translation/catalog.json").write_text(json.dumps({"profile": "fixture", "language": "fr",
                "rom_sha1": "0" * 40, "source_sha256": source_digest(items)}))

            class Server:
                server_address = ("127.0.0.1", 0)

                def __init__(self, web, data, store, port):
                    self.store = store

                def serve_forever(self):
                    self.store.save({"profile": "fixture", "language": "fr", "rom_sha1": "0" * 40,
                        "entries": [{"id": "s1", "source_text": "Hello", "base": "", "translation": "Salut"}]})

                def server_close(self):
                    pass

            source = str(TOOLKIT_ROOT / "src")
            argv = ["--project-root", str(registry), "--profile", str(profile), "--lang", "fr",
                    "web", "server", "start", "--page", "editor", "--foreground", "--port", "0"]
            with patch.object(editorsave, "local_server", Server), patch.dict(os.environ, {"PYTHONPATH": source}):
                self.assertEqual(cli.main(argv), 0)
                if starts > 1:   # a second start keeps the data
                    class Quiet(Server):
                        def serve_forever(self):
                            if hasattr(self, "refresh"):   # the background refresh ends first
                                self.refresh.join()
                    view = registry / "build/editor/fixture/fr"
                    bundle = registry / "build/editor/gen3tl-check.zip"
                    self.assertTrue(editorcheck.bundle_current(bundle))
                    bundle_stat = bundle.stat()
                    self.assertTrue((view / editorsave.STAMP).is_file())
                    if old_schema:
                        index_path = view / "index.json"
                        index = json.loads(index_path.read_text())
                        index["schema"] = 1
                        index_path.write_text(json.dumps(index))
                        test = self

                        class Current(Quiet):
                            def serve_forever(self):
                                from gen3tl.web.editordata import SCHEMA
                                test.assertEqual(json.loads(index_path.read_text())["schema"], SCHEMA)
                                test.assertFalse(hasattr(self, "refresh"))

                        with patch.object(editorsave, "local_server", Current), \
                                patch("subprocess.run", wraps=subprocess.run) as run:
                            self.assertEqual(cli.main(argv), 0)
                            test.assertEqual(sum(refreshing(c.args[0]) for c in run.call_args_list), 1)
                        return
                    with patch.object(editorsave, "local_server", Quiet), \
                            patch("subprocess.run", wraps=subprocess.run) as run, \
                            patch.object(editorcheck, "write_bundle", wraps=editorcheck.write_bundle) as write_check:
                        self.assertEqual(cli.main(argv), 0)
                        write_check.assert_not_called()
                        self.assertEqual(bundle.stat().st_mtime_ns, bundle_stat.st_mtime_ns)
                        refreshes = [c for c in run.call_args_list if refreshing(c.args[0])]
                        self.assertEqual(refreshes, [])
                        for missing in (True, False):
                            with self.subTest(missing=missing):
                                if missing:
                                    bundle.unlink()
                                else:
                                    initializer = (Path(editorcheck.__file__).parents[1] / "__init__.py").read_bytes()
                                    with zipfile.ZipFile(bundle, "w") as archive:
                                        archive.writestr("gen3tl/__init__.py",
                                                         initializer.replace(__version__.encode(), b"0.0.0"))
                                self.assertEqual(cli.main(argv), 0)
                                write_check.assert_called_once()
                                write_check.reset_mock()
                                self.assertTrue(editorcheck.bundle_current(bundle))
                                self.assertEqual([c for c in run.call_args_list if refreshing(c.args[0])], [])
                        write(catalog / "source.po", [PoEntry("s1", "Hello", "Salut!", translator=["source: fixture"])],
                              {"Project-Id-Version": "fixture", "Language": "fr"})
                        self.assertEqual(cli.main(argv), 0)
                        refreshes = [c for c in run.call_args_list if refreshing(c.args[0])]
                        self.assertEqual(len(refreshes), 1)
                        self.assertEqual(json.loads((view / "index.json").read_text())["list"][0]["translation"], "Salut!")
                        project = Registry.load(registry).resolve("fixture", "fr")
                        with registry_scope(registry), project.activate():
                            inputs = editor_inputs(project, Profile.load(profile), "fr")
                        self.assertEqual((view / editorsave.STAMP).read_text(),
                                         editorsave.data_key(catalog, profile, toolkit_fingerprint(), inputs))
                    return
            saved = read(catalog / "source.po")[1][0]
            self.assertEqual((saved.msgstr, saved.fuzzy, saved.source_note), ("Salut", True, "fixture"))
            view = registry / "build/editor/fixture/fr"
            index = json.loads((view / "index.json").read_text())
            self.assertEqual(index["list"][0]["translation"], "Salut")
            self.assertEqual(index["chunks"][0]["file"], "translation/po/source.po")   # the file under its catalog


if __name__ == "__main__":
    unittest.main()
