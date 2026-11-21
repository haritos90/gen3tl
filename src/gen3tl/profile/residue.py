"""Named discovery shortfalls retained as overlay notes."""

from __future__ import annotations

import tomllib
from pathlib import Path

from . import schema

FIELD = "discovery_shortfall_evidence"


def read(path, sha1):
    path = Path(path)
    files = schema.overlay_files(path) if path.is_dir() else [path]
    records = []
    for file in files:
        if file.suffix == ".toml" and not file.name.endswith(".notes.toml"):
            document = tomllib.loads(file.read_text(encoding="utf-8"))
            if FIELD in document.get("rom", {}):
                records.append(document["rom"][FIELD])
    if not records:
        return {"unresolved": [], "shortfall": []}
    if len(records) != 1:
        raise ValueError("discovery residue: duplicate ledger")
    data = tomllib.loads(records[0])
    if data.get("schema") != "1.0.0" or data.get("rom_sha1") != sha1:
        raise ValueError("discovery residue: schema or ROM differs")
    seen = set()
    for row in data.get("unresolved", []):
        if row["id"] in seen or not row.get("reason", "").strip():
            raise ValueError("discovery residue: duplicate or unexplained unresolved fact")
        seen.add(row["id"])
    seen.clear()
    for row in data.get("shortfall", []):
        if not row.get("section") or not row.get("facts") or not row.get("reason", "").strip():
            raise ValueError("discovery residue: incomplete shortfall")
        if seen.intersection(row["facts"]):
            raise ValueError("discovery residue: duplicate shortfall fact")
        seen.update(row["facts"])
    return data


def unresolved(issues, declared):
    """A changed explanation is a new finding, even for a known id."""
    known = {row["id"]: row["reason"] for row in declared.get("unresolved", [])}
    acknowledged, new = [], []
    for fact in issues:
        if fact.status != "unresolved":
            continue
        destination = acknowledged if known.get(fact.id) == fact.evidence.sentence else new
        destination.append(fact)
    return acknowledged, new


def missing_coverage(result, declared):
    """Require every below-target overlay fact to have a named shortfall."""
    named = {(row["section"], identifier) for row in declared.get("shortfall", [])
             for identifier in row["facts"]}
    sections = {row.name for row in result.sections if row.rom_facts and row.covered / row.rom_facts < .9}
    return [comparison.fact.id for comparison in result.comparisons
            if comparison.fact.section in sections
            and comparison.outcome not in ("reproduced", "configuration")
            and (comparison.fact.section, comparison.fact.id) not in named]
