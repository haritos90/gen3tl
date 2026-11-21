"""Lay out the game project of a translation-stage profile."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ..profile.profile import Profile
from ..project import MANIFEST, Manifest, ProjectError

FOLDERS = ("translation", "source/snapshot", "data/pokedex", "releases", "site")
STAGES = ("legacy", "translation")


@dataclass
class Laid:
    root: Path
    written: list[Path] = field(default_factory=list)
    kept: list[Path] = field(default_factory=list)
    profile_files: list[Path] = field(default_factory=list)
    entries: int = 0
    notes: int = 0
    graphics: int = 0


def profile_texts(profile: Profile) -> list[Path]:
    """The hand-written files of a profile: its overlay, or the flat file."""
    from ..profile import schema
    path = Path(profile.path)
    if not path.is_dir():
        return [path]
    return [file for file in schema.overlay_files(path) if file.suffix == ".toml"]


def without_notes(text: str) -> str:
    """A profile text without its [[translator_notes]] tables."""
    out: list[str] = []
    skipping = False
    for line in text.split("\n"):
        if re.match(r"\s*\[", line):
            skipping = re.match(r"\s*\[\[\s*translator_notes\s*\]\]", line) is not None
            if skipping:
                while out and out[-1].startswith("#"):
                    out.pop()
                continue
        elif skipping:
            if line.startswith("#"):
                skipping = False
            elif not line.strip():
                skipping = False
                continue
            else:
                continue
        out.append(line)
    return "\n".join(out)


def without_graphics_paths(text: str) -> str:
    """A profile text without the project paths of its [graphics] table."""
    out, section = [], None
    for line in text.split("\n"):
        if m := re.match(r"\s*\[\[?\s*([A-Za-z0-9_.\"-]+)\s*\]\]?", line):
            section = m[1]
        elif section == "graphics" and re.match(r"\s*(inventory|previews)\s*=", line):
            continue
        out.append(line)
    return "\n".join(out)


def _rewrite(path: Path, change, expected) -> bool:
    """Apply a text change to a profile file when it leaves exactly `expected` data."""
    text = path.read_text(encoding="utf-8")
    new = change(text)
    if new == text:
        return False
    if tomllib.loads(new) != expected(tomllib.loads(text)):
        raise ProjectError(f"{path}: cannot move the project data out of the profile as text")
    path.write_text(new, encoding="utf-8")
    return True


def _drop_notes(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k != "translator_notes"}


def _drop_graphics_paths(doc: dict) -> dict:
    doc = dict(doc)
    if isinstance(doc.get("graphics"), dict):
        doc["graphics"] = {k: v for k, v in doc["graphics"].items() if k not in ("inventory", "previews")}
    return doc


def init(profile: Profile, root: Path, registry, rom=None) -> Laid:
    """Lay out the game project of `profile` under `root` (see the module)."""
    from .. import __version__
    from ..build.graphics import graphics_text, inventory_rows
    from ..core.graphics import graphics_document, preview_views, write_preview_views
    from . import extract
    from .guidance import write_notes
    stage = profile.get("support", {}).get("stage", "legacy")
    if stage not in STAGES:
        raise ProjectError(f"{profile.name}: project init needs a translation-stage profile; its stage is {stage}")
    root = Path(root).resolve()
    laid = Laid(root)
    manifest_path = root / MANIFEST
    if manifest_path.is_file():
        manifest = Manifest.read(manifest_path)
        if (manifest.profile, manifest.rom_sha1) != (profile.name, profile.sha1):
            raise ProjectError(f"{manifest_path}: belongs to {manifest.profile}")
        laid.kept.append(manifest_path)
    else:
        languages = tuple(p.language for p in registry.projects if p.profile == profile.name and p.root == root)
        default = registry.default_language if registry.default_language in languages else (languages or ("",))[0]
        manifest = Manifest(manifest_path, profile.name, profile.sha1, __version__, languages, default)
        root.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(manifest.text(), encoding="utf-8")
        laid.written.append(manifest_path)
    for folder in FOLDERS:
        (root / folder).mkdir(parents=True, exist_ok=True)
    source = root / "source"
    notes = source / "notes.toml"
    rows = profile.get("translator_notes", [])
    if notes.is_file():
        if rows:
            raise ProjectError(f"{profile.name}: translator notes in the profile and in {notes}")
        laid.kept.append(notes)
    else:
        write_notes(notes, rows)
        laid.written.append(notes)
        laid.notes = len(rows)
        laid.profile_files += [p for p in profile_texts(profile) if _rewrite(p, without_notes, _drop_notes)]
    graphics = source / "graphics.toml"
    section = profile.get("graphics", {})
    if graphics.is_file():
        laid.kept.append(graphics)
    else:
        inventory = root / section["inventory"] if section.get("inventory") else None
        rows = (graphics_document(inventory)["graphic"] if inventory and inventory.suffix == ".toml"
                else inventory_rows(inventory) if inventory else [])
        views = preview_views(root / section["previews"]) if section.get("previews") else {}
        text = graphics_text(rows)
        laid.written += write_preview_views(source / "graphics" / "views", views)
        graphics.write_text(text, encoding="utf-8")
        laid.written.append(graphics)
        laid.graphics = len(rows)
        laid.profile_files += [p for p in profile_texts(profile)
                               if _rewrite(p, without_graphics_paths, _drop_graphics_paths)]
    if rom is not None:
        entries = extract.entries(profile, rom, root / "build", root)
        pot = root / "build/catalog.pot"
        extract.write(pot, entries, profile)
        laid.written.append(pot)
        laid.entries = len(entries)
    return laid
