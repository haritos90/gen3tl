"""Profile-conditioned script studies as evidence-bearing string facts."""

from __future__ import annotations

from copy import deepcopy

from ..profile import schema
from .foundations import Result, evidence

SOURCE = "script-flow/1.0.0"
INPUTS = ("script", "maps", "table", "strings", "context", "reach")


def inputs(profile):
    """Retain input provenance without promoting declarations to discoveries."""
    data = {section: profile.get(section, {}) for section in INPUTS}
    data["reach"] = {key: value for key, value in data["reach"].items() if key != "observed"}
    return [{"id": item.id, "value_sha256": schema.value_digest(item.value)}
            for item in schema.facts(data)]


def collect(section, study, inventory, profile):
    """Attach assumptions and source locations to every derived string record."""
    entries = {entry["id"]: entry for entry in inventory["entries"]}
    result = Result({}, [], {"study": {key: value for key, value in study.items() if key != "strings"},
                            "inputs": inputs(profile), "section": section})
    for sid, value in study["strings"].items():
        entry = entries[sid]
        uses = value.get("uses", [])
        sites = [use["at"] for use in uses if isinstance(use.get("at"), int)]
        sites += [step["at"] for step in value.get("evidence", []) if isinstance(step.get("at"), int)]
        if section == "reach":
            sentence = ("Static pointer and script flow classify this string under the listed profile inputs; "
                        "unseen computed and runtime paths remain outside the analysis.")
            counts = {"commands": len(value.get("commands", [])), "evidence_steps": len(value.get("evidence", []))}
        else:
            sentence = ("Script flow derives uses, speakers, trainers, gender hints and buffer fills under the listed "
                        "profile inputs; nearby-map inference and declared gender/native semantics are assumptions.")
            counts = {"uses": len(uses), "trainers": sum("trainer" in use for use in uses),
                      "speakers": len(value.get("speakers", [])), "slots": len(value.get("slots", {}))}
        proof = evidence(sentence, "profile-conditioned-script-flow", [entry["offset"]], sites, **counts)
        result.facts.append(schema.Fact(f"{section}.strings[id={schema.atom(sid)}]", section, section,
                                       {"id": sid, "record": deepcopy(value)}, tier="shape", source=SOURCE,
                                       evidence=proof))
    return result


def project(result):
    """Render the compatibility study exclusively from its derived records."""
    output = deepcopy(result.metadata["study"])
    strings = {}
    for item in result.facts:
        sid = item.value["id"]
        if sid in strings or item.status != "verified":
            raise ValueError("invalid study fact projection")
        strings[sid] = deepcopy(item.value["record"])
    output["strings"] = strings
    return output


def document(result):
    """Serialize the study facts separately from compatibility consumers."""
    from .emission import record

    return {"schema": "1.0.0", "metadata": result.metadata,
            "facts": [{**record(item), "value": item.value} for item in result.facts]}
