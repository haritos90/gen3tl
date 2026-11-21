"""Finite component joins for native argument-flow block entries."""

from dataclasses import replace
from itertools import product

from .accessors import Expression


class Joins:
    def __init__(self, cap=16, combinations=128):
        self.cap, self.combinations = cap, combinations
        self.rows, self.widened = {}, set()

    def enter(self, pc, registers, memory, buffers):
        incoming = {('r', n): value for n, value in enumerate(registers) if n != 15}
        incoming.update({('m', key): value for key, value in memory})
        incoming.update({('b', key): value for key, value in buffers})
        previous = self.rows.get(pc)
        current = {} if previous is None else previous
        changed = previous is None
        for key in current.keys() | incoming.keys():
            value = incoming.get(key)
            values = current.get(key, () if previous is None else (None,))
            if (pc, key) in self.widened:
                continue
            choices = {self.identity(v): v for v in values}
            identity = self.identity(value)
            if identity not in choices:
                choices[identity] = value
                changed = True
            elif isinstance(value, Expression):
                existing = choices[identity]
                value = replace(existing, origins=tuple(sorted(set(existing.origins + value.origins))),
                                sites=tuple(sorted(set(existing.sites + value.sites))),
                                bounds=existing.bounds if existing.bounds == value.bounds else ())
                if value != existing:
                    choices[identity] = value
                    changed = True
            current[key] = tuple(sorted(choices.values(), key=repr))
            if len(current[key]) > self.cap:
                self.widen(pc, key, current)
        self.rows[pc] = current
        if not changed:
            return ()
        def size():
            count = 1
            for values in current.values():
                count *= len(values)
            return count
        while size() > self.combinations:
            candidates = [key for key, values in current.items() if len(values) > 1]
            key = min(candidates, key=lambda key: ((3 if key[0] == 'b' else self.pointer_values(current[key])),
                                                  -len(current[key]), repr(key)))
            self.widen(pc, key, current)
        keys = sorted(current, key=repr)
        result = []
        for choices in product(*(current[key] for key in keys)):
            regs, saved, content = list(registers), {}, {}
            for key, value in zip(keys, choices):
                kind, name = key
                if kind == 'r':
                    regs[name] = value if value is not None else Expression.symbol(f'join@{pc:x}:r{name}')
                elif value is not None:
                    (saved if kind == 'm' else content)[name] = value
            result.append((tuple(regs), tuple(sorted(saved.items(), key=repr)),
                           tuple(sorted(content.items(), key=repr))))
        return tuple(result)

    @staticmethod
    def identity(value):
        return value.key if isinstance(value, Expression) else value

    @staticmethod
    def pointer_values(values):
        if any(isinstance(v, Expression) and not v.terms and 0x08000000 <= v.constant < 0x0A000000 for v in values):
            return 2
        return int(any(isinstance(v, Expression) and any(
            name.startswith(('load', 'getter')) for name, _ in v.terms) for v in values))

    def widen(self, pc, key, current):
        self.widened.add((pc, key))
        value = None if key[0] in ('m', 'b') else Expression.symbol(f'join@{pc:x}:r{key[1]}')
        current[key] = (value,)
