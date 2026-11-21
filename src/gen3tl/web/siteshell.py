"""Synchronize complete page chrome from shared HTML fragments."""

from __future__ import annotations

import html
import re
from pathlib import Path
from urllib.parse import urljoin

from .. import TOOLKIT_ROOT

WEB = TOOLKIT_ROOT / "web"
POINT = re.compile(r"\{\{([a-z_]+)\}\}")
MARKER = re.compile(r"<!-- (/?)site-shell: ([a-z-]+) -->")
REGION = re.compile(r"<!-- site-shell: ([a-z-]+) -->\n.*?\n<!-- /site-shell: \1 -->", re.S)
GUIDE = re.compile(r"(?:save-)?guide(?:\.([a-z]{2,3}(?:-[A-Za-z0-9]+)?))?\.html")
DEFAULT_LICENSES = [
    {"label_key": "gen3tl licence", "url": "license-gen3tl.txt"},
    {"label_key": "Font licenses", "url": "fonts/OFL.txt"},
]
CONTENT_LICENSE = {"label_key": "Content licence", "url": "https://creativecommons.org/licenses/by-sa/4.0/"}


def fragment(name: str, values: dict[str, str], web: Path) -> str:
    """Replace named points without evaluating expressions."""
    source = (web / "fragments" / f"{name}.html").read_text(encoding="utf-8").rstrip("\n")
    points = set(POINT.findall(source))
    if points != values.keys():
        raise ValueError(f"{name}: substitution points: {', '.join(sorted(points ^ values.keys()))}")
    return POINT.sub(lambda match: values[match[1]], source)


def license_links(licenses: list[dict], web: Path) -> str:
    """Escape metadata before inserting it into HTML."""
    return "".join(fragment("license-link", {"url": html.escape(row["url"], quote=True),
                                           "label": html.escape(row["label_key"], quote=True)}, web)
                   for row in licenses)


def render(kind: str, page: str, web: Path, licenses: list[dict] | None = None) -> str:
    """Render one declared page region."""
    links = license_links(DEFAULT_LICENSES if licenses is None else licenses, web)
    if kind == "footer":
        return fragment("footer", {"licenses": links}, web)
    if kind == "footer-static":
        return fragment("footer-static", {"licenses": license_links(DEFAULT_LICENSES + [CONTENT_LICENSE], web)}, web)
    if kind not in {"masthead", "masthead-workspace"}:
        raise ValueError(f"unknown region: {kind}")
    workspace = kind == "masthead-workspace"
    if workspace and page != "editor.html":
        raise ValueError("workspace masthead: editor page required")
    guide = GUIDE.fullmatch(page)
    current = "guide" if guide else Path(page).stem
    attributes = {tool: f'data-tool="{tool}" href="{tool}"' for tool in ("patch", "editor", "save", "pokedex")}
    if guide:
        suffix = ".html" if page.startswith("guide") else ""
        attributes = {tool: f'href="{tool}{suffix}"' for tool in attributes}
    if page == "pokedex.html":
        for tool in ("patch", "editor", "save"):
            attributes[tool] += f' id="link-{tool}"'
    if page == "submit.html":
        for tool in attributes:
            attributes[tool] += f' id="link-{tool}"'
    if workspace:
        attributes["editor"] = 'href="#start" id="link-start"'
        attributes["pokedex"] += ' id="link-pokedex"'
    guide_href = "guide" + (f".{guide[1]}" if guide and guide[1] else "")
    if page.startswith("guide"):
        guide_href += ".html"
    attributes["guide"] = f'href="{guide_href}"'
    if workspace:
        attributes["guide"] += (' target="_blank" rel="noopener" title="Contributor guide, in a new tab"'
                                ' data-ui-title="Contributor guide, in a new tab"')
    for tool in attributes:
        if tool == current:
            attributes[tool] += ' aria-current="page"'
    return fragment("masthead", {
        **{f"{tool}_attributes": value for tool, value in attributes.items()},
        "mast_attributes": (' data-site-language="display"' if page == "editor.html" else
                            ' data-site-menu-licenses=""' if page == "pokedex.html" else ""),
        "home_current": ' aria-current="page"' if current == "index" else "",
        "language_title": "Display language" if page == "editor.html" else "Site and game language",
        "menu_licenses": (fragment("menu-licenses", {"licenses": links}, web)
                          if page == "pokedex.html" else ""),
    }, web)


def rewrite(source: str, page: str, web: Path | None = None, licenses: list[dict] | None = None) -> str:
    """Reject missing or malformed boundaries before replacing regions."""
    web = Path(web or WEB)
    regions = list(REGION.finditer(source))
    expected = ["masthead", "footer-static" if GUIDE.fullmatch(page) else "footer"]
    if page == "editor.html":
        expected.append("masthead-workspace")
    if [match[1] for match in regions] != expected:
        raise ValueError("missing or unordered shell regions")
    markers = [(closing, kind) for closing, kind in MARKER.findall(source)]
    if markers != [(closing, kind) for kind in expected for closing in ("", "/")]:
        raise ValueError("unpaired shell markers")
    outside = REGION.sub("", source)
    if re.search(r"data-site-(?:mast|footer)\b", outside):
        raise ValueError("unmarked shell region")
    return REGION.sub(lambda match: (f"<!-- site-shell: {match[1]} -->\n"
                                    f"{render(match[1], page, web, licenses)}\n"
                                    f"<!-- /site-shell: {match[1]} -->"), source)


def pages(web: Path | None = None, *, sync: bool = False) -> tuple[list[Path], list[tuple[Path, str]]]:
    """Check all pages; synchronize only structurally valid input."""
    web = Path(web or WEB)
    paths = sorted(web.glob("*.html"))
    if not paths:
        raise ValueError(f"{web}: no page files")
    changes, problems = {}, []
    for path in paths:
        try:
            source = path.read_text(encoding="utf-8")
            wanted = rewrite(source, path.name, web)
        except (OSError, ValueError) as exc:
            problems.append((path, str(exc)))
            continue
        if source != wanted:
            changes[path] = wanted
    if problems:
        return [], sorted(problems + [(path, "out of sync") for path in changes])
    if sync:
        for path, wanted in changes.items():
            path.write_text(wanted, encoding="utf-8")
        return list(changes), []
    return [], [(path, "out of sync") for path in changes]


def registry_licenses(data: Path) -> list[dict] | None:
    """Resolve declared licence URLs relative to served pages."""
    import json
    from .editordata import validate_registry

    path = data / "registry.json"
    if not path.is_file():
        return None
    site = validate_registry(json.loads(path.read_text(encoding="utf-8"))).get("site", {})
    if "licenses" not in site:
        return None
    return [{**row, "url": urljoin("data/registry.json", row["url"])} for row in site["licenses"]]
