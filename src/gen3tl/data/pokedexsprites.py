"""Pokedex sprites: one PNG per species slot and variant."""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

from ..core.notes import has_prose
from ..build.graphics import lz77_decompress
from ..check.render import indexed_png
from ..core.rom import ROM_BASE, pointer_at

SPRITE_TABLES = ("front", "back")
PALETTE_TABLES = ("palettes", "shiny_palettes")
VARIANTS = ("front", "back", "shiny", "back_shiny")


def variant_files(slot: int) -> dict:
    """The file of each variant of a slot, relative to the sprite directory."""
    return {v: f"sprites/{slot}-{v.replace('_', '-')}.png" for v in VARIANTS}


def trainer_file(picture: int) -> str:
    """The front picture shared by trainer records."""
    return f"trainers/{picture}.png"


def check_trainers(block: dict) -> list[str]:
    """Validate optional trainer pictures and their record index."""
    pictures = block.get("pictures")
    if pictures is None:
        return []
    if not isinstance(pictures, dict):
        return ["pictures: a table"]
    problems = []
    fields, stride = block.get("fields", {}), block.get("stride")
    field = fields.get("picture") if isinstance(fields, dict) else None
    if type(field) is not int or type(stride) is not int or not 0 <= field < stride:
        problems.append("fields picture: an offset within the trainer record")
    if not has_prose(pictures.get("evidence", "")):
        problems.append("pictures evidence")
    side = pictures.get("side")
    if type(side) is not int or side <= 0 or side % 8:
        problems.append("pictures side: a positive multiple of eight")
    for name in ("front", "palettes"):
        table = pictures.get(name, {})
        if not isinstance(table, dict):
            problems.append(f"pictures {name}: a table")
            continue
        for key in ("base", "stride", "count"):
            value = table.get(key)
            if type(value) is not int or value < (0 if key == "base" else 1):
                problems.append(f"pictures {name} {key}")
        if type(table.get("stride")) is int and table["stride"] < 8:
            problems.append(f"pictures {name} stride: at least eight bytes")
    front, palettes = pictures.get("front", {}), pictures.get("palettes", {})
    if isinstance(front, dict) and isinstance(palettes, dict) and front.get("count") != palettes.get("count"):
        problems.append("pictures: matching picture and palette counts")
    return problems


def trainer_sources(pictures: dict, data: bytes) -> list[dict]:
    """Resolve each picture's compressed pixel and palette records."""
    sources = []
    for picture in range(int(pictures["front"]["count"])):
        row = {"id": picture, "image": trainer_file(picture)}
        for name in ("front", "palettes"):
            table = pictures[name]
            at = int(table["base"]) + picture * int(table["stride"])
            if at + 8 > len(data):
                raise ValueError(f"trainer picture {picture} {name}: record exceeds ROM")
            offset = pointer_at(data, at)
            if offset is None:
                raise ValueError(f"trainer picture {picture} {name}: invalid pointer")
            row[name] = {"record": f"0x{at:06x}", "data": f"0x{offset:06x}"}
        sources.append(row)
    return sources


def _record(rom_data: bytes, table: dict, slot: int, what: str) -> tuple[int, int]:
    """The data pointer and tag field of one slot's record."""
    base, stride, count = int(table["base"]), int(table.get("stride", 8)), int(table["count"])
    if not 0 <= slot < count:
        raise ValueError(f"[pokedex.sprites.{what}] slot {slot} outside count {count}")
    at = base + slot * stride
    pointer = int.from_bytes(rom_data[at:at + 4], "little")
    tag_at = at + int(table.get("tag", 4))
    tag = int.from_bytes(rom_data[tag_at:tag_at + 2], "little")
    return pointer, tag


def _sprite_bytes(rom_data: bytes, pointer: int, what: str, side: int | None = None) -> tuple[list[list[int]], int]:
    """The pixel indices (4 bits each) of the square sprite a pointer holds."""
    offset = pointer - ROM_BASE
    blob, _ = lz77_decompress(rom_data, offset)
    if side is None:
        side = int((len(blob) * 2) ** 0.5)
        if side * side // 2 != len(blob):
            raise ValueError(f"{what}: {len(blob)} bytes is not a square 4bpp sprite")
    elif len(blob) < side * side // 2:
        raise ValueError(f"{what}: {len(blob)} bytes cannot fill the declared canvas")
    px = [[0] * side for _ in range(side)]
    tiles = side // 8
    for ty in range(tiles):
        for tx in range(tiles):
            base = (ty * tiles + tx) * 32
            for y in range(8):
                for x in range(4):
                    b = blob[base + y * 4 + x]
                    px[ty * 8 + y][tx * 8 + x * 2] = b & 0xF
                    px[ty * 8 + y][tx * 8 + x * 2 + 1] = b >> 4
    return px, side


def _palette(rom_data: bytes, pointer: int) -> list[tuple[int, int, int]]:
    """The 16 RGB555 colours of a palette blob, expanded to 8-bit channels."""
    blob, _ = lz77_decompress(rom_data, pointer - ROM_BASE)
    out = []
    for i in range(16):
        v = struct.unpack_from("<H", blob, i * 2)[0]
        out.append((((v & 0x1F) << 3), (((v >> 5) & 0x1F) << 3), (((v >> 10) & 0x1F) << 3)))
    return out


def check(block: dict | None) -> list[str]:
    """Every problem that keeps a [pokedex] block from producing sprites."""
    if not block:
        return ["[pokedex] block"]
    sprites = block.get("sprites")
    if not sprites:
        return ["[pokedex.sprites] block"]
    problems = []
    for name in SPRITE_TABLES + PALETTE_TABLES:
        table = sprites.get(name)
        if not table:
            problems.append(f"[pokedex.sprites] {name}")
            continue
        for key in ("base", "stride", "count"):
            if key not in table:
                problems.append(f"[pokedex.sprites.{name}] {key}")
    if not has_prose(sprites.get("evidence", "")):
        problems.append("[pokedex.sprites] evidence")
    problems += [f"[pokedex.trainers] {p}" for p in check_trainers(block.get("trainers", {}))]
    return problems


def build(block: dict, rom_data: bytes) -> dict:
    """The sprite documents: one PNG per variant per slot, as byte strings."""
    problems = check(block)
    if problems:
        raise ValueError("the profile cannot drive Pokedex sprites: " + ", ".join(problems))
    sprites = block["sprites"]
    count = int(sprites["front"]["count"])
    first = int(block["species"].get("first", 1)) if "species" in block else 1
    docs = []
    for slot in range(first, count):
        variants = {}
        pixels = {}
        for name in SPRITE_TABLES:
            pointer, _ = _record(rom_data, sprites[name], slot, name)
            pixels[name], _ = _sprite_bytes(rom_data, pointer, f"slot {slot} {name}")
        palette_pointers = {}
        for name in PALETTE_TABLES:
            pointer, _ = _record(rom_data, sprites[name], slot, name)
            palette_pointers[name] = _palette(rom_data, pointer)
        variants["front"] = indexed_png(pixels["front"], palette_pointers["palettes"], 4, transparent=0)
        variants["back"] = indexed_png(pixels["back"], palette_pointers["palettes"], 4, transparent=0)
        variants["shiny"] = indexed_png(pixels["front"], palette_pointers["shiny_palettes"], 4, transparent=0)
        variants["back_shiny"] = indexed_png(pixels["back"], palette_pointers["shiny_palettes"], 4, transparent=0)
        docs.append({"slot": slot, "variants": variants})
    trainers = []
    if pictures := block.get("trainers", {}).get("pictures"):
        for source in trainer_sources(pictures, rom_data):
            pixels, _ = _sprite_bytes(rom_data, ROM_BASE + int(source["front"]["data"], 16),
                                      f"trainer picture {source['id']}", int(pictures["side"]))
            palette = _palette(rom_data, ROM_BASE + int(source["palettes"]["data"], 16))
            trainers.append({**source, "png": indexed_png(pixels, palette, 4, transparent=0)})
    return {"rom_sha1": hashlib.sha1(rom_data).hexdigest(), "count": count,
            "files": variant_files, "slots": docs, "trainers": trainers}


def write(out: Path, doc: dict) -> tuple[Path, int]:
    """Write the PNGs and the manifest; returns (manifest, bytes written)."""
    out = Path(out)
    bytes_written = 0
    manifest_files = {}
    for slot_doc in doc["slots"]:
        slot = slot_doc["slot"]
        files = doc["files"](slot)
        manifest_files[str(slot)] = files
        for variant, data in slot_doc["variants"].items():
            path = out / files[variant]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            bytes_written += len(data)
    manifest = {
        "format": "gen3tl-pokedex-sprites",
        "rom": {"sha1": doc["rom_sha1"]},
        "count": doc["count"],
        "files": manifest_files,
    }
    if trainers := doc.get("trainers"):
        manifest["trainers"] = [{k: v for k, v in row.items() if k != "png"} for row in trainers]
        for row in trainers:
            target = out / row["image"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(row["png"])
            bytes_written += len(row["png"])
    path = out / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    encoding="utf-8")
    return path, bytes_written


def verify(out: Path, block: dict, rom_sha1: str) -> list[str]:
    """Every way the written sprites are stale against the profile; empty when current."""
    problems = check(block)
    if problems:
        return [f"profile: {p}" for p in problems]
    path = out / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"manifest: cannot read {path}: {exc}"]
    if manifest.get("rom", {}).get("sha1") != rom_sha1:
        return [f"rom sha1: manifest is {manifest.get('rom', {}).get('sha1')!r}, profile pins {rom_sha1!r}"]
    count = int(block["sprites"]["front"]["count"])
    if manifest.get("count") != count:
        return [f"count: manifest lists {manifest.get('count')!r}, the table holds {count}"]
    missing = []
    for slot in range(int(block.get("species", {}).get("first", 1)), count):
        for variant in VARIANTS:
            if not (out / variant_files(slot)[variant]).exists():
                missing.append(variant_files(slot)[variant])
    if missing:
        return [f"files missing: {len(missing)}, first {missing[0]}"]
    if pictures := block.get("trainers", {}).get("pictures"):
        trainers = manifest.get("trainers", [])
        count = int(pictures["front"]["count"])
        if [row.get("id") for row in trainers] != list(range(count)):
            return ["trainer pictures: regenerate the sprites with gen3tl data sprites"]
        for row in trainers:
            path = trainer_file(row["id"])
            if row.get("image") != path or not (out / path).is_file():
                return [f"trainer picture missing: {path}"]
    return []
