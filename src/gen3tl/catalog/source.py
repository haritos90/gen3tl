"""The English source lines of the base ROM, read through the inventory's facts, and their digests."""

from __future__ import annotations

import hashlib

from ..core.charmap import Charmap, CharmapError


def digest(text: str) -> str:
    """The SHA-256 of an English line, UTF-8 encoded."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def row(entry: dict) -> list:
    """What reads an inventory entry's line: offset, length, text mode and form."""
    form = entry["kind"] if entry["kind"] == "pointer" or "unterminated" not in entry["flags"] else "unterminated"
    return [entry["offset"], entry["length"], entry["mode"], form]


def line(data: bytes, facts: list, cm: Charmap) -> str:
    """The line `facts` (row) names in the ROM image `data`."""
    offset, length, mode, form = facts
    if form == "pointer":
        return cm.decode(data, offset, mode=mode)[0]
    if form == "fixed":
        return cm.decode(data, offset, limit=length)[0]
    return cm.decode(bytes(data[offset:offset + length]) + b"\xff", 0)[0]


def read(data: bytes, rows: dict[str, list]) -> tuple[dict[str, str], list[str]]:
    """The lines of `rows` whose digest matches, and the keys whose line differs or does not decode."""
    cm = Charmap()
    texts, missing = {}, []
    for key, (*facts, expected) in rows.items():
        try:
            text = line(data, facts, cm)
        except (CharmapError, IndexError, KeyError, ValueError):
            missing.append(key)
            continue
        if digest(text) == expected:
            texts[key] = text
        else:
            missing.append(key)
    return texts, missing
