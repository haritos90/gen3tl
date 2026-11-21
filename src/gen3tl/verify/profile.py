"""Profile records for byte equality and canonical fact comparison."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..profile import schema

FILES = ("document.json", "fact-ids.json")


def _json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def record(profile: str | Path, destination: str | Path) -> dict[str, bytes]:
    """Write merged data and ordered fact ids."""
    document = schema.load(profile)
    records = dict(zip(FILES, (_json(document), _json([fact.id for fact in schema.facts(document)]))))
    destination = Path(destination)
    destination.mkdir(parents=True)
    for name, content in records.items():
        (destination / name).write_bytes(content)
    return records


def _child(path: str, key: str) -> str:
    return f"{path}.{schema.key_text(key)}"


def _difference(before: Any, after: Any, path: str = "$") -> tuple[str, str] | None:
    if type(before) is not type(after):
        return path, "type"
    if isinstance(before, dict):
        old_keys, new_keys = list(before), list(after)
        for old, new in zip(old_keys, new_keys):
            if old != new:
                if old not in after:
                    return _child(path, old), "missing key"
                if new not in before:
                    return _child(path, new), "added key"
                return _child(path, old), "key order"
            found = _difference(before[old], after[new], _child(path, old))
            if found:
                return found
        if len(old_keys) > len(new_keys):
            return _child(path, old_keys[len(new_keys)]), "missing key"
        if len(new_keys) > len(old_keys):
            return _child(path, new_keys[len(old_keys)]), "added key"
    elif isinstance(before, list):
        for index, (old, new) in enumerate(zip(before, after)):
            found = _difference(old, new, f"{path}[{index}]")
            if found:
                return found
        if len(before) != len(after):
            return f"{path}[{min(len(before), len(after))}]", "missing item" if len(before) > len(after) else "added item"
    elif _json(before) != _json(after):
        return path, "value"
    return None


@dataclass(frozen=True)
class Comparison:
    file: str
    before_bytes: int
    after_bytes: int
    equal: bool
    path: str = ""
    reason: str = ""


def _read(directory: Path, name: str) -> tuple[bytes, Any]:
    path = directory / name
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8"))
    valid = isinstance(value, dict) if name == FILES[0] else (
        isinstance(value, list) and all(isinstance(item, str) for item in value))
    if not valid:
        raise ValueError(f"{path}: invalid profile proof record")
    return raw, value


def compare(before: str | Path, after: str | Path) -> list[Comparison]:
    """Compare stored bytes; decoded values only locate differences."""
    results = []
    for name in FILES:
        old, old_value = _read(Path(before), name)
        new, new_value = _read(Path(after), name)
        path, reason = ("", "") if old == new else (_difference(old_value, new_value) or ("$", "encoding"))
        results.append(Comparison(name, len(old), len(new), old == new, path, reason))
    return results


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class SetDifference:
    path: str
    reason: str
    before: str = "-"
    after: str = "-"
    allowed: bool = False


def _fact_values(directory: Path) -> tuple[dict, dict[str, str]]:
    _, document = _read(directory, FILES[0])
    _, ids = _read(directory, FILES[1])
    pairs = sorted((fact.id, _canonical(fact.value)) for fact in schema.facts(document))
    values = dict(pairs)
    if len(values) != len(pairs):
        raise ValueError(f"{directory / FILES[0]}: duplicate fact id")
    if sorted(ids) != list(values):
        raise ValueError(f"{directory / FILES[1]}: fact ids differ from document")
    return document, values


def _key_orders(before: Any, after: Any, path: str = "$") -> list[SetDifference]:
    results = []
    if isinstance(before, dict) and isinstance(after, dict):
        if list(before) != list(after) and _canonical(before) == _canonical(after):
            results.append(SetDifference(path, "key order", allowed=True))
        for key in sorted(before.keys() & after.keys()):
            results.extend(_key_orders(before[key], after[key], _child(path, key)))
    elif isinstance(before, list) and isinstance(after, list):
        for index, (old, new) in enumerate(zip(before, after)):
            results.extend(_key_orders(old, new, f"{path}[{index}]"))
    return results


def compare_sets(before: str | Path, after: str | Path, *,
                 allow_added_prefix: str | None = None) -> list[SetDifference]:
    """Compare canonical facts and report permitted order changes."""
    if allow_added_prefix == "":
        raise ValueError("empty allowed fact prefix")
    old_document, old = _fact_values(Path(before))
    new_document, new = _fact_values(Path(after))
    results = []
    for fact_id in sorted(old.keys() | new.keys()):
        if fact_id not in new:
            results.append(SetDifference(fact_id, "lost fact", before=old[fact_id]))
        elif fact_id not in old:
            allowed = allow_added_prefix is not None and fact_id.startswith(allow_added_prefix)
            reason = "added fact" if allowed else "addition outside prefix"
            results.append(SetDifference(fact_id, reason, after=new[fact_id], allowed=allowed))
        elif old[fact_id] != new[fact_id]:
            results.append(SetDifference(fact_id, "changed value", old[fact_id], new[fact_id]))
    results.extend(_key_orders(old_document, new_document))
    return results
