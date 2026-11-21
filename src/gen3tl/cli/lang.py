"""Language projects, glossary forms, the term index, renames, name tables and sprite font sheets."""

from __future__ import annotations

from ..profile.profile import Profile
from .common import (_context_records, _font_codes, _shadow_end, _style_measures, _style_shadow_ends,
                     _style_widths, add, command, line, write_options)


@command("lang init")
def lang_init(args):
    """Create a language pack, translation workspace, policy and registration."""
    import unicodedata
    from pathlib import Path
    from .. import INSTALLED
    from ..lang.langinit import TOOLKIT_ROOT, create
    packs = Path(args.packs) if args.packs else None
    if INSTALLED and packs is None:
        line("error", f"{TOOLKIT_ROOT / 'assets/lang'}: the toolkit runs installed; "
                      "run lang init from a toolkit checkout or pass --packs <dir>")
        return 1
    try:
        result = create(args.code, Profile.by_name(args.profile), args.registry, pack_root=packs)
    except (OSError, ValueError) as exc:
        line("error", exc)
        return 1
    line("pack", result.pack)
    line("project", result.workspace)
    line("empty", f"{result.entries} entries")
    line("site", result.site or "unchanged")
    for char, byte in result.missing.items():
        line("missing", char)
        line("unicode", unicodedata.name(char, "UNKNOWN"))
        line("slot", f"{byte:02X}")
    return 0


@command("lang fill-tables")
def fill_tables(args):
    """Fill name-table catalog entries from approved glossary terms; list the names that remain."""
    from pathlib import Path
    from ..lang import termbase as tb
    from ..lang.fill import fill_tables as fill
    prof = Profile.by_name(args.profile)
    reports = fill(prof.get("table", []), Path(args.po_root), tb.load(Path(args.glossary)), overwrite=args.overwrite)
    out = Path(args.missing)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = ["table\tid\tenglish"]
    for r in reports:
        line("table", r.table)
        line("filled", f"{r.filled} entries")
        line("blank", f"{r.blank} entries")
        line("kept", f"{r.kept} entries")
        line("missing", f"{len(r.missing)} names")
        lines += [f"{r.table}\t{m}" for m in r.missing]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    line("wrote", out)
    return 0


@command("lang cite")
def glossary_cite(args):
    """Merge a researched source table (names, gender evidence) into the glossary rows of a category."""
    from pathlib import Path
    from ..lang import termbase as tb
    from ..lang.cite import load, merge
    cols = dict(c.split("=", 1) for c in args.columns or [])
    rows = load(Path(args.table), cols)
    path = Path(args.glossary)
    terms = tb.load(path)
    s = merge(terms, rows, args.category, args.label)
    tb.save(path, terms, write_notes=True)
    line("cited", f"{s.terms} terms")
    line("source", f"{len(rows)} rows")
    line("matched", f"{s.same_name} names")
    line("variants", f"{s.other_names} names")
    line("gendered", f"{s.gendered} terms")
    line("wrote", path)
    for en in s.unknown:
        line("unknown", en)
    return 1 if s.unknown else 0


@command("lang labels")
def glossary_labels(args):
    """Copy the reviewed table entries of a batch into the glossary labels (field forms) of their terms."""
    import json
    from pathlib import Path
    from ..lang import termbase as tb
    from ..lang.fill import labels_from_catalog
    d = args.project.batches / args.batch
    if not json.loads((d / "status.json").read_text(encoding="utf-8")).get("approved"):
        line("batch", args.batch)
        line("status", "unapproved")
        return 1
    ids = [x["id"] for x in json.loads((d / "source.json").read_text(encoding="utf-8"))]
    path = Path(args.glossary)
    terms = tb.load(path)
    changes = labels_from_catalog(Path(args.po_root), terms, ids)
    tb.save(path, terms)
    for tid, old, new in changes:
        line("term", tid)
        line("before", old or "none")
        line("after", new or "full")
    line("changed", f"{len(changes)} labels")
    line("wrote", path)
    return 0


@command("lang forms")
def term_forms(args):
    """Generate glossary forms using the selected language pack."""
    from pathlib import Path
    from ..lang import termbase as tb
    path = Path(args.glossary)
    terms = tb.load(path)
    n = 0
    for t in terms:
        if args.all or not t.forms:
            t.forms = tb.generate_forms(t)
            n += 1
    tb.save(path, terms)
    line("generated", f"{n} terms")
    line("total", f"{len(terms)} terms")
    line("wrote", path)


@command("lang index")
def term_index(args):
    """Write the term index: every glossary term occurrence in the translations."""
    from collections import Counter
    from pathlib import Path
    from ..lang import termbase as tb
    from ..catalog.catalog import load_catalog
    from ..catalog import keys
    terms = tb.load(Path(args.glossary))
    catalog = load_catalog(Path(args.po_root), english=False)   # the English where build/catalog.pot holds it
    keyed = any(e.keyed for _, e in catalog.values())
    # without the English, the facts of the index it replaces
    told = tb.told_by(Path(args.index), keys.for_profile(None).key) if keyed else None
    rows = tb.build_index(terms, catalog, told)
    tb.save_index(Path(args.index), rows)
    per = Counter(r["term"] for r in rows)
    line("occurrences", f"{len(rows)} occurrences")
    line("terms", f"{len(per)} terms")
    line("strings", f"{len({r['string'] for r in rows})} strings")
    line("wrote", args.index)
    # the form-only occurrences
    line("uses", f"{sum(1 for r in rows if r['use'] == 'yes')} occurrences")
    line("form", f"{sum(1 for r in rows if r['use'] not in ('yes', tb.UNKNOWN))} occurrences")
    if keyed:
        line("unknown", f"{sum(1 for r in rows if r['source_names_term'] == tb.UNKNOWN)} occurrences")




def _rename_checks(args, catalog, terms, renames, changes):
    """The check batch checks on every line a rename changes, before and after."""
    import dataclasses
    from ..discover import inventory as inv_mod
    from ..check import qa as qa_mod
    from ..lang import termbase as tb
    from ..core.charmap import CharmapError
    from ..check.entrycheck import entry_problems
    from ..build.font import FontTable
    from ..lang.langpack import LangPack
    from ..check.layout import Measure
    from ..build.release import expand, load as load_version
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
    inv = inv_mod.load(args.project.build / "inventory.json")
    ctx = _context_records()
    renamed = {sid: c["after"] for sid, c in changes.items()}
    lim0 = qa_mod.compute_limits(prof, inv, catalog, measure, cm, ctx, rom=rom)
    lim1 = qa_mod.compute_limits(prof, inv, catalog, measure, cm, ctx, renamed, rom=rom)
    windows = qa_mod.Windows(prof, inv, _style_measures(prof, rom, args.lang))
    rules = prof.get("qa", {})
    optional, forbidden = set(rules.get("optional_tokens", [])), rules.get("forbidden", {})
    droppable = qa_mod.droppable_tokens(rules)
    addable = qa_mod.addable_tokens(rules)
    rows = {r.old.id: dataclasses.replace(r.old, translation=r.translation, forms=r.forms) for r in renames}
    m0, m1 = tb.TermMatcher(terms), tb.TermMatcher([rows.get(t.id, t) for t in terms])
    version = load_version()

    def problems(sid, text, lim, matcher):
        if lim is None:
            return ["no inventory entry for this id"]
        try:
            return entry_problems(catalog[sid][1].msgid, expand(text, version), lim, measure, cm,
                                  optional | droppable.get(sid, set()), forbidden, windows.other(sid),
                                  windows.measures, matcher, addable.get(sid))[1]
        except CharmapError as e:   # check batch stops here
            return [f"not encodable: {e}"]
    checked = {sid: e.msgstr for sid, (p, e) in catalog.items()
               if e.msgstr and sid not in changes and lim0.get(sid) != lim1.get(sid)}
    before, after = {}, {}
    for sid in list(changes) + list(checked):
        text = catalog[sid][1].msgstr
        before[sid] = problems(sid, text, lim0.get(sid), m0)
        after[sid] = problems(sid, renamed.get(sid, text), lim1.get(sid), m1)
    return before, after, checked


@command("lang rename")
def term_rename(args):
    """Rename glossary terms everywhere, keeping each occurrence's grammatical form."""
    import json
    from pathlib import Path
    from ..lang import renamecheck as rc
    from ..lang import termbase as tb
    from ..catalog.catalog import load_catalog
    from ..catalog.po import read, write
    if len(args.pairs) % 2:
        line("error", "unpaired replacement")
        return 2
    gpath = Path(args.glossary)
    terms = tb.load(gpath)
    by_id = {t.id: t for t in terms}
    if not args.replacement_glossary:
        line("error", "missing --replacement-glossary")
        return 2
    replacement_rows = tb.load(Path(args.replacement_glossary))
    replacements = {term.id: term for term in replacement_rows}
    if len(replacements) != len(replacement_rows):
        raise ValueError("replacement glossary has duplicate term IDs")
    renames = []
    for tid, translated in zip(args.pairs[::2], args.pairs[1::2]):
        old = by_id.get(tid)
        if old is None:
            line("term", tid)
            line("error", "unknown term")
            return 1
        new = replacements.get(tid)
        if new is None or new.translation != translated or new.source_text != old.source_text or new.category != old.category:
            raise ValueError(f"replacement glossary has no matching row for {tid}")
        forms = new.all_forms()
        if not translated.strip() or forms.get("nomn.sing") != translated:
            raise ValueError(f"replacement glossary has inconsistent nominative for {tid}")
        if any(not isinstance(value, str) or not value.strip() for value in forms.values()):
            raise ValueError(f"replacement glossary has empty forms for {tid}")
        if new.label and forms.get("label") != new.label:
            raise ValueError(f"replacement glossary has inconsistent label for {tid}")
        missing = set(old.all_forms()) - set(forms)
        if missing:
            raise ValueError(f"replacement glossary lacks {tid} forms: {', '.join(sorted(missing))}")
        from ..catalog import keys
        include = frozenset(keys.for_profile(None).entry_id(x) for x in args.include)   # a key or an id
        renames.append(rc.Rename(old, translated, forms, new.gender, include=include))
    catalog = load_catalog(Path(args.po_root))
    texts = {sid: e.msgstr for sid, (p, e) in catalog.items() if e.msgstr}
    english = {sid: e.msgid for sid, (p, e) in catalog.items()}
    senses = tb.Senses(terms)
    changes = rc.apply_renames(texts, renames, english, senses)
    from ..build.keyboard import reject_owned
    reject_owned(changes)
    before, after, checked = _rename_checks(args, catalog, terms, renames, changes)
    report = rc.build_report(renames, changes, english, {sid: str(p) for sid, (p, e) in catalog.items()}, before,
                             after, checked, None, senses)
    left = rc.left_out(texts, renames, english, senses)
    for sid, hits in left.items():
        line("entry", sid)
        line("status", "excluded")
        for hit in hits:
            line("match", hit["match"])
        line("text", texts[sid][:60])
    for e in report["entries"]:
        line("entry", e["id"])
        line("before", e["before"][:60])
        line("after", e["after"][:60])
        for problem in e["added"]:
            line(problem["severity"], problem["message"])
        for agreement in e["agreement"]:
            line("agreement", "required")
            line("term", agreement["term"])
            line("form", agreement["form"])
            line("match", agreement["match"])
            for word in agreement["words"]:
                line("word", word)
        for ambiguity in e["ambiguous"]:
            line("warning", ambiguity)
    s = report["summary"]
    for r in report["renames"]:
        line("term", r["term"])
        line("before", r["old"])
        line("after", r["new"])
        line("changed", f"{r['lines']} lines")
        line("grammar", "changed" if r["grammar"]["changes"] else "unchanged")
        for change in r["grammar"]["changes"]:
            line("property", change["property"])
            line("before", change["old"])
            line("after", change["new"])
    line("changed", f"{s['changed']} lines")
    line("checked", f"{s['limit_checked']} lines")
    line("errors", f"{s['errors']} errors")
    line("affected", f"{s['error_lines']} lines")
    line("warnings", f"{s['warnings']} warnings")
    line("affected", f"{s['warning_lines']} lines")
    line("agreement", f"{s['agreement_lines']} lines")
    line("ambiguous", f"{s['ambiguous_lines']} lines")
    if left:
        line("excluded", f"{len(left)} lines")
    if args.report:
        stem = Path(args.report)
        stem = stem.with_suffix("") if stem.suffix in (".json", ".md") else stem
        stem.parent.mkdir(parents=True, exist_ok=True)
        stem.with_name(stem.name + ".json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                                                       encoding="utf-8")
        stem.with_name(stem.name + ".md").write_text(rc.markdown(report), encoding="utf-8")
        line("wrote", f"{stem}.json")
        line("report", f"{stem}.md")
    if not args.apply:
        line("mode", "dry-run")
        line("status", "blocked" if s["blocked"] else "ready")
        return 1 if s["blocked"] else 0
    if s["blocked"] and not args.allow_errors:
        line("status", "blocked")
        line("errors", f"{s['errors']} errors")
        line("affected", f"{s['error_lines']} lines")
        return 1
    entries = {e["id"]: e for e in report["entries"] if e["changed"]}
    by_file = {}
    for sid in changes:
        by_file.setdefault(catalog[sid][0], []).append(sid)
    from ..catalog import keys
    entry_id = keys.for_profile(None).entry_id
    for p, ids in by_file.items():
        hdr, es = read(p)
        for e in es:
            if entry_id(e.msgctxt) in ids:
                x = entries[entry_id(e.msgctxt)]
                e.msgstr = x["after"]   # its state stays
                note = []
                if x["ambiguous"]:
                    note.append("check case: " + "; ".join(x["ambiguous"]))
                if x["agreement"]:
                    note.append("check agreement: " + ", ".join(w for a in x["agreement"] for w in a["words"]))
                if x["added"]:
                    note.append("adds: " + "; ".join(q["message"] for q in x["added"]))
                if note:
                    e.translator.append("; ".join(note))
        write(p, es, hdr)
    from ..lang.termedit import append_variant
    for r in renames:
        append_variant(r.old, r.old.translation)
        r.old.translation = r.translation
        r.old.forms = r.forms
        replacement = replacements[r.old.id]
        r.old.label, r.old.gender = replacement.label, replacement.gender
        r.old.declension = replacement.declension
    tb.save(gpath, terms)
    rows = tb.build_index(terms, load_catalog(Path(args.po_root)))
    tb.save_index(Path(args.index), rows)
    line("status", "applied")
    line("review", "required")
    line("index", args.index)
    line("occurrences", f"{len(rows)} occurrences")
    return 0


@command("lang assemble")
def glossary_assemble(args):
    """Merge the research tables into the glossary (keeps approved rows)."""
    from pathlib import Path
    from ..lang import termbase as tb
    from ..lang.glossary_assemble import assemble
    out = Path(args.glossary)
    terms, problems = assemble(Path(args.research), args.only)
    if out.exists():
        old = {t.id: t for t in tb.load(out)}
        for i, t in enumerate(terms):
            o = old.get(t.id)
            if o is not None and o.status in ("reviewed", "approved"):
                terms[i] = o  # reviewed rows are not overwritten by research
            elif o is not None and (o.note_ids or t.note_ids):
                records, related, findings = t.note_records, t.related_note_records, t.research_findings
                t.notes, t.note_ids = o.notes, o.note_ids
                t.notes_cell = o.notes_cell
                t.note_records, t.related_note_records = o.note_records, o.related_note_records
                t.research_findings = dict(o.research_findings)
                tb.attach_notes(t, list(records), related, findings)
        if args.only:  # merge one research file; other rows stay as they are
            new = {t.id: t for t in terms}
            terms = [new.pop(t.id, t) for t in old.values()] + list(new.values())
    tb.save(out, terms, write_notes=True)
    from collections import Counter
    line("terms", f"{len(terms)} terms")
    line("wrote", out)
    for category, count in Counter(t.category for t in terms).items():
        line("category", category)
        line("count", f"{count} terms")
    for p in problems[:20]:
        line("error", p)


@command("lang sprite-sheet")
def sprite_sheet(args):
    """Render each sprite font sheet with the language pack's tiles framed: build/preview/sprite-<lang>-<font>.png."""
    from pathlib import Path
    from ..build import spritefont
    from ..check.render import sprite_sheet as draw, write_png
    from ..lang.langpack import LangPack
    prof = Profile.by_name(args.profile)
    fonts = [f for f in spritefont.from_profile(prof) if not args.font or f.name in args.font]
    missing = sorted(set(args.font or []) - {f.name for f in fonts})
    for name in missing:
        line("error", f"{name}: no sprite font of the profile")
    if missing or not fonts:
        line("fonts", f"{len(fonts)} fonts")
        return 1
    rom = prof.load_rom(args.rom)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for font in fonts:
        pack = LangPack.load(args.lang, font.style, rows=font.cell_rows, top=0)
        plan = spritefont.plan(font, rom, pack)
        path = out / f"sprite-{args.lang}-{font.name}.png"
        write_png(path, draw(plan.sheet, font.tiles, font.ink, font.shadow, font.background).scaled(args.scale))
        line("font", font.name)
        line("style", font.style)
        line("tiles", f"{len(plan.sheet) // spritefont.TILE} tiles")
        line("appended", f"{len(plan.added)} tiles")
        line("copies", f"{len(plan.copied)} copies")
        line("wrote", path)
    return 0


def register(sub):
    li = add(sub, "init", lang_init)
    li.add_argument("code", help="language code for the new project and pack")
    li.add_argument("--packs", metavar="DIR", help="write the language pack under DIR")
    for name, fn in (("forms", term_forms), ("index", term_index), ("rename", term_rename)):
        tp = add(sub, name, fn)
        tp.add_argument("--glossary", default=None, help="term glossary TSV file")
        tp.add_argument("--po-root", default=None, help="translation PO catalog directory")
        tp.add_argument("--index", default=None, help="term occurrence index TSV file")
        if fn is term_forms:
            tp.add_argument("--all", action="store_true", help="regenerate forms of every term")
        if fn is term_rename:
            tp.add_argument("pairs", nargs="+", metavar="TERM TRANSLATION",
                            help="a term id and its replacement translation; repeat the pair to rename several terms "
                                 "together, applied in order (a term that contains another first)")
            tp.add_argument("--replacement-glossary", help="replacement rows with prepared forms")
            write_options(tp, dest="apply", write_value=True, default_write=False, write_aliases=("--apply",),
                          check_help="report the renamed terms and translations (default)",
                          write_help="write the renamed terms and translations")
            tp.add_argument("--allow-errors", action="store_true",
                            help="apply even when the rename adds errors")
            tp.add_argument("--report", default=None, metavar="PATH",
                            help="write the check report to PATH.json and PATH.md")
            tp.add_argument("--include", action="append", default=[], metavar="ID",
                            help="also rename the form-only occurrences of this entry; repeatable")
    ft = add(sub, "fill-tables", fill_tables)
    ft.add_argument("--glossary", default=None, help="term glossary TSV file")
    ft.add_argument("--po-root", default=None, help="translation PO catalog directory")
    ft.add_argument("--missing", default=None, help="report of names missing from the glossary")
    ft.add_argument("--overwrite", action="store_true", help="replace existing translations too")
    gc = add(sub, "cite", glossary_cite)
    gc.add_argument("table", help="source TSV table of wording evidence")
    gc.add_argument("--category", required=True, help="glossary term category to cite")
    gc.add_argument("--label", required=True, help="note prefix, e.g. TV")
    gc.add_argument("--columns", nargs="*", help="key=column for source_text, name, dub, gender, evidence, source")
    gc.add_argument("--glossary", default=None, help="term glossary TSV file")
    gl = add(sub, "labels", glossary_labels)
    gl.add_argument("batch", help="approved project-local batch of table entries")
    gl.add_argument("--glossary", default=None, help="term glossary TSV file")
    gl.add_argument("--po-root", default=None, help="translation PO catalog directory")
    ss = add(sub, "sprite-sheet", sprite_sheet)
    ss.add_argument("--font", action="append", default=[], metavar="NAME",
                    help="render this sprite font only (repeatable)")
    ss.add_argument("--out", default=None, help="directory for the PNG files")
    ss.add_argument("--scale", type=int, default=3, help="pixels per sheet pixel")
    ga = add(sub, "assemble", glossary_assemble)
    ga.add_argument("--research", default=None, help="directory of reviewed glossary research files")
    ga.add_argument("--glossary", default=None, help="term glossary TSV file")
    ga.add_argument("--only", default=None, help="merge only this research file (stem), keep all other rows")
