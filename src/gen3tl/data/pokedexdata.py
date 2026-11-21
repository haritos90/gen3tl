"""Pokedex data: one language-neutral dataset per hack, read from the ROM through the profile."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from ..core.notes import has_prose

FORMAT = "gen3tl-pokedex"
SCHEMA = 1
LEARNSETS_FORMAT = "gen3tl-pokedex-learnsets"
LEARNSETS_FILE = "learnsets.json"
LEARNSET_COLUMNS = ("method", "move", "level", "index", "offset")

# Fields a fields table may name, in record order.
STAT_FIELDS = ("hp", "attack", "defense", "speed", "sp_attack", "sp_defense")
RECORD_FIELDS = STAT_FIELDS + ("type1", "type2", "catch_rate", "base_exp", "growth", "gender_ratio",
                               "ability1", "ability2", "hidden_ability", "height", "weight", "dex_number",
                               "egg_group1", "egg_group2", "base_friendship")
# Optional blocks: fields become null, the index names them.
OPTIONAL = ("dex", "entries", "evolution")
REQUIRED = ("species",)
BROWSING_TABLES = ("species", "moves", "abilities", "items")
POINTER_LOW, POINTER_HIGH = 0x08000000, 0x0A000000


class PokedexError(ValueError):
    """The profile cannot drive a Pokedex dataset."""


def _field(spec) -> tuple[int, int]:
    """(offset, width) of one entry field: an offset, or {offset, width}."""
    if isinstance(spec, dict):
        return int(spec["offset"]), int(spec.get("width", 1))
    return int(spec), 1


def _value(data: bytes, at: int, width: int) -> int | None:
    if at < 0 or width <= 0 or at + width > len(data):
        return None
    return int.from_bytes(data[at:at + width], "little")


def _text_id(data: bytes, at: int) -> tuple[str | None, int]:
    """The catalog id of the string a pointer at `at` leads to, and that pointer's offset."""
    pointer = _value(data, at, 4)
    if pointer and POINTER_LOW <= pointer < POINTER_HIGH:
        return f"s{pointer - POINTER_LOW:07X}", at
    return None, at


def _optional_missing(block: dict) -> list[str]:
    """Optional blocks the profile does not carry; entries count as carried when the dex records point at the text."""
    entries_in_dex = "description" in ((block.get("dex") or {}).get("records") or {}).get("fields", {})
    return [name for name in OPTIONAL if name not in block and not (name == "entries" and entries_in_dex)]


def _check_browsing(block: dict, kind: str) -> list[str]:
    key = f"{kind}_browsing"
    browsing = block.get(key, {})
    if not isinstance(browsing, dict):
        return [f"{key}: a table"]
    problems = []
    table = block.get(kind, {})
    first, count = int(table.get("first", 0)), int(table.get("count", 0))
    for number, rule in browsing.items():
        if kind == "species" and number == "forms":
            from . import pokedexforms
            problems += pokedexforms.check_browsing(rule, count)
            if (isinstance(rule, dict) and isinstance(rule.get("compare"), list)
                    and "learnset" in rule["compare"] and "learnsets" not in block):
                problems.append("species_browsing forms: learnsets required")
            continue
        label = f"{key} {number}"
        valid_slot = bool(re.fullmatch(r"0|[1-9][0-9]*", str(number))) and first <= int(number) < count
        if not valid_slot:
            problems.append(f"{label} {kind}: an exported slot")
        if not isinstance(rule, dict):
            problems.append(f"{label}: a table")
            continue
        if rule.get("status") != "unreleased":
            problems.append(f"{label} status: unreleased")
        if not has_prose(rule.get("evidence")):
            problems.append(f"{label} evidence")
        proofs = rule.get("rom")
        if not isinstance(proofs, list) or not proofs:
            problems.append(f"{label} rom")
            continue
        for proof in proofs:
            try:
                valid = (type(proof["offset"]) is int and proof["offset"] >= 0
                         and type(proof["size"]) is int and proof["size"] > 0
                         and isinstance(proof["sha256"], str) and bool(re.fullmatch(r"[0-9a-f]{64}", proof["sha256"])))
            except (KeyError, TypeError, ValueError):
                valid = False
            if not valid:
                problems.append(f"{label} rom")
            elif (not valid_slot
                  or proof["offset"] != int(table.get("base", 0)) + int(number) * int(table.get("stride", 0))
                  or proof["size"] != int(table.get("stride", 0))):
                problems.append(f"{label} rom: complete {kind} record")
    return problems


def _browsing(block: dict, data: bytes, kind: str) -> dict:
    browsing = block.get(f"{kind}_browsing", {})
    for number, rule in browsing.items():
        if kind == "species" and number == "forms":
            continue
        for proof in rule["rom"]:
            at, size = proof["offset"], proof["size"]
            if at + size > len(data) or hashlib.sha256(data[at:at + size]).hexdigest() != proof["sha256"]:
                raise PokedexError(f"{kind}_browsing {number}: ROM span differs at {at:#x}")
    return {str(number): rule if kind == "species" and number == "forms" else rule["status"]
            for number, rule in browsing.items()}


def _species_browsing(block: dict, data: bytes) -> dict:
    return _browsing(block, data, "species")


def check(block: dict | None) -> list[str]:
    """Every problem that keeps the block from producing a dataset; empty when it can."""
    if not block:
        return ["[pokedex] block"]
    problems = []
    for name in REQUIRED:
        if name not in block:
            problems.append(f"[pokedex.{name}]")
            continue
        for key in ("base", "stride", "count"):
            if key not in block[name]:
                problems.append(f"[pokedex.{name}] {key}")
        if not has_prose(block[name].get("evidence")):   # nothing unproven runs
            problems.append(f"[pokedex.{name}] evidence")
    if "name_table" not in block:
        problems.append("[pokedex] name_table")
    if "species" in block and "fields" not in block["species"]:
        problems.append("[pokedex.species] fields")
    if "species" in block and "category_table" in block["species"] and "category_field" not in block["species"]:
        problems.append("[pokedex.species] category_field")
    if "ev_yield" in block.get("species", {}):
        ev_yield = block["species"]["ev_yield"] or {}
        if "offset" not in ev_yield:
            problems.append("[pokedex.species.ev_yield] offset")
        if not has_prose(ev_yield.get("evidence", "")):
            problems.append("[pokedex.species.ev_yield] evidence")
    dex = block.get("dex") or {}
    if "dex" in block and "records" not in dex:
        problems.append("[pokedex.dex.records]")
    if dex.get("records") and "base" not in dex["records"]:
        problems.append("[pokedex.dex.records] base")
    if dex.get("records") and not has_prose(dex["records"].get("evidence", "")):
        problems.append("[pokedex.dex.records] evidence")
    ev = block.get("evolution") or {}
    if ev:
        if not has_prose(ev.get("evidence", "")):
            problems.append("[pokedex.evolution] evidence")
        if ev.get("index") == "pointer" and "pointer" not in ev:
            problems.append("[pokedex.evolution] pointer")
        if ev.get("index") != "pointer":
            problems += [f"[pokedex.evolution] {key}" for key in ("base", "stride", "slots") if key not in ev]
        if "fields" not in ev:
            problems.append("[pokedex.evolution] fields")
        methods = ev.get("tile_methods", [])
        if not isinstance(methods, list) or any(type(m) is not int or m < 0 for m in methods):
            problems.append("[pokedex.evolution] tile_methods")
        for field in ("form_kinds", "parameter_kinds"):
            kinds = ev.get(field, {})
            if not isinstance(kinds, dict):
                problems.append(f"[pokedex.evolution] {field}")
                kinds = {}
            if kinds and not {"method", "parameter", "extra"} <= set(ev.get("fields", {})):
                problems.append(f"[pokedex.evolution.{field}] fields")
            for kind, rule in kinds.items():
                valid = isinstance(kind, str) and re.fullmatch(r"[a-z][a-z0-9_]*", kind)
                if not isinstance(rule, dict):
                    valid = False
                else:
                    extras = rule.get("extras")
                    valid = (valid and all(type(rule.get(key)) is int and rule[key] >= 0
                                           for key in ("method", "parameter_min"))
                             and isinstance(extras, list) and bool(extras)
                             and all(type(value) is int and value >= 0 for value in extras)
                             and len(extras) == len(set(extras))
                             and has_prose(rule.get("evidence")))
                if not valid:
                    problems.append(f"[pokedex.evolution.{field}] {kind}")
    if "tile_behaviors" in block:
        tiles = block["tile_behaviors"]
        if not isinstance(tiles, dict):
            problems.append("[pokedex.tile_behaviors] block")
        else:
            if not has_prose(tiles.get("evidence", "")):
                problems.append("[pokedex.tile_behaviors] evidence")
            names = tiles.get("names")
            if not isinstance(names, dict) or any(
                    not str(key).isdigit() or not 0 <= int(key) <= 255
                    or not isinstance(name, str) or not name.strip()
                    for key, name in names.items()):
                problems.append("[pokedex.tile_behaviors] names")
    # the sprite tables the sprite field names
    spr = block.get("sprites")
    if spr:
        for key in ("front", "back", "palettes", "shiny_palettes"):
            if key not in spr:
                problems.append(f"[pokedex.sprites] {key}")
            else:
                problems += [f"[pokedex.sprites.{key}] {field}" for field in ("base", "stride", "count")
                             if field not in spr[key]]
        if not has_prose(spr.get("evidence", "")):
            problems.append("[pokedex.sprites] evidence")
    if "moves" in block:
        from . import pokedexmoves
        problems += [f"[pokedex.moves] {problem}" for problem in pokedexmoves.check(block["moves"])]
        table = block["moves"].get("environments")
        if (isinstance(table, dict) and "encounters" not in block
                and ("scenes" in table or "scripts" in table
                     or any(row.get("map_codes") for row in table.get("rows", [])))):
            problems.append("[pokedex.moves.environments] encounters context required for map codes, scenes and scripts")
    if "abilities" in block:
        from . import pokedexabilities
        problems += [f"[pokedex.abilities] {problem}" for problem in pokedexabilities.check(block["abilities"])]
    if "items" in block:
        from . import pokedexitems
        problems += [f"[pokedex.items] {problem}" for problem in pokedexitems.check(block["items"])]
    if "learnsets" in block:
        from . import pokedexlearnsets
        problems += [f"[pokedex.learnsets] {problem}" for problem in pokedexlearnsets.check(block["learnsets"])]
    if "encounters" in block:
        from . import pokedexencounters
        problems += [f"[pokedex.encounters] {problem}" for problem in pokedexencounters.check(block["encounters"])]
    if "locations" in block:
        from . import pokedexlocations
        problems += [f"[pokedex.locations] {problem}" for problem in pokedexlocations.check(block["locations"])]
        if "names" in block["locations"]:
            names = block["locations"]["names"]
            problems += [f"[pokedex.locations] {problem}" for problem in pokedexlocations.check_names(names)]
            if isinstance(names, dict) and "labels" in block:
                from . import pokedexlabels
                known = pokedexlabels.build(block["labels"])
                declared = {row["name"] for row in names.get("maps", [])
                            if isinstance(row, dict) and isinstance(row.get("name"), str)}
                problems += [f"[pokedex.locations.names] unknown name label {name}"
                             for name in sorted(declared - known.keys())]
        if "encounters" not in block:
            problems.append("[pokedex.locations] encounters")
    if "trainers" in block:
        from . import pokedextrainers
        problems += [f"[pokedex.trainers] {problem}" for problem in pokedextrainers.check(block["trainers"])]
        if "ivs" in block["trainers"] and "learnsets" not in block:
            problems.append("[pokedex.trainers.ivs] learnsets required for default moves")
        if "abilities" in block["trainers"]:
            if "abilities" not in block or any(key not in block.get("species", {}).get("fields", {})
                                                for key in ("ability1", "ability2", "hidden_ability")):
                problems.append("[pokedex.trainers.abilities] species ability slots required")
    if "sources" in block:
        from . import pokedexsources
        problems += [f"[pokedex.sources] {problem}" for problem in pokedexsources.check(block["sources"])]
    if "maps" in block:
        from . import pokedexmaps
        problems += pokedexmaps.check(block)
    if "icons" in block:
        from . import pokedexicons
        problems += pokedexicons.check(block)
    if "type_effectiveness" in block:
        from . import pokedextypes
        problems += [f"[pokedex.type_effectiveness] {problem}" for problem in pokedextypes.check(block["type_effectiveness"])]
    if "type_icons" in block:
        from . import pokedextypes
        problems += [f"[pokedex.type_icons] {problem}" for problem in pokedextypes.check_icons(block["type_icons"])]
    if "map_sections" in block:
        for key in ("base", "first", "count", "evidence"):
            if key not in block["map_sections"]:
                problems.append(f"[pokedex.map_sections] {key}")
    if "labels" in block:
        from . import pokedexlabels
        problems += [f"[pokedex.labels] {problem}" for problem in pokedexlabels.check(block["labels"])]
    if "form_names" in block:
        from . import pokedexforms
        problems += pokedexforms.check(block["form_names"], int(block.get("species", {}).get("count", 0)))
    for kind in BROWSING_TABLES:
        problems += _check_browsing(block, kind)
    if not problems and "encounters" in block:
        from . import pokedexlabels
        labels = pokedexlabels.build(block.get("labels", {}))
        names = {name for row in pokedexencounters.method_labels(block["encounters"]).values() for name in row}
        problems += [f"[pokedex.encounters] unknown method label {name}" for name in sorted(names - labels.keys())]
    return problems


def _manifest(data: bytes) -> dict:
    return {"sha1": hashlib.sha1(data).hexdigest(), "size": len(data)}


def block_fingerprint(block: dict) -> str:
    """SHA-256 of the canonical [pokedex] block; any profile change moves it."""
    return hashlib.sha256(dump(block).encode("utf-8")).hexdigest()


def verify_doc(doc: dict, block: dict, profile_name: str, rom_sha1: str) -> list[str]:
    """Every way the tracked dataset is stale against the profile; empty when current."""
    from . import pokedexlabels
    try:
        block = pokedexlabels.resolve(block, profile_name)
    except (OSError, ValueError) as exc:
        return [f"profile: {exc}"]
    problems = check(block)
    if problems:
        return [f"profile: {p}" for p in problems]
    if doc.get("format") != FORMAT or doc.get("schema") != SCHEMA:
        return [f"format: expected {FORMAT} schema {SCHEMA}"]
    if doc.get("profile", {}).get("id") != profile_name:
        return [f"profile: dataset is {doc.get('profile', {}).get('id')!r}, expected {profile_name!r}"]
    if doc.get("rom", {}).get("sha1") != rom_sha1:
        return [f"rom sha1: dataset is {doc.get('rom', {}).get('sha1')!r}, profile pins {rom_sha1!r}"]
    if doc.get("name_table") != block.get("name_table"):
        return [f"name_table: dataset is {doc.get('name_table')!r}, profile says {block.get('name_table')!r}"]
    if doc.get("profile_block") != block_fingerprint(block):
        return ["profile_block: regenerate the dataset with gen3tl data pokedex (the profile block changed)"]
    for kind in BROWSING_TABLES:
        key = f"{kind}_browsing"
        browsing = {str(number): rule if kind == "species" and number == "forms" else rule["status"]
                    for number, rule in block.get(key, {}).items()}
        if doc.get(key, {}) != browsing:
            problems.append(f"{key}: regenerate the dataset with gen3tl data pokedex")
        field = "slot" if kind == "species" else "id"
        exported = {str(record.get(field)) for record in doc.get(kind, [])}
        if kind == "species" and "species" not in doc:
            exported = set(doc.get("files", {}))
        if not (set(browsing) - ({"forms"} if kind == "species" else set())) <= exported:
            problems.append(f"{key}: a classified {kind} record is missing")
    if "forms" in block.get("species_browsing", {}) and "species" in doc:
        from . import pokedexforms
        records = doc["species"]
        if doc.get("learnsets") == LEARNSETS_FILE:
            for record in records:
                if record.get("base_species") not in (None, record["slot"]):
                    rule = record.get("form_browsing", {})
                    if (not isinstance(rule, dict) or type(rule.get("separate")) is not bool
                            or not isinstance(rule.get("differences"), list)
                            or rule.get("reason") not in {"preserved_transformation", "battle_only", "gameplay_difference", "same_gameplay"}):
                        problems.append(f"form_browsing: regenerate species slot {record['slot']}")
                        break
        else:
            try:
                expected = pokedexforms.classify(block["species_browsing"]["forms"], records)
                for record in records:
                    if record.get("form_browsing") != expected.get(record["slot"]):
                        problems.append(f"form_browsing: regenerate species slot {record['slot']}")
                        break
            except ValueError as exc:
                problems.append(str(exc))
    if doc.get("missing") != _optional_missing(block):
        return [f"missing: dataset lists {doc.get('missing')!r}, profile yields {_optional_missing(block)!r}"]
    for name in ("moves", "abilities", "items", "map_sections"):
        if (name in block) != isinstance(doc.get(name), list):
            problems.append(f"{name}: regenerate the dataset with gen3tl data pokedex")
    if "moves" in block and not isinstance(doc.get("move_readers"), dict):
        problems.append("move_readers: regenerate the dataset with gen3tl data pokedex")
    move_block = block.get("moves", {})
    overrides = move_block.get("effect_overrides", {})
    for record in doc.get("moves", []):
        if "flinch" in move_block:
            from .pokedexmoves import flinch_fields
            try:
                fields = flinch_fields(move_block["flinch"], record)
                if any(record.get(key) != value or type(record.get(key)) is not type(value)
                       for key, value in fields.items()):
                    problems.append(f"move {record['id']} flinch rule: regenerate the dataset with gen3tl data pokedex")
            except ValueError as exc:
                problems.append(str(exc))
        rule = overrides.get(str(record["id"]))
        expected = (rule["key"] if rule else move_block.get("effects", {}).get(str(record.get("effect"))))
        if (expected is not None and record.get("effect_key") != expected
                or rule and record.get("effect") != rule["effect"]):
            problems.append(f"move {record['id']} effect rule: regenerate the dataset with gen3tl data pokedex")
    for number, power in block.get("moves", {}).get("power", {}).items():
        record = next((row for row in doc.get("moves", []) if row["id"] == int(number)), {})
        if record.get("power_kind") != power["kind"] or record.get("power_base") != power.get("base"):
            problems.append(f"move {number} power rule: regenerate the dataset with gen3tl data pokedex")
        if "item_tiers" not in power:
            continue
        table = power["item_tiers"]
        rows = record.get("power_items")
        if (not isinstance(rows, list) or len(rows) != table["count"] - len(table.get("exclude", []))
                or any(not isinstance(row, dict) or any(type(row.get(key)) is not int or row[key] < 0
                                                       for key in ("item", "type", "power")) for row in rows)
                or len({row["item"] for row in rows}) != len(rows)
                or any(row["power"] == 0 or row["item"] in table.get("exclude", []) for row in rows)):
            problems.append(f"move {number} item power: regenerate the dataset with gen3tl data pokedex")
    table = move_block.get("environments", {})
    for number, mechanics in move_block.get("mechanics", {}).items():
        if "environments" not in mechanics:
            continue
        record = next((row for row in doc.get("moves", []) if row["id"] == int(number)), {})
        shown = (record.get("mechanics") or {}).get("environments") or {}
        rows = shown.get("rows")
        kind = str if mechanics["environments"] == "effect" else int
        if (shown.get("column") != mechanics["environments"] or not isinstance(rows, list)
                or shown.get("script_persistence") != table.get("scripts", {}).get("persistence")
                or [(row.get("environment"), row.get("where")) for row in rows if isinstance(row, dict)]
                != [(row["environment"], row["where"]) for row in table.get("rows", [])]
                or any(type(row.get("value")) is not kind for row in rows)
                or any(not isinstance(row.get(name, []), list)
                       or any(not isinstance(item, dict) or set(item) != {"map", "section"}
                              or not isinstance(item["map"], str) or type(item["section"]) is not int
                              for item in row.get(name, []))
                       for row in rows for name in ("map_code_maps", "scene_maps", "script_maps", "also_script_maps"))
                or any(bool(declared.get("map_codes")) != ("map_code_maps" in row)
                       for declared, row in zip(table.get("rows", []), rows))
                or bool(table.get("scenes") or table.get("scripts")
                        or any(row.get("map_codes") for row in table.get("rows", []))) != any(
                    name in row for row in rows
                    for name in ("map_code_maps", "scene_maps", "script_maps", "also_script_maps"))):
            problems.append(f"move {number} environments: regenerate the dataset with gen3tl data pokedex")
    if ("sources" in block.get("items", {}) or "mining" in block.get("items", {})) and any(
            not isinstance(row.get("sources"), list) for row in doc.get("items", [])):
        problems.append("item sources: regenerate the dataset with gen3tl data pokedex")
    if "uses" in block.get("items", {}) and any(not isinstance(row.get("uses"), list) for row in doc.get("items", [])):
        problems.append("item uses: regenerate the dataset with gen3tl data pokedex")
    if "sell_price" in block.get("items", {}):
        from .pokedexitems import sell_price
        rule = block["items"]["sell_price"]
        for row in doc.get("items", []):
            expected = sell_price(rule, row)
            if ("sell_price" not in row or row["sell_price"] != expected
                    or type(row["sell_price"]) is not type(expected)):
                problems.append("item sell prices: regenerate the dataset with gen3tl data pokedex")
                break
    held = block.get("items", {}).get("held_effects", {})
    if held.get("amounts"):
        from .pokedexitems import amount_record
        amounts, overrides = held["amounts"], held.get("overrides", {})
        for row in doc.get("items", []):
            effect, override = row.get("held_effect"), overrides.get(str(row.get("id")))
            spec = (amounts.get(f"item.{row.get('id')}") if override and override["effect"] == effect
                    else amounts.get(f"{effect}.{row.get('hold_effect_param')}", amounts.get(str(effect))))
            if not row.get("held_effect_key"):
                spec = None
            if row.get("held_effect_amount") != (spec and amount_record(spec)):
                problems.append("item held effect amounts: regenerate the dataset with gen3tl data pokedex")
                break
    if "learnsets" in block:
        if doc.get("learnsets") != LEARNSETS_FILE:   # the inlined form of read() is accepted too
            for record in doc.get("species", []):
                if not isinstance(record.get("learnset"), list):
                    problems.append(f"learnset: regenerate species slot {record.get('slot')}")
                    break
        from . import pokedexlearnsets
        for record in doc.get("species", []):
            expected = pokedexlearnsets.resolve_species(
                block["learnsets"], record["slot"], record.get("evolution"))
            if record.get("learnset_species") != expected:
                problems.append(f"learnset_species: regenerate species slot {record.get('slot')}")
                break
    if "hidden_ability" in block["species"].get("fields", {}):
        for record in doc.get("species", []):
            if record.get("abilities") and not isinstance(record.get("ability_details"), list):
                problems.append(f"ability_details: regenerate species slot {record.get('slot')}")
                break
    if "presentation" in block.get("abilities", {}):
        from . import pokedexabilities
        problems += pokedexabilities.verify(block["abilities"], doc.get("species", []), doc.get("abilities", []))
    if "ev_yield" in block["species"]:
        for record in doc.get("species", []):
            if not isinstance(record.get("ev_yield"), dict):
                problems.append(f"ev_yield: regenerate species slot {record.get('slot')}")
                break
    if "encounters" in block:
        if not isinstance(doc.get("encounters"), list):
            problems.append("encounters: regenerate the dataset with gen3tl data pokedex")
        else:
            from . import pokedexencounters
            labels = pokedexencounters.method_labels(block["encounters"])
            for row in doc["encounters"]:
                expected = labels.get((row.get("runtime_map"), row.get("method")))
                if row.get("method_labels") != expected:
                    problems.append("method_labels: regenerate the dataset with gen3tl data pokedex")
                    break
        if not isinstance(doc.get("encounter_unavailable"), list):
            problems.append("encounter_unavailable: regenerate the dataset with gen3tl data pokedex")
    if "locations" in block and not isinstance(doc.get("location_details"), list):
        problems.append("location_details: regenerate the dataset with gen3tl data pokedex")
    if "names" in block.get("locations", {}) and not isinstance(doc.get("location_maps"), list):
        problems.append("location_maps: regenerate the dataset with gen3tl data pokedex")
    rates = block.get("locations", {}).get("encounter_rates")
    if rates is not None and doc.get("encounter_rates") != rates:
        problems.append("encounter_rates: regenerate the dataset with gen3tl data pokedex")
    if "trainers" in block and not isinstance(doc.get("trainers"), list):
        problems.append("trainers: regenerate the dataset with gen3tl data pokedex")
    elif "maps" in block.get("trainers", {}):
        from .pokedextrainers import RUNTIME_MAP
        selected = {record for selection in block["trainers"].get("difficulty", {}).get("selections", [])
                    for record in selection["records"]}
        selected.update(record for row in block["trainers"]["maps"].get("resolutions", []) for record in row["records"])
        for row in doc["trainers"]:
            maps, sections = row.get("map"), row.get("map_section")
            if (not isinstance(maps, list) or not isinstance(sections, list)
                    or len(maps) != len(sections)
                    or any(not isinstance(name, str) or not RUNTIME_MAP.fullmatch(name) for name in maps)
                    or any(type(section) is not int or not 0 <= section <= 255 for section in sections)
                    or len(set(maps)) != len(maps)
                    or maps != sorted(maps, key=lambda name: tuple(map(int, name.split("."))))
                    or row["id"] in selected and not maps
                    or type(row.get("ev_training")) is not bool):
                problems.append("trainer maps: regenerate the dataset with gen3tl data pokedex")
                break
    if isinstance(doc.get("trainers"), list):
        from .pokedextrainers import KINDS, map_summary, order_summary, verify_encounters
        if doc.get("trainer_maps") != map_summary(doc["trainers"], block.get("trainers", {})):
            problems.append("trainer map evidence: regenerate the dataset with gen3tl data pokedex")
        if "abilities" in block.get("trainers", {}):
            from . import pokedextrainerabilities
            problems += pokedextrainerabilities.verify(block["trainers"]["abilities"], doc["trainers"], doc.get("abilities", []))
        if any(row.get("kind") not in KINDS for row in doc["trainers"]):
            problems.append("trainer kinds: regenerate the dataset with gen3tl data pokedex")
        encounter_problems = verify_encounters(doc.get("trainer_encounters"), doc["trainers"], block.get("trainers", {}))
        problems += encounter_problems
        if not encounter_problems and (doc.get("trainer_order") != order_summary(
                doc["trainer_encounters"], block.get("trainers", {}))
                or doc.get("provenance", {}).get("trainer_order") != block.get("trainers", {}).get("order")):
            problems.append("trainer order: regenerate the dataset with gen3tl data pokedex")
        if pictures := block.get("trainers", {}).get("pictures"):
            from .pokedexsprites import trainer_file
            if any(type(row.get("picture")) is not int
                   or not 0 <= row["picture"] < int(pictures["front"]["count"])
                   or row.get("sprite") != trainer_file(row["picture"]) for row in doc["trainers"]):
                problems.append("trainer pictures: regenerate the dataset with gen3tl data pokedex")
    from .pokedextrainers import difficulty_presentation
    if doc.get("trainer_difficulty") != difficulty_presentation(block.get("trainers", {})):
        problems.append("trainer_difficulty: regenerate the dataset with gen3tl data pokedex")
    if "ivs" in block.get("trainers", {}):
        from . import pokedextrainerivs
        companion = doc.get("trainer_ivs")
        if isinstance(companion, dict):
            problems += pokedextrainerivs.verify_doc(
                companion, block["trainers"]["ivs"], profile_name, rom_sha1, block_fingerprint(block),
                block["trainers"].get("evs"))
            members = {(row.get("trainer"), row.get("slot")): row for row in companion.get("members", [])}
            if any((mon["moves"] is None) != ("move_bands" in members.get((trainer["id"], mon["index"]), {}))
                   for trainer in doc.get("trainers", []) for mon in trainer["party"] if mon["species"]):
                problems.append("trainer default moves: regenerate the dataset with gen3tl data pokedex")
        elif companion != pokedextrainerivs.FILENAME:
            problems.append("trainer_ivs: regenerate the dataset with gen3tl data pokedex")
    if "form_names" in block and any("form_name" not in row or type(row.get("form_name_full")) is not bool
                                     for row in doc.get("species", [])):
        problems.append("form names: regenerate the dataset with gen3tl data pokedex")
    if "sources" in block and not isinstance(doc.get("sources"), list):
        problems.append("sources: regenerate the dataset with gen3tl data pokedex")
    if "maps" in block and not isinstance(doc.get("maps"), list):
        problems.append("maps: regenerate the dataset with gen3tl data pokedex")
    if "icons" in block and not isinstance(doc.get("icons"), dict):
        problems.append("icons: regenerate the dataset with gen3tl data pokedex")
    if "type_effectiveness" in block and not isinstance(doc.get("type_effectiveness"), list):
        problems.append("type_effectiveness: regenerate the dataset with gen3tl data pokedex")
    if "type_icons" in block and not isinstance(doc.get("types"), list):
        problems.append("types: regenerate the dataset with gen3tl data pokedex")
    if "labels" in block:
        from . import pokedexlabels
        if doc.get("labels") != pokedexlabels.build(block["labels"]):
            problems.append("labels: regenerate the dataset with gen3tl data pokedex (the page labels changed)")
    return problems


def _toolkit() -> str:
    from ..web.editordata import toolkit_version
    return toolkit_version().partition("+")[0]


def _natdex_map(block: dict, data: bytes) -> dict[int, int]:
    """slot -> national dex number, from [pokedex.dex.map] when the profile carries one."""
    map_ = (block.get("dex") or {}).get("map")
    if not map_:
        return {}
    base, stride, width = int(map_["base"]), int(map_.get("stride", 2)), int(map_.get("width", 2))
    count = int(block["species"]["count"])
    first = int(block["species"].get("first", 0))
    out = {}
    for slot in range(first, count):
        value = _value(data, base + slot * stride, width)
        if value is not None:
            out[slot] = value
    return out


def _dex_values(block: dict, data: bytes, slot: int, natdex: int | None) -> tuple[dict, int | None]:
    """The [pokedex.dex.records] values for one slot, and the record's offset."""
    dex = block.get("dex") or {}
    records = dex.get("records")
    if not records:
        return {}, None
    index = slot
    if dex.get("index", "slot") == "dex":
        if natdex is None:
            return {}, None
        index = natdex
    start = int(records["base"]) + index * int(records.get("stride", 1))
    fields = {name: _field(spec) for name, spec in records.get("fields", {}).items()}
    values = {}
    for name, (off, width) in fields.items():
        if width == 4 and name in ("description", "category"):
            values[name], values[f"{name}_offset"] = _text_id(data, start + off)
        elif name == "category":
            values["category_offset"] = f"0x{start + off:06x}"   # inline text carries no id
            # the named [[table]] is indexed by the record index
            table = dex.get("category_table")
            if table:
                values["category"] = f"{table}:{index}"
        else:
            values[name] = _value(data, start + off, width)
    return values, start


def _species_values(block: dict, data: bytes, slot: int) -> tuple[dict, int]:
    sp = block["species"]
    start = int(sp["base"]) + slot * int(sp["stride"])
    fields = {name: _field(spec) for name, spec in sp.get("fields", {}).items()}
    values = {name: _value(data, start + off, width) for name, (off, width) in fields.items()}
    # an inline category, named by its [[table]]
    if sp.get("category_table"):
        values["category"] = f"{sp['category_table']}:{slot}"
        values["category_offset"] = f"0x{start + int(sp['category_field']):06x}"
    return values, start


def _ev_yield(block: dict, data: bytes, start: int) -> dict | None:
    """The EV yield of one slot, 0-3 per stat in STAT_FIELDS order; None when undeclared."""
    spec = block["species"].get("ev_yield")
    word = _value(data, start + int(spec["offset"]), 2) if spec else None
    if word is None:
        return None
    return {name: (word >> 2 * i) & 3 for i, name in enumerate(STAT_FIELDS)}


def _evolution(block: dict, data: bytes, slot: int, start: int) -> list[dict] | None:
    """The evolution edges of one slot, for either evolution shape; None when unreadable."""
    ev = block.get("evolution")
    if not ev:
        return None
    fields = {name: _field(spec) for name, spec in ev["fields"].items()}
    size = int(ev.get("record", 8))
    stop = int(ev.get("stop", 0))

    def read(row: int) -> dict:
        values = {name: _value(data, row + off, width) for name, (off, width) in fields.items()}
        if values.get("method") in ev.get("tile_methods", []):
            tile = block.get("tile_behaviors", {}).get("names", {}).get(str(values.get("extra")))
            if tile:
                values["tile"] = tile
        for field, output in (("form_kinds", "kind"), ("parameter_kinds", "parameter_kind")):
            kinds = [kind for kind, rule in ev.get(field, {}).items()
                     if values.get("method") == rule["method"]
                     and type(values.get("parameter")) is int and values["parameter"] >= rule["parameter_min"]
                     and values.get("extra") in rule["extras"]]
            if len(kinds) > 1:
                raise ValueError(f"ambiguous evolution {output.replace('_', ' ')} at 0x{row:x}")
            if kinds:
                values[output] = kinds[0]
        return values

    if ev.get("index") == "pointer":
        pointer = _value(data, start + int(ev["pointer"]), 4)
        if not pointer or not POINTER_LOW <= pointer < POINTER_HIGH:
            return []
        at, limit = pointer - POINTER_LOW, int(ev.get("count", 32))
        rows = []
        for i in range(limit):
            row = at + i * size
            values = read(row)
            if values.get("method") in (None, stop):
                break
            rows.append(values)
        return rows
    base, stride, slots = int(ev["base"]), int(ev["stride"]), int(ev["slots"])
    rows = []
    for i in range(slots):
        row = base + slot * stride + i * size
        values = read(row)
        if values.get("method") in (None, stop):
            break
        rows.append(values)
    return rows


def _entries(block: dict, data: bytes, slot: int) -> list[str] | None:
    """The catalog ids of the entry text a [pokedex.entries] block points at; an empty slot has none."""
    entries = block.get("entries")
    if not entries:
        return None
    stride = int(entries.get("stride", 4))
    length = int(entries.get("record", 0)) or stride
    at = int(entries["pointer"]) + slot * length if entries.get("record") else int(entries["pointer"]) + slot * stride
    id_, _ = _text_id(data, at)
    if id_ and data[int(id_[1:], 16)] == 0xFF:
        return []
    return [id_] if id_ else []


def _record(block: dict, data: bytes, slot: int, natdex: int | None,
            base_species: int | None = None) -> dict:
    values, start = _species_values(block, data, slot)
    dex, dex_offset = _dex_values(block, data, slot, natdex)
    stats = {name: values.get(name) for name in STAT_FIELDS}
    types = [values.get(name) for name in ("type1", "type2")]
    abilities = [values.get(name) for name in ("ability1", "ability2", "hidden_ability")]
    ability_details = {a: {"id": a, "regular": False, "hidden": False}
                       for a in dict.fromkeys(a for a in abilities if a)}
    for ability, hidden in zip(abilities, (False, False, True)):
        if ability:
            ability_details[ability]["hidden" if hidden else "regular"] = True
    egg_groups = [values.get("egg_group1"), values.get("egg_group2")]
    entry = [dex["description"]] if dex.get("description") else _entries(block, data, slot)
    # the sprite files of the slot, from [pokedex.sprites]
    sprite = {"front": f"sprites/{slot}-front.png", "back": f"sprites/{slot}-back.png",
              "shiny": f"sprites/{slot}-shiny.png", "back_shiny": f"sprites/{slot}-back-shiny.png"} \
        if "sprites" in block else None
    return {
        "slot": slot,
        "offset": f"0x{start:06x}",
        "name": f"{block.get('name_table', 'species')}:{slot}",
        "dex": natdex if natdex is not None else values.get("dex_number"),
        "base_species": base_species,
        "types": [t for t in types if t is not None],   # type id 0 is a real type
        "base_stats": stats if any(v is not None for v in stats.values()) else None,
        "ev_yield": _ev_yield(block, data, start),
        "abilities": list(dict.fromkeys(a for a in abilities if a)) or None,   # ability id 0 is none
        "ability_details": list(ability_details.values()) or None,
        "catch_rate": values.get("catch_rate"),
        "base_exp": values.get("base_exp"),
        "growth": values.get("growth"),
        "gender_ratio": values.get("gender_ratio"),
        "base_friendship": values.get("base_friendship"),
        "egg_groups": egg_groups if any(group is not None for group in egg_groups) else None,
        "height": dex.get("height", values.get("height")),
        "weight": dex.get("weight", values.get("weight")),
        "category": dex.get("category") or values.get("category"),
        "category_offset": dex.get("category_offset") or values.get("category_offset"),
        "dex_offset": f"0x{dex_offset:06x}" if dex_offset is not None else None,
        "evolution": _evolution(block, data, slot, start),
        "entry": entry or None,
        "sprite": sprite,
    }


def build_pickups(block: dict, data: bytes, profile: dict, pictured: set[str]) -> tuple[dict, list[dict], list[dict]]:
    """Extract item pickups and resolve their map pictures and names."""
    from . import pokedexpickups, pokedexlocations
    found, _ = pokedexpickups.build(block["items"]["sources"]["pickups"], data, profile,
                                    int(block["items"]["count"]), pictured)
    selected = [source for rows in found.values() for source in rows]
    resolved = pokedexlocations.resolve_maps(data, profile, selected)
    named = []
    if "names" in block.get("locations", {}):
        from . import pokedexlabels
        named = pokedexlocations.map_names(block["locations"]["names"], data, profile, selected,
                                           pokedexlabels.build(block.get("labels", {})), resolved)
    return found, resolved, named


def build(block: dict, data: bytes, profile: dict) -> dict:
    """The dataset document; `block` is the profile's [pokedex], `data` the ROM bytes."""
    from . import pokedexlabels
    block = pokedexlabels.resolve(block, profile.get("id", ""))
    problems = check(block)
    if problems:
        raise PokedexError("the profile cannot drive a Pokedex dataset: " + ", ".join(problems))
    sp = block["species"]
    count, first = int(sp["count"]), int(sp.get("first", 0))
    natdex = _natdex_map(block, data)
    base_by_dex = {}
    for slot, dex in natdex.items():
        if dex > 0:
            base_by_dex[dex] = min(slot, base_by_dex.get(dex, slot))
    provenance = {}
    for name in REQUIRED + tuple(n for n in OPTIONAL if n in block) + tuple(
            n for n in ("moves", "abilities", "items", "map_sections") if n in block):
        sub = block[name]
        if name == "dex" and "records" in sub:
            sub = sub["records"]
        offset = sub.get("base", sub.get("pointer", 0))
        provenance[name] = {"offset": f"0x{int(offset):06x}", "evidence": sub.get("evidence", "")}
    for name, provenance_name in (("moves", "move_descriptions"), ("abilities", "ability_descriptions")):
        if "descriptions" in block.get(name, {}):
            sub = block[name]["descriptions"]
            provenance[provenance_name] = {
                "offset": f"0x{int(sub['base']):06x}", "evidence": sub.get("evidence", "")}
    if "tm_moves" in block.get("items", {}):
        sub = block["items"]["tm_moves"]
        provenance["tm_moves"] = {
            "offset": f"0x{int(sub['base']):06x}", "evidence": sub.get("evidence", "")}
    for number, power in block.get("moves", {}).get("power", {}).items():
        if "item_tiers" in power:
            table = power["item_tiers"]
            provenance.setdefault("move_item_power", {})[str(number)] = {
                "offset": f"0x{table['base']:06x}", "evidence": power["evidence"], "sha256": table["sha256"]}
    if "environments" in block.get("moves", {}):
        table = block["moves"]["environments"]
        provenance["move_environments"] = {
            "offset": f"0x{table['base']:06x}", "evidence": table["evidence"], "sha256": table["sha256"]}
    if "sources" in block.get("items", {}):
        provenance["item_sources"] = {"offset": "", "evidence": block["items"]["sources"].get("evidence", "")}
    if "pickups" in block.get("items", {}).get("sources", {}):
        provenance["item_pickups"] = {"offset": "", "evidence": block["items"]["sources"]["pickups"]["evidence"]}
    if "names" in block.get("locations", {}):
        provenance["location_map_names"] = {"offset": "", "evidence": block["locations"]["names"]["evidence"]}
    if "uses" in block.get("items", {}):
        provenance["item_uses"] = {"offset": "", "evidence": block["items"]["uses"]["evidence"]}
        provenance["item_uses"]["declarations"] = [
            {"form": name, "index": index, "evidence": row["evidence"], "rom": row["rom"]}
            for name in ("rows", "buyers", "recipes", "tutors", "prices")
            for index, row in enumerate(block["items"]["uses"].get(name, [])) if row.get("rom")]
    if "sell_price" in block.get("items", {}):
        provenance["item_sell_price"] = {"offset": "", "evidence": block["items"]["sell_price"]["evidence"]}
    if "mining" in block.get("items", {}):
        sub = block["items"]["mining"]
        provenance["item_mining"] = {
            "offset": f"0x{int(sub['base']):06x}", "evidence": sub.get("evidence", "")}
    for name in ("level_up", "tmhm", "egg", "tutor"):
        if name in block.get("learnsets", {}):
            sub = block["learnsets"][name]
            provenance[name] = {
                "offset": f"0x{int(sub['base']):06x}", "evidence": sub.get("evidence", "")}
    if "form_reversions" in block.get("learnsets", {}):
        sub = block["learnsets"]["form_reversions"]
        offset = block["evolution"].get("base", block["evolution"].get("pointer", 0))
        provenance["form_reversions"] = {
            "offset": f"0x{int(offset):06x}", "evidence": sub.get("evidence", "")}
    if "encounters" in block:
        encounters = block["encounters"]
        provenance["encounters"] = {
            "offset": f"0x{int(encounters['headers']['day']['base']):06x}",
            "evidence": encounters.get("evidence", "")}
        provenance["encounter_context"] = {
            "offset": f"0x{int(encounters['context']['groups']):06x}",
            "evidence": encounters["context"].get("evidence", "")}
    if "locations" in block:
        provenance["locations"] = {"offset": "", "evidence": block["locations"].get("evidence", "")}
    if "trainers" in block:
        provenance["trainers"] = {
            "offset": f"0x{int(block['trainers']['base']):06x}",
            "evidence": block["trainers"].get("evidence", "")}
        provenance["trainer_party"] = {
            "offset": f"0x{int(block['trainers']['base']):06x}",
            "evidence": block["trainers"]["party"].get("evidence", "")}
        if "class_names" in block["trainers"]:
            provenance["trainer_class_names"] = {
                "offset": f"0x{int(block['trainers']['class_names']['base']):06x}",
                "evidence": block["trainers"]["class_names"].get("evidence", "")}
        if pictures := block["trainers"].get("pictures"):
            from .pokedexsprites import trainer_sources
            provenance["trainer_pictures"] = {
                "offset": f"0x{int(pictures['front']['base']):06x}",
                "evidence": pictures["evidence"], "pictures": trainer_sources(pictures, data)}
        if "difficulty" in block["trainers"]:
            provenance["trainer_difficulty"] = {
                "offset": f"0x{int(block['trainers']['difficulty']['variable']):06x}",
                "evidence": block["trainers"]["difficulty"].get("evidence", "")}
        if "evs" in block["trainers"]:
            provenance["trainer_evs"] = {
                "offset": f"0x{int(block['trainers']['evs']['spans'][0]['offset']):06x}",
                "evidence": block["trainers"]["evs"]["evidence"]}
        if "ivs" in block["trainers"]:
            provenance["trainer_ivs"] = {
                "offset": f"0x{int(block['trainers']['ivs']['spans'][0]['offset']):06x}",
                "evidence": block["trainers"]["ivs"]["evidence"]}
        if "abilities" in block["trainers"]:
            provenance["trainer_abilities"] = {
                "offset": f"0x{int(block['trainers']['base']):06x}",
                "evidence": block["trainers"]["abilities"]["evidence"]}
        for name in ("maps", "ev_training", "kinds"):
            if name in block["trainers"]:
                provenance[f"trainer_{name}"] = {"offset": "", "evidence": block["trainers"][name].get("evidence", "")}
        if "participants" in block["trainers"]:
            provenance["trainer_participants"] = [
                {"offset": f"0x{group['script']:07X}", "evidence": group["evidence"], "rom": group["rom"]}
                for group in block["trainers"]["participants"]]
        if "order" in block["trainers"]:
            provenance["trainer_order"] = block["trainers"]["order"]
    if "ev_yield" in sp:
        provenance["ev_yield"] = {"offset": f"0x{int(sp['base']) + int(sp['ev_yield']['offset']):06x}",
                                  "evidence": sp["ev_yield"].get("evidence", "")}
    if "sources" in block:
        provenance["source_commands"] = {
            "offset": f"0x{int(block['sources']['commands']['table']):06x}",
            "evidence": block["sources"]["commands"].get("evidence", "")}
        provenance["source_trades"] = {
            "offset": f"0x{int(block['sources']['trades']['base']):06x}",
            "evidence": block["sources"]["trades"].get("evidence", "")}
    if "maps" in block:
        provenance["maps"] = {"offset": "", "evidence": block["maps"].get("evidence", "")}
    if "icons" in block:
        provenance["icons"] = {"offset": f"0x{int(block['icons']['split']['base']):06x}",
                               "evidence": block["icons"]["split"].get("evidence", "")}
    if "type_effectiveness" in block:
        provenance["type_effectiveness"] = {
            "offset": f"0x{int(block['type_effectiveness']['base']):06x}",
            "evidence": block["type_effectiveness"].get("evidence", "")}
    if "type_icons" in block:
        provenance["type_icons"] = {"offset": f"0x{int(block['type_icons']['sheet']):06x}",
                                    "evidence": block["type_icons"].get("evidence", "")}
    doc = {
        "schema": SCHEMA,
        "format": FORMAT,
        "toolkit": _toolkit(),
        "profile": {"id": profile.get("id", ""), "name": profile.get("name", "")},
        "rom": _manifest(data),
        "name_table": block.get("name_table", "species"),
        "profile_block": block_fingerprint(block),
        "provenance": provenance,
        "missing": _optional_missing(block),
        "species": [_record(block, data, slot, natdex.get(slot), base_by_dex.get(natdex.get(slot)))
                    for slot in range(first, count)],
    }
    for kind in BROWSING_TABLES:
        if f"{kind}_browsing" in block:
            doc[f"{kind}_browsing"] = _browsing(block, data, kind)
    if "form_names" in block:
        from . import pokedexforms
        pokedexforms.apply(block["form_names"], doc["species"])
        provenance["form_names"] = {"offset": "", "evidence": block["form_names"]["evidence"],
                                    "type_families": block["form_names"].get("type_families", [])}
    if "map_sections" in block:
        sections = block["map_sections"]
        doc["map_sections"] = []
        for section in range(int(sections["first"]), int(sections["first"]) + int(sections["count"])):
            pointer = _value(data, int(sections["base"]) + (section - int(sections["first"]))
                             * int(sections.get("stride", 4)), 4)
            name = f"s{pointer - POINTER_LOW:07X}" if pointer and POINTER_LOW <= pointer < POINTER_HIGH else None
            doc["map_sections"].append({"id": section, "name": name})
    learnsets = None
    if "learnsets" in block:
        from . import pokedexlearnsets
        learnsets = pokedexlearnsets.build(block["learnsets"], data, count)
        for record in doc["species"]:
            record["learnset_species"] = pokedexlearnsets.resolve_species(
                block["learnsets"], record["slot"], record.get("evolution"))
            record["learnset"] = learnsets[record["slot"]]
    if "abilities" in block:
        from . import pokedexabilities
        doc["abilities"] = pokedexabilities.build(block["abilities"], data)
        if "presentation" in block["abilities"]:
            pokedexabilities.apply(block["abilities"], doc["species"], doc["abilities"])
    if "forms" in block.get("species_browsing", {}):
        from . import pokedexforms
        classified = pokedexforms.classify(block["species_browsing"]["forms"], doc["species"])
        for record in doc["species"]:
            if record["slot"] in classified:
                record["form_browsing"] = classified[record["slot"]]
    if "moves" in block:
        from . import pokedexmoves
        places = None
        table = block["moves"].get("environments", {})
        if ("scenes" in table or "scripts" in table
                or any(row.get("map_codes") for row in table.get("rows", []))):
            from . import pokedexencounters
            places = pokedexmoves.environment_maps(
                table, data, profile, pokedexencounters._context_maps(data, block["encounters"]["context"]))
        doc["moves"] = pokedexmoves.build(block["moves"], data, places)
        doc["move_readers"] = pokedexmoves.build_readers(block["moves"])
    if "items" in block:
        from . import pokedexitems
        doc["items"] = pokedexitems.build(block["items"], data)
        if "uses" in block["items"]:
            from . import pokedexuses
            uses = pokedexuses.build(block["items"]["uses"], data, profile, doc["items"],
                                     block.get("learnsets", {}).get("tutor"))
            for row in doc["items"]:
                row["uses"] = uses[row["id"]]
    if "encounters" in block:
        from . import pokedexencounters
        extracted = pokedexencounters.build(block["encounters"], data, count)
        doc["encounters"] = extracted["records"]
        doc["encounter_unavailable"] = extracted["unavailable"]
    if "locations" in block:
        from . import pokedexlocations
        if "encounter_rates" in block["locations"]:
            doc["encounter_rates"] = block["locations"]["encounter_rates"]
        doc["location_details"] = pokedexlocations.build(
            block["locations"], data, block["encounters"]["context"])
    if "trainers" in block:
        from . import pokedextrainers
        if presentation := pokedextrainers.difficulty_presentation(block["trainers"]):
            doc["trainer_difficulty"] = presentation
        doc["trainers"], doc["trainer_encounters"] = pokedextrainers.build(
            block["trainers"], data, count,
            int(block.get("items", {}).get("count", 1 << 16)),
            int(block.get("moves", {}).get("count", 1 << 16)), profile, return_encounters=True)
        doc["trainer_order"] = pokedextrainers.order_summary(doc["trainer_encounters"], block["trainers"])
        doc["trainer_maps"] = pokedextrainers.map_summary(doc["trainers"], block["trainers"])
        if "abilities" in block["trainers"]:
            from . import pokedextrainerabilities
            pokedextrainerabilities.annotate(block["trainers"], data, doc["trainers"], block["species"])
        if "ivs" in block["trainers"]:
            from . import pokedextrainerivs
            doc["trainer_ivs"] = pokedextrainerivs.build(
                block["trainers"]["ivs"], data, doc["trainers"], learnsets,
                doc["profile"], doc["rom"], doc["profile_block"],
                block["trainers"].get("evs"), pokedextrainerivs.absent_members(block["trainers"]),
                doc.get("items"), {record["slot"]: record.get("abilities") for record in doc["species"]},
                doc["trainer_encounters"])
            if "abilities" in block["trainers"]:
                pokedextrainerabilities.annotate_changes(block["trainers"], data, doc["trainers"], block["species"],
                                                         doc["trainer_ivs"])
    if "sources" in block:
        from . import pokedexsources
        doc["sources"] = pokedexsources.build(
            block["sources"], data, profile, count,
            int(block.get("items", {}).get("count", 1 << 16)))
    if "type_effectiveness" in block:
        from . import pokedextypes
        doc["type_effectiveness"] = pokedextypes.build(block["type_effectiveness"], data)
    map_found = None
    if "maps" in block:   # the area pictures of the location cards
        from . import pokedexmaps
        map_found = pokedexmaps.areas(block, data, count)
    if "pickups" in block.get("items", {}).get("sources", {}):
        from . import pokedexpickups
        found, resolved, named = build_pickups(block, data, profile, set(map_found or ()))
        order = ("shop", *pokedexpickups.KINDS)
        for row in doc["items"]:   # shops, then pickups, then mining
            rows = row["sources"] + found.get(row["id"], [])
            row["sources"] = sorted(rows, key=lambda source: order.index(source["kind"]) if source["kind"] in order
                                    else len(order))
        if map_found is not None:
            map_found.update({row["runtime_map"]: int(row["map_header"], 16) for row in resolved if "runtime_map" in row})
        if "names" in block.get("locations", {}):
            doc["location_maps"] = named
    if map_found is not None:
        doc["maps"] = pokedexmaps.index(block, data, count, map_found)
    if "items" in block:
        pokedexitems.join_rewards(doc["items"])
    if "icons" in block:   # the move split icons
        from . import pokedexicons
        doc["icons"] = pokedexicons.index(block)
        if "items" in block["icons"] and isinstance(doc.get("items"), list):
            files = pokedexicons.item_index(block, data)
            for row in doc["items"]:
                row["icon"] = files.get(row["id"])
    if "type_icons" in block:   # a colour per type
        from . import pokedextypes
        doc["types"] = pokedextypes.colours(block["type_icons"], data,
                                            int(block.get("type_effectiveness", {}).get("count", 24)))
    if "labels" in block:   # the page labels as game strings
        from . import pokedexlabels
        doc["labels"] = pokedexlabels.build(block["labels"])
    return doc


def dump(doc, **kwargs) -> str:
    """The canonical JSON text of a document or record: sorted keys, no padding."""
    return json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"), **kwargs)


def learnsets_doc(doc: dict) -> dict | None:
    """The learnsets file of a document; None when no record carries rows."""
    if not any("learnset" in record for record in doc.get("species", [])):
        return None
    rows = {}
    for record in doc["species"]:
        rows[str(record["slot"])] = [[row.get(column) for column in LEARNSET_COLUMNS]
                                     for row in record.get("learnset") or []]
    return {"format": LEARNSETS_FORMAT, "schema": SCHEMA, "profile": doc.get("profile"), "rom": doc.get("rom"),
            "profile_block": doc.get("profile_block"), "columns": list(LEARNSET_COLUMNS), "species": rows}


def inline_learnsets(doc: dict, learnsets: dict) -> None:
    """Put the rows of a learnsets file back on the records."""
    columns = learnsets["columns"]
    for record in doc.get("species", []):
        rows = learnsets["species"].get(str(record["slot"]))
        record["learnset"] = None if rows is None else [
            {"species": record["slot"], **dict(zip(columns, row))} for row in rows]
    doc.pop("learnsets", None)


def write(out: Path, doc: dict, split: bool = False) -> list[Path]:
    """Write the dataset and companions; returns the index first."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    index = dict(doc)
    from . import pokedextrainerivs
    trainer_ivs = doc.get("trainer_ivs") if isinstance(doc.get("trainer_ivs"), dict) else None
    if trainer_ivs:
        index["trainer_ivs"] = pokedextrainerivs.FILENAME
        (out / pokedextrainerivs.FILENAME).write_text(dump(trainer_ivs), encoding="utf-8")
    learnsets = learnsets_doc(doc)   # the rows go to their own file
    if learnsets:
        index["species"] = [{k: v for k, v in record.items() if k != "learnset"} for record in index["species"]]
        index["learnsets"] = LEARNSETS_FILE
        (out / LEARNSETS_FILE).write_text(dump(learnsets), encoding="utf-8")
    if split:
        records, files = index.pop("species"), {}
        (out / "species").mkdir(parents=True, exist_ok=True)
        for record in records:
            name = f"species/{record['slot']}.json"
            (out / name).write_text(dump(record), encoding="utf-8")
            files[str(record["slot"])] = name
            written.append(out / name)
        index["count"] = len(records)
        index["files"] = files
    else:
        index["count"] = len(index["species"])
    path = out / "pokedex.json"
    path.write_text(dump(index), encoding="utf-8")
    return ([path] + written + ([out / pokedextrainerivs.FILENAME] if trainer_ivs else [])
            + ([out / LEARNSETS_FILE] if learnsets else []))


def read(path: Path) -> dict:
    """Read the dataset with split records and companions inlined."""
    path = Path(path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    if "files" in doc:
        doc["species"] = [json.loads((path.parent / name).read_text(encoding="utf-8"))
                          for name in doc["files"].values()]
    if isinstance(doc.get("learnsets"), str):
        learnsets = json.loads((path.parent / doc["learnsets"]).read_text(encoding="utf-8"))
        if learnsets.get("format") != LEARNSETS_FORMAT or learnsets.get("profile_block") != doc.get("profile_block"):
            raise PokedexError(f"{doc['learnsets']}: not the learnsets of {path.name}")
        inline_learnsets(doc, learnsets)
    if isinstance(doc.get("trainer_ivs"), str):
        from . import pokedextrainerivs
        companion = pokedextrainerivs.read(path.parent / doc["trainer_ivs"])
        if companion.get("format") != pokedextrainerivs.FORMAT \
                or companion.get("profile_block") != doc.get("profile_block"):
            raise PokedexError(f"{doc['trainer_ivs']}: not the trainer IVs of {path.name}")
        doc["trainer_ivs"] = companion
    return doc
