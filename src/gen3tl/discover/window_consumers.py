"""Reference-to-printer evidence with portable consumer function slots."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace

from ..core.rom import ROM_BASE
from ..core.charmap import Charmap, CharmapError
from ..core.thumb import ThumbError
from .window_switches import branches as local_branches, walk
from ..families.fingerprints import Fingerprint
from ..families.windows import argument_slot
from .accessors import analyze
from .references import function_instructions, literal_word
from .scan import pointer_index
from .window_printers import Printers
from .window_flow import trace, describe
from .window_functions import returning_body
from .window_getters import Getters, ReturnConsumers

ASSUMPTION = ("Returning function shapes identify code; saved registers follow the procedure-call convention. "
              "Callees receive pointers through arguments and preserve unrelated saved values. "
              "Valid copied buffers stay within their RAM bank and extend forward from their destination. "
              "Block joins combine possible values without proving path feasibility.")


def signature(rom, start):
    """Fingerprint a returning body and retain local branch destinations."""
    body, _ = returning_body(rom, start)
    if not body:
        body = function_instructions(rom, start, 4096)
    if not body:
        raise ValueError("No bounded returning consumer function.")
    end = max(i.address + i.size for i in body)
    fingerprint = Fingerprint.bootstrap(rom.data, start, end, "consumer", "window", walker=walk)
    branches = local_branches(rom.data, body, start, end)
    return fingerprint, branches


def matches(rom, start, row):
    """Masked calls alone cannot validate local control flow."""
    fingerprint = Fingerprint.read(row)
    end = start + fingerprint.length
    if start < 0 or start % 2 or end > len(rom):
        return False
    try:
        actual = Fingerprint.bootstrap(rom.data, start, end, fingerprint.name, fingerprint.category, walker=walk)
        branches = local_branches(rom.data, walk(rom.data, start, end), start, end)
    except ThumbError:
        return False
    return actual == fingerprint and branches == row.get("branches", [])


class Consumers:
    """Resolve literal sources only when their value reaches a known printer."""

    def __init__(self, rom, printers, templates=(), script=None):
        self.rom = rom
        self.printers = printers
        self.summaries = Printers(rom, printers)
        self.code = self.summaries.code
        self.flows = {}
        self.getters = Getters(rom)
        self.returned = ReturnConsumers(self)
        self.templates = templates
        self.reader_rows = None
        self.reader_report = None
        self.functions = {}
        self.targets = {}
        self.segments = {}
        self.tables = {}
        self.index = None
        self.scripts = None
        if script and 'command_table' in script and 'command_table_end' in script:
            from .window_scripts import Scripts

            base, end = script['command_table'], script['command_table_end']
            if 0 <= base < end <= len(rom) and base % 4 == 0 and (end - base) % 4 == 0:
                self.scripts = Scripts(self, base, (end - base) // 4)

    def printer(self, target):
        if target not in self.targets:
            self.targets[target] = self.summaries.get(target)
        return self.targets[target]

    def function(self, start):
        if start not in self.functions:
            try:
                fingerprint, branches = signature(self.rom, start)
                body = walk(self.rom.data, start, start + fingerprint.length)
                calls = [i for i in body if i.mnemonic == "bl" and self.printer(i.target)]
                flow = self.flow(start, start + fingerprint.length)
                if flow is not None:
                    instructions = {i.address: i for i in body if i.mnemonic == "bl"}
                    for call in flow.calls:
                        if call.site in instructions and self.printer(call.target):
                            instruction = replace(instructions[call.site], target=call.target)
                            if instruction not in calls:
                                calls.append(instruction)
                self.functions[start] = (fingerprint, branches, calls, None)
            except (ThumbError, ValueError) as exc:
                self.functions[start] = (None, None, None, str(exc))
        return self.functions[start]

    def segment(self, start, end):
        key = start, end
        if key not in self.segments:
            try:
                self.segments[key] = analyze(self.rom.data, start, end)
            except ThumbError:
                self.segments[key] = None
        return self.segments[key]

    def flow(self, start, end):
        key = start, end
        if key not in self.flows:
            try:
                self.flows[key] = trace(self.rom, start, end, transfers=self.summaries.transfers, getters=self.getters, merge=True)
            except ThumbError:
                self.flows[key] = None
        return self.flows[key]

    def literal(self, string, reference):
        found, reasons = [], set()
        for proof in reference.get("evidence", []):
            for load in proof.get("loads", []):
                start = load["function"]
                fingerprint, branches, calls, error = self.function(start)
                if error:
                    reasons.add(error)
                    continue
                for instruction in calls:
                    if instruction.address < load["load"]:
                        continue
                    analysis = self.flow(start, start + fingerprint.length)
                    if analysis is None:
                        analysis = self.flow(start, instruction.address + instruction.size)
                    if analysis is None:
                        analysis = self.flow(load["load"], instruction.address + instruction.size)
                    if analysis is None:
                        reasons.add("Literal-to-printer analysis exceeds its state bound.")
                        continue
                    printer = self.printer(instruction.target)
                    argument = printer["text_argument"]
                    for call in analysis.calls:
                        if call.site != instruction.address or call.target != instruction.target:
                            continue
                        values = [(call.arguments[argument], ())] + [
                            (source.source, source.copies) for source in
                            sorted(analysis.sources.get(call, {}).get(argument, ()), key=repr)]
                        for value, copies in values:
                            if (value.terms
                                    or value.constant != ROM_BASE + string["offset"]
                                    or not any(origin.pool == reference["at"] and origin.site == load["load"]
                                               for origin in value.origins)):
                                continue
                            slot = f"call:{call.site - start}:{argument_slot(argument)}"
                            row = {"function": fingerprint.sha256, "slot": slot, "tier": "shape",
                                   "kind": "literal-pool", "entry": start, "length": fingerprint.length,
                                   "branches": branches, "printer": printer["name"],
                                   "evidence": {"method": "literal-load+argument-flow+printer-fingerprint",
                                                "reference": reference["at"], "load": load["load"],
                                                "call": call.site, "target": call.target,
                                                "redirect": analysis.redirects.get(call, {}),
                                                "printer_depth": printer.get("depth", 0),
                                                "printer_summaries": printer.get("summaries", []),
                                                "arguments": [describe(value, analysis.loads) for value in call.arguments],
                                                "assumption": ASSUMPTION}}
                            if copies:
                                row['evidence']['buffer_trace'] = [
                                    {'call': site, 'helper': self.summaries.transfers.get(target)}
                                    for site, target in copies]
                            if row not in found:
                                found.append(row)
        if not found and not reasons:
            reasons.add("No literal value reaches a fingerprinted printer argument.")
        return found, sorted(reasons)

    def table(self, string, reference):
        found = []
        if self.index is None:
            self.index = pointer_index(self.rom)
        for proof in reference.get("evidence", []):
            base, end, stride = (proof[key] for key in ("start", "end", "stride"))
            fields = proof.get("fields", [proof.get("field", 0)])
            if not base <= reference["at"] < end or (reference["at"] - base) % stride not in fields:
                continue
            field = (reference["at"] - base) % stride
            key = base, stride, field
            if key not in self.tables:
                self.tables[key] = []
                for pool in self.index.get(base, ()):
                    literal = literal_word(self.rom, pool) or self.code.literal(pool)
                    for load in literal.get("loads", []) if literal else ():
                        start = load["function"]
                        fingerprint, branches, calls, error = self.function(start)
                        if error:
                            continue
                        for instruction in calls:
                            if instruction.address < load["load"]:
                                continue
                            analysis = self.segment(start, instruction.address + instruction.size)
                            if analysis is None:
                                continue
                            printer = self.printer(instruction.target)
                            argument = printer["text_argument"]
                            reads = [read for read in analysis.accessors if read.base + read.field_offset == base + field
                                     and read.width == 4 and len(read.indices) == 1 and read.indices[0][1] == stride]
                            flow = self.flow(start, start + fingerprint.length)
                            for read in reads:
                                for call in flow.calls if flow else ():
                                    values = [call.arguments[argument], *[source.source for source in
                                              flow.sources.get(call, {}).get(argument, ())]]
                                    if call.site != instruction.address or not any(read.site in value.sites for value in values):
                                        continue
                                    row = {"function": fingerprint.sha256,
                                        "slot": f"call:{call.site - start}:{argument_slot(argument)}:read:{read.site - start}",
                                        "tier": "shape", "kind": "table", "entry": start,
                                        "length": fingerprint.length, "branches": branches,
                                        "printer": printer["name"], "evidence": {
                                            "method": "table-literal+indexed-read+printer-fingerprint",
                                            "base": base, "stride": stride, "field": field,
                                            "read": read.site, "load": load["load"], "pool": pool,
                                            "call": call.site, "target": call.target,
                                            "redirect": flow.redirects.get(call, {}),
                                            "printer_depth": printer.get("depth", 0),
                                            "printer_summaries": printer.get("summaries", []),
                                            "arguments": [describe(value, flow.loads) for value in call.arguments],
                                            "assumption": ASSUMPTION + " The pointer run supplies possible reader indices; reachability is not inferred."}}
                                    copies = sorted({copy for source in flow.sources.get(call, {}).get(argument, ())
                                                     if read.site in source.source.sites for copy in source.copies})
                                    if copies:
                                        row['evidence']['buffer_trace'] = [
                                            {'call': site, 'helper': self.summaries.transfers.get(target)}
                                            for site, target in copies]
                                    self.tables[key].append(row)
            if self.rom.ptr_at(reference["at"]) != string["offset"]:
                continue
            for consumer in self.tables[key]:
                row = dict(consumer, evidence=dict(consumer["evidence"], reference=reference["at"]))
                if row not in found:
                    found.append(row)
        return found, [] if found else ["Indexed table reader continuation to a printer is unresolved."]

    def reader_bindings(self):
        from .window_readers import Readers, presentation_case

        if self.reader_rows is not None:
            return self.reader_rows
        readers = Readers(self.rom, self.summaries, self.templates)
        readers.derive()
        self.reader_report = {"rules": list(readers.rules.values()), "calls": readers.calls, "stops": readers.stops}
        self.reader_rows = {}
        charmap = Charmap()
        for call in readers.calls:
            base, count = call['base']['constant'] - ROM_BASE, call['count']['constant']
            if not 0 <= call['argument'] < 12:
                continue
            try:
                fingerprint, branches = signature(self.rom, call['caller'])
                entries = []
                for index in range(count):
                    at = base + index * call['stride'] + call['field']
                    target = self.rom.ptr_at(at)
                    if target is None:
                        raise ValueError("Table text field is not a ROM pointer.")
                    text, _ = charmap.decode(self.rom.data, target, limit=2048)
                    if not text or "{0x" in text:
                        raise ValueError("Table text field does not decode.")
                    entries.append((at, target))
            except (ThumbError, CharmapError, ValueError):
                continue
            for at, target in entries:
                x = call['x']
                if ('header_x' in call and self.rom.u32(at - call['field'] + call['id_field']) == call['header_id']):
                    x = call['header_x']
                row = {"function": fingerprint.sha256,
                       "slot": f"call:{call['call'] - call['caller']}:{argument_slot(call['argument'])}:table",
                       "tier": "shape", "kind": "table", "entry": call['caller'],
                       "length": fingerprint.length, "branches": branches, "printer": "table-reader",
                       "evidence": {"method": "table-argument+reader-summary+printer",
                                    "reference": at, "call": call['call'], "target": call['target'],
                                    "reader": call['entry'], "read": call['read'],
                                    "base": base, "stride": call['stride'], "field": call['field'], "count": count,
                                    "window": call['window'], "font": call['font'], "x": x, "y": call['y'],
                                    "chain": call['chain'], "assumption": ASSUMPTION +
                                    " The table domain supplies possible indices without proving menu reachability."}}
                self.reader_rows.setdefault(at, []).append((target, row))
                case = presentation_case(self.rom, call, at)
                if case is not None:
                    qualified = dict(row, slot=row['slot'] + ':' + case['slot'],
                                     evidence=dict(row['evidence'], presentation_case=case))
                    self.reader_rows[at].append((target, qualified))
        return self.reader_rows

    def audit(self, strings):
        rows = []
        readers = self.reader_bindings()
        returned = self.returned.audit(strings)
        for string in strings:
            consumers, unresolved = list(returned.get(string["id"], ())), []
            for reference in string["references"]:
                kind = reference["kind"]
                if kind == "literal-pool" and reference["status"] == "reference":
                    found, reasons = self.literal(string, reference)
                    consumers.extend(row for row in found if row not in consumers)
                elif kind == "table" and reference["status"] == "reference":
                    found, reasons = self.table(string, reference)
                    consumers.extend(row for row in found if row not in consumers)
                elif kind == "script" and reference["status"] == "reference" and self.scripts is not None:
                    found, reasons = self.scripts.resolve(string, reference)
                    consumers.extend(row for row in found if row not in consumers)
                elif reference["status"] == "non-reference":
                    continue
                else:
                    reasons = [{"script": "Script operand continuation to a printer is unresolved.",
                                "table": "Indexed table reader continuation to a printer is unresolved."}.get(
                                    kind, "Reference kind has no proven consumer.")]
                linked = [row for target, row in readers.get(reference['at'], ()) if target == string['offset']]
                consumers.extend(row for row in linked if row not in consumers)
                if linked:
                    reasons = []
                if kind in ("table", "unclassified") and reference["status"] != "non-reference":
                    proof = literal_word(self.rom, reference["at"]) or self.code.literal(reference["at"])
                    if proof:
                        found, other = self.literal(string, dict(reference, evidence=[proof]))
                        consumers.extend(row for row in found if row not in consumers)
                        reasons = [] if found else sorted(set(reasons + other))
                elif kind == "literal-pool" and reasons:
                    proof = self.code.literal(reference["at"])
                    if proof:
                        found, other = self.literal(string, dict(reference, evidence=[proof]))
                        consumers.extend(row for row in found if row not in consumers)
                        reasons = [] if found else sorted(set(reasons + other))
                if reasons:
                    row = {"at": reference["at"], "kind": kind, "reasons": reasons}
                    if kind == 'script' and self.scripts is not None:
                        row['stops'] = self.scripts.stops.get(reference['at'], [])
                    unresolved.append(row)
            if not string["references"]:
                unresolved.append({"kind": "none", "reasons": ["No direct pointer reference."]})
            rows.append({"id": string["id"], "offset": string["offset"],
                         "consumers": consumers, "unresolved": unresolved})
        kinds = Counter(row["kind"] for string in rows for row in string["consumers"])
        return {"strings": rows, "resolved": sum(bool(row["consumers"]) for row in rows),
                "multiple": sum(len({(c["function"], c["slot"]) for c in row["consumers"]}) > 1
                                for row in rows),
                "unresolved": sum(not row["consumers"] for row in rows),
                "by_reference_kind": dict(sorted(kinds.items()))}
