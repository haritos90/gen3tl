"""Shape summaries for text sources copied into printer buffers."""

from __future__ import annotations

from dataclasses import replace

from ..core.charmap import EOS, CTRL, PLACEHOLDER, SCROLL
from ..core.thumb import ThumbError, decode, walk
from .window_flow import parameter, trace
from .accessors import Expression
from .window_functions import returning_body, returns_to_caller


def base_parameter(value):
    if len(value.terms) != 1 or value.terms[0][1] != 1:
        return None
    name = value.terms[0][0]
    return int(name[1:]) if name.startswith('a') and name[1:].isdigit() else None


def copy_loop(rom, start):
    body, reason = returning_body(rom, start, span=128, limit=64)
    if reason or any(i.mnemonic == 'bl' for i in body):
        return None
    try:
        flow = trace(rom, start, max(i.address + i.size for i in body))
    except ThumbError:
        return None
    writes = [a for a in flow.accesses if a.write and a.width == 1]
    reads = [a for a in flow.accesses if not a.write and a.width == 1]
    pairs = {(base_parameter(read.address), base_parameter(write.address), read.site)
             for read in reads for write in writes if read.site in write.value.sites
             and read.address.constant in (0, 1) and write.address.constant in (0, 1)}
    if len(pairs) != 1:
        return None
    source, destination, read_site = pairs.pop()
    if source is None or destination is None or source == destination or max(source, destination) >= 4:
        return None
    if {a.address.constant for a in reads} != {0, 1} or any(
            base_parameter(a.address) != source for a in reads):
        return None
    if not writes or any(base_parameter(a.address) != destination or a.address.constant not in (0, 1)
                         or (read_site not in a.value.sites and (a.value.terms or a.value.constant != EOS))
                         for a in writes):
        return None
    compares = [c for c in flow.comparisons if read_site in c.left.sites
                and not c.right.terms and c.right.constant == EOS]
    if not compares or not any(not a.value.terms and a.value.constant == EOS for a in writes):
        return None
    if not any(i.mnemonic == 'b' and i.condition == 'ne' and i.target < i.address
               and i.address - 2 in {c.site for c in compares} for i in body):
        return None
    return {'source': source, 'destination': destination, 'kind': 'terminated-byte-copy',
            'read': read_site, 'writes': sorted({a.site for a in writes}),
            'terminator': EOS, 'entry': start}


def dispatch(rom, at):
    """Recognize a bounded word-table branch with an unsigned guard."""
    if at < 14:
        return None
    try:
        compare, guard, shift, pool, add, load, jump = [decode(rom.data, pc) for pc in range(at - 12, at + 2, 2)]
    except ThumbError:
        return None
    if (compare.mnemonic != 'cmp' or compare.immediate is None or compare.immediate > 255
            or guard.mnemonic != 'b' or guard.condition != 'hi'
            or shift.mnemonic != 'lsl' or shift.immediate != 2 or shift.rn != compare.rn
            or pool.literal is None or pool.literal + 4 > len(rom)
            or add.mnemonic != 'add' or add.rn != shift.rd or add.rm != pool.rd
            or load.mnemonic != 'ldr' or load.rn != add.rd or load.immediate != 0
            or jump.mnemonic != 'mov' or jump.rd != 15 or jump.rm != load.rd):
        return None
    base = rom.ptr_at(pool.literal)
    count = compare.immediate + 1
    if base is None or base % 4 or base + count * 4 > len(rom):
        return None
    targets = [rom.ptr_at(base + n * 4) for n in range(count)]
    if any(target is None or target % 2 for target in targets):
        return None
    return {'base': base, 'targets': targets, 'default': guard.target, 'selector': compare.rn}


def expand_loop(rom, start):
    try:
        body = walk(rom.data, start, min(start + 512, len(rom) & ~1), limit=128)
    except ThumbError:
        return None
    jumps = [i for i in body if i.mnemonic == 'mov' and i.rd == 15]
    if len(jumps) != 1:
        return None
    jump = jumps[0]
    table = dispatch(rom, jump.address)
    if table is None or len(table['targets']) != EOS - SCROLL + 1:
        return None
    subtract = decode(rom.data, jump.address - 14)
    if subtract.mnemonic != 'sub' or subtract.immediate != SCROLL or subtract.rd != table['selector']:
        return None
    try:
        prefix = trace(rom, start, jump.address)
    except ThumbError:
        return None
    reads = [a for a in prefix.accesses if not a.write and a.width == 1 and parameter(a.address) is not None]
    if len(reads) != 1:
        return None
    read = reads[0]
    source = parameter(read.address)
    advance = decode(rom.data, read.site + 2)
    byte_read = decode(rom.data, read.site)
    if (advance.mnemonic != 'add' or advance.rd != byte_read.rn or advance.rn != byte_read.rn
            or advance.immediate != 1):
        return None
    default = table['default']
    if not start <= default < start + 512:
        return None
    store, increment, repeat = [decode(rom.data, default + n * 2) for n in range(3)]
    byte = decode(rom.data, read.site)
    if (store.mnemonic != 'strb' or store.rd != byte.rd or store.immediate != 0
            or increment.mnemonic != 'add' or increment.rd != store.rn or increment.rn != store.rn
            or increment.immediate != 1 or repeat.mnemonic != 'b' or repeat.condition
            or repeat.target != read.site):
        return None
    try:
        normal = trace(rom, start, repeat.address + 2)
    except ThumbError:
        return None
    copied = [a for a in normal.accesses if a.site == default and a.write and read.site in a.value.sites]
    destinations = {parameter(a.address) for a in copied if a.address.constant == 0}
    if len(destinations) != 1 or None in destinations:
        return None
    destination = destinations.pop()
    if source == destination or max(source, destination) >= 4:
        return None
    end = table['targets'][EOS - SCROLL]
    if not start <= end < start + 512:
        return None
    eos, put, returned = [decode(rom.data, end + n * 2) for n in range(3)]
    try:
        tail = walk(rom.data, end, min(end + 32, len(rom) & ~1), limit=16)
    except ThumbError:
        return None
    combined = {i.address: i for i in (*body, *tail)}
    combined[jump.address] = replace(jump, mnemonic='b', rd=None, rm=None, target=end)
    if (not returns_to_caller(tuple(combined[pc] for pc in sorted(combined))) or eos.mnemonic != 'mov' or eos.immediate != EOS or put.mnemonic != 'strb'
            or put.rd != eos.rd or put.rn != store.rn or put.immediate != 0
            or returned.mnemonic not in ('mov', 'add') or returned.rd != 0
            or (returned.rm if returned.mnemonic == 'mov' else returned.rn) != store.rn):
        return None
    if table['targets'][PLACEHOLDER - SCROLL] == default or table['targets'][CTRL - SCROLL] == default:
        return None
    return {'source': source, 'destination': destination, 'kind': 'codec-dispatch-byte-expansion',
            'read': read.site, 'write': default, 'dispatch': jump.address,
            'terminator': EOS, 'termination': end, 'entry': start,
            'assumption': 'The ordinary-byte loop establishes a possible source contribution; control-byte paths may transform it.'}


class Transfers:
    def __init__(self, rom):
        self.rom, self.rows, self.pending = rom, {}, set()

    def get(self, start):
        if start in self.rows:
            return self.rows[start]
        if start in self.pending or len(self.pending) >= 4 or not 0 <= start < len(self.rom):
            return None
        self.pending.add(start)
        row = copy_loop(self.rom, start) or expand_loop(self.rom, start)
        if row is None:
            body, reason = returning_body(self.rom, start, span=256, limit=128)
            calls = [i for i in body if i.mnemonic == 'bl']
            if not reason and len(calls) == 1 and not any(i.mnemonic == 'b' for i in body):
                callee = self.get(calls[0].target)
                if callee is not None:
                    try:
                        flow = trace(self.rom, start, max(i.address + i.size for i in body))
                    except ThumbError:
                        flow = None
                    if flow is not None and len(flow.calls) == 1 and all(
                            not a.write or a.address.terms == (("sp", 1),) for a in flow.accesses):
                        call = flow.calls[0]
                        source = parameter(call.arguments[callee['source']])
                        destination = (call.arguments[callee['destination']] if 'destination' in callee
                                       else Expression(callee['destination_constant']))
                        argument = parameter(destination)
                        if source is not None and source < 4 and (argument is not None and argument < 4
                                or not destination.terms and 0x02000000 <= destination.constant < 0x04000000):
                            row = {'source': source, 'kind': 'copy-wrapper', 'entry': start,
                                   'call': call.site, 'callee': callee}
                            row.update({'destination': argument} if argument is not None
                                       else {'destination_constant': destination.constant})
        self.pending.remove(start)
        self.rows[start] = row
        return row
