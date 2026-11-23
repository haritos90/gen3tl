import json
import re
import tempfile
import unittest
from pathlib import Path

from gen3tl.web import editordata, sitecatalog
from gen3tl.catalog.po import PoEntry, write
from project_fixture import workspace_project
from editorstore_support import decoded

def catalog(code: str, entries: list[PoEntry], directory: Path) -> Path:
    path = directory / f"{code}.po"
    write(path, entries, {"Project-Id-Version": "site", "Language": code,
                          "Content-Type": "text/plain; charset=UTF-8"})
    return path

class ReaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_identity_and_keys(self):
        entries = [PoEntry("site:Patch", "Patch", "Патч"), PoEntry("site:Loading {game}…", "Loading {game}…", "")]
        path = catalog("xx", entries, self.dir)
        self.assertEqual([e.msgid for e in sitecatalog.load(path)], ["Patch", "Loading {game}…"])
        self.assertEqual(sitecatalog.strings(sitecatalog.load(path)), {"Patch": "Патч"})
        self.assertEqual(sitecatalog.page_doc("xx", entries),
                         {"schema": 1, "language": "xx", "strings": {"Patch": "Патч"}})
        for bad, message in (([PoEntry("page:Patch", "Patch")], "is not site:"),
                             ([PoEntry("site:A", "A"), PoEntry("site:A", "A")], "duplicate key")):
            with self.subTest(message=message), self.assertRaisesRegex(sitecatalog.SiteCatalogError, message):
                sitecatalog.load(catalog("xx", bad, self.dir))
        other = self.dir / "yy.po"
        path.rename(other)
        with self.assertRaisesRegex(sitecatalog.SiteCatalogError, "header"):
            sitecatalog.load(other)

    def test_write_gives_the_pages_their_strings_and_the_editor_a_project(self):
        catalog("xx", [PoEntry("site:Patch", "Patch", "Патч", extracted=["index.html"]),
                       PoEntry("site:{n} entries", "{n} entries")], self.dir / "i18n")
        web = self.dir / "web"
        (web / "js").mkdir(parents=True)
        (web / "js" / "pokedex-startup.js").write_text('N_("Patch");', encoding="utf-8")
        root = self.dir / "data"
        self.assertEqual(sitecatalog.write(root, self.dir / "i18n", web), ["xx"])
        page = json.loads((root / "site" / "xx.json").read_text(encoding="utf-8"))
        self.assertEqual(page, {"schema": 1, "language": "xx", "strings": {"Patch": "Патч"}})
        self.assertEqual((root / "site" / "startup.js").read_text(encoding="utf-8"),
                         'globalThis.pokedexStartupCatalog = {"xx":{"Patch":"Патч"}};\n')
        data = decoded(root / "site" / "xx")
        index, chunk = data["index"], data["chunks"][0]
        self.assertEqual((index["profile"], index["language"]), ("site", "xx"))
        self.assertEqual(index["capabilities"]["check"], "tokens")
        self.assertIsNone(index["rom"]["sha1"])
        self.assertEqual([(r["id"], r["status"]) for r in index["list"]],
                         [("site:Patch", "approved"), ("site:{n} entries", "untranslated")])
        self.assertEqual(chunk["entries"][0]["context"]["comments"], ["index.html"])
        self.assertIsNone(chunk["entries"][0]["window"])
        self.assertEqual(index["schema"], editordata.SCHEMA)
        self.assertEqual(chunk["schema"], index["schema"])

    def test_editor_keeps_legacy_source_notes(self):
        entry = PoEntry("site:Label", "Label", "Target", translator=["source: Reference wording."])
        doc = sitecatalog.editor_doc("xx", [entry], "interface.po")
        self.assertEqual(doc["entries"][0]["note"], "Reference wording.")
        self.assertEqual(doc["entries"][0]["translator_notes"], [])
        self.assertEqual(doc["schema"], editordata.SCHEMA)

    def test_the_index_lists_every_catalog_for_the_language_menu(self):
        catalog("xx", [PoEntry("site:Patch", "Patch", "Патч"), PoEntry("site:Save", "Save")], self.dir / "i18n")
        catalog("yy", [PoEntry("site:Patch", "Patch")], self.dir / "i18n")
        for name in ("guide.html", "guide.xx.html", "guide.zz.html", "guide.Bad.html", "guides.html",
                     "save-guide.html", "save-guide.xx.html"):
            (self.dir / name).write_text("<html></html>", encoding="utf-8")
        root = self.dir / "data"
        self.assertEqual(sitecatalog.write(root, self.dir / "i18n", self.dir), ["xx", "yy"])
        self.assertEqual(json.loads((root / "site" / "catalogs.json").read_text(encoding="utf-8")),
                         {"schema": 1, "catalogs": [{"code": "xx", "keys": 2, "translated": 1},
                                                    {"code": "yy", "keys": 1, "translated": 0}],
                          "guides": ["xx", "zz"]})
        (self.dir / "i18n" / "yy.po").unlink()
        sitecatalog.write(root, self.dir / "i18n")
        index = json.loads((root / "site" / "catalogs.json").read_text(encoding="utf-8"))
        self.assertEqual([c["code"] for c in index["catalogs"]], ["xx"], "a removed catalog leaves the index")
        self.assertEqual(sitecatalog.write(root, self.dir / "none"), [])
        self.assertEqual(json.loads((root / "site" / "catalogs.json").read_text(encoding="utf-8"))["catalogs"], [])

class ManifestTest(unittest.TestCase):
    def test_the_editor_lists_the_site_project_and_the_registry_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = editordata.write_languages(Path(tmp), toolkit="0.0.0")
            site = [lang for lang in doc["languages"] if lang["profile"] == "site"]
            self.assertEqual([lang["code"] for lang in site], sorted(sitecatalog.catalogs()))
            self.assertEqual(site[0]["index"], f"site/{site[0]['code']}/index.json")
            self.assertEqual(next(p for p in doc["profiles"] if p["id"] == "site"), sitecatalog.profile_entry())
            registry = json.loads((Path(tmp) / "registry.json").read_text(encoding="utf-8"))
            self.assertNotIn("site", [g["id"] for g in registry["games"]])
            self.assertTrue((Path(tmp) / "site" / "ru.json").is_file())

    def test_linked_targets_are_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, outside = Path(tmp) / "data", Path(tmp) / "outside"
            root.mkdir()
            outside.mkdir()
            sentinel = outside / "languages.json"
            sentinel.write_text("unchanged", encoding="utf-8")
            (root / "languages.json").symlink_to(sentinel)
            with self.assertRaisesRegex(ValueError, "languages.json") as error:
                editordata.write_languages(root)
            self.assertNotIn("\n", str(error.exception))
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")
            self.assertFalse((root / "site").exists())

            (root / "languages.json").unlink()
            (root / "site").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "site"):
                editordata.write_languages(root)
            self.assertEqual([path.name for path in outside.iterdir()], ["languages.json"])
            self.assertFalse((root / "registry.json").exists())

            (root / "site").unlink()
            editordata.write_languages(root, toolkit="0.0.0")
            self.assertTrue((root / "languages.json").is_file())
            self.assertTrue((root / "registry.json").is_file())

class CommandTest(unittest.TestCase):
    def test_the_web_group_writes_the_catalogs(self):
        import contextlib
        import io

        from gen3tl.__main__ import main
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()):
            profile = workspace_project(Path(tmp))
            self.assertEqual(main(["--project-root", tmp, "--profile", str(profile),
                                   "web", "site-catalog", "--data", tmp]), 0)
            self.assertTrue((Path(tmp) / "site" / "ru.json").is_file())
            self.assertTrue((Path(tmp) / "site" / "ru" / "index.json").is_file())
            index = json.loads((Path(tmp) / "site" / "catalogs.json").read_text(encoding="utf-8"))
            self.assertEqual([c["code"] for c in index["catalogs"]], sorted(sitecatalog.catalogs()))
            self.assertRegex(out.getvalue(), r"wrote +\S+/site/catalogs.json\n")
            self.assertIn("untranslated 0 entries", out.getvalue())

class NoProjectTest(unittest.TestCase):
    """Without a registered project the site's data commands take the toolkit's data and fixture workspaces."""

    def test_site_commands_without_a_project_do_not_select_a_profile(self):
        from contextlib import chdir
        from unittest.mock import Mock, patch

        from gen3tl.cli import main as cli

        commands = [("web site-catalog", ["web", "site-catalog"]),
                    ("web registry", ["web", "registry"]),
                    ("web site-shell", ["web", "site-shell"]),
                    ("web import", ["web", "import", "site.json"]),
                    ("web server", ["web", "server", "status", "--page", "site"])]
        with tempfile.TemporaryDirectory() as tmp, chdir(tmp), \
                patch.object(cli.Profile, "by_name", side_effect=AssertionError("profile selected")):
            for name, argv in commands:
                with self.subTest(command=name):
                    handler = Mock(return_value=0)
                    with patch.dict(cli.COMMANDS, {name: handler}):
                        self.assertEqual(cli.main(argv), 0)
                    args, = handler.call_args.args
                    self.assertIsNone(args.project)
                    self.assertIsNone(args.profile)
                    self.assertEqual(args.build_root, Path.cwd() / "build")

    def test_the_site_is_written_and_served_outside_any_registry(self):
        import contextlib
        import io
        import threading
        import urllib.request
        from unittest.mock import patch

        from gen3tl.__main__ import main
        from gen3tl.cli.web import FIXTURES
        from gen3tl.profile.profile import name_of, profile_paths
        from gen3tl.project import Registry
        from gen3tl.web import editorserve
        fixtures = [p.key for p in Registry.load(FIXTURES).projects]
        served = {}
        real = editorserve.preview_server

        def serving(*args, **kwargs):
            """The real server: two pages fetched from it, then stopped."""
            server = real(*args, **kwargs)
            server.timeout = 30   # a lost request fails the test, never hangs it
            base = f"http://127.0.0.1:{server.server_address[1]}/"
            direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # no proxy for the loopback

            def fetch():
                for path in ("editor", "data/registry.json"):
                    with direct.open(base + path, timeout=30) as response:
                        served[path] = response.read()

            def serve_forever():
                client = threading.Thread(target=fetch)
                client.start()
                server.handle_request()
                server.handle_request()
                client.join()
                raise KeyboardInterrupt

            server.serve_forever = serve_forever
            return server

        with tempfile.TemporaryDirectory() as tmp, contextlib.chdir(tmp):
            self.assertEqual(Registry.load().projects, (), "no projects.toml at or above the directory")
            with contextlib.redirect_stdout(io.StringIO()) as out, \
                    contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(main(["web", "site-catalog"]), 0)
                self.assertEqual(main(["web", "registry"]), 0)
                with patch.object(editorserve, "preview_server", serving):
                    self.assertEqual(main(["web", "server", "start", "--page", "site", "--foreground", "--port", "0"]), 0)
                self.assertEqual(err.getvalue(), "")
            data = Path(tmp) / "build" / "editor"
            for key in fixtures:
                self.assertIn(f"project      {key}\n", out.getvalue())
            manifest = json.loads((data / "languages.json").read_text(encoding="utf-8"))
            listed = sorted(f"{x['profile']}/{x['code']}" for x in manifest["languages"])
            self.assertEqual(listed, sorted(fixtures + [f"site/{code}" for code in sitecatalog.catalogs()]))
            registry = json.loads((data / "registry.json").read_text(encoding="utf-8"))
            self.assertTrue(registry["games"][0]["languages"])
            self.assertEqual(registry["default"], registry["games"][0]["id"])
            games = {g["id"]: g["languages"] for g in registry["games"]}
            self.assertEqual(sorted(games), sorted(name_of(path) for path in profile_paths()))
            for key in fixtures:
                profile, code = key.split("/")
                self.assertIn(code, games[profile])
            self.assertIn(b"<title", served["editor"])
            self.assertEqual(json.loads(served["data/registry.json"]), registry)
            self.assertIn("status       ready", out.getvalue())

def site(root: Path) -> Path:
    """A small site: a page, a module and its catalogs' directory."""
    web = root / "web"
    (web / "js").mkdir(parents=True)
    (web / "i18n").mkdir()
    (web / "index.html").write_text('<html><head><title data-ui="Home">Home</title></head><body>'
                                    '<p data-ui="Kept &amp; used">Kept &amp; used</p></body></html>', encoding="utf-8")
    (web / "js" / "page.js").write_text('// t("Commented out")\nshow(t("Loading {game}…", { game }));\n'
                                        'const KEPT = N_("Kept & used");\n', encoding="utf-8")
    return web

class SyncTest(unittest.TestCase):
    """The sync brings each catalog to the keys of the pages; lang init seeds a new one."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.web = site(Path(self.tmp.name))


    def test_registry_keys_follow_configuration_and_preserve_translations(self):
        from unittest.mock import patch

        from gen3tl.project import registry_scope

        root = Path(self.tmp.name)
        profile = workspace_project(root, "xx")
        profile.write_text(profile.read_text() + '\n[landing]\nreview = "Declared review"\n'
                           'review_title = "Review policy"\n[compatibility]\nsaves = "Declared saves"\n'
                           '\n[[language_rules]]\nid = "ending"\ntitle = "Declared ending"\n'
                           'description = "A fixed engine ending."\nkind = "catalog_text"\n'
                           'related = []\nexamples = []\nevidence = "Recorded source."\n',
                           encoding="utf-8")
        project = root / "projects.toml"
        project.write_text(project.read_text() + '\n[site]\nlicenses = [{label_key = "Declared licence", '
                           'url = "../terms"}]\n', encoding="utf-8")
        other = root / "other.toml"
        other.write_text('[rom]\nsha1 = "' + 'a' * 40 + '"\n[landing]\nreview = "Unrelated review"\n',
                         encoding="utf-8")
        directory = self.web / "i18n"
        catalog("xx", [PoEntry("site:Declared review", "Declared review", "Review wording", extracted=["registry"]),
                       PoEntry("site:Declared ending", "Declared ending", "Ending wording", extracted=["registry"])], directory)
        with registry_scope(root), patch("gen3tl.profile.profile.profile_paths", return_value=[profile, other]), \
                patch.object(sitecatalog, "WEB", self.web), patch.object(sitecatalog, "CATALOGS", directory):
            self.assertEqual(sitecatalog.registry_keys(), {"Declared review", "Review policy", "Declared saves",
                                                          "Declared licence", "Declared ending", "A fixed engine ending."})
            keys = sitecatalog.page_keys()
            self.assertEqual(keys["Declared review"], ["registry"])
            self.assertNotIn("Unrelated review", keys)
            sitecatalog.sync()
            with patch("gen3tl.profile.profile.profile_paths", return_value=[]):
                self.assertNotIn("Declared review", sitecatalog.page_keys())
            entries = {entry.msgid: entry for entry in sitecatalog.load(directory / "xx.po")}
            self.assertEqual(entries["Declared review"].msgstr, "Review wording")
            self.assertEqual(entries["Declared review"].extracted, ["registry"])
            self.assertEqual(entries["Declared ending"].msgstr, "Ending wording")
            self.assertEqual(entries["A fixed engine ending."].extracted, ["registry"])


    def test_sync_and_seed_without_a_registry_keep_registry_entries(self):
        directory = self.web / "i18n"
        catalog("xx", [PoEntry("site:Home", "Home", "Дом", extracted=["index.html, registry"]),
                       PoEntry("site:Declared review", "Declared review", "Обзор", extracted=["registry"]),
                       PoEntry("site:Gone", "Gone", "Нет", extracted=["js/gone.js"])], directory)
        [synced] = sitecatalog.sync(directory, self.web)
        self.assertEqual(synced.dropped, ["Gone"])
        entries = {e.msgid: e for e in sitecatalog.load(directory / "xx.po")}
        declared = entries["Declared review"]
        self.assertEqual((declared.msgstr, declared.extracted), ("Обзор", ["registry"]))
        self.assertEqual((entries["Home"].msgstr, entries["Home"].extracted), ("Дом", ["index.html, registry"]))
        seeded = {e.msgid: e for e in sitecatalog.load(sitecatalog.seed("yy", directory, self.web))}
        self.assertEqual((seeded["Declared review"].msgstr, seeded["Declared review"].extracted), ("", ["registry"]))
        self.assertEqual([p for c in sitecatalog.compare(directory, self.web)[1] for p in c.problems], [])

    def test_sync_in_a_registry_drops_a_key_it_no_longer_declares(self):
        from unittest.mock import patch

        from gen3tl.project import registry_scope

        root = Path(self.tmp.name)
        profile = workspace_project(root, "xx")
        profile.write_text(profile.read_text() + '\n[landing]\nreview = "Declared review"\n', encoding="utf-8")
        directory = self.web / "i18n"
        catalog("xx", [PoEntry("site:Declared review", "Declared review", "Обзор", extracted=["registry"]),
                       PoEntry("site:Removed review", "Removed review", "Убрано", extracted=["registry"])],
                directory)
        with registry_scope(root), patch("gen3tl.profile.profile.profile_paths", return_value=[profile]), \
                patch.object(sitecatalog, "WEB", self.web), patch.object(sitecatalog, "CATALOGS", directory):
            [synced] = sitecatalog.sync()
        self.assertEqual(synced.dropped, ["Removed review"])
        entries = {e.msgid: e for e in sitecatalog.load(directory / "xx.po")}
        self.assertNotIn("Removed review", entries)
        declared = entries["Declared review"]
        self.assertEqual((declared.msgstr, declared.extracted), ("Обзор", ["registry"]))

    def test_the_command_syncs_before_it_writes(self):
        import contextlib
        import io
        from unittest.mock import patch

        from gen3tl.__main__ import main
        catalog("xx", [PoEntry("site:Stale", "Stale", "Старое")], self.web / "i18n")
        data = Path(self.tmp.name) / "data"
        with patch.object(sitecatalog, "WEB", self.web), patch.object(sitecatalog, "CATALOGS", self.web / "i18n"), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            profile = workspace_project(Path(self.tmp.name))
            self.assertEqual(main(["--project-root", self.tmp.name, "--profile", str(profile),
                                   "web", "site-catalog", "--sync", "--data", str(data)]), 0)
        self.assertIn("added        3 keys\ndropped      1 keys", out.getvalue())
        self.assertIn("untranslated 3 entries", out.getvalue())
        self.assertEqual([e.msgid for e in sitecatalog.load(self.web / "i18n" / "xx.po")],
                         ["Home", "Kept & used", "Loading {game}…"])
        self.assertEqual(json.loads((data / "site" / "xx.json").read_text(encoding="utf-8"))["strings"], {})

class CompareTest(unittest.TestCase):
    """The pull-request comparison; without a registry the catalogs' registry entries stand for it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.web = site(Path(self.tmp.name))
        self.i18n = self.web / "i18n"

    def page_entries(self):
        return [PoEntry("site:Home", "Home", "Дом", extracted=["index.html"]),
                PoEntry("site:Kept & used", "Kept & used", "Оставлено", extracted=["index.html, js/page.js"]),
                PoEntry("site:Loading {game}…", "Loading {game}…", "Загрузка {game}…", extracted=["js/page.js"])]

    def problems(self):
        keys, compared = sitecatalog.compare(self.i18n, self.web)
        return keys, [problem for c in compared for problem in c.problems]

    def test_registry_entries_stand_without_a_registry(self):
        entries = self.page_entries()
        entries[0].extracted = ["index.html, registry"]
        catalog("xx", entries + [PoEntry("site:Declared review", "Declared review", "Обзор", extracted=["registry"])],
                self.i18n)
        keys, problems = self.problems()
        self.assertEqual(problems, [])
        self.assertEqual(keys["Declared review"], ["registry"])
        self.assertEqual(keys["Home"], ["index.html", "registry"])

    def test_a_stale_page_entry_is_still_reported(self):
        path = catalog("xx", self.page_entries() + [PoEntry("site:Gone", "Gone", "Нет", extracted=["js/gone.js"])],
                       self.i18n)
        self.assertEqual(self.problems()[1], [f"stale key  {path}: Gone"])

    def test_an_active_registry_decides_the_registry_entries(self):
        from unittest.mock import patch
        path = catalog("xx", self.page_entries() + [PoEntry("site:Declared review", "Declared review", "Обзор",
                                                            extracted=["registry"])], self.i18n)
        with patch.object(sitecatalog, "WEB", self.web), \
                patch.object(sitecatalog, "registry_keys", return_value={"Declared ending"}):
            self.assertEqual(self.problems()[1], [f"missing key  {path}: Declared ending",
                                                  f"stale key  {path}: Declared review"])

    def test_holes_and_unsynced_locations_are_reported(self):
        entries = self.page_entries()
        entries[2].msgstr = "Загрузка…"
        holed = catalog("xx", entries, self.i18n)
        entries = self.page_entries()
        entries[0].extracted = ["pick.html"]
        unsynced = catalog("yy", entries, self.i18n)
        self.assertEqual(self.problems()[1], [f"dropped hole  {holed}: Loading {{game}}…",
                                              f"out of sync  {unsynced}: web site-catalog --sync would change it"])

class ApprovalTest(unittest.TestCase):
    """An import writes new site texts translated and keeps approved ones; the command reports and approves."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.web = site(Path(self.tmp.name))

    def export(self, **entries):
        return {"schema": 0, "toolkit": "0.0.0", "language": "xx", "profile": "site",
                "rom": {"sha1": None},
                "entries": [{"id": f"site:{key}", "source_text": key, "translation": text, "note": ""}
                            for key, text in entries.items()]}

    def test_an_import_keeps_approved_texts_and_marks_the_others_translated(self):
        i18n = self.web / "i18n"
        catalog("xx", [PoEntry("site:Home", "Home", "Дом", extracted=["index.html"]),
                       PoEntry("site:Loading {game}…", "Loading {game}…", "Ждите {game}…",
                               extracted=["js/page.js"], flags=["fuzzy"])], i18n)
        applied = sitecatalog.apply_export(self.export(**{"Home": "Главная", "Loading {game}…": "Загрузка {game}…"}),
                                           directory=i18n)
        self.assertEqual(applied.changed, ["site:Home", "site:Loading {game}…"])
        self.assertEqual([(e.msgid, e.msgstr, e.fuzzy) for e in sitecatalog.load(i18n / "xx.po")],
                         [("Home", "Главная", False), ("Loading {game}…", "Загрузка {game}…", True)])

    def test_an_import_writes_new_text_translated_and_approve_clears_named_keys(self):
        i18n = self.web / "i18n"
        catalog("xx", [PoEntry("site:Home", "Home", "", extracted=["index.html"]),
                       PoEntry("site:Loading {game}…", "Loading {game}…", "", extracted=["js/page.js"])], i18n)
        applied = sitecatalog.apply_export(self.export(Home="Дом"), directory=i18n)
        self.assertEqual(applied.changed, ["site:Home"])
        entries = sitecatalog.load(i18n / "xx.po")
        self.assertEqual([(e.msgid, e.msgstr, e.fuzzy) for e in entries],
                         [("Home", "Дом", True), ("Loading {game}…", "", False)])
        approved = sitecatalog.approve("xx", ["Home"], directory=i18n)
        self.assertEqual(approved.cleared, 1)
        self.assertEqual([(e.msgid, e.fuzzy) for e in sitecatalog.load(i18n / "xx.po")],
                         [("Home", False), ("Loading {game}…", False)])

    def test_approve_all_clears_every_fuzzy_entry_and_refuses_unknown_keys(self):
        i18n = self.web / "i18n"
        catalog("xx", [PoEntry("site:Home", "Home", "Дом", extracted=["index.html"], flags=["fuzzy"]),
                       PoEntry("site:Loading {game}…", "Loading {game}…", "Загрузка {game}…",
                               extracted=["js/page.js"], flags=["fuzzy"])], i18n)
        approved = sitecatalog.approve("xx", None, directory=i18n)
        self.assertEqual(approved.cleared, 2)
        self.assertFalse(any(e.fuzzy for e in sitecatalog.load(i18n / "xx.po")))
        with self.assertRaisesRegex(sitecatalog.SiteCatalogError, "unknown key"):
            sitecatalog.approve("xx", ["Missing"], directory=i18n)
        with self.assertRaisesRegex(sitecatalog.SiteCatalogError, "no site catalog"):
            sitecatalog.approve("yy", None, directory=i18n)

    def test_the_command_reports_translated_entries_and_approves(self):
        import contextlib
        import io
        from unittest.mock import patch

        from gen3tl.__main__ import main
        i18n = self.web / "i18n"
        catalog("xx", [PoEntry("site:Home", "Home", "Дом", extracted=["index.html"], flags=["fuzzy"])], i18n)
        data = Path(self.tmp.name) / "data"
        profile = workspace_project(Path(self.tmp.name))
        with patch.object(sitecatalog, "WEB", self.web), patch.object(sitecatalog, "CATALOGS", i18n), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--project-root", self.tmp.name, "--profile", str(profile),
                                   "web", "site-catalog", "--data", str(data)]), 0)
        self.assertRegex(out.getvalue(), r"translated\s+1 entries")
        self.assertNotIn("fuzzy", out.getvalue())
        self.assertTrue(sitecatalog.load(i18n / "xx.po")[0].fuzzy)
        with patch.object(sitecatalog, "WEB", self.web), patch.object(sitecatalog, "CATALOGS", i18n), \
                contextlib.redirect_stdout(io.StringIO()) as out2, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--project-root", self.tmp.name, "--profile", str(profile),
                                   "web", "site-catalog", "--approve", "xx", "Home", "--data", str(data)]), 0)
        self.assertRegex(out2.getvalue(), r"approved\s+1 entries")
        self.assertRegex(out2.getvalue(), r"translated\s+0 entries")
        self.assertFalse(sitecatalog.load(i18n / "xx.po")[0].fuzzy)

class CompletenessTest(unittest.TestCase):
    """Every key the pages show has a translation in every site catalog; no catalog keeps a stale key."""

    def test_the_toolkit_catalogs_pass_the_pull_request_comparison(self):
        from gen3tl import TOOLKIT_ROOT
        from gen3tl.project import registry_scope
        with registry_scope(TOOLKIT_ROOT):   # the check runs in a toolkit checkout
            keys, compared = sitecatalog.compare()
        self.assertEqual([c.path.name for c in compared], [path.name for path in sitecatalog.catalogs().values()])
        self.assertGreater(len(keys), 400)
        self.assertEqual([problem for c in compared for problem in c.problems], [])

    def test_a_sync_in_the_toolkit_checkout_keeps_the_catalogs(self):
        import shutil
        from gen3tl import TOOLKIT_ROOT
        from gen3tl.project import registry_scope
        with tempfile.TemporaryDirectory() as tmp, registry_scope(TOOLKIT_ROOT):
            copies = Path(tmp)
            for path in sitecatalog.catalogs().values():
                shutil.copyfile(path, copies / path.name)
            sitecatalog.sync(copies)
            for path in sitecatalog.catalogs().values():
                self.assertEqual((copies / path.name).read_bytes(), path.read_bytes(), path.name)
            self.assertEqual([problem for c in sitecatalog.compare(copies)[1] for problem in c.problems], [])

    def test_translations_keep_the_holes_of_their_keys(self):
        hole = re.compile(r"\{\w+\}")
        for code, path in sitecatalog.catalogs().items():
            for e in sitecatalog.load(path):
                with self.subTest(code=code, key=e.msgid):
                    if e.msgstr:
                        self.assertEqual(sorted(hole.findall(e.msgstr)), sorted(hole.findall(e.msgid)))

    def test_language_names_stay_english(self):
        names = {"English", "Russian", "German", "Spanish", "French"}
        for code, path in sitecatalog.catalogs().items():
            for entry in sitecatalog.load(path):
                if entry.msgid in names:
                    with self.subTest(code=code, key=entry.msgid):
                        self.assertEqual(entry.msgstr, entry.msgid)
