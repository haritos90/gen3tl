"""Script operand reads continued through native printer summaries."""

from __future__ import annotations

from ..core.lists import append_unique
from ..core.thumb import ThumbError, decode
from ..families.windows import argument_slot
from .accessors import Expression
from .scripts import _reader_shape, infer_handler
from .window_flow import describe, trace


class Scripts:
    def __init__(self, consumers, table, count):
        self.consumers, self.rom = consumers, consumers.rom
        self.table, self.count = table, count
        self.handlers, self.flows, self.stops = {}, {}, {}

    def handler(self, opcode):
        if opcode not in self.handlers:
            row = None
            if 0 <= opcode < self.count and 0 <= self.table + opcode * 4 <= len(self.rom) - 4:
                pointer = self.rom.ptr_at(self.table + opcode * 4)
                if pointer is not None and pointer & 1:
                    row = infer_handler(self.rom.data, opcode, pointer & ~1, {})
            self.handlers[opcode] = row
        return self.handlers[opcode]

    def flow(self, handler, read):
        key = handler.entry, read.site
        if key not in self.flows:
            fingerprint, branches, _, error = self.consumers.function(handler.entry)
            instruction = decode(self.rom.data, read.site)
            reader = _reader_shape(self.rom.data, read.target) if read.target is not None else None
            if (error or instruction.mnemonic != 'bl' or instruction.target != read.target
                    or reader is None or reader.width != 4):
                self.flows[key] = None
            else:
                try:
                    self.flows[key] = trace(self.rom, handler.entry, handler.entry + fingerprint.length,
                        transfers=self.consumers.summaries.transfers,
                        returns={(read.site, read.target): Expression.symbol('script-text')})
                except ThumbError:
                    self.flows[key] = None
        return self.flows[key]

    def resolve(self, string, reference):
        from .window_consumers import ASSUMPTION

        found, reasons, stops = [], set(), []
        if self.rom.ptr_at(reference['at']) != string['offset']:
            return [], ['Script text pointer does not match its reference.']
        for proof in reference.get('evidence', ()):
            opcode, field, command = (proof.get(k) for k in ('opcode', 'field', 'command'))
            if (type(opcode) is not int or type(field) is not int or type(command) is not int
                    or not 0 <= command < len(self.rom) or self.rom.data[command] != opcode
                    or command + field != reference['at']):
                reasons.add('Script operand has no matching command boundary.')
                continue
            handler = self.handler(opcode)
            if handler is None or handler.status not in ('fixed', 'shape'):
                reasons.add('Script handler format is unresolved.')
                continue
            if proof.get('length') not in handler.lengths:
                reasons.add('Script command length contradicts its handler format.')
                continue
            reads = [r for r in handler.reads if r.offset + 1 == field and r.width == 4 and r.target is not None]
            if not reads:
                reasons.add('Script operand has no complete word-reader summary.')
            for read in reads:
                flow = self.flow(handler, read)
                if flow is None:
                    reasons.add('Script handler argument flow is unresolved.')
                    stops.append({'site': read.site, 'target': read.target, 'kind': 'reader-flow'})
                    continue
                fingerprint, branches, _, _ = self.consumers.function(handler.entry)
                symbol = Expression.symbol('script-text').key
                for call in flow.calls:
                    printer = self.consumers.printer(call.target)
                    if printer is None:
                        if any(v.key == symbol for v in call.arguments):
                            transfer = self.consumers.summaries.transfers.get(call.target)
                            stops.append({'site': call.site, 'target': call.target,
                                          'kind': 'buffer-continuation' if transfer else 'call-continuation',
                                          'arguments': [describe(v, flow.loads) for v in call.arguments]})
                        continue
                    argument = printer['text_argument']
                    sources = [(call.arguments[argument], ())] + [
                        (source.source, source.copies) for source in
                        sorted(flow.sources.get(call, {}).get(argument, ()), key=repr)]
                    for value, copies in sources:
                        if value.key != symbol:
                            continue
                        row = {'function': fingerprint.sha256,
                               'slot': f'call:{call.site - handler.entry}:{argument_slot(argument)}',
                               'tier': 'shape', 'kind': 'script', 'entry': handler.entry,
                               'length': fingerprint.length, 'branches': branches, 'printer': printer['name'],
                               'evidence': {'method': 'script-operand+word-reader+printer-summary',
                                            'reference': reference['at'], 'command': command, 'opcode': opcode,
                                            'field': field, 'read': read.site, 'reader': read.target,
                                            'call': call.site, 'target': call.target,
                                            'printer_summaries': printer.get('summaries', []),
                                            'arguments': [describe(v, flow.loads) for v in call.arguments],
                                            'assumption': ASSUMPTION,
                                            'handler_assumptions': list(handler.assumptions)}}
                        if copies:
                            row['evidence']['buffer_trace'] = [{'call': at,
                                'helper': self.consumers.summaries.transfers.get(target)} for at, target in copies]
                        append_unique(found, row)
                for access in flow.accesses:
                    if access.write and access.value.key == symbol:
                        stops.append({'site': access.site, 'kind': 'stored-operand',
                                      'address': describe(access.address, flow.loads)})
                if not found:
                    stops.extend({'site': site, 'kind': kind} for site, kind in flow.stops)
                    reasons.add('Script operand has no local printer continuation.')
        self.stops[reference['at']] = stops
        return found, [] if found else sorted(reasons)
