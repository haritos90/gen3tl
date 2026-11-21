"""Review proposed notes and publish their derived catalog and editor views."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from ..catalog import entrynotes, keys
from ..catalog.catalog import load_catalog
from ..catalog.po import read, write
from ..core import notes
from ..project import Registry
from ..web import notechanges


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True)
    if result.returncode:
        raise ValueError("note base: Git read failed")
    return result.stdout


def _branch_ids(project, prof, current: dict, base: str) -> list[str]:
    root = project.registry_root.resolve()
    revision = _git(root, "rev-parse", "--verify", "--end-of-options", base + "^{commit}").decode().strip()
    tracked = set(_git(root, "ls-tree", "-r", "--name-only", "-z", revision).decode().split("\0"))
    previous = {}
    for path, shape in notechanges._containers(project, prof).items():
        relative = path.relative_to(root).as_posix()
        if relative in tracked:
            previous.update(notechanges._stored(shape, _git(root, "show", f"{revision}:{relative}")))
    return sorted(nid for nid, record in current.items()
                  if record.get("status") == "proposed" and record["subject"]["kind"] in notechanges.APPLIED
                  and record != previous.get(nid))


@dataclass
class Acceptance:
    changes: list[dict]
    application: notechanges.Application
    catalogs: list[Path]
    projects: list

    def write(self, project, prof, refresh) -> None:
        """Restore notes, catalogs and editor views if publication fails."""
        paths = {*self.application.files, *self.catalogs}
        original = {path: path.read_bytes() if path.exists() else None for path in paths}
        installed = []
        project.build.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".note-accept-", dir=project.build) as tmp:
            stage = Path(tmp)
            try:
                self.application.write(project, prof)
                for path in self.catalogs:
                    header, entries = read(path)
                    write(path, entries, header)
                outputs = []
                for index, peer in enumerate(self.projects):
                    outputs.extend(refresh(peer, stage / str(index)))
                for index, (source, target) in enumerate(outputs):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    backup = stage / f"previous-{index}" if target.exists() else None
                    if backup is not None:
                        os.replace(target, backup)
                    installed.append((target, backup))
                    os.replace(source, target)
            except BaseException:
                for target, backup in reversed(installed):
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink(missing_ok=True)
                    if backup is not None:
                        os.replace(backup, target)
                for path, content in original.items():
                    entrynotes.invalidate(path)
                    if content is None:
                        path.unlink(missing_ok=True)
                    else:
                        path.write_bytes(content)
                raise


def prepare(project, prof, ids, *, base=None, export=None, reviewer: str, date: str, checked=()) -> Acceptance:
    """Validate the complete selection before planning any writes."""
    if sum((bool(ids), base is not None, export is not None)) != 1:
        raise ValueError("select note IDs, --base or --input, exclusively")
    notes._text(reviewer, "reviewer")
    notes._date(date, "review date", timestamp=True)
    notechanges._plain(reviewer, "reviewer")
    catalog = load_catalog(project.catalog, english=False)
    subjects, current = notechanges.inventory(project, prof, catalog)
    changes = []
    if export is not None:
        from ..web.editorimport import read_export
        document = read_export(Path(export), prof.name, project.language, prof.sha1, catalog,
                               carried=("note_changes",))["document"]
        if any(document.get(key) for key in ("entries", "pictures", "terms", "glyphs", "keyboard", "engine_rules")):
            raise ValueError("note acceptance requires a note-only export")
        changes = notechanges.validate(document, project, prof, catalog, author=reviewer, created=date)
        if not changes:
            raise ValueError("note acceptance requires note changes")
    ids = _branch_ids(project, prof, current, base) if base is not None else list(ids)
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate selected note ID")
    foreign = notechanges._other_identities(project)
    selected = {}
    for nid in ids:
        if nid not in current:
            raise ValueError(f"{nid}: unknown note")
        record = deepcopy(current[nid])
        notechanges._plain(record, nid)
        if nid in foreign:
            raise ValueError(f"{nid}: note identity belongs to another language project")
        if notechanges.legacy(record):
            raise ValueError(f"{nid}: legacy notes require --input with their exact base")
        if record["subject"]["kind"] not in notechanges.APPLIED:
            raise ValueError(f"{nid}: unsupported contributed note subject")
        if record["status"] != "proposed":
            raise ValueError(f"{nid}: expected proposed note")
        notechanges._subject(record, subjects, project.language)
        for related in record.get("related_notes", []):
            if related not in current:
                raise ValueError(f"{nid}: unknown related note {related}")
        selected[nid] = record
        changes.append({"operation": "edit", "id": nid, "subject": record["subject"],
                        "base": current[nid], "proposed": record})
    selected.update((change["id"], change["proposed"]) for change in changes if change["proposed"] is not None)
    seen = set()
    for nid, number in checked:
        if nid not in selected:
            raise ValueError(f"{nid}: checked source requires a selected note")
        try:
            index = int(number)
        except ValueError:
            raise ValueError(f"{nid}: source index must be a positive integer") from None
        sources = selected[nid].get("sources", [])
        if not 1 <= index <= len(sources):
            raise ValueError(f"{nid}: unknown source index {index}")
        if (nid, index) in seen:
            raise ValueError(f"{nid}: duplicate checked source {index}")
        seen.add((nid, index))
        sources[index - 1]["verification"] = "checked"
    for nid, record in selected.items():
        record.update(status="accepted", reviewed_by=reviewer, reviewed_at=date)
        notes.validate_note(record, where=nid)
    application = notechanges.plan(changes, project, prof, catalog)
    aliases = keys.for_profile(prof)
    affected = [notechanges.note_target(record) for change in changes for record in (change["base"], change["proposed"])
                if record is not None]
    catalogs = sorted({catalog[aliases.entry_id(record["subject"]["id"])][0]
                       for record in affected if record["subject"]["kind"] == "entry"
                       and record["scope"]["kind"] == "language"})
    projects = [project] if changes else []
    if any(record["scope"]["kind"] == "shared" for record in affected):
        projects += [peer for peer in Registry.load(project.registry_root).projects
                     if peer.profile == project.profile and peer.root == project.root
                     and peer.language != project.language]
    return Acceptance(changes, application, catalogs, projects)
