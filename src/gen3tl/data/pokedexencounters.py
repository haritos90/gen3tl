"""Wild encounter tables with map aliases and per-slot percentages."""

from __future__ import annotations

import re

from ..core.notes import has_prose

VARIANTS = ("day", "morning", "evening", "night")
ROM_BASE = 0x08000000


def _value(data: bytes, at: int, width: int = 4) -> int | None:
    if at < 0 or at + width > len(data):
        return None
    return int.from_bytes(data[at:at + width], "little")


def _pointer(data: bytes, at: int) -> int | None:
    value = _value(data, at)
    if value is None or not ROM_BASE <= value < ROM_BASE + len(data):
        return None
    return value - ROM_BASE


def check(block: dict | None) -> list[str]:
    """Configuration problems that would make encounter extraction unproven."""
    if not block:
        return ["block"]
    problems = []
    if not has_prose(block.get("evidence", "")):
        problems.append("evidence")
    headers = block.get("headers") or {}
    if "stride" not in headers:
        problems.append("headers stride")
    if "day" not in headers:
        problems.append("headers day")
    for name in VARIANTS:
        if name not in headers:
            continue
        for key in ("base", "count"):
            if key not in headers[name]:
                problems.append(f"headers {name} {key}")
    methods = block.get("methods") or {}
    for name in ("land", "water", "rock_smash", "fishing"):
        if name not in methods:
            problems.append(f"methods {name}")
            continue
        for key in ("pointer", "slots"):
            if key not in methods[name]:
                problems.append(f"methods {name} {key}")
        if name != "fishing" and len(methods[name].get("rates", [])) != methods[name].get("slots"):
            problems.append(f"methods {name} rates")
    for rod in ("old", "good", "super"):
        row = (block.get("fishing") or {}).get(rod) or {}
        if "first" not in row or not row.get("rates"):
            problems.append(f"fishing {rod}")
        elif row["first"] + len(row["rates"]) > methods.get("fishing", {}).get("slots", 0):
            problems.append(f"fishing {rod} rates")
    context = block.get("context") or {}
    for key in ("groups", "engine_groups", "engine_count", "evidence"):
        if key not in context:
            problems.append(f"context {key}")
    for row in block.get("unavailable", []):
        if not row.get("kind") or not row.get("reason"):
            problems.append("unavailable kind/reason")
    problems += _check_method_labels(block)
    return problems


def _check_method_labels(block: dict) -> list[str]:
    declarations = block.get("method_labels", [])
    if not isinstance(declarations, list):
        return ["method_labels"]
    if not declarations and "trigger_tiles" not in block:
        return []
    problems = []
    tiles = block.get("trigger_tiles", {})
    if not isinstance(tiles, dict):
        return ["trigger_tiles"]
    if type(tiles.get("primary_metatiles")) is not int or not 0 < tiles["primary_metatiles"] < 1024:
        problems.append("trigger_tiles primary_metatiles")
    if not has_prose(tiles.get("evidence")):
        problems.append("trigger_tiles evidence")
    for field in ("water_walk_behaviors", "water_surf_behaviors"):
        if not _behaviors(tiles.get(field), allow_empty=True):
            problems.append(f"trigger_tiles {field}")
    required = tiles.get("required_methods", [])
    if not isinstance(required, list) or any(method not in ("land", "water") for method in required):
        problems.append("trigger_tiles required_methods")
    if problems:
        return problems
    seen = set()
    for index, row in enumerate(declarations):
        prefix = f"method_labels {index}"
        if not isinstance(row, dict):
            problems.append(prefix)
            continue
        maps = row.get("maps")
        if not isinstance(maps, list) or not maps or any(
                not isinstance(runtime, str) or not re.fullmatch(r"\d+\.\d+", runtime) for runtime in maps):
            problems.append(f"{prefix} maps")
        else:
            for runtime in maps:
                key = (runtime, str(row.get("method")))
                if key in seen:
                    problems.append(f"{prefix} duplicate {runtime} {row.get('method')}")
                seen.add(key)
        method = row.get("method")
        if method not in ("land", "water"):
            problems.append(f"{prefix} method")
            continue
        labels = row.get("labels")
        if not isinstance(labels, list) or not labels or any(
                not isinstance(label, str) or not label.strip() for label in labels):
            problems.append(f"{prefix} labels")
        if not has_prose(row.get("evidence")):
            problems.append(f"{prefix} evidence")
        triggers = row.get("triggers")
        if not isinstance(triggers, list) or not triggers or not isinstance(labels, list) or len(triggers) != len(labels):
            problems.append(f"{prefix} triggers")
            continue
        for trigger in triggers:
            if not isinstance(trigger, dict) or not _behaviors(trigger.get("behaviors")):
                problems.append(f"{prefix} trigger behaviors")
                continue
            encounter = trigger.get("encounter")
            movement = trigger.get("movement")
            if type(encounter) is not int or encounter != {"land": 1, "water": 2}.get(method):
                problems.append(f"{prefix} trigger encounter")
            if movement not in ("walk", "surf") or method == "land" and movement != "walk":
                problems.append(f"{prefix} trigger movement")
            elif method == "water" and not set(trigger["behaviors"]) <= set(tiles.get(f"water_{movement}_behaviors", [])):
                problems.append(f"{prefix} trigger movement behaviors")
    return problems


def _behaviors(value, *, allow_empty: bool = False) -> bool:
    return (isinstance(value, list) and (allow_empty or bool(value))
            and all(type(item) is int and 0 <= item <= 0x1FF for item in value))


def method_labels(block: dict) -> dict[tuple[str, str], list[str]]:
    """Resolve declared labels without changing table identities."""
    return {(runtime, row["method"]): row["labels"]
            for row in block.get("method_labels", []) for runtime in row["maps"]}


def _validate_triggers(block: dict, data: bytes, context_maps: dict, tables: set) -> None:
    from .pokedexmaps import layout

    declared = method_labels(block)
    for runtime, method in tables:
        if method in block.get("trigger_tiles", {}).get("required_methods", []) and (runtime, method) not in declared:
            raise ValueError(f"{runtime} {method}: missing method label declaration")
    layouts = {}
    for row in block.get("method_labels", []):
        for runtime in row["maps"]:
            method = row["method"]
            prefix = f"{runtime} {method}"
            if (runtime, method) not in tables:
                raise ValueError(f"{prefix}: declared table is absent")
            if runtime not in context_maps:
                raise ValueError(f"{prefix}: trigger map is unresolved")
            if runtime not in layouts:
                lay = layout(data, int(context_maps[runtime]["map_header"], 16))
                attributes = [_pointer(data, lay[key] + 20) for key in ("primary", "secondary")]
                if None in attributes:
                    raise ValueError(f"{prefix}: invalid metatile attribute pointer")
                primary = block["trigger_tiles"]["primary_metatiles"]
                found = set()
                for index in range(lay["width"] * lay["height"]):
                    tile = _value(data, lay["blocks"] + index * 2, 2) & 0x3FF
                    bank, slot = (0, tile) if tile < primary else (1, tile - primary)
                    value = _value(data, attributes[bank] + slot * 4)
                    if value is None:
                        raise ValueError(f"{prefix}: metatile attributes exceed ROM")
                    found.add((value & 0x1FF, value >> 24 & 7))
                layouts[runtime] = found
            found = {behavior for behavior, encounter in layouts[runtime]
                     if (encounter & 1 if method == "land" else encounter & 2 and not encounter & 1)}
            covered = set()
            for trigger in row["triggers"]:
                behaviors = set(trigger["behaviors"])
                if not behaviors <= found:
                    raise ValueError(f"{prefix}: declared trigger is absent from map tiles")
                covered.update(behaviors)
            if method == "water":
                tiles = block["trigger_tiles"]
                candidates = set(tiles["water_walk_behaviors"] + tiles["water_surf_behaviors"])
                if found & candidates != covered:
                    raise ValueError(f"{prefix}: map tiles have an undeclared trigger")


def _legacy_headers(data: bytes, context: dict) -> dict[int, str]:
    groups = int(context["groups"])
    starts = []
    at = groups
    while (start := _pointer(data, at)) is not None:
        starts.append(start)
        at += 4
    ends = sorted(set(starts) | {groups})
    headers = {}
    for group, start in enumerate(starts):
        following = next((end for end in ends if end > start), None)
        for number in range(512):
            at = start + number * 4
            if following is not None and at >= following:
                break
            header = _pointer(data, at)
            if header is None:
                if following is None:
                    break
                continue
            if _pointer(data, header) is not None and _pointer(data, header + 4) is not None:
                headers.setdefault(header, f"{group}.{number}")
    return headers


def _runtime_banks(data: bytes, context: dict) -> tuple[list[int], list[int]]:
    table = int(context["engine_groups"])
    banks = []
    for group in range(int(context["engine_count"])):
        encoded = _value(data, table + group * 4)
        if encoded is None:
            raise ValueError("runtime map table exceeds ROM")
        bank = (encoded ^ int(context.get("bank_xor", 0))) - ROM_BASE
        if not 0 <= bank <= len(data) - 4 or bank % 4:
            raise ValueError(f"invalid runtime map bank {group}")
        banks.append(bank)
    return banks, sorted(set(banks) | {table, int(context["groups"]), len(data)})


def _runtime_word(data: bytes, context: dict, runtime: str) -> int | None:
    group, number = (int(part) for part in runtime.split("."))
    banks, ends = _runtime_banks(data, context)
    if not 0 <= group < len(banks) or number < 0:
        return None
    bank = banks[group]
    end = next(end for end in ends if end > bank)
    if number >= min(256, (end - bank) // 4):
        return None
    return _value(data, bank + number * 4)


def _runtime_headers(data: bytes, context: dict) -> dict[str, int]:
    banks, ends = _runtime_banks(data, context)
    headers = {}
    for group, bank in enumerate(banks):
        end = next(end for end in ends if end > bank)
        for number in range(min(256, (end - bank) // 4)):
            header = _pointer(data, bank + number * 4)
            if header is None:
                continue
            headers[f"{group}.{number}"] = header
    return headers


def _structural_headers(data: bytes, known: set[int]) -> dict[int, str]:
    """Context ids for headers no bank table names."""
    def layout_ok(at):
        if at is None or at % 4:
            return False
        width = _value(data, at)
        height = _value(data, at + 4)
        return (width is not None and height is not None and 1 <= width <= 512 and 1 <= height <= 512
                and all(_pointer(data, at + 8 + 4 * i) is not None for i in range(4)))

    def events_ok(at):
        if at is None or at % 4:
            return False
        for i in range(4):
            count = _value(data, at + i, 1)
            pointer = _value(data, at + 4 + i * 4)
            if (count or pointer) and _pointer(data, at + 4 + i * 4) is None:
                return False
        return True

    headers = {}
    for match in re.finditer(rb"[\x08\x09]", data):
        at = match.start() - 3
        if (at >= 0 and at % 4 == 0 and at not in known and layout_ok(_pointer(data, at))
                and events_ok(_pointer(data, at + 4))
                and (_pointer(data, at + 8) is not None or data[at + 8:at + 12] == b"\0\0\0\0")):
            headers[at] = f"h{at:07X}"
    return headers


def _context_maps(data: bytes, context: dict) -> dict[str, dict]:
    legacy = _legacy_headers(data, context)
    legacy.update(_structural_headers(data, set(legacy)))
    out = {}
    for runtime, header in _runtime_headers(data, context).items():
        if header in legacy:
            out[runtime] = {
                "map": legacy[header],
                "map_section": _value(data, header + 20, 1),
                "map_header": f"0x{header:06x}",
            }
    return out


def build(block: dict, data: bytes, species_count: int) -> dict:
    """Extract raw variants and effective times for every configured encounter slot."""
    problems = check(block)
    if problems:
        raise ValueError("invalid encounter configuration: " + ", ".join(problems))
    context_maps = _context_maps(data, block["context"])
    records = []
    coverage = {}
    unavailable = [dict(row) for row in block.get("unavailable", [])]
    missing = {(row["kind"], row.get("id")) for row in unavailable}
    header_block = block["headers"]
    stride = int(header_block.get("stride", 20))
    for variant in VARIANTS:
        if variant not in header_block:
            continue
        table = header_block[variant]
        for index in range(int(table["count"])):
            header = int(table["base"]) + index * stride
            group, number = _value(data, header, 1), _value(data, header + 1, 1)
            if group == 0xFF:
                raise ValueError(f"{variant} header {index} ends before its configured count")
            runtime = f"{group}.{number}"
            map_info = context_maps.get(runtime)
            map_error = None
            if map_info is None:
                word = _runtime_word(data, block["context"], runtime)
                map_error = ("runtime-map-invalid" if word is not None and not ROM_BASE <= word < ROM_BASE + len(data)
                             else "runtime-map-unindexed")
                if ("map", runtime) not in missing:
                    unavailable.append({"kind": "map", "id": runtime, "reason": map_error})
                    missing.add(("map", runtime))
            for method, config in block["methods"].items():
                info = _pointer(data, header + int(config["pointer"]))
                if info is None:
                    continue
                coverage.setdefault((runtime, method), set()).add(variant)
                encounter_rate = _value(data, info, 1)
                entries = _pointer(data, info + 4)
                if encounter_rate is None or entries is None:
                    raise ValueError(f"{variant} {runtime} {method}: invalid info pointer")
                choices = []
                if method == "fishing":
                    for rod, rule in block["fishing"].items():
                        choices += [(slot, percentage, rod) for slot, percentage in
                                    enumerate(rule["rates"], start=int(rule["first"]))]
                else:
                    choices = list(enumerate(config["rates"]))
                for slot, percentage, *rod in choices:
                    at = entries + slot * 4
                    min_level = _value(data, at, 1)
                    max_level = _value(data, at + 1, 1)
                    species = _value(data, at + 2, 2)
                    if min_level is None or max_level is None or species is None:
                        raise ValueError(f"{variant} {runtime} {method} slot {slot}: entry exceeds ROM")
                    if not 0 < species < species_count:
                        raise ValueError(f"{variant} {runtime} {method} slot {slot}: invalid species {species}")
                    records.append({
                        "map": map_info["map"] if map_info else None,
                        "runtime_map": runtime,
                        "map_section": map_info["map_section"] if map_info else None,
                        "map_header": map_info["map_header"] if map_info else None,
                        "map_error": map_error,
                        "variant": variant,
                        "method": method,
                        "rod": rod[0] if rod else None,
                        "slot": slot,
                        "species": species,
                        "min_level": min_level,
                        "max_level": max_level,
                        "percentage": percentage,
                        "encounter_rate": encounter_rate,
                        "header": f"0x{header:06x}",
                        "offset": f"0x{at:06x}",
                    })
        terminator = int(table["base"]) + int(table["count"]) * stride
        if _value(data, terminator, 1) != 0xFF:
            raise ValueError(f"{variant}: configured count has no header terminator")
    _validate_triggers(block, data, context_maps, set(coverage))
    labels = method_labels(block)
    for row in records:
        if (row["runtime_map"], row["method"]) in labels:
            row["method_labels"] = labels[row["runtime_map"], row["method"]]
        covered = coverage.get((row["runtime_map"], row["method"]), set())
        row["variants"] = (["day"] + [variant for variant in VARIANTS[1:]
                                     if variant in header_block and variant not in covered]
                           if row["variant"] == "day" else [row["variant"]])
    return {"records": records, "unavailable": unavailable}
