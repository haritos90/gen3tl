"""Species learnsets for the Pokedex: level-up, TM/HM, egg and tutor records, kept numeric."""

from __future__ import annotations

from ..core.notes import has_prose
from ..core.rom import rom_offset, value_at

LEVEL_UP, TM_HM, EGG, TUTOR = 0, 1, 2, 3
METHODS = ("level_up", "tmhm", "egg", "tutor")   # names of the method codes


def check(block: dict) -> list[str]:
    """Problems in a learnsets block; empty when every present table is proven."""
    problems = []
    for name in METHODS:
        table = block.get(name)
        if not table:
            continue
        if "base" not in table or not str(table["base"]).strip():
            problems.append(f"{name} base")
        if not has_prose(table.get("evidence")):
            problems.append(f"{name} evidence")
        if name == "level_up" and "count" not in table:
            problems.append("level_up count")
        if name in ("tmhm", "tutor"):
            for key in ("stride", "count", "move_base", "move_count"):
                if key not in table:
                    problems.append(f"{name} {key}")
    forms = block.get("form_reversions")
    if forms:
        if not forms.get("methods"):
            problems.append("form_reversions methods")
        if not has_prose(forms.get("evidence", "")):
            problems.append("form_reversions evidence")
    return problems


def resolve_species(block: dict, slot: int, evolution: list[dict] | None) -> int:
    """The slot whose learnset tables represent this species."""
    forms = block.get("form_reversions") or {}
    methods = set(forms.get("methods", []))
    targets = {row["target"] for row in evolution or []
               if row["method"] in methods and row["parameter"] == 0}
    return next(iter(targets)) if len(targets) == 1 else slot


def _read(data: bytes, at: int, width: int) -> int:
    value = value_at(data, at, width)
    if value is None:
        raise ValueError(f"read outside ROM at 0x{at:x}")
    return value


def _record(species: int, method: int, move: int, index: int | None, level: int | None, offset: int) -> dict:
    return {"species": species, "move": move, "method": method,
            "index": index, "level": level, "offset": f"0x{offset:06x}"}


def _level_up(table: dict, data: bytes, count: int, out: dict[int, list]) -> None:
    for species in range(count):
        pointer = _read(data, int(table["base"]) + species * 4, 4)
        start = rom_offset(pointer)
        if start is None:
            raise ValueError(f"species {species}: invalid level-up pointer 0x{pointer:08x}")
        for index in range(256):
            at = start + index * 3
            move, level = _read(data, at, 2), data[at + 2]
            if move == 0 and level == 0xFF:
                break
            out[species].append(_record(species, LEVEL_UP, move, None, level, at))
        else:
            raise ValueError(f"species {species}: unterminated level-up learnset")


def _bitfields(table: dict, data: bytes, count: int, out: dict[int, list], method: int) -> None:
    for species in range(count):
        start = int(table["base"]) + species * int(table["stride"])
        for index in range(int(table["move_count"])):
            at = start + (index // 32) * 4
            if _read(data, at, 4) & (1 << (index % 32)):
                move = _read(data, int(table["move_base"]) + index * 2, 2)
                out[species].append(_record(species, method, move, index, None, at))


def _egg(table: dict, data: bytes, count: int, out: dict[int, list]) -> None:
    at = int(table["base"])
    species = None
    for _ in range(100000):
        value = _read(data, at, 2)
        if value == 0xFFFF:
            return
        if value >= 20000:
            species = value - 20000
            if species >= count:
                raise ValueError(f"egg-move marker outside species table: {value}")
        else:
            if species is None:
                raise ValueError("egg-move list starts before its species marker")
            out[species].append(_record(species, EGG, value, None, None, at))
        at += 2
    raise ValueError("unterminated egg-move table")


def build(block: dict, data: bytes, count: int) -> dict[int, list]:
    """Extract every species' learnset records in method order."""
    out = {species: [] for species in range(count)}
    if "level_up" in block:
        _level_up(block["level_up"], data, count, out)
    if "tmhm" in block:
        _bitfields(block["tmhm"], data, count, out, TM_HM)
    if "egg" in block:
        _egg(block["egg"], data, count, out)
    if "tutor" in block:
        _bitfields(block["tutor"], data, count, out, TUTOR)
    return out
