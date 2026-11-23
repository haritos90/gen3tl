"""Editor data, snapshot and bundle, local servers, editor imports and submissions, the site registry, catalog and tree."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import TOOLKIT_ROOT
from ..profile.profile import Profile
from .common import (_context_records, _font_codes, _limits_and_matcher, _shadow_end, _share_limits, _shared_limits,
                     _style_measures, _style_shadow_ends, _style_widths, _window_measures, add, command, line,
                     write_options)


@command("web data")
def editor_data(args):
    """Write the editor data of a language: build/editor/<lang>.json."""
    import json
    from collections import Counter
    from pathlib import Path
    from ..core.terminal import table
    from ..discover import inventory as inv_mod
    from ..check import qa as qa_mod
    from ..lang import termbase
    from ..catalog.batch import record_speakers
    from ..catalog.catalog import load_catalog
    from ..web.editordata import build as build_data
    from ..web.editordata import toolkit_version
    from ..build.font import FontTable
    from ..lang.langpack import LangPack, load_styles
    from ..check.layout import Measure
    prof = Profile.by_name(args.profile)
    if prof.get("support", {}).get("stage") == "catalog":
        from ..catalog.draftcatalog import editor_data as draft_data
        from ..web.editordata import split, write_languages, write_split
        manifest = json.loads((args.project.translation / "catalog.json").read_text())
        doc = draft_data(prof, args.lang, load_catalog(Path(args.po_root)), manifest,
                         note_root=args.project.translation)
        if args.split:
            from ..data.pokedextexts import write as write_texts
            index, chunks = split(doc, None, {})
            out = Path(args.out) if args.out else args.project.editor
            write_split(out, index, chunks)
            write_texts(out, index, page_catalog=args.project.translation / "pokedex-labels.po",
                        aliases_file=args.project.translation / "pokedex-aliases.toml")
            root = out.parent if args.out else args.project.registry_root / "build/editor"
            if not args.language_only:
                write_languages(root)
        else:
            out = Path(args.out) if args.out else args.project.build / "editor" / f"{args.lang}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")))
        line("wrote", out)
        line("entries", f"{len(doc['entries'])} entries")
        line("support", "unverified")
        line("status", "written")
        return 0
    from ..web import editorsnapshot
    snapshot = source = None
    if args.no_rom:
        snapshot = editorsnapshot.load(Path(args.snapshot))
        catalog = load_catalog(Path(args.po_root), english=False)   # keyed without build/catalog.pot
        editorsnapshot.validate(snapshot, prof, args.lang, catalog, Path(args.po_root))
        inv, ctx, reach_doc, facts = editorsnapshot.inputs(snapshot, catalog, prof)
        measure, cm, measures = editorsnapshot.measures(prof, args.lang, snapshot["fonts"])
        if any(e.keyed for _, e in catalog.values()):   # the snapshot's facts stand for the English
            source = snapshot["source"]
            limits = editorsnapshot.limits(snapshot, catalog, prof)
            line("english", "snapshot facts")
        else:
            limits = qa_mod.compute_limits(prof, inv, catalog, measure, cm, ctx, facts=facts)
        windows = qa_mod.Windows(prof, inv, measures)
        notes = snapshot["notes"]
        cursor = snapshot["records"]["cursor"]
    else:
        rom = prof.load_rom(args.rom)
        font = FontTable.from_profile(prof)[0]
        pack = LangPack.load(args.lang, prof["font_style"]["style"])
        slots = pack.glyph_slots(rom, font)
        cm = pack.charmap()
        # the measure and limits of the qa gate
        measure = Measure(lambda b: slots[b].width if b in slots else font.width(rom, b),
                          prof.get("layout", {}).get("placeholders", {}), styles=_style_widths(prof, rom, args.lang),
                          shadow_end=_shadow_end(slots, font, rom), shadow_styles=_style_shadow_ends(prof, rom, args.lang),
                          fonts=_font_codes(prof))
        inv = inv_mod.load(args.project.build / "inventory.json")
        from ..catalog import extract
        catalog = load_catalog(Path(args.po_root), source={}, english=False)
        extract.from_rom(catalog, prof, rom, args.project)   # a key-only catalog's English, from this ROM
        ctx = _context_records()
        prepared = {"profile": prof, "rom": rom, "charmap": cm, "measure": measure, "inventory": inv,
                    "catalog": catalog, "context": ctx, "english_slots": False}
        shared = _shared_limits(args, prepared)
        if shared is None:
            prepared["limits"] = qa_mod.compute_limits(prof, inv, catalog, measure, cm, ctx, rom=rom)
            _share_limits(args, prepared)
        else:
            prepared = shared
        limits = prepared["limits"]
        windows = qa_mod.Windows(prof, inv, _window_measures(prof, rom, args.lang))
        rules = prof.get("context", {}).get("record_speakers", [])
        notes = record_speakers(rules, rom, inv) if rules else {}
        from ..web.editorkeyboard import cursor_columns
        cursor = cursor_columns(prof, rom)
    g = Path(args.glossary)
    terms = termbase.load(g) if g.exists() else None
    packs = load_styles(args.lang, prof)
    toolkit = toolkit_version()
    if snapshot is None:
        reach_path = args.project.build / "reach.json"
        reach_doc = json.loads(reach_path.read_text(encoding="utf-8")) if reach_path.exists() else None
        if reach_doc is None:
            line("missing", reach_path)
            line("reach", "catalog")
    from ..check import rules as rules_mod
    wording_rows = rules_mod.load_list(args.rules, code=args.lang)
    wording = rules_mod.wording_rules(wording_rows, catalog)
    from ..check import namepairs
    tokens = {sid: row[1] for sid, row in (source or {}).items()}
    doc = build_data(args.lang, prof, catalog, limits, measure, cm, packs, terms, notes, windows, toolkit,
                     reach_doc, rules_mod.profile_rules(prof, inv, catalog, limits, tokens), wording, inv=inv,
                     pokedex=namepairs.dataset(prof), cursor=cursor, source=source, contexts=ctx,
                     note_root=args.project.translation, wording_rows=wording_rows,
                     fill_facts=snapshot["records"] if snapshot else
                     {"code_originals": qa_mod.fill_originals(prof, catalog, rom, inv=inv)})
    from ..build import release
    doc["version"] = release.load()
    n = Counter(x["status"] for x in doc["entries"])
    line("entries", f"{len(doc['entries'])} entries")
    table(("state", "entries"), sorted(n.items()))
    line("terms", f"{len(doc['glossary'])} terms")
    line("unmeasured", f"{sum(1 for x in doc['entries'] if not x['window'])} entries")
    reach_n = Counter(x["reach"]["class"] for x in doc["entries"])
    table(("reach", "entries"), sorted(reach_n.items()))
    # entries with rules, wording rules
    line("rules", f"{sum(1 for x in doc['entries'] if x['rules'])} entries")
    line("wording", f"{sum(len(v) for v in wording.values())} rules")
    line("source", args.rules)
    if not args.split:
        out = Path(args.out) if args.out else args.project.build / "editor" / f"{args.lang}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        line("wrote", out)
        line("size", f"{out.stat().st_size} bytes")
        line("status", "written")
        return
    # the split form: the check's problems at data time
    from ..build import release
    from ..web.editorcheck import RULES, Checker, latin_cells, latin_widths
    from ..web.editordata import data_problems, glyph_art, keyed_rules, split, write_languages, write_split
    from ..catalog import keys
    qa_rules = keyed_rules(prof.get("qa", {}), keys.for_profile(prof))   # the entries carry keys
    if snapshot is not None:
        checker = editorsnapshot.checker(doc, snapshot["fonts"], qa_rules, release.load())
    else:
        checker = Checker(doc, latin_widths(doc["fonts"], bytes(rom.data), prof.sha1),
                          {k: qa_rules[k] for k in RULES if k in qa_rules}, release.load(),
                          latin_cells(doc["fonts"], bytes(rom.data)))
    english = {keys.for_profile(prof).key(sid): e.msgid for sid, (_, e) in catalog.items() if not e.keyed}
    problems = data_problems(doc, checker, english)
    from ..web.editordata import terms_data
    tdata = terms_data(terms, doc, catalog, source) if terms else None
    index, chunks = split(doc, release.load(), glyph_art(prof, packs), problems, tdata)
    out = Path(args.out) if args.out else args.project.editor
    # reads English from the user's ROM in the browser
    from ..web.editordata import source_data
    if snapshot is None and args.project.snapshot.is_file():
        snapshot = editorsnapshot.load(args.project.snapshot)
    write_split(out, index, chunks, tdata, source=source_data(prof, catalog, inv, snapshot))
    from ..web.editordata import write_keys
    if not args.language_only:
        write_keys(out.parent, prof)
    from ..data.pokedextexts import write as write_texts
    # the Pokedex texts read the English beside the rows
    named = {**index, "list": [{**row, "source_text": english.get(row["id"], "")} for row in index["list"]]}
    texts = write_texts(out, named, page_catalog=args.project.translation / "pokedex-labels.po",
                        aliases_file=None if source else args.project.translation / "pokedex-aliases.toml")
    if texts:
        line("texts", texts)
        line("size", f"{texts.stat().st_size} bytes")
    if tdata:
        used = sum(1 for t in tdata["terms"] if t["in_source"] or t["in_translation"])
        line("terms", f"{len(tdata['terms'])} terms")
        line("used", f"{used} terms")
        line("wrote", out / "terms.json")
    manifest_root = out.parent if args.out else args.project.registry_root / "build/editor"
    if not args.language_only:
        write_languages(manifest_root, toolkit=toolkit)
    line("problems", f"{sum(1 for p in problems.values() if p)} entries")
    line("wrote", out / "index.json")
    line("size", f"{(out / 'index.json').stat().st_size} bytes")
    line("chunks", f"{len(chunks)} chunks")
    if not args.language_only:
        line("manifest", manifest_root / "languages.json")
    line("status", "written")


@command("web snapshot")
def editor_snapshot(args):
    """Extract or verify ROM-free editor measurements for the selected language."""
    import json
    from pathlib import Path
    from ..web import editorsnapshot
    from ..catalog.catalog import load_catalog
    from ..lang import termbase
    prof = Profile.by_name(args.profile)
    rom = prof.load_rom(args.rom)
    documents = [json.loads((args.project.build / f"{name}.json").read_text(encoding="utf-8"))
                 for name in ("inventory", "context", "reach")]
    glossary = args.project.glossary
    terms = termbase.load(glossary) if glossary.is_file() else None
    doc = editorsnapshot.extract(prof, args.lang, rom, *documents,
                                 load_catalog(Path(args.po_root)), Path(args.po_root), terms)
    out = Path(args.out)
    if args.check:
        if editorsnapshot.load(out) != doc:
            raise ValueError(f"{out}: differs from fresh ROM measurements; regenerate web snapshot")
        line("snapshot", out)
        line("verified", "passed")
    else:
        editorsnapshot.write(out, doc)
        for path in editorsnapshot.paths(out):
            line("wrote", path)
        line("records", f"{len(doc['inventory'])} records")


@command("web bundle")
def editor_bundle(args):
    """Write the editor's layout check for the browser: build/editor/gen3tl-check.zip."""
    from pathlib import Path
    from ..web.editorcheck import RULES, write_bundle
    prof = Profile.by_name(args.profile)
    rules = prof.get("qa", {})
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    names = write_bundle(out, {k: rules[k] for k in RULES if k in rules}, profile=Profile.load(prof.path))
    for name in names:
        line("file", name)
    line("wrote", out)
    line("size", f"{out.stat().st_size} bytes")


def editor_preview(args):
    """Serve the site (web/) with the editor data (build/editor/) on this machine."""
    from pathlib import Path
    from ..data.pokedextexts import FILE
    from ..web.editordata import write_languages, write_releases
    from ..web.editorserve import WEB, preview_server
    data = Path(args.data)
    try:
        # Release previews contain no language exports.
        languages = any(data.glob("*/*/index.json")) or any(data.glob(f"*/*/{FILE}"))
        if languages and _prepare_editor_languages(args, data, include_selected=False):
            write_languages(data)
        write_releases(data)
    except (OSError, ValueError) as exc:
        line("error", exc)
        return 1
    if not any((data / name).exists() for name in ("languages.json", "releases/releases.json")):
        line("data", "missing")
        return 1
    server = preview_server(WEB, data, args.port)
    site = f"http://localhost:{server.server_address[1]}/"
    line("site", site)
    line("editor", f"{site}editor")
    line("status", "ready")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


@command("web fetch-patches")
def site_patches(args):
    """Fetch the offered release assets into the project's ignored store."""
    from ..web.patcher import fetch_patches, patch_store
    count = fetch_patches(args.project.releases, args.repository)
    line("patches", count)
    line("store", patch_store(args.project.releases))
    line("status", "verified")
    return 0


@command("web assemble")
def site_assemble(args):
    """Write a static site into one empty directory: a game site with its editor data, or the application alone."""
    import json
    import os
    import shutil
    from .. import __version__
    from ..web.editordata import validate_registry
    from ..web.editorserve import WEB
    from ..web.siteassets import asset_plan, carry_versions, rewrite_pages, write_build, write_manifest
    from ..web.sitepreview import generate as site_previews
    from ..web import sitecatalog, siteshell
    registry_file, previous = getattr(args, "registry_file", None), getattr(args, "previous", None)
    data = None if registry_file else Path(args.data).resolve()
    out, web = Path(args.out or "site").resolve(), WEB.resolve()
    try:
        from ..web.sitepreview import public_url
        public_url(getattr(args, "public_base", None))
        public_url(getattr(args, "application", None))
        registry = validate_registry(json.loads(Path(registry_file).read_text(encoding="utf-8"))) if registry_file else None
    except (OSError, ValueError) as exc:
        line("error", exc)
        return 1
    if (web / "fragments").is_dir():
        _, problems = siteshell.pages(web)
        if problems:
            for path, reason in problems:
                line("page", f"{path.name}: {reason}")
            return 1
    if data is not None and not (data / "languages.json").is_file():
        line("data", "missing")
        return 1
    if any(out == path or out.is_relative_to(path) or path.is_relative_to(out) for path in (data, web) if path is not None):
        line("error", f"{out}: overlaps the pages or the data")
        return 1
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        line("error", f"{out}: not an empty directory")
        return 1
    if data is not None:
        try:
            manifest = json.loads((data / "languages.json").read_text(encoding="utf-8"))
            roots = {Path("languages.json"), Path("registry.json"), Path(manifest["bundle"]), Path("site"), Path("releases")}
            roots.update(Path(profile["id"]) for profile in manifest["profiles"])
            roots.update(Path(language["index"]).parent for language in manifest["languages"])
            for profile in manifest["profiles"]:
                if profile.get("pokedex"):
                    roots.add(Path(profile["pokedex"]).parent)
                if profile.get("source_catalog"):
                    roots.add(Path(profile["source_catalog"]["manifest"]).parent)
        except (OSError, ValueError, KeyError) as exc:
            line("error", exc)
            return 1

    def wanted(path, ancestors=False):
        relative = path.relative_to(data)
        return any(relative == root or relative.is_relative_to(root)
                   or (ancestors and root.is_relative_to(relative)) for root in roots)

    # web/data holds toolkit datasets; editor data replaces it
    plan = []
    for top, dirs, names in os.walk(web):
        here = Path(top)
        dirs[:] = sorted(d for d in dirs if not d.startswith(".")
                         and not (here == web and d in {"data", "test", "fragments"}))
        # hidden files are local metadata
        plan += [(here / name, out / (here / name).relative_to(web))
                 for name in [*dirs, *sorted(names)] if not name.startswith(".")]
    page_plan = plan
    plan = []
    for top, dirs, names in os.walk(data) if data is not None else ():
        here = Path(top)
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and wanted(here / d, ancestors=True))
        plan += [(here / name, out / "data" / (here / name).relative_to(data))
                 for name in [*dirs, *sorted(names)] if not name.startswith(".") and wanted(here / name)]
    links = [path for path, _ in [*page_plan, *plan] if path.is_symlink()]
    if links:
        line("error", f"{links[0]}: a link")
        return 1
    try:
        pages, targets = asset_plan(page_plan, web, out, __version__)
    except (OSError, ValueError) as exc:
        line("error", exc)
        return 1
    plan = [*pages, *plan]
    size = 0
    for path, destination in plan:
        if path.is_dir():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        size += destination.stat().st_size
    if registry is not None:
        sitecatalog.write_interface(out / "data")
        (out / "data/registry.json").write_text(json.dumps(registry, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if (web / "fragments").is_dir():
        licenses = siteshell.registry_licenses(out / "data")
        for path in sorted(out.glob("*.html")):
            path.write_text(siteshell.rewrite(path.read_text(encoding="utf-8"), path.name, web, licenses),
                            encoding="utf-8")
    if data is not None and getattr(args, "public_base", None):
        from ..web.pokedexshare import generate
        try:
            shares = generate(data, out, args.public_base, application=getattr(args, "application", None))
        except (OSError, ValueError, KeyError) as exc:
            line("error", exc)
            return 1
        for share in shares:
            profile = next(row for row in manifest["profiles"] if row["id"] == share["profile"])
            profile["share_pages"] = share["share_pages"]
            line("share", share["profile"])
            line("pages", f"{share['page_count']} pages")
            line("images", f"{share['image_count']} images")
        (out / "data/languages.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n",
                                                encoding="utf-8")
    try:
        site_previews(web, out, getattr(args, "public_base", None),
                      image_dir=f"assets/{__version__}/previews" if registry is not None else "share/assets")
        sitecatalog.list_catalogs(out)
        rewrite_pages(out, targets)
        write_manifest(out, __version__)
        kept, skipped = carry_versions(str(previous), out, __version__) if previous else ([], [])
        identity = write_build(out, __version__, [version for version, _ in kept])
    except (OSError, ValueError) as exc:
        line("error", exc)
        return 1
    line("wrote", out)
    line("assets", f"assets/{__version__}")
    for version, count in kept:
        line("kept", f"{version} {count} files")
    for version, reason in skipped:
        line("skipped", f"{version} {reason}")
    if previous and not kept:
        line("kept", "none")
    line("build", identity)
    line("files", f"{sum(1 for path, _ in plan if not path.is_dir())} files")
    line("size", f"{size} bytes")
    return 0


@command("web registry")
def site_registry(args):
    """Write the site registry of an editor data directory: every game, or one game project."""
    import json
    from pathlib import Path
    from ..web.editordata import registry_of, validate_registry
    data = Path(args.data)
    if not (data / "languages.json").is_file():
        line("data", "missing")
        return 1
    doc = json.loads((data / "languages.json").read_text(encoding="utf-8"))
    registry = registry_of(doc, args.data_url, args.game or None, args.repository)
    out = Path(args.out) if args.out else data / "registry.json"
    if out.is_file():
        previous = validate_registry(json.loads(out.read_text(encoding="utf-8")))
        if "include" in previous:
            registry["include"] = previous["include"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(registry, ensure_ascii=False, indent=1), encoding="utf-8")
    line("wrote", out)
    line("games", f"{len(registry['games'])} games")
    line("default", registry.get("default", "none"))
    return 0


FIXTURES = TOOLKIT_ROOT / "tests" / "fixtures" / "sample-hack"   # in a checkout only


def _fixture_data(data: Path) -> list[str]:
    """Write the editor data of the toolkit's catalog-stage fixture workspaces under `data`; their keys."""
    import json
    from ..catalog.catalog import load_catalog
    from ..catalog.draftcatalog import editor_data as draft_data
    from ..data.pokedextexts import write as write_texts
    from ..project import Registry, registry_scope
    from ..web.editordata import split, write_split
    if not (FIXTURES / "projects.toml").is_file():
        return []
    registry = Registry.load(FIXTURES)
    written = []
    with registry_scope(registry.root):
        for project in registry.projects:
            prof = project.load_profile()
            if prof.get("support", {}).get("stage") != "catalog":
                continue
            prof = project.effective(prof)
            project.validate(prof)
            with project.activate(prof):
                manifest = json.loads((project.translation / "catalog.json").read_text(encoding="utf-8"))
                doc = draft_data(prof, project.language, load_catalog(project.catalog), manifest)
                index, chunks = split(doc, None, {})
                out = data / project.profile / project.language
                write_split(out, index, chunks)
                write_texts(out, index, page_catalog=project.translation / "pokedex-labels.po",
                            aliases_file=project.translation / "pokedex-aliases.toml")
            written.append(project.key)
    return written


@command("web site-shell")
def site_shell(args):
    """Check shared page markup, or synchronize it from its fragments."""
    from ..web import siteshell
    changed, problems = siteshell.pages(Path(args.web), sync=args.sync)
    for path, reason in problems:
        line("page", f"{path.name}: {reason}")
    if problems:
        return 1
    for path in changed:
        line("synced", path)
    line("site shell", "verified")
    return 0


@command("web site-catalog")
def site_catalog(args):
    """Write the site interface catalogs into an editor data directory."""
    from ..web import sitecatalog
    from ..web.editordata import write_languages
    data = Path(args.data)
    if args.approve is not None or args.approve_all is not None:
        if args.approve is not None and args.approve_all is not None:
            line("error", "name either --approve or --approve-all")
            return 1
        if args.approve is not None:
            code, keys = args.approve[0], args.approve[1:]
            if not keys:
                line("error", "--approve names a language code and at least one key")
                return 1
        else:
            code, keys = args.approve_all, None
        try:
            approved = sitecatalog.approve(code, keys)
        except (OSError, ValueError) as exc:
            line("error", exc)
            return 1
        path = approved.path
        line("catalog", path.relative_to(sitecatalog.WEB).as_posix() if path.is_relative_to(sitecatalog.WEB) else path)
        line("approved", f"{approved.cleared} entries")
    if args.sync:
        for synced in sitecatalog.sync():
            line("synced", synced.path)
            line("added", f"{len(synced.added)} keys")
            line("dropped", f"{len(synced.dropped)} keys")
    if args.project is None:   # the toolkit's own data and its fixture workspaces
        for key in _fixture_data(data.resolve()):
            line("project", key)
        write_languages(data)
        line("manifest", data / "languages.json")
    elif (data / "languages.json").exists():
        write_languages(data)   # lists the editor project beside the games
    else:
        sitecatalog.write(data)
    for code, path in sitecatalog.catalogs().items():
        entries = sitecatalog.load(path)
        missing = sum(1 for e in entries if not e.msgstr)
        translated = sum(1 for e in entries if e.msgstr and e.fuzzy)
        line("wrote", data / sitecatalog.SITE / f"{code}.json")
        line("keys", f"{len(entries)} keys")
        line("untranslated", f"{missing} entries")
        line("translated", f"{translated} entries")
    line("wrote", data / sitecatalog.SITE / "catalogs.json")
    return 0


def _editor_refresh(project, prof, lang, toolkit, ignore_pin=False, parts=None):
    """Bind regeneration and its inputs to one language project; `parts`: the served game parts."""
    import subprocess
    import tempfile
    import shutil
    from ..web.editordata import editor_inputs
    from ..web.editorsave import STAMP, data_key
    from ..web.editorstore import PARTS, copy_parts
    inputs = editor_inputs(project, prof, lang)

    def refresh(catalog, destination):
        project.build.mkdir(parents=True, exist_ok=True)
        key = data_key(catalog, prof.path, toolkit, inputs)
        with tempfile.TemporaryDirectory(prefix=".editor-refresh-", dir=project.build) as tmp:
            generated = Path(tmp) / "view"
            command = [sys.executable, "-m", "gen3tl", "--project-root", str(project.registry_root),
                       "--profile", str(prof.path.resolve()), "--lang", lang, "web", "data",
                       "--po-root", str(catalog), "--no-rom", "--split", "--language-only",
                       "--out", str(generated)]
            if ignore_pin:
                command.append("--ignore-pin")
            result = subprocess.run(command, cwd=project.registry_root, capture_output=True, text=True)
            if result.returncode:
                raise ValueError("editor refresh failed: " + (result.stderr or result.stdout)[-1500:])
            # the parts stay served beside the published language
            copy_parts(Path(tmp) / PARTS, parts or Path(destination).parent / PARTS)
            shutil.copytree(generated, destination, dirs_exist_ok=True)
            (destination / STAMP).write_text(key, encoding="utf-8")
    return refresh, inputs


def _prepare_editor_languages(args, data, *, include_selected=True):
    """Refresh every served project before opening the listener."""
    import json
    import threading
    from ..data.pokedextexts import FILE, tracked_dataset
    from ..project import REGISTRY, Registry
    from ..web.editordata import SCHEMA, text_projects, toolkit_fingerprint, write_keys
    from ..web.editorsave import STAMP, background_refresh, data_key
    from ..web.editorstore import PARTS

    selected = args.project
    if selected and not (selected.registry_root / REGISTRY).is_file():
        projects = [(selected, args.profile, args.lang, Path(args.po_root).resolve())]
    else:
        registry = Registry.load(selected.registry_root) if selected else Registry.load()
        needed = {project.key for project in registry.projects
                  if any((data / project.key / name).is_file() for name in ("index.json", FILE))}
        if include_selected:
            needed.add(selected.key)
        for name, project in text_projects(registry).items():
            if any(key.startswith(name + "/") for key in needed) and tracked_dataset(name) is not None:
                needed.add(project.key)
        projects = [(project,
                     args.profile if selected and selected.profile == project.profile
                     else str(project.load_profile().path),
                     project.language, Path(args.po_root).resolve() if project == selected else project.catalog)
                    for project in registry.projects if project.key in needed]
    toolkit = toolkit_fingerprint()
    prepared = {}
    for project, profile, lang, catalog in projects:
        prof = Profile.by_name(profile)
        project_key = f"{prof.name}/{lang}"
        target = data / project_key
        refresh, inputs = _editor_refresh(project, prof, lang, toolkit, getattr(args, "ignore_pin", False),
                                          target.parent / PARTS)
        stamp = target / STAMP
        key = lambda: data_key(catalog, prof.path, toolkit, inputs)   # noqa: E731
        index = target / "index.json"
        have = index.is_file() and json.loads(index.read_text(encoding="utf-8")).get("schema") == SCHEMA
        texts = tracked_dataset(prof.name) is None or (target / FILE).is_file()
        current = have and texts and stamp.is_file() and stamp.read_text(encoding="utf-8") == key()
        line("project", project_key)
        if current:
            line("data", "current")
        else:
            line("data", "stale" if have else "missing")
            line("data", "regenerating" if have else "generating")
            project.build.mkdir(parents=True, exist_ok=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            if not background_refresh(refresh, catalog, target, key, threading.Lock(), project.build,
                                      success="regenerated" if have else "generated"):
                raise ValueError(f"{project_key}: stale editor data")
        write_keys(target.parent, prof)
        prepared[project_key] = refresh
    return prepared


def editor(args):
    """Serve the selected catalog with explicit local text saves."""
    import time
    from ..web.editorcheck import RULES, bundle_current, write_bundle
    from ..web.editordata import write_languages
    from ..web.editorsave import DraftStore, local_server
    from ..web.editorserve import WEB

    started = time.monotonic()
    project = args.project
    prof = Profile.by_name(args.profile)
    data = Path(args.data).resolve()
    target = data / prof.name / args.lang
    refresh = _prepare_editor_languages(args, data)[f"{prof.name}/{args.lang}"]
    write_languages(data)
    line("manifest", data / "languages.json")
    bundle = data / "gen3tl-check.zip"
    if not bundle_current(bundle):
        rules = prof.get("qa", {})
        write_bundle(bundle, {k: rules[k] for k in RULES if k in rules}, profile=Profile.load(prof.path))
        line("bundle", "generated")
    store = DraftStore(args.po_root, target, prof.name, args.lang, prof.sha1, refresh, project.build,
                       effective_profile=prof, project=project)
    server = local_server(WEB, data, store, args.port)
    site = f"http://127.0.0.1:{server.server_address[1]}/"
    line("site", site)
    line("editor", f"{site}editor")
    line("ready", f"{time.monotonic() - started:.1f} s")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _alive(pid: int) -> bool:
    """Whether a process runs; a finished child of this process is reaped."""
    import os
    try:
        if os.waitpid(pid, os.WNOHANG)[0]:
            return False
    except ChildProcessError:   # not a child of this process
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:   # another user's process
        return True
    return True


def _server_process(pid: int, port: int, page: str) -> bool:
    """Whether a live process is the selected server on this port."""
    import subprocess
    try:
        result = subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                                capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return False
    words = result.stdout.split()
    expected_port = ["--port", str(port)]
    expected_page = ["--page", page]
    port_matches = any(words[index:index + 2] == expected_port for index in range(len(words) - 1))
    page_matches = any(words[index:index + 2] == expected_page for index in range(len(words) - 1))
    server_matches = "web server" in result.stdout and "start" in words and page_matches
    return result.returncode == 0 and "gen3tl" in result.stdout and port_matches and server_matches


def _server_pid(pid_file: Path, port: int, page: str = "editor") -> int | None:
    """The live selected process a pid file names; stale files are removed."""
    try:
        pid = int(pid_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except ValueError:
        pid = 0
    if pid > 0 and _alive(pid) and _server_process(pid, port, page):
        return pid
    pid_file.unlink(missing_ok=True)
    return None


def _stop_server(pid_file: Path, port: int, page: str = "editor") -> int:
    """End the recorded server with SIGTERM and wait for its exit."""
    import os
    import signal
    import time
    pid = _server_pid(pid_file, port, page)
    if pid is None:
        line("status", "not running")
        return 0
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 10
    while _alive(pid):
        if time.monotonic() > deadline:
            line("status", "running")
            line("pid", pid)
            return 1
        time.sleep(0.1)
    pid_file.unlink(missing_ok=True)
    line("status", "stopped")
    return 0


def _start_server(args, pid_file: Path, log: Path) -> int:
    """Spawn the selected server and wait until its page answers."""
    import socket
    import subprocess
    import time
    from http.client import HTTPException
    from urllib.request import ProxyHandler, build_opener
    pid = _server_pid(pid_file, args.port, args.page)
    if pid is not None:
        line("status", "running")
        line("pid", pid)
        return 1
    try:
        socket.create_connection(("127.0.0.1", args.port), timeout=1).close()
    except OSError:
        pass
    else:   # another process answers on the port
        line("status", "in use")
        line("port", args.port)
        return 1
    root = args.project.registry_root if args.project is not None else Path(args.build_root).parent
    command = [sys.executable, "-m", "gen3tl"]
    if (root / "projects.toml").is_file():
        command += ["--project-root", str(root)]
    for option, value in (("--profile", args.profile), ("--lang", args.lang)):
        if value:
            command += [option, value]
    command += ["web", "server", "start", "--page", args.page, "--foreground",
                "--port", str(args.port), "--data", args.data]
    if args.page == "editor":
        command += ["--po-root", args.po_root]
    if getattr(args, "ignore_pin", False):
        command.append("--ignore-pin")
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    if log.exists():
        log.replace(log.with_name(log.name + ".1"))
    with log.open("wb") as output:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                   start_new_session=True)   # outlives the terminal
    pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
    site = f"http://127.0.0.1:{args.port}/"
    urlopen = build_opener(ProxyHandler({})).open   # the local server, never a proxy
    started = time.monotonic()
    deadline = started + args.wait
    while process.poll() is None:
        try:
            with urlopen(site, timeout=1) as page:
                answered = page.status == 200
        except (OSError, HTTPException):
            answered = False
        if answered:
            for label, value in (("status", "started"), ("pid", process.pid), ("site", site),
                                 ("editor", f"{site}editor"), ("log", log)):
                line(label, value)
            return 0
        if time.monotonic() > deadline:
            break
        time.sleep(0.25)
    if process.poll() is None:
        line("status", "starting")
        line("waited", f"{time.monotonic() - started:.1f} s")
        line("pid", process.pid)
    else:
        pid_file.unlink(missing_ok=True)
        line("status", "exited")
    line("log", log)
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    line("last", next((text for text in reversed(lines) if text.strip()), "empty"))
    return 1


@command("web server")
def server(args):
    """Control a detached site or editor server: start, stop, restart, status or log."""
    if args.foreground:
        return editor_preview(args) if args.page == "site" else editor(args)
    pid_file = Path(args.build_root) / f"{args.page}-server-{args.port}.pid"
    log = pid_file.with_suffix(".log")
    if args.action == "status":
        pid = _server_pid(pid_file, args.port, args.page)
        line("status", "not running" if pid is None else "running")
        if pid is not None:
            line("pid", pid)
        line("port", args.port)
        if pid is not None:
            site = f"http://127.0.0.1:{args.port}/"
            line("site", site)
            line("editor", f"{site}editor")
        return 0
    if args.action == "log":
        if not log.is_file():
            line("log", "missing")
            return 1
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        for text in lines[max(len(lines) - args.lines, 0):]:
            print(text)
        return 0
    if args.action in ("stop", "restart"):
        code = _stop_server(pid_file, args.port, args.page)
        if code or args.action == "stop":
            return code
    return _start_server(args, pid_file, log)


def _pack_import(args, prof) -> int:
    """Apply the pack edits of an export: glyph art and the keyboard layout."""
    from pathlib import Path
    from ..build import keyboard
    from ..lang.langpack import TOOLKIT_ROOT
    from ..web.editorfonts import apply_glyph_edits, glyph_edits
    from ..web.editorimport import read_export
    try:
        data = read_export(Path(args.export_file), prof.name, args.lang, prof.sha1, {}, graphics_profile=prof)
        glyphs = glyph_edits(data["document"].get("glyphs", []), prof, args.lang)
        edit = data["document"].get("keyboard")
        planned = None
        if edit is not None:
            base = prof.get("keyboard") or {}
            if not base:
                raise ValueError("the profile declares no language-pack keyboard")
            # the pack holds one layout beside its charmap
            planned = keyboard.apply_edit(args.lang, "keyboard.toml", base, edit, None, write=False)
        written = apply_glyph_edits(glyphs, args.dry_run)
        if edit is not None and not args.dry_run:
            planned = keyboard.apply_edit(args.lang, "keyboard.toml", base, edit, None)
    except (OSError, ValueError, KeyError) as exc:
        line("error", exc)
        return 1
    if edit is not None:
        line("keyboard", planned["path"])
        line("status", "planned" if args.dry_run else "applied")
        line("rows", f"{len(planned['rows'])} rows")
        line("pages", "reordered" if planned["pages"] else "unchanged")
    for g in glyphs:
        line("glyph", g.label)
        line("style", g.style)
        line("status", ("planned" if args.dry_run else "applied") if g.changed else "unchanged")
        if g.copy is not None:
            line("source", f"copy:{g.copy:02X} -> glyph")
    for path in written:
        line("file", path.relative_to(TOOLKIT_ROOT).as_posix() if path.is_relative_to(TOOLKIT_ROOT) else path)
    line("catalog", "unchanged")
    line("review", "required")
    return 0


@command("web import")
def editor_import(args):
    """Import editor text, note, glyph and keyboard drafts; apply a site export to its catalog."""
    from pathlib import Path
    from ..build import keyboard
    from ..catalog.batch import record_speakers
    from ..catalog.catalog import load_catalog
    from ..lang.langpack import TOOLKIT_ROOT
    from ..web import notechanges, sitecatalog
    from ..web.editorfonts import apply_glyph_edits, edited_measure, glyph_edits
    from ..web.editorimport import import_export, read_export
    from ..discover.inventory import load
    from datetime import datetime, timezone
    try:
        site = sitecatalog.read_export(Path(args.export_file))
        if site is not None:
            applied = sitecatalog.apply_export(site, write=not args.dry_run)
    except (OSError, ValueError) as exc:
        line("error", exc)
        return 1
    if site is not None:
        path = applied.path
        line("catalog", path.relative_to(TOOLKIT_ROOT).as_posix() if path.is_relative_to(TOOLKIT_ROOT) else path)
        line("status", ("planned" if args.dry_run else "applied") if applied.changed else "unchanged")
        line("changed", f"{len(applied.changed)} entries")
        line("unchanged", f"{applied.unchanged} entries")
        if applied.notes:
            line("notes", f"{applied.notes} entries kept as in the catalog")
        line("review", "required")
        return 0
    if getattr(args, "project", None) is None:
        import json
        from ..web.editorimport import _object
        from ..web.exportenvelope import validate
        try:
            doc = validate(json.loads(Path(args.export_file).read_bytes(), object_pairs_hook=_object))
        except (OSError, ValueError) as exc:
            line("error", exc)
            return 1
        if doc.get("entries") or doc.get("terms") or doc.get("pictures") \
                or not (doc.get("glyphs") or doc.get("keyboard")):
            line("error", "a game export needs a translation project; this repository has none")
            return 1
        return _pack_import(args, Profile.by_name(args.profile))
    prof = Profile.by_name(args.profile)
    result = {"batches": []}
    try:
        catalog = load_catalog(Path(args.po_root))
        data = read_export(Path(args.export_file), prof.name, args.lang, prof.sha1, catalog,
                           carried=("note_changes",), graphics_profile=prof)
        note_changes, application = notechanges.prepare(
            data["document"], args.project, prof, catalog, author=args.source,
            created=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        glyphs = glyph_edits(data["document"].get("glyphs", []), prof, args.lang)
        edit = data["document"].get("keyboard")
        if edit is not None:
            kb = prof.get("keyboard") or {}
            if "rows" not in kb:
                raise ValueError("the project declares no language-pack keyboard")
            english = {name: catalog[sid][1].msgid for name, sid in kb["row_bindings"].items() if sid in catalog}
            import tomllib
            with Path(prof.policy_path).open("rb") as stream:
                layout = tomllib.load(stream)["keyboard"]["layout"]
            planned = keyboard.apply_edit(args.lang, layout, kb, edit, english, write=False)
            from ..web.editorkeyboard import chat_pages, chat_problems, cursor_columns, off_cursor
            rom = prof.load_rom(args.rom)   # the game's glyph widths and cursor columns
            style = prof["font_style"]["style"]
            measures = _style_measures(prof, rom, args.lang)
            measure, cm = measures[style]
            inv = load(args.project.build / "inventory.json")
            # the export's redrawn glyphs at the resulting widths
            off = off_cursor(prof, inv, {**kb["rows"], **edit.get("rows", {})},
                             edited_measure(measure, glyphs, style), cm, cursor_columns(prof, rom) or [])
            if off:
                raise ValueError(f"keyboard: glyphs outside the cursor frame of their column: {', '.join(off)}")
            if chat := kb.get("chat"):
                small, chars = measures[chat["style"]]
                small = edited_measure(small, glyphs, chat["style"])
                for page in chat_pages(kb, inv, chars, small):
                    for row in page["rows"]:
                        text = edit.get("rows", {}).get(row["name"], row["text"])
                        problems = chat_problems(text, chat, row["bytes"], small, chars)
                        if problems:
                            raise ValueError(f"keyboard: {row['name']}: {'; '.join(problems)}")
        if data["document"]["entries"]:
            limits, matcher = _limits_and_matcher(args)
            rules = prof.get("context", {}).get("record_speakers", [])
            notes = record_speakers(rules, prof.load_rom(args.rom), load(args.project.build / "inventory.json")) \
                if rules else {}
            result = import_export(data, Path(args.po_root), args.project.batches, Path(args.plan),
                                   limits, matcher, args.source, args.dry_run, notes)
        written = apply_glyph_edits(glyphs, args.dry_run)
        if edit is not None and not args.dry_run:
            planned = keyboard.apply_edit(args.lang, layout, kb, edit, english)
        if application.files and not args.dry_run:
            application.write(args.project, prof)
    except (OSError, ValueError, KeyError) as exc:
        line("error", exc)
        return 1
    if edit is not None:
        line("keyboard", planned["path"])
        line("status", "planned" if args.dry_run else "applied")
        line("rows", f"{len(planned['rows'])} rows")
        line("pages", "reordered" if planned["pages"] else "unchanged")
        if not args.dry_run:
            line("snapshot", "stale")
    for batch in result["batches"]:
        action = "planned" if args.dry_run else "seeded"
        if not batch["changed"]:
            action = "unchanged"
        line("batch", batch["name"])
        line("status", action)
        line("entries", f"{batch['entries']} entries")
        line("kind", batch["kind"])
    for g in glyphs:
        line("glyph", g.label)
        line("style", g.style)
        line("status", ("planned" if args.dry_run else "applied") if g.changed else "unchanged")
        if g.copy is not None:
            line("source", f"copy:{g.copy:02X} -> glyph")
    for path in written:
        line("file", path.relative_to(TOOLKIT_ROOT).as_posix() if path.is_relative_to(TOOLKIT_ROOT) else path)
    for path in application.files:
        line("note file", path.resolve().relative_to(args.project.registry_root.resolve()).as_posix())
    if note_changes:
        line("notes", f"{len(note_changes)} changes, status proposed" + (", planned" if args.dry_run else ""))
    line("catalog", "unchanged")
    line("review", "required")
    return 0


@command("web submission")
def editor_submission(args):
    """Apply the editor export of a submission issue to its PO files, or write why it is refused."""
    from pathlib import Path
    from ..web.submission import run
    return run(Path(args.event), Path(args.out), args.registry, args.check)


def register(sub):
    ed = add(sub, "data", editor_data)
    ed.add_argument("--po-root", default=None, help="translation PO catalog directory")
    ed.add_argument("--glossary", default=None, help="term glossary TSV file")
    ed.add_argument("--rules", default=None,
                    help="the language's rule list")
    ed.add_argument("--out", default=None,
                    help="output path (default: build/editor/<lang>.json; split: shared build/editor/<profile>/<lang>)")
    ed.add_argument("--split", action="store_true",
                    help="write the editor page's form: an index, one file per PO file, languages.json")
    # Shared files survive language refreshes.
    ed.add_argument("--language-only", action="store_true", help=argparse.SUPPRESS)
    ed.add_argument("--no-rom", action="store_true", help="assemble from the tracked measurement snapshot")
    ed.add_argument("--snapshot", default=None, help="measurement file for --no-rom (default: the project's)")
    es = add(sub, "snapshot", editor_snapshot)
    es.add_argument("--po-root", default=None, help="translation PO catalog directory")
    es.add_argument("--out", default=None, help="snapshot path (default: the project's)")
    write_options(es, dest="check", check_aliases=("--check",),
                  check_help="compare the snapshot with fresh local ROM measurements")
    eb = add(sub, "bundle", editor_bundle)
    eb.add_argument("--out", default="build/editor/gen3tl-check.zip", help="output offline check bundle ZIP file")
    se = add(sub, "server", server)
    se.add_argument("action", choices=("start", "stop", "restart", "status", "log"),
                    help="local site or editor server operation")
    se.add_argument("lines", nargs="?", type=int, default=40, metavar="N", help="log lines")
    se.add_argument("--page", choices=("site", "editor"), default="editor", help="page to serve")
    se.add_argument("--po-root", default=None, help="translation PO catalog directory")
    se.add_argument("--data", default="build/editor", help="editor data directory")
    se.add_argument("--port", type=int, default=8000, help="local HTTP port")
    se.add_argument("--wait", type=int, default=20, metavar="N", help="seconds to wait for the page")
    se.add_argument("--foreground", action="store_true", help=argparse.SUPPRESS)
    ei = add(sub, "import", editor_import)
    ei.add_argument("export_file", help="editor JSON export")
    ei.add_argument("--po-root", default=None, help="translation PO catalog directory")
    ei.add_argument("--plan", default=None, help="batch plan for updating review state")
    ei.add_argument("--source", choices=("maintainer", "contributor"), default="contributor",
                    help="review role assigned to imported translations")
    write_options(ei, check_help="validate the import only")
    su = add(sub, "submission", editor_submission)
    su.add_argument("--event", required=True, help="GitHub issues event JSON of the submission issue")
    su.add_argument("--out", required=True, help="directory for the answer and pull request texts")
    write_options(su, dest="check", check_aliases=("--check",),
                  check_help="validate only; write the answer under --out")
    from ..web.siteshell import WEB
    ss = add(sub, "site-shell", site_shell)
    ss.add_argument("--web", default=str(WEB), help="web source directory with pages and fragments")
    ss.add_argument("--sync", action="store_true", help="rewrite the shared regions from their fragments")
    sc = add(sub, "site-catalog", site_catalog)
    sc.add_argument("--data", default="build/editor", help="editor data directory")
    sc.add_argument("--sync", action="store_true",
                    help="first add the keys the pages use to every catalog and drop the stale ones")
    sc.add_argument("--approve", nargs="+", metavar=("CODE", "KEY"), default=None,
                    help="mark approved the named keys of the site catalog CODE")
    sc.add_argument("--approve-all", metavar="CODE", default=None,
                    help="mark approved every translated entry of the site catalog CODE")
    sa = add(sub, "assemble", site_assemble)
    source = sa.add_mutually_exclusive_group()
    source.add_argument("--data", default="build/editor", help="editor data directory with languages.json")
    source.add_argument("--registry", dest="registry_file", metavar="FILE",
                        help="registry file of an application site")
    sa.add_argument("--out", default=None, help="empty directory (default: the project's site/)")
    sa.add_argument("--previous", default=None, metavar="SOURCE",
                    help="directory or https:// address of the earlier site; the new site keeps the assets of its two newest "
                         "earlier versions")
    sa.add_argument("--public-base", default=None, help="absolute site origin for static Pokédex share targets")
    sa.add_argument("--application", default=None, metavar="URL", help="absolute application URL for Pokédex share forwarding")
    sp = add(sub, "fetch-patches", site_patches)
    sp.add_argument("--repository", required=True, metavar="ACCOUNT/NAME", help="GitHub repository of published patches")
    sr = add(sub, "registry", site_registry)
    sr.add_argument("--data", default="build/editor", help="editor data directory with languages.json")
    sr.add_argument("--data-url", default="./", help="the games' data URL, relative to the registry")
    sr.add_argument("--game", action="append", default=[], help="list only this profile (repeatable)")
    sr.add_argument("--out", default=None, help="registry path (default: <data>/registry.json)")
    sr.add_argument("--repository", default=None, metavar="ACCOUNT/NAME",
                    help="GitHub repository that receives editor submissions")
