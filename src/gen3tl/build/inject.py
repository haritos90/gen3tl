"""Text injection in place or relocated through verified pointers, then decode-back, pointer and diff-guard checks."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..core.rom import Rom, ptr_bytes
from ..core.fields import long_name_fields
from ..discover.scan import INPLACE_ONLY_KINDS, VERIFIED_KINDS, table_base_lengths, table_base_members, table_base_problems


@dataclass
class Region:
    start: int
    end: int

    @property
    def size(self) -> int:
        return self.end - self.start


class FreeSpace:
    """First-fit allocator over 0xFF runs of the base ROM (and freed slots)."""

    def __init__(self, regions: list[Region]):
        self.regions = sorted(regions, key=lambda r: r.start)
        self.total = sum(r.size for r in self.regions)
        self.used = 0

    @classmethod
    def scan(cls, data: bytes, min_run: int = 256, guard: int = 16,
             exclude: list[tuple[int, int]] | None = None,
             reserved: list[tuple[int, int]] | None = None) -> "FreeSpace":
        """Drop runs overlapping `exclude`; cut `reserved` ranges out of overlapping runs."""
        regions = []
        for m in re.finditer(rb"\xff{%d,}" % min_run, data):
            s, e = m.start() + guard, m.end() - guard
            if any(lo < e and s < hi for lo, hi in exclude or []):
                continue
            pieces = [(s, e)]
            for lo, hi in reserved or []:
                pieces = [q for a, b in pieces for q in ((a, min(b, lo)), (max(a, hi), b)) if q[1] > q[0]]
            regions += [Region(a, b) for a, b in pieces if b - a >= 32]
        return cls(regions)

    def add(self, start: int, end: int) -> None:
        if end - start >= 8:
            self.regions.append(Region(start, end))
            self.regions.sort(key=lambda r: r.start)
            self.total += end - start

    def alloc(self, n: int) -> int | None:
        best = None
        for r in self.regions:
            if r.size >= n and (best is None or r.size < best.size):
                best = r  # best fit keeps large regions for long strings
        if best is None:
            return None
        off = best.start
        best.start += n
        self.used += n
        return off

    def alloc_top(self, n: int) -> int | None:
        """n bytes from the end of the largest region: later first fits keep their places."""
        best = max(self.regions, key=lambda r: r.size, default=None)
        if best is None or best.size < n:
            return None
        best.end -= n
        self.used += n
        return best.end

    @property
    def remaining(self) -> int:
        return sum(r.size for r in self.regions)


@dataclass
class Placement:
    id: str
    old: int
    new: int
    length: int
    mode: str              # same | in-place | relocated | pooled | field | group
    pointers: list[int] = field(default_factory=list)
    kept_refs: list[int] = field(default_factory=list)   # classified words retain their original value


@dataclass
class BuildResult:
    placements: list[Placement] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    allowed: set[int] = field(default_factory=set)
    free_total: int = 0
    free_used: int = 0
    free_remaining: int = 0
    failures: list[str] = field(default_factory=list)
    space: FreeSpace | None = None   # the allocator, shared with the graphics injector
    computed: list[tuple[int, int]] = field(default_factory=list)   # (pointer, relocated target)
    table_bases: list[tuple[int, int]] = field(default_factory=list)   # (pool word, its value)
    text_prefixes: list[tuple[int, int]] = field(default_factory=list)
    held: dict[str, list[int]] = field(default_factory=dict)


def _reference_positions(e: dict) -> tuple[list[int], list[int], list[int]]:
    """Separate writable references, retained words and unresolved positions."""
    facts = {ref["at"]: ref["status"] for ref in e.get("reference_classification", [])}
    verified = {ref["at"] for ref in e["refs"]
                if ref["kind"] in VERIFIED_KINDS or ref["kind"].startswith("table:")}
    positions = {ref["at"] for ref in e["refs"]}
    states = {at: facts.get(at, "unclassified" if "reference_classification" in e or at not in verified
                           else "reference") for at in positions}
    return ([at for at in sorted(positions) if states[at] == "reference"],
            [at for at in sorted(positions) if states[at] in ("unread", "non-reference")],
            [at for at in sorted(positions) if states[at] not in ("reference", "unread", "non-reference")])


def _hold(res: BuildResult, sid: str, positions: list[int]) -> None:
    res.held[sid] = positions
    res.skipped.append((sid, "unclassified references: " + ", ".join(hex(at) for at in positions)))


def _fits_in_place(base: Rom, e: dict, blob: bytes) -> bool:
    if bytes(base.data[e["offset"]:e["offset"] + len(blob)]) == blob:
        return True
    return len(blob) <= e["length"] and "overlap" not in e["flags"]


def field_bytes(blob: bytes, length: int, pad: int = 0x00) -> bytes:
    """The bytes of an unterminated field."""
    return blob[:-1].ljust(length, bytes([pad]))


def _computed_groups(base: Rom, by_id: dict, texts: dict[str, bytes], groups: list[dict],
                     res: BuildResult) -> list[tuple[int, list[dict]]]:
    """The computed groups some member of which outgrows its slot."""
    out, seen = [], set()
    for g in groups:
        at, ids = g["pointer"], g["ids"]
        if not set(ids) & set(texts):
            continue   # nothing translated, nothing moves
        members = [by_id.get(sid) for sid in ids]
        anchor = base.u32(at) - 0x08000000
        if (None in members or any(e["kind"] != "pointer" for e in members)
                or anchor not in {e["offset"] for e in members} or seen & set(ids)):
            res.failures.append(f"computed group at {at:#x}: {', '.join(ids)} must be pointer strings "
                                f"of this group only, one of them at {anchor:#x}")
            continue
        seen |= set(ids)
        if all(_fits_in_place(base, e, texts[e["id"]]) for e in members if e["id"] in texts):
            continue
        out.append((at, sorted(members, key=lambda e: e["offset"])))
    return out


def _follow_copies(inv: dict, texts: dict[str, bytes]) -> dict:
    """The inventory with untranslated copy entries' references back on their original."""
    idle = [e for e in inv["entries"] if "copy" in e.get("flags", ()) and e["id"] not in texts]
    if not idle:
        return inv
    from ..catalog.keys import copy_source
    back: dict[str, list[dict]] = {}
    facts: dict[str, list[dict]] = {}
    for e in idle:
        back.setdefault(copy_source(e["id"]), []).extend(e["refs"])
        facts.setdefault(copy_source(e["id"]), []).extend(e.get("reference_classification", []))
    gone = {e["id"] for e in idle}
    entries = [dict(e, refs=e["refs"] + back[e["id"]]) if e["id"] in back else e
               for e in inv["entries"] if e["id"] not in gone]
    for e in entries:
        if e["id"] in back and "reference_classification" in e:
            e["reference_classification"] = e["reference_classification"] + facts[e["id"]]
    return dict(inv, entries=entries)


def inject(rom: Rom, base: Rom, inv: dict, texts: dict[str, bytes], tables: dict[str, dict],
           reuse_slots: bool = False, min_run: int = 256,
           reserved: list[tuple[int, int]] | None = None,
           computed: list[dict] | None = None, table_bases: list[dict] | None = None,
           text_prefixes: list[dict] | None = None) -> BuildResult:
    """Write encoded translations (id -> bytes with terminator) into `rom`, relocating computed groups and table bases."""
    res = BuildResult()
    inv = _follow_copies(inv, texts)
    by_id = {e["id"]: e for e in inv["entries"]}
    extensions = {name: long_name_fields(table) for name, table in tables.items()}
    exclude = [(t["base"], t["base"] + t["stride"] * t["count"]) for t in tables.values()]
    exclude += [(e["offset"], e["offset"] + e["length"]) for e in inv["entries"]]
    space = FreeSpace.scan(bytes(base.data), min_run=min_run, exclude=exclude, reserved=reserved)
    res.free_total = space.total
    res.space = space
    pool: dict[bytes, int] = {}
    relocate: list[tuple[dict, bytes]] = []
    groups = _computed_groups(base, by_id, texts, computed or [], res)
    grouped = {e["id"] for _, members in groups for e in members}
    # tables the code indexes from a pooled base
    # - each pool word must hold the base, and keeps it
    # - a member is written in place or stays English
    held: dict[int, int] = {}   # member offset -> a pool word
    room: dict[int, int] = {}   # member offset -> declared field
    for tb in table_bases or []:
        problems = table_base_problems(base, tb)
        res.failures += problems
        if problems:
            continue
        for off in table_base_members(tb):
            held.setdefault(off, tb["pointers"][0])
        room.update(table_base_lengths(tb))
        res.table_bases += [(p, base.u32(p)) for p in tb["pointers"]]
    for sid in sorted(grouped):
        if by_id[sid]["offset"] in held:
            res.failures.append(f"{sid}: member of a computed group and of a table base")

    for sid, blob in sorted(texts.items(), key=lambda kv: by_id[kv[0]]["offset"] if kv[0] in by_id else 0):
        e = by_id.get(sid)
        if e is None:
            res.skipped.append((sid, "not in inventory"))
            continue
        if sid in grouped:
            continue   # placed with its group below
        old = e["offset"]
        if e["kind"] == "fixed":
            if len(blob) > e["length"]:
                rule = extensions.get(e.get("table"), {}).get(e.get("index"))
                if rule is None or len(blob) > rule["length"]:
                    limit = rule["length"] if rule else e["length"]
                    res.skipped.append((sid, f"field overflow {len(blob)} > {limit}"))
                    continue
                table = tables[e["table"]]
                expected = table["base"] + table["stride"] * e["index"] + table.get("field_offset", 0)
                if (old != expected or e["length"] != table["field_length"]
                        or base.ptr_at(old) is not None or not blob.endswith(b"\xff")):
                    res.failures.append(f"{sid}: invalid inline field for pointer conversion")
                    continue
                new = space.alloc(len(blob))
                if new is None:
                    res.skipped.append((sid, f"no free space for {len(blob)} bytes"))
                    continue
                if new + len(blob) > 0x02000000:
                    res.failures.append(f"{sid}: long-name pointer exceeds its ROM address range")
                    continue
                rom.write(new, blob)
                rom.write(old, ptr_bytes(new))
                res.allowed.update(range(new, new + len(blob)))
                res.allowed.update(range(old, old + 4))
                res.placements.append(Placement(sid, old, new, len(blob), "field-pointer", [old]))
                continue
            mode = "same" if bytes(base.data[old:old + len(blob)]) == blob else "field"
            rom.write(old, blob)
            res.allowed.update(range(old, old + len(blob)))
            res.placements.append(Placement(sid, old, old, len(blob), mode))
            continue
        if "copy" in e["flags"]:   # its own text, never the shared slot
            relocate.append((e, blob))
            continue
        if bytes(base.data[old:old + len(blob)]) == blob:
            res.placements.append(Placement(sid, old, old, len(blob), "same"))
            continue
        slot = room.get(old, e["length"])   # a declared field length wins
        if len(blob) <= slot and "overlap" not in e["flags"]:
            mode = "in-place"
            rom.write(old, blob)
            res.allowed.update(range(old, old + len(blob)))
            res.placements.append(Placement(sid, old, old, len(blob), mode))
            continue
        if old in held:   # the table keeps its layout
            res.skipped.append((sid, f"table base at {held[old]:#x}: {len(blob)} bytes, slot {slot}"))
            continue
        kinds = {r["kind"] for r in e["refs"]}
        if kinds & INPLACE_ONLY_KINDS:
            res.skipped.append((sid, f"in-place-only string too long ({len(blob)} > {e['length']})"))
            continue
        relocate.append((e, blob))

    # Computed groups move as one block.
    # Members keep their distance to the anchor.
    # Overlong translations stay English; gaps return to free space.
    for at, members in groups:
        unknown = sorted({pos for e in members for pos in _reference_positions(e)[2]})
        if unknown:
            for e in members:
                if e["id"] in texts:
                    _hold(res, e["id"], unknown)
            continue
        lo, anchor = members[0]["offset"], base.u32(at) - 0x08000000
        body, translated = {}, set()
        for e, nxt in zip(members, members[1:] + [None]):
            sid = e["id"]
            room = None if nxt is None else nxt["offset"] - e["offset"]
            blob = texts.get(sid)
            if blob is not None and room is not None and len(blob) > room:
                res.skipped.append((sid, f"computed group at {at:#x}: {len(blob)} bytes, "
                                         f"{room} before {nxt['id']}"))
                blob = None
            if blob is not None:
                translated.add(sid)
            body[sid] = blob if blob is not None else bytes(base.data[e["offset"]:e["offset"] + e["length"]])
            if room is not None and len(body[sid]) > room:   # members sharing bytes
                res.failures.append(f"computed group at {at:#x}: {sid} runs into {nxt['id']}")
        size = members[-1]["offset"] - lo + len(body[members[-1]["id"]])
        new = space.alloc(size)
        if new is None:
            res.skipped += [(sid, f"no free space for computed group at {at:#x} ({size} bytes)")
                            for sid in sorted(translated)]
            continue
        end = new
        for e in members:
            sid, blob, old = e["id"], body[e["id"]], e["offset"]
            pos = new + old - lo
            space.add(end, pos)
            rom.write(pos, blob)
            res.allowed.update(range(pos, pos + len(blob)))
            end = pos + len(blob)
            if sid not in translated:
                continue   # the English copy: its references stay
            vpos, others, _ = _reference_positions(e)
            vpos = [pos for pos in vpos if pos != at]
            others = [pos for pos in others if pos != at]
            ptrs = []
            for q in vpos:
                if base.u32(q) != 0x08000000 + old:
                    res.failures.append(f"{sid}: reference at {q:#x} does not hold {0x08000000 + old:#x}")
                    continue
                rom.write(q, ptr_bytes(pos))
                res.allowed.update(range(q, q + 4))
                ptrs.append(q)
            res.placements.append(Placement(sid, old, pos, len(blob), "group", ptrs, others))
        rom.write(at, ptr_bytes(new + anchor - lo))
        res.allowed.update(range(at, at + 4))
        res.computed.append((at, new + anchor - lo))

    # Longest first reduces free space fragmentation.
    # Copies allocate last, from the top.
    # Other byte placements remain unchanged.
    order = sorted(relocate, key=lambda x: ("copy" in x[0]["flags"], -len(x[1])))
    for e, blob in order:
        sid, old = e["id"], e["offset"]
        vpos, others, unknown = _reference_positions(e)
        if unknown:
            _hold(res, sid, unknown)
            continue
        if not vpos:
            res.skipped.append((sid, "no verified reference to rewrite"))
            continue
        if blob in pool:
            new, mode = pool[blob], "pooled"
        else:
            new = (space.alloc_top if "copy" in e["flags"] else space.alloc)(len(blob))
            if new is None:
                res.skipped.append((sid, f"no free space for {len(blob)} bytes"))
                continue
            rom.write(new, blob)
            res.allowed.update(range(new, new + len(blob)))
            pool[blob] = new
            mode = "relocated"
        ptrs = []
        for at in vpos:
            if base.u32(at) != 0x08000000 + old:
                res.failures.append(f"{sid}: reference at {at:#x} does not hold {0x08000000 + old:#x}")
                continue
            rom.write(at, ptr_bytes(new))
            res.allowed.update(range(at, at + 4))
            ptrs.append(at)
        res.placements.append(Placement(sid, old, new, len(blob), mode, ptrs, others))
        if reuse_slots and not others and "overlap" not in e["flags"]:
            space.add(old, old + e["length"])

    from .textprefix import apply as apply_prefixes
    try:
        res.text_prefixes = apply_prefixes(base, rom, text_prefixes or [], res.allowed)
        for pointer, _ in res.text_prefixes:
            res.allowed.update(range(pointer, pointer + 4))
    except ValueError as exc:
        res.failures.append(str(exc))
    res.free_used = space.used
    res.free_remaining = space.remaining
    return res


def diff_positions(a: bytes | bytearray, b: bytes | bytearray, block: int = 0x10000) -> list[int]:
    """Offsets where a and b differ (compared block by block)."""
    out = []
    for s in range(0, len(a), block):
        if a[s:s + block] != b[s:s + block]:
            out.extend(i for i in range(s, min(s + block, len(a))) if a[i] != b[i])
    return out


DIFF_RULES = ("exclusive", "shared", "allowed")


@dataclass
class DiffClass:
    """One diff-guard class: its label, written spans and overlap rule."""
    name: str
    spans: set[int] | frozenset[int] = field(default_factory=set)
    rule: str = "exclusive"


class DiffRegistry:
    """The diff-guard classes in build order."""

    def __init__(self):
        self.classes: list[DiffClass] = []

    def add(self, name: str, spans: set[int] | frozenset[int] = frozenset(),
            rule: str = "exclusive") -> None:
        if rule not in DIFF_RULES:
            raise ValueError(f"diff class rule must be one of {', '.join(DIFF_RULES)}")
        self.classes.append(DiffClass(name, set(spans), rule))


def verify(rom: Rom, base: Rom, res: BuildResult, texts: dict[str, bytes], extra_allowed: set[int],
           registry: DiffRegistry | None = None) -> list[str]:
    """Decode-back, pointer and diff-guard checks."""
    fails = list(res.failures)
    for p in res.placements:
        blob = texts[p.id]
        if bytes(rom.data[p.new:p.new + len(blob)]) != blob:
            fails.append(f"{p.id}: bytes at {p.new:#x} do not match the encoded translation")
        for at in p.pointers:
            if rom.u32(at) != 0x08000000 + p.new:
                fails.append(f"{p.id}: pointer at {at:#x} does not point to {p.new:#x}")
        for at in p.kept_refs:
            if rom.u32(at) != 0x08000000 + p.old:
                fails.append(f"{p.id}: retained word at {at:#x} was changed")
    for at, target in res.computed:
        if rom.u32(at) != 0x08000000 + target:
            fails.append(f"computed group: pointer at {at:#x} does not point to {target:#x}")
    for at, word in res.table_bases:
        if rom.u32(at) != word:
            fails.append(f"table base: pool word at {at:#x} no longer holds {word:#x}")
    for at, target in res.text_prefixes:
        if rom.u32(at) != 0x08000000 + target or rom.u8(target) != 255:
            fails.append(f"text prefix: pointer at {at:#x} does not select its terminator")
    allowed = res.allowed | extra_allowed
    prior: list[tuple[str, set[int]]] = [("text", res.allowed), ("font", extra_allowed)]
    for entry in (registry.classes if registry else ()):
        if entry.rule == "allowed":
            allowed |= entry.spans
            continue
        others = prior if entry.rule == "exclusive" else prior[1:]
        for name, other in others:
            clash = entry.spans & other
            if clash:
                fails.append(f"diff guard: {len(clash)} {entry.name} bytes are also {name} bytes, "
                             f"first at {min(clash):#x}")
        allowed |= entry.spans
        prior.append((entry.name, entry.spans))
    names = ["text", "font", "text-pointer"] + [e.name for e in (registry.classes if registry else ())]
    outside = [i for i in diff_positions(rom.data, base.data) if i not in allowed]
    if outside:
        fails.append(f"diff guard: {len(outside)} changed bytes outside "
                     f"{', '.join(names[:-1])} and {names[-1]} regions, "
                     f"first at {outside[0]:#x}")
    if bytes(rom.data[0xA0:0xC0]) != bytes(base.data[0xA0:0xC0]):
        fails.append("diff guard: ROM header changed")
    return fails
