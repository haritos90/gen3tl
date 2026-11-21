"""Graphics with text pixels: sheet extraction, the text-area pixel check, graphic and tilemap injection."""

from __future__ import annotations

import csv
import json
import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from ..check.render import PngImage, indexed_png, read_png_image, write_indexed_png
from ..core import notes as structured
from ..core.graphics import (GREY, LZ77_MAX, LZ77_MIN, LZ77_MIN_DISP, LZ77_TYPE, LZ77_WINDOW, GraphicsError, Rgb,
                             _PALETTE, bgr555, graphics_document, lz77_compress, lz77_decompress, palette_colours,
                             preview_views, tiles_to_pixels, validate_graphic_note, validate_graphics_document)
from ..core.rom import ROM_BASE, Rom, ptr_bytes

DEFAULT_SHEET_WIDTH = 16   # tiles per row of an extracted sheet
ALIGN = 4                  # the BIOS decoders read their source word-aligned


@dataclass
class Graphic:
    id: str
    offset: int
    compressed: bool
    slot: int                 # ROM bytes of the graphic: LZ77 block or raw
    size: int                 # decoded size in bytes
    tiles: int
    bpp: int
    palette: str              # palette as the inventory names it
    pointers: list[int]       # aligned words that hold the graphic's address
    pointers_complete: bool   # False when the inventory lists only part of the pointers
    screen: str = ""
    text: str = ""
    category: str = ""

    @property
    def tile_bytes(self) -> int:
        return 8 * self.bpp

    @property
    def compression(self) -> str:
        return "lz77" if self.compressed else "none"


def load_inventory(path: str | Path) -> dict[str, Graphic | None]:
    """Graphics inventory TSV -> id -> Graphic (None: no ROM source traced)."""
    return inventory_of(inventory_rows(path))


def inventory_rows(path: str | Path) -> list[dict[str, str]]:
    """The inventory rows of its columns, in file order: a TSV or a graphics document."""
    if Path(path).suffix == ".toml":
        rows = []
        for record in graphics_document(path)["graphic"]:
            row = {k: v for k, v in record.items() if k not in ("notes_schema", "notes")}
            if "notes" in record:
                row["reason"] = _notes_reason(record["notes"])
            rows.append(row)
        return rows
    with Path(path).open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


GRAPHICS_HEADER = ("# Graphics with text: the inventory rows and their preview layouts.\n"
                   "# Generated from discovery; category and reason are the flags.\n")


def graphic_notes(path: str | Path, *, notes_path: Path | None = None,
                  language: str | None = None) -> dict[str, list[dict]]:
    """Resolve shared notes and an optional selected language collection."""
    rows = graphics_document(path)["graphic"] if Path(path).suffix == ".toml" else inventory_rows(path)
    by_id = {}
    for row in rows:
        gid = row.get("id")
        if not gid or gid in by_id:
            raise GraphicsError(f"{path}: missing or duplicate graphic id {gid!r}")
        by_id[gid] = row
    found = {gid: list(row.get("notes", [])) for gid, row in by_id.items()}
    if notes_path is None:
        return found
    if not language:
        raise GraphicsError("language notes require a selected language")
    notes_path = Path(notes_path)
    if not notes_path.exists():
        return found
    seen = {note["id"] for records in found.values() for note in records}
    try:
        for note in structured.read_notes(notes_path)["note"]:
            gid = note["subject"]["id"]
            where = f"{notes_path}: {note['id']}"
            if gid not in by_id:
                raise structured.NoteError(f"{where}: unknown graphic {gid}")
            validate_graphic_note(note, gid, {"kind": "language", "language": language}, where)
            if note["id"] in seen:
                raise structured.NoteError(f"{where}: duplicate note {note['id']}")
            if by_id[gid].get("reason"):
                raise structured.NoteError(f"{where}: both legacy reason and structured notes")
            if "notes_schema" not in by_id[gid]:
                raise structured.NoteError(f"{where}: language notes require an embedded notes container")
            seen.add(note["id"])
            found[gid].append(note)
    except structured.NoteError as exc:
        raise GraphicsError(str(exc)) from exc
    return found


def _notes_reason(records: list[dict]) -> str:
    def details(value):
        if isinstance(value, dict):
            return "; ".join(f"{key.replace('_', ' ')}: {details(item)}" for key, item in value.items())
        if isinstance(value, list):
            return "\n".join(details(item) for item in value)
        return str(value)

    blocks = []
    for record in records:
        if record["status"] not in ("unreviewed", "accepted"):
            continue
        prose = [item for key in structured.PROSE_FIELDS for item in record.get(key, [])]
        metadata = {key: record[key] for key in ("status", "sources", "evidence", "related_notes",
                                                 "related_subjects", *structured.ATTRIBUTION_FIELDS) if key in record}
        blocks.append("\n".join([*prose, details(metadata)]))
    return "\n".join(blocks)


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_toml_key(k)} = {_toml_value(v)}" for k, v in value.items()) + "}"
    raise GraphicsError(f"unsupported graphics value {value!r}")


def _toml_key(key: str) -> str:
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else json.dumps(key, ensure_ascii=False)


def graphics_text(rows: list[dict], previews: dict | None = None) -> str:
    """Serialize inventory rows and notes, optionally retaining legacy embedded views."""
    previews = previews or {}
    version = "2.0.0" if "graphic" in previews else 1
    if previews.get("version", version) != version:
        raise GraphicsError("unsupported graphics preview version")
    validate_graphics_document({"version": "2.0.0", "graphic": rows, "views": previews.get("views", {})}, "graphics")
    out = [GRAPHICS_HEADER, '\nversion = "2.0.0"\n']
    for row in rows:
        out.append("\n[[graphic]]\n" + "".join(f"{_toml_key(k)} = {_toml_value(v)}\n"
                                              for k, v in row.items() if k != "notes"))
        if "notes" in row:
            if not row["notes"]:
                out.append("notes = []\n")
            for note in row["notes"]:
                text = structured.notes_text({"schema": row["notes_schema"], "note": [note]})
                out.append("\n" + text.split("\n", 1)[1].lstrip().replace("[[note]]", "[[graphic.notes]]", 1))
    for gid, views in previews.get("views", {}).items():
        for view in views:
            out.append(f"\n[[views.{_toml_key(gid)}]]\n")
            for key, value in view.items():
                if isinstance(value, list) and value and all(isinstance(v, list) for v in value):
                    out.append(f"{_toml_key(key)} = [\n" + "".join(f"  {_toml_value(v)},\n" for v in value) + "]\n")
                else:
                    out.append(f"{_toml_key(key)} = {_toml_value(value)}\n")
    return "".join(out)


def inventory_of(rows) -> dict[str, Graphic | None]:
    """Inventory rows (the TSV's columns) -> id -> Graphic (None: no ROM source traced)."""
    out: dict[str, Graphic | None] = {}
    for row in rows:
        gid = row["id"]
        if not row.get("rom_offset"):
            out[gid] = None
            continue
        size = int(row["size_bytes"])
        compressed = row["compressed"].startswith("yes")
        slot = size
        if compressed:
            m = re.search(r"(\d+) bytes", row["compressed"])
            if not m:
                raise GraphicsError(f"{gid}: no LZ77 block length in {row['compressed']!r}")
            slot = int(m.group(1))
        m = re.match(r"\s*(\d+)(?:\s*\((\d+)bpp\))?", row["tiles"])
        if not m:
            raise GraphicsError(f"{gid}: unreadable tile count {row['tiles']!r}")
        ptr_text = row.get("pointer_at", "")
        out[gid] = Graphic(
            id=gid, offset=int(row["rom_offset"], 16), compressed=compressed, slot=slot, size=size,
            tiles=int(m.group(1)), bpp=int(m.group(2) or 4), palette=row.get("palette", ""),
            pointers=[int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]+)", ptr_text)],
            pointers_complete="(+" not in ptr_text,
            screen=row.get("screen", ""), text=row.get("source_text", ""), category=row.get("category", ""))
    return out


@dataclass
class Edit:
    id: str
    png: Path | None
    areas: list[tuple[int, int, int, int]]
    tiles: int | None = None  # tiles of the edited sheet; extras are appended
    evidence: str = ""
    form: str = "sheet"       # "sheet" or "areas" (the area form)
    label: dict | None = None


FORMS = ("sheet", "areas")


TILE_MASK, HFLIP, VFLIP, PAL_SHIFT = 0x3FF, 0x400, 0x800, 12   # text background tilemap entry


@dataclass
class Cell:
    """A tilemap cell to change; a field left None keeps the base ROM's value."""
    row: int
    column: int
    tile: int | None = None
    hflip: bool | None = None
    vflip: bool | None = None
    palette: int | None = None

    def apply(self, entry: int) -> int:
        if self.tile is not None:
            entry = (entry & ~TILE_MASK) | self.tile
        if self.hflip is not None:
            entry = (entry & ~HFLIP) | (HFLIP if self.hflip else 0)
        if self.vflip is not None:
            entry = (entry & ~VFLIP) | (VFLIP if self.vflip else 0)
        if self.palette is not None:
            entry = (entry & ~(0xF << PAL_SHIFT)) | (self.palette << PAL_SHIFT)
        return entry


@dataclass
class TilemapEdit:
    """A [[graphics.tilemap]] entry: changed cells of an LZ77 tilemap of 16-bit entries."""
    name: str
    offset: int
    size: int                 # decoded bytes, unchanged by the edit
    pointers: list[int]
    cells: list[Cell]
    width: int = 32           # entries per row
    graphic: str = ""         # graphic whose tiles the map indexes from 0
    evidence: str = ""

    @property
    def rows(self) -> int:
        return self.size // (2 * self.width)


def _int(e: dict, key: str, what: str, lo: int, hi: int) -> int:
    v = e[key]
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise GraphicsError(f"{what}: {key} {v!r} is not an integer in {lo}..{hi}")
    return v


def parse_tilemap(t: dict) -> TilemapEdit:
    """A [[graphics.tilemap]] table -> TilemapEdit, with its fields and cells checked."""
    need = {"name", "offset", "size", "pointers", "cells"}
    if not need <= set(t):
        raise GraphicsError(f"graphics tilemap {t!r} needs {', '.join(sorted(need))}")
    what = f"tilemap {t['name']}"
    unknown = set(t) - need - {"width", "graphic", "evidence"}
    if unknown:
        raise GraphicsError(f"{what}: unknown key(s) {', '.join(sorted(unknown))}")
    width = _int(t, "width", what, 1, 1024) if "width" in t else 32
    size = _int(t, "size", what, 2, (1 << 24) - 1)
    if size % (2 * width):
        raise GraphicsError(f"{what}: size {size} is not a whole number of {width}-entry rows")
    if not isinstance(t["pointers"], list) or not all(isinstance(p, int) for p in t["pointers"]):
        raise GraphicsError(f"{what}: pointers must be a list of ROM offsets")
    m = TilemapEdit(t["name"], _int(t, "offset", what, 0, (1 << 25) - 1), size, list(t["pointers"]), [],
                    width, str(t.get("graphic", "")), str(t.get("evidence", "")))
    if not isinstance(t["cells"], list) or not t["cells"] or not all(isinstance(c, dict) for c in t["cells"]):
        raise GraphicsError(f"{what}: cells must be a non-empty list of tables")
    seen = set()
    for c in t["cells"]:
        if not {"row", "column"} <= set(c) or not {"tile", "hflip", "vflip", "palette"} & set(c):
            raise GraphicsError(f"{what}: cell {c!r} needs row, column and tile, hflip, vflip or palette")
        if set(c) - {"row", "column", "tile", "hflip", "vflip", "palette"}:
            raise GraphicsError(f"{what}: cell {c!r} has an unknown key")
        row, col = _int(c, "row", what, 0, m.rows - 1), _int(c, "column", what, 0, width - 1)
        if (row, col) in seen:
            raise GraphicsError(f"{what}: cell ({row}, {col}) is listed twice")
        seen.add((row, col))
        for flag in ("hflip", "vflip"):
            if flag in c and not isinstance(c[flag], bool):
                raise GraphicsError(f"{what}: cell ({row}, {col}) {flag} {c[flag]!r} is not true or false")
        m.cells.append(Cell(row, col, _int(c, "tile", what, 0, TILE_MASK) if "tile" in c else None,
                            c.get("hflip"), c.get("vflip"),
                            _int(c, "palette", what, 0, 15) if "palette" in c else None))
    return m


def parse_area(spec) -> tuple[int, int, int, int]:
    """A text area as "x,y,w,h" or [x, y, w, h], in sheet pixels."""
    parts = spec.split(",") if isinstance(spec, str) else list(spec)
    if len(parts) != 4:
        raise GraphicsError(f"text area {spec!r} is not x,y,w,h")
    try:
        x, y, w, h = (int(p) for p in parts)
    except (TypeError, ValueError):
        raise GraphicsError(f"text area {spec!r} is not x,y,w,h") from None
    return x, y, w, h


class GraphicsConfig:
    """The profile's [graphics] section: inventory, sheet layout, palette overrides, edits, tilemaps."""

    def __init__(self, section: dict, root: Path, inventory: dict | None = None, release: Path | None = None):
        self.root = Path(root)
        self.section = section
        self.widths = dict(section.get("sheet_width", {}))
        self.palettes = dict(section.get("palette", {}))
        self.edits = []
        for e in section.get("edit", []):
            if not {"id", "areas"} <= set(e) or ("png" in e) == ("label" in e):
                raise GraphicsError(f"graphics edit {e!r} needs id, areas and one image source")
            tiles = _int(e, "tiles", f"graphics edit {e['id']}", 1, 1 << 16) if "tiles" in e else None
            form = e.get("form", "sheet")
            if form not in FORMS:
                raise GraphicsError(f"graphics edit {e['id']}: form {form!r} is not one of {', '.join(FORMS)}")
            label = None
            if "label" in e:
                from .graphiclabel import load as load_label
                from .release import load as load_version, source
                if form != "areas" or tiles is None:
                    raise GraphicsError("graphic labels require appended area-form tiles")
                facts = section.get("label_facts", {}).get(e["id"], {})
                label = load_label(self.root / e["label"], load_version(release or source(self.root)), facts)
            self.edits.append(Edit(e["id"], self.root / e["png"] if "png" in e else None,
                                   [parse_area(a) for a in e["areas"]], tiles,
                                   e.get("evidence", ""), form, label))
        self.tilemaps = [parse_tilemap(t) for t in section.get("tilemap", [])]
        self._inventory: dict[str, Graphic | None] | None = inventory

    @classmethod
    def from_profile(cls, prof) -> "GraphicsConfig":
        return cls(prof.get("graphics", {}), getattr(prof, "project_root", None) or Path.cwd(),
                   release=getattr(prof, "release_path", None))

    @property
    def inventory(self) -> dict[str, Graphic | None]:
        if self._inventory is None:
            if "inventory" not in self.section:
                raise GraphicsError("the profile has no [graphics] inventory")
            self._inventory = load_inventory(self.root / self.section["inventory"])
        return self._inventory

    def graphic(self, gid: str) -> Graphic:
        if gid not in self.inventory:
            raise GraphicsError(f"{gid} is not in the graphics inventory")
        g = self.inventory[gid]
        if g is None:
            raise GraphicsError(f"{gid} has no ROM source in the graphics inventory")
        return g

    def sheet_width(self, g: Graphic) -> int:
        return max(1, min(int(self.widths.get(g.id, DEFAULT_SHEET_WIDTH)), g.tiles))

    def shared_edits(self, edited) -> dict[str, str]:
        """Translate rows another edit draws, keyed by row id."""
        holders = {g.offset: gid for gid in edited if (g := self.inventory.get(gid)) is not None}
        out = {}
        for gid, g in self.inventory.items():
            holder = holders.get(g.offset) if g is not None else None
            if (holder and gid not in edited and g.category == "translate"
                    and self.sheet_width(g) == self.sheet_width(self.graphic(holder))):
                out[gid] = holder
        return out

    def palette_spec(self, g: Graphic) -> str:
        p = self.palettes.get(g.id)
        if p is None:
            return g.palette
        return p["at"] if isinstance(p, dict) else str(p)

    def sheet(self, rom: Rom, g: Graphic) -> "Sheet":
        data = decode_graphic(rom, g)
        wt = self.sheet_width(g)
        colours, source = palette_colours(rom, self.palette_spec(g), g.bpp)
        return Sheet(g, wt, tiles_to_pixels(data, g.tiles, g.bpp, wt), colours, source, data)

    def tile_count(self, gid: str) -> int:
        """Tiles of a graphic as built: the count its edit declares, else the inventory's."""
        declared = [e.tiles for e in self.edits if e.id == gid and e.tiles is not None]
        return declared[0] if declared else self.graphic(gid).tiles

    def ranges(self) -> list[tuple[int, int]]:
        """ROM ranges of every inventory graphic (end exclusive)."""
        return sorted({(g.offset, g.offset + g.slot) for g in self.inventory.values() if g is not None})



def decode_graphic(rom: Rom, g: Graphic) -> bytes:
    """The decoded tile data of a graphic, checked against the inventory's size."""
    if g.compressed:
        data, used = lz77_decompress(rom.data, g.offset)
        if len(data) != g.size or used != g.slot:
            raise GraphicsError(f"{g.id}: the LZ77 block at {g.offset:#x} decodes to {len(data)} bytes from "
                                f"{used}; the inventory lists {g.size} from {g.slot}")
    else:
        data = rom.read(g.offset, g.size)
    if g.tiles * g.tile_bytes > len(data):
        raise GraphicsError(f"{g.id}: {g.tiles} tiles need {g.tiles * g.tile_bytes} bytes, the graphic has {len(data)}")
    return data


def pixels_to_tiles(px: list[list[int]], tiles: int, bpp: int, wt: int) -> bytes:
    """Inverse of tiles_to_pixels."""
    out = bytearray()
    for k in range(tiles):
        ox, oy = (k % wt) * 8, (k // wt) * 8
        for r in range(8):
            row = px[oy + r]
            if bpp == 4:
                out += bytes(row[ox + 2 * c] | (row[ox + 2 * c + 1] << 4) for c in range(4))
            else:
                out += bytes(row[ox:ox + 8])
    return bytes(out)


@dataclass
class Sheet:
    graphic: Graphic
    width_tiles: int
    pixels: list[list[int]]
    palette: list[Rgb]
    palette_source: str
    data: bytes               # decoded graphic, including any bytes after the last whole tile

    @property
    def width(self) -> int:
        return len(self.pixels[0])

    @property
    def height(self) -> int:
        return len(self.pixels)

    def encode(self, px: list[list[int]], tiles: int | None = None) -> bytes:
        """Decoded graphic data for an edited sheet of `tiles` tiles; bytes after the last whole tile are kept."""
        g = self.graphic
        n = g.tiles if tiles is None else tiles
        if n != g.tiles:
            problem = append_problem(g, n, len(self.data))
            if problem:
                raise GraphicsError(f"{g.id}: {problem}")
        return pixels_to_tiles(px, n, g.bpp, self.width_tiles) + self.data[g.tiles * g.tile_bytes:]


def append_problem(g: Graphic, tiles: int, decoded: int | None = None) -> str:
    """Why a graphic cannot be built with `tiles` tiles, or "" when it can."""
    if tiles == g.tiles:
        return ""
    if tiles < g.tiles:
        return f"{tiles} tiles is below the original {g.tiles}; tiles can only be appended"
    if not g.compressed:
        return "tiles can only be appended to an LZ77 graphic; the size of a raw graphic is fixed by its loader"
    extra = (g.size if decoded is None else decoded) - g.tiles * g.tile_bytes
    if extra:
        return f"the graphic holds {extra} bytes after its last tile; appended tiles would not follow it"
    return ""


def hex_rgb(c) -> str:
    return "#%02x%02x%02x" % tuple(c[:3])


def png_depth(palette: list) -> int:
    """Bit depth of a sheet's PNG, as write_sheet writes it."""
    return 4 if len(palette) <= 16 else 8


def write_sheet(sheet: Sheet, out_dir: str | Path, base_sha1: str = "") -> tuple[Path, Path]:
    """Write <id>.png (indexed, with the palette) and its <id>.json sidecar."""
    g = sheet.graphic
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    png, side = out_dir / f"{g.id}.png", out_dir / f"{g.id}.json"
    write_indexed_png(png, sheet.pixels, sheet.palette, png_depth(sheet.palette))
    meta = {
        "id": g.id, "screen": g.screen, "source_text": g.text, "category": g.category,
        "offset": f"0x{g.offset:07X}", "compression": g.compression, "slot_bytes": g.slot,
        "size_bytes": g.size, "bpp": g.bpp, "tiles": g.tiles,
        "sheet": {"width_tiles": sheet.width_tiles, "width": sheet.width, "height": sheet.height,
                  "padding_tiles": -(-g.tiles // sheet.width_tiles) * sheet.width_tiles - g.tiles},
        "palette": {"source": sheet.palette_source, "colours": [hex_rgb(c) for c in sheet.palette]},
        "pointers": [f"0x{p:07X}" for p in g.pointers], "png": png.name, "base_sha1": base_sha1,
    }
    side.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return png, side



@dataclass
class CheckResult:
    findings: list[str]                 # each one fails the check
    changed_inside: int = 0             # changed pixels inside the text areas
    indices: list[list[int]] | None = None   # the edited sheet as palette indices
    changed_appended: int = 0           # pixels of the appended tiles that are not index 0


def pixel_check(sheet: Sheet, img: PngImage, areas: list[tuple[int, int, int, int]],
                tiles: int | None = None) -> CheckResult:
    """Compare an edited sheet with the original: report pixels changed outside the text areas and other changes."""
    findings: list[str] = []
    g, wt = sheet.graphic, sheet.width_tiles
    n_tiles = g.tiles if tiles is None else tiles
    if n_tiles < g.tiles:
        return CheckResult([f"{n_tiles} tiles is below the original {g.tiles}; tiles can only be appended"])
    W, H = sheet.width, max(sheet.height, -(-n_tiles // wt) * 8)
    for x, y, w, h in areas:
        if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > W or y + h > H:
            findings.append(f"text area {x},{y},{w},{h} is empty or leaves the {W}x{H} sheet")
    if (img.width, img.height) != (W, H):
        want = "the original" if n_tiles == g.tiles else f"the {n_tiles}-tile sheet's"
        findings.append(f"dimensions {img.width}x{img.height} differ from {want} {W}x{H}")
        return CheckResult(findings)
    pal, n = sheet.palette, len(sheet.palette)
    orig = sheet.pixels + [[0] * W for _ in range(H - sheet.height)]
    rows: list[list[int]] = []
    if img.indices is not None:
        if len(img.palette) < n:
            findings.append(f"palette has {len(img.palette)} entries, the original {n}")
        for k in range(min(n, len(img.palette))):
            if tuple(img.palette[k]) != tuple(pal[k]):
                findings.append(f"palette entry {k} is {hex_rgb(img.palette[k])}, the original {hex_rgb(pal[k])}")
        for y in range(H):
            row = []
            for x in range(W):
                v = img.indices[y][x]
                if v >= n:
                    findings.append(f"pixel {x},{y}: index {v} is outside the {n}-colour palette")
                    v = orig[y][x]
                row.append(v)
            rows.append(row)
    else:
        lookup: dict[Rgb, list[int]] = {}
        for k, c in enumerate(pal):
            lookup.setdefault(tuple(c), []).append(k)
        for y in range(H):
            row = []
            for x in range(W):
                c, o = tuple(img.rgb[y][x]), orig[y][x]
                ks = lookup.get(c)
                if not ks:
                    findings.append(f"pixel {x},{y}: colour {hex_rgb(c)} is not in the palette")
                    v = o
                elif o in ks:
                    v = o
                elif len(ks) > 1:
                    findings.append(f"pixel {x},{y}: colour {hex_rgb(c)} is palette entries "
                                    f"{', '.join(map(str, ks))}; save the sheet as an indexed PNG")
                    v = o
                else:
                    v = ks[0]
                row.append(v)
            rows.append(row)
    inside = appended = 0
    for y in range(H):
        for x in range(W):
            o, v = orig[y][x], rows[y][x]
            if o == v:
                continue
            k = (y // 8) * wt + x // 8
            if k >= n_tiles:
                findings.append(f"pixel {x},{y}: index {o} -> {v} past the last tile (the graphic has {n_tiles} tiles)")
            elif k >= g.tiles:
                appended += 1
            elif any(ax <= x < ax + aw and ay <= y < ay + ah for ax, ay, aw, ah in areas):
                inside += 1
            else:
                findings.append(f"pixel {x},{y}: index {o} -> {v} outside the text areas")
    return CheckResult(findings, inside, rows, appended)


@dataclass
class CheckReport:
    """`gen3tl build gfx-check` of an edited sheet (check_report): exit status and output."""
    code: int                           # 0 passed, 1 findings, 2 not checked
    lines: list[str]                    # Preserve the command report fields.
    result: CheckResult | None = None
    sheet: Sheet | None = None
    areas: list[tuple[int, int, int, int]] = field(default_factory=list)
    tiles: int = 0


def check_report(cfg: GraphicsConfig, rom: Rom, gid: str, load, areas, tiles: int | None = None) -> CheckReport:
    """build gfx-check of a sheet against graphic `gid` of `rom`: pixel check, tile count, encoded size."""
    try:
        g = cfg.graphic(gid)
        sheet = cfg.sheet(rom, g)
        areas = [parse_area(a) for a in areas]
        img = load()
    except (GraphicsError, OSError, ValueError, zlib.error) as e:
        return CheckReport(2, [f"FAIL     {e}"])
    n = g.tiles if tiles is None else tiles
    problem = append_problem(g, n, len(sheet.data))
    if problem:
        return CheckReport(2, [f"FAIL     {gid}: {problem}"], None, sheet, areas, n)
    chk = pixel_check(sheet, img, areas, n)
    lines = [f"FAIL     {f}" for f in chk.findings]
    lines.append(f"{gid}: {chk.changed_inside} changed pixels inside {len(areas)} text area(s)"
                 + (f", {chk.changed_appended} set in {n - g.tiles} appended tile(s)" if n > g.tiles else "")
                 + f", {len(chk.findings)} finding(s)")
    if not chk.findings and (chk.changed_inside or n > g.tiles):
        new = sheet.encode(chk.indices, n)
        size = len(lz77_compress(new)) if g.compressed else len(new)
        lines.append(f"encoded  {size} bytes, slot {g.slot}: " + ("written in place" if size <= g.slot else "relocated"))
    return CheckReport(1 if chk.findings else 0, lines, chk, sheet, areas, n)



def declared(x: int, y: int, areas, wt: int, base_tiles: int, tiles: int) -> bool:
    """Whether pixel x, y of a sheet `wt` tiles wide lies in the declared area."""
    k = (y // 8) * wt + x // 8
    if k >= tiles:
        return False
    return k >= base_tiles or any(ax <= x < ax + aw and ay <= y < ay + ah for ax, ay, aw, ah in areas)


def area_form(px: list[list[int]], areas, wt: int, base_tiles: int, tiles: int | None = None) -> list[list[int]]:
    """The area form of an edited sheet's palette indices: index 0 outside the declared area."""
    n = base_tiles if tiles is None else tiles
    return [[v if declared(x, y, areas, wt, base_tiles, n) else 0 for x, v in enumerate(row)]
            for y, row in enumerate(px)]


def area_palette(bpp: int) -> list[Rgb]:
    """The area form's palette: grey, no ROM colour (GREY for 4bpp)."""
    return GREY if bpp == 4 else [(i, i, i) for i in range(256)]


def area_png(px: list[list[int]], bpp: int) -> bytes:
    """Area form indices of a `bpp` graphic as an indexed PNG with area_palette."""
    top = max((v for row in px for v in row), default=0)
    if top >= 1 << bpp:
        raise GraphicsError(f"index {top} does not fit a {bpp}bpp graphic")
    return indexed_png(px, area_palette(bpp), bpp)


def apply_area_form(sheet: Sheet, img: PngImage, areas, tiles: int | None = None) -> tuple[PngImage | None, list[str]]:
    """The edited sheet an area form gives over the original `sheet`, and its findings."""
    g, wt = sheet.graphic, sheet.width_tiles
    n = g.tiles if tiles is None else tiles
    W, H = sheet.width, max(sheet.height, -(-n // wt) * 8)
    if img.indices is None:
        return None, ["the area form is not an indexed PNG"]
    if (img.width, img.height) != (W, H):
        want = "the original" if n == g.tiles else f"the {n}-tile sheet's"
        return None, [f"dimensions {img.width}x{img.height} differ from {want} {W}x{H}"]
    orig = sheet.pixels + [[0] * W for _ in range(H - sheet.height)]
    findings: list[str] = []
    rows = []
    for y in range(H):
        row = []
        for x in range(W):
            v = img.indices[y][x]
            if declared(x, y, areas, wt, g.tiles, n):
                row.append(v)
                continue
            if v:
                findings.append(f"pixel {x},{y}: index {v} outside the text areas of the area form")
            row.append(orig[y][x])
        rows.append(row)
    pal = list(sheet.palette)
    rgb = [[tuple(pal[v]) if v < len(pal) else (0, 0, 0) for v in row] for row in rows]
    return PngImage(W, H, rgb, pal, rows), findings


def area_form_png(sheet: Sheet, px: list[list[int]], areas, tiles: int | None = None) -> bytes:
    """The area form of edited indices `px` of `sheet` as an indexed PNG (area_png)."""
    g = sheet.graphic
    return area_png(area_form(px, areas, sheet.width_tiles, g.tiles, tiles), g.bpp)


def area_size(cfg: GraphicsConfig, g: Graphic, tiles: int | None = None) -> tuple[int, int]:
    """Width and height of the area form of graphic `g` with `tiles` tiles (default its own)."""
    wt = cfg.sheet_width(g)
    return wt * 8, -(-max(g.tiles, g.tiles if tiles is None else tiles) // wt) * 8


def area_check(cfg: GraphicsConfig, gid: str, img: PngImage, areas, tiles: int | None = None) -> list[str]:
    """The findings of an area form against the inventory, without the ROM."""
    g = cfg.graphic(gid)
    n = g.tiles if tiles is None else tiles
    problem = append_problem(g, n)
    if problem:
        return [problem]
    wt = cfg.sheet_width(g)
    blank = Sheet(g, wt, tiles_to_pixels(bytes(g.size), g.tiles, g.bpp, wt), area_palette(g.bpp), "area form",
                  bytes(g.size))
    laid, findings = apply_area_form(blank, img, [parse_area(a) for a in areas], n)
    if laid is None:
        return findings
    return findings + pixel_check(blank, laid, [parse_area(a) for a in areas], n).findings



@dataclass
class GraphicPlacement:
    id: str
    old: int
    new: int
    length: int               # bytes written (encoded form)
    slot: int
    mode: str                 # same | in-place | relocated
    compressed: bool
    data: bytes               # decoded graphic as written
    pointers: list[int] = field(default_factory=list)
    changed_pixels: int = 0
    base_tiles: int = 0       # tile count in the base ROM
    tiles: int = 0            # tile count as written (more: tiles appended)


@dataclass
class TilemapPlacement:
    name: str
    old: int
    new: int
    length: int               # bytes written (LZ77 block)
    slot: int                 # length of the base ROM's LZ77 block
    mode: str                 # same | in-place | relocated
    data: bytes               # decoded tilemap as written
    pointers: list[int] = field(default_factory=list)
    changes: list[tuple[int, int, int, int]] = field(default_factory=list)   # row, column, before, after


@dataclass
class GraphicsResult:
    placements: list[GraphicPlacement] = field(default_factory=list)
    allowed: set[int] = field(default_factory=set)       # graphics class of the diff guard
    failures: list[str] = field(default_factory=list)
    findings: dict[str, list[str]] = field(default_factory=dict)   # pixel check findings per graphic
    declared: int = 0
    tilemaps: list[TilemapPlacement] = field(default_factory=list)
    declared_tilemaps: int = 0


def aligned_refs(data: bytes | bytearray, offset: int) -> list[int]:
    """Offsets of the aligned 32-bit words that hold the ROM address of `offset`."""
    key, out = ptr_bytes(offset), []
    q = data.find(key)
    while q >= 0:
        if q % 4 == 0:
            out.append(q)
        q = data.find(key, q + 1)
    return out


def inject_graphics(rom: Rom, base: Rom, cfg: GraphicsConfig, space, taken: set[int] = frozenset()) -> GraphicsResult:
    """Write the profile's edited graphics into `rom`."""
    res = GraphicsResult(declared=len(cfg.edits))
    by_offset: dict[int, str] = {}
    for e in cfg.edits:
        try:
            g = cfg.graphic(e.id)
            sheet = cfg.sheet(base, g)
            if e.label is not None:
                from .graphiclabel import image
                img = image(sheet, e.label, e.tiles)
            else:
                img = read_png_image(e.png)
        except (GraphicsError, OSError, ValueError, zlib.error) as x:
            res.failures.append(f"graphic {e.id}: {x}")
            continue
        if g.offset in by_offset:
            res.failures.append(f"graphic {e.id}: same ROM graphic as {by_offset[g.offset]}; "
                                "declare all its text areas in one entry")
            continue
        by_offset[g.offset] = e.id
        tiles = g.tiles if e.tiles is None else e.tiles
        problem = append_problem(g, tiles, len(sheet.data))
        if not problem and tiles > g.tiles and not structured.has_prose(e.evidence):
            problem = "appended tiles need evidence that the game loads them over unused memory"
        if problem:
            res.failures.append(f"graphic {e.id}: {problem}")
            continue
        if e.form == "areas":   # laid over the base sheet
            img, problems = apply_area_form(sheet, img, e.areas, tiles)
            if problems:
                res.findings[e.id] = problems
                res.failures.append(f"graphic {e.id}: area form: {len(problems)} finding(s), first: {problems[0]}")
                continue
        chk = pixel_check(sheet, img, e.areas, tiles)
        if chk.findings:
            res.findings[e.id] = chk.findings
            res.failures.append(f"graphic {e.id}: pixel check: {len(chk.findings)} finding(s), first: {chk.findings[0]}")
            continue
        new = sheet.encode(chk.indices, tiles)
        if new == sheet.data:
            res.placements.append(GraphicPlacement(e.id, g.offset, g.offset, 0, g.slot, "same", g.compressed, new,
                                                   base_tiles=g.tiles, tiles=tiles))
            continue
        blob = lz77_compress(new) if g.compressed else new
        if g.compressed and lz77_decompress(blob)[0] != new:
            res.failures.append(f"graphic {e.id}: the LZ77 encoding does not decode back")
            continue
        ptrs: list[int] = []
        if len(blob) <= g.slot:
            target, mode = g.offset, "in-place"
        else:
            problem = relocation_problem(base, g)
            if problem:
                res.failures.append(f"graphic {e.id}: encoded {len(blob)} bytes exceed the {g.slot}-byte slot "
                                    f"and it cannot be relocated: {problem}")
                continue
            at = space.alloc(len(blob) + ALIGN - 1)
            if at is None:
                res.failures.append(f"graphic {e.id}: no free space for {len(blob)} bytes")
                continue
            target, mode, ptrs = -(-at // ALIGN) * ALIGN, "relocated", list(g.pointers)
        span = set(range(target, target + len(blob))) | {p + k for p in ptrs for k in range(4)}
        clash = (span & taken) | (span & res.allowed)
        if clash:
            res.failures.append(f"graphic {e.id}: its bytes overlap bytes already changed, first at {min(clash):#x}")
            continue
        rom.write(target, blob)
        for p in ptrs:
            rom.write(p, ptr_bytes(target))
        res.allowed |= span
        res.placements.append(GraphicPlacement(e.id, g.offset, target, len(blob), g.slot, mode, g.compressed, new,
                                               ptrs, chk.changed_inside + chk.changed_appended, g.tiles, tiles))
    inject_tilemaps(rom, base, cfg, space, taken, res)
    return res


def inject_tilemaps(rom: Rom, base: Rom, cfg: GraphicsConfig, space, taken: set[int], res: GraphicsResult) -> None:
    """Write the profile's tilemap edits into `rom`, in place or in free space with rewritten pointers."""
    res.declared_tilemaps = len(cfg.tilemaps)
    by_offset: dict[int, str] = {}
    for m in cfg.tilemaps:
        what = f"tilemap {m.name}"
        if m.offset in by_offset:
            res.failures.append(f"{what}: same ROM tilemap as {by_offset[m.offset]}; list all its cells in one entry")
            continue
        by_offset[m.offset] = m.name
        try:
            old, slot = lz77_decompress(base.data, m.offset)
        except GraphicsError as x:
            res.failures.append(f"{what}: {x}")
            continue
        if len(old) != m.size:
            res.failures.append(f"{what}: the LZ77 block at {m.offset:#x} decodes to {len(old)} bytes; "
                                f"the profile declares {m.size}")
            continue
        if m.graphic:
            try:
                count = cfg.tile_count(m.graphic)
            except GraphicsError as x:
                res.failures.append(f"{what}: {x}")
                continue
            over = [c for c in m.cells if c.tile is not None and c.tile >= count]
            if over:
                res.failures.append(f"{what}: cell ({over[0].row}, {over[0].column}) places tile {over[0].tile}; "
                                    f"{m.graphic} has {count} tiles")
                continue
        new = bytearray(old)
        changes = []
        for c in m.cells:
            i = 2 * (c.row * m.width + c.column)
            was = int.from_bytes(old[i:i + 2], "little")
            now = c.apply(was)
            new[i:i + 2] = now.to_bytes(2, "little")
            if now != was:
                changes.append((c.row, c.column, was, now))
        new = bytes(new)
        if new == old:
            res.tilemaps.append(TilemapPlacement(m.name, m.offset, m.offset, 0, slot, "same", new))
            continue
        blob = lz77_compress(new)
        back = lz77_decompress(blob)[0]
        if len(back) != m.size or back != new:
            res.failures.append(f"{what}: the LZ77 encoding does not decode back to the {m.size}-byte tilemap")
            continue
        ptrs: list[int] = []
        if len(blob) <= slot:
            target, mode = m.offset, "in-place"
        else:
            problem = pointer_problem(base, m.offset, m.pointers, "the profile's")
            if problem:
                res.failures.append(f"{what}: encoded {len(blob)} bytes exceed the {slot}-byte block "
                                    f"and it cannot be relocated: {problem}")
                continue
            at = space.alloc(len(blob) + ALIGN - 1)
            if at is None:
                res.failures.append(f"{what}: no free space for {len(blob)} bytes")
                continue
            target, mode, ptrs = -(-at // ALIGN) * ALIGN, "relocated", list(m.pointers)
        span = set(range(target, target + len(blob))) | {p + k for p in ptrs for k in range(4)}
        clash = (span & taken) | (span & res.allowed)
        if clash:
            res.failures.append(f"{what}: its bytes overlap bytes already changed, first at {min(clash):#x}")
            continue
        rom.write(target, blob)
        for p in ptrs:
            rom.write(p, ptr_bytes(target))
        res.allowed |= span
        res.tilemaps.append(TilemapPlacement(m.name, m.offset, target, len(blob), slot, mode, new, ptrs, changes))


def relocation_problem(base: Rom, g: Graphic) -> str:
    """Why the pointers of a graphic cannot be rewritten, or "" when every one is verified."""
    if not g.compressed:
        return "a raw graphic keeps its size"
    if not g.pointers:
        return "the inventory lists no pointer"
    if not g.pointers_complete:
        return "the inventory lists only part of its pointers"
    return pointer_problem(base, g.offset, g.pointers, "the inventory's")


def pointer_problem(base: Rom, offset: int, pointers: list[int], source: str) -> str:
    """Why the pointers to `offset` cannot be rewritten, or "" when every one is verified."""
    if not pointers:
        return f"{source} list holds no pointer"
    bad = [p for p in pointers if base.u32(p) != ROM_BASE + offset]
    if bad:
        return f"the word at {bad[0]:#x} does not hold {ROM_BASE + offset:#x}"
    refs = aligned_refs(base.data, offset)
    if sorted(refs) != sorted(pointers):
        return (f"aligned references {', '.join(f'{r:#x}' for r in refs)} differ from {source} "
                f"{', '.join(f'{p:#x}' for p in pointers)}")
    return ""


def verify_graphic_sources(rom: Rom, base: Rom, cfg: GraphicsConfig,
                           allowed: set[int] | frozenset[int] = frozenset()) -> list[str]:
    """Preserve source graphics outside declared edit bytes."""
    failures = []
    for gid, graphic in cfg.inventory.items():
        if graphic is None:
            continue
        start, end = graphic.offset, graphic.offset + graphic.slot
        if not 0 <= start < end <= len(base.data):
            failures.append(f"graphic {gid}: source range outside base ROM")
            continue
        if rom.data[start:end] == base.data[start:end]:
            continue
        changed = next((at for at in range(start, end)
                        if rom.data[at] != base.data[at] and at not in allowed), None)
        if changed is not None:
            failures.append(f"graphic {gid}: source overwritten outside declared edits at {changed:#x}")
    return failures


def verify_graphics(rom: Rom, res: GraphicsResult) -> list[str]:
    """Decode every written graphic back and check its rewritten pointers."""
    fails = []
    for p in res.placements:
        if p.mode == "same":
            continue
        try:
            got = lz77_decompress(rom.data, p.new)[0] if p.compressed else rom.read(p.new, len(p.data))
        except GraphicsError as x:
            got = None
            fails.append(f"graphic {p.id}: {x}")
        if got is not None and got != p.data:
            fails.append(f"graphic {p.id}: the data at {p.new:#x} does not decode to the edited graphic")
        for at in p.pointers:
            if rom.u32(at) != ROM_BASE + p.new:
                fails.append(f"graphic {p.id}: pointer at {at:#x} does not point to {p.new:#x}")
    for t in res.tilemaps:   # base decoded size: the game's buffer holds it
        if t.mode == "same":
            continue
        try:
            got = lz77_decompress(rom.data, t.new)[0]
        except GraphicsError as x:
            got = None
            fails.append(f"tilemap {t.name}: {x}")
        if got is not None and len(got) != len(t.data):
            fails.append(f"tilemap {t.name}: the block at {t.new:#x} decodes to {len(got)} bytes, "
                         f"the base ROM's to {len(t.data)}")
        elif got is not None and got != t.data:
            fails.append(f"tilemap {t.name}: the block at {t.new:#x} does not decode to the edited tilemap")
        for at in t.pointers:
            if rom.u32(at) != ROM_BASE + t.new:
                fails.append(f"tilemap {t.name}: pointer at {at:#x} does not point to {t.new:#x}")
    return fails


def report_lines(res: GraphicsResult, limit: int = 200) -> list[str]:
    """Build report section: one row per declared graphic and tilemap, then the pixel check findings."""
    if not (res.placements or res.tilemaps or res.failures):
        return []
    lines = ["", "## Graphics"]
    if res.placements:
        lines += ["", "| Graphic | Mode | Offset | Written at | Encoded / slot bytes | Tiles | Pointers | "
                  "Changed pixels |", "|---|---|---|---|---|---|---|---|"]
    for p in res.placements:
        tiles = str(p.base_tiles) if p.tiles == p.base_tiles else f"{p.base_tiles} + {p.tiles - p.base_tiles} appended"
        lines.append(f"| {p.id} | {p.mode} | 0x{p.old:07X} | 0x{p.new:07X} | {p.length or '-'} / {p.slot} | "
                     f"{tiles} | {', '.join(f'0x{a:07X}' for a in p.pointers) or '-'} | {p.changed_pixels} |")
    if res.tilemaps:
        lines += ["", "| Tilemap | Mode | Offset | Written at | Encoded / slot bytes | Decoded bytes | Pointers | "
                  "Changed cells (row, column: entry) |", "|---|---|---|---|---|---|---|---|"]
    for t in res.tilemaps:
        cells = "; ".join(f"({r}, {c}): 0x{a:04X} -> 0x{b:04X}" for r, c, a, b in t.changes[:limit]) or "-"
        if len(t.changes) > limit:
            cells += f"; ... {len(t.changes) - limit} more"
        lines.append(f"| {t.name} | {t.mode} | 0x{t.old:07X} | 0x{t.new:07X} | {t.length or '-'} / {t.slot} | "
                     f"{len(t.data)} | {', '.join(f'0x{a:07X}' for a in t.pointers) or '-'} | {cells} |")
    for gid, fs in res.findings.items():
        lines += ["", f"Pixel check {gid}: {len(fs)} finding(s)", ""] + [f"- {f}" for f in fs[:limit]]
        if len(fs) > limit:
            lines.append(f"- ... {len(fs) - limit} more (gen3tl build gfx-check {gid} lists all)")
    return lines
