"""Verified map connections and points of interest for location cards."""

from __future__ import annotations

import re

from ..core.notes import has_prose
from ..core.rom import Rom
from ..discover.maps import Walker

ROM_BASE = 0x08000000
RUNTIME_MAP = re.compile(r"\d+\.\d+")
CONTEXT_MAP = re.compile(r"(?:\d+\.\d+|h[0-9A-F]{7})")
NAME_KINDS = {"area", "interior", "underground"}


def check_names(block: dict | None) -> list[str]:
    """Problems of the declared map-name rules."""
    if not isinstance(block, dict):
        return ["names"]
    problems = []
    if not has_prose(block.get("evidence", "")):
        problems.append("names evidence")
    kinds = block.get("type_kinds")
    if (not isinstance(kinds, dict) or not kinds
            or any(not str(key).isdigit() or not 0 <= int(key) <= 255
                   or not isinstance(value, str) or value not in NAME_KINDS for key, value in kinds.items())):
        problems.append("names type_kinds")
    rows = block.get("maps", [])
    if not isinstance(rows, list):
        return problems + ["names maps"]
    seen = set()
    for index, row in enumerate(rows):
        prefix = f"names.maps[{index}]"
        if not isinstance(row, dict):
            problems.append(prefix)
            continue
        selectors = [key for key in ("map", "runtime_map") if key in row]
        valid = (
            len(selectors) == 1 and set(row) <= {*selectors, "name", "parent_section", "kind", "evidence"}
            and isinstance(row.get(selectors[0]), str)
            and bool((RUNTIME_MAP if selectors[0] == "runtime_map" else CONTEXT_MAP).fullmatch(row[selectors[0]]))
            and ("name" in row) != ("parent_section" in row)
            and ("name" not in row or isinstance(row["name"], str) and bool(row["name"].strip()))
            and ("parent_section" not in row or type(row["parent_section"]) is int
                 and 0 <= row["parent_section"] <= 255)
            and isinstance(row.get("kind", "area"), str) and row.get("kind", "area") in NAME_KINDS
            and has_prose(row.get("evidence"))
        )
        if not valid:
            problems.append(prefix)
            continue
        key = (selectors[0], row.get(selectors[0]))
        if key in seen:
            problems.append(f"{prefix} duplicate")
        seen.add(key)
    return problems


def _value(data: bytes, at: int, width: int = 4, signed: bool = False) -> int | None:
    if at < 0 or at + width > len(data):
        return None
    return int.from_bytes(data[at:at + width], "little", signed=signed)


def _pointer(data: bytes, at: int) -> int | None:
    value = _value(data, at)
    if value is None or not ROM_BASE <= value < ROM_BASE + len(data):
        return None
    return value - ROM_BASE


def check(block: dict | None) -> list[str]:
    """Configuration problems that would make location details unproven."""
    if not block:
        return ["block"]
    problems = []
    if not has_prose(block.get("evidence", "")):
        problems.append("evidence")
    directions = block.get("directions")
    if not isinstance(directions, dict) or not directions \
            or any(not str(key).isdigit() or not str(value).strip() for key, value in directions.items()):
        problems.append("directions")
    rates = block.get("encounter_rates")
    if rates is not None:
        if not isinstance(rates, dict) or not has_prose(rates.get("evidence", "")):
            problems.append("encounter_rates evidence")
        if isinstance(rates, dict):
            for method, rule in rates.items():
                if method == "evidence":
                    continue
                if (not isinstance(rule, dict)
                        or type(rule.get("divisor")) is not int or rule["divisor"] <= 0
                        or ("threshold" in rule) == ("multiplier" in rule)
                        or any(type(rule[key]) is not int or rule[key] < 0
                               for key in ("threshold", "multiplier") if key in rule)
                        or not rule.get("label") or not has_prose(rule.get("note"))):
                    problems.append(f"encounter_rates {method}")
    maps = block.get("maps")
    if not isinstance(maps, list) or not maps:
        return problems + ["maps"]
    seen = set()
    for index, row in enumerate(maps):
        runtime = row.get("runtime_map") if isinstance(row, dict) else None
        if not isinstance(runtime, str) or not RUNTIME_MAP.fullmatch(runtime) or runtime in seen:
            problems.append(f"maps[{index}] runtime_map")
        seen.add(runtime)
        if not isinstance(row, dict):
            continue
        points = row.get("points", [])
        if not isinstance(points, list):
            problems.append(f"maps[{index}] points")
            continue
        for point_index, point in enumerate(points):
            valid = (isinstance(point, dict)
                     and isinstance(point.get("kind"), str) and bool(point["kind"])
                     and all(type(point.get(key)) is int for key in ("x", "y", "offset", "script"))
                     and point["x"] >= 0 and point["y"] >= 0 and point["offset"] >= 0 and point["script"] > 0
                     and isinstance(point.get("description"), str) and bool(point["description"])
                     and has_prose(point.get("description_evidence")))
            if not valid:
                problems.append(f"maps[{index}].points[{point_index}]")
    return problems


def _map_info(found: dict, runtime: str) -> dict:
    info = found.get(runtime)
    if info is None:
        raise ValueError(f"location map {runtime}: no verified header")
    return info


def _point(data: bytes, point: dict, objects: int, count: int) -> dict:
    at = int(point["offset"])
    if not objects <= at < objects + 24 * count or (at - objects) % 24:
        raise ValueError(f"point 0x{at:06x}: outside the map objects")
    x, y = _value(data, at + 4, 2, True), _value(data, at + 6, 2, True)
    script = _pointer(data, at + 16)
    if (x, y, script) != (point["x"], point["y"], point["script"]):
        raise ValueError(f"point 0x{at:06x}: coordinates or script differ")
    if _value(data, at + 2, 1) == 255:
        raise ValueError(f"point 0x{at:06x}: borrowed connected-map object")
    return {
        "kind": point["kind"], "x": x, "y": y,
        "description": point["description"], "description_evidence": point["description_evidence"],
        "offset": f"0x{at:06x}", "script": f"0x{script:06x}",
        "messages": [f"0x{int(message):06x}" for message in point.get("messages", [])],
    }


def _exits(block: dict, data: bytes, header: int, found: dict) -> list[dict]:
    connections = _pointer(data, header + 12)
    if connections is None:
        return []
    count = _value(data, connections)
    rows = _pointer(data, connections + 4)
    if count is None or count > 64 or rows is None or rows + 12 * count > len(data):
        raise ValueError(f"map header 0x{header:06x}: invalid connections")
    exits = []
    for index in range(count):
        at = rows + 12 * index
        direction_id = _value(data, at)
        direction = block["directions"].get(str(direction_id))
        group, number = _value(data, at + 8, 1), _value(data, at + 9, 1)
        runtime = f"{group}.{number}"
        destination = found.get(runtime)
        if direction is None or destination is None:
            raise ValueError(f"connection 0x{at:06x}: unverified direction or destination")
        exits.append({
            "to_runtime_map": runtime, "to_map": destination["map"], "to_section": destination["map_section"],
            "direction": direction, "alignment": _value(data, at + 4, 4, True), "offset": f"0x{at:06x}",
        })
    return exits


def build(block: dict, data: bytes, encounter_context: dict, found: dict | None = None) -> list[dict]:
    """Extract configured points and every ordinary connection of the selected maps."""
    problems = check(block)
    if problems:
        raise ValueError("invalid location configuration: " + ", ".join(problems))
    if found is None:
        from . import pokedexencounters
        found = pokedexencounters._context_maps(data, encounter_context)
    records = []
    for selected in block["maps"]:
        runtime = selected["runtime_map"]
        header_info = _map_info(found, runtime)
        header = int(header_info["map_header"], 16)
        events = _pointer(data, header + 4)
        if events is None:
            raise ValueError(f"location map {runtime}: invalid events")
        count = _value(data, events, 1)
        objects = _pointer(data, events + 4)
        if count is None or objects is None and selected.get("points"):
            raise ValueError(f"location map {runtime}: invalid objects")
        records.append({
            "runtime_map": runtime, "map": header_info["map"], "map_section": header_info["map_section"],
            "map_header": header_info["map_header"],
            "points": [_point(data, point, objects, count) for point in selected.get("points", [])],
            "exits": _exits(block, data, header, found),
        })
    return records


def _named_section(profile: dict, data: bytes, section: int) -> bool:
    maps = profile["maps"]
    index = section - int(maps["mapsec_first"])
    return 0 <= index < int(maps.get("mapsec_count", 255)) \
        and _pointer(data, int(maps["mapsec_names"]) + 4 * index) is not None


def _warp_parents(walker: Walker, engine: dict[int, list[str]]) -> dict[str, list[tuple[int, int]]]:
    parents = {}
    for header, aliases in engine.items():
        for runtime in aliases:
            parents.setdefault(runtime, [])
    data = walker.d
    for source, header in walker.ctx.headers.items():
        events = walker.ptr(header + 4)
        if events is None:
            continue
        warps = walker.ptr(events + 8)
        for index in range(data[events + 1] if warps is not None else 0):
            at = warps + 8 * index
            runtime = f"{data[at + 7]}.{data[at + 6]}"
            if runtime in parents and _named_section(walker.prof, data, data[header + 20]):
                parents[runtime].append((data[header + 20], at))
    return parents


def resolve_maps(data: bytes, profile: dict, sources: list[dict]) -> list[dict]:
    """Resolve the headers and runtime aliases of selected map sources."""
    walker = Walker(Rom(data), profile)
    walker.walk_all()
    walker.walk_structural()
    engine = walker.engine_map_ids() or {}
    by_runtime = {runtime: header for header, aliases in engine.items() for runtime in aliases}
    records = []
    seen = set()
    for source in sorted(sources, key=lambda row: (row["map_section"], row["map"], row.get("runtime_map") or "")):
        context, runtime = source["map"], source.get("runtime_map")
        identity = (context, runtime)
        if identity in seen:
            continue
        seen.add(identity)
        header = walker.ctx.headers.get(context, by_runtime.get(runtime))
        if header is None or runtime is not None and runtime not in engine.get(header, []):
            raise ValueError(f"location map {context} / {runtime}: no verified header")
        record = {"map": context, "map_section": data[header + 20], "map_header": f"0x{header:06x}",
                  "map_type": data[header + 23]}
        if runtime is not None:
            record["runtime_map"] = runtime
        records.append(record)
    return records


def map_names(block: dict, data: bytes, profile: dict, sources: list[dict], labels: dict,
              resolved: list[dict] | None = None) -> list[dict]:
    """Name selected maps by declared signs, map sections or incoming warps."""
    problems = check_names(block)
    if problems:
        raise ValueError("invalid location map-name configuration: " + ", ".join(problems))
    declarations = {(key, row[key]): row for row in block.get("maps", []) for key in ("map", "runtime_map") if key in row}
    walker = Walker(Rom(data), profile)
    walker.walk_all()
    walker.walk_structural()
    engine = walker.engine_map_ids() or {}
    parents = _warp_parents(walker, engine)
    records = []
    used = set()
    for resolved_record in (resolve_maps(data, profile, sources) if resolved is None else resolved):
        record = dict(resolved_record)
        context, runtime = record["map"], record.get("runtime_map")
        header, section, map_type = int(record["map_header"], 16), record["map_section"], record["map_type"]
        declared = declarations.get(("runtime_map", runtime)) or declarations.get(("map", context))
        if declared is not None:
            key = next(key for key in ("runtime_map", "map") if key in declared)
            used.add((key, declared[key]))
        kind = block["type_kinds"].get(str(map_type))
        if declared and "name" in declared:
            if declared["name"] not in labels:
                raise ValueError(f"location map {context}: unknown name label {declared['name']}")
            record.update(name=declared["name"], name_rule="sign", name_evidence=declared["evidence"])
        elif declared:
            record.update(parent_section=declared["parent_section"], kind=declared.get("kind", kind or "area"),
                          name_rule="warp-parent", name_evidence=declared["evidence"])
        elif _named_section(profile, data, section):
            record.update(name_rule="map-section",
                          name_evidence=f"Map header 0x{header:06X}: named section {section}, map type {map_type}")
            if kind:
                record["kind"] = kind
        else:
            facts = parents.get(runtime, [])
            sections = {section for section, _at in facts}
            if len(sections) != 1:
                raise ValueError(f"location map {context} / {runtime}: no declared name or unique warp parent")
            parent = sections.pop()
            offsets = ", ".join(f"0x{at:06X}" for sec, at in facts if sec == parent)
            record.update(parent_section=parent, kind=kind or "area", name_rule="warp-parent",
                          name_evidence=f"Incoming warp events at {offsets}: named parent section {parent}")
        records.append(record)
    unused = sorted(set(declarations) - used)
    if unused:
        raise ValueError("location map-name declarations name no pickup map: "
                         + ", ".join(f"{key}:{value}" for key, value in unused))
    return records
