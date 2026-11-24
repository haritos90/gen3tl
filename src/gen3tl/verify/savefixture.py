"""Test saves for the save converter, written by the game itself."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .savefile import EOS, NAME_LEN, Layout

if TYPE_CHECKING:   # functions import it when they run
    from pathlib import Path

STATE_EWRAM = 0x21000
EWRAM = 0x02000000
LANGUAGE_ENGLISH = 2
# eggs created in game (giveegg, daycare) get a fixed name:
# FireRed's Japanese egg word, then the ROM bytes after it
GAME_EGG_NICKNAME = bytes.fromhex("606f8bfff44b630c4b21")


@dataclass
class TestMon:
    place: str            # "party", "daycare" or "box"
    slot: int             # party/daycare index, or box record index (box-1)*30 + position
    species: int
    nickname: bytes       # name bytes without terminator, or the whole field
    egg: bool = False
    personality: int = 0x1234567
    otid: int = 0x2468ACE
    moves: tuple[int, ...] = (33,)  # Tackle
    item: int = 0


def _name_field(name: bytes) -> bytes:
    return (name + bytes([EOS]) * NAME_LEN)[:NAME_LEN]


def _header(m: TestMon, ot: bytes) -> bytes:
    sanity = 0x02 | (0x04 if m.egg else 0)  # hasSpecies, isEgg
    ot_field = (ot + bytes([EOS]) * 7)[:7]
    return struct.pack("<II", m.personality, m.otid) + _name_field(m.nickname) + \
        bytes([LANGUAGE_ENGLISH, sanity]) + ot_field + b"\x00"


def _ivs(m: TestMon) -> int:
    return 0x3FFFFFFF | (1 << 30 if m.egg else 0)  # six 5-bit IVs of 31; bit 30 = egg


def box_pokemon(m: TestMon, ot: bytes) -> bytes:
    """80-byte unencrypted BoxPokemon (party and daycare)."""
    sub0 = struct.pack("<HHIBBBB", m.species, m.item, 135, 0, 70, 4, 0)
    mv = (list(m.moves) + [0] * 4)[:4]
    sub1 = struct.pack("<HHHHBBBB", *mv, *(35 if x else 0 for x in mv))
    sub2 = bytes(12)
    sub3 = struct.pack("<BBHII", 0, 0x10, 5 | (3 << 7), _ivs(m), 0)   # met level 5, game 3
    return _header(m, ot) + struct.pack("<HH", m.species, 0) + sub0 + sub1 + sub2 + sub3


def party_pokemon(m: TestMon, ot: bytes) -> bytes:
    stats = struct.pack("<IBBHHHHHHH", 0, 5, 0xFF, 20, 20, 10, 10, 10, 10, 10)
    return box_pokemon(m, ot) + stats


def compressed_pokemon(m: TestMon, ot: bytes) -> bytes:
    """58-byte CFRU CompressedPokemon: header, growth (11), moves (5 bytes of 10-bit fields), EVs, misc."""
    growth = struct.pack("<HHIBBB", m.species, 0, 135, 0, 70, 4)
    moves = (33).to_bytes(5, "little")  # move1 = Tackle, others 0
    misc = struct.pack("<BBHI", 0, 0x10, 5 | (3 << 7), _ivs(m))
    rec = _header(m, ot) + growth + moves + bytes(6) + misc
    assert len(rec) == 58, len(rec)
    return rec


def place(state: bytearray, layout: Layout, mons: list[TestMon], ot: bytes) -> dict[str, int]:
    """Write test Pokemon into the RAM of a savestate blob; returns the addresses used."""
    def put(addr: int, data: bytes) -> None:
        o = STATE_EWRAM + addr - EWRAM
        state[o:o + len(data)] = data

    used = {}
    party = [m for m in mons if m.place == "party"]
    # saving copies gPlayerParty (0x2024284, count 0x2024029) into SaveBlock1
    if party:
        put(0x2024029, bytes([len(party)]))
    for m in party:
        addr = 0x2024284 + m.slot * 100
        put(addr, party_pokemon(m, ot))
        used[f"party {m.slot + 1}"] = addr
    d = layout.daycare
    for m in (m for m in mons if m.place == "daycare"):
        addr = d["base"] + m.slot * d["stride"]
        put(addr, box_pokemon(m, ot) + bytes(d["stride"] - 80))
        used[f"daycare {m.slot + 1}"] = addr
    r = layout.box_record
    for m in (m for m in mons if m.place == "box"):
        box, pos = divmod(m.slot, r["per_box"])
        rng = next(x for x in layout.boxes if x["first"] <= box + 1 < x["first"] + x["count"])
        addr = rng["ram"] + ((box + 1 - rng["first"]) * r["per_box"] + pos) * r["size"]
        put(addr, compressed_pokemon(m, ot))
        used[f"box {box + 1} slot {pos + 1}"] = addr
    return used


# headless helpers need the libretro core and verify/states

def default_mons(species_names: list[bytes], egg: bytes) -> list[TestMon]:
    """One Pokemon in every storage area: party, daycare and the box range ends of each saved RAM block."""
    n = species_names
    return [
        TestMon("party", 0, 1, n[1]),
        TestMon("party", 1, 25, b"\xcd\xe4\xd5\xe6\xdf\xed"),     # typed nickname "Sparky"
        TestMon("party", 2, 95, n[95]),
        TestMon("party", 3, 39, n[39]),
        TestMon("party", 4, 175, egg, egg=True),
        TestMon("party", 5, 122, n[122]),
        TestMon("daycare", 0, 133, n[133]),
        TestMon("box", 0, 4, n[4]),                    # box 1 slot 1 (storage, section 5)
        TestMon("box", 1, 175, GAME_EGG_NICKNAME, egg=True),   # box 1 slot 2
        TestMon("box", 18 * 30 + 29, 7, n[7]),         # box 19 slot 30 (section 13)
        TestMon("box", 19 * 30, 16, n[16]),            # box 20 slot 1 (sector 30)
        TestMon("box", 21 * 30 + 29, 19, n[19]),       # box 22 slot 30 (sector 31)
        TestMon("box", 22 * 30, 10, n[10]),            # box 23 slot 1 (SaveBlock1)
        TestMon("box", 23 * 30 + 29, 29, n[29]),       # box 24 slot 30 (SaveBlock1)
        TestMon("box", 24 * 30, 13, n[13]),            # box 25 slot 1 (SaveBlock2)
        TestMon("box", 24 * 30 + 29, 175, egg, egg=True),
    ]


def make_fixture(rom_path, layout: Layout, out_dir, state: str = "e6") -> "Path":
    """Load a checkpoint, place the default test Pokemon, set FLAG_SYS_POKEMON_GET, save from the Start menu."""
    from pathlib import Path
    from .headless import Gba, STATE_DIR
    from .savefile import rom_names
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    names, egg = rom_names(Path(rom_path).read_bytes(), layout)
    g = Gba(Path(rom_path))
    g.load_state((STATE_DIR / f"{state}.state").read_bytes())
    g.run(2)
    st = bytearray(g.save_state())
    place(st, layout, default_mons(names, egg), b"\xbb")          # OT name "A"
    flag = 0x828                                                  # FLAG_SYS_POKEMON_GET, SaveBlock1 flags at +0xEE0
    o = STATE_EWRAM + 0x202552C + 0xEE0 + flag // 8 - EWRAM
    st[o] |= 1 << (flag % 8)
    g.load_state(bytes(st))
    g.run(2)
    # Start menu with a party: Pokemon List, Cube, Save Game, ...
    for key, after in (("START", 60), ("DOWN", 20), ("DOWN", 20), ("A", 90)) + (("A", 120),) * 6:
        g.press(key, 4, after)
    g.run(200)
    g.screenshot(out_dir / "fixture-saved.png")
    path = out_dir / "fixture.sav"
    path.write_bytes(g.save_ram())
    g.close()
    return path


def boot(rom_path, save_path):
    """An emulator with the flash of a save file in any container the converter reads."""
    from pathlib import Path
    from .headless import Gba
    from .savefile import unwrap
    g = Gba(Path(rom_path))
    g.load_save_ram(unwrap(Path(save_path).read_bytes(), len(g.save_ram()))[1])
    return g


def check_save(rom_path, save_path, out_dir) -> list["Path"]:
    """Boot a ROM with a save, continue, open the party screen and the first summary; screenshots."""
    from pathlib import Path
    out_dir = Path(out_dir)
    g = boot(rom_path, save_path)
    g.run(900)
    shots = []
    steps = [("START", 90, None), ("A", 150, None), ("A", 150, "continue"), ("A", 200, None), (None, 200, "field"),
             ("START", 60, None), ("A", 220, "party"), ("A", 60, None), ("A", 150, "summary")]
    for key, after, name in steps:
        if key:
            g.press(key, 4, after)
        else:
            g.run(after)
        if name:
            shots.append(g.screenshot(out_dir / f"{name}.png"))
    g.close()
    return shots


ENEMY_PARTY = 0x202402C        # gEnemyParty (vanilla FireRed)
SCRATCH_SCRIPT = 0x202B500     # PC box 10 RAM; empty in the fixture save


def _continue(g) -> None:
    g.run(900)
    for key, after in (("START", 90), ("A", 150), ("A", 150), ("A", 200)):
        g.press(key, 4, after)
    g.run(200)


def battle_session(rom_path, save_path, out_dir, species: int = 16, layout: dict | None = None) -> list["Path"]:
    """Boot with a save, start a wild battle against `species` level 5 and screenshot the battle screens."""
    from pathlib import Path
    from .savefile import Layout, rom_names
    out_dir = Path(out_dir)
    names, _ = rom_names(Path(rom_path).read_bytes(), Layout.from_dict(layout))
    g = boot(rom_path, save_path)
    _continue(g)
    st = bytearray(g.save_state())
    mon = party_pokemon(TestMon("party", 0, species, names[species], personality=0x55, otid=0x77), b"\xbb")
    o = STATE_EWRAM + ENEMY_PARTY - EWRAM
    st[o:o + 100] = mon
    flag = 0x829   # FLAG_SYS_POKEDEX_GET: the Start menu offers the Pokedex after the battle
    o = STATE_EWRAM + 0x202552C + 0xEE0 + flag // 8 - EWRAM
    st[o] |= 1 << (flag % 8)
    g.load_state(bytes(st))
    g.run_script(bytes([0xB7, 0x02]), addr=SCRATCH_SCRIPT)   # dowildbattle; end
    shots = []
    g.run(400)
    shots.append(g.screenshot(out_dir / "battle-1-intro.png"))
    g.press("A", 4, 150)
    shots.append(g.screenshot(out_dir / "battle-2-send-out.png"))
    g.run(250)
    shots.append(g.screenshot(out_dir / "battle-3-action-menu.png"))
    g.press("A", 4, 90)
    shots.append(g.screenshot(out_dir / "battle-4-move-menu.png"))
    g.press("A", 4, 150)
    shots.append(g.screenshot(out_dir / "battle-5-move-used.png"))
    # run away (Run: right, down); the species is marked seen
    g.run(600)
    for key, after in (("B", 60), ("RIGHT", 30), ("DOWN", 30), ("A", 200)):
        g.press(key, 4, after)
    shots.append(g.screenshot(out_dir / "battle-6-run.png"))
    for key, after in (("A", 150), ("A", 150), ("A", 200), ("START", 60), ("A", 200)):
        g.press(key, 4, after)
    shots.append(g.screenshot(out_dir / "dex-1-contents.png"))
    # the numerical list uses the regional order;
    # open the habitat page (the cursor stops on disabled lines)
    g.press("DOWN", 4, 30)
    g.press("DOWN", 4, 30)
    g.press("A", 4, 200)
    shots.append(g.screenshot(out_dir / "dex-2-habitat.png"))
    g.press("A", 4, 200)
    shots.append(g.screenshot(out_dir / "dex-3-entry.png"))
    g.press("RIGHT", 4, 120)
    shots.append(g.screenshot(out_dir / "dex-4-entry-page-2.png"))
    g.close()
    return shots


def pc_session(rom_path, save_path, out_dir, species: int | None = None, layout: dict | None = None) -> list["Path"]:
    """Boot with a save, open the bag and the PC storage system; screenshot the bag, PC menu, box 1 and a panel."""
    from pathlib import Path
    out_dir = Path(out_dir)
    g = boot(rom_path, save_path)
    _continue(g)
    if species is not None:
        from .savefile import Layout, rom_names
        lay = Layout.from_dict(layout)
        names, _ = rom_names(Path(rom_path).read_bytes(), lay)
        st = bytearray(g.save_state())
        place(st, lay, [TestMon("box", 0, species, names[species])], b"\xbb")
        g.load_state(bytes(st))
    shots = []
    # the bag ("Cube"): second Start entry once a party exists
    for key, after in (("START", 60), ("DOWN", 20), ("A", 200)):
        g.press(key, 4, after)
    shots.append(g.screenshot(out_dir / "bag-1-items.png"))
    for key, after in (("B", 120), ("B", 60), ("B", 60)):
        g.press(key, 4, after)
    g.run(60)
    g.run_script(bytes([0x25, 0x3C, 0x00, 0x27, 0x02]), addr=SCRATCH_SCRIPT)   # special 0x3C; waitstate; end
    g.run(200)
    shots.append(g.screenshot(out_dir / "pc-1-menu.png"))
    g.press("A", 4, 150)
    shots.append(g.screenshot(out_dir / "pc-2-box.png"))
    g.press("A", 4, 150)
    shots.append(g.screenshot(out_dir / "pc-3-selected.png"))
    g.close()
    return shots


def names_session(rom_path, save_path, out_dir, lead: int, moves: tuple[int, ...], enemy: int,
                  item: int = 0, layout: dict | None = None) -> list["Path"]:
    """Screens that show name-table entries: a wild battle against `enemy`, the move menu, party and summary screens."""
    from pathlib import Path
    from .savefile import Layout, rom_names
    out_dir = Path(out_dir)
    names, _ = rom_names(Path(rom_path).read_bytes(), Layout.from_dict(layout))
    g = boot(rom_path, save_path)
    _continue(g)
    st = bytearray(g.save_state())

    def put(addr: int, data: bytes) -> None:
        o = STATE_EWRAM + addr - EWRAM
        st[o:o + len(data)] = data

    put(0x2024284, party_pokemon(TestMon("party", 0, lead, names[lead], moves=moves, item=item), b"\xbb"))
    put(ENEMY_PARTY, party_pokemon(TestMon("party", 0, enemy, names[enemy], personality=0x55, otid=0x77), b"\xbb"))
    g.load_state(bytes(st))
    g.run_script(bytes([0xB7, 0x02]), addr=SCRATCH_SCRIPT)   # dowildbattle; end
    shots = []

    def shot(name: str) -> None:
        shots.append(g.screenshot(out_dir / f"{name}.png"))

    g.run(400)
    shot("names-1-intro")
    g.press("A", 4, 120)
    shot("names-2-switch-in")
    g.run(120)
    shot("names-3-switch-in-later")
    g.run(400)
    g.press("A", 4, 90)                          # Fight
    shot("names-4-move-1")
    for key, name in (("RIGHT", "names-5-move-2"), ("DOWN", "names-6-move-4"), ("LEFT", "names-7-move-3")):
        g.press(key, 4, 40)
        shot(name)
    # leave the battle (Run: right, down); open party and summary
    for key, after in (("B", 60), ("RIGHT", 30), ("DOWN", 30), ("A", 200), ("A", 150), ("A", 150), ("A", 200)):
        g.press(key, 4, after)
    g.run(200)
    g.press("START", 4, 60)
    g.press("A", 4, 220)
    shot("names-8-party")
    g.press("A", 4, 60)
    g.press("A", 4, 150)
    shot("names-9-summary-1")
    g.press("RIGHT", 4, 90)
    shot("names-10-summary-2")
    g.press("RIGHT", 4, 90)
    shot("names-11-summary-3")
    g.press("A", 4, 90)
    shot("names-12-summary-3-detail")
    g.close()
    return shots


def healthbox_shots(rom_path, save_path, out_dir, species: list[int], layout: dict | None = None) -> list["Path"]:
    """The player's battle health box with each of `species` as the first party Pokemon."""
    from pathlib import Path
    from .savefile import Layout, rom_names
    out_dir = Path(out_dir)
    names, _ = rom_names(Path(rom_path).read_bytes(), Layout.from_dict(layout))
    g = boot(rom_path, save_path)
    _continue(g)
    base = g.save_state()
    shots = []
    for sp in species:
        st = bytearray(base)
        for addr, mon in ((0x2024284, TestMon("party", 0, sp, names[sp], personality=0)),
                          (ENEMY_PARTY, TestMon("party", 0, 16, names[16], personality=0x55, otid=0x77))):
            o = STATE_EWRAM + addr - EWRAM
            st[o:o + 100] = party_pokemon(mon, b"\xbb")
        g.load_state(bytes(st))
        g.run_script(bytes([0xB7, 0x02]), addr=SCRATCH_SCRIPT)
        g.run(400)
        g.press("A", 4, 120)
        g.run(400)
        shots.append(g.screenshot(out_dir / f"healthbox-{sp}.png"))
    g.close()
    return shots


def hatch_check(data: bytes, layout: Layout, slot: int, species: int, catalog: str, table: dict) -> dict:
    """Party `slot` of a save the game wrote, against a hatched `species` named `catalog`; returns {species, egg, name, catalog, passed}."""
    from .savefile import SaveFile, name_of
    save = SaveFile(data, layout)
    newest = save.slots[0]
    found, egg, name = 0, False, b""
    for place, addr, sp, is_egg in save.mons(newest):
        if place == f"party {slot}":
            found, egg, name = sp, is_egg, name_of(save.nickname(newest, addr))
    text = "".join(table.get(b, f"[{b:02X}]") for b in name)
    return {"species": found, "egg": egg, "name": text, "catalog": catalog,
            "passed": found == species and not egg and text == catalog}


def catalog_species(po_root, species: int) -> tuple[str, str]:
    """The English name and the translation of `species` in a language's catalog."""
    from pathlib import Path
    from ..catalog.catalog import load_catalog
    key = f"table:species/{species}"
    for _, e in load_catalog(Path(po_root)).values():
        if e.msgctxt == key:
            return e.msgid, e.msgstr
    raise ValueError(f"{po_root}: no {key}")
