"""The English source catalog, regenerated from the base ROM through the profile's facts."""

from __future__ import annotations

import json
from pathlib import Path

from .po import PoEntry, read, with_source, write as write_po
from .source import digest

STUDIES = ("inventory", "context", "reach")


def studies(profile, build: Path) -> dict[str, dict]:
    """The studies under `build`, each of `profile` and its base ROM."""
    from ..discover import inventory as inventory_module
    from ..project import ProjectError
    documents = {}
    for name in STUDIES:
        path = Path(build) / f"{name}.json"
        if not path.is_file():
            raise ProjectError(f"missing {path}; run gen3tl discover {name} first")
        documents[name] = inventory_module.load(path) if name == "inventory" else json.loads(path.read_text("utf-8"))
        if (documents[name].get("profile"), documents[name].get("rom_sha1")) != (profile.name, profile.sha1):
            raise ProjectError(f"{path}: profile or ROM identity differs")
    return documents


def entries(profile, rom, build: Path, root: Path) -> list[PoEntry]:
    """The English source entries, in catalog order; `root` holds the alias table."""
    from ..build.font import FontTable
    from ..core.charmap import Charmap, CharmapError
    from ..project import ProjectError
    from . import keys, source
    from .catalog import build_entries, never_shown
    documents = studies(profile, build)
    data, cm = bytes(rom.data), Charmap()
    for entry in documents["inventory"]["entries"]:
        try:
            entry["text"] = source.line(data, source.row(entry), cm)
        except CharmapError as exc:
            raise ProjectError(f"{entry['id']}: {exc}") from exc
    font = FontTable.from_profile(profile)[0]
    rules = profile.get("reach", {})
    never = never_shown(documents["reach"], rules.get("never_shown", []), rules.get("observed", []))
    groups = build_entries(documents["inventory"], documents["context"], Charmap(), lambda b: font.width(rom, b),
                           profile.name, never, keys.load(profile.sha1, root))
    return [PoEntry(e.msgctxt, e.msgid, "", list(e.extracted), [], list(e.references), list(e.flags))
            for _, group in sorted(groups.items()) for e in group]


def verify(source: list[PoEntry], digests: dict[str, str], aliases) -> list[str]:
    """The keys of `digests` whose English in `source` differs or is missing."""
    english = {aliases.entry_id(e.msgctxt): e.msgid for e in source}
    return sorted(aliases.key(sid) for sid, expected in digests.items()
                  if sid not in english or digest(english[sid]) != expected)


def regenerate(profile, rom, project) -> list[PoEntry]:
    """The English source catalog regenerated from the ROM, verified against the snapshot digests."""
    from ..web.editorsnapshot import digests, load
    from . import keys
    found = entries(profile, rom, project.build, project.root)
    doc = load(project.snapshot)
    expected = digests(doc)
    if not expected:
        raise ValueError(f"{project.snapshot}: no English digests; regenerate gen3tl web snapshot")
    differ = verify(found, expected, keys.load(profile.sha1, project.root))
    if differ:
        raise ValueError(f"the English of {len(differ)} entries differs from the snapshot digests: "
                         f"{', '.join(differ[:10])}; the ROM, the profile or the studies differ")
    return found


def from_rom(catalog: dict, profile, rom, project) -> None:
    """Join keyed entries of a loaded catalog with their English from the ROM or the project's build/catalog.pot."""
    from .catalog import rom_sha1
    keyed = [e for _, e in catalog.values() if e.keyed]
    if not keyed:
        return
    source = regenerate(profile, rom, project) if project is not None and project.snapshot.is_file() else None
    english(keyed, source, rom_sha1(profile))


def header(profile) -> dict[str, str]:
    return {"Project-Id-Version": profile.name, "Language": "", "Content-Type": "text/plain; charset=UTF-8",
            "Content-Transfer-Encoding": "8bit", "X-ROM-SHA1": profile.sha1}


def write(path: Path, source: list[PoEntry], profile) -> None:
    write_po(Path(path), source, header(profile), [])


def changes(before: list[PoEntry], after: list[PoEntry]) -> dict[str, list[str]]:
    """The keys added, removed and changed (English, comments or offset) from `before` to `after`."""
    old = {e.msgctxt: e for e in before}
    new = {e.msgctxt: e for e in after}
    shape = lambda e: (e.msgid, e.extracted, e.references)   # noqa: E731
    return {"added": [k for k in new if k not in old], "removed": [k for k in old if k not in new],
            "changed": [k for k in new if k in old and shape(new[k]) != shape(old[k])]}


def path() -> Path:
    """The English source catalog of the running project, else of the registry root: build/catalog.pot."""
    from ..project import active, registry_root
    project = active()
    return (project.build if project is not None else registry_root() / "build") / "catalog.pot"


_loaded: dict[Path, tuple[tuple[int, int], dict[str, str], dict[str, PoEntry]]] = {}


def load(source: Path, sha1: str | None = None, profile: str | None = None) -> dict[str, PoEntry]:
    """The entries of an English source catalog file by key, checked against `sha1` and `profile`."""
    source = Path(source).resolve()
    stat = source.stat()
    cached = _loaded.get(source)
    if cached is None or cached[0] != (stat.st_mtime_ns, stat.st_size):
        header, found = read(source)
        cached = ((stat.st_mtime_ns, stat.st_size), header, {e.msgctxt: e for e in found})
        _loaded[source] = cached
    if sha1 and cached[1].get("X-ROM-SHA1", sha1) != sha1 or profile and cached[1].get("Project-Id-Version",
                                                                                         profile) != profile:
        raise ValueError(f"{source}: the English source catalog of another ROM; run gen3tl catalog extract")
    return cached[2]


def english(catalog: list[PoEntry], source=None, sha1: str | None = None, required: bool = True,
            profile: str | None = None) -> None:
    """Join the keyed entries among `catalog` with the English source catalog `source`."""
    keyed = [e for e in catalog if e.keyed]
    if not keyed:
        return
    where = path() if source is None else source
    if isinstance(where, list):
        found = {e.msgctxt: e for e in where}
    elif isinstance(where, dict):
        found = where
    elif where is not None and Path(where).is_file():
        try:
            found = load(where, sha1, profile)
        except ValueError:
            if required:
                raise
            return
    else:
        if required:
            raise ValueError(f"a key-only catalog without its English source catalog {where};"
                             " run gen3tl catalog extract")
        return
    missing = [e.msgctxt for e in keyed if e.msgctxt not in found]
    if missing and required:
        raise ValueError(f"{len(missing)} keys missing from the English source catalog: {', '.join(missing[:5])}"
                         "; run gen3tl catalog extract")
    for e in keyed:
        if e.msgctxt in found:
            with_source(e, found[e.msgctxt])
