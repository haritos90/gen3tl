"""Typed overlay tables with lossless cells and canonical output."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..core.rom import RomError

TYPES = frozenset({"str", "bool", "int", "hex", "str[]", "int[]", "hex[]"})
INTEGER = re.compile(r"[+-]?(?:0|[1-9](?:_?[0-9])*)|0x[0-9A-Fa-f](?:_?[0-9A-Fa-f])*"
                     r"|0o[0-7](?:_?[0-7])*|0b[01](?:_?[01])*")
ESCAPES = {"t": "\t", "n": "\n", "\\": "\\"}


class TSVError(RomError):
    pass


def error(path: Path, row: int, column: int, reason: str) -> TSVError:
    return TSVError(f"{str(path)!r}: row {row}, column {column}: {reason}")


def _schema(path: Path, columns: Mapping[str, str]) -> None:
    if not columns:
        raise error(path, 1, 1, "empty column schema")
    for number, (name, kind) in enumerate(columns.items(), 1):
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise error(path, 1, number, "invalid column name")
        if not isinstance(kind, str) or kind not in TYPES:
            raise error(path, 1, number, f"unknown cell type {kind!r}")


def _unescape(cell: str) -> str:
    out = []
    index = 0
    while index < len(cell):
        char = cell[index]
        if char == "\\":
            index += 1
            if index == len(cell):
                raise ValueError("unfinished escape")
            char = cell[index]
            if char not in ESCAPES:
                raise ValueError(f"unknown escape {char!r}")
            char = ESCAPES[char]
        out.append(char)
        index += 1
    return "".join(out)


def _decode(cell: str, kind: str) -> Any:
    if kind.endswith("[]"):
        parts = cell.split(",")
        if any(part == "" for part in parts):
            raise ValueError("empty list item")
        return [_decode(part, kind[:-2]) for part in parts]
    value = _unescape(cell)
    if kind == "str":
        return value
    if kind == "bool":
        if value not in ("true", "false"):
            raise ValueError("expected true or false")
        return value == "true"
    if not INTEGER.fullmatch(value):
        raise ValueError("invalid TOML integer")
    try:
        return tomllib.loads(f"value = {value}")["value"]
    except ValueError as exc:
        raise ValueError("invalid TOML integer") from exc


def _position(prefix: bytes) -> tuple[int, int]:
    return prefix.count(b"\n") + 1, prefix.rsplit(b"\n", 1)[-1].count(b"\t") + 1


def read(path: Path, columns: Mapping[str, str]) -> list[dict[str, Any]]:
    """Read rows using the declared column order and cell types."""
    path = Path(path)
    _schema(path, columns)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise error(path, 1, 1, f"read failed: {exc.strerror}") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise error(path, *_position(raw[:exc.start]), "invalid UTF-8") from exc
    if "\r" in text:
        raise error(path, *_position(raw[:raw.index(b"\r")]), "carriage return; expected LF")
    if not text:
        raise error(path, 1, 1, "missing header")
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    expected = list(columns)
    header = lines[0].split("\t")
    for index in range(max(len(header), len(expected))):
        actual = header[index] if index < len(header) else None
        wanted = expected[index] if index < len(expected) else None
        if actual != wanted:
            raise error(path, 1, index + 1, f"header expected {wanted!r}, found {actual!r}")
    rows = []
    for number, line in enumerate(lines[1:], 2):
        cells = line.split("\t")
        if len(cells) != len(columns):
            raise error(path, number, min(len(cells), len(columns)) + 1,
                        f"cell count expected {len(columns)}, found {len(cells)}")
        row = {}
        for column, ((name, kind), cell) in enumerate(zip(columns.items(), cells), 1):
            if cell == "":
                continue
            try:
                row[name] = _decode(cell, kind)
            except ValueError as exc:
                raise error(path, number, column, str(exc)) from exc
        rows.append(row)
    return rows


def _encode(value: Any, kind: str) -> str:
    if kind == "bool":
        if type(value) is not bool:
            raise ValueError("expected boolean")
        return "true" if value else "false"
    if kind.endswith("[]"):
        if not isinstance(value, list) or not value:
            raise ValueError("expected nonempty list")
        parts = [_encode(item, kind[:-2]) for item in value]
        if any("," in part for part in parts):
            raise ValueError("comma in list item")
        return ",".join(parts)
    if kind == "str":
        if not isinstance(value, str) or value == "":
            raise ValueError("expected nonempty string")
        if "\r" in value:
            raise ValueError("carriage return in string")
        return value.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n")
    if type(value) is not int:
        raise ValueError("expected integer")
    return f"{value:#x}" if kind == "hex" and value >= 0 else str(value)


def write(path: Path, rows: list[dict[str, Any]], columns: Mapping[str, str]) -> None:
    """Write canonical UTF-8 and LF without changing row order."""
    path = Path(path)
    _schema(path, columns)
    lines = ["\t".join(columns)]
    for number, row in enumerate(rows, 2):
        if not isinstance(row, dict):
            raise error(path, number, 1, "expected table")
        unknown = [key for key in row if key not in columns]
        if unknown:
            raise error(path, number, len(columns) + 1, f"unknown key {unknown[0]!r}")
        cells = []
        for column, (name, kind) in enumerate(columns.items(), 1):
            try:
                cell = _encode(row[name], kind) if name in row else ""
                cell.encode("utf-8")
            except ValueError as exc:
                raise error(path, number, column, str(exc)) from exc
            cells.append(cell)
        lines.append("\t".join(cells))
    try:
        path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    except OSError as exc:
        raise error(path, 1, 1, f"write failed: {exc.strerror}") from exc
