"""Stable catalog keys and the alias table."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

POINTER_LOW = 0x08000000
ALIAS_DIR = Path("source/keys")
HEADER = ("key", "offset", "id")
EVENTS = ("npc", "trigger", "sign", "mapscript")

# a key, also inside text
KEY_FORMS = (r"map:\d+\.\d+/(?:npc|trigger|sign|mapscript):\d+/\d+",
             r"table:[a-z0-9_-]+/\d+(?:/[a-z0-9_]+)?",
             r"battle:\d+", r"code:[A-Za-z0-9_]+/\d+", r"sym:[A-Za-z0-9_]+", r"hash:[0-9a-f]{8}")
KEY = re.compile("(?:" + "|".join(KEY_FORMS) + ")")
FULL_KEY = re.compile("(?:" + "|".join(KEY_FORMS) + r")\Z")
# inventory id inside text, not part of a name
ID_TOKEN = re.compile(r"(?<![\w-])(s[0-9A-F]{7}|[a-z][a-z0-9_]*:\d+)(?![\w-]|/\d)")
KEY_TOKEN = re.compile(r"(?<![\w-])(" + KEY.pattern + r")(?![\w-]|/\d)")


def copy_source(sid: str) -> str:
    """The inventory id of the text a copy entry repeats."""
    return sid.split("-", 1)[0]


def is_key(name: str) -> bool:
    return bool(FULL_KEY.match(name))


class Aliases:
    """Keys, offsets and inventory ids of one base ROM; empty maps every name to itself."""

    def __init__(self, rows=()):
        self.rows = sorted(rows, key=lambda r: (r[1] is None, r[1] or 0))
        self.by_key = {k: i for k, _, i in self.rows}
        self.by_id = {i: k for k, _, i in self.rows}
        self.offsets = {k: o for k, o, _ in self.rows}
        self.at: dict[int, str] = {}
        for _, o, i in self.rows:   # a copy entry follows its original
            if o is not None:
                self.at.setdefault(o, i)
        if len(self.by_key) != len(self.rows) or len(self.by_id) != len(self.rows):
            raise ValueError("alias table: duplicate key or inventory id")

    def __len__(self) -> int:
        return len(self.rows)

    def __bool__(self) -> bool:
        return bool(self.rows)

    def entry_id(self, name: str) -> str:
        """The inventory id of a key, an id or a hex offset; anything else unchanged."""
        if name in self.by_key:
            return self.by_key[name]
        if re.fullmatch(r"0x[0-9A-Fa-f]+", name):
            return self.at.get(int(name, 16), name)
        return name

    def key(self, name: str) -> str:
        """The key of an inventory id, a key or a hex offset; anything else unchanged."""
        return self.by_id.get(self.entry_id(name), name)

    def resolve(self, name: str) -> str | None:
        """The key a key, an inventory id or a hex offset names, or None."""
        key = self.key(name)
        return key if key in self.by_key else None

    def rekey_text(self, text: str) -> str:
        """Prose with every aliased inventory id written as its key."""
        return ID_TOKEN.sub(lambda m: self.by_id.get(m[1], m[1]), text) if self.rows else text

    def unkey_text(self, text: str) -> str:
        """Prose with every aliased key written as its inventory id."""
        return KEY_TOKEN.sub(lambda m: self.by_key.get(m[1], m[1]), text) if self.rows else text


EMPTY = Aliases()


def alias_path(sha1: str, root: Path | None = None) -> Path:
    """The alias table of a base ROM under `root`, else the running command's registry root."""
    from ..project import REGISTRY_ROOT, find_registry
    name = ALIAS_DIR / f"{sha1.lower()}.tsv"
    if root is not None:
        return Path(root) / name
    selected = REGISTRY_ROOT.get()
    if selected is not None and (Path(selected) / name).is_file():
        return Path(selected) / name
    return find_registry() / name


def read(path: Path) -> Aliases:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    if not lines or tuple(lines[0].split("\t")) != HEADER:
        raise ValueError(f"{path}: expected the header {' '.join(HEADER)}")
    rows = []
    for n, line in enumerate(lines[1:], 2):
        parts = line.split("\t")
        if len(parts) != 3 or not is_key(parts[0]) or not re.fullmatch(r"0x[0-9a-f]+", parts[1]):
            raise ValueError(f"{path}:{n}: invalid alias row")
        rows.append((parts[0], int(parts[1], 16), parts[2]))
    return Aliases(rows)


def write(path: Path, aliases: Aliases) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = ["\t".join(HEADER)] + [f"{k}\t{o:#09x}\t{i}" for k, o, i in aliases.rows]
    path.write_text("\n".join(body) + "\n", encoding="utf-8")


_cache: dict[Path, tuple[int, Aliases]] = {}


def load(sha1: str | None, root: Path | None = None) -> Aliases:
    """The alias table of a base ROM; EMPTY without one."""
    if not sha1:
        return EMPTY
    path = alias_path(sha1, root)
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return EMPTY
    cached = _cache.get(path)
    if cached is None or cached[0] != stamp:
        cached = (stamp, read(path))
        _cache[path] = cached
    return cached[1]


def for_profile(prof) -> Aliases:
    """The alias table of a profile's base ROM, or of the effective profile."""
    if prof is None:
        from ..profile.profile import EFFECTIVE_PROFILE
        prof = EFFECTIVE_PROFILE.get()
    try:
        sha1 = prof.sha1 if prof is not None else None
    except (KeyError, TypeError, AttributeError):
        sha1 = None
    return load(sha1)


def for_catalog(catalog: dict) -> Aliases:
    """The keys a loaded catalog carries: the msgctxt of each inventory id (no offsets)."""
    return Aliases((e.msgctxt, None, sid) for sid, (_, e) in catalog.items() if e.msgctxt != sid)


_sha1s: dict[str, str | None] = {}


def for_profile_name(name: str | None) -> Aliases:
    """The alias table of a profile named by a catalog header; EMPTY when unknown."""
    if not name:
        return EMPTY
    if name not in _sha1s:
        from ..core.rom import RomError
        from ..profile.profile import Profile
        try:
            _sha1s[name] = Profile.by_name(name).sha1
        except (RomError, OSError, KeyError, ValueError):
            _sha1s[name] = None
    return load(_sha1s[name])


def text_hash(english: str, previous: str, salt: int = 0) -> str:
    data = english + "\0" + previous + (f"\0{salt}" if salt else "")
    return "hash:" + hashlib.sha1(data.encode("utf-8")).hexdigest()[:8]


def exported(e: dict) -> bool:
    return not ({"unverified", "keep"} & set(e["flags"]))


def _pointer(data, at: int) -> int | None:
    if at < 0 or at + 4 > len(data):
        return None
    value = int.from_bytes(data[at:at + 4], "little")
    return value - POINTER_LOW if POINTER_LOW <= value < POINTER_LOW + len(data) else None


def description_slots(prof, data) -> list[tuple[str, int, int]]:
    """(table, index, string offset) of the Pokedex description tables, in profile order."""
    out = []
    for block in (prof.get("pokedex") or {}).values():
        if not isinstance(block, dict) or not isinstance(block.get("name_table"), str):
            continue
        name = block["name_table"]
        table = block.get("descriptions")
        if isinstance(table, dict) and "base" in table and "count" in table:
            for i in range(int(table["count"])):
                if (target := _pointer(data, int(table["base"]) + i * int(table.get("stride", 4)))) is not None:
                    out.append((name, i, target))
        field = (block.get("fields") or {}).get("description")
        if isinstance(field, dict) and int(field.get("width", 0)) == 4 and {"base", "stride", "count"} <= block.keys():
            for i in range(int(block.get("first", 0)), int(block["count"])):
                at = int(block["base"]) + i * int(block["stride"]) + int(field["offset"])
                if (target := _pointer(data, at)) is not None:
                    out.append((name, i, target))
    return out


def battle_slots(prof, data) -> list[tuple[int, int]]:
    """(battle string id, string offset) of the profile's [keys.battle] table."""
    table = (prof.get("keys") or {}).get("battle")
    if not table:
        return []
    out = []
    for i in range(int(table.get("first", 0)), int(table["count"])):
        if (target := _pointer(data, int(table["base"]) + 4 * i)) is not None:
            out.append((i, target))
    return out


def map_number(mid: str, record: dict) -> tuple[int, int] | None:
    """The (group, number) of a context map: its lowest engine map id, else its own."""
    names = record.get("engine_maps")
    names = [mid] if names is None else names
    found = sorted(tuple(int(x) for x in m.split(".")) for m in names if re.fullmatch(r"\d+\.\d+", m))
    return found[0] if found else None


def map_slots(ctx: dict, events, every: bool = False) -> dict:
    """Inventory id -> the first (group, number, event, n, i) of its map uses; `every`: all of them, in order."""
    maps = ctx.get("maps") or {}
    numbers = {mid: map_number(mid, m) for mid, m in maps.items()}
    out = {}
    for sid, rec in (ctx.get("strings") or {}).items():
        found = []
        for u in rec.get("uses", []):
            mid = u.get("map") or ""
            if numbers.get(mid) is None or u.get("event") not in EVENTS:
                continue
            script = int(u["script"], 16)
            n = next((n for kind, n, s in events(int(maps[mid]["header"], 16))
                      if kind == u["event"] and s == script), None)
            if n is None:
                continue
            group, number = numbers[mid]
            found.append((group, number, EVENTS.index(u["event"]), n, u["order"]))
        if found:
            out[sid] = sorted(set(found)) if every else min(found)
    return out


def assign(inv: dict, ctx: dict, prof=None, data=None, events=None, known: Aliases = EMPTY) -> Aliases:
    """The keys of the exported inventory entries (see the module)."""
    entries = sorted((e for e in inv["entries"] if exported(e)), key=lambda e: e["offset"])
    by_offset = {e["offset"]: e for e in entries if "copy" not in e.get("flags", ())}
    copies = {e["id"]: e for e in entries if "copy" in e.get("flags", ())}
    tables = {t["name"]: t for t in inv.get("pointer_tables", [])}
    keys: dict[str, str] = {}
    taken: set[str] = set()

    def give(e, key):
        if e["id"] not in keys and key not in taken:
            keys[e["id"]] = key
            taken.add(key)

    for e in entries:
        if e["id"] in known.by_id:
            give(e, known.by_id[e["id"]])
    for e in entries:
        if e["kind"] == "fixed":
            give(e, f"table:{e['table']}/{e['index']}")
        elif e.get("table") in tables:
            t = tables[e["table"]]
            fields = t.get("fields") or [0]
            key = f"table:{e['table']}/{e['index']}"
            if len(fields) > 1:
                record = t["base"] + e["index"] * t["stride"]
                at = next((r["at"] - record for r in e["refs"] if 0 <= r["at"] - record < t["stride"]), None)
                if at not in fields:
                    continue
                key += f"/{fields.index(at)}"
            give(e, key)
    if prof is not None and data is not None:
        for name, i, target in description_slots(prof, data):
            if target in by_offset:
                give(by_offset[target], f"table:{name}/{i}/description")
        for i, target in battle_slots(prof, data):
            if target in by_offset:
                give(by_offset[target], f"battle:{i}")
    if events is not None:
        slots = map_slots(ctx, events)
        for e in sorted((e for e in entries if e["id"] in slots), key=lambda e: slots[e["id"]]):
            g, n, kind, k, i = slots[e["id"]]
            give(e, f"map:{g}.{n}/{EVENTS[kind]}:{k}/{i}")
        # a copy can find its first site taken
        for sid, found in map_slots(ctx, events, every=True).items():
            if sid in copies:
                for g, n, kind, k, i in found:
                    give(copies[sid], f"map:{g}.{n}/{EVENTS[kind]}:{k}/{i}")
    for e in entries:
        salt = 0
        while e["id"] not in keys:
            give(e, text_hash(e["text"], e["id"], salt))
            salt += 1
    return Aliases((keys[e["id"]], e["offset"], e["id"]) for e in entries)


def rom_events(rom, prof):
    """Header offset -> [(event, n, script)] of the ROM's map headers, cached."""
    from ..discover.maps import Walker
    walker = Walker(rom, prof)
    memo: dict[int, list] = {}

    def events(h: int):
        if h not in memo:
            memo[h] = [(kind, n, s) for kind, n, s, _, _ in walker.events(h)]
        return memo[h]
    return events


def entry_ids(data: dict, aliases: Aliases) -> None:
    """Name the entries of a profile's project sections by inventory id, in place."""
    if not aliases:
        return
    slots = data.get("policy_slots") or {}
    if isinstance(slots.get("window_width"), list):
        slots["window_width"] = [aliases.entry_id(x) for x in slots["window_width"]]
    for note in data.get("translator_notes") or []:
        if isinstance(note, dict) and isinstance(note.get("id"), str):
            note["id"] = aliases.entry_id(note["id"])


def policy_entry_ids(doc: dict, aliases: Aliases) -> None:
    """Name the entries of a language policy by inventory id, in place."""
    if not aliases:
        return
    layout = doc.get("layout") or {}
    if isinstance(layout.get("window_width"), dict):
        layout["window_width"] = {aliases.entry_id(k): v for k, v in layout["window_width"].items()}
    marks = layout.get("unverified") or {}
    if isinstance(marks.get("window_width"), list):
        marks["window_width"] = [aliases.entry_id(x) for x in marks["window_width"]]
    for kind in ("droppable", "addable"):
        for selection in (doc.get("qa") or {}).get(kind) or []:
            if isinstance(selection, dict) and isinstance(selection.get("ids"), list):
                selection["ids"] = [aliases.entry_id(x) for x in selection["ids"]]
