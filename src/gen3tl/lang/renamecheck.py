"""Checks of a glossary term rename: the problems a rename adds to the lines it changes."""

from __future__ import annotations

from . import language

import re
from collections import Counter
from dataclasses import dataclass

from ..check.provenance import formatted, is_warning
from .termbase import FORM_ONLY, Senses, Term, rename_in_text

FORMAT = "gen3tl-term-rename"
VERSION = "2.0.0"


@dataclass
class Rename:
    """One term of a rename: the glossary row before it, the new term and its forms."""
    old: Term
    translation: str
    forms: dict[str, str]
    gender: str | None = None   # the replacement row's; None keeps the current
    include: frozenset[str] = frozenset()   # entries whose form-only occurrences it renames


def apply_renames(texts: dict[str, str], renames: list[Rename], english: dict[str, str] | None = None,
                  senses: Senses | None = None, named=None) -> dict[str, dict]:
    """Entry id -> {before, after, steps} for every text a rename changes; renames apply in order."""
    out = {}
    for sid, text in texts.items():
        cur, steps = text, []
        for r in renames:
            new, n, amb = rename_in_text(cur, r.old, r.forms, None if english is None else english.get(sid, ""),
                                         senses, sid in r.include, None if named is None else named(sid, r.old.id))
            if n:
                steps.append({"term": r.old.id, "text": cur, "ambiguous": amb})
                cur = new
        if steps:
            out[sid] = {"before": text, "after": cur, "steps": steps}
    return out


def left_out(texts: dict[str, str], renames: list[Rename], english: dict[str, str],
             senses: Senses) -> dict[str, list[dict]]:
    """Entry id -> [{term, match}]: the form-only occurrences a rename leaves out."""
    out: dict[str, list[dict]] = {}
    for sid, text in texts.items():
        for r in renames:
            if sid in r.include:
                continue
            for _, match, _, kind in senses.uses(text, r.old, english.get(sid, "")):
                if kind == FORM_ONLY:
                    out.setdefault(sid, []).append({"term": r.old.id, "match": match})
    return out



QUOTED = r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\""


def problem_key(p: str | dict) -> str:
    """The kind of a check message, a text or a code: numbers and quoted excerpts left out."""
    text = formatted(p)
    m = re.match(r"glossary term (" + QUOTED + ")", text)
    if m:
        return "glossary term " + m.group(1)
    return re.sub(QUOTED, "'…'", re.sub(r"\d+", "N", text))


def severity(p: str | dict) -> str:
    return "warning" if (is_warning(p) if isinstance(p, dict) else "(warning)" in p) else "error"


def added_problems(before: list, after: list) -> list[dict]:
    """The after messages no before message of the same kind pairs with, as {message, severity}."""
    left = Counter(problem_key(p) for p in before)
    out = []
    for p in after:
        k = problem_key(p)
        if left[k]:
            left[k] -= 1
        else:
            out.append({"message": p, "severity": severity(p)})
    return out


# grammar and agreement come from the language pack

def grammar(forms, gender="", declension="", morph=None):
    return language.current().grammar(forms, gender, declension, morph)


def grammar_changes(old: dict, new: dict) -> list[dict]:
    """Known grammatical properties that change between terms."""
    out = []
    for key in ("gender", "number", "declension", "animacy"):
        if key == "gender" and "plur" in (old["number"], new["number"]):
            continue
        if old[key] and new[key] and old[key] != new[key]:
            out.append({"property": key, "old": old[key], "new": new[key]})
    return out


def describe(grammar):
    provider = language.current().agreement_provider()
    return provider.describe(grammar) if provider else "grammar not inferred"


def agreement(*args, **kwargs):
    provider = language.current().agreement_provider()
    return provider.agreement(*args, **kwargs) if provider else []



def build_report(renames: list[Rename], changes: dict[str, dict], english: dict[str, str], files: dict[str, str],
                 before: dict[str, list[str]], after: dict[str, list[str]], checked: dict[str, str] | None = None,
                 morph=None, senses: Senses | None = None) -> dict:
    """The report of a rename (FORMAT, VERSION)."""
    gram = {}
    head = []
    for r in renames:
        g0 = grammar(r.old.all_forms(), r.old.gender, r.old.declension, morph)
        g1 = grammar(r.forms, r.old.gender if r.gender is None else r.gender, r.old.declension, morph)
        gram[r.old.id] = (g0, g1, grammar_changes(g0, g1))
        head.append({"term": r.old.id, "source_text": r.old.source_text, "old": r.old.translation, "new": r.translation,
                     "forms": r.forms, "lines": sum(1 for c in changes.values()
                                                    if any(s["term"] == r.old.id for s in c["steps"])),
                     "grammar": {"old": g0, "new": g1, "changes": gram[r.old.id][2]}})
    by_id = {r.old.id: r for r in renames}
    entries = []
    for sid in sorted(set(changes) | set(checked or {})):
        c = changes.get(sid)
        added = added_problems(before.get(sid, []), after.get(sid, []))
        if c is None:
            if added:
                text = (checked or {})[sid]
                entries.append({"id": sid, "file": files.get(sid, ""), "source_text": english.get(sid, ""),
                                "before": text, "after": text, "changed": False, "terms": [], "ambiguous": [],
                                "added": added, "agreement": []})
            continue
        agree = []
        for s in c["steps"]:
            g0, g1, ch = gram[s["term"]]
            if any(x["property"] != "declension" for x in ch):
                r = by_id[s["term"]]
                agree += agreement(s["text"], r.old, g0, g1, morph, None if senses is None else english.get(sid, ""),
                                   senses, sid in r.include)
        entries.append({"id": sid, "file": files.get(sid, ""), "source_text": english.get(sid, ""),
                        "before": c["before"], "after": c["after"], "changed": True,
                        "terms": [s["term"] for s in c["steps"]],
                        "ambiguous": [a for s in c["steps"] for a in s["ambiguous"]],
                        "added": added, "agreement": agree})
    errors = [e for e in entries if any(p["severity"] == "error" for p in e["added"])]
    warnings = [e for e in entries if any(p["severity"] == "warning" for p in e["added"])]
    summary = {"changed": len(changes), "limit_checked": len(checked or {}),
               "errors": sum(1 for e in entries for p in e["added"] if p["severity"] == "error"),
               "error_lines": len(errors),
               "warnings": sum(1 for e in entries for p in e["added"] if p["severity"] == "warning"),
               "warning_lines": len(warnings),
               "agreement_lines": sum(1 for e in entries if e["agreement"]),
               "ambiguous_lines": sum(1 for e in entries if e["ambiguous"]),
               "blocked": bool(errors)}
    return {"format": FORMAT, "version": VERSION, "renames": head, "summary": summary, "entries": entries}


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def markdown(report: dict) -> str:
    """The readable form of a report."""
    s = report["summary"]
    lines = ["# Term rename check", "", "| Term | Old | New | Lines | Grammar |", "|---|---|---|---|---|"]
    for r in report["renames"]:
        g = r["grammar"]
        if g["changes"]:
            gr = "changes: " + "; ".join(f"{c['property']} {c['old']} -> {c['new']}" for c in g["changes"]) + \
                 f" ({describe(g['old'])} -> {describe(g['new'])})"
        else:
            gr = "unchanged: " + describe(g["old"])
        lines.append(f"| {r['term']} | {_cell(r['old'])} | {_cell(r['new'])} | {r['lines']} | {_cell(gr)} |")
    lines += ["", f"Lines changed: {s['changed']}; unchanged lines checked for a changed limit: "
              f"{s['limit_checked']}. New errors: {s['errors']} in {s['error_lines']} lines; new warnings: "
              f"{s['warnings']} in {s['warning_lines']} lines; lines to check for agreement: "
              f"{s['agreement_lines']}; lines with ambiguous forms: {s['ambiguous_lines']}.", "",
              "Result: " + ("blocked: the rename adds errors; lang rename --write refuses it without "
                            "--allow-errors." if s["blocked"] else "no new error.")]
    for sev, title in (("error", "New errors"), ("warning", "New warnings")):
        lines += ["", f"## {title}", ""]
        rows = [e for e in report["entries"] if any(p["severity"] == sev for p in e["added"])]
        for e in rows:
            lines.append(f"- {e['id']} ({e['file']})" + ("" if e["changed"] else ", limit changed"))
            lines += [f"  - {formatted(p['message'])}" for p in e["added"] if p["severity"] == sev]
            lines += [f"  - before: {e['before']}", f"  - after: {e['after']}"]
        if not rows:
            lines.append("None.")
    lines += ["", "## Agreement to check", "",
              "Words around an occurrence that may agree with the old gender, number or animacy "
              "(a heuristic: check each line).", ""]
    rows = [(e, a) for e in report["entries"] for a in e["agreement"]]
    if rows:
        lines += ["| Line | Term | Form | Old | Words | After |", "|---|---|---|---|---|---|"]
        lines += [f"| {e['id']} | {a['term']} | {a['form']} | {_cell(a['match'])} | {_cell(', '.join(a['words']))} "
                  f"| {_cell(e['after'])} |" for e, a in rows]
    else:
        lines.append("None.")
    lines += ["", "## Ambiguous forms", "",
              "An old form that stands for several cases whose new forms differ: the nominative-first choice "
              "was taken.", ""]
    rows = [e for e in report["entries"] if e["ambiguous"]]
    if rows:
        lines += ["| Line | Choices | After |", "|---|---|---|"]
        lines += [f"| {e['id']} | {_cell('; '.join(e['ambiguous']))} | {_cell(e['after'])} |" for e in rows]
    else:
        lines.append("None.")
    lines += ["", "## Changed lines", "", "| Line | Before | After | New problems |", "|---|---|---|---|"]
    lines += [f"| {e['id']} | {_cell(e['before'])} | {_cell(e['after'])} | "
              f"{len(e['added']) or ''} |" for e in report["entries"] if e["changed"]]
    return "\n".join(lines) + "\n"
