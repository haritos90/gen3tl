"""Portable measured windows and their consumer function contracts."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ..profile import schema, tsv
from .fingerprints import Fingerprint

COLUMNS = {"function": "str", "slot": "str", **schema.OVERLAY_TABLES["layout/windows.tsv"][1]}
SLOT = re.compile(r"call:(0|[1-9][0-9]*):(r[0-3]|s[0-7])(?::read:(0|[1-9][0-9]*)|:table(?::case:(0|[1-9][0-9]*):(eq|ne):(0|[1-9][0-9]*))?)?\Z")


@dataclass
class Windows:
    rows: list[dict]
    functions: list[dict]
    printers: list[dict]
    paths: tuple[Path, ...] = ()
    table_rows: list[dict] = field(default_factory=list)
    templates: list[dict] = field(default_factory=list)


def argument_slot(argument):
    return f"r{argument}" if argument < 4 else f"s{argument - 4}"


def slot_argument(value):
    return int(value[1:]) + (4 if value.startswith("s") else 0)


def load(directory: Path) -> Windows:
    directory = Path(directory)
    table = directory / "windows.tsv"
    functions = directory / "window-functions.json"
    if not table.exists():
        return Windows([], [], [])
    table_windows = directory / "table-windows.toml"
    table_rows = []
    paths = (table, functions)
    try:
        document = json.loads(functions.read_text(encoding="utf-8"))
        if document["schema"] != 1 or document["family"] != directory.name:
            raise ValueError("invalid window family identity")
        consumers, printers = document["consumers"], document["printers"]
        templates = document.get("table_printers", [])
        for row in consumers + printers + templates + [row['reader'] for row in templates]:
            fingerprint = Fingerprint.read(row)
            branches = row["branches"]
            if not isinstance(branches, list) or any(
                    not isinstance(branch, list) or len(branch) != 2
                    or any(type(value) is not int or value % 2 or not 0 <= value < fingerprint.length
                           for value in branch) for branch in branches):
                raise ValueError("invalid consumer local branches")
        if any(type(row["text_argument"]) is not int or not 0 <= row["text_argument"] < 12 for row in printers):
            raise ValueError("invalid printer text argument")
        for row in printers:
            for name in ("text", "window", "font", "x", "y"):
                arg, field = row.get(name + "_argument"), row.get(name + "_field")
                if arg is not None and (type(arg) is not int or not 0 <= arg < 12):
                    raise ValueError("invalid printer argument")
                if field is not None and (arg is None or type(field) is not int or not 0 <= field < 256):
                    raise ValueError("invalid printer template field")
        for row in templates:
            parent, reader = Fingerprint.read(row), Fingerprint.read(row['reader'])
            for key, length in (('reader_call', parent.length), ('render_call', reader.length), ('text_read', reader.length)):
                if type(row[key]) is not int or row[key] % 2 or not 0 <= row[key] < length - 2:
                    raise ValueError("invalid template reader call")
            if any(type(row[key]) is not int or not 0 <= row[key] < 12
                   for key in ('template_argument', 'render_argument')):
                raise ValueError("invalid template reader argument")
            if (any(type(row[key]) is not int for key in ('count_width', 'stride', 'text_field'))
                    or row['count_width'] not in (1, 2, 4) or row['stride'] < 4 or row['stride'] % 4
                    or not 0 <= row['text_field'] <= row['stride'] - 4
                    or any(type(row[key]) is not int or not 0 <= row[key] < 256
                           for key in ('items_field', 'count_field', 'window_field', 'x_field'))
                    or row['items_field'] % 4 or row['text_field'] % 4
                    or row['count_field'] % row['count_width'] or not row.get('evidence')):
                raise ValueError("invalid template record shape")
            if 'header_x_field' in row and (
                    type(row['header_x_field']) is not int or not 0 <= row['header_x_field'] < 256
                    or type(row['id_field']) is not int or not 0 <= row['id_field'] <= row['stride'] - 4
                    or type(row['header_id']) is not int or not 0 <= row['header_id'] <= 0xFFFFFFFF):
                raise ValueError("invalid template header position")
        by_digest = {row["sha256"]: row for row in consumers}
        if len(by_digest) != len(consumers):
            raise ValueError("duplicate window consumer fingerprint")
        rows = tsv.read(table, COLUMNS)
        keys, names = set(), {}
        for row in rows:
            key = row["function"], row["slot"]
            slot = SLOT.fullmatch(row["slot"])
            if key in keys or row["function"] not in by_digest or slot is None:
                raise ValueError("duplicate or unknown window consumer slot")
            if slot[4] is not None and int(slot[6]) > 0xFFFFFFFF:
                raise ValueError("invalid window table presentation case")
            length = by_digest[row["function"]]["length"]
            offsets = [int(slot[1]), *([int(slot[3])] if slot[3] is not None else [])]
            if any(value % 2 or value >= length for value in offsets):
                raise ValueError("window slot leaves consumer function")
            value = {key: value for key, value in row.items() if key not in ("function", "slot")}
            name = value["name"]
            if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", name):
                raise ValueError("invalid family window name")
            if name in names and names[name] != value:
                raise ValueError("family window name has differing values")
            if not value.get("evidence") or not any(key in value for key in schema.WINDOW_FIELDS):
                raise ValueError("family window requires measures and evidence")
            names[name] = value
            keys.add(key)
        if table_windows.is_file():
            document = tomllib.loads(table_windows.read_text(encoding="utf-8"))
            if document["schema"] != 1 or document["family"] != directory.name:
                raise ValueError("invalid table window family identity")
            table_rows = document["window"]
            table_names = set()
            allowed = {"function", "slot", "extent", "name", "where", *schema.WINDOW_FIELDS}
            for row in table_rows:
                slot = SLOT.fullmatch(row["slot"])
                if (set(row) - allowed or row["function"] not in by_digest or slot is None
                        or row["extent"] not in ("text-pointer-run", "reader-count")
                        or (slot[3] is None if row["extent"] == "text-pointer-run" else not row["slot"].endswith(":table"))
                        or row["name"] in table_names or type(row["width"]) is not int or row["width"] < 1):
                    raise ValueError("invalid table window consumer or extent")
                length = by_digest[row["function"]]["length"]
                if any(int(value) % 2 or int(value) >= length for value in (slot[1], slot[3]) if value is not None):
                    raise ValueError("table window slot leaves consumer function")
                table_names.add(row["name"])
            paths = (*paths, table_windows)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise schema.ProfileError(f"{directory}: invalid family windows: {exc}") from exc
    return Windows(rows, consumers, printers, paths, table_rows, templates)
