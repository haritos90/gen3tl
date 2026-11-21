"""Easy-chat alphabet: word lists of the alphabet mode, grouped by the initials of the shown words."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from ..core.notes import has_prose
from ..core.rom import ROM_BASE, Rom, RomEvidence

ALTERNATIVES = 0xFFFF     # then a count and that many word ids
GROUP_SHIFT = 9
WORD_RECORD = 12          # {text, alternates count, alternates}
TEXT_LIMIT = 256          # bytes a word's text may take
KEYS = {"records", "lists", "groups", "names", "limit", "keys", "rows", "evidence"}


class EasyChatError(ValueError):
    pass


@dataclass
class Span:
    at: int
    end: int
    sha1: str


@dataclass
class Alphabet:
    records: Span
    count: int
    lists: Span
    groups: int
    group_count: int
    names: dict[int, str]        # group -> name table
    limit: int
    keys: RomEvidence
    rows: list[str]
    evidence: str


def _int(value, lo: int, hi: int, what: str) -> int:
    if type(value) is not int or not lo <= value <= hi:
        raise EasyChatError(f"easy-chat alphabet: {what} must be an integer in {lo}..{hi}")
    return value


def _table(value, keys: set[str], what: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise EasyChatError(f"easy-chat alphabet: {what} needs exactly {', '.join(sorted(keys))}")
    return value


def _sha1(value, what: str) -> str:
    if not isinstance(value, str) or len(value) != 40 or set(value) - set("0123456789abcdef"):
        raise EasyChatError(f"easy-chat alphabet: {what} sha1 must be 40 lowercase hex digits")
    return value


def parse(entry: dict) -> Alphabet:
    """An [[easy_chat_alphabet]] table -> Alphabet, every field checked."""
    _table(entry, KEYS, "the entry")
    top = (1 << 25) - 1
    rec = _table(entry["records"], {"at", "count", "sha1"}, "records")
    count = _int(rec["count"], 2, 64, "records count")
    at = _int(rec["at"], 0, top, "records at")
    if at % 4:
        raise EasyChatError("easy-chat alphabet: records must be word-aligned")
    records = Span(at, at + 8 * count, _sha1(rec["sha1"], "records"))
    lst = _table(entry["lists"], {"at", "end", "sha1"}, "lists")
    lists = Span(_int(lst["at"], 0, top, "lists at"), _int(lst["end"], 0, top, "lists end"),
                 _sha1(lst["sha1"], "lists"))
    if lists.at % 2 or lists.end <= lists.at or (lists.end - lists.at) % 2:
        raise EasyChatError("easy-chat alphabet: lists must span whole halfwords")
    if lists.at < records.end and records.at < lists.end:
        raise EasyChatError("easy-chat alphabet: lists and records overlap")
    grp = _table(entry["groups"], {"at", "count"}, "groups")
    group_count = _int(grp["count"], 1, 1 << (16 - GROUP_SHIFT), "groups count")
    names = {}
    if not isinstance(entry["names"], list):
        raise EasyChatError("easy-chat alphabet: names must list {groups, table}")
    for n in entry["names"]:
        n = _table(n, {"groups", "table"}, "a name entry")
        if not isinstance(n["table"], str) or not n["table"] or not isinstance(n["groups"], list):
            raise EasyChatError("easy-chat alphabet: a name entry needs groups and a table name")
        for g in n["groups"]:
            if _int(g, 0, group_count - 1, "a name group") in names:
                raise EasyChatError(f"easy-chat alphabet: group {g} is named twice")
            names[g] = n["table"]
    keys = RomEvidence.parse(entry["keys"], "easy-chat alphabet keys", EasyChatError)
    rows = entry["rows"]
    if (not isinstance(rows, list) or not rows or len(set(rows)) != len(rows)
            or not all(isinstance(r, str) and r for r in rows)):
        raise EasyChatError("easy-chat alphabet: rows must list distinct string ids")
    if keys.size % len(rows):
        raise EasyChatError("easy-chat alphabet: keys must hold the same positions per row")
    if not has_prose(entry["evidence"]):
        raise EasyChatError("easy-chat alphabet: evidence must be text")
    return Alphabet(records, count, lists, _int(grp["at"], 0, top, "groups at"), group_count, names,
                    _int(entry["limit"], 1, 0xFFFF, "limit"), keys, list(rows), entry["evidence"])


def from_profile(prof) -> Alphabet | None:
    entries = prof.get("easy_chat_alphabet", [])
    if len(entries) > 1:
        raise EasyChatError("a profile declares at most one easy-chat alphabet")
    return parse(entries[0]) if entries else None


def read_lists(rom: Rom, alpha: Alphabet) -> list[list[tuple[int, ...]]]:
    """The entries of each record: one word id, or the word ids of alternatives."""
    out = []
    for n in range(alpha.count):
        at, size = rom.u32(alpha.records.at + 8 * n) - ROM_BASE, rom.u32(alpha.records.at + 8 * n + 4)
        if not alpha.lists.at <= at <= at + 2 * size <= alpha.lists.end:
            raise EasyChatError(f"easy-chat alphabet: record {n} leaves the lists")
        words = [rom.u16(at + 2 * i) for i in range(size)]
        entries, i = [], 0
        while i < size:
            if words[i] != ALTERNATIVES:
                entries.append((words[i],))
                i += 1
                continue
            k = words[i + 1] if i + 1 < size else 0
            if not k or i + 2 + k > size or ALTERNATIVES in words[i + 2:i + 2 + k]:
                raise EasyChatError(f"easy-chat alphabet: record {n} holds broken alternatives at {at + 2 * i:#x}")
            entries.append(tuple(words[i + 2:i + 2 + k]))
            i += 2 + k
        out.append(entries)
    return out


def _decode(rom: Rom, at: int, limit: int, cm) -> str:
    raw = rom.read(at, limit)
    end = raw.find(0xFF)
    return cm.decode((raw if end < 0 else raw[:end]) + b"\xff")[0]


def word_text(rom: Rom, alpha: Alphabet, tables: dict[str, dict], cm, word: int) -> str:
    """The text the screen shows for a word id, read from `rom`."""
    group, index = word >> GROUP_SHIFT, word & ((1 << GROUP_SHIFT) - 1)
    if group >= alpha.group_count:
        raise EasyChatError(f"easy-chat alphabet: word {word:#06x} names no group")
    if group in alpha.names:
        t = tables[alpha.names[group]]
        if index >= t["count"]:
            raise EasyChatError(f"easy-chat alphabet: word {word:#06x} leaves table {alpha.names[group]}")
        return _decode(rom, t["base"] + t["stride"] * index + t.get("field_offset", 0), t["field_length"], cm)
    words = rom.ptr_at(alpha.groups + 8 * group)
    text = None if words is None else rom.ptr_at(words + WORD_RECORD * index)
    if text is None:
        raise EasyChatError(f"easy-chat alphabet: word {word:#06x} has no text pointer")
    return _decode(rom, text, TEXT_LIMIT, cm)


def changed(alpha: Alphabet, base: Rom, rom: Rom, tables: dict[str, dict], cm) -> int:
    """The listed words whose text in `rom` differs from the base ROM's."""
    words = [w for entries in read_lists(base, alpha) for e in entries for w in e]
    return sum(word_text(rom, alpha, tables, cm, w) != word_text(base, alpha, tables, cm, w) for w in words)


def collation(buttons: list[str], fold: dict[str, str]):
    """Sort key of a text: letters by pack alphabet position, then other characters by code point."""
    rank: dict[str, int] = {}
    for c in "".join(buttons):
        rank.setdefault(fold.get(c, c), len(rank))

    def key(text: str) -> tuple:
        return tuple((1, rank[c]) if c in rank else (0, ord(c))
                     for c in (fold.get(c, c) for c in text.upper()))
    return key


def check_buttons(buttons, count: int) -> list[str]:
    """The pack's letter buttons: one string of capital letters per letter record."""
    if (not isinstance(buttons, list) or len(buttons) != count - 1
            or not all(isinstance(b, str) and b for b in buttons)):
        raise EasyChatError(f"easy-chat alphabet: the pack needs {count - 1} letter buttons")
    letters = "".join(buttons)
    if len(set(letters)) != len(letters) or not all(c.isalpha() and c == c.upper() for c in letters):
        raise EasyChatError("easy-chat alphabet: the pack's buttons need distinct capital letters")
    return list(buttons)


@dataclass
class Plan:
    alphabet: Alphabet
    buttons: list[str]
    lists: list[list[tuple[int, ...]]]      # per record, in screen order
    texts: dict[int, str]                    # word id -> shown text
    blob: bytes                              # the lists, back to back
    records: bytes


def _encode(entries: list[tuple[int, ...]]) -> bytes:
    out = []
    for e in entries:
        out += list(e) if len(e) == 1 else [ALTERNATIVES, len(e), *e]
    return b"".join(w.to_bytes(2, "little") for w in out)


def stale(base: Rom, alpha: Alphabet) -> str | None:
    """Why the base ROM does not hold the declared bytes, or None."""
    for span, what in ((alpha.records, "records"), (alpha.lists, "lists")):
        if hashlib.sha1(base.read(span.at, span.end - span.at)).hexdigest() != span.sha1:
            return f"easy-chat alphabet: the base ROM does not hold the declared {what} at {span.at:#x}"
    if not alpha.keys.matches(base.data):
        return f"easy-chat alphabet: the base ROM does not hold the declared keys at {alpha.keys.offset:#x}"
    if set(alpha.keys.read(base.data)) != set(range(alpha.count)):
        return "easy-chat alphabet: keys must reach every record and no other"
    return None


def plan(alpha: Alphabet, base: Rom, rom: Rom, tables: dict[str, dict], cm, buttons: list[str],
         fold: dict[str, str] | None = None) -> Plan:
    """The lists of every record for the words `rom` shows (the built ROM after the text)."""
    problem = stale(base, alpha)
    if problem:
        raise EasyChatError(problem)
    buttons = check_buttons(buttons, alpha.count)
    missing = sorted({t for t in alpha.names.values() if t not in tables})
    if missing:
        raise EasyChatError(f"easy-chat alphabet: no [[table]] {', '.join(missing)}")
    button_of = {c: n + 1 for n, letters in enumerate(buttons) for c in letters}
    key = collation(buttons, fold or {})
    original = [e for entries in read_lists(base, alpha) for e in entries]
    words = [w for e in original for w in e]
    if len(set(words)) != len(words):
        raise EasyChatError("easy-chat alphabet: a word is listed twice in the base ROM")
    texts = {w: word_text(rom, alpha, tables, cm, w) for w in words}
    placed: list[list[tuple]] = [[] for _ in range(alpha.count)]
    for pos, entry in enumerate(original):
        pieces: dict[str, list[int]] = {}
        for w in entry:
            pieces.setdefault(texts[w], []).append(w)
        for text, ws in pieces.items():
            placed[button_of.get(text[:1].upper(), 0)].append((key(text), text, pos, tuple(ws)))
    lists = []
    for p in placed:
        merged = {}
        for _, text, _, ws in sorted(p):
            merged.setdefault(text, ws)
        lists.append(list(merged.values()))
    over = [n for n, entries in enumerate(lists) if len(entries) > alpha.limit]
    if over:
        raise EasyChatError(f"easy-chat alphabet: record {over[0]} lists {len(lists[over[0]])} words, "
                            f"more than {alpha.limit}")
    blobs = [_encode(entries) for entries in lists]
    blob = b"".join(blobs)
    if len(blob) > alpha.lists.end - alpha.lists.at:
        raise EasyChatError(f"easy-chat alphabet: {len(blob)} bytes of lists exceed the "
                            f"{alpha.lists.end - alpha.lists.at} of the declared span")
    records, at = bytearray(), alpha.lists.at
    for b in blobs:
        records += (ROM_BASE + at).to_bytes(4, "little") + (len(b) // 2).to_bytes(4, "little")
        at += len(b)
    return Plan(alpha, buttons, lists, texts, blob, bytes(records))


@dataclass
class Result:
    allowed: set[int] = field(default_factory=set)
    failures: list[str] = field(default_factory=list)
    written: bool = False

    def register(self, guard: "DiffRegistry") -> None:
        """Add this class to the diff-guard registry."""
        guard.add("easy-chat alphabet", self.allowed, "exclusive")


def apply(rom: Rom, p: Plan, taken: set[int] | frozenset[int] = frozenset()) -> Result:
    """Write the planned lists and records into `rom`; `taken` holds bytes other classes changed."""
    res = Result()
    alpha = p.alphabet
    writes = [(alpha.lists.at, p.blob), (alpha.records.at, p.records)]
    span = {at + k for at, data in writes for k in range(len(data))}
    clash = span & set(taken)
    if clash:
        res.failures.append(f"easy-chat alphabet: its bytes overlap bytes already changed, first at {min(clash):#x}")
        return res
    for at, data in writes:
        rom.write(at, data)
    res.allowed, res.written = span, True
    return res


def verify(rom: Rom, p: Plan | None, res: Result) -> list[str]:
    """Read the written lists back through the records and check each against the plan."""
    if p is None or not res.written:
        return []
    alpha = p.alphabet
    if rom.read(alpha.records.at, len(p.records)) != p.records or rom.read(alpha.lists.at, len(p.blob)) != p.blob:
        return ["easy-chat alphabet: the written lists or records do not match the plan"]
    try:
        lists = read_lists(rom, alpha)
    except EasyChatError as exc:
        return [str(exc)]
    fails = []
    words = [w for entries in lists for e in entries for w in e]
    planned = [w for entries in p.lists for e in entries for w in e]
    if sorted(words) != sorted(planned):
        fails.append("easy-chat alphabet: the lists do not hold every planned word once")
    button_of = {c: n + 1 for n, letters in enumerate(p.buttons) for c in letters}
    for n, entries in enumerate(lists):
        if len(entries) > alpha.limit:
            fails.append(f"easy-chat alphabet: record {n} lists more than {alpha.limit} words")
        wrong = [e for e in entries for w in e if button_of.get(p.texts[w][:1].upper(), 0) != n]
        if wrong:
            fails.append(f"easy-chat alphabet: record {n} lists {p.texts[wrong[0][0]]!r} of another button")
    return fails


def report_lines(p: Plan | None, res: Result) -> list[str]:
    if p is None or not res.written:
        return []
    lines = ["", "## Easy-chat alphabet", ""]
    labels = ["others"] + p.buttons
    for n, entries in enumerate(p.lists):
        lines.append(f"- record {n} ({labels[n]}): {len(entries)} words")
    return lines
