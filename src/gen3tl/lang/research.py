"""Research rows with persistent identities and structured note references."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from ..core import notes as structured
from . import language

IDENTITY_COLUMNS = ("record_id", "note_ids")
TERM_METHODS = ("research-assembly", "research-citation")


@dataclass
class Row:
    values: dict[str, str]
    note_records: tuple[dict, ...] = field(default=(), repr=False)

    @property
    def record_id(self) -> str:
        return self.values.get("record_id", "")

    @property
    def note_ids(self) -> tuple[str, ...]:
        value = self.values.get("note_ids", "")
        return tuple(value.split("|")) if value else ()


@dataclass
class Table:
    columns: tuple[str, ...]
    rows: list[Row]


def load(path: Path) -> Table:
    """Read original cells and resolve notes without generating identities."""
    path = Path(path)
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        table = Table(tuple(reader.fieldnames or ()), [Row(row) for row in reader])
    for row, records in zip(table.rows, _records(path, table)):
        row.note_records = records
    return table


def save(path: Path, table: Table) -> None:
    """Write original columns and references without editing note collections."""
    path = Path(path)
    for row, records in zip(table.rows, _records(path, table)):
        if row.note_records and row.note_records != records:
            raise ValueError(f"{path}: {row.record_id}: structured notes changed; reload the research table")
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=table.columns, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(row.values for row in table.rows)


def index(folder: Path) -> dict[str, Row]:
    """Resolve persistent findings across the language's research tables."""
    rows, notes = {}, set()
    for path in sorted(folder.glob("*.tsv")):
        for row in load(path).rows:
            if not row.record_id:
                continue
            if row.record_id in rows:
                raise ValueError(f"{path}: duplicate record_id {row.record_id}")
            rows[row.record_id] = row
            for nid in row.note_ids:
                if nid in notes:
                    raise ValueError(f"{path}: duplicate note {nid}")
                notes.add(nid)
    return rows


def assembly_facts(row: Row, fields=("role", "evidence")) -> list[str]:
    """Render canonical research cells in their established order."""
    return [f"{key}: {row.values[key].strip()}" for key in fields if row.values.get(key)]


def term_facts(note: dict, row: Row) -> list[str]:
    """Resolve generated summaries without changing the stored record."""
    facts = note.get("facts", [])
    if any(item["method"] == "research-assembly" for item in note.get("evidence", [])):
        derived = assembly_facts(row, note.get("assembly_fields", ("role", "evidence")))
        if facts and "assembly_fields" not in note and not any(fact in derived for fact in facts):
            return list(facts)
        return derived + [fact for fact in facts if fact not in derived]
    if "citation" in note:
        from .cite import _summary, citation_columns, citation_values
        citation = note["citation"]
        values = citation_values(row, citation_columns(citation.get("columns")))
        return [_summary([values], citation["label"]), *facts]
    return list(facts)


def term_note(term_id: str, row: Row, method: str, facts: list[str], *, citation: dict | None = None) -> dict:
    """Reference canonical findings without copying their note prose."""
    if method not in TERM_METHODS:
        raise ValueError(f"invalid research method {method}")
    _identity(row.record_id, "research record_id")
    identity = str(uuid5(NAMESPACE_URL, f"{method}\n{term_id}\n{row.record_id}"))
    note = {
        "id": f"{method}:{identity}",
        "subject": {"kind": "term", "id": term_id},
        "scope": {"kind": "language", "language": language.current().code},
        "evidence": [{"method": method, "verification": "recorded"}],
        "related_subjects": [{"kind": "research", "id": row.record_id}],
        "status": "unreviewed",
    }
    if method == "research-assembly":
        fields = [key for key in ("role", "evidence")
                  if row.values.get(key) and f"{key}: {row.values[key].strip()}" in facts]
        facts = [fact for fact in facts if fact not in assembly_facts(row, fields)]
        if fields and facts:
            note["assembly_fields"] = fields
    if facts:
        note["facts"] = facts
    if citation is not None:
        note["citation"] = citation
    if row.note_ids:
        note["related_notes"] = list(row.note_ids)
    structured.validate_note(note)
    return note


def is_term_note(note: dict) -> bool:
    return any(item["method"] in TERM_METHODS for item in note.get("evidence", []))


def is_reference(note: dict) -> bool:
    """Identify generated links without independent term prose."""
    facts = note.get("facts", [])
    if any(item["method"] == "research-assembly" for item in note.get("evidence", [])):
        facts = [text for text in facts if text != "Terminology follows the linked research record."]
    return is_term_note(note) and not any((facts, note.get("guidance"), note.get("reason")))


def _identity(value: str, where: str) -> None:
    if not value or value != value.strip() or any(char in value for char in "|\t\r\n"):
        raise ValueError(f"{where}: invalid identity")


def _records(path: Path, table: Table) -> list[tuple[dict, ...]]:
    columns = table.columns
    if (not columns or any(not isinstance(column, str) or not column.strip() for column in columns)
            or len(set(columns)) != len(columns)):
        raise ValueError(f"{path}: invalid columns")
    identified = all(column in columns for column in IDENTITY_COLUMNS)
    if not identified and any(column in columns for column in IDENTITY_COLUMNS):
        raise ValueError(f"{path}: record_id and note_ids columns required together")
    targets = set()
    for index, row in enumerate(table.rows):
        where = f"{path}: row {index + 1}"
        if row.values.keys() != set(columns) or any(not isinstance(value, str) for value in row.values.values()):
            raise ValueError(f"{where}: cells differ from columns")
        if identified:
            _identity(row.record_id, f"{where}: record_id")
            if row.record_id in targets:
                raise ValueError(f"{where}: duplicate record_id {row.record_id}")
            targets.add(row.record_id)

    notes_path = path.with_suffix(".notes.toml")
    document = structured.read_notes(notes_path) if notes_path.exists() else {"note": []}
    by_id = {note["id"]: note for note in document["note"]}
    for note in by_id.values():
        where = f"{notes_path}: {note['id']}"
        if note["subject"]["kind"] != "research":
            raise ValueError(f"{where}: expected a research subject")
        if note["subject"]["id"] not in targets:
            raise ValueError(f"{where}: unknown research record {note['subject']['id']}")
        if note["scope"] != {"kind": "language", "language": language.current().code}:
            raise ValueError(f"{where}: expected language scope {language.current().code}")

    selected, referenced = [], set()
    for row in table.rows:
        where = f"{path}: {row.record_id}"
        for nid in row.note_ids:
            _identity(nid, f"{where}: note_ids")
        if len(set(row.note_ids)) != len(row.note_ids):
            raise ValueError(f"{where}: duplicate note_ids")
        if row.note_ids and row.values.get("notes"):
            raise ValueError(f"{where}: both legacy and structured notes")
        records = []
        for nid in row.note_ids:
            if nid not in by_id:
                raise ValueError(f"{where}: unknown note {nid}")
            note = by_id[nid]
            if note["subject"]["id"] != row.record_id:
                raise ValueError(f"{where}: note {nid} targets a different research record")
            records.append(note)
            referenced.add(nid)
        selected.append(tuple(records))
    unused = by_id.keys() - referenced
    if unused:
        raise ValueError(f"{notes_path}: unreferenced notes: {', '.join(sorted(unused))}")
    return selected
