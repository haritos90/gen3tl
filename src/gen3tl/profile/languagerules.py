"""Validate declarative language rules and their storage bindings."""

from __future__ import annotations

from copy import deepcopy

from ..core.notes import has_prose
from .schema import ProfileError


KINDS = frozenset({"catalog", "policy", "catalog_text", "reference", "engine_fills"})
CHOICES = frozenset({"original", "off", "custom", "enabled"})
REFERENCES = frozenset({"layout.code_fill", "context.native_fill", "easy_chat_alphabet",
                        "sprite_font", "keyboard"})
OPERATIONS = {
    "write": {"op", "at", "bytes"},
    "append": {"op", "operand", "bytes", "buffer_bytes"},
    "strip_prefix": {"op", "operand", "count"},
}
CONDITIONS = {
    "quantity_gt": {"op", "value"}, "quantity_ne": {"op", "value"},
    "last_in": {"op", "bytes"}, "last_not_in": {"op", "bytes"},
    "first_in": {"op", "bytes"}, "empty": {"op"}, "nonempty": {"op"},
}


def _keys(value, allowed, where, required=()):
    if not isinstance(value, dict):
        raise ProfileError(f"{where}: expected a table")
    if unknown := value.keys() - set(allowed):
        raise ProfileError(f"{where}: unknown keys {', '.join(sorted(unknown))}")
    if missing := set(required) - value.keys():
        raise ProfileError(f"{where}: missing keys {', '.join(sorted(missing))}")


def _list(value, where, *, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value):
        raise ProfileError(f"{where}: expected {'nonempty ' if nonempty else ''}array")
    return value


def _text(value, where):
    if not isinstance(value, str) or not value.strip():
        raise ProfileError(f"{where}: expected nonempty text")
    return value


def _integer(value, where, minimum=0, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ProfileError(f"{where}: invalid integer {value!r}")
    return value


def _bytes(value, where):
    for byte in _list(value, where, nonempty=True):
        _integer(byte, where, maximum=255)


def _texts(value, where):
    for text in _list(value, where):
        _text(text, where)
    if len(value) != len(set(value)):
        raise ProfileError(f"{where}: duplicate values")


def _choices(value, where):
    _texts(value, where)
    if set(value) - CHOICES:
        raise ProfileError(f"{where}: unknown choices")


def _named(rows, name, where, key="name"):
    _text(name, where)
    matches = [row for row in rows if row.get(key) == name]
    if len(matches) != 1:
        raise ProfileError(f"{where}: unknown or ambiguous binding {name!r}")
    return matches[0]


def _operand(raw, data, routines, where):
    _keys(raw, {"id", "bindings", "choices", "off", "custom_bytes", "allowed_bytes"}, where,
          {"id", "bindings", "choices"})
    _text(raw["id"], where)
    _choices(raw["choices"], where)
    bindings = []
    for binding in _list(raw["bindings"], where, nonempty=True):
        _keys(binding, {"table", "index", "entry", "routine"}, where, {"table", "index", "entry"})
        table = _named(data.get("table", []), binding["table"], where)
        index = _integer(binding["index"], where)
        if index >= table["count"]:
            raise ProfileError(f"{where}: table index outside declaration")
        _text(binding["entry"], where)
        if "routine" in binding:
            _text(binding["routine"], where)
        if routines and binding.get("routine") not in routines:
            raise ProfileError(f"{where}: unknown routine")
        if not routines and "routine" in binding:
            raise ProfileError(f"{where}: undeclared routine")
        bindings.append(dict(binding, offset=table["base"] + index * table["stride"]
                             + table.get("field_offset", 0), byte_budget=table["field_length"],
                             terminated=table.get("terminated", True), pad=table.get("pad", 255)))
    shapes = {(b["byte_budget"], b["terminated"], b["pad"]) for b in bindings}
    if len(shapes) != 1:
        raise ProfileError(f"{where}: grouped operands have different storage")
    if routines and (len(bindings) != len(routines) or {b["routine"] for b in bindings} != routines):
        raise ProfileError(f"{where}: operand must bind every routine exactly once")
    budget, terminated, _ = next(iter(shapes))
    capacity = budget - int(terminated)
    if "off" in raw:
        _bytes(raw["off"], where)
        if len(raw["off"]) > budget or "off" not in raw["choices"]:
            raise ProfileError(f"{where}: invalid off preset")
    elif "off" in raw["choices"]:
        raise ProfileError(f"{where}: off needs explicit bytes")
    if "custom_bytes" in raw:
        limits = raw["custom_bytes"]
        _list(limits, where)
        if len(limits) != 2:
            raise ProfileError(f"{where}: custom_bytes needs minimum and maximum")
        _integer(limits[0], where, maximum=capacity)
        _integer(limits[1], where, minimum=limits[0], maximum=capacity)
        if "custom" not in raw["choices"]:
            raise ProfileError(f"{where}: custom limits without custom choice")
    elif "custom" in raw["choices"]:
        raise ProfileError(f"{where}: custom choice needs byte limits")
    if "allowed_bytes" in raw:
        _bytes(raw["allowed_bytes"], where)
        if budget != 1 or "custom" in raw["choices"]:
            raise ProfileError(f"{where}: restricted bytes require preset-only immediate storage")
        if any(byte not in raw["allowed_bytes"] for byte in raw.get("off", [])):
            raise ProfileError(f"{where}: off outside allowed bytes")
    return dict(raw, bindings=bindings)


def _operand_ref(name, operands, where, *, immediate=False):
    _text(name, where)
    if name not in operands:
        raise ProfileError(f"{where}: unknown operand {name!r}")
    if immediate and any(b["byte_budget"] != 1 or b["terminated"] for b in operands[name]["bindings"]):
        raise ProfileError(f"{where}: byte expression needs an immediate operand")


def _branches(branches, operands, where):
    seen = set()
    for branch in _list(branches, where):
        _keys(branch, {"id", "when", "operations"}, where, {"id", "when", "operations"})
        _text(branch["id"], where)
        if branch["id"] in seen:
            raise ProfileError(f"{where}: duplicate branch")
        seen.add(branch["id"])
        for condition in _list(branch["when"], where):
            op = condition.get("op") if isinstance(condition, dict) else None
            if not isinstance(op, str) or op not in CONDITIONS:
                raise ProfileError(f"{where}: unknown condition {op!r}")
            _keys(condition, CONDITIONS[op], where, CONDITIONS[op])
            if "value" in condition:
                _integer(condition["value"], where)
            if "bytes" in condition:
                _bytes(condition["bytes"], where)
        for operation in _list(branch["operations"], where):
            op = operation.get("op") if isinstance(operation, dict) else None
            if not isinstance(op, str) or op not in OPERATIONS:
                raise ProfileError(f"{where}: unknown operation {op!r}")
            required = {"op"} if op == "append" else OPERATIONS[op]
            _keys(operation, OPERATIONS[op], where, required)
            if op == "write":
                _integer(operation["at"], where, minimum=-1)
            if op == "append":
                if ("operand" in operation) == ("bytes" in operation):
                    raise ProfileError(f"{where}: append needs one byte source")
                if "bytes" in operation:
                    _integer(operation.get("buffer_bytes"), where, minimum=1)
                    if len(_list(operation["bytes"], where)) > operation["buffer_bytes"]:
                        raise ProfileError(f"{where}: append exceeds temporary buffer")
            if "bytes" in operation:
                for byte in _list(operation["bytes"], where, nonempty=True):
                    if isinstance(byte, dict):
                        _keys(byte, {"operand", "add"}, where, {"operand"})
                        _operand_ref(byte["operand"], operands, where, immediate=True)
                        _integer(byte.get("add", 0), where, maximum=255)
                    else:
                        _integer(byte, where, maximum=255)
            if "operand" in operation:
                _operand_ref(operation["operand"], operands, where)
                operand = operands[operation["operand"]]
                if op == "append" and ("buffer_bytes" in operation or
                                       any(not b["terminated"] for b in operand["bindings"])):
                    raise ProfileError(f"{where}: append needs a terminated field")
                if op == "strip_prefix":
                    count = _integer(operation["count"], where, minimum=1)
                    if operand.get("custom_bytes") != [count, count] or "off" in operand["choices"]:
                        raise ProfileError(f"{where}: stripped prefix needs exact custom length and no off")


def _policy(binding, data, where):
    _keys(binding, {"kind", "name", "fields", "field_limits"}, where, {"kind", "name"})
    kind, name = binding["kind"], _text(binding["name"], where)
    if kind == "code_replacement":
        target = _named(data.get("code_replacement", []), name, where)
        allowed = target.get("routine", {}).get("words", [])
    elif kind == "text_prefix":
        _named(data.get("text_prefixes", []), name, where)
        allowed = []
    elif kind == "droppable":
        if name not in data.get("text_controls", {}).get("droppable", {}):
            raise ProfileError(f"{where}: unknown optional-token capability {name!r}")
        allowed = []
    else:
        raise ProfileError(f"{where}: unknown policy kind {kind!r}")
    _texts(binding.get("fields", []), where)
    if set(binding.get("fields", [])) - set(allowed):
        raise ProfileError(f"{where}: unknown policy fields")
    limits = binding.get("field_limits", {})
    _keys(limits, binding.get("fields", []), where)
    for field, limit in limits.items():
        _keys(limit, {"bytes", "pixels", "font"}, where, {"bytes", "pixels"})
        _integer(limit["bytes"], where, minimum=2)
        _integer(limit["pixels"], where, minimum=1)
        if "font" in limit:
            _integer(limit["font"], where, maximum=255)


def _reference(binding, data, where):
    _keys(binding, {"section", "name"}, where, {"section"})
    section = binding["section"]
    if not isinstance(section, str) or section not in REFERENCES:
        raise ProfileError(f"{where}: unknown reference section")
    target = data
    for key in section.split("."):
        target = target.get(key, {}) if isinstance(target, dict) else {}
    if not target:
        raise ProfileError(f"{where}: missing reference {section}")
    if "name" in binding:
        _named(target if isinstance(target, list) else [], binding["name"], where)


def load(data: dict) -> tuple[dict, ...]:
    """Validate declarations and resolve table offsets without changing profile data."""
    result, seen, occupied = [], set(), set()
    for raw in _list(data.get("language_rules", []), "language_rules"):
        _keys(raw, {"id", "title", "description", "kind", "choices", "operands", "routines",
                    "branches", "policies", "references", "related", "examples", "terminator", "output",
                    "limit_status", "evidence"}, "language_rules",
              {"id", "title", "description", "kind", "related", "evidence"})
        for key in ("id", "title", "description", "kind"):
            _text(raw[key], f"language_rules.{key}")
        if not has_prose(raw["evidence"]):
            raise ProfileError("language_rules.evidence: expected nonempty text")
        where = f"language_rules {raw['id']}"
        if raw["id"] in seen:
            raise ProfileError(f"{where}: duplicate rule")
        seen.add(raw["id"])
        if raw["kind"] not in KINDS:
            raise ProfileError(f"{where}: unknown rule kind")
        _choices(raw.get("choices", []), where)
        for key in ("related", "examples"):
            _texts(raw.get(key, []), where)
        if raw.get("limit_status", "verified") not in ("verified", "unverified"):
            raise ProfileError(f"{where}: unknown limit status")
        if raw.get("output", "input") not in ("input", "empty"):
            raise ProfileError(f"{where}: unknown output initialization")
        if "terminator" in raw:
            _integer(raw["terminator"], where, maximum=255)
        routines = set()
        for routine in _list(raw.get("routines", []), where):
            _keys(routine, {"id", "at", "buffer_bytes"}, where, {"id", "at"})
            _text(routine["id"], where)
            _integer(routine["at"], where)
            if "buffer_bytes" in routine:
                _integer(routine["buffer_bytes"], where, minimum=1)
            if routine["id"] in routines:
                raise ProfileError(f"{where}: duplicate routine")
            routines.add(routine["id"])
        operands = {}
        for operand in _list(raw.get("operands", []), where):
            resolved = _operand(operand, data, routines, where)
            if resolved["id"] in operands:
                raise ProfileError(f"{where}: duplicate operand")
            operands[resolved["id"]] = resolved
            for binding in resolved["bindings"]:
                span = set(range(binding["offset"], binding["offset"] + binding["byte_budget"]))
                if occupied & span:
                    raise ProfileError(f"{where}: overlapping operand bindings")
                occupied.update(span)
        _branches(raw.get("branches", []), operands, where)
        for policy in _list(raw.get("policies", []), where):
            _policy(policy, data, where)
        for reference in _list(raw.get("references", []), where):
            _reference(reference, data, where)
        if raw["kind"] in ("reference", "engine_fills", "catalog_text"):
            if operands or raw.get("policies") or raw.get("branches") or raw.get("choices"):
                raise ProfileError(f"{where}: read-only behavior cannot declare controls")
        if raw["kind"] in ("reference", "engine_fills") and not raw.get("references"):
            raise ProfileError(f"{where}: missing references")
        if raw["kind"] == "catalog" and (not operands or not raw.get("branches") or "terminator" not in raw):
            raise ProfileError(f"{where}: catalog rule needs operands, branches and terminator")
        if raw["kind"] == "policy" and not raw.get("policies"):
            raise ProfileError(f"{where}: missing policy binding")
        result.append(deepcopy(dict(raw, operands=list(operands.values()))))
    return tuple(result)
