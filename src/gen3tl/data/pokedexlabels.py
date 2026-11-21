"""Pokedex labels from ROM bindings and a project template."""

from __future__ import annotations

import re
from pathlib import Path

ID = re.compile(r"^(s[0-9A-F]{7}|[a-z_]+:\d+)$")
SPEC = re.compile(r"^(s[0-9A-F]{7}|[a-z_]+:\d+)(?:@([\^~]|[A-Z][A-Z_0-9]*\+?))?$")
TABLES = ("strings", "symbols", "unmapped")


def load(block: dict, template: Path) -> dict:
    """Join profile bindings with the template's reasons."""
    from ..catalog.po import template_reasons

    if block.get("unmapped"):
        raise ValueError(f"{template}: unmapped labels also declared in the profile")
    resolved = dict(block, unmapped=template_reasons(template))
    if problems := check(resolved):
        raise ValueError(f"{template}: {'; '.join(problems)}")
    return resolved


def resolve(block: dict | None, profile: str) -> dict | None:
    """Resolve raw dataset labels through the tracked project registry."""
    from ..project import REGISTRY, Registry, registry_root

    labels = (block or {}).get("labels")
    if not isinstance(labels, dict) or "unmapped" in labels:
        return block
    root = registry_root()
    if not (root / REGISTRY).is_file():
        return block
    templates = {project.pokedex_labels for project in Registry.load(root).projects
                 if project.profile == profile and project.pokedex_labels is not None}
    if len(templates) > 1:
        raise ValueError(f"{profile}: multiple Pokedex label templates")
    if not templates or not (template := next(iter(templates))).is_file():
        return block
    return dict(block, labels=load(labels, template))


def bare_id(value: str) -> str:
    """The catalog id of a label value, without its fragment rule."""
    return value.split("@", 1)[0]


def check(block: dict) -> list[str]:
    """Problems of a [pokedex.labels] block: bad ids, empty reasons, keys in two tables."""
    problems = []
    for name in TABLES:
        if not isinstance(block.get(name), dict):
            problems.append(f"{name}: a table of label keys")
    if problems:
        return problems
    for key, value in block["strings"].items():
        if not isinstance(value, str) or not SPEC.match(value):
            problems.append(f"strings: {key!r} is not a catalog id")
    for key, value in block["symbols"].items():
        if not isinstance(value, str):
            problems.append(f"symbols: {key!r} is not a string")
    for key, value in block["unmapped"].items():
        if not isinstance(value, str) or not value.strip():
            problems.append(f"unmapped: {key!r} has no reason")
    seen = {}
    for name in TABLES:
        for key in block[name]:
            if key in seen:
                problems.append(f"{key!r} is in both {seen[key]} and {name}")
            seen[key] = name
    return problems


def build(block: dict) -> dict[str, str | None]:
    """key -> catalog id, symbol or None, sorted by key."""
    out = {}
    out.update(block.get("strings", {}))
    out.update(block.get("symbols", {}))
    out.update({key: None for key in block.get("unmapped", {})})
    return dict(sorted(out.items()))
