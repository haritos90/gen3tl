"""Merge a researched source table into glossary rows: sources, variants and a note."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .termbase import Term, attach_notes
from . import research


@dataclass
class Summary:
    terms: int = 0
    same_name: int = 0
    other_names: int = 0
    gendered: int = 0
    unknown: list[str] = field(default_factory=list)


def citation_columns(overrides: dict[str, str] | None = None) -> dict[str, str]:
    return {key: key for key in ("source_text", "name", "dub", "gender", "evidence", "source")} | (overrides or {})


def citation_values(finding: research.Row, cols: dict[str, str]) -> dict[str, str]:
    return {key: finding.values.get(column, "").strip() for key, column in cols.items()}


def load(path: Path, columns: dict[str, str] | None = None) -> list[dict]:
    """Rows of a findings TSV with the standard keys english, name, dub, gender, evidence, source."""
    cols = citation_columns(columns)
    rows = []
    for finding in research.load(path).rows:
        row = citation_values(finding, cols)
        row.update(finding=finding, columns=cols)
        rows.append(row)
    return rows


def _split_variants(s: str) -> list[tuple[str, list[str]]]:
    out = []
    for part in [p.strip() for p in s.split("|") if p.strip()]:
        name, _, srcs = part.partition(" @ ")
        out.append((name.strip(), [x.strip() for x in srcs.split(",") if x.strip()]))
    return out


def _join_variants(vs: list[tuple[str, list[str]]]) -> str:
    return " | ".join(f"{n} @ {', '.join(s)}" if s else n for n, s in vs)


def merge(terms: list[Term], rows: list[dict], category: str, label: str) -> Summary:
    """Merge the findings into the glossary rows of `category`."""
    by_en: dict[str, list[Term]] = defaultdict(list)   # forms of one species share the English name
    for t in terms:
        if t.category == category:
            by_en[t.source_text].append(t)
    found: dict[str, list[dict]] = defaultdict(list)
    summ = Summary()
    for r in rows:
        if r["source_text"] in by_en:
            found[r["source_text"]].append(r)
        elif r["source_text"] not in summ.unknown:
            summ.unknown.append(r["source_text"])
    for en, rs in found.items():
        if any(t.note_ids for t in by_en[en]) or any(r.get("finding") and r["finding"].note_ids for r in rs):
            if any(not r.get("finding") or not r["finding"].record_id for r in rs):
                raise ValueError(f"{en}: structured citations require persistent research identities")
    for en, rs in found.items():
        for t in by_en[en]:
            _merge_one(t, rs, label, summ)
    return summ


def _merge_one(t: Term, rs: list[dict], label: str, summ: Summary) -> None:
    summ.terms += 1
    names: dict[str, list[dict]] = defaultdict(list)
    for r in rs:
        if r["name"] and "(" not in r["name"]:  # annotated forms are not names
            names[r["name"]].append(r)
    sources = [s.strip() for s in t.sources.split(",") if s.strip()]
    variants = _split_variants(t.variants)
    for name, nrs in names.items():
        keys = list(dict.fromkeys(r["source"] for r in nrs))
        if name == t.translation:
            summ.same_name += 1
            sources += [k for k in keys if k not in sources]
        else:
            summ.other_names += 1
            for i, (vn, vs) in enumerate(variants):
                if vn == name:
                    variants[i] = (vn, vs + [k for k in keys if k not in vs])
                    break
            else:
                variants.append((name, keys))
    if any(r["gender"] in ("m", "f") for r in rs):
        summ.gendered += 1
    if t.note_ids or any(r.get("finding") and r["finding"].note_ids for r in rs):
        if t.editable_notes is not None:
            cell = replace_segment(t.editable_notes, label)
            if t.note_ids:
                t.notes_cell = cell
            else:
                t.notes = cell
        records = [research.term_note(t.id, r["finding"], "research-citation", [],
                                     citation={"label": label, "columns": r.get("columns", citation_columns())}) for r in rs]
        related = tuple(note for r in rs for note in r["finding"].note_records)
        attach_notes(t, records, related, {r["finding"].record_id: r["finding"] for r in rs})
    else:
        t.notes = replace_segment(t.notes, label, _summary(rs, label))
    t.sources = ", ".join(sources)
    t.variants = _join_variants(variants)


def replace_segment(notes: str, label: str, summary: str = "") -> str:
    """Replace labelled segments without altering independent prose."""
    parts = []
    replaced = False
    for part in notes.split(" | "):
        if part.startswith(f"{label}: "):
            if not replaced and summary:
                parts.append(summary)
            replaced = True
        elif part or not summary:
            parts.append(part)
    if not replaced and summary:
        parts.append(summary)
    return " | ".join(parts)


def _summary(rs: list[dict], label: str) -> str:
    names: dict[str, list[dict]] = defaultdict(list)
    for r in rs:
        if r["name"] and "(" not in r["name"]:
            names[r["name"]].append(r)
    parts = []
    for name, nrs in names.items():
        keys = list(dict.fromkeys(r["source"] for r in nrs))
        parts.append(f"{name} ({', '.join(keys)}; {nrs[0]['dub'].split(' (')[0]})")
    genders = []
    for g in ("m", "f"):
        gr = [r for r in rs if r["gender"] == g]
        if gr:
            e = gr[0]
            n = len({r["evidence"] for r in gr})  # Count identical quotations once.
            genders.append(f"{g} ×{n} ({e['source']} {e['dub'].split(' (')[0]} {e['evidence']})")
    seg = f"{label}: " + ("; ".join(parts) if parts else "no nominative attested")
    seg += "; gender " + (", ".join(genders) if genders else "not shown")
    return seg
