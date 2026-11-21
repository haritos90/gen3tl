"""UPS patches for verified base and translated ROMs."""

from __future__ import annotations

import zlib


class UpsError(ValueError):
    pass


def _vlen(n: int) -> bytes:
    out = bytearray()
    while True:
        x = n & 0x7F
        n >>= 7
        if n == 0:
            out.append(0x80 | x)
            return bytes(out)
        out.append(x)
        n -= 1


def _vread(p: bytes, i: int, end: int | None = None) -> tuple[int, int]:
    end = len(p) if end is None else end
    data, shift = 0, 1
    while True:
        if i >= end:
            raise UpsError("truncated varint")
        x = p[i]
        i += 1
        data += (x & 0x7F) * shift
        if data > 2 ** 53 - 1:
            raise UpsError("UPS integer is too large")
        if x & 0x80:
            return data, i
        shift <<= 7
        data += shift


def create(source: bytes, target: bytes) -> bytes:
    out = bytearray(b"UPS1")
    out += _vlen(len(source)) + _vlen(len(target))
    n = max(len(source), len(target))
    src = source.ljust(n, b"\x00")
    tgt = target.ljust(n, b"\x00")
    i = last = 0
    while i < n:
        if src[i] == tgt[i]:
            i += 1
            continue
        out += _vlen(i - last)
        while i < n and src[i] != tgt[i]:
            out.append(src[i] ^ tgt[i])
            i += 1
        out.append(0)
        i += 1
        last = i
    out += zlib.crc32(source).to_bytes(4, "little") + zlib.crc32(target).to_bytes(4, "little")
    out += zlib.crc32(bytes(out)).to_bytes(4, "little")
    return bytes(out)


def apply(source: bytes, patch: bytes) -> bytes:
    if len(patch) < 18 or patch[:4] != b"UPS1":
        raise UpsError("not a UPS patch")
    if zlib.crc32(patch[:-4]).to_bytes(4, "little") != patch[-4:]:
        raise UpsError("patch CRC mismatch")
    i = 4
    end = len(patch) - 12
    ssize, i = _vread(patch, i, end)
    tsize, i = _vread(patch, i, end)
    if len(source) != ssize or zlib.crc32(source).to_bytes(4, "little") != patch[-12:-8]:
        raise UpsError("source ROM does not match the patch (size or CRC32)")
    out = bytearray(source.ljust(max(ssize, tsize), b"\x00"))
    pos = 0
    while i < end:
        skip, i = _vread(patch, i, end)
        pos += skip
        if pos >= len(out):
            raise UpsError("UPS offset is outside the ROM")
        while True:
            if i >= end:
                raise UpsError("truncated UPS record")
            x = patch[i]
            i += 1
            if x == 0:
                pos += 1
                break
            if pos >= len(out):
                raise UpsError("UPS record is outside the ROM")
            out[pos] ^= x
            pos += 1
    out = bytes(out[:tsize])
    if zlib.crc32(out).to_bytes(4, "little") != patch[-8:-4]:
        raise UpsError("result CRC mismatch")
    return out
