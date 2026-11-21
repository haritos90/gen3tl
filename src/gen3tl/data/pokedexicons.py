"""Pokedex interface icons: font and move masks, and item icons."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..core.notes import has_prose
from ..build.font import INK, FontTable
from ..build.graphics import GraphicsError, bgr555, lz77_decompress
from ..check.render import indexed_png
from ..core.rom import Rom

ROM_BASE = 0x08000000
ITEM_PIC = 9 * 32   # nine 8 x 8 4bpp tiles
CATEGORIES = ("physical", "special", "status")
KEYS = ("base", "stride", "width", "height", "symbol")


def icon_file(category: str) -> str:
    """The file of a category's icon, relative to the sprite directory."""
    return f"icons/{category}.png"


def check(block: dict | None) -> list[str]:
    """Every problem that keeps a [pokedex] block from producing icons."""
    if not block:
        return ["[pokedex] block"]
    icons = block.get("icons")
    if not icons or not icons.get("split"):
        return ["[pokedex.icons.split] block"]
    split = icons["split"]
    problems = [f"[pokedex.icons.split] {key}" for key in KEYS if key not in split]
    symbol = split.get("symbol")
    if isinstance(symbol, list) and len(symbol) != len(CATEGORIES):
        problems.append("[pokedex.icons.split] symbol: one index per category")
    if not has_prose(split.get("evidence", "")):
        problems.append("[pokedex.icons.split] evidence")
    if "pokedollar" in icons:
        currency = icons["pokedollar"]
        if not isinstance(currency.get("font"), str) or not currency["font"]:
            problems.append("[pokedex.icons.pokedollar] font")
        if not isinstance(currency.get("glyph"), int):
            problems.append("[pokedex.icons.pokedollar] glyph")
        if not has_prose(currency.get("evidence", "")):
            problems.append("[pokedex.icons.pokedollar] evidence")
    if "items" in icons:
        for key in ("table", "count"):
            if not isinstance(icons["items"].get(key), int):
                problems.append(f"[pokedex.icons.items] {key}")
        if not has_prose(icons["items"].get("evidence", "")):
            problems.append("[pokedex.icons.items] evidence")
    return problems


def item_file(item_id: int) -> str:
    """The icon file of an item, relative to the sprite directory."""
    return f"icons/items/{item_id}.png"


def _pointer(data: bytes, at: int) -> int | None:
    if at + 4 > len(data):
        return None
    value = int.from_bytes(data[at:at + 4], "little")
    return value - ROM_BASE if ROM_BASE <= value < ROM_BASE + len(data) else None


def item_icon(data: bytes, table: int, item_id: int) -> tuple[list[list[int]], list[tuple[int, int, int]]] | None:
    """The pixel rows and palette of an item's icon, or None when the row is not a 24 x 24 icon."""
    pic_at, pal_at = _pointer(data, table + 8 * item_id), _pointer(data, table + 8 * item_id + 4)
    if pic_at is None or pal_at is None or data[pic_at] != 0x10:
        return None
    try:
        pic, _ = lz77_decompress(data, pic_at)
        palette = lz77_decompress(data, pal_at)[0] if data[pal_at] == 0x10 else data[pal_at:pal_at + 32]
    except GraphicsError:
        return None
    if len(pic) != ITEM_PIC or len(palette) < 32:
        return None
    rows = [[0] * 24 for _ in range(24)]
    for tile in range(9):
        ox, oy = (tile % 3) * 8, (tile // 3) * 8
        for y in range(8):
            for x in range(4):
                byte = pic[tile * 32 + y * 4 + x]
                rows[oy + y][ox + 2 * x] = byte & 15
                rows[oy + y][ox + 2 * x + 1] = byte >> 4
    colours = [bgr555(int.from_bytes(palette[2 * i:2 * i + 2], "little")) for i in range(16)]
    return rows, colours


def item_index(block: dict, data: bytes) -> dict[int, str]:
    """The icon file of every item whose table row holds a 24 x 24 icon."""
    items = (block.get("icons") or {}).get("items")
    if not items:
        return {}
    table, count = int(items["table"]), int(items["count"])
    return {item_id: item_file(item_id) for item_id in range(count) if item_icon(data, table, item_id) is not None}


def bitmap(data: bytes, at: int, width: int, height: int) -> list[list[int]]:
    """The palette indices of a 4bpp tile bitmap: 8 x 8 tiles in rows, two pixels per byte."""
    if width % 8 or height % 8 or at < 0 or at + width * height // 2 > len(data):
        raise ValueError(f"bitmap 0x{at:06x}: outside the ROM or not whole tiles")
    rows = [[0] * width for _ in range(height)]
    per_row = width // 8
    for tile in range(per_row * height // 8):
        ox, oy = (tile % per_row) * 8, (tile // per_row) * 8
        for y in range(8):
            for x in range(4):
                byte = data[at + tile * 32 + y * 4 + x]
                rows[oy + y][ox + 2 * x] = byte & 15
                rows[oy + y][ox + 2 * x + 1] = byte >> 4
    return rows


def mask(rows: list[list[int]], symbol: int) -> list[list[int]]:
    """1 where a pixel holds the symbol index, 0 elsewhere, over the drawn rows of the bitmap."""
    drawn = rows[:max((y + 1 for y, row in enumerate(rows) if any(row)), default=0)]
    out = [[1 if pixel == symbol else 0 for pixel in row] for row in drawn]
    if not any(any(row) for row in out):
        raise ValueError(f"symbol index {symbol} draws nothing")
    return out


def _pokedollar(block: dict, data: bytes, fonts: list[dict]) -> dict:
    currency = block["icons"]["pokedollar"]
    matches = [row for row in fonts if row.get("name") == currency["font"]]
    if len(matches) != 1:
        raise ValueError(f"pokedollar font {currency['font']!r} is not a unique [[font]] table")
    row = matches[0]
    font = FontTable(row["name"], int(row["glyphs"]), int(row["widths"]), int(row["count"]),
                     fmt=row.get("format", "16x16"))
    glyph = int(currency["glyph"])
    if not 0 <= glyph < font.count:
        raise ValueError(f"pokedollar glyph {glyph:#x} beyond {font.name}")
    rom = Rom(data)
    width = font.width(rom, glyph)
    if not 0 < width <= 16:
        raise ValueError(f"pokedollar glyph width {width} is outside the font cell")
    pixels = font.glyph(rom, glyph)
    rows = mask([[INK if pixel == INK else 0 for pixel in line[:width]] for line in pixels], INK)
    return {"category": "pokedollar", "image": icon_file("pokedollar"), "width": width, "height": len(rows),
            "png": indexed_png(rows, [(0, 0, 0), (255, 255, 255)], 1, transparent=0)}


def build(block: dict, data: bytes, fonts: list[dict] | None = None) -> dict:
    """The interface masks and item icons as byte strings."""
    problems = check(block)
    if problems:
        raise ValueError("the profile cannot drive Pokedex icons: " + ", ".join(problems))
    split = block["icons"]["split"]
    base, stride = int(split["base"]), int(split["stride"])
    width, height = int(split["width"]), int(split["height"])
    icons = []
    for index, category in enumerate(CATEGORIES):
        rows = mask(bitmap(data, base + index * stride, width, height), int(split["symbol"][index]))
        icons.append({"category": category, "image": icon_file(category), "width": width, "height": len(rows),
                      "png": indexed_png(rows, [(0, 0, 0), (255, 255, 255)], 1, transparent=0)})
    if "pokedollar" in block["icons"]:
        icons.append(_pokedollar(block, data, fonts or []))
    items = []
    if "items" in block["icons"]:
        table, count = int(block["icons"]["items"]["table"]), int(block["icons"]["items"]["count"])
        for item_id in range(count):
            decoded = item_icon(data, table, item_id)
            if decoded is None:
                continue
            rows, colours = decoded
            items.append({"id": item_id, "image": item_file(item_id), "png": indexed_png(rows, colours, 4, transparent=0)})
    return {"rom_sha1": hashlib.sha1(data).hexdigest(), "icons": icons, "items": items}


def index(block: dict) -> dict:
    """The dataset's icon record: the file of every interface mask."""
    problems = check(block)
    if problems:
        raise ValueError("the profile cannot drive Pokedex icons: " + ", ".join(problems))
    out = {"split": [icon_file(category) for category in CATEGORIES]}
    if "pokedollar" in block["icons"]:
        out["pokedollar"] = icon_file("pokedollar")
    return out


def write(out: Path, doc: dict) -> tuple[Path, int]:
    """Write the PNGs and the manifest; returns (manifest, bytes written)."""
    out = Path(out)
    bytes_written = 0
    files = {}
    for icon in doc["icons"]:
        path = out / icon["image"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(icon["png"])
        bytes_written += len(icon["png"])
        files[icon["category"]] = {key: icon[key] for key in ("image", "width", "height")}
    item_files = {}
    for item in doc.get("items", []):
        path = out / item["image"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(item["png"])
        bytes_written += len(item["png"])
        item_files[str(item["id"])] = item["image"]
    manifest = {"format": "gen3tl-pokedex-icons", "rom": {"sha1": doc["rom_sha1"]}, "files": files,
                "items": item_files}
    path = out / "icons" / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    encoding="utf-8")
    return path, bytes_written


def verify(out: Path, block: dict, rom_sha1: str) -> list[str]:
    """Every way the written icons are stale against the profile; empty when current."""
    problems = check(block)
    if problems:
        return [f"profile: {p}" for p in problems]
    path = Path(out) / "icons" / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"manifest: cannot read {path}: {exc}"]
    if manifest.get("rom", {}).get("sha1") != rom_sha1:
        return [f"rom sha1: manifest is {manifest.get('rom', {}).get('sha1')!r}, profile pins {rom_sha1!r}"]
    expected = [icon_file(category) for category in CATEGORIES]
    if "pokedollar" in block["icons"]:
        expected.append(icon_file("pokedollar"))
    missing = [image for image in expected if not (Path(out) / image).exists()]
    missing += [image for image in (manifest.get("items") or {}).values() if not (Path(out) / image).exists()]
    if "items" in (block["icons"]) and not manifest.get("items"):
        return ["items: regenerate the icons with gen3tl data icons"]
    if missing:
        return [f"files missing: {len(missing)}, first {missing[0]}"]
    return []
