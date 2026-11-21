import { t } from "../ui.js";
import { compareSpecies } from "./species-data.js";
import { displayName, speciesNumber, detailOf } from "./species-search.js";


export const STAT_LABELS = {
  hp: "HP", attack: "Attack", defense: "Defense",
  sp_attack: "Sp. Attack", sp_defense: "Sp. Defense", speed: "Speed",
};

export const STAT_ORDER = ["hp", "attack", "defense", "sp_attack", "sp_defense", "speed"];

export const SPECIES_SORT_KEYS = ["dex", "name", "types", "abilities", ...STAT_ORDER, "total"];


/** Validate the species mode's saved ordering. */
export function speciesSort(value) {
  return { key: SPECIES_SORT_KEYS.includes(value?.key) ? value.key : "dex", direction: value?.direction === "desc" ? "desc" : "asc" };
}


/** Resolve exactly the representative row's displayed values. */
export function speciesListFields(record, records, text, index = null) {
  const detail = detailOf(record, text);
  const stats = Object.fromEntries(STAT_ORDER.map((key) => [key, record.base_stats?.[key] ?? null]));
  return {
    dex: speciesNumber(record) > 0 ? speciesNumber(record) : null,
    name: displayName(record, records, text, index),
    types: detail.types.map((type) => type.text).join(" / "),
    abilities: detail.abilities.map((ability) => ability.text).join(" / "),
    ...stats,
    total: STAT_ORDER.every((key) => stats[key] != null) ? STAT_ORDER.reduce((sum, key) => sum + stats[key], 0) : null,
  };
}


/** Sort all filtered representatives with deterministic internal ID ties. */
export function sortSpeciesIndices(indices, records, text, ordering, locale = "en", collator = null, index = null) {
  const { key, direction } = speciesSort(ordering);
  collator ??= new Intl.Collator(locale, { numeric: true, sensitivity: "base" });
  const values = new Map(indices.map((i) => [i, key === "dex"
    ? speciesNumber(records[i]) > 0 ? speciesNumber(records[i]) : null
    : speciesListFields(records[i], records, text, index)[key]]));
  return [...indices].sort((a, b) => {
    const av = values.get(a), bv = values.get(b);
    const missingA = av == null || av === "", missingB = bv == null || bv === "";
    if (missingA !== missingB) return missingA ? 1 : -1;
    const compared = missingA ? 0 : typeof av === "number" ? av - bv : collator.compare(av, bv);
    return compared * (direction === "desc" ? -1 : 1) || compareSpecies(records[a], records[b]);
  });
}


export const STAT_MAX = 255;


export const TIER_LABELS = ["worst", "bad", "average", "good", "best"];

export const VARIANT_LABELS = { day: "day", night: "night", morning: "morning", evening: "Evening" };

export const CATALOG_ID = /^(s[0-9A-F]{7}|[a-z_]+:\d+)(@.+)?$/;


/** Sort species stat values and totals for ranking. */
export function statRanks(species) {
  const ranks = Object.fromEntries([...STAT_ORDER, "total"].map((k) => [k, []]));
  for (const record of species ?? []) {
    const stats = record.base_stats;
    if (!(record.dex > 0) || !stats || STAT_ORDER.some((k) => stats[k] == null)) continue;
    for (const k of STAT_ORDER) ranks[k].push(stats[k]);
    ranks.total.push(STAT_ORDER.reduce((n, k) => n + stats[k], 0));
  }
  for (const k of Object.keys(ranks)) ranks[k].sort((a, b) => a - b);
  return ranks;
}


/** Rank values by share below, counting ties halfway. */
export function tierOf(sorted, value) {
  if (!sorted?.length) return null;
  let below = 0, equal = 0;
  for (const v of sorted) {
    if (v < value) below++;
    else if (v === value) equal++;
  }
  const percentile = (below + equal / 2) / sorted.length;
  const tier = Math.min(4, Math.floor(percentile * 5));
  return { percentile: Math.round(percentile * 100), tier, name: TIER_LABELS[tier] };
}


/** Build stat rows with values, bars, totals and tiers. */
export function statRows(stats, ranks = null) {
  const rows = STAT_ORDER.filter((k) => stats?.[k] != null).map((k) => ({
    key: k, label: t(STAT_LABELS[k]), value: stats[k], percent: Math.round(stats[k] / STAT_MAX * 1000) / 10,
  }));
  if (rows.length) rows.push({ key: "total", label: t("Total"), value: rows.reduce((n, r) => n + r.value, 0), total: true });
  if (ranks) {
    for (const row of rows) {
      const rank = tierOf(ranks[row.key], row.value);
      if (rank) Object.assign(row, { tier: rank.tier, percentile: rank.percentile, tierName: rank.name });
    }
  }
  return rows;
}
