"""The local site server: validators, compression and the catalog list."""
import gzip
import io
import json
import shutil
import tempfile
import threading
import unittest
from http.client import HTTPConnection, HTTPResponse
from pathlib import Path
from unittest.mock import Mock

from gen3tl.web import editorserve, sitecatalog


class PageRouteTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.web, self.data = root / "web", root / "data"
        self.web.mkdir()
        self.data.mkdir()
        self.pages = [path.name for path in editorserve.WEB.glob("*.html")]
        for name in self.pages:
            (self.web / name).write_text(f"<!doctype html><title>{name}</title>", encoding="utf-8")

    def request(self, path, method="GET", **headers):
        request = Mock()
        rows = [f"{method} {path} HTTP/1.1", "Host: localhost", "Connection: close"]
        rows += [f"{key}: {value}" for key, value in headers.items()]
        request.makefile.return_value = io.BytesIO(("\r\n".join(rows) + "\r\n\r\n").encode())
        output = io.BytesIO()
        request.sendall.side_effect = output.write
        editorserve.PreviewHandler(request, ("127.0.0.1", 0), None, directory=str(self.web), data=self.data)
        response_socket = Mock()
        response_socket.makefile.return_value = io.BytesIO(output.getvalue())
        response = HTTPResponse(response_socket, method=method)
        response.begin()
        return response, response.read()

    def test_every_page_has_short_and_legacy_addresses(self):
        self.assertTrue(self.pages)
        for name in self.pages:
            with self.subTest(page=name):
                short = "/" if name == "index.html" else "/" + name.removesuffix(".html")
                legacy, legacy_body = self.request("/" + name + "?next=editor")
                response, body = self.request(short + "?next=editor")
                self.assertEqual((response.status, legacy.status), (200, 200))
                self.assertEqual(body, legacy_body)
                self.assertEqual(response.getheader("Content-Type"), "text/html")
                head, body = self.request(short, "HEAD")
                self.assertEqual((head.status, body), (200, b""))
                self.assertEqual(head.getheader("Content-Length"), response.getheader("Content-Length"))

    def test_short_pages_share_validators_and_compression(self):
        response, body = self.request("/editor", **{"Accept-Encoding": "gzip"})
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Encoding"), "gzip")
        self.assertEqual(gzip.decompress(body), (self.web / "editor.html").read_bytes())
        etag = response.getheader("ETag")
        for path in ("/editor", "/editor.html"):
            cached, body = self.request(path, **{"If-None-Match": etag})
            self.assertEqual((cached.status, body), (304, b""))

    def test_complete_shell_licenses_and_validators_follow_registry_changes(self):
        shutil.copytree(editorserve.WEB / "fragments", self.web / "fragments")
        for name in ("index.html", "pokedex.html", "guide.html"):
            shutil.copyfile(editorserve.WEB / name, self.web / name)
        registry = self.data / "registry.json"
        registry.write_text(json.dumps({"schema": 1, "games": [], "site": {"licenses": [
            {"label_key": "Example terms", "url": "../terms?x=1&y=2"},
        ]}}))
        response, body = self.request("/", **{"Accept-Encoding": "gzip"})
        body = gzip.decompress(body)
        self.assertIn(b'class="mast-controls"', body)
        self.assertIn(b'href="terms?x=1&amp;y=2"', body)
        self.assertNotIn(b'license-gen3tl.txt', body)
        etag = response.getheader("ETag")
        self.assertEqual(self.request("/index", **{"If-None-Match": etag})[0].status, 304)
        self.assertEqual(self.request("/pokedex")[1].count(b'Example terms</a>'), 2)
        self.assertIn(b'Content licence', self.request("/guide")[1])
        self.assertNotIn(b'Example terms', self.request("/guide")[1])
        registry.write_text(json.dumps({"schema": 1, "games": [], "site": {"licenses": []}}))
        response, body = self.request("/", **{"If-None-Match": etag, "Accept-Encoding": "gzip"})
        self.assertEqual(response.status, 200)
        self.assertNotEqual(response.getheader("ETag"), etag)
        self.assertNotIn(b'Example terms', gzip.decompress(body))
        registry.write_text('{"invalid"')
        self.assertIn(b'license-gen3tl.txt', self.request("/")[1])
        shutil.rmtree(self.web / "fragments")
        self.assertEqual(self.request("/")[1], (self.web / "index.html").read_bytes())

    def test_root_pages_list_the_served_catalogs_as_a_built_site_does(self):
        page = "<!doctype html>\n<html><head>\n<title>Home</title></head></html>"
        (self.web / "nested").mkdir()
        for path in (self.web / "index.html", self.web / "nested/index.html", self.data / "record.html"):
            path.write_text(page, encoding="utf-8")
        index = self.data / "site" / "catalogs.json"
        index.parent.mkdir()
        index.write_text(json.dumps({"schema": 1, "catalogs": [{"code": "ru"}, {"code": "pt-BR"}], "guides": []}))
        site = self.web.parent / "site"
        (site / "data").mkdir(parents=True)
        shutil.copytree(index.parent, site / "data" / "site")
        (site / "index.html").write_text(page, encoding="utf-8")
        self.assertEqual(sitecatalog.list_catalogs(site), ["ru", "pt-BR"])
        response, body = self.request("/", **{"Accept-Encoding": "gzip"})
        self.assertEqual(gzip.decompress(body), (site / "index.html").read_bytes())
        self.assertIn(b'<meta name="site-catalogs" content="ru pt-BR">', gzip.decompress(body))
        self.assertEqual(self.request("/nested/")[1], page.encode())
        self.assertEqual(self.request("/data/record.html")[1], page.encode())
        etag = response.getheader("ETag")
        index.write_text(json.dumps({"schema": 1, "catalogs": []}))
        response, body = self.request("/", **{"If-None-Match": etag})
        self.assertEqual(response.status, 200)
        self.assertIn(b'<meta name="site-catalogs" content="">', body)
        index.write_text('{"schema": 1')
        self.assertEqual(self.request("/")[1], page.encode())

    def test_existing_paths_and_data_keep_their_resolution(self):
        (self.web / "exact").write_text("exact")
        (self.web / "exact.html").write_text("fallback")
        (self.web / "nested").mkdir()
        (self.web / "nested/index.html").write_text("directory")
        (self.web / "nested.html").write_text("fallback")
        (self.data / "record.html").write_text("data")
        self.assertEqual(self.request("/exact")[1], b"exact")
        self.assertEqual(self.request("/nested/")[1], b"directory")
        self.assertEqual(self.request("/nested")[0].getheader("Location"), "/nested/")
        self.assertEqual(self.request("/data/record")[0].status, 404)
        self.assertEqual(self.request("/data/record.html")[1], b"data")

    def test_aliases_cannot_escape_the_web_root(self):
        outside = self.web.parent / "secret.html"
        outside.write_text("secret")
        (self.web / "escape.html").symlink_to(outside)
        for path in ("/../secret", "/%2e%2e/secret", "/escape", "/data/../web/editor", "/missing", "/editor/"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0].status, 404)
        self.assertEqual(self.request("/%65ditor")[0].status, 200)


class PreviewServerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.web, self.data = root / "web", root / "data"
        self.web.mkdir()
        self.data.mkdir()
        (self.web / "index.html").write_text("<!doctype html><title>x</title>", encoding="utf-8")
        (self.web / "a.png").write_bytes(b"\x89PNG" + bytes(2000))
        self.doc = {"rows": list(range(2000))}
        (self.data / "index.json").write_text(json.dumps(self.doc), encoding="utf-8")
        self.server = editorserve.preview_server(self.web, self.data, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def get(self, path, **headers):
        conn = HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        conn.request("GET", path, headers=headers)
        response = conn.getresponse()
        body = response.read()
        conn.close()
        return response, body

    def test_json_is_gzip_encoded_when_accepted_and_plain_otherwise(self):
        response, body = self.get("/data/index.json", **{"Accept-Encoding": "gzip, br"})
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Encoding"), "gzip")
        self.assertEqual(response.getheader("Content-Type"), "application/json")
        self.assertEqual(response.getheader("Vary"), "Accept-Encoding")
        self.assertEqual(json.loads(gzip.decompress(body)), self.doc)
        self.assertLess(len(body), len(json.dumps(self.doc)))
        plain, body = self.get("/data/index.json")
        self.assertIsNone(plain.getheader("Content-Encoding"))
        self.assertEqual(json.loads(body), self.doc)
        self.assertEqual(plain.getheader("Cache-Control"), "no-cache")
        self.assertEqual(plain.version, 11)   # HTTP/1.1: browsers keep fetch() responses for revalidation

    def test_images_are_not_compressed(self):
        response, body = self.get("/a.png", **{"Accept-Encoding": "gzip"})
        self.assertEqual(response.status, 200)
        self.assertIsNone(response.getheader("Content-Encoding"))
        self.assertEqual(len(body), 2004)

    def test_a_conditional_request_gets_304_until_the_file_changes(self):
        first, _ = self.get("/data/index.json")
        etag = first.getheader("ETag")
        self.assertTrue(etag and first.getheader("Last-Modified"))
        again, body = self.get("/data/index.json", **{"If-None-Match": etag, "Accept-Encoding": "gzip"})
        self.assertEqual((again.status, body), (304, b""))
        self.assertEqual(again.getheader("ETag"), etag)
        changed = {"rows": [1]}
        (self.data / "index.json").write_text(json.dumps(changed), encoding="utf-8")
        fresh, body = self.get("/data/index.json", **{"If-None-Match": etag})
        self.assertEqual(fresh.status, 200)
        self.assertNotEqual(fresh.getheader("ETag"), etag)
        self.assertEqual(json.loads(body), changed)

    def test_head_carries_the_validators_without_a_body(self):
        conn = HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        conn.request("HEAD", "/data/index.json", headers={"Accept-Encoding": "gzip"})
        response = conn.getresponse()
        body = response.read()
        conn.close()
        self.assertEqual((response.status, body), (200, b""))
        self.assertEqual(response.getheader("ETag"), self.get("/data/index.json")[0].getheader("ETag"))
        self.assertEqual(response.getheader("Content-Encoding"), "gzip")

    def test_paths_outside_the_roots_and_directories_keep_their_answers(self):
        self.assertEqual(self.get("/../secret")[0].status, 404)
        self.assertEqual(self.get("/data/../web/index.html")[0].status, 404)
        self.assertEqual(self.get("/data/missing.json")[0].status, 404)
        response, body = self.get("/")
        self.assertEqual(response.status, 200)
        self.assertIn(b"<title>x</title>", body)


if __name__ == "__main__":
    unittest.main()
