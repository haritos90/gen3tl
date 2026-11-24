"""Move records for the Pokedex dataset: numeric fields, keys and the code that reads them."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from ..core.notes import has_prose
from ..core.rom import Rom, RomEvidence, value_at
from ..discover.maps import VAR_FIRST, Walker

FIELDS = ("effect", "power", "type", "accuracy", "pp", "secondary_chance",
          "target", "priority", "flags", "z_move_power", "damage_category")
TABLES = ("targets", "effects", "flag_bits")
KEY = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*")
MECHANICS_KEY = re.compile(r"page:mechanics:[a-z0-9_]+(?::[a-z0-9_]+)*")
ENVIRONMENT_COLUMNS = ("effect", "move", "type")
POINTER_LOW, POINTER_HIGH = 0x08000000, 0x0A000000


def _field(spec) -> tuple[int, int, bool]:
    if isinstance(spec, dict):
        return int(spec["offset"]), int(spec.get("width", 1)), bool(spec.get("signed"))
    return int(spec), 1, False


def _field_value(data: bytes, at: int, spec) -> int | None:
    offset, width, signed = _field(spec)
    return value_at(data, at + offset, width, signed)


def _table(block: dict, name: str) -> dict[int, str]:
    return {int(number): key for number, key in (block.get(name) or {}).items()
            if number != "evidence"}


def _check_item_tiers(table: dict) -> list[str]:
    if not isinstance(table, dict):
        return ["table"]
    problems = [key for key in ("base", "count", "stride", "terminator")
                if type(table.get(key)) is not int or table[key] < (1 if key in ("count", "stride") else 0)]
    if not re.fullmatch(r"[0-9a-f]{64}", str(table.get("sha256", ""))):
        problems.append("sha256")
    fields = table.get("fields")
    for key in ("item", "type", "power"):
        spec = fields.get(key) if isinstance(fields, dict) else None
        offset = spec.get("offset") if isinstance(spec, dict) else spec
        width = spec.get("width", 1) if isinstance(spec, dict) else 1
        if (type(offset) is not int or offset < 0 or type(width) is not int or width not in (1, 2, 4)
                or "stride" in problems or offset + width > table["stride"]
                or isinstance(spec, dict) and spec.get("signed", False)):
            problems.append(f"fields {key}")
    excluded = table.get("exclude", [])
    if (not isinstance(excluded, list) or any(type(value) is not int or value < 0 for value in excluded)
            or len(set(excluded)) != len(excluded)):
        problems.append("exclude")
    return problems


def _item_tiers(table: dict, data: bytes) -> list[dict]:
    start, count, stride = table["base"], table["count"], table["stride"]
    end = start + (count + 1) * stride
    if end > len(data):
        raise ValueError(f"item power table truncated at 0x{start:x}")
    if hashlib.sha256(data[start:end]).hexdigest() != table["sha256"]:
        raise ValueError(f"item power table evidence differs at 0x{start:x}")
    fields = table["fields"]
    if _field_value(data, end - stride, fields["item"]) != table["terminator"]:
        raise ValueError(f"item power table terminator differs at 0x{end - stride:x}")
    rows = [{key: _field_value(data, start + index * stride, fields[key]) for key in ("item", "type", "power")}
            for index in range(count)]
    ids = {row["item"] for row in rows}
    if len(ids) != count or table["terminator"] in ids or any(row["power"] <= 0 for row in rows):
        raise ValueError(f"item power table has invalid rows at 0x{start:x}")
    excluded = set(table.get("exclude", []))
    if not excluded <= ids:
        raise ValueError(f"item power table has unknown exclusions at 0x{start:x}")
    return [row for row in rows if row["item"] not in excluded]


def _check_flinch(rule: dict, block: dict) -> list[str]:
    if not isinstance(rule, dict):
        return ["flinch table"]
    problems = []
    if rule.get("secondary_comparison") not in ("inclusive", "exclusive"):
        problems.append("flinch secondary_comparison")
    rules = rule.get("rules")
    if not isinstance(rules, list) or not rules:
        problems.append("flinch rules")
        rules = []
    for label, item in [("flinch", rule), *[(f"flinch rules {i}", item) for i, item in enumerate(rules)]]:
        if not isinstance(item, dict):
            problems.append(f"{label} table")
            continue
        if not has_prose(item.get("evidence")):
            problems.append(f"{label} evidence")
        proofs = item.get("rom")
        if not isinstance(proofs, list) or not proofs:
            problems.append(f"{label} rom")
        else:
            for proof in proofs:
                if not RomEvidence.valid(proof):
                    problems.append(f"{label} rom")
        conditions = item.get("conditions", [])
        if (not isinstance(conditions, list)
                or any(not isinstance(key, str) or not MECHANICS_KEY.fullmatch(key) for key in conditions)):
            problems.append(f"{label} conditions")
        elif len(set(conditions)) != len(conditions):
            problems.append(f"{label} conditions duplicates")
        if label == "flinch":
            continue
        chance = item.get("chance")
        if chance != "secondary" and not (type(chance) is int and 1 <= chance <= 100):
            problems.append(f"{label} chance")
        for field in ("effects", "moves"):
            if field == "moves" and field not in item:
                continue
            ids = item.get(field)
            if (not isinstance(ids, list) or not ids
                    or any(type(value) is not int or (str(value) not in block.get("effects", {}) if field == "effects"
                           else not int(block.get("first", 0)) <= value < int(block.get("count", 0))) for value in ids)):
                problems.append(f"{label} {field}")
            elif len(set(ids)) != len(ids):
                problems.append(f"{label} {field} duplicates")
    return problems


def flinch_fields(rule: dict, record: dict) -> dict:
    """Resolve base flinch chances from declared script paths."""
    matches = [item for item in rule["rules"] if record.get("effect") in item["effects"]
               and ("moves" not in item or record["id"] in item["moves"])]
    if len(matches) > 1:
        raise ValueError(f"move {record['id']} has overlapping flinch rules")
    if not matches:
        return {"flinch_chance": 0, "flinch_conditions": []}
    item = matches[0]
    chance = item["chance"]
    if chance == "secondary":
        stored = record.get("secondary_chance")
        if type(stored) is not int or not 0 <= stored <= 255:
            raise ValueError(f"move {record['id']} has invalid secondary chance")
        chance = min(100, stored + (rule["secondary_comparison"] == "inclusive"))
    conditions = [*rule.get("conditions", []), *item.get("conditions", [])] if chance else []
    return {"flinch_chance": chance, "flinch_conditions": conditions}


def check(block: dict) -> list[str]:
    """Problems in a move block; an empty list means it can be extracted."""


def _valid_proofs(proofs) -> bool:
    if not isinstance(proofs, list) or not proofs:
        return False
    return all(RomEvidence.valid(proof) for proof in proofs)


def _check_environments(table) -> list[str]:
    if not isinstance(table, dict):
        return ["table"]
    problems = [key for key in ("base", "count", "stride")
                if type(table.get(key)) is not int or table[key] < (1 if key in ("count", "stride") else 0)]
    if not re.fullmatch(r"[0-9a-f]{64}", str(table.get("sha256", ""))):
        problems.append("sha256")
    fields = table.get("fields")
    for key in ENVIRONMENT_COLUMNS:
        spec = fields.get(key) if isinstance(fields, dict) else None
        offset = spec.get("offset") if isinstance(spec, dict) else spec
        width = spec.get("width", 1) if isinstance(spec, dict) else 1
        if (type(offset) is not int or offset < 0 or type(width) is not int or width not in (1, 2)
                or "stride" in problems or offset + width > table["stride"]
                or isinstance(spec, dict) and spec.get("signed", False)):
            problems.append(f"fields {key}")
    effects = table.get("effects")
    if (not isinstance(effects, dict) or not effects
            or any(not re.fullmatch(r"0|[1-9][0-9]*", str(value)) or not isinstance(key, str)
                   or not MECHANICS_KEY.fullmatch(key) for value, key in effects.items())):
        problems.append("effects")
    rows = table.get("rows")
    count = table["count"] if "count" not in problems else 0
    if (not isinstance(rows, list) or not rows
            or any(not isinstance(row, dict) or type(row.get("index")) is not int or not 0 <= row["index"] < count
                   or any(not isinstance(row.get(key), str) or not MECHANICS_KEY.fullmatch(row[key])
                          for key in ("environment", "where"))
                   or not isinstance(row.get("sections", []), list)
                   or any(type(value) is not int or value < 0 for value in row.get("sections", []))
                   or len(set(row.get("sections", []))) != len(row.get("sections", []))
                   or not isinstance(row.get("maps", []), list)
                   or any(not isinstance(item, dict) or set(item) != {"map", "section"}
                          or not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", str(item["map"]))
                          or type(item["section"]) is not int or item["section"] < 0 for item in row.get("maps", []))
                   or not isinstance(row.get("scripts", []), list)
                   or any(type(value) is not int or value < 0 for value in row.get("scripts", []))
                   or len(set(row.get("scripts", []))) != len(row.get("scripts", []))
                   or not isinstance(row.get("map_codes", []), list)
                   or any(type(code) is not int or not 0 <= code <= 0xFFFF for code in row.get("map_codes", []))
                   or len(set(row.get("map_codes", []))) != len(row.get("map_codes", []))
                   or row.get("maps") and row.get("map_codes")
                   or "place" in row and (type(row["place"]) is not int or row["place"] < 0)
                   for row in rows)):
        problems.append("rows")
    elif len({row["index"] for row in rows}) != len(rows):
        problems.append("rows duplicates")
    if isinstance(rows, list) and len({code for row in rows if isinstance(row, dict)
                                       and isinstance(row.get("map_codes", []), list)
                                       for code in row.get("map_codes", []) if isinstance(code, int)}) != sum(
            len(row.get("map_codes", [])) for row in rows if isinstance(row, dict)
            if isinstance(row.get("map_codes", []), list)):
        problems.append("rows map codes duplicates")
    if "scenes" in table or "scripts" in table:
        first = table.get("environment_row")
        if type(first) is not int or first < 0 or "count" not in problems and first >= table["count"]:
            problems.append("environment_row")
    if "scenes" in table:
        scenes = table["scenes"]
        if (not isinstance(scenes, dict) or set(scenes) != {"field", "base", "stride", "count", "default"}
                or any(type(scenes[key]) is not int or scenes[key] < (1 if key in ("stride", "count") else 0)
                       for key in scenes)
                or scenes["stride"] < 2):
            problems.append("scenes")
    if "scripts" in table:
        scripts = table["scripts"]
        if (not isinstance(scripts, dict) or set(scripts) not in ({"variable"}, {"variable", "persistence"})
                or type(scripts["variable"]) is not int or not 0x4000 <= scripts["variable"] <= 0xFFFF
                or "persistence" in scripts
                and (not isinstance(scripts["persistence"], str)
                     or not MECHANICS_KEY.fullmatch(scripts["persistence"]))):
            problems.append("scripts")
    if not has_prose(table.get("evidence")):
        problems.append("evidence")
    if not _valid_proofs(table.get("rom")):
        problems.append("rom")
    return problems


VAR_WRITES = {0x16: "setvar", 0x17: "addvar", 0x18: "subvar", 0x19: "copyvar", 0x1A: "setorcopyvar",
              0x26: "specialvar"}


class _VariableWalker(Walker):
    """Map walker keeping the walks that write one script variable."""

    def __init__(self, rom: Rom, profile: dict, variable: int):
        super().__init__(rom, profile)
        self.variable = variable.to_bytes(2, "little")
        self.writes: dict[int, set[tuple[str, str]]] = defaultdict(set)

    def walk_script(self, start, mid, name, event, local_id, sprite):
        seen = super().walk_script(start, mid, name, event, local_id, sprite)
        d = self.d
        for pc in seen:
            if d[pc] in VAR_WRITES and d[pc + 1:pc + 3] == self.variable:
                self.writes[pc].add((mid, event))


def _map_order(item: dict) -> list[int]:
    return [item["section"], *(int(part) for part in item["map"].split("."))]


def environment_maps(table: dict, data: bytes, profile: dict, maps: dict[str, dict]) -> dict[int, dict]:
    """Row index -> the maps whose map code, header scene or scripts choose that row.

    maps: runtime map -> {map_header, map_section} of every verified header.
    A scene selects its row unless the map's own map scripts set a nonzero value."""
    first, shown = table.get("environment_row", 0), {row["index"]: row for row in table["rows"]}
    places: dict[int, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    by_header = defaultdict(list)
    for runtime, info in maps.items():
        by_header[int(info["map_header"], 16)].append((runtime, info["map_section"]))
    for row in table["rows"]:
        for code in row.get("map_codes", []):
            runtime = f"{code & 0xFF}.{code >> 8}"
            if runtime not in maps:
                raise ValueError(f"environment map code 0x{code:04X} has no verified map")
            places[row["index"]]["map_code_maps"].add((runtime, maps[runtime]["map_section"]))
    scripted = set()
    if "scripts" in table:
        walker = _VariableWalker(Rom(data), profile, table["scripts"]["variable"])
        walker.walk_all()
        walker.walk_structural()
        declared = defaultdict(list)
        for row in table["rows"]:
            for value in row.get("scripts", []):
                declared[value].append(row["index"])
        for pc, walks in sorted(walker.writes.items()):
            op = data[pc]
            value = int.from_bytes(data[pc + 3:pc + 5], "little")
            if op not in (0x16, 0x1A) or value >= VAR_FIRST:
                raise ValueError(f"environment variable {VAR_WRITES[op]} at 0x{pc:07X} has no literal value")
            if value == 0:
                continue
            own = value + first
            rows = [(index, "script_maps") for index in declared.get(value, [])]
            if own in shown and own not in declared.get(value, []):
                rows.append((own, "also_script_maps"))
            if not rows:
                raise ValueError(f"environment variable value {value} at 0x{pc:07X} has no row")
            for mid, event in walks:
                header = walker.ctx.headers[mid]
                if event == "mapscript":
                    scripted.add(header)
                for runtime, section in by_header.get(header, []):
                    for index, kind in rows:
                        places[index][kind].add((runtime, section))
    if "scenes" in table:
        scenes = table["scenes"]
        pairs = {}
        for n in range(scenes["count"]):
            at = scenes["base"] + n * scenes["stride"]
            pairs.setdefault(data[at], data[at + 1])
        for header, runtimes in by_header.items():
            scene = data[header + scenes["field"]] if header + scenes["field"] < len(data) else 0
            index = pairs.get(scene, scenes["default"]) + first
            if scene and index in shown and header not in scripted:
                for item in runtimes:
                    places[index]["scene_maps"].add(item)
    return {index: {kind: sorted(({"map": runtime, "section": section} for runtime, section in items),
                                 key=_map_order)
                    for kind, items in sorted(kinds.items())}
            for index, kinds in sorted(places.items())}


def _environments(table: dict, column: str, data: bytes, moves: int, places: dict[int, dict]) -> list[dict]:
    start, stride = table["base"], table["stride"]
    end = start + table["count"] * stride
    if end > len(data):
        raise ValueError(f"environment table truncated at 0x{start:x}")
    if hashlib.sha256(data[start:end]).hexdigest() != table["sha256"]:
        raise ValueError(f"environment table evidence differs at 0x{start:x}")
    effects = {int(value): key for value, key in table["effects"].items()}
    out = []
    for row in table["rows"]:
        value = _field_value(data, start + row["index"] * stride, table["fields"][column])
        if column == "effect":
            if value not in effects:
                raise ValueError(f"environment {row['index']} effect {value} has no key")
            value = effects[value]
        elif column == "move" and not 0 < value < moves:
            raise ValueError(f"environment {row['index']} move {value} is invalid")
        record = {"environment": row["environment"], "where": row["where"], "value": value}
        if "place" in row:
            record["place"] = row["place"]
        record.update({key: row[key] for key in ("sections", "maps") if row.get(key)})
        record.update(places.get(row["index"], {}))
        out.append(record)
    return out


def check(block: dict) -> list[str]:
    """Problems in a move block; an empty list means it can be extracted.

    The [pokedex.moves] block names a fixed-size table and its numeric fields. The
    optional nested descriptions block maps the same move ids to catalog string ids.
    Target, effect and flag tables name the numeric values with language-neutral
    keys. Optional move-id lists add their keys to every member record. Reader
    blocks map flag and list keys to the ability and item ids proven in the code.
    Optional mechanics rows carry page keys guarded by ROM bytes; a mechanics
    block may show one column of the shared, hash-guarded environment table.
    Effect overrides bind move ids and raw effects to byte-checked keys.
    No move name or description text enters the dataset."""
    problems = [key for key in ("base", "stride", "count") if key not in block]
    if not has_prose(block.get("evidence", "")):
        problems.append("evidence")
    if "flinch" in block:
        problems += _check_flinch(block["flinch"], block)
    fields = block.get("fields") or {}
    problems += [f"fields {name}" for name in FIELDS if name not in fields]
    descriptions = block.get("descriptions")
    if descriptions:
        problems += [f"descriptions {key}" for key in ("base", "count") if key not in descriptions]
        if not has_prose(descriptions.get("evidence", "")):
            problems.append("descriptions evidence")
    for name in TABLES:
        table = block.get(name)
        if table is None:
            continue
        if not has_prose(table.get("evidence", "")):
            problems.append(f"{name} evidence")
        for number, key in table.items():
            if number == "evidence":
                continue
            try:
                int(number)
            except (TypeError, ValueError):
                problems.append(f"{name} id {number}")
            if not KEY.fullmatch(str(key)):
                problems.append(f"{name} key {key}")
    for index, item in enumerate(block.get("lists") or []):
        problems += [f"lists {index} {key}" for key in ("key", "base")
                     if not str(item.get(key, "")).strip()]
        if not has_prose(item.get("evidence")):
            problems.append(f"lists {index} evidence")
        if item.get("key") and not KEY.fullmatch(str(item["key"])):
            problems.append(f"lists {index} key {item['key']}")
    overrides = block.get("effect_overrides", {})
    if not isinstance(overrides, dict):
        problems.append("effect_overrides table")
        overrides = {}
    for number, item in overrides.items():
        label = f"effect_overrides {number}"
        if (not re.fullmatch(r"0|[1-9][0-9]*", str(number))
                or not int(block.get("first", 0)) <= int(number) < int(block.get("count", 0))):
            problems.append(f"{label} id")
        if not isinstance(item, dict):
            problems.append(f"{label} table")
            continue
        if type(item.get("effect")) is not int or str(item["effect"]) not in block.get("effects", {}):
            problems.append(f"{label} effect")
        if not isinstance(item.get("key"), str) or not KEY.fullmatch(item["key"]):
            problems.append(f"{label} key")
        if not has_prose(item.get("evidence")):
            problems.append(f"{label} evidence")
        proofs = item.get("rom")
        if not isinstance(proofs, list) or not proofs:
            problems.append(f"{label} rom")
            continue
        for proof in proofs:
            if not RomEvidence.valid(proof):
                problems.append(f"{label} rom")
    for number, item in (block.get("power") or {}).items():
        if not str(number).isdigit() or not 0 <= int(number) < int(block.get("count", 0)):
            problems.append(f"power id {number}")
        if item.get("kind") not in ("fixed", "computed"):
            problems.append(f"power {number} kind")
        if "base" in item and (item.get("kind") != "fixed" or type(item["base"]) is not int or item["base"] <= 0):
            problems.append(f"power {number} base")
        if not re.fullmatch(r"page:power:[a-z0-9_]+", str(item.get("formula", ""))):
            problems.append(f"power {number} formula")
        if not has_prose(item.get("evidence", "")):
            problems.append(f"power {number} evidence")
        if "item_tiers" in item:
            problems += [f"power {number} item_tiers {problem}" for problem in _check_item_tiers(item["item_tiers"])]
        tiers = item.get("tiers")
        if tiers is not None:
            if (not isinstance(tiers, list) or not tiers
                    or any(not isinstance(row, dict)
                           or not re.fullmatch(r"page:power:cond:[a-z0-9_]+", str(row.get("condition", "")))
                           or type(row.get("power")) is not int or row["power"] <= 0 for row in tiers)):
                problems.append(f"power {number} tiers")
            elif len({row["condition"] for row in tiers}) != len(tiers):
                problems.append(f"power {number} tiers duplicates")
        tiers = item.get("weight_tiers")
        if tiers is not None:
            if (not isinstance(tiers, list) or not tiers
                    or any(not isinstance(row, list) or len(row) != 2
                           or any(type(value) is not int or value < 0 for value in row)
                           or row[1] == 0 for row in tiers)):
                problems.append(f"power {number} weight_tiers")
            elif tiers[0][0] != 0 or any(a[0] >= b[0] for a, b in zip(tiers, tiers[1:])):
                problems.append(f"power {number} weight_tiers order")
        for proof in item.get("rom", []):
            if not RomEvidence.valid(proof):
                problems.append(f"power {number} rom")
    if "environments" in block:
        problems += [f"environments {problem}" for problem in _check_environments(block["environments"])]
    mechanics = block.get("mechanics", {})
    if not isinstance(mechanics, dict):
        problems.append("mechanics table")
        mechanics = {}
    for number, item in mechanics.items():
        if (not re.fullmatch(r"0|[1-9][0-9]*", str(number))
                or not 0 <= int(number) < int(block.get("count", 0))):
            problems.append(f"mechanics id {number}")
        if not isinstance(item, dict):
            problems.append(f"mechanics {number} table")
            continue
        if not MECHANICS_KEY.fullmatch(str(item.get("summary", ""))):
            problems.append(f"mechanics {number} summary")
        rows = item.get("rows")
        if (not isinstance(rows, list) or not rows
                or any(not isinstance(row, dict)
                       or any(not isinstance(row.get(key), str) or not MECHANICS_KEY.fullmatch(row[key])
                              for key in ("condition", "effect"))
                       or not isinstance(row.get("notes", []), list)
                       or any(not isinstance(key, str) or not MECHANICS_KEY.fullmatch(key)
                              for key in row.get("notes", []))
                       for row in rows)):
            problems.append(f"mechanics {number} rows")
        elif len({row["condition"] for row in rows}) != len(rows):
            problems.append(f"mechanics {number} rows duplicates")
        if "environments" in item and (item["environments"] not in ENVIRONMENT_COLUMNS
                                       or not isinstance(block.get("environments"), dict)):
            problems.append(f"mechanics {number} environments")
        if not has_prose(item.get("evidence")):
            problems.append(f"mechanics {number} evidence")
        proofs = item.get("rom")
        if not isinstance(proofs, list) or not proofs:
            problems.append(f"mechanics {number} rom")
            continue
        for proof in proofs:
            if not RomEvidence.valid(proof):
                problems.append(f"mechanics {number} rom")
    known = set(_table(block, "flag_bits").values())
    known.update(item["key"] for item in block.get("lists") or [] if item.get("key"))
    seen = set()
    for index, item in enumerate(block.get("readers") or []):
        key = item.get("key")
        if not has_prose(item.get("evidence", "")):
            problems.append(f"readers {index} evidence")
        if not key or not KEY.fullmatch(str(key)) or key not in known or key in seen:
            problems.append(f"readers {index} key {key}")
        seen.add(key)
        for kind in ("abilities", "items"):
            ids = item.get(kind, [])
            if not isinstance(ids, list) or any(isinstance(value, bool) or not isinstance(value, int)
                                                or value < 0 for value in ids):
                problems.append(f"readers {index} {kind}")
            elif len(set(ids)) != len(ids):
                problems.append(f"readers {index} {kind} duplicates")
    return problems


def build_readers(block: dict) -> dict:
    """Reader ids for every flag and list key, including empty sets."""
    known = set(_table(block, "flag_bits").values())
    known.update(item["key"] for item in block.get("lists") or [])
    configured = {item["key"]: item for item in block.get("readers") or []}
    return {key: {"readers": {kind: sorted(configured.get(key, {}).get(kind, []))
                              for kind in ("abilities", "items")}}
            for key in sorted(known)}


def _list_members(block: dict, data: bytes) -> list[tuple[str, set[int]]]:
    lists = []
    for item in block.get("lists") or []:
        at = int(item["base"])
        terminator = int(item.get("terminator", 0xFEFE))
        members = set()
        while at + 2 <= len(data):
            move = int.from_bytes(data[at:at + 2], "little")
            if move == terminator:
                break
            if move >= int(block["count"]):
                raise ValueError(f"move list {item['key']} has invalid id {move} at 0x{at:x}")
            members.add(move)
            at += 2
        else:
            raise ValueError(f"move list {item['key']} has no terminator")
        lists.append((item["key"], members))
    return lists


def build(block: dict, data: bytes, places: dict[int, dict] | None = None) -> list[dict]:
    """Extract numeric move records and their keys in move-id order.

    places: the environment_maps result attached to the environment rows."""
    fields = block["fields"]
    descriptions = block.get("descriptions") or {}
    tables = {name: _table(block, name) for name in TABLES}
    lists = _list_members(block, data)
    formulas = {int(number): item for number, item in (block.get("power") or {}).items()}
    mechanics = {int(number): item for number, item in (block.get("mechanics") or {}).items()}
    overrides = {int(number): item for number, item in block.get("effect_overrides", {}).items()}
    flinch = block.get("flinch")
    if flinch:
        for item in [flinch, *flinch["rules"]]:
            for proof in item["rom"]:
                at = proof["offset"]
                if not RomEvidence.parse(proof).matches(data):
                    raise ValueError(f"flinch evidence differs at 0x{at:x}")
    for number, item in overrides.items():
        for proof in item["rom"]:
            at = proof["offset"]
            if not RomEvidence.parse(proof).matches(data):
                raise ValueError(f"move {number} effect evidence differs at 0x{at:x}")
    for number, item in formulas.items():
        for proof in item.get("rom", []):
            at = proof["offset"]
            if not RomEvidence.parse(proof).matches(data):
                raise ValueError(f"move {number} power evidence differs at 0x{at:x}")
    for number, item in mechanics.items():
        for proof in item["rom"]:
            at = proof["offset"]
            if not RomEvidence.parse(proof).matches(data):
                raise ValueError(f"move {number} mechanics evidence differs at 0x{at:x}")
    if any("environments" in item for item in mechanics.values()):
        for proof in block["environments"]["rom"]:
            at = proof["offset"]
            if not RomEvidence.parse(proof).matches(data):
                raise ValueError(f"environment evidence differs at 0x{at:x}")
    out = []
    for move in range(int(block.get("first", 0)), int(block["count"])):
        start = int(block["base"]) + move * int(block["stride"])
        description = None
        if descriptions and move < int(descriptions["count"]):
            at = int(descriptions["base"]) + move * int(descriptions.get("stride", 4))
            pointer = int.from_bytes(data[at:at + 4], "little")
            if POINTER_LOW <= pointer < POINTER_HIGH:
                description = f"s{pointer - POINTER_LOW:07X}"
        record = {name: _field_value(data, start, fields[name]) for name in FIELDS}
        record.update({
            "id": move,
            "offset": f"0x{start:06x}",
            "name": f"{block.get('name_table', 'moves')}:{move}",
            "description": description,
        })
        for field, name in (("target", "targets"), ("effect", "effects")):
            if tables[name]:
                if record[field] not in tables[name]:
                    raise ValueError(f"move {move} {field} {record[field]} has no key")
                record[f"{field}_key"] = tables[name][record[field]]
        if move in overrides:
            item = overrides[move]
            if record["effect"] != item["effect"]:
                raise ValueError(f"move {move} effect differs from override {item['effect']}")
            record["effect_key"] = item["key"]
        if tables["flag_bits"]:
            mask = sum(1 << bit for bit in tables["flag_bits"])
            if record["flags"] & ~mask:
                raise ValueError(f"move {move} flags 0x{record['flags']:x} have no keys")
            record["flag_keys"] = [key for bit, key in sorted(tables["flag_bits"].items())
                                   if record["flags"] & (1 << bit)]
        if block.get("lists"):
            record["list_keys"] = [key for key, members in lists if move in members]
        if move in formulas:
            record["power_kind"] = formulas[move]["kind"]
            if "base" in formulas[move]:
                record["power_base"] = formulas[move]["base"]
            record["power_formula"] = formulas[move]["formula"]
            if "item_tiers" in formulas[move]:
                record["power_items"] = _item_tiers(formulas[move]["item_tiers"], data)
            if "tiers" in formulas[move]:
                record["power_tiers"] = [[row["condition"], row["power"]] for row in formulas[move]["tiers"]]
            if "weight_tiers" in formulas[move]:
                record["power_weight"] = formulas[move]["weight_tiers"]
        if move in mechanics:
            item = mechanics[move]
            record["mechanics"] = {"summary": item["summary"], "rows": [
                {"condition": row["condition"], "effect": row["effect"], "notes": row.get("notes", [])}
                for row in item["rows"]]}
            if "environments" in item:
                table = block["environments"]
                environments = {"column": item["environments"], "rows": _environments(
                    table, item["environments"], data, int(block["count"]), places or {})}
                persistence = table.get("scripts", {}).get("persistence")
                if persistence:
                    environments["script_persistence"] = persistence
                record["mechanics"]["environments"] = environments
        if flinch:
            record.update(flinch_fields(flinch, record))
        out.append(record)
    return out
