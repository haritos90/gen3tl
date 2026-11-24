"""Trainer headers, their stored party layouts and their maps for the Pokedex."""

from __future__ import annotations

import hashlib
import heapq
import re
from collections import defaultdict

from ..core.lists import append_unique
from ..core.notes import has_prose
from ..core.rom import Rom, pointer_at, value_at
from ..discover.maps import Walker
from .pokedexsprites import check_trainers, trainer_file

ROM_BASE = 0x08000000
TRAINERBATTLE = 0x5C
VARIABLES = 0x4000   # a trainer operand from here on names a variable
RUNTIME_MAP = re.compile(r"\d+\.\d+")
KINDS = ("league", "story", "special", "partners", "paired", "world")


def check(block: dict | None) -> list[str]:
    """Configuration required to prove every trainer and party field."""
    if not block:
        return ["block"]
    problems = []
    for key in ("base", "stride", "count", "name_table", "class_table", "evidence"):
        if key not in block:
            problems.append(key)
    fields = block.get("fields") or {}
    for key in ("party_flags", "class", "ai_flags", "party_size", "party"):
        if key not in fields:
            problems.append(f"fields {key}")
    party = block.get("party") or {}
    for key in ("custom_moves_flag", "held_item_flag", "default_stride", "custom_stride",
                "iv", "level", "species", "held_item", "moves", "moves_with_item",
                "move_count", "evidence"):
        if key not in party:
            problems.append(f"party {key}")
    if "class_names" in block:
        for key in ("base", "stride", "count", "pointer", "evidence"):
            if key not in block["class_names"]:
                problems.append(f"class_names {key}")
    if "difficulty" in block:
        problems += [f"difficulty {problem}" for problem in check_difficulty(block["difficulty"], block.get("count"))]
    if "ivs" in block:
        from . import pokedextrainerivs
        problems += [f"ivs {problem}" for problem in pokedextrainerivs.check(block["ivs"])]
    if "evs" in block:
        from . import pokedextrainerivs
        problems += [f"evs {problem}" for problem in pokedextrainerivs.check_evs(block["evs"])]
        if "ivs" not in block or block["evs"].get("absent") == "abilities" and "abilities" not in block:
            problems.append("evs: shared spread and ability declarations required")
    if "abilities" in block:
        from . import pokedextrainerabilities
        problems += [f"abilities {problem}" for problem in pokedextrainerabilities.check(block["abilities"])]
        if "ivs" not in block:
            problems.append("abilities: shared spread declarations required")
    if "maps" in block and not has_prose((block["maps"] or {}).get("evidence")):
        problems.append("maps evidence")
    if "resolutions" in (block.get("maps") or {}):
        problems += check_map_resolutions(block["maps"], block.get("count", 0))
    for typ, roles in ((block.get("maps") or {}).get("roles") or {}).items():
        if not str(typ).isdigit() or not isinstance(roles, dict) or set(roles) - set(ROLES) or any(
                not isinstance(offsets, list) or any(type(at) is not int or at < 2 for at in offsets)
                for offsets in roles.values()):
            problems.append(f"maps roles {typ}: opponent and partner operand offsets")
    if "encounters" in block:
        problems += [f"encounters {problem}" for problem in check_encounters(block["encounters"], block.get("count"))]
    if "participants" in block:
        problems += check_participants(block)
        if "maps" not in block:
            problems.append("participants: maps required")
    if "order" in block:
        problems += check_order(block["order"], block.get("count", 0))
    if "ev_training" in block:
        training = block["ev_training"] or {}
        if "maps" not in block:
            problems.append("ev_training: needs [pokedex.trainers.maps]")
        if not has_prose(training.get("evidence", "")):
            problems.append("ev_training evidence")
        maps = training.get("maps")
        if not isinstance(maps, list) or not maps or any(
                not isinstance(m, str) or not RUNTIME_MAP.fullmatch(m) for m in maps):
            problems.append("ev_training maps: a list of runtime map ids")
    if "kinds" in block:
        kinds = block["kinds"] or {}
        if not has_prose(kinds.get("evidence", "")):
            problems.append("kinds evidence")
        for key in ("league_classes", "story_classes", "paired_classes", "story_records", "partner_records"):
            values = kinds.get(key, [])
            limit = int(block.get("count", 0)) if key.endswith("records") else int((block.get("class_names") or {}).get("count", 256))
            if not isinstance(values, list) or any(type(value) is not int or not 0 <= value < limit for value in values):
                problems.append(f"kinds {key}: a list of valid ids")
        maps = kinds.get("special_maps", [])
        if not isinstance(maps, list) or any(not isinstance(m, str) or not RUNTIME_MAP.fullmatch(m) for m in maps):
            problems.append("kinds special_maps: a list of runtime map ids")
        if maps and "maps" not in block:
            problems.append("kinds special_maps: needs [pokedex.trainers.maps]")
    return problems + check_trainers(block)


def check_encounters(groups, count) -> list[str]:
    """Declared battle starts outside the difficulty selections.

    [[pokedex.trainers.encounters]] names a script instruction the map walk
    reaches and its records, one of:

      starter = [326, 327, 328]   # the record per starter value 1, 2, 3
      records = [453]              # every setting
      stages = [{ record = 459 }, { record = 460, flag = 0x821, condition = "After the second badge" }]

    Stages are successive encounters in declaration order. `commands` lists
    the literal battle commands the declaration covers; `evidence` is
    required."""
    problems = []
    if not isinstance(groups, list):
        return ["a list of tables"]
    for index, group in enumerate(groups):
        if not isinstance(group, dict) or "script" not in group or not has_prose(group.get("evidence", "")):
            problems.append(f"[{index}]: script and evidence")
            continue
        shapes = [key for key in ("starter", "records", "stages") if key in group]
        if len(shapes) != 1:
            problems.append(f"[{index}]: one of starter, records, stages")
            continue
        rows = group[shapes[0]] if shapes[0] != "stages" else [stage.get("record") for stage in group["stages"]]
        if not isinstance(rows, list) or not rows or any(
                type(record) is not int or count is not None and not 0 <= record < int(count) for record in rows):
            problems.append(f"[{index}]: valid record ids")
        for stage in group.get("stages", []):
            if ("flag" in stage) != ("condition" in stage):
                problems.append(f"[{index}]: a stage flag needs its condition label")
        if any(type(pc) is not int for pc in group.get("commands", [])):
            problems.append(f"[{index}]: command offsets")
        if group.get("native", "unconfirmed") not in ("confirmed", "unconfirmed"):
            problems.append(f"[{index}]: native status")
    return problems


def check_difficulty(difficulty: dict, count) -> list[str]:
    """The settings and every selection of the difficulty block."""
    problems = []
    for key in ("variable", "settings", "evidence", "selections"):
        if key not in difficulty:
            problems.append(key)
    settings = difficulty.get("settings")
    if not isinstance(settings, list) or not settings or any(not isinstance(name, str) for name in settings):
        problems.append("settings: a list of setting names")
        return problems
    if "presentation" in difficulty:
        presentation = difficulty["presentation"]
        if not isinstance(presentation, dict):
            return problems + ["presentation: a table"]
        if type(presentation.get("default")) is not int or presentation["default"] not in range(len(settings)):
            problems.append("presentation default: a setting value")
        order = presentation.get("order")
        if (not isinstance(order, list) or any(type(value) is not int for value in order)
                or sorted(order) != list(range(len(settings)))):
            problems.append("presentation order: every setting value once")
        for key in ("labels",):
            values = presentation.get(key)
            if (not isinstance(values, list) or len(values) != len(settings)
                    or any(not isinstance(value, str) or not value.strip() for value in values)):
                problems.append(f"presentation {key}: one label per setting")
        if not has_prose(presentation.get("evidence", "")):
            problems.append("presentation evidence")
    for index, selection in enumerate(difficulty.get("selections") or []):
        records = selection.get("records") if isinstance(selection, dict) else None
        if not isinstance(records, list) or len(records) != len(settings):
            problems.append(f"selections[{index}]: one record per setting")
            continue
        if count is not None and any(not isinstance(record, int) or not 0 <= record < int(count) for record in records):
            problems.append(f"selections[{index}]: a record id out of range")
        if "script" not in selection:
            problems.append(f"selections[{index}]: script")
        if "starter" in selection and type(selection["starter"]) is not int:
            problems.append(f"selections[{index}]: starter value")
        if selection.get("native", "unconfirmed") not in ("confirmed", "unconfirmed"):
            problems.append(f"selections[{index}]: native status")
        for alternative in selection.get("alternatives", []):
            if (alternative.get("setting") not in settings or type(alternative.get("record")) is not int
                    or type(alternative.get("level_above")) is not int):
                problems.append(f"selections[{index}]: alternative setting, record and level_above")
    if "starter" in difficulty:
        starter = difficulty["starter"]
        if (not isinstance(starter, dict) or type(starter.get("variable")) is not int
                or not isinstance(starter.get("species"), list) or not starter["species"]
                or any(type(value) is not int for value in starter["species"])
                or not has_prose(starter.get("evidence", ""))):
            problems.append("starter: variable, species and evidence")
    return problems


def difficulty_presentation(block: dict) -> dict | None:
    """Keep menu order separate from engine values."""
    difficulty = block.get("difficulty", {})
    presentation = difficulty.get("presentation")
    if presentation is None:
        return None
    out = {"settings": list(difficulty["settings"]),
           **{key: presentation[key] for key in ("default", "order", "labels")}}
    if "starter" in difficulty:
        out["starters"] = list(difficulty["starter"]["species"])
    return out


ROLES = ("opponent", "partner")
PARTICIPANT_ROLES = ("second_opponent", "partner")


def check_participants(block: dict) -> list[str]:
    """Require selectors, battle ownership and ROM evidence."""
    difficulty = block.get("difficulty", {})
    settings = difficulty.get("settings", [])
    scripts = {row["script"] for row in difficulty.get("selections", []) if "script" in row}
    scripts.update(row["script"] for row in block.get("encounters", []) if "records" in row)
    problems, claimed, anchors = [], set(), set()
    groups = block.get("participants")
    if not isinstance(groups, list):
        return ["participants: a list of tables"]
    for index, group in enumerate(groups):
        label = f"participants[{index}]"
        if not isinstance(group, dict):
            problems.append(f"{label}: a table")
            continue
        script = group.get("script")
        if type(script) is not int or script not in scripts or script in anchors:
            problems.append(f"{label}: unique selection script")
        else:
            anchors.add(script)
        if not has_prose(group.get("evidence", "")):
            problems.append(f"{label}: evidence")
        if group.get("native", "unconfirmed") not in ("confirmed", "unconfirmed"):
            problems.append(f"{label}: native status")
        commands = group.get("commands")
        if (not isinstance(commands, list) or not commands
                or any(type(pc) is not int or pc < 0 for pc in commands)):
            problems.append(f"{label}: command offsets")
        elif len(set(commands)) != len(commands) or claimed.intersection(commands):
            problems.append(f"{label}: commands already claimed")
        else:
            claimed.update(commands)
        proofs = group.get("rom")
        if not isinstance(proofs, list) or not proofs:
            problems.append(f"{label}: ROM proofs")
        else:
            for proof in proofs:
                if (not isinstance(proof, dict) or type(proof.get("offset")) is not int or proof["offset"] < 0
                        or type(proof.get("size")) is not int or proof["size"] <= 0
                        or not isinstance(proof.get("sha256"), str)
                        or not re.fullmatch(r"[0-9a-f]{64}", proof["sha256"])):
                    problems.append(f"{label}: ROM proof identity")
        selectors = group.get("selectors")
        if not isinstance(selectors, list) or not selectors:
            problems.append(f"{label}: selectors")
            continue
        occupied = defaultdict(set)
        for row in selectors:
            if not isinstance(row, dict) or row.get("role") not in PARTICIPANT_ROLES:
                problems.append(f"{label}: participant role")
                continue
            served = row.get("settings", settings)
            records = row.get("records")
            if (not isinstance(served, list) or not served or any(name not in settings for name in served)
                    or len(set(served)) != len(served) or not isinstance(records, list) or len(records) != len(served)
                    or any(type(record) is not int or not 0 <= record < block.get("count", 0) for record in records)):
                problems.append(f"{label}: one valid record per setting")
                continue
            starter = row.get("starter")
            if starter is not None and (type(starter) is not int
                    or not 1 <= starter <= len(difficulty.get("starter", {}).get("species", []))):
                problems.append(f"{label}: starter value")
                continue
            for setting in served:
                key = (row["role"], setting)
                if occupied[key] and (starter is None or None in occupied[key] or starter in occupied[key]):
                    problems.append(f"{label}: overlapping selectors")
                occupied[key].add(starter)
    return problems


def validate_participants(groups: list[dict], data: bytes) -> None:
    """Reject selectors when their ROM evidence changes."""
    for group in groups:
        for proof in group["rom"]:
            at, size = proof["offset"], proof["size"]
            if at + size > len(data) or hashlib.sha256(data[at:at + size]).hexdigest() != proof["sha256"]:
                raise ValueError(f"trainer participants: ROM span differs at {at:#x}")


def check_order(order, count: int) -> list[str]:
    """Require explicit encounter identities and guarded ordering evidence."""
    if not isinstance(order, dict):
        return ["order: a table"]
    problems, ids = [], set()
    if not has_prose(order.get("evidence")):
        problems.append("order: evidence")
    if not isinstance(order.get("searched"), str) or not order["searched"].strip():
        problems.append("order: searched")
    rules = order.get("rules")
    if not isinstance(rules, list) or not rules:
        return problems + ["order: rules"]
    for index, rule in enumerate(rules):
        label = f"order.rules[{index}]"
        if not isinstance(rule, dict):
            problems.append(f"{label}: a table")
            continue
        name = rule.get("id")
        if not isinstance(name, str) or not name.strip() or name in ids:
            problems.append(f"{label}: unique id")
        else:
            ids.add(name)
        if not has_prose(rule.get("evidence")):
            problems.append(f"{label}: evidence")
        if rule.get("native") not in ("confirmed", "unconfirmed"):
            problems.append(f"{label}: native status")
        proofs = rule.get("rom")
        if not isinstance(proofs, list) or not proofs:
            problems.append(f"{label}: ROM proofs")
        else:
            for proof in proofs:
                if (not isinstance(proof, dict) or type(proof.get("offset")) is not int or proof["offset"] < 0
                        or type(proof.get("size")) is not int or proof["size"] <= 0
                        or not isinstance(proof.get("sha256"), str)
                        or not re.fullmatch(r"[0-9a-f]{64}", proof["sha256"])):
                    problems.append(f"{label}: ROM proof identity")
        sequence = rule.get("sequence")
        if not isinstance(sequence, list) or len(sequence) < 2:
            problems.append(f"{label}: ordered encounter sequence")
            continue
        for selector in sequence:
            if (not isinstance(selector, dict) or set(selector) - {"kind", "script", "record"}
                    or selector.get("kind") not in ("selection", "group", "battle")
                    or selector["kind"] == "battle" and ("script" in selector or "record" not in selector)
                    or selector["kind"] != "battle" and "script" not in selector
                    or "script" in selector and (type(selector["script"]) is not int or selector["script"] < 0)
                    or "record" in selector and (type(selector["record"]) is not int
                                                  or not 0 <= selector["record"] < count)):
                problems.append(f"{label}: encounter selector")
    return problems


def validate_order(order: dict | None, data: bytes) -> None:
    """Reject changed scripts before applying their ordering constraints."""
    for rule in (order or {}).get("rules", []):
        for proof in rule["rom"]:
            at, size = proof["offset"], proof["size"]
            if at + size > len(data) or hashlib.sha256(data[at:at + size]).hexdigest() != proof["sha256"]:
                raise ValueError(f"trainer order: ROM span differs at {at:#x}")


def order_encounters(battles: list[dict], trainers: list[dict], order: dict | None) -> list[dict]:
    """Respect proven precedence, using levels among unconstrained encounters."""
    levels = {row["id"]: row["average_level"] for row in trainers}
    battles = sorted(battles, key=lambda row: (
        min(levels[v["record"]] if levels[v["record"]] is not None else 101 for v in row["variants"]),
        min(v["record"] for v in row["variants"])))
    successors = [set() for _ in battles]
    incoming = [0] * len(battles)
    for row in battles:
        row["order"] = {"status": "unknown", "rules": []}
    for rule in (order or {}).get("rules", []):
        sequence = []
        for selector in rule["sequence"]:
            matched = [index for index, row in enumerate(battles) if row["kind"] == selector["kind"]
                       and ("script" not in selector or row["script"] == f"0x{selector['script']:07X}")
                       and ("record" not in selector or any(v["record"] == selector["record"]
                                                           for v in row["variants"]))]
            if len(matched) != 1 or matched[0] in sequence:
                raise ValueError(f"trainer order: {rule['id']}: encounter selector is not unique")
            sequence.append(matched[0])
            row = battles[matched[0]]
            row["order"]["status"] = "constrained"
            row["order"]["rules"].append(rule["id"])
        for before, after in zip(sequence, sequence[1:]):
            if after not in successors[before]:
                successors[before].add(after)
                incoming[after] += 1
    ready = [index for index, count in enumerate(incoming) if not count]
    heapq.heapify(ready)
    result = []
    while ready:
        index = heapq.heappop(ready)
        result.append(battles[index])
        for after in sorted(successors[index]):
            incoming[after] -= 1
            if not incoming[after]:
                heapq.heappush(ready, after)
    if len(result) != len(battles):
        raise ValueError("trainer order: cyclic encounter constraints")
    return result


def order_summary(battles: list[dict], block: dict) -> dict:
    """List every encounter without an established story relationship."""
    order = block.get("order", {})
    return {"unknown": [row["id"] for row in battles if row["order"]["status"] == "unknown"],
            "searched": order.get("searched", "No story ordering rules declared."),
            "rules": order.get("rules", [])}


def verify_encounters(battles, trainers: list[dict], block: dict) -> list[str]:
    """Check participant references and declared difficulty assignments."""
    error = ["trainer encounters: regenerate the dataset with gen3tl data pokedex"]
    settings = block.get("difficulty", {}).get("settings", [])
    if not isinstance(battles, list):
        return error
    memberships = [[] for _ in trainers]
    for index, battle in enumerate(battles):
        if (not isinstance(battle, dict) or battle.get("id") != index
                or not isinstance(battle.get("variants"), list) or not battle["variants"]):
            return error
        for row in battle["variants"]:
            if (not isinstance(row, dict) or type(row.get("record")) is not int
                    or not 0 <= row["record"] < len(trainers)
                    or row.get("role") not in ("opponent", *PARTICIPANT_ROLES)
                    or "starter" in row and type(row["starter"]) is not int
                    or "settings" not in row or row["settings"] is not None and (
                        not isinstance(row["settings"], list) or not row["settings"]
                        or any(value not in settings for value in row["settings"]))):
                return error
            append_unique(memberships[row["record"]], index)
    if any(row.get("encounters") != memberships[index] or "base" in row for index, row in enumerate(trainers)):
        return error
    for group in block.get("participants", []):
        matched = [battle for battle in battles if battle.get("script") == f"0x{group['script']:07X}"]
        if len(matched) != 1:
            return error
        expected = {(row["role"], setting, record, row.get("starter")) for row in group["selectors"]
                    for setting, record in zip(row.get("settings", settings), row["records"])}
        actual = {(row["role"], setting, row["record"], row.get("starter")) for row in matched[0]["variants"]
                  if row["role"] in PARTICIPANT_ROLES for setting in row["settings"] or settings}
        if actual != expected:
            return error
    try:
        expected = order_encounters([{**row} for row in battles], trainers, block.get("order"))
        if any(row["id"] != index or row.get("order") != battles[index].get("order")
               for index, row in enumerate(expected)):
            return error
    except (KeyError, TypeError, ValueError):
        return error
    return []


def selected_records(difficulty: dict | None) -> set[int]:
    """Records a declared difficulty selection picks."""
    return {record for selection in (difficulty or {}).get("selections", []) for record in selection["records"]}


def check_map_resolutions(maps: dict, count: int) -> list[str]:
    """Require guarded associations anchored in reachable scripts."""
    problems, ids = [], set()
    if not isinstance(maps.get("searched"), str) or not maps["searched"].strip():
        problems.append("maps searched")
    rows = maps["resolutions"]
    if not isinstance(rows, list) or not rows:
        return problems + ["maps resolutions: a nonempty list"]
    for row in rows:
        if not isinstance(row, dict):
            problems.append("maps resolution: a table")
            continue
        name = row.get("id")
        if not isinstance(name, str) or not name.strip() or name in ids:
            problems.append("maps resolution: unique id")
        else:
            ids.add(name)
        if type(row.get("script")) is not int or row["script"] < 0:
            problems.append("maps resolution: script offset")
        records = row.get("records")
        if (not isinstance(records, list) or not records
                or any(type(record) is not int or not 0 <= record < count for record in records)
                or len(set(records)) != len(records)):
            problems.append("maps resolution: unique valid records")
        if (not has_prose(row.get("evidence"))
                or row.get("native") not in ("confirmed", "unconfirmed")):
            problems.append("maps resolution: evidence and native status")
        proofs = row.get("rom")
        if not isinstance(proofs, list) or not proofs or any(
                not isinstance(proof, dict) or type(proof.get("offset")) is not int or proof["offset"] < 0
                or type(proof.get("size")) is not int or proof["size"] <= 0
                or not isinstance(proof.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", proof["sha256"]) for proof in proofs):
            problems.append("maps resolution: ROM proofs")
    return problems


def map_summary(trainers: list[dict], block: dict) -> dict:
    """Keep unresolved locations explicit alongside their search scope."""
    maps = block.get("maps") or {}
    return {"unknown": [row["id"] for row in trainers if not row.get("map")],
            "searched": maps.get("searched", "No variable battle search declared."),
            "resolutions": maps.get("resolutions", [])}


def encounters(trainers: list[dict], difficulty: dict | None, groups: list[dict] | None,
               commands: dict[int, list[tuple[str, int]]] | None,
               participants: list[dict] | None = None, order: dict | None = None,
               modes: dict | None = None) -> list[dict]:
    """Battle starts as encounters, each with its record variants.

    A selection script makes one encounter: its rows' records per setting,
    their starter values and level alternatives. A declared group makes one
    encounter, or one per stage. Every literal record outside them makes one
    encounter per role; a partner or second opponent only when the record has
    no other. Declared story constraints precede the lowest stored mean party
    level and record id; per record, `encounters` lists them and `difficulty` the
    settings it serves, null when it serves every setting or none. With
    `modes`, each encounter lists its trainerbattle commands and their types."""
    settings = list((difficulty or {}).get("settings", []))
    out = []
    linked = defaultdict(set)
    for pc, (_, scripts) in (modes or {}).items():
        for script in scripts:
            linked[script].add(pc)

    def add(kind, script, variants, pcs=()):
        out.append({"kind": kind, "script": f"0x{script:07X}" if script is not None else None,
                    "variants": variants, "commands": set(pcs) | linked.get(script, set())})

    by_script: dict[int, list[dict]] = {}
    for selection in (difficulty or {}).get("selections", []):
        by_script.setdefault(selection["script"], []).append(selection)
    for script, rows in by_script.items():
        served: dict[tuple, list[str]] = {}
        for row in rows:
            for setting, record in zip(settings, row["records"]):
                served.setdefault((record, row.get("starter"), None), []).append(setting)
            for alternative in row.get("alternatives", []):
                served.setdefault((alternative["record"], row.get("starter"), alternative["level_above"]),
                                  []).append(alternative["setting"])
        add("selection", script, [{"record": record, "role": "opponent",
                                   "settings": None if set(values) == set(settings) else
                                   [name for name in settings if name in values],
                                   **({"starter": starter} if starter is not None else {}),
                                   **({"level_above": level} if level is not None else {})}
                                  for (record, starter, level), values in served.items()])
    covered = set()
    for group in groups or []:
        covered.update(group.get("commands", []))
        pcs = group.get("commands", [])
        if "starter" in group:
            add("group", group["script"], [{"record": record, "role": "opponent", "settings": None, "starter": index + 1}
                                           for index, record in enumerate(group["starter"])], pcs)
        elif "records" in group:
            add("group", group["script"], [{"record": record, "role": "opponent", "settings": None}
                                           for record in group["records"]], pcs)
        else:
            for stage in group["stages"]:
                add("group", group["script"], [{"record": stage["record"], "role": "opponent", "settings": None,
                                                **({"flag": f"0x{stage['flag']:X}", "condition": stage["condition"]}
                                                   if "flag" in stage else {})}], pcs)
    for group in participants or []:
        covered.update(group["commands"])
        encounter = next(row for row in out if row["script"] == f"0x{group['script']:07X}")
        encounter["commands"].update(group["commands"])
        for selector in group["selectors"]:
            served = defaultdict(list)
            for setting, record in zip(selector.get("settings", settings), selector["records"]):
                served[record].append(setting)
            encounter["variants"].extend({"record": record, "role": selector["role"],
                                          "settings": None if set(values) == set(settings) else values,
                                          **({"starter": selector["starter"]} if "starter" in selector else {})}
                                         for record, values in served.items())
    literal: dict[tuple[int, str], list[int]] = defaultdict(list)
    for pc, operands in sorted((commands or {}).items()):
        if pc in covered:
            continue
        for role, record in operands:
            literal[(record, role)].append(pc)
    claimed = {variant["record"] for encounter in out for variant in encounter["variants"]}
    for (record, role), pcs in sorted(literal.items()):
        if role == "opponent" or record not in claimed and (record, "opponent") not in literal:
            add("battle", None, [{"record": record, "role": role, "settings": None}], pcs)
    for encounter in out:
        pcs = encounter.pop("commands")
        if modes is None:
            continue
        if pcs - modes.keys():
            raise ValueError(f"trainer encounters: unreached battle commands {sorted(pcs - modes.keys())}")
        encounter["commands"] = [{"script": f"0x{pc:07X}", "mode": modes[pc][0]} for pc in sorted(pcs)]
    levels = {trainer["id"]: trainer["average_level"] for trainer in trainers}
    out = [encounter for encounter in out if all(variant["record"] in levels for variant in encounter["variants"])]
    out = order_encounters(out, trainers, order)
    for index, encounter in enumerate(out):
        encounter["id"] = index
    for trainer in trainers:
        trainer["encounters"] = []
        trainer["difficulty"] = None
    serves: dict[int, set] = {}
    for encounter in out:
        for variant in encounter["variants"]:
            trainer = trainers[variant["record"]]
            append_unique(trainer["encounters"], encounter["id"])
            serves.setdefault(variant["record"], set()).update(variant["settings"] or settings)
    for record, values in serves.items():
        if settings and values != set(settings):
            trainers[record]["difficulty"] = [name for name in settings if name in values]
    return out


class _BattleWalker(Walker):
    """Map walker keeping the maps whose scripts reach each trainerbattle."""

    def __init__(self, rom: Rom, profile: dict, difficulty: dict | None = None,
                 groups: list[dict] | None = None, roles: dict | None = None,
                 participants: list[dict] | None = None, resolutions: list[dict] | None = None):
        super().__init__(rom, profile)
        self.battles: dict[int, set[str]] = defaultdict(set)
        self.selections: dict[int, set[int]] = defaultdict(set)
        self.commands: dict[int, list[tuple[str, int]]] = {}
        self.modes: dict[int, int] = {}
        self.links: dict[int, set[int]] = defaultdict(set)
        self.roles = {int(typ): spec for typ, spec in (roles or {}).items()}
        for selection in (difficulty or {}).get("selections", []):
            self.selections[selection["script"]].update(selection["records"])
            self.selections[selection["script"]].update(row["record"] for row in selection.get("alternatives", []))
        for group in groups or []:
            records = group.get("starter") or group.get("records") or [stage["record"] for stage in group["stages"]]
            self.selections[group["script"]].update(records)
        for group in participants or []:
            self.selections[group["script"]].update(record for row in group["selectors"] for record in row["records"])
        for row in resolutions or []:
            self.selections[row["script"]].update(row["records"])

    def walk_script(self, start, mid, name, event, local_id, sprite):
        seen = super().walk_script(start, mid, name, event, local_id, sprite)
        selections = sorted(pc for pc in seen if pc in self.selections)
        for pc in seen:
            for trainer in self.selections.get(pc, ()):
                self.battles[trainer].add(mid)
            if self.d[pc] == TRAINERBATTLE and self.d[pc + 1] in self.tb:
                typ = self.d[pc + 1]
                spec = self.roles.get(typ, {"opponent": [2]})
                operands = [(role, int.from_bytes(self.d[pc + at:pc + at + 2], "little"))
                            for role in ROLES for at in spec.get(role, [])]
                for role, trainer in operands:
                    self.battles[trainer].add(mid)
                self.commands[pc] = [(role, trainer) for role, trainer in operands if trainer < VARIABLES]
                self.modes[pc] = typ
                # A variable operand belongs to the nearest preceding selection.
                before = [script for script in selections if script <= pc]
                if before and any(trainer >= VARIABLES for _, trainer in operands):
                    self.links[pc].add(before[-1])


def _runtime_key(name: str) -> tuple[int, ...]:
    return tuple(int(part) for part in name.split("."))


def battle_maps(data: bytes, profile: dict, difficulty: dict | None = None, groups: list[dict] | None = None,
                roles: dict | None = None, commands: dict | None = None,
                participants: list[dict] | None = None, resolutions: list[dict] | None = None,
                modes: dict | None = None) -> dict[int, dict[str, int]]:
    """trainer id -> {runtime map: map section} of the maps whose scripts battle it.

    `modes` receives each reached trainerbattle's type and the selection
    scripts its variable operands come from."""
    for row in resolutions or []:
        for proof in row["rom"]:
            at, size = proof["offset"], proof["size"]
            if at + size > len(data) or hashlib.sha256(data[at:at + size]).hexdigest() != proof["sha256"]:
                raise ValueError(f"trainer maps: ROM span differs at {at:#x}")
    walker = _BattleWalker(Rom(data), profile, difficulty, groups, roles, participants, resolutions)
    walker.walk_all()
    walker.walk_structural()
    for row in resolutions or []:
        if row["script"] not in walker.decoded:
            raise ValueError(f"trainer maps: unreachable resolution at {row['script']:#x}")
    for group in participants or []:
        if group["script"] not in walker.decoded or any(pc not in walker.commands for pc in group["commands"]):
            raise ValueError(f"trainer participants: unreachable battle at {group['script']:#x}")
    if commands is not None:
        commands.update(walker.commands)
    if modes is not None:
        modes.update({pc: (mode, sorted(walker.links.get(pc, ()))) for pc, mode in walker.modes.items()})
    engine = walker.engine_map_ids()
    out: dict[int, dict[str, int]] = {}
    for trainer, mids in walker.battles.items():
        if trainer >= VARIABLES:
            continue
        for mid in mids:
            header = walker.ctx.headers[mid]
            names = engine.get(header, []) if engine is not None else [mid] if RUNTIME_MAP.fullmatch(mid) else []
            for name in names:
                out.setdefault(trainer, {})[name] = data[header + 20]
    return out


def annotate_maps(trainers: list[dict], maps: dict[int, dict[str, int]] | None, training: dict | None) -> None:
    """Keep all maps and sections in numeric map order."""
    declared = set((training or {}).get("maps", []))
    if maps is not None:
        missing = sorted(declared - {name for rows in maps.values() for name in rows}, key=_runtime_key)
        if missing:
            raise ValueError(f"ev_training maps {', '.join(missing)}: no trainerbattle in their scripts")
    for trainer in trainers:
        rows = (maps or {}).get(trainer["id"], {})
        trainer["map"] = sorted(rows, key=_runtime_key)
        trainer["map_section"] = [rows[name] for name in trainer["map"]]
        trainer["ev_training"] = bool(declared & rows.keys())


def annotate_kinds(trainers: list[dict], classes: list[int], maps: dict[int, dict[str, int]] | None,
                   kinds: dict | None, selected: set[int] = frozenset()) -> None:
    """Assign one kind, retaining league and story roles across locations."""
    facts = kinds or {}
    facilities = set(facts.get("special_maps", []))
    if maps is not None:
        missing = sorted(facilities - {name for rows in maps.values() for name in rows}, key=_runtime_key)
        if missing:
            raise ValueError(f"kinds special_maps {', '.join(missing)}: no trainerbattle in their scripts")
    for trainer, class_id in zip(trainers, classes):
        if class_id in facts.get("league_classes", []):
            kind = "league"
        elif trainer["id"] in facts.get("partner_records", []):
            kind = "partners"
        elif class_id in facts.get("story_classes", []) or trainer["id"] in facts.get("story_records", []) or trainer["id"] in selected:
            kind = "story"
        elif trainer["ev_training"] or facilities.intersection((maps or {}).get(trainer["id"], {})):
            kind = "special"
        elif class_id in facts.get("paired_classes", []):
            kind = "paired"
        else:
            kind = "world"
        trainer["kind"] = kind


def build(block: dict, data: bytes, species_count: int, item_count: int, move_count: int,
          profile: dict | None = None, return_encounters: bool = False):
    """Extract trainer headers, stored members and declared classifications."""
    problems = check(block)
    if problems:
        raise ValueError("invalid trainer configuration: " + ", ".join(problems))
    if "maps" in block and not (profile or {}).get("maps"):
        raise ValueError("trainer maps: the profile carries no [maps] block")
    fields, party = block["fields"], block["party"]
    custom, held = int(party["custom_moves_flag"]), int(party["held_item_flag"])
    out = []
    class_ids = []
    for trainer_id in range(int(block["count"])):
        header = int(block["base"]) + trainer_id * int(block["stride"])
        flags = value_at(data, header + int(fields["party_flags"]), 1)
        class_id = value_at(data, header + int(fields["class"]), 1)
        ai_flags = value_at(data, header + int(fields["ai_flags"]), 4)
        size = value_at(data, header + int(fields["party_size"]), 1)
        if flags is None or class_id is None or ai_flags is None or size is None:
            raise ValueError(f"trainer {trainer_id}: header exceeds ROM")
        if flags & ~(custom | held):
            raise ValueError(f"trainer {trainer_id}: unknown party flags {flags}")
        if size > 6:
            raise ValueError(f"trainer {trainer_id}: invalid party size {size}")
        class_ids.append(class_id)
        class_name = f"{block['class_table']}:{class_id}"
        if "class_names" in block:
            classes = block["class_names"]
            if class_id >= int(classes["count"]):
                raise ValueError(f"trainer {trainer_id}: invalid class {class_id}")
            name_pointer = value_at(data, int(classes["base"]) + class_id * int(classes["stride"])
                                    + int(classes["pointer"]), 4)
            if name_pointer is not None and ROM_BASE <= name_pointer < ROM_BASE + len(data):
                class_name = f"s{name_pointer - ROM_BASE:07X}"
        pointer = pointer_at(data, header + int(fields["party"])) if size else None
        if size and pointer is None:
            raise ValueError(f"trainer {trainer_id}: invalid party pointer")
        stride = int(party["custom_stride"] if flags & custom else party["default_stride"])
        members = []
        for index in range(size):
            at = pointer + index * stride
            iv = value_at(data, at + int(party["iv"]), 2)
            level = value_at(data, at + int(party["level"]), 2)
            species = value_at(data, at + int(party["species"]), 2)
            item = value_at(data, at + int(party["held_item"]), 2) if flags & held else None
            move_at = int(party["moves_with_item"] if flags & held else party["moves"])
            moves = [value_at(data, at + move_at + 2 * i, 2) for i in range(int(party["move_count"]))] \
                if flags & custom else None
            if iv is None or level is None or species is None or flags & held and item is None \
                    or moves is not None and any(move is None for move in moves):
                raise ValueError(f"trainer {trainer_id} member {index}: party exceeds ROM")
            if species == 0:
                if iv or level or item or any(moves or []):
                    raise ValueError(f"trainer {trainer_id} member {index}: invalid empty member")
            else:
                if not 0 < species < species_count:
                    raise ValueError(f"trainer {trainer_id} member {index}: invalid species {species}")
                if not 0 < level <= 100:
                    raise ValueError(f"trainer {trainer_id} member {index}: invalid level {level}")
                if item is not None and not 0 <= item < item_count:
                    raise ValueError(f"trainer {trainer_id} member {index}: invalid item {item}")
                if moves is not None and any(move >= move_count for move in moves):
                    raise ValueError(f"trainer {trainer_id} member {index}: invalid custom move")
            members.append({"index": index, "offset": f"0x{at:06x}", "species": species,
                            "level": level, "iv": iv, "item": item, "moves": moves})
        levels = [member["level"] for member in members if member["species"]]
        out.append({
            "id": trainer_id,
            "offset": f"0x{header:06x}",
            "name": f"{block['name_table']}:{trainer_id}",
            "class": class_name,
            "class_id": class_id,
            "ai_flags": ai_flags,
            "party_flags": flags,
            "party_size": size,
            "party_offset": f"0x{pointer:06x}" if pointer is not None else None,
            "party": members,
            "average_level": sum(levels) / len(levels) if levels else None,
        })
        if pictures := block.get("pictures"):
            picture = value_at(data, header + int(fields["picture"]), 1)
            if picture is None or picture >= int(pictures["front"]["count"]):
                raise ValueError(f"trainer {trainer_id}: invalid picture {picture}")
            out[-1].update(picture=picture, sprite=trainer_file(picture))
    participants = block.get("participants", [])
    validate_participants(participants, data)
    validate_order(block.get("order"), data)
    commands: dict[int, list[tuple[str, int]]] = {}
    modes: dict[int, tuple[int, list[int]]] | None = {} if "maps" in block else None
    maps = battle_maps(data, profile, block.get("difficulty"), block.get("encounters"),
                       (block.get("maps") or {}).get("roles"), commands, participants,
                       (block.get("maps") or {}).get("resolutions"), modes) if "maps" in block else None
    annotate_maps(out, maps, block.get("ev_training"))
    selection_scripts = {row["script"] for row in block.get("difficulty", {}).get("selections", [])}
    selected = selected_records(block.get("difficulty")) | {
        record for group in participants if group["script"] in selection_scripts
        for row in group["selectors"] for record in row["records"]}
    annotate_kinds(out, class_ids, maps, block.get("kinds"), selected)
    battles = encounters(out, block.get("difficulty"), block.get("encounters"), commands, participants,
                         block.get("order"), modes)
    if return_encounters:
        return out, battles
    return out
