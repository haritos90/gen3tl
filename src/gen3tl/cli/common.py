"""Shared parts of the group modules: the command registry and measurement helpers."""

from __future__ import annotations

from ..core.terminal import StatusParser, StatusVersion, line  # noqa: F401
from ..profile.profile import Profile

COMMANDS = {}


def add(sub, name: str, fn):
    """Register the handler and its group command."""
    parser = sub.add_parser(name, help=fn.__doc__, description=fn.__doc__)
    parser.set_defaults(cmd=fn.command_path)
    return parser


def command(path: str):
    def register(fn):
        COMMANDS[path] = fn
        fn.command_path = path
        return fn
    return register


def write_options(parser, *, dest="dry_run", write_value=False, default_write=True,
                  check_aliases=(), write_aliases=(), check_help="validate without changing target files",
                  write_help="write target files"):
    """Register exclusive check and write modes, preserving handler defaults."""
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", *check_aliases, dest=dest, action="store_const", const=not write_value,
                       help=check_help)
    modes.add_argument("--write", *write_aliases, dest=dest, action="store_const", const=write_value,
                       help=write_help)
    parser.set_defaults(**{dest: write_value if default_write else not write_value})


def _context_records():
    """build/context.json of the running project, else of the working directory, or None."""
    import json
    from pathlib import Path
    from ..project import active
    project = active()
    path = (project.build if project else Path("build")) / "context.json"
    if not path.exists():
        line("missing", path)
        line("widths", "profile")
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _measure_state(measure):
    def widths(function):
        return tuple(function(b) for b in range(512))

    def shadows(function):
        return None if function is None else tuple(bool(function(b)) for b in range(512))

    return (widths(measure.glyph_width), tuple(sorted(measure.ph.items())), measure.default_ph,
            tuple((name, widths(function)) for name, function in sorted(measure.styles.items())),
            shadows(measure.shadow_end),
            tuple((name, shadows(function)) for name, function in sorted(measure.shadow_styles.items())),
            tuple(sorted(measure.fonts.items())))


def _limits_state(args, prepared):
    from pathlib import Path
    root = Path(args.po_root)
    files = []
    for path in sorted(root.rglob("*.po")):
        stat = path.stat()
        files.append((path, stat.st_size, stat.st_mtime_ns))
    catalog = prepared["catalog"]
    entries = tuple((sid, path, repr(vars(entry))) for sid, (path, entry) in sorted(catalog.items()))
    return (repr(prepared["profile"].data), repr(prepared["inventory"]), _measure_state(prepared["measure"]),
            repr(vars(prepared["charmap"])), repr(prepared["context"]), prepared["rom"].sha1(),
            prepared["english_slots"], tuple(files), entries)


def _share_limits(args, prepared: dict) -> None:
    """Share prepared limits only within an explicit command runtime."""
    runtime = getattr(args, "runtime", None)
    if runtime is not None:
        runtime["translation limits"] = (_limits_state(args, prepared), prepared)


def _shared_limits(args, prepared: dict) -> dict | None:
    """The shared limits while every input is unchanged."""
    runtime = getattr(args, "runtime", None)
    if runtime is None:
        return None
    cached = runtime.get("translation limits")
    if cached is None:
        return None
    state, retained = cached
    if state != _limits_state(args, prepared):
        runtime.pop("translation limits", None)
        return None
    return retained


def _style_measures(prof, rom, lang: str) -> dict:
    """Measure and charmap per font style of the profile, for the table windows."""
    from ..build.font import FontTable
    from ..lang.langpack import load_styles
    from ..check.layout import Measure
    packs = load_styles(lang, prof)
    out = {}
    for f in FontTable.from_profile(prof):
        slots = packs[f.style].glyph_slots(rom, f)
        out[f.style] = (Measure(lambda b, s=slots, f=f: s[b].width if b in s else f.width(rom, b), {},
                                shadow_end=_shadow_end(slots, f, rom)),
                        packs[f.style].charmap())
    return out


def _window_measures(prof, rom, lang: str) -> dict:
    """The table windows' measures: font styles and sprite fonts under their names."""
    from ..lang.langpack import LangPack
    from ..web.editorfonts import sprite_measures
    out = _style_measures(prof, rom, lang)
    cm = LangPack.load(lang, prof["font_style"]["style"]).charmap()
    out.update({name: (m, cm) for name, m in sprite_measures(prof, lang).items()})
    return out


def _shadow_end(slots: dict, font, rom):
    """byte -> whether its glyph's last column holds no ink (font.shadow_end)."""
    from ..build.font import shadow_end
    return lambda b: shadow_end(slots[b].pixels, slots[b].width) if b in slots else font.shadow_end(rom, b)


def _style_widths(prof, rom, lang: str) -> dict:
    """Glyph widths per font style, for the [layout.window_style] ids."""
    return {style: m.glyph_width for style, (m, _) in _style_measures(prof, rom, lang).items()}


def _style_shadow_ends(prof, rom, lang: str) -> dict:
    """shadow_end per font style, beside _style_widths."""
    return {style: m.shadow_end for style, (m, _) in _style_measures(prof, rom, lang).items()}


def _font_codes(prof) -> dict:
    """{FONT xx} argument -> font style, beside _style_widths."""
    from ..build.font import font_codes
    return font_codes(prof)


def _save_layout_and_charmap(prof, lang: str):
    from ..lang.langpack import LangPack
    pack = LangPack.load(lang, prof["font_style"]["style"])
    cm = pack.charmap()
    table = {}
    for b in range(0x100):
        try:
            ch, _ = cm.decode(bytes([b, 0xFF]), 0)
        except Exception:
            continue
        if len(ch) == 1:
            table[b] = ch
    return prof["save"], table, {}


def _limits_and_matcher(args, overrides: dict | None = None, english_slots: bool = False):
    """Limits of the catalog with the placeholder widths of the context records; `overrides` replace inserted words."""
    from pathlib import Path
    from ..discover import inventory as inv_mod
    from ..check import qa as qa_mod
    from ..catalog.catalog import load_catalog
    from ..build.font import FontTable
    from ..lang.langpack import LangPack
    from ..check.layout import Measure
    prof = Profile.by_name(args.profile)
    rom = prof.load_rom(args.rom)
    font = FontTable.from_profile(prof)[0]
    pack = LangPack.load(args.lang, prof["font_style"]["style"])
    slots = pack.glyph_slots(rom, font)
    measure = Measure(lambda b: slots[b].width if b in slots else font.width(rom, b),
                      prof.get("layout", {}).get("placeholders", {}), styles=_style_widths(prof, rom, args.lang),
                      fonts=_font_codes(prof))
    inv = inv_mod.load(args.project.build / "inventory.json")
    limits = qa_mod.compute_limits(prof, inv, load_catalog(Path(args.po_root)), measure, pack.charmap(),
                                   _context_records(), overrides, english_slots=english_slots, rom=rom)
    matcher = None
    g = args.project.glossary
    if g.exists():
        from ..lang import termbase
        matcher = termbase.TermMatcher(termbase.load(g))
    return limits, matcher
