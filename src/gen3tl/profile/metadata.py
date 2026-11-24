"""The discovery metadata file beside the generated profile.

It holds the run's metadata, never a profile fact. A window binding is stored under its
window and takes its consumers from the audit of its string. A prose text that repeats is
stored once in `texts` and named by its position there. A list of records reached through
tables is stored as a collection: its keys once and a row of values per record, null for a
key the record lacks. Records that disagree on key order or hold null stay as records.
Records, rows and texts stand one per line.
"""

from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path
from typing import Any

from ..core.rom import RomError

VERSION = "2.0.0"
KEYED = "1.0.0"   # earlier form: every record keeps its keys
TEXTS = "texts"
KEYS, ROWS = "keys", "rows"   # a collection: its keys once, a row per record
TEXT_FIELDS = frozenset({"assumption", "reason"})   # prose repeated across records
TEXT_LISTS = frozenset({"reasons"})
WIDTH = 100   # shorter containers stay on one line


class MetadataError(RomError):
    pass


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _is_collection(value: Any) -> bool:
    return isinstance(value, dict) and set(value) == {KEYS, ROWS}


def _formatted(value: Any, depth: int = 0) -> str:
    """Break long tables by key, and lists of records, rows or texts by item."""
    text = _compact(value)
    if len(text) <= WIDTH or not isinstance(value, (dict, list)):
        return text
    inner = " " * (depth + 1)
    if isinstance(value, dict):
        # the keys of a collection head its rows on one line
        rows = [inner + _compact(key) + ": "
                + (_compact(item) if key == KEYS and _is_collection(value) else _formatted(item, depth + 1))
                for key, item in value.items()]
        return "{\n" + ",\n".join(rows) + "\n" + " " * depth + "}"
    if not any(isinstance(item, (dict, list, str)) for item in value):
        return text
    return "[\n" + ",\n".join(inner + _compact(item) for item in value) + "\n" + " " * depth + "]"


def _rewrite(value: Any, change, key: str | None = None) -> Any:
    """The document with each prose text replaced by `change`."""
    if isinstance(value, dict):
        return {name: _rewrite(item, change, name) for name, item in value.items()}
    if isinstance(value, list):
        return [_rewrite(item, change, key if key in TEXT_LISTS else None) for item in value]
    return change(value) if key in TEXT_FIELDS or key in TEXT_LISTS else value


def _grouped(windows: dict) -> dict:
    bindings = windows.get("bindings")
    if not bindings:
        return windows
    audit = {row["id"]: row["consumers"] for row in windows["audit"]["strings"]}
    groups = {}
    for binding in bindings:
        string, found = binding["string"], audit.get(binding["string"])
        if found is None or any(consumer not in found for consumer in binding["consumers"]):
            raise ValueError(f"window binding {string}: consumers outside the audit")
        group = groups.setdefault(binding["window"], {"window": binding["window"], "strings": []})
        group["strings"].append(string)
        if binding["consumers"] != found:
            group.setdefault("consumers", {})[string] = [found.index(item) for item in binding["consumers"]]
    return {**windows, "bindings": list(groups.values())}


def _collection(records: list[dict]) -> dict | list[dict]:
    """The keys once and a row per record, when one key order serves every record."""
    keys = list(dict.fromkeys(key for record in records for key in record))
    for record in records:
        # null marks an absent key, so null values keep records
        if [key for key in keys if key in record] != list(record) or None in record.values():
            return records
    return {KEYS: keys, ROWS: [[record.get(key) for key in keys] for record in records]}


def _collections(value: Any) -> Any:
    """Each list of records reached through tables, stored as a collection."""
    if isinstance(value, dict):
        if _is_collection(value):
            raise ValueError(f"discovery metadata: a table of only {KEYS} and {ROWS} is reserved")
        return {key: _collections(item) for key, item in value.items()}
    if isinstance(value, list) and len(value) > 1 and all(isinstance(item, dict) for item in value):
        return _collection(value)
    return value


def encode(metadata: dict) -> str:
    """The stored form of the metadata of one discovery run."""
    if {"schema", TEXTS} & set(metadata):
        raise ValueError(f"discovery metadata: schema and {TEXTS} are reserved")
    document = dict(metadata)
    if isinstance(document.get("windows"), dict):
        document["windows"] = _grouped(document["windows"])
    counts = Counter()

    def count(value):
        if type(value) is int:   # numbers name stored texts
            raise ValueError("discovery metadata: a prose field holds a number")
        if isinstance(value, str) and " " in value:   # codes stay as written
            counts[value] += 1
        return value

    _rewrite(document, count)
    texts = [text for text, number in counts.items() if number > 1]
    codes = {text: code for code, text in enumerate(texts)}
    document = _rewrite(document, lambda value: codes.get(value, value) if isinstance(value, str) else value)
    return _formatted({"schema": VERSION, TEXTS: texts, **_collections(document)}) + "\n"


def _bindings(windows: dict, where: Path) -> dict:
    groups = windows.get("bindings")
    if not groups:
        return windows
    try:
        audit = {row["id"]: row["consumers"] for row in windows["audit"]["strings"]}
    except (KeyError, TypeError) as exc:
        raise MetadataError(f"{where}: window bindings need the window audit") from exc
    found = {}
    for group in groups:
        if (not isinstance(group, dict) or set(group) - {"window", "strings", "consumers"}
                or not isinstance(group.get("window"), str) or not isinstance(group.get("strings"), list)
                or not isinstance(group.get("consumers", {}), dict)):
            raise MetadataError(f"{where}: invalid window binding group")
        subsets = group.get("consumers", {})
        for string in group["strings"]:
            if not isinstance(string, str) or string not in audit or string in found:
                raise MetadataError(f"{where}: window binding {string!r} is unknown or repeated")
            consumers = audit[string]
            chosen = subsets.get(string)
            if chosen is not None and not (isinstance(chosen, list)
                                           and all(type(n) is int and 0 <= n < len(consumers) for n in chosen)):
                raise MetadataError(f"{where}: window binding {string}: invalid consumer positions")
            chosen = consumers if chosen is None else [consumers[n] for n in chosen]
            found[string] = {"string": string, "window": group["window"], "consumers": copy.deepcopy(chosen)}
    position = {string: number for number, string in enumerate(audit)}   # bindings follow the audit
    return {**windows, "bindings": [found[string] for string in sorted(found, key=position.get)]}


def _records(value: Any, where: Path) -> Any:
    """Every collection reached through tables back to its records."""
    if not isinstance(value, dict):
        return value
    if not _is_collection(value):
        return {key: _records(item, where) for key, item in value.items()}
    keys, rows = value[KEYS], value[ROWS]
    if (not isinstance(keys, list) or not all(isinstance(key, str) for key in keys) or len(set(keys)) != len(keys)
            or not isinstance(rows, list) or not all(isinstance(row, list) and len(row) == len(keys) for row in rows)):
        raise MetadataError(f"{where}: a collection needs distinct {KEYS} and a value per key in each row")
    return [{key: item for key, item in zip(keys, row) if item is not None} for row in rows]


def read(path: str | Path) -> dict:
    """The metadata as discovery produced it."""
    path = Path(path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MetadataError(f"{path}: cannot read discovery metadata: {exc}") from exc
    if not isinstance(document, dict) or document.get("schema") not in (VERSION, KEYED):
        raise MetadataError(f"{path}: needs schema {VERSION}")
    texts = document.get(TEXTS)
    if not isinstance(texts, list) or not all(isinstance(text, str) for text in texts):
        raise MetadataError(f"{path}: {TEXTS} must list texts")

    def expand(value):
        if type(value) is not int:
            return value
        if not 0 <= value < len(texts):
            raise MetadataError(f"{path}: unknown text {value}")
        return texts[value]

    metadata = {key: value for key, value in document.items() if key not in ("schema", TEXTS)}
    if document["schema"] == VERSION:
        metadata = _records(metadata, path)
    metadata = _rewrite(metadata, expand)
    if isinstance(metadata.get("windows"), dict):
        metadata["windows"] = _bindings(metadata["windows"], path)
    return metadata
