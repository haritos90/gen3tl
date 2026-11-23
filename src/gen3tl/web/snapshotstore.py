"""Diffable shared measurements and per-language JSON pointer changes."""

from __future__ import annotations

import copy
import json
import re
import tempfile
from pathlib import Path

from ..check import provenance

FORMAT = "2.0.0"
SHARED = "shared.json"
# a participant no text or game data names
UNSTATED = {"basis": "", "in_source": "not-stated", "sources": []}
CUE = {"evidence": "", "gender": "unknown", "value": ""}
ROLES = ("addressee", "speaker")
IDENTITY = {"schema": "2.0.0", **dict.fromkeys(ROLES, UNSTATED)}
LOOSE = re.compile(r"s([0-9A-F]{7})")   # an id that names its offset
FIXED = re.compile(r"([a-z0-9_]+):(\d+)")   # a table record


def paths(path: Path) -> tuple[Path, Path]:
    """Both files required to read a snapshot."""
    return path, path.with_name(SHARED)


def _read(path: Path, fields: set[str]) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.keys() != fields | {"format"} or doc["format"] != FORMAT:
        raise ValueError(f"{path}: snapshot storage format differs; regenerate gen3tl web snapshot")
    return doc


def _shared(path: Path) -> dict:
    doc = _read(path.with_name(SHARED), {"snapshots", "tables", "data"})
    names = doc["snapshots"]
    if (not isinstance(doc["data"], dict) or not isinstance(names, list) or not names
            or any(not isinstance(name, str) or Path(name).name != name or name in (".", "..", SHARED)
                   for name in names) or len(names) != len(set(names))
            or not isinstance(doc["tables"], dict) or not isinstance(doc["tables"].get("limit_sources"), list)):
        raise ValueError(f"{path}: invalid shared snapshot")
    return doc


def _assemble(path: Path, shared: dict) -> dict:
    changes = _read(path, {"changes"})["changes"]
    if path.name not in shared["snapshots"] or not isinstance(changes, dict):
        raise ValueError(f"{path}: invalid snapshot changes")
    doc = copy.deepcopy(shared["data"])
    for pointer, value in changes.items():
        if not pointer.startswith("/"):
            raise ValueError(f"{path}: invalid snapshot pointer {pointer}")
        parts = pointer[1:].split("/")
        parts = [part.replace("~1", "/").replace("~0", "~") for part in parts]
        target = doc
        try:
            for part in parts[:-1]:
                target = target[int(part)] if isinstance(target, list) else target[part]
            if isinstance(target, list):
                index = int(parts[-1])
                if str(index) != parts[-1] or index < 0:
                    raise ValueError(pointer)
                target[index] = value
            else:
                target[parts[-1]] = value
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{path}: invalid snapshot pointer {pointer}") from exc
    try:
        return expand(doc, shared["tables"])
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{path}: invalid snapshot record: {exc}") from exc


def load(path: Path) -> dict:
    """Reconstruct the original measurement object."""
    return _assemble(path, _shared(path))


class Table:
    """Distinct values in order of first use, by index; equal content in another key order is another row."""

    def __init__(self):
        self.rows, self.at = [], {}

    def index(self, value) -> int:
        key = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if key not in self.at:
            self.at[key] = len(self.rows)
            self.rows.append(value)
        return self.at[key]


def without_defaults(value: dict, defaults: dict) -> dict:
    """`value` without the fields equal to their default."""
    return {key: item for key, item in value.items() if key not in defaults or item != defaults[key]}


def condense(doc: dict, sources: Table) -> dict:
    """The stored form of a measurement object: repeated values by index, defaults absent."""
    if "schema" not in doc:   # a partial record stays as written
        return doc
    out = dict(doc)
    source = doc.get("source", {})
    full = {sid: row for sid, row in source.items() if len(row) == 6}
    styles = list(next(iter(full.values()))[5]["widths"]) if full else []
    if full:
        out["styles"] = styles
    if "source" in doc:
        out["source"] = {sid: _condensed_row(sid, source[sid], styles, sources) if sid in full else source[sid]
                         for sid in sorted(source)}
    if "inventory" in doc:
        out["inventory"] = [without_defaults(row, _inventory_defaults(row["id"], source)) for row in doc["inventory"]]
    if "context" in doc:
        out["context"] = {sid: without_defaults({**rec, "uses": [{key: value for key, value in use.items() if value is not None}
                                                         for use in rec["uses"]]},
                                        {"uses": [], "slots": {}, "fills": [], "slot_refs": {"unwalked": 0, "code": 0}})
                          for sid, rec in doc["context"].items()}
    if "reach" in doc:
        out["reach"] = {sid: without_defaults({**rec, "evidence": [{key: value for key, value in item.items() if value}
                                                           for item in rec["evidence"]]},
                                      {"group": "script", "evidence": []})
                        for sid, rec in doc["reach"].items()}
    return out


def _condensed_row(sid: str, row: list, styles: list, sources: Table) -> list:
    digest, tokens, lines, widths, limit, facts = row
    if list(facts["widths"]) != styles:
        raise ValueError(f"{sid}: measured styles differ")
    context = facts["context"]
    stored = without_defaults(context, {"maps": [], "events": [], "boxes": [], "uses": 0, "speakers": [],
                                "identity": IDENTITY})
    if "identity" in stored:
        identity = context["identity"]
        stored["identity"] = without_defaults({**identity, **{role: without_defaults(
            {**identity[role], "sources": [without_defaults(cue, CUE) for cue in identity[role]["sources"]]}, UNSTATED)
            for role in ROLES}}, {"schema": "2.0.0", **dict.fromkeys(ROLES, {})})
    while limit and limit[-1] is None:
        limit = limit[:-1]
    return [digest, tokens, lines, widths, limit, {
        **without_defaults({key: facts[key] for key in ("chars", "terms", "names")}, {"terms": [], "names": []}),
        "sources": {key: sources.index(_coded_source(value)) for key, value in facts["sources"].items()},
        "widths": [facts["widths"][style] for style in styles], "context": stored}]


def _inventory_defaults(sid: str, source: dict) -> dict:
    """The values an inventory row omits: named by its id or held by its source row."""
    loose, fixed = LOOSE.fullmatch(sid), FIXED.fullmatch(sid)
    return {"kind": "fixed" if fixed else "pointer", "mode": "field", "flags": [],
            "category": f"table:{fixed[1]}" if fixed else "script",
            "table": fixed[1] if fixed else None, "index": int(fixed[2]) if fixed else None,
            **({"offset": int(loose[1], 16)} if loose else {}),
            **({"source_sha256": source[sid][0]} if sid in source else {})}


def _coded_source(value: list) -> dict:
    kind, rule, evidence, unverified = value
    out = {"kind": kind, "rule": provenance.canonical(rule)}
    if evidence:
        out["evidence"] = evidence
    if unverified:
        out["unverified"] = True
    return out


def _sorted(value):
    # as a stored record reads back
    if isinstance(value, dict):
        return {key: _sorted(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_sorted(item) for item in value]
    return value


def expand(doc: dict, tables: dict) -> dict:
    """The measurement object of a stored form (condense)."""
    if "schema" not in doc:
        return doc
    out = dict(doc)
    styles = out.pop("styles", [])
    sources = [[s["kind"], s["rule"], s.get("evidence", ""), s.get("unverified", False)]
               for s in tables["limit_sources"]]
    source = {sid: _expanded_row(row, styles, sources) if len(row) == 6 else row
              for sid, row in doc.get("source", {}).items()}
    if "source" in doc:
        out["source"] = source
    if "inventory" in doc:
        out["inventory"] = [{**_inventory_defaults(row["id"], source), **row} for row in doc["inventory"]]
    if "context" in doc:
        out["context"] = {sid: {"slots": {}, "fills": [], "slot_refs": {"unwalked": 0, "code": 0}, **rec,
                                "uses": [{"event": None, "box": None, "at": None, "trainer_role": None, **use}
                                         for use in rec.get("uses", [])]}
                          for sid, rec in doc["context"].items()}
    if "reach" in doc:
        out["reach"] = {sid: {"group": "script", **rec,
                              "evidence": [{"kind": "", "at": "", **item} for item in rec.get("evidence", [])]}
                        for sid, rec in doc["reach"].items()}
    return _sorted(out)


def _expanded_row(row: list, styles: list, sources: list) -> list:
    digest, tokens, lines, widths, limit, facts = row
    context = facts["context"]
    identity = context.get("identity", {})
    return [digest, tokens, lines, widths, limit + [None] * (7 - len(limit)), {
        "chars": facts["chars"], "terms": facts.get("terms", []), "names": facts.get("names", []),
        "sources": {key: list(sources[n]) for key, n in facts["sources"].items()},
        "widths": dict(zip(styles, facts["widths"], strict=True)),
        "context": {"maps": [], "events": [], "boxes": [], "uses": 0, "speakers": [], **context,
                    "identity": {"schema": "2.0.0", **identity, **{role: {**UNSTATED, **identity.get(role, {}), "sources": [
                        {**CUE, **cue} for cue in identity.get(role, {}).get("sources", [])]} for role in ROLES}}}}]


def _factor(documents: list[dict]) -> tuple[dict, list[dict]]:
    changes = [{} for _ in documents]

    def common(values, pointer=""):
        first = values[0]
        if all(value == first for value in values[1:]):
            return first
        if all(isinstance(value, dict) for value in values):
            keys = set.intersection(*(set(value) for value in values))
            child = lambda key: pointer + "/" + key.replace("~", "~0").replace("/", "~1")
            result = {key: common([value[key] for value in values], child(key)) for key in sorted(keys)}
            for value, patch in zip(values, changes):
                patch.update((child(key), item) for key, item in value.items() if key not in keys)
            return result
        if all(isinstance(value, list) and len(value) == len(first) for value in values):
            return [common(list(items), pointer + "/" + str(index))
                    for index, items in enumerate(zip(*values))]
        for value, patch in zip(values, changes):
            patch[pointer] = value
        return None

    return common(documents), changes


def _write(path: Path, doc: dict, depth=0) -> None:
    def formatted(value, depth=0):
        compact = lambda child: json.dumps(child, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if depth >= 2 or not isinstance(value, (dict, list)) or not value:
            return compact(value)
        prefix = " " * (depth + 1)
        if isinstance(value, dict):
            rows = [prefix + compact(key) + ": " + formatted(value[key], depth + 1) for key in sorted(value)]
            left, right = "{", "}"
        else:
            rows = [prefix + formatted(child, depth + 1) for child in value]
            left, right = "[", "]"
        return left + "\n" + ",\n".join(rows) + "\n" + " " * depth + right

    path.write_text(formatted(doc, depth) + "\n", encoding="utf-8")


def _save(directory: Path, documents: dict[str, dict]) -> None:
    names = sorted(documents)
    sources = Table()   # limit sources of every snapshot
    data, changes = _factor([condense(documents[name], sources) for name in names])
    with tempfile.TemporaryDirectory(prefix=".snapshot-", dir=directory) as temporary:
        staged = Path(temporary)
        for name, patch in zip(names, changes):
            _write(staged / name, {"format": FORMAT, "changes": patch})
        # Keep measurement records on separate lines.
        _write(staged / SHARED, {"format": FORMAT, "snapshots": names, "tables": {"limit_sources": sources.rows}, "data": data}, depth=-1)
        for name in [*names, SHARED]:
            target = directory / name
            if not target.exists() or target.read_bytes() != (staged / name).read_bytes():
                (staged / name).replace(target)


def write(path: Path, doc: dict) -> None:
    """Refactor the collection while preserving every other snapshot."""
    if path.name == SHARED:
        raise ValueError(f"{path}: reserved shared snapshot filename")
    path.parent.mkdir(parents=True, exist_ok=True)
    documents = {}
    if path.with_name(SHARED).exists():
        shared = _shared(path)
        documents = {name: _assemble(path.with_name(name), shared)
                     for name in shared["snapshots"] if name != path.name}
    documents[path.name] = doc
    _save(path.parent, documents)


def remove(path: Path) -> None:
    """Remove a snapshot and retain its peers' measurements."""
    shared = _shared(path)
    documents = {name: _assemble(path.with_name(name), shared)
                 for name in shared["snapshots"] if name != path.name}
    if documents:
        _save(path.parent, documents)
    else:
        path.with_name(SHARED).unlink()
    path.unlink(missing_ok=True)
