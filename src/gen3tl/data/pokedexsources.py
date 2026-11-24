"""Scripted gift, egg, starter and trade sources of species for the Pokedex."""

from __future__ import annotations

import bisect
import hashlib
import re
from collections import defaultdict

from ..core.notes import has_prose
from ..discover.maps import Walker
from ..discover.reach import Code, SCRIPT_OPERAND
from ..core.rom import Rom, RomEvidence, value_at
from ..discover.scan import pointer_index


def _variable(value: int) -> bool:
    return 0x4000 <= value < 0x4200 or 0x5000 <= value < 0x5200 or 0x8000 <= value < 0x8020


def _inside(at: int, regions: list[dict]) -> bool:
    return any(int(row["start"]) <= at < int(row["end"]) for row in regions)


def check(block: dict | None) -> list[str]:
    """Configuration required to prove scripted species sources."""
    if not block:
        return ["block"]
    problems = []
    if not has_prose(block.get("evidence", "")):
        problems.append("evidence")
    for key in ("regions", "linked_regions"):
        rows = block.get(key)
        if not isinstance(rows, list):
            problems.append(key)
        else:
            for row in rows:
                if not isinstance(row, dict) or "start" not in row or "end" not in row \
                        or int(row["start"]) >= int(row["end"]):
                    problems.append(f"{key} start/end")
                    break
    commands = block.get("commands") or {}
    for key in ("table", "gift", "egg", "evidence"):
        if key not in commands or key == "evidence" and not has_prose(commands[key]):
            problems.append(f"commands {key}")
    trades = block.get("trades") or {}
    for key in ("base", "stride", "count", "species", "wanted", "command", "evidence"):
        if key not in trades or key == "evidence" and not has_prose(trades[key]):
            problems.append(f"trades {key}")
    if "original_trainer" in trades and (type(trades["original_trainer"]) is not int
            or type(trades.get("stride")) is not int
            or not 0 <= trades["original_trainer"] <= trades["stride"] - 4):
        problems.append("trades original_trainer bounds")
    if "bindings" in trades:
        context = trades.get("context", {})
        if not isinstance(context, dict) or any(type(context.get(key)) is not int
                for key in ("groups", "engine_groups", "engine_count", "bank_xor")):
            problems.append("trades context")
        if not isinstance(trades["bindings"], list) or not trades["bindings"]:
            problems.append("trades bindings")
        else:
            for index, binding in enumerate(trades["bindings"]):
                if (not isinstance(binding, dict)
                        or type(binding.get("index")) is not int
                        or not 0 <= binding["index"] < trades.get("count", 0)
                        or not re.fullmatch(r"\d+\.\d+", str(binding.get("map", "")))
                        or any(type(binding.get(key)) is not int or binding[key] < 0
                               for key in ("map_header", "script"))
                        or not RomEvidence.valid(binding.get("guard"))
                        or not isinstance(binding.get("roots"), list) or not binding["roots"]
                        or any(type(root) is not int for root in binding["roots"])
                        or not has_prose(binding.get("evidence"))):
                    problems.append(f"trades bindings[{index}]")
                if ("speaker" in binding and (not re.fullmatch(r"s[0-9a-fA-F]{7}", str(binding.get("speaker", "")))
                        or type(binding.get("speaker_offset")) is not int or binding["speaker_offset"] < 2
                        or not has_prose(binding.get("speaker_evidence")))) \
                        or "speaker_evidence" in binding and "speaker" not in binding:
                    problems.append(f"trades bindings[{index}] speaker")
                selector = binding.get("selector", {}) if isinstance(binding, dict) else {}
                if (not isinstance(selector, dict)
                        or type(selector.get("offset")) is not int or selector["offset"] < 0
                        or selector.get("width") not in (1, 2, 4)
                        or "second" in selector and (any(type(selector.get(key)) is not int
                                                        for key in ("second", "first", "stride", "base"))
                                                      or selector["stride"] <= 0)):
                    problems.append(f"trades bindings[{index}] selector")
        for index, proof in enumerate(trades.get("proofs", [])):
            if (not isinstance(proof, dict)
                    or type(proof.get("offset")) is not int or type(proof.get("size")) is not int
                    or proof["offset"] < 0 or proof["size"] <= 0
                    or not re.fullmatch(r"[0-9a-f]{64}", str(proof.get("sha256", "")))):
                problems.append(f"trades proofs[{index}]")
    seen = set()
    for index, choice in enumerate(block.get("choices", [])):
        offset = choice.get("offset")
        if offset in seen:
            problems.append(f"choices[{index}] duplicate offset")
        seen.add(offset)
        if offset is None or not choice.get("species"):
            problems.append(f"choices[{index}] offset/species")
        if choice.get("kind", "gift") not in ("gift", "egg", "starter"):
            problems.append(f"choices[{index}] kind")
        if not has_prose(choice.get("evidence", "")):
            problems.append(f"choices[{index}] evidence")
    return problems


class _SourceWalker(Walker):
    """Map walker retaining each command's source context."""

    def __init__(self, rom: Rom, profile: dict):
        super().__init__(rom, profile)
        self.contexts: dict[int, list[tuple[str, int, int]]] = defaultdict(list)

    def walk_script(self, start, mid, name, event, local_id, sprite):
        self.ctx.scripts += 1
        seen = set()
        self._walk(start, None, mid, name, event, local_id, sprite, start, seen, self.new_state(), 0)
        section = self.d[self.ctx.headers[mid] + 20]
        for pc in seen:
            self.contexts[pc].append((mid, section, start))


def _linked(data: bytes, code: Code, entry: int, references: list[int]) -> bool:
    for at in references:
        for op, (offset, _) in SCRIPT_OPERAND.items():
            if at >= offset and data[at - offset] == op and not code.starts(at - offset).ambiguous:
                return True
    return False


def _candidate(data: bytes, at: int, commands: dict, species_count: int, item_count: int) -> tuple | None:
    op = data[at]
    species = value_at(data, at + 1, 2)
    if species is None or not (0 < species < species_count or _variable(species)):
        return None
    if op == commands["egg"]:
        return ("egg", species, None, None, 3)
    if op != commands["gift"] or at + 15 > len(data):
        return None
    level, item = data[at + 3], value_at(data, at + 4, 2)
    if not 0 < level <= 100 or item is None or not (0 <= item < item_count or _variable(item)):
        return None
    if any(value_at(data, at + offset, 4) not in (0, 1) for offset in (6, 10)):
        return None
    if data[at + 14] not in (0, 1, 15):
        return None
    return ("gift", species, level, item, 15)


def _record(kind: str, species: int | None, level: int | None, item: int | None,
            context: tuple[str | None, int | None, int], offset: int,
            wanted: int | None = None, species_variable: int | None = None,
            item_variable: int | None = None, map_section: int | None = None) -> dict:
    mid, section, script = context
    record = {
        "kind": kind,
        "species": species,
        "wanted": wanted,
        "level": level,
        "item": item,
        "map": mid,
        "map_section": map_section if map_section is not None else section,
        "script": f"0x{script:07X}",
        "offset": f"0x{offset:07X}",
    }
    if species_variable is not None:
        record["species_variable"] = species_variable
    if item_variable is not None:
        record["item_variable"] = item_variable
    return record


def _bound_trades(trades: dict, data: bytes, species_count: int) -> list[dict]:
    """Read only trade rows with verified runtime bindings."""
    from .pokedexencounters import _runtime_word

    for proof in trades.get("proofs", []):
        at, size = proof["offset"], proof["size"]
        if at + size > len(data) or hashlib.sha256(data[at:at + size]).hexdigest() != proof["sha256"]:
            raise ValueError(f"trade proof 0x{at:X}: ROM bytes differ")
    records = []
    for binding in trades["bindings"]:
        index, header = binding["index"], binding["map_header"]
        if _runtime_word(data, trades["context"], binding["map"]) != 0x08000000 + header:
            raise ValueError(f"trade {index}: runtime map differs")
        events = (value_at(data, header + 4, 4) or 0) - 0x08000000
        count = value_at(data, events, 1)
        objects = (value_at(data, events + 4, 4) or 0) - 0x08000000
        if count is None or objects < 0 or objects + count * 24 > len(data):
            raise ValueError(f"trade {index}: invalid map objects")
        roots = {value_at(data, objects + i * 24 + 16, 4) - 0x08000000
                 for i in range(count) if data[objects + i * 24 + 2] != 255}
        if not set(binding["roots"]) <= roots:
            raise ValueError(f"trade {index}: NPC script roots differ")
        if not RomEvidence.parse(binding["guard"]).matches(data):
            raise ValueError(f"trade {index}: selector bytes differ")
        selector = binding["selector"]
        selected = value_at(data, selector["offset"], selector["width"])
        if "second" in selector:
            choice = value_at(data, selector["second"], selector["width"])
            if selected is None or selected < selector["first"] or choice is None or not 0 <= choice < selector["stride"]:
                raise ValueError(f"trade {index}: invalid selector inputs")
            selected = selector["base"] + (selected - selector["first"]) * selector["stride"] + choice
        if selected != index:
            raise ValueError(f"trade {index}: selector index differs")
        if "dispatch" in binding and value_at(data, binding["dispatch"], 4) != 0x08000000 + binding["script"]:
            raise ValueError(f"trade {index}: dispatch script differs")
        at = trades["base"] + index * trades["stride"]
        species = value_at(data, at + trades["species"], 2)
        wanted = value_at(data, at + trades["wanted"], 2)
        if species is None or wanted is None or not 0 < species < species_count or not 0 < wanted < species_count:
            raise ValueError(f"trade {index}: invalid species")
        record = _record("trade", species, None, None,
                         (binding["map"], data[header + 20], binding["script"]), at, wanted=wanted)
        record["trade_index"] = index
        record["selector_offset"] = f"0x{binding['guard']['offset']:07X}"
        if binding.get("availability"):
            record["availability"] = binding["availability"]
        if "speaker" in binding:
            offset = binding["speaker_offset"]
            pointer = value_at(data, offset, 4)
            if data[offset - 2:offset] != b"\x0f\x00" \
                    or pointer != 0x08000000 + int(binding["speaker"][1:], 16):
                raise ValueError(f"trade {index}: speaker name box differs")
            record["speaker"] = binding["speaker"]
        if "original_trainer" in trades:
            pointer = value_at(data, at + trades["original_trainer"], 4)
            if pointer is None or not 0x08000000 <= pointer < 0x08000000 + len(data):
                raise ValueError(f"trade {index}: invalid original trainer pointer")
            record.update(original_trainer=f"s{pointer - 0x08000000:07X}", original_trainer_offset=f"0x{at:07X}")
        records.append(record)
    return records


def _trade_records(block: dict, data: bytes, walker: _SourceWalker,
                   species_count: int) -> list[dict]:
    trades = block["trades"]
    if "bindings" in trades:
        return _bound_trades(trades, data, species_count)
    contexts = [pc for pc in walker.contexts
                if walker.d[pc] == 0x25 and value_at(walker.d, pc + 1, 2) == trades["command"]]
    if not contexts:
        contexts = [0]
    records = []
    for index in range(int(trades["count"])):
        at = int(trades["base"]) + index * int(trades["stride"])
        species = value_at(data, at + int(trades["species"]), 2)
        wanted = value_at(data, at + int(trades["wanted"]), 2)
        if species is None or wanted is None:
            raise ValueError(f"trade {index}: record exceeds ROM")
        if not 0 < species < species_count or not 0 < wanted < species_count:
            raise ValueError(f"trade {index}: invalid species")
        for script in contexts:
            record = _record("trade", species, None, None, (None, None, script), at, wanted=wanted)
            records.append(record)
    return records


def build(block: dict, data: bytes, profile: dict, species_count: int, item_count: int) -> list[dict]:
    """Extract every proven script command and in-game trade record."""
    problems = check(block)
    if problems:
        raise ValueError("invalid source configuration: " + ", ".join(problems))
    rom = Rom(data)
    walker = _SourceWalker(rom, profile)
    walker.walk_all()
    walker.walk_structural()
    code = Code(data, walker.cmd_len, walker.tb)
    references = pointer_index(rom)
    spans = walker.command_spans()
    commands = block["commands"]
    choices = {int(row["offset"]): row for row in block.get("choices", [])}
    records = []

    def emit(candidate, at: int, entry: int, contexts: list[tuple[str, int, int]]):
        kind, species, level, item, _ = candidate
        choice = choices.get(at)
        for mid, section, script in contexts or [(None, None, entry)]:
            if choice:
                for selected in choice["species"]:
                    if not 0 < int(selected) < species_count:
                        raise ValueError(f"source {at:#x}: invalid choice species {selected}")
                    records.append(_record(
                        choice.get("kind", kind), int(selected), level, choice.get("item", item),
                        (mid, section, script), at, map_section=choice.get("map_section")))
            else:
                records.append(_record(
                    kind, species if not _variable(species) else None, level,
                    item if item is None or not _variable(item) else None,
                    (mid, section, script), at,
                    species_variable=species if _variable(species) else None,
                    item_variable=item if item is not None and _variable(item) else None))

    opcodes = b"[" + re.escape(bytes((commands["gift"], commands["egg"]))) + b"]"
    for match in re.finditer(opcodes, data):
        at = match.start()
        candidate = _candidate(data, at, commands, species_count, item_count)
        if candidate is None:
            continue
        if at in walker.decoded:
            emit(candidate, at, at, walker.contexts[at])
            continue
        length = candidate[-1]
        pos = bisect.bisect_left(spans, (at, at + length))
        if pos and spans[pos - 1][0] < at + length and at < spans[pos - 1][1]:
            continue
        if pos < len(spans) and spans[pos][0] < at + length and at < spans[pos][1]:
            continue
        entry = code.starts(at)
        if entry.ambiguous:
            continue
        if _inside(at, block["regions"]):
            emit(candidate, at, entry.entry, [])
        elif _inside(at, block["linked_regions"]) and _linked(data, code, entry.entry, references.get(entry.entry, [])):
            emit(candidate, at, entry.entry, [])
    records.extend(_trade_records(block, data, walker, species_count))
    unique = {}
    for record in records:
        key = tuple((name, record[name]) for name in sorted(record))
        unique[key] = record
    return sorted(unique.values(), key=lambda row: (
        int(row["offset"], 16), row["kind"],
        -1 if row["species"] is None else row["species"],
        -1 if row["map_section"] is None else row["map_section"],
        row["map"] or "", int(row["script"], 16)))
