"""Validate note contributions and apply them to the project's note files."""

from __future__ import annotations

import base64
import binascii
import functools
import hashlib
import json
import re
import tempfile
import tomllib
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from ..core import notes

SCHEMA = "3.0.0"
LEGACY_SCHEMA = "3.1.0"
CAPTURE_LIMIT = 3 * 1024 * 1024   # its base64 fits the 5 MiB export
CHANGE_LIMIT = 50
ATTACHMENT_LIMIT = 8
TEXT_LIMIT = 4000
NODE_LIMIT = 20000
REVIEW_LIMIT = 30000   # characters; a pull request body holds 65,536
BODY_LIMIT = 20000
SUBJECT_TEXT_LIMIT = 320
FIELDS = ("operation", "id", "subject", "scope", "base_revision", "base", "proposed")
OPTIONAL = ("binding", "message", "attachments")
CAPTURE = ("id", "name", "type", "size", "sha256", "data", "role", "build", "context")
NOTE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}")
ATTACHMENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
CONTROL = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029]")
APPLIED = ("entry", "term", "research", "graphic", "wording_rule")


class Refused(ValueError):
    """A refused note change; its reason may quote submitted text."""


def _refusing(function):
    @functools.wraps(function)
    def checked(*args, **kwargs):
        from ..core.graphics import GraphicsError
        try:
            return function(*args, **kwargs)
        except Refused:
            raise
        except RecursionError:
            raise Refused("note changes nest too deeply") from None
        except (ValueError, GraphicsError) as exc:
            raise Refused(str(exc)) from exc
    return checked


def _plain(value, where: str) -> None:
    """Refuse control characters, oversized text and oversized structures."""
    stack, count = [value], 0
    while stack:
        item = stack.pop()
        count += 1
        if count > NODE_LIMIT:
            raise ValueError(f"{where}: too large")
        if isinstance(item, dict):
            if any(not isinstance(key, str) or not key.isprintable() for key in item):
                raise ValueError(f"{where}: unsupported field name")
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, str):
            if CONTROL.search(item):
                raise ValueError(f"{where}: unsupported control character")
            if len(item) > TEXT_LIMIT:
                raise ValueError(f"{where}: text exceeds {TEXT_LIMIT} characters")


def _identity(value, pattern: re.Pattern, where: str) -> None:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{where}: expected letters, digits and . _ : / - only")


def legacy(record) -> bool:
    return isinstance(record, dict) and "subject" not in record


def note_target(record: dict) -> dict:
    """Resolve legacy metadata without changing the stored base."""
    if not legacy(record):
        return record
    subject = {"kind": "entry", "id": record["id"]}
    return {"id": record["id"], "subject": subject, "scope": {"kind": "shared"},
            "binding": {**subject, "source_sha256": record["source_sha256"]}}


@_refusing
def records(document: dict) -> list[dict]:
    """Require an explicit supported envelope, including empty changes."""
    if not {"note_changes", "note_changes_schema"} & document.keys():
        return []
    if document.get("note_changes_schema") not in (SCHEMA, LEGACY_SCHEMA):
        raise ValueError(f"note changes: unsupported schema; expected {SCHEMA} or {LEGACY_SCHEMA}")
    changes = document.get("note_changes")
    if not isinstance(changes, list):
        raise ValueError("note changes: expected an array")
    if len(changes) > CHANGE_LIMIT:
        raise ValueError(f"note changes: at most {CHANGE_LIMIT} per submission; split the submission")
    seen = set()
    for index, change in enumerate(changes, 1):
        where = f"note change {index}"
        if not isinstance(change, dict):
            raise ValueError(f"{where}: expected an object")
        _plain({key: value for key, value in change.items() if key != "attachments"}, where)
        attachments = change.get("attachments", [])
        if not isinstance(attachments, list) or len(attachments) > ATTACHMENT_LIMIT:
            raise ValueError(f"{where}: at most {ATTACHMENT_LIMIT} attachments")
        for item in attachments:
            _plain({key: value for key, value in item.items() if key != "data"} if isinstance(item, dict) else item,
                   f"{where}: capture")
        notes._object(change, FIELDS, OPTIONAL, where)
        _identity(change["id"], NOTE_ID, f"{where}.id")
        if change["id"] in seen:
            raise ValueError(f"duplicate note change: {change['id']}")
        seen.add(change["id"])
        notes._choice(change["operation"], ("add", "edit", "remove"), "note change.operation")
        notes._subject(change["subject"], "note change.subject")
        notes._scope(change["scope"], "note change.scope")
        notes._text(change["base_revision"], "note change.base_revision")
        if "message" in change:
            notes._text(change["message"], "note change.message")
        for key in ("base", "proposed"):
            record = change[key]
            absent = change["operation"] == ("add" if key == "base" else "remove")
            if absent:
                if record is not None:
                    raise ValueError(f"{change['id']}: {key} must be null")
            else:
                if key == "base" and legacy(record) and document["note_changes_schema"] == LEGACY_SCHEMA:
                    from ..catalog import guidance
                    guidance._validate(record, f"{where}.base")
                    if change["subject"]["kind"] != "entry":
                        raise ValueError(f"{change['id']}: legacy base requires an entry subject")
                    if change["operation"] == "edit" and change["id"] == record["id"]:
                        raise ValueError(f"{change['id']}: replacement requires an independent note identity")
                    if change["operation"] == "remove" and change["id"] != record["id"]:
                        raise ValueError(f"{change['id']}: base identity differs")
                    continue
                notes.validate_note(record)
                if record["id"] != change["id"] or record["subject"] != change["subject"]:
                    raise ValueError(f"{change['id']}: {key} identity differs")
        selected = note_target(change["proposed"] or change["base"])
        if selected["scope"] != change["scope"] or selected.get("binding") != change.get("binding"):
            raise ValueError(f"{change['id']}: scope or binding differs")
        if "binding" in change and change["binding"] is None:
            raise ValueError(f"{change['id']}: binding cannot be null")
    return changes


def _inventory_path(prof) -> Path | None:
    section = prof.get("graphics", {})
    return Path(prof.project_root) / section["inventory"] if section.get("inventory") else None


def inventory(project, prof, catalog: dict):
    """Resolve subjects from project inputs, never submitted destinations."""
    from ..build.graphics import graphic_notes
    from ..catalog import entrynotes, guidance, keys
    from ..catalog.source import digest
    from ..check.rules import list_path, load_list
    from ..lang import research, termbase
    from ..profile import schema
    from .editorsnapshot import project_digests

    aliases = keys.for_profile(prof)
    digests = project_digests() if any(entry.keyed for _, entry in catalog.values()) else {}
    subjects, current = {}, {}

    def add(kind, sid, binding=None, rows=()):
        key = kind, sid
        if key in subjects:
            raise ValueError(f"duplicate note subject: {kind}/{sid}")
        subjects[key] = binding
        collect(rows)

    def collect(rows):
        for row in rows:
            if legacy(row):
                guidance._validate(row, "legacy note")
            else:
                notes.validate_note(row)
            if row["id"] in current:
                raise ValueError(f"duplicate note identity: {row['id']}")
            current[row["id"]] = row

    for sid, (_, entry) in catalog.items():
        english = digests.get(sid) if entry.keyed else digest(entry.msgid)
        for key in dict.fromkeys((sid, aliases.key(sid))):
            add("entry", key, {"kind": "entry", "id": key, "source_sha256": english})
    collect(guidance.profile_rows(prof))
    path = entrynotes.path(project.catalog)
    if path.is_file():
        for rows in entrynotes.load(path, project.language, aliases).values():
            collect(rows)
    if project.glossary.is_file():
        for term in termbase.load(project.glossary):
            add("term", term.id, rows=term.note_records)
    for sid, row in research.index(project.translation / "research").items():
        add("research", sid, rows=row.note_records)
    for row in load_list(list_path(project.translation), code=project.language):
        if "id" in row:
            add("wording_rule", row["id"], rows=row.get("notes", []))
    graphics = _inventory_path(prof)
    if graphics is not None:
        for gid, rows in graphic_notes(graphics, notes_path=project.translation / "notes" / "graphics.toml",
                                       language=project.language).items():
            add("graphic", gid, {"kind": "graphic", "id": gid}, rows)
    if prof.path.exists() and prof.path.resolve().is_relative_to(project.registry_root.resolve()):
        sources = schema.read(prof.path) if prof.path.is_dir() else schema.flat(prof.path)
        for fact in schema.overlay_facts(sources):
            add("profile_fact", fact.id, fact, fact.note_records)
        if project.profile_notes.is_file():
            for rows in schema.language_notes(prof.path, project.profile_notes, project.language).values():
                collect(rows)
    return subjects, current


def _subject(record: dict, subjects: dict, language: str) -> None:
    from ..profile import schema

    kind, sid = record["subject"]["kind"], record["subject"]["id"]
    if (kind, sid) not in subjects:
        raise ValueError(f"{record['id']}: unknown note subject {kind}/{sid}")
    scope = record["scope"]
    if (scope != {"kind": "shared"} and scope != {"kind": "language", "language": language}
            or kind in ("term", "research", "wording_rule") and scope["kind"] != "language"):
        raise ValueError(f"{record['id']}: unsupported note scope")
    expected = subjects[kind, sid]
    if kind == "profile_fact":
        field = record["binding"].get("field", "")
        if not field:
            raise ValueError(f"{record['id']}: profile binding requires a prose field")
        schema._note_field(expected.value, field, record["id"])
        expected = {"kind": kind, "id": sid, "value_sha256": schema.value_digest(expected.value), "field": field}
    if expected is not None and record.get("binding") != expected:
        raise ValueError(f"{record['id']}: stale note binding")
    if kind == "entry" and expected["source_sha256"] is None:
        raise ValueError(f"{record['id']}: current English digest unavailable")


def _other_identities(project) -> set[str]:
    from ..catalog import entrynotes
    from ..check.rules import list_path, load_list
    from ..lang import language, research, termbase
    from ..project import Registry

    identities = set()
    for peer in Registry.load(project.registry_root).projects:
        if peer.profile != project.profile or peer.root != project.root or peer.language == project.language:
            continue
        with language.use(peer.language):
            rows = []
            for path in (entrynotes.path(peer.catalog), peer.translation / "notes" / "graphics.toml", peer.profile_notes):
                if path.is_file():
                    rows.extend(notes.read_notes(path)["note"])
            if peer.glossary.is_file():
                rows.extend(note for term in termbase.load(peer.glossary) for note in term.note_records)
            rows.extend(note for row in research.index(peer.translation / "research").values() for note in row.note_records)
            rows.extend(note for rule in load_list(list_path(peer.translation), code=peer.language) for note in rule.get("notes", []))
            for row in rows:
                if row["id"] in identities:
                    raise ValueError(f"duplicate note identity: {row['id']}")
                identities.add(row["id"])
    return identities


def _citations(record: dict, base: dict) -> None:
    sources = record.get("sources", [])
    for key in ("sources", "evidence"):
        for value in record.get(key, []):
            if value not in base.get(key, []) and value["verification"] != "unresolved":
                raise ValueError(f"{record['id']}: changed {key} require unresolved verification")
    for source in sources:
        if source["kind"] != "web":
            continue
        url = urlsplit(source["address"])
        if url.scheme not in ("http", "https") or not url.hostname or any(c.isspace() for c in source["address"]):
            raise ValueError(f"{record['id']}: web citation requires an HTTP or HTTPS address")
        if source not in base.get("sources", []):
            retrieved = source.get("retrieved")
            if retrieved is None or date.fromisoformat(retrieved) > date.today():
                raise ValueError(f"{record['id']}: new web citation requires a nonfuture retrieval date")
    if record["subject"]["kind"] == "term" and not any(s.get("quoted_term") or s.get("quote") for s in sources):
        raise ValueError(f"{record['id']}: term claims require a source and supporting term or quotation")
    if (record["scope"]["kind"] == "shared" or record["subject"]["kind"] == "graphic") and record.get("facts"):
        if not any(s["kind"] in ("rom", "project") and s.get("locator") for s in sources):
            raise ValueError(f"{record['id']}: game observations require a ROM or build reference and location")


def _attachments(change: dict) -> None:
    attachments = change.get("attachments", [])
    if not isinstance(attachments, list):
        raise ValueError(f"{change['id']}: attachments must be an array")
    originals = {item.get("context") for item in attachments if isinstance(item, dict) and item.get("role") == "original"}
    found = {}
    for item in attachments:
        notes._object(item, CAPTURE, (), "capture")
        _identity(item["id"], ATTACHMENT_ID, "capture.id")
        for key in ("name", "build", "context", "data"):
            notes._text(item[key], f"capture.{key}")
        notes._choice(item["role"], ("original", "current", "proposed", "failed"), "capture.role")
        notes._choice(item["type"], ("image/png", "image/jpeg"), "capture.type")
        notes._digest(item["sha256"], 64, "capture.sha256")
        if type(item["size"]) is not int or not 0 < item["size"] <= CAPTURE_LIMIT:
            raise ValueError("capture: invalid size; maximum 3 MiB")
        if len(item["data"]) > 4 * ((CAPTURE_LIMIT + 2) // 3):
            raise ValueError("capture: encoded image exceeds 3 MiB")
        try:
            raw = base64.b64decode(item["data"], validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("capture: invalid base64 image") from exc
        signature = b"\x89PNG\r\n\x1a\n" if item["type"] == "image/png" else b"\xff\xd8\xff"
        if (len(raw) != item["size"] or hashlib.sha256(raw).hexdigest() != item["sha256"]
                or not raw.startswith(signature)):
            raise ValueError("capture: image type, size or digest differs")
        address = "attachment:" + item["id"]
        if address in found:
            raise ValueError("capture: duplicate attachment identity")
        found[address] = {"address": address, "sha256": item["sha256"],
                          "locator": " · ".join(item[k] for k in ("role", "build", "context"))}
        if item["role"] != "original" and item["context"] not in originals:
            raise ValueError("capture: comparison requires a matching original")
    used = set()
    for evidence in (change["proposed"] or {}).get("evidence", []):
        for capture in evidence.get("captures", []):
            if capture["address"].startswith("attachment:"):
                if found.get(capture["address"]) != capture:
                    raise ValueError("capture: missing attachment or mismatched metadata")
                used.add(capture["address"])
    if used != found.keys():
        raise ValueError("capture: unreferenced attachment")


def _unreferenced(removed: set[str], records) -> None:
    for record in records:
        hit = removed & set(record.get("related_notes", []))
        if hit:
            raise ValueError(f"{record['id']}: relates to removed note {sorted(hit)[0]}")


@_refusing
def validate(document: dict, project, prof, catalog: dict, *, author: str, created: str) -> list[dict]:
    """Validate the entire batch before returning attributed changes."""
    from .editorimport import _object
    from ..catalog import keys

    changes = deepcopy(records(document))
    if not changes:
        return []
    notes._text(author, "note submission author")
    notes._date(created, "note submission date", timestamp=True)
    subjects, current = inventory(project, prof, catalog)
    foreign = _other_identities(project)
    if foreign & current.keys():
        raise ValueError("duplicate note identities across language projects")
    removed = {change["base"]["id"] for change in changes
               if change["operation"] == "remove" or legacy(change["base"])}
    available = set(current) - removed | {change["id"] for change in changes if change["proposed"] is not None}
    bases = set()
    aliases = keys.for_profile(prof)
    for change in changes:
        nid, base, proposed = change["id"], change["base"], change["proposed"]
        if nid in foreign:
            raise ValueError(f"{nid}: note identity belongs to another language project")
        if change["subject"]["kind"] not in APPLIED:
            raise ValueError(f"{nid}: profile fact notes change through a pull request to the profile")
        try:
            revision = json.loads(change["base_revision"], object_pairs_hook=_object)
        except (ValueError, RecursionError):
            raise ValueError(f"{nid}: invalid base revision") from None
        base_id = base["id"] if base is not None else nid
        if revision != base or base != current.get(base_id) or (base is not None and base_id not in current):
            raise ValueError(f"{nid}: stale note base")
        if base is not None:
            if base_id in bases:
                raise ValueError(f"{base_id}: duplicate note base")
            bases.add(base_id)
            _subject(note_target(base), subjects, project.language)
        if legacy(base):
            if aliases.entry_id(base_id) != aliases.entry_id(change["subject"]["id"]):
                raise ValueError(f"{nid}: legacy base belongs to another entry")
            if proposed is not None and nid in current:
                raise ValueError(f"{nid}: replacement note identity already exists")
        if proposed is not None:
            _subject(proposed, subjects, project.language)
            if proposed["status"] != "proposed" or any(k in proposed for k in ("reviewed_by", "reviewed_at")):
                raise ValueError(f"{nid}: contributed notes must remain proposed without review attribution")
            if base is None:
                if any(key in proposed for key in notes.ATTRIBUTION_FIELDS):
                    raise ValueError(f"{nid}: new note attribution comes from the authenticated workflow")
                proposed.update(author=author, created=created)
            elif any(proposed.get(k) != base.get(k) for k in ("author", "created", "updated")):
                raise ValueError(f"{nid}: existing note attribution cannot change")
            notes.validate_note(proposed, new=base is None)
            _citations(proposed, {} if legacy(base) else base or {})
            for related in proposed.get("related_notes", []):
                if related not in available:
                    raise ValueError(f"{nid}: unknown or removed related note {related}")
            related = [*proposed.get("related_subjects", []),
                       *(subject for evidence in proposed.get("evidence", []) for subject in evidence.get("related_subjects", []))]
            if any((subject["kind"], subject["id"]) not in subjects for subject in related):
                raise ValueError(f"{nid}: unknown related subject")
        _attachments(change)
    changed = {change["id"] for change in changes} | removed
    _unreferenced(removed, [*(record for nid, record in current.items() if nid not in changed),
                            *(change["proposed"] for change in changes if change["proposed"])])
    return changes


@dataclass
class Application:
    """Note files after a batch: TOML texts, research tables and glossary rows."""
    texts: dict[Path, str] = field(default_factory=dict)
    tables: dict[Path, object] = field(default_factory=dict)
    terms: list | None = None
    glossary: Path | None = None

    @property
    def files(self) -> list[Path]:
        return sorted({*self.texts, *self.tables, *([self.glossary] if self.terms is not None else [])})

    @_refusing
    def write(self, project, prof) -> None:
        """Write, reload every note file, and restore them on failure."""
        from ..catalog.entrynotes import invalidate
        from ..catalog.catalog import load_catalog
        from ..lang import research, termbase
        original = {path: path.read_bytes() if path.exists() else None for path in self.files}
        try:
            for path, text in self.texts.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                invalidate(path)
                path.write_text(text, encoding="utf-8")
            for path, table in self.tables.items():
                research.save(path, table)
            if self.terms is not None:
                termbase.save(self.glossary, self.terms)
            inventory(project, prof, load_catalog(project.catalog, english=False))
        except BaseException:
            for path, data in original.items():
                invalidate(path)
                if data is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(data)
            raise


def _operations(changes: list[dict]) -> dict[tuple[str, str], list[tuple[str, dict]]]:
    """Additions and removals by container; a scope change moves the note."""
    found = {}
    for change in changes:
        base, proposed = change["base"], change["proposed"]
        key = lambda record: (note_target(record)["subject"]["kind"], note_target(record)["scope"]["kind"])
        if base is not None and proposed is not None and key(base) == key(proposed):
            found.setdefault(key(base), []).append(("edit", change))
            continue
        if base is not None:
            found.setdefault(key(base), []).append(("remove", change))
        if proposed is not None:
            found.setdefault(key(proposed), []).append(("add", change))
    return found


def _edit(rows: list[dict], operations: list[tuple[str, dict]]) -> list[dict]:
    """Edits and removals keep the order; additions follow."""
    pending = {(change["base"]["id"] if operation != "add" else change["id"]): (operation, change)
               for operation, change in operations}
    out = []
    for row in rows:
        operation, change = pending.pop(row["id"], (None, None))
        if operation is None:
            out.append(row)
        elif operation == "edit":
            out.append(change["proposed"])
    for operation, change in pending.values():
        if operation != "add":
            raise ValueError(f"{change['id']}: the current note is not in its note file")
        out.append(change["proposed"])
    return out


def _document(path: Path) -> dict:
    return notes.read_notes(path) if path.is_file() else {"schema": SCHEMA, "note": []}


def _hosts(operations) -> dict[str, list[tuple[str, dict]]]:
    found = {}
    for operation, change in operations:
        found.setdefault(change["subject"]["id"], []).append((operation, change))
    return found


def _added(operations, subject: str, legacy: bool) -> None:
    if legacy and any(operation == "add" for operation, _ in operations):
        raise ValueError(f"{subject}: holds legacy note prose; structured notes need its migration first")


@_refusing
def plan(changes: list[dict], project, prof, catalog: dict, terms: list | None = None) -> Application:
    """The note files after the batch, validated before any write."""
    from ..catalog import entrynotes, guidance, keys
    from ..check.rules import list_path, list_text, load_list
    from ..lang import research, termbase

    application = Application()
    if not changes:
        return application
    aliases = keys.for_profile(prof)
    groups = _operations(changes)
    with tempfile.TemporaryDirectory(prefix="gen3tl-notes-") as tmp:
        staged = Path(tmp)
        if ("entry", "language") in groups:
            operations = groups["entry", "language"]
            path = entrynotes.path(project.catalog)
            for sid, selected in _hosts(operations).items():
                entry = catalog[aliases.entry_id(sid)][1]
                _added(selected, sid, bool(entry.source_note) and entrynotes.MARKER not in entry.translator)
            rows = _edit(_document(path)["note"], operations)
            application.texts[path] = notes.notes_text({"schema": SCHEMA, "note": rows})
        if ("entry", "shared") in groups:
            operations = groups["entry", "shared"]
            path = getattr(prof, "notes_path", None)
            if path is None:
                raise ValueError("shared entry notes need the project's source/notes.toml")
            path = Path(path)
            rows = tomllib.loads(path.read_text(encoding="utf-8")).get("note", []) if path.is_file() else []
            legacy = {aliases.entry_id(row["id"]) for row in rows if isinstance(row, dict) and "subject" not in row}
            for sid, selected in _hosts(operations).items():
                _added(selected, sid, aliases.entry_id(sid) in legacy)
            application.texts[path] = guidance.notes_text({"schema": SCHEMA, "note": _edit(rows, operations)})
        if ("term", "language") in groups:
            operations = groups["term", "language"]
            if not project.glossary.is_file():
                raise ValueError(f"{project.key} has no glossary; its submissions carry no term notes")
            terms = terms if terms is not None else termbase.load(project.glossary)
            by_id = {term.id: term for term in terms}
            path = project.glossary.parent / "notes" / "terms.toml"
            rows = _edit(_document(path)["note"], operations)
            records = {row["id"]: row for row in rows}
            hosts = _hosts(operations)
            for tid, selected in hosts.items():
                term = by_id[tid]
                _added(selected, tid, bool(term.editable_notes))
                gone = {change["id"] for operation, change in selected if operation == "remove"}
                added = [change["id"] for operation, change in selected if operation == "add"]
                term.note_ids = tuple(nid for nid in (*term.note_ids, *added) if nid not in gone)
            document = {"schema": SCHEMA, "note": rows}
            notes.validate(document)
            selected = termbase._glossary_notes(project.glossary, terms, document)
            for term, (related, findings) in zip(terms, termbase._related_notes(project.glossary, selected)):
                if term.id in hosts:
                    term.note_records = tuple(records[nid] for nid in term.note_ids)
                    term.related_note_records = related
                    term.research_findings = findings
                    if term.notes_cell and term.note_records and not termbase.all_references(term.note_records):
                        raise ValueError(f"{term.id}: holds legacy note prose; migrate it before adding structured prose")
                    if term.note_records:
                        term.notes_cell = (term.notes_cell or "") if termbase.all_references(term.note_records) else None
                        term.refresh_notes()
                    else:
                        term.notes, term.notes_cell = term.notes_cell or "", None
            application.texts[path] = notes.notes_text(document)
            application.terms, application.glossary = terms, project.glossary
        if ("research", "language") in groups:
            folder = project.translation / "research"
            hosts = _hosts(groups["research", "language"])
            for table_path in sorted(folder.glob("*.tsv")):
                table = research.load(table_path)
                rows_by_id = {row.record_id: row for row in table.rows if row.record_id}
                operations = [item for rid in hosts.keys() & rows_by_id.keys() for item in hosts[rid]]
                if not operations:
                    continue
                path = table_path.with_suffix(".notes.toml")
                rows = _edit(_document(path)["note"], operations)
                records = {row["id"]: row for row in rows}
                for rid in hosts.keys() & rows_by_id.keys():
                    row = rows_by_id[rid]
                    _added(hosts[rid], rid, bool(row.values.get("notes")) and not row.note_ids)
                    gone = {change["id"] for operation, change in hosts[rid] if operation == "remove"}
                    added = [change["id"] for operation, change in hosts[rid] if operation == "add"]
                    row.values["note_ids"] = "|".join(nid for nid in (*row.note_ids, *added) if nid not in gone)
                    row.note_records = tuple(records[nid] for nid in row.note_ids)
                text = notes.notes_text({"schema": SCHEMA, "note": rows})
                (staged / path.name).write_text(text, encoding="utf-8")
                research._records(staged / table_path.name, table)
                application.texts[path], application.tables[table_path] = text, table
        if ("wording_rule", "language") in groups:
            path = list_path(project.translation)
            if path.suffix != ".toml":
                raise ValueError("wording-rule notes need the structured rules.toml")
            rows = load_list(path, code=project.language)
            by_id = {row["id"]: row for row in rows}
            for rid, selected in _hosts(groups["wording_rule", "language"]).items():
                rule = by_id[rid]
                _added(selected, rid, "notes" not in rule)
                rule["notes"] = _edit(rule.get("notes", []), selected)
                if not rule["notes"]:
                    raise ValueError(f"{rid}: a wording rule keeps at least one note")
            application.texts[path] = list_text(rows, code=project.language)
        if ("graphic", "shared") in groups or ("graphic", "language") in groups:
            from ..build.graphics import graphic_notes, graphics_text
            from ..core.graphics import graphics_document
            inventory_path = _inventory_path(prof)
            if inventory_path is None or inventory_path.suffix != ".toml":
                raise ValueError("graphic notes need the structured graphics inventory")
            document = graphics_document(inventory_path)
            by_id = {row["id"]: row for row in document["graphic"]}
            for gid, selected in _hosts(groups.get(("graphic", "shared"), [])).items():
                row = by_id[gid]
                _added(selected, gid, "notes_schema" not in row)
                row["notes"] = _edit(row.get("notes", []), selected)
            for gid, selected in _hosts(groups.get(("graphic", "language"), [])).items():
                _added(selected, gid, "notes_schema" not in by_id[gid])
            shared = staged / inventory_path.name
            shared.write_text(graphics_text(document["graphic"], document), encoding="utf-8")
            language_path = project.translation / "notes" / "graphics.toml"
            local = staged / "graphics.notes.toml"
            if ("graphic", "language") in groups:
                rows = _edit(_document(language_path)["note"], groups["graphic", "language"])
                local.write_text(notes.notes_text({"schema": SCHEMA, "note": rows}), encoding="utf-8")
            elif language_path.is_file():
                local.write_bytes(language_path.read_bytes())
            graphic_notes(shared, notes_path=local, language=project.language)
            if ("graphic", "shared") in groups:
                application.texts[inventory_path] = shared.read_text(encoding="utf-8")
            if ("graphic", "language") in groups:
                application.texts[language_path] = local.read_text(encoding="utf-8")
    root = project.root.resolve()
    if any(not path.resolve().is_relative_to(root) for path in application.files):
        raise ValueError("note file outside this project")
    return application


def prepare(document: dict, project, prof, catalog: dict, *, author: str, created: str):
    """Validated changes and their note files, for routes without term edits."""
    changes = validate(document, project, prof, catalog, author=author, created=created)
    return changes, plan(changes, project, prof, catalog)


def _containers(project, prof) -> dict[Path, str]:
    """Note files by the shape that holds their records."""
    from ..catalog import entrynotes
    found = {entrynotes.path(project.catalog): "note", project.translation / "notes" / "terms.toml": "note",
             project.translation / "notes" / "graphics.toml": "note", project.profile_notes: "note",
             project.translation / "rules.toml": "rule"}
    for path in (project.translation / "research").glob("*.tsv"):
        found[path.with_suffix(".notes.toml")] = "note"
    if getattr(prof, "notes_path", None) is not None:
        found[Path(prof.notes_path)] = "note"
    graphics = _inventory_path(prof)
    if graphics is not None and graphics.suffix == ".toml":
        found[graphics] = "graphic"
    return {path.resolve(): shape for path, shape in found.items()}


def _stored(shape: str, data: bytes) -> dict[str, dict]:
    document = tomllib.loads(data.decode("utf-8"))
    if shape == "rule":
        rows = [note for rule in document.get("rule", []) if isinstance(rule, dict) for note in rule.get("notes", [])]
    elif shape == "graphic":
        rows = [note for row in document.get("graphic", []) if isinstance(row, dict) for note in row.get("notes", [])]
    else:
        rows = document.get("note", [])
    return {row["id"]: row for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)}


def note_files(project, prof, files) -> list[Path]:
    """The note files among `files`."""
    shapes = _containers(project, prof)
    return sorted(Path(path) for path in files if Path(path).resolve() in shapes)


@_refusing
def compare(project, prof, catalog: dict, files, before) -> list[dict]:
    """Validated note changes of the changed note files against their base contents."""
    shapes = _containers(project, prof)
    selected = note_files(project, prof, files)
    if not selected:
        return []
    subjects, current = inventory(project, prof, catalog)
    old, new = {}, {}
    for path in selected:
        shape = shapes[Path(path).resolve()]
        relative = Path(path).resolve().relative_to(project.registry_root.resolve()).as_posix()
        data = before(path)
        for side, content in ((old, data), (new, path.read_bytes() if path.exists() else None)):
            if content is not None:
                side.update((nid, (relative, record)) for nid, record in _stored(shape, content).items())
    changes = []
    for nid in sorted(old.keys() | new.keys()):
        (source, base), (target, proposed) = old.get(nid, (None, None)), new.get(nid, (None, None))
        if base == proposed:
            continue
        operation = "add" if base is None else "remove" if proposed is None else "edit"
        changes.append({"operation": operation, "id": nid, "base": base, "proposed": proposed,
                        "file": target or source})
    foreign = _other_identities(project)
    for change in changes:
        record = change["proposed"]
        if record is None:
            continue
        if record["id"] in foreign:
            raise ValueError(f"{record['id']}: note identity belongs to another language project")
        _subject(note_target(record), subjects, project.language)
        for related in record.get("related_notes", []):
            if related not in current:
                raise ValueError(f"{record['id']}: unknown related note {related}")
    _unreferenced({change["id"] for change in changes if change["proposed"] is None}, current.values())
    return changes


def inline(value) -> str:
    """Text as inline code: no mention, link, image, reference or line."""
    text = CONTROL.sub("\ufffd", str(value)).replace("\n", " \u21b5 ").replace("|", "\\|") or " "
    tick = "`" * (1 + max(map(len, re.findall(r"`+", text)), default=0))
    pad = " " if text.startswith(("`", " ")) or text.endswith(("`", " ")) else ""
    return f"{tick}{pad}{text}{pad}{tick}"


def _review_value(field: str, value) -> str:
    if field == "sources":
        optional = ("locator", "retrieved", "quoted_term", "quote", "relation")
        value = [{**source, **{key: source.get(key, "not recorded") for key in optional}} for source in value]
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _scope(record: dict) -> str:
    scope = record["scope"]
    return "shared" if scope["kind"] == "shared" else f"language: {scope['language']}"


def subject_texts(project, prof, catalog: dict, changes: list[dict]) -> dict:
    """Resolve review context without adding it to stored notes."""
    from ..catalog import keys
    from ..check.rules import list_path, load_list
    from ..lang import research, termbase

    selected = {(record["subject"]["kind"], record["subject"]["id"])
                for change in changes for record in (note_target(change["proposed"] or change["base"]),)}
    found = {}
    aliases = keys.for_profile(prof)
    for kind, sid in selected:
        if kind == "entry" and aliases.entry_id(sid) in catalog:
            entry = catalog[aliases.entry_id(sid)][1]
            found[kind, sid] = [("English", "text unavailable" if entry.keyed else entry.msgid),
                                ("Current translation", entry.msgstr)]
    if any(kind == "term" for kind, _ in selected) and project.glossary.is_file():
        for term in termbase.load(project.glossary):
            if ("term", term.id) in selected:
                found["term", term.id] = [("Term", term.source_text), ("Selected form", term.translation)]
    if any(kind == "research" for kind, _ in selected):
        for sid, row in research.index(project.translation / "research").items():
            if ("research", sid) in selected:
                values = {key: value for key, value in row.values.items()
                          if key not in (*research.IDENTITY_COLUMNS, "notes") and value}
                found["research", sid] = [("Research row", json.dumps(values, ensure_ascii=False))]
    if any(kind == "wording_rule" for kind, _ in selected):
        for row in load_list(list_path(project.translation), code=project.language):
            if ("wording_rule", row.get("id")) in selected:
                text = row.get("rule") or "\n".join(
                    text for note in row.get("notes", []) for field in notes.PROSE_FIELDS for text in note.get(field, []))
                found["wording_rule", row["id"]] = [("Wording rule", text)]
    return found


def _subject_text(record: dict, subjects: dict) -> str:
    subject = record["subject"]
    fields = subjects.get((subject["kind"], subject["id"]), [("Subject", "text unavailable")])
    if subject["kind"] == "entry":
        fields = [*fields, ("English SHA-256", record.get("binding", {}).get("source_sha256", "unavailable"))]
    cells = []
    for label, value in fields:
        text = str(value)
        if len(text) > SUBJECT_TEXT_LIMIT:
            text = text[:SUBJECT_TEXT_LIMIT] + "… [truncated]"
        cells.append(f"{label}: {inline(text)}")
    return "; ".join(cells)


def _row(change: dict, subjects: dict) -> str:
    record = note_target(change["proposed"] or change["base"])
    state = "removal" if change["proposed"] is None else record.get("status", "legacy")
    values = [change["id"], change["operation"], f"{record['subject']['kind']}/{record['subject']['id']}",
              _scope(record), state]
    if "file" in change:
        values.append(change["file"])
    return "| " + " | ".join([*(inline(value) for value in values), _subject_text(record, subjects)]) + " |"


def review(changes: list[dict], limit: int | None = None, *, subjects: dict | None = None) -> tuple[str, bool]:
    """The review of note changes, bounded by `limit`, and whether it was cut."""
    head = ["## Note changes", "",
            "- [ ] Review content, source relevance, quotations, locators and verification gaps.",
            "- [ ] Accept notes on this branch with `gen3tl check accept-notes NOTE --reviewer NAME --date YYYY-MM-DD`.",
            "- [ ] Record verified citations with `--checked-source NOTE INDEX` (1-based source index).",
            "- [ ] Approve note guidance independently of accompanying game changes.", "",
            "A merged note with status proposed stays stored without becoming guidance. "
            "Attribution is shown as the note files record it; this check does not authenticate it.", "",
            "| Note | Operation | Subject | Scope | Review state | File | Subject text |", "|---|---|---|---|---|---|---|"]
    rows = [_row(change, subjects or {}) for change in changes]
    details = []
    for change in changes:
        base, proposed = change["base"], change["proposed"]
        lines = ["", f"### {change['operation'].title()} {inline(change['id'])}", "",
                 "| Field | Current | Proposed |", "|---|---|---|"]
        for name in (*notes.NOTE_FIELDS, "text", "source_sha256"):
            if not any(side is not None and name in side for side in (base, proposed)):
                continue
            values = []
            for side, absent in ((base, "no current note"), (proposed, "removed")):
                values.append(absent if side is None else "not recorded" if name not in side
                              else _review_value(name, side[name]))
            lines.append(f"| {name} | " + " | ".join(inline(value) for value in values) + " |")
        details.append("\n".join(lines))
    full = "\n".join([*head, *rows, *details]).rstrip() + "\n"
    if limit is None or len(full) <= limit:
        return full, False
    shown = []
    budget = limit - sum(len(line) + 1 for line in head) - 400
    for row in rows:
        budget -= len(row) + 1
        if budget < 0:
            break
        shown.append(row)
    tail = ["", f"Rows shown: {len(shown)} of {len(changes)}. The complete review exceeds {limit} characters; "
                "it is note-review.md in the pr-check artifact."]
    return "\n".join([*head, *shown, *tail]) + "\n", True


def provenance(changes: list[dict], *, submitter: str, submitted: str, sha256: str, files,
               subjects: dict | None = None) -> str:
    """The authenticated submission record of the pull request body."""
    head = ["## Note submission", "",
            "| Submission provenance | Value |", "|---|---|",
            f"| Submitted by | {inline(submitter)}, the issue author |",
            f"| Submitted at | {inline(submitted)} |",
            f"| Export SHA-256 | {inline(sha256)} |",
            f"| Note files | {', '.join(inline(path) for path in files)} |", "",
            "| Note | Operation | Subject | Scope | Review state | Subject text |", "|---|---|---|---|---|---|",
            *(_row(change, subjects or {}) for change in changes)]
    messages = [f"- {inline(change['id'])}: {inline(change['message'])}" for change in changes if change.get("message")]
    captures = [(change["id"], item) for change in changes for item in change.get("attachments", [])]
    extra = []
    if messages:
        extra += ["", "Reviewer messages:", "", *messages]
    if captures:
        extra += ["", "Evidence captures stay in the export file of the issue; they are not replacement pictures.", "",
                  "| Note | Attachment | File | Media type | Bytes | SHA-256 | Role | Build | Context |",
                  "|---|---|---|---|---|---|---|---|---|",
                  *("| " + " | ".join(inline(value) for value in (nid, *(item[key] for key in CAPTURE if key != "data")))
                    + " |" for nid, item in captures)]
    text = "\n".join([*head, *extra]) + "\n"
    if len(text) > BODY_LIMIT:
        text = "\n".join([*head, "", "Reviewer messages and evidence captures: in the export file of the issue."]) + "\n"
    if len(text) > BODY_LIMIT:
        text = "\n".join(head[:8] + ["", f"Note changes: {len(changes)}; listed in the pull request checks."]) + "\n"
    return text
