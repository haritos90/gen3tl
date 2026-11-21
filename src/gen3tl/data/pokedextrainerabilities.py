"""Resolve listed trainer abilities through declared construction rules."""

from __future__ import annotations

import hashlib

from ..core.notes import has_prose
from . import pokedextrainerivs as iv

MODES = {"hidden", "first", "second", "regular", "all", "personality"}
OPERATIONS = {"hidden", "regular", "personality", "shiny", "absent"}


def check(block: dict) -> list[str]:
    """Require explicit construction and fallback rules."""
    problems = []
    if block.get("schema") != 1 or block.get("context") != "listed-species":
        problems.append("schema/context")
    if not has_prose(block.get("evidence")) or not block.get("difficulties"):
        problems.append("evidence/difficulties")
    initial = block.get("initial", {})
    for key in ("name_offset", "name_size", "terminator", "gender_offset", "partner_music_mask",
                "fixed_gender_classes", "evidence"):
        if key not in initial:
            problems.append(f"initial {key}")
    spread = block.get("spread", {})
    for key in ("first", "last", "selector_offset", "modes", "evidence"):
        if key not in spread:
            problems.append(f"spread {key}")
    if any(mode not in MODES for mode in spread.get("modes", [])):
        problems.append("spread modes")
    fallback = block.get("fallback", {})
    if fallback.get("second") != "first" or fallback.get("hidden") != "personality" or not has_prose(fallback.get("evidence")):
        problems.append("fallback")
    if not block.get("spans"):
        problems.append("spans")
    for span in block.get("spans", []):
        if (not iv._integer(span.get("offset")) or not iv._integer(span.get("size"), 1)
                or not iv.SHA256.fullmatch(span.get("sha256", ""))):
            problems.append("span identity")
    for rule in block.get("overrides", []):
        if (rule.get("operation") not in OPERATIONS or not rule.get("trainers")
                or not rule.get("species") or not rule.get("difficulties") or not has_prose(rule.get("evidence"))):
            problems.append("override")
        if rule.get("operation") == "personality" and rule.get("bit") not in (0, 1):
            problems.append("override bit")
    return problems


def resolve(block: dict, trainer: int, species: int, difficulty: int, initial_bit: int,
            spread_options: list[int], spreads: list[list[int]], slots: list[int]) -> list[int]:
    """Keep personality alternatives until slot fallbacks collapse them."""
    if initial_bit not in (0, 1) or len(slots) != 3 or not spread_options:
        raise ValueError("trainer ability: missing construction inputs")
    outcomes = set()
    present = set()
    spread_rule = block["spread"]
    for spread in spread_options:
        states = {(initial_bit, False)}
        absent = False
        if spread_rule["first"] <= spread <= spread_rule["last"]:
            if spread >= len(spreads):
                raise ValueError(f"trainer ability: missing spread {spread}")
            selector = spreads[spread][spread_rule["selector_offset"]]
            modes = spread_rule["modes"]
            mode = modes[selector] if selector < len(modes) else "personality"
            if mode == "first":
                states = {(0, False)}
            elif mode == "second":
                states = {(1, False)}
            elif mode == "hidden":
                states = {(0, True), (1, True)}
            elif mode == "regular":
                states = {(0, False), (1, False)}
            elif mode == "all":
                states = {(0, False), (1, False), (0, True), (1, True)}
            for rule in block.get("overrides", []):
                if (trainer not in rule["trainers"] or species not in rule["species"]
                        or difficulty not in rule["difficulties"]):
                    continue
                operation = rule["operation"]
                if operation == "hidden":
                    states = {(bit, True) for bit, hidden in states}
                elif operation == "regular":
                    states = {(bit, False) for bit, hidden in states}
                elif operation == "personality":
                    states = {(rule["bit"], hidden) for bit, hidden in states}
                elif operation == "shiny":
                    states = {(value, hidden) for bit, hidden in states
                              for value in ((0, 1) if hidden else (bit,))}
                elif operation == "absent":
                    absent = True
        present.add(not absent)
        if not absent:
            for bit, hidden in states:
                outcomes.add(slots[2] if hidden and slots[2] else slots[1] if bit and slots[1] else slots[0])
    if len(present) > 1 or 0 in outcomes:
        raise ValueError("trainer ability: unresolved presence or empty first slot")
    return sorted(outcomes)


def _prepare(block: dict, data: bytes) -> tuple[dict, iv.Resolver, list[list[int]]]:
    rule = block["abilities"]
    problems = check(rule)
    if problems:
        raise ValueError("invalid trainer abilities: " + ", ".join(problems))
    if rule["difficulties"] != block["ivs"]["difficulties"]:
        raise ValueError("trainer ability: difficulty mismatch")
    iv.validate_rom(block["ivs"], data)
    for span in rule["spans"]:
        raw = data[span["offset"]:span["offset"] + span["size"]]
        if hashlib.sha256(raw).hexdigest() != span["sha256"]:
            raise ValueError("trainer ability: code hash mismatch")
    class_iv, spreads = iv.read_tables(block["ivs"], data)
    return rule, iv.Resolver(block["ivs"], class_iv, spreads, {}), spreads


def _bits(block: dict, data: bytes, trainer: dict) -> tuple[int, int]:
    """The initial personality bit on the partner and opponent sides."""
    initial = block["abilities"]["initial"]
    header = block["base"] + trainer["id"] * block["stride"]
    start = header + initial["name_offset"]
    name = data[start:start + initial["name_size"]]
    if initial["terminator"] not in name:
        raise ValueError(f"trainer {trainer['id']}: unterminated name")
    parity = name.index(initial["terminator"]) & 1
    fixed = trainer["class_id"] in initial["fixed_gender_classes"]
    partner = bool(data[header + initial["gender_offset"]] & initial["partner_music_mask"])
    return 0 if fixed or partner else parity, 0 if fixed else parity


def _slots(data: bytes, species: dict, slot: int) -> list[int]:
    at = species["base"] + slot * species["stride"]
    return [data[at + species["fields"][field]] for field in ("ability1", "ability2", "hidden_ability")]


def annotate(block: dict, data: bytes, trainers: list[dict], species: dict) -> None:
    """Generate abilities for every difficulty and battle side."""
    rule, resolver, spreads = _prepare(block, data)
    for trainer in trainers:
        bits = _bits(block, data, trainer)
        for member in trainer["party"]:
            contexts = {}
            slot = member["species"]
            slots = _slots(data, species, slot)
            for difficulty in range(len(rule["difficulties"])):
                options = resolver.spread_options(trainer, member, slot, difficulty, None)
                if options is None:
                    raise ValueError(f"trainer {trainer['id']}: missing spread")
                for side in (0, 1):
                    contexts[f"{difficulty}/{side}"] = resolve(
                        rule, trainer["id"], slot, difficulty, bits[side],
                        [spread for spread, condition in options], spreads, slots) if slot else []
            member["abilities"] = contexts


def annotate_changes(block: dict, data: bytes, trainers: list[dict], species: dict, doc: dict) -> None:
    """Abilities of each built species: its slots, chosen by the steps that read the listed record."""
    rule, resolver, spreads = _prepare(block, data)
    stages = {row["id"]: row["stage"] for row in doc.get("species_rules", [])}
    trainers = {trainer["id"]: trainer for trainer in trainers}
    for row in doc["members"]:
        if "species_changes" not in row:
            continue
        trainer = trainers[row["trainer"]]
        member = next(mon for mon in trainer["party"] if mon["index"] == row["slot"])
        side = 0 if trainer.get("kind") == "partners" else 1
        bit = _bits(block, data, trainer)[side]
        for change in row["species_changes"]:
            source = change.get("from", member["species"])
            seen = iv.stage_species(stages[change["rule"]], source, change["species"])[0]
            slots = _slots(data, species, change["species"])
            contexts = {}
            for difficulty in change["difficulties"]:
                options = resolver.spread_options(trainer, member, seen, difficulty, None)
                if options is None:
                    raise ValueError(f"trainer {trainer['id']}: missing spread")
                contexts[str(difficulty)] = resolve(rule, trainer["id"], seen, difficulty, bit,
                                                    [spread for spread, condition in options], spreads, slots)
            change["abilities"] = contexts


def verify(block: dict, trainers: list[dict], abilities: list[dict]) -> list[str]:
    """Reject missing contexts and invalid ability identities."""
    keys = {f"{difficulty}/{side}" for difficulty in range(len(block["difficulties"])) for side in (0, 1)}
    valid = {row["id"] for row in abilities if row["id"]}
    for trainer in trainers:
        for member in trainer["party"]:
            contexts = member.get("abilities")
            if not isinstance(contexts, dict) or set(contexts) != keys:
                return ["trainer abilities: regenerate difficulty contexts"]
            for ids in contexts.values():
                if (not isinstance(ids, list) or any(type(value) is not int or value not in valid for value in ids)
                        or ids != sorted(set(ids))):
                    return ["trainer abilities: invalid ability alternatives"]
    return []
