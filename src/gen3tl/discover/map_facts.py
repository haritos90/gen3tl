"""Map banks, headers and event roots derived from native readers."""

from __future__ import annotations

from ..core.rom import ROM_BASE
from ..core.thumb import decode
from ..families.fingerprints import Fingerprint, find
from .maps import Walker
from .scan import pointer_index


class Roots(Walker):
    """Collect map structure without decoding script commands."""

    def __init__(self, rom, profile):
        super().__init__(rom, profile)
        self.roots = []

    def mapsec_name(self, sec):
        return str(sec)

    def walk_script(self, start, mid, name, event, local_id, sprite):
        self.roots.append({key: value for key, value in {
            "map": mid, "header": self.ctx.headers[mid], "event": event,
            "script": start, "local_id": local_id, "sprite": sprite,
            "tier": "shape", "method": "event-record-script-pointer"}.items() if value is not None})


def reader_tables(rom, knowledge):
    """Resolve fingerprinted two-level indexed readers and their XOR pools."""
    signatures = tuple(Fingerprint.read(row) for row in knowledge.get("readers", []))
    matches = find(rom.data, signatures)
    tables = []
    for signature in signatures:
        for start in matches[signature.name]:
            table_load, mask_load = decode(rom.data, start), decode(rom.data, start + 6)
            if table_load.literal is None or mask_load.literal is None:
                continue
            table = rom.ptr_at(table_load.literal)
            if table is None or table % 4:
                continue
            mask = rom.u32(mask_load.literal)
            banks = []
            for at in range(table, min(len(rom) - 3, table + 256 * 4), 4):
                bank = (rom.u32(at) ^ mask) - ROM_BASE
                if not 0 <= bank <= len(rom) - 4 or bank % 4 or rom.ptr_at(bank) is None:
                    break
                banks.append(bank)
            if banks:
                tables.append({"groups": table, "count": len(banks), "bank_xor": mask,
                               "banks": banks, "reader": start,
                               "pools": [table_load.literal, mask_load.literal]})
    return tables


def shared_table(rom, banks, headers, index):
    """Match bank coordinates shared by native and plain tables."""
    candidates = set()
    for number, bank in enumerate(banks):
        for at in index.get(bank, []):
            table = at - number * 4
            if at % 4 or table < 0:
                continue
            candidates.add(table)
    scored = []
    for table in sorted(candidates):
        run = []
        for at in range(table, min(len(rom) - 3, table + 256 * 4), 4):
            bank = rom.ptr_at(at)
            if bank is None or bank % 4:
                break
            run.append(bank)
        overlap = sum(a == b and rom.ptr_at(a) in headers for a, b in zip(run, banks))
        if overlap >= 2:
            scored.append((overlap, table, run))
    best = max((row[0] for row in scored), default=0)
    winners = [row for row in scored if row[0] == best]
    return winners[0] if len(winners) == 1 else None


def discover(rom, knowledge, family):
    from .foundations import Result, evidence, fact

    result = Result({}, [], {"headers": [], "roots": [], "shortfalls": []})
    tables = reader_tables(rom, knowledge)
    if len(tables) != 1:
        reason = "A unique native map bank reader with valid banks is required."
        result.issues.append(fact("maps.engine", "map", None,
                                  evidence(reason, "map-reader-literal-pools", candidates=len(tables)),
                                  status="unresolved"))
        return result
    runtime, = tables
    walker = Roots(rom, {"engine": {"family": family}})
    headers = set(walker.find_headers())
    shared = shared_table(rom, runtime["banks"], headers, pointer_index(rom))
    engine = {key: runtime[key] for key in ("groups", "count", "bank_xor")}
    result.data["maps"] = {"engine": engine}
    result.facts.append(fact("maps.engine", "map", engine,
        evidence("Indexed native loads and the XOR literal resolve every runtime bank; the first invalid bank ends the contiguous run.",
                 "map-reader-literal-pools+bank-shape", runtime["pools"], [runtime["reader"]],
                 banks=len(runtime["banks"]))))
    if shared is None:
        reason = "No unique plain bank table shares at least two runtime banks."
        result.issues.append(fact("maps.groups", "map", None,
                                  evidence(reason, "shared-bank-run"), status="unresolved"))
    else:
        overlap, table, banks = shared
        result.data["maps"]["groups"] = table
        result.facts.append(fact("maps.groups", "map", table,
            evidence("The unique best plain bank run shares runtime coordinates; retained banks are assumed to keep their indices.",
                     "shared-bank-run+header-shape", [runtime["groups"], table], [runtime["reader"]],
                     shared_banks=overlap, banks=len(banks))))
        walker.prof["maps"] = {"groups": table}
        walker.walk_all()
    walker.walk_structural()
    for mid, header in walker.ctx.headers.items():
        result.metadata["headers"].append({key: value for key, value in {"map": mid, "header": header,
            "layout": rom.ptr_at(header), "events": rom.ptr_at(header + 4),
            "scripts": rom.ptr_at(header + 8), "tier": "shape",
            "method": "bank-walk+header-layout-event-shape"}.items() if value is not None})
    result.metadata["roots"] = walker.roots
    result.metadata["runtime"] = runtime
    result.metadata["counts"] = {"headers": len(walker.ctx.headers), "roots": len(walker.roots),
                                 "unique_roots": len({row["script"] for row in walker.roots})}
    return result
