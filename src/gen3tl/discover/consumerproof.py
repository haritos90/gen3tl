"""Bounded path-sensitive BIOS consumer proofs for graphics discovery."""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..core import thumb
from ..core.graphics import GraphicsError, lz77_decompress
from ..core.rom import ROM_BASE, Rom, rom_offset
from .accessors import Expression, Literal, aligned

FUNCTION_SPAN = 0x100
MAX_CALL_DEPTH = 4
MAX_STATES = 20000
MAX_PATHS = 256
VRAM = (0x06000000, 0x06018000)
PALETTE_RAM = (0x05000000, 0x05000400)
CPU_SET = (0x0B, 0x0C)
LZ77 = (0x11, 0x12)
_HIGH_OPERATIONS, _HIGH_BX = 0x4400, 0x4700


@dataclass(frozen=True)
class Value:
    expression: Expression
    low: int = 0
    high: int = 0xFFFFFFFF

    @classmethod
    def known(cls, value: int, origins: tuple[Literal, ...] = (), site: int | None = None) -> Value:
        expression = Expression(value, origins=origins)
        if site is not None:
            expression = expression.at(site)
        return cls(expression, value & 0xFFFFFFFF, value & 0xFFFFFFFF)

    @classmethod
    def unknown(cls, name: str, site: int) -> Value:
        return cls(Expression.symbol(name).at(site))

    @property
    def constant(self) -> int | None:
        return self.expression.constant if not self.expression.terms else None

    def at(self, site: int) -> Value:
        return replace(self, expression=self.expression.at(site))

    def add(self, other: Value, scale: int = 1, site: int | None = None) -> Value:
        expression = self.expression.add(other.expression, scale)
        if scale >= 0:
            low, high = self.low + other.low * scale, self.high + other.high * scale
        else:
            low, high = self.low + other.high * scale, self.high + other.low * scale
        if low < 0 or high > 0xFFFFFFFF or low > high:
            low, high = 0, 0xFFFFFFFF
        value = Value(expression, low, high)
        return value.at(site) if site is not None else value

    def multiply(self, scale: int, site: int | None = None) -> Value:
        expression = self.expression.multiply(scale)
        low, high = self.low * scale, self.high * scale
        if scale < 0 or high > 0xFFFFFFFF:
            low, high = 0, 0xFFFFFFFF
        value = Value(expression, low, high)
        return value.at(site) if site is not None else value

    def shift_right(self, shift: int, site: int) -> Value:
        constant = self.constant
        if constant is not None:
            return Value.known(constant >> shift, self.expression.origins, site)
        expression = Expression.symbol(f"lsr@{site:x}").at(site)
        return Value(expression, self.low >> shift, self.high >> shift)

    def mask(self, mask: int, site: int) -> Value:
        constant = self.constant
        if constant is not None:
            return Value.known(constant & mask, self.expression.origins, site)
        return Value(Expression.symbol(f"and@{site:x}").at(site), 0, mask)


@dataclass(frozen=True)
class ConsumerEvent:
    swi: int
    site: int
    chain: tuple[int, ...]
    source: Value
    destination: Value
    control: Value

    @property
    def root_call(self) -> int:
        return self.chain[0]


@dataclass(frozen=True)
class MemoryEffect:
    event_index: int
    site: int
    destination: Value
    length: int | None
    value: Value | None = None


@dataclass(frozen=True)
class ProofPath:
    events: tuple[ConsumerEvent, ...]
    writes: tuple[MemoryEffect, ...] = ()


@dataclass(frozen=True)
class PaletteProof:
    event_index: int
    target: int
    sub: int
    length: int
    bank: int | None
    destination: int
    destination_kind: str
    origins: tuple[Literal, ...]


@dataclass(frozen=True)
class GraphicProof:
    event_index: int
    target: int
    destination_low: int
    destination_high: int
    length: int | None
    origins: tuple[Literal, ...]
    region: str


@dataclass(frozen=True)
class Flags:
    n: bool | None = None
    z: bool | None = None
    c: bool | None = None
    v: bool | None = None


@dataclass
class _State:
    pc: int
    start: int
    end: int
    registers: tuple[Value, ...]
    memory: dict
    events: tuple[ConsumerEvent, ...]
    chain: tuple[int, ...] = ()
    returned: bool = False
    flags: Flags = Flags()
    writes: tuple[MemoryEffect, ...] = ()
    loops: tuple[tuple[int, int], ...] = ()


@dataclass
class _Budget:
    states: int = MAX_STATES


def _contains(value: Value, start: int, end: int) -> bool:
    return start <= value.low and value.high < end


DMA3_COPY = 0x100
DMA3_DEFERRED = 0x3A000000
COPIES = (*CPU_SET, DMA3_COPY)


@dataclass(frozen=True)
class TransferProof:
    source: int
    destination: int
    length: int
    site: int


def _transfer_length(event: ConsumerEvent) -> int | None:
    control = event.control.constant
    if control is None:
        return None
    if event.swi in CPU_SET and control & 0x01000000:
        return None
    if event.swi == 0x0B:
        unit = 4 if control & 0x04000000 else 2
        length = (control & 0x1FFFFF) * unit
    elif event.swi == 0x0C:
        unit = 4
        length = ((control & 0x1FFFFF) + 7) // 8 * 32
    elif event.swi == DMA3_COPY:
        # Repeat, DRQ and blank timing do not copy here.
        if (not control & 0x80000000 or control & DMA3_DEFERRED
                or (control >> 21) & 3 or (control >> 23) & 3):
            return None
        unit = 4 if control & 0x04000000 else 2
        length = (control & 0xFFFF or 0x10000) * unit
    else:
        return None
    # Transfers ignore low address bits; unaligned spans are unknown.
    if any(value.constant is not None and value.constant % unit
           for value in (event.source, event.destination)):
        return None
    return length


def _decoded_length(rom: Rom, event: ConsumerEvent) -> int | None:
    target = rom_offset(event.source.constant or 0)
    if target is None or target >= len(rom) or rom.data[target] != 0x10:
        return None
    try:
        return len(lz77_decompress(rom.data, target)[0])
    except GraphicsError:
        return None


def event_length(rom: Rom, event: ConsumerEvent) -> int | None:
    if event.swi in COPIES:
        return _transfer_length(event)
    if event.swi in LZ77:
        return _decoded_length(rom, event)
    return None


def _rom_source(value: Value) -> tuple[int, tuple[Literal, ...]] | None:
    constant = value.constant
    if constant is None:
        return None
    target = rom_offset(constant)
    return (target, value.expression.origins) if target is not None else None


def _overlaps(destination: Value, written: int | None, address: int, length: int) -> bool | None:
    base = destination.constant
    if base is None or written is None:
        return None
    return base < address + length and address < base + written


def _write_invalidates(write: MemoryEffect, producer: int, consumer: int,
                       address: int, length: int) -> bool:
    if not producer < write.event_index <= consumer:
        return False
    return _overlaps(write.destination, write.length, address, length) is not False


def _source_origin(rom: Rom, path: ProofPath, index: int, seen: tuple[int, ...] = (),
                   address: int | None = None, length: int | None = None) -> tuple[int, int, tuple[Literal, ...]] | None:
    events = path.events
    event = events[index]
    transferred = event_length(rom, event)
    source = event.source.constant
    address = source if address is None else address
    length = transferred if length is None else length
    if length is None or address is None or source is None or index in seen:
        return None
    direct = _rom_source(event.source)
    if direct is not None and source <= address and address + length <= source + transferred:
        return direct[0], address - source, direct[1]
    for previous in range(index - 1, -1, -1):
        if previous in seen:
            continue
        producer = events[previous]
        if producer.swi not in (*COPIES, *LZ77):
            return None
        prior_length = event_length(rom, producer)
        overlap = _overlaps(producer.destination, prior_length, address, length)
        if overlap is None:
            return None
        if not overlap:
            continue
        base = producer.destination.constant
        if base > address or address + length > base + prior_length:
            return None
        if any(_write_invalidates(write, previous, index, address, length) for write in path.writes):
            return None
        offset = address - base
        if producer.swi in LZ77:
            source = _rom_source(producer.source)
            return (source[0], offset, source[1]) if source is not None else None
        producer_source = producer.source.constant
        if producer_source is None:
            return None
        return _source_origin(rom, path, previous, (*seen, index), producer_source + offset, length)
    return None


def _hardware_palette(destination: Value, length: int) -> tuple[int, str, int] | None:
    if not _contains(destination, *PALETTE_RAM):
        return None
    address = destination.constant
    if address is None or address % 32 or address + length > PALETTE_RAM[1]:
        return None
    region = "obj" if address >= 0x05000200 else "bg"
    base = 0x05000200 if region == "obj" else 0x05000000
    if address + length > base + 0x200:
        return None
    return (address - base) // 32, region, address


def _palette_slices(rom: Rom, path: ProofPath, index: int, destination: Value,
                    kind: str | None = None) -> list[PaletteProof]:
    event = path.events[index]
    length = event_length(rom, event)
    source = event.source.constant
    base = destination.constant
    if length is None or length < 2 or length % 2 or source is None or base is None:
        return []
    direct = _rom_source(event.source) is not None
    if direct:
        slices = [(0, length)]
    else:
        first = max(0, (PALETTE_RAM[0] - base + 31) // 32 * 32)
        end = min(length, PALETTE_RAM[1] - base)
        slices = [(offset, min(32, length - offset)) for offset in range(first, end, 32)]
    kind = kind or ("hardware:" if direct else "staging:local+")
    proofs = []
    for offset, slice_length in slices:
        origin = _source_origin(rom, path, index, address=source + offset, length=slice_length)
        if origin is None:
            continue
        hardware = _hardware_palette(destination.add(Value.known(offset)), slice_length)
        if hardware is None:
            continue
        bank, region, address = hardware
        proofs.append(PaletteProof(index, origin[0], origin[1], slice_length, bank, address,
                                   kind + region, origin[2]))
    return proofs


def palette_proofs(rom: Rom, path: ProofPath) -> list[PaletteProof]:
    return [proof for index, event in enumerate(path.events) if event.swi in COPIES
            for proof in _palette_slices(rom, path, index, event.destination)]


def staged_palette_candidates(rom: Rom, path: ProofPath, transfers: tuple[TransferProof, ...]
                              ) -> list[tuple[PaletteProof, TransferProof]]:
    """Copies into a buffer that some DMA3 transfer reads; never proofs."""
    found = []
    for index, event in enumerate(path.events):
        length = event_length(rom, event) if event.swi in COPIES else None
        base = event.destination.constant
        if not length or base is None:
            continue
        for transfer in transfers:
            if transfer.source <= base and base + length <= transfer.source + transfer.length:
                mapped = Value.known(transfer.destination + base - transfer.source)
                found += [(proof, transfer) for proof in _palette_slices(
                    rom, path, index, mapped, "staging-dma-candidate:")]
    return found


def _vram_region(destination: Value, length: int) -> str | None:
    address = destination.constant
    if address is None:
        return None
    for region, base, end in (("bg", 0x06000000, 0x06010000), ("obj", 0x06010000, VRAM[1])):
        if base <= address and address + length <= end:
            return region
    return None


def graphic_proofs(rom: Rom, path: ProofPath) -> list[GraphicProof]:
    proofs = []
    for index, event in enumerate(path.events):
        if event.swi not in (*COPIES, *LZ77):
            continue
        source = _rom_source(event.source)
        if source is None:
            continue
        length = event_length(rom, event)
        if not length:
            continue
        region = _vram_region(event.destination, length)
        if region is None:
            continue
        proofs.append(GraphicProof(index, source[0], event.destination.low, event.destination.high,
                                   length, source[1], region))
    return proofs


def _arithmetic_flags(left: Value, right: Value, subtract: bool, carry: bool | None) -> Flags:
    a, b = left.constant, right.constant
    if a is None or b is None or carry is None:
        return Flags()
    carry_in = 1 if carry else 0
    exact = a - b - 1 + carry_in if subtract else a + b + carry_in
    result = exact & 0xFFFFFFFF
    signed_a = a - 0x100000000 if a & 0x80000000 else a
    signed_b = b - 0x100000000 if b & 0x80000000 else b
    signed = signed_a - signed_b - 1 + carry_in if subtract else signed_a + signed_b + carry_in
    carry_out = exact >= 0 if subtract else exact > 0xFFFFFFFF
    overflow = not -0x80000000 <= signed < 0x80000000
    return Flags(bool(result & 0x80000000), result == 0, carry_out, overflow)


def _comparison_flags(left: Value, right: Value, subtract: bool) -> Flags:
    return _arithmetic_flags(left, right, subtract, subtract)


def _shift_carry(op: str, source: Value, amount: int, previous: bool | None) -> bool | None:
    if amount == 0:
        return previous
    if op in ("lsl", "lsr") and amount > 32:
        return False
    value = source.constant
    if value is None:
        return None
    if op == "lsl":
        return bool(value & (1 << (32 - amount))) if amount <= 32 else False
    if op == "lsr":
        return bool(value & (1 << (amount - 1))) if amount < 32 else bool(value & 0x80000000)
    if op == "asr":
        return bool(value & (1 << (amount - 1))) if amount < 32 else bool(value & 0x80000000)
    return bool(value & 0x80000000) if amount % 32 == 0 else bool(
        ((value >> (amount % 32)) | (value << (32 - amount % 32))) & 0x80000000)


def _condition(flags: Flags, condition: str) -> bool | None:
    n, z, c, v = flags.n, flags.z, flags.c, flags.v
    if condition == "eq":
        return z
    if condition == "ne":
        return None if z is None else not z
    if condition in ("cs", "cc"):
        return c if condition == "cs" else None if c is None else not c
    if condition in ("mi", "pl"):
        return n if condition == "mi" else None if n is None else not n
    if condition in ("vs", "vc"):
        return v if condition == "vs" else None if v is None else not v
    if condition == "hi":
        return None if c is None or z is None else c and not z
    if condition == "ls":
        return None if c is None or z is None else not c or z
    if condition in ("ge", "lt"):
        equal = None if n is None or v is None else n == v
        return equal if condition == "ge" else None if equal is None else not equal
    if condition in ("gt", "le"):
        greater = None if n is None or v is None or z is None else n == v and not z
        return greater if condition == "gt" else None if greater is None else not greater
    return None


def _execute(rom: Rom, state: _State, depth: int, budget: _Budget) -> list[_State]:
    todo = [state]
    done = []
    while todo:
        current = todo.pop()
        budget.states -= 1
        if budget.states < 0 or len(done) >= MAX_PATHS:
            done.append(current)
            continue
        if not current.start <= current.pc < current.end:
            done.append(current)
            continue
        try:
            ins = thumb.decode(rom.data, current.pc)
        except thumb.ThumbError:
            done.append(current)
            continue
        if current.pc + ins.size > current.end:
            done.append(current)
            continue
        regs = list(current.registers)
        address = ins.address
        regs[15] = Value.known(ROM_BASE + address + 4)
        memory = dict(current.memory)
        events = current.events
        writes = current.writes
        flags = current.flags
        op, rd = ins.mnemonic, ins.rd

        def unknown(register: int) -> Value:
            return Value.unknown(f"{op}@{address:x}:r{register}", address)

        def operand() -> Value:
            if ins.rm is not None:
                return regs[ins.rm]
            return Value.known(ins.immediate or 0)

        def load(at: Value, width: int, signed: bool = False) -> Value:
            key = at.expression.key, width
            if not aligned(at.expression, width):
                return unknown(rd)
            if key in memory:
                return memory[key].at(address)
            constant = at.constant
            offset = rom_offset(constant) if constant is not None else None
            if offset is not None and offset + width <= len(rom):
                number = int.from_bytes(rom.data[offset:offset + width], "little", signed=signed)
                origins = ()
                if width == 4 and rom_offset(number) is not None:
                    origins = (Literal(address, offset, number, rd),)
                value = Value.known(number, origins, address)
            else:
                value = Value.unknown(f"mem{width}@{address:x}:{at.expression.key!r}", address)
                memory[key] = value
            return value

        def store(at: Value, width: int, value: Value) -> None:
            nonlocal writes
            constant = at.constant
            if constant is not None and rom_offset(constant) is not None:
                memory.clear()
                return
            if not aligned(at.expression, width):
                memory.clear()
                writes = (*writes, MemoryEffect(len(events), address, at, width))
                return
            if width < 4:
                value = value.mask((1 << (width * 8)) - 1, address)
            writes = (*writes, MemoryEffect(len(events), address, at, width, value))
            for key in tuple(memory):
                (old_constant, old_terms), old_width = key
                if old_terms != at.expression.terms or not (
                        old_constant + old_width <= at.expression.constant
                        or at.expression.constant + width <= old_constant):
                    del memory[key]
            memory[at.expression.key, width] = value

        if ins.literal is not None:
            if ins.literal + 4 > len(rom):
                done.append(current)
                continue
            number = rom.u32(ins.literal)
            origins = (Literal(address, ins.literal, number, rd),) if rom_offset(number) is not None else ()
            regs[rd] = Value.known(number, origins, address)
        elif op in ("ldr", "ldrb", "ldrh", "ldrsb", "ldrsh", "str", "strb", "strh"):
            at = regs[ins.rn].add(operand())
            if op.startswith("ldr"):
                regs[rd] = load(at, ins.width, ins.signed)
            else:
                store(at, ins.width, regs[rd])
                if op == "str" and ins.width == 4 and at.constant == 0x040000DC:
                    source = memory.get(((0x040000D4, ()), 4), unknown(0))
                    destination = memory.get(((0x040000D8, ()), 4), unknown(1))
                    events = (*events, ConsumerEvent(DMA3_COPY, address, current.chain,
                                                     source, destination, regs[rd]))
        elif op == "mov":
            regs[rd] = operand().at(address)
        elif op in ("add", "sub"):
            left = regs[ins.rn]
            if ins.rn == 15 and ins.immediate is not None:
                left = Value.known((ROM_BASE + address + 4) & ~3)
            regs[rd] = left.add(operand(), -1 if op == "sub" else 1, address)
        elif op in ("lsl", "lsr", "asr", "ror"):
            amount = operand().constant
            if amount is not None and 0x4000 <= ins.raw[0] < _HIGH_OPERATIONS:
                amount &= 0xFF
            source = regs[ins.rn]
            if amount is None:
                regs[rd] = unknown(rd)
            elif amount == 0 or op == "ror" and amount % 32 == 0:
                regs[rd] = source.at(address)
            elif op == "lsl" and amount < 32:
                regs[rd] = source.multiply(1 << amount, address)
            elif op == "lsr" and amount < 32:
                regs[rd] = source.shift_right(amount, address)
            elif op in ("lsl", "lsr"):
                regs[rd] = Value.known(0, site=address)
            elif op == "asr" and source.constant is not None:
                value = source.constant
                signed = value - 0x100000000 if value & 0x80000000 else value
                regs[rd] = Value.known((signed >> min(amount, 31)) & 0xFFFFFFFF, site=address)
            elif op == "ror" and source.constant is not None:
                rotate = amount % 32
                value = source.constant
                regs[rd] = Value.known(((value >> rotate) | (value << (32 - rotate))) & 0xFFFFFFFF,
                                       site=address)
            else:
                regs[rd] = unknown(rd)
        elif op == "mul":
            left, right = regs[ins.rn], operand()
            if left.constant is not None:
                regs[rd] = right.multiply(left.constant, address)
            elif right.constant is not None:
                regs[rd] = left.multiply(right.constant, address)
            else:
                regs[rd] = unknown(rd)
        elif op == "and":
            mask = operand().constant
            regs[rd] = regs[ins.rn].mask(mask, address) if mask is not None else unknown(rd)
        elif op in ("push", "pop"):
            if op == "push":
                regs[13] = regs[13].add(Value.known(-4 * len(ins.registers)))
            for index, register in enumerate(ins.registers):
                at = regs[13].add(Value.known(4 * index))
                if op == "push":
                    store(at, 4, regs[register])
                else:
                    regs[register] = load(at, 4)
            if op == "pop":
                regs[13] = regs[13].add(Value.known(4 * len(ins.registers)))
        elif op == "bl":
            next_pc = address + ins.size
            if ins.target is None or depth >= MAX_CALL_DEPTH:
                outcomes = []
            else:
                called = list(regs)
                called[14] = Value.known(ROM_BASE + next_pc)
                target = ins.target
                nested = _State(target, target, min(len(rom), target + FUNCTION_SPAN),
                                tuple(called), memory, events, current.chain + (address,),
                                flags=Flags(), writes=writes)
                allowance = min(2048, budget.states)
                nested_budget = _Budget(allowance)
                try:
                    outcomes = [result for result in _execute(
                        rom, nested, depth + 1, nested_budget) if result.returned]
                except thumb.ThumbError:
                    outcomes = []
                budget.states -= allowance - nested_budget.states
            if 0 < len(outcomes) <= 16:
                for result in outcomes:
                    returned = list(result.registers)
                    for register in (0, 1, 2, 3, 12, 14):
                        returned[register] = Value.unknown(f"return@{address:x}:r{register}", address)
                    todo.append(_State(next_pc, current.start, current.end, tuple(returned),
                                       dict(result.memory), result.events, current.chain,
                                       flags=Flags(), writes=result.writes, loops=current.loops))
                continue
            for register in (0, 1, 2, 3, 12, 14):
                regs[register] = unknown(register)
            memory = {key: value for key, value in memory.items()
                      if rom_offset(key[0][0]) is not None and not key[0][1]}
            writes = (*writes, MemoryEffect(len(events), address, unknown(0), None))
            flags = Flags()
        elif op == "swi":
            events = (*events, ConsumerEvent(ins.immediate, address, current.chain,
                                             regs[0], regs[1], regs[2]))
            if ins.immediate not in (*COPIES, *LZ77):
                writes = (*writes, MemoryEffect(len(events) - 1, address, unknown(0), None))
            for register in (0, 1, 2, 3, 12):
                regs[register] = unknown(register)
            flags = Flags()
        elif op not in ("cmp", "cmn", "tst", "b", "bx", "undefined"):
            if op == "stmia":
                base = regs[ins.rn]
                if base.constant is None:
                    writes = (*writes, MemoryEffect(len(events), address, base, None))
                else:
                    for index, register in enumerate(ins.registers):
                        writes = (*writes, MemoryEffect(len(events), address,
                                                        base.add(Value.known(4 * index)), 4, regs[register]))
                memory.clear()
            for register in ins.written:
                regs[register] = unknown(register)

        half = ins.raw[0]
        left = current.registers[ins.rn] if ins.rn is not None else Value.known(0)
        right = current.registers[ins.rm] if ins.rm is not None else Value.known(ins.immediate or 0)
        if _HIGH_OPERATIONS <= half < _HIGH_BX and not half & 0xC0:
            # Low-register forms are unpredictable before ARMv6.
            if op != "cmp":
                regs[rd] = unknown(rd)
            flags = Flags()
        elif op in ("cmp", "cmn"):
            flags = _comparison_flags(left, right, op == "cmp")
        elif op == "tst":
            a, b = left.constant, right.constant
            result = a & b if a is not None and b is not None else None
            flags = Flags() if result is None else Flags(bool(result & 0x80000000), result == 0,
                                                         current.flags.c, current.flags.v)
        elif half < _HIGH_OPERATIONS and op in ("add", "sub"):
            flags = _comparison_flags(left, right, op == "sub")
        elif op in ("adc", "sbc"):
            flags = _arithmetic_flags(left, right, op == "sbc", current.flags.c)
        elif op == "neg":
            flags = _comparison_flags(Value.known(0), right, True)
        elif half < _HIGH_OPERATIONS and op in ("lsl", "lsr", "asr", "ror"):
            amount = right.constant
            if amount is not None and 0x4000 <= half < _HIGH_OPERATIONS:
                amount &= 0xFF
            result = regs[rd].constant
            carry = _shift_carry(op, left, amount, current.flags.c) if amount is not None else None
            flags = Flags(None if result is None else bool(result & 0x80000000),
                          None if result is None else result == 0, carry, current.flags.v)
        elif op == "mul":
            result = regs[rd].constant
            # Multiply carry is architecturally unknown.
            flags = Flags(None if result is None else bool(result & 0x80000000),
                          None if result is None else result == 0, None, current.flags.v)
        elif half < _HIGH_OPERATIONS and op in ("mov", "and", "eor", "orr", "bic", "mvn"):
            result = regs[rd].constant
            flags = Flags(None if result is None else bool(result & 0x80000000),
                          None if result is None else result == 0, current.flags.c, current.flags.v)

        current = _State(address + ins.size, current.start, current.end,
                         tuple(regs), memory, events, current.chain, flags=flags, writes=writes,
                         loops=current.loops)
        if op == "pop" and 15 in ins.registers:
            current.returned = True
            done.append(current)
            continue
        if op == "bx":
            target_value = regs[ins.rm].constant
            target = rom_offset(target_value & ~1) if target_value is not None else None
            returning = target is None or current.chain and target == current.chain[-1] + 4
            if returning:
                current.returned = True
                done.append(current)
            else:
                current.pc = target
                current.start = target
                current.end = min(len(rom), target + FUNCTION_SPAN)
                todo.append(current)
            continue
        if op == "undefined" or 15 in ins.written:
            done.append(current)
            continue
        if op == "b":
            take = _condition(flags, ins.condition) if ins.condition else True
            if ins.target <= address:
                counts = dict(current.loops)
                counts[address] = counts.get(address, 0) + 1
                current.loops = tuple(sorted(counts.items()))
                widen = counts[address] > 16 or take is None and counts[address] > 1
                if widen and take is not False:
                    if not ins.condition:
                        done.append(current)
                        continue
                    widened = tuple(Value.unknown(f"loop@{address:x}:r{register}", address)
                                    for register in range(16))
                    effect = MemoryEffect(len(events), address, unknown(0), None)
                    current = _State(address + ins.size, current.start, current.end, widened, {},
                                     events, current.chain, flags=Flags(), writes=writes + (effect,),
                                     loops=current.loops)
                    todo.append(current)
                    continue
            if take is not False:
                current.pc = ins.target
                todo.append(current)
            if take is not True:
                fallthrough = _State(address + ins.size, current.start, current.end,
                                     tuple(regs), dict(memory), events, current.chain,
                                     flags=flags, writes=writes, loops=current.loops)
                todo.append(fallthrough)
            continue
        todo.append(current)
    return done


def consumer_start(rom: Rom, site: int, back: int = 0x20) -> int:
    """A decode-validated lookback after the latest prior function exit."""
    start = max(0, site - back)
    try:
        instructions = thumb.disassemble(rom.data, start, site)
    except thumb.ThumbError:
        return site
    for ins in instructions:
        if ins.mnemonic in ("bx", "undefined") or 15 in ins.written:
            start = ins.address + ins.size
    return start


def dma_transfers(rom: Rom) -> tuple[TransferProof, ...]:
    """Research DMA3 candidates; these are not path or consumer evidence."""
    literals = []
    for address in range(0, len(rom) - 1, 2):
        half = rom.data[address] | rom.data[address + 1] << 8
        if half & 0xF800 != 0x4800:
            continue
        ins = thumb.decode(rom.data, address)
        if ins.literal + 4 <= len(rom):
            literals.append((ins, rom.u32(ins.literal)))
    dma_sites = [ins.address for ins, value in literals if value in (0x040000D4, 0x040000D8, 0x040000DC)]
    found = {}
    for load, value in literals:
        if not 0x02000000 <= value < 0x04000000:
            continue
        nearby = [site for site in dma_sites if 0 <= site - load.address <= 24]
        if not nearby:
            continue
        try:
            paths = consumer_paths(rom, load.address, min(len(rom), max(nearby) + 16))
        except thumb.ThumbError:
            continue
        for path in paths:
            for event in path.events:
                if event.swi != DMA3_COPY:
                    continue
                length = event_length(rom, event)
                source, destination = event.source.constant, event.destination.constant
                if length is None or source is None or destination is None:
                    continue
                found[event.site, source, destination, length] = TransferProof(
                    source, destination, length, event.site)
    return tuple(found[key] for key in sorted(found))


def consumer_paths(rom: Rom, start: int, end: int,
                   initial: dict[int, int] | None = None) -> list[ProofPath]:
    """Execute bounded local and callee paths, preserving event order."""
    registers = [Value.unknown("sp" if register == 13 else f"r{register}", start)
                 for register in range(16)]
    registers[15] = Value.known(ROM_BASE + start + 4)
    for register, value in (initial or {}).items():
        registers[register] = Value.known(value)
    state = _State(start, start, min(len(rom), end), tuple(registers), {}, ())
    return [ProofPath(result.events, result.writes) for result in _execute(rom, state, 0, _Budget())]
