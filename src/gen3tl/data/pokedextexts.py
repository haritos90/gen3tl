"""The Pokedex page's texts: the catalog strings a dataset names, per language.

A language file holds its translations; the English is a game part the languages share.
"""

from __future__ import annotations

import functools
import json
import re
import tomllib
from pathlib import Path

SCHEMA = "2.0.0"
SCHEMAS = (1, SCHEMA)   # the first form holds the English beside each translation
FILE = "pokedex-texts.json"
PART = "pokedex-texts"
PART_PATH = re.compile(r"\.\./shared/[0-9a-f]{64}\.json")
FAMILIES = ("species", "pokedex_categories", "types", "abilities", "moves", "items",
            "trainer_names", "trainer_classes")
ID = re.compile(r"^(s[0-9A-F]{7}|[a-z_]+:\d+)$")
LABEL = re.compile(r"^(s[0-9A-F]{7}|[a-z_]+:\d+)@[^@]+$")   # a label id with a fragment rule


def referenced(dataset) -> set[str]:
    """Every catalog id the dataset names as a string, anywhere in it."""
    ids: set[str] = set()

    def walk(x):
        if isinstance(x, str):
            if ID.match(x):
                ids.add(x)
            elif LABEL.match(x):
                ids.add(LABEL.match(x).group(1))
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(dataset)
    return ids


def carried(row: dict) -> str | None:
    """The English an index row carries; None for a key-only entry, whose row holds its key or none."""
    text = row.get("source_text")
    return text if isinstance(text, str) and text != row["id"] else None


def source_english(index: dict, aliases) -> dict[str, str]:
    """Inventory id -> English of the running project's English source catalog, {} without that file."""
    from ..catalog import extract
    path = extract.path()
    if not path.is_file():
        return {}
    found = extract.load(path, (index.get("rom") or {}).get("sha1"), index["profile"])
    return {aliases.entry_id(key): e.msgid for key, e in found.items()}


def source_digests() -> dict[str, str]:
    """Inventory id -> digest of the English, of the running project's snapshot; {} without one."""
    from ..web.editorsnapshot import project_digests
    return project_digests()


def select(dataset, rows, aliases=None, english=None) -> dict[str, list[str]]:
    """id -> [english, translation] of the index rows the page can show, by key."""
    entry_id = aliases.entry_id if aliases is not None else (lambda x: x)
    named = referenced(dataset)
    out = {}
    for row in rows:
        sid = entry_id(row["id"])
        family = sid.split(":")[0] if ":" in sid else None
        if sid in named or family in FAMILIES:
            text = carried(row)
            if text is None:
                text = english().get(sid, "") if english is not None else ""
            out[sid] = [text, row["translation"]]
    return dict(sorted(out.items()))


def document(index: dict, dataset: dict, dataset_path: str, page: dict | None = None,
             aliases: dict | None = None) -> dict:
    """The texts file of an index (editordata.split) and its profile's dataset."""
    from ..catalog import keys
    table = keys.load((index.get("rom") or {}).get("sha1"))
    english = functools.cache(lambda: source_english(index, table))   # read once, where a row needs it
    doc = {"schema": SCHEMA, "profile": index["profile"], "language": index["language"],
           "dataset": dataset_path, "draft": index.get("capabilities", {}).get("check") == "tokens",
           "texts": select(dataset, index["list"], table, english)}
    if page:
        doc["page"] = dict(sorted(page.items()))   # the Pokédex label catalog
    if aliases:
        doc["search_aliases"] = dict(sorted(aliases.items()))
    return doc


def search_aliases(path: Path | None, index: dict, dataset: dict) -> dict[str, list[str]]:
    """Validate optional species aliases against their source names."""
    if path is None or not path.exists():
        return {}
    with path.open("rb") as stream:
        doc = tomllib.load(stream)
    if (doc.get("schema") != "2.0.0" or doc.get("profile") != index["profile"]
            or doc.get("language") != index["language"]):
        raise ValueError(f"{path}: search aliases do not match this project")
    if set(doc) != {"schema", "profile", "language", "species"} or not isinstance(doc["species"], dict):
        raise ValueError(f"{path}: expected a species alias table")
    from ..catalog import keys
    from ..catalog.source import digest
    entry_id = keys.load((index.get("rom") or {}).get("sha1")).entry_id
    names = {row["name"] for row in dataset.get("species", [])}
    sources = {entry_id(row["id"]): carried(row) for row in index["list"]}
    expected = functools.cache(source_digests)   # a key-only row is judged by its digest
    aliases = {}
    for sid, entry in doc["species"].items():
        if sid not in names or sid not in sources:
            raise ValueError(f"{path}: unknown species name {sid}")
        if not isinstance(entry, dict) or set(entry) != {"source_sha256", "aliases"}:
            raise ValueError(f"{path}: {sid} needs source_sha256 and aliases")
        if sources[sid] is None and sid not in expected():
            raise ValueError(f"{path}: {sid} source name unknown without the English or the snapshot")
        if entry["source_sha256"] != (digest(sources[sid]) if sources[sid] is not None else expected()[sid]):
            raise ValueError(f"{path}: {sid} source name changed")
        values = entry["aliases"]
        if (not isinstance(values, list) or not values
                or any(not isinstance(value, str) or not value.strip() for value in values)):
            raise ValueError(f"{path}: {sid} needs nonempty alias strings")
        values = [value.strip() for value in values]
        if any(any(ord(char) < 32 for char in value) for value in values):
            raise ValueError(f"{path}: {sid} aliases must be single-line text")
        if len({value.casefold() for value in values}) != len(values):
            raise ValueError(f"{path}: {sid} has duplicate aliases")
        aliases[sid] = values
    return aliases


def page_labels(catalog: Path | None) -> dict[str, str | list[str]]:
    """Keyed page labels retain their English fallback."""
    if not catalog or not Path(catalog).exists():
        return {}
    from ..catalog.po import read
    _header, entries = read(Path(catalog))
    keyed = ("page:power:", "page:effect:", "page:mechanics:")
    return {e.msgctxt if e.msgctxt.startswith(keyed) else e.msgid:
            [e.msgid, e.msgstr] if e.msgctxt.startswith(keyed) else e.msgstr
            for e in entries if e.msgstr or e.msgctxt.startswith(keyed)}


def dataset_dir(profile: str) -> Path:
    """Resolve declared dataset inputs before the conventional locations."""
    from ..project import REGISTRY, Registry, registry_root
    root = registry_root()
    if (root / REGISTRY).is_file():
        for project in Registry.load(root).projects:
            if project.profile == profile and project.dataset_path is not None:
                return project.dataset_path
    return default_dataset_dir(profile)


def default_dataset_dir(profile: str) -> Path:
    """The conventional dataset location, independent of declared read inputs."""
    from ..profile.profile import TOOLKIT_ROOT
    from ..project import game_root, game_roots
    for root in game_roots():
        if game_root(root, profile):
            return root / "data" / "pokedex"
    return TOOLKIT_ROOT / "web" / "data" / "pokedex" / profile


def tracked_dataset(profile: str) -> Path | None:
    """The tracked dataset of a profile (dataset_dir), or None."""
    path = dataset_dir(profile) / "pokedex.json"
    return path if path.exists() else None


def split(doc: dict) -> tuple[dict, dict]:
    """The language file and the English part of a texts document."""
    texts, page = doc["texts"], doc.get("page", {})
    part = {"part": PART, "schema": doc["schema"], "texts": {sid: pair[0] for sid, pair in texts.items()}}
    keyed = {key: value[0] for key, value in page.items() if isinstance(value, list)}
    if keyed:
        part["page"] = keyed
    own = {key: value for key, value in doc.items() if key not in ("texts", "page")}
    own["texts"] = {sid: pair[1] for sid, pair in texts.items() if pair[1]}
    translated = {key: value[1] if isinstance(value, list) else value for key, value in page.items()}
    if translated := {key: value for key, value in translated.items() if value}:
        own["page"] = translated
    return own, dict(sorted(part.items()))


def decode(stored: dict, part: dict) -> dict:
    """The texts document of a language file and its English part (web/js/pokedexstore.js)."""
    if part.get("part") != PART or part.get("schema") != stored.get("schema"):
        raise ValueError("Pokedex texts: not their English part")
    own, own_page = stored.get("texts", {}), stored.get("page", {})
    texts = {sid: [english, own.get(sid, "")] for sid, english in part["texts"].items()}
    if set(own) - set(texts):
        raise ValueError(f"Pokedex texts: {sorted(set(own) - set(texts))[0]} has no English")
    page = {key: [english, own_page.get(key, "")] for key, english in part.get("page", {}).items()}
    page.update({key: value for key, value in own_page.items() if key not in page})
    doc = {key: value for key, value in stored.items() if key not in ("source", "texts", "page")}
    doc["texts"] = texts
    if page:
        doc["page"] = dict(sorted(page.items()))
    return dict(sorted(doc.items()))


def part_path(path) -> Path | None:
    """The English part a texts file names, or None."""
    path = Path(path)
    try:
        source = json.loads(path.read_text(encoding="utf-8")).get("source")
    except (OSError, ValueError, AttributeError):
        return None
    return path.parent / source if isinstance(source, str) and PART_PATH.fullmatch(source) else None


def read(path) -> dict:
    """A texts file as its document; a language file reads its English part."""
    path = Path(path)
    stored = json.loads(path.read_text(encoding="utf-8"))
    if "source" not in stored:
        return stored
    if not isinstance(stored["source"], str) or not PART_PATH.fullmatch(stored["source"]):
        raise ValueError(f"{path}: invalid English part {stored['source']!r}")
    return decode(stored, json.loads((path.parent / stored["source"]).read_text(encoding="utf-8")))


def write(out, index: dict, dataset_source: Path | None = None, page_catalog: Path | None = None,
          aliases_file: Path | None = None) -> Path | None:
    """Write out/pokedex-texts.json and its English part for the index's profile; None without a dataset."""
    from ..web.editorstore import PARTS, write_part
    from .pokedexstore import load
    source = dataset_source or tracked_dataset(index["profile"])
    if source is None:
        return None
    dataset = load(source)
    doc = document(index, dataset, f"pokedex/{index['profile']}/pokedex.json", page_labels(page_catalog),
                   search_aliases(aliases_file, index, dataset))
    own, part = split(doc)
    path = Path(out) / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    own["source"] = f"../{PARTS}/{write_part(path.parent.parent / PARTS, part)}"
    path.write_text(json.dumps(own, ensure_ascii=False, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    return path
