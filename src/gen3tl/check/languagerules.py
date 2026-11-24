"""Evaluate declared byte operations without modifying catalog or ROM data."""

from __future__ import annotations

from dataclasses import dataclass

from ..core.charmap import Charmap, CharmapError
from ..core.lists import append_unique
from ..profile.languagerules import load


class RuleError(ValueError):
    pass


@dataclass(frozen=True)
class Evaluation:
    branch: str | None
    result: bytes
    stored: bytes
    buffer_bytes: int | None
    temporary: tuple[bytes, ...]
    problems: tuple[str, ...]

    @property
    def used_bytes(self) -> int:
        return len(self.stored)


@dataclass(frozen=True)
class Problem:
    entry: str
    message: str
    severity: str = "error"


def _terminated(value: bytes, terminator: int) -> bytes:
    try:
        return value[:value.index(terminator) + 1]
    except ValueError as exc:
        raise RuleError("missing terminator") from exc


def _field(operand: dict, value: bytes, terminator: int) -> bytes:
    binding = operand["bindings"][0]
    if not isinstance(value, bytes) or not value or len(value) > binding["byte_budget"]:
        raise RuleError(f"{operand['id']}: invalid field byte count")
    if binding["terminated"]:
        return _terminated(value, terminator)
    if len(value) != binding["byte_budget"]:
        raise RuleError(f"{operand['id']}: incomplete immediate field")
    return value


def validate_operand(operand: dict, value: bytes, terminator: int, *,
                     original: bytes | None = None, cm: Charmap | None = None,
                     glyphs: set[int] | None = None) -> bytes:
    """Validate field bytes against presets, custom limits and available glyphs."""
    value = _field(operand, value, terminator)
    off = bytes(operand.get("off", []))
    preset = ("off" in operand["choices"] and value == off)
    if allowed := operand.get("allowed_bytes"):
        if any(byte not in allowed for byte in value):
            raise RuleError(f"{operand['id']}: unsupported arithmetic operand")
        preset = True
    if original is not None and "original" in operand["choices"]:
        preset |= value == _field(operand, original, terminator)
    content = value.split(bytes([terminator]), 1)[0]
    if not preset:
        limits = operand.get("custom_bytes")
        if "custom" not in operand["choices"] or limits is None:
            raise RuleError(f"{operand['id']}: unsupported operand")
        if not limits[0] <= len(content) <= limits[1]:
            raise RuleError(f"{operand['id']}: custom byte count {len(content)} outside {limits}")
    cm = cm or Charmap()
    if any(byte not in cm.decode_table for byte in content):
        raise RuleError(f"{operand['id']}: controls or unknown glyphs are unsupported")
    if glyphs is not None and any(byte not in glyphs for byte in content):
        raise RuleError(f"{operand['id']}: unavailable glyph")
    return value


def _encode_field(operand: dict, text: str, terminator: int, cm: Charmap) -> bytes:
    if any(char not in cm.encode_table for char in text):
        raise RuleError(f"{operand['id']}: expected encodable literal characters")
    value = cm.encode(text, terminate=False)
    binding = operand["bindings"][0]
    if binding["terminated"]:
        value += bytes([terminator])
    else:
        value = value.ljust(binding["byte_budget"], bytes([binding["pad"]]))
    return value


def encode_operand(operand: dict, text: str, terminator: int, cm: Charmap, *,
                   original: bytes | None = None, glyphs: set[int] | None = None) -> bytes:
    """Encode literal text using the backing field's padding and terminator."""
    value = _encode_field(operand, text, terminator, cm)
    return validate_operand(operand, value, terminator, original=original, cm=cm, glyphs=glyphs)


def _matches(condition: dict, content: bytes, quantity: int) -> bool:
    op = condition["op"]
    if op == "quantity_gt":
        return quantity > condition["value"]
    if op == "quantity_ne":
        return quantity != condition["value"]
    if op == "empty":
        return not content
    if op == "nonempty":
        return bool(content)
    if op == "first_in":
        return bool(content) and content[0] in condition["bytes"]
    if op == "last_in":
        return bool(content) and content[-1] in condition["bytes"]
    if op == "last_not_in":
        return bool(content) and content[-1] not in condition["bytes"]
    raise RuleError(f"unknown condition: {op}")


def evaluate(rule: dict, text: bytes, operands: dict[str, bytes], *, routine: str | None = None,
             quantity: int = 1, originals: dict[str, bytes] | None = None,
             cm: Charmap | None = None, glyphs: set[int] | None = None) -> Evaluation:
    """Evaluate a resolved language rule against encoded text."""
    if type(quantity) is not int or quantity < 0:
        raise RuleError("quantity must be a nonnegative integer")
    if not isinstance(text, bytes):
        raise RuleError("input must be encoded bytes")
    terminator = rule["terminator"]
    content = _terminated(text, terminator)[:-1]
    declarations = {operand["id"]: operand for operand in rule.get("operands", [])}
    if operands.keys() != declarations.keys():
        raise RuleError("operand identities do not match the rule")
    values = {name: validate_operand(operand, operands[name], terminator,
                                    original=(originals or {}).get(name), cm=cm, glyphs=glyphs)
              for name, operand in declarations.items()}
    routines = {row["id"]: row for row in rule.get("routines", [])}
    if routine is None and len(routines) == 1:
        routine = next(iter(routines))
    if routine not in routines and (routine is not None or routines):
        raise RuleError("select a declared routine")
    limit = routines.get(routine, {}).get("buffer_bytes")
    stored = bytearray((b"" if rule.get("output") == "empty" else content) + bytes([terminator]))
    anchor = len(stored) - 1
    temporary, problems = [], []
    branch = next((branch for branch in rule["branches"]
                   if all(_matches(condition, content, quantity) for condition in branch["when"])), None)

    def expression(items):
        return bytes((values[item["operand"]][0] + item.get("add", 0)) & 255
                     if isinstance(item, dict) else item for item in items)

    for operation in branch["operations"] if branch else []:
        op = operation["op"]
        if op == "write":
            start = anchor + operation["at"]
            if not 0 <= start <= len(stored):
                raise RuleError("write outside initialized output")
            value = expression(operation["bytes"])
            stored[start:start + len(value)] = value
        elif op == "append":
            value = values[operation["operand"]] if "operand" in operation else expression(operation["bytes"])
            if "bytes" in operation:
                temporary.append(value)
                if len(value) > operation["buffer_bytes"]:
                    problems.append("temporary buffer overflow")
            try:
                value = _terminated(value, terminator)
                start = len(_terminated(stored, terminator)) - 1
            except RuleError:
                problems.append("append source or destination missing terminator")
                continue
            stored[start:start + len(value)] = value
        elif op == "strip_prefix":
            prefix = _terminated(values[operation["operand"]], terminator)[:-1]
            if len(prefix) != operation["count"]:
                raise RuleError("prefix length differs from fixed skip count")
            if stored.startswith(prefix):
                value = _terminated(stored, terminator)[len(prefix):]
                stored[:len(value)] = value
        else:
            raise RuleError(f"unknown operation: {op}")
    if limit is not None and len(stored) > limit:
        problems.append(f"buffer overflow: {len(stored)}/{limit} bytes")
    try:
        result = _terminated(bytes(stored), terminator)
    except RuleError:
        result = bytes(stored)
        problems.append("output missing terminator")
    return Evaluation(branch["id"] if branch else None, result, bytes(stored), limit,
                      tuple(temporary), tuple(problems))


def catalog_problems(prof, catalog: dict, cm: Charmap, *,
                     source_as_translation: bool = False, glyphs: set[int] | None = None) -> list[Problem]:
    """Check every binding and declared example using the current catalog state."""
    rules = prof.language_rules if hasattr(prof, "language_rules") else load(prof)
    by_key = {entry.msgctxt: sid for sid, (_, entry) in catalog.items()}
    problems = []

    def target(entry):
        if not source_as_translation:
            if "empty" in entry.flags:
                return ""
            if entry.msgstr:
                return entry.msgstr
        return None if entry.keyed else entry.msgid

    for rule in rules:
        if rule["kind"] != "catalog":
            continue
        # A partial catalog may hold none of them.
        if not any(by_key.get(binding["entry"], binding["entry"]) in catalog
                   for operand in rule["operands"] for binding in operand["bindings"]):
            continue
        values, originals = {}, {}
        incomplete = False
        for operand in rule["operands"]:
            copies = []
            for binding in operand["bindings"]:
                sid = by_key.get(binding["entry"], binding["entry"])
                if sid not in catalog:
                    problems.append(Problem(sid, f"{rule['id']}: missing operand entry"))
                    incomplete = True
                    continue
                entry = catalog[sid][1]
                text = target(entry)
                if text is None:
                    problems.append(Problem(sid, f"{rule['id']}: original operand unavailable", "warning"))
                    incomplete = True
                    continue
                try:
                    original = None if entry.keyed else _encode_field(
                        operand, entry.msgid, rule["terminator"], Charmap())
                    value = encode_operand(operand, text, rule["terminator"], cm,
                                           original=original, glyphs=glyphs)
                except RuleError as exc:
                    problems.append(Problem(sid, f"{rule['id']}: {exc}"))
                    incomplete = True
                    continue
                copies.append(value)
                values[operand["id"]] = value
                originals[operand["id"]] = original
            if len(set(copies)) > 1:
                for binding in operand["bindings"]:
                    sid = by_key.get(binding["entry"], binding["entry"])
                    problems.append(Problem(sid, f"{rule['id']}: {operand['id']}: routine copies differ"))
                incomplete = True
        if incomplete:
            continue
        quantities = {0, 1, 2}
        for branch in rule["branches"]:
            for condition in branch["when"]:
                if "value" in condition:
                    quantities.update((condition["value"], condition["value"] + 1))
        for key in rule.get("examples", []):
            sid = by_key.get(key, key)
            if sid not in catalog or (text := target(catalog[sid][1])) is None:
                continue
            try:
                encoded = cm.encode(text)
            except CharmapError:
                continue
            for routine in rule.get("routines") or [{"id": None}]:
                for quantity in sorted(quantities):
                    result = evaluate(rule, encoded, values, routine=routine["id"], quantity=quantity,
                                      originals=originals, cm=cm, glyphs=glyphs)
                    for message in result.problems:
                        problem = Problem(sid, f"{rule['id']}/{routine['id']}: {message}",
                                          "warning" if source_as_translation else "error")
                        append_unique(problems, problem)
    return problems
