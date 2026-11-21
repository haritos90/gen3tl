"""Compose versioned labels without storing original artwork."""

from __future__ import annotations

import copy
import json

from .graphics import GraphicsError, area_png
from ..check.render import png_image


def load(path, version: str, facts: dict) -> dict:
    asset = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(asset, dict):
        raise GraphicsError("graphic label asset must be an object")
    if asset.get("schema") != "1.0.0":
        raise GraphicsError("unsupported graphic label schema")
    if asset.keys() - {"schema", "text", "gap", "top", "align", "glyphs"}:
        raise GraphicsError("graphic label asset contains ROM geometry")
    if not isinstance(facts, dict) or not facts:
        raise GraphicsError("graphic label requires verified ROM geometry")
    glyphs = asset.get("glyphs", {})
    if not isinstance(glyphs, dict) or any(
            not isinstance(glyph, dict) or "parts" in glyph
            for glyph in glyphs.values()):
        raise GraphicsError("graphic label source rectangles belong in profile facts")
    spec = copy.deepcopy(facts)
    spec.update({k: v for k, v in asset.items() if k != "glyphs"})
    spec.setdefault("glyphs", {}).update(glyphs)
    text = spec.get("text")
    if not isinstance(text, str) or "@VERSION@" not in text:
        raise GraphicsError("graphic label needs a version token")
    spec["text"] = text.replace("@VERSION@", version)
    return spec


def _number(spec, key, minimum=0, maximum=1024):
    value = spec.get(key)
    if type(value) is not int or not minimum <= value <= maximum:
        raise GraphicsError(f"graphic label: invalid {key}")
    return value


def _glyph(sheet, spec, background):
    if not isinstance(spec, dict):
        raise GraphicsError("graphic label glyph must be an object")
    width = _number(spec, "width", 1, 32)
    height = _number(spec, "height", 1, 32)
    pixels = [[background] * width for _ in range(height)]
    if ("parts" in spec) == ("rows" in spec):
        raise GraphicsError("glyph needs source parts or drawn rows")
    if "parts" in spec:
        if not isinstance(spec["parts"], list) or not spec["parts"]:
            raise GraphicsError("glyph source parts must be a nonempty array")
        for part in spec["parts"]:
            if not isinstance(part, list) or len(part) != 6 or any(type(v) is not int or v < 0 for v in part):
                raise GraphicsError("invalid glyph source rectangle")
            sx, sy, w, h, x, y = part
            if not w or not h or sx + w > sheet.width or sy + h > sheet.height or x + w > width or y + h > height:
                raise GraphicsError("glyph source rectangle exceeds its bounds")
            for dy in range(h):
                pixels[y + dy][x:x + w] = sheet.pixels[sy + dy][sx:sx + w]
    else:
        rows = spec["rows"]
        if not isinstance(rows, list) or len(rows) != height or any(not isinstance(r, str) or len(r) != width for r in rows):
            raise GraphicsError("drawn glyph dimensions differ")
        colours = spec.get("colours", {})
        if not isinstance(colours, dict):
            raise GraphicsError("glyph colours must be an object")
        for y, row in enumerate(rows):
            for x, char in enumerate(row):
                if char not in colours:
                    raise GraphicsError(f"missing glyph colour: {char!r}")
                value = colours[char]
                if type(value) is not int or not 0 <= value < 1 << sheet.graphic.bpp:
                    raise GraphicsError("glyph colour exceeds the palette")
                pixels[y][x] = value
    return pixels


def image(sheet, spec: dict, tiles: int):
    """Return an area-form image containing appended label tiles."""
    if not isinstance(spec, dict):
        raise GraphicsError("graphic label specification must be an object")
    width = _number(spec, "width", 8)
    height = _number(spec, "height", 8)
    if width % 8 or height % 8:
        raise GraphicsError("graphic label dimensions must align to tiles")
    start = _number(spec, "start_tile", 0, 65535)
    if start != sheet.graphic.tiles or tiles != start + width * height // 64:
        raise GraphicsError("graphic label must fill its appended tiles")
    background = _number(spec, "background", 0, (1 << sheet.graphic.bpp) - 1)
    gap = _number(spec, "gap", 0, 16)
    top = _number(spec, "top", 0, height - 1)
    text = spec.get("text")
    if not isinstance(text, str) or not text or "@" in text:
        raise GraphicsError("graphic label text is unresolved")
    glyphs = spec.get("glyphs", {})
    if not isinstance(glyphs, dict):
        raise GraphicsError("graphic label glyphs must be an object")
    if any(char not in glyphs for char in text):
        raise GraphicsError("graphic label contains an undrawn character")
    letters = [_glyph(sheet, glyphs[char], background) for char in text]
    measured = sum(len(g[0]) for g in letters) + gap * (len(letters) - 1)
    if measured > width or any(top + len(g) > height for g in letters):
        raise GraphicsError("graphic label exceeds its declared area")
    if spec.get("align") not in ("left", "right", "center"):
        raise GraphicsError("invalid graphic label alignment")
    x = {"left": 0, "right": width - measured, "center": (width - measured) // 2}[spec["align"]]
    label = [[background] * width for _ in range(height)]
    for glyph in letters:
        for y, row in enumerate(glyph):
            label[top + y][x:x + len(row)] = row
        x += len(glyph[0]) + gap
    pixels = [[0] * sheet.width for _ in range((tiles + sheet.width_tiles - 1) // sheet.width_tiles * 8)]
    for i in range(width * height // 64):
        sx, sy = i % (width // 8) * 8, i // (width // 8) * 8
        dx, dy = (start + i) % sheet.width_tiles * 8, (start + i) // sheet.width_tiles * 8
        for y in range(8):
            pixels[dy + y][dx:dx + 8] = label[sy + y][sx:sx + 8]
    return png_image(area_png(pixels, sheet.graphic.bpp))
