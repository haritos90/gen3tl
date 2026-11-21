"""Transitive printer wrappers proved from parameter forwarding."""

from __future__ import annotations

from collections import defaultdict
import sys
from array import array

from ..core.thumb import ThumbError, decode
from ..core.rom import ROM_BASE
from ..families.fingerprints import Fingerprint, find
from .window_switches import walk
from .accessors import Expression
from .references import function_instructions, instruction_word
from .window_flow import describe, parameter, trace
from .window_functions import function_index, returning_body
from .window_buffers import Transfers


def call_index(rom):
    result = defaultdict(list)
    halves = array("H", rom.data[:len(rom) & ~1])
    if sys.byteorder != "little":
        halves.byteswap()
    for index, half in enumerate(halves):
        if index + 1 < len(halves) and half & 0xF800 == 0xF000 and halves[index + 1] & 0xF800 == 0xF800:
            instruction = decode(rom.data, index * 2)
            if 0 <= instruction.target < len(rom):
                result[instruction.target].append(instruction.address)
    indirect = []
    for target, sites in list(result.items()):
        thunk = decode(rom.data, target)
        if thunk.literal is not None and thunk.literal + 4 <= len(rom) and target + 4 <= len(rom):
            branch = decode(rom.data, target + 2)
            value = rom.u32(thunk.literal)
            if (branch.mnemonic == 'bx' and branch.rm == thunk.rd and value & 1
                    and ROM_BASE <= value < ROM_BASE + len(rom)):
                indirect.extend(((value - ROM_BASE) & ~1, site) for site in sites)
            continue
        if thunk.mnemonic != "bx" or thunk.rm >= 13:
            continue
        for site in sites:
            for at in range(site - 2, max(-1, site - 130), -2):
                instruction = decode(rom.data, at)
                if instruction.mnemonic == "b" or instruction.mnemonic == "bl" and thunk.rm < 4:
                    break
                if thunk.rm not in instruction.written:
                    continue
                if instruction.literal is not None and instruction.literal + 4 <= len(rom):
                    value = rom.u32(instruction.literal)
                    if value & 1 and ROM_BASE <= value < ROM_BASE + len(rom):
                        indirect.append(((value - ROM_BASE) & ~1, site))
                break
    for target, site in indirect:
        if site not in result[target]:
            result[target].append(site)
    return result


def argument(call, flow, row, name):
    index = row.get(name + "_argument")
    if index is None:
        return Expression(row[name + "_constant"]) if name + "_constant" in row else None
    value = call.arguments[index]
    field = row.get(name + "_field")
    if field is not None:
        address = value.add(Expression(field))
        value = flow.frames[call].get((address.key, 4 if name == "text" else 1))
    if name == 'text' and value is not None:
        sources = flow.contents.get(call, {}).get(value.key, ())
        if sources:
            values = sorted({source.source for source in sources}, key=repr)
            return values[0] if len({value.key for value in values}) == 1 else None
    return value


def buffered_sources(call, flow, row):
    index = row.get('text_argument')
    if index is None:
        return ()
    value = call.arguments[index]
    if 'text_field' in row:
        value = flow.frames[call].get((value.add(Expression(row['text_field'])).key, 4))
    return sorted(flow.contents.get(call, {}).get(value.key, ()), key=repr) if value is not None else ()


def instantiate(value, call):
    if "parameter" in value:
        return call.arguments[value["parameter"]]
    if "constant" in value:
        return Expression(value["constant"])
    if "narrow" in value:
        child = instantiate(value["value"], call)
        if child is not None and not child.terms:
            return Expression(child.constant & ((1 << value["narrow"]) - 1))
    return None


class Printers:
    def __init__(self, rom, seeds):
        self.rom, self.seeds = rom, seeds
        self.rows, self.flows, self.rejected = {}, {}, {}
        self.code = function_index(rom)
        self.transfers = Transfers(rom)
        self.ready = False

    def flow(self, start):
        if start not in self.flows:
            body, _ = returning_body(self.rom, start)
            if not body:
                body = function_instructions(self.rom, start, 4096)
            try:
                self.flows[start] = trace(self.rom, start, max(i.address + i.size for i in body), transfers=self.transfers) if body else None
            except ThumbError as exc:
                self.rejected[start] = str(exc)
                self.flows[start] = None
        return self.flows[start]

    def derive(self):
        from .window_consumers import matches

        if self.ready:
            return
        self.ready = True
        locations = find(self.rom.data, tuple(Fingerprint.read(row) for row in self.seeds), walker=walk)
        for row in self.seeds:
            for at in locations[row["name"]]:
                if matches(self.rom, at, row):
                    self.rows.setdefault(at, []).append(dict(row, depth=0, chain=[]))
        reverse = call_index(self.rom)
        frontier = set(self.rows)
        inspected = {}
        for depth in range(1, 9):
            callers = set()
            for target in sorted(frontier):
                for site in reverse.get(target, ()):
                    if site not in inspected:
                        inspected[site] = instruction_word(self.rom, site) or self.code.instruction(site)
                    proof = inspected[site]
                    if proof:
                        callers.add(proof["function"])
            following = set()
            for start in sorted(callers - self.rows.keys()):
                flow = self.flow(start)
                if flow is None:
                    continue
                summaries = []
                for call in flow.calls:
                    for callee in self.rows.get(call.target, ()):
                        value = argument(call, flow, callee, "text")
                        arg = parameter(value) if value is not None else None
                        if arg is None or arg >= 12:
                            continue
                        row = {"name": "derived-printer", "text_argument": arg, "depth": callee["depth"] + 1,
                               "chain": [call.site, *callee["chain"]]}
                        buffers = [{'call': site, 'helper': self.transfers.get(target)}
                                   for source in buffered_sources(call, flow, callee) for site, target in source.copies]
                        if buffers or callee.get('buffers'):
                            row['buffers'] = [*buffers, *callee.get('buffers', [])]
                        for name in ("window", "font", "x", "y"):
                            if name in callee:
                                value = instantiate(callee[name], call)
                            else:
                                value = argument(call, flow, callee, name)
                            if value is not None:
                                row[name] = describe(value, flow.loads)
                        if row not in summaries:
                            summaries.append(row)
                if summaries:
                    self.rows[start] = summaries
                    following.add(start)
            frontier = following
            if not frontier:
                break

    def get(self, target):
        self.derive()
        rows = self.rows.get(target, ())
        arguments = {row["text_argument"] for row in rows if "text_field" not in row}
        if len(arguments) != 1 or any("text_field" in row for row in rows):
            return None
        return {**rows[0], "summaries": [{key: value for key, value in row.items()
                if key in ("name", "depth", "chain", "text_argument", "window", "font", "x", "y", "buffers")
                or key.endswith(("_argument", "_field", "_constant"))} for row in rows]}
