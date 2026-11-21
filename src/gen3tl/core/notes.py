"""Structured note validation and lossless TOML interchange."""

from __future__ import annotations

import json
import math
import re
import tomllib
from datetime import date, datetime
from pathlib import Path

SCHEMA = "3.0.0"
SUBJECT_KINDS = ("entry", "term", "research", "graphic", "profile_fact", "wording_rule")
STATUSES = ("unreviewed", "proposed", "accepted", "needs_review", "superseded")
VERIFICATIONS = ("recorded", "checked", "unresolved")
SOURCE_KINDS = ("web", "publication", "rom", "project")
SOURCE_RELATIONS = ("selected_form", "alternative", "context")
LOCATION_KINDS = ("rom_offset", "cpu_address", "symbol", "file")
PROSE_FIELDS = ("guidance", "facts", "reason")
ATTRIBUTION_FIELDS = ("author", "created", "updated", "reviewed_by", "reviewed_at")
NOTE_FIELDS = ("id", "subject", "scope", "binding", *PROSE_FIELDS, "sources", "evidence",
               "related_notes", "related_subjects", "assembly_fields", "citation", "status", *ATTRIBUTION_FIELDS)


class NoteError(ValueError):
    pass


class BoundProse(str):
    """Rendered prose whose adapter validated its note bindings."""


def has_prose(value, *, single_line: bool = False) -> bool:
    """Require visible prose; bound notes permit line breaks."""
    return (isinstance(value, str) and bool(value.strip())
            and (isinstance(value, BoundProse) or not single_line or not any(c in value for c in "\r\n")))


def _object(value, required: tuple[str, ...], optional: tuple[str, ...], where: str) -> None:
    if not isinstance(value, dict):
        raise NoteError(f"{where}: expected an object")
    missing = set(required) - value.keys()
    extra = value.keys() - set(required + optional)
    if missing:
        raise NoteError(f"{where}: missing fields: {', '.join(sorted(missing))}")
    if extra:
        raise NoteError(f"{where}: unknown fields: {', '.join(sorted(map(str, extra)))}")


def _text(value, where: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise NoteError(f"{where}: expected a nonempty string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise NoteError(f"{where}: invalid Unicode") from exc


def _choice(value, choices: tuple[str, ...], where: str) -> None:
    if not isinstance(value, str) or value not in choices:
        raise NoteError(f"{where}: expected one of {', '.join(choices)}")


def _array(value, where: str) -> None:
    if not isinstance(value, list) or not value:
        raise NoteError(f"{where}: expected a nonempty array")


def _date(value, where: str, timestamp: bool = False) -> None:
    _text(value, where)
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            date.fromisoformat(value)
            return
        if timestamp and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value):
            datetime.fromisoformat(value)
            return
    except ValueError:
        pass
    raise NoteError(f"{where}: invalid date" + (" or zoned timestamp" if timestamp else ""))


def _digest(value, length: int, where: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(rf"[0-9a-f]{{{length}}}", value):
        raise NoteError(f"{where}: expected a lowercase {length}-digit digest")


def _subject(value, where: str) -> None:
    _object(value, ("kind", "id"), (), where)
    _choice(value["kind"], SUBJECT_KINDS, f"{where}.kind")
    _text(value["id"], f"{where}.id")


def _scope(value, where: str) -> None:
    _object(value, ("kind",), ("language",), where)
    _choice(value["kind"], ("shared", "language"), f"{where}.kind")
    if value["kind"] == "shared":
        if "language" in value:
            raise NoteError(f"{where}: shared scope cannot name a language")
    else:
        language = value.get("language")
        if not isinstance(language, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*", language):
            raise NoteError(f"{where}.language: expected a language identifier")


def _binding(value, subject: dict, where: str) -> None:
    kind = subject["kind"]
    fields = {"entry": ("source_sha256",), "profile_fact": ("value_sha256",), "graphic": ()}
    if kind not in fields:
        raise NoteError(f"{where}: binding is not defined for {kind}")
    _object(value, ("kind", "id", *fields[kind]), ("field",) if kind == "profile_fact" else (), where)
    if value["kind"] != kind or value["id"] != subject["id"]:
        raise NoteError(f"{where}: binding differs from subject")
    for key in fields[kind]:
        _digest(value[key], 64, f"{where}.{key}")
    if "field" in value:
        _text(value["field"], f"{where}.field")


def _source(value, where: str) -> None:
    _object(value, ("kind", "name", "address", "verification"),
            ("locator", "retrieved", "quoted_term", "quote", "relation"), where)
    _choice(value["kind"], SOURCE_KINDS, f"{where}.kind")
    _choice(value["verification"], VERIFICATIONS, f"{where}.verification")
    for key in ("name", "address", "locator", "quoted_term", "quote"):
        if key in value:
            _text(value[key], f"{where}.{key}")
    if "retrieved" in value:
        _date(value["retrieved"], f"{where}.retrieved")
    if "relation" in value:
        _choice(value["relation"], SOURCE_RELATIONS, f"{where}.relation")


def _location(value, where: str) -> None:
    _object(value, ("kind", "value"), ("label",), where)
    _choice(value["kind"], LOCATION_KINDS, f"{where}.kind")
    if value["kind"] in ("rom_offset", "cpu_address"):
        if type(value["value"]) is not int or not 0 <= value["value"] <= 0xffffffff:
            raise NoteError(f"{where}.value: expected an unsigned 32-bit integer")
    else:
        _text(value["value"], f"{where}.value")
    if "label" in value:
        _text(value["label"], f"{where}.label")


def _measurement(value, where: str) -> None:
    _object(value, ("name", "value", "unit"), (), where)
    _text(value["name"], f"{where}.name")
    _text(value["unit"], f"{where}.unit")
    number = value["value"]
    if type(number) not in (int, float) or (isinstance(number, float) and not math.isfinite(number)):
        raise NoteError(f"{where}.value: expected a finite number")
    if value["unit"] == "count" and (type(number) is not int or number < 0):
        raise NoteError(f"{where}.value: expected a nonnegative integer count")


def _capture(value, where: str) -> None:
    _object(value, ("address",), ("sha256", "locator"), where)
    _text(value["address"], f"{where}.address")
    if "sha256" in value:
        _digest(value["sha256"], 64, f"{where}.sha256")
    if "locator" in value:
        _text(value["locator"], f"{where}.locator")


def _items(value: dict, key: str, validator, where: str) -> None:
    if key in value:
        _array(value[key], f"{where}.{key}")
        for index, item in enumerate(value[key]):
            validator(item, f"{where}.{key}[{index}]")


def _evidence(value, where: str) -> None:
    _object(value, ("method", "verification"), ("locations", "measurements", "related_subjects", "captures"), where)
    _text(value["method"], f"{where}.method")
    _choice(value["verification"], VERIFICATIONS, f"{where}.verification")
    for key, validator in (("locations", _location), ("measurements", _measurement),
                           ("related_subjects", _subject), ("captures", _capture)):
        _items(value, key, validator, where)


def validate_note(note: dict, *, new: bool = False, expected_binding: dict | None = None,
                  where: str = "note") -> None:
    """Validate one record without supplying missing metadata."""
    required = ("id", "subject", "scope", "status")
    _object(note, required, tuple(key for key in NOTE_FIELDS if key not in required), where)
    _text(note["id"], f"{where}.id")
    _subject(note["subject"], f"{where}.subject")
    _scope(note["scope"], f"{where}.scope")
    _choice(note["status"], STATUSES, f"{where}.status")
    for key in PROSE_FIELDS:
        _items(note, key, _text, where)
    bound = note["subject"]["kind"] in ("entry", "profile_fact", "graphic")
    if bound and "binding" not in note:
        raise NoteError(f"{where}: binding required")
    if "binding" in note:
        _binding(note["binding"], note["subject"], f"{where}.binding")
    if expected_binding is not None and note.get("binding") != expected_binding:
        raise NoteError(f"{where}: stale binding")
    for key, validator in (("sources", _source), ("evidence", _evidence),
                           ("related_subjects", _subject), ("related_notes", _text)):
        _items(note, key, validator, where)
    methods = {item["method"] for item in note.get("evidence", [])}
    subjects = note.get("related_subjects", [])
    generated = (note["subject"]["kind"] == "term" and len(subjects) == 1
                 and subjects[0]["kind"] == "research")
    if "assembly_fields" in note:
        fields = note["assembly_fields"]
        if not generated or "research-assembly" not in methods:
            raise NoteError(f"{where}: assembly fields require a generated research reference")
        _array(fields, f"{where}.assembly_fields")
        for value in fields:
            _choice(value, ("role", "evidence"), f"{where}.assembly_fields")
        if len(fields) != len(set(fields)):
            raise NoteError(f"{where}: duplicate assembly fields")
    if "citation" in note:
        citation = note["citation"]
        if not generated or "research-citation" not in methods:
            raise NoteError(f"{where}: citation requires a generated research reference")
        _object(citation, ("label",), ("columns",), f"{where}.citation")
        _text(citation["label"], f"{where}.citation.label")
        if "columns" in citation:
            _object(citation["columns"], (), ("source_text", "name", "dub", "gender", "evidence", "source"),
                    f"{where}.citation.columns")
            for key, value in citation["columns"].items():
                _text(value, f"{where}.citation.columns.{key}")
    derived = generated and ("research-assembly" in methods or "citation" in note)
    if not any(key in note for key in PROSE_FIELDS) and not derived:
        raise NoteError(f"{where}: guidance, facts or reason required")
    related = note.get("related_notes", [])
    if len(related) != len(set(related)) or note["id"] in related:
        raise NoteError(f"{where}.related_notes: duplicate or self reference")
    if (note["subject"]["kind"] == "term" and note["status"] == "accepted"
            and "guidance" in note and not any(source["verification"] != "unresolved"
                                               for source in note.get("sources", []))):
        raise NoteError(f"{where}: accepted term guidance requires a source")
    for key in ATTRIBUTION_FIELDS:
        if key in note:
            if key in ("created", "updated", "reviewed_at"):
                _date(note[key], f"{where}.{key}", timestamp=True)
            else:
                _text(note[key], f"{where}.{key}")
    if new and not {"author", "created"} <= note.keys():
        raise NoteError(f"{where}: new notes require author and created")


def validate(document: dict, *, new: bool = False) -> None:
    """Validate a versioned note collection without modifying it."""
    _object(document, ("schema", "note"), (), "notes")
    if document["schema"] != SCHEMA:
        raise NoteError(f"notes.schema: unsupported schema; expected {SCHEMA}")
    if not isinstance(document["note"], list):
        raise NoteError("notes.note: expected an array")
    seen = set()
    for index, note in enumerate(document["note"]):
        where = f"notes.note[{index}]"
        validate_note(note, new=new, where=where)
        if note["id"] in seen:
            raise NoteError(f"{where}.id: duplicate note {note['id']}")
        seen.add(note["id"])


def read_notes(path: Path) -> dict:
    """Read and validate a structured note collection."""
    path = Path(path)
    try:
        with path.open("rb") as stream:
            document = tomllib.load(stream)
        validate(document)
    except (tomllib.TOMLDecodeError, NoteError) as exc:
        raise NoteError(f"{path}: {exc}") from exc
    return document


def _literal(value) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{key} = {_literal(item)}" for key, item in value.items()) + " }"
    if isinstance(value, list):
        return "[" + ", ".join(_literal(item) for item in value) + "]"
    return repr(value)


def notes_text(document: dict) -> str:
    """Serialize validated fields without inventing dates or attribution."""
    validate(document)
    lines = [f"schema = {_literal(SCHEMA)}"]
    if not document["note"]:
        lines.append("note = []")
    for note in document["note"]:
        lines.extend(("", "[[note]]"))
        lines.extend(f"{key} = {_literal(note[key])}" for key in NOTE_FIELDS if key in note)
    return "\n".join(lines) + "\n"


def write_notes(path: Path, document: dict) -> None:
    from ..catalog.entrynotes import invalidate
    text = notes_text(document)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    invalidate(path)
    path.write_text(text, encoding="utf-8")
    # A reader during the write may retain old content.
    invalidate(path)
