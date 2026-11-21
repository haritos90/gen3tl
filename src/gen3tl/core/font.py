"""Gen 3 Latin font tables: 2bpp glyphs of 8x8 tiles plus a width table."""

from __future__ import annotations

from dataclasses import dataclass

from .rom import Rom

GLYPH_BYTES = 0x40
# 8x8 tile origins inside a glyph, in storage order
TILE_ORIGINS = ((0, 0), (8, 0), (0, 8), (8, 8))
FORMATS = {"16x16": TILE_ORIGINS, "8x16": ((0, 0), (0, 8))}


def glyph_size(fmt: str) -> int:
    return 0x10 * len(FORMATS[fmt])

# pixel values
OUTSIDE, INK, SHADOW, BACKGROUND = 0, 1, 2, 3


@dataclass
class FontTable:
    name: str
    glyphs: int
    widths: int
    count: int
    style: str = ""
    fmt: str = "16x16"

    @classmethod
    def from_profile(cls, prof) -> list["FontTable"]:
        """Every font of the profile; the dialogue-font copies first."""
        main = prof.get("font_style", {}).get("style", "")
        return [cls(f["name"], f["glyphs"], f["widths"], f["count"], f.get("style", main), f.get("format", "16x16"))
                for f in prof["font"]]

    @property
    def size(self) -> int:
        return glyph_size(self.fmt)

    def glyph_bytes(self, rom: Rom, gid: int) -> bytes:
        return rom.read(self.glyphs + gid * self.size, self.size)

    def glyph(self, rom: Rom, gid: int) -> list[list[int]]:
        return decode_glyph(self.glyph_bytes(rom, gid), self.fmt)

    def width(self, rom: Rom, gid: int) -> int:
        return rom.u8(self.widths + gid)

    def shadow_end(self, rom: Rom, gid: int) -> bool:
        """shadow_end of a glyph of this font."""
        return shadow_end(self.glyph(rom, gid), self.width(rom, gid))

    def all_bytes(self, rom: Rom) -> tuple[bytes, bytes]:
        return (rom.read(self.glyphs, self.count * self.size), rom.read(self.widths, self.count))


def by_style(fonts: list[FontTable]) -> dict[str, list[FontTable]]:
    out: dict[str, list[FontTable]] = {}
    for f in fonts:
        out.setdefault(f.style, []).append(f)
    return out


def font_codes(prof) -> dict[str, str]:
    """The {FONT xx} argument -> the font style it prints in, from the `codes` of the profile's fonts."""
    main = prof.get("font_style", {}).get("style", "")
    out: dict[str, str] = {}
    for f in prof["font"]:
        for c in f.get("codes", []):
            k, style = f"{int(c):02X}", f.get("style", main)
            if out.get(k, style) != style:
                raise ValueError(f"font code {k}: styles {out[k]} and {style}")
            out[k] = style
    return out


def decode_glyph(raw: bytes, fmt: str = "16x16") -> list[list[int]]:
    """16 rows of 16 pixel values (0..3); columns an 8x16 glyph does not have stay 0."""
    px = [[0] * 16 for _ in range(16)]
    for t, (ox, oy) in enumerate(FORMATS[fmt]):
        base = t * 0x10
        for r in range(8):
            lo, hi = raw[base + 2 * r], raw[base + 2 * r + 1]
            x = ox
            for byte in (hi, lo):
                for k in range(4):
                    px[oy + r][x] = (byte >> (6 - 2 * k)) & 3
                    x += 1
    return px


def shadow_end(px: list[list[int]], width: int) -> bool:
    """Whether the last column of a glyph holds no ink in every row of the stored glyph."""
    return 0 < width <= 16 and all(row[width - 1] != INK for row in px)


def encode_glyph(px: list[list[int]], fmt: str = "16x16") -> bytes:
    out = bytearray(glyph_size(fmt))
    for t, (ox, oy) in enumerate(FORMATS[fmt]):
        base = t * 0x10
        for r in range(8):
            row = px[oy + r][ox:ox + 8]
            hi = lo = 0
            for k in range(4):
                hi |= (row[k] & 3) << (6 - 2 * k)
                lo |= (row[4 + k] & 3) << (6 - 2 * k)
            out[base + 2 * r] = lo
            out[base + 2 * r + 1] = hi
    return bytes(out)


def check_copies(rom: Rom, fonts: list[FontTable]) -> list[str]:
    """Report differences between the copies of each font style; empty when every style's copies are identical."""
    problems = []
    for group in by_style(fonts).values():
        ref = group[0].all_bytes(rom)
        n = group[0].size
        for f in group[1:]:
            g, w = f.all_bytes(rom)
            if g != ref[0]:
                diff = [i for i in range(f.count) if g[i * n:(i + 1) * n] != ref[0][i * n:(i + 1) * n]]
                problems.append(f"{f.name}: {len(diff)} glyphs differ from {group[0].name}, first {diff[:8]}")
            if w != ref[1]:
                diff = [i for i in range(f.count) if w[i] != ref[1][i]]
                problems.append(f"{f.name}: {len(diff)} widths differ from {group[0].name}, first {diff[:8]}")
    return problems
