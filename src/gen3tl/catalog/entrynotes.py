"""Authoritative language notes and generated catalog comments."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from ..core import notes as structured
from . import keys
from .guidance import _legacy_view
from .source import digest

MARKER = "source-notes: generated"
_paths: dict[Path, Path] = {}
_cache: dict[Path, tuple[tuple[int, int, str, keys.Aliases], dict[str, list[dict]]]] = {}


def path(catalog: Path) -> Path:
    """The note collection beside a language catalog."""
    return Path(catalog).parent / "notes" / "entries.toml"


def for_file(file: Path) -> Path | None:
    """Locate the containing catalog without crossing project boundaries."""
    from ..project import active
    file = Path(file).absolute()
    if file in _paths:
        return _paths[file]
    project = active()
    if project is not None and file.is_relative_to(project.catalog.absolute()):
        return path(project.catalog)
    root = next((parent for parent in file.parents if parent.name == "po"), None)
    return path(root) if root is not None else None


def remember(file: Path, notes_path: Path) -> None:
    """Retain explicit catalog locations for subsequent projections."""
    _paths[Path(file).absolute()] = Path(notes_path).absolute()


def load(file: Path, code: str, aliases=keys.EMPTY) -> dict[str, list[dict]]:
    """Copy complete records grouped by their resolved entry identity."""
    return deepcopy(_records(file, code, aliases))


def invalidate(file: Path) -> None:
    """Discard retained records when writes could preserve file stamps."""
    _cache.pop(Path(file).resolve(), None)


def _records(file: Path, code: str, aliases) -> dict[str, list[dict]]:
    """Retain validated records for projection without copying the collection."""
    file = Path(file).resolve()
    stat = file.stat()
    stamp = stat.st_mtime_ns, stat.st_size, code, aliases
    cached = _cache.get(file)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    document = structured.read_notes(file)
    selected = {}
    for note in document["note"]:
        where = f"{file}: {note['id']}"
        if note["subject"]["kind"] != "entry":
            raise ValueError(f"{where}: expected an entry subject")
        if note["scope"] != {"kind": "language", "language": code}:
            raise ValueError(f"{where}: expected language scope {code}")
        selected.setdefault(aliases.entry_id(note["subject"]["id"]), []).append(note)
    _cache[file] = stamp, selected
    return selected


def project(entries, file: Path | None, code: str, aliases=keys.EMPTY, digests=None):
    """Regenerate projections without accepting changes from catalog comments."""
    if file is None or not Path(file).exists():
        return entries
    records = _records(file, code, aliases)
    result = []
    for entry in entries:
        sid = aliases.entry_id(entry.msgctxt)
        notes = records.get(sid, [])
        generated = MARKER in entry.translator
        if not notes and not generated:
            result.append(entry)
            continue
        if notes and entry.source_note and not generated:
            raise ValueError(f"{file}: {sid}: both legacy and structured source notes")
        expected = (digests or {}).get(sid) if entry.keyed else digest(entry.msgid)
        blocks = []
        for note in notes:
            if expected is not None and note["binding"]["source_sha256"] != expected:
                raise ValueError(f"{file}: {sid}: source note differs from catalog English")
            if note["status"] in ("unreviewed", "accepted"):
                view = _legacy_view(note)
                blocks.append(view["text"] + "\n" + view["evidence"])
        projected = replace(entry, translator=[line for line in entry.translator if line != MARKER])
        text = "\n".join(blocks)
        # Escape separators that cannot occupy PO comments.
        for char in "\r\v\f\x00\x1c\x1d\x1e\x85\u2028\u2029":
            text = text.replace(char, f"\\u{ord(char):04x}")
        projected.source_note = text
        if notes:
            projected.translator.append(MARKER)
        result.append(projected)
    return result


def refresh(catalog: dict, code: str, aliases=keys.EMPTY, digests=None) -> None:
    """Refresh loaded entries before producing editor views."""
    files = {}
    for sid, (file, entry) in catalog.items():
        files.setdefault(for_file(file), []).append((sid, file, entry))
    for notes_path, rows in files.items():
        entries = project([entry for _, _, entry in rows], notes_path, code, aliases, digests)
        for (sid, file, _), entry in zip(rows, entries):
            catalog[sid] = file, entry
