"""ROM profiles: every ROM-specific value, keyed by the base ROM SHA-1."""

from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path
from typing import Any

from .. import TOOLKIT_ROOT
from ..core.rom import Rom, RomError
from . import languagerules, schema

PROFILE_DIR = TOOLKIT_ROOT / "profiles"
PROJECT_ROOT = ContextVar("project_root", default=None)
EFFECTIVE_PROFILE = ContextVar("effective_profile", default=None)


def profile_paths(root: Path | None = None) -> list[Path]:
    """Toolkit and scoped game profiles, sorted by identity."""
    from ..project import game_roots

    root = PROFILE_DIR if root is None else Path(root)
    found = [p for p in root.glob("*.toml") if p.is_file()]
    found += [p for p in root.iterdir() if p.is_dir() and (p / schema.GENERATED).is_file()] if root.is_dir() else []
    found += [game / "profile" for game in game_roots() if (game / "profile" / schema.GENERATED).is_file()]
    return sorted(dict.fromkeys(found), key=lambda p: (name_of(p), p.is_dir()))


def name_of(path: Path) -> str:
    """Game manifests preserve identity across profile moves."""
    if path.is_dir() and path.name == "profile":
        from ..project import MANIFEST, Manifest, ProjectError

        try:
            return Manifest.read(path.parent / MANIFEST).profile
        except ProjectError as exc:
            raise RomError(f"{path}: requires a project manifest profile id: {exc}") from exc
    return path.stem if path.suffix == ".toml" else path.name


def source_files(path: str | Path) -> list[Path]:
    """The files a profile is read from."""
    from ..families.knowledge import source_files as family_sources

    path = Path(path)
    sources = schema.read(path) if path.is_dir() else schema.flat(path)
    generated = [path / name for name in (schema.GENERATED, schema.DISCOVERY) if (path / name).is_file()]
    instance = [*generated, *sources.overlay_files] if path.is_dir() else [path]
    return (family_sources(sources.family_file) if sources.family_file is not None else []) + instance


def locate(name: str) -> Path:
    """Resolve explicit paths or unambiguous project and toolkit names."""
    given = Path(name)
    if name.endswith(".toml") or given.is_absolute() or len(given.parts) > 1:
        path = given
        # a directory file selects its directory
        if path.name in (schema.GENERATED, schema.OVERLAY) and (path.parent / schema.GENERATED).is_file():
            path = path.parent
        elif path.exists():
            for parent in (path, *path.parents):
                if parent.name == schema.OVERLAY_DIR and (parent.parent / schema.GENERATED).is_file():
                    path = parent.parent
                    break
    else:
        from ..project import game_roots

        path, folder = PROFILE_DIR / f"{name}.toml", PROFILE_DIR / name
        projects = [game / "profile" for game in game_roots() if (game / "profile").is_dir()]
        matches = [candidate for candidate in projects if name_of(candidate) == name]
        matches += ([path] if path.exists() else []) + ([folder] if folder.is_dir() else [])
        matches = list(dict.fromkeys(matches))
        if len(matches) > 1:
            raise RomError(f"profile {name}: both {' and '.join(map(str, matches))} exist; keep one")
        if matches:
            path = matches[0]
    if not path.exists():
        raise RomError(f"no profile {path}")
    name_of(path)
    return path


class Profile:
    def __init__(self, data: dict[str, Any], path: Path):
        self.data = data
        self.path = path
        self.language_rules = languagerules.load(data)

    @classmethod
    def load(cls, path: str | Path) -> "Profile":
        path = Path(path)
        return cls(schema.load(path), path)

    @classmethod
    def by_name(cls, name: str | None) -> "Profile":
        """Load a named, explicit or uniquely default profile."""
        if name is None:
            defaults = [p for p in profile_paths() if cls.load(p).get("project", {}).get("default")]
            if len(defaults) != 1:
                raise RomError("select --profile explicitly; no unique default profile")
            path = defaults[0]
        else:
            path = locate(name)
        selected = EFFECTIVE_PROFILE.get()
        if selected is not None and path.resolve() == selected.path.resolve():
            return selected
        return cls.load(path)

    def for_project(self, language: str, registry_root: Path | None = None) -> "Profile":
        """Select an explicit translation policy for programmatic consumers."""
        from ..project import Registry, registry_scope
        registry = Registry.load(registry_root)
        with registry_scope(registry.root):   # the policy names entries through its alias table
            return registry.resolve(self, language).effective(self)

    @property
    def project_root(self) -> Path:
        return getattr(self, "_project_root", None) or PROJECT_ROOT.get() or Path.cwd()

    @property
    def name(self) -> str:
        return name_of(self.path)

    def output_stem(self, language: str) -> str:
        """The profile slug and selected language identify build outputs."""
        return f"{self.get('project', {}).get('slug', self.name)}-{language}"

    @property
    def sha1(self) -> str:
        return self.data["rom"]["sha1"].lower()

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def load_rom(self, path: str | Path | None = None) -> Rom:
        """Load the base ROM named by the profile, verifying its SHA-1."""
        if path is None:
            from ..project import registry_root
            path = registry_root() / self.data["rom"]["path"]
        return Rom.load(path, self.sha1)

    def command_problem(self, command: str, built: str | None = None,
                        mode: str = "field") -> str | None:
        """Restrict starter profiles to verified discovery operations."""
        stage = self.get("support", {}).get("stage", "legacy")
        if stage in ("legacy", "translation"):
            return None
        if stage == "catalog":
            if command in ("rom identify", "rom evidence", "discover run", "discover candidates", "discover graphics",
                           "catalog source", "catalog prepare", "web data",
                           "web bundle", "web server", "web submission", "catalog progress",
                           "check po", "catalog tm", "check commit-message",
                           "data pokedex", "data probe", "data profile", "data sprites",
                           "discover check", "discover convert"):
                return None
            if command == "rom decode" and not built and mode == "field":
                return None
            return (f"{self.name}: editable catalog only; {command} requires verified translation support. "
                    "Fonts, insertion and display limits remain unverified.")
        if stage != "discovery":
            return f"{self.name}: unknown support stage {stage!r}"
        if command in ("rom identify", "rom evidence", "discover run", "discover candidates", "discover graphics",
                       "discover flag", "catalog source", "data probe", "discover check", "discover convert",
                       "check commit-message"):
            return None
        if command == "rom decode" and not built and mode == "field":
            return None
        return (f"{self.name}: discovery profile; {command} not verified; "
                "use rom identify, discover run, catalog source, or field-mode rom decode on the original ROM")
