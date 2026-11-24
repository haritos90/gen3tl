"""Resolve trainer-party IVs from declared ROM-matched operations."""

from __future__ import annotations

import hashlib
import itertools
import re
from pathlib import Path

from ..core.lists import append_unique
from ..core.notes import has_prose
from ..core.rom import ROM_BASE
from ..core.thumb import decode

FORMAT = "gen3tl-pokedex-trainer-ivs"
SCHEMA = 1
DOC_SCHEMA = "4.0.0"
FILENAME = "trainer-ivs.json"
OPERATIONS = ("base_uniform", "rival_spread", "spread_uniform", "speed_zero", "packed_masks")
SPECIES_RULE_KINDS = ("trainer_level", "held_item_form", "minior_core", "scaled_evolution")
SPECIES_STAGES = ("create", "held_item", "adjustment", "final")
EVOLUTION_CLASSES = ("level", "other", "item", "friendship")
SCALED_LABELS = ("range", "badges", "badges_range", "hall", "hall_range", "from")
SHA256 = re.compile(r"[0-9a-f]{64}")


class TrainerIvError(ValueError):
    """The declared IV pipeline cannot produce trustworthy data."""


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _offsets(value):
    return isinstance(value, list) and all(_integer(item) for item in value)


def _ints(value, minimum=0):
    return isinstance(value, list) and bool(value) and all(_integer(item, minimum) for item in value)


def _proofs(value):
    return isinstance(value, list) and bool(value) and all(
        isinstance(proof, dict) and _integer(proof.get("offset")) and _integer(proof.get("size"), 1)
        and SHA256.fullmatch(proof.get("sha256", "")) for proof in value)


def _span(value, size_key):
    return isinstance(value, dict) and _integer(value.get("offset")) and _integer(value.get(size_key), 1)


def check(block: dict | None) -> list[str]:
    """Validate declarations without reading a ROM."""
    if not isinstance(block, dict):
        return ["block"]
    problems = []
    if block.get("schema") != SCHEMA:
        problems.append("schema")
    if not SHA256.fullmatch(block.get("rom_sha256", "")):
        problems.append("rom_sha256")
    if block.get("missing_input") != "unresolved":
        problems.append("missing_input")
    if not has_prose(block.get("evidence", "")):
        problems.append("evidence")
    if block.get("stat_order") != ["hp", "attack", "defense", "speed", "sp_attack", "sp_defense"]:
        problems.append("stat_order")
    difficulties = block.get("difficulties")
    if not isinstance(difficulties, list) or not difficulties or any(not isinstance(name, str) or not name for name in difficulties):
        problems.append("difficulties")
    for group in ("tables", "spans"):
        rows = block.get(group)
        if not isinstance(rows, list) or not rows:
            problems.append(group)
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                problems.append(f"{group}[{index}]")
                continue
            for key in ("name", "offset", "sha256"):
                if key not in row:
                    problems.append(f"{group}[{index}] {key}")
            if group == "tables":
                if not _integer(row.get("stride"), 1) or not _integer(row.get("count"), 1):
                    problems.append(f"tables[{index}] dimensions")
            elif not _integer(row.get("size"), 1):
                problems.append(f"spans[{index}] size")
            if not SHA256.fullmatch(row.get("sha256", "")):
                problems.append(f"{group}[{index}] sha256")
    stages = block.get("stages")
    if not isinstance(stages, list) or [stage.get("operation") for stage in stages] != list(OPERATIONS):
        problems.append("stages: ordered supported operations")
        stages = []
    table_names = {row.get("name") for row in block.get("tables", []) if isinstance(row, dict)}
    if "trainer_records" not in table_names:
        problems.append("tables trainer_records")
    for stage in stages:
        for key in ("class_table", "spread_table"):
            if key in stage and stage[key] not in table_names:
                problems.append(f"{stage['operation']} unknown table {stage[key]}")
    for index, stage in enumerate(stages):
        operation = stage["operation"]
        for key in ("condition_offsets", "operation_offsets"):
            if not _offsets(stage.get(key)):
                problems.append(f"stages[{index}] {key}")
        if operation == "base_uniform":
            for key in ("class_table", "opponent_side", "insane_difficulty", "cap", "zero"):
                if key not in stage:
                    problems.append(f"base_uniform {key}")
        elif operation == "rival_spread":
            if not isinstance(stage.get("class_rules"), list) or not _integer(stage.get("default_spread")):
                problems.append("rival_spread class_rules/default_spread")
            for rule in stage.get("rules", []):
                if not rule.get("species") or not rule.get("spreads") or not _offsets(rule.get("condition_offsets")):
                    problems.append("rival_spread rule")
                if "flag" in rule and len(rule.get("spreads", [])) != 2:
                    problems.append("rival_spread conditional alternatives")
                if ("flag_difficulties" in rule or "flag_ai_above" in rule) and (
                        "flag" not in rule or not isinstance(rule.get("flag_difficulties"), list)
                        or not _integer(rule.get("flag_ai_above"))):
                    problems.append("rival_spread flag scope")
            for table in stage.get("move_tables", []):
                if not SHA256.fullmatch(table.get("sha256", "")) or not _integer(table.get("offset")):
                    problems.append("rival_spread move table identity")
        elif operation == "spread_uniform":
            for key in ("spread_table", "first_spread", "last_spread", "iv_offset", "vanilla_difficulty", "cap"):
                if key not in stage:
                    problems.append(f"spread_uniform {key}")
        elif operation == "speed_zero":
            for key in ("moves", "stat_index", "value", "first_spread", "last_spread"):
                if key not in stage:
                    problems.append(f"speed_zero {key}")
        elif operation == "packed_masks":
            for key in ("first_spread", "last_spread", "eligibility_offsets"):
                if key not in stage or key == "eligibility_offsets" and not _offsets(stage[key]):
                    problems.append(f"packed_masks {key}")
            ids = set()
            for rule in stage.get("rules", []):
                if not isinstance(rule.get("id"), str) or rule["id"] in ids:
                    problems.append("packed_masks unique id")
                ids.add(rule.get("id"))
                if not _integer(rule.get("trainer")) or not rule.get("species") or not rule.get("difficulties"):
                    problems.append(f"packed_masks {rule.get('id')} context")
                if not _integer(rule.get("packed"), 1) or rule["packed"] > 0x3FFFFFFF:
                    problems.append(f"packed_masks {rule.get('id')} packed")
                if not _offsets(rule.get("condition_offsets")) or not _offsets(rule.get("operation_offsets")):
                    problems.append(f"packed_masks {rule.get('id')} provenance")
    variants = block.get("species_variants", [])
    if not isinstance(variants, list):
        problems.append("species_variants")
    else:
        seen = set()
        for variant in variants:
            key = (variant.get("trainer"), variant.get("slot"))
            if not all(_integer(value) for value in key) or key in seen or not variant.get("species"):
                problems.append("species_variants member")
            if not _offsets(variant.get("evidence_offsets")):
                problems.append("species_variants evidence_offsets")
            seen.add(key)
    problems.extend(check_species_conditions(block))
    problems.extend(check_species_rules(block))
    problems.extend(check_write_rules(block))
    return problems


def check_species_conditions(block: dict) -> list[str]:
    """Require complete, disjoint species choices with verifiable causes."""
    problems, conditions = [], {}
    rows = block.get("species_conditions", [])
    if not isinstance(rows, list):
        return ["species_conditions"]
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
            problems.append("species condition identity")
            continue
        name = row["id"]
        if name in conditions:
            problems.append("species condition duplicate id")
        conditions[name] = row
        if (row.get("status") not in ("known", "unknown") or not row.get("label") or not has_prose(row.get("evidence"))
                or row.get("native") not in ("confirmed", "unconfirmed")
                or row.get("status") == "unknown" and not row.get("searched")):
            problems.append(f"species condition {name}: evidence")
        choices = row.get("when")
        if not isinstance(choices, list) or not choices or any(
                not isinstance(choice, dict) or not choice or any(
                    not isinstance(flag, str) or not re.fullmatch(r"0x[0-9A-F]+", flag)
                    or type(value) is not int or value not in (0, 1)
                    for flag, value in choice.items()) for choice in choices):
            problems.append(f"species condition {name}: flag predicates")
        if not _proofs(row.get("rom")):
            problems.append(f"species condition {name}: ROM proofs")
    if problems:
        return problems
    variants = block.get("species_variants", [])
    if not isinstance(variants, list):
        return ["species_variants"]
    for variant in variants:
        outcomes = variant.get("outcomes")
        if not isinstance(outcomes, list) or not outcomes or any(
                not isinstance(row, dict) or row.get("condition") not in conditions
                or not _integer(row.get("species"), 1) for row in outcomes):
            problems.append("species variant outcomes")
            continue
        if sorted(row["species"] for row in outcomes) != sorted(variant["species"]):
            problems.append("species variant coverage")
        predicates = [conditions[row["condition"]]["when"] for row in outcomes]
        flags = sorted({flag for choices in predicates for choice in choices for flag in choice})
        for values in itertools.product((0, 1), repeat=len(flags)):
            state = dict(zip(flags, values))
            matches = sum(any(all(state[flag] == value for flag, value in choice.items())
                              for choice in choices) for choices in predicates)
            if matches != 1:
                problems.append("species variant predicates: complete disjoint coverage required")
                break
    return problems


def _check_form(form) -> bool:
    if not isinstance(form, dict) or not _ints(form.get("species"), 1) or not _integer(form.get("hold_effect")):
        return False
    sources = [key for key in ("target", "table", "params") if key in form]
    if len(sources) != 1:
        return False
    if "target" in form and not _integer(form["target"], 1) or "table" in form and not _span(form["table"], "count"):
        return False
    if "params" in form and (not isinstance(form["params"], list) or not form["params"] or any(
            not isinstance(pair, list) or len(pair) != 2 or not _integer(pair[0]) or not _integer(pair[1], 1)
            for pair in form["params"])):
        return False
    if "otherwise" in form and not _integer(form["otherwise"], 1) or "ability" in form and not _integer(form["ability"]):
        return False
    return all(_ints(form[key]) for key in ("keep_sections", "unsupported_effects") if key in form) \
        and ("keep_sections" not in form or "otherwise" in form)


def _check_writes(change: dict) -> bool:
    """The held item and move slots a trainer branch also writes."""
    moves = change.get("moves", [])
    return ("item" not in change or _integer(change["item"], 1)) and isinstance(moves, list) and all(
        isinstance(row, dict) and _integer(row.get("slot")) and row["slot"] < 4 and _integer(row.get("move"), 1)
        and ("level_above" not in row or _integer(row["level_above"], 1)) for row in moves)


def stage_species(stage: str, source: int, built: int) -> tuple[int, int, int]:
    """Species read by the spread and adjustment steps, the EV grant and the default moves."""
    if stage == "create":
        return built, built, built
    if stage == "held_item":
        return built, built, source
    if stage == "adjustment":
        return source, built, source
    return source, source, source


MINIOR_SHARED = ("species", "cores", "spreads", "ability_offset", "ability_max")


def _check_scaled(row: dict, count: int) -> bool:
    evolutions, methods, levels = row.get("evolutions"), row.get("methods"), row.get("levels")
    labels, battles = row.get("labels"), row.get("battles", [])
    return (_ints(row.get("modes")) and isinstance(evolutions, dict)
            and all(_integer(evolutions.get(key), 1) for key in ("offset", "stride", "entries"))
            and _integer(evolutions.get("pointer"))
            and isinstance(methods, dict) and set(methods) == set(EVOLUTION_CLASSES)
            and all(_ints(methods[key]) for key in EVOLUTION_CLASSES)
            and isinstance(levels, dict) and set(levels) == set(EVOLUTION_CLASSES[1:])
            and all(_integer(levels[key], 1) for key in levels)
            and _ints(row.get("boss_classes")) and _integer(row.get("custom_moves_flag"), 1)
            and _ints(row.get("party_level_penalties")) and len(row["party_level_penalties"]) == 6
            and _integer(row.get("raised_level_penalty"))
            and isinstance(row.get("keep_items"), list) and all(_integer(value, 1) for value in row["keep_items"])
            and all(isinstance(row.get(key), list) and all(_integer(value) for value in row[key])
                    for key in ("raise_difficulties", "unscaled_difficulties", "unscaled_sections", "unscaled_trainers"))
            and all(value < count for key in ("raise_difficulties", "unscaled_difficulties") for value in row[key])
            and _span(row.get("badge_levels"), "count") and _integer(row["badge_levels"].get("stride"), 1)
            and _integer(row.get("hall_badges"), 1)
            and isinstance(labels, dict) and all(isinstance(labels.get(key), str) and labels[key] for key in SCALED_LABELS)
            and isinstance(battles, list) and all(
                isinstance(battle, dict) and isinstance(battle.get("id"), str) and battle.get("label")
                and has_prose(battle.get("evidence")) and (("modes" in battle) != ("command" in battle))
                and ("modes" not in battle or _ints(battle["modes"]))
                and ("command" not in battle or _integer(battle["command"]) and _integer(battle.get("party_level"), 1))
                for battle in battles)
            and len({battle["id"] for battle in battles}) == len(battles))


def check_species_rules(block: dict) -> list[str]:
    """Require each built-species rule to name its inputs and ROM proofs."""
    problems, ids, minior = [], set(), {}
    rows = block.get("species_rules", [])
    if not isinstance(rows, list):
        return ["species_rules"]
    count = len(block.get("difficulties") or [])
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"] or row["id"] in ids:
            problems.append("species rule identity")
            continue
        name = row["id"]
        ids.add(name)
        kind = row.get("kind")
        if (kind not in SPECIES_RULE_KINDS or row.get("status") not in ("known", "unknown") or not row.get("label")
                or not has_prose(row.get("evidence")) or row.get("native") not in ("confirmed", "unconfirmed")
                or row.get("status") == "unknown" and not row.get("searched")
                or row.get("stage") not in SPECIES_STAGES):
            problems.append(f"species rule {name}: evidence")
        if not _proofs(row.get("rom")):
            problems.append(f"species rule {name}: ROM proofs")
        spreads = row.get("spreads")
        gate = isinstance(spreads, list) and len(spreads) == 2 and all(_integer(value) for value in spreads) \
            and spreads[0] <= spreads[1]
        if kind == "trainer_level":
            changes = row.get("changes")
            if (not _integer(row.get("trainer")) or not _ints(row.get("difficulties"))
                    or any(value >= count for value in row["difficulties"]) or not gate
                    or "opponent" in row and not _integer(row["opponent"])
                    or not isinstance(changes, list) or not changes or any(
                        not isinstance(change, dict) or not all(_integer(change.get(key), 1)
                                                                for key in ("species", "target", "level_above"))
                        for change in changes)
                    or len({change["species"] for change in changes}) != len(changes)
                    or not all(_check_writes(change) for change in changes)):
                problems.append(f"species rule {name}: trainer changes")
        elif kind == "held_item_form":
            forms = row.get("forms")
            if not _integer(row.get("item_flag"), 1) or not isinstance(forms, list) or not forms \
                    or not all(_check_form(form) for form in forms):
                problems.append(f"species rule {name}: forms")
            elif len({species for form in forms for species in form["species"]}) != sum(
                    len(form["species"]) for form in forms):
                problems.append(f"species rule {name}: one form entry per species")
        elif kind == "minior_core":
            personality = row.get("personality")
            fixed = personality == "fixed"
            if (personality not in ("random", "fixed") or personality in minior or not _ints(row.get("species"), 1)
                    or not _span(row.get("cores"), "count") or not gate
                    or not _integer(row.get("ability_offset")) or not _integer(row.get("ability_max"))
                    or fixed and (not _span(row.get("names"), "stride") or not _span(row.get("trainer_name"), "size")
                                  or not _span(row.get("gender"), "shift") or not _ints(row.get("gender_classes"))
                                  or not isinstance(row.get("values"), dict)
                                  or not all(_integer(row["values"].get(key)) for key in ("male", "female")))):
                problems.append(f"species rule {name}: core inputs")
            else:
                minior[personality] = row
        elif kind == "scaled_evolution" and not _check_scaled(row, count):
            problems.append(f"species rule {name}: scaled evolution inputs")
    if minior and (len(minior) != 2 or any(minior["random"][key] != minior["fixed"][key] for key in MINIOR_SHARED)):
        problems.append("species rules: random and fixed cores share their inputs")
    return problems


def check_write_rules(block: dict) -> list[str]:
    """Require bounded writes and explicit conditions with ROM evidence."""
    rows = block.get("write_rules", [])
    if not isinstance(rows, list):
        return ["write_rules"]
    problems, seen = [], set()
    for row in rows:
        name = row.get("id") if isinstance(row, dict) else None
        if not isinstance(name, str) or not name or name in seen:
            problems.append("write rule identity")
            continue
        seen.add(name)
        if (row.get("stage") not in ("selector", "adjustment")
                or row.get("status") not in ("known", "unknown")
                or row.get("native") not in ("confirmed", "unconfirmed")
                or not has_prose(row.get("evidence")) or not _proofs(row.get("rom"))):
            problems.append(f"write rule {name}: evidence")
        if row.get("status") == "unknown":
            members = row.get("members")
            if (not row.get("searched") or not isinstance(members, list) or not members
                    or any(not isinstance(member, dict) or not _integer(member.get("trainer"))
                           or not _integer(member.get("slot")) or member["slot"] > 5
                           or not member.get("fields") or not set(member["fields"]) <= {"moves", "item"}
                           for member in members)
                    or "moves" in row or "item" in row):
                problems.append(f"write rule {name}: unresolved members")
            continue
        when = row.get("when", {})
        if not isinstance(when, dict):
            problems.append(f"write rule {name}: condition")
            continue
        allowed = {"trainers", "classes", "species", "difficulties", "spreads", "ai_min",
                   "level_min", "level_max", "flags"}
        if (set(when) - allowed or not _ints(when.get("species"), 1)
                or not _ints(when.get("difficulties"))
                or not set(when["difficulties"]) <= set(range(len(block["difficulties"])))):
            problems.append(f"write rule {name}: condition")
        for key in ("trainers", "classes"):
            if key in when and not _ints(when[key]):
                problems.append(f"write rule {name}: {key}")
        for key in ("level_min", "level_max", "ai_min"):
            if key in when and not _integer(when[key], 1):
                problems.append(f"write rule {name}: {key}")
        first, last = when.get("level_min", 1), when.get("level_max", 100)
        if not _integer(first, 1) or not _integer(last, 1) or not first <= last <= 100:
            problems.append(f"write rule {name}: level range")
        if "spreads" in when and (not _ints(when["spreads"], 1) or len(when["spreads"]) != 2
                                  or when["spreads"][0] > when["spreads"][1]):
            problems.append(f"write rule {name}: spreads")
        flags = when.get("flags", {})
        if not isinstance(flags, dict) or any(not isinstance(key, str) or not re.fullmatch(r"0x[0-9A-F]+", key)
                or type(value) is not int or value not in (0, 1) for key, value in flags.items()):
            problems.append(f"write rule {name}: flags")
        moves = row.get("moves", [])
        if ("item" not in row and not moves or "item" in row and not _integer(row["item"])
                or not isinstance(moves, list) or any(not isinstance(move, dict)
                    or not _integer(move.get("move"), 1)
                    or ("slot" in move) == ("from_move" in move)
                    or "slot" in move and (not _integer(move["slot"]) or move["slot"] > 3)
                    or "from_move" in move and not _integer(move["from_move"], 1) for move in moves)):
            problems.append(f"write rule {name}: writes")
    return problems


def apply_moves(moves: list[int], writes: list[dict], level: int) -> list[int]:
    """Apply ordered slot writes or first-match substitutions."""
    result = list(moves)
    for write in writes:
        if level <= write.get("level_above", 0):
            continue
        if "from_move" in write:
            if write["from_move"] not in result:
                continue
            slot = result.index(write["from_move"])
        else:
            slot = write["slot"]
        result += [0] * (slot + 1 - len(result))
        result[slot] = write["move"]
    return result


class PartyWrites:
    """Compile conditional item and move writes for each member."""

    def __init__(self, block: dict, resolver: Resolver):
        self.resolver = resolver
        self.rules = sorted((row for row in block.get("write_rules", []) if row["status"] == "known"),
                            key=lambda row: ("selector", "adjustment").index(row["stage"]))

    def changes(self, trainer: dict, member: dict, species: int, baseline,
                levels: range, days: list[int], species_write: dict | None = None) -> list[dict]:
        """Emit only differing values, under disjoint level and flag conditions."""
        rules = [row for row in self.rules if species in row["when"]["species"]
                 and trainer["id"] in row["when"].get("trainers", [trainer["id"]])
                 and trainer["class_id"] in row["when"].get("classes", [trainer["class_id"]])
                 and trainer["ai_flags"] >= row["when"].get("ai_min", 0)]
        if not rules:
            return []
        flags = sorted({key for row in rules for key in row["when"].get("flags", {})})
        domains = {key: frozenset((0, 1)) for key in flags}
        found = []
        for day in days:
            for values in itertools.product((0, 1), repeat=len(flags)):
                state = dict(zip(flags, values))
                progress = {int(key, 16): value for key, value in state.items()}
                spreads = self.resolver.spread_options(trainer, member, species, day, progress)
                bands = []
                for level in levels:
                    moves, item = baseline(level)
                    original = (list(moves), item)
                    applied = []
                    for row in rules:
                        when = row["when"]
                        if (day not in when["difficulties"]
                                or not when.get("level_min", 1) <= level <= when.get("level_max", 100)
                                or any(state[key] != value for key, value in when.get("flags", {}).items())):
                            continue
                        if "spreads" in when and (not spreads or not all(
                                when["spreads"][0] <= spread <= when["spreads"][1] for spread, _ in spreads)):
                            continue
                        moves = apply_moves(moves, row.get("moves", []), level)
                        item = row.get("item", item)
                        applied.append(row["id"])
                    if species_write:
                        moves = apply_moves(moves, species_write.get("moves", []), level)
                        item = species_write.get("item", item)
                    if (moves, item) == original:
                        continue
                    value = {"moves": moves, "item": item}
                    if bands and bands[-1]["last"] == level - 1 and all(bands[-1][key] == value[key] for key in value):
                        bands[-1]["last"] = level
                        bands[-1]["rules"] = sorted(set(bands[-1]["rules"]) | set(applied))
                    else:
                        bands.append({"level": level, "last": level, **value, "rules": sorted(applied)})
                found.extend((band, day, state) for band in bands)
        groups = {}
        for band, day, state in found:
            key = (band["level"], band["last"], tuple(band["moves"]), band["item"], tuple(band["rules"]))
            groups.setdefault(key, []).append((day, {key: [value] for key, value in state.items()}))
        result = []
        for (first, last, moves, item, rules), cubes in groups.items():
            merged = _merge(cubes, domains)
            conditions = {}
            for day, cube in merged:
                when = tuple((key, tuple(sorted(values))) for key, values in sorted(cube.items()))
                conditions.setdefault(when, []).append(day)
            for when, days in conditions.items():
                result.append({"level": first, **({"last": last} if last < 100 else {}),
                               "difficulties": sorted(set(days)),
                               "when": {key: list(values) for key, values in when},
                               "moves": list(moves), "item": item, "rules": list(rules)})
        return sorted(result, key=lambda row: (row["difficulties"], row["level"], str(row["when"])))


def unpack(packed: int) -> list[int]:
    """Decode six five-bit IVs in stat order."""
    return [(packed >> (5 * index)) & 31 for index in range(6)]


def default_moves(learnsets: dict, species: int, level: int) -> list[int] | None:
    """Return distinct moves from the last four ROM entries."""
    rows = learnsets.get(species)
    if rows is None:
        return None
    eligible = [row for row in rows if row.get("method") == 0 and (row.get("level") or 0) <= level]
    moves = []
    for row in eligible[-4:]:
        append_unique(moves, row["move"])
    return moves


def default_move_bands(learnsets: dict, species: int, minimum: int = 1, maximum: int = 100) -> list[dict]:
    """Group default moves at each built level into contiguous bands."""
    bands = []
    for level in range(minimum, maximum + 1):
        moves = default_moves(learnsets, species, level)
        if moves is None:
            raise TrainerIvError(f"species {species}: default moves unavailable")
        if bands and bands[-1]["moves"] == moves:
            bands[-1]["last"] = level
        else:
            bands.append({"level": level, "last": level, "moves": moves})
    if maximum == 100:
        del bands[-1]["last"]
    return bands


def _walk_local(data: bytes, start: int, end: int):
    todo = [start]
    seen = set()
    while todo:
        at = todo.pop()
        if at in seen or not start <= at < end:
            continue
        seen.add(at)
        instruction = decode(data, at)
        yield instruction
        following = at + instruction.size
        if instruction.mnemonic == "bl" and instruction.target is not None and start <= instruction.target < end:
            todo.append(instruction.target)
            todo.append(following)
        else:
            todo.extend(instruction.successors)


def _slice(data: bytes, offset: int, size: int, label: str) -> bytes:
    if offset < 0 or size < 0 or offset + size > len(data):
        raise TrainerIvError(f"{label}: ROM span outside input")
    return data[offset:offset + size]


def validate_rom(block: dict, data: bytes) -> None:
    """Check the exact ROM and every declared table and span."""
    problems = check(block)
    if problems:
        raise TrainerIvError("invalid IV declarations: " + ", ".join(problems))
    if _digest(data) != block["rom_sha256"]:
        raise TrainerIvError("unsupported ROM SHA-256")
    for table in block["tables"]:
        size = table["stride"] * table["count"]
        raw = _slice(data, table["offset"], size, table["name"])
        if _digest(raw) != table["sha256"]:
            raise TrainerIvError(f"{table['name']}: table hash mismatch")
    for span in block["spans"]:
        raw = _slice(data, span["offset"], span["size"], span["name"])
        if _digest(raw) != span["sha256"]:
            raise TrainerIvError(f"{span['name']}: code hash mismatch")
    for condition in block.get("species_conditions", []):
        for proof in condition["rom"]:
            raw = _slice(data, proof["offset"], proof["size"], condition["id"])
            if _digest(raw) != proof["sha256"]:
                raise TrainerIvError(f"{condition['id']}: species condition hash mismatch")
    for rule in block.get("species_rules", []):
        for proof in rule["rom"]:
            if _digest(_slice(data, proof["offset"], proof["size"], rule["id"])) != proof["sha256"]:
                raise TrainerIvError(f"{rule['id']}: species rule hash mismatch")
    for rule in block.get("write_rules", []):
        for proof in rule["rom"]:
            if _digest(_slice(data, proof["offset"], proof["size"], rule["id"])) != proof["sha256"]:
                raise TrainerIvError(f"{rule['id']}: party write hash mismatch")
    audit = block.get("audit", {})
    fields = set(audit.get("iv_fields", []))
    excluded = set(audit.get("non_iv_candidates", []))
    for span in block["spans"]:
        if not span.get("audit"):
            continue
        start = span["offset"]
        found = {}
        for instruction in _walk_local(data, start, start + span["size"]):
            address = instruction.address + ROM_BASE
            if address in excluded:
                continue
            if instruction.mnemonic == "mov" and instruction.rd == 1 and instruction.immediate in fields:
                found.setdefault(instruction.immediate, []).append(address)
        expected = {row["field"]: sorted(row["offsets"]) for row in span.get("iv_writes", [])}
        if {field: sorted(addresses) for field, addresses in found.items()} != expected:
            raise TrainerIvError(f"{span['name']}: unaccounted IV write candidates")
    speed = next(stage for stage in block["stages"] if stage["operation"] == "speed_zero")
    forbidden = set(speed["moves"])
    rival = next(stage for stage in block["stages"] if stage["operation"] == "rival_spread")
    for table in rival.get("move_tables", []):
        raw = _slice(data, table["offset"], 2 * len(table["moves"]), table["name"])
        if _digest(raw) != table["sha256"]:
            raise TrainerIvError(f"{table['name']}: rival move hash mismatch")
        moves = [int.from_bytes(raw[index:index + 2], "little") for index in range(0, len(raw), 2)]
        if moves != table["moves"]:
            raise TrainerIvError(f"{table['name']}: rival move mismatch")
        if forbidden.intersection(moves):
            raise TrainerIvError(f"{table['name']}: unmodeled rival Speed override")


def read_tables(block: dict, data: bytes) -> tuple[list[int], list[list[int]]]:
    """Read class values and spread rows after ROM validation."""
    tables = {table["name"]: table for table in block["tables"]}
    class_table = tables["class_iv"]
    class_iv = list(_slice(data, class_table["offset"], class_table["stride"] * class_table["count"], "class_iv"))
    spread_table = tables["spreads"]
    raw = _slice(data, spread_table["offset"], spread_table["stride"] * spread_table["count"], "spreads")
    stride = spread_table["stride"]
    spreads = [list(raw[index:index + stride]) for index in range(0, len(raw), stride)]
    return class_iv, spreads


class Resolver:
    """Apply only operations present in the profile declaration."""

    def __init__(self, block: dict, class_iv: list[int], spreads: list[list[int]], learnsets: dict):
        problems = check(block)
        if problems:
            raise TrainerIvError("invalid IV declarations: " + ", ".join(problems))
        self.block = block
        self.class_iv = class_iv
        self.spreads = spreads
        self.learnsets = learnsets
        self.stages = {stage["operation"]: stage for stage in block["stages"]}
        self.trainer_count = next(table["count"] for table in block["tables"] if table["name"] == "trainer_records")

    def _base(self, trainer: dict, difficulty: int, side: int) -> list[int] | None:
        class_id = trainer.get("class_id")
        if not _integer(class_id) or class_id >= len(self.class_iv):
            return None
        stage = self.stages["base_uniform"]
        value = self.class_iv[class_id]
        if side == stage["opponent_side"] and difficulty >= stage["insane_difficulty"]:
            value = stage["cap"]
        value = min(value, stage["cap"]) or stage["zero"]
        return [value] * 6

    def spread_options(self, trainer: dict, member: dict, species: int, difficulty: int,
                        progress: dict | None) -> list[tuple[int, dict]] | None:
        raw_iv = member.get("iv")
        if not _integer(raw_iv) or raw_iv > 0xFFFF:
            return None
        stored = raw_iv & 0xFF
        stage = self.stages["rival_spread"]
        selected = any(rule.get("class_id") == trainer.get("class_id") and difficulty in rule.get("difficulties", [])
                       for rule in stage["class_rules"])
        if not selected:
            return [(stored, {})]
        matches = [rule for rule in stage.get("rules", []) if species in rule["species"]]
        if len(matches) > 1:
            raise TrainerIvError(f"overlapping rival spread rules for species {species}")
        if not matches:
            return [(stage["default_spread"], {})]
        rule = matches[0]
        spreads = rule["spreads"]
        scoped = "flag_difficulties" in rule and (
            difficulty not in rule["flag_difficulties"] or trainer.get("ai_flags", 0) <= rule["flag_ai_above"])
        if "flag" not in rule or scoped:
            return [(spreads[0], {})]
        flag = rule["flag"]
        if progress is not None and flag in progress:
            return [(spreads[1 if progress[flag] else 0], {flag: 1 if progress[flag] else 0})]
        return [(spread, {flag: int(bool(value))}) for spread, value in zip(spreads, (0, 1))]

    def _finish(self, ivs: list[int], spread: int, trainer: dict, species: int, difficulty: int,
                moves: list[int]) -> list[int]:
        spread_stage = self.stages["spread_uniform"]
        speed_stage = self.stages["speed_zero"]
        first = spread_stage["first_spread"]
        last = spread_stage["last_spread"]
        if first <= spread <= last:
            if spread >= len(self.spreads):
                raise TrainerIvError(f"spread {spread}: table row unavailable")
            value = self.spreads[spread][spread_stage["iv_offset"]]
            if difficulty != spread_stage["vanilla_difficulty"] and value:
                ivs = [min(value, spread_stage["cap"])] * 6
            if speed_stage["first_spread"] <= spread <= speed_stage["last_spread"] \
                    and forbidden_move(moves, speed_stage["moves"]):
                ivs[speed_stage["stat_index"]] = speed_stage["value"]
        mask_stage = self.stages["packed_masks"]
        eligible = mask_stage["first_spread"] <= spread <= mask_stage["last_spread"]
        matches = [rule for rule in mask_stage.get("rules", []) if eligible
                   and rule["trainer"] == trainer.get("id") and difficulty in rule["difficulties"]
                   and (rule["species"] == "any" or species in rule["species"])]
        if len(matches) > 1:
            raise TrainerIvError(f"overlapping packed masks for trainer {trainer.get('id')} species {species}")
        if matches:
            ivs = unpack(matches[0]["packed"])
        return ivs

    def resolve(self, trainer: dict, member: dict, *, difficulty: int | None, side: int | None,
                listed: bool = True, species: int | None = None, level: int | None = None,
                moves: list[int] | None = None, progress: dict | None = None,
                construction: str | None = None) -> dict:
        """Resolve one member or explicitly retain unresolved alternatives."""
        reasons = []
        trainer_id = trainer.get("id")
        if not _integer(trainer_id) or not 0 <= trainer_id < self.trainer_count:
            reasons.append("trainer")
        if not _integer(difficulty) or not 0 <= difficulty < len(self.block["difficulties"]):
            reasons.append("difficulty")
        if side not in (0, 1):
            reasons.append("side")
        if not listed and construction != "trainer-party":
            reasons.append("construction")
        if species is None:
            species = member.get("species") if listed else None
        if level is None:
            level = member.get("level") if listed else None
        if not _integer(species, 1):
            reasons.append("species")
        if not _integer(level, 1) or level > 100:
            reasons.append("level")
        if moves is None and listed and _integer(species, 1) and _integer(level, 1):
            moves = member.get("moves")
            if moves is None:
                moves = default_moves(self.learnsets, species, level)
        if not isinstance(moves, list) or any(not _integer(move) for move in moves):
            reasons.append("moves")
        base = self._base(trainer, difficulty, side) if not reasons else None
        if base is None and not reasons:
            reasons.append("class")
        options = self.spread_options(trainer, member, species, difficulty, progress) if not reasons else None
        if options is None and not reasons:
            reasons.append("spread")
        if reasons:
            return {"status": "unresolved", "reasons": sorted(set(reasons))}
        alternatives = {}
        for spread, condition in options:
            vector = tuple(self._finish(list(base), spread, trainer, species, difficulty, moves))
            key = tuple(sorted(condition.items()))
            alternatives.setdefault(vector, []).append(key)
        if len(alternatives) == 1:
            return {"status": "resolved", "ivs": list(next(iter(alternatives)))}
        rows = []
        for vector, conditions in alternatives.items():
            rows.append({"ivs": list(vector), "conditions": [
                {f"0x{flag:X}": value for flag, value in condition} for condition in conditions]})
        return {"status": "conditional", "alternatives": rows}


def forbidden_move(moves: list[int], forbidden: list[int]) -> bool:
    """Check a declared Speed-override move set."""
    return any(move in forbidden for move in moves)


STATS = ("hp", "attack", "defense", "speed", "sp_attack", "sp_defense")
EV_OPERATIONS = ("clear", "set", "even_hp")
OTHER = "other"   # a battle place outside every declared place


def _flag(value) -> str:
    return f"0x{value:X}"


def check_evs(block: dict | None) -> list[str]:
    """Validate the EV declarations without reading a ROM."""
    if not isinstance(block, dict):
        return ["block"]
    problems = []
    if block.get("schema") != 1:
        problems.append("schema")
    if not has_prose(block.get("evidence", "")):
        problems.append("evidence")
    if block.get("absent", "abilities") != "abilities":
        problems.append("absent: abilities")
    for group in ("tables", "spans"):
        rows = block.get(group)
        if not isinstance(rows, list) or not rows:
            problems.append(group)
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or not _integer(row.get("offset")) or not SHA256.fullmatch(row.get("sha256", "")):
                problems.append(f"{group}[{index}] identity")
            elif group == "tables" and (not _integer(row.get("stride"), 1) or not _integer(row.get("count"), 1)
                                        or not _integer(row.get("pointer"))):
                problems.append(f"tables[{index}] dimensions")
            elif group == "spans" and not _integer(row.get("size"), 1):
                problems.append(f"spans[{index}] size")
    names = {row.get("name") for row in block.get("tables", []) if isinstance(row, dict)}
    if not {"base_stats", "evolutions"} <= names:
        problems.append("tables base_stats/evolutions")
    spread = block.get("spread")
    if not isinstance(spread, dict) or not all(_integer(spread.get(key)) for key in ("first", "last", "offset")) \
            or not isinstance(spread.get("skip_difficulties"), list) or not _offsets(spread.get("operation_offsets")):
        problems.append("spread")
    ids = set()
    for rule in block.get("adjustments", []):
        name = rule.get("id")
        if not isinstance(name, str) or name in ids:
            problems.append("adjustments unique id")
        ids.add(name)
        if rule.get("operation") not in EV_OPERATIONS or not rule.get("difficulties") or not _offsets(rule.get("offsets")):
            problems.append(f"adjustment {name}")
        if rule.get("operation") == "set" and (not isinstance(rule.get("evs"), dict) or not rule["evs"]
                                               or any(key not in STATS or not _integer(value) or value > 255
                                                      for key, value in rule["evs"].items())):
            problems.append(f"adjustment {name} evs")
        if "species" in rule and "not_species" in rule:
            problems.append(f"adjustment {name} species")
        if ("flag" in rule) != ("flag_value" in rule):
            problems.append(f"adjustment {name} flag")
    grant = block.get("grant")
    if not isinstance(grant, dict):
        problems.append("grant")
        return problems
    for key in ("value", "rebalance_flag", "rebalance_total", "rebalance_cap", "evolution_stop"):
        if not _integer(grant.get(key)):
            problems.append(f"grant {key}")
    for key in ("rebalance_excluded", "evolution_ignored", "rules", "unreached_maps"):
        if not isinstance(grant.get(key), list):
            problems.append(f"grant {key}")
    for rule in grant.get("rules", []):
        if not _integer(rule.get("difficulty")) or not _offsets(rule.get("offsets")):
            problems.append("grant rule")
    places = block.get("places")
    if not isinstance(places, list) or any(not isinstance(row.get("map"), str) or not _integer(row.get("section"))
                                           for row in places):
        problems.append("places")
    for row in block.get("conditions", []):
        known = row.get("status", "known") == "known"
        labels = [row.get(key) for key in ("set", "clear")]
        if (not _integer(row.get("flag")) or not has_prose(row.get("evidence"))
                or row.get("status", "known") not in ("known", "unknown")
                or known and any(not isinstance(label, str) or not label.strip()
                                 or "{flag}" in label or re.search(r"0x[0-9a-f]+", label, re.I) for label in labels)
                or not known and any(label is not None for label in labels)):
            problems.append("conditions")
    return problems


def condition_labels(block: dict) -> list[dict]:
    """Keep unresolved predicates available without inventing card labels."""
    return [{"flag": _flag(row["flag"]), **{key: row[key] for key in ("set", "clear") if key in row}}
            for row in block.get("conditions", [])]


def absent_members(trainers: dict) -> list[dict]:
    """Members the adjustment routine removes, as the ability rules declare them."""
    if (trainers.get("evs") or {}).get("absent") != "abilities":
        return []
    return [rule for rule in (trainers.get("abilities") or {}).get("overrides", []) if rule.get("operation") == "absent"]


def validate_ev_rom(block: dict, data: bytes) -> None:
    """Check every EV table, its pointer literal and every code span."""
    problems = check_evs(block)
    if problems:
        raise TrainerIvError("invalid EV declarations: " + ", ".join(problems))
    for table in block["tables"]:
        raw = _slice(data, table["offset"], table["stride"] * table["count"], table["name"])
        if _digest(raw) != table["sha256"]:
            raise TrainerIvError(f"{table['name']}: table hash mismatch")
        literal = int.from_bytes(_slice(data, table["pointer"], 4, table["name"]), "little")
        if literal != ROM_BASE + table["offset"]:
            raise TrainerIvError(f"{table['name']}: pointer literal mismatch")
    for span in block["spans"]:
        if _digest(_slice(data, span["offset"], span["size"], span["name"])) != span["sha256"]:
            raise TrainerIvError(f"{span['name']}: code hash mismatch")


def read_ev_tables(block: dict, data: bytes) -> tuple[list[list[int]], list[list[int]]]:
    """Base stat bytes and evolution methods per species."""
    tables = {table["name"]: table for table in block["tables"]}
    stats = tables["base_stats"]
    base = [list(data[stats["offset"] + index * stats["stride"]:][:6]) for index in range(stats["count"])]
    evolutions = tables["evolutions"]
    methods = []
    for index in range(evolutions["count"]):
        at = evolutions["offset"] + index * evolutions["stride"]
        methods.append([int.from_bytes(data[at + row:at + row + 2], "little")
                        for row in range(0, evolutions["stride"], 8)])
    return base, methods


def _merge(cubes: list[tuple[object, dict]], domains: dict) -> list[tuple[object, dict]]:
    """Join conditions of equal outcomes that differ in one variable."""
    cubes = [(outcome, {name: frozenset(values) for name, values in cube.items()}) for outcome, cube in cubes]
    changed = True
    while changed:
        changed = False
        for i in range(len(cubes)):
            for j in range(i + 1, len(cubes)):
                (left, a), (right, b) = cubes[i], cubes[j]
                if left != right or set(a) != set(b):
                    continue
                differ = [name for name in a if a[name] != b[name]]
                if len(differ) != 1:
                    continue
                name = differ[0]
                joined = dict(a)
                joined[name] = a[name] | b[name]
                if joined[name] == domains[name]:
                    del joined[name]
                cubes[i] = (left, joined)
                del cubes[j]
                changed = True
                break
            if changed:
                break
    return cubes


class EvResolver:
    """Apply the declared EV writes in party-construction order."""

    def __init__(self, block: dict, resolver: Resolver, base: list[list[int]], evolutions: list[list[int]],
                 absent: list[dict] | None = None):
        problems = check_evs(block)
        if problems:
            raise TrainerIvError("invalid EV declarations: " + ", ".join(problems))
        self.block = block
        self.resolver = resolver
        self.base = base
        self.evolutions = evolutions
        self.absent = absent or []
        self.places = {row["map"]: row["section"] for row in block["places"]}
        grant = block["grant"]
        self.grant = {rule["difficulty"]: rule for rule in grant["rules"]}
        flags = {rule["flag"] for rule in block.get("adjustments", []) if "flag" in rule}
        flags |= {rule["flag"] for rule in grant["rules"] if "flag" in rule} | {grant["rebalance_flag"]}
        self.labels = {row["flag"] for row in block.get("conditions", [])}
        if not flags <= self.labels:
            raise TrainerIvError("EV conditions: every flag needs a declaration")

    def _can_evolve(self, species: int) -> bool:
        grant = self.block["grant"]
        for method in self.evolutions[species]:
            if method == grant["evolution_stop"]:
                return False
            if method not in grant["evolution_ignored"]:
                return True
        return False

    def ranked(self, species: int, stat: int, rebalanced: bool) -> int:
        """The base stat the automatic training compares."""
        grant = self.block["grant"]
        row = self.base[species]
        if stat == 0 or not rebalanced or species in grant["rebalance_excluded"] or self._can_evolve(species):
            return row[stat]
        return min(row[stat] * (grant["rebalance_total"] - row[0]) // (sum(row) - row[0]), grant["rebalance_cap"])

    def top_two(self, species: int, rebalanced: bool) -> tuple[int, int]:
        """HP and Attack seed the pair; later stats displace only when higher."""
        value = [self.ranked(species, stat, rebalanced) for stat in range(6)]
        best, second = (1, 0) if value[0] <= value[1] else (0, 1)
        for stat in range(2, 6):
            if value[stat] > value[best]:
                best, second = stat, best
            elif value[stat] > value[second]:
                second = stat
        return best, second

    def _matches(self, rule: dict, trainer: dict, member: dict, species: int, difficulty: int,
                 flags: dict, place: str) -> bool:
        if difficulty not in rule["difficulties"]:
            return False
        if "trainers" in rule and trainer["id"] not in rule["trainers"]:
            return False
        if "species" in rule and species not in rule["species"]:
            return False
        if species in rule.get("not_species", []):
            return False
        if "held_item" in rule and member.get("item") != rule["held_item"]:
            return False
        if member["level"] < rule.get("level_min", 0):
            return False
        if "flag" in rule and flags[rule["flag"]] != rule["flag_value"]:
            return False
        return "maps" not in rule or place in rule["maps"]

    def _hp(self, species: int, level: int, iv_hp: int, ev_hp: int) -> int:
        return (2 * self.base[species][0] + iv_hp + ev_hp // 4) * level // 100 + level + 10

    def evs(self, trainer: dict, member: dict, species: int, difficulty: int, flags: dict, place: str,
            section: int | None, side: int, moves: list[int], grant_species: int | None = None) -> list[int] | None:
        """One fully specified construction context; None when the member is removed.

        The automatic grant ranks grant_species when a later write replaced the species."""
        for rule in self.absent:
            if trainer["id"] in rule["trainers"] and species in rule["species"] and difficulty in rule["difficulties"]:
                return None
        progress = {flag: value for flag, value in flags.items() if isinstance(flag, int)}
        options = self.resolver.spread_options(trainer, member, species, difficulty, progress)
        if options is None or len(options) != 1:
            raise TrainerIvError(f"trainer {trainer['id']} slot {member['index']}: unresolved EV spread")
        spread = options[0][0]
        rule = self.block["spread"]
        evs = [0] * 6
        if rule["first"] <= spread <= rule["last"]:
            if difficulty not in rule["skip_difficulties"]:
                row = self.resolver.spreads[spread]
                evs = list(row[rule["offset"]:rule["offset"] + 6])
            for adjustment in self.block.get("adjustments", []):
                if not self._matches(adjustment, trainer, member, species, difficulty, flags, place):
                    continue
                operation = adjustment["operation"]
                if operation == "clear":
                    evs = [0] * 6
                elif operation == "set":
                    evs = [adjustment["evs"].get(stat, evs[index]) for index, stat in enumerate(STATS)]
                else:
                    result = self.resolver.resolve(trainer, member, difficulty=difficulty, side=side, species=species,
                                                   level=member["level"], moves=moves, progress=progress)
                    if result["status"] != "resolved":
                        raise TrainerIvError(f"trainer {trainer['id']} slot {member['index']}: unresolved HP IV")
                    for _ in range(64):
                        if not self._hp(species, member["level"], result["ivs"][0], evs[0]) & 1:
                            break
                        evs[0] = (evs[0] - 4) & 0xFF
                        evs[2] = (evs[2] + 4) & 0xFF
                    else:
                        raise TrainerIvError(f"trainer {trainer['id']} slot {member['index']}: odd HP loop")
        grant = self.grant.get(difficulty)
        if grant and not sum(evs):
            applies = flags[grant["flag"]] == 1 if "flag" in grant else (
                trainer["class_id"] in grant.get("classes", []) or place in grant.get("maps", [])
                or section in grant.get("sections", []))
            if applies:
                best, second = self.top_two(species if grant_species is None else grant_species,
                                            flags[self.block["grant"]["rebalance_flag"]] == 1)
                evs[best] = evs[second] = self.block["grant"]["value"]
        return evs

    def _variables(self, trainer: dict, difficulty: int, species_options: list[int], places: list[str]) -> dict:
        domains = {"species": species_options, "place": places}
        stage = self.resolver.stages["rival_spread"]
        if any(rule.get("class_id") == trainer.get("class_id") and difficulty in rule.get("difficulties", [])
               for rule in stage["class_rules"]):
            for rule in stage.get("rules", []):
                if "flag" in rule:
                    domains[rule["flag"]] = [0, 1]
        for rule in self.block.get("adjustments", []):
            if "flag" in rule and difficulty in rule["difficulties"]:
                domains[rule["flag"]] = [0, 1]
        grant = self.grant.get(difficulty)
        if grant:
            domains[self.block["grant"]["rebalance_flag"]] = [0, 1]
            if "flag" in grant:
                domains[grant["flag"]] = [0, 1]
        return domains

    def check_places(self, trainers: list[dict]) -> None:
        """Declared place sections agree with the decoded battle maps."""
        for trainer in trainers:
            for name, section in zip(trainer.get("map") or [], trainer.get("map_section") or []):
                if self.places.get(name, section) != section:
                    raise TrainerIvError(f"EV place {name}: section {section}, declared {self.places[name]}")

    def places_of(self, trainer: dict) -> list[str]:
        """Battle places of a trainer, or every declared place and others when none is decoded."""
        unreached = set(self.block["grant"]["unreached_maps"])
        maps = [name for name in trainer.get("map") or [] if name not in unreached]
        if maps:
            return maps
        declared = list(self.places)
        for rule in self.grant.values():
            declared += [f"section:{section}" for section in rule.get("sections", [])]
        return declared + [OTHER]

    def contexts(self, trainer: dict, member: dict, species_options: list[int], side: int,
                 moves_of, species_predicates: dict | None = None, difficulties: list[int] | None = None,
                 grant_species: int | None = None) -> dict[str, object]:
        """EVs per difficulty: a vector, None, or alternatives with conditions."""
        out = {}
        places = self.places_of(trainer)
        sections = {**self.places, **dict(zip(trainer.get("map") or [], trainer.get("map_section") or []))}
        if difficulties is None:
            difficulties = range(len(self.resolver.block["difficulties"]))
        for difficulty in difficulties:
            domains = self._variables(trainer, difficulty, species_options, places)
            names = list(domains)
            cubes = []
            for values in itertools.product(*(domains[name] for name in names)):
                combo = dict(zip(names, values))
                flags = {name: value for name, value in combo.items() if isinstance(name, int)}
                predicates = (species_predicates or {}).get(combo["species"])
                if predicates and not any(all(int(flag, 16) not in flags or flags[int(flag, 16)] == value
                                              for flag, value in choice.items()) for choice in predicates):
                    continue
                place = combo["place"]
                section = int(place[8:]) if place.startswith("section:") else sections.get(place)
                vector = self.evs(trainer, member, combo["species"], difficulty, flags, place, section, side,
                                  moves_of(combo["species"]), grant_species)
                cubes.append((None if vector is None else tuple(vector),
                              {name: [combo[name]] for name in names if len(domains[name]) > 1}))
            merged = _merge(cubes, {name: frozenset(values) for name, values in domains.items()})
            if len(merged) == 1:
                vector = merged[0][0]
                out[str(difficulty)] = None if vector is None else list(vector)
                continue
            rows = []
            for vector, cube in merged:
                when = {}
                for name in names:
                    if name not in cube:
                        continue
                    values = sorted(cube[name], key=lambda value: (str(type(value)), value))
                    key = _flag(name) if isinstance(name, int) else name
                    when[key] = values if name != "place" else [value for value in places if value in cube[name]]
                rows.append({"evs": None if vector is None else list(vector), "when": when})
            out[str(difficulty)] = {"alternatives": rows}
        return out


def _halfwords(data: bytes, table: dict, label: str) -> list[int]:
    raw = _slice(data, table["offset"], 2 * table["count"], label)
    return [int.from_bytes(raw[index:index + 2], "little") for index in range(0, len(raw), 2)]


def _string(data: bytes, offset: int, size: int) -> bytes:
    raw = _slice(data, offset, size, "name")
    return raw.split(b"\xff", 1)[0]


class SpeciesRules:
    """Species the party builder writes over the listed record."""

    def __init__(self, block: dict, data: bytes, resolver: Resolver, items: list[dict] | None,
                 abilities: dict[int, list[int]] | None, variants: set,
                 encounters: list[dict] | None = None, options: dict | None = None):
        self.rules = block.get("species_rules", [])
        self.options = options or {}
        self.battles: dict[int, list[tuple[str, dict | None, set[int]]]] = {}
        names = block["difficulties"]
        for encounter in encounters or []:
            for variant in encounter["variants"]:
                days = set(range(len(names))) if variant["settings"] is None else {
                    names.index(name) for name in variant["settings"]}
                rows = self.battles.setdefault(variant["record"], [])
                rows.extend((variant["role"], command, days) for command in encounter.get("commands") or [None])
        self.data = data
        self.resolver = resolver
        self.difficulties = list(range(len(block["difficulties"])))
        self.items = {row["id"]: row for row in items or []}
        self.abilities = abilities or {}
        self.variants = variants
        self.counts = {rule["id"]: {"members": 0, "changed": 0} for rule in self.rules}
        self.forms, self.cores, self.badge_levels, self.chains = {}, {}, {}, {}
        for rule in self.rules:
            if rule["kind"] == "held_item_form":
                if items is None:
                    raise TrainerIvError(f"{rule['id']}: item hold effects unavailable")
                for form in rule["forms"]:
                    table = _halfwords(data, form["table"], rule["id"]) if "table" in form else None
                    for species in form["species"]:
                        self.forms[rule["id"], species] = (form, table)
            elif rule["kind"] == "minior_core":
                cores = _halfwords(data, rule["cores"], rule["id"])
                if not set(cores) <= set(rule["species"]):
                    raise TrainerIvError(f"{rule['id']}: cores outside the declared species")
                self.cores[rule["id"]] = cores
            elif rule["kind"] == "scaled_evolution":
                if encounters is None:
                    raise TrainerIvError(f"{rule['id']}: trainer encounters unavailable")
                table = rule["evolutions"]
                if int.from_bytes(_slice(data, table["pointer"], 4, rule["id"]), "little") != ROM_BASE + table["offset"]:
                    raise TrainerIvError(f"{rule['id']}: evolution table pointer mismatch")
                levels = rule["badge_levels"]
                raw = _slice(data, levels["offset"], levels["stride"] * levels["count"], rule["id"])
                self.badge_levels[rule["id"]] = list(raw[::levels["stride"]])

    def level_ranges(self, trainer: dict, member: dict) -> dict:
        """Bound default moves by the declared scaling rules."""
        rule = next((row for row in self.rules if row["kind"] == "scaled_evolution"), None)
        listed = member["level"]
        ranges = {str(day): {"min": listed, "max": listed} for day in self.difficulties}
        if rule is None:
            return ranges
        lowest = min(mon["level"] for mon in trainer["party"] if mon["species"])
        delta = listed - lowest
        for day in self.difficulties:
            unscaled = day in rule["unscaled_difficulties"]
            if unscaled and trainer["id"] not in rule["unscaled_trainers"] and not set(
                    trainer.get("map_section") or []) & set(rule["unscaled_sections"]):
                continue
            if not unscaled and (trainer["class_id"] in rule["boss_classes"]
                                 or trainer["party_flags"] & rule["custom_moves_flag"]):
                maximum = 100
            else:
                penalty = (rule["raised_level_penalty"] if day in rule["raise_difficulties"]
                           else rule["party_level_penalties"][trainer["party_size"] - 1])
                maximum = min(100, max(listed, max(self.badge_levels[rule["id"]]) + delta,
                                       100 - penalty + delta))
            ranges[str(day)]["max"] = maximum
        return ranges

    def _gated(self, rule: dict, trainer: dict, member: dict, difficulty: int, ability: bool = False) -> bool:
        first, last = rule["spreads"]
        hits = set()
        for spread, _ in self.resolver.spread_options(trainer, member, member["species"], difficulty, None) or []:
            passed = first <= spread <= last
            if passed and ability:
                passed = self.resolver.spreads[spread][rule["ability_offset"]] <= rule["ability_max"]
            hits.add(passed)
        if len(hits) != 1:
            raise TrainerIvError(f"trainer {trainer['id']} slot {member['index']}: {rule['id']} spread gate unresolved")
        return hits.pop()

    def _trainer_level(self, rule: dict, trainer: dict, member: dict):
        change = next((row for row in rule["changes"] if row["species"] == member["species"]), None)
        if trainer["id"] != rule["trainer"] or change is None:
            return False, []
        days = [day for day in rule["difficulties"] if self._gated(rule, trainer, member, day)]
        return True, [{"species": change["target"], "rule": rule["id"], "level": change["level_above"] + 1,
                       "difficulties": days}] if days else []

    def _held_item_form(self, rule: dict, trainer: dict, member: dict):
        form, table = self.forms.get((rule["id"], member["species"]), (None, None))
        if form is None or not trainer["party_flags"] & rule["item_flag"]:
            return False, []
        item = self.items.get(member.get("item") or 0, {})
        effect, param = item.get("held_effect", 0), item.get("hold_effect_param", 0)
        if effect in form.get("unsupported_effects", []):
            raise TrainerIvError(f"trainer {trainer['id']} slot {member['index']}: unmodeled form item {member['item']}")
        if "ability" in form and set(self.abilities.get(member["species"], [])) != {form["ability"]}:
            raise TrainerIvError(f"trainer {trainer['id']} slot {member['index']}: form ability unresolved")
        target = None
        if effect == form["hold_effect"]:
            if "target" in form:
                target = form["target"]
            elif "params" in form:
                target = dict(form["params"]).get(param)
            elif param < len(table):
                target = table[param] or None
        elif "keep_sections" in form and form["otherwise"] != member["species"]:
            raise TrainerIvError(f"trainer {trainer['id']} slot {member['index']}: map-dependent form")
        target = target or form.get("otherwise")
        if target is None or target == member["species"]:
            return True, []
        return True, [{"species": target, "rule": rule["id"], "difficulties": self.difficulties}]

    def _personality(self, rule: dict, trainer: dict, party: list[dict], slot: int) -> int:
        """The fixed value of a member that keeps its built personality."""
        if trainer.get("kind") == "partners":
            raise TrainerIvError(f"trainer {trainer['id']}: partner personality not modeled")
        header = int(trainer["offset"], 16)
        name = _string(self.data, header + rule["trainer_name"]["offset"], rule["trainer_name"]["size"])
        gender = self.data[header + rule["gender"]["offset"]] >> rule["gender"]["shift"] & 1
        fixed = trainer["class_id"] in rule["gender_classes"]
        values = rule["values"]
        total = 0
        for index, mon in enumerate(party[:slot + 1]):
            if (trainer["id"], mon["index"]) in self.variants:
                raise TrainerIvError(f"trainer {trainer['id']}: name hash depends on a species variant")
            names = rule["names"]
            total += sum(name) + sum(_string(self.data, names["offset"] + names["stride"] * mon["species"],
                                             names["stride"]))
        if fixed:
            base = values["female"] if gender else values["male"]
        else:
            # Every third member takes the opposite gender.
            female = ((len(name) & 1) == 0) == ((slot + 1) % 3 == 0)
            base = (values["female"] if female else values["male"]) ^ len(name)
        return (base + (total << 8)) & 0xFFFFFFFF

    def _minior_core(self, rule: dict, trainer: dict, member: dict):
        if member["species"] not in rule["species"]:
            return False, []
        random = rule["personality"] == "random"
        days = [day for day in self.difficulties if self._gated(rule, trainer, member, day, True) == random]
        if not days:
            return False, []
        cores = self.cores[rule["id"]]
        if random:
            return True, [{"species": core, "rule": rule["id"], "difficulties": days} for core in cores]
        party = [mon for mon in trainer["party"] if mon["species"]]
        core = cores[self._personality(rule, trainer, party, party.index(member)) % len(cores)]
        return True, [] if core == member["species"] else [{"species": core, "rule": rule["id"], "difficulties": days}]

    def _evolve(self, rule: dict, species: int, level: int) -> int:
        """EvolveSpeciesByLevel: the first passing entry, repeated."""
        table, methods, levels = rule["evolutions"], rule["methods"], rule["levels"]
        seen = set()
        while species not in seen:
            seen.add(species)
            at = table["offset"] + species * table["stride"]
            for index in range(table["entries"]):
                method, param, target = (int.from_bytes(_slice(self.data, at + 8 * index + 2 * field, 2, rule["id"]),
                                                        "little") for field in range(3))
                if method == 0:
                    return species
                kind = method & 0xFF
                if kind in methods["level"] and param <= level or any(
                        kind in methods[name] and level >= levels[name] for name in EVOLUTION_CLASSES[1:]):
                    species = target
                    break
            else:
                return species
        raise TrainerIvError(f"{rule['id']}: evolution chain of species {species} does not end")

    def _chain(self, rule: dict, species: int, listed: int) -> list[dict]:
        """Species by built level once scaling raises the listed level."""
        key = (rule["id"], species, listed)
        if key not in self.chains:
            self.chains[key] = self._runs(rule, species, listed)
        return self.chains[key]

    def _runs(self, rule: dict, species: int, listed: int) -> list[dict]:
        runs = []
        for level in range(listed + 1, 101):
            target = self._evolve(rule, species, level)
            if runs and runs[-1]["species"] == target:
                runs[-1]["last"] = level
            else:
                runs.append({"species": target, "level": level, "last": level})
        runs = [row for row in runs if row["species"] != species]
        if runs:
            del runs[-1]["last"]
        return runs

    def _battle(self, rule: dict, trainer: dict) -> tuple[str, set[int]] | None:
        """The declared battle condition, empty when every battle enables it, and the difficulties served."""
        enabled, other, days = [], [], set()
        for role, command, served in self.battles.get(trainer["id"], []):
            on = role != "partner" and command is not None and command["mode"] in rule["modes"]
            (enabled if on else other).append(command)
            if on:
                days |= served
        if not enabled:
            return None
        if not other:
            return "", days
        for battle in rule.get("battles", []):
            if all(command["mode"] in battle["modes"] if "modes" in battle
                   else int(command["script"], 16) == battle["command"] for command in enabled):
                return battle["id"], days
        raise TrainerIvError(f"trainer {trainer['id']}: {rule['id']} battle condition undeclared")

    def _scaled_evolution(self, rule: dict, trainer: dict, member: dict):
        reached = self._battle(rule, trainer)
        if reached is None:
            return False, []
        battle, served = reached
        if member.get("item") in rule["keep_items"]:
            return True, []
        party = [mon for mon in trainer["party"] if mon["species"]]
        lowest = min(mon["level"] for mon in party)
        options = self.options.get((trainer["id"], member["index"]))
        found: dict[tuple, list[int]] = {}
        for day in sorted(served):
            if day in rule["unscaled_difficulties"]:
                if trainer["id"] in rule["unscaled_trainers"] or set(trainer.get("map_section") or []) & set(
                        rule["unscaled_sections"]):
                    raise TrainerIvError(f"trainer {trainer['id']}: {rule['id']} scaling exception not modeled")
                continue
            badges = None
            if trainer["class_id"] not in rule["boss_classes"] and not trainer["party_flags"] & rule["custom_moves_flag"] \
                    and day not in rule["raise_difficulties"]:
                # Only the badge minimum raise evolves these members.
                badges = next((count for count, level in enumerate(self.badge_levels[rule["id"]]) if level > lowest),
                              None)
                if badges is None:
                    continue
            for species in options or [member["species"]]:
                for row in self._chain(rule, species, member["level"]):
                    if row["species"] in self.form_species:
                        raise TrainerIvError(f"trainer {trainer['id']} slot {member['index']}: evolves into a form species")
                    key = (species if options else None, row["level"], row.get("last"), badges, row["species"])
                    found.setdefault(key, []).append(day)
        rows = []
        for (source, level, last, badges, species), days in sorted(found.items(), key=lambda item: (
                item[0][0] or 0, item[1][0], item[0][1], item[0][3] or 0)):
            row = {"species": species, "rule": rule["id"], "level": level, "difficulties": days}
            for key, value in (("last", last), ("badges", badges), ("battle", battle or None), ("from", source)):
                if value is not None:
                    row[key] = value
            rows.append(row)
        return True, rows

    @property
    def form_species(self) -> set[int]:
        return {species for (_, species) in self.forms} | {
            species for rule in self.rules if rule["kind"] == "minior_core" for species in rule["species"]}

    def changes(self, trainer: dict, member: dict) -> list[dict]:
        rows = []
        for rule in self.rules:
            reached, found = getattr(self, f"_{rule['kind']}")(rule, trainer, member)
            if reached:
                self.counts[rule["id"]]["members"] += 1
            rows.extend(found)
        scaled = {rule["id"] for rule in self.rules if rule["kind"] == "scaled_evolution"}
        fixed = [row for row in rows if row["rule"] not in scaled and "level" in row]
        # A trainer branch reaching the same species already covers it.
        rows = [row for row in rows if row["rule"] not in scaled or "last" in row or "from" in row or not any(
            other["species"] == row["species"] and other["level"] <= row["level"]
            and set(row["difficulties"]) <= set(other["difficulties"]) for other in fixed)]
        for name in dict.fromkeys(row["rule"] for row in rows):
            self.counts[name]["changed"] += 1
        return rows


class Outcomes:
    """Moves, IVs and EVs of each built species, by built level."""

    def __init__(self, block: dict, resolver: Resolver, ev_resolver: EvResolver | None, learnsets: dict,
                 progress_options: list[dict], species_conditions: dict):
        self.rules = {rule["id"]: rule for rule in block.get("species_rules", [])}
        self.resolver = resolver
        self.writes = PartyWrites(block, resolver)
        self.ev_resolver = ev_resolver
        self.learnsets = learnsets
        self.progress_options = progress_options
        self.species_conditions = species_conditions
        self.levels_read = [rule for rule in (ev_resolver.block.get("adjustments", []) if ev_resolver else [])
                            if "level_min" in rule or rule["operation"] == "even_hp"]

    def _ivs(self, trainer: dict, member: dict, species: int, level: int, moves: list[int], difficulty: int,
             side: int) -> list[int]:
        found = set()
        for progress in self.progress_options:
            result = self.resolver.resolve(trainer, member, difficulty=difficulty, side=side, species=species,
                                           level=level, moves=moves, progress=progress)
            if result["status"] != "resolved":
                raise TrainerIvError(f"trainer {trainer['id']} slot {member['index']}: {result['status']} built IVs")
            found.add(tuple(result["ivs"]))
        if len(found) != 1:
            raise TrainerIvError(f"trainer {trainer['id']} slot {member['index']}: uncollapsed built IVs")
        return list(found.pop())

    def details(self, trainer: dict, member: dict, change: dict, side: int, variants: list[dict] | None) -> dict:
        """The written held item; moves, IVs and EVs, by bands of built levels where they vary."""
        rule = self.rules[change["rule"]]
        source = change.get("from", member["species"])
        spread, grant, learned = stage_species(rule["stage"], source, change["species"])
        write = next((row for row in rule.get("changes", []) if row["species"] == source
                      and row["target"] == change["species"]), {})
        days = change["difficulties"]
        if "level" in change:
            levels = range(change["level"], change.get("last", 100) + 1)
        else:
            levels = [member["level"]]
        predicates = {}
        for option in variants or []:
            if option["species"] == source:
                predicates[spread] = self.species_conditions[option["condition"]]["when"]
        reads_level = any("trainers" not in row or trainer["id"] in row["trainers"] for row in self.levels_read)
        cache, bands = {}, []
        for level in levels:
            moves = member["moves"] if member["moves"] is not None else default_moves(self.learnsets, learned, level)
            if moves is None:
                raise TrainerIvError(f"trainer {trainer['id']} slot {member['index']}: built moves unavailable")
            built = list(moves)
            for row in write.get("moves", []):
                if level > row.get("level_above", 0):
                    built += [0] * (row["slot"] + 1 - len(built))
                    built[row["slot"]] = row["move"]
            key = (tuple(moves), level if reads_level else None)
            if key not in cache:
                ivs = {str(day): self._ivs(trainer, member, spread, level, moves, day, side) for day in days}
                evs = None if self.ev_resolver is None else self.ev_resolver.contexts(
                    trainer, {**member, "level": level}, [spread], side, lambda _, moves=moves: moves, predicates,
                    days, grant)
                cache[key] = (ivs, evs)
            ivs, evs = cache[key]
            band = {"moves": built, "ivs": ivs, **({} if evs is None else {"evs": evs})}
            if bands and {key: value for key, value in bands[-1].items() if key not in ("level", "last")} == band:
                bands[-1]["last"] = level
                continue
            if "level" in change:
                band = {"level": level, "last": level, **band}
            bands.append(band)
        out = {"item": write["item"]} if "item" in write else {}
        for name in ("moves", "ivs", "evs"):
            if name in bands[0] and all(band[name] == bands[0][name] for band in bands):
                out[name] = bands[0][name]
                for band in bands:
                    del band[name]
        if len(bands) > 1:
            for band in bands:
                if band["last"] == change.get("last", 100):
                    del band["last"]
            out["bands"] = bands

        def baseline(level):
            band = next((row for row in out.get("bands", [])
                         if row["level"] <= level <= row.get("last", 100)), {})
            return band.get("moves", out.get("moves")), out.get("item", member.get("item") or 0)
        rewrites = self.writes.changes(trainer, member, spread, baseline, levels, days, write)
        if rewrites:
            out["write_changes"] = rewrites
        return out


def _progress_options(block: dict) -> list[dict]:
    rival = next(stage for stage in block["stages"] if stage["operation"] == "rival_spread")
    flags = sorted({rule["flag"] for rule in rival.get("rules", []) if "flag" in rule})
    return [dict(zip(flags, values)) for values in itertools.product((0, 1), repeat=len(flags))]


def build(block: dict, data: bytes, trainers: list[dict], learnsets: dict,
          profile: dict, rom: dict, profile_block: str, evs: dict | None = None,
          absent: list[dict] | None = None, items: list[dict] | None = None,
          abilities: dict[int, list[int]] | None = None, encounters: list[dict] | None = None) -> dict:
    """Generate all listed difficulty and side contexts deterministically.

    With an EV block, every member also carries its effective EVs per
    difficulty, from the same spread selection and the declared EV writes.
    Declared species rules add the species the builder writes instead."""
    problems = check(block)
    if problems:
        raise TrainerIvError("invalid IV declarations: " + ", ".join(problems))
    validate_rom(block, data)
    class_iv, spreads = read_tables(block, data)
    resolver = Resolver(block, class_iv, spreads, learnsets)
    ev_resolver = None
    if evs is not None:
        validate_ev_rom(evs, data)
        ev_resolver = EvResolver(evs, resolver, *read_ev_tables(evs, data), absent)
        ev_resolver.check_places(trainers)
        ev_counts = {"members_with_evs": [0] * len(block["difficulties"]),
                     "conditional_members": [0] * len(block["difficulties"])}
    variants = {(row["trainer"], row["slot"]): row["species"] for row in block.get("species_variants", [])}
    species_outcomes = {(row["trainer"], row["slot"]): row["outcomes"] for row in block.get("species_variants", [])}
    species_conditions = {row["id"]: row for row in block.get("species_conditions", [])}
    progress_options = _progress_options(block)
    species_rules = SpeciesRules(block, data, resolver, items, abilities, set(variants), encounters, variants)
    built = Outcomes(block, resolver, ev_resolver, learnsets, progress_options, species_conditions)
    members = []
    used_variants = set()
    evaluated = 0
    for trainer in trainers:
        if not _integer(trainer.get("class_id")) or not _integer(trainer.get("ai_flags")):
            raise TrainerIvError(f"trainer {trainer.get('id')}: numeric class or AI flags unavailable")
        for member in trainer["party"]:
            if not member["species"]:
                continue
            variant_key = (trainer["id"], member["index"])
            if variant_key in variants:
                used_variants.add(variant_key)
            species_options = variants.get(variant_key, [member["species"]])
            listed_moves = member["moves"]
            if listed_moves is None:
                listed_moves = default_moves(learnsets, member["species"], member["level"])
            if listed_moves is None:
                raise TrainerIvError(f"trainer {trainer['id']} slot {member['index']}: default moves unavailable")
            contexts = {}
            for difficulty in range(len(block["difficulties"])):
                for side in (0, 1):
                    # Partner construction uses only three slots.
                    if side == 0 and member["index"] >= 3:
                        continue
                    outcomes = set()
                    for species in species_options:
                        moves = member["moves"]
                        if moves is None:
                            moves = default_moves(learnsets, species, member["level"])
                        if moves is None:
                            raise TrainerIvError(f"trainer {trainer['id']} slot {member['index']}: variant moves unavailable")
                        for progress in progress_options:
                            result = resolver.resolve(trainer, member, difficulty=difficulty, side=side,
                                                      species=species, level=member["level"], moves=moves,
                                                      progress=progress)
                            evaluated += 1
                            if result["status"] != "resolved":
                                raise TrainerIvError(
                                    f"trainer {trainer['id']} slot {member['index']}: {result['status']} progress context")
                            outcomes.add(tuple(result["ivs"]))
                    if len(outcomes) != 1:
                        raise TrainerIvError(f"trainer {trainer['id']} slot {member['index']}: uncollapsed progress IVs")
                    contexts[f"{difficulty}/{side}"] = list(next(iter(outcomes)))
            row = {
                "trainer": trainer["id"], "slot": member["index"],
                "class_id": trainer["class_id"], "ai_flags": trainer["ai_flags"],
                "party_flags": trainer["party_flags"],
                "listed": {"species": member["species"], "level": member["level"],
                           "moves": listed_moves, "spread": member["iv"] & 0xFF},
                "contexts": contexts,
            }
            if member["moves"] is None:
                ranges = species_rules.level_ranges(trainer, member)
                row["level_ranges"] = ranges
                row["move_bands"] = default_move_bands(learnsets, member["species"],
                    min(value["min"] for value in ranges.values()), max(value["max"] for value in ranges.values()))

            def baseline(level):
                moves = member["moves"] if member["moves"] is not None else default_moves(
                    learnsets, member["species"], level)
                return moves, member.get("item") or 0
            rewrites = built.writes.changes(trainer, member, member["species"], baseline,
                                           range(1, 101), list(range(len(block["difficulties"]))))
            if rewrites:
                row["write_changes"] = rewrites
            outcomes = species_outcomes.get(variant_key)
            if outcomes:
                row["species_variants"] = outcomes
            changes = species_rules.changes(trainer, member)
            if changes:
                if outcomes and any("from" not in change for change in changes):
                    raise TrainerIvError(f"trainer {trainer['id']} slot {member['index']}: species variant and rule")
                side = 0 if trainer.get("kind") == "partners" else 1
                for change in changes:
                    change.update(built.details(trainer, member, change, side, outcomes))
                row["species_changes"] = changes
            if ev_resolver is not None:
                def moves_of(species, member=member):
                    return member["moves"] if member["moves"] is not None \
                        else default_moves(learnsets, species, member["level"])
                side = 0 if trainer.get("kind") == "partners" else 1
                predicates = {option["species"]: species_conditions[option["condition"]]["when"]
                              for option in outcomes or []}
                row["evs"] = ev_resolver.contexts(trainer, member, species_options, side, moves_of, predicates)
                for key, value in row["evs"].items():
                    if isinstance(value, dict):
                        ev_counts["conditional_members"][int(key)] += 1
                    elif value is not None and any(value):
                        ev_counts["members_with_evs"][int(key)] += 1
            members.append(row)
    if used_variants != set(variants):
        missing = sorted(set(variants) - used_variants)
        raise TrainerIvError(f"species variants without listed members: {missing}")
    doc = {
        "format": FORMAT, "schema": DOC_SCHEMA, "profile": profile, "rom": rom,
        "profile_block": profile_block, "stat_order": block["stat_order"],
        "difficulties": block["difficulties"], "context": "listed-team",
        "members": members,
        "species_conditions": list(species_conditions.values()),
        "species_rules": block.get("species_rules", []),
        "write_rules": block.get("write_rules", []),
        "audit": {"species_variant_members": len(variants), "evaluated_contexts": evaluated,
                  "different_iv_outcomes": 0, "species_rules": species_rules.counts},
        "provenance": {
            "evidence": block["evidence"],
            "tables": [{"name": row["name"], "sha256": row["sha256"]} for row in block["tables"]],
            "spans": [{"name": row["name"], "sha256": row["sha256"]} for row in block["spans"]],
            "stages": [{"operation": stage["operation"],
                        "condition_offsets": stage["condition_offsets"],
                        "operation_offsets": stage["operation_offsets"]} for stage in block["stages"]],
        },
    }
    if ev_resolver is not None:
        doc["ev_conditions"] = condition_labels(evs)
        doc["ev_places"] = dict(ev_resolver.places)
        doc["audit"]["evs"] = ev_counts
        doc["provenance"]["evs"] = {
            "evidence": evs["evidence"],
            "tables": [{"name": row["name"], "sha256": row["sha256"]} for row in evs["tables"]],
            "spans": [{"name": row["name"], "sha256": row["sha256"]} for row in evs["spans"]],
            "adjustments": [{"id": row["id"], "offsets": row["offsets"]} for row in evs.get("adjustments", [])],
            "grant": [{"difficulty": row["difficulty"], "offsets": row["offsets"]} for row in evs["grant"]["rules"]],
        }
    return doc


def _ev_vector(value) -> bool:
    return value is None or isinstance(value, list) and len(value) == 6 and all(
        _integer(item) and item <= 255 for item in value)


def _ev_value(value) -> bool:
    """A vector, a removed member, or alternatives with conditions."""
    if not isinstance(value, dict):
        return _ev_vector(value)
    rows = value.get("alternatives")
    return isinstance(rows, list) and len(rows) > 1 and all(
        isinstance(row, dict) and _ev_vector(row.get("evs")) and isinstance(row.get("when"), dict) and row["when"]
        for row in rows)


def _iv_vector(value) -> bool:
    return isinstance(value, list) and len(value) == 6 and all(_integer(item) and item <= 31 for item in value)


def _move_bands(bands, listed: dict, ranges: dict | None = None) -> bool:
    """Require complete, compact levels and the listed moves at baseline."""
    if not isinstance(bands, list) or not bands or not _integer(listed.get("level"), 1):
        return False
    minimum = min(value["min"] for value in ranges.values()) if ranges else 1
    maximum = max(value["max"] for value in ranges.values()) if ranges else 100
    level, previous = minimum, None
    for band in bands:
        if not isinstance(band, dict) or not _integer(band.get("level"), 1) or band["level"] != level:
            return False
        last, moves = band.get("last", 100), band.get("moves")
        if not _integer(last, level) or last > maximum or not isinstance(moves, list) or len(moves) > 4 \
                or not all(_integer(move, 1) for move in moves) or len(set(moves)) != len(moves) or moves == previous:
            return False
        if level <= listed.get("level", 0) <= last and moves != listed.get("moves"):
            return False
        level, previous = last + 1, moves
    return level == maximum + 1


def _level_ranges(ranges, listed: dict, difficulties: set[int]) -> bool:
    """Require bounded ranges for every difficulty."""
    return isinstance(ranges, dict) and set(ranges) == {str(day) for day in difficulties} and all(
        isinstance(value, dict) and _integer(value.get("min"), 1) and _integer(value.get("max"), 1)
        and value["min"] == listed.get("level") and value["min"] <= value["max"] <= 100
        for value in ranges.values())


def _built_details(change: dict, with_evs: bool) -> bool:
    """Every field of a built species once, at the change or in each band of built levels."""
    days = {str(day) for day in change["difficulties"]}
    bands = change.get("bands", [{}])
    if "bands" in change and (len(bands) < 2 or "level" not in change or not all(
            isinstance(band, dict) and _integer(band.get("level"), 1) for band in bands)):
        return False
    checks = {"moves": lambda value: isinstance(value, list) and all(_integer(move) for move in value),
              "ivs": lambda value: isinstance(value, dict) and set(value) == days
              and all(_iv_vector(vector) for vector in value.values())}
    if with_evs:
        checks["evs"] = lambda value: isinstance(value, dict) and set(value) == days \
            and all(_ev_value(item) for item in value.values())
    for name, valid in checks.items():
        if (name in change) == all(name in band for band in bands) or not all(
                valid(item.get(name)) for item in ([change] if name in change else bands)):
            return False
    abilities = change.get("abilities")
    return ("item" not in change or _integer(change["item"], 1)) and (abilities is None or isinstance(
        abilities, dict) and set(abilities) == days and all(
        isinstance(ids, list) and all(_integer(value, 1) for value in ids) for ids in abilities.values()))


def _write_changes(rows, rules: set[str], difficulties: set[int]) -> bool:
    """Reject undeclared writes and ambiguous display conditions."""
    if not isinstance(rows, list):
        return False
    for row in rows:
        if (not isinstance(row, dict) or not _ints(row.get("difficulties"))
                or not set(row["difficulties"]) <= difficulties
                or not _integer(row.get("level"), 1) or not _integer(row.get("last", 100), 1)
                or not row["level"] <= row.get("last", 100) <= 100
                or not _integer(row.get("item")) or not isinstance(row.get("moves"), list)
                or len(row["moves"]) > 4 or any(not _integer(move) for move in row["moves"])
                or not isinstance(row.get("rules"), list) or not row["rules"]
                or any(not isinstance(name, str) or name not in rules for name in row["rules"])
                or not isinstance(row.get("when"), dict)):
            return False
        for flag, values in row["when"].items():
            if (not isinstance(flag, str) or not re.fullmatch(r"0x[0-9A-F]+", flag)
                    or not _ints(values) or not set(values) <= {0, 1}):
                return False
    for index, row in enumerate(rows):
        for other in rows[index + 1:]:
            if (set(row["difficulties"]) & set(other["difficulties"])
                    and max(row["level"], other["level"]) <= min(row.get("last", 100), other.get("last", 100))):
                flags = set(row["when"]) | set(other["when"])
                if all(set(row["when"].get(flag, [0, 1])) & set(other["when"].get(flag, [0, 1])) for flag in flags):
                    return False
    return True


def verify_doc(doc: dict, block: dict, profile_name: str, rom_sha1: str, profile_block: str,
               evs: dict | None = None) -> list[str]:
    """Verify a generated companion without reading the ROM."""
    problems = []
    if doc.get("format") != FORMAT or doc.get("schema") != DOC_SCHEMA:
        return [f"format: expected trainer IV schema {DOC_SCHEMA}"]
    if doc.get("profile", {}).get("id") != profile_name:
        problems.append("profile: regenerate trainer IV data")
    if doc.get("rom", {}).get("sha1") != rom_sha1:
        problems.append("rom sha1: regenerate trainer IV data")
    if doc.get("profile_block") != profile_block:
        problems.append("profile_block: regenerate trainer IV data")
    if doc.get("stat_order") != block["stat_order"] or doc.get("difficulties") != block["difficulties"]:
        problems.append("context labels: regenerate trainer IV data")
    if doc.get("context") != "listed-team":
        problems.append("context: expected listed-team")
    if doc.get("species_conditions") != block.get("species_conditions", []):
        problems.append("species conditions: regenerate trainer IV data")
    if evs is not None and doc.get("ev_conditions") != condition_labels(evs):
        problems.append("EV conditions: regenerate trainer IV data")
    rules = {row["id"]: row for row in block.get("species_rules", [])}
    if doc.get("species_rules") != block.get("species_rules", []) \
            or set(doc.get("audit", {}).get("species_rules", {})) != set(rules):
        problems.append("species rules: regenerate trainer IV data")
    if doc.get("write_rules", []) != block.get("write_rules", []):
        problems.append("write rules: regenerate trainer IV data")
    write_rules = {row["id"] for row in block.get("write_rules", []) if row["status"] == "known"}
    difficulties = set(range(len(block["difficulties"])))
    variants = {(row["trainer"], row["slot"]): row["outcomes"] for row in block.get("species_variants", [])}
    seen = set()
    for member in doc.get("members", []):
        key = (member.get("trainer"), member.get("slot"))
        if not all(_integer(value) for value in key) or key in seen:
            problems.append("members: unique trainer and slot required")
            continue
        seen.add(key)
        if member.get("species_variants") != variants.get(key):
            problems.append(f"member {key}: species conditions differ from declarations")
        changes = member.get("species_changes")
        sources = {option["species"] for option in variants.get(key) or []}
        if changes is not None and (not isinstance(changes, list) or not changes or any(
                not isinstance(row, dict) or row.get("rule") not in rules or not _integer(row.get("species"), 1)
                or not _ints(row.get("difficulties")) or not set(row["difficulties"]) <= difficulties
                or ("from" in row) != bool(sources) or "from" in row and row["from"] not in sources
                for row in changes)):
            problems.append(f"member {key}: species changes without declared rules")
        elif changes is not None and not all(_built_details(row, evs is not None) for row in changes):
            problems.append(f"member {key}: built species details incomplete")
        write_sources = [member] + ([row for row in changes if isinstance(row, dict)]
                                    if isinstance(changes, list) else [])
        for source in write_sources:
            if not _write_changes(source.get("write_changes", []), write_rules, difficulties):
                problems.append(f"member {key}: invalid party writes")
        listed = member.get("listed", {})
        if not _integer(listed.get("species"), 1) or not _integer(listed.get("level"), 1) \
                or not isinstance(listed.get("moves"), list) or not _integer(listed.get("spread")):
            problems.append(f"member {key}: invalid listed context")
        if "move_bands" in member:
            if not _level_ranges(member.get("level_ranges"), listed, difficulties):
                problems.append(f"member {key}: invalid default move level ranges")
            elif not _move_bands(member["move_bands"], listed, member["level_ranges"]):
                problems.append(f"member {key}: invalid default move bands")
        sides = (1,) if member["slot"] >= 3 else (0, 1)
        expected = {f"{difficulty}/{side}" for difficulty in difficulties for side in sides}
        if set(member.get("contexts", {})) != expected:
            problems.append(f"member {key}: incomplete contexts")
        for vector in member.get("contexts", {}).values():
            if not isinstance(vector, list) or len(vector) != 6 or any(not _integer(value) or value > 31 for value in vector):
                problems.append(f"member {key}: invalid IV vector")
                break
        if evs is not None:
            values = member.get("evs")
            if not isinstance(values, dict) or set(values) != {str(index) for index in range(len(block["difficulties"]))}:
                problems.append(f"member {key}: incomplete EV contexts")
            elif not all(_ev_value(value) for value in values.values()):
                problems.append(f"member {key}: invalid EV vector")
        elif "evs" in member:
            problems.append(f"member {key}: EVs without declarations")
    if set(variants) - seen:
        problems.append("species variants: declared members missing")
    return problems


def read(path: Path) -> dict:
    """Read one generated companion as its document."""
    from .pokedexstore import load
    return load(path)
