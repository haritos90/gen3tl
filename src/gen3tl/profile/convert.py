"""Move a flat profile into a profile directory: the whole profile becomes the overlay."""

from __future__ import annotations

import shutil
import tomllib
from pathlib import Path

from . import schema
from .check import report

OVERLAY_HEADER = f"# Hand-proven facts with prose evidence, merged over {schema.GENERATED}.\n\n".encode("utf-8")


def convert(path: str | Path) -> Path:
    """Write profiles/<rom-id>/ from profiles/<rom-id>.toml and return the directory."""
    source = Path(path)
    if source.suffix != ".toml" or not source.is_file():
        raise schema.ProfileError(f"{source}: not a flat profile file")
    target = source.with_suffix("")
    if target.exists():
        raise schema.ProfileError(f"{target} exists; remove it or keep the flat profile")
    raw = source.read_bytes()
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise schema.ProfileError(f"{source}: {exc}") from exc
    reserved = sorted({schema.HEADER, schema.RECORD} & set(data))
    if reserved:
        raise schema.ProfileError(f"{source}: top-level {', '.join(reserved)} is reserved in a profile directory")
    if "family" in data or "external_reference" in data:
        data = schema.load(source)
    target.mkdir()
    try:
        overlay = target / schema.OVERLAY_DIR
        overlay.mkdir()
        (overlay / "facts.toml").write_bytes(OVERLAY_HEADER + raw)
        (overlay / schema.OVERLAY_ORDER).write_text("facts.toml\n", encoding="utf-8")
        (target / schema.GENERATED).write_text(schema.GENERATED_TEXT, encoding="utf-8")
        (target / schema.REPORT).write_text(report(target.name, []), encoding="utf-8")
        if repr(schema.load(target)) != repr(data):   # key order and value types too
            raise schema.ProfileError(f"{target}: the merged profile differs from {source}")
    except BaseException:
        shutil.rmtree(target)
        raise
    source.unlink()
    return target
