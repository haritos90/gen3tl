"""Returned ROM pointers and affine text fields at native call sites."""

from dataclasses import replace

from ..core.lists import append_unique
from ..core.rom import ROM_BASE
from ..core.thumb import ThumbError
from .accessors import Expression, Literal, MemoryAccess
from .window_flow import trace
from .window_functions import returning_body


class Getters:
    def __init__(self, rom):
        self.rom, self.rows, self.pending = rom, {}, set()

    def get(self, start):
        if start in self.rows:
            return self.rows[start]
        if start in self.pending or len(self.pending) >= 3:
            return None
        self.pending.add(start)
        body, reason = returning_body(self.rom, start, span=1024, limit=192)
        result = None
        if not reason:
            try:
                flow = trace(self.rom, start, max(i.address + i.size for i in body), getters=self)
                values = tuple(sorted({value for _, value in flow.returned}, key=repr))
                def rom_source(value):
                    if any(ROM_BASE <= origin.value < ROM_BASE + len(self.rom) for origin in value.origins):
                        return True
                    return any(name in flow.loads and any(
                        ROM_BASE <= origin.value < ROM_BASE + len(self.rom)
                        for origin in flow.loads[name][0].origins) for name, _ in value.terms)

                if values and len({value.key for value in values}) <= 4 and any(rom_source(v) for v in values):
                    result = (values, flow.loads)
            except ThumbError:
                pass
        self.pending.remove(start)
        self.rows[start] = result
        return result

    def resolve(self, call, loads, accesses):
        summary = self.get(call.target)
        if summary is None:
            return ()
        values, original = summary

        def substitute(value, depth=0):
            if depth > 12:
                return Expression.symbol('getter-depth')
            result = Expression(value.constant, origins=value.origins, sites=value.sites)
            for name, scale in value.terms:
                if name.startswith('a') and name[1:].isdigit() and int(name[1:]) < len(call.arguments):
                    child = call.arguments[int(name[1:])]
                elif name in original:
                    at, width, signed, site = original[name]
                    address = substitute(at, depth + 1)
                    if width == 0:
                        bits = int(name.split(':', 1)[0][1:])
                        if not address.terms:
                            child = replace(address, constant=address.constant & ((1 << bits) - 1))
                        else:
                            key = f'u{bits}:{address.key!r}'
                            loads[key] = (address, 0, False, site)
                            child = Expression.symbol(key).at(site)
                    elif not address.terms and ROM_BASE <= address.constant <= ROM_BASE + len(self.rom) - width:
                        at = address.constant - ROM_BASE
                        number = int.from_bytes(self.rom.data[at:at + width], 'little', signed=signed)
                        child = Expression(number, origins=(Literal(site, at, number, 0),), sites=address.sites).at(site)
                        accesses.add(MemoryAccess(site, address, width, signed, child))
                    else:
                        key = f'getter{width}@{call.site:x}:{site:x}:{address.key!r}'
                        loads[key] = (address, width, signed, site)
                        child = Expression.symbol(key).at(site)
                        accesses.add(MemoryAccess(site, address, width, signed, child))
                else:
                    child = Expression.symbol(f'getter-unknown@{call.site:x}:{name}')
                result = result.add(child, scale)
            return result.at(call.site)

        return tuple(sorted({substitute(value) for value in values}, key=repr))


def source_match(value, loads, string):
    target = ROM_BASE + string['offset']
    if not value.terms:
        if value.constant == target:
            return {'kind': 'literal-pool', 'reference': value.origins[0].pool if value.origins else string['offset']}
        return None
    if len(value.terms) == 1 and value.terms[0][1] == 1 and value.constant == 0:
        name = value.terms[0][0]
        if name in loads:
            address, width, signed, site = loads[name]
            if width == 4 and len(address.terms) == 1:
                stride = address.terms[0][1]
                for reference in string['references']:
                    for proof in reference.get('evidence', ()):
                        base = proof.get('start')
                        if (base is not None and proof.get('stride') == stride
                                and any(origin.value == ROM_BASE + base for origin in address.origins)
                                and (ROM_BASE + reference['at'] - address.constant) % stride == 0):
                            return {'kind': 'table', 'reference': reference['at'], 'base': base,
                                    'stride': stride, 'read': site}
    if len(value.terms) == 1:
        stride = value.terms[0][1]
        if (stride >= 2 and ROM_BASE <= value.constant <= target
                and (target - value.constant) % stride == 0
                and (target - value.constant) // stride < 65536
                and any(ROM_BASE <= origin.value <= value.constant
                        and value.constant - origin.value < stride for origin in value.origins)):
            return {'kind': 'inline-field', 'reference': value.origins[0].pool,
                    'base': value.constant - ROM_BASE, 'stride': stride,
                    'index': (target - value.constant) // stride}
    return None


class ReturnConsumers:
    def __init__(self, consumers):
        self.consumers = consumers
        self.reverse = None
        self.callers = {}
        self.stops = {}

    def owners(self, strings):
        from .scan import pointer_index

        c = self.consumers
        c.code.derive()
        index = pointer_index(c.rom)
        by_string = {}
        for string in strings:
            pools = {r['at'] for r in string['references']}
            for reference in string['references']:
                for proof in reference.get('evidence', ()):
                    pools.update(index.get(proof.get('start'), ()))
            entries = {start for pool in pools for start, _ in c.code.pools.get(pool, ())}
            if not string['references']:
                for base, positions in index.items():
                    if 0 <= string['offset'] - base < 65536:
                        entries.update(start for pool in positions for start, _ in c.code.pools.get(pool, ()))
                        entries.update(start for field in positions for pool in index.get(field, ())
                                       for start, _ in c.code.pools.get(pool, ()))
            by_string[string['id']] = entries
        return by_string

    def caller_sites(self, target):
        from .window_printers import call_index
        from .references import instruction_word

        c = self.consumers
        if self.reverse is None:
            self.reverse = call_index(c.rom)
        if target not in self.callers:
            result = []
            for site in self.reverse.get(target, ()):
                proof = c.code.instruction(site) or instruction_word(c.rom, site)
                if proof:
                    result.append((proof['function'], site))
            self.callers[target] = result
        return self.callers[target]

    def audit(self, strings):
        from ..families.windows import argument_slot
        from .window_flow import describe
        from .window_consumers import ASSUMPTION

        c = self.consumers
        owners = self.owners(strings)
        rows = {}
        for string in strings:
            found, seen = [], set()
            todo = []
            for entry in sorted(owners[string['id']]):
                summary = c.getters.get(entry)
                if summary and any(source_match(value, summary[1], string) for value in summary[0]):
                    todo.append((entry, ()))
            while todo:
                target, chain = todo.pop()
                if target in seen or len(chain) >= 3:
                    continue
                seen.add(target)
                for start, site in self.caller_sites(target):
                    fingerprint, branches, _, error = c.function(start)
                    if error:
                        continue
                    flow = c.flow(start, start + fingerprint.length)
                    if flow is None:
                        self.stops.setdefault(string['id'], []).append({'entry': start, 'site': site, 'reason': 'caller-flow-bound'})
                        continue
                    for call in flow.calls:
                        printer = c.printer(call.target)
                        if printer is None:
                            continue
                        arg = printer['text_argument']
                        values = [call.arguments[arg], *[v.source for v in flow.sources.get(call, {}).get(arg, ())]]
                        for value in values:
                            proof = source_match(value, flow.loads, string)
                            if site not in value.sites or proof is None:
                                continue
                            row = {'function': fingerprint.sha256,
                                   'slot': f'call:{call.site - start}:{argument_slot(arg)}',
                                   'tier': 'shape', 'kind': proof['kind'], 'entry': start,
                                   'length': fingerprint.length, 'branches': branches,
                                   'printer': printer['name'], 'evidence': {
                                       'method': 'returned-pointer+caller-argument+printer', **proof,
                                       'call': call.site, 'target': call.target,
                                       'getters': [*chain, {'entry': target, 'call': site}],
                                       'arguments': [describe(v, flow.loads) for v in call.arguments],
                                       'assumption': ASSUMPTION + ' Getter domains describe possible indices, not reachability.'}}
                            append_unique(found, row)
                    if any(site in value.sites and source_match(value, flow.loads, string)
                           for _, value in flow.returned):
                        todo.append((start, (*chain, {'entry': target, 'call': site})))
                    elif not found:
                        self.stops.setdefault(string['id'], []).append({'entry': start, 'site': site,
                            'reason': 'returned-value-has-no-printer-continuation',
                            'exits': list(flow.stops)})
            rows[string['id']] = found
        return rows


def reference_strings(consumers, entries):
    from ..core.charmap import Charmap, CharmapError
    from .table_windows import pointer_run

    rom, strings, seen = consumers.rom, {}, set()
    todo = [(entry, 0) for entry in entries]
    charmap = Charmap()

    def append(target, reference):
        try:
            text, _ = charmap.decode(rom.data, target, limit=2048)
        except CharmapError:
            return
        if not text or '{0x' in text:
            return
        row = strings.setdefault(target, {'id': f's{target:07X}', 'offset': target, 'references': []})
        append_unique(row['references'], reference)

    while todo:
        start, depth = todo.pop()
        if start in seen or depth >= 3:
            continue
        seen.add(start)
        fingerprint, _, _, error = consumers.function(start)
        if error:
            continue
        flow = consumers.flow(start, start + fingerprint.length)
        for call in flow.calls if flow else ():
            summary = consumers.getters.get(call.target)
            if summary is None:
                continue
            values, loads = summary
            useful = False
            for value in values:
                if not value.terms and ROM_BASE <= value.constant < ROM_BASE + len(rom):
                    for origin in value.origins:
                        if origin.value != value.constant:
                            continue
                        append(value.constant - ROM_BASE, {'at': origin.pool, 'kind': 'literal-pool',
                            'status': 'reference', 'evidence': [{'loads': [{'function': call.target,
                            'load': origin.site, 'register': origin.register}]}]})
                        useful = True
                elif value.constant == 0 and len(value.terms) == 1 and value.terms[0][1] == 1:
                    read = loads.get(value.terms[0][0])
                    if read is None or read[1] != 4 or len(read[0].terms) != 1 or read[0].terms[0][1] != 4:
                        continue
                    for origin in read[0].origins:
                        base = origin.value - ROM_BASE
                        try:
                            count = pointer_run(rom, base)
                        except ValueError:
                            continue
                        for index in range(count):
                            at = base + index * 4
                            append(rom.ptr_at(at), {'at': at, 'kind': 'table', 'status': 'reference',
                                'evidence': [{'start': base, 'end': base + count * 4,
                                              'stride': 4, 'fields': [0]}]})
                        useful = True
            if useful:
                todo.append((call.target, depth + 1))
    return list(strings.values())
