"""Language packs: the character slot map and the glyph art of a language."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .. import TOOLKIT_ROOT
from ..core.charmap import BASE_CHARS, NAMED_BYTES, PREFIXED_ARG, Charmap
from ..build.font import BACKGROUND, INK, OUTSIDE, SHADOW

ART_VALUES = {"_": BACKGROUND, "#": INK, "s": SHADOW, ".": OUTSIDE}
ART_CHARS = {v: k for k, v in ART_VALUES.items()}


class LangPackError(ValueError):
    pass


@dataclass
class Glyph:
    char: str | int
    width: int
    pixels: list[list[int]]  # 16 rows x 16 columns


@dataclass
class LangPack:
    """A pack under assets/lang/<code>/."""
    code: str
    chars: dict[str, int] = field(default_factory=dict)  # char -> byte
    sources: dict[str, str] = field(default_factory=dict)  # char -> base|glyph|copy
    copies: dict[str, int] = field(default_factory=dict)  # char -> copied ROM byte
    glyphs: dict[str | int, Glyph] = field(default_factory=dict)  # character or slot
    style: str | None = None

    @classmethod
    def load(cls, code: str, style: str | None = None, root: Path | None = None,
             rows: int = 14, top: int = 0) -> "LangPack":
        base = (root or TOOLKIT_ROOT / "assets" / "lang") / code
        pack = cls(code)
        for n, line in enumerate((base / "charmap.tsv").read_text(encoding="utf-8").splitlines(), 1):
            if not line or line.startswith("#") or line.startswith("char\t"):
                continue
            ch, byte, source = line.split("\t")
            kind, _, src = source.partition(":")
            if len(ch) != 1 or ch in pack.chars or not (source in ("glyph", "base") or kind == "copy" and src):
                raise LangPackError(f"charmap.tsv:{n}: bad row {line!r}")
            b = int(byte, 16)
            if source == "base":
                if BASE_CHARS.get(b) != ch or b in pack.chars.values():
                    raise LangPackError(f"charmap.tsv:{n}: base mapping must preserve its original character")
            elif b in BASE_CHARS or b in NAMED_BYTES or b >= min(PREFIXED_ARG) or b in pack.chars.values():
                raise LangPackError(f"charmap.tsv:{n}: {ch} on byte {b:02X}, which another character reads")
            pack.chars[ch] = b
            pack.sources[ch] = kind
            if kind == "copy":
                s = int(src, 16)
                if not BASE_CHARS.get(s, "").isalpha():
                    raise LangPackError(f"charmap.tsv:{n}: copy:{src} is not a letter of the base table")
                pack.copies[ch] = s
        if style:
            pack.style = style
            art = base / "fonts" / f"{style}.txt"
            text = art.read_text(encoding="utf-8") if art.exists() or "glyph" in pack.sources.values() else ""
            pack.glyphs = parse_art(text, rows, top)
            for key in pack.glyphs:
                if isinstance(key, int) and BASE_CHARS.get(key, "").isalpha() and key not in pack.base_letter_slots():
                    raise LangPackError(f"{style}.txt: slot {key:03X} needs its original letter declared as base")
                if isinstance(key, str) and pack.sources.get(key) == "base":
                    raise LangPackError(f"{style}.txt: base character {key!r} requires explicit slot art")
            missing = [c for c, s in pack.sources.items() if s == "glyph" and c not in pack.glyphs]
            if missing:
                raise LangPackError(f"{style}.txt: no art for {''.join(missing)}")
        return pack

    def allocated_slots(self) -> set[int]:
        """Only new slots require unused base bytes."""
        return {b for c, b in self.chars.items() if self.sources[c] != "base"}

    def base_letter_slots(self) -> set[int]:
        """Declared identity letters that accept explicit slot art."""
        return {b for c, b in self.chars.items() if self.sources[c] == "base" and c.isalpha()}

    def base_problems(self, rom, fonts) -> list[str]:
        """Check declared base glyph availability without modifying pixels."""
        problems = []
        for char, byte in self.chars.items():
            if self.sources[char] != "base":
                continue
            for font in fonts:
                if byte >= font.count:
                    problems.append(f"base character {char!r}: outside {font.name}")
                    continue
                width = font.width(rom, byte)
                maximum = 8 if font.fmt == "8x16" else 16
                if not 0 < width <= maximum:
                    problems.append(f"base character {char!r}: invalid width in {font.name}")
                if char != " " and not any(INK in row for row in font.glyph(rom, byte)):
                    problems.append(f"base character {char!r}: blank in {font.name}")
        return problems

    def charmap(self) -> Charmap:
        # Charmap already holds the base characters
        return Charmap({c: b for c, b in self.chars.items() if self.sources[c] != "base"})

    def glyph_slots(self, rom=None, font=None) -> dict[int, Glyph]:
        """byte -> glyph for every character drawn by this pack, with copied letters read from a ROM font."""
        out = {c if isinstance(c, int) else self.chars[c]: g for c, g in self.glyphs.items()
               if isinstance(c, int) or self.sources.get(c) == "glyph"}
        if font is not None:
            for c, s in self.copies.items():
                out[self.chars[c]] = Glyph(c, font.width(rom, s), font.glyph(rom, s))
        return out

    def copy_slots(self) -> dict[int, int]:
        """byte -> base ROM byte whose glyph and width the letter copies."""
        return {self.chars[c]: s for c, s in self.copies.items()}


def style_geometry(prof, style: str) -> tuple[int, int]:
    """(top row, rows) of a font style's cell."""
    fs = prof.get("font_style", {})
    if style == fs.get("style"):
        return 0, fs.get("cell_height", 14)
    g = prof.get("font_styles", {}).get(style)
    if g is None:
        raise LangPackError(f"profile has no geometry for font style {style!r}")
    return g["top"], g["rows"]


def load_styles(code: str, prof, root: Path | None = None) -> dict[str, "LangPack"]:
    """The language pack once per font style the profile's fonts use."""
    from ..build.font import FontTable
    styles = dict.fromkeys(f.style for f in FontTable.from_profile(prof))
    out = {}
    for s in styles:
        top, rows = style_geometry(prof, s)
        out[s] = LangPack.load(code, s, root, rows=rows, top=top)
    return out


def slots_by_style(packs: dict[str, "LangPack"]) -> dict[str, dict[int, "Glyph"]]:
    return {s: p.glyph_slots() for s, p in packs.items()}


def parse_art(text: str, cell_rows: int = 14, top: int = 0) -> dict[str | int, Glyph]:
    glyphs: dict[str | int, Glyph] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.startswith(("glyph ", "slot ")):
            i += 1
            continue
        parts = line.split()
        if len(parts) != 4 or parts[2] != "width":
            raise LangPackError(f"line {i + 1}: expected 'glyph <char> width <n>'")
        ch, width = parts[1], int(parts[3])
        if parts[0] == "slot":
            try:
                ch = int(ch, 16)
            except ValueError as e:
                raise LangPackError(f"line {i + 1}: invalid hexadecimal slot") from e
            if ch not in NAMED_BYTES and not BASE_CHARS.get(ch, "").isalpha() and not 0x100 <= ch <= 0x1FF:
                raise LangPackError(f"line {i + 1}: slot must be a named base glyph, base letter or 100..1FF")
        if ch in glyphs:
            raise LangPackError(f"line {i + 1}: duplicate glyph {ch}")
        rows = lines[i + 1:i + 1 + cell_rows]
        if len(rows) != cell_rows:
            raise LangPackError(f"glyph {ch}: expected {cell_rows} rows")
        px = [[OUTSIDE] * 16 for _ in range(16)]
        for y, row in enumerate(rows, top):
            if len(row) != width:
                raise LangPackError(f"glyph {ch} row {y}: length {len(row)} != width {width}")
            for x, c in enumerate(row):
                if c not in ART_VALUES or c == ".":
                    raise LangPackError(f"glyph {ch} row {y}: bad pixel {c!r}")
                px[y][x] = ART_VALUES[c]
        glyphs[ch] = Glyph(ch, width, px)
        i += 1 + cell_rows
    return glyphs


def format_art(g: Glyph, cell_rows: int = 14, top: int = 0) -> str:
    rows = ["".join(ART_CHARS[g.pixels[y][x]] for x in range(g.width)) for y in range(top, top + cell_rows)]
    head = f"slot {g.char:03X}" if isinstance(g.char, int) else f"glyph {g.char}"
    return f"{head} width {g.width}\n" + "\n".join(rows) + "\n"


def replace_art(text: str, key: str | int, width: int, rows: list[str]) -> str:
    """`text` with the block of glyph `key` redrawn as `rows`, `width` px wide; other blocks stay."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        parts = line.split()
        if len(parts) != 4 or parts[0] not in ("glyph", "slot") or parts[2] != "width":
            continue
        try:
            found = int(parts[1], 16) if parts[0] == "slot" else parts[1]
        except ValueError:
            continue
        if found == key:
            head = f"{parts[0]} {parts[1]} width {width}"
            return "\n".join(lines[:i] + [head] + list(rows) + lines[i + 1 + len(rows):])
    raise LangPackError(f"no art for {key if isinstance(key, str) else f'slot {key:03X}'}")


def insert_art(text: str, char: str | int, width: int, rows: list[str], later=()) -> str:
    """Insert characters in charmap order; append slots after existing blocks."""
    lines = text.split("\n")
    head = f"slot {char:03X}" if isinstance(char, int) else f"glyph {char}"
    block = [f"{head} width {width}", *rows]
    if isinstance(char, int):
        return text.rstrip("\n") + ("\n\n" if text.strip() else "") + "\n".join(block) + "\n"
    heads = [i for i, line in enumerate(lines) if line.startswith("glyph ") and len(line.split()) == 4]
    following = [i for i in heads if lines[i].split()[1] in later]
    if following:
        at = following[0]
        return "\n".join(lines[:at] + block + [""] + lines[at:])
    if heads:
        at = heads[-1] + 1 + len(rows)
        return "\n".join(lines[:at] + [""] + block + lines[at:])
    return text.rstrip("\n") + ("\n\n" if text.strip() else "") + "\n".join(block) + "\n"


def set_source(text: str, char: str, source: str) -> str:
    """charmap.tsv `text` with the source of `char` set; every other line stays."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        parts = line.split("\t")
        if len(parts) == 3 and parts[0] == char and not line.startswith("#"):
            lines[i] = "\t".join(parts[:2] + [source])
            return "\n".join(lines)
    raise LangPackError(f"charmap.tsv: no row for {char}")


def style_problems(g: Glyph, cell_rows: int = 14, top: int = 0, max_width: int = 16) -> list[str]:
    """Hard style failures of one glyph against the conventions of the original font."""
    p = g.pixels
    out = []
    if g.width > max_width:
        out.append(f"width {g.width} > {max_width}")
    for y in range(16):
        for x in range(16):
            v = p[y][x]
            inside = top <= y < top + cell_rows and x < g.width
            if inside and v == OUTSIDE:
                out.append(f"({x},{y}) outside value inside the cell")
            if not inside and v != OUTSIDE:
                out.append(f"({x},{y}) pixel beyond the cell")
            if v == SHADOW:
                src = [(x - 1, y), (x, y - 1), (x - 1, y - 1)]
                if not any(0 <= a < 16 and 0 <= b < 16 and p[b][a] == INK for a, b in src):
                    out.append(f"({x},{y}) shadow with no ink at left, above or above-left")
            if v == INK and y >= top + cell_rows - 1:
                out.append(f"({x},{y}) ink on the last cell row")
    return out
