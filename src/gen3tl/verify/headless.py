"""Headless GBA emulation through a libretro core (mGBA built from source)."""

from __future__ import annotations

import ctypes as C
import struct
from pathlib import Path

from ..check.render import write_png
from ..core.rom import ROM_BASE

# machine-local, under the project the command runs in
TOOLS = Path("build") / "tools"
CORE_DEFAULT = TOOLS / "mgba-build" / "mgba_libretro.dylib"
STATE_DIR = Path("verify") / "states"

# libretro constants
ENV_GET_CAN_DUPE = 3
ENV_GET_SYSTEM_DIRECTORY = 9
ENV_SET_PIXEL_FORMAT = 10
ENV_GET_SAVE_DIRECTORY = 31
PIXEL_0RGB1555, PIXEL_XRGB8888, PIXEL_RGB565 = 0, 1, 2
MEMORY_SAVE_RAM, MEMORY_SYSTEM_RAM = 0, 2
STATE_IWRAM, STATE_EWRAM = 0x19000, 0x21000   # offsets in an mGBA savestate
# Scratch for injected scripts; zero in every checkpoint.
# Checkpoints: verify/states e1-e8.
SCRIPT_RAM = 0x0203FF00
PARTY_RAM, PARTY_COUNT_RAM = 0x02024284, 0x02024029   # gPlayerParty and its count (vanilla FireRed)
JOYPAD = {"B": 0, "SELECT": 2, "START": 3, "UP": 4, "DOWN": 5, "LEFT": 6, "RIGHT": 7,
          "A": 8, "L": 10, "R": 11}


class GameInfo(C.Structure):
    _fields_ = [("path", C.c_char_p), ("data", C.c_void_p), ("size", C.c_size_t), ("meta", C.c_char_p)]


ENV_CB = C.CFUNCTYPE(C.c_bool, C.c_uint, C.c_void_p)
VIDEO_CB = C.CFUNCTYPE(None, C.c_void_p, C.c_uint, C.c_uint, C.c_size_t)
AUDIO_CB = C.CFUNCTYPE(None, C.c_int16, C.c_int16)
AUDIO_BATCH_CB = C.CFUNCTYPE(C.c_size_t, C.c_void_p, C.c_size_t)
POLL_CB = C.CFUNCTYPE(None)
STATE_CB = C.CFUNCTYPE(C.c_int16, C.c_uint, C.c_uint, C.c_uint, C.c_uint)


class HeadlessError(RuntimeError):
    pass


class Gba:
    """One emulator instance (a libretro core can host one game per process)."""

    def __init__(self, rom: str | Path, core: str | Path = CORE_DEFAULT, save: str | Path | None = None):
        self.core_path = Path(core).resolve()
        if not self.core_path.exists():
            raise HeadlessError(f"core not built: {self.core_path}")
        self.lib = C.CDLL(str(self.core_path))
        self.pixel_format = PIXEL_0RGB1555
        self.frame = None
        self.width = self.height = 0
        self.keys: set[str] = set()
        self.frames_run = 0
        self._sysdir = C.c_char_p(str(TOOLS.resolve()).encode())
        # keep callback objects alive
        self._cbs = [ENV_CB(self._env), VIDEO_CB(self._video), AUDIO_CB(lambda l, r: None),
                     AUDIO_BATCH_CB(lambda d, n: n), POLL_CB(lambda: None), STATE_CB(self._input)]
        L = self.lib
        L.retro_set_environment(self._cbs[0])
        L.retro_set_video_refresh(self._cbs[1])
        L.retro_set_audio_sample(self._cbs[2])
        L.retro_set_audio_sample_batch(self._cbs[3])
        L.retro_set_input_poll(self._cbs[4])
        L.retro_set_input_state(self._cbs[5])
        L.retro_init()
        self._rom_bytes = Path(rom).read_bytes()
        self._rom_buf = C.create_string_buffer(self._rom_bytes, len(self._rom_bytes))
        info = GameInfo(str(Path(rom).resolve()).encode(), C.cast(self._rom_buf, C.c_void_p),
                        len(self._rom_bytes), None)
        L.retro_load_game.restype = C.c_bool
        if not L.retro_load_game(C.byref(info)):
            raise HeadlessError("retro_load_game failed")
        L.retro_get_memory_data.restype = C.c_void_p
        L.retro_get_memory_size.restype = C.c_size_t
        L.retro_serialize_size.restype = C.c_size_t
        L.retro_serialize.restype = C.c_bool
        L.retro_unserialize.restype = C.c_bool
        if save is not None:
            self.load_save_ram(Path(save).read_bytes())

    def _env(self, cmd, data):
        if cmd == ENV_SET_PIXEL_FORMAT:
            self.pixel_format = C.cast(data, C.POINTER(C.c_int))[0]
            return True
        if cmd in (ENV_GET_SYSTEM_DIRECTORY, ENV_GET_SAVE_DIRECTORY):
            C.cast(data, C.POINTER(C.c_char_p))[0] = self._sysdir.value
            return True
        if cmd == ENV_GET_CAN_DUPE:
            C.cast(data, C.POINTER(C.c_bool))[0] = True
            return True
        return False

    def _video(self, data, width, height, pitch):
        if not data:
            return
        self.width, self.height = width, height
        self.frame = (C.string_at(data, pitch * height), pitch)

    def _input(self, port, device, index, ident):
        if port != 0 or device != 1:
            return 0
        return 1 if any(JOYPAD.get(k) == ident for k in self.keys) else 0

    def run(self, frames: int = 1) -> None:
        for _ in range(frames):
            self.lib.retro_run()
            self.frames_run += 1

    def press(self, keys: str, hold: int = 4, after: int = 12) -> None:
        self.keys = {k.strip().upper() for k in keys.split(",") if k.strip()}
        self.run(hold)
        self.keys = set()
        self.run(after)

    def reset(self) -> None:
        self.lib.retro_reset()

    def save_state(self) -> bytes:
        n = self.lib.retro_serialize_size()
        buf = C.create_string_buffer(n)
        if not self.lib.retro_serialize(buf, n):
            raise HeadlessError("serialize failed")
        return buf.raw

    def load_state(self, blob: bytes) -> None:
        buf = C.create_string_buffer(blob, len(blob))
        if not self.lib.retro_unserialize(buf, len(blob)):
            raise HeadlessError("unserialize failed")

    def save_ram(self) -> bytes:
        p = self.lib.retro_get_memory_data(MEMORY_SAVE_RAM)
        n = self.lib.retro_get_memory_size(MEMORY_SAVE_RAM)
        return C.string_at(p, n) if p and n else b""

    def load_save_ram(self, blob: bytes) -> None:
        p = self.lib.retro_get_memory_data(MEMORY_SAVE_RAM)
        n = self.lib.retro_get_memory_size(MEMORY_SAVE_RAM)
        if not p or not n:
            raise HeadlessError("core exposes no save RAM")
        C.memmove(p, blob, min(n, len(blob)))

    def write_ewram(self, addr: int, data: bytes) -> None:
        """Write bytes into EWRAM, through a savestate as ewram() reads it."""
        st = bytearray(self.save_state())
        o = STATE_EWRAM + addr - 0x02000000
        st[o:o + len(data)] = data
        self.load_state(bytes(st))

    def ewram(self) -> bytes:
        """EWRAM (256 KiB at 0x02000000)."""
        return self.save_state()[STATE_EWRAM:STATE_EWRAM + 0x40000]

    def run_script(self, script: bytes, addr: int = SCRIPT_RAM) -> None:
        """Make the game run an event script: write it to free EWRAM and start the FireRed script context on it."""
        st = bytearray(self.save_state())
        o = STATE_EWRAM + addr - 0x02000000
        st[o:o + len(script)] = script
        st[STATE_IWRAM + 0xEA8] = 0                  # status: running
        ctx = STATE_IWRAM + 0xEB0
        st[ctx] = 0                                  # stack depth
        st[ctx + 1] = 1                              # mode: bytecode
        st[ctx + 8:ctx + 12] = addr.to_bytes(4, "little")
        self.load_state(bytes(st))

    def pixels(self) -> list[list[tuple[int, int, int]]]:
        if self.frame is None:
            raise HeadlessError("no frame rendered yet")
        raw, pitch = self.frame
        rows = []
        for y in range(self.height):
            row = []
            base = y * pitch
            for x in range(self.width):
                if self.pixel_format == PIXEL_XRGB8888:
                    b, g, r = raw[base + 4 * x], raw[base + 4 * x + 1], raw[base + 4 * x + 2]
                else:
                    v = raw[base + 2 * x] | (raw[base + 2 * x + 1] << 8)
                    if self.pixel_format == PIXEL_RGB565:
                        r, g, b = (v >> 11) << 3, ((v >> 5) & 0x3F) << 2, (v & 0x1F) << 3
                    else:
                        r, g, b = ((v >> 10) & 0x1F) << 3, ((v >> 5) & 0x1F) << 3, (v & 0x1F) << 3
                row.append((r, g, b))
            rows.append(row)
        return rows

    def screenshot(self, path: str | Path, scale: int = 2) -> Path:
        rows = self.pixels()
        out = []
        for row in rows:
            r = [px for px in row for _ in range(scale)]
            out.extend([r] * scale)
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_png(path, out)
        return path

    def close(self) -> None:
        self.lib.retro_unload_game()
        self.lib.retro_deinit()


def side_by_side(left: Path, right: Path, out: Path, gap: int = 8) -> Path:
    """Combine two PNG screenshots horizontally (both written by this module)."""
    import struct
    import zlib

    def read_png(p: Path):
        data = p.read_bytes()
        pos = 8
        w = h = 0
        idat = b""
        while pos < len(data):
            n = struct.unpack(">I", data[pos:pos + 4])[0]
            tag = data[pos + 4:pos + 8]
            body = data[pos + 8:pos + 8 + n]
            if tag == b"IHDR":
                w, h = struct.unpack(">II", body[:8])
            elif tag == b"IDAT":
                idat += body
            pos += 12 + n
        raw = zlib.decompress(idat)
        rows = []
        stride = 1 + 3 * w
        for y in range(h):
            line = raw[y * stride + 1:(y + 1) * stride]
            rows.append([tuple(line[3 * x:3 * x + 3]) for x in range(w)])
        return rows

    a, b = read_png(left), read_png(right)
    h = max(len(a), len(b))
    bg = (255, 255, 255)
    rows = []
    for y in range(h):
        ra = a[y] if y < len(a) else [bg] * len(a[0])
        rb = b[y] if y < len(b) else [bg] * len(b[0])
        rows.append(ra + [bg] * gap + rb)
    write_png(out, rows)
    return out


def record_presses(rom: Path, state: str, presses: int, outdir: Path, key: str = "A",
                   after: int = 90) -> list[Path]:
    """Load a checkpoint, press KEY PRESSES times and screenshot after each press."""
    g = Gba(rom)
    g.load_state((STATE_DIR / f"{state}.state").read_bytes())
    g.run(2)
    shots = []
    for i in range(presses):
        g.press(key, 4, after)
        shots.append(g.screenshot(outdir / f"{i:03d}.png"))
    g.close()
    return shots


# step -> (fewest, most) arguments
STEPS = {"wait": (1, 1), "press": (1, 3), "shot": (1, 1), "state": (2, 2), "statefile": (2, 2),
         "savefile": (1, 1), "include": (1, 1), "partymon": (4, 4), "special": (1, 1), "goto": (1, 1),
         "wallegg": (1, 1), "giveegg": (1, 1), "eggcycles": (1, 1), "stepcounter": (1, 1),
         "writesave": (1, 1), "hatched": (3, 3)}


def parse_scenario(script: str, directory: Path | None = None) -> list[tuple[str, list[str]]]:
    """The steps of a scenario as (step, arguments), `repeat N` written out."""
    steps, saved = [], set()
    for number, raw in enumerate(script.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        rep = 1
        if parts[0] == "repeat" and len(parts) > 2 and parts[1].isdigit():
            rep, parts = int(parts[1]), parts[2:]
        if parts[0] not in STEPS:
            raise HeadlessError(f"line {number}: unknown scenario step {line!r}")
        fewest, most = STEPS[parts[0]]
        if not fewest <= len(parts) - 1 <= most:
            count = f"{fewest}" if fewest == most else f"{fewest} to {most}"
            raise HeadlessError(f"line {number}: {parts[0]} takes {count} argument{'s' * (most > 1)}: {line!r}")
        if parts[0] == "include":
            if directory is None:
                raise HeadlessError(f"line {number}: include needs the scenario's directory: {line!r}")
            path = directory / parts[1]
            steps += parse_scenario(path.read_text(encoding="utf-8"), path.parent) * rep
            saved = {args[0] for op, args in steps if op == "writesave"}
            continue
        if parts[0] == "writesave":
            saved.add(parts[1])
        if parts[0] == "hatched" and (parts[1] not in saved or not (parts[2] + parts[3]).isdigit()):
            raise HeadlessError(f"line {number}: hatched takes a save written before, a slot and a species")
        steps += [(parts[0], parts[1:])] * rep
    return steps


def hatch_checks(steps: list[tuple[str, list[str]]]) -> list[tuple[str, int, int]]:
    """The `hatched` steps: (saved game name, party slot, species)."""
    return [(args[0], int(args[1]), int(args[2])) for step, args in steps if step == "hatched"]


def run_scenario(rom: Path, script: str, outdir: Path, prefix: str, states: dict | None = None,
                 layout: dict | None = None, directory: Path | None = None, observer=None) -> list[Path]:
    """Run a scenario and return the screenshots taken."""
    steps = parse_scenario(script, directory)
    states = states if states is not None else {}
    g = Gba(rom)
    shots = []
    for op, args in steps:
        if op == "wait":
            g.run(int(args[0]))
        elif op == "press":
            hold = int(args[1]) if len(args) > 1 else 4
            after = int(args[2]) if len(args) > 2 else 12
            g.press(args[0], hold, after)
        elif op == "shot":
            shots.append(g.screenshot(outdir / f"{prefix}-{args[0]}.png"))
            if observer is not None:
                try:
                    observer(g, args[0])
                except Exception:
                    g.close()
                    raise
        elif op == "state":
            if args[0] == "save":
                states[args[1]] = g.save_state()
            else:
                g.load_state(states[args[1]])
        elif op == "statefile":
            f = STATE_DIR / f"{args[1]}.state"
            if args[0] == "save":
                STATE_DIR.mkdir(parents=True, exist_ok=True)
                f.write_bytes(g.save_state())
            else:
                g.load_state(f.read_bytes())
        elif op == "savefile":
            g.load_save_ram(Path(args[0]).read_bytes())
            g.reset()
        elif op == "partymon":
            from .savefixture import TestMon, party_pokemon
            party = (layout or {}).get("party")
            if not party:
                raise HeadlessError("partymon: the profile's [save] table has no party facts")
            slot = int(args[0]) - 1
            mon = TestMon("party", slot, int(args[1], 0), b"", personality=int(args[2], 0), otid=int(args[3], 0))
            g.write_ewram(PARTY_RAM + slot * party["stride"], party_pokemon(mon, b"\xbb"))
            if g.ewram()[PARTY_COUNT_RAM - 0x02000000] <= slot:
                g.write_ewram(PARTY_COUNT_RAM, bytes([slot + 1]))
        elif op == "special":
            g.run_script(b"\x25" + struct.pack("<H", int(args[0], 0)) + b"\x02")
        elif op == "goto":
            g.run_script(b"\x05" + struct.pack("<I", int(args[0], 0) | ROM_BASE))
        elif op == "wallegg":
            hatch = (layout or {}).get("egg_hatch")
            if not hatch or "eggs_waiting" not in hatch:
                raise HeadlessError("wallegg: the profile's [save.egg_hatch] table has no eggs_waiting fact")
            for _ in range(int(args[0])):
                if g.ewram()[hatch["eggs_waiting"] - 0x02000000]:
                    break
                g.press("DOWN" if _ % 2 == 0 else "UP", 16, 4)
            else:
                raise HeadlessError(f"wallegg: no egg after {args[0]} steps")
        elif op == "giveegg":
            g.run_script(bytes([0x7A]) + struct.pack("<H", int(args[0])) + b"\x02")   # giveegg; end
        elif op in ("eggcycles", "stepcounter"):
            hatch = (layout or {}).get("egg_hatch")
            if not hatch:
                raise HeadlessError(f"{op}: the profile's [save] table has no egg_hatch facts")
            if op == "stepcounter":
                g.write_ewram(hatch["step_counter"], bytes([int(args[0])]))
                continue
            party, ram = layout["party"], g.ewram()
            for i in range(ram[PARTY_COUNT_RAM - 0x02000000]):
                rec = PARTY_RAM + i * party["stride"]
                ivs, = struct.unpack_from("<I", ram, rec - 0x02000000 + party["ivs"])
                if ivs >> 30 & 1:
                    g.write_ewram(rec + hatch["cycles"], bytes([int(args[0])]))
        elif op == "writesave":
            path = outdir / f"{prefix}-{args[0]}.sav"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(g.save_ram())
    g.close()
    return shots
