import test from "node:test";
import assert from "node:assert/strict";
import { ballFactor, catchChance, familyItems, familyOf, maxHp } from "../js/catchcalc.js";

test("max HP takes the base stat, the level and average IVs", () => {
  assert.equal(maxHp(35, 23), 52);   // Onix at 23
  assert.equal(maxHp(45, 5), 20);    // Bulbasaur at 5
  assert.equal(maxHp(1, 50), 1);     // Shedinja
});

test("the chance follows the FireRed odds: a third of the rate at full HP, all of it near 1 HP", () => {
  assert.equal(catchChance({ rate: 45, hp: 52, hpMax: 52 }), 15 / 255);
  assert.equal(catchChance({ rate: 45, hp: 1, hpMax: 52 }), 44 / 255);
  assert.equal(catchChance({ rate: 45, hp: 1, hpMax: 52, mul: 2 }), 88 / 255);
  assert.equal(catchChance({ rate: 45, hp: 1, hpMax: 52, mul: 2, status: "sleep" }), 220 / 255);
  assert.equal(catchChance({ rate: 45, hp: 1, hpMax: 52, mul: 2, status: "burn" }), 132 / 255);
  assert.equal(catchChance({ rate: 255, hp: 1, hpMax: 20 }), 246 / 255); // not certain even at 1 HP
  assert.equal(catchChance({ rate: 45, hp: 52, hpMax: 52, master: true }), 1);
  assert.equal(catchChance({ rate: 45, hp: 52, hpMax: 52, add: 20 }), 21 / 255);   // Heavy Ball on Onix
  assert.equal(catchChance({ rate: 3, hp: 20, hpMax: 20, add: -20 }), 0);
});

test("each ball rule reads the facts of the throw", () => {
  const onix = { level: 23, types: [5, 4], speed: 70, weight: 210, dex: 95, status: "none", condition: false };
  assert.deepEqual(ballFactor(4, onix), { mul: 1, add: 0 });
  assert.deepEqual(ballFactor(2, onix), { mul: 2, add: 0 });
  assert.deepEqual(ballFactor(1, onix), { mul: 1, add: 0, master: true });
  assert.deepEqual(ballFactor(60, onix), { mul: 1, add: 0 });
  assert.deepEqual(ballFactor(60, { ...onix, condition: true }), { mul: 3, add: 0 });
  assert.deepEqual(ballFactor(62, { ...onix, condition: true }), { mul: 5, add: 0 });
  assert.deepEqual(ballFactor(6, onix), { mul: 1, add: 0 });
  assert.deepEqual(ballFactor(6, { ...onix, types: [11, 4] }), { mul: 3.5, add: 0 });
  assert.deepEqual(ballFactor(622, { ...onix, speed: 100 }), { mul: 4, add: 0 });
  assert.deepEqual(ballFactor(625, onix), { mul: 1, add: 20 });
  assert.deepEqual(ballFactor(625, { ...onix, weight: 6.9 }), { mul: 1, add: -20 });
  assert.deepEqual(ballFactor(625, { ...onix, weight: 400 }), { mul: 1, add: 30 });
  assert.deepEqual(ballFactor(8, onix), { mul: 1.8, add: 0 });
  assert.deepEqual(ballFactor(8, { ...onix, level: 30 }), { mul: 1, add: 0 });
  assert.deepEqual(ballFactor(10, { ...onix, turn: 1 }), { mul: 1, add: 0 });
  assert.deepEqual(ballFactor(10, { ...onix, turn: 11 }), { mul: 4, add: 0 });
  assert.deepEqual(ballFactor(623, { ...onix, yourLevel: 32 }), { mul: 2, add: 0 });
  assert.deepEqual(ballFactor(623, { ...onix, yourLevel: 92 }), { mul: 8, add: 0 });
  assert.deepEqual(ballFactor(623, { ...onix, yourLevel: 20 }), { mul: 1, add: 0 });
  assert.deepEqual(ballFactor(628, { ...onix, moonFamily: true }), { mul: 4, add: 0 });
  assert.deepEqual(ballFactor(631, { ...onix, status: "sleep" }), { mul: 4, add: 0 });
  assert.deepEqual(ballFactor(630, onix), { mul: 0.1, add: 0 });
  assert.deepEqual(ballFactor(630, { ...onix, dex: 793 }), { mul: 5, add: 0 });
  assert.deepEqual(ballFactor(0, onix), { mul: 1, add: 0 });
});

test("the family follows evolution edges both ways and lists its evolution items", () => {
  const records = [
    { slot: 29, evolution: [{ method: 4, parameter: 16, target: 30 }] },
    { slot: 30, evolution: [{ method: 7, parameter: 94, target: 31 }] },
    { slot: 31, evolution: [] },
    { slot: 95, evolution: [{ method: 6, parameter: 199, target: 208 }] },
  ];
  assert.deepEqual(familyOf(records[2], records).map((r) => r.slot).sort(), [29, 30, 31]);
  assert.deepEqual(familyOf(records[0], records).map((r) => r.slot).sort(), [29, 30, 31]);
  assert.deepEqual(familyItems(familyOf(records[0], records)), [94]);
  assert.deepEqual(familyItems(familyOf(records[3], records)), []);
});
