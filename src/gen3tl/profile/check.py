"""Coverage of a profile by discovery: what the generated profile reproduces, what stays manual."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from . import configuration, schema

OUTCOMES = ("manual", "reproduced", "extends", "granularity", "contradicts", "configuration")


@dataclass
class Granularity:
    generated: tuple[schema.Fact, ...]
    overlay: tuple[schema.Fact, ...]


@dataclass
class Comparison:
    fact: schema.Fact
    generated: schema.Fact | None
    outcome: str
    fields: tuple[str, ...] = ()
    related: tuple[schema.Fact, ...] = ()


@dataclass
class Section:
    name: str
    kind: str
    facts: int = 0
    generated: int = 0
    overlay: int = 0
    granular_generated: int = 0
    granular_conflicts: int = 0
    counts: dict = field(default_factory=lambda: dict.fromkeys(OUTCOMES, 0))

    @property
    def rom_facts(self) -> int:
        return self.facts - self.counts["configuration"]

    @property
    def covered(self) -> int:
        """Facts whose generated value stands without overlay correction."""
        return (self.generated - self.counts["extends"] - self.counts["contradicts"]
                - self.granular_generated + self.granular_conflicts)


@dataclass
class Result:
    sources: schema.Sources
    generated: list[schema.Fact]
    comparisons: list[Comparison]
    granularities: list[Granularity]
    sections: list[Section]
    conflicts: list[Comparison] = field(default_factory=list)

    @property
    def contradictions(self) -> list[Comparison]:
        return [c for c in self.comparisons if c.outcome == "contradicts"] + self.conflicts


def same(a: Any, b: Any) -> bool:
    """Equal values of equal types, so 1 differs from true."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


def compare(over: Any, generated: Any) -> tuple[str, tuple[str, ...]]:
    if isinstance(over, dict) and isinstance(generated, dict):
        stated = [k for k in over if not schema.is_prose(k)]
        differ = tuple(k for k in stated if k in generated and not same(over[k], generated[k]))
        if differ:
            return "contradicts", differ
        missing = tuple(k for k in stated if k not in generated)
        return ("extends", missing) if missing else ("reproduced", ())
    return ("reproduced", ()) if same(over, generated) else ("contradicts", ("value",))


def sources_of(path: str | Path) -> schema.Sources:
    path = Path(path)
    return schema.read(path) if path.is_dir() else schema.flat(path)


def _child_ids(fact: schema.Fact) -> tuple[str, ...]:
    if not isinstance(fact.value, dict):
        return ()
    return tuple(f"{fact.id}.{schema.key_text(key)}" for key in fact.value if not schema.is_prose(key))


def _granularities(generated: list[schema.Fact], overlay: list[schema.Fact]) -> tuple[
        list[Granularity], dict[str, tuple[schema.Fact, ...]]]:
    by_id = {f.id: f for f in generated}
    exact = {f.id for f in overlay if f.id in by_id}
    used_generated, used_overlay = set(exact), set(exact)
    overlay_by_id = {f.id: f for f in overlay}
    relations = []
    related = {}

    def relate(parent: schema.Fact, candidates: dict[str, schema.Fact], used: set[str],
               generated_parent: bool) -> tuple[schema.Fact, ...] | None:
        matches = tuple(candidates[child_id] for child_id in _child_ids(parent)
                        if child_id in candidates and child_id not in used)
        if len(matches) < 2:
            return None
        used.update(f.id for f in matches)
        if generated_parent:
            relations.append(Granularity((parent,), matches))
            for fact in matches:
                related[fact.id] = (parent,)
        else:
            relations.append(Granularity(matches, (parent,)))
            related[parent.id] = matches
        return matches

    for fact in overlay:
        if fact.id not in used_overlay:
            relate(fact, by_id, used_generated, False)
            if fact.id in related:
                used_overlay.add(fact.id)
    for fact in generated:
        if fact.id not in used_generated:
            matches = relate(fact, overlay_by_id, used_overlay, True)
            if matches is not None:
                used_generated.add(fact.id)
    return relations, related


def _canonical_fields(facts: tuple[schema.Fact, ...]) -> dict[str, schema.Fact]:
    if len(facts) > 1:
        return {fact.id: fact for fact in facts}
    parent = facts[0]
    return {
        child_id: replace(parent, id=child_id, value=parent.value[key], node=None)
        for key in parent.value if not schema.is_prose(key)
        for child_id in (f"{parent.id}.{schema.key_text(key)}",)
    }


def _value_conflicts(granularities: list[Granularity]) -> list[Comparison]:
    conflicts = []
    for relation in granularities:
        generated = _canonical_fields(relation.generated)
        for fact in _canonical_fields(relation.overlay).values():
            other = generated.get(fact.id)
            if other is None:
                continue
            outcome, fields = compare(fact.value, other.value)
            if outcome == "contradicts":
                conflicts.append(Comparison(fact, other, outcome, fields))
    return conflicts


def run(path: str | Path) -> Result:
    sources = sources_of(path)
    generated = schema.generated_facts(sources)
    for index, fact in enumerate(generated):
        if fact.section == "strings.classified":
            action, value = schema.classifier_target(fact.value)
            section = f"strings.{action}"
            generated[index] = replace(fact, id=f"{section}[{schema.atom(value)}]",
                                       section=section, value=value)
    overlay = schema.overlay_facts(sources)
    by_id = {f.id: f for f in generated}
    granularities, related = _granularities(generated, overlay)
    comparisons = []
    for fact in overlay:
        other = by_id.get(fact.id)
        value = configuration.value(fact.id, fact.section, fact.value)
        if value is None:
            outcome, fields = "configuration", ()
        elif fact.id in related:
            outcome, fields = "granularity", ()
        else:
            outcome, fields = ("manual", ()) if other is None else compare(value,
                configuration.value(other.id, other.section, other.value))
        comparisons.append(Comparison(fact, other, outcome, fields, related.get(fact.id, ())))
    conflicts = _value_conflicts(granularities)
    sections: dict[str, Section] = {}

    def section(name: str) -> Section:
        return sections.setdefault(name, Section(name, schema.kind_of(name)))
    granular_generated = {f.id for relation in granularities for f in relation.generated}
    for fact in generated:
        row = section(fact.section)
        row.generated += 1
        row.granular_generated += fact.id in granular_generated
    for c in comparisons:
        row = section(c.fact.section)
        row.overlay += 1
        row.counts[c.outcome] += 1
    for c in conflicts:
        row = section(c.fact.section)
        row.counts[c.outcome] += 1
        row.granular_conflicts += 1
    overlay_ids = {c.fact.id for c in comparisons}
    for fact in generated:
        section(fact.section).facts += fact.id not in overlay_ids
    for c in comparisons:
        section(c.fact.section).facts += 1
    return Result(sources, generated, comparisons, granularities, list(sections.values()), conflicts)


def _share(part: int, whole: int) -> str:
    return f"{part / whole:.0%}" if whole else "-"


def tier_counts(result: Result, sections: Sequence[str] | None = None) -> dict[str, dict[str, int]]:
    """Count evidence tiers independently for each source layer."""
    layers = {
        "family": result.sources.family_facts,
        "generated": result.generated,
        "overlay": [comparison.fact for comparison in result.comparisons],
    }
    counts = {}
    for name, facts in layers.items():
        row = dict.fromkeys((*schema.TIERS, "unstated"), 0)
        for fact in facts:
            if not sections or fact.section in sections or fact.section.split(".")[0] in sections:
                row[fact.tier or "unstated"] += 1
        counts[name] = row
    return counts


def _short(text: str, limit: int = 100) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _field(fact: schema.Fact, key: str) -> str:
    value = fact.value.get(key) if isinstance(fact.value, dict) else fact.value
    return _short(repr(value), 60)


def text(result: Result, name: str, sections: list[str] | None = None, listing: bool = True) -> str:
    """The coverage table, granularity differences, contradictions and overlay facts, as Markdown."""
    chosen = [s for s in result.sections if not sections or s.name in sections or s.name.split(".")[0] in sections]
    names = {s.name for s in chosen}
    lines = [f"# Profile check: {name}", ""]
    if result.sources.header:
        lines.append(f"Generated profile: {len(result.generated)} facts; overlay: {len(result.comparisons)} facts.")
    else:
        lines.append(f"Flat profile: no generated profile; all {len(result.comparisons)} facts are manual.")
    lines += ["", "| Section | Kind | Facts | Generated | Overlay | Reproduced | Extended | Granularity | Contradicted | Coverage |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    total = Section("total", "")
    for row in chosen:
        lines.append(f"| {row.name} | {row.kind} | {row.facts} | {row.generated} | {row.overlay} | "
                     f"{row.counts['reproduced']} | {row.counts['extends']} | {row.counts['granularity']} | "
                     f"{row.counts['contradicts']} | {_share(row.covered, row.rom_facts)} |")
        total.facts += row.facts
        total.generated += row.generated
        total.overlay += row.overlay
        total.granular_generated += row.granular_generated
        total.granular_conflicts += row.granular_conflicts
        for outcome in OUTCOMES:
            total.counts[outcome] += row.counts[outcome]
    lines.append(f"| total | | {total.facts} | {total.generated} | {total.overlay} | {total.counts['reproduced']} | "
                 f"{total.counts['extends']} | {total.counts['granularity']} | {total.counts['contradicts']} | "
                 f"{_share(total.covered, total.rom_facts)} |")
    lines += ["", "Coverage: facts the generated profile holds with the overlay agreeing or silent.", "",
              f"Configuration-only facts excluded from the denominator: {total.counts['configuration']}.", "",
              "## Evidence tiers", "",
              "Source layers are counted independently; family defaults do not count as generated coverage.", "",
              "| Source | Shape | Crossref | Ingame | Unstated | Total |", "|---|---|---|---|---|---|"]
    for source, counts in tier_counts(result, sections).items():
        values = " | ".join(str(count) for count in counts.values())
        lines.append(f"| {source} | {values} | {sum(counts.values())} |")
    lines.append("")
    granularities = [g for g in result.granularities if g.overlay[0].section in names]
    lines += [f"## Granularity differences ({len(granularities)})", ""]
    for relation in granularities:
        generated_ids = ", ".join(f"`{fact.id}`" for fact in relation.generated)
        overlay_ids = ", ".join(f"`{fact.id}`" for fact in relation.overlay)
        lines.append(f"- generated {generated_ids}; overlay {overlay_ids}")
    if not granularities:
        lines.append("None.")
    contradictions = [c for c in result.contradictions if c.fact.section in names]
    lines += ["", f"## Contradictions ({len(contradictions)})", ""]
    for c in contradictions:
        details = "; ".join(f"{k}: generated {_field(c.generated, k)}, overlay {_field(c.fact, k)}" for k in c.fields)
        lines.append(f"- `{c.fact.id}`: {details}")
    if not contradictions:
        lines.append("None.")
    reproduced = [c for c in result.comparisons if c.outcome == "reproduced" and c.fact.section in names]
    if reproduced:
        lines += ["", f"{len(reproduced)} overlay facts are reproduced by discovery and can leave the overlay."]
    if listing:
        chosen_facts = [c for c in result.comparisons if c.fact.section in names]
        lines += ["", f"## Overlay facts ({len(chosen_facts)})", "",
                  "| Outcome | Kind | Tier | Fact | Evidence |", "|---|---|---|---|---|"]
        for c in chosen_facts:
            evidence = _short(c.fact.evidence.sentence) if c.fact.evidence else "none stated"
            lines.append(f"| {c.outcome} | {c.fact.kind} | {c.fact.tier or 'unstated'} | `{_short(c.fact.id, 80)}` | "
                         f"{evidence.replace('|', '/')} |")
    return "\n".join(lines) + "\n"


def items(rows) -> list[str]:
    """A two-column table of named values."""
    return ["| Item | Value |", "|---|---|", *(f"| {item} | {value} |" for item, value in rows)]


def report(name: str, verified: Sequence[schema.Fact], candidates: Sequence[schema.Fact] = (),
           unresolved: Sequence[schema.Fact] = (), omitted: Sequence[schema.Fact] = ()) -> str:
    """Count discovery states and list candidate, omitted and unresolved facts."""
    lines = [f"# Discovery report: {name}", "",
             *items([("Generated profile", f"`{schema.GENERATED}`"),
                     ("Inputs", "base ROM, family files, optional ROM-matched inventory"),
                     ("Overlay coverage", "`gen3tl discover check`")]), ""]
    if not verified and not candidates and not unresolved and not omitted:
        lines += ["- Discovered facts: 0", ""]
    lines += ["| Kind | Verified | Candidate | Omitted | Unresolved |", "|---|---|---|---|---|"]
    for kind in schema.KINDS:
        counts = [sum(f.kind == kind for f in group) for group in (verified, candidates, omitted, unresolved)]
        lines.append(f"| {kind} | {counts[0]} | {counts[1]} | {counts[2]} | {counts[3]} |")
    for title, group in (("Candidates", candidates), ("Omitted", omitted), ("Unresolved", unresolved)):
        lines += ["", f"## {title}", ""]
        if group:
            lines += ["| Fact | Kind | Statement |", "|---|---|---|"]
        for f in group:
            statement = " ".join((f.evidence.sentence if f.evidence else "no evidence").split()).replace("|", "/")
            lines.append(f"| `{f.id}` | {f.kind} | {statement} |")
        if not group:
            lines.append("- None")
    return "\n".join(lines) + "\n"
