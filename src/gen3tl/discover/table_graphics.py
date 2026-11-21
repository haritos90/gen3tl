"""Growth, type and icon tables recovered from native graphics consumers."""

from __future__ import annotations

from ..core.thumb import decode
from .foundations import evidence
from .table_core import GraphicsError, _issue, _set, lz77_decompress


def _accessors(readers, roles, width=None):
    for role in roles:
        for _, analysis in readers.analyze(role):
            for row in analysis.accessors:
                if width is None or row.width == width:
                    yield row


def _growth(rom, result, readers, descriptor):
    roles = (descriptor["summary"], descriptor["level"])
    native = [row for row in _accessors(readers, roles, 4)
              if any(value == 1024 for _, value in row.indices)]
    bases = {row.base for row in native}
    if len(bases) != 1 or not native:
        _issue(result, "pokedex.growth_rates", "Independent growth readers do not select one 1024-byte growth stride.",
               "semantic-reader+growth-stride", candidates=len(bases))
        return
    base = bases.pop()
    if base + 1024 > len(rom):
        _issue(result, "pokedex.growth_rates", "The recovered growth table leaves ROM bounds.", "table-bounds", [base])
        return
    sites = [row.site for row in native]
    proof = evidence("Independent native experience reads use 32-bit entries and a 1024-byte growth-row stride.",
        "semantic-readers+growth-stride", [base], sites, entries=256, entry_width=4, readers=len(native))
    _set(result, "pokedex.growth_rates", {"base": base, "entries": 256}, proof)


def _types(rom, result, readers, descriptor):
    native = [row for row in _accessors(readers, (descriptor["reader"],), 1)
              if any(value == 24 for _, value in row.indices)]
    bases = {row.base for row in native}
    if len(bases) != 1 or not native:
        _issue(result, "pokedex.type_effectiveness", "The native type-matrix reader does not select one 24-type stride.",
               "semantic-reader+matrix-stride", candidates=len(bases))
        return
    base = bases.pop()
    if base + 24 * 24 > len(rom):
        _issue(result, "pokedex.type_effectiveness", "The complete square type matrix leaves ROM bounds.", "table-bounds", [base])
        return
    proof = evidence("Native attack indexing multiplies by 24 and defense indexing reads one byte across the square matrix.",
        "semantic-reader+matrix-stride+complete-bounds", [base], [row.site for row in native],
        types=24, rows=24, entry_width=1)
    _set(result, "pokedex.type_effectiveness", {"base": base, "count": 24}, proof)


def _item_icons(rom, result, readers, items):
    native = [row for row in _accessors(readers, ("item-icons",), 4)
              if any(value == 8 for _, value in row.indices)]
    bases = {row.base for row in native}
    if len(bases) != 1 or not native:
        _issue(result, "pokedex.icons.items", "The native item-icon reader does not select one eight-byte record array.",
               "semantic-reader+icon-record-stride", candidates=len(bases))
        return
    base = bases.pop()
    try:
        for number in range(items.count):
            at = base + number * 8
            picture, palette = rom.ptr_at(at), rom.ptr_at(at + 4)
            if picture is None or palette is None or len(lz77_decompress(rom.data, picture)[0]) != 288:
                raise GraphicsError("invalid item icon")
            palette_bytes = lz77_decompress(rom.data, palette)[0] if rom.u8(palette) == 16 else rom.read(palette, 32)
            if len(palette_bytes) != 32:
                raise GraphicsError("invalid item palette")
    except (GraphicsError, IndexError, TypeError):
        _issue(result, "pokedex.icons.items", "Every item picture and palette must decode to 288 and 32 bytes.",
               "complete-icon-domain", [base])
        return
    proof = evidence("Native eight-byte indexing and complete 288-byte picture/32-byte palette decoding validate every item row.",
        "semantic-reader+complete-icon-domain", [base], [row.site for row in native],
        rows=items.count, stride=8, picture_bytes=288, palette_bytes=32)
    _set(result, "pokedex.icons.items", {"table": base, "count": items.count}, proof)


def _split_icons(rom, result, knowledge, readers):
    descriptor = knowledge["icons"]["split"]
    native = []
    for _, analysis in readers.analyze(descriptor["reader"]):
        native.extend(row for row in analysis.addresses if any(value == 192 for _, value in row.indices))
    bases = {row.base for row in native}
    if len(bases) != 1 or not native:
        _issue(result, "pokedex.icons.split", "The native split-bitmap address does not use a unique 192-byte stride.",
               "semantic-reader+bitmap-stride", candidates=len(bases))
        return
    base = bases.pop()
    width_low = decode(rom.data, native[0].site + 2)
    if width_low.mnemonic != "mov" or width_low.immediate != 15:
        _issue(result, "pokedex.icons.split", "The split bitmap lacks its native width/height argument sequence.",
               "native-bitmap-arguments")
        return
    width_high = decode(rom.data, width_low.address + 4)
    if width_high.mnemonic != "add" or width_high.rn != width_low.rd or width_high.immediate != 9:
        _issue(result, "pokedex.icons.split", "The native bitmap width is not 24 pixels.", "native-bitmap-arguments")
        return
    proof = evidence("Native 192-byte indexing and explicit 24-pixel blit arguments determine six 8x8 tiles per bitmap.",
        "semantic-reader+bitmap-stride+native-bitmap-arguments", [base],
        [row.site for row in native] + [width_low.address, width_high.address],
        stride=192, width=24, stored_height=16, drawn_height=15)
    _set(result, "pokedex.icons.split", {"base": base, "stride": 192, "width": 24, "height": 16}, proof)


def _row_geometry(rows, tiles):
    valid = []
    for stride in range(1, tiles + 1):
        used = set()
        for width, height, offset in rows:
            cells = {offset + y * stride + x for y in range((height + 7) // 8)
                     for x in range((width + 7) // 8)}
            if used & cells:
                break
            used.update(cells)
        else:
            if used and max(used) < tiles:
                valid.append(stride)
    return valid


def _type_icons(rom, result, knowledge, readers):
    descriptor = knowledge["icons"]["types"]
    sheet, palette = readers.load(descriptor["sheet"]), readers.load(descriptor["palette"])
    table = readers.load(descriptor["table"])
    if not all((sheet, palette, table)) or palette["base"] <= sheet["base"] or (palette["base"] - sheet["base"]) % 32:
        _issue(result, "pokedex.type_icons", "Native type-icon literals do not delimit a 4bpp sheet and palette.",
               "semantic-readers+graphics-boundary")
        return
    tiles = (palette["base"] - sheet["base"]) // 32
    rows = []
    while len(rows) < 256 and table["base"] + (len(rows) + 1) * 4 <= len(rom):
        at = table["base"] + len(rows) * 4
        width, height, offset = rom.u8(at), rom.u8(at + 1), rom.u16(at + 2)
        if not 0 < width <= 64 or height != 12 or offset >= tiles:
            break
        rows.append((width, height, offset))
    geometry = _row_geometry(rows, tiles)
    if not rows or len(geometry) != 1:
        _issue(result, "pokedex.type_icons", "Complete icon records do not select one non-overlapping sheet-row geometry.",
               "complete-icon-records+row-geometry", candidates=len(geometry))
        return
    sites = [row["site"] for row in (sheet, palette, table)]
    proof = evidence("Native literals delimit the raw 4bpp sheet and palette; complete 12-pixel icon records uniquely determine row geometry.",
        "semantic-readers+graphics-boundary+complete-icon-records+row-geometry",
        [sheet["base"], palette["base"], table["base"]], sites,
        tiles=tiles, tiles_per_row=geometry[0], rows=len(rows), record_stride=4)
    value = {"sheet": sheet["base"], "tiles": tiles, "tiles_per_row": geometry[0],
             "palette": palette["base"], "table": table["base"], "count": len(rows)}
    _set(result, "pokedex.type_icons", value, proof)


def run(rom, result, knowledge, readers, bindings):
    if "growth" in knowledge:
        _growth(rom, result, readers, knowledge["growth"])
    if "type_effectiveness" in knowledge:
        _types(rom, result, readers, knowledge["type_effectiveness"])
    if "items" in bindings and knowledge.get("icons", {}).get("items"):
        _item_icons(rom, result, readers, bindings["items"])
    if knowledge.get("icons", {}).get("split"):
        _split_icons(rom, result, knowledge, readers)
    if knowledge.get("icons", {}).get("types"):
        _type_icons(rom, result, knowledge, readers)
