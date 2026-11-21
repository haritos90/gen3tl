"""Export engine rules with catalog references and policy state."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from ..profile.languagerules import load
from ..check.languagerules import RuleError, encode_operand, evaluate
from ..core.charmap import Charmap, CharmapError


SCHEMA = "1.0.0"
KEYBOARD_ORDER = ("base_pages", "first_page", "opening_keyboards", "reserved", "opening_keyboard_offsets",
                  "page_count", "labels", "labels_evidence", "evidence", "row_bindings", "chat", "table")


def catalog_preview(rule, entries, drafts, request, cm, glyphs=None):
    """Preview catalog states with the shared byte evaluator."""
    if rule["kind"] != "catalog":
        raise RuleError("rule has no catalog controls")
    rows = {row["id"]: row for row in entries}
    drafts = {row["id"]: row for row in drafts}
    proposal = request.get("operands", {})
    if proposal.keys() - {operand["id"] for operand in rule["operands"]}:
        raise RuleError("unknown operand")
    quantity = request.get("quantity", 2)
    if type(quantity) is not int or quantity < 0:
        raise RuleError("quantity must be a nonnegative integer")
    example = request.get("example")
    if example is not None and example not in rule.get("examples", []):
        raise RuleError("unknown example")
    name = request.get("name")
    if name is not None and not isinstance(name, str):
        raise RuleError("example name must be text")
    eos, base = rule["terminator"], Charmap()
    routines = rule.get("routines") or [{"id": None}]
    changes = []

    def state(row):
        return row.get("state") or ("empty" if "empty" in row.get("flags", []) else
                                    "text" if row.get("translation") else "original")

    def text(row, mode):
        value = {**row, **drafts.get(row["id"], {})} if mode == "proposed" else row
        return row.get("source_text") if mode == "original" or state(value) == "original" else (
            "" if state(value) == "empty" else value.get("translation"))

    def field(operand, value, codec):
        binding = operand["bindings"][0]
        encoded = codec.encode(value, terminate=False)
        return (encoded + bytes([eos]) if binding["terminated"] else
                encoded.ljust(binding["byte_budget"], bytes([binding["pad"]])))

    def preview(mode):
        codec = base if mode == "original" else cm
        fields, values, originals, problems, unavailable = [], {}, {}, [], []
        for operand in rule["operands"]:
            copies = []
            for binding in operand["bindings"]:
                key = binding["entry"]
                row = rows.get(key)
                item = {"operand": operand["id"], "entry": key, "routine": binding.get("routine"),
                        "limit": binding["byte_budget"], "used": None, "text": None, "state": None,
                        "problems": []}
                fields.append(item)
                if row is None:
                    unavailable.append(key)
                    continue
                value = text(row, mode)
                current = {**row, **drafts.get(key, {})} if mode == "proposed" else row
                item["state"] = "original" if mode == "original" else state(current)
                try:
                    original = field(operand, row["source_text"], base) if isinstance(row.get("source_text"), str) else None
                    selected = proposal.get(operand["id"]) if mode == "proposed" else None
                    if selected is not None:
                        choice = selected.get("choice")
                        if choice not in operand["choices"]:
                            raise RuleError(f"{operand['id']}: unsupported choice")
                        if choice == "off":
                            encoded = bytes(operand["off"])
                            value = codec.decode(encoded + bytes([eos]))[0]
                            if field(operand, value, codec) != encoded:
                                raise RuleError(f"{operand['id']}: preset cannot round-trip")
                            item["state"] = "text" if value else "empty"
                        elif choice == "original":
                            value, item["state"] = row.get("source_text"), "original"
                        else:
                            value, item["state"] = selected.get("text", ""), "text"
                            if not value:
                                raise RuleError(f"{operand['id']}: custom text must be nonempty")
                        if row.get("owner"):
                            raise RuleError("language-pack entry is read-only")
                        changes.append({"id": key, "translation": value if item["state"] == "text" else "",
                                        "state": item["state"]})
                    item["text"] = value
                    if value is None:
                        unavailable.append(key)
                        continue
                    encoding = base if item["state"] == "original" else codec
                    item["used"] = len(field(operand, value, encoding))
                    encoded = encode_operand(operand, value, eos, encoding, original=original,
                                             glyphs=None if mode == "original" else glyphs)
                    item["bytes"] = list(encoded)
                    copies.append(encoded)
                    routine = binding.get("routine")
                    values.setdefault(routine, {})[operand["id"]] = encoded
                    originals.setdefault(routine, {})[operand["id"]] = original
                except (RuleError, CharmapError) as exc:
                    item["problems"].append(str(exc))
                    problems.append(f"{key}: {exc}")
            if len(set(copies)) > 1:
                problems.append(f"{operand['id']}: routine copies differ")

        def run(input_text, count, routine):
            if any(char not in codec.encode_table for char in input_text):
                raise RuleError("example name requires encodable literal characters")
            return evaluate(rule, codec.encode(input_text), values.get(routine, {}), routine=routine,
                            quantity=count, originals=originals.get(routine, {}), cm=codec,
                            glyphs=None if mode == "original" else glyphs)

        input_text = name if name is not None else text(rows[example], mode) if example in rows else ""
        results = []
        if input_text is None:
            unavailable.append(example)
        for routine in routines:
            rid = routine["id"]
            result = {"routine": rid, "input": input_text, "text": None, "used": None,
                      "limit": routine.get("buffer_bytes"), "problems": []}
            results.append(result)
            if unavailable or problems:
                continue
            try:
                evaluated = run(input_text, quantity, rid)
                result.update(text=codec.decode(evaluated.result)[0], used=evaluated.used_bytes,
                              branch=evaluated.branch, stored=list(evaluated.stored),
                              problems=list(evaluated.problems))
            except (RuleError, CharmapError) as exc:
                result["problems"].append(str(exc))
        if mode == "proposed" and not problems and not unavailable:
            quantities = {0, 1, 2, quantity}
            for branch in rule["branches"]:
                for condition in branch["when"]:
                    if "value" in condition:
                        quantities.update((condition["value"], condition["value"] + 1))
            for key in rule.get("examples", []):
                sample = text(rows[key], mode) if key in rows else None
                if sample is None:
                    unavailable.append(key)
                    continue
                for routine in routines:
                    for count in sorted(quantities):
                        try:
                            evaluated = run(sample, count, routine["id"])
                            problems.extend(f"{key}/{routine['id']}: {problem}" for problem in evaluated.problems)
                        except (RuleError, CharmapError) as exc:
                            problems.append(f"{key}/{routine['id']}: {exc}")
        return {"fields": fields, "results": results, "problems": sorted(set(problems)),
                "unavailable": sorted(set(unavailable))}

    result = {mode: preview(mode) for mode in ("original", "current", "proposed")}
    proposed = result["proposed"]
    valid = not (proposed["problems"] or proposed["unavailable"] or
                 any(row["problems"] for row in proposed["results"]))
    return {**result, "changes": changes if valid else [], "valid": valid}


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def _reference(prof, binding):
    value = prof
    for part in binding["section"].split("."):
        value = value.get(part, {})
    if "name" in binding:
        return [row for row in value if row.get("name") == binding["name"]]
    return value if isinstance(value, list) else [value]


def _capability(prof, binding):
    kind, name = binding["kind"], binding["name"]
    if kind == "droppable":
        return prof.get("text_controls", {}).get(kind, {})[name]
    section = "text_prefixes" if kind == "text_prefix" else kind
    return next(row for row in prof.get(section, []) if row["name"] == name)


def capability_fingerprint(prof, rule: dict) -> str:
    """Bind a resolved rule and its referenced capabilities to the base ROM."""
    return _digest({"schema": SCHEMA, "rom_sha1": prof.get("rom", {}).get("sha1"),
                    "rule": rule,
                    "policies": [_capability(prof, row) for row in rule.get("policies", [])],
                    "references": [_reference(prof, row) for row in rule.get("references", [])]})


def _rekey(value, key):
    if isinstance(value, dict):
        return {key(k): _rekey(v, key) for k, v in value.items()}
    if isinstance(value, list):
        return [_rekey(v, key) for v in value]
    return key(value) if isinstance(value, str) else value


SOURCE_TEXT_FIELDS = frozenset({"english", "msgid", "source_text", "original_text"})


def _public(value):
    """Drop fields the key-only export rejects."""
    if isinstance(value, dict):
        return {k: _public(v) for k, v in value.items() if k not in SOURCE_TEXT_FIELDS}
    if isinstance(value, list):
        return [_public(v) for v in value]
    return value


def _policy(prof, binding, key):
    kind, name = binding["kind"], binding["name"]
    capability = _capability(prof, binding)
    if kind == "code_replacement":
        selected = prof.get("selected_code_replacements", {})
        current = {"enabled": name in selected, "fields": deepcopy(selected.get(name, {}))}
        capability = {"calls": [{"offset": row["guard"]["offset"], "byte_budget": row["guard"]["size"]}
                                for row in capability.get("calls", [])],
                      "required": [field for field in capability.get("routine", {}).get("words", [])
                                   if field not in capability.get("routine", {}).get("fallback", {})],
                      "fallbacks": {field: {"pointer": row["at"] if isinstance(row, dict) else row}
                                    for field, row in capability.get("routine", {}).get("fallback", {}).items()}}
    elif kind == "text_prefix":
        selected = prof.get("selected_text_prefixes", [])
        current = {"enabled": any(row["name"] == name for row in selected)}
        capability = {"pointer": capability["pointer"], "source": capability["source"]["offset"],
                      "limits": deepcopy(capability.get("limits", {}))}
    else:
        selections = prof.get("qa", {}).get(kind, [])
        ids = set(capability["ids"])
        selected = [row for row in selections if row.get("tokens") == capability.get("tokens")
                    and set(row.get("ids", [])) <= ids]
        current = {"enabled": bool(selected),
                   "entries": sorted({key(sid) for row in selected for sid in row["ids"]})}
    return {**deepcopy(binding), "capability": _rekey(capability, key), "current": current}


def _declarations(prof, binding, key, inventory):
    section = binding["section"]
    result = []
    for row in _reference(prof, binding):
        if section == "keyboard":
            ordered = {key: row[key] for key in KEYBOARD_ORDER if key in row}
            row = {**ordered, **row}
        sources = []
        hosts = [key(sid) for sid in row.get("ids", [])]
        if section == "layout.code_fill":
            sources = [key(sid) for slot in row.get("slots", {}).values() for sid in slot.get("strings", [])]
        elif section == "context.native_fill":
            pointers = {row["base"] + i * row.get("stride", 4) for i in range(row["count"])
                        if i not in row.get("exclude", [])}
            sources = [key(entry["id"]) for entry in inventory
                       if any((ref["at"] if isinstance(ref, dict) else ref[0]) in pointers
                              for ref in entry.get("refs", []))]
            sources.extend(key(sid) for variant in row.get("variants", []) for sid in variant)
        identity = row.get("name") or (hosts[0] if hosts else section)
        unverified = prof.get("policy_unverified", {}).get("code_fill", {})
        facts = _public(_rekey(row, key))
        if section == "keyboard":
            # Keyboard page origins appear in their dedicated view.
            for board in facts.get("keyboards", []):
                board.pop("base", None)
        result.append({"id": f"{section}/{identity}", "read_only": True,
                       "facts": facts, "hosts": hosts,
                       "sources": sorted(set(sources)),
                       "unverified": section == "layout.code_fill" and row.get("name") in unverified})
    return {**binding, "declarations": result}


def rules_data(prof, entries: list[dict], *, key=lambda value: value, inv=None) -> dict:
    """Build editor engine-rule data without unavailable source text."""
    by_key = {row["id"]: row for row in entries}
    rules = load(prof.data if hasattr(prof, "data") else prof)
    result = []
    for rule in rules:
        model = deepcopy(rule)
        for name in ("related", "examples"):
            model[name] = [key(sid) for sid in rule.get(name, [])]
        for operand in model["operands"]:
            for binding in operand["bindings"]:
                binding["entry"] = key(binding["entry"])
        model.update(capability_sha256=capability_fingerprint(prof, rule),
                     read_only=rule["kind"] not in ("catalog", "policy"),
                     collapsed=rule["kind"] == "engine_fills",
                     limit_status=rule.get("limit_status", "verified"))
        for name in ("choices", "routines", "branches", "examples"):
            model.setdefault(name, [])
        model.setdefault("output", "input")
        model["policies"] = [_policy(prof, row, key) for row in rule.get("policies", [])]
        model["references"] = [_declarations(prof, row, key, (inv or {}).get("entries", []))
                               for row in rule.get("references", [])]
        linked = list(model.get("related", [])) + list(model.get("examples", []))
        linked.extend(binding["entry"] for operand in model["operands"] for binding in operand["bindings"])
        for reference in model["references"]:
            for row in reference["declarations"]:
                linked.extend(row["hosts"] + row["sources"])
        model["sources"] = [{"entry": sid, "available": sid in by_key,
                             "state": ("empty" if "empty" in by_key[sid].get("flags", []) else
                                       "text" if by_key[sid].get("translation") else "original")
                             if sid in by_key else None,
                             "status": by_key.get(sid, {}).get("status"),
                             "reach": deepcopy(by_key.get(sid, {}).get("reach"))}
                            for sid in sorted(set(linked))]
        result.append(model)
    return {"schema": SCHEMA, "rules": result}
