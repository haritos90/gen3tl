"""ROM image access and GBA pointer helpers."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

ROM_BASE = 0x08000000
ROM_MIRROR_END = 0x0A000000  # 32 MiB window starting at ROM_BASE


class RomError(Exception):
    pass


@dataclass(frozen=True)
class RomEvidence:
    """A ROM span identified without storing its bytes."""
    offset: int
    size: int
    sha256: str

    @classmethod
    def valid(cls, value) -> bool:
        return (isinstance(value, dict) and set(value) == {"offset", "size", "sha256"}
                and type(value["offset"]) is int and type(value["size"]) is int
                and 0 <= value["offset"] < value["offset"] + value["size"] <= ROM_MIRROR_END - ROM_BASE
                and isinstance(value["sha256"], str)
                and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is not None)

    @classmethod
    def parse(cls, value, what="ROM evidence", error=ValueError) -> "RomEvidence":
        if not cls.valid(value):
            raise error(f"{what}: expected offset, positive size and lowercase SHA-256")
        return cls(**value)

    def matches(self, data: bytes | bytearray) -> bool:
        raw = data[self.offset:self.offset + self.size]
        return len(raw) == self.size and hashlib.sha256(raw).hexdigest() == self.sha256

    def read(self, data: bytes | bytearray) -> bytes:
        if not self.matches(data):
            raise ValueError(f"ROM evidence differs at {self.offset:#x}")
        return bytes(data[self.offset:self.offset + self.size])


class Rom:
    """A mutable ROM image with little-endian accessors."""

    def __init__(self, data: bytes | bytearray, path: Path | None = None):
        self.data = bytearray(data)
        self.path = path

    @classmethod
    def load(cls, path: str | Path, expected_sha1: str | None = None) -> "Rom":
        path = Path(path)
        data = path.read_bytes()
        rom = cls(data, path)
        if expected_sha1 is not None and rom.sha1() != expected_sha1.lower():
            raise RomError(
                f"{path}: SHA-1 {rom.sha1()} does not match the profile ({expected_sha1})"
            )
        return rom

    def __len__(self) -> int:
        return len(self.data)

    def sha1(self) -> str:
        return hashlib.sha1(self.data).hexdigest()

    def md5(self) -> str:
        return hashlib.md5(self.data).hexdigest()

    def save(self, path: str | Path) -> None:
        Path(path).write_bytes(bytes(self.data))

    def copy(self) -> "Rom":
        return Rom(bytes(self.data), self.path)

    def u8(self, off: int) -> int:
        return self.data[off]

    def u16(self, off: int) -> int:
        return int.from_bytes(self.data[off:off + 2], "little")

    def u32(self, off: int) -> int:
        return int.from_bytes(self.data[off:off + 4], "little")

    def put_u32(self, off: int, value: int) -> None:
        self.data[off:off + 4] = value.to_bytes(4, "little")

    def read(self, off: int, n: int) -> bytes:
        return bytes(self.data[off:off + n])

    def write(self, off: int, blob: bytes) -> None:
        if off < 0 or off + len(blob) > len(self.data):
            raise RomError(f"write of {len(blob)} bytes at {off:#x} leaves the ROM")
        self.data[off:off + len(blob)] = blob

    def is_ptr(self, value: int) -> bool:
        return ROM_BASE <= value < ROM_BASE + len(self.data)

    def ptr_at(self, off: int) -> int | None:
        """The ROM offset a pointer at `off` refers to, or None."""
        return pointer_at(self.data, off)


def to_ptr(offset: int) -> int:
    return offset + ROM_BASE


def ptr_bytes(offset: int) -> bytes:
    return to_ptr(offset).to_bytes(4, "little")


def value_at(data: bytes | bytearray, at: int, width: int = 4, signed: bool = False) -> int | None:
    """The little-endian integer of `width` bytes at `at`, or None when they leave `data`."""
    if at < 0 or width <= 0 or at + width > len(data):
        return None
    return int.from_bytes(data[at:at + width], "little", signed=signed)


def pointer_at(data: bytes | bytearray, at: int) -> int | None:
    """The ROM offset the pointer at `at` refers to, or None."""
    value = value_at(data, at)
    return value - ROM_BASE if value is not None and ROM_BASE <= value < ROM_BASE + len(data) else None
