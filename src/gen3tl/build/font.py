"""Write language glyphs through the shared read-only font helpers."""

from __future__ import annotations

from ..core.rom import Rom
from ..core.font import (BACKGROUND, FORMATS, GLYPH_BYTES, INK, OUTSIDE, SHADOW, TILE_ORIGINS, FontTable,
                         by_style, check_copies, decode_glyph, encode_glyph, font_codes, glyph_size, shadow_end)


def write_glyphs(rom: Rom, fonts: list[FontTable], slots: dict[str, dict],
                 copies: dict[int, int] | None = None) -> list[tuple[int, int]]:
    """Write language-pack glyphs and copied glyph slots into every font; returns the changed byte ranges."""
    changed, originals = [], []
    for f in fonts:
        if f.style not in slots:
            raise ValueError(f"no glyphs for font style {f.style!r} ({f.name})")
        sources = {}
        for b, src in sorted((copies or {}).items()):
            if b >= f.count or src >= f.count or src in (copies or {}):
                raise ValueError(f"copy {src:#x} -> {b:#x} not possible in {f.name}")
            sources[src] = (f.glyph_bytes(rom, src), f.width(rom, src))
        originals.append(sources)
    for f, sources in zip(fonts, originals):
        for b, g in sorted(slots[f.style].items()):
            if b >= f.count:
                raise ValueError(f"slot {b:#x} beyond {f.name} ({f.count} glyphs)")
            off = f.glyphs + b * f.size
            rom.write(off, encode_glyph(g.pixels, f.fmt))
            rom.data[f.widths + b] = g.width
            changed += [(off, f.size), (f.widths + b, 1)]
        for b, src in sorted((copies or {}).items()):
            off = f.glyphs + b * f.size
            glyph, width = sources[src]
            rom.write(off, glyph)
            rom.data[f.widths + b] = width
            changed += [(off, f.size), (f.widths + b, 1)]
    return changed
