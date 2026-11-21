import assert from "node:assert/strict";

import { readFileSync } from "node:fs";

import { test } from "node:test";

import { DexMemory } from "../js/dexpage.js";

import { FILTER_SETS, FilterSet, filterText, migrateFilters } from "../js/filters.js";

import { fillPage, gameLabel, setCatalog, setLabelSource, setUiLanguage, SOURCE_LANGUAGE, t } from "../js/ui.js";

import { PokedexView } from "../js/pokedex/pokedex.js";

import { configureListScroll } from "../js/pokedexexplorer.js";

import { EntryList } from "../js/listview.js";

import { moveFields, PokedexDataView } from "../js/pokedexdataview/pokedexdataview.js";

import { renderCatchCalculator } from "../js/catchcalc.js";

import { fakeStorage, STANDALONE_LABELS, stubPage } from "./fixture.mjs";

const emptyContext = {
  types: [], moves: [], species: [], abilities: [], items: [], data: {},
  text: (id) => id, source: () => "", otherNames: () => [],
  speciesName: () => "", speciesNames: () => [],
};

test("the sticky bar shares live search, counts, chips and Reset across list modes", (context) => {
  const { $ } = stubPage(context, "pokedex");
  const mast = document.createElement("header"), heading = document.createElement("div"), rail = document.createElement("aside");
  const bar = $("c-list-bar"), field = $("x-search"), search = $("c-list-search"), toggle = $("c-list-toggle");
  const controls = $("c-list-controls"), observed = [];
  const win = { innerWidth: 1440, innerHeight: 800, scrollX: 0, scrollY: 0,
    Event: class { constructor(type) { this.type = type; } },
    KeyboardEvent: class { constructor(type, values) { this.type = type; Object.assign(this, values); } },
    ResizeObserver: class { constructor(fn) { this.run = fn; } observe(node) { observed.push(node); } },
    addEventListener() {}, removeEventListener() {}, scrollTo(_x, y) { this.scrollY = y; },
  };
  mast.getBoundingClientRect = () => ({ height: win.innerWidth <= 760 ? 54 : 72 });
  rail.getBoundingClientRect = () => ({ height: 70 });
  bar.getBoundingClientRect = () => ({ top: mast.getBoundingClientRect().height, height: 120 });
  const find = (node, id) => node.id === id || node.getAttribute?.("id") === id ? node
    : (node.children ?? []).map((child) => find(child, id)).find(Boolean);
  const root = { body: document.body,
    getElementById: (id) => id === "c-rail" ? rail : $(id) ?? find($("x-filters"), id) ?? find(controls, id),
    querySelector: (selector) => selector === ".mast" ? mast : heading,
  };
  const list = new EntryList($("x-list"), $("x-list-space"), (p) => ({ p }));
  list.space.getClientRects = () => [];
  const memory = {}, queries = [], keys = [];
  const view = { mode: "species", list, filterSets: {},
    activateMode(mode) { this.mode = mode; this.renderFilters(); },
    renderFilters() {
      const set = this.filterSets[this.mode];
      set.render(emptyContext);
      set.details.getBoundingClientRect = () => ({ top: 1300 - win.scrollY });
    },
    syncSearchClear() {}, rememberFiltersOpen(mode, open) { memory[mode] = open; },
    resetFilters() { field.value = ""; this.syncSearchClear(); },
    clearSearch() { this.resetFilters(); },
  };
  for (const mode of ["species", "moves", "items", "trainers", "locations", "types"]) {
    const set = new FilterSet(mode, FILTER_SETS[mode], {
      container: $("x-filters"), onChange: () => set.refresh(set.values(), 3, 10),
      onReset: () => { field.value = ""; view.syncSearchClear(); set.refresh(set.values(), 10, 10); },
      onOpenChange: (open) => view.rememberFiltersOpen(mode, open),
    });
    set.open = false;
    view.filterSets[mode] = set;
  }
  field.dispatchEvent = (event) => {
    if (event.type === "input") { queries.push(field.value); view.syncSearchClear(); }
    else keys.push(event.key);
  };
  configureListScroll(view, root, win);
  assert.deepEqual(observed, [bar, $("x-filters"), heading, mast]);
  for (const width of [1440, 390]) {
    win.innerWidth = width;
    for (const mode of ["species", "moves", "items", "trainers", "locations"]) {
      view.activateMode(mode);
      const set = view.filterSets[mode];
      assert.equal(bar.hidden, false);
      assert.equal(list.page, win);
      assert.deepEqual(list.insets(), { top: width === 390 ? 174 : 192, bottom: width === 390 ? 70 : 0 });
      assert.equal(toggle.getAttribute("aria-controls"), `x-filter-block-${mode}`);
      assert.equal(toggle.getAttribute("aria-expanded"), "false");
      assert.equal(controls.children[0], set.countNode);
      assert.equal(controls.children[1], set.chipsNode);
      set.refresh({}, 3, 10);
      assert.equal(controls.children[0].textContent, "3 / 10");
      search.value = "query";
      search.listeners.input();
      assert.equal(field.value, "query");
      field.value = "heading query";
      view.syncSearchClear();
      assert.equal(search.value, "heading query");
      assert.equal($("c-list-clear").hidden, false);
      let focused = false;
      search.focus = () => { focused = true; };
      $("c-list-clear").listeners.click();
      assert.equal(field.value, "");
      assert.equal(search.value, "");
      assert.equal($("c-list-clear").hidden, true);
      assert.equal(focused, true);
      field.value = "heading query";
      view.syncSearchClear();
      win.scrollY = 4000;
      toggle.listeners.click();
      assert.equal(set.details.open, true);
      assert.equal(memory[mode], true);
      assert.equal(toggle.getAttribute("aria-expanded"), "true");
      assert.equal(win.scrollY, 1300 - list.insets().top);
      if (mode === "moves") {
        set.set("power", { op: ">=", a: 80 });
        assert.equal(controls.children[1].children.length, 1);
        controls.children[1].children[0].listeners.click();
        assert.deepEqual(set.values(), {});
        assert.equal(controls.children[1].children.length, 0);
        set.set("power", { op: ">=", a: 80 });
      }
      controls.children[2].listeners.click({ preventDefault() {} });
      assert.deepEqual(set.values(), {});
      assert.equal(search.value, "");
      assert.equal(controls.children[0].textContent, "10 / 10");
      toggle.listeners.click();
      assert.equal(set.details.open, false);
    }
  }
  search.value = field.value = "query";
  for (const key of ["Enter", "Escape"]) search.listeners.keydown({ key, preventDefault() {} });
  assert.deepEqual(keys, ["Enter"]);
  assert.equal(search.value, "");
  assert.equal(queries.length, 10);
  view.activateMode("types");
  assert.equal(bar.hidden, true);
  assert.equal(list.page, null);
});

test("standalone game labels capitalize cards, chips, options and headings without changing sentences", (context) => {
  const { $ } = stubPage(context, "pokedex");
  context.after(() => { setLabelSource(null); setUiLanguage(SOURCE_LANGUAGE); });
  const snapshot = JSON.stringify(STANDALONE_LABELS);
  for (const language of STANDALONE_LABELS.languages) {
    const texts = new Map(STANDALONE_LABELS.texts.map(([id, english, translated]) =>
      [id, { source_text: english, translation: language.translated ? translated : "" }]));
    const view = { lang: language.code, texts, host: { data: () => ({ labels: STANDALONE_LABELS.labels }) } };
    setUiLanguage(language.code);
    setLabelSource((key) => PokedexView.prototype.label.call(view, key));
    const [flinch, ...statuses] = language.labels;
    assert.deepEqual(moveFields({ flinch_chance: 31, secondary_chance: 30 }).at(-1), [flinch, "31%"]);
    const set = new FilterSet("moves", FILTER_SETS.moves, { container: $("x-filters"), onChange() {} });
    set.render(emptyContext);
    const chance = set.controls.get("flinchChance");
    assert.equal(chance.op.getAttribute("aria-label"), flinch);
    chance.a.value = "1";
    chance.a.listeners.input();
    set.refresh(set.values(), 1, 1);
    assert.equal(set.chipsNode.children[0].textContent, `${flinch} ≥ 1×`);
    assert.equal(set.controls.get("power").op.children.at(-1).textContent, "Between");
    const root = document.createElement("div");
    renderCatchCalculator(root, { rate: 45, baseHp: 50, balls: [{ key: 4, name: "Ball" }] });
    const status = root.querySelectorAll("select").find((node) => node.getAttribute("id") === "x-catch-status");
    assert.deepEqual(status.children.slice(1).map((node) => node.textContent), statuses);
    const heading = document.createElement("th");
    heading.dataset = { ui: "Flinch chance" };
    fillPage({ querySelectorAll: (selector) => selector === "[data-ui]" ? [heading] : [] });
    assert.equal(heading.textContent, flinch);
    const sentence = texts.get("s0000001");
    const raw = sentence.translation || sentence.source_text;
    assert.equal(t("Sentence", { name: "Example" }), gameLabel(raw, { name: "Example" }));
    assert.equal(t("Sentence"), gameLabel(raw));
  }
  assert.equal(JSON.stringify(STANDALONE_LABELS), snapshot);
});

test("metric filter labels retain units when game labels omit them", (context) => {
  setCatalog({ "Weight (kg)": "Вес, кг", "Height (m)": "Рост, м" });
  setLabelSource((key) => key === "Weight (kg)" || key === "Height (m)" ? { text: "game label", game: true } : null);
  context.after(() => { setCatalog(null); setLabelSource(null); });
  assert.equal(filterText("Weight (kg)"), "Вес, кг");
  assert.equal(filterText("Height (m)"), "Рост, м");
});

test("Russian chip removal tooltips preserve nominative labels", (context) => {
  const { $ } = stubPage(context, "pokedex");
  const catalog = readFileSync(new URL("../i18n/ru.po", import.meta.url), "utf8");
  const text = JSON.parse(catalog.match(/^msgid "Remove \{label\}"\nmsgstr (".*")$/m)[1]);
  setCatalog({ "Remove {label}": text, "Evolution kind": "Эволюция", Item: "Предмет" });
  context.after(() => setCatalog(null));
  const set = new FilterSet("species", FILTER_SETS.species, { container: $("x-filters"), onChange() {} });
  set.render(emptyContext);
  set.set("evolution", "item");
  set.refresh(set.values(), 1, 2);
  const chip = set.chipsNode.children[0];
  assert.equal(chip.title, "Убрать фильтр «Эволюция: Предмет»");
  assert.equal(chip.getAttribute("aria-label"), "Убрать фильтр «Эволюция: Предмет»");
});

test("switch buttons apply their labelled values and announce selection", (context) => {
  const { $ } = stubPage(context, "pokedex");
  const set = new FilterSet("moves", FILTER_SETS.moves, { container: $("x-filters"), onChange() {} });
  set.render(emptyContext);
  const buttons = set.controls.get("category").buttons;
  assert.deepEqual(buttons.map((button) => button.textContent), ["All", "Physical", "Special", "Status"]);
  buttons[1].listeners.click();
  assert.deepEqual(set.values(), { category: "0" });
  assert.equal(buttons[1].getAttribute("aria-pressed"), "true");
  assert.equal(set.matches({ damage_category: 0 }, set.values()), true);
  assert.equal(set.matches({ damage_category: 1 }, set.values()), false);
  set.clear();
  assert.deepEqual(set.values(), {});
  assert.equal(buttons[0].getAttribute("aria-pressed"), "true");
});

test("formula and never-miss clicks work alone and remove only their own chips", (context) => {
  const { $ } = stubPage(context, "pokedex");
  const set = new FilterSet("moves", FILTER_SETS.moves, { container: $("x-filters"), onChange() {} });
  set.render(emptyContext);
  const power = set.controls.get("power"), accuracy = set.controls.get("accuracy");
  power.subButtons[1].listeners.click();
  assert.deepEqual(set.values(), { power: { kind: "formula" } });
  assert.equal(set.matches({ power_kind: "computed", power_formula: "formula" }, set.values()), true);
  assert.equal(set.matches({ power: 80 }, set.values()), false);
  power.a.value = "80";
  power.a.listeners.input();
  set.refresh(set.values(), 1, 2);
  assert.deepEqual(set.chipsNode.children.map((node) => node.textContent), ["Power ≥ 80×", "Power: Formula moves×"]);
  set.chipsNode.children[1].listeners.click();
  assert.deepEqual(set.values(), { power: { op: ">=", a: "80" } });
  power.subButtons[1].listeners.click();
  set.refresh(set.values(), 1, 2);
  set.chipsNode.children[0].listeners.click();
  assert.deepEqual(set.values(), { power: { kind: "formula" } });
  set.clear();
  power.subButtons[2].listeners.click();
  assert.equal(set.matches({ power: 80 }, set.values()), true);
  assert.equal(set.matches({ power_kind: "computed", power_formula: "formula" }, set.values()), false);
  set.clear();
  accuracy.subButtons[1].listeners.click();
  assert.deepEqual(set.values(), { accuracy: { never: "never" } });
  assert.equal(set.matches({ accuracy: 0 }, set.values()), true);
  assert.equal(set.matches({ accuracy: 100 }, set.values()), false);
  set.refresh(set.values(), 1, 2);
  assert.equal(set.chipsNode.children[0].textContent, "Accuracy: Never misses×");
});

test("number changes respect inclusive bounds and retain accessible names", (context) => {
  const { $ } = stubPage(context, "pokedex");
  const set = new FilterSet("species", FILTER_SETS.species, { container: $("x-filters"), onChange() {} });
  set.render(emptyContext);
  const weight = set.controls.get("weight");
  weight.op.value = "between";
  weight.op.listeners.change();
  assert.equal(weight.b.hidden, false);
  weight.a.value = "10";
  weight.b.value = "20";
  weight.b.listeners.input();
  for (const [hectograms, matches] of [[99, false], [100, true], [200, true], [201, false]]) {
    assert.equal(set.matches({ weight: hectograms }, set.values()), matches);
  }
  assert.equal(weight.a.getAttribute("aria-label"), "Weight (kg) ≥");
  assert.equal(weight.b.getAttribute("aria-label"), "Weight (kg) ≤");
  set.refresh(set.values(), 1, 2);
  assert.equal(set.chipsNode.children[0].textContent, "Weight (kg): 10–20×");
  set.clear();
  const stat = set.controls.get("stat");
  assert.equal(stat.stat.value, "hp");
  stat.a.value = "50";
  stat.a.listeners.input();
  assert.equal(set.matches({ base_stats: { hp: 49 } }, set.values()), false);
  assert.equal(set.matches({ base_stats: { hp: 50 } }, set.values()), true);
  set.refresh(set.values(), 1, 2);
  assert.equal(set.chipsNode.children[0].textContent, "HP ≥ 50×");
});

test("property clicks retain other chips and the existing secondary-effect filters", (context) => {
  const { $ } = stubPage(context, "pokedex");
  const set = new FilterSet("moves", FILTER_SETS.moves, { container: $("x-filters"), onChange() {} });
  set.render({ ...emptyContext, moves: [{ flag_keys: ["makes_contact", "protect_affected"] }] });
  const choose = (value) => set.controls.get("properties").buttons
    .find((button) => button.getAttribute("data-value") === value).listeners.click();
  choose("makes_contact");
  choose("protect_affected");
  set.refresh(set.values(), 1, 1);
  set.chipsNode.children[0].listeners.click();
  assert.deepEqual(set.values(), { properties: ["protect_affected"] });
  set.clear();
  assert.ok(!set.controls.get("properties").buttons.some((button) => button.getAttribute("data-value") === "flinch"));
  set.set("flinchChance", { op: ">=", a: 1 });
  assert.equal(set.matches({ flinch_chance: 1, secondary_chance: 0 }, set.values()), true);
  assert.equal(set.matches({ flinch_chance: 0, secondary_chance: 30 }, set.values()), false);
  set.clear();
  choose("secondary");
  assert.equal(set.matches({ secondary_chance: 10 }, set.values()), true);
  assert.equal(set.matches({ secondary_chance: 0 }, set.values()), false);
});

test("species keep unordered type criteria and match every evolution kind they support", (context) => {
  const { $ } = stubPage(context, "pokedex");
  const set = new FilterSet("species", FILTER_SETS.species, { container: $("x-filters"), onChange() {} });
  set.render(emptyContext);
  assert.equal(set.matches({ types: [10, 2] }, { type: "2", type2: "10" }), true);
  const buttons = set.controls.get("evolution").buttons;
  const choose = (value) => buttons.find((button) => button.getAttribute("data-value") === value).listeners.click();
  const mixed = { evolution: [{ method: 7 }, { method: 2 }] };
  choose("item");
  assert.equal(set.matches(mixed, set.values()), true);
  choose("level");
  assert.equal(set.matches(mixed, set.values()), true);
  assert.equal(set.matches({ evolution: [{ method: 30 }] }, set.values()), false);
  choose("other");
  assert.equal(set.matches({ evolution: [{ method: 30 }] }, set.values()), true);
  choose("none");
  assert.equal(set.matches({ evolution: [{ method: 254 }] }, set.values()), true);
});

test("flinch comparisons combine declared chances, multiple targets and other criteria", () => {
  const set = new FilterSet("moves", FILTER_SETS.moves, {});
  const moves = [0, 1, 11, 21, 31, 100].map((flinch_chance, id) => ({ id, flinch_chance,
    target_key: id % 2 ? "selected" : "both", type: id % 2, damage_category: 0,
    effect: 900 + id, effect_key: "flinch_hit", secondary_chance: 0, flag_keys: ["makes_contact"] }));
  const ids = (filters) => moves.filter((move) => set.matches(move, filters)).map((move) => move.id);
  for (const [value, expected] of [
    [{ op: ">=", a: 1 }, [1, 2, 3, 4, 5]], [{ op: "<=", a: 11 }, [0, 1, 2]],
    [{ op: "=", a: 0 }, [0]], [{ op: "=", a: 100 }, [5]], [{ op: "between", a: 11, b: 31 }, [2, 3, 4]],
  ]) assert.deepEqual(ids({ flinchChance: value }), expected);
  assert.equal(set.matches({}, { flinchChance: { op: "=", a: 0 } }), false);
  assert.deepEqual(ids({ target: ["both"] }), [0, 2, 4]);
  const combined = { flinchChance: { op: "between", a: 11, b: 31 }, target: ["both", "selected"],
    type: "1", category: "0", effect: "flinch_hit", properties: ["makes_contact"] };
  assert.deepEqual(ids(combined), [3]);
  assert.deepEqual(ids({ ...combined, target: ["user"] }), []);
});

test("move controls update matches, chips, counts, memory and Reset together", async (context) => {
  const { $ } = stubPage(context, "pokedex");
  const moves = [
    { id: 1, flinch_chance: 0, target_key: "user", type: 1 },
    { id: 2, flinch_chance: 31, target_key: "selected", type: 1 },
    { id: 3, flinch_chance: 31, target_key: "both", type: 1 },
    { id: 4, flinch_chance: 100, target_key: "selected", type: 2 },
  ].map((move) => ({ ...move, name: `moves:${move.id}`, damage_category: 0 }));
  const storage = fakeStorage(), memory = new DexMemory("game", SOURCE_LANGUAGE, () => storage);
  const data = { moves, species: [], types: [{ id: 1 }, { id: 2 }] };
  const view = new PokedexView({ data: () => data, load: async () => {}, memory: () => memory,
    entry: async (id) => ({ source_text: id, translation: "" }), spriteSrc: (path) => path, setHash() {},
  }, $);
  view.reset({ kind: "moves" });
  await view.show();
  const set = view.filterSets.moves, chance = set.controls.get("flinchChance");
  const choose = (key) => set.controls.get("target").buttons.find((button) => button.getAttribute("data-value") === key).listeners.click();
  const ids = () => view.dataView.filtered.map((row) => row.id);
  chance.op.value = "between";
  chance.op.listeners.change();
  chance.a.value = "31";
  chance.b.value = "100";
  chance.b.listeners.input();
  assert.deepEqual(ids(), [2, 3, 4]);
  choose("selected");
  assert.deepEqual(ids(), [2, 4]);
  choose("both");
  set.set("type", "1");
  assert.deepEqual(ids(), [2, 3]);
  assert.equal(set.countNode.textContent, "2 / 4");
  assert.deepEqual(new DexMemory("game", SOURCE_LANGUAGE, () => storage).state.modes.moves.filters,
    { search: "", type: "1", flinchChance: { op: "between", a: "31", b: "100" }, target: ["selected", "both"] });
  assert.ok(set.chipsNode.children.some((chip) => chip.textContent === "Flinch chance: 31–100×"));
  set.chipsNode.children.find((chip) => chip.textContent === "Both opponents×").listeners.click();
  assert.deepEqual(set.values().target, ["selected"]);
  assert.deepEqual(ids(), [2]);
  await view.setMode("species");
  await view.setMode("moves");
  assert.deepEqual(ids(), [2]);
  set.chipsNode.children.find((chip) => chip.getAttribute("data-key") === "flinchChance").listeners.click();
  assert.equal(set.values().flinchChance, undefined);
  set.details.querySelectorAll("button").find((button) => button.textContent === "Reset").listeners.click({ preventDefault() {} });
  assert.deepEqual(set.values(), {});
  assert.deepEqual(ids(), [1, 2, 3, 4]);
  assert.equal(set.countNode.textContent, "4 / 4");
  assert.equal(set.chipsNode.children.length, 0);
  assert.deepEqual(new DexMemory("game", SOURCE_LANGUAGE, () => storage).state.modes.moves.filters, { search: "" });
});

test("boolean flinch migration preserves other properties and explicit numeric choices", () => {
  const storage = fakeStorage(), key = `gen3tl-dex-last:migration:${SOURCE_LANGUAGE}`;
  for (const saved of [{ properties: ["flinch", "makes_contact"] }, { "x-mf-effect": "flinch", "x-mf-property": "makes_contact" }]) {
    storage.setItem(key, JSON.stringify({ modes: { moves: { filters: saved } } }));
    const memory = new DexMemory("migration", SOURCE_LANGUAGE, () => storage);
    const expected = { flinchChance: { op: ">=", a: 1 }, properties: ["makes_contact"] };
    assert.deepEqual(memory.state.modes.moves.filters, expected);
    memory.save();
    assert.deepEqual(new DexMemory("migration", SOURCE_LANGUAGE, () => storage).state.modes.moves.filters, expected);
  }
  assert.deepEqual(migrateFilters("moves", { properties: ["flinch"] }), { flinchChance: { op: ">=", a: 1 } });
  assert.deepEqual(migrateFilters("moves", { "x-mf-property": "flinch" }), { flinchChance: { op: ">=", a: 1 } });
  assert.deepEqual(migrateFilters("moves", { properties: ["flinch", "secondary"], flinchChance: { op: "=", a: 0 } }),
    { flinchChance: { op: "=", a: 0 }, properties: ["secondary"] });
  assert.deepEqual(migrateFilters("moves", { properties: ["secondary"] }), { properties: ["secondary"] });
});

test("saved filters migrate through the registry and trainer criteria share one battle", () => {
  const storage = fakeStorage();
  storage.setItem("gen3tl-dex-last:legacy:ru", JSON.stringify({ mode: "moves", modes: {
    species: { filters: { "x-search": "seed", "x-type-filter": "10", "x-type-filter-2": "2", "x-ability-filter": "4" } },
    moves: { filters: { "x-mf-type": "7", "x-mf-category": "1", "x-mf-power": "80", "x-mf-accuracy": "90",
      "x-mf-priority": "-1", "x-mf-effect": "flinch", "x-mf-property": "makes_contact", "x-mf-learner": "Pika" } },
    trainers: { filters: { "x-data-filter": "ev_training", "x-trainer-kind": "world" } },
  } }));
  const memory = new DexMemory("legacy", "ru", () => storage);
  assert.deepEqual(memory.state.modes.species.filters, { search: "seed", type: "10", type2: "2", ability: "4" });
  assert.deepEqual(memory.state.modes.moves.filters, { type: "7", category: "1", power: { op: ">=", a: "80" },
    accuracy: { op: ">=", a: "90" }, priority: { op: ">=", a: "-1" },
    flinchChance: { op: ">=", a: 1 }, properties: ["makes_contact"], learner: "Pika" });
  assert.deepEqual(memory.state.modes.trainers.filters, { kind: "world", evTraining: "yes" });
  const set = new FilterSet("trainers", FILTER_SETS.trainers, {});
  const record = { battles: [{ kind: "world", ev_training: false, difficulty: ["expert"] },
    { kind: "special", ev_training: true, difficulty: ["expert"] }] };
  assert.equal(set.matches(record, memory.state.modes.trainers.filters), false);
  record.battles[0].ev_training = true;
  assert.equal(set.matches(record, memory.state.modes.trainers.filters), true);
  assert.equal(set.matches(record, { kind: "world", difficulty: "vanilla" }), false);
});

test("a learner input reapplies matches after deferred learnsets arrive", async (context) => {
  const { $ } = stubPage(context, "pokedex");
  let renderer, calls = 0, pending = true, resolve;
  const loaded = new Promise((done) => { resolve = done; });
  const move = { id: 1, name: "moves:1", type: 12 };
  const data = { moves: [move], species: [{ slot: 1, name: "species:1" }] };
  const filters = new FilterSet("moves", FILTER_SETS.moves,
    { container: $("x-filters"), onChange: () => renderer.refilter() });
  const contextData = { ...emptyContext, ...data, speciesNames: () => ["Bulbasaur"] };
  filters.render(contextData);
  const view = { generation: 1, host: { data: () => data }, filterSets: { moves: filters },
    filterContext: () => contextData, text: (id) => id, otherNames: () => [],
    list: { setCount() {} }, ensureCatalogs() {}, learnsetsPending: () => pending,
    ensureLearnsets: () => { calls++; return loaded; } };
  renderer = new PokedexDataView(view, $);
  renderer.cur = 1;
  renderer.showCount = () => {};
  renderer.updateCross = () => {};
  renderer.renderList = () => {};
  const learner = filters.controls.get("learner").input;
  learner.value = "Bulba";
  learner.listeners.input();
  assert.equal(calls, 1);
  assert.deepEqual(renderer.filtered, []);
  data.species[0].learnset = [{ move: 1 }];
  pending = false;
  resolve(true);
  await new Promise((done) => setImmediate(done));
  assert.deepEqual(renderer.filtered.map((row) => row.id), [1]);
});

test("number operators apply to moves, species numbers and base stats", () => {
  const moves = new FilterSet("moves", FILTER_SETS.moves, {});
  const move = { type: 7, damage_category: 1, power: 80, accuracy: 100, priority: 1, pp: 10 };
  assert.equal(moves.matches(move, { power: { op: ">=", a: 80 } }), true);
  assert.equal(moves.matches(move, { power: { op: "between", a: 70, b: 90 } }), true);
  assert.equal(moves.matches(move, { priority: { op: "<=", a: 0 } }), false);
  assert.equal(moves.matches(move, { accuracy: { op: "=", a: 100 } }), true);
  assert.equal(moves.matches(move, { pp: { op: ">=", a: 15 } }), false);
  const species = new FilterSet("species", FILTER_SETS.species, {});
  const record = { base_stats: { hp: 45, attack: 49, defense: 49, sp_attack: 65, sp_defense: 65, speed: 45 },
    weight: 100, height: 10 };
  assert.equal(species.matches(record, { weight: { op: ">=", a: 10 } }), true);
  assert.equal(species.matches(record, { height: { op: "<=", a: 0.9 } }), false);
  assert.equal(species.matches(record, { stat: { stat: "speed", op: ">=", a: 45 } }), true);
  assert.equal(species.matches(record, { stat: { stat: "total", op: "between", a: 300, b: 400 } }), true);
});

test("the power kind and accuracy switches split formula, fixed and never-miss moves", () => {
  const moves = new FilterSet("moves", FILTER_SETS.moves, {});
  const fixed = { power: 80, accuracy: 100 };
  const formula = { power: 80, power_kind: "computed", power_formula: "page:power:computed", accuracy: 0 };
  assert.equal(moves.matches(formula, { power: { kind: "formula" } }), true);
  assert.equal(moves.matches(formula, { power: { op: ">=", a: 80, kind: "formula" } }), false);
  assert.equal(moves.matches(fixed, { power: { op: ">=", a: 80, kind: "formula" } }), false);
  assert.equal(moves.matches(fixed, { power: { op: ">=", a: 80, kind: "fixed" } }), true);
  assert.equal(moves.matches(formula, { accuracy: { never: "never" } }), true);
  assert.equal(moves.matches(fixed, { accuracy: { never: "never" } }), false);
});

test("a rendered block removes chips and Reset clears every criterion", (context) => {
  const { $ } = stubPage(context, "pokedex");
  let changes = 0, resets = 0;
  const set = new FilterSet("species", FILTER_SETS.species, {
    container: $("x-filters"), onChange: () => { changes++; }, onReset: () => { resets++; },
  });
  set.render(emptyContext);
  set.set("type", "7");
  set.set("weight", { op: ">=", a: "10" });
  assert.equal(changes, 2);
  assert.deepEqual(set.values(), { type: "7", weight: { op: ">=", a: "10" } });
  set.refresh(set.values(), 5, 100);
  assert.equal(set.chipsNode.children.length, 2);
  set.chipsNode.children[0].listeners.click();
  assert.equal(set.values().type, undefined);
  assert.deepEqual(set.values(), { weight: { op: ">=", a: "10" } });
  set.clear();
  assert.equal(resets, 1);
  assert.deepEqual(set.values(), {});
});

test("adding one registry entry renders and applies its control", (context) => {
  const { $ } = stubPage(context, "pokedex");
  const entries = [...FILTER_SETS.species, {
    key: "slot", label: "Slot", group: "Other", kind: "number", operators: [">="],
    predicate: (record, value) => (record.slot ?? 0) >= Number(value.a),
  }];
  const set = new FilterSet("species", entries, { container: $("x-filters"), onChange() {} });
  set.render(emptyContext);
  assert.ok(set.controls.has("slot"));
  assert.match(set.details.textContent, /Slot/);
  set.set("slot", { op: ">=", a: "5" });
  assert.deepEqual(set.values().slot, { op: ">=", a: "5" });
  const records = [{ slot: 4 }, { slot: 6 }];
  assert.deepEqual(records.filter((record) => set.matches(record, set.values())).map((record) => record.slot), [6]);
});

test("the block open state is remembered per mode", async (context) => {
  const { $ } = stubPage(context, "pokedex");
  const memory = new DexMemory("game", "ru", () => fakeStorage());
  const data = { species: [{ slot: 1, dex: 1, name: "species:1", types: [12], learnset: [] }],
    moves: [], trainers: [], encounters: [], abilities: [], items: [], types: [{ id: 12 }], type_effectiveness: [] };
  const view = new PokedexView({ data: () => data, load: async () => {}, memory: () => memory,
    entry: async (id) => ({ source_text: id, translation: "" }), spriteSrc: (path) => path, setHash: () => {},
  }, $);
  await view.load();
  view.loaded = true;
  view.applyFilters();
  const set = view.filterSets.species;
  set.details.open = false;
  set.details.listeners.toggle();
  assert.equal(memory.state.modes.species.filtersOpen, false);
  await view.setMode("moves");
  await view.setMode("species");
  assert.equal(view.filterSets.species.open, false);
  assert.equal(view.filterSets.species.details.open, false);
});

test("ability filters group species-specific names under one ability identity", () => {
  const filter = FILTER_SETS.species.find((row) => row.key === "ability");
  const names = { "abilities:1": "Ordinary", "abilities:3": "Cloud", "abilities:4": "Cloud" };
  const options = filter.options({ ...emptyContext, text: (id) => names[id] ?? "",
    abilities: [{ id: 1, name: "abilities:1", alternate_names: ["abilities:3", "abilities:4"] }] });
  assert.deepEqual(options, [{ value: "1", label: "Ordinary / Cloud" }]);
  assert.ok(filter.predicate({ ability_details: [{ id: 1, name: "abilities:3" }] }, "1"));
  assert.ok(!filter.predicate({ ability_details: [{ id: 1, name: "abilities:3" }] }, "3"));
});
