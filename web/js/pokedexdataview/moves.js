import { el } from "../dom.js";
import { effectName, powerLabel } from "../moveeffects.js";
import { flagName, moveListName } from "../movelabels.js";
import { itemLink, typeChip } from "../pokedex/evolutions.js";
import { t } from "../ui.js";
import { CATEGORY } from "./items.js";
import { targetLabel } from "./trainers.js";
import { levels } from "./locations.js";


/** Pair translated move properties with their readers. */
export function moveProperties(record, readers = {}, text = () => "", link = (id) => itemLink(id, text)) {
  const keys = [...record.flag_keys ?? [], ...record.list_keys ?? []];
  if (!keys.length) return t("None");
  return el("div", { class: "dex-move-properties" }, ...keys.map((key) => {
    const source = readers[key]?.readers ?? {};
    const label = t(flagName(key) ?? moveListName(key) ?? key);
    // Omit readers whose names merely repeat the flag.
    const names = (source.abilities ?? []).map((id) => text(`abilities:${id}`) || t("Ability {n}", { n: id }))
      .filter((name) => name !== label);
    names.push(...(source.items ?? []).map((id) => link(id)));
    const details = names.length ? el("span", { class: "dex-move-readers" }, " — ",
      ...names.flatMap((name, index) => (index ? [", ", name] : [name]))) : null;
    return el("div", { class: "dex-move-property", title: key }, el("span", {}, label), details);
  }));
}


/** Share formula presentation across both move cards. */
export function powerFormula(record, { text = () => "", types = [], link = (id) => itemLink(id, text) } = {}) {
  const sentence = el("p", { class: "dex-sub" }, t(record.power_formula));
  if (record.power_items?.length) return el("div", {}, sentence, el("table", { class: "t-forms dex-enc" },
    el("thead", {}, el("tr", {}, el("th", { scope: "col" }, t("Item")),
      el("th", { scope: "col" }, t("Type")), el("th", { scope: "col", class: "num" }, t("Power")))),
    el("tbody", {}, ...record.power_items.map(({ item, type, power }) => el("tr", {},
      el("td", {}, link(item)), el("td", {}, typeChip(type, text(`types:${type}`), types)),
      el("td", { class: "num" }, String(power)))))));
  if (!record.power_tiers?.length) return sentence;
  return el("div", {}, sentence, el("table", { class: "t-forms dex-enc" },
    el("thead", {}, el("tr", {}, el("th", { scope: "col" }, t("Condition")),
      el("th", { scope: "col", class: "num" }, t("Power")))),
    el("tbody", {}, ...record.power_tiers.map(([condition, power]) => el("tr", {},
      el("td", {}, t(condition)), el("td", { class: "num" }, String(power)))))));
}


export function mechanicsText(key, place = "") {
  return t(key, { hp: t("HP"), attack: t("Attack"), defense: t("Defense"), speed: t("Speed"),
    sp_attack: t("Sp. Attack"), sp_defense: t("Sp. Defense"), accuracy: t("Accuracy"), place });
}


// Environment tables explain a column, not the target.
export const targetMechanics = (record) => record.mechanics && !record.mechanics.environments;


/** Describe conditional targets without replacing their engine identifiers. */
export function moveTarget(record) {
  return targetMechanics(record) ? mechanicsText(record.mechanics.summary) : targetLabel(record.target, record.target_key);
}


/** Tabulate one environment column with names from the catalogs. */
export function environmentTable({ column, rows, script_persistence: persistence }, { text, types, move, location }) {
  const section = (id) => location?.(id) ?? (text(`mapsec:${id}`) || t("map section {n}", { n: id }));
  const place = (row) => row.place == null ? "" : text(`mapsec:${row.place}`) || t("map section {n}", { n: row.place });
  const value = (row) => column === "effect" ? mechanicsText(row.value)
    : column === "move" ? move?.(row.value) ?? (text(`moves:${row.value}`) || t("Move {n}", { n: row.value }))
      : typeChip(row.value, text(`types:${row.value}`), types);
  // One link per section; the title lists its maps.
  const sectionMaps = (maps = [], sections = [], single = []) => {
    const groups = new Map(sections.map((id) => [id, []]));
    for (const { map, section: id } of maps) groups.set(id, [...groups.get(id) ?? [], map]);
    return [...groups].map(([id, names]) => [el("span", names.length ? { title: names.join(", ") } : {}, section(id)),
      ...(names.length === 1 && single.includes(id) ? [" ", t("(one map)")] : [])]);
  };
  const joined = (links) => links.flatMap((parts, index) => [index ? ", " : " ", ...parts]);
  const where = (row) => {
    const direct = [...row.maps ?? [], ...row.map_code_maps ?? []];
    const links = sectionMaps([...direct, ...row.scene_maps ?? [], ...row.script_maps ?? []], row.sections,
      direct.map(({ section: id }) => id));
    const also = sectionMaps(row.also_script_maps);
    return el("td", {}, el("p", {}, mechanicsText(row.where, place(row)), ...joined(links)),
      ...(also.length ? [el("p", { class: "dex-sub" }, t("Also set by event scripts in:"), ...joined(also))] : []));
  };
  const table = el("table", { class: "t-forms dex-enc dex-move-environments" },
    el("thead", {}, el("tr", {}, el("th", { scope: "col" }, t("Environment")), el("th", { scope: "col" }, t("Where")),
      el("th", { scope: "col" }, column === "effect" ? t("Added effect") : column === "move" ? t("Move") : t("Type")))),
    el("tbody", {}, ...rows.map((row) => el("tr", {},
      el("td", {}, mechanicsText(row.environment, place(row))), where(row), el("td", {}, value(row))))));
  return persistence ? el("div", {}, table, el("p", { class: "dex-sub" }, mechanicsText(persistence))) : table;
}


/**
 * Explain profile-bound mechanics on every move surface.
 * - text(id), types: catalog text and type colours
 * - move(id), location(section): optional link elements
 */
export function moveMechanics(record, { text = () => "", types = [], move = null, location = null } = {}) {
  const mechanics = record.mechanics;
  if (!mechanics) return [];
  const rows = mechanics.rows.map(({ condition, effect, notes }) => el("tr", {},
    el("td", {}, mechanicsText(condition)), el("td", {}, el("p", {}, mechanicsText(effect)),
      ...(notes?.length ? [el("ul", { class: "dex-sub" },
        ...notes.map((key) => el("li", {}, mechanicsText(key))))] : []))));
  return [el("div", { class: "dex-move-mechanics" },
    el("p", {}, mechanicsText(mechanics.summary)),
    el("table", { class: "t-forms dex-enc" },
      el("thead", {}, el("tr", {}, el("th", { scope: "col" }, t("Condition")), el("th", { scope: "col" }, t("Effect")))),
      el("tbody", {}, ...rows)),
    ...(mechanics.environments ? [environmentTable(mechanics.environments, { text, types, move, location })] : []))];
}


/** Share declared flinch chances across both move surfaces. */
export function flinchFields(record) {
  if (!(record.flinch_chance > 0)) return [];
  const conditions = (record.flinch_conditions || []).map((key) => t(key)).join(" ");
  return [[t("Flinch chance"), `${record.flinch_chance}%${conditions ? ` · ${conditions}` : ""}`]];
}


/** Describe readable move fields while retaining engine identifiers. */
export function moveFields(record, text = () => "", { types = [], link } = {}) {
  const fields = [
    [t("Type"), text(`types:${record.type}`) || t("Type {n}", { n: record.type })],
    [t("Category"), CATEGORY[record.damage_category] ? t(CATEGORY[record.damage_category]) : record.damage_category],
    [t("Power"), record.power_formula
      ? el("div", {}, powerLabel(record), powerFormula(record, { text, types, link })) : powerLabel(record)],
    [t("PP"), record.pp], [t("Accuracy"), record.accuracy],
    [t("Priority"), record.priority], [t("Target"), moveTarget(record)],
    [t("Effect"), effectName(record.effect, record.effect_key)],
  ];
  if (record.flinch_chance > 0) {
    fields.push(...flinchFields(record));
  } else if (record.secondary_chance > 0) {
    fields.push([t("Secondary effect chance"), `${record.secondary_chance}%`]);
  }
  return fields;
}


/** Collect move engine identifiers for the technical record. */
export function moveFacts(record) {
  return [...(targetMechanics(record) ? [[t("Target"), record.target]] : []),
    [t("Effect id"), record.effect], [t("Raw flags"), record.flags], [t("ROM offset"), record.offset]];
}


export const LEARN_ORDER = [0, 1, 3, 2];

export const LEARN_TITLES = { 0: "Level up", 1: "TM and HM", 3: "Tutor", 2: "Egg" };


/** Measure names on canvas.
 * Otherwise estimate 7.4 pixels per letter. */
export function nameMeasure(doc = globalThis.document, size = "13px") {
  try {
    const context = doc?.createElement?.("canvas")?.getContext?.("2d");
    const family = doc?.body ? getComputedStyle(doc.body).fontFamily : "sans-serif";
    if (context) {
      context.font = `${size} ${family}`;
      return (text) => context.measureText(text).width;
    }
  } catch { /* Fallback measurements keep layouts usable without canvas. */ }
  return (text) => [...String(text)].length * 7.4;
}


/** Fit learner names and levels with 3% slack. */
export function learnerColumnWidth(names, measure = nameMeasure(), levels = []) {
  const widest = (texts) => Math.ceil(Math.max(0, ...texts.map((text) => measure(String(text)))) * 1.03);
  return 11 + 24 + 6 + widest(names) + 6 + (levels.length ? 6 + widest(levels) : 0); // Reserve room for divider, sprite, gaps and levels.
}


/** Group learners by method with ascending levels. */
export function learnerGroupsOf(rows) {
  const groups = new Map();
  for (const entry of rows) {
    if (!groups.has(entry.method)) groups.set(entry.method, []);
    groups.get(entry.method).push(entry);
  }
  const order = (method) => (LEARN_ORDER.includes(method) ? LEARN_ORDER.indexOf(method) : LEARN_ORDER.length + method);
  return [...groups.entries()].sort((a, b) => order(a[0]) - order(b[0])).map(([method, entries]) => ({
    method,
    title: LEARN_TITLES[method] ? t(LEARN_TITLES[method]) : t("Method {n}", { n: method }),
    entries: method === 0 ? [...entries].sort((a, b) => (a.level ?? 0) - (b.level ?? 0) || a.slot - b.slot) : entries,
  }));
}
