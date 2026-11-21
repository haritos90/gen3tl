"""Bounded Thumb evidence, without assigning table identities or trust tiers."""

from __future__ import annotations

from dataclasses import dataclass, replace

from gen3tl.core.rom import ROM_BASE
from gen3tl.core.thumb import ThumbError, decode, walk


@dataclass(frozen=True, order=True)
class Literal:
    site: int
    pool: int
    value: int
    register: int


@dataclass(frozen=True)
class Expression:
    constant: int = 0
    terms: tuple[tuple[str, int], ...] = ()
    origins: tuple[Literal, ...] = ()
    sites: tuple[int, ...] = ()
    bounds: tuple[tuple[str, int, int], ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "constant", self.constant & 0xFFFFFFFF)

    @classmethod
    def symbol(cls, name: str, bounds: tuple[tuple[str, int, int], ...] = ()) -> Expression:
        return cls(terms=((name, 1),), bounds=bounds)

    @property
    def key(self) -> tuple:
        return self.constant, self.terms

    @property
    def state_key(self) -> tuple:
        return self.constant, self.terms, self.bounds

    def add(self, other: Expression, scale: int = 1) -> Expression:
        terms = dict(self.terms)
        for name, coefficient in other.terms:
            terms[name] = terms.get(name, 0) + coefficient * scale
        known: dict[str, tuple[int, int] | None] = {}
        for name, low, high in self.bounds:
            known[name] = (low, high)
        for name, low, high in other.bounds:
            if scale >= 0:
                scaled = (scale * low, scale * high)
            else:
                scaled = (scale * high, scale * low)
            if name in known:
                current = known[name]
                known[name] = None if current is None else (current[0] + scaled[0],
                                                            current[1] + scaled[1])
            else:
                known[name] = scaled
        self_terms = dict(self.terms)
        other_terms = dict(other.terms)
        for name in terms:
            self_has = self_terms.get(name, 0) != 0
            other_has = other_terms.get(name, 0) != 0
            self_known = any(bound_name == name for bound_name, _, _ in self.bounds)
            other_known = any(bound_name == name for bound_name, _, _ in other.bounds)
            if (self_has and not self_known) or (other_has and not other_known):
                known[name] = None
        bounds = tuple(sorted((name, low, high) for name, value in known.items()
                              if value is not None and terms.get(name)
                              for low, high in (value,)))
        return Expression(self.constant + other.constant * scale,
                          tuple(sorted((k, v) for k, v in terms.items() if v)),
                          tuple(sorted(set(self.origins + other.origins))),
                          tuple(sorted(set(self.sites + other.sites))),
                          bounds)

    def multiply(self, scale: int) -> Expression:
        terms = tuple((k, v * scale) for k, v in self.terms if v * scale)
        bounds = tuple((name, scale * low, scale * high) if scale >= 0
                       else (name, scale * high, scale * low)
                       for name, low, high in self.bounds
                       if any(term_name == name for term_name, _ in terms))
        return Expression(self.constant * scale, terms,
                          self.origins if scale == 1 else (), self.sites, bounds)

    def at(self, site: int) -> Expression:
        return replace(self, sites=tuple(sorted(set((*self.sites, site)))))

    def contribution_bounds(self) -> dict:
        """Per-term lower and upper contribution ranges for value proofs."""
        result = {}
        for name, low, high in self.bounds:
            result[name] = (low, high)
        return result


def aligned(address: Expression, width: int) -> bool:
    """Entry SP is word-aligned; other input registers are unconstrained."""
    return address.constant % width == 0 and all(
        coefficient % width == 0 or name == "sp" for name, coefficient in address.terms)


@dataclass(frozen=True)
class MemoryAccess:
    site: int
    address: Expression
    width: int
    signed: bool
    value: Expression
    write: bool = False


@dataclass(frozen=True)
class Accessor:
    site: int
    base: int
    indices: tuple[tuple[str, int], ...]
    field_offset: int
    width: int
    signed: bool
    literals: tuple[Literal, ...]
    code_sites: tuple[int, ...]


@dataclass(frozen=True)
class Address:
    site: int
    register: int
    base: int
    indices: tuple[tuple[str, int], ...]
    field_offset: int
    literals: tuple[Literal, ...]
    code_sites: tuple[int, ...]


@dataclass(frozen=True)
class Call:
    site: int
    target: int
    arguments: tuple[Expression, ...]


@dataclass(frozen=True)
class Comparison:
    site: int
    operation: str
    left: Expression
    right: Expression


@dataclass(frozen=True)
class Analysis:
    start: int
    end: int
    literals: tuple[Literal, ...]
    accesses: tuple[MemoryAccess, ...]
    accessors: tuple[Accessor, ...]
    calls: tuple[Call, ...]
    straight_line: bool
    addresses: tuple[Address, ...]
    exit_sites: tuple[int, ...]
    comparisons: tuple[Comparison, ...] = ()


def literal_loads(data: bytes | bytearray, start: int, end: int) -> tuple[Literal, ...]:
    """Read reachable PC-relative pools, excluding branched-over pool bytes."""
    result = []
    for instruction in walk(data, start, end):
        if instruction.literal is not None:
            pool = instruction.literal
            if pool + 4 > len(data):
                raise ThumbError(f"literal leaves ROM at {pool:#x}")
            result.append(Literal(instruction.address, pool,
                                  int.from_bytes(data[pool:pool + 4], "little"), instruction.rd))
    return tuple(result)


def analyze(data: bytes | bytearray, start: int, end: int, limit: int = 4096) -> Analysis:
    """Track affine register values along bounded local control-flow paths."""
    if start < 0 or start % 2 or end < start or end % 2 or end > len(data) or limit < 1:
        raise ThumbError("invalid accessor analysis bounds")
    initial = tuple(Expression.symbol("sp" if r == 13 else f"r{r}") for r in range(16))
    todo = [(start, initial, (), 0)]
    seen, literals, accesses, calls, addresses, exits = set(), set(), set(), set(), set(), set()
    comparisons = set()
    straight_line = True
    while todo:
        address, previous, saved, step = todo.pop()
        if not start <= address < end:
            continue
        state = address, previous, saved, step
        if state in seen:
            continue
        if len(seen) >= limit:
            raise ThumbError("accessor analysis state limit reached")
        seen.add(state)
        instruction = decode(data, address)
        if address + instruction.size > end:
            raise ThumbError(f"instruction crosses span at {address:#x}")
        regs, memory = list(previous), dict(saved)
        regs[15] = Expression(ROM_BASE + address + 4)
        op, rd = instruction.mnemonic, instruction.rd
        if op in ("b", "bl", "swi", "undefined"):
            straight_line = False

        def unknown(destination: str = "value") -> Expression:
            return Expression.symbol(f"{op}@{address:x}:{step}:{destination}").at(address)

        def operand() -> Expression:
            return regs[instruction.rm] if instruction.rm is not None else Expression(instruction.immediate or 0)

        def load(at: Expression, width: int, signed: bool = False) -> Expression:
            key = at.key, width
            if not aligned(at, width):
                value = unknown()
            elif key in memory:
                value = memory[key]
                if signed and value.terms:
                    value = unknown()
                elif signed:
                    number = value.constant & ((1 << (width * 8)) - 1)
                    value = Expression(number - (1 << (width * 8)) if number >> (width * 8 - 1) else number)
            elif not at.terms and at.constant % width == 0 and ROM_BASE <= at.constant <= ROM_BASE + len(data) - width:
                offset = at.constant - ROM_BASE
                number = int.from_bytes(data[offset:offset + width], "little", signed=signed)
                value = Expression(number, sites=at.sites)
                if width == 4 and ROM_BASE <= number < ROM_BASE + len(data):
                    origin = Literal(address, offset, number, rd)
                    value = replace(value, origins=(origin,))
                    literals.add(origin)
            else:
                value = Expression.symbol(f"mem{width}@{address:x}:{step}:{at.key!r}")
                memory[key] = value
            accesses.add(MemoryAccess(address, at, width, signed, value))
            return value.at(address)

        def store(at: Expression, width: int, value: Expression):
            accesses.add(MemoryAccess(address, at, width, False, value, True))
            if (not aligned(at, width)
                    or not at.terms and ROM_BASE <= at.constant < ROM_BASE + len(data)):
                memory.clear()
                return
            for key, old_width in tuple(memory):
                constant, terms = key
                if terms != at.terms or not (constant + old_width <= at.constant or at.constant + width <= constant):
                    del memory[key, old_width]
            if width < 4:
                value = (Expression(value.constant & ((1 << (width * 8)) - 1)) if not value.terms
                         else Expression.symbol(f"store{width}@{address:x}:{step}"))
            memory[at.key, width] = value

        if op in ("cmp", "cmn", "tst"):
            comparisons.add(Comparison(address, op, regs[instruction.rn], operand()))

        if instruction.literal is not None:
            pool = instruction.literal
            if pool + 4 > len(data):
                raise ThumbError(f"literal leaves ROM at {pool:#x}")
            literal = Literal(address, pool, int.from_bytes(data[pool:pool + 4], "little"), rd)
            literals.add(literal)
            origins = (literal,) if ROM_BASE <= literal.value < ROM_BASE + len(data) else ()
            regs[rd] = Expression(literal.value, origins=origins).at(address)
        elif op in ("ldr", "ldrb", "ldrh", "ldrsb", "ldrsh", "str", "strb", "strh"):
            at = regs[instruction.rn].add(operand())
            if op.startswith("ldr"):
                regs[rd] = load(at, instruction.width, instruction.signed)
            else:
                store(at, instruction.width, regs[rd])
        elif op == "mov":
            regs[rd] = operand().at(address)
        elif op in ("add", "sub"):
            left = regs[instruction.rn]
            if instruction.rn == 15 and instruction.immediate is not None:
                left = Expression((ROM_BASE + address + 4) & ~3)
            regs[rd] = left.add(operand(), -1 if op == "sub" else 1).at(address)
        elif op == "lsl" and not operand().terms and 0 <= operand().constant < 32:
            regs[rd] = regs[instruction.rn].multiply(1 << operand().constant).at(address)
        elif op == "mul" and (not regs[instruction.rn].terms or not operand().terms):
            left, right = regs[instruction.rn], operand()
            regs[rd] = (left.multiply(right.constant) if not right.terms else right.multiply(left.constant)).at(address)
        elif op in ("push", "pop"):
            if op == "push":
                regs[13] = regs[13].add(Expression(-4 * len(instruction.registers)))
            for index, reg in enumerate(instruction.registers):
                at = regs[13].add(Expression(4 * index))
                if op == "push":
                    store(at, 4, regs[reg])
                else:
                    regs[reg] = load(at, 4)
            if op == "pop":
                regs[13] = regs[13].add(Expression(4 * len(instruction.registers)))
        elif op in ("bl", "swi"):
            if op == "bl":
                calls.add(Call(address, instruction.target, tuple(regs[:4])))
            for reg in (0, 1, 2, 3, 12, 14):
                regs[reg] = unknown(f"r{reg}")
            memory.clear()
        elif op not in ("cmp", "cmn", "tst", "b", "bx", "undefined"):
            for reg in instruction.written:
                regs[reg] = unknown(f"r{reg}")
            if op == "stmia":
                memory.clear()
        for reg in instruction.written:
            value = regs[reg]
            if value.terms and len(value.origins) == 1 and all(v > 0 for _, v in value.terms):
                origin = value.origins[0]
                addresses.add(Address(address, reg, origin.value - ROM_BASE, value.terms,
                                      value.constant - origin.value, value.origins, value.sites))
        if op != "undefined" and not instruction.successors:
            exits.add(address)
        saved = tuple(sorted(memory.items(), key=repr))
        for successor in reversed(instruction.successors):
            todo.append((successor, tuple(regs), saved, step + 1))
    accessors = []
    for access in accesses:
        at = access.address
        if access.write or not at.terms or len(at.origins) != 1 or not aligned(at, access.width):
            continue
        origin = at.origins[0]
        if any(coefficient <= 0 for _, coefficient in at.terms):
            continue
        accessors.append(Accessor(access.site, origin.value - ROM_BASE, at.terms,
                                  at.constant - origin.value, access.width, access.signed,
                                  at.origins, tuple(sorted(set((*at.sites, access.site))))))
    return Analysis(start, end, tuple(sorted(literals)), tuple(sorted(accesses, key=repr)),
                    tuple(sorted(accessors, key=repr)), tuple(sorted(calls, key=repr)), straight_line,
                    tuple(sorted(addresses, key=repr)), tuple(sorted(exits)), tuple(sorted(comparisons, key=repr)))


@dataclass(frozen=True)
class ScriptReader:
    entry: int
    width: int
    pointer_offset: int
    code_sites: tuple[int, ...]


def script_reader(analysis: Analysis) -> ScriptReader | None:
    """Recognise consecutive byte reads and matching context-pointer advance."""
    if not analysis.straight_line or not analysis.exit_sites:
        return None
    context = Expression.symbol("r0")
    for pointer in analysis.accesses:
        if pointer.write or pointer.width != 4 or pointer.address.terms != context.terms:
            continue
        stream = pointer.value
        reads = [a for a in analysis.accesses if not a.write and a.width == 1
                 and a.address.terms == stream.terms]
        offsets = {a.address.constant - stream.constant for a in reads}
        width = len(offsets)
        writes = [a for a in analysis.accesses if a.write and a.width == 4
                  and a.address.key == pointer.address.key and a.value.terms == stream.terms]
        advances = {a.value.constant - stream.constant for a in writes}
        if width in (1, 2, 4) and offsets == set(range(width)) and advances == set(range(1, width + 1)):
            sites = tuple(sorted({pointer.site, *(a.site for a in reads), *(a.site for a in writes)}))
            return ScriptReader(analysis.start, width, pointer.address.constant, sites)
    return None


def script_read_calls(analysis: Analysis, readers: tuple[ScriptReader, ...]) -> tuple[tuple[Call, ScriptReader], ...]:
    """Match direct calls only to independently established reader entries."""
    by_entry = {reader.entry: reader for reader in readers}
    return tuple((call, by_entry[call.target]) for call in analysis.calls if call.target in by_entry)
