"""Versioned static assets and page references for deployment."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
from html import escape, unescape
from html.parser import HTMLParser
from http.client import HTTPException
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

from ..build.release import SEMVER

CODE = {".js", ".mjs", ".css", ".wasm"}
MANIFEST = "files.json"
KEPT_VERSIONS = 2
FILE_BYTES = 16 * 1024 * 1024
VERSION_BYTES = 64 * 1024 * 1024
REQUEST_SECONDS = 30
LOOPBACK = {"127.0.0.1", "localhost"}
SHA256 = re.compile(r"[0-9a-f]{64}")
CSS_URL = re.compile(r'''url\(\s*["']?([^\s)'";]+)["']?\s*\)|@import\s+["']([^"']+)["']''')
SCRIPT_URL = re.compile(r'''\bnew URL\(\s*["'](\.[^"']+)["']\s*,\s*import\.meta\.url''')
ATTRIBUTE = re.compile(r'''([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))''')


def local_path(url: str, page: Path) -> Path | None:
    """Resolve a local URL within the site tree."""
    parts = urlsplit(url)
    if parts.scheme or parts.netloc or not parts.path:
        return None
    name = unquote(parts.path)
    return Path(posixpath.normpath(name.lstrip("/") if name.startswith("/") else (page.parent / name).as_posix()))


def asset_plan(plan: list[tuple[Path, Path]], web: Path, out: Path, version: str):
    """Keep relative dependencies beside versioned scripts and styles."""
    sources = {path.relative_to(web): path for path, _ in plan if path.is_file()}
    versioned = {name for name in sources if name.suffix in CODE or name.name == "LICENSE"}
    assets = set(versioned)
    for name in sorted(assets):
        if name.suffix not in {".css", ".js", ".mjs"}:
            continue
        source = sources[name].read_text(encoding="utf-8")
        if name.suffix == ".css":
            references = (match[1] or match[2] for match in CSS_URL.finditer(source))
            kind = "CSS"
        else:
            references = (match[1] for match in SCRIPT_URL.finditer(source))
            kind = "script"
        for url in references:
            resource = local_path(url, name)
            if resource is not None:
                if resource not in sources:
                    raise ValueError(f"{name}: missing {kind} resource {resource}")
                assets.add(resource)
    targets = {name: Path("assets") / version / name for name in assets}
    relocated = [(path, out / targets[path.relative_to(web)] if path.relative_to(web) in versioned else destination)
                 for path, destination in plan if not path.is_dir()]
    relocated.extend((sources[name], out / targets[name]) for name in sorted(assets) if name not in versioned)
    return relocated, targets


class PageAssets(HTMLParser):
    """Rewrite asset attributes while preserving all other markup."""

    def __init__(self, document: str, page: Path, targets: dict[Path, Path]):
        super().__init__(convert_charrefs=False)
        self.document, self.page, self.targets = document, page, targets
        self.version = next((target.parts[1] for target in targets.values()), "")
        self.lines = [0]
        for line in document.splitlines(keepends=True):
            self.lines.append(self.lines[-1] + len(line))
        self.edits = []

    def handle_starttag(self, tag, attrs):
        if tag not in {"script", "link"}:
            return
        attributes = dict(attrs)
        required = tag == "script" or bool(set((attributes.get("rel") or "").lower().split())
                                          & {"stylesheet", "modulepreload"})
        line, column = self.getpos()
        offset = self.lines[line - 1] + column
        for match in ATTRIBUTE.finditer(self.get_starttag_text()):
            if match[1].lower() != ("src" if tag == "script" else "href"):
                continue
            group = next(n for n in (2, 3, 4) if match[n] is not None)
            url = unescape(match[group])
            name = local_path(url, self.page)
            target = self.targets.get(name)
            if target is None:
                if required and name is not None:
                    if tag != "script" or name.parts[:1] != ("data",) or not self.version:
                        raise ValueError(f"{self.page}: unversioned asset {url}")
                    # Generated data scripts keep their path.
                    parts = urlsplit(url)
                    value = urlunsplit(("", "", parts.path, f"v={self.version}", parts.fragment))
                    self.edits.append((offset + match.start(group), offset + match.end(group), escape(value, quote=True)))
                continue
            parts = urlsplit(url)
            relative = posixpath.relpath(target.as_posix(), self.page.parent.as_posix())
            value = urlunsplit(("", "", relative, parts.query, parts.fragment))
            self.edits.append((offset + match.start(group), offset + match.end(group), escape(value, quote=True)))

    handle_startendtag = handle_starttag

    def rewrite(self) -> str:
        self.feed(self.document)
        self.close()
        result = self.document
        for start, end, value in reversed(self.edits):
            result = result[:start] + value + result[end:]
        return result


def rewrite_pages(out: Path, targets: dict[Path, Path]) -> None:
    """Version page assets, including generated share targets."""
    for page in sorted(out.rglob("*.html")):
        if page.is_relative_to(out / "data") or page.is_relative_to(out / "assets"):
            continue
        document = page.read_text(encoding="utf-8")
        rewritten = PageAssets(document, page.relative_to(out), targets).rewrite()
        if document != rewritten:
            page.write_text(rewritten, encoding="utf-8")


def write_manifest(out: Path, version: str) -> None:
    """List a version's files so that a published site can be read."""
    folder = out / "assets" / version
    files = {path.relative_to(folder).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(folder.rglob("*")) if path.is_file() and path != folder / MANIFEST}
    folder.mkdir(parents=True, exist_ok=True)
    (folder / MANIFEST).write_text(json.dumps({"version": version, "files": files}, indent=1) + "\n", encoding="utf-8")


def site_address(source: str) -> bool:
    """Accept HTTPS, and plain HTTP only on the loopback host."""
    try:
        parts = urlsplit(source)
        return bool(parts.hostname) and (parts.scheme == "https"
                                         or parts.scheme == "http" and parts.hostname in LOOPBACK)
    except ValueError:
        return False


class AcceptedRedirects(HTTPRedirectHandler):
    """Follow a redirect only to an accepted address."""

    def redirect_request(self, request, stream, code, message, headers, target):
        if not site_address(target):
            return None
        return super().redirect_request(request, stream, code, message, headers, target)


def source_reader(source: str):
    """Read bounded files of an earlier site from a directory or an address."""
    if urlsplit(source).scheme in {"http", "https"}:
        if not site_address(source):
            raise ValueError(f"{source}: not an accepted address")
        # A local server is never behind a proxy.
        handlers = [ProxyHandler({})] if urlsplit(source).hostname in LOOPBACK else []
        opener, base = build_opener(AcceptedRedirects, *handlers), source.rstrip("/") + "/"

        def read(name: str, limit: int) -> bytes | None:
            try:
                with opener.open(base + quote(name), timeout=REQUEST_SECONDS) as response:
                    return response.read(limit + 1)
            except (OSError, ValueError, HTTPException) as exc:
                if isinstance(exc, HTTPError):
                    exc.close()
                return None
        return read
    root = Path(source).resolve()

    def read(name: str, limit: int) -> bytes | None:
        path = root / name
        try:
            # A link may lead outside the site.
            if path.resolve() != path or not path.is_file():
                return None
            with path.open("rb") as stream:
                return stream.read(limit + 1)
        except OSError:
            return None
    return read


def safe_name(name) -> bool:
    """Accept a relative path that stays inside its folder."""
    # A colon can name a drive.
    return (isinstance(name, str) and not any(char in "\\:" or ord(char) < 32 for char in name)
            and all(part and not part.startswith(".") for part in name.split("/")))


def read_version(read, version: str) -> tuple[bytes, dict[str, bytes]]:
    """Read one version whole, each file checked against its manifest."""
    folder = f"assets/{version}/"
    manifest = read(folder + MANIFEST, FILE_BYTES)
    if manifest is None:
        raise ValueError("manifest missing")
    try:
        doc = json.loads(manifest)
        files = doc["files"]
        valid = doc["version"] == version and all(SHA256.fullmatch(digest) for digest in files.values())
    except (ValueError, TypeError, KeyError, AttributeError):
        valid = False
    if not valid:
        raise ValueError("manifest invalid")
    # A file cannot also be a folder.
    folders = {parent.as_posix() for name in files for parent in PurePosixPath(name).parents}
    contents, size = {}, 0
    for name, digest in sorted(files.items()):
        if not safe_name(name) or name in folders or name == MANIFEST:
            raise ValueError(f"unsafe path {name!r}")
        data = read(folder + name, FILE_BYTES)
        if data is None:
            raise ValueError(f"file missing {name}")
        if len(data) > FILE_BYTES:
            raise ValueError(f"file too large {name}")
        size += len(data)
        if size > VERSION_BYTES:
            raise ValueError("version too large")
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f"hash mismatch {name}")
        contents[name] = data
    return manifest, contents


def carry_versions(source: str, out: Path, current: str) -> tuple[list[tuple[str, int]], list[tuple[str, str]]]:
    """Carry the newest earlier versions of a site into the new tree."""
    kept, skipped = [], []
    try:
        read = source_reader(source)
        build = json.loads(read("site-build.json", FILE_BYTES) or b"null")
        names = [build["version"], *build.get("kept", [])]
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        # A site of the earlier form names no version.
        return kept, skipped
    earlier = {name for name in names if isinstance(name, str) and SEMVER.fullmatch(name) and name != current}
    for version in sorted(earlier, key=lambda name: tuple(map(int, name.split("."))), reverse=True)[:KEPT_VERSIONS]:
        try:
            manifest, contents = read_version(read, version)
        except ValueError as exc:
            skipped.append((version, str(exc)))
            continue
        folder = out / "assets" / version
        for name, data in {**contents, MANIFEST: manifest}.items():
            (folder / name).parent.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(data)
        kept.append((version, len(contents)))
    return kept, skipped


def write_build(out: Path, version: str, kept=()) -> str:
    """Identify the complete published tree before embedding its identity."""
    digest = hashlib.sha256(version.encode("utf-8") + b"\0")
    pages = []
    for path in sorted(out.rglob("*")):
        if not path.is_file():
            continue
        name = path.relative_to(out)
        digest.update(name.as_posix().encode("utf-8") + b"\0")
        with path.open("rb") as stream:
            digest.update(hashlib.file_digest(stream, "sha256").digest())
        if name.suffix == ".html" and path.parent == out:
            pages.append(path)
    identity = digest.hexdigest()
    (out / "site-build.json").write_text(json.dumps({"id": identity, "version": version, "kept": list(kept)}) + "\n",
                                         encoding="utf-8")
    meta = (f'<meta name="site-build" content="{identity}">\n'
            f'<meta name="site-version" content="{escape(version, quote=True)}">')
    for page in pages:
        document = page.read_text(encoding="utf-8")
        rewritten, count = re.subn(r"(<head\b[^>]*>)", lambda match: match[0] + "\n" + meta,
                                  document, count=1, flags=re.IGNORECASE)
        page.write_text(rewritten if count else meta + "\n" + document, encoding="utf-8")
    return identity
