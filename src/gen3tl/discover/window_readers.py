"""Generic menu table readers derived from printer and loop summaries."""

from __future__ import annotations

from collections import defaultdict

from ..core.lists import append_unique
from ..core.rom import ROM_BASE
from ..core.thumb import ThumbError, decode
from .references import function_instructions, instruction_word
from .window_flow import describe, trace
from .window_switches import walk
from .accessors import Expression
from .window_printers import argument, call_index
from ..families.fingerprints import Fingerprint, find


def simplify(value):
    if 'narrow' in value:
        child = simplify(value['value'])
        return {'constant': child['constant'] & ((1 << value['narrow']) - 1)} if 'constant' in child else dict(value, value=child)
    if 'sum' in value:
        terms = [simplify(term) for term in value['sum']]
        constant = sum(term['constant'] for term in terms if 'constant' in term) & 0xFFFFFFFF
        terms = [term for term in terms if 'constant' not in term]
        if not terms:
            return {'constant': constant}
        if constant == 0 and len(terms) == 1 and terms[0].get('scale') == 1:
            return terms[0]['value']
        return {'sum': [{'constant': constant}, *terms]}
    if 'scale' in value:
        child = simplify(value['value'])
        return {'constant': child['constant'] * value['scale'] & 0xFFFFFFFF} if 'constant' in child else dict(value, value=child)
    return value


def reify(value):
    if 'constant' in value:
        return Expression(value['constant'])
    if 'unknown' in value:
        return Expression.symbol(value['unknown'])
    if 'parameter' in value:
        return Expression.symbol(f"a{value['parameter']}")
    if 'scale' in value:
        child = reify(value['value'])
        return child.multiply(value['scale']) if child is not None else None
    if 'sum' in value:
        result = Expression()
        for term in value['sum']:
            child = reify(term)
            if child is None:
                return None
            result = result.add(child)
        return result
    return None


def substitute(rom, value, args, frame=None, loads=None):
    if 'parameter' in value:
        return args[value['parameter']] if value['parameter'] < len(args) else {'unknown': 'stack-argument-bound'}
    if 'load' in value:
        address = substitute(rom, value['address'], args, frame, loads)
        width = value['load']
        expression = reify(address)
        if frame is not None and expression is not None:
            key = expression.key, width
            if key in frame:
                return describe(frame[key], loads or {})
            for (old, size), stored in frame.items():
                if (old[1] == expression.terms and old[0] <= expression.constant
                        and expression.constant + width <= old[0] + size and not stored.terms):
                    return {'constant': stored.constant >> (8 * (expression.constant - old[0])) & ((1 << (8 * width)) - 1)}
        at = address.get('constant', -1) - ROM_BASE
        if 0 <= at <= len(rom) - width and at % width == 0:
            return {'constant': int.from_bytes(rom.data[at:at + width], 'little')}
        return dict(value, address=address)
    if 'sum' in value:
        return simplify({'sum': [substitute(rom, term, args, frame, loads) for term in value['sum']]})
    if 'value' in value:
        return simplify(dict(value, value=substitute(rom, value['value'], args, frame, loads)))
    return value


def unnarrow(value):
    while 'narrow' in value:
        value = value['value']
    return value


def presentation_case(rom, call, at):
    """Qualify the existing template header comparison."""
    if 'header_x' not in call:
        return None
    field, expected = call['id_field'], call['header_id']
    base = at - call['field']
    if not 0 <= field <= call['stride'] - 4 or not 0 <= base + field <= len(rom) - 4:
        return None
    equal = rom.u32(base + field) == expected
    return {'slot': f"case:{field}:{'eq' if equal else 'ne'}:{expected}",
            'field': field, 'value': expected, 'equal': equal,
            'x': call['header_x'] if equal else call['x']}


def base_offset(address):
    if 'parameter' in address:
        return address, 0
    if 'sum' in address:
        constant = sum(term.get('constant', 0) for term in address['sum'])
        terms = [term for term in address['sum'] if 'constant' not in term]
        if len(terms) == 1 and terms[0].get('scale') == 1 and 'parameter' in terms[0]['value']:
            return terms[0]['value'], constant
    return None


def expression_parameter(value):
    if 'parameter' in value:
        return value['parameter']
    if 'load' in value:
        return expression_parameter(value['address'])
    if 'value' in value:
        return expression_parameter(value['value'])
    if 'sum' in value:
        found = {expression_parameter(term) for term in value['sum']} - {-1}
        return next(iter(found)) if len(found) == 1 else -1
    return -1


def parameter_expression(value):
    if 'unknown' in value:
        return False
    if 'constant' in value:
        return True
    if 'parameter' in value:
        return True
    if 'load' in value:
        return expression_parameter(value['address']) >= 0 and parameter_expression(value['address'])
    if 'value' in value:
        return parameter_expression(value['value'])
    if 'sum' in value:
        return all(parameter_expression(term) for term in value['sum'])
    return False


def primitives(rom, printers):
    rules = defaultdict(list)
    printers.derive()
    for start, flow in list(printers.flows.items()):
        if flow is None:
            continue
        bodies = function_instructions(rom, start, 4096)
        if sum(i.mnemonic == "b" and i.target < i.address for i in bodies) != 1:
            continue
        groups = defaultdict(list)
        for call in flow.calls:
            for printer in printers.rows.get(call.target, ()):
                text = argument(call, flow, printer, 'text')
                if text is None:
                    continue
                expr = describe(text, flow.loads)
                if expr.get('load') != 4:
                    continue
                candidate = base_offset(expr['address'])
                if candidate:
                    base, offset = candidate
                    groups[(call.site, expr['site'], base['parameter'])].append((offset, call, printer))
        for (call_at, read_at, base_arg), samples in groups.items():
            offsets = sorted({offset for offset, _, _ in samples})
            if len(offsets) != 2:
                continue
            stride = offsets[1] - offsets[0]
            if stride < 4 or stride > 64 or stride % 4 or offsets[0] >= stride:
                continue
            loops = []
            for compare in flow.comparisons:
                if compare.left.terms or compare.left.constant not in (1, 2):
                    continue
                edge = next((i for i in bodies if i.address == compare.site + 2), None)
                if edge is None or edge.mnemonic != 'b' or edge.condition != 'cc' or not edge.target <= read_at < call_at < edge.address:
                    continue
                count = describe(compare.right, flow.loads)
                if 'parameter' in unnarrow(count):
                    loops.append((compare.site, count))
            if not loops or len({site for site, _ in loops}) != 1:
                continue
            compare_site = loops[0][0]
            edge = decode(rom.data, compare_site + 2)
            counter = decode(rom.data, compare_site).rn
            end = max(i.address + i.size for i in bodies)
            try:
                symbolic = trace(rom, start, end, loop_indices={edge.target: counter})
            except ThumbError:
                continue
            index_name = f"index@{edge.target:x}"
            expected = tuple(sorted(((f"a{base_arg}", 1), (index_name, stride))))
            reads = [read for read in symbolic.accesses if read.site == read_at and not read.write
                     and read.width == 4 and read.address.terms == expected
                     and read.address.constant == offsets[0]]
            increments = []
            for comparison in symbolic.comparisons:
                value = comparison.left
                while (value.constant == 0 and len(value.terms) == 1 and value.terms[0][1] == 1
                       and value.terms[0][0] in symbolic.loads
                       and symbolic.loads[value.terms[0][0]][1] == 0):
                    value = symbolic.loads[value.terms[0][0]][0]
                if comparison.site == compare_site:
                    increments.append(value.key == (1, ((index_name, 1),)))
            if not reads or not increments or not all(increments):
                continue
            call, printer = samples[0][1:]
            row = {'base': {'parameter': base_arg}, 'count': loops[0][1], 'first': {'constant': 0},
                   'stride': stride, 'field': offsets[0], 'entry': start, 'read': read_at,
                   'printer_call': call_at, 'count_compare': loops[0][0], 'chain': [], 'depth': 0}
            for name in ('window', 'font', 'x', 'y'):
                if name in printer:
                    value = substitute(rom, printer[name], [describe(v, flow.loads) for v in call.arguments])
                else:
                    expression = argument(call, flow, printer, name)
                    value = describe(expression, flow.loads) if expression is not None else {'unknown': 'printer-field'}
                row[name] = value
            rules[start].append(row)
    return dict(rules)


class Readers:
    def __init__(self, rom, printers, templates=()):
        self.rom, self.printers, self.templates = rom, printers, templates
        self.rules, self.calls, self.stops = {}, [], []

    def derive(self):
        self.rules = primitives(self.rom, self.printers)
        from .window_consumers import matches

        for contract in self.templates:
            found = find(self.rom.data, (Fingerprint.read(contract),), walker=walk)[contract['name']]
            for entry in found:
                if not matches(self.rom, entry, contract):
                    continue
                call = decode(self.rom.data, entry + contract['reader_call'])
                if call.mnemonic != 'bl' or not matches(self.rom, call.target, contract['reader']):
                    continue
                render = decode(self.rom.data, call.target + contract['render_call'])
                printer = self.printers.get(render.target) if render.mnemonic == 'bl' else None
                if printer is None or printer['text_argument'] != contract['render_argument']:
                    continue
                read = decode(self.rom.data, call.target + contract['text_read'])
                if read.mnemonic != 'ldr' or read.width != 4:
                    continue
                template = contract['template_argument']
                def field(offset, width):
                    return {'load': width, 'address': simplify({'sum': [{'constant': offset},
                        {'scale': 1, 'value': {'parameter': template}}]})}
                row = {'base': field(contract['items_field'], 4),
                       'count': field(contract['count_field'], contract['count_width']),
                       'first': {'constant': 0}, 'stride': contract['stride'], 'field': contract['text_field'],
                       'entry': entry, 'read': call.target + contract['text_read'],
                       'printer_call': render.address, 'chain': [], 'depth': 0,
                       'window': field(contract['window_field'], 1),
                       'font': {'unknown': 'template-font-bitfield'},
                       'x': field(contract['x_field'], 1), 'y': {'unknown': 'template-position-bitfield'},
                       'template_argument': template, 'template_evidence': contract['evidence']}
                if 'header_x_field' in contract:
                    row.update(header_x=field(contract['header_x_field'], 1),
                               id_field=contract['id_field'], header_id=contract['header_id'])
                self.rules.setdefault(entry, []).append(row)
        reverse = call_index(self.rom)
        frontier = set(self.rules)
        inspected = {}
        for depth in range(1, 9):
            following = set()
            for target in sorted(frontier):
                for site in reverse.get(target, ()):
                    if site not in inspected:
                        inspected[site] = instruction_word(self.rom, site) or self.printers.code.instruction(site)
                    proof = inspected[site]
                    if not proof:
                        continue
                    start = proof['function']
                    flow = self.printers.flow(start)
                    if flow is None:
                        continue
                    for call in flow.calls:
                        if call.site != site or call.target != target:
                            continue
                        args = [describe(value, flow.loads) for value in call.arguments]
                        for rule in list(self.rules[target]):
                            if start in rule['chain']:
                                continue
                            row = {**rule, **{name: substitute(self.rom, rule[name], args, flow.frames[call], flow.loads)
                                    for name in ('base', 'count', 'first', 'window', 'font', 'x', 'y', 'header_x') if name in rule},
                                   'chain': [start, *rule['chain']], 'depth': depth, 'call': site, 'caller': start,
                                   'argument': unnarrow(rule['base']).get('parameter', rule.get('template_argument', -1)), 'target': target}
                            if (expression_parameter(row['base']) >= 0 and parameter_expression(row['base'])
                                    and parameter_expression(row['count'])):
                                if row not in self.rules.get(start, ()):
                                    row['template_argument'] = expression_parameter(row['base'])
                                    self.rules.setdefault(start, []).append(row)
                                    following.add(start)
                            elif 'constant' in row['base'] and 'constant' in row['count']:
                                base, count = row['base']['constant'] - ROM_BASE, row['count']['constant']
                                if base % 4 == 0 and count > 0 and count <= 1024 and 0 <= base <= len(self.rom) - count * row['stride']:
                                    append_unique(self.calls, row)
                            else:
                                self.stops.append(row)
            frontier = following
            if not frontier:
                break
