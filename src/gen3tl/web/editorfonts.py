"""The editor's font review: every glyph sheet the translation installs."""

from __future__ import annotations

import copy
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..build import spritefont
from ..build.font import FontTable
from ..core.charmap import BASE_CHARS, NAMED_BYTES
from ..lang.langpack import (ART_CHARS, ART_VALUES, TOOLKIT_ROOT, Glyph, LangPack, LangPackError, insert_art,
                             parse_art, replace_art, set_source, style_geometry, style_problems)

FONTS = "fonts"


def code_of(b: int) -> str:
    return f"{b:02X}" if b < 0x100 else f"{b:03X}"


def art_rows(glyph, top: int, rows: int) -> list[str]:
    return ["".join(ART_CHARS[glyph.pixels[y][x]] for x in range(glyph.width)) for y in range(top, top + rows)]


def pack_root(root: Path | None) -> Path:
    return root or TOOLKIT_ROOT / "assets" / "lang"


def style_entry(code: str, style: str, top: int, rows: int, root: Path | None) -> dict:
    art = pack_root(root) / code / FONTS / f"{style}.txt"
    if not art.is_file():
        return {"top": top, "rows": rows, "art": None}
    try:
        source = art.relative_to(TOOLKIT_ROOT).as_posix()
    except ValueError:
        source = f"{code}/{FONTS}/{art.name}"
    return {"top": top, "rows": rows, "art": {"source": source, "path": f"{FONTS}/{art.name}"}}


def font_glyphs(pack: LangPack, count: int) -> list[dict]:
    """The glyphs of a font sheet with a character or name, the pack's included."""
    rows: dict[int, dict] = {b: {"char": c, "source": "base"} for b, c in BASE_CHARS.items()}
    rows.update({b: {"name": n, "source": "base"} for b, n in NAMED_BYTES.items()})
    rows.update({b: {"name": f"F9 {b - 0x100:02X}", "source": "base"}
                 for b in range(0x100, min(count, 0x200))})
    for char, b in pack.chars.items():
        if pack.sources[char] == "glyph":
            rows[b] = {"char": char, "source": "added"}
        elif pack.sources[char] == "copy":
            rows[b] = {"char": char, "source": "copied", "from": code_of(pack.copies[char])}
        elif b in pack.base_letter_slots():
            rows[b]["redraw"] = True
    for key in pack.glyphs:
        if isinstance(key, int):   # explicit art keeps the character identity
            rows[key] = {**rows.get(key, {"name": f"F9 {key - 0x100:02X}"}), "source": "changed"}
    slots = pack.glyph_slots()
    out = []
    for b in sorted(rows):
        if b < count:
            out.append({"code": code_of(b), "tile": b, **rows[b],
                        **({"width": slots[b].width} if b in slots else {})})
    return out


class BlankBase:
    """A base image of zero bytes: a sprite plan without the ROM's tiles."""

    def read(self, offset: int, n: int) -> bytes:
        return bytes(n)


def sprite_sheet(font: spritefont.SpriteFont, pack: LangPack) -> dict:
    """A sprite font as the build plans it for the pack (spritefont.plan)."""
    plan = spritefont.plan(font, BlankBase(), pack)
    table = list(plan.table)
    own = {b: c for c, b in pack.chars.items() if pack.sources[c] != "base"}
    glyphs = []
    for b in range(spritefont.TABLE):
        char, base = own.get(b), BASE_CHARS.get(b)
        if char in plan.added:
            row = {"char": char, "source": "added"}
        elif char in plan.copied:
            row = {"char": char, "source": "copied", "from": code_of(pack.copies[char])}
        elif base is not None and base in font.order:
            row = {"char": base, "source": "base"}
        elif base is not None and base in font.aliases:
            row = {"char": base, "source": "alias", "from": code_of(spritefont.BYTE_OF[font.aliases[base]])}
        else:
            continue   # the fallback tile
        glyphs.append({"code": code_of(b), "tile": table[b], **row,
                       "width": plan.advances[b] if font.stepped else font.advance})
    return {"kind": "sprite", "name": font.name, "style": font.style, "format": "8x8",
            "count": len(plan.sheet) // spritefont.TILE, "tiles": font.tiles, "advance": font.advance,
            **({"advances": list(plan.advances)} if font.stepped else {}),
            "fallback": font.fallback,
            "palette": {"ink": font.ink, "shadow": font.shadow, "background": font.background},
            "rom": {"sheet": font.sheet}, "table": table,
            "art": {str(t): art_rows(pack.glyphs[c], 0, font.cell_rows) for c, t in sorted(plan.added.items(),
                                                                                        key=lambda kv: kv[1])},
            "glyphs": glyphs}


def sprite_measures(prof, code: str, root: Path | None = None) -> dict:
    """The measure of every sprite font of the profile with the language pack `code`: font name -> layout.SpriteMeasure."""
    out = {}
    for font in spritefont.from_profile(prof):
        pack = LangPack.load(code, font.style, root, rows=font.cell_rows, top=0)
        out[font.name] = spritefont.measure(spritefont.plan(font, BlankBase(), pack))
    return out


def font_sheets(prof, code: str, root: Path | None = None) -> dict:
    """Every glyph sheet of the profile with the language pack `code`; `root` is the language packs' directory."""
    styles: dict[str, dict] = {}
    sheets = []
    packs: dict[str, LangPack] = {}
    entries = prof.get("font", [])
    for f, entry in zip(FontTable.from_profile(prof) if entries else [], entries):
        if f.style not in packs:
            top, rows = style_geometry(prof, f.style)
            packs[f.style] = LangPack.load(code, f.style, root, rows=rows, top=top)
            styles[f.style] = style_entry(code, f.style, top, rows, root)
        sheets.append({"kind": "font", "name": f.name, "style": f.style, "format": f.fmt, "count": f.count,
                       "codes": [f"{int(c):02X}" for c in entry.get("codes", [])],
                       "rom": {"glyphs": f.glyphs, "widths": f.widths},
                       "glyphs": font_glyphs(packs[f.style], f.count)})
    for font in spritefont.from_profile(prof):
        pack = LangPack.load(code, font.style, root, rows=font.cell_rows, top=0)
        styles.setdefault(font.style, style_entry(code, font.style, 0, font.cell_rows, root))
        sheets.append(sprite_sheet(font, pack))
    directory = pack_root(root) / code
    try:
        where = directory.relative_to(TOOLKIT_ROOT).as_posix()
    except ValueError:
        where = code
    return {"pack": where, "styles": styles, "sheets": sheets}


def write_art(out, code: str, sheets: dict, root: Path | None = None) -> list[Path]:
    """Copy the art file of every style into `out` where the sheets name it; returns the files written."""
    out = Path(out)
    shutil.rmtree(out / FONTS, ignore_errors=True)
    written = []
    for style, entry in sorted(sheets.get("styles", {}).items()):
        if entry.get("art"):
            target = out / entry["art"]["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(pack_root(root) / code / FONTS / f"{style}.txt", target)
            written.append(target)
    return written


GLYPH_KEYS = {"style", "width", "rows", "base"}
DRAWN = set("_#s")   # the art values a cell holds
COPY = re.compile(r"copy:([0-9A-Fa-f]{2})")


@dataclass
class GlyphEdit:
    """A redrawn glyph of an art file, checked against the file."""
    style: str
    key: str | int        # a character, or a slot
    code: int | None      # the byte it draws; None: no charmap row
    width: int
    rows: list[str]
    path: Path
    top: int              # the art's first cell row
    cell: int             # the art's cell rows
    changed: bool         # the rows or the width differ from the file
    copy: int | None = None   # main-font copies need a charmap source change
    new: bool = False     # the file has no block for it

    @property
    def label(self) -> str:
        return self.key if isinstance(self.key, str) else f"slot {self.key:03X}"


def art_styles(prof) -> dict[str, tuple[int, int, int, bool]]:
    """style -> (top row, cell rows, widest glyph, whether the main fonts' style rules apply)."""
    out: dict[str, tuple[int, int, int, bool]] = {}
    for f in FontTable.from_profile(prof) if prof.get("font") else []:
        top, rows = style_geometry(prof, f.style)
        widest = min(8 if f.fmt == "8x16" else 16, out.get(f.style, (0, 0, 16))[2])
        out[f.style] = (top, rows, widest, True)
    for font in spritefont.from_profile(prof):
        out.setdefault(font.style, (0, font.cell_rows, 8, False))
    return out


def _art(value, rows: int, widest: int, what: str) -> tuple[int, list[str]]:
    if not isinstance(value, dict) or set(value) != {"width", "rows"}:
        raise ValueError(f"{what} needs width and rows")
    width, art = value["width"], value["rows"]
    if type(width) is not int or not 1 <= width <= widest:
        raise ValueError(f"{what}: width must be 1..{widest}")
    if (not isinstance(art, list) or len(art) != rows
            or any(not isinstance(r, str) or len(r) != width or set(r) - DRAWN for r in art)):
        raise ValueError(f"{what}: {rows} rows of {width} pixels, each _, # or s")
    return width, list(art)


def glyph_edits(records, prof, code: str, root: Path | None = None) -> list[GlyphEdit]:
    """Validate exported redraws, copied letters and new slot blocks."""
    if not isinstance(records, list):
        raise ValueError("editor export glyphs must be a list")
    styles = art_styles(prof)
    pack = LangPack.load(code, None, root)
    chars = pack.chars
    texts: dict[str, dict] = {}
    out, seen = [], set()
    for record in records:
        if not isinstance(record, dict) or len(set(record) & {"char", "slot"}) != 1 \
                or set(record) - {"char", "slot"} != GLYPH_KEYS:
            raise ValueError("each glyph needs style, char or slot, width, rows and base")
        style = record["style"]
        if "char" in record:
            key = record["char"]
            if not isinstance(key, str) or len(key) != 1 or key.isspace():
                raise ValueError(f"glyph {key!r}: char must be one character")
        else:
            try:
                key = int(record["slot"], 16)
            except (TypeError, ValueError):
                raise ValueError(f"glyph slot {record['slot']!r}: not hexadecimal") from None
        label = key if isinstance(key, str) else f"slot {key:03X}"
        what = f"glyph {label} of {style}"
        if isinstance(key, int) and BASE_CHARS.get(key, "").isalpha() and key not in pack.base_letter_slots():
            raise ValueError(f"{what}: a base-letter slot needs its original letter declared as base")
        if isinstance(key, str) and pack.sources.get(key) == "base":
            raise ValueError(f"{what}: a base character requires explicit slot art")
        if style not in styles:
            raise ValueError(f"{what}: the profile draws no font in this style")
        if (style, key) in seen:
            raise ValueError(f"{what}: edited twice")
        seen.add((style, key))
        top, rows, widest, rules = styles[style]
        path = pack_root(root) / code / FONTS / f"{style}.txt"
        if style not in texts:
            texts[style] = parse_art(path.read_text(encoding="utf-8"), rows, top) if path.is_file() else {}
        current = texts[style].get(key)
        source = record["base"].get("source") if isinstance(record["base"], dict) else None
        copied = None
        if source == "rom":
            if (set(record["base"]) != {"source"} or not isinstance(key, int) or not rules
                    or key not in NAMED_BYTES and key not in pack.base_letter_slots() and not 0x100 <= key <= 0x1FF):
                raise ValueError(f"{what}: a ROM slot must be a named base glyph, declared base letter "
                                 "or 100..1FF in a main font")
            counts = [f.count for f in FontTable.from_profile(prof) if f.style == style]
            if any(key >= count for count in counts):
                raise ValueError(f"{what}: slot outside the profile's font")
            if current is not None:
                raise ValueError(f"{what}: the art changed after the edit; redraw it on the current pack")
        elif source is not None:
            found = COPY.fullmatch(source) if isinstance(source, str) else None
            if set(record["base"]) != {"source"} or found is None or not isinstance(key, str):
                raise ValueError(f"{what} base: a copied letter names its source as copy:<byte>")
            copied = int(found.group(1), 16)
            if ((pack.sources.get(key), pack.copies.get(key)) != ("copy", copied)
                    or current is not None and not rules):
                raise ValueError(f"{what}: the art changed after the edit; redraw it on the current pack")
        elif current is None:
            raise ValueError(f"{what}: {style}.txt draws no such glyph; the import redraws the pack's glyphs "
                             "and its copied letters only")
        width, art = _art({k: record[k] for k in ("width", "rows")}, rows, widest, what)
        base = _art(record["base"], rows, widest, f"{what} base") if source is None else None
        if source is None and base != (current.width, art_rows(current, top, rows)):
            raise ValueError(f"{what}: the art changed after the edit; redraw it on the current pack")
        if rules:
            pixels = [[ART_VALUES["."]] * 16 for _ in range(16)]
            for y, row in enumerate(art, top):
                pixels[y][:width] = [ART_VALUES[c] for c in row]
            problems = style_problems(Glyph(key, width, pixels), rows, top, widest)
            if problems:
                raise ValueError(f"{what}: {'; '.join(problems[:3])}")
        out.append(GlyphEdit(style, key, chars.get(key) if isinstance(key, str) else key, width, art, path, top,
                             rows, (width, art) != base, copied if rules else None, current is None))
    for char in sorted({e.key for e in out if e.copy is not None}):
        missing = []
        for style, (top, rows, _, _) in styles.items():
            path = pack_root(root) / code / FONTS / f"{style}.txt"
            if style not in texts:
                texts[style] = parse_art(path.read_text(encoding="utf-8"), rows, top) if path.is_file() else {}
            if char not in texts[style] and not any(e.style == style and e.key == char for e in out):
                missing.append(style)
        if missing:
            raise ValueError(f"glyph {char}: a drawn letter needs art in every style; draw it in "
                             f"{', '.join(missing)} too")
    return out


def edited_measure(measure, edits: list[GlyphEdit], style: str):
    """`measure` (layout.Measure) with the widths the glyph edits of `style` draw."""
    widths = {e.code: e.width for e in edits if e.style == style and e.code is not None}
    if not widths:
        return measure
    own, out = measure.glyph_width, copy.copy(measure)
    out.glyph_width = lambda b: widths.get(b, own(b))
    return out


def apply_glyph_edits(edits: list[GlyphEdit], dry_run: bool = False) -> list[Path]:
    """Write validated blocks and main-font source changes, preserving unrelated art."""
    by_path: dict[Path, list[GlyphEdit]] = {}
    for e in edits:
        if e.changed:
            by_path.setdefault(e.path, []).append(e)
    staged = {}
    for path, group in by_path.items():
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        order = list(LangPack.load(path.parents[1].name, None, path.parents[2]).chars)
        for e in group:
            later = order[order.index(e.key) + 1:] if isinstance(e.key, str) and e.key in order else ()
            text = (insert_art(text, e.key, e.width, e.rows, later) if e.new
                    else replace_art(text, e.key, e.width, e.rows))
        back = parse_art(text, group[0].cell, group[0].top)
        for e in group:
            if (back[e.key].width, art_rows(back[e.key], e.top, e.cell)) != (e.width, e.rows):
                raise LangPackError(f"{path.name}: glyph {e.label} does not read back")
        staged[path] = text
    for e in edits:
        if e.copy is not None:
            charmap = e.path.parents[1] / "charmap.tsv"
            text = set_source(staged.get(charmap) or charmap.read_text(encoding="utf-8"), e.key, "glyph")
            rows = [r for r in (line.split("\t") for line in text.split("\n")) if len(r) == 3 and r[0] == e.key]
            if [r[2] for r in rows] != ["glyph"]:
                raise LangPackError(f"charmap.tsv: the source of {e.key} does not read back")
            staged[charmap] = text
    if dry_run:
        return []
    for path, text in staged.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False,
                                         prefix=f".{path.name}.") as tmp:
            tmp.write(text)
        os.chmod(tmp.name, path.stat().st_mode & 0o777 if path.exists() else 0o644)
        os.replace(tmp.name, path)
    return list(staged)
