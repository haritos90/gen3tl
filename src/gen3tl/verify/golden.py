"""Regenerate and compare the project's deterministic outputs."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import tempfile
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path

from ..core.terminal import line
from .. import __version__

SCHEMA = 3
INPUT_KEYS = ("base_sha1", "tracked_sha256", "toolkit_version", "translation_version")
GOLDEN_VERSION = "0.0.0"  # outputs embed it, so version bumps keep the hashes
GENERATED = ("__pycache__", ".DS_Store")   # bytecode and desktop metadata, never inputs
LISTINGS = "gen3tl-tree-listings"   # beside the temporary work directories


class GoldenError(ValueError):
    pass


def file_hash(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def input_checksum(root: Path, paths: list[Path], toolkit: Path | None = None) -> str:
    """The hash of the inputs, each named relative to the toolkit root or the project root `root`."""
    digest = hashlib.sha256()
    root = Path(root).resolve()
    toolkit = Path(toolkit).resolve() if toolkit else None
    for path in sorted((Path(path).resolve() for path in paths), key=lambda p: p.as_posix()):
        base = toolkit if toolkit and path.is_relative_to(toolkit) else root
        name = path.relative_to(base).as_posix()
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def tracked_files(root: Path) -> set[str] | None:
    """The files Git tracks under `root`, relative to it; None outside a checkout."""
    try:
        listing = subprocess.run(["git", "-C", str(root), "--no-optional-locks", "ls-files", "-z"],
                                 capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return {name for name in listing.decode("utf-8").split("\0") if name} or None


def tracked_inputs(paths, root: Path, toolkit: Path | None = None) -> list[Path]:
    """The paths Git tracks, under the toolkit root or the project root `root`."""
    root = Path(root).resolve()
    toolkit = Path(toolkit).resolve() if toolkit else None
    listings, kept = {}, []
    for path in (Path(path).resolve() for path in paths):
        base = toolkit if toolkit and path.is_relative_to(toolkit) else root
        if base not in listings:
            listings[base] = tracked_files(base)
        name = path.relative_to(base)
        if listings[base] is None:
            if not set(name.parts) & set(GENERATED) and name.suffix != ".pyc":
                kept.append(path)
        elif name.as_posix() in listings[base]:
            kept.append(path)
    return kept


def input_paths(args) -> list[Path]:
    from ..lang import language
    from ..profile.profile import TOOLKIT_ROOT, Profile, profile_paths, source_files

    project = args.project
    root = project.registry_root
    paths = {*source_files(Path(args.profile).resolve()), project.policy.resolve(), project.release}
    paths.update(path for path in project.catalog.rglob("*.po") if path.is_file())
    from ..web.editordata import profile_label_inputs
    paths.update(profile_label_inputs(Profile.by_name(args.profile)).values())
    for name in ("glossary.tsv", "rules.tsv", "rules.toml", "pokedex-labels.po", "pokedex-aliases.toml"):
        paths.add(project.translation / name)
    from ..catalog.keys import alias_path
    paths.add(alias_path(Profile.by_name(args.profile).sha1, root))
    graphics = project.translation / "graphics"
    paths.update(path for path in graphics.rglob("*") if path.is_file())
    for name in ("notes", "research"):
        paths.update(path for path in (project.translation / name).rglob("*") if path.is_file())
    paths.update(path for path in (project.notes, project.graphics, project.pokedex_labels) if path is not None)
    if project.graphics_views is not None:
        paths.update(path for path in project.graphics_views.glob("*.json") if path.is_file())
    pack_root = TOOLKIT_ROOT / "assets/lang"
    selected = language.load(args.lang)
    paths.update(path for path in selected.root.rglob("*") if path.is_file())
    for metadata in pack_root.glob("*/language.json"):
        paths.add(metadata)
        pack = language.load(metadata.parent.name)
        for key in ("provider", "agreement"):
            if pack.data.get(key, "manual") != "manual":
                paths.add(pack.root / pack.data[key])
    paths.update(file for profile in profile_paths() for file in source_files(profile))
    return tracked_inputs(sorted(path.resolve() for path in paths if path.is_file()), root, TOOLKIT_ROOT)


def identity(args) -> dict:
    from ..profile.profile import TOOLKIT_ROOT, Profile
    from ..build.release import load

    return {
        "base_sha1": Profile.by_name(args.profile).sha1,
        "tracked_sha256": input_checksum(args.project.registry_root, input_paths(args), TOOLKIT_ROOT),
        "toolkit_version": __version__,
        "translation_version": load(args.project.release),
    }


def load(path: Path) -> dict:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GoldenError(f"cannot read golden manifest {path}: {exc}") from exc
    if not isinstance(doc, dict) or set(doc) != {"schema", "profile", "language", "inputs", "outputs", "identifiers"}:
        raise GoldenError(f"{path}: invalid golden manifest shape")
    if doc["schema"] != SCHEMA or not all(isinstance(doc[key], str) for key in ("profile", "language")):
        raise GoldenError(f"{path}: unsupported golden manifest")
    inputs = doc["inputs"]
    if not isinstance(inputs, dict) or set(inputs) != set(INPUT_KEYS) or not all(
            isinstance(inputs[key], str) for key in INPUT_KEYS):
        raise GoldenError(f"{path}: invalid golden input identity")
    for key in ("outputs", "identifiers"):
        hashes = doc[key]
        if not isinstance(hashes, dict) or not all(isinstance(name, str) and name and isinstance(value, str)
                                                    and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
                                                    for name, value in hashes.items()):
            raise GoldenError(f"{path}: invalid golden {key} hashes")
    if not set(doc["identifiers"]) <= set(doc["outputs"]):
        raise GoldenError(f"{path}: identifier hashes of unrecorded outputs")
    return doc


def write(path: Path, doc: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def identity_notes(manifest: dict, current_identity: dict) -> list[str]:
    """Changed inputs are reported, not failed: the outputs decide."""
    notes = []
    for key in INPUT_KEYS:
        expected = manifest["inputs"][key]
        actual = current_identity[key]
        if actual != expected:
            notes.append(f"inputs.{key}: recorded {expected}, now {actual}")
    return notes


def canonical(path: Path, aliases) -> bytes:
    """A JSON output with its catalog keys written as inventory ids, keys sorted."""
    def walk(value):
        if isinstance(value, str):
            return aliases.unkey_text(value)
        if isinstance(value, list):
            return [walk(x) for x in value]
        if isinstance(value, dict):
            return {aliases.unkey_text(k): walk(v) for k, v in value.items()}
        return value
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return json.dumps(walk(doc), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_hash(path: Path, aliases) -> str:
    return hashlib.sha256(canonical(path, aliases)).hexdigest()


def identifier_hashes(paths: dict[str, Path], aliases) -> dict[str, str]:
    """The canonical hash of every JSON output."""
    return {name: canonical_hash(path, aliases) for name, path in paths.items() if name.endswith(".json")}


def tree_listing(path: Path, aliases) -> list[list[str]]:
    """[name, file hash, canonical hash] of every file of a tree, by name; only JSON has a canonical form."""
    path = Path(path)
    rows = []
    for child in (child for child in path.rglob("*") if child.is_file()):
        digest = file_hash(child)
        rows.append([child.relative_to(path).as_posix(), digest,
                     canonical_hash(child, aliases) if child.suffix == ".json" else digest])
    return sorted(rows)


def listing_digest(rows: list[list[str]], column: int = 1) -> str:
    """The hash of a tree: the SHA-256 of its listing in sha256sum form."""
    return hashlib.sha256("".join(f"{row[column]}  {row[0]}\n" for row in rows).encode("utf-8")).hexdigest()


def listing_store() -> Path:
    return Path(tempfile.gettempdir()) / LISTINGS


def keep_listings(listings: dict[str, list[list[str]]]) -> None:
    """Every tree listing, kept under its hash to name the files of a later difference."""
    store = listing_store()
    try:
        store.mkdir(exist_ok=True)
        for rows in listings.values():
            path = store / f"{listing_digest(rows)}.json"
            if not path.is_file():
                temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
                temporary.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
                temporary.replace(path)
    except OSError as exc:
        line("warning", f"tree listings not kept: {exc}")


def kept_listing(digest: str) -> list[list[str]] | None:
    """The kept listing of a tree hash; None where none reproduces it."""
    try:
        rows = json.loads((listing_store() / f"{digest}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(rows, list) or not all(isinstance(row, list) and len(row) == 3
                                             and all(isinstance(value, str) for value in row) for row in rows):
        return None
    return rows if listing_digest(rows) == digest else None


def differences(manifest: dict, outputs: dict[str, str], identifiers: dict[str, str] | None = None) -> dict[str, str]:
    """How each differing output differs, by name; one whose canonical hash matches differs in identifiers only."""
    found = {}
    expected_outputs = manifest["outputs"]
    expected_identifiers = manifest.get("identifiers", {})
    for name in sorted(set(expected_outputs) | set(outputs)):
        expected = expected_outputs.get(name)
        actual = outputs.get(name)
        if expected is None:
            found[name] = f"not recorded, got {actual}"
        elif actual is None:
            found[name] = f"missing, expected {expected}"
        elif actual != expected and not (name in expected_identifiers and identifiers is not None
                                         and identifiers.get(name) == expected_identifiers[name]):
            found[name] = f"expected {expected}, got {actual}"
    return found


def compare(manifest: dict, outputs: dict[str, str], identifiers: dict[str, str] | None = None) -> list[str]:
    """The differences of the outputs, each naming its output."""
    return [f"{name}: {reason}" for name, reason in differences(manifest, outputs, identifiers).items()]


def file_differences(manifest: dict, name: str, rows: list[list[str]] | None) -> list[tuple[str, str]]:
    """(label, file) of each file that differs inside a differing tree, from the kept recorded listing."""
    expected = manifest["outputs"].get(name)
    if rows is None or expected is None:
        return []
    recorded = kept_listing(expected)
    if recorded is None:
        return [("hint", f"{name}: no listing of the recorded tree is kept here; "
                         "verify the recorded state once to name its files")]
    column = 2 if name in manifest.get("identifiers", {}) else 1
    before = {row[0]: row[column] for row in recorded}
    after = {row[0]: row[column] for row in rows}
    found = []
    for file in sorted(before.keys() | after.keys()):
        if file not in after:
            found.append(("missing", f"{name}/{file}"))
        elif file not in before:
            found.append(("added", f"{name}/{file}"))
        elif before[file] != after[file]:
            found.append(("changed", f"{name}/{file}"))
    return found


def identifier_only(manifest: dict, outputs: dict[str, str]) -> list[str]:
    """The outputs that passed on their canonical hash alone."""
    return sorted(name for name, value in outputs.items()
                  if name in manifest["outputs"] and value != manifest["outputs"][name])


def report(differences: list[str]) -> str:
    return "Golden verification failed:\n" + "\n".join(f"- {line}" for line in differences)


def _run(args, command: list[str], runtime: dict | None = None) -> None:
    from ..cli.main import main

    argv = ["--profile", str(args.profile), "--lang", args.lang]
    if args.rom:
        argv += ["--rom", str(args.rom)]
    if args.ignore_pin:
        argv.append("--ignore-pin")
    argv += command
    output = io.StringIO()
    with redirect_stdout(output), redirect_stderr(output):
        code = main(argv, runtime=runtime)
    if code:
        raise GoldenError(f"{' '.join(command[:2])} failed with exit {code}:\n{output.getvalue()}")
    line("generated", " ".join(command[:2]))


def snapshot_name(project) -> str:
    """The snapshot output, named by its project path."""
    return project.snapshot.relative_to(project.root).as_posix()


def sprites_name(project) -> str:
    """The sprite tree output, named by its tracked path."""
    if project.game:
        return (project.dataset / "sprites").relative_to(project.root).as_posix()
    return f"web/data/pokedex/{project.profile}/sprites"


def output_paths(args, work: Path) -> dict[str, Path]:
    """Every output by its project path: a file, or a tree, the directory of its files."""
    from ..web.editorstore import PARTS
    from ..web.snapshotstore import paths as snapshot_paths
    project = args.project
    build_name = Path(args.default_build).name
    outputs = {
        f"build/{build_name}": work / "build" / build_name,
        f"build/{Path(build_name).stem}.ups": work / "build" / f"{Path(build_name).stem}.ups",
        **{path.relative_to(project.root).as_posix(): work / path.relative_to(project.root)
           for path in snapshot_paths(project.snapshot)},
    }
    for name in ("inventory", "context", "reach"):
        outputs[f"build/{name}.json"] = work / "build" / f"{name}.json"
    for name in (f"build/editor/{project.profile}/{project.language}", f"build/editor/{project.profile}/{PARTS}",
                 f"build/pokedex/{project.profile}", sprites_name(project)):
        outputs[name] = work / name
    return outputs


def output_hashes(args, work: Path) -> tuple[dict[str, str], dict[str, str], dict[str, list[list[str]]]]:
    """The file or tree hashes, the canonical hashes and the tree listings of the outputs."""
    from ..catalog import keys
    from ..profile.profile import Profile
    paths = output_paths(args, work)
    for name, path in paths.items():
        if not path.exists():
            raise GoldenError(f"{name}: generator did not write {path}")
    aliases = keys.load(Profile.by_name(args.profile).sha1, args.project.registry_root)
    listings = {name: tree_listing(path, aliases) for name, path in paths.items() if path.is_dir()}
    files = {name: path for name, path in paths.items() if name not in listings}
    outputs = {name: file_hash(path) for name, path in files.items()}
    identifiers = identifier_hashes(files, aliases)
    for name, rows in listings.items():
        outputs[name] = listing_digest(rows)
        if any(file.endswith(".json") for file, _, _ in rows):
            identifiers[name] = listing_digest(rows, 2)
    return outputs, identifiers, listings


def generate(args, work: Path) -> tuple[dict[str, str], dict[str, str], dict[str, list[list[str]]]]:
    from ..web.editordata import pinned_toolkit_version
    from ..build.release import write_patch
    from ..project import build_scope

    work = Path(work)
    if work.exists() and any(work.iterdir()):
        raise GoldenError(f"golden work directory is not empty: {work}")
    work.mkdir(parents=True, exist_ok=True)
    project = args.project
    build_name = Path(args.default_build).name
    built = work / "build" / build_name
    editor = work / "build/editor" / project.profile / project.language
    sprites = work / sprites_name(project)
    runtime = {}
    # the project's own studies stay as they are
    with pinned_toolkit_version(GOLDEN_VERSION), build_scope(work / "build"):
        _run(args, ["discover", "inventory"])
        _run(args, ["discover", "context"])
        _run(args, ["discover", "reach"])
        _run(args, ["catalog", "extract"])   # the English of the key-only catalogs
        verify_english(args, work / "build/catalog.pot")
        _run(args, ["build", "rom", "--out", str(built)], runtime)
        _run(args, ["data", "pokedex", "--out", str(work / "build/pokedex" / project.profile)])
        _run(args, ["data", "sprites", "--out", str(sprites)])
        _run(args, ["data", "icons", "--out", str(sprites)])
        _run(args, ["data", "maps", "--out", str(sprites)])
        _run(args, ["web", "data", "--split", "--out", str(editor)], runtime)
        _run(args, ["web", "snapshot", "--out", str(work / snapshot_name(project))])
        from ..profile.profile import Profile
        base = Profile.by_name(args.profile).load_rom(args.rom)
        write_patch(built.with_suffix(".ups"), bytes(base.data), built.read_bytes())
        line("generated", "UPS")
    return output_hashes(args, work)


def verify_english(args, path: Path) -> None:
    """The extraction against the digests of the project's snapshot; a differing English is refused by key."""
    from ..catalog import extract, keys
    from ..catalog.po import read
    from ..profile.profile import Profile
    from ..web.editorsnapshot import digests, load
    project = args.project
    expected = digests(load(project.snapshot))
    if not expected:
        raise GoldenError(f"{project.snapshot}: no English digests; regenerate gen3tl web snapshot")
    differ = extract.verify(read(path)[1], expected, keys.load(Profile.by_name(args.profile).sha1,
                                                               project.registry_root))
    if differ:
        raise GoldenError(f"the English of {len(differ)} entries differs from the snapshot digests: "
                          f"{', '.join(differ[:10])}")
    line("checked", f"{len(expected)} entries")


@contextmanager
def work_directory(path: str | None):
    if path:
        yield Path(path)
        return
    with tempfile.TemporaryDirectory(prefix="gen3tl-golden-") as temporary:
        yield Path(temporary)


def run(args) -> int:
    project = args.project
    manifest_path = project.golden
    try:
        current_identity = identity(args)
        manifest = None if args.record else load(manifest_path)
        with work_directory(args.work_dir) as work:
            outputs, identifiers, listings = generate(args, work)
        keep_listings(listings)
        files = len(outputs) - len(listings) + sum(map(len, listings.values()))
        if args.record:
            doc = {"schema": SCHEMA, "profile": project.profile, "language": project.language,
                   "inputs": current_identity, "outputs": outputs, "identifiers": identifiers}
            write(manifest_path, doc)
            line("outputs", f"{len(outputs)} outputs")
            line("files", f"{files} files")
            line("wrote", manifest_path)
            return 0
        found = differences(manifest, outputs, identifiers)
        notes = identity_notes(manifest, current_identity)
        mapped = [] if found else identifier_only(manifest, outputs)
    except (GoldenError, OSError, ValueError) as exc:
        line("error", exc)
        return 1
    for key in INPUT_KEYS:
        if manifest["inputs"][key] == current_identity[key]:
            continue
        line("input", key)
        line("recorded", manifest["inputs"][key])
        line("current", current_identity[key])
    if found:
        line("verified", "failed")
        for name, reason in found.items():
            line("error", f"{name}: {reason}")
            for label, value in file_differences(manifest, name, listings.get(name)):
                line(label, value)
        return 1
    line("manifest", manifest_path)
    line("outputs", f"{len(outputs)} outputs")
    line("files", f"{files} files")
    line("verified", "passed")
    if mapped:
        line("mapped", f"{len(mapped)} outputs")
        for path in mapped:
            line("file", path)
    if notes:
        line("identity", "stale")
    return 0
