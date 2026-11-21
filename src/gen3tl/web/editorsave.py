"""Local text and note drafts with staged publication."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import tempfile
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from ..core.terminal import line
from .editorimport import _object, base_state, entry_state
from .editorserve import PreviewHandler
from ..catalog import entrynotes, extract
from ..catalog.po import KEY_ONLY, SOURCE_FIELD, key_only, read, write


STAMP = ".data-key"   # the data key, beside index.json
LOCAL_AUTHOR = "local"   # the author of notes a local save adds
SAVE_LIMIT = 5 * 1024 * 1024   # the export limit of submissions


def data_key(catalog, profile_path, toolkit: str, inputs=()) -> str:
    """The key of the editor data a catalog yields: its files, the profile, the toolkit fingerprint and other inputs."""
    import hashlib
    digest = hashlib.sha256()

    def add(root, label):
        root = Path(root)
        digest.update(f"<{label}>".encode("utf-8"))
        if root.is_dir():
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
                    digest.update(path.read_bytes() + b"\0")
        elif root.is_file():
            digest.update(root.read_bytes() + b"\0")
        else:
            digest.update(b"absent\0")
    add(catalog, "catalog")
    add(profile_path, "profile")
    digest.update(toolkit.encode("utf-8") + b"\0")
    for path in inputs:
        add(path, Path(path).name)
    return digest.hexdigest()


def background_refresh(refresh, catalog, target, key, lock, scratch, log=line, after=None, attempts=3,
                       success="regenerated") -> bool:
    """Regenerate the editor data of a changed catalog while the previous data is served."""
    import time
    target = Path(target)
    for _ in range(attempts):
        started = time.monotonic()
        expected = key()
        stage = Path(tempfile.mkdtemp(prefix=".editor-refresh-", dir=scratch))
        try:
            try:
                refresh(catalog, stage / "view")
            except Exception as exc:   # the next start refreshes
                log("data", "failed")
                log("error", exc)
                return False
            with lock:
                if key() != expected:
                    log("data", "retrying")
                    continue
                if target.exists():
                    os.replace(target, stage / "previous")
                os.replace(stage / "view", target)
            if after:
                after()
            log("data", success)
            log("elapsed", f"{time.monotonic() - started:.0f} s")
            return True
        finally:
            shutil.rmtree(stage, ignore_errors=True)
    log("data", "stale")
    return False


class SaveConflict(ValueError):
    pass


def catalog_paths(directory, staged, catalog):
    """Keep generated file labels outside staging."""
    prefix = str(staged) + os.sep

    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "file" and isinstance(child, str) and child.startswith(prefix):
                    value[key] = str(catalog / child[len(prefix):])
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for path in directory.rglob("*.json"):
        doc = json.loads(path.read_text())
        visit(doc)
        path.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")))


class DraftStore:
    def __init__(self, catalog, editor, profile, language, rom_sha1, refresh, scratch=None,
                 effective_profile=None, project=None):
        self.catalog = Path(catalog).resolve()
        self.editor = Path(editor).resolve()
        self.profile, self.language, self.rom_sha1 = profile, language, rom_sha1
        self.refresh = refresh
        self.effective_profile = effective_profile
        self.project = project
        self.scratch = Path(scratch or self.catalog.parent).resolve()
        self.source = extract.path()   # the server threads run outside the project
        self.token = secrets.token_hex(32)
        self.lock = threading.RLock()

    def session(self):
        return {"profile": self.profile, "language": self.language,
                "rom_sha1": self.rom_sha1, "token": self.token}

    def snapshot(self):
        files = {}
        for path in sorted(self.catalog.rglob("*.po")):
            if path.is_symlink() or not path.resolve().is_relative_to(self.catalog):
                raise ValueError("catalog files must remain inside the selected project")
            files[path.relative_to(self.catalog)] = path.read_bytes()
        return files

    def note_changes(self, doc):
        """Validated note changes and the note files they produce."""
        from . import notechanges
        if not notechanges.records(doc):
            return [], notechanges.Application()
        if self.project is None or self.effective_profile is None:
            raise ValueError("note changes require the selected project")
        from ..catalog.catalog import load_catalog
        with self.project.activate(self.effective_profile):
            catalog = load_catalog(self.catalog, english=False)
            return notechanges.prepare(doc, self.project, self.effective_profile, catalog, author=LOCAL_AUTHOR,
                                       created=datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def write_notes(self, application):
        with self.project.activate(self.effective_profile):
            application.write(self.project, self.effective_profile)

    def save(self, doc):
        with self.lock:
            return self._save(doc)

    def _save(self, doc):
        required = {"profile", "language", "rom_sha1", "entries"}
        if not isinstance(doc, dict) or set(doc) - {"note_changes", "note_changes_schema"} != required:
            raise ValueError("save needs project identity and text entries or note changes")
        if any(doc[key] != value for key, value in self.session().items() if key != "token"):
            raise ValueError("save identity differs from the selected project")
        records = doc["entries"]
        if (not isinstance(records, list) or len(records) > 1000
                or not (records or doc.get("note_changes"))):
            raise ValueError("save needs between 1 and 1000 text entries or note changes")
        before = self.snapshot()
        notes_path = entrynotes.path(self.catalog)
        notes_before = notes_path.read_bytes() if notes_path.exists() else None
        note_changes, application = self.note_changes(doc)
        note_files = {path: path.read_bytes() if path.exists() else None for path in application.files}
        if notes_path in application.texts:
            notes_after = application.texts[notes_path].encode("utf-8")
        else:
            notes_after = notes_before
        from ..catalog import keys
        aliases = keys.load(self.rom_sha1)   # entries are named by key or inventory id
        files, entries = {}, {}
        with tempfile.TemporaryDirectory(prefix="gen3tl-save-notes-") as projection:
            notes_view = notes_path
            if notes_after != notes_before:   # the comments of the notes in force after the save
                notes_view = Path(projection) / "entries.toml"
                notes_view.write_bytes(notes_after)
            for relative in before:
                header, items = read(self.catalog / relative, notes_path=notes_path)
                extract.english(items, self.source)   # a key-only catalog's English, which the record names
                items = entrynotes.project(items, notes_view, self.language, aliases)
                for key, expected in (("Project-Id-Version", self.profile), ("Language", self.language)):
                    if header.get(key, expected) != expected:
                        raise ValueError(f"{relative}: catalog identity differs")
                files[relative] = (header, items)
                for entry in items:
                    if aliases.key(entry.msgctxt) in entries:
                        raise ValueError(f"duplicate catalog id: {entry.msgctxt}")
                    entries[aliases.key(entry.msgctxt)] = (relative, entry)
        changed, seen = set(), set()
        for record in records:
            if (not isinstance(record, dict)
                    or set(record) - {"source_text", "state", "base_state"} != {"id", "base", "translation"}
                    or any(not isinstance(value, str) for value in record.values())):
                raise ValueError("each entry needs id, base and translation strings; source_text and states are optional")
            sid = aliases.key(record["id"])
            if self.effective_profile is not None:
                from ..build.keyboard import reject_owned
                reject_owned([aliases.entry_id(sid)], self.effective_profile)
            if sid in seen or sid not in entries:
                raise ValueError(f"duplicate or unknown catalog entry: {sid}")
            seen.add(sid)
            relative, entry = entries[sid]
            state = entry_state(record, entry)
            if ((record.get("source_text", entry.msgid), record["base"]) != (entry.msgid, entry.msgstr)
                    or not base_state(record, entry)):
                raise SaveConflict(f"{sid}: source or translation changed; reload before saving")
            target = record["translation"]
            if any(ord(c) < 32 and c not in "\n\t" for c in target):
                raise ValueError(f"{sid}: unsupported control character")
            if entry.set_translation(target, state, keep_approved=True):
                changed.add(relative)
        if not changed and not note_changes:
            return {"saved": 0, "entries": [], "notes": 0}
        self.editor.parent.mkdir(parents=True, exist_ok=True)
        self.scratch.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".editor-save-", dir=self.scratch))
        cleanup = True
        try:
            staged_catalog, staged_editor = stage / "po", stage / "view"
            if notes_after is not None:
                staged_notes = entrynotes.path(staged_catalog)
                staged_notes.parent.mkdir(parents=True)
                staged_notes.write_bytes(notes_after)
            for relative, raw in before.items():
                target = staged_catalog / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
            for relative in changed:
                header, items = files[relative]
                target = staged_catalog / relative
                write(target, items, header)
                actual_header, actual_items = read(target)
                written = [key_only(e) for e in items] if header.get(SOURCE_FIELD) == KEY_ONLY else items
                if actual_header != header or [asdict(e) for e in actual_items] != [asdict(e) for e in written]:
                    raise ValueError(f"{relative}: saved PO does not round-trip")
                msgfmt = shutil.which("msgfmt")
                if msgfmt:
                    result = subprocess.run([msgfmt, "--check-format", "-o", os.devnull, str(target)],
                                            capture_output=True, text=True)
                    if result.returncode:
                        raise ValueError(f"{relative}: {result.stderr.strip()[:300]}")
            backups = stage / "backup"
            backups.mkdir()
            prepared = {relative: (staged_catalog / relative).read_bytes() for relative in changed}
            installed, previous_editor, published_editor, written = [], False, False, {}
            try:
                if any(current_bytes(path) != data for path, data in note_files.items()):
                    raise SaveConflict("note files changed during saving; reload before saving")
                if note_files:   # the refreshed view shows the stored notes
                    self.write_notes(application)
                    written = {path: current_bytes(path) for path in note_files}
                self.refresh(staged_catalog, staged_editor)
                catalog_paths(staged_editor, staged_catalog, self.catalog)
                if not (staged_editor / "index.json").is_file():
                    raise ValueError("editor refresh did not produce an index")
                index = json.loads((staged_editor / "index.json").read_text())
                if (index.get("profile"), index.get("language"), index.get("rom", {}).get("sha1")) != (
                        self.profile, self.language, self.rom_sha1):
                    raise ValueError("refreshed editor identity differs")
                if self.snapshot() != before:
                    raise SaveConflict("catalog changed during saving; reload before saving")
                if current_bytes(notes_path) != notes_after:
                    raise SaveConflict("source notes changed during saving; reload before saving")
                for relative in sorted(changed):
                    current, backup = self.catalog / relative, backups / relative
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(current, backup)
                    installed.append(relative)
                    if backup.read_bytes() != before[relative]:
                        raise SaveConflict(f"{relative}: catalog changed during publication")
                    os.replace(staged_catalog / relative, current)
                if self.snapshot() != {**before, **prepared}:
                    raise SaveConflict("catalog changed during publication")
                if current_bytes(notes_path) != notes_after:
                    raise SaveConflict("source notes changed during publication")
                if self.editor.exists():
                    os.replace(self.editor, stage / "previous-view")
                    previous_editor = True
                os.replace(staged_editor, self.editor)
                published_editor = True
                if self.snapshot() != {**before, **prepared}:
                    raise SaveConflict("catalog changed during publication")
                if current_bytes(notes_path) != notes_after:
                    raise SaveConflict("source notes changed during publication")
            except BaseException:
                try:
                    conflicts = []
                    for relative in reversed(installed):
                        current = self.catalog / relative
                        if current.exists() and current.read_bytes() != prepared[relative]:
                            conflicts.append(str(relative))
                            continue
                        os.replace(backups / relative, current)
                    if written:
                        for path, data in note_files.items():
                            entrynotes.invalidate(path)
                            if current_bytes(path) != written[path]:
                                conflicts.append(str(path))
                            elif data is None:
                                path.unlink()
                            else:
                                path.write_bytes(data)
                    if published_editor:
                        os.replace(self.editor, stage / "failed-view")
                    if previous_editor:
                        os.replace(stage / "previous-view", self.editor)
                    if conflicts:
                        raise OSError("newer catalog or note edits retained: " + ", ".join(conflicts))
                except BaseException as exc:
                    cleanup = False
                    raise OSError(f"rollback failed; recovery files preserved at {stage}") from exc
                raise
        finally:
            if cleanup:
                shutil.rmtree(stage)
        return {"saved": len(seen) if changed else 0, "entries": records if changed else [],
                "notes": len(note_changes)}


def current_bytes(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None



class LocalHandler(PreviewHandler):
    def __init__(self, *args, store: DraftStore, **kwargs):
        self.store = store
        super().__init__(*args, **kwargs)

    def _local(self):
        port = self.server.server_address[1]
        host = self.headers.get("Host", "")
        return host in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _json(self, code, value):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._local():
            self._json(403, {"error": "local host required"})
            return
        with self.store.lock:
            if urlsplit(self.path).path == "/api/session":
                self._json(200, self.store.session())
            else:
                super().do_GET()

    def do_POST(self):
        if urlsplit(self.path).path != "/api/save":
            self._json(404, {"error": "unknown endpoint"})
            return
        if (not self._local()
                or self.headers.get("Origin") != "http://" + self.headers.get("Host", "")
                or not secrets.compare_digest(self.headers.get("X-Editor-Token", "").encode(),
                                               self.store.token.encode())):
            self._json(403, {"error": "same-origin editor session required"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= SAVE_LIMIT or self.headers.get_content_type() != "application/json":
                raise ValueError("save requires a JSON document of at most 5 MiB")
            self.connection.settimeout(10)
            try:
                doc = json.loads(self.rfile.read(length), object_pairs_hook=_object)
            except RecursionError:
                raise ValueError("the save nests too deeply") from None
            result = self.store.save(doc)
        except SaveConflict as exc:
            self._json(409, {"error": str(exc)})
        except (ValueError, OSError) as exc:
            self._json(400, {"error": str(exc)})
        else:
            self._json(200, result)


def local_server(web, data, store, port=8000):
    handler = partial(LocalHandler, directory=str(web), data=data, store=store)
    return ThreadingHTTPServer(("127.0.0.1", port), handler)
