"""LZ77 and tile/palette primitives shared by discovery and the build."""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import notes as structured
from .rom import Rom

LZ77_TYPE = 0x10
LZ77_WINDOW = 0x1000
LZ77_MIN, LZ77_MAX = 3, 18
LZ77_MIN_DISP = 2          # VRAM decoder writes 16 bits: no copy from last byte

# fallback palette: index 0 magenta, 1-15 greys
GREY = [(255, 0, 255)] + [(235 - 15 * i,) * 3 for i in range(1, 16)]

Rgb = tuple[int, int, int]


class GraphicsError(Exception):
    pass


def lz77_decompress(data: bytes | bytearray, off: int = 0) -> tuple[bytes, int]:
    """Decode the LZ77 block at `off`. Returns the data and the block length in bytes."""
    if off + 4 > len(data) or data[off] != LZ77_TYPE:
        raise GraphicsError(f"no LZ77 header at {off:#x}")
    size = int.from_bytes(data[off + 1:off + 4], "little")
    out = bytearray()
    i, n = off + 4, len(data)
    while len(out) < size:
        if i >= n:
            raise GraphicsError(f"LZ77 block at {off:#x} runs past the end of the data")
        flags = data[i]
        i += 1
        for bit in range(8):
            if len(out) >= size:
                break
            if flags & (0x80 >> bit):
                if i + 2 > n:
                    raise GraphicsError(f"LZ77 block at {off:#x} runs past the end of the data")
                b1, b2 = data[i], data[i + 1]
                i += 2
                disp = (((b1 & 0xF) << 8) | b2) + 1
                if disp > len(out):
                    raise GraphicsError(f"LZ77 block at {off:#x}: back reference before the start")
                start = len(out) - disp
                for k in range((b1 >> 4) + LZ77_MIN):
                    out.append(out[start + k])
            else:
                if i >= n:
                    raise GraphicsError(f"LZ77 block at {off:#x} runs past the end of the data")
                out.append(data[i])
                i += 1
    return bytes(out[:size]), i - off


def lz77_compress(data: bytes) -> bytes:
    """Encode as BIOS LZ77 (type 0x10) in the fewest bytes, with displacements of at least 2."""
    data = bytes(data)
    n = len(data)
    if n >= 1 << 24:
        raise GraphicsError(f"{n} bytes do not fit the 24-bit LZ77 size field")
    longest, disp = [0] * n, [0] * n
    for i in range(LZ77_MIN_DISP, n):
        top = min(LZ77_MAX, n - i)
        if top < LZ77_MIN:
            break
        lo = max(0, i - LZ77_WINDOW)
        s = data.rfind(data[i:i + LZ77_MIN], lo, i - LZ77_MIN_DISP + LZ77_MIN)
        if s < 0:
            continue
        a, b = LZ77_MIN, top        # match of length a at nearest s; none longer
        while a < b:
            m = (a + b + 1) // 2
            t = data.rfind(data[i:i + m], lo, i - LZ77_MIN_DISP + m)
            if t >= 0:
                a, s = m, t
            else:
                b = m - 1
        longest[i], disp[i] = a, i - s
    # bit cost with its flag: literal 9, back reference 17
    cost = [0] * (n + 1)
    step = [1] * n
    for i in range(n - 1, -1, -1):
        best, choice = 9 + cost[i + 1], 1
        for length in range(LZ77_MIN, longest[i] + 1):
            c = 17 + cost[i + length]
            if c < best:
                best, choice = c, length
        cost[i], step[i] = best, choice
    out = bytearray([LZ77_TYPE]) + n.to_bytes(3, "little")
    i = 0
    while i < n:
        flag_at = len(out)
        out.append(0)
        for bit in range(8):
            if i >= n:
                break
            length = step[i]
            if length >= LZ77_MIN:
                d = disp[i] - 1
                out += bytes((((length - LZ77_MIN) << 4) | (d >> 8), d & 0xFF))
                out[flag_at] |= 0x80 >> bit
            else:
                out.append(data[i])
            i += length
    return bytes(out)


def bgr555(v: int) -> Rgb:
    r, g, b = v & 31, (v >> 5) & 31, (v >> 10) & 31
    return ((r << 3) | (r >> 2), (g << 3) | (g >> 2), (b << 3) | (b >> 2))


_PALETTE = re.compile(r"\s*(raw|lz):0x([0-9A-Fa-f]+)(?:\+0x([0-9A-Fa-f]+))?")


def palette_colours(rom: Rom, spec: str, bpp: int) -> tuple[list[Rgb], str]:
    """Colours of a palette named raw:0xOFF or lz:0xOFF[+0xSUB]; a grey ramp without a ROM location."""
    n = 16 if bpp == 4 else 256
    m = _PALETTE.match(spec or "")
    if not m:
        return (GREY if n == 16 else [(i, i, i) for i in range(256)]), "grey (palette not located)"
    kind, off, sub = m.group(1), int(m.group(2), 16), int(m.group(3) or "0", 16)
    if kind == "raw":
        blob = rom.read(off + sub, 2 * n)
    else:
        blob = lz77_decompress(rom.data, off)[0][sub:sub + 2 * n]
    if len(blob) < 2 * n:
        raise GraphicsError(f"palette {spec!r} holds fewer than {n} colours")
    source = f"{kind}:0x{off:07X}" + (f"+0x{sub:X}" if sub else "")
    return [bgr555(int.from_bytes(blob[2 * k:2 * k + 2], "little")) for k in range(n)], source


def tiles_to_pixels(data: bytes, tiles: int, bpp: int, wt: int) -> list[list[int]]:
    """Tiles in ROM order, `wt` per row, as rows of palette indices (index 0 after the last tile)."""
    tb = 8 * bpp
    px = [[0] * (wt * 8) for _ in range(-(-tiles // wt) * 8)]
    for k in range(tiles):
        ox, oy = (k % wt) * 8, (k // wt) * 8
        t = data[k * tb:(k + 1) * tb]
        for r in range(8):
            row = px[oy + r]
            if bpp == 4:
                for c in range(4):
                    v = t[r * 4 + c]
                    row[ox + 2 * c] = v & 15
                    row[ox + 2 * c + 1] = v >> 4
            else:
                row[ox:ox + 8] = t[r * 8:r * 8 + 8]
    return px


def graphics_document(path: str | Path) -> dict:
    """Read canonical inventory rows, embedded notes and preview layouts."""
    import tomllib
    path = Path(path)
    try:
        with path.open("rb") as fh:
            doc = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise GraphicsError(f"{path}: {exc}") from exc
    validate_graphics_document(doc, str(path))
    return {"version": doc.get("version"), "graphic": doc.get("graphic", []), "views": doc.get("views", {})}


def preview_views(path: str | Path) -> dict:
    """Read a view directory, legacy previews JSON or embedded TOML views."""
    path = Path(path)
    if path.is_dir():
        views = {}
        for file in sorted(path.glob("*.json")):
            try:
                views[file.stem] = json.loads(file.read_text(encoding="utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise GraphicsError(f"{file}: {exc}") from exc
        _validate_views(views, str(path))
        return views
    if path.suffix == ".toml":
        return graphics_document(path)["views"]
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("version") != 1:
        raise GraphicsError("unsupported graphics preview version")
    return document["views"]


def _validate_views(views: dict, where: str) -> None:
    if not isinstance(views, dict):
        raise GraphicsError(f"{where}: expected graphic views")
    for gid, records in views.items():
        if not isinstance(gid, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", gid):
            raise GraphicsError(f"{where}: invalid graphic id {gid!r}")
        if not isinstance(records, list) or any(not isinstance(view, dict) for view in records):
            raise GraphicsError(f"{where}: {gid}: expected an array of views")


def write_preview_views(path: str | Path, views: dict) -> list[Path]:
    """Write compact view arrays, preserving canonical source key and cell order."""
    path = Path(path)
    _validate_views(views, str(path))
    texts = {gid: json.dumps(records, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
             for gid, records in sorted(views.items())}
    path.mkdir(parents=True, exist_ok=True)
    written = []
    for gid, text in texts.items():
        file = path / f"{gid}.json"
        file.write_text(text, encoding="utf-8")
        written.append(file)
    return written


def validate_graphics_document(doc: dict, where: str) -> None:
    if doc.get("version") != "2.0.0":
        raise GraphicsError(f"{where}: unsupported graphics version; expected 2.0.0")
    rows, views = doc.get("graphic", []), doc.get("views", {})
    if (set(doc) - {"version", "graphic", "views"} or not isinstance(rows, list) or not isinstance(views, dict)
            or any(not isinstance(row, dict) or not all(isinstance(v, str) for k, v in row.items()
                                                        if k != "notes") for row in rows)):
        raise GraphicsError(f"{where}: expected version, [[graphic]] rows of strings and views")
    identities, note_ids = set(), set()
    for row in rows:
        gid = row.get("id")
        if not gid or not gid.strip() or gid in identities:
            raise GraphicsError(f"{where}: missing or duplicate graphic id {gid!r}")
        identities.add(gid)
        if "notes" not in row and "notes_schema" not in row:
            continue
        label = f"{where}: {gid}"
        try:
            structured.validate({"schema": row.get("notes_schema"), "note": row.get("notes")})
            for note in row["notes"]:
                validate_graphic_note(note, gid, {"kind": "shared"}, label)
                if note["id"] in note_ids:
                    raise structured.NoteError(f"{label}: duplicate note {note['id']}")
                note_ids.add(note["id"])
        except structured.NoteError as exc:
            raise GraphicsError(f"{label}: {exc}") from exc
        if row.get("reason"):
            raise GraphicsError(f"{label}: both legacy reason and structured notes")


def validate_graphic_note(note: dict, gid: str, scope: dict, where: str) -> None:
    structured.validate_note(note, expected_binding={"kind": "graphic", "id": gid}, where=where)
    if note["scope"] != scope:
        raise structured.NoteError(f"{where}: expected scope {scope}")
