"""Approval of the translated entries that a played release holds unchanged."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import entrynotes, keys
from .po import parse_text, read, write

VERSION = re.compile(r"\d+\.\d+\.\d+")


@dataclass
class Played:
    tag: str
    approved: int = 0
    kept: int = 0   # translated entries that differ from the tag
    files: list[Path] = field(default_factory=list)   # the files an approval changes


def _git(root: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *argv], capture_output=True)


def repository(root: Path) -> Path:
    """The top level of the git repository that holds `root`."""
    top = _git(root, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        raise ValueError(f"{root}: not a git repository")
    return Path(os.fsdecode(top.stdout).rstrip("\n")).resolve()


def held(top: Path, tag: str, catalog: Path, key) -> dict[str, tuple[str, str, bool]]:
    """Text, state and fuzzy flag of each entry of `catalog` as the tagged commit holds it, by key."""
    folder = catalog.resolve().relative_to(top).as_posix()
    listed = _git(top, "ls-tree", "-r", "-z", "--name-only", tag, "--", folder)
    if listed.returncode != 0:
        raise ValueError(f"git ls-tree: {os.fsdecode(listed.stderr).strip() or 'failed'}")
    entries = {}
    for name in os.fsdecode(listed.stdout).split("\0"):
        if not name.endswith(".po"):
            continue
        shown = _git(top, "show", f"{tag}:{name}")
        if shown.returncode != 0:
            raise ValueError(f"git show: {os.fsdecode(shown.stderr).strip() or 'failed'}")
        for entry in parse_text(shown.stdout.decode("utf-8"))[1]:
            entries[key(entry.msgctxt)] = (entry.msgstr, entry.state, entry.fuzzy)
    return entries


def approve_played(root: Path, catalog: Path, code: str, version: str, *, dry_run: bool = False) -> Played:
    """Clear fuzzy on the translated entries that the tag <code>-v<version> holds with the same text and state."""
    if not VERSION.fullmatch(version):
        raise ValueError(f"{version}: not a version <major>.<minor>.<patch>")
    tag = f"{code}-v{version}"
    top = repository(root)
    if not catalog.resolve().is_relative_to(top):
        raise ValueError(f"{catalog}: not inside the git repository")
    if _git(top, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}").returncode != 0:
        raise ValueError(f"tag {tag} does not exist")
    key = keys.for_profile(None).key   # either side may carry keys
    played = held(top, tag, catalog, key)
    result = Played(tag)
    notes = entrynotes.path(catalog)
    for path in sorted(catalog.rglob("*.po")):
        header, entries = read(path, notes_path=notes)
        cleared = 0
        for entry in entries:
            if not entry.fuzzy or entry.state == "original":
                continue   # approved or untranslated
            if played.get(key(entry.msgctxt)) == (entry.msgstr, entry.state, True):
                entry.set_fuzzy(False)
                cleared += 1
            else:
                result.kept += 1
        if cleared:
            result.approved += cleared
            result.files.append(path)
            if not dry_run:
                write(path, entries, header, notes_path=notes)
    return result
