#!/usr/bin/env python3
"""Reject game data and private binaries in the application site."""

import json
from pathlib import Path
import re
import sys

from gen3tl.web.editordata import validate_registry

EXCLUDED = {
    ".gba", ".agb", ".gb", ".gbc", ".bin", ".mb", ".elf", ".nds", ".srl",
    ".sav", ".srm", ".sa1", ".sa2", ".fla", ".eep", ".sram", ".dsv", ".state", ".sgm",
    *(f".ss{n}" for n in range(10)), *(f".st{n}" for n in range(10)),
    ".zip", ".7z", ".rar", ".gz", ".tgz", ".tar", ".bz2", ".xz", ".zst", ".lz", ".lzma",
    ".ups", ".ips", ".bps", ".xdelta", ".vcdiff", ".ppf", ".aps",
}
MAGIC = (b"PK\x03\x04", b"7z\xbc\xaf\x27\x1c", b"Rar!", b"\x1f\x8b", b"\xfd7zXZ", b"BZh", b"\x28\xb5\x2f\xfd",
         b"UPS1", b"BPS1", b"PATCH", b"\xd6\xc3\xc4", b"\x7fELF")
SAVES = (b"\x25\x20\x01\x08", b"\x99\x19\x12\x01", b"\x98\x19\x12\x01")
SITE_FILE = re.compile(r"site/(?:catalogs\.json|startup\.js|[a-z]{2,3}(?:-[A-Za-z0-9]+)?(?:\.json|/(?:index\.json|entries/\d+\.json))|shared/[0-9a-f]{64}\.json)")


def check(tree):
    data = tree / "data"
    registry = validate_registry(json.loads((data / "registry.json").read_text(encoding="utf-8")))
    manifest = json.loads((data / "languages.json").read_text(encoding="utf-8"))
    if registry["games"] or [row["id"] for row in manifest["profiles"]] != ["site"]:
        raise ValueError("application contains game profiles")
    if any(row["profile"] != "site" for row in manifest["languages"]) or "bundle" in manifest:
        raise ValueError("application contains game catalogs or a bundle")
    files = 0
    for path in sorted(tree.rglob("*")):
        relative = path.relative_to(tree)
        if path.is_symlink() or any(part.startswith(".") for part in relative.parts) or relative.parts[0] == "share":
            raise ValueError(f"excluded path: {relative}")
        if not path.is_file():
            continue
        files += 1
        if relative.parts[0] == "data":
            name = path.relative_to(data).as_posix()
            if name not in {"registry.json", "languages.json"} and not SITE_FILE.fullmatch(name):
                raise ValueError(f"game data: {relative}")
        content = path.read_bytes()
        save = any(content.find(signature, at + 4096, at + 4100) == at + 4096
                   for signature in SAVES for at in [content.find(signature)] if at >= 0)
        rom = len(content) >= 192 and content[0xB2] == 0x96 and content[4:8] == b"\x24\xff\xae\x51"
        if path.suffix.lower() in EXCLUDED or content.startswith(MAGIC) or content[257:262] == b"ustar" or save or rom:
            raise ValueError(f"excluded file: {relative}")
    return files


def main(argv):
    try:
        if len(argv) != 2 or not Path(argv[1]).is_dir():
            raise ValueError("expected an assembled site directory")
        print(f"files        {check(Path(argv[1]))}")
    except (OSError, ValueError, KeyError) as exc:
        print(f"error        {str(exc).replace(chr(10), ' ')}")
        return 1
    print("verified     passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
