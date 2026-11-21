"""ROM table shapes, bounded readers and evidence records."""

from __future__ import annotations

import bisect
import copy
import math
import re
import struct
from dataclasses import dataclass, field

from ..core.charmap import Charmap, CharmapError
from ..core.rom import ROM_BASE, Rom
from ..core.thumb import ThumbError, decode, walk
from ..profile import schema
from .accessors import analyze
from .foundations import evidence

SOURCE = "tables/1.0.0"


class GraphicsError(Exception):
    pass


def lz77_decompress(data: bytes | bytearray, off: int = 0) -> tuple[bytes, int]:
    """Decode the LZ77 block at `off`. Returns the data and the block length in bytes."""
    if off + 4 > len(data) or data[off] != 0x10:
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
                for k in range((b1 >> 4) + 3):
                    out.append(out[start + k])
            else:
                if i >= n:
                    raise GraphicsError(f"LZ77 block at {off:#x} runs past the end of the data")
                out.append(data[i])
                i += 1
    return bytes(out[:size]), i - off


@dataclass
class Result:
    data: dict = field(default_factory=dict)
    facts: list[schema.Fact] = field(default_factory=list)
    issues: list[schema.Fact] = field(default_factory=list)
    metadata: dict = field(default_factory=lambda: {"methods": {}, "bindings": []})


@dataclass(frozen=True)
class Names:
    name: str
    base: int
    stride: int
    count: int
    length: int
    anchors: tuple[int, ...]


class Index:
    """Index aligned pointer words and linked Thumb readers once."""

    def __init__(self, rom: Rom):
        self.rom = rom
        pointers, pools, entries = {}, {}, set()
        for index, (value,) in enumerate(struct.iter_unpack("<I", memoryview(rom.data)[:len(rom) & ~3])):
            if ROM_BASE <= value < ROM_BASE + len(rom):
                pointers.setdefault(value - ROM_BASE, []).append(index * 4)
                target = (value - ROM_BASE) & ~1
                if value & 1 and target + 2 <= len(rom) and rom.u16(target) & 0xFF00 == 0xB500:
                    entries.add(target)
        for index, (halfword,) in enumerate(struct.iter_unpack("<H", memoryview(rom.data)[:len(rom) & ~1])):
            at = index * 2
            if halfword & 0xF800 == 0x4800:
                pool = ((at + 4) & ~3) + (halfword & 255) * 4
                if pool + 4 <= len(rom) and rom.ptr_at(pool) is not None:
                    pools.setdefault(pool, []).append(at)
            elif halfword & 0xF800 == 0xF000 and at + 4 <= len(rom):
                lower = rom.u16(at + 2)
                if lower & 0xF800 == 0xF800:
                    distance = ((halfword & 0x7FF) << 12) | ((lower & 0x7FF) << 1)
                    target = at + 4 + (distance - (1 << 23) if distance & (1 << 22) else distance)
                    if 0 <= target < len(rom):
                        entries.add(target)
        self.pointers, self.pools = pointers, pools
        pending = list(entries)
        while pending:
            start = pending.pop()
            try:
                first = decode(rom.data, start)
                second = decode(rom.data, start + first.size)
            except ThumbError:
                continue
            target = None
            if first.mnemonic == "b" and first.condition is None:
                target = first.target
            elif first.literal is not None and second.rm == first.rd and (
                    second.mnemonic == "bx" or second.mnemonic == "mov" and second.rd == 15):
                pointer = rom.ptr_at(first.literal)
                if pointer is not None:
                    target = pointer & ~1
            if target is not None and 0 <= target < len(rom) and target not in entries:
                entries.add(target)
                pending.append(target)
        self.entries = sorted(entries)
        self.walks, self.analyses = {}, {}

    def readers(self, base: int, stride: int, header: int | None = None, offsets=range(1)) -> list[dict]:
        words = set()
        for offset in offsets:
            words.update(self.pointers.get(base + offset, ()))
        for word in tuple(words):
            words.update(self.pointers.get(word, ()))
        if header is not None:
            words.update(self.pointers.get(header, ()))
        found = {}
        for pool in sorted(words):
            for site in self.pools.get(pool, ()):
                first = bisect.bisect_left(self.entries, max(0, site - 512))
                last = bisect.bisect_right(self.entries, site)
                for entry in self.entries[first:last]:
                    bound = min(len(self.rom), entry + 1024)
                    if entry not in self.walks:
                        try:
                            self.walks[entry] = {instruction.address for instruction in walk(self.rom.data, entry, bound)}
                        except ThumbError:
                            self.walks[entry] = set()
                    if site not in self.walks[entry]:
                        continue
                    starts = [entry, *(at for at in range(max(entry, site - 96), site + 1, 2)
                                       if at in self.walks[entry])]
                    for start in starts:
                        end = min(bound, site + 48)
                        key = start, end
                        if key not in self.analyses:
                            try:
                                result = analyze(self.rom.data, start, end, limit=512)
                                self.analyses[key] = (*result.accessors, *result.addresses)
                            except ThumbError:
                                self.analyses[key] = ()
                        for reader in self.analyses[key]:
                            coefficients = [value for _, value in reader.indices]
                            if not base <= reader.base < base + stride or stride not in coefficients and (
                                    not coefficients or math.gcd(*coefficients) != stride):
                                continue
                            offset = reader.field_offset + reader.base - base
                            identity = reader.site, offset, getattr(reader, "width", 0)
                            found[identity] = {"entry": entry, "site": reader.site, "field": offset,
                                               "stride": stride, "width": getattr(reader, "width", 0),
                                               "signed": getattr(reader, "signed", False),
                                               "indices": list(reader.indices),
                                               "pools": [literal.pool for literal in reader.literals],
                                               "code_sites": list(reader.code_sites)}
                    if found:
                        break
        return [found[key] for key in sorted(found)]


def text(rom: Rom, start: int, maximum: int, name=False, blank=False) -> tuple[str, int] | None:
    if start < 0 or start + maximum > len(rom):
        return None
    try:
        value, end = Charmap().decode(rom.data, start, limit=maximum)
    except (CharmapError, IndexError):
        return None
    if not value or not blank and not value.strip() or re.search(r"\{[0-9A-F]{2}\}", value):
        return None
    clean = value.replace("{PK}", "PK").replace("{MN}", "MN")
    if name and ("{" in clean or "\\" in value or not all(c.isprintable() for c in value)):
        return None
    return value, end


def name_stride(rom: Rom, base: int, anchors: list[dict], maximum=128) -> tuple[int, tuple[int, ...]] | None:
    candidates = []
    for stride in range(2, maximum + 1):
        sites = []
        for anchor in anchors:
            at = base + anchor["index"] * stride
            actual = text(rom, at, stride, name=True)
            if actual is None or actual[0].casefold() != anchor["text"].casefold():
                break
            sites.append(at)
        else:
            candidates.append((stride, tuple(sites)))
    return candidates[0] if len(candidates) == 1 else None


def names(rom: Rom, name: str, base: int, anchors: list[dict], blank=False, pointers=False) -> Names | None:
    found = name_stride(rom, base, anchors)
    if found is None:
        return None
    stride, sites = found
    count = 0
    while count < 8192:
        at = base + count * stride
        pointer = rom.ptr_at(at) if pointers else None
        value = text(rom, pointer if pointer is not None else at, 128 if pointer is not None else stride,
                     name=True, blank=blank)
        if value is None or pointer is None and any(byte not in (0, 255) for byte in rom.data[value[1]:at + stride]):
            break
        count += 1
    if count <= max(anchor["index"] for anchor in anchors) or count == 8192:
        return None
    return Names(name, base, stride, count, stride, sites)


def item_names(rom: Rom, base: int, anchors: list[dict]) -> Names | None:
    found = name_stride(rom, base, anchors)
    if found is None:
        return None
    stride, sites = found
    offsets = [offset for offset in range(2, stride - 1, 2)
               if all(rom.u16(base + number * stride + offset) == number for number in range(32))]
    if len(offsets) != 1:
        return None
    count, length = 0, offsets[0]
    while count < 8192 and base + (count + 1) * stride <= len(rom):
        at = base + count * stride
        if rom.u16(at + length) >= 8192:
            break
        pointer = rom.ptr_at(at)
        if pointer is None and rom.u8(at) == 255:
            count += 1
            continue
        if text(rom, pointer if pointer is not None else at, 96 if pointer is not None else length, name=True) is None:
            break
        count += 1
    if count < 32 or count == 8192:
        return None
    return Names("items", base, stride, count, length, sites)


def _record(result: Result, identifier: str, kind: str, value, proof, tier="crossref"):
    section = schema.section_of(tuple(identifier.split("[", 1)[0].split(".")))
    result.facts.append(schema.Fact(identifier, section, kind, copy.deepcopy(value), tier=tier,
                                    status="verified", source=SOURCE, evidence=proof))


def _issue(result: Result, identifier: str, sentence: str, method: str, anchors=(), *, status="unresolved", **counts):
    section = schema.section_of(tuple(identifier.split("[", 1)[0].split(".")))
    result.issues.append(schema.Fact(identifier, section, "table", None,
                                    status=status, source=SOURCE,
                                    evidence=evidence(sentence, method, anchors, **counts)))


def _set(result: Result, identifier: str, value, proof, tier="crossref"):
    path = identifier.split(".")
    parent = result.data
    for name in path[:-1]:
        parent = parent.setdefault(name, {})
    previous = parent.get(path[-1])
    parent[path[-1]] = schema.merge(previous, value) if isinstance(previous, dict) and isinstance(value, dict) else value
    _record(result, identifier, "table", value, proof, tier)


def record_layout(rom: Rom, base: int, anchors: dict) -> tuple[int, dict[str, int]] | None:
    candidates = []
    for stride in range(8, 129):
        if base + (max(anchors["indices"]) + 1) * stride > len(rom):
            continue
        fields = {}
        for name, values in anchors["fields"].items():
            matches = [offset for offset in range(stride) if all(
                rom.u8(base + number * stride + offset) == value
                for number, value in zip(anchors["indices"], values))]
            if len(matches) != 1:
                break
            fields[name] = matches[0]
        else:
            candidates.append((stride, fields))
    return candidates[0] if len(candidates) == 1 else None


def find_names(rom, name, config):
    anchors = config["anchors"]
    anchor = anchors[0]
    pattern, positions, start = Charmap().encode(anchor["text"]), [], 0
    while (at := rom.data.find(pattern, start)) >= 0:
        positions.append(at)
        start = at + len(pattern)
    found = {}
    for at in positions:
        for stride in range(2, 129):
            base = at - anchor["index"] * stride
            if base < 0:
                continue
            binding = names(rom, name, base, anchors, config.get("blank", False), config.get("pointers", False))
            if binding is not None:
                found[(binding.base, binding.stride)] = binding
    return tuple(found.values())


def pointer_name_dispatch(rom, readers):
    for reader in readers:
        if reader["field"] != 3 or reader["width"] != 1:
            continue
        family = [row for row in readers if row["entry"] == reader["entry"] and row["width"] == 1]
        if {row["field"] for row in family} != set(range(4)):
            continue
        site = reader["site"]
        if site + 10 > len(rom):
            continue
        load, move, subtract, compare, branch = [decode(rom.data, site + number * 2) for number in range(5)]
        if (move.mnemonic in ("lsl", "add") and move.rn == load.rd and move.immediate == 0
                and subtract.mnemonic == "sub" and subtract.rn == move.rd
                and subtract.immediate == ROM_BASE >> 24 and compare.mnemonic == "cmp"
                and compare.rn == subtract.rd
                and compare.immediate == ((ROM_BASE + len(rom) - 1) >> 24) - subtract.immediate
                and branch.mnemonic == "b" and branch.condition == "hi"):
            return [row["site"] for row in family] + [move.address, subtract.address, compare.address, branch.address]
    return []


def sign_extension(rom: Rom, site: int, end: int) -> tuple[int, ...]:
    instruction = decode(rom.data, site)
    if instruction.signed:
        return (site,)
    if instruction.mnemonic != "ldrb":
        return ()
    registers = [0] * 16
    registers[instruction.rd] = 1
    pending = [(site + instruction.size, tuple(registers), (site,))]
    seen = set()
    while pending:
        at, prior, sites = pending.pop()
        if not site < at < min(end, len(rom)) or (at, prior) in seen:
            continue
        if len(seen) >= 1024:
            return ()
        seen.add((at, prior))
        current = decode(rom.data, at)
        regs = list(prior)
        if current.mnemonic == "asr" and current.immediate == 24 and prior[current.rn] == 2:
            return (*sites, at)
        value = 0
        if current.mnemonic == "mov" and current.rm is not None:
            value = prior[current.rm]
        elif current.mnemonic in ("add", "sub") and current.immediate is not None:
            value = prior[current.rn]
        elif current.mnemonic == "lsl" and current.immediate == 24 and prior[current.rn] == 1:
            value = 2
        elif current.mnemonic == "lsr" and current.immediate == 24 and prior[current.rn] == 2:
            value = 1
        for register in current.written:
            regs[register] = value if register == current.rd else 0
        if current.mnemonic == "bl":
            for register in (0, 1, 2, 3, 12, 14):
                regs[register] = 0
        path = (*sites, at) if value else sites
        pending.extend((successor, tuple(regs), path) for successor in current.successors)
    return ()


def pointer_texts(rom: Rom, base: int, maximum=8192) -> int:
    count = 0
    while count < maximum and base + count * 4 + 4 <= len(rom):
        pointer = rom.ptr_at(base + count * 4)
        if pointer is None or text(rom, pointer, min(2048, len(rom) - pointer)) is None:
            break
        count += 1
    return count if count < maximum else 0


def sprite_layout(rom: Rom, base: int, count: int, palette: bool) -> dict | None:
    candidates = []
    for stride in range(6, 33, 2):
        if base + stride * count > len(rom):
            continue
        for tag in range(4, stride - 1, 2):
            first = rom.u16(base + tag)
            if not all(rom.ptr_at(base + number * stride) is not None
                       and first <= rom.u16(base + number * stride + tag) < first + count
                       for number in range(count)):
                continue
            if not all(rom.u16(base + number * stride + tag) == first + number
                       for number in range(min(32, count))):
                continue
            value = {"base": base, "stride": stride, "count": count, "tag": tag}
            if not palette:
                sizes = [offset for offset in range(4, stride - 1, 2) if offset != tag and all(
                    0 < rom.u16(base + number * stride + offset) <= 32768
                    and rom.u16(base + number * stride + offset) % 32 == 0 for number in range(count))]
                if len(sizes) != 1:
                    continue
                value["size"] = sizes[0]
            candidates.append(value)
    return candidates[0] if len(candidates) == 1 else None
