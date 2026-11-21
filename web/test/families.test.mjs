// The Families view's helpers.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  englishWords, familiesOf, filterFamilies, hasHyphen, isShortened, marksOf, marksText, memberRows, moveCells,
  moveFacts, sharedWord, FamiliesView,
} from "../js/families.js";
import { setCatalog } from "../js/ui.js";

const term = (id, category, english, translation, label = "", extra = {}) =>
  ({ id, category, source_text: english, translation, label, in_source: [], in_translation: [], ...extra });
const move = (id, english, translation, n, label = "") =>
  term(id, "move", english, translation, label, { in_source: [`s${n}`, `moves:${n}`] });

const TERMS = [
  move("move:fire-punch", "Fire Punch", "Кулак огня", 7),
  move("move:thunderpunch", "ThunderPunch", "Кулак грома", 9),
  move("move:mega-punch", "Mega Punch", "Мегаудар", 5),
  move("move:mach-punch", "Mach Punch", "Мах-кулак", 183),
  move("move:focus-punch", "Focus Punch", "Фокус-кулак", 264),
  move("move:thunder", "Thunder", "Гром", 87),
  move("move:thunder-wave", "Thunder Wave", "Электроволны", 86),
  move("move:low-kick", "Low Kick", "Низкий пинок", 67),
  term("item:fire-gem", "item", "Fire Gem", "Огнелит"),
  term("item:water-gem", "item", "Water Gem", "Водолит"),
  term("item:ice-gem", "item", "Ice Gem", "Лёдолит", "Ледит"),
  term("item:up-grade", "item", "Up-Grade", "Апгрейд"),
  term("item:x-up", "item", "X Up", "Икс"),
  term("item:z-up", "item", "Z Up", "Зет"),
];
const M = (id, power, type, category, lists = []) =>
  ({ id, name: `moves:${id}`, power, accuracy: 100, pp: 15, type, damage_category: category, list_keys: lists });
const DATASET = { moves: [
  M(7, 75, 10, 0, ["punching"]), M(9, 75, 13, 0, ["punching"]), M(5, 80, 0, 0, ["punching"]),
  M(183, 40, 1, 0, ["punching"]), M(264, 150, 1, 0, ["punching"]), M(87, 110, 13, 1), M(86, 0, 13, 2),
  { ...M(67, 1, 1, 0), power_kind: "computed" },
] };
const byId = new Map(TERMS.map((t) => [t.id, t]));
const get = (id) => byId.get(id);
const facts = moveFacts(TERMS, DATASET);
const TYPES = { 0: "Normal", 1: "Fight", 10: "Fire", 13: "Electr" };
const families = familiesOf(TERMS, facts, (n) => TYPES[n]);
const fam = (id) => families.find((f) => f.id === id);

test("English words: spaces, hyphens, inner capitals", () => {
  assert.deepEqual(englishWords("ThunderPunch"), ["thunder", "punch"]);
  assert.deepEqual(englishWords("Up-Grade"), ["up", "grade"]);
  assert.deepEqual(englishWords("King's Rock"), ["king", "rock"]);
  assert.deepEqual(englishWords("Z-Move 25"), ["move"]); // a letter, digits only: no word
  assert.deepEqual(englishWords(""), []);
});

test("move facts: the moves:<n> entries a move term's English names", () => {
  assert.equal(facts.get("move:fire-punch")[0].power, 75);
  const keyed = moveFacts([{ ...TERMS[0], in_source: ["hash:0a1b2c3d", "table:moves/7"] }], DATASET);
  assert.equal(keyed.get("move:fire-punch")[0].power, 75);
  assert.equal(facts.has("item:fire-gem"), false);
  assert.equal(moveFacts(TERMS, null).size, 0);
});

test("families: word families of three, per category, generic words out", () => {
  assert.deepEqual(fam("word:move:punch").members,
    ["move:fire-punch", "move:thunderpunch", "move:mega-punch", "move:mach-punch", "move:focus-punch"]);
  assert.deepEqual(fam("word:item:gem").members, ["item:fire-gem", "item:water-gem", "item:ice-gem"]);
  assert.equal(fam("word:move:thunder").members.length, 3); // ThunderPunch, Thunder, Thunder Wave
  assert.equal(fam("word:item:up"), undefined); // generic
  assert.equal(fam("word:move:fire"), undefined); // two members
  assert.equal(fam("word:item:fire"), undefined); // other category
});

test("families: move types and game move lists, ordered by kind", () => {
  assert.deepEqual(fam("type:13").members, ["move:thunderpunch", "move:thunder", "move:thunder-wave"]);
  assert.equal(fam("type:13").title, "Electr moves");
  assert.equal(fam("type:10"), undefined); // one member
  assert.equal(fam("list:punching").title, "Punching moves");
  assert.equal(fam("list:punching").members.length, 5);
  assert.deepEqual([...new Set(families.map((f) => f.kind))], ["type", "list", "word"]);
  assert.deepEqual(familiesOf(TERMS).filter((f) => f.kind !== "word"), []); // no dataset
});

test("members: moves by power, variable power, then status", () => {
  const rows = memberRows(fam("type:1"), get, facts);
  assert.deepEqual(rows.map((r) => r.id), ["move:mach-punch", "move:focus-punch", "move:low-kick"]);
  const electric = memberRows(fam("type:13"), get, facts);
  assert.deepEqual(electric.map((r) => r.move.power), [75, 110, 0]);
  assert.deepEqual(moveCells(electric[2].move), ["—", "100%", "15", "status"]);
  assert.deepEqual(moveCells(rows[2].move), ["varies", "100%", "15", "physical"]);
  assert.equal(moveCells({ power: 1 })[0], "varies");
  assert.equal(moveCells({ power: 1, power_kind: "fixed" })[0], "1");
  const gems = memberRows(fam("word:item:gem"), get, facts);
  assert.deepEqual(gems.map((r) => r.source_text), ["Fire Gem", "Ice Gem", "Water Gem"]);
  assert.equal(gems[0].move, null);
});

test("members carry this browser's edits", () => {
  const edits = new Map([["move:mega-punch", { row: { translation: "Мегакулак", label: "" } }]]);
  const rows = memberRows(fam("word:move:punch"), get, facts, (t) => edits.get(t.id)?.row ?? t,
    (id) => edits.has(id));
  const mega = rows.find((r) => r.id === "move:mega-punch");
  assert.equal(mega.translation, "Мегакулак");
  assert.equal(mega.edited, true);
  assert.equal(marksOf(rows).apart.size, 0); // every member now holds «кулак»
});

test("marks: hyphens, shortened labels and the shared word", () => {
  const rows = memberRows(fam("word:move:punch"), get, facts);
  const marks = marksOf(rows);
  assert.deepEqual([...marks.hyphen].sort(), ["move:focus-punch", "move:mach-punch"]);
  assert.equal(marks.shared.word, "кулак");
  assert.deepEqual([...marks.apart], ["move:mega-punch"]);
  assert.equal(marksText(marks, rows.length), "4 of 5 share «кулак» · 2 hyphenated · 0 shortened");
  const gems = marksOf(memberRows(fam("word:item:gem"), get, facts));
  assert.deepEqual([...gems.shortened], ["item:ice-gem"]);
  assert.equal(gems.shared, null); // a shared suffix is not a shared word
  assert.equal(hasHyphen("Кулак-пуля"), true);
  assert.equal(hasHyphen("Кулак пули"), false);
  assert.equal(isShortened({ translation: "Лёдолит", label: "ледолит" }, { "ё": "е" }), false); // ё as е
});

test("shared word: compounds count, below half there is none", () => {
  const rows = (...xs) => xs.map((translation, i) => ({ id: `t${i}`, translation }));
  assert.equal(sharedWord(rows("Ядохвост", "Драконий хвост", "Удар хвостом", "Виляние")).word, "хвост");
  assert.equal(sharedWord(rows("Искра", "Гром", "Разряд", "Удар молнии")), null);
  assert.equal(sharedWord(rows("Гром", "Громоклык", "Искра", "Разряд")).ids.size, 2); // half
});

test("filters: kind, category, and search through members", () => {
  const ids = (f) => filterFamilies(families, f, get).map((i) => families[i].id);
  assert.deepEqual(ids({ kind: "list" }), ["list:punching"]);
  assert.deepEqual(ids({ category: "item" }), ["word:item:gem"]);
  assert.deepEqual(ids({ search: "лёдолит" }), ["word:item:gem"]); // ё as е, a member
  assert.ok(ids({ search: "punch" }).includes("word:move:punch"));
  assert.deepEqual(ids({ search: "type:13" }), ["type:13"]);
});

test("switching projects discards an unfinished load", async () => {
  let finish;
  const data = new Promise((resolve) => { finish = resolve; });
  const view = new FamiliesView({ loadTerms: () => data, dataset: async () => DATASET, terms: () => TERMS },
    () => ({ replaceChildren() {} }));
  view.reset();
  const pending = view.of("move:fire-punch");
  view.reset();
  finish();
  assert.deepEqual(await pending, []);
  assert.equal(view.families.length, 0);
  const fresh = await view.of("move:fire-punch");
  assert.deepEqual(fresh.map((f) => f.id), ["list:punching", "word:move:punch"]);
});

test("relabel waits for the data, then names the families in the site language", async () => {
  const view = new FamiliesView({ loadTerms: async () => {}, dataset: async () => DATASET, terms: () => TERMS },
    () => ({ replaceChildren() {} }));
  view.reset();
  view.relabel(); // nothing loaded yet
  await view.ensure();
  setCatalog({ "{list} moves": "Приёмы: {list}" });
  try {
    view.relabel();
    assert.equal(view.family("list:punching").title, "Приёмы: Punching");
    assert.deepEqual((await view.of("move:fire-punch")).map((f) => f.title), ["Приёмы: Punching", "Punch"]);
  } finally {
    setCatalog(null);
  }
  view.relabel();
  assert.equal(view.family("list:punching").title, "Punching moves");
});
