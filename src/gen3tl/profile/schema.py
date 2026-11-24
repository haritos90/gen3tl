"""The fact model and the profile directory: generated facts, the manual overlay and their merge."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..core import notes
from ..core.rom import RomError
from . import metadata, tsv

SCHEMA = 1
GENERATED = "profile.toml"
DISCOVERY = "discovery.json"   # generated metadata of the discovery run
OVERLAY = "overlay.toml"
OVERLAY_DIR = "overlay"
OVERLAY_ORDER = "order.txt"
REPORT = "report.md"
HEADER = "discovery"   # generated profile metadata table
RECORD = "fact"        # fact records, in both files
TEXTS = "texts"        # generated fact sentences stated more than once

KINDS = ("identity", "family", "font", "table", "pointer-table", "string", "reference", "script-command", "map",
         "window", "keyboard", "save", "sprite-table", "graphic", "code", "reach", "context", "project",
         "unclassified")
SCOPES = ("instance", "family")
TIERS = ("shape", "crossref", "ingame")
STATUSES = ("verified", "candidate", "omitted", "unresolved")
OVERLAY_SOURCE = "overlay"
OVERLAY_TABLES = {
    "pokedex/items/shops.tsv": (("pokedex", "items", "sources", "shops"), {
        "map": "str", "map_section": "hex", "offset": "hex", "list": "hex", "items": "int[]",
    }),
    "pokedex/form-names.tsv": (("pokedex", "form_names", "labels"), {
        "species": "int", "text": "str", "full": "bool", "expected_gender": "int",
    }),
    "reach/never-shown.tsv": (("reach", "never_shown"), {"ids": "str[]", "evidence": "str"}),
    "progress.tsv": (("progress", "topics"), {"topic": "str", "files": "str[]", "evidence": "str"}),
    "layout/windows.tsv": (("layout", "window"), {
        "name": "str", "where": "str", "width": "int", "box": "str", "lines": "int", "style": "str", "evidence": "str",
        "prompt_arrow_width": "int", "newline_scroll_after": "int", "instant_print": "bool", "slot_context": "str",
    }),
    "layout/window-strings.tsv": (("layout", "window_strings"), {"string": "str", "window": "str"}),
}
WINDOW_FIELDS = ("width", "box", "lines", "style", "prompt_arrow_width", "newline_scroll_after", "instant_print", "slot_context")

# section kinds; else the top-level table's kind
SECTION_KINDS = {
    "rom": "identity", "engine": "family",
    "font": "font", "font_style": "font", "font_styles": "font", "sprite_font": "font",
    "table": "table", "pokedex": "table", "easy_chat_alphabet": "table",
    "pokedex.sprites": "sprite-table", "pokedex.icons": "sprite-table", "pokedex.type_icons": "sprite-table",
    "pokedex.trainers.pictures": "sprite-table",
    "strings": "reference", "coverage": "reference",
    "strings.pointer_table": "pointer-table", "reach.table": "pointer-table", "keys": "pointer-table",
    "strings.text_mode": "string", "text_controls": "string", "text_prefixes": "string", "probe": "string",
    "script": "script-command", "maps": "map", "layout": "window", "keyboard": "keyboard", "save": "save",
    "graphics": "graphic", "code_replacement": "code", "reach": "reach", "context": "context",
    "project": "project", "support": "project", "batches": "project", "progress": "project",
    "policy_slots": "project", "translator_notes": "project",
}
# element identity keys, preferred first
IDENTITY_KEYS = ("name", "key", "id", "topic", "offset", "pointer", "at", "first", "start", "kind", "ids")
# lists whose entries are facts
ENTRY_LISTS = frozenset({"strings.include", "strings.exclude"})
RECORD_KEYS = frozenset({"id", "kind", "scope", "tier", "status", "source", "evidence", "notes_schema", "notes"})
EVIDENCE_KEYS = frozenset({"sentence", "method", "anchors", "code_sites", "counts"})
BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
BARE_VALUE = re.compile(r"[A-Za-z0-9_.:+-]+")
GENERATED_NOTE = ("# Discovery regeneration overwrites manual edits to these facts.\n"
                  "# Overlay facts stay separate so loading overrides generated values.\n")
GENERATED_HEADER = f"[{HEADER}]\nschema = {SCHEMA}\n"
GENERATED_TEXT = f"{GENERATED_NOTE}\n{GENERATED_HEADER}"


class ProfileError(RomError):
    pass


@dataclass(frozen=True)
class Evidence:
    """How a fact was proven: method, anchors, code sites, counts, and one sentence."""
    sentence: str
    method: str = ""
    anchors: tuple[int, ...] = ()
    code_sites: tuple[int, ...] = ()
    counts: tuple[tuple[str, int], ...] = ()


@dataclass
class Fact:
    """One statement about a ROM: a profile value and what proves it."""
    id: str
    section: str
    kind: str
    value: Any
    scope: str = "instance"
    tier: str | None = None
    status: str = "verified"
    source: str = OVERLAY_SOURCE
    evidence: Evidence | None = None
    node: dict | None = field(default=None, repr=False, compare=False)   # the table holding the fields
    note_records: tuple[dict, ...] = field(default=(), repr=False)


@dataclass
class Sources:
    """Instance files and optional family defaults, with metadata split off."""
    path: Path
    overlay_file: Path
    generated: dict
    overlay: dict
    header: dict
    generated_records: list
    overlay_records: list
    family_data: dict = field(default_factory=dict)
    family_file: Path | None = None
    family_facts: list[Fact] = field(default_factory=list)
    overlay_files: list[Path] = field(default_factory=list)
    external_references: list[dict[str, str]] = field(default_factory=list)


def is_prose(key: str) -> bool:
    return key in ("evidence", "note") or key.endswith("_evidence")


def value_digest(value: Any) -> str:
    """Bind notes to technical values, excluding prose recursively."""
    def technical(item):
        if isinstance(item, dict):
            return {key: technical(child) for key, child in item.items()
                    if not (is_prose(key) and isinstance(child, str))}
        if isinstance(item, list):
            return [technical(child) for child in item]
        return item

    text = json.dumps(technical(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_table_array(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, dict) for v in value)


def key_text(key: str) -> str:
    return key if BARE_KEY.fullmatch(key) else json.dumps(key, ensure_ascii=False)


def path_id(path: tuple[str, ...]) -> str:
    return ".".join(key_text(k) for k in path)


def atom(value: Any) -> str:
    """A value as it appears in a fact id: integers in hex."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"{value:#x}" if value >= 0 else f"-{-value:#x}"
    if isinstance(value, str):
        return value if BARE_VALUE.fullmatch(value) else json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return ",".join(atom(v) for v in value)
    return json.dumps(value, ensure_ascii=False, default=str)


def classifier_target(value: Any) -> tuple[str, int | str]:
    """Validate a string outcome and resolve its compatibility identity."""
    if isinstance(value, dict):
        value = {key: item for key, item in value.items() if not is_prose(key)}
    if (not isinstance(value, dict) or set(value) != {"name", "action", "offset", "classifier"}
            or value["action"] not in ("include", "exclude")
            or type(value["offset"]) is not int or value["offset"] < 0
            or any(not isinstance(value[key], str) or not value[key].strip()
                   for key in ("name", "classifier"))):
        raise ValueError("strings.classified: invalid classifier outcome")
    action, offset = value["action"], value["offset"]
    return action, offset if action == "include" else f"s{offset:07X}"


def _names(value: Any) -> bool:
    if isinstance(value, list):
        return bool(value) and all(isinstance(v, (str, int)) for v in value)
    return isinstance(value, (str, int))


def element_ids(elements: list) -> list[str]:
    """Identity of each list element: its first identity key, a repeat counter, else its position."""
    seen, out = Counter(), []
    for i, element in enumerate(elements):
        if isinstance(element, dict):
            key = next((k for k in IDENTITY_KEYS if k in element and _names(element[k])), None)
            base = f"{key}={atom(element[key])}" if key else None
        else:
            base = atom(element)
        if base is None:
            out.append(f"#{i}")
            continue
        seen[base] += 1
        out.append(base if seen[base] == 1 else f"{base}#{seen[base]}")
    return out


def section_of(path: tuple[str, ...]) -> str:
    return path_id(path[:2])


def kind_of(section: str) -> str:
    return SECTION_KINDS.get(section) or SECTION_KINDS.get(section.split(".", 1)[0], "unclassified")


def facts(data: dict, named: frozenset[str] = frozenset()) -> list[Fact]:
    """Every fact of a profile document, in document order."""
    out: list[Fact] = []
    _walk(data, (), named, out)
    return out


def _fact(fact_id: str, section: str, value: Any, node: dict | None = None) -> Fact:
    return Fact(fact_id, section, kind_of(section), value, node=node)


def _walk(node: dict, path: tuple[str, ...], named: frozenset[str], out: list[Fact]) -> None:
    for key, value in node.items():
        here = path + (key,)
        if isinstance(value, dict):
            if isinstance(value.get("evidence"), str) or path_id(here) in named:
                own = {k: v for k, v in value.items() if not isinstance(v, dict) and not is_table_array(v)}
                out.append(_fact(path_id(here), section_of(here), own, value))
                _walk({k: v for k, v in value.items() if k not in own}, here, named, out)
            else:
                _walk(value, here, named, out)
        elif is_table_array(value):
            for element_id, element in zip(element_ids(value), value):
                out.append(_fact(f"{path_id(here)}[{element_id}]", section_of(here), element, element))
        elif path_id(here) in ENTRY_LISTS and isinstance(value, list):
            for element_id, element in zip(element_ids(value), value):
                out.append(_fact(f"{path_id(here)}[{element_id}]", section_of(here), element))
        else:
            out.append(_fact(path_id(here), section_of(path or here), value))


def merge(base: dict, over: dict, path: tuple[str, ...] = ()) -> dict:
    """Higher-precedence facts over lower-precedence facts."""
    out = {}
    for key, value in over.items():
        here = path + (key,)
        if key not in base:
            out[key] = value
        elif isinstance(base[key], dict) and isinstance(value, dict):
            out[key] = merge(base[key], value, here)
        elif is_table_array(base[key]) and is_table_array(value):
            out[key] = _merge_elements(base[key], value, here)
        elif path_id(here) in ENTRY_LISTS and isinstance(base[key], list) and isinstance(value, list):
            out[key] = base[key] + [v for v in value if v not in base[key]]
        else:
            out[key] = value
    out.update((key, value) for key, value in base.items() if key not in over)
    return out


def _merge_elements(base: list, over: list, path: tuple[str, ...]) -> list:
    over_ids = element_ids(over)
    by_id = dict(zip(element_ids(base), base))
    out = [merge(by_id[i], element, path) if i in by_id else element for i, element in zip(over_ids, over)]
    known = set(over_ids)
    return out + [element for i, element in zip(element_ids(base), base) if i not in known]


def _read(path: Path) -> dict:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(f"{path}: {exc}") from exc


def overlay_files(path: Path) -> list[Path]:
    """Overlay inputs in merge order, including the directory manifest."""
    return [file for file, _ in _overlay_entries(path)]


def _overlay_entries(path: Path) -> list[tuple[Path, tuple[str, ...]]]:
    single, directory = path / OVERLAY, path / OVERLAY_DIR
    if single.exists() and directory.exists():
        raise ProfileError(f"{path}: both {OVERLAY} and {OVERLAY_DIR}/ exist; keep one")
    if not directory.exists():
        return [(single, ())] if single.is_file() else []
    order = directory / OVERLAY_ORDER
    if not order.is_file():
        raise ProfileError(f"{directory}: missing {OVERLAY_ORDER}")
    entries = {}
    for row, line in enumerate(order.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.strip().startswith("#"):
            continue
        fields = line.split("\t")
        name = fields[0].strip()
        where = f"{order}: row {row}, column 1"
        relative = Path(name)
        target = directory / relative
        if (relative.is_absolute() or ".." in relative.parts or relative.as_posix() != name
                or relative.suffix not in (".toml", ".tsv")
                or not target.resolve().is_relative_to(directory.resolve())):
            raise ProfileError(f"{where}: invalid overlay file {name!r}")
        if name.endswith(".notes.toml"):
            raise ProfileError(f"{where}: note files load through their sibling TSV table")
        document_path = ()
        if relative.suffix == ".tsv":
            if name not in OVERLAY_TABLES:
                raise ProfileError(f"{where}: undeclared TSV file {name!r}")
            document_path, _ = OVERLAY_TABLES[name]
            if len(fields) != 2 or fields[1].strip() != path_id(document_path):
                raise ProfileError(f"{order}: row {row}, column 2: expected document path {path_id(document_path)}")
        elif len(fields) != 1:
            raise ProfileError(f"{order}: row {row}, column 2: TOML fragment takes no document path")
        if target in entries:
            raise ProfileError(f"{where}: repeated file {name!r}")
        if not target.is_file():
            raise ProfileError(f"{where}: missing file {name!r}")
        entries[target] = document_path
        if relative.suffix == ".tsv":
            sibling = target.with_suffix(".notes.toml")
            if sibling.exists():
                if not sibling.is_file() or not sibling.resolve().is_relative_to(directory.resolve()):
                    raise ProfileError(f"{where}: invalid note file {sibling.name!r}")
                entries[sibling] = ()
    unlisted = sorted(p.relative_to(directory).as_posix() for p in directory.rglob("*")
                      if p.is_file() and p != order and p not in entries)
    if unlisted:
        raise ProfileError(f"{order}: unlisted files: {', '.join(repr(name) for name in unlisted)}")
    return [(order, ()), *entries.items()]


def _join_overlay(base: dict, fragment: dict, file: Path | str, path: tuple[str, ...] = ()) -> None:
    """Join disjoint values without changing their first-seen order."""
    for key, value in fragment.items():
        here = path + (key,)
        if key not in base:
            base[key] = value
        elif isinstance(base[key], dict) and isinstance(value, dict):
            _join_overlay(base[key], value, file, here)
        else:
            raise ProfileError(f"{file}: duplicate overlay key {path_id(here)}")


def _table_notes(file: Path, data: dict) -> list[dict]:
    """Adapt sibling collections to the existing fact records."""
    sibling = file.with_suffix(".notes.toml")
    if not sibling.is_file():
        return []
    try:
        document = notes.read_notes(sibling)
    except notes.NoteError as exc:
        raise ProfileError(str(exc)) from exc
    found = {fact.id for fact in facts(data) if fact.node is not None}
    records = {}
    for note in document["note"]:
        subject = note["subject"]
        label = f"{sibling}: note {note['id']}"
        if subject["kind"] != "profile_fact" or subject["id"] not in found:
            raise ProfileError(f"{label}: subject names no row fact in {file.name}")
        if note["binding"].get("field") != "evidence":
            raise ProfileError(f"{label}: note binding requires field evidence")
        record = records.setdefault(subject["id"], {"id": subject["id"], "notes_schema": document["schema"],
                                                    "notes": []})
        record["notes"].append(note)
    return list(records.values())


def _expand_windows(overlay: dict, tables: dict[str, tuple[Path, list[dict]]], generated: dict | None = None) -> None:
    """Expand window bindings after collecting every overlay map."""
    if not tables:
        return
    window_file, rows = tables.get("window", (None, []))
    defaults = {row["name"]: row for row in (generated or {}).get("layout", {}).get("window", [])}
    windows = {}
    for number, row in enumerate(rows, 2):
        name = row.get("name", "")
        if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", name):
            raise tsv.error(window_file, number, 1, f"invalid window name {name!r}")
        if name in windows:
            raise tsv.error(window_file, number, 1, f"duplicate window name {name!r}")
        windows[name] = {**defaults.get(name, {}), **row}
    for string, binding in (generated or {}).get("layout", {}).get("window_binding", {}).items():
        if binding.get("name") in windows:
            overlay.setdefault("layout", {}).setdefault("window_binding", {})[string] = dict(windows[binding["name"]])
    layout = overlay.get("layout", {})
    existing = set()
    if isinstance(layout, dict):
        for field in WINDOW_FIELDS:
            values = layout.get(f"window_{field}", {})
            if isinstance(values, dict):
                existing.update(values)
    string_file, bindings = tables.get("window_strings", (None, []))
    seen, used = set(), set()
    for number, binding in enumerate(bindings, 2):
        for column, key in enumerate(("string", "window"), 1):
            if key not in binding:
                raise tsv.error(string_file, number, column, f"missing {key}")
        string, name = binding["string"], binding["window"]
        if string in seen:
            raise tsv.error(string_file, number, 1, f"duplicate string {string!r}")
        if string in existing:
            raise tsv.error(string_file, number, 1, f"string {string!r} already in an overlay TOML window map")
        if name not in windows:
            raise tsv.error(string_file, number, 2, f"unknown window {name!r}")
        seen.add(string)
        used.add(name)
        for field in WINDOW_FIELDS:
            if field in windows[name]:
                fragment = {"layout": {f"window_{field}": {string: windows[name][field]}}}
                _join_overlay(overlay, fragment, f"{string_file!s}: row {number}, column 1")
        if any(field in windows[name] for field in ("newline_scroll_after", "instant_print")):
            overlay["layout"].setdefault("window_binding", {})[string] = dict(windows[name])
    for number, row in enumerate(rows, 2):
        annotation = row["name"] in defaults and set(row) <= {"name", "evidence", "where"}
        if row["name"] not in used and not annotation:
            raise tsv.error(window_file, number, 1, f"window {row['name']!r} has no strings")


def read(path: str | Path) -> Sources:
    """Read a profile directory with an optional file or directory overlay."""
    path = Path(path)
    if not (path / GENERATED).is_file():
        raise ProfileError(f"{path}: profile directory without {GENERATED}")
    generated = _read(path / GENERATED)
    entries = _overlay_entries(path)
    overlay_file = path / OVERLAY_DIR if (path / OVERLAY_DIR).is_dir() else path / OVERLAY
    overlay = {}
    table_records = []
    window_tables = {}
    for file, document_path in entries:
        if file.name == OVERLAY_ORDER or file.name.endswith(".notes.toml"):
            continue
        if not document_path:
            _join_overlay(overlay, _read(file), file)
            continue
        _, columns = OVERLAY_TABLES[file.relative_to(overlay_file).as_posix()]
        try:
            fragment = tsv.read(file, columns)
        except tsv.TSVError as exc:
            raise ProfileError(str(exc)) from exc
        if document_path in (("layout", "window"), ("layout", "window_strings")):
            window_tables[document_path[-1]] = (file, fragment)
            if document_path[-1] == "window_strings":
                table_records.extend(_table_notes(file, {}))
                continue
        for key in reversed(document_path):
            fragment = {key: fragment}
        table_records.extend(_table_notes(file, fragment))
        _join_overlay(overlay, fragment, f"{file}: row 1, column 1")
    try:
        _expand_windows(overlay, window_tables, generated)
    except tsv.TSVError as exc:
        raise ProfileError(str(exc)) from exc
    header = generated.pop(HEADER, None)
    texts = generated.pop(TEXTS, [])
    if not isinstance(header, dict) or header.get("schema") != SCHEMA:
        raise ProfileError(f"{path / GENERATED}: needs [{HEADER}] schema = {SCHEMA}")
    if (path / DISCOVERY).is_file():
        try:
            stored = metadata.read(path / DISCOVERY)
        except metadata.MetadataError as exc:
            raise ProfileError(str(exc)) from exc
        repeated = sorted(set(header) & set(stored))
        if repeated:
            raise ProfileError(f"{path / DISCOVERY}: {', '.join(repeated)} also in [{HEADER}] of {GENERATED}")
        header.update(stored)
    if HEADER in overlay:
        raise ProfileError(f"{overlay_file}: [{HEADER}] is reserved for {GENERATED}")
    records = generated.pop(RECORD, []), overlay.pop(RECORD, [])
    for file, found in zip((path / GENERATED, overlay_file), records):
        if not isinstance(found, list):
            raise ProfileError(f"{file}: [[{RECORD}]] must be an array of tables")
    _sentences(records[0], texts, path / GENERATED)
    records[1].extend(table_records)
    return _with_family(Sources(path, overlay_file, generated, overlay, header, *records,
                                overlay_files=[file for file, _ in entries]))


def _sentences(records: list, texts: Any, where: Path) -> None:
    """Restore each fact sentence named by its position in the texts."""
    if not isinstance(texts, list) or not all(notes.has_prose(text) for text in texts):
        raise ProfileError(f"{where}: {TEXTS} must list sentences")
    for record in records:
        evidence = record.get("evidence") if isinstance(record, dict) else None
        number = evidence.get("sentence") if isinstance(evidence, dict) else None
        if type(number) is int:
            if not 0 <= number < len(texts):
                raise ProfileError(f"{where}: fact {record.get('id')}: unknown text {number}")
            evidence["sentence"] = texts[number]


def flat(path: str | Path) -> Sources:
    """A flat profile file seen as an overlay without a generated profile."""
    path = Path(path)
    data = _read(path)
    records = data.pop(RECORD, [])
    if not isinstance(records, list):
        raise ProfileError(f"{path}: [[{RECORD}]] must be an array of tables")
    return _with_family(Sources(path, path, {}, data, {}, [], records))


def _with_family(sources: Sources) -> Sources:
    from ..families.knowledge import load

    layout = sources.overlay.get("layout", {})
    if "window_strings" in layout:
        tables = {key: (sources.overlay_file, layout[key]) for key in ("window", "window_strings") if key in layout}
        try:
            _expand_windows(sources.overlay, tables, sources.generated)
        except tsv.TSVError as exc:
            raise ProfileError(str(exc)) from exc
        layout.pop("window_strings")
    _external_references(sources)
    generated = sources.generated.pop("family", None)
    overlay = sources.overlay.pop("family", None)
    if generated is not None and overlay is not None and generated != overlay:
        raise ProfileError(f"{sources.path}: family declarations differ")
    reference = overlay if overlay is not None else generated
    if reference is None:
        return sources
    if not isinstance(reference, dict) or set(reference) != {"name"}:
        raise ProfileError(f"{sources.path}: [family] needs only a name")
    family = load(reference["name"])
    engine = merge(sources.generated, sources.overlay).get("engine", {})
    if not isinstance(engine, dict):
        raise ProfileError(f"{sources.path}: engine must be a table")
    identity = engine.get("family")
    if identity is not None and identity != family.name:
        raise ProfileError(f"{sources.path}: family declaration differs from engine.family")
    sources.family_data = family.data
    sources.family_file = family.path
    sources.family_facts = family.facts
    return sources


def _external_references(sources: Sources) -> None:
    seen = set()
    for data, file in ((sources.generated, sources.path / GENERATED), (sources.overlay, sources.overlay_file)):
        records = data.pop("external_reference", [])
        if not isinstance(records, list):
            raise ProfileError(f"{file}: [[external_reference]] must be an array of tables")
        for index, record in enumerate(records):
            where = f"{file}: external_reference[{index}]"
            if not isinstance(record, dict) or set(record) != {"key", "name", "address"}:
                raise ProfileError(f"{where}: needs only key, name and address")
            if any(not isinstance(value, str) or not value.strip() for value in record.values()):
                raise ProfileError(f"{where}: key, name and address must be nonempty strings")
            key = record["key"]
            if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", key):
                raise ProfileError(f"{where}: key must use lowercase words and hyphens")
            if key in seen:
                raise ProfileError(f"{where}: duplicate external reference key {key}")
            address = record["address"]
            try:
                url = urlsplit(address)
                valid = url.scheme in ("http", "https") and bool(url.hostname) and not re.search(r"\s", address)
            except ValueError:
                valid = False
            if not valid:
                raise ProfileError(f"{where}: address must be an HTTP or HTTPS URL")
            seen.add(key)
            sources.external_references.append(record)


def _evidence(raw: Any, where: str) -> Evidence:
    if not isinstance(raw, dict) or set(raw) - EVIDENCE_KEYS:
        raise ProfileError(f"{where}: evidence takes {', '.join(sorted(EVIDENCE_KEYS))}")
    sentence, method = raw.get("sentence"), raw.get("method", "")
    anchors, sites, counts = raw.get("anchors", []), raw.get("code_sites", []), raw.get("counts", {})
    if not notes.has_prose(sentence) or not isinstance(method, str):
        raise ProfileError(f"{where}: evidence needs a sentence")
    for name, values in (("anchors", anchors), ("code_sites", sites)):
        if not isinstance(values, list) or not all(isinstance(v, int) and not isinstance(v, bool) for v in values):
            raise ProfileError(f"{where}: evidence {name} must be integers")
    if not isinstance(counts, dict) or not all(isinstance(v, int) and not isinstance(v, bool)
                                               for v in counts.values()):
        raise ProfileError(f"{where}: evidence counts must be integers")
    return Evidence(sentence, method, tuple(anchors), tuple(sites), tuple(counts.items()))


def _records(raw: list, where: Path) -> dict[str, dict]:
    out = {}
    note_ids = set()
    for record in raw:
        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
            raise ProfileError(f"{where}: every [[{RECORD}]] needs an id")
        label = f"{where}: fact {record['id']}"
        if set(record) - RECORD_KEYS:
            raise ProfileError(f"{label}: unknown keys {', '.join(sorted(set(record) - RECORD_KEYS))}")
        for key, allowed in (("kind", KINDS), ("scope", SCOPES), ("tier", TIERS), ("status", STATUSES)):
            if key in record and record[key] not in allowed:
                raise ProfileError(f"{label}: {key} is one of {', '.join(allowed)}")
        if "source" in record and not (isinstance(record["source"], str) and record["source"].strip()):
            raise ProfileError(f"{label}: source must name the discoverer, family file or overlay")
        if record["id"] in out:
            raise ProfileError(f"{label}: recorded twice")
        if "notes" in record or "notes_schema" in record:
            try:
                notes.validate({"schema": record.get("notes_schema"), "note": record.get("notes")})
            except notes.NoteError as exc:
                raise ProfileError(f"{label}: {exc}") from exc
            for note in record["notes"]:
                if note["id"] in note_ids:
                    raise ProfileError(f"{label}: duplicate note {note['id']}")
                note_ids.add(note["id"])
        out[record["id"]] = record
    return out


def _note_field(value: Any, name: str, where: str) -> tuple[dict | None, str]:
    if name.startswith("/"):
        parts = name[1:].split("/")
        if any(re.search(r"~(?![01])", part) for part in parts):
            raise ProfileError(f"{where}: invalid note field {name}")
        parts = [part.replace("~1", "/").replace("~0", "~") for part in parts]
    else:
        parts = [name]
    if not is_prose(parts[-1]):
        raise ProfileError(f"{where}: note field must name prose: {name}")
    if not isinstance(value, dict) and name == "evidence":
        return None, name
    target = value
    for part in parts[:-1]:
        if isinstance(target, dict) and part in target:
            target = target[part]
        elif isinstance(target, list) and re.fullmatch(r"0|[1-9][0-9]*", part) and int(part) < len(target):
            target = target[int(part)]
        else:
            raise ProfileError(f"{where}: unknown note field {name}")
    if not isinstance(target, dict):
        raise ProfileError(f"{where}: unknown note field {name}")
    return target, parts[-1]


def _attach_notes(fact: Fact, record: dict, where: str) -> str | None:
    if not record["notes"]:
        return None
    try:
        digest = value_digest(fact.value)
    except (TypeError, ValueError) as exc:
        raise ProfileError(f"{where}: invalid note binding value: {exc}") from exc
    fields: dict[tuple[int, str], tuple[dict | None, str, bool, list[str]]] = {}
    for note in record.get("notes", []):
        subject = {"kind": "profile_fact", "id": fact.id}
        if note["subject"] != subject or note["scope"] != {"kind": "shared"}:
            raise ProfileError(f"{where}: notes require this profile fact and shared scope")
        binding = note["binding"]
        name = binding.get("field")
        if not name:
            raise ProfileError(f"{where}: note binding requires the original prose field")
        if binding["value_sha256"] != digest:
            raise ProfileError(f"{where}: stale note binding")
        target, key = _note_field(fact.value, name, where)
        if target is not None and key in target and target[key] != "":
            raise ProfileError(f"{where}: legacy prose and structured notes share field {name}")
        root_evidence = key == "evidence" and (target is None or target is fact.value)
        legacy = record.get("evidence")
        if root_evidence and isinstance(legacy, dict) and "sentence" in legacy:
            raise ProfileError(f"{where}: legacy evidence sentence and structured notes share field {name}")
        identity = (id(target), key)
        _, _, _, prose = fields.setdefault(identity, (target, key, root_evidence, []))
        if note["status"] in ("unreviewed", "accepted"):
            prose.extend(item for field in notes.PROSE_FIELDS for item in note.get(field, []))
    sentence = None
    for target, key, root_evidence, prose in fields.values():
        if not prose:
            raise ProfileError(f"{where}: {key}: evidence needs a sentence")
        rendered = notes.BoundProse("\n".join(prose))
        if target is not None:
            target[key] = rendered
        if root_evidence:
            sentence = rendered
    if fact.node is not None and isinstance(fact.value, dict):
        fact.node.update(fact.value)
    fact.note_records = tuple(deepcopy(record.get("notes", [])))
    return sentence


def _attach(found: list[Fact], records: dict[str, dict], where: Path, generated: bool) -> list[Fact]:
    unknown = sorted(set(records) - {f.id for f in found})
    if unknown:
        raise ProfileError(f"{where}: records name no fact: {', '.join(unknown[:5])}")
    for fact in found:
        record = records.get(fact.id)
        label = f"{where}: fact {fact.id}"
        if record is None:
            if generated:
                raise ProfileError(f"{label}: a generated fact needs a [[{RECORD}]] record")
            prose = fact.value.get("evidence") if isinstance(fact.value, dict) else None
            fact.evidence = Evidence(prose) if isinstance(prose, str) else None
            continue
        if record.get("status", "verified") != "verified":
            raise ProfileError(f"{label}: only verified facts enter a profile; others go to {REPORT}")
        source = record.get("source")
        if generated and "notes" in record:
            raise ProfileError(f"{label}: structured notes belong in the overlay")
        if generated and (not source or source == OVERLAY_SOURCE or "tier" not in record or "evidence" not in record):
            raise ProfileError(f"{label}: a generated fact needs its tier, source and evidence")
        if not generated and source not in (None, OVERLAY_SOURCE):
            raise ProfileError(f"{label}: an overlay fact has source {OVERLAY_SOURCE}")
        fact.kind = record.get("kind", fact.kind)
        fact.scope = record.get("scope", fact.scope)
        fact.tier = record.get("tier")
        fact.source = source or OVERLAY_SOURCE
        sentence = _attach_notes(fact, record, label) if "notes" in record else None
        if "evidence" in record:
            evidence = record["evidence"]
            if sentence is not None and isinstance(evidence, dict):
                evidence = dict(evidence, sentence=sentence)
            fact.evidence = _evidence(evidence, label)
        elif sentence is not None:
            fact.evidence = Evidence(sentence)
        else:
            prose = fact.value.get("evidence") if isinstance(fact.value, dict) else None
            fact.evidence = Evidence(prose) if isinstance(prose, str) else None
    return found


def generated_facts(sources: Sources) -> list[Fact]:
    records = _records(sources.generated_records, sources.path / GENERATED)
    found = facts(sources.generated, frozenset(records))
    return _attach(found, records, sources.path / GENERATED, generated=True)


def _overlay(sources: Sources) -> tuple[dict, list[Fact]]:
    records = _records(sources.overlay_records, sources.overlay_file)
    data = deepcopy(sources.overlay)
    found = facts(data, frozenset(records))
    generated_records = _records(sources.generated_records, sources.path / GENERATED)
    generated = {fact.id: fact for fact in facts(sources.generated, frozenset(generated_records))}
    for fact in found:
        record = records.get(fact.id, {})
        if (record.get("notes") and isinstance(fact.value, dict)
                and {key for key in fact.value if not is_prose(key)} == {"name"}
                and fact.id in generated and isinstance(generated[fact.id].value, dict)):
            fact.value = merge(generated[fact.id].value, fact.value)
    found = _attach(found, records, sources.overlay_file, generated=False)
    return data, found


def overlay_facts(sources: Sources) -> list[Fact]:
    return _overlay(sources)[1]


def language_notes(profile_path: str | Path, path: str | Path, language: str) -> dict[str, list[dict]]:
    """A language's notes on overlay facts, by fact id, validated against the profile."""
    try:
        document = notes.read_notes(Path(path))
    except notes.NoteError as exc:
        raise ProfileError(str(exc)) from exc
    sources = read(profile_path) if Path(profile_path).is_dir() else flat(profile_path)
    found = {fact.id: fact for fact in overlay_facts(sources)}
    shared = {note["id"] for fact in found.values() for note in fact.note_records}
    by_fact: dict[str, list[dict]] = {}
    for note in document["note"]:
        label = f"{path}: note {note['id']}"
        subject = note["subject"]
        if subject["kind"] != "profile_fact" or note["scope"] != {"kind": "language", "language": language}:
            raise ProfileError(f"{label}: expected a profile fact note of language {language}")
        fact = found.get(subject["id"])
        if fact is None:
            raise ProfileError(f"{label}: unknown profile fact {subject['id']}")
        if note["id"] in shared:
            raise ProfileError(f"{label}: duplicate note {note['id']}")
        name = note["binding"].get("field")
        if not name:
            raise ProfileError(f"{label}: note binding requires the original prose field")
        if note["binding"]["value_sha256"] != value_digest(fact.value):
            raise ProfileError(f"{label}: stale note binding")
        _note_field(fact.value, name, label)
        by_fact.setdefault(fact.id, []).append(note)
    return by_fact


def add_language_prose(data: dict, by_fact: dict[str, list[dict]], where: str) -> None:
    """Append visible language prose after the shared prose of each field."""
    located = {fact.id: fact for fact in facts(data, frozenset(by_fact)) if fact.id in by_fact}
    for fact_id, rows in by_fact.items():
        fact = located.get(fact_id)
        if fact is None or not isinstance(fact.value, dict) or fact.node is None:
            raise ProfileError(f"{where}: fact {fact_id} has no prose field in the project profile")
        for note in rows:
            if note["status"] not in ("unreviewed", "accepted"):
                continue
            target, key = _note_field(fact.value, note["binding"]["field"], where)
            if target is fact.value:
                target = fact.node   # the value is a copy of its own fields
            prose = [item for name in notes.PROSE_FIELDS for item in note.get(name, [])]
            current = target.get(key)
            target[key] = notes.BoundProse("\n".join(([current] if isinstance(current, str) and current else []) + prose))


def family_facts(data: dict, raw: list, path: Path) -> list[Fact]:
    """Verified family records without injecting instance evidence fields."""
    records = _records(raw, path)
    found = _attach(facts(data, frozenset(records)), records, path, generated=True)
    if any(fact.scope != "family" for fact in found):
        raise ProfileError(f"{path}: every family fact needs scope = family")
    if any(fact.node is not None and "evidence" in fact.node for fact in found):
        raise ProfileError(f"{path}: family evidence belongs in fact records")
    return found


def validate_prompt_arrows(layout: dict) -> None:
    """Validate waiting behavior and its printer evidence."""
    def width(value, where, field="prompt_arrow_width"):
        if type(value) is not int or value < 0:
            raise ProfileError(f"{where}: {field} must be a nonnegative integer")

    if "prompt_arrow_width" in layout:
        width(layout["prompt_arrow_width"], "layout")
    for section in ("window", "table_window", "record_window"):
        for row in layout.get(section, []):
            where = f"layout.{section} {row.get('name', '')}"
            for field in ("prompt_arrow_width", "newline_scroll_after", "instant_print"):
                if field in row:
                    if field == "instant_print":
                        if type(row[field]) is not bool:
                            raise ProfileError(f"{where}: {field} must be a boolean")
                    else:
                        width(row[field], where, field)
                    if not notes.has_prose(row.get("evidence")):
                        raise ProfileError(f"{where}: {field} requires printer evidence")
    for key, value in layout.get("window_prompt_arrow_width", {}).items():
        width(value, f"layout.window_prompt_arrow_width {key}")
    for key, value in layout.get("window_newline_scroll_after", {}).items():
        width(value, f"layout.window_newline_scroll_after {key}", "newline_scroll_after")
    for key, value in layout.get("window_instant_print", {}).items():
        if type(value) is not bool:
            raise ProfileError(f"layout.window_instant_print {key}: instant_print must be a boolean")


def load(path: str | Path) -> dict:
    """The merged profile data of either form, as consumers read it."""
    sources = read(path) if Path(path).is_dir() else flat(path)
    overlay_data, found = _overlay(sources)
    overlay = {fact.id for fact in found}
    for fact in generated_facts(sources):
        if fact.node is not None:
            if "evidence" in fact.node:
                raise ProfileError(f"{sources.path / GENERATED}: fact {fact.id}: evidence belongs in its record")
            if fact.id not in overlay:
                fact.node["evidence"] = fact.evidence.sentence   # consumers read prose evidence
    data = merge(merge(sources.family_data, sources.generated), overlay_data)
    validate_prompt_arrows(data.get("layout", {}))
    from ..check.fillsources import validate as validate_fill_sources
    validate_fill_sources(data)
    return data
