"""Sprite fonts: letters drawn from a tile sheet through a byte-to-tile table."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..core.notes import has_prose
from ..check.layout import SpriteMeasure
from ..core.charmap import BASE_CHARS
from ..core.rom import ROM_BASE, Rom, RomEvidence, ptr_bytes
from ..data.pokedexlearnsets import METHODS
from .codereplace import Patch, parse_patch
from .font import BACKGROUND, INK, OUTSIDE, SHADOW
from .graphics import ALIGN

TILE = 32          # bytes of a 4bpp tile
TABLE = 256        # one entry per byte value
KEYS = {"name", "style", "cell_rows", "sheet", "tiles", "pointers", "order", "aliases", "fallback",
        "ink", "shadow", "background", "lookup", "evidence"}
OPTIONAL = {"advance", "patches", "windows", "pair"}
STEP_KEYS = {"shared", "advances", "routine", "calls"}
PATCH_KEYS = {"guard", "code"}
WINDOW_KEYS = {"table", "width", "where"}
PAIR_KEYS = {"width", "where", "encounters", "learnsets", "evidence"}
PAIR_TABLES = ("moves", "abilities")   # drawn in this order
ROUTINE_KEYS = {"code", "words"}
ADDRESS_WORDS = ("table", "sheet", "advance")
VALUE_WORDS = ("tiles", "shadow", "step")
BYTE_OF = {c: b for b, c in BASE_CHARS.items()}


class SpriteFontError(ValueError):
    pass


@dataclass
class NameWindow:
    """A canvas that shows the names of a table, each followed by suffix."""
    table: str
    width: int
    where: str
    suffix: str = ""


@dataclass
class PairRule:
    """A move and an ability drawn one after the other for a species."""
    width: int
    where: str
    encounters: list[str]
    learnsets: list[str]
    evidence: str


@dataclass
class SpriteFont:
    name: str
    style: str
    cell_rows: int
    sheet: int
    tiles: int
    pointers: list[int]
    order: str
    aliases: dict[str, str]
    fallback: int
    ink: int
    shadow: int
    background: int
    lookup: Patch
    evidence: str
    advance: int = 8                                         # px per character
    shared: int = 0
    advances: dict[str, int] = field(default_factory=dict)
    routine: bytes = b""
    words: list[str] = field(default_factory=list)
    calls: list[Patch] = field(default_factory=list)
    patches: list[Patch] = field(default_factory=list)
    windows: list[NameWindow] = field(default_factory=list)
    pair: PairRule | None = None

    @property
    def stepped(self) -> bool:
        return bool(self.routine)


def _int(entry: dict, key: str, lo: int, hi: int, what: str = "") -> int:
    value = entry.get(key)
    if type(value) is not int or not lo <= value <= hi:
        raise SpriteFontError(f"{what or 'sprite font'}: {key} must be an integer in {lo}..{hi}")
    return value


def _hex(value, what: str, key: str) -> bytes:
    try:
        return bytes.fromhex(value)
    except (TypeError, ValueError):
        raise SpriteFontError(f"{what}: {key} must be hexadecimal") from None


def _edit(entry, what: str) -> Patch:
    if not isinstance(entry, dict) or set(entry) != PATCH_KEYS:
        raise SpriteFontError(f"{what} needs exactly {', '.join(sorted(PATCH_KEYS))}")
    guard = RomEvidence.parse(entry["guard"], what, SpriteFontError)
    code = _hex(entry["code"], what, "code")
    if not code or len(code) != guard.size:
        raise SpriteFontError(f"{what}: code must replace the original bytes one for one")
    return Patch(guard, code)


def parse(entry: dict) -> SpriteFont:
    """A [[sprite_font]] table -> SpriteFont, every field checked."""
    if not isinstance(entry, dict) or not KEYS <= set(entry) or set(entry) - KEYS - OPTIONAL - STEP_KEYS:
        raise SpriteFontError(f"sprite font needs {', '.join(sorted(KEYS))}")
    name = entry["name"]
    if not isinstance(name, str) or not name:
        raise SpriteFontError("sprite font name must be text")
    what = f"sprite font {name}"
    for key in ("style", "order", "evidence"):
        if not has_prose(entry[key]):
            raise SpriteFontError(f"{what}: {key} must be text")
    tiles = _int(entry, "tiles", 1, TABLE, what)
    pointers = entry["pointers"]
    if (not isinstance(pointers, list) or not pointers
            or any(type(p) is not int or p < 0 or p % 4 for p in pointers)):
        raise SpriteFontError(f"{what}: pointers must be aligned ROM offsets")
    order = entry["order"]
    if len(order) > tiles or len(set(order)) != len(order):
        raise SpriteFontError(f"{what}: order needs distinct characters, at most one per tile")
    aliases = entry["aliases"]
    if (not isinstance(aliases, dict)
            or any(not isinstance(k, str) or len(k) != 1 or k in order
                   or not isinstance(v, str) or v not in order
                   for k, v in aliases.items())):
        raise SpriteFontError(f"{what}: aliases map new characters to characters of order")
    font = SpriteFont(name, entry["style"], _int(entry, "cell_rows", 1, 8, what),
                      _int(entry, "sheet", 0, (1 << 25) - 1, what), tiles, list(pointers), order, dict(aliases),
                      _int(entry, "fallback", 0, tiles - 1, what), _int(entry, "ink", 0, 15, what),
                      _int(entry, "shadow", 0, 15, what), _int(entry, "background", 0, 15, what),
                      parse_patch(entry["lookup"], f"{what} lookup", PATCH_KEYS, SpriteFontError), entry["evidence"])
    unknown = [c for c in order + "".join(aliases) if c not in BYTE_OF]
    if unknown:
        raise SpriteFontError(f"{what}: {''.join(unknown)} not in the base character table")
    if "advance" in entry:
        font.advance = _int(entry, "advance", 1, 8, what)
    if "patches" in entry:
        if not isinstance(entry["patches"], list) or not entry["patches"]:
            raise SpriteFontError(f"{what}: patches must list code replaced in place")
        font.patches = [_edit(e, f"{what} patch") for e in entry["patches"]]
    if STEP_KEYS & set(entry):
        _parse_step(font, entry, what)
    if "windows" in entry:
        font.windows = _windows(entry["windows"], what)
    if "pair" in entry:
        font.pair = _pair(entry["pair"], what)
        if not set(PAIR_TABLES) <= {w.table for w in font.windows}:
            raise SpriteFontError(f"{what}: pair needs the windows of {' and '.join(PAIR_TABLES)}")
    spans = sorted((p.at, p.at + p.guard.size) for p in [font.lookup] + font.calls + font.patches)
    if any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
        raise SpriteFontError(f"{what}: replaced code spans overlap")
    return font


def _parse_step(font: SpriteFont, entry: dict, what: str) -> None:
    if not STEP_KEYS | {"advance"} <= set(entry):
        raise SpriteFontError(f"{what}: a per-glyph step needs {', '.join(sorted(STEP_KEYS | {'advance'}))}")
    font.shared = _int(entry, "shared", 0, 7, what)
    advances = entry["advances"]
    if (not isinstance(advances, dict)
            or any(k not in BYTE_OF or type(v) is not int or not 0 <= v <= 16 for k, v in advances.items())):
        raise SpriteFontError(f"{what}: advances map base characters to steps of 0..16")
    font.advances = dict(advances)
    routine = entry["routine"]
    if not isinstance(routine, dict) or set(routine) != ROUTINE_KEYS:
        raise SpriteFontError(f"{what}: routine needs exactly {', '.join(sorted(ROUTINE_KEYS))}")
    font.routine = _hex(routine["code"], what, "routine code")
    words = routine["words"]
    if (not isinstance(words, list) or len(set(words)) != len(words)
            or any(w not in ADDRESS_WORDS + VALUE_WORDS for w in words)):
        raise SpriteFontError(f"{what}: routine words name {', '.join(ADDRESS_WORDS + VALUE_WORDS)}")
    if not font.routine or len(font.routine) % 4:
        raise SpriteFontError(f"{what}: routine code must fill whole words")
    font.words = list(words)
    calls = entry["calls"]
    if not isinstance(calls, list) or not calls:
        raise SpriteFontError(f"{what}: calls must list the code that reaches the routine")
    font.calls = [parse_patch(c, f"{what} call", PATCH_KEYS | {"entry"}, SpriteFontError) for c in calls]
    if any(c.entry >= len(font.routine) for c in font.calls):
        raise SpriteFontError(f"{what}: a call enters past the routine code")


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _where_keys(value: dict, what: str) -> set[str]:
    localized = {key for key in value if re.fullmatch(r"where_[a-z]{2,3}(?:-[A-Za-z0-9]+)?", key)}
    if any(not _text(value[key]) for key in localized):
        raise SpriteFontError(f"{what}: translated descriptions must be text")
    return localized


def _windows(value, what: str) -> list[NameWindow]:
    if not isinstance(value, list) or not value:
        raise SpriteFontError(f"{what}: windows must list the name tables it draws")
    out = []
    for w in value:
        if (not isinstance(w, dict) or not WINDOW_KEYS <= set(w)
                or set(w) - WINDOW_KEYS - {"suffix"} - _where_keys(w, what)
                or not _text(w["table"]) or not _text(w["where"]) or not isinstance(w.get("suffix", ""), str)):
            raise SpriteFontError(f"{what}: a window needs table, width and where as text, an optional suffix")
        out.append(NameWindow(w["table"], _int(w, "width", 1, 1 << 16, f"{what} window"), w["where"],
                              w.get("suffix", "")))
    if len({w.table for w in out}) != len(out):
        raise SpriteFontError(f"{what}: one window per table")
    return out


def _pair(value, what: str) -> PairRule:
    if not isinstance(value, dict) or set(value) - _where_keys(value, what) != PAIR_KEYS:
        raise SpriteFontError(f"{what}: pair needs exactly {', '.join(sorted(PAIR_KEYS))}")
    if not _text(value["where"]) or not has_prose(value["evidence"]):
        raise SpriteFontError(f"{what}: pair where and evidence must be text")
    for key in ("encounters", "learnsets"):
        names = value[key]
        if (not isinstance(names, list) or not names or len(set(names)) != len(names)
                or not all(_text(n) for n in names)):
            raise SpriteFontError(f"{what}: pair {key} must list distinct names")
    if set(value["learnsets"]) - set(METHODS):
        raise SpriteFontError(f"{what}: pair learnsets name {', '.join(METHODS)}")
    return PairRule(_int(value, "width", 1, 1 << 16, f"{what} pair"), value["where"], list(value["encounters"]),
                    list(value["learnsets"]), value["evidence"])


def from_profile(prof) -> list[SpriteFont]:
    fonts = [parse(e) for e in prof.get("sprite_font", [])]
    if len({f.name for f in fonts}) != len(fonts):
        raise SpriteFontError("sprite font names must be distinct")
    return fonts


def base_table(font: SpriteFont) -> bytearray:
    """The tile the original code chooses for every byte value."""
    table = bytearray([font.fallback]) * TABLE
    for tile, char in enumerate(font.order):
        table[BYTE_OF[char]] = tile
    for char, like in font.aliases.items():
        table[BYTE_OF[char]] = font.order.index(like)
    return table


def base_advances(font: SpriteFont) -> bytearray:
    """The step of every byte value before the pack's letters."""
    steps = bytearray([font.advance]) * TABLE
    for char, step in font.advances.items():
        steps[BYTE_OF[char]] = step
    return steps


def glyph_tile(font: SpriteFont, pixels: list[list[int]]) -> bytes:
    """An art cell from the top of a tile as 4bpp tile bytes."""
    colour = {OUTSIDE: font.background, BACKGROUND: font.background, INK: font.ink, SHADOW: font.shadow}
    out = bytearray()
    for y in range(8):
        row = [colour[pixels[y][x]] if y < font.cell_rows else font.background for x in range(8)]
        out += bytes(row[x] | row[x + 1] << 4 for x in range(0, 8, 2))
    return bytes(out)


@dataclass
class Plan:
    font: SpriteFont
    sheet: bytes                                   # the base tiles, then one tile per drawn letter
    table: bytes
    added: dict[str, int] = field(default_factory=dict)    # letter -> its own tile
    copied: dict[str, int] = field(default_factory=dict)   # letter -> its base letter's tile
    advances: bytes = b""                          # step per byte value (per-glyph step only)


def plan(font: SpriteFont, base: Rom, pack) -> Plan:
    """The extended sheet and the tables for the letters of `pack`."""
    if any(isinstance(key, int) and BASE_CHARS.get(key, "").isalpha() for key in pack.glyphs):
        raise SpriteFontError(f"sprite font {font.name}: base-letter slot overrides require a main font")
    table = base_table(font)
    steps = base_advances(font)
    drawn = set(BYTE_OF[c] for c in font.order + "".join(font.aliases))
    sheet = bytearray(base.read(font.sheet, font.tiles * TILE))
    result = Plan(font, b"", b"")
    missing = []
    for char, byte in sorted(pack.chars.items(), key=lambda kv: kv[1]):
        if pack.sources[char] == "base":
            continue
        glyph = pack.glyphs.get(char)
        if glyph is not None:
            if glyph.width > 8:
                raise SpriteFontError(f"sprite font {font.name}: {char} is wider than a tile")
            result.added[char] = len(sheet) // TILE
            table[byte] = result.added[char]
            steps[byte] = max(glyph.width - font.shared, 1)
            sheet += glyph_tile(font, glyph.pixels)
        elif char in pack.copies and pack.copies[char] in drawn:
            result.copied[char] = table[byte] = table[pack.copies[char]]
            steps[byte] = steps[pack.copies[char]]
        else:
            missing.append(char)
    if missing:
        raise SpriteFontError(f"sprite font {font.name}: no art in {font.style}.txt and no drawn base "
                              f"letter for {''.join(missing)}")
    if len(sheet) // TILE > TABLE:
        raise SpriteFontError(f"sprite font {font.name}: {len(sheet) // TILE} tiles exceed one byte")
    result.sheet, result.table = bytes(sheet), bytes(table)
    result.advances = bytes(steps) if font.stepped else b""
    return result


def measure(p: Plan) -> SpriteMeasure:
    """The widths the routine draws text of the plan at (layout.SpriteMeasure)."""
    return SpriteMeasure(p.font.advance, p.font.tiles, p.table, p.advances or None, p.font.shared)


def text_width(p: Plan, data: bytes) -> int:
    """Pixels of encoded text as a stepped font draws it, outline included."""
    m = measure(p)
    return m.bytes_width(data) + m.shared


def draw(p: Plan, data: bytes, width: int) -> list[list[int]]:
    """Palette indices of encoded text as the stepped routine draws it with an added glyph."""
    font = p.font
    rows = [[0] * width for _ in range(8)]
    x = 0
    for b in data:
        tile = p.sheet[p.table[b] * TILE:(p.table[b] + 1) * TILE]
        for y in range(8):
            for c in range(8):
                v = tile[y * 4 + c // 2] >> 4 * (c & 1) & 15
                if v and x + c < width and rows[y][x + c] in (0, font.shadow):
                    rows[y][x + c] = v
        x += p.advances[b]
    return rows


@dataclass
class Placement:
    name: str
    sheet: int            # offset of the extended sheet
    table: int            # offset of the table
    tiles: int            # tiles written
    added: int            # tiles of drawn letters
    copied: int           # letters on base letter tiles
    pointers: list[int] = field(default_factory=list)
    lookup: int = 0       # offset of the lookup code
    code: bytes = b""     # lookup code and table word as written
    advances: int = 0     # offset of the step table (0: none)
    routine: int = 0      # offset of the routine (0: none)
    blob: bytes = b""     # routine code and words as written
    calls: list[tuple[int, bytes]] = field(default_factory=list)   # (offset, code and word as written)
    patches: list[int] = field(default_factory=list)                # offsets of the in-place patches


@dataclass
class Result:
    placements: list[Placement] = field(default_factory=list)
    allowed: set[int] = field(default_factory=set)
    failures: list[str] = field(default_factory=list)
    declared: int = 0

    def register(self, guard: "DiffRegistry") -> None:
        """Add this class to the diff-guard registry."""
        guard.add("sprite font", self.allowed, "exclusive")


def _place(space, n: int) -> int | None:
    at = space.alloc(n + ALIGN - 1)
    return None if at is None else -(-at // ALIGN) * ALIGN


def _blob(font: SpriteFont, sheet: int, table: int, steps: int) -> bytes:
    values = {"table": ROM_BASE + table, "sheet": ROM_BASE + sheet, "advance": ROM_BASE + steps,
              "tiles": font.tiles, "shadow": font.shadow, "step": font.advance}
    return font.routine + b"".join(values[w].to_bytes(4, "little") for w in font.words)


def apply(rom: Rom, base: Rom, plans: list[Plan], space, taken: set[int] = frozenset()) -> Result:
    """Write every planned sprite font into `rom`."""
    res = Result(declared=len(plans))
    for p in plans:
        font = p.font
        what = f"sprite font {font.name}"
        stale = [q for q in [font.lookup] + font.calls + font.patches
                 if not q.guard.matches(base.data)]
        if stale:
            res.failures.append(f"{what}: the base ROM does not hold the declared code at {stale[0].at:#x}")
            continue
        stale = [q for q in font.pointers if base.u32(q) != ROM_BASE + font.sheet]
        if stale:
            res.failures.append(f"{what}: the word at {stale[0]:#x} does not hold the sheet address")
            continue
        sizes = [len(p.sheet), len(p.table)] + ([len(p.advances), len(font.routine) + 4 * len(font.words)]
                                                if font.stepped else [])
        places = []
        for n in sizes:
            at = _place(space, n)
            if at is None:
                break
            places.append(at)
        if len(places) != len(sizes):
            res.failures.append(f"{what}: no free space for {sum(sizes)} bytes")
            continue
        sheet_at, table_at = places[0], places[1]
        steps_at, routine_at = (places[2], places[3]) if font.stepped else (0, 0)
        writes = [(sheet_at, p.sheet), (table_at, p.table), (font.lookup.at, font.lookup.code + ptr_bytes(table_at))]
        writes += [(q, ptr_bytes(sheet_at)) for q in font.pointers]
        writes += [(q.at, q.code) for q in font.patches]
        calls = []
        if font.stepped:
            blob = _blob(font, sheet_at, table_at, steps_at)
            writes += [(steps_at, p.advances), (routine_at, blob)]
            calls = [(c.at, c.written(routine_at)) for c in font.calls]
            writes += calls
        span = {at + k for at, data in writes for k in range(len(data))}
        clash = span & (set(taken) | res.allowed)
        if clash:
            res.failures.append(f"{what}: its bytes overlap bytes already changed, first at {min(clash):#x}")
            continue
        for at, data in writes:
            rom.write(at, data)
        res.allowed |= span
        res.placements.append(Placement(font.name, sheet_at, table_at, len(p.sheet) // TILE, len(p.added),
                                        len(p.copied), list(font.pointers), font.lookup.at,
                                        font.lookup.code + ptr_bytes(table_at), steps_at, routine_at,
                                        _blob(font, sheet_at, table_at, steps_at) if font.stepped else b"",
                                        calls, [q.at for q in font.patches]))
    return res


def verify(rom: Rom, base: Rom, plans: list[Plan], res: Result) -> list[str]:
    """Read back every written sprite font and check tiles, tables, sheet words and routine."""
    fails = []
    by_name = {p.font.name: p for p in plans}
    for pl in res.placements:
        p = by_name[pl.name]
        font = p.font
        what = f"sprite font {pl.name}"
        if rom.read(pl.sheet, len(p.sheet)) != p.sheet:
            fails.append(f"{what}: the sheet at {pl.sheet:#x} does not match the plan")
        if rom.read(pl.sheet, font.tiles * TILE) != base.read(font.sheet, font.tiles * TILE):
            fails.append(f"{what}: the base tiles changed")
        written = rom.read(pl.table, TABLE)
        if written != p.table:
            fails.append(f"{what}: the table at {pl.table:#x} does not match the plan")
        drifted = [b for b, t in enumerate(base_table(font)) if written[b] != t and t != font.fallback]
        if drifted:
            fails.append(f"{what}: byte {drifted[0]:#04x} no longer draws its base tile")
        for q in pl.pointers:
            if rom.u32(q) != ROM_BASE + pl.sheet:
                fails.append(f"{what}: the word at {q:#x} does not point to the sheet")
        if rom.read(pl.lookup, len(pl.code)) != font.lookup.code + ptr_bytes(pl.table):
            fails.append(f"{what}: the lookup code at {pl.lookup:#x} does not read the table")
        for q in font.patches:
            if rom.read(q.at, len(q.code)) != q.code:
                fails.append(f"{what}: the patch at {q.at:#x} does not hold its code")
        if not font.stepped:
            continue
        steps = rom.read(pl.advances, TABLE)
        if steps != p.advances:
            fails.append(f"{what}: the step table at {pl.advances:#x} does not match the plan")
        base_steps = base_advances(font)
        moved = [b for b in range(TABLE) if written[b] < font.tiles and steps[b] != base_steps[b]]
        if moved:
            fails.append(f"{what}: byte {moved[0]:#04x} of the base sheet changed its step")
        if rom.read(pl.routine, len(pl.blob)) != _blob(font, pl.sheet, pl.table, pl.advances):
            fails.append(f"{what}: the routine at {pl.routine:#x} does not match the plan")
        for c in font.calls:
            if rom.read(c.at, len(c.code) + 4) != c.written(pl.routine):
                fails.append(f"{what}: the call at {c.at:#x} does not reach the routine")
    return fails


def report_lines(res: Result) -> list[str]:
    if not res.placements:
        return []
    lines = ["", "## Sprite fonts", ""]
    for pl in res.placements:
        line = (f"- {pl.name}: {pl.tiles} tiles at {pl.sheet:#x} ({pl.added} drawn letters, "
                f"{pl.copied} on base letters), table at {pl.table:#x}, lookup at {pl.lookup:#x}")
        if pl.routine:
            line += (f"; steps at {pl.advances:#x}, routine at {pl.routine:#x}, "
                     f"calls at {', '.join(f'{at:#x}' for at, _ in pl.calls)}")
        if pl.patches:
            line += f"; patches at {', '.join(f'{at:#x}' for at in pl.patches)}"
        lines.append(line)
    return lines
