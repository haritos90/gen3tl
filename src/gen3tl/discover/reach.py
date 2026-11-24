"""Classify script text beyond the map walk."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from ..core.notes import has_prose
from ..catalog.catalog import observed_entries, pointer_runs
from .maps import CMD_LEN, END_OPS, TEXT_PTR_AT, Walker
from ..core.rom import ROM_BASE, Rom, pointer_at
from .scan import TRAINERBATTLE_TEXT_OFFSETS, pointer_index, table_base_members

SCRIPT_REACH_DESCRIPTION = """Reachability of the script text no map walk reaches.

The context build (maps.Walker) starts at the map headers and follows call, goto,
goto_if, call_if and the trainerbattle continue scripts. The catalog's script group
holds the strings of its script files, many of which that walk never meets. This
study asks, for each of them, whether the game can still run a script that shows it.
The catalog's system strings are classed by the code that reads their tables and
literal pools (DataStudy). A literal pool
counts only where reached THUMB code loads it (Thumb); the profile's declared
code references ([[strings.computed]], [[strings.interior]], [[strings.table_base]])
make their strings reachable where reached code loads their pool word.

Method:

1. Commands. Every script reference of a string (build/inventory.json refs) gives the
   command holding the pointer: loadpointer 0F bb <ptr>, bufferstring 85 bb <ptr>,
   message 67, vmessage BD, messageautoscroll 9B, trainerbattle 5C tt. Two kinds of
   reference are no command: one inside a known data table (the records of the
   profile's [[table]] and [[strings.pointer_table]] entries, or in step with a run of 8
   or more table pointers of the catalog, which the profile's [progress] topics name),
   and an aligned word a THUMB ldr loads where the command decodes from no boundary
   (step 2). The string is classed by that table or by the code.
2. Script start walk. Straight-line decoding with the command lengths of maps.CMD_LEN
   and the profile ([script] command_lengths, trainerbattle_text_offsets) stops at an
   ending command (end, return, goto, gotostd, ...), an unknown opcode, the bytes of a
   catalogued string, movement data (movement_spans) and the trainer parties of the
   trainer table. The chain of a command is every position within WINDOW bytes before
   it from which decoding reaches it. A boundary is a chain position right after padding or a terminator (0xFF, 0xFE,
   filler 0xAA), end, return, goto, gotonative or vgoto; the entry is the lowest
   boundary, and the starts are the command positions decoded from the boundaries. A
   chain without a boundary is an ambiguous start: every chain position counts.
3. Pointer scan. Every 4-byte value 0x08000000-0x09FFFFFF (scan.pointer_index) that
   points at a start is placed by where it sits, in this order:
   - inside a catalogued string, or a map header or events field that points at map
     data: ignored;
   - an object, trigger, sign or map script field of a map header the context build
     walks ("map") or of one found by structure that it does not walk ("map-unwalked");
   - the operand of a command a start walk decodes: a script operand (call, goto,
     goto_if, call_if, loadpointer, trainerbattle continue script, setvaddress and the
     relative branches of mystery event scripts) makes that command a node in turn; a
     data operand (applymovement, message, mart lists) is ignored; where readings
     overlap, the earliest command holds the pointer;
   - a word a THUMB ldr loads ("pool"), unless the instruction lies inside a run of
     aligned ROM pointers, including either edge of the run; the ldr must lie in code
     that something reaches (Thumb), else the word counts only as the table
     data it may be, and a lone word is ignored ("dead-pool");
   - the script of a map script table entry {variable, value, script} outside the map
     headers found: "map-table" when a pointer reaches the table, through a map script
     list something points at; otherwise the table is part of the unreached set
     ("map-orphan", ignored);
   - a word beside another ROM pointer ("table") or in records of a fixed stride with
     ROM pointers ("record"); it counts when a chain of pointers from a literal pool,
     through one table at most, leads to the table's start; "table-unreferenced" when
     only other pointers reach the start, and "table-orphan" (ignored, part of the
     unreached set) when none does; inside a known table, that table's reference
     decides ("known-table");
   - the operand of a command that decodes from no boundary (a node with an ambiguous
     start); an unaligned window that shares bytes with an aligned ROM pointer: ignored;
     an aligned or unaligned word otherwise ("aligned", "unaligned"). Such an unplaced
     pointer counts only when it points at a boundary: script data points at a
     script's entry, its labels are reached by the script's own branches.
4. Classes. Reachable: some command is reached from a map (a map root, or a command
   the map walk decodes with the profile's command lengths: "map-walk"), a literal pool
   or a referenced table, or through the script operands of reached commands,
   transitively; or the string has such a pointer of its own. Possibly reachable: not
   reachable, and some command or the string has weak evidence (map-unwalked,
   map-table, table-unreferenced, aligned, unaligned, ambiguous start), directly or
   through script operands. Unreachable: neither; every pointer to its scripts comes
   from scripts or data that nothing reaches themselves.

Limits: a script reached through a computed address (a base plus an offset, a relative
vgoto, a table indexed far from its start) or through a pointer the game builds in RAM
is not seen; a table whose start lies more than 0x1000 bytes of null entries or two
strides from its first pointer has no root and may be taken for an orphan; the limits
of Thumb apply to the code that loads a pool word; a map header counts as walked when
the context build walks it, and no code reference to the bank table it reads is known,
so map-unwalked headers stay weak evidence; a command length of an engine extension the
profile does not list cuts the start walk (an ambiguous start, or an entry after the
real start that hides the pointers to it); a coincidental word in binary data that
points at a script entry makes a string possibly reachable.
"""

SYSTEM_REACH_DESCRIPTION = """Reachability of the system strings: strings that code, data tables and
records point at.

Method:

1. Pointers. Every 4-byte value that points at the string (scan.pointer_index) is
   placed: inside a catalogued string, or an unaligned window that shares bytes with an
   aligned ROM pointer: ignored; an aligned word a THUMB ldr loads ("pool"): code reads
   it, unless the ldr lies in an aligned pointer run, or in a ROM pointer with pointers
   on both sides at its record stride (table data that decodes as an ldr); an ldr in
   code nothing reaches (Thumb) leaves the word to a reached table, else
   "dead-pool" (ignored), as for a word whose ldr and a neighbour's sit within 64 bytes
   before them (a literal pool, no table); a field of
   the GBA or Game Freak ROM header (below 0x200): read only where a pool word points at
   that field ("rom-header"), the header otherwise serves external software; the text
   operand of a script command (maps.TEXT_PTR_AT, any buffer) where no ROM pointer
   precedes it ("script"): classed by the script study's graph for that command; any
   other unaligned word, or an aligned word with no ROM pointer at any stride beside
   it: weak ("unaligned", "aligned"); otherwise an entry of a table.
2. Tables. The stride of the entry is the one with the most ROM pointers in step with
   it (4 to 64 bytes, up to 64 records each way, null words passed); the table runs
   from the entry back and forth over entries holding a ROM pointer or null (one odd
   entry passed), its base the lowest entry. Code reads a table through a pointer to a
   word in [base - max(2 strides, 64), base + 2 strides] (its header, base, a field of
   its first record, or an index from 1), to a word inside it up to the entry (a part
   of the table), or through a section anchor: a pool word up to 127 bytes before the
   base where an instruction near the ldr adds or loads at the offset to the table
   (gcc's Thumb-1 anchors), weak where only a mov, cmp, sub or store holds that
   immediate (a copy of the object before the table, say). Pointers to text (into a catalogued
   string, or from a field whose neighbours at its stride mostly point at text, to a
   word before the table's first pointer) and unaligned targets are no table pointers.
   A pointer from inside the table counts as its own position does. A table the profile
   declares ([[reach.table]]) has its declared start: pointers count to that
   base, one record before it and into the records, no header or anchor. Each pointer to the
   table is placed as in step 1, a table pointer in turn up to 6 tables deep; at
   other candidate strides of the entry only a code root counts.
3. Classes. Reachable: some pointer leads to code (pool, rom-header, anchor) or to a
   script the script study reaches. Possibly reachable: not reachable, and some
   pointer leads to weak evidence: an unaligned or lone aligned word pointing at a
   table's start or at the string, a script with weak evidence, a chain deeper than 6
   tables or a cycle of tables. Unreachable: every pointer is ignored or lies in a table
   that nothing but other unreached data points at ("table-orphan").

Limits: a table reached through a computed address that no immediate near a pool load
gives (a base far before it, an offset built in registers), through a pointer built in
RAM, or through a section anchor farther than 127 bytes is not seen; the limits of
Thumb apply to the code that loads a pool word, and a coincidental word in binary data
that points at a table start or a string makes it possibly reachable; tables merged by the
walk (a literal pool before a table, two adjacent tables) share their pointers, which
errs towards reachable; a string the game copies or decompresses before showing is
classed by the pointers to its ROM bytes."""

WINDOW = 0x3000                          # how far the start walk looks back from a command
CLASSES = ("reachable", "possibly reachable", "unreachable")

# text reference kind -> pointer offset from the opcode
TEXT_REF_AT = {"script:loadpointer": 2, "script:bufferstring": 2, "script:message": 1,
               "script:vmessage": 1, "script:autoscroll": 1}
# script pointer operands: opcode -> (operand offset, name);
# goto_if and call_if take a condition; loadpointer takes a bank;
# setvaddress and the v-branches stay inside their script.
SCRIPT_OPERAND = {0x04: (1, "call"), 0x05: (1, "goto"), 0x06: (2, "goto_if"),
                  0x07: (2, "call_if"), 0x0F: (2, "loadpointer"), 0xB8: (1, "setvaddress"),
                  0xB9: (1, "vgoto"), 0xBA: (1, "vcall"), 0xBB: (2, "vgoto_if"), 0xBC: (2, "vcall_if")}
TB_CONTINUE = (1, 2, 6, 8)   # trainerbattle types with a continue script after the texts
# commands whose pointer operand is data: opcode -> operand offset
DATA_OPERAND = {0x4F: 3, 0x50: 3, 0x67: 1, 0x9B: 1, 0xBD: 1, 0x85: 2, 0x86: 1, 0x87: 1, 0x88: 1}
# ending commands a boundary follows: end, return, goto, gotonative, vgoto;
# gotostd, returnram and endram are left out:
# their bytes are common operand values.
BOUNDARY_ENDS = {0x02: 1, 0x03: 1, 0x05: 5, 0x24: 5, 0xB9: 5}
EDGE_KINDS = {name for _, name in SCRIPT_OPERAND.values()} | {"trainerbattle"}
STRONG = {"map", "map-walk", "pool", "table", "record", "known-table"}
WEAK = {"map-unwalked", "map-table", "aligned", "unaligned", "table-unreferenced", "ambiguous-start"}
IGNORED = {"in-string", "data-operand", "straddle", "into-script", "map-data", "map-orphan", "table-orphan",
           "dead-pool"}


def origin(off: int, regions=(), default: str = "rom") -> str:
    """Classify an offset using the profile's named regions."""
    for item in regions:
        if item["start"] <= off < item["end"]:
            return item["name"]
    return default


def region(off: int) -> str:
    """The 64 KB region of an offset, as the catalog's region files name it."""
    return f"0x{off >> 16:03X}0000"


@dataclass
class Start:
    command: int
    entry: int              # lowest boundary; the lowest chain position when ambiguous
    starts: list[int]       # positions from which straight-line flow reaches the command
    ambiguous: bool


class Code:
    """Straight-line decoding of FRLG script bytecode."""

    def __init__(self, data, cmd_len: dict[int, int] | None = None,
                 trainerbattle: dict[int, tuple[int, ...]] | None = None,
                 not_code: bytearray | None = None, window: int = WINDOW):
        self.d = data
        self.cmd_len = dict(CMD_LEN) if cmd_len is None else cmd_len
        self.tb = dict(TRAINERBATTLE_TEXT_OFFSETS) if trainerbattle is None else trainerbattle
        self.not_code = not_code
        self.window = window
        self.longest = max([*self.cmd_len.values()] + [max(o) + 8 for o in self.tb.values()])
        self._next: dict[int, int] = {}
        self._starts: dict[int, Start] = {}

    def length(self, pc: int) -> int | None:
        d = self.d
        if d[pc] == 0x5C:
            offs = self.tb.get(d[pc + 1]) if pc + 1 < len(d) else None
            if offs is None:
                return None
            return max(offs) + 4 + (4 if d[pc + 1] in TB_CONTINUE else 0)
        return self.cmd_len.get(d[pc])

    def next(self, pc: int) -> int:
        """The command after the one at pc in straight-line flow; -1 where flow stops."""
        r = self._next.get(pc)
        if r is None:
            n = None if self.not_code is not None and self.not_code[pc] else self.length(pc)
            r = -1 if n is None or self.d[pc] in END_OPS else pc + n
            self._next[pc] = r
        return r

    def chain(self, c: int) -> set[int]:
        """Every position within the window before c from which decoding reaches c."""
        reach = {c}
        todo = [c]
        lo = max(0, c - self.window)
        while todo:
            x = todo.pop()
            for p in range(max(lo, x - self.longest), x):
                if p not in reach and self.next(p) == x:
                    reach.add(p)
                    todo.append(p)
        return reach

    def ends_at(self, t: int) -> bool:
        """True when padding, a terminator or an ending command ends right before t."""
        d = self.d
        if t == 0 or d[t - 1] in (0xFF, 0xFE):
            return True
        if d[t - 1] == 0xAA and t >= 2 and (d[t - 2] in (0xAA, 0x00) or self.ends_at(t - 1)):
            return True
        for op, n in BOUNDARY_ENDS.items():
            if t - n >= 0 and d[t - n] == op and (n == 1 or op == 0xB9 or d[t - 1] in (0x08, 0x09)):
                return True
        return False

    def starts(self, c: int) -> Start:
        st = self._starts.get(c)
        if st is None:
            st = self._starts[c] = self._find_starts(c)
        return st

    def _find_starts(self, c: int) -> Start:
        chain = self.chain(c)
        bounds = sorted(b for b in chain if self.ends_at(b))
        if not bounds:
            return Start(c, min(chain), sorted(chain), True)
        pos: set[int] = set()
        for b in bounds:
            p = b
            while p != c and p not in pos:
                pos.add(p)
                p = self.next(p)
        pos.add(c)
        return Start(c, bounds[0], sorted(pos), False)


def _in_pointer_run(data, position: int) -> bool:
    """Whether an aligned pointer has another beside it."""
    def pointer(at):
        return pointer_at(data, at) is not None

    word = position & ~3
    return pointer(word) and (pointer(word - 4) or pointer(word + 4))


def pool_load(data, word: int, reach: int = 1024) -> int | None:
    """Position of the nearest THUMB `ldr rX, [pc, #imm]` within `reach` bytes before `word` that loads it, or None."""
    for a in range(word - 2, max(0, word - reach) - 1, -2):
        h = data[a] | (data[a + 1] << 8)
        if (h & 0xF800) == 0x4800 and ((a + 4) & ~3) + (h & 0xFF) * 4 == word:
            if not _in_pointer_run(data, a):
                return a
    return None


CASES = 256          # jump table entries read after mov pc
CASE_SPAN = 0x10000  # a case lies this close to its switch
# gcc __gnu_thumb1_case_* helpers: load after push, mov r1, lr;
# -> entry size, signed (uqi, sqi, uhi, shi, si)
CASE_LOADS = {0x5C09: (1, False), 0x5609: (1, True), 0x5A09: (2, False), 0x5E09: (2, True),
              0x5808: (4, True)}
CALLASM = (0x23, 0xF8)   # field and CFRU battle script callasm


class Thumb:
    """THUMB code that something reaches."""

    def __init__(self, data, idx: dict[int, list[int]], not_code: bytearray | None = None,
                 unused: set[int] | None = None):
        self.d = data
        self.nc = not_code if not_code is not None else bytearray(len(data))
        self.reached = bytearray(len(data))
        self._helpers: dict[int, tuple[int, bool] | None] = {}
        unused = unused or set()   # table entries nothing calls
        roots = [t - 1 for t, qs in idx.items() if t % 2 and not self.nc[t - 1]
                 and any(not self.nc[q] and q not in unused
                         and (q % 4 == 0 or self.pushes(t - 1, 8 if q and data[q - 1] in CALLASM else 3))
                         for q in qs)]
        self.walk(roots)

    def walk(self, todo: list[int]) -> None:
        d, nc, seen = self.d, self.nc, self.reached
        end = len(d) - 1
        while todo:
            pc = todo.pop()
            while 0 <= pc < end and pc % 2 == 0 and not seen[pc] and not nc[pc]:
                seen[pc] = 1
                h = d[pc] | d[pc + 1] << 8
                if h >> 11 == 0x1E:                    # bl: prefix and suffix
                    h2 = d[pc + 2] | d[pc + 3] << 8 if pc + 3 <= end else 0
                    if h2 >> 11 != 0x1F:
                        break
                    off = (h & 0x7FF) << 12 | (h2 & 0x7FF) << 1
                    t = pc + 4 + off - (off & 0x400000) * 2
                    todo.append(t)
                    seen[pc + 2] = 1
                    helper = self.case_helper(t)
                    if helper is not None:   # a table of offsets follows
                        todo += self.helper_cases(pc, *helper)
                        break
                    pc += 4
                elif h >> 11 == 0x1C:                  # b
                    off = (h & 0x7FF) << 1
                    pc += 4 + off - (off & 0x800) * 2
                elif h >> 12 == 0xD:                   # b<cond>, swi
                    cond = h >> 8 & 15
                    if cond == 0xE:
                        break
                    if cond != 0xF:
                        off = (h & 0xFF) << 1
                        todo.append(pc + 4 + off - (off & 0x100) * 2)
                    pc += 2
                elif h >> 10 == 0x11:                  # hi-register ops, bx
                    op = h >> 8 & 3
                    if op == 3:
                        break
                    if op != 1 and h & 0x87 == 0x87:   # add / mov pc: a jump table
                        todo += self.cases(pc)
                        break
                    pc += 2
                elif h >> 12 == 0xB:                   # sp, push, pop
                    if h >> 8 & 15 not in (0x0, 0x4, 0x5, 0xC):   # pop {pc}, undefined
                        break
                    pc += 2
                elif h >> 11 in (0x1D, 0x1F):          # a BL suffix alone
                    break
                else:
                    pc += 2

    def hw(self, a: int) -> int:
        return self.d[a] | self.d[a + 1] << 8 if 0 <= a < len(self.d) - 1 else 0

    def pushes(self, t: int, n: int = 3) -> bool:
        """Whether one of the first n instructions at t is a push."""
        return any(self.hw(t + 2 * k) >> 9 == 0x5A for k in range(n))

    def cases(self, pc: int) -> list[int]:
        """The targets of the jump table of the mov pc or add pc at pc."""
        out = []
        a = (pc + 5) & ~3
        for p in range(pc - 2, pc - 14, -2):
            h = self.hw(p)
            if h & 0xF800 == 0x4800:
                w = ((p + 4) & ~3) + (h & 0xFF) * 4
                v = int.from_bytes(self.d[w:w + 4], "little") - ROM_BASE
                if pc < v <= pc + 64 and v % 4 == 0:
                    a = v
                    break
        for _ in range(CASES):
            v = (int.from_bytes(self.d[a:a + 4], "little") - ROM_BASE) & ~1
            if not (0 <= v < len(self.d) and abs(v - pc) < CASE_SPAN and not self.nc[v]):
                break
            out.append(v)
            a += 4
        return out

    def case_helper(self, t: int) -> tuple[int, bool] | None:
        """(entry size, signed) when the BL target t is a gcc case helper."""
        if t not in self._helpers:
            found = None
            if self.hw(t) in (0xB402, 0xB403) and self.hw(t + 2) == 0x4671:
                found = next((CASE_LOADS[self.hw(t + k)] for k in range(4, 14, 2)
                              if self.hw(t + k) in CASE_LOADS), None)
            self._helpers[t] = found
        return self._helpers[t]

    def helper_cases(self, pc: int, size: int, signed: bool) -> list[int]:
        """The targets of the offset table after the BL at pc to a case helper."""
        n = next((self.hw(p) & 0xFF for p in range(pc - 2, pc - 18, -2) if self.hw(p) >> 11 == 5), None)
        base = (pc + 7) & ~3 if size == 4 else pc + 4
        out = []
        for i in range(n + 1 if n is not None else CASES):
            e = int.from_bytes(self.d[base + i * size:base + (i + 1) * size], "little", signed=signed)
            t = (base + e) & ~1 if size == 4 else base + 2 * e
            if n is None and not base + (i + 1) * size <= t < base + CASE_SPAN:
                break
            if 0 <= t < len(self.d) and not self.nc[t]:
                out.append(t)
        return out

    def load(self, word: int, reach: int = 1024) -> int | None:
        """A reached THUMB `ldr rX, [pc, #imm]` within `reach` bytes before `word` that loads it."""
        d = self.d
        for a in range(word - 2, max(0, word - reach) - 1, -2):
            h = d[a] | (d[a + 1] << 8)
            if (h & 0xF800) == 0x4800 and ((a + 4) & ~3) + (h & 0xFF) * 4 == word and self.reached[a]:
                return a
        return None


def movement_spans(data) -> dict[int, int]:
    """Movement data: start -> offset of its 0xFE terminator, for every applymovement pattern whose target holds movement bytes."""
    out: dict[int, int] = {}
    for m in re.finditer(rb"[\x4f\x50]", data):
        a = m.start()
        lid = data[a + 1] | data[a + 2] << 8 if a + 2 < len(data) else 0
        if not (lid in (0xFF, 0x7F) or 1 <= lid < 0x40):
            continue
        v = pointer_at(data, a + 3)
        if v is None or v in out:
            continue
        e = data.find(b"\xfe", v, v + 32)
        if e > v and all(x < 0xE1 for x in data[v:e]):
            out[v] = e
    return out


def ref_command(data, ref: dict, tb: dict[int, tuple[int, ...]]) -> int | None:
    """Position of the command that holds a script text reference."""
    at, kind = ref["at"], ref["kind"]
    if kind in TEXT_REF_AT:
        return at - TEXT_REF_AT[kind]
    if kind == "script:trainerbattle":
        for typ, offs in sorted(tb.items()):
            for k in offs:
                if at >= k and data[at - k] == 0x5C and data[at - k + 1] == typ:
                    return at - k
    return None


def map_event_fields(walker: Walker) -> dict[int, tuple[int, str]]:
    """Pointer position -> (map header, event kind) for the event and script fields of every map header found by structure."""
    d, ptr = walker.d, walker.ptr

    def layout_ok(lay):
        if lay is None or lay % 4:
            return False
        w = int.from_bytes(d[lay:lay + 4], "little")
        h = int.from_bytes(d[lay + 4:lay + 8], "little")
        return 1 <= w <= 512 and 1 <= h <= 512 and all(ptr(lay + 8 + 4 * k) is not None for k in range(4))

    def events_ok(ev):
        if ev is None or ev % 4:
            return False
        for k in range(4):
            v = int.from_bytes(d[ev + 4 + 4 * k:ev + 8 + 4 * k], "little")
            if (d[ev + k] or v) and ptr(ev + 4 + 4 * k) is None:
                return False
        return True

    heads = set(walker.walked)
    for m in re.finditer(rb"[\x08\x09]", d):
        q = m.start() - 3
        if q % 4 == 0 and layout_ok(ptr(q)) and events_ok(ptr(q + 4)) and (
                ptr(q + 8) is not None or d[q + 8:q + 12] == b"\0\0\0\0"):
            heads.add(q)
    out: dict[int, tuple[int, str]] = {}
    data: set[tuple[int, int]] = set()
    for h in sorted(heads):
        ev = ptr(h + 4)
        if ev is None:
            continue
        data.update((f, h) for f in (h, h + 4, h + 8, h + 12, ev + 4, ev + 8, ev + 12, ev + 16))
        n_obj, n_coord, n_bg = d[ev], d[ev + 2], d[ev + 3]
        objs, coords, bgs = ptr(ev + 4), ptr(ev + 12), ptr(ev + 16)
        if objs is not None:
            for i in range(n_obj):
                out.setdefault(objs + 24 * i + 16, (h, "npc"))
        if coords is not None:
            for i in range(n_coord):
                out.setdefault(coords + 16 * i + 12, (h, "trigger"))
        if bgs is not None:
            for i in range(n_bg):
                if d[bgs + 12 * i + 5] <= 4:
                    out.setdefault(bgs + 12 * i + 8, (h, "sign"))
        ms = ptr(h + 8)
        k = 0
        while ms is not None and k < 16 and d[ms + 5 * k] != 0:
            p = ptr(ms + 5 * k + 1)
            if p is not None:
                if d[ms + 5 * k] in (2, 4):
                    data.add((ms + 5 * k + 1, h))
                    j = 0
                    while int.from_bytes(d[p + 8 * j:p + 8 * j + 2], "little") != 0 and j < 32:
                        out.setdefault(p + 8 * j + 4, (h, "mapscript"))
                        j += 1
                else:
                    out.setdefault(ms + 5 * k + 1, (h, "mapscript"))
            k += 1
    for f, h in data:
        out.setdefault(f, (h, "data"))
    return out


class _VisitWalker(Walker):
    """The context build's map walk, recording the script roots and the commands it decodes."""

    def __init__(self, rom: Rom, prof):
        super().__init__(rom, prof)
        self.roots: dict[int, str] = {}
        self.visited: set[int] = set()

    def walk_script(self, start, mid, name, event, local_id, sprite):
        self.roots.setdefault(start, f"{mid} {event}")
        self.visited |= super().walk_script(start, mid, name, event, local_id, sprite)


@dataclass
class Table:
    """A known data table: records of `stride` bytes from `base`, or a run of pointers."""
    name: str
    base: int
    stride: int
    end: int                       # offset just past the last record (or pointer)
    source: str                    # profile table, pointer table or catalog run
    topic: str | None = None


def known_tables(prof, inv: dict) -> list[Table]:
    """The profile's tables and the table, struct or pool runs of the catalog, named by their [progress] topic."""
    topics = {f: t["topic"] for t in prof.get("progress", {}).get("topics", []) for f in t.get("files", [])}
    runs: dict[int, list[int]] = defaultdict(list)
    for at, start in pointer_runs(inv).items():
        runs[start].append(at)
    run_tables = []
    for start, pos in sorted(runs.items()):
        if len(pos) < 8:
            continue
        pos.sort()
        name = f"table-{start:07X}"
        run_tables.append(Table(name, start, pos[1] - pos[0], pos[-1] + 4, "catalog run", topics.get(name)))

    def topic_in(base: int, end: int) -> str | None:   # the topic of a catalog run inside the records
        return next((r.topic for r in run_tables if base <= r.base < end and r.topic), None)

    out = [Table(t["name"], t["base"], t["stride"], t["base"] + t["stride"] * t["count"], "profile table")
           for t in prof.get("table", [])]
    out += [Table(pt["name"], pt["base"], pt["stride"], pt["base"] + pt["stride"] * pt["count"], "pointer table",
                  topics.get(pt["name"])) for pt in inv.get("pointer_tables", [])]
    for t in out:
        t.topic = t.topic or topic_in(t.base, t.end)
    return out + run_tables


class Study:
    """Reachability classes of script strings over one ROM image."""

    def __init__(self, data, idx: dict[int, list[int]], code: Code, events: dict[int, tuple[int, str]] | None = None,
                 walked: set[int] | None = None, roots: dict[int, str] | None = None,
                 visited: set[int] | None = None, tables: list[Table] | None = None,
                 origins=(), default_origin: str = "rom", thumb: Thumb | None = None):
        self.d = data
        self.idx = idx
        self.code = code
        self.thumb = thumb   # None counts every pool
        self.events = events or {}
        self.walked = walked or set()
        self.roots = roots or {}
        self.visited = visited or set()
        self.tables = tables or []
        self.origins = origins
        self.default_origin = default_origin
        self.nodes: dict[int, dict] = {}
        self.edges: dict[int, list[tuple[int, dict]]] = defaultdict(list)
        self.level: dict[int, int] = {}
        self.why: dict[int, dict] = {}
        self._referenced: dict[tuple[int, int, int], dict | None] = {}

    def ptr(self, q: int) -> int | None:
        return pointer_at(self.d, q)

    def live_pool(self, q: int) -> bool:
        """Whether reached code loads the pool word q."""
        return self.thumb is None or self.thumb.load(q) is not None

    def place(self, q: int) -> tuple[str, int | None]:
        """(kind, source command) of the pointer at q; the source command for script operands."""
        d = self.d
        nc = self.code.not_code
        if nc is not None and nc[q]:
            return "in-string", None
        if q in self.events:
            h, ev = self.events[q]
            if ev == "data":
                return "map-data", None
            return ("map" if h in self.walked else "map-unwalked"), None
        aligned = q % 4 == 0
        # among overlapping commands, the earliest decoded one holds it
        cands = []
        for op, (k, name) in SCRIPT_OPERAND.items():
            if q >= k and d[q - k] == op and (k != 2 or op == 0x0F or d[q - 1] <= 5) and (op != 0x0F or d[q - 1] <= 3):
                cands.append((q - k, name))
        for typ in TB_CONTINUE:
            offs = self.code.tb.get(typ)
            k = max(offs) + 4 if offs else 0
            if offs and q >= k and d[q - k] == 0x5C and d[q - k + 1] == typ:
                cands.append((q - k, "trainerbattle"))
        for op, k in DATA_OPERAND.items():
            if q >= k and d[q - k] == op:
                cands.append((q - k, "data-operand"))
        decoded = sorted(c for c in cands if not self.code.starts(c[0]).ambiguous)
        if decoded:
            c, kind = decoded[0]
            return kind, c
        # a THUMB ldr loading the word marks a literal pool;
        # a decoded command wins: script bytes can read as ldr;
        # unreached loads or lookalike data: a table may hold it
        dead = aligned and pool_load(d, q) is not None
        if dead and self.live_pool(q):
            return "pool", None
        # no command decodes: a script entry, table word or operand
        mt = self.map_table(q)
        if mt is not None:
            return ("dead-pool" if dead and mt == "map-table" else mt), None
        if aligned:
            if self.ptr(q - 4) is not None or self.ptr(q + 4) is not None:
                return "table", None
            if self.record_stride(q):
                return "record", None
        if dead:
            return "dead-pool", None
        script = [c for c in cands if c[1] != "data-operand"]
        if script:   # undecoded operand, no data proof: "0C 50 05 00 <ptr>"
            c, kind = min(script)   # reads as a map script entry (var 0x500C) too
            return kind, c
        if not aligned and (self.ptr(q & ~3) is not None or self.ptr((q & ~3) + 4) is not None):
            return "straddle", None   # bytes of two aligned words, one a ROM pointer
        return ("aligned" if aligned else "unaligned"), None

    def map_table(self, q: int) -> str | None:
        """"map-table", "map-orphan" or None for the script of a map script table entry at q."""
        d, u16 = self.d, lambda o: d[o] | d[o + 1] << 8

        def var(o):   # a script variable: saved 0x4000-0x41FF, engine extension 0x5000-0x51FF, special 0x8000-0x801F
            v = u16(o)
            return 0x4000 <= v < 0x4200 or 0x5000 <= v < 0x5200 or 0x8000 <= v < 0x8020

        if q < 4 or not var(q - 4) or self.ptr(q - 4) is not None or not (
                u16(q + 4) == 0 or (var(q + 4) and self.ptr(q + 8) is not None)):
            return None
        ts = q - 4
        while ts >= 8 and var(ts - 8) and self.ptr(ts - 8) is None and self.ptr(ts - 4) is not None:
            ts -= 8
        for r in self.idx.get(ts, []):
            if not (r >= 1 and d[r - 1] in (2, 4)):
                return "map-table"
            ls = r - 1   # the map script list: 5-byte entries {type 1-7, pointer}
            while ls >= 5 and 1 <= d[ls - 5] <= 7 and self.ptr(ls - 4) is not None:
                ls -= 5
            if self.idx.get(ls):
                return "map-table"
        return "map-orphan"

    def table_root(self, q: int, stride: int, depth: int = 1) -> dict | None:
        """A chain of pointers ending in a literal pool that leads to the start of the table holding the pointer at q, or None."""
        first = start = p = q
        while q - p < 0x10000 and p >= stride:
            p -= stride
            if self.ptr(p) is not None:
                first = start = p
            elif self.d[p:p + 4] == b"\0\0\0\0" and first - p <= 0x1000:
                start = p
            else:
                break
        for at in dict.fromkeys((first, start)):
            root = self.root_in(max(0, at - 2 * stride), at + 2 * stride, depth)
            if root:
                return dict(root, first=first)
        return None

    def table_pointed(self, q: int, stride: int) -> bool:
        """True when any pointer reaches the start of the table holding the pointer at q."""
        first = start = p = q
        while q - p < 0x10000 and p >= stride:
            p -= stride
            if self.ptr(p) is not None:
                first = start = p
            elif self.d[p:p + 4] == b"\0\0\0\0" and first - p <= 0x1000:
                start = p
            else:
                break
        return any(self.idx.get(t) for at in (first, start) for t in range(max(0, at - 2 * stride), at + 2 * stride + 1))

    def root_in(self, lo: int, hi: int, depth: int = 0) -> dict | None:
        """The first pointer to an offset in [lo, hi] from a literal pool or, `depth` levels deep, from a table."""
        key = (lo, hi, depth)
        if key not in self._referenced:
            self._referenced[key] = None    # a table that leads back to itself has no root
            found = None
            for t in range(lo, hi + 1):
                for r in self.idx.get(t, []):
                    if r % 4:
                        continue
                    if pool_load(self.d, r) is not None:
                        if self.live_pool(r):
                            found = {"base": t, "at": r}
                    elif depth and (self.ptr(r - 4) is not None or self.ptr(r + 4) is not None
                                    or self.record_stride(r)):
                        via = self.table_root(r, self.stride_of(r), depth - 1)
                        if via:
                            found = {"base": t, "at": r, "via": via}
                    if found:
                        break
                if found:
                    break
            self._referenced[key] = found
        return self._referenced[key]

    def record_stride(self, q: int) -> int | None:
        """The stride of the records holding the aligned pointer at q."""
        p = self.ptr
        for s in range(8, 68, 4):
            if (p(q - s) is not None and p(q + s) is not None) or (p(q + s) is not None and p(q + 2 * s) is not None) \
                    or (p(q - s) is not None and p(q - 2 * s) is not None):
                return s
        return None

    def stride_of(self, q: int) -> int:
        """Entry stride of the table holding the aligned pointer at q."""
        if self.ptr(q - 4) is not None and self.ptr(q + 4) is not None:
            return 4
        return self.record_stride(q) or 4

    def evidence(self, q: int, target: int) -> dict:
        kind, src = self.place(q)
        item = {"kind": kind, "at": q, "to": target}
        if src is not None:
            item["from"] = src
        if kind in ("map", "map-unwalked"):
            h, ev = self.events[q]
            item["map"], item["event"] = h, ev
        if kind in ("table", "record", "aligned"):
            known = self.table_of(q)
            if known is not None:
                item.update(self.table_class(known), placed=kind)
            elif kind != "aligned":
                stride = self.stride_of(q)
                root = self.table_root(q, stride)
                if root is not None:
                    item["table"] = root
                elif self.table_pointed(q, stride):
                    item.update(kind="table-unreferenced", placed=kind)
                else:
                    item.update(kind="table-orphan", placed=kind)
        if item["kind"] in WEAK and not self.code.ends_at(target):
            # unplaced pointer into a script block, not its start:
            # data points at entries; branches reach labels
            item.update(kind="into-script", placed=item["kind"])
        return item

    # known tables (misread table fields)
    def table_of(self, at: int) -> Table | None:
        """The known table whose record field holds the pointer at `at`."""
        for t in self.tables:
            if t.source != "catalog run" and t.base <= at < t.end:
                return t
        for t in self.tables:
            if t.source != "catalog run" or (at - t.base) % t.stride:
                continue
            if t.base <= at < t.end:
                return t
            for j in range(1, 5):
                if at == t.base - j * t.stride or at == t.end - 4 + j * t.stride:
                    lo, hi = (at, t.base) if at < t.base else (t.end - 4, at)
                    if all(self.ptr(p) is not None for p in range(lo + t.stride, hi, t.stride)):
                        return t
        return None

    def table_class(self, t: Table) -> dict:
        """Evidence for a known table: "known-table" or "table-unreferenced"."""
        if t.source == "catalog run":
            root = self.table_root(t.base, t.stride)
        else:
            root = self.root_in(max(0, t.base - t.stride), t.base, 1)
        item = {"kind": "known-table" if root else "table-unreferenced", "table": t.name,
                "topic": t.topic, "source": t.source}
        if root:
            item["root"] = root
        return item

    def node(self, c: int) -> dict:
        n = self.nodes.get(c)
        if n is not None:
            return n
        st = self.code.starts(c)
        items: list[dict] = []
        n = {"start": st, "items": items, "ignored": []}   # ignored: pointers to data, a few kept
        self.nodes[c] = n
        on_walk = [p for p in st.starts if p in self.visited]
        if on_walk:
            items.append({"kind": "map-walk", "at": on_walk[0], "to": on_walk[0]})
        for t in st.starts:
            if t in self.roots:
                items.append({"kind": "map", "at": t, "to": t, "root": self.roots[t]})
            for q in self.idx.get(t, []):
                item = self.evidence(q, t)
                if item["kind"] in IGNORED:
                    if len(n["ignored"]) < 4:
                        n["ignored"].append(item)
                    continue
                items.append(item)
        if st.ambiguous:
            items.append({"kind": "ambiguous-start", "at": st.entry, "to": st.entry})
        return n

    def build(self, commands: list[int]) -> None:
        todo = list(commands)
        while todo:
            c = todo.pop()
            if c in self.nodes:
                continue
            for item in self.node(c)["items"]:
                if item["kind"] in EDGE_KINDS:
                    self.edges[item["from"]].append((c, item))
                    if item["from"] not in self.nodes:
                        todo.append(item["from"])
        # propagation: strong roots first, then weak ones
        for lvl, kinds in ((2, STRONG), (1, WEAK)):
            front = []
            for c, n in self.nodes.items():
                if self.level.get(c, 0) >= lvl:
                    continue
                hit = next((it for it in n["items"] if it["kind"] in kinds), None)
                if hit is not None:
                    self.level[c], self.why[c] = lvl, hit
                    front.append(c)
            while front:
                x = front.pop()
                for y, item in self.edges.get(x, ()):
                    if self.level.get(y, 0) < lvl:
                        self.level[y], self.why[y] = lvl, item
                        front.append(y)

    def path(self, c: int) -> list[dict]:
        """The evidence that gives a command its class, from the command up to the root."""
        out, seen = [], set()
        while c in self.why and c not in seen:
            seen.add(c)
            it = self.why[c]
            out.append(dict(it, command=c))
            if it["kind"] not in EDGE_KINDS:
                break
            c = it["from"]
        return out

    def misread(self, ref: dict) -> dict | None:
        """A script reference that is a known-table field or a THUMB ldr word: the misread record and its evidence."""
        at = ref["at"]
        t = self.table_of(at)
        if t is not None:
            return {"ref": at, "kind": ref["kind"], "as": "table field", "table": t.name, "topic": t.topic,
                    "source": t.source, "field": (at - t.base) % t.stride,
                    "evidence": dict(self.table_class(t), at=at, to=self.ptr(at))}
        c = ref_command(self.d, ref, self.code.tb)
        if at % 4 == 0 and c is not None and self.code.starts(c).ambiguous:
            ldr = pool_load(self.d, at)
            if ldr is not None:
                kind = "pool" if self.live_pool(at) else "dead-pool"
                return {"ref": at, "kind": ref["kind"], "as": "literal pool word", "ldr": ldr,
                        "evidence": {"kind": kind, "at": at, "to": self.ptr(at), "ldr": ldr}}
        return None

    def classify(self, entries: list[dict]) -> dict[str, dict]:
        """id -> record for inventory entries of the script group."""
        tb = self.code.tb
        cmds: dict[str, list[tuple[dict, int]]] = {}
        misread: dict[str, list[dict]] = {}
        for e in entries:
            cs = []
            for r in e["refs"]:
                if not r["kind"].startswith("script:"):
                    continue
                m = self.misread(r)
                if m is not None:
                    misread.setdefault(e["id"], []).append(m)
                    continue
                c = ref_command(self.d, r, tb)
                if c is not None:
                    cs.append((r, c))
            cmds[e["id"]] = cs
        self.build([c for cs in cmds.values() for _, c in cs])
        out: dict[str, dict] = {}
        for e in entries:
            lvl, evidence, commands = 0, [], []
            for r, c in cmds[e["id"]]:
                st = self.node(c)["start"]
                cl = self.level.get(c, 0)
                pointed = [t for t in st.starts if t in self.idx or t in self.roots]
                commands.append({"at": c, "ref": r["at"], "kind": r["kind"][7:], "entry": st.entry,
                                 "pointed": pointed[:8], "ambiguous": st.ambiguous,
                                 "class": CLASSES[2 - cl], "origin": origin(c, self.origins, self.default_origin)})
                if cl > lvl:
                    lvl, evidence = cl, self.path(c)
            for m in misread.get(e["id"], []):
                kind = m["evidence"]["kind"]
                cl = 2 if kind in STRONG else 0 if kind in IGNORED else 1
                if cl > lvl:
                    lvl, evidence = cl, [m["evidence"]]
            for r in e["refs"]:     # pointers to the string that are no script commands
                if r["kind"].startswith("script:") or r["kind"].startswith("table:"):
                    continue
                item = self.evidence(r["at"], e["offset"])
                cl = 2 if item["kind"] in STRONG else 1 if item["kind"] in WEAK else 0
                if cl > lvl:
                    lvl, evidence = cl, [dict(item, string=True)]
            ignored = []
            if lvl == 0:   # the pointers that exist come from unreachable scripts only
                for r, c in cmds[e["id"]]:
                    for it in self.node(c)["items"][:6]:
                        it = dict(it, command=c)
                        if it["kind"] in EDGE_KINDS:   # the script holding the pointing command
                            src = self.node(it["from"])
                            it["from_entry"] = src["start"].entry
                            it["from_pointers"] = sum(1 for x in src["items"] if x["kind"] != "ambiguous-start")
                        evidence.append(it)
                    ignored += [dict(it, command=c) for it in self.node(c)["ignored"]][:4]
            out[e["id"]] = {
                "class": CLASSES[2 - lvl], "region": region(e["offset"]), "origin": origin(e["offset"], self.origins, self.default_origin),
                "commands": commands, "evidence": evidence, "ignored": ignored,
                "misread": [{k: v for k, v in m.items() if k != "evidence"} for m in misread.get(e["id"], [])],
            }
        return out


HEADER_END = 0x200   # GBA and Game Freak ROM headers
BEFORE = 64          # a structure header before its first pointer field
NEAR = 64            # a small function's load before its pool
ANCHOR = 127         # gcc Thumb-1 section anchor offsets: 0-127
DEPTH = 6            # tables in a chain of table pointers
NULL_SPAN = 0x1000   # null entries a table may hold past its last pointer


class DataStudy:
    """Classify system strings through code and data references."""

    def __init__(self, study: Study, declared: list[dict] | None = None):
        self.st = study
        self.d = study.d
        self.idx = study.idx
        self.nc = study.code.not_code if study.code.not_code is not None else bytearray(len(study.d))
        self._stride: dict[int, list[int]] = {}
        self._tables: dict[tuple[int, int], list | None] = {}
        # field position -> (base, stride, first, last) of a declared table
        self.declared: dict[int, tuple[int, int, int, int]] = {}
        for t in declared or []:
            pos = declared_positions(t)
            for q in pos:
                self.declared.setdefault(q, (t["base"], t["stride"], min(pos), max(pos)))

    def ptr(self, q: int) -> int | None:
        return self.st.ptr(q)

    def null(self, p: int) -> bool:
        return 0 <= p <= len(self.d) - 4 and self.d[p:p + 4] == b"\0\0\0\0"

    def run(self, q: int, s: int, cap: int = 64) -> int:
        """ROM pointers in step with q at stride s, both ways, passing null words."""
        n = 0
        for sign in (-1, 1):
            p = last = q
            for _ in range(cap):
                p += sign * s
                if self.ptr(p) is not None:
                    n += 1
                    last = p
                elif not (self.null(p) and abs(p - last) <= NULL_SPAN):
                    break
        return n

    def strides(self, q: int, n: int = 4) -> list[int]:
        """Candidate strides of the entry at q, the most pointers in step first."""
        r = self._stride.get(q)
        if r is None:
            runs = sorted(((self.run(q, s), -s) for s in range(4, 68, 4)), reverse=True)
            r = self._stride[q] = [-ms for k, ms in runs[:n] if k > 0] or [4]
        return r

    def extent(self, q: int, s: int) -> tuple[int, int, int]:
        """(first pointer, base, last pointer) of the table holding q at stride s."""
        def walk(sign):
            p = first = q
            gap = False
            while abs(p - q) < 0x10000:
                p2 = p + sign * s
                if self.ptr(p2) is not None:
                    p = first = p2
                    gap = False
                elif self.null(p2) and abs(p2 - first) <= NULL_SPAN:
                    p = p2
                elif not gap and self.ptr(p2 + sign * s) is not None:   # one odd entry
                    p, gap = p2, True
                else:
                    break
            return first, p
        first, base = walk(-1)
        last, _ = walk(1)
        return first, min(first, base), last

    def text_field(self, r: int) -> bool:
        """True when the neighbours of r at its stride mostly point at text."""
        if r % 4:
            return False
        s = self.strides(r)[0]
        ts = [t for t in (self.ptr(r + k * s) for k in (-4, -3, -2, -1, 1, 2, 3, 4)) if t is not None]
        return len(ts) >= 2 and 4 * sum(self.nc[t] for t in ts) >= 3 * len(ts)

    def code_pool(self, q: int) -> int | None:
        """The ldr that loads the aligned word q, unless that ldr is table data."""
        ldr = pool_load(self.d, q)
        if ldr is None:
            return None
        w = ldr & ~3
        s = self.strides(w)[0]
        if all(self.ptr(p) is not None for p in (w - s, w, w + s)):
            return None
        return ldr

    def anchor_use(self, ldr: int, lo: int, hi: int) -> tuple[int, int] | None:
        """(position, level) of an instruction near the ldr with an immediate in [lo, hi]."""
        d = self.d
        best = None
        for a in range(max(0, ldr - 16), min(len(d) - 1, ldr + 64), 2):
            hw = d[a] | d[a + 1] << 8
            if hw >> 13 == 1:                      # mov, cmp, add, sub #imm8
                v, lvl = hw & 0xFF, 2 if hw >> 11 == 6 else 1
            elif hw >> 13 == 3:                    # ldr, str, ldrb, strb #imm5
                v, lvl = (hw >> 6 & 31) * (1 if hw & 0x1000 else 4), 2 if hw & 0x800 else 1
            elif hw >> 12 == 8:                    # ldrh, strh #imm5
                v, lvl = (hw >> 6 & 31) * 2, 2 if hw & 0x800 else 1
            elif hw >> 10 == 7:                    # add, sub #imm3
                v, lvl = hw >> 6 & 7, 1 if hw & 0x200 else 2
            else:
                continue
            if lo <= v <= hi and (best is None or lvl > best[1]):
                best = (a, lvl)
        return best

    def script_command(self, q: int) -> int | None:
        """The script command whose text operand is the word at q."""
        if q < 4 or self.ptr(q - 4) is not None:
            return None
        for op, k in TEXT_PTR_AT.items():
            if self.d[q - k] == op and (k == 1 or self.d[q - 1] <= (3 if op == 0x0F else 0x0F)):
                return q - k
        return None

    def word(self, q: int, depth: int = 0, middle: bool = False) -> tuple[int, dict]:
        """(level, evidence) of the pointer at q: 2 reachable, 1 possibly, 0 none."""
        lvl, ev = self._word(q, depth, middle)
        if lvl < 2 and not middle:
            c = self.script_command(q)
            if c is not None:
                if c not in self.st.nodes:
                    self.st.build([c])
                sl = self.st.level.get(c, 0)
                # a script operand outside a table: the script decides
                if sl > lvl or ev["kind"] in ("unaligned", "aligned"):
                    return sl, {"kind": "script", "at": q, "command": c, "path": self.st.path(c)}
        return lvl, ev

    def _word(self, q: int, depth: int, middle: bool) -> tuple[int, dict]:
        if self.nc[q]:
            return 0, {"kind": "in-string", "at": q}
        if q < HEADER_END:
            for r in self.idx.get(q, []):
                ldr = self.code_pool(r) if r % 4 == 0 else None
                if ldr is not None and self.st.live_pool(r):
                    return 2, {"kind": "rom-header", "at": q, "pool": r, "ldr": ldr}
            return 0, {"kind": "rom-header", "at": q}
        weak = 0 if middle else 1
        if q % 4:
            if self.ptr(q & ~3) is not None or self.ptr((q & ~3) + 4) is not None:
                return 0, {"kind": "straddle", "at": q}
            return weak, {"kind": "unaligned", "at": q}
        ldr = self.code_pool(q)
        if ldr is not None:
            if self.st.live_pool(q):
                return 2, {"kind": "pool", "at": q, "ldr": ldr}
            # unreached loads or lookalike data: only a reached table counts
            dead = (0, {"kind": "dead-pool", "at": q, "ldr": ldr})
            s = self.strides(q)[0]
            # loads before it and a neighbour's: a pool, no table
            if self.run(q, s, cap=2) == 0 or pool_load(self.d, q, NEAR) is not None and any(
                    self.ptr(q + k * s) is not None and pool_load(self.d, q + k * s, NEAR) is not None
                    for k in (-1, 1)):
                return dead
            lvl, ev = self.table(q, depth)
            return (lvl, ev) if lvl == 2 else dead
        if self.run(q, self.strides(q)[0], cap=2) == 0:
            return weak, {"kind": "aligned", "at": q}
        return self.table(q, depth)

    def table(self, q: int, depth: int) -> tuple[int, dict]:
        if q in self.declared:   # its declared base, exactly
            return self.table_at(q, self.declared[q][1], depth, self.declared[q])
        best = None
        for k, s in enumerate(self.strides(q)):
            lvl, ev = self.table_at(q, s, depth)
            if k and lvl < 2:   # other strides: code roots only
                continue
            if best is None or lvl > best[0]:
                best = (lvl, ev)
            if lvl == 2:
                break
        return best

    def table_at(self, q: int, s: int, depth: int,
                 known: tuple[int, int, int, int] | None = None) -> tuple[int, dict]:
        if known is not None:
            base, _, first, last = known
        else:
            first, base, last = self.extent(q, s)
        info = self._tables.get((base, s), "new")
        if info is None:                           # a cycle of tables
            return 1, {"kind": "cycle", "at": q, "table": base, "stride": s}
        if info == "new":
            if depth >= DEPTH:
                return 1, {"kind": "deep", "at": q, "table": base, "stride": s}
            self._tables[(base, s)] = None
            info = self._tables[(base, s)] = self.pointed(first, base, last, s, depth, known is not None)
        tab = {"at": q, "table": base, "stride": s, "first": first, "last": last}
        best, ignored = None, []
        for t, lvl, ev in info:
            if t > base + 2 * s and t > q:
                break
            if lvl and (best is None or lvl > best[0]):
                best = (lvl, dict(tab, kind="table", via=dict(ev, to=t)))
            elif not lvl and len(ignored) < 4:
                ignored.append(dict(ev, to=t))
        return best or (0, dict(tab, kind="table-orphan", ignored=ignored))

    def pointed(self, first: int, base: int, last: int, s: int, depth: int,
                exact: bool = False) -> list[tuple[int, int, dict]]:
        """(target, level, evidence) of every pointer into the table, by target."""
        lo, head, hi = base - (s if exact else max(2 * s, BEFORE)), base + 2 * s, last + 3
        out, inner = [], []
        for t in range(max(0, base - ANCHOR), hi + 1):
            if t % 4 or self.nc[t]:   # tables start on a word; text
                continue
            if exact and t != lo and t < base:   # no header, no anchor
                continue
            for r in self.idx.get(t, []):
                ldr = self.code_pool(r) if r % 4 == 0 else None
                if t < lo:   # a section anchor
                    if ldr is not None and self.st.live_pool(r):
                        use = self.anchor_use(ldr, base - t, first - t)
                        out.append((t, use[1], {"kind": "anchor", "at": r, "ldr": ldr, "use": use[0]}) if use
                                   else (t, 0, {"kind": "anchor-unused", "at": r, "ldr": ldr}))
                    continue
                if base <= r <= hi and ldr is None:
                    inner.append((t, r))
                    continue
                if t < first and self.text_field(r):
                    continue
                lvl, ev = self.word(r, depth + 1, t > head)
                out.append((t, lvl, ev))
        out.sort(key=lambda x: x[0])
        for _ in range(2):   # an inner pointer counts as its position
            for t, r in inner:
                best = None
                for t2, l2, e2 in out:
                    if t2 > max(r, head):
                        break
                    if l2 and e2.get("at") != r and (best is None or l2 > best[0]):
                        best = (l2, e2)
                if best and not any(e.get("at") == r and l >= best[0] for _, l, e in out):
                    out.append((t, best[0], {"kind": "inner", "at": r, "via": best[1]}))
            out.sort(key=lambda x: x[0])
        return out

    def classify(self, entries: list[dict]) -> dict[str, dict]:
        """id -> record for inventory entries of the system group."""
        out = {}
        for e in entries:
            lvl, best, items = 0, None, []
            for q in self.idx.get(e["offset"], []):
                l, ev = self.word(q)
                items.append(ev)
                if l > lvl:
                    lvl, best = l, ev
            evidence = flatten(best) if best else [flatten(ev, depth=1)[0] for ev in items[:6]]
            out[e["id"]] = {"class": CLASSES[2 - lvl], "group": "system", "region": region(e["offset"]),
                            "origin": origin(e["offset"], self.st.origins, self.st.default_origin), "pointers": len(items), "evidence": evidence}
        return out


def flatten(ev: dict, depth: int = 64) -> list[dict]:
    """A nested evidence chain ("via", script "path") as a list of steps, string first."""
    out = []
    while ev is not None and len(out) < depth:
        step = {k: v for k, v in ev.items() if k not in ("via", "path", "ignored")}
        if "ignored" in ev:
            step["ignored"] = [flatten(x, 1)[0] for x in ev["ignored"]]
        out.append(step)
        if "path" in ev:
            out += [dict(x, script=True) for x in ev["path"]]
        ev = ev.get("via")
    return out


def apply_observations(strings: dict[str, dict], rules: list[dict],
                       inventory_ids: set[str]) -> None:
    """Runtime evidence overrides incomplete static inference."""
    observed = observed_entries(rules)
    unknown = observed.keys() - inventory_ids
    if unknown:
        raise ValueError(f"observed entries absent from inventory: {sorted(unknown)}")
    for sid, evidence in observed.items():
        if sid in strings:
            rec = strings[sid]
            rec["static_class"] = rec["class"]
            rec["class"] = "reachable"
            rec["evidence"] = [*rec.get("evidence", []), evidence]


def declared_tables(prof) -> list[dict]:
    """The profile's [[reach.table]] rules: tables whose start the review found; each needs evidence."""
    rules = prof.get("reach", {}).get("table", [])
    for t in rules:
        if not all(k in t for k in ("base", "stride", "count")) or not has_prose(t.get("evidence", "")):
            raise ValueError(f"[[reach.table]] {t.get('name')}: needs base, stride, count and evidence")
    return rules


def unused_entries(prof) -> set[int]:
    """Positions of the function table entries the [[reach.unused_entries]] rules name; each needs evidence."""
    out = set()
    for r in prof.get("reach", {}).get("unused_entries", []):
        if "base" not in r or not r.get("indexes") or not has_prose(r.get("evidence", "")):
            raise ValueError(f"[[reach.unused_entries]] {r.get('name')}: needs base, indexes and evidence")
        out |= {r["base"] + i * r.get("stride", 4) for i in r["indexes"]}
    return out


def declared_positions(t: dict) -> list[int]:
    """The text pointer positions of a declared table: `count` records of `stride` from `base`, pointers at `fields`."""
    idx = t.get("indexes", range(t["count"]))
    return [t["base"] + i * t["stride"] + f for i in idx for f in t.get("fields", [0])]


def code_refs(prof) -> list[tuple[str, list[int], list[str]]]:
    """(kind, pool words, member ids) of the profile's declared code references."""
    s = prof.get("strings", {})
    out = [("computed", [r["pointer"]], list(r["ids"])) for r in s.get("computed", [])]
    out += [("interior", [r["pointer"]], [r["id"]]) for r in s.get("interior", [])]
    out += [("table-base", list(r["pointers"]), [f"s{o:07X}" for o in table_base_members(r)])
            for r in s.get("table_base", [])]
    return out


def apply_code_refs(strings: dict[str, dict], refs, thumb: Thumb) -> None:
    """Declared code references count where reached code loads their pool word."""
    for kind, pools, ids in refs:
        live = next(((p, ldr) for p in pools if (ldr := thumb.load(p)) is not None), None)
        if live is None:
            continue
        for sid in ids:
            rec = strings.get(sid)
            if rec is not None and rec["class"] != "reachable":
                rec["class"] = "reachable"
                rec["evidence"] = [{"kind": kind, "at": live[0], "ldr": live[1]}, *rec.get("evidence", [])]


def _analyze(rom: Rom, prof, inv: dict, ids: list[str], system_ids: list[str] | None = None) -> dict:
    """Classify the inventory entries `ids` (script group) and `system_ids` (system group) on the base ROM."""
    d = rom.data
    tb = dict(TRAINERBATTLE_TEXT_OFFSETS)
    tb.update({int(k): tuple(v) for k, v in prof.get("script", {}).get("trainerbattle_text_offsets", {}).items()})
    cmd_len = dict(CMD_LEN)
    cmd_len.update({int(k): int(v) for k, v in prof.get("script", {}).get("command_lengths", {}).items()})
    not_code = bytearray(len(d))
    for e in inv["entries"]:
        if e["kind"] == "pointer":
            not_code[e["offset"]:e["offset"] + e["length"]] = b"\x01" * e["length"]
    for t, e in movement_spans(d).items():
        not_code[t:e + 1] = b"\x01" * (e + 1 - t)
    for t in prof.get("table", []):   # trainer parties (the gTrainers layout of batchplan.trainer_levels)
        if t["name"] != "trainer_names":
            continue
        for tid in range(t["count"]):
            rec = t["base"] + tid * t["stride"]
            n = d[rec + t.get("party_count_offset", 32)]
            party = rom.ptr_at(rec + t.get("party_pointer_offset", 36))
            if party is not None and 0 < n <= 6:
                size = n * (16 if d[rec + t.get("party_flags_offset", 0)] & 1 else 8)
                not_code[party:party + size] = b"\x01" * size
    walker = _VisitWalker(rom, prof)
    walker.walk_all()
    walker.walk_structural()
    events = map_event_fields(walker)
    idx = pointer_index(rom)
    study = Study(d, idx, Code(d, cmd_len, tb, not_code), events, set(walker.walked),
                  walker.roots, walker.visited, known_tables(prof, inv),
                  prof.get("reach", {}).get("origin_regions", []),
                  prof.get("reach", {}).get("default_origin", "rom"), Thumb(d, idx, not_code, unused_entries(prof)))
    by_id = {e["id"]: e for e in inv["entries"]}
    strings = study.classify([by_id[i] for i in ids])
    for r in strings.values():
        r["group"] = "script"
    nodes = len(study.nodes)
    data = DataStudy(study, declared_tables(prof))   # after the script classes, which it extends
    strings.update(data.classify([by_id[i] for i in system_ids or []]))
    apply_code_refs(strings, code_refs(prof), study.thumb)
    stats = {"strings": len(ids), "nodes": nodes, "maps_walked": len(walker.walked),
             "map_headers": len({h for h, _ in events.values()}), "map_roots": len(walker.roots),
             "walk_commands": len(walker.visited), "pointers": sum(len(v) for v in study.idx.values()),
             "system_strings": len(system_ids or []), "system_tables": len(data._tables)}
    return {"stats": stats, "strings": strings}


def discover(rom: Rom, prof, inv: dict, ids: list[str], system_ids: list[str] | None = None):
    """Derive static reach facts before runtime observations are merged."""
    from .flow_facts import collect

    return collect("reach", _analyze(rom, prof, inv, ids, system_ids), inv, prof)


def render(result, prof, inv):
    """Merge overlay observations into a copy of the static projection."""
    from .flow_facts import project

    output = project(result)
    apply_observations(output["strings"], prof.get("reach", {}).get("observed", []),
                       {entry["id"] for entry in inv["entries"]})
    return output


def run(rom: Rom, prof, inv: dict, ids: list[str], system_ids: list[str] | None = None) -> dict:
    """Render the reach study from static facts and overlay observations."""
    return render(discover(rom, prof, inv, ids, system_ids), prof, inv)


def _hex(x):
    """JSON form: offsets as hex strings."""
    if isinstance(x, dict):
        return {k: (f"{v:#x}" if isinstance(v, int) and not isinstance(v, bool)
                    and k in HEX_KEYS else _hex(v)) for k, v in x.items()}
    if isinstance(x, list):
        return [_hex(v) for v in x]
    return x


HEX_KEYS = {"at", "to", "from", "ref", "entry", "command", "map", "first", "base", "ldr", "from_entry",
            "table", "last", "use", "pool"}


def to_json(result: dict) -> dict:
    out = _hex(result)
    for rec in out["strings"].values():
        for c in rec.get("commands", []):   # system records have none
            c["pointed"] = [f"{p:#x}" for p in c["pointed"]]
    return out


def system_report(result: dict) -> list[str]:
    """The system strings' part of the report."""
    recs = {i: r for i, r in result["strings"].items() if r.get("group") == "system"}
    st = result["stats"]
    lines = ["", "# System string reachability", "",
             f"Strings of the catalog's system group: {len(recs)}. Tables examined: {st.get('system_tables', 0)}.",
             "", "## System counts per class", "", "| Class | Strings |", "|---|---|"]
    by_class = Counter(r["class"] for r in recs.values())
    lines += [f"| {c} | {by_class[c]} |" for c in CLASSES]
    lines += ["", "## System evidence", "", "The kind of the root that gives a reachable or possibly reachable "
              "string its class (the last step of its evidence; a script root names the script study's root).",
              "", "| Class | Root kind | Strings |", "|---|---|---|"]
    roots = Counter((r["class"], r["evidence"][-1]["kind"]) for r in recs.values()
                    if r["class"] != "unreachable" and r["evidence"])
    lines += [f"| {c} | {k} | {n} |" for (c, k), n in sorted(roots.items())]
    groups: dict[tuple, list[str]] = defaultdict(list)
    for i, r in recs.items():
        if r["class"] == "unreachable":
            ev = next((x for x in r["evidence"] if x["kind"] == "table-orphan"), r["evidence"][0] if r["evidence"] else {})
            groups[(ev.get("kind", "none"), ev.get("table", ""), ev.get("stride", ""))].append(i)
    lines += ["", "## Tables nothing reads", "",
              "Grouped by: first table holding a pointer to the string (base, stride, first and last pointer).",
              "", "Ignored pointers to the table: coincidental words, unused anchors, unreached data.", "",
              "A string with several pointers is listed once; its record names every table.", "",
              "| Table | Stride | Pointers | Strings | Example | Pointers to the table |", "|---|---|---|---|---|---|"]
    for (kind, table, stride), ids in sorted(groups.items(), key=lambda kv: (-len(kv[1]), str(kv[0]))):
        ev = next((x for x in recs[ids[0]]["evidence"] if x["kind"] == kind), {})
        span = f"{ev.get('first', '')}-{ev.get('last', '')}" if kind == "table-orphan" else kind
        ign = ", ".join(f"{x['kind']} {x['at']}" for x in ev.get("ignored", [])) or "none"
        lines.append(f"| {table or kind} | {stride} | {span} | {len(ids)} | {sorted(ids)[0]} | {ign} |")
    doc = SYSTEM_REACH_DESCRIPTION
    lines += ["", "## System method", ""] + doc.split("Method:")[1].split("Limits:")[0].strip().splitlines()
    limits = " ".join(doc.split("Limits:")[1].split())
    lines += ["", "## System limits", "", limits[0].upper() + limits[1:]]
    lines += ["", "Record per system string in build/reach.json: class, group, region, origin, pointer count, "
              "evidence (steps to the root, or each pointer with its table)."]
    return lines


def fewer_strings(result: dict, previous: dict | None) -> str | None:
    """Why a new study must not replace the stored one, or None."""
    if not previous:
        return None
    new, old = len(result.get("strings", {})), len(previous.get("strings", {}))
    if new >= old:
        return None
    return (f"the run classifies {new} strings, the stored build/reach.json {old}: "
            f"the narrower run would drop {old - new} classifications; "
            f"select the groups of the stored study or --allow-fewer")


def report(result: dict, previous: dict | None = None) -> str:
    recs = {i: r for i, r in result["strings"].items() if r.get("group", "script") == "script"}
    st = result["stats"]
    lines = ["# Script text reachability", "",
             f"Strings of the catalog's script group: {st['strings']}.", "",
             f"Script commands examined: {st['nodes']}; map headers with events: {st['map_headers']}; "
             f"walked by the context build: {st['maps_walked']}; map script roots: {st['map_roots']}; "
             f"commands decoded by the map walk: {st['walk_commands']}; ROM pointers scanned: {st['pointers']}.", "",
             "## Counts per class", "", "| Class | Strings |", "|---|---|"]
    by_class = Counter(r["class"] for r in recs.values())
    lines += [f"| {c} | {by_class[c]} |" for c in CLASSES]
    lines += ["", "## Counts per origin and class", "",
              "Origin: profile reach region. Script origin: regions of the commands showing the string.", "",
              "| Origin | Script origin | " + " | ".join(CLASSES) + " | Total |", "|---|---|---|---|---|---|"]
    combo = Counter()
    for r in recs.values():
        so = "+".join(sorted({c["origin"] for c in r["commands"]})) or "none"
        combo[(r["origin"], so, r["class"])] += 1
    for o, so in sorted({(o, so) for o, so, _ in combo}):
        row = [combo[(o, so, c)] for c in CLASSES]
        lines.append(f"| {o} | {so} | " + " | ".join(map(str, row)) + f" | {sum(row)} |")
    lines += ["", "## Counts per region and class", "", "| Region | " + " | ".join(CLASSES) + " | Total |",
              "|---|---|---|---|---|"]
    reg = Counter((r["region"], r["class"]) for r in recs.values())
    for g in sorted({g for g, _ in reg}):
        row = [reg[(g, c)] for c in CLASSES]
        lines.append(f"| {g} | " + " | ".join(map(str, row)) + f" | {sum(row)} |")
    lines += ["", "## Evidence", "", "Root kind: last step of the evidence path that gives the class.", "",
              "Distinct roots: root positions; one coincidental word can make a script block possibly reachable.",
              "",
              "| Class | Root kind | Strings | Distinct roots |", "|---|---|---|---|"]
    roots, where = Counter(), defaultdict(set)
    for r in recs.values():
        if r["class"] != "unreachable" and r["evidence"]:
            key = (r["class"], r["evidence"][-1]["kind"])
            roots[key] += 1
            where[key].add(r["evidence"][-1]["at"])
    lines += [f"| {c} | {k} | {n} | {len(where[(c, k)])} |" for (c, k), n in sorted(roots.items())]
    walk = roots[("reachable", "map-walk")]
    if walk:
        lines += ["", f"map-walk: {walk} strings are met by the context build's map walk with the profile's current "
                  "command lengths; a context build with this profile gives them map context."]
    tables = {i: [m for m in r["misread"] if m["as"] == "table field"] for i, r in recs.items()}
    tables = {i: ms for i, ms in tables.items() if ms}
    pools = {i: [m for m in r["misread"] if m["as"] == "literal pool word"] for i, r in recs.items()}
    pools = {i: ms for i, ms in pools.items() if ms}
    lines += ["", f"## Misclassified table data: {len(tables)}", "",
              "Script references inside records of a known data table (profile tables, pointer tables, "
              "catalog table runs): table fields, not commands.", "", "Class: by the table.", "",
              "| String | Reference | Table | Topic | Field | Class |", "|---|---|---|---|---|---|"]
    for i, ms in sorted(tables.items()):
        lines += [f"| {i} | {m['ref']} {m['kind']} | {m['table']} ({m['source']}) | {m.get('topic') or ''} | "
                  f"+{m['field']} | {recs[i]['class']} |" for m in ms]
    lines += ["", f"## Misread code references: {len(pools)}", "",
              "Script references that are literal pool words a THUMB ldr loads; no command decodes there.", "",
              "Class: by the code.", "", "| String | Reference | ldr | Class |", "|---|---|---|---|"]
    for i, ms in sorted(pools.items()):
        lines += [f"| {i} | {m['ref']} {m['kind']} | {m['ldr']} | {recs[i]['class']} |" for m in ms]
    doc = SCRIPT_REACH_DESCRIPTION
    lines += ["", "## Method", ""] + [ln for ln in doc.split("Method:")[1].split("Limits:")[0].strip().splitlines()]
    limits = doc.split("Limits:")[1].strip()
    lines += ["", "## Limits", ""] + (limits[0].upper() + limits[1:]).splitlines()
    lines += ["", "Record per string in build/reach.json: class, region, origin, commands with entry and starts, "
              "evidence (pointer path to the root, or the existing pointers from unreached scripts)."]
    if any(r.get("group") == "system" for r in result["strings"].values()):
        lines += system_report(result)
    if previous is not None:
        lines += changes_report(previous, result)
    return "\n".join(lines) + "\n"


def changes_report(previous: dict, result: dict) -> list[str]:
    """The strings whose class differs from the previous build/reach.json."""
    old = previous.get("strings") or {}
    new = result["strings"]
    changed = sorted(i for i in new if i in old and old[i].get("class") != new[i]["class"])
    lines = ["", f"## Class changes: {len(changed)}", "",
             "Strings of both runs whose class differs from the previous build/reach.json, with the first "
             "step of the new evidence.", "",
             "| String | Group | Before | After | Evidence | At |", "|---|---|---|---|---|---|"]
    for i in changed:
        ev = (new[i].get("evidence") or [{}])[0]
        lines.append(f"| {i} | {new[i].get('group', 'script')} | {old[i]['class']} | {new[i]['class']} | "
                     f"{ev.get('kind', '')} | {ev.get('at', '')} |")
    gone = sorted((set(old) - set(new)) | (set(new) - set(old)))
    if gone:
        lines += ["", f"Strings in one run only: {len(gone)} (the studied strings differ)."]
    return lines
