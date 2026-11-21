"""Bounded argument and stack flow for text printer summaries."""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..core.rom import ROM_BASE
from ..core.thumb import ThumbError, decode
from .window_switches import successors
from .window_merge import Joins
from .accessors import Call, Comparison, Expression, Literal, MemoryAccess, aligned


@dataclass(frozen=True)
class Buffered:
    source: Expression
    copies: tuple


@dataclass
class Flow:
    calls: tuple
    accesses: tuple
    loads: dict
    stops: tuple
    frames: dict
    comparisons: tuple
    redirects: dict
    sources: dict
    contents: dict
    returned: tuple


def parameter(value):
    if value.constant or len(value.terms) != 1 or value.terms[0][1] != 1:
        return None
    name = value.terms[0][0]
    return int(name[1:]) if name.startswith("a") and name[1:].isdigit() else None


def trace(rom, start, end, limit=8192, loop_indices=None, transfers=None, returns=None, getters=None, merge=False):
    """Trace arguments with optional finite joins at block entries."""
    initial = tuple(Expression.symbol("sp" if r == 13 else f"a{r}") for r in range(16))
    todo = [(start, initial, (), frozenset(), (), False)]
    joins, leaders = (Joins() if merge else None), {start}
    if merge:
        limit *= 4
    seen, calls, accesses, stops = set(), set(), set(), set()
    loads, frames, comparisons, redirects, sources, contents = {}, {}, set(), {}, {}, {}
    returned = set()
    while todo:
        pc, previous, saved, backedges, content, joined = todo.pop()
        if not start <= pc < end:
            continue
        if loop_indices and pc in loop_indices:
            values = list(previous)
            values[loop_indices[pc]] = Expression.symbol(f"index@{pc:x}")
            previous = tuple(values)
        if joins is not None and pc in leaders and not joined:
            for registers, memory, buffers in joins.enter(pc, previous, saved, content):
                todo.append((pc, registers, memory, frozenset(), buffers, True))
            continue
        state = pc, previous, saved, backedges, content
        if state in seen:
            continue
        if len(seen) >= limit:
            raise ThumbError("Window argument flow exceeds its state bound.")
        seen.add(state)
        i = decode(rom.data, pc)
        if pc + i.size > end:
            raise ThumbError("Window instruction crosses its function bound.")
        regs, memory, buffers = list(previous), dict(saved), dict(content)
        op, rd = i.mnemonic, i.rd
        alternatives = ()
        regs[15] = Expression(ROM_BASE + pc + 4)

        def unknown(label="value"):
            return Expression.symbol(f"{label}@{pc:x}").at(pc)

        def operand():
            return regs[i.rm] if i.rm is not None else Expression(i.immediate or 0)

        def load(at, width, signed=False):
            key = at.key, width
            if not at.terms and not aligned(at, width):
                value = unknown("unaligned")
            elif key in memory:
                value = memory[key]
            elif any(old[1] == at.terms and old[0] <= at.constant
                     and at.constant + width <= old[0] + size and not value.terms
                     for (old, size), value in memory.items()):
                old, size, value = next((old, size, value) for (old, size), value in memory.items()
                    if old[1] == at.terms and old[0] <= at.constant
                    and at.constant + width <= old[0] + size and not value.terms)
                number = value.constant >> (8 * (at.constant - old[0])) & ((1 << (8 * width)) - 1)
                value = Expression(number)
            elif not at.terms and ROM_BASE <= at.constant <= ROM_BASE + len(rom) - width:
                offset = at.constant - ROM_BASE
                number = int.from_bytes(rom.data[offset:offset + width], "little", signed=signed)
                origins = (Literal(pc, offset, number, rd),) if width == 4 else ()
                value = Expression(number, origins=origins, sites=at.sites)
            elif at.terms == (("sp", 1),) and at.constant < 32 and at.constant % 4 == 0 and width == 4:
                value = Expression.symbol(f"a{4 + at.constant // 4}")
            else:
                name = f"load{width}@{pc:x}:{at.key!r}"
                value = Expression.symbol(name)
                loads[name] = (at, width, signed, pc)
            accesses.add(MemoryAccess(pc, at, width, signed, value))
            return value.at(pc)

        def store(at, width, value):
            for key in tuple(buffers):
                constant, terms = key
                separate = (not at.terms and not terms
                            and at.constant >> 24 in (2, 3) and constant >> 24 in (2, 3)
                            and at.constant >> 24 != constant >> 24)
                store_offset = at.constant - (1 << 32) if at.terms and at.constant & (1 << 31) else at.constant
                buffer_offset = constant - (1 << 32) if terms and constant & (1 << 31) else constant
                before = at.terms == terms and store_offset + width <= buffer_offset
                if not separate and not before and (at.terms != (("sp", 1),) or terms == (("sp", 1),)):
                    del buffers[key]
            accesses.add(MemoryAccess(pc, at, width, False, value, True))
            if not aligned(at, width):
                memory.clear()
                return
            for old, old_width in tuple(memory):
                constant, terms = old
                if terms != at.terms or not (constant + old_width <= at.constant or at.constant + width <= constant):
                    del memory[old, old_width]
            if width < 4:
                value = narrow(value, width * 8)
            memory[at.key, width] = value

        def narrow(value, bits):
            mask = (1 << bits) - 1
            if not value.terms:
                return replace(value, constant=value.constant & mask)
            name = f"u{bits}:{value.key!r}"
            loads[name] = (value, 0, False, pc)
            return Expression.symbol(name).at(pc)

        if op == "cmp":
            comparisons.add(Comparison(pc, op, regs[i.rn], operand()))
        if i.literal is not None:
            if i.literal + 4 > len(rom):
                raise ThumbError("Window literal leaves the ROM.")
            literal = Literal(pc, i.literal, rom.u32(i.literal), rd)
            regs[rd] = Expression(literal.value, origins=(literal,)).at(pc)
        elif op in ("ldr", "ldrb", "ldrh", "ldrsb", "ldrsh", "str", "strb", "strh"):
            at = regs[i.rn].add(operand())
            if op.startswith("ldr"):
                regs[rd] = load(at, i.width, i.signed)
            else:
                store(at, i.width, regs[rd])
        elif op == "mov":
            regs[rd] = operand().at(pc)
        elif op in ("add", "sub"):
            left = regs[i.rn]
            if i.rn == 15 and i.immediate is not None:
                left = Expression((ROM_BASE + pc + 4) & ~3)
            regs[rd] = left.add(operand(), -1 if op == "sub" else 1).at(pc)
        elif op == "lsl" and not operand().terms and operand().constant < 32:
            regs[rd] = regs[i.rn].multiply(1 << operand().constant).at(pc)
        elif op in ("lsr", "asr") and not operand().terms:
            value, shift = regs[i.rn], operand().constant
            if not value.terms:
                number = value.constant
                if op == "asr" and number & 0x80000000:
                    number -= 0x100000000
                regs[rd] = Expression(number >> shift).at(pc)
            elif shift < 32 and value.constant % (1 << shift) == 0 and all(
                    coefficient % (1 << shift) == 0 for _, coefficient in value.terms):
                reduced = replace(value, constant=value.constant >> shift,
                                  terms=tuple((name, coefficient >> shift) for name, coefficient in value.terms))
                regs[rd] = narrow(reduced, 32 - shift) if op == "lsr" else unknown("signed")
            else:
                regs[rd] = unknown(op)
        elif op == "and" and (not regs[i.rn].terms or not operand().terms):
            left, right = regs[i.rn], operand()
            value, mask = (left, right.constant) if not right.terms else (right, left.constant)
            regs[rd] = narrow(value, mask.bit_length()) if mask & (mask + 1) == 0 else unknown(op)
        elif op == "mul" and (not regs[i.rn].terms or not operand().terms):
            left, right = regs[i.rn], operand()
            regs[rd] = (left.multiply(right.constant) if not right.terms else right.multiply(left.constant)).at(pc)
        elif op in ("ldmia", "stmia"):
            base = regs[i.rn]
            for index, reg in enumerate(i.registers):
                at = base.add(Expression(4 * index))
                if op == "ldmia":
                    regs[reg] = load(at, 4)
                else:
                    store(at, 4, regs[reg])
            if op == "stmia" or i.rn not in i.registers:
                regs[i.rn] = base.add(Expression(4 * len(i.registers)))
        elif op in ("push", "pop"):
            if op == "push":
                regs[13] = regs[13].add(Expression(-4 * len(i.registers)))
            for index, reg in enumerate(i.registers):
                at = regs[13].add(Expression(4 * index))
                if op == "push":
                    store(at, 4, regs[reg])
                else:
                    regs[reg] = load(at, 4)
            if op == "pop":
                regs[13] = regs[13].add(Expression(4 * len(i.registers)))
        elif op in ("bl", "swi"):
            if op == "bl":
                args = tuple(regs[:4]) + tuple(load(regs[13].add(Expression(4 * n)), 4) for n in range(8))
                target, redirect = i.target, None
                if 0 <= target <= len(rom) - 2:
                    thunk = decode(rom.data, target)
                    if thunk.literal is not None and thunk.literal + 4 <= len(rom) and target + 4 <= len(rom):
                        branch = decode(rom.data, target + 2)
                        value = rom.u32(thunk.literal)
                        if (branch.mnemonic == 'bx' and branch.rm == thunk.rd and value & 1
                                and ROM_BASE <= value < ROM_BASE + len(rom)):
                            redirect = {'thunk': target, 'register': thunk.rd,
                                        'loads': [target], 'pools': [thunk.literal]}
                            if thunk.rd < 4:
                                args = tuple(Expression(value) if n == thunk.rd else arg for n, arg in enumerate(args))
                            target = (value - ROM_BASE) & ~1
                    if thunk.mnemonic == "bx" and thunk.rm < 13:
                        value = regs[thunk.rm]
                        if (not value.terms and value.constant & 1
                                and ROM_BASE <= value.constant < ROM_BASE + len(rom)):
                            redirect = {"thunk": target, "register": thunk.rm,
                                        "loads": [origin.site for origin in value.origins],
                                        "pools": [origin.pool for origin in value.origins]}
                            target = (value.constant - ROM_BASE) & ~1
                call = Call(pc, target, args)
                if redirect is not None:
                    redirects[call] = redirect
                calls.add(call)
                frames[call] = dict(memory)
                for key, value in buffers.items():
                    contents.setdefault(call, {}).setdefault(key, set()).add(value)
                for n, value in enumerate(args):
                    if value.key in buffers:
                        sources.setdefault(call, {}).setdefault(n, set()).add(buffers[value.key])
                transfer = transfers.get(target) if transfers is not None else None
                carried = None
                if transfer is not None:
                    source = args[transfer["source"]]
                    earlier = buffers.get(source.key)
                    carried = Buffered(earlier.source if earlier else source,
                                       (*(earlier.copies if earlier else ()), (pc, target)))
                buffers = {key: value for key, value in buffers.items()
                           if all(argument.key != key for argument in args)}
                if transfer is not None:
                    destination = (args[transfer["destination"]] if "destination" in transfer
                                   else Expression(transfer["destination_constant"]))
                    buffers[destination.key] = carried
            escaped = any(any(name == "sp" for name, _ in value.terms) for value in (args if op == "bl" else regs[:4]))
            memory = {key: value for key, value in memory.items()
                      if key[0][1] == (("sp", 1),) and not escaped}
            for reg in (0, 1, 2, 3, 12, 14):
                regs[reg] = unknown(f"return{reg}")
            if op == "bl" and getters is not None:
                alternatives = getters.resolve(call, loads, accesses)
                if alternatives:
                    regs[0] = alternatives[0]
            if op == "bl" and returns and (pc, target) in returns:
                regs[0] = returns[pc, target].at(pc)
        elif op not in ("cmp", "cmn", "tst", "b", "bx", "undefined"):
            for reg in i.written:
                regs[reg] = unknown(op)
            if op == "stmia":
                memory.clear()
        if op in ("bx", "undefined") or 15 in i.written:
            stops.add((pc, op))
            if op == "bx" or op == "pop" and 15 in i.registers:
                returned.add((pc, regs[0]))
        saved = tuple(sorted(memory.items(), key=repr))
        following_sites = successors(rom.data, i, start, end)
        if joins is not None and (op == 'b' or len(following_sites) > 1):
            leaders.update(following_sites)
        for successor in reversed(following_sites):
            edge = (pc, successor)
            if joins is None and successor <= pc and edge in backedges:
                stops.add((pc, "loop-bound"))
                continue
            for value in alternatives or (regs[0],):
                following = list(regs)
                following[0] = value
                todo.append((successor, tuple(following), saved,
                             (backedges | {edge} if successor <= pc else backedges) if joins is None else frozenset(),
                             tuple(sorted(buffers.items(), key=repr)), False))
    if joins is not None:
        stops.update((pc, 'merge-widen') for pc, _ in joins.widened)
    return Flow(tuple(sorted(calls, key=repr)), tuple(sorted(accesses, key=repr)), loads, tuple(sorted(stops)), frames, tuple(sorted(comparisons, key=repr)), redirects, sources, contents, tuple(sorted(returned, key=repr)))


def describe(value, loads, depth=0):
    """Expose parameter, constant, field and narrowing expressions."""
    if depth > 12:
        return {"unknown": "expression-depth"}
    if not value.terms:
        return {"constant": value.constant}
    arg = parameter(value)
    if arg is not None:
        return {"parameter": arg}
    if value.constant == 0 and len(value.terms) == 1 and value.terms[0][1] == 1:
        name = value.terms[0][0]
        if name in loads:
            at, width, signed, site = loads[name]
            if width == 0:
                return {"narrow": int(name.split(":", 1)[0][1:]), "value": describe(at, loads, depth + 1)}
            return {"load": width, "address": describe(at, loads, depth + 1), "site": site}
    return {"sum": [{"constant": value.constant}, *[
        {"scale": coefficient, "value": describe(Expression.symbol(name), loads, depth + 1)
         if name in loads or name.startswith("a") and name[1:].isdigit() else {"unknown": name}}
        for name, coefficient in value.terms]]}
