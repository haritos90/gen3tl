"""Conservative Western text candidates without engine-specific reference claims."""

import hashlib
import json
from pathlib import Path

from ..core.charmap import Charmap, CharmapError
from ..core.rom import Rom
from .scan import is_string_start, is_text, pointer_index


def candidates(rom: Rom) -> list[dict]:
    """Scan pointer targets without interpreting scripts or tables."""
    cm = Charmap()
    data = rom.data
    found = []
    for offset, references in sorted(pointer_index(rom).items()):
        terminator = data.find(b"\xff", offset, offset + 4000)
        if terminator < 0:
            continue
        try:
            text, end = cm.decode(data, offset, limit=terminator - offset + 1)
        except CharmapError:
            continue
        if not is_text(data, offset, end - 1) or not is_string_start(data, offset):
            continue
        found.append({"id": f"s{offset:07X}", "offset": offset, "length": end - offset,
                      "text": text, "references": references})
    return found


def inspect(prof, rom: Rom) -> dict:
    """Return research data, separate from translation inventories."""
    return {
        "profile": prof.name,
        "rom_sha1": rom.sha1(),
        "engine": prof.get("engine", {}).get("family", "unspecified"),
        "codec": "gen3-western-baseline",
        "status": "unverified-candidates",
        "limitations": [
            "False positives and missing strings are expected.",
            "Pointer-shaped words are not verified references.",
            "Custom characters and placeholder meanings need engine verification.",
            "No fonts, scripts, tables, context, saves or layout limits are inferred.",
            "This file is not a translation inventory or a build input.",
        ],
        "candidates": candidates(rom),
    }


def source_catalog(doc: dict, directory: Path) -> dict:
    """Write reproducible source templates with unverified provenance."""
    from ..catalog.po import PoEntry, write
    rows = []
    for item in doc["candidates"]:
        rows.append(PoEntry(item["id"], item["text"], flags=["fuzzy"],
                           references=[f"0x{item['offset']:07X}"], extracted=[
                               "status: unverified candidate",
                               "context, reachability, encoding extensions and layout: unknown",
                               f"source length: {item['length']} bytes",
                               "pointer candidates: " + ", ".join(f"0x{p:07X}" for p in item["references"]),
                           ]))
    path = directory / "source.pot"
    write(path, rows, {"Project-Id-Version": doc["profile"], "Language": "",
                      "Content-Type": "text/plain; charset=UTF-8",
                      "Content-Transfer-Encoding": "8bit", "X-ROM-SHA1": doc["rom_sha1"],
                      "X-Catalog-Status": "unverified-candidates", "X-Source-Codec": doc["codec"]})
    metadata = {k: v for k, v in doc.items() if k != "candidates"}
    metadata.update(schema="1.0.0", source_language="en", target_language=None,
                    generator="gen3-pointer-candidates/1.0.0", entries=len(rows),
                    translations=0, source_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    (directory / "catalog.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                                            encoding="utf-8")
    return metadata


def publish_catalog(directory: Path, editor_root: Path) -> None:
    """Copy source templates into local editor data."""
    import shutil
    metadata = json.loads((directory / "catalog.json").read_text(encoding="utf-8"))
    profile = metadata["profile"]
    if Path(profile).name != profile or profile in (".", ".."):
        raise ValueError("invalid source catalog profile")
    source = directory / "source.pot"
    if hashlib.sha256(source.read_bytes()).hexdigest() != metadata["source_sha256"]:
        raise ValueError(f"{source}: source catalog checksum differs")
    target = editor_root / "sources" / profile
    target.mkdir(parents=True, exist_ok=True)
    for name in ("source.pot", "catalog.json"):
        shutil.copyfile(directory / name, target / name)
