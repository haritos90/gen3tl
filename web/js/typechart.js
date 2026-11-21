// The type chart: the effectiveness table as a matrix.
// Values: 0 none, 1 immune, 5 half, 10, 20.

import { t } from "./ui.js";

/** The chart's type order, by the engine's ids. */
export const CHART_ORDER = [0, 10, 11, 13, 12, 15, 1, 3, 4, 2, 14, 6, 5, 7, 16, 17, 8, 23];

/** A raw table value as a damage multiplier. */
export function multiplier(raw) {
  if (raw === 1) return 0;
  if (raw === 5) return 0.5;
  if (raw === 20) return 2;
  return 1; // 0 (no entry) and 10
}

/** attack:defense -> multiplier, from the dataset rows. */
export function effectivenessMap(rows) {
  return new Map(rows.map((row) => [`${row.attack}:${row.defense}`, multiplier(row.multiplier)]));
}

/** The types the chart shows, in conventional order. */
export function chartTypes(types) {
  const known = new Set(types.map((row) => row.id));
  return CHART_ORDER.filter((id) => known.has(id));
}

/** The multiplier of an attack against one type. */
export const against = (map, attack, defense) => map.get(`${attack}:${defense}`) ?? 1;

/** The combined multiplier against a species' types. */
export function combined(map, attack, types) {
  return [...new Set(types)].reduce((m, type) => m * against(map, attack, type), 1);
}

/** The defensive profile of a species' types. */
export function defenseGroups(map, types, attackers) {
  const groups = new Map([[4, []], [2, []], [0.5, []], [0.25, []], [0, []]]);
  for (const attack of attackers) {
    const m = combined(map, attack, types);
    if (groups.has(m)) groups.get(m).push(attack);
  }
  return [...groups].filter(([, ids]) => ids.length).map(([factor, ids]) => ({ factor, ids }));
}

/** The offensive profile of one attacking type. */
export function offenseGroups(map, attack, defenders) {
  const groups = new Map([[2, []], [0.5, []], [0, []]]);
  for (const defense of defenders) {
    const m = against(map, attack, defense);
    if (groups.has(m)) groups.get(m).push(defense);
  }
  return [...groups].filter(([, ids]) => ids.length).map(([factor, ids]) => ({ factor, ids }));
}

/** The label of an offence group. */
export function hitLabel(factor) {
  const label = { 2: t("Hits ×2"), 0.5: t("Hits ×½"), 0: t("Hits ×0") }[factor];
  return label ?? `×${factor}`;
}

/** The text of a multiplier cell: nothing for normal damage. */
export function cellLabel(m) {
  return { 0: "0", 0.25: "¼", 0.5: "½", 1: "", 2: "2", 4: "4" }[m] ?? String(m);
}

/** The class of a multiplier cell or label. */
export function cellClass(m) {
  return { 0: "zero", 0.25: "quarter", 0.5: "half", 1: "", 2: "double", 4: "quadruple" }[m] ?? "";
}

/** The label of a defence group, in the interface language. */
export function factorLabel(factor) {
  const label = { 4: t("Weak ×4"), 2: t("Weak ×2"), 0.5: t("Resists ×½"), 0.25: t("Resists ×¼"), 0: t("Immune") }[factor];
  return label ?? `×${factor}`;
}


/** The species records per unordered type pair. */
export function pairCounts(species) {
  const counts = new Map();
  for (const record of species) {
    if (!(record.dex > 0) || !(record.types ?? []).length) continue;
    const [a, b] = [...new Set(record.types)].sort((x, y) => x - y);
    const key = `${a}:${b ?? a}`;
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return counts;
}

/** The count of a pair, in either order. */
export function pairCount(counts, primary, secondary = primary) {
  const [a, b] = [primary, secondary].sort((x, y) => x - y);
  return counts.get(`${a}:${b}`) ?? 0;
}

/** The chart of a first type with combined multipliers. */
export function dualRows(map, primary, ids) {
  const row = (types) => ({ types, cells: ids.map((attack) => ({ attack, m: combined(map, attack, types) })) });
  return [row([primary]), ...ids.filter((id) => id !== primary).map((id) => row([primary, id]))];
}
