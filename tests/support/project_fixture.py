import contextlib
import hashlib
import json
import unittest
from pathlib import Path

from test_roots import TEST_ROOT
from gen3tl.profile.profile import Profile

def rom_evidence(offset, value):
    """Describe synthetic fixture bytes by their digest."""
    raw = bytes.fromhex(value) if isinstance(value, str) else value
    return {"offset": offset, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}

FIXTURES = TEST_ROOT / "fixtures"

CATALOG_STAGE = FIXTURES / "sample-hack"

SAMPLE = "sample-hack-1.0"

def sample_rom() -> bytes:
    """The ROM of the made-up game: menu, script and table texts behind their references."""
    from gen3tl.core.charmap import Charmap
    from gen3tl.core.rom import Rom, ptr_bytes
    rom = Rom(b"\xff" * 0x800)
    texts = {0x300: "NEW GAME", 0x310: "Welcome to the sample region!",
             0x330: "Hello, {PLAYER}!\\nYour journey starts here.",
             0x360: "Sample Berry", 0x370: "Test Orb", 0x380: "Draft Stone", 0x390: "Stray note"}
    for at, text in texts.items():
        rom.write(at, Charmap().encode(text))
    rom.write(0x100, bytes([0x0F, 0x48, 0x10, 0x49]))   # two PC-relative loads
    rom.write(0x140, ptr_bytes(0x300) + ptr_bytes(0x300))   # their literal pool
    rom.write(0x180, bytes([0x0F, 0x00]) + ptr_bytes(0x310) + bytes([0x67]) + ptr_bytes(0x330))   # script
    rom.write(0x200, ptr_bytes(0x360) + ptr_bytes(0x370) + ptr_bytes(0x380))   # a name table
    rom.write(0x251, ptr_bytes(0x390))   # an unaligned word: an unverified candidate
    return bytes(rom.data)

@contextlib.contextmanager
def shared_profiles():
    """Parse repository profiles once; give each caller an isolated copy."""
    import copy
    from unittest.mock import patch

    from gen3tl.profile.profile import profile_paths

    paths = {path.resolve() for path in profile_paths()}
    profiles = {}
    original = Profile.load.__func__

    def load(cls, path):
        absolute = Path(path).resolve()
        if absolute not in paths:
            return original(cls, path)
        key = (cls, absolute)
        if key not in profiles:
            profiles[key] = original(cls, path)
        result = copy.deepcopy(profiles[key])
        result.path = Path(path)
        return result

    with patch.object(Profile, "load", classmethod(load)):
        yield

def select_language(code):
    """Select a language pack for the rest of a test module."""
    from gen3tl.lang import language
    token = language.ACTIVE.set(language.load(code))
    unittest.addModuleCleanup(language.ACTIVE.reset, token)

def literal(value):
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{json.dumps(k)} = {literal(v)}" for k, v in value.items()) + " }"
    if isinstance(value, list):
        return "[" + ", ".join(literal(v) for v in value) + "]"
    return json.dumps(value, ensure_ascii=False)

def with_profile_notes(value, status="unreviewed"):
    """Load synthetic prose migrations through validated fact bindings."""
    import copy
    import tempfile

    from gen3tl.core import notes
    from gen3tl.profile import schema

    value = copy.deepcopy(value)
    fields = []

    def clear(item, path=""):
        if isinstance(item, dict):
            for key, child in item.items():
                pointer = path + "/" + key.replace("~", "~0").replace("/", "~1")
                if schema.is_prose(key) and isinstance(child, str):
                    fields.append((pointer, child))
                    item[key] = ""
                else:
                    clear(child, pointer)
        elif isinstance(item, list):
            for index, child in enumerate(item):
                clear(child, f"{path}/{index}")

    clear(value)
    data = {"table": [value]}
    fact = schema.facts(data)[0]
    statuses = (status,) if isinstance(status, str) else status
    records = [{"id": f"proof-{index}-{ordinal}", "subject": {"kind": "profile_fact", "id": fact.id},
                "scope": {"kind": "shared"}, "status": review, "facts": [text or "Measured reader."],
                "binding": {"kind": "profile_fact", "id": fact.id,
                            "value_sha256": schema.value_digest(value), "field": field}}
               for index, (field, text) in enumerate(fields) for ordinal, review in enumerate(statuses)]
    data["fact"] = [{"id": fact.id, "notes_schema": notes.SCHEMA, "notes": records}]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "profile.toml"
        path.write_text("\n".join(f"{key} = {literal(item)}" for key, item in data.items()), encoding="utf-8")
        return schema.load(path)["table"][0]

def write_policy(root, profile, language="ru", **sections):
    identity = {"version": "2.0.0", "profile": profile.name, "language": language, "rom_sha1": profile.sha1}
    doc = {"policy": identity, **sections}
    (root / "policy.toml").write_text("\n".join(f"{k} = {literal(v)}" for k, v in doc.items()), encoding="utf-8")

def workspace_project(root, language="ru"):
    """A registry at `root` with one workspace of a minimal profile."""
    profile = Path(root) / "game.toml"
    profile.write_text('[rom]\nsha1 = "' + "0" * 40 + '"\n', encoding="utf-8")
    write_policy(Path(root), Profile.load(profile), language)
    (Path(root) / "projects.toml").write_text(
        f'[defaults]\nprofile = "game"\nlanguage = "{language}"\n\n[[project]]\nprofile = "game"\n'
        f'language = "{language}"\nroot = "."\npolicy = "policy.toml"\n', encoding="utf-8")
    return profile
