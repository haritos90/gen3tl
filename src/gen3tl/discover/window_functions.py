"""Returning Thumb entry points and their literal-pool ownership."""

from __future__ import annotations

from collections import defaultdict

from ..core.thumb import ThumbError, decode
from .scan import pointer_index
from .window_switches import successors, walk


_cached = None


def function_index(rom):
    """Share immutable ownership across analyses of identical bytes."""
    global _cached
    identity = rom.sha1()
    if _cached is None or _cached[0] != identity:
        _cached = identity, Functions(rom.copy())
    return _cached[1]


def returns_to_caller(body, limit=32768, edges=None):
    instructions = {i.address: i for i in body}
    registers = tuple(n == 14 for n in range(16))
    todo, seen = [(body[0].address, registers, 0, ())], set()
    while todo:
        state = todo.pop()
        if state in seen:
            continue
        if len(seen) >= limit:
            return False
        seen.add(state)
        pc, previous, sp, saved = state
        i = instructions[pc]
        regs, stack = list(previous), dict(saved)
        if i.mnemonic == "bx":
            if not regs[i.rm] or sp != 0:
                return False
            continue
        if i.mnemonic == "push":
            sp -= 4 * len(i.registers)
            for n, reg in enumerate(i.registers):
                stack[sp + n * 4] = regs[reg]
        elif i.mnemonic == "pop":
            for n, reg in enumerate(i.registers):
                regs[reg] = stack.pop(sp + n * 4, False)
            sp += 4 * len(i.registers)
            if 15 in i.registers and (not regs[15] or sp != 0):
                return False
        elif i.rd == 13:
            if i.mnemonic not in ("add", "sub") or i.rn != 13 or i.immediate is None:
                return False
            sp += i.immediate * (-1 if i.mnemonic == "sub" else 1)
        elif i.mnemonic == "str" and i.rn == 13 and i.immediate is not None:
            stack[sp + i.immediate] = regs[i.rd]
        elif i.mnemonic == "ldr" and i.rn == 13 and i.immediate is not None:
            regs[i.rd] = stack.get(sp + i.immediate, False)
        elif i.mnemonic == "mov":
            regs[i.rd] = regs[i.rm] if i.rm is not None else False
        elif i.mnemonic == "add" and i.immediate == 0:
            regs[i.rd] = regs[i.rn]
        elif i.mnemonic == "bl":
            for reg in (0, 1, 2, 3, 12, 14):
                regs[reg] = False
        else:
            for reg in i.written:
                regs[reg] = False
        if not -65536 <= sp <= 0:
            return False
        todo.extend((next_pc, tuple(regs), sp, tuple(sorted(stack.items()))) for next_pc in (edges.get(pc, i.successors) if edges else i.successors))
    return True


def returning_body(rom, start, span=65536, limit=4096):
    """Require decoded paths and returns within one bounded body."""
    end = min(len(rom) & ~1, start + span)
    if start < 0 or start % 2 or start >= end:
        return (), "Invalid Thumb entry."
    try:
        body = walk(rom.data, start, end, limit)
    except ThumbError as exc:
        return (), str(exc)
    if any(i.mnemonic == "undefined" for i in body):
        return (), "Function contains an invalid instruction."
    found = {i.address: i for i in body}
    edges = {i.address: successors(rom.data, i, start, end) for i in body}
    returns = [i for i in body if not edges[i.address]
               and (i.mnemonic == "bx" or 15 in i.written)]
    if not returns:
        return (), "Function has no return."
    popped = {i.address + i.size: i.registers for i in found.values() if i.mnemonic == "pop"}
    for instruction in returns:
        if instruction.mnemonic == "pop" and 15 in instruction.registers:
            continue
        if instruction.mnemonic == "bx" and (instruction.rm == 14
                or instruction.rm in popped.get(instruction.address, ())):
            continue
        return (), "Function has an unresolved indirect exit."
    occupied = set()
    for i in found.values():
        covered = set(range(i.address, i.address + i.size))
        if occupied & covered:
            return (), "Function has overlapping instructions."
        occupied.update(covered)
    if any(i.literal is not None and (i.literal + 4 > len(rom)
           or occupied.intersection(range(i.literal, i.literal + 4))) for i in found.values()):
        return (), "Function literal overlaps code or leaves the ROM."
    body = tuple(found[pc] for pc in sorted(found))
    if not returns_to_caller(body, edges=edges):
        return (), "Function does not restore its entry stack and return address."
    return body, None


class Functions:
    """Index pointer entries, called functions and their actual loads."""

    def __init__(self, rom):
        self.rom = rom
        self.bodies, self.rejected, self.sources = {}, {}, defaultdict(set)
        self.owners, self.pools = defaultdict(list), defaultdict(list)
        self.ready = False

    def derive(self):
        if self.ready:
            return
        self.ready = True
        for target, sites in pointer_index(self.rom).items():
            if target & 1:
                self.sources[target & ~1].update(("pointer", site) for site in sites)
        todo = sorted(self.sources, reverse=True)
        while todo:
            start = todo.pop()
            if start in self.bodies or start in self.rejected:
                continue
            body, reason = returning_body(self.rom, start)
            if reason:
                self.rejected[start] = reason
                continue
            self.bodies[start] = body
            for i in body:
                self.owners[i.address].append(start)
                if i.literal is not None:
                    self.pools[i.literal].append((start, i))
                if i.mnemonic == "bl" and 0 <= i.target < len(self.rom):
                    self.sources[i.target].add(("call", i.address))
                    todo.append(i.target)

    def instruction(self, position):
        self.derive()
        for start in sorted(self.owners.get(position, ()), reverse=True):
            body = self.bodies[start]
            covering = [i for i in body if i.address < position + 4 and i.address + i.size > position]
            covered = {at for i in covering for at in range(i.address, i.address + i.size)}
            if set(range(position, position + 4)) <= covered:
                return {"function": start, "method": "thumb-entry+control-flow+return",
                        "entries": sorted(self.sources[start]),
                        "assumption": "A pointer entry with decoded returning paths identifies executable bytes."}
        return None

    def literal(self, position):
        self.derive()
        loads = []
        for start, i in self.pools.get(position, ()):
            loads.append({"load": i.address, "register": i.rd, "function": start,
                          "method": "thumb-entry+control-flow+literal-load",
                          "entries": sorted(self.sources[start])})
        return {"pool": position, "loads": loads, "method": "returning-entry+literal-load"} if loads else None
