"""String inventory: every translatable string with id, references and category."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..core.lists import append_unique
from ..core.notes import has_prose
from ..core.charmap import Charmap, CharmapError
from ..core.fields import long_name_fields
from ..profile.profile import Profile
from ..profile.schema import classifier_target
from ..core.rom import Rom
from .references import inventory_classifications
from .scan import (FIELD_TEXT_KINDS, INPLACE_ONLY_KINDS, VERIFIED_KINDS, Found, classify_refs, discover,
                   table_base_lengths, table_base_members, table_base_problems)

FD_FIELD = set(range(0x01, 0x07))
COPY = "copy"   # flag of a declared group's own entry
GROUP_NAME = re.compile(r"[a-z][a-z0-9-]*")


@dataclass
class Entry:
    id: str                     # s<ROM offset hex> | <table>:<index>, stable across runs
    offset: int
    length: int                 # bytes with terminator (fixed, declared: field length)
    kind: str                   # pointer | fixed
    category: str
    mode: str                   # field | battle (placeholder naming)
    text: str
    refs: list[dict] = field(default_factory=list)
    table: str | None = None
    index: int | None = None
    flags: list[str] = field(default_factory=list)
    mode_evidence: dict = field(default_factory=dict)
    species: list[str] | None = None   # species ids a record serves


def _mode_for(raw: bytes) -> str:
    i = 0
    while i < len(raw) - 1:
        if raw[i] == 0xFD:
            if raw[i + 1] not in FD_FIELD:
                return "battle"
            i += 2
            continue
        i += 1
    return "field"


def _category(refs: list[dict]) -> str:
    kinds = {r["kind"] for r in refs if r["kind"] in VERIFIED_KINDS or r["kind"].startswith("table:")}
    if any(k.startswith("table:") for k in kinds):
        return "name-table"
    if "script:setword" in kinds:
        return "battle-script"
    if kinds & {"script:loadpointer", "script:message", "script:trainerbattle", "script:autoscroll",
                "script:vmessage", "script:chain", "script:bufferstring"}:
        return "script"
    if kinds & {"pool", "table-base"}:
        return "code"
    if kinds & {"table", "struct"}:
        return "data-table"
    return "unverified"


def text_modes(rules: list[dict]) -> dict[str, dict]:
    """Validate evidence-bound placeholder modes."""
    if not isinstance(rules, list):
        raise ValueError("strings.text_mode must be a list")
    out = {}
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != {"id", "mode", "refs", "evidence"}:
            raise ValueError("strings.text_mode needs id, mode, refs and evidence")
        sid, mode, refs, evidence = (rule[k] for k in ("id", "mode", "refs", "evidence"))
        if (not isinstance(sid, str) or not re.fullmatch(r"s[0-9A-F]{7}", sid)
                or mode not in ("field", "battle")
                or not has_prose(evidence, single_line=True)):
            raise ValueError("strings.text_mode needs a pointer ID, valid mode and one-line evidence")
        if not isinstance(refs, list) or not refs:
            raise ValueError(f"strings.text_mode {sid} needs the complete reference set")
        pairs = []
        for ref in refs:
            if (not isinstance(ref, dict) or set(ref) != {"at", "kind"}
                    or type(ref["at"]) is not int or ref["at"] < 0
                    or not isinstance(ref["kind"], str) or not ref["kind"].strip()):
                raise ValueError(f"strings.text_mode {sid} has an invalid reference")
            pairs.append((ref["at"], ref["kind"]))
        if len(pairs) != len(set(pairs)):
            raise ValueError(f"strings.text_mode {sid} has duplicate references")
        if sid in out:
            raise ValueError(f"duplicate strings.text_mode entry {sid}")
        out[sid] = rule
    return out


def split_rules(rules: list[dict]) -> list[dict]:
    """Validate [[strings.split]]: a named group of one text's references."""
    if not isinstance(rules, list):
        raise ValueError("strings.split must be a list")
    seen: set[tuple[str, str]] = set()
    taken: dict[tuple[str, int], str] = {}
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != {"id", "group", "refs", "evidence"}:
            raise ValueError("strings.split needs id, group, refs and evidence")
        sid, group, refs = rule["id"], rule["group"], rule["refs"]
        if (not isinstance(sid, str) or not re.fullmatch(r"s[0-9A-F]{7}", sid)
                or not isinstance(group, str) or not GROUP_NAME.fullmatch(group)
                or not has_prose(rule["evidence"])):
            raise ValueError("strings.split needs a pointer ID, a group name and evidence")
        if (not isinstance(refs, list) or not refs
                or any(type(at) is not int or at < 0 for at in refs) or len(set(refs)) != len(refs)):
            raise ValueError(f"strings.split {sid} {group} needs distinct reference positions")
        if (sid, group) in seen:
            raise ValueError(f"duplicate strings.split group {sid} {group}")
        seen.add((sid, group))
        for at in refs:
            if (sid, at) in taken:
                raise ValueError(f"strings.split {sid}: reference {at:#x} in groups {taken[(sid, at)]} and {group}")
            taken[(sid, at)] = group
    return rules


def copy_id(sid: str, group: str) -> str:
    return f"{sid}-{group}"


def _split(entries: list[Entry], rules: list[dict]) -> list[Entry]:
    """Entries with each declared group moved into a copy entry after its text."""
    if not rules:
        return entries
    by_id = {e.id: e for e in entries}
    copies: dict[str, list[Entry]] = {}
    for rule in rules:
        sid, group = rule["id"], rule["group"]
        e = by_id.get(sid)
        if e is None or e.kind != "pointer":
            raise ValueError(f"strings.split {sid} {group}: no pointer string {sid} in the inventory")
        refs = {r["at"]: r for r in e.refs}
        missing = [at for at in rule["refs"] if at not in refs]
        if missing:
            raise ValueError(f"strings.split {sid} {group}: references not in the inventory: "
                             + ", ".join(f"{at:#x}" for at in missing))
        moved = [refs[at] for at in rule["refs"]]
        bad = [r for r in moved if r["kind"] not in VERIFIED_KINDS or r["kind"] in INPLACE_ONLY_KINDS]
        if bad:
            raise ValueError(f"strings.split {sid} {group}: reference {bad[0]['at']:#x} "
                             f"({bad[0]['kind']}) cannot be rewritten")
        rest = [r for r in e.refs if r["at"] not in set(rule["refs"])]
        if not rest:
            raise ValueError(f"strings.split {sid} {group}: the group takes every reference")
        e.refs = rest
        copy = Entry(id=copy_id(sid, group), offset=e.offset, length=e.length, kind=e.kind,
                     category=e.category, mode=e.mode, text=e.text,
                     refs=sorted(moved, key=lambda r: r["at"]), table=e.table, index=e.index,
                     flags=[f for f in e.flags if f != "overlap"] + [COPY],
                     mode_evidence=dict(e.mode_evidence), species=e.species)
        copies.setdefault(sid, []).append(copy)
    return [x for e in entries for x in [e] + copies.get(e.id, [])]


def _dex_species(prof: Profile, rom: Rom) -> dict[int, list[str]]:
    """Dex number -> species catalog ids, the reverse of [pokedex.dex.map]."""
    block = prof.get("pokedex") or {}
    spec = (block.get("dex") or {}).get("map")
    species = block.get("species") or {}
    if not spec or not species:
        raise ValueError("[[strings.pointer_table]] species_dex_map needs [pokedex.dex.map] and [pokedex.species]")
    base, stride, width = int(spec["base"]), int(spec.get("stride", 2)), int(spec.get("width", 2))
    count = int(species["count"])
    first = int(species.get("first", 1))
    out: dict[int, list[str]] = {}
    for slot in range(first, count):
        if base + slot * stride + width > len(rom.data):
            break
        value = int.from_bytes(rom.data[base + slot * stride:base + slot * stride + width], "little")
        out.setdefault(value, []).append(f"species:{slot}")
    return out


def _pointer_table_species(prof: Profile, rom: Rom, pt: dict) -> dict[int, list[str]] | None:
    """Record index -> species ids when the table declares species_dex_map."""
    if pt.get("species_dex_map"):
        return _dex_species(prof, rom)
    if pt.get("species_slot"):
        return {i: [f"species:{i}"] for i in range(int(pt.get("count", 0)))}
    return None


def build(prof: Profile, rom: Rom, cm: Charmap | None = None, *,
          save_link_keys: tuple[str, ...] = ("save_link", "save_link_defaults", "save_link_letters")) -> dict:
    cm = cm or Charmap()
    modes = text_modes(prof.get("strings", {}).get("text_mode", []))
    strings = prof.get("strings", {})
    classified = strings.get("classified", [])
    if not isinstance(classified, list):
        raise ValueError("strings.classified: expected a list")
    outcomes = [classifier_target(value) for value in classified]
    if any(value["offset"] >= len(rom) for value in classified):
        raise ValueError("strings.classified: offset outside ROM")
    includes = list(dict.fromkeys([*strings.get("include", []),
                                  *(value for action, value in outcomes if action == "include")]))
    excluded = set(strings.get("exclude", [])) | {value for action, value in outcomes if action == "exclude"}
    d = rom.data
    tbo = {int(k): tuple(v) for k, v in prof.get("script", {}).get("trainerbattle_text_offsets", {}).items()}
    tables = prof.get("table", [])
    exclude = [(t["base"], t["base"] + t["stride"] * t["count"]) for t in tables]
    found, rejected_mid, idx = discover(rom, cm, exclude=exclude, trainerbattle_offsets=tbo)
    by_off: dict[int, Found] = {f.offset: f for f in found}
    # profile-listed strings the scan cannot recognise (short, no letters);
    # decode limit is the scan's; such strings may be long
    unverified_includes: list[int] = []
    for off in includes:
        if off not in by_off:
            _, end = cm.decode(d, off, limit=4000)
            by_off[off] = Found(off, end, classify_refs(rom, idx, {off}, tbo).get(off, []))
            if not by_off[off].verified_refs():
                unverified_includes.append(off)
    if unverified_includes:
        # resolve included labels that need sibling text pointers as evidence.
        # Keep references already verified by the original classification unchanged.
        refs = classify_refs(rom, idx, set(by_off), tbo)
        for off in unverified_includes:
            by_off[off].refs = refs[off]
    # Declared pointer tables ([[strings.pointer_table]]): every target is catalogued;
    # bytes never exclude one (empty line reads as movement);
    # entries record table and index; empty strings are skipped.
    pointer_tables = prof.get("strings", {}).get("pointer_table", [])
    in_table: dict[int, tuple[str, int]] = {}
    in_table_species: dict[int, list[str]] = {}
    for pt in pointer_tables:
        dex_species = _pointer_table_species(prof, rom, pt)
        for i in range(pt["count"]):
            for k in pt["fields"]:
                off = rom.ptr_at(pt["base"] + pt["stride"] * i + k)
                if off is None or d[off] == 0xFF:
                    continue
                in_table.setdefault(off, (pt["name"], i))
                if dex_species is not None:
                    sp = dex_species.get(i, [])
                    cur = in_table_species.setdefault(off, [])
                    cur += [s for s in sp if s not in cur]
                if off not in by_off:
                    _, end = cm.decode(d, off, limit=4000)
                    by_off[off] = Found(off, end, classify_refs(rom, idx, {off}, tbo).get(off, []))
    # Tables indexed from a pooled base ([[strings.table_base]]):
    # - every member is catalogued, whatever its bytes
    # - table-base references pin members in place (INPLACE_ONLY_KINDS)
    table_base_at: dict[int, list[int]] = {}
    field_len: dict[int, int] = {}   # declared field lengths
    for tb in prof.get("strings", {}).get("table_base", []):
        problems = table_base_problems(rom, tb)
        if problems:
            raise ValueError("; ".join(problems))
        field_len.update(table_base_lengths(tb))
        for off in table_base_members(tb):
            if d[off] == 0xFF:
                continue   # an empty member shows nothing
            words = table_base_at.setdefault(off, [])
            words += [p for p in tb["pointers"] if p not in words]
            if off not in by_off:
                _, end = cm.decode(d, off, limit=4000)
                by_off[off] = Found(off, end, classify_refs(rom, idx, {off}, tbo).get(off, []))
    entries: list[Entry] = []
    table_ptr_refs: dict[int, list[dict]] = {}

    for t in tables:
        extended = long_name_fields(t)
        for i in range(t["count"]):
            rec = t["base"] + t["stride"] * i
            off = rec + t.get("field_offset", 0)
            flen = t["field_length"]
            if t.get("long_name_pointers") and rom.ptr_at(off) is not None:
                target = rom.ptr_at(off)
                table_ptr_refs.setdefault(target, []).append({"at": off, "kind": f"table:{t['name']}"})
                continue
            try:
                if t.get("terminated", True):
                    text, end = cm.decode(d, off, limit=flen)
                else:  # filled to its length, unterminated (keyboard keys)
                    text, end = cm.decode(bytes(d[off:off + flen]) + b"\xff", 0)
            except CharmapError:
                continue
            e = Entry(id=f"{t['name']}:{i}", offset=off, length=flen, kind="fixed",
                      category=f"table:{t['name']}", mode="field", text=text,
                      table=t["name"], index=i, mode_evidence={"rule": "fixed-field", "table": t["name"]})
            if i in extended:
                e.mode_evidence["pointer_length"] = extended[i]["length"]
                e.mode_evidence["pointer_evidence"] = extended[i]["evidence"]
            if not t.get("terminated", True):
                e.flags.append("unterminated")
            if t.get("save_link"):   # copied into save or link data
                e.flags.append("save-link")
            if t.get("translate") is False:
                e.flags.append("keep")
            if "gender_offset" in t:
                e.flags.append("female" if d[rec + t["gender_offset"]] & 0x80 else "male")
            if "class_offset" in t:
                e.flags.append(f"class={d[rec + t['class_offset']]}")
            entries.append(e)

    # long-name pointer targets discovery missed
    for target in table_ptr_refs:
        if target not in by_off:
            try:
                _, end = cm.decode(d, target, limit=200)
            except CharmapError:
                continue
            by_off[target] = Found(target, end, [])

    save_link_ids = {sid for key in save_link_keys for sid in prof.get("strings", {}).get(key, [])}
    for off in sorted(by_off):
        f = by_off[off]
        if f"s{off:07X}" in excluded:
            continue
        refs = [{"at": r.at, "kind": r.kind} for r in f.refs] + table_ptr_refs.get(off, [])
        for p in table_base_at.get(off, []):
            hit = [r for r in refs if r["at"] == p]
            for r in hit:
                r["kind"] = "table-base"
            if not hit:
                refs.append({"at": p, "kind": "table-base"})
        raw = bytes(d[off:f.end])
        mode = _mode_for(raw)
        text, _ = cm.decode(d, off, mode=mode)
        cat = _category(refs)
        e = Entry(id=f"s{off:07X}", offset=off, length=field_len.get(off, f.end - off), kind="pointer",
                  category=cat, mode=mode, text=text, refs=refs)
        if off in in_table:
            e.table, e.index = in_table[off]
            if in_table_species.get(off):
                e.species = in_table_species[off]
            # evidence proves declared tables the run detector misses
            if e.category == "unverified":
                e.category = "data-table"
        if e.id in save_link_ids:
            e.flags.append("save-link")
        if any(r["kind"] in INPLACE_ONLY_KINDS for r in refs):
            e.flags.append("in-place-only")
        if not any(r["kind"] in VERIFIED_KINDS or r["kind"].startswith("table:") for r in refs) and e.table is None:
            e.flags.append("unverified")
        entries.append(e)

    # Fallback: battle mode spreads to strings with ambiguous placeholders;
    # (0xFD 01-06) sharing a table or region with battle text.
    # Collect seeds before field overrides.
    def has_fd(e: Entry) -> bool:
        raw = bytes(d[e.offset:e.offset + e.length])
        return b"\xfd" in raw
    ptr_entries = [x for x in entries if x.kind == "pointer"]
    missing = set(modes) - {x.id for x in ptr_entries}
    if missing:
        raise ValueError(f"strings.text_mode entries missing from inventory: {', '.join(sorted(missing))}")
    battle_regions: dict[int, int] = {}
    battle_runs: set[int] = set()
    runs = _pointer_runs(ptr_entries)
    for x in ptr_entries:
        if x.mode == "battle":
            battle_regions[x.offset >> 16] = battle_regions.get(x.offset >> 16, 0) + 1
            battle_runs.update(runs.get(r["at"], -1) for r in x.refs)
    for x in ptr_entries:
        initial_mode = x.mode
        evidence = {"fallback_reasons": ["placeholder-bytes"] if x.mode == "battle" else []}
        if x.mode == "field" and has_fd(x):
            matching_runs = sorted({runs[r["at"]] for r in x.refs
                                    if r["at"] in runs and runs[r["at"]] in battle_runs})
            if matching_runs:
                evidence["fallback_reasons"].append("pointer-run")
                evidence["pointer_runs"] = matching_runs
            region_count = battle_regions.get(x.offset >> 16, 0)
            if region_count >= 5:
                evidence["fallback_reasons"].append("battle-region")
                evidence["battle_region"] = (x.offset >> 16) << 16
                evidence["battle_region_count"] = region_count
            if x.category == "battle-script":
                evidence["fallback_reasons"].append("battle-script")
            if evidence["fallback_reasons"]:
                x.mode = "battle"
        if not evidence["fallback_reasons"]:
            evidence["fallback_reasons"].append("default-field")
        evidence["fallback_mode"] = x.mode
        kinds = {r["kind"] for r in x.refs}
        evidence["reference_kinds"] = sorted(kinds)
        # Non-exclusive reference sets keep the original seeds and fallback.
        # Unknown references are kept when deciding exclusivity.
        if kinds and kinds <= FIELD_TEXT_KINDS:
            x.mode = "field"
            evidence["rule"] = "script-text-only"
        elif kinds & FIELD_TEXT_KINDS:
            evidence["rule"] = "mixed-references"
        else:
            evidence["rule"] = "reference-fallback"
        if x.id in modes:
            rule = modes[x.id]
            expected = sorted((r["at"], r["kind"]) for r in rule["refs"])
            actual = sorted((r["at"], r["kind"]) for r in x.refs)
            if actual != expected:
                raise ValueError(f"strings.text_mode {x.id} reference set changed: "
                                 f"expected {expected}, found {actual}")
            evidence["inferred_rule"] = evidence["rule"]
            evidence["rule"] = "profile-text-mode"
            evidence["profile_mode"] = rule["mode"]
            evidence["evidence"] = rule["evidence"]
            x.mode = rule["mode"]
        x.mode_evidence = evidence
        if x.mode != initial_mode:
            x.text = cm.decode(d, x.offset, mode=x.mode)[0]
    # entries sharing bytes: no in-place write, no slot reuse
    ordered = sorted(entries, key=lambda x: x.offset)
    for a, b in zip(ordered, ordered[1:]):
        if b.offset < a.offset + a.length:
            for x in (a, b):
                append_unique(x.flags, "overlap")
    entries = _split(entries, split_rules(prof.get("strings", {}).get("split", [])))
    out_entries = []
    for e in entries:
        d = asdict(e)
        if d["species"] is None:   # only records the table tied to species
            d.pop("species")
        out_entries.append(d)
    inventory = {
        "profile": prof.name,
        "rom_sha1": rom.sha1(),
        "rejected_mid_string_targets": rejected_mid,
        "excluded": sorted(excluded),
        "pointer_tables": [{k: v for k, v in pt.items() if k != "evidence"} for pt in pointer_tables],
        "entries": out_entries,
    }
    inventory_classifications(rom, inventory, prof, idx)
    return inventory


def _pointer_runs(entries: list[Entry]) -> dict[int, int]:
    """Reference position -> start of its run of aligned references (stride 4-64)."""
    pos = sorted({r["at"] for e in entries for r in e.refs if r["kind"] in ("table", "struct", "pool")})
    out: dict[int, int] = {}
    i = 0
    while i < len(pos):
        j = i
        stride = pos[i + 1] - pos[i] if i + 1 < len(pos) else 0
        if 4 <= stride <= 64:
            while j + 1 < len(pos) and pos[j + 1] - pos[j] == stride:
                j += 1
        for k in range(i, j + 1):
            out[pos[k]] = pos[i]
        i = j + 1
    return out


def save(inv: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(inv, ensure_ascii=False, indent=0), encoding="utf-8")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def report(inv: dict) -> str:
    es = inv["entries"]
    lines = [f"# Inventory report ({inv['profile']})", "",
             f"Entries: {len(es)}; coincidental mid-string pointer targets rejected: {inv['rejected_mid_string_targets']}", "",
             "| Category | Entries | Bytes |", "|---|---|---|"]
    cats = Counter(); size = Counter()
    for e in es:
        cats[e["category"]] += 1
        size[e["category"]] += e["length"]
    for c in sorted(cats, key=lambda c: -size[c]):
        lines.append(f"| {c} | {cats[c]} | {size[c]} |")
    kinds = Counter(r["kind"] for e in es for r in e["refs"])
    lines += ["", "| Reference kind | Count |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in kinds.most_common()]
    modes = Counter(e.get("mode_evidence", {}).get("rule", "unrecorded") for e in es)
    lines += ["", "| Placeholder mode rule | Entries |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in modes.most_common()]
    lines += ["", "Undeclared mixed references retain the placeholder-byte, pointer-run and region fallback; "
              "each entry records its rule, fallback and reference kinds in `mode_evidence`, "
              "with pointer positions in `refs`."]
    lines += ["Profile text modes require evidence and an exact reference-set match."]
    flags = Counter(f for e in es for f in e["flags"] if "=" not in f)
    lines += ["", "| Flag | Entries |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in flags.most_common()]
    unv = [e for e in es if "unverified" in e["flags"]]
    lines += ["", f"Unverified strings (identified, never rewritten): {len(unv)}, "
              f"{sum(e['length'] for e in unv)} bytes; listed in inventory.json with flag `unverified`."]
    held = [e for e in es if e.get("relocation", {}).get("unclassified")]
    lines += ["", f"Relocation held by unclassified references: {len(held)}", "",
              "| String | Unclassified references |", "|---|---|"]
    lines += [f"| {e['id']} | {', '.join(hex(at) for at in e['relocation']['unclassified'])} |" for e in held]
    return "\n".join(lines) + "\n"


def untranslated_slot_usage(inv: dict, slots: set[int], cm: Charmap, catalog,
                            included: set[str] | None = None) -> list[tuple[str, int]]:
    """Find source glyph collisions without replacements the build includes."""
    sources = {e["id"]: e["text"] for e in inv["entries"]}
    unresolved = []
    for sid, byte in slot_usage(inv, slots, cm, unverified=True):
        pair = catalog.get(sid)
        entry = pair[1] if pair else None
        english = None if entry is None else sources[sid] if entry.keyed else entry.msgid
        if (entry is None or not entry.msgstr
                or english != sources[sid] or entry.msgstr == english
                or (included is not None and sid not in included)):
            unresolved.append((sid, byte))
    return unresolved


def slot_usage(inv: dict, slots: set[int], cm: Charmap, unverified: bool = False) -> list[tuple[str, int]]:
    """(entry id, byte) for every verified entry whose text uses one of `slots`; `unverified` adds the rest."""
    hits = []
    for e in inv["entries"]:
        if "unverified" in e["flags"] and not unverified:
            continue
        raw = cm.encode(e["text"], terminate=False)
        i = 0
        while i < len(raw):
            b = raw[i]
            if b in (0xFC,):
                code = raw[i + 1] if i + 1 < len(raw) else 0
                from ..core.charmap import CTRL_CODES
                i += 2 + CTRL_CODES.get(code, ("", 0))[1]
                continue
            if b in (0xFD, 0xF7, 0xF8, 0xF9):
                i += 2
                continue
            if b in slots:
                hits.append((e["id"], b))
            i += 1
    return hits
