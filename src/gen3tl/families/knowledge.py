"""Verified, address-independent defaults for explicitly selected engine families."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .. import TOOLKIT_ROOT
from ..profile import schema

FAMILY_DIR = TOOLKIT_ROOT / "families"
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")


@dataclass
class Knowledge:
    path: Path
    name: str
    version: str
    data: dict
    facts: list[schema.Fact]


@dataclass
class ScriptKnowledge:
    path: Path
    name: str
    version: str
    data: dict
    discovery: dict


def source_files(engine: Path) -> list[Path]:
    """Engine defaults and the available discovery inputs beside them."""
    return [engine, *(path for name in ("anchors.toml", "functions.json", "table-readers.json", "script.json",
                                      "window-functions.json", "windows.tsv", "table-windows.toml")
                      if (path := engine.with_name(name)).is_file())]


def load(name: str, root: Path | None = None) -> Knowledge:
    """Load one required family engine file without instance metadata."""
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise schema.ProfileError("family name must be a lowercase hyphenated identifier")
    path = (FAMILY_DIR if root is None else Path(root)) / name / "engine.toml"
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise schema.ProfileError(f"{path}: cannot read family knowledge: {exc}") from exc
    header = data.pop("family", None)
    if (not isinstance(header, dict) or set(header) != {"schema", "name", "version", "sources"}
            or type(header["schema"]) is not int or header["schema"] != 1 or header["name"] != name
            or not isinstance(header["version"], str) or not VERSION.fullmatch(header["version"])
            or not isinstance(header["sources"], list) or not header["sources"]
            or any(not isinstance(source, str) or not source.strip() for source in header["sources"])):
        raise schema.ProfileError(f"{path}: invalid family schema, identity, version or sources")
    records = data.pop(schema.RECORD, None)
    if not isinstance(records, list) or not records or not data or schema.HEADER in data:
        raise schema.ProfileError(f"{path}: family knowledge needs verified facts and their records")
    facts = schema.family_facts(data, records, path)
    return Knowledge(path, name, header["version"], data, facts)


def load_script(name: str, root: Path | None = None) -> ScriptKnowledge:
    """Load portable command semantics separately from compatibility defaults."""
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise schema.ProfileError("family name must be a lowercase hyphenated identifier")
    path = (FAMILY_DIR if root is None else Path(root)) / name / "script.json"
    try:
        with path.open(encoding="utf-8") as stream:
            document = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise schema.ProfileError(f"{path}: cannot read family script knowledge: {exc}") from exc
    try:
        discovery = document["discovery"]
        fixed = document["fixed_lengths"]
        text = document["text_offsets"]
        code = document["code_offsets"]
        ends = document["end_ops"]
        variables = document["variable_handlers"]
        defaults = document["defaults"]
        commands = defaults["command_lengths"]
        default_text = defaults.get("text_ptr_at", {})
        default_code = defaults.get("code_ptr_at", {})
        default_ends = defaults.get("end_ops", ends)
        trainer = defaults["trainerbattle_text_offsets"]
        continues = defaults["trainerbattle_continue_types"]
        version = document["version"]
    except (KeyError, TypeError) as exc:
        raise schema.ProfileError(f"{path}: incomplete family script knowledge") from exc

    def valid_lengths(rows) -> bool:
        return isinstance(rows, dict) and all(
            isinstance(key, str) and key.isdigit() and type(value) is int and 1 <= value <= 255
            for key, value in rows.items())

    def valid_text(rows, lengths) -> bool:
        return isinstance(rows, dict) and all(
            isinstance(key, str) and key.isdigit() and key in lengths
            and isinstance(values, list) and values
            and all(type(offset) is int and 0 < offset < lengths[key] for offset in values)
            for key, values in rows.items())

    def valid_code(rows, lengths) -> bool:
        return isinstance(rows, dict) and all(
            isinstance(key, str) and key.isdigit() and key in lengths
            and type(offset) is int and 0 < offset < lengths[key]
            for key, offset in rows.items())

    lengths = dict(fixed) if isinstance(fixed, dict) else {}
    lengths.update(commands if isinstance(commands, dict) else {})
    if (document.get("schema") != 1 or document.get("family") != name
            or not isinstance(version, str) or not VERSION.fullmatch(version)
            or not isinstance(document.get("sources"), list) or not document["sources"]
            or not isinstance(discovery, dict) or not isinstance(discovery.get("fingerprints"), list)
            or not valid_lengths(fixed) or not fixed or not valid_lengths(commands)
            or not isinstance(variables, dict) or any(op in fixed for op in variables)
            or not valid_text(text, fixed) or not valid_text(default_text, lengths)
            or not valid_code(code, fixed) or not valid_code(default_code, lengths)
            or not isinstance(ends, list) or any(type(op) is not int or str(op) not in fixed for op in ends)
            or not isinstance(default_ends, list)
            or any(type(op) is not int or str(op) not in lengths for op in default_ends)
            or not isinstance(trainer, dict) or not isinstance(continues, list)):
        raise schema.ProfileError(f"{path}: invalid family script knowledge")
    bios = discovery.get("bios")
    if bios is not None:
        def registers(value):
            return isinstance(value, list) and all(type(reg) is int and 0 <= reg <= 3 for reg in value)

        if (not isinstance(bios, dict) or not isinstance(bios.get("source"), str)
                or not bios["source"] or not registers(bios.get("clobbers"))
                or not isinstance(bios.get("calls"), dict)):
            raise schema.ProfileError(f"{path}: invalid BIOS interface")
        for number, call in bios["calls"].items():
            if (not number.isdigit() or not 0 <= int(number) <= 255 or not isinstance(call, dict)
                    or not isinstance(call.get("name"), str) or not call["name"]
                    or any(not registers(call.get(key)) for key in ("arguments", "returns", "writes"))
                    or not set(call["writes"]) <= set(call["arguments"]) or not set(call["returns"]) <= set(bios["clobbers"])):
                raise schema.ProfileError(f"{path}: invalid BIOS call")
    validation = discovery.get("validation", {})
    if (not isinstance(validation, dict)
            or type(validation.get("rom_header_size", 0)) is not int
            or validation.get("rom_header_size", 0) < 0
            or any(type(validation[key]) is not int or not 0 <= validation[key] <= 255
                   for key in ("padding_opcode", "erased_byte") if key in validation)
            or any(not valid_text(validation.get(key, {}), lengths) for key in ("null_ends", "runtime_text"))
            or not isinstance(validation.get("runtime_ranges", []), list)
            or any(not isinstance(row, list) or len(row) != 2
                   or any(type(value) is not int for value in row) or not 0 <= row[0] < row[1] <= 0x100000000
                   for row in validation.get("runtime_ranges", []))):
        raise schema.ProfileError(f"{path}: invalid script validation rules")
    text_ptr_at = dict(default_text)
    text_ptr_at.update(text)
    code_ptr_at = dict(default_code)
    code_ptr_at.update(code)
    data = {"script": {"command_lengths": lengths, "text_ptr_at": text_ptr_at,
                       "code_ptr_at": code_ptr_at, "end_ops": default_ends,
                       "trainerbattle_text_offsets": trainer,
                       "trainerbattle_continue_types": continues}}
    discovery = dict(discovery, fixed_lengths=fixed, text_offsets=text,
                     variable_handlers=variables, defaults=defaults)
    return ScriptKnowledge(path, name, version, data, discovery)
