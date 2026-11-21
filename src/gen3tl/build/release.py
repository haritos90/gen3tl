"""The translation's release version, the @VERSION@ token and the retained builds of each version."""

from __future__ import annotations

import re
import hashlib
import json
import tomllib
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

TOKEN = "@VERSION@"
SOURCE = Path("translation/release.toml")
SEMVER = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
NAME_KEYS = ("output_name", "archive_name", "release_label")   # keys of earlier release files


class Names(NamedTuple):
    """The names of a release."""
    output: str    # stem of the ROM, its patch and its identity file
    archive: str   # file of the previous versions
    label: str     # in release titles and notes


def source(root: str | Path | None = None) -> Path:
    """The release file of the running project, else the workspace file under `root`."""
    from ..project import active
    project = active()
    if project is not None and (root is None or Path(root).resolve() == project.root):
        return project.release
    return Path(root or ".") / SOURCE


def load(path: str | Path | None = None) -> str:
    """The version from the release file; it must be MAJOR.MINOR.PATCH."""
    path = Path(path) if path is not None else source()
    with path.open("rb") as fh:
        document = tomllib.load(fh)
    named = [key for key in NAME_KEYS if key in document]
    if named:
        raise ValueError(f"{path}: remove {', '.join(named)}; the release names follow from the game, "
                         "the language and the version")
    version = document.get("version")
    if not isinstance(version, str) or not SEMVER.fullmatch(version):
        raise ValueError(f"{path}: version {version!r} is not MAJOR.MINOR.PATCH")
    return version


def expand(text: str, version: str | None) -> str:
    """Text with the version in place of the token; unchanged without a version."""
    return text if version is None else text.replace(TOKEN, version)


def game_title(profile) -> str:
    """The game title of release names: the ROM name of the profile, else the profile name."""
    title = profile.get("rom", {}).get("name") or profile.name
    return profile.name if any(c in title for c in '/\\\n\r\0') else title   # a file name holds no separator


def release_names(title: str, code: str, version: str) -> Names:
    """The names of a release, from the game title, the language code and the translation version."""
    if not isinstance(version, str) or not SEMVER.fullmatch(version):
        raise ValueError(f"invalid translation version {version!r}")
    mark = code.upper()
    tag = f"{mark} v{version}" if version.startswith("0.") else mark   # an early version shows its number
    return Names(f"{title} [{tag} by gen3tl]", f"{title} (Previous versions) [{mark} by gen3tl].zip",
                 f"{mark} by gen3tl")


def superseded_outputs(directory: Path, title: str, code: str, current: str) -> list[str]:
    """Stems of the current files that releases below 1.0.0 named by their version."""
    pattern = re.compile(re.escape(f"{title} [{code.upper()} v") + SEMVER.pattern + re.escape(" by gen3tl]"))
    stems = {path.stem for path in directory.glob("*.gba")} if directory.is_dir() else set()
    return sorted(stem for stem in stems if stem != current and pattern.fullmatch(stem))


def _drop_superseded(rom: Path, name: str, base: bytes, archive: Path) -> None:
    """Remove the current files of an earlier version that the archive reproduces."""
    from ..core import ups
    identity = rom.with_name(rom.stem + ".project.json")
    version = json.loads(identity.read_text(encoding="utf-8")).get("version") if identity.exists() else None
    delta = _archive_member(archive, f"{name}-{version}.ups") if version else None
    patch = rom.with_name(rom.stem + ".ups")
    if delta is None or ups.apply(base, delta) != rom.read_bytes():
        return   # unarchived bytes stay in place
    if patch.exists() and patch.read_bytes() != delta:
        return
    for path in (rom, patch, identity):
        path.unlink(missing_ok=True)


def build_names(profile, code: str | None, path: Path | None) -> Names:
    """The names of the current build: derived with a release file, else those of a bare workspace."""
    title = game_title(profile)
    if path is not None and path.exists():
        return release_names(title, code, load(path))
    return Names(profile.output_stem(code) if code else profile.name, f"{title}-{code}-archive.zip", code)


def history(path: Path) -> dict:
    """Read local build and gameplay records."""
    if not path.exists():
        return {"versions": []}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("versions"), list):
        raise ValueError(f"{path}: invalid translation history")
    return value


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


COMPANIONS = (".project.json", ".report.md")
KEEP = 2


def _order(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _retained(directory: Path, prefixes: list[str], suffix: str) -> dict[str, list[Path]]:
    """Versioned ROM files under each name prefix."""
    found: dict[str, list[Path]] = {}
    for prefix in prefixes:
        for path in sorted(directory.iterdir()) if directory.exists() else []:
            head, tail = path.name[:len(prefix) + 1], path.name[len(prefix) + 1:]
            version = tail[:-len(suffix)] if tail.endswith(suffix) else ""
            if head == f"{prefix}-" and SEMVER.fullmatch(version):
                found.setdefault(version, []).append(path)
    return found


def _archive_member(archive: Path, member: str) -> bytes | None:
    if not archive.exists():
        return None
    with zipfile.ZipFile(archive) as zf:
        return zf.read(member) if member in zf.namelist() else None


def _rotate(directory: Path, prefixes: list[str], suffix: str, name: str, archive: Path,
            keep: int, companions: tuple[str, ...], pack=None,
            kept_versions: set[str] | None = None) -> tuple[list[str], list[str]]:
    """Keep the newest versions as files; archive the rest."""
    found = _retained(directory, prefixes, suffix)
    if keep < 0:
        raise ValueError("retained version count cannot be negative")
    order = sorted(found, key=_order)
    old, kept = (order[:-keep], order[-keep:]) if keep else (order, [])
    if kept_versions is not None:   # follow the retained builds
        old = [v for v in order if v not in kept_versions]
        kept = [v for v in order if v in kept_versions]
    for version in kept:
        for path in found[version]:
            for ext in (suffix, *companions):
                source = path.with_name(path.name[:-len(suffix)] + ext)
                target = directory / f"{name}-{version}{ext}"
                if source == target or not source.exists():
                    continue
                if not target.exists():
                    source.rename(target)
                elif source.read_bytes() == target.read_bytes():
                    source.unlink()
                elif ext == suffix:
                    raise ValueError(f"{source}: differs from {target.name}; resolve before archiving")
    pending: list[tuple[Path, str, bytes]] = []
    for version in old:
        for path in found[version]:
            for ext in (suffix, *companions):
                source = path.with_name(path.name[:-len(suffix)] + ext)
                if source.exists():
                    data = source.read_bytes()
                    member = f"{name}-{version}{ext}"
                    if ext == suffix and pack:
                        member, data = pack(version, data)
                    pending.append((source, member, data))
    if pending:
        with zipfile.ZipFile(archive, "a", zipfile.ZIP_DEFLATED) as zf:
            members = set(zf.namelist())
            for source, member, data in pending:
                if member in members:
                    if zf.read(member) != data and not member.endswith(COMPANIONS):
                        raise ValueError(f"{archive.name}: {member} holds different bytes")
                    continue
                zf.writestr(member, data)
                members.add(member)
        # verify before removing originals
        with zipfile.ZipFile(archive) as zf:
            if zf.testzip() is not None:
                raise ValueError(f"{archive.name}: archive check failed")
            for source, member, data in pending:
                if zf.read(member) == data:
                    source.unlink()
                elif not member.endswith(COMPANIONS):
                    raise ValueError(f"{archive.name}: {member} holds different bytes")
    return old, kept


def _delta(base: bytes, data: bytes) -> bytes:
    """A UPS patch from the base ROM that reproduces `data` exactly."""
    from ..core import ups
    patch = ups.create(base, data)
    if ups.apply(base, patch) != data:
        raise ValueError("UPS delta does not reproduce the build")
    return patch


def _convert_archive(archive: Path, base: bytes, suffix: str) -> int:
    """Replace full ROM members by verified UPS deltas."""
    if not archive.exists():
        return 0
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        full = [n for n in names if n.endswith(suffix)]
        if not full:
            return 0
        temporary = archive.with_name(archive.name + ".tmp")
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as out:
            written = set()
            for n in names:
                data = zf.read(n)
                if n.endswith(suffix):
                    n, data = n[:-len(suffix)] + ".ups", _delta(base, data)
                if n in written:
                    continue
                out.writestr(n, data)
                written.add(n)
        with zipfile.ZipFile(temporary) as out:
            if out.testzip() is not None:
                raise ValueError(f"{temporary.name}: archive check failed")
            for n in full:
                from ..core import ups
                if ups.apply(base, out.read(n[:-len(suffix)] + ".ups")) != zf.read(n):
                    raise ValueError(f"{temporary.name}: delta for {n} does not reproduce it")
    temporary.replace(archive)
    return len(full)


def archive_builds(latest: Path, name: str, identity: dict, keep: int = KEEP,
                   base: bytes | None = None, archive_name: str | None = None) -> list[str]:
    """Keep the newest versions as files; move older ones into one archive."""
    directory, suffix = latest.parent, latest.suffix
    archive = directory / (archive_name or f"{name}-archive.zip")
    pack = None
    if base is not None:
        _convert_archive(archive, base, suffix)
        pack = lambda v, data: (f"{name}-{v}.ups", _delta(base, data))
    old, kept = _rotate(directory, list(dict.fromkeys([name, latest.stem])), suffix, name,
                        archive, keep, COMPANIONS, pack)
    moved = {v: f"{name}-{v}{suffix}" for v in old + kept}
    ledger = directory / "translation-history.json"
    records = history(ledger)
    for row in records["versions"]:
        if (row["profile"], row["language"]) != (identity["profile"], identity["language"]):
            continue
        if base is not None and row.get("archive") == archive.name and row["version"] not in kept:
            row["delta"] = f"{name}-{row['version']}.ups"   # archived as a full ROM
        if row["version"] in moved:
            row["file"] = moved[row["version"]]
            if row["version"] in old:
                row["archive"] = archive.name
                if base is not None:
                    row["delta"] = f"{name}-{row['version']}.ups"
            else:
                row.pop("archive", None)
                row.pop("delta", None)
    if ledger.exists() or records["versions"]:
        _write_json(ledger, records)
    return [f"{name}-{v}{suffix}" for v in old]


def archive_patches(patch: Path, name: str, earlier: list[str], keep: int = KEEP,
                    archive_name: str | None = None) -> list[str]:
    """Keep the patches of the retained builds; move the others into one archive."""
    archive = patch.parent / (archive_name or f"{name}-archive.zip")
    builds = set(_retained(patch.parent, [name], ".gba"))
    current = patch.name[len(name) + 1:-len(patch.suffix)]
    kept_versions = builds | {current} if builds and keep else None
    old, _ = _rotate(patch.parent, list(dict.fromkeys([name, *earlier])), patch.suffix, name,
                     archive, keep, (), kept_versions=kept_versions)
    return [f"{name}-{v}{patch.suffix}" for v in old]


def write_patch(path: Path, base: bytes, data: bytes) -> Path:
    """Write the verified UPS patch of a preserved build."""
    patch = _delta(base, data)
    if path.exists() and path.read_bytes() != patch:
        raise ValueError(f"{path}: version already contains a different patch")
    if not path.exists():
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_bytes(patch)
        temporary.replace(path)
    return path

def preserve_build(data: bytes, latest: Path, version: str, identity: dict,
                   changes: str = "", name: str | None = None, keep: int = KEEP,
                   base: bytes | None = None, archive_name: str | None = None) -> Path:
    """Preserve immutable versions before replacing the latest build."""
    if not SEMVER.fullmatch(version):
        raise ValueError(f"invalid translation version {version!r}")
    built_sha1 = hashlib.sha1(data).hexdigest()
    if identity.get("built_sha1") != built_sha1:
        raise ValueError("build identity does not match ROM bytes")
    name = name or latest.stem
    target = latest.with_name(f"{name}-{version}{latest.suffix}")
    archive = latest.with_name(archive_name or f"{name}-archive.zip")
    archived = _archive_member(archive, target.name)
    delta = _archive_member(archive, target.with_suffix(".ups").name) if base is not None else None
    if delta is not None:
        from ..core import ups
        archived = ups.apply(base, delta)
    if archived is not None and archived != data:
        raise ValueError(f"translation {version} already identifies different bytes; raise the version")
    ledger = latest.parent / "translation-history.json"
    records = history(ledger)
    matches = [r for r in records["versions"] if
               (r["profile"], r["language"], r["version"]) ==
               (identity["profile"], identity["language"], version)]
    if len(matches) > 1:
        raise ValueError(f"duplicate translation history for {version}")
    if matches and any(matches[0].get(k) != identity[k]
                       for k in ("rom_sha1", "built_sha1")):
        raise ValueError(f"translation {version} already identifies different bytes; raise the version")
    latest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("xb") as fh:
            fh.write(data)
    except FileExistsError:
        if target.read_bytes() != data:
            raise ValueError(f"{target}: version already contains different bytes; raise the version") from None
    metadata = dict(identity, version=version)
    manifest = target.with_suffix(".project.json")
    if manifest.exists():
        previous = json.loads(manifest.read_text(encoding="utf-8"))
        if any(previous.get(k) != metadata[k] for k in metadata):
            raise ValueError(f"{manifest}: existing version identity differs")
    else:
        _write_json(manifest, metadata)
    if not matches:
        records["versions"].append(dict(metadata, file=target.name, changes=changes,
                                        built_at=datetime.now(timezone.utc).isoformat(),
                                        gameplay="not recorded", playtests=[]))
    _write_json(ledger, records)
    temporary = latest.with_suffix(latest.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(latest)
    _write_json(latest.with_suffix(".project.json"), metadata)
    archive_builds(latest, name, identity, keep, base, archive_name)
    return target


def record_playtest(path: Path, profile: str, language: str, version: str,
                    status: str, note: str, notes: Path | None = None) -> dict:
    """Record explicit observations without inferring successful gameplay."""
    if status not in ("launched", "issues reported", "checked") or not note.strip():
        raise ValueError("playtest needs a valid status and a description")
    records = history(path)
    matches = [r for r in records["versions"] if
               (r["profile"], r["language"], r["version"]) == (profile, language, version)]
    if len(matches) != 1:
        raise ValueError(f"no unique build recorded for {profile}/{language} {version}")
    record = matches[0]
    record.setdefault("playtests", []).append({"at": datetime.now(timezone.utc).isoformat(),
                                              "status": status, "note": note})
    record["gameplay"] = status
    _write_json(path, records)
    update_notes(notes or path.with_name("release-notes.md"), records)
    return record


def _write_bytes(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def publish_patch(path: Path, base: bytes, data: bytes, version: str,
                  name: str, earlier: list[str], archive_name: str | None = None) -> Path:
    """Archive an immutable patch before replacing the current patch."""
    if not SEMVER.fullmatch(version):
        raise ValueError(f"invalid translation version {version!r}")
    patch = _delta(base, data)
    versioned = path.with_name(f"{name}-{version}.ups")
    archived = _archive_member(path.with_name(archive_name or f"{name}-archive.zip"), versioned.name)
    if archived is not None and archived != patch:
        raise ValueError(f"translation {version} already identifies different bytes; raise the version")
    path.parent.mkdir(parents=True, exist_ok=True)
    write_patch(versioned, base, data)
    archive_patches(versioned, name, earlier, keep=0, archive_name=archive_name)
    _write_bytes(path, patch)
    return path


def publish_build(data: bytes, latest: Path, version: str, identity: dict,
                  changes: str, name: str, base: bytes, earlier: list[str],
                  archive_name: str | None = None, release_label: str | None = None,
                  notes: Path | None = None, superseded: list[str] = ()) -> Path:
    """Archive every version, then replace the current release pair and drop superseded ones."""
    label = release_label or identity["language"]
    _release_key(label, version)
    descriptions = public_changes(changes)
    versioned = latest.with_name(f"{name}-{version}{latest.suffix}")
    preserve_build(data, versioned, version, identity, changes, name=name, keep=0, base=base,
                   archive_name=archive_name)
    for prefix in earlier:
        if _retained(latest.parent, [prefix], latest.suffix):
            archive_builds(latest.with_name(prefix + latest.suffix), name, identity, keep=0, base=base,
                           archive_name=archive_name)
    publish_patch(latest.with_suffix(".ups"), base, data, version, name, earlier, archive_name)
    _write_bytes(latest, data)
    _write_json(latest.with_suffix(".project.json"), dict(identity, version=version))
    archive = latest.with_name(archive_name or f"{name}-archive.zip")
    for stem in superseded:
        _drop_superseded(latest.with_name(stem + latest.suffix), name, base, archive)
    records = history(latest.parent / "translation-history.json")
    digest = hashlib.sha256(latest.with_suffix(".ups").read_bytes()).hexdigest()
    for row in records["versions"]:
        if (row["profile"], row["language"], row["version"]) == (identity["profile"], identity["language"], version):
            row["release_label"], row["patch_sha256"] = label, digest
            if descriptions or "release_notes" not in row:
                row["release_notes"] = descriptions
    _write_json(latest.parent / "translation-history.json", records)
    update_notes(notes or latest.parent / "release-notes.md", records, version=version, label=label,
                 changes=changes if descriptions else None, patch_sha256=digest)
    return latest


NOTE_HEADING = re.compile(r"^## \[([^\]\r\n]+)\] (\d+\.\d+\.\d+)[ \t]*$", re.MULTILINE)
PRIVATE_NOTE = re.compile(
    r"\b(?:tasks?|milestones?|decisions?|[a-z]+-owner|QA)\b|q-[a-z0-9]{6}\b"
    r"|^(?:Gameplay|Evidence|Archive|Member|Built|Verification|Apply to|Known issues):",
    re.IGNORECASE | re.MULTILINE)


def public_changes(value: str) -> list[str]:
    """Validate translation changes before publishing them."""
    lines = [line.removeprefix("- ").strip() for line in value.splitlines() if line.strip()]
    if any(PRIVATE_NOTE.search(line) or line.startswith(("#", "<!--")) for line in lines):
        raise ValueError("release changes must contain translation changes without workflow references")
    return lines


def _release_key(label: str, version: str) -> tuple[str, str]:
    if not label.strip() or any(c in label for c in "[]\r\n") or not SEMVER.fullmatch(version):
        raise ValueError("release notes need a language label and translation version")
    return label, version


def update_notes(path: Path, records: dict | None = None, *, version: str | None = None,
                 label: str | None = None, changes: str | None = None,
                 patch_sha256: str | None = None) -> None:
    """Write translation changes and compact patch hashes by labeled version."""
    sections: dict[tuple[str, str], dict] = {}
    if path.exists():
        text = path.read_text(encoding="utf-8")
        headings = list(NOTE_HEADING.finditer(text))
        for i, heading in enumerate(headings):
            key = _release_key(heading[1], heading[2])
            if key in sections:
                raise ValueError(f"{path}: duplicate release notes for {key}")
            stop = headings[i + 1].start() if i + 1 < len(headings) else len(text)
            body = text[heading.end():stop].strip()
            hashes = re.findall(r"^UPS SHA-256: `([a-f0-9]{64})`$", body, re.MULTILINE)
            body = re.sub(r"^UPS SHA-256: `[a-f0-9]{64}`\n?", "", body, flags=re.MULTILINE)
            sections[key] = {"changes": public_changes(body), "hash": hashes[0] if hashes else None}
    for row in (records or {}).get("versions", []):
        key = _release_key(row.get("release_label", row["language"]), row["version"])
        lines = public_changes("\n".join(row.get("release_notes", [])))
        section = sections.setdefault(key, {"changes": lines, "hash": None})
        if row.get("patch_sha256"):
            section["hash"] = row["patch_sha256"]
    if version is not None:
        key = _release_key(label or "", version)
        section = sections.setdefault(key, {"changes": [], "hash": None})
        if changes is not None:
            section["changes"] = public_changes(changes)
        if patch_sha256 is not None:
            section["hash"] = patch_sha256
    lines = ["# Release notes"]
    for (label, version), section in sorted(
            sections.items(), key=lambda item: (_order(item[0][1]), item[0][0]), reverse=True):
        lines += ["", f"## [{label}] {version}"]
        if section["changes"]:
            lines += [""] + [f"- {line}" for line in section["changes"]]
        if section["hash"]:
            if not re.fullmatch(r"[a-f0-9]{64}", section["hash"]):
                raise ValueError("invalid release patch SHA-256")
            lines += ["", f"UPS SHA-256: `{section['hash']}`"]
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_bytes(path, ("\n".join(lines) + "\n").encode("utf-8"))
