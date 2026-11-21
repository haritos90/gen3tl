"""Profile-owned catalog labels and browsing rules for species forms."""

import re

from ..core.notes import has_prose

REFERENCE = re.compile(r"(?:s[0-9A-F]{7}|[a-z_]+:\d+|page:.+)")
COMPARISONS = ("types", "base_stats", "abilities", "learnset")


def check_browsing(block: dict, count: int) -> list[str]:
    """Validate declared comparisons and temporary-form exclusions."""
    if not isinstance(block, dict):
        return ["species_browsing forms: a table"]
    problems = []
    compare = block.get("compare")
    if not isinstance(compare, list) or not compare or any(key not in COMPARISONS for key in compare):
        problems.append("species_browsing forms compare: gameplay fields")
    if not has_prose(block.get("evidence")):
        problems.append("species_browsing forms evidence")
    for key in ("preserve_methods", "battle_only"):
        values = block.get(key, [])
        limit = count if key == "battle_only" else 65536
        if (not isinstance(values, list) or any(type(value) is not int or not 0 < value < limit for value in values)
                or len(set(values)) != len(values)):
            problems.append(f"species_browsing forms {key}: unique identifiers")
    kinds = block.get("preserve_kinds", [])
    if not isinstance(kinds, list) or any(not isinstance(kind, str) or not kind.strip() for kind in kinds):
        problems.append("species_browsing forms preserve_kinds: names")
    return problems


def _traits(record: dict, indexed: dict) -> dict:
    """Compare gameplay values independently of storage locations."""
    learnset = record.get("learnset")
    source = indexed.get(record.get("learnset_species", record["slot"]))
    if learnset is not None and source is not None and source is not record:
        inherited = source.get("learnset")
        learnset = None if inherited is None else learnset + [row for row in inherited if row["method"] != 0]
    abilities = record.get("ability_details")
    return {
        "types": None if record.get("types") is None else sorted(set(record["types"])),
        "base_stats": record.get("base_stats"),
        "abilities": (sorted((row["id"], row["regular"], row["hidden"]) for row in abilities)
                      if abilities is not None else record.get("abilities")),
        "learnset": (sorted(set((row["method"], row["move"], row.get("level")) for row in learnset))
                     if learnset is not None else None),
    }


def classify(block: dict, records: list[dict]) -> dict[int, dict]:
    """Classify forms from declared gameplay differences and exclusions."""
    indexed = {row["slot"]: row for row in records}
    preserved = {edge["target"] for row in records for edge in row.get("evolution") or []
                 if edge.get("parameter", 0) > 0 and (edge.get("kind") in block.get("preserve_kinds", [])
                                                       or edge["method"] in block.get("preserve_methods", []))}
    battle_only = set(block.get("battle_only", []))
    traits = {slot: _traits(row, indexed) for slot, row in indexed.items()}
    classified = {}
    for slot, record in indexed.items():
        base = record.get("base_species")
        if base is None or base == slot:
            continue
        if base not in indexed:
            raise ValueError(f"species_browsing forms {slot}: base species {base} missing")
        missing = [key for key in block["compare"] if traits[slot][key] is None or traits[base][key] is None]
        if missing:
            raise ValueError(f"species_browsing forms {slot}: missing {', '.join(missing)}")
        differences = [key for key in block["compare"] if traits[slot][key] != traits[base][key]]
        reason = ("preserved_transformation" if slot in preserved else "battle_only" if slot in battle_only
                  else "gameplay_difference" if differences else "same_gameplay")
        classified[slot] = {"separate": reason == "gameplay_difference", "reason": reason, "differences": differences}
    if not battle_only <= classified.keys():
        raise ValueError("species_browsing forms battle_only: an exported alternate form is required")
    return classified


def check(block: dict, count: int) -> list[str]:
    """Reject ambiguous labels and incomplete family declarations."""
    if not isinstance(block, dict):
        return ["form_names: a table"]
    problems, seen = [], set()
    if not has_prose(block.get("evidence", "")):
        problems.append("form_names evidence")
    labels = block.get("labels", [])
    families = block.get("type_families", [])
    if not isinstance(labels, list) or not isinstance(families, list):
        return problems + ["form_names labels and type_families: lists"]
    for row in labels:
        if not isinstance(row, dict):
            problems.append("form_names label: a table")
            continue
        slot = row.get("species")
        if type(slot) is not int or not 0 <= slot < count:
            problems.append("form_names label species: a valid slot")
        elif slot in seen:
            problems.append(f"form_names duplicate species {slot}")
        else:
            seen.add(slot)
        if not isinstance(row.get("text"), str) or not REFERENCE.fullmatch(row["text"]):
            problems.append("form_names label text: a catalog reference")
        if type(row.get("full", False)) is not bool:
            problems.append("form_names label full: boolean")
        if "expected_gender" in row and (type(row["expected_gender"]) is not int or not 0 <= row["expected_gender"] <= 255):
            problems.append("form_names label expected_gender: a byte value")
    seen = set()
    for row in families:
        if not isinstance(row, dict):
            problems.append("form_names type family: a table")
            continue
        base = row.get("base")
        if type(base) is not int or not 0 <= base < count:
            problems.append("form_names type family base: a valid slot")
        elif base in seen:
            problems.append(f"form_names duplicate type family {base}")
        else:
            seen.add(base)
        for key in ("dex", "count"):
            if type(row.get(key)) is not int or row[key] < 1:
                problems.append(f"form_names type family {key}: a positive integer")
        if type(row.get("label_base", True)) is not bool:
            problems.append("form_names type family label_base: boolean")
        if not re.fullmatch(r"[a-z_]+", str(row.get("type_table", ""))):
            problems.append("form_names type family type_table")
        if not has_prose(row.get("evidence", "")):
            problems.append("form_names type family evidence")
    return problems


def apply(block: dict, records: list[dict]) -> None:
    """Attach only declared labels, leaving other names unresolved."""
    indexed = {row["slot"]: row for row in records}
    for record in records:
        record.update(form_name=None, form_name_full=False)
    for label in block.get("labels", []):
        record = indexed.get(label["species"])
        if record is None:
            raise ValueError(f"form_names: species {label['species']} is not exported")
        if "expected_gender" in label and record.get("gender_ratio") != label["expected_gender"]:
            raise ValueError(f"form_names: species {label['species']} gender differs")
        record.update(form_name=label["text"], form_name_full=label.get("full", False))
    for family in block.get("type_families", []):
        members = [r for r in records if r.get("base_species") == family["base"]]
        if len(members) != family["count"] or any(r.get("dex") != family["dex"] for r in members):
            raise ValueError(f"form_names: type family {family['base']} membership differs")
        for record in members:
            if record["form_name"] is not None:
                raise ValueError(f"form_names: overlapping label for species {record['slot']}")
            types = record.get("types")
            if not types or len(set(types)) != 1 or type(types[0]) is not int:
                raise ValueError(f"form_names: type family species {record['slot']} is not single-type")
            if record["slot"] == family["base"] and not family.get("label_base", True):
                continue
            record["form_name"] = f"{family['type_table']}:{types[0]}"
