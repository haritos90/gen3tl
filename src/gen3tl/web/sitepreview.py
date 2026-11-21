"""Static link previews for the site's top-level pages."""

from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from ..catalog.po import read
from .pokedexshare import Canvas, HEIGHT, WIDTH, rgba
from .siteshell import GUIDE

TITLE = re.compile(r"(<title\b[^>]*>).*?(</title>)", re.S | re.I)


def definitions(web: Path) -> dict[str, dict[str, str]]:
    """Read the English preview keys beside the page fragments."""
    path = web / "fragments" / "page-previews.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def translated(web: Path) -> dict[str, dict[str, str]]:
    """Keep the pages whose file name can declare a language."""
    return {page: row for page, row in definitions(web).items() if GUIDE.fullmatch(page)}


def public_url(value: str | None) -> str:
    """Normalize an optional public HTTP origin and path prefix."""
    if not value:
        return ""
    parts = urlsplit(value)
    if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None
            or parts.password is not None or any(char in value for char in "?#\\")
            or any(char.isspace() or ord(char) < 32 for char in value)):
        raise ValueError("Public base must be an absolute HTTP(S) URL without credentials, query or fragment")
    return value.rstrip("/") + "/"


def texts(page: str, web: Path) -> dict[str, str]:
    """Translate only guides whose filename declares a language."""
    guide = GUIDE.fullmatch(page)
    source = page.replace(f".{guide[1]}.html", ".html") if guide and guide[1] else page
    row = definitions(web).get(source)
    if row is None:
        raise ValueError(f"{page}: missing preview texts")
    if not guide or not guide[1]:
        return row
    catalog = web / "i18n" / f"{guide[1]}.po"
    translated = {entry.msgid: entry.msgstr for entry in read(catalog)[1] if entry.msgstr}
    if any(value not in translated for value in row.values()):
        raise ValueError(f"{page}: missing preview translation")
    return {key: translated[value] for key, value in row.items()}


def picture(web: Path) -> bytes:
    """Render the existing brand with the site's light palette."""
    css = (web / "site.css").read_text(encoding="utf-8")
    palette = re.search(r":root\s*\{([^}]+)\}", css)[1]
    colours = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;", palette))
    font = json.loads((web / "fonts" / "share-bitmap.json").read_text(encoding="utf-8"))
    canvas = Canvas(font)
    canvas.rectangle(0, 0, WIDTH, HEIGHT, rgba(colours["bg"]))
    canvas.rectangle(0, HEIGHT - 18, WIDTH, 18, rgba(colours["accent"]))
    canvas.rounded(64, 64, WIDTH - 128, HEIGHT - 146, 24, rgba(colours["panel"]))
    canvas.sprite(web / "brand.png", 88, 50, 512)
    scale = 10
    canvas.text(640, (HEIGHT - 18 - font["height"] * scale) // 2, "gen3tl", scale,
                rgba(colours["fg"]), max_width=448)
    return canvas.save()


def rewrite(document: str, page: str, web: Path, base: str = "", image: str = "") -> str:
    """Escape preview metadata while retaining the existing title attributes."""
    row = texts(page, web)
    title, description = (html.escape(row[key], quote=True) for key in ("title", "description"))
    document, count = TITLE.subn(lambda match: match[1] + title + match[2], document)
    if count != 1 or document.count("</head>") != 1:
        raise ValueError(f"{page}: one title and head required")
    tags = [f'<meta name="description" content="{description}">',
            '<meta property="og:type" content="website">',
            '<meta property="og:site_name" content="gen3tl">',
            f'<meta property="og:title" content="{title}">',
            f'<meta property="og:description" content="{description}">',
            f'<meta name="twitter:title" content="{title}">',
            f'<meta name="twitter:description" content="{description}">']
    base = public_url(base)
    if base:
        if not image:
            raise ValueError("Public preview image required")
        canonical = html.escape(base + ("" if page == "index.html" else Path(page).stem), quote=True)
        image_url = html.escape(base + image, quote=True)
        tags += [f'<link rel="canonical" href="{canonical}">',
                 f'<meta property="og:url" content="{canonical}">',
                 f'<meta property="og:image" content="{image_url}">',
                 '<meta property="og:image:type" content="image/png">',
                 f'<meta property="og:image:width" content="{WIDTH}">',
                 f'<meta property="og:image:height" content="{HEIGHT}">',
                 '<meta property="og:image:alt" content="gen3tl">',
                 '<meta name="twitter:card" content="summary_large_image">',
                 f'<meta name="twitter:image" content="{image_url}">',
                 '<meta name="twitter:image:alt" content="gen3tl">']
    else:
        tags.append('<meta name="twitter:card" content="summary">')
    return document.replace("</head>", "\n".join(tags) + "\n</head>")


def generate(web: Path, out: Path, public_base: str | None = None, image_dir: str = "share/assets") -> None:
    """Add complete metadata to assembled pages without script execution."""
    base = public_url(public_base)
    if not definitions(web):
        return
    image = ""
    if base:
        content = picture(web)
        image = f"{image_dir}/{hashlib.sha256(content).hexdigest()}.png"
        destination = out / image
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    for path in sorted(out.glob("*.html")):
        path.write_text(rewrite(path.read_text(encoding="utf-8"), path.name, web, base, image), encoding="utf-8")
