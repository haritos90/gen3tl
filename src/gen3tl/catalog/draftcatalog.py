"""Editable source catalogs with explicit capabilities."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from ..core.charmap import Charmap
from .po import PoEntry, read, write


CAPABILITIES = {"build": False, "layout": False, "fonts": False,
                "encoding": False, "check": "tokens"}
LIMITATIONS = [
    "Source bytes and reference patterns were checked against the exact ROM.",
    "Reference patterns do not establish script reachability or speaker identity.",
    "Fixed name tables and unreferenced strings may be missing.",
    "Game fonts, target encoding and display limits remain unverified.",
    "Draft translations can be exported; translation builds remain unavailable.",
]


def source_digest(items) -> str:
    data = sorted((item.msgctxt, item.msgid) for item in items)
    return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()


def prepare(profile, rom, language: str, directory: Path) -> dict:
    """Create empty rows, preserving existing translations."""
    from ..discover.inventory import build
    from ..lang.language import save_link_keys
    if profile.get("support", {}).get("stage") != "catalog":
        raise ValueError("catalog preparation requires a catalog-stage profile")
    if rom.sha1() != profile.sha1:
        raise ValueError("source ROM identity differs")
    inventory = build(profile, rom, Charmap(), save_link_keys=save_link_keys(language))
    grouped, excluded = {}, Counter()
    probe = profile.get("probe", {}).get("menu", {})
    for entry in inventory["entries"]:
        if entry["flags"] or entry["category"] == "unverified":
            excluded.update(entry["flags"] or ["unverified"])
            continue
        cm = Charmap()
        text, end = cm.decode(rom.data, entry["offset"], mode=entry["mode"], limit=entry["length"])
        # a fixed field's length includes terminator and padding:
        # the decode stops at the terminator, so compare text only
        if entry["kind"] == "fixed":
            if text != entry["text"]:
                raise ValueError(f"{entry['id']}: source bytes differ")
        elif text != entry["text"] or end != entry["offset"] + entry["length"]:
            raise ValueError(f"{entry['id']}: source bytes differ")
        if any(rom.ptr_at(r["at"]) != entry["offset"] for r in entry["refs"]):
            raise ValueError(f"{entry['id']}: source references differ")
        native = entry["offset"] == probe.get("offset") and text == probe.get("text")
        evidence = "native menu probe" if native else "reference pattern; use unverified"
        comments = [f"category: {entry['category']}; mode: {entry['mode']}",
                    f"source validation: {evidence}",
                    f"source length: {entry['length']} bytes",
                    "reachability, speaker, font and window: unknown",
                    "references: " + ", ".join(f"0x{r['at']:07X} ({r['kind']})" for r in entry["refs"])]
        item = PoEntry(entry["id"], text, extracted=comments, flags=["fuzzy"],
                       references=[f"0x{entry['offset']:07X}"])
        grouped.setdefault(entry["category"], []).append(item)
    previous = {}
    for path in sorted((directory / "po").rglob("*.po")):
        if path.parent != directory / "po/source":
            raise ValueError(f"{path}: unmanaged catalog file; review before regeneration")
        header, entries = read(path)
        if (header.get("Project-Id-Version"), header.get("Language"), header.get("X-ROM-SHA1")) != (
                profile.name, language, profile.sha1):
            raise ValueError(f"{path}: catalog identity differs")
        for item in entries:
            if item.msgctxt in previous:
                raise ValueError(f"duplicate catalog id: {item.msgctxt}")
            previous[item.msgctxt] = item
        grouped.setdefault(path.stem, [])
    current = {item.msgctxt: item for entries in grouped.values() for item in entries}
    for sid, old in previous.items():
        if old.msgstr and (sid not in current or current[sid].msgid != old.msgid):
            raise ValueError(f"{sid}: translated source changed; review before regeneration")
        if sid in current and current[sid].msgid == old.msgid:
            current[sid].msgstr = old.msgstr
            current[sid].translator = old.translator
    directory.mkdir(parents=True, exist_ok=True)
    header = {"Project-Id-Version": profile.name, "Language": language,
              "Content-Type": "text/plain; charset=UTF-8", "Content-Transfer-Encoding": "8bit",
              "X-ROM-SHA1": profile.sha1, "X-Catalog-Status": "draft"}
    files = {}
    for category, entries in sorted(grouped.items()):
        path = directory / "po" / "source" / f"{category}.po"
        write(path, entries, header)
        files[path.relative_to(directory).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {"schema": "1.0.0", "profile": profile.name, "language": language,
                "rom_sha1": profile.sha1, "status": "draft", "capabilities": CAPABILITIES,
                "entries": len(current), "translations": sum(bool(e.msgstr) for e in current.values()),
                "categories": {k: len(v) for k, v in sorted(grouped.items())},
                "excluded": dict(sorted(excluded.items())), "limitations": LIMITATIONS, "files": files,
                "source_sha256": source_digest(current.values())}
    (directory / "catalog.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def editor_data(profile, language: str, catalog: dict, manifest: dict, limitations=None, *, note_root=None) -> dict:
    """Expose drafts without invented font or window metrics."""
    from ..web.editordata import SCHEMA, attach_notes, context, status, toolkit_version
    from .po import catalog_name
    from .guidance import entry_notes
    if (manifest.get("profile"), manifest.get("language"), manifest.get("rom_sha1")) != (
            profile.name, language, profile.sha1):
        raise ValueError("draft catalog identity differs")
    if source_digest(item for _, item in catalog.values()) != manifest.get("source_sha256"):
        raise ValueError("draft source entries differ; regenerate the catalog from its ROM")
    limitations = LIMITATIONS if limitations is None else limitations
    source = "catalog capabilities"
    guidance = entry_notes(profile, catalog)
    from . import entrynotes, keys
    entrynotes.refresh(catalog, language, keys.for_profile(profile))
    entries = []
    for sid, (path, item) in catalog.items():
        entries.append({"id": sid, "file": catalog_name(path), "tokens": re.findall(r"\{[^{}]*\}", item.msgid),
                        "translation": item.msgstr, "flags": item.flags, "status": status(item),
                        "context": context(item.extracted, catalog), "window": None, "terms": [],
                        **({"owner": item.owner} if item.owner else {}),
                        "approved": [], "note": item.source_note,
                        "translator_notes": guidance.get(sid, []),
                        "reach": {"class": "unknown", "source": "not studied", "evidence": ""},
                        "rules": [{"text": text, "source": source, "evidence": ""}
                                  for text in limitations[1:]]})
    doc = {"schema": SCHEMA, "toolkit": toolkit_version(), "language": language,
            "profile": profile.name, "rom": {"sha1": profile.sha1},
            "capabilities": CAPABILITIES, "limitations": limitations,
            "check_rules": {}, "fonts": {"main": None, "styles": {}, "codes": {}},
            "charmap": {}, "placeholders": {"default": 0, "widths": {}},
            "glossary": {}, "graphics": None, "entries": entries}
    attach_notes(doc, profile, catalog, folder=note_root)
    return doc
