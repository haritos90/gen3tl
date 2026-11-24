"""The translation rules of each entry, with their source and evidence."""

from __future__ import annotations

import csv
import json
import tomllib
from pathlib import Path

from ..core import notes as structured
from ..lang import language
from ..lang.language import save_link_keys
from .provenance import code as coded

LIST_COLUMNS = ("ids", "source_text", "rule", "source")
HOSTS_SHOWN = 4   # ids named per inserted-word rule


def rule(text: str | dict, source: str | dict, evidence: str | dict = "") -> dict:
    """A rule as the editor shows it: its wording, where it comes from, its evidence; each a text or a code."""
    return {"text": text, "source": source, "evidence": evidence}


def _counted(name: str, n: int, /, **args) -> dict:
    """The wording `name` of `n` things: its singular form for one."""
    return coded(f"{name}_one" if n == 1 else name, n=n, **args)


def _listed(name: str, slot: str, ids: list[str]) -> dict:
    """The wording `name` naming the first HOSTS_SHOWN ids, then the count of the rest."""
    if len(ids) > HOSTS_SHOWN:
        return coded(f"{name}_more", slot=slot, n=len(ids), entries=ids[:HOSTS_SHOWN], more=len(ids) - HOSTS_SHOWN)
    return _counted(name, len(ids), slot=slot, entries=ids)


def save_link_rules(prof, inv: dict) -> dict[str, list[dict]]:
    """The save-link flag, with the profile key that sets it."""
    tables = {t["name"]: t for t in prof.get("table", [])}
    keys = {sid: key for key in save_link_keys(getattr(prof, "language", None))
            for sid in prof.get("strings", {}).get(key, [])}
    text = coded("wording_save_link")
    out = {}
    for e in inv["entries"]:
        if "save-link" not in e["flags"]:
            continue
        t = tables.get(e.get("table")) if e["kind"] == "fixed" else None
        if t is not None and t.get("save_link"):
            out[e["id"]] = [rule(text, coded("rule_table_save_link", table=t["name"]), t.get("evidence", ""))]
        else:
            out[e["id"]] = [rule(text, coded("rule_strings", key=keys.get(e["id"], "save_link")))]
    return out


def computed_rules(prof, inv: dict) -> dict[str, list[dict]]:
    """[[strings.computed]] groups: members in ROM order, each keeping within the gap to the next."""
    by_id = {e["id"]: e for e in inv["entries"]}
    out: dict[str, list[dict]] = {}
    for d in prof.get("strings", {}).get("computed", []):
        members = sorted((by_id[s] for s in d["ids"] if s in by_id), key=lambda e: e["offset"])
        source = coded("rule_computed", pointer=f"0x{d['pointer']:X}")
        for e, nxt in zip(members, members[1:] + [None]):
            group = {"others": ", ".join(m["id"] for m in members if m is not e), "pointer": f"0x{d['pointer']:X}"}
            text = (coded("wording_computed_last", **group) if nxt is None
                    else coded("wording_computed", **group, bytes=nxt["offset"] - e["offset"] - 1, next=nxt["id"]))
            out.setdefault(e["id"], []).append(rule(text, source, d.get("evidence", "")))
    return out


def interior_rules(prof, limits: dict) -> dict[str, list[dict]]:
    """[[strings.interior]]: the head and slot of the limit (qa.interior_heads)."""
    out = {}
    for d in prof.get("strings", {}).get("interior", []):
        lim = limits.get(d["id"])
        if lim is None or lim.head is None:
            continue
        pointer = f"0x{d['pointer']:X}"
        text = coded("wording_interior", head=lim.head, offset=d["offset"], pointer=pointer, bytes=lim.bytes)
        out[d["id"]] = [rule(text, coded("rule_interior", pointer=pointer), d.get("evidence", ""))]
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
        text = (_counted("wording_code_char_end", n) if pad == 0xFF
                else _counted("wording_code_char", n, pad=f"0x{pad:02X}"))
        out[e["id"]] = [rule(text, coded("rule_table", table=t["name"]), t.get("evidence", ""))]
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
                    fill = coded("fill_measured", measured=prof["layout"]["fill_source"][source]["label"])
                elif "strings" in s:   # bounded sources have no fixed width
                    fill = _counted("fill_strings", len(s["strings"]) + len(s.get("originals", [])))
                elif "digits" in s:
                    fill = _counted("fill_digits", int(s["digits"]))
                else:
                    fill = coded("fill_width", width=s["width"])
                slot = "{%s}" % name
                identity = r.get("name") or (f"0x{r['base']:X}" if "base" in r else r["ids"][0])
                if r.get("where"):
                    text = coded("wording_code_fill_where", slot=slot, fill=fill, where=r["where"])
                    source = coded("rule_code_fill_where", name=identity, where=r["where"])
                else:
                    text = coded("wording_code_fill", slot=slot, fill=fill)
                    source = coded("rule_code_fill", name=identity)
                out.setdefault(e["id"], []).append(rule(text, source, r["evidence"]))
    return out


def droppable_rules(prof) -> dict[str, list[dict]]:
    """[[qa.droppable]]: the tokens a translation may leave out."""
    out: dict[str, list[dict]] = {}
    for d in prof.get("qa", {}).get("droppable", []):
        for sid in d["ids"]:
            out.setdefault(sid, []).append(rule(coded("wording_droppable", tokens=" ".join(d["tokens"])),
                                                coded("rule_droppable"), d.get("evidence", "")))
    return out


def pointer_table_rules(prof, inv: dict) -> dict[str, list[dict]]:
    """The `note` of a [[strings.pointer_table]], on each string of its table."""
    notes = {t["name"]: t for t in prof.get("strings", {}).get("pointer_table", []) if t.get("note")}
    out = {}
    for e in inv["entries"]:
        t = notes.get(e.get("table")) if e["kind"] == "pointer" else None
        if t is not None:
            out[e["id"]] = [rule(t["note"], coded("rule_pointer_table", table=t["name"]), t.get("evidence", ""))]
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
        first.append(rule(coded("wording_never_shown"), reach["source"], reach["evidence"]))
    for name, rows in (("wording_hosts", ctx["hosts"]), ("wording_words", ctx["words"])):
        slots: dict[str, list[str]] = {}
        for row in rows:
            slots.setdefault(row["slot"], []).append(row["id"])
        last.extend(rule(_listed(name, slot, ids), "catalog context") for slot, ids in slots.items())
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
