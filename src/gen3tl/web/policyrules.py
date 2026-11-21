"""Typed engine-rule proposals resolve only declared policy capabilities."""

from copy import deepcopy

from ..check.policy import PolicyError
from ..core.charmap import CharmapError


class MeasurementsRequired(PolicyError):
    pass


def binding(rule):
    """Return the single supported policy control."""
    policies = rule.get("policies", [])
    if (rule.get("kind") != "policy" or len(policies) != 1
            or policies[0]["kind"] not in ("code_replacement", "text_prefix")):
        raise PolicyError("rule has no supported policy control")
    return policies[0]


def value_of(rule):
    return deepcopy(binding(rule)["current"])


def validate_value(rule, value, cm=None, glyphs=None, measure=None):
    """Validate full selections, including terminated field budgets."""
    policy = binding(rule)
    fields = policy["kind"] == "code_replacement"
    wanted = {"enabled", "fields"} if fields else {"enabled"}
    if not isinstance(value, dict) or set(value) != wanted or type(value["enabled"]) is not bool:
        raise PolicyError("rule value has unsupported fields")
    choice = ("enabled" if fields else "off") if value["enabled"] else "original"
    if choice not in rule.get("choices", []):
        raise PolicyError("rule choice is unsupported")
    if not fields:
        return []
    texts = value["fields"]
    if (not isinstance(texts, dict) or texts.keys() - set(policy.get("fields", []))
            or any(not isinstance(text, str) or not text.strip() for text in texts.values())):
        raise PolicyError("rule fields must contain declared nonempty text values")
    if not value["enabled"]:
        if texts:
            raise PolicyError("original units cannot carry replacement fields")
        return []
    if set(policy["capability"]["required"]) - texts.keys():
        raise PolicyError("rule is missing required unit fields")
    reports = []
    current = policy["current"]["fields"]
    for field, text in texts.items():
        limit = policy.get("field_limits", {}).get(field)
        changed = current.get(field) != text
        if changed and (rule.get("limit_status") == "unverified" or limit is None):
            raise PolicyError(f"{field}: custom unit limit is unverified")
        report = {"field": field, "text": text, "used": None, "limit": limit and limit["bytes"]}
        reports.append(report)
        if not changed:
            continue
        if cm is None or glyphs is None or measure is None:
            raise MeasurementsRequired(f"{field}: custom unit validation needs current font measurements")
        if any(char not in cm.encode_table for char in text):
            raise PolicyError(f"{field}: unit needs literal encodable characters")
        try:
            encoded = cm.encode(text)
            if any(byte not in glyphs for byte in encoded[:-1]):
                raise PolicyError(f"{field}: unit glyph is unavailable")
            if len(encoded) > limit["bytes"]:
                raise PolicyError(f"{field}: unit exceeds its terminated byte budget")
            width = measure.font_measure(limit.get("font", 0)).width(text, cm)
            if width > limit["pixels"]:
                raise PolicyError(f"{field}: unit exceeds its pixel budget")
            report.update(used=len(encoded), pixels=width, pixel_limit=limit["pixels"])
        except CharmapError as exc:
            raise PolicyError(f"{field}: {exc}") from exc
    return reports


def validate_record(rule, record, cm=None, glyphs=None, measure=None):
    if (not isinstance(record, dict)
            or set(record) != {"id", "capability_sha256", "base", "value"}):
        raise PolicyError("rule patch needs only id, capability_sha256, base and value")
    if record["id"] != rule["id"] or record["capability_sha256"] != rule["capability_sha256"]:
        raise PolicyError("rule capability changed since this draft was created")
    if record["base"] != value_of(rule):
        raise PolicyError("rule value changed since this draft was created")
    validate_value(rule, record["base"])
    return validate_value(rule, record["value"], cm, glyphs, measure)


def preview(rule, request, cm=None, glyphs=None, measure=None):
    """Use the same proposal checks in the worker and importer."""
    current = value_of(rule)
    original = {"enabled": False, **({"fields": {}} if "fields" in current else {})}
    record = {"id": rule["id"], "capability_sha256": rule["capability_sha256"],
              "base": current, "value": deepcopy(request.get("value", current))}
    if "draft" in request:
        record = {**request["draft"], "value": record["value"]}
    problems, fields = [], []
    try:
        fields = validate_record(rule, record, cm, glyphs, measure)
    except PolicyError as exc:
        problems.append(str(exc))
    return {"original": original, "current": current, "proposed": record["value"],
            "fields": fields, "problems": problems, "valid": not problems,
            "changes": [record] if not problems and record["value"] != current else []}


def patches(prof, records, cm=None, glyphs=None, measure=None):
    """Resolve identifiers server-side; reject unknown and overlapping controls."""
    from .editorrules import rules_data
    if not isinstance(records, list):
        raise PolicyError("engine_rules must be a list")
    rules = {rule["id"]: rule for rule in rules_data(prof, [])["rules"]}
    seen, targets, result = set(), set(), []
    for record in records:
        identity = record.get("id") if isinstance(record, dict) else None
        if not isinstance(identity, str) or identity not in rules or identity in seen:
            raise PolicyError("unknown or duplicate engine rule")
        seen.add(identity)
        rule = rules[identity]
        policy = binding(rule)
        target = policy["kind"], policy["name"]
        if target in targets:
            raise PolicyError("engine rules overlap one policy capability")
        targets.add(target)
        validate_record(rule, record, cm, glyphs, measure)
        if record["value"] != record["base"]:
            result.append({"id": identity, "kind": target[0], "name": target[1],
                           "base": deepcopy(record["base"]), "value": deepcopy(record["value"])})
    return result
