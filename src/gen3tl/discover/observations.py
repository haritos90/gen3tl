"""ROM-bound runtime samples; predictions never substitute for observations."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from ..core.font import FontTable
from ..core.lists import append_unique
from ..profile import schema
from .verification import annotate


VERSION = "2.0.0"


def digest(raw):
    return {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def valid_digest(value):
    return (isinstance(value, dict) and set(value) == {"size", "sha256"}
            and type(value["size"]) is int and value["size"] > 0
            and isinstance(value["sha256"], str)
            and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is not None)


def scenario_sources(path):
    from ..verify.headless import parse_scenario

    path = Path(path)
    steps = parse_scenario(path.read_text(encoding="utf-8"), path.parent)
    files = []
    from ..verify.headless import STATE_DIR
    for op, args in steps:
        if op == "statefile":
            if args[0] != "load":
                raise ValueError("observations: scenarios cannot write shared states")
            source = STATE_DIR / (args[1] + ".state")
        elif op == "savefile":
            source = Path(args[0])
        else:
            continue
        append_unique(files, str(source))
    return steps, files


def scenario(path, inputs=None, input_root=Path(".")):
    steps, files = scenario_sources(path)
    if inputs is None:
        inputs = [{"path": name, **digest((Path(input_root) / name).read_bytes())} for name in files]
    if (not isinstance(inputs, list) or [row.get("path") for row in inputs] != files
            or any(not valid_digest({k: v for k, v in row.items() if k != "path"}) for row in inputs)):
        raise ValueError("observations: invalid scenario inputs")
    script = [steps, hashlib.sha256(Path(path).read_bytes()).hexdigest(), inputs]
    return {"path": str(path), "sha256": hashlib.sha256(json.dumps(script, sort_keys=True).encode()).hexdigest(),
            "inputs": inputs}


def prediction(rom, fact, sample):
    """Derive one concrete sample solely from the current fact and ROM."""
    value = fact.value
    if fact.kind == "font":
        glyph = sample["glyph"]
        if type(glyph) is not int or not 0 <= glyph < value["count"]:
            raise ValueError("observations: glyph outside font")
        font, = FontTable.from_profile({"font": [value]})
        palette = sample["palette"]
        if (len(palette) != 4 or any(len(rgb) != 3 or any(type(c) is not int or not 0 <= c <= 255
                for c in rgb) for rgb in palette) or len({tuple(rgb) for rgb in palette}) < 3):
            raise ValueError("observations: invalid glyph palette")
        x, y = sample["x"], sample["y"]
        if not 0 <= x <= 224 or not 0 <= y <= 144:
            raise ValueError("observations: glyph outside framebuffer")
        pixels = font.glyph(rom, glyph)
        if not any(pixel == 1 for row in pixels for pixel in row):
            raise ValueError("observations: glyph has no ink")
        return bytes(c for row in pixels for pixel in row for c in palette[pixel])
    if fact.id.startswith("table[name="):
        row = sample["row"]
        if type(row) is not int or not 0 <= row < value["count"]:
            raise ValueError("observations: row outside table")
        at = value["base"] + row * value["stride"] + value["field_offset"]
        target = rom.ptr_at(at) if value.get("long_name_pointers") else None
        from .verification import decode_back
        _, size = decode_back(rom, target if target is not None else at,
            min(4096, len(rom) - target) if target is not None else value["field_length"],
            terminated=True if target is not None else value.get("terminated", True))
        return rom.read(target if target is not None else at, size)
    if fact.id.startswith("keyboard.table["):
        offset, size = sample["offset"], sample["length"]
        if not 0 <= offset < offset + size <= value["entry"]:
            raise ValueError("observations: sample leaves keyboard entry")
        return rom.read(value["base"] + offset, size)
    raise ValueError(f"observations: unsupported fact {fact.id}")


def read_runtime(game, fact, sample, size):
    if fact.kind == "font":
        x, y = sample["x"], sample["y"]
        pixels = game.pixels()
        return bytes(c for row in pixels[y:y + 16] for rgb in row[x:x + 16] for c in rgb)
    address = sample["ram"]
    ram = game.ewram()
    offset = address - 0x02000000
    if not 0 <= offset < offset + size <= len(ram):
        raise ValueError("observations: read leaves EWRAM")
    return bytes(ram[offset:offset + size])


def produce(rom, rom_path, facts, plan, out, layout=None):
    """Execute declared scenario shots and record actual framebuffer/RAM reads."""
    from ..verify.headless import run_scenario

    if plan.get("schema") != "1.0.0" or not plan.get("samples"):
        raise ValueError("observations: missing sample plan")
    by_id = {fact.id: fact for fact in facts}
    groups = {}
    for sample in plan["samples"]:
        fact = by_id[sample["fact"]]
        predicted = prediction(rom, fact, sample)
        groups.setdefault(sample["scenario"], []).append((sample, fact, predicted))
    output = Path(out)
    if output.exists():
        raise ValueError("observations: output already exists")
    rows = []
    for index, (path, samples) in enumerate(groups.items()):
        provenance = scenario(path)
        seen = set()

        def observe(game, shot):
            for number, (sample, fact, predicted) in enumerate(samples):
                if sample["shot"] != shot:
                    continue
                if number in seen:
                    raise ValueError("observations: repeated sample shot")
                seen.add(number)
                actual = read_runtime(game, fact, sample, len(predicted))
                rows.append({"fact": fact.id, "value_sha256": schema.value_digest(fact.value),
                    "scenario": provenance, "sample": sample, "predicted": digest(predicted),
                    "observed": digest(actual), "read": "framebuffer" if fact.kind == "font" else "ewram"})

        source = Path(path)
        run_scenario(rom_path, source.read_text(encoding="utf-8"), output.parent / (output.stem + "-shots"),
                     f"sample-{index}", layout=layout, directory=source.parent, observer=observe)
        if len(seen) != len(samples):
            raise ValueError("observations: scenario did not reach every sample")
    document = {"schema": VERSION, "rom_sha1": rom.sha1(), "observations": rows}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return document


def section(fact):
    if fact.kind == "font":
        return "font"
    if fact.id.startswith("table[name=") and {"count", "base", "stride", "field_offset", "field_length"} <= fact.value.keys():
        return "table"
    if fact.id.startswith("keyboard.table["):
        return "keyboard.table"
    return None


def shared_predictions(rom, facts, fact, sample, expected):
    shared = []
    for other in facts:
        if other.id == fact.id or section(other) != section(fact):
            continue
        try:
            raw = prediction(rom, other, sample)
        except ValueError:
            continue
        if raw == expected:
            shared.append(other.id)
    return sorted(shared)


def verify(rom, result, document, plan=None):
    """Upgrade only facts whose independently read samples match predictions."""
    if document.get("schema") == "1.0.0":
        raise ValueError("observations: schema 1.0.0 requires discover observe --convert <record> --out <record>")
    if (set(document) != {"schema", "rom_sha1", "observations"}
            or document.get("schema") != VERSION or document.get("rom_sha1") != rom.sha1()
            or not document.get("observations")):
        raise ValueError("observations: schema, ROM or samples differ")
    if plan is not None:
        samples = lambda rows: Counter(json.dumps(row, sort_keys=True) for row in rows)
        if (plan.get("schema") != "1.0.0" or samples(plan.get("samples", [])) !=
                samples(row["sample"] for row in document["observations"])):
            raise ValueError("observations: sample plan differs")
    by_id = {fact.id: fact for fact in result.facts}
    confirmed = {}
    seen = set()
    reports = []
    for row in document["observations"]:
        if set(row) != {"fact", "value_sha256", "scenario", "sample", "predicted", "observed", "read"}:
            raise ValueError("observations: invalid record fields")
        fact = by_id.get(row["fact"])
        if fact is None or row["value_sha256"] != schema.value_digest(fact.value):
            raise ValueError("observations: unknown or changed fact")
        sample = row["sample"]
        parameters = {"font": {"glyph", "x", "y", "palette"}, "table": {"row", "ram"},
                      "keyboard.table": {"offset", "length", "ram"}}.get(section(fact))
        if parameters is None or set(sample) != {"fact", "scenario", "shot"} | parameters:
            raise ValueError("observations: invalid sample fields")
        identity = json.dumps(sample, sort_keys=True)
        if identity in seen or sample["fact"] != fact.id:
            raise ValueError("observations: duplicate or mismatched sample")
        seen.add(identity)
        if row["scenario"] != scenario(sample["scenario"], row["scenario"].get("inputs", [])):
            raise ValueError("observations: stale scenario or fixture")
        expected = prediction(rom, fact, sample)
        measured = digest(expected)
        if row["read"] != ("framebuffer" if fact.kind == "font" else "ewram"):
            raise ValueError("observations: wrong read domain")
        if (not valid_digest(row["predicted"]) or not valid_digest(row["observed"])
                or row["predicted"] != measured or row["observed"] != measured):
            raise ValueError(f"observations: sample differs for {fact.id}")
        shared = shared_predictions(rom, result.facts, fact, sample, expected)
        report = {"fact": fact.id, "shot": sample["shot"], **measured,
                  "shared": shared, "status": "shared" if shared else "discriminating"}
        if fact.kind == "font":
            background = bytes(sample["palette"][0])
            report["foreground_pixels"] = sum(expected[i:i + 3] != background for i in range(0, len(expected), 3))
        reports.append(report)
        if not shared:
            confirmed[fact.id] = confirmed.get(fact.id, 0) + 1
    record_digest = hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()
    result.metadata["observations"] = {"schema": VERSION, "sha256": record_digest, "samples": reports}
    for index, fact in enumerate(result.facts):
        if fact.id in confirmed:
            proof = annotate(fact, "recorded-runtime-sample",
                f"Discriminating scenario samples match this fact; observation SHA-256 {record_digest}. Whole-domain runtime coverage is not claimed.",
                ingame_samples=confirmed[fact.id])
            result.facts[index] = replace(proof, tier="ingame")
    return reports


def convert(rom, facts, document, input_root=Path(".")):
    """Recheck legacy predictions and input provenance before discarding bytes."""
    from .foundations import Result

    if document.get("schema") != "1.0.0":
        raise ValueError("observations: conversion requires schema 1.0.0")
    converted = deepcopy(document)
    converted["schema"] = VERSION
    for row in converted["observations"]:
        path = row["sample"]["scenario"]
        provenance = scenario(path, input_root=input_root)
        steps, _ = scenario_sources(path)
        files = {path: hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                 **{item["path"]: item["sha256"] for item in provenance["inputs"]}}
        legacy = hashlib.sha256(json.dumps([steps, files], sort_keys=True).encode()).hexdigest()
        if row["scenario"] != {"path": path, "sha256": legacy}:
            raise ValueError("observations: stale legacy scenario or fixture")
        row["scenario"] = provenance
        for key in ("predicted", "observed"):
            row[key] = digest(bytes.fromhex(row[key]))
    verify(rom, Result({}, list(facts), {}), converted)
    return converted


def apply(rom, result, profile_path, path=None):
    """Use the tracked record and plan during both regeneration paths."""
    explicit = path is not None
    path = Path(path) if explicit else Path(profile_path) / "observations" / "observations.json"
    if not explicit and not path.exists():
        return
    plan_path = path.with_name("samples.json")
    plan = json.loads(plan_path.read_text(encoding="utf-8")) if not explicit or plan_path.exists() else None
    verify(rom, result, json.loads(path.read_text(encoding="utf-8")), plan)


def report_lines(samples):
    lines = ["", "## Runtime samples", "",
             "| Item | Value |", "|---|---|",
             "| Compared bytes | size and SHA-256 |",
             "| Raw predictions | `gen3tl rom evidence --sample` |",
             "| Tier raised by | discriminating samples |",
             "| Coverage | the listed samples |", "",
             "| Fact | Shot | Compared bytes | SHA-256 | Non-background pixels | Result | Tier raised | Shared facts |",
             "|---|---|---:|---|---:|---|---|---|"]
    for row in samples:
        partners = ", ".join(f"`{name}`" for name in row["shared"]) or "none"
        tier = "ingame" if row["status"] == "discriminating" else "none"
        lines.append(f"| `{row['fact']}` | `{row['shot']}` | {row['size']} | `{row['sha256']}` | "
                     f"{row.get('foreground_pixels', 'n/a')} | {row['status']} | {tier} | {partners} |")
    return lines
