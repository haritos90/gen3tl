// The type chart over the extracted effectiveness table.
import assert from "node:assert/strict";
import { test } from "node:test";

import { against, cellClass, cellLabel, CHART_ORDER, chartTypes, combined, defenseGroups, dualRows, effectivenessMap, factorLabel, multiplier, pairCount, pairCounts, offenseGroups, hitLabel } from "../js/typechart.js";

// the raw table values the extractor keeps
const rows = [
  [1, 7, 1], [0, 7, 1], [4, 3, 20], [14, 3, 20], [7, 7, 20], [17, 7, 20],
  [3, 3, 5], [3, 7, 5], [6, 3, 5], [6, 7, 5], [12, 3, 5], [23, 3, 5], [10, 12, 20], [10, 10, 5], [16, 23, 1],
].map(([attack, defense, value]) => ({ attack, defense, multiplier: value }));

test("raw table values read as multipliers: no entry and 10 are normal damage", () => {
  assert.deepEqual([0, 1, 5, 10, 20].map(multiplier), [1, 0, 0.5, 1, 2]);
  const map = effectivenessMap(rows);
  assert.equal(against(map, 1, 7), 0);
  assert.equal(against(map, 10, 12), 2);
  assert.equal(against(map, 10, 10), 0.5);
  assert.equal(against(map, 0, 0), 1); // no pair
});

test("a species' defence combines both types once, in chart order", () => {
  const map = effectivenessMap(rows);
  assert.equal(combined(map, 4, [7, 3]), 2);
  assert.equal(combined(map, 3, [7, 3]), 0.25);
  assert.equal(combined(map, 1, [7, 3]), 0);
  assert.equal(combined(map, 10, [10, 10]), 0.5); // a pure type counts once
  const attackers = chartTypes(CHART_ORDER.map((id) => ({ id })));
  assert.deepEqual(defenseGroups(map, [7, 3], attackers), [
    { factor: 2, ids: [4, 14, 7, 17] }, { factor: 0.5, ids: [12, 23] }, { factor: 0.25, ids: [3, 6] }, { factor: 0, ids: [0, 1] },
  ]);
  assert.deepEqual(defenseGroups(map, [11], attackers), []);
});

test("the chart shows the dataset's types in the conventional order", () => {
  assert.deepEqual(chartTypes([{ id: 3 }, { id: 9 }, { id: 10 }, { id: 0 }]), [0, 10, 3]);
  assert.equal(CHART_ORDER.length, 18);
  assert.deepEqual([0, 0.25, 0.5, 1, 2, 4].map(cellLabel), ["0", "¼", "½", "", "2", "4"]);
  assert.deepEqual([0, 0.25, 0.5, 1, 2, 4].map(cellClass), ["zero", "quarter", "half", "", "double", "quadruple"]);
  assert.equal(factorLabel(2), "Weak ×2");
  assert.equal(factorLabel(0), "Immune");
});

test("the dual chart of a first type lists the pure type, then every pair, with species counts", () => {
  const map = effectivenessMap(rows);
  const dual = dualRows(map, 7, [0, 10, 3, 7]);
  assert.deepEqual(dual.map((row) => row.types), [[7], [7, 0], [7, 10], [7, 3]]);
  assert.deepEqual(dual[3].cells.map((cell) => [cell.attack, cell.m]), [[0, 0], [10, 1], [3, 0.25], [7, 2]]);
  assert.deepEqual(dual[0].cells.map((cell) => cell.m), [0, 1, 0.5, 2]);
  const species = [
    { dex: 1, types: [12, 3] }, { dex: 2, types: [12, 3] }, { dex: 3, types: [3, 12] }, { dex: 4, types: [12, 12] },
    { dex: 0, types: [12, 3] }, { dex: 5, types: [12] }, { dex: 6 },
  ];
  const counts = pairCounts(species);
  assert.equal(pairCount(counts, 3, 12), 3);
  assert.equal(pairCount(counts, 12, 3), 3);
  assert.equal(pairCount(counts, 12), 2);
  assert.equal(pairCount(counts, 12, 12), 2);
  assert.equal(pairCount(counts, 10, 3), 0);
});

test("the offensive profile of a type lists the defending types it hits ×2, ×½ and ×0", () => {
  const map = effectivenessMap([{ attack: 10, defense: 12, multiplier: 20 }, { attack: 10, defense: 11, multiplier: 5 },
    { attack: 7, defense: 0, multiplier: 1 }, { attack: 12, defense: 11, multiplier: 20 }]); // Fire→Grass 2, Fire→Water ½, Ghost→Normal 0, Grass→Water 2
  assert.deepEqual(offenseGroups(map, 10, [0, 10, 11, 12]), [{ factor: 2, ids: [12] }, { factor: 0.5, ids: [11] }]);
  assert.deepEqual(offenseGroups(map, 7, [0, 10]), [{ factor: 0, ids: [0] }]);
  assert.deepEqual(offenseGroups(map, 0, [0, 10]), []);
  assert.equal(hitLabel(2), "Hits ×2");
  assert.equal(hitLabel(0.5), "Hits ×½");
  assert.equal(hitLabel(0), "Hits ×0");
  assert.equal(hitLabel(3), "×3");
});
