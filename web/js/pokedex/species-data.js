import { DATA_MODES } from "../pokedexdataview/items.js";
import { t } from "../ui.js";


export const DEX_ROW = 64;

// Defer mode lookup because view modules import each other.
export const modes = () => ["species", ...DATA_MODES, "types"];

/** Categories that record and show recently opened entries. */
export const RECENT_MODES = ["species", "items", "trainers", "locations", "types"];


/** Convert stored decimetres and hectograms into display units. */
export const metric = (v) => (v == null ? "" : String(v / 10).replace(/\.0$/, ""));


/** Locate an auxiliary file beside the dataset. */
export function datasetPath(dataset, name) {
  return `${dataset.slice(0, dataset.lastIndexOf("/") + 1)}${name}`;
}


/** Resolve sprite manifests relative to the dataset. */
export function spritePath(dataset, path) {
  return datasetPath(dataset, `sprites/${path}`);
}


/** Attach separately loaded learnset rows to species records. */
export function inlineLearnsets(data, doc) {
  if (doc?.format !== "gen3tl-pokedex-learnsets" || !Array.isArray(doc.columns) || !doc.species) {
    throw new Error("learnsets.json: unknown format");
  }
  if (doc.profile_block !== data.profile_block) throw new Error("learnsets.json: not the rows of this dataset");
  for (const record of data.species ?? []) {
    const rows = doc.species[String(record.slot)];
    record.learnset = rows == null ? null
      : rows.map((row) => Object.fromEntries([["species", record.slot], ...doc.columns.map((c, i) => [c, row[i]])]));
  }
  delete data.learnsets;
  borrowLearnsets(data.species ?? []);
}


/**
 * Borrow machine, tutor and egg rows from related species.
 * Keep each form's own level-up rows and record sources.
 */
export function borrowLearnsets(records) {
  const bySlot = new Map(records.map((record) => [record.slot, record]));
  for (const record of records) {
    const source = record.learnset_species;
    if (source == null || source === record.slot || !Array.isArray(record.learnset)) continue;
    const rows = bySlot.get(source)?.learnset;
    if (!Array.isArray(rows)) continue;
    const own = record.learnset.filter((row) => row.source == null);
    const has = new Set(own.map((row) => `${row.method}:${row.move}`));
    record.learnset = [...own, ...rows
      .filter((row) => row.method !== 0 && row.source == null && !has.has(`${row.method}:${row.move}`))
      .map((row) => ({ ...row, species: record.slot, source }))];
  }
}


/** Order real entries before placeholders, each by internal ID. */
export function compareSpecies(a, b) {
  return Number(!(a.dex > 0)) - Number(!(b.dex > 0)) || a.slot - b.slot;
}


export const GROWTH_RATES = ["Medium Fast", "Erratic", "Fluctuating", "Medium Slow", "Fast", "Slow"];

export const EGG_GROUPS = {
  1: "Monster", 2: "Water 1", 3: "Bug", 4: "Flying", 5: "Field", 6: "Fairy", 7: "Grass", 8: "Human-Like",
  9: "Water 3", 10: "Mineral", 11: "Amorphous", 12: "Water 2", 13: "Ditto", 14: "Dragon", 15: "Undiscovered",
};


/** Resolve growth identifiers to verified curve names. */
export function growthRate(value) {
  return value == null ? "" : GROWTH_RATES[value] ? t(GROWTH_RATES[value]) : String(value);
}


/** Name breeding groups without repeating matching pairs. */
export function eggGroups(values) {
  return [...new Set(values ?? [])].map((id) => EGG_GROUPS[id] ? t(EGG_GROUPS[id]) : String(id)).join(" / ");
}


/** Describe the game's female threshold as a gender ratio. */
export function genderRatio(value) {
  if (value == null) return "";
  if (value === 255) return t("Genderless");
  const female = Number((value / 254 * 100).toFixed(1));
  const male = Number((100 - female).toFixed(1));
  return t("{male}% male / {female}% female", { male, female });
}


/** Collect catalog identifiers referenced by a species record. */
export function recordIds(record, items = []) {
  const ids = [record.name, record.category, ...(record.entry ?? [])];
  if (record.form_name && !record.form_name.startsWith("page:")) ids.push(record.form_name);
  for (const t of record.types ?? []) ids.push(`types:${t}`);
  for (const a of record.abilities ?? []) ids.push(`abilities:${a}`);
  for (const a of record.ability_details ?? []) ids.push(`abilities:${a.id}`, a.name, a.description);
  for (const e of record.evolution ?? []) {
    if ([6, 7, 24, 25, 34, 36, 39, 254].includes(e.method) && e.parameter > 0 && e.parameter_kind !== "move") {
      ids.push(...itemKeys(e.parameter, items));
    }
    if (e.parameter_kind === "move" && e.parameter > 0) ids.push(`moves:${e.parameter}`);
    if ([35, 36].includes(e.method) && e.extra > 0) ids.push(...itemKeys(e.extra, items));
    if ([26, 37, 38].includes(e.method)) ids.push(`moves:${e.parameter}`);
    if (e.method === 17) ids.push(`types:${e.parameter}`);
    if (e.method === 18) ids.push(`types:${e.extra}`);
    if (e.method === 19) ids.push(`mapsec:${e.parameter}`);
    if (e.method === 27) ids.push(`species:${e.parameter}`);
  }
  return ids.filter((x) => x);
}


/** Resolve an item reference to catalog and generic keys. */
export function itemKeys(id, items = []) {
  const row = items.find((item) => item.id === Number(id));
  const declared = row?.name;
  return declared && declared !== `items:${id}` ? [declared, `items:${id}`] : [`items:${id}`];
}


/** Resolve an item reference through declared catalog keys. */
export function itemText(id, text = () => "", items = []) {
  return itemKeys(id, items).map((key) => text(key)).find((name) => name) ?? "";
}


/** Name an item reference, else the localized generic label. */
export function itemName(id, text = () => "", items = []) {
  return itemText(id, text, items) || t("Item {n}", { n: id });
}
