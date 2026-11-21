"""The scoped context of the running command."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from .manifest import MANIFEST, Manifest, REGISTRY

ACTIVE_PROJECT = ContextVar("active_project", default=None)
REGISTRY_ROOT = ContextVar("registry_root", default=None)   # the running command's registry
BUILD_ROOT = ContextVar("build_root", default=None)   # a generation's own build directory


def active() -> "Project | None":
    """The project of the running command, if any."""
    return ACTIVE_PROJECT.get()


def pin_check(project: "Project") -> tuple[str, bool] | None:
    """The pin line of a project pinning another toolkit version, and whether the command stops."""
    from .. import INSTALLED, __version__
    if not project.game:
        return None
    manifest = Manifest.read(project.root / MANIFEST)
    if manifest.toolkit == __version__:
        return None
    pinned, running = (tuple(map(int, version.split("."))) for version in (manifest.toolkit, __version__))
    text = f"{manifest.path}: pins toolkit {manifest.toolkit}, running {__version__}"
    if running[:2] == pinned[:2] and running[2] > pinned[2]:
        return f"{text} (newer patch)", False
    if not INSTALLED:
        return f"{text} (checkout)", False
    return f"{text} (--ignore-pin overrides)", True


def find_registry() -> Path:
    """Find the nearest registry within the enclosing repository."""
    cwd = Path.cwd()
    for root in (cwd, *cwd.parents):
        if (root / REGISTRY).is_file():
            return root
        if (root / ".git").is_dir() or (root / ".git").is_file():
            break
    return cwd


@contextmanager
def registry_scope(root: Path):
    """Run with `root` as the registry root of the running command."""
    token = REGISTRY_ROOT.set(Path(root))
    try:
        yield
    finally:
        REGISTRY_ROOT.reset(token)


@contextmanager
def build_scope(path: Path):
    """Run with `path` as the build directory of every project."""
    token = BUILD_ROOT.set(Path(path))
    try:
        yield
    finally:
        BUILD_ROOT.reset(token)


def build_root(root: Path) -> Path:
    """The scoped build directory, or the root's generated outputs."""
    selected = BUILD_ROOT.get()
    return Path(selected) if selected is not None else root / "build"


def registry_root() -> Path:
    """The registry root of the running command, else the one find_registry gives."""
    selected = REGISTRY_ROOT.get()
    return Path(selected) if selected is not None else find_registry()


def game_root(root: Path, profile: str) -> bool:
    """Whether `root` holds the game project of `profile`."""
    path = Path(root) / MANIFEST
    return path.is_file() and Manifest.read(path).profile == profile


def game_roots() -> list[Path]:
    """The game project roots of the running command: its project's, then its registry's."""
    project = active()
    roots, seen = [], set()
    for root in (project.root if project else None, registry_root()):
        if root is not None and Path(root).resolve() not in seen and (Path(root) / MANIFEST).is_file():
            seen.add(Path(root).resolve())
            roots.append(Path(root))
    return roots
