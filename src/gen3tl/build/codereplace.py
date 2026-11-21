"""Code replacements: routines of the base ROM replaced by routines in free space."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..core.notes import has_prose
from ..core.charmap import CharmapError
from ..core.rom import ROM_BASE, Rom, RomEvidence

ROM_END = 1 << 25
ALIGN = 4
KEYS = {"name", "routines", "routine", "calls", "evidence"}
SPAN_KEYS = {"guard", "where"}
ROUTINE_KEYS = {"code", "words"}
ROUTINE_OPTIONAL = {"fallback"}
CALL_KEYS = {"guard", "code", "entry"}
WORD = re.compile(r"[a-z][a-z0-9_]*")
SELECTED = "selected_code_replacements"   # name -> word -> text, from the policy


class CodeReplacementError(ValueError):
    pass


@dataclass
class Patch:
    """Code replacing a span; the aligned word after the code holds an address."""
    guard: RomEvidence
    code: bytes
    entry: int = 0        # routine offset the word addresses (calls)

    @property
    def at(self) -> int:
        return self.guard.offset

    def written(self, routine: int) -> bytes:
        """The code and its word for a routine written at `routine`."""
        return self.code + (ROM_BASE + routine + self.entry + 1).to_bytes(4, "little")


def integer(entry: dict, key: str, lo: int, hi: int, what: str, error=CodeReplacementError) -> int:
    value = entry.get(key)
    if type(value) is not int or not lo <= value <= hi:
        raise error(f"{what}: {key} must be an integer in {lo}..{hi}")
    return value


def hexbytes(value, what: str, key: str, error=CodeReplacementError) -> bytes:
    try:
        return bytes.fromhex(value)
    except (TypeError, ValueError):
        raise error(f"{what}: {key} must be hexadecimal") from None


def parse_patch(entry, what: str, keys: set[str], error=CodeReplacementError) -> Patch:
    """A span whose code loads an address from the aligned word after it."""
    if not isinstance(entry, dict) or set(entry) != keys:
        raise error(f"{what} needs exactly {', '.join(sorted(keys))}")
    guard = RomEvidence.parse(entry["guard"], what, error)
    code = hexbytes(entry["code"], what, "code", error)
    at = guard.offset
    # address loaded from the aligned word after it
    if not code or len(code) % 2 or len(code) + 4 != guard.size or (at + len(code)) % 4:
        raise error(f"{what}: code plus an aligned word must fill the original span")
    entry_at = integer(entry, "entry", 0, 1 << 16, what, error) if "entry" in keys else 0
    if entry_at % 2:
        raise error(f"{what}: entry must be a halfword offset")
    return Patch(guard, code, entry_at)


@dataclass
class Routine:
    """A routine of the base ROM that the replacement does not run."""
    guard: RomEvidence
    where: str

    @property
    def at(self) -> int:
        return self.guard.offset

    @property
    def end(self) -> int:
        return self.at + self.guard.size


@dataclass
class CodeReplacement:
    name: str
    routines: list[Routine]
    code: bytes
    words: list[str]
    calls: list[Patch]
    evidence: str
    fallbacks: dict[str, int | dict] = field(default_factory=dict)


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _routine(entry, what: str) -> Routine:
    if not isinstance(entry, dict) or set(entry) != SPAN_KEYS or not _text(entry["where"]):
        raise CodeReplacementError(f"{what}: a routine needs exactly guard and where")
    guard = RomEvidence.parse(entry["guard"], f"{what} routine", CodeReplacementError)
    return Routine(guard, entry["where"])


def parse(entry: dict) -> CodeReplacement:
    """A [[code_replacement]] table -> CodeReplacement, every field checked."""
    if not isinstance(entry, dict) or set(entry) != KEYS:
        raise CodeReplacementError(f"code replacement needs exactly {', '.join(sorted(KEYS))}")
    if not _text(entry["name"]):
        raise CodeReplacementError("code replacement name must be text")
    what = f"code replacement {entry['name']}"
    if not has_prose(entry["evidence"]):
        raise CodeReplacementError(f"{what}: evidence must be text")
    if not isinstance(entry["routines"], list) or not entry["routines"]:
        raise CodeReplacementError(f"{what}: routines must list the replaced routines")
    routines = sorted((_routine(r, what) for r in entry["routines"]), key=lambda r: r.at)
    if any(a.end > b.at for a, b in zip(routines, routines[1:])):
        raise CodeReplacementError(f"{what}: routines overlap")
    routine = entry["routine"]
    if (not isinstance(routine, dict) or not ROUTINE_KEYS <= set(routine) <= ROUTINE_KEYS | ROUTINE_OPTIONAL):
        raise CodeReplacementError(f"{what}: routine needs code, words and optionally fallback")
    code = hexbytes(routine["code"], what, "routine code")
    if not code or len(code) % 4:
        raise CodeReplacementError(f"{what}: routine code must fill whole words")
    words = routine["words"]
    if (not isinstance(words, list) or len(set(words)) != len(words)
            or not all(isinstance(w, str) and WORD.fullmatch(w) for w in words)):
        raise CodeReplacementError(f"{what}: routine words must be distinct lower-case names")
    fallbacks = routine.get("fallback", {})
    if not isinstance(fallbacks, dict) or not set(fallbacks) <= set(words):
        raise CodeReplacementError(f"{what}: fallback must map words to ROM pointers")
    parsed = {}
    variant = None
    for word, fallback in fallbacks.items():
        if type(fallback) is int:
            if not 0 <= fallback < ROM_END:
                raise CodeReplacementError(f"{what}: fallback {word} must be a ROM pointer")
            parsed[word] = fallback
        elif isinstance(fallback, dict):
            if variant is not None:
                raise CodeReplacementError(f"{what}: only one fallback may replace the routine code")
            if set(fallback) != {"at", "code", "words"}:
                raise CodeReplacementError(f"{what}: fallback {word} needs exactly at, code and words")
            at = integer(fallback, "at", 0, ROM_END - 1, f"{what} fallback {word}")
            vcode = hexbytes(fallback["code"], f"{what} fallback {word}", "code")
            if not vcode or len(vcode) % 4:
                raise CodeReplacementError(f"{what}: fallback {word} code must fill whole words")
            variant_words = fallback["words"]
            if (not isinstance(variant_words, list) or len(set(variant_words)) != len(variant_words)
                    or not all(isinstance(v, str) and WORD.fullmatch(v) for v in variant_words)):
                raise CodeReplacementError(f"{what}: fallback {word} words must be distinct lower-case names")
            if variant_words != [v for v in words if v != word]:
                raise CodeReplacementError(f"{what}: fallback {word} words must be the other words in order")
            parsed[word] = {"at": at, "code": vcode, "words": variant_words}
            variant = word
        else:
            raise CodeReplacementError(f"{what}: fallback {word} must be a ROM pointer or a routine variant")
    if not isinstance(entry["calls"], list) or not entry["calls"]:
        raise CodeReplacementError(f"{what}: calls must list the code that reaches the routine")
    calls = sorted((parse_patch(c, f"{what} call", CALL_KEYS) for c in entry["calls"]), key=lambda c: c.at)
    if any(a.at + a.guard.size > b.at for a, b in zip(calls, calls[1:])):
        raise CodeReplacementError(f"{what}: calls overlap")
    for c in calls:
        if c.entry >= len(code):
            raise CodeReplacementError(f"{what}: the call at {c.at:#x} enters past the routine code")
        home = next((r for r in routines if r.at <= c.at and c.at + c.guard.size <= r.end), None)
        if home is None:
            raise CodeReplacementError(f"{what}: the call at {c.at:#x} lies outside the routines")
    idle = [r for r in routines if not any(r.at <= c.at < r.end for c in calls)]
    if idle:
        raise CodeReplacementError(f"{what}: no call replaces code of the routine at {idle[0].at:#x}")
    for word, fallback in parsed.items():
        if isinstance(fallback, dict) and any(c.entry >= len(fallback["code"]) for c in calls):
            raise CodeReplacementError(f"{what}: a call enters past the fallback code of {word}")
    return CodeReplacement(entry["name"], routines, code, list(words), calls, entry["evidence"], parsed)


def from_profile(prof) -> list[CodeReplacement]:
    found = [parse(e) for e in prof.get("code_replacement", [])]
    if len({r.name for r in found}) != len(found):
        raise CodeReplacementError("code replacement names must be distinct")
    return found


def configure(data: dict, selected) -> None:
    """Select the replacements a language policy names, each with the texts of its words."""
    if not isinstance(selected, dict) or not all(isinstance(t, dict) for t in selected.values()):
        raise ValueError("expected a table of word texts per replacement")
    declared = {r.name: r for r in from_profile(data)}
    unknown = sorted(set(selected) - declared.keys())
    if unknown:
        raise ValueError(f"no [[code_replacement]] {unknown[0]} in the profile")
    for name, texts in selected.items():
        rep = declared[name]
        required = set(rep.words) - set(rep.fallbacks)
        given = set(texts)
        if (not given <= set(rep.words) or not required <= given
                or not all(_text(t) for t in texts.values())):
            raise ValueError(f"{name}: needs one text for each word: {', '.join(rep.words) or 'none'}")
    data[SELECTED] = {name: dict(texts) for name, texts in selected.items()}


@dataclass
class Plan:
    replacement: CodeReplacement
    texts: list[bytes | int]     # one encoded, terminated text or fallback word offset per word

    @property
    def size(self) -> int:
        return len(self.replacement.code) + 4 * len(self.texts) \
            + sum(len(t) for t in self.texts if isinstance(t, bytes))

    def words(self, at: int, rom: Rom | None = None) -> tuple[bytes, bytes]:
        """Word pointers and appended texts as written at `at`, with the fallback word's pointer."""
        rep = self.replacement
        text_at = at + len(rep.code) + 4 * len(self.texts)
        words, texts = b"", b""
        for t in self.texts:
            if isinstance(t, int):
                words += (rom.u32(t) if rom else ROM_BASE + t).to_bytes(4, "little")
            else:
                words += (ROM_BASE + text_at + len(texts)).to_bytes(4, "little")
                texts += t
        return words, texts

    def blob(self, at: int, rom: Rom | None = None) -> bytes:
        """The code, its words and the texts as written at `at`."""
        words, texts = self.words(at, rom)
        return self.replacement.code + words + texts


def plan(prof, charmap) -> list[Plan]:
    """The selected replacements of a profile, their texts encoded with `charmap`."""
    chosen = prof.get(SELECTED) or {}
    plans = []
    for rep in from_profile(prof):
        if rep.name not in chosen:
            continue
        texts = chosen[rep.name]
        try:
            code, words = rep.code, rep.words
            for w, fb in rep.fallbacks.items():
                if w not in texts and isinstance(fb, dict):
                    code, words = fb["code"], fb["words"]
                    break
            slots = []
            for w in words:
                if w in texts:
                    slots.append(charmap.encode(texts[w]))
                elif w in rep.fallbacks and type(rep.fallbacks[w]) is int:
                    slots.append(rep.fallbacks[w])
                else:
                    raise CodeReplacementError(f"code replacement {rep.name}: no text for word {w}")
            plans.append(Plan(CodeReplacement(rep.name, rep.routines, code, list(words), rep.calls, rep.evidence),
                              slots))
        except CharmapError as exc:
            raise CodeReplacementError(f"code replacement {rep.name}: {exc}") from None
    return plans


@dataclass
class Placement:
    name: str
    routine: int          # offset of the written code
    size: int             # code, words and texts
    calls: list[int] = field(default_factory=list)


@dataclass
class Result:
    placements: list[Placement] = field(default_factory=list)
    allowed: set[int] = field(default_factory=set)
    failures: list[str] = field(default_factory=list)

    def register(self, guard: "DiffRegistry") -> None:
        """Add this class to the diff-guard registry."""
        guard.add("code replacement", self.allowed, "exclusive")


def apply(rom: Rom, base: Rom, plans: list[Plan], space, taken: set[int] = frozenset()) -> Result:
    """Write every planned replacement into `rom`."""
    res = Result()
    for p in plans:
        rep = p.replacement
        what = f"code replacement {rep.name}"
        stale = [r for r in [*rep.routines, *rep.calls] if not r.guard.matches(base.data)]
        if stale:
            res.failures.append(f"{what}: the base ROM does not hold the declared routine at {stale[0].at:#x}")
            continue
        at = space.alloc(p.size + ALIGN - 1)
        if at is None:
            res.failures.append(f"{what}: no free space for {p.size} bytes")
            continue
        at = -(-at // ALIGN) * ALIGN
        writes = [(at, p.blob(at, rom))] + [(c.at, c.written(at)) for c in rep.calls]
        span = {a + k for a, data in writes for k in range(len(data))}
        clash = span & (set(taken) | res.allowed)
        if clash:
            res.failures.append(f"{what}: its bytes overlap bytes already changed, first at {min(clash):#x}")
            continue
        for a, data in writes:
            rom.write(a, data)
        res.allowed |= span
        res.placements.append(Placement(rep.name, at, p.size, [c.at for c in rep.calls]))
    return res


def verify(rom: Rom, plans: list[Plan], res: Result) -> list[str]:
    """Read back every written replacement: the code, its words and texts, and the calls."""
    fails = []
    by_name = {p.replacement.name: p for p in plans}
    for pl in res.placements:
        p = by_name[pl.name]
        what = f"code replacement {pl.name}"
        if rom.read(pl.routine, pl.size) != p.blob(pl.routine, rom):
            fails.append(f"{what}: the routine at {pl.routine:#x} does not match the plan")
        for c in p.replacement.calls:
            if rom.read(c.at, c.guard.size) != c.written(pl.routine):
                fails.append(f"{what}: the call at {c.at:#x} does not reach the routine")
    return fails


def report_lines(res: Result) -> list[str]:
    if not res.placements:
        return []
    lines = ["", "## Code replacements", ""]
    for pl in res.placements:
        lines.append(f"- {pl.name}: routine at {pl.routine:#x} ({pl.size} bytes), "
                     f"calls at {', '.join(f'{at:#x}' for at in pl.calls)}")
    return lines
