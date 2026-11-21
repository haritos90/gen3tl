"""String discovery for Gen 3 ROMs: 0xFF-terminated runs a pointer word points at, references classified by kind."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from ..core.notes import has_prose
from ..core.charmap import BASE_CHARS, CTRL, CTRL_CODES, EOS, NAMED_BYTES, NEWLINE, PARAGRAPH, PLACEHOLDER, PREFIXED_ARG, SCROLL, Charmap, CharmapError
from ..core.rom import Rom

LETTERS = set(range(0xBB, 0xEF))
DIGITS = set(range(0xA1, 0xAB))
PUNCT = {0x00, 0xAB, 0xAC, 0xAD, 0xAE, 0xB0, 0xB1, 0xB2, 0xB3, 0xB4, 0xB5, 0xB6, 0xB7, 0xB8, 0xBA,
         0xF0, 0x2D, 0x5B, 0x5C, 0x5D, 0x36, 0x35, 0x1B, 0x53, 0x54, 0x34, 0xEF, 0x2E, 0xB9, 0x85, 0x86}
BREAKS = {NEWLINE, SCROLL, PARAGRAPH}
ARROWS = {0x79, 0x7A, 0x7B, 0x7C}
PLAIN = LETTERS | DIGITS | PUNCT | BREAKS

# reference kinds, by the bytes around the pointer:
# script:* names its command; pool, table, struct, table-base are data;
# aligned unverified references are identified, never rewritten.
VERIFIED_KINDS = {
    "script:loadpointer", "script:message", "script:bufferstring", "script:trainerbattle",
    "script:autoscroll", "script:vmessage", "script:setword", "script:chain", "pool", "table",
    "struct", "table-base",
}
# direct field text commands only:
# a chain may follow battle-script setword, so it is excluded
FIELD_TEXT_KINDS = {
    "script:loadpointer", "script:message", "script:bufferstring", "script:trainerbattle",
    "script:autoscroll", "script:vmessage",
}
# references identifying a string that are never rewritten
INPLACE_ONLY_KINDS = {"script:vmessage", "table-base"}

# trainerbattle: type -> text pointer offsets from opcode 0x5C
TRAINERBATTLE_TEXT_OFFSETS = {
    0: (6, 10), 1: (6, 10), 2: (6, 10), 3: (6,), 4: (6, 10, 14), 5: (6, 10),
    6: (6, 10, 14), 7: (6, 10, 14), 8: (6, 10, 14), 9: (6, 10),
}


@dataclass
class Ref:
    at: int
    kind: str


@dataclass
class Found:
    offset: int
    end: int  # offset just past the terminator
    refs: list[Ref] = field(default_factory=list)

    @property
    def length(self) -> int:
        return self.end - self.offset

    def verified_refs(self) -> list[Ref]:
        return [r for r in self.refs if r.kind in VERIFIED_KINDS]

    def unverified_refs(self) -> list[Ref]:
        return [r for r in self.refs if r.kind not in VERIFIED_KINDS]


def tokens(data: bytes, start: int, end: int):
    """Yield (pos, kind, byte) over data[start:end]; kind is char/ctrl/ph/break."""
    i = start
    while i < end:
        b = data[i]
        if b == CTRL:
            code = data[i + 1] if i + 1 < end else 0
            yield i, "ctrl", b
            i += 2 + CTRL_CODES.get(code, ("", 0))[1]
        elif b == PLACEHOLDER or b in PREFIXED_ARG:
            yield i, "ph", b
            i += 2
        elif b in BREAKS:
            yield i, "break", b
            i += 1
        else:
            yield i, "char", b
            i += 1


def text_quality(data: bytes, start: int, end: int, scripted: bool = False) -> tuple[float, int, int]:
    """(share of plain characters among characters, letters, characters)."""
    chars = plain = letters = 0
    for _, kind, b in tokens(data, start, end):
        if kind != "char":
            continue
        chars += 1
        if b in PLAIN or (scripted and b in ARROWS):
            plain += 1
        if b in LETTERS:
            letters += 1
    return (plain / chars if chars else 0.0), letters, chars


ASSIGNED = set(BASE_CHARS) | set(NAMED_BYTES)


# valid first bytes: letters, digits, space, punctuation
START_OK = LETTERS | DIGITS | {0x00, 0xB1, 0xB3, 0xB0, 0x5C, 0xAE, 0xAB, 0xAC, 0x51, 0x52, 0xB5, 0xB6,
                               0xAD, 0xB8, 0xBA, 0x2D, 0x2E, 0x5B, 0xB7, 0x53, 0x34, 0xEF, 0x1B, 0xB4}


def is_text(data: bytes, start: int, end: int, scripted: bool = False) -> bool:
    # scripted: a script command points here; checks skipped:
    # short words, letterless first lines, digit-heavy text, centred captions;
    # unassigned bytes reject the run as bytecode or data
    first = True
    seen_letter = False
    for _, kind, b in tokens(data, start, end):
        if kind == "char" and b not in ASSIGNED:
            return False
        if first:
            first = False
            if kind == "char" and b not in START_OK and not (scripted and b in ARROWS):
                return False  # movement data, script tail or other binary prefix
        if kind == "char" and (b in LETTERS or b in DIGITS):
            seen_letter = True
        if kind == "ph" and b == PLACEHOLDER:
            seen_letter = True  # a placeholder stands for a word: «{PLAYER}!\n…»
        if kind == "break" and not seen_letter and not scripted:
            return False  # line break before a letter: movement data ends 0xFE
    q, letters, chars = text_quality(data, start, end, scripted=scripted)
    if chars == 0 or letters == 0:
        return False
    if q < 0.9:
        return False
    spaces = sum(1 for _, kind, b in tokens(data, start, end) if kind == "char" and b == 0x00)
    if not scripted and (spaces > 0.5 * chars or letters < 0.3 * (chars - spaces)):
        return False
    raw = bytes(data[start:end])
    if b"\x00" * 17 in raw:
        return False
    if scripted:
        return True
    # at least two letters in a row somewhere
    return re.search(rb"[\xbb-\xee]{2}", raw) is not None


def stray_prefix(data: bytes, t: int) -> bool:
    """True when a single text byte sits between the previous terminator or padding and t."""
    a = segment_start(data, t)
    n, reached = _text_run_before(data, a, t)
    return reached and n <= 1


def script_text_ref(data: bytes, positions: list[int]) -> bool:
    """True when one of the pointer positions is the operand of a script text command."""
    for q in positions:
        if q >= 2 and ((data[q - 2] == 0x0F and data[q - 1] <= 3) or (data[q - 2] == 0x85 and data[q - 1] <= 7)):
            return True
        if q >= 1 and data[q - 1] in (0x67, 0xBD):
            return True
    return False


def pointer_index(rom: Rom) -> dict[int, list[int]]:
    """target offset -> positions of 32-bit values pointing at it (any alignment)."""
    d = rom.data
    idx: dict[int, list[int]] = defaultdict(list)
    size = len(d)
    for m in re.finditer(rb"[\x08\x09]", d):
        q = m.start() - 3
        if q < 0:
            continue
        v = int.from_bytes(d[q:q + 4], "little") - 0x08000000
        if 0 < v < size:
            idx[v].append(q)
    return idx


def segment_start(data: bytes, t: int, lookback: int = 4096) -> int:
    """Offset after the nearest 0xFF before t (or t - lookback)."""
    lo = max(0, t - lookback)
    k = data.rfind(b"\xff", lo, t)
    return k + 1 if k >= 0 else lo


def is_string_start(data: bytes, t: int) -> bool:
    """True when nothing but binary data or padding precedes t in its segment."""
    a = segment_start(data, t)
    if a == t:
        return True
    if data[t - 1] in (0xFC, 0xFD, 0xF7, 0xF8, 0xF9) or (t - 2 >= a and data[t - 2] == 0xFC):
        return False
    j = t - 1
    while j >= a and data[j] == 0x00:
        j -= 1
    if j < a or (t - 1 - j) > 2:
        return True  # padding
    n, reached = _text_run_before(data, a, j + 1)
    if reached:
        return False  # text all the way back to the previous terminator
    return n < 3


def _text_run_before(data: bytes, a: int, t: int, cap: int = 8) -> tuple[int, bool]:
    """(text tokens directly before t, whether the run reaches back to a)."""
    i = t - 1
    n = 0
    while i >= a and n < cap:
        if i - 1 >= a and data[i - 1] in (0xFD, 0xF7, 0xF8, 0xF9):
            i -= 2
        elif i - 1 >= a and data[i - 1] == 0xFC:
            i -= 2
        elif i - 2 >= a and data[i - 2] == 0xFC:
            i -= 3
        elif i - 3 >= a and data[i - 3] == 0xFC:
            i -= 4
        elif data[i] in PLAIN:
            i -= 1
        else:
            return n, False
        n += 1
    return n, i < a


def thumb_pool_loads(rom: Rom, word: int, reach: int = 1024) -> bool:
    """True when a THUMB PC-relative LDR within `reach` bytes before loads `word`."""
    d = rom.data
    lo = max(0, word - reach)
    for a in range(word - 2, lo - 1, -2):
        h = d[a] | (d[a + 1] << 8)
        if (h & 0xF800) == 0x4800 and ((a + 4) & ~3) + (h & 0xFF) * 4 == word:
            return True
    return False


def table_base_members(tb: dict) -> list[int]:
    """Member offsets of a [[strings.table_base]] declaration."""
    base = int(tb["id"][1:], 16)
    if "offsets" in tb:
        rel = list(tb["offsets"])
    else:
        rel = [tb["stride"] * i + f for i in range(tb["count"]) for f in tb.get("fields", [0])]
    return [base + k for k in rel]


def table_base_lengths(tb: dict) -> dict[int, int]:
    """Declared field lengths of a [[strings.table_base]]."""
    if "length" not in tb:
        return {}
    rel = list(tb["offsets"]) if "offsets" in tb else list(tb.get("fields", [0]))
    n = tb["length"]
    lens = list(n) if isinstance(n, list) else [n] * len(rel)
    base = int(tb["id"][1:], 16)
    if "offsets" in tb:
        return {base + k: m for k, m in zip(rel, lens)}
    return {base + tb["stride"] * i + f: m for i in range(tb["count"]) for f, m in zip(rel, lens)}


def table_base_problems(rom: Rom, tb: dict) -> list[str]:
    """What is wrong with a [[strings.table_base]] declaration or its declared field length."""
    sid = tb.get("id", "?")
    out = []
    if not has_prose(tb.get("evidence")):
        out.append(f"[[strings.table_base]] {sid}: no evidence")
    if not tb.get("pointers"):
        out.append(f"[[strings.table_base]] {sid}: no pool words")
    base = int(sid[1:], 16)
    if base not in table_base_members(tb):
        out.append(f"[[strings.table_base]] {sid}: the base is not a member")
    for p in tb.get("pointers", []):
        if rom.u32(p) != 0x08000000 + base:
            out.append(f"[[strings.table_base]] {sid}: the word at {p:#x} does not hold {0x08000000 + base:#x}")
    if "length" not in tb:
        return out
    # the declared field length
    rel = list(tb["offsets"]) if "offsets" in tb else list(tb.get("fields", [0]))
    n = tb["length"]
    lens = list(n) if isinstance(n, list) else [n]
    if not has_prose(tb.get("length_evidence")):
        out.append(f"[[strings.table_base]] {sid}: no evidence for the field length")
    if isinstance(n, list) and len(lens) != len(rel):
        what = "offsets" if "offsets" in tb else "fields"
        out.append(f"[[strings.table_base]] {sid}: {len(lens)} field lengths for {len(rel)} {what}")
        return out
    if any(not isinstance(m, int) or m < 1 for m in lens):
        out.append(f"[[strings.table_base]] {sid}: a field length is not a positive number")
        return out
    fields = table_base_lengths(tb)
    ends = set(fields)
    if "offsets" not in tb:   # a field ends with its record
        ends |= {base + tb["stride"] * i for i in range(1, tb["count"] + 1)}
    ends = sorted(ends)
    for off in sorted(fields):
        m = fields[off]
        if EOS not in rom.data[off:off + m]:
            out.append(f"[[strings.table_base]] {sid}: the English at {off:#x} does not end "
                       f"within its {m}-byte field")
        nxt = next((x for x in ends if x > off), None)
        if nxt is not None and off + m > nxt:
            out.append(f"[[strings.table_base]] {sid}: the {m}-byte field at {off:#x} runs into {nxt:#x}")
    return out


def classify_refs(rom: Rom, idx: dict[int, list[int]], starts: set[int],
                  trainerbattle_offsets: dict[int, tuple[int, ...]] | None = None) -> dict[int, list[Ref]]:
    d = rom.data
    out: dict[int, list[Ref]] = {}
    tb = dict(TRAINERBATTLE_TEXT_OFFSETS)
    tb.update(trainerbattle_offsets or {})
    tb_pairs = sorted({(k, typ) for typ, offs in tb.items() for k in offs})

    def trainerbattle_at(q: int) -> bool:
        return any(q >= k and d[q - k] == 0x5C and d[q - k + 1] == typ for k, typ in tb_pairs)

    def is_ptr(q: int) -> bool:
        return 0 <= q and rom.ptr_at(q) is not None

    for t in starts:
        refs = []
        for q in idx.get(t, []):
            kind = "unverified"
            if q % 4 == 0:
                if thumb_pool_loads(rom, q):
                    kind = "pool"
                elif is_ptr(q - 4) or is_ptr(q + 4):
                    kind = "table"
                else:
                    kind = "aligned"
            if q >= 2 and d[q - 2] == 0x0F and d[q - 1] <= 3:
                kind = "script:loadpointer"
            elif q >= 1 and d[q - 1] == 0x67:
                kind = "script:message"
            elif q >= 2 and d[q - 2] == 0x85 and d[q - 1] <= 10:  # buffers 4-10: engine extension
                kind = "script:bufferstring"
            elif trainerbattle_at(q):
                kind = "script:trainerbattle"
            elif q >= 1 and d[q - 1] == 0x9B:
                kind = "script:autoscroll"
            elif q >= 1 and d[q - 1] == 0xBD:
                kind = "script:vmessage"
            elif q >= 5 and d[q - 5] == 0xFA and d[q - 1] == 0x02 and d[q - 2] == 0x03:
                kind = "script:setword"
            refs.append(Ref(q, kind))
        out[t] = refs
    # structs: aligned word, sibling text pointer at fixed stride
    def text_ptr(q: int) -> bool:
        t2 = rom.ptr_at(q) if q >= 0 else None
        return t2 is not None and t2 in starts

    for t, refs in out.items():
        for r in refs:
            if r.kind != "aligned":
                continue
            for stride in range(8, 68, 4):
                if text_ptr(r.at - stride) or text_ptr(r.at + stride):
                    r.kind = "struct"
                    break
    # chains: unaligned unverified ref after a verified script ref
    by_pos = {r.at: (t, r) for t, refs in out.items() for r in refs}
    changed = True
    while changed:
        changed = False
        for t, refs in out.items():
            for r in refs:
                if r.kind != "unverified":
                    continue
                prev = by_pos.get(r.at - 4)
                if prev and prev[1].kind.startswith("script:"):
                    r.kind = "script:chain"
                    changed = True
    return out


def discover(rom: Rom, charmap: Charmap | None = None, exclude: list[tuple[int, int]] | None = None,
             trainerbattle_offsets: dict[int, tuple[int, ...]] | None = None):
    """Find string starts with references. Returns (strings, rejected_mid, pointer index)."""
    cm = charmap or Charmap()
    d = rom.data
    idx = pointer_index(rom)
    exclude = exclude or []
    starts: set[int] = set()
    rejected_mid = 0
    ends: dict[int, int] = {}
    for t in idx:
        if any(lo <= t < hi for lo, hi in exclude):
            continue
        e = d.find(b"\xff", t, t + 4000)
        if e < 0:
            continue
        try:
            _, end = cm.decode(d, t, limit=e - t + 1)
        except CharmapError:
            continue
        # a scripted target may be a short word ("s");
        # or follow a stray byte after padding;
        # or start without letters and hold more digits than letters
        scripted = script_text_ref(d, idx[t])
        if not is_text(d, t, end - 1, scripted=scripted):
            continue
        if not is_string_start(d, t) and not (scripted and stray_prefix(d, t)):
            rejected_mid += 1
            continue
        starts.add(t)
        ends[t] = end
    refs = classify_refs(rom, idx, starts, trainerbattle_offsets)
    found = [Found(t, ends[t], refs[t]) for t in sorted(starts)]
    return found, rejected_mid, idx
