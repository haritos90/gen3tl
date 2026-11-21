import { el } from "../dom.js";
import { VARIANT_LABELS } from "../pokedex/species-list.js";
import { siteText, t, uiLanguage } from "../ui.js";
import { PICKUP_KINDS } from "./items.js";

export const LOCATION_LABELS = {
  "Encounter rate: {n}%": () => siteText("Encounter rate: {n}%"),
  "Bite check: {n}%": () => siteText("Bite check: {n}%"),
  "Base value, before rate modifiers; other conditions also affect battles.": () => siteText("Base value, before rate modifiers; other conditions also affect battles."),
  "Base value, before rate modifiers; ability modifiers are ignored.": () => siteText("Base value, before rate modifiers; ability modifiers are ignored."),
  "50% normally; 85% with Suction Cups or Sticky Hold on a non-Egg lead. Other fishing conditions apply.": () => siteText("50% normally; 85% with Suction Cups or Sticky Hold on a non-Egg lead. Other fishing conditions apply."),
  "Points of Interest": () => siteText("Points of Interest"),
  "Exits": () => siteText("Exits"),
  "Item Locations": () => siteText("Item Locations"),
  "Point of interest": () => siteText("Point of interest"),
  "Details": () => siteText("Details"),
  "From": () => siteText("From"),
  "To": () => siteText("To"),
  "Hidden": () => siteText("Hidden"),
  "Ground": () => siteText("Ground"),
  "Zygarde Cell": () => siteText("Zygarde Cell"),
  "Northeast fenced area": () => siteText("Northeast fenced area"),
  "Northwest Route 9 channel": () => siteText("Northwest Route 9 channel"),
  "Island southeast of the Route 6 entrance": () => siteText("Island southeast of the Route 6 entrance"),
  "Peninsula north corner": () => siteText("Peninsula north corner"),
  "Peninsula east corner": () => siteText("Peninsula east corner"),
  "Context map": () => siteText("Context map"),
  "Encounter parameter": () => siteText("Encounter parameter"),
  "north": () => siteText("north"),
  "south": () => siteText("south"),
  "east": () => siteText("east"),
  "west": () => siteText("west"),
};

export const locationText = (key) => (LOCATION_LABELS[key] ?? siteText)(key);

export const pickupLabel = (kind) => kind === "exchange" ? t("Exchange") : kind === "gift" ? t("Gift")
  : kind === "hidden" ? siteText("Hidden") : siteText("Ground");


export const METHOD_LABELS = { land: "Grass", water: "Surfing", rock_smash: "Rock Smash", fishing: "Fishing" };

export const ROD_LABELS = { old: "Old Rod", good: "Good Rod", super: "Super Rod" };

export const COUNT_LABELS = { moves: "{shown} of {total} moves", items: "{shown} of {total} items",
  trainers: "{shown} of {total} trainers", locations: "{shown} of {total} locations" };


/** Describe encounter methods in player-facing terms. */
export function methodLabel(row) {
  if (row.method_labels?.length) return row.method_labels.map((key) => t(key)).join(" / ");
  if (row.method === "fishing" && row.rod) return ROD_LABELS[row.rod] ? t(ROD_LABELS[row.rod]) : t("{rod} rod", { rod: row.rod });
  return METHOD_LABELS[row.method] ? t(METHOD_LABELS[row.method]) : row.method;
}


/** List every effective time without losing raw provenance. */
export function variantLabel(row) {
  return (row.variants ?? [row.variant]).map((variant) => (t(VARIANT_LABELS[variant] ?? variant) ?? "")
    .toLocaleLowerCase(uiLanguage()).replace(/^\p{Ll}/u, (letter) => letter.toLocaleUpperCase(uiLanguage()))).join(" / ");
}


/** Group map areas, encounters and verified location details by section. */
export function sectionGroups(records, details = [], items = [], sections = null, maps = []) {
  const groups = new Map();
  for (const row of records) {
    if (row.map_error === "runtime-map-invalid") continue;
    const id = row.map_section == null ? row.runtime_map : String(row.map_section);
    if (!groups.has(id)) {
      groups.set(id, {
        id, section: row.map_section, name: row.map_section == null ? null : `mapsec:${row.map_section}`, areas: new Map(),
      });
    }
    const areas = groups.get(id).areas;
    if (!areas.has(row.runtime_map)) {
      areas.set(row.runtime_map, {
        id: row.runtime_map, map: row.map, map_header: row.map_header, map_error: row.map_error, encounters: [],
      });
    }
    areas.get(row.runtime_map).encounters.push(row);
  }
  const pictured = new Set(maps.map((row) => row.runtime_map));
  const pickups = items.flatMap((item) => (item.sources ?? []).filter((source) => PICKUP_KINDS.includes(source.kind)
    && (!sections || sections.has(source.map_section))));
  for (const source of [...details, ...pickups]) {
    if (source.map_section == null) continue;
    const id = String(source.map_section);
    if (!groups.has(id)) groups.set(id, { id, section: source.map_section, name: `mapsec:${source.map_section}`, areas: new Map() });
    let row = source;
    if (PICKUP_KINDS.includes(row.kind)) {
      const runtime = row.runtime_map ?? (/^\d+\.\d+$/.test(row.map ?? "") ? row.map : null);
      if (!pictured.has(runtime)) continue;
      row = { ...row, runtime_map: runtime };
    }
    const areas = groups.get(id).areas;
    if (!areas.has(row.runtime_map)) {
      areas.set(row.runtime_map, { id: row.runtime_map, map: row.map, map_header: row.map_header, map_error: null, encounters: [] });
    }
  }
  return [...groups.values()].map((group) => ({ ...group, areas: [...group.areas.values()]
    .sort((a, b) => a.id.localeCompare(b.id, undefined, { numeric: true })) }));
}


/** Location pages: encounters, detailed maps and pickups in named sections. */
export function dataSectionGroups(data) {
  const sections = data.map_sections ? new Set(data.map_sections.map((row) => row.id)) : null;
  return sectionGroups(data.encounters ?? [], data.location_details ?? [], data.items ?? [], sections, data.maps ?? []);
}


/**
 * Group encounters by method, rod and time.
 * Merge species chances and level ranges, sorting by chance.
 */
export function encounterTables(encounters) {
  const tables = new Map();
  for (const row of encounters) {
    const variants = row.variants ?? [row.variant];
    const key = JSON.stringify([row.method, row.rod ?? "", variants, row.method_labels ?? []]);
    if (!tables.has(key)) {
      tables.set(key, { method_id: row.method, method: methodLabel(row), variant: row.variant, variants,
        rate: row.encounter_rate, rows: new Map() });
    }
    const rows = tables.get(key).rows;
    const entry = rows.get(row.species);
    if (entry) {
      entry.chance += row.percentage;
      entry.min = Math.min(entry.min, row.min_level);
      entry.max = Math.max(entry.max, row.max_level);
    } else {
      rows.set(row.species, { species: row.species, chance: row.percentage, min: row.min_level, max: row.max_level });
    }
  }
  return [...tables.values()].map((table) => ({ ...table, rows: [...table.rows.values()]
    .sort((a, b) => b.chance - a.chance || a.species - b.species) }));
}


/** Explain the verified method-specific check without promising a battle. */
export function encounterRate(table, rules) {
  const rule = rules?.evidence && rules[table.method_id];
  if (!rule) return null;
  const threshold = rule.threshold ?? table.rate * rule.multiplier;
  if (!Number.isFinite(threshold) || !Number.isFinite(rule.divisor) || rule.divisor <= 0) return null;
  const n = Math.min(100, Math.max(0, threshold * 100 / rule.divisor));
  return { label: siteText(rule.label, { n }), note: locationText(rule.note) };
}


/** Find species encounters by section, area, method and time. */
export function speciesLocations(encounters, slot, details = []) {
  const out = [];
  for (const group of sectionGroups(encounters, details)) {
    for (const [index, area] of group.areas.entries()) {
      for (const table of encounterTables(area.encounters.filter((row) => row.species === slot))) {
        const row = table.rows[0];
        out.push({ id: group.id, name: group.name, area: area.id, area_number: index + 1, method: table.method, variant: table.variant,
          variants: table.variants, min: row.min, max: row.max, chance: row.chance });
      }
    }
  }
  return out;
}


/** Format a level range for encounter tables. */
export const levels = (row) => t("Lv {n}", { n: row.min === row.max ? row.min : `${row.min}–${row.max}` });


/** Group runtime maps with context and encounter rows. */
export function locationGroups(records) {
  const groups = new Map();
  for (const row of records) {
    if (!groups.has(row.runtime_map)) {
      groups.set(row.runtime_map, {
        id: row.runtime_map, map: row.map, map_section: row.map_section,
        map_header: row.map_header, map_error: row.map_error, encounters: [],
      });
    }
    groups.get(row.runtime_map).encounters.push(row);
  }
  return [...groups.values()].sort((a, b) => a.id.localeCompare(b.id, undefined, { numeric: true }));
}


export function flow(text) {
  return (text ?? "").split("\\p").map((part) => part.split(/\\[nl]/).join(" "));
}


export function fieldList(fields) {
  return el("dl", { class: "kv" }, ...fields.filter(([, value]) => value !== null && value !== undefined)
    // Values may contain elements such as item links.
    .map(([key, value]) => [el("dt", {}, key), el("dd", {}, typeof value === "object" ? value : String(value))]).flat());
}


export function mapPosition(map, position) {
  return Number.isInteger(position.x) && Number.isInteger(position.y)
    && position.x >= 0 && position.y >= 0 && position.x < map.width && position.y < map.height;
}


/** Keep edge crops inside the map. */
export function itemSpotCrop(map, position) {
  const width = Math.min(80, map.width * 16), height = Math.min(80, map.height * 16);
  const x = (position.x + 0.5) * 16, y = (position.y + 0.5) * 16;
  const left = Math.max(0, Math.min(x - width / 2, map.width * 16 - width));
  const top = Math.max(0, Math.min(y - height / 2, map.height * 16 - height));
  return { left, top, width, height, markerX: x - left, markerY: y - top };
}
