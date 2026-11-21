"""Base ROM identity and string decoding."""

from __future__ import annotations

import argparse

from ..core.charmap import Charmap
from ..profile.profile import Profile
from .common import add, command, line


@command("rom identify")
def info(args):
    """Verify the base ROM against the profile and print its identity."""
    prof = Profile.by_name(args.profile)
    rom = prof.load_rom(args.rom)
    line("profile", prof.name)
    line("rom", rom.path)
    line("size", f"{len(rom)} bytes")
    line("sha1", rom.sha1())
    line("verified", "passed")
    line("md5", rom.md5())
    line("title", rom.read(0xA0, 12).decode("ascii", "replace").rstrip("\0"))
    line("code", rom.read(0xAC, 4).decode("ascii", "replace"))
    if prof.get("support"):
        line("support", prof["support"]["stage"])
        line("engine", prof.get("engine", {}).get("family", "unspecified"))


@command("rom decode")
def decode(args):
    """Decode the string at an offset (hex); --built reads a built ROM with its language pack."""
    prof = Profile.by_name(args.profile)
    if args.built:
        from ..lang.langpack import LangPack
        from ..core.rom import Rom
        rom, cm = Rom.load(args.built), LangPack.load(args.lang).charmap()
    else:
        rom, cm = prof.load_rom(args.rom), Charmap()
    for off in args.offsets:
        text, end = cm.decode(rom.data, off, mode=args.mode)
        line("offset", f"{off:#09x}")
        line("size", f"{end - off} bytes")
        line("text", text)


@command("rom evidence")
def evidence(args):
    """Read a profile fact's evidence or guard from the ROM."""
    from ..core.rom import RomEvidence
    from ..profile import schema

    prof = Profile.by_name(args.profile)
    if getattr(args, "sample", None) is not None:
        import json
        from pathlib import Path
        from ..discover import observations

        path = Path(args.samples) if args.samples else prof.path / "observations" / "samples.json"
        plan = json.loads(path.read_text(encoding="utf-8"))
        if plan.get("schema") != "1.0.0" or not 0 <= args.sample < len(plan.get("samples", [])):
            raise ValueError("observations: invalid sample index or plan")
        sample = plan["samples"][args.sample]
        facts = {fact.id: fact for fact in schema.generated_facts(schema.read(prof.path))}
        if sample["fact"] != args.fact or args.fact not in facts:
            raise ValueError("observations: mismatched sample fact")
        raw = observations.prediction(prof.load_rom(args.rom), facts[args.fact], sample)
        line("fact", args.fact)
        line("sample", args.sample)
        line("size", f"{len(raw)} bytes")
        line("sha256", observations.digest(raw)["sha256"])
        line("bytes", raw.hex())
        return 0
    sources = schema.read(prof.path) if prof.path.is_dir() else schema.flat(prof.path)
    named = frozenset(row["id"] for row in sources.generated_records + sources.overlay_records)
    facts = {fact.id: fact.value for fact in schema.facts(prof.data, named)}
    if args.fact not in facts:
        raise ValueError(f"unknown profile fact: {args.fact}")
    value = facts[args.fact]
    if args.field:
        try:
            for part in args.field.split("."):
                value = value[int(part)] if isinstance(value, list) and part.isdecimal() else value[part]
        except (KeyError, IndexError, TypeError):
            raise ValueError(f"unknown evidence field: {args.field}") from None
    span = RomEvidence.parse(value)
    raw = span.read(prof.load_rom(args.rom).data)
    line("fact", args.fact)
    if args.field:
        line("field", args.field)
    line("offset", f"{span.offset:#09x}")
    line("size", f"{span.size} bytes")
    line("sha256", span.sha256)
    line("bytes", raw.hex())
    line("verified", "passed")


def hexadecimal_offset(value):
    """Validate offsets before loading a ROM."""
    try:
        offset = int(value, 16)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid hexadecimal offset: {value}") from None
    if offset < 0:
        raise argparse.ArgumentTypeError(f"negative offset: {value}")
    return offset


def register(sub):
    add(sub, "identify", info)
    d = add(sub, "decode", decode)
    d.add_argument("offsets", nargs="+", type=hexadecimal_offset, help="one or more hexadecimal string offsets")
    d.add_argument("--mode", default="field", choices=["field", "battle"], help="string control-code mode")
    d.add_argument("--built", default=None, help="built ROM to read (default: the base ROM)")
    e = add(sub, "evidence", evidence)
    e.add_argument("fact", help="profile fact identifier")
    selected = e.add_mutually_exclusive_group()
    selected.add_argument("--field", help="dotted field path within the fact; list positions start at zero")
    selected.add_argument("--sample", type=int, help="zero-based runtime sample index; print its predicted bytes")
    e.add_argument("--samples", help="sample plan JSON (default: selected profile's observations/samples.json)")
