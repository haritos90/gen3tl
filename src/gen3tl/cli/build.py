"""The translated ROM, fonts, graphics, patches and releases."""

from __future__ import annotations

import sys
from pathlib import Path

from ..lang import language
from ..core.charmap import Charmap
from ..profile.profile import Profile
from .common import (_context_records, _font_codes, _save_layout_and_charmap, _shadow_end, _share_limits,
                     _style_measures, _style_shadow_ends, _style_widths, add, command, line)


def pseudo_localize(text: str, growth: float) -> str:
    """Pseudo-localization through selected pack mappings and word growth."""
    import re as _re
    rules = language.current().data.get("pseudo", {})
    mapping = dict(zip(rules.get("source", ""), rules.get("target", "")))
    step = int(round(1 / growth)) if growth > 0 else 0
    out = []
    for tok in _re.split(r"(\{[^}]*\}|\\[nlp])", text):
        if tok.startswith("{") or (tok.startswith("\\") and len(tok) == 2):
            out.append(tok)
            continue
        chars, n = [], 0
        for ch in tok:
            chars.append(mapping.get(ch, ch))
            if ch.isalpha():
                n += 1
                if step and n % step == 0:
                    chars.append(rules.get("padding", "x"))
        out.append("".join(chars))
    return "".join(out)


@command("build font-check")
def font_check(args):
    """Check that the profile's font copies are byte-identical and round-trip."""
    from ..build.font import FontTable, check_copies, decode_glyph, encode_glyph
    prof = Profile.by_name(args.profile)
    rom = prof.load_rom(args.rom)
    fonts = FontTable.from_profile(prof)
    for f in fonts:
        bad = [g for g in range(f.count)
               if encode_glyph(decode_glyph(f.glyph_bytes(rom, g), f.fmt), f.fmt) != f.glyph_bytes(rom, g)]
        line("font", f.name)
        line("style", f.style)
        line("format", f.fmt)
        line("glyphs", f"{f.glyphs:#x}")
        line("widths", f"{f.widths:#x}")
        line("count", f"{f.count} glyphs")
        line("codec", "passed" if not bad else "failed")
        for glyph in bad[:8]:
            line("failed", f"{glyph:#x}")
    problems = check_copies(rom, fonts)
    for p in problems:
        line("error", p)
    if not problems:
        line("copies", "identical")
        line("fonts", f"{len(fonts)} fonts")
    return 1 if problems else 0


@command("build font-preview")
def font_preview(args):
    """Render the language pack glyphs beside the original font into build/."""
    from ..build.font import FontTable
    from ..lang.langpack import LangPack, style_problems
    from ..check.render import Canvas, GlyphSource, draw_text, write_png
    from ..lang.langpack import style_geometry
    prof = Profile.by_name(args.profile)
    rom = prof.load_rom(args.rom)
    style = args.style or prof["font_style"]["style"]
    font = next(f for f in FontTable.from_profile(prof) if f.style == style)
    top, rows = style_geometry(prof, style)
    pack = LangPack.load(args.lang, style, rows=rows, top=top)
    cm = pack.charmap()
    src = GlyphSource(rom, font, pack.glyph_slots(rom, font))
    out = args.project.build / "preview"; out.mkdir(parents=True, exist_ok=True)
    maxw = 8 if font.fmt == "8x16" else 16
    problems = {c: style_problems(g, rows, top, maxw) for c, g in pack.glyphs.items()}
    problems = {c: p for c, p in problems.items() if p}
    for c, p in problems.items():
        line("glyph", c)
        for problem in p[:3]:
            line("warning", problem)
    lines = list(args.lines) or language.current().data.get("preview_lines", [
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz", "0123456789 !?…,."
    ])
    data = [cm.encode(l) for l in lines]
    from ..check.render import line_width
    w = max(line_width(src, d) for d in data) + 16
    cv = Canvas(w, 16 * len(lines) + 12)
    y = 6
    for d in data:
        y = draw_text(cv, src, 8, y, d)
    path = out / f"font-{args.lang}-{style}.png"
    write_png(path, cv.scaled(args.scale))
    line("wrote", path)
    line("glyphs", f"{len(pack.glyphs)} glyphs")
    line("problems", f"{len(problems)} glyphs")
    return 1 if problems else 0


@command("build font")
def font_build(args):
    """Write the language pack glyphs into a copy of the base ROM and verify it."""
    from ..build.font import FontTable, write_glyphs, encode_glyph, BACKGROUND, OUTSIDE
    from ..lang.langpack import LangPack, load_styles, slots_by_style
    prof = Profile.by_name(args.profile)
    base = prof.load_rom(args.rom)
    fonts = FontTable.from_profile(prof)
    styles = slots_by_style(load_styles(args.lang, prof))
    pack = LangPack.load(args.lang)
    copies = pack.copy_slots()
    failures = pack.base_problems(base, fonts)
    for f in fonts:
        # Only allocated destinations require blank glyphs.
        for b in pack.allocated_slots():
            px = f.glyph(base, b)
            if any(v not in (BACKGROUND, OUTSIDE) for row in px for v in row):
                failures.append(f"slot {b:#04x} is not blank in {f.name}")
        for b, src in copies.items():
            if all(v in (BACKGROUND, OUTSIDE) for row in f.glyph(base, src) for v in row):
                failures.append(f"copy source {src:#04x} of slot {b:#04x} is blank in {f.name}")
    rom = base.copy()
    changed = write_glyphs(rom, fonts, styles, copies)
    allowed = set()
    for off, n in changed:
        allowed.update(range(off, off + n))
    diff = [i for i in range(len(rom)) if rom.data[i] != base.data[i]]
    outside = [i for i in diff if i not in allowed]
    if outside:
        failures.append(f"{len(outside)} changed bytes outside glyph/width slots, first {outside[0]:#x}")
    for f in fonts:
        for b, g in styles[f.style].items():
            if f.glyph_bytes(rom, b) != encode_glyph(g.pixels, f.fmt) or f.width(rom, b) != g.width:
                failures.append(f"{f.name} slot {b:#04x} does not read back")
        for b, src in copies.items():
            if f.glyph_bytes(rom, b) != f.glyph_bytes(base, src) or f.width(rom, b) != f.width(base, src):
                failures.append(f"{f.name} slot {b:#04x} is no copy of {src:#04x}")
    out = args.project.build / "font-only.gba"
    out.parent.mkdir(parents=True, exist_ok=True)
    rom.save(out)
    for st, sl in styles.items():
        n = sum(1 for f in fonts if f.style == st)
        line("style", st)
        line("slots", f"{len(sl)} glyphs")
        line("copies", f"{len(copies)} copies")
        line("fonts", f"{n} fonts")
    line("changed", f"{len(diff)} bytes")
    line("outside", f"{len(outside)} bytes")
    for x in failures:
        line("error", x)
    line("wrote", out)
    line("verified", "failed" if failures else "passed")
    return 1 if failures else 0


@command("build font-compare")
def font_compare(args):
    """Render a labelled sheet: each new glyph above the original glyph it is modelled on."""
    from ..build.font import FontTable
    from ..lang.langpack import LangPack
    from ..check.render import Canvas, GlyphSource, draw_text, write_png, line_width
    from ..lang.langpack import style_geometry
    prof = Profile.by_name(args.profile)
    rom = prof.load_rom(args.rom)
    style = args.style or prof["font_style"]["style"]
    font = next(f for f in FontTable.from_profile(prof) if f.style == style)
    top, rows = style_geometry(prof, style)
    pack = LangPack.load(args.lang, style, rows=rows, top=top)
    cm = pack.charmap()
    src = GlyphSource(rom, font, pack.glyph_slots(rom, font))
    pairs = [p.split(":") for p in (args.pairs or language.current().data.get("font_pairs", "")).split()]
    cell = 22
    rows = [("NEW", [a for a, _ in pairs]), ("ORIGINAL", [b for _, b in pairs])]
    label_w = max(line_width(src, cm.encode(r[0])) for r in rows) + 12
    cols_per_row = 16
    blocks = [pairs[i:i + cols_per_row] for i in range(0, len(pairs), cols_per_row)]
    cv = Canvas(label_w + cell * cols_per_row + 8, len(blocks) * 44 + 8)
    y = 4
    for blk in blocks:
        draw_text(cv, src, 4, y, cm.encode("NEW"))
        draw_text(cv, src, 4, y + 18, cm.encode("ORIGINAL"))
        for i, (a, b) in enumerate(blk):
            x = label_w + i * cell
            draw_text(cv, src, x + 4, y, cm.encode(a))
            draw_text(cv, src, x + 4, y + 18, cm.encode(b))
            cv.rect(x, y - 1, x + cell - 2, y + 34, (200, 200, 220))
        y += 44
    out = args.project.build / "preview"; out.mkdir(parents=True, exist_ok=True)
    path = out / f"font-{args.lang}-{style}-compare.png"
    write_png(path, cv.scaled(args.scale))
    line("wrote", path)


def build_report_path(out, default=None) -> "Path":
    """The default build reports to build/build-report.md; other builds write beside the ROM."""
    from pathlib import Path
    out = Path(out)
    if default is None:
        from ..project import Registry
        registry = Registry.load()
        prof = Profile.by_name(registry.default_profile)
        from ..build.release import build_names
        project = registry.resolve(prof, registry.default_language)
        default = project.build / f"{build_names(prof, project.language, project.release).output}.gba"
    return out.parent / "build-report.md" if out.resolve() == Path(default).resolve() else out.with_suffix(".report.md")


@command("build rom")
def build(args):
    """Build the translated ROM: font, layout, QA, injection, keyboard pages, graphics, verification, report."""
    from collections import Counter
    from pathlib import Path
    from ..discover import inventory as inv_mod
    from ..build import keyboard as kb_mod
    from ..check import languagerules, qa as qa_mod
    from ..catalog.catalog import load_catalog
    from ..build.font import FontTable, write_glyphs
    from ..build.inject import DiffRegistry, diff_positions, field_bytes, inject, verify
    from ..lang.langpack import LangPack, LangPackError
    from ..check.layout import Measure, check, layout
    prof = Profile.by_name(args.profile)
    if args.source_as_translation:
        from ..build.textprefix import original_limits
        prof = original_limits(prof)
    from ..catalog import extract
    catalog = load_catalog(Path(args.po_root), source={}, english=False)
    base = prof.load_rom(args.rom)
    try:   # a key-only catalog's English is read from this ROM
        extract.from_rom(catalog, prof, base, getattr(args, "project", None))
    except (OSError, ValueError) as exc:
        line("error", exc, file=sys.stderr)
        return 1
    fonts = FontTable.from_profile(prof)
    pack = LangPack.load(args.lang, prof["font_style"]["style"])
    base_problems = pack.base_problems(base, fonts)
    if base_problems:
        raise ValueError("; ".join(base_problems))
    slots = pack.glyph_slots(base, fonts[0])
    cm = pack.charmap()
    rule_problems = languagerules.catalog_problems(
        prof, catalog, cm, source_as_translation=args.source_as_translation,
        glyphs=set(Charmap().decode_table) | set(slots))
    for problem in rule_problems:
        line(problem.severity, f"{problem.entry}: engine language rule: {problem.message}", file=sys.stderr)
    if any(problem.severity == "error" for problem in rule_problems):
        return 1
    width = lambda b: slots[b].width if b in slots else fonts[0].width(base, b)
    measure = Measure(width, prof.get("layout", {}).get("placeholders", {}),
                      styles=_style_widths(prof, base, args.lang), shadow_end=_shadow_end(slots, fonts[0], base),
                      shadow_styles=_style_shadow_ends(prof, base, args.lang), fonts=_font_codes(prof))
    inv = inv_mod.load(args.project.build / "inventory.json")
    ctx = _context_records()
    limits = qa_mod.compute_limits(prof, inv, catalog, measure, cm, ctx,
                                   english_slots=args.source_as_translation, rom=base)
    _share_limits(args, {"profile": prof, "rom": base, "charmap": cm, "measure": measure, "inventory": inv,
                         "catalog": catalog, "context": ctx, "english_slots": args.source_as_translation,
                         "limits": limits})
    windows = qa_mod.Windows(prof, inv, _style_measures(prof, base, args.lang))
    from ..build.release import expand, load as load_version
    version = load_version()   # replaces @VERSION@ before layout, checks and encoding
    import re
    by_kind = {x["id"]: x["kind"] for x in inv["entries"]}
    by_entry = {x["id"]: x for x in inv["entries"]}
    pads = {t["name"]: t.get("pad", 0x00) for t in prof.get("table", [])}   # pad byte of each table's fixed fields
    texts, qa_skipped, status = {}, [], Counter()
    for sid, (p, e) in catalog.items():
        state = None   # of a catalog translation
        if args.source_as_translation:
            target = e.msgid
        elif args.pseudo is not None:
            target = pseudo_localize(e.msgid, args.pseudo)
        elif args.stress_growth:
            # Stress growth uses the pack filler.
            target = e.msgid
            if by_kind.get(sid) != "fixed":
                plain = len(re.sub(r"\{[^}]*\}|\\[nlp]", "", e.msgid))
                n = max(1, int(plain * args.stress_growth))
                target = e.msgid + ("" if n < 2 else " " + " ".join([language.current().data.get("pseudo", {}).get("stress", "xxx")] * max(1, n // 4)))
        else:
            if e.state == "original":
                status["untranslated"] += 1
                continue
            target = "" if e.state == "empty" else expand(e.msgstr, version)
            state = "translated" if e.fuzzy else "approved"
        lim = limits[sid]
        # source-as-translation keeps English breaks; over-wide lines are skipped
        laid = target if args.source_as_translation else layout(target, lim, measure, cm)
        probs = check(laid, lim, measure, cm) + windows.problems(sid, target)
        if args.ignore_width:  # test builds: show over-wide text; byte and length limits hold
            probs = [x for x in probs if not x.startswith("line too wide")]
        if probs:
            qa_skipped.append((sid, probs[0]))
            continue
        blob = cm.encode(laid)
        e_inv = by_entry.get(sid)
        if e_inv is not None and "unterminated" in e_inv["flags"]:  # no terminator: pad to the field length
            blob = field_bytes(blob, e_inv["length"], pads.get(e_inv["table"], 0x00))
        texts[sid] = blob
        status["included"] += 1
        if state:
            status[state] += 1
    if not (args.no_font or args.source_as_translation or args.pseudo is not None or args.stress_growth):
        collisions = inv_mod.untranslated_slot_usage(inv, pack.allocated_slots(), Charmap(),
                                                    catalog, set(texts))
        if collisions:
            ids = ", ".join(sorted({sid for sid, _ in collisions}))
            raise ValueError(f"source glyph collision requires an included translation: {ids}")
    rom = base.copy()
    font_bytes = set()
    if not args.no_font:
        from ..lang.langpack import load_styles, slots_by_style
        for off, n in write_glyphs(rom, fonts, slots_by_style(load_styles(args.lang, prof)), pack.copy_slots()):
            font_bytes.update(range(off, off + n))
    tables = {t["name"]: t for t in prof.get("table", [])}
    kb = prof.get("keyboard")
    reserved = [tuple(kb["reserved"])] if kb else []
    from ..build.graphics import (GraphicsConfig, GraphicsResult, inject_graphics, report_lines,
                           verify_graphics, verify_graphic_sources)
    graphics_config = GraphicsConfig.from_profile(prof)
    if "inventory" in graphics_config.section:
        reserved += graphics_config.ranges()
    res = inject(rom, base, inv, texts, tables, reuse_slots=args.reuse_slots, reserved=reserved,
                 computed=prof.get("strings", {}).get("computed", []),
                 table_bases=prof.get("strings", {}).get("table_base", []),
                 text_prefixes=[] if args.source_as_translation else prof.get("selected_text_prefixes", []))
    gfx = GraphicsResult()
    if not args.no_graphics:  # after the text, in its free space; checked per graphic
        gfx = inject_graphics(rom, base, graphics_config, res.space, res.allowed | font_bytes)
        res.free_used, res.free_remaining = res.space.used, res.space.remaining
    kb_patch, kb_fails = None, []
    if kb and not args.no_keyboard:  # copied sheets need translated graphic pointers
        try:
            kb_patch = kb_mod.plan(kb, base, rom)
            kb_mod.apply(rom, kb_patch)
            kb_fails = kb_mod.verify(rom, kb, kb_patch)
        except kb_mod.KeyboardError as e:
            kb_fails = [f"keyboard: {e}"]
    kb_bytes = kb_patch.allowed if kb_patch else set()
    from ..build import spritefont
    sprite_plans, sprites = [], spritefont.Result()
    if not args.no_font:   # the pack's letters, like the fonts
        try:
            for font in spritefont.from_profile(prof):
                art = LangPack.load(args.lang, font.style, rows=font.cell_rows, top=0)
                sprite_plans.append(spritefont.plan(font, base, art))
            sprites = spritefont.apply(rom, base, sprite_plans, res.space,
                                       res.allowed | font_bytes | kb_bytes | gfx.allowed)
        except (spritefont.SpriteFontError, LangPackError, OSError) as exc:
            sprites.failures.append(str(exc))
        res.free_used, res.free_remaining = res.space.used, res.space.remaining
    from ..build import codereplace
    code_plans, code = [], codereplace.Result()
    if not args.source_as_translation:   # the English text keeps the original screens
        try:
            code_plans = codereplace.plan(prof, cm)
            code = codereplace.apply(rom, base, code_plans, res.space,
                                     res.allowed | font_bytes | kb_bytes | gfx.allowed | sprites.allowed)
        except codereplace.CodeReplacementError as exc:
            code.failures.append(str(exc))
        res.free_used, res.free_remaining = res.space.used, res.space.remaining
    from ..build import easychat
    alpha_plan, alpha = None, easychat.Result()
    if not (args.source_as_translation or args.pseudo is not None or args.stress_growth):
        try:   # the words as shown, so after every text change
            decl, pack_data = easychat.from_profile(prof), language.load(args.lang).data
            if (decl is not None and "easy_chat_alphabet" in pack_data
                    and easychat.changed(decl, base, rom, tables, cm)):
                alpha_plan = easychat.plan(decl, base, rom, tables, cm, pack_data["easy_chat_alphabet"],
                                           pack_data.get("fold", {}))
                alpha = easychat.apply(rom, alpha_plan, res.allowed | font_bytes | kb_bytes | gfx.allowed
                                       | sprites.allowed | code.allowed)
        except easychat.EasyChatError as exc:
            alpha.failures.append(str(exc))
    guard = DiffRegistry()
    guard.add("keyboard", kb_bytes)
    guard.add("graphics", gfx.allowed)
    sprites.register(guard)
    code.register(guard)
    alpha.register(guard)
    fails = (verify(rom, base, res, texts, font_bytes, guard)
             + kb_fails + gfx.failures + verify_graphics(rom, gfx)
             + sprites.failures + spritefont.verify(rom, base, sprite_plans, sprites)
             + code.failures + codereplace.verify(rom, code_plans, code)
             + alpha.failures + easychat.verify(rom, alpha_plan, alpha))
    if "inventory" in graphics_config.section:
        fails += verify_graphic_sources(rom, base, graphics_config, gfx.allowed)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    import json
    identity = {"profile": prof.name, "language": args.lang, "rom_sha1": base.sha1(),
                "built_sha1": rom.sha1(), "version": version}
    standard = not any((args.source_as_translation, args.no_font,
                        args.no_keyboard, args.no_graphics, args.ignore_width,
                        args.stress_growth, args.pseudo is not None, args.reuse_slots))
    preserved = None
    if (out.resolve() == Path(args.default_build).resolve() and standard
            and not getattr(args, "build_root_override", False)   # scoped builds are not releases
            and not (fails or qa_skipped or res.skipped)):
        from ..build.release import game_title, publish_build, superseded_outputs
        preserved = publish_build(bytes(rom.data), out, version, identity, args.changes,
                                  name=args.release_name, base=bytes(base.data),
                                  earlier=args.earlier_names, archive_name=args.archive_name,
                                  release_label=args.release_label, notes=args.project.release_notes,
                                  superseded=superseded_outputs(out.parent, game_title(prof), args.lang, out.stem))
    else:
        rom.save(out)
        out.with_suffix(".project.json").write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")
    modes = Counter(p.mode for p in res.placements)
    changed = len(diff_positions(rom.data, base.data))
    lines = ["# Build report", "", f"Output: {out}", f"Translation version: {version}", f"Base SHA-1: {base.sha1()}",
             f"Built SHA-1: {rom.sha1()}  MD5: {rom.md5()}", "",
             "| Item | Count |", "|---|---|"]
    lines += [f"| catalog: {k} | {v} |" for k, v in sorted(status.items())]
    lines += [f"| placement: {k} | {v} |" for k, v in sorted(modes.items())]
    lines += [f"| skipped by layout check | {len(qa_skipped)} |", f"| skipped by injector | {len(res.skipped)} |",
              f"| held by unclassified references | {len(res.held)} |",
              f"| changed bytes | {changed} |",
              f"| free space: total / used / remaining | {res.free_total} / {res.free_used} / {res.free_remaining} |",
              f"| keyboard pages | {kb_patch.count if kb_patch else 'not written'} |",
              f"| keyboard bytes allowed | {len(kb_bytes)} |",
              f"| graphics: declared / written | {gfx.declared} / {sum(p.mode != 'same' for p in gfx.placements)} |",
              f"| tilemaps: declared / written | {gfx.declared_tilemaps} / "
              f"{sum(t.mode != 'same' for t in gfx.tilemaps)} |",
              f"| graphics bytes allowed | {len(gfx.allowed)} |",
              f"| sprite fonts: declared / written | {len(sprite_plans)} / {len(sprites.placements)} |",
              f"| sprite font bytes allowed | {len(sprites.allowed)} |",
              f"| code replacements: selected / written | {len(code_plans)} / {len(code.placements)} |",
              f"| code replacement bytes allowed | {len(code.allowed)} |",
              f"| easy-chat alphabet lists | {len(alpha_plan.lists) if alpha.written else 'not written'} |",
              f"| easy-chat alphabet bytes allowed | {len(alpha.allowed)} |",
              f"| verification failures | {len(fails)} |"]
    if kb_patch:
        lines += ["", "## Keyboard pages", ""] + [f"- {x}" for x in kb_mod.describe(kb, kb_patch)]
    lines += report_lines(gfx)
    lines += spritefont.report_lines(sprites)
    lines += codereplace.report_lines(code)
    lines += easychat.report_lines(alpha_plan, alpha)
    if preserved:
        lines += ["", f"Preserved version: {preserved}", "Gameplay: recorded separately in build history."]
    if fails:
        lines += ["", "## Verification failures", ""] + [f"- {f}" for f in fails[:200]]
    if qa_skipped or res.skipped:
        lines += ["", "## Skipped", ""] + [f"- {sid}: {why}" for sid, why in (qa_skipped + res.skipped)[:300]]
    if res.held:
        lines += ["", "## Unclassified references", "", "| String | References |", "|---|---|"]
        lines += [f"| {sid} | {', '.join(hex(at) for at in positions)} |" for sid, positions in res.held.items()]
    rep = "\n".join(lines) + "\n"
    build_report_path(out, args.default_build).write_text(rep, encoding="utf-8")
    line("wrote", out)
    line("report", build_report_path(out, args.default_build))
    line("sha1", rom.sha1())
    line("md5", rom.md5())
    for state, count in sorted(status.items()):
        line("catalog", state)
        line("count", f"{count} entries")
    for mode, count in sorted(modes.items()):
        line("placement", mode)
        line("count", f"{count} entries")
    line("skipped", f"{len(qa_skipped) + len(res.skipped)} entries")
    for sid, positions in res.held.items():
        line("held", f"{sid}: {', '.join(hex(at) for at in positions)}")
    line("changed", f"{changed} bytes")
    line("free", f"{res.free_remaining} bytes")
    line("used", f"{res.free_used} bytes")
    line("total", f"{res.free_total} bytes")
    line("keyboard", f"{kb_patch.count if kb_patch else 0} pages")
    line("graphics", f"{sum(p.mode != 'same' for p in gfx.placements)} graphics")
    line("tilemaps", f"{sum(t.mode != 'same' for t in gfx.tilemaps)} tilemaps")
    line("sprites", f"{len(sprites.placements)} fonts")
    line("routines", f"{len(code.placements)} replacements")
    line("alphabet", f"{len(alpha_plan.lists) if alpha.written else 0} lists")
    line("verified", "failed" if fails else "passed")
    for failure in fails[:200]:
        line("error", failure)
    if preserved:
        line("preserved", preserved)
    if args.source_as_translation and args.no_font:
        outside = [i for i in diff_positions(rom.data, base.data) if i not in kb_bytes and i not in gfx.allowed]
        if outside:
            line("verified", "failed")
            line("error", "unexpected source differences")
            line("outside", f"{len(outside)} bytes")
            line("offset", f"{outside[0]:#x}")
            return 1
    return 1 if fails else 0


@command("build history")
def build_history(args):
    """List preserved builds and record local gameplay observations."""
    from ..build.release import history, record_playtest
    from ..core.terminal import table
    ledger = Path(args.default_build).parent / "translation-history.json"
    prof = Profile.by_name(args.profile)
    if args.mark:
        if not args.status or not args.note:
            raise ValueError("--mark requires --status and --note")
        record_playtest(ledger, prof.name, args.lang, args.mark,
                        args.status.replace("-", " "), args.note, args.project.release_notes)
    elif args.status or args.note:
        raise ValueError("--status and --note require --mark VERSION")
    rows = [r for r in history(ledger)["versions"]
            if (r["profile"], r["language"]) == (prof.name, args.lang)]
    line("history", ledger)
    line("builds", f"{len(rows)} builds")
    table(("version", "gameplay", "file", "archive", "sha1"),
          ((row["version"], row.get("gameplay", "unknown").replace(" ", "-"),
            row.get("delta") or row["file"], row.get("archive") or "-", row["built_sha1"]) for row in rows))
    events = [(row["version"], event["at"], event["status"].replace(" ", "-"))
              for row in rows for event in row.get("playtests", [])]
    if events:
        table(("version", "date", "gameplay"), events)
    line("status", "recorded" if args.mark else "ready")


@command("build patcher-data")
def patcher_data(args):
    """Prepare one verified release for the static browser patcher, with name tables and public notes."""
    from ..web.patcher import add_names, add_notes, copy_releases, names_document, notes_document, prepare
    from ..build.release import game_title, history, release_names
    from ..verify.savefile import language_rules
    prof = Profile.by_name(args.profile)
    names_of = None
    if prof.get("save"):
        _layout, table, _shared = _save_layout_and_charmap(prof, args.lang)
        rules = language_rules()
        names_of = lambda row, target: names_document(prof, row, base, target, table, rules)   # noqa: E731
    try:
        base = bytes(prof.load_rom(args.rom).data)
        if args.names_only:
            if names_of is None:
                raise ValueError(f"{prof.name}: no save layout, so no name tables")
            added = add_names(prof, base, Path(args.out), names_of)
            if not args.project.game and Path(args.out).resolve() == args.project.releases.resolve():
                copy_releases(Path(args.out), Path(args.out), prof.name)
            line("wrote", args.out)
            line("tables", f"{len(added)} tables")
            line("status", "published" if added else "unchanged")
            for name in added:
                line("table", name)
            return 0
        if not (args.built and args.patch_file):
            raise ValueError("--built and --patch-file name the release (or --names-only)")
        records = history(Path(args.built).parent / "translation-history.json")["versions"]

        def notes_of(row):
            identity = (row["profile"], row["language"], row["version"], row["patch_sha256"])
            matches = [r for r in records if identity == (r.get("profile"), r.get("language"), r.get("version"),
                                                          r.get("patch_sha256"))]
            if len(matches) != 1:
                raise ValueError(f"{row['version']}: no unique public notes match the released patch")
            return notes_document(row, matches[0])

        title, derived = game_title(prof), args.project.release.exists()

        def output_of(version):   # each record by its own version
            stem = release_names(title, args.lang, version).output if derived else prof.output_stem(args.lang)
            return f"{stem}.gba"

        row = prepare(prof, args.lang, base, Path(args.built), Path(args.patch_file), Path(args.out), names_of, notes_of,
                      output_of=output_of)
        add_notes(prof, Path(args.out), notes_of)
        if not args.project.game and Path(args.out).resolve() == args.project.releases.resolve():
            copy_releases(Path(args.out), Path(args.out), prof.name)
    except (OSError, ValueError) as exc:
        line("error", exc)
        return 1
    line("project", f"{row['profile']}/{row['language']}")
    line("version", row["version"])
    line("wrote", Path(args.out) / "releases.json")
    line("names", "published" if "names" in row else "absent")
    line("notes", "published" if "notes" in row else "absent")
    return 0




@command("build patch")
def patch(args):
    """Create a verified UPS patch and concise translation release notes."""
    import hashlib
    from ..build.release import (_delta, _write_bytes, _write_json, expand, history,
                          load as load_version, public_changes, publish_patch, update_notes)
    from ..verify.savefile import standalone_source
    descriptions = public_changes(args.changes)
    prof = Profile.by_name(args.profile)
    base = bytes(prof.load_rom(args.rom).data)
    built = Path(args.built).read_bytes()
    version = load_version()
    out = Path(expand(args.out, version))
    default_output = out.resolve() == Path(args.default_patch).resolve()
    ledger = Path(args.default_build).parent / "translation-history.json"
    records = history(ledger)
    matches = [r for r in records["versions"] if
               (r["profile"], r["language"], r["version"]) == (prof.name, args.lang, version)]
    if default_output and any(r["built_sha1"] != hashlib.sha1(built).hexdigest() for r in matches):
        raise ValueError(f"translation {version} already identifies different bytes; raise the version")
    data = _delta(base, built)
    if default_output:
        publish_patch(out, base, built, version, args.release_name, args.earlier_names, args.archive_name)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        _write_bytes(out, data)
    layout, table, shared = _save_layout_and_charmap(prof, args.lang)
    Path(args.converter).write_text(standalone_source(layout, table, shared), encoding="utf-8")
    digest = hashlib.sha256(data).hexdigest()
    if default_output and matches:
        for row in matches:
            row["release_label"], row["patch_sha256"] = args.release_label, digest
            if descriptions:
                row["release_notes"] = descriptions
        _write_json(ledger, records)
    default_notes = Path(args.notes).resolve() == args.project.release_notes.resolve()
    update_notes(Path(args.notes), records if default_notes else None, version=version,
                 label=args.release_label, changes=args.changes if descriptions else None, patch_sha256=digest)
    line("patch", out)
    line("size", f"{len(data)} bytes")
    line("release", args.release_label)
    line("version", version)
    line("converter", args.converter)
    line("notes", args.notes)
    return 0


@command("build tag")
def build_tag(args):
    """Derive the release tag and check the commit that holds it."""
    import json
    import subprocess
    from ..build.release import load as load_version
    from ..web.patcher import publication, read_patch, validate as validate_manifest
    project = args.project
    try:
        version = load_version(project.release)
        tag = f"{project.language}-v{version}"
        label = args.release_label
        manifest = project.releases / "releases.json"
        rows = validate_manifest(json.loads(manifest.read_text(encoding="utf-8")))
        matches = [row for row in rows
                   if (row["profile"], row["language"], row["version"]) ==
                   (project.profile, project.language, version)]
        if len(matches) != 1:
            raise ValueError(f"{manifest}: no release {project.profile}/{project.language} {version}")
        if any(matches[0].get(key) != value for key, value in publication(matches[0]).items()):
            raise ValueError(f"{manifest}: missing release publication")
        read_patch(project.releases, matches[0])
        for key in ("names", "notes"):
            if key not in matches[0]:
                continue
            path = project.releases / matches[0][key]
            if path.is_symlink() or not path.resolve().is_relative_to(project.releases.resolve()) or not path.is_file():
                raise ValueError(f"{path}: missing release file")
        top = subprocess.run(["git", "-C", str(project.root), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True)
        if top.returncode != 0:
            raise ValueError(f"{project.root}: not a git repository")
        root = Path(top.stdout.strip()).resolve()

        def committed(path: Path) -> bytes:
            if not path.resolve().is_relative_to(root):
                raise ValueError(f"{path}: not inside the git repository")
            name = path.resolve().relative_to(root).as_posix()
            shown = subprocess.run(["git", "-C", str(project.root), "show", f"HEAD:{name}"], capture_output=True)
            if shown.returncode != 0 or shown.stdout != path.read_bytes():
                raise ValueError(f"{name}: not in the current commit")
            return shown.stdout

        committed(manifest)
        if "notes" in matches[0]:
            notes = json.loads(committed(project.releases / matches[0]["notes"]))
            if (notes.get("release_label"), notes.get("version")) != (label, version):
                raise ValueError(f"release notes at HEAD do not carry [{label}] {version}")
        local = subprocess.run(["git", "-C", str(project.root), "tag", "--list", tag],
                               capture_output=True, text=True)
        if local.returncode != 0 or local.stdout.strip():
            raise ValueError(f"tag {tag} already exists")
        commit = subprocess.run(["git", "-C", str(project.root), "rev-parse", "HEAD"],
                                capture_output=True, text=True)
        if commit.returncode != 0:
            raise ValueError(f"{project.root}: no release commit")
    except (OSError, ValueError) as exc:
        line("error", exc)
        return 1
    line("tag", tag)
    line("commit", commit.stdout.strip())
    return 0


@command("build release-notes")
def release_notes(args):
    """Write the title and body of the GitHub release of a tag: the commits of the language since its previous tag."""
    from ..build.releasenotes import compose
    try:
        if args.project is None:
            raise ValueError(f"no project of the language {args.lang}")
        text = compose(args.project, args.tag, args.repository, args.release_label)
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "title.txt").write_text(text.title + "\n", encoding="utf-8")
        (out / "body.md").write_text(text.body, encoding="utf-8")
    except (OSError, ValueError) as exc:
        line("error", exc)
        return 1
    line("tag", args.tag)
    line("previous", text.previous or "none")
    line("commits", len(text.commits))
    line("title", out / "title.txt")
    line("body", out / "body.md")
    return 0


@command("build gfx-extract")
def gfx_extract(args):
    """Write inventory graphics as indexed PNG sheets with their palette and a JSON sidecar."""
    from ..build.graphics import GraphicsConfig, GraphicsError, write_sheet
    prof = Profile.by_name(args.profile)
    base = prof.load_rom(args.rom)
    cfg = GraphicsConfig.from_profile(prof)
    failed = 0
    for gid in args.ids:
        try:
            g = cfg.graphic(gid)
            sheet = cfg.sheet(base, g)
        except GraphicsError as e:
            line("error", e)
            failed += 1
            continue
        png, side = write_sheet(sheet, args.out, base.sha1())
        line("graphic", gid)
        line("offset", f"0x{g.offset:07X}")
        line("compression", g.compression)
        line("slot", f"{g.slot} bytes")
        line("decoded", f"{g.size} bytes")
        line("tiles", f"{g.tiles} tiles")
        line("depth", f"{g.bpp} bpp")
        line("width", f"{sheet.width} px")
        line("height", f"{sheet.height} px")
        line("stride", f"{sheet.width_tiles} tiles/row")
        line("palette", sheet.palette_source)
        for pointer in g.pointers:
            line("pointer", f"0x{pointer:07X}")
        line("wrote", png)
        line("wrote", side)
    return 1 if failed else 0


@command("build gfx-check")
def gfx_check(args):
    """Pixel check: compare an edited sheet with the original graphic."""
    from ..build.graphics import GraphicsConfig, check_report
    from ..check.render import read_png_image
    prof = Profile.by_name(args.profile)
    base = prof.load_rom(args.rom)
    rep = check_report(GraphicsConfig.from_profile(prof), base, args.id, lambda: read_png_image(args.png),
                       [a for group in args.area for a in group], args.tiles)
    line("graphic", args.id)
    line("verified", "passed" if rep.code == 0 else "failed")
    for message in rep.lines:
        if message.startswith("FAIL     "):
            line("error", message.removeprefix("FAIL     "))
    if rep.result is not None:
        line("changed", f"{rep.result.changed_inside} pixels")
        line("appended", f"{rep.result.changed_appended} pixels")
        line("areas", f"{len(rep.areas)} areas")
        line("findings", f"{len(rep.result.findings)} findings")
        if not rep.result.findings and (rep.result.changed_inside or rep.tiles > rep.sheet.graphic.tiles):
            from ..build.graphics import lz77_compress
            encoded = rep.sheet.encode(rep.result.indices, rep.tiles)
            size = len(lz77_compress(encoded)) if rep.sheet.graphic.compressed else len(encoded)
            line("encoded", f"{size} bytes")
            line("slot", f"{rep.sheet.graphic.slot} bytes")
            line("placement", "inplace" if size <= rep.sheet.graphic.slot else "relocated")
    return rep.code


@command("build gfx-areas")
def gfx_areas(args):
    """Write edited graphics in the area form: only their text areas and appended tiles."""
    from pathlib import Path
    from ..build.graphics import GraphicsConfig, GraphicsError, apply_area_form, area_form_png, pixel_check
    from ..check.render import png_image, read_png_image
    prof = Profile.by_name(args.profile)
    base = prof.load_rom(args.rom)
    cfg = GraphicsConfig.from_profile(prof)
    edits = {e.id: e for e in cfg.edits}
    failed = 0
    for gid in args.ids:
        e = edits.get(gid)
        if e is None or e.form != "sheet":
            line("graphic", gid)
            line("error", "missing graphics edit" if e is None else "area form")
            failed += 1
            continue
        try:
            sheet = cfg.sheet(base, cfg.graphic(gid))
            chk = pixel_check(sheet, read_png_image(e.png), e.areas, e.tiles)
        except (GraphicsError, OSError, ValueError) as x:
            line("graphic", gid)
            line("error", x)
            failed += 1
            continue
        if chk.findings:
            line("graphic", gid)
            line("findings", f"{len(chk.findings)} findings")
            for finding in chk.findings:
                line("error", finding)
            failed += 1
            continue
        data = area_form_png(sheet, chk.indices, e.areas, e.tiles)
        back, problems = apply_area_form(sheet, png_image(data), e.areas, e.tiles)
        if problems or back.indices != chk.indices:   # the build reads the same sheet
            line("graphic", gid)
            line("error", "area roundtrip mismatch")
            failed += 1
            continue
        out = Path(args.out) / f"{gid}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        line("graphic", gid)
        line("source", e.png)
        line("wrote", out)
        line("size", f"{len(data)} bytes")
        line("form", "areas")
    return 1 if failed else 0


def register(sub):
    b = add(sub, "rom", build)
    b.add_argument("--po-root", default=None, help="catalog (default: the project's)")
    b.add_argument("--out", default=None, help="output ROM path")
    b.add_argument("--changes", default="", help="change description for the preserved local build")
    b.add_argument("--source-as-translation", action="store_true", help="use the English as translation")
    b.add_argument("--no-font", action="store_true", help="skip the language pack glyphs")
    b.add_argument("--no-keyboard", action="store_true",
                   help="skip the keyboard pages of the profile (keeps the base ROM's page count, for comparisons)")
    b.add_argument("--no-graphics", action="store_true", help="skip the edited graphics of the profile")
    b.add_argument("--reuse-slots", action="store_true", help="reuse slots of relocated strings")
    b.add_argument("--stress-growth", type=float, default=0.0, help="test: grow every English string by this share")
    b.add_argument("--pseudo", type=float, default=None, help="test: pseudo-localize with pack rules, growing words by this share")
    b.add_argument("--ignore-width", action="store_true",
                   help="test: include text wider than its window, to check the window in game")
    pt = add(sub, "patch", patch)
    pt.add_argument("--built", default=None, help="translated ROM to inspect")
    pt.add_argument("--out", default=None,
                    help="patch path; @VERSION@ becomes the translation's version (its release.toml)")
    pt.add_argument("--notes", default=None, help="release notes (default: the project's)")
    pt.add_argument("--changes", default="", help="translation changes for the release notes")
    pt.add_argument("--converter", default=None, help="output standalone save converter")
    pd = add(sub, "patcher-data", patcher_data)
    pd.add_argument("--built", help="preserved release ROM with its .project.json identity")
    pd.add_argument("--patch-file", help="released standard UPS patch")
    pd.add_argument("--names-only", action="store_true",
                    help="publish the save page's name tables of the profile's releases that lack them")
    pd.add_argument("--out", default=None, help="release directory (default: the project's releases/)")
    add(sub, "tag", build_tag)
    rn = add(sub, "release-notes", release_notes)
    rn.add_argument("--tag", required=True, help="release tag, <code>-v<version>")
    rn.add_argument("--repository", default=None, help="GitHub repository <account>/<name>, for links to the commits")
    rn.add_argument("--out", required=True, help="directory for title.txt and body.md")
    bh = add(sub, "history", build_history)
    bh.add_argument("--mark", metavar="VERSION", help="record a gameplay observation for this version")
    bh.add_argument("--status", choices=["launched", "issues-reported", "checked"],
                    help="gameplay observation status for --mark")
    bh.add_argument("--note", help="what was launched, checked or found")
    fb = add(sub, "font", font_build)
    add(sub, "font-check", font_check)
    fp = add(sub, "font-preview", font_preview)
    fp.add_argument("--scale", type=int, default=4, help="pixels per rendered pixel")
    fp.add_argument("lines", nargs="*", help="preview text lines; omitted: language pack samples")
    fp.add_argument("--style", default=None, help="font style (default: the main style)")
    fc = add(sub, "font-compare", font_compare)
    fc.add_argument("--scale", type=int, default=5, help="pixels per rendered pixel")
    fc.add_argument("--style", default=None, help="font style (default: the main style)")
    fc.add_argument("--pairs", default=None, help="space-separated original:translated glyph pairs")
    gx = add(sub, "gfx-extract", gfx_extract)
    gx.add_argument("ids", nargs="+", help="graphics inventory ids (G01...)")
    gx.add_argument("--out", default=None, help="directory for editable graphics sheets")
    gk = add(sub, "gfx-check", gfx_check)
    gk.add_argument("id", help="graphics inventory id")
    gk.add_argument("png", help="edited sheet")
    gk.add_argument("--area", nargs="+", action="append", default=[], metavar="X,Y,W,H",
                    help="text area in sheet pixels; repeat or list several")
    gk.add_argument("--tiles", type=int, default=None,
                    help="tile count of the edited sheet; above the graphic's, the extra tiles are appended")
    gr = add(sub, "gfx-areas", gfx_areas)
    gr.add_argument("ids", nargs="+", help="graphics inventory ids with a [[graphics.edit]] entry")
    gr.add_argument("--out", default=None, help="directory of the area forms (default: the language's graphics/areas)")
