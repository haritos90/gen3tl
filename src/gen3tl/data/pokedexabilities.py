"""Ability identity and description links for the Pokedex."""

from __future__ import annotations

from ..core.lists import append_unique
from ..core.notes import has_prose
from ..core.rom import RomEvidence

POINTER_LOW, POINTER_HIGH = 0x08000000, 0x0A000000


def check(block: dict) -> list[str]:
    """Problems in an ability block; an empty list means it can be extracted."""
    problems = [key for key in ("base", "stride", "count") if key not in block]
    if not has_prose(block.get("evidence", "")):
        problems.append("evidence")
    descriptions = block.get("descriptions")
    if descriptions:
        problems += [f"descriptions {key}" for key in ("base", "count") if key not in descriptions]
        if not has_prose(descriptions.get("evidence", "")):
            problems.append("descriptions evidence")
    presentation = block.get("presentation")
    if presentation is not None:
        if not isinstance(presentation, dict):
            return problems + ["presentation"]
        if not has_prose(presentation.get("evidence", "")):
            problems.append("presentation evidence")
        inheritance = presentation.get("inherit_forms")
        if inheritance is not None and (not isinstance(inheritance, dict)
                or type(inheritance.get("method")) is not int or inheritance["method"] < 0
                or type(inheritance.get("parameter_min")) is not int or inheritance["parameter_min"] < 1
                or not has_prose(inheritance.get("evidence", ""))):
            problems.append("presentation inherit_forms")
        for field in ("names", "descriptions"):
            rules = presentation.get(field, [])
            if not isinstance(rules, list):
                problems.append(f"presentation {field}")
                continue
            for index, rule in enumerate(rules):
                label = f"presentation {field} {index}"
                if not isinstance(rule, dict):
                    problems.append(label)
                    continue
                allowed = {"ability", "value", "species", "dex", "types", "evidence"}
                selectors = set(rule) & {"species", "dex", "types"}
                if (set(rule) - allowed or not selectors
                        or type(rule.get("ability")) is not int
                        or not 0 <= rule["ability"] < int(descriptions.get("count", block.get("count", 0)))
                        or not has_prose(rule.get("evidence", ""))):
                    problems.append(label)
                for selector in selectors:
                    values = rule[selector]
                    if (not isinstance(values, list) or not values
                            or any(type(value) is not int or value < 0 for value in values)
                            or len(values) != len(set(values))):
                        problems.append(f"{label} {selector}")
                value = rule.get("value")
                limit = int(block.get("count", 0)) if field == "names" else POINTER_HIGH - POINTER_LOW
                if type(value) is not int or not 0 <= value < limit:
                    problems.append(f"{label} value")
        proofs = presentation.get("rom")
        if not isinstance(proofs, list) or not proofs:
            problems.append("presentation rom")
        else:
            for proof in proofs:
                if not RomEvidence.valid(proof):
                    problems.append("presentation rom")
    return problems


def build(block: dict, data: bytes) -> list[dict]:
    """Extract ability ids, name offsets and description catalog ids."""
    descriptions = block.get("descriptions") or {}
    for proof in block.get("presentation", {}).get("rom", []):
        at = proof["offset"]
        if not RomEvidence.parse(proof).matches(data):
            raise ValueError(f"ability presentation ROM mismatch at {at:#x}")
    out = []
    for ability in range(int(block.get("first", 0)), int(block["count"])):
        start = int(block["base"]) + ability * int(block["stride"])
        description = None
        if descriptions and ability < int(descriptions["count"]):
            at = int(descriptions["base"]) + ability * int(descriptions.get("stride", 4))
            pointer = int.from_bytes(data[at:at + 4], "little")
            if POINTER_LOW <= pointer < POINTER_HIGH:
                description = f"s{pointer - POINTER_LOW:07X}"
        if descriptions and description is None:
            continue
        record = {
            "id": ability,
            "offset": f"0x{start:06x}",
            "name": f"{block.get('name_table', 'abilities')}:{ability}",
            "description": description,
        }
        names = list(dict.fromkeys(
            f"{block.get('name_table', 'abilities')}:{rule['value']}"
            for rule in block.get("presentation", {}).get("names", []) if rule["ability"] == ability))
        if names:
            record["alternate_names"] = names
        out.append(record)
    return out


def matches(rule: dict, species: dict, ability: int) -> bool:
    """Match raw species, National numbers and original types."""
    return (rule["ability"] == ability
            and ("species" not in rule or species["slot"] in rule["species"])
            and ("dex" not in rule or species.get("dex") in rule["dex"])
            and ("types" not in rule or bool(set(species.get("types") or []) & set(rule["types"]))))


def resolve(block: dict, species: dict, ability: dict) -> dict:
    """Resolve both selectors independently, retaining the underlying ability."""
    result = {key: ability[key] for key in ("name", "description")}
    presentation = block.get("presentation", {})
    for field, output in (("names", "name"), ("descriptions", "description")):
        for rule in presentation.get(field, []):
            if matches(rule, species, ability["id"]):
                result[output] = (f"{block.get('name_table', 'abilities')}:{rule['value']}" if field == "names"
                                  else f"s{rule['value']:07X}")
                break
    return result


def inherited_slots(block: dict, species: list[dict]) -> dict[int, list[dict]]:
    """Collect source slots for transformations preserving battle abilities."""
    inheritance = block.get("presentation", {}).get("inherit_forms")
    if not inheritance:
        return {}
    inherited = {}
    for record in species:
        for edge in record.get("evolution") or []:
            if edge["method"] != inheritance["method"] or edge["parameter"] < inheritance["parameter_min"]:
                continue
            target = inherited.setdefault(edge["target"], {})
            for detail in record.get("stored_ability_details", record.get("ability_details")) or []:
                row = target.setdefault(detail["id"], {"id": detail["id"], "regular": False,
                                                      "hidden": False, "sources": []})
                for role in ("regular", "hidden"):
                    row[role] |= detail[role]
                append_unique(row["sources"], record["slot"])
    return {slot: list(rows.values()) for slot, rows in inherited.items()}


def apply(block: dict, species: list[dict], abilities: list[dict]) -> None:
    """Attach displayed text to each species ability slot."""
    by_id = {row["id"]: row for row in abilities}
    inherited = inherited_slots(block, species)
    for record in species:
        if record["slot"] in inherited:
            record["stored_ability_details"] = record.get("stored_ability_details", record.get("ability_details"))
            record["ability_details"] = inherited[record["slot"]]
            record["abilities"] = [row["id"] for row in record["ability_details"]]
        for detail in record.get("ability_details") or []:
            if detail["id"] not in by_id:
                raise ValueError(f"species {record['slot']}: unknown ability {detail['id']}")
            detail.update(resolve(block, record, by_id[detail["id"]]))


def verify(block: dict, species: list[dict], abilities: list[dict]) -> list[str]:
    """Reject stale per-species presentation and standalone name aliases."""
    problems = []
    by_id = {row["id"]: row for row in abilities}
    limit = block.get("descriptions", {}).get("count", block["count"])
    if len(by_id) != len(abilities) or any(row["id"] >= limit or not row.get("description") for row in abilities):
        problems.append("abilities: duplicate or name-only records")
    for ability in abilities:
        names = list(dict.fromkeys(
            f"{block.get('name_table', 'abilities')}:{rule['value']}"
            for rule in block.get("presentation", {}).get("names", []) if rule["ability"] == ability["id"]))
        if ability.get("alternate_names", []) != names:
            problems.append(f"ability {ability['id']}: regenerate alternate names")
    inherited = inherited_slots(block, species)
    for record in species:
        if "abilities" in record and (record["abilities"] or []) != [row["id"] for row in record.get("ability_details") or []]:
            problems.append(f"ability slots: regenerate species slot {record['slot']}")
        if record["slot"] in inherited:
            actual = [{key: value for key, value in row.items() if key not in ("name", "description")}
                      for row in record.get("ability_details") or []]
            if ("stored_ability_details" not in record or actual != inherited[record["slot"]]
                    or record.get("abilities") != [row["id"] for row in actual]):
                problems.append(f"ability inheritance: regenerate species slot {record['slot']}")
        for detail in record.get("ability_details") or []:
            ability = by_id.get(detail["id"])
            if ability is None or any(detail.get(key) != value for key, value in resolve(block, record, ability).items()):
                problems.append(f"ability presentation: regenerate species slot {record['slot']}")
    return problems
