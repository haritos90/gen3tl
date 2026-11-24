"""Script dispatch, handler operand reads, and variable-format evidence."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ..core.rom import ROM_BASE, Rom
from ..core.thumb import ThumbError, decode, walk
from ..families.fingerprints import find
from .accessors import Expression, ScriptReader, analyze, literal_loads, script_reader

class _Shape(set):
    def __init__(self, limit, bios=None):
        super().__init__()
        self.budget = [limit]
        self.redirects = []
        self.calls = {}
        self.bios = bios or {}
        self.conditions = {}
        self.advances = {}

    @property
    def remaining(self):
        return self.budget[0]

    @remaining.setter
    def remaining(self, value):
        self.budget[0] = value

    def fork(self):
        child = _Shape(0, self.bios)
        child.budget = self.budget
        return child

    def absorb(self, assumptions, redirects, conditions, advances, calls):
        self.update(assumptions)
        self.redirects.extend(redirects)
        self.conditions.update(conditions)
        self.advances.update(advances)
        self.calls.update(calls)


@dataclass(frozen=True)
class Read:
    site: int
    offset: int
    width: int
    target: int | None


@dataclass
class Handler:
    opcode: int
    entry: int
    status: str
    lengths: tuple[int, ...] = ()
    reads: tuple[Read, ...] = ()
    code_sites: tuple[int, ...] = ()
    reasons: tuple[str, ...] = ()
    tier: str = "shape"
    assumptions: tuple[str, ...] = ()
    assumption_sites: tuple[int, ...] = ()
    paths: tuple[dict, ...] = ()
    crossref: dict = field(default_factory=dict)
    redirects: tuple[dict, ...] = ()
    conditions: tuple[dict, ...] = ()
    advances: tuple[dict, ...] = ()
    calls: tuple[dict, ...] = ()


@dataclass
class Result:
    table: int | None
    end: int | None
    initializer: int | None
    dispatcher: int | None
    handlers: list[Handler] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    code_sites: tuple[int, ...] = ()
    table_verified: bool = False
    readers_verified: bool = False


def _unknown(name: str) -> Expression:
    return Expression.symbol(name)


def _loaded(op: str, address: int, register: int, width: int, signed: bool) -> Expression:
    """A load result with byte/halfword value bounds when the opcode is unsigned."""
    name = f"{op}@{address:x}:r{register}"
    if signed and width in (1, 2):
        bits = width * 8
        return Expression.symbol(name, ((name, -(1 << (bits - 1)), (1 << (bits - 1)) - 1),))
    if not signed and width in (1, 2):
        return Expression.symbol(name, ((name, 0, (1 << (width * 8)) - 1),))
    return Expression.symbol(name)


def _function_pointer(value: Expression, size: int) -> int | None:
    if value.terms or not value.constant & 1:
        return None
    offset = value.constant - ROM_BASE
    return offset - 1 if 0 <= offset - 1 < size and (offset - 1) % 2 == 0 else None


def _context_relative(value: Expression) -> bool:
    return value.terms == (("context", 1),)


def _context_pointer_write(base: Expression, width: int) -> bool:
    """Whether a write can hit the stream slot."""
    if _stream(base) or _stack(base):
        return False
    terms = dict(base.terms)
    if terms.get("context") != 1:
        return True
    others = {name: coefficient for name, coefficient in terms.items() if name != "context"}
    if not others:
        return base.constant < 12 and base.constant + width > 8
    lower = upper = base.constant
    bounds = base.contribution_bounds()
    for name, coefficient in others.items():
        contribution = bounds.get(name)
        if contribution is None:
            return True
        lower += contribution[0]
        upper += contribution[1]
    if lower < 0 or upper > 0xFFFFFFFF:
        return True
    return not (upper + width <= 8 or lower >= 12)


def _unknown_memory_write(base: Expression) -> bool:
    return not (_stream(base) or _stack(base) or any(
        name == "context" or name.startswith("context:") for name, _ in base.terms))


def _stream(value: Expression) -> bool:
    return value.terms == (("stream", 1),)


def _stack(value: Expression) -> bool:
    return value.terms == (("sp", 1),)


def _return(value: Expression) -> bool:
    return value.terms == (("return", 1),)


def _operand_read(site, offset, width, signed=False):
    name = f"operand:{offset}:{width}@{site:x}"
    bounds = () if width == 4 or signed else ((name, 0, (1 << (8 * width)) - 1),)
    return Expression.symbol(name, bounds)


def _redirect(value):
    return value.terms == (("redirect", 1),)


def _redirect_source(value):
    if not value.terms:
        return "null" if value.constant == 0 else None
    words = [name for name, coefficient in value.terms if coefficient == 1
             and name.startswith("operand:") and name.split(":")[2].split("@")[0] == "4"]
    if len(words) == 1 and not _context_value(value) and not _stack(value):
        return words[0] if len(value.terms) == 1 and not value.constant else f"relative:{words[0]}"
    if len(value.terms) != 1 or value.terms[0][1] != 1 or value.constant:
        return None
    name = value.terms[0][0]
    if name.startswith(("context-field:", "table:")):
        return name
    return None


def _limits(value):
    lower = upper = value.constant
    bounds = value.contribution_bounds()
    for name, _ in value.terms:
        if name not in bounds:
            return None
        low, high = bounds[name]
        lower += low
        upper += high
    return (lower, upper) if 0 <= lower <= upper <= 0xFFFFFFFF else None


def _table_load(data, base):
    bounds = _limits(base)
    if bounds is None or not base.terms or base.constant % 4:
        return None
    low, high = bounds
    if any(coefficient % 4 for _, coefficient in base.terms):
        return None
    if low < ROM_BASE or high + 4 > ROM_BASE + len(data) or high - low > 1020:
        return None
    values = [int.from_bytes(data[at - ROM_BASE:at - ROM_BASE + 4], "little")
              for at in range(low, high + 1, 4)]
    if not values or any(not ROM_BASE <= value < ROM_BASE + len(data) for value in values):
        return None
    return Expression.symbol(f"table:{low:x}:{high:x}")


def _branch_registers(instruction, registers, successor):
    if not instruction.condition:
        return registers
    left, right = registers[16:18]
    limits = _limits(left)
    if right.terms or limits is None:
        return registers
    low, high = limits
    value = right.constant
    taken = successor == instruction.target
    condition = instruction.condition
    if high <= 0x7FFFFFFF and value <= 0x7FFFFFFF:
        condition = {"gt": "hi", "ge": "cs", "lt": "cc", "le": "ls"}.get(condition, condition)
    if not taken:
        condition = {"eq": "ne", "ne": "eq", "hi": "ls", "ls": "hi",
                     "cs": "cc", "cc": "cs"}.get(condition)
    if condition == "eq":
        low, high = max(low, value), min(high, value)
    elif condition == "ne" and low == high == value:
        return None
    elif condition in ("hi", "cs"):
        low = max(low, value + (condition == "hi"))
    elif condition in ("ls", "cc"):
        high = min(high, value - (condition == "cc"))
    if low > high:
        return None
    if len(left.terms) == 1 and left.terms[0][1] > 0:
        name, scale = left.terms[0]
        low = (low - left.constant + scale - 1) // scale
        high = (high - left.constant) // scale
        narrowed = []
        for reg in registers:
            bounds = reg.contribution_bounds()
            coefficient = dict(reg.terms).get(name)
            if coefficient is not None:
                bounds[name] = (low * coefficient, high * coefficient) if coefficient > 0 else (high * coefficient, low * coefficient)
            narrowed.append(replace(reg, bounds=tuple((key, *pair) for key, pair in sorted(bounds.items()))))
        return narrowed
    return registers


def _case_table(data, target, return_site, index):
    """Recognize the Thumb byte-offset switch helper by instructions."""
    pattern = (("push", None, 13, None, None, (1,)), ("mov", 1, None, 14, None, ()),
               ("lsr", 1, 1, None, 1, ()), ("lsl", 1, 1, None, 1, ()),
               ("ldrb", 1, 1, 0, None, ()), ("lsl", 1, 1, None, 1, ()),
               ("add", 14, 14, 1, None, ()), ("pop", None, 13, None, None, (1,)),
               ("bx", None, None, 14, None, ()))
    for position, expected in enumerate(pattern):
        try:
            ins = decode(data, target + position * 2)
        except ThumbError:
            return None
        actual = (ins.mnemonic, ins.rd, ins.rn, ins.rm, ins.immediate, ins.registers)
        if actual != expected:
            return None
    bounds = _limits(index)
    if bounds is None or bounds[1] - bounds[0] > 255 or return_site + bounds[1] >= len(data):
        return ()
    return tuple((value, return_site + 2 * data[return_site + value])
                 for value in range(bounds[0], bounds[1] + 1))


def _saved_load(memory, base, width):
    if width == 4 and (base.key, width) in memory:
        return memory[base.key, width]
    for (key, stored_width), value in memory.items():
        constant, terms = key
        if (_context_value(value) and terms == base.terms
                and constant < base.constant + width and base.constant < constant + stored_width):
            return _unknown("context:partial-spill")
    return None


def _save_value(memory, base, width, value):
    for key, old_width in tuple(memory):
        constant, terms = key
        if old_width == 0:
            if terms == base.terms and base.constant <= constant < base.constant + width:
                del memory[key, old_width]
            continue
        if terms != base.terms or constant + old_width <= base.constant or base.constant + width <= constant:
            continue
        old_value = memory.pop((key, old_width))
        if (_context_value(old_value)
                and not (base.constant <= constant and constant + old_width <= base.constant + width)):
            memory[key, old_width] = _unknown("context:partial-spill")
    if width == 4 and (_stack(base) or _context_value(value)):
        memory[base.key, width] = value
    elif _context_value(value):
        memory[base.key, width] = _unknown("context:partial-spill")


def _reader_shape(data, entry: int, max_states: int = 64,
                  max_depth: int = 3, active: tuple[int, ...] = (),
                  max_span: int = 4096, budget: list[int] | None = None) -> ScriptReader | None:
    """Prove complete readers and wrappers, including their returned value."""
    if entry in active or len(active) > max_depth:
        return None
    registers = [Expression.symbol(f"r{reg}") for reg in range(16)]
    registers[0] = Expression.symbol("context")
    registers[13] = Expression.symbol("sp")
    registers[14] = Expression.symbol("return")
    initial = tuple(registers)
    pointer = Expression.symbol("stream")
    memory, offsets, sites = {}, set(), set()
    address = entry
    remaining = [max_states] if budget is None else budget

    def operand(instruction):
        return (registers[instruction.rm] if instruction.rm is not None
                else Expression(instruction.immediate or 0))

    def byte_value(offset):
        return Expression.symbol(f"byte:{offset}")

    def byte_mask(value):
        mask = 0
        if value.constant:
            return None
        for name, coefficient in value.terms:
            if not name.startswith("byte:") or coefficient not in (1, 256, 65536, 16777216):
                return None
            bits = coefficient * 255
            if mask & bits:
                return None
            mask |= bits
        return mask

    for _ in range(max_states):
        if remaining[0] <= 0:
            return None
        remaining[0] -= 1
        try:
            instruction = decode(data, address)
        except ThumbError:
            return None
        if address + instruction.size > entry + max_span:
            return None
        sites.add(address)
        op = instruction.mnemonic
        if op == "mov":
            if instruction.rd == 15:
                return None
            registers[instruction.rd] = operand(instruction)
        elif op in ("add", "sub"):
            registers[instruction.rd] = registers[instruction.rn].add(
                operand(instruction), -1 if op == "sub" else 1)
        elif op == "lsl" and instruction.immediate is not None:
            registers[instruction.rd] = registers[instruction.rn].multiply(1 << instruction.immediate)
        elif op == "orr":
            left, right = registers[instruction.rn], operand(instruction)
            left_mask, right_mask = byte_mask(left), byte_mask(right)
            if left_mask is None or right_mask is None or left_mask & right_mask:
                return None
            registers[instruction.rd] = left.add(right)
        elif op in ("ldr", "ldrb") and instruction.literal is None:
            base = registers[instruction.rn].add(operand(instruction))
            if op == "ldr" and base == initial[0].add(Expression(8)):
                registers[instruction.rd] = pointer
            elif op == "ldrb" and _stream(base) and base.constant < 4:
                offsets.add(base.constant)
                registers[instruction.rd] = byte_value(base.constant)
            elif op == "ldr" and _stack(base) and base.constant in memory:
                registers[instruction.rd] = memory[base.constant]
            else:
                return None
        elif op == "str":
            base = registers[instruction.rn].add(operand(instruction))
            value = registers[instruction.rd]
            if base == initial[0].add(Expression(8)) and _stream(value) and value.constant <= 4:
                pointer = value
            else:
                return None
        elif op == "push":
            if not _stack(registers[13]):
                return None
            registers[13] = registers[13].add(Expression(-4 * len(instruction.registers)))
            for index, reg in enumerate(instruction.registers):
                memory[(registers[13].constant + 4 * index) & 0xFFFFFFFF] = registers[reg]
        elif op == "pop":
            if not _stack(registers[13]):
                return None
            for index, reg in enumerate(instruction.registers):
                key = (registers[13].constant + 4 * index) & 0xFFFFFFFF
                if key not in memory:
                    return None
                registers[reg] = memory.pop(key)
            registers[13] = registers[13].add(Expression(4 * len(instruction.registers)))
        elif op == "bl":
            if registers[0] != initial[0]:
                return None
            child = _reader_shape(data, instruction.target, max_states, max_depth,
                                  (*active, entry), max_span, remaining)
            if child is None or pointer.constant + child.width > 4:
                return None
            for key in tuple(memory):
                if (key - registers[13].constant) & 0x80000000:
                    del memory[key]
            value = Expression()
            for index in range(child.width):
                offset = pointer.constant + index
                offsets.add(offset)
                value = value.add(byte_value(offset).multiply(1 << (8 * index)))
            pointer = pointer.add(Expression(child.width))
            for reg in (0, 1, 2, 3, 12, 14):
                registers[reg] = Expression.symbol(f"call:{address}:r{reg}")
            registers[0] = value
            sites.update(child.code_sites)
        elif op != "bx":
            return None

        stack_offset = registers[13].constant
        if stack_offset & 0x80000000:
            stack_offset -= 1 << 32
        if not _stack(registers[13]) or not -max_span <= stack_offset <= 0:
            return None
        if op == "bx" or op == "pop" and 15 in instruction.registers:
            returned = registers[instruction.rm] if op == "bx" else registers[15]
            width = pointer.constant
            expected = Expression()
            for index in range(width if width <= 4 else 0):
                expected = expected.add(byte_value(index).multiply(1 << (8 * index)))
            if (returned != initial[14] or width not in (1, 2, 4)
                    or offsets != set(range(width)) or registers[0] != expected
                    or any(registers[reg] != initial[reg] for reg in (*range(4, 12), 13))):
                return None
            return ScriptReader(entry, width, 8, tuple(sorted(sites)))
        address += instruction.size
    return None


def _clobber_reader_stack(memory, stack):
    for key, width in tuple(memory):
        constant, terms = key
        if terms == stack.terms and (constant - stack.constant) & 0x80000000:
            del memory[key, width]


def _trampoline(data, target: int, registers: tuple[Expression, ...]):
    try:
        instruction = decode(data, target)
    except ThumbError:
        return None
    if instruction.mnemonic != "bx" or instruction.rm == 14:
        return None
    return _function_pointer(registers[instruction.rm], len(data))


def _reader_at(data, target, cache, max_states, max_depth, max_span):
    key = ("reader", target, max_states, max_depth, max_span)
    if cache is not None and key in cache:
        return cache[key]
    result = _reader_shape(data, target, min(64, max_states), max_depth, max_span=max_span)
    if cache is not None:
        cache[key] = result
    return result


def _context_value(value: Expression) -> bool:
    return any(name == "context" or name.startswith("context:") or name == "stream"
               for name, _ in value.terms)


def _argument_context(registers, memory):
    """Check arguments and pointers to stored context copies."""
    if not _stack(registers[13]):
        return True
    arguments = list(registers[:4])
    for (key, width), value in memory.items():
        constant, terms = key
        if (width > 0 and terms == registers[13].terms
                and (constant - registers[13].constant) & 0x80000000 == 0
                and (key, 0) not in memory):
            arguments.append(value)
    for value in arguments:
        if _context_value(value):
            return True
        for (key, width), stored in memory.items():
            if width <= 0 or not _context_value(stored):
                continue
            constant, terms = key
            if terms == value.terms and (constant - value.constant) & 0x80000000 == 0:
                return True
    return False


def _argument_free(registers, pointer, memory, shape, site, target):
    if shape is None or _argument_context(registers, memory):
        return None
    shape.add(site)
    shape.calls[site] = {"site": site, "target": target,
                         "effect": "no-context-argument",
                         "assumption": "arguments-only-preserved-registers-and-frame"}
    returned = list(registers)
    for reg in (0, 1, 2, 3, 12):
        returned[reg] = _unknown(f"call@{site:x}:r{reg}")
    memory = dict(memory)
    _clobber_reader_stack(memory, registers[13])
    return ((tuple(returned), pointer, tuple(sorted(memory.items(), key=repr)), ()),), set(), {site}, set()


def _bios_effect(instruction, registers, memory, shape):
    site, number = instruction.address, instruction.immediate
    description = shape.bios.get("calls", {}).get(str(number)) if shape is not None else None
    if description is None:
        return f"unresolved software call at {site:#x}: unknown BIOS interface {number:#x}"
    for reg in description["writes"]:
        destination = registers[reg]
        if _context_value(destination):
            return f"unresolved software call at {site:#x}: BIOS destination r{reg} derives from context"
        if _stack(destination):
            return f"unresolved software call at {site:#x}: BIOS destination r{reg} reaches caller frame"
        for (key, width), value in tuple(memory.items()):
            if width > 0 and _context_value(value) and key[1] == destination.terms:
                return f"unresolved software call at {site:#x}: BIOS destination r{reg} reaches stored context"
    shape.add(site)
    shape.calls[site] = {"site": site, "bios": number, "name": description["name"],
                         "arguments": description["arguments"], "returns": description["returns"],
                         "writes": description["writes"], "effect": "bios-no-context-write",
                         "assumption": "bios-documented-pointer-effects", "source": shape.bios["source"]}
    _clobber_reader_stack(memory, registers[13])
    derived = any(_context_value(registers[reg]) for reg in description["arguments"])
    prefix = "context:" if derived else ""
    for reg in shape.bios["clobbers"]:
        registers[reg] = _unknown(f"{prefix}bios@{site:x}:r{reg}")
    return None


def _callee(data, entry, registers, pointer, memory, readers,
            max_span, max_states, max_tail, depth, max_depth, cache, shape=None, site=None):
    free = _argument_free(registers, pointer, memory, shape, site, entry)
    if free is not None:
        return free
    unchanged = _summarize(data, entry, registers, pointer, memory, readers,
                           max_span, max_states, max_tail, depth, max_depth,
                           cache, readonly=True, shape=shape)
    if unchanged[0] and not unchanged[3]:
        return unchanged
    return _summarize(data, entry, registers, pointer, memory, readers,
                      max_span, max_states, max_tail, depth, max_depth, cache, shape=shape)


def _summarize(data: bytes | bytearray, entry: int, registers: tuple[Expression, ...],
               pointer: Expression, memory: dict, readers: dict[int, ScriptReader],
               max_span: int, max_states: int, max_tail: int, depth: int,
               max_depth: int, cache: dict | None = None,
               readonly: bool = False, shape=None) -> tuple[tuple, set, set, set]:
    """Bounded helper net effect: return states, stream reads, sites, reasons."""
    if depth > max_depth:
        return (), set(), set(), {f"helper depth limit reached at {entry:#x}"}
    if cache is not None:
        cache_key = (entry, tuple(value.state_key for value in registers), pointer.state_key,
                     tuple(sorted(memory.items(), key=repr)), depth, max_depth,
                     max_span, max_states, max_tail, readonly,
                     shape.remaining if shape is not None else None)
        cached = cache.get(cache_key)
        if cached is not None:
            if shape is not None:
                shape.absorb(*cached[1:6])
                shape.remaining -= cached[6]
            return cached[0]
    starting_budget = shape.remaining if shape is not None else 0
    parent_shape = shape
    shape = shape.fork() if shape is not None else None
    todo = [(entry, entry, registers, pointer, tuple(sorted(memory.items(), key=repr)), 0, ())]
    seen, reads, sites, reasons, exits = set(), set(), set(), set(), []
    return_address = registers[14]
    entry_stack = registers[13]

    def operand(instruction, registers):
        return registers[instruction.rm] if instruction.rm is not None else Expression(instruction.immediate or 0)

    def saved_memory(memory):
        return tuple(sorted(memory.items(), key=repr))

    while todo:
        region, address, previous, pointer, saved, tail, conditions = todo.pop()
        key = (region, address, tuple(value.state_key for value in previous), pointer.state_key, saved, tail)
        if key in seen:
            continue
        if shape is not None:
            shape.remaining -= 1
        if len(seen) >= max_states or shape is not None and shape.remaining < 0:
            reasons.add(f"helper state limit reached at {entry:#x}")
            break
        seen.add(key)
        if tail > max_tail or not region <= address < region + max_span or address + 2 > len(data):
            reasons.add(f"helper control flow leaves bounded code at {address:#x}")
            continue
        try:
            instruction = decode(data, address)
        except ThumbError:
            reasons.add(f"invalid instruction at {address:#x}")
            continue
        if address + instruction.size > region + max_span:
            reasons.add(f"instruction crosses helper bound at {address:#x}")
            continue
        sites.add(address)
        registers = list(previous)
        memory = dict(saved)
        op = instruction.mnemonic

        def unknowns(*names):
            for reg in names:
                sources = [registers[index] for index in (instruction.rn, instruction.rm)
                           if index is not None]
                derived = op not in ("bl", "swi") and any(_context_value(value) for value in sources)
                prefix = "context:" if derived else ""
                registers[reg] = _unknown(f"{prefix}{op}@{address:x}:r{reg}")

        def add_read(read: Read) -> None:
            reads.add(read)

        def reader(target: int) -> bool:
            nonlocal pointer
            if not (_context_relative(registers[0]) and registers[0].constant == 0):
                return False
            script_reader = _reader_at(data, target, cache, max_states,
                                       max_depth - depth - 1, max_span)
            if script_reader is None:
                return False
            if not (_context_relative(registers[0]) and registers[0].constant == 0
                    and _stack(registers[13])):
                return False
            if not _stream(pointer):
                reasons.add(f"reader at {address:#x} has unknown stream position")
                return True
            add_read(Read(address, pointer.constant, script_reader.width, target))
            pointer = pointer.add(Expression(script_reader.width))
            _clobber_reader_stack(memory, registers[13])
            unknowns(0, 1, 2, 3, 12)
            registers[0] = _operand_read(address, pointer.constant - script_reader.width, script_reader.width)
            sites.update(script_reader.code_sites)
            return True

        def store_stack(at: Expression, width: int, value: Expression) -> None:
            _save_value(memory, at, width, value)

        def transfer(value: Expression) -> None:
            nonlocal registers, memory
            if _return(value):
                if value == return_address:
                    exits.append((tuple(registers), pointer, saved_memory(memory), conditions))
                elif region <= value.constant < region + max_span:
                    todo.append((region, value.constant, tuple(registers), pointer,
                                 saved_memory(memory), tail, conditions))
                else:
                    reasons.add(f"unproved helper return at {address:#x}")
                return
            if len(value.terms) == 1 and value.terms[0][0].startswith("table:"):
                _, first, last = value.terms[0][0].split(":")
                for index, pool in enumerate(range(int(first, 16), int(last, 16) + 1, 4)):
                    raw = int.from_bytes(data[pool - ROM_BASE:pool - ROM_BASE + 4], "little")
                    if op == "bx" and not raw & 1:
                        reasons.add(f"non-Thumb jump table target at {address:#x}")
                        continue
                    target = (raw - ROM_BASE) & ~1
                    todo.append((target, target, tuple(registers), pointer, saved_memory(memory),
                                 tail + 1, conditions + ((address, f"table[{index}]", True),)))
                return
            if op == "bx" and registers[14] == return_address and registers[13] == entry_stack:
                free = _argument_free(registers, pointer, memory, shape, address,
                                      _function_pointer(value, len(data)))
                if free is not None:
                    returned, _, saved, _ = free[0][0]
                    registers, memory = list(returned), dict(saved)
                    transfer(registers[14])
                    return
            target = _function_pointer(value, len(data))
            if target is None and op == "mov" and instruction.rd == 15 and not value.terms:
                raw = value.constant - ROM_BASE
                target = raw if 0 <= raw < len(data) and raw % 2 == 0 else None
            if target is None:
                reasons.add(f"unresolved indirect branch at {address:#x}")
                return
            todo.append((target, target, tuple(registers), pointer, saved_memory(memory), tail + 1, conditions))

        if instruction.literal is not None:
            if instruction.literal + 4 > len(data):
                reasons.add(f"literal leaves ROM at {instruction.literal:#x}")
                continue
            registers[instruction.rd] = Expression(int.from_bytes(
                data[instruction.literal:instruction.literal + 4], "little"))
        elif op in ("ldr", "ldrb", "ldrh", "ldrsb", "ldrsh"):
            base = registers[instruction.rn].add(operand(instruction, registers))
            if op == "ldr" and _context_relative(base) and base.constant == 8:
                registers[instruction.rd] = pointer
            elif _stream(base):
                add_read(Read(address, base.constant, instruction.width, None))
                registers[instruction.rd] = _operand_read(address, base.constant,
                                                          instruction.width, instruction.signed)
            elif _redirect(base):
                reasons.add(f"read after script redirect at {address:#x}")
                continue
            elif (saved_value := _saved_load(memory, base, instruction.width)) is not None:
                registers[instruction.rd] = saved_value
            elif not base.terms and ROM_BASE <= base.constant <= ROM_BASE + len(data) - instruction.width:
                at = base.constant - ROM_BASE
                registers[instruction.rd] = Expression(int.from_bytes(
                    data[at:at + instruction.width], "little", signed=instruction.signed))
            elif instruction.width == 4 and (table_value := _table_load(data, base)) is not None:
                registers[instruction.rd] = table_value
            elif "context" in dict(base.terms) and not _context_pointer_write(base, instruction.width):
                registers[instruction.rd] = (_unknown(f"context-field:{address:x}") if instruction.width == 4
                                            else _loaded(op, address, instruction.rd, instruction.width,
                                                         instruction.signed))
            else:
                registers[instruction.rd] = _loaded(op, address, instruction.rd,
                                                    instruction.width, instruction.signed)
        elif op in ("str", "strb", "strh"):
            base = registers[instruction.rn].add(operand(instruction, registers))
            if readonly:
                return (), set(), sites, {f"callee memory write at {address:#x}"}
            overlaps = _context_pointer_write(base, instruction.width)
            if shape is not None and _unknown_memory_write(base):
                shape.add(address)
                overlaps = False
            if overlaps:
                value = registers[instruction.rd]
                if op == "str" and _context_relative(base) and base.constant == 8 and _stream(value):
                    pointer = value
                    if shape is not None:
                        shape.advances[address] = {"site": address, "offset": value.constant}
                elif (shape is not None and op == "str" and _context_relative(base)
                      and base.constant == 8 and _stream(pointer) and _redirect_source(value)
                      and all(read.offset + read.width <= pointer.constant for read in reads)):
                    source = _redirect_source(value)
                    shape.add(address)
                    shape.redirects.append({"site": address, "source": source,
                                            "consumed": pointer.constant})
                    pointer = Expression(pointer.constant, (("redirect", 1),))
                else:
                    pointer = _unknown(f"next@{address:x}")
                    reasons.add(f"next script pointer is opaque at {address:#x}")
            if _unknown_memory_write(base) and shape is None:
                memory.clear()
            elif _stack(base) or shape is not None:
                store_stack(base, instruction.width, registers[instruction.rd])
        elif op == "stmia":
            if readonly:
                return (), set(), sites, {f"callee memory write at {address:#x}"}
            base = registers[instruction.rn]
            overlaps = _context_pointer_write(base, 4 * len(instruction.registers))
            if shape is not None and _unknown_memory_write(base):
                shape.add(address)
                overlaps = False
            if overlaps:
                pointer = _unknown(f"next@{address:x}")
                reasons.add(f"next script pointer is opaque at {address:#x}")
            if _unknown_memory_write(base) and shape is None:
                memory.clear()
            elif _stack(base) or shape is not None:
                for index, reg in enumerate(instruction.registers):
                    store_stack(base.add(Expression(4 * index)), 4, registers[reg])
            unknowns(*instruction.written)
        elif op == "mov":
            value = operand(instruction, registers)
            if instruction.rd == 15:
                transfer(value)
                continue
            registers[instruction.rd] = value
        elif op in ("add", "sub"):
            left = registers[instruction.rn]
            right = operand(instruction, registers)
            value = left.add(right, -1 if op == "sub" else 1)
            if readonly and right.terms and not _context_value(value):
                registers[instruction.rd] = _unknown(f"effect:r{instruction.rd}")
            else:
                registers[instruction.rd] = value if (any(name.startswith("operand:") for name, _ in value.terms)
                                                   or not value.terms or value.bounds or right == Expression() or _context_relative(value) or _stream(value) or _stack(value)
                                                   or _context_value(value)
                                                   or _function_pointer(value, len(data)) is not None) else _unknown(f"r{instruction.rd}")
        elif op == "lsl" and instruction.immediate is not None and 0 <= instruction.immediate < 32:
            if readonly and instruction.immediate and not _context_value(registers[instruction.rn]):
                registers[instruction.rd] = _unknown(f"effect:r{instruction.rd}")
            else:
                registers[instruction.rd] = registers[instruction.rn].multiply(1 << instruction.immediate)
        elif op == "lsr" and instruction.immediate is not None:
            source = registers[instruction.rn]
            scale = 1 << instruction.immediate
            if _limits(source) is not None and all(coefficient % scale == 0 for _, coefficient in source.terms):
                registers[instruction.rd] = Expression(source.constant // scale,
                    tuple((name, coefficient // scale) for name, coefficient in source.terms),
                    bounds=tuple((name, low // scale, high // scale) for name, low, high in source.bounds))
            else:
                unknowns(instruction.rd)
        elif op == "bl":
            if readonly:
                return (), set(), sites, {f"callee forwards control at {address:#x}"}
            target = instruction.target
            cases = _case_table(data, target, address + instruction.size, registers[0])
            if cases is not None:
                if not cases:
                    reasons.add(f"unproved jump table bounds at {address:#x}")
                sites.update(range(target, target + 18, 2))
                for index, destination in cases:
                    next_registers = list(registers)
                    next_registers[0] = Expression(index)
                    next_registers[16:18] = [_unknown("flags-left"), _unknown("flags-right")]
                    next_registers[14] = Expression(destination, (("return", 1),))
                    todo.append((region, destination, tuple(next_registers), pointer,
                                 saved_memory(memory), tail,
                                 conditions + ((address, f"switch-index={index}", True),)))
                continue
            indirect = _trampoline(data, target, registers)
            if indirect is not None:
                target = indirect
            if reader(target):
                registers[14] = Expression(address + 4, (("return", 1),))
            else:
                callee = list(registers)
                callee[14] = Expression(address + 4, (("return", 1),))
                sub_exits, sub_reads, sub_sites, sub_reasons = _callee(
                    data, target, tuple(callee), pointer, memory, readers,
                    max_span, max_states, max_tail, depth + 1, max_depth, cache, shape, address)
                reads.update(sub_reads)
                sites.update(sub_sites)
                for reason in sub_reasons:
                    reasons.add(f"unresolved helper call at {address:#x} targets {target:#x}: {reason}")
                if sub_reasons:
                    continue
                if sub_exits:
                    for sub_registers, sub_pointer, sub_memory, sub_conditions in sub_exits:
                        todo.append((region, address + instruction.size, sub_registers,
                                     sub_pointer, sub_memory, tail, conditions + sub_conditions))
                    registers = None
                else:
                    pointer = _unknown(f"next@{address:x}")
                    reasons.add(f"unresolved helper call at {address:#x} targets {target:#x}")
                    unknowns(0, 1, 2, 3, 12)
                    registers[14] = Expression(address + 4, (("return", 1),))
        elif op == "bx":
            transfer(registers[instruction.rm])
            continue
        elif op == "push":
            if not _stack(registers[13]):
                reasons.add(f"opaque stack pointer at {address:#x}")
                continue
            registers[13] = registers[13].add(Expression(-4 * len(instruction.registers)))
            for index, reg in enumerate(instruction.registers):
                at = registers[13].add(Expression(4 * index))
                store_stack(at, 4, registers[reg])
                if (address == region and (14 in instruction.registers
                        or all(4 <= saved <= 11 for saved in instruction.registers))):
                    memory[at.key, 0] = Expression(1)
        elif op == "pop":
            for index, reg in enumerate(instruction.registers):
                at = registers[13].add(Expression(4 * index))
                saved_value = _saved_load(memory, at, 4)
                registers[reg] = saved_value if saved_value is not None else _unknown(f"pop@{address:x}:r{reg}")
                memory.pop((at.key, 4), None)
                memory.pop((at.key, 0), None)
            registers[13] = registers[13].add(Expression(4 * len(instruction.registers)))
            if 15 in instruction.registers:
                transfer(registers[15])
                continue
        elif op == "ldmia":
            base = registers[instruction.rn]
            if _context_value(base) or instruction.rn in instruction.registers:
                reasons.add(f"unresolved multiple load at {address:#x}")
                continue
            for index, reg in enumerate(instruction.registers):
                at = base.add(Expression(4 * index))
                value = _saved_load(memory, at, 4)
                if value is None and not at.terms and ROM_BASE <= at.constant <= ROM_BASE + len(data) - 4:
                    value = Expression(int.from_bytes(data[at.constant - ROM_BASE:at.constant - ROM_BASE + 4], "little"))
                registers[reg] = value if value is not None else _loaded(op, address, reg, 4, False)
            registers[instruction.rn] = base.add(Expression(4 * len(instruction.registers)))
        elif op == "swi":
            reason = _bios_effect(instruction, registers, memory, shape)
            if reason:
                reasons.add(reason)
                continue
        elif op in ("b", "cmp", "cmn", "tst"):
            pass
        elif op == "undefined" or 15 in instruction.written:
            reasons.add(f"unresolved control flow at {address:#x}")
            continue
        else:
            unknowns(*instruction.written)

        if registers is None:
            continue
        if op == "cmp":
            registers[16:18] = [registers[instruction.rn], operand(instruction, registers)]
        elif op != "b":
            registers[16:18] = [_unknown("flags-left"), _unknown("flags-right")]
        if shape is not None and instruction.condition:
            left, right = registers[16:18]
            shape.conditions[address] = {"site": address, "condition": instruction.condition,
                "left": {"constant": left.constant, "terms": list(left.terms)},
                "right": {"constant": right.constant, "terms": list(right.terms)}}
        for successor in instruction.successors:
            refined = _branch_registers(instruction, registers, successor)
            if refined is None:
                continue
            next_region = region if region <= successor < region + max_span else successor
            next_tail = tail if next_region == region else tail + 1
            edge = ((address, instruction.condition, successor == instruction.target),) if instruction.condition else ()
            path = conditions if edge and edge[0] in conditions else conditions + edge
            todo.append((next_region, successor, tuple(refined), pointer, saved_memory(memory), next_tail, path))

    result = (tuple(exits), reads, sites, reasons)
    details = (set(shape or ()), list(shape.redirects) if shape is not None else [],
               dict(shape.conditions) if shape is not None else {},
               dict(shape.advances) if shape is not None else {},
               dict(shape.calls) if shape is not None else {})
    if parent_shape is not None:
        parent_shape.absorb(*details)
    if cache is not None:
        spent = starting_budget - shape.remaining if shape is not None else 0
        cache[cache_key] = (result, *details, spent)
    return result


def infer_handler(data: bytes | bytearray, opcode: int, entry: int, readers: dict[int, ScriptReader],
                  max_span: int = 4096, max_states: int = 20000, max_tail: int = 16,
                  max_depth: int = 3, max_helper_states: int = 2000,
                  cache: dict | None = None, shape=None, bios=None) -> Handler:
    """Follow stream expressions with explicit alias assumptions."""
    if shape is None:
        shape = _Shape(max_states, bios)
    initial = tuple(Expression.symbol("context") if reg == 0 else
                    Expression.symbol("sp") if reg == 13 else
                    Expression.symbol("return") if reg == 14 else _unknown(f"r{reg}")
                    for reg in range(18))
    if shape is False:
        shape = None
    todo = [(entry, entry, initial, Expression.symbol("stream"), (), 0, ())]
    seen, lengths, reads, sites, reasons = set(), set(), set(), set(), set()
    positions = {0}
    paths = []

    def operand(instruction, registers):
        return registers[instruction.rm] if instruction.rm is not None else Expression(instruction.immediate or 0)

    def saved_memory(memory):
        return tuple(sorted(memory.items(), key=repr))

    while todo:
        region, address, previous, pointer, saved, tail, conditions = todo.pop()
        key = (region, address, tuple(value.state_key for value in previous), pointer.state_key, saved, tail)
        if key in seen:
            continue
        if shape is not None:
            shape.remaining -= 1
        if len(seen) >= max_states or shape is not None and shape.remaining < 0:
            reasons.add(f"handler state limit reached at {address:#x}; stream offset {max(positions)}")
            break
        seen.add(key)
        if _stream(pointer):
            positions.add(pointer.constant)
        if tail > max_tail or not region <= address < region + max_span or address + 2 > len(data):
            reasons.add(f"handler control flow leaves bounded code at {address:#x}")
            continue
        try:
            instruction = decode(data, address)
        except ThumbError:
            reasons.add(f"invalid instruction at {address:#x}")
            continue
        if address + instruction.size > region + max_span:
            reasons.add(f"instruction crosses handler bound at {address:#x}")
            continue
        sites.add(address)
        registers = list(previous)
        memory = dict(saved)
        op = instruction.mnemonic

        def unknowns(*names):
            for reg in names:
                sources = [registers[index] for index in (instruction.rn, instruction.rm)
                           if index is not None]
                derived = op not in ("bl", "swi") and any(_context_value(value) for value in sources)
                prefix = "context:" if derived else ""
                registers[reg] = _unknown(f"{prefix}{op}@{address:x}:r{reg}")

        def add_read(read: Read) -> None:
            reads.add(read)

        def reader(target: int) -> bool:
            nonlocal pointer
            if not (_context_relative(registers[0]) and registers[0].constant == 0):
                return False
            reader = _reader_at(data, target, cache, max_helper_states, max_depth - 1, max_span)
            if reader is None:
                return False
            if not (_context_relative(registers[0]) and registers[0].constant == 0
                    and _stack(registers[13])):
                return False
            if not _stream(pointer):
                reasons.add(f"reader at {address:#x} has unknown stream position")
                return True
            add_read(Read(address, pointer.constant, reader.width, target))
            pointer = pointer.add(Expression(reader.width))
            _clobber_reader_stack(memory, registers[13])
            unknowns(0, 1, 2, 3, 12)
            registers[0] = _operand_read(address, pointer.constant - reader.width, reader.width)
            sites.update(reader.code_sites)
            return True

        def store_stack(at: Expression, width: int, value: Expression) -> None:
            _save_value(memory, at, width, value)

        def transfer(value: Expression) -> None:
            nonlocal pointer, registers, memory

            def finish() -> None:
                if _stream(pointer) or _redirect(pointer):
                    lengths.add(1 + pointer.constant)
                    paths.append({"length": 1 + pointer.constant, "exit": address,
                                  "conditions": [{"site": site, "condition": condition, "taken": taken}
                                                 for site, condition, taken in conditions]})
                else:
                    reasons.add(f"exit has unknown script pointer at {address:#x}")

            if _return(value):
                if value.constant == 0:
                    finish()
                else:
                    todo.append((region, value.constant, tuple(registers), pointer,
                                 saved_memory(memory), tail, conditions))
                return
            if len(value.terms) == 1 and value.terms[0][0].startswith("table:"):
                _, first, last = value.terms[0][0].split(":")
                for index, pool in enumerate(range(int(first, 16), int(last, 16) + 1, 4)):
                    raw = int.from_bytes(data[pool - ROM_BASE:pool - ROM_BASE + 4], "little")
                    if op == "bx" and not raw & 1:
                        reasons.add(f"non-Thumb jump table target at {address:#x}")
                        continue
                    target = (raw - ROM_BASE) & ~1
                    todo.append((target, target, tuple(registers), pointer, saved_memory(memory),
                                 tail + 1, conditions + ((address, f"table[{index}]", True),)))
                return
            if op == "bx" and registers[14] == initial[14] and registers[13] == initial[13]:
                free = _argument_free(registers, pointer, memory, shape, address,
                                      _function_pointer(value, len(data)))
                if free is not None:
                    returned, _, saved, _ = free[0][0]
                    registers, memory = list(returned), dict(saved)
                    transfer(registers[14])
                    return
            target = _function_pointer(value, len(data))
            if target is None and op == "mov" and instruction.rd == 15 and not value.terms:
                raw = value.constant - ROM_BASE
                target = raw if 0 <= raw < len(data) and raw % 2 == 0 else None
            if target is None:
                reasons.add(f"unresolved indirect branch at {address:#x}")
                return
            if reader(target):
                returned = registers[14]
                if not _return(returned):
                    reasons.add(f"reader tail call at {address:#x} has unknown return provenance")
                elif returned.constant == 0:
                    finish()
                else:
                    todo.append((region, returned.constant, tuple(registers), pointer,
                                 saved_memory(memory), tail, conditions))
                return
            if target in readers:
                pointer = _unknown(f"next@{address:x}")
                reasons.add(f"reader tail call at {address:#x} has unknown context pointer")
                return
            todo.append((target, target, tuple(registers), pointer, saved_memory(memory), tail + 1, conditions))

        if instruction.literal is not None:
            if instruction.literal + 4 > len(data):
                reasons.add(f"literal leaves ROM at {instruction.literal:#x}")
                continue
            registers[instruction.rd] = Expression(int.from_bytes(
                data[instruction.literal:instruction.literal + 4], "little"))
        elif op in ("ldr", "ldrb", "ldrh", "ldrsb", "ldrsh"):
            base = registers[instruction.rn].add(operand(instruction, registers))
            if op == "ldr" and _context_relative(base) and base.constant == 8:
                registers[instruction.rd] = pointer
            elif _stream(base):
                add_read(Read(address, base.constant, instruction.width, None))
                registers[instruction.rd] = _operand_read(address, base.constant,
                                                          instruction.width, instruction.signed)
            elif _redirect(base):
                reasons.add(f"read after script redirect at {address:#x}")
                continue
            elif (saved_value := _saved_load(memory, base, instruction.width)) is not None:
                registers[instruction.rd] = saved_value
            elif not base.terms and ROM_BASE <= base.constant <= ROM_BASE + len(data) - instruction.width:
                at = base.constant - ROM_BASE
                registers[instruction.rd] = Expression(int.from_bytes(
                    data[at:at + instruction.width], "little", signed=instruction.signed))
            elif instruction.width == 4 and (table_value := _table_load(data, base)) is not None:
                registers[instruction.rd] = table_value
            elif "context" in dict(base.terms) and not _context_pointer_write(base, instruction.width):
                registers[instruction.rd] = (_unknown(f"context-field:{address:x}") if instruction.width == 4
                                            else _loaded(op, address, instruction.rd, instruction.width,
                                                         instruction.signed))
            else:
                registers[instruction.rd] = _loaded(op, address, instruction.rd,
                                                    instruction.width, instruction.signed)
        elif op in ("str", "strb", "strh"):
            base = registers[instruction.rn].add(operand(instruction, registers))
            overlaps = _context_pointer_write(base, instruction.width)
            if shape is not None and _unknown_memory_write(base):
                shape.add(address)
                overlaps = False
            if overlaps:
                value = registers[instruction.rd]
                if op == "str" and _context_relative(base) and base.constant == 8 and _stream(value):
                    pointer = value
                    if shape is not None:
                        shape.advances[address] = {"site": address, "offset": value.constant}
                elif (shape is not None and op == "str" and _context_relative(base)
                      and base.constant == 8 and _stream(pointer) and _redirect_source(value)
                      and all(read.offset + read.width <= pointer.constant for read in reads)):
                    source = _redirect_source(value)
                    shape.add(address)
                    shape.redirects.append({"site": address, "source": source,
                                            "consumed": pointer.constant})
                    pointer = Expression(pointer.constant, (("redirect", 1),))
                else:
                    pointer = _unknown(f"next@{address:x}")
                    reasons.add(f"next script pointer is opaque at {address:#x}")
            if _unknown_memory_write(base) and shape is None:
                memory.clear()
            elif _stack(base) or shape is not None:
                store_stack(base, instruction.width, registers[instruction.rd])
        elif op == "stmia":
            base = registers[instruction.rn]
            overlaps = _context_pointer_write(base, 4 * len(instruction.registers))
            if shape is not None and _unknown_memory_write(base):
                shape.add(address)
                overlaps = False
            if overlaps:
                pointer = _unknown(f"next@{address:x}")
                reasons.add(f"next script pointer is opaque at {address:#x}")
            if _unknown_memory_write(base) and shape is None:
                memory.clear()
            elif _stack(base) or shape is not None:
                for index, reg in enumerate(instruction.registers):
                    store_stack(base.add(Expression(4 * index)), 4, registers[reg])
            unknowns(*instruction.written)
        elif op == "mov":
            value = operand(instruction, registers)
            if instruction.rd == 15:
                transfer(value)
                continue
            registers[instruction.rd] = value
        elif op in ("add", "sub"):
            left = registers[instruction.rn]
            right = operand(instruction, registers)
            value = left.add(right, -1 if op == "sub" else 1)
            registers[instruction.rd] = value if (any(name.startswith("operand:") for name, _ in value.terms)
                                                   or not value.terms or value.bounds or right == Expression() or _context_relative(value) or _stream(value) or _stack(value)
                                                   or _context_value(value)
                                                   or _function_pointer(value, len(data)) is not None) else _unknown(f"r{instruction.rd}")
        elif op == "lsl" and instruction.immediate is not None and 0 <= instruction.immediate < 32:
            registers[instruction.rd] = registers[instruction.rn].multiply(1 << instruction.immediate)
        elif op == "lsr" and instruction.immediate is not None:
            source = registers[instruction.rn]
            scale = 1 << instruction.immediate
            if _limits(source) is not None and all(coefficient % scale == 0 for _, coefficient in source.terms):
                registers[instruction.rd] = Expression(source.constant // scale,
                    tuple((name, coefficient // scale) for name, coefficient in source.terms),
                    bounds=tuple((name, low // scale, high // scale) for name, low, high in source.bounds))
            else:
                unknowns(instruction.rd)
        elif op == "bl":
            target = instruction.target
            cases = _case_table(data, target, address + instruction.size, registers[0])
            if cases is not None:
                if not cases:
                    reasons.add(f"unproved jump table bounds at {address:#x}")
                sites.update(range(target, target + 18, 2))
                for index, destination in cases:
                    next_registers = list(registers)
                    next_registers[0] = Expression(index)
                    next_registers[16:18] = [_unknown("flags-left"), _unknown("flags-right")]
                    next_registers[14] = Expression(destination, (("return", 1),))
                    todo.append((region, destination, tuple(next_registers), pointer,
                                 saved_memory(memory), tail,
                                 conditions + ((address, f"switch-index={index}", True),)))
                continue
            indirect = _trampoline(data, target, registers)
            if indirect is not None:
                target = indirect
            if reader(target):
                registers[14] = Expression(address + 4, (("return", 1),))
            else:
                callee = list(registers)
                callee[14] = Expression(address + 4, (("return", 1),))
                helper_exits, helper_reads, helper_sites, helper_reasons = _callee(
                    data, target, tuple(callee), pointer, memory, readers,
                    max_span, max_helper_states, max_tail, 1, max_depth, cache, shape, address)
                reads.update(helper_reads)
                sites.update(helper_sites)
                for reason in helper_reasons:
                    reasons.add(f"unresolved helper call at {address:#x} targets {target:#x}: {reason}")
                if helper_reasons:
                    continue
                if helper_exits:
                    for helper_registers, helper_pointer, helper_memory, helper_conditions in helper_exits:
                        todo.append((region, address + instruction.size, helper_registers,
                                     helper_pointer, helper_memory, tail, conditions + helper_conditions))
                    continue
                pointer = _unknown(f"next@{address:x}")
                reasons.add(f"unresolved helper call at {address:#x} targets {target:#x}")
                unknowns(0, 1, 2, 3, 12)
                registers[14] = Expression(address + 4, (("return", 1),))
        elif op == "bx":
            transfer(registers[instruction.rm])
            continue
        elif op == "push":
            if not _stack(registers[13]):
                reasons.add(f"opaque stack pointer at {address:#x}")
                continue
            registers[13] = registers[13].add(Expression(-4 * len(instruction.registers)))
            for index, reg in enumerate(instruction.registers):
                at = registers[13].add(Expression(4 * index))
                store_stack(at, 4, registers[reg])
                if (address == region and (14 in instruction.registers
                        or all(4 <= saved <= 11 for saved in instruction.registers))):
                    memory[at.key, 0] = Expression(1)
        elif op == "pop":
            for index, reg in enumerate(instruction.registers):
                at = registers[13].add(Expression(4 * index))
                saved_value = _saved_load(memory, at, 4)
                registers[reg] = saved_value if saved_value is not None else _unknown(f"pop@{address:x}:r{reg}")
                memory.pop((at.key, 4), None)
                memory.pop((at.key, 0), None)
            registers[13] = registers[13].add(Expression(4 * len(instruction.registers)))
            if 15 in instruction.registers:
                transfer(registers[15])
                continue
        elif op == "ldmia":
            base = registers[instruction.rn]
            if _context_value(base) or instruction.rn in instruction.registers:
                reasons.add(f"unresolved multiple load at {address:#x}")
                continue
            for index, reg in enumerate(instruction.registers):
                at = base.add(Expression(4 * index))
                value = _saved_load(memory, at, 4)
                if value is None and not at.terms and ROM_BASE <= at.constant <= ROM_BASE + len(data) - 4:
                    value = Expression(int.from_bytes(data[at.constant - ROM_BASE:at.constant - ROM_BASE + 4], "little"))
                registers[reg] = value if value is not None else _loaded(op, address, reg, 4, False)
            registers[instruction.rn] = base.add(Expression(4 * len(instruction.registers)))
        elif op == "swi":
            reason = _bios_effect(instruction, registers, memory, shape)
            if reason:
                reasons.add(reason)
                continue
        elif op in ("b", "cmp", "cmn", "tst"):
            pass
        elif op == "undefined" or 15 in instruction.written:
            reasons.add(f"unresolved control flow at {address:#x}")
            continue
        else:
            unknowns(*instruction.written)

        if op == "cmp":
            registers[16:18] = [registers[instruction.rn], operand(instruction, registers)]
        elif op != "b":
            registers[16:18] = [_unknown("flags-left"), _unknown("flags-right")]
        if shape is not None and instruction.condition:
            left, right = registers[16:18]
            shape.conditions[address] = {"site": address, "condition": instruction.condition,
                "left": {"constant": left.constant, "terms": list(left.terms)},
                "right": {"constant": right.constant, "terms": list(right.terms)}}
        for successor in instruction.successors:
            refined = _branch_registers(instruction, registers, successor)
            if refined is None:
                continue
            next_region = region if region <= successor < region + max_span else successor
            next_tail = tail if next_region == region else tail + 1
            edge = ((address, instruction.condition, successor == instruction.target),) if instruction.condition else ()
            path = conditions if edge and edge[0] in conditions else conditions + edge
            todo.append((next_region, successor, tuple(refined), pointer, saved_memory(memory), next_tail, path))

    reads = {read for read in reads if 0 <= read.offset < max_span}
    ordered_reads = tuple(sorted(reads, key=lambda read: (read.offset, read.site, read.width, read.target or -1)))
    repeated_advance = (lengths and max(positions) > max(lengths) - 1
                        and any("state limit" in reason for reason in reasons))
    if len(lengths) > 1 or repeated_advance:
        status = "variable"
    elif len(lengths) == 1 and not reasons:
        status = "fixed"
    elif reasons:
        status = "unresolved"
    elif len(lengths) == 1:
        status = "fixed"
    else:
        status = "unresolved"
        reasons.add(f"no bounded handler exit at {entry:#x}")
    if status == "fixed" and shape:
        status = "shape"
    return Handler(opcode, entry, status, tuple(sorted(lengths)), ordered_reads,
                   tuple(sorted(sites)), tuple(sorted(reasons)),
                   assumptions=(("noncontext-stores-do-not-alias-context",) if shape else ())
                   + tuple(sorted({call["assumption"] for call in shape.calls.values()})) if shape is not None else (),
                   assumption_sites=tuple(sorted(shape or ())), paths=tuple(paths), redirects=tuple(shape.redirects if shape is not None else ()),
                   conditions=tuple(shape.conditions[site] for site in sorted(shape.conditions)) if shape is not None else (),
                   advances=tuple(shape.advances[site] for site in sorted(shape.advances)) if shape is not None else (),
                   calls=tuple(shape.calls[site] for site in sorted(shape.calls)) if shape is not None else ())


def cross_reference(rom: Rom, handlers: list[Handler], starts, *, end_ops,
                    code_offsets, text_offsets, occurrences=(), max_commands=20000, rules=None):
    """Validate complete script graphs using only derived operand lengths."""
    rules = rules or {}
    by_opcode = {handler.opcode: handler for handler in handlers}
    lengths = {h.opcode: h.lengths[0] for h in handlers
               if h.status in ("fixed", "shape") and len(h.lengths) == 1}
    expected, observed, confirmed, blocked, contradictions = {}, {}, {}, {}, {}
    blocked_roots, blocking_opcodes = {}, {}
    census = occurrences if isinstance(occurrences, dict) else {}
    roots, accepted = [], []
    for start in sorted(set(starts)):
        failure = None
        if not rules.get("rom_header_size", 0) <= start < len(rom):
            failure = {"site": start, "reason": "root outside script address space"}
        elif rules.get("padding_opcode") in lengths and lengths[rules["padding_opcode"]] == 1:
            end = start
            while end < len(rom) and rom.data[end] == rules["padding_opcode"]:
                end += 1
            if end > start and end < len(rom) and rom.data[end] == rules.get("erased_byte"):
                failure = {"site": start, "end": end, "reason": "padding-only root reaches erased data"}
        if failure:
            roots.append({"start": start, "status": "invalid-input", "commands": 0, "failures": [failure]})
        else:
            accepted.append(start)
    accepted_set = set(accepted)
    census = {start: sites for start, sites in census.items() if start in accepted_set}
    occurrence_sites = ({pc for sites in census.values() for pc in sites}
                        if isinstance(occurrences, dict) else set(occurrences))
    for pc in occurrence_sites:
        if 0 <= pc < len(rom):
            expected.setdefault(rom.data[pc], set()).add(pc)
    for start in accepted:
        pending, graph, failures, local = [start], {}, [], {}
        null_ends = set()
        while pending:
            pc = pending.pop()
            if pc in graph:
                continue
            if not 0 <= pc < len(rom):
                failures.append({"site": pc, "reason": "script target outside ROM"})
                continue
            if len(graph) >= max_commands:
                failures.append({"site": pc, "reason": "script command limit"})
                break
            opcode = rom.data[pc]
            local.setdefault(opcode, set()).add(pc)
            observed.setdefault(opcode, set()).add(pc)
            graph[pc] = []
            length = lengths.get(opcode)
            if length is None:
                failures.append({"site": pc, "opcode": opcode, "reason": "no derived length"})
                continue
            if pc + length > len(rom):
                failure = {"site": pc, "opcode": opcode, "reason": "derived command leaves ROM"}
                failures.append(failure)
                contradictions.setdefault(opcode, []).append(failure)
                continue
            roles = [(offset, "text") for offset in text_offsets.get(opcode, ())]
            if opcode in code_offsets:
                roles.append((code_offsets[opcode], "script"))
            targets = []
            for offset, role in roles:
                target = rom.ptr_at(pc + offset) if 0 < offset and offset + 4 <= length else None
                if target is None:
                    fits = 0 < offset and offset + 4 <= length
                    raw = rom.u32(pc + offset) if fits else None
                    if (fits and raw == 0 and role == "script" and opcode in end_ops
                            and offset in rules.get("null_ends", {}).get(str(opcode), ())):
                        null_ends.add(pc)
                        continue
                    if (fits and role == "text"
                            and offset in rules.get("runtime_text", {}).get(str(opcode), ())
                            and (raw == 0 or any(low <= raw < high for low, high in rules.get("runtime_ranges", ())))):
                        failures.append({"site": pc, "opcode": opcode, "operand": offset,
                                         "value": raw, "reason": "runtime text operand lacks ROM contents"})
                        continue
                    failure = {"site": pc, "opcode": opcode, "operand": offset,
                               "reason": f"{role} pointer does not resolve inside derived command"}
                    failures.append(failure)
                    contradictions.setdefault(opcode, []).append(failure)
                elif role == "script":
                    targets.append(target)
            if opcode not in end_ops:
                following = pc + length
                if following < len(rom) and rom.data[following] not in by_opcode:
                    failure = {"site": pc, "opcode": opcode, "successor": following,
                               "reason": "derived successor opcode outside command table"}
                    failures.append(failure)
                    contradictions.setdefault(opcode, []).append(failure)
                targets.append(following)
            graph[pc] = targets
            pending.extend(targets)
        terminal = {pc for pc, targets in graph.items()
                    if not targets and (pc in null_ends or rom.data[pc] in end_ops) and rom.data[pc] in lengths}
        finished = set(terminal)
        changed = True
        while changed:
            changed = False
            for pc, targets in graph.items():
                if pc not in finished and targets and all(target in finished for target in targets):
                    finished.add(pc)
                    changed = True
        if start not in finished and not failures:
            failures.append({"site": start, "reason": "script graph has a cycle without a proved end"})
        complete = not failures and start in finished
        if not complete:
            for pc in census.get(start, ()):
                local.setdefault(rom.data[pc], set()).add(pc)
        for opcode, sites in local.items():
            if not complete:
                blocked_roots.setdefault(opcode, set()).add(start)
                blocking_opcodes.setdefault(opcode, set()).update(
                    failure["opcode"] for failure in failures
                    if failure["reason"] == "no derived length" and "opcode" in failure)
            target = confirmed if complete else blocked
            target.setdefault(opcode, set()).update(sites)
        roots.append({"start": start, "status": "confirmed" if complete else "incomplete",
                      "commands": len(graph), "failures": failures})
    for handler in handlers:
        opcode = handler.opcode
        required = expected.get(opcode, set()) | observed.get(opcode, set())
        good = confirmed.get(opcode, set())
        bad = blocked.get(opcode, set())
        conflicts = list({repr(row): row for row in contradictions.get(opcode, [])}.values())
        status = ("contradiction" if conflicts else "unobserved" if not required else
                  "confirmed" if required <= good and not bad
                  and (isinstance(occurrences, dict) or all(root["status"] in ("confirmed", "invalid-input") for root in roots)) else "incomplete")
        handler.crossref = {"status": status, "occurrences": len(required),
                            "confirmed": len(good), "blocked": len(bad),
                            "unreached": len(required - good - bad), "contradictions": conflicts,
                            "length_source": "derived-only",
                            "blocking_roots": sorted(blocked_roots.get(opcode, ())),
                            "blocking_opcodes": sorted(blocking_opcodes.get(opcode, ()))}
        if conflicts:
            handler.crossref["derived_lengths"] = list(handler.lengths)
            handler.lengths = ()
            handler.status = "unresolved"
            handler.reasons += tuple(f"script crossref contradiction at {row['site']:#x}: {row['reason']}"
                                     for row in conflicts)
        elif status == "confirmed" and handler.status in ("fixed", "shape"):
            handler.crossref["shape_status"] = handler.status
            handler.status = "crossref"
            handler.tier = "crossref"
    return {"length_source": "derived-only", "roots": roots,
            "commands": sum(len(sites) for sites in observed.values()),
            "confirmed_roots": sum(row["status"] == "confirmed" for row in roots),
            "invalid_roots": sum(row["status"] == "invalid-input" for row in roots)}


def walk_inputs(rom: Rom, profile):
    """Collect map roots and occurrence sites without importing operand lengths."""
    from .maps import Walker

    class InputWalker(Walker):
        def walk_script(self, start, *args):
            census.setdefault(start, set()).update(super().walk_script(start, *args))

    census = {}
    walker = InputWalker(rom, profile)
    walker.walk_all()
    return sorted(census), {start: sorted(sites) for start, sites in sorted(census.items())}


def _table_candidate(rom: Rom, initializer: int, span: int) -> tuple[int, int] | None:
    literals = literal_loads(rom.data, initializer, initializer + span)
    candidates = set()
    for first in literals:
        for second in literals:
            if first.pool >= second.pool:
                continue
            start, end = first.value - ROM_BASE, second.value - ROM_BASE
            if not (0 <= start < end <= len(rom)) or (end - start) % 4 or not 4 <= end - start <= 1024:
                continue
            entries = [rom.u32(at) for at in range(start, end, 4)]
            if all(value & 1 and rom.is_ptr(value) for value in entries):
                candidates.add((start, end))
    if len(candidates) == 1:
        return candidates.pop()
    return None


def discover(rom: Rom, family, matches: dict[str, tuple[int, ...]]) -> Result:
    """Discover the live table and infer each fixed or variable handler."""
    config = family.scripts
    signatures = family.script_fingerprints
    found = find(rom.data, signatures)
    init_name, dispatch_name = config["initializer"], config["dispatcher"]
    issues = []
    if len(found[init_name]) != 1 or len(found[dispatch_name]) != 1:
        for name in (init_name, dispatch_name):
            if len(found[name]) != 1:
                issues.append({"id": f"script.{name}", "status": "unresolved",
                               "reason": f"expected one fingerprint match, found {len(found[name])}",
                               "candidates": list(found[name])})
        return Result(None, None, None, None, issues=issues)
    initializer, dispatcher = found[init_name][0], found[dispatch_name][0]
    by_name = {signature.name: signature for signature in signatures}
    sites = {initializer, dispatcher}
    candidate = _table_candidate(rom, initializer, by_name[init_name].length)
    if candidate is None:
        issues.append({"id": "script.command_table", "status": "unresolved",
                       "reason": "initializer literals do not identify one bounded Thumb handler table"})
        return Result(None, None, initializer, dispatcher, issues=issues,
                      code_sites=tuple(sorted(sites)))
    table, end = candidate
    dispatch_sites = {i.address for i in walk(rom.data, dispatcher, dispatcher + by_name[dispatch_name].length)
                      if i.mnemonic == "ldr" and i.rn != 15 and i.immediate in (92, 96)}
    dispatcher_verified = len(dispatch_sites) == 2
    if not dispatcher_verified:
        issues.append({"id": "script.dispatch", "status": "unresolved",
                       "reason": "dispatcher table bounds accesses are ambiguous"})
    sites |= dispatch_sites
    readers = {}
    for name in ("script-halfword", "script-word"):
        addresses = matches.get(name, ())
        if len(addresses) != 1:
            issues.append({"id": f"script.{name}", "status": "unresolved",
                           "reason": f"expected one reader fingerprint match, found {len(addresses)}"})
            continue
        address = addresses[0]
        signature = next(item for item in family.fingerprints if item.name == name)
        try:
            reader = script_reader(analyze(rom.data, address, address + signature.length))
        except ThumbError as exc:
            issues.append({"id": f"script.{name}", "status": "unresolved", "reason": str(exc)})
            continue
        shape = _reader_shape(rom.data, address,
                              min(64, config.get("max_helper_states", 2000)),
                              config.get("max_helper_depth", 3),
                              max_span=config.get("max_handler_span", 4096))
        if reader is None or shape is None or reader.width != shape.width or reader.pointer_offset != 8:
            issues.append({"id": f"script.{name}", "status": "unresolved",
                           "reason": "reader body lacks complete read, advance and return proof"})
            continue
        readers[address] = shape
        sites.add(address)
    readers_verified = len(readers) == 2
    if issues:
        return Result(table, end, initializer, dispatcher, issues=issues, code_sites=tuple(sorted(sites)),
                      table_verified=dispatcher_verified, readers_verified=readers_verified)
    cache = {}
    handlers = [infer_handler(rom.data, opcode, rom.u32(table + opcode * 4) - ROM_BASE - 1, readers,
                              max_span=config.get("max_handler_span", 4096),
                              max_states=config.get("max_handler_states", 20000),
                              max_tail=config.get("max_handler_tail", 16),
                              max_depth=config.get("max_helper_depth", 3),
                              max_helper_states=config.get("max_helper_states", 2000),
                              cache=cache, bios=config.get("bios"))
                for opcode in range((end - table) // 4)]
    variable_handlers = {int(opcode) for opcode in config.get("variable_handlers", [])}
    labels = config.get("variable_handlers", {})
    if not isinstance(labels, dict):
        labels = {}
    for handler in handlers:
        if handler.opcode in variable_handlers:
            handler.status = "variable"
            label = labels.get(str(handler.opcode), "runtime data")
            handler.reasons += (f"{label}: variable operand layouts are not inferred",)
        if handler.status not in ("fixed", "shape", "crossref"):
            issues.append({"id": f"script.command[{handler.opcode}]", "status": "candidate"
                           if handler.status == "variable" else "unresolved",
                           "opcode": handler.opcode, "handler": handler.entry,
                           "lengths": list(handler.lengths), "reasons": list(handler.reasons)})
    sites |= {site for handler in handlers for site in handler.code_sites}
    return Result(table, end, initializer, dispatcher, handlers, issues, tuple(sorted(sites)),
                  table_verified=dispatcher_verified, readers_verified=readers_verified)
