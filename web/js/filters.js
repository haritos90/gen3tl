// The Pokédex filter registries and their rendered block.

import { el, setText, updateChildren } from "./dom.js";
import { basePower, effectFilterKey, matchesEffect, resolvedEffects } from "./moveeffects.js";
import { flagName, moveListName, targetName } from "./movelabels.js";
import { formatNumber, siteText, t } from "./ui.js";

export const OPERATORS = [">=", "<=", "=", "between"];
export const OPERATOR_NAMES = { ">=": "≥", "<=": "≤", "=": "=", "between": "Between" };

export const EGG_GROUPS = {
  1: "Monster", 2: "Water 1", 3: "Bug", 4: "Flying", 5: "Field", 6: "Fairy", 7: "Grass", 8: "Human-Like",
  9: "Water 3", 10: "Mineral", 11: "Amorphous", 12: "Water 2", 13: "Ditto", 14: "Dragon", 15: "Undiscovered",
};
export const TRAINER_KINDS = {
  league: "Gym leaders and league", story: "Story characters", world: "World trainers",
  special: "Special buildings", partners: "Battle partners", paired: "Trainer pairs",
};
export const DIFFICULTY_SETTINGS = ["difficult", "vanilla", "expert", "insane"];
export const SETTING_LABELS = { difficult: "Difficult", vanilla: "Vanilla", expert: "Expert", insane: "Insane" };

const FORM_CHANGE_METHODS = new Set([253, 254]);
const ITEM_METHODS = new Set([7, 34, 36, 39]);
const TRADE_METHODS = new Set([5, 6]);
const LEVEL_METHODS = new Set([1, 2, 3, 4, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18,
  19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 31, 32, 35, 37, 38]);
const PROPERTY_TESTS = {
  secondary: { label: "Secondary effect chance", matches: (record) => record.secondary_chance > 0 },
};

const legacyMinimum = (value) => value == null || value === "" ? null : { op: ">=", a: value };

/** Compare one record number against an operator value. */
export function numberMatches(actual, value) {
  if (value == null || value.a == null || value.a === "") return true;
  const a = Number(value.a);
  if (!Number.isFinite(a)) return true;
  if (!Number.isFinite(actual)) return false;
  const op = value.op ?? ">=";
  if (op === ">=") return actual >= a;
  if (op === "<=") return actual <= a;
  if (op === "=") return actual === a;
  if (op === "between") {
    const b = Number(value.b);
    return Number.isFinite(b) ? actual >= a && actual <= b : true;
  }
  return true;
}

/** A value counts once it holds an actual criterion. */
export function activeValue(value) {
  if (value == null || value === "") return false;
  if (value instanceof Set) return true; // a prepared empty set still matches nothing
  if (Array.isArray(value)) return value.length > 0;
  return true;
}

/** Resolve a free-text query to matching move ids. */
function moveSet(value, context) {
  const q = String(value ?? "").trim().toLowerCase();
  if (!q) return null;
  const set = new Set();
  for (const row of context.moves ?? []) {
    const names = [context.text(row.name), ...(context.otherNames?.(row.name) ?? [])];
    if (names.some((name) => name.toLowerCase().includes(q))) set.add(row.id);
  }
  return set;
}

/** Resolve a species query to the moves its matches learn. */
function learnerSet(value, context) {
  const q = String(value ?? "").trim().toLowerCase();
  if (!q) return null;
  const set = new Set();
  for (const record of context.species ?? []) {
    const names = context.speciesNames?.(record) ?? [context.text(record.name)];
    if (!names.some((name) => name.toLowerCase().includes(q))) continue;
    for (const row of record.learnset ?? []) set.add(row.move);
  }
  return set;
}

/** Resolve a type to its move ids. */
function movesOfTypeSet(value, context) {
  return new Set((context.moves ?? []).filter((row) => row.type === Number(value)).map((row) => row.id));
}

/** Compare a base stat, total included, against an operator value. */
function statMatches(record, value) {
  const stats = record.base_stats ?? {};
  const actual = value.stat === "total"
    ? ["hp", "attack", "defense", "sp_attack", "sp_defense", "speed"].reduce((sum, key) => sum + (stats[key] ?? 0), 0)
    : stats[value.stat];
  return numberMatches(actual, value);
}

/** Match any outgoing evolution, excluding form changes. */
function evolutionMatches(record, kind) {
  const edges = (record.evolution ?? []).filter((edge) => !FORM_CHANGE_METHODS.has(edge.method));
  if (kind === "none") return edges.length === 0;
  return edges.some(({ method }) => {
    const value = ITEM_METHODS.has(method) ? "item" : TRADE_METHODS.has(method) ? "trade"
      : LEVEL_METHODS.has(method) ? "level" : "other";
    return value === kind;
  });
}

/** List named move flags and engine lists alphabetically. */
export function propertyOptions(moves) {
  const keys = new Set(moves.flatMap((row) => [...row.flag_keys ?? [], ...row.list_keys ?? []]));
  for (const key of Object.keys(PROPERTY_TESTS)) keys.add(key);
  return [...keys].map((key) => ({ value: key,
    label: t(PROPERTY_TESTS[key]?.label ?? flagName(key) ?? moveListName(key) ?? key) }))
    .sort((a, b) => a.label.localeCompare(b.label));
}

/** Select the behavior displayed for each move. */
export function effectOptions(moves) {
  return resolvedEffects(moves).map(({ key, name }) => ({ value: key, label: name }));
}

/** Offer only targets used by loaded moves. */
export function targetOptions(moves) {
  return [...new Set(moves.map((row) => row.target_key).filter(Boolean))]
    .map((value) => ({ value, label: t(targetName(value) ?? value) }))
    .sort((a, b) => a.label.localeCompare(b.label));
}

/** Resolve a registry label at render time. */
export function filterText(key) {
  switch (key) {
    case "Type": return t("Type");
    case "Type {n}": return t("Type {n}");
    case "Ability": return t("Ability");
    case "Ability {n}": return t("Ability {n}");
    case "Second type": return t("Second type");
    case "Category": return t("Category");
    case "Power": return t("Power");
    case "Accuracy": return t("Accuracy");
    case "Priority": return t("Priority");
    case "PP": return t("PP");
    case "Flinch chance": return t("Flinch chance");
    case "Target": return t("Target");
    case "Effect": return t("Effect");
    case "Properties": return t("Properties");
    case "Learned by": return t("Learned by");
    case "Knows move": return t("Knows move");
    case "Moves of type": return t("Moves of type");
    case "Base stat": return t("Base stat");
    case "Weight (kg)": return siteText("Weight (kg)");
    case "Height (m)": return siteText("Height (m)");
    case "Egg group": return t("Egg group");
    case "Evolution kind": return t("Evolution kind");
    case "Class": return t("Class");
    case "Kind": return t("Kind");
    case "EV training": return t("EV training");
    case "Difficulty": return t("Difficulty");
    case "Filters": return t("Filters");
    case "Reset": return t("Reset");
    case "Advanced": return t("Advanced");
    case "Remove {label}": return t("Remove {label}");
    case "Any": return t("Any");
    case "All": return t("All");
    case "Any type": return t("Any type");
    case "Any second type": return t("Any second type");
    case "Any ability": return t("Any ability");
    case "Any effect": return t("Any effect");
    case "Any difficulty": return t("Any difficulty");
    case "All classes": return t("All classes");
    case "All kinds": return t("All kinds");
    case "Physical": return t("Physical");
    case "Special": return t("Special");
    case "Status": return t("Status");
    case "Formula moves": return t("Formula moves");
    case "Fixed": return t("Fixed");
    case "Never misses": return t("Never misses");
    case "Item": return t("Item");
    case "Trade": return t("Trade");
    case "Level": return t("Level");
    case "Does not evolve": return t("Does not evolve");
    case "Between": return t("Between");
    case "Levels shown here": return t("Levels shown here");
    case "Difficult": return t("Difficult");
    case "Vanilla": return t("Vanilla");
    case "Expert": return t("Expert");
    case "Insane": return t("Insane");
    case "Move name…": return t("Move name…");
    case "Learned by species…": return t("Learned by species…");
    case "Characteristics": return t("Characteristics");
    case "Effect and properties": return t("Effect and properties");
    case "Who learns it": return t("Who learns it");
    case "Type and ability": return t("Type and ability");
    case "Moves": return t("Moves");
    case "Numbers": return t("Numbers");
    case "Other": return t("Other");
    case "Trainer": return t("Trainer");
    case "Gym leaders and league": return t("Gym leaders and league");
    case "Story characters": return t("Story characters");
    case "World trainers": return t("World trainers");
    case "Special buildings": return t("Special buildings");
    case "Battle partners": return t("Battle partners");
    case "Trainer pairs": return t("Trainer pairs");
    case "HP": return t("HP");
    case "Attack": return t("Attack");
    case "Defense": return t("Defense");
    case "Sp. Attack": return t("Sp. Attack");
    case "Sp. Defense": return t("Sp. Defense");
    case "Speed": return t("Speed");
    case "Total": return t("Total");
    case "Monster": return t("Monster");
    case "Water 1": return t("Water 1");
    case "Bug": return t("Bug");
    case "Flying": return t("Flying");
    case "Field": return t("Field");
    case "Fairy": return t("Fairy");
    case "Grass": return t("Grass");
    case "Human-Like": return t("Human-Like");
    case "Water 3": return t("Water 3");
    case "Mineral": return t("Mineral");
    case "Amorphous": return t("Amorphous");
    case "Water 2": return t("Water 2");
    case "Ditto": return t("Ditto");
    case "Dragon": return t("Dragon");
    case "Undiscovered": return t("Undiscovered");
    default: return t(key);
  }
}

/** Sort and label the type rows of the loaded dataset. */
export function typeOptions(types, text) {
  return [...new Set(types.map((row) => row.id))].sort((a, b) => a - b)
    .map((id) => ({ value: String(id), label: text(`types:${id}`) || t("Type {n}", { n: id }) }));
}

/** Build the entries of one mode. */
export const FILTER_SETS = {
  moves: [
    {
      key: "type", label: "Type", group: "Characteristics", kind: "select", empty: "Any type",
      legacy: (saved) => saved["x-mf-type"],
      options: (context) => typeOptions(context.types ?? [], context.text),
      predicate: (record, value) => record.type === Number(value),
    },
    {
      key: "category", label: "Category", group: "Characteristics", kind: "switch",
      legacy: (saved) => saved["x-mf-category"],
      options: [["", "All"], ["0", "Physical"], ["1", "Special"], ["2", "Status"]],
      predicate: (record, value) => record.damage_category === Number(value),
    },
    {
      key: "power", label: "Power", group: "Characteristics", kind: "number", operators: OPERATORS,
      legacy: (saved) => legacyMinimum(saved["x-mf-power"]),
      subKey: "kind", sub: [["", "All"], ["formula", "Formula moves"], ["fixed", "Fixed"]],
      predicate: (record, value) => {
        if (value.kind === "formula" && record.power_kind !== "computed") return false;
        if (value.kind === "fixed" && record.power_kind === "computed") return false;
        return numberMatches(basePower(record), value);
      },
    },
    {
      key: "accuracy", label: "Accuracy", group: "Characteristics", kind: "number", operators: OPERATORS,
      legacy: (saved) => legacyMinimum(saved["x-mf-accuracy"]),
      subKey: "never", sub: [["", "All"], ["never", "Never misses"]],
      predicate: (record, value) => {
        if (value.never && record.accuracy !== 0) return false;
        return numberMatches(record.accuracy, value);
      },
    },
    {
      key: "priority", label: "Priority", group: "Characteristics", kind: "number", operators: OPERATORS,
      legacy: (saved) => legacyMinimum(saved["x-mf-priority"]),
      predicate: (record, value) => numberMatches(record.priority, value),
    },
    {
      key: "pp", label: "PP", group: "Characteristics", kind: "number", operators: OPERATORS,
      predicate: (record, value) => numberMatches(record.pp, value),
    },
    {
      key: "flinchChance", label: "Flinch chance", group: "Characteristics", kind: "number", operators: OPERATORS,
      legacy: (saved) => (Array.isArray(saved.properties) && saved.properties.includes("flinch")) || saved["x-mf-effect"] === "flinch"
        || saved["x-mf-property"] === "flinch" ? { op: ">=", a: 1 } : null,
      predicate: (record, value) => numberMatches(record.flinch_chance, value),
    },
    {
      key: "target", label: "Target", group: "Characteristics", kind: "chips",
      options: (context) => targetOptions(context.moves ?? []),
      predicate: (record, value) => value.includes(record.target_key),
    },
    {
      key: "effect", label: "Effect", group: "Effect and properties", kind: "select", empty: "Any effect", advanced: true,
      legacy: (saved) => ["flinch", "secondary"].includes(saved["x-mf-effect"]) ? null : saved["x-mf-effect"],
      options: (context) => effectOptions(context.moves ?? []),
      normalize: effectFilterKey,
      predicate: matchesEffect,
    },
    {
      key: "properties", label: "Properties", group: "Effect and properties", kind: "chips", advanced: true,
      legacy: (saved) => [saved["x-mf-property"],
        saved["x-mf-effect"] === "secondary" ? "secondary" : null].filter(Boolean),
      normalize: (value) => Array.isArray(value) ? value.filter((key) => key !== "flinch") : [],
      options: (context) => propertyOptions(context.moves ?? []),
      predicate: (record, value) => value.every((key) => PROPERTY_TESTS[key]
        ? PROPERTY_TESTS[key].matches(record) : (record.flag_keys ?? []).includes(key)
          || (record.list_keys ?? []).includes(key)),
    },
    {
      key: "learner", label: "Learned by", group: "Who learns it", kind: "text", list: true, advanced: true,
      legacy: (saved) => saved["x-mf-learner"],
      placeholder: "Learned by species…",
      options: (context) => [...new Set((context.species ?? [])
        .flatMap((record) => context.speciesNames?.(record) ?? [context.text(record.name)]).filter(Boolean))]
        .map((name) => ({ value: name, label: name })),
      prepare: learnerSet,
      predicate: (record, value) => value.has(record.id),
    },
  ],
  species: [
    {
      key: "type", label: "Type", group: "Type and ability", kind: "select", empty: "Any type",
      legacy: (saved) => saved["x-type-filter"],
      options: (context) => typeOptions(context.types ?? [], context.text),
      predicate: (record, value) => (record.types ?? []).includes(Number(value)),
    },
    {
      key: "type2", label: "Second type", group: "Type and ability", kind: "select", empty: "Any second type",
      legacy: (saved) => saved["x-type-filter-2"],
      options: (context) => typeOptions(context.types ?? [], context.text),
      predicate: (record, value) => (record.types ?? []).includes(Number(value)),
    },
    {
      key: "ability", label: "Ability", group: "Type and ability", kind: "select", empty: "Any ability",
      legacy: (saved) => saved["x-ability-filter"],
      options: (context) => (context.abilities ?? [])
        .map((row) => ({ value: String(row.id), label: [...new Set([row.name, ...row.alternate_names ?? []]
          .map((id) => context.text(id)).filter(Boolean))].join(" / ") || t("Ability {n}", { n: row.id }) }))
        .filter((row) => row.label).sort((a, b) => a.label.localeCompare(b.label)),
      predicate: (record, value) => {
        const ids = record.ability_details ? record.ability_details.map((row) => row.id) : record.abilities ?? [];
        return ids.includes(Number(value));
      },
    },
    {
      key: "move", label: "Knows move", group: "Moves", kind: "text", list: true, advanced: true,
      legacy: (saved) => saved["x-move-filter"],
      placeholder: "Move name…",
      options: (context) => [...new Set((context.moves ?? [])
        .flatMap((row) => [context.text(row.name), ...(context.otherNames?.(row.name) ?? [])]).filter(Boolean))]
        .map((name) => ({ value: name, label: name })),
      prepare: moveSet,
      predicate: (record, value) => (record.learnset ?? []).some((row) => value.has(row.move)),
    },
    {
      key: "moveType", label: "Moves of type", group: "Moves", kind: "select", empty: "Any type", advanced: true,
      legacy: (saved) => saved["x-move-type-filter"],
      options: (context) => typeOptions(context.types ?? [], context.text),
      prepare: movesOfTypeSet,
      predicate: (record, value) => (record.learnset ?? []).some((row) => value.has(row.move)),
    },
    {
      key: "stat", label: "Base stat", group: "Numbers", kind: "stat", operators: OPERATORS, advanced: true,
      options: [
        ["hp", "HP"], ["attack", "Attack"], ["defense", "Defense"], ["sp_attack", "Sp. Attack"],
        ["sp_defense", "Sp. Defense"], ["speed", "Speed"], ["total", "Total"],
      ],
      predicate: statMatches,
    },
    {
      key: "weight", label: "Weight (kg)", group: "Numbers", kind: "number", operators: OPERATORS, advanced: true,
      predicate: (record, value) => numberMatches((record.weight ?? 0) / 10, value),
    },
    {
      key: "height", label: "Height (m)", group: "Numbers", kind: "number", operators: OPERATORS, advanced: true,
      predicate: (record, value) => numberMatches((record.height ?? 0) / 10, value),
    },
    {
      key: "eggGroup", label: "Egg group", group: "Other", kind: "select", empty: "All", advanced: true,
      options: () => Object.entries(EGG_GROUPS).map(([value, label]) => ({ value, label: t(label) })),
      predicate: (record, value) => (record.egg_groups ?? []).includes(Number(value)),
    },
    {
      key: "evolution", label: "Evolution kind", group: "Other", kind: "switch", advanced: true,
      options: [["", "All"], ["item", "Item"], ["trade", "Trade"], ["level", "Level"],
        ["other", "Other"], ["none", "Does not evolve"]],
      predicate: evolutionMatches,
    },
  ],
  trainers: [
    {
      key: "class", label: "Class", group: "Trainer", kind: "select", empty: "All classes", advanced: true,
      legacy: (saved) => saved["x-data-filter"] === "ev_training" ? null : saved["x-data-filter"],
      options: (context) => {
        const label = (id) => context.text(id) || context.source?.(id) || id;
        return [...new Set((context.data?.trainers ?? []).filter((row) => row.party_offset !== null || row.party?.length)
          .map((row) => row.class))].sort((a, b) => label(a).localeCompare(label(b)))
          .map((id) => ({ value: id, label: label(id) }));
      },
      predicate: (record, value) => record.class === value,
    },
    {
      key: "kind", label: "Kind", group: "Trainer", kind: "select", empty: "All kinds",
      scope: "battles", legacy: (saved) => saved["x-trainer-kind"],
      options: (context) => {
        const kinds = new Set((context.data?.trainers ?? []).map((row) => row.kind).filter(Boolean));
        return Object.entries(TRAINER_KINDS).filter(([key]) => kinds.has(key))
          .map(([value, label]) => ({ value, label: t(label) }));
      },
      predicate: (battle, value) => battle.kind === value,
    },
    {
      key: "evTraining", label: "EV training", group: "Trainer", kind: "switch", advanced: true,
      scope: "battles", legacy: (saved) => saved["x-data-filter"] === "ev_training" ? "yes" : null,
      options: [["", "All"], ["yes", "EV training"]],
      predicate: (battle) => !!battle.ev_training,
    },
    {
      key: "difficulty", label: "Difficulty", group: "Trainer", kind: "select", empty: "Any difficulty", advanced: true,
      scope: "battles",
      options: () => [["base", "Levels shown here"],
        ...DIFFICULTY_SETTINGS.map((value) => [value, SETTING_LABELS[value]])]
        .map(([value, label]) => ({ value, label: t(label) })),
      predicate: (battle, value) => value === "base" || !battle.difficulty
        || battle.difficulty.includes(DIFFICULTY_SETTINGS[Number(value)] ?? value),
    },
  ],
  items: [],
  locations: [],
  types: [],
};

/** The per-mode filter keys for memory and route code. */
export const MODE_FILTERS = {};
for (const mode of Object.keys(FILTER_SETS)) {
  Object.defineProperty(MODE_FILTERS, mode, { enumerable: true,
    get: () => FILTER_SETS[mode].map((entry) => entry.key) });
}

/** Preserve criteria while replacing obsolete controls. */
export function migrateFilters(mode, saved) {
  const stored = {};
  if (!saved || typeof saved !== "object" || Array.isArray(saved)) return stored;
  const search = saved.search ?? saved["x-search"];
  if (typeof search === "string") stored.search = search;
  for (const entry of FILTER_SETS[mode] ?? []) {
    let value = saved[entry.key] ?? entry.legacy?.(saved);
    if (entry.normalize && value != null) value = entry.normalize(value);
    if (activeValue(value)) stored[entry.key] = value;
  }
  return stored;
}

/** Context-dependent options need resolution before shared row normalization. */
function optionsOf(entry, context) {
  const options = typeof entry.options === "function" ? entry.options(context) : entry.options;
  return (options ?? []).map((row) => Array.isArray(row) ? { value: row[0], label: filterText(row[1]) } : row);
}

/** Read a control's value in its stored shape. */
function readValue(entry, control) {
  if (entry.kind === "select") return control.select.value;
  if (entry.kind === "text") return control.input.value;
  if (entry.kind === "switch") return control.current;
  if (entry.kind === "chips") return [...control.selected];
  const value = {};
  if (entry.sub && control.sub) value[entry.subKey ?? "kind"] = control.sub;
  const a = control.a.value, b = control.b.value, op = control.op.value;
  if (a !== "" && (op !== "between" || b !== "")) {
    value.op = op;
    value.a = a;
    if (op === "between") value.b = b;
    if (entry.kind === "stat") value.stat = control.stat.value;
  }
  return Object.keys(value).length ? value : null;
}

/** Name each numeric bound without introducing layout text. */
function labelNumbers(entry, control) {
  const label = entry.kind === "stat"
    ? control.options.find((row) => row.value === control.stat.value)?.label ?? filterText(entry.label)
    : filterText(entry.label);
  control.op.setAttribute("aria-label", filterText(entry.label));
  control.a.setAttribute("aria-label", `${label} ${control.op.value === "between" ? "≥" : OPERATOR_NAMES[control.op.value]}`);
  control.b.setAttribute("aria-label", `${label} ≤`);
  control.stat?.setAttribute("aria-label", filterText(entry.label));
}

/** Set a control from its stored value, DOM state untouched. */
function writeValue(entry, control, value) {
  if (entry.kind === "select") {
    control.select.value = (entry.normalize ? entry.normalize(value) : value) ?? "";
    return;
  }
  if (entry.kind === "text") {
    control.input.value = value ?? "";
    return;
  }
  if (entry.kind === "switch") {
    control.current = value ?? "";
    for (const button of control.buttons) {
      const on = (button.getAttribute?.("data-value") ?? button.dataset?.value) === control.current;
      button.classList?.toggle?.("on", on);
      button.setAttribute?.("aria-pressed", String(on));
    }
    return;
  }
  if (entry.kind === "chips") {
    control.selected = new Set(Array.isArray(value) ? value : []);
    for (const button of control.buttons) {
      const on = control.selected.has(button.getAttribute?.("data-value") ?? button.dataset?.value);
      button.classList?.toggle?.("on", on);
      button.setAttribute?.("aria-pressed", String(on));
    }
    return;
  }
  value ??= { op: entry.operators?.[0] ?? ">=", a: "" };
  control.op.value = value.op ?? entry.operators?.[0] ?? ">=";
  control.a.value = value.a ?? "";
  control.b.value = value.op === "between" ? value.b ?? "" : "";
  control.b.hidden = value.op !== "between";
  if (entry.kind === "stat") control.stat.value = value.stat ?? control.options[0]?.value ?? "";
  if (entry.sub) control.sub = value[entry.subKey ?? "kind"] ?? "";
  if (entry.sub) {
    for (const button of control.subButtons ?? []) {
      const on = (button.getAttribute?.("data-value") ?? button.dataset?.value) === control.sub;
      button.classList?.toggle?.("on", on);
      button.setAttribute?.("aria-pressed", String(on));
    }
  }
  labelNumbers(entry, control);
}

/** Describe one active value for its removable chip. */
function chipLabels(entry, value, context) {
  const option = (key) => (optionsOf(entry, context)).find((row) => String(row.value) === String(key));
  const out = [];
  const add = (label, remaining = null) => out.push({ label, remaining });
  if (entry.kind === "select") {
    add(`${filterText(entry.label)}: ${option(value)?.label ?? value}`);
  } else if (entry.kind === "text") {
    add(`${filterText(entry.label)}: ${value}`);
  } else if (entry.kind === "switch") {
    add(`${filterText(entry.label)}: ${option(value)?.label ?? value}`);
  } else if (entry.kind === "chips") {
    for (const key of value) add(option(key)?.label ?? key, value.filter((item) => item !== key));
  } else if (entry.kind === "stat") {
    const stat = option(value.stat)?.label ?? value.stat;
    add(value.op === "between" ? `${stat}: ${value.a}–${value.b}` : `${stat} ${filterText(OPERATOR_NAMES[value.op])} ${value.a}`);
  } else {
    const subKey = entry.subKey ?? "kind";
    if (value.a !== "" && value.a != null) {
      add(value.op === "between" ? `${filterText(entry.label)}: ${value.a}–${value.b}`
        : `${filterText(entry.label)} ${filterText(OPERATOR_NAMES[value.op])} ${value.a}`,
        value[subKey] ? { [subKey]: value[subKey] } : null);
    }
    if (entry.sub && value[subKey]) {
      const sub = optionsOf({ options: entry.sub }, context).find((row) => row.value === value[subKey]);
      const remaining = { ...value };
      delete remaining[subKey];
      add(`${filterText(entry.label)}: ${sub?.label ?? value[subKey]}`,
        remaining.a != null && remaining.a !== "" ? remaining : null);
    }
  }
  return out;
}

/** One rendered filter block built from its registry. */
export class FilterSet {
  constructor(mode, entries, host) {
    this.mode = mode;
    this.entries = entries ?? [];
    this.host = host;
    this.controls = new Map();
    this.open = true;
    this.details = null;
  }

  controlId(key, suffix = "") {
    return `x-filter-${this.mode}-${key}${suffix ? `-${suffix}` : ""}`;
  }

  render(context) {
    this.context = context;
    const basic = this.entries.filter((entry) => !entry.advanced);
    const advanced = this.entries.filter((entry) => entry.advanced);
    const details = el("details", {
      class: "dex-filter-block", id: `x-filter-block-${this.mode}`,
      on: { toggle: () => { this.open = details.open; this.host.onOpenChange?.(details.open); } },
    });
    details.open = this.open;
    const count = el("span", { class: "dex-filter-count", id: `x-filter-count-${this.mode}` }, "");
    const chips = el("span", { class: "dex-filter-chips", id: `x-filter-chips-${this.mode}` });
    const summary = el("summary", { class: "dex-filter-head" },
      el("b", { class: "dex-filter-title" }, filterText("Filters")),
      count,
      chips,
      el("button", {
        type: "button", class: "btn sm dex-filter-reset", id: `x-filter-reset-${this.mode}`,
        on: {
          click: (event) => {
            event.preventDefault?.();
            this.clear();
          },
        },
      }, filterText("Reset")));
    const body = el("div", { class: "dex-filter-body" });
    this.controls = new Map();
    body.append(...this.grouped(basic, context));
    if (advanced.length) {
      const advancedDetails = el("details", { class: "dex-advanced dex-filter-advanced" },
        el("summary", {}, filterText("Advanced")),
        el("div", { class: "dex-filter-advanced-body" },
          ...this.grouped(advanced, context)));
      body.append(advancedDetails);
    }
    details.append(summary, body);
    this.details = details;
    this.countNode = count;
    this.chipsNode = chips;
    this.host.container.replaceChildren(details);
    this.restore({});
  }

  grouped(entries, context) {
    const groups = new Map();
    for (const entry of entries) {
      if (!groups.has(entry.group)) groups.set(entry.group, []);
      groups.get(entry.group).push(entry);
    }
    return [...groups].map(([group, rows]) => this.group(group, rows, context));
  }

  group(name, entries, context) {
    return el("div", { class: "dex-filter-group" },
      el("div", { class: "dex-filter-group-name" }, filterText(name)),
      ...entries.map((entry) => this.row(entry, context)));
  }

  row(entry, context) {
    const control = this.control(entry, context);
    this.controls.set(entry.key, control);
    const grouped = entry.kind !== "select" && entry.kind !== "text";
    const labelId = this.controlId(entry.key, "label");
    if (grouped) {
      control.root.setAttribute("role", "group");
      control.root.setAttribute("aria-labelledby", labelId);
    }
    return el("div", { class: "dex-filter-row" },
      el(grouped ? "span" : "label", { class: "dex-filter-label", id: labelId,
        ...(grouped ? {} : { for: this.controlId(entry.key) }) }, filterText(entry.label)),
      el("div", { class: "dex-filter-control" }, control.root));
  }

  control(entry, context) {
    if (entry.kind === "select") {
      const select = el("select", {
        class: "input dex-filter-select", id: this.controlId(entry.key),
        on: { change: () => this.host.onChange?.() },
      });
      this.fillOptions(select, entry, context, true);
      return { root: select, select };
    }
    if (entry.kind === "text") {
      const input = el("input", {
        type: "text", class: "input dex-filter-text", id: this.controlId(entry.key),
        placeholder: filterText(entry.placeholder ?? ""), on: { input: () => this.host.onChange?.() },
      });
      let list = null;
      if (entry.list) {
        list = el("datalist", { id: `${this.controlId(entry.key)}-list` });
        this.fillOptions(list, entry, context, false);
        input.setAttribute("list", list.id);
      }
      return { root: el("span", { class: "dex-filter-text" }, input, list), input };
    }
    if (entry.kind === "switch") {
      const buttons = [];
      const control = { root: null, buttons, current: "" };
      control.root = el("span", { class: "dex-seg dex-filter-seg", role: "group" },
        ...(optionsOf(entry, context)).map((option) => {
          const button = el("button", {
            type: "button", class: "dex-filter-opt", "data-value": String(option.value), "aria-pressed": "false",
            on: { click: () => { control.current = String(option.value); this.paint(buttons, control.current); this.host.onChange?.(); } },
          }, filterText(option.label));
          buttons.push(button);
          return button;
        }));
      return control;
    }
    if (entry.kind === "chips") {
      const buttons = [];
      const control = { root: null, buttons, selected: new Set() };
      control.root = el("span", { class: "dex-filter-props", role: "group" },
        ...(optionsOf(entry, context)).map((option) => {
          const button = el("button", {
            type: "button", class: "dex-filter-prop", "data-value": String(option.value), "aria-pressed": "false",
            on: {
              click: () => {
                if (control.selected.has(option.value)) control.selected.delete(option.value);
                else control.selected.add(option.value);
                button.classList?.toggle?.("on", control.selected.has(option.value));
                button.setAttribute?.("aria-pressed", String(control.selected.has(option.value)));
                this.host.onChange?.();
              },
            },
          }, option.label);
          buttons.push(button);
          return button;
        }));
      return control;
    }
    const op = el("select", {
      class: "input dex-filter-op", id: this.controlId(entry.key, "op"),
      on: { change: () => this.syncNumber(entry, control) },
    });
    this.fillOptions(op, { options: (entry.operators ?? OPERATORS).map((value) => ({ value, label: filterText(OPERATOR_NAMES[value]) })) }, null, false);
    const a = el("input", {
      type: "number", step: "any", class: "input dex-filter-num", id: this.controlId(entry.key, "a"),
      on: { input: () => this.host.onChange?.() },
    });
    const b = el("input", {
      type: "number", step: "any", class: "input dex-filter-num", id: this.controlId(entry.key, "b"), hidden: true,
      on: { input: () => this.host.onChange?.() },
    });
    const control = { root: null, op, a, b, sub: "", subButtons: [] };
    if (entry.kind === "stat") {
      control.options = optionsOf(entry, context);
      const stat = el("select", {
        class: "input dex-filter-stat", id: this.controlId(entry.key, "stat"),
        on: { change: () => this.syncNumber(entry, control) },
      });
      this.fillOptions(stat, { options: control.options }, null, false);
      control.stat = stat;
      control.root = el("span", { class: "dex-filter-nums" }, stat, op, a, b);
    } else {
      control.root = el("span", { class: "dex-filter-nums" }, op, a, b);
    }
    if (entry.sub) {
      const subButtons = [];
      control.subButtons = subButtons;
      control.root = el("span", { class: "dex-filter-nums dex-filter-with-sub" }, control.root,
        el("span", { class: "dex-seg dex-filter-seg", role: "group" },
          ...entry.sub.map(([value, label]) => {
            const button = el("button", {
              type: "button", class: "dex-filter-opt", "data-value": String(value), "aria-pressed": "false",
              on: { click: () => { control.sub = String(value); this.paint(subButtons, control.sub); this.host.onChange?.(); } },
            }, filterText(label));
            subButtons.push(button);
            return button;
          })));
    }
    return control;
  }

  syncNumber(entry, control) {
    control.b.hidden = control.op.value !== "between";
    if (control.b.hidden) control.b.value = "";
    labelNumbers(entry, control);
    this.host.onChange?.();
  }

  paint(buttons, current) {
    for (const button of buttons) {
      const on = (button.getAttribute?.("data-value") ?? button.dataset?.value) === current;
      button.classList?.toggle?.("on", on);
      button.setAttribute?.("aria-pressed", String(on));
    }
  }

  fillOptions(root, source, context, empty) {
    const rows = [];
    if (empty) rows.push(el("option", { value: "" }, filterText(source.empty ?? "Any")));
    const options = optionsOf(source, context);
    for (const option of options) {
      rows.push(el("option", { value: String(option.value) }, option.label));
    }
    updateChildren(root, ...rows);
  }

  values() {
    const out = {};
    for (const entry of this.entries) {
      const control = this.controls.get(entry.key);
      if (!control) continue;
      const value = readValue(entry, control);
      if (activeValue(value)) out[entry.key] = value;
    }
    return out;
  }

  prepare(values, context) {
    const out = { ...values };
    for (const entry of this.entries) {
      if (!activeValue(out[entry.key])) continue;
      if (entry.prepare) out[entry.key] = entry.prepare(out[entry.key], context) ?? null;
    }
    return out;
  }

  matches(record, values, context) {
    const scoped = new Map();
    for (const entry of this.entries) {
      const value = values[entry.key];
      if (!activeValue(value)) continue;
      if (entry.scope) {
        if (!scoped.has(entry.scope)) scoped.set(entry.scope, []);
        scoped.get(entry.scope).push(entry);
        continue;
      }
      if (!entry.predicate(record, value, context)) return false;
    }
    for (const [scope, entries] of scoped) {
      if (!(record[scope] ?? []).some((child) => entries.every((entry) => entry.predicate(child, values[entry.key], context)))) return false;
    }
    return true;
  }

  restore(saved) {
    for (const entry of this.entries) {
      const control = this.controls.get(entry.key);
      if (!control) continue;
      writeValue(entry, control, saved?.[entry.key]);
    }
  }

  clear() {
    for (const entry of this.entries) {
      const control = this.controls.get(entry.key);
      if (control) writeValue(entry, control, null);
    }
    (this.host.onReset ?? this.host.onChange)?.();
  }

  /** Set one criterion from tests and scripts. */
  set(key, value, options = {}) {
    const entry = this.entries.find((row) => row.key === key);
    const control = this.controls.get(key);
    if (!entry || !control) return;
    writeValue(entry, control, value);
    if (options.silent !== true) this.host.onChange?.();
  }

  refresh(values, shown, total) {
    if (!this.details) return;
    this.details.open = this.open;
    setText(this.countNode, `${formatNumber(shown)} / ${formatNumber(total)}`);
    const labels = [];
    for (const entry of this.entries) {
      const value = values[entry.key];
      if (!activeValue(value)) continue;
      labels.push(...chipLabels(entry, value, this.context).map((chip) => ({ key: entry.key, ...chip })));
    }
    updateChildren(this.chipsNode, ...labels.map(({ key, label, remaining }) => el("button", {
      type: "button", class: "dex-filter-chip", "data-key": key,
      title: t("Remove {label}", { label }), "aria-label": t("Remove {label}", { label }),
      on: { click: (event) => { event?.preventDefault?.(); this.set(key, remaining); } },
    }, label, el("i", { "aria-hidden": "true" }, "×"))));
  }
}
