"""The projects.toml registry: stages, workspaces and their projects."""

from __future__ import annotations

import hashlib
import json
import os
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ..profile.profile import EFFECTIVE_PROFILE, Profile, PROJECT_ROOT
from .manifest import IDENTIFIER, MANIFEST, Manifest, ProjectError, REGISTRY
from .scope import ACTIVE_PROJECT, build_root, find_registry


@dataclass(frozen=True)
class Project:
    profile: str
    language: str
    root: Path
    registry_root: Path
    policy: Path | None = None
    stage: str = "translation"
    game: bool = False   # root holds project.toml
    review: Path | None = None   # review batches, when not beside the catalog
    profile_path: Path | None = None
    dataset_path: Path | None = None

    def load_profile(self) -> Profile:
        """Load the registered profile from its declared scope."""
        from ..profile.profile import locate
        from .scope import registry_scope

        with registry_scope(self.root if self.game else self.registry_root):
            profile = (Profile.load(locate(str(self.profile_path))) if self.profile_path
                       else Profile.by_name(self.profile))
        if profile.name != self.profile:
            raise ProjectError(f"{self.key}: profile is {profile.name}")
        return profile

    @property
    def key(self) -> str:
        return f"{self.profile}/{self.language}"

    @property
    def translation(self) -> Path:
        """The language folder."""
        return self.root / "translation" / self.language if self.game else self.root / "translation"

    @property
    def build(self) -> Path:
        """The generated files: build/, or the directory build_scope selects."""
        return build_root(self.root)

    @property
    def catalog(self) -> Path:
        return self.translation / "po"

    @property
    def profile_labels(self) -> Path:
        """The language's translations of profile descriptions."""
        return self.translation / "profile-labels.po"

    @property
    def batches(self) -> Path:
        return self.review or self.translation / "batches"

    @property
    def glossary(self) -> Path:
        return self.translation / "glossary.tsv"

    @property
    def release(self) -> Path:
        return self.translation / "release.toml"

    @property
    def golden(self) -> Path:
        return self.translation / "golden.json"

    @property
    def source(self) -> Path | None:
        """Shared source data of a game project."""
        return self.root / "source" if self.game else None

    @property
    def notes(self) -> Path | None:
        return self.source / "notes.toml" if self.game else None

    @property
    def pokedex_labels(self) -> Path | None:
        return self.source / "pokedex-labels.pot" if self.game else None

    @property
    def profile_notes(self) -> Path:
        """The language's notes on profile facts."""
        return self.translation / "notes" / "profile.toml"

    @property
    def graphics(self) -> Path | None:
        return self.source / "graphics.toml" if self.game else None

    @property
    def graphics_views(self) -> Path | None:
        return self.source / "graphics" / "views" if self.game else None

    @property
    def snapshot(self) -> Path:
        """The measurement snapshot of this language."""
        if self.game:
            return self.source / "snapshot" / f"{self.language}.snapshot.json"
        return self.translation / "editor-inputs.json"

    @property
    def dataset(self) -> Path | None:
        """The tracked Pokedex dataset of a game project."""
        return self.root / "data" / "pokedex" if self.game else None

    @property
    def editor(self) -> Path:
        return self.registry_root / "build/editor" / self.profile / self.language

    @property
    def releases(self) -> Path:
        """The release directory: releases/ of a game project, else the editor data's."""
        return self.root / "releases" if self.game else self.registry_root / "build/editor/releases"

    @property
    def release_notes(self) -> Path:
        """The cumulative release notes of the local builds."""
        return self.build / "release-notes.md"

    @property
    def site(self) -> Path:
        """The assembled static site."""
        return self.root / "site" if self.game else self.build / "site"

    def validate(self, profile: Profile, catalog: Path | None = None) -> None:
        """Reject another project's identities before reading translation data."""
        if profile.name != self.profile:
            raise ProjectError(f"{self.key}: profile is {profile.name}")
        for filename in ("inventory.json", "context.json", "reach.json"):
            path = self.build / filename
            if not path.exists():
                continue
            doc = json.loads(path.read_text(encoding="utf-8"))
            expected = {"profile": self.profile, "rom_sha1": profile.sha1}
            for key, value in expected.items():
                actual = doc.get(key)
                if actual != value:
                    raise ProjectError(f"{path}: {key} {actual!r} differs from {value!r}")
        from ..catalog.po import read
        for path in sorted((catalog or self.catalog).rglob("*.po")):
            header, _ = read(path)
            for key, value in (("Project-Id-Version", self.profile), ("Language", self.language)):
                actual = header.get(key)
                if actual != value:
                    raise ProjectError(f"{path}: {key} {actual!r} differs from {value!r}")

    def validate_built(self, profile: Profile, path: Path) -> None:
        """Check build provenance when consuming a translated ROM."""
        manifest = path.with_suffix(".project.json")
        if not manifest.exists():
            raise ProjectError(f"{path}: missing build identity {manifest.name}; rebuild this project")
        doc = json.loads(manifest.read_text(encoding="utf-8"))
        expected = {"profile": self.profile, "language": self.language, "rom_sha1": profile.sha1,
                    "built_sha1": hashlib.sha1(path.read_bytes()).hexdigest()}
        if any(doc.get(key) != value for key, value in expected.items()):
            raise ProjectError(f"{path}: build identity does not match {self.key} or ROM contents")

    def effective(self, profile: Profile) -> Profile:
        """Resolve this project's validated language configuration."""
        from ..check.policy import effective
        if profile.name != self.profile:
            raise ProjectError(f"{self.key}: profile is {profile.name}")
        if self.policy is None:
            raise ProjectError(f"{self.key}: configure an explicit language policy in projects.toml")
        if hasattr(profile, "policy_path"):
            profile = Profile.load(profile.path)
        if self.game and profile.get("translator_notes"):
            raise ProjectError(f"{profile.name}: move the translator notes to {self.notes}")
        result = effective(profile, self.language, self.policy, self.root,
                           notes_path=self.profile_notes if self.game else None)
        result.release_path = self.release
        if self.game:
            result.notes_path = self.notes
            if self.pokedex_labels.is_file():
                from ..catalog.po import template_reasons
                labels = result.data.setdefault("pokedex", {}).setdefault("labels", {})
                if labels.get("unmapped"):
                    raise ValueError(f"{self.pokedex_labels}: unmapped labels also declared in the profile")
                labels["unmapped"] = template_reasons(self.pokedex_labels)
            if self.graphics.is_file():
                relative = self.graphics.relative_to(self.root).as_posix()
                previews = self.graphics_views if self.graphics_views.is_dir() else self.graphics
                result.data.setdefault("graphics", {}).update(
                    inventory=relative, previews=previews.relative_to(self.root).as_posix())
        return result

    @contextmanager
    def activate(self, profile: Profile | None = None):
        """Scope relative paths for one synchronous CLI invocation."""
        if profile is not None and (profile.name != self.profile
                                    or getattr(profile, "language", None) != self.language
                                    or profile.project_root != self.root):
            raise ProjectError(f"{self.key}: activate its effective project profile")
        previous = Path.cwd()
        token = PROJECT_ROOT.set(self.root)
        selected = EFFECTIVE_PROFILE.set(profile)
        current = ACTIVE_PROJECT.set(self)
        try:
            os.chdir(self.root)
            from ..lang.language import use
            with use(self.language):
                yield self
        finally:
            os.chdir(previous)
            PROJECT_ROOT.reset(token)
            EFFECTIVE_PROFILE.reset(selected)
            ACTIVE_PROJECT.reset(current)


@dataclass(frozen=True)
class Registry:
    root: Path
    default_profile: str
    default_language: str
    projects: tuple[Project, ...]
    site: dict | None = None

    @classmethod
    def load(cls, root: Path | None = None) -> "Registry":
        """The registry of `root`, else the one find_registry gives, else an empty registry."""
        if root is None:
            root = find_registry()
            if not (root / REGISTRY).is_file():
                return cls(root.resolve(), "", "", ())
        root = Path(root).resolve()
        path = root / REGISTRY
        try:
            with path.open("rb") as stream:
                doc = tomllib.load(stream)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ProjectError(f"cannot read project registry {path}: {exc}") from exc
        defaults = doc.get("defaults", {})
        projects = []
        keys, roots = set(), set()
        manifests: dict[Path, Manifest] = {}
        inputs: dict[str, tuple[Path | None, Path | None]] = {}
        for item in doc.get("project", []):
            name, language = item.get("profile", ""), item.get("language", "")
            if not all(IDENTIFIER.fullmatch(v) for v in (name, language)):
                raise ProjectError(f"{path}: invalid profile or language identifier")
            workspace = (root / item.get("root", "")).resolve()
            if not item.get("root") or not workspace.is_dir():
                raise ProjectError(f"{path}: project workspace does not exist: {workspace}")
            policy = item.get("policy")
            if not isinstance(policy, str) or not policy:
                raise ProjectError(f"{path}: {name}/{language} needs an explicit language policy")
            policy_path = (workspace / policy).resolve()
            if Path(policy).is_absolute() or not policy_path.is_relative_to(workspace):
                raise ProjectError(f"{path}: policy must stay inside its project workspace")
            review = item.get("batches")
            if review is not None:
                if not isinstance(review, str) or not review or Path(review).is_absolute() \
                        or not (workspace / review).resolve().is_relative_to(workspace):
                    raise ProjectError(f"{path}: batches must stay inside its project workspace")
                review = (workspace / review).resolve()
            stage = item.get("stage", "translation")
            if stage != "translation":
                raise ProjectError(f"{path}: unsupported project stage {stage!r}")
            game = (workspace / MANIFEST).is_file()
            if game:
                manifest = manifests.get(workspace) or Manifest.read(workspace / MANIFEST)
                manifests[workspace] = manifest
                if manifest.profile != name or language not in manifest.languages:
                    raise ProjectError(f"{path}: {name}/{language} is not a language of {workspace / MANIFEST}")
            declared = []
            for field in ("profile_path", "dataset_path"):
                value = item.get(field)
                if field in item:
                    if not isinstance(value, str) or not value.strip() or Path(value).is_absolute():
                        raise ProjectError(f"{path}: {field} must be a nonempty relative path")
                    value = (root / value).resolve()
                    if field == "profile_path":
                        exists = value.is_file() or value.is_dir() and (value / "profile.toml").is_file()
                    else:
                        exists = value.is_dir() and (value / "pokedex.json").is_file()
                    if not exists:
                        raise ProjectError(f"{path}: missing {field} input: {value}")
                declared.append(value)
            sources = tuple(declared)
            if name in inputs and inputs[name] != sources:
                raise ProjectError(f"{path}: {name} languages disagree on profile_path or dataset_path")
            project = Project(name, language, workspace, root, policy_path, stage, game, review, *sources)
            if name not in inputs and project.profile_path is not None:
                project.load_profile()
            inputs[name] = sources
            if project.key in keys or project.translation in roots:
                raise ProjectError(f"{path}: duplicate project or shared workspace: {project.key}")
            keys.add(project.key)
            roots.add(project.translation)
            projects.append(project)
        for workspace, manifest in manifests.items():
            registered = {p.language for p in projects if p.root == workspace}
            if registered != set(manifest.languages):
                raise ProjectError(f"{manifest.path}: languages differ from the projects of {path}")
        result = cls(root, defaults.get("profile", ""), defaults.get("language", ""), tuple(projects), doc.get("site"))
        if projects:
            result.resolve(result.default_profile, result.default_language)
        return result

    def profiles(self) -> dict[str, Profile]:
        """Load library metadata and the registry's explicit profile inputs."""
        from ..profile.profile import locate, name_of, profile_paths
        from .scope import registry_scope

        games = {project.profile for project in self.projects}
        with registry_scope(self.root):
            paths = {name_of(path): path for path in profile_paths() if name_of(path) in games}
            for project in self.projects:
                if project.profile_path is not None:
                    paths[project.profile] = project.profile_path
                elif project.game and (project.root / "profile" / "profile.toml").is_file():
                    paths[project.profile] = project.root / "profile"
            return {name: Profile.load(locate(str(path))) for name, path in sorted(paths.items())}

    def resolve(self, profile: Profile | str, language: str | None = None) -> Project:
        name = profile.name if isinstance(profile, Profile) else profile
        language = language or self.default_language
        for project in self.projects:
            if (project.profile, project.language) == (name, language):
                return project
        raise ProjectError(f"unconfigured translation project {name}/{language}; register it in projects.toml")

    def protect(self, project: Project, paths) -> None:
        """Reject explicit paths into another project's managed directories."""
        for value in paths:
            path = Path(value).resolve()
            for other in self.projects:
                if other == project:
                    continue
                for directory in (other.translation, other.build):
                    if directory == project.build:   # languages of one game share it
                        continue
                    # Nested workspaces own their descendants.
                    if path.is_relative_to(project.root) and project.root.is_relative_to(directory):
                        continue
                    if path.is_relative_to(directory):
                        raise ProjectError(f"{path}: belongs to {other.key}, selected {project.key}")
