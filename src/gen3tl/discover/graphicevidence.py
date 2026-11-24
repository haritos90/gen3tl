"""Bounded ROM code and data evidence for graphics discovery."""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict

from ..core import thumb
from ..core.graphics import GraphicsError, lz77_decompress
from ..core.rom import Rom, rom_offset
from .consumerproof import (DMA3_COPY, TransferProof, consumer_paths, consumer_start, graphic_proofs,
                            palette_proofs, staged_palette_candidates)

MAX_RAW_SIZE = 65536
DUPLICATE_TILES = 256
CALL_WINDOW = 16       # instructions after a load that may carry its call
CONTEXT_BYTES = 64     # bytes disassembled after a load
CONTEXT_BACK = 0x100   # bytes disassembled before a load, for veneer registers


def literal_loads(rom: Rom) -> dict[int, list[thumb.Instruction]]:
    """ROM targets loaded by actual Thumb PC-relative LDR instructions."""
    out: dict[int, list[thumb.Instruction]] = defaultdict(list)
    data = rom.data
    for at in range(0, len(data) - 1, 2):
        half = data[at] | data[at + 1] << 8
        if half & 0xF800 != 0x4800:
            continue
        literal = ((at + 4) & ~3) + (half & 0xFF) * 4
        target = rom.ptr_at(literal) if literal + 4 <= len(data) else None
        if target is not None:
            out[target].append(thumb.decode(data, at))
    return dict(out)


def _context(rom: Rom, load: thumb.Instruction) -> list[thumb.Instruction]:
    start = max(0, load.address - CONTEXT_BACK)
    end = min(len(rom), load.address + CONTEXT_BYTES)
    instructions = []
    while start < end:
        try:
            ins = thumb.decode(rom.data, start)
        except thumb.ThumbError:
            break
        if start + ins.size > end:
            break
        instructions.append(ins)
        start += ins.size
    return instructions


def _decode_span(rom: Rom, start: int, end: int) -> list[thumb.Instruction]:
    """Decode one bounded code span, stopping at the first failure."""
    instructions = []
    while start < end:
        try:
            ins = thumb.decode(rom.data, start)
        except thumb.ThumbError:
            break
        if start + ins.size > end:
            break
        instructions.append(ins)
        start += ins.size
    return instructions


def _constant(instructions: list[thumb.Instruction], call: int, register: int) -> tuple[int | None, list[int]]:
    value = None
    evidence = []
    for ins in instructions[max(0, call - 7):call]:
        if register not in ins.written:
            continue
        evidence.append(ins.address)
        if ins.mnemonic == "mov" and ins.rd == register and ins.immediate is not None:
            value = ins.immediate
        elif value is not None and ins.rd == register and ins.rn == register:
            if ins.mnemonic == "lsl":
                value <<= ins.immediate
            elif ins.mnemonic == "add" and ins.immediate is not None:
                value += ins.immediate
            elif ins.mnemonic == "sub" and ins.immediate is not None:
                value -= ins.immediate
            else:
                value = None
        else:
            value = None
    return value, evidence


def _calls(rom: Rom, load: thumb.Instruction) -> tuple[list[thumb.Instruction], list[int]]:
    instructions = _context(rom, load)
    try:
        at = next(i for i, ins in enumerate(instructions) if ins.address == load.address)
    except StopIteration:
        return instructions, []
    return instructions, [i for i in range(at + 1, min(len(instructions), at + 1 + CALL_WINDOW))
                          if instructions[i].mnemonic == "bl"]


def _bios_reachable(rom: Rom, target: int | None, swis: tuple[int, ...],
                    depth: int = 2, seen: set[int] | None = None) -> bool:
    """Cheap prefilter only; never establishes consumer dataflow."""
    if target is None or depth < 0 or not 0 <= target < len(rom):
        return False
    if seen is None:
        seen = set()
    if target in seen:
        return False
    seen.add(target)
    try:
        body = thumb.walk(rom.data, target, min(len(rom), target + 0x100))
    except thumb.ThumbError:
        body = _decode_span(rom, target, target + 0x100)
    if any(ins.mnemonic == "swi" and ins.immediate in swis for ins in body):
        return True
    return any(_bios_reachable(rom, ins.target, swis, depth - 1, seen)
               for ins in body if ins.mnemonic == "bl")


def _palette_spec(rom: Rom, target: int, sub: int) -> str:
    if rom.data[target] != 0x10:
        return f"raw:0x{target + sub:07X}"
    suffix = "" if sub == 0 else f"+0x{sub:X}"
    return f"lz:0x{target:07X}{suffix}"


def valid_palette(rom: Rom, offset: int, length: int = 32) -> bool:
    """True when `length` bytes at `offset` hold in-bounds BGR555 words."""
    if offset < 0 or length < 2 or length % 2 or offset + length > len(rom):
        return False
    words = [int.from_bytes(rom.read(offset + 2 * k, 2), "little") for k in range(length // 2)]
    return all(word & 0x8000 == 0 for word in words)


def palette_shaped(rom: Rom, offset: int, length: int = 32) -> bool:
    """Shape candidate: valid BGR555 words with at least one non-zero colour."""
    return valid_palette(rom, offset, length) and any(
        int.from_bytes(rom.read(offset + 2 * k, 2), "little") for k in range(length // 2))


def _decoded_palette_valid(rom: Rom, target: int, sub: int, length: int) -> bool:
    """Bounds and BGR555 encoding of a palette source, decompressing LZ77 targets."""
    if rom.data[target] == 0x10:
        try:
            data, _used = lz77_decompress(rom.data, target)
        except GraphicsError:
            return False
        if sub + length > len(data):
            return False
        if length % 2:
            return False
        words = [int.from_bytes(data[sub + 2 * k:sub + 2 * k + 2], "little")
                 for k in range(length // 2)]
        return all(word & 0x8000 == 0 for word in words)
    return valid_palette(rom, target + sub, length)


def _palette_paths(rom: Rom, loads: dict[int, list[thumb.Instruction]]):
    """Bounded paths from palette-shaped literal loads that reach a copy call."""
    for target, sites in sorted(loads.items()):
        if not _decoded_palette_valid(rom, target, 0, 32):
            continue
        for load in sites:
            instructions, calls = _calls(rom, load)
            if not calls:
                continue
            call_target = instructions[calls[0]].target
            veneer = call_target is not None and bool(_decode_span(rom, call_target, call_target + 2))
            if veneer:
                veneer = _decode_span(rom, call_target, call_target + 2)[0].mnemonic == "bx"
            if not veneer and not _bios_reachable(rom, call_target, (0x0B, 0x0C)):
                continue
            start = consumer_start(rom, load.address, 0x80)
            try:
                paths = consumer_paths(rom, start, min(len(rom), load.address + 0x100))
            except thumb.ThumbError:
                continue
            for path_index, path in enumerate(paths):
                yield load, path_index, path


def _add_palette_records(rom: Rom, out: list[dict], load: thumb.Instruction, path_index: int, path,
                         proof, tier: str, method: str, staging: dict | None = None) -> None:
    length = proof.length
    if not (length in (32, 512) or length % 32 == 0 and 32 <= length <= 512):
        return
    if length == 32:
        records = [(proof.target, proof.sub, proof.bank)]
    elif length == 512:
        records = [(proof.target, proof.sub, None)]
    elif proof.bank is not None and length // 32 <= 16:
        records = [(proof.target, proof.sub + 32 * i, proof.bank + i) for i in range(length // 32)]
    else:
        return
    event = path.events[proof.event_index]
    call_event = event
    if not call_event.chain and event.swi == DMA3_COPY:
        call_event = next((prior for prior in reversed(path.events[:proof.event_index])
                           if prior.chain), event)
    if not call_event.chain:
        return
    resolved = thumb.decode(rom.data, call_event.root_call).target
    for base, sub, rec_bank in records:
        if not _decoded_palette_valid(rom, base, sub, 32 if length != 512 else 512):
            continue
        evidence = {"method": method, "load": load.address,
                    "pool": load.literal, "call": call_event.root_call,
                    "resolved_call": resolved,
                    "bios_swi": (event.site, event.swi),
                    "call_chain": call_event.chain,
                    "length": length, "source_target": proof.target,
                    "source_sub": proof.sub,
                    "destination": proof.destination,
                    "destination_kind": proof.destination_kind,
                    "source_event": proof.event_index,
                    "path": path_index, "path_diverged": False}
        if staging is not None:
            evidence["staging_transfer"] = staging
        record = {"target": base if rom.data[base] == 0x10 else base + sub,
                  "spec": _palette_spec(rom, base, sub),
                  "bpp": 8 if length == 512 else 4,
                  "bank": rec_bank, "tier": tier, "evidence": evidence}
        key = (record["spec"], record["bpp"], record["bank"])
        if not any((p["spec"], p["bpp"], p["bank"]) == key for p in out):
            out.append(record)


def palette_evidence(rom: Rom, loads: dict[int, list[thumb.Instruction]],
                     transfers: tuple[TransferProof, ...] = ()) -> tuple[list[dict], list[dict]]:
    """Proved palette copies, and copies into buffers some DMA3 transfer reads."""
    proved: list[dict] = []
    staged: list[dict] = []
    for load, path_index, path in _palette_paths(rom, loads):
        for proof in palette_proofs(rom, path):
            _add_palette_records(rom, proved, load, path_index, path, proof, "consumer", "palette-copy")
        for proof, transfer in staged_palette_candidates(rom, path, transfers):
            _add_palette_records(rom, staged, load, path_index, path, proof, "candidate",
                                 "staging-dma-candidate",
                                 {"source": transfer.source, "destination": transfer.destination,
                                  "length": transfer.length, "site": transfer.site})
    keys = {(p["spec"], p["bpp"], p["bank"]) for p in proved}
    return proved, [p for p in staged if (p["spec"], p["bpp"], p["bank"]) not in keys]


def palette_loads(rom: Rom, loads: dict[int, list[thumb.Instruction]]) -> list[dict]:
    """Palette copies with proved source, count and hardware destination."""
    return palette_evidence(rom, loads)[0]


def adjacent_palette_records(rom: Rom, blocks: list[tuple[int, int]]) -> list[dict]:
    """Palette-shaped bytes immediately after an LZ77 block end (data-layout candidate)."""
    out = []
    for offset, block_length in blocks:
        pal = offset + block_length
        if palette_shaped(rom, pal):
            out.append({"target": pal, "spec": _palette_spec(rom, pal, 0), "bpp": 4, "bank": None,
                        "tier": "candidate",
                        "evidence": {"method": "adjacent-after-lz77-block", "block": offset,
                                     "block_length": block_length, "palette": pal}})
    return out


def descriptor_triples(rom: Rom, idx: dict[int, list[int]]) -> list[dict]:
    """Aligned runs of three ROM pointers with a valid palette third."""
    out = []
    positions = sorted({at for refs in idx.values() for at in refs if at % 4 == 0})
    for at in positions:
        if at + 12 > len(rom):
            continue
        first, second, third = rom.ptr_at(at), rom.ptr_at(at + 4), rom.ptr_at(at + 8)
        if first is None or second is None or third is None:
            continue
        if third % 4:
            continue
        if rom.data[first] != 0x10 or rom.data[second] != 0x10:
            continue
        if not valid_palette(rom, third):
            continue
        out.append({"at": at, "first": first, "second": second, "third": third})
    return out


def verified_descriptor_triples(rom: Rom, triples: list[dict],
                                loads: dict[int, list[thumb.Instruction]]) -> list[dict]:
    """Triples whose exact fields feed graphic and palette consumer roles."""
    verified = []
    by_at = {triple["at"]: triple for triple in triples}

    def from_field(proof, pool: int, target: int) -> bool:
        return proof.target == target and any(
            origin.pool == pool and rom_offset(origin.value) == target
            for origin in proof.origins)

    for base, sites in sorted(loads.items()):
        triple = by_at.get(base)
        if triple is None:
            continue
        for load in sites:
            start = consumer_start(rom, load.address)
            try:
                paths = consumer_paths(rom, start, min(len(rom), load.address + 0xC0))
            except thumb.ThumbError:
                continue
            for path_index, path in enumerate(paths):
                graphics = graphic_proofs(rom, path)
                palettes = palette_proofs(rom, path)
                first = next((proof for proof in graphics
                              if from_field(proof, base, triple["first"])), None)
                second = next((proof for proof in graphics
                               if from_field(proof, base + 4, triple["second"])), None)
                palette = next((proof for proof in palettes
                                if from_field(proof, base + 8, triple["third"])
                                and proof.length in (32, 512)), None)
                if first is None or second is None or palette is None or palette.bank is None:
                    continue
                palette_region = "obj" if palette.destination >= 0x05000200 else "bg"
                if first.region != second.region or first.region != palette_region:
                    continue
                palette_event = path.events[palette.event_index]
                if not palette_event.chain:
                    continue
                consumers = [(path.events[proof.event_index].site,
                              path.events[proof.event_index].swi) for proof in (first, second)]
                verified.append({"at": triple["at"], "first": triple["first"],
                                 "second": triple["second"], "third": triple["third"],
                                 "bank": palette.bank,
                                 "evidence": {"load": load.address, "region": first.region,
                                              "palette_call": palette_event.root_call,
                                              "bios_swi": (palette_event.site, palette_event.swi),
                                              "dest": palette.destination,
                                              "destination_kind": palette.destination_kind,
                                              "consumers": consumers, "path": path_index,
                                              "fields": {0: first.event_index, 4: second.event_index,
                                                         8: palette.event_index}}})
    return verified


def _descriptor_records(rom: Rom, idx: dict[int, list[int]]) -> list[dict]:
    positions = {at: target for target, refs in idx.items() for at in refs if at % 4 == 0}

    def size_at(at: int) -> int | None:
        if at not in positions or at + 8 > len(rom):
            return None
        size = rom.u32(at + 4) & 0xFFFF
        return size if 32 <= size <= MAX_RAW_SIZE and size % 32 == 0 else None

    records = []
    visited = set()
    for at in sorted(positions):
        if at in visited or size_at(at) is None:
            continue
        run = []
        current = at
        while size_at(current) is not None:
            run.append(current)
            visited.add(current)
            current += 8
        if len(run) < 3:
            continue
        for member in run:
            records.append({"offset": positions[member], "size": size_at(member),
                            "evidence": {"method": "pointer-size-table", "record": member,
                                         "size_field": member + 4, "stride": 8, "members": len(run)}})
    return records


def _code_records(rom: Rom, loads: dict[int, list[thumb.Instruction]]) -> list[dict]:
    records = []
    for target, sites in sorted(loads.items()):
        if rom.data[target] == 0x10:
            continue
        for load in sites:
            instructions, calls = _calls(rom, load)
            for call in calls[:1]:
                size, size_sites = _constant(instructions, call, 2)
                if size is None or not 32 <= size <= MAX_RAW_SIZE or size % 32:
                    continue
                if target + size > len(rom):
                    continue
                records.append({"offset": target, "size": size,
                                "evidence": {"method": "code-copy-length", "load": load.address,
                                             "pool": load.literal, "call": instructions[call].address,
                                             "length_instructions": size_sites}})
    return records


def _accessor_targets(rom: Rom, loads: dict[int, list[thumb.Instruction]]) -> set[int]:
    out = set()
    for target, sites in loads.items():
        for load in sites:
            instructions = _context(rom, load)
            try:
                at = next(i for i, ins in enumerate(instructions) if ins.address == load.address)
            except StopIteration:
                continue
            following = instructions[at + 1:at + 4]
            if any(ins.mnemonic == "bx" and ins.rm == 14 for ins in following):
                out.add(target)
                break
    return out


def _consumer_targets(rom: Rom, loads: dict[int, list[thumb.Instruction]]) -> set[int]:
    out = set()
    for target, sites in loads.items():
        for load in sites:
            if load.rd == 0 and _calls(rom, load)[1]:
                out.add(target)
                break
    return out


def _duplicate_records(rom: Rom, idx: dict[int, list[int]], sources: list[dict],
                       known: set[int]) -> list[dict]:
    out = []
    for source in sources:
        if source["size"] < 2048 or source["size"] % 32:
            continue
        offset = source["offset"]
        prefix = bytes(rom.data[offset:offset + 256])
        tiles = [prefix[i:i + 32] for i in range(0, 256, 32)]
        if len(set(tiles)) < 2 or all(len(set(tile)) == 1 for tile in tiles):
            continue
        at = rom.data.find(prefix)
        while at >= 0:
            if at != offset and at % 4 == 0 and not idx.get(at) and at not in known:
                size = min(DUPLICATE_TILES * 32, source["size"])
                if at + size <= len(rom):
                    out.append({"offset": at, "size": size,
                                "evidence": {"method": "bounded-tile-duplicate", "source": offset,
                                             "anchor": at, "prefix_tiles": 8, "tile_bound": DUPLICATE_TILES}})
                    known.add(at)
            at = rom.data.find(prefix, at + 1)
    return out


def raw_records(rom: Rom, idx: dict[int, list[int]], loads: dict[int, list[thumb.Instruction]],
                lz_offsets: set[int], palette_offsets: set[int]) -> list[dict]:
    """Raw spans from descriptors, copy lengths, referenced anchors and bounded duplicates."""
    descriptors = _descriptor_records(rom, idx)
    exact = {}
    for record in descriptors + _code_records(rom, loads):
        old = exact.get(record["offset"])
        if old is not None and old["size"] != record["size"]:
            continue
        exact[record["offset"]] = record
    anchors = sorted(lz_offsets | palette_offsets | set(exact) | _accessor_targets(rom, loads))
    fallback = sorted(_consumer_targets(rom, loads) - set(anchors))
    for target, sites in sorted(loads.items()):
        if target in exact or target in lz_offsets or target % 4:
            continue
        at = bisect_right(anchors, target)
        anchor = anchors[at] if at < len(anchors) else None
        method = "referenced-anchor"
        if anchor is None or not 32 <= anchor - target <= MAX_RAW_SIZE:
            at = bisect_right(fallback, target)
            anchor = fallback[at] if at < len(fallback) else None
            method = "code-consumer-anchor"
        if anchor is None:
            continue
        size = anchor - target
        if not 32 <= size <= MAX_RAW_SIZE or target + size > len(rom):
            continue
        exact[target] = {"offset": target, "size": size,
                         "evidence": {"method": method, "anchor": anchor,
                                      "load": sites[0].address, "pool": sites[0].literal}}
    ordered = sorted(exact.values(), key=lambda record: record["offset"])
    duplicates = _duplicate_records(rom, idx, ordered, set(exact) | lz_offsets)
    return ordered + duplicates
