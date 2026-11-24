"""The site's interface catalog: one PO per language beside the web sources."""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .. import TOOLKIT_ROOT
from ..catalog.po import PoEntry, read, write as write_po
from .sitepreview import translated as preview_texts
from ..core.lists import append_unique

SITE = "site"   # the editor project id, never a ROM profile
PREVIEW_NOTE = "Link preview "   # opens every generated preview note
NAME = "Site interface"
PREFIX = "site:"
SCHEMA = 1
WEB = TOOLKIT_ROOT / "web"
CATALOGS = WEB / "i18n"
CODE = re.compile(r"[a-z]{2,3}(?:-[A-Za-z0-9]+)?")
HEADER = {"MIME-Version": "1.0", "Content-Type": "text/plain; charset=UTF-8", "Content-Transfer-Encoding": "8bit"}
CAPABILITIES = {"build": False, "layout": False, "fonts": False, "encoding": False, "check": "tokens"}
LIMITATIONS = [
    "The texts of this site; no game font, window or ROM applies to them.",
    "Keep every {name} hole: the page fills it.",
]
# the pages in location order; other pages follow by name
PAGES = ["index.html", "pick.html", "patch.html", "save.html", "editor.html", "pokedex.html", "submit.html", "guide.html"]
MASTHEAD_ONLY = {"guide.html"}   # the guide's body is a document, not interface
GUIDE = re.compile(r"(?:save-)?guide\.([a-z]{2,3}(?:-[A-Za-z0-9]+)?)\.html")   # a translated guide
GUIDE_PAGE = re.compile(r"(?:save-)?guide(?:\.[a-z]{2,3}(?:-[A-Za-z0-9]+)?)?\.html")
POKEDEX = {"pokedex", "pokedexdataview", "typechart", "dexpage", "movecard", "catchcalc", "movelabels",
           "moveeffects", "pokedexexplorer"}   # their t() keys are game strings first
MARKUP = re.compile(r'data-ui(?:-placeholder|-title|-label)?="([^"]+)"')
SITE_PARTS = re.compile(r'<head>.*?</head>|<header class="mast"[^>]*>.*?</header>|<footer class="foot"[^>]*>.*?</footer>', re.S)
TAGS = re.compile(r"<[a-z][^>]*>")
SITE_MARK = re.compile(r"\sdata-ui-site[\s=>]")   # a Pokédex element the site catalog keys
LITERAL = r'"((?:[^"\\]|\\.)*)"'
CALLS = re.compile(r"\b(?:t|N_|uiError)\(\s*" + LITERAL)   # a page label source may answer first
SITE_CALLS = [re.compile(r"\bsiteText\(\s*" + LITERAL), re.compile(r"\bshowText\((?:[^,()]|\([^()]*\))*,\s*" + LITERAL)]
LOCATIONS = re.compile(r"[\w.-]+\.html|js/[\w./-]+\.js|fragments/[\w.-]+\.json|registry")
REGISTERED = "registry"   # the location of keys the registry declares
HOLE = re.compile(r"\{\w+\}")   # a value the page fills


class SiteCatalogError(ValueError):
    pass


@dataclass(frozen=True)
class Synced:
    path: Path
    added: list[str]
    dropped: list[str]
    keys: int


@dataclass(frozen=True)
class Applied:
    path: Path
    changed: list[str]   # the ids of the changed translations
    unchanged: int
    notes: int   # source notes that differ, not applied


@dataclass(frozen=True)
class Approved:
    path: Path
    cleared: int


@dataclass(frozen=True)
class Compared:
    path: Path
    translated: int   # translated entries awaiting approval
    problems: list[str]


def load(path: Path) -> list[PoEntry]:
    """The entries of a site catalog, its identity checked: the file's language, one entry per key."""
    path = Path(path)
    code = path.stem
    if not CODE.fullmatch(code):
        raise SiteCatalogError(f"{path}: not a language code")
    header, entries = read(path)
    if header.get("Language") != code or header.get("Project-Id-Version") != SITE:
        raise SiteCatalogError(f"{path}: header names another catalog")
    seen = set()
    for entry in entries:
        if entry.msgctxt != PREFIX + entry.msgid or not entry.msgid:
            raise SiteCatalogError(f"{path}: {entry.msgctxt!r} is not {PREFIX}<its msgid>")
        if entry.msgid in seen:
            raise SiteCatalogError(f"{path}: duplicate key {entry.msgid!r}")
        seen.add(entry.msgid)
    return entries


def catalogs(directory: Path | None = None) -> dict[str, Path]:
    """The site catalogs of a directory by language code."""
    directory = Path(directory or CATALOGS)
    return {path.stem: path for path in sorted(directory.glob("*.po"))} if directory.is_dir() else {}


def registry_keys(root: Path | None = None) -> set[str]:
    """Interface keys declared by the active registry configuration."""
    from ..profile.profile import Profile, profile_paths
    from ..project import REGISTRY, Registry, registry_root, registry_scope

    root = Path(root) if root is not None else registry_root()
    registered = Registry.load(root) if (root / REGISTRY).is_file() else None
    keys = {license["label_key"] for license in (registered.site or {}).get("licenses", [])} if registered else set()
    with registry_scope(root):
        profiles = registered.profiles().values() if registered else map(Profile.load, profile_paths())
        for profile in profiles:
            for field in ("compatibility", "landing"):
                keys.update(profile.get(field, {}).values())
            for rule in profile.get("language_rules", []):
                keys.update((rule["title"], rule["description"]))
    return keys


def page_keys(web: Path | None = None, registry: set[str] | None = None) -> dict[str, list[str]]:
    """Every key the site's pages and modules show through the site catalog: key -> where.

    `registry`: the keys the registry declares; None reads the active registry for the installed pages.
    """
    web = Path(web or WEB)
    keys: dict[str, list[str]] = {}

    def add_use(key, where):
        append_unique(keys.setdefault(key, []), where)

    pages = [name for name in PAGES if (web / name).is_file()]
    pages += sorted(path.name for path in web.glob("*.html")
                    if path.name not in PAGES and not GUIDE_PAGE.fullmatch(path.name))   # shared guide chrome
    for name in pages:
        text = (web / name).read_text(encoding="utf-8")
        for key in MARKUP.findall("".join(SITE_PARTS.findall(text))):
            add_use(html.unescape(key), name)
        if name in MASTHEAD_ONLY:
            continue
        body = SITE_PARTS.sub("", text)
        if name == "pokedex.html":   # its other keys are game strings first
            body = "".join(tag for tag in TAGS.findall(body) if SITE_MARK.search(tag))
        for key in MARKUP.findall(body):
            add_use(html.unescape(key), name)
    for path in sorted((web / "js").rglob("*.js")):
        source = "\n".join(line for line in path.read_text(encoding="utf-8").split("\n")
                           if not re.match(r"\s*//", line))
        relative = path.relative_to(web / "js")
        where = f"js/{relative.as_posix()}"
        pokedex = path.stem in POKEDEX or relative.parts[0] in {"pokedex", "pokedexdataview"}
        for match in CALLS.finditer(source):
            key = json.loads(f'"{match.group(1)}"')
            if not pokedex:
                add_use(key, where)
        for pattern in SITE_CALLS:
            for match in pattern.finditer(source):
                add_use(json.loads(f'"{match.group(1)}"'), where)
    if registry is None and web.resolve() == WEB.resolve():
        registry = registry_keys()
    for key in sorted(registry or ()):
        add_use(key, REGISTERED)
    for row in preview_texts(web).values():
        for key in row.values():
            add_use(key, "fragments/page-previews.json")
    return keys


def preview_notes(web: Path | None = None) -> dict[str, str]:
    """Describe each preview field in every interface catalog."""
    return {text: (f"{PREVIEW_NOTE}title for {page}; retain the page's purpose." if field == "title" else
                   f"{PREVIEW_NOTE}description for {page}; use one sentence.")
            for page, row in preview_texts(Path(web or WEB)).items() for field, text in row.items()}


def location(where: list[str]) -> str:
    """The location comment of a key: the files that use it."""
    return ", ".join(where)


def is_location(comment: str) -> bool:
    """Whether an extracted comment is a location comment."""
    return all(LOCATIONS.fullmatch(item) for item in comment.split(", "))


def places(entry: PoEntry) -> list[str]:
    """The files an entry's location comment names."""
    return [item for comment in entry.extracted if is_location(comment) for item in comment.split(", ")]


def merged(entries: list[PoEntry], keys: dict[str, list[str]], notes: dict[str, str] | None = None) -> list[PoEntry]:
    """The entries of the keys in use, then an empty entry per new key; locations refreshed."""
    out = []
    for e in entries:
        if e.msgid in keys:
            e.extracted = [c for c in e.extracted if not is_location(c)] + [location(keys[e.msgid])]
            out.append(e)
    have = {e.msgid for e in out}
    out += [PoEntry(PREFIX + key, key, extracted=[location(where)]) for key, where in keys.items() if key not in have]
    for entry in out if notes is not None else ():
        # A generated note follows its source.
        entry.extracted = [c for c in entry.extracted if not c.startswith(PREVIEW_NOTE)]
        if entry.msgid in notes:
            entry.extracted.insert(0, notes[entry.msgid])
    return out


def catalog_keys(directory: Path | None = None, web: Path | None = None) -> dict[str, list[str]]:
    """The keys every catalog of `directory` holds: those of the pages under `web` and of the registry.

    Without an active registry, the catalogs' registry entries stand for its keys.
    """
    web = Path(web or WEB)
    registry = registry_keys() if web.resolve() == WEB.resolve() else set()
    if not registry:   # only a game project knows its registry
        registry = {e.msgid for path in catalogs(directory).values() for e in load(path) if REGISTERED in places(e)}
    return page_keys(web, registry)


def sync(directory: Path | None = None, web: Path | None = None) -> list[Synced]:
    """Bring every catalog of `directory` to the keys of the pages under `web` and of the registry."""
    keys = catalog_keys(directory, web)
    out = []
    for path in catalogs(directory).values():
        entries = load(path)
        header = read(path)[0]
        kept = merged(entries, keys, preview_notes(web))
        old = {e.msgid for e in entries}
        write_po(path, kept, header)
        out.append(Synced(path, [k for k in keys if k not in old], [e.msgid for e in entries if e.msgid not in keys],
                          len(kept)))
    return out


def compare(directory: Path | None = None, web: Path | None = None) -> tuple[dict[str, list[str]], list[Compared]]:
    """Compare every catalog of `directory` with the keys it holds (catalog_keys): the keys, each catalog's problems."""
    from tempfile import TemporaryDirectory
    keys = catalog_keys(directory, web)
    loaded = {path: load(path) for path in catalogs(directory).values()}
    out = []
    for path, entries in loaded.items():
        header = read(path)[0]
        have = {e.msgid for e in entries}
        added = [key for key in keys if key not in have]
        dropped = [e.msgid for e in entries if e.msgid not in keys]
        problems = [f"missing key  {path}: {key}" for key in added] + [f"stale key  {path}: {key}" for key in dropped]
        problems += [f"dropped hole  {path}: {e.msgid}" for e in entries
                     if e.msgstr and sorted(HOLE.findall(e.msgstr)) != sorted(HOLE.findall(e.msgid))]
        translated = sum(1 for e in entries if e.msgstr and e.fuzzy)
        if not added and not dropped:
            with TemporaryDirectory() as tmp:
                synced = Path(tmp) / path.name
                write_po(synced, merged(entries, keys), header)
                if synced.read_bytes() != path.read_bytes():
                    problems.append(f"out of sync  {path}: web site-catalog --sync would change it")
        out.append(Compared(path, translated, problems))
    return keys, out


def seed(code: str, directory: Path | None = None, web: Path | None = None) -> Path | None:
    """Create the catalog of a new language, every key with an empty target; None when it exists."""
    if not CODE.fullmatch(code):
        raise SiteCatalogError(f"{code}: not a language code")
    path = Path(directory or CATALOGS) / f"{code}.po"
    if path.exists():
        return None
    header = {"Project-Id-Version": SITE, "Language": code, **HEADER}
    write_po(path, merged([], catalog_keys(directory, web), preview_notes(web)), header, obsolete=[])
    return path


def read_export(path: Path) -> dict | None:
    """The editor export at `path` when its profile is the site project, else None."""
    from .editorimport import _object
    from .exportenvelope import validate
    try:
        doc = json.loads(Path(path).read_bytes(), object_pairs_hook=_object)
    except RecursionError:
        raise ValueError("editor export nests too deeply") from None
    doc = validate(doc)
    return doc if doc["profile"] == SITE else None


def apply_export(doc: dict, directory: Path | None = None, write: bool = True) -> Applied:
    """Apply the text entries of an editor export of the site project to the catalog; a changed approved text stays approved."""
    from .exportenvelope import validate
    try:
        doc = validate(doc)
    except ValueError as exc:
        raise SiteCatalogError(str(exc)) from exc
    language = doc["language"]
    if doc["profile"] != SITE:
        raise SiteCatalogError(f"the export names no {SITE} catalog language")
    path = catalogs(directory).get(language)
    if path is None:
        raise SiteCatalogError(f"no site catalog for {language!r}")
    for key in ("pictures", "terms", "glyphs"):
        if doc.get(key, []) not in ([], None):
            raise SiteCatalogError(f"the export holds {key}; the site catalog takes text entries only")
    if doc.get("keyboard") is not None:
        raise SiteCatalogError("the export holds keyboard; the site catalog takes text entries only")
    if {"note_changes", "note_changes_schema"} & doc.keys():
        from .notechanges import records as note_records
        if note_records(doc):
            raise SiteCatalogError("the export holds note_changes; the site catalog takes text entries only")
    records = doc.get("entries")
    if not isinstance(records, list) or not records:
        raise SiteCatalogError("the export needs a nonempty entries list")
    entries = {e.msgctxt: e for e in load(path)}
    targets, notes, seen = {}, 0, set()
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
            raise SiteCatalogError("each editor entry needs a string id")
        sid = record["id"]
        if sid in seen:
            raise SiteCatalogError(f"duplicate editor entry: {sid}")
        seen.add(sid)
        entry = entries.get(sid)
        if entry is None:
            raise SiteCatalogError(f"{sid}: not in the site catalog {path.name}")
        text, note = record.get("translation"), record.get("note", entry.source_note)
        if not isinstance(text, str) or not isinstance(note, str):
            raise SiteCatalogError(f"{sid}: translation and note must be strings")
        for key, current in (("source_text", entry.msgid), ("base", entry.msgstr)):
            if key in record and record[key] != current:
                raise SiteCatalogError(f"{sid}: editor {key} differs from the current catalog")
        if any(ord(c) < 32 and c not in "\n\t" for c in text):
            raise SiteCatalogError(f"{sid}: unsupported control character in the translation")
        if not text:
            raise SiteCatalogError(f"{sid}: an empty translation; the page would show the English")
        if sorted(HOLE.findall(text)) != sorted(HOLE.findall(entry.msgid)):
            holes = ", ".join(HOLE.findall(entry.msgid)) or "no hole"
            raise SiteCatalogError(f"{sid}: the translation must keep the holes of its key ({holes})")
        notes += note != entry.source_note
        if text != entry.msgstr:
            targets[sid] = text
    if write and targets:
        header, kept = read(path)
        for entry in kept:
            if entry.msgctxt in targets:
                entry.set_translation(targets[entry.msgctxt], "text", keep_approved=True)
        write_po(path, kept, header)
        load(path)   # one entry per key under its identity
    return Applied(path, list(targets), len(records) - len(targets), notes)


def approve(code: str, keys: list[str] | None = None, directory: Path | None = None) -> Approved:
    """Clear fuzzy on the named keys of the site catalog `code`, or on every fuzzy entry when `keys` is None."""
    path = catalogs(directory).get(code)
    if path is None:
        raise SiteCatalogError(f"no site catalog for {code!r}")
    header, entries = read(path)
    known = {e.msgid for e in entries}
    missing = [key for key in (keys or []) if key not in known]
    if missing:
        raise SiteCatalogError(f"{path.name}: unknown key: {', '.join(missing)}")
    cleared = 0
    for entry in entries:
        if (keys is None or entry.msgid in keys) and entry.fuzzy:
            entry.set_fuzzy(False)
            cleared += 1
    if cleared:
        write_po(path, entries, header)
        load(path)   # one entry per key under its identity
    return Approved(path, cleared)


def strings(entries: list[PoEntry]) -> dict[str, str]:
    """The translated entries: key -> text."""
    return {e.msgid: e.msgstr for e in entries if e.msgstr}


def page_doc(code: str, entries: list[PoEntry]) -> dict:
    """What the pages fetch for a language."""
    return {"schema": SCHEMA, "language": code, "strings": strings(entries)}


STARTUP = "js/pokedex-startup.js"


def startup_doc(languages: dict[str, list[PoEntry]], keys: dict[str, list[str]]) -> dict[str, dict[str, str]]:
    """The translated startup keys by interface language."""
    return {code: {key: text for key, text in strings(entries).items() if STARTUP in keys.get(key, [])}
            for code, entries in languages.items()}


def editor_doc(code: str, entries: list[PoEntry], file: str) -> dict:
    """The catalog as an editor document, in the shape of the draft catalogs (draftcatalog.editor_data)."""
    from .editordata import SCHEMA as EDITOR_SCHEMA, context, status, toolkit_version
    rows = []
    for e in entries:
        rows.append({"id": e.msgctxt, "file": file, "source_text": e.msgid, "translation": e.msgstr,
                     "flags": list(e.flags), "status": status(e), "context": context(list(e.extracted), {}),
                     "window": None, "terms": [], "approved": [], "note": e.source_note, "translator_notes": [],
                     "reach": {"class": "unknown", "source": "not studied", "evidence": ""},
                     "rules": [{"text": text, "source": NAME, "evidence": ""} for text in LIMITATIONS]})
    return {"schema": EDITOR_SCHEMA, "toolkit": toolkit_version(), "language": code, "profile": SITE,
            "rom": {"sha1": None}, "capabilities": CAPABILITIES, "limitations": LIMITATIONS,
            "check_rules": {}, "fonts": {"main": None, "styles": {}, "codes": {}},
            "charmap": {}, "placeholders": {"default": 0, "widths": {}},
            "glossary": {}, "graphics": None, "entries": rows}


def profile_entry() -> dict:
    """The site project in languages.json: listed for the editor, never a game of the registry."""
    return {"id": SITE, "name": NAME, "support": "interface", "engine": "unspecified"}


def guides(web: Path | None = None) -> list[str]:
    """The languages of a translated contributor guide under `web`."""
    web = Path(web or WEB)
    return sorted(m.group(1) for path in web.glob("guide.*.html") if (m := GUIDE.fullmatch(path.name)))


def index_doc(languages: dict[str, list[PoEntry]], guided: list[str] | None = None) -> dict:
    """The index of the catalogs: every language with its key and translation counts, and translated guides."""
    return {"schema": SCHEMA, "catalogs": [{"code": code, "keys": len(entries), "translated": len(strings(entries))}
                                           for code, entries in languages.items()], "guides": guided or []}


def write(root: Path, directory: Path | None = None, web: Path | None = None) -> list[str]:
    """Write site/<code>.json, startup.js and the editor project of every catalog under `root`, then site/catalogs.json."""
    from .editordata import _check_output, split, write_split
    root = Path(root)
    out = root / SITE
    _check_output(root, out)
    out.mkdir(parents=True, exist_ok=True)
    languages = {}
    for code, path in catalogs(directory).items():
        entries = load(path)
        (out / f"{code}.json").write_text(json.dumps(page_doc(code, entries), ensure_ascii=False,
                                                     separators=(",", ":"), sort_keys=True), encoding="utf-8")
        try:
            file = path.resolve().relative_to(CATALOGS.parents[1]).as_posix()
        except ValueError:
            file = path.name
        index, chunks = split(editor_doc(code, entries, file), None, {})
        write_split(out / code, index, chunks, key_only=False)
        languages[code] = entries
    startup = json.dumps(startup_doc(languages, page_keys(web)), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    (out / "startup.js").write_text(f"globalThis.pokedexStartupCatalog = {startup};\n", encoding="utf-8")
    (out / "catalogs.json").write_text(json.dumps(index_doc(languages, guides(web)), ensure_ascii=False,
                                                  separators=(",", ":")), encoding="utf-8")
    return list(languages)


def write_interface(root: Path) -> None:
    """Write only the interface catalogs and their editor manifest."""
    from .editordata import SCHEMA as EDITOR_SCHEMA, toolkit_version
    from .editorstore import identity
    write(root)
    languages = []
    for path in sorted((root / SITE).glob("*/index.json")):
        row = identity(path)
        languages.append({"profile": SITE, "code": row["language"], "index": path.relative_to(root).as_posix(),
                          "entries": row["entries"], "progress": row["progress"]})
    manifest = {"schema": EDITOR_SCHEMA, "toolkit": toolkit_version(), "languages": languages,
                "profiles": [profile_entry()]}
    (root / "languages.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def list_catalogs(site: Path) -> list[str] | None:
    """Name the catalogs of the site's data/site/catalogs.json in its root pages, which then ask for no other."""
    try:
        index = json.loads((Path(site) / "data" / SITE / "catalogs.json").read_text(encoding="utf-8"))
        codes = [row["code"] for row in index["catalogs"]] if index["schema"] == SCHEMA else None
    except (OSError, ValueError, KeyError, TypeError):
        return None
    # An unreadable index leaves the pages unlisted.
    if codes is None or not all(isinstance(code, str) and CODE.fullmatch(code) for code in codes):
        return None
    meta = f'<meta name="site-catalogs" content="{" ".join(codes)}">'
    for page in sorted(Path(site).glob("*.html")):
        document = page.read_text(encoding="utf-8")
        listed, count = re.subn(r"<head\b[^>]*>", lambda match: match[0] + "\n" + meta, document, count=1,
                                flags=re.IGNORECASE)
        if count:
            page.write_text(listed, encoding="utf-8")
    return codes
