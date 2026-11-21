"""Address-independent Thumb fingerprints and bounded ROM searches."""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass

from ..core.thumb import ThumbError, decode, walk


def instruction_key(instruction) -> tuple:
    immediate = instruction.immediate
    if instruction.rn == 15:
        immediate = None
    return (instruction.mnemonic, instruction.size, instruction.rd, instruction.rn,
            instruction.rm, immediate, instruction.registers, instruction.width,
            instruction.signed, instruction.condition)


def digest(instructions) -> str:
    body = [instruction_key(instruction) for instruction in instructions]
    return hashlib.sha256(json.dumps(body, separators=(",", ":")).encode()).hexdigest()


def prefix(data, start: int, end: int, count: int = 4) -> tuple:
    found = []
    while start < end and len(found) < count:
        instruction = decode(data, start)
        if start + instruction.size > end:
            raise ThumbError("fingerprint prefix crosses code span")
        found.append(instruction)
        start += instruction.size
        if instruction.mnemonic in ("undefined", "bx") or 15 in instruction.written:
            break
        if instruction.mnemonic == "b" and not instruction.condition:
            break
    return tuple(found)


@dataclass(frozen=True)
class Fingerprint:
    name: str
    category: str
    length: int
    instructions: int
    sha256: str
    prefix_sha256: str
    prefix_count: int
    entry: tuple

    @classmethod
    def bootstrap(cls, data, start: int, end: int, name: str, category: str, walker=walk) -> Fingerprint:
        body = walker(data, start, end)
        first = prefix(data, start, end)
        if not body or not first or any(i.mnemonic == "undefined" for i in body):
            raise ThumbError("fingerprint needs decoded reachable code")
        result = cls(name, category, end - start, len(body), digest(body), digest(first),
                     len(first), instruction_key(first[0]))
        return cls.read(result.document())

    def document(self) -> dict:
        return {"name": self.name, "category": self.category, "length": self.length,
                "instructions": self.instructions, "sha256": self.sha256,
                "prefix_sha256": self.prefix_sha256, "prefix_count": self.prefix_count,
                "entry": list(self.entry[:6]) + [list(self.entry[6]), *self.entry[7:]]}

    @classmethod
    def read(cls, document: dict) -> Fingerprint:
        try:
            entry = document["entry"]
            entry = (*entry[:6], tuple(entry[6]), *entry[7:])
            result = cls(*(document[key] for key in ("name", "category", "length", "instructions", "sha256",
                                                    "prefix_sha256", "prefix_count")), entry)
        except (KeyError, TypeError, IndexError) as exc:
            raise ValueError("invalid function fingerprint") from exc
        if (not isinstance(result.name, str) or not result.name or not isinstance(result.category, str)
                or not result.category or type(result.length) is not int or not 2 <= result.length <= 65536
                or result.length % 2 or type(result.instructions) is not int or result.instructions < 1
                or type(result.prefix_count) is not int or not 1 <= result.prefix_count <= 4
                or len(entry) != 10 or not isinstance(entry[0], str)
                or type(entry[1]) is not int or entry[1] not in (2, 4)
                or any(value is not None and type(value) is not int for value in entry[2:6])
                or any(type(value) is not int or not 0 <= value < 16 for value in entry[6])
                or type(entry[7]) is not int or type(entry[8]) is not bool
                or entry[9] is not None and not isinstance(entry[9], str)
                or any(not isinstance(value, str) or len(value) != 64
                       or any(c not in "0123456789abcdef" for c in value)
                       for value in (result.sha256, result.prefix_sha256))):
            raise ValueError("invalid function fingerprint")
        return result


def find(data: bytes | bytearray, fingerprints: tuple[Fingerprint, ...], walker=walk) -> dict[str, tuple[int, ...]]:
    """Search every aligned position, retaining missing and ambiguous matches."""
    if len({f.name for f in fingerprints}) != len(fingerprints):
        raise ValueError("duplicate fingerprint name")
    found = {f.name: [] for f in fingerprints}
    entries: dict[tuple, list[Fingerprint]] = {}
    for fingerprint in fingerprints:
        entries.setdefault(fingerprint.entry, []).append(fingerprint)
    selectors = {}
    for halfword in range(65536):
        try:
            choices = entries.get(instruction_key(decode(struct.pack("<HH", halfword, 0xF800), 0)), ())
        except ThumbError:
            continue
        if choices:
            selectors[halfword] = choices
    for index, (halfword,) in enumerate(struct.iter_unpack("<H", memoryview(data)[:len(data) & ~1])):
        choices = selectors.get(halfword, ())
        if not choices:
            continue
        start = index * 2
        prefixes, bodies = {}, {}
        for fingerprint in choices:
            end = start + fingerprint.length
            if end > len(data):
                continue
            try:
                key = fingerprint.length, fingerprint.prefix_count
                if key not in prefixes:
                    prefixes[key] = digest(prefix(data, start, end, fingerprint.prefix_count))
                if prefixes[key] != fingerprint.prefix_sha256:
                    continue
                if fingerprint.length not in bodies:
                    body = walker(data, start, end)
                    bodies[fingerprint.length] = len(body), digest(body)
                if bodies[fingerprint.length] == (fingerprint.instructions, fingerprint.sha256):
                    found[fingerprint.name].append(start)
            except ThumbError:
                continue
    return {name: tuple(addresses) for name, addresses in found.items()}
