"""Item pickups of every map: item balls, hidden items and gifts read from map events and scripts."""

from __future__ import annotations

from collections import Counter

from ..core.notes import has_prose
from ..core.rom import Rom, value_at
from ..discover.maps import END_OPS, VAR_FIRST, VAR_SAFE_OPS, Walker

KINDS = ("ground", "hidden", "gift")
CALLSTD, GOTO, GOTO_IF, CALL, CALL_IF = 0x09, 0x05, 0x06, 0x04, 0x07
SETVAR, COPYVAR, SETORCOPYVAR = 0x16, 0x19, 0x1A
COMPARES = (0x21, 0x22)   # compare_var_value, compare_var_var: variable at +1
TRAINERBATTLE = 0x5C
CONNECTED = 255   # an object borrowed from a connected map


def _byte(value) -> bool:
    return type(value) is int and 0 <= value <= 0xFF


def _word(value) -> bool:
    return type(value) is int and 0 <= value <= 0xFFFF


def check(block: dict | None) -> list[str]:
    """Problems of a pickup block; empty when every map's pickups can be read."""
    if not isinstance(block, dict):
        return ["block"]
    problems = []
    if not has_prose(block.get("evidence", "")):
        problems.append("evidence")
    for key in ("item_var", "quantity_var", "result_var"):
        if not _word(block.get(key)) or block[key] < VAR_FIRST:
            problems.append(key)
    if not _byte(block.get("ground_std")):
        problems.append("ground_std")
    gift = block.get("gift_std")
    if not isinstance(gift, list) or not gift or not all(_byte(n) for n in gift):
        problems.append("gift_std")
    if not _byte(block.get("additem")):
        problems.append("additem")
    hidden = block.get("hidden")
    if (not isinstance(hidden, dict) or set(hidden) != {"kind", "item", "quantity", "mask"}
            or not all(_byte(value) for value in hidden.values())
            or hidden["item"] + 2 > 12 or hidden["quantity"] >= 12):
        problems.append("hidden")
    for key in ("routines", "purchases"):
        values = block.get(key)
        if not isinstance(values, list) or not values or not all(_byte(n) for n in values):
            problems.append(key)
    skip = block.get("skip_scripts", [])
    if not isinstance(skip, list) or not all(type(n) is int and 0 < n < 0x2000000 for n in skip):
        problems.append("skip_scripts")
    for index, point in enumerate(block.get("points", [])):
        valid = (isinstance(point, dict) and set(point) <= {"offset", "description", "description_evidence",
                                                           "related_sections"}
                 and type(point.get("offset")) is int and point["offset"] > 0
                 and isinstance(point.get("description"), str) and bool(point["description"].strip())
                 and has_prose(point.get("description_evidence"))
                 and all(_byte(n) for n in point.get("related_sections", [])))
        if not valid:
            problems.append(f"points[{index}]")
    return problems


class _PickupWalker(Walker):
    """Map walker resolving the item and quantity each gift command gives."""

    def __init__(self, rom: Rom, profile: dict, block: dict):
        super().__init__(rom, profile)
        self.block = block
        self.rows: list[dict] = []
        self.left: Counter = Counter()
        self.event: dict = {}

    def walk_map(self, mid: str, h: int) -> None:
        if h in self.walked:
            return
        self.walked.add(h)
        self.ctx.headers[mid] = h
        self.ctx.maps += 1
        d, block = self.d, self.block
        events = self.ptr(h + 4)
        base = {"map": mid, "header": h, "map_section": d[h + 20]}
        objects, coords, bgs = self.ptr(events + 4), self.ptr(events + 12), self.ptr(events + 16)
        for index in range(d[events] if objects is not None else 0):
            at = objects + 24 * index
            script = self.ptr(at + 16)
            if script is None:
                continue
            place = {**base, "event": at, "x": value_at(d, at + 4, 2, signed=True),
                     "y": value_at(d, at + 6, 2, signed=True)}
            if d[at + 2] == CONNECTED:
                self.left["connected-map object"] += 1
                continue
            if script in block.get("skip_scripts", []):
                self.left["declared script kept out"] += 1
                continue
            ground = self.ground(script)
            if ground is not None:
                self.rows.append({**place, "kind": "ground", "item": ground[0], "quantity": ground[1]})
                continue
            self.walk_event(script, place)
        for index in range(d[events + 2] if coords is not None else 0):
            at = coords + 16 * index
            script = self.ptr(at + 12)
            if script is not None:
                self.walk_event(script, {**base, "event": at, "x": value_at(d, at, 2), "y": value_at(d, at + 2, 2)})
        hidden = block["hidden"]
        for index in range(d[events + 3] if bgs is not None else 0):
            at = bgs + 12 * index
            place = {**base, "event": at, "x": value_at(d, at, 2), "y": value_at(d, at + 2, 2)}
            if d[at + 5] == hidden["kind"]:
                item = value_at(d, at + hidden["item"], 2)
                if item:
                    self.rows.append({**place, "kind": "hidden", "item": item,
                                      "quantity": d[at + hidden["quantity"]] & hidden["mask"]})
            elif d[at + 5] <= 4 and (script := self.ptr(at + 8)) is not None:
                self.walk_event(script, place)
        for event, _n, script, _local, _sprite in self.events(h):
            if event == "mapscript":
                self.walk_event(script, {**base, "event": None})

    def ground(self, script: int) -> tuple[int, int] | None:
        """Item and quantity of an item ball script: two setorcopyvar commands, then callstd."""
        d, block = self.d, self.block
        if (d[script] == SETORCOPYVAR and value_at(d, script + 1, 2) == block["item_var"]
                and d[script + 5] == SETORCOPYVAR and value_at(d, script + 6, 2) == block["quantity_var"]
                and d[script + 10] == CALLSTD and d[script + 11] == block["ground_std"]):
            item, quantity = value_at(d, script + 3, 2), value_at(d, script + 8, 2)
            if item < VAR_FIRST and quantity < VAR_FIRST:
                return item, quantity
        return None

    def walk_event(self, script: int, place: dict) -> None:
        self.ctx.scripts += 1
        self.event = {**place, "script": f"0x{script:07X}"}
        self.trace(script, {}, set(), set(), False, 0)

    def give(self, pc: int, item, quantity, decided: bool) -> None:
        if item is None or quantity is None:
            self.left["item or quantity in a variable"] += 1
        elif decided == "paid":
            self.left["bought with money or coins"] += 1
        elif decided:
            self.left["branch decided by a routine"] += 1
        elif item and quantity:
            self.rows.append({**self.event, "kind": "gift", "item": item, "quantity": quantity, "command": pc})

    def trace(self, pc, known: dict, tainted: set, seen: set, decided: bool, depth: int) -> None:
        """Follow one script path; values survive straight-line code and branches."""
        d, block = self.d, self.block
        steps = 0
        while 0 <= pc < len(d) and pc not in seen and steps < 4000 and depth < 40:
            seen.add(pc)
            self.decoded.add(pc)
            steps += 1
            op = d[pc]
            if op == TRAINERBATTLE:
                offsets = self.tb.get(d[pc + 1])
                if offsets is None:
                    self.ctx.unknown_ops[0x5C00 | d[pc + 1]] += 1
                    return
                length = max(offsets) + 4
                known.clear()
                if d[pc + 1] in (1, 2, 6, 8):
                    target = self.ptr(pc + length)
                    if target is not None:
                        self.trace(target, {}, set(tainted), seen, decided, depth + 1)
                    length += 4
                pc += length
                continue
            length = self.cmd_len.get(op)
            if length is None:
                self.ctx.unknown_ops[op] += 1
                return
            if op == CALLSTD and d[pc + 1] in block["gift_std"]:
                self.give(pc, known.get(block["item_var"]), known.get(block["quantity_var"]), decided)
            elif op == block["additem"]:
                item, quantity = value_at(d, pc + 1, 2), value_at(d, pc + 3, 2)
                self.give(pc, item if item < VAR_FIRST else known.get(item),
                          quantity if quantity < VAR_FIRST else known.get(quantity), decided)
            if op == SETVAR:
                var = value_at(d, pc + 1, 2)
                known[var] = value_at(d, pc + 3, 2)
                tainted.discard(var)
            elif op in (COPYVAR, SETORCOPYVAR):
                var, source = value_at(d, pc + 1, 2), value_at(d, pc + 3, 2)
                value = source if op == SETORCOPYVAR and source < VAR_FIRST else known.get(source)
                if value is None:
                    known.pop(var, None)
                else:
                    known[var] = value
                if source in tainted and source >= VAR_FIRST:
                    tainted.add(var)
                else:
                    tainted.discard(var)
            elif op in block["routines"]:
                known.clear()
                tainted.add(value_at(d, pc + 1, 2) if op == 0x26 else block["result_var"])
            elif op in block["purchases"]:
                decided = decided or "paid"
            elif op in COMPARES:
                decided = (decided or value_at(d, pc + 1, 2) in tainted
                           or op == COMPARES[1] and value_at(d, pc + 3, 2) in tainted)
            elif op not in VAR_SAFE_OPS and op not in (GOTO, GOTO_IF, CALL, CALL_IF):
                known.clear()
                tainted.discard(block["result_var"])
            if op in (CALL, CALL_IF, GOTO_IF):
                target = self.ptr(pc + (1 if op == CALL else 2))
                if target is not None:
                    self.trace(target, dict(known), set(tainted), seen, decided, depth + 1)
                if op != GOTO_IF:
                    known.clear()
            elif op == GOTO:
                target = self.ptr(pc + 1)
                if target is None:
                    return
                pc = target
                continue
            if op in END_OPS:
                return
            pc += length


def build(block: dict, data: bytes, profile: dict, count: int, pictured: set[str] | None = None) -> tuple[dict, dict]:
    """item id -> pickup sources, and the count of rows per kind and of left-out commands per reason."""
    problems = check(block)
    if problems:
        raise ValueError("invalid pickup configuration: " + ", ".join(problems))
    walker = _PickupWalker(Rom(data), profile, block)
    walker.walk_all()
    walker.walk_structural()
    engine = walker.engine_map_ids() or {}
    points = {point["offset"]: point for point in block.get("points", [])}
    used = set()
    out: dict[int, list[dict]] = {}
    seen = set()
    for row in sorted(walker.rows, key=lambda row: (row["map_section"], row["map"], row["event"] or 0,
                                                     KINDS.index(row["kind"]), row.get("command", 0))):
        if not 0 < row["item"] < count:
            walker.left["not an item"] += 1
            continue
        source = {"kind": row["kind"], "map": row["map"], "map_section": row["map_section"],
                  "quantity": row["quantity"]}
        if "script" in row:
            source["script"] = row["script"]
        aliases = sorted(engine.get(row["header"], []), key=lambda name: [int(part) for part in name.split(".")])
        runtime = next((name for name in aliases if name in (pictured or ())), None)
        runtime = runtime or (row["map"] if row["map"] in aliases else aliases[0] if aliases else None)
        if runtime is not None:
            source["runtime_map"] = runtime
        if row["event"] is not None and "x" in row:
            source.update(x=row["x"], y=row["y"])
        point = points.get(row["event"])
        if point is not None:
            used.add(row["event"])
            source.update({key: point[key] for key in ("description", "description_evidence", "related_sections")
                           if key in point})
        key = (row["item"], tuple(sorted((name, str(value)) for name, value in source.items())))
        if key in seen:
            continue
        seen.add(key)
        out.setdefault(row["item"], []).append(source)
    unused = sorted(set(points) - used)
    if unused:
        raise ValueError("pickup points name no pickup: " + ", ".join(f"0x{at:07X}" for at in unused))
    shown = Counter(source["kind"] for rows in out.values() for source in rows)
    return out, {"shown": dict(sorted(shown.items())), "left_out": dict(sorted(walker.left.items()))}
