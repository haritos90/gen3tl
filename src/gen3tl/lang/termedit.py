"""Glossary term edits from the editor: apply exported term records and preview their renames."""

from __future__ import annotations

from . import language

import dataclasses
import json

from ..core.charmap import CharmapError
from ..check.layout import lines_of, paragraphs
from ..check.provenance import code as coded
from ..build.release import expand
from .renamecheck import Rename, apply_renames, build_report, grammar, grammar_changes
from .termbase import Senses, Term, TermMatcher

FIELDS = ("translation", "label", "gender", "declension", "forms", "notes", "sources")
RENAMING = ("translation", "label", "gender", "forms")   # fields whose change renames


def values(t: Term) -> dict:
    """The FIELDS of a row; forms as a dict."""
    return {f: dict(t.forms) if f == "forms" else
            t.editable_notes if f == "notes" and t.editable_notes is not None else getattr(t, f) for f in FIELDS}


def changed(t: Term, changes: dict) -> Term:
    """The row with `changes` (FIELDS only)."""
    if t.editable_notes is None and "notes" in changes and changes["notes"] != t.notes:
        raise ValueError(f"{t.id}: edit structured notes in notes/terms.toml")
    result = dataclasses.replace(t, **{k: dict(v) if k == "forms" else v for k, v in changes.items() if k in FIELDS})
    if t.note_ids and t.editable_notes is not None and "notes" in changes:
        result.notes_cell = changes["notes"]
        result.refresh_notes()
    return result


def append_variant(t: Term, replaced: str) -> None:
    """Append a replaced translation with the row's current sources."""
    variant = replaced + (f" @ {t.sources}" if t.sources else "")
    t.variants = " | ".join(x for x in (t.variants, variant) if x)


def rename_of(old: Term, new: Term) -> Rename | None:
    """The rename an edit makes, None where no RENAMING field changes."""
    if new.all_forms() == old.all_forms() and new.gender == old.gender:
        return None
    forms = new.all_forms()
    if "label" in old.all_forms():
        forms.setdefault("label", new.translation)
    return Rename(old, new.translation, forms, new.gender)


def apply_changes(terms: list[Term], records: list[dict]) -> tuple[list[Term], list[Rename], list[str]]:
    """Apply exported term edits and return renames and refusals."""
    at = {t.id: i for i, t in enumerate(terms)}
    out, renames, refused = list(terms), [], []
    for r in records:
        tid, ch, base = r.get("id"), r.get("changes") or {}, r.get("base") or {}
        if tid not in at:
            refused.append(f"{tid}: no such term in the glossary")
            continue
        old = out[at[tid]]
        fields = {f for f in FIELDS}
        cur = values(old)
        why = [f"{k} cannot be edited" for k in ch if k not in fields]
        why += [f"{k} has no base value" for k in ch if k in fields and k not in base]
        why += [f"{k} is {cur[k]!r} in the glossary, the edit was made on {base[k]!r}"
                for k in ch if k in fields and k in base and cur[k] != base[k]]
        note_field = "notes"
        if old.editable_notes is None and note_field in ch and ch[note_field] != old.notes:
            why.append("edit structured notes in notes/terms.toml")
        if why:
            refused.append(f"{tid}: " + "; ".join(why))
            continue
        new = changed(old, ch)
        rn = rename_of(old, new)
        if rn is not None:
            append_variant(new, old.translation)
            renames.append(dataclasses.replace(rn, include=frozenset(r.get("include") or ())))
        out[at[tid]] = new
    return out, renames, refused



def term_row(row: dict) -> Term:
    """A Term from an editor term (terms_data: the glossary columns, translation as translation)."""
    return Term(row["id"], row.get("category", ""), row.get("source_text", ""), row.get("translation", ""),
                label=row.get("label", ""), gender=row.get("gender", ""),
                declension=row.get("declension", "") or "auto", forms=dict(row.get("forms") or {}),
                sources=row.get("sources", ""), variants=row.get("variants", ""),
                confidence=row.get("confidence", ""), address=row.get("address", ""),
                notes=row.get("notes", ""), status=row.get("status", "") or "proposed",
                note_ids=tuple(row.get("note_ids") or ()))


def word_width(checker, text: str) -> int | None:
    """The widest line of a word in the main style, as qa.slot_widths measures it."""
    t = expand(text, checker.version)
    try:
        return max(checker.measure.width(l, checker.cm) for par in paragraphs(t) for l in lines_of(par))
    except CharmapError:
        return None


def _messages(res: dict) -> list[str]:
    return [p["message"] for p in res["problems"]]


@language.selected
def preview(checker, old: dict, new: dict, items: list[dict], slots: dict | None = None,
            scope: dict | None = None) -> dict | None:
    """The renamecheck report of an edit of a term, None where it renames nothing."""
    t0, t1 = term_row(old), term_row(new)
    r = rename_of(t0, t1)
    if r is None:
        return None
    scope = scope or {}
    r = dataclasses.replace(r, include=frozenset(scope.get("include") or ()))
    senses = Senses([t0] + [term_row(x) for x in scope.get("rows") or [] if x.get("id") != t0.id])
    entries = {x["entry"]["id"]: x["entry"] for x in items}
    texts = {x["entry"]["id"]: x["text"] or "" for x in items}
    english = {k: e.get("source_text") or "" for k, e in entries.items()}
    # an entry without English uses the term the index names
    named = set(old.get("in_source") or ())
    read = {k: e["source_text"] if isinstance(e.get("source_text"), str) else t0.source_text if k in named else ""
            for k, e in entries.items()}
    changes = apply_renames({k: v for k, v in texts.items() if v}, [r], read, senses)
    g0 = grammar(t0.all_forms(), t0.gender, t0.declension)
    g1 = grammar(r.forms, t1.gender, t0.declension)
    if not any(c["property"] != "declension" for c in grammar_changes(g0, g1)):
        changes = {k: c for k, c in changes.items() if c["after"] != c["before"]}
    wide: dict[str, dict[str, tuple[int, dict]]] = {}   # line -> slot -> (px, source)
    ph, default = checker.measure.ph, checker.measure.default_ph
    for sid, c in changes.items():
        px = word_width(checker, c["after"])
        for host, name in (slots or {}).get(sid, []):
            w = (entries.get(host) or {}).get("window")
            if px is None or not w:
                continue
            now = (w.get("placeholders") or {}).get(name, ph.get(name, default))
            if px > wide.get(host, {}).get(name, (now, ""))[0]:
                wide.setdefault(host, {})[name] = (px, coded("slot_word", id=sid, text=repr(c["after"])))
    terms = getattr(checker, "terms", [])
    m0 = checker.matcher or TermMatcher([])
    m1 = TermMatcher([t1 if t.id == t1.id else t for t in terms])
    before, after, checked = {}, {}, {}
    for sid in list(changes) + [h for h in wide if h not in changes and texts.get(h)]:
        e = entries[sid]
        e1 = e
        if sid in wide:
            w = e["window"]
            e1 = {**e, "window": {**w, "placeholders": {**(w.get("placeholders") or {}),
                                                        **{k: v[0] for k, v in wide[sid].items()}},
                                  "slot_sources": {**(w.get("slot_sources") or {}),
                                                   **{k: v[1] for k, v in wide[sid].items()}}}}
        before[sid] = _messages(checker.check(e, texts[sid], m0))
        after[sid] = _messages(checker.check(e1, changes[sid]["after"] if sid in changes else texts[sid], m1))
        if sid not in changes:
            checked[sid] = texts[sid]
    return build_report([r], changes, english, {k: e["file"] for k, e in entries.items()}, before, after, checked,
                        senses=senses)


def preview_json(checker, old, new, items, slots=None, scope=None) -> str:
    """preview() for the page: JSON in (text or objects), JSON out ("null": no rename)."""
    load = lambda x: json.loads(x) if isinstance(x, str) else x   # noqa: E731
    return json.dumps(preview(checker, load(old), load(new), load(items), load(slots), load(scope)),
                      ensure_ascii=False)
