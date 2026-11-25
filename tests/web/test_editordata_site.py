from test_roots import TOOLKIT_ROOT
"""Editordata site checks and fixtures."""

import sys
from pathlib import Path
import json
import os
import shutil
import tempfile
import unittest

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl import __version__
from gen3tl.web import editordata
from gen3tl.project import registry_scope
from project_fixture import workspace_project
from editordata_support import TOOLKIT_RE, setUpModule
from test_roots import TOOLKIT_ROOT as ROOT


class SiteRegistryTest(unittest.TestCase):
    """registry.json for the site: validation, one game, the command."""

    DOC = {"languages": [{"code": "ru", "profile": "b"}, {"code": "de", "profile": "b"}],
           "profiles": [{"id": "a", "name": "A", "support": "catalog", "engine": "emerald"},
                        {"id": "b", "name": "B", "support": "translation", "engine": "firered-cfru"},
                        {"id": "c", "name": "C", "support": "unknown"}]}

    def test_generated_metadata_fixtures(self):
        fixtures = json.loads((ROOT / "web/test/registry-fixtures.json").read_text(encoding="utf-8"))
        for fixture in fixtures:
            with self.subTest(name=fixture["name"]):
                self.assertEqual(editordata.registry_of(fixture["manifest"]), fixture["registry"])
                selected = editordata.registry_of(fixture["manifest"], "../data/", ["sample"])
                self.assertEqual(selected.get("site"), fixture["registry"].get("site"))
                self.assertEqual(selected["games"][0], dict(fixture["registry"]["games"][0], data="../data/"))

    def test_metadata_generation_from_configuration(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "projects.toml").write_text(
                '[defaults]\nprofile = "sample"\nlanguage = "xx"\n'
                '[[project]]\nprofile = "sample"\nlanguage = "xx"\nroot = "."\npolicy = "policy.toml"\n'
                '[site]\nname = "Example site"\nhome = "../"\n', encoding="utf-8")
            profile = root / "sample.toml"
            profile.write_text('[rom]\nname = "Sample game"\nsha1 = "' + 'a' * 40 + '"\n'
                               'source_language = "pt-BR"\n[engine]\nfamily = "sample"\nlabel = "Sample engine"\n'
                               '[links]\nproject = "https://example.org/game"\n'
                               '[compatibility]\nsaves = "Declared save support"\n'
                               '[landing]\neditor = "Declared editor measurement"\n'
                               'review_title = "Public review"\nreview = "Declared review policy"\n', encoding="utf-8")
            from gen3tl.catalog.po import PoEntry, write
            write(root / "translation" / "interface-labels.po",
                  [PoEntry("site:Declared review policy", "Declared review policy", "Own review policy", flags=["fuzzy"]),
                   PoEntry("site:Public review", "Public review", ""),
                   PoEntry("site:Removed review", "Removed review", "Removed")],
                  {"Project-Id-Version": "sample", "Language": "xx", "X-Group": "interface-labels"})
            with registry_scope(root), patch("gen3tl.profile.profile.profile_paths", return_value=[profile]):
                doc = editordata.write_languages(root)
            self.assertEqual(doc["site"], {"name": "Example site", "home": "../"})
            game = next(p for p in doc["profiles"] if p["id"] == "sample")
            registry = json.loads((root / "registry.json").read_text(encoding="utf-8"))
            self.assertEqual(registry, editordata.registry_of(doc))
            expected = {"source_language": "pt-BR", "engine_label": "Sample engine",
                        "links": {"project": "https://example.org/game"},
                        "compatibility": {"saves": "Declared save support"},
                        "landing": {"editor": "Declared editor measurement", "review_title": "Public review",
                                    "review": "Declared review policy"},
                        "interface_labels": {"xx": "sample/xx/interface-labels.json"}}
            for key, value in expected.items():
                self.assertEqual(game[key], value)
                self.assertEqual(registry["games"][0][key], value)
            self.assertEqual(json.loads((root / "sample/xx/interface-labels.json").read_text(encoding="utf-8")),
                             {"schema": 1, "language": "xx", "strings": {"Declared review policy": "Own review policy"}})
            (root / "translation" / "interface-labels.po").unlink()
            with registry_scope(root), patch("gen3tl.profile.profile.profile_paths", return_value=[profile]):
                doc = editordata.write_languages(root)
            self.assertEqual(next(p for p in doc["profiles"] if p["id"] == "sample")["interface_labels"], {})

    def test_invalid_metadata(self):
        for key, values in {
            "site": [None, [], "site", {"unknown": "x"}, {"name": " "}, {"brand": False},
                     {"home": "javascript:alert(1)"}, {"repository": "owner"}, {"repository": False},
                     {"new_language_url": "data:text/html,x"}, {"licenses": {}},
                     {"licenses": [None]}, {"licenses": [{"label_key": "License"}]},
                     {"licenses": [{"url": "license"}]},
                     {"licenses": [{"label_key": " ", "url": "license"}]},
                     {"licenses": [{"label_key": "License", "url": "file:///license"}]},
                     {"licenses": [{"label_key": "License", "url": "license", "other": True}]}],
            "landing": [None, [], "yes", {"unknown": "x"}, {"editor": True}, {"review": " "}],
            "engine_label": [None, False, [], " "],
            "links": [None, [], "link", {"unknown": "x"}, {"project": 1}, {"new_language": False}],
            "compatibility": [None, [], "yes", {"unknown": "x"}, {"saves": True},
                              {"link_play": " "}, {"changed_bytes": ["text"]}],
            "interface_labels": [None, [], "labels.json", {"r u": "ru.json"}, {"ru": True},
                                 {"ru": "javascript:alert(1)"}, {"ru": "../a b"}],
        }.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    doc = editordata.registry_of(self.DOC)
                    (doc if key == "site" else doc["games"][0])[key] = value
                    with self.assertRaises(ValueError):
                        editordata.validate_registry(doc)
        for url in ("", " ", "javascript:alert(1)", "data:text/html,x", "file:///tmp/a", "//example.org/a",
                    "https:example.org", "https://", "https://[invalid", "https://example.org:99999",
                    "https://example.org/a\nb", "../a b", "..\\a"):
            for key in ("project", "new_language", "content_license"):
                with self.subTest(key=key, url=url):
                    doc = editordata.registry_of(self.DOC)
                    doc["games"][0]["links"] = {key: url}
                    with self.assertRaisesRegex(ValueError, "invalid links"):
                        editordata.validate_registry(doc)

    def test_registry_of_every_game_and_of_one(self):
        doc = editordata.registry_of(self.DOC)
        self.assertEqual([g["id"] for g in doc["games"]], ["b", "a", "c"])
        self.assertEqual(doc["default"], "b")
        self.assertEqual(doc["games"][0]["languages"], ["de", "ru"])
        self.assertEqual(doc["games"][2]["stage"], "legacy")
        self.assertEqual(doc["games"][2]["engine"], "unspecified")
        one = editordata.registry_of(self.DOC, "https://example.org/b/data/", ["a"])
        self.assertEqual(one, {"schema": 1, "default": "a", "games": [
            {"id": "a", "name": "A", "stage": "catalog", "engine": "emerald", "languages": [],
             "data": "https://example.org/b/data/"}]})
        with self.assertRaisesRegex(ValueError, "no profile x"):
            editordata.registry_of(self.DOC, only=["x"])

    def test_registry_carries_a_declared_source_language(self):
        doc = json.loads(json.dumps(self.DOC))
        doc["profiles"][0]["source_language"] = "pt-BR"
        registry = editordata.registry_of(doc, only=["a"])
        self.assertEqual(registry["games"][0]["source_language"], "pt-BR")
        registry["games"][0]["source_language"] = "../en"
        with self.assertRaisesRegex(ValueError, "invalid source language"):
            editordata.validate_registry(registry)

    def test_validation(self):
        good = editordata.registry_of(self.DOC)
        for change, message in ((lambda d: d.update(schema=2), "schema"),
                                (lambda d: d.update(extra=1), "unknown keys"),
                                (lambda d: d.update(default="z"), "default game"),
                                (lambda d: d["games"][0].update(id="../x"), "invalid game id"),
                                (lambda d: d["games"].append(dict(d["games"][0])), "duplicate"),
                                (lambda d: d["games"][0].update(name=" "), "no name"),
                                (lambda d: d["games"][0].update(stage="released"), "unknown stage"),
                                (lambda d: d["games"][0].update(languages=["r u"]), "invalid languages"),
                                (lambda d: d["games"][0].update(data=None), "no data URL"),
                                (lambda d: d["games"][0].update(url="x"), "unknown keys")):
            doc = json.loads(json.dumps(good))
            change(doc)
            with self.assertRaisesRegex(ValueError, message):
                editordata.validate_registry(doc)

    def test_command_writes_a_project_registry(self):
        import contextlib
        import io

        from gen3tl.__main__ import main
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            selection = ["--project-root", tmp, "--profile", str(workspace_project(data))]
            manifest = dict(self.DOC, site={"name": "Example site", "repository": None})
            (data / "languages.json").write_text(json.dumps(manifest), encoding="utf-8")
            out = data / "site" / "registry.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main([*selection, "web", "registry", "--data", str(data), "--game", "b",
                                       "--data-url", "../", "--out", str(out)]), 0)
            doc = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual([g["id"] for g in doc["games"]], ["b"])
            self.assertEqual(doc["site"], manifest["site"])
            self.assertEqual(doc["games"][0]["data"], "../")
            self.assertNotIn("repository", doc["games"][0])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main([*selection, "web", "registry", "--data", str(data), "--repository", "account/name",
                                       "--out", str(out)]), 0)
            doc = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual({g["repository"] for g in doc["games"]}, {"account/name"})

    def test_submission_repository(self):
        doc = editordata.registry_of(self.DOC, repository="owner/repo.name")
        self.assertEqual({g["repository"] for g in doc["games"]}, {"owner/repo.name"})
        for value in ("owner", "owner/..", "-owner/repo", "owner/re po", 7):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "invalid repository"):
                editordata.registry_of(self.DOC, repository=value)


class ToolkitVersionTest(unittest.TestCase):
    """gen3tl --version and the editor data's toolkit version."""

    def test_cli_flag(self):
        import contextlib
        import io

        from gen3tl.__main__ import main
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as end:
            main(["--version"])
        self.assertEqual((end.exception.code, out.getvalue()), (0, f"version      {__version__}\n"))
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")   # SemVer 2.0.0 core

    @unittest.skipUnless(shutil.which("git"), "no git")
    def test_commit_metadata(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(editordata.toolkit_version(tmp), __version__)   # outside a checkout
            git = ["git", "-C", tmp, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
            subprocess.run(git + ["init", "-q"], check=True)
            (Path(tmp) / "a.txt").write_text("a", encoding="utf-8")
            subprocess.run(git + ["add", "a.txt"], check=True)
            subprocess.run(git + ["commit", "-q", "--no-verify", "-m", "a"], check=True)
            head = subprocess.run(git + ["rev-parse", "--short", "HEAD"], check=True, capture_output=True,
                                  text=True).stdout.strip()
            (Path(tmp) / "b.txt").write_text("b", encoding="utf-8")   # untracked: not dirty
            self.assertEqual(editordata.toolkit_version(tmp), f"{__version__}+{head}")
            (Path(tmp) / "a.txt").write_text("changed", encoding="utf-8")
            self.assertEqual(editordata.toolkit_version(tmp), f"{__version__}+{head}.dirty")

    def test_no_git(self):
        from unittest import mock
        with mock.patch("subprocess.run", side_effect=FileNotFoundError("git")):
            self.assertEqual(editordata.toolkit_version(), __version__)

    def test_this_checkout(self):
        self.assertRegex(editordata.toolkit_version(), TOOLKIT_RE)


class PreviewServerTest(unittest.TestCase):
    """gen3tl web server start --page site: web/ at /, the data at /data/."""

    def test_paths(self):
        import threading
        import urllib.error
        import urllib.request

        from gen3tl.web.editorserve import preview_server
        with tempfile.TemporaryDirectory() as tmp:
            web, data = Path(tmp) / "web", Path(tmp) / "data"
            web.mkdir()
            data.mkdir()
            (web / "index.html").write_text("page", encoding="utf-8")
            (web / "app.js").write_text("js", encoding="utf-8")
            (data / "languages.json").write_text("{}", encoding="utf-8")
            (data / "art.txt").write_text("glyph Ж width 6", encoding="utf-8")
            (Path(tmp) / "secret.txt").write_text("no", encoding="utf-8")
            server = preview_server(web, data, 0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # no proxy for localhost
            try:
                def get(path):
                    with direct.open(base + path) as r:
                        return r.read().decode(), r.headers["Content-Type"]
                self.assertEqual(get("/")[0], "page")
                self.assertEqual(get("/app.js"), ("js", "text/javascript"))
                self.assertEqual(get("/data/languages.json"), ("{}", "application/json"))
                self.assertEqual(get("/data/art.txt"), ("glyph Ж width 6", "text/plain; charset=utf-8"))
                for path in ("/data/../secret.txt", "/../secret.txt", "/data/%2e%2e/secret.txt"):
                    with self.assertRaises(urllib.error.HTTPError) as err:
                        get(path)
                    self.assertEqual(err.exception.code, 404)
                    err.exception.close()
            finally:
                server.shutdown()
                server.server_close()


class SiteAssembleTest(unittest.TestCase):
    """gen3tl web assemble: the pages and the data in one static tree."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        cwd = os.getcwd()
        os.chdir(self.root)   # no game project around the test
        self.addCleanup(os.chdir, cwd)
        self.web, self.data, self.out = self.root / "web", self.root / "data", self.root / "site"
        for path in ("index.html", "js/app.js", "data/pokedex/other/pokedex.json", "test/app.test.mjs", ".DS_Store"):
            (self.web / path).parent.mkdir(parents=True, exist_ok=True)
            (self.web / path).write_text(path, encoding="utf-8")
        for path in ("languages.json", "registry.json", "game/ru/index.json", "game/ru/interface-labels.json",
                     "game/ru/.data-key"):
            (self.data / path).parent.mkdir(parents=True, exist_ok=True)
            (self.data / path).write_text(path, encoding="utf-8")
        self.manifest = {"bundle": "gen3tl-check.zip",
                         "languages": [{"profile": "game", "index": "game/ru/index.json"}],
                         "profiles": [{"id": "game"}]}
        (self.data / "languages.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def assemble(self, **paths):
        from types import SimpleNamespace
        from unittest import mock

        from gen3tl.cli.web import site_assemble
        args = SimpleNamespace(**{"data": str(self.data), "out": str(self.out), **paths})
        with mock.patch("gen3tl.web.editorserve.WEB", self.web), mock.patch("builtins.print") as output:
            code = site_assemble(args)
        return code, " ".join(str(c) for c in output.call_args_list)

    def test_pages_and_data_without_tests_datasets_or_local_files(self):
        self.assertEqual(self.assemble()[0], 0)
        files = sorted(p.relative_to(self.out).as_posix() for p in self.out.rglob("*") if p.is_file())
        self.assertEqual(files, [f"assets/{__version__}/files.json", f"assets/{__version__}/js/app.js",
                                "data/game/ru/index.json", "data/game/ru/interface-labels.json", "data/languages.json",
                                "data/registry.json", "index.html",
                                "site-build.json"])
        self.assertEqual(json.loads((self.out / "data/languages.json").read_text(encoding="utf-8")), self.manifest)

    def test_leaves_out_an_unlisted_game(self):
        for path in ("other/ru/index.json", "pokedex/other/pokedex.json", "sources/other/catalog.json"):
            (self.data / path).parent.mkdir(parents=True, exist_ok=True)
            (self.data / path).write_text(path, encoding="utf-8")
        self.assertEqual(self.assemble()[0], 0)
        self.assertTrue((self.out / "data/game/ru/index.json").is_file())
        for path in ("other", "pokedex/other", "sources/other"):
            self.assertFalse((self.out / "data" / path).exists())

    def test_refuses_a_used_or_overlapping_directory_and_links(self):
        self.out.mkdir()
        (self.out / "old.html").write_text("old", encoding="utf-8")
        for paths, problem in (({}, "not an empty directory"), ({"out": str(self.data / "site")}, "overlaps"),
                               ({"data": str(self.root / "none")}, "missing")):
            code, output = self.assemble(**paths)
            self.assertEqual((code, problem in output), (1, True), problem)
        (self.data / "game/rom.gba").symlink_to(self.root / "elsewhere.gba")
        code, output = self.assemble(out=str(self.root / "new"))
        self.assertEqual((code, "a link" in output), (1, True))
        self.assertFalse((self.root / "new").exists())
        self.assertEqual([p.name for p in self.out.iterdir()], ["old.html"])
