"""Merge a catalog across base ROM versions."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from . import entrynotes, keys
from .po import PoEntry, parse
from .po import write as write_po

THRESHOLD = 0.6
OUTCOMES = ("same", "changed", "moved", "similar", "new", "obsolete")

# relative path -> header, active entries, obsolete entries
Files = dict[str, tuple[dict[str, str], list[PoEntry], list[PoEntry]]]


@dataclass
class Match:
    """An outcome; `key` is None for obsolete entries, `old` for new ones."""
    outcome: str
    key: str | None
    old: str | None
    ratio: float | None = None


@dataclass
class Result:
    files: Files
    matches: list[Match] = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)


def load(path: Path) -> Files:
    """A catalog directory (every .po below it) or one PO or POT file."""
    path = Path(path)
    if path.is_dir():
        return {p.relative_to(path).as_posix(): parse(p, notes_path=entrynotes.path(path))
                for p in sorted(path.rglob("*.po"))}
    return {path.name: parse(path)}


def translated(e: PoEntry) -> bool:
    return bool(e.msgstr) or "empty" in e.flags


def _offset(e: PoEntry) -> float:
    """The first reference as an offset; entries without one sort last."""
    for ref in e.references:
        try:
            return int(ref.split()[0], 16)
        except (ValueError, IndexError):
            continue
    return float("inf")


def _entries(files: Files, what: str) -> list[tuple[str, PoEntry]]:
    out, seen = [], set()
    for rel, (_, entries, _) in files.items():
        for e in entries:
            if e.msgctxt in seen:
                raise ValueError(f"{what}: duplicate key {e.msgctxt}")
            seen.add(e.msgctxt)
            out.append((rel, e))
    return out


def _similar(texts: list[str], candidates: list[str], threshold: float) -> list[tuple[float, int, int]]:
    """(ratio, text, candidate) of every pair above the threshold."""
    pairs = []
    matcher = SequenceMatcher(None, autojunk=False)
    for j, text in enumerate(texts):
        matcher.set_seq2(text)
        for i, candidate in enumerate(candidates):
            matcher.set_seq1(candidate)
            # cheap upper bounds first
            if matcher.real_quick_ratio() > threshold and matcher.quick_ratio() > threshold:
                ratio = matcher.ratio()
                if ratio > threshold:
                    pairs.append((ratio, j, i))
    return pairs


def merge(old: Files, new: Files, threshold: float = THRESHOLD, into: str | None = None) -> Result:
    """The source catalog `new` with the entries of the catalog `old` carried over."""
    if not 0 < threshold < 1:
        raise ValueError(f"similarity threshold {threshold} is not between 0 and 1")
    olds = _entries(old, "catalog")
    news = _entries(new, "source")
    active = len(olds)
    known = {e.msgctxt for _, e in olds}
    earlier = []   # obsolete entries, their place among the candidates
    for rel, (_, _, dead) in old.items():
        for e in dead:
            earlier.append((rel, e, None if e.msgctxt in known else len(olds)))
            if e.msgctxt not in known:
                known.add(e.msgctxt)
                olds.append((rel, e))
    pick: dict[int, tuple[str, int, float | None]] = {}
    used: set[int] = set()

    def take(j: int, outcome: str, i: int, ratio: float | None = None) -> None:
        pick[j] = (outcome, i, ratio)
        used.add(i)

    by_key = {e.msgctxt: i for i, (_, e) in enumerate(olds) if keys.is_key(e.msgctxt)}
    for j, (_, e) in enumerate(news):
        if (i := by_key.get(e.msgctxt)) is not None:
            take(j, "same" if olds[i][1].msgid == e.msgid else "changed", i)
    by_text: dict[str, list[int]] = defaultdict(list)
    for i in sorted((i for i, (_, o) in enumerate(olds) if i not in used and o.msgid),
                    key=lambda i: (not translated(olds[i][1]), _offset(olds[i][1]), i)):
        by_text[olds[i][1].msgid].append(i)
    for j in sorted((j for j, (_, e) in enumerate(news) if j not in pick and e.msgid),
                    key=lambda j: (_offset(news[j][1]), j)):
        if by_text.get(news[j][1].msgid):
            take(j, "moved", by_text[news[j][1].msgid].pop(0))
    rest = [j for j, (_, e) in enumerate(news) if j not in pick and e.msgid]
    pool = [i for i, (_, o) in enumerate(olds) if i not in used and o.msgid and translated(o)]
    pairs = _similar([news[j][1].msgid for j in rest], [olds[i][1].msgid for i in pool], threshold)
    for ratio, j, i in sorted(pairs, key=lambda p: (-p[0], p[1], p[2])):
        if rest[j] not in pick and pool[i] not in used:
            take(rest[j], "similar", pool[i], ratio)

    language = next((h["Language"] for h, _, _ in old.values() if h.get("Language")), None)
    identity = next((h["Project-Id-Version"] for h, _, _ in new.values() if h.get("Project-Id-Version")), None)
    files: Files = {rel: ({**header, "Language": language} if language else dict(header), [], [])
                    for rel, (header, _, _) in new.items()}
    result = Result(files)
    for j, (rel, e) in enumerate(news):
        if j not in pick:
            files[rel][1].append(e)
            result.matches.append(Match("new", e.msgctxt, None))
            continue
        outcome, i, ratio = pick[j]
        o = olds[i][1]
        merged = PoEntry(e.msgctxt, e.msgid, o.msgstr, extracted=list(e.extracted), translator=list(o.translator),
                         references=list(e.references), flags=list(o.flags), previous_msgid=o.previous_msgid)
        if outcome in ("changed", "similar") and translated(o):
            # the English the translation answers
            merged.previous_msgid = o.previous_msgid if o.fuzzy and o.previous_msgid is not None else o.msgid
            merged.set_fuzzy(True)
            result.counts["fuzzy"] += 1
        files[rel][1].append(merged)
        result.matches.append(Match(outcome, e.msgctxt, o.msgctxt, ratio))

    def section(rel: str) -> list[PoEntry]:
        target = into or rel
        if target not in files:
            header = old[rel][0]
            files[target] = ({**header, "Project-Id-Version": identity} if identity else dict(header), [], [])
        return files[target][2]

    for rel, o, i in earlier:
        if i not in used:
            section(rel).append(o)
            result.counts["retained"] += 1
    for i, (rel, o) in enumerate(olds[:active]):
        if i not in used:
            section(rel).append(PoEntry(o.msgctxt, o.msgid, o.msgstr, translator=list(o.translator),
                                        flags=list(o.flags), previous_msgid=o.previous_msgid))
            result.matches.append(Match("obsolete", None, o.msgctxt))
    result.counts.update(m.outcome for m in result.matches)
    return result


def write(result: Result, out: Path, single: bool = False, *, notes_path: Path | None = None) -> list[Path]:
    """Write the merged catalog: the one file `out`, else every file below the directory `out`."""
    out = Path(out)
    destination = entrynotes.for_file(out) if single else entrynotes.path(out)
    if destination is not None and destination.exists():
        notes_path = destination
    if single:
        (header, entries, dead), = result.files.values()
        write_po(out, entries, header, dead, notes_path=notes_path)
        return [out]
    for rel, (header, entries, dead) in result.files.items():
        write_po(out / rel, entries, header, dead, notes_path=notes_path)
    return [out / rel for rel in result.files]
