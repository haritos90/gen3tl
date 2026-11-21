"""Translation batch plan: split the untranslated catalog into ordered batches for translation and review."""

from __future__ import annotations

import json
import math
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .batch import approved_by_english, prefill
from .catalog import load_catalog


@dataclass
class Batch:
    name: str
    kind: str                 # fill | translate | done | active | deferred
    group: str
    files: list[str]
    ids: list[str] = field(default_factory=list)
    words: int = 0
    level: float | None = None
    notes: str = ""
    prefill: int | None = None    # entries a pre-filled export drafts; None for done and active


def trainer_levels(rom: bytes, table: dict) -> dict[int, float]:
    """Average party level per trainer id (gTrainers layout of the profile's trainer table)."""
    out = {}
    for tid in range(table["count"]):
        rec = table["base"] + tid * table["stride"]
        flags = rom[rec + table.get("party_flags_offset", 0)]
        n = rom[rec + table.get("party_count_offset", 32)]
        ptr = struct.unpack_from("<I", rom, rec + table.get("party_pointer_offset", 36))[0] - 0x08000000
        if not 0 < n <= 6 or not 0 <= ptr < len(rom) - 16 * n:
            continue
        stride = 16 if flags & 1 else 8      # custom moves make a party record 16 bytes
        lv = [struct.unpack_from("<H", rom, ptr + i * stride + 2)[0] for i in range(n)]
        lv = [x for x in lv if 0 < x <= 100]
        if lv:
            out[tid] = sum(lv) / len(lv)
    return out


def map_levels(context: dict, levels: dict[int, float], slug) -> dict[str, float]:
    acc: dict[str, list[float]] = {}
    for v in context.values():
        for u in v.get("uses", []):
            t = u.get("trainer")
            if t and u.get("map_name") and t["id"] in levels:
                acc.setdefault(slug(u["map_name"]), []).append(levels[t["id"]])
    return {k: sum(v) / len(v) for k, v in acc.items()}


def _chunks(ids: list[str], words: dict[str, int], size: int, max_words: int,
            key=None) -> list[list[str]]:
    """Split ids in order into the fewest near-equal parts within `size` units and `max_words`."""
    if key is None:
        units = [[i] for i in ids]
    else:
        order: dict[str, list[str]] = {}
        for i in ids:
            order.setdefault(key(i), []).append(i)
        units = list(order.values())
    uw = [words[u[0]] if key else words[u[0]] for u in units]
    total_w = sum(uw)
    n = max(math.ceil(len(units) / size), math.ceil(total_w / max_words), 1)
    parts, cur, cur_w, k = [], [], 0, 1
    for u, w in zip(units, uw):
        target = total_w * k / n
        if cur and (len(cur) >= size or cur_w + w / 2 > target) and k < n:
            parts.append(cur)
            cur, k = [], k + 1
        cur.append(u)
        cur_w += w
    if cur:
        parts.append(cur)
    return [[i for u in part for i in u] for part in parts]


def plan(po_root: Path, levels: dict[str, float], first: list[str], filled: set[str],
         approved: dict[str, list[str]], size: int = 150, min_size: int = 60,
         max_words: int = 2500, active: dict[str, list[str]] | None = None,
         deferred: set[str] | None = None, excluded: set[str] | None = None) -> list[Batch]:
    """The batches of the untranslated catalog, in translation order."""
    excluded = excluded or set()
    from . import keys
    loaded = load_catalog(po_root)
    entry_id = keys.for_catalog(loaded).entry_id   # batches may name entries by key
    filled = {entry_id(sid) for sid in filled}
    approved = {name: [entry_id(sid) for sid in ids] for name, ids in approved.items()}
    active = {name: [entry_id(sid) for sid in ids] for name, ids in (active or {}).items()}
    catalog = {sid: row for sid, row in loaded.items() if sid not in excluded}
    words = {sid: len(e.msgid.split()) for sid, (p, e) in catalog.items()}
    approved = {name: [sid for sid in ids if sid not in excluded] for name, ids in approved.items()}
    active = {name: [sid for sid in ids if sid not in excluded] for name, ids in (active or {}).items()}
    deferred = deferred or set()
    done_ids = {i for ids in list(approved.values()) + list(active.values()) for i in ids}
    by_file: dict[str, list[str]] = {}
    held: dict[str, list[str]] = {}   # deferred entries per group, not planned
    for sid, (p, e) in catalog.items():   # catalog order = file order, entry order
        # empty: translated as the empty string.
        # no msgid: unused table slot.
        if e.msgstr or "empty" in e.flags or not e.msgid or sid in done_ids:
            continue
        rel = p.relative_to(po_root).as_posix()
        if sid in deferred:
            held.setdefault(rel.split("/")[0].removesuffix(".po"), []).append(sid)
            continue
        by_file.setdefault(rel, []).append(sid)

    out = [Batch(n, "done", "", [], ids, sum(words.get(i, 0) for i in ids)) for n, ids in approved.items()]
    out += [Batch(n, "active", "", [], ids, sum(words.get(i, 0) for i in ids), notes="exported, in review")
            for n, ids in active.items()]

    def group_of(rel: str) -> str:
        return rel.split("/")[0].removesuffix(".po")

    english = {sid: e.msgid for sid, (p, e) in catalog.items()}
    # approved and exported batches keep their names:
    # - extra parts of a file are numbered past them
    # - so a plan name never rewrites an exported batch
    taken = set(approved) | set(active)

    def free(base: str, n: int = 0) -> str:
        n += 1
        while f"{base}-{n}" in taken:
            n += 1
        return f"{base}-{n}"

    def emit(group: str, label: str, files: list[str], ids: list[str], kind="translate", level=None, notes=""):
        # outside maps, repeated English shares one batch
        key = None if group == "maps" else english.get
        parts = _chunks(ids, words, size, max_words, key) if kind == "translate" else [ids]
        base, name = f"{group}-{label}", ""
        for part in parts:
            if len(parts) == 1 and base not in taken:
                name = base
            else:
                k = int(name.rsplit("-", 1)[1]) if name.startswith(base + "-") else 0
                name = free(base, k)
            taken.add(name)
            out.append(Batch(name, kind, group, files, part, sum(words[i] for i in part), level, notes))

    # 1-2: tables
    tables = sorted(f for f in by_file if group_of(f) == "tables")
    fill_ids = [i for f in tables for i in by_file[f] if i in filled]
    if fill_ids:
        emit("tables", "fill", tables, fill_ids, kind="fill", notes="gen3tl lang fill-tables; check fit in game")
    for f in tables:
        rest = [i for i in by_file[f] if i not in filled]
        if rest:
            emit("tables", Path(f).stem.replace("_", "-"), [f], rest)

    # 3: maps in story order
    maps = [f for f in by_file if group_of(f) == "maps"]
    order_hint = {Path(f).stem: k for k, f in enumerate(sorted(maps))}
    lv: dict[str, float | None] = {}
    prev = 0.0
    for f in sorted(maps):                  # maps without trainers follow the map before them
        stem = Path(f).stem
        prev = levels.get(stem, prev)
        lv[stem] = levels.get(stem)
        if lv[stem] is None:
            lv[stem] = prev
    rank = {s: k for k, s in enumerate(first)}
    maps.sort(key=lambda f: (rank.get(Path(f).stem, len(rank)), lv[Path(f).stem] or 0.0, order_hint[Path(f).stem]))
    def label(files: list[str]) -> str:
        stem = Path(files[0]).stem.replace("_", "-")
        return stem + (f"-plus-{len(files) - 1}" if len(files) > 1 else "")

    def flush(group: str, files: list[str], level=None) -> None:
        if files:
            emit(group, label(files), list(files), [i for p in files for i in by_file[p]], level=level)
            files.clear()

    pending: list[str] = []
    for f in maps:
        stem = Path(f).stem
        if len(by_file[f]) >= min_size or stem in rank:
            n_pending = sum(len(by_file[p]) for p in pending)
            if pending and n_pending + len(by_file[f]) <= size and stem not in rank:
                pending.append(f)            # small maps join the next one when it fits
                flush("maps", pending, lv[Path(pending[0]).stem])
                continue
            flush("maps", pending, lv[Path(pending[0]).stem] if pending else None)
            emit("maps", stem, [f], by_file[f], level=lv[stem])
            continue
        pending.append(f)
        if sum(len(by_file[p]) for p in pending) >= min_size:
            flush("maps", pending, lv[Path(pending[0]).stem])
    flush("maps", pending, lv[Path(pending[0]).stem] if pending else None)
    # tiny map batches join the previous one
    merged: list[Batch] = []
    for b in out:
        prev = merged[-1] if merged else None
        if (b.group == "maps" and prev is not None and prev.group == "maps" and prev.kind == b.kind == "translate"
                and len(b.ids) < min_size // 2 and len(prev.ids) + len(b.ids) <= size + min_size // 2):
            prev.ids += b.ids
            prev.words += b.words
            prev.files += [f for f in b.files if f not in prev.files]
            prev.name = prev.name.split("-plus-")[0] + f"-plus-{len(prev.files) - 1}" if len(prev.files) > 1 else prev.name
            if prev.name in taken:              # the merged name can be an exported batch's
                prev.name = free(prev.name)
            taken.add(prev.name)
            continue
        merged.append(b)
    out[:] = merged

    # 4: script regions, battle, system
    for grp in ("script", "battle", "system"):
        pending = []
        for f in sorted(f for f in by_file if group_of(f) == grp):
            pending.append(f)
            if sum(len(by_file[p]) for p in pending) >= min_size:
                flush(grp, pending)
        flush(grp, pending)

    # deferred entries: listed once, never exported by plan name
    for grp, ids in held.items():
        out.append(Batch(f"deferred-{grp}", "deferred", grp, [], ids, sum(words.get(i, 0) for i in ids),
                         notes="unreachable by build/reach.json; not translated"))

    # entries a pre-filled export of each batch would draft
    approved_en = approved_by_english(catalog)
    for b in out:
        if b.kind not in ("done", "active", "deferred"):
            b.prefill = len(prefill(b.ids, catalog, approved_en)[0])
    key = keys.for_catalog(loaded).key   # the plan names entries by key
    for b in out:
        b.ids = [key(i) for i in b.ids]
    return out


def write(batches: list[Batch], root: Path) -> tuple[Path, Path]:
    """plan.tsv for people and plan.json with the entry ids for `catalog batch-export --ids`."""
    root.mkdir(parents=True, exist_ok=True)
    tsv = root / "plan.tsv"
    lines = ["order\tbatch\tkind\tgroup\tentries\tprefill\twords\tlevel\tfiles\tnotes"]
    for k, b in enumerate(batches, 1):
        lvl = "" if b.level is None else f"{b.level:.0f}"
        pre = "" if b.prefill is None else b.prefill
        files = ",".join(Path(f).stem for f in b.files)
        lines.append(f"{k}\t{b.name}\t{b.kind}\t{b.group}\t{len(b.ids)}\t{pre}\t{b.words}\t{lvl}\t{files}\t{b.notes}")
    tsv.write_text("\n".join(lines) + "\n", encoding="utf-8")
    js = root / "plan.json"
    js.write_text(json.dumps([asdict(b) for b in batches], ensure_ascii=False, indent=0), encoding="utf-8")
    return tsv, js
