"""Species-indexed learnsets and item/move ranges from native getters."""

from __future__ import annotations

import math
import re

from ..core.thumb import decode, walk
from .foundations import evidence
from .table_core import _issue, _set, text


def _array(readers, role, width):
    candidates = {}
    for _, analysis in readers.analyze(role):
        for reader in analysis.accessors:
            if reader.width != width or not reader.indices:
                continue
            stride = max(value for _, value in reader.indices)
            candidates.setdefault((reader.base, stride), []).append(reader)
    if len(candidates) != 1:
        return None
    (base, stride), native = next(iter(candidates.items()))
    return base, stride, native


def item_ranges(rom, items):
    named = {"TM": {}, "HM": {}}
    for slot in range(items.count):
        at = items.base + slot * items.stride
        pointer = rom.ptr_at(at)
        value = text(rom, pointer if pointer is not None else at, 128 if pointer is not None else items.length, name=True)
        match = re.fullmatch(r"(TM|HM)([0-9]{2,3})", value[0]) if value is not None else None
        if match is None:
            continue
        prefix, number = match[1], int(match[2])
        if number in named[prefix]:
            return None
        named[prefix][number] = slot
    ordered, ranges = [], []
    for prefix in ("TM", "HM"):
        rows = named[prefix]
        if not rows or set(rows) != set(range(1, max(rows) + 1)):
            return None
        start = len(ordered)
        for number in range(1, max(rows) + 1):
            slot = rows[number]
            if number > 1 and ranges[-1][2] + ranges[-1][1] == slot:
                ranges[-1][1] += 1
            else:
                ranges.append([start + number - 1, 1, slot])
            ordered.append(slot)
    return len(ordered), ranges


def _level_up(rom, result, descriptor, readers, species, moves):
    identifier = "pokedex.learnsets.level_up"
    array = _array(readers, descriptor["reader"], 4)
    matches = readers.matches.get(descriptor["reader"], ())
    if array is None or len(matches) != 1:
        _issue(result, identifier, "Native pointer-array and packed-entry readers must match uniquely.", "semantic-reader")
        return
    base, stride, native = array
    entry = decode(rom.data, matches[0] + descriptor["entry_stride"])
    level = decode(rom.data, matches[0] + descriptor["level_instruction"])
    stop = decode(rom.data, level.address + level.size)
    if (stride != 4 or entry.mnemonic != "mov" or entry.immediate != 3
            or level.mnemonic != "ldrb" or level.immediate != entry.immediate - 1
            or stop.mnemonic != "cmp" or stop.rn != level.rd or stop.immediate != 255
            or base + species.count * stride > len(rom)):
        _issue(result, identifier, "Decoded packed-entry layout and level terminator do not agree.", "packed-entry-reader")
        return
    longest = 0
    for slot in range(species.count):
        pointer = rom.ptr_at(base + slot * stride)
        if pointer is None:
            break
        for count in range(512):
            at = pointer + count * entry.immediate
            if at + entry.immediate > len(rom):
                break
            move, value = rom.u16(at), rom.u8(at + level.immediate)
            if move == 0 or value == stop.immediate:
                longest = max(longest, count)
                break
            if move >= moves.count:
                break
        else:
            break
        if at + entry.immediate > len(rom) or move >= moves.count and value != stop.immediate:
            break
    else:
        proof = evidence("Native pointer indexing and packed-entry reads validate a terminated learnset for every recovered species slot.",
            "semantic-reader+packed-entry-layout+complete-slot-domain", [base],
            [entry.address, level.address, stop.address, *(reader.site for reader in native)],
            slots=species.count, pointer_stride=stride, entry_stride=entry.immediate, longest=longest)
        _set(result, identifier, {"base": base, "count": species.count}, proof)
        return
    _issue(result, identifier, "A species slot has an invalid, out-of-domain or unterminated packed learnset.",
           "complete-slot-domain", [base], slot=slot)


def _egg(rom, result, descriptor, readers, species, moves):
    identifier = "pokedex.learnsets.egg"
    load = readers.load(descriptor)
    if load is None:
        _issue(result, identifier, "The native species-marker reader is missing or ambiguous.", "semantic-reader")
        return
    marker = decode(rom.data, load["start"] + descriptor["marker"])
    bound = decode(rom.data, load["start"] + descriptor["bound"])
    if marker.literal is None or bound.literal is None:
        _issue(result, identifier, "The native marker offset and array bound are not literal operands.", "native-marker-bound")
        return
    offset, count = rom.u32(marker.literal), rom.u32(bound.literal)
    if not moves.count <= offset < 65535 - species.count or not 0 < count <= 65536 or load["base"] + count * 2 > len(rom):
        _issue(result, identifier, "Native marker and count operands do not fit a complete bounded u16 array.",
               "native-marker-bound", [load["base"]])
        return
    values = [rom.u16(load["base"] + number * 2) for number in range(count)]
    markers = [value - offset for value in values if offset < value < offset + species.count]
    if not markers or values[0] != offset + 1 or any(
            not (value < moves.count or offset < value < offset + species.count or value == 65535) for value in values):
        _issue(result, identifier, "Native marker arithmetic does not validate every packed species/move entry.",
               "native-marker-bound+complete-packed-array", [load["base"]])
        return
    proof = evidence("Native marker arithmetic, an explicit array bound and complete species/move validation identify the packed egg list.",
        "semantic-reader+native-marker-bound+complete-packed-array", [load["pool"], marker.literal, bound.literal],
        [load["site"], marker.address, bound.address], entries=count, marker_offset=offset, species_markers=len(markers))
    _set(result, identifier, {"base": load["base"]}, proof)


def run(rom, result, knowledge, readers, bindings):
    descriptor = knowledge.get("learnsets")
    species, moves, items = (bindings.get(name) for name in ("species", "moves", "items"))
    if not descriptor or species is None or moves is None or items is None:
        return
    _level_up(rom, result, descriptor["level_up"], readers, species, moves)
    _egg(rom, result, descriptor["egg"], readers, species, moves)
    ranges = item_ranges(rom, items)
    for name in ("tmhm", "tutor"):
        identifier = "pokedex.learnsets." + name
        row = descriptor[name]
        array = _array(readers, row["reader"], 4)
        move_array = _array(readers, row["moves"], 2)
        if array is None or move_array is None or name == "tmhm" and ranges is None:
            _issue(result, identifier, "Native bit-row/move getters and the item-index domain must identify unique complete arrays.",
                   "semantic-reader+bit-row-layout+move-domain")
            continue
        base, stride, native = array
        move_base, move_stride, move_native = move_array
        sites = [reader.site for reader in (*native, *move_native)]
        if name == "tmhm":
            count = ranges[0]
        else:
            starts = readers.matches[row["moves"]]
            guard = decode(rom.data, starts[0] + row["move_guard"]) if len(starts) == 1 else None
            if guard is None or guard.mnemonic != "cmp" or guard.rn != 0 or guard.immediate is None:
                _issue(result, identifier, "The tutor getter lacks a unique decoded move-index bound.", "native-index-bound")
                continue
            count = guard.immediate + 1
            sites.append(guard.address)
        if (not 0 < count <= stride * 8 or move_stride != 2 or base + stride * species.count > len(rom)
                or move_base + count * move_stride > len(rom)
                or not all(0 < rom.u16(move_base + number * move_stride) < moves.count for number in range(count))):
            _issue(result, identifier, "The native bit capacity and complete move domain disagree.",
                   "native-index-bound+complete-move-domain", [base, move_base])
            continue
        proof = evidence("Native species indexing supplies bit-row bytes; native move reads and an independent index domain bound every move.",
            "semantic-reader+bit-row-layout+native-move-domain", [base, move_base], sites,
            rows=species.count, stride=stride, move_stride=move_stride, moves=count)
        value = {"base": base, "stride": stride, "count": species.count, "move_base": move_base, "move_count": count}
        _set(result, identifier, value, proof)
        if name == "tmhm":
            alias = {"base": move_base, "stride": move_stride, "count": count, "ranges": ranges[1]}
            alias_proof = evidence("The item TM/HM alias reuses the native move array; decoded numbered item names determine contiguous slot ranges.",
                "shared-binding+numbered-item-domain", [move_base, items.base], sites,
                moves=count, stride=move_stride, ranges=len(ranges[1]))
            _set(result, "pokedex.items.tm_moves", alias, alias_proof)
