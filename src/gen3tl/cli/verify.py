"""Profile proofs, emulator screenshots, save fixtures and conversion, golden outputs."""

from __future__ import annotations

from pathlib import Path

from ..core.terminal import table
from ..profile.profile import Profile
from .common import _save_layout_and_charmap, add, command, line, write_options


@command("verify profile")
def profile_proof(args):
    """Record a merged profile and its fact ids, or compare records by bytes or fact sets."""
    from ..verify import profile

    if args.set_mode and not args.compare:
        raise ValueError("--set requires --compare")
    if args.allow_added_prefix is not None and not args.set_mode:
        raise ValueError("--allow-added-prefix requires --set")
    if args.record:
        records = profile.record(args.profile, args.record)
        table(("file", "bytes"), [(name, len(content)) for name, content in records.items()])
        line("wrote", args.record)
    elif args.set_mode:
        results = profile.compare_sets(*args.compare, allow_added_prefix=args.allow_added_prefix)
        line("mode", "set")
        line("prefix", args.allow_added_prefix or "none")
        table(("fact / section", "before", "after", "status", "difference"),
              [(row.path, row.before, row.after, "allowed" if row.allowed else "different", row.reason)
               for row in results])
        failed = next((row for row in results if not row.allowed), None)
        if failed:
            line("error", f"profile proof differs: {failed.path} ({failed.reason})")
            return 1
    else:
        results = profile.compare(*args.compare)
        table(("file", "before bytes", "after bytes", "status", "path", "difference"),
              [(row.file, row.before_bytes, row.after_bytes, "equal" if row.equal else "different",
                row.path or "-", row.reason or "-") for row in results])
        failed = next((row for row in results if not row.equal), None)
        if failed:
            line("error", f"profile proof differs: {failed.file}: {failed.path} ({failed.reason})")
            return 1
    line("result", "passed")
    return 0


@command("verify golden")
def golden(args):
    """Regenerate every golden output and compare or record its SHA-256."""
    from ..verify.golden import run
    return run(args)


def _toolkit_bootstrap() -> str:
    """Import the toolkit independently of the selected workspace."""
    source = str(Path(__file__).resolve().parents[2])
    return f"import sys; sys.path.insert(0, {source!r}); "


def _recording_failure(result, tag, out):
    """Keep subprocess diagnostics outside terminal output."""
    out.mkdir(parents=True, exist_ok=True)
    log = out / f"{tag}-error.log"
    log.write_text(result.stderr, encoding="utf-8")
    line("error", f"recording failed: {tag}, exit {result.returncode}")
    line("log", log)
    line("hint", "recording: diagnostic log")
    return 1


@command("verify shots")
def shots(args):
    """Run a scenario on the base ROM and a built ROM; write side-by-side screenshots."""
    import subprocess
    import sys as _sys
    from pathlib import Path
    from ..check.render import contact_sheet
    from ..verify.headless import HeadlessError, hatch_checks, parse_scenario, side_by_side
    prof = Profile.by_name(args.profile)
    base = prof.load_rom(args.rom)
    scen = Path(args.scenario)
    try:
        checks = hatch_checks(parse_scenario(scen.read_text(encoding="utf-8"), scen.parent))
    except HeadlessError as e:
        line("error", e)
        return 1
    out = Path(args.out) if args.out else args.project.build / "screens" / scen.stem
    out.mkdir(parents=True, exist_ok=True)
    # a libretro core hosts one game per process
    for tag, rom in (("base", base.path), (args.lang, Path(args.built))):
        code = (_toolkit_bootstrap() + "from pathlib import Path; "
                "from gen3tl.verify.headless import run_scenario; "
                f"run_scenario(Path({str(rom)!r}), Path({str(scen)!r}).read_text(encoding='utf-8'), "
                f"Path({str(out)!r}), {tag!r}, layout={prof['save']!r}, directory=Path({str(scen.parent)!r}))")
        r = subprocess.run([_sys.executable, "-c", code], capture_output=True, text=True)
        if r.returncode:
            return _recording_failure(r, tag, out)
    pairs = []
    for en in sorted(out.glob("base-*.png")):
        translated = out / en.name.replace("base-", f"{args.lang}-", 1)
        if translated.exists():
            pairs.append(side_by_side(en, translated, out / en.name.replace("base-", "pair-", 1)))
    if pairs:
        sheet = contact_sheet(pairs, out / "sheet.png", cols=2)
        line("pairs", f"{len(pairs)} pairs")
        line("wrote", sheet)
    if not checks:
        return 0
    from ..verify.savefile import Layout, SaveError
    from ..verify.savefixture import catalog_species, hatch_check
    layout = Layout.from_dict(prof["save"])
    table = _save_layout_and_charmap(prof, args.lang)[1]
    failed = 0
    for name, slot, species in checks:
        try:
            english, translated = catalog_species(args.project.catalog, species)
            results = []
            # an untranslated name keeps the English one
            for tag, expected in (("base", english), (args.lang, translated or english)):
                save = out / f"{tag}-{name}.sav"
                results.append((tag, save, hatch_check(save.read_bytes(), layout, slot, species, expected, table)))
        except (ValueError, SaveError) as e:
            line("error", e)
            return 1
        for tag, save, r in results:
            line("save", save)
            line("language", tag)
            line("slot", slot)
            line("species", r["species"])
            line("name", r["name"])
            line("catalog", r["catalog"])
            line("status", "passed" if r["passed"] else "failed")
            failed += not r["passed"]
    line("verified", "failed" if failed else "passed")
    return 1 if failed else 0


@command("verify story")
def story(args):
    """Record a checkpoint on the base ROM and a built ROM, press A repeatedly, pair frames from a pairs file."""
    import csv
    import subprocess
    import sys as _sys
    from pathlib import Path
    from ..check.render import contact_sheet
    from ..verify.headless import side_by_side
    prof = Profile.by_name(args.profile)
    base = prof.load_rom(args.rom)
    pairs_file = Path(args.pairs)
    rows = [r for r in csv.DictReader(
        (ln for ln in pairs_file.read_text(encoding="utf-8").splitlines() if not ln.startswith("#")),
        delimiter="\t")]
    out = Path(args.out) if args.out else args.project.build / "screens" / pairs_file.stem
    for tag, rom in (("base", base.path), (args.lang, Path(args.built))):
        code = (_toolkit_bootstrap() + "from pathlib import Path; "
                "from gen3tl.verify.headless import record_presses; "
                f"record_presses(Path({str(rom)!r}), {args.state!r}, {args.presses}, Path({str(out / tag)!r}))")
        r = subprocess.run([_sys.executable, "-c", code], capture_output=True, text=True)
        if r.returncode:
            return _recording_failure(r, tag, out)
    # overview sheets of every frame for visual review
    for tag in ("base", args.lang):
        frames = sorted((out / tag).glob("[0-9][0-9][0-9].png"))
        for k in range(0, len(frames), 24):
            contact_sheet(frames[k:k + 24], out / f"{tag}-frames-{k // 24}.png", cols=6, shrink=2)
    made = []
    for r in rows:
        en = out / "base" / f"{int(r['source']):03d}.png"
        translated = out / args.lang / f"{int(r['translated']):03d}.png"
        made.append(side_by_side(en, translated, out / f"pair-{r['name']}.png"))
    if made:
        contact_sheet(made, out / "sheet.png", cols=2)
    line("pairs", f"{len(made)} pairs")
    line("source", pairs_file)
    line("wrote", out)
    return 0


@command("verify save-convert")
def save_convert(args):
    """Convert saved names using the selected language pack."""
    from ..verify.savefile import main as convert_main
    prof = Profile.by_name(args.profile)
    layout, table, shared = _save_layout_and_charmap(prof, args.lang)
    argv = [args.save] + ([args.out] if args.out else [])
    argv += [x for opt, value in (("--from-rom", args.from_rom), ("--to-rom", args.to_rom), ("--game", args.game))
             if value for x in (opt, value)]
    argv += ["--edit"] if args.edit else []
    argv += ["--player-name", args.player_name] if args.player_name is not None else []
    argv += [x for r in args.rename for x in ("--rename", r)] + [x for b in args.box_name for x in ("--box-name", b)]
    return convert_main(argv + (["--dry-run"] if args.dry_run else []), layout=layout, charmap=table,
                        charmap_target=shared)


@command("verify save-fixture")
def save_fixture(args):
    """Make a test save with the game: Pokemon in every storage area, saved from the Start menu."""
    from pathlib import Path
    from ..verify.savefile import Layout
    from ..verify.savefixture import make_fixture
    prof = Profile.by_name(args.profile)
    rom = args.game_rom or str(prof.load_rom(args.rom).path)
    line("wrote", make_fixture(rom, Layout.from_dict(prof["save"]), Path(args.out), args.state))
    return 0


@command("verify save-check")
def save_check(args):
    """Boot a ROM with a save, continue, screenshot the field, party screen and summary."""
    from pathlib import Path
    from ..verify.savefixture import check_save
    for p in check_save(args.game_rom, args.save, Path(args.out)):
        line("wrote", p)
    return 0


@command("verify ui-shots")
def ui_shots(args):
    """Party, summary, bag, battle, Pokedex and PC screens on the base ROM and a built ROM, side by side."""
    import subprocess
    import sys as _sys
    from pathlib import Path
    from ..verify.headless import side_by_side
    from ..check.render import contact_sheet
    prof = Profile.by_name(args.profile)
    base = prof.load_rom(args.rom)
    out = Path(args.out)
    for tag, rom, sav in (("base", base.path, args.base_save), (args.lang, Path(args.built), args.translated_save)):
        code = (_toolkit_bootstrap() +
                "from gen3tl.verify.savefixture import battle_session, check_save, pc_session; "
                f"check_save({str(rom)!r}, {str(sav)!r}, {str(out / tag)!r}); "
                f"battle_session({str(rom)!r}, {str(sav)!r}, {str(out / tag)!r}, {args.species}, {prof['save']!r}); "
                f"pc_session({str(rom)!r}, {str(sav)!r}, {str(out / tag)!r})")
        r = subprocess.run([_sys.executable, "-c", code], capture_output=True, text=True)
        if r.returncode:
            return _recording_failure(r, tag, out)
    pairs = [side_by_side(en, out / args.lang / en.name, out / f"pair-{en.name}") for en in sorted((out / "base").glob("*.png"))]
    contact_sheet(pairs, out / "sheet.png", cols=1)
    line("pairs", f"{len(pairs)} pairs")
    line("wrote", out / "sheet.png")
    return 0


def register(sub):
    proof = add(sub, "profile", profile_proof)
    modes = proof.add_mutually_exclusive_group(required=True)
    modes.add_argument("--record", metavar="DIRECTORY", help="write document.json and fact-ids.json in a new directory")
    modes.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"),
                       help="byte-compare two record directories")
    proof.add_argument("--set", dest="set_mode", action="store_true",
                       help="with --compare: compare sorted fact ids and canonical values; report reordered sections")
    proof.add_argument("--allow-added-prefix", metavar="PREFIX",
                       help="with --set: allow added fact ids only under this prefix")
    sh = add(sub, "shots", shots)
    sh.add_argument("scenario", help="emulator scenario script file")
    sh.add_argument("--built", default=None, help="translated ROM to inspect")
    sh.add_argument("--out", default=None, help="screenshot directory; omitted: build/screens/<scenario-stem>")
    st = add(sub, "story", story)
    st.add_argument("pairs", help="TSV with columns name, source, translated, caption (frame numbers)")
    st.add_argument("--state", required=True, help="checkpoint name in verify/states/")
    st.add_argument("--presses", type=int, required=True, help="number of dialogue advances to capture")
    st.add_argument("--built", default=None, help="translated ROM to inspect")
    st.add_argument("--out", default=None, help="screenshot directory; omitted: build/screens/<pairs-stem>")
    bs = add(sub, "ui-shots", ui_shots)
    bs.add_argument("--base-save", required=True, help="save for the base ROM (party needed)")
    bs.add_argument("--translated-save", dest="translated_save", required=True,
                    help="the same save converted for the built ROM")
    bs.add_argument("--built", default=None, help="translated ROM to inspect")
    bs.add_argument("--species", type=int, default=16, help="species identifier to show in the comparison")
    bs.add_argument("--out", default=None, help="directory for paired interface screenshots")
    sf = add(sub, "save-fixture", save_fixture)
    sf.add_argument("--game-rom", default=None, help="ROM to run (default: the base ROM)")
    sf.add_argument("--state", default="e6", help="checkpoint in verify/states with a working Start menu")
    sf.add_argument("--out", default=None, help="directory for generated saves and screenshots")
    sk = add(sub, "save-check", save_check)
    sk.add_argument("save", help="input save file")
    sk.add_argument("--game-rom", required=True, help="ROM to load with the save")
    sk.add_argument("--out", default=None, help="directory for save verification screenshots")
    sc = add(sub, "save-convert", save_convert)
    sc.add_argument("save", help="input save file")
    sc.add_argument("out", nargs="?", help="output save path; with --edit: <save>-edited.<ext> beside the input")
    sc.add_argument("--from-rom", help="ROM the save was made with (with --edit: one of the games to detect)")
    sc.add_argument("--to-rom", help="ROM the save is converted for (with --edit: the other game to detect)")
    sc.add_argument("--edit", action="store_true", help="edit names only, for the ROM the save was made with")
    sc.add_argument("--game", choices=("original", "translated"),
                    help="with --edit: the ROM the save was made with (default: detected from the default names)")
    write_options(sc, check_help="report only; with --edit, list every stored name")
    sc.add_argument("--player-name", metavar="NEW", help="rename the player and the OT names that match the player's")
    sc.add_argument("--rename", metavar="PLACE=NEW", action="append", default=[],
                    help='rename the name at a place the report lists ("party 1=Sparky", "box 3 slot 5=Rex")')
    sc.add_argument("--box-name", metavar="N=NEW", action="append", default=[], help="rename box N")
    g = add(sub, "golden", golden)
    write_options(g, dest="record", write_value=True, default_write=False, write_aliases=("--record",),
                  check_help="compare the golden outputs with the manifest (default)",
                  write_help="refresh the golden manifest after a deliberate input change")
    g.add_argument("--work-dir", default=None, help="keep generated outputs in a new or empty directory")
