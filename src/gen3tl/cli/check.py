"""QA gate, catalog, commit message and pull request checks, coverage, batch checks and previews."""

from __future__ import annotations

import argparse

from ..core.charmap import Charmap
from ..profile.profile import Profile
from .common import (_context_records, _font_codes, _limits_and_matcher, _shadow_end, _style_shadow_ends,
                     _style_widths, _window_measures, add, command, line)


@command("check po")
def po_check(args):
    """Parse every PO file with the built-in reader and with msgfmt when installed."""
    import shutil
    import subprocess
    from pathlib import Path
    from ..catalog.po import read
    files = sorted(Path(args.po_root).rglob("*.po"))
    ids = {}
    problems = 0
    for f in files:
        _, es = read(f)
        for e in es:
            if e.msgctxt in ids:
                line("error", "duplicate id")
                line("entry", e.msgctxt)
                line("file", f)
                line("file", ids[e.msgctxt])
                problems += 1
            ids[e.msgctxt] = f
    msgfmt = shutil.which("msgfmt")
    if msgfmt:
        for f in files:
            r = subprocess.run([msgfmt, "--check-format", "-o", "/dev/null", str(f)], capture_output=True, text=True)
            if r.returncode:
                line("file", f)
                line("error", r.stderr.strip()[:300])
                problems += 1
    line("files", f"{len(files)} files")
    line("entries", f"{len(ids)} entries")
    line("msgfmt", "used" if msgfmt else "missing")
    line("problems", f"{problems} problems")
    return 1 if problems else 0


@command("check pr")
def pr_check(args):
    """Check changed PO translations against the measurement snapshot and render approximate before/after boxes."""
    from pathlib import Path
    from ..check.prcheck import run
    from ..project import Registry
    return run(args.base, Path(args.out), Registry.load(args.project.registry_root))


@command("check accept-notes")
def accept_notes(args):
    """Accept proposed notes and refresh catalog comments and editor data."""
    import contextlib
    import io
    from ..check.noteaccept import prepare
    from ..check.rules import list_path
    from ..core.terminal import table
    from .web import editor_data

    prof = Profile.by_name(args.profile)
    acceptance = prepare(args.project, prof, args.ids, base=args.base, export=args.input, reviewer=args.reviewer,
                         date=args.date, checked=args.checked_source)
    table(("note", "subject", "before", "after", "reviewer", "date"),
          [(change["id"], change["subject"]["kind"] + "/" + change["subject"]["id"],
            (change["base"] or {}).get("status", "legacy" if change["base"] else "absent"),
            "accepted" if change["proposed"] is not None else "removed",
            args.reviewer, args.date) for change in acceptance.changes])
    legacy = [(change["base"]["id"], "removed", change["id"] if change["proposed"] is not None else "none")
              for change in acceptance.changes if change["base"] is not None and "subject" not in change["base"]]
    if legacy:
        table(("legacy base", "action", "replacement"), legacy)
    sources = [(change["id"], index, source["name"], source["address"],
                old[index - 1]["verification"] if index <= len(old) else "absent", source["verification"])
               for change in acceptance.changes
               for old in [(change["base"] or {}).get("sources", [])]
               for index, source in enumerate((change["proposed"] or {}).get("sources", []), 1)]
    if sources:
        table(("note", "source", "name", "address", "before", "after"), sources)
    table(("file", "action"), [(path, "notes") for path in acceptance.application.files]
          + [(path, "comments") for path in acceptance.catalogs]
          + [(peer.editor, "editor data") for peer in acceptance.projects]
          + [(peer.build / "editor" / f"{peer.language}.json", "editor data") for peer in acceptance.projects
             if (peer.build / "editor" / f"{peer.language}.json").is_file()])

    def refresh(peer, stage):
        effective = peer.effective(Profile.load(prof.path))
        options = argparse.Namespace(project=peer, profile=str(prof.path), lang=peer.language,
            po_root=str(peer.catalog), glossary=str(peer.glossary), rules=str(list_path(peer.translation)),
            snapshot=str(peer.snapshot), no_rom=peer.snapshot.is_file(), rom=args.rom,
            split=True, language_only=True, out=str(stage / "split"))
        outputs = [(stage / "split", peer.editor)]
        with peer.activate(effective), contextlib.redirect_stdout(io.StringIO()):
            if editor_data(options):
                raise ValueError(f"{peer.key}: editor refresh failed")
            single = peer.build / "editor" / f"{peer.language}.json"
            if single.is_file():
                options.split, options.out = False, str(stage / "single.json")
                if editor_data(options):
                    raise ValueError(f"{peer.key}: editor refresh failed")
                outputs.append((stage / "single.json", single))
        return outputs

    if not args.dry_run and acceptance.changes:
        acceptance.write(args.project, prof, refresh)
    line("notes", len(acceptance.changes))
    line("result", "dry-run" if args.dry_run else "accepted" if acceptance.changes else "unchanged")
    return 0


@command("check commit-message")
def commit_message(args):
    """Check one commit message file or every non-merge commit of a revision range."""
    from ..check.commitmsg import check_file, check_range
    if getattr(args, "range", None) is not None:
        return check_range(args.range)
    return check_file(args.message)


@command("check qa")
def qa(args):
    """Run the QA gate over the catalog; writes build/qa-report.md, exits non-zero on errors."""
    from pathlib import Path
    from ..discover import inventory as inv_mod
    from ..check import qa as qa_mod
    from ..build.font import FontTable
    from ..lang.langpack import LangPack
    from ..check.layout import Measure
    prof = Profile.by_name(args.profile)
    if args.source_as_translation:
        from ..build.textprefix import original_limits
        prof = original_limits(prof)
    rom = prof.load_rom(args.rom)
    font = FontTable.from_profile(prof)[0]
    pack = LangPack.load(args.lang, prof["font_style"]["style"])
    slots = pack.glyph_slots(rom, font)
    width = lambda b: slots[b].width if b in slots else font.width(rom, b)
    measure = Measure(width, prof.get("layout", {}).get("placeholders", {}),
                      styles=_style_widths(prof, rom, args.lang), shadow_end=_shadow_end(slots, font, rom),
                      shadow_styles=_style_shadow_ends(prof, rom, args.lang), fonts=_font_codes(prof))
    inv = inv_mod.load(args.project.build / "inventory.json")
    from ..build import release
    ctx = _context_records()
    from ..check import namepairs
    from ..catalog.catalog import load_catalog
    catalog = load_catalog(Path(args.po_root), prof)
    limits = qa_mod.compute_limits(prof, inv, catalog, measure, pack.charmap(), ctx,
                                   english_slots=args.source_as_translation, rom=rom)
    gate = qa_mod.run(prof, inv, Path(args.po_root), pack.charmap(), measure, pack.allocated_slots(),
                      source_as_translation=args.source_as_translation,
                      glossary=args.project.glossary,
                      windows=qa_mod.Windows(prof, inv, _window_measures(prof, rom, args.lang)),
                      version=release.load(), ctx=ctx, rom=rom, pokedex=namepairs.dataset(prof),
                      catalog=catalog, limits=limits)
    rep = qa_mod.report(gate)
    out = args.project.build / ("qa-report-source.md" if args.source_as_translation else "qa-report.md")
    out.write_text(rep, encoding="utf-8")
    line("checked", f"{gate.checked} entries")
    line("errors", f"{gate.errors} errors")
    line("warnings", f"{sum(f.severity == 'warning' for f in gate.findings)} warnings")
    line("report", out)
    if ctx is not None and not args.source_as_translation:
        # the approved catalog re-checked with the widths of the insertions
        text, n = qa_mod.inserted_width_report(prof, inv, catalog, ctx, measure,
                                               pack.charmap(), release.load(), rom, limits)
        wpath = args.project.build / "inserted-width-report.md"
        wpath.write_text(text, encoding="utf-8")
        line("report", wpath)
        line("overflow", f"{n} entries")
    return 1 if gate.errors else 0


@command("check coverage")
def coverage(args):
    """Reconcile the source, the catalog and a built ROM: build/coverage-report.md."""
    import json
    from pathlib import Path
    from ..check import coverage as cov
    from ..catalog.catalog import load_catalog
    from ..lang.langpack import LangPack
    from ..build.release import expand, load as load_version
    from ..core.rom import Rom
    prof = Profile.by_name(args.profile)
    inv = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
    ctx = json.loads(Path(args.context).read_text(encoding="utf-8"))
    version = load_version()
    from ..discover.maps import Walker
    base = prof.load_rom(args.rom)
    walker = Walker(base, prof)
    walker.walk_all()
    walker.walk_structural()
    walker.walk_buffer_starts([r["at"] for e in inv["entries"] for r in e["refs"] if r["kind"] == "script:bufferstring"])
    rep = cov.build(inv, load_catalog(Path(args.po_root)), Rom.load(args.built), LangPack.load(args.lang).charmap(),
                    prof, ctx, lambda t: expand(t, version), base, walker.command_spans())
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(cov.markdown(rep, args.built), encoding="utf-8")
    line("report", out)
    line("open", f"{rep.open} entries")
    if rep.open:
        line("error", f"unresolved coverage: {rep.open} entries")
    return 1 if rep.open else 0


@command("check unmeasured")
def unmeasured(args):
    """List the entries whose width limit is the English width, by catalog file: build/unmeasured-report.md."""
    from pathlib import Path
    from ..check import qa as qa_mod
    from ..catalog.catalog import load_catalog
    limits, _ = _limits_and_matcher(args)
    text, n = qa_mod.unmeasured_report(load_catalog(Path(args.po_root)), limits)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    line("report", out)
    line("measured", f"{n} entries")


@command("check batch")
def batch_check(args):
    """Check <batches>/<name>/translation.json with the QA rules."""
    import json
    from collections import Counter
    from pathlib import Path
    from ..check.entrycheck import entry_problems
    from ..check.layout import Measure
    from ..check.provenance import formatted
    from ..build.font import FontTable
    from ..lang.langpack import LangPack
    prof = Profile.by_name(args.profile)
    rom = prof.load_rom(args.rom)
    font = FontTable.from_profile(prof)[0]
    pack = LangPack.load(args.lang, prof["font_style"]["style"])
    slots = pack.glyph_slots(rom, font)
    cm = pack.charmap()
    measure = Measure(lambda b: slots[b].width if b in slots else font.width(rom, b),
                      prof.get("layout", {}).get("placeholders", {}), styles=_style_widths(prof, rom, args.lang),
                      shadow_end=_shadow_end(slots, font, rom), shadow_styles=_style_shadow_ends(prof, rom, args.lang),
                      fonts=_font_codes(prof))
    d = args.project.batches / args.name
    from ..catalog import keys
    aliases = keys.for_profile(prof)   # a batch names entries by key or inventory id
    source = json.loads((d / "source.json").read_text(encoding="utf-8"))
    given = json.loads((d / args.file).read_text(encoding="utf-8"))
    names = {aliases.entry_id(name): name for name in [*given, *(x["id"] for x in source)]}   # as the batch names them
    src = {aliases.entry_id(x["id"]): x for x in source}
    from ..catalog.batch import translated_text
    tr = {aliases.entry_id(k): translated_text(v, src.get(aliases.entry_id(k), {}).get("source_text", ""))
          for k, v in given.items()}
    # Sentence measurement needs translated words, excluding untranslated entries.
    limits, matcher = _limits_and_matcher(args, {k: v for k, v in tr.items() if v})
    from ..discover import inventory as inv_mod
    from ..check import qa as qa_mod
    windows = qa_mod.Windows(prof, inv_mod.load(args.project.build / "inventory.json"),
                             _window_measures(prof, rom, args.lang))
    from ..build.release import expand, load as load_version
    version = load_version()   # checks see the version the game shows
    droppable = qa_mod.droppable_tokens(prof.get("qa", {}))   # per entry
    addable = qa_mod.addable_tokens(prof.get("qa", {}))   # per entry
    # the catalog's never-shown tags
    from ..catalog.catalog import NEVER_SHOWN, load_catalog, never_shown_tag
    catalog = load_catalog(Path(args.po_root))
    never = {sid: never_shown_tag(catalog[sid][1].extracted) for sid in src if sid in catalog}
    from ..check import namepairs
    pairs = namepairs.results(prof, {sid: e.msgstr or e.msgid for sid, (p, e) in catalog.items()},
                              windows.measures, namepairs.dataset(prof))
    out, kinds = [], Counter()
    for sid, x in src.items():
        if never.get(sid):   # a marker, not a problem
            out.append((sid, f"{NEVER_SHOWN} ({never[sid]})"))
        if sid not in tr or tr[sid] is None:   # null: a pre-filled entry awaiting translation
            out.append((sid, "missing translation")); continue
        t = expand(tr[sid], version)
        optional = set(prof.get("qa", {}).get("optional_tokens", [])) | droppable.get(sid, set())
        from ..lang.fill import entry_matcher
        scoped_matcher = entry_matcher(catalog[sid][1], matcher) if sid in catalog else matcher
        pair = next((y for y in (namepairs.partner(r, sid, aliases.key) for r in pairs) if y), None)
        _, probs = entry_problems(x["source_text"], t, limits[sid], measure, cm, optional,
                                  prof.get("qa", {}).get("forbidden", {}), windows.other(sid), windows.measures,
                                  scoped_matcher, addable.get(sid), pair)
        out += [(sid, formatted(prob)) for prob in probs]
    for sid in tr:
        if sid not in src:
            out.append((sid, "id not in the batch"))
    for sid, prob in out:
        line("entry", names.get(sid, sid))
        line("warning" if "(warning)" in prob else "never" if prob.startswith(NEVER_SHOWN) else "error", prob)
        kinds["never" if prob.startswith(NEVER_SHOWN) else "warning" if "(warning)" in prob else "error"] += 1
    line("checked", f"{len(tr)} entries")
    line("errors", f"{kinds['error']} errors")
    line("warnings", f"{kinds['warning']} warnings")
    line("never", f"{kinds['never']} entries")
    return 1 if kinds["error"] else 0


def _caption(text: str, cm) -> bytes:
    """An entry id as the font draws it: a character the charmap lacks becomes '-'."""
    return cm.encode("".join(c if cm.can_encode(c) else "-" for c in text))


@command("check render")
def batch_render(args):
    """Render a batch as English/translated message boxes in the game font (build/screens/<batch>-boxes.png)."""
    import json
    from ..build.font import FontTable
    from ..lang.langpack import LangPack
    from ..check.layout import Measure, entry_measure, layout, paragraphs, lines_of
    from ..check.render import Canvas, GlyphSource, draw_text, line_width, placeholder_px, write_png, PALETTE
    prof = Profile.by_name(args.profile)
    rom = prof.load_rom(args.rom)
    font = FontTable.from_profile(prof)[0]
    pack = LangPack.load(args.lang, prof["font_style"]["style"])
    slots = pack.glyph_slots(rom, font)
    cm, base_cm = pack.charmap(), Charmap()
    src = GlyphSource(rom, font, slots)
    measure = Measure(lambda b: slots[b].width if b in slots else font.width(rom, b),
                      prof.get("layout", {}).get("placeholders", {}), shadow_end=_shadow_end(slots, font, rom))
    d = args.project.batches / args.name
    from ..catalog import keys
    aliases = keys.for_profile(prof)   # a batch names entries by key or inventory id
    source = json.loads((d / "source.json").read_text(encoding="utf-8"))
    names = {aliases.entry_id(x["id"]): x["id"] for x in source}   # as the batch names them
    entries = [{**x, "id": aliases.entry_id(x["id"])} for x in source]
    from ..catalog.batch import translated_text
    english = {x["id"]: x["source_text"] for x in entries}
    tr = {aliases.entry_id(k): translated_text(v, english.get(aliases.entry_id(k), ""))
          for k, v in json.loads((d / "translation.json").read_text(encoding="utf-8")).items()}
    limits, _ = _limits_and_matcher(args, {k: v for k, v in tr.items() if v})
    english_limits, _ = _limits_and_matcher(args, english_slots=True)
    from ..build.release import expand, load as load_version
    version = load_version()   # render the version the game shows
    if args.ids:
        entries = [x for x in entries if x["id"] in {aliases.entry_id(i) for i in args.ids}]
    entries = entries[:args.limit]
    box_w = 224
    blocks = []
    for x in entries:
        en = x["source_text"]
        translated = layout(expand(tr.get(x["id"], ""), version), limits[x["id"]], measure, cm)
        cols = []
        # placeholders at the widths the layout measured
        translated_slots = placeholder_px(translated, entry_measure(measure, limits[x["id"]]), cm)
        en_slots = placeholder_px(en, entry_measure(measure, english_limits[x["id"]]), base_cm)
        for text, codec, slots_px in ((en, base_cm, en_slots), (translated, cm, translated_slots)):
            pars = paragraphs(text)
            h = sum(16 * len(lines_of(p_)) + 8 for p_ in pars) + 4
            cols.append((pars, codec, h, slots_px))
        blocks.append((names[x["id"]], cols, max(c[2] for c in cols)))
    total_h = sum(b[2] + 14 for b in blocks) + 4
    cv = Canvas(2 * box_w + 24, total_h)
    y = 4
    for sid, cols, h in blocks:
        draw_text(cv, src, 4, y, _caption(sid, base_cm))
        y += 14
        for ci, (pars, codec, _, slots_px) in enumerate(cols):
            x0 = 4 + ci * (box_w + 12)
            yy = y
            for p_ in pars:
                n = len(lines_of(p_))
                cv.rect(x0, yy, x0 + box_w, yy + 16 * n + 5, PALETTE["frame"])
                try:
                    interior = Canvas(box_w - 1, 16 * n + 4)
                    draw_text(interior, src, 7, 1, codec.encode(p_.replace("\\l", "\\n")), slots=slots_px)
                    for dy, row in enumerate(interior.px):
                        cv.px[yy + 1 + dy][x0 + 1:x0 + box_w] = row
                    for li, text_line in enumerate(lines_of(p_)):
                        width = line_width(src, codec.encode(text_line), slots=slots_px)
                        if width > box_w - 8:
                            cv.rect(x0 + box_w, yy + 2 + 16 * li,
                                    x0 + box_w, yy + 15 + 16 * li, PALETTE["overflow"])
                            column = "English" if ci == 0 else args.lang
                            line("entry", sid)
                            line("language", column)
                            line("overflow", f"{width - (box_w - 8)} px")
                            line("width", f"{width} px")
                            line("limit", f"{box_w - 8} px")
                except Exception:
                    pass
                yy += 16 * n + 8
        y += h
    out = args.project.build / "screens" / f"{args.name}-boxes.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    write_png(out, cv.scaled(args.scale))
    line("wrote", out)
    line("entries", f"{len(blocks)} entries")


def register(sub):
    q = add(sub, "qa", qa)
    q.add_argument("--po-root", default=None, help="translation PO catalog directory")
    q.add_argument("--source-as-translation", action="store_true", help="check the English as translation")
    po = add(sub, "po", po_check)
    po.add_argument("--po-root", default=None, help="translation PO catalog directory")
    pc = add(sub, "pr", pr_check)
    pc.add_argument("--base", required=True, help="Git ref to compare with the working tree")
    pc.add_argument("--out", required=True, help="directory for report.md and preview PNGs")
    na = add(sub, "accept-notes", accept_notes)
    na.add_argument("ids", nargs="*", metavar="NOTE", help="proposed note IDs; exclusive with --base and --input")
    na.add_argument("--base", help="accept all proposed notes differing from this Git revision")
    na.add_argument("--input", metavar="JSON", help="review and apply a note-only editor export, including legacy bases")
    na.add_argument("--reviewer", required=True, help="reviewer identity recorded on every selected note")
    na.add_argument("--date", required=True, help="review date: YYYY-MM-DD or ISO timestamp with timezone")
    na.add_argument("--checked-source", nargs=2, action="append", default=[], metavar=("NOTE", "INDEX"),
                    help="mark this note's 1-based source index checked; repeat for each verified citation")
    na.add_argument("--dry-run", action="store_true", help="list the note, citation and output changes")
    cm = add(sub, "commit-message", commit_message)
    what = cm.add_mutually_exclusive_group(required=True)
    what.add_argument("message", nargs="?", help="message file git passes to the hook")
    what.add_argument("--range", default=argparse.SUPPRESS, help="revision range to check")
    um = add(sub, "unmeasured", unmeasured)
    um.add_argument("--po-root", default=None, help="translation PO catalog directory")
    um.add_argument("--out", default=None, help="output Markdown report of unmeasured entries")
    cv = add(sub, "coverage", coverage)
    cv.add_argument("--built", default=None, help="built ROM (default: the project build)")
    cv.add_argument("--inventory", default=None, help="string inventory (default: the project build)")
    cv.add_argument("--context", default=None, help="script context (default: the project build)")
    cv.add_argument("--po-root", default=None, help="translation PO catalog directory")
    cv.add_argument("--out", default=None, help="report (default: the project build)")
    for name, fn in (("batch", batch_check), ("render", batch_render)):
        bp = add(sub, name, fn)
        bp.add_argument("name", help="project-local batch name")
        bp.add_argument("--po-root", default=None, help="translation PO catalog directory")
        if fn is batch_render:
            bp.add_argument("--ids", nargs="*", help="entry identifiers to render; omitted: all batch entries")
            bp.add_argument("--limit", type=int, default=40, help="maximum number of rendered entries")
            bp.add_argument("--scale", type=int, default=2, help="pixels per rendered pixel")
        if fn is batch_check:
            bp.add_argument("--file", default="translation.json", help="file in the batch directory to check")
