"""The project.toml manifest and the shared registry file name."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

MANIFEST = "project.toml"
REGISTRY = "projects.toml"
MANIFEST_KEYS = ("schema", "profile", "rom_sha1", "toolkit", "languages", "default_language")
SEMVER = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
IDENTIFIER = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*")


class ProjectError(ValueError):
    pass


@dataclass(frozen=True)
class Manifest:
    """project.toml: the identity and languages of a game project."""
    path: Path
    profile: str
    rom_sha1: str
    toolkit: str
    languages: tuple[str, ...]
    default_language: str

    @classmethod
    def read(cls, path: Path) -> "Manifest":
        try:
            with Path(path).open("rb") as stream:
                doc = tomllib.load(stream)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ProjectError(f"cannot read game project {path}: {exc}") from exc
        if set(doc) != set(MANIFEST_KEYS) or doc["schema"] != 1:
            raise ProjectError(f"{path}: expected schema 1 and {', '.join(MANIFEST_KEYS[1:])}")
        languages = doc["languages"]
        if (not isinstance(doc["profile"], str) or not IDENTIFIER.fullmatch(doc["profile"])
                or not isinstance(doc["rom_sha1"], str) or not re.fullmatch(r"[0-9a-f]{40}", doc["rom_sha1"])
                or not isinstance(doc["toolkit"], str) or not SEMVER.fullmatch(doc["toolkit"])):
            raise ProjectError(f"{path}: invalid profile, ROM SHA-1 or toolkit version")
        if (not isinstance(languages, list) or len(set(languages)) != len(languages)
                or not all(isinstance(code, str) and IDENTIFIER.fullmatch(code) for code in languages)
                or doc["default_language"] not in (languages or [""])):
            raise ProjectError(f"{path}: invalid languages or default language")
        return cls(Path(path), doc["profile"], doc["rom_sha1"], doc["toolkit"], tuple(languages),
                   doc["default_language"])

    def text(self) -> str:
        languages = ", ".join(f'"{code}"' for code in self.languages)
        return ("# Game project: base ROM profile, pinned toolkit, languages.\n\n"
                f'schema = 1\nprofile = "{self.profile}"\nrom_sha1 = "{self.rom_sha1}"\n'
                f'toolkit = "{self.toolkit}"\nlanguages = [{languages}]\n'
                f'default_language = "{self.default_language}"\n')
