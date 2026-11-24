"""Map and script context for strings: where, by whom and in what box each text is shown."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import re

from ..core.notes import has_prose
from ..core.charmap import Charmap, CharmapError
from ..core.rom import Rom, pointer_at
from ..families.knowledge import load_script

# total command length (opcode included) for fixed-size FRLG commands
CMD_LEN = {
    0x00: 1, 0x01: 1, 0x02: 1, 0x03: 1, 0x04: 5, 0x05: 5, 0x06: 6, 0x07: 6, 0x08: 2, 0x09: 2,
    0x0A: 3, 0x0B: 3, 0x0C: 1, 0x0D: 1, 0x0E: 2, 0x0F: 6, 0x10: 3, 0x11: 6, 0x12: 6, 0x13: 6,
    0x14: 3, 0x15: 9, 0x16: 5, 0x17: 5, 0x18: 5, 0x19: 5, 0x1A: 5, 0x1B: 3, 0x1C: 3, 0x1D: 6,
    0x1E: 6, 0x1F: 6, 0x20: 9, 0x21: 5, 0x22: 5, 0x23: 5, 0x24: 5, 0x25: 3, 0x26: 5, 0x27: 1,
    0x28: 3, 0x29: 3, 0x2A: 3, 0x2B: 3, 0x2C: 5, 0x2D: 1, 0x2E: 1, 0x2F: 3, 0x30: 1, 0x31: 3,
    0x32: 1, 0x33: 4, 0x34: 3, 0x35: 1, 0x36: 3, 0x37: 2, 0x38: 2, 0x39: 8, 0x3A: 8, 0x3B: 8,
    0x3C: 3, 0x3D: 8, 0x3E: 8, 0x3F: 8, 0x40: 8, 0x41: 8, 0x42: 5, 0x43: 1, 0x44: 5, 0x45: 5,
    0x46: 5, 0x47: 5, 0x48: 3, 0x49: 5, 0x4A: 5, 0x4B: 3, 0x4C: 3, 0x4D: 3, 0x4E: 3, 0x4F: 7,
    0x50: 9, 0x51: 3, 0x52: 5, 0x53: 3, 0x54: 5, 0x55: 3, 0x56: 5, 0x57: 7, 0x58: 5, 0x59: 5,
    0x5A: 1, 0x5B: 4, 0x5D: 1, 0x5E: 1, 0x5F: 1, 0x60: 3, 0x61: 3, 0x62: 3, 0x63: 7,
    0x64: 3, 0x65: 4, 0x66: 1, 0x67: 5, 0x68: 1, 0x69: 1, 0x6A: 1, 0x6B: 1, 0x6C: 1, 0x6D: 1,
    0x6E: 3, 0x6F: 5, 0x70: 6, 0x71: 6, 0x72: 1, 0x73: 5, 0x74: 5, 0x75: 5, 0x76: 1, 0x77: 2,
    0x78: 5, 0x79: 15, 0x7A: 3, 0x7B: 5, 0x7C: 3, 0x7D: 4, 0x7E: 2, 0x7F: 4, 0x80: 4, 0x81: 4,
    0x82: 4, 0x83: 4, 0x84: 4, 0x85: 6, 0x86: 5, 0x87: 5, 0x88: 5, 0x89: 3, 0x8A: 4, 0x8B: 1,
    0x8C: 1, 0x8D: 1, 0x8E: 1, 0x8F: 3, 0x90: 6, 0x91: 6, 0x92: 6, 0x93: 4, 0x94: 3, 0x95: 4,
    0x96: 3, 0x97: 2, 0x98: 3, 0x99: 3, 0x9A: 2, 0x9B: 5, 0x9C: 3, 0x9D: 4, 0x9E: 3, 0x9F: 3,
    0xA0: 1, 0xA1: 5, 0xA2: 9, 0xA3: 1, 0xA4: 3, 0xA5: 1, 0xA6: 2, 0xA7: 3, 0xA8: 6, 0xA9: 5,
    0xAA: 9, 0xAB: 3, 0xAC: 5, 0xAD: 5, 0xAE: 1, 0xAF: 5, 0xB0: 5, 0xB1: 8, 0xB2: 1, 0xB3: 3,
    0xB4: 3, 0xB5: 3, 0xB6: 6, 0xB7: 1, 0xB8: 5, 0xB9: 5, 0xBA: 5, 0xBB: 6, 0xBC: 6, 0xBD: 5,
    0xBE: 5, 0xBF: 6, 0xC0: 3, 0xC1: 3, 0xC2: 3, 0xC3: 2, 0xC4: 8, 0xC5: 1, 0xC6: 4, 0xC7: 2,
    0xC8: 5, 0xC9: 1, 0xCA: 1, 0xCB: 1, 0xCC: 6, 0xCD: 3, 0xCE: 3, 0xCF: 1, 0xD0: 3, 0xD1: 8,
    0xD2: 4, 0xD3: 5, 0xD4: 6,   # bufferitemnameplural: opcode, buffer, item, quantity
}
END_OPS = {0x02, 0x03, 0x05, 0x08, 0x0C, 0x0D, 0x24, 0xB9}  # flow does not fall through
# command -> offset of a text pointer
TEXT_PTR_AT = {0x0F: 2, 0x67: 1, 0x78: 1, 0x85: 2, 0x9B: 1, 0xBD: 1, 0xBF: 2, 0xC8: 1}
# command -> offset of a script pointer to follow
CODE_PTR_AT = {0x04: 1, 0x05: 1, 0x06: 2, 0x07: 2}
STD_KIND = {2: "npc", 3: "sign", 4: "default", 5: "yesno", 6: "autoclose", 7: "getitem"}
TRAINER_TEXT_ROLES = {
    0: ("intro", "defeat"), 1: ("intro", "defeat"), 2: ("intro", "defeat"),
    3: ("defeat",), 4: ("intro", "defeat", "cannot-battle"), 5: ("intro", "defeat"),
    6: ("intro", "defeat", "cannot-battle"), 7: ("intro", "defeat", "cannot-battle"),
    8: ("intro", "defeat", "cannot-battle"), 9: ("defeat", "victory"),
}
# other buffer commands -> what STR_VAR (byte 1) receives;
# byte 2: table index or number, a variable from 0x4000;
# bufferleadmonspeciesname has no index; the player types nicknames.
BUFFER_OPS = {0x7D: "species", 0x7E: "species", 0x7F: "nickname", 0x80: "item", 0x82: "move",
              0x83: "number", 0x84: "std", 0xC6: "box", 0xD4: "item"}
NO_INDEX_BUFFERS = {0x7E, 0x7F, 0xC6}
VAR_FIRST = 0x4000
# commands writing no script variable keep a setvar value;
# any other command forgets every value
VAR_SAFE_OPS = {0x00, 0x01, 0x0F, 0x21, 0x22, 0x28, 0x29, 0x2A, 0x2B, 0x2F, 0x30, 0x31, 0x32, 0x4F, 0x51,
                0x5A, 0x66, 0x67, 0x68, 0x69, 0x6A, 0x6B, 0x6C, 0x6D, 0x85, 0xC7} | set(BUFFER_OPS)


def native_fill_rules(prof) -> dict:
    """Validate bounded sources written by verified native callbacks: callasm rules by address, special rules by id."""
    out, names = {}, set()
    for rule in prof.get("context", {}).get("native_fill", []):
        by = "special" if isinstance(rule, dict) and "special" in rule else "address"
        required = {"name", by, "slot", "base", "stride", "count", "variants", "evidence"}
        key = ("special", rule.get(by)) if by == "special" else rule.get(by) if isinstance(rule, dict) else None
        if (not isinstance(rule, dict) or not required <= rule.keys()
                or rule.keys() - required - {"exclude"}
                or not isinstance(rule["name"], str) or not rule["name"]
                or rule["name"] in names
                or any(type(rule[k]) is not int for k in (by, "slot", "base", "stride", "count"))
                or (not 0 <= rule[by] <= 0xFFFF if by == "special"
                    else not 0 < rule[by] < 0x2000000 or rule[by] % 2 != 1)
                or not 0 <= rule["slot"] <= 2 or not 0 <= rule["base"] < 0x2000000
                or rule["stride"] < 4 or not 0 < rule["count"] <= 4096
                or rule["base"] + rule["stride"] * (rule["count"] - 1) + 4 > 0x2000000
                or key in out or not has_prose(rule["evidence"])):
            raise ValueError("invalid native-fill declaration")
        excluded = rule.get("exclude", [])
        variants = rule["variants"]
        if (not isinstance(excluded, list) or any(type(i) is not int or not 0 <= i < rule["count"] for i in excluded)
                or len(set(excluded)) != len(excluded)
                or not isinstance(variants, list)
                or any(not isinstance(row, list) or not row
                       or any(not isinstance(sid, str) or not re.fullmatch(r"s[0-9A-F]{7}", sid) for sid in row)
                       for row in variants)
                or len(excluded) == rule["count"] and not variants):
            raise ValueError("invalid native-fill source variants")
        out[key] = rule
        names.add(rule["name"])
    return out


def native_fill_sources(rule, inv) -> list[tuple[str, ...]]:
    """Resolve each declared table cell and concatenated alternative."""
    positions = {rule["base"] + i * rule["stride"] for i in range(rule["count"])
                 if i not in rule.get("exclude", [])}
    refs = defaultdict(set)
    ids = {e["id"] for e in inv["entries"]}
    for entry in inv["entries"]:
        for ref in entry["refs"]:
            at = ref["at"] if isinstance(ref, dict) else ref[0]
            if at in positions:
                refs[at].add(entry["id"])
    if set(refs) != positions or any(len(value) != 1 for value in refs.values()):
        raise ValueError(f"{rule['name']}: incomplete native-fill table references")
    variants = [(next(iter(refs[at])),) for at in sorted(refs)] + [tuple(row) for row in rule["variants"]]
    if any(sid not in ids for row in variants for sid in row):
        raise ValueError(f"{rule['name']}: missing native-fill source")
    return list(dict.fromkeys(variants))


@dataclass
class TextUse:
    map: str
    map_name: str
    event: str            # npc / sign / trigger / mapscript
    local_id: int | None
    sprite: int | None
    textcolor: int | None
    box: str | None       # callstd kind following a loadword
    trainer: dict | None
    order: int
    script: int
    buffer: int | None = None   # bufferstring: the STR_VAR slot (0-2) this word fills
    at: int | None = None       # position of the text pointer in the command
    walk: int | None = None     # the script walk that met the text
    trainer_role: str | None = None


@dataclass
class BufferFill:
    """A buffer command met by a script walk."""
    walk: int
    order: int            # the order number of the next text of the walk
    seq: int
    slot: int             # STR_VAR slot, 0-2
    kind: str             # "string" (bufferstring) or a BUFFER_OPS value
    value: int | str | None  # Offset, index, number, or native source name.
    at: int


@dataclass
class Join:
    """A walk reaching code it met before: the slot values reach the texts after it."""
    walk: int
    order: int
    seq: int
    target: tuple[int, int]   # (order, seq) of the walk that first met the code
    pc: int | None = None     # the code position met a second time


@dataclass
class Branch:
    """A walk entering (how None) or leaving a branch it follows, and how it left."""
    walk: int
    order: int
    seq: int
    kind: str
    how: str | None = None


@dataclass
class MapContext:
    uses: dict[int, list[TextUse]] = field(default_factory=lambda: defaultdict(list))
    maps: int = 0
    scripts: int = 0
    unknown_ops: dict[int, int] = field(default_factory=lambda: defaultdict(int))
    fills: list[BufferFill] = field(default_factory=list)
    joins: list[Join] = field(default_factory=list)
    branches: list[Branch] = field(default_factory=list)
    side_uses: dict[int, list[TextUse]] = field(default_factory=lambda: defaultdict(list))
    headers: dict[str, int] = field(default_factory=dict)


class Walker:
    def __init__(self, rom: Rom, prof, cm: Charmap | None = None):
        self.rom = rom
        self.d = rom.data
        self.prof = prof
        self.native_fills = native_fill_rules(prof)
        self.cm = cm or Charmap()
        family = prof.get("engine", {}).get("family", "firered-cfru")
        defaults = load_script(family).data["script"]
        script = prof.get("script", {})
        self.default_tb = {int(k): tuple(v) for k, v in defaults["trainerbattle_text_offsets"].items()}
        self.tb = dict(self.default_tb)
        for k, v in script.get("trainerbattle_text_offsets", {}).items():
            self.tb[int(k)] = tuple(v)
        roles = {k: v for k, v in TRAINER_TEXT_ROLES.items()
                 if k in self.tb and self.tb.get(k) == self.default_tb.get(k)}
        roles.update({int(k): tuple(v) for k, v in script.get("trainerbattle_text_roles", {}).items()})
        self.tb_roles = {}
        for k, values in roles.items():
            if (k not in self.tb or len(values) != len(self.tb[k])
                    or set(values) - {"intro", "defeat", "victory", "cannot-battle"}):
                raise ValueError(f"invalid trainerbattle text roles for type {k}")
            self.tb_roles[k] = dict(zip(self.tb[k], values))
        self.tb_continue = set(script.get("trainerbattle_continue_types", defaults["trainerbattle_continue_types"]))
        self.cmd_len = {int(k): int(v) for k, v in defaults["command_lengths"].items()}
        for k, v in script.get("command_lengths", {}).items():
            self.cmd_len[int(k)] = int(v)
        self.text_ptr_at = {int(k): v[0] for k, v in defaults["text_ptr_at"].items()}
        for k, v in script.get("text_ptr_at", {}).items():
            self.text_ptr_at[int(k)] = int(v[0] if isinstance(v, list) else v)
        self.code_ptr_at = {int(k): int(v) for k, v in defaults["code_ptr_at"].items()}
        for k, v in script.get("code_ptr_at", {}).items():
            self.code_ptr_at[int(k)] = int(v)
        self.end_ops = set(script.get("end_ops", defaults["end_ops"]))
        self.trainers = next((t for t in prof.get("table", []) if t["name"] == "trainer_names"), None)
        self.ctx = MapContext()
        self.walked: set[int] = set()
        self.sink = self.ctx.uses   # side walks record their texts in ctx.side_uses
        self.walks = 0
        self.flow_memo: dict[tuple[int, int], tuple[frozenset, bool]] = {}
        self.decoded: set[int] = set()   # command starts the walks decode

    def ptr(self, off: int) -> int | None:
        return pointer_at(self.d, off)

    def command_spans(self) -> list[tuple[int, int]]:
        """[start, end) of every script command the walks decoded."""
        out = []
        for pc in self.decoded:
            op = self.d[pc]
            if op == 0x5C:
                offs = self.tb.get(self.d[pc + 1])
                n = max(offs) + 4 + (4 if self.d[pc + 1] in self.tb_continue else 0) if offs else 1
            else:
                n = self.cmd_len.get(op, 1)
            out.append((pc, pc + n))
        return sorted(out)

    def special_fill(self, pc: int, slot: int | None = None) -> dict | None:
        """The native_fill rule of the special at `pc`, of `slot` when given."""
        rule = self.native_fills.get(("special", int.from_bytes(self.d[pc + 1:pc + 3], "little")))
        return rule if rule is not None and slot in (None, rule["slot"]) else None

    def mapsec_name(self, sec: int) -> str:
        m = self.prof["maps"]
        idx = sec - m["mapsec_first"]
        p = self.ptr(m["mapsec_names"] + 4 * idx) if 0 <= idx < m.get("mapsec_count", 255) else None
        if p is None:
            return f"mapsec {sec:#x}"
        try:
            return self.cm.decode(self.d, p, limit=64)[0]
        except CharmapError:
            return f"mapsec {sec:#x}"

    def trainer_info(self, tid: int) -> dict | None:
        t = self.trainers
        if not t or not (0 <= tid < t["count"]):
            return None
        rec = t["base"] + t["stride"] * tid
        try:
            name = self.cm.decode(self.d, rec + t["field_offset"], limit=t["field_length"])[0]
        except CharmapError:
            name = "?"
        return {"id": tid, "name": name, "class": self.d[rec + t["class_offset"]],
                "gender": "female" if self.d[rec + t["gender_offset"]] & 0x80 else "male"}

    def engine_map_ids(self) -> dict[int, list[str]] | None:
        """Resolve configured runtime coordinates by map header."""
        cfg = self.prof["maps"].get("engine")
        if cfg is None:
            return None
        table = cfg["groups"]
        banks = []
        for g in range(cfg["count"]):
            at = table + 4 * g
            if not 0 <= at <= len(self.d) - 4:
                raise ValueError("runtime map table exceeds ROM")
            bank = (int.from_bytes(self.d[at:at + 4], "little") ^ cfg.get("bank_xor", 0)) - 0x08000000
            if not 0 <= bank <= len(self.d) - 4 or bank % 4:
                raise ValueError(f"invalid runtime map bank {g}")
            banks.append(bank)
        ends = sorted(set(banks) | {table, self.prof["maps"]["groups"], len(self.d)})
        out: dict[int, list[str]] = defaultdict(list)
        for g, bank in enumerate(banks):
            end = next(e for e in ends if e > bank)
            for n in range(min(256, (end - bank) // 4)):
                header = self.ptr(bank + 4 * n)
                if header is None:
                    continue
                out[header].append(f"{g}.{n}")
        return dict(out)

    def walk_all(self) -> MapContext:
        m = self.prof["maps"]
        groups = m["groups"]
        bank_starts = []
        g = 0
        while True:
            p = self.ptr(groups + 4 * g)
            if p is None:
                break
            bank_starts.append(p)
            g += 1
        # the bank table also ends the last bank list
        ends = sorted(set(bank_starts) | {groups})
        for g, start in enumerate(bank_starts):
            # a bank list ends at the next list or table;
            # placeholder entries inside are skipped
            nxt = next((e for e in ends if e > start), None)
            n = 0
            while n < 512:
                pos = start + 4 * n
                if nxt is not None and pos >= nxt:
                    break
                h = self.ptr(pos)
                if h is None:
                    if nxt is None:
                        break
                    n += 1
                    continue
                if self.ptr(h) is not None and self.ptr(h + 4) is not None:
                    self.walk_map(f"{g}.{n}", h)
                n += 1
        return self.ctx

    def find_headers(self) -> list[int]:
        """Map headers found by structure: layout, events block and map script list pointer."""
        import re
        d = self.d
        def layout_ok(L):
            if L is None or L % 4:
                return False
            w = int.from_bytes(d[L:L + 4], "little"); h = int.from_bytes(d[L + 4:L + 8], "little")
            return 1 <= w <= 512 and 1 <= h <= 512 and all(self.ptr(L + 8 + 4 * k) is not None for k in range(4))
        def events_ok(E):
            if E is None or E % 4:
                return False
            c = d[E:E + 4]
            for k in range(4):
                v = int.from_bytes(d[E + 4 + 4 * k:E + 8 + 4 * k], "little")
                if (c[k] or v) and self.ptr(E + 4 + 4 * k) is None:
                    return False
            return True
        heads = []
        for m in re.finditer(rb"[\x08\x09]", d):
            q = m.start() - 3
            # a header may have no map script list
            if q % 4 == 0 and layout_ok(self.ptr(q)) and events_ok(self.ptr(q + 4)) and (
                    self.ptr(q + 8) is not None or d[q + 8:q + 12] == b"\0\0\0\0"):
                heads.append(q)
        return heads

    def walk_structural(self) -> MapContext:
        """Walk every map header found by structure that the bank walk did not reach."""
        for h in self.find_headers():
            if h not in self.walked:
                self.walk_map(f"h{h:07X}", h)
        return self.ctx

    def walk_map(self, mid: str, h: int) -> None:
        d = self.d
        if h in self.walked:
            return
        self.walked.add(h)
        self.ctx.headers[mid] = h
        self.ctx.maps += 1
        name = self.mapsec_name(d[h + 20])
        for event, _n, s, local_id, sprite in self.events(h):
            self.walk_script(s, mid, name, event, local_id, sprite)

    def events(self, h: int) -> list[tuple[str, int, int, int | None, int | None]]:
        """The scripts of a map header in walk order: (event, n, script, local id, sprite)."""
        d = self.d
        out = []
        ev = self.ptr(h + 4)
        n_obj, n_warp, n_coord, n_bg = d[ev], d[ev + 1], d[ev + 2], d[ev + 3]
        objs, coords, bgs = self.ptr(ev + 4), self.ptr(ev + 12), self.ptr(ev + 16)
        if objs is not None:
            for i in range(n_obj):
                o = objs + 24 * i
                s = self.ptr(o + 16)
                if s is not None:
                    out.append(("npc", i, s, d[o], d[o + 1]))
        if coords is not None:
            for i in range(n_coord):
                s = self.ptr(coords + 16 * i + 12)
                if s is not None:
                    out.append(("trigger", i, s, None, None))
        if bgs is not None:
            for i in range(n_bg):
                b = bgs + 12 * i
                if d[b + 5] <= 4:
                    s = self.ptr(b + 8)
                    if s is not None:
                        out.append(("sign", i, s, None, None))
        ms = self.ptr(h + 8)
        k = n = 0
        while ms is not None and k < 16:
            typ = d[ms + 5 * k]
            if typ == 0:
                break
            p = self.ptr(ms + 5 * k + 1)
            if p is not None:
                if typ in (2, 4):
                    j = 0
                    while int.from_bytes(d[p + 8 * j:p + 8 * j + 2], "little") != 0 and j < 32:
                        s = self.ptr(p + 8 * j + 4)
                        if s is not None:
                            out.append(("mapscript", n, s, None, None))
                            n += 1
                        j += 1
                else:
                    out.append(("mapscript", n, p, None, None))
                    n += 1
            k += 1
        return out

    def walk_script(self, start: int, mid: str, name: str, event: str,
                    local_id: int | None, sprite: int | None) -> set[int]:
        """Walk a script in execution order, entering calls and conditional branches where they occur.

        Returns the command starts this walk decoded."""
        self.ctx.scripts += 1
        seen: set[int] = set()
        state = self.new_state()
        self._walk(start, None, mid, name, event, local_id, sprite, start, seen, state, 0)
        return seen

    def new_state(self) -> dict:
        """Walk state: text order and the buffer-link bookkeeping."""
        self.walks += 1
        return {"order": 0, "walk": self.walks, "seq": 0, "first": {}, "exit": None}

    def branch(self, state, kind: str, how: str | None = None) -> None:
        state["seq"] += 1
        self.ctx.branches.append(Branch(state["walk"], state["order"], state["seq"], kind, how))

    def walk_buffer_starts(self, positions) -> MapContext:
        """Walk from bufferstring commands no map walk reached."""
        met = {u.at for us in list(self.ctx.uses.values()) + list(self.ctx.side_uses.values()) for u in us}
        self.sink = self.ctx.side_uses
        for at in sorted(positions):
            if at in met or at < 2 or self.d[at - 2] != 0x85:
                continue
            self._walk(at - 2, None, "", "", "buffer", None, None, at - 2, set(), self.new_state(), 0)
            met |= {u.at for us in self.ctx.side_uses.values() for u in us}
        self.sink = self.ctx.uses
        return self.ctx

    def call_pair(self, pc: int) -> tuple[int, int] | None:
        """The callees of the second call_if of a VAR_RESULT pair."""
        p, d = pc - 16, self.d
        if p < 0 or d[p] != 0x21 or d[p + 5] != 0x07 or d[p + 11] != 0x21 or d[pc] != 0x07:
            return None
        if d[p + 6] != 1 or d[pc + 1] != 1:
            return None
        var1, v1 = int.from_bytes(d[p + 1:p + 3], "little"), int.from_bytes(d[p + 3:p + 5], "little")
        var2, v2 = int.from_bytes(d[p + 12:p + 14], "little"), int.from_bytes(d[p + 14:p + 16], "little")
        if var1 != 0x800D or var2 != 0x800D or {v1, v2} != {0, 1}:
            return None
        a, b = self.ptr(p + 7), self.ptr(pc + 2)
        return (a, b) if a is not None and b is not None else None

    def flow(self, pc: int, slot: int) -> tuple[frozenset, bool]:
        """Texts shown from `pc` before STR_VAR_(slot+1) is filled."""
        key, memo = (pc, slot), self.flow_memo
        if key in memo:
            return memo[key]
        memo[key] = (frozenset(), True)   # a recursive call: assume it returns
        d, texts, returns = self.d, set(), False
        work, seen = [pc], set()
        while work:
            p = work.pop()
            while 0 <= p < len(d) and p not in seen and len(seen) < 20000:
                seen.add(p)
                op = d[p]
                if op == 0x5C:
                    typ = d[p + 1]
                    offs = self.tb.get(typ)
                    if offs is None:
                        break
                    texts.update(t for k in offs if (t := self.ptr(p + k)) is not None)
                    length = max(offs) + 4
                    if typ in self.tb_continue:   # continue-script pointer
                        if (s2 := self.ptr(p + length)) is not None:
                            work.append(s2)
                        length += 4
                    p += length
                    continue
                n = self.cmd_len.get(op)
                if n is None:
                    break
                if op in self.text_ptr_at and op not in (0x85, 0xBF):
                    t = self.ptr(p + self.text_ptr_at[op])
                    if t is not None and (op != 0x0F or d[p + 1] == 0):
                        texts.add(t)
                if (op in BUFFER_OPS or op in (0x85, 0xBF)) and d[p + 1] == slot:
                    break   # the path fills the slot
                if op == 0x23:
                    native = self.native_fills.get(self.ptr(p + 1))
                    if native is None or native["slot"] == slot:
                        break   # fills the slot, or may: unknown code
                if op == 0x25 and self.special_fill(p, slot):
                    break   # a declared special fills it
                if op in (0x04, 0x07):
                    if (s2 := self.ptr(p + self.code_ptr_at[op])) is not None:
                        sub, back = self.flow(s2, slot)
                        texts |= sub
                        if op == 0x04 and not back:
                            break   # the called script fills the slot or ends
                        pair = self.call_pair(p) if op == 0x07 else None
                        if pair and not back and not self.flow(pair[0], slot)[1]:
                            break   # either callee fills the slot
                elif op == 0x06:
                    if (s2 := self.ptr(p + 2)) is not None:
                        work.append(s2)
                elif op == 0x05:
                    if (s2 := self.ptr(p + 1)) is None:
                        break
                    p = s2
                    continue
                if op in self.end_ops:
                    returns = returns or op == 0x03
                    break
                p += n
        memo[key] = (frozenset(texts), returns)
        return memo[key]

    def _walk(self, pc, color, mid, name, event, local_id, sprite, start, seen, state, depth,
              after_battle=False):
        d = self.d
        steps = 0
        known: dict[int, int] = {}   # variable -> value set before, in straight-line code
        while 0 <= pc < len(d) and pc not in seen and steps < 4000 and depth < 40:
            seen.add(pc)
            self.decoded.add(pc)
            state["first"][pc] = (state["order"], state["seq"])
            steps += 1
            op = d[pc]
            if op == 0x5C:
                typ = d[pc + 1]
                offs = self.tb.get(typ)
                if offs is None:
                    self.ctx.unknown_ops[0x5C00 | typ] += 1
                    state["exit"] = "end"
                    return color, after_battle
                tid = int.from_bytes(d[pc + 2:pc + 4], "little")
                info = self.trainer_info(tid) if tid < 0x4000 else None
                for k in offs:
                    t = self.ptr(pc + k)
                    if t is not None:
                        self.sink[t].append(TextUse(mid, name, event, local_id, sprite, color, "trainer", info,
                                                    state["order"], start, at=pc + k, walk=state["walk"],
                                                    trainer_role=self.tb_roles.get(typ, {}).get(k)))
                        state["order"] += 1
                known.clear()
                after_battle = True
                length = max(offs) + 4
                if typ in self.tb_continue:  # continue-script pointer after the texts
                    s2 = self.ptr(pc + length)
                    if s2 is not None:
                        self.branch(state, "goto_if")
                        color, after_battle = self._walk(
                            s2, color, mid, name, event, local_id, sprite, start, seen, state,
                            depth + 1, after_battle)
                        self.branch(state, "goto_if", state["exit"])
                    length += 4
                pc += length
                continue
            n = self.cmd_len.get(op)
            if n is None:
                self.ctx.unknown_ops[op] += 1
                state["exit"] = "end"
                return color, after_battle
            if op == 0xC7:
                color = d[pc + 1]
            if op in self.text_ptr_at:
                t = self.ptr(pc + self.text_ptr_at[op])
                if t is not None and (op != 0x0F or d[pc + 1] == 0):
                    box = None
                    if op == 0x0F and d[pc + 6] == 0x09:
                        box = STD_KIND.get(d[pc + 7], f"std{d[pc + 7]}")
                    buf = d[pc + 1] if op in (0x85, 0xBF) else None
                    self.sink[t].append(TextUse(mid, name, event, local_id, sprite, color, box, None,
                                                state["order"], start, buf, at=pc + self.text_ptr_at[op],
                                                walk=state["walk"],
                                                trainer_role="after-battle" if after_battle and buf is None else None))
                    state["order"] += 1
            # buffer commands and the variables they read
            if op in BUFFER_OPS or op in (0x85, 0xBF):
                if op in BUFFER_OPS:
                    arg = int.from_bytes(d[pc + 2:pc + 4], "little")
                    kind = BUFFER_OPS[op]
                    value = None if op in NO_INDEX_BUFFERS else arg if arg < VAR_FIRST else known.get(arg)
                else:
                    kind, value = "string", self.ptr(pc + 2)
                state["seq"] += 1
                self.ctx.fills.append(BufferFill(state["walk"], state["order"], state["seq"], d[pc + 1],
                                                 kind, value, pc))
            if op == 0x23 and (native := self.native_fills.get(self.ptr(pc + 1))) is not None:
                state["seq"] += 1
                self.ctx.fills.append(BufferFill(state["walk"], state["order"], state["seq"], native["slot"],
                                                 "native", native["name"], pc))
            if op == 0x25 and (native := self.special_fill(pc)) is not None:
                state["seq"] += 1
                self.ctx.fills.append(BufferFill(state["walk"], state["order"], state["seq"], native["slot"],
                                                 "native", native["name"], pc))
            if op == 0x16:            # setvar
                known[int.from_bytes(d[pc + 1:pc + 3], "little")] = int.from_bytes(d[pc + 3:pc + 5], "little")
            elif op in (0x19, 0x1A):  # copyvar, setorcopyvar
                dst, src = int.from_bytes(d[pc + 1:pc + 3], "little"), int.from_bytes(d[pc + 3:pc + 5], "little")
                v = src if op == 0x1A and src < VAR_FIRST else known.get(src)
                if v is None:
                    known.pop(dst, None)
                else:
                    known[dst] = v
            elif op in (0x17, 0x18):  # addvar, subvar
                known.pop(int.from_bytes(d[pc + 1:pc + 3], "little"), None)
            elif op not in VAR_SAFE_OPS:
                known.clear()
            if op in (0x04, 0x07, 0x06):  # call, call_if, goto_if: enter, then fall through
                s2 = self.ptr(pc + self.code_ptr_at[op])
                if s2 is not None:
                    kind = {0x04: "call", 0x07: "call_if", 0x06: "goto_if"}[op]
                    if op == 0x07 and self.call_pair(pc):
                        kind = "call_if_pair"   # one of two runs
                    self.branch(state, kind)
                    color, returned_after = self._walk(
                        s2, color, mid, name, event, local_id, sprite, start, seen, state,
                        depth + 1, after_battle)
                    if op == 0x04:
                        after_battle = returned_after
                    self.branch(state, kind, state["exit"])
            elif op == 0x05:  # goto: continue at the target
                s2 = self.ptr(pc + 1)
                if s2 is None:
                    state["exit"] = "end"
                    return color, after_battle
                pc = s2
                known.clear()   # the target may be reached from elsewhere
                continue
            if op in self.end_ops:
                state["exit"] = "return" if op == 0x03 else "end"
                return color, after_battle
            pc += n
        state["exit"] = "limit"
        if 0 <= pc < len(d) and pc in seen and pc in state["first"]:
            state["seq"] += 1
            self.ctx.joins.append(Join(state["walk"], state["order"], state["seq"], state["first"][pc], pc))
            state["exit"] = "join"
        return color, after_battle


IDENTITY_SCHEMA = "2.0.0"
GENDER_WORDS = re.compile(
    r"\b(?:he|him|his|himself|she|her|hers|herself|male|female|man|woman|boy|girl|"
    r"men|women|boys|girls|father|mother|son|daughter|brother|sister|husband|wife|"
    r"king|queen|prince|princess|sir|madam|gentleman|lady|ladies|gentlemen|"
    r"aunt|uncle|niece|nephew|grandfather|grandmother|grandpa|grandma|dad|daddy|mom|mommy|mum|"
    r"boyfriend|girlfriend|guy|lass|lad|dude|fella|lord|duke|duchess|mr|mrs|miss|ms)\b", re.I)
GENDERED_ROLE = r"(?:male|female|man|woman|boy|girl|father|mother|son|daughter|brother|sister|husband|wife)\b"
SELF_GENDER = re.compile(r"(?:^|[.!]\s+)I(?: am|['’]m) (?:a |an |the |your |his |her )?" + GENDERED_ROLE, re.I)
ADDRESSEE_GENDER = re.compile(r"(?:^|[.!]\s+)you(?: are|['’]re) (?:a |an |the |my |his |her )?" + GENDERED_ROLE, re.I)


def identity_context(text: str | None) -> dict:
    """Keep textual gender separate from game-data cues."""
    plain = re.sub(r"\{[^}]*\}|\\[nlp]", " ", text or "").strip()
    status = "unknown" if text is None or GENDER_WORDS.search(plain) else "not-stated"
    identity = {"schema": IDENTITY_SCHEMA,
                **{role: {"in_source": status, "basis": "", "sources": []}
                   for role in ("speaker", "addressee")}}
    if not re.search(r'["“”?]|\b(?:if|wish|pretend)\b', plain, re.I):
        for role, pattern in (("speaker", SELF_GENDER), ("addressee", ADDRESSEE_GENDER)):
            if pattern.search(plain):
                identity[role].update(in_source="stated", basis="gendered word")
    return identity


def gender_source(source: str, gender: str | None, value="", evidence="") -> dict:
    """Store identifiers and provenance without decoded game text."""
    return {"source": source, "gender": gender or "unknown", "value": str(value), "evidence": evidence}
