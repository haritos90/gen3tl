import assert from "node:assert/strict";

import { test } from "node:test";

import { SPECIES_SORT_KEYS, speciesSort, speciesListFields, sortSpeciesIndices, abilityChanges, abilityChangeText, chipText, compareSpecies, conditionParts, detailOf, displayName, evolutionCondition, matchRank, edgeItems, eggGroups, evolutionGrid, evolutionPaths, evolutionTree, filterSpecies, formLabel, genderRatio, growthRate, itemIcon, itemKeys, itemLink, itemName, itemText, metric, speciesMatches, typeChip, recordIds, rowLabel, dexNumber, numberedName, statRows, statRanks, tierOf, megaLetter, sourceParts, speciesSearchNames, baseSlot, formesOf, formSectionRecords, speciesGroupSearchNames, createSpeciesSearch, speciesFamilyIndex } from "../js/pokedex/pokedex.js";

import { DexMemory, searchTexts, textsCache } from "../js/dexpage.js";

import { ENCOUNTER_LABELS, fakeFetch, fakeStorage, stubPage } from "./fixture.mjs";

import { setCatalog, setLabelSource, setUiLanguage, SOURCE_LANGUAGE } from "../js/ui.js";

import { CHART_ORDER } from "../js/typechart.js";

test("species locations label every column and retain location navigation", async (context) => {
  const { $ } = stubPage(context, "pokedex");
  const { PokedexView } = await import("../js/pokedex/pokedex.js");
  context.after(() => { setLabelSource(null); setUiLanguage(null); });
  const encounter = { runtime_map: "43.5", map_section: 7, species: 1, method: "land", variant: "day",
    variants: ["day", "morning", "evening", "night"], min_level: 8, max_level: 12, percentage: 30 };
  const data = { species: [{ slot: 1, dex: 1, name: "species:1" }], encounters: [encounter],
    location_details: [{ runtime_map: "43.4", map_section: 7 }], map_sections: [{ id: 7, name: "s0000001" }], sources: [] };
  const view = new PokedexView({ data: () => data, spriteSrc: (path) => path }, $);
  const opened = [];
  view.openLocation = (id) => opened.push(id);
  view.texts.set("s0000001", { source_text: "Test place", translation: "Test place" });
  for (const { code, headings, times, display } of ENCOUNTER_LABELS) {
    const page = Object.fromEntries(ENCOUNTER_LABELS[0].headings.map((key, i) => [key, headings[i]]));
    Object.assign(page, Object.fromEntries(["day", "morning", "Evening", "night"].map((key, i) => [key, times[i]])));
    view.host.pageLabels = () => page;
    setUiLanguage(code);
    view.useLabels();
    view.renderSpecies(1, false);
    const table = $("x-locations").querySelector("table");
    assert.equal($("x-locations").children[0].className, "dex-location-table");
    const headers = table.querySelectorAll("th");
    assert.deepEqual(headers.map((header) => header.textContent), headings);
    assert.ok(headers.every((header) => header.getAttribute("scope") === "col"));
    const cells = table.querySelectorAll("td");
    assert.deepEqual(cells.map((cell) => cell.textContent), ["Test place", "2", `Grass · ${display}`, "Lv 8–12", "30%"]);
    assert.ok(!table.textContent.includes(encounter.runtime_map));
    cells[0].querySelector("button").listeners.click();
    assert.equal(opened.at(-1), "7");
  }
  data.encounters = [];
  view.renderSpecies(1, false);
  assert.equal($("x-locations").children.length, 0);
  assert.equal($("x-locations-empty").textContent, "Not found in the wild");
});

test("Forms excludes classified Mega targets across regional branches without altering families", () => {
  const records = [
    { slot: 1, dex: 1, evolution: [{ method: 99, target: 3, kind: "mega" }, { method: 253, target: 4 }] },
    { slot: 2, dex: 1, base_species: 1, name: "Mega is only a name", evolution: [] },
    { slot: 3, dex: 1, base_species: 1, evolution: [{ method: 254, parameter: 0, target: 1 }] },
    { slot: 4, dex: 1, base_species: 1, evolution: [] },
    { slot: 5, dex: 1, base_species: 1, evolution: [] },
  ];
  const index = speciesFamilyIndex(records);
  const before = JSON.stringify(records);
  for (const record of records) {
    assert.deepEqual(formSectionRecords(record, records, index).map(r => r.slot), [1, 2, 4, 5]);
    assert.deepEqual(formSectionRecords(record, records).map(r => r.slot), [1, 2, 4, 5]);
    assert.equal(formesOf(record, records, index).length, 5);
  }
  assert.deepEqual(evolutionTree(records[1], records), { slot: 2, branches: [] });
  assert.ok(evolutionGrid(evolutionTree(records[0], records)).cells.some(c => c.kind === "species" && c.slot === 3));
  assert.equal(JSON.stringify(records), before);
  const unclassified = records.map(r => ({ ...r, evolution: (r.evolution ?? []).map(({ kind, ...edge }) => edge) }));
  assert.equal(formSectionRecords(unclassified[0], unclassified).length, 5);
});

test("Mega-only families leave a singleton and non-Mega families are preserved", () => {
  const mega = [{ slot: 1, dex: 1, evolution: [{ target: 2, kind: "mega" }] },
    { slot: 2, dex: 1, base_species: 1, evolution: [] }];
  for (const record of mega) assert.deepEqual(formSectionRecords(record, mega).map(r => r.slot), [1]);
  const ordinary = mega.map(r => ({ ...r, evolution: [] }));
  for (const record of ordinary) assert.deepEqual(formSectionRecords(record, ordinary), formesOf(record, ordinary));
});

test("saved species chips restore filters, pin in order, reopen and clear", async (context) => {
  const fake = () => ({
    children: [], className: "", textContent: "", value: "", hidden: false, style: {},
    scrollTop: 0, clientHeight: 400, listeners: {},
    addEventListener(event, fn) { this.listeners[event] = fn; },
    setAttribute(key, value) { this[key] = value; },
    append(...kids) { this.children.push(...kids.flat()); },
    replaceChildren(...kids) { this.children = kids.flat(); },
    classList: { toggle() {} },
  });
  globalThis.document = { createElement: fake, createTextNode: (text) => ({ text }) };
  globalThis.requestAnimationFrame = (fn) => fn();
  const { PokedexView } = await import("../js/pokedex/pokedex.js");
  const { setLabelSource } = await import("../js/ui.js");
  context.after(() => {
    delete globalThis.document;
    delete globalThis.requestAnimationFrame;
    setLabelSource(null);
  });
  const elements = new Map();
  const $ = (id) => {
    if (!elements.has(id)) elements.set(id, fake());
    return elements.get(id);
  };
  const storage = fakeStorage();
  let memory = new DexMemory("game", "ru", () => storage);
  memory.state.modes.species.filters = { search: "species", type: "12" };
  memory.state.modes.moves.filters = { power: { op: ">=", a: "50" } };
  memory.open("species", 3);
  const data = { species: Array.from({ length: 12 }, (_, i) => ({ slot: i + 1, dex: i + 1,
    name: `species:${i + 1}`, types: [12], sprite: { front: `${i + 1}-front.png` } })),
    moves: [{ id: 33, name: "moves:33", power: 60, type: 12 }], items: [{ id: 4, name: "items:4" }] };
  const hashes = [];
  let loadedData = null;
  const view = new PokedexView({
    load: async () => { loadedData = data; }, data: () => loadedData, memory: () => memory,
    entry: async (id) => ({ source_text: id, translation: id }), spriteSrc: (path) => `sprites/${path}`,
    setHash: (id) => hashes.push(id),
  }, $);
  view.reset(memory.want(null));
  await view.show();
  assert.deepEqual(memory.state.modes.species.filters, { search: "species", type: "12" });
  assert.equal(view.cur, 3);
  assert.equal(hashes.at(-1), "species/3");
  assert.equal($("x-search").value, "species");
  assert.equal(view.filterSets.species.controls.get("type").select.value, "12");
  assert.equal($("x-recent").hidden, false);
  assert.equal($("x-favourites").hidden, true);
  const chips = (id) => (id === "x-recent" ? $(id).children : $(id).children[1].children);
  assert.equal(chips("x-recent")[0].children[0].children[0].src, "sprites/3-front.png");
  assert.equal(chips("x-recent")[0].children[0].children.length, 1); // the sprite alone
  assert.equal(chips("x-recent")[0].children[0]["aria-label"], "species:3");
  $("x-pin").listeners.click();
  assert.equal($("x-pin")["aria-pressed"], "true");
  assert.equal($("x-recent").hidden, true);
  assert.equal($("x-favourites").hidden, false);
  for (let slot = 4; slot <= 12; slot += 1) view.open(slot);
  assert.equal(chips("x-recent").length, 9);
  assert.equal(chips("x-recent")[0].className, "dex-saved-chip cur"); // the open species is framed
  assert.equal(chips("x-recent")[1].className, "dex-saved-chip");
  $("x-pin").listeners.click();
  assert.deepEqual(memory.state.modes.species.favourites, [3, 12]);
  assert.equal(chips("x-favourites")[0].children[0].children[1].children[0], "species:3"); // favourites keep names
  await chips("x-favourites")[0].children[0].listeners.click();
  assert.equal(view.cur, 3);
  assert.ok(!memory.state.modes.species.recent.includes(3));
  chips("x-favourites")[0].children[1].listeners.click();
  assert.deepEqual(memory.state.modes.species.favourites, [12]);
  assert.equal($("x-pin")["aria-pressed"], "false");
  await chips("x-recent")[0].children[0].listeners.click();
  assert.equal(view.cur, 11);
  $("x-search").value = "no matches";
  view.applyFilters();
  assert.deepEqual(view.filtered, []);
  memory = new DexMemory("game", "ru", () => storage);
  view.reset(memory.want({ kind: "species", id: 2 }));
  await view.show();
  assert.equal(view.cur, 2);
  assert.equal($("x-search").value, "no matches");
  assert.equal($("x-favourites").hidden, false);
  await view.goTo({ kind: "moves", id: 33 });
  assert.deepEqual(memory.state.modes.moves.filters.power, { op: ">=", a: "50" });
  assert.equal(memory.state.modes.moves.last, 33);
  assert.deepEqual(memory.state.modes.moves.recent, []);
  for (const id of ["x-recent", "c-recent", "x-card-recent", "c-card-recent"]) assert.equal($(id).hidden, true, id);
  await view.goTo({ kind: "items", id: 4 });
  assert.equal($("x-card-recent").hidden, false);
  assert.equal($("x-card-recent").children[0]["data-key"], "items:4:recent");
  await view.setMode("moves");
  assert.equal(view.dataView.cur, 33);
  await view.setMode("species");
  assert.equal(view.cur, 2);
  assert.equal(memory.state.mode, "species");
  const order = [...view.filtered], history = [...hashes];
  $("x-list").scrollTop = 173;
  const records = view.records;
  view.setLang("en");
  assert.equal(view.cur, 2);
  assert.equal($("x-list").scrollTop, 173);
  assert.deepEqual(view.filtered, order);
  assert.equal(view.records, records);
  assert.deepEqual(hashes, history);
  memory = new DexMemory("blocked", "ru", () => fakeStorage(true));
  view.open(1);
  assert.equal(memory.available, false);
  for (const id of ["x-recent", "x-favourites", "x-pin"]) assert.equal($(id).hidden, true);
  // A species opened from another tab adds one history entry.
  await view.goTo({ kind: "items", id: 4 });
  const pushes = hashes.length;
  await view.openSpecies(5);
  assert.equal(view.mode, "species");
  assert.equal(view.cur, 5);
  assert.deepEqual(hashes.slice(pushes), ["species/5"]);
});

const records = [
  {
    slot: 1, dex: 1, name: "species:1", types: [12, 3], base_stats: {
      hp: 45, attack: 49, defense: 49, speed: 45, sp_attack: 65, sp_defense: 65,
    },
    ev_yield: { hp: 0, attack: 0, defense: 0, speed: 2, sp_attack: 1, sp_defense: 0 },
    abilities: [65], height: 7, weight: 69, category: "pokedex_categories:1",
    catch_rate: 45, base_exp: 64, growth: 3, egg_groups: [1, 7], gender_ratio: 31,
    evolution: [{ method: 4, parameter: 16, target: 2, extra: 0 }],
    entry: ["s0444CB2"], offset: "0x19e0cb8", dex_offset: "0x1a357f0",
  },
  {
    slot: 25, dex: 25, name: "species:25", types: [13, 13], base_stats: {
      hp: 35, attack: 55, defense: 40, speed: 90, sp_attack: 50, sp_defense: 50,
    },
    abilities: [9, 9], height: 4, weight: 60, category: "pokedex_categories:25",
    entry: ["s044561D"],
  },
  {
    slot: 1292, dex: 892, name: "species:1292", types: [2, 17], base_stats: {
      hp: 100, attack: 130, defense: 100, speed: 97, sp_attack: 63, sp_defense: 60,
    },
    abilities: [33], height: 19, weight: 1050, category: "pokedex_categories:892",
    entry: ["s044B30B"],
  },
];

const ivysaur = {
  slot: 2, dex: 2, name: "species:2", types: [12, 3], base_stats: records[0].base_stats,
  abilities: [65], category: "pokedex_categories:2", entry: [], evolution: [],
  sprite: { front: "sprites/2-front.png", back: "sprites/2-back.png", shiny: "sprites/2-shiny.png", back_shiny: "sprites/2-back-shiny.png" },
};

const charizards = [
  { slot: 6, dex: 6, name: "species:6", evolution: [
    { method: 254, parameter: 447, target: 870 },
    { method: 254, parameter: 448, target: 871 },
    { method: 253, parameter: 0, target: 1261 },
  ] },
  { slot: 870, dex: 6, name: "species:870", evolution: [{ method: 254, parameter: 0, target: 6 }] },
  { slot: 871, dex: 6, name: "species:871", evolution: [{ method: 254, parameter: 0, target: 6 }] },
  { slot: 1261, dex: 6, name: "species:1261", evolution: [{ method: 253, parameter: 0, target: 6 }] },
  { slot: 999, dex: 6, name: "species:999", evolution: [] },
];

const text = (id) => ({
  "species:1": "Бульбазавр", "species:2": "Ивизавр", "species:25": "", "species:1292": "Уршифу",
  "species:6": "Чаризард", "species:870": "Чаризард", "species:871": "Чаризард", "species:1261": "Чаризард",
  "items:447": "Чаризардит X", "items:448": "Чаризардит Y",
  "types:1": "Боевой", "types:12": "Трава", "types:3": "Яд", "types:13": "Электро",
  "types:2": "Летающий", "types:17": "Тёмный",
  "abilities:65": "Зарастание", "abilities:9": "Статика", "abilities:33": "Невидимый кулак",
  "pokedex_categories:1": "Семя", "pokedex_categories:25": "Мышь", "pokedex_categories:892": "",
  "s0444CB2": "С самого рождения он носит на спине семя.", "s044561D": "",
})[id] ?? "";

test("the stats table rows carry a bar over 255 and end with the total", () => {
  const rows = statRows(records[0].base_stats);
  assert.deepEqual(rows.map((r) => [r.label, r.value]), [["HP", 45], ["Attack", 49], ["Defense", 49],
    ["Sp. Attack", 65], ["Sp. Defense", 65], ["Speed", 45], ["Total", 318]]);
  assert.equal(rows[0].percent, 17.6);
  assert.equal(rows[3].percent, 25.5);
  assert.equal(rows[6].total, true);
  assert.deepEqual(statRows(null), []);
});

test("stats rank against every dex species in five tiers", () => {
  const stat = (hp) => ({ hp, attack: hp, defense: hp, speed: hp, sp_attack: hp, sp_defense: hp });
  const species = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100].map((hp, i) => ({ slot: i + 1, dex: i + 1, base_stats: stat(hp) }));
  species.push({ slot: 99, dex: 0, base_stats: stat(999) }); // a placeholder stays out
  const ranks = statRanks(species);
  assert.deepEqual(ranks.hp, [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]);
  assert.deepEqual(ranks.total, ranks.hp.map((v) => v * 6));
  assert.deepEqual(tierOf(ranks.hp, 10), { percentile: 5, tier: 0, name: "worst" });
  assert.deepEqual(tierOf(ranks.hp, 50), { percentile: 45, tier: 2, name: "average" });
  assert.deepEqual(tierOf(ranks.hp, 100), { percentile: 95, tier: 4, name: "best" });
  assert.deepEqual(tierOf(ranks.hp, 70), { percentile: 65, tier: 3, name: "good" });
  assert.deepEqual(tierOf(ranks.hp, 35), { percentile: 30, tier: 1, name: "bad" }); // between two species
  assert.equal(tierOf([], 5), null);
  const rows = statRows(stat(100), ranks);
  assert.deepEqual(rows.map((r) => r.label + " " + r.tier), ["HP 4", "Attack 4", "Defense 4", "Sp. Attack 4", "Sp. Defense 4", "Speed 4", "Total 4"]);
  assert.equal(rows[0].tierName, "best");
  assert.equal(statRows(stat(100)).some((r) => "tier" in r), false);
});

test("heights and weights come from the stored decimetres and hectograms", () => {
  assert.equal(metric(7), "0.7");
  assert.equal(metric(1050), "105");
  assert.equal(metric(null), "");
  assert.equal(metric(undefined), "");
});

test("positive dex numbers sort before internal placeholders", () => {
  const egg = { slot: 252, dex: 0 };
  const missing = { slot: 253, dex: null };
  assert.deepEqual([egg, records[1], missing, records[0]].sort(compareSpecies).map((r) => r.slot),
    [1, 25, 252, 253]);
});

test("gender ratios cover every threshold and special value", () => {
  assert.equal(genderRatio(0), "100% male / 0% female");
  assert.equal(genderRatio(31), "87.8% male / 12.2% female");
  assert.equal(genderRatio(63), "75.2% male / 24.8% female");
  assert.equal(genderRatio(127), "50% male / 50% female");
  assert.equal(genderRatio(191), "24.8% male / 75.2% female");
  assert.equal(genderRatio(223), "12.2% male / 87.8% female");
  assert.equal(genderRatio(254), "0% male / 100% female");
  assert.equal(genderRatio(255), "Genderless");
  assert.equal(genderRatio(null), "");
});

test("growth rates and egg groups use every proven name", () => {
  const rates = ["Medium Fast", "Erratic", "Fluctuating", "Medium Slow", "Fast", "Slow"];
  rates.forEach((name, id) => assert.equal(growthRate(id), name));
  const groups = ["Monster", "Water 1", "Bug", "Flying", "Field", "Fairy", "Grass", "Human-Like",
    "Water 3", "Mineral", "Amorphous", "Water 2", "Ditto", "Dragon", "Undiscovered"];
  assert.equal(eggGroups(groups.map((_, index) => index + 1)), groups.join(" / "));
  assert.equal(eggGroups([3, 3]), "Bug");
  assert.equal(growthRate(99), "99");
  assert.equal(eggGroups([99]), "99");
  assert.equal(growthRate(null), "");
  assert.equal(eggGroups(null), "");
});

test("every record names the catalog ids its fields resolve", () => {
  assert.deepEqual(recordIds(records[0]),
    ["species:1", "pokedex_categories:1", "s0444CB2", "types:12", "types:3", "abilities:65"]);
  assert.deepEqual(recordIds({ name: "species:1" }), ["species:1"]);
});

test("search matches dex number, slot, ids and resolved text", () => {
  assert.deepEqual(filterSpecies(records, "", text), [0, 1, 2]);
  assert.deepEqual(filterSpecies(records, "892", text), [2]);
  assert.deepEqual(filterSpecies(records, "уршифу", text), [2]);
  assert.deepEqual(filterSpecies(records, "1292", text), [2]);
  assert.deepEqual(filterSpecies(records, "s0444CB2", text), [0]);
  assert.deepEqual(filterSpecies(records, "", text, "3"), [0]);
  assert.deepEqual(filterSpecies(records, "", text, "13"), [1]);
  assert.deepEqual(filterSpecies(records, "no such species", text), []);
  // the English name matches as well
  const source = (id) => (id === records[2].name ? "Urshifu" : "");
  assert.deepEqual(filterSpecies(records, "urshi", text, null, source), [2]);
  assert.deepEqual(filterSpecies(records, "urshi", text), []);
});

test("optional aliases find species without renaming them", () => {
  const species = [{ slot: 175, dex: 175, name: "species:175", types: [18] },
    { slot: 176, dex: 176, name: "species:176", types: [18, 2] }];
  const text = (id) => ({ "species:175": "Тогэпи", "species:176": "Тогэтик" })[id] ?? "";
  const source = (id) => ({ "species:175": "Togepi", "species:176": "Togetic" })[id] ?? "";
  const aliases = (id) => ({ "species:175": ["Тогепи"], "species:176": ["Тогетик", "Togechikku"] })[id] ?? [];
  for (const query of ["Тогепи", " ТОГЕПИ ", "геп"]) {
    assert.deepEqual(filterSpecies(species, query, text, null, source, aliases), [0]);
  }
  assert.deepEqual(filterSpecies(species, "Тогепи", text, null, source), []);
  assert.deepEqual(filterSpecies(species, "Тогепи", text, { types: [2] }, source, aliases), []);
  assert.deepEqual(filterSpecies(species, "Тогэпи", text, null, source, aliases), [0]);
  assert.deepEqual(filterSpecies(species, "togepi", text, null, source, aliases), [0]);
  assert.deepEqual(filterSpecies(species, "togech", text, null, source, aliases), [1]);
  assert.equal(matchRank(speciesSearchNames(species[0], text, source, aliases), "тоге"), 0);
  assert.equal(matchRank(speciesSearchNames(species[0], text, source, aliases), "геп"), 1);
  assert.deepEqual(rowLabel(species[0], text), { dex: "175", name: "Тогэпи" });
});

test("the advanced filter combines types, an ability and a learnable move", () => {
  const bulbasaur = { ...records[0], learnset: [{ method: 0, level: 1, move: 33 }, { method: 1, level: null, move: 264 }] };
  const pikachu = { ...records[1], ability_details: [{ id: 9, regular: true }, { id: 31, hidden: true }], learnset: [{ method: 3, level: null, move: 264 }] };
  const all = [bulbasaur, pikachu, records[2]];
  assert.deepEqual(filterSpecies(all, "", text, { types: [12, 3] }), [0]);
  assert.deepEqual(filterSpecies(all, "", text, { types: [3, 12] }), [0]);
  assert.deepEqual(filterSpecies(all, "", text, { types: [12, 13] }), []);
  assert.deepEqual(filterSpecies(all, "", text, { ability: 31 }), [1]);     // a hidden ability counts
  assert.deepEqual(filterSpecies(all, "", text, { ability: 65 }), [0]);
  assert.deepEqual(filterSpecies(all, "", text, { moves: new Set([264]) }), [0, 1]);
  assert.deepEqual(filterSpecies(all, "", text, { moves: new Set([33]) }), [0]);
  assert.deepEqual(filterSpecies(all, "", text, { moves: new Set() }), []);
  assert.deepEqual(filterSpecies(all, "уршифу", text, { types: [2] }), [2]);
  assert.deepEqual(filterSpecies(all, "", text, { types: [13], moves: new Set([264]) }), [1]);
  assert.equal(speciesMatches(records[2], null), true);
});

test("the learnable move filter matches both languages without duplicate suggestions", async () => {
  const { FILTER_SETS, FilterSet } = await import("../js/filters.js");
  const data = {
    moves: [{ id: 33, name: "moves:33" }, { id: 1, name: "moves:1" }],
  };
  const context = {
    moves: data.moves,
    text: (id) => ({ "moves:33": "Бодание", "moves:1": "Same" })[id] ?? "",
    otherNames: (id) => (id === "moves:33" ? ["Tackle"] : []),
  };
  const set = new FilterSet("species", FILTER_SETS.species, {});
  const move = FILTER_SETS.species.find((entry) => entry.key === "move");
  const all = [
    { ...records[0], learnset: [{ move: 33 }] },
    { ...records[1], learnset: [{ move: 33 }] },
    { ...records[2], learnset: [{ move: 1 }] },
  ];
  let expected = null;
  for (const query of [" БОДАНИЕ ", "tAcKlE"]) {
    const values = set.prepare({ move: query }, context);
    const matched = all.map((record, index) => index).filter((index) => set.matches(all[index], values, context));
    if (expected) assert.deepEqual(matched, expected);
    else expected = matched;
  }
  assert.deepEqual(expected, [0, 1]);
  assert.deepEqual(move.options(context).map((option) => option.value), ["Бодание", "Tackle", "Same"]);
});

test("a learnable move type combines every learnset method with move, species and search filters", async () => {
  const { FILTER_SETS, FilterSet } = await import("../js/filters.js");
  const moves = [{ id: 33, name: "Tackle", type: 0 }, { id: 100, name: "Ghost move", type: 7 },
    { id: 101, name: "Other Ghost move", type: 7 }];
  const context = { moves, text: (id) => id, otherNames: () => [] };
  const set = new FilterSet("species", FILTER_SETS.species, {});
  const all = [0, 1, 2, 3].map((method) => ({ slot: method + 1, name: `species:${method + 1}`,
    types: [method === 3 ? 13 : 12], abilities: [method === 2 ? 2 : 1],
    learnset: [{ method, move: 100 + method % 2 }, ...(method % 2 ? [] : [{ method: 0, move: 33 }])] }));
  all.push({ slot: 5, name: "species:5", types: [7], learnset: [{ method: 0, move: 33 }, { move: 999 }] },
    { slot: 6, name: "species:6", learnset: [] }, { slot: 7, name: "species:7" });
  const matches = (raw) => {
    const values = set.prepare(raw, context);
    return all.map((record, index) => index).filter((index) => set.matches(all[index], values, context));
  };
  assert.deepEqual(matches({ moveType: "7" }), [0, 1, 2, 3]);
  assert.deepEqual(matches({ moveType: "7", move: "Tackle" }), [0, 2]);
  assert.deepEqual(matches({ moveType: "7", move: "Tackle", type: "12", ability: "2" }), [2]);
  assert.deepEqual(matches({ moveType: "7", move: "Unknown" }), []);
  assert.deepEqual(matches({ moveType: "0" }), [0, 2, 4]);
  assert.deepEqual(matches({ moveType: "9" }), []);
  assert.deepEqual(matches({}), [0, 1, 2, 3, 4, 5, 6]);
});

test("the move type selector loads learnsets, updates counts, restores and clears", async (context) => {
  const { $ } = stubPage(context, "pokedex");
  const { PokedexView } = await import("../js/pokedex/pokedex.js");
  const { setLabelSource } = await import("../js/ui.js");
  context.after(() => setLabelSource(null));
  const storage = fakeStorage();
  let memory = new DexMemory("game", "ru", () => storage);
  const data = { profile_block: "abc", learnsets: "learnsets.json", types: [{ id: 0 }, { id: 7 }, { id: 9 }],
    moves: [{ id: 0, name: "moves:0", type: 0 }, { id: 1, name: "moves:1", type: 7 }],
    species: [{ slot: 1, dex: 1, name: "species:1", types: [0] }, { slot: 2, dex: 2, name: "species:2", types: [0] }] };
  const names = { "types:0": ["Normal", "Норма"], "types:7": ["Ghost", "Дух"], "types:9": ["Unknown", "???"] };
  const fetcher = fakeFetch({ "data/learnsets.json": { format: "gen3tl-pokedex-learnsets", profile_block: "abc",
    columns: ["method", "move"], species: { 1: [[3, 1]], 2: [[0, 0]] } } });
  const view = new PokedexView({ data: () => data, load: async () => {}, memory: () => memory,
    entry: async (id) => ({ source_text: names[id]?.[0] ?? id, translation: names[id]?.[1] ?? id }),
    datasetFile: (file) => `data/${file}`, fetcher, spriteSrc: (path) => path, setHash: () => {},
  }, $);
  await view.load();
  view.loaded = true;
  view.applyFilters();
  assert.deepEqual(fetcher.calls, []);
  const moveType = () => view.filterSets.species.controls.get("moveType").select;
  assert.deepEqual(moveType().children.map((option) => option.value), ["", "0", "7", "9"]);
  assert.equal(moveType().children[2].textContent, "Дух");
  moveType().value = "7";
  moveType().listeners.change();
  await view.learnsets;
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(fetcher.calls, ["data/learnsets.json"]);
  assert.deepEqual(view.filtered, [0]);
  assert.equal($("x-count").textContent, "1 of 2 species");
  assert.equal(memory.state.modes.species.filters.moveType, "7");
  view.setLang("en");
  assert.equal(moveType().children[2].textContent, "Ghost");
  assert.equal(moveType().value, "7");
  view.setLang("translation");
  assert.equal(moveType().children[2].textContent, "Дух");
  memory = new DexMemory("game", "ru", () => storage);
  data.learnsets = "learnsets.json";
  for (const record of data.species) delete record.learnset;
  view.reset();
  await view.show();
  assert.equal(moveType().value, "7");
  assert.deepEqual(view.filtered, [0]);
  assert.equal(fetcher.calls.length, 2);
  view.filterSets.species.clear();
  assert.equal(moveType().value, "");
  assert.deepEqual(view.filtered, [0, 1]);
  assert.equal($("x-count").textContent, "2 of 2 species");
  assert.equal(memory.state.modes.species.filters.moveType, undefined);
});

test("forms are labeled from their incoming evolution evidence", () => {
  assert.equal(formLabel(charizards[0], charizards, text), ""); // the base form carries no tag
  assert.equal(formLabel(charizards[1], charizards, text), "Mega X"); // plain, with the stone's letter
  assert.equal(formLabel(charizards[2], charizards, text), "Mega Y");
  assert.equal(formLabel(charizards[3], charizards, text), "Giga");
  assert.equal(formLabel(charizards[4], charizards, text), "Form 999");
  const pikachus = [{ slot: 25, dex: 25, name: "species:25", evolution: [] },
    { slot: 1085, dex: 25, name: "species:1085", form_name: "page:Surfing", evolution: [] }];
  assert.equal(formLabel(pikachus[0], pikachus, text), "");
  assert.equal(formLabel(pikachus[1], pikachus, text), "Surfing");
  for (const [slot, dex, label] of [[1023, 27, "Alolan"], [1024, 28, "Alolan"],
    [1212, 52, "Galarian"], [1231, 554, "Galarian Zen Mode"], [1234, 58, "Hisuian"]]) {
    const pair = [{ slot: dex, dex }, { slot, dex, form_name: `page:${label}` }];
    assert.equal(formLabel(pair[1], pair, text), label);
  }
  for (const [slot, dex, label] of [[718, 487, "Origin Forme"], [719, 492, "Sky Forme"]]) {
    const pair = [{ slot: dex, dex }, { slot, dex, form_name: `page:${label}` }];
    assert.equal(formLabel(pair[1], pair, text), label);
  }
  // flower colours and dance styles, base forms included
  for (const [slot, dex, label] of [[777, 669, "Red Flower"], [840, 669, "Blue Flower"], [843, 669, "White Flower"],
    [848, 670, "Eternal Flower"], [852, 671, "White Flower"], [958, 741, "Baile Style"], [1045, 741, "Sensu Style"]]) {
    const family = [{ slot: 777, dex: 669 }, { slot: 778, dex: 670 }, { slot: 779, dex: 671 }, { slot: 958, dex: 741 }, { slot, dex, form_name: `page:${label}` }];
    assert.equal(formLabel(family[4], family, text), label);
  }
  for (const [slot, type, label] of [[720, 1, "Боевой"], [735, 17, "Тёмный"]]) {
    const pair = [{ slot: 493, dex: 493 }, { slot, dex: 493, types: [type, type], form_name: `types:${type}` }];
    assert.equal(formLabel(pair[1], pair, text), label);
  }
  assert.equal(displayName(charizards[1], charizards, text), "Mega Чаризард X"); // the game's "Mega {species}"
  assert.deepEqual(recordIds(charizards[0]).filter((id) => id.startsWith("items:")), ["items:447", "items:448"]);
  assert.equal(formLabel(records[0], records, text), "");
});

test("translated page form names retain complete English search names", () => {
  const records = [
    { slot: 1, dex: 646, name: "species:1" },
    { slot: 2, dex: 646, name: "species:2", base_species: 1,
      form_name: "page:Black Kyurem", form_name_full: true },
    { slot: 3, dex: 483, name: "species:3" },
    { slot: 4, dex: 483, name: "species:4", base_species: 3,
      form_name: "page:Origin Forme", form_name_full: false },
  ];
  const source = id => Number(id.split(":")[1]) <= 2 ? "Kyurem" : "Dialga";
  const translated = id => Number(id.split(":")[1]) <= 2 ? "Курем" : "Диалга";
  const labels = { "Black Kyurem": "Чёрный Курем", "Origin Forme": "Исходная форма" };
  setLabelSource(key => labels[key] ? { text: labels[key], game: false } : null);
  try {
    for (const [query, flat, grouped] of [["Black Kyurem", 1, 0], ["Чёрный Курем", 1, 0],
      ["Dialga Origin Forme", 3, 2], ["Диалга Исходная форма", 3, 2]]) {
      assert.deepEqual(filterSpecies(records, query, translated, null, source), [flat]);
      assert.deepEqual(filterSpecies(records, query, translated, null, source, () => [], true), [grouped]);
    }
  } finally {
    setLabelSource(null);
  }
});

test("grouped species search and filters match every forme through one base row", () => {
  const forms = [
    { slot: 990, dex: 773, base_species: 990, name: "species:990", types: [0], learnset: [] },
    { slot: 1048, dex: 773, base_species: 990, name: "species:1048", form_name: "types:1", types: [1], learnset: [] },
    { slot: 1049, dex: 773, base_species: 990, name: "species:1049", form_name: "types:10", types: [10], learnset: [] },
    { slot: 991, dex: 774, base_species: 991, name: "species:991", types: [5], learnset: [] },
  ];
  const names = (id) => ({
    "species:990": "Silvally", "species:1048": "Silvally", "species:1049": "Silvally", "species:991": "Minior",
    "types:1": "Fighting", "types:10": "Fire",
  })[id] ?? "";
  assert.equal(baseSlot(forms[2], forms), 990);
  assert.deepEqual(formesOf(forms[2], forms).map((record) => record.slot), [990, 1048, 1049]);
  assert.equal(displayName(forms[2], forms, names), "Silvally Fire");
  assert.ok(speciesGroupSearchNames(forms[0], forms, names, names).includes("Silvally Fire"));
  assert.ok(speciesSearchNames(charizards[1], text, (id) => [text(id)], () => [], charizards).includes("Mega Чаризард X"));
  assert.deepEqual(filterSpecies(forms, "", names, null, names, () => [], true), [0, 3]);
  assert.deepEqual(filterSpecies(forms, "Silvally Fire", names, null, names, () => [], true), [0]);
  assert.deepEqual(filterSpecies(forms, "Silvally Fire", names, null, names), [2]);
  assert.deepEqual(filterSpecies(forms, "", names, { types: [10] }, names, () => [], true), [0]);
});

test("grouped refilters reuse family indexes and cached search names", () => {
  const rows = Array.from({ length: 40 }, (_, i) => ({ slot: i + 1, dex: i + 1, name: `species:${i + 1}`, types: [] }));
  let scans = 0;
  let lookups = 0;
  const records = new Proxy(rows, {
    get(target, property, receiver) {
      if (property === "filter") scans++;
      return Reflect.get(target, property, receiver);
    },
  });
  const text = (id) => { lookups++; return id; };
  const search = createSpeciesSearch(records, text, text, () => [], true, speciesFamilyIndex(records));
  const filtered = search.filter("");
  assert.deepEqual(filtered, rows.map((_, i) => i));
  for (const i of filtered) search.names(records[i]);
  const expectedScans = scans;
  const expectedLookups = lookups;
  for (let i = 0; i < 10; i++) {
    search.names(records[0]);
    assert.deepEqual(search.filter(""), rows.map((_, index) => index));
  }
  assert.ok(scans <= rows.length * 6, `grouped filtering rescanned records ${scans} times`);
  assert.equal(scans, expectedScans);
  assert.equal(lookups, expectedLookups);
});

test("cards number species by internal ID and keep the National number in the information panel", async (context) => {
  const { $ } = stubPage(context, "pokedex");
  const { PokedexView } = await import("../js/pokedex/pokedex.js");
  const species = [
    { slot: 75, dex: 75, name: "species:75", types: [], learnset: [] },
    { slot: 990, dex: 773, name: "species:990", types: [], learnset: [] },
    { slot: 773, dex: 665, name: "species:773", types: [], learnset: [] },
    { slot: 9998, dex: 1234, name: "species:9998", types: [], learnset: [] },
  ];
  const view = new PokedexView({
    data: () => ({ species, abilities: [], moves: [], items: [], types: [] }), load: async () => {},
    entry: async (id) => ({ source_text: id, translation: "" }), profile: () => ({ name: "Sample Game 1.2.3" }),
    spriteSrc: (path) => path, setHash: () => {},
  }, $);
  await view.show();
  for (const [slot, national, display] of [
    [75, "75", "#075"], [990, "773", "#990"], [773, "665", "#773"], [9998, "1234", "#9998"],
  ]) {
    await view.openSpecies(slot);
    assert.equal($("x-id").textContent, display);
    assert.equal($("x-id").title, "Internal ID");
    assert.ok($("x-name").textContent.startsWith(`${display} `));
    assert.equal($("x-national-number").textContent, national);
    assert.equal($("x-game-id"), null);
    assert.equal($("x-game-id-label"), null);
    const facts = $("x-facts").children;
    const internal = facts.findIndex((node) => node.textContent === "Sample Game 1.2.3 internal ID");
    assert.ok(internal >= 0);
    assert.equal(facts[internal + 1].textContent, String(slot));
    assert.equal(view.record(slot).slot, slot);
  }
});

test("the default order follows internal IDs and either number finds the entry", () => {
  const separate = { separate: true, reason: "gameplay_difference", differences: ["types"] };
  const species = [
    { slot: 410, dex: 386, base_species: 410, name: "species:410", types: [], learnset: [] },
    { slot: 1040, dex: 386, base_species: 410, name: "species:1040", types: [], learnset: [], form_browsing: separate },
    { slot: 38, dex: 38, base_species: 38, name: "species:38", types: [], learnset: [] },
    { slot: 1026, dex: 38, base_species: 38, name: "species:1026", types: [], learnset: [], form_browsing: separate },
    { slot: 1027, dex: 38, base_species: 38, name: "species:1027", types: [], learnset: [] },
  ];
  const names = (id) => id;
  const search = createSpeciesSearch(species, names, names, () => [], true);
  const slots = (indices) => indices.map((i) => species[i].slot);
  assert.deepEqual(slots(sortSpeciesIndices(search.filter(""), species, names, speciesSort())), [38, 410, 1026, 1040]);
  assert.deepEqual(species.map((record) => rowLabel(record, names).dex), ["410", "1040", "38", "1026", "1027"]);
  assert.deepEqual(slots(search.filter("1040")), [1040]);
  assert.deepEqual(slots(search.filter("386")), [410, 1040]);
  assert.deepEqual(slots(search.filter("1027")), [38]); // a grouped form finds its row
});

test("separate gameplay forms get rows, slot numbers, filters and reciprocal card links", async (context) => {
  const { $ } = stubPage(context, "pokedex");
  const { PokedexView } = await import("../js/pokedex/pokedex.js");
  const species = [
    { slot: 10, dex: 7, base_species: 10, name: "species:10", types: [1], learnset: [] },
    { slot: 20, dex: 7, base_species: 10, name: "species:20", form_name: "species:20", form_name_full: true,
      types: [2], learnset: [], form_browsing: { separate: true, reason: "gameplay_difference", differences: ["types"] } },
    { slot: 30, dex: 7, base_species: 10, name: "species:30", form_name: "species:30", form_name_full: true,
      types: [1], learnset: [], form_browsing: { separate: false, reason: "same_gameplay", differences: [] } },
  ];
  const source = (id) => ({ "species:10": "Sample", "species:20": "Sample Alternate", "species:30": "Sample Spotted" }[id] ?? id);
  const translated = (id) => `Localized ${source(id)}`;
  for (const names of [source, translated]) {
    const search = createSpeciesSearch(species, names, source, () => [], true);
    assert.deepEqual(search.filter(""), [0, 1]);
    assert.deepEqual(search.filter("Alternate"), [1]);
    assert.deepEqual(search.filter(names("species:20")), [1]);
    assert.deepEqual(search.filter("20"), [1]);
    assert.deepEqual(search.filter("Spotted"), [0]);
    assert.deepEqual(search.filter("", { types: [2] }), [1]);
    assert.ok(!speciesGroupSearchNames(species[0], species, names, source).includes(names("species:20")));
    assert.equal(speciesListFields(species[1], species, names).dex, 20);
    assert.equal(rowLabel(species[1], names).dex, "20");
    assert.equal(numberedName(species[1], species, names), `#020 ${names("species:20")}`);
    assert.deepEqual(sortSpeciesIndices([1, 0], species, names, { key: "dex" }), [0, 1]);
  }
  const paths = [];
  const view = new PokedexView({
    data: () => ({ species, abilities: [], moves: [], items: [], types: [] }), load: async () => {},
    entry: async (id) => ({ source_text: source(id), translation: "" }), profile: () => ({ id: "sample", name: "Sample" }),
    spriteSrc: (path) => path, setHash: (path) => paths.push(path),
  }, $);
  await view.show();
  assert.deepEqual(view.filtered, [0, 1]);
  assert.equal($("x-count").textContent, "2 of 2 species");
  await $("x-formes").children[1].listeners.click();
  assert.equal(view.cur, 20);
  assert.equal($("x-id").textContent, "#020");
  assert.equal($("x-name").textContent, "#020 Sample Alternate");
  assert.equal($("x-national-number").textContent, "7");
  assert.ok(paths.includes("species/20"));
  $("x-search").value = "Sample";
  const type = view.filterSets.species.controls.get("type").select;
  type.value = "1";
  view.refilter();
  assert.equal(view.cur, 10);
  type.value = "2";
  view.refilter();
  assert.equal(view.cur, 20);
  view.filterSets.species.clear();
  await $("x-formes").children[0].listeners.click();
  assert.equal(view.cur, 10);
  assert.equal($("x-name").textContent, "#010 Sample");
  await $("x-formes").children[2].listeners.click();
  assert.equal(view.cur, 30);
  assert.equal($("x-id").textContent, "#030");
  assert.deepEqual(view.filtered, [0, 1]);
});

test("normal lineage excludes form transformations and spells out conditions", () => {
  const chain = [
    { slot: 4, dex: 4, evolution: [{ method: 4, parameter: 16, target: 5, extra: 0 }] },
    { slot: 5, dex: 5, evolution: [{ method: 4, parameter: 36, target: 6, extra: 0 }] },
    ...charizards,
  ];
  const paths = evolutionPaths(chain[2], chain);
  assert.deepEqual(paths.map((path) => path.map((step) => step.slot)), [[4, 5, 6]]);
  assert.equal(paths[0][1].via.parameter, 16);
  assert.equal(paths[0][2].via.parameter, 36);
  assert.deepEqual(evolutionPaths(charizards[1], chain), []);
  assert.equal(evolutionCondition({ method: 7, parameter: 447, extra: 0 }, text), "Чаризардит X");
  assert.equal(evolutionCondition({ method: 19, parameter: 138, extra: 0 },
    (id) => id === "mapsec:138" ? "Test Location" : text(id)), "At Test Location");
  for (let method = 1; method <= 39; method++) {
    assert.ok(!evolutionCondition({ method, parameter: 1, extra: 0 }, text).startsWith("Method "), `method ${method}`);
  }
});

test("item-at-tile evolution translates the template and terrain through page labels", async (context) => {
  const { setLabelSource } = await import("../js/ui.js");
  context.after(() => setLabelSource(null));
  const labels = {
    "Use {item} on {tile}": "Использовать {item} на {tile}",
    "shallow water": "мелководье",
    "Use {item} at tile behavior {n}": "Использовать {item} на тайле {n}",
  };
  setLabelSource((key) => labels[key] ? { text: labels[key], game: false } : null);
  const edge = { method: 34, parameter: 96, extra: 23, tile: "shallow water" };
  const item = (id) => id === "items:96" ? "Камень грома" : "";
  assert.equal(evolutionCondition(edge, item), "Использовать Камень грома на мелководье");
  assert.equal(evolutionCondition({ ...edge, tile: undefined }, item),
    "Использовать Камень грома на тайле 23");
});

test("the evolution tree runs from the base species with transformations as branches", () => {
  const chain = [
    { slot: 4, dex: 4, evolution: [{ method: 4, parameter: 16, target: 5, extra: 0 }] },
    { slot: 5, dex: 5, evolution: [{ method: 4, parameter: 36, target: 6, extra: 0 }] },
    ...charizards,
  ];
  const shape = (node) => [node.slot, node.branches.map((b) => [b.edge.method, shape(b.node)])];
  const expected = [4, [[4, [5, [[4, [6, [[254, [870, []]], [254, [871, []]], [253, [1261, []]]]]]]]]]];
  assert.deepEqual(shape(evolutionTree(chain[2], chain)), expected);
  assert.deepEqual(shape(evolutionTree(chain[3], chain)), expected); // from Mega X
  assert.deepEqual(shape(evolutionTree(chain[0], chain)), expected); // from Charmander
  const snorunt = [
    { slot: 346, dex: 361, evolution: [{ method: 4, parameter: 30, target: 347, extra: 0 },
      { method: 7, parameter: 100, target: 531, extra: 254 }] },
    { slot: 347, dex: 362, evolution: [{ method: 254, parameter: 481, target: 904, extra: 0 }] },
    { slot: 531, dex: 478, evolution: [] },
    { slot: 904, dex: 362, evolution: [{ method: 254, parameter: 0, target: 347, extra: 0 }] },
  ];
  assert.deepEqual(shape(evolutionTree(snorunt[2], snorunt)),
    [346, [[4, [347, [[254, [904, []]]]]], [7, [531, []]]]]);
  assert.deepEqual(shape(evolutionTree(snorunt[3], snorunt)), shape(evolutionTree(snorunt[0], snorunt)));
  // edges to one target join its branch, in record order
  const haunter = [
    { slot: 93, dex: 93, evolution: [{ method: 5, parameter: 0, target: 94, extra: 0 },
      { method: 7, parameter: 91, target: 94, extra: 0 }] },
    { slot: 94, dex: 94, evolution: [] },
  ];
  const joined = evolutionTree(haunter[1], haunter);
  assert.deepEqual(shape(joined), [93, [[5, [94, []]]]]);
  assert.deepEqual(joined.branches[0].edges.map((e) => e.method), [5, 7]);
  assert.deepEqual(joined.branches[0].edges.map((e) => evolutionCondition(e, (id) => id === "items:91" ? "Link Stone" : "", [], true)),
    ["Trade", "Use Link Stone"]);
  // stages in columns; ancestors span their branches' rows
  const cell = (c) => [c.kind === "species" ? c.slot : `${c.from}>${c.to}`, c.column, c.row, c.span];
  const charizard = evolutionGrid(evolutionTree(chain[2], chain));
  assert.equal(charizard.columns, 7);
  assert.equal(charizard.rows, 3);
  assert.deepEqual(charizard.cells.map(cell), [
    [4, 1, 1, 3], ["4>5", 2, 1, 3], [5, 3, 1, 3], ["5>6", 4, 1, 3], [6, 5, 1, 3],
    ["6>870", 6, 1, 1], [870, 7, 1, 1], ["6>871", 6, 2, 1], [871, 7, 2, 1], ["6>1261", 6, 3, 1], [1261, 7, 3, 1],
  ]);
  const froslass = evolutionGrid(evolutionTree(snorunt[0], snorunt));
  assert.deepEqual([froslass.columns, froslass.rows], [5, 2]);
  assert.deepEqual(froslass.cells.map(cell), [
    [346, 1, 1, 2], ["346>347", 2, 1, 1], [347, 3, 1, 1], ["347>904", 4, 1, 1], [904, 5, 1, 1],
    ["346>531", 2, 2, 1], [531, 3, 2, 1],
  ]);
  assert.deepEqual(evolutionGrid(evolutionTree(haunter[0], haunter)).cells.find((c) => c.kind === "edge").edges.length, 2);
  assert.deepEqual(evolutionGrid({ slot: 2, branches: [] }), { columns: 1, rows: 1, cells: [{ kind: "species", slot: 2, column: 1, row: 1, span: 1 }] });
  assert.equal(evolutionTree({ slot: 1, evolution: null }, []), null);
  assert.deepEqual(shape(evolutionTree(ivysaur, [ivysaur])), [2, []]);
  assert.equal(evolutionCondition({ method: 253, parameter: 1, extra: 0 }, text), "Giga");
  assert.equal(evolutionCondition({ method: 254, parameter: 447, extra: 0 }, text), "Mega · Чаризардит X");
});

test("evolution markup wraps each branch and preserves every grid cell", async (context) => {
  const { $ } = stubPage(context, "pokedex");
  const { PokedexView } = await import("../js/pokedex/pokedex.js");
  let data;
  const view = new PokedexView({ data: () => data, spriteSrc: (path) => path }, $);
  for (const count of [2, 4]) {
    const records = [
      { slot: 1, dex: 1, evolution: Array.from({ length: count }, (_, index) => ({ method: 4, parameter: 20, target: index + 2 })) },
      ...Array.from({ length: count }, (_, index) => ({ slot: index + 2, dex: index + 2, evolution: [] })),
    ];
    data = { species: records, items: [], abilities: [] };
    const grid = view.evolutionGridElement(evolutionGrid(evolutionTree(records[0], records)));
    assert.equal(grid.children[0].style, `grid-column:1;grid-row:1 / span ${count}`);
    const fork = grid.children[1];
    assert.equal(fork.className, "dex-evo-fork split");
    assert.equal(fork.getAttribute("data-branches"), String(count));
    assert.equal(fork.children.length, count);
    for (const [index, branch] of fork.children.entries()) {
      assert.equal(branch.className, "dex-evo-branch");
      assert.equal(branch.children.length, 2);
      const [meta, target] = branch.children;
      assert.equal(meta.className, "dex-evo-meta");
      assert.equal(meta.style, `grid-column:2;grid-row:${index + 1} / span 1`);
      assert.equal(target.className, "dex-evo-target");
      assert.equal(target.style, `grid-column:3;grid-row:${index + 1} / span 1`);
      assert.equal(target.title, `species:${index + 2}`);
    }
  }
});

test("joined item use names the action and lone item use stays bare", async (context) => {
  const { $ } = stubPage(context, "pokedex");
  const { PokedexView } = await import("../js/pokedex/pokedex.js");
  context.after(() => setLabelSource(null));
  setLabelSource((key) => ({
    "Use {item}": { text: "Apply {item}", game: false },
    or: { text: "either", game: false },
  })[key] ?? null);
  const items = [{ id: 91, name: "items:91" }, { id: 92, name: "items:92" }];
  const records = [{ slot: 1, dex: 1, ability_details: [] }, { slot: 2, dex: 2, ability_details: [] }];
  const view = new PokedexView({ data: () => ({ species: records, items, abilities: [] }), spriteSrc: (path) => path }, $);
  view.text = (id) => ({ "items:91": "Stone", "items:92": "Charm" })[id] ?? "";
  const use = { method: 7, parameter: 91, target: 2, extra: 0 };
  const trade = { method: 6, parameter: 91, target: 2, extra: 0 };
  const joined = view.evolutionMeta(1, 2, [trade, use]);
  assert.equal(joined.title, "Trade holding Stone either Apply Stone");
  assert.deepEqual(joined.children.map((child) => child.textContent), ["Trade holding Stone", "either", "Apply Stone"]);
  assert.equal(joined.children[2].querySelector(".dex-item-link").textContent, "Stone");
  const lone = view.evolutionMeta(1, 2, use);
  assert.equal(lone.title, "Stone");
  assert.equal(lone.textContent, "Stone");
  assert.equal(lone.children.length, 1);
  assert.equal(lone.querySelector(".dex-item-link").textContent, "Stone");
  assert.deepEqual(conditionParts(use, view.text, items, true), [{ text: "Apply " }, { item: 91, text: "Stone" }]);
  assert.deepEqual(conditionParts(use, view.text, items), [{ item: 91, text: "Stone" }]);
  for (const [edge, expected] of [
    [{ method: 34, parameter: 91, extra: 0, tile: "shallow water" }, "Use Stone on shallow water"],
    [{ method: 36, parameter: 91, extra: 92 }, "Use Stone while holding Charm"],
    [{ method: 39, parameter: 91, extra: 0 }, "Use Stone at night"],
    [{ method: 254, parameter: 91, extra: 0 }, "Mega · Stone"],
  ]) {
    assert.equal(evolutionCondition(edge, view.text, items), expected);
    assert.equal(evolutionCondition(edge, view.text, items, true), expected);
  }
});

test("profile transformations distinguish names and linked conditions from Mega", (context) => {
  context.after(() => setLabelSource(null));
  const records = [
    {slot: 1, dex: 1, name: "species:1", evolution: [{method: 254, parameter: 10, target: 2, kind: "primal"}]},
    {slot: 2, dex: 1, name: "species:1", form_name: "page:Primal Kyogre", form_name_full: true},
    {slot: 3, dex: 2, name: "species:3", evolution: [{method: 254, parameter: 11, target: 4, kind: "ultra"}]},
    {slot: 4, dex: 2, name: "species:3", form_name: "page:Ultra Necrozma", form_name_full: true},
    {slot: 5, dex: 3, name: "species:5", evolution: [{method: 254, parameter: 12, target: 6, kind: "mega"}]},
    {slot: 6, dex: 3, name: "species:5"},
  ];
  const english = {"species:1": "Kyogre", "species:3": "Necrozma", "species:5": "Charizard",
    "items:10": "Blue Orb", "items:11": "Necrozium Z", "items:12": "Charizardite X"};
  const russian = {"species:1": "Кайогр", "species:3": "Некрозма", "species:5": "Чаризард",
    "items:10": "Синяя сфера", "items:11": "Некрозиум Z", "items:12": "Чаризардит X"};
  for (const translated of [false, true]) {
    const labels = translated ? {"Primal Kyogre": "Прото-Кайогр", "Ultra Necrozma": "Ультра-Некрозма",
      "Primal Reversion · {item}": "Протопревращение · {item}", "Ultra Burst · {item}": "Ультравзрыв · {item}"} : {};
    setLabelSource((key) => labels[key] ? {text: labels[key], game: false} : null);
    const text = (id) => (translated ? russian : english)[id] ?? "";
    for (const [own, name, condition] of [[1, translated ? "Прото-Кайогр" : "Primal Kyogre", translated ? "Протопревращение · Синяя сфера" : "Primal Reversion · Blue Orb"],
      [3, translated ? "Ультра-Некрозма" : "Ultra Necrozma", translated ? "Ультравзрыв · Некрозиум Z" : "Ultra Burst · Necrozium Z"]]) {
      const target = records[own], edge = records[own - 1].evolution[0];
      assert.equal(formLabel(target, records, text), name);
      assert.equal(displayName(target, records, text), name);
      assert.equal(megaLetter(target, records, text), null);
      assert.equal(evolutionCondition(edge, text), condition);
      const search = speciesSearchNames(target, text, (id) => english[id] ?? "", () => [], records);
      assert.ok(search.includes(target.form_name.slice(5)));
      assert.ok(search.includes(name));
      assert.deepEqual(edgeItems(edge), [edge.parameter]);
      assert.deepEqual(conditionParts(edge, text).filter(part => part.item != null).map(part => part.item), [edge.parameter]);
    }
    assert.equal(megaLetter(records[5], records, text), "X");
    assert.equal(displayName(records[5], records, text), translated ? "Mega Чаризард X" : "Mega Charizard X");
  }
  setLabelSource(null);
  const declared = records[1];
  assert.equal(formLabel({...declared, form_name: null}, records, (id) => english[id] ?? ""), "Form 2");
});

test("profile move parameters resolve as moves while item conditions keep their fallback", (context) => {
  context.after(() => setLabelSource(null));
  const edge = {method: 254, parameter: 559, target: 911, extra: 2, kind: "mega", parameter_kind: "move"};
  const ordinary = {method: 254, parameter: 447, target: 870, extra: 0, kind: "mega"};
  for (const translated of [false, true]) {
    const labels = {
      Mega: translated ? "МЕГА" : "MEGA",
      "Mega · {item}": translated ? "МЕГА · {item}" : "MEGA · {item}",
      "Knows {move}": translated ? "Знает {move}" : "Knows {move}",
    };
    setLabelSource((key) => labels[key] ? {text: labels[key], game: false} : null);
    const text = (id) => ({
      "moves:559": translated ? "Драковзлёт" : "DragonAscent",
      "items:447": translated ? "Чаризардит X" : "Charizite X",
      "items:559": translated ? "Ровап-ягода" : "Rowap Berry",
    })[id] ?? "";
    assert.equal(evolutionCondition(edge, text), translated ? "МЕГА · Знает Драковзлёт" : "MEGA · Knows DragonAscent");
    assert.deepEqual(conditionParts(edge, text), [
      {text: translated ? "МЕГА · Знает " : "MEGA · Knows "},
      {move: 559, text: translated ? "Драковзлёт" : "DragonAscent"},
    ]);
    assert.deepEqual(recordIds({name: "species:406", evolution: [edge]}), ["species:406", "moves:559"]);
    assert.deepEqual(edgeItems(edge), []);
    assert.equal(evolutionCondition(ordinary, text), translated ? "МЕГА · Чаризардит X" : "MEGA · Charizite X");
    assert.deepEqual(edgeItems(ordinary), [447]);
    const unknown = {...edge, parameter_kind: "future"};
    assert.equal(evolutionCondition(unknown, text), translated ? "МЕГА · Ровап-ягода" : "MEGA · Rowap Berry");
    assert.deepEqual(edgeItems(unknown), [559]);
    assert.deepEqual(recordIds({name: "species:406", evolution: [unknown]}), ["species:406", "items:559"]);
  }
  setLabelSource(null);
  assert.equal(evolutionCondition({...edge, parameter: 999}, () => ""), "Mega · Knows Move 999");
});

test("evolution edges name their items and the icon comes from the dataset", () => {
  assert.deepEqual(edgeItems({ method: 7, parameter: 100, extra: 0 }), [100]);
  assert.deepEqual(edgeItems({ method: 254, parameter: 447, extra: 0 }), [447]);
  assert.deepEqual(edgeItems({ method: 36, parameter: 7, extra: 9 }), [7, 9]);
  assert.deepEqual(edgeItems({ method: 4, parameter: 16, extra: 0 }), []);
  globalThis.document = { createElement: () => ({ children: [], setAttribute(k, v) { this[k] = v; }, append(...k) { this.children.push(...k); } }) };
  const items = [{ id: 100, icon: "icons/items/100.png" }, { id: 7, icon: null }];
  assert.equal(itemIcon(100, items, (p) => `data/${p}`).src, "data/icons/items/100.png");
  assert.equal(itemIcon(7, items), null);
  assert.equal(itemIcon(5, items), null);
  delete globalThis.document;
});

test("evolution conditions resolve declared item names before generic fallbacks", () => {
  const items = [{ id: 723, name: "s0EB9283" }, { id: 4, name: "items:4" }];
  const names = { "s0EB9283": "Галар-браслет", "items:4": "Покебол" };
  const text = (id) => names[id] ?? "";
  assert.deepEqual(itemKeys(723, items), ["s0EB9283", "items:723"]);
  assert.deepEqual(itemKeys(4, items), ["items:4"]);
  assert.equal(itemText(723, text, items), "Галар-браслет");
  assert.equal(itemText(4, text, items), "Покебол");
  assert.equal(itemName(723, text, items), "Галар-браслет");
  assert.equal(itemName(4, text, items), "Покебол");
  assert.equal(itemName(999, text, items), "Item 999");
  assert.equal(evolutionCondition({ method: 7, parameter: 723, extra: 0 }, text, items), "Галар-браслет");
  assert.equal(evolutionCondition({ method: 7, parameter: 4, extra: 0 }, text, items), "Покебол");
  assert.deepEqual(conditionParts({ method: 7, parameter: 723, extra: 0 }, text, items),
    [{ item: 723, text: "Галар-браслет" }]);
  assert.deepEqual(conditionParts({ method: 36, parameter: 723, extra: 4 }, text, items),
    [{ text: "Use " }, { item: 723, text: "Галар-браслет" }, { text: " while holding " }, { item: 4, text: "Покебол" }]);
  const record = { name: "species:1215", evolution: [{ method: 7, parameter: 723, extra: 0, target: 1216 }] };
  assert.deepEqual(recordIds(record, items), ["species:1215", "s0EB9283", "items:723"]);
  assert.deepEqual(recordIds(record), ["species:1215", "items:723"]);
});

test("declared item names fall back safely when the catalog entry is missing", () => {
  const items = [{ id: 723, name: "s0EB9283" }];
  assert.equal(itemText(723, () => "", items), "");
  assert.equal(itemName(723, () => "", items), "Item 723");
  assert.equal(evolutionCondition({ method: 7, parameter: 723, extra: 0 }, () => "", items), "Item 723");
  assert.deepEqual(conditionParts({ method: 7, parameter: 723, extra: 0 }, () => "", items),
    [{ item: 723, text: "Item 723" }]);
});

test("known-move conditions keep their move names and links", () => {
  const text = (id) => id === "moves:559" ? "Драковзлёт" : "";
  const items = [{ id: 723, name: "s0EB9283" }];
  assert.equal(evolutionCondition({ method: 26, parameter: 559, extra: 0 }, text, items), "Knows Драковзлёт");
  assert.deepEqual(conditionParts({ method: 26, parameter: 559, extra: 0 }, text, items),
    [{ text: "Knows " }, { move: 559, text: "Драковзлёт" }]);
});

test("declared item names resolve in every supported data language", () => {
  const items = [{ id: 723, name: "s0EB9283" }, { id: 724, name: "s0EB9291" }];
  const catalogs = {
    en: { "s0EB9283": "Galarica Cuff", "s0EB9291": "Galarica Wreath" },
    ru: { "s0EB9283": "Галар-браслет", "s0EB9291": "Венок галарики" },
    es: { "s0EB9283": "Galarica Cuff", "s0EB9291": "Galarica Wreath" },
    de: { "s0EB9283": "Galarica Cuff", "s0EB9291": "Galarica Wreath" },
    fr: { "s0EB9283": "Galarica Cuff", "s0EB9291": "Galarica Wreath" },
  };
  for (const names of Object.values(catalogs)) {
    const text = (id) => names[id] ?? "";
    assert.equal(evolutionCondition({ method: 7, parameter: 723, extra: 0 }, text, items), names["s0EB9283"]);
    assert.equal(evolutionCondition({ method: 7, parameter: 724, extra: 0 }, text, items), names["s0EB9291"]);
  }
});

test("unnamed item links reuse the localized noun and preserve names and targets", (context) => {
  const previous = globalThis.document;
  context.after(() => {
    if (previous) globalThis.document = previous;
    else delete globalThis.document;
    setLabelSource(null);
  });
  globalThis.document = { createElement: () => ({
    children: [], listeners: {},
    setAttribute(key, value) { this[key] = value; },
    addEventListener(event, callback) { this.listeners[event] = callback; },
    append(...children) { this.children.push(...children); },
  }) };
  setLabelSource((key) => key === "Item" ? { text: "Предмет", game: false } : null);
  const opened = [];
  const missing = itemLink(708, () => "", [{ id: 708, icon: "icons/items/708.png" }],
    (path) => `data/${path}`, (id) => opened.push(id));
  assert.equal(missing.children[1], "Предмет 708");
  assert.equal(missing.children[0].src, "data/icons/items/708.png");
  assert.equal(missing.title, "items:708");
  missing.listeners.click();
  const known = itemLink(4, () => "Existing item", [], undefined, (id) => opened.push(id));
  assert.equal(known.children[0], "Existing item");
  assert.equal(known.title, "items:4");
  known.listeners.click();
  assert.deepEqual(opened, [708, 4]);
  setLabelSource((key) => key === "Item" ? { text: "Item", game: false } : null);
  assert.equal(itemLink(708).children[0], "Item 708");
});

test("conditions split at their item names for the item links", () => {
  assert.deepEqual(conditionParts({ method: 7, parameter: 91, extra: 0 }, (id) => (id === "items:91" ? "Link Stone" : "")),
    [{ item: 91, text: "Link Stone" }]);
  assert.deepEqual(conditionParts({ method: 36, parameter: 7, extra: 9 }, () => ""),
    [{ text: "Use " }, { item: 7, text: "Item 7" }, { text: " while holding " }, { item: 9, text: "Item 9" }]);
  assert.deepEqual(conditionParts({ method: 4, parameter: 16, extra: 0 }, text), [{ text: "Level 16" }]);
  assert.deepEqual(conditionParts({ method: 254, parameter: 447, extra: 0 }, text), [{ text: "Mega · " }, { item: 447, text: "Чаризардит X" }]);
  assert.deepEqual(conditionParts({ method: 5, parameter: 0, extra: 0 }, text), [{ text: "Trade" }]);
});

test("type chips carry the game's colour with readable text", async () => {
  globalThis.document = { createElement: () => ({ children: [], setAttribute(k, v) { this[k] = v; }, append(...k) { this.children.push(...k); } }), createTextNode: (t) => ({ text: t }) };
  const colours = [{ id: 10, colour: "#f75231" }, { id: 12, colour: "#7bce52" }];
  const fire = typeChip(10, "Огонь", colours);
  assert.equal(fire.className, "chip ptype");
  assert.equal(fire.style, "--type:#f75231;--type-fg:#fff");
  assert.equal(fire.children[0], "Огонь");
  const grass = typeChip(12, "", colours, " muted");
  assert.equal(grass.style, "--type:#7bce52;--type-fg:#111");
  assert.equal(grass.children[0], "Type 12");
  assert.equal(typeChip(3, "Яд", []).style, undefined);
  assert.equal(chipText("#ffffff"), "#111");
  assert.equal(chipText("#000000"), "#fff");
  delete globalThis.document;
});

test("a side of an ability change without an ability reads none", () => {
  const source = { ability_details: [{ id: 65, regular: true, hidden: false }, { id: 34, regular: false, hidden: true }] };
  const target = { ability_details: [{ id: 47, regular: true, hidden: false }] };
  const names = { "abilities:65": "Overgrow", "abilities:34": "Chlorophyll", "abilities:47": "Thick Fat", "abilities:22": "Rivalry" };
  const changes = abilityChanges(source, target, (id) => names[id] ?? "");
  // an empty slot takes the first ability.
  // the hidden ability is tagged.
  assert.deepEqual(changes.map(abilityChangeText), ["Overgrow ➝ Thick Fat", "Chlorophyll (hidden) ➝ Thick Fat"]);
  assert.deepEqual(changes.map((c) => c.standIn), [false, true]);
  const secondSource = { ability_details: [...source.ability_details, { id: 22, regular: true, hidden: false }] };
  const secondSlot = abilityChanges(secondSource, target, (id) => names[id] ?? "");
  assert.deepEqual(secondSlot.map(abilityChangeText),
    ["Overgrow ➝ Thick Fat", "Rivalry ➝ Thick Fat", "Chlorophyll (hidden) ➝ Thick Fat"]); // empty ability2 falls back
  assert.deepEqual(secondSlot.map((c) => c.standIn), [false, true, true]);
  assert.deepEqual(abilityChanges(source, target, (id) => names[id] ?? "", 253), []); // G-Max reloads no ability
  const twoRegular = { ability_details: [{ id: 65, regular: true, hidden: false }, { id: 22, regular: true, hidden: false }] };
  const personality = abilityChanges(source, twoRegular, (id) => names[id] ?? "");
  assert.deepEqual(personality.map(abilityChangeText), ["none ➝ Rivalry", "Chlorophyll (hidden) ➝ Overgrow or Rivalry"]); // personality bit selects
  assert.equal(personality[1].standIn, false);
  assert.equal(personality[1].alternate.text, "Rivalry");
  const haunter = { ability_details: [{ id: 26, regular: true, hidden: false }] };
  const gengar = { ability_details: [{ id: 26, regular: true, hidden: false }, { id: 130, regular: false, hidden: true }] };
  assert.deepEqual(abilityChanges(haunter, gengar, (id) => ({ "abilities:130": "Cursed Body" })[id] ?? "").map(abilityChangeText),
    ["none (hidden) ➝ Cursed Body"]);
  assert.deepEqual(abilityChanges({ abilities: [] }, { ability_details: [{ id: 47, regular: true }] }, (id) => names[id])
    .map(abilityChangeText), ["none ➝ Thick Fat"]); // a species without abilities gains one
});

test("ability changes come from the actual source and target roles", () => {
  const luxio = { ability_details: [
    { id: 94, regular: true, hidden: false }, { id: 22, regular: true, hidden: false },
    { id: 62, regular: false, hidden: true },
  ] };
  const luxray = { ability_details: [
    { id: 129, regular: true, hidden: false }, { id: 22, regular: true, hidden: false },
    { id: 62, regular: false, hidden: true },
  ] };
  const names = (id) => ({ "abilities:94": "Rivalry", "abilities:129": "Strong Jaw" })[id] ?? "";
  const changes = abilityChanges(luxio, luxray, names);
  assert.equal(changes.length, 1);
  assert.equal(abilityChangeText(changes[0]), "Rivalry ➝ Strong Jaw"); // by name
  assert.deepEqual(abilityChanges(luxio, luxio, names), []);
});

test("the number leads the name everywhere a species is named", () => {
  assert.equal(dexNumber(1), "#001");
  assert.equal(dexNumber(892), "#892");
  assert.equal(dexNumber(0), "");
  assert.equal(dexNumber(undefined), "");
  assert.equal(numberedName(charizards[0], charizards, text), "#006 Чаризард");
  assert.equal(numberedName(charizards[1], charizards, text), "#870 Mega Чаризард X");
  assert.equal(numberedName({ slot: 999, dex: 0, name: "species:999" }, [], (id) => id), "species:999");
});

test("the list row carries the dex number and the resolved name", () => {
  assert.deepEqual(rowLabel(records[0], text), { dex: "1", name: "Бульбазавр" });
  assert.deepEqual(rowLabel(records[1], text), { dex: "25", name: "" });
});

test("the detail resolves every display string through the selected catalog", () => {
  const d = detailOf(records[0], text);
  assert.equal(d.name, "Бульбазавр");
  assert.equal(d.category, "Семя");
  assert.equal(d.height, "0.7");
  assert.equal(d.weight, "6.9");
  assert.deepEqual(d.types, [{ id: "types:12", text: "Трава" }, { id: "types:3", text: "Яд" }]);
  assert.deepEqual(d.abilities, [{ id: "abilities:65", text: "Зарастание", descriptionId: null,
    description: "", regular: true, hidden: false }]);
  assert.deepEqual(d.entries, [{ id: "s0444CB2", text: "С самого рождения он носит на спине семя." }]);
  assert.deepEqual(d.stats, records[0].base_stats);
  assert.equal(d.catchRate, "45 / 255");
  assert.equal(d.baseExp, "64");
  assert.equal(d.growth, "Medium Slow");
  assert.equal(d.eggGroups, "Monster / Grass");
  assert.equal(d.gender, "87.8% male / 12.2% female");
});

test("ability details include descriptions and hidden roles without borrowing text", () => {
  const record = { ability_details: [
    { id: 66, regular: true, hidden: false }, { id: 96, regular: false, hidden: true },
  ] };
  const abilityRecords = [{ id: 66, description: "s66" }, { id: 96, description: "s96" }];
  const d = detailOf(record, (id) => ({
    "abilities:66": "Яркий огонь", "abilities:96": "Сила солнца",
    s66: "Усиливает огненные приёмы.",
  })[id] ?? "", abilityRecords);
  assert.deepEqual(d.abilities.map((a) => [a.text, a.description, a.hidden]), [
    ["Яркий огонь", "Усиливает огненные приёмы.", false], ["Сила солнца", "", true],
  ]);
});

test("details leave unavailable strings and metadata empty", () => {
  const d = detailOf(records[1], text);
  assert.equal(d.name, "");
  assert.equal(d.category, "Мышь");
  assert.deepEqual(d.entries, [{ id: "s044561D", text: "" }]);
  assert.deepEqual(d.types, [{ id: "types:13", text: "Электро" }]);
  assert.deepEqual(d.abilities, [{ id: "abilities:9", text: "Статика", descriptionId: null,
    description: "", regular: true, hidden: false }]);
  assert.equal(d.catchRate, "");
  assert.equal(d.gender, "");
  const u = detailOf(records[2], text);
  assert.equal(u.category, "");
  assert.equal(u.weight, "105");
});

test("a condition names its move as a part that opens the move card", () => {
  const parts = conditionParts({ method: 26, parameter: 33, extra: 0 }, (id) => ({ "moves:33": "Tackle" })[id] ?? "");
  assert.deepEqual(parts, [{ text: "Knows " }, { move: 33, text: "Tackle" }]);
  assert.deepEqual(conditionParts({ method: 26, parameter: 999, extra: 0 }), [{ text: "Knows " }, { move: 999, text: "Move 999" }]);
});

test("effectiveness pairs ordered badges with every multiplier, including neutral damage", async (context) => {
  const { $ } = stubPage(context, "pokedex");
  const { PokedexView } = await import("../js/pokedex/pokedex.js");
  const record = { slot: 1, dex: 1, name: "species:1", types: [12, 15] };
  const data = { species: [record],
    types: [15, 13, 12, 11, 10, 0].map((id) => ({ id, colour: "#7bce52" })),
    type_effectiveness: [
      [0, 12, 1], [10, 12, 20], [10, 15, 20], [11, 12, 20],
      [13, 12, 5], [13, 15, 5], [12, 12, 5],
    ].map(([attack, defense, multiplier]) => ({ attack, defense, multiplier })),
  };
  const view = new PokedexView({ data: () => data }, $);
  view.texts = new Map(data.types.map(({ id }) => [`types:${id}`, { translation: `Type <${id}> with a long label` }]));
  const blocks = view.effectivenessBlocks(record);
  assert.equal(blocks.length, 6);
  const grid = blocks[1];
  assert.equal(grid.tagName, "DL");
  assert.deepEqual(grid.children.map((cell) => cell.children.map((node) => node.tagName)), Array(6).fill(["DT", "DD"]));
  const badges = grid.children.map((cell) => cell.children[0].children[0]);
  assert.deepEqual(badges.map((badge) => badge.title), [0, 10, 11, 13, 12, 15].map((id) => `types:${id}`));
  for (const badge of badges) {
    const id = Number(badge.title.split(":")[1]);
    assert.equal(badge.textContent, `Type <${id}> with a long label`);
    assert.equal(badge.className, view.chip(id).className);
    assert.equal(badge.style, view.chip(id).style);
    assert.equal(badge.children.length, 0);
  }
  assert.deepEqual(grid.children.map((cell) => cell.children[1].textContent), ["0", "4", "2", "¼", "½", "1"]);
  assert.deepEqual(grid.children.map((cell) => cell.children[1].className),
    ["zero", "quadruple", "double", "quarter", "half", "neutral"].map((name) => `dex-eff-value ${name}`));
  const pure = view.effectivenessBlocks({ ...record, types: [12, 12] });
  assert.equal(pure.length, 4);
  assert.deepEqual(pure[1].children.map((cell) => cell.children[1].textContent), ["0", "2", "2", "½", "½", "1"]);
  assert.deepEqual(view.effectivenessBlocks({ ...record, types: [] }), []);
  delete data.type_effectiveness;
  assert.deepEqual(view.effectivenessBlocks(record), []);
});

test("effectiveness sizes every block from the dataset with complete equal rows", async (context) => {
  const { $ } = stubPage(context, "pokedex");
  const { PokedexView } = await import("../js/pokedex/pokedex.js");
  const record = { slot: 1, dex: 1, name: "species:1", types: CHART_ORDER.slice(0, 2) };
  const data = { species: [record], type_effectiveness: [], types: [] };
  const view = new PokedexView({ data: () => data }, $);
  assert.deepEqual(view.effectivenessBlocks(record), []);
  for (let count = 1; count <= CHART_ORDER.length; count++) {
    data.types = CHART_ORDER.slice(0, count).map((id) => ({ id }));
    record.types = CHART_ORDER.slice(0, Math.min(2, count));
    const grids = view.effectivenessBlocks(record).filter((node) => node.tagName === "DL");
    assert.equal(grids.length, record.types.length + 1);
    for (const grid of grids) {
      assert.equal(grid.style.getPropertyValue("--dex-eff-count"), String(data.types.length));
      assert.equal(grid.children.length, data.types.length);
      assert.equal(grid.getAttribute("style"), null);
      for (const [property, minimumRows] of [["--dex-eff-medium", 2], ["--dex-eff-small", 3]]) {
        const columns = Number(grid.style.getPropertyValue(property));
        assert.ok(Number.isInteger(columns) && columns > 0);
        assert.equal(count % columns, 0, `${count} types: no incomplete row`);
        assert.ok(count / columns >= Math.min(count, minimumRows));
        if (count % minimumRows === 0) assert.equal(columns, count / minimumRows);
      }
    }
  }
});

test("the page's masthead switches the view between English and the translation", async () => {
  const fake = () => ({
    children: [], className: "", textContent: "", title: "", value: "", hidden: false,
    disabled: false, style: {}, scrollTop: 0, clientHeight: 400, listeners: {},
    addEventListener(ev, fn) { this.listeners[ev] = fn; }, setAttribute(k, v) { this[k] = v; }, click() {},
    append(...kids) { this.children.push(...kids.flat()); },
    replaceChildren(...kids) { this.children = [...kids.flat()]; },
    classList: { set: new Set(), toggle(c, on) { on ? this.set.add(c) : this.set.delete(c); } },
  });
  globalThis.document = { createElement: () => fake(), createTextNode: (t) => ({ text: t }) };
  const { PokedexView } = await import("../js/pokedex/pokedex.js");
  const elements = new Map();
  const $ = (id) => {
    if (!elements.has(id)) elements.set(id, fake());
    return elements.get(id);
  };
  const view = new PokedexView({
    data: () => ({ species: [records[1]] }),
    entry: async () => null, draft: () => false, spriteSrc: (p) => p, setHash: () => {},
  }, $);
  view.reset();
  view.texts = new Map([["species:25", { translation: "Пикачу", source_text: "Pikachu" }]]);
  view.setLang("en");
  assert.equal(view.lang, "en");
  assert.equal(view.text("species:25"), "Pikachu");
  view.setLang("translation");
  assert.equal(view.text("species:25"), "Пикачу");
  delete globalThis.document;
});

for (const draft of [false, true]) test(draft ? "draft catalogs show the source muted where untranslated"
  : "untranslated species names and entries render the English game text", async () => {
  const fake = () => ({
    children: [], className: "", textContent: "", title: "", value: "", hidden: false,
    disabled: false, style: {}, scrollTop: 0, clientHeight: 400,
    addEventListener() {}, setAttribute(k, v) { this[k] = v; }, click() {},
    append(...kids) { this.children.push(...kids.flat()); },
    replaceChildren(...kids) { this.children = [...kids.flat()]; },
    classList: { set: new Set(), toggle(c, on) { on ? this.set.add(c) : this.set.delete(c); } },
  });
  globalThis.document = { createElement: () => fake(), createTextNode: (t) => ({ text: t }) };
  const { PokedexView } = await import("../js/pokedex/pokedex.js");
  const elements = new Map();
  const $ = (id) => {
    if (!elements.has(id)) elements.set(id, fake());
    return elements.get(id);
  };
  const view = new PokedexView({
    data: () => ({ species: [records[1]] }),
    entry: async (id) => ({ translation: "", source_text: ({
      "species:25": "Pikachu", "types:13": "Electric", "abilities:9": "Static",
      "pokedex_categories:25": "Mouse", "s044561D": "",
    })[id] ?? "" }),
    draft: () => draft,
    spriteSrc: (p) => `data/${p}`,
    setHash: () => {},
  }, $);
  view.texts = new Map([
    ["species:25", { translation: "", source_text: "Pikachu" }],
    ["types:13", { translation: "", source_text: "Electric" }],
    ["abilities:9", { translation: "", source_text: "Static" }],
    ["pokedex_categories:25", { translation: "", source_text: "Mouse" }],
    ["s044561D", { translation: "", source_text: "" }],
  ]);
  view.applyFilters();
  view.open(25);
  assert.equal($("x-name").textContent, "#025 Pikachu");
  assert.equal($("x-name").classList.set.has("muted"), draft);
  assert.equal($("x-category").textContent, "Mouse");
  assert.equal($("x-category").classList.set.has("muted"), draft);
  assert.equal($("x-entry-text").children.length, 0); // an empty source stays empty
  assert.equal($("x-count").textContent.includes("source shown where untranslated"), draft);
  const entry = "It keeps its tail raised to monitor its surroundings.";
  for (const target of [{ translation: "" }, {}]) {
    view.texts.set("species:25", { source_text: "Pikachu", ...target });
    view.texts.set("s044561D", { source_text: entry, ...target });
    view.open(25);
    assert.equal($("x-name").textContent, "#025 Pikachu");
    assert.equal(view.row(0).children[1].children[0], "Pikachu");
    assert.equal($("x-entry-text").children[0].children[0].text, entry);
  }
  view.texts.set("species:25", { source_text: "Pikachu", translation: "Пикачу" });
  view.texts.set("s044561D", { source_text: entry, translation: text("s0444CB2") });
  view.open(25);
  assert.equal($("x-name").textContent, "#025 Пикачу");
  assert.equal($("x-entry-text").children[0].children[0].text, text("s0444CB2"));
  view.texts.set("trainer_classes:1", { source_text: "{PK}{MN} Trainer", translation: "" });
  assert.equal(view.text("trainer_classes:1"), "Pokémon Trainer");
  assert.equal(view.text("missing"), "");
  assert.equal(view.text(null), "");
  delete globalThis.document;
});

test("a species card loads the learnsets on first use and the move filter waits for them", async () => {
  const fake = () => ({
    children: [], className: "", textContent: "", title: "", value: "", hidden: false,
    disabled: false, style: {}, scrollTop: 0, clientHeight: 400, listeners: {},
    addEventListener(ev, fn) { this.listeners[ev] = fn; }, setAttribute(k, v) { this[k] = v; }, click() {},
    append(...kids) { this.children.push(...kids.flat()); },
    replaceChildren(...kids) { this.children = [...kids.flat()]; },
    classList: { set: new Set(), toggle(c, on) { on ? this.set.add(c) : this.set.delete(c); } },
  });
  globalThis.document = { createElement: () => fake(), createTextNode: (t) => ({ text: t }) };
  const { PokedexView } = await import("../js/pokedex/pokedex.js");
  const { fakeFetch } = await import("./fixture.mjs");
  const elements = new Map();
  const $ = (id) => {
    if (!elements.has(id)) elements.set(id, fake());
    return elements.get(id);
  };
  const moves = []; moves[33] = { id: 33, type: 0, damage_category: 0, power: 40, accuracy: 100, pp: 35 };
  const data = { profile_block: "abc", learnsets: "learnsets.json", moves, items: [],
    species: [{ ...records[1], evolution: [] }] };
  const fetcher = fakeFetch({ "data/learnsets.json": { format: "gen3tl-pokedex-learnsets", schema: 1, profile_block: "abc",
    columns: ["method", "move", "level", "index", "offset"], species: { 25: [[0, 33, 5, null, "0x1"]] } } });
  const view = new PokedexView({
    data: () => data, entry: async () => null, draft: () => false, spriteSrc: (p) => p, setHash: () => {},
    datasetFile: (file) => `data/${file}`, fetcher,
  }, $);
  view.texts = new Map([["species:25", { translation: "", source_text: "Pikachu" }], ["moves:33", { translation: "", source_text: "Tackle" }]]);
  view.loaded = true;
  view.applyFilters();
  view.open(25);
  assert.equal($("x-moves-empty").textContent, "Loading the moves…");
  assert.equal(await view.ensureLearnsets(), true);
  assert.deepEqual(fetcher.calls, ["data/learnsets.json"]);
  assert.equal(data.species[0].learnset.length, 1);
  await new Promise((r) => setTimeout(r, 0));
  assert.equal($("x-moves-empty").textContent, "");
  assert.equal($("x-moves").children.length, 1);
  assert.equal(await view.ensureLearnsets(), true); // once
  assert.equal(fetcher.calls.length, 1);
  delete globalThis.document;
});

test("species move failures retry without pretending data is absent or crossing navigation", async () => {
  const { PokedexView } = await import("../js/pokedex/pokedex.js");
  const fake = (tag) => ({
    tag, children: [], listeners: {}, _text: "",
    get textContent() { return this._text + this.children.map((node) => typeof node === "string" ? node : node.textContent).join(""); },
    set textContent(value) { this._text = value; this.children = []; },
    addEventListener(event, fn) { this.listeners[event] = fn; },
    setAttribute(key, value) { this[key] = value; },
    append(...children) { this.children.push(...children.flat()); },
    replaceChildren(...children) { this._text = ""; this.children = children.flat(); },
  });
  globalThis.document = { createElement: fake };
  const elements = new Map();
  const $ = (id) => { if (!elements.has(id)) elements.set(id, fake()); return elements.get(id); };
  const record = { slot: 1 }, other = { slot: 2 };
  const view = Object.create(PokedexView.prototype);
  let pending = true, requests = 0, finish;
  Object.assign(view, {
    $, mode: "species", generation: 1, cur: 1,
    record: (slot) => slot === 1 ? record : other,
    learnsetsPending: () => pending,
    ensureLearnsets: () => { requests++; return new Promise((resolve) => { finish = resolve; }); },
    dataView: { learnsetTables: () => [fake("table")] },
  });
  view.renderMoves(record);
  assert.equal($("x-moves-empty").textContent, "Loading the moves…");
  finish(false);
  await Promise.resolve();
  assert.match($("x-moves-empty").textContent, /Could not load the moves\./);
  assert.doesNotMatch($("x-moves-empty").textContent, /Not extracted/);
  view.renderMoves(record);
  assert.equal(requests, 1);
  $("x-moves-empty").children.find((node) => node.tag === "button").listeners.click();
  assert.equal(requests, 2);
  assert.equal($("x-moves-empty").textContent, "Loading the moves…");
  pending = false;
  record.learnset = [{ method: 0, move: 33, level: 1 }];
  finish(true);
  await Promise.resolve();
  assert.equal($("x-moves-empty").textContent, "");
  assert.equal($("x-moves").children[0].tag, "table");
  view.renderMoves({ ...record, learnset: null });
  assert.equal($("x-moves-empty").textContent, "Not extracted");
  for (const navigate of [() => { view.cur = 2; }, () => { view.mode = "items"; }, () => { view.generation++; }]) {
    delete record.learnset;
    pending = true;
    view.cur = 1;
    view.mode = "species";
    view.moveFailure = null;
    view.renderMoves(record);
    navigate();
    $("x-moves-empty").textContent = "Current page";
    finish(false);
    await Promise.resolve();
    assert.equal($("x-moves-empty").textContent, "Current page");
  }
  delete globalThis.document;
});

test("a name that starts with the query ranks before one containing it and before other fields", () => {
  assert.equal(matchRank("Оникс", "они"), 0);
  assert.equal(matchRank("Энтони", "они"), 1);
  assert.equal(matchRank("Глум", "они"), 2);
  assert.equal(matchRank("Anything", ""), 0);
  assert.equal(matchRank(undefined, "x"), 2);
  assert.equal(matchRank(["Оникс", "Onix"], "oni"), 0);
  assert.equal(matchRank(["Энтони", "Anthony"], "thon"), 1);
});

test("the capture calculator uses item and type ids without English", async () => {
  const fake = () => ({
    children: [], className: "", textContent: "", title: "", value: "", hidden: false,
    disabled: false, style: {}, scrollTop: 0, clientHeight: 400, listeners: {},
    addEventListener(ev, fn) { this.listeners[ev] = fn; }, setAttribute(k, v) { this[k] = v; }, click() {},
    append(...kids) { this.children.push(...kids.flat()); },
    replaceChildren(...kids) { this.children = [...kids.flat()]; },
    classList: { set: new Set(), toggle(c, on) { on ? this.set.add(c) : this.set.delete(c); } },
  });
  globalThis.document = { createElement: () => fake(), createTextNode: (t) => ({ text: t }) };
  const { PokedexView } = await import("../js/pokedex/pokedex.js");
  const { fakeFetch } = await import("./fixture.mjs");
  const elements = new Map();
  const $ = (id) => {
    if (!elements.has(id)) elements.set(id, fake());
    return elements.get(id);
  };
  const data = { profile_block: "abc", learnsets: "learnsets.json", moves: [], encounters: [],
    items: [{ id: 4, name: "items:4", pocket: 3 }, { id: 6, name: "items:6", pocket: 3 },
      { id: 60, name: "items:60", pocket: 3 }, { id: 628, name: "items:628", pocket: 3 }, { id: 1, name: "items:1", pocket: 1 }],
    species: [{ ...records[1], types: [11], evolution: [{ method: 7, parameter: 94, target: 26 }], learnset: [],
      catch_rate: 190, base_stats: { ...records[1].base_stats, hp: 35 } }] };
  const view = new PokedexView({
    data: () => data, entry: async () => null, draft: () => false, spriteSrc: (p) => p, setHash: () => {},
    datasetFile: (file) => `data/${file}`, fetcher: fakeFetch({}),
  }, $);
  view.texts = new Map([["species:25", { translation: "Пикачу", source_text: "" }],
    ["items:4", { translation: "Покебол", source_text: "" }], ["items:6", { translation: "Нетбол", source_text: "" }],
    ["items:60", { translation: "Даскбол", source_text: "" }], ["items:628", { translation: "Мунбол", source_text: "" }],
    ["items:94", { translation: "Лунный камень", source_text: "" }]]);
  view.loaded = true;
  view.applyFilters();
  view.open(25);
  assert.equal($("x-catch-open").hidden, false);
  assert.equal($("x-catch").children.length, 0);
  await view.openCatch(view.record(25), [{ min: 5 }, { min: 9 }], data.items.filter((row) => row.pocket === 3));
  const textOf = (n) => typeof n === "string" ? n : n.text ?? [n.textContent, ...(n.children ?? []).map(textOf)].join("");
  const byId = (n, id) => n.id === id ? n : (n.children ?? []).map((child) => byId(child, id)).find(Boolean);
  const text = textOf($("x-catch")); // the fake document hosts no dialog
  assert.equal($("x-catch").children[0].className, "dex-catch");
  assert.match(text, /Catch calculator/);
  assert.match(text, /ПокеболНетболДаскболМунбол/);
  assert.match(text, /24[.,]7%/); // rate 190, base HP 35, level 5, full HP
  const select = byId($("x-catch"), "x-catch-ball");
  select.value = 6;
  select.listeners.change();
  assert.match(textOf($("x-catch")), /×3[.,]5/);
  select.value = 628;
  select.listeners.change();
  assert.match(textOf($("x-catch")), /×4/);
  delete globalThis.document;
});

test("the search matches every open catalog language whatever the page shows", async () => {
  const fake = () => ({
    children: [], className: "", textContent: "", title: "", value: "", hidden: false,
    disabled: false, style: {}, scrollTop: 0, clientHeight: 400, listeners: {},
    addEventListener(ev, fn) { this.listeners[ev] = fn; }, setAttribute(k, v) { this[k] = v; }, click() {},
    append(...kids) { this.children.push(...kids.flat()); }, prepend(...kids) { this.children.unshift(...kids.flat()); },
    replaceChildren(...kids) { this.children = [...kids.flat()]; },
    classList: { set: new Set(), toggle(c, on) { on ? this.set.add(c) : this.set.delete(c); } },
  });
  globalThis.document = { createElement: () => fake(), createTextNode: (t) => ({ text: t }) };
  globalThis.requestAnimationFrame = (fn) => fn();
  const { PokedexView } = await import("../js/pokedex/pokedex.js");
  const elements = new Map();
  const $ = (id) => {
    if (!elements.has(id)) elements.set(id, fake());
    return elements.get(id);
  };
  const data = {
    species: [{ slot: 25, dex: 25, name: "species:25", types: [13], learnset: [] },
      { slot: 94, dex: 94, name: "species:94", types: [7], learnset: [{ method: 0, level: 1, move: 1 }] }],
    moves: [{ id: 1, name: "moves:1", type: 0, damage_category: 0, power: 40, accuracy: 100, pp: 35 }],
    trainers: [{ id: 1, name: "trainer_names:1", class: "trainer_classes:57", party: [{ species: 25, level: 5 }] }],
    encounters: [{ runtime_map: "3.1", map: "3.1", map_section: 93, species: 25, method: "land", min: 2, max: 4 }],
    map_sections: [{ id: 93, name: "s071CA70" }], abilities: [], items: [], types: [],
  };
  const english = { "species:25": "Pikachu", "species:94": "Gengar", "moves:1": "Pound", "trainer_names:1": "Joey",
    "trainer_classes:57": "Youngster", s071CA70: "Tehl Town", "types:7": "Ghost", "types:13": "Electric" };
  const catalog = (code, names, aliases) => ({ schema: 1, language: code, search_aliases: aliases,
    texts: Object.fromEntries(Object.entries(english).map(([id, en]) => [id, [en, names[id] ?? ""]])) });
  // German has no translation; Russian names everything
  const docs = { de: catalog("de", {}), ru: catalog("ru", { "species:25": "Пикачу", "species:94": "Генгар",
    "moves:1": "Затрещина", "trainer_names:1": "Джоуи", "trainer_classes:57": "Юнец", s071CA70: "Городок Тель" },
  { "species:25": ["Пикачю"] }) };
  const profile = { id: "game", languages: [{ code: "de", texts: "de.json" }, { code: "ru", texts: "ru.json" }] };
  const fetched = [];
  const file = textsCache(async (lang) => {
    fetched.push(lang.code);
    return docs[lang.code];
  });
  let shown = "de"; // English mode reads the first open catalog, the empty one
  const view = new PokedexView({
    data: () => data, load: async () => {}, draft: () => false, spriteSrc: (p) => p, setHash: () => {},
    entry: async (id) => {
      const pair = docs[shown].texts[id];
      return pair ? { source_text: pair[0], translation: pair[1] } : null;
    },
    searchAliases: (id) => docs[shown].search_aliases?.[id] ?? [],
    searchTexts: () => searchTexts(profile, file),
  }, $);
  const slots = () => view.filtered.map((i) => view.records[i].slot);
  const search = async (query) => {
    $("x-search").value = query;
    view.applyFilters();
    await view.catalogsLoad;
    return slots();
  };
  view.reset();
  view.setLang("en");
  await view.show();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(fetched, []); // nothing loads before a search
  $("x-search").value = "генгар";
  view.applyFilters();
  assert.deepEqual(slots(), []); // the other catalogs are still loading
  await view.catalogsLoad;
  assert.deepEqual(slots(), [94]);
  assert.equal(view.openFirst(), true);
  assert.equal($("x-name").textContent, "#094 Gengar"); // the shown language stays English
  assert.deepEqual(await search("пикачю"), [25]); // another catalog's alias
  assert.deepEqual(await search("gengar"), [94]);
  $("x-search").value = "городок";
  view.applyFilters();
  await view.crossNames;
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(view.cross.map((hit) => [hit.kind, hit.name]), [["locations", "Tehl Town"]]);
  $("x-search").value = "";
  view.filterSets.species.set("move", "затрещ");
  assert.deepEqual(slots(), [94]);
  const moveOptions = view.filterSets.species.controls.get("move").root.children[1];
  assert.deepEqual(moveOptions.children.map((option) => option.value), ["Pound", "Затрещина"]);
  view.filterSets.species.set("move", "");
  for (const [mode, query, id] of [["moves", "затрещ", 1], ["trainers", "джоуи", 1], ["locations", "городок", "93"]]) {
    await view.setMode(mode);
    $("x-search").value = query;
    view.dataView.applyFilters();
    assert.deepEqual(view.dataView.filtered.map((row) => row.id), [id], mode);
    assert.equal(view.text(view.dataView.filtered[0].name), english[view.catalogId(view.dataView.filtered[0].name)]);
  }
  assert.deepEqual(fetched, ["de", "ru"]); // each file once
  // with Russian shown, English and German names match too
  docs.de.texts["moves:1"][1] = "Pfund";
  shown = "ru";
  view.reset();
  view.setLang("translation");
  await view.show();
  assert.deepEqual(await search("gengar"), [94]);
  view.openFirst();
  assert.equal($("x-name").textContent, "#094 Генгар");
  $("x-search").value = "";
  view.filterSets.species.set("move", "pfund");
  await view.catalogsLoad;
  assert.deepEqual(slots(), [94]);
  assert.deepEqual(fetched, ["de", "ru"]); // the cache serves the second page
  delete globalThis.requestAnimationFrame;
  delete globalThis.document;
});

test("one search field serves every tab; each tab keeps its own filters", async () => {
  const fake = () => ({
    children: [], className: "", textContent: "", title: "", value: "", hidden: false,
    disabled: false, style: { setProperty(key, value) { this[key] = String(value); } }, scrollTop: 0, clientHeight: 400, listeners: {},
    addEventListener(ev, fn) { this.listeners[ev] = fn; }, setAttribute(k, v) { this[k] = v; }, click() {},
    append(...kids) { this.children.push(...kids.flat()); }, prepend(...kids) { this.children.unshift(...kids.flat()); },
    replaceChildren(...kids) { this.children = [...kids.flat()]; },
    classList: { set: new Set(), toggle(c, on) { on ? this.set.add(c) : this.set.delete(c); } },
  });
  globalThis.document = { createElement: () => fake(), createTextNode: (t) => ({ text: t }) };
  globalThis.requestAnimationFrame = (fn) => fn();
  const { PokedexView } = await import("../js/pokedex/pokedex.js");
  const elements = new Map();
  const $ = (id) => {
    if (!elements.has(id)) elements.set(id, fake());
    return elements.get(id);
  };
  const data = {
    species: [{ slot: 25, dex: 25, name: "species:25", types: [13], learnset: [] },
      { slot: 26, dex: 26, name: "species:26", types: [13], learnset: [] },
      { slot: 94, dex: 94, name: "species:94", types: [7], learnset: [] },
      { slot: 642, dex: 642, name: "species:642", types: [13, 2], learnset: [] }],
    moves: [{ id: 1, name: "moves:1", type: 0, damage_category: 0, power: 40, accuracy: 100, pp: 35 },
      { id: 85, name: "moves:85", type: 13, damage_category: 1, power: 90, accuracy: 100, pp: 15 },
      { id: 87, name: "moves:87", type: 13, damage_category: 1, power: 110, accuracy: 70, pp: 10 }],
    trainers: [], encounters: [], map_sections: [], abilities: [], items: [],
    types: [{ id: 0 }, { id: 2 }, { id: 7 }, { id: 13 }], type_effectiveness: [],
  };
  const names = { "species:25": "Pikachu", "species:26": "Raichu", "species:94": "Gengar", "species:642": "Thundurus",
    "moves:1": "Pound", "moves:85": "Thunderbolt", "moves:87": "Thunder", "types:0": "Normal", "types:2": "Flying",
    "types:7": "Ghost", "types:13": "Electric" };
  const memory = new DexMemory("game", "ru", () => fakeStorage());
  const view = new PokedexView({
    data: () => data, load: async () => {}, draft: () => false, spriteSrc: (p) => p, setHash: () => {}, memory: () => memory,
    entry: async (id) => (names[id] ? { source_text: names[id], translation: "" } : null),
  }, $);
  const field = $("x-search");
  const type = async (query) => {
    field.value = query;
    field.listeners.input();
    await view.crossNames;
    await new Promise((resolve) => setTimeout(resolve, 0));
  };
  const enter = () => {
    let handled = false;
    field.listeners.keydown({ key: "Enter", preventDefault: () => { handled = true; } });
    return handled;
  };
  const species = () => view.filtered.map((i) => view.records[i].slot);
  const moves = () => view.dataView.filtered.map((row) => row.id);
  const hits = (list) => list.map((hit) => `${hit.kind}:${hit.id}`);
  view.reset();
  await view.show();
  // the Species tab keeps its filter; other tabs' hits follow
  assert.equal(field.placeholder, "Search");
  assert.equal(field["aria-label"], "Search");
  view.filterSets.species.set("type", "13");
  view.list.list.scrollTop = 500;
  await type("thund");
  assert.equal(view.list.list.scrollTop, 0);
  assert.deepEqual(species(), [642]);
  assert.deepEqual(hits(view.cross), ["moves:85", "moves:87"]);
  // Each mode restores its own query.
  await view.setMode("moves");
  assert.equal(field.value, "");
  await type("thund");
  assert.equal(field.placeholder, "Search");
  assert.equal(field["aria-label"], "Search");
  assert.deepEqual(moves(), [85, 87]);
  assert.equal(view.dataView.cur, 85);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(hits(view.dataView.cross), ["species:642"]);
  view.dataView.open(87);
  assert.equal(enter(), true);
  assert.equal(view.dataView.cur, 85); // Enter opens the tab's first match
  view.filterSets.moves.set("power", { op: ">=", a: "100" });
  assert.deepEqual(moves(), [87]);
  await type("chu");
  assert.deepEqual(moves(), []);
  assert.deepEqual(hits(view.dataView.cross), ["species:25", "species:26"]);
  assert.equal(view.filterSets.species.controls.get("type").select.value, "13");
  // Returning restores the species query and filters.
  await view.setMode("species");
  assert.equal(field.placeholder, "Search");
  assert.equal(field.value, "thund");
  assert.deepEqual(species(), [642]);
  await type("chu");
  assert.deepEqual(species(), [25, 26]);
  assert.equal(view.filterSets.species.controls.get("type").select.value, "13");
  view.open(94);
  assert.equal(enter(), true);
  assert.equal(view.cur, 25);
  await view.setMode("moves");
  assert.equal(field.value, "chu");
  assert.equal(view.filterSets.moves.controls.get("power").a.value, "100");
  assert.deepEqual(moves(), []);
  assert.equal(memory.state.modes.moves.filters.search, "chu");
  // the Types tab filters attacking rows and defending columns
  view.setMode("types");
  assert.equal(field.disabled, false);
  assert.equal(field.value, "");
  await type("chu");
  assert.equal($("x-type-chart").children[1].children.length, 0);
  assert.equal($("x-dual-chart").children.length, 0);
  await type("i");
  assert.deepEqual($("x-type-chart").children[0].children[0].children.slice(1).map((cell) => cell.children[0].title),
    ["types:13", "types:2"]);
  assert.deepEqual($("x-type-chart").children[1].children.map((row) => row.children[0].children[0].title),
    ["types:13", "types:2"]);
  assert.deepEqual($("x-dual-pick").children.map((button) => button.children[0].title), ["types:13", "types:2"]);
  assert.equal($("x-dual-chart").children[0].children[0].children.length, 4);
  assert.equal($("x-dual-chart").children[1].children.length, 2);
  await type("");
  assert.deepEqual($("x-type-chart").children[1].children.map((row) => row.children[0].children[0].title),
    ["types:0", "types:13", "types:2", "types:7"]);
  assert.equal($("x-dual-pick").children.length, 4);
  await view.setMode("species");
  assert.equal(field.disabled, false);
  assert.equal(field.value, "chu");
  assert.deepEqual(species(), [25, 26]);
  view.filterSets.species.clear();
  assert.equal(field.value, "");
  assert.equal(view.filterSets.species.controls.get("type").select.value, "");
  assert.deepEqual(species(), [25, 26, 94, 642]);
  await view.setMode("moves");
  assert.equal(field.value, "chu");
  assert.equal(view.filterSets.moves.controls.get("power").a.value, "100");
  view.filterSets.moves.clear();
  assert.equal(field.value, "");
  assert.equal(view.filterSets.moves.controls.get("power").a.value, "");
  assert.deepEqual(moves(), [1, 85, 87]);
  delete globalThis.requestAnimationFrame;
  delete globalThis.document;
});

test("search input opens the list drawer on a narrow viewport", async (context) => {
  const fake = () => ({
    children: [], value: "", textContent: "", dataset: {}, style: {}, hidden: false,
    scrollTop: 0, clientHeight: 400, listeners: {}, classList: { toggle() {} },
    get options() { return this.children; },
    addEventListener(event, fn) {
      const prior = this.listeners[event];
      this.listeners[event] = prior ? (arg) => { prior(arg); fn(arg); } : fn;
    },
    setAttribute(key, value) { this[key] = value; },
    append(...kids) { this.children.push(...kids.flat()); },
    replaceChildren(...kids) { this.children = kids.flat(); },
  });
  const originals = Object.fromEntries(["document", "window", "location", "history", "localStorage", "requestAnimationFrame", "fetch"]
    .map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  context.after(() => {
    for (const [key, descriptor] of Object.entries(originals)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  });
  const elements = new Map();
  const $ = (id) => {
    if (!elements.has(id)) elements.set(id, fake());
    return elements.get(id);
  };
  const title = { ...fake(), dataset: { ui: "Pokédex · gen3tl" }, textContent: "Pokédex · gen3tl" };
  const document = { ...fake(), baseURI: "http://fixture/", getElementById: $,
    querySelector: (selector) => (selector === "title" ? title : null),
    querySelectorAll: (selector) => (selector === "[data-ui]" ? [title] : []),
    createElement: fake, createTextNode: (text) => ({ text }) };
  const window = { ...fake(), scrollY: 0, innerWidth: 760, scrollTo(_x, y) { this.scrollY = y; } };
  const location = { hash: "#game/en" };
  const history = { state: null, replaceState() {}, pushState() {}, back() {} };
  const { fakeFetch } = await import("./fixture.mjs");
  const fetcher = fakeFetch({
    "http://fixture/data/registry.json": { schema: 1, games: [{ id: "game", name: "Game", stage: "translation", languages: ["ru"], data: "./" }] },
    "http://fixture/data/languages.json": { profiles: [{ id: "game", name: "Game", pokedex: "pokedex.json" }],
      languages: [{ profile: "game", code: "ru", index: "index.json", texts: "texts.json" }] },
    "http://fixture/data/texts.json": { schema: 1, profile: "game", language: "ru", texts: {
      "species:1": ["First", ""], "species:2": ["Second", ""], "types:12": ["Grass", ""],
    } },
    "http://fixture/data/pokedex.json": { species: [1, 2].map((slot) => ({ slot, dex: slot, name: `species:${slot}`, types: [12] })) },
  });
  Object.assign(globalThis, { document, window, location, history, localStorage: fakeStorage(),
    requestAnimationFrame: (fn) => fn(), fetch: fetcher });
  await import("../js/dexpage.js?drawer-search-test");
  for (let i = 0; i < 20; i += 1) await new Promise((resolve) => setImmediate(resolve));
  assert.equal($("banner").textContent, "");
  assert.equal($("app").dataset.drawer, undefined);
  const field = $("x-search");
  field.value = "Second";
  field.listeners.input();
  assert.equal($("app").dataset.drawer, "list");
  assert.equal($("show-list")["aria-expanded"], "true");
  assert.equal($("x-list-space").children.length, 1);
  field.value = "First";
  field.listeners.input();
  assert.equal($("app").dataset.drawer, "list");
  $("show-list").listeners.click();
  assert.equal($("app").dataset.drawer, undefined);
  window.innerWidth = 761;
  field.listeners.input();
  assert.equal($("app").dataset.drawer, undefined);
  for (let i = 0; i < 20; i += 1) await new Promise((resolve) => setImmediate(resolve));
});

test("search clearing restores results and focus across tabs and saved queries", async (context) => {
  const { $ } = stubPage(context, "pokedex");
  const { PokedexView } = await import("../js/pokedex/pokedex.js");
  context.after(() => setLabelSource(null));
  const data = {
    species: [1, 2].map((slot) => ({ slot, dex: slot, name: `species:${slot}`, types: [0], learnset: [] })),
    moves: [1, 2].map((id) => ({ id, name: `moves:${id}`, type: 0, damage_category: 0 })),
    items: [1, 2].map((id) => ({ id, name: `items:${id}` })),
    types: [{ id: 0 }, { id: 1 }], type_effectiveness: [],
  };
  const view = new PokedexView({ data: () => data, load: async () => {}, entry: async (id) => ({ source_text: id }),
    spriteSrc: (path) => path, setHash() {} }, $);
  const field = $("x-search"), clear = $("x-search-clear");
  field.focus = () => { document.activeElement = field; };
  const type = (value) => { field.value = value; field.listeners.input(); };
  const escape = () => {
    let prevented = false;
    field.listeners.keydown({ key: "Escape", preventDefault() { prevented = true; } });
    assert.equal(prevented, true);
  };
  const assertCleared = () => {
    assert.equal(field.value, "");
    assert.equal(clear.hidden, true);
    assert.equal(document.activeElement, field);
    assert.equal(view.filterState().search, "");
  };
  await view.show();
  assert.equal(clear.hidden, true);
  for (const kind of ["species", "moves", "items"]) {
    await view.setMode(kind);
    const count = () => kind === "species" ? view.filtered.length : view.dataView.filtered.length;
    assert.equal(count(), 2);
    for (const activate of [() => clear.listeners.click(), escape]) {
      type("no matching entry");
      assert.equal(count(), 0);
      assert.equal(clear.hidden, false);
      document.activeElement = clear;
      activate();
      assertCleared();
      assert.equal(count(), 2);
    }
    type(" ");
    assert.equal(clear.hidden, false);
    type("");
    assert.equal(clear.hidden, true);
  }
  type("items:2");
  await view.setMode("species");
  assert.equal(clear.hidden, true);
  await view.setMode("items");
  assert.equal(field.value, "items:2");
  assert.equal(clear.hidden, false);
  view.resetFilters();
  assert.equal(clear.hidden, true);
  await view.setMode("species");
  view.filterSets.species.set("type", "0");
  type("no matching entry");
  escape();
  assertCleared();
  assert.equal(view.filterSets.species.controls.get("type").select.value, "0");
  await view.setMode("types");
  type("no matching entry");
  assert.equal($("x-type-chart").children[1].children.length, 0);
  escape();
  assertCleared();
  assert.equal($("x-type-chart").children[1].children.length, 2);
  escape();
  assertCleared();
});

test("hidden direct routes show the not-listed state and retain raw records", async (context) => {
  const { $ } = stubPage(context, "pokedex");
  const { PokedexView } = await import("../js/pokedex/pokedex.js");
  context.after(() => setLabelSource(null));
  const data = {
    species: [1, 7].map((slot) => ({ slot, dex: slot, name: `species:${slot}`, types: [0], learnset: [] })),
    moves: [0, 7].map((id) => ({ id, name: `moves:${id}`, type: 0, damage_category: 0 })),
    items: [0, 7].map((id) => ({ id, name: `items:${id}` })),
    species_browsing: { "7": "unreleased" }, moves_browsing: { "7": "unreleased" }, items_browsing: { "7": "unreleased" },
  };
  const before = structuredClone(data), hashes = [];
  const view = new PokedexView({ data: () => data, load: async () => {}, entry: async (id) => ({ source_text: id }),
    spriteSrc: (path) => path, setHash: (hash) => hashes.push(hash) }, $);
  view.reset({ kind: "species", id: "7" });
  await view.show();
  assert.equal($("x-not-listed").hidden, false);
  assert.equal($("x-species-card").hidden, true);
  for (const kind of ["species", "moves", "items"]) {
    for (const lang of ["en", "translation"]) {
      const id = lang === "en" ? "7" : "07";
      await view.goTo({ kind, id });
      view.setLang(lang);
      assert.equal(hashes.at(-1), `${kind}/${id}`);
      assert.equal($("x-not-listed").hidden, false);
      assert.equal($("x-not-listed").textContent, "Not listed");
      assert.equal($(view.model.card).hidden, true);
      await view.goTo({ kind, id: kind === "species" ? 1 : 0 });
      assert.equal($("x-not-listed").hidden, true);
      assert.equal($(view.model.card).hidden, false);
    }
  }
  for (const kind of ["moves", "items"]) {
    await (kind === "moves" ? view.openMove("07") : view.openItem("07"));
    assert.equal(hashes.at(-1), `${kind}/07`);
    assert.equal($("x-not-listed").hidden, false);
    assert.equal($(view.model.card).hidden, true);
  }
  assert.equal(view.record(7), data.species[1]);
  assert.deepEqual(data, before);
});

test("card recents navigate every card kind and retain the pinned current card", async (context) => {
  const { $ } = stubPage(context, "pokedex");
  const { PokedexView } = await import("../js/pokedex/pokedex.js");
  context.after(() => setLabelSource(null));
  const data = {
    species: [1, 2].map((slot) => ({ slot, dex: slot, name: `species:${slot}`, sprite: { front: `${slot}.png` } })),
    moves: [1, 2].map((id) => ({ id, name: `moves:${id}`, damage_category: 0 })),
    items: [1, 2].map((id) => ({ id, name: `items:${id}`, icon: `${id}.png` })),
    trainers: [1, 2].map((id) => ({ id, name: `trainer_names:${id}`, class: "trainer_classes:1",
      sprite: `${id}.png`, party: [{ species: 1, level: 5 }] })),
    encounters: [1, 2].map((id) => ({ runtime_map: `1.${id}`, map_section: id, species: 1, method: "land" })),
    types: [{ id: 0 }], type_effectiveness: [],
  };
  const hashes = [];
  let memory = new DexMemory("fixture", SOURCE_LANGUAGE, () => fakeStorage());
  const view = new PokedexView({ data: () => data, load: async () => {}, entry: async (id) => ({ source_text: id }),
    memory: () => memory, spriteSrc: (path) => path, setHash: (hash) => hashes.push(hash) }, $);
  await view.show();
  const chips = () => $("x-card-recent").children;
  const button = (chip) => chip.children[0];
  const current = () => chips().filter((chip) => chip.className.includes(" cur"));
  for (const id of [1, 2]) await view.goTo({ kind: "moves", id });
  assert.equal($("c-card-recent").hidden, true);
  assert.equal($("x-recent").hidden, true);
  assert.equal(chips().length, 0);
  assert.deepEqual(memory.state.modes.moves.recent, []);
  for (const kind of ["species", "items", "trainers", "locations"]) {
    const ids = kind === "locations" ? ["1", "2"] : [1, 2];
    for (const id of ids) await view.goTo({ kind, id });
    assert.equal($("c-card-recent").hidden, false, kind);
    assert.equal($("x-card-recent").hidden, false, kind);
    assert.deepEqual(chips().map((chip) => button(chip).getAttribute("data-id")), ["2", "1"]);
    assert.equal(current().length, 1);
    assert.equal(button(current()[0]).getAttribute("aria-current"), "true");
    assert.equal(button(current()[0]).getAttribute("data-id"), "2");
    if (kind !== "species") assert.ok(chips().every((chip) => button(chip).textContent.length > 0));
    view.toggleFavourite(ids[1]);
    assert.equal(button(current()[0]).getAttribute("data-id"), "2");
    assert.deepEqual(memory.state.modes[kind].recent, [ids[0]]);
    assert.equal($("x-recent").children.length, 1);
    const before = hashes.length;
    $("x-card-recent").scrollLeft = 200;
    await button(chips()[1]).listeners.click();
    assert.deepEqual(hashes.slice(before), [`${kind}/1`]);
    assert.equal($("x-card-recent").scrollLeft, 0);
    assert.equal(view.currentId(), ids[0]);
    assert.equal(button(current()[0]).getAttribute("data-id"), "1");
    view.toggleFavourite(ids[0]);
    assert.equal($("x-recent").hidden, true);
    assert.equal($("c-card-recent").hidden, false);
    assert.equal(chips().length, 1);
  }
  await view.goTo({ kind: "types", id: 0 });
  assert.equal($("c-card-recent").hidden, true);
  assert.equal(chips().length, 0);
  memory = new DexMemory("fixture", SOURCE_LANGUAGE, () => fakeStorage(true));
  await view.goTo({ kind: "species", id: 1 });
  assert.equal($("c-card-recent").hidden, true);
  assert.equal(chips().length, 0);
});

test("all card modes use the same list and one navigation entry", async (context) => {
  const { $ } = stubPage(context, "pokedex");
  const { PokedexView } = await import("../js/pokedex/pokedex.js");
  const { setLabelSource } = await import("../js/ui.js");
  context.after(() => setLabelSource(null));
  const data = {
    species: [{ slot: 25, dex: 25, name: "species:25", types: [0], learnset: [] }],
    moves: [{ id: 33, name: "moves:33", type: 0, damage_category: 0, effect: 0, flags: 0 }],
    items: [{ id: 4, name: "items:4" }], types: [{ id: 0 }], type_effectiveness: [],
    trainers: [{ id: 7, name: "trainer_names:7", class: "trainer_classes:1", party: [{ species: 25, level: 5 }] }],
    encounters: [{ runtime_map: "3.69", map_section: 102, variant: "day", method: "land", slot: 0,
      species: 25, min_level: 5, max_level: 5, percentage: 100, encounter_rate: 15 }],
  };
  const hashes = [], memory = new DexMemory("game", "en", () => fakeStorage());
  const view = new PokedexView({ data: () => data, load: async () => {}, entry: async (id) => ({ source_text: id }),
    memory: () => memory, spriteSrc: (path) => path, setHash: (hash) => hashes.push(hash) }, $);
  await view.show();
  const list = view.list;
  for (const [kind, id, open] of [
    ["moves", 33, () => view.gotoMove(33)], ["items", 4, () => view.gotoItem(4)],
    ["locations", "102", () => view.openLocation("3.69")],
    ["trainers", 7, () => view.goTo({ kind: "trainers", id: 7 })],
    ["species", 25, () => view.openSpecies(25)], ["types", 0, () => view.pickDual(0)],
  ]) {
    const before = hashes.length;
    await open();
    assert.deepEqual(hashes.slice(before), [`${kind}/${id}`]);
    assert.equal(view.mode, kind);
    assert.equal(memory.state.modes[kind].last, id);
    assert.equal(view.list, list);
    assert.equal(view.dataView.list, list);
    assert.equal(view.list.list, $("x-list"));
    assert.equal($(view.model.card).hidden, false);
    assert.equal($("x-search").getAttribute("aria-label"), "Search");
    assert.equal($("x-search").getAttribute("placeholder"), "Search");
  }
  // links open the compact card; without dialogs, the page
  const pushed = hashes.length;
  const dialog = { available: true, node: null, shown: [], show(content, title) { this.shown.push(title ?? "move"); this.node = { open: true }; }, close() {} };
  view.moveDialog = dialog;
  const moves = data.moves;
  data.moves = Object.assign([], { 33: moves[0] }); // move records are read by id
  await view.openMove(33);
  data.moves = moves;
  await view.openItem(4);
  assert.equal(dialog.shown.length, 2);
  assert.deepEqual(hashes.slice(pushed), []);
  view.moveDialog = { available: false, close() {} };
  await view.openMove(33);
  await view.openItem(4);
  assert.deepEqual(hashes.slice(pushed), ["moves/33", "items/4"]);
});

test("declared complete and suffix form names load and search through catalog references", () => {
  const family = [
    { slot: 1, dex: 1, base_species: 1, name: "species:1" },
    { slot: 2, dex: 1, base_species: 1, name: "species:2", form_name: "species:2", form_name_full: true },
    { slot: 3, dex: 1, base_species: 1, name: "species:3", form_name: "s0000100", form_name_full: false },
    { slot: 1048, dex: 1, base_species: 1, name: "species:1048", form_name: null, form_name_full: false },
  ];
  const text = (id) => ({ "species:1": "Unown", "species:2": "Unown B", "species:3": "Furfrou",
    "species:1048": "Unresolved", "s0000100": "Heart" })[id] ?? "";
  assert.equal(displayName(family[1], family, text), "Unown B");
  assert.equal(displayName(family[2], family, text), "Furfrou Heart");
  assert.ok(recordIds(family[2]).includes("s0000100"));
  assert.equal(formLabel(family[3], family, text), "Form 1048");
  assert.deepEqual(filterSpecies(family, "Heart", text, null, text, () => [], true), [0]);
});

test("profile gender symbols label declared forms without translated words", () => {
  const symbols = { "Female form": "♀", "Male form": "♂" };
  setLabelSource((key) => symbols[key] ? { text: symbols[key], game: false } : null);
  try {
    const forms = [{ slot: 1, dex: 1, name: "species:1", form_name: "page:Male form" },
      { slot: 2, dex: 1, name: "species:2", form_name: "page:Female form" }];
    const text = () => "Meowstic";
    assert.equal(displayName(forms[0], forms, text), "Meowstic ♂");
    assert.equal(displayName(forms[1], forms, text), "Meowstic ♀");
  } finally {
    setLabelSource(null);
  }
});

test("species columns resolve representative stats, labels and missing values", () => {
  const records = [
    { slot: 400, dex: 25, name: "species:400", types: [1, 1, 2], abilities: [3, 3, 4],
      base_stats: { hp: 40, attack: 50, defense: 60, sp_attack: 70, sp_defense: 80, speed: 90 } },
    { slot: 401, dex: 25, base_species: 400, name: "species:401", base_stats: { hp: 100 } },
  ];
  const labels = { "species:400": "Base", "species:401": "Form", "types:1": "Fire", "types:2": "Flying", "abilities:3": "Blaze", "abilities:4": "Solar Power" };
  const fields = speciesListFields(records[0], records, (id) => labels[id] ?? "");
  assert.equal(fields.dex, 400);
  assert.equal(fields.types, "Fire / Flying");
  assert.equal(fields.abilities, "Blaze / Solar Power");
  assert.equal(fields.total, 390);
  assert.equal(speciesListFields(records[1], records, (id) => labels[id] ?? "").total, null);
  assert.equal(speciesListFields({ slot: 9, dex: 0 }, [], () => "").dex, null);
});

test("species sorting covers the complete filtered set with internal ID ties and missing values last", () => {
  const records = Array.from({ length: 90 }, (_, i) => ({ slot: 300 - i, dex: i + 1, name: `species:${i}`, types: [i % 2], abilities: [i % 3],
    base_stats: { hp: 20 + i % 3, attack: i, defense: 1, sp_attack: 1, sp_defense: 1, speed: 1 } }));
  records.push({ slot: 400, dex: 0, name: "unknown" });
  const text = (id) => ({ "types:0": "Water", "types:1": "Fire", "abilities:0": "Z", "abilities:1": "A", "abilities:2": "B" })[id] ?? id;
  const indices = records.map((_, i) => i).reverse();
  for (const key of SPECIES_SORT_KEYS) {
    for (const direction of ["asc", "desc"]) {
      const sorted = sortSpeciesIndices(indices, records, text, { key, direction });
      const values = sorted.map((i) => speciesListFields(records[i], records, text)[key]);
      const collator = new Intl.Collator("en", { numeric: true, sensitivity: "base" });
      for (let i = 1; i < values.length; i++) {
        const a = values[i - 1], b = values[i];
        if (b == null || b === "") continue;
        assert.ok(a != null && a !== "");
        const comparison = typeof a === "number" ? a - b : collator.compare(a, b);
        assert.ok(direction === "asc" ? comparison <= 0 : comparison >= 0, `${key} ${direction}`);
        if (!comparison) assert.ok(compareSpecies(records[sorted[i - 1]], records[sorted[i]]) <= 0);
      }
    }
  }
  assert.equal(sortSpeciesIndices(indices, records, text, { key: "attack", direction: "desc" })[0], 89);
  assert.deepEqual(indices, records.map((_, i) => i).reverse());
});

test("species mode persists validated sorting alongside pins and filter memory", () => {
  const storage = fakeStorage();
  const memory = new DexMemory("test", "ru", () => storage);
  memory.state.modes.species.sort = { key: "total", direction: "desc" };
  memory.state.modes.species.filters = { search: "25" };
  memory.pin("species", 25);
  memory.save();
  const restored = new DexMemory("test", "ru", () => storage);
  assert.deepEqual(restored.state.modes.species.sort, { key: "total", direction: "desc" });
  assert.equal(restored.state.modes.species.filters.search, "25");
  assert.ok(restored.state.modes.species.favourites.includes(25));
  assert.deepEqual(speciesSort({ key: "bogus", direction: "bad" }), { key: "dex", direction: "asc" });
  assert.equal(restored.state.modes.items.sort, undefined);
});

test("species number searches promote the displayed ID and preserve other sorting", async () => {
  const records = [12, 2, 32, 22, 42, 52].map((slot) => ({
    slot, dex: slot === 42 ? 2 : slot + 100, name: `species:${slot}`, base_stats: { attack: slot },
  }));
  const labels = { "species:12": "Other", "species:2": "Exact", "species:32": "2 Prefix",
    "species:22": "Contains 2", "species:42": "National", "species:52": "Other" };
  const field = { value: "2" }, ordering = { key: "attack", direction: "desc" };
  const view = {
    records, filterContext: () => ({}), familyIndex: () => speciesFamilyIndex(records),
    rememberFilters() {}, ensureCatalogs() {}, text: (id) => labels[id] ?? "",
    otherNames: () => [], searchAliases: () => [], modeState: () => ({ sort: ordering }),
    $: () => field, showCount() {}, list: { setCount() {} }, updateCross() {},
    filterSets: { species: { values: () => ({}), prepare: () => ({}), matches: () => true, refresh() {} } },
    host: { data: () => ({ species: records }) },
  };
  const { PokedexView } = await import("../js/pokedex/pokedex.js");
  const results = (query) => {
    field.value = query;
    PokedexView.prototype.applyFilters.call(view);
    return view.filtered.map((i) => records[i].slot);
  };
  for (const query of ["2", " 2 "]) {
    assert.deepEqual(results(query), [2, 32, 22, 52, 42, 12]);
    assert.equal(view.strong, 3);
    assert.deepEqual(view.order[0], { own: 0 });
  }
  assert.deepEqual(results(""), [52, 42, 32, 22, 12, 2]);
  assert.deepEqual(results("Other"), [52, 12]);
  ordering.direction = "asc";
  assert.deepEqual(results("2"), [2, 22, 32, 12, 42, 52]);
});

test("species sorting retains global-search relevance tiers and labeled cross results", async () => {
  const records = [
    { slot: 1, dex: 100, name: "species:1", abilities: [1], base_stats: { attack: 10 } },
    { slot: 2, dex: 2, name: "species:2", abilities: [1], base_stats: { attack: 90 } },
    { slot: 3, dex: 3, name: "species:3", abilities: [1], base_stats: { attack: 80 } },
  ];
  const labels = { "species:1": "Meloetta", "species:2": "Pikachu", "species:3": "Raichu", "abilities:1": "Melodic", "trainer:1": "Mel" };
  const data = { species: records, trainers: [{ id: 7, name: "trainer:1" }] };
  const view = {
    records, generation: 1, mode: "species", lang: "en", filterContext: () => ({}),
    familyIndex: () => speciesFamilyIndex(records), rememberFilters() {}, ensureCatalogs() {},
    text: (id) => labels[id] ?? "", otherNames: () => [], searchAliases: () => [],
    modeState: () => ({ sort: { key: "attack", direction: "desc" } }),
    $: () => ({ value: "Mel" }), showCount() {}, list: { setCount() {} }, renderList() {},
    filterSets: { species: { values: () => ({}), prepare: () => ({}), matches: () => true, refresh() {} } },
    host: { data: () => data }, dataView: { source: (id) => labels[id] ?? "" }, ensureCrossNames: async () => {},
  };
  const { PokedexView } = await import("../js/pokedex/pokedex.js");
  view.updateCross = PokedexView.prototype.updateCross.bind(view);
  PokedexView.prototype.applyFilters.call(view);
  await Promise.resolve();
  assert.deepEqual(view.filtered, [0, 1, 2]);
  assert.equal(view.strong, 1);
  assert.deepEqual(view.order, [{ own: 0 }, { cross: 0 }, { own: 1 }, { own: 2 }]);
  assert.equal(view.cross[0].kind, "trainers");
  assert.equal(view.cross[0].name, "Mel");
  assert.equal(view.cross[0].id, 7);
});

test("trade sources retain requested species links through localized surrounding text", () => {
  const source = { kind: "trade", species: 237, wanted: 108, map_section: 155, item: 460 };
  const before = JSON.stringify(source);
  setLabelSource((key) => ({
    "Trade for {species}": { text: "Обмен на {species}", game: false },
    "in {map}": { text: "({map})", game: false },
    "with {item}": { text: "с предметом {item}", game: false },
  })[key] ?? null);
  try {
    const parts = sourceParts(source, (id) => ({ "species:108": "Ликитанг", "mapsec:155": "Питомник", "items:460": "Мьютунит Y" })[id] ?? "");
    assert.deepEqual(parts, [{ text: "Обмен на " }, { species: 108, text: "Ликитанг" },
      { text: " (Питомник) с предметом " }, { item: 460, text: "Мьютунит Y" }]);
    assert.equal(JSON.stringify(source), before);
  } finally { setLabelSource(null); }
  assert.deepEqual(sourceParts({ kind: "trade", wanted: 108 }, () => ""),
    [{ text: "Trade for " }, { species: 108, text: "Species 108" }]);
});

test("requested trade forms use their canonical form label", () => {
  const records = [{ slot: 27, dex: 27, name: "species:27" },
    { slot: 1023, dex: 27, name: "species:1023", form_name: "page:Alolan" }];
  const text = (key) => ({ "species:27": "Sandshrew", "species:1023": "Sandshrew", "page:Alolan": "Alolan" })[key] ?? "";
  const parts = sourceParts({ kind: "trade", wanted: 1023 }, text, [], records);
  assert.deepEqual(parts.find((part) => part.species != null), { species: 1023, text: "Sandshrew Alolan" });
});

test("an alternative trade location stays qualified as the same trade", () => {
  const parts = sourceParts({ kind: "trade", wanted: 95, availability: "alternate_trade" },
    (id) => id === "species:95" ? "Onix" : "");
  assert.deepEqual(parts.find((part) => part.species != null), { species: 95, text: "Onix" });
  assert.match(parts.at(-1).text, /Another location for the same trade/);
  assert.match(parts.at(-1).text, /schedule and progress/);
});

test("visiting trade sources retain links and disclose conditional availability", () => {
  const parts = sourceParts({ kind: "trade", wanted: 108, availability: "visiting_trader" },
    (id) => id === "species:108" ? "Lickitung" : "");
  assert.deepEqual(parts.filter((part) => part.species != null), [{ species: 108, text: "Lickitung" }]);
  assert.match(parts.at(-1).text, /Visiting trader; the offer depends on schedule and progress/);
});

test("trade OT metadata uses an explicit label without asserting NPC identity", () => {
  const source = { kind: "trade", species: 237, wanted: 108, original_trainer: "s1F81E2A" };
  assert.deepEqual(sourceParts(source, (id) => ({ "species:108": "Lickitung", s1F81E2A: "Saige" })[id] ?? ""),
    [{ text: "Trade for " }, { species: 108, text: "Lickitung" }, { text: " (Original Trainer: Saige)" }]);
  assert.deepEqual(sourceParts(source, (id) => id === "species:108" ? "Lickitung" : ""),
    [{ text: "Trade for " }, { species: 108, text: "Lickitung" }]);
  assert.deepEqual(sourceParts({ ...source, kind: "egg" }, () => "Saige"), [{ text: "Egg from an NPC" }]);
});

test("trade original trainer is omitted only when it repeats the shown character", () => {
  const text = (id) => ({
    "species:474": "Cherrim", "species:214": "Heracross", "species:108": "Lickitung",
    speakerSame: "Mirskle", trainerSame: "Mirskle",
    speakerDifferent: "Benjamin", trainerDifferent: "Benjy", trainerUnnamed: "Saige",
  })[id] ?? "";
  assert.deepEqual(sourceParts({ kind: "trade", wanted: 474, speaker: "speakerSame", original_trainer: "trainerSame" }, text),
    [{ text: "Mirskle: trade for " }, { species: 474, text: "Cherrim" }]);
  assert.deepEqual(sourceParts({ kind: "trade", wanted: 214, speaker: "speakerDifferent", original_trainer: "trainerDifferent" }, text),
    [{ text: "Benjamin: trade for " }, { species: 214, text: "Heracross" }, { text: " (Original Trainer: Benjy)" }]);
  assert.deepEqual(sourceParts({ kind: "trade", wanted: 108, original_trainer: "trainerUnnamed" }, text),
    [{ text: "Trade for " }, { species: 108, text: "Lickitung" }, { text: " (Original Trainer: Saige)" }]);
});

test("named trade sources show the offering character in English and Russian", (context) => {
  context.after(() => setCatalog(null));
  setCatalog(null);
  const source = { kind: "trade", species: 565, wanted: 474, speaker: "s1F15C64" };
  assert.deepEqual(sourceParts(source, (id) => ({ "species:474": "Cherrim", s1F15C64: "Mirskle" })[id] ?? ""),
    [{ text: "Mirskle: trade for " }, { species: 474, text: "Cherrim" }]);
  setCatalog({ "{name}: trade for {species}": "{name}: обмен на {species}" });
  assert.deepEqual(sourceParts(source, (id) => ({ "species:474": "Черрим", s1F15C64: "Мёрскл" })[id] ?? ""),
    [{ text: "Мёрскл: обмен на " }, { species: 474, text: "Черрим" }]);
  assert.deepEqual(sourceParts({ kind: "trade", wanted: 474 }, () => ""),
    [{ text: "Trade for " }, { species: 474, text: "Species 474" }]);
});

test("species ability presentation keeps displayed and underlying names searchable", () => {
  const record = { slot: 1, dex: 10, name: "species:1", abilities: [1],
    ability_details: [{ id: 1, name: "abilities:3", description: "s0000123", regular: false, hidden: true }] };
  const form = { ...record, slot: 2, name: "species:2", base_species: 1,
    ability_details: [{ id: 1, name: "abilities:4", description: "s0000124", regular: true, hidden: false }] };
  const source = { "species:1": "Example", "species:2": "Example form", "abilities:1": "Base name",
    "abilities:3": "Cloud name", "abilities:4": "Form name", "s0000123": "Species effect", "s0000124": "Form effect" };
  const translated = { ...source, "abilities:1": "Translated base", "abilities:3": "Translated cloud" };
  const text = (id) => translated[id] ?? "", english = (id) => source[id] ?? "";
  const abilities = [{ id: 1, name: "abilities:1", description: "s0000999" }];
  assert.deepEqual(detailOf(record, text, abilities).abilities, [{ id: "abilities:3", text: "Translated cloud",
    descriptionId: "s0000123", description: "Species effect", regular: false, hidden: true }]);
  assert.equal(detailOf(form, text, abilities).abilities[0].description, "Form effect");
  const search = createSpeciesSearch([record, form], text, english);
  for (const query of ["base name", "translated base", "cloud name", "translated cloud"]) {
    assert.ok(search.filter(query).includes(0), query);
  }
  assert.deepEqual(filterSpecies([record, form], "", text, { ability: 1 }), [0, 1]);
  assert.equal(speciesListFields(record, [record, form], text).abilities, "Translated cloud");
  const regular = { ...record, ability_details: [{ ...record.ability_details[0], regular: true, hidden: false }] };
  assert.deepEqual(abilityChanges(regular, form, text).map(abilityChangeText), ["Translated cloud ➝ Form name"]);
  assert.deepEqual(abilityChanges(regular, form, text, 253), []);
});
