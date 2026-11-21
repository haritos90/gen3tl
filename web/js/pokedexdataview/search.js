import { el } from "../dom.js";
import { createSpeciesSearch, matchRank, numberedName, publicRecords, speciesFamilyIndex } from "../pokedex/species-search.js";
import { t } from "../ui.js";
import { CATEGORY } from "./items.js";
import { trainerName, trainerGroups } from "./trainers.js";
import { dataSectionGroups } from "./locations.js";


export const KIND_LABELS = { species: "Species", moves: "Moves", items: "Items", trainers: "Trainers", locations: "Locations", types: "Types" };

export const CROSS_LIMIT = 30;


/** Cache normalized fields for unified searches across every category. */
export function createCrossSearch(data, text, source, species = [], aliases = () => [], other = source) {
  const familyIndex = speciesFamilyIndex(species);
  const speciesSearch = createSpeciesSearch(species, text, other, aliases, false, familyIndex, data.items ?? [], data.species_browsing);
  const groups = {
    trainers: trainerGroups(data.trainers ?? [], source),
    locations: dataSectionGroups(data),
  };
  const searches = Object.fromEntries([
    ["moves", data.moves ?? []], ["items", data.items ?? []], ["trainers", groups.trainers], ["locations", groups.locations],
    ["types", (data.types ?? []).map((row) => ({ ...row, name: `types:${row.id}` }))],
  ].map(([kind, rows]) => [kind, createEntrySearch(publicRecords(data, kind, rows), text, kind, other, source)]));
  const matches = (query, active, limit = CROSS_LIMIT) => {
    const q = (query ?? "").trim();
    if (!q) return [];
    const out = [];
    if (active !== "species") {
      const ranked = speciesSearch.filter(q).map((i, order) => ({ i, order, rank: speciesSearch.rank(species[i], q) }))
        .sort((a, b) => a.rank - b.rank || a.order - b.order);
      for (const { i, rank } of ranked.slice(0, limit)) {
        out.push({ kind: "species", id: species[i].slot, name: numberedName(species[i], species, text, familyIndex), rank });
      }
    }
    const label = (record, kind) => kind === "moves" ? text(record.name) || `moves:${record.id}`
      : kind === "items" ? text(record.name) || `items:${record.id}`
        : kind === "trainers" ? `${trainerName(record, text, source)}${text(record.class) ? ` · ${text(record.class)}` : ""}`
          : kind === "locations" ? text(record.name) || t("Area {n}", { n: record.id })
            : text(record.name) || t("Type {n}", { n: record.id });
    for (const kind of ["moves", "items", "trainers", "locations", "types"]) {
      if (kind === active) continue;
      const search = searches[kind];
      for (const record of search.ranked(q).slice(0, limit)) out.push({ kind, id: record.id, name: label(record, kind), rank: search.rank(record, q) });
    }
    return out;
  };
  return { matches };
}


/**
 * Search other categories with a per-category result limit.
 * Order species, moves, items, trainers, locations, then types.
 */
export function crossMatches(query, data, text, source, active, species = [], limit = CROSS_LIMIT, aliases = () => [], other = source) {
  return createCrossSearch(data, text, source, species, aliases, other).matches(query, active, limit);
}


/** Place name matches before weak rows across categories. */
export function listOrder(strong, total, cross) {
  const strongCross = [];
  const weakCross = [];
  cross.forEach((hit, j) => ((hit.rank ?? 2) <= 1 ? strongCross : weakCross).push({ cross: j }));
  const out = [];
  for (let i = 0; i < Math.min(strong, total); i++) out.push({ own: i });
  out.push(...strongCross);
  for (let i = Math.min(strong, total); i < total; i++) out.push({ own: i });
  out.push(...weakCross);
  return out;
}


/** Render cross-category matches linking to their own views. */
export function crossRow(hit, top, first, open) {
  return el("div", {
    class: `row cross${first ? " first" : ""}`, role: "option", style: `top:${top}px`,
    "data-key": `${hit.kind}:${hit.id}`,
    on: { click: () => open({ kind: hit.kind, id: String(hit.id) }) },
  }, el("span", { class: "dex-kind" }, t(KIND_LABELS[hit.kind] ?? hit.kind)),
  el("span", { class: "dex-name-list" }, hit.name));
}


/** Refresh existing matches without changing their order. */
export function relabelMatches(hits, view) {
  const data = view.host.data() ?? {};
  for (const hit of hits ?? []) {
    const records = hit.kind === "species" ? view.records
      : hit.kind === "trainers" ? trainerGroups(data.trainers ?? [], (id) => view.dataView.source(id))
        : hit.kind === "locations" ? dataSectionGroups(data) : data[hit.kind] ?? [];
    const record = records.find((row) => String(row.slot ?? row.id) === String(hit.id));
    if (!record) continue;
    hit.name = hit.kind === "species" ? view.speciesName(record)
      : hit.kind === "trainers" ? trainerName(record, (id) => view.text(id), (id) => view.dataView.source(id))
        : view.text(record.name) || (hit.kind === "locations" ? t("Area {n}", { n: record.id }) : record.name);
    if (hit.kind === "trainers" && view.text(record.class)) hit.name += ` · ${view.text(record.class)}`;
  }
}


/** Rank name matches, preserving list order within ranks. */
export function rankedBy(rows, nameOf, query) {
  return rows.map((row, i) => [row, matchRank(nameOf(row), query), i])
    .sort((a, b) => a[1] - b[1] || a[2] - b[2]).map(([row]) => row);
}


/** Cache normalized matching and ranking fields for one data category. */
export function createEntrySearch(records, text, kind, source = () => "", rankSource = source) {
  const fields = new Map();
  const fieldOf = (record) => {
    if (fields.has(record)) return fields.get(record);
    const hay = kind === "locations"
      ? [record.id, text(record.name), source(record.name), ...record.areas.flatMap((area) => [area.id, area.map, area.map_error,
        ...area.encounters.flatMap((row) => [`species:${row.species}`, text(`species:${row.species}`)])])]
      : kind === "trainers"
        ? [record.name, trainerName(record, text), source(record.name), ...record.battles.map((battle) => String(battle.id))]
        : kind === "items"
          ? [record.id, record.name, text(record.name), source(record.name)]
          : [record.id, record.name, record.type, record.damage_category, text(record.name), source(record.name)];
    const names = [kind === "trainers" ? trainerName(record, text, rankSource) : text(record.name), source(record.name)]
      .flat().map((name) => String(name ?? "").toLowerCase());
    const entry = { hay: hay.flat().filter((x) => x !== null && x !== undefined).join(" ").toLowerCase(), names };
    fields.set(record, entry);
    return entry;
  };
  const rank = (record, query) => {
    const q = (query ?? "").trim().toLowerCase();
    if (!q) return 0;
    if (q === String(record.id)) return -1;
    const names = fieldOf(record).names;
    return names.some((name) => name.startsWith(q)) ? 0 : names.some((name) => name.includes(q)) ? 1 : 2;
  };
  const filter = (query, candidates = records) => {
    const q = (query ?? "").trim().toLowerCase();
    if (!q) return candidates;
    return candidates.filter((record) => {
      if (kind === "moves" && ["physical", "special", "status"].includes(q)) {
        return CATEGORY[record.damage_category]?.toLowerCase() === q;
      }
      return fieldOf(record).hay.includes(q);
    });
  };
  const ranked = (query, candidates = records) => filter(query, candidates)
    .map((record, i) => ({ record, i, rank: rank(record, query) }))
    .sort((a, b) => a.rank - b.rank || a.i - b.i).map(({ record }) => record);
  return { filter, rank, ranked };
}


/**
 * Search records and resolved catalog text.
 * Trainer classes filter results independently of name searches.
 */
export function filterEntries(records, query, text, kind, source = () => "") {
  return createEntrySearch(records, text, kind, source).filter(query);
}
