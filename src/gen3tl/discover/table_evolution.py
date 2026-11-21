"""Evolution record geometry and the native tile-method consumer."""
from __future__ import annotations

from ..core.thumb import ThumbError, decode
from .foundations import evidence
from .table_core import _issue, _set


def _instruction(rom, start, offset, mnemonic, immediate=None, width=None):
    instruction = decode(rom.data, start + offset)
    if instruction.mnemonic != mnemonic or immediate is not None and instruction.immediate != immediate:
        return None
    if width is not None and instruction.width != width:
        return None
    return instruction


def run(rom, result, knowledge, readers, bindings):
    descriptor = knowledge.get("evolution")
    species = bindings.get("species")
    if not descriptor or species is None:
        return
    identifiers = ("pokedex.evolution", "pokedex.evolution.fields.method", "pokedex.evolution.fields.parameter",
                   "pokedex.evolution.fields.target", "pokedex.evolution.fields.extra.offset",
                   "pokedex.evolution.fields.extra.width")
    matches = readers.matches.get(descriptor["reader"], ())
    if len(matches) != 1:
        for identifier in identifiers:
            _issue(result, identifier, "The real evolution core is missing or ambiguous.", "semantic-reader")
        return
    start = matches[0]
    try:
        base_load = decode(rom.data, start + descriptor["base_load"])
        instructions = {
            name: _instruction(rom, start, offset, mnemonic, immediate, width)
            for name, offset, mnemonic, immediate, width in (
                ("species_scale", descriptor["species_scale"], "lsl", 7, None),
                ("slot_scale", descriptor["slot_scale"], "lsl", 3, None),
                ("method", descriptor["method"], "ldrh", 0, 2),
                ("parameter", descriptor["parameter"], "ldrh", 2, 2),
                ("tile_method", descriptor["tile_method"], "cmp", 34, None),
                ("extra", descriptor["extra"], "ldrh", 6, 2),
                ("target", descriptor["target"], "ldrh", 4, 2),
            )
        }
    except ThumbError:
        instructions, base_load = {}, None
    if (base_load is None or base_load.literal is None
            or any(value is None for value in instructions.values())):
        for identifier in identifiers:
            _issue(result, identifier, "Decoded evolution arithmetic, field reads and tile-method case disagree.",
                   "semantic-reader+instruction-sequence")
        return
    base = rom.ptr_at(rom.ptr_at(base_load.literal)) if rom.ptr_at(base_load.literal) is not None else None
    if base is None or base + species.count * 128 > len(rom):
        for identifier in identifiers:
            _issue(result, identifier, "The evolution base is not a two-level ROM pointer or leaves ROM bounds.",
                   "semantic-reader+pointer-chain", [base_load.literal])
        return
    # Native core proves 128-byte species rows, 8-byte slot records.
    for number in range(species.count):
        at = base + number * 128
        if any(rom.u16(at + slot * 8) == 0 and any(
                rom.u16(at + slot * 8 + offset) for offset in (2, 4, 6)) for slot in range(16)):
            _issue(result, "pokedex.evolution", "Method-zero stop records must have zero parameter, target and extra.",
                   "stop-record-domain", [base])
            return
    sites = [base_load.address, base_load.literal, base, *(row.address for row in instructions.values())]
    proof = evidence("The native evolution core proves 128-byte species rows, 8-byte slot records, all four halfword fields and the method-34 tile case.",
        "semantic-reader+affine-accessor+instruction-sequence", [base], sites,
        species_stride=128, slots=16, record_stride=8, stop=0, tile_methods=1)
    value = {"base": base, "stride": 128, "slots": 16, "record": 8, "stop": 0, "tile_methods": [34]}
    _set(result, "pokedex.evolution", value, proof)
    for name, offset in (("method", 0), ("parameter", 2), ("target", 4)):
        _set(result, "pokedex.evolution.fields." + name, {"offset": offset, "width": 2}, proof)
    extra_proof = evidence("The method-34 case reads the extra halfword and compares it with the native map-grid behavior.",
        "semantic-case+extra-halfword+tile-comparison", [base],
        [instructions["tile_method"].address, instructions["extra"].address,
         instructions["target"].address, base_load.address], tile_method=34, offset=6, width=2)
    _set(result, "pokedex.evolution.fields.extra.offset", 6, extra_proof)
    _set(result, "pokedex.evolution.fields.extra.width", 2, extra_proof)
