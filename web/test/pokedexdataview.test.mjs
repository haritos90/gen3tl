import assert from "node:assert/strict";

import { readFileSync } from "node:fs";

import { test } from "node:test";

import { bandLevel, battlesFor, cardEncounters, crossMatches, settingsLabel, variantConditions, DATA_MODES, defaultMoves, difficultyFields, difficultyLabel, effectOptions, encountersOf, encounterRate, encounterTables, evTraining, evYield, filterEntries, inlineTrainerIvs, itemEvolutions, trainerEvRows, itemFacts, itemFields, learnerColumnWidth, learnerGroupsOf, learnerIndex, learnLabel, learnsetGroups, levelRange, listOrder, locationGroups, methodLabel, moveFacts, moveFields, moveMatches, moveProperties, PokedexDataView, propertyOptions, rankedBy, sectionGroups, speciesLocations, targetLabel, trainerGroups, trainerIvMember, trainerIvSummary, trainerName, variantLabel } from "../js/pokedexdataview/pokedexdataview.js";

import { FILTER_SETS, FilterSet } from "../js/filters.js";

import { EFFECT_COUNT, effectName } from "../js/moveeffects.js";

import { setCatalog, setLabelSource, setUiLanguage } from "../js/ui.js";

import { EntryList } from "../js/listview.js";

import { PokedexView } from "../js/pokedex/pokedex.js";

import { domPage, ENCOUNTER_LABELS, LEARNSET_LABELS, NEEDS_DOM, stubPage, TRAINER_DIFFICULTY } from "./fixture.mjs";

import { factorWrites, itemSpotCrop, relabelMatches } from "../js/pokedexdataview/pokedexdataview.js";

test("existing cross-category results change names without reordering", () => {
  const hits = [{ kind: "moves", id: 1, name: "Old move", rank: 0 },
    { kind: "items", id: 3, name: "Old item", rank: 1 }];
  const move = hits[0], item = hits[1];
  relabelMatches(hits, {
    host: { data: () => ({ moves: [{ id: 1, name: "moves:1" }], items: [{ id: 3, name: "items:3" }] }) },
    text: (id) => ({ "moves:1": "New move", "items:3": "New item" })[id],
  });
  assert.equal(hits[0], move);
  assert.equal(hits[1], item);
  assert.deepEqual(hits.map(({ name, rank }) => [name, rank]), [["New move", 0], ["New item", 1]]);
});

test("cross-category public species search applies only declared browsing status", () => {
  const stats = { hp: 255, attack: 255, defense: 255, speed: 255, sp_attack: 255, sp_defense: 255 };
  const species = [
    { slot: 253, dex: 0, name: "species:253", base_stats: stats, catch_rate: 0 },
    { slot: 278, dex: 253, name: "species:278" },
    { slot: 500, dex: 0, name: "species:500", base_stats: stats, catch_rate: 0 },
  ];
  const names = (id) => ({ "species:253": "MISSINGNO.", "species:500": "MISSINGNO.", "species:278": "Grovyle" })[id] ?? "";
  const data = { species, species_browsing: { "253": "unreleased" } };
  const original = structuredClone(species);
  for (const mode of ["moves", "items", "trainers", "locations", "types"]) {
    const missing = crossMatches("MISSINGNO.", data, names, names, mode, species);
    assert.deepEqual(missing.filter((r) => r.kind === "species").map((r) => r.id), [500]);
    for (const query of ["253", "278"]) {
      const hits = crossMatches(query, data, names, names, mode, species);
      assert.ok(hits.some((r) => r.kind === "species" && r.id === 278), `${mode} ${query}`);
      assert.ok(!hits.some((r) => r.kind === "species" && r.id === 253));
    }
  }
  const raw = crossMatches("MISSINGNO.", { species }, names, names, "moves", species);
  assert.deepEqual(raw.map((r) => r.id), [253, 500]);
  assert.deepEqual(species, original);
});

test("entry browsing rules filter lists and cross-search independently of names and identifiers", () => {
  const data = Object.fromEntries(["moves", "items", "abilities"].flatMap((kind) => [
    [kind, [0, 7, 8].map((id) => ({ id, name: `${kind}:${id}` }))],
    [`${kind}_browsing`, { "7": "unreleased" }],
  ]));
  const before = structuredClone(data);
  for (const name of ["", "---", "Placeholder", "Localized name"]) {
    const text = () => name;
    for (const mode of ["moves", "items"]) {
      const view = Object.create(PokedexDataView.prototype);
      Object.assign(view, { mode, host: { data: () => data } });
      assert.deepEqual(view.records.map((row) => row.id), [0, 8]);
      assert.equal(view.record(7), null);
      assert.equal(view.recordFor(mode, 7), data[mode][1]);
      for (const query of ["7", name].filter(Boolean)) {
        const matches = crossMatches(query, data, text, text, "species");
        assert.ok(!matches.some((row) => row.id === 7));
      }
      assert.ok(crossMatches("0", data, text, text, "species").some((row) => row.kind === mode && row.id === 0));
    }
  }
  const view = Object.create(PokedexView.prototype);
  view.host = { data: () => data };
  const context = view.filterContext();
  for (const kind of ["moves", "items", "abilities"]) {
    assert.deepEqual(context[kind].map((row) => row.id), [0, 8]);
  }
  assert.deepEqual(data, before);
});

function dataRenderer(parent, $) {
  const view = new PokedexDataView(parent, $);
  parent.list ??= new EntryList($("x-list"), $("x-list-space"), (p) => view.row(p));
  parent.renderList ??= () => parent.list.render();
  return view;
}

const text = (id) => ({
  "moves:1": "Thunderbolt", "trainer_names:1": "Joey", "trainer_classes:57": "Youngster",
  "mapsec:102": "Route 2", "species:163": "Hoothoot", "species:72": "Tentacool",
})[id] ?? "";

const move = {
  id: 1, name: "moves:1", type: 13, damage_category: 1, power: 90, pp: 15,
  accuracy: 100, priority: 0, effect: 31, effect_key: "flinch_hit", target: 0, target_key: "selected",
  secondary_chance: 30, flinch_chance: 31, flags: 0, flag_keys: [], list_keys: [], offset: "0xa769af",
};

test("move page power shows tier rows or an expression without the placeholder", () => {
  globalThis.document = { createElement: (tag) => ({ ...fakeElement(), tag }), createTextNode: (text) => ({ text }) };
  const labels = { "page:power:test": "Effective target weight", "page:power:cond:light": "Under 10 kg",
    "page:power:cond:heavy": "At least 10 kg", "page:power:return": "Power by friendship" };
  setLabelSource((key) => labels[key] ? { text: labels[key], game: false } : null);
  try {
    const view = { view: { text: () => "" }, data: {}, categoryIcon: () => fakeElement(), learnerTable: () => fakeElement() };
    const pagePower = (record) => PokedexDataView.prototype.moveDetail.call(view, record)
      .children[0].children[0].children[0].children[5].children[0].children[1];
    const record = { ...move, power: 1, power_kind: "computed", power_formula: "page:power:test",
      power_tiers: [["page:power:cond:light", 20], ["page:power:cond:heavy", 40]] };
    const power = pagePower(record);
    const table = power.children[1];
    assert.equal(table.tag, "table");
    assert.deepEqual(table.children[1].children.map((row) => row.children.map((cell) => cell.children[0])),
      [["Under 10 kg", "20"], ["At least 10 kg", "40"]]);
    assert.ok(!JSON.stringify(power).includes('"1"'));
    const sentence = pagePower({ ...move, power: 1, power_kind: "computed", power_formula: "page:power:return" });
    assert.equal(sentence.tag, "p");
    assert.deepEqual(sentence.children, ["Power by friendship"]);
  } finally {
    setLabelSource(null);
    delete globalThis.document;
  }
});

test("move rows and cards show English for empty or missing translations", async () => {
  const fake = () => ({
    children: [], style: {}, className: "", textContent: "", value: "", hidden: false,
    addEventListener() {}, setAttribute(k, v) { this[k] = v; },
    append(...kids) { this.children.push(...kids.flat()); },
    replaceChildren(...kids) { this.children = kids.flat(); },
    classList: { toggle() {} },
  });
  globalThis.document = { createElement: () => fake(), createTextNode: (value) => ({ text: value }) };
  const { PokedexView } = await import("../js/pokedex/pokedex.js");
  const elements = new Map();
  const $ = (id) => {
    if (!elements.has(id)) elements.set(id, fake());
    return elements.get(id);
  };
  const parent = new PokedexView({
    data: () => ({ moves: [move], species: [] }),
    draft: () => false, spriteSrc: (path) => path, setHash() {},
  }, $);
  const view = parent.dataView;
  view.mode = "moves";
  view.filtered = [move];
  for (const target of [{ translation: "" }, {}, { translation: "Громовой разряд" }]) {
    parent.texts.set(move.name, { source_text: "Thunderbolt", ...target });
    view.renderDetail(move.id);
    const expected = target.translation || "Thunderbolt";
    assert.equal($("x-data-title").textContent, expected);
    assert.equal(view.row(0).children[1].children[0], expected);
    assert.equal(view.source(move.name), "Thunderbolt");
  }
  delete globalThis.document;
});

test("move properties name ability readers and link item readers", () => {
  const fake = () => ({
    children: [], className: "", title: "", type: "",
    addEventListener() {}, setAttribute() {},
    append(...kids) { this.children.push(...kids.flat()); },
  });
  globalThis.document = { createElement: () => fake(), createTextNode: (value) => ({ text: value }) };
  const record = { ...move, flags: 1, flag_keys: ["makes_contact"], list_keys: ["punching", "sound"] };
  const readers = {
    makes_contact: { readers: { abilities: [100, 217], items: [578] } },
    punching: { readers: { abilities: [93], items: [] } },
    sound: { readers: { abilities: [43, 218], items: [709] } },
  };
  const names = (id) => ({
    "abilities:100": "Unseen Fist", "abilities:217": "Long Reach", "abilities:93": "Iron Fist",
    "abilities:43": "Soundproof", "abilities:218": "Liquid Voice", "items:578": "Protect Pads", "items:709": "Throat Spray",
  })[id] ?? "";
  const shown = JSON.stringify(moveProperties(record, readers, names));
  for (const expected of ["Makes contact", "Unseen Fist", "Long Reach", "Protect Pads", "Punching", "Iron Fist",
    "Sound", "Soundproof", "Liquid Voice", "Throat Spray", "items:578", "items:709"]) {
    assert.ok(shown.includes(expected), expected);
  }
  // engine fields sit in the technical record
  assert.equal(moveFacts(record).find(([key]) => key === "Raw flags")[1], 1);
  assert.deepEqual(moveFacts(record).map(([key]) => key), ["Effect id", "Raw flags", "ROM offset"]);
  assert.equal(moveFields(record, names).some(([key]) => key === "Raw flags" || key === "Flags" || key === "Effect id"), false);
  assert.equal(moveProperties(move), "None");
  delete globalThis.document;
});

test("move filtering and fields include type, category and secondary chance", () => {
  assert.equal(filterEntries([move], "thunder", text, "moves").length, 1);
  assert.equal(filterEntries([move], "13", text, "moves").length, 1);
  assert.equal(filterEntries([move], "special", text, "moves").length, 1);
  assert.equal(filterEntries([move], "physical", text, "moves").length, 0);
  const fields = moveFields(move, (id) => (id === "types:13" ? "Electric" : ""));
  assert.deepEqual(fields.find(([key]) => key === "Type"), ["Type", "Electric"]);
  assert.deepEqual(fields.find(([key]) => key === "Category"), ["Category", "Special"]);
  assert.deepEqual(fields.find(([key]) => key === "Power"), ["Power", "90"]);
  assert.deepEqual(fields.find(([key]) => key === "PP"), ["PP", 15]);
  assert.deepEqual(fields.find(([key]) => key === "Accuracy"), ["Accuracy", 100]);
  assert.deepEqual(fields.find(([key]) => key === "Flinch chance"), ["Flinch chance", "31%"]);
  assert.deepEqual(fields.find(([key]) => key === "Target"), ["Target", "Selected target"]);
  assert.equal(targetLabel(32), "All others");
  assert.equal(targetLabel(3), "Target 3");
});

test("the move filter combines type, category, thresholds, effect and learner", () => {
  const surf = { id: 57, type: 11, damage_category: 1, power: 95, accuracy: 100, priority: 0, effect: 0, secondary_chance: 0 };
  const airSlash = { id: 356, type: 2, damage_category: 1, power: 75, accuracy: 95, priority: 0, effect: 31, secondary_chance: 30, flinch_chance: 31 };
  const agility = { id: 97, type: 14, damage_category: 2, power: 0, accuracy: 0, priority: 0, effect: 52, secondary_chance: 0 };
  const quick = { id: 98, type: 0, damage_category: 0, power: 40, accuracy: 100, priority: 1, effect: 103, secondary_chance: 0 };
  const moves = [surf, airSlash, agility, quick];
  const ids = (criteria) => moves.filter((m) => moveMatches(m, criteria)).map((m) => m.id);
  assert.deepEqual(ids(null), [57, 356, 97, 98]);
  assert.deepEqual(ids({ type: 11 }), [57]);
  assert.deepEqual(ids({ category: 2 }), [97]);
  assert.deepEqual(ids({ minPower: 80 }), [57]);
  assert.deepEqual(ids({ minAccuracy: 100 }), [57, 98]);
  assert.deepEqual(ids({ minPriority: 1 }), [98]);
  assert.deepEqual(ids({ flinch: true }), [356]);
  assert.deepEqual(ids({ secondary: true }), [356]);
  assert.deepEqual(ids({ effect: 52 }), [97]);
  assert.deepEqual(ids({ learner: new Set([57, 97]), category: 1 }), [57]);
  assert.deepEqual(effectOptions(moves), [{ id: "flinch_hit", name: "Flinch hit" }, { id: "hit", name: "Hit" },
    { id: "quick_attack", name: "Quick Attack" }, { id: "speed_up_2", name: "Speed up 2" }]);
  assert.equal(EFFECT_COUNT, 249);
  assert.equal(effectName(12), "Speed up");
  assert.equal(effectName(212), "Dragon Dance");
  assert.equal(effectName(300), "Effect 300");
});

test("items have their own entry kind with readable fields", () => {
  assert.deepEqual(DATA_MODES, ["moves", "items", "trainers", "locations"]);
  const tm = { id: 289, name: "items:289", description: "s1", pocket: 4, price: 3000, held_effect: 0, hold_effect_param: 0,
    battle_usage: 0, tm_index: 0, taught_move: 264, offset: "0x8" };
  const berry = { id: 133, name: "items:133", pocket: 5, price: 20, held_effect: 1, hold_effect_param: 10, battle_usage: 3,
    tm_index: null, taught_move: null, offset: "0x9" };
  // engine ids and the offset sit in the technical record
  assert.deepEqual(itemFields(tm), [["Pocket", "TMs and HMs"], ["Base buy price (Pokédollar)", 3000], ["Machine", "TM 1"]]);
  assert.deepEqual(itemFacts(tm), [["ROM offset", "0x8"]]);
  assert.deepEqual(itemFields(berry), [["Pocket", "Berries"], ["Base buy price (Pokédollar)", 20]]);
  assert.deepEqual(itemFacts(berry), [["Held effect id", 1], ["Held effect parameter", 10], ["Battle usage id", 3], ["ROM offset", "0x9"]]);
  assert.equal(itemFields({ pocket: 9, price: 0 })[0][1], "Pocket 9");
  const names = (id) => ({ "items:289": "ТМ01" })[id] ?? "";
  assert.equal(filterEntries([tm, berry], "тм01", names, "items").length, 1);
  assert.equal(filterEntries([tm, berry], "133", names, "items")[0].id, 133);
  assert.equal(filterEntries([tm, berry], "tm01", names, "items", (id) => (id === "items:289" ? "TM01" : "")).length, 1);
});

test("item prices use declared payouts and localized base-price labels", () => {
  const item = { pocket: 3, price: 201, sell_price: 100 };
  assert.deepEqual(itemFields(item).slice(1), [
    ["Base buy price (Pokédollar)", 201], ["Base sell price (Pokédollar)", 100],
  ]);
  for (const sell_price of [undefined, null, -1, "100", false]) {
    assert.equal(itemFields({ ...item, sell_price }).length, 2);
  }
  assert.equal(itemFields({ ...item, sell_price: 0 })[2][1], 0);
  assert.equal(itemFields({ ...item, sell_price: 67 })[2][1], 67);
  setCatalog({ "Base buy price": "Purchase base", "Base sell price": "Sale base" });
  try {
    assert.deepEqual(itemFields(item).slice(1), [
      ["Purchase base (Pokédollar)", 201], ["Sale base (Pokédollar)", 100],
    ]);
  } finally {
    setCatalog(null);
  }
});

test("held effect amounts replace the label and follow the interface number format", () => {
  const item = { pocket: 1, price: 0, held_effect: 29, hold_effect_param: 2, held_effect_key: "Boost Speed; lock one move",
    held_effect_amount: { key: "Boost Speed: ×{x}; lock one move", values: { x: 1.5 } } };
  const typed = { pocket: 1, price: 0, held_effect: 129, hold_effect_param: 23, held_effect_key: "Boost moves of type: {type}",
    held_effect_param_name: "types:23", held_effect_amount: { key: "Boost moves of type {type}: ×{x}", values: { x: 1.2 } } };
  const effect = (record) => itemFields(record, (id) => ({ "types:23": "Fairy" })[id] ?? "").find(([name]) => name === "Held effect")[1];
  assert.equal(effect(item), "Boost Speed: ×1.5; lock one move");
  assert.equal(effect(typed), "Boost moves of type Fairy: ×1.2");
  assert.equal(effect({ ...item, held_effect_amount: undefined }), "Boost Speed; lock one move");
  setUiLanguage("ru");
  try {
    assert.equal(effect(item), "Boost Speed: ×1,5; lock one move");
  } finally {
    setUiLanguage("en");
  }
});

test("item conditions stay ordered and localize on cards and popups", async () => {
  const { record, view } = currencyFixture();
  const { itemCardContent } = await import("../js/movecard.js");
  const base = "Double the holder's stat.";
  const conditions = ["No bonus for a scaled holder.", "An unevolved holder keeps the bonus."];
  const item = { ...record, held_effect_key: "Boost the holder's stat",
    held_effect_amount: { key: base, values: {}, conditions } };
  const labels = new Map([[base, "Localized bonus."], [conditions[0], "Localized exception."],
    [conditions[1], "Localized explanation."]]);
  try {
    const effect = () => itemFields(item).find(([key]) => key === "Held effect")[1];
    assert.equal(effect(), [base, ...conditions].join(" "));
    setLabelSource((key) => labels.has(key) ? { text: labels.get(key), game: false } : null);
    const expected = "Localized bonus. Localized exception. Localized explanation.";
    assert.equal(effect(), expected);
    for (const card of [PokedexDataView.prototype.itemDetail.call(view, item), itemCardContent(item)]) {
      assert.ok(JSON.stringify(card).includes(expected));
      assert.ok(!JSON.stringify(card).includes(conditions[0]));
    }
    labels.delete(conditions[1]);
    assert.equal(effect(), "Localized bonus. Localized exception. " + conditions[1]);
    item.held_effect_amount = { key: "Lower weight: ×{x}", values: { x: 0.5 },
      conditions: ["In battles where weight determines Speed.", "Heavier holders move faster."] };
    assert.equal(effect(), "Lower weight: ×0.5. In battles where weight determines Speed. Heavier holders move faster.");
  } finally {
    setLabelSource(null);
    clearCurrencyFixture();
  }
});

test("unnamed item effects retain raw facts and absent effects stay absent", () => {
  const row = { pocket: 9, price: 0, held_effect: 255, hold_effect_param: 23, battle_usage: 9, offset: "0x1" };
  assert.deepEqual(itemFields(row), [["Pocket", "Pocket 9"], ["Base buy price (Pokédollar)", 0]]);
  assert.deepEqual(itemFacts(row), [
    ["Held effect id", 255], ["Held effect parameter", 23], ["Battle usage id", 9], ["ROM offset", "0x1"],
  ]);
  assert.deepEqual(itemFacts({ ...row, held_effect: 0, battle_usage: 0 }), [["ROM offset", "0x1"]]);
});

function currencyFixture() {
  const fake = () => ({
    children: [], className: "", style: { cssText: "" }, addEventListener() {}, setAttribute(k, v) { this[k] = v; },
    append(...kids) { this.children.push(...kids.flat()); },
    replaceChildren(...kids) { this.children = kids.flat(); },
  });
  globalThis.document = { createElement: () => fake(), createTextNode: (t) => ({ text: t }) };
  const images = [];
  globalThis.Image = class { constructor() { images.push(this); } };
  const view = {
    data: { icons: { pokedollar: "icons/pokedollar.png" } }, host: { spriteSrc: (path) => `data/${path}` },
    view: { text: () => "", openMove() {} }, itemUpgradeBlock: () => [], itemSourceBlock: () => [], itemUseBlock: () => [], itemEvolutionBlock: () => [],
    pokedollarIcon: PokedexDataView.prototype.pokedollarIcon,
  };
  return { images, record: { id: 93, pocket: 1, price: 2100, sell_price: 1050, tm_index: null }, view };
}

function clearCurrencyFixture() {
  delete globalThis.document;
  delete globalThis.CSS;
  delete globalThis.Image;
}

test("item price text stays visible until the accessible currency mask loads", () => {
  const { images, record, view } = currencyFixture();
  globalThis.CSS = { supports: (property) => property === "mask-image" };
  try {
    const priceLabel = (card) => card.children[0].children[0].children[0].children[2].children[2];
    const detail = PokedexDataView.prototype.itemDetail.call(view, record);
    const icon = priceLabel(detail);
    const saleIcon = detail.children[0].children[0].children[0].children[4].children[2];
    assert.notEqual(icon, saleIcon);
    assert.deepEqual(saleIcon.children, ["Pokédollar"]);
    assert.equal(detail.children[0].children[0].children[0].children[5].children[0], "1050");
    assert.deepEqual(icon.children, ["Pokédollar"]);
    assert.equal(icon.className, "");
    assert.equal(images[0].src, "data/icons/pokedollar.png");
    images[0].onload();
    assert.deepEqual(icon.children, []);
    assert.equal(icon.className, "dex-pokedollar-icon");
    assert.equal(icon.role, "img");
    assert.equal(icon["aria-label"], "Pokédollar");
    assert.equal(icon.style.cssText, '--icon:url("data/icons/pokedollar.png")');
    assert.equal(saleIcon.className, "dex-pokedollar-icon");
    assert.deepEqual(saleIcon.children, []);
    const cached = priceLabel(PokedexDataView.prototype.itemDetail.call(view, record));
    assert.deepEqual(cached.children, []);
    assert.equal(cached.className, "dex-pokedollar-icon");
    assert.equal(images.length, 1);
    view.data = {};
    const fallback = priceLabel(PokedexDataView.prototype.itemDetail.call(view, record));
    assert.deepEqual(fallback.children, ["Pokédollar"]);
    assert.ok(!JSON.stringify(fallback).includes("¥"));
  } finally {
    clearCurrencyFixture();
  }
});

test("item popup currency text remains visible when the icon asset fails", async () => {
  const { images, record, view } = currencyFixture();
  globalThis.CSS = { supports: () => true };
  setLabelSource((key) => key === "Pokédollar" ? { text: "Pokédollar localisé", game: false } : null);
  try {
    const { itemCardContent } = await import("../js/movecard.js");
    const card = itemCardContent(record, { text: () => "", pokedollarIcon: () => view.pokedollarIcon() });
    const icon = card.children[1].children[2].children[2];
    const saleIcon = card.children[1].children[4].children[2];
    assert.deepEqual(icon.children, ["Pokédollar localisé"]);
    images[0].onerror();
    assert.deepEqual(icon.children, ["Pokédollar localisé"]);
    assert.equal(icon.className, "");
    assert.equal(icon.style.cssText, "");
    assert.equal(icon["aria-label"], "Pokédollar localisé");
    assert.deepEqual(saleIcon.children, ["Pokédollar localisé"]);
    assert.equal(saleIcon.className, "");
  } finally {
    setLabelSource(null);
    clearCurrencyFixture();
  }
});

test("item price currency text remains visible when CSS masks are unsupported", () => {
  const { images, view } = currencyFixture();
  globalThis.CSS = { supports: () => false };
  try {
    const icon = view.pokedollarIcon();
    assert.deepEqual(icon.children, ["Pokédollar"]);
    assert.equal(icon.className, "");
    assert.equal(icon.style.cssText, "");
    assert.equal(images.length, 0);
  } finally {
    clearCurrencyFixture();
  }
});

test("a move page opened by a deep link fills its learners once the learnsets load", async () => {
  const fake = () => ({
    children: [], style: {}, listeners: {}, className: "", textContent: "", value: "", hidden: false,
    title: "", disabled: false, src: "",
    addEventListener(ev, fn) { this.listeners[ev] = fn; }, setAttribute(k, v) { this[k] = v; },
    append(...kids) { this.children.push(...kids.flat()); }, replaceChildren(...kids) { this.children = kids.flat(); },
    classList: { toggle() {} },
  });
  globalThis.document = { createElement: () => fake(), createTextNode: (t) => ({ text: t }) };
  const surf = { id: 57, name: "moves:57", type: 11, damage_category: 1, power: 90, accuracy: 100, pp: 15, target: 32,
    effect: 0, flags: 0, secondary_chance: 0, offset: "0x1" };
  const species = [{ slot: 7, dex: 7, name: "species:7", learnset: undefined }];
  let pending = true;
  const parent = {
    host: { data: () => ({ moves: [surf], species, learnsets: "learnsets.json", types: [] }), spriteSrc: (p) => p, setHash() {} },
    generation: 0, mode: "moves", texts: new Map(), opened: [],
    text: (id) => ({ "moves:57": "Surf", "species:7": "Squirtle" })[id] ?? "",
    record: (slot) => species.find((r) => r.slot === slot) ?? null,
    speciesName: (record) => "#007 Squirtle",
    learnsetsPending: () => pending,
    ensureLearnsets: async () => { pending = false; species[0].learnset = [{ method: 1, move: 57, level: null }]; return true; },
    openSpecies() {},
  };
  const elements = new Map();
  const $ = (id) => {
    if (!elements.has(id)) elements.set(id, fake());
    return elements.get(id);
  };
  const view = dataRenderer(parent, $);
  view.mode = "moves";
  parent.goTo = ({ id }) => view.renderDetail(id);
  view.open("57"); // the route's id, a string
  assert.equal(view.cur, "57");
  const shown = () => JSON.stringify($("x-data-content").children);
  assert.ok(shown().includes("Loading the learners"));
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  assert.ok(shown().includes("#007 Squirtle"), shown().slice(0, 200));
  assert.ok(!shown().includes("Loading the learners"));
  assert.equal($("x-data-id").textContent, ""); // no id line above the title
  // Enter in the search opens the first filtered record
  view.filtered = [{ id: 57 }, { id: 58 }];
  view.cur = null;
  assert.equal(view.openFirst(), true);
  assert.equal(view.cur, 57);
  view.filtered = [];
  assert.equal(view.openFirst(), false);
  delete globalThis.document;
});

test("failed learners show retry while stale navigation leaves current content alone", async () => {
  const fake = (tag) => ({
    tag, children: [], listeners: {},
    addEventListener(event, fn) { this.listeners[event] = fn; },
    setAttribute(key, value) { this[key] = value; },
    append(...children) { this.children.push(...children.flat()); },
    replaceChildren(...children) { this.children = children.flat(); },
  });
  globalThis.document = { createElement: fake };
  const moves = [{ id: 57 }, { id: 58 }];
  let finish, pending = true, requests = 0;
  const parent = {
    generation: 0, mode: "moves", host: { data: () => ({ moves, species: [] }) },
    learnsetsPending: () => pending,
    ensureLearnsets: () => { requests++; return new Promise((resolve) => { finish = resolve; }); },
  };
  const view = new PokedexDataView(parent, () => fake());
  view.mode = "moves";
  view.cur = "57";
  let shown;
  view.renderDetail = () => { shown = view.learnerTable(moves[0]); };
  const text = () => JSON.stringify(shown.children);
  view.renderDetail();
  assert.equal(requests, 1);
  finish(false);
  await Promise.resolve();
  assert.match(text(), /Could not load the learners\./);
  assert.doesNotMatch(text(), /Loading the learners/);
  const retry = shown.children.find((node) => node.tag === "button");
  assert.equal(retry.children[0], "Retry");
  retry.listeners.click();
  assert.equal(requests, 2);
  assert.match(text(), /Loading the learners/);
  pending = false;
  finish(true);
  await Promise.resolve();
  assert.match(text(), /No species learns it/);
  for (const navigate of [() => { view.cur = 58; }, () => { parent.mode = "species"; }, () => { parent.generation++; }]) {
    pending = true;
    parent.mode = "moves";
    view.cur = "57";
    view.renderDetail();
    const before = shown;
    navigate();
    finish(false);
    await Promise.resolve();
    assert.equal(shown, before);
    assert.match(text(), /Loading the learners/);
  }
});

test("the learner index lists every species of a move with how it learns it", () => {
  const species = [
    { slot: 1, learnset: [{ method: 0, level: 0, move: 33 }, { method: 1, level: null, move: 264, index: 0 }] },
    { slot: 4, learnset: [{ method: 0, level: 7, move: 52 }, { method: 3, level: null, move: 33 }] },
    { slot: 5, learnset: null },
  ];
  const index = learnerIndex(species);
  assert.deepEqual(index.get(33), [{ slot: 1, method: 0, level: 0 }, { slot: 4, method: 3, level: null }]);
  assert.deepEqual(index.get(264).map((e) => e.slot), [1]);
  assert.equal(index.get(99), undefined);
  assert.deepEqual([{ method: 0, level: 0 }, { method: 0, level: 1 }, { method: 0, level: 7 }, { method: 1 }, { method: 2 }, { method: 3 }].map(learnLabel),
    ["Start", "Start", "Lv 7", "TM/HM", "Egg", "Tutor"]);
});

test("learnsets group by method with levels and machine items", () => {
  const record = { learnset: [
    { method: 0, level: 16, move: 52, index: null }, { method: 0, level: 0, move: 356, index: null },
    { method: 1, level: null, move: 264, index: 0 }, { method: 1, level: null, move: 15, index: 100 },
    { method: 3, level: null, move: 7, index: 0 }, { method: 2, level: null, move: 44, index: null },
    { method: 9, level: null, move: 1, index: null },
  ] };
  const items = [{ id: 289, tm_index: 0, taught_move: 264 }, { id: 300, tm_index: 11, taught_move: 15 }];
  const groups = learnsetGroups(record, items);
  assert.deepEqual(groups.level_up.map((e) => [e.level, e.move]), [[0, 356], [16, 52]]);
  assert.deepEqual(groups.tmhm.map((e) => [e.index, e.move, e.item]), [[0, 264, 289], [100, 15, null]]);
  assert.deepEqual(groups.tutor.map((e) => e.move), [7]);
  assert.deepEqual(groups.egg.map((e) => e.move), [44]);
  assert.deepEqual(learnsetGroups({}), { level_up: [], tmhm: [], tutor: [], egg: [] });
});

test("the species Moves block renders a table per method with icons and links", async () => {
  const fake = () => ({
    children: [], style: {}, listeners: {}, className: "", textContent: "", value: "", hidden: false,
    title: "", disabled: false, src: "", alt: "",
    addEventListener(ev, fn) { this.listeners[ev] = fn; },
    setAttribute(k, v) { this[k] = v; },
    append(...kids) { this.children.push(...kids.flat()); },
    replaceChildren(...kids) { this.children = kids.flat(); },
    classList: { toggle() {} },
  });
  globalThis.document = { createElement: () => fake(), createTextNode: (t) => ({ text: t }) };
  const moves = []; moves[52] = { id: 52, type: 10, damage_category: 1, power: 40, accuracy: 100, pp: 25 };
  moves[264] = { id: 264, type: 1, damage_category: 0, power: 150, accuracy: 100, pp: 20 };
  const parent = {
    host: { data: () => ({ moves, items: [{ id: 289, tm_index: 0 }], icons: { split: ["icons/physical.png", "icons/special.png", "icons/status.png"] } }),
      spriteSrc: (p) => `data/${p}` },
    generation: 0, texts: new Map(), opened: null,
    text: (id) => ({ "moves:52": "Ember", "moves:264": "Focus Punch", "types:10": "Fire", "types:1": "Fighting", "items:289": "TM01" })[id] ?? "",
    openMove(id) { this.opened = id; },
    openItem(id) { this.openedItem = id; },
  };
  const view = dataRenderer(parent, fake);
  const tables = view.learnsetTables({ learnset: [
    { method: 0, level: 7, move: 52, index: null }, { method: 1, level: null, move: 264, index: 0 },
  ] });
  assert.deepEqual(tables.map((t) => t.children[0].children[0]), ["Level up", "TM and HM"]);
  const row = tables[0].children[1].children[0].children[1].children[0];
  const cell = (td) => (String(td.children[0]?.className).startsWith("dex-cat-icon") ? "icon" : td.children[0]?.children?.[0] ?? td.children[0]);
  assert.deepEqual(row.children.map(cell), ["Lv 7", "Ember", "Fire", "icon", "40", "100", "25"]);
  assert.equal(row.children[3].children[0].className, "dex-cat-icon dex-cat-1");
  assert.equal(row.children[3].children[0].style, '--icon:url("data/icons/special.png")');
  row.children[1].children[0].listeners.click();
  assert.equal(parent.opened, 52);
  const tm = tables[1].children[1].children[0].children[1].children[0];
  // the machine links to its item page
  const machine = tm.children[0].children[0];
  assert.equal(machine.className, "dex-link dex-item-link");
  assert.equal(machine.title, "items:289");
  assert.equal(machine.children.at(-1), "TM01");
  machine.listeners.click();
  assert.equal(parent.openedItem, 289);
  assert.equal(tm.children[3].children[0].style, '--icon:url("data/icons/physical.png")');
  // without icon assets the category is its name
  parent.host.data = () => ({ moves, items: [] });
  const plain = view.learnsetTables({ learnset: [{ method: 3, level: null, move: 52, index: 0 }] });
  assert.equal(plain[0].children[0].children[0], "Tutor");
  assert.equal(plain[0].children[1].children[0].children[1].children[0].children[3].children[0].children[0], "Special");
  moves[67] = { id: 67, type: 1, damage_category: 0, power: 1, accuracy: 100, pp: 20,
    power_kind: "computed", power_weight: [[0, 20], [100, 40], [250, 60], [500, 80], [1000, 100], [2000, 120]] };
  const weighted = view.learnsetTables({ weight: 905, learnset: [
    { method: 0, level: 1, move: 67 }, { method: 1, index: 0, move: 67 },
    { method: 2, move: 67 }, { method: 3, index: 0, move: 67 },
  ] });
  assert.equal(weighted.length, 4);
  for (const table of weighted) {
    const power = table.children[1].children[0].children[1].children[0].children[4];
    assert.equal(power.children[0], "—");
    assert.equal(power.title, "");
  }
  delete globalThis.document;
});

test("every move method labels all seven columns and aligns numeric headings with values", (context) => {
  const { $ } = stubPage(context, "pokedex");
  context.after(() => { setLabelSource(null); setUiLanguage(null); });
  const moves = [
    { id: 0, type: 0, damage_category: 0, power: 40, accuracy: 95, pp: 35 },
    { id: 1, type: 1, damage_category: 1, power: 90, accuracy: 100, pp: 15 },
    { id: 2, type: 0, damage_category: 2, power: 0, accuracy: 0, pp: 20 },
  ];
  const learnset = [0, 1, 3, 2].flatMap((method) => moves.map(({ id }) =>
    ({ method, level: method === 0 ? id * 7 : null, move: id, index: id })));
  const data = { moves, species: [], items: [{ id: 1, tm_index: 1, taught_move: 1 }] };
  const parent = new PokedexView({ data: () => data, spriteSrc: (path) => path }, $);
  for (const { code, headings, machine } of LEARNSET_LABELS) {
    const keys = LEARNSET_LABELS[0].headings;
    parent.host.pageLabels = () => ({ ...Object.fromEntries(keys.map((key, i) => [key, headings[i]])), "TM and HM": machine });
    parent.lang = code;
    setUiLanguage(code);
    parent.useLabels();
    const groups = parent.dataView.learnsetTables({ learnset });
    assert.equal(groups.length, 4);
    for (const [method, group] of groups.entries()) {
      const table = group.querySelector("table");
      const headers = table.querySelectorAll("th");
      assert.deepEqual(headers.map((header) => header.textContent), [method === 1 ? machine : headings[0], ...headings.slice(1)]);
      assert.ok(headers.every((header) => header.getAttribute("scope") === "col"));
      const rows = table.querySelector("tbody").children;
      assert.equal(rows.length, moves.length);
      for (const [i, row] of rows.entries()) {
        assert.equal(headers.length, row.children.length);
        assert.deepEqual(row.children.slice(4).map((cell) => cell.textContent),
          [moves[i].power ? String(moves[i].power) : "—", moves[i].accuracy ? String(moves[i].accuracy) : "—", String(moves[i].pp)]);
        for (const [column, cell] of row.children.entries()) {
          const numeric = column >= 4 || (column === 0 && method !== 1);
          assert.equal(headers[column].className.split(" ").includes("num"), numeric);
          assert.equal(cell.className.split(" ").includes("num"), numeric);
        }
        if (method >= 2) assert.equal(row.children[0].textContent, "—");
      }
      const scroll = group.children[1];
      assert.equal(scroll.className, "dex-learn-scroll");
      assert.equal(scroll.getAttribute("tabindex"), "0");
      assert.equal(scroll.getAttribute("aria-label"), group.querySelector("h3").textContent);
    }
  }
});

const alice = [
  { id: 20, name: "trainer_names:20", class: "trainer_classes:84", party: [{ level: 31 }, { level: 30 }, { level: 32 }] },
  { id: 76, name: "trainer_names:76", class: "trainer_classes:84", party: [{ level: 83 }, { level: 84 }] },
  { id: 77, name: "trainer_names:77", class: "trainer_classes:84", party: [{ level: 85 }] },
  { id: 1, name: "trainer_names:1", class: "trainer_classes:57", party: [] },
  { id: 0, name: "trainer_names:0", class: "trainer_classes:0", party: [], party_offset: null, party_size: 0 },
];

const source = (id) => ({
  "trainer_names:20": "Alice", "trainer_names:76": "Alice", "trainer_names:77": "Alice", "trainer_names:1": "Joey",
})[id] ?? "";

test("trainers group by source name and class into one card with every battle", () => {
  const groups = trainerGroups(alice, source);
  assert.deepEqual(groups.map((group) => [group.id, group.battles.map((battle) => battle.id)]), [[20, [20, 76, 77]], [1, [1]]]);
  assert.equal(groups[0].class, "trainer_classes:84");
  // search matches names in either language and battle ids
  const ru = (id) => ({ "trainer_names:20": "Элис", "trainer_classes:84": "Лидер", "trainer_names:1": "Джоуи" })[id] ?? "";
  assert.equal(filterEntries(groups, "эли", ru, "trainers", source).length, 1);
  assert.equal(filterEntries(groups, "alice", ru, "trainers", source).length, 1);
  assert.equal(filterEntries(groups, "77", ru, "trainers", source)[0].id, 20);
  assert.equal(filterEntries(groups, "лидер", ru, "trainers", source).length, 0);
  assert.equal(filterEntries(groups, "joey", ru, "trainers", source)[0].id, 1);
  assert.equal(levelRange(alice[0].party), "30–32");
  assert.equal(levelRange(alice[2].party), "85");
  assert.equal(levelRange([]), "");
});

test("trainer names use sourced text or an explicit localized record label", (ctx) => {
  ctx.after(() => setLabelSource(null));
  const record = { id: 54, name: "trainer_names:54" };
  assert.equal(trainerName(record, () => "           ", () => ""), "Trainer 54");
  assert.equal(trainerName(record, () => " ", () => " Alice "), "Alice");
  assert.equal(trainerName(record, () => " Элис ", () => "Alice"), "Элис");
  setLabelSource((key) => key === "Trainer {n}" ? { text: "ТРЕНЕР", game: true } : null);
  assert.equal(trainerName(record, () => ""), "ТРЕНЕР 54");
});

test("blank source names do not merge unrelated trainer records", () => {
  const rows = [53, 54, 55].map((id) => ({ id, name: "shared-empty", class: "leader", party: [] }));
  rows.push({ id: 6, name: "first", class: "leader", party: [] },
    { id: 72, name: "second", class: "leader", party: [] });
  const before = structuredClone(rows);
  const source = (id) => ({ "shared-empty": "           ", first: "Mirskle", second: " Mirskle " })[id] ?? "";
  const groups = trainerGroups(rows, source);
  assert.deepEqual(groups.map((r) => r.battles.map((b) => b.id)), [[53], [54], [55], [6, 72]]);
  assert.deepEqual(rows, before);
  assert.equal(filterEntries(groups, "Trainer 54", () => "", "trainers", source)[0].id, 54);
  assert.equal(crossMatches("Trainer 54", { trainer_difficulty: TRAINER_DIFFICULTY, trainers: rows }, () => "", source, "items")[0].name, "Trainer 54");
});

test("trainer grouping refreshes once asynchronous source names arrive", async () => {
  const rows = [20, 76].map((id) => ({ id, name: `trainer_names:${id}`, class: "leader", party: [] }));
  const texts = new Map();
  const parent = { generation: 1, navigation: 1, texts, catalogId: (id) => id,
    text: (id) => texts.get(id)?.translation || texts.get(id)?.source_text || "",
    host: { data: () => ({ trainer_difficulty: TRAINER_DIFFICULTY, trainers: rows }), entry: async (id) => {
      await Promise.resolve(); return { source_text: id === "leader" ? "Leader" : "Alice", translation: "" };
    } } };
  const view = dataRenderer(parent, fakeElement); view.mode = "trainers";
  assert.equal(view.records.length, 2);
  assert.equal(await view.load(), true);
  assert.equal(view.records.length, 1);
  assert.deepEqual(view.records[0].battles.map((r) => r.id), [20, 76]);
});

test("trainer progression uses the first record and preserves class and name ties through filters", () => {
  const trainer = (id, name, classId, average, kind = "world") => ({
    id, name, class: `trainer_classes:${classId}`, average_level: average, kind, party: [],
  });
  const trainers = [
    trainer(20, "shared", 2, 30.5, "story"),
    trainer(21, "shared", 2, 1, "story"),
    trainer(22, "shared", 2, 90, "story"),
    trainer(1, "early", 2, 5.5), trainer(2, "tie", 1, 30.5),
    trainer(3, "zulu", 2, 30.5), trainer(4, "alpha", 2, 30.5),
    trainer(5, "empty", 1, null), trainer(6, "legacy", 1, undefined),
    trainer(7, "near", 2, 30.25, "special"),
  ];
  trainers[0].difficulty = ["standard"];
  trainers[1].difficulty = ["relaxed"];
  trainers[0].base = trainers[1].base = 20;
  const original = structuredClone(trainers);
  const names = { shared: "Shared", early: "Early", tie: "Tie", zulu: "Zulu", alpha: "Alpha",
    empty: "Empty", legacy: "Legacy", near: "Near", "trainer_classes:1": "A", "trainer_classes:2": "B" };
  const source = (id) => names[id] ?? "";
  for (const localized of [false, true]) {
    const text = (id) => localized ? ({ alpha: "Zulu", zulu: "Alpha" }[id] ?? source(id)) : source(id);
    const data = { trainers };
    const set = new FilterSet("trainers", FILTER_SETS.trainers, {});
    const context = { data, text, source, speciesNames: () => [] };
    const parent = {
      host: { data: () => data }, text, otherNames: () => [],
      texts: new Map(Object.entries(names).map(([id, english]) => [id, { source_text: english }])),
      filterSets: { trainers: set }, filterContext: () => context, list: { setCount() {} },
    };
    const view = new PokedexDataView(parent, fakeElement);
    view.mode = "trainers";
    const tied = localized ? [3, 20, 4] : [4, 20, 3];
    const expected = [1, 7, 2, ...tied, 5, 6];
    assert.deepEqual(view.records.map((row) => row.id), expected);
    assert.equal(view.record(21).average_level, 30.5);
    assert.deepEqual(view.record(22).battles.map((row) => row.id), [20, 21, 22]);
    view.difficulty = 1;
    view.groups = null;
    view.applyFilters();
    assert.deepEqual(view.filtered.map((row) => row.id), expected);
    set.controls.set("kind", { select: { value: "world" } });
    set.controls.set("class", { select: { value: "trainer_classes:2" } });
    view.applyFilters();
    assert.deepEqual(view.filtered.map((row) => row.id), [1, ...tied.filter((id) => id !== 20)]);
    assert.deepEqual(filterEntries(view.records, "Shared", text, "trainers", source).map((row) => row.id), [20]);
    set.controls.clear();
    view.applyFilters();
    assert.deepEqual(view.filtered.map((row) => row.id), expected);
  }
  assert.deepEqual(trainers, original);
});

test("an EV yield reads like +2 Spd, the stats in card order joined with a space", () => {
  const none = { hp: 0, attack: 0, defense: 0, speed: 0, sp_attack: 0, sp_defense: 0 };
  assert.equal(evYield({ ...none, speed: 2 }), "+2 Spd");
  assert.equal(evYield({ ...none, speed: 1, defense: 1, sp_defense: 2, hp: 3 }), "+3 HP +1 Def +2 Sp. Def +1 Spd");
  assert.equal(evYield(none), "");
  assert.equal(evYield(null), "");
  // abbreviations and sign come from the catalog and game strings
  const labels = { "Spd": { text: "Скор", game: false }, "Sp. Atk": { text: "Ос. атака", game: true },
    "+{n} {stat}": { text: "+{n} {stat}", game: false } };
  setLabelSource((key) => labels[key] ?? null);
  assert.equal(evYield({ ...none, speed: 2, sp_attack: 1 }), "+1 Ос. атака +2 Скор");
  setLabelSource(null);
});

const fakeElement = () => ({
  children: [], style: {}, listeners: {}, className: "", textContent: "", value: "", hidden: false,
  title: "", disabled: false, selected: false, src: "",
  addEventListener(ev, fn) { this.listeners[ev] = fn; },
  setAttribute(k, v) { this[k] = v; },
  append(...kids) { this.children.push(...kids.flat()); },
  replaceChildren(...kids) { this.children = kids.flat(); },
  showModal() { this.open = true; },
  close() { this.open = false; },
  classList: { toggle() {} },
});

const rooms = [
  { id: 8, name: "trainer_names:8", class: "trainer_classes:9", party: [], map: ["7.3"], map_section: [90], ev_training: true },
  { id: 249, name: "trainer_names:249", class: "trainer_classes:30", party: [], map: ["14.11"], map_section: [94], ev_training: true },
  { id: 1, name: "trainer_names:1", class: "trainer_classes:57", party: [], map: ["3.69"], map_section: [102], ev_training: false },
  { id: 2, name: "trainer_names:2", class: "trainer_classes:57", party: [], map: [], map_section: [], ev_training: false },
];

test("no technical data value is a bare catalog key", (ctx) => {
  ctx.after(() => { delete globalThis.document; });
  globalThis.document = { createElement: () => fakeElement(), createTextNode: (text) => ({ text }) };
  const battle = rooms[2];
  const parent = {
    host: { data: () => ({ trainer_difficulty: TRAINER_DIFFICULTY, trainers: [battle] }), spriteSrc: () => "" },
    text, texts: new Map(), record: () => null,
  };
  const view = dataRenderer(parent, fakeElement);
  view.mode = "trainers";
  const detail = view.trainerDetail(trainerGroups([battle])[0]);
  const fields = detail.children.at(-1).children[1].children;
  const values = fields.filter((node, index) => index % 2 === 1);
  const textOf = (node) => typeof node === "string" ? node
    : node.textContent || node.text || (node.children ?? []).map(textOf).join("");
  for (const value of values) assert.doesNotMatch(textOf(value), /^[a-z_]+:\d+$/);
  assert.equal(textOf(values[0]), "Joey Catalog key: trainer_names:1");
});

test("trainer list and card use the record portrait through the sprite host", () => {
  globalThis.document = { createElement: () => fakeElement(), createTextNode: (text) => ({ text }) };
  const battle = { ...rooms[2], sprite: "trainers/82.png", picture: 82 };
  const group = trainerGroups([battle])[0];
  const parent = { host: { data: () => ({ trainer_difficulty: TRAINER_DIFFICULTY, trainers: [battle] }), spriteSrc: (path) => `rom-art/${path}` },
    text: (id) => id, texts: new Map(), record: () => null };
  const view = dataRenderer(parent, fakeElement);
  view.mode = "trainers";
  view.filtered = [group];
  const icon = view.row(0).children[1];
  assert.equal(icon.src, "rom-art/trainers/82.png");
  assert.equal(icon.className, "dex-trainer-icon");
  assert.equal(icon.alt, "");
  const portrait = view.trainerDetail(group).children[0].children[0];
  assert.equal(portrait.src, icon.src);
  assert.equal(portrait.width, 128);
  assert.match(portrait.className, /dex-trainer-portrait/);
  assert.equal(portrait.alt, "trainer_names:1");
  assert.equal(view.trainerSprite({}), null);
  delete globalThis.document;
});

test("blank trainer list rows, cards and cross results relabel without losing identity", (ctx) => {
  ctx.after(() => { delete globalThis.document; setLabelSource(null); });
  globalThis.document = { createElement: () => fakeElement(), createTextNode: (text) => ({ text }) };
  const battle = { id: 54, name: "trainer_names:54", class: "trainer_classes:84", party: [] };
  const elements = new Map(), $ = (id) => {
    if (!elements.has(id)) elements.set(id, fakeElement());
    return elements.get(id);
  };
  const parent = { host: { data: () => ({ trainer_difficulty: TRAINER_DIFFICULTY, trainers: [battle] }), spriteSrc: () => "" },
    text: () => "           ", texts: new Map([[battle.name, { source_text: "           " }]]) };
  const view = dataRenderer(parent, $); parent.dataView = view;
  view.mode = "trainers"; view.filtered = view.records;
  view.detail = () => fakeElement();
  const textOf = (n) => typeof n === "string" ? n : n.textContent || n.text || (n.children ?? []).map(textOf).join("");
  assert.match(textOf(view.row(0)), /Trainer 54/);
  view.renderDetail(54); assert.equal($("x-data-title").textContent, "Trainer 54");
  const hits = [{ kind: "trainers", id: 54, name: "", rank: 0 }];
  relabelMatches(hits, parent); assert.match(hits[0].name, /^Trainer 54/);
  setLabelSource((key) => key === "Trainer {n}" ? { text: "ТРЕНЕР", game: true } : null);
  view.renderDetail(54); relabelMatches(hits, parent);
  assert.equal($("x-data-title").textContent, "ТРЕНЕР 54");
  assert.match(hits[0].name, /^ТРЕНЕР 54/);
  assert.equal(view.cur, 54);
});

test("the trainer registry's class options list only real classes; EV training is its own switch", () => {
  let trainers = rooms;
  const names = { "trainer_names:8": "Daniel", "trainer_names:249": "Koji", "trainer_names:1": "Joey", "trainer_names:2": "Ben" };
  const text = (id) => ({ ...names, "trainer_classes:9": "Kindler", "trainer_classes:30": "Roughneck", "trainer_classes:57": "Youngster" })[id] ?? "";
  const source = (id) => names[id] ?? "";
  const context = { data: { trainers }, text, source, speciesNames: () => [] };
  const classOptions = FILTER_SETS.trainers.find((entry) => entry.key === "class").options(context);
  assert.deepEqual(classOptions.map((option) => option.value), ["trainer_classes:9", "trainer_classes:30", "trainer_classes:57"]);
  const groups = trainerGroups(trainers, source);
  const set = new FilterSet("trainers", FILTER_SETS.trainers, {});
  assert.deepEqual(groups.filter((group) => set.matches(group, { evTraining: "yes" }, context)).map((group) => group.id), [8, 249]);
  assert.deepEqual(groups.filter((group) => set.matches(group, { class: "trainer_classes:57" }, context)).map((group) => group.id), [1, 2]);
  // a dataset without the rooms offers no EV training matches
  trainers = rooms.map((row) => ({ ...row, ev_training: false }));
  const withoutRooms = { ...context, data: { trainers } };
  assert.deepEqual(trainerGroups(trainers, source).filter((group) => set.matches(group, { evTraining: "yes" }, withoutRooms)), []);
});

test("trainer locations load map sections and the house sign through the page catalog", async () => {
  globalThis.document = { createElement: () => fakeElement(), createTextNode: (t) => ({ text: t }) };
  const requested = [];
  const elements = new Map();
  const $ = (id) => {
    if (!elements.has(id)) elements.set(id, fakeElement());
    return elements.get(id);
  };
  const parent = {
    host: { data: () => ({ trainer_difficulty: TRAINER_DIFFICULTY, trainers: [{ ...rooms[0], map: ["7.3", "14.11", "3.69"], map_section: [90, 94, 102] }] }),
      entry: async (id) => { requested.push(id); return {}; } },
    generation: 0, texts: new Map(), text: () => "", record: () => null,
    catalogId: (id) => ({ "mapsec:90": "s90", "mapsec:94": "s94", "mapsec:102": "s102" })[id] ?? id,
  };
  const view = dataRenderer(parent, $);
  view.mode = "trainers";
  assert.equal(await view.load(), true);
  for (const id of ["s90", "s94", "s102", "map:3.84/sign:2/0"]) assert.ok(requested.includes(id), id);
  assert.equal(requested.some((id) => id.startsWith("mapsec:")), false);
  delete globalThis.document;
});

test("the trainer card names every mapped location and each party member's EV yield", () => {
  globalThis.document = { createElement: () => fakeElement(), createTextNode: (t) => ({ text: t }) };
  const none = { hp: 0, attack: 0, defense: 0, speed: 0, sp_attack: 0, sp_defense: 0 };
  const species = {
    568: { slot: 568, name: "species:568", ev_yield: { ...none, speed: 1 } },
    958: { slot: 958, name: "species:958", ev_yield: { ...none, sp_attack: 2, speed: 1 } },
  };
  const parent = {
    host: { data: () => ({ trainer_difficulty: TRAINER_DIFFICULTY, trainers: rooms }), spriteSrc: (p) => `data/${p}` }, generation: 0, texts: new Map(),
    text: (id) => ({ "mapsec:90": "Dresco Town", "mapsec:94": "Fallshore City", "mapsec:102": "Route 2",
      "map:3.84/sign:2/0": "Inverse Battle House\\pEnjoy backwards battles!",
      "trainer_classes:9": "Kindler", "trainer_classes:30": "Roughneck", "trainer_classes:57": "Youngster" })[id] ?? "",
    record: (slot) => species[slot] ?? null,
  };
  const view = dataRenderer(parent, fakeElement);
  view.mode = "trainers";
  const rows = (detail) => detail.children[0].children[1].children.map((node) => node.children[0]);
  const party = [{ index: 0, species: 568, level: 15, iv: 0, item: null, moves: [1, 1, 1, 1] },
    { index: 1, species: 958, level: 15, iv: 0, item: null, moves: [1, 1, 1, 1] },
    { index: 2, species: 0, level: 0, iv: 0, item: null, moves: [0, 0, 0, 0] }];
  const daniel = view.trainerDetail({ id: 8, name: "trainer_names:8", class: "trainer_classes:9",
    battles: [{ ...rooms[0], party }] });
  assert.deepEqual(rows(daniel).slice(0, 6), ["Class", "Kindler", "Location", "Dresco Town", "EV training", "Dresco Town"]);
  const facts = (mon) => mon.children[1].children[2].children[2];
  const members = daniel.children[1].children[1].children[1].children;
  assert.deepEqual(members.map((mon) => [facts(mon).children[0].children[0], facts(mon).children[1]]),
    [["EV earned: ", "+1 Spd"], ["EV earned: ", "+2 Sp. Atk +1 Spd"], ["EV earned: ", "—"]]);
  const joey = view.trainerDetail({ id: 1, name: "trainer_names:1", class: "trainer_classes:57", battles: [rooms[2]] });
  assert.deepEqual(rows(joey).slice(0, 4), ["Class", "Youngster", "Location", "Route 2"]);
  assert.equal(rows(joey).includes("EV training"), false);
  const ben = view.trainerDetail({ id: 2, name: "trainer_names:2", class: "trainer_classes:57", battles: [rooms[3]] });
  assert.equal(rows(ben).includes("Location"), false);
  const koji = view.trainerDetail({ id: 249, name: "trainer_names:249", class: "trainer_classes:30", battles: [rooms[1]] });
  assert.deepEqual(rows(koji).slice(0, 6), ["Class", "Roughneck", "Location", "Inverse Battle House", "EV training", "Fallshore City"]);
  assert.equal(view.trainerPlaces({ battles: [{ ...rooms[1], map: ["14.12"] }] }), "Inverse Battle House");
  const multiple = { id: 8, name: rooms[0].name, class: rooms[0].class,
    battles: [{ ...rooms[0], map: ["7.3", "14.11", "14.12", "3.69"], map_section: [90, 94, 94, 102] }] };
  assert.equal(view.trainerPlaces(multiple), "Dresco Town, Inverse Battle House, Route 2");
  assert.equal(view.trainingPlaces(multiple), "Dresco Town, Fallshore City, Route 2");
  assert.equal(rows(view.trainerDetail(multiple))[3], "Dresco Town, Inverse Battle House, Route 2");
  view.filtered = [multiple];
  const location = view.row(0).children[1].children.find((node) => node.className === "dex-sub dex-trainer-location");
  assert.equal(location.children[0], "Dresco Town");
  assert.equal(view.trainerPlaces({ battles: [{ map: ["1.2", "2.3"], map_section: [7, 8] }] }),
    "Map section 7, Map section 8");
  delete globalThis.document;
});

test("location groups preserve context and filter by map, method or species", () => {
  const rows = [
    { runtime_map: "3.69", map: "h0776B58", map_section: 102, map_header: "0x776b58",
      map_error: null, variant: "night", method: "land", rod: null, species: 163 },
    { runtime_map: "3.69", map: "h0776B58", map_section: 102, map_header: "0x776b58",
      map_error: null, variant: "day", method: "water", rod: null, species: 72 },
    { runtime_map: "46.10", map: null, map_section: null, map_header: null,
      map_error: "runtime-map-unindexed", variant: "day", method: "land", rod: null, species: 1 },
  ];
  const groups = locationGroups(rows);
  assert.equal(groups.length, 2);
  assert.equal(groups[0].map, "h0776B58");
  assert.equal(groups[0].encounters.length, 2);
  assert.equal(groups[1].map_error, "runtime-map-unindexed");
  const sections = sectionGroups([...rows, { ...rows[0], runtime_map: "3.70", map: "h0776C00" }]);
  assert.deepEqual(sections.map((group) => [group.id, group.name, group.areas.map((area) => area.id)]),
    [["102", "mapsec:102", ["3.69", "3.70"]], ["46.10", null, ["46.10"]]]);
  assert.equal(filterEntries(sections, "route 2", text, "locations").length, 1);
  assert.equal(filterEntries(sections, "hoothoot", text, "locations")[0].id, "102");
  assert.equal(filterEntries(sections, "46.10", text, "locations")[0].id, "46.10");
  assert.equal(filterEntries(sections, "3.70", text, "locations")[0].id, "102");
  assert.equal(filterEntries(sections, "water", text, "locations").length, 0);
  const detailOnly = sectionGroups([], [{ runtime_map: "3.79", map: "h07AC76C", map_section: 109, map_header: "0x7ac76c" }]);
  assert.deepEqual(detailOnly.map((group) => [group.id, group.name, group.areas.map((area) => area.id)]),
    [["109", "mapsec:109", ["3.79"]]]);
  const invalid = { ...rows[2], map_error: "runtime-map-invalid" };
  assert.deepEqual(sectionGroups([invalid]), []);
  assert.deepEqual(speciesLocations([invalid], 1), []);
  assert.equal(locationGroups([invalid])[0].map_error, "runtime-map-invalid");
});

const route = [
  { runtime_map: "3.69", map: "h0776B58", map_section: 102, variant: "day", method: "land", rod: null, slot: 0, species: 163, min_level: 8, max_level: 11, percentage: 20, encounter_rate: 15 },
  { runtime_map: "3.69", map: "h0776B58", map_section: 102, variant: "day", method: "land", rod: null, slot: 1, species: 72, min_level: 8, max_level: 11, percentage: 20, encounter_rate: 15 },
  { runtime_map: "3.69", map: "h0776B58", map_section: 102, variant: "day", method: "land", rod: null, slot: 2, species: 163, min_level: 9, max_level: 12, percentage: 10, encounter_rate: 15 },
  { runtime_map: "3.69", map: "h0776B58", map_section: 102, variant: "night", method: "land", rod: null, slot: 0, species: 72, min_level: 8, max_level: 11, percentage: 100, encounter_rate: 15 },
  { runtime_map: "3.69", map: "h0776B58", map_section: 102, variant: "day", method: "fishing", rod: "old", slot: 0, species: 72, min_level: 10, max_level: 15, percentage: 70, encounter_rate: 10 },
  { runtime_map: "3.69", map: "h0776B58", map_section: 102, variant: "day", method: "fishing", rod: "super", slot: 0, species: 72, min_level: 30, max_level: 35, percentage: 100, encounter_rate: 10 },
  { runtime_map: "3.70", map: "h0776C00", map_section: 102, variant: "day", method: "water", rod: null, slot: 0, species: 72, min_level: 5, max_level: 5, percentage: 60, encounter_rate: 2 },
  { runtime_map: "46.10", map: null, map_section: null, map_error: "runtime-map-unindexed", variant: "day", method: "rock_smash", rod: null, slot: 0, species: 163, min_level: 1, max_level: 1, percentage: 100, encounter_rate: 50 },
];

test("encounter tables merge the slots of a species per method, rod and time", () => {
  const tables = encounterTables(route.slice(0, 6));
  assert.deepEqual(tables.map((table) => [table.method, table.variant, table.rate]),
    [["Grass", "day", 15], ["Grass", "night", 15], ["Old Rod", "day", 10], ["Super Rod", "day", 10]]);
  assert.deepEqual(tables[0].rows, [
    { species: 163, chance: 30, min: 8, max: 12 }, { species: 72, chance: 20, min: 8, max: 11 },
  ]);
  assert.equal(methodLabel({ method: "rock_smash" }), "Rock Smash");
  assert.equal(methodLabel({ method: "water" }), "Surfing");
  assert.equal(methodLabel({ method: "fishing", rod: "good" }), "Good Rod");
  assert.deepEqual(speciesLocations(route, 163).map((row) => [row.id, row.area, row.method, row.variant, row.min, row.max, row.chance]),
    [["102", "3.69", "Grass", "day", 8, 12, 30], ["46.10", "46.10", "Rock Smash", "day", 1, 1, 100]]);
  assert.deepEqual(speciesLocations(route, 72).map((row) => [row.area, row.method, row.chance]),
    [["3.69", "Grass", 20], ["3.69", "Grass", 100], ["3.69", "Old Rod", 70], ["3.69", "Super Rod", 100], ["3.70", "Surfing", 60]]);
  assert.deepEqual(speciesLocations(route, 1), []);
});

test("species area numbers match location cards before filtering species", () => {
  const encounters = [...route, { ...route[0], runtime_map: "3.100", species: 999 }];
  const details = [{ runtime_map: "3.9", map_section: 102 }];
  const group = sectionGroups(encounters, details).find((row) => row.id === "102");
  const view = Object.create(PokedexDataView.prototype);
  for (const slot of [72, 163, 999]) {
    for (const row of speciesLocations(encounters, slot, details).filter((row) => row.id === group.id)) {
      assert.equal(view.locationArea(group, row.area), `Area ${row.area_number}`);
    }
  }
  assert.equal(speciesLocations(encounters, 999, details)[0].area_number, 4);
});

const encounterRules = {
  evidence: true,
  land: { divisor: 1600, multiplier: 16, label: "Encounter rate: {n}%",
    note: "Base value, before rate modifiers; other conditions also affect battles." },
  water: { divisor: 1600, multiplier: 16, label: "Encounter rate: {n}%",
    note: "Base value, before rate modifiers; other conditions also affect battles." },
  rock_smash: { divisor: 1600, multiplier: 16, label: "Encounter rate: {n}%",
    note: "Base value, before rate modifiers; ability modifiers are ignored." },
  fishing: { divisor: 100, threshold: 50, label: "Bite check: {n}%",
    note: "Bite check rises to 85% for a non-Egg lead with a bite-boosting ability." },
};

test("declared triggers localize and remain separate in encounter and species tables", (context) => {
  context.after(() => setLabelSource(null));
  setLabelSource((key) => ({ text: `localized ${key}`, game: false }));
  const base = { ...route[0], method: "water" };
  const rows = [
    { ...base, method_labels: ["First flowers"] },
    { ...base, method_labels: ["Second flowers", "Surfing"] },
  ];
  assert.equal(methodLabel(rows[0]), "localized First flowers");
  const expected = ["localized First flowers", "localized Second flowers / localized Surfing"];
  assert.deepEqual(encounterTables(rows).map((table) => table.method), expected);
  assert.deepEqual(encounterTables(rows).map((table) => table.rows[0].chance), [20, 20]);
  assert.deepEqual(speciesLocations(rows, base.species).map((row) => row.method), expected);
});

test("encounter rates name their verified check and assumptions, never a universal battle chance", () => {
  const tables = encounterTables(route.slice(0, 6));
  assert.deepEqual(tables.map((table) => encounterRate(table, encounterRules).label), [
    "Encounter rate: 15%", "Encounter rate: 15%", "Bite check: 50%", "Bite check: 50%",
  ]);
  assert.match(encounterRate(tables[0], encounterRules).note, /other conditions/);
  assert.match(encounterRate(tables[0], encounterRules).note, /Base value, before rate modifiers/);
  assert.match(encounterRate(tables[2], encounterRules).note, /85%.*non-Egg lead/);
  assert.equal(encounterRate(encounterTables([route[7]])[0], encounterRules).label, "Encounter rate: 50%");
  assert.match(encounterRate(encounterTables([route[7]])[0], encounterRules).note, /ability modifiers are ignored/);
  assert.equal(encounterRate({ method_id: "other", rate: 10 }, encounterRules), null);
  assert.equal(encounterRate(tables[0]), null);
});

test("effective encounter times retain raw variants and exclusive tables", () => {
  assert.equal(variantLabel({}), "");
  assert.equal(variantLabel({ variants: [] }), "");
  const shared = { ...route[0], variants: ["day", "morning", "evening", "night"] };
  const exclusive = { ...route[3], variants: ["night"] };
  const tables = encounterTables([shared, exclusive]);
  assert.deepEqual(tables.map((table) => [table.variant, table.variants]),
    [["day", ["day", "morning", "evening", "night"]], ["night", ["night"]]]);
  assert.deepEqual(tables.map(variantLabel), ["Day / Morning / Evening / Night", "Night"]);
  assert.deepEqual(speciesLocations([shared, exclusive], 163).map((row) => row.variants),
    [["day", "morning", "evening", "night"]]);
  try {
    for (const { code, times, display } of ENCOUNTER_LABELS) {
      const labels = Object.fromEntries(["day", "morning", "Evening", "night"].map((key, i) =>
        [key, { text: times[i], game: key !== "Evening" }]));
      setUiLanguage(code);
      setLabelSource((key) => labels[key] ?? null);
      assert.equal(variantLabel(tables[0]), display);
      assert.equal(variantLabel(tables[1]), display.split(" / ").at(-1));
    }
  } finally {
    setLabelSource(null);
    setUiLanguage(null);
  }
});

test("the location card groups the areas of a section with a table per method and time", async () => {
  const fake = () => ({
    children: [], style: {}, listeners: {}, className: "", textContent: "", value: "", hidden: false,
    title: "", disabled: false, src: "",
    addEventListener(ev, fn) { this.listeners[ev] = fn; },
    setAttribute(k, v) { this[k] = v; },
    append(...kids) { this.children.push(...kids.flat()); },
    replaceChildren(...kids) { this.children = kids.flat(); },
    classList: { toggle() {} },
  });
  globalThis.document = { createElement: () => fake(), createTextNode: (t) => ({ text: t }) };
  const species = { 163: { slot: 163, name: "species:163", sprite: { front: "sprites/163-front.png" } }, 72: { slot: 72, name: "species:72" } };
  const parent = {
    host: { data: () => ({ encounters: route }), spriteSrc: (p) => `data/${p}` }, generation: 0, texts: new Map(), opened: null,
    text: (id) => ({ "mapsec:102": "Маршрут 2", "species:163": "Хутхут", "species:72": "Тентакул" })[id] ?? "",
    record: (slot) => species[slot] ?? null,
    openSpecies(slot) { this.opened = slot; },
  };
  parent.host.data = () => ({ encounter_rates: encounterRules, encounters: route, maps: [{ runtime_map: "3.69", image: "maps/3.69.png", width: 70, height: 40 }] });
  const view = dataRenderer(parent, fake);
  view.mode = "locations";
  assert.deepEqual(view.records.map((group) => group.id), ["46.10", "102"]); // by shown name, 46.10 before Маршрут 2
  view.filtered = view.records;
  assert.equal(view.row(0).children[1].children[0], "Area 46.10");
  assert.equal(crossMatches("46.10", { encounters: route }, parent.text, parent.text, "species", [], 30)
    .find((hit) => hit.kind === "locations").name, "Area 46.10");
  setLabelSource((key) => key === "Area {n}" ? { text: "Ареал", game: true } : null);
  assert.equal(view.row(0).children[1].children[0], "Ареал 46.10");
  assert.equal(crossMatches("46.10", { encounters: route }, parent.text, parent.text, "species", [], 30)
    .find((hit) => hit.kind === "locations").name, "Ареал 46.10");
  setLabelSource(null);
  assert.equal(view.record("3.70").id, "102"); // an old runtime-map link
  const detail = view.locationDetail(view.record("102"));
  assert.equal(detail.children.length, 3); // two areas and the technical record
  const area = detail.children[0];
  assert.deepEqual(area.children[0].children.map((child) => child.children[0]), ["Area 1", ""]);
  // the area picture precedes the tables.
  // an area without a picture has no img.
  assert.equal(area.children[1].className, "dex-map");
  assert.equal(area.children[1].src, "data/maps/3.69.png");
  assert.ok(area.children[1].alt.length > 0);
  assert.equal(area.children[1].width, 1120);
  assert.equal(detail.children[1].children[1].className, "dex-enc-grid");
  assert.equal(area.children.length, 3);
  assert.equal(detail.children[1].children.length, 2);
  const tables = area.children[2].children;
  assert.deepEqual(tables.map((table) => table.children[0].children[0].children[0]),
    ["Grass · Day", "Grass · Night", "Old Rod · Day", "Super Rod · Day"]);
  assert.deepEqual(tables.map((table) => table.children[0].children[1].children[0].children[0]),
    ["Encounter rate: 15%", "Encounter rate: 15%", "Bite check: 50%", "Bite check: 50%"]);
  assert.ok(tables.every((table) => table.children[0].children.length === 2));
  assert.equal(tables.some((table) => JSON.stringify(table).includes("rate 15")), false);
  const first = tables[0].children[1].children[0];
  assert.equal(first.children[0].children[0].src, "data/sprites/163-front.png");
  assert.equal(first.children[1].children[0].children[0], "Хутхут");
  first.children[1].children[0].listeners.click();
  assert.equal(parent.opened, 163);
  assert.deepEqual(first.children.slice(2).map((td) => td.children[0]), ["Lv 8–12", "30%"]);
  assert.equal(detail.children[2].className, "block dex-record");
  delete globalThis.document;
});

test("encounter help shares the page dialog and localizes each method's note", (context) => {
  const fake = () => Object.assign(fakeElement(), { focus() { document.activeElement = this; } });
  globalThis.document = { body: fake(), createElement: fake };
  const nodes = (node) => typeof node === "object" ? [node, ...(node.children ?? []).flatMap(nodes)] : [];
  const catalog = {
    "Encounter rate: {n}%": "Localized rate: {n}%",
    "Bite check: {n}%": "Localized bite: {n}%",
    [encounterRules.land.note]: "Localized base value before modifiers and other conditions.",
    [encounterRules.rock_smash.note]: "Localized base value with ability modifiers ignored.",
    [encounterRules.fishing.note]: "Localized fishing conditions.",
  };
  context.after(() => { setCatalog(null); delete globalThis.document; });
  const encounters = [...route, { ...route[7], runtime_map: "3.69", map_section: 102, map_error: null }];
  const view = dataRenderer({
    host: { data: () => ({ encounter_rates: encounterRules, encounters }) },
    texts: new Map(), text: () => "", record: () => null,
  }, fake);
  view.mode = "locations";
  for (const labels of [null, catalog]) {
    setCatalog(labels);
    const detail = view.locationDetail(view.record("102"));
    assert.ok(nodes(detail).every((node) => !node.className?.includes("dex-enc-note")));
    const controls = nodes(detail).filter((node) => node["aria-haspopup"] === "dialog");
    const tables = view.record("102").areas.flatMap((area) => encounterTables(area.encounters));
    assert.equal(controls.length, tables.length);
    for (const [index, control] of controls.entries()) {
      const rule = encounterRules[tables[index].method_id];
      const note = labels?.[rule.note] ?? rule.note;
      assert.equal(JSON.stringify(detail).includes(note), false);
      assert.equal(control.type, "button");
      assert.equal(control.children[1]["aria-hidden"], "true");
      control.focus();
      control.listeners.click();
      const [dialog] = document.body.children;
      assert.equal(dialog.open, true);
      assert.equal(document.body.children.length, 1);
      assert.equal(dialog["aria-label"], control.children[0]);
      assert.equal(dialog.children[1].children[0].children[0].children[0], control.children[0]);
      assert.equal(dialog.children[1].children[1].children[0], note);
      if (index % 3 === 0) dialog.children[0].listeners.click();
      else if (index % 3 === 1) dialog.listeners.cancel({ preventDefault() {} });
      else dialog.listeners.click({ target: dialog });
      assert.equal(dialog.open, false);
      assert.equal(document.activeElement, control);
    }
    assert.ok(controls.some((control) => control.children[0].startsWith(labels ? "Localized rate:" : "Encounter rate:")));
    assert.ok(controls.some((control) => control.children[0].startsWith(labels ? "Localized bite:" : "Bite check:")));
  }
});

test("location details render generic points, exits, items and trainer group links", () => {
  globalThis.document = { createElement: () => fakeElement(), createTextNode: (text) => ({ text }) };
  const maps = [
    { runtime_map: "3.69", map_header: "0x776b58", image: "maps/3.69.png", width: 70, height: 40 },
    { runtime_map: "3.70", map_header: "0x776c00", image: "maps/3.70.png", width: 70, height: 40 },
  ];
  const items = [
    { id: 405, name: "items:405", icon: "icons/items/405.png", taught_move: 264, sources: [
      { kind: "ground", map: "h0776B58", map_section: 102, quantity: 1,
        x: 47, y: 13, description: "Peninsula north corner", description_evidence: "owner-description-map-image" },
    ] },
    { id: 111, name: "items:111", icon: "icons/items/111.png", taught_move: null, sources: [
      { kind: "ground", map: "h07AC76C", map_section: 93, map_section_name: "Route 9", quantity: 1,
        runtime_map: "3.79", x: 19, y: 0, description: "Northwest Route 9 channel", related_sections: [102] },
    ] },
  ];
  const trainers = [{ id: 527, name: "trainer_names:527", class: "trainer_classes:65",
    map: ["3.69"], map_section: [102], party: [{ species: 163 }] }];
  const locationDetails = [{ runtime_map: "3.69", map: "h0776B58", map_section: 102, map_header: "0x776b58",
    points: [{ kind: "zygarde_cell", x: 28, y: 12, description: "Northeast fenced area",
      description_evidence: "owner-description-map-image" }],
    exits: [{ to_runtime_map: "3.80", to_map: "h07B1DF0", to_section: 93, direction: "south", alignment: 0 }] }];
  const data = { encounter_rates: encounterRules, encounters: route, maps, items, trainers, location_details: locationDetails };
  const opened = [];
  const parent = {
    host: { data: () => data, spriteSrc: (path) => `data/${path}` }, generation: 0, texts: new Map(),
    text: (id) => ({ "mapsec:102": "Auburn Waterway", "mapsec:93": "Route 9", "species:163": "Hoothoot",
      "items:405": "TM89", "items:111": "Heart Scale", "trainer_names:527": "Alice", "trainer_classes:65": "Ace Trainer" })[id] ?? "",
    record: (slot) => ({ 163: { slot: 163, name: "species:163" }, 72: { slot: 72, name: "species:72" } })[slot] ?? null,
    goTo(want) { opened.push(want); }, openLocation(id) { opened.push({ kind: "locations", id }); },
    openItem(id) { opened.push({ kind: "items", id }); }, moveLink: (id) => `move:${id}`,
  };
  const view = dataRenderer(parent, fakeElement);
  view.mode = "locations";
  const detail = view.locationDetail(view.record("102"));
  assert.equal(detail.children.length, 6);
  assert.equal(detail.children[0].children[0].children[0].children[0], "Area 1");
  assert.equal(detail.children[1].children[0].children[0].children[0], "Area 2");
  assert.equal(detail.children[0].children[1].className, "dex-map-wrap");
  assert.equal(detail.children[0].children[1].children.length, 3);
  const points = detail.children[2].children[1].children[0].children[1].children;
  assert.equal(points.length, 2);
  assert.equal(points[0].children[1].children[0], "Zygarde Cell");
  assert.equal(points[1].children[1].children[0].children[0], "Alice");
  points[1].children[1].children[0].listeners.click();
  assert.deepEqual(opened.at(-1), { kind: "trainers", id: 527 });
  const exits = detail.children[3].children[1].children[0].children[1].children;
  assert.equal(exits.length, 1);
  exits[0].children[1].children[0].listeners.click();
  assert.deepEqual(opened.at(-1), { kind: "locations", id: "93" });
  const itemRows = detail.children[4].children[1].children[0].children[1].children;
  assert.equal(itemRows.length, 2);
  assert.equal(itemRows[0].children[2].children[2].children[1].children[0], "Peninsula north corner");
  itemRows[0].children[0].children[0].listeners.click();
  assert.deepEqual(opened.at(-1), { kind: "items", id: 405 });
  itemRows[1].children[1].children[0].listeners.click();
  assert.deepEqual(opened.at(-1), { kind: "locations", id: "93" });
  assert.match(JSON.stringify(detail.children[5]), /3\.69/);
  assert.match(JSON.stringify(detail.children[5]), /h0776B58/);
  delete globalThis.document;
});

test("pickups mark their kind on the map, fill the item table and give their sections pages", () => {
  globalThis.document = { createElement: () => fakeElement(), createTextNode: (text) => ({ text }) };
  const maps = [
    { runtime_map: "3.69", map_header: "0x776b58", image: "maps/3.69.png", width: 70, height: 40 },
    { runtime_map: "6.2", map_header: "0x000200", image: "maps/6.2.png", width: 20, height: 20 },
    { runtime_map: "7.1", map_header: "0x000100", image: "maps/7.1.png", width: 15, height: 10 },
  ];
  const place = { map: "h0776B58", map_section: 102, runtime_map: "3.69", quantity: 1 };
  const items = [
    { id: 306, name: "items:306", icon: "icons/items/306.png", taught_move: null, sources: [
      { kind: "gift", ...place, x: 49, y: 26 }, { kind: "gift", map: "7.1", map_section: 120, quantity: 1 },
      { kind: "ground", map: "6.2", runtime_map: "6.2", map_section: 120, quantity: 1, x: 2, y: 3 }] },
    { id: 588, name: "items:588", icon: "icons/items/588.png", taught_move: null, sources: [
      { kind: "hidden", ...place, x: 27, y: 7 }, { kind: "shop", map: "7.2", map_section: 121 }] },
  ];
  const locationMaps = [
    { map: "7.1", map_section: 120, map_header: "0x000100", map_type: 8, kind: "interior",
      name_rule: "map-section", name_evidence: "synthetic section and type" },
    { map: "6.2", runtime_map: "6.2", map_section: 120, map_header: "0x000200", map_type: 8,
      name: "Crater Pokémon Gym", name_rule: "sign", name_evidence: "synthetic gym sign" },
  ];
  const data = { encounters: route, maps, items, location_details: [], location_maps: locationMaps };
  const parent = {
    host: { data: () => data, spriteSrc: (path) => `data/${path}` }, generation: 0, texts: new Map(),
    text: (id) => ({ "mapsec:102": "Auburn Waterway", "mapsec:120": "Dresco Town", "items:306": "TM18",
      "items:588": "Misty Seed" })[id] ?? "",
    record: (slot) => ({ 163: { slot: 163, name: "species:163" }, 72: { slot: 72, name: "species:72" } })[slot] ?? null,
    goTo() {}, openLocation() {}, openItem() {}, moveLink: (id) => `move:${id}`,
  };
  const pages = sectionGroups(route, [], items).map((group) => group.id);
  assert.ok(pages.includes("120") && !pages.includes("121") && !sectionGroups(route).some((group) => group.id === "120"));
  assert.ok(!sectionGroups(route, [], items, new Set([102])).some((group) => group.id === "120"));
  assert.deepEqual(sectionGroups(route, [], items).find((group) => group.id === "120").areas, []);
  assert.deepEqual(sectionGroups(route, [], items, null, maps).find((group) => group.id === "120")
    .areas.map((area) => area.id), ["6.2", "7.1"]);
  const view = dataRenderer(parent, fakeElement);
  view.mode = "locations";
  const detail = view.locationDetail(view.record("102"));
  const wrap = detail.children[0].children[1];
  const markers = wrap.children.slice(1);
  assert.deepEqual(markers.map((marker) => marker.className).sort(),
    ["dex-map-marker dex-map-item dex-map-gift", "dex-map-marker dex-map-item dex-map-hidden"]);
  assert.ok(markers.some((marker) => marker.title === "TM18 · Gift"));
  assert.ok(markers.some((marker) => marker.title === "Misty Seed · Hidden"));
  assert.equal(markers[0].style.left, `${(49.5) * 100 / 70}%`);
  assert.equal(markers[0].style.top, `${(26.5) * 100 / 40}%`);
  const table = JSON.stringify(detail.children.find((child) => /dex-location-items/.test(child.className ?? "")));
  for (const expected of ["TM18", "Gift", "Misty Seed", "Hidden"]) assert.ok(table.includes(expected), expected);
  const pickupOnly = view.locationDetail(view.record("120"));
  const areaNodes = pickupOnly.children.slice(0, 2);
  assert.deepEqual(areaNodes.map((area) => area.children[0].children[0].children[0]),
    ["Crater Pokémon Gym", "Dresco Town · Interior"]);
  assert.deepEqual(areaNodes.map((area) => {
    const picture = area.children[1];
    return (picture.className === "dex-map" ? picture : picture.children[0]).src;
  }), ["data/maps/6.2.png", "data/maps/7.1.png"]);
  const ground = areaNodes[0].children[1].children[1];
  assert.equal(ground.className, "dex-map-marker dex-map-item dex-map-ground");
  assert.equal(ground.style.left, "12.5%");
  assert.equal(ground.style.top, "17.5%");
  const visible = JSON.stringify(pickupOnly.children.slice(0, -1));
  assert.ok(visible.includes("Dresco Town · Interior"), visible);
  assert.ok(visible.includes("Crater Pokémon Gym"), visible);
  const technical = JSON.stringify(pickupOnly.children.at(-1));
  assert.ok(technical.includes("6.2") && technical.includes("7.1"), technical);
  delete globalThis.document;
});

test("item map crops keep natural pixels and clamp at every edge", () => {
  const map = { width: 20, height: 10 };
  assert.deepEqual(itemSpotCrop(map, { x: 10, y: 5 }),
    { left: 128, top: 48, width: 80, height: 80, markerX: 40, markerY: 40 });
  for (const [x, y, left, top, markerX, markerY] of [
    [0, 0, 0, 0, 8, 8], [19, 0, 240, 0, 72, 8],
    [0, 9, 0, 80, 8, 72], [19, 9, 240, 80, 72, 72],
  ]) assert.deepEqual(itemSpotCrop(map, { x, y }), { left, top, width: 80, height: 80, markerX, markerY });
  assert.deepEqual(itemSpotCrop({ width: 2, height: 3 }, { x: 1, y: 2 }),
    { left: 0, top: 0, width: 32, height: 48, markerX: 24, markerY: 40 });
});

function itemSpotTable(context, sources) {
  const fake = () => {
    const node = fakeElement();
    node.dataset = {};
    node.style.setProperty = function (key, value) { this[key] = value; };
    node.setAttribute = (key, value) => {
      node[key] = value;
      if (key.startsWith("data-")) node.dataset[key.slice(5)] = String(value);
    };
    return node;
  };
  globalThis.document = { createElement: fake };
  context.after(() => { delete globalThis.document; setCatalog(null); });
  const map = { runtime_map: "1.1", image: "maps/1.1.png", width: 20, height: 10 };
  const data = { maps: [map, { ...map, runtime_map: "1.2", image: "maps/1.2.png" }],
    items: sources.map((source, index) => ({ id: index + 1, name: `items:${index + 1}`,
      sources: [{ kind: "ground", map: "1.1", runtime_map: "1.1", map_section: 7, ...source }] })) };
  const nodes = new Map();
  const $ = (id) => { if (!nodes.has(id)) nodes.set(id, fake()); return nodes.get(id); };
  const view = dataRenderer({ host: { data: () => data, spriteSrc: (path) => `data/${path}` },
    text: (id) => id, openLocation() {} }, $);
  const group = { id: "7", section: 7, areas: [{ id: "1.1", encounters: [] }] };
  view.mode = "locations";
  view.record = () => group;
  const detail = view.locationDetail(group);
  const section = detail.children.find((node) => node.className?.includes("dex-location-items"));
  const table = section.children[1].children[0];
  const headings = table.children[0].children[0].children;
  const rows = table.children[1].children;
  const spots = rows.flatMap((row) => row.children[3]?.children ?? []);
  section.querySelectorAll = (selector) => {
    assert.equal(selector, ".dex-item-spot-image");
    return spots;
  };
  return { view, $, section, headings, rows, spots };
}

test("item tables add labelled map cells only for positions on displayed maps", (context) => {
  setCatalog({ Map: "Localized map" });
  const { headings, rows, spots } = itemSpotTable(context, [
    { x: 10, y: 5 }, {}, { x: -1, y: 5 }, { x: 20, y: 5 }, { x: 10, y: 10 },
    { x: 1.5, y: 5 }, { x: null, y: 5 }, { x: 2, y: 2, runtime_map: "1.2" },
    { x: 2, y: 2, runtime_map: "1.3" },
  ]);
  assert.equal(headings.length, 4);
  assert.equal(headings[3].scope, "col");
  assert.equal(headings[3]["aria-label"], "Localized map");
  assert.deepEqual(headings[3].children, []);
  assert.equal(spots.length, 1);
  assert.equal(spots[0].className, "dex-item-spot-image");
  assert.equal(spots[0].role, "img");
  assert.equal(spots[0]["aria-label"], "Localized map");
  assert.equal(spots[0].style.backgroundImage, undefined);
  assert.deepEqual(spots[0].dataset, { map: "1.1", x: "10", y: "5" });
  for (const row of rows) {
    assert.equal(row.children.length, 4);
    assert.equal(row.children[3].className, "dex-item-spot");
  }
  assert.ok(rows.slice(1).every((row) => row.children[3].children.length === 0));
});

test("an item table without positions retains three columns", (context) => {
  const { view, section, headings, rows } = itemSpotTable(context, [{}, { positions: [] }]);
  assert.equal(headings.length, 3);
  assert.ok(rows.every((row) => row.children.length === 3));
  globalThis.IntersectionObserver = class { constructor() { assert.fail("No pictures to observe"); } };
  context.after(() => { delete globalThis.IntersectionObserver; });
  view.observeItemSpots(section);
  assert.equal(view.itemSpotObserver, null);
});

test("item backgrounds wait for visibility and use the first valid trigger position", (context) => {
  const { view, section, spots } = itemSpotTable(context, [
    { positions: [{ x: -1, y: 0 }, { x: 10, y: 5 }, { x: 19, y: 9 }] }, { x: 19, y: 9 },
  ]);
  let callback, observed, disconnects = 0, imagePaths = [];
  view.host.spriteSrc = (path) => { imagePaths.push(path); return `data/${path}`; };
  globalThis.IntersectionObserver = class {
    constructor(fn) { callback = fn; }
    observe(node) { observed = node; }
    disconnect() { disconnects += 1; }
  };
  context.after(() => { delete globalThis.IntersectionObserver; });
  view.observeItemSpots(section);
  assert.equal(observed, section);
  callback([{ isIntersecting: false }]);
  assert.deepEqual(imagePaths, []);
  assert.ok(spots.every((node) => node.style.backgroundImage === undefined));
  callback([{ isIntersecting: true }]);
  assert.equal(disconnects, 1);
  assert.equal(view.itemSpotObserver, null);
  assert.deepEqual(imagePaths, ["maps/1.1.png", "maps/1.1.png"]);
  for (const node of spots) {
    assert.equal(node.style.backgroundImage, 'url("data/maps/1.1.png")');
    assert.equal(node.style.backgroundSize, "320px 160px");
    assert.equal(node.style.width, "80px");
    assert.equal(node.style.height, "80px");
    assert.equal(node.children.length, 0);
  }
  assert.equal(spots[0].style.backgroundPosition, "-128px -48px");
  assert.equal(spots[0].style["--spot-x"], "40px");
  assert.equal(spots[0].style["--spot-y"], "40px");
  assert.equal(spots[1].style.backgroundPosition, "-240px -80px");
  assert.equal(spots[1].style["--spot-x"], "72px");
  assert.equal(spots[1].style["--spot-y"], "72px");
  callback([{ isIntersecting: true }]);
  assert.equal(imagePaths.length, 2);
});

test("item backgrounds render immediately without an intersection observer", (context) => {
  const { view, section, spots } = itemSpotTable(context, [{ x: 0, y: 0 }]);
  assert.equal(globalThis.IntersectionObserver, undefined);
  view.observeItemSpots(section);
  assert.equal(spots[0].style.backgroundImage, 'url("data/maps/1.1.png")');
  assert.equal(spots[0].style.backgroundPosition, "0px 0px");
  assert.equal(spots[0].style["--spot-x"], "8px");
  assert.equal(spots[0].style["--spot-y"], "8px");
});

test("item observers follow the reconciled table and disconnect on replacement and reset", (context) => {
  const { view, $, section, spots } = itemSpotTable(context, [{ x: 10, y: 5 }]);
  const observers = [];
  globalThis.IntersectionObserver = class {
    constructor(callback) { this.callback = callback; this.disconnected = false; observers.push(this); }
    observe(node) { this.target = node; }
    disconnect() { this.disconnected = true; }
  };
  context.after(() => { delete globalThis.IntersectionObserver; });
  $("x-data-content").querySelector = () => section;
  view.renderDetail("7");
  assert.equal(observers[0].target, section);
  view.renderDetail("7");
  assert.equal(observers[0].disconnected, true);
  assert.equal(observers[1].target, section);
  observers[0].callback([{ isIntersecting: true }]);
  assert.equal(spots[0].style.backgroundImage, undefined);
  view.reset();
  assert.equal(observers[1].disconnected, true);
  assert.equal(view.itemSpotObserver, null);
  observers[1].callback([{ isIntersecting: true }]);
  assert.equal(spots[0].style.backgroundImage, undefined);
});

test("a narrow card stacks an item's texts beside its picture", () => {
  const css = readFileSync(new URL("../site.css", import.meta.url), "utf8");
  assert.match(css, /^\.dex-location-items:has\(\.dex-item-spot\) \{ container-type: inline-size; \}/m);
  const narrow = css.match(/@container \(max-width: 520px\) \{\n(  \.dex-location-items:has\(\.dex-item-spot\)[^]*?)\n\}/)[1];
  assert.match(narrow, /:has\(\.dex-item-spot\) thead \{ display: none; \}/);
  assert.match(narrow, /:has\(\.dex-item-spot\) tr \{ display: grid; grid-template-columns: minmax\(0, 1fr\) auto;/);
  assert.match(narrow, /tr:has\(\.dex-item-choices\) > td:not\(\.dex-item-spot\) \{ grid-column: 1; \}/);
  assert.match(narrow, /td\.dex-item-spot \{ grid-column: 2; grid-row: 1 \/ span 3; width: auto; \}/);
  assert.match(narrow, /td\.dex-item-spot:empty \{ display: none; \}/);
  assert.doesNotMatch(css, /td\.dex-item-spot \{ grid-column: 1 \/ -1;/);
  assert.match(css, /\.dex-item-spot-image \{[^}]*background-repeat: no-repeat;/);
  assert.match(css, /\.dex-item-spot-image::after \{[^}]*left: var\(--spot-x, 50%\); top: var\(--spot-y, 50%\);/);
});

test("difficulty rules use player-facing difficulty labels", () => {
  assert.deepEqual(difficultyFields("1", TRAINER_DIFFICULTY), { label: "Vanilla" });
  for (const [mode, label] of [["0", "Difficult"], ["2", "Expert"], ["3", "Insane"]]) {
    assert.deepEqual(difficultyFields(mode, TRAINER_DIFFICULTY), { label });
  }
  assert.equal(difficultyFields("base", TRAINER_DIFFICULTY), null);
  assert.equal(difficultyFields(null, TRAINER_DIFFICULTY), null);
});

test("trainer IV controls summarize equal, mixed and invalid vectors", () => {
  assert.equal(trainerIvSummary([31, 31, 31, 31, 31, 31]), "31");
  assert.equal(trainerIvSummary([31, 30, 31, 0, 30, 31]), "0–31");
  for (const vector of [null, [], [31], [32, 31, 31, 31, 31, 31], [31, 31, 31, 31, 31, -1]]) {
    assert.equal(trainerIvSummary(vector), null);
  }
});

test("trainer IV companions validate identity and index members without fallbacks", () => {
  const data = { profile_block: "abc" };
  const doc = { format: "gen3tl-pokedex-trainer-ivs", schema: 3, profile_block: "abc", members: [
    { trainer: 700, slot: 4, contexts: { "0/1": [31, 31, 14, 0, 31, 31] } },
  ] };
  inlineTrainerIvs(data, doc);
  assert.equal(trainerIvMember(data, 700, 4), doc.members[0]);
  assert.equal(trainerIvMember(data, 513, 3), null);
  assert.throws(() => inlineTrainerIvs({ profile_block: "other" }, doc), /not the contexts/);
  assert.throws(() => inlineTrainerIvs({ profile_block: "abc" }, { format: "other" }), /unknown format/);
});

test("a card shows the battles proven for the chosen difficulty, labelled", () => {
  const battles = [
    { id: 20, difficulty: ["difficult"], base: 20 }, { id: 76, difficulty: ["difficult", "vanilla", "expert"], base: 76 },
    { id: 77, difficulty: ["insane"], base: 76 }, { id: 725, difficulty: ["vanilla"], base: 20 },
    { id: 726, difficulty: ["expert"], base: 20 }, { id: 727, difficulty: ["insane"], base: 20 },
    { id: 900, difficulty: null, base: null },
  ];
  assert.deepEqual(battlesFor(battles, "base", TRAINER_DIFFICULTY).map((b) => b.id), []);
  assert.deepEqual(battlesFor(battles, "0", TRAINER_DIFFICULTY).map((b) => b.id), [20, 76, 900]);
  assert.deepEqual(battlesFor(battles, "2", TRAINER_DIFFICULTY).map((b) => b.id), [726, 76, 900]);
  assert.deepEqual(battlesFor(battles, "3", TRAINER_DIFFICULTY).map((b) => b.id), [727, 77, 900]);
  assert.equal(difficultyLabel(battles[1], TRAINER_DIFFICULTY), "Vanilla · Difficult · Expert");
  assert.equal(difficultyLabel(battles[6], TRAINER_DIFFICULTY), "");
});

test("numeric deep links open the card of the trainer's group", () => {
  const fake = () => ({
    children: [], style: {}, listeners: {}, className: "", textContent: "", value: "", hidden: false,
    addEventListener(ev, fn) { this.listeners[ev] = fn; },
    replaceChildren(...kids) { this.children = kids.flat(); },
    classList: { toggle() {} },
  });
  const parent = {
    host: { data: () => ({ trainer_difficulty: TRAINER_DIFFICULTY, trainers: alice }) }, generation: 0,
    texts: new Map(Object.keys(alice).map((i) => [alice[i].name, { translation: "", source_text: source(alice[i].name) }])),
    text: () => "",
  };
  const view = dataRenderer(parent, fake);
  view.mode = "trainers";
  assert.equal(view.record("20").id, 20);
  assert.equal(view.record("77").id, 20); // the third battle opens Alice's card
  assert.equal(view.record("1").id, 1);
  assert.equal(view.record("7"), null);
});

test("the trainer card keeps the chosen difficulty and lays the party out as cards", async () => {
  const fake = () => ({
    children: [], style: {}, listeners: {}, className: "", textContent: "", value: "", hidden: false,
    title: "", disabled: false, selected: false, src: "",
    addEventListener(ev, fn) { this.listeners[ev] = fn; },
    setAttribute(k, v) { this[k] = v; },
    append(...kids) { this.children.push(...kids.flat()); },
    replaceChildren(...kids) { this.children = kids.flat(); },
    classList: { toggle() {} },
  });
  globalThis.document = { createElement: () => fake(), createTextNode: (t) => ({ text: t }) };
  const species = { slot: 85, name: "species:85", sprite: { front: "sprites/85-front.png" } };
  const trainerIvs = { format: "gen3tl-pokedex-trainer-ivs", schema: 3, profile_block: undefined, members: [
    { trainer: 20, slot: 0, listed: { moves: [1, 1, 1, 1] }, contexts: { "3/1": [31, 31, 31, 31, 31, 31] } },
  ] };
  const parent = {
    host: { data: () => ({ trainer_difficulty: TRAINER_DIFFICULTY, trainers: alice, trainer_ivs: trainerIvs }), spriteSrc: (p) => `data/${p}` }, generation: 0,
    texts: new Map(), opened: null,
    text: (id) => ({ "species:85": "Додрио", "trainer_classes:84": "Лидер", "items:592": "Летанит", "moves:1": "Удар" })[id] ?? "",
    record: (slot) => (slot === 85 ? species : null),
    openSpecies(slot) { this.opened = slot; },
    openItem(id) { this.openedItem = id; },
  };
  const view = dataRenderer(parent, fake);
  view.mode = "trainers";
  view.difficulty = "3";
  const group = { id: 20, name: "trainer_names:20", class: "trainer_classes:84", battles: [
    { ...alice[0], party: [{ index: 0, species: 85, level: 31, iv: 122, item: 592, moves: [1, 1, 1, 1] }, { index: 1, species: 0, level: 30, iv: 0, item: null, moves: null }] },
    alice[1],
  ] };
  const detail = view.trainerDetail(group);
  const select = detail.children[0].children[0].children[1];
  assert.deepEqual(select.children.map((option) => [option.value, option.selected]),
    [["1", false], ["0", false], ["2", false], ["3", true]]);
  assert.deepEqual(select.children.map((option) => option.children[0]),
    ["Vanilla", "Difficult", "Expert", "Insane"]);
  // one block per encounter, its difficulty variants inside
  const battle = detail.children[1];
  assert.equal(battle.className, "block dex-battle dex-encounter");
  assert.deepEqual(battle.children[0].children.map((child) => child.children[0]),
    ["Battle 1"]);
  const variantHead = battle.children[1].children[0];
  assert.equal(variantHead.className, "dex-battle-head dex-variant-head");
  assert.deepEqual(variantHead.children.map((child) => child.children[0]), ["trainer 20", "Lv 30–31", "2 Pokémon"]);
  // Unselected difficulty variants stay out.
  group.battles[1] = { ...group.battles[1], difficulty: ["expert"], base: 20 };
  group.battles[0] = { ...group.battles[0], difficulty: ["difficult", "insane"], base: 20 };
  const insane = view.trainerDetail(group);
  assert.equal(insane.children.length, 3); // rules, one encounter, technical record
  assert.deepEqual(insane.children[1].children[0].children.map((child) => child.children[0]),
    ["Battle"]);
  assert.deepEqual(insane.children[1].children[1].children[0].children.map((child) => child.children[0]),
    ["Difficult · Insane", "trainer 20", "Lv 30–31", "2 Pokémon"]);
  const party = battle.children[1].children[1];
  assert.equal(party.className, "dex-party");
  // the card: sprite, head and four move tiles.
  // head: name, level, types, IV, item.
  assert.equal(party.children.length, 2);
  const mon = party.children[0];
  assert.equal(mon.children[0].src, "data/sprites/85-front.png");
  const head = mon.children[1];
  assert.equal(head.className, "dex-mon-head");
  const title = head.children[0];
  assert.equal(title.children[0].children[0], "Додрио");
  title.children[0].click?.() ?? title.children[0].listeners.click();
  assert.equal(parent.opened, 85);
  assert.equal(title.children[1].children[0], "Lv 31");
  const facts = head.children[2];
  assert.equal(facts.children[0].children[0].children[0], "Item: ");
  assert.equal(facts.children[1].children[0].children[0], "IV: 31");
  assert.match(JSON.stringify(detail.children[3]), /0:122/);
  // the held item links to its page
  assert.equal(facts.children[0].children[1].children.at(-1), "Летанит");
  const tiles = mon.children[2];
  assert.equal(tiles.className, "dex-mon-moves-grid");
  const names = tiles.children.filter((tile) => tile.className === "dex-move-tile").map((tile) => tile.children[0].children[0]);
  assert.deepEqual(names, ["Удар", "Удар", "Удар", "Удар"]);
  const held = facts.children[0].children[1];
  assert.equal(held.title, "items:592");
  held.listeners.click();
  assert.equal(parent.openedItem, 592);
  const blank = party.children[1];
  assert.equal(blank.children[0].className, "dex-mon-blank");
  assert.equal(blank.children[1].children[0].children[0].disabled, true);
  assert.equal(detail.children[2].children[0].children[0].children[0], "Battle 2");
  assert.equal(detail.children[3].className, "block dex-record");
  delete globalThis.document;
});

test("trainer cards derive listed IVs or report unresolved context", () => {
  globalThis.document = { createElement: fakeElement, createTextNode: (text) => ({ text }) };
  const battle = { id: 700, name: "trainer_names:700", class: "trainer_classes:84", class_id: 84, ai_flags: 1,
    party_flags: 1, party_size: 1, party_offset: "0x100", offset: "0x200", party: [
      { index: 4, species: 85, level: 60, iv: 129, item: null, moves: [510, 157, 544, 632] },
    ] };
  const trainerIvs = { format: "gen3tl-pokedex-trainer-ivs", schema: 3, profile_block: undefined, members: [
    { trainer: 700, slot: 4, listed: { moves: [510, 157, 544, 632] }, contexts: {
      "0/1": [31, 31, 14, 0, 31, 31], "1/1": [31, 31, 31, 31, 31, 31],
    } },
  ] };
  const data = { trainer_difficulty: TRAINER_DIFFICULTY, trainers: [battle], trainer_ivs: trainerIvs };
  const parent = {
    host: { data: () => data, spriteSrc: (path) => path }, generation: 0, texts: new Map(),
    text: () => "", record: () => null,
  };
  const view = dataRenderer(parent, fakeElement);
  const group = { id: 700, name: battle.name, class: battle.class, battles: [battle] };
  const value = (detail) => detail.children[1].children[1].children[1].children[0]
    .children[1].children[2].children[1];
  const base = value(view.trainerDetail(group));
  assert.equal(base.children[0].children[0], "IV: 0–31");
  view.difficulty = "0";
  assert.equal(value(view.trainerDetail(group)).children[0].children[0], "IV: 0–31");
  view.difficulty = "1";
  assert.equal(value(view.trainerDetail(group)).children[0].children[0], "IV: 31");
  data.trainer_ivs = undefined;
  data.trainerIvMembers = undefined;
  assert.equal(value(view.trainerDetail(group)).children[0].children[0], "IV: ?");
  delete globalThis.document;
});

test("party facts keep item IV earned EV and ability separate", () => {
  globalThis.document = { createElement: fakeElement, createTextNode: (text) => ({ text }) };
  const russian = {
    Item: "Предмет", IV: "IV", "EV earned": "EV за победу",
    HP: "ОЖ", Atk: "Атк", Def: "Защ", "Sp. Atk": "Ос. атк", "Sp. Def": "Ос. защ", Spd: "Скор",
    "Choose a difficulty to see IVs": "Выберите сложность, чтобы увидеть IV",
  };
  setLabelSource((key) => russian[key] ? { text: russian[key], game: false } : null);
  try {
    const battle = { id: 700, name: "trainer_names:700", class: "trainer_classes:84", class_id: 84, ai_flags: 1,
      party_flags: 1, party_size: 1, party_offset: "0x100", offset: "0x200", party: [
        { index: 4, species: 85, level: 60, iv: 129, item: null, moves: [510, 157, 544, 632] },
      ] };
    const trainerIvs = { format: "gen3tl-pokedex-trainer-ivs", schema: 3, profile_block: undefined, members: [
      { trainer: 700, slot: 4, listed: { moves: [510, 157, 544, 632] }, contexts: {
        "0/1": [31, 31, 14, 0, 31, 31], "1/1": [31, 31, 31, 31, 31, 31],
      } },
    ] };
    const species = { slot: 85, name: "species:85", ev_yield: { hp: 0, attack: 2, defense: 0, speed: 0, sp_attack: 0, sp_defense: 0 } };
    const parent = {
      host: { data: () => ({ trainer_difficulty: TRAINER_DIFFICULTY, trainers: [battle], trainer_ivs: trainerIvs }), spriteSrc: (path) => path },
      generation: 0, texts: new Map(), text: () => "", record: () => species,
    };
    const view = dataRenderer(parent, fakeElement);
    const group = { id: 700, name: battle.name, class: battle.class, battles: [battle] };
    const rows = (detail) => detail.children[1].children[1].children[1].children[0]
      .children[1].children[2].children;
    assert.deepEqual(rows(view.trainerDetail(group)).map((row, index) =>
      index === 1 ? row.children[0].children[0] : row.children[0].children[0]),
      ["Предмет: ", "IV: 0–31", "EV за победу: ", "Ability: "]);
    view.difficulty = "0";
    const mixed = rows(view.trainerDetail(group));
    assert.equal(mixed[0].children[1], "—");
    assert.equal(mixed[1].children[0].children[0], "IV: 0–31");
    assert.equal(mixed[2].children[1], "+2 Атк");
    view.difficulty = "1";
    assert.equal(rows(view.trainerDetail(group))[1].children[0].children[0], "IV: 31");
    view.difficulty = "base";
    assert.equal(rows(view.trainerDetail(group))[1].children[0].children[0], "IV: 0–31");
    assert.ok(rows(view.trainerDetail(group)).every((row) => row.className.split(" ").includes("dex-mon-fact")));
    const css = readFileSync(new URL("../site.css", import.meta.url), "utf8");
    assert.match(css, /\.dex-mon-facts \{ display: grid; grid-template-columns: minmax\(0, 1fr\)/);
  } finally {
    setLabelSource(null);
    delete globalThis.document;
  }
});

function trainerStatsFixture($ = fakeElement) {
  const battle = { id: 700, name: "trainer_names:700", class: "trainer_classes:84", class_id: 84, ai_flags: 1,
    party_flags: 1, party_size: 1, party_offset: "0x100", offset: "0x200", party: [
      { index: 4, species: 1076, level: 60, iv: 129, item: null, moves: [510, 157, 544, 632] },
    ] };
  const member = { trainer: 700, slot: 4, listed: { moves: [510, 157, 544, 632] }, contexts: {
    "0/1": [31, 31, 14, 0, 31, 31], "1/1": [31, 31, 31, 31, 31, 31],
  }, evs: { 0: [252, 252, 0, 0, 0, 6], 1: [0, 0, 0, 0, 0, 0] } };
  const species = { slot: 1076, name: "species:1076",
    base_stats: { hp: 61, attack: 131, defense: 211, speed: 13, sp_attack: 53, sp_defense: 101 } };
  const data = { trainer_difficulty: TRAINER_DIFFICULTY, trainers: [battle], trainer_ivs: {
    format: "gen3tl-pokedex-trainer-ivs", schema: 3, profile_block: undefined, members: [member],
  } };
  const parent = {
    host: { data: () => data, spriteSrc: (path) => path }, generation: 0, texts: new Map(),
    text: () => "", record: (slot) => slot === species.slot ? species : null,
  };
  const view = dataRenderer(parent, $);
  const group = { id: 700, name: battle.name, class: battle.class, battles: [battle] };
  view.mode = "trainers";
  view.groups = [group];
  return { view, group, battle, member, species, data };
}

const partyDisclosures = (detail) => detail.children.slice(1, -1).flatMap((encounter) =>
  encounter.children.slice(1).flatMap((variant) => variant.children[1].children.map((mon) =>
    mon.children[1].children[2].children.find((node) => node.className?.includes("dex-party-stats")))));

const partyStatValues = (disclosure) => disclosure.children[1].children[1].children.map((row) =>
  row.children.map((cell) => cell.children[0]));

function togglePartyStats(disclosure, open) {
  disclosure.open = open;
  disclosure.listeners.toggle({ currentTarget: disclosure });
}

test("party ability facts distinguish fixed, possible, absent and missing contexts", (context) => {
  globalThis.document = { createElement: (tag) => ({ ...fakeElement(), tag }) };
  context.after(() => { setCatalog(null); delete globalThis.document; });
  const opened = [];
  const view = { view: { abilityLink(species, id) {
    const button = fakeElement();
    button.append(`Ability ${id}`);
    button.listeners.click = () => opened.push([species, id]);
    return button;
  } } };
  const mon = { species: 8, abilities: { "0/1": [10], "1/1": [10, 20], "2/1": [], "0/0": [30] } };
  for (const labels of [null, { "Possible abilities": "Localized alternatives", "Abilities unavailable": "Localized unavailable",
    "Not in party on this difficulty": "Localized absent" }]) {
    setCatalog(labels);
    const render = (mode, kind = "world") => PokedexDataView.prototype.partyAbility.call(view, mon, { kind }, mode);
    assert.equal(render(0).children[0].children[0], "Ability: ");
    const possible = render(1);
    assert.equal(possible.children[0].children[0], `${labels?.["Possible abilities"] ?? "Possible abilities"}: `);
    possible.children.filter((node) => node.listeners?.click).forEach((node) => node.listeners.click());
    assert.deepEqual(opened.slice(-2), [[8, 10], [8, 20]]);
    assert.equal(render(2).children[1], labels?.["Not in party on this difficulty"] ?? "Not in party on this difficulty");
    assert.equal(render(null).children[1], labels?.["Abilities unavailable"] ?? "Abilities unavailable");
    assert.equal(render(0, "partners").children[1].children[0], "Ability 30");
  }
});

test("trainer cards open on the declared difficulty with IVs and keep raw variants technical", () => {
  globalThis.document = { createElement: (tag) => ({ ...fakeElement(), tag }), createTextNode: (text) => ({ text }) };
  try {
    const textOf = (nodes) => nodes.map((node) => typeof node === "string" ? node : textOf(node?.children ?? [])).join(" ");
    const { view, group, battle, data } = trainerStatsFixture();
    battle.difficulty = [TRAINER_DIFFICULTY.settings[0]];
    const alternate = { ...battle, id: 701, base: battle.id, difficulty: [TRAINER_DIFFICULTY.settings[1]],
      party: [{ ...battle.party[0], level: 22, iv: 77 }] };
    group.battles.push(alternate);
    const initial = view.trainerDetail(group);
    const select = initial.children[0].children[0].children[1];
    assert.deepEqual(select.children.map((option) => option.value), TRAINER_DIFFICULTY.order.map(String));
    assert.deepEqual(select.children.filter((option) => option.selected).map((option) => option.value), ["0"]);
    assert.equal(partyDisclosures(initial)[0].children[0].children[0], "IV: 0–31");
    assert.doesNotMatch(textOf([initial.children[0]]),
      /Level scaling|IV|Trained EVs|Held items and moves|Party for the selected|Scales with badges/);
    assert.match(textOf([initial.children[0]]), /Difficulty.*Class/);
    assert.doesNotMatch(textOf(initial.children.slice(0, -1)), /Stored|\bdata\b|trainer 701|Lv 22/);
    assert.match(textOf([initial.children.at(-1)]), /Stored levels.*4:22/);
    assert.match(textOf([initial.children.at(-1)]), /Stored IV\/spread.*4:77/);
    data.trainer_difficulty = { ...TRAINER_DIFFICULTY, default: 1, order: [3, 2, 1, 0],
      labels: ["Tier A", "Tier B", "Tier C", "Tier D"] };
    const changed = view.trainerDetail(group);
    const changedSelect = changed.children[0].children[0].children[1];
    assert.deepEqual(changedSelect.children.map((option) => option.children[0]), ["Tier D", "Tier C", "Tier B", "Tier A"]);
    assert.deepEqual(changedSelect.children.filter((option) => option.selected).map((option) => option.value), ["1"]);
    assert.match(textOf(changed.children.slice(0, -1)), /trainer 701.*Lv 22/);
    delete data.trainer_difficulty;
    const technical = view.trainerDetail(group);
    assert.equal(partyDisclosures(technical).length, 0);
    assert.doesNotMatch(JSON.stringify(technical.children.slice(0, -1)), /dex-difficulty/);
    assert.match(textOf([technical.children.at(-1)]), /Stored levels.*4:22/);
  } finally {
    delete globalThis.document;
  }
});

test("native party disclosures show base stats and engine IVs in species card order", () => {
  const body = fakeElement();
  globalThis.document = { body, createElement: (tag) => ({ ...fakeElement(), tag }), createTextNode: (text) => ({ text }) };
  try {
    const { view, group } = trainerStatsFixture();
    view.difficulty = "0";
    const [disclosure] = partyDisclosures(view.trainerDetail(group));
    assert.equal(disclosure.tag, "details");
    assert.equal(disclosure.open, false);
    assert.equal(disclosure.children[0].tag, "summary");
    assert.equal(disclosure.children[0].children[0], "IV: 0–31");
    assert.deepEqual(Object.keys(disclosure.children[0].listeners), []);
    const table = disclosure.children[1];
    assert.equal(table.tag, "table");
    assert.deepEqual(table.children[0].children[0].children.map((cell) => [cell.children[0], cell.scope]),
      [["Base stats", "col"], ["Base", "col"], ["EV", "col"], ["IV", "col"]]);
    assert.deepEqual(partyStatValues(disclosure), [
      ["HP", "61", "252", "31"], ["Attack", "131", "252", "31"], ["Defense", "211", "0", "14"],
      ["Sp. Attack", "53", "0", "31"], ["Sp. Defense", "101", "6", "31"], ["Speed", "13", "0", "0"],
    ]);
    assert.ok(table.children[1].children.every((row) => row.children[0].scope === "row"));
    assert.deepEqual(table.children[2].children[0].children.map((cell) => cell.children[0]), ["Total", "570", "510", "—"]);
    assert.equal(disclosure.children.length, 2);
    togglePartyStats(disclosure, true);
    assert.equal(body.children.length, 0);
    assert.equal(PokedexView.prototype.openTrainerIvs, undefined);
    view.difficulty = "1";
    const [equal] = partyDisclosures(view.trainerDetail(group));
    assert.equal(equal.children[0].children[0], "IV: 31");
    assert.ok(partyStatValues(equal).every((row) => row[3] === "31"));
    assert.equal(equal.children.length, 2);
  } finally {
    delete globalThis.document;
  }
});

test("party EV columns follow the selected difficulty, conditions and removed members", (context) => {
  globalThis.document = { createElement: (tag) => ({ ...fakeElement(), tag }), createTextNode: (text) => ({ text }) };
  context.after(() => { delete globalThis.document; });
  const { view, group, member, data } = trainerStatsFixture();
  data.trainer_ivs.ev_conditions = [{ flag: "0x821", set: "After the second badge", clear: "Before the second badge" },
    { flag: "0x9F3", set: "With a battle partner", clear: "Without a battle partner" }];
  data.trainer_ivs.ev_places = { "1.79": 137 };
  view.view.text = (id) => ({ "mapsec:137": "League" })[id] ?? "";
  member.evs[3] = { alternatives: [
    { evs: [0, 0, 0, 0, 0, 0], when: { "0x821": [0] } },
    { evs: [0, 252, 0, 252, 0, 0], when: { "0x821": [1], "0x9F3": [1] } },
    { evs: [252, 252, 252, 252, 252, 252], when: { place: ["1.79"] } },
    { evs: null, when: { place: ["14.3", "other"] } },
  ] };
  view.difficulty = "3";
  const [disclosure] = partyDisclosures(view.trainerDetail(group));
  const table = disclosure.children[1];
  assert.deepEqual(table.children[0].children[0].children.slice(2).map((cell) => cell.children[0]),
    ["EV 1", "EV 2", "EV 3", "EV 4", "IV"]);
  assert.deepEqual(partyStatValues(disclosure)[1], ["Attack", "131", "0", "252", "252", "—", "—"]);
  assert.deepEqual(table.children[2].children[0].children.slice(2).map((cell) => cell.children[0]), ["0", "504", "1512", "—", "—"]);
  assert.deepEqual(disclosure.children.slice(2).map((node) => node?.children?.[0]).filter(Boolean), [
    "IVs unavailable", "EV 1: Before the second badge", "EV 2: After the second badge, With a battle partner",
    "EV 3: Battle in League", "EV 4: Battle elsewhere"]);
  member.evs[2] = null;
  view.difficulty = "2";
  const [removed] = partyDisclosures(view.trainerDetail(group));
  assert.ok(partyStatValues(removed).every((row) => row[2] === "—"));
  assert.equal(removed.children.at(-1).children[0], "Not in party on this difficulty");
});

test("species alternatives retain conditions when EV vectors coincide", (context) => {
  globalThis.document = { createElement: (tag) => ({ ...fakeElement(), tag }), createTextNode: (text) => ({ text }) };
  context.after(() => { setCatalog(null); delete globalThis.document; });
  const { view, group, member, data, species } = trainerStatsFixture();
  const replacement = { ...species, slot: 999, name: "Replacement" };
  view.view.record = (slot) => [species, replacement].find((row) => row.slot === slot);
  view.view.speciesName = (record) => record === species ? "Original" : "Replacement";
  const opened = [];
  view.view.openSpecies = (slot) => opened.push(slot);
  member.species_variants = [{ species: species.slot, condition: "before" }, { species: 999, condition: "after" }];
  data.trainer_ivs.species_conditions = [
    { id: "before", status: "known", label: "Before unlocking" },
    { id: "after", status: "known", label: "After unlocking" },
  ];
  setCatalog({ "Possible species": "Localized species", "Before unlocking": "Localized before",
    "After unlocking": "Localized after", "{species}: {condition}": "{condition} — {species}" });
  const facts = view.partySpecies(member);
  assert.equal(facts.children[0].children[0], "Localized species: ");
  assert.deepEqual(facts.children.slice(1).map((row) => row.children[1]), [": Localized before", ": Localized after"]);
  facts.children.slice(1).forEach((row) => row.children[0].listeners.click());
  assert.deepEqual(opened, [species.slot, 999]);
  assert.equal(view.evCondition({ species: [species.slot, 999] }, member),
    "Localized before — Original / Localized after — Replacement");
  view.difficulty = "1";
  const [disclosure] = partyDisclosures(view.trainerDetail(group));
  assert.equal(disclosure.children[1].children[0].children[0].children.length, 4);
  assert.ok(JSON.stringify(view.trainerDetail(group), (key, value) => key === "listeners" ? undefined : value)
    .includes("Localized after"));
  data.trainer_ivs.species_conditions[1].status = "unknown";
  data.trainer_ivs.species_conditions[1].searched = "Selector and setter references";
  assert.equal(view.speciesCondition(member.species_variants[1]), "Unknown species condition");
  assert.equal(view.evCondition({ species: [1234] }, member), "Unknown species condition — species:1234");
});

test("built species rules show the outcomes of the selected difficulty", (context) => {
  globalThis.document = { createElement: (tag) => ({ ...fakeElement(), tag }), createTextNode: (text) => ({ text }) };
  context.after(() => { setCatalog(null); delete globalThis.document; });
  const { view, member, data, species } = trainerStatsFixture();
  const cores = [999, 1000].map((slot) => ({ ...species, slot, name: `Core ${slot}` }));
  view.view.record = (slot) => [species, ...cores].find((row) => row.slot === slot);
  view.view.speciesName = (record) => record.slot === species.slot ? "Evolved" : record.name;
  view.view.text = (id) => id === "trainer_names:700" ? "Rival" : "";
  member.species_changes = [
    { species: species.slot, rule: "level", level: 36, difficulties: [2, 3] },
    { species: 999, rule: "core", difficulties: [0, 1, 2, 3] },
    { species: 1000, rule: "core", difficulties: [0, 1, 2, 3] },
  ];
  data.trainer_ivs.species_rules = [
    { id: "level", status: "known", label: "Against {trainer}, level {level}", opponent: 700 },
    { id: "core", status: "known", label: "Random core" },
  ];
  setCatalog({ "Possible species": "Species", "Against {trainer}, level {level}": "{trainer} {level}",
    "Random core": "Random" });
  const expert = view.partySpecies(member, "2");
  assert.equal(expert.children[0].children[0], "Species: ");
  assert.deepEqual(expert.children.slice(1).map((row) => row.children.at(-1)), [": Rival 36", ": Random"]);
  assert.deepEqual(expert.children[2].children.map((node) => node.children?.[0] ?? node),
    ["Core 999", " / ", "Core 1000", ": Random"]);
  const vanilla = view.partySpecies(member, "1");
  assert.deepEqual(vanilla.children.slice(1).map((row) => row.children.at(-1)), [": Random"]);
  data.trainer_ivs.species_rules[1].status = "unknown";
  assert.equal(view.speciesRule(member.species_changes[1]), "Unknown species condition");
  delete member.species_changes;
  assert.equal(view.partySpecies(member, "2"), null);
});

test("default moves expand only differing level bands and preserve the listed view", (context) => {
  globalThis.document = { createElement: (tag) => ({ ...fakeElement(), tag }), createTextNode: (text) => ({ text }) };
  context.after(() => { setCatalog(null); delete globalThis.document; });
  const { view, member, battle } = trainerStatsFixture();
  view.view.moveLink = (id) => `move ${id}`;
  const mon = battle.party[0];
  mon.moves = null;
  mon.level = 15;
  member.listed.level = 15;
  member.listed.moves = [1, 2];
  member.move_bands = [{ level: 1, last: 9, moves: [1] }, { level: 10, last: 20, moves: [1, 2] },
    { level: 21, last: 21, moves: [2, 3] }, { level: 22, moves: [3, 4] }];
  member.level_ranges = { "0": { min: 15, max: 93 }, "1": { min: 15, max: 15 },
    "2": { min: 15, max: 93 }, "3": { min: 15, max: 98 } };
  setCatalog({ "Default moves": "Defaults", "Lv {n}": "Lv. {n}" });
  for (const mode of ["0", "2", "3"]) {
    const card = view.partyMember(mon, null, battle, mode);
    const grid = card.children[2];
    assert.deepEqual(grid.children.slice(0, -1).map((tile) => tile.children[0]), ["move 1", "move 2"]);
    const disclosure = grid.children.at(-1);
    assert.equal(disclosure.tag, "details");
    assert.ok(!disclosure.open);
    assert.equal(disclosure.children[0].children[0], "Defaults");
    const bands = disclosure.children[1].children;
    assert.deepEqual(bands.map((band) => band.children[0].children[0]),
      ["Lv. 21", `Lv. 22–${member.level_ranges[mode].max}`]);
    assert.deepEqual(bands.map((band) => band.children[1].children.map((tile) => tile.children[0])),
      [["move 2", "move 3"], ["move 3", "move 4"]]);
  }
  assert.equal(view.partyMember(mon, null, battle, "1").children[2].children.at(-1).tag, "span");
  member.move_bands = [{ level: 1, last: 20, moves: [3] }, { level: 21, last: 94, moves: [1, 2] },
    { level: 95, moves: [4] }];
  const clipped = view.partyDefaultMoves(member, [1, 2], "2").children[1].children;
  assert.deepEqual(clipped.map((band) => band.children[0].children[0]), ["Lv. 15–20"]);
  member.move_bands[0].last = 14;
  member.move_bands[1].level = 15;
  assert.equal(view.partyDefaultMoves(member, [1, 2], "2").tag, "span");
  assert.equal(view.partyDefaultMoves(member, [1, 2], null).children[1].children[0].children[0].children[0], "Lv. 95–98");
  member.move_bands = [{ level: 1, moves: [1, 2] }];
  assert.equal(view.partyMember(mon, null, battle, "2").children[2].children.at(-1).tag, "span");
  delete member.move_bands;
  assert.equal(view.partyMember(mon, null, battle, "2").children[2].children.at(-1).tag, "span");
  mon.moves = [4, 3];
  member.move_bands = [{ level: 1, moves: [1, 2] }];
  assert.deepEqual(view.partyMember(mon, null, battle, "2").children[2].children.map((tile) => tile.children[0]),
    ["move 4", "move 3"]);
});

test("unconditional member writes replace head values only on their difficulty", (context) => {
  globalThis.document = { createElement: (tag) => ({ ...fakeElement(), tag }), createTextNode: (text) => ({ text }) };
  context.after(() => { delete globalThis.document; });
  const { view, member, data, battle } = trainerStatsFixture();
  data.trainer_ivs.difficulties = TRAINER_DIFFICULTY.settings;
  view.itemLink = (id) => `item ${id}`;
  view.view.moveLink = (id) => `move ${id}`;
  const mon = battle.party[0];
  mon.item = 8;
  member.write_changes = [
    { level: 1, difficulties: [2], when: {}, moves: [1, 9, 3, 0], item: 7, rules: ["first"] },
    { level: 1, last: 100, difficulties: [3], when: {}, moves: [4, 0, 0, 0], item: 0, rules: ["second"] },
  ];
  const before = structuredClone({ member, mon });
  for (const mode of ["2", "3", "0", "1", "2", null]) {
    const card = view.partyMember(mon, null, battle, mode);
    const write = member.write_changes.find((row) => mode != null && row.difficulties.includes(Number(mode)));
    const expected = { ...mon, item: write?.item ?? mon.item, moves: write ? write.moves.filter(Boolean) : mon.moves };
    const listed = view.partyMember({ ...expected, index: 99 }, null, battle, mode);
    assert.equal(JSON.stringify(card.children[1].children[2].children[0]), JSON.stringify(listed.children[1].children[2].children[0]));
    assert.equal(JSON.stringify(card.children[2]), JSON.stringify(listed.children[2]));
    assert.equal(card.children.filter((node) => node.className === "dex-party-species").length, mode == null ? 1 : 0);
  }
  assert.deepEqual({ member, mon }, before);
  mon.moves = null;
  member.move_bands = [{ level: 1, moves: [5] }];
  member.level_ranges = { "2": { min: 1, max: 100 } };
  const grid = view.partyMember(mon, null, battle, "2").children[2];
  assert.deepEqual(grid.children.map((tile) => tile.children[0]), ["move 1", "move 9", "move 3"]);
});

test("member writes with remaining conditions retain the listed head and disclosure", (context) => {
  globalThis.document = { createElement: (tag) => ({ ...fakeElement(), tag }), createTextNode: (text) => ({ text }) };
  context.after(() => { delete globalThis.document; });
  const { view, member, data, battle } = trainerStatsFixture();
  data.trainer_ivs.difficulties = TRAINER_DIFFICULTY.settings;
  view.itemLink = (id) => `item ${id}`;
  view.view.moveLink = (id) => `move ${id}`;
  const mon = battle.party[0];
  const listed = view.partyMember(mon, null, battle, "2");
  for (const condition of [{ level: 2 }, { last: 99 }, { when: { "0x100": [0] } },
    { when: { "0x5000": [1] } }, { when: { opponent: [17] } }, { when: { species: [mon.species] } }]) {
    member.write_changes = [{ level: 1, difficulties: [2], when: {}, moves: [1, 9, 3, 0],
      item: 7, rules: ["branch"], ...condition }];
    const card = view.partyMember(mon, null, battle, "2");
    assert.equal(JSON.stringify(card.children[1].children[2].children[0]), JSON.stringify(listed.children[1].children[2].children[0]));
    assert.equal(JSON.stringify(card.children[2]), JSON.stringify(listed.children[2]));
    const disclosureGroup = card.children.find((node) => node.className === "dex-party-species");
    if (view.evCondition(condition.when, member) === null) {
      assert.equal(disclosureGroup, undefined);
      assert.equal(view.technicalConditions(battle).length, 1);
      continue;
    }
    const disclosure = disclosureGroup.children[0];
    assert.equal(disclosure.tag, "details");
    assert.equal(disclosure.children[1].children[0].children.at(-1), "item 7");
    assert.deepEqual(disclosure.children[1].children[1].children.map((tile) => tile.children[0]),
      ["move 1", "move 9", "move 3"]);
  }
});

test("writes inside a species outcome stay under its condition", (context) => {
  globalThis.document = { createElement: (tag) => ({ ...fakeElement(), tag }), createTextNode: (text) => ({ text }) };
  context.after(() => { delete globalThis.document; });
  const { view, member, data, battle } = trainerStatsFixture();
  data.trainer_ivs.difficulties = TRAINER_DIFFICULTY.settings;
  view.itemLink = (id) => `item ${id}`;
  view.view.moveLink = (id) => `move ${id}`;
  const mon = battle.party[0];
  const listed = view.partyMember(mon, null, battle, "2");
  member.species_changes = [{ species: 999, level: 30, difficulties: [2], abilities: { 2: [] },
    write_changes: [{ level: 1, difficulties: [2], when: {}, moves: [1], item: 7, rules: ["branch"] }] }];
  const card = view.partyMember(mon, null, battle, "2");
  assert.equal(JSON.stringify(card.children[1].children[2].children[0]), JSON.stringify(listed.children[1].children[2].children[0]));
  assert.equal(JSON.stringify(card.children[2]), JSON.stringify(listed.children[2]));
  const species = card.children.at(-1).children[1];
  assert.equal(species.tag, "details");
  const write = species.children[1].children.find((node) => node.className === "dex-party-species").children[0];
  assert.equal(write.tag, "details");
  assert.equal(write.children[1].children[0].children.at(-1), "item 7");
  assert.equal(write.children[1].children[1].children[0].children[0], "move 1");
  assert.equal(write.children[0].children[0], "Expert, Lv 30+");
});

test("member writes split at evolution thresholds on each difficulty", (context) => {
  globalThis.document = { createElement: (tag) => ({ ...fakeElement(), tag }), createTextNode: (text) => ({ text }) };
  context.after(() => { delete globalThis.document; });
  const { view, member, data, battle } = trainerStatsFixture();
  data.trainer_ivs.difficulties = TRAINER_DIFFICULTY.settings;
  data.trainer_ivs.species_rules = [{ id: "evolution", status: "known", kind: "scaled_evolution",
    label: "Level {level} or higher", labels: { range: "Level {level} to {last}" } }];
  view.itemLink = (id) => `item ${id}`;
  view.view.moveLink = (id) => `move ${id}`;
  const mon = battle.party[0];
  member.write_changes = [{ level: 20, last: 60, difficulties: [2, 3], when: {},
    moves: [1, 9, 3, 0], item: 7, rules: ["branch"] }];
  member.species_changes = [
    { species: 999, level: 30, last: 39, difficulties: [2], abilities: { 2: [] }, rule: "evolution" },
    { species: 1000, level: 40, difficulties: [2], abilities: { 2: [] }, rule: "evolution" },
    { species: 1000, level: 50, difficulties: [3], abilities: { 3: [] }, rule: "evolution" },
  ].map((change) => ({ ...change, write_changes: member.write_changes.map((row) => ({ ...row, difficulties: change.difficulties })) }));
  const before = structuredClone(member);
  const disclosures = (card) => [
    ...card.children.find((node) => node.className === "dex-party-species")?.children ?? [],
    ...card.children.at(-1).children.slice(1).flatMap((outcome) =>
      outcome.children[1].children.find((node) => node.className === "dex-party-species")?.children ?? []),
  ];
  for (const [mode, expected] of [["2", ["Expert, Lv 20–29", "Expert, Lv 30–39", "Expert, Lv 40–60"]],
    ["3", ["Insane, Lv 20–49", "Insane, Lv 50–60"]]]) {
    const rows = disclosures(view.partyMember(mon, null, battle, mode));
    assert.deepEqual(rows.map((row) => row.children[0].children[0]), expected);
    for (const row of rows) {
      assert.equal(row.children[1].children[0].children.at(-1), "item 7");
      assert.deepEqual(row.children[1].children[1].children.map((tile) => tile.children[0]),
        ["move 1", "move 9", "move 3"]);
    }
  }
  assert.equal(view.partyWrites(member, "0"), null);
  assert.deepEqual(view.partyWrites(member, null).children.map((row) => row.children[0].children[0]),
    ["Expert, Lv 20–29", "Insane, Lv 20–49"]);
  assert.deepEqual(member, before);
});

test("writes wholly within one form appear only on that form", (context) => {
  globalThis.document = { createElement: (tag) => ({ ...fakeElement(), tag }), createTextNode: (text) => ({ text }) };
  context.after(() => { delete globalThis.document; });
  const { view, member, data, battle } = trainerStatsFixture();
  data.trainer_ivs.difficulties = TRAINER_DIFFICULTY.settings;
  data.trainer_ivs.species_rules = [{ id: "evolution", status: "known", kind: "scaled_evolution", label: "Level {level} or higher" }];
  view.itemLink = (id) => `item ${id}`;
  view.view.moveLink = (id) => `move ${id}`;
  member.write_changes = [
    { level: 20, last: 29, difficulties: [2], when: {}, moves: [1], item: 7, rules: ["early"] },
    { level: 53, difficulties: [2], when: {}, moves: [2], item: 8, rules: ["late"] },
  ];
  member.species_changes = [{ species: 999, level: 30, difficulties: [2], abilities: { 2: [] },
    rule: "evolution", write_changes: structuredClone(member.write_changes) }];
  const card = view.partyMember(battle.party[0], null, battle, "2");
  const base = card.children.find((node) => node.className === "dex-party-species").children;
  const outcome = card.children.at(-1).children[1].children[1].children
    .find((node) => node.className === "dex-party-species").children;
  assert.deepEqual(base.map((row) => row.children[0].children[0]), ["Expert, Lv 20–29"]);
  assert.deepEqual(outcome.map((row) => row.children[0].children[0]), ["Expert, Lv 53+"]);
  assert.equal(base[0].children[1].children[0].children.at(-1), "item 7");
  assert.equal(outcome[0].children[1].children[0].children.at(-1), "item 8");
  member.write_changes.shift();
  assert.equal(view.partyWrites(member, "2"), null);
});

test("evolution clipping keeps conditional outcomes and head writes distinct", (context) => {
  globalThis.document = { createElement: (tag) => ({ ...fakeElement(), tag }), createTextNode: (text) => ({ text }) };
  context.after(() => { delete globalThis.document; });
  const { view, member, data, battle } = trainerStatsFixture();
  data.trainer_ivs.difficulties = TRAINER_DIFFICULTY.settings;
  data.trainer_ivs.species_rules = [{ id: "evolution", status: "known", kind: "scaled_evolution", label: "Level {level} or higher" },
    { id: "opponent", status: "known", kind: "trainer_level", opponent: 17, label: "Level {level} against {trainer}" }];
  view.itemLink = (id) => `item ${id}`;
  view.view.moveLink = (id) => `move ${id}`;
  member.write_changes = [{ level: 1, difficulties: [2, 3], when: {}, moves: [9], item: 7, rules: ["branch"] }];
  const change = { species: 999, level: 30, difficulties: [2], abilities: { 2: [] }, rule: "evolution" };
  member.species_changes = [change];
  const card = view.partyMember(battle.party[0], null, battle, "2");
  assert.deepEqual(card.children[2].children.map((tile) => tile.children[0]), battle.party[0].moves.map((id) => `move ${id}`));
  assert.equal(card.children.find((node) => node.className === "dex-party-species").children[0].children[0].children[0],
    "Expert, Lv 1–29");
  assert.equal(view.partyWrites(member, "3").children[0].children[0].children[0], "Insane");
  for (const condition of [{ badges: 3 }, { battle: "rematch" }, { from: 998 }, { rule: "opponent" }]) {
    member.species_changes = [{ ...change, ...condition }];
    assert.equal(view.partyWrites(member, "2").children[0].children[0].children[0], "Expert · Insane");
  }
});

test("member writes show only selected conditions, including cleared items", (context) => {
  globalThis.document = { createElement: (tag) => ({ ...fakeElement(), tag }), createTextNode: (text) => ({ text }) };
  context.after(() => { setCatalog(null); delete globalThis.document; });
  const { view, member, data, battle } = trainerStatsFixture();
  data.trainer_ivs.difficulties = ["difficult", "vanilla", "expert", "insane"];
  data.trainer_difficulty = TRAINER_DIFFICULTY;
  data.trainer_ivs.ev_conditions = [{ flag: "0x100", set: "After badge", clear: "Before badge" }];
  view.itemLink = (id) => `item ${id}`;
  view.moveTile = (id) => `move ${id}`;
  member.write_changes = [{ level: 31, last: 39, difficulties: [2], when: { "0x100": [0] },
    moves: [1, 9, 3, 0], item: 0, rules: ["branch"] },
  { level: 40, difficulties: [3], when: {}, moves: [1, 8, 3, 4], item: 7, rules: ["branch"] }];
  const before = structuredClone(member);
  const rows = view.partyWrites(member, "2").children;
  assert.equal(rows.length, 1);
  assert.equal(rows[0].tag, "details");
  const [summary, body] = rows[0].children;
  assert.match(summary.children[0], /Expert.*31–39.*Before badge/);
  assert.equal(body.children[0].children.at(-1), "—");
  assert.deepEqual(body.children[1].children, ["move 1", "move 9", "move 3"]);
  assert.equal(view.partyWrites(member, "3").children[0].children[1].children[0].children.at(-1), "item 7");
  assert.equal(view.partyWrites(member, "1"), null);
  assert.equal(view.partyWrites({}, "2"), null);
  assert.equal(view.partyWrites(member, null).children.length, 2);
  assert.ok(view.partyMember(battle.party[0], null, battle, "2").children.some((node) =>
    node.className === "dex-party-species"));
  assert.deepEqual(member, before);
  member.write_changes = [{ level: 1, difficulties: [2], when: {}, moves: [1], item: 7, rules: ["branch"] }];
  const every = view.partyWrites(member, "2").children[0].children[0].children[0];
  assert.match(every, /Expert/);
  assert.doesNotMatch(every, /Lv|\d/);
});

test("a member's variant item shows under its named condition, not in technical data", (context) => {
  globalThis.document = { createElement: (tag) => ({ ...fakeElement(), tag }), createTextNode: (text) => ({ text }) };
  context.after(() => { setCatalog(null); delete globalThis.document; });
  const { view, member, data, battle } = trainerStatsFixture();
  data.trainer_ivs.difficulties = ["difficult", "vanilla", "expert", "insane"];
  data.trainer_difficulty = TRAINER_DIFFICULTY;
  data.trainer_ivs.ev_conditions = [{ flag: "0x16E2", set: "Rebalanced variant", clear: "Standard variant" }];
  view.itemLink = (id) => `item ${id}`;
  view.moveTile = (id) => `move ${id}`;
  member.write_changes = [
    { level: 1, difficulties: [3], when: { "0x16E2": [0] }, moves: [1, 2], item: 557, rules: ["standard"] },
    { level: 1, difficulties: [3], when: { "0x16E2": [1] }, moves: [1, 2], item: 473, rules: ["rebalanced"] }];
  const rows = view.partyWrites(member, "3").children;
  assert.deepEqual(rows.map((row) => row.children[0].children[0]),
    ["Insane, Standard variant", "Insane, Rebalanced variant"]);
  assert.deepEqual(rows.map((row) => row.children[1].children[0].children.at(-1)), ["item 557", "item 473"]);
  assert.equal(view.partyWrites(member, "2"), null);
  assert.deepEqual(view.technicalConditions(battle), []);
  const head = view.partyMember(battle.party[0], null, battle, "3").children[1].children[2].children[0];
  assert.notEqual(head.children.at(-1), "item 557");
});

test("an item held in every state goes to the head; move rows keep only their moves", () => {
  const rows = [
    { level: 1, difficulties: [3], when: { "0x1": [0], "0x2": [0] }, moves: [5, 6], item: 9, rules: ["a"] },
    { level: 1, difficulties: [3], when: { "0x1": [1], "0x2": [0] }, moves: [7, 8], item: 9, rules: ["a"] },
    { level: 1, difficulties: [3], when: { "0x2": [1] }, moves: [7, 8], item: 9, rules: ["a"] }];
  const { head, rows: shown } = factorWrites(rows, { item: 0, moves: [7, 8] });
  assert.deepEqual(head, { item: 9 });
  assert.deepEqual(shown, [{ level: 1, difficulties: [3], when: { "0x1": [0], "0x2": [0] }, moves: [5, 6] }]);
});

test("a variant item shows once per value beside move rows of other conditions", () => {
  const rows = [];
  for (const variant of [0, 1]) {
    const item = variant ? 473 : 557;
    rows.push({ level: 42, difficulties: [3], when: { "0x16E2": [variant], "0x170B": [0], "0x9F3": [0] }, moves: [1, 2], item, rules: ["r"] },
      { level: 42, difficulties: [3], when: { "0x16E2": [variant], "0x170B": [1], "0x9F3": [0] }, moves: [3, 4], item, rules: ["r"] },
      { level: 42, difficulties: [3], when: { "0x16E2": [variant], "0x9F3": [1] }, moves: [3, 4], item, rules: ["r"] });
  }
  const { head, rows: shown } = factorWrites(rows, { item: 0, moves: [3, 4] });
  assert.deepEqual(head, {});
  assert.deepEqual(shown.filter((row) => "item" in row).map((row) => [row.when, row.item]),
    [[{ "0x16E2": [0] }, 557], [{ "0x16E2": [1] }, 473]]);
  assert.deepEqual(shown.filter((row) => row.moves).map((row) => [row.when, row.moves]),
    [[{ "0x170B": [0], "0x9F3": [0] }, [1, 2]]]);
  assert.ok(shown.every((row) => !("item" in row && row.moves)));
  const joint = rows.slice(0, 1).map((row) => ({ ...row, when: { "0x16E2": [0] } }));
  assert.deepEqual(factorWrites(joint, { item: 0, moves: [3, 4] }).rows, joint);
});

test("a member's always-held item reads in the head, its move row without an item", (context) => {
  globalThis.document = { createElement: (tag) => ({ ...fakeElement(), tag }), createTextNode: (text) => ({ text }) };
  context.after(() => { setCatalog(null); delete globalThis.document; });
  const { view, member, data, battle } = trainerStatsFixture();
  data.trainer_ivs.difficulties = ["difficult", "vanilla", "expert", "insane"];
  data.trainer_difficulty = TRAINER_DIFFICULTY;
  data.trainer_ivs.ev_conditions = [{ flag: "0x9F3", set: "With a battle partner", clear: "Without a battle partner" }];
  view.itemLink = (id) => `item ${id}`;
  view.moveTile = (id) => `move ${id}`;
  const listed = member.listed?.moves ?? battle.party[0].moves;
  member.write_changes = [
    { level: 1, difficulties: [3], when: { "0x9F3": [0] }, moves: [1, 2], item: 493, rules: ["r"] },
    { level: 1, difficulties: [3], when: { "0x9F3": [1] }, moves: listed, item: 493, rules: ["r"] }];
  const card = view.partyMember(battle.party[0], null, battle, "3");
  assert.equal(card.children[1].children[2].children[0].children.at(-1), "item 493");
  const rows = card.children.find((node) => node.className === "dex-party-species").children;
  assert.deepEqual(rows.map((row) => row.children[0].children[0]), ["Insane, Without a battle partner"]);
  assert.deepEqual(rows[0].children[1].children.map((node) => node.children), [["move 1", "move 2"]]);
});

test("built species expand to item, ability, stats and moves by built level", (context) => {
  globalThis.document = { createElement: (tag) => ({ ...fakeElement(), tag }), createTextNode: (text) => ({ text }) };
  context.after(() => { setCatalog(null); delete globalThis.document; });
  const { view, member, data, species, battle } = trainerStatsFixture();
  const evolved = { ...species, slot: 999, name: "Evolved", base_stats: { ...species.base_stats, speed: 50 } };
  view.view.record = (slot) => [species, evolved].find((row) => row.slot === slot);
  view.view.speciesName = (record) => record.name;
  view.view.abilityLink = (slot, id) => `ability ${slot}/${id}`;
  view.view.moveLink = (id) => `move ${id}`;
  view.itemLink = (id) => `item ${id}`;
  data.trainer_ivs.species_rules = [{ id: "level", status: "known", label: "Level {level}" }];
  member.species_changes = [{ species: 999, rule: "level", level: 36, difficulties: [2, 3], item: 9,
    abilities: { 2: [5], 3: [5, 6] }, ivs: { 2: [31, 31, 31, 31, 31, 31], 3: [31, 31, 31, 31, 31, 31] },
    evs: { 2: [0, 0, 0, 252, 0, 0], 3: { alternatives: [{ evs: [0, 0, 0, 0, 0, 0], when: { "0x821": [0] } },
      { evs: [252, 0, 0, 252, 0, 0], when: { "0x821": [1] } }] } },
    bands: [{ level: 36, last: 39, moves: [1, 0, 2] }, { level: 40, moves: [3, 4] }] }];
  data.trainer_ivs.ev_conditions = [{ flag: "0x821", set: "After badge", clear: "Before badge" }];
  setCatalog({ "Possible species": "Species", "Level {level}": "L{level}", "Item": "Held", "Ability": "Ab",
    "Possible abilities": "Abs", "Lv {n}": "Lv. {n}" });
  const mon = battle.party[0];
  const [, disclosure] = view.partySpecies(member, "2", mon).children;
  assert.equal(disclosure.className, "dex-built");
  const [summary, body] = disclosure.children;
  assert.deepEqual(summary.children.map((node) => node.children?.[0] ?? node), ["Evolved", ": L36"]);
  const [item, ability, stats, ...bands] = body.children;
  assert.deepEqual(item.children.map((node) => node.children?.[0] ?? node), ["Held: ", "item 9"]);
  assert.deepEqual(ability.children.map((node) => node.children?.[0] ?? node), ["Ab: ", "ability 999/5"]);
  const rows = stats.children[0].children[1].children.map((row) => row.children.map((cell) => cell.children[0]));
  assert.deepEqual(rows.find((row) => row[1] === "50").slice(1), ["50", "252", "31"]);
  assert.deepEqual(bands.map((band) => band.children[0].children[0]), ["Lv. 36–39", "Lv. 40+"]);
  assert.deepEqual(bands.map((band) => band.children[1].children.map((tile) => tile.children[0])),
    [["move 1", "move 2"], ["move 3", "move 4"]]);
  const insane = view.partySpecies(member, "3", mon).children[1].children[1].children;
  assert.equal(insane[1].children[0].children[0], "Abs: ");
  assert.equal(insane[2].children[0].children[0].children[0].children.length, 5);
  assert.equal(insane[2].children.length, 3);
  delete member.species_changes[0].abilities;
  assert.equal(view.partySpecies(member, "2", mon).children[1].tag, "div");
  assert.equal(bandLevel({ level: 31 }, { last: 39 }), "Lv. 31–39");
});

test("evolution on level scaling names badges, level range, battle and source species", (context) => {
  globalThis.document = { createElement: (tag) => ({ ...fakeElement(), tag }), createTextNode: (text) => ({ text }) };
  context.after(() => { setCatalog(null); delete globalThis.document; });
  const { view, member, data, species } = trainerStatsFixture();
  const records = [1, 2, 3].map((slot) => ({ ...species, slot, name: `Mon ${slot}` }));
  view.view.record = (slot) => records.find((row) => row.slot === slot);
  view.view.speciesName = (record) => record.name;
  data.trainer_ivs.species_rules = [{ id: "scaled", kind: "scaled_evolution", status: "known", label: "L {level}+",
    hall_badges: 9, labels: { range: "L {level}-{last}", badges: "B{badges} L {level}+", badges_range: "B{badges} L {level}-{last}",
      hall: "H L {level}+", hall_range: "H L {level}-{last}", from: "{condition} from {species}" },
    battles: [{ id: "rematch", modes: [5], label: "{condition} rematch" },
      { id: "party-level", command: 1, party_level: 30, label: "{condition} party {party_level}" }] }];
  setCatalog({ "Possible species": "Species" });
  member.species_changes = [
    { species: 2, rule: "scaled", level: 20, last: 44, badges: 3, difficulties: [0, 2] },
    { species: 3, rule: "scaled", level: 45, badges: 9, difficulties: [0, 2] },
    { species: 2, rule: "scaled", level: 20, last: 44, difficulties: [3], battle: "rematch" },
    { species: 3, rule: "scaled", level: 45, difficulties: [3], battle: "party-level", from: 1 },
  ];
  const expert = view.partySpecies(member, "2");
  assert.deepEqual(expert.children.slice(1).map((row) => row.children.at(-1)), [": B3 L 20-44", ": H L 45+"]);
  const insane = view.partySpecies(member, "3");
  assert.deepEqual(insane.children.slice(1).map((row) => row.children.at(-1)),
    [": L 20-44 rematch", ": L 45+ party 30 from Mon 1"]);
  assert.equal(view.partySpecies(member, "1"), null);
  assert.equal(view.speciesRule({ species: 2, rule: "scaled", level: 21, difficulties: [3] }), "L 21+");
});

test("trainer EV rows read one difficulty", () => {
  const data = { profile_block: "abc" };
  inlineTrainerIvs(data, { format: "gen3tl-pokedex-trainer-ivs", schema: 3, profile_block: "abc", members: [
    { trainer: 1, slot: 1, evs: { 3: [4, 0, 0, 252, 252, 0], 2: null } },
  ] });
  const member = trainerIvMember(data, 1, 1);
  assert.deepEqual(trainerEvRows(member, "3"), [{ evs: [4, 0, 0, 252, 252, 0], when: null }]);
  assert.deepEqual(trainerEvRows(member, "2"), [{ evs: null, when: null }]);
  assert.deepEqual(trainerEvRows(null, "3"), null);
});

test("party IV tables use the selected difficulty on the displayed side", (context) => {
  globalThis.document = { createElement: fakeElement, createTextNode: (text) => ({ text }) };
  context.after(() => { delete globalThis.document; });
  const { view, group, member, battle } = trainerStatsFixture();
  const fixed = [31, 0, 31, 31, 31, 31];
  const changed = [0, 31, 31, 31, 31, 31];
  member.contexts = Object.fromEntries(TRAINER_DIFFICULTY.order.flatMap((mode) => [
    [`${mode}/1`, [...fixed]], [`${mode}/0`, [...changed]],
  ]));
  const render = () => partyDisclosures(view.trainerDetail(group))[0];
  for (const mode of TRAINER_DIFFICULTY.order) {
    view.difficulty = String(mode);
    assert.equal(render().children.length, 2);
  }
  member.contexts["3/1"] = changed;
  for (const mode of TRAINER_DIFFICULTY.order) {
    view.difficulty = String(mode);
    const disclosure = render();
    assert.equal(disclosure.children[0].children[0], "IV: 0–31");
    assert.equal(partyStatValues(disclosure)[0][3], mode === 3 ? "0" : "31");
    assert.equal(disclosure.children.length, 2);
  }
  battle.kind = "partners";
  view.difficulty = "0";
  assert.equal(render().children.length, 2);
  assert.equal(partyStatValues(render())[0][3], "0");
  member.contexts["2/0"] = fixed;
  assert.equal(render().children.length, 2);
  view.difficulty = "2";
  assert.equal(partyStatValues(render())[0][3], "31");
  assert.equal(render().children.length, 2);
});

test("missing alternative IV contexts keep only the stats table", (context) => {
  globalThis.document = { createElement: fakeElement, createTextNode: (text) => ({ text }) };
  context.after(() => { delete globalThis.document; });
  const { view, group, member } = trainerStatsFixture();
  member.contexts = { "0/1": [31, 31, 31, 31, 31, 31], "1/1": null, "2/1": [], "3/1": [32, 31, 31, 31, 31, 31] };
  assert.equal(partyDisclosures(view.trainerDetail(group))[0].children.length, 2);
});

test("unresolved party IVs keep base stats and explain missing context", () => {
  globalThis.document = { createElement: fakeElement, createTextNode: (text) => ({ text }) };
  try {
    const { view, group, member } = trainerStatsFixture();
    member.contexts["0/1"] = null;
    const [unresolved] = partyDisclosures(view.trainerDetail(group));
    assert.equal(unresolved.children[0].children[0], "IV: ?");
    assert.equal(unresolved.children[2].children[0], "IVs unavailable");
    assert.deepEqual(partyStatValues(unresolved).map((row) => [row[1], row[3]]),
      [["61", "—"], ["131", "—"], ["211", "—"], ["53", "—"], ["101", "—"], ["13", "—"]]);
    view.difficulty = "0";
    for (const vector of [null, [31, 31], [31, 31, 31, 32, 31, 31]]) {
      member.contexts["0/1"] = vector;
      const [missing] = partyDisclosures(view.trainerDetail(group));
      assert.equal(missing.children[0].children[0], "IV: ?");
      assert.equal(missing.children[2].children[0], "IVs unavailable");
      assert.ok(partyStatValues(missing).every((row) => row[3] === "—"));
    }
    view.view.record = () => null;
    const [missingSpecies] = partyDisclosures(view.trainerDetail(group));
    assert.ok(partyStatValues(missingSpecies).every((row) => row[1] === "—"));
    assert.equal(missingSpecies.children[1].children[2].children[0].children[1].children[0], "—");
  } finally {
    delete globalThis.document;
  }
});

test("unbuilt partner slots do not reuse opponent IVs", (context) => {
  globalThis.document = { createElement: fakeElement, createTextNode: (text) => ({ text }) };
  context.after(() => { delete globalThis.document; });
  const { view, group, member, battle } = trainerStatsFixture();
  member.contexts = { "0/1": [31, 31, 31, 31, 31, 31] };
  battle.kind = "partners";
  view.difficulty = "0";
  const [unbuilt] = partyDisclosures(view.trainerDetail(group));
  assert.equal(unbuilt.children[0].children[0], "IV: ?");
  assert.equal(unbuilt.children[2].children[0], "IVs unavailable");
  assert.ok(partyStatValues(unbuilt).every((row) => row[3] === "—"));
  battle.kind = "opponent";
  const [opponent] = partyDisclosures(view.trainerDetail(group));
  assert.equal(opponent.children[0].children[0], "IV: 31");
  assert.ok(partyStatValues(opponent).every((row) => row[3] === "31"));
});

test("party disclosures use the linked form's stats and localized labels", () => {
  globalThis.document = { createElement: fakeElement, createTextNode: (text) => ({ text }) };
  const labels = { "Base stats": "Localized base stats", Base: "Localized base", Attack: "Localized attack" };
  setLabelSource((key) => labels[key] ? { text: labels[key], game: false } : null);
  try {
    const { view, group, species } = trainerStatsFixture();
    const base = { ...species, slot: 1, base_stats: { ...species.base_stats, attack: 1 } };
    species.base_species = base.slot;
    view.view.record = (slot) => slot === species.slot ? species : base;
    const [initial] = partyDisclosures(view.trainerDetail(group));
    assert.equal(initial.children[1].children[0].children[0].children[1].children[0], "Localized base");
    assert.deepEqual(partyStatValues(initial)[1], ["Localized attack", "131", "252", "31"]);
    assert.equal(initial.children.length, 2);
    view.difficulty = "1";
    const [changed] = partyDisclosures(view.trainerDetail(group));
    assert.equal(changed.children.length, 2);
  } finally {
    setLabelSource(null);
    delete globalThis.document;
  }
});

test("each party disclosure retains its state when difficulty replaces a roster", () => {
  globalThis.document = { createElement: fakeElement, createTextNode: (text) => ({ text }) };
  try {
    const nodes = new Map();
    const $ = (id) => {
      if (!nodes.has(id)) nodes.set(id, fakeElement());
      return nodes.get(id);
    };
    const { view, group, battle, member, data } = trainerStatsFixture($);
    battle.party.push({ ...battle.party[0], index: 5 });
    const variant = { ...battle, id: 701, base: 700, difficulty: ["vanilla"] };
    battle.base = 700;
    battle.difficulty = ["difficult"];
    group.battles.push(variant);
    data.trainer_ivs.members.push({ ...member, trainer: 701 });
    const detail = () => $("x-data-content").children[0];
    const change = (mode) => detail().children[0].children[0].children[1].listeners.change({ target: { value: mode } });
    view.difficulty = "0";
    view.renderDetail(group.id);
    let [first, second] = partyDisclosures(detail());
    assert.equal(first.open, false);
    assert.equal(second.open, false);
    togglePartyStats(first, true);
    change("1");
    [first, second] = partyDisclosures(detail());
    assert.equal(first.open, true);
    assert.equal(second.open, false);
    assert.equal(first.children[0].children[0], "IV: 31");
    togglePartyStats(first, false);
    togglePartyStats(second, true);
    change("0");
    [first, second] = partyDisclosures(detail());
    assert.equal(first.open, false);
    assert.equal(second.open, true);
    change("base");
    const all = partyDisclosures(detail());
    assert.deepEqual(all.map((node) => node.open), [false, true]);
    assert.deepEqual(all.map((node) => node.children[0].children[0]), ["IV: 0–31", "IV: ?"]);
    view.reset();
    assert.equal(view.expandedPartyMembers.size, 0);
  } finally {
    delete globalThis.document;
  }
});

test("native disclosure clicks preserve focus and state through DOM difficulty updates", { skip: NEEDS_DOM }, (context) => {
  const win = domPage(context, "pokedex");
  const { view, group } = trainerStatsFixture((id) => document.getElementById(id));
  view.renderDetail(group.id);
  const disclosure = document.querySelector(".dex-party-stats");
  const summary = disclosure.querySelector("summary");
  assert.equal(disclosure.open, false);
  summary.focus();
  summary.click();
  disclosure.dispatchEvent(new win.Event("toggle"));
  assert.equal(disclosure.open, true);
  assert.equal(document.activeElement, summary);
  const select = document.querySelector(".dex-difficulty select");
  select.value = "0";
  select.dispatchEvent(new win.Event("change"));
  assert.equal(document.querySelector(".dex-party-stats"), disclosure);
  assert.equal(disclosure.open, true);
  assert.equal(summary.textContent, "IV: 0–31");
  assert.equal(document.querySelectorAll("dialog[open]").length, 0);
  summary.click();
  disclosure.dispatchEvent(new win.Event("toggle"));
  select.value = "1";
  select.dispatchEvent(new win.Event("change"));
  assert.equal(disclosure.open, false);
  assert.equal(summary.textContent, "IV: 31");
});

test("trainer IV data loads once and failures stay explicit", async () => {
  const doc = { format: "gen3tl-pokedex-trainer-ivs", schema: 3, profile_block: "abc", members: [] };
  const data = { profile_block: "abc", trainer_ivs: "trainer-ivs.json" };
  const calls = [];
  const parent = {
    host: {
      data: () => data, datasetFile: (name) => `data/${name}`,
      fetcher: async (url) => { calls.push(url); return { ok: true, json: async () => doc }; },
    },
    texts: new Map(),
  };
  const view = dataRenderer(parent, fakeElement);
  await view.loadTrainerIvs();
  await view.loadTrainerIvs();
  assert.deepEqual(calls, ["data/trainer-ivs.json"]);
  assert.equal(view.trainerIvsFailed, false);
  assert.ok(data.trainerIvMembers instanceof Map);
  const broken = { profile_block: "other", trainer_ivs: "trainer-ivs.json" };
  const failed = dataRenderer({ host: { ...parent.host, data: () => broken } }, fakeElement);
  await failed.loadTrainerIvs();
  assert.equal(failed.trainerIvsFailed, true);
  assert.equal(broken.trainerIvMembers, undefined);
  const absent = dataRenderer({ host: { data: () => ({}) } }, fakeElement);
  await absent.loadTrainerIvs();
  assert.equal(absent.trainerIvsFailed, true);
  const malformed = { trainer_ivs: { format: "invalid" } };
  const invalid = dataRenderer({ host: { data: () => malformed } }, fakeElement);
  await invalid.loadTrainerIvs();
  assert.equal(invalid.trainerIvsFailed, true);
  assert.equal(malformed.trainer_ivs, null);
  assert.equal(trainerIvMember(malformed, 700, 4), null);
});

async function staleTrainerIvLoad(fail) {
  const companion = (profile_block) => ({ format: "gen3tl-pokedex-trainer-ivs", schema: 3, profile_block, members: [] });
  const a = { profile_block: "a", trainer_ivs: "a.json" };
  const b = { profile_block: "b", trainer_ivs: "b.json" };
  let current = a, resolveA, rejectA;
  const pending = new Promise((resolve, reject) => { resolveA = resolve; rejectA = reject; });
  const parent = {
    generation: 0, navigation: 0, texts: new Map(),
    host: {
      data: () => current, datasetFile: (name) => `data/${name}`,
      fetcher: async (url) => url.endsWith("a.json") ? pending : { ok: true, json: async () => companion("b") },
    },
  };
  const view = dataRenderer(parent, fakeElement);
  view.mode = "trainers";
  const stale = view.load();
  current = b;
  parent.generation += 1;
  await view.loadTrainerIvs();
  const members = b.trainerIvMembers, loaded = b.trainer_ivs;
  if (fail) rejectA(new Error("late failure"));
  else resolveA({ ok: true, json: async () => companion("a") });
  assert.equal(await stale, false);
  assert.equal(b.trainerIvMembers, members);
  assert.equal(b.trainer_ivs, loaded);
  assert.equal(view.trainerIvsFailed, false);
  assert.equal(a.trainerIvMembers, undefined);
  assert.equal(a.trainer_ivs, "a.json");
}

test("a delayed companion success cannot mutate a newer dataset", async () => {
  await staleTrainerIvLoad(false);
});

test("a delayed companion failure cannot mutate a newer dataset", async () => {
  await staleTrainerIvLoad(true);
});

test("the learners of a move group by method, level-up first and by level", () => {
  const rows = [{ slot: 9, method: 1, level: null }, { slot: 4, method: 0, level: 7 }, { slot: 1, method: 0, level: 0 },
    { slot: 2, method: 3, level: null }, { slot: 7, method: 2, level: null }, { slot: 3, method: 0, level: 7 }, { slot: 5, method: 9, level: null }];
  const groups = learnerGroupsOf(rows);
  assert.deepEqual(groups.map((g) => [g.method, g.title, g.entries.map((e) => e.slot)]),
    [[0, "Level up", [1, 3, 4]], [1, "TM and HM", [9]], [3, "Tutor", [2]], [2, "Egg", [7]], [9, "Method 9", [5]]]);
  assert.deepEqual(learnerGroupsOf([]), []);
});

test("a learner column fits the longest name, with room for a level in level-up groups", () => {
  const measure = (text) => text.length * 7;
  assert.equal(learnerColumnWidth(["#004 Charmander", "#555 Darmanitan (Galarian Zen Mode)"], measure, ["Lv 7", "Start"]),
    11 + 24 + 6 + Math.ceil(35 * 7 * 1.03) + 6 + 6 + Math.ceil(5 * 7 * 1.03));
  assert.equal(learnerColumnWidth(["#004 Charmander"], measure), 11 + 24 + 6 + Math.ceil(15 * 7 * 1.03) + 6);
  assert.equal(learnerColumnWidth([], measure), 47);
});

test("the move filter narrows by a flag or list key, named like the card", () => {
  const moves = [
    { id: 7, flag_keys: ["makes_contact"], list_keys: ["punching", "sheer_force"] },
    { id: 14, flag_keys: [], list_keys: ["dance"] },
    { id: 33, flag_keys: ["makes_contact"] },
    { id: 0 },
  ];
  const ids = (property) => moves.filter((m) => moveMatches(m, { property })).map((m) => m.id);
  assert.deepEqual(ids("punching"), [7]);
  assert.deepEqual(ids("makes_contact"), [7, 33]);
  assert.deepEqual(ids("dance"), [14]);
  assert.deepEqual(ids(null), [7, 14, 33, 0]);
  assert.deepEqual(propertyOptions(moves), [
    { key: "dance", name: "Dance" }, { key: "makes_contact", name: "Makes contact" },
    { key: "punching", name: "Punching" }, { key: "sheer_force", name: "Sheer Force-boosted" }]);
});

test("item cards link placed sources and show mining chances", () => {
  const fake = () => ({
    children: [], className: "", textContent: "", title: "", value: "", hidden: false, disabled: false, listeners: {},
    addEventListener(ev, fn) { this.listeners[ev] = fn; }, setAttribute(k, v) { this[k] = v; },
    append(...kids) { this.children.push(...kids.flat()); }, replaceChildren(...kids) { this.children = [...kids.flat()]; },
    prepend(...kids) { this.children.unshift(...kids.flat()); }, classList: { toggle() {} },
  });
  globalThis.document = { createElement: () => fake(), createTextNode: (t) => ({ text: t }) };
  const opened = [];
  const parent = {
    host: { data: () => ({ species: [], items: [] }), spriteSrc: (path) => path },
    text: (id) => ({ "mapsec:120": "Dresco Town", "mapsec:153": "Route 9", "mapsec:132": "Route 5" })[id] ?? "",
    openLocation(id) { opened.push(id); }, openMove() {}, openItem() {},
  };
  const elements = new Map();
  const $ = (id) => { if (!elements.has(id)) elements.set(id, fake()); return elements.get(id); };
  const view = dataRenderer(parent, $);
  const sold = { id: 1, name: "items:1", pocket: 1, price: 200, description: null,
    sources: [{ kind: "shop", map: "17.0", map_section: 120 }] };
  const found = { id: 2, name: "items:2", pocket: 1, price: 0, description: null, sources: [
    { kind: "ground", map: "1.81", map_section: 153, quantity: 2 },
    { kind: "hidden", map: "2.50", map_section: 132, quantity: 1 },
  ] };
  const shopCard = JSON.stringify(view.itemDetail(sold));
  assert.ok(shopCard.includes("Where to get") && shopCard.includes("Shop") && shopCard.includes("Dresco Town"));
  view.itemSourceBlock(sold)[0].children[1].children[0].children[2].listeners.click();
  const mapCard = JSON.stringify(view.itemDetail(found));
  for (const expected of ["On the map", "Route 9", "Hidden", "Route 5", "×2"]) assert.ok(mapCard.includes(expected), expected);
  view.itemSourceBlock(found)[0].children[1].children[0].children[2].listeners.click();
  const mined = { id: 105, name: "items:105", pocket: 1, price: 0, description: null,
    sources: [{ kind: "mining", chance: 0.5 }, { kind: "mining", chance: 0.8 }] };
  const miningCard = JSON.stringify(view.itemDetail(mined));
  for (const expected of ["Where to get", "Mining", "0.5%", "0.8%"]) assert.ok(miningCard.includes(expected), expected);
  assert.ok(!miningCard.includes("undefined"));
  const plain = JSON.stringify(view.itemDetail({ id: 3, name: "items:3", pocket: 1, price: 0, description: null, sources: [] }));
  assert.ok(!plain.includes("Where to get"));
  assert.deepEqual(opened, ["120", "153"]);
  delete globalThis.document;
});

test("item cards list exchanges, upgrades and requirements with places and speakers", () => {
  const fake = () => ({
    children: [], className: "", textContent: "", title: "", value: "", hidden: false, disabled: false, listeners: {},
    addEventListener(ev, fn) { this.listeners[ev] = fn; }, setAttribute(k, v) { this[k] = v; },
    append(...kids) { this.children.push(...kids.flat()); }, replaceChildren(...kids) { this.children = [...kids.flat()]; },
    prepend(...kids) { this.children.unshift(...kids.flat()); }, classList: { toggle() {} },
  });
  globalThis.document = { createElement: () => fake(), createTextNode: (t) => ({ text: t }) };
  const opened = [];
  const items = [{ id: 47, name: "items:47" }, { id: 46, name: "items:46" }, { id: 219, name: "items:219" },
    { id: 195, name: "items:195" }, { id: 181, name: "items:181" }, { id: 94, name: "items:94" }, { id: 92, name: "items:92" }];
  const parent = {
    host: { data: () => ({ species: [], items, encounters: [{ map_section: 99, runtime_map: "3.1", species: 1 }] }),
      spriteSrc: (path) => path },
    text: (id) => ({ "items:47": "Sea Shell", "items:46": "Sea Salt", "items:219": "Shell Bell", "items:195": "Everstone",
      "items:181": "Macho Brace", "items:94": "Moon Stone", "items:92": "Magma Stone", "mapsec:99": "Seaport City",
      "mapsec:91": "Crater Town", "s1F16D88": "Alice", "moves:8": "Ice Punch", "species:7": "Ancient creature" })[id] ?? "",
    openLocation(id) { opened.push(id); }, openMove() {}, openItem(id) { opened.push(`items:${id}`); },
    record: (id) => ({ slot: id, name: `species:${id}` }),
    openSpecies(id) { opened.push(`species:${id}`); },
    moveLink: (id) => ({ text: `move ${id}` }),
  };
  const elements = new Map();
  const $ = (id) => { if (!elements.has(id)) elements.set(id, fake()); return elements.get(id); };
  const view = dataRenderer(parent, $);
  const shell = { id: 47, name: "items:47", pocket: 1, price: 0, description: null, sources: [], uses: [
    { kind: "exchange", takes: [{ item: 47, quantity: 4 }, { item: 46, quantity: 4 }], gives: [{ item: 219, quantity: 1 }],
      places: [{ map: "h1", map_section: 99 }], speaker: "s1F16D88",
      conditions: ["Once per day; offer depends on story progress"] },
    { kind: "exchange", takes: [{ item: 47, quantity: null }], gives: [{ money: 7000 }], places: [{ map: "h2", map_section: 91 }] },
  ] };
  const card = JSON.stringify(view.itemDetail(shell));
  for (const expected of ["Uses", "Exchange", "×4", "Sea Shell", "Sea Salt", "Shell Bell", "Seaport City", "Alice",
    "7000", "Per item", "Crater Town", "Once per day; offer depends on story progress"]) assert.ok(card.includes(expected), expected);
  assert.ok(!card.includes("undefined") && !card.includes("null") && !card.includes("×1"));
  const block = view.itemUseBlock(shell)[0].children[1].children[0];
  block.children.find((child) => child.title === "items:219").listeners.click();
  const reward = { id: 219, sources: [{ ...shell.uses[0], map: "h1", map_section: 99 }] };
  const received = view.itemSourceBlock(reward)[0].children[1].children;
  assert.equal(received.length, 1);
  for (const expected of ["Exchange", "×4", "Sea Shell", "Sea Salt", "Shell Bell", "Seaport City", "Alice",
    "Once per day; offer depends on story progress"])
    assert.ok(JSON.stringify(received).includes(expected), expected);
  assert.ok(!JSON.stringify(received).includes("Gift"));
  received[0].children.find((child) => child.title === "items:47").listeners.click();
  const stone = { id: 195, name: "items:195", pocket: 1, price: 0, description: null, sources: [], uses: [
    { kind: "upgrade", takes: [{ item: 195, quantity: [3, 10] }], gives: [{ upgrade: 181 }], places: [{ map: "h3", map_section: 91 }] },
    { kind: "exchange", takes: [{ item: 195, quantity: 4 }], gives: [{ item: 94 }, { item: 219 }], choice: true, places: [] },
    { kind: "requirement", takes: [{ item: 195, quantity: 1 }], gives: [{ move: 8 }, { effect: "Relearn a move" }], places: [] },
  ] };
  const upgrades = JSON.stringify(view.itemDetail(stone));
  for (const expected of ["Upgrade", "×3–10", "Level +1:", "Macho Brace", "One of", "Moon Stone", " / ", "Required",
    "Teaches", "move 8", "Relearn a move"]) assert.ok(upgrades.includes(expected), expected);
  const variants = { id: 92, uses: [
    { kind: "exchange", takes: [{ item: 92, quantity: 3 }], gives: [{ move: 8 }], places: [],
      conditions: ["Offer varies with the date"] },
    { kind: "exchange", takes: [{ item: 92, quantity: 2 }], gives: [{ move: 7 }], places: [],
      conditions: ["Offer varies with the date"] },
    { kind: "exchange", takes: [{ item: 92, quantity: null }], gives: [{ money: 90 }], places: [],
      conditions: ["Birthday discount active"] },
    { kind: "exchange", takes: [{ item: 92, quantity: 1 }], gives: [{ species: 7 }], places: [] },
  ] };
  const variantRows = view.itemUseBlock(variants)[0].children[1].children;
  assert.equal(variantRows.length, 4);
  const variantText = JSON.stringify(variantRows);
  for (const expected of ["Offer varies with the date", "×3", "×2", "move 8", "move 7", "90",
    "Per item", "Birthday discount active", "Ancient creature"]) assert.ok(variantText.includes(expected), expected);
  assert.ok(!variantText.includes("One of"));
  variantRows[3].children.find((child) => child.title === "species:7").listeners.click();
  assert.equal(view.itemUseBlock({ id: 3, uses: [] }).length, 0);
  assert.deepEqual(opened, ["items:219", "items:47", "species:7"]);
  delete globalThis.document;
});

test("grouped rewards wrap compactly and keep quantities, links and trigger positions", () => {
  globalThis.document = { createElement: () => fakeElement(), createTextNode: (text) => ({ text }) };
  const source = { kind: "gift", map: "0.0", map_section: 7, runtime_map: "0.0", script: "0x0000500",
    choices: [{ item: 1, quantity: 2 }, { item: 1, quantity: 3 }, { item: 2, quantity: 1 }],
    positions: [{ x: 1, y: 2 }, { x: 3, y: 4 }] };
  const items = [1, 2].map((id) => ({ id, name: `items:${id}`, sources: [source] }));
  const opened = [];
  const data = { items, encounters: [], location_details: [{ runtime_map: "0.0", map: "0.0", map_section: 7,
    map_header: "0x100", points: [], exits: [] }],
    maps: [{ runtime_map: "0.0", map_header: "0x100", image: "maps/0.0.png", width: 8, height: 8 }] };
  const parent = { host: { data: () => data, spriteSrc: (path) => path },
    text: (id) => ({ "items:1": "First prize", "items:2": "Second prize", "mapsec:7": "Prize hall" })[id] ?? "",
    openLocation() {}, openItem(id) { opened.push(id); } };
  const view = dataRenderer(parent, fakeElement);
  const card = view.itemSourceBlock(items[1])[0];
  assert.equal(card.children[1].children.length, 1);
  for (const expected of ["Possible rewards", "First prize", "Second prize", "×2", "×3", "Prize hall"])
    assert.ok(JSON.stringify(card).includes(expected), expected);
  assert.ok(!JSON.stringify(card).includes("One of"));
  view.itemChoices(source.choices).flatMap((node) => node?.children ?? [])
    .find((node) => node.title === "items:2").listeners.click();
  assert.deepEqual(opened, [2]);
  view.mode = "locations";
  const group = view.record("7");
  assert.equal(view.locationItems(group).length, 1);
  const detail = view.locationDetail(group);
  const markers = detail.children[0].children[1].children.slice(1);
  assert.equal(markers.length, 2);
  assert.ok(markers.every((marker) => marker.title.includes("First prize") && marker.title.includes("Second prize")));
  const table = detail.children.find((child) => /dex-location-items/.test(child.className ?? ""));
  const row = table.children[1].children[0].children[1].children[0];
  assert.equal(table.children[1].children[0].children[1].children.length, 1);
  const choices = row.children[0].children[0];
  assert.equal(choices.className, "dex-item-choices");
  assert.deepEqual(choices.children.filter((node) => typeof node !== "string")
    .map((choice) => choice.children[0].title), ["items:1", "items:1", "items:2"]);
  const css = readFileSync(new URL("../site.css", import.meta.url), "utf8");
  assert.match(css, /^\.dex-item-choices \{ display: flex; flex-wrap: wrap;/m);
  assert.match(css, /^\.dex-location-items:has\(\.dex-item-choices\) th:nth-child\(1\) \{ width: auto; \}/m);
  const phone = css.match(/@media \(max-width: 760px\) \{\n(  \.dex-location-items:has[^]*?)\n\}/)[1];
  assert.match(phone, /\.dex-location-data \{ display: grid; grid-template-columns: minmax\(128px, 1fr\) repeat\(2, minmax\(0, 1fr\)\); \}/);
  assert.match(phone, /:is\(thead, tbody, tr\) \{ display: contents; \}/);
  assert.match(phone, /tr:has\(\.dex-item-choices\) > td:first-child \{ grid-column: 1 \/ -1;/);
  assert.match(phone, /tr:has\(\.dex-item-choices\) > td:nth-child\(2\) \{ grid-column: 2; \}/);
  assert.match(phone, /\.dex-item-choices > span, \.dex-item-choices \.dex-item-link \{ flex-wrap: nowrap; white-space: nowrap; \}/);
  assert.doesNotMatch(phone, /style=/);
  source.choices = [{ item: 1, quantity: 2 }];
  const single = view.locationDetail(group).children.find((child) => /dex-location-items/.test(child.className ?? ""));
  const singleCell = single.children[1].children[0].children[1].children[0].children[0];
  assert.equal(singleCell.children.length, 1);
  assert.equal(singleCell.children[0].className, "");
  delete globalThis.document;
});

test("item cards list every upgrade level with localized amounts and location links", () => {
  const fake = (tag) => ({
    tag, children: [], className: "", listeners: {},
    addEventListener(event, fn) { this.listeners[event] = fn; }, setAttribute(key, value) { this[key] = value; },
    append(...children) { this.children.push(...children.flat()); },
    replaceChildren(...children) { this.children = children.flat(); }, classList: { toggle() {} },
  });
  globalThis.document = { createElement: fake, createTextNode: (text) => ({ text }) };
  const opened = [];
  const parent = {
    host: { data: () => ({ species: [], items: [], encounters: [{ map_section: 8, species: 1 }] }), spriteSrc: (path) => path },
    text: (id) => id === "mapsec:8" ? "Training hall" : "", openLocation: (id) => opened.push(id),
  };
  const view = dataRenderer(parent, fake);
  const record = { id: 1, pocket: 1, price: 0, held_effect_key: "Boost", held_effect_amount: {
    key: "Boost: ×{x}", values: { x: 1.5 }, conditions: ["Only in battle."],
    levels: [{ level: 1, values: { x: 1.5 } }, { level: 2, values: { x: 2 } }, { level: 3, values: { x: 2.5 } }],
    upgrade_sources: [{ key: "Win the training battle.", map_sections: [8] }],
  } };
  const before = structuredClone(record);
  setLabelSource((key) => key === "Boost: ×{x}" ? { text: "Bonus: ×{x}", game: false }
    : key === "Win the training battle." ? { text: "Complete training.", game: false } : null);
  try {
    const block = view.itemUpgradeBlock(record)[0];
    const levels = block.children[1].children.map((node) => node.children[0]);
    assert.deepEqual(levels, ["Level 1", "Bonus: ×1.5", "Level 2", "Bonus: ×2", "Level 3", "Bonus: ×2.5"]);
    assert.deepEqual([block.children[0].tag, block.children[2].tag], ["h2", "h2"]);
    assert.equal(block.children[2].children[0], "Where to upgrade");
    const source = block.children[3].children[0];
    assert.equal(source.children[0].children[0], "Training hall");
    assert.equal(source.children[2], "Complete training.");
    source.children[0].listeners.click();
    assert.deepEqual(opened, ["8"]);
    const card = JSON.stringify(view.itemDetail(record));
    for (const expected of ["Upgrade levels", "Level 3", "Only in battle.", "Complete training."])
      assert.ok(card.includes(expected), expected);
    assert.deepEqual(view.itemUpgradeBlock({}), []);
    assert.deepEqual(record, before);
  } finally {
    setLabelSource(null);
    delete globalThis.document;
  }
});

test("an item card lists the evolutions the item triggers", async () => {
  const species = [
    { slot: 44, dex: 44, name: "species:44", evolution: [{ method: 7, parameter: 93, target: 182, extra: 0 }, { method: 7, parameter: 94, target: 45, extra: 0 }] },
    { slot: 182, dex: 182, name: "species:182", evolution: [] },
    { slot: 191, dex: 191, name: "species:191", evolution: [{ method: 7, parameter: 93, target: 192, extra: 0 }] },
    { slot: 192, dex: 192, name: "species:192", evolution: [] },
    { slot: 6, dex: 6, name: "species:6", evolution: [{ method: 254, parameter: 447, target: 877, extra: 0 }] },
    { slot: 406, dex: 384, name: "species:406", evolution: [{ method: 254, parameter: 559, target: 911, extra: 2, parameter_kind: "move" }] },
  ];
  assert.deepEqual(itemEvolutions(93, species).map((r) => [r.source.slot, r.target?.slot]), [[44, 182], [191, 192]]);
  assert.deepEqual(itemEvolutions(447, species).map((r) => [r.source.slot, r.target]), [[6, null]]);
  assert.deepEqual(itemEvolutions(559, species), []);
  assert.deepEqual(itemEvolutions(1, species), []);
  const fake = () => ({
    children: [], className: "", textContent: "", title: "", value: "", hidden: false, disabled: false, listeners: {},
    addEventListener(ev, fn) { this.listeners[ev] = fn; }, setAttribute(k, v) { this[k] = v; },
    append(...kids) { this.children.push(...kids.flat()); }, replaceChildren(...kids) { this.children = [...kids.flat()]; },
    prepend(...kids) { this.children.unshift(...kids.flat()); },
    classList: { set: new Set(), toggle(c, on) { on ? this.set.add(c) : this.set.delete(c); } },
  });
  globalThis.document = { createElement: () => fake(), createTextNode: (t) => ({ text: t }) };
  const { PokedexDataView } = await import("../js/pokedexdataview/pokedexdataview.js");
  const names = { "species:44": "Глум", "species:182": "Белоссом", "species:191": "Санкерн", "species:192": "Санфлора", "items:93": "Камень солнца" };
  const parent = {
    host: { data: () => ({ species, items: [{ id: 93, pocket: 1, price: 2100 }] }), spriteSrc: (p) => `data/${p}` },
    text: (id) => names[id] ?? "", speciesName: (r) => names[r.name], record: (slot) => species.find((s) => s.slot === slot),
    openSpecies(slot) { this.opened = slot; }, openMove() {}, openItem() {},
  };
  const elements = new Map();
  const $ = (id) => { if (!elements.has(id)) elements.set(id, fake()); return elements.get(id); };
  const view = dataRenderer(parent, $);
  view.mode = "items";
  const card = view.itemDetail({ id: 93, name: "items:93", pocket: 1, price: 2100, description: null });
  const shown = JSON.stringify(card);
  for (const expected of ["Evolutions", "Глум", "Белоссом", "Санкерн", "Санфлора", "Камень солнца"]) assert.ok(shown.includes(expected), expected);
  assert.ok(!shown.includes("Use Камень солнца"));
  const plain = JSON.stringify(view.itemDetail({ id: 1, name: "items:1", pocket: 1, price: 200, description: null }));
  assert.ok(!plain.includes("Evolutions"));
  delete globalThis.document;
});

test("an item card names declared evolution item keys", async () => {
  const species = [
    { slot: 1215, dex: 79, name: "species:1215", evolution: [{ method: 7, parameter: 723, target: 1216, extra: 0 }] },
    { slot: 1216, dex: 80, name: "species:1216", evolution: [] },
  ];
  const fake = () => ({
    children: [], className: "", textContent: "", title: "", value: "", hidden: false, disabled: false, listeners: {},
    addEventListener(ev, fn) { this.listeners[ev] = fn; }, setAttribute(k, v) { this[k] = v; },
    append(...kids) { this.children.push(...kids.flat()); }, replaceChildren(...kids) { this.children = [...kids.flat()]; },
    prepend(...kids) { this.children.unshift(...kids.flat()); },
    classList: { set: new Set(), toggle(c, on) { on ? this.set.add(c) : this.set.delete(c); } },
  });
  globalThis.document = { createElement: () => fake(), createTextNode: (t) => ({ text: t }) };
  const names = { "species:1215": "Слоупок Галар", "species:1216": "Слоубро Галар", "s0EB9283": "Галар-браслет" };
  const parent = {
    host: { data: () => ({ species, items: [{ id: 723, name: "s0EB9283", pocket: 1, price: 6000 }] }), spriteSrc: (p) => `data/${p}` },
    text: (id) => names[id] ?? "", speciesName: (r) => names[r.name], record: (slot) => species.find((s) => s.slot === slot),
    openSpecies(slot) { this.opened = slot; }, openMove() {}, openItem() {},
  };
  const elements = new Map();
  const $ = (id) => { if (!elements.has(id)) elements.set(id, fake()); return elements.get(id); };
  const view = dataRenderer(parent, $);
  view.mode = "items";
  const card = view.itemDetail({ id: 723, name: "s0EB9283", pocket: 1, price: 6000, description: null });
  const shown = JSON.stringify(card);
  assert.ok(shown.includes("Галар-браслет"), shown);
  assert.ok(!shown.includes("Use Галар-браслет"), shown);
  assert.ok(!shown.includes("Item 723"), shown);
  delete globalThis.document;
});

test("stale asynchronous cross searches cannot replace newer or invalidated results", async () => {
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  let query = "Alice";
  const view = {
    generation: 1, searchRevision: 1, mode: "species", cross: [],
    $: () => ({ value: query }), ensureCrossNames: () => pending,
    crossSearch: () => ({ matches: (value) => [{ kind: value === "Alice" ? "trainers" : "moves", id: value, rank: 0 }] }),
    list: { setCount() {} }, renderList() {}, filtered: [], strong: 0,
  };
  PokedexView.prototype.updateCross.call(view, "Alice");
  query = "Pound";
  view.$ = () => ({ value: query });
  PokedexView.prototype.updateCross.call(view, "Pound");
  release();
  await pending;
  await Promise.resolve();
  assert.deepEqual(view.cross, [{ kind: "moves", id: "Pound", rank: 0 }]);
  query = "Alice";
  view.$ = () => ({ value: query });
  let secondRelease;
  view.ensureCrossNames = () => new Promise((resolve) => { secondRelease = resolve; });
  PokedexView.prototype.updateCross.call(view, "Alice");
  view.searchRevision += 1;
  secondRelease();
  await Promise.resolve();
  assert.deepEqual(view.cross, []);
});

test("a search reaches the other categories after the active one, labelled", () => {
  const data = {
    moves: [{ id: 7, name: "moves:7", type: 10, damage_category: 0 }, { id: 33, name: "moves:33", type: 0, damage_category: 0 }],
    items: [{ id: 93, name: "items:93" }],
    trainers: [{ id: 20, name: "trainer_names:20", class: "trainer_classes:84", party: [] }],
    encounters: [],
    types: [{ id: 12 }],
  };
  const species = [{ slot: 1, dex: 1, name: "species:1", types: [12] }, { slot: 2, dex: 2, name: "species:2", types: [12] }];
  const names = { "moves:7": "Кулак огня", "moves:33": "Затрещина", "items:93": "Камень солнца", "trainer_names:20": "Элис",
    "trainer_classes:84": "Лидер", "species:1": "Бульбазавр", "species:2": "Ивизавр", "types:12": "Трава" };
  const text = (id) => names[id] ?? "";
  const source = (id) => ({ "trainer_names:20": "Alice" })[id] ?? "";
  assert.deepEqual(crossMatches("элис", data, text, source, "species", species), [{ kind: "trainers", id: 20, name: "Элис · Лидер", rank: 0 }]);
  assert.deepEqual(crossMatches("кулак", data, text, source, "species", species), [{ kind: "moves", id: 7, name: "Кулак огня", rank: 0 }]);
  assert.deepEqual(crossMatches("ивиз", data, text, source, "moves", species), [{ kind: "species", id: 2, name: "#002 Ивизавр", rank: 0 }]);
  assert.deepEqual(crossMatches("камень", data, text, source, "items", species), []);
  assert.deepEqual(crossMatches("", data, text, source, "species", species), []);
  assert.deepEqual(crossMatches("alice", data, text, source, "moves", species).map((h) => h.kind), ["trainers"]);
  assert.deepEqual(crossMatches("трава", data, text, source, "species", species), [{ kind: "types", id: 12, name: "Трава", rank: 0 }]);
  assert.deepEqual(crossMatches("элис", data, text, source, "types", species), [{ kind: "trainers", id: 20, name: "Элис · Лидер", rank: 0 }]);
  // a name match ranks before other-field matches
  const dex = [{ slot: 44, dex: 44, name: "species:44", types: [12], entry: ["s44"] }, { slot: 95, dex: 95, name: "species:95", types: [5] }];
  const words = { "species:44": "Глум", s44: "Они растут в тени", "species:95": "Оникс" };
  assert.deepEqual(crossMatches("они", data, (id) => words[id] ?? names[id] ?? "", source, "trainers", dex).map((h) => h.name), ["#095 Оникс", "#044 Глум"]);
  assert.deepEqual(crossMatches("они", data, (id) => words[id] ?? names[id] ?? "", source, "trainers", dex).map((h) => h.rank), [0, 2]);
  // the English name finds the species and ranks it first
  const english = (id) => ({ "species:95": "Onix", "species:44": "Gloom" })[id] ?? source(id);
  assert.deepEqual(crossMatches("oni", data, (id) => words[id] ?? names[id] ?? "", english, "trainers", dex).map((h) => h.name), ["#095 Оникс"]);
});

test("cross-category aliases rank as names and keep canonical labels", () => {
  const species = [{ slot: 1, dex: 1, name: "species:1", entry: ["s1"] },
    { slot: 175, dex: 175, name: "species:175" }];
  const text = (id) => ({ "species:1": "Бульбазавр", s1: "Тогепи mentioned here", "species:175": "Тогэпи" })[id] ?? "";
  const source = () => "";
  const aliases = (id) => id === "species:175" ? ["Тогепи"] : [];
  const hits = crossMatches("ТОГЕПИ", {}, text, source, "moves", species, 1, aliases);
  assert.deepEqual(hits, [{ kind: "species", id: 175, name: "#175 Тогэпи", rank: 0 }]);
  assert.deepEqual(crossMatches("Тогепи", {}, text, source, "moves", species, 1),
    [{ kind: "species", id: 1, name: "#001 Бульбазавр", rank: 2 }]);
});

test("every data list promotes its exact displayed number without reordering other matches", () => {
  for (const mode of DATA_MODES) {
    const records = [12, 2, 32, 22, 42, 52].map((id) => ({
      id: mode === "locations" ? String(id) : id, name: `${mode}:${id}`,
      battles: [{ id }], areas: [{ id: `${id}.1`, encounters: [] }],
    }));
    const labels = { [`${mode}:12`]: "Other 7", [`${mode}:2`]: "Exact", [`${mode}:32`]: "2 Prefix",
      [`${mode}:22`]: "Contains 2", [`${mode}:42`]: "Other", [`${mode}:52`]: "Other 7" };
    const field = { value: "2" };
    const view = {
      mode, records, $: () => field, source: () => "", list: { setCount() {} },
      showCount() {}, updateCross() {}, entrySearch: PokedexDataView.prototype.entrySearch,
      view: { text: (id) => labels[id] ?? "", otherNames: () => [] },
    };
    const results = (query) => {
      field.value = query;
      PokedexDataView.prototype.applyFilters.call(view);
      return view.filtered.map((row) => Number(row.id));
    };
    for (const query of ["2", " 2 "]) {
      assert.deepEqual(results(query), [2, 32, 22, 12, 42, 52], mode);
      assert.equal(view.strong, 3, mode);
      assert.deepEqual(view.order[0], { own: 0 }, mode);
    }
    assert.deepEqual(results(""), [12, 2, 32, 22, 42, 52], mode);
    assert.deepEqual(results("7"), [12, 52], mode);
    assert.deepEqual(results("Contains"), [22], mode);
    assert.deepEqual(results("missing"), [], mode);
  }
});

test("the moves and locations match their English names and the lists rank names first", () => {
  const moves = [{ id: 1, name: "moves:1", type: 0, damage_category: 0 }, { id: 2, name: "moves:2", type: 0, damage_category: 0 }];
  const text = (id) => ({ "moves:1": "Удар", "moves:2": "Царапанье" })[id] ?? "";
  const english = (id) => ({ "moves:1": "Pound", "moves:2": "Scratch" })[id] ?? "";
  assert.deepEqual(filterEntries(moves, "scra", text, "moves", english).map((r) => r.id), [2]);
  assert.deepEqual(filterEntries(moves, "scra", text, "moves").map((r) => r.id), []);
  assert.deepEqual(rankedBy(moves, (r) => [text(r.name), english(r.name)], "ц").map((r) => r.id), [2, 1]);
  assert.deepEqual(rankedBy(moves, (r) => [text(r.name), english(r.name)], "").map((r) => r.id), [1, 2]);
});

test("English cross-category results match translations and keep source grouping", () => {
  const species = [{ slot: 175, dex: 175, name: "species:175" }];
  const data = { moves: [{ id: 227, name: "moves:227" }], items: [{ id: 1, name: "items:1" }],
    trainers: [{ id: 1, name: "trainer_names:1", class: "trainer_classes:1" },
      { id: 2, name: "trainer_names:2", class: "trainer_classes:1" }],
    encounters: [{ runtime_map: "1.1", map_section: 1, species: 175 }] };
  const english = (id) => ({ "species:175": "Togepi", "moves:227": "Encore", "items:1": "Master Ball",
    "trainer_names:1": "Red", "trainer_names:2": "Blue", "trainer_classes:1": "Trainer", "mapsec:1": "Route 1" })[id] ?? "";
  const translated = (id) => ({ "species:175": "Тогэпи", "moves:227": "На бис", "items:1": "Мастербол",
    "trainer_names:1": "Тренер", "trainer_names:2": "Тренер", "trainer_classes:1": "Тренер", "mapsec:1": "Маршрут 1" })[id] ?? "";
  const aliases = (id) => id === "species:175" ? ["Тогепи"] : [];
  const search = (query) => crossMatches(query, data, english, english, "none", species, 30, aliases, translated);
  for (const query of ["ТОГЭПИ", "Тогепи", "геп", "togepi"]) {
    assert.deepEqual(search(query).filter((hit) => hit.kind === "species").map((hit) => hit.name), ["#175 Togepi"]);
  }
  for (const [query, kind, expected] of [["НА БИ", "moves", "Encore"], ["мастер", "items", "Master Ball"],
    ["марш", "locations", "Route 1"]]) {
    assert.deepEqual(search(query).map((hit) => [hit.kind, hit.name, hit.rank]), [[kind, expected, 0]]);
  }
  assert.deepEqual(search("тренер").map((hit) => hit.name), ["Red · Trainer", "Blue · Trainer"]);
  assert.deepEqual(crossMatches("Encore", data, translated, english, "species").map((hit) => hit.name), ["На бис"]);
  assert.deepEqual(crossMatches("Master Ball", data, translated, english, "species").map((hit) => hit.name), ["Мастербол"]);
});

test("a trainer's battles group into encounters by their base record", () => {
  const b = (id, base, difficulty) => ({ id, base, difficulty, party: [] });
  const battles = [b(77, 76, ["insane"]), b(20, 20, ["difficult"]), b(721, 20, ["insane"]), b(76, 76, ["difficult", "vanilla", "expert"]),
    b(719, 20, ["vanilla"]), b(720, 20, ["expert"]), b(900, null, null)];
  const encounters = encountersOf(battles, TRAINER_DIFFICULTY);
  assert.deepEqual(encounters.map((e) => [e.base, e.battles.map((x) => x.id)]), [[20, [719, 20, 720, 721]], [76, [76, 77]], [900, [900]]]);
  assert.deepEqual(encountersOf([]), []);
});

test("encounters sort by mean party level with record ids breaking ties", () => {
  const battle = (id, levels) => ({ id, party: levels.map((level) => ({ level })) });
  const battles = [battle(10, [20, 70]), battle(20, [40, 40, 40]), battle(40, [34, 36]), battle(30, [35])];
  assert.deepEqual(encountersOf(battles).map((encounter) => encounter.base), [30, 40, 20, 10]);
  assert.deepEqual(battles.map((battle) => battle.id), [10, 20, 40, 30]);
});

function trainerFightHeads(battles, mode) {
  globalThis.document = { createElement: fakeElement, createTextNode: (text) => ({ text }) };
  try {
    const view = dataRenderer({
      host: { data: () => ({ trainer_difficulty: TRAINER_DIFFICULTY }), spriteSrc: (path) => path }, text: () => "", record: () => null,
      abilityLink: () => fakeElement(),
    }, fakeElement);
    view.difficulty = mode;
    return view.trainerDetail({ battles }).children.filter((child) => child.className.includes("dex-encounter"))
      .map((encounter) => ({
        title: encounter.children[0].children[0].children[0],
        variants: encounter.children.slice(1).map((variant) => variant.children[0].children.map((child) => child.children[0])),
      }));
  } finally {
    delete globalThis.document;
  }
}

test("the trainer card numbers the lower-level fight first despite reversed record ids", () => {
  const battles = [{ id: 1, party: [{ level: 80 }] }, { id: 2, party: [{ level: 34 }] }];
  assert.deepEqual(trainerFightHeads(battles, "base"), [
    { title: "Battle 1", variants: [["trainer 2", "Lv 34", "1 Pokémon"]] },
    { title: "Battle 2", variants: [["trainer 1", "Lv 80", "1 Pokémon"]] },
  ]);
});

test("generated story constraints survive difficulty changes and label unknown order", () => {
  globalThis.document = { createElement: fakeElement, createTextNode: (text) => ({ text }) };
  try {
    const record = (id, level, encounters) => ({ id, encounters, party: [{ index: 0, species: 1, level }] });
    const battles = [record(0, 80, [0]), record(1, 10, [1]), record(2, 90, [1]), record(3, 30, [2])];
    const data = { trainer_difficulty: { settings: ["normal", "hard"], labels: ["Normal", "Hard"],
      order: [0, 1] },
      trainer_encounters: [
        { id: 0, order: { status: "constrained", rules: ["gate"] },
          variants: [{ record: 0, role: "opponent", settings: null }] },
        { id: 1, order: { status: "constrained", rules: ["gate"] }, variants: [
          { record: 1, role: "opponent", settings: ["normal"] }, { record: 2, role: "opponent", settings: ["hard"] }] },
        { id: 2, order: { status: "unknown", rules: [] }, variants: [{ record: 3, role: "opponent", settings: null }] },
      ] };
    const view = dataRenderer({ host: { data: () => data, spriteSrc: (path) => path }, text: () => "",
      record: (slot) => ({ slot, name: `species:${slot}` }) }, fakeElement);
    for (const mode of ["0", "1"]) {
      view.difficulty = mode;
      assert.deepEqual(cardEncounters({ battles }, data, mode).shown.map((row) => row.variants[0].battle.id),
        [0, mode === "0" ? 1 : 2, 3]);
      const blocks = view.trainerDetail({ battles }).children.filter((node) => node.className.includes("dex-encounter"));
      assert.ok(blocks.every((node) => node.children[0].children.length === 1));
      assert.deepEqual(blocks.map((node) => node.children[0].children[0].children[0]), ["Battle 1", "Battle 2", "Battle 3"]);
    }
  } finally {
    delete globalThis.document;
  }
});

test("generated encounters show the selected variant, starter alternatives and unused records", () => {
  globalThis.document = { createElement: fakeElement, createTextNode: (text) => ({ text }) };
  try {
    const record = (id, level, encounters) => ({ id, party: [{ index: 0, species: 1, level }], encounters });
    const battles = [record(375, 40, [0]), record(686, 39, [0]), record(687, 41, [0]), record(326, 10, [1]),
      record(327, 10, [1]), record(61, 50, [])];
    const data = { trainer_difficulty: { ...TRAINER_DIFFICULTY, starters: [496, 246, 398] }, trainer_encounters: [
      { id: 0, variants: [{ record: 375, role: "opponent", settings: ["difficult"] },
        { record: 686, role: "opponent", settings: ["vanilla"] }, { record: 687, role: "opponent", settings: ["expert"] },
        { record: 687, role: "opponent", settings: ["expert"], level_above: 39 }] },
      { id: 1, variants: [{ record: 326, role: "opponent", settings: null, starter: 1 },
        { record: 327, role: "partner", settings: null, starter: 2 }] },
    ] };
    const view = dataRenderer({ host: { data: () => data, spriteSrc: (path) => path }, text: () => "",
      record: (slot) => ({ slot, name: `species:${slot}` }), speciesName: (row) => `Species ${row.slot}`,
      abilityLink: () => fakeElement() }, fakeElement);
    view.difficulty = "2";
    const detail = view.trainerDetail({ battles });
    const blocks = detail.children.filter((child) => /dex-encounter|dex-unused/.test(child.className ?? ""));
    const heads = blocks.map((block) => [block.children[0].children[0].children[0],
      block.children.slice(1).map((variant) => variant.children[0].children.map((child) => child.children[0]))]);
    assert.deepEqual(heads, [
      ["Battle 1", [["Expert", "trainer 687", "Lv 41", "1 Pokémon"],
        ["Expert", "Highest party level above 39", "trainer 687", "Lv 41", "1 Pokémon"]]],
      ["Battle 2", [["If you chose Species 496", "trainer 326", "Lv 10", "1 Pokémon"],
        ["If you chose Species 246", "Battle partner", "trainer 327", "Lv 10", "1 Pokémon"]]],
      ["Not found in any battle", [["trainer 61", "Lv 50", "1 Pokémon"]]],
    ]);
    assert.deepEqual(cardEncounters({ battles }, data, null).shown, []);
    assert.equal(settingsLabel(["vanilla", "difficult"], TRAINER_DIFFICULTY), "Vanilla · Difficult");
    assert.deepEqual(variantConditions({ starter: 4 }, data.trainer_difficulty), ["If you chose 4"]);
  } finally {
    delete globalThis.document;
  }
});

test("trainer cards link compatible participants on the selected difficulty", () => {
  globalThis.document = { createElement: fakeElement, createTextNode: (text) => ({ text }) };
  try {
    const trainers = Array.from({ length: 6 }, (_, id) => ({ id, name: `trainer:${id}`, party: [], encounters: [0] }));
    const difficulty = { settings: ["normal", "hard"], order: [0, 1], default: 0,
      labels: ["Normal", "Hard"], starters: [10, 20] };
    const variants = [
      { record: 0, role: "opponent", settings: ["normal"] },
      { record: 1, role: "opponent", settings: ["hard"] },
      { record: 2, role: "second_opponent", settings: ["normal"] },
      { record: 3, role: "partner", settings: ["normal"], starter: 1 },
      { record: 4, role: "partner", settings: ["hard"], starter: 1 },
      { record: 5, role: "partner", settings: null, starter: 2 },
    ];
    const data = { trainers, trainer_difficulty: difficulty, trainer_encounters: [{ id: 0, variants }] };
    const group = { battles: trainers.slice(0, 2) };
    const participantIds = (group, mode) => cardEncounters(group, data, mode).shown
      .flatMap((row) => row.variants.flatMap((variant) => variant.participants.map((other) => other.record)));
    assert.deepEqual(participantIds(group, "0"), [2, 3, 5]);
    assert.deepEqual(participantIds(group, "1"), [4, 5]);
    assert.deepEqual(cardEncounters({ battles: [trainers[2]] }, data, "1").shown, []);
    assert.deepEqual(participantIds({ battles: [trainers[3]] }, "0"), [0, 2]);
    let target;
    const view = dataRenderer({ host: { data: () => data }, text: (id) => id,
      record: (slot) => ({ slot, name: `species:${slot}` }), goTo: (value) => { target = value; } }, fakeElement);
    view.difficulty = "0";
    const related = view.trainerDetail(group).children[1].children[1].children[2];
    assert.deepEqual(related.children.filter((_, index) => index % 2 === 0).map((node) => node.children[0]),
      ["Second opponent", "Battle partner", "Battle partner"]);
    const button = related.children[1].children[0].children[0];
    assert.equal(button.children[0], "trainer:2");
    button.listeners.click();
    assert.deepEqual(target, { kind: "trainers", id: 2 });
    assert.equal(related.children[3].children[0].children[1], " · If you chose species:10");
    const ally = view.trainerDetail({ battles: [trainers[3]] }).children[1].children[1];
    assert.equal(ally.children[2].children[0].children[0], "First opponent");
  } finally {
    delete globalThis.document;
  }
});

test("the default moveset deduplicates the last four ROM entries", () => {
  const species = { learnset: [
    { method: 0, level: 1, move: 33, index: 0 }, { method: 0, level: 1, move: 45, index: 1 }, { method: 0, level: 7, move: 52, index: 2 },
    { method: 0, level: 13, move: 44, index: 3 }, { method: 0, level: 19, move: 108, index: 4 }, { method: 0, level: 25, move: 163, index: 5 },
    { method: 1, level: null, move: 264, index: 0 }, { method: 0, level: 31, move: 53, index: 6 },
  ] };
  assert.deepEqual(defaultMoves(species, 25), [52, 44, 108, 163]);
  assert.deepEqual(defaultMoves(species, 10), [33, 45, 52]);
  assert.deepEqual(defaultMoves(species, 100), [44, 108, 163, 53]);
  assert.deepEqual(defaultMoves({ learnset: [
    { method: 0, level: 0, move: 182 }, { method: 0, level: 1, move: 106 }, { method: 0, level: 8, move: 182 },
  ] }, 42), [182, 106]);
  assert.deepEqual(defaultMoves({ learnset: [1, 2, 3, 4, 4].map((move, level) => ({ method: 0, level, move })) }, 42), [2, 3, 4]);
  assert.equal(defaultMoves({ learnset: null }, 10), null);
  assert.equal(defaultMoves(undefined, 10), null);
});

test("the learner index lists a form through its borrowed rows", () => {
  const species = [{ slot: 94, learnset: [{ method: 1, move: 247 }] },
    { slot: 877, learnset: [{ method: 0, move: 94, level: 1 }, { method: 1, move: 247, source: 94 }] }];
  assert.deepEqual(learnerIndex(species).get(247).map((e) => e.slot), [94, 877]);
});

test("the list puts the other categories' name matches before the weak rows of the active one", () => {
  const cross = [{ rank: 0 }, { rank: 2 }, { rank: 1 }, {}];
  assert.deepEqual(listOrder(2, 4, cross), [{ own: 0 }, { own: 1 }, { cross: 0 }, { cross: 2 }, { own: 2 }, { own: 3 }, { cross: 1 }, { cross: 3 }]);
  assert.deepEqual(listOrder(0, 1, [{ rank: 0 }]), [{ cross: 0 }, { own: 0 }]);
  assert.deepEqual(listOrder(3, 3, []), [{ own: 0 }, { own: 1 }, { own: 2 }]);
  assert.deepEqual(listOrder(5, 2, []), [{ own: 0 }, { own: 1 }]);
});

test("the learner filter matches a Russian name in English mode", async () => {
  const fake = () => ({
    children: [], className: "", textContent: "", title: "", value: "", hidden: false,
    disabled: false, style: {}, scrollTop: 0, clientHeight: 400, listeners: {},
    addEventListener(ev, fn) { this.listeners[ev] = fn; }, setAttribute(k, v) { this[k] = v; }, click() {},
    append(...kids) { this.children.push(...kids.flat()); }, prepend(...kids) { this.children.unshift(...kids.flat()); },
    replaceChildren(...kids) { this.children = [...kids.flat()]; },
    classList: { set: new Set(), toggle(c, on) { on ? this.set.add(c) : this.set.delete(c); } },
  });
  globalThis.document = { createElement: () => fake(), createTextNode: (value) => ({ text: value }) };
  globalThis.requestAnimationFrame = (fn) => fn();
  const { PokedexView } = await import("../js/pokedex/pokedex.js");
  const elements = new Map();
  const $ = (id) => {
    if (!elements.has(id)) elements.set(id, fake());
    return elements.get(id);
  };
  const species = [{ slot: 94, dex: 94, name: "species:94", types: [7], learnset: [{ method: 0, level: 1, move: 1 }] }];
  const english = { "moves:1": "Thunderbolt", "species:94": "Gengar" };
  let searches = 0;
  const view = new PokedexView({
    data: () => ({ moves: [move], species, types: [] }),
    entry: async (id) => ({ source_text: english[id] ?? "", translation: "" }),
    searchTexts: async () => {
      searches++;
      return [{ code: "ru", texts: { "species:94": ["Gengar", "Генгар"] }, aliases: {} }];
    },
    draft: () => false, spriteSrc: (path) => path, setHash() {},
  }, $);
  view.setLang("en");
  view.loaded = true;
  await view.setMode("moves");
  view.filterSets.moves.set("learner", "генгар");
  assert.deepEqual(view.dataView.filtered, []);
  await view.catalogsLoad;
  assert.deepEqual(view.dataView.filtered.map((row) => row.id), [1]);
  const learnerOptions = view.filterSets.moves.controls.get("learner").root.children[1];
  assert.deepEqual(learnerOptions.children.map((option) => option.value), ["#094 Gengar", "Gengar", "Генгар"]);
  assert.equal(searches, 1);
  view.dataView.list.list.scrollTop = 500;
  $("x-search").value = "Thunderbolt";
  view.search();
  assert.equal(view.dataView.list.list.scrollTop, 0);
  await view.crossNames;
  delete globalThis.requestAnimationFrame;
  delete globalThis.document;
});

test("the data search and the cross matches read every name an id has in other languages", () => {
  // English shown; other names from German and Russian catalogs
  const english = (id) => ({ "moves:1": "Pound", "moves:2": "Scratch", "species:94": "Gengar", "trainer_names:1": "Joey",
    "trainer_classes:1": "Youngster", "mapsec:93": "Tehl Town" })[id] ?? "";
  const names = { "moves:1": ["Pound", "Затрещина"], "moves:2": ["Scratch", "Царапанье"], "species:94": ["Gengar", "Генгар"],
    "trainer_names:1": ["Joey", "Джоуи"], "mapsec:93": ["Tehl Town", "Городок Тель"] };
  const other = (id) => names[id] ?? [];
  const moves = [{ id: 1, name: "moves:1", type: 0, damage_category: 0 }, { id: 2, name: "moves:2", type: 0, damage_category: 0 }];
  assert.deepEqual(filterEntries(moves, "затрещ", english, "moves", other).map((r) => r.id), [1]);
  assert.deepEqual(filterEntries(moves, "pound,", english, "moves", other).map((r) => r.id), []); // names never run together
  assert.deepEqual(rankedBy(moves, (r) => [english(r.name), other(r.name)], "цара").map((r) => r.id), [2, 1]);
  const trainers = trainerGroups([{ id: 1, name: "trainer_names:1", class: "trainer_classes:1", party: [{ species: 94 }] }], english);
  assert.deepEqual(filterEntries(trainers, "джоуи", english, "trainers", other).map((r) => r.id), [1]);
  const sections = sectionGroups([{ runtime_map: "3.1", map: "3.1", map_section: 93, species: 94 }]);
  assert.deepEqual(filterEntries(sections, "городок", english, "locations", other).map((r) => r.id), ["93"]);
  const species = [{ slot: 94, dex: 94, name: "species:94", types: [7] }];
  const data = { moves, trainers: [{ id: 1, name: "trainer_names:1", class: "trainer_classes:1", party: [{ species: 94 }] }],
    encounters: [{ runtime_map: "3.1", map_section: 93, species: 94 }] };
  const search = (query, active) => crossMatches(query, data, english, english, active, species, 30, () => [], other)
    .map((hit) => [hit.kind, hit.name, hit.rank]);
  assert.deepEqual(search("генгар", "moves"), [["species", "#094 Gengar", 0]]);
  assert.deepEqual(search("затрещ", "species"), [["moves", "Pound", 0]]);
  assert.deepEqual(search("джоуи", "species"), [["trainers", "Joey · Youngster", 0]]);
  assert.deepEqual(search("тель", "species"), [["locations", "Tehl Town", 1]]);
});

test("unresolved trainer predicates keep complete outcomes only in technical data", (context) => {
  globalThis.document = { createElement: (tag) => ({ ...fakeElement(), tag }), createTextNode: (text) => ({ text }) };
  context.after(() => { setCatalog(null); delete globalThis.document; });
  const { view, group, member, data } = trainerStatsFixture();
  data.trainer_ivs.difficulties = ["difficult", "vanilla", "expert", "insane"];
  data.trainer_ivs.ev_conditions = [{ flag: "0x821", set: "After badge", clear: "Before badge" },
    { flag: "0x123" }, { flag: "0x456", set: "Flag {flag} set", clear: "Flag {flag} clear" }];
  member.evs[3] = { alternatives: [
    { evs: [0, 0, 0, 0, 0, 0], when: { "0x821": [0] } },
    { evs: [252, 0, 0, 0, 0, 0], when: { "0x821": [1], "0x123": [1] } },
  ] };
  const write = { difficulties: [3], level: 1, moves: [1, 2], item: 7, when: { "0x456": [0] } };
  member.write_changes = [write];
  member.species_changes = [{ species: 999, difficulties: [0], write_changes: [
    { ...write, when: { "var:0x4321": [2] } }], bands: [
    { evs: { 0: { alternatives: [{ evs: [1, 0, 0, 0, 0, 0], when: { "0x789": [0] } }] } } }],
  }];
  view.difficulty = "3";
  const detail = view.trainerDetail(group);
  const technical = detail.children.at(-1);
  assert.equal(technical.className, "block dex-record");
  assert.ok(!technical.open);
  assert.doesNotMatch(JSON.stringify(detail.children.slice(0, -1)), /0x(?:123|456|4321|789)|After badge|EV 2:/);
  assert.match(JSON.stringify(detail.children.slice(0, -1)), /EV 1: Before badge/);
  const rows = JSON.parse(view.technicalConditions(group.battles[0])[0][1].children[0]);
  assert.equal(rows.length, 4);
  assert.deepEqual(rows.find((row) => row.path === "evs.3.alternatives.1").when, { "0x821": [1], "0x123": [1] });
  assert.deepEqual(rows.find((row) => row.path === "write_changes.0").moves, [1, 2]);
  for (const value of ["0x123", "0x456", "var:0x4321", "0x789"]) assert.ok(JSON.stringify(technical).includes(value));
  assert.equal(view.evCondition({ "0x821": [0, 1] }, member), null);
  setCatalog({ "After badge": "Localized milestone" });
  assert.equal(view.evCondition({ "0x821": [1] }, member), "Localized milestone");
  assert.equal(view.evCondition({}, member), "");
});
