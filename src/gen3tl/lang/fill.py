"""Fill name-table catalog entries from the reviewed termbase."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..catalog.extract import english
from ..catalog.po import read, write
from .termbase import Term


@dataclass
class FillReport:
    table: str
    filled: int = 0
    blank: int = 0
    kept: int = 0
    missing: list[str] = field(default_factory=list)
    filled_ids: list[str] = field(default_factory=list)


def field_form(t: Term) -> str:
    return t.label or t.translation


def entry_term(entry, categories: list[str], by_id: dict[str, Term], by_cat: dict) -> Term | None:
    """Resolve reviewed identity before category defaults."""
    bound = {line.removeprefix("filled from glossary ") for line in entry.translator
             if line.startswith("filled from glossary ")}
    if bound:
        if len(bound) != 1:
            raise ValueError(f"{entry.msgctxt}: multiple glossary bindings {sorted(bound)}")
        tid = next(iter(bound))
        term = by_id.get(tid)
        if term is None or term.status != "approved":
            raise ValueError(f"{entry.msgctxt}: glossary binding {tid} is not an approved term")
        if term.source_text != entry.msgid:
            raise ValueError(f"{entry.msgctxt}: glossary binding {tid} source differs from catalog")
        return term
    for category in categories:
        matches = by_cat.get(category, {}).get(entry.msgid, [])
        if len(matches) > 1:
            raise ValueError(f"{entry.msgctxt}: ambiguous glossary identity; bind one of "
                             + ", ".join(term.id for term in matches))
        if matches:
            return matches[0]
    return None


def entry_matcher(entry, matcher):
    """Check the reviewed identity without unrelated homonyms."""
    if matcher is None:
        return None
    term = entry_term(entry, [], matcher.by_id, {})
    return matcher.only([term.id]) if term is not None else matcher


def fill_tables(tables: list[dict], po_root: Path, terms: list[Term], overwrite: bool = False,
                dry_run: bool = False) -> list[FillReport]:
    """Fill the tables' catalog entries; with dry_run, report without writing."""
    by_cat: dict[str, dict[str, list[Term]]] = {}
    by_id = {term.id: term for term in terms}
    for t in terms:
        if t.status != "approved":
            continue
        by_cat.setdefault(t.category, {}).setdefault(t.source_text, []).append(t)
    reports = []
    for tab in tables:
        cats = tab.get("glossary")
        po = po_root / "tables" / f"{tab['name']}.po"
        if not cats or not po.exists():
            continue
        rep = FillReport(tab["name"])
        hdr, entries = read(po)
        english(entries)
        for e in entries:
            if e.msgstr and not overwrite:
                rep.kept += 1
                continue
            if not e.msgid.strip():
                e.msgstr = e.msgid
                e.set_fuzzy(False)
                rep.blank += 1
                rep.filled_ids.append(e.msgctxt)
                continue
            term = entry_term(e, cats, by_id, by_cat)
            if term is None:
                rep.missing.append(f"{e.msgctxt}\t{e.msgid}")
                continue
            e.msgstr = field_form(term)
            e.set_fuzzy(False)
            note = f"filled from glossary {term.id}"
            e.translator = [c for c in e.translator if not c.startswith("filled from glossary ")] + [note]
            rep.filled += 1
            rep.filled_ids.append(e.msgctxt)
        if not dry_run:
            write(po, entries, hdr)
        reports.append(rep)
    return reports


def labels_from_catalog(po_root: Path, terms: list[Term], ids: list[str]) -> list[tuple[str, str, str]]:
    """Copy reviewed table translations back into the glossary label of the terms that filled them."""
    from ..catalog.catalog import load_catalog
    catalog = load_catalog(po_root)
    by_id = {t.id: t for t in terms}
    wanted: dict[str, str] = {}
    for sid in ids:
        p, e = catalog[sid]
        src = next((c.removeprefix("filled from glossary ") for c in e.translator
                    if c.startswith("filled from glossary ")), None)
        if src is None or src not in by_id or not e.msgstr:
            continue
        if wanted.setdefault(src, e.msgstr) != e.msgstr:
            raise ValueError(f"{src}: two field forms, {wanted[src]!r} and {e.msgstr!r}")
    changes = []
    for tid, text in wanted.items():
        t = by_id[tid]
        new = "" if text == t.translation else text
        if new != t.label:
            changes.append((tid, t.label, new))
            t.label = new
    return changes
