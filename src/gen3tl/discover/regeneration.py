"""Regenerate named study inputs and compare both profile outputs twice."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from ..core.terminal import line
from ..profile import residue, schema
from ..profile.profile import Profile
from ..project import build_scope

OUTPUTS = (schema.GENERATED, schema.DISCOVERY, schema.REPORT)


def outputs(directory: Path) -> dict[str, bytes]:
    return {name: (directory / name).read_bytes() for name in OUTPUTS if (directory / name).is_file()}


def differences(expected, actual, previous=None):
    problems = []
    for name in OUTPUTS:
        if actual.get(name) != expected.get(name):
            problems.append(f"tracked:{name}")
        if previous is not None and actual.get(name) != previous.get(name):
            problems.append(f"repeat:{name}")
    return problems


def run(args):
    from ..cli import discover as commands
    from . import emission, foundations, observations, scripts

    profile = Profile.by_name(args.profile)
    if not profile.path.is_dir():
        raise ValueError("regeneration requires a generated profile directory")
    rom = profile.load_rom(args.rom)
    expected = outputs(profile.path)
    declared = residue.read(profile.path, rom.sha1())
    prior, failures = None, []
    parent = Path(args.work_dir) if args.work_dir else args.project.build / "regeneration"
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="check-", dir=parent) as temporary:
        root = Path(temporary)
        for number in (1, 2):
            line("pass", number)
            directory = root / str(number)
            study = directory / "build"
            study.mkdir(parents=True)
            current = copy.copy(args)
            current.group, current.allow_fewer, current.build_root = None, False, study
            with build_scope(study):
                for name in ("inventory", "context", "reach"):
                    if getattr(commands, name)(current):
                        raise ValueError(f"regeneration input failed: {name}")
                    path = study / (name + ".json")
                    line("input", name)
                    line("sha256", hashlib.sha256(path.read_bytes()).hexdigest())
            inventory = json.loads((study / "inventory.json").read_text(encoding="utf-8"))
            roots, occurrences = scripts.walk_inputs(rom, profile) if profile.get("maps") else (None, ())
            result = foundations.run(rom, inventory=inventory, script_roots=roots, script_occurrences=occurrences)
            observations.apply(rom, result, profile.path)
            _, new = residue.unresolved(result.issues, declared)
            if new:
                raise ValueError("unresolved discovery facts: " + ", ".join(fact.id for fact in new))
            output = directory / "profile"
            output.mkdir()
            for source in schema.overlay_files(profile.path):
                target = output / source.relative_to(profile.path)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            emission.write(result, output, profile.name, profile)
            actual = outputs(output)
            problems = differences(expected, actual, prior)
            for problem in problems:
                line("difference", problem)
            failures.extend(problems)
            prior = actual
    if failures:
        line("error", f"regeneration differences: {len(failures)}")
        return 1
    line("verified", "passed")
    return 0
