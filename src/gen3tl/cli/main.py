"""The command line parser: command groups and dispatch."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..lang import language
from ..core.terminal import CommandError, require_names, run_status
from ..profile.profile import Profile
from . import build, catalog, check, data, discover, lang, project, rom, verify, web
from .common import COMMANDS, StatusParser, StatusVersion, line

GROUPS = (rom, discover, catalog, check, build, verify, data, lang, web, project)
PROFILE_FREE = ("check commit-message", "web site-catalog", "web site-shell", "web registry", "web submission", "web assemble")


def needs_profile(args, registry):
    """Whether dispatch needs a selected game profile."""
    if not args.cmd or args.cmd in PROFILE_FREE or args.cmd == "web server" and args.page == "site":
        return False
    if args.cmd == "web import":
        from ..web.sitecatalog import read_export
        if not registry.projects or not args.export_file:
            return False
        try:
            return read_export(Path(args.export_file)) is None
        except (OSError, ValueError):
            return False
    return True


def shared_defaults(args, registry, explicit=()):
    """Resolve registry paths without loading a game profile."""
    from ..project import build_root
    from ..profile.profile import name_of
    args.registry = registry
    args.project_root = str(registry.root)
    args.profile = args.profile or registry.default_profile or None
    args.lang = args.lang or registry.default_language
    name = name_of(Path(args.profile)) if args.profile else None
    args.project = next((project for project in registry.projects
                         if (project.profile, project.language) == (name, args.lang)), None)
    args.build_root = build_root(registry.root)
    if args.project:
        project_defaults(args, args.project)
    site_defaults(args, registry, explicit)


def selection_options(p):
    """Register options accepted before or after a command."""
    p.add_argument("--profile", default=None, help="profile name, file or directory")
    p.add_argument("--lang", default=None, help="registered translation language")
    p.add_argument("--project-root", default=None, help="directory containing projects.toml")
    p.add_argument("--rom", default=None, help="base ROM path (default: from the profile)")
    p.add_argument("--ignore-pin", action="store_true", help="run a project that pins another toolkit version")


def parser():
    """The parser and the command names of each group."""
    p = StatusParser(prog="gen3tl", description="Inspect, translate and verify Gen 3 ROMs.")
    p.add_argument("--version", action=StatusVersion, nargs=0, help="print the toolkit version")
    selection_options(p)
    sub = p.add_subparsers(title="command groups", metavar="<group>", required=True)
    groups = {}
    for module in GROUPS:
        name = module.__name__.rsplit(".", 1)[1]
        summary = module.__doc__.strip()
        commands = sub.add_parser(name, help=summary, description=summary).add_subparsers(
            title="commands", metavar="<command>", required=True)
        module.register(commands)
        groups[name] = set(commands.choices)
    return p, groups


def project_defaults(args, project) -> None:
    """Fill the data paths a command leaves to its project."""
    from ..check.rules import list_path
    paths = {"po_root": project.catalog, "glossary": project.glossary, "rules": list_path(project.translation),
             "index": project.translation / "term-index.tsv", "research": project.translation / "research",
             "snapshot": project.snapshot}
    if args.cmd == "discover flag":
        paths.pop("index")
    if args.cmd != "catalog batch-export":   # there a missing plan means none
        paths["plan"] = project.batches / "plan.json"
    if args.cmd == "web snapshot":
        paths["out"] = project.snapshot
    if args.cmd == "catalog batch-plan":
        paths["defer"] = project.build / "reach.json"
    if args.cmd == "check coverage":
        paths.update(inventory=project.build / "inventory.json", context=project.build / "context.json",
                     out=project.build / "coverage-report.md")
    outputs = {"check unmeasured": "unmeasured-report.md", "lang sprite-sheet": "preview",
               "verify ui-shots": "screens/ui", "verify save-fixture": "saves", "verify save-check": "saves/check",
               "build gfx-extract": "graphics/edit"}
    if args.cmd in outputs:
        paths["out"] = project.build / outputs[args.cmd]
    if args.cmd == "lang fill-tables":
        paths["missing"] = project.build / "fill-missing.tsv"
    if args.cmd == "data pokedex":
        paths["out"] = project.dataset or project.build / "pokedex" / project.profile
    if args.cmd == "build gfx-areas":
        paths["out"] = project.translation / "graphics/areas"
    if args.cmd == "build patch":
        paths["notes"] = project.release_notes
    if args.cmd == "build patcher-data":
        paths["out"] = project.releases
    if args.cmd == "web assemble":
        paths["out"] = project.site
    for field, path in paths.items():
        if hasattr(args, field) and getattr(args, field) is None:
            setattr(args, field, str(path))


def build_defaults(args, prof, registry, selected):
    """Resolve build names without opening ROM or catalog data."""
    from ..build.release import build_names, game_title
    from ..project import build_root
    stem = prof.output_stem(args.lang) if args.lang else prof.name
    current_name, args.archive_name, args.release_label = build_names(
        prof, args.lang, selected.release if selected else None)
    title = game_title(prof)
    args.release_name = f"{title} [{args.release_label}]"
    args.earlier_names = [f"{title}-{args.lang}", f"{prof.name}-{args.lang}", stem]
    args.build_root = selected.build if selected else build_root(registry.root)
    own_build = selected.root / "build" if selected else registry.root / "build"
    args.build_root_override = Path(args.build_root).resolve() != own_build.resolve()
    args.default_build = str(args.build_root / f"{current_name}.gba")
    if hasattr(args, "built") and args.built is None and args.cmd != "rom decode":
        args.built = args.default_build
    if args.cmd == "build rom" and args.out is None:
        args.out = args.default_build
    if args.cmd == "build patch":
        args.default_patch = str(args.build_root / f"{current_name}.ups")
        args.out = args.out or args.default_patch
        args.converter = args.converter or str(args.build_root / f"{stem}-save-convert.py")


def site_defaults(args, registry, explicit=()) -> None:
    """Keep shared editor paths anchored to the registry."""
    if args.cmd == "web bundle" and "out" not in explicit:
        args.out = str(registry.root / "build/editor/gen3tl-check.zip")
    if (args.cmd in ("web server", "web registry", "web site-catalog", "web assemble")
            and "data" not in explicit):
        args.data = str(registry.root / "build/editor")


def help_parsers(p, seen=None):
    """Visit every parser in the command tree once."""
    seen = set() if seen is None else seen
    if id(p) in seen:
        return
    seen.add(id(p))
    yield p
    for action in p._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                yield from help_parsers(child, seen)


def help_defaults(p, selection):
    """Resolve displayed defaults without dispatching or validating inputs."""
    from ..core.rom import RomError
    from ..project import Registry, registry_scope
    selector = StatusParser(add_help=False)
    selection_options(selector)
    args = argparse.Namespace(**{action.dest: action.default for action in p._actions
                                 if action.default != argparse.SUPPRESS})
    vars(args).update(vars(selector.parse_args(selection)))
    args.cmd = p.get_default("cmd") or ""
    try:
        registry = Registry.load(Path(args.project_root) if args.project_root else None)
        args.project_root = str(registry.root)
        args.lang = args.lang or registry.default_language
        with registry_scope(registry.root):
            if not needs_profile(args, registry):
                shared_defaults(args, registry)
                if args.cmd == "web registry" and args.out is None:
                    args.out = str(Path(args.data) / "registry.json")
                return vars(args)
            prof = Profile.by_name(args.profile or registry.default_profile or None)
            args.profile = str(prof.path.resolve())
            rom_path = prof.get("rom", {}).get("path")
            args.rom = args.rom or (str(registry.root / rom_path) if rom_path else None)
            selected = next((project for project in registry.projects
                             if (project.profile, project.language) == (prof.name, args.lang)), None)
            build_defaults(args, prof, registry, selected)
            if selected:
                project_defaults(args, selected)
                site_defaults(args, registry)
            handler_help_defaults(args, prof, registry, selected)
    except (OSError, ValueError, RomError):
        return {key: "unresolved" if value is None else value for key, value in vars(args).items()}
    return vars(args)


def handler_help_defaults(args, prof, registry, selected):
    """Describe handler fallbacks, including input-dependent output paths."""
    paths = {}
    if args.cmd == "project init":
        paths["root"] = registry.root
    if args.cmd == "discover run":
        paths["out"] = prof.path if prof.path.is_dir() else args.build_root / "discovery" / prof.name
    if args.cmd == "discover graphics":
        paths["out"] = args.build_root / "discovery" / prof.name / "graphics"
    if args.cmd == "catalog source":
        paths["out"] = catalog.source_directory(registry.root, prof.name)
    if args.cmd == "discover reach":
        paths["group"] = ["script", "system"]
    if args.cmd == "web registry":
        paths["out"] = Path(args.data) / "registry.json"
    if args.cmd in ("build font-preview", "build font-compare"):
        paths["style"] = prof.get("font_style", {}).get("style")
        pack = language.load(args.lang)
        if args.cmd == "build font-compare":
            paths["pairs"] = pack.data.get("font_pairs", "")
        else:
            paths["lines"] = pack.data.get("preview_lines", [
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz", "0123456789 !?…,."
            ])
    if args.cmd == "lang init":
        from .. import INSTALLED, TOOLKIT_ROOT
        paths["packs"] = "required for an installed toolkit" if INSTALLED else TOOLKIT_ROOT / "assets/lang"
    if args.cmd == "verify save-fixture":
        paths["game_rom"] = args.rom
    if args.cmd == "verify golden":
        paths["work_dir"] = "temporary directory"
    if selected:
        if args.cmd == "catalog extract":
            paths["out"] = selected.build / "catalog.pot"
        if args.cmd in ("catalog key-only", "catalog merge", "catalog remap"):
            paths["english"] = selected.build / "catalog.pot"
        if args.cmd == "discover flag":
            paths["graphics"] = selected.graphics
        if args.cmd == "web data":
            paths["out"] = f"{selected.build / 'editor' / (args.lang + '.json')}; --split: {selected.editor}"
        if args.cmd in ("data sprites", "data icons", "data maps"):
            from ..data.pokedextexts import default_dataset_dir
            paths["out"] = (selected.dataset or default_dataset_dir(prof.name)) / "sprites"
        if args.cmd in ("verify shots", "verify story"):
            stem = "<scenario-stem>" if args.cmd == "verify shots" else "<pairs-stem>"
            paths["out"] = selected.build / "screens" / stem
    for field, value in paths.items():
        if getattr(args, field, None) is None:
            setattr(args, field, str(value) if isinstance(value, Path) else value)


def main(argv=None, runtime=None):
    return run_status(lambda report: _main(argv, report, runtime))


def _main(argv, report, runtime=None):
    p, _ = parser()
    from ..project import ProjectError, Registry, registry_scope
    tokens = list(sys.argv[1:] if argv is None else argv)
    selection, remaining = [], []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.split("=", 1)[0] in ("--profile", "--lang", "--rom", "--project-root"):
            selection.append(token)
            if "=" not in token and i + 1 < len(tokens):
                i += 1
                selection.append(tokens[i])
        elif token == "--ignore-pin":
            selection.append(token)
        else:
            remaining.append(token)
        i += 1
    for current in help_parsers(p):
        if current is p or current.get_default("cmd"):
            current.help_defaults = lambda target: help_defaults(target, selection)
    args = p.parse_args(selection + remaining)
    args.runtime = runtime
    report.command = args.cmd
    if args.cmd == "verify profile" and (args.compare or args.set_mode or args.allow_added_prefix is not None):
        return COMMANDS[args.cmd](args)
    try:
        registry = Registry.load(Path(args.project_root) if args.project_root else None)
    except ProjectError as exc:
        p.error(exc)
    with registry_scope(registry.root):   # its alias tables and base ROMs
        return _dispatch(p, args, tokens, registry)


def _dispatch(p, args, tokens, registry):
    """Select the profile and project of the parsed command and run it."""
    from ..project import ProjectError, pin_check
    from ..core.rom import RomError
    from ..profile.profile import name_of, profile_paths
    if not needs_profile(args, registry):
        explicit = {token.split("=", 1)[0].removeprefix("--").replace("-", "_")
                    for token in tokens if token.startswith("--")}
        shared_defaults(args, registry, explicit)
        if args.project is not None and not args.ignore_pin and (pin := pin_check(args.project)):
            line("pin", pin[0], file=sys.stderr)
            if pin[1]:
                p.error(pin[0])
        return COMMANDS[args.cmd](args)
    profile_name = args.profile or registry.default_profile or None
    args.lang = args.lang or registry.default_language
    try:
        prof = Profile.by_name(profile_name)
    except RomError:
        if profile_name and Path(profile_name).name == profile_name and not profile_name.endswith(".toml"):
            require_names("profile", [profile_name], (name_of(path) for path in profile_paths()))
        raise
    args.profile = str(prof.path.resolve())
    selected = next((project for project in registry.projects
                     if (project.profile, project.language) == (prof.name, args.lang)), None)
    if selected is not None and not args.ignore_pin and (pin := pin_check(selected)):
        line("pin", pin[0], file=sys.stderr)
        if pin[1]:
            p.error(pin[0])
    if args.cmd == "verify profile":
        return COMMANDS[args.cmd](args)
    build_defaults(args, prof, registry, selected)
    problem = prof.command_problem(args.cmd, getattr(args, "built", None), getattr(args, "mode", "field"))
    if problem:
        p.error(problem)
    if args.cmd == "lang init":
        args.registry = registry
        return COMMANDS[args.cmd](args)
    if (args.cmd in ("rom identify", "rom evidence", "discover run", "discover candidates", "discover graphics",
                     "catalog source", "discover check", "discover convert", "project init")
            or args.cmd == "rom decode" and not args.built):
        return COMMANDS[args.cmd](args)
    try:
        if selected is None:
            p.error(CommandError(f"unconfigured translation project {prof.name}/{args.lang}",
                                 hint="language: registered project", valid=sorted({project.language
                                     for project in registry.projects if project.profile == prof.name})))
        project = registry.resolve(prof, args.lang)
        prof = project.effective(prof)
        args.project = project
        project_defaults(args, project)
        if (args.cmd.startswith("catalog batch-") or args.cmd in ("check batch", "check render")) and hasattr(args, "name"):
            if Path(args.name).name != args.name or args.name in (".", ".."):
                raise ProjectError("batch name must identify one project-local directory")
            if args.cmd != "catalog batch-export":
                require_names("batch", [args.name],
                              (path.parent.name for path in project.batches.glob("*/source.json")))
        if args.cmd == "check batch":
            batch_root = project.batches / args.name
            if not (batch_root / args.file).resolve().is_relative_to(batch_root.resolve()):
                raise ProjectError("batch check file must remain inside its batch")
        explicit = {token.split("=", 1)[0].removeprefix("--").replace("-", "_")
                    for token in tokens if token.startswith("--")}
        path_fields = {"rom", "out", "built", "po_root", "glossary", "rules", "index", "defer",
                       "plan", "report", "data", "notes", "converter", "research", "game_rom",
                       "from_rom", "to_rom", "base_save", "translated_save",
                       "snapshot", "patch_file", "replacement_glossary", "work_dir",
                       "inventory", "context", "missing", "english", "graphics", "flags"}
        positional = {"save", "scenario", "table", "png", "export_file"}
        if args.cmd == "verify story":
            positional.add("pairs")
        if args.cmd == "verify save-convert":
            positional.add("out")
        if args.cmd == "catalog batch-export":
            path_fields.add("file")
        if args.cmd == "catalog merge":   # read only, possibly another project's
            args.catalog, args.source = (str(Path(value).resolve()) for value in (args.catalog, args.source))
        if args.cmd == "catalog remap":   # explicit scope, possibly several projects'
            args.catalog, args.source = (str(Path(value).resolve()) for value in (args.catalog, args.source))
            for field in ("translation", "reference", "profile_file"):
                setattr(args, field, [str(Path(value).resolve()) for value in (getattr(args, field) or [])])
        paths = [project.translation, project.build, project.catalog, project.batches]
        for field in path_fields | positional:
            value = getattr(args, field, None)
            if not value or field not in explicit | positional:
                continue
            resolved = [str(Path(v).resolve()) for v in value] if isinstance(value, list) else str(Path(value).resolve())
            setattr(args, field, resolved)
            paths.extend(resolved if isinstance(resolved, list) else [resolved])
        registry.protect(project, paths)
        site_defaults(args, registry, explicit)
        catalog = Path(args.po_root) if hasattr(args, "po_root") else project.catalog
        if not catalog.is_absolute():
            catalog = project.root / catalog
        project.validate(prof, catalog)
        if getattr(args, "built", None):
            built_path = Path(args.built)
            if not built_path.is_absolute():
                built_path = project.root / built_path
            project.validate_built(prof, built_path)
    except CommandError:
        raise
    except (ProjectError, ValueError) as exc:
        p.error(exc)
    with project.activate(prof):
        return COMMANDS[args.cmd](args)
