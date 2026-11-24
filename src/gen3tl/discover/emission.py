"""Deterministic generated profiles with independently rendered evidence."""

from __future__ import annotations

import copy
import json
import os
import stat
import tempfile
import tomllib
from collections import Counter
from pathlib import Path

from ..profile import check, metadata, residue, schema
from ..profile.profile import Profile
from . import table_scope
from .foundations import Result, evidence, fact

ROM_FONT_FIELDS = frozenset({"name", "glyphs", "widths", "count", "codes", "format"})


def literal(value) -> str:
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{schema.key_text(k)} = {literal(v)}" for k, v in value.items()) + " }"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(literal(v) for v in value) + "]"
    if value is None:
        raise ValueError("null is not a profile value")
    return json.dumps(value, ensure_ascii=False)


def record(proof: schema.Fact) -> dict:
    details = proof.evidence
    return {"id": proof.id, "kind": proof.kind, "scope": proof.scope, "tier": proof.tier,
            "source": proof.source, "status": "verified", "evidence": {
                "method": details.method, "anchors": list(details.anchors), "code_sites": list(details.code_sites),
                "counts": dict(details.counts), "sentence": details.sentence}}


def document(data: dict, facts: list[schema.Fact]) -> str:
    """The generated profile; a sentence of several facts stands once in texts."""
    records = [record(proof) for proof in facts]
    counts = Counter(row["evidence"]["sentence"] for row in records)
    texts = [sentence for sentence, number in counts.items() if number > 1]
    codes = {sentence: code for code, sentence in enumerate(texts)}
    lines = [schema.GENERATED_NOTE.rstrip()]
    if texts:
        lines += ["", "# Fact sentences stated more than once, named by position.",
                  f"{schema.TEXTS} = [", *(f" {literal(sentence)}," for sentence in texts), "]"]
    lines += ["", schema.GENERATED_HEADER.rstrip()]
    for name, value in data.items():
        items = value if isinstance(value, list) else [value]
        for item in items:
            lines += ["", f"[[{name}]]" if isinstance(value, list) else f"[{name}]"]
            lines.extend(f"{schema.key_text(key)} = {literal(v)}" for key, v in item.items())
    for row in records:
        evidence = row["evidence"]
        evidence["sentence"] = codes.get(evidence["sentence"], evidence["sentence"])
        lines += ["", "[[fact]]"]
        lines.extend(f"{key} = {literal(value)}" for key, value in row.items())
    text = "\n".join(lines) + "\n"
    tomllib.loads(text)
    return text


def comparison(result: Result, reference: Profile | None) -> dict:
    if reference is None:
        return {"available": False}
    reference = Profile.load(reference.path)
    sources = check.sources_of(reference.path)
    raw = {k: v for k, v in result.data.items() if k != "family"}
    merged = schema.merge(schema.merge(sources.family_data, raw), sources.overlay)
    expected = reference.get("font", [])
    derived = {font["name"]: font for font in result.data.get("font", [])}
    full = {font["name"]: font for font in merged.get("font", [])}
    rows, manual = [], []
    for font in expected:
        name = font["name"]
        wanted = {key: value for key, value in font.items() if key in ROM_FONT_FIELDS}
        actual = {key: value for key, value in derived.get(name, {}).items() if key in ROM_FONT_FIELDS}
        clean = lambda value: {key: item for key, item in value.items() if not schema.is_prose(key)}
        rows.append({"name": name, "raw_ROM_fields_equal": check.same(actual, wanted),
                     "full_merged_fields_equal": check.same(clean(full.get(name, {})), clean(font))})
        for key in set(font) - ROM_FONT_FIELDS:
            if not schema.is_prose(key):
                manual.append({"font": name, "field": key, "value": font[key], "source": "selected overlay configuration"})
    return {"available": True, "fonts": rows, "raw_ROM_fonts_equal": sum(r["raw_ROM_fields_equal"] for r in rows),
            "full_merged_fonts_equal": sum(r["full_merged_fields_equal"] for r in rows),
            "expected_fonts": len(expected), "retained_presentation_bindings": manual,
            "full_merged_data_equal": check.same(merged, reference.data),
            "scope": "Comparison only; selected overlay facts are not discovery inputs or generated values."}


def _structural_lines(facts: list[schema.Fact]) -> list[str]:
    rows = [item for item in facts if table_scope.selected(item)]
    lines = ["", "## Structural discovery", "",
             *check.items([("Scope", "discovered ROM bindings and layouts"), ("Whole-ROM coverage", "unverified")]), "",
             "| Section | Verified | Candidate | Omitted | Unresolved |",
             "|---|---:|---:|---:|---:|"]
    for section in sorted({item.section for item in rows}):
        counts = [sum(item.section == section and item.status == status for item in rows)
                  for status in schema.STATUSES]
        lines.append(f"| `{section}` | " + " | ".join(str(count) for count in counts) + " |")
    if not rows:
        lines.append("| None | 0 | 0 | 0 | 0 |")
    return lines


def report(result: Result, name: str, declared=None) -> str:
    candidates = [item for item in result.issues if item.status == "candidate"]
    omitted = [item for item in result.issues if item.status == "omitted"]
    unresolved = [item for item in result.issues if item.status == "unresolved"]
    summary = check.report(name, result.facts, candidates, unresolved, omitted)
    lines = summary.splitlines()
    if "observations" in result.metadata:
        from .observations import report_lines
        lines += report_lines(result.metadata["observations"]["samples"])
    if declared and (declared.get("unresolved") or declared.get("shortfall")):
        known, new = residue.unresolved(result.issues, declared)
        lines += ["", "## Named residue", "",
                  *check.items([("Acknowledged unresolved facts", len(known)), ("New unresolved facts", len(new)),
                                ("Effect of an acknowledgement on coverage", "none")])]
    lines += _structural_lines(result.facts + result.issues)
    lines += ["", "## Family evidence", "",
              "| Family | Fingerprints | Selected | Header valid | Categories |",
              "|---|---:|---|---|---|"]
    for family in result.metadata["families"]:
        lines.append(f"| `{family['name']}` | {family['matched']}/{family['total']} ({family['ratio']:.0%}) | "
                     f"{str(family['selected']).lower()} | {str(family['header_valid']).lower()} | "
                     + ", ".join(family["categories"]) + " |")
    for family in result.metadata["families"]:
        lines += ["", f"### `{family['name']}`", "", "| Function | Addresses |", "|---|---|"]
        for function, addresses in family["functions"].items():
            lines.append(f"| `{function}` | " + (", ".join(f"`{at:#x}`" for at in addresses) or "unresolved") + " |")
    selected = result.metadata.get("codec")
    lines += ["", "## Codec and controls", ""]
    if selected:
        lines += [*check.items([("Selected codec", f"`{selected['candidate']}`"),
                                ("Control contract", f"`{selected['control_contract']}`"),
                                ("Selection", "family anchors"), ("Unobserved and custom controls", "unverified")]), "",
                  "| Candidate | Text anchors | Control anchors | Score |", "|---|---:|---:|---:|"]
        for score in selected["scores"]:
            lines.append(f"| `{score['candidate']}` | {score['matched_anchors']} | "
                         f"{score['matched_controls']} | {score['score']} |")
    else:
        lines += check.items([("Selected codec", "unresolved: no unique supported family")])
    lines += ["", "## Verified font bindings", "",
              "| Font | Glyphs | Widths | Count | Format | Printer IDs |", "|---|---|---|---:|---|---|"]
    for font in result.data.get("font", []):
        lines.append(f"| `{font['name']}` | `{font['glyphs']:#x}` | `{font['widths']:#x}` | "
                     f"{font['count']} | `{font.get('format', '16x16')}` | {font['codes']} |")
    scripts = result.metadata.get("scripts")
    if scripts is not None:
        lines += ["", "## Script commands", ""]
        if scripts.get("table") is None:
            rows = [("Command table", "unresolved: no unique command table")]
        else:
            handlers = scripts.get("handlers", [])
            counts = {status: sum(handler["status"] == status for handler in handlers)
                      for status in ("fixed", "variable", "unresolved")}
            rows = [("Command table", f"`{scripts['table']:#x}`-`{scripts['end']:#x}`"),
                    ("Initializer", f"`{scripts['initializer']:#x}`"), ("Dispatcher", f"`{scripts['dispatcher']:#x}`"),
                    ("Handlers", len(handlers)), ("Fixed", counts["fixed"]), ("Variable", counts["variable"]),
                    ("Unresolved", counts["unresolved"])]
        lines += check.items([*rows, ("Handler evidence and compatibility comparisons", "discovery metadata")])
    slots = result.metadata["free_slots"]
    lines += ["", "## Character slots", ""]
    if slots["status"] == "candidate":
        lines += [*check.items([("Status", slots["status"]),
                                ("Scope", "supplied ROM-matched inventory, unverified entries included"),
                                ("Whole-ROM allocation", "unverified"),
                                ("Inventory entries", slots["inventory_entries"]),
                                ("Inventory digest", f"`{slots['inventory_content_sha256']}`"),
                                ("Scoped free bytes", " ".join(f"`{byte:02X}`" for byte in slots["free"]))]), "",
                  "| Candidates | Used | Blank in every font | Scoped free |", "|---:|---:|---:|---:|",
                  f"| {len(slots['candidates'])} | {len(slots['used'])} | "
                  f"{len(slots['blank'])} | {len(slots['free'])} |"]
    else:
        lines += check.items([("Status", slots["status"]), ("Scope", slots["scope"])])
    return "\n".join(lines).rstrip() + "\n"


def write(result: Result, directory: Path, name: str, reference: Profile | None = None) -> dict:
    """Validate every output before replacing generated files; preserve overlays."""
    directory = Path(directory)
    if directory.exists() and not directory.is_dir():
        raise ValueError("discovery output must be a profile directory")
    overlays = schema.overlay_files(directory)
    for path in [directory / schema.GENERATED, *(file for file in overlays if file.suffix == ".toml")]:
        if path.is_file():
            prior = tomllib.loads(path.read_text(encoding="utf-8")).get("rom", {}).get("sha1")
            if prior is not None and prior != result.data["rom"]["sha1"]:
                raise ValueError(f"{path}: output profile identifies another ROM")
    data, facts = copy.deepcopy(result.data), list(result.facts)
    if not overlays:
        data["support"] = {"stage": "discovery"}
        facts.append(fact("support.stage", "project", "discovery",
                          evidence("A standalone partial profile grants discovery operations only.", "output-policy"), "shape"))
    compared = comparison(result, reference)
    structural = None
    if reference is not None:
        expected = schema.overlay_facts(check.sources_of(reference.path))
        structural = table_scope.compare(expected, result.facts)
        structural = {**structural, "full_overlay_facts": len(expected)}
    discovered = {key: value for key, value in result.metadata.items() if key != "schema"}
    discovered["comparison"] = compared
    outputs = {schema.GENERATED: document(data, facts), schema.DISCOVERY: metadata.encode(discovered)}
    declared = residue.read(reference.path, result.data["rom"]["sha1"]) if reference else None
    outputs[schema.REPORT] = report(result, name, declared)
    with tempfile.TemporaryDirectory(prefix="discovery-validate-") as temporary:
        stage = Path(temporary)
        for filename in (schema.GENERATED, schema.DISCOVERY):
            (stage / filename).write_text(outputs[filename], encoding="utf-8")
        sources = schema.read(stage)
        schema.generated_facts(sources)
        if sources.generated_records != [record(proof) for proof in facts]:
            raise ValueError("fact records differ once stored")
        if sources.header != {"schema": schema.SCHEMA, **json.loads(json.dumps(discovered))}:
            raise ValueError("discovery metadata differs once stored")
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".discovery-", dir=directory) as temporary:
        for filename, text in outputs.items():
            target = directory / filename
            staged = Path(temporary) / filename
            staged.write_text(text, encoding="utf-8")
            if target.exists():
                staged.chmod(stat.S_IMODE(target.stat().st_mode))
            os.replace(staged, target)
    output = {"profile": str(directory / schema.GENERATED), "metadata": str(directory / schema.DISCOVERY),
              "report": str(directory / schema.REPORT),
              "verified": len(facts), "candidate": sum(f.status == "candidate" for f in result.issues),
              "omitted": sum(f.status == "omitted" for f in result.issues),
              "unresolved": sum(f.status == "unresolved" for f in result.issues), "comparison": compared}
    if structural:
        output["structural"] = {
            "structural_facts": structural["structural_facts"],
            "raw_named_facts": structural["raw_named_facts"],
            "full_overlay_facts": structural["full_overlay_facts"],
            "reproduced": sum(row.get("outcome") == "reproduced" for row in structural["facts"]),
            "sections": structural["sections"],
            "unresolved": [{"id": row["id"], "fields": row.get("fields", []),
                            "reason": row.get("reason", "no reason recorded")}
                           for row in structural["facts"] if row.get("outcome") == "unresolved"]}
    return output
