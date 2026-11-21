"""Map-section name bindings and map-geometry constants from native loaders."""
from __future__ import annotations

from ..core.thumb import ThumbError, decode, walk
from .foundations import evidence
from .table_core import _issue, _set


def _instruction(rom, start, offset, mnemonic, immediate=None, width=None):
    instruction = decode(rom.data, start + offset)
    if instruction.mnemonic != mnemonic or immediate is not None and instruction.immediate != immediate:
        return None
    if width is not None and instruction.width != width:
        return None
    return instruction


def _immediate_pair(rom, start, move_offset, shift_offset):
    try:
        move = decode(rom.data, start + move_offset)
        shift = decode(rom.data, start + shift_offset)
    except ThumbError:
        return None
    if (move.mnemonic != "mov" or shift.mnemonic != "lsl"
            or shift.rd != move.rd or shift.rn != move.rd or shift.immediate is None):
        return None
    return move.immediate << shift.immediate


def _map_geometry(rom, result, knowledge, readers):
    descriptor = knowledge.get("maps")
    if not descriptor:
        return
    tilesets = readers.matches.get(descriptor["tilesets"], ())
    palettes = readers.matches.get(descriptor["palettes"], ())
    partition = readers.matches.get(descriptor["metatile_partition"], ())
    if len(tilesets) != 1 or len(palettes) != 1 or len(partition) != 1:
        _issue(result, "pokedex.maps", "Native map tileset, palette or metatile-partition loaders are missing or ambiguous.",
               "semantic-reader")
        return
    tilesets_start, palettes_start, partition_start = tilesets[0], palettes[0], partition[0]
    primary_tiles = _immediate_pair(rom, tilesets_start, descriptor["primary_tiles_move"],
                                    descriptor["primary_tiles_shift"])
    secondary_tiles = _immediate_pair(rom, tilesets_start, descriptor["secondary_tiles_move"],
                                      descriptor["secondary_tiles_shift"])
    if primary_tiles is None or secondary_tiles is None or primary_tiles <= 0 or secondary_tiles <= 0:
        _issue(result, "pokedex.maps", "The native tileset loader does not yield positive primary and secondary tile counts.",
               "map-loader+instruction-operands")
        return
    try:
        primary_call = decode(rom.data, palettes_start + descriptor["primary_call"])
        secondary_call = decode(rom.data, palettes_start + descriptor["secondary_call"])
        if primary_call.mnemonic != "bl" or secondary_call.mnemonic != "bl":
            raise ThumbError("palette loader calls are not direct calls")
        primary_dest = decode(rom.data, primary_call.target + descriptor["primary_dest_offset"])
        primary_size = decode(rom.data, primary_call.target + descriptor["primary_size_offset"])
        secondary_dest = decode(rom.data, secondary_call.target + descriptor["secondary_dest_offset"])
        secondary_size = decode(rom.data, secondary_call.target + descriptor["secondary_size_offset"])
    except ThumbError:
        primary_call = secondary_call = primary_dest = primary_size = secondary_dest = secondary_size = None
    if (primary_call is None or primary_call.mnemonic != "bl" or secondary_call is None
            or secondary_call.mnemonic != "bl" or primary_dest is None or primary_dest.mnemonic != "mov"
            or primary_size is None or primary_size.mnemonic != "mov" or primary_size.immediate is None
            or secondary_dest is None or secondary_dest.mnemonic != "mov" or secondary_dest.immediate is None
            or secondary_size is None or secondary_size.mnemonic != "mov" or secondary_size.immediate is None):
        _issue(result, "pokedex.maps", "The native palette loader call operands disagree.",
               "map-loader+instruction-operands")
        return
    palette_bytes = descriptor["palette_bytes"]
    if (primary_size.immediate % palette_bytes or secondary_size.immediate % palette_bytes):
        _issue(result, "pokedex.maps", "Palette loader sizes are not whole palette entries.",
               "instruction-operand")
        return
    primary_palettes = primary_size.immediate // palette_bytes
    secondary_palettes = secondary_size.immediate // palette_bytes
    total_palettes = primary_palettes + secondary_palettes
    if secondary_dest.immediate != primary_palettes * 16 or primary_palettes <= 0 or secondary_palettes <= 0:
        _issue(result, "pokedex.maps", "The secondary palette destination does not follow the primary palette count.",
               "map-loader+instruction-operands")
        return
    try:
        bound_load = decode(rom.data, partition_start + descriptor["metatile_bound_load"])
        bound_compare = decode(rom.data, partition_start + descriptor["metatile_bound_compare"])
        bound_branch = decode(rom.data, partition_start + descriptor["metatile_branch"])
    except ThumbError:
        bound_load = bound_compare = bound_branch = None
    if (bound_load is None or bound_load.literal is None or bound_compare is None
            or bound_compare.mnemonic != "cmp" or bound_compare.rm != bound_load.rd
            or bound_branch is None or bound_branch.mnemonic != "b" or bound_branch.condition != "hi"):
        _issue(result, "pokedex.maps", "The native metatile partition does not bound primary ids before the secondary set.",
               "map-loader+instruction-operands")
        return
    bound = rom.u32(bound_load.literal)
    primary_metatiles = bound + 1
    if bound > 0xFFFF or primary_metatiles <= 0:
        _issue(result, "pokedex.maps", "The metatile partition bound is not a plausible id bound.",
               "table-bounds", [bound_load.literal])
        return
    try:
        body = walk(rom.data, partition_start, min(len(rom), partition_start + readers.rows[descriptor["metatile_partition"]]["length"]))
    except ThumbError:
        body = ()
    if not ({row.immediate for row in body if row.mnemonic == "ldr"} >= {0x10, 0x14}):
        _issue(result, "pokedex.maps", "The metatile partition does not read both tileset layout slots.",
               "map-loader+instruction-operands")
        return
    value = {"primary_tiles": primary_tiles, "primary_metatiles": primary_metatiles,
             "primary_palettes": primary_palettes, "palettes": total_palettes}
    sites = [tilesets_start + descriptor["primary_tiles_move"], tilesets_start + descriptor["primary_tiles_shift"],
             tilesets_start + descriptor["secondary_tiles_move"], tilesets_start + descriptor["secondary_tiles_shift"],
             primary_call.address, secondary_call.address, primary_dest.address, primary_size.address,
             secondary_dest.address, secondary_size.address, bound_load.address, bound_compare.address,
             bound_branch.address]
    proof = evidence("The native map tileset loader copies primary tiles and places the secondary set after them; "
                     "the native palette loader loads whole primary and secondary palette entries; "
                     "the native map block renderer partitions metatile ids before the secondary set.",
                     "map-loader+tileset-copy+palette-loader+metatile-partition",
                     [tilesets_start, palettes_start, partition_start], sites,
                     primary_tiles=primary_tiles, secondary_tiles=secondary_tiles,
                     primary_palettes=primary_palettes, secondary_palettes=secondary_palettes)
    _set(result, "pokedex.maps", value, proof)


def run(rom, result, knowledge, readers):
    _map_geometry(rom, result, knowledge, readers)
    descriptor = knowledge.get("map_sections")
    if not descriptor:
        return
    matches = readers.matches.get(descriptor["reader"], ())
    if len(matches) != 1:
        _issue(result, "pokedex.map_sections", "The native map-section lookup is missing or ambiguous.",
               "semantic-reader")
        return
    start = matches[0]
    try:
        base_load = decode(rom.data, start + descriptor["base_load"])
        scale = decode(rom.data, start + descriptor["scale"])
        add_load = decode(rom.data, start + descriptor["first_add"])
        bound = decode(rom.data, start + descriptor["bound"])
    except ThumbError:
        base_load = scale = add_load = bound = None
    if (base_load is None or base_load.literal is None
            or scale is None or scale.mnemonic != "lsl" or scale.immediate != 2
            or add_load is None or add_load.literal is None
            or bound is None or bound.mnemonic != "cmp"
            or bound.immediate is None or bound.immediate < 0):
        _issue(result, "pokedex.map_sections", "Decoded map-section lookup operands disagree.",
               "semantic-reader+instruction-operands")
        return
    addend = rom.u32(add_load.literal)
    first = 0x10000 - (addend >> 16) if addend & 0xFFFF0000 else None
    if first is None or addend & 0xFFFF:
        _issue(result, "pokedex.map_sections", "The native section-id addend is not a 16-bit two's complement.",
               "instruction-operand")
        return
    base = rom.ptr_at(base_load.literal)
    count = bound.immediate + 1
    if base is None or base + count * 4 > len(rom):
        _issue(result, "pokedex.map_sections", "The recovered section-name table leaves ROM bounds.",
               "table-bounds", [base_load.literal])
        return
    proof = evidence("The native lookup subtracts the first section id, scales by four and bounds the pointer domain.",
        "semantic-reader+instruction-operands+table-bounds",
        [base], [base_load.address, scale.address, add_load.address, bound.address],
        stride=4, first=first, count=count)
    _set(result, "pokedex.map_sections", {"base": base, "stride": 4, "first": first, "count": count}, proof)
