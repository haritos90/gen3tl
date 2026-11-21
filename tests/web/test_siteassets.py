"""Static deployments identify one build and keep the assets of earlier versions."""

import contextlib
import functools
import io
import hashlib
import json
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import unittest
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import unquote, urljoin, urlsplit
from urllib.request import Request

from gen3tl import TOOLKIT_ROOT, __version__
from gen3tl.cli.web import site_assemble
from gen3tl.web.siteassets import AcceptedRedirects, safe_name, site_address, write_build


class PageReferences(HTMLParser):
    def __init__(self, document):
        super().__init__()
        self.assets, self.elements = [], {}
        self.feed(document)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("src"):
            self.assets.append(attrs["src"])
        if tag == "link" and attrs.get("rel") in {"stylesheet", "modulepreload"}:
            self.assets.append(attrs["href"])
        if attrs.get("id"):
            self.elements[attrs["id"]] = {"dataset": {name[5:]: value for name, value in attrs.items()
                                                      if name.startswith("data-")}}


class SiteCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.web, self.data = self.root / "web", self.root / "data"
        self.web.mkdir()
        self.data.mkdir()
        self.write(self.data / "languages.json", json.dumps({"bundle": "check.zip", "profiles": [], "languages": []}))

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def assemble(self, out, version=__version__, web=None, public_base=None, previous=None):
        args = SimpleNamespace(data=self.data, out=out, public_base=public_base, previous=previous)
        output = io.StringIO()
        with mock.patch("gen3tl.__version__", version), mock.patch("gen3tl.web.editorserve.WEB", web or self.web), \
                contextlib.redirect_stdout(output):
            result = site_assemble(args)
        return result, output.getvalue()

    def release(self, version):
        page = f'''<!doctype html>
<link rel="stylesheet" href="site.css?sheet=1&amp;theme=2#top">
<link rel="modulepreload" href="js/label.mjs">
<script type="module" src="js/app.mjs"></script>
<header id="header-{version}" data-release="{version}"></header>
<a href="data/registry.json">Data</a>
'''
        self.write(self.web / "index.html", page)
        self.write(self.web / "js/app.mjs", f'''
import {{ label }} from "./label.mjs";
const {{ dynamic }} = await import("./dynamic.mjs");
const header = document.getElementById("header-{version}");
if (header.dataset.release !== label || dynamic !== label) throw new Error("Mixed release");
globalThis.loaded = label;
globalThis.worker = new URL("./worker.mjs", import.meta.url).href;
globalThis.coverage = new URL("../fonts/coverage.json", import.meta.url).href;
''')
        self.write(self.web / "js/label.mjs", f'export const label = "{version}";\n')
        self.write(self.web / "js/dynamic.mjs", f'export const dynamic = "{version}";\n')
        self.write(self.web / "js/worker.mjs", f'export const worker = "{version}";\n')
        self.write(self.web / "fonts/coverage.json", '{"schema": 1}\n')
        self.write(self.web / "site.css", '@import "css/palette.css";\nbody { background: url("icons/tile.svg"); }\n')
        self.write(self.web / "css/palette.css", f':root {{ --release: "{version}"; }}\n')
        self.write(self.web / "icons/tile.svg", f'<svg xmlns="http://www.w3.org/2000/svg"><title>{version}</title></svg>')
        return page

    def assert_assets(self, document, page, out, version):
        references = PageReferences(document)
        self.assertTrue(references.assets)
        for reference in references.assets:
            path = Path(unquote(urlsplit(urljoin(page.as_uri(), reference)).path))
            if path.is_relative_to(out / "data"):
                self.assertEqual(urlsplit(reference).query, f"v={version}", reference)
                continue
            self.assertTrue(path.is_relative_to(out / "assets" / version), reference)
            self.assertTrue(path.is_file(), path)
        return references


class SiteAssetsTest(SiteCase):
    @unittest.skipUnless(shutil.which("node"), "no node")
    def test_current_release_executes_its_versioned_scripts(self):
        out = self.root / "site"
        source = self.release("2.0.0")
        self.assertEqual(self.assemble(out, "2.0.0")[0], 0)
        self.assertEqual((self.web / "index.html").read_text(), source)
        self.assertEqual([path.name for path in (out / "assets").iterdir()], ["2.0.0"])
        self.assertFalse((out / "js").exists())
        self.assertFalse((out / "site.css").exists())
        document = (out / "index.html").read_text()
        references = self.assert_assets(document, out / "index.html", out, "2.0.0")
        self.assertIn('?sheet=1&amp;theme=2#top', document)
        self.assertIn('href="data/registry.json"', document)
        entry = next(url for url in references.assets if url.endswith("app.mjs"))
        script = '''
import assert from "node:assert/strict";
const [entry, elements] = process.argv.slice(1);
const nodes = JSON.parse(elements);
globalThis.document = { getElementById: id => nodes[id] ?? null };
await import(entry);
assert.equal(globalThis.loaded, "2.0.0");
assert.equal((await import(globalThis.worker)).worker, "2.0.0");
'''
        result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script,
                                 urljoin((out / "index.html").as_uri(), entry), json.dumps(references.elements)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2.0.0", (out / "assets/2.0.0/css/palette.css").read_text())
        self.assertIn("2.0.0", (out / "assets/2.0.0/icons/tile.svg").read_text())

    def test_every_shipped_page_and_dependency_uses_the_version_directory(self):
        out = self.root / "site"
        result, output = self.assemble(out, web=TOOLKIT_ROOT / "web")
        self.assertEqual(result, 0, output)
        pages = list(out.rglob("*.html"))
        self.assertGreater(len(pages), 10)
        identity = json.loads((out / "site-build.json").read_text())["id"]
        for page in pages:
            if page.parent == out:
                self.assertIn(f'<meta name="site-build" content="{identity}">\n'
                              f'<meta name="site-version" content="{__version__}">', page.read_text())
            else:
                self.assertNotIn('name="site-build"', page.read_text())
                self.assertNotIn('name="site-version"', page.read_text())
            self.assert_assets(page.read_text(), page, out, __version__)
        assets = out / "assets" / __version__
        imports = re.compile(r'''(?:\bfrom\s*|\bimport\s*\(?\s*)["'](\.[^"']+)["']''')
        workers = re.compile(r'''\bnew URL\(\s*["'](\.[^"']+)["']\s*,\s*import\.meta\.url''')
        css = re.compile(r'''url\(\s*["']?([^\s)'";]+)|@import\s+["']([^"']+)''')
        for path in out.rglob("*"):
            if path.suffix not in {".js", ".mjs", ".css"}:
                continue
            self.assertTrue(path.is_relative_to(assets), path)
            self.assertEqual(path.read_bytes(), (TOOLKIT_ROOT / "web" / path.relative_to(assets)).read_bytes())
            source = path.read_text()
            expressions = (imports.findall(source) + workers.findall(source) if path.suffix != ".css"
                           else [a or b for a, b in css.findall(source)])
            for relative in expressions:
                target = Path(unquote(urlsplit(urljoin(path.as_uri(), relative)).path))
                self.assertTrue(target.is_relative_to(assets), relative)
                self.assertTrue(target.is_file(), target)

    def test_patch_module_and_license_are_published_beside_versioned_scripts(self):
        out = self.root / "site"
        result, output = self.assemble(out, web=TOOLKIT_ROOT / "web")
        self.assertEqual(result, 0, output)
        source = TOOLKIT_ROOT / "web/js/patcher"
        target = out / "assets" / __version__ / "js/patcher"
        for name in ("rom-patcher.wasm", "rom-patcher.js", "LICENSE"):
            self.assertEqual((target / name).read_bytes(), (source / name).read_bytes())
            self.assertFalse((out / "js/patcher" / name).exists())

    def test_vendored_patch_module_matches_its_release_record(self):
        folder = TOOLKIT_ROOT / "web/js/patcher"
        record = json.loads((folder / "version.json").read_text())
        self.assertEqual(record["version"], "1.4.0")
        self.assertEqual(set(record["sha256"]), {"rom-patcher.wasm", "rom-patcher.js", "LICENSE"})
        self.assertEqual({name: hashlib.sha256((folder / name).read_bytes()).hexdigest()
                          for name in record["sha256"]}, record["sha256"])

    def test_generated_share_pages_and_nested_pages_are_rewritten(self):
        self.release("1.0.0")
        self.write(self.web / "nested/index.html", "<script type='module' src='../js/app.mjs'></script>")
        out = self.root / "site"

        def generate(data, destination, public_base, application=None):
            self.write(destination / "share/game/xx/species/1/index.html",
                       '<link rel="stylesheet" href="../../../../../site.css">'
                       '<script src="../../../../../js/app.mjs"></script>')
            return []

        with mock.patch("gen3tl.web.pokedexshare.generate", side_effect=generate):
            self.assertEqual(self.assemble(out, "1.0.0", public_base="https://site.test/subpath/")[0], 0)
        for page in out.rglob("*.html"):
            self.assert_assets(page.read_text(), page, out, "1.0.0")

    def test_build_identity_is_embedded_only_in_root_pages(self):
        out = self.root / "site"
        names = ["index.html", "editor.html", "nested/index.html", "share/game/en/species/1/index.html",
                 "assets/1.0.0/page.html", "data/page.html"]
        source = "<!doctype html><html><head></head><body></body></html>"
        for name in names:
            self.write(out / name, source)
        identity = write_build(out, "1.0.0")
        meta = f'<meta name="site-build" content="{identity}">\n<meta name="site-version" content="1.0.0">'
        for name in names:
            expected = source.replace("<head>", "<head>\n" + meta) if "/" not in name else source
            self.assertEqual((out / name).read_text(), expected, name)

    def test_assembled_licenses_are_static_and_source_drift_blocks_assembly(self):
        shutil.copytree(TOOLKIT_ROOT / "web", self.web, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("test", "data"))
        self.write(self.data / "registry.json", json.dumps({"schema": 1, "games": [], "site": {
            "licenses": [{"label_key": "Example terms", "url": "../terms"}],
        }}))
        out = self.root / "site"
        result, output = self.assemble(out)
        self.assertEqual(result, 0, output)
        self.assertFalse((out / "fragments").exists())
        self.assertIn('href="terms"', (out / "index.html").read_text())
        self.assertEqual((out / "pokedex.html").read_text().count('Example terms</a>'), 2)
        self.assertNotIn('Example terms', (out / "guide.html").read_text())
        self.assertIn('Content licence', (out / "guide.html").read_text())
        source = self.web / "index.html"
        source.write_text(source.read_text().replace('>gen3tl</a>', '>Changed</a>'))
        blocked = self.root / "blocked"
        result, output = self.assemble(blocked)
        self.assertEqual(result, 1)
        self.assertIn('index.html: out of sync', output)
        self.assertFalse(blocked.exists())

    def identity(self, name, version="1.0.0"):
        out = self.root / name
        result, output = self.assemble(out, version)
        self.assertEqual(result, 0, output)
        identity = json.loads((out / "site-build.json").read_text())["id"]
        self.assertRegex(identity, r"^[a-f0-9]{64}$")
        self.assertIn(identity, output)
        return identity

    def test_build_identity_is_stable_for_equal_inputs(self):
        self.release("1.0.0")
        self.assertEqual(self.identity("first"), self.identity("second"))

    def test_build_identity_changes_with_toolkit_version(self):
        self.release("1.0.0")
        self.assertNotEqual(self.identity("first"), self.identity("second", "2.0.0"))

    def test_build_identity_changes_with_published_data(self):
        self.release("1.0.0")
        path = self.data / "site/xx.json"
        self.write(path, '{"value": 1}')
        first = self.identity("first")
        self.write(path, '{"value": 2}')
        second = self.identity("second")
        self.assertNotEqual(first, second)
        path.rename(path.with_name("yy.json"))
        self.assertNotEqual(second, self.identity("renamed"))

    def test_unpublished_data_does_not_change_identity(self):
        self.release("1.0.0")
        first = self.identity("first")
        self.write(self.data / "unused.json", '{"value": 2}')
        self.assertEqual(first, self.identity("second"))

    def test_generated_data_scripts_keep_their_path_with_the_version(self):
        self.write(self.web / "index.html", self.release("1.0.0") + '<script src="data/site/startup.js"></script>\n')
        result, output = self.assemble(self.root / "site", "1.0.0")
        self.assertEqual(result, 0, output)
        document = (self.root / "site" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<script src="data/site/startup.js?v=1.0.0"></script>', document)

    def test_missing_local_scripts_and_styles_refuse_assembly(self):
        self.write(self.web / "index.html", '<script src="missing.js"></script>')
        result, output = self.assemble(self.root / "site")
        self.assertEqual(result, 1)
        self.assertIn("unversioned asset missing.js", output)


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class KeptVersionsTest(SiteCase):
    def site(self, version, previous=None, name=""):
        out = self.root / f"site-{version}{name}"
        self.release(version)
        result, output = self.assemble(out, version, previous=previous)
        self.assertEqual(result, 0, output)
        return out, output

    def chain(self, *versions, name=""):
        out = None
        for version in versions:
            out, _ = self.site(version, previous=out, name=name)
        return out

    def folder(self, site, version):
        folder = site / "assets" / version
        return {path.relative_to(folder).as_posix(): path.read_bytes() for path in folder.rglob("*") if path.is_file()}

    def build(self, site):
        return json.loads((site / "site-build.json").read_text())

    def serve(self, site):
        server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(site)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}/"

    def test_command_line_takes_a_directory_or_an_address(self):
        from gen3tl.cli.main import parser
        for source in ("site", "https://site.test/app/"):
            self.assertEqual(parser()[0].parse_args(["web", "assemble", "--previous", source]).previous, source)
        self.assertIsNone(parser()[0].parse_args(["web", "assemble"]).previous)

    def test_manifest_of_the_current_version_lists_exactly_its_files(self):
        out, _ = self.site("1.0.0")
        files = self.folder(out, "1.0.0")
        manifest = json.loads(files.pop("files.json"))
        self.assertEqual(set(manifest), {"version", "files"})
        self.assertEqual(manifest["version"], "1.0.0")
        self.assertEqual(manifest["files"], {name: hashlib.sha256(data).hexdigest() for name, data in files.items()})
        self.assertEqual(set(files), {"js/app.mjs", "js/label.mjs", "js/dynamic.mjs", "js/worker.mjs", "site.css",
                                      "css/palette.css", "icons/tile.svg", "fonts/coverage.json"})

    def test_site_without_a_source_names_its_version_and_keeps_none(self):
        out, output = self.site("1.0.0")
        build = self.build(out)
        self.assertEqual(list(build), ["id", "version", "kept"])
        self.assertEqual((build["version"], build["kept"]), ("1.0.0", []))
        self.assertNotIn("kept", output)
        self.assertNotIn("skipped", output)
        self.assertEqual([path.name for path in (out / "assets").iterdir()], ["1.0.0"])

    def test_directory_source_carries_the_two_newest_versions_in_version_order(self):
        third = self.chain("1.9.0", "1.10.0", "2.0.0")
        self.assertEqual(self.build(third)["kept"], ["1.10.0", "1.9.0"])
        out, output = self.site("2.0.1", previous=third)
        self.assertEqual(self.build(out), {"id": self.build(out)["id"], "version": "2.0.1", "kept": ["2.0.0", "1.10.0"]})
        self.assertEqual(sorted(path.name for path in (out / "assets").iterdir()), ["1.10.0", "2.0.0", "2.0.1"])
        for version in ("2.0.0", "1.10.0"):
            self.assertEqual(self.folder(out, version), self.folder(third, version))
            self.assertIn(f"kept         {version} 8 files\n", output)
        self.assertNotIn("1.9.0", output)
        self.assertNotIn("kept         none", output)
        self.assertIn(f"build        {self.build(out)['id']}\n", output)

    def test_address_source_carries_versions_through_a_local_server(self):
        second = self.chain("1.0.0", "1.1.0")
        out, output = self.site("1.2.0", previous=self.serve(second))
        self.assertEqual(self.build(out)["kept"], ["1.1.0", "1.0.0"])
        for version in ("1.1.0", "1.0.0"):
            self.assertEqual(self.folder(out, version), self.folder(second, version))
            self.assertIn(f"kept         {version} 8 files\n", output)

    @unittest.skipUnless(shutil.which("node"), "no node")
    def test_page_of_a_kept_version_executes_its_own_scripts_from_the_new_site(self):
        first, _ = self.site("1.0.0")
        old_page = (first / "index.html").read_text()
        self.write(first / "data/stale.json", "{}")
        second, _ = self.site("2.0.0", previous=first)
        self.assertFalse((second / "data/stale.json").exists())
        for version, document in (("1.0.0", old_page), ("2.0.0", (second / "index.html").read_text())):
            references = self.assert_assets(document, second / "index.html", second, version)
            entry = next(url for url in references.assets if url.endswith("app.mjs"))
            script = '''
import assert from "node:assert/strict";
const [entry, elements, expected] = process.argv.slice(1);
const nodes = JSON.parse(elements);
globalThis.document = { getElementById: id => nodes[id] ?? null };
await import(entry);
assert.equal(globalThis.loaded, expected);
assert.equal((await import(globalThis.worker)).worker, expected);
'''
            result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script,
                                     urljoin((second / "index.html").as_uri(), entry),
                                     json.dumps(references.elements), version], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_equal_version_is_assembled_anew_and_its_kept_versions_stay(self):
        second = self.chain("1.0.0", "2.0.0")
        (second / "assets/2.0.0/js/label.mjs").write_text("changed")
        out, output = self.site("2.0.0", previous=second, name="-again")
        self.assertEqual(self.build(out)["kept"], ["1.0.0"])
        self.assertEqual((out / "assets/2.0.0/js/label.mjs").read_text(), 'export const label = "2.0.0";\n')
        self.assertNotIn("skipped", output)

    def test_changed_missing_and_oversized_files_skip_the_version_and_report_it(self):
        def rename(folder):
            manifest = folder / "files.json"
            manifest.write_text(manifest.read_text().replace("1.0.0", "1.0.1"))

        def link(folder):
            (folder / "js/label.mjs").unlink()
            (folder / "js/label.mjs").symlink_to(self.web / "js/label.mjs")

        for name, damage, reason in [
            ("changed", lambda folder: (folder / "js/label.mjs").write_text("changed"), "hash mismatch js/label.mjs"),
            ("missing", lambda folder: (folder / "js/label.mjs").unlink(), "file missing js/label.mjs"),
            ("linked", link, "file missing js/label.mjs"),
            ("unlisted", lambda folder: (folder / "files.json").unlink(), "manifest missing"),
            ("invalid", lambda folder: (folder / "files.json").write_text("{}"), "manifest invalid"),
            ("renamed", rename, "manifest invalid"),
        ]:
            with self.subTest(name=name):
                second = self.chain("1.0.0", "1.1.0", name=name)
                damage(second / "assets/1.0.0")
                out, output = self.site("1.2.0", previous=second, name=name)
                self.assertEqual(self.build(out)["kept"], ["1.1.0"])
                self.assertIn(f"skipped      1.0.0 {reason}\n", output)
                self.assertIn("kept         1.1.0 8 files\n", output)
                self.assertNotIn("kept         none", output)
                self.assertEqual(sorted(path.name for path in (out / "assets").iterdir()), ["1.1.0", "1.2.0"])
        first, _ = self.site("1.0.0")
        manifest = first / "assets/1.0.0/files.json"
        doc = json.loads(manifest.read_text())
        doc["files"]["css/palette.css"] = hashlib.sha256(b" " * 4097).hexdigest()
        manifest.write_text(json.dumps(doc))
        (first / "assets/1.0.0/css/palette.css").write_bytes(b" " * 4097)
        for limit, reason in (("FILE_BYTES", "file too large css/palette.css"), ("VERSION_BYTES", "version too large")):
            with self.subTest(limit=limit), mock.patch(f"gen3tl.web.siteassets.{limit}", 4096):
                out, output = self.site("1.1.0", previous=first, name=limit)
                self.assertEqual(self.build(out)["kept"], [])
                self.assertIn(f"skipped      1.0.0 {reason}\n", output)
                self.assertIn("kept         none\n", output)
                self.assertEqual([path.name for path in (out / "assets").iterdir()], ["1.1.0"])

    def test_unsafe_manifest_path_skips_the_version_and_reports_it(self):
        cases = ("../../index.html", "/js/label.mjs", "js\\label.mjs", "js/./label.mjs", "js//label.mjs",
                 "js/.hidden.mjs", "C:label.mjs", "js", "files.json", "")
        for number, unsafe in enumerate(cases):
            with self.subTest(unsafe=unsafe):
                first, _ = self.site("1.0.0", name=f"-{number}")
                manifest = first / "assets/1.0.0/files.json"
                doc = json.loads(manifest.read_text())
                doc["files"][unsafe] = doc["files"]["js/label.mjs"]
                manifest.write_text(json.dumps(doc))
                out, output = self.site("1.1.0", previous=first, name=f"-{number}")
                self.assertEqual(self.build(out)["kept"], [])
                self.assertIn(f"skipped      1.0.0 unsafe path {unsafe!r}\n", output)
                self.assertIn("kept         none\n", output)
                self.assertEqual([path.name for path in (out / "assets").iterdir()], ["1.1.0"])
        self.assertEqual(sorted(path.name for path in self.root.iterdir()),
                         sorted(["data", "web", *(f"site-1.{minor}.0-{number}" for minor in (0, 1)
                                                  for number in range(len(cases)))]))
        for name in ("js/app.mjs", "site.css", "js/patcher/LICENSE", "fonts/a b.woff2"):
            self.assertTrue(safe_name(name), name)
        for name in (None, 1, "a\nb", "a/../b"):
            self.assertFalse(safe_name(name), name)

    def test_absent_unreachable_and_earlier_sources_carry_nothing(self):
        with socket.socket() as closed:
            closed.bind(("127.0.0.1", 0))
            port = closed.getsockname()[1]
        earlier, _ = self.site("1.0.0")
        (earlier / "site-build.json").write_text(json.dumps({"id": self.build(earlier)["id"]}))
        unnamed, _ = self.site("1.1.0")
        (unnamed / "site-build.json").write_text(json.dumps({"id": self.build(unnamed)["id"], "kept": ["1.1.0"]}))
        empty = self.root / "empty"
        empty.mkdir()
        for name, source in [("absent", self.root / "absent"), ("empty", empty), ("earlier", earlier),
                             ("unnamed", unnamed), ("refused", f"http://127.0.0.1:{port}/"),
                             ("missing", self.serve(empty)), ("plain", "http://site.invalid/"),
                             ("scheme", "ftp://site.invalid/")]:
            with self.subTest(name=name):
                out, output = self.site("2.0.0", previous=source, name=name)
                self.assertEqual(self.build(out)["kept"], [])
                self.assertIn("kept         none\n", output)
                self.assertNotIn("skipped", output)
                self.assertEqual([path.name for path in (out / "assets").iterdir()], ["2.0.0"])
                self.assertTrue((out / "index.html").is_file())

    def test_addresses_are_https_or_loopback_http_also_after_a_redirect(self):
        for address in ("https://site.test/app/", "http://127.0.0.1:8000/", "http://localhost/site/"):
            self.assertTrue(site_address(address), address)
        for address in ("http://site.test/", "ftp://site.test/", "file:///site", "https://", "site", "http://[::1"):
            self.assertFalse(site_address(address), address)
        request = Request("https://site.test/site-build.json")
        follow = lambda target: AcceptedRedirects().redirect_request(request, None, 302, "Found", {}, target)
        self.assertEqual(follow("https://other.test/site-build.json").full_url, "https://other.test/site-build.json")
        self.assertIsNone(follow("http://other.test/site-build.json"))

    def test_build_identity_covers_the_kept_versions(self):
        first, _ = self.site("1.0.0")
        alone, _ = self.site("2.0.0", name="-alone")
        kept, _ = self.site("2.0.0", previous=first, name="-kept")
        again, _ = self.site("2.0.0", previous=first, name="-again")
        self.assertNotEqual(self.build(alone)["id"], self.build(kept)["id"])
        self.assertEqual(self.build(kept), self.build(again))
        for site in (alone, kept):
            page = (site / "index.html").read_text()
            self.assertIn(f'<meta name="site-build" content="{self.build(site)["id"]}">\n'
                          '<meta name="site-version" content="2.0.0">', page)


if __name__ == "__main__":
    unittest.main()
