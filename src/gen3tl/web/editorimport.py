"""Route editor text drafts into independent review batches; glyph edits go to the pack's art files."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from ..catalog import keys
from ..catalog.batch import export_batch, import_path, put_section, translation_value
from ..catalog.catalog import load_catalog, nothing_to_translate
from ..catalog.po import translation_state
from .exportenvelope import validate


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def entry_state(record: dict, entry) -> str:
    """Keep legacy blanks distinct from explicit Off."""
    state = record.get("state", "text" if record["translation"] else
                       "empty" if entry.state == "empty" else "original")
    try:
        return translation_state(record["translation"], state)
    except ValueError as exc:
        raise ValueError(f"{record['id']}: {exc}") from exc


def base_state(record: dict, entry) -> bool:
    """Compare explicit state snapshots when supplied."""
    if "base_state" not in record:
        return True
    try:
        state = translation_state(record.get("base"), record["base_state"])
    except ValueError as exc:
        raise ValueError(f"{record['id']}: invalid base state") from exc
    return state == entry.state


def picture_bases(records: list[dict], prof) -> None:
    """Reject stale snapshots before any writes."""
    from .editorgfx import graphics_data
    if not isinstance(records, list):
        raise ValueError("the export's pictures must be a list")
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
            raise ValueError("each picture needs a string id")
        gid = record["id"]
        if "base" not in record:
            raise ValueError(f"{gid}: picture base is missing; recreate the edit from the current project")
        base = record["base"]
        if base is not None and not (isinstance(base, str) and re.fullmatch(r"[0-9a-f]{64}", base)):
            raise ValueError(f"{gid}: picture base must be a SHA-256 in lowercase hexadecimal or null")
    graphics = graphics_data(prof.get("graphics"), prof.project_root) or {}
    for record in records:
        gid = record["id"]
        edit = graphics.get("edits", {}).get(gid)
        if edit is not None and "label" in edit:
            raise ValueError(f"{gid}: the policy draws it from {edit['label']}; a picture cannot replace it")
        current = hashlib.sha256(base64.b64decode(edit["png"])).hexdigest() if edit else None
        if record["base"] != current:
            raise ValueError(f"{gid}: editor base differs from the current project picture; stale picture")


def read_export(path: Path, profile: str, language: str, rom_sha1: str, catalog: dict, carried=(),
                graphics_profile=None, rule_profile=None, rule_context=()) -> dict:
    """Validate identity and available source snapshots before writing; `carried` names keys the caller applies itself."""
    raw = path.read_bytes()
    try:
        doc = json.loads(raw, object_pairs_hook=_object)
    except RecursionError:
        raise ValueError("editor export nests too deeply") from None
    doc = validate(doc)
    if (doc.get("profile"), doc.get("language")) != (profile, language):
        raise ValueError("editor export profile or language differs from the selected project")
    if "rom_sha1" in doc and doc["rom_sha1"] != rom_sha1:
        raise ValueError("editor export ROM identity differs")
    if {"note_changes", "note_changes_schema"} & doc.keys():
        from .notechanges import records
        if records(doc) and "note_changes" not in carried:
            raise ValueError("note changes require the project submission review path")
    rule_patches = []
    if "engine_rules" in doc:
        if doc["engine_rules"]:
            if "engine_rules" not in carried or rule_profile is None:
                raise ValueError("engine rules require the project submission review path")
            from .policyrules import patches
            rule_patches = patches(rule_profile, doc["engine_rules"], *rule_context)
    if doc.get("pictures") and (graphics_profile is not None or "pictures" in carried):
        if graphics_profile is None:
            raise ValueError("picture validation needs the current project profile")
        picture_bases(doc["pictures"], graphics_profile)
    for key in ("pictures", "terms"):
        if key not in carried and key in doc and doc[key] != []:
            raise ValueError(f"web import handles texts, glyphs and keyboards only; export contains {key}")
    records = doc.setdefault("entries", [])
    keyboard = doc.get("keyboard")
    if not (records or doc.get("glyphs") or keyboard or any(doc.get(key) for key in carried)):
        raise ValueError("editor export needs a nonempty entries list, glyph edits or a keyboard")
    aliases = keys.load(rom_sha1)   # an export names entries by key or inventory id
    seen = set()
    digests = None   # a key-only entry's English: the snapshot's digest
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
            raise ValueError("each editor entry needs a string id")
        record["id"] = aliases.key(record["id"])
        sid = record["id"]
        if sid in seen:
            raise ValueError(f"duplicate editor entry: {sid}")
        seen.add(sid)
        if aliases.entry_id(sid) not in catalog:
            raise ValueError(f"{sid}: not in the selected catalog")
        if not isinstance(record.get("translation"), str) or not isinstance(record.get("note", ""), str):
            raise ValueError(f"{sid}: translation and note must be strings")
        entry = catalog[aliases.entry_id(sid)][1]
        record["state"] = entry_state(record, entry)
        if not base_state(record, entry):
            raise ValueError(f"{sid}: editor base state differs from the current catalog")
        if entry.owner:
            raise ValueError(f"{sid}: language-pack keyboard entries are read-only")
        if "source_text" in record and entry.keyed:
            from ..catalog.source import digest
            from .editorsnapshot import project_digests
            digests = project_digests() if digests is None else digests
            if aliases.entry_id(sid) not in digests:
                raise ValueError(f"{sid}: editor source_text cannot be checked without the English source catalog "
                                 "or the snapshot")
            if not isinstance(record["source_text"], str) or digest(record["source_text"]) != digests[aliases.entry_id(sid)]:
                raise ValueError(f"{sid}: editor source_text differs from the current catalog")
        for key, current in (("source_text", entry.msgid), ("base", entry.msgstr)):
            if key in record and record[key] != current and not (key == "source_text" and entry.keyed):
                raise ValueError(f"{sid}: editor {key} differs from the current catalog")
    baseline = {r["id"]: (catalog[aliases.entry_id(r["id"])][1].msgid,
                           catalog[aliases.entry_id(r["id"])][1].msgstr,
                           catalog[aliases.entry_id(r["id"])][1].state)
                for r in records}
    return {"document": doc, "raw": raw, "sha256": hashlib.sha256(raw).hexdigest(), "baseline": baseline,
            "filename": path.name, "profile": profile, "language": language, "rom_sha1": rom_sha1,
            "rule_patches": rule_patches}


def _name(value) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise ValueError(f"invalid batch name: {value!r}")
    return value


def _snapshot(directory: Path) -> dict | None:
    if not directory.exists():
        return None
    result = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            value = ("symlink", str(path.readlink()))
        elif path.is_dir():
            value = ("directory", path.stat().st_mode)
        else:
            value = ("file", path.stat().st_mode, hashlib.sha256(path.read_bytes()).hexdigest())
        result[path.relative_to(directory).as_posix()] = value
    return result


def _routes(records: list[dict], catalog: dict, plan_path: Path, digest: str,
            aliases: keys.Aliases = keys.EMPTY) -> dict:
    """Batches of the records (named by key) and their planned entries."""
    approved, pending, changed = [], [], set()
    for record in records:
        entry = catalog[aliases.entry_id(record["id"])][1]
        if (record["translation"], record["state"]) != (entry.msgstr, entry.state):
            changed.add(record["id"])
        ready = not entry.fuzzy and (entry.msgstr or "empty" in entry.flags or nothing_to_translate(entry))
        if not ready:
            pending.append(record)
        elif record["id"] in changed:
            approved.append(record)
    routes = {}
    if approved:
        routes[f"align-editor-{digest[:16]}"] = {"kind": "align", "ids": [r["id"] for r in approved],
                                                 "records": approved}
    if not pending:
        return routes
    if not plan_path.exists():
        if any(record["id"] in changed for record in pending):
            raise ValueError("untranslated entries need a batch plan; run gen3tl catalog batch-plan")
        return routes
    plan = json.loads(plan_path.read_text(), object_pairs_hook=_object)
    if not isinstance(plan, list):
        raise ValueError("batch plan must be a list")
    wanted = {r["id"] for r in pending}
    owners = {}
    for batch in plan:
        if not isinstance(batch, dict) or batch.get("kind") not in ("translate", "fill", "active"):
            continue
        ids = batch.get("ids")
        if not isinstance(ids, list) or any(not isinstance(sid, str) for sid in ids):
            raise ValueError("planned batch ids must be a list of strings")
        ids = [aliases.key(sid) for sid in ids]
        if not wanted.intersection(ids):
            continue
        name = _name(batch.get("name"))
        if name in routes:
            raise ValueError(f"duplicate planned batch name: {name}")
        if len(ids) != len(set(ids)) or any(aliases.entry_id(sid) not in catalog for sid in ids):
            raise ValueError(f"{name}: duplicate or unknown planned entries")
        for sid in wanted.intersection(ids):
            if sid in owners:
                raise ValueError(f"{sid}: belongs to several planned batches")
            owners[sid] = name
        routes[name] = {"kind": "planned", "ids": ids, "records": []}
    for record in pending:
        sid = record["id"]
        if sid not in owners:
            if sid in changed:
                raise ValueError(f"{sid}: no active translation plan; refresh the batch plan")
            continue
        routes[owners[sid]]["records"].append(record)
    return routes


def _prepare(destination: Path, staged: Path, name: str, route: dict, data: dict,
             catalog: dict, po_root: Path, limits: dict, matcher, source: str, notes) -> bool:
    digest = data["sha256"]
    aliases = keys.load(data["rom_sha1"])
    changed = {record["id"] for record in route["records"]
               if (record["translation"], record["state"]) !=
               (catalog[aliases.entry_id(record["id"])][1].msgstr,
                catalog[aliases.entry_id(record["id"])][1].state)}
    receipt = Path("editor-imports") / f"{digest}.json"
    provenance = receipt.with_suffix(".source.json")
    if destination.exists():
        if any(path.is_symlink() for path in destination.rglob("*")):
            raise ValueError(f"{name}: existing batch contains symbolic links")
        if (destination / receipt).exists():
            if (destination / receipt).read_bytes() != data["raw"]:
                raise ValueError(f"{name}: existing export receipt differs")
            previous = json.loads((destination / provenance).read_text())
            expected = [r["id"] for r in route["records"]]
            if [aliases.key(x) for x in previous.get("entries") or []] != expected:
                raise ValueError(f"{name}: existing import routing differs")
            return False
        status_path = destination / "status.json"
        status = json.loads(status_path.read_text())
        if not isinstance(status, dict) or status.get("name") != name:
            raise ValueError(f"{name}: invalid batch status")
        if status.get("approved") or list(destination.glob("review-*.json")):
            raise ValueError(f"{name}: review has started; existing review records cannot change")
        source_path = destination / "source.json"
        if not source_path.exists():
            raise ValueError(f"{name}: existing batch has no source snapshot")
        existing = json.loads(source_path.read_text())
        if (not isinstance(existing, list) or any(not isinstance(r, dict) for r in existing)
                or [aliases.key(r.get("id")) for r in existing] != route["ids"]):
            raise ValueError(f"{name}: existing source differs from its plan")
        for row in existing:
            path, entry = catalog[aliases.entry_id(row["id"])]
            if ((row.get("source_text"), row.get("current")) != (entry.msgid, entry.msgstr)
                    or row.get("current_state", entry.state) != entry.state):
                raise ValueError(f"{row['id']}: batch source snapshot is stale")
            if import_path(row["file"], po_root) != path.resolve():
                raise ValueError(f"{row['id']}: batch catalog path differs")
        shutil.copytree(destination, staged / name)
    else:
        if not changed:
            return False
        export_batch(name, route["ids"], po_root, limits, matcher, root=staged, notes=notes)
    directory = staged / name
    path = directory / "translation.json"
    translations = json.loads(path.read_text()) if path.exists() else {}
    if isinstance(translations, dict):
        translations = {aliases.key(sid): value for sid, value in translations.items()}
    if not isinstance(translations, dict) or any(sid not in route["ids"] for sid in translations):
        raise ValueError(f"{name}: invalid existing translation drafts")
    for value in translations.values():
        if value is not None:
            translation_value(value)
    for sid in route["ids"]:
        translations.setdefault(sid, None)
    for record in route["records"]:
        sid, text, state = record["id"], record["translation"], record["state"]
        if translations[sid] is not None and translation_value(translations[sid]) != (text, state):
            raise ValueError(f"{sid}: existing translation draft conflicts with the export")
        if sid in changed:
            translations[sid] = {"translation": text, "state": state} if state == "original" else text
    if not changed:
        return False
    path.write_text(json.dumps(translations, ensure_ascii=False, indent=1) + "\n")
    metadata = {"schema": "1.0.0", "profile": data["profile"], "language": data["language"],
                "rom_sha1": data["rom_sha1"], "filename": data["filename"], "sha256": digest,
                "source": source, "route": route["kind"], "entries": [r["id"] for r in route["records"]],
                "snapshots": {r["id"]: ("export-matched" if "source_text" in r and "base" in r
                                          else "current-catalog") for r in route["records"]}}
    (directory / receipt).parent.mkdir(exist_ok=True)
    (directory / receipt).write_bytes(data["raw"])
    (directory / provenance).write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    lines = [f"## Editor import {digest[:16]}", "", f"- Draft source: {source}.",
             f"- Export: {json.dumps(data['filename'], ensure_ascii=False)}.", f"- SHA-256: {digest}.",
             "- These drafts require independent accuracy, proofreading and editorial reviews.",
             "- Legacy exports use current catalog sources; editor source freshness is unknown.", ""]
    for record in route["records"]:
        lines.append(f"- {record['id']}: {json.dumps(record.get('note', ''), ensure_ascii=False)}")
    put_section(directory / "translator-notes.md", "\n".join(lines) + "\n")
    path = directory / "status.json"
    status = json.loads(path.read_text())
    status["approved"] = False
    status.setdefault("editor_imports", []).append(metadata)
    path.write_text(json.dumps(status, ensure_ascii=False, indent=1) + "\n")
    return True


def import_export(data: dict, po_root: Path, root: Path, plan_path: Path, limits: dict,
                  matcher=None, source: str = "contributor", dry_run: bool = False, notes=None) -> dict:
    """Stage every batch before publishing; leave catalogs unchanged."""
    if source not in ("maintainer", "contributor"):
        raise ValueError("draft source must be maintainer or contributor")
    from ..build.keyboard import reject_owned
    aliases = keys.load(data["rom_sha1"])
    reject_owned(aliases.entry_id(row["id"]) for row in data["document"]["entries"])
    catalog = load_catalog(po_root)
    for sid, previous in data["baseline"].items():
        found = catalog.get(aliases.entry_id(sid))
        if found is None or (found[1].msgid, found[1].msgstr, found[1].state) != previous:
            raise ValueError(f"{sid}: catalog changed after export validation")
    routes = _routes(data["document"]["entries"], catalog, plan_path, data["sha256"], aliases)
    root = root.resolve()
    result = {"sha256": data["sha256"], "dry_run": dry_run, "batches": []}
    if not routes:
        return result
    staged = Path(tempfile.mkdtemp(prefix=".editor-import-", dir=root.parent))
    cleanup = True
    try:
        changed, originals, prepared = [], {}, {}
        for name, route in routes.items():
            destination = root / _name(name)
            if destination.is_symlink() or not destination.resolve().is_relative_to(root):
                raise ValueError(f"{name}: batch must remain inside its project")
            originals[name] = _snapshot(destination)
            fresh = _prepare(destination, staged, name, route, data, catalog, po_root, limits, matcher, source, notes)
            result["batches"].append({"name": name, "kind": route["kind"], "entries": len(route["records"]),
                                      "changed": fresh})
            if fresh:
                changed.append(name)
                prepared[name] = _snapshot(staged / name)
        if dry_run:
            return result
        if not changed:
            return result
        root.mkdir(exist_ok=True)
        (staged / ".backup").mkdir()
        backups, installed = [], []
        try:
            for name in changed:
                destination = root / name
                if _snapshot(destination) != originals[name]:
                    raise ValueError(f"{name}: batch changed during import; retry after reviewing changes")
                if destination.exists():
                    backup = staged / ".backup" / name
                    os.replace(destination, backup)
                    backups.append((destination, backup))
                    if _snapshot(backup) != originals[name]:
                        raise ValueError(f"{name}: batch changed during publication")
                os.replace(staged / name, destination)
                installed.append(destination)
        except BaseException:
            try:
                for destination in reversed(installed):
                    if _snapshot(destination) != prepared[destination.name]:
                        raise OSError(f"{destination.name}: published batch changed before rollback")
                    shutil.rmtree(destination)
                for destination, backup in reversed(backups):
                    os.replace(backup, destination)
            except BaseException as exc:
                cleanup = False
                raise OSError(f"rollback incomplete; recovery files retained at {staged}") from exc
            raise
    finally:
        if cleanup:
            shutil.rmtree(staged)
    return result
