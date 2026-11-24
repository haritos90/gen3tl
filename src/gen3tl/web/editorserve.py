"""Local preview of the site and the editor page on 127.0.0.1."""

from __future__ import annotations

import gzip
import hashlib
import os
import threading
from email.utils import formatdate
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .. import TOOLKIT_ROOT

WEB = TOOLKIT_ROOT / "web"
DATA = "/data/"
# images, fonts and archives are compressed already
COMPRESSIBLE = ("text/", "application/json", "application/javascript", "image/svg+xml")
_gzipped: dict[str, tuple[int, int, bytes]] = {}   # path -> (size, mtime_ns, gzip bytes)
_gzip_lock = threading.Lock()


def validator(st: os.stat_result) -> str:
    """The ETag of a file: its size and modification time."""
    return f'"{st.st_size:x}-{st.st_mtime_ns:x}"'


def gzipped(path: str, st: os.stat_result, raw: bytes) -> bytes:
    """The gzip bytes of a file, cached in memory by size and mtime."""
    with _gzip_lock:
        cached = _gzipped.get(path)
        if cached and cached[0] == st.st_size and cached[1] == st.st_mtime_ns:
            return cached[2]
    data = gzip.compress(raw, 6, mtime=0)
    with _gzip_lock:
        _gzipped[path] = (st.st_size, st.st_mtime_ns, data)
    return data


def page_body(path: Path, data: Path, web: Path) -> bytes:
    """Resolve project licences and list the served catalogs before sending complete HTML."""
    from . import sitecatalog, siteshell
    source = path.read_text(encoding="utf-8")
    if "<!-- site-shell:" in source and (path.parent / "fragments").is_dir():
        try:
            licenses = siteshell.registry_licenses(data)
        except (OSError, ValueError):
            licenses = None
        source = siteshell.rewrite(source, path.name, path.parent, licenses)
    codes = sitecatalog.index_codes(data) if path.parent == web else None   # built sites list root pages only
    return (source if codes is None else sitecatalog.listed(source, codes)).encode("utf-8")


class PreviewHandler(SimpleHTTPRequestHandler):
    """web/ at /, the data directory at /data/; nothing outside the two."""

    # Chrome caches fetch() responses of HTTP/1.1 servers only
    protocol_version = "HTTP/1.1"
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".js": "text/javascript",
                      ".mjs": "text/javascript", ".wasm": "application/wasm", ".json": "application/json", ".zip": "application/zip",
                      ".txt": "text/plain; charset=utf-8"}   # pack art names its letters

    def __init__(self, *args, data: Path, **kwargs):
        self.data = Path(data).resolve()
        super().__init__(*args, **kwargs)

    def translate_path(self, path: str) -> str:
        p = unquote(urlsplit(path).path)
        root, rel = (self.data, p[len(DATA):]) if p.startswith(DATA) else (Path(self.directory).resolve(), p)
        target = (root / rel.lstrip("/")).resolve()
        if target != root and root not in target.parents:
            return ""   # outside: not found
        if not p.startswith(DATA) and not p.endswith("/") and not target.exists():
            page = Path(str(target) + ".html").resolve()
            if root in page.parents and page.is_file():
                target = page
        return str(target)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-cache")   # revalidate every time, re-send only changes
        super().end_headers()

    def do_HEAD(self) -> None:
        """The headers of do_GET without the body."""
        self.do_GET(head=True)

    def do_GET(self, head: bool = False) -> None:
        """A file with validators, gzip-encoded when accepted; the rest as SimpleHTTP serves it."""
        path = self.translate_path(self.path)
        if path and os.path.isdir(path) and urlsplit(self.path).path.endswith("/"):
            index = Path(path) / "index.html"
            if index.is_file():
                path = str(index)
        try:
            st = os.stat(path) if path else None
        except OSError:
            st = None
        if st is None or not os.path.isfile(path):
            (super().do_HEAD if head else super().do_GET)()   # directories, index.html and 404s
            return
        etag = validator(st)
        body = None
        if Path(path).suffix == ".html":
            try:
                body = page_body(Path(path), self.data, Path(self.directory).resolve())
            except OSError:
                self.send_error(404, "File not found")
                return
            etag = f'"{hashlib.sha256(body).hexdigest()}"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.end_headers()
            return
        ctype = self.guess_type(path)
        try:
            if body is None:
                with open(path, "rb") as fh:
                    body = fh.read()
        except OSError:
            self.send_error(404, "File not found")
            return
        encoding = None
        if ctype.startswith(COMPRESSIBLE) and "gzip" in self.headers.get("Accept-Encoding", ""):
            body = gzip.compress(body, 6, mtime=0) if Path(path).suffix == ".html" else gzipped(path, st, body)
            encoding = "gzip"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", etag)
        self.send_header("Last-Modified", formatdate(st.st_mtime, usegmt=True))
        self.send_header("Vary", "Accept-Encoding")
        if encoding:
            self.send_header("Content-Encoding", encoding)
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def log_message(self, format, *args) -> None:   # noqa: A002
        pass


def preview_server(web: Path, data: Path, port: int = 8000, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """A server for the page and the data; port 0 picks a free port."""
    return ThreadingHTTPServer((host, port), partial(PreviewHandler, directory=str(web), data=data))
