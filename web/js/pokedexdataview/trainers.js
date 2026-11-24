import { el } from "../dom.js";
import { basePower, matchesEffect, resolvedEffects } from "../moveeffects.js";
import { flagName, moveListName, targetName } from "../movelabels.js";
import { edgeItems } from "../pokedex/evolutions.js";
import { TRAINER_IV_SCHEMAS } from "../pokedexstore.js";
import { t } from "../ui.js";
import { TARGET_KEYS, LEARN_METHODS } from "./items.js";
import { levels } from "./locations.js";


/**
 * Deduplicate the last four ROM entries at this level.
 * Return null until the species' learnset is loaded.
 */
export function defaultMoves(species, level) {
  if (!Array.isArray(species?.learnset)) return null;
  const learned = species.learnset.filter((row) => row.method === 0 && (row.level ?? 0) <= level);
  const moves = [];
  for (const row of learned.slice(-4)) {
    if (!moves.includes(row.move)) moves.push(row.move);
  }
  return moves;
}


/** Find item-triggered evolutions ordered by source species. */
export function itemEvolutions(item, species) {
  const bySlot = new Map(species.map((record) => [record.slot, record]));
  const out = [];
  for (const source of species) {
    for (const edge of source.evolution ?? []) {
      if (edgeItems(edge).includes(item)) out.push({ source, target: bySlot.get(edge.target) ?? null, edge });
    }
  }
  return out.sort((a, b) => a.source.slot - b.source.slot || (a.target?.slot ?? 0) - (b.target?.slot ?? 0));
}


/** Preserve unresolved facts for inspection. */
export function itemFacts(record) {
  const facts = [];
  if (record.held_effect && !record.held_effect_key) facts.push([t("Held effect id"), record.held_effect], [t("Held effect parameter"), record.hold_effect_param]);
  if (record.battle_usage && !record.battle_usage_key) facts.push([t("Battle usage id"), record.battle_usage]);
  facts.push([t("ROM offset"), record.offset]);
  return facts;
}


/** Index species learning each move in slot order. */
export function learnerIndex(species) {
  const index = new Map();
  for (const record of species) {
    for (const row of record.learnset ?? []) {
      if (!index.has(row.move)) index.set(row.move, []);
      index.get(row.move).push({ slot: record.slot, method: row.method, level: row.level });
    }
  }
  return index;
}


/** Describe learning by level or acquisition method. */
export function learnLabel(entry) {
  if (entry.method === 0) return entry.level > 1 ? t("Lv {n}", { n: entry.level }) : t("Start");
  const label = { 1: t("TM/HM"), 2: t("Egg"), 3: t("Tutor") }[entry.method];
  return label ?? t("Method {n}", { n: entry.method });
}


/** Require every active advanced move filter to match. */
export function moveMatches(record, criteria) {
  if (!criteria) return true;
  if (criteria.type != null && record.type !== criteria.type) return false;
  if (criteria.category != null && record.damage_category !== criteria.category) return false;
  if (criteria.minPower != null && (basePower(record) == null || basePower(record) < criteria.minPower)) return false;
  if (criteria.minAccuracy != null && !(record.accuracy >= criteria.minAccuracy)) return false;
  if (criteria.minPriority != null && !(record.priority >= criteria.minPriority)) return false;
  if (criteria.effect != null && !matchesEffect(record, criteria.effect)) return false;
  if (criteria.flinch && !(record.flinch_chance > 0)) return false;
  if (criteria.secondary && !(record.secondary_chance > 0)) return false;
  if (criteria.learner && !criteria.learner.has(record.id)) return false;
  if (criteria.property && !(record.flag_keys ?? []).includes(criteria.property)
    && !(record.list_keys ?? []).includes(criteria.property)) return false;
  return true;
}


/** List named move flags and engine lists alphabetically. */
export function propertyOptions(moves) {
  const keys = new Set(moves.flatMap((row) => [...row.flag_keys ?? [], ...row.list_keys ?? []]));
  return [...keys].map((key) => ({ key, name: t(flagName(key) ?? moveListName(key) ?? key) }))
    .sort((a, b) => a.name.localeCompare(b.name));
}


/** Select the behavior displayed for each move. */
export function effectOptions(moves) {
  return resolvedEffects(moves).map(({ key, name }) => ({ id: key, name }));
}


/** Describe known move targets; retain unknown numeric values. */
export function targetLabel(target, key = null) {
  const name = targetName(key ?? TARGET_KEYS[target]);
  return name ? t(name) : t("Target {n}", { n: target });
}


/** Group learnsets by level, machine, tutor and egg methods. */
export function learnsetGroups(record, items = []) {
  const machines = new Map(items.filter((item) => item.tm_index != null).map((item) => [item.tm_index, item]));
  const groups = { level_up: [], tmhm: [], tutor: [], egg: [] };
  for (const row of record.learnset ?? []) {
    const group = groups[LEARN_METHODS[row.method]];
    if (!group) continue;
    const entry = { move: row.move, level: row.level, index: row.index };
    if (row.method === 1) entry.item = machines.get(row.index)?.id ?? null;
    group.push(entry);
  }
  groups.level_up.sort((a, b) => a.level - b.level);
  return groups;
}


/** Label the difficulty settings selecting a battle. */
export function difficultyLabel(battle, difficulty) {
  return (difficulty?.order ?? []).filter((value) => battle.difficulty?.includes(difficulty.settings[value]))
    .map((value) => t(difficulty.labels[value])).join(" · ");
}


/**
 * Select proven difficulty rosters and unassigned records.
 * Keep encounter records together after filtering.
 */
export function battlesFor(battles, mode, difficulty) {
  const setting = mode == null ? null : difficulty?.settings[Number(mode)];
  if (!setting) return [];
  return battles
    .filter((battle) => !battle.difficulty || battle.difficulty.includes(setting))
    .sort((a, b) => (a.base ?? a.id) - (b.base ?? b.id) || a.id - b.id);
}


/**
 * Group selected battle variants under their base encounter.
 * Lower party levels approximate earlier encounters.
 */
export function encountersOf(battles, difficulty) {
  const settings = (difficulty?.order ?? []).map((value) => difficulty.settings[value]);
  const rank = (battle) => {
    const index = settings.findIndex((setting) => battle.difficulty?.includes(setting));
    return index < 0 ? settings.length : index;
  };
  const meanLevel = (group) => {
    const levels = group.battles.flatMap((battle) => (battle.party ?? []).map((mon) => mon.level))
      .filter((level) => level != null);
    return levels.reduce((sum, level) => sum + level, 0) / (levels.length || 1);
  };
  const groups = new Map();
  for (const battle of battles) {
    const base = battle.base ?? battle.id;
    if (!groups.has(base)) groups.set(base, { base, battles: [] });
    groups.get(base).battles.push(battle);
  }
  return [...groups.values()].sort((a, b) => meanLevel(a) - meanLevel(b) || a.base - b.base)
    .map((group) => ({ ...group, battles: [...group.battles].sort((a, b) => rank(a) - rank(b) || a.id - b.id) }));
}


/** Label the settings of an encounter variant, in menu order. */
export function settingsLabel(settings, difficulty) {
  return (difficulty?.order ?? []).filter((value) => settings?.includes(difficulty.settings[value]))
    .map((value) => t(difficulty.labels[value])).join(" · ");
}


/**
 * Card encounters retain generated order, each with the variants
 * of the selected setting, and the records no battle starts.
 * Without generated encounters every record is its own battle.
 */
export function cardEncounters(group, data, mode) {
  const difficulty = data?.trainer_difficulty;
  if (!Array.isArray(data?.trainer_encounters)) {
    return { shown: encountersOf(battlesFor(group.battles, mode, difficulty), difficulty).map((encounter) => ({
      variants: encounter.battles.map((battle) => ({ battle, settings: battle.difficulty })),
    })), unused: [] };
  }
  const setting = mode == null ? null : difficulty?.settings[Number(mode)];
  const records = new Map(group.battles.map((battle) => [battle.id, battle]));
  const allRecords = new Map((data.trainers ?? group.battles).map((battle) => [battle.id, battle]));
  const ids = [...new Set(group.battles.flatMap((battle) => battle.encounters ?? []))].sort((a, b) => a - b);
  const shown = ids.map((id) => data.trainer_encounters[id]).filter(Boolean).map((encounter) => {
    const eligible = setting == null ? [] : encounter.variants.filter((variant) =>
      !variant.settings || variant.settings.includes(setting));
    return { encounter, variants: eligible.filter((variant) => records.has(variant.record)).map((variant) => ({
      ...variant, battle: records.get(variant.record),
      participants: eligible.filter((other) => other.role !== variant.role && allRecords.has(other.record)
        && (variant.starter == null || other.starter == null || variant.starter === other.starter))
        .map((other) => ({ ...other, battle: allRecords.get(other.record) })),
    })) };
  }).filter((row) => row.variants.length);
  return { shown, unused: group.battles.filter((battle) => !battle.encounters?.length) };
}


/** Variant requirements: starter, party level, progress and role. */
export function variantConditions(variant, difficulty, speciesName = (slot) => `species:${slot}`) {
  const parts = [];
  if (variant.starter != null) {
    const slot = difficulty?.starters?.[variant.starter - 1];
    parts.push(t("If you chose {species}", { species: slot == null ? String(variant.starter) : speciesName(slot) }));
  }
  if (variant.level_above != null) parts.push(t("Highest party level above {n}", { n: variant.level_above }));
  if (variant.condition) parts.push(t(variant.condition));
  if (variant.role === "partner") parts.push(t("Battle partner"));
  if (variant.role === "second_opponent") parts.push(t("Second opponent"));
  return parts;
}


/** Describe difficulty effects proven by party-building behavior. */
export function difficultyFields(mode, difficulty) {
  if (mode == null || !difficulty?.settings[Number(mode)]) return null;
  return { label: t(difficulty.labels[Number(mode)]) };
}


export const EV_TRAINING = "ev_training";

export const TRAINER_KINDS = {
  league: "Gym leaders and league", story: "Story characters", world: "World trainers",
  special: "Special buildings", partners: "Battle partners", paired: "Trainer pairs",
};

export const EV_LABELS = {
  hp: "HP", attack: "Atk", defense: "Def", sp_attack: "Sp. Atk", sp_defense: "Sp. Def", speed: "Spd",
};


/** Attach validated trainer IV contexts to the dataset. */
export function inlineTrainerIvs(data, doc) {
  if (doc?.format !== "gen3tl-pokedex-trainer-ivs" || !TRAINER_IV_SCHEMAS.includes(doc?.schema) || !Array.isArray(doc.members)) {
    throw new Error("trainer-ivs.json: unknown format");
  }
  if (doc.profile_block !== data.profile_block) throw new Error("trainer-ivs.json: not the contexts of this dataset");
  data.trainer_ivs = doc;
  data.trainerIvMembers = new Map(doc.members.map((member) => [`${member.trainer}/${member.slot}`, member]));
}


/** Find one generated member without guessing missing data. */
export function trainerIvMember(data, trainer, slot) {
  if (!(data.trainerIvMembers instanceof Map) && typeof data.trainer_ivs === "object" && data.trainer_ivs !== null) {
    inlineTrainerIvs(data, data.trainer_ivs);
  }
  return data.trainerIvMembers?.get(`${trainer}/${slot}`) ?? null;
}


/** EV alternatives for one difficulty: [{evs, when}].
 * Null evs denotes a removed member. */
export function trainerEvRows(member, mode) {
  return evAlternatives(mode == null ? undefined : member?.evs?.[String(Number(mode))]);
}


/** One generated EV value as rows of alternatives. */
export function evAlternatives(value) {
  if (value === undefined) return null;
  if (value === null || Array.isArray(value)) return [{ evs: value, when: null }];
  return Array.isArray(value?.alternatives) ? value.alternatives : null;
}


/** Species ability links, or the reason none exist. */
export function abilityFact(view, species, ids) {
  const label = ids?.length > 1 ? t("Possible abilities") : t("Ability");
  const values = ids?.length ? ids.flatMap((id, index) => [
    ...(index ? [" / "] : []), view.abilityLink(species, id),
  ]) : [ids ? t("Not in party on this difficulty") : t("Abilities unavailable")];
  return el("span", { class: "dex-mon-fact dex-mon-ability" },
    el("span", { class: "muted" }, `${label}: `), ...values);
}


/** The built levels of one band of a built species. */
export function bandLevel(band, change) {
  const last = band.last ?? change.last;
  if (last == null) return t("Lv {n}", { n: `${band.level}+` });
  return t("Lv {n}", { n: band.level === last ? String(last) : `${band.level}–${last}` });
}


/** Keep writes within their species' level ranges. */
export function trainerWriteRows(source, mode, rules = []) {
  const changes = (source?.species_changes ?? []).filter((change) =>
    rules.some((rule) => rule.id === change.rule && rule.status === "known" && rule.kind === "scaled_evolution") &&
    change.badges == null && change.battle == null && change.from == null);
  return (source?.write_changes ?? []).flatMap((row) => {
    const groups = new Map();
    for (const day of row.difficulties) {
      let ranges = [[Math.max(row.level, source.level ?? 1), Math.min(row.last ?? 100, source.last ?? 100)]];
      for (const change of changes.filter((change) => change.difficulties.includes(day))) {
        ranges = ranges.flatMap(([first, last]) => {
          if (change.level > last || (change.last ?? 100) < first) return [[first, last]];
          return [[first, change.level - 1], [(change.last ?? 100) + 1, last]];
        }).filter(([first, last]) => first <= last);
      }
      for (const [first, last] of ranges.filter(([first, last]) => first <= last)) {
        const key = `${first}/${last}`;
        if (!groups.has(key)) groups.set(key, { ...row, level: first,
          ...(last < 100 || source.last != null ? { last } : {}), difficulties: [] });
        groups.get(key).difficulties.push(day);
      }
    }
    return [...groups.values()].filter((row) => mode == null || row.difficulties.includes(Number(mode)));
  });
}


/** The levels at which a source's own writes can show. */
export function writeRange(source, mode, rules = []) {
  const rows = trainerWriteRows({ ...source, write_changes: [{ level: 1, difficulties: [Number(mode)], when: {} }] },
    mode, rules);
  return rows.length === 1 ? rows[0] : { level: 0, last: 101 };
}


/** Join equal-valued rows whose conditions differ in one flag. */
export function mergeWhen(rows, field) {
  const result = [...rows];
  for (let joined = true; joined;) {
    joined = false;
    for (let a = 0; a < result.length && !joined; a++) {
      for (let b = a + 1; b < result.length && !joined; b++) {
        const [x, y] = [result[a], result[b]];
        if (JSON.stringify(x[field]) !== JSON.stringify(y[field])) continue;
        const keys = Object.keys(x.when);
        const differ = keys.filter((key) => JSON.stringify(x.when[key]) !== JSON.stringify(y.when[key]));
        if (differ.length !== 1 || keys.length !== Object.keys(y.when).length) continue;
        const { [differ[0]]: _, ...when } = x.when;
        result.splice(b, 1);
        result[a] = { ...x, when };
        joined = true;
      }
    }
  }
  return result;
}


/** Show each value once, under its required conditions. */
export function factorWrites(rows, baseline, range = { level: 1, last: 100 }) {
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const bands = new Map();
  for (const row of rows) {
    const key = `${row.level}/${row.last ?? ""}/${row.difficulties.join(",")}`;
    bands.set(key, [...bands.get(key) ?? [], row]);
  }
  const head = {};
  const out = [];
  for (const group of bands.values()) {
    const flags = [...new Set(group.flatMap((row) => Object.keys(row.when)))].sort();
    const states = flags.reduce((all, flag) => all.flatMap((state) => [0, 1].map((value) => ({ ...state, [flag]: value }))), [{}]);
    const match = (state) => group.find((row) => Object.entries(row.when).every(([key, values]) => values.includes(state[key])));
    // A state without a row keeps the member's own values.
    const value = (state, field) => match(state)?.[field] ?? baseline[field];
    const depends = (field) => flags.filter((flag) => states.some((state) =>
      !same(value(state, field), value({ ...state, [flag]: 1 - state[flag] }, field))));
    const items = depends("item");
    const moves = depends("moves");
    const first = group[0];
    // Flagless writes collapse only when covering every level.
    const whole = flags.length ? first.level <= range.level && (first.last ?? 100) >= (range.last ?? 100)
      : first.level === 1 && (first.last ?? 100) === 100;
    // Only flag conditions can be split by value.
    if (flags.some((flag) => !/^0x[\da-f]+$/i.test(flag)) || same(items, moves) && (items.length || !whole)) {
      out.push(...group);
      continue;
    }
    for (const [field, keys] of [["item", items], ["moves", moves]]) {
      const seen = new Set();
      const found = [];
      for (const state of states) {
        const when = Object.fromEntries(keys.map((key) => [key, [state[key]]]));
        const id = JSON.stringify(when);
        if (seen.has(id)) continue;
        seen.add(id);
        const result = value(state, field);
        if (same(result, baseline[field])) continue;
        if (!keys.length && whole) head[field] = result;
        else found.push({ level: first.level, ...(first.last != null ? { last: first.last } : {}),
          difficulties: first.difficulties, when, [field]: result });
      }
      out.push(...mergeWhen(found, field));
    }
  }
  return { head, rows: out };
}


/** Keep differing IVs visible when collapsed. */
export function trainerIvSummary(vector) {
  if (!Array.isArray(vector) || vector.length !== 6 || vector.some((value) => !Number.isInteger(value) || value < 0 || value > 31)) return null;
  if (vector.every((value) => value === vector[0])) return String(vector[0]);
  return `${Math.min(...vector)}–${Math.max(...vector)}`;
}

export const INVERSE_BATTLE_MAPS = new Set(["14.11", "14.12"]);

export const INVERSE_BATTLE_SIGN = "map:3.84/sign:2/0";


/** Format a species' EV yield as "+2 Spd". */
export function evYield(ev) {
  return Object.entries(EV_LABELS).filter(([key]) => ev?.[key] > 0)
    .map(([key, label]) => t("+{n} {stat}", { n: ev[key], stat: t(label) })).join(" ");
}


/** Check whether a trainer group battles in an EV-training room. */
export const evTraining = (group) => group.battles.some((battle) => battle.ev_training);


export const nameText = (value) => typeof value === "string" ? value.trim() : "";


/** Keep unnamed trainers identifiable without inventing names. */
export function trainerName(record, text, source = () => "") {
  return nameText(text(record.name)) || nameText(source(record.name)) || t("Trainer {n}", { n: record.id });
}


/** Group battles sharing a trainer name and class. */
export function trainerGroups(trainers, source = (id) => id) {
  const groups = new Map();
  for (const trainer of trainers) {
    // Exclude the empty trainer without a party pointer.
    if (trainer.party_offset === null && !trainer.party?.length) continue;
    const key = `${nameText(source(trainer.name)) || `#${trainer.id}`}|${trainer.class}`;
    // First records anchor progression across rematches and difficulties.
    if (!groups.has(key)) groups.set(key, { id: trainer.id, name: trainer.name, class: trainer.class,
      average_level: trainer.average_level, battles: [] });
    groups.get(key).battles.push(trainer);
  }
  return [...groups.values()];
}


/** Format the lowest and highest stored party levels. */
export function levelRange(party) {
  const levels = party.map((mon) => mon.level).filter((level) => level != null);
  if (!levels.length) return "";
  const min = Math.min(...levels), max = Math.max(...levels);
  return min === max ? String(min) : `${min}–${max}`;
}
