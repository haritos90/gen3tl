"""Read and convert Gen 3 saves: default names and typed names between two ROMs."""

from __future__ import annotations

import binascii
import json
import re
import struct
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path

EOS = 0xFF
DIGIT_ZERO = 0xA1   # the game's '0'
NAME_LEN = 10

LAYOUT = None  # filled in by the standalone release script


class SaveError(Exception):
    pass


def checksum(data: bytes, size: int) -> int:
    total = 0
    for (w,) in struct.iter_unpack("<I", data[:size]):
        total = (total + w) & 0xFFFFFFFF
    return ((total >> 16) + (total & 0xFFFF)) & 0xFFFF



SHARKPORT = b"\x0d\x00\x00\x00SharkPortSave"
SHARKPORT_GBA = 0x000F0000
SHARKPORT_HEADER = 0x1C            # ROM title, code and checks before the flash
GSV, GSV_FOOTER, GSV_FLASH = b"ADVSAVEG", b"xV4\x12", 0x430
GSV_CHECKSUM, GSV_LENGTH = 0x20, 0x24
NOCASH = b"NocashGbaBackupMediaSavDataFile\x1a"
NOCASH_ENTRY = 0x40
BIZHAWK = b"GBABATT\x00"
CLOCK_FOOTERS = (7, *range(0x0C, 0x31, 2))   # clock data emulators and dumpers append
MISTER_CLOCK = 0x200
LIBRETRO_EEPROM = 0x2000
# RetroArch SaveRAM: magic, version, u32 chunk size, u64 total;
# per chunk: u32 compressed size, then a zlib stream
RZIP, RZIP_HEADER, RZIP_LEVEL = b"#RZIPv\x01#", 20, 6

# kind: what the file is, by the converter's name
CONTAINERS = {
    "raw": "raw save",
    "clock": "raw save with clock data",
    "mister": "MiSTer save with clock data",
    "meteor": "meteor save RAM",
    "libretro": "VBA Next or Beetle GBA save RAM",
    "padded": "padded raw save",
    "bizhawk": "BizHawk 1.x battery file",
    "sharkport": "GameShark, Action Replay or Xploder file",
    "gsv": "GameShark SP file",
    "nocash": "NO$GBA save file",
    "rzip": "RetroArch compressed save",
}


def accepted(size: int) -> str:
    """The files the converter reads, for the message about any other file."""
    return "; ".join((
        f"{size} bytes (raw .sav, .srm, .fla)",
        f"{size} bytes followed by clock data of 7 bytes or an even 12 to 48 ({size + 16} from mGBA)",
        f"{size + MISTER_CLOCK} bytes (MiSTer)", f"{size + 4} bytes (meteor .srm)",
        f"{size + LIBRETRO_EEPROM} bytes (VBA Next, Beetle GBA .srm)",
        f"{size} bytes and padding of 00 or FF bytes",
        "GameShark, Action Replay and Xploder .sps and .xps", "GameShark SP .gsv", "NO$GBA .sav",
        "BizHawk 1.x .SaveRAM", "RetroArch compressed .srm of any of these"))


def sharkport_checksum(payload: bytes, header_unsigned: bool = False) -> int:
    """The checksum after a SharkPort payload: each byte, sign-extended, shifted by the sum so far."""
    total = 0
    for i, b in enumerate(payload):
        v = b if b < 0x80 or header_unsigned and i < SHARKPORT_HEADER else b - 0x100
        total = (total + (v << total % 24)) & 0xFFFFFFFF
    return total


def gsv_checksum(payload: bytes) -> int:
    """CRC-16/CCITT-FALSE of the complete GameShark SP payload."""
    return binascii.crc_hqx(payload, 0xFFFF)


def rle_decode(data: bytes, at: int, size: int) -> tuple[bytes, int]:
    """NO$GBA run-length code from `at`: the bytes and the offset past its end code."""
    out = bytearray()
    while at < len(data):
        c = data[at]
        at += 1
        if c == 0:
            if len(out) != size:
                break
            return bytes(out), at
        if c < 0x80:
            chunk = data[at:at + c]
            at += c
        elif c == 0x80:
            chunk = data[at:at + 1] * (data[at + 1] | data[at + 2] << 8 if at + 2 < len(data) else 0)
            at += 3
        else:
            chunk = data[at:at + 1] * (c - 0x80)
            at += 1
        if not chunk or len(out) + len(chunk) > size or c < 0x80 and len(chunk) < c:
            break
        out += chunk
    raise SaveError("the NO$GBA save data is damaged")


def rle_encode(data: bytes) -> bytes:
    """NO$GBA run-length code: three or more equal bytes repeat, the rest is copied."""
    out = bytearray()
    start = i = 0

    def copy(end):
        nonlocal start
        while start < end:
            n = min(end - start, 0x7F)
            out.append(n)
            out.extend(data[start:start + n])
            start += n

    while i < len(data):
        n = 1
        while i + n < len(data) and n < 0xFFFF and data[i + n] == data[i]:
            n += 1
        if n < 3:
            i += n
            continue
        copy(i)
        out.extend(bytes([0x80 + n, data[i]]) if n < 0x80 else bytes([0x80, data[i]]) + struct.pack("<H", n))
        i = start = i + n
    copy(len(data))
    out.append(0)
    return bytes(out)


@dataclass
class Container:
    """How a save file holds the flash: the bytes before and after it, kept as they are."""
    kind: str
    head: bytes
    tail: bytes
    source: bytes
    flash: bytes
    extra: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return CONTAINERS[self.kind]

    def wrap(self, flash: bytes) -> bytes:
        flash = bytes(flash)
        if flash == self.flash:
            return self.source
        x = self.extra
        if self.kind == "rzip":
            plain = x["inner"].wrap(flash)
            n = x["chunk"]
            chunks = (zlib.compress(plain[i:i + n], RZIP_LEVEL) for i in range(0, len(plain), n))
            return (self.head[:12] + struct.pack("<Q", len(plain))
                    + b"".join(struct.pack("<I", len(c)) + c for c in chunks))
        if self.kind == "nocash":
            held = flash + x["beyond"]
            n = x["stored"] if not held[x["stored"]:].strip(b"\xff") else len(held)
            if x["method"] == 0:
                entry = struct.pack("<4sII", b"SRAM", 0, n) + held[:n]
            else:
                code = rle_encode(held[:n])
                entry = struct.pack("<4sIII", b"SRAM", 1, len(code) + 4 * x["counts_size"], n) + code
            return self.head + entry + self.tail
        out = bytearray(self.head + flash + self.tail)
        if self.kind == "sharkport":
            start, end = x["payload"]
            struct.pack_into("<I", out, end, sharkport_checksum(bytes(out[start:end]), x["header_unsigned"]))
        elif self.kind == "gsv":
            declared = struct.unpack_from("<I", self.head, GSV_LENGTH)[0]
            if declared != len(self.source) - GSV_FLASH or len(flash) != len(self.flash):
                raise SaveError("the GameShark SP payload size differs from its header; the file cannot be edited")
            struct.pack_into("<H", out, GSV_CHECKSUM, gsv_checksum(bytes(out[GSV_FLASH:])))
        return bytes(out)


def unwrap(data: bytes, size: int) -> tuple[Container, bytes]:
    """The container of a save file and the `size` bytes of flash it holds."""
    data = bytes(data)

    def u32(at):
        return struct.unpack_from("<I", data, at)[0] if at + 4 <= len(data) else -1

    def held(kind, at, extra=None):
        flash = data[at:at + size]
        return Container(kind, data[:at], data[at + size:], data, flash, extra or {}), flash

    if data.startswith(RZIP) and len(data) >= RZIP_HEADER:
        chunk, total = struct.unpack_from("<IQ", data, 8)
        if total > 16 * size:   # no save of a GBA game is that large
            raise SaveError(f"the RetroArch compressed file holds {total} bytes in no save format this game uses; "
                            f"accepted: {accepted(size)}")
        plain, at = bytearray(), RZIP_HEADER
        while chunk and len(plain) < total:
            n = u32(at)
            d = zlib.decompressobj()
            try:
                plain += d.decompress(data[at + 4:at + 4 + n], chunk)   # RetroArch's buffer holds one chunk
            except zlib.error:
                break
            if not d.eof or d.unused_data:
                break
            at += 4 + n
        if not chunk or len(plain) != total or at != len(data):
            raise SaveError("the RetroArch compressed save is damaged")
        inner, flash = unwrap(bytes(plain), size)
        return Container("rzip", data[:RZIP_HEADER], b"", data, flash, {"inner": inner, "chunk": chunk}), flash
    if data.startswith(SHARKPORT) and u32(len(SHARKPORT)) == SHARKPORT_GBA:
        at = len(SHARKPORT) + 4
        for _ in range(3):   # title, date or description, notes
            n = u32(at)
            at = len(data) if n < 0 else at + 4 + n
        payload = u32(at)
        start, end = at + 4, at + 4 + payload
        if payload < 0 or end + 4 > len(data):
            raise SaveError("the GameShark file is cut short")
        if payload - SHARKPORT_HEADER < size:
            raise SaveError(f"the GameShark file holds {payload - SHARKPORT_HEADER} bytes of save data, "
                            f"a save of this game {size}")
        unsigned = sharkport_checksum(data[start:end]) != u32(end) and \
            sharkport_checksum(data[start:end], True) == u32(end)
        return held("sharkport", start + SHARKPORT_HEADER, {"payload": (start, end), "header_unsigned": unsigned})
    if data.startswith(GSV) and data[GSV_FLASH - 4:GSV_FLASH] == GSV_FOOTER:
        if len(data) - GSV_FLASH < size:
            raise SaveError(f"the GameShark SP file holds {len(data) - GSV_FLASH} bytes of save data, "
                            f"a save of this game {size}")
        return held("gsv", GSV_FLASH)
    if data.startswith(NOCASH) and data[NOCASH_ENTRY:NOCASH_ENTRY + 4] == b"SRAM":
        method, length = u32(NOCASH_ENTRY + 4), u32(NOCASH_ENTRY + 8)
        at = NOCASH_ENTRY + 12
        if method == 0 and 0 < length <= len(data) - at:
            stored, end, counts = data[at:at + length], at + length, False
        elif method == 1:
            stored, end = rle_decode(data, at + 4, u32(at))
            if length not in (end - at, end - at - 4):
                raise SaveError("the NO$GBA save data is damaged")
            counts = length == end - at
        elif method == 2:
            raise SaveError("NO$GBA compressed this save: set its SAV/SNA File Format option to Uncompressed or Raw, "
                            "then save in the game again")
        else:
            raise SaveError("the NO$GBA save data is damaged")
        full = stored.ljust(size, b"\xff")   # NO$GBA omits unwritten flash bytes
        extra = {"method": method, "stored": len(stored), "beyond": full[size:], "counts_size": counts}
        return Container("nocash", data[:NOCASH_ENTRY], data[end:], data, full[:size], extra), full[:size]
    if data.startswith(BIZHAWK) and len(data) >= 16:
        flash_size, eeprom_size = struct.unpack_from("<ii", data, 8)
        if flash_size >= size and eeprom_size >= 0 and len(data) == 16 + flash_size + eeprom_size:
            return held("bizhawk", 16)
    extra = len(data) - size
    if extra == 0:
        return held("raw", 0)
    if extra in CLOCK_FOOTERS:
        return held("clock", 0)
    if extra == MISTER_CLOCK:
        return held("mister", 0)
    if extra == 4 and u32(size) == size:
        return held("meteor", 0)
    if extra == LIBRETRO_EEPROM:
        return held("libretro", 0)
    if extra > 0 and data[size] in (0x00, 0xFF) and data[size:] == bytes([data[size]]) * extra:
        return held("padded", 0)
    raise SaveError(f"the file holds {len(data)} bytes in no save format this game uses; accepted: {accepted(size)}")


@dataclass
class Layout:
    size: int
    sector_size: int
    data_size: int
    signature: int
    slot_sectors: int
    section_sizes: list[int]
    blocks: dict[str, dict]          # name -> {"ram": addr, "sections": [ids]}
    parasite: dict                    # {"ram": addr, "sections": [[id, size], ...]}
    extra: dict                       # {"ram": addr, "sectors": [30, 31]}
    party: dict                       # keys: count, base, slots, stride, species, ivs
    daycare: dict                     # {"base": addr, "slots": 2, "stride": 0x8C, "species": 0x20, "ivs": 0x48}
    boxes: list[dict]                 # [{"first": 1, "count": 19, "ram": addr}], 30 records each
    box_record: dict                  # {"size": 58, "species": 0x1C, "ivs": 0x36, "per_box": 30}
    names: dict = field(default_factory=dict)  # {"species_table": addr, "species_stride": 11, "species_count": n, "egg_pointer": addr}
    # name fields; the profile's [save.*] tables give keys and evidence
    player: dict = field(default_factory=dict)        # {"name": addr, "size", "limit", "trainer_id": addr}
    rival: dict = field(default_factory=dict)         # {"name": addr, "size", "limit"}
    mon_names: dict = field(default_factory=dict)     # offsets in party, daycare and box records
    box_names: dict = field(default_factory=dict)     # {"size", "limit", "addresses": [box 1, ...]}
    mail: dict = field(default_factory=dict)          # {"base", "count", "stride", "author", ...}
    daycare_mail: dict = field(default_factory=dict)  # offsets in a daycare record
    link_records: dict = field(default_factory=dict)  # {"base", "count", "stride", "name_size", ...}
    hall_of_fame: dict = field(default_factory=dict)  # {"sectors": [28, 29], "data_size", ...}

    @classmethod
    def from_dict(cls, d: dict) -> "Layout":
        def conv(x):
            if isinstance(x, dict):
                return {k: conv(v) for k, v in x.items() if k != "evidence"}
            if isinstance(x, list):
                return [conv(v) for v in x]
            return x
        d = conv(d)
        keys = cls.__dataclass_fields__.keys()
        return cls(**{k: d[k] for k in keys if k in d})


@dataclass
class Slot:
    index: int                    # 0 or 1
    counter: int
    sectors: dict[int, int]       # section id -> absolute sector number


class SaveFile:
    """A FireRed flash save as the Complete FireRed Upgrade extends it."""

    def __init__(self, data: bytes, layout: Layout):
        self.container, flash = unwrap(data, layout.size)
        self.raw = bytearray(flash)
        self.L = layout
        self.slots = self._find_slots()
        if not self.slots:
            raise SaveError("no valid save slot (signature or checksum mismatch)")
        self.hof_valid = self._hof_valid()

    def sector(self, n: int) -> memoryview:
        s = self.L.sector_size
        return memoryview(self.raw)[n * s:(n + 1) * s]

    def footer(self, n: int) -> tuple[int, int, int, int]:
        return struct.unpack_from("<HHII", self.raw, n * self.L.sector_size + 0xFF4)

    def _find_slots(self) -> list[Slot]:
        L = self.L
        slots = []
        for si in (0, 1):
            ids: dict[int, int] = {}
            counter = None
            ok = True
            for k in range(L.slot_sectors):
                n = si * L.slot_sectors + k
                sid, chk, sig, cnt = self.footer(n)
                if sig != L.signature or sid >= L.slot_sectors or sid in ids:
                    ok = False
                    break
                if checksum(bytes(self.sector(n)), L.section_sizes[sid]) != chk:
                    ok = False
                    break
                if counter is None:
                    counter = cnt
                elif cnt != counter:
                    ok = False
                    break
                ids[sid] = n
            if ok and len(ids) == L.slot_sectors:
                slots.append(Slot(si, counter, ids))
        return sorted(slots, key=lambda s: s.counter, reverse=True)

    def regions(self, slot: Slot) -> list[tuple[int, int, int]]:
        """(ram address, absolute file offset, length) for every saved RAM range of a slot."""
        L = self.L
        out = []
        for name, b in L.blocks.items():
            ram = b["ram"]
            for sid in b["sections"]:
                size = L.section_sizes[sid] if sid == b["sections"][-1] else L.data_size
                out.append((ram, slot.sectors[sid] * L.sector_size, size))
                ram += size
        ram = L.parasite["ram"]
        for sid, size in L.parasite["sections"]:
            out.append((ram, slot.sectors[sid] * L.sector_size + L.data_size - size, size))
            ram += size
        ram = L.extra["ram"]
        for n in L.extra["sectors"]:
            out.append((ram, n * L.sector_size, L.data_size))
            ram += L.data_size
        return out

    def segments(self, slot: Slot, addr: int, length: int) -> list[tuple[int, int]]:
        """Map contiguous RAM into saved file segments."""
        original = addr, length
        out = []
        while length:
            for ram, off, size in self.regions(slot):
                if ram <= addr < ram + size:
                    n = min(length, ram + size - addr)
                    out.append((off + addr - ram, n))
                    addr += n
                    length -= n
                    break
            else:
                addr, length = original
                raise SaveError(f"RAM range {addr:#x}+{length} is not stored in the save")
        return out

    def locate(self, slot: Slot, addr: int, length: int) -> int:
        for ram, off, size in self.regions(slot):
            if ram <= addr and addr + length <= ram + size:
                return off + (addr - ram)
        raise SaveError(f"RAM range {addr:#x}+{length} is not stored in the save")

    def read(self, slot: Slot, addr: int, length: int) -> bytes:
        return b"".join(bytes(self.raw[o:o + n]) for o, n in self.segments(slot, addr, length))

    def write(self, slot: Slot, addr: int, data: bytes) -> None:
        at = 0
        for o, n in self.segments(slot, addr, len(data)):
            self.raw[o:o + n] = data[at:at + n]
            at += n

    def mons(self, slot: Slot):
        """Yield (place, address, species, is_egg) for every stored Pokemon."""
        L = self.L
        p = L.party
        count = self.read(slot, p["count"], 1)[0]
        for i in range(min(count, p["slots"])):
            yield from self._mon(slot, f"party {i + 1}", p["base"] + i * p["stride"], p)
        d = L.daycare
        for i in range(d["slots"]):
            yield from self._mon(slot, f"daycare {i + 1}", d["base"] + i * d["stride"], d)
        r = L.box_record
        for rng in L.boxes:
            for b in range(rng["count"]):
                for k in range(r["per_box"]):
                    addr = rng["ram"] + (b * r["per_box"] + k) * r["size"]
                    yield from self._mon(slot, f"box {rng['first'] + b} slot {k + 1}", addr, r)

    def _mon(self, slot, place, addr, fmt):
        species = struct.unpack("<H", self.read(slot, addr + fmt["species"], 2))[0]
        if species == 0:
            return
        ivs = struct.unpack("<I", self.read(slot, addr + fmt["ivs"], 4))[0]
        yield place, addr, species, bool(ivs >> 30 & 1)

    def nickname(self, slot: Slot, addr: int) -> bytes:
        return self.read(slot, addr + 8, NAME_LEN)

    # Hall of Fame sectors lie outside the save slots.
    def _hof_valid(self) -> bool:
        """Sectors 28 and 29 hold a Hall of Fame: the signature and the checksum match."""
        h, S = self.L.hall_of_fame, self.L.sector_size
        if not h:
            return False
        for n in h["sectors"]:
            chk, = struct.unpack_from("<H", self.raw, n * S + 0xFF4)
            sig, = struct.unpack_from("<I", self.raw, n * S + 0xFF8)
            if sig != h["signature"] or checksum(bytes(self.sector(n)), h["data_size"]) != chk:
                return False
        return True

    def hof_offset(self, pos: int, length: int) -> int:
        """File offset of a Hall of Fame data range (the data continues from one sector to the next)."""
        h = self.L.hall_of_fame
        k, o = divmod(pos, h["data_size"])
        if o + length > h["data_size"]:
            raise SaveError(f"Hall of Fame range {pos:#x}+{length} crosses a sector")
        return h["sectors"][k] * self.L.sector_size + o

    def hof_mons(self):
        """Yield (place, species, personality, OT id, nickname file offset) for every Pokemon of a valid Hall of Fame."""
        if not self.hof_valid:
            return
        h = self.L.hall_of_fame
        data = b"".join(bytes(self.sector(n))[:h["data_size"]] for n in h["sectors"])
        for t in range(len(data) // h["team_size"]):
            base = t * h["team_size"]
            if not struct.unpack_from("<H", data, base + h["species"])[0]:
                break   # stored from the first; an empty team ends the list
            for j in range(h["mons"]):
                o = base + j * h["mon_size"]
                species, = struct.unpack_from("<H", data, o + h["species"])
                if not species:
                    continue
                pid, = struct.unpack_from("<I", data, o + h["personality"])
                otid, = struct.unpack_from("<I", data, o + h["trainer_id"])
                yield (f"hall of fame {t + 1} slot {j + 1}", species, pid, otid,
                       self.hof_offset(o + h["nickname"], h["nickname_size"]))

    def name_fields(self) -> list["NameField"]:
        """Every name the save holds, newest slot first."""
        L = self.L
        out: list[NameField] = []
        seen: dict[int, NameField] = {}

        def u32(slot, addr):
            return struct.unpack("<I", self.read(slot, addr, 4))[0]

        def add(slot, label, offset, size, limit, key, pad=0, egg=False, segments=None, species=None):
            if offset in seen:
                seen[offset].slots.append(slot.index)
                return
            f = NameField([slot.index] if slot else [], label, offset, size, limit, key, pad, egg, segments or [], species)
            seen[offset] = f
            out.append(f)

        def name(slot, addr, size, pad=0):
            n = name_of(self.read(slot, addr, size))
            return n.rstrip(b"\x00") if pad else n

        def ram_field(slot, label, addr, size, limit, key_of, pad=0, egg=False, species=None):
            n = name(slot, addr, size, pad)
            segments = self.segments(slot, addr, size)
            add(slot, label, segments[0][0], size, limit, key_of(n), pad, egg, segments, species)
            return n

        def mail(slot, label, addr):
            m = L.mail
            if not struct.unpack("<H", self.read(slot, addr + m["item"], 2))[0]:
                return   # no mail here
            tid = u32(slot, addr + m["trainer_id"])
            ram_field(slot, label, addr + m["author"], m["author_size"], m["author_limit"],
                      lambda n: ("trainer", tid, n), m["author_pad"])

        for slot in self.slots:
            tid = None
            if L.player:
                p = L.player
                tid = u32(slot, p["trainer_id"])
                ram_field(slot, "player", p["name"], p["size"], p["limit"], lambda n: ("trainer", tid, n))
            if L.rival:
                r = L.rival
                ram_field(slot, "rival", r["name"], r["size"], r["limit"], lambda n: ("rival", n))
            b = L.box_names
            for i, addr in enumerate(b.get("addresses", []), 1):
                ram_field(slot, f"box {i}", addr, b["size"], b["limit"], lambda n, i=i: ("box", i, n))
            m = L.mon_names
            if m:
                for place, addr, species, egg in self.mons(slot):
                    pid = u32(slot, addr + m["personality"])
                    otid = u32(slot, addr + m["ot_id"])
                    ram_field(slot, place, addr + m["nickname"], m["nickname_size"], m["nickname_limit"],
                              lambda n: ("mon", pid, otid, n), egg=egg, species=species)
                    ram_field(slot, f"{place} OT", addr + m["ot_name"], m["ot_name_size"], m["ot_name_limit"],
                              lambda n: ("trainer", otid, n))
                    d = L.daycare_mail
                    if d and L.mail and place.startswith("daycare"):
                        mail(slot, f"{place} mail", addr + d["mail"])
                        if struct.unpack("<H", self.read(slot, addr + d["mail"] + L.mail["item"], 2))[0]:
                            ram_field(slot, f"{place} mail OT", addr + d["ot_name"], d["ot_name_size"],
                                      L.mail["author_limit"], lambda n: ("trainer", tid, n))
                            ram_field(slot, f"{place} mail nickname", addr + d["nickname"], d["nickname_size"],
                                      m["nickname_limit"], lambda n: ("mon", pid, otid, n), egg=egg, species=species)
            if L.mail:
                for i in range(L.mail["count"]):
                    mail(slot, f"mail {i + 1}", L.mail["base"] + i * L.mail["stride"])
            k = L.link_records
            for i in range(k.get("count", 0)):
                addr = k["base"] + i * k["stride"]
                if name(slot, addr, k["name_size"]):
                    rid = struct.unpack("<H", self.read(slot, addr + k["trainer_id"], 2))[0]
                    ram_field(slot, f"link record {i + 1}", addr, k["name_size"], k["limit"],
                              lambda n: ("link", rid, n))
        for place, species, pid, otid, off in self.hof_mons():
            size = L.hall_of_fame["nickname_size"]
            nick = name_of(bytes(self.raw[off:off + size]))
            add(None, place, off, size, L.hall_of_fame["nickname_limit"], ("mon", pid, otid, nick),
                egg=species == L.hall_of_fame.get("egg_species"), species=species)
        return out

    def field_bytes(self, f: "NameField") -> bytes:
        return b"".join(bytes(self.raw[o:o + n]) for o, n in f.segments or [(f.offset, f.size)])

    def field_name(self, f: "NameField") -> bytes:
        n = name_of(self.field_bytes(f))
        return n.rstrip(b"\x00") if f.pad else n

    def write_name(self, f: "NameField", old: bytes, new: bytes) -> None:
        if f.pad:   # the game pads a shorter name with spaces (mail authors)
            old, new = old.ljust(f.pad, b"\x00"), new.ljust(f.pad, b"\x00")
        cur = renamed(self.field_bytes(f), old, new)
        at = 0
        for o, n in f.segments or [(f.offset, f.size)]:
            self.raw[o:o + n] = cur[at:at + n]
            at += n

    def fix_checksums(self) -> None:
        L = self.L
        for slot in self.slots:
            for sid, n in slot.sectors.items():
                chk = checksum(bytes(self.sector(n)), L.section_sizes[sid])
                struct.pack_into("<H", self.raw, n * L.sector_size + 0xFF6, chk)
        if self.hof_valid:
            h = L.hall_of_fame
            for n in h["sectors"]:
                chk = checksum(bytes(self.sector(n)), h["data_size"])
                struct.pack_into("<H", self.raw, n * L.sector_size + 0xFF4, chk)

    def to_bytes(self, output: str = "original") -> bytes:
        """Write the original container or raw flash."""
        if output == "original":
            return self.container.wrap(self.raw)
        if output in ("sav", "srm"):
            return bytes(self.raw)
        raise SaveError(f"unknown output format {output!r}")


def blank_save(layout: Layout, counter: int = 1, slot: int = 0, rotation: int = 0) -> bytes:
    """A save with one valid slot of zeroed sections (tests and fixtures)."""
    L = layout
    raw = bytearray(b"\xff" * L.size)
    for sid in range(L.slot_sectors):
        n = slot * L.slot_sectors + (sid + rotation) % L.slot_sectors
        o = n * L.sector_size
        raw[o:o + L.data_size] = bytes(L.data_size)
        struct.pack_into("<HHII", raw, o + 0xFF4, sid, checksum(bytes(L.data_size), L.section_sizes[sid]),
                         L.signature, counter)
    for n in L.extra["sectors"]:
        raw[n * L.sector_size:n * L.sector_size + L.data_size] = bytes(L.data_size)
    return bytes(raw)


def name_of(field: bytes) -> bytes:
    """Name bytes up to the terminator (at most NAME_LEN)."""
    field = field[:NAME_LEN]
    i = field.find(bytes([EOS]))
    return field if i < 0 else field[:i]


def renamed(field: bytes, old: bytes, new: bytes) -> bytes:
    """Write `new` over a name field that holds `old` (any field length)."""
    out = bytearray(field)
    out[:len(new)] = new
    end_old = min(len(old) + 1, len(field))
    end_new = min(len(new) + 1, len(field))
    out[len(new):end_new] = bytes([EOS]) * (end_new - len(new))
    fill = field[end_old:end_old + 1] or bytes([EOS])
    out[end_new:end_old] = fill * max(0, end_old - end_new)
    return bytes(out)


@dataclass
class Change:
    slot: int | None      # None: the Hall of Fame, outside the slots
    place: str
    species: int
    egg: bool
    old: bytes
    new: bytes
    trailing_changed: bool
    box: bool = False     # a box name, not a Pokemon


def pointed_name(rom: bytes, pointer: int) -> bytes:
    """The name a ROM word at `pointer` points to."""
    at = struct.unpack_from("<I", rom, pointer)[0] - 0x08000000
    return name_of(rom[at:at + NAME_LEN + 1])


def rom_box_names(rom: bytes, L: Layout) -> list[list[bytes]]:
    """The default names of each box, in the same order on every ROM."""
    n = L.names
    if "box_text_pointer" not in n:
        return []
    text = pointed_name(rom, n["box_text_pointer"])
    whole = {int(k): pointed_name(rom, v) for k, v in n.get("box_name_pointers", {}).items()}
    boxes = []
    for i in range(1, len(L.box_names.get("addresses", [])) + 1):
        numbered = text + bytes(DIGIT_ZERO + int(d) for d in str(i))
        boxes.append([numbered] + ([whole[i]] if i in whole else []))
    return boxes


def rom_names(rom: bytes, L: Layout) -> tuple[list[bytes], bytes]:
    n = L.names
    names = []
    for i in range(n["species_count"]):
        o = n["species_table"] + i * n["species_stride"]
        names.append(name_of(rom[o:o + n["species_stride"]]))
    egg_ptr = struct.unpack_from("<I", rom, n["egg_pointer"])[0] - 0x08000000
    egg = name_of(rom[egg_ptr:egg_ptr + NAME_LEN + 1])
    return names, egg


def conversion_names(rom: bytes, L: Layout) -> tuple[list[bytes], bytes, list[list[bytes]]]:
    """rom_names and the default box names: what convert maps."""
    return (*rom_names(rom, L), rom_box_names(rom, L))


def convert(save: SaveFile, src: tuple, dst: tuple, known_tables: tuple = ()) -> list[Change]:
    """Rename default-named Pokemon in every valid slot and in the Hall of Fame; returns the changes."""
    src_species, src_egg = src[:2]
    dst_species, dst_egg = dst[:2]
    defaults = species_defaults([src, *known_tables])
    changes = []
    for slot in save.slots:
        # sectors outside the slots are shared by both slots;
        # a name converted in one slot differs in the other
        for place, addr, species, egg in save.mons(slot):
            if egg or species >= len(src_species) or species >= len(dst_species):
                continue
            field = save.nickname(slot, addr)
            cur = name_of(field)
            old, new = cur, dst_species[species]
            if cur not in defaults.get(species, ()) or old == new:
                continue
            out = renamed(field, old, new)
            trailing = any(field[i] != EOS for i in range(len(old) + 1, min(len(new) + 1, NAME_LEN)))
            save.write(slot, addr + 8, out)
            changes.append(Change(slot.index, place, species, egg, old, new, trailing))
    # Hall of Fame records carry the species;
    # an egg shows its stored egg word, so map it
    h = save.L.hall_of_fame
    for place, species, _pid, _otid, off in save.hof_mons():
        egg = species == h.get("egg_species")
        if not egg and (species >= len(src_species) or species >= len(dst_species)):
            continue
        size = h["nickname_size"]
        field = bytes(save.raw[off:off + size])
        old, new = (src_egg, dst_egg) if egg else (name_of(field), dst_species[species])
        if (name_of(field) != old if egg else old not in defaults.get(species, ())) or old == new:
            continue
        trailing = any(field[i] != EOS for i in range(len(old) + 1, min(len(new) + 1, size)))
        save.raw[off:off + size] = renamed(field, old, new)
        changes.append(Change(None, place, species, egg, old, new, trailing))
    # a box takes only its own default names
    b = save.L.box_names
    src_boxes, dst_boxes = (src[2] if len(src) > 2 else []), (dst[2] if len(dst) > 2 else [])
    for slot in save.slots:
        for i, addr in enumerate(b.get("addresses", [])[:min(len(src_boxes), len(dst_boxes))]):
            field = save.read(slot, addr, b["size"])
            for old, new in zip(src_boxes[i], dst_boxes[i]):
                if name_of(field) != old or old == new or len(new) > b["limit"]:
                    continue
                trailing = any(field[k] != EOS for k in range(len(old) + 1, min(len(new) + 1, b["size"])))
                save.write(slot, addr, renamed(field, old, new))
                changes.append(Change(slot.index, f"box {i + 1}", 0, False, old, new, trailing, box=True))
                break
    save.fix_checksums()
    return changes



SAVE_RULES = None  # filled by standalone generation


def language_rules():
    if SAVE_RULES is not None:
        return SAVE_RULES
    from ..lang.language import current
    selected = current()
    return selected.data.get("save", {})


def transliterate(text: str) -> str:
    """Apply pack transliteration while preserving capitalization."""
    table = language_rules().get("transliteration", {})
    cased = [c for c in text if c.isupper() or c.islower()]
    caps = len(cased) > 1 and all(c.isupper() for c in cased)
    out = []
    for c in text:
        low = c.lower()
        if low not in table:
            out.append(c)
        elif c == low:
            out.append(table[low])
        else:
            out.append(table[low].upper() if caps else table[low].capitalize())
    return "".join(out)


def _target_letter(ch: str) -> bool:
    pattern = language_rules().get("letter_pattern")
    return bool(pattern and re.fullmatch(pattern, ch))


class Charset:
    """Glyph codes of the original and translated ROMs."""

    def __init__(self, table: dict | None, translated: dict | None):
        self.table = table or {}
        self.shared = translated or {}
        self.latin: dict[str, int] = {}     # Original glyphs remain valid in names.
        self.translated: dict[str, int] = {}   # Translated names use the pack glyphs.
        for b in sorted(self.table):
            ch = self.table[b]
            (self.translated if _target_letter(ch) else self.latin).setdefault(ch, b)
        for ch, b in self.latin.items():
            self.translated.setdefault(ch, b)
        for b in sorted(self.shared):
            self.translated.setdefault(self.shared[b], b)

    def has_target_letters(self, name: bytes) -> bool:
        """The name holds a pack-specific glyph code."""
        return any(_target_letter(self.table.get(b, "")) for b in name)

    def transliterate(self, name: bytes) -> bytes | None:
        """Convert pack letters; preserve unrelated glyph bytes."""
        if not self.has_target_letters(name):
            return None
        text = ""
        for b in name:
            ch = self.shared.get(b) or self.table.get(b, "")
            text += ch if _target_letter(ch) else chr(0xE000 + b)   # private-use stand-in keeps the code
        out = bytearray()
        for ch in transliterate(text):
            if ch >= "\ue000":
                out.append(ord(ch) - 0xE000)
            elif ch in self.latin:
                out.append(self.latin[ch])
            else:
                raise SaveError(f"the character set has no {ch!r}")
        return bytes(out)

    def letters(self, translated: bool) -> str:
        """Every character a name for the target ROM may hold, for the page's check as it is typed."""
        return "".join(sorted(self.translated if translated else self.latin))

    def encode(self, text: str, translated: bool) -> bytes:
        """Encode a name using the target ROM glyphs."""
        codes = self.translated if translated else self.latin
        missing = sorted({ch for ch in text if ch not in codes})
        if missing:
            raise SaveError(f"{text!r}: the target ROM has no {', '.join(repr(c) for c in missing)}")
        return bytes(codes[ch] for ch in text)


@dataclass
class NameField:
    """A stored name: where it is, how long it may be, which fields are renamed with it."""
    slots: list[int]      # slots holding it (both when shared); empty outside slots
    label: str            # place, as printed and as --rename takes it
    offset: int           # file offset
    size: int             # field bytes
    limit: int            # most letters the naming screen allows there
    key: tuple            # name key: ("trainer", id, name), ("mon", personality, ...)
    pad: int = 0          # shorter names are space-padded to this length (mail)
    egg: bool = False     # an egg's name; the game replaces it at hatching
    segments: list[tuple[int, int]] = field(default_factory=list)  # file offsets and lengths; empty when contiguous
    species: int | None = None

    def where(self, newest: int) -> str:
        return self.label if not self.slots or newest in self.slots else f"slot {self.slots[0]} {self.label}"


@dataclass
class NameChange:
    field: NameField
    old: bytes
    new: bytes
    renamed: bool         # a replacement given by the user (else transliterated)


@dataclass
class Overflow:
    """A transliterated name longer than its field: all fields of one key."""
    fields: list[NameField]
    old: bytes
    latin: bytes


def find_name(fields: list[NameField], label: str, newest: int) -> NameField:
    """The field a place label names: "party 1", "box 3 slot 5 OT", "box 3", "player"; "slot N <place>" picks another slot."""
    m = re.fullmatch(r"slot (\d+) (.+)", label.strip())
    slot, place = (int(m[1]), m[2]) if m else (newest, label.strip())
    for f in fields:
        if f.label == place and (not f.slots or slot in f.slots):
            return f
    raise SaveError(f"no name at {label!r} (places are listed by --dry-run)")


def plan_names(save: SaveFile, fields: list[NameField], charset: Charset, renames: dict[tuple, bytes],
               latin: bool) -> tuple[list[NameChange], list[Overflow]]:
    """Renames by key; `latin` also transliterates names with pack letters; names that do not fit are returned."""
    changes, over = [], {}
    for f in fields:
        old = save.field_name(f)
        if f.egg:
            if f.key in renames:
                raise SaveError(f"{f.where(save.slots[0].index)} is an egg: the game names it at hatching")
            continue
        if f.key in renames:
            new = renames[f.key]
            if len(new) > f.limit:
                raise SaveError(f"{f.label}: {len(new)} letters, the field holds {f.limit}")
            if new != old:
                changes.append(NameChange(f, old, new, True))
            continue
        new = charset.transliterate(old) if latin else None
        if new is None:
            continue
        if len(new) > f.limit:
            over.setdefault(f.key, Overflow([], old, new)).fields.append(f)
        else:
            changes.append(NameChange(f, old, new, False))
    return changes, list(over.values())


def apply_names(save: SaveFile, changes: list[NameChange]) -> None:
    for c in changes:
        save.write_name(c.field, c.old, c.new)
    save.fix_checksums()


def change_kind(label: str) -> str:
    """What a name field holds, for the count breakdown."""
    if label == "player":
        return "trainer record"
    if label == "rival":
        return "rival"
    if label.endswith(" mail OT"):
        return "mail original trainer"
    if label.endswith(" OT"):
        return "original trainer of a Pokémon"
    if label.endswith(" mail"):
        return "mail author"
    if label.startswith("mail "):
        return "mail author"
    if label.startswith("link record"):
        return "link record"
    if re.fullmatch(r"box \d+", label):
        return "box name"
    return "Pokémon nickname"   # party, box slots, daycare, mail nicknames, the Hall of Fame


def change_groups(changes: list[NameChange], newest: int) -> list[dict]:
    """The changes counted by kind, in order of first appearance: [{kind, count, older_slot}]."""
    groups: dict[str, dict] = {}
    for c in changes:
        kind = change_kind(c.field.label)
        g = groups.setdefault(kind, {"kind": kind, "count": 0, "older_slot": 0})
        g["count"] += 1
        if c.field.slots and newest not in c.field.slots:
            g["older_slot"] += 1
    return list(groups.values())


def groups_text(groups: list[dict]) -> str:
    """"2 × trainer record, 6 × original trainer of a Pokémon (3 in the older save slot)"."""
    return ", ".join(f"{g['count']} × {g['kind']}" + (f" ({g['older_slot']} in the older save slot)" if g["older_slot"] else "")
                     for g in groups)



def name_table(rom: bytes, L: Layout) -> dict:
    """The species, egg and default box names of a ROM as published with a release: hex bytes."""
    species, egg, boxes = conversion_names(rom, L)
    table = {"species": [n.hex() for n in species], "egg": egg.hex()}
    if boxes:
        table["boxes"] = [[n.hex() for n in box] for box in boxes]
    return table


def names_of(table: dict) -> tuple[list[bytes], bytes, list[list[bytes]]]:
    """conversion_names from a published name table; without "boxes" boxes keep their names."""
    return ([bytes.fromhex(x) for x in table["species"]], bytes.fromhex(table["egg"]),
            [[bytes.fromhex(x) for x in box] for box in table.get("boxes", [])])


def species_defaults(tables) -> dict[int, set[bytes]]:
    """Index exact names by species, across published tables."""
    defaults: dict[int, set[bytes]] = {}
    for names, *_rest in tables:
        for species, name in enumerate(names):
            if name:
                defaults.setdefault(species, set()).add(name)
    return defaults


def default_fields(save: SaveFile, tables) -> set[int]:
    """Classify original fields before conversion or manual edits."""
    defaults = species_defaults(tables)
    return {f.offset for f in save.name_fields()
            if f.species is not None and save.field_name(f) in defaults.get(f.species, ())}


def default_matches(save: SaveFile, tables: list[tuple]) -> list[int]:
    """For each names_of table, the Pokemon of the newest slot that carry its default species name."""
    slot = save.slots[0]
    counts = [0] * len(tables)
    for _place, addr, species, egg in save.mons(slot):
        cur = name_of(save.nickname(slot, addr))
        for i, (names, *_rest) in enumerate(tables):
            if not egg and species < len(names) and cur == names[species]:
                counts[i] += 1
    return counts


def requested_names(fields: list[NameField], requested, newest: int, charset: Charset,
                    translated: bool) -> dict[tuple, bytes]:
    """Renames by key from (place, new name) pairs, in the glyph codes of the target ROM."""
    player = [f.key for f in fields if f.label == "player"]
    renames: dict[tuple, bytes] = {}
    for place, text in requested:
        f = find_name(fields, place, newest)
        if f.label != "player" and f.key in player:
            raise SaveError(f"{place} holds the player's name; rename the player")
        renames[f.key] = charset.encode(text.strip(), translated)
    return renames


def output_details(save: SaveFile, output: str) -> dict:
    """Describe the selected output container and extension."""
    if output not in ("original", "sav", "srm"):
        raise SaveError(f"unknown output format {output!r}")
    return {"format": output, "container": save.container.kind if output == "original" else "raw",
            "extension": None if output == "original" else f".{output}"}


def browser_convert(data: bytes, layout: dict, src: tuple[list[bytes], bytes], dst: tuple[list[bytes], bytes],
                    charmap: dict | None, charmap_target: dict | None = None,
                    requested: list[tuple[str, str]] = (), output: str = "original",
                    known_tables: tuple = ()) -> tuple[dict, bytes | None]:
    """main() without files or a terminal, for the save page."""
    lay = Layout.from_dict(layout)
    table = {int(k): v for k, v in charmap.items()} if charmap else None
    shared = {int(k): v for k, v in (charmap_target or {}).items()}
    save = SaveFile(data, lay)
    newest = save.slots[0].index
    newest_slot = save.slots[0]
    made = dict(zip(("source", "target"), default_matches(save, [src, dst])))
    default_offsets = default_fields(save, [src, dst, *known_tables])
    changes = convert(save, src, dst, known_tables)
    source_shared = shared if _has_target_letters(src[0], table) else None
    target_shared = shared if _has_target_letters(dst[0], table) else None
    charset = Charset(table, shared)
    fields = save.name_fields()
    latin = bool(table) and target_shared is None
    renames = requested_names(fields, requested, newest, charset, target_shared is not None)
    names, over = plan_names(save, fields, charset, renames, latin)
    old = lambda b: _decode(b, table, source_shared)   # noqa: E731
    new = lambda b: _decode(b, table, target_shared)   # noqa: E731
    changed = {c.field.key: c for c in names}
    too_long = {o.fields[0].key: o for o in over}
    defaults = {c.place: c for c in changes if c.slot == newest}
    rows, keys = [], set()
    for f in fields:
        if f.key in keys or f.slots and newest not in f.slots:
            continue
        listed = f.label in ("player", "rival") or re.fullmatch(r"(party \d+|box \d+( slot \d+)?)", f.label)
        if not (listed or f.key in changed or f.key in too_long):
            continue
        keys.add(f.key)
        cur = b"" if f.egg else save.field_name(f)
        stored, kind = cur, "kept"
        if f.label in defaults:
            stored, kind = defaults[f.label].old, "default"
        after = cur
        if f.key in too_long:
            after, kind = too_long[f.key].latin, "over"
        elif f.key in changed:
            after = changed[f.key].new
            kind = "renamed" if changed[f.key].renamed else "transliterated"
        rows.append({"place": f.where(newest), "stored": old(stored), "after": new(after), "kind": kind,
                     "limit": f.limit, "letters": len(after), "egg": f.egg,
                     "renameable": not f.egg and f.offset not in default_offsets})
    report = {
        "container": save.container.kind,
        "output": output_details(save, output),
        "slots": [{"index": s.index, "counter": s.counter} for s in save.slots],
        "player": next((r["stored"] for r in rows if r["place"] == "player"), ""),
        "party": sum(1 for c in save.mons(newest_slot) if c[0].startswith("party ")),
        "made": made, "defaults": len(changes),
        "listed_defaults": sum(1 for r in rows if r["kind"] == "default"),
        "transliterated": sum(not c.renamed for c in names), "renamed": sum(c.renamed for c in names),
        # what the counts are made of
        "renamed_groups": change_groups([c for c in names if c.renamed], newest),
        "transliterated_groups": change_groups([c for c in names if not c.renamed], newest),
        "over": [r["place"] for r in rows if r["kind"] == "over"], "rows": rows,
        "letters": charset.letters(target_shared is not None),
    }
    if over:
        return report, None
    apply_names(save, names)
    return report, save.to_bytes(output)


def browser_edit(data: bytes, layout: dict, charmap: dict | None, charmap_target: dict | None, translated: bool,
                 requested: list[tuple[str, str]] = (), output: str = "original",
                 known_tables: tuple = ()) -> tuple[dict, bytes]:
    """The same-ROM pass of the save page: names edited for the ROM the save comes from."""
    lay = Layout.from_dict(layout)
    table = {int(k): v for k, v in charmap.items()} if charmap else None
    shared = {int(k): v for k, v in (charmap_target or {}).items()}
    save = SaveFile(data, lay)
    newest = save.slots[0].index
    charset = Charset(table, shared)
    default_offsets = default_fields(save, known_tables)
    fields = save.name_fields()
    renames = requested_names(fields, requested, newest, charset, translated)
    names, _over = plan_names(save, fields, charset, renames, False)
    show = lambda b: _decode(b, table, shared if translated else None)   # noqa: E731
    changed = {c.field.key: c.new for c in names}
    rows, keys = [], set()
    for f in fields:
        if f.key in keys or f.slots and newest not in f.slots:
            continue
        keys.add(f.key)
        cur = b"" if f.egg else save.field_name(f)
        after = changed.get(f.key, cur)
        rows.append({"place": f.where(newest), "stored": show(cur), "after": show(after),
                     "kind": "renamed" if f.key in changed else "kept", "limit": f.limit, "letters": len(after),
                     "egg": f.egg, "renameable": not f.egg and f.offset not in default_offsets})
    report = {
        "container": save.container.kind,
        "output": output_details(save, output),
        "slots": [{"index": s.index, "counter": s.counter} for s in save.slots],
        "player": next((r["stored"] for r in rows if r["place"] == "player"), ""),
        "party": sum(1 for c in save.mons(save.slots[0]) if c[0].startswith("party ")),
        "renamed": len(names), "renamed_groups": change_groups(names, newest), "over": [], "rows": rows,
        "letters": charset.letters(translated),
    }
    apply_names(save, names)
    return report, save.to_bytes(output)



def _decode(b: bytes, table: dict | None, translated: dict | None = None) -> str:
    """Decode names with the selected ROM's shared glyphs."""
    if not table:
        return b.hex(" ")
    over = translated or {}
    return "".join(over.get(x) or table.get(x, f"[{x:02X}]") for x in b)


def _has_target_letters(names: list[bytes], table: dict | None) -> bool:
    """Identify pack glyphs in the ROM's species names."""
    return bool(table) and any(_target_letter(table.get(x, "")) for n in names for x in n)


def _ask(o: Overflow, newest: int, charset: Charset, translated: bool, show) -> bytes:
    """Ask on the terminal for a name that fits."""
    from ..core.terminal import line
    f = o.fields[0]
    line("field", f.where(newest))
    line("before", show(o.old, True))
    line("after", show(o.latin, False))
    line("length", f"{len(o.latin)} letters")
    line("limit", f"{f.limit} letters")
    if f.label == "player":
        line("trainers", "linked")
    while True:
        try:
            line("replacement", "required")
            text = input().strip()
        except EOFError:
            raise SaveError("no replacement given") from None
        try:
            new = charset.encode(text, translated)
        except SaveError as e:
            line("error", e)
            continue
        if 0 < len(new) <= f.limit:
            return new
        line("minimum", "1 letter")
        line("limit", f"{f.limit} letters")


def _option(f: NameField, newest: int) -> str:
    place = f.where(newest)
    if place == "player":
        return "--player-name NEW"
    m = re.fullmatch(r"box (\d+)", place)
    return f'--box-name "{m[1]}=NEW"' if m else f'--rename "{place}=NEW"'


def edited_name(name: str) -> str:
    """The file name the save page gives an edited save: "-edited" before the input's extension."""
    m = re.search(r"\.[A-Za-z][A-Za-z0-9]*$", name)
    ext = m[0] if m else ".sav"
    return f"{(name[:m.start()] if m else name) or 'save'}-edited{ext}"


def _edit(a, ap, layout: dict, table: dict | None, shared: dict, requested: list[tuple[str, str]]) -> int:
    """main() with --edit: the save page's edit pass (browser_edit), so the same edit gives the same bytes."""
    from ..core.terminal import line
    data = Path(a.save).read_bytes()
    roms = [r for r in (a.from_rom, a.to_rom) if r]
    if not a.game and not roms:
        ap.error("--edit takes the game from --game, or from --from-rom and --to-rom")
    try:
        rom, matched = None, None
        if a.game:
            translated = a.game == "translated"
        else:
            lay = Layout.from_dict(layout)
            names = [conversion_names(Path(r).read_bytes(), lay) for r in roms]
            pick = 0
            if len(roms) > 1:
                counts = default_matches(SaveFile(data, lay), names)
                matched = max(counts)
                if not matched or counts.count(matched) > 1:
                    raise SaveError("no default name in this save tells the games apart: choose it with --game")
                pick = counts.index(matched)
            rom, translated = roms[pick], _has_target_letters(names[pick][0], table)
        report, out = browser_edit(data, layout, table, shared, translated, requested)
    except SaveError as e:
        line("error", e, file=sys.stderr)
        return 1
    line("container", CONTAINERS[report["container"]])
    for slot in report["slots"]:
        line("slot", slot["index"])
        line("save", slot["counter"])
    line("game", "translated" if translated else "original")
    if rom:
        line("rom", rom)
    if matched:
        line("matches", f"{matched} names")
    for r in report["rows"]:
        if r["kind"] != "renamed" and not a.dry_run:
            continue
        line("field", r["place"])
        if r["egg"]:   # the game shows its own egg word
            line("status", "egg")
            continue
        if r["kind"] == "renamed":
            line("before", r["stored"])
            line("after", r["after"])
        else:
            line("name", r["stored"])
        line("limit", f"{r['limit']} letters")
        line("status", r["kind"])
    line("renamed", f"{report['renamed']} names")
    if report["renamed"] > 1:   # one name makes many entries
        for group in report["renamed_groups"]:
            line("group", group["kind"])
            line("count", f"{group['count']} names")
            line("older", f"{group['older_slot']} names")
    if a.dry_run:
        return 0
    path = a.out or str(Path(a.save).with_name(edited_name(Path(a.save).name)))
    Path(path).write_bytes(out)
    line("wrote", path)
    return 0


def main(argv=None, layout: dict | None = None, charmap: dict | None = None,
         charmap_target: dict | None = None, interactive: bool | None = None) -> int:
    from ..core.terminal import StatusParser, line
    ap = StatusParser(description="Convert Gen 3 saves between base and translated ROMs: "
                                             "default species names map to the other language, typed names "
                                             "use the pack transliteration, eggs keep their name; "
                                             "--edit: edit names only, for the game of the save; "
                                             "standalone use prints no hint lines.")
    ap.add_argument("save", help="input save: a raw 128 KiB dump or a container listed in the error for any other file")
    ap.add_argument("out", nargs="?", help="output save, in the input's container (omit with --dry-run; with --edit, "
                                           "<save>-edited.<ext> beside the input by default)")
    ap.add_argument("--from-rom", help="ROM the save was made with (with --edit: one of the games to detect)")
    ap.add_argument("--to-rom", help="ROM the save is converted for (with --edit: the other game to detect)")
    ap.add_argument("--edit", action="store_true",
                    help="edit names only, for the game the save was made with: the requested names are written")
    ap.add_argument("--game", choices=("original", "translated"),
                    help="with --edit: the game the save was made with; without it, the one of --from-rom and "
                         "--to-rom whose default names the save holds")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing; with --edit, list every name")
    ap.add_argument("--player-name", metavar="NEW",
                    help="rename the player; the OT name of every Pokemon with the player's trainer id and name "
                         "changes with it")
    ap.add_argument("--rename", metavar="PLACE=NEW", action="append", default=[],
                    help='rename the name at a place the report lists, e.g. "party 1=Sparky", '
                         '"box 3 slot 5=Rex", "rival=Gary" (repeatable)')
    ap.add_argument("--box-name", metavar="N=NEW", action="append", default=[], help="rename box N (repeatable)")
    a = ap.parse_args(argv)
    lay = Layout.from_dict(layout or LAYOUT)
    table = {int(k): v for k, v in (charmap or {}).items()} if charmap else None
    shared = {int(k): v for k, v in (charmap_target or {}).items()}
    requested = []
    for opt, values, prefix in (("--rename", a.rename, ""), ("--box-name", a.box_name, "box ")):
        for x in values:
            place, sep, text = x.partition("=")
            if not sep:
                ap.error(f"{opt} takes {'PLACE' if not prefix else 'N'}=NEW; got {x!r}")
            requested.append((prefix + place.strip(), text))
    if a.player_name is not None:
        requested.append(("player", a.player_name))
    if a.edit:
        return _edit(a, ap, layout or LAYOUT, table, shared, requested)
    if a.game:
        ap.error("--game takes the game of --edit")
    if not a.from_rom or not a.to_rom:
        ap.error("converting takes --from-rom and --to-rom")
    save = SaveFile(Path(a.save).read_bytes(), lay)
    src = conversion_names(Path(a.from_rom).read_bytes(), lay)
    dst = conversion_names(Path(a.to_rom).read_bytes(), lay)
    changes = convert(save, src, dst)
    source_shared = shared if _has_target_letters(src[0], table) else None
    target_shared = shared if _has_target_letters(dst[0], table) else None
    line("container", save.container.name)
    for slot in save.slots:
        line("slot", slot.index)
        line("save", slot.counter)
    for c in changes:
        where = f"slot {c.slot} " if c.slot is not None else ""
        line("field", f"{where}{c.place}")
        line("kind", "box" if c.box else "egg" if c.egg else "species")
        if not c.box and not c.egg:
            line("species", c.species)
        line("before", _decode(c.old, table, source_shared))
        line("after", _decode(c.new, table, target_shared))
        line("trailing", "changed" if c.trailing_changed else "unchanged")
    line("converted", f"{len(changes)} names")
    # typed names: Latin for a Latin-named ROM, renames on request
    charset = Charset(table, shared)
    fields = save.name_fields()
    newest = save.slots[0].index
    latin = bool(table) and target_shared is None
    show = lambda b, old: _decode(b, table, source_shared if old else target_shared)  # noqa: E731
    renames: dict[tuple, bytes] = {}
    try:
        player = [f.key for f in fields if f.label == "player"]
        for place, text in requested:
            f = find_name(fields, place, newest)
            if f.label != "player" and f.key in player:
                raise SaveError(f"{place} holds the player's name; --player-name renames it")
            renames[f.key] = charset.encode(text.strip(), target_shared is not None)
        while True:
            names, over = plan_names(save, fields, charset, renames, latin)
            if not over:
                break
            if interactive is None:
                interactive = sys.stdin.isatty()
            if not interactive or a.dry_run:
                line("error", "name overflow", file=sys.stderr)
                line("overflow", f"{len(over)} names", file=sys.stderr)
                for o in over:
                    f = o.fields[0]
                    line("option", _option(f, newest), file=sys.stderr)
                    line("before", show(o.old, True), file=sys.stderr)
                    line("after", show(o.latin, False), file=sys.stderr)
                    line("length", f"{len(o.latin)} letters", file=sys.stderr)
                    line("limit", f"{f.limit} letters", file=sys.stderr)
                return 1
            for o in over:
                renames[o.fields[0].key] = _ask(o, newest, charset, target_shared is not None, show)
    except SaveError as e:
        line("error", e, file=sys.stderr)
        return 1
    for c in names:
        where = f"slot {'/'.join(map(str, c.field.slots))} " if c.field.slots else ""
        line("field", f"{where}{c.field.label}")
        line("before", show(c.old, True))
        line("after", show(c.new, False))
        line("status", "renamed" if c.renamed else "transliterated")
    line("transliterated", f"{sum(not c.renamed for c in names)} names")
    line("renamed", f"{sum(c.renamed for c in names)} names")
    for word, subset in (("transliterated", [c for c in names if not c.renamed]), ("renamed", [c for c in names if c.renamed])):
        if len(subset) > 1:   # one name makes many entries
            line("status", word)
            for group in change_groups(subset, newest):
                line("group", group["kind"])
                line("count", f"{group['count']} names")
                line("older", f"{group['older_slot']} names")
    apply_names(save, names)
    if a.dry_run:
        return 0
    if not a.out:
        ap.error("output path required without --dry-run")
    Path(a.out).write_bytes(save.to_bytes())
    line("wrote", a.out)
    return 0


def standalone_source(layout: dict, charmap: dict[int, str], charmap_target: dict[int, str] | None = None) -> str:
    """This module as a single script with the layout and the byte-to-text tables embedded."""
    import inspect
    from ..core import terminal
    src = Path(__file__).read_text(encoding="utf-8")
    src = src.replace("    from ..core.terminal import StatusParser, line\n", "")
    src = src.replace("    from ..core.terminal import line\n", "")
    # the whole module: line() reaches its error helpers
    definitions = inspect.getsource(terminal).replace("from __future__ import annotations\n", "", 1)
    src = src.replace("EOS = 0xFF", definitions + "\nEOS = 0xFF", 1)
    src = src.replace("SAVE_RULES = None  # filled by standalone generation",
                      "SAVE_RULES = " + repr(language_rules()), 1)
    src = src.replace("LAYOUT = None  # filled in by the standalone release script",
                      "LAYOUT = " + json.dumps(layout, indent=1, ensure_ascii=False) +
                      "\nCHARMAP = " + json.dumps({str(k): v for k, v in charmap.items()}, ensure_ascii=False) +
                      "\nCHARMAP_TARGET = " + json.dumps({str(k): v for k, v in (charmap_target or {}).items()},
                                                     ensure_ascii=False),
                      1)  # the module-level line, not the copy in this function
    return src + """

if __name__ == "__main__":
    try:
        sys.exit(main(charmap=CHARMAP, charmap_target=CHARMAP_TARGET))
    except Exception as exc:
        line("error", str(exc) or type(exc).__name__, file=sys.stderr)
        sys.exit(1)
"""
