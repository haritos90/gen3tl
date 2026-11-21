"""Preview rendering: PNG output and text drawn with the game font."""

from __future__ import annotations

import re
import struct
import zlib
from pathlib import Path

from ..core.charmap import CTRL, CTRL_CODES, EOS, NEWLINE, PARAGRAPH, PLACEHOLDER, PREFIXED_ARG, SCROLL, CharmapError
from ..build.font import BACKGROUND, INK, OUTSIDE, SHADOW, FontTable, decode_glyph

# message-box colours used for previews (approximation of the in-game palette)
PALETTE = {
    "paper": (248, 248, 248),
    INK: (80, 80, 88),
    SHADOW: (208, 208, 200),
    "frame": (40, 80, 160),
    "label": (120, 120, 120),
    "overflow": (200, 40, 40),
}


def write_png(path: str | Path, rows: list[list[tuple[int, int, int]]]) -> None:
    h = len(rows)
    w = len(rows[0]) if h else 0
    raw = b"".join(b"\x00" + bytes(c for px in r for c in px) for r in rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    Path(path).write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


class GlyphSource:
    """Glyph lookup: ROM font, optionally overridden by language pack glyphs."""

    def __init__(self, rom, font: FontTable, overrides: dict | None = None):
        self.rom = rom
        self.font = font
        self.overrides = overrides or {}  # byte -> langpack.Glyph
        self._cache: dict[int, tuple[list[list[int]], int]] = {}

    def get(self, b: int) -> tuple[list[list[int]], int]:
        if b not in self._cache:
            if b in self.overrides:
                g = self.overrides[b]
                self._cache[b] = (g.pixels, g.width)
            else:
                self._cache[b] = (self.font.glyph(self.rom, b), self.font.width(self.rom, b))
        return self._cache[b]

    def width(self, b: int) -> int:
        return self.get(b)[1]


class Canvas:
    def __init__(self, w: int, h: int, bg=PALETTE["paper"]):
        self.w, self.h = w, h
        self.px = [[bg] * w for _ in range(h)]

    def put(self, x: int, y: int, c) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            self.px[y][x] = c

    def glyph(self, x: int, y: int, pixels, width: int, box: bool = False) -> None:
        for gy in range(14):
            for gx in range(width):
                v = pixels[gy][gx]
                if v in (INK, SHADOW):
                    self.put(x + gx, y + gy, PALETTE[v])
                elif box and v in (BACKGROUND, OUTSIDE):
                    self.put(x + gx, y + gy, (236, 236, 244) if (gx + gy) % 2 else (244, 244, 250))

    def rect(self, x0, y0, x1, y1, c) -> None:
        for x in range(x0, x1 + 1):
            self.put(x, y0, c); self.put(x, y1, c)
        for y in range(y0, y1 + 1):
            self.put(x0, y, c); self.put(x1, y, c)

    def scaled(self, s: int):
        out = []
        for row in self.px:
            r = [c for c in row for _ in range(s)]
            out.extend([r] * s)
        return out


def iter_text_bytes(data: bytes):
    """Yield ('char', byte) / ('break', kind) / ('skip', None) for encoded text."""
    i = 0
    while i < len(data):
        b = data[i]
        if b == EOS:
            return
        if b in (NEWLINE, SCROLL, PARAGRAPH):
            yield ("break", b); i += 1; continue
        if b == CTRL:
            code = data[i + 1] if i + 1 < len(data) else 0
            yield ("skip", None)
            i += 2 + CTRL_CODES.get(code, ("", 0))[1]
            continue
        if b == 0xF9 and i + 1 < len(data):
            yield ("char", 0x100 + data[i + 1]); i += 2; continue
        if b == PLACEHOLDER or b in PREFIXED_ARG:
            yield ("placeholder", data[i:i + 2]); i += 2; continue
        yield ("char", b)
        i += 1


def placeholder_px(text: str, m, cm) -> dict[bytes, int]:
    """Encoded placeholder -> its width in `m`, for the placeholders of `text`."""
    out: dict[bytes, int] = {}
    for tok in re.findall(r"\{[^}]*\}", text):
        try:
            b = cm.encode(tok, terminate=False)
        except CharmapError:
            continue
        if len(b) == 2 and (b[0] == PLACEHOLDER or b[0] in PREFIXED_ARG):
            out[b] = m.token_width(tok, cm)
    return out


def line_width(glyphs: GlyphSource, data: bytes, placeholder_width: int = 42,
               slots: dict[bytes, int] | None = None) -> int:
    """Width of encoded text; a placeholder in `slots` takes its width there, any other `placeholder_width`."""
    w = 0
    for kind, v in iter_text_bytes(data):
        if kind == "char":
            w += glyphs.width(v)
        elif kind == "placeholder":
            w += (slots or {}).get(bytes(v), placeholder_width)
    return w


def draw_slot(canvas: Canvas, x: int, y: int, width: int) -> None:
    """The blank of a placeholder `width` px wide: an underlined slot with a tick at each end."""
    n = max(0, width - 2)
    for gx in range(n):
        canvas.put(x + gx, y + 11, PALETTE["label"])
    if n:
        for gy in (9, 10):
            canvas.put(x, y + gy, PALETTE["label"])
            canvas.put(x + n - 1, y + gy, PALETTE["label"])


def draw_text(canvas: Canvas, glyphs: GlyphSource, x: int, y: int, data: bytes,
              line_height: int = 16, placeholder_width: int = 42,
              slots: dict[bytes, int] | None = None) -> int:
    """Draw encoded text; line breaks start a new line."""
    cx = x
    for kind, v in iter_text_bytes(data):
        if kind == "char":
            px, w = glyphs.get(v)
            canvas.glyph(cx, y, px, w)
            cx += w
        elif kind == "placeholder":
            w = (slots or {}).get(bytes(v), placeholder_width)
            draw_slot(canvas, cx, y, w)
            cx += w
        elif kind == "break":
            cx = x
            y += line_height
    return y + line_height


def read_png(path: str | Path) -> list[list[tuple[int, int, int]]]:
    """Read an 8-bit RGB PNG written by write_png (filter type 0 rows only)."""
    data = Path(path).read_bytes()
    pos = 8
    w = h = 0
    idat = b""
    while pos < len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + n]
        if tag == b"IHDR":
            w, h = struct.unpack(">II", body[:8])
        elif tag == b"IDAT":
            idat += body
        pos += 12 + n
    raw = zlib.decompress(idat)
    stride = 1 + 3 * w
    return [[tuple(raw[y * stride + 1 + 3 * x:y * stride + 4 + 3 * x]) for x in range(w)] for y in range(h)]


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def write_indexed_png(path: str | Path, rows: list[list[int]], palette: list[tuple[int, int, int]],
                      bit_depth: int = 8) -> None:
    """Write palette indices as an indexed PNG (colour type 3) with `palette` as PLTE."""
    # indexed_png also gives the editor the bytes
    Path(path).write_bytes(indexed_png(rows, palette, bit_depth))


def indexed_png(rows: list[list[int]], palette: list[tuple[int, int, int]], bit_depth: int = 8,
                transparent: int | None = None) -> bytes:
    """The bytes write_indexed_png writes; `transparent`: the palette index a tRNS chunk marks."""
    if bit_depth not in (1, 2, 4, 8) or not 1 <= len(palette) <= 1 << bit_depth:
        raise ValueError(f"{len(palette)} palette entries do not fit bit depth {bit_depth}")
    if transparent is not None and not 0 <= transparent < len(palette):
        raise ValueError(f"transparent index {transparent} is not in the palette")
    h = len(rows)
    w = len(rows[0]) if h else 0
    per = 8 // bit_depth
    raw = bytearray()
    for r in rows:
        raw.append(0)
        for x in range(0, w, per):
            b = 0
            for k in range(per):
                v = r[x + k] if x + k < w else 0
                b |= v << (8 - bit_depth * (k + 1))
            raw.append(b)
    alpha = bytes(0 if i == transparent else 255 for i in range(len(palette)))
    trns = _png_chunk(b"tRNS", alpha) if transparent is not None else b""
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, bit_depth, 3, 0, 0, 0))
            + _png_chunk(b"PLTE", bytes(c for rgb in palette for c in rgb))
            + trns
            + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + _png_chunk(b"IEND", b""))


class PngImage:
    """A decoded PNG: every pixel as RGB, and the palette indices of an indexed image."""

    def __init__(self, width: int, height: int, rgb: list[list[tuple[int, int, int]]],
                 palette: list[tuple[int, int, int]] | None = None, indices: list[list[int]] | None = None):
        self.width, self.height = width, height
        self.rgb = rgb
        self.palette = palette      # PLTE of an indexed image, else None
        self.indices = indices      # palette indices of an indexed image, else None


def read_png_image(path: str | Path) -> PngImage:
    """Read any non-interlaced PNG (all filter types; grey, RGB, indexed, with or without alpha)."""
    # png_image also decodes bytes for the editor
    return png_image(Path(path).read_bytes(), path)


def png_image(data: bytes, path: str | Path = "PNG") -> PngImage:
    """Decode PNG bytes as read_png_image does; `path` names the image in errors."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path}: not a PNG file")
    pos, idat, plte, hdr = 8, b"", None, None
    while pos < len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        tag, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + n]
        if tag == b"IHDR":
            hdr = struct.unpack(">IIBBBBB", body[:13])
        elif tag == b"PLTE":
            plte = [tuple(body[i:i + 3]) for i in range(0, len(body) - 2, 3)]
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
        pos += 12 + n
    if hdr is None:
        raise ValueError(f"{path}: no IHDR chunk")
    w, h, depth, ctype, _, _, interlace = hdr
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype)
    if channels is None or interlace or depth not in (1, 2, 4, 8, 16) or (ctype == 3 and depth == 16):
        raise ValueError(f"{path}: unsupported PNG (colour type {ctype}, bit depth {depth}, interlace {interlace})")
    if ctype == 3 and plte is None:
        raise ValueError(f"{path}: indexed PNG without a palette")
    raw = zlib.decompress(idat)
    bpp = max(1, channels * depth // 8)
    stride = (w * channels * depth + 7) // 8
    prev = bytearray(stride)
    lines = []
    for y in range(h):
        ft = raw[y * (stride + 1)]
        cur = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = cur[i - bpp] if i >= bpp else 0
            b = prev[i]
            if ft == 1:
                cur[i] = (cur[i] + a) & 0xFF
            elif ft == 2:
                cur[i] = (cur[i] + b) & 0xFF
            elif ft == 3:
                cur[i] = (cur[i] + (a + b) // 2) & 0xFF
            elif ft == 4:
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                cur[i] = (cur[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
            elif ft != 0:
                raise ValueError(f"{path}: unknown filter type {ft} in row {y}")
        lines.append(cur)
        prev = cur
    rgb, indices = [], ([] if ctype == 3 else None)
    top = (1 << depth) - 1
    for line in lines:
        if depth == 16:
            samples = [line[i] for i in range(0, len(line), 2)]   # high byte of each sample
        elif depth == 8:
            samples = list(line)
        else:
            per = 8 // depth
            samples = [(line[i // per] >> (8 - depth * (i % per + 1))) & top for i in range(w * channels)]
        row = []
        if ctype == 3:
            idx = samples[:w]
            indices.append(idx)
            row = [plte[v] if v < len(plte) else (0, 0, 0) for v in idx]
        elif ctype in (0, 4):
            scale = 1 if depth >= 8 else 255 // top
            row = [(samples[x * channels] * scale,) * 3 for x in range(w)]
        else:
            row = [tuple(samples[x * channels:x * channels + 3]) for x in range(w)]
        rgb.append(row)
    return PngImage(w, h, rgb, plte if ctype == 3 else None, indices)


# The editor's Fonts view draws the same sheet:
# 16 tiles a row in sheet order, spaced;
# tiles the pack adds are framed.
SHEET_COLUMNS = 16
SHEET_GAP = 2
TILE_BYTES = 32   # a 4bpp 8x8 tile
SHEET_COLORS = {
    "ink": (0xF0, 0xF0, 0xF0), "shadow": (0x38, 0x38, 0x40), "other": (0xC8, 0xB0, 0x48),
    "field": (0x68, 0x78, 0xA0), "gap": (0xC8, 0xCC, 0xD4), "mark": (0xA3, 0x47, 0x00),
}


def sheet_layout(count: int, columns: int = SHEET_COLUMNS) -> tuple[int, int, list[tuple[int, int]]]:
    """Width and height in px of a sheet of `count` tiles and each tile's top left corner."""
    pitch = 8 + SHEET_GAP
    rows = -(-count // columns)
    at = [(SHEET_GAP + t % columns * pitch, SHEET_GAP + t // columns * pitch) for t in range(count)]
    return SHEET_GAP + min(count, columns) * pitch, SHEET_GAP + rows * pitch, at


def sprite_sheet(sheet: bytes, tiles: int, ink: int, shadow: int, background: int) -> Canvas:
    """The 4bpp tiles of `sheet`, the pack's tiles from `tiles` on framed."""
    count = len(sheet) // TILE_BYTES
    w, h, at = sheet_layout(count)
    cv = Canvas(w, h, SHEET_COLORS["gap"])
    colour = {ink: SHEET_COLORS["ink"], shadow: SHEET_COLORS["shadow"], background: SHEET_COLORS["field"]}
    for t, (x, y) in enumerate(at):
        if t >= tiles:
            cv.rect(x - 1, y - 1, x + 8, y + 8, SHEET_COLORS["mark"])
        tile = sheet[t * TILE_BYTES:(t + 1) * TILE_BYTES]
        for py in range(8):
            for px in range(8):
                v = tile[py * 4 + px // 2] >> 4 * (px & 1) & 15
                cv.put(x + px, y + py, colour.get(v, SHEET_COLORS["other"]))
    return cv


def contact_sheet(paths: list[str | Path], out: str | Path, cols: int = 4, shrink: int = 1, gap: int = 4) -> Path:
    """Tile PNGs (same size) into one sheet, optionally shrunk by an integer factor."""
    imgs = [[row[::shrink] for row in read_png(p)[::shrink]] for p in paths]
    w, h = len(imgs[0][0]), len(imgs[0])
    white = (255, 255, 255)
    rows = []
    for r in range(0, len(imgs), cols):
        grp = imgs[r:r + cols]
        for y in range(h):
            line = []
            for im in grp:
                line += im[y] + [white] * gap
            line += [white] * ((w + gap) * (cols - len(grp)))
            rows.append(line)
        rows += [[white] * len(rows[-1]) for _ in range(gap)]
    write_png(out, rows)
    return Path(out)
