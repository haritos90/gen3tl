"""ARMv4T Thumb instructions, with ROM-relative code and pool addresses."""

from __future__ import annotations

from dataclasses import dataclass


class ThumbError(ValueError):
    pass


CONDITIONS = ("eq", "ne", "cs", "cc", "mi", "pl", "vs", "vc", "hi", "ls", "ge", "lt", "gt", "le")
ALU = ("and", "eor", "lsl", "lsr", "asr", "adc", "sbc", "ror", "tst", "neg", "cmp", "cmn", "orr", "mul", "bic", "mvn")
MEMORY = ("str", "strh", "strb", "ldrsb", "ldr", "ldrh", "ldrb", "ldrsh")


def signed(value: int, bits: int) -> int:
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


@dataclass(frozen=True)
class Instruction:
    address: int
    size: int
    mnemonic: str
    raw: tuple[int, ...]
    rd: int | None = None
    rn: int | None = None
    rm: int | None = None
    immediate: int | None = None
    registers: tuple[int, ...] = ()
    width: int = 0
    signed: bool = False
    condition: str | None = None
    target: int | None = None
    literal: int | None = None

    @property
    def written(self) -> tuple[int, ...]:
        op = self.mnemonic
        if op == "bl":
            return (14,)
        if op == "push":
            return (13,)
        if op == "pop":
            return (*self.registers, 13)
        if op == "ldmia":
            return tuple(sorted(set(self.registers) | {self.rn}))
        if op == "stmia":
            return (self.rn,)
        if op.startswith("str") or op in ("cmp", "cmn", "tst", "b", "bx", "swi", "undefined"):
            return ()
        return (self.rd,) if self.rd is not None else ()

    @property
    def successors(self) -> tuple[int, ...]:
        following = self.address + self.size
        if self.mnemonic in ("undefined", "bx") or 15 in self.written:
            return ()
        if self.mnemonic == "b":
            return (following, self.target) if self.condition else (self.target,)
        return (following,)


def decode(data: bytes | bytearray, address: int) -> Instruction:
    """Decode one instruction; reject misalignment and truncated operands."""
    if address < 0 or address % 2 or address + 2 > len(data):
        raise ThumbError(f"invalid Thumb address {address:#x}")
    h = int.from_bytes(data[address:address + 2], "little")

    def ins(op, **fields):
        return Instruction(address, 2, op, (h,), **fields)

    rd, rn, rm = h & 7, h >> 3 & 7, h >> 6 & 7
    if h & 0xF800 == 0x1800:
        return ins("sub" if h & 0x200 else "add", rd=rd, rn=rn,
                   **({"immediate": rm} if h & 0x400 else {"rm": rm}))
    if h & 0xE000 == 0:
        op = ("lsl", "lsr", "asr")[h >> 11]
        shift = h >> 6 & 31
        return ins(op, rd=rd, rn=rn, immediate=shift or (0 if op == "lsl" else 32))
    if h & 0xE000 == 0x2000:
        op = ("mov", "cmp", "add", "sub")[h >> 11 & 3]
        reg = h >> 8 & 7
        return ins(op, rd=reg, rn=None if op == "mov" else reg, immediate=h & 255)
    if h & 0xFC00 == 0x4000:
        return ins(ALU[h >> 6 & 15], rd=rd, rn=rd, rm=rn)
    if h & 0xFC00 == 0x4400:
        op = ("add", "cmp", "mov", "bx")[h >> 8 & 3]
        reg = (h & 7) | (h >> 4 & 8)
        if op == "bx":
            return ins(op, rm=h >> 3 & 15) if h & 0x87 == 0 else ins("undefined")
        return ins(op, rd=reg, rn=None if op == "mov" else reg, rm=h >> 3 & 15)
    if h & 0xF800 == 0x4800:
        imm = (h & 255) * 4
        return ins("ldr", rd=h >> 8 & 7, rn=15, immediate=imm, width=4,
                   literal=((address + 4) & ~3) + imm)
    if h & 0xF000 == 0x5000:
        op = h >> 9 & 7
        return ins(MEMORY[op], rd=rd, rn=rn, rm=rm, width=(4, 2, 1, 1, 4, 2, 1, 2)[op],
                   signed=op in (3, 7))
    if h & 0xE000 == 0x6000:
        width = 1 if h & 0x1000 else 4
        return ins(("ldr" if h & 0x800 else "str") + ("b" if width == 1 else ""),
                   rd=rd, rn=rn, immediate=(h >> 6 & 31) * width, width=width)
    if h & 0xF000 == 0x8000:
        return ins("ldrh" if h & 0x800 else "strh", rd=rd, rn=rn, immediate=(h >> 6 & 31) * 2, width=2)
    if h & 0xF000 == 0x9000:
        return ins("ldr" if h & 0x800 else "str", rd=h >> 8 & 7, rn=13, immediate=(h & 255) * 4, width=4)
    if h & 0xF000 == 0xA000:
        return ins("add", rd=h >> 8 & 7, rn=13 if h & 0x800 else 15, immediate=(h & 255) * 4)
    if h & 0xFF00 == 0xB000:
        return ins("sub" if h & 0x80 else "add", rd=13, rn=13, immediate=(h & 127) * 4)
    if h & 0xF600 == 0xB400:
        pop = bool(h & 0x800)
        registers = tuple(i for i in range(8) if h & (1 << i))
        if h & 0x100:
            registers += (15 if pop else 14,)
        if not registers:
            return ins("undefined")
        return ins("pop" if pop else "push", rn=13, registers=registers)
    if h & 0xF000 == 0xC000:
        if not h & 255:
            return ins("undefined")
        return ins("ldmia" if h & 0x800 else "stmia", rn=h >> 8 & 7,
                   registers=tuple(i for i in range(8) if h & (1 << i)), width=4)
    if h & 0xF000 == 0xD000:
        cond = h >> 8 & 15
        if cond == 15:
            return ins("swi", immediate=h & 255)
        if cond == 14:
            return ins("undefined")
        return ins("b", condition=CONDITIONS[cond], target=address + 4 + signed(h & 255, 8) * 2)
    if h & 0xF800 == 0xE000:
        return ins("b", target=address + 4 + signed(h & 0x7FF, 11) * 2)
    if h & 0xF800 == 0xF000:
        if address + 4 > len(data):
            raise ThumbError(f"truncated Thumb BL at {address:#x}")
        low = int.from_bytes(data[address + 2:address + 4], "little")
        if low & 0xF800 == 0xF800:
            offset = signed((h & 0x7FF) << 12 | (low & 0x7FF) << 1, 23)
            return Instruction(address, 4, "bl", (h, low), target=address + 4 + offset)
    return ins("undefined")


def disassemble(data: bytes | bytearray, start: int, end: int) -> tuple[Instruction, ...]:
    """Decode an explicit code span; literal pools are not excluded."""
    if start < 0 or start % 2 or end < start or end > len(data) or end % 2:
        raise ThumbError("invalid Thumb span")
    result = []
    while start < end:
        instruction = decode(data, start)
        if start + instruction.size > end:
            raise ThumbError(f"instruction crosses span at {start:#x}")
        result.append(instruction)
        start += instruction.size
    return tuple(result)


def walk(data: bytes | bytearray, start: int, end: int, limit: int = 4096) -> tuple[Instruction, ...]:
    """Follow local branches, not callees or unresolved indirect transfers."""
    if start < 0 or start % 2 or end > len(data) or end < start or end % 2 or limit < 1:
        raise ThumbError("invalid Thumb walk bounds")
    todo, found = [start], {}
    while todo:
        address = todo.pop()
        if address in found or not start <= address < end:
            continue
        if len(found) >= limit:
            raise ThumbError("Thumb walk limit reached")
        instruction = decode(data, address)
        if address + instruction.size > end:
            raise ThumbError(f"instruction crosses span at {address:#x}")
        found[address] = instruction
        todo.extend(reversed(instruction.successors))
    return tuple(found[address] for address in sorted(found))
