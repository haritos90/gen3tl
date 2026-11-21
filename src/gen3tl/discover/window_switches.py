"""Bounded compiler switch tables within Thumb consumer functions."""

from ..core.rom import ROM_BASE
from ..core.thumb import ThumbError, decode


def dispatch(data, at, start, end):
    if at - 14 < start:
        return None
    shift, pool, add, load, jump = [decode(data, pc) for pc in range(at - 8, at + 2, 2)]
    if (shift.mnemonic != 'lsl' or shift.immediate != 2
            or pool.literal is None or pool.literal + 4 > len(data)
            or add.mnemonic != 'add' or add.rn != shift.rd or add.rm != pool.rd
            or pool.rd == shift.rd or load.mnemonic != 'ldr'
            or load.rn != add.rd or load.immediate != 0
            or jump.mnemonic != 'mov' or jump.rd != 15 or jump.rm != load.rd):
        return None
    compare, guard = [decode(data, pc) for pc in (at - 12, at - 10)]
    if guard.mnemonic == 'b' and guard.condition is None:
        compare, guard = [decode(data, pc) for pc in (at - 14, at - 12)]
        if guard.condition != 'ls' or guard.target != shift.address:
            return None
        default = decode(data, at - 10).target
    elif guard.mnemonic == 'b' and guard.condition == 'hi':
        default = guard.target
    else:
        return None
    if (compare.mnemonic != 'cmp' or compare.immediate is None
            or compare.rn != shift.rn or not start <= default < end):
        return None
    base = int.from_bytes(data[pool.literal:pool.literal + 4], 'little') - ROM_BASE
    count = compare.immediate + 1
    if base % 4 or not start <= base < base + count * 4 <= end:
        return None
    targets = tuple(int.from_bytes(data[base + n * 4:base + n * 4 + 4], 'little') - ROM_BASE
                    for n in range(count))
    if any(target % 2 or not start <= target < end
           or base <= target < base + count * 4 for target in targets):
        return None
    return {'base': base, 'targets': targets, 'default': default, 'selector': compare.rn,
            'guard': guard.address, 'compare': compare.address}


def successors(data, instruction, start, end):
    if instruction.mnemonic == 'mov' and instruction.rd == 15:
        table = dispatch(data, instruction.address, start, end)
        if table is not None:
            return tuple(sorted(set(table['targets'])))
    return instruction.successors


def branches(data, body, start, end):
    result = []
    for instruction in body:
        if instruction.mnemonic == 'b':
            result.append([instruction.address - start, instruction.target - start])
        elif instruction.mnemonic == 'mov' and instruction.rd == 15:
            table = dispatch(data, instruction.address, start, end)
            if table is not None:
                result.extend([instruction.address - start, target - start] for target in table['targets'])
    return result


def walk(data, start, end, limit=4096):
    """Exclude table words and require every branch inside the extent."""
    if start < 0 or start % 2 or end > len(data) or end < start or end % 2:
        raise ThumbError('Invalid switch walk bounds.')
    todo, found, tables = [start], {}, set()
    while todo:
        pc = todo.pop()
        if pc in found:
            continue
        if not start <= pc < end or len(found) >= limit:
            raise ThumbError('Switch walk leaves its function extent.')
        instruction = decode(data, pc)
        if pc + instruction.size > end:
            raise ThumbError('Switch instruction crosses its function extent.')
        found[pc] = instruction
        if instruction.mnemonic == 'mov' and instruction.rd == 15:
            table = dispatch(data, pc, start, end)
            if table is not None:
                tables.update(range(table['base'], table['base'] + len(table['targets']) * 4))
        todo.extend(reversed(successors(data, instruction, start, end)))
    if any(tables.intersection(range(i.address, i.address + i.size)) for i in found.values()):
        raise ThumbError('Switch table overlaps executable instructions.')
    return tuple(found[pc] for pc in sorted(found))
