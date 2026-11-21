"""Assemble the glossary of a language from its research tables, one row per category and English name."""

from __future__ import annotations

import re
from pathlib import Path

from .termbase import Term, attach_notes
from . import language, research as findings

CATEGORY = {
    "moves": "move", "items": "item", "abilities": "ability", "types": "type", "natures": "nature",
    "trainer_classes": "trainer-class", "general": "term", "species": "species",
    "characters": "character", "locations": "location", "proper-nouns": "proper-noun",
    "generic-speakers": "speaker", "dex-categories-chosen": "dex-category",
}


def slug(s: str) -> str:
    s = s.replace("é", "e").replace("♂", " m").replace("♀", " f")
    s = re.sub(r"\{[^}]*\}", "pkmn", s)
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "x"


def declension_for(category: str, translated: str) -> str:
    provider = language.current().provider()
    return provider.declension_for(category, translated) if provider else "manual"


def assemble(research: Path, only: str | None = None) -> tuple[list[Term], list[str]]:
    """Assemble glossary terms and report invalid research rows."""
    terms: list[Term] = []
    findings.index(research)
    seen: dict[str, int] = {}
    problems: list[str] = []
    for stem, cat in CATEGORY.items():
        if only is not None and stem != only:
            continue
        f = research / f"{stem}.tsv"
        if not f.exists():
            continue
        for finding in findings.load(f).rows:
            row = finding.values
            en = (row.get("source_text") or "").strip()
            translated = (row.get("chosen") or "").strip()
            if not en or not translated:
                problems.append(f"{stem}: empty source_text or chosen in {row}")
                continue
            declension = (row.get("declension") or "").strip()
            if declension not in ("", "auto", "fixed", "manual"):
                raise ValueError(f"{stem}: {en}: invalid declension {declension!r}")
            base = f"{cat}:{slug(en)}"
            n = seen.get(base, 0)
            seen[base] = n + 1
            tid = base if n == 0 else f"{base}~{n + 1}"
            label = (row.get("label") or "").strip()
            if (row.get("fits") or "").strip() == "no" and (row.get("short_form") or "").strip():
                label = (row.get("short_form") or "").strip()  # Use the fitting field form.
            notes = (row.get("notes") or "").strip()
            extra = findings.assembly_facts(finding)
            term = Term(
                id=tid, category=cat, source_text=en, translation=translated, label=label,
                gender=(row.get("gender") or "").strip(),
                declension=declension or declension_for(cat, translated),
                sources=(row.get("sources") or "").strip(),
                variants=(row.get("variants") or "").strip(),
                confidence=(row.get("confidence") or "").strip(),
                address=(row.get("address_to_player") or "").strip(),
                notes="; ".join([notes] + extra) if extra else notes,
                status="proposed")
            if finding.note_ids:
                term.notes = ""
                record = findings.term_note(tid, finding, "research-assembly",
                                            [] if extra else ["Terminology follows the linked research record."])
                attach_notes(term, [record], finding.note_records, {finding.record_id: finding})
            terms.append(term)
    return terms, problems
