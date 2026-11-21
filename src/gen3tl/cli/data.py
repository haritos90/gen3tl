"""Pokedex datasets, sprites, icons, maps and profiles."""

from __future__ import annotations

from pathlib import Path

from ..data.pokedextexts import dataset_dir, default_dataset_dir
from ..profile.profile import Profile
from .common import add, command, line


def verification_path(args, prof, *, sprites=False):
    """Verify the selected dataset without changing its output."""
    if args.verify is None:
        return None
    default = dataset_dir(prof.name)
    if sprites:
        default /= "sprites"
    return Path(args.verify or args.out or default)


def dataset_options(parser, description):
    """Use directories for dataset verification and preserve writing defaults."""
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--verify", metavar="DIR", help=description)
    modes.add_argument("--dry-run", dest="verify", action="store_const", const="",
                       help="verify the output directory from its files alone")
    modes.add_argument("--write", dest="verify", action="store_const", const=None,
                       help="write the output dataset (default)")


@command("data sprites")
def pokedex_sprites(args):
    """Write the Pokedex sprite PNGs of a hack from its [pokedex.sprites] profile block."""
    from pathlib import Path
    from ..data import pokedexsprites
    prof = Profile.by_name(args.profile)
    block = prof.get("pokedex")
    if (path := verification_path(args, prof, sprites=True)) is not None:
        problems = pokedexsprites.verify(path, block, prof.sha1)
        if problems:
            line("file", path)
            line("profile", prof.name)
            line("data", "stale")
            for problem in problems:
                line("error", problem)
            return 1
        line("file", path)
        line("profile", prof.name)
        line("verified", "passed")
        return 0
    rom = prof.load_rom(args.rom)
    doc = pokedexsprites.build(block, rom.data)
    out = Path(args.out) if args.out else default_dataset_dir(prof.name) / "sprites"
    manifest, size = pokedexsprites.write(out, doc)
    files = 1 + len(doc["slots"]) * len(pokedexsprites.VARIANTS) + len(doc.get("trainers", []))
    line("wrote", manifest)
    line("slots", f"{len(doc['slots'])} slots")
    line("files", f"{files - 1} PNGs")
    line("size", f"{size} bytes")
    line("average", f"{size // max(1, files - 1)} bytes/file")
    return 0


@command("data maps")
def pokedex_maps(args):
    """Write the area map PNGs of a hack from its [pokedex.maps] profile block."""
    from pathlib import Path
    from ..data import pokedexmaps
    prof = Profile.by_name(args.profile)
    block = prof.get("pokedex")
    if (path := verification_path(args, prof, sprites=True)) is not None:
        problems = pokedexmaps.verify(path, block, prof.sha1)
        if problems:
            line("file", path)
            line("profile", prof.name)
            line("data", "stale")
            for problem in problems:
                line("error", problem)
            return 1
        line("file", path)
        line("profile", prof.name)
        line("verified", "passed")
        return 0
    problems = pokedexmaps.check(block)
    if problems:
        line("profile", prof.name)
        for problem in problems:
            line("error", problem)
        return 1
    rom = prof.load_rom(args.rom)
    count = int(block["species"]["count"])
    found = pokedexmaps.areas(block, rom.data, count)
    resolved = []
    if "pickups" in block.get("items", {}).get("sources", {}):
        from ..data import pokedexdata
        metadata = {"maps": prof.get("maps"), "script": prof.get("script", {})}
        _, resolved, _ = pokedexdata.build_pickups(block, rom.data, metadata, set(found))
        found.update({row["runtime_map"]: int(row["map_header"], 16) for row in resolved if "runtime_map" in row})
    doc = pokedexmaps.build(block, rom.data, count, found)
    out = Path(args.out) if args.out else default_dataset_dir(prof.name) / "sprites"
    manifest, size = pokedexmaps.write(out, doc)
    line("wrote", manifest)
    line("maps", f"{len(doc['maps'])} maps")
    line("size", f"{size} bytes")
    for row in resolved:
        if "runtime_map" not in row:
            line("unrenderable", f"{row['map']} {row['map_header']}: no runtime map alias")
    return 0


@command("data icons")
def pokedex_icons(args):
    """Write a hack's interface masks and item icons from its [pokedex.icons] block."""
    from pathlib import Path
    from ..data import pokedexicons
    prof = Profile.by_name(args.profile)
    block = prof.get("pokedex")
    if (path := verification_path(args, prof, sprites=True)) is not None:
        problems = pokedexicons.verify(path, block, prof.sha1)
        if problems:
            line("file", path)
            line("profile", prof.name)
            line("data", "stale")
            for problem in problems:
                line("error", problem)
            return 1
        line("file", path)
        line("profile", prof.name)
        line("verified", "passed")
        return 0
    problems = pokedexicons.check(block)
    if problems:
        line("profile", prof.name)
        for problem in problems:
            line("error", problem)
        return 1
    rom = prof.load_rom(args.rom)
    doc = pokedexicons.build(block, rom.data, prof.get("font", []))
    out = Path(args.out) if args.out else default_dataset_dir(prof.name) / "sprites"
    manifest, size = pokedexicons.write(out, doc)
    line("wrote", manifest)
    line("icons", f"{len(doc['icons'])} icons")
    line("size", f"{size} bytes")
    return 0


@command("data pokedex")
def pokedex_data(args):
    """Write a hack's language-neutral Pokedex dataset from its [pokedex] profile block."""
    import json
    from pathlib import Path
    from ..data import pokedexdata, pokedexlabels
    prof = Profile.by_name(args.profile)
    block = pokedexlabels.resolve(prof.get("pokedex"), prof.name)
    if (path := verification_path(args, prof)) is not None:
        if path.is_dir() or not path.is_file() and path.suffix.lower() != ".json":
            path /= "pokedex.json"
        try:
            doc = (pokedexdata.read(path) if "forms" in (block or {}).get("species_browsing", {})
                   else json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            line("file", path)
            line("error", exc)
            return 1
        problems = pokedexdata.verify_doc(doc, block, prof.name, prof.sha1)
        if not problems and isinstance(doc.get("trainer_ivs"), str):
            from ..data import pokedextrainerivs
            try:
                companion = pokedextrainerivs.read(path.parent / doc["trainer_ivs"])
            except (OSError, ValueError) as exc:
                line("file", path.parent / doc["trainer_ivs"])
                line("error", exc)
                return 1
            problems = pokedextrainerivs.verify_doc(
                companion, block["trainers"]["ivs"], prof.name, prof.sha1, doc["profile_block"],
                block["trainers"].get("evs"))
        if problems:
            line("file", path)
            line("profile", prof.name)
            line("data", "stale")
            for problem in problems:
                line("error", problem)
            return 1
        line("file", path)
        line("profile", prof.name)
        line("verified", "passed")
        line("species", f"{len(doc.get('species', []))} species")
        line("block", doc.get("profile_block", "")[:12])
        return 0
    problems = pokedexdata.check(block)
    if problems:
        line("profile", prof.name)
        for problem in problems:
            line("error", problem)
        return 1
    rom = prof.load_rom(args.rom)
    metadata = {"id": prof.name, "name": prof.get("name", "")}
    environments = block.get("moves", {}).get("environments", {})
    # map walks need map and script layouts
    if ("sources" in block or "maps" in block.get("trainers", {}) or "scripts" in environments
            or any(row.get("map_codes") for row in environments.get("rows", []))):
        metadata.update({"maps": prof.get("maps"), "script": prof.get("script", {})})
    doc = pokedexdata.build(block, rom.data, metadata)
    out = Path(args.out)
    written = pokedexdata.write(out, doc, split=args.split)
    index = json.loads(written[0].read_text(encoding="utf-8"))
    line("wrote", written[0])
    line("species", f"{index.get('count', 0)} species")
    line("records", f"{len(written) - 1} records")
    if index.get("missing"):
        for block_name in index["missing"]:
            line("missing", block_name)
    return 0


@command("data probe")
def pokedex_probe(args):
    """Report what a ROM's binary alone establishes for a Pokedex profile."""
    from ..data import pokedexprobe
    from ..core.rom import Rom
    rom = Rom.load(args.rom) if args.rom else Profile.by_name(args.profile).load_rom()
    doc = pokedexprobe.probe(rom)
    line("rom", doc["rom"]["path"])
    line("sha1", doc["rom"]["sha1"])
    line("code", doc["rom"]["game_code"])
    line("title", doc["rom"]["title"])
    line("size", f"{doc['rom']['size']} bytes")
    line("engine", doc["engine"]["family"])
    line("text", doc["charmap"]["verdict"])
    line("species", f"{doc['species_count']} species" if doc["species_count"] is not None else "unknown")
    line("blocks", f"{doc['lz77_shapes']} blocks")
    line("support", "unverified")
    for candidate in doc["candidates"]:
        line("candidate", candidate["kind"])
        line("offset", f"{candidate['base']:#x}")
        line("stride", f"{candidate['stride']} bytes")
        line("count", f"{candidate['count']} records")
        line("confidence", candidate["confidence"])
    for field in doc["unknown"]:
        line("unproven", field)
    return 0


@command("data profile")
def pokedex_profile(args):
    """Write a Pokedex profile skeleton from the probe findings."""
    from pathlib import Path
    from ..data import pokedexprobe
    from ..core.rom import Rom
    rom = Rom.load(args.rom) if args.rom else Profile.by_name(args.profile).load_rom()
    text = pokedexprobe.skeleton(str(rom.path), pokedexprobe.probe(rom))
    out = Path(args.out)
    if out.exists():
        line("file", out)
        line("error", "exists")
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    line("wrote", out)
    line("evidence", "required")
    return 0


def register(sub):
    px = add(sub, "pokedex", pokedex_data)
    px.add_argument("--out", default=None,
                    help="output directory (default: project data/pokedex; workspace build/pokedex/<profile>)")
    px.add_argument("--split", action="store_true", help="one JSON file per species beside the index")
    dataset_options(px, "check a dataset directory against the profile, from its files alone")
    ps = add(sub, "sprites", pokedex_sprites)
    ps.add_argument("--out", default=None, help="sprite directory (default: the tracked dataset's sprites)")
    dataset_options(ps, "check written sprites against the profile, from their files alone")
    pi = add(sub, "icons", pokedex_icons)
    pi.add_argument("--out", default=None, help="sprite directory (default: the tracked dataset's sprites)")
    dataset_options(pi, "check written icons against the profile, from their files alone")
    pm = add(sub, "maps", pokedex_maps)
    pm.add_argument("--out", default=None, help="sprite directory (default: the tracked dataset's sprites)")
    dataset_options(pm, "check written maps against the profile, from their files alone")
    add(sub, "probe", pokedex_probe)
    pf = add(sub, "profile", pokedex_profile)
    pf.add_argument("--out", required=True, help="profile path to write (e.g. profiles/<hack>.toml)")
