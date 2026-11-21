"""Text candidates, the string inventory, script context and reach, read from the ROM; profile coverage."""

from __future__ import annotations

from pathlib import Path

from ..core.charmap import Charmap
from ..core.terminal import require_names
from ..profile.profile import Profile
from .common import add, command, line


@command("discover candidates")
def discover_candidates(args):
    """Write unverified text candidates to a separate profile directory."""
    import json
    from ..discover.discovery import inspect
    prof = Profile.by_name(args.profile)
    doc = inspect(prof, prof.load_rom(args.rom))
    out = args.build_root / "discovery" / prof.name / "candidates.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    line("wrote", out)
    line("candidates", f"{len(doc['candidates'])} candidates")
    line("support", "unverified")
    for warning in doc["limitations"]:
        line("warning", warning)
    return 0


@command("discover run")
def discover(args):
    """Derive identity, family, codec and fonts into a generated profile and evidence report."""
    import json
    from ..discover import emission, foundations

    prof = Profile.by_name(args.profile)
    destination = Path(args.out) if args.out else (prof.path if prof.path.is_dir()
                                                   else args.build_root / "discovery" / prof.name)
    inventory = json.loads(Path(args.inventory).read_text(encoding="utf-8")) if args.inventory else None
    from ..discover import scripts

    rom = prof.load_rom(args.rom)
    roots, occurrences = scripts.walk_inputs(rom, prof) if prof.get("maps") else (None, ())
    result = foundations.run(rom, inventory=inventory, script_roots=roots,
                             script_occurrences=occurrences)
    from ..discover import observations
    observations.apply(rom, result, prof.path, args.observations)
    output = emission.write(result, destination, prof.name, prof)
    line("wrote", output["profile"])
    line("report", output["report"])
    for key in ("verified", "candidate", "omitted", "unresolved"):
        line(key, f"{output[key]} facts")
    for family in result.metadata["families"]:
        line("family", family["name"])
        line("confidence", f"{family['matched']}/{family['total']} ({family['ratio']:.0%})")
        line("selected", str(family["selected"]).lower())
    line("codec", result.metadata.get("codec", {}).get("candidate", "unresolved"))
    line("fonts", f"{len(result.data.get('font', []))} verified bindings")
    line("slot scope", result.metadata["free_slots"]["scope"])
    structural = output.get("structural")
    if structural:
        line("structural", f"{structural['reproduced']}/{structural['structural_facts']} "
                           f"({structural['raw_named_facts']} named / {structural['full_overlay_facts']} overlay)")
        for section, counts in structural["sections"].items():
            total = counts.get("structural", 0)
            if not total:
                continue
            line("section", section)
            line("coverage", f"{counts.get('reproduced', 0)}/{total} "
                             f"({counts.get('reproduced', 0) / total:.0%})")
        for residue in structural["unresolved"]:
            line("residue", residue["id"])
            line("reason", residue["reason"])
            line("missing", ", ".join(residue["fields"]) or "no fields")
    from ..profile import residue
    declared = residue.read(prof.path, rom.sha1())
    known, new = residue.unresolved(result.issues, declared)
    line("named residue", len(known))
    unresolved = [item.id for item in new]
    if unresolved:
        line("error", f"unresolved discovery facts: {', '.join(unresolved)}")
    return int(bool(unresolved))


@command("discover observe")
def observe(args):
    """Record runtime samples from declared scenario shots."""
    import json
    from ..discover import observations
    from ..profile import check, schema

    prof = Profile.by_name(args.profile)
    rom = prof.load_rom(args.rom)
    facts = schema.generated_facts(check.sources_of(prof.path))
    if args.convert:
        if Path(args.out).exists():
            raise ValueError("observations: output already exists")
        document = observations.convert(rom, facts,
            json.loads(Path(args.convert).read_text(encoding="utf-8")), Path(args.inputs_root))
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    else:
        document = observations.produce(rom, rom.path, facts,
            json.loads(Path(args.samples).read_text(encoding="utf-8")), args.out, prof.get("save"))
    line("wrote", args.out)
    line("observations", len(document["observations"]))
    from ..discover.foundations import Result
    reports = observations.verify(rom, Result({}, facts, {}), document)
    for row in reports:
        line("fact", row["fact"])
        line("shot", row["shot"])
        line("size", f"{row['size']} bytes")
        line("sha256", row["sha256"])
        if "foreground_pixels" in row:
            line("non-background pixels", row["foreground_pixels"])
        line("strength", row["status"])
        line("shared", ", ".join(row["shared"]) or "none")
    line("verified", "passed")
    return 0


@command("discover regenerate")
def regenerate(args):
    """Refresh studies and verify two byte-identical profile regenerations."""
    from ..discover.regeneration import run
    return run(args)


@command("discover inventory")
def inventory(args):
    """Build build/inventory.json and build/inventory-report.md from the base ROM."""
    from ..discover import inventory as inv_mod
    from ..lang.language import save_link_keys
    from ..lang.langpack import LangPack
    prof = Profile.by_name(args.profile)
    rom = prof.load_rom(args.rom)
    cm = Charmap()
    inv = inv_mod.build(prof, rom, cm, save_link_keys=save_link_keys(args.lang))
    out = args.project.build
    inv_mod.save(inv, out / "inventory.json")
    rep = inv_mod.report(inv)
    pack = LangPack.load(args.lang)
    hits = inv_mod.slot_usage(inv, pack.allocated_slots(), cm, unverified=True)
    rep += (f"\nLanguage pack `{args.lang}` slots ({len(pack.allocated_slots())}, {len(pack.copies)} of them copies) "
            f"used by original strings, verified or not: {len(hits)}\n")
    for sid, b in hits[:20]:
        rep += f"- {sid}: byte {b:#04x}\n"
    from ..catalog.catalog import load_catalog
    unresolved = inv_mod.untranslated_slot_usage(inv, pack.allocated_slots(), cm,
                                               load_catalog(args.project.catalog, prof, english=False))
    rep += f"\nSource glyph collisions without replacements: {len(unresolved)}\n"
    (out / "inventory-report.md").write_text(rep, encoding="utf-8")
    line("wrote", out / "inventory.json")
    line("report", out / "inventory-report.md")
    line("entries", f"{len(inv['entries'])} entries")
    line("collisions", f"{len(hits)} slots")
    line("unresolved", f"{len(unresolved)} slots")
    return 1 if unresolved else 0


@command("discover context")
def context(args):
    """Build build/context.json (map, event, speaker, trainer context) and a coverage report."""
    import json
    from collections import Counter
    from ..discover import inventory as inv_mod
    from ..discover.context import discover_context
    from ..discover.flow_facts import document, project
    prof = Profile.by_name(args.profile)
    rom = prof.load_rom(args.rom)
    out = args.project.build
    inv = inv_mod.load(out / "inventory.json")
    facts = discover_context(rom, prof, inv)
    projected = project(facts)
    ctx = {key: projected[key] for key in ("schema", "stats", "strings", "maps")}
    (out / "context-facts.json").write_text(json.dumps(document(facts), ensure_ascii=False, indent=0), encoding="utf-8")
    ctx.update(profile=prof.name, rom_sha1=prof.sha1)
    (out / "context.json").write_text(json.dumps(ctx, ensure_ascii=False, indent=0), encoding="utf-8")
    cats = Counter(); direct = Counter(); inferred = Counter(); speakers = Counter()
    by_id = {e["id"]: e for e in inv["entries"]}
    for sid, rec in ctx["strings"].items():
        c = by_id[sid]["category"]
        cats[c] += 1
        if rec["uses"]:
            direct[c] += 1
        elif rec["inferred_map"]:
            inferred[c] += 1
        if rec["speakers"]:
            speakers[c] += 1
    trainer = sum(1 for r in ctx["strings"].values() if any("trainer" in u for u in r["uses"]))
    lines = ["# Context coverage", "", f"Maps walked: {ctx['stats']['maps']}; scripts: {ctx['stats']['scripts']}; "
             f"trainer battle texts with trainer data: {trainer}", "",
             "| Category | Strings | Direct map context | Inferred map | Inline speaker |", "|---|---|---|---|---|"]
    for c in sorted(cats, key=lambda c: -cats[c]):
        lines.append(f"| {c} | {cats[c]} | {direct[c]} ({direct[c] / cats[c]:.0%}) | {inferred[c]} | {speakers[c]} |")
    lines += ["", "Unknown opcodes where a script walk stopped: " + ", ".join(f"{k}: {v}" for k, v in ctx["stats"]["unknown_ops"].items())]
    # what the scripts insert into the placeholder slots
    slot_recs = [s for r in ctx["strings"].values() for s in r.get("slots", {}).values()]
    lines += ["", f"Placeholder slots: buffer commands met: {ctx['stats']['buffer_fills']}; texts met by walks from "
              f"bufferstring commands no map walk reached: {ctx['stats']['side_walk_texts']}; slots of texts the walks "
              f"met: {len(slot_recs)}, with a known fill: {sum(1 for s in slot_recs if s['fills'])}, with a fill on "
              f"every walked path: {sum(1 for s in slot_recs if s['fills'] and not s['unfilled'])}"]
    rep = "\n".join(lines) + "\n"
    (out / "context-report.md").write_text(rep, encoding="utf-8")
    line("wrote", out / "context.json")
    line("report", out / "context-report.md")
    line("maps", f"{ctx['stats']['maps']} maps")
    line("scripts", f"{ctx['stats']['scripts']} scripts")
    line("entries", f"{len(ctx['strings'])} entries")
    line("trainers", f"{trainer} entries")
    line("slots", f"{len(slot_recs)} slots")


@command("discover reach")
def reach(args):
    """Classify every script and system string by reachability: build/reach.json and build/reach-report.md."""
    import json
    from ..discover import inventory as inv_mod
    import sys
    from ..catalog.catalog import load_catalog
    from ..discover.reach import discover, fewer_strings, render, report, to_json
    from ..discover.flow_facts import document
    prof = Profile.by_name(args.profile)
    rom = prof.load_rom(args.rom)
    out = args.project.build
    inv = inv_mod.load(out / "inventory.json")
    groups = args.group or ["script", "system"]
    ids = {g: [] for g in groups}
    root = args.project.catalog
    for sid, (path, _) in load_catalog(root, prof, english=False).items():   # keys and files only
        group = path.relative_to(root).parts[0].removesuffix(".po")   # the catalog's top file or folder
        if group in ids:
            ids[group].append(sid)
    facts = discover(rom, prof, inv, ids.get("script", []), ids.get("system", []))
    res = render(facts, prof, inv)
    res.update(profile=prof.name, rom_sha1=prof.sha1)
    res = to_json(res)
    prev_path = out / "reach.json"
    previous = json.loads(prev_path.read_text(encoding="utf-8")) if prev_path.exists() else None
    # a narrower run keeps the stored study
    why = None if args.allow_fewer else fewer_strings(res, previous)
    if why:
        line("error", why, file=sys.stderr)
        return 1
    prev_path.write_text(json.dumps(res, ensure_ascii=False, indent=0), encoding="utf-8")
    (out / "reach-facts.json").write_text(json.dumps(document(facts), ensure_ascii=False, indent=0), encoding="utf-8")
    rep = report(res, previous)
    (out / "reach-report.md").write_text(rep, encoding="utf-8")
    line("wrote", prev_path)
    line("report", out / "reach-report.md")
    from collections import Counter
    for status, count in sorted(Counter(r["class"] for r in res["strings"].values()).items()):
        line("reach", status)
        line("count", f"{count} entries")


@command("discover graphics")
def graphics(args):
    """Write referenced LZ77 graphics candidates with indexed PNGs and evidence."""
    from ..discover import graphics as gfx
    from ..build.graphics import graphics_document

    prof = Profile.by_name(args.profile)
    rom = prof.load_rom(args.rom)
    result = gfx.scan(rom)
    out = Path(args.out) if args.out else args.build_root / "discovery" / prof.name / "graphics"
    rows = None
    if args.catalog_graphics:
        rows = graphics_document(Path(args.catalog_graphics))["graphic"]
    doc = gfx.write(result, rom, out, rows=rows, render=not args.no_render,
                    limit=args.limit, profile=prof.name)
    line("wrote", out / "index.json")
    line("report", out / "report.md")
    line("candidates", f"{doc['stats']['candidates']} graphics candidates")
    line("rejected", f"{doc['stats']['rejected']} false candidates")
    line("rendered", f"{sum(len(c['renders']) for c in doc['candidates'])} indexed PNGs"
                     if not args.no_render else "skipped")
    if "recall" in doc:
        rec = doc["recall"]
        line("recall", f"{len(rec['matched'])} catalogue rows matched, "
                       f"{len(rec['raw'])} raw rows matched, {len(rec['missing'])} missing; "
                       f"complete={rec['complete']}")
    return 0


@command("discover flag")
def flag(args):
    """Write text-carrying flags into source/graphics.toml."""
    import json
    from ..discover import graphics as gfx

    path = Path(args.graphics) if args.graphics else None
    if path is None:
        project = getattr(args, "project", None)
        path = project.graphics if project is not None and project.graphics is not None else None
    if path is None:
        line("error", "no project source/graphics.toml; pass --graphics")
        return 1
    flags = []
    if args.flags:
        flags.extend(json.loads(Path(args.flags).read_text(encoding="utf-8")))
    if args.id:
        if not args.category or not args.reason:
            line("error", "--id needs --category and --reason")
            return 1
        flags.extend({"id": gid, "category": args.category, "reason": args.reason} for gid in args.id)
    if not flags:
        line("error", "no flags; use --id or --flags")
        return 1
    candidates = json.loads(Path(args.index).read_text(encoding="utf-8")) if args.index else None
    rom = Profile.by_name(args.profile).load_rom(args.rom) if args.index else None
    try:
        updated = gfx.write_flags(path, flags, candidates, rom)
    except (OSError, ValueError) as exc:
        line("error", exc)
        return 1
    for gid in updated:
        line("flagged", gid)
    line("wrote", path)
    return 0


@command("discover check")
def profile_check(args):
    """Coverage per section; overlay facts, granularity differences and contradictions."""
    from ..profile import check
    from ..profile.profile import locate, name_of
    path = locate(args.profile)
    result = check.run(path)
    require_names("section", args.section or [],
                  [name for section in result.sections for name in (section.name, section.name.split(".")[0])])
    line("profile", name_of(path))
    chosen = [s for s in result.sections if not args.section or s.name in args.section
              or s.name.split(".")[0] in args.section]
    for section in chosen:
        line("section", section.name)
        line("kind", section.kind)
        line("facts", f"{section.facts} facts")
        line("generated", f"{section.generated} facts")
        line("overlay", f"{section.overlay} facts")
        for outcome, count in section.counts.items():
            line(outcome, f"{count} facts")
        line("coverage", f"{section.covered / section.rom_facts:.0%}" if section.rom_facts else "not-applicable")
    for source, counts in check.tier_counts(result, args.section).items():
        line("tier source", source)
        for tier, count in counts.items():
            line(tier, f"{count} facts")
    names = {s.name for s in chosen}
    for comparison in result.comparisons + result.conflicts:
        if comparison.fact.section not in names or (
                args.summary and comparison.outcome not in ("granularity", "contradicts")):
            continue
        line("fact", comparison.fact.id)
        line("status", comparison.outcome)
        if comparison.outcome == "granularity":
            line("generated", ", ".join(fact.id for fact in comparison.related))
        for field in comparison.fields:
            line("field", field)
            if comparison.outcome == "contradicts":
                line("generated", check._field(comparison.generated, field))
                line("overlay", check._field(comparison.fact, field))
    if result.contradictions:
        line("error", f"profile contradictions: {len(result.contradictions)} facts")
    if args.require_shortfalls:
        from ..profile import residue
        missing = residue.missing_coverage(result, residue.read(path, Profile.load(path).sha1))
        for identifier in missing:
            line("unnamed shortfall", identifier)
        if missing:
            line("error", f"unnamed coverage shortfalls: {len(missing)} facts")
    else:
        missing = []
    return int(bool(result.contradictions or missing))


@command("discover convert")
def profile_convert(args):
    """Move a flat profile into profiles/<rom-id>/: the whole profile as overlay, an empty generated profile."""
    from ..profile.convert import convert
    from ..profile.profile import locate
    from ..profile import schema
    source = locate(args.profile)
    target = convert(source)
    for path in [target / schema.GENERATED, *schema.overlay_files(target), target / schema.REPORT]:
        line("wrote", path)
    line("removed", source)
    return 0


def register(sub):
    add(sub, "candidates", discover_candidates)
    run = add(sub, "run", discover)
    run.add_argument("--out", help="generated profile directory (default: selected profile directory, else build/discovery/<profile>)")
    run.add_argument("--inventory", help="explicit ROM-matched inventory for scoped free-slot comparison")
    run.add_argument("--observations", help="recorded runtime samples bound to this ROM and its facts")
    obs = add(sub, "observe", observe)
    source = obs.add_mutually_exclusive_group(required=True)
    source.add_argument("--samples", help="scenario sample plan JSON")
    source.add_argument("--convert", help="convert a schema 1.0.0 record after ROM and fixture checks")
    obs.add_argument("--inputs-root", default=".", help="root for legacy state/save inputs during conversion")
    obs.add_argument("--out", required=True, help="new observation JSON file")
    regen = add(sub, "regenerate", regenerate)
    regen.add_argument("--work-dir", help="scratch parent for temporary regenerated studies and profiles")
    iv = add(sub, "inventory", inventory)
    gx = add(sub, "graphics", graphics)
    gx.add_argument("--out", help="output directory (default: build/discovery/<profile>/graphics)")
    gx.add_argument("--catalog-graphics", help="graphics document for comparison and recall")
    gx.add_argument("--no-render", action="store_true", help="write the candidate index only")
    gx.add_argument("--limit", type=int, default=None, help="render at most this many candidates (tests)")
    fl = add(sub, "flag", flag)
    fl.add_argument("--graphics", default=None, help="source/graphics.toml (default: the project's)")
    fl.add_argument("--id", action="append", default=[], help="graphic row id (G01) or candidate id (g1FC08A0); repeat")
    fl.add_argument("--category", choices=["translate", "optional", "keep"], default=None, help="classification to apply to the selected graphics")
    fl.add_argument("--reason", default=None, help="evidence for the graphics classification")
    fl.add_argument("--flags", default=None, help="JSON file of [{\"id\", \"category\", \"reason\"}]")
    fl.add_argument("--index", default=None, help="discovery index for materializing new candidate rows")
    add(sub, "context", context)
    rc = add(sub, "reach", reach)
    rc.add_argument("--group", action="append", choices=["script", "system"],
                    help="catalog group to classify; repeat for more (default: script and system)")
    rc.add_argument("--allow-fewer", action="store_true",
                    help="write the study even when it classifies fewer strings than build/reach.json")
    ck = add(sub, "check", profile_check)
    ck.add_argument("--section", action="append", help="limit the report to a section or top-level table; repeat")
    ck.add_argument("--summary", action="store_true",
                    help="the coverage table, granularity differences and contradictions only")
    ck.add_argument("--require-shortfalls", action="store_true", help="fail on unnamed below-target facts")
    add(sub, "convert", profile_convert)
