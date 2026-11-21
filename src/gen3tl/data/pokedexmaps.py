"""Pokedex area maps: one PNG per runtime map with encounters or pickups."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..core.notes import has_prose
from ..build.graphics import GraphicsError, bgr555, lz77_decompress, tiles_to_pixels
from ..check.render import indexed_png

ROM_BASE = 0x08000000
BLOCK = 16          # px per block
TILE_BYTES = 32     # one 8x8 4bpp tile
KEYS = ("primary_tiles", "primary_metatiles", "primary_palettes", "palettes")


def map_file(runtime: str) -> str:
    """The file of a runtime map, relative to the sprite directory."""
    return f"maps/{runtime}.png"


def _value(data: bytes, at: int, width: int) -> int | None:
    if at < 0 or at + width > len(data):
        return None
    return int.from_bytes(data[at:at + width], "little")


def _pointer(data: bytes, at: int) -> int | None:
    value = _value(data, at, 4)
    if value is None or not ROM_BASE <= value < ROM_BASE + len(data):
        return None
    return value - ROM_BASE


def check(block: dict | None) -> list[str]:
    """Every problem that keeps a [pokedex] block from producing area maps."""
    if not block:
        return ["[pokedex] block"]
    maps = block.get("maps")
    if not maps:
        return ["[pokedex.maps] block"]
    problems = [f"[pokedex.maps] {key}" for key in KEYS if not isinstance(maps.get(key), int) or maps[key] <= 0]
    if not has_prose(maps.get("evidence", "")):
        problems.append("[pokedex.maps] evidence")
    if "encounters" not in block:
        problems.append("[pokedex.encounters] block")
    return problems


def layout(data: bytes, header: int) -> dict:
    """The layout a map header points to: size in blocks and the three pointers."""
    at = _pointer(data, header)
    if at is None:
        raise ValueError(f"map header 0x{header:06x}: invalid layout pointer")
    width, height = _value(data, at, 4), _value(data, at + 4, 4)
    blocks, primary, secondary = _pointer(data, at + 12), _pointer(data, at + 16), _pointer(data, at + 20)
    if not (width and height and 1 <= width <= 512 and 1 <= height <= 512):
        raise ValueError(f"map header 0x{header:06x}: invalid layout size")
    if blocks is None or primary is None or secondary is None:
        raise ValueError(f"map header 0x{header:06x}: invalid layout pointers")
    if blocks + 2 * width * height > len(data):
        raise ValueError(f"map header 0x{header:06x}: blocks exceed ROM")
    return {"at": at, "width": width, "height": height, "blocks": blocks,
            "primary": primary, "secondary": secondary}


def tileset(data: bytes, at: int) -> dict:
    """A tileset's tile rows (one 8 px wide tile per 8 rows), palettes and metatile base."""
    compressed = _value(data, at, 1)
    tiles_at, palettes_at, metatiles_at = _pointer(data, at + 4), _pointer(data, at + 8), _pointer(data, at + 12)
    if compressed is None or tiles_at is None or palettes_at is None or metatiles_at is None:
        raise ValueError(f"tileset 0x{at:06x}: invalid pointers")
    if compressed:
        try:
            blob, _ = lz77_decompress(data, tiles_at)
        except GraphicsError as exc:
            raise ValueError(f"tileset 0x{at:06x}: {exc}") from exc
    else:
        blob = data[tiles_at:tiles_at + 1024 * TILE_BYTES]
    count = len(blob) // TILE_BYTES
    rows = tiles_to_pixels(bytes(blob), count, 4, 1)
    palettes = [[bgr555(_value(data, palettes_at + 32 * p + 2 * c, 2) or 0) for c in range(16)] for p in range(16)]
    return {"rows": rows, "count": count, "palettes": palettes, "metatiles": metatiles_at}


def render(data: bytes, header: int, maps: dict) -> tuple[list[list[int]], list[tuple[int, int, int]], dict]:
    """The pixel indices and palette of a map, plus its layout."""
    lay = layout(data, header)
    primary, secondary = tileset(data, lay["primary"]), tileset(data, lay["secondary"])
    primary_tiles, primary_metatiles = int(maps["primary_tiles"]), int(maps["primary_metatiles"])
    primary_palettes, total = int(maps["primary_palettes"]), int(maps["palettes"])
    palettes = primary["palettes"][:primary_palettes] + secondary["palettes"][primary_palettes:total]
    palette = [colour for entries in palettes for colour in entries]
    palette += [(0, 0, 0)] * (256 - len(palette))
    width, height = lay["width"], lay["height"]
    image = [[0] * (width * BLOCK) for _ in range(height * BLOCK)]

    def pixel(tile: int, x: int, y: int, hflip: int, vflip: int) -> int:
        if hflip:
            x = 7 - x
        if vflip:
            y = 7 - y
        source, index = (primary, tile) if tile < primary_tiles else (secondary, tile - primary_tiles)
        if index >= source["count"]:
            return 0
        return source["rows"][index * 8 + y][x]

    for by in range(height):
        for bx in range(width):
            metatile = _value(data, lay["blocks"] + 2 * (by * width + bx), 2) & 0x3FF
            if metatile < primary_metatiles:
                entry_at = primary["metatiles"] + metatile * 16
            else:
                entry_at = secondary["metatiles"] + (metatile - primary_metatiles) * 16
            if entry_at + 16 > len(data):
                raise ValueError(f"map header 0x{header:06x}: metatile {metatile} exceeds ROM")
            for layer in range(2):
                for quarter in range(4):
                    entry = _value(data, entry_at + 2 * (layer * 4 + quarter), 2)
                    tile, hflip, vflip, pal = entry & 0x3FF, entry >> 10 & 1, entry >> 11 & 1, entry >> 12
                    ox, oy = bx * BLOCK + (quarter & 1) * 8, by * BLOCK + (quarter >> 1) * 8
                    for y in range(8):
                        row = image[oy + y]
                        for x in range(8):
                            colour = pixel(tile, x, y, hflip, vflip)
                            if colour:
                                row[ox + x] = pal * 16 + colour
                            elif layer == 0:
                                row[ox + x] = 0
    return image, palette, lay


def areas(block: dict, data: bytes, species_count: int) -> dict[str, int]:
    """The map header of every runtime map that has an encounter table."""
    from . import pokedexencounters
    out = {}
    for row in pokedexencounters.build(block["encounters"], data, species_count)["records"]:
        if row["map_header"] is not None:
            out[row["runtime_map"]] = int(row["map_header"], 16)
    return out


def index(block: dict, data: bytes, species_count: int, found: dict[str, int] | None = None) -> list[dict]:
    """The dataset's map records: runtime map, header, size in blocks and file."""
    problems = check(block)
    if problems:
        raise ValueError("the profile cannot drive area maps: " + ", ".join(problems))
    found = areas(block, data, species_count) if found is None else found
    records = []
    for runtime, header in sorted(found.items(), key=lambda item: [int(part) for part in item[0].split(".")]):
        lay = layout(data, header)
        records.append({"runtime_map": runtime, "map_header": f"0x{header:06x}",
                        "width": lay["width"], "height": lay["height"], "image": map_file(runtime)})
    return records


def build(block: dict, data: bytes, species_count: int, found: dict[str, int] | None = None) -> dict:
    """The map documents: one PNG per runtime map, as byte strings."""
    records = index(block, data, species_count, found)
    maps = []
    for record in records:
        image, palette, _ = render(data, int(record["map_header"], 16), block["maps"])
        maps.append({**record, "png": indexed_png(image, palette, 8)})
    return {"rom_sha1": hashlib.sha1(data).hexdigest(), "maps": maps}


def write(out: Path, doc: dict) -> tuple[Path, int]:
    """Write the PNGs and the manifest; returns (manifest, bytes written)."""
    out = Path(out)
    bytes_written = 0
    files = {}
    for record in doc["maps"]:
        path = out / record["image"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(record["png"])
        bytes_written += len(record["png"])
        files[record["runtime_map"]] = {key: record[key] for key in ("image", "map_header", "width", "height")}
    manifest = {"format": "gen3tl-pokedex-maps", "rom": {"sha1": doc["rom_sha1"]},
                "count": len(files), "files": files}
    path = out / "maps" / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    encoding="utf-8")
    return path, bytes_written


def verify(out: Path, block: dict, rom_sha1: str) -> list[str]:
    """Every way the written maps are stale against the profile; empty when current."""
    problems = check(block)
    if problems:
        return [f"profile: {p}" for p in problems]
    path = Path(out) / "maps" / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"manifest: cannot read {path}: {exc}"]
    if manifest.get("rom", {}).get("sha1") != rom_sha1:
        return [f"rom sha1: manifest is {manifest.get('rom', {}).get('sha1')!r}, profile pins {rom_sha1!r}"]
    files = manifest.get("files") or {}
    if manifest.get("count") != len(files):
        return [f"count: manifest lists {manifest.get('count')!r} for {len(files)} files"]
    missing = [record["image"] for record in files.values() if not (Path(out) / record["image"]).exists()]
    if missing:
        return [f"files missing: {len(missing)}, first {missing[0]}"]
    return []
