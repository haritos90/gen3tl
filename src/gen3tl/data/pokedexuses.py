"""Item uses by characters and events: exchanges, upgrades and requirements."""

from __future__ import annotations

from collections import defaultdict
from ..core.notes import has_prose
from ..core.rom import Rom, RomEvidence
from ..discover.maps import Walker

KINDS = ("exchange", "upgrade", "requirement")
GIVES = ("item", "table", "held_effect", "tutor", "money", "species", "effect", "upgrade")
TAKES = {0x45: True, 0x47: False}   # removeitem takes, checkitem only requires
VARIABLE = 0x4000
SET_OPS = (0x16, 0x1A)   # setvar, setorcopyvar: u16 variable, then value
ADDITEM, GIVEMON, LOADPOINTER, CALLASM = 0x44, 0x79, 0x0F, 0x23
ITEM_VAR, QUANTITY_VAR, TUTOR_VAR = 0x8000, 0x8001, 0x8005


def _u16(data: bytes, at: int) -> int:
    return int.from_bytes(data[at:at + 2], "little")


def _offset(value) -> bool:
    return type(value) is int and 0 <= value < 0x2000000


def _quantity(value) -> bool:
    return (type(value) is int and value > 0 or isinstance(value, list) and len(value) == 2
            and all(type(n) is int and n > 0 for n in value) and value[0] < value[1])


def _rom_check(row: dict, *, required=False) -> bool:
    proofs = row.get("rom", [])
    return (isinstance(proofs, list) and (bool(proofs) or not required)
            and all(RomEvidence.valid(proof) for proof in proofs))


def _conditions_check(row: dict) -> bool:
    values = row.get("conditions", [])
    return isinstance(values, list) and all(isinstance(key, str) and key.strip() for key in values)


def _native_check(name: str, row, count: int) -> bool:
    if (not isinstance(row, dict) or not has_prose(row.get("evidence", ""))
            or not _rom_check(row, required=True) or not _conditions_check(row)):
        return False
    common = {"rom", "evidence", "conditions"}
    if name == "recipes":
        return (set(row) <= common | {"at", "outputs", "base", "count", "stride", "items", "index_field"}
                and _offset(row.get("at")) and _offset(row.get("base"))
                and row.get("index_field") == "hold_effect_param"
                and isinstance(row.get("outputs"), dict) and set(row["outputs"]) == {"base", "count"}
                and _offset(row["outputs"]["base"]) and type(row["outputs"]["count"]) is int
                and 0 < row["outputs"]["count"] <= 256
                and type(row.get("count")) is int and 0 < row["count"] <= 256
                and type(row.get("stride")) is int and row["stride"] > 0
                and isinstance(row.get("items"), list) and 0 < len(row["items"]) <= row["stride"]
                and all(type(item) is int and 0 < item < count for item in row["items"])
                and len(set(row["items"])) == len(row["items"]))
    if name == "tutors":
        return (set(row) <= common | {"tables", "count", "stride", "tutor", "move", "quantity", "entries"}
                and isinstance(row.get("tables"), list) and bool(row["tables"])
                and all(_offset(base) for base in row["tables"])
                and type(row.get("count")) is int and 0 < row["count"] <= 256
                and type(row.get("stride")) is int and row["stride"] > 0
                and all(type(row.get(key)) is int and 0 <= row[key] <= row["stride"] - width
                        for key, width in (("tutor", 1), ("move", 2), ("quantity", 1)))
                and isinstance(row.get("entries"), list) and bool(row["entries"])
                and all(isinstance(entry, dict) and set(entry) == {"at", "index", "item"}
                        and _offset(entry["at"]) and type(entry["index"]) is int
                        and 0 <= entry["index"] < row["count"] and type(entry["item"]) is int
                        and 0 < entry["item"] < count for entry in row["entries"]))
    price = row.get("price")
    valid_price = (type(price) is int and price > 0 or isinstance(price, dict)
                   and set(price) == {"field", "multiplier", "divisor"} and price["field"] == "price"
                   and all(type(price[key]) is int and price[key] > 0 for key in ("multiplier", "divisor")))
    domain = row.get("items")
    valid_domain = (isinstance(domain, list) and bool(domain)
                    and all(type(item) is int and 0 < item < count for item in domain)
                    and "pocket" not in row and "range" not in row)
    if "pocket" in row:
        bounds = row.get("range")
        valid_domain = (domain is None and type(row["pocket"]) is int and 0 < row["pocket"] <= 255
                        and isinstance(bounds, list) and len(bounds) == 2
                        and all(type(n) is int for n in bounds) and 0 < bounds[0] <= bounds[1] < count)
    return (set(row) <= common | {"at", "items", "pocket", "range", "price"}
            and _offset(row.get("at")) and valid_domain and valid_price)


def _give_check(give, count: int) -> bool:
    if not isinstance(give, dict) or len(set(give) & set(GIVES)) != 1:
        return False
    kind = next(iter(set(give) & set(GIVES)))
    value = give[kind]
    if set(give) - {kind, "at", "quantity", "count"}:
        return False
    if "at" in give and not _offset(give["at"]):
        return False
    if "quantity" in give and not _quantity(give["quantity"]):
        return False
    if kind == "table":
        return _offset(value) and type(give.get("count")) is int and 0 < give["count"] <= 64 and "at" not in give
    if "count" in give:
        return False
    if kind == "effect":
        return isinstance(value, str) and bool(value.strip()) and "at" not in give
    if kind in ("item", "upgrade"):
        return type(value) is int and 0 < value < count
    return type(value) is int and value >= 0


def check(block: dict | None, count: int) -> list[str]:
    """Problems of an item-use block; empty when its rows can be extracted."""
    if not isinstance(block, dict):
        return ["block"]
    problems = []
    if not has_prose(block.get("evidence", "")):
        problems.append("evidence")
    if not _offset(block.get("name_box")):
        problems.append("name_box")
    record = block.get("buyer_record")
    if (not isinstance(record, dict) or set(record) != {"stride", "item", "price"}
            or any(type(record[key]) is not int or record[key] < 0 for key in record)
            or record["item"] + 2 > record["stride"] or record["price"] + 4 > record["stride"]):
        problems.append("buyer_record")
    for index, buyer in enumerate(block.get("buyers", [])):
        if (not isinstance(buyer, dict) or not {"base", "count", "at"} <= set(buyer)
                or set(buyer) - {"base", "count", "at", "evidence", "rom"}
                or not _offset(buyer["base"]) or not _offset(buyer["at"])
                or type(buyer["count"]) is not int or not 0 < buyer["count"] <= 256
                or not _rom_check(buyer)
                or buyer.get("rom") and not has_prose(buyer.get("evidence", ""))):
            problems.append(f"buyers[{index}]")
    for name in ("recipes", "tutors", "prices"):
        entries = block.get(name, [])
        if not isinstance(entries, list):
            problems.append(name)
            continue
        for index, entry in enumerate(entries):
            if not _native_check(name, entry, count):
                problems.append(f"{name}[{index}]")
    rows = block.get("rows")
    if not isinstance(rows, list) or not rows:
        return problems + ["rows"]
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) - {"kind", "takes", "items", "held_effect", "quantity",
                                                     "gives", "choice", "evidence", "rom", "conditions"}:
            problems.append(f"rows[{index}]")
            continue
        takes, gives = row.get("takes"), row.get("gives")
        valid = (row.get("kind") in KINDS
                 and isinstance(takes, list) and bool(takes) and all(_offset(at) for at in takes)
                 and len(set(takes)) == len(takes)
                 and isinstance(gives, list) and bool(gives) and all(_give_check(give, count) for give in gives)
                 and (row.get("kind") == "upgrade") == any("upgrade" in give for give in gives)
                 and type(row.get("choice", False)) is bool
                 and _rom_check(row) and _conditions_check(row)
                 and ("quantity" not in row or _quantity(row["quantity"])))
        items = row.get("items")
        if items is not None and (not isinstance(items, list) or not items or len(set(items)) != len(items)
                                  or any(type(item) is not int or not 0 < item < count for item in items)):
            valid = False
        if "held_effect" in row and (items is not None or type(row["held_effect"]) is not int
                                     or not 0 < row["held_effect"] <= 255):
            valid = False
        bare = [give for give in gives or [] if isinstance(give, dict) and "at" not in give]
        needs = bare or items is not None or "held_effect" in row or "quantity" in row or row.get("rom")
        if needs and not has_prose(row.get("evidence", "")):
            valid = False
        if not valid:
            problems.append(f"rows[{index}]")
    return problems


def _take(data: bytes, at: int, row: dict, index: int) -> tuple[bool, int | None, object]:
    """Whether the command takes the item, its literal item (None: the row's items) and quantity."""
    op = data[at] if 0 <= at < len(data) else None
    if op not in TAKES:
        raise ValueError(f"item use {index}: 0x{at:07X} is not checkitem or removeitem")
    item, quantity = _u16(data, at + 1), _u16(data, at + 3)
    if (item >= VARIABLE) != ("items" in row or "held_effect" in row):
        raise ValueError(f"item use {index}: 0x{at:07X} item operand and declared items disagree")
    if quantity >= VARIABLE:
        quantity = row.get("quantity")
    elif "quantity" in row:
        raise ValueError(f"item use {index}: 0x{at:07X} has a literal quantity")
    return TAKES[op], None if item >= VARIABLE else item, quantity


def _proof(data: bytes, give: dict, kind: str, index: int) -> int | None:
    """The literal quantity the command at `at` gives; rejects another value."""
    if "at" not in give:
        return None
    at, value, op = give["at"], give[kind], data[give["at"]]
    found, quantity = None, None
    if op in SET_OPS:
        var, found = _u16(data, at + 1), _u16(data, at + 3)
        if kind == "tutor" and var != TUTOR_VAR:
            found = None
        nxt = at + 5
        if kind == "item" and var == ITEM_VAR and data[nxt] in SET_OPS and _u16(data, nxt + 1) == QUANTITY_VAR:
            quantity = _u16(data, nxt + 3)
    elif op == ADDITEM and kind in ("item", "upgrade"):
        found, quantity = _u16(data, at + 1), _u16(data, at + 3)
    elif op in TAKES and kind == "upgrade":
        found = _u16(data, at + 1)
    elif op == GIVEMON and kind == "species":
        found = _u16(data, at + 1)
    if found != value:
        raise ValueError(f"item use {index}: 0x{at:07X} does not give {kind} {value}")
    if quantity is not None and quantity >= VARIABLE:
        quantity = None
    if "quantity" in give and quantity is not None and quantity != give["quantity"]:
        raise ValueError(f"item use {index}: 0x{at:07X} gives another quantity")
    return quantity


def _gives(data: bytes, row: dict, index: int, taken: int, items: dict, moves: list[int] | None) -> list[dict]:
    out = []
    for give in row["gives"]:
        kind = next(key for key in GIVES if key in give)
        found = _proof(data, give, kind, index)
        if kind == "table":
            for n in range(give["count"]):
                item = _u16(data, give["table"] + 2 * n)
                if item not in items:
                    raise ValueError(f"item use {index}: table item {item} is not an item")
                out.append({"item": item})
            continue
        if kind == "held_effect":
            source = items.get(taken, {})
            matches = [row_id for row_id, record in items.items() if record.get("held_effect") == give[kind]
                       and record.get("hold_effect_param") == source.get("hold_effect_param")]
            if len(matches) != 1:
                raise ValueError(f"item use {index}: no single item with held effect {give[kind]} for {taken}")
            out.append({"item": matches[0]})
            continue
        if kind == "tutor":
            if moves is None or not 0 <= give[kind] < len(moves):
                raise ValueError(f"item use {index}: tutor {give[kind]} has no move")
            out.append({"move": moves[give[kind]]})
            continue
        entry = {kind: give[kind]}
        if give.get("quantity", found) is not None:
            entry["quantity"] = give.get("quantity", found)
        out.append(entry)
    return out


class _ReachWalker(Walker):
    """Map walker keeping the maps and name boxes of the walks reaching item-use commands."""

    def __init__(self, rom: Rom, profile: dict, targets: set[int], name_box: int):
        super().__init__(rom, profile)
        self.targets, self.name_box = targets, name_box
        self.maps: dict[int, set[str]] = defaultdict(set)
        self.scripts: dict[tuple[int, str], set[int]] = defaultdict(set)
        self.speakers: dict[int, set[int]] = defaultdict(set)

    def walk_script(self, start, mid, name, event, local_id, sprite):
        seen = super().walk_script(start, mid, name, event, local_id, sprite)
        hits = self.targets & seen
        if not hits:
            return
        d = self.d
        names = {self.ptr(pc + 2) for pc in seen
                 if d[pc] == LOADPOINTER and d[pc + 6] == CALLASM and self.ptr(pc + 7) == self.name_box}
        for pc in hits:
            self.maps[pc].add(mid)
            self.scripts[pc, mid].add(start)
            self.speakers[pc] |= names - {None}


def _places(walker: _ReachWalker, data: bytes, at: int) -> list[dict]:
    rows = {(mid, data[walker.ctx.headers[mid] + 20]) for mid in walker.maps.get(at, ())}
    return [{"map": mid, "map_section": section,
             "scripts": [f"0x{script:07X}" for script in sorted(walker.scripts[at, mid])]}
            for mid, section in sorted(rows, key=lambda row: (row[1], row[0]))]


def _native_rows(block: dict, data: bytes, items: dict, moves: list[int] | None):
    """Expand evidenced native tables into ordinary exchanges."""
    for row in block.get("recipes", []):
        for n in range(row["outputs"]["count"]):
            item = _u16(data, row["outputs"]["base"] + n * 2)
            index = items.get(item, {}).get(row["index_field"])
            if type(index) is not int or not 0 <= index < row["count"]:
                raise ValueError(f"item recipe: invalid output or cost index {item}")
            at = row["base"] + index * row["stride"]
            quantities = data[at:at + len(row["items"])]
            if len(quantities) != len(row["items"]) or not any(quantities):
                raise ValueError(f"item recipe: missing costs for {item}")
            takes = [{"item": item, "quantity": quantity} for item, quantity in zip(row["items"], quantities)
                     if quantity]
            yield row, row["at"], takes, [{"item": item, "quantity": 1}]
    for row in block.get("tutors", []):
        for entry in row["entries"]:
            for base in row["tables"]:
                at = base + entry["index"] * row["stride"]
                record = data[at:at + row["stride"]]
                if len(record) != row["stride"]:
                    raise ValueError("item tutor: truncated table")
                tutor = record[row["tutor"]]
                move = _u16(record, row["move"])
                quantity = record[row["quantity"]]
                if moves is None or tutor >= len(moves) or moves[tutor] != move or not move or not quantity:
                    raise ValueError("item tutor: move or cost disagrees with tutor table")
                yield row, entry["at"], [{"item": entry["item"], "quantity": quantity}], [{"move": move}]
    for row in block.get("prices", []):
        domain = row.get("items")
        if domain is None:
            domain = [item for item, record in items.items() if record.get("pocket") == row["pocket"]
                      and row["range"][0] <= item <= row["range"][1]]
        if not domain:
            raise ValueError("item price: empty domain")
        for item in domain:
            price = row["price"]
            if isinstance(price, dict):
                value = items.get(item, {}).get(price["field"])
                if type(value) is not int or value < 0:
                    raise ValueError(f"item price: missing field for {item}")
                price = value * price["multiplier"] // price["divisor"]
            yield row, row["at"], [{"item": item, "quantity": None}], [{"money": price}]


def build(block: dict, data: bytes, profile: dict, items: list[dict], tutor: dict | None = None) -> dict[int, list[dict]]:
    """item id -> its use rows: what it is given for, where and to whom."""
    count = max((record["id"] for record in items), default=0) + 1
    problems = check(block, count)
    if problems:
        raise ValueError("invalid item-use configuration: " + ", ".join(problems))
    for name in ("rows", "buyers", "recipes", "tutors", "prices"):
        for index, row in enumerate(block.get(name, [])):
            for proof in row.get("rom", []):
                at = proof["offset"]
                if not RomEvidence.parse(proof).matches(data):
                    raise ValueError(f"item use {name}[{index}]: ROM evidence differs at 0x{at:07X}")
    by_id = {record["id"]: record for record in items}
    moves = None
    if tutor:
        moves = [_u16(data, int(tutor["move_base"]) + 2 * n) for n in range(int(tutor["move_count"]))]
    targets = {at for row in block["rows"] for at in row["takes"]} | {buyer["at"] for buyer in block.get("buyers", [])}
    targets |= {row["at"] for name in ("recipes", "prices") for row in block.get(name, [])}
    targets |= {entry["at"] for row in block.get("tutors", []) for entry in row["entries"]}
    walker = _ReachWalker(Rom(data), profile, targets, block["name_box"])
    walker.walk_all()
    walker.walk_structural()
    missing = sorted(at for at in targets if not walker.maps.get(at))
    if missing:
        raise ValueError("item use commands no map reaches: " + ", ".join(f"0x{at:07X}" for at in missing))

    def where(offsets) -> dict:
        places = {}
        for at in offsets:
            for place in _places(walker, data, at):
                key = place["map"], place["map_section"]
                if key in places:
                    place["scripts"] = sorted(set(place["scripts"]) | set(places[key]["scripts"]))
                places[key] = place
        speakers = set().union(*(walker.speakers.get(at, set()) for at in offsets))
        out = {"places": sorted(places.values(), key=lambda place: (place["map_section"], place["map"]))}
        if len(speakers) == 1:
            out["speaker"] = f"s{next(iter(speakers)):07X}"
        return out

    out: dict[int, list[dict]] = {record["id"]: [] for record in items}
    for index, row in enumerate(block["rows"]):
        takes = [_take(data, at, row, index) for at in row["takes"]]
        if row["kind"] != "requirement" and not any(taken for taken, _, _ in takes):
            raise ValueError(f"item use {index}: an {row['kind']} removes no item")
        if row["kind"] == "requirement" and any(taken for taken, _, _ in takes):
            raise ValueError(f"item use {index}: a requirement removes its item")
        place = where(row["takes"])
        domain_items = row.get("items")
        if "held_effect" in row:
            domain_items = [item for item, record in by_id.items() if record.get("held_effect") == row["held_effect"]]
            if not domain_items:
                raise ValueError(f"item use {index}: no item has held effect {row['held_effect']}")
        for domain in domain_items or [None]:
            listed = [{"item": domain if item is None else item, "quantity": quantity}
                      for _, item, quantity in takes]
            for entry in listed:
                if entry["item"] not in by_id:
                    raise ValueError(f"item use {index}: item {entry['item']} is not an item")
            use = {"kind": row["kind"], "takes": listed,
                   "gives": _gives(data, row, index, listed[0]["item"], by_id, moves), **place}
            if row.get("choice") or any("table" in give for give in row["gives"]):
                use["choice"] = True
            if row.get("conditions"):
                use["conditions"] = list(row["conditions"])
            for entry in listed:
                if use not in out[entry["item"]]:
                    out[entry["item"]].append(use)
    record = block["buyer_record"]
    for buyer in block.get("buyers", []):
        if data[buyer["at"]] != CALLASM:
            raise ValueError(f"buyer 0x{buyer['at']:07X}: not a callasm command")
        place = where([buyer["at"]])
        for n in range(buyer["count"]):
            at = buyer["base"] + n * record["stride"]
            item = _u16(data, at + record["item"])
            price = int.from_bytes(data[at + record["price"]:at + record["price"] + 4], "little")
            if item not in by_id or price <= 0:
                raise ValueError(f"buyer 0x{buyer['base']:07X}: record {n} is not an item and a price")
            use = {"kind": "exchange", "takes": [{"item": item, "quantity": None}],
                   "gives": [{"money": price}], **place}
            if use not in out[item]:
                out[item].append(use)
    for row, at, takes, gives in _native_rows(block, data, by_id, moves):
        if data[at] != CALLASM:
            raise ValueError(f"native item use 0x{at:07X}: not a callasm command")
        use = {"kind": "exchange", "takes": takes, "gives": gives, **where([at])}
        if row.get("conditions"):
            use["conditions"] = list(row["conditions"])
        for entry in takes:
            if entry["item"] not in out:
                raise ValueError(f"native item use: invalid cost item {entry['item']}")
            if use not in out[entry["item"]]:
                out[entry["item"]].append(use)
    return out
