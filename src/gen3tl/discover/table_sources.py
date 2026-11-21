"""Gift/egg command bindings and trade records from native consumers."""
from __future__ import annotations

import struct

from ..core.rom import ROM_BASE
from ..core.thumb import ThumbError, decode, walk
from .foundations import evidence
from .table_core import _issue, _set


def _calls(rom, start, end):
    try:
        return tuple(row for row in walk(rom.data, start, end) if row.mnemonic == "bl")
    except ThumbError:
        return ()


def _helper_width(rom, target):
    if target is None:
        return None
    try:
        body = walk(rom.data, target, min(len(rom), target + 64))
    except ThumbError:
        return None
    loads = [row for row in body if row.mnemonic == "ldrb"]
    return 2 if len(loads) == 2 else 4 if len(loads) == 4 else None


def _commands(rom, result, knowledge, readers):
    descriptor = knowledge.get("commands")
    if not descriptor:
        return
    gift_matches = readers.matches.get(descriptor["gift"]["reader"], ())
    egg_matches = readers.matches.get(descriptor["egg"]["reader"], ())
    if len(gift_matches) != 1 or not egg_matches:
        _issue(result, "pokedex.sources.commands", "Native gift or egg handlers are missing or ambiguous.",
               "semantic-reader")
        return
    gift_start = gift_matches[0]
    gift_calls = _calls(rom, gift_start, gift_start + readers.rows[descriptor["gift"]["reader"]]["length"])
    egg_calls = [call for start in egg_matches
                 for call in _calls(rom, start, start + readers.rows[descriptor["egg"]["reader"]]["length"])]
    gift_widths = [width for call in gift_calls if (width := _helper_width(rom, call.target)) is not None]
    egg_widths = [width for call in egg_calls if (width := _helper_width(rom, call.target)) is not None]
    if sorted(set(gift_widths)) != [2, 4] or any(width != 2 for width in egg_widths) or not egg_widths:
        _issue(result, "pokedex.sources.commands", "Handler stream reads do not prove gift u16/u32 and egg u16 semantics.",
               "handler-stream-widths")
        return
    gift_wide = [call for call in gift_calls if _helper_width(rom, call.target) == 2]
    egg_wide = [call for call in egg_calls if _helper_width(rom, call.target) == 2]
    if not gift_wide or not egg_wide or len({call.target for call in (*gift_wide, *egg_wide)}) != 1:
        _issue(result, "pokedex.sources.commands", "Gift and egg handlers do not share one u16 stream reader.",
               "handler-shared-stream-reader")
        return
    bases = {}
    for opcode, entry in ((descriptor["gift"]["opcode"], gift_start),
                          (descriptor["egg"]["opcode"], None)):
        needle = struct.pack("<I", ROM_BASE + entry + 1) if entry is not None else None
        if needle is None:
            continue
        position = 0
        while True:
            position = rom.data.find(needle, position)
            if position < 0:
                break
            candidate = position - opcode * 4
            if candidate >= 0:
                bases.setdefault(candidate, []).append(entry)
            position += 1
    matches = {}
    for base in bases:
        for opcode, candidate_entries in ((descriptor["gift"]["opcode"], (gift_start,)),
                                          (descriptor["egg"]["opcode"], egg_matches)):
            at = base + opcode * 4
            if at + 4 > len(rom):
                break
            value = rom.u32(at)
            if any(ROM_BASE + entry + 1 == value for entry in candidate_entries):
                matches.setdefault(base, {})[opcode] = value
    if len(matches) != 1 or len(matches[list(matches)[0]]) != 2:
        _issue(result, "pokedex.sources.commands", "Handler pointers do not select one script command table.",
               "handler-pointer-reverse-match", candidates=len(matches))
        return
    base = next(iter(matches))
    sites = [base + opcode * 4 for opcode in (descriptor["gift"]["opcode"], descriptor["egg"]["opcode"])]
    sites += [call.address for call in (*gift_calls, *egg_calls)]
    proof = evidence("Behavioral gift/egg fingerprints and their pointer entries select one script command table.",
        "handler-stream-widths+handler-pointer-reverse-match", [base], sites,
        gift=descriptor["gift"]["opcode"], egg=descriptor["egg"]["opcode"])
    _set(result, "pokedex.sources.commands",
         {"table": base, "gift": descriptor["gift"]["opcode"], "egg": descriptor["egg"]["opcode"]}, proof)


def _specials_table(rom, result, knowledge, readers):
    """The native special dispatch function and its bounded pointer table."""
    descriptor = knowledge.get("specials")
    if not descriptor:
        return None
    matches = readers.matches.get(descriptor["reader"], ())
    if len(matches) != 1:
        _issue(result, "pokedex.sources.commands", "The native special dispatch function is missing or ambiguous.",
               "semantic-reader")
        return None
    start = matches[0]
    try:
        read_call = decode(rom.data, start + descriptor["read_call"])
        scale = decode(rom.data, start + descriptor["scale"])
        scale_high = decode(rom.data, start + descriptor["scale"] + 2)
        base_load = decode(rom.data, start + descriptor["base_load"])
        bound_load = decode(rom.data, start + descriptor["bound_load"])
        compare = decode(rom.data, start + descriptor["bound_compare"])
        branch = decode(rom.data, start + descriptor["branch"])
    except ThumbError:
        read_call = scale = scale_high = base_load = bound_load = compare = branch = None
    if (read_call is None or read_call.mnemonic != "bl"
            or scale is None or scale.mnemonic != "lsl" or scale.immediate != 16
            or scale_high is None or scale_high.mnemonic != "lsr" or scale_high.immediate != 14
            or base_load is None or base_load.mnemonic != "ldr" or base_load.literal is None
            or bound_load is None or bound_load.mnemonic != "ldr" or bound_load.literal is None
            or compare is None or compare.mnemonic != "cmp" or compare.rn != compare.rd
            or branch is None or branch.mnemonic != "b" or branch.condition != "cs"):
        _issue(result, "pokedex.sources.commands",
               "Special dispatch operands do not prove id scaling, table bounds and the call.",
               "semantic-reader+instruction-operands")
        return None
    table = rom.ptr_at(base_load.literal)
    bound = rom.ptr_at(bound_load.literal)
    if table is None or bound is None or bound <= table or (bound - table) % 4:
        _issue(result, "pokedex.sources.commands",
               "The special dispatch table leaves ROM bounds or is not a pointer array.",
               "table-bounds", [base_load.literal, bound_load.literal])
        return None
    return {"start": start, "table": table, "end": bound, "entries": (bound - table) // 4,
            "sites": [read_call.address, scale.address, scale_high.address,
                      base_load.address, bound_load.address, compare.address, branch.address],
            "anchors": [table, bound]}


def _trade_command(rom, result, knowledge, readers):
    """Reverse-match the trade-special handler into the native special dispatch table."""
    dispatch = _specials_table(rom, result, knowledge, readers)
    descriptor = knowledge.get("specials", {}).get("trade")
    if dispatch is None or not descriptor:
        return None
    matches = readers.matches.get(descriptor["reader"], ())
    task_matches = readers.matches.get(descriptor.get("task", ""), ())
    found = []
    for start in matches:
        needle = struct.pack("<I", ROM_BASE + start + 1)
        position = 0
        while True:
            position = rom.data.find(needle, position)
            if position < 0:
                break
            if not (dispatch["table"] <= position < dispatch["end"] and (position - dispatch["table"]) % 4 == 0):
                position += 1
                continue
            scheduled = ()
            try:
                callback_load = decode(rom.data, start + descriptor["callback_load"])
                callback = (rom.ptr_at(callback_load.literal) & ~1) if callback_load.literal is not None else None
                if callback is not None:
                    body = walk(rom.data, callback, min(len(rom), callback + 64))
                    scheduled = tuple(value & ~1 for row in body if row.literal is not None
                                      and (value := rom.ptr_at(row.literal)) is not None and value & 1)
            except ThumbError:
                pass
            if task_matches and any(task in scheduled for task in task_matches):
                found.append(((position - dispatch["table"]) // 4, start, position))
            position += 1
    if len(found) != 1:
        _issue(result, "pokedex.sources.trades.command",
               "The trade-special handler and its scheduled task do not select one special dispatch table slot.",
               "specials-dispatch+handler-reverse-match", candidates=len(found))
        return None
    command, start, position = found[0]
    if not 0 <= command < dispatch["entries"]:
        _issue(result, "pokedex.sources.trades.command", "The reversed special id leaves the dispatch table.",
               "table-bounds", [position])
        return None
    return {"command": command, "start": start, "position": position, "dispatch": dispatch}


def _trades(rom, result, knowledge, readers, bindings):
    descriptor = knowledge.get("trades")
    species = bindings.get("species")
    if not descriptor or species is None:
        return
    matches = readers.matches.get(descriptor["reader"], ())
    if len(matches) != 1:
        _issue(result, "pokedex.sources.trades", "The native trade formatter is missing or ambiguous.",
               "semantic-reader")
        return
    start = matches[0]
    row = readers.rows[descriptor["reader"]]
    window = row.get("analysis", {"start": 0, "end": row["length"]})
    entry = start + window["start"]
    try:
        code = {instruction.address: instruction for instruction in walk(rom.data, entry, start + window["end"])}
    except ThumbError:
        code = {}
    base_load = code.get(start + descriptor["offset"])
    wanted = code.get(start + descriptor["wanted"])
    given = code.get(start + descriptor["species"])
    if any(instruction is None for instruction in (base_load, wanted, given)):
        reason = descriptor.get("unreachable_reason")
        omitted = (bool(code) and all(instruction is None for instruction in (base_load, wanted, given))
                   and isinstance(reason, str) and bool(reason.strip()))
        sentence = "Native trade table loads are not reachable from the reader entry."
        if omitted:
            sentence += " " + reason.strip()
        _issue(result, "pokedex.sources.trades", sentence,
               "semantic-reader+entry-reachability", [entry], status="omitted" if omitted else "unresolved")
        return
    if (base_load.literal is None
            or wanted.mnemonic != "ldrh" or wanted.immediate != 0x38
            or given.mnemonic != "ldrh" or given.immediate != 0x0C):
        _issue(result, "pokedex.sources.trades", "Native trade field reads do not match wanted and given offsets.",
               "semantic-reader+accessor")
        return
    base = rom.ptr_at(base_load.literal)
    candidates = []
    for stride in range(13, 256):
        if base + stride * 2 > len(rom):
            continue
        count = 0
        while count < 64 and base + (count + 1) * stride <= len(rom):
            at = base + count * stride
            if not 0 < rom.u16(at + given.immediate) < species.count:
                break
            count += 1
        if count >= 2:
            candidates.append((count, stride))
    if not candidates:
        _issue(result, "pokedex.sources.trades", "The complete trade record domain selects no stride.",
               "complete-record-domain", [base])
        return
    maximum = max(count for count, _ in candidates)
    best = [stride for count, stride in candidates if count == maximum]
    if len(best) != 1:
        _issue(result, "pokedex.sources.trades", "Trade record stride is ambiguous.",
               "complete-record-domain", [base], candidates=len(best))
        return
    stride, count = best[0], maximum
    value = {"base": base, "stride": stride, "count": count,
             "species": given.immediate, "wanted": wanted.immediate}
    sites = [entry, base_load.address, wanted.address, given.address]
    anchors = [base]
    method = "semantic-reader+entry-reachability+accessor+complete-record-domain"
    sentence = "Native trade field reads and the complete species-bounded domain select the record layout."
    command = _trade_command(rom, result, knowledge, readers)
    if command is not None:
        value["command"] = command["command"]
        sites.extend((command["position"], *command["dispatch"]["sites"]))
        anchors.extend(command["dispatch"]["anchors"])
        method += "+specials-dispatch+handler-reverse-match"
        sentence = ("Native trade field reads and the species-bounded domain select the layout; "
                    "the special dispatch scales the id by four, bounds it by the table end and calls the entry; "
                    "handler operands and scheduler reach are not decoded.")
    proof = evidence(sentence, method, anchors, sites, stride=stride, rows=count)
    _set(result, "pokedex.sources.trades", value, proof)


def run(rom, result, knowledge, readers, bindings):
    if "commands" in knowledge:
        _commands(rom, result, knowledge, readers)
    if "trades" in knowledge:
        _trades(rom, result, knowledge, readers, bindings)
