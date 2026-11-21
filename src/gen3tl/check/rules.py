"""The translation rules of each entry, with their source and evidence."""

from __future__ import annotations

import csv
import json
import tomllib
from pathlib import Path

from ..core import notes as structured
from ..lang import language
from ..lang.language import save_link_keys

LIST_COLUMNS = ("ids", "source_text", "rule", "source")
HOSTS_SHOWN = 4   # ids named per inserted-word rule


def rule(text: str, source: str, evidence: str = "") -> dict:
    """A rule as the editor shows it: the text, where it comes from, its evidence."""
    return {"text": text, "source": source, "evidence": evidence}


def _ids(ids: list[str]) -> str:
    """Ids for a rule's text: the first HOSTS_SHOWN, then the count of the rest."""
    head = ", ".join(ids[:HOSTS_SHOWN])
    return head if len(ids) <= HOSTS_SHOWN else f"{head} and {len(ids) - HOSTS_SHOWN} more"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def save_link_rules(prof, inv: dict) -> dict[str, list[dict]]:
    """The save-link flag, with the profile key that sets it."""
    tables = {t["name"]: t for t in prof.get("table", [])}
    keys = {sid: key for key in save_link_keys(getattr(prof, "language", None))
            for sid in prof.get("strings", {}).get(key, [])}
    text = ("Copied into save or link data: translated like all other text; in trades and link play with "
            "the original ROM, unsupported glyphs may display incorrectly.")
    out = {}
    for e in inv["entries"]:
        if "save-link" not in e["flags"]:
            continue
        t = tables.get(e.get("table")) if e["kind"] == "fixed" else None
        if t is not None and t.get("save_link"):
            out[e["id"]] = [rule(text, f"profile [[table]] {t['name']} save_link", t.get("evidence", ""))]
        else:
            out[e["id"]] = [rule(text, f"profile [strings] {keys.get(e['id'], 'save_link')}")]
    return out


def computed_rules(prof, inv: dict) -> dict[str, list[dict]]:
    """[[strings.computed]] groups: members in ROM order, each keeping within the gap to the next."""
    by_id = {e["id"]: e for e in inv["entries"]}
    out: dict[str, list[dict]] = {}
    for d in prof.get("strings", {}).get("computed", []):
        members = sorted((by_id[s] for s in d["ids"] if s in by_id), key=lambda e: e["offset"])
        source = f"profile [[strings.computed]] 0x{d['pointer']:X}"
        for e, nxt in zip(members, members[1:] + [None]):
            others = ", ".join(m["id"] for m in members if m is not e)
            head = (f"Moves with {others} as one block: code reaches the group by pointer arithmetic "
                    f"(pool word 0x{d['pointer']:X}).")
            if nxt is None:
                tail = " The last member: no byte gap binds it."
            else:
                gap = nxt["offset"] - e["offset"]
                tail = (f" At most {gap - 1} bytes, the gap to {nxt['id']} less the terminator; "
                        f"a longer translation stays English.")
            out.setdefault(e["id"], []).append(rule(head + tail, source, d.get("evidence", "")))
    return out


def interior_rules(prof, limits: dict) -> dict[str, list[dict]]:
    """[[strings.interior]]: the head and slot of the limit (qa.interior_heads)."""
    out = {}
    for d in prof.get("strings", {}).get("interior", []):
        lim = limits.get(d["id"])
        if lim is None or lim.head is None:
            continue
        out[d["id"]] = [rule(f"Begins with {lim.head}, the first {d['offset']} bytes of the English; code reads "
                             f"the text from byte {d['offset']} (pool word 0x{d['pointer']:X}); "
                             f"kept in place: at most {lim.bytes} bytes.",
                             f"profile [[strings.interior]] 0x{d['pointer']:X}", d.get("evidence", ""))]
    return out


def code_char_rules(prof, inv: dict) -> dict[str, list[dict]]:
    """Fields of an unterminated [[table]] with a pad byte: character codes in game code."""
    tables = {t["name"]: t for t in prof.get("table", []) if "pad" in t and not t.get("terminated", True)}
    out = {}
    for e in inv["entries"]:
        t = tables.get(e.get("table")) if e["kind"] == "fixed" else None
        if t is None:
            continue
        n, pad = t["field_length"], t["pad"]
        end = ", the terminator" if pad == 0xFF else ""
        out[e["id"]] = [rule(f"Character code written by game code: at most {_plural(n, 'character')}; only "
                             f"that byte changes; empty writes 0x{pad:02X}{end}.",
                             f"profile [[table]] {t['name']}", t.get("evidence", ""))]
    return out


def code_fill_rules(prof, inv: dict, catalog: dict, tokens: dict | None = None) -> dict[str, list[dict]]:
    """[[layout.code_fill]]: the placeholders of an entry's English the code fills, by the rule's ids and table."""
    from .qa import code_fills
    by_id, by_at = code_fills(prof.get("layout", {}))
    out: dict[str, list[dict]] = {}
    for e in inv["entries"]:
        if e["id"] not in catalog:
            continue
        english = "".join((tokens or {})[e["id"]]) if catalog[e["id"]][1].keyed else catalog[e["id"]][1].msgid
        found = by_id.get(e["id"], []) + [r for x in e["refs"] for r in by_at.get(x["at"], [])]
        for r in [r for i, r in enumerate(found) if all(r is not x for x in found[:i])]:
            for name, s in r["slots"].items():
                if "{%s}" % name not in english:
                    continue
                source = prof.get("layout", {}).get("code_slot_source", {}).get(r.get("name"), {}).get(name)
                if source:
                    label = prof["layout"]["fill_source"][source]["label"]
                    what = f"text measured from {label.lower()}"
                elif "strings" in s:   # bounded sources have no fixed width
                    count = len(s["strings"]) + len(s.get("originals", []))
                    what = f"one of {_plural(count, 'source string')}, measured at their widest rendering"
                else:
                    what = (f"a number of {_plural(int(s['digits']), 'digit')}" if "digits" in s
                            else f"text {s['width']} px wide")
                where = f" ({r['where']})" if r.get("where") else ""
                identity = r.get("name") or (f"0x{r['base']:X}" if "base" in r else r["ids"][0])
                source = f"profile [[layout.code_fill]] {identity}"
                if r.get("where"):
                    source += f": {r['where']}"
                out.setdefault(e["id"], []).append(
                    rule(f"The code fills {{{name}}} with {what}{where}.", source, r["evidence"]))
    return out


def droppable_rules(prof) -> dict[str, list[dict]]:
    """[[qa.droppable]]: the tokens a translation may leave out."""
    out: dict[str, list[dict]] = {}
    for d in prof.get("qa", {}).get("droppable", []):
        for sid in d["ids"]:
            out.setdefault(sid, []).append(rule(f"May leave out {' '.join(d['tokens'])}: the code fills it with "
                                                "text only the English needs.", "profile [[qa.droppable]]",
                                                d.get("evidence", "")))
    return out


def pointer_table_rules(prof, inv: dict) -> dict[str, list[dict]]:
    """The `note` of a [[strings.pointer_table]], on each string of its table."""
    notes = {t["name"]: t for t in prof.get("strings", {}).get("pointer_table", []) if t.get("note")}
    out = {}
    for e in inv["entries"]:
        t = notes.get(e.get("table")) if e["kind"] == "pointer" else None
        if t is not None:
            out[e["id"]] = [rule(t["note"], f"profile [[strings.pointer_table]] {t['name']}", t.get("evidence", ""))]
    return out


def profile_rules(prof, inv: dict, catalog: dict, limits: dict, tokens: dict | None = None) -> dict[str, list[dict]]:
    """Entry id -> the rules generated from the profile, in the order of `parts`."""
    parts = [save_link_rules(prof, inv), computed_rules(prof, inv), interior_rules(prof, limits),
             code_char_rules(prof, inv), code_fill_rules(prof, inv, catalog, tokens), droppable_rules(prof),
             pointer_table_rules(prof, inv)]
    out: dict[str, list[dict]] = {}
    for part in parts:
        for sid, rs in part.items():
            out.setdefault(sid, []).extend(rs)
    return out


def context_rules(ctx: dict, reach: dict) -> tuple[list[dict], list[dict]]:
    """The rules of an entry's context and reach: the never-shown rule and those of the inserted words."""
    first, last = [], []
    if reach["class"] == "never shown":
        first.append(rule("The game never shows this text: it is not translated, and an approved translation "
                          "stays.", reach["source"], reach["evidence"]))
    slots: dict[str, list[str]] = {}
    for h in ctx["hosts"]:
        slots.setdefault(h["slot"], []).append(h["id"])
    for slot, ids in slots.items():
        last.append(rule(f"Inserted as {slot} into {_plural(len(ids), 'text')} ({_ids(ids)}): one form serves "
                         "every sentence it fills.", "catalog context"))
    slots = {}
    for w in ctx["words"]:
        slots.setdefault(w["slot"], []).append(w["id"])
    for slot, ids in slots.items():
        last.append(rule(f"{slot} takes {_plural(len(ids), 'inserted word')} ({_ids(ids)}): the sentence is "
                         "built around their fixed forms.", "catalog context"))
    return first, last


def list_path(folder: Path) -> Path:
    """Select one canonical rule list, retaining legacy projects."""
    folder = Path(folder)
    current, legacy = folder / "rules.toml", folder / "rules.tsv"
    if current.exists() and legacy.exists():
        raise ValueError(f"{folder}: both legacy and structured rule lists")
    return current if current.exists() else legacy


def _validate_list(document: dict, code: str) -> list[dict]:
    if not isinstance(document, dict) or set(document) != {"schema", "rule"}:
        raise ValueError("wording rules: expected schema and rule array")
    if document["schema"] != structured.SCHEMA:
        raise ValueError(f"wording rules: unsupported schema; expected {structured.SCHEMA}")
    rows = document["rule"]
    if not isinstance(rows, list):
        raise ValueError("wording rules: expected rule array")
    identities, notes = set(), []
    for index, row in enumerate(rows):
        where = f"wording rules[{index}]"
        if (not isinstance(row, dict) or "id" not in row
                or row.keys() - {"id", "ids", "source_text", "rule", "source", "notes"}):
            raise ValueError(f"{where}: expected id, selectors and prose or notes")
        rid = row["id"]
        if (not isinstance(rid, str) or not rid or any(char.isspace() for char in rid)):
            raise ValueError(f"{where}: invalid rule id")
        if rid in identities:
            raise ValueError(f"{where}: duplicate rule {rid}")
        identities.add(rid)
        ids, english = row.get("ids", []), row.get("source_text", "")
        if (not isinstance(ids, list) or any(not isinstance(sid, str) or not sid
                                           or any(char.isspace() for char in sid) for sid in ids)
                or not isinstance(english, str) or bool(ids) == bool(english)):
            raise ValueError(f"{where}: expected ids or an English text")
        if len(set(ids)) != len(ids):
            raise ValueError(f"{where}: duplicate entry selector")
        if "notes" not in row:
            if any(not isinstance(row.get(key), str) or not row[key].strip() for key in ("rule", "source")):
                raise ValueError(f"{where}: expected rule and source text")
            continue
        if "rule" in row or "source" in row:
            raise ValueError(f"{where}: both legacy prose and structured notes")
        records = row["notes"]
        if not isinstance(records, list) or not records:
            raise ValueError(f"{where}: expected nonempty notes array")
        for note in records:
            structured.validate_note(note, where=where)
            if note["subject"] != {"kind": "wording_rule", "id": rid}:
                raise ValueError(f"{where}: note targets a different wording rule")
            if note["scope"] != {"kind": "language", "language": code}:
                raise ValueError(f"{where}: expected language scope {code}")
        notes.extend(records)
    structured.validate({"schema": document["schema"], "note": notes})
    return rows


def list_text(rows: list[dict], *, code: str | None = None) -> str:
    """Serialize canonical rule rows without inventing note metadata."""
    _validate_list({"schema": structured.SCHEMA, "rule": rows}, code or language.current().code)
    marker = f'schema = "{structured.SCHEMA}"\n'
    blocks = [marker]
    if not rows:
        blocks.append("rule = []\n")
    for row in rows:
        lines = ["", "[[rule]]"]
        for key in ("id", "ids", "source_text", "rule", "source"):
            if key in row:
                value = json.dumps(row[key], ensure_ascii=False).replace("\x7f", "\\u007f")
                lines.append(f"{key} = {value}")
        blocks.append("\n".join(lines) + "\n")
        if "notes" in row:
            text = structured.notes_text({"schema": structured.SCHEMA, "note": row["notes"]})
            blocks.append(text.removeprefix(marker).replace("\n[[note]]\n", "\n[[rule.notes]]\n"))
    return "".join(blocks)


def write_list(path: Path, rows: list[dict], *, code: str | None = None) -> None:
    text = list_text(rows, code=code)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def load_list(path, *, code: str | None = None) -> list[dict]:
    """Read canonical TOML or legacy TSV; absent lists are empty."""
    path = Path(path)
    if not path.exists():
        return []
    if path.suffix == ".toml":
        try:
            with path.open("rb") as stream:
                return _validate_list(tomllib.load(stream), code or language.current().code)
        except ValueError as exc:
            raise ValueError(f"{path}: {exc}") from exc
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t", quoting=csv.QUOTE_NONE)
        if tuple(reader.fieldnames or ()) != LIST_COLUMNS:
            raise ValueError(f"{path}: columns {reader.fieldnames}, expected {list(LIST_COLUMNS)}")
        rows = []
        for n, r in enumerate(reader, 2):
            ids, english = (r["ids"] or "").split(), r["source_text"] or ""
            if not (r["rule"] and r["source"]) or bool(ids) == bool(english):
                raise ValueError(f"{path}:{n}: a row gives a rule, a source and ids or an English text")
            rows.append({"ids": ids, "source_text": english, "rule": r["rule"], "source": r["source"]})
    return rows


def _note_rule(note: dict, rid: str) -> dict:
    def details(value):
        if isinstance(value, dict):
            return "; ".join(f"{key.replace('_', ' ')}: {details(item)}" for key, item in value.items())
        if isinstance(value, list):
            return "\n".join(details(item) for item in value)
        return str(value)

    text = "\n".join(item for key in structured.PROSE_FIELDS for item in note.get(key, []))
    sources = "; ".join(source["name"] for source in note.get("sources", []))
    metadata = {key: note[key] for key in ("status", "sources", "evidence", "related_notes",
                                          "related_subjects", *structured.ATTRIBUTION_FIELDS) if key in note}
    return rule(text, sources or f"wording rule {rid}", details(metadata))


def wording_rules(rows: list[dict], catalog: dict) -> dict[str, list[dict]]:
    """Entry id -> the rules of the rule list rows (load_list) that name or match it."""
    from ..catalog import keys
    entry_id = keys.for_catalog(catalog).entry_id   # rows name entries by key or inventory id
    keyed = any(e.keyed for _, e in catalog.values())
    out: dict[str, list[dict]] = {}
    for r in rows:
        named = [entry_id(sid) for sid in r.get("ids", [])]
        ids = named or [sid for sid, (_, e) in catalog.items() if not e.keyed and r["source_text"] in e.msgid]
        missing = [given for sid, given in zip(named, r.get("ids", [])) if sid not in catalog]
        if not missing and not ids and keyed:
            continue
        if missing or not ids:
            label = r.get("rule", r.get("id", ""))
            raise ValueError(f"rule list: {label[:40]!r}: " + (f"no entry {', '.join(missing)}" if missing
                                                                     else f"no English contains {r['source_text']!r}"))
        shown = ([_note_rule(note, r["id"]) for note in r["notes"]
                  if note["status"] in ("unreviewed", "accepted")] if "notes" in r
                 else [rule(r["rule"], r["source"])])
        if not shown:
            continue
        for sid in ids:
            out.setdefault(sid, []).extend(dict(item) for item in shown)
    return out
