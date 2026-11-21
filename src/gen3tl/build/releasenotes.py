"""The GitHub release of a tag: the changes of one language since its previous tag."""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from ..lang.names import language_name

TAG = re.compile(r"(?P<code>[A-Za-z]{2,3}(?:-[A-Za-z0-9]+)*)-v(?P<version>\d+\.\d+\.\d+)")


@dataclass(frozen=True)
class ReleaseText:
    title: str
    body: str
    previous: str | None
    commits: tuple[tuple[str, str], ...]   # full hash, subject; oldest first


def _git(root: Path, *argv: str) -> str:
    run = subprocess.run(["git", "-C", str(root), *argv], capture_output=True, text=True)
    if run.returncode != 0:
        raise ValueError(f"git {argv[0]}: {run.stderr.strip() or 'failed'}")
    return run.stdout


def _order(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def previous_tag(root: Path, code: str, version: str) -> str | None:
    """The tag of the same language with the nearest lower version."""
    earlier = []
    for name in _git(root, "tag", "--list", f"{code}-v*").split():
        match = TAG.fullmatch(name)
        if match and match["code"] == code and _order(match["version"]) < _order(version):
            earlier.append((_order(match["version"]), name))
    return max(earlier)[1] if earlier else None


def _at(root: Path, tag: str, path: Path) -> str | None:
    """A file as the tagged commit holds it."""
    name = path.resolve().relative_to(root).as_posix()
    run = subprocess.run(["git", "-C", str(root), "show", f"{tag}:{name}"], capture_output=True, text=True)
    return run.stdout if run.returncode == 0 else None


def compose(project, tag: str, repository: str | None = None, label: str | None = None) -> ReleaseText:
    """Title and body of the release of `tag`, from the commits of its language folder."""
    match = TAG.fullmatch(tag)
    if not match:
        raise ValueError(f"{tag}: not a release tag <code>-v<version>")
    code, version = match["code"], match["version"]
    if code != project.language:
        raise ValueError(f"{tag}: not a tag of the language {project.language}")
    if repository is not None and not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+", repository):
        raise ValueError(f"{repository}: not a repository <account>/<name>")
    root = Path(_git(project.root, "rev-parse", "--show-toplevel").strip()).resolve()
    _git(root, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}")
    release = _at(root, tag, project.release)
    if release is None:
        raise ValueError(f"{tag}: the tagged commit holds no release file of {code}")
    settings = tomllib.loads(release)
    if settings.get("version") != version:
        raise ValueError(f"{tag}: the release file of the tagged commit states version {settings.get('version')}")
    previous = previous_tag(root, code, version)
    folder = project.translation.resolve().relative_to(root).as_posix()
    log = _git(root, "log", "--reverse", "--no-merges", "--format=%H%x09%s",
               f"{previous}..{tag}" if previous else tag, "--", folder)
    commits = tuple(tuple(row.split("\t", 1)) for row in log.splitlines() if row)
    name = language_name(code)
    lines = [f"Changes of the {name} translation since `{previous}`:" if previous
             else f"Changes of the {name} translation:", ""]
    for sha, subject in commits:
        reference = f"[`{sha[:7]}`](https://github.com/{repository}/commit/{sha})" if repository else f"`{sha[:7]}`"
        subject = subject.replace("@", "@\u200b")   # a subject mentions no account
        lines.append(f"- {subject} ({reference})")
    manifest = _at(root, tag, project.releases / "releases.json")
    for row in json.loads(manifest).get("releases", []) if manifest else []:
        if (row.get("profile"), row.get("language"), row.get("version")) == (project.profile, code, version):
            lines += ["", f"UPS SHA-256: `{row['patch_sha256']}`"]
    return ReleaseText(f"[{label or code}] {version}", "\n".join(lines) + "\n", previous, commits)
