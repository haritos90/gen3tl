"""A declared structural inventory, separate from raw schema coverage."""

from __future__ import annotations

from collections import Counter, defaultdict

from ..profile import check, schema

PHYSICAL_ROOTS={'pokedex.abilities','pokedex.species','pokedex.moves','pokedex.moves.descriptions','pokedex.abilities.descriptions','pokedex.items','pokedex.items.tm_moves','pokedex.items.mining','pokedex.learnsets.level_up','pokedex.learnsets.tmhm','pokedex.learnsets.egg','pokedex.learnsets.tutor','pokedex.trainers','pokedex.trainers.fields','pokedex.trainers.party','pokedex.trainers.pictures','pokedex.trainers.pictures.front','pokedex.trainers.pictures.palettes','pokedex.trainers.class_names','pokedex.sources.commands','pokedex.sources.trades','pokedex.type_icons','pokedex.icons.split','pokedex.icons.items','pokedex.map_sections','pokedex.type_effectiveness','pokedex.dex.map','pokedex.dex.records','pokedex.dex.records.fields','pokedex.evolution','pokedex.maps','pokedex.growth_rates'}
PHYSICAL_PREFIXES=('pokedex.species.fields.','pokedex.species.ev_yield','pokedex.moves.fields.','pokedex.moves.lists[','pokedex.items.fields','pokedex.items.mining.item','pokedex.encounters.headers.','pokedex.encounters.methods.','pokedex.encounters.fishing.','pokedex.encounters.context','pokedex.dex.records.fields.','pokedex.evolution.fields.','pokedex.sprites.')
CONFIGURATION_FIELDS={'name','key','glossary','windows','long_name_fields','pad','save_link','name_table','class_table','category_table','index','symbol','0','1','2','3','4','5','tiers'}
RESIDUE_REASONS={
    'table[name=types_short]': 'unimplemented path: the anchored short-type-name copy has no linked indexed reader in the current implementation; the name anchors exist but no accessor witness is emitted.',
    'pokedex.moves.lists[key=quiet_bgm]': 'unimplemented path: no move-list reader descriptor for quiet_bgm is implemented, so no discovery was attempted.',
    'pokedex.moves.descriptions': 'unimplemented path: no move-description pointer-array reader is implemented, so no discovery was attempted.',
    'pokedex.encounters.context': 'unimplemented path: no context-bank discovery path is implemented.',
    'pokedex.species.ev_yield': 'unimplemented path: species record fields are recovered except ev_yield; no signed/width accessor for ev_yield is emitted.',
}


def selected(fact: schema.Fact) -> bool:
    return fact.section in ("table", "strings.pointer_table", "keyboard.table") or fact.section == "pokedex" or fact.section.startswith("pokedex.")


def classify(fact: schema.Fact) -> dict:
    identifier, value = fact.id, fact.value
    candidate = (fact.section in ("table", "strings.pointer_table", "keyboard.table")
                 or identifier in PHYSICAL_ROOTS or identifier.startswith(PHYSICAL_PREFIXES))
    fields, retained = [], []
    if candidate and isinstance(value, dict):
        fields = [key for key in value if key not in CONFIGURATION_FIELDS and not schema.is_prose(key)
                  and not key.startswith("where")]
        retained = [key for key in value if key in CONFIGURATION_FIELDS]
    elif candidate:
        fields = ["value"]
    included = candidate and bool(fields)
    if included:
        category = "ROM_structure"
        reason = "Physical binding or record layout; membership does not depend on discovery or trust tier."
    elif identifier.startswith(("pokedex.labels", "pokedex.form_names")):
        category, reason = "configuration", "Selected label/form identity mapping, not table storage or layout."
    elif isinstance(value, dict) and all(schema.is_prose(key) for key in value):
        category, reason = "documentation", "Evidence container without a physical value; children are counted separately."
    elif identifier.startswith(("pokedex.sources.regions[", "pokedex.sources.linked_regions[")):
        category, reason = "configuration", "Selected scan/acceptance range, not table storage or layout."
    elif identifier.startswith(("pokedex.moves.power.", "pokedex.moves.mechanics.", "pokedex.moves.readers[",
                               "pokedex.trainers.difficulty", "pokedex.trainers.maps", "pokedex.trainers.ev_training",
                               "pokedex.trainers.kinds", "pokedex.sources.choices[", "pokedex.learnsets.form_reversions",
                               "pokedex.gender_ratios", "pokedex.egg_groups", "pokedex.tile_behaviors")):
        category, reason = "semantic_behavior", "Behavior/source association or authored category, outside table storage/layout discovery."
    elif identifier.startswith("pokedex.encounters.unavailable["):
        category, reason = "configuration", "Runtime availability classification, not an unconditional static table."
    else:
        category, reason = "configuration", "Logical identity, enum/label mapping or selected presentation binding."
    return {"id": identifier, "section": fact.section, "included": included, "category": category,
            "reason": reason, "structural_fields": fields if included else [],
            "retained_configuration_fields": retained}


def compare(expected: list[schema.Fact], derived: list[schema.Fact]) -> dict:
    actual = {fact.id: fact for fact in derived if fact.status == "verified" and fact.scope == "instance"
              and fact.source.startswith("tables/")}
    rows, sections = [], defaultdict(Counter)
    for fact in expected:
        if not selected(fact):
            continue
        row = classify(fact)
        if not row["included"]:
            row["outcome"] = row["category"]
            sections[fact.section][row["category"]] += 1
            rows.append(row)
            continue
        other = actual.get(fact.id)
        if other is None and isinstance(fact.value, dict):
            children = {identifier: child for identifier, child in actual.items()
                        if identifier.startswith(fact.id + ".")}
            if children:
                other = schema.Fact(fact.id, fact.section, fact.kind,
                                    {identifier.rsplit(".", 1)[1]: child.value
                                     for identifier, child in children.items()},
                                    tier=next(iter(children.values())).tier,
                                    status="verified",
                                    source=next(iter(children.values())).source)
        missing = []
        for key in row["structural_fields"]:
            wanted = fact.value if key == "value" else fact.value[key]
            value = (other.value if key == "value" else other.value.get(key)
                     if isinstance(other.value, dict) else None) if other is not None else None
            if not check.same(wanted, value):
                missing.append(key)
        row.update(outcome="reproduced" if not missing else "unresolved", fields=missing,
                   tier=other.tier if other is not None else None,
                   discovery_source=other.source if other is not None else None)
        if missing:
            if fact.id in RESIDUE_REASONS:
                row["reason"] = RESIDUE_REASONS[fact.id]
            else:
                row["reason"] = "No verified ROM discovery reproduced: " + ", ".join(missing) + "."
        sections[fact.section]["structural"] += 1
        sections[fact.section][row["outcome"]] += 1
        rows.append(row)
    return {"raw_named_facts": len(rows), "structural_facts": sum(row["included"] for row in rows),
            "sections": {key: dict(value) for key, value in sorted(sections.items())}, "facts": rows}
