"""Measured table windows from bounded indexed text consumers."""

from __future__ import annotations

from ..core.charmap import Charmap, CharmapError
from .window_switches import walk
from ..families.fingerprints import Fingerprint, find
from ..families.windows import SLOT, slot_argument
from .window_consumers import ASSUMPTION, Consumers, matches


def pointer_run(rom, base, limit=4096):
    """Reject invalid text instead of truncating pointer runs."""
    if base % 4 or not 0 <= base <= len(rom) - 4:
        raise ValueError("Text pointer run has no aligned ROM base.")
    charmap = Charmap()
    count = 0
    while count < limit and base + count * 4 + 4 <= len(rom):
        target = rom.ptr_at(base + count * 4)
        if target is None:
            if count:
                return count
            break
        try:
            text, _ = charmap.decode(rom.data, target, limit=2048)
        except CharmapError as exc:
            raise ValueError("Pointer run contains an invalid text target.") from exc
        if not text or "{0x" in text:
            raise ValueError("Pointer run contains an invalid text target.")
        count += 1
    raise ValueError("Text pointer run has no bounded non-pointer end.")


def discover(rom, knowledge, consumers=None):
    from .foundations import Result, evidence, fact

    result = Result({}, [], {"bindings": [], "unresolved": []})
    if not knowledge.table_rows:
        return result
    required = {row["function"] for row in knowledge.table_rows}
    functions = {row["sha256"]: row for row in knowledge.functions if row["sha256"] in required}
    locations = find(rom.data, tuple(Fingerprint.read(row) for row in functions.values()), walker=walk)
    consumers = consumers or Consumers(rom, knowledge.printers, knowledge.templates)
    rows = []
    for rule in knowledge.table_rows:
        try:
            function = functions[rule["function"]]
            found = [at for at in locations[function["name"]] if matches(rom, at, function)]
            if len(found) != 1:
                raise ValueError("Table consumer fingerprint is missing or ambiguous.")
            start = found[0]
            if rule["extent"] == "reader-count":
                bindings = [row for entries in consumers.reader_bindings().values() for _, row in entries
                            if row['function'] == rule['function'] and row['slot'] == rule['slot']]
                domains = {(row['evidence']['base'], row['evidence']['stride'], row['evidence']['field'],
                            row['evidence']['count']) for row in bindings}
                if len(domains) != 1:
                    raise ValueError("Table caller has no unique reader domain.")
                base, stride, field, count = domains.pop()
                value = {"name": rule["name"], "base": base, "stride": stride, "count": count, "fields": [field],
                         **{key: value for key, value in rule.items() if key not in ("function", "slot", "extent")}}
                proof = evidence("The generic menu reader consumes a counted text-pointer field. " + ASSUMPTION,
                    "caller+reader-summary+counted-text-field", [base, base + count * stride],
                    [bindings[0]['evidence']['call'], bindings[0]['evidence']['read']], rows=count)
                item = fact(f"layout.table_window[name={rule['name']}]", "window", value, proof, tier="shape")
                item.section, item.source = "layout.table_window", "table-windows/1.1.0"
                result.facts.append(item)
                rows.append(value)
                result.metadata["bindings"].append({"name": rule["name"], "function": rule["function"],
                                                   "slot": rule["slot"], "base": base, "count": count})
                continue
            slot = SLOT.fullmatch(rule["slot"])
            call_at, argument, read_at = start + int(slot[1]), slot_argument(slot[2]), start + int(slot[3])
            analysis = consumers.segment(start, call_at + 4)
            if analysis is None:
                raise ValueError("Indexed reader analysis exceeds its state bound.")
            flow = consumers.flow(start, call_at + 4)
            calls = [call for call in flow.calls if call.site == call_at
                     and consumers.printer(call.target) is not None
                     and consumers.printer(call.target)["text_argument"] == argument] if flow else []
            values = [value for call in calls for value in [call.arguments[argument], *[
                source.source for source in flow.sources.get(call, {}).get(argument, ())]]]
            reads = [read for read in analysis.accessors if read.site == read_at and read.width == 4
                     and len(read.indices) == 1 and read.indices[0][1] == 4
                     and any(read.site in value.sites for value in values)]
            bases = {read.base + read.field_offset for read in reads}
            if len(bases) != 1:
                raise ValueError("No unique indexed pointer field reaches the printer.")
            base = bases.pop()
            count = pointer_run(rom, base)
            value = {"name": rule["name"], "base": base, "stride": 4, "count": count, "fields": [0],
                     **{key: value for key, value in rule.items() if key not in ("function", "slot", "extent")}}
            proof = evidence("An indexed text pointer reaches the fingerprinted printer. " + ASSUMPTION +
                " The contiguous valid text-pointer run is assumed to be one table and ends at a non-pointer word.",
                "consumer-slot+indexed-reader+terminated-pointer-run", [base, base + count * 4],
                [start, read_at, call_at], rows=count)
            item = fact(f"layout.table_window[name={rule['name']}]", "window", value, proof, tier="shape")
            item.section, item.source = "layout.table_window", "table-windows/1.0.0"
            result.facts.append(item)
            rows.append(value)
            result.metadata["bindings"].append({"name": rule["name"], "function": rule["function"],
                                               "slot": rule["slot"], "base": base, "count": count})
        except ValueError as exc:
            result.metadata["unresolved"].append({"name": rule["name"], "reason": str(exc)})
    if rows:
        result.data["layout"] = {"table_window": rows}
    return result
