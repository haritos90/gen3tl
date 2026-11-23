"""Prepare verified UPS releases for the static patcher."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path

from ..core import ups
from ..profile.profile import Profile
from ..build.release import SEMVER

MAX_SIZE = 128 * 1024 * 1024
SLUG = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]*")
FIELDS = {"profile", "profile_name", "language", "version", "source_sha1", "source_size",
          "result_sha1", "result_size", "patch", "patch_sha256", "patch_size", "output"}
NAME_FIELDS = {"names", "names_sha256"}   # the save page's name tables
NOTE_FIELDS = {"notes", "notes_sha256"}   # public release notes
PUBLICATION_FIELDS = {"release_tag", "release_asset"}
ASSET_RUN = re.compile(r"[^A-Za-z0-9@_+-]+")   # characters GitHub replaces in asset names
SUPPORTED_STAGES = {"translation"}   # the only stage that may publish a release


def release_stage(stage, source: str) -> str:
    """The stage a release may publish from; absent defaults to translation."""
    if not isinstance(stage, str) or stage not in SUPPORTED_STAGES:
        raise ValueError(f"{source}: unsupported stage {stage!r}")
    return stage


def validate(document: dict) -> list[dict]:
    """Validate a manifest without trusting filesystem paths."""
    if (not isinstance(document, dict) or set(document) != {"schema", "releases"}
            or type(document["schema"]) is not int or document["schema"] != 1):
        raise ValueError("invalid patcher manifest schema")
    releases = document["releases"]
    if not isinstance(releases, list):
        raise ValueError("invalid patcher releases")
    identities = set()
    profiles = {}
    for row in releases:
        optional = set(row) - FIELDS if isinstance(row, dict) else set()
        if (not isinstance(row, dict) or not FIELDS.issubset(row)
                or optional - PUBLICATION_FIELDS not in (set(), NAME_FIELDS, NOTE_FIELDS, NAME_FIELDS | NOTE_FIELDS)
                or optional & PUBLICATION_FIELDS not in (set(), PUBLICATION_FIELDS)):
            raise ValueError("invalid patcher release fields")
        for key in ("profile", "language"):
            if not isinstance(row[key], str) or not SLUG.fullmatch(row[key]):
                raise ValueError(f"invalid release {key}")
        if not isinstance(row["profile_name"], str) or not row["profile_name"].strip():
            raise ValueError("invalid release profile name")
        if not isinstance(row["version"], str) or not SEMVER.fullmatch(row["version"]):
            raise ValueError("invalid release version")
        for key, length in (("source_sha1", 40), ("result_sha1", 40), ("patch_sha256", 64)):
            if not isinstance(row[key], str) or not re.fullmatch(f"[0-9a-f]{{{length}}}", row[key]):
                raise ValueError(f"invalid release {key}")
        for key in ("source_size", "result_size", "patch_size"):
            if type(row[key]) is not int or not 0 < row[key] <= MAX_SIZE:
                raise ValueError(f"invalid release {key}")
        output = row["output"]
        if (row["patch"] != f"{row['profile']}/{row['language']}/{row['version']}.ups"
                or not isinstance(output, str) or not re.fullmatch(r'[^<>:"/\\|?*\x00-\x1f\x7f]+\.gba', output)
                or not output[:-4].strip()):
            raise ValueError("invalid release filenames")
        if "names" in row and (row["names"] != names_path(row) or not isinstance(row["names_sha256"], str)
                               or not re.fullmatch("[0-9a-f]{64}", row["names_sha256"])):
            raise ValueError("invalid release name tables")
        if "notes" in row and (row["notes"] != notes_path(row) or not isinstance(row["notes_sha256"], str)
                               or not re.fullmatch("[0-9a-f]{64}", row["notes_sha256"])):
            raise ValueError("invalid release notes")
        if "release_tag" in row and any(row[key] != value for key, value in publication(row).items()):
            raise ValueError("invalid release publication")
        identity = row["profile"], row["language"], row["version"]
        if identity in identities:
            raise ValueError("duplicate patcher release")
        identities.add(identity)
        source = row["profile_name"], row["source_sha1"], row["source_size"]
        if profiles.setdefault(row["profile"], source) != source:
            raise ValueError("inconsistent base identity for one release profile")
    return releases


def publication(row: dict) -> dict:
    """The release tag and the asset name of its patch: the output name with .ups, as GitHub keeps it."""
    return {"release_tag": f"{row['language']}-v{row['version']}",
            "release_asset": github_asset_name(row["output"].removesuffix(".gba") + ".ups")}


def github_asset_name(name: str) -> str:
    """The name GitHub keeps for an uploaded file: each run of other characters becomes one period, none at an end."""
    return ASSET_RUN.sub(".", name).strip(".")


def browser_row(row: dict) -> dict:
    """Keep publication coordinates outside the browser contract."""
    return {key: value for key, value in row.items() if key not in PUBLICATION_FIELDS}


def patch_store(directory: Path) -> Path:
    """The persistent ignored patch store beside project metadata."""
    return directory.parent / "build" / "patches"


def patch_path(directory: Path, row: dict) -> Path:
    """Resolve an offered patch inside its local store."""
    store = patch_store(directory)
    path = store / row["patch"]
    if path.is_symlink() or not path.resolve().is_relative_to(store.resolve()):
        raise ValueError(f"{path}: release patches must be regular files")
    return path


def verify_patch(row: dict, data: bytes) -> None:
    """Require the exact bytes identified by the release manifest."""
    if len(data) != row["patch_size"] or hashlib.sha256(data).hexdigest() != row["patch_sha256"]:
        raise ValueError(f"{row['patch']}: release patch differs from its manifest")


def read_patch(directory: Path, row: dict) -> bytes:
    """Read and verify one offered patch from local storage."""
    path = patch_path(directory, row)
    if not path.is_file():
        raise ValueError(f"{path}: missing release file")
    data = path.read_bytes()
    verify_patch(row, data)
    return data


def fetch_patches(directory: Path, repository: str) -> int:
    """Fetch published assets into local storage before site assembly."""
    from urllib.error import HTTPError, URLError
    from urllib.request import urlopen
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("invalid release repository")
    rows = validate(json.loads((directory / "releases.json").read_text(encoding="utf-8")))
    pending = {}
    for row in rows:
        if not PUBLICATION_FIELDS.issubset(row):
            raise ValueError(f"{row['patch']}: missing release publication")
        path = patch_path(directory, row)
        if path.exists():
            read_patch(directory, row)
            continue
        url = f"https://github.com/{repository}/releases/download/{row['release_tag']}/{row['release_asset']}"
        try:
            with urlopen(url, timeout=60) as response:
                data = response.read(row["patch_size"] + 1)
        except (OSError, URLError) as exc:
            if isinstance(exc, HTTPError):
                exc.close()
            raise ValueError(f"{row['release_asset']}: release download failed") from None
        verify_patch(row, data)
        pending[path] = data
    # Validate every asset before storing downloads.
    for path, data in pending.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
        try:
            temporary.write_bytes(data)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return len(pending)


def names_path(row: dict) -> str:
    """The name tables of a release, beside its patch."""
    return f"{row['profile']}/{row['language']}/{row['version']}.names.json"


def notes_path(row: dict) -> str:
    """The public notes of a release, beside its patch."""
    return f"{row['profile']}/{row['language']}/{row['version']}.notes.json"


def names_document(profile: Profile, row: dict, base: bytes, target: bytes, charmap: dict, rules: dict) -> bytes:
    """The save page's data of a release: species and egg names, the character table and the save rules."""
    from ..verify.savefile import Layout, name_table
    layout = Layout.from_dict(profile["save"])
    doc = {"schema": 1, "profile": row["profile"], "language": row["language"], "version": row["version"],
           "source": name_table(base, layout), "result": name_table(target, layout),
           "charmap": {str(b): ch for b, ch in sorted(charmap.items())}, "rules": rules}
    return (json.dumps(doc, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def notes_document(row: dict, record: dict) -> bytes:
    """The public translation changes of one release."""
    from ..build.release import _release_key, public_changes
    label = record.get("release_label", row["language"])
    _release_key(label, row["version"])
    notes = record.get("release_notes", [])
    if not isinstance(notes, list) or not all(isinstance(note, str) for note in notes):
        raise ValueError("invalid public release notes")
    doc = {"schema": 1, "profile": row["profile"], "language": row["language"], "version": row["version"],
           "release_label": label, "notes": public_changes("\n".join(notes))}
    return (json.dumps(doc, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def _write_manifest(directory: Path, document: dict) -> None:
    manifest = directory / "releases.json"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         prefix=".releases-", delete=False) as fh:
            temporary = Path(fh.name)
            fh.write(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(manifest)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _write_names(directory: Path, row: dict, data: bytes) -> None:
    destination = directory / names_path(row)
    if destination.is_symlink() or not destination.resolve().is_relative_to(directory.resolve()):
        raise ValueError("release name tables escape the release directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    row["names"] = names_path(row)
    row["names_sha256"] = hashlib.sha256(data).hexdigest()


def _write_notes(directory: Path, row: dict, data: bytes) -> None:
    destination = directory / notes_path(row)
    if destination.is_symlink() or not destination.resolve().is_relative_to(directory.resolve()):
        raise ValueError("release notes escape the release directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    row["notes"] = notes_path(row)
    row["notes_sha256"] = hashlib.sha256(data).hexdigest()


def copy_releases(source: Path, destination: Path, profile: str) -> int:
    """Copy the releases of `profile` into the site data; every file must match its manifest hash."""
    source, destination = Path(source), Path(destination)
    if not (source / "releases.json").is_file():
        return 0
    rows = [r for r in validate(json.loads((source / "releases.json").read_text(encoding="utf-8")))
            if r["profile"] == profile]
    manifest = destination / "releases.json"
    previous = (json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists()
                else {"schema": 1, "releases": []})
    kept = [browser_row(r) for r in validate(previous) if r["profile"] != profile]
    files = {}
    for row in rows:
        for key in ("patch", "names", "notes"):
            if key not in row:
                continue
            if key == "patch":
                files[destination / row[key]] = read_patch(source, row)
                continue
            path = source / row[key]
            if path.is_symlink() or not path.resolve().is_relative_to(source.resolve()):
                raise ValueError(f"{path}: release files must be regular files")
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != row[f"{key}_sha256"]:
                raise ValueError(f"{path}: differs from its manifest")
            files[destination / row[key]] = data
    document = {"schema": 1, "releases": sorted(kept + [browser_row(r) for r in rows], key=lambda r: (
        r["profile"], r["language"], tuple(map(int, r["version"].split(".")))))}
    validate(document)
    # every file checked before the first write
    changed = [path for path, data in files.items() if not path.is_file() or path.read_bytes() != data]
    for path in changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(files[path])
    if document != previous:
        destination.mkdir(parents=True, exist_ok=True)
        _write_manifest(destination, document)
    return len(changed)


def add_notes(profile: Profile, directory: Path, notes_of) -> list[str]:
    """Publish the notes of every release of `profile` that has none."""
    manifest = directory / "releases.json"
    lock = directory / ".prepare.lock"
    try:
        handle = lock.open("x")
    except FileExistsError:
        raise ValueError(f"{lock}: another release preparation is active") from None
    try:
        handle.close()
        document = json.loads(manifest.read_text(encoding="utf-8"))
        added = []
        for row in validate(document):
            if row["profile"] != profile.name or "notes" in row:
                continue
            _write_notes(directory, row, notes_of(row))
            added.append(row["version"])
        validate(document)
        if added:
            _write_manifest(directory, document)
        return added
    finally:
        lock.unlink()


def add_names(profile: Profile, base: bytes, directory: Path, names_of) -> list[str]:
    """Publish the name tables of every release of `profile` that has none."""
    if hashlib.sha1(base).hexdigest() != profile.sha1:
        raise ValueError("release base SHA-1 differs from the selected profile")
    manifest = directory / "releases.json"
    lock = directory / ".prepare.lock"
    try:
        handle = lock.open("x")
    except FileExistsError:
        raise ValueError(f"{lock}: another release preparation is active") from None
    try:
        handle.close()
        document = json.loads(manifest.read_text(encoding="utf-8"))
        added = []
        for row in validate(document):
            if row["profile"] != profile.name or "names" in row:
                continue
            data = read_patch(directory, row)
            target = ups.apply(base, data)
            if hashlib.sha1(target).hexdigest() != row["result_sha1"]:
                raise ValueError(f"{row['patch']}: the patched ROM differs from the released result")
            _write_names(directory, row, names_of(row, target))
            added.append(row["version"])
        validate(document)
        if added:
            _write_manifest(directory, document)
        return added
    finally:
        lock.unlink()


def prepare(profile: Profile, language: str, base: bytes, built: Path,
            patch: Path, directory: Path, names_of=None, notes_of=None, *, output_of=None) -> dict:
    """Register one explicit release after a byte-for-byte round trip; `output_of` names the ROM of a version."""
    problem = profile.command_problem("build patcher-data")
    if problem:
        raise ValueError(problem)
    identity = json.loads(built.with_suffix(".project.json").read_text(encoding="utf-8"))
    if any(path.stat().st_size > MAX_SIZE for path in (built, patch)):
        raise ValueError("release files exceed the browser size limit")
    target, data = built.read_bytes(), patch.read_bytes()
    expected = {"profile": profile.name, "language": language, "rom_sha1": profile.sha1,
                "built_sha1": hashlib.sha1(target).hexdigest()}
    if not isinstance(identity, dict) or any(identity.get(k) != v for k, v in expected.items()):
        raise ValueError("release build identity differs from the selected project or ROM")
    release_stage(identity.get("stage", "translation"), "release build identity")
    release_stage(getattr(profile, "stage", "translation"), "release profile")
    if hashlib.sha1(base).hexdigest() != profile.sha1:
        raise ValueError("release base SHA-1 differs from the selected profile")
    version = identity.get("version")
    row = {"profile": profile.name, "profile_name": profile["rom"]["name"],
           "language": language, "version": version,
           "source_sha1": profile.sha1, "source_size": len(base),
           "result_sha1": expected["built_sha1"], "result_size": len(target),
           "patch": f"{profile.name}/{language}/{version}.ups",
           "patch_sha256": hashlib.sha256(data).hexdigest(), "patch_size": len(data),
           "output": output_of(version) if output_of is not None else f"{profile.name}-{language}-{version}.gba"}
    row.update(publication(row))
    validate({"schema": 1, "releases": [row]})
    # Bound allocations before the existing decoder.
    cursor = 4
    for expected_size in (len(base), len(target)):
        value, shift = 0, 1
        for _ in range(5):
            if cursor >= len(data) - 12:
                raise ValueError("truncated UPS size")
            byte = data[cursor]
            cursor += 1
            value += (byte & 127) * shift
            if byte & 128:
                break
            shift *= 128
            value += shift
        else:
            raise ValueError("UPS size is too large")
        if value != expected_size:
            raise ValueError("UPS sizes differ from the release ROMs")
    try:
        actual = ups.apply(base, data)
    except (ValueError, IndexError, OverflowError) as exc:
        raise ValueError(f"invalid release UPS: {exc}") from exc
    if actual != target:
        raise ValueError("UPS result differs from the identified release build")
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / "releases.json"
    lock = directory / ".prepare.lock"
    try:
        handle = lock.open("x")
    except FileExistsError:
        raise ValueError(f"{lock}: another release preparation is active") from None
    try:
        handle.close()
        document = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {"schema": 1, "releases": []}
        rows = validate(document)
        validate({"schema": 1, "releases": [r for r in rows if
                  (r["profile"], r["language"], r["version"]) !=
                  (row["profile"], row["language"], row["version"])] + [row]})
        for old in rows:
            read_patch(directory, old)
            if all(old[k] == row[k] for k in ("profile", "language", "version")):
                if output_of is None:
                    row["output"] = old["output"]
                if any(old[k] != row[k] for k in FIELDS - {"output"}):
                    raise ValueError("release version already identifies different contents")
                added = old["output"] != row["output"] or not PUBLICATION_FIELDS.issubset(old)
                old["output"] = row["output"]
                old.update(publication(row))
                if names_of is not None and "names" not in old:
                    _write_names(directory, old, names_of(old, target))
                    added = True
                if notes_of is not None and "notes" not in old:
                    _write_notes(directory, old, notes_of(old))
                    added = True
                if added:
                    validate(document)
                    _write_manifest(directory, document)
                return old
        destination = patch_path(directory, row)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with destination.open("xb") as fh:
                fh.write(data)
        except FileExistsError:
            if destination.read_bytes() != data:
                raise ValueError("release patch filename already contains different bytes") from None
        if names_of is not None:
            _write_names(directory, row, names_of(row, target))
        if notes_of is not None:
            _write_notes(directory, row, notes_of(row))
        rows.append(row)
        rows.sort(key=lambda r: (r["profile"], r["language"], tuple(map(int, r["version"].split(".")))))
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                             prefix=".releases-", delete=False) as fh:
                temporary = Path(fh.name)
                fh.write(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
            temporary.replace(manifest)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return row
    finally:
        lock.unlink()
