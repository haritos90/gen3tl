"""The stored form of the split editor data: game parts once, a language what differs.

The page reads it through web/js/editorstore.js, which rebuilds the documents written here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from ..check import provenance
from .snapshotstore import Table, without_defaults

PARTS = "shared"   # beside the language directories of a game
PART_NAME = re.compile(r"[0-9a-f]{64}\.json")
# the order of a decoded entry and of its context
ENTRY = ("id", "file", "tokens", "translation", "flags", "status", "context", "owner", "window", "terms",
         "approved", "note", "reach", "translator_notes", "rules", "source_sha256", "note_ids")
CONTEXT = ("comments", "events", "boxes", "uses", "speakers", "inserted", "words", "hosts", "identity", "maps")
GAME_CONTEXT = {"comments": [], "events": [], "boxes": [], "uses": 0, "speakers": [], "hosts": []}
LANGUAGE_ENTRY = {"flags": [], "terms": [], "approved": [], "note": "", "translator_notes": [], "rules": []}
LANGUAGE_CONTEXT = {"inserted": [], "words": []}
# a participant no text or game data names
UNSTATED = {"in_source": "not-stated", "basis": "", "sources": []}
IDENTITY = {"schema": "2.0.0", "speaker": UNSTATED, "addressee": UNSTATED}
REACH = {"class": "reachable", "source": "not studied", "evidence": ""}
GAME_INDEX = ("where_texts", "map_order", "codec")
GAME_FILLS = ("contexts", "globals", "code", "dependencies", "fragments")
# index fields the reader rebuilds
REBUILT = ("list", "notes", "fill_sources", "chunks")


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def default_status(translation: str) -> str:
    """The status a list row omits: approved with a translation."""
    return "approved" if translation else "untranslated"


def _limit_source(source: dict) -> dict:
    if list(source)[:3] != ["kind", "rule", "evidence"] or set(source) - {"kind", "rule", "evidence", "unverified"}:
        raise ValueError(f"unexpected limit source fields: {sorted(source)}")
    out = {"kind": source["kind"], "rule": provenance.canonical(source["rule"])}
    if source["evidence"] != "":
        out["evidence"] = source["evidence"]
    if "unverified" in source:
        if source["unverified"] is not True:
            raise ValueError("unverified limit sources are marked true")
        out["unverified"] = True
    return out


class Store:
    """The tables of one language and its game."""

    def __init__(self):
        self.tables = {name: Table() for name in ("windows", "limit_sources", "consumers", "slot_details", "rules")}
        self.reach = Table()

    def source(self, value: dict) -> int:
        return self.tables["limit_sources"].index(_limit_source(value))

    def consumer(self, value: dict) -> int:
        keys = list(value)
        if keys[0] != "name" or keys[-1] != "source":
            raise ValueError(f"unexpected consumer fields: {keys}")
        out = {key: item for key, item in value.items() if key not in ("name", "source")}
        if value["name"] != value["source"]["rule"]:
            out = {"name": value["name"], **out}
        return self.tables["consumers"].index({**out, "source": self.source(value["source"])})

    def window(self, value: dict | None) -> int | None:
        if value is None:
            return None
        out = dict(value)
        if value.get("sources") is not None:
            out["sources"] = {key: self.source(item) for key, item in value["sources"].items()}
        if value.get("consumers") is not None:
            out["consumers"] = [self.consumer(item) for item in value["consumers"]]
        if value.get("slot_details") is not None:
            out["slot_details"] = {key: self.tables["slot_details"].index(item)
                                   for key, item in value["slot_details"].items()}
        return self.tables["windows"].index(out)

    def reach_of(self, value: dict) -> int | None:
        if list(value) != list(REACH):
            raise ValueError(f"unexpected reach fields: {list(value)}")
        out = without_defaults(value, REACH)
        return self.reach.index(out) if out else None


def _entry_parts(store: Store, e: dict) -> tuple[dict, dict]:
    """The game part and the language part of an entry."""
    unknown = set(e) - set(ENTRY)
    context = e["context"]
    extra = set(context) - set(CONTEXT)
    if extra:
        raise ValueError(f"{e['id']}: unexpected context fields: {sorted(extra)}")
    game = {}
    if e.get("tokens", []) != []:
        game["tokens"] = e.get("tokens")   # null: a record without tokens
    if "source_sha256" in e:
        game["source_sha256"] = e["source_sha256"]
    shared = without_defaults({key: context[key] for key in (*GAME_CONTEXT, "identity") if key in context},
                      {**GAME_CONTEXT, "identity": IDENTITY})
    if shared:
        game["context"] = shared
    language = without_defaults({key: e[key] for key in LANGUAGE_ENTRY if key in e}, LANGUAGE_ENTRY)
    for key in ("note", "translator_notes"):   # a record without the field
        if key not in e:
            language[key] = None
    if e.get("rules"):
        language["rules"] = [store.tables["rules"].index(rule) for rule in e["rules"]]
    if "note_ids" in e:
        language["note_ids"] = e["note_ids"]
    language.update(without_defaults({key: context[key] for key in LANGUAGE_CONTEXT if key in context}, LANGUAGE_CONTEXT))
    for key in sorted(unknown):   # kept as written, after the known fields
        language[key] = e[key]
    return game, language


def _row(store: Store, row: dict, e: dict, n: int) -> tuple[dict, dict]:
    """The game columns and the language columns of a list row."""
    expected = {"id": e["id"], "chunk": n, "status": e["status"], "translation": e["translation"],
                "kind": (e["window"] or {}).get("kind"), "reach": e["reach"]["class"], "maps": e["context"]["maps"]}
    for key, value in expected.items():
        if row.get(key) != value:
            raise ValueError(f"{e['id']}: list row {key} differs from its entry")
    if set(row) - {*expected, "owner", "shortened", "problems"} or row.get("owner") != e.get("owner"):
        raise ValueError(f"{e['id']}: unexpected list row fields")
    game = {"id": e["id"]}
    if (reach := store.reach_of(e["reach"])) is not None:
        game["reach"] = reach
    if row["maps"]:
        game["maps"] = row["maps"]
    language = {}
    if row["status"] != default_status(row["translation"]):
        language["status"] = row["status"]
    for key in ("owner", "translation"):
        if row.get(key):
            language[key] = row[key]
    if (window := store.window(e["window"])) is not None:
        language["window"] = window
    for key in ("shortened", "problems"):
        if row.get(key):
            language[key] = row[key]
    return game, language


def _store(path: Path, data: bytes) -> None:
    if path.is_file() and path.read_bytes() == data:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".part-", delete=False) as stream:
        stream.write(data)
    os.replace(stream.name, path)


def write_part(directory: Path, doc: dict) -> str:
    """Write a game part once, named by its content; its file name."""
    data = _json(doc).encode("utf-8")
    name = hashlib.sha256(data).hexdigest() + ".json"
    _store(directory / name, data)
    return name


def copy_parts(source, target) -> None:
    """Add the game parts a staged generation wrote to the served directory."""
    for path in sorted(Path(source).glob("*.json")):
        _store(Path(target) / path.name, path.read_bytes())


TERM_EXTRA = ("declension", "sources", "variants", "confidence")
TERM_USES = ("in_source", "in_translation", "form_only", "shortened")


def _terms(terms: dict, glossary: dict) -> dict:
    rows = []
    for row in terms["terms"]:
        own = {key: value for key, value in row.items() if key != "id" and key not in TERM_USES + TERM_EXTRA}
        if row["id"] in glossary:
            if _json(own) != _json(glossary[row["id"]]):
                raise ValueError(f"{row['id']}: term row differs from the glossary")
            own = {}
        rows.append({"id": row["id"], **own, **{key: row[key] for key in TERM_EXTRA},
                     **{key: row[key] for key in TERM_USES if row[key]}})
    return {"schema": terms["schema"], "terms": rows, "slots": terms["slots"]}


def write(out, index: dict, chunks: list[dict], terms: dict | None = None, source: dict | None = None) -> None:
    """Write the language directory `out` and its game parts in `out/../shared`."""
    from .editordata import SCHEMA, progress_of
    out = Path(out)
    parts = out.parent / PARTS
    store = Store()
    game_rows, rows, descriptors, files = [], [], [], {}
    at = 0
    for n, (chunk, descriptor) in enumerate(zip(chunks, index["chunks"], strict=True)):
        game_entries, language_entries = [], []
        for e in chunk["entries"]:
            game_row, row = _row(store, index["list"][at], e, n)
            game_rows.append(game_row)
            rows.append(row)
            game, language = _entry_parts(store, e)
            game_entries.append(game)
            language_entries.append(language)
            at += 1
        name = write_part(parts, {"schema": SCHEMA, "part": "entries", "entries": game_entries})
        files[descriptor["path"]] = {"schema": SCHEMA, "entries": language_entries}
        descriptors.append({**descriptor, "part": f"../{PARTS}/{name}"})
    if at != len(index["list"]):
        raise ValueError("list rows without chunk entries")
    notes = index.get("notes") or {"note": []}
    shared_notes = [note for note in notes["note"] if note["scope"]["kind"] == "shared"]
    position = {id(note): n for n, note in enumerate(shared_notes)}
    fills = index.get("fill_sources")
    game = {"schema": SCHEMA, "part": "index", "messages": provenance.MESSAGES,
            **{key: index[key] for key in GAME_INDEX if key in index},
            "list": game_rows, "reach": store.reach.rows, "notes": shared_notes,
            **({"fill_sources": {key: fills[key] for key in GAME_FILLS if key in fills}} if fills else {})}
    if source is not None:
        game["source"] = write_part(parts, {**source, "schema": SCHEMA, "part": "source"})
    stored = {"schema": SCHEMA, "shared": f"../{PARTS}/{write_part(parts, game)}",
              **{key: value for key, value in index.items() if key not in (*GAME_INDEX, *REBUILT, "schema")},
              **({"fill_sources": {key: value for key, value in fills.items() if key not in GAME_FILLS}
                  if fills is not None else None} if "fill_sources" in index else {}),
              **({"notes": {**notes, "note": [position.get(id(note), note) for note in notes["note"]]}}
                 if "notes" in index else {}),
              "chunks": descriptors, "list": rows,
              **{name: table.rows for name, table in store.tables.items()},
              "progress": progress_of(index["list"])}
    shutil.rmtree(out / "entries", ignore_errors=True)
    (out / "entries").mkdir(parents=True)
    for path, doc in files.items():
        (out / path).write_text(_json(doc), encoding="utf-8")
    if terms and index.get("terms"):
        (out / index["terms"]["path"]).write_text(_json(_terms(terms, index.get("glossary") or {})),
                                                  encoding="utf-8")
    (out / "index.json").write_text(_json(stored), encoding="utf-8")


def identity(path) -> dict:
    """The envelope of a stored language index: schema, profile, language, rom, progress, its entry count."""
    from .editordata import progress_of
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return {**{key: doc.get(key) for key in ("schema", "profile", "language", "rom")},
            "progress": doc["progress"] if "progress" in doc else progress_of(doc.get("list", [])),
            "entries": len(doc.get("list", []))}


def referenced(index_path) -> set[Path]:
    """The game parts a stored language index names."""
    index_path = Path(index_path)
    doc = json.loads(index_path.read_text(encoding="utf-8"))
    names = [doc["shared"], *(chunk["part"] for chunk in doc.get("chunks", []))]
    paths = {(index_path.parent / name).resolve() for name in names}
    game = (index_path.parent / doc["shared"]).resolve()
    if game.is_file() and (source := json.loads(game.read_text(encoding="utf-8")).get("source")):
        paths.add((game.parent / source).resolve())
    return paths


def prune(root) -> list[Path]:
    """Remove the game parts under `root` no language index or texts file names; the removed paths."""
    from ..data.pokedextexts import FILE, part_path
    root = Path(root)
    kept = set()
    for path in root.rglob("index.json"):
        try:
            kept |= referenced(path)
        except (KeyError, TypeError, ValueError):   # not a stored language index
            continue
    for path in root.rglob(FILE):   # the English of the Pokedex texts
        if (part := part_path(path)) is not None:
            kept.add(part.resolve())
    removed = []
    for path in root.glob(f"*/{PARTS}/*.json"):
        if PART_NAME.fullmatch(path.name) and path.resolve() not in kept:
            path.unlink()
            removed.append(path)
    return removed
