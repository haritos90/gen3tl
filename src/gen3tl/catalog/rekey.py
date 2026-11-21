"""Move a project from inventory ids to catalog keys."""

from __future__ import annotations

import json
import re
import tomllib
from collections import Counter
from pathlib import Path

from . import keys as keys_mod
from .po import read, write

GENERATED = re.compile(r"^(prev |next |inserted as \{[^}]+\} into |\{[^}]+\} may be )(\S+?)(: )")
SPECIES = "species: "
TEXT_PATTERNS = ("policy.toml", "rules.tsv", "rules.toml", "glossary.tsv", "term-index.tsv", "*.md", "research/*.tsv",
                 "research/*.md", "graphics/*.tsv", "batches/**/*.json", "batches/**/*.tsv", "batches/**/*.md")
PROFILE_SECTIONS = ("translator_notes", "policy_slots")


def rekey_comment(comment: str, aliases: keys_mod.Aliases) -> str:
    """A generated extracted comment with its entry ids written as keys."""
    if m := GENERATED.match(comment):
        return m[1] + aliases.key(m[2]) + m[3] + comment[m.end():]
    if comment.startswith(SPECIES):
        return SPECIES + ", ".join(aliases.key(x) for x in comment[len(SPECIES):].split(", "))
    return comment


def rekey_po(path: Path, aliases: keys_mod.Aliases, dry_run: bool = False) -> int:
    """Rewrite one catalog; the number of entries changed."""
    header, entries = read(path)
    changed = 0
    for e in entries:
        before = (e.msgctxt, list(e.extracted), list(e.translator))
        e.msgctxt = aliases.key(e.msgctxt)
        e.extracted = [rekey_comment(c, aliases) for c in e.extracted]
        e.translator = [aliases.rekey_text(c) for c in e.translator]
        changed += before != (e.msgctxt, e.extracted, e.translator)
    if changed and not dry_run:
        write(path, entries, header)
    return changed


def rekey_file(path: Path, aliases: keys_mod.Aliases, dry_run: bool = False) -> bool:
    text = path.read_text(encoding="utf-8")
    new = aliases.rekey_text(text)
    if new != text and not dry_run:
        path.write_text(new, encoding="utf-8")
    return new != text


def _profile_sections(lines: list[str], rewrite) -> list[str]:
    """The lines with the project sections of a profile rewritten."""
    section = None
    out = []
    for line in lines:
        if m := re.match(r"\s*\[\[?\s*([A-Za-z0-9_.\"-]+)\s*\]\]?\s*(#.*)?$", line):
            section = m[1].split(".")[0].strip('"')
        out.append(rewrite(line) if section in PROFILE_SECTIONS else line)
    return out


def rekey_profile(path: Path, aliases: keys_mod.Aliases, dry_run: bool = False) -> bool:
    """Rewrite the project sections of a profile file, line by line."""
    lines = path.read_text(encoding="utf-8").split("\n")
    out = _profile_sections(lines, aliases.rekey_text)
    if out != lines and not dry_run:
        path.write_text("\n".join(out), encoding="utf-8")
    return out != lines


def text_files(translation: Path) -> list[Path]:
    found = set()
    for pattern in TEXT_PATTERNS:
        found.update(p for p in translation.glob(pattern) if p.is_file())
    return sorted(found)


def profile_files(prof) -> list[Path]:
    """Writable overlay TOML fragments, or the passed flat file."""
    path = Path(getattr(prof, "path", prof))
    if not path.is_dir():
        return [path]
    from ..profile import schema
    if not (path / schema.GENERATED).is_file():
        raise ValueError(f"{path}: profile directory has no {schema.GENERATED}")
    return [file for file in schema.overlay_files(path) if file.suffix == ".toml"]


def migrate(prof, aliases: keys_mod.Aliases, projects, dry_run: bool = False) -> dict:
    """Rewrite the catalogs and files of `projects` (see the module)."""
    report = {"entries": 0, "catalogs": 0, "files": [], "unknown": []}
    for project in projects:
        for path in sorted(project.catalog.rglob("*.po")):
            n = rekey_po(path, aliases, dry_run)
            report["entries"] += n
            report["catalogs"] += bool(n)
            _, entries = read(path)
            report["unknown"] += [f"{path}: {e.msgctxt}" for e in entries
                                  if aliases.resolve(aliases.key(e.msgctxt)) is None]
        report["files"] += [str(p) for p in text_files(project.translation) if rekey_file(p, aliases, dry_run)]
    shared = sorted({p for project in projects for p in (project.notes, project.graphics) if p and p.is_file()})
    report["files"] += [str(p) for p in shared if rekey_file(p, aliases, dry_run)]
    report["files"] += [str(p) for p in profile_files(prof) if rekey_profile(p, aliases, dry_run)]
    return report


class KeyMap:
    """The key pairs of a merge's match list, applied at once."""

    def __init__(self, matches, retained=()):
        self.mapping: dict[str, str] = {}
        destinations: dict[str, str] = {}
        unmatched: set[str] = set()
        for match in matches:
            if match.outcome == "obsolete":
                if match.old:
                    unmatched.add(match.old)
                continue
            if not match.old or not match.key or match.old == match.key:
                continue
            if match.old in self.mapping and self.mapping[match.old] != match.key:
                raise ValueError(f"match list maps {match.old} to both {self.mapping[match.old]} and {match.key}")
            if match.key in destinations and destinations[match.key] != match.old:
                raise ValueError(f"match list maps {destinations[match.key]} and {match.old} "
                                 f"to the same key {match.key}")
            self.mapping[match.old] = match.key
            destinations[match.key] = match.old
        for old in retained:
            if old and old not in self.mapping:
                unmatched.add(old)
        self.unmatched = sorted(unmatched)
        self._pattern = None
        if self.mapping:
            old_keys = sorted(self.mapping, key=len, reverse=True)
            self._pattern = re.compile(r"(?<![\w/-])(" + "|".join(re.escape(k) for k in old_keys)
                                       + r")(?![\w/-])")

    def rewrite(self, text: str) -> tuple[str, Counter]:
        """The rewritten text and the occurrence count of each old key."""
        if self._pattern is None:
            return text, Counter()
        counts = Counter()

        def mapped(match):
            counts[match[1]] += 1
            return self.mapping[match[1]]

        return self._pattern.sub(mapped, text), counts

    def replace(self, text: str) -> tuple[str, int]:
        """The text with every mapped old key token written as its new key, and the count."""
        new, counts = self.rewrite(text)
        return new, sum(counts.values())

    def map_text(self, text: str) -> str:
        return self.replace(text)[0]


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate definition {key}")
        result[key] = value
    return result


def _validate_rewritten(path: Path, text: str) -> None:
    suffix = path.suffix.lower()
    if suffix not in (".json", ".toml"):
        return
    try:
        if suffix == ".json":
            document = json.loads(text, object_pairs_hook=_object)
            if (path.name == "source.json" and path.parent.parent.name == "batches"
                    and isinstance(document, list)):
                seen = set()
                for row in document:
                    if isinstance(row, dict) and isinstance(row.get("id"), str):
                        if row["id"] in seen:
                            raise ValueError(f"duplicate definition {row['id']}")
                        seen.add(row["id"])
            return
        doc = tomllib.loads(text)
        for section in ("note", "translator_notes"):
            rows = doc.get(section, [])
            if not isinstance(rows, list):
                continue
            seen = set()
            for row in rows:
                if isinstance(row, dict) and "id" in row:
                    if row["id"] in seen:
                        raise ValueError(f"duplicate definition {row['id']}")
                    seen.add(row["id"])
    except ValueError as exc:
        # Parser details can include catalog text.
        if isinstance(exc, json.JSONDecodeError):
            detail = f"line {exc.lineno}, column {exc.colno}"
        elif isinstance(exc, tomllib.TOMLDecodeError):
            detail = "invalid TOML"
        else:
            detail = exc
        raise ValueError(f"{path}: rewritten {suffix[1:].upper()} has colliding definitions or invalid syntax: "
                         f"{detail}") from None


def remap_file(path: Path, keymap: KeyMap, dry_run: bool = False) -> tuple[bool, int]:
    """Rewrite one key-named file; (changed, number of rewritten keys)."""
    text = path.read_text(encoding="utf-8")
    new, count = keymap.replace(text)
    if new != text:
        _validate_rewritten(path, new)
        if not dry_run:
            path.write_text(new, encoding="utf-8")
    return new != text, count


def _profile_text(text: str, keymap: KeyMap) -> tuple[str, Counter]:
    counts = Counter()

    def rewrite(line):
        new, found = keymap.rewrite(line)
        counts.update(found)
        return new

    out = _profile_sections(text.split("\n"), rewrite)
    return "\n".join(out), counts


def remap_profile(path: Path, keymap: KeyMap, dry_run: bool = False) -> bool:
    """Rewrite the project sections of a profile file with the key map."""
    text = path.read_text(encoding="utf-8")
    new, _ = _profile_text(text, keymap)
    if new != text:
        _validate_rewritten(path, new)
        if not dry_run:
            path.write_text(new, encoding="utf-8")
    return new != text


def _scope_files(translations, references, profiles) -> list[tuple[Path, bool]]:
    selected = {}

    def add(path, profile):
        path = Path(path)
        identity = path.resolve()
        if identity in selected:
            if selected[identity][1] != profile:
                raise ValueError(f"{path}: selected as both a full file and a profile-only file")
            return
        selected[identity] = (path, profile)

    for translation in translations:
        root = Path(translation)
        for path in ([root] if root.is_file() else text_files(root)):
            add(path, False)
    for path in references:
        add(path, False)
    for prof in profiles:
        for path in profile_files(prof):
            add(path, True)
    return sorted(selected.values(), key=lambda item: str(item[0]))


def remap_files(matches, retained=(), translations=(), references=(), profiles=(),
                dry_run: bool = False) -> dict:
    """Validate the complete plan before rewriting selected key-named files."""
    keymap = KeyMap(matches, retained)
    plans = []
    for path, profile in _scope_files(translations, references, profiles):
        text = path.read_text(encoding="utf-8")
        new, counts = _profile_text(text, keymap) if profile else keymap.rewrite(text)
        if new != text:
            _validate_rewritten(path, new)
            plans.append((path, new, counts))
    report = {"files": [], "rewrites": 0, "unmatched": keymap.unmatched, "pairs": []}
    for path, new, counts in plans:
        report["files"].append(str(path))
        report["rewrites"] += sum(counts.values())
        report["pairs"] += [{"file": str(path), "old": old, "new": keymap.mapping[old], "count": count}
                            for old, count in counts.items()]
    if not dry_run:
        for path, new, _ in plans:
            path.write_text(new, encoding="utf-8")
    return report
