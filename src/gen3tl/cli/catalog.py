"""Source catalogs, PO export, translation memory, batches and progress."""

from __future__ import annotations

from pathlib import Path

from ..catalog.merge import THRESHOLD
from ..core.charmap import Charmap
from ..profile.profile import Profile
from .common import _limits_and_matcher, add, command, line, write_options


def source_directory(root: Path, profile: str) -> Path:
    """Keep source downloads together under their profile."""
    return root / "translation/sources" / profile


@command("catalog source")
def source_catalog(args):
    """Generate language-neutral, unverified source catalogs for community review."""
    from ..discover.discovery import inspect, publish_catalog, source_catalog as generate
    from ..web.editordata import write_languages
    from ..project import Registry
    prof = Profile.by_name(args.profile)
    root = Registry.load(Path(args.project_root) if args.project_root else None).root
    out = Path(args.out) if args.out else source_directory(root, prof.name)
    metadata = generate(inspect(prof, prof.load_rom(args.rom)), out)
    if args.editor_out:
        editor_root = Path(args.editor_out)
        publish_catalog(out, editor_root)
        write_languages(editor_root)
    line("wrote", out)
    line("entries", f"{metadata['entries']} entries")
    line("support", "discovery")
    return 0


@command("catalog prepare")
def catalog_prepare(args):
    """Prepare editable source catalogs."""
    from ..catalog.draftcatalog import prepare
    prof = Profile.by_name(args.profile)
    manifest = prepare(prof, prof.load_rom(args.rom), args.lang, args.project.translation)
    line("project", args.project.key)
    line("entries", f"{manifest['entries']} entries")
    line("translations", f"{manifest['translations']} translations")
    line("support", "draft")
    return 0


@command("catalog export")
def export(args):
    """Export the inventory and context to the PO files of the catalog, tagging never-shown text."""
    import json
    from pathlib import Path
    from ..discover import inventory as inv_mod
    from ..catalog.catalog import export as do_export
    from ..build.font import FontTable
    prof = Profile.by_name(args.profile)
    rom = prof.load_rom(args.rom)
    font = FontTable.from_profile(prof)[0]
    inv = inv_mod.load(args.project.build / "inventory.json")
    ctx = json.loads((args.project.build / "context.json").read_text(encoding="utf-8"))
    # the reach study and reviewed overrides
    from ..catalog.catalog import never_shown
    reach_path = args.project.build / "reach.json"
    reach_doc = json.loads(reach_path.read_text(encoding="utf-8")) if reach_path.exists() else None
    if reach_doc is None:
        line("missing", reach_path)
        line("reach", "profile")
    reach_rules = prof.get("reach", {})
    never = never_shown(reach_doc, reach_rules.get("never_shown", []),
                        reach_rules.get("observed", []))
    from ..catalog import keys
    aliases = keys.for_profile(prof)
    check = getattr(args, "check", False)
    keys_changed = False
    if aliases:   # entries added to the inventory get keys
        extended = keys.assign(inv, ctx, prof, bytes(rom.data), keys.rom_events(rom, prof), aliases)
        if extended.rows != aliases.rows:
            keys_changed = True
            line("keys", f"{len(extended) - len(aliases)} keys")
            if not check:
                keys.write(keys.alias_path(prof.sha1), extended)
            line("stale" if check else "wrote", keys.alias_path(prof.sha1))
        aliases = extended
    # project catalogs are by key; the snapshot has English
    digests = None
    if args.project.snapshot.is_file():
        from ..web.editorsnapshot import digests as snapshot_digests, load as load_snapshot
        digests = snapshot_digests(load_snapshot(args.project.snapshot))
    stats = do_export(inv, ctx, Charmap(), lambda b: font.width(rom, b), Path(args.po_root), prof.name,
                      language=args.lang, never=never, aliases=aliases, key_only=args.project.game,
                      digests=digests, check=check)
    for k, v in stats.items():
        if isinstance(v, list):
            for value in v:
                line(k, value)
        else:
            line(k, f"{v} {'files' if 'files' in k else 'entries'}")
    if check:
        stale = bool(keys_changed or stats["changed_files"])
        line("status", "stale" if stale else "unchanged")
        return int(stale)


@command("catalog extract")
def catalog_extract(args):
    """Regenerate the English source catalog from the base ROM through the profile's facts and studies."""
    from ..catalog import extract
    from ..catalog.po import read
    from ..project import ProjectError
    prof = Profile.by_name(args.profile)
    project = args.project
    out = Path(args.out) if args.out else project.build / "catalog.pot"
    try:
        entries = extract.entries(prof, prof.load_rom(args.rom), project.build, project.root)
    except (ProjectError, OSError, ValueError) as exc:
        line("error", exc)
        return 1
    before = out.read_bytes() if out.is_file() else None
    diff = extract.changes(read(out)[1] if before is not None else [], entries)
    extract.write(out, entries, prof)
    line("entries", f"{len(entries)} entries")
    for outcome in ("added", "removed", "changed"):
        line(outcome, f"{len(diff[outcome])} entries")
    line("status", "written" if before is None else "unchanged" if out.read_bytes() == before else "updated")
    line("wrote", out)
    return 0


@command("catalog key-only")
def catalog_key_only(args):
    """Rewrite the catalogs of every language of the profile in the key-only form."""
    from ..catalog import extract
    from ..catalog.po import KEY_ONLY, SOURCE_FIELD, key_only, parse as parse_po, with_source, write
    from ..project import Registry
    prof = Profile.by_name(args.profile)
    source = Path(args.english) if args.english else args.project.build / "catalog.pot"
    try:
        found = extract.load(source, prof.sha1)
    except (OSError, ValueError) as exc:
        line("file", source)
        line("error", exc)
        return 1
    projects = [p for p in Registry.load(args.project.registry_root).projects if p.profile == prof.name]
    plans, problems = [], []
    for project in projects:
        for path in sorted(project.catalog.rglob("*.po")):
            header, entries, dead = parse_po(path)
            if header.get(SOURCE_FIELD) == KEY_ONLY:
                continue
            for e in entries:
                joined = key_only(e)
                if e.msgctxt in found:
                    with_source(joined, found[e.msgctxt])
                if e.msgctxt not in found or joined != e:   # lost unless the ROM gives it back
                    problems.append(e.msgctxt)
            plans.append((project, path, {**header, SOURCE_FIELD: KEY_ONLY}, entries, dead))
    line("source", source)
    for key in problems[:20]:
        line("entry", key)
    if problems:
        line("error", f"{len(problems)} entries differ from the English source catalog")
        return 1
    for project, path, header, entries, dead in plans:
        if not args.dry_run:
            write(path, entries, header, dead)
    for project in projects:
        rewritten = [plan for plan in plans if plan[0] == project]
        line("language", project.language)
        line("files", f"{len(rewritten)} files")
        line("entries", f"{sum(len(plan[3]) for plan in rewritten)} entries")
    line("status", ("planned" if args.dry_run else "rewritten") if plans else "unchanged")
    return 0


@command("catalog rekey")
def rekey(args):
    """Key the catalog entries by stable content keys."""
    import json
    from collections import Counter
    from ..core.terminal import table
    from ..catalog import keys
    from ..catalog.rekey import migrate
    from ..discover import inventory as inv_mod
    prof = Profile.by_name(args.profile)
    rom = prof.load_rom(args.rom)
    inv = inv_mod.load(args.project.build / "inventory.json")
    ctx = json.loads((args.project.build / "context.json").read_text(encoding="utf-8"))
    for name, doc in (("inventory", inv), ("context", ctx)):
        if doc.get("rom_sha1") != prof.sha1:
            line("file", f"build/{name}.json")
            line("profile", prof.name)
            line("error", "profile mismatch")
            return 1
    known = keys.for_profile(prof)
    aliases = keys.assign(inv, ctx, prof, bytes(rom.data), keys.rom_events(rom, prof), known)
    from ..project import Registry
    registry = Registry.load(args.project.registry_root)
    path = keys.alias_path(prof.sha1, registry.root)
    if aliases.rows != known.rows and not args.dry_run:
        keys.write(path, aliases)
    forms = Counter(k.split(":")[0] for k, _, _ in aliases.rows)
    line("keys", f"{len(aliases)} keys")
    line("file", path)
    table(("form", "keys"), sorted(forms.items()))
    projects = [p for p in registry.projects if p.profile == prof.name]
    report = migrate(prof, aliases, projects, args.dry_run)
    line("entries", f"{report['entries']} entries")
    line("catalogs", f"{report['catalogs']} catalogs")
    for project in projects:
        line("language", project.language)
    for f in report["files"]:
        line("file", f)
    for unknown in report["unknown"][:20]:
        line("unknown", unknown)
    changed = aliases.rows != known.rows or report["catalogs"] or report["files"]
    status = ("planned" if args.dry_run else "rewritten") if changed else "unchanged"
    line("status", "failed" if report["unknown"] else status)
    return 1 if report["unknown"] else 0


@command("catalog merge")
def catalog_merge(args):
    """Carry a catalog into the source catalog of a new base ROM."""
    from ..catalog.extract import english
    from ..catalog.merge import OUTCOMES, load, merge, write
    from ..catalog.po import KEY_ONLY, SOURCE_FIELD
    old, new = Path(args.catalog), Path(args.source)
    out = Path(args.out) if args.out else None
    for path in (old, new):
        if not path.exists():
            line("file", path)
            line("error", "missing")
            return 1
    problem = None
    if out is None and not args.dry_run:
        problem = "--out is required unless --dry-run"
    elif out is not None and new.is_dir() and (out.is_file() or out.is_dir() and any(out.iterdir())):
        problem = "the merged catalog needs a new or empty directory"
    elif out is not None and not new.is_dir() and out.is_dir():
        problem = "a source file merges into a file"
    if problem:
        line("file", out)
        line("error", problem)
        return 1
    try:
        current = load(old)
        # a key-only catalog compares the English of its own base
        keyed = any(e.keyed for _, entries, _ in current.values() for e in entries)
        source = Path(args.english) if args.english else None
        english([e for _, entries, _ in current.values() for e in entries], source)
        english([e for _, _, dead in current.values() for e in dead], source, required=False)
        result = merge(current, load(new), args.threshold, None if new.is_dir() else new.name)
    except (OSError, ValueError) as exc:
        line("error", exc)
        return 1
    if keyed:   # and the merged catalog keeps the form
        for header, _, _ in result.files.values():
            header[SOURCE_FIELD] = KEY_ONLY
    line("file", old)
    line("source", new)
    for outcome in OUTCOMES:
        line(outcome, f"{result.counts[outcome]} entries")
    line("fuzzy", f"{result.counts['fuzzy']} entries")
    if result.counts["retained"]:
        line("retained", f"{result.counts['retained']} entries")
    if args.dry_run:
        line("status", "planned")
        return 0
    from ..catalog import entrynotes
    notes_path = entrynotes.path(old) if old.is_dir() else entrynotes.for_file(old)
    try:
        write(result, out, single=not new.is_dir(), notes_path=notes_path)
    except (OSError, ValueError) as exc:
        line("error", exc)
        return 1
    line("status", "merged")
    line("wrote", out)
    return 0


@command("catalog remap")
def catalog_remap(args):
    """Rewrite the key-named files of a merge by its match list."""
    from ..catalog.extract import english
    from ..catalog.merge import OUTCOMES, load, merge
    from ..catalog.rekey import remap_files
    old, new = Path(args.catalog), Path(args.source)
    for path in (old, new):
        if not path.exists():
            line("file", path)
            line("error", "missing")
            return 1
    scopes = {"translation": args.translation, "reference": args.reference, "profile-file": args.profile_file}
    for values in scopes.values():
        for path in values or []:
            if not Path(path).exists():
                line("file", path)
                line("error", "missing")
                return 1
    for path in args.reference or []:
        if not Path(path).is_file():
            line("file", path)
            line("error", "a reference must be a file")
            return 1
    if not any(values for values in scopes.values()):
        line("error", "one of --translation, --reference or --profile-file is required")
        return 1
    try:
        current = load(old)
        source = Path(args.english) if args.english else None
        english([e for _, entries, _ in current.values() for e in entries], source)
        english([e for _, _, dead in current.values() for e in dead], source, required=False)
        result = merge(current, load(new), args.threshold, None if new.is_dir() else new.name)
        retained = [e.msgctxt for _, (_, _, dead) in result.files.items() for e in dead]
        report = remap_files(result.matches, retained, [Path(p) for p in args.translation or []],
                             [Path(p) for p in args.reference or []], [Path(p) for p in args.profile_file or []],
                             args.dry_run)
    except (OSError, ValueError) as exc:
        line("error", exc)
        return 1
    line("file", old)
    line("source", new)
    for outcome in OUTCOMES:
        line(outcome, f"{result.counts[outcome]} entries")
    line("fuzzy", f"{result.counts['fuzzy']} entries")
    if result.counts["retained"]:
        line("retained", f"{result.counts['retained']} entries")
    line("rewrites", f"{report['rewrites']} keys")
    line("files", f"{len(report['files'])} files")
    for path in report["files"]:
        line("file", path)
    for pair in report["pairs"]:
        line("rewrite", f"{pair['file']}: {pair['old']} -> {pair['new']} ({pair['count']})")
    for old_key in report["unmatched"]:
        line("unmatched", old_key)
    line("status", "planned" if args.dry_run and report["files"]
         else "rewritten" if report["files"] else "unchanged")
    return 0


@command("catalog tm")
def tm_report(args):
    """Write build/tm-report.md: repeated English strings and translation conflicts."""
    from pathlib import Path
    from ..catalog.catalog import tm_report as rep
    text = rep(Path(args.po_root))
    out = args.project.build / "tm-report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    line("report", out)


@command("catalog batch-plan")
def batch_plan(args):
    """Plan translation batches in story order: plan.tsv and plan.json in the review batches."""
    import json
    from pathlib import Path
    from ..lang import termbase as tb
    from ..catalog.batchplan import map_levels, plan, trainer_levels, write
    from ..lang.fill import fill_tables
    from ..catalog.catalog import slug
    prof = Profile.by_name(args.profile)
    rom = prof.load_rom(args.rom)
    trainers = next(t for t in prof["table"] if t["name"] == "trainer_names")
    ctx = json.loads((args.project.build / "context.json").read_text(encoding="utf-8"))["strings"]
    levels = map_levels(ctx, trainer_levels(bytes(rom.data), trainers), slug)
    po_root = Path(args.po_root)
    reps = fill_tables(prof.get("table", []), po_root, tb.load(Path(args.glossary)), dry_run=True)
    filled = {i for r in reps for i in r.filled_ids}
    approved, active = {}, {}
    for src_file in sorted(args.project.batches.glob("*/source.json")):
        st = src_file.parent / "status.json"
        ids = [x["id"] for x in json.loads(src_file.read_text(encoding="utf-8"))]
        if st.exists() and json.loads(st.read_text(encoding="utf-8")).get("approved"):
            approved[src_file.parent.name] = ids
        else:  # exported and in review: entries are not replanned
            active[src_file.parent.name] = ids
    first = prof.get("batches", {}).get("first", [])
    # strings the reachability study finds unreachable are deferred
    reach_path = Path(args.defer)
    deferred = set()
    if reach_path.exists():
        deferred = {sid for sid, r in json.loads(reach_path.read_text(encoding="utf-8"))["strings"].items()
                    if r["class"] == "unreachable"}
    batches = plan(po_root, levels, first, filled, approved, args.size, args.min_size, args.max_words, active,
                   deferred, set(prof.get("strings", {}).get("exclude", [])))
    tsv, js = write(batches, args.project.batches)
    todo = [b for b in batches if b.kind not in ("done", "active", "deferred")]
    held = [b for b in batches if b.kind == "deferred"]
    line("pending", f"{len(todo)} batches")
    line("entries", f"{sum(len(b.ids) for b in todo)} entries")
    line("words", f"{sum(b.words for b in todo)} words")
    line("started", f"{len(batches) - len(todo) - len(held)} batches")
    line("deferred", f"{sum(len(b.ids) for b in held)} entries")
    line("reach", reach_path if reach_path.exists() else "missing")
    line("wrote", tsv)
    line("wrote", js)
    return 0


@command("catalog batch-export")
def batch_export(args):
    """Export catalog entries (by PO file or ids) into <batches>/<name>/source.json."""
    from pathlib import Path
    from ..catalog.batch import export_batch
    from ..catalog.catalog import load_catalog
    from ..core.terminal import require_names
    ids = list(args.ids or [])
    if args.plan is not None:   # the batch of this name in the plan
        import json
        plan_path = Path(args.plan) if args.plan else args.project.batches / "plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        require_names("planned batch", [args.name], (batch["name"] for batch in plan))
        ids += next(b["ids"] for b in plan if b["name"] == args.name)
    if args.file:
        for f in args.file:
            ids += [sid for sid, (p, e) in load_catalog(Path(args.po_root)).items() if p.resolve() == Path(f).resolve()]
    limits, matcher = _limits_and_matcher(args)
    # speakers of texts held by records
    from ..discover import inventory as inv_mod
    from ..catalog.batch import record_speakers
    prof = Profile.by_name(args.profile)
    rules = prof.get("context", {}).get("record_speakers", [])
    notes = record_speakers(rules, prof.load_rom(args.rom), inv_mod.load(args.project.build / "inventory.json")) \
        if rules else {}
    try:
        d = export_batch(args.name, ids, Path(args.po_root), limits, matcher, root=args.project.batches,
                         force=args.force, prefill_drafts=args.prefill, notes=notes)
    except ValueError as e:   # an approved batch
        line("error", e)
        return 1
    line("entries", f"{len(ids)} entries")
    line("wrote", d / "source.json")
    if args.prefill:
        import json
        n = json.loads((d / "status.json").read_text(encoding="utf-8"))["prefill"]
        line("prefilled", f"{n['filled']} entries")
        line("variants", f"{n['variants']} entries")
        line("wrote", d / "translation.json")
        line("wrote", d / "translator-notes.md")


@command("catalog batch-import")
def batch_import(args):
    """Import <batches>/<name>/translation.json into the PO files (as translated)."""
    from pathlib import Path
    from ..catalog.batch import import_batch
    r = import_batch(args.name, Path(args.po_root), args.project.batches)
    line("entries", f"{r['entries']} entries")
    line("changed", f"{r['changed']} entries")
    for entry in r["missing"]:
        line("missing", entry)


@command("catalog batch-status")
def batch_status(args):
    """Show the latest review round and finding count per pass."""
    from ..catalog.batch import review_state
    for p, st in review_state(args.name, args.project.batches).items():
        line("review", p)
        line("round", f"{st['round']} rounds")
        line("findings", f"{st['findings']} findings")


@command("catalog progress")
def progress(args):
    """Translation progress per topic: approved entries among those the game shows, words."""
    import json
    from pathlib import Path
    from ..core.terminal import table
    from ..catalog.progress import cells, count, file_topics, graphics_row, markdown, short
    prof = Profile.by_name(args.profile)
    reach_path = args.project.build / "reach.json"   # the study the editor data reads
    study = json.loads(reach_path.read_text(encoding="utf-8")) if reach_path.exists() else None
    parts = cells(count(Path(args.po_root), file_topics(prof), study=study))
    graphic = graphics_row(prof)
    after = cells([graphic])[1] if graphic is not None else []
    if not args.full:
        after = short(parts[0], after, parts[2])[1]
        parts = short(*parts)
    if args.markdown:
        report = args.project.build / "progress.md"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(markdown(*parts, after) + "\n", encoding="utf-8")
        line("report", report)
    else:
        labels = {"Topic": "topic", "Approved": "approved (entries)", "Translated or in review": "translated or in review (entries)",
                  "Total": "total (entries)", "Done": "done (%)", "Words done": "words (%)",
                  "Never shown": "never (entries)"}
        headers, body, overall = parts
        table([labels[heading] for heading in headers],
              ([value.removesuffix("%") if heading in ("Done", "Words done") else value
                for heading, value in zip(headers, row)] for row in [*body, overall, *after]))
    line("status", "ready")
    return 0


@command("catalog batch-approve")
def batch_approve(args):
    """Mark approved the entries of a batch whose last round of all three review passes is clean."""
    from pathlib import Path
    from ..catalog.batch import approve_batch
    result = approve_batch(args.name, Path(args.po_root), args.project.batches)
    line("batch", args.name)
    line("approved", f"{result['cleared_fuzzy']} entries")


@command("catalog approve")
def catalog_approve(args):
    """Mark approved the translated entries that a played version holds unchanged."""
    from ..catalog.approval import approve_played
    project = args.project
    try:
        result = approve_played(project.root, project.catalog, project.language, args.played,
                                dry_run=args.dry_run)
    except (OSError, ValueError) as exc:
        line("error", exc)
        return 1
    line("tag", result.tag)
    line("approved", f"{result.approved} entries")
    line("kept", f"{result.kept} entries")
    line("files", f"{len(result.files)} changed")
    line("status", ("planned" if args.dry_run else "applied") if result.files else "unchanged")
    return 0


def register(sub):
    so = add(sub, "source", source_catalog)
    so.add_argument("--out", help="directory for unverified source catalogs")
    so.add_argument("--editor-out", help="also expose source downloads in this editor data directory")
    add(sub, "prepare", catalog_prepare)
    ex = add(sub, "extract", catalog_extract)
    ex.add_argument("--out", help="the source catalog (default: build/catalog.pot)")
    ko = add(sub, "key-only", catalog_key_only)
    ko.add_argument("--english", default=None, help="the English source catalog (default: build/catalog.pot)")
    write_options(ko, check_help="report what would change")
    rk = add(sub, "rekey", rekey)
    write_options(rk, check_help="report what would change")
    mg = add(sub, "merge", catalog_merge)
    mg.add_argument("catalog", help="the catalog of the current base: a PO directory or file")
    mg.add_argument("source", help="the source catalog of the new base: a PO directory, a PO or POT file")
    mg.add_argument("--out", help="merged catalog directory or file; required when writing")
    mg.add_argument("--threshold", type=float, default=THRESHOLD,
                    help=f"similarity a fuzzy match exceeds, a difflib ratio (default {THRESHOLD})")
    write_options(mg, check_help="report the counts")
    mg.add_argument("--english", default=None,
                    help="the English source catalog of a key-only catalog (default: build/catalog.pot)")
    rp = add(sub, "remap", catalog_remap)
    rp.add_argument("catalog", help="the catalog of the current base: a PO directory or file")
    rp.add_argument("source", help="the source catalog of the new base: a PO directory, a PO or POT file")
    rp.add_argument("--threshold", type=float, default=THRESHOLD,
                    help=f"similarity a fuzzy match exceeds, a difflib ratio (default {THRESHOLD})")
    rp.add_argument("--english", default=None,
                    help="the English source catalog of a key-only catalog (default: build/catalog.pot)")
    write_options(rp, check_help="report every rewrite")
    rp.add_argument("--translation", nargs="*", default=None,
                    help="translation folders whose key-named files follow the merge")
    rp.add_argument("--reference", nargs="*", default=None,
                    help="shared notes or graphics files that name entries")
    rp.add_argument("--profile-file", nargs="*", default=None,
                    help="profile files or folders whose project sections follow the merge")
    for name, fn in (("export", export), ("tm", tm_report), ("progress", progress)):
        sp = add(sub, name, fn)
        sp.add_argument("--po-root", default=None, help="translation PO catalog directory")
        if fn is export:
            sp.add_argument("--check", action="store_true", help="compare the export with the catalog and fail on a difference")
        if fn is progress:
            sp.add_argument("--markdown", action="store_true", help="print a Markdown table")
            sp.add_argument("--full", action="store_true",
                            help="every column: approved, in review, total, words, never shown")
    bpl = add(sub, "batch-plan", batch_plan)
    bpl.add_argument("--po-root", default=None, help="translation PO catalog directory")
    bpl.add_argument("--glossary", default=None, help="term glossary TSV file")
    bpl.add_argument("--size", type=int, default=150, help="largest batch, entries")
    bpl.add_argument("--min-size", type=int, default=60, help="smaller maps are merged up to this size")
    bpl.add_argument("--max-words", type=int, default=2500, help="largest batch, English words")
    bpl.add_argument("--defer", default=None,
                     help="gen3tl discover reach output (default: the project build); its unreachable strings "
                          "are deferred")
    ap = add(sub, "approve", catalog_approve)
    ap.add_argument("--played", required=True, metavar="VERSION",
                    help="version whose release tag <code>-v<version> was playtested")
    write_options(ap, check_help="report the counts")
    for name, fn in (("batch-export", batch_export), ("batch-import", batch_import),
                     ("batch-status", batch_status), ("batch-approve", batch_approve)):
        bp = add(sub, name, fn)
        bp.add_argument("name", help="project-local batch name")
        bp.add_argument("--po-root", default=None, help="translation PO catalog directory")
        if fn is batch_export:
            bp.add_argument("--file", nargs="*", help="PO files to include")
            bp.add_argument("--ids", nargs="*", help="entries to include: keys, inventory ids or offsets")
            bp.add_argument("--plan", nargs="?", const="", default=None,
                            help="take batch ids from a plan; --plan alone selects the project's plan")
            bp.add_argument("--force", action="store_true", help="export even over an approved batch or one with a translation")
            bp.add_argument("--prefill", action="store_true",
                            help="draft translation.json from approved entries with the same English")
