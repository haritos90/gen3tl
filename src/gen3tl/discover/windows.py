"""Generate instance window bindings from fingerprinted text consumers."""

from __future__ import annotations

from collections import defaultdict

from ..core.charmap import Charmap, CharmapError
from .window_switches import walk
from ..families.fingerprints import Fingerprint, find
from ..profile import schema
from .window_consumers import ASSUMPTION, Consumers, matches


def reference_strings(rom, knowledge, consumers=None, profile=None):
    """Enumerate text literals from matched consumers without overlay bindings."""
    signatures = tuple(Fingerprint.read(row) for row in knowledge.functions)
    locations = find(rom.data, signatures, walker=walk)
    strings, unresolved, entries = {}, [], set()
    charmap = Charmap()
    for function in knowledge.functions:
        found = [at for at in locations[function["name"]] if matches(rom, at, function)]
        if len(found) != 1:
            unresolved.append({"function": function["sha256"], "reason": "Consumer fingerprint is missing or ambiguous.",
                               "matches": found})
            continue
        start = found[0]
        entries.add(start)
        for instruction in walk(rom.data, start, start + function["length"]):
            pool = instruction.literal
            target = rom.ptr_at(pool) if pool is not None and pool + 4 <= len(rom) else None
            if target is None:
                continue
            try:
                text, _ = charmap.decode(rom.data, target, limit=2048)
            except CharmapError:
                continue
            if not text or "{0x" in text:
                continue
            string = strings.setdefault(target, {"id": f"s{target:07X}", "offset": target, "references": []})
            string["references"].append({"at": pool, "status": "reference", "kind": "literal-pool",
                "evidence": [{"pool": pool, "method": "function-fingerprint+literal-load", "loads": [
                    {"load": instruction.address, "register": instruction.rd, "function": start}]}]})
    if consumers is not None:
        required = {row['sha256'] for row in knowledge.functions}
        for at, bindings in consumers.reader_bindings().items():
            for target, consumer in bindings:
                if consumer['function'] not in required:
                    continue
                proof = consumer['evidence']
                string = strings.setdefault(target, {"id": f"s{target:07X}", "offset": target, "references": []})
                string['references'].append({'at': at, 'kind': 'table', 'status': 'reference', 'evidence': [{
                    'start': proof['base'], 'end': proof['base'] + proof['stride'] * proof['count'],
                    'stride': proof['stride'], 'fields': [proof['field']]}]})
    if consumers is not None:
        from .window_getters import reference_strings as returned_strings

        for row in returned_strings(consumers, entries):
            target = row['offset']
            if target in strings:
                strings[target]['references'].extend(r for r in row['references']
                                                     if r not in strings[target]['references'])
            else:
                strings[target] = row
    if consumers is not None and consumers.scripts is not None and profile is not None:
        from .references import script_operands

        scripts = consumers.scripts
        opcodes = {opcode for opcode in range(scripts.count)
                   if (rom.ptr_at(scripts.table + opcode * 4) or 0) & ~1 in entries}
        if opcodes:
            operands, _ = script_operands(rom, profile)
            for at, proof in sorted(operands.items()):
                if proof['opcode'] not in opcodes:
                    continue
                target = rom.ptr_at(at)
                if target is None:
                    continue
                try:
                    text, _ = charmap.decode(rom.data, target, limit=2048)
                except CharmapError:
                    continue
                if not text or '{0x' in text:
                    continue
                string = strings.setdefault(target, {'id': f's{target:07X}', 'offset': target, 'references': []})
                string['references'].append({'at': at, 'kind': 'script', 'status': 'reference', 'evidence': [proof]})
    return [strings[key] for key in sorted(strings)], unresolved


def bind(audit, rows):
    """Conflicting windows remain explicit instead of choosing one budget."""
    windows = {(row["function"], row["slot"]): row for row in rows}
    bindings, unresolved = [], []
    for string in audit["strings"]:
        found = []
        for consumer in string["consumers"]:
            row = windows.get((consumer["function"], consumer["slot"]))
            if row is not None:
                found.append((row, consumer))
        if not found:
            continue
        values = [{key: value for key, value in row.items() if key not in ("function", "slot")}
                  for row, _ in found]
        if any(value != values[0] for value in values):
            unresolved.append({"string": string["id"], "reason": "Consumer slots select different windows.",
                               "windows": sorted({value["name"] for value in values})})
            continue
        bindings.append({"string": string["id"], "window": values[0],
                         "consumers": [consumer for _, consumer in found]})
    return bindings, unresolved


def discover(rom, knowledge, profile=None):
    from .foundations import Result, evidence, fact
    from . import table_windows

    result = Result({}, [], {"bindings": [], "unresolved": []})
    consumers = Consumers(rom, knowledge.printers, knowledge.templates,
                          script=profile.get("script") if profile else None)
    tables = table_windows.discover(rom, knowledge, consumers)
    result.data.update(tables.data)
    result.facts.extend(tables.facts)
    result.metadata["tables"] = tables.metadata
    if not knowledge.rows:
        return result
    strings, missing = reference_strings(rom, knowledge, consumers, profile)
    audit = consumers.audit(strings)
    bindings, conflicts = bind(audit, knowledge.rows)
    result.metadata["audit"] = audit
    result.metadata["unresolved"] = missing + conflicts
    windows, layout, by_window = {}, {}, defaultdict(list)
    for binding in bindings:
        string, window = binding["string"], binding["window"]
        windows[window["name"]] = window
        by_window[window["name"]].extend(binding["consumers"])
        result.metadata["bindings"].append({"string": string, "window": window["name"],
                                           "consumers": binding["consumers"]})
        for field in schema.WINDOW_FIELDS:
            if field in window:
                layout.setdefault(f"window_{field}", {})[string] = window[field]
        if any(field in window for field in ("newline_scroll_after", "instant_print")):
            layout.setdefault("window_binding", {})[string] = {
                key: value for key, value in window.items() if key != "evidence"}
    if not windows:
        return result
    layout = {"window": [{key: value for key, value in row.items() if key != "evidence"}
                          for row in windows.values()], **layout}
    result.data.setdefault("layout", {}).update(layout)
    named = frozenset(f"layout.window_binding.{schema.key_text(string)}"
                      for string in layout.get("window_binding", {}))
    for item in schema.facts({"layout": layout}, named):
        if item.section == "layout.window":
            name = item.value["name"]
            sentence = windows[name]["evidence"]
            consumers = by_window[name]
        else:
            source = next(binding for binding in bindings if item.id.endswith(
                "." + schema.key_text(binding["string"])))
            sentence = (source["window"]["evidence"] if item.section == "layout.window_binding" else
                        "Reference argument flow selects the measured family consumer window. " + ASSUMPTION)
            consumers = source["consumers"]
        proof = evidence(sentence, "reference+consumer-slot+family-window",
                         [consumer["evidence"]["reference"] for consumer in consumers],
                         [consumer["evidence"]["call"] for consumer in consumers], bindings=len(consumers))
        derived = fact(item.id, "window", item.value, proof, tier="shape")
        derived.section = item.section
        derived.source = "windows/1.0.0"
        result.facts.append(derived)
    return result
