"""String classifier outcomes from table fields and bounded binary containers."""

from __future__ import annotations

import re
import string

from ..core.charmap import PREFIXED_ARG, Charmap, CharmapError
from ..core.font import glyph_size
from .references import Spans, binary_spans, literal_word, text_record
from .scan import discover, is_string_start, is_text, tokens


def table_fields(rom, found, index):
    """Recognize alphabet grids, ordinal records and bounded text arrays."""
    cm, decoded, result = Charmap(), {}, []

    def text(target):
        if target not in decoded:
            try:
                decoded[target] = cm.decode(rom.data, target, limit=4000) if target is not None else (None, 0)
            except CharmapError:
                decoded[target] = (None, 0)
        return decoded[target]

    def ordinal(at):
        target = rom.ptr_at(at) if 0 <= at <= len(rom) - 12 else None
        return target is not None and rom.u32(at + 8) == 1 and rom.u32(at + 4) < 4096 and bool(text(target)[0])

    seen = set()
    for at in sorted({at for refs in index.values() for at in refs if at % 4 == 0}):
        if at in seen or not ordinal(at):
            continue
        start, end = at, at + 12
        while ordinal(start - 12):
            start -= 12
        while ordinal(end):
            end += 12
        seen.update(range(start, end, 12))
        count = (end - start) // 12
        if (count < 3 or {rom.u32(pos + 4) for pos in range(start, end, 12)} != set(range(count))
                or sum(rom.ptr_at(pos) in found for pos in range(start, end, 12)) < count / 2):
            continue
        result.extend((pos, "ordinal-word-field", start) for pos in range(start, end, 12))
    positions = {at: target for target, refs in index.items() for at in refs if at % 4 == 0}
    numeric = {}
    for at, target in positions.items():
        value = text(target)[0]
        match = re.fullmatch(r"([A-Za-z]*)([0-9]{1,2})([A-Za-z]*)", value or "")
        if match and (match[1] or match[3]):
            numeric[at] = (match[1], int(match[2]), match[3])
    for stride in range(4, 65, 4):
        for start, (prefix, number, suffix) in sorted(numeric.items()):
            for direction in (-1, 1):
                if numeric.get(start - stride) == (prefix, number - direction, suffix):
                    continue
                count = 1
                while numeric.get(start + count * stride) == (prefix, number + count * direction, suffix):
                    count += 1
                if count >= 3:
                    result.extend((start + i * stride, "ordinal-label-field", start) for i in range(count))
    seen.clear()
    for start in sorted(positions):
        if start in seen:
            continue
        end = start
        while end in positions:
            seen.add(end)
            end += 4
        fields = list(range(start, end, 4))
        if len(fields) < 3:
            continue
        rows = [text(positions[pos])[0] for pos in fields]
        first = rows[0] or ""
        width = len(first)
        if (2 <= width <= 13 and first == string.ascii_uppercase[:width]
                and rows[1] == string.ascii_uppercase[width:width * 2]
                and first.lower() in rows and rows[1].lower() in rows
                and all(value is not None and sum(1 for _ in tokens(
                    rom.data, positions[pos], text(positions[pos])[1] - 1)) == width
                        for pos, value in zip(fields, rows))):
            result.extend((pos, "alphabet-grid-field", start) for pos in fields)
        letters, clear_fields = "", []
        for pos, value in zip(fields, rows):
            if value is None or "{CLEAR " not in value:
                break
            plain = re.sub(r"\{CLEAR [0-9A-F]{2}\}", "", value).replace(" ", "")
            match = re.fullmatch(r"([A-Z]+)[a-z]*" if not clear_fields else r"([A-Z]+)", plain)
            if match is None:
                break
            letters += match[1]
            clear_fields.append(pos)
        if letters == string.ascii_uppercase:
            result.extend((pos, "positioned-alphabet-field", start) for pos in clear_fields)
        if sum(positions[pos] in found for pos in fields) < len(fields) / 2:
            continue
        for pos, value in zip(fields, rows):
            target = positions[pos]
            if target in found or not value or not is_text(rom.data, target, text(target)[1] - 1, True):
                continue
            if re.fullmatch(r"\d+ [A-Za-z] \d+", value):
                result.append((pos, "numeric-comparison-label", start))
            elif rom.data[target] == 0xFD:
                result.append((pos, "placeholder-template-field", start))
    return result


def include_targets(rom, data, found, index, used_sections=None):
    """Find missed text fields without reading manual string declarations."""
    cm, outcomes = Charmap(), {}

    def include(at, rule, anchors=()):
        target = rom.ptr_at(at)
        if target is None or target in found or rom.data[target] == 255:
            return
        try:
            text, end = cm.decode(rom.data, target, limit=4000)
        except CharmapError:
            return
        if not text.strip():
            return
        outcomes[target] = {"name": f"include-{target:07X}", "action": "include", "offset": target,
                            "classifier": rule, "anchors": sorted({at, *anchors}), "length": end - target}

    items = data.get("pokedex", {}).get("items", {})
    field = items.get("fields", {}).get("description")
    if isinstance(field, dict) and field.get("width") == 4:
        for index_ in range(items["count"]):
            include(items["base"] + index_ * items["stride"] + field["offset"],
                    "item-description-field", [items["base"]])
    sections = data.get("pokedex", {}).get("map_sections", {})
    if sections:
        for index_ in range(sections["count"]):
            at = sections["base"] + index_ * sections["stride"]
            target = rom.ptr_at(at)
            selected = (index_ + sections.get("first", 0) in used_sections if used_sections is not None else
                        target and rom.data[target - 1] not in PREFIXED_ARG and rom.data[target - 1] != 0xFD)
            if target and selected:
                include(at, "map-section-name-field", [sections["base"]])
    keys = next((row for row in data.get("table", []) if row["name"] == "naming_keyboard"), None)
    rows = next((row for row in data.get("keyboard", {}).get("table", []) if row.get("text")), None)
    if keys and rows and rows["entry"] == 16:
        for index_ in range(keys["count"]):
            include(rows["base"] + index_ * 4, "keyboard-display-field", rows.get("refs", []))
    for at, rule, start in table_fields(rom, found, index):
        include(at, rule, [start])
    for target, positions in sorted(index.items()):
        if target in found:
            continue
        try:
            text, end = cm.decode(rom.data, target, limit=4000)
        except CharmapError:
            continue
        if is_string_start(rom.data, target) and is_text(rom.data, target, end - 1, True):
            raw = rom.read(target, end - target)
            native = (raw[0] in (0xB0, 0xFE) or b"\xfc\x13" in raw
                      or re.fullmatch(r" [A-Za-z]-[A-Za-z]", text))
            if native:
                for at in positions:
                    if proof := literal_word(rom, at):
                        include(at, "native-formatted-text", [row["load"] for row in proof["loads"]])
                        break
            if raw[0] == 0xFD:
                for at in positions:
                    if at >= 5 and rom.data[at - 5] == 0xFA and rom.data[at - 2:at] == b"\x03\x02":
                        include(at, "script-word-template", [at - 5])
                        break
        if not re.fullmatch(r"(?=.*[A-Za-z])(?=.*[0-9])[A-Za-z0-9]{2,4}", text):
            continue
        for at in positions:
            if shape := text_record(rom, at):
                include(at, "short-alphanumeric-menu-field", [shape["start"]])
                break
    return list(outcomes.values())


def exclude_targets(rom, data, found, index):
    """Reject promoted text candidates inside proven non-text containers."""
    binary = Spans(binary_spans(rom, index) + [
        {"kind": "font-glyph", "start": row["glyphs"],
         "end": row["glyphs"] + row["count"] * glyph_size(row.get("format", "16x16")),
         "method": "discovered-glyph-allocation"} for row in data.get("font", [])])
    outcomes = []
    fill = next((row for row in data.get("keyboard", {}).get("table", []) if row["name"] == "fill"), None)
    for start, candidate in sorted(found.items()):
        if not candidate.verified_refs():
            continue
        containers = binary.containing(start, candidate.length)
        if containers:
            container = containers[0]
            outcomes.append({"name": f"exclude-{start:07X}", "action": "exclude", "offset": start,
                             "classifier": f"{container['kind']}-payload", "length": candidate.length,
                             "anchors": [container.get("header", container["start"])],
                             "container": container})
        elif fill and start == fill["base"] and rom.read(start, 3) == b"\xee\xdd\xff":
            outcomes.append({"name": f"exclude-{start:07X}", "action": "exclude", "offset": start,
                             "classifier": "keyboard-fill-bytes", "length": candidate.length,
                             "anchors": sorted(fill["refs"])})
    return outcomes


def run(rom, data, trainerbattle_offsets=None):
    """Emit classifier records; compatibility lists are never inputs."""
    from .foundations import Result, evidence, fact

    tables = data.get("table", [])
    found, rejected, index = discover(rom, exclude=[
        (row["base"], row["base"] + row["stride"] * row["count"]) for row in tables],
        trainerbattle_offsets=trainerbattle_offsets)
    found = {row.offset: row for row in found}
    used_sections = None
    if data.get("maps", {}).get("groups"):
        from .map_facts import Roots
        walker = Roots(rom, data)
        walker.walk_all()
        walker.walk_structural()
        used_sections = {rom.data[header + 20] for header in walker.ctx.headers.values()}
    rows = sorted(include_targets(rom, data, found, index, used_sections) + exclude_targets(rom, data, found, index),
                  key=lambda row: (row["action"], row["offset"]))
    values, proofs = [], []
    for row in rows:
        value = {key: row[key] for key in ("name", "action", "offset", "classifier")}
        values.append(value)
        proof = evidence(
            "A discovered text field bypasses byte heuristics." if row["action"] == "include" else
            "A bounded binary container rejects a promoted text candidate; unverified scan candidates remain unchanged.",
            row["classifier"], row["anchors"], bytes=row["length"])
        proofs.append(fact(f"strings.classified[name={row['name']}]", "reference", value, proof, "shape"))
    return Result({"strings": {"classified": values}} if values else {}, proofs,
                  {"outcomes": rows, "scan_candidates": len(found), "rejected_mid_string_targets": rejected})
