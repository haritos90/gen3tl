"""Verified omission of a prefix through its dedicated text pointer."""

from __future__ import annotations

import copy

from ..core.notes import BoundProse, has_prose
from ..core.rom import RomEvidence


def original_limits(profile):
    """Keep source verification on original reader geometry."""
    result = copy.deepcopy(profile)
    for fact in result.get("selected_text_prefixes", []):
        limits = fact["limits"]
        table = next(t for t in result["table"] if t["name"] == limits["table"])
        window = next(w for w in table["windows"] if w.get("where") == limits["where"])
        window.update(width=limits["original_width"], chars=limits["original_chars"])
        for index in window["indices"]:
            result["layout"]["window_width"][f"{limits['table']}:{index}"] = limits["original_width"]
    result.data["selected_text_prefixes"] = []
    return result


def configure(data: dict, selected: list[str]) -> None:
    """Select declared pointer changes and their measured consumer limits."""
    if not isinstance(selected, list) or any(not isinstance(x, str) for x in selected):
        raise ValueError("text_prefixes: expected capability names")
    if len(set(selected)) != len(selected):
        raise ValueError("text_prefixes: duplicate capability")
    facts = data.get("text_prefixes", [])
    by_name = {row["name"]: row for row in facts}
    if len(by_name) != len(facts) or set(selected) - by_name.keys():
        raise ValueError("text_prefixes: unknown or duplicate capability")
    plans = []
    for name in selected:
        fact = copy.deepcopy(by_name[name])
        if not has_prose(fact.get("evidence")):
            raise ValueError(f"text_prefixes.{name}: missing evidence")
        source = RomEvidence.parse(fact.get("source"), f"text_prefixes.{name}.source")
        if source.size < 2:
            raise ValueError(f"text_prefixes.{name}: invalid terminated prefix")
        if type(fact["pointer"]) is not int or fact["pointer"] < 0:
            raise ValueError(f"text_prefixes.{name}: invalid pointer")
        limits = fact["limits"]
        tables = [t for t in data.get("table", []) if t["name"] == limits["table"]]
        windows = [w for t in tables for w in t.get("windows", [])
                   if w.get("where") == limits["where"]]
        if len(windows) != 1:
            raise ValueError(f"text_prefixes.{name}: consumer must be unique")
        window = windows[0]
        for key in ("width", "chars"):
            if window.get(key) != limits["original_" + key]:
                raise ValueError(f"text_prefixes.{name}: stale {key} limit")
            if type(limits[key]) is not int or limits[key] < 1:
                raise ValueError(f"text_prefixes.{name}: invalid {key} limit")
        widths = data.get("layout", {}).get("window_width", {})
        for index in window["indices"]:
            sid = f"{limits['table']}:{index}"
            if widths.get(sid) != limits["original_width"]:
                raise ValueError(f"text_prefixes.{name}: stale entry width {sid}")
            widths[sid] = limits["width"]
        window.update(width=limits["width"], chars=limits["chars"])
        parts = [window["evidence"], fact["evidence"]]
        evidence = " ".join(part for part in parts if part.strip())
        window["evidence"] = BoundProse(evidence) if any(isinstance(part, BoundProse) for part in parts) else evidence
        plans.append(fact)
    data["selected_text_prefixes"] = plans


def apply(base, rom, plans: list[dict], occupied: set[int]) -> list[tuple[int, int]]:
    """Validate every plan before writing any pointer."""
    changes, claimed = [], set()
    for fact in plans:
        pointer = fact["pointer"]
        source = RomEvidence.parse(fact.get("source"), "text prefix source")
        span = set(range(pointer, pointer + 4))
        if pointer % 4 or pointer + 4 > len(base) or source.offset + source.size > len(base):
            raise ValueError("text prefix: pointer or source outside ROM")
        if span & (occupied | claimed):
            raise ValueError("text prefix: overlapping pointer mutation")
        if base.u32(pointer) != 0x08000000 + source.offset or rom.u32(pointer) != base.u32(pointer):
            raise ValueError("text prefix: source pointer changed")
        if not source.matches(base.data) or not source.matches(rom.data):
            raise ValueError("text prefix: original bytes changed")
        raw = source.read(base.data)
        if len(raw) < 2 or raw[-1] != 255 or 255 in raw[:-1]:
            raise ValueError("text prefix: invalid terminated prefix")
        changes.append((pointer, source.offset + source.size - 1))
        claimed.update(span)
    for pointer, target in changes:
        rom.write(pointer, (0x08000000 + target).to_bytes(4, "little"))
    return changes
