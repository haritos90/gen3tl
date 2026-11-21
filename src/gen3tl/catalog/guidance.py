"""Shared translator guidance bound to exact source strings."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from ..core import notes as structured
from .source import digest

FIELDS = ("id", "source_sha256", "text", "evidence")
HEADER = ("# Translator guidance per catalog entry, shared by every language.\n"
          "# Keys and digests bind guidance to ROM text.\n")


def _structured(row) -> bool:
    return isinstance(row, dict) and "subject" in row


def _validate(row, where: str) -> None:
    if _structured(row):
        structured.validate_note(row, where=where)
        if row["subject"]["kind"] != "entry" or row["scope"]["kind"] != "shared":
            raise ValueError(f"{where}: expected a shared entry note")
        return
    if not isinstance(row, dict) or set(row) != set(FIELDS):
        raise ValueError(f"{where}: expected id, source_sha256, text, and evidence")
    if any(not isinstance(row[key], str) or not row[key].strip() for key in FIELDS):
        raise ValueError(f"{where}: fields must be nonempty strings")
    if not re.fullmatch(r"[0-9a-f]{64}", row["source_sha256"]):
        raise ValueError(f"{where}: invalid English digest")


def _document_rows(doc: dict, where: str) -> list[dict]:
    if set(doc) - {"schema", "note"} or not isinstance(doc.get("note", []), list):
        raise ValueError(f"{where}: expected [[note]] tables and optional schema")
    rows = doc.get("note", [])
    if "schema" in doc:
        if "note" not in doc:
            raise ValueError(f"{where}: schema requires a note array")
        structured.validate({"schema": doc["schema"],
                             "note": [row for row in rows if _structured(row)]})
        seen = set()
        for index, row in enumerate(rows):
            _validate(row, f"{where}: note[{index}]")
            if row["id"] in seen:
                raise ValueError(f"{where}: duplicate note {row['id']}")
            seen.add(row["id"])
    elif any(_structured(row) for row in rows):
        raise ValueError(f"{where}: structured notes require schema")
    return rows


def read_notes(path: Path) -> list[dict]:
    """The notes of a notes file; none without the file."""
    path = Path(path)
    if not path.is_file():
        return []
    try:
        with path.open("rb") as stream:
            doc = tomllib.load(stream)
        return _document_rows(doc, "translator_notes")
    except ValueError as exc:
        raise ValueError(f"{path}: {exc}") from exc


def toml_string(value: str) -> str:
    """A TOML basic string; JSON escapes are valid TOML."""
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def notes_text(rows: list[dict] | dict) -> str:
    versioned = isinstance(rows, dict) or any(_structured(row) for row in rows)
    doc = rows if isinstance(rows, dict) else {"note": rows}
    if versioned and not isinstance(rows, dict):
        doc = dict(doc, schema=structured.SCHEMA)
    if isinstance(rows, dict) and "schema" not in doc:
        raise ValueError("translator_notes: structured notes require schema")
    rows = _document_rows(doc, "translator_notes")
    marker = f'schema = "{structured.SCHEMA}"\n'
    blocks = []
    for row in rows:
        if _structured(row):
            text = structured.notes_text({"schema": structured.SCHEMA, "note": [row]})
            blocks.append(text.removeprefix(marker))
        else:
            block = "\n".join(["[[note]]"] + [f"{key} = {toml_string(row[key])}" for key in FIELDS])
            blocks.append("\n" + block + "\n")
    return HEADER + (marker if versioned else "") + ("note = []\n" if versioned and not rows else "") + "".join(blocks)


def write_notes(path: Path, rows: list[dict] | dict) -> None:
    text = notes_text(rows)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def profile_rows(profile) -> list[dict]:
    """The notes of a profile: its project's notes file, else its own."""
    notes_path = getattr(profile, "notes_path", None)
    if notes_path is not None:
        return read_notes(notes_path)
    rows = profile.get("translator_notes", [])
    if isinstance(rows, list) and any(_structured(row) for row in rows):
        raise ValueError(f"{profile.name}: translator_notes: structured notes require schema in a notes file")
    return rows


def _details(value) -> str:
    if isinstance(value, dict):
        return "; ".join(f"{key.replace('_', ' ')}: {_details(item)}" for key, item in value.items())
    if isinstance(value, list):
        return "\n".join(_details(item) for item in value)
    return str(value)


def _legacy_view(row: dict) -> dict:
    if not _structured(row):
        return {"text": row["text"], "evidence": row["evidence"]}
    details = {key: row[key] for key in ("status", "sources", "evidence", "related_subjects", "related_notes",
                                        *structured.ATTRIBUTION_FIELDS) if key in row}
    return {"text": "\n".join(item for key in structured.PROSE_FIELDS for item in row.get(key, [])),
            "evidence": _details(details)}


def entry_notes(profile, catalog: dict, digests: dict[str, str] | None = None) -> dict[str, list[dict]]:
    """Validate the notes and select present source entries by inventory id."""
    from . import keys
    entry_id = keys.for_profile(profile).entry_id
    rows = profile_rows(profile)
    if not isinstance(rows, list):
        raise ValueError("translator_notes: expected an array")
    notes, identities, entries = {}, set(), {}
    for index, row in enumerate(rows):
        where = f"{profile.name}: translator_notes[{index}]"
        _validate(row, where)
        common = _structured(row)
        binding = row["binding"] if common else row
        sid = entry_id(binding["id"])
        if sid in entries and not (common and entries[sid]):
            raise ValueError(f"{where}: duplicate entry {sid}")
        entries[sid] = common
        if row["id"] in identities:
            raise ValueError(f"{where}: duplicate note {row['id']}")
        identities.add(row["id"])
        if sid not in catalog:
            continue
        entry = catalog[sid][1]
        expected = (digests or {}).get(sid) if entry.keyed else digest(entry.msgid)
        if (not entry.keyed or digests is not None) and expected != binding["source_sha256"]:
            raise ValueError(f"{where}: {sid}: translator note source differs from catalog")
        if common and row["status"] not in ("unreviewed", "accepted"):
            continue
        notes.setdefault(sid, []).append(_legacy_view(row))
    return notes
