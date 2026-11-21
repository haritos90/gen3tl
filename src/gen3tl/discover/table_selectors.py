"""Bounded evaluation of native register-only slot selector tails."""

from __future__ import annotations

from collections import Counter

from ..core.thumb import ThumbError, decode

MASK = 0xFFFFFFFF
RETURN = object()


def _condition(name, flags):
    if flags is None:
        raise ThumbError("selector branch uses unknown flags")
    negative, zero, carry, overflow = flags
    return {"eq": zero, "ne": not zero, "cs": carry, "cc": not carry,
            "mi": negative, "pl": not negative, "vs": overflow, "vc": not overflow,
            "hi": carry and not zero, "ls": not carry or zero,
            "ge": negative == overflow, "lt": negative != overflow,
            "gt": not zero and negative == overflow, "le": zero or negative != overflow}[name]


def _subtract(left, right):
    value = (left - right) & MASK
    return value, (bool(value & (1 << 31)), value == 0, left >= right,
                   bool((left ^ right) & (left ^ value) & (1 << 31)))


def evaluate(data, start, end, sample_site, register, sample, inputs=None, limit=256):
    """Enumerate a remainder input after the validated normalization prefix."""
    registers, stack = [None] * 16, []
    registers[14] = RETURN
    for index, value in (inputs or {}).items():
        registers[int(index)] = value
    at, sampled, flags, visited = start, False, None, []
    for _ in range(limit):
        if not start <= at < end:
            raise ThumbError("selector leaves its verified code span")
        if at == sample_site and not sampled:
            registers[register], sampled = sample, True
        instruction = decode(data, at)
        visited.append(at)
        op, rd, rn, rm, immediate = (instruction.mnemonic, instruction.rd, instruction.rn,
                                    instruction.rm, instruction.immediate)
        following = at + instruction.size
        if op == "push":
            stack = [registers[index] for index in instruction.registers] + stack
        elif op == "pop":
            if len(stack) < len(instruction.registers):
                raise ThumbError("selector restores an unknown stack")
            for index in instruction.registers:
                registers[index] = stack.pop(0)
            if 15 in instruction.registers:
                if registers[15] is RETURN and sampled and type(registers[0]) is int:
                    return registers[0], tuple(visited)
                raise ThumbError("selector restores an unsupported program counter")
        elif op == "bx":
            if registers[rm] is RETURN and sampled and type(registers[0]) is int:
                return registers[0], tuple(visited)
            raise ThumbError("selector has an unsupported indirect transfer")
        elif op == "bl" and not sampled:
            for index in (0, 1, 2, 3, 12, 14):
                registers[index] = None
            flags = None
        elif op == "b":
            if instruction.condition is None or _condition(instruction.condition, flags):
                following = instruction.target
        elif op in ("mov", "add", "sub", "cmp", "lsl", "lsr", "asr"):
            right = registers[rm] if rm is not None else immediate
            left = registers[rn] if rn is not None else None
            if op == "mov":
                value = right
                flags = None
            elif type(left) is not int or type(right) is not int:
                value = None
                flags = None
            elif op in ("sub", "cmp"):
                value, flags = _subtract(left, right)
            elif op == "add":
                value = (left + right) & MASK
                flags = (bool(value & (1 << 31)), value == 0, left + right > MASK,
                         bool(~(left ^ right) & (left ^ value) & (1 << 31)))
            elif op == "lsl":
                value = (left << right) & MASK
                flags = None
            elif op == "lsr":
                value = left >> right
                flags = None
            else:
                value = ((left - (1 << 32) if left & (1 << 31) else left) >> right) & MASK
                flags = None
            if op == "cmp":
                if value is None:
                    raise ThumbError("selector compares an unknown value")
            else:
                registers[rd] = value
        else:
            raise ThumbError(f"unsupported selector operation {op}")
        at = following
    raise ThumbError("selector instruction limit reached")


def distribution(data, start, end, sample_site, register, inputs=None):
    values, sites = [], set()
    for residue in range(100):
        value, visited = evaluate(data, start, end, sample_site, register, residue, inputs)
        if not 0 <= value < 32:
            raise ThumbError("selector result is outside supported slot bounds")
        values.append(value)
        sites.update(visited)
    counts = Counter(values)
    first, last = min(counts), max(counts)
    if set(counts) != set(range(first, last + 1)):
        raise ThumbError("selector slots are not consecutive")
    return first, [counts[index] for index in range(first, last + 1)], sorted(sites)
