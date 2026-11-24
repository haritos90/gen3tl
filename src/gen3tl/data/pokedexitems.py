"""Extract item facts with profile-defined effect labels."""

from __future__ import annotations

import math
import re

from ..core.notes import has_prose
from ..core.rom import RomEvidence, value_at

FIELDS = ("price", "held_effect", "hold_effect_param", "pocket", "battle_usage")
POINTER_LOW, POINTER_HIGH = 0x08000000, 0x0A000000


def join_rewards(items: list[dict]) -> None:
    """Link exchange costs and group scripted rewards."""
    by_id = {item["id"]: item for item in items}
    gifts = {item["id"]: [source for source in item.get("sources", []) if source["kind"] == "gift"]
             for item in items}
    exchanges = []
    for item in items:
        for use in item.get("uses", []):
            if use["kind"] == "exchange" and use not in exchanges:
                exchanges.append(use)
    for use in exchanges:
        for give in use["gives"]:
            if "item" not in give:
                continue
            item = by_id[give["item"]]
            sources = item.setdefault("sources", [])
            for place in use["places"]:
                matches = [source for source in gifts[item["id"]] if source["map"] == place["map"]
                           and source.get("script") in place["scripts"]]
                for source in matches:
                    if source in sources:
                        sources.remove(source)
                locations = matches or [{"map": place["map"], "map_section": place["map_section"]}]
                for location in locations:
                    source = {**location, **{key: use[key] for key in ("takes", "gives", "choice", "speaker", "conditions")
                                            if key in use}, "kind": "exchange"}
                    if "quantity" in give:
                        source["quantity"] = give["quantity"]
                    if source not in sources:
                        sources.append(source)
    groups = {}
    for item in items:
        for source in item.get("sources", []):
            if source["kind"] == "gift" and "script" in source:
                key = (source["map"], source["script"], source.get("description"),
                       source.get("description_evidence"), tuple(source.get("related_sections", [])))
                groups.setdefault(key, []).append((item, source))
    for rows in groups.values():
        choices = sorted({(item["id"], source["quantity"]) for item, source in rows})
        if len(rows) < 2:
            continue
        source = {key: value for key, value in rows[0][1].items() if key not in ("quantity", "x", "y")}
        source["choices"] = [{"item": item, "quantity": quantity} for item, quantity in choices]
        source["positions"] = [{"x": x, "y": y} for x, y in sorted({(row["x"], row["y"])
                                for _, row in rows if "x" in row and "y" in row})]
        for item, old in rows:
            item["sources"].remove(old)
        for item_id in dict.fromkeys(item for item, _ in choices):
            by_id[item_id]["sources"].append(source)
    order = ("shop", "ground", "hidden", "gift", "exchange", "mining")
    for item in items:
        item.get("sources", []).sort(key=lambda source: order.index(source["kind"]) if source["kind"] in order
                                     else len(order))


def _field(spec) -> tuple[int, int]:
    if isinstance(spec, dict):
        return int(spec["offset"]), int(spec.get("width", 1))
    return int(spec), 1


def _field_value(data: bytes, at: int, spec) -> int | None:
    offset, width = _field(spec)
    return value_at(data, at + offset, width)


def check(block: dict) -> list[str]:
    """Problems in an item block; an empty list means it can be extracted.

    Held effect amounts, keyed by effect, effect.parameter or item.id for an
    overridden item, replace the label with one whose holes carry the code
    amounts; ROM bytes guard them."""
    problems = [key for key in ("base", "stride", "count") if key not in block]
    if not has_prose(block.get("evidence", "")):
        problems.append("evidence")
    fields = block.get("fields") or {}
    problems += [f"fields {name}" for name in FIELDS if name not in fields]
    if "description" not in fields:
        problems.append("fields description")
    if "sell_price" in block:
        rule = block["sell_price"]
        if not isinstance(rule, dict):
            problems.append("sell_price block")
        else:
            if not has_prose(rule.get("evidence", "")):
                problems.append("sell_price evidence")
            if type(rule.get("divisor")) is not int or rule["divisor"] <= 0:
                problems.append("sell_price divisor")
            if type(rule.get("minimum_price")) is not int or rule["minimum_price"] < 0:
                problems.append("sell_price minimum_price")
            if rule.get("rounding") != "floor":
                problems.append("sell_price rounding")
            pockets = rule.get("pockets")
            if (not isinstance(pockets, list) or not pockets
                    or any(type(value) is not int or not 0 <= value <= 255 for value in pockets)):
                problems.append("sell_price pockets")
    for name in ("held_effects", "battle_usages"):
        if name not in block:
            continue
        facts = block[name]
        if not isinstance(facts, dict):
            problems.append(f"{name} block")
            continue
        if not has_prose(facts.get("evidence", "")):
            problems.append(f"{name} evidence")
        if not _names(facts.get("names"), pointer=name == "battle_usages"):
            problems.append(f"{name} names")
    held = block.get("held_effects")
    if isinstance(held, dict):
        types = held.get("type_parameters", [])
        if not isinstance(types, list) or any(type(n) is not int or not 0 <= n <= 255 for n in types):
            problems.append("held_effects type_parameters")
        variants = held.get("variants", {})
        if not isinstance(variants, dict) or any(
                not str(effect).isdigit() or not 0 <= int(effect) <= 255 or not _names(names)
                for effect, names in variants.items()):
            problems.append("held_effects variants")
        overrides = held.get("overrides", {})
        if not isinstance(overrides, dict) or any(
                not str(item).isdigit() or not isinstance(spec, dict)
                or type(spec.get("effect")) is not int or not 0 <= spec["effect"] <= 255
                or not isinstance(spec.get("key"), str) or not spec["key"].strip()
                or type(spec.get("type_parameter", False)) is not bool
                for item, spec in overrides.items()):
            problems.append("held_effects overrides")
        amounts = held.get("amounts", {})
        if not isinstance(amounts, dict):
            problems.append("held_effects amounts")
            amounts = {}
        for number, spec in amounts.items():
            problems += [f"held_effects amounts {number} {problem}" for problem in _amount_check(number, spec)]
    battle = block.get("battle_usages")
    if isinstance(battle, dict):
        field = battle.get("field")
        if (not isinstance(field, dict) or type(field.get("offset")) is not int
                or field["offset"] < 0 or field.get("width") != 4
                or field["offset"] + 4 > int(block.get("stride", 0))):
            problems.append("battle_usages field")
    tm = block.get("tm_moves")
    if tm:
        problems += [f"tm_moves {key}" for key in ("base", "count") if key not in tm]
        if not has_prose(tm.get("evidence", "")):
            problems.append("tm_moves evidence")
        ranges = tm.get("ranges") or []
        if not ranges:
            problems.append("tm_moves ranges")
        elif any(len(r) != 3 for r in ranges) or sum(int(r[1]) for r in ranges) != int(tm["count"]):
            problems.append("tm_moves ranges cover")
    if "sources" in block:
        sources = block["sources"]
        if not isinstance(sources, dict):
            problems.append("sources block")
        else:
            problems += [f"sources {problem}" for problem in _source_check(sources, int(block["count"]))]
    if "uses" in block:
        from . import pokedexuses
        problems += [f"uses {problem}" for problem in pokedexuses.check(block["uses"], int(block.get("count", 0)))]
    if "mining" in block:
        mining = block["mining"]
        if not isinstance(mining, dict):
            problems.append("mining block")
        else:
            problems += [f"mining {problem}" for problem in _mining_check(mining)]
    return problems


def _amount_check(number, spec) -> list[str]:
    if not (re.fullmatch(r"item\.(0|[1-9][0-9]*)", str(number))
            or re.fullmatch(r"(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*))?", str(number)) and int(str(number).split(".")[0]) <= 255):
        return ["id"]
    if not isinstance(spec, dict):
        return ["table"]
    problems = []
    key, values = spec.get("key"), spec.get("values")
    if (not isinstance(values, dict)
            or any(not re.fullmatch(r"[a-z]+", str(name)) or name in ("n", "type")
                   or type(value) not in (int, float) or value <= 0 for name, value in values.items())):
        problems.append("values")
        values = {}
    holes = set(re.findall(r"\{([a-z]+)\}", key)) if isinstance(key, str) else set()
    if not isinstance(key, str) or not key.strip() or not set(values) <= holes or not holes <= set(values) | {"n", "type"}:
        problems.append("key")
    conditions = spec.get("conditions", [])
    if (not isinstance(conditions, list)
            or any(not isinstance(key, str) or not key.strip() or "{" in key or "}" in key
                   for key in conditions)):
        problems.append("conditions")
    if "levels" in spec or "upgrade_sources" in spec:
        levels = spec.get("levels")
        if (not isinstance(levels, list) or not levels
                or any(not isinstance(row, dict) or type(row.get("level")) is not int
                       or row["level"] < 1 or not isinstance(row.get("values"), dict)
                       or set(row["values"]) != set(values)
                       or any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0
                              for value in row["values"].values()) for row in levels)):
            problems.append("levels")
        elif ([row["level"] for row in levels] != list(range(levels[0]["level"], levels[0]["level"] + len(levels)))
              or levels[0]["values"] != values):
            problems.append("levels")
        sources = spec.get("upgrade_sources")
        if (not isinstance(sources, list) or not sources
                or any(not isinstance(row, dict) or not isinstance(row.get("key"), str)
                       or not row["key"].strip() or "{" in row["key"] or "}" in row["key"]
                       or not isinstance(row.get("map_sections"), list) or not row["map_sections"]
                       or any(type(section) is not int or section < 0 for section in row["map_sections"])
                       or len(set(row["map_sections"])) != len(row["map_sections"]) for row in sources)):
            problems.append("upgrade_sources")
    if not has_prose(spec.get("evidence")):
        problems.append("evidence")
    proofs = spec.get("rom")
    if not isinstance(proofs, list) or not proofs or any(
            not RomEvidence.valid(proof)
            for proof in proofs):
        problems.append("rom")
    return problems


def amount_record(spec: dict) -> dict:
    """Keep declared levels, sources and conditions."""
    record = {"key": spec["key"], "values": dict(spec["values"])}
    if spec.get("conditions"):
        record["conditions"] = list(spec["conditions"])
    if spec.get("levels"):
        record["levels"] = [{"level": row["level"], "values": dict(row["values"])} for row in spec["levels"]]
        record["upgrade_sources"] = [{"key": row["key"], "map_sections": list(row["map_sections"])}
                                     for row in spec["upgrade_sources"]]
    return record


def _check_amount_proofs(held: dict, data: bytes) -> None:
    for number, spec in held.get("amounts", {}).items():
        for proof in spec["rom"]:
            at = proof["offset"]
            if not RomEvidence.parse(proof).matches(data):
                raise ValueError(f"held effect {number} amount evidence differs at 0x{at:x}")


def _names(names, *, pointer=False) -> bool:
    if not isinstance(names, dict):
        return False
    for number, key in names.items():
        try:
            value = int(number, 0) if pointer else int(number)
        except (TypeError, ValueError):
            return False
        if not (POINTER_LOW <= value < POINTER_HIGH if pointer else 0 <= value <= 255):
            return False
        if not isinstance(key, str) or not key.strip():
            return False
    return True


def _source_check(block: dict, count: int) -> list[str]:
    problems = []
    if not has_prose(block.get("evidence", "")):
        problems.append("evidence")
    shops = block.get("shops")
    if not isinstance(shops, list):
        problems.append("shops")
        shops = []
    seen = set()
    for index, row in enumerate(shops):
        valid = (isinstance(row, dict)
                 and all(type(row.get(key)) is int and 0 <= row[key] <= (255 if key == "map_section" else 0xFFFFFFFF)
                         for key in ("map_section", "offset", "list"))
                 and isinstance(row.get("map"), str) and bool(row["map"])
                 and isinstance(row.get("items"), list) and bool(row["items"])
                 and all(type(item) is int and 0 < item < count for item in row["items"]))
        if not valid:
            problems.append(f"shops[{index}]")
            continue
        key = (row["offset"], row["map"])
        if key in seen:
            problems.append(f"shops[{index}] duplicate")
        seen.add(key)
    if "pickups" in block:
        from . import pokedexpickups
        problems += [f"pickups {problem}" for problem in pokedexpickups.check(block["pickups"])]
    return problems


def _source_records(block: dict | None, count: int) -> dict[int, list[dict]]:
    if not block:
        return {}
    out = {item: [] for item in range(count)}
    for shop in block["shops"]:
        for item in shop["items"]:
            if not 0 <= item < count:
                raise ValueError(f"item source: invalid shop item {item}")
            source = {"kind": "shop", "map": shop["map"], "map_section": shop["map_section"]}
            if source not in out[item]:
                out[item].append(source)
    return out


def _mining_check(block: dict) -> list[str]:
    problems = []
    if not has_prose(block.get("evidence", "")):
        problems.append("evidence")
    if (type(block.get("base")) is not int or block["base"] < 0
            or any(type(block.get(key)) is not int or block[key] <= 0
                   for key in ("stride", "count", "draw_count"))):
        problems.append("layout")
    item = block.get("item")
    if (not isinstance(item, dict) or type(item.get("offset")) is not int or item["offset"] < 0
            or type(item.get("width")) is not int or item["width"] <= 0
            or item["offset"] + item["width"] > int(block.get("stride", 0))):
        problems.append("item")
    draws = block.get("draws")
    if (not isinstance(draws, list) or not draws
            or any(type(base) is not int or base < 0 for base in draws)):
        problems.append("draws")
    count = block["count"] if type(block.get("count")) is int else 0
    tiers = block.get("tiers", [])
    if (not isinstance(tiers, list) or any(
            not isinstance(tier, list) or len(tier) != 2
            or any(type(value) is not int for value in tier)
            or not 0 <= tier[0] < tier[1] <= count for tier in tiers)):
        problems.append("tiers")
    return problems


def _mining_records(block: dict, data: bytes, count: int) -> dict[int, list[dict]]:
    out = {item: [] for item in range(count)}
    draw_count = int(block["draw_count"])
    tiers = {start: stop for start, stop in block.get("tiers", [])}
    for base in block["draws"]:
        draws = data[base:base + draw_count]
        if len(draws) != draw_count:
            raise ValueError("mining source: truncated draw table")
        chances = {}
        for index in dict.fromkeys(draws):
            if not 0 <= index < int(block["count"]):
                raise ValueError(f"mining source: invalid table index {index}")
            stop = tiers.get(index, index + 1)
            share = 100 * draws.count(index) / draw_count / (stop - index)
            for target in range(index, stop):
                chances[target] = chances.get(target, 0) + share
        for index, chance in chances.items():
            start = int(block["base"]) + index * int(block["stride"])
            item = _field_value(data, start, block["item"])
            if not 0 < item < count:
                raise ValueError(f"mining source: invalid item {item}")
            source = {"kind": "mining", "chance": round(chance, 1)}
            if source not in out[item]:
                out[item].append(source)
    return out


def _effect_names(block: dict, record: dict, data: bytes, start: int, item: int) -> None:
    held = block.get("held_effects", {})
    effect = record["held_effect"]
    parameter = record["hold_effect_param"]
    key = held.get("names", {}).get(str(effect)) if effect else None
    key = held.get("variants", {}).get(str(effect), {}).get(str(parameter), key)
    typed = effect in held.get("type_parameters", [])
    override = held.get("overrides", {}).get(str(item))
    if override and override["effect"] == effect:
        key = override["key"]
        typed = override.get("type_parameter", False)
    if key:
        record["held_effect_key"] = key
        if typed:
            record["held_effect_param_name"] = f"types:{parameter}"
        amounts = held.get("amounts", {})
        amount = (amounts.get(f"item.{item}") if override and override["effect"] == effect
                  else amounts.get(f"{effect}.{parameter}", amounts.get(str(effect))))
        if amount:
            record["held_effect_amount"] = amount_record(amount)
    battle = block.get("battle_usages")
    if battle:
        pointer = _field_value(data, start, battle["field"])
        record["battle_use_function"] = pointer
        names = {int(address, 0): key for address, key in battle["names"].items()}
        if record["battle_usage"] and pointer in names:
            record["battle_usage_key"] = names[pointer]


def _tm_index(block: dict, item: int) -> int | None:
    for first, count, item_first in block.get("ranges", []):
        if item_first <= item < item_first + count:
            return first + item - item_first
    return None


def sell_price(rule: dict, record: dict) -> int | None:
    """Derive the base unit payout from a declared sale rule."""
    price, pocket = record.get("price"), record.get("pocket")
    if (type(price) is not int or price < rule["minimum_price"]
            or type(pocket) is not int or pocket not in rule["pockets"]):
        return None
    return price // rule["divisor"]


def build(block: dict, data: bytes) -> list[dict]:
    """Extract item records in item-id order."""
    fields = block["fields"]
    tm = block.get("tm_moves") or {}
    count = int(block["count"])
    _check_amount_proofs(block.get("held_effects", {}), data)
    sources = _source_records(block.get("sources"), count)
    if "mining" in block:
        for item, rows in _mining_records(block["mining"], data, count).items():
            sources.setdefault(item, []).extend(rows)
    out = []
    for item in range(int(block.get("first", 0)), count):
        start = int(block["base"]) + item * int(block["stride"])
        description = None
        desc_offset, desc_width = _field(fields["description"])
        pointer = _field_value(data, start, {"offset": desc_offset, "width": desc_width})
        if pointer and POINTER_LOW <= pointer < POINTER_HIGH:
            description = f"s{pointer - POINTER_LOW:07X}"
        tm_index = _tm_index(tm, item) if tm else None
        taught_move = None
        if tm_index is not None:
            at = int(tm["base"]) + tm_index * int(tm.get("stride", 2))
            taught_move = int.from_bytes(data[at:at + int(tm.get("stride", 2))], "little")
        record = {name: _field_value(data, start, fields[name]) for name in FIELDS}
        if "sell_price" in block:
            record["sell_price"] = sell_price(block["sell_price"], record)
        _effect_names(block, record, data, start, item)
        name = f"{block.get('name_table', 'items')}:{item}"
        if block.get("long_name_pointers"):
            pointer = int.from_bytes(data[start:start + 4], "little")
            if POINTER_LOW <= pointer < POINTER_HIGH:
                name = f"s{pointer - POINTER_LOW:07X}"
        record.update({
            "id": item,
            "offset": f"0x{start:06x}",
            "name": name,
            "description": description,
            "tm_index": tm_index,
            "taught_move": taught_move,
        })
        if "sources" in block or "mining" in block:
            record["sources"] = sources[item]
        out.append(record)
    return out
