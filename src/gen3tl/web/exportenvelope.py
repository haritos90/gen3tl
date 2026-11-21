"""Versioned editor export envelopes shared by all import routes."""

import json
import re

VERSION = "4.0.0"
_NUMBER = r"(?:0|[1-9][0-9]*)"
_PRERELEASE = rf"(?:{_NUMBER}|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
_SEMVER = re.compile(rf"({_NUMBER})\.{_NUMBER}\.{_NUMBER}"
                     rf"(?:-{_PRERELEASE}(?:\.{_PRERELEASE})*)?"
                     r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?")
_FIELDS = {
    "profile": (str, "a string"),
    "language": (str, "a string"),
    "entries": (list, "a list"),
    "pictures": (list, "a list"),
    "terms": (list, "a list"),
    "glyphs": (list, "a list"),
    "keyboard": ((dict, type(None)), "an object or null"),
    "engine_rules": (list, "a list"),
    "note_changes_schema": (str, "a string"),
    "note_changes": (list, "a list"),
    "notes": ((str, type(None)), "a string or null"),
    "rom_sha1": (str, "a string"),
}


def validate(document: object) -> dict:
    """Validate top-level fields before routing; retain unknown fields."""
    version = document.get("version", VERSION) if isinstance(document, dict) else VERSION

    def refuse(reason):
        raise ValueError(f"editor export version {json.dumps(version, ensure_ascii=True)}; "
                         f"supported {VERSION}: {reason}")

    if not isinstance(document, dict):
        refuse("the export must be a JSON object")
    match = _SEMVER.fullmatch(version) if isinstance(version, str) else None
    if match is None:
        refuse("version must be a Semantic Versioning string")
    if match.group(1) != VERSION.split(".")[0]:
        refuse("unsupported major version")
    required = ["profile", "language"]
    if {"note_changes_schema", "note_changes"} & document.keys():
        required.extend(("note_changes_schema", "note_changes"))
    for name in required:
        if name not in document:
            refuse(f"missing required field: {name}")
    for name, (kind, description) in _FIELDS.items():
        if name in document and not isinstance(document[name], kind):
            refuse(f"{name} must be {description}")
    return {"version": version, **document}
