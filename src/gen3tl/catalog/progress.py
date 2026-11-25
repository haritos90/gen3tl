"""Translation progress per topic of the catalog, with the graphics that carry text."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .catalog import load_catalog, never_shown_evidence, nothing_to_translate

TABLE_TOPICS = {
    "species": "Species names", "moves": "Move names", "items": "Item names",
    "abilities": "Ability names", "types": "Type names", "types_short": "Type names",
    "trainer_classes": "Trainer classes", "trainer_names": "Trainer names",
    "pokedex_categories": "Pokedex categories", "naming_keyboard": "Naming keyboard",
}
GROUP_TOPICS = {"maps": "Map dialogue", "script": "Script text", "battle": "Battle messages"}
INTERFACE = "Interface strings"
GRAPHICS = "Graphics with text"
OTHER = "System data, other"


@dataclass
class Row:
    topic: str
    total: int = 0
    approved: int = 0
    review: int = 0
    words: int = 0
    words_approved: int = 0
    never: int = 0          # left out, never shown


def file_topics(prof: dict) -> dict[str, str]:
    """System or table PO file stem -> topic, from the profile's [progress] topics list."""
    out = {}
    for t in prof.get("progress", {}).get("topics", []):
        for f in t["files"]:
            out[f] = t["topic"]
    return out


def topic_of(rel: Path, extracted: list[str], files: dict[str, str]) -> str:
    group = rel.parts[0] if len(rel.parts) > 1 else rel.stem
    if group == "tables":   # the profile's topic, then the generic name
        return files.get(rel.stem, TABLE_TOPICS.get(rel.stem, rel.stem))
    if group in GROUP_TOPICS:
        return GROUP_TOPICS[group]
    if rel.stem in files:
        return files[rel.stem]
    if any(c.startswith("category: code") for c in extracted):
        return INTERFACE
    return OTHER


def graphics_row(prof) -> Row | None:
    """Translate-category graphics; done when a profile edit draws them."""
    gfx = prof.get("graphics", {}) or {}
    inv = gfx.get("inventory")
    if not inv or not Path(inv).exists():
        return None
    from ..build.graphics import GraphicsConfig, inventory_of, inventory_rows
    rows = inventory_rows(inv)
    want = {r["id"] for r in rows if r.get("category") == "translate"}
    edited = {e.get("id") for e in gfx.get("edit", [])}
    cfg = GraphicsConfig({"sheet_width": gfx.get("sheet_width", {})}, Path.cwd(), inventory_of(rows))
    done = (edited | cfg.shared_edits(edited).keys()) & want
    return Row(GRAPHICS, total=len(want), approved=len(done))


def in_review(batch_root: Path) -> set[str]:
    """Ids exported in batches that are not approved yet."""
    ids = set()
    for src in sorted(batch_root.glob("*/source.json")):
        st = src.parent / "status.json"
        if st.exists() and json.loads(st.read_text(encoding="utf-8")).get("approved"):
            continue
        ids |= {x["id"] for x in json.loads(src.read_text(encoding="utf-8"))}
    return ids


def count(po_root: Path, files: dict[str, str], review_ids: set[str] | None = None,
          study: dict | None = None) -> list[Row]:
    """One progress row per topic."""
    rows: dict[str, Row] = {}
    for sid, (path, e) in load_catalog(po_root).items():
        r = rows.setdefault(t := topic_of(Path(path).relative_to(po_root), e.extracted, files), Row(t))
        if never_shown_evidence(sid, e.extracted, study) is not None:   # out of the estimate
            r.never += 1
            continue
        if nothing_to_translate(e):   # an unused slot
            continue
        w = len(e.msgid.split())
        r.total += 1
        r.words += w
        if (e.msgstr or "empty" in e.flags) and not e.fuzzy:
            r.approved += 1
            r.words_approved += w
        elif e.fuzzy or sid in (review_ids or set()):
            r.review += 1
    # running text first, then file-map topics, then name tables.
    # name-table topics sort with their table
    order = list(dict.fromkeys(list(GROUP_TOPICS.values()) + [INTERFACE]
                               + sorted(set(files.values()) - set(TABLE_TOPICS.values()))
                               + [OTHER] + list(TABLE_TOPICS.values())))
    return sorted(rows.values(), key=lambda r: (order.index(r.topic) if r.topic in order else len(order), r.topic))


def pct(a: int, b: int) -> str:
    """Rounded down, as the editor page shows it."""
    return f"{100 * a // b}%" if b else "-"


def row_cells(row: Row) -> list[str]:
    return [row.topic, str(row.approved), str(row.review), str(row.total),
            pct(row.approved, row.total), pct(row.words_approved, row.words), str(row.never)]


def cells(rows: list[Row]):
    """Translation columns and their overall totals."""
    headers = ["Topic", "Approved", "Translated or in review", "Total", "Done", "Words done", "Never shown"]
    body, total = [], Row("Overall")
    for row in rows:
        body.append(row_cells(row))
        for field in ("total", "approved", "review", "words", "words_approved", "never"):
            setattr(total, field, getattr(total, field) + getattr(row, field))
    return headers, body, row_cells(total)


SHORT = ("Topic", "Done")


def short(headers: list[str], body: list[list[str]], overall: list[str]):
    """Keep the default translation columns."""
    keep = [i for i, header in enumerate(headers) if header in SHORT]

    def pick(row):
        return [row[i] for i in keep]

    return pick(headers), [pick(row) for row in body], pick(overall)


def markdown(headers: list[str], body: list[list[str]], overall: list[str], after: list[list[str]] = ()) -> str:
    """`after`: rows below the overall line, outside its totals."""
    lines = ["| " + " | ".join(headers) + " |", "|---|" + "---:|" * (len(headers) - 1)]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    lines.append("| **" + overall[0] + "** | " + " | ".join(overall[1:]) + " |")
    lines += ["| " + " | ".join(r) + " |" for r in after]
    return "\n".join(lines)


def draw(headers: list[str], body: list[list[str]], overall: list[str]) -> str:
    """Aligned table with box-drawing borders for a terminal."""
    widths = [max(len(r[i]) for r in [headers] + body + [overall]) for i in range(len(headers))]

    def row(r):
        return "│ " + " │ ".join(c.ljust(w) if i == 0 else c.rjust(w)
                                 for i, (c, w) in enumerate(zip(r, widths))) + " │"

    def rule(left, mid, right):
        return left + mid.join("─" * (w + 2) for w in widths) + right
    return "\n".join([rule("┌", "┬", "┐"), row(headers), rule("├", "┼", "┤")] + [row(r) for r in body]
                     + [rule("├", "┼", "┤"), row(overall), rule("└", "┴", "┘")])


def table(rows: list[Row]) -> str:
    """Markdown table with an overall line."""
    return markdown(*cells(rows))
