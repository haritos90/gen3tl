"""Pokedex profiling: probe a ROM for structural candidates and write a profile skeleton."""

from __future__ import annotations

import re
from pathlib import Path

from ..core.charmap import Charmap
from ..core.rom import Rom

# FireRed and Emerald differ in speed / sp.attack position;
# Bulbasaur's hp 45, atk 49, def 49 tell them apart.
BULBASAUR_ANCHORS = {
    "firered": b"\x2d\x31\x31\x41\x41\x2d",     # 45 49 49 65 65 45
    "emerald": b"\x2d\x31\x31\x2d\x41\x41",     # 45 49 49 45 65 65
}
IVYSAUR_ANCHORS = {
    "firered": b"\x3c\x3e\x3f\x50\x50\x3c",     # 60 62 63 80 80 60
    "emerald": b"\x3c\x3e\x3f\x3c\x50\x50",     # 60 62 63 60 80 80
}
NAME_STRIDE = 11
STAT_STRIDES = (28, 36, 44, 152)
ENGINE_FAMILIES = {
    "BPRE": ("firered", "header game code BPRE; CFRU presence needs the x28 species accessor check"),
    "BPEE": ("emerald", "header game code BPEE; pokeemerald-based hacks"),
    "BPGE": ("firered", "header game code BPGE"),
    "AXVE": ("ruby", "header game code AXVE"),
    "AXPE": ("sapphire", "header game code AXPE"),
}
LETTER_BYTES = frozenset(range(0xBB, 0xD5)) | frozenset(range(0xD5, 0xEF))
_U16_ANCHOR = re.compile(rb"\x01\x00.{46}\x19\x00", re.DOTALL)   # map[1] = 1, map[25] = 25
_LZ77_SHAPE = re.compile(rb"\x10[\x00-\xff][\x00-\x20]\x00", re.DOTALL)


def _header(data: bytes) -> dict:
    return {"game_code": data[0xAC:0xB0].decode("latin-1", "replace"),
            "title": data[0xA0:0xAC].decode("latin-1", "replace").strip() or "(none)"}


def _engine(data: bytes, header: dict) -> dict:
    code = header["game_code"]
    family, evidence = ENGINE_FAMILIES.get(code, ("unknown", f"header game code {code}"))
    return {"family": family, "evidence": evidence}


def _slot_name(cm: Charmap, data: bytes, off: int) -> str | None:
    """The name an 11-byte slot holds, when it is one."""
    slot = data[off:off + NAME_STRIDE]
    end = slot.find(b"\xff")
    if end <= 1:
        return None
    try:
        text = cm.decode(slot)[0]
    except Exception:
        return None
    letters = sum(c.isalpha() for c in text)
    if letters < 2 or letters < len(text) * 0.5:
        return None
    if any(c in text for c in "{}"):   # control codes, not a name
        return None
    return text


def _name_runs(data: bytes, cm: Charmap) -> list[dict]:
    """Runs of 11-byte name slots with valid slots 1-3, in quality order."""
    found: dict[int, dict] = {}
    limit = len(data) - NAME_STRIDE * 4
    for off in range(0, limit):
        if data[off] not in LETTER_BYTES or data[off + NAME_STRIDE] not in LETTER_BYTES:
            continue
        one = _slot_name(cm, data, off)
        two = _slot_name(cm, data, off + NAME_STRIDE)
        three = _slot_name(cm, data, off + NAME_STRIDE * 2)
        if not (one and two and three):
            continue
        base = off - NAME_STRIDE
        if base in found:
            continue
        valid = total = 0
        last_good = -1
        at = base + NAME_STRIDE
        while at + NAME_STRIDE <= len(data) and total < 6000:
            if _slot_name(cm, data, at) is not None:
                valid += 1
                last_good = total
            total += 1
            at += NAME_STRIDE
            if total > 64 and total - valid >= 64:
                break
        count = last_good + 2   # slot 0 leads the run, the junk tail is cut
        if count < 100 or valid / (last_good + 1) < 0.8:
            continue
        found[base] = {
            "kind": "species names",
            "base": base,
            "stride": NAME_STRIDE,
            "count": count,
            "slot1": one,
            "slot2": two,
            "slot3": three,
            "anchor": int(one.lower() == "bulbasaur") + int(two.lower() == "ivysaur"),
            "confidence": "structural: 11-byte name slots, slots 1-3 decode",
            "evidence": f"slots 1-3 read {one} / {two} / {three}; {valid} of {last_good + 1} slots decode",
        }
    # Every slot shift of one table repeats the run;
    # the Bulbasaur/Ivysaur anchor wins, then the widest span
    kept: list[dict] = []
    order = sorted(found.values(), key=lambda c: (-c["anchor"], -c["count"], c["base"]))
    for candidate in order:
        if any(c["base"] <= candidate["base"] < c["base"] + c["count"] * c["stride"] for c in kept):
            continue
        kept.append(candidate)
    return kept


def _inline_name_fields(data: bytes, stats_runs: list[dict], cm: Charmap) -> list[dict]:
    """Name fields embedded in the species records, one per stats-run candidate."""
    found: list[dict] = []
    for run in stats_runs:
        stride, count, base = run["stride"], min(run["count"], 400), run["base"]
        for field in range(0, stride - NAME_STRIDE + 1):
            valid = 0
            first = None
            for i in range(1, count):
                name = _slot_name(cm, data, base + i * stride + field)
                if name is not None:
                    valid += 1
                    if first is None:
                        first = name
            if first is None or valid / (count - 1) < 0.8:
                continue
            found.append({
                "kind": "species names (inline field)",
                "base": base,
                "stride": stride,
                "field": field,
                "count": run["count"],
                "slot1": first,
                "anchor": int(first.lower() == "bulbasaur"),
                "confidence": "structural: a field of the species records decodes as names",
                "evidence": f"field +{field:#x} of the {run['stride']}-byte records; "
                            f"{valid} of {count - 1} rows decode, row 1 reads {first}",
            })
    return sorted(found, key=lambda c: (-c["anchor"], -c["count"], c["base"]))


def _stats_runs(data: bytes, name_count: int | None) -> list[dict]:
    """Runs whose slot 1 holds Bulbasaur's stat bytes in either engine order."""
    found: dict[tuple[int, int], dict] = {}
    for stride in STAT_STRIDES:
        for order, anchor in BULBASAUR_ANCHORS.items():
            start = 0
            while True:
                hit = data.find(anchor, start)
                if hit < 0:
                    break
                base = hit - stride
                start = hit + 1
                if base < 0 or (base, stride) in found:
                    continue
                if base + stride * 3 > len(data):
                    continue
                ivy = IVYSAUR_ANCHORS[order]
                ivy_hit = data[base + stride * 2:base + stride * 2 + 6] == ivy
                plausible = total = 0
                last_good = -1
                at = base + stride
                while at + 6 <= len(data) and total < 6000:
                    stats = data[at:at + 6]
                    types = data[at + 6:at + 8]
                    if all(1 <= b <= 255 for b in stats) and all(b <= 18 for b in types):
                        plausible += 1
                        last_good = total
                    total += 1
                    at += stride
                    if total > 64 and total - plausible >= 64:
                        break
                count = last_good + 2   # slot 0 leads the run, the junk tail is cut
                if count < 100 or plausible / (last_good + 1) < 0.8:
                    continue
                # species runs overrun the table; names bound them
                bound = ""
                if name_count and count > name_count * 1.2:
                    count = name_count
                    plausible = min(plausible, count)
                    bound = f"; count bounded by the name table at {name_count}"
                found[(base, stride)] = {
                    "kind": "species info / base stats",
                    "base": base,
                    "stride": stride,
                    "count": count,
                    "order": order,
                    "confidence": "structural: Bulbasaur stat anchor"
                                  + (", Ivysaur row matches" if ivy_hit else ""),
                    "evidence": f"slot 1 starts with the six Bulbasaur stat bytes in the {order} order"
                                + (f"; slot 2 is Ivysaur" if ivy_hit else "")
                                + f"; {plausible} of {last_good + 1} rows keep stats 1-255 and types 0-18"
                                + bound,
                }
    return sorted(found.values(), key=lambda c: (-c["count"], c["base"]))


def _dex_maps(data: bytes) -> list[dict]:
    """u16 runs whose entries 1, 25 and 133 read their own index."""
    found: dict[int, dict] = {}
    for m in _U16_ANCHOR.finditer(data):
        off = m.start()
        if off - 2 in found:
            continue
        if off + 132 * 2 + 2 > len(data):
            continue
        if int.from_bytes(data[off + 132 * 2:off + 132 * 2 + 2], "little") != 133:
            continue
        # entry 0 leads the run, as in real dex maps
        base = off - 2 if int.from_bytes(data[off - 2:off], "little") == 0 else off
        values = []
        at = base
        while at + 2 <= len(data) and len(values) < 6000:
            value = int.from_bytes(data[at:at + 2], "little")
            if value > 1500:
                break
            values.append(value)
            at += 2
        if len(values) < 100:
            continue
        distinct = len(set(values))
        dense = sum(1 for v in values if v < len(values))
        if distinct / len(values) < 0.6 or dense / len(values) < 0.8:
            continue
        found[base] = {
            "kind": "species to national dex map",
            "base": base,
            "stride": 2,
            "count": len(values),
            "confidence": "structural: entries 1, 25 and 133 read their own index",
            "evidence": f"u16 run; {distinct} distinct of {len(values)} values, {dense} below the run length",
        }
    return sorted(found.values(), key=lambda c: (-c["count"], c["base"]))


def _lz77_shapes(data: bytes) -> int:
    return len(_LZ77_SHAPE.findall(data))


def probe(rom: Rom) -> dict:
    """Everything the ROM alone establishes, candidates and unknowns included."""
    data = bytes(rom.data)
    cm = Charmap()
    header = _header(data)
    engine = _engine(data, header)
    names = _name_runs(data, cm)
    best = names[0] if names else None
    name_count = best["count"] if best and best["count"] >= 400 else None   # small runs are other lists
    stats = _stats_runs(data, name_count)
    inline_names = _inline_name_fields(data, stats, cm)
    if inline_names and (not best or inline_names[0]["anchor"] > best.get("anchor", 0)
                         or best["kind"] != "species names"):
        names = inline_names + names
        best = names[0]
    maps = _dex_maps(data)
    charmap = {
        "verdict": "western-gen3" if best else "unknown",
        "evidence": (f"the species name slots decode as {best['slot1']} / {best.get('slot2', '?')} / "
                     f"{best.get('slot3', '?')} at {best['base']:#x}") if best
        else "no plain name run found; the text answer is not settled",
    }
    species_count = best["count"] if best else None
    unknown = ["dex entry text", "evolution records", "sprite and palette tables"]
    return {
        "rom": {"path": str(rom.path or ""), "sha1": rom.sha1(), "size": len(data),
                "game_code": header["game_code"], "title": header["title"]},
        "engine": engine,
        "charmap": charmap,
        "species_count": species_count,
        "lz77_shapes": _lz77_shapes(data),
        "candidates": names + stats + maps,
        "unknown": unknown,
    }


def format_report(doc: dict) -> str:
    """The probe report, one line per finding, as the command prints it."""
    rom = doc["rom"]
    lines = [
        "Pokedex probe: structural candidates only, none proven",
        "",
        f"rom:    {rom['path']}",
        f"sha1:   {rom['sha1']}",
        f"header: {rom['game_code']} {rom['title']} ({rom['size']:#x} bytes)",
        f"engine: {doc['engine']['family']} - {doc['engine']['evidence']}",
        f"text:   {doc['charmap']['verdict']} - {doc['charmap']['evidence']}",
        f"species count: {doc['species_count'] if doc['species_count'] is not None else 'unknown'}",
        f"LZ77-shaped blocks: {doc['lz77_shapes']}",
        "",
        "Candidates:",
    ]
    if not doc["candidates"]:
        lines.append("  (none)")
    for c in doc["candidates"]:
        lines.append(f"- {c['kind']}: base {c['base']:#x}, stride {c['stride']}, count {c['count']}")
        lines.append(f"  confidence: {c['confidence']}")
        lines.append(f"  evidence: {c['evidence']}")
    lines += ["", "Unproven:", ]
    for u in doc["unknown"]:
        lines.append(f"- {u}")
    lines += ["", "Next: gen3tl data profile writes a skeleton; each table needs its evidence line."]
    return "\n".join(lines) + "\n"


def _toml_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def skeleton(rom_path: str, doc: dict) -> str:
    """The profile skeleton text: discovered candidates, empty evidence, nothing proven."""
    best_stats = next((c for c in doc["candidates"] if c["kind"] == "species info / base stats"), None)
    best_map = next((c for c in doc["candidates"] if c["kind"] == "species to national dex map"), None)
    rom = doc["rom"]
    lines = [
        "# Pokedex profile skeleton, written by gen3tl data profile.",
        "# The probe reports candidates only; nothing here is proven. data pokedex",
        "# refuses this file until every evidence line is filled.",
        "#",
        "# Probe findings (structural confidence only):",
        f"#   engine: {doc['engine']['family']} ({rom['game_code']})",
        f"#   text: {doc['charmap']['verdict']}",
        f"#   species count: {doc['species_count'] if doc['species_count'] is not None else 'unknown'}",
    ]
    for c in doc["candidates"]:
        lines.append(f"#   candidate: {c['kind']} base {c['base']:#x} stride {c['stride']} "
                     f"count {c['count']} ({c['confidence']})")
    lines += [
        "",
        "[rom]",
        f"name = {_toml_str(rom['title'])}",
        f"path = {_toml_str(rom_path)}",
        f"sha1 = {_toml_str(rom['sha1'])}",
        f"size = {rom['size']:#x}",
        "",
        "[engine]",
        f"family = {_toml_str(doc['engine']['family'])}",
        "",
        "[pokedex]",
        "schema = 1",
        'name_table = "species"   # the [[table]] whose ids name the slots',
        "",
        "[pokedex.species]",
    ]
    if best_stats:
        lines += [f"base = {best_stats['base']:#x}",
                  f"stride = {best_stats['stride']}",
                  f"count = {best_stats['count']}",
                  "first = 1",
                  'evidence = ""   # REQUIRED: the code or data cross-reference that proves this table']
    else:
        lines += ["# base = 0x0   # required: slot 0 of the species info table",
                  "# stride = 0",
                  "# count = 0",
                  "# first = 1",
                  'evidence = ""   # REQUIRED: the code or data cross-reference that proves this table']
    lines += [
        "# fields = { hp = 0, attack = 1, defense = 2, speed = 3, sp_attack = 4, sp_defense = 5,",
        "#           type1 = 6, type2 = 7, catch_rate = 8, base_exp = 9, growth = 19,",
        "#           ability1 = 22, ability2 = 23, gender_ratio = 16 }",
        "# The FireRed order is hp, attack, defense, sp_attack, sp_defense, speed;",
        "# the Emerald order is hp, attack, defense, speed, sp_attack, sp_defense.",
        "",
        "# [pokedex.dex]",
        "# index = \"dex\"   # records indexed by national dex",
        "# [pokedex.dex.map]",
    ]
    if best_map:
        lines += [f"# base = {best_map['base']:#x}",
                  "# stride = 2",
                  "# width = 2",
                  '# evidence = "..."']
    else:
        lines += ["# base = 0x0", "# stride = 2", "# width = 2", '# evidence = "..."']
    lines += [
        "# [pokedex.dex.records]",
        "# base = 0x0   # one 36-byte row per national dex number (the Emerald hacks)",
        "# stride = 36",
        '# evidence = "..."',
        "# fields = { category = 0, height = 12, weight = 14, description = { offset = 16, width = 4 } }",
        "# An inline category (no pointer) is recorded as category_offset.",
        "",
        "# [pokedex.entries]",
        "# pointer = 0x10   # the description pointer's offset inside each record",
        "# stride = 36      # the record stride, when the pointer lives in a record",
        '# evidence = "..."',
        "",
        "# [pokedex.evolution]   # one of the two shapes pokedexdata.py documents",
        "# index = \"slot\"       # a fixed block per slot",
        "# base = 0x0",
        "# stride = 128",
        "# slots = 16",
        "# record = 8",
        "# stop = 0",
        "# fields = { method = { offset = 0, width = 2 }, parameter = { offset = 2, width = 2 },",
        "#           target = { offset = 4, width = 2 }, extra = { offset = 6, width = 2 } }",
        '# evidence = "..."',
        "",
        "# Unproven fields stay commented out. data pokedex names every missing",
        "# field and every empty evidence line when it refuses this file.",
        "",
    ]
    return "\n".join(lines) + "\n"
