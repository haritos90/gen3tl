import assert from "node:assert/strict";

import { test } from "node:test";

import { spawnSync } from "node:child_process";

import { catalogRows, DexMemory, ENGLISH, moveFiltersURL, pageChoice, pageHash, pageLanguage, pageWant, readMoveFilters, searchCatalogs, searchTexts, textEntry, textsCache } from "../js/dexpage.js";

import { Chunks, loadAliases, loadIndex } from "../js/data.js";

import { PokedexView } from "../js/pokedex/pokedex.js";

import { moveMechanics } from "../js/pokedexdataview/pokedexdataview.js";

import { domPage, fakeFetch, fakeStorage, NEEDS_DOM, observeRemovals, storedLanguage, stubPage, TRAINER_DIFFICULTY } from "./fixture.mjs";

import { chosenLanguage, fillPage, setLabelSource, SITE_LANG_KEY, uiLanguage, useSiteLanguage } from "../js/ui.js";

test("the page switches catalogs without removing nodes or moving the open card", { skip: NEEDS_DOM }, async (context) => {
  const win = domPage(context, "pokedex", "#game/en/species/20");
  const base = "https://site.test/data/";
  const species = Array.from({ length: 40 }, (_, i) => ({ slot: i + 1, dex: i + 1,
    name: `species:${i + 1}`, types: [1], learnset: [] }));
  const texts = (language) => ({ schema: 1, profile: "game", language,
    texts: Object.fromEntries([...species.map((record) => [record.name, [`Source ${record.slot}`, `${language} ${record.slot}`]]),
      ["types:1", ["Source type", `${language} type`]]]) });
  const files = {
    [`${base}registry.json`]: { schema: 1, default: "game", games: [
      { id: "game", name: "Test game", stage: "translation", languages: ["ru", "fr"], data: "./" },
    ] },
    [`${base}languages.json`]: { profiles: [{ id: "game", name: "Test game", pokedex: "dex.json" }],
      languages: ["ru", "fr"].map((code) => ({ profile: "game", code, index: `${code}/index.json`, texts: `${code}/texts.json` })) },
    [`${base}site/catalogs.json`]: { schema: 1, catalogs: [{ code: "ru" }, { code: "fr" }] },
    [`${base}site/ru.json`]: { schema: 1, language: "ru", strings: { "Base stats": "Stats RU" } },
    [`${base}site/fr.json`]: { schema: 1, language: "fr", strings: { "Base stats": "Stats FR" } },
    [`${base}dex.json`]: { species, types: [{ id: 1 }], abilities: [], moves: [], items: [] },
    [`${base}ru/texts.json`]: texts("ru"), [`${base}fr/texts.json`]: texts("fr"),
  };
  const fetched = fakeFetch(files);
  let release = null;
  globalThis.fetch = (url) => url === `${base}fr/texts.json`
    ? new Promise((resolve) => { release = () => resolve(fetched(url)); }) : fetched(url);
  await import(`../js/dexpage.js?page-test=${Date.now()}`);
  const $ = (id) => document.getElementById(id);
  for (let i = 0; i < 100 && $("x-name").textContent !== "#020 Source 20"; i++) {
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.equal($("x-name").textContent, "#020 Source 20");
  await new Promise((resolve) => win.requestAnimationFrame(resolve));
  $("x-list").scrollTop = 418;
  $("x-list").dispatchEvent(new win.Event("scroll"));
  await new Promise((resolve) => win.requestAnimationFrame(resolve));
  $("x-detail").scrollTop = 192;
  win.scrollTo(7, 236);
  $("x-search").focus();
  const row = $("x-list-space").firstChild, card = $("x-name").firstChild;
  const offsets = () => [win.scrollX, win.scrollY, $("x-list").scrollTop, $("x-detail").scrollTop];
  const before = offsets();
  const removed = observeRemovals(win);
  const switching = useSiteLanguage("fr");
  for (let i = 0; i < 100 && !release; i++) await new Promise((resolve) => setTimeout(resolve, 1));
  assert.ok(release);
  assert.equal($("x-name").textContent, "#020 Source 20");
  assert.equal($("status").textContent, "");
  assert.equal($("x-list-space").firstChild, row);
  release();
  await switching;
  assert.equal($("x-name").textContent, "#020 fr 20");
  assert.equal($("x-name").firstChild, card);
  assert.equal($("x-list-space").firstChild, row);
  assert.equal(document.activeElement, $("x-search"));
  assert.deepEqual(offsets(), before);
  assert.equal(location.hash, "#game/fr/20");
  assert.equal(removed().length, 0);
  context.diagnostic(`offsets ${JSON.stringify({ before, after: offsets() })}`);
  context.diagnostic("removed 0");
  assert.equal(fetched.calls.filter((url) => url === `${base}dex.json`).length, 1);
  setLabelSource(null);
});

test("saved cards and filters round trip separately for each game and language", () => {
  const storage = fakeStorage();
  const memory = new DexMemory("game", "ru", () => storage);
  memory.state.modes.species.filters = { search: "ivy", type: "12" };
  memory.state.modes.moves.filters = { power: { op: ">=", a: "80" } };
  memory.open("species", 2);
  memory.open("moves", "33");
  memory.open("locations", "3.69");
  memory.pin("species", 2);
  const restored = new DexMemory("game", "ru", () => storage);
  assert.deepEqual(restored.state, memory.state);
  assert.deepEqual(restored.want(null), { kind: "locations", id: "3.69" });
  assert.deepEqual(restored.want(pageWant(["moves"])), { kind: "moves", id: 33 });
  assert.deepEqual(restored.want(pageWant(["6"])), { kind: "species", id: "6" });
  assert.deepEqual(restored.want(pageWant(["moves", "10"])), { kind: "moves", id: "10" });
  for (const [game, lang] of [["other", "ru"], ["game", "en"]]) {
    const separate = new DexMemory(game, lang, () => storage);
    assert.equal(separate.state.modes.species.last, null);
    assert.deepEqual(separate.state.modes.species.favourites, []);
    assert.deepEqual(separate.state.modes.species.filters, {});
  }
  assert.ok(storage.map.has("gen3tl-dex-last:game:ru"));
});

test("move filter addresses round trip, migrate and reject malformed criteria", () => {
  const base = `https://site.test/pokedex?keep=yes#game/${ENGLISH}/moves/1`;
  const filters = { search: "move", flinchChance: { op: "between", a: "11", b: "31" }, target: ["selected", "both"], type: "2" };
  const href = moveFiltersURL(base, filters);
  assert.deepEqual(readMoveFilters(href), filters);
  assert.equal(new URL(href).searchParams.get("keep"), "yes");
  assert.equal(new URL(href).hash, new URL(base).hash);
  assert.equal(moveFiltersURL(href), base);
  assert.equal(readMoveFilters(base), null);
  assert.equal(readMoveFilters(undefined), null);
  const url = new URL(base);
  const read = (value) => { url.searchParams.set("move-filters", value); return readMoveFilters(url.href); };
  assert.equal(read("{"), null);
  assert.deepEqual(read(JSON.stringify({ properties: ["flinch", "makes_contact"] })),
    { flinchChance: { op: ">=", a: 1 }, properties: ["makes_contact"] });
  assert.deepEqual(read(JSON.stringify({ flinchChance: { op: "between", a: "bad", b: 100 }, target: "both", type: {}, extra: true })), {});
  assert.deepEqual(read(JSON.stringify({ flinchChance: { op: "=", a: 0 }, target: ["user"] })),
    { flinchChance: { op: "=", a: 0 }, target: ["user"] });
});

test("move filter addresses restore on startup, Back, Forward and Reset", () => {
  const script = `
    import assert from "node:assert/strict";
    import { stubPage, fakeFetch } from ${JSON.stringify(new URL("./fixture.mjs", import.meta.url).href)};
    import { DexMemory, ENGLISH, moveFiltersURL, readMoveFilters } from ${JSON.stringify(new URL("../js/dexpage.js", import.meta.url).href)};
    const dexpageModule = ${JSON.stringify(new URL("../js/dexpage.js", import.meta.url).href)};
  `;
  const run = async () => {
    const context = { after() {} };
    const { $, storage } = stubPage(context, "pokedex");
    const filters = { flinchChance: { op: ">=", a: "1" }, target: ["selected", "both"] };
    globalThis.location = new URL(moveFiltersURL(`https://site.test/pokedex#game/${ENGLISH}/moves/2`, filters));
    const entries = [{ url: location.href, state: null }];
    let at = 0;
    const listeners = {};
    Object.assign(window, { scrollY: 0, innerWidth: 1400, scrollTo() {}, addEventListener: (key, fn) => { listeners[key] = fn; } });
    globalThis.history = {
      get state() { return entries[at].state; },
      replaceState(state, _title, url) { location.href = url; entries[at] = { state, url }; },
      pushState(state, _title, url) { location.href = url; entries.splice(++at, entries.length, { state, url }); },
      back() { location.href = entries[--at].url; listeners.popstate(); },
      forward() { location.href = entries[++at].url; listeners.popstate(); },
    };
    $("x-history-back").parentElement = document.createElement("div");
    const memory = new DexMemory("game", ENGLISH, () => storage);
    memory.state.modes.moves.filters = { flinchChance: { op: "=", a: 0 } };
    memory.save();
    const moves = [
      { id: 1, flinch_chance: 0, target_key: "user" },
      { id: 2, flinch_chance: 11, target_key: "selected" },
      { id: 3, flinch_chance: 31, target_key: "both" },
      { id: 4, flinch_chance: 100, target_key: "selected" },
    ].map((move) => ({ ...move, name: `moves:${move.id}`, damage_category: 0 }));
    const base = "https://site.test/data/";
    globalThis.fetch = fakeFetch({
      [`${base}registry.json`]: { schema: 1, games: [{ id: "game", name: "Game", stage: "translation", languages: [ENGLISH], data: "./" }] },
      [`${base}languages.json`]: { profiles: [{ id: "game", name: "Game", pokedex: "dex.json" }],
        languages: [{ profile: "game", code: ENGLISH, index: "index.json", texts: "texts.json" }] },
      [`${base}site/catalogs.json`]: { schema: 1, catalogs: [] },
      [`${base}dex.json`]: { species: [], moves },
      [`${base}texts.json`]: { schema: 1, profile: "game", language: ENGLISH,
        texts: Object.fromEntries(moves.map((move) => [move.name, [`Move ${move.id}`, ""]])) },
    });
    const settle = async () => { for (let i = 0; i < 20; i++) await new Promise((resolve) => setImmediate(resolve)); };
    await import(`${dexpageModule}?move-filter-address-test`);
    await settle();
    assert.equal($("banner").textContent, "");
    const { view } = globalThis.pokedexPage;
    const ids = () => view.dataView.filtered.map((move) => move.id);
    const set = view.filterSets.moves;
    assert.deepEqual(ids(), [2, 3, 4]);
    assert.deepEqual(set.values(), filters);
    await view.goTo({ kind: "moves", id: 3 });
    set.set("flinchChance", { op: "<=", a: "31" });
    assert.deepEqual(ids(), [2, 3]);
    const changed = { ...filters, flinchChance: { op: "<=", a: "31" } };
    assert.deepEqual(readMoveFilters(location.href), { search: "", ...changed });
    history.back();
    await settle();
    assert.deepEqual(ids(), [2, 3, 4]);
    assert.deepEqual(set.values(), filters);
    history.forward();
    await settle();
    assert.deepEqual(ids(), [2, 3]);
    assert.deepEqual(set.values(), changed);
    set.clear();
    assert.deepEqual(ids(), [1, 2, 3, 4]);
    assert.deepEqual(readMoveFilters(location.href), { search: "" });
  };
  const result = spawnSync(process.execPath, ["--input-type=module", "-e", `${script}\nawait (${run.toString()})();`],
    { encoding: "utf8", timeout: 10000 });
  assert.equal(result.status, 0, result.stderr || result.error?.message);
});

test("recent species are unique, newest first, limited to fifty and exclude favourites", () => {
  const storage = fakeStorage();
  const memory = new DexMemory("game", "ru", () => storage);
  for (let slot = 1; slot <= 10; slot += 1) memory.open("species", slot);
  assert.deepEqual(memory.state.modes.species.recent, [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]);
  memory.open("species", 5);
  memory.open("moves", 100);
  assert.deepEqual(memory.state.modes.species.recent, [5, 10, 9, 8, 7, 6, 4, 3, 2, 1]);
  memory.pin("species", 5);
  memory.open("species", 5);
  assert.deepEqual(memory.state.modes.species.recent, [10, 9, 8, 7, 6, 4, 3, 2, 1]);
  memory.pin("species", 10);
  memory.pin("species", 5);
  memory.pin("species", 5);
  assert.deepEqual(memory.state.modes.species.favourites, [10, 5]);
  for (let slot = 11; slot <= 20; slot += 1) memory.pin("species", slot);
  assert.equal(memory.state.modes.species.favourites.length, 12);
  assert.equal(memory.state.modes.species.last, 5);
  for (let slot = 21; slot <= 80; slot += 1) memory.open("species", slot);
  assert.equal(memory.state.modes.species.recent.length, 50);
  assert.deepEqual(memory.state.modes.species.recent.slice(0, 2), [80, 79]);
});

test("moves record no recents and stored move recents are dropped", () => {
  const storage = fakeStorage();
  const kinds = ["species", "moves", "items", "trainers", "locations", "types"];
  const id = (kind) => kind === "locations" ? "3.69" : 7;
  storage.setItem("gen3tl-dex-last:game:ru", JSON.stringify({ mode: "moves", modes: Object.fromEntries(kinds.map((kind) =>
    [kind, { last: id(kind), recent: [id(kind)], favourites: kind === "moves" ? [9] : [] }])) }));
  const memory = new DexMemory("game", "ru", () => storage);
  assert.deepEqual(memory.state.modes.moves.recent, []);
  assert.deepEqual(memory.state.modes.moves.favourites, [9]);
  assert.equal(memory.state.modes.moves.last, 7);
  for (const kind of kinds.filter((kind) => kind !== "moves")) assert.deepEqual(memory.state.modes[kind].recent, [id(kind)]);
  memory.open("moves", 12);
  assert.deepEqual(memory.state.modes.moves.recent, []);
  assert.equal(memory.state.modes.moves.last, 12);
  memory.pin("moves", 12);
  assert.deepEqual(memory.state.modes.moves.favourites, [9, 12]);
  memory.open("items", 12);
  assert.deepEqual(memory.state.modes.items.recent, [12, 7]);
  assert.deepEqual(JSON.parse(storage.getItem(memory.key)).modes.moves.recent, []);
});

test("damaged or refused browser storage does not prevent opening cards", () => {
  for (const storage of [() => null, () => { throw new Error("refused"); },
    () => ({ getItem() { throw new Error("refused"); } }), () => fakeStorage(true)]) {
    const memory = new DexMemory("game", "ru", storage);
    memory.open("species", 1);
    assert.equal(memory.available, false);
    assert.deepEqual(memory.want(pageWant(["2"])), { kind: "species", id: "2" });
  }
  const storage = fakeStorage();
  storage.setItem("gen3tl-dex-last:game:ru", "broken json");
  const memory = new DexMemory("game", "ru", () => storage);
  assert.deepEqual(memory.state.modes.species.recent, []);
  memory.open("species", 3);
  assert.equal(new DexMemory("game", "ru", () => storage).state.modes.species.last, 3);
  storage.setItem(memory.key, JSON.stringify({ mode: "unknown", recent: [1, 1, -1, "2", 2],
    favourites: [2, null, 2], last: { species: {}, unknown: 1 }, filters: { "x-search": [], "x-mf-type": "3" } }));
  const cleaned = new DexMemory("game", "ru", () => storage);
  assert.equal(cleaned.state.mode, "species");
  assert.deepEqual(cleaned.state.modes.species, { last: null, filters: {}, sort: { key: "dex", direction: "asc" }, filtersOpen: true, recent: [1], favourites: [2] });
  assert.deepEqual(cleaned.state.modes.moves.filters, { type: "3" });
});

test("a route's tail names a species slot or a kind and an id", () => {
  assert.equal(pageWant([]), null);
  assert.deepEqual(pageWant(["6"]), { kind: "species", id: "6" });
  assert.deepEqual(pageWant(["trainers", "20"]), { kind: "trainers", id: "20" });
  assert.deepEqual(pageWant(["locations", "3.69"]), { kind: "locations", id: "3.69" });
  assert.deepEqual(pageWant(["moves"]), { kind: "moves", id: undefined });
  assert.deepEqual(pageWant(["types"]), { kind: "types", id: undefined });
});

test("the page opens the routed language, else the first catalog", () => {
  const profile = { languages: [{ code: "fr" }, { code: "ru" }, { code: "de" }] };
  assert.equal(pageLanguage(profile, "de").code, "de");
  assert.equal(pageLanguage(profile, "xx").code, "fr");
  assert.equal(pageLanguage(profile, null).code, "fr");
  assert.equal(pageLanguage({ languages: [] }, "ru"), null);
});

test("English labels use the configured source with one or four generated languages", async (context) => {
  stubPage(context, "pokedex");
  context.after(() => setLabelSource(null));
  const source = { code: "ru", texts: "game/ru/pokedex-texts.json" };
  const key = "page:mechanics:flinch:hit";
  const sentence = "The target flinches when this move hits.";
  const docs = { ru: { page: { [key]: [sentence, "Translated sentence"] }, texts: {} },
    de: { page: {}, texts: {} }, es: { page: {}, texts: {} }, fr: { page: {}, texts: {} } };
  for (const codes of [["ru"], ["de", "es", "fr", "ru"], ["de"]]) {
    const profile = { id: "game", source_texts: source,
      languages: codes.map((code) => ({ code, texts: `game/${code}/pokedex-texts.json` })) };
    const requested = [];
    const file = textsCache(async (catalog) => { requested.push(catalog.code); return docs[catalog.code]; });
    const choice = pageChoice(profile, ENGLISH);
    assert.deepEqual(choice, { code: ENGLISH, catalog: source, missing: null });
    const doc = await file(choice.catalog);
    const view = { lang: ENGLISH, host: { data: () => ({}), pageLabels: () => doc.page } };
    assert.deepEqual(PokedexView.prototype.label.call(view, key), { text: sentence, game: false });
    setLabelSource((label) => PokedexView.prototype.label.call(view, label));
    const move = { id: 23, flinch_chance: 31, mechanics: { summary: "Move effect",
      rows: [{ condition: "Flinched", effect: key }] } };
    const rendered = moveMechanics(move)[0].textContent;
    assert.ok(rendered.includes(`Flinched${sentence}`));
    assert.ok(!rendered.includes("page:"));
    assert.deepEqual(requested, [source.code]);
    assert.equal(pageChoice(profile, null, "missing").catalog, source);
    assert.equal(pageChoice(profile, codes[0]).catalog.code, codes[0]);
  }
});

test("without a route the masthead's language opens its catalog, else English says what is missing", () => {
  const profile = { languages: [{ code: "fr" }, { code: "ru" }, { code: "de" }] };
  assert.deepEqual(pageChoice(profile, null, "ru"), { code: "ru", catalog: profile.languages[1], missing: null });
  assert.deepEqual(pageChoice(profile, null, "en"), { code: "en", catalog: profile.languages[0], missing: null });
  assert.deepEqual(pageChoice(profile, null, "es"), { code: "en", catalog: profile.languages[0], missing: "es" });
  assert.deepEqual(pageChoice(profile, null, "fr"), { code: "fr", catalog: profile.languages[0], missing: null });
  assert.deepEqual(pageChoice(profile, "xx", "de"), { code: "de", catalog: profile.languages[2], missing: null });
  assert.equal(pageChoice(profile, null).code, "en");
});

test("keyed index chunks resolve ROM aliases without changing compact texts", async () => {
  const base = "https://site.test/data/game/";
  const ids = { "species:1": "table:species/1", s0444CB2: "table:pokedex_entries/1", s0000999: "hash:12345678" };
  const entries = [
    { id: "table:species/1", source_text: "Bulbasaur", translation: "Бульбазавр" },
    { id: "table:pokedex_entries/1", source_text: "A seed", translation: "Семя" },
  ];
  const url = new URL("ru/index.json", base);
  const fetcher = fakeFetch({
    [`${base}keys.json`]: { schema: 1, profile: "game", rom_sha1: "a".repeat(40), ids },
    ...storedLanguage(url.href, { profile: "game", language: "ru", rom: { sha1: "a".repeat(40) },
      chunks: [{ file: "source.po", path: "entries/0.json", entries: entries.length }] }, entries),
  });
  const index = await loadIndex(url.href, fetcher);
  fetcher.calls.length = 0;
  const aliases = await loadAliases(new URL("../keys.json", url).href, fetcher);
  const rows = catalogRows(index, aliases);
  const chunks = new Chunks(new URL("./", url).href, index, fetcher);
  const compact = { texts: { "species:1": ["Bulbasaur", "Бульбазавр"], s0444CB2: ["A seed", "Семя"] } };
  for (const id of ["species:1", "s0444CB2"]) {
    const entry = await chunks.entry(index.list[rows.get(id)]);
    assert.deepEqual({ source_text: entry.source_text, translation: entry.translation }, textEntry(compact, id));
    assert.equal(rows.get(id), rows.get(ids[id]));
  }
  assert.equal(rows.has("s0000999"), false, "an alias without a catalog row stays unresolved");
  assert.equal(rows.has("s0000888"), false, "an unknown inventory id gets no invented key");
  assert.deepEqual(fetcher.calls, [`${base}keys.json`, `${base}ru/entries/0.json`, "https://site.test/data/game/shared/ru-0.json"]);
  assert.deepEqual(index.list.map((row) => row.id), entries.map((entry) => entry.id));
});

test("legacy inventory indexes work without aliases and retain direct rows", async () => {
  const legacy = { list: [{ id: "species:1", chunk: 0 }] };
  const missing = await loadAliases("https://site.test/missing/keys.json", fakeFetch({}));
  assert.equal(catalogRows(legacy, missing).get("species:1"), 0);
  const mixed = { list: [...legacy.list, { id: "table:species/1", chunk: 1 }] };
  const rows = catalogRows(mixed, new Map([["species:1", "table:species/1"]]));
  assert.equal(rows.get("species:1"), 0);
  assert.equal(rows.get("table:species/1"), 1);
});

test("the search reads every open catalog and fetches each texts file once", async () => {
  const profile = { languages: [{ code: "de", texts: "de.json" }, { code: "fr", texts: "fr.json" },
    { code: "ru", texts: "ru.json" }, { code: "xx", index: "xx/index.json" }] };
  // English mode reads the first open catalog
  assert.deepEqual(pageChoice(profile, ENGLISH), { code: ENGLISH, catalog: profile.languages[0], missing: null });
  assert.deepEqual(searchCatalogs(profile).map((l) => l.code), ["de", "fr", "ru"]);
  assert.deepEqual(searchCatalogs(null), []);
  const docs = { de: { texts: { "species:94": ["Gengar", ""] } },
    ru: { texts: { "species:94": ["Gengar", "Генгар"] }, search_aliases: { "species:94": ["Генгор"] } } };
  const fetched = [];
  const file = textsCache(async (lang) => {
    fetched.push(lang.code);
    return docs[lang.code];
  });
  assert.equal(await file(profile.languages[0]), docs.de); // the page opens the shown catalog first
  const [first, second] = await Promise.all([searchTexts(profile, file), searchTexts(profile, file)]);
  assert.deepEqual(first, [{ code: "de", texts: docs.de.texts, aliases: {} },
    { code: "fr", texts: {}, aliases: {} },
    { code: "ru", texts: docs.ru.texts, aliases: docs.ru.search_aliases }]);
  assert.deepEqual(second, first);
  assert.deepEqual(await searchTexts(profile, file), first);
  assert.deepEqual(fetched, ["de", "fr", "ru"]);
  // a failed file is left out and refetched
  let offline = true;
  const flaky = textsCache(async (lang) => {
    if (offline && lang.code === "ru") throw new Error("offline");
    return docs[lang.code];
  });
  const error = console.error;
  console.error = () => {};
  try {
    assert.deepEqual((await searchTexts(profile, flaky)).map((c) => c.code), ["de", "fr"]);
  } finally {
    console.error = error;
  }
  offline = false;
  assert.deepEqual((await searchTexts(profile, flaky)).map((c) => c.code), ["de", "fr", "ru"]);
});

test("catalog prefetch does not block ready and the first search reuses it", () => {
  const script = `
    import assert from "node:assert/strict";
    import { fakeFetch, fakeStorage } from ${JSON.stringify(new URL("./fixture.mjs", import.meta.url).href)};
    const fake = () => ({ children: [], value: "", textContent: "", dataset: {}, style: {}, hidden: false,
      scrollTop: 0, clientHeight: 400, listeners: {}, classList: { toggle() {} },
      get options() { return this.children; }, addEventListener(name, fn) { this.listeners[name] = fn; },
      setAttribute(key, value) { this[key] = value; }, append(...kids) { this.children.push(...kids.flat()); },
      replaceChildren(...kids) { this.children = kids.flat(); } });
    const elements = new Map();
    const $ = id => { if (!elements.has(id)) elements.set(id, fake()); return elements.get(id); };
    const title = { ...fake(), dataset: { ui: "Pokédex · gen3tl" } };
    const document = { ...fake(), baseURI: "https://fixture/", getElementById: $,
      querySelector: selector => selector === "title" ? title : null, querySelectorAll: () => [],
      createElement: fake, createTextNode: text => ({ text }) };
    const location = new URL("https://fixture/pokedex#game/ru/species/1");
    const history = { state: null, replaceState(state, title, url) {
      this.state = state; location.href = new URL(url, location.href).href;
    } };
    const base = "https://fixture/data/";
    const dataset = { species: [{ slot: 1, dex: 1, name: "species:1", types: [] }] };
    const files = {
      [base + "registry.json"]: { schema: 1, games: [
        { id: "game", name: "Game", stage: "translation", languages: ["ru", "de"], data: "./" }] },
      [base + "languages.json"]: { profiles: [{ id: "game", name: "Game", pokedex: "dex.json" }],
        languages: ["ru", "de"].map(code => ({ profile: "game", code, index: code + "/index.json", texts: code + "/texts.json" })) },
      [base + "site/catalogs.json"]: { schema: 1, catalogs: [{ code: "ru" }] },
      [base + "site/ru.json"]: { schema: 1, language: "ru", strings: {} },
      [base + "dex.json"]: dataset,
      [base + "ru/texts.json"]: { schema: 1, profile: "game", language: "ru", texts: { "species:1": ["First", "Первый"] } },
      [base + "de/texts.json"]: { schema: 1, profile: "game", language: "de", texts: { "species:1": ["First", "Erste"] } },
    };
    const fetcher = fakeFetch(files);
    let release;
    const fetch = (url, ...args) => String(url).endsWith("/de/texts.json")
      ? new Promise(resolve => { release = () => resolve(fetcher(url, ...args)); }) : fetcher(url, ...args);
    Object.assign(globalThis, { document, location, history, fetch, localStorage: fakeStorage(),
      window: { ...fake(), scrollY: 0, innerWidth: 1400, scrollTo() {} }, requestAnimationFrame: fn => fn() });
    $("x-history-back").parentElement = fake();
    const settle = () => new Promise(resolve => setImmediate(resolve));
    await import(${JSON.stringify(new URL("../js/dexpage.js?catalog-prefetch", import.meta.url).href)});
    for (let i = 0; i < 100 && (!release || !globalThis.pokedexPage?.ready); i++) await settle();
    assert.equal(globalThis.pokedexPage.ready, true, "ready must not wait for the nonselected catalog");
    assert.equal($("x-name").textContent, "#001 Первый");
    $("x-search").value = "erste";
    const view = globalThis.pokedexPage.view;
    view.applyFilters();
    assert.deepEqual(view.filtered, []);
    release();
    await view.catalogsLoad;
    assert.deepEqual(view.filtered, [0]);
    assert.equal(fetcher.calls.filter(url => url.endsWith("/de/texts.json")).length, 1);
    assert.equal(fetcher.calls.filter(url => url.endsWith("/ru/texts.json")).length, 1);
  `;
  const result = spawnSync(process.execPath, ["--input-type=module", "-e", script], { encoding: "utf8", timeout: 10000 });
  assert.equal(result.status, 0, result.stderr || result.error?.message);
});

test("language replacement completes during initial and routed loading", () => {
  for (const phase of ["aliases", "compact", "dataset", "chunks", "index-error", "compact-error",
    "loaded-aliases", "loaded-compact", "loaded-dataset", "loaded-chunks", "loaded-index-error", "loaded-compact-error"]) {
    const script = `
    import assert from "node:assert/strict";
    import { fakeFetch, fakeStorage, storedLanguage } from ${JSON.stringify(new URL("./fixture.mjs", import.meta.url).href)};
    import { chosenLanguage, useSiteLanguage } from ${JSON.stringify(new URL("../js/ui.js", import.meta.url).href)};
    const phase = ${JSON.stringify(phase)};
    const fromLoaded = phase.startsWith("loaded-");
    const kind = phase.replace("loaded-", "").replace("-error", "");
    const fake = () => ({ children: [], value: "", textContent: "", dataset: {}, style: {}, hidden: false,
      scrollTop: 0, clientHeight: 400, listeners: {}, classList: { toggle() {} },
      get options() { return this.children; }, addEventListener(name, fn) { this.listeners[name] = fn; },
      setAttribute(key, value) { this[key] = value; }, append(...kids) { this.children.push(...kids.flat()); },
      replaceChildren(...kids) { this.children = kids.flat(); } });
    const elements = new Map();
    const $ = id => { if (!elements.has(id)) elements.set(id, fake()); return elements.get(id); };
    const title = { ...fake(), dataset: { ui: "Pokédex · gen3tl" } };
    const document = { ...fake(), baseURI: "https://fixture/", getElementById: $,
      querySelector: selector => selector === "title" ? title : null, querySelectorAll: () => [], createElement: fake,
      createTextNode: text => ({ text }) };
    const location = new URL("https://fixture/pokedex#game/ru/species/1");
    const history = { state: null, replaceState(state, title, url) {
      this.state = state; location.href = new URL(url, location.href).href;
    } };
    const dataset = { species: [1, 2].map(slot => ({ slot, dex: slot, name: "species:" + slot, types: [12] })) };
    const base = "https://fixture/data/";
    const files = {
      [base + "registry.json"]: { schema: 1, games: [
        { id: "game", name: "Game", stage: "translation", languages: ["ru", "fr"], data: "./" }] },
      [base + "languages.json"]: { profiles: [{ id: "game", name: "Game", pokedex: "dex.json" }],
        languages: ["ru", "fr"].map(code => ({ profile: "game", code, index: "game/" + code + "/index.json",
          ...(kind === "compact" ? { texts: "game/" + code + "/texts.json" } : {}) })) },
      [base + "site/catalogs.json"]: { schema: 1, catalogs: [{ code: "ru" }, { code: "fr" }] },
      [base + "dex.json"]: dataset,
      [base + "game/keys.json"]: { ids: { "species:1": "table:species/1", "species:2": "table:species/2",
        "types:12": "table:types/12" } },
    };
    for (const [code, name] of [["ru", "Бульбазавр"], ["fr", "Bulbizarre"]]) {
      const entries = [{ id: "table:species/1", source_text: "Bulbasaur", translation: name },
        { id: "table:species/2", source_text: "Ivysaur", translation: code === "ru" ? "Ивизавр" : "Herbizarre" },
        { id: "table:types/12", source_text: "Grass", translation: "" }];
      files[base + "site/" + code + ".json"] = { schema: 1, language: code, strings: {} };
      Object.assign(files, storedLanguage(base + "game/" + code + "/index.json", { profile: "game", language: code,
        chunks: [{ file: "source.po", path: "entries/0.json", entries: entries.length }] }, entries));
      files[base + "game/" + code + "/texts.json"] = { schema: 1, profile: "game", language: code,
        texts: { "species:1": ["Bulbasaur", name], "species:2": ["Ivysaur", code === "ru" ? "Ивизавр" : "Herbizarre"],
          "types:12": ["Grass", ""] } };
    }
    const fetcher = fakeFetch(files);
    let held = false, release, gateEnabled = !fromLoaded;
    const fetch = (url, ...args) => {
      const code = fromLoaded ? "fr" : "ru";
      const suffix = { aliases: "/keys.json", compact: "/" + code + "/texts.json", dataset: "/dex.json",
        chunks: "/" + code + "/entries/0.json", index: "/" + code + "/index.json" }[kind];
      if (gateEnabled && String(url).endsWith(suffix) && !held) {
        held = true;
        return new Promise((resolve, reject) => {
          release = () => phase.endsWith("-error") ? reject(new Error("stale initial load failed"))
            : fetcher(url, ...args).then(resolve);
        });
      }
      return fetcher(url, ...args);
    };
    Object.assign(globalThis, { document, location, history, fetch, localStorage: fakeStorage(),
      window: { ...fake(), scrollY: 0, innerWidth: 1400, scrollTo() {} }, requestAnimationFrame: fn => fn() });
    $("x-history-back").parentElement = fake();
    const settle = () => new Promise(resolve => setImmediate(resolve));
    await import(${JSON.stringify(new URL("../js/dexpage.js?initial-alias-race", import.meta.url).href)});
    if (fromLoaded) {
      for (let i = 0; i < 100 && !$("x-name").textContent.includes("Бульбазавр"); i++) await settle();
      assert.equal($("x-name").textContent, "#001 Бульбазавр");
      assert.equal($("x-count").textContent, "2 of 2 species");
      gateEnabled = kind !== "compact";
      location.hash = "#game/fr/species/2";
      window.listeners.hashchange();
    }
    if (!fromLoaded || kind !== "compact") {
      for (let i = 0; i < 100 && !release; i++) await settle();
      assert.ok(release, "initial " + phase + " must be pending");
    }
    const chosen = fromLoaded ? "ru" : "fr";
    const expected = fromLoaded ? "#002 Ивизавр" : "#001 Bulbizarre";
    await useSiteLanguage(chosen);
    for (let i = 0; i < 100 && $("x-name").textContent !== expected; i++) await settle();
    assert.equal($("banner").textContent, "", "replacement must finish before the old request");
    assert.equal($("x-name").textContent, expected, "replacement must finish before the old request");
    release?.();
    for (let i = 0; i < 100; i++) await settle();
    assert.equal(chosenLanguage(), chosen);
    assert.equal($("x-name").textContent, expected);
    assert.equal($("pokedex").lang, chosen);
    assert.equal(location.hash, "#game/" + chosen + "/species/" + (fromLoaded ? 2 : 1));
    assert.equal($("x-count").textContent, "2 of 2 species");
    assert.equal($("status").textContent, "");
    assert.equal($("banner").textContent, "", "stale failures must not replace the active page");
    `;
    const result = spawnSync(process.execPath, ["--input-type=module", "-e", script], { encoding: "utf8", timeout: 10000 });
    assert.equal(result.status, 0, phase + ": " + (result.stderr || result.error?.message));
  }
});

test("address changes during start-up wait for the registry and open the latest address", () => {
  for (const phase of ["registry", "dataset"]) {
    const script = `
    import assert from "node:assert/strict";
    import { fakeFetch, fakeStorage } from ${JSON.stringify(new URL("./fixture.mjs", import.meta.url).href)};
    const fake = () => ({ children: [], value: "", textContent: "", dataset: {}, style: {}, hidden: false,
      scrollTop: 0, clientHeight: 400, listeners: {}, classList: { toggle() {} },
      get options() { return this.children; }, addEventListener(name, fn) { this.listeners[name] = fn; },
      setAttribute(key, value) { this[key] = value; }, append(...kids) { this.children.push(...kids.flat()); },
      replaceChildren(...kids) { this.children = kids.flat(); } });
    const elements = new Map();
    const $ = id => { if (!elements.has(id)) elements.set(id, fake()); return elements.get(id); };
    const title = { ...fake(), dataset: { ui: "Pokédex · gen3tl" } };
    const document = { ...fake(), baseURI: "https://fixture/", getElementById: $,
      querySelector: selector => selector === "title" ? title : null, querySelectorAll: () => [], createElement: fake,
      createTextNode: text => ({ text }) };
    const location = new URL("https://fixture/pokedex#game/en/species/1");
    const history = { state: null, replaceState(state, title, url) {
      this.state = state; location.href = new URL(url, location.href).href;
    } };
    const base = "https://fixture/data/";
    const fetcher = fakeFetch({
      [base + "registry.json"]: { schema: 1, games: [
        { id: "game", name: "Game", stage: "translation", languages: ["ru"], data: "./" }] },
      [base + "languages.json"]: { profiles: [{ id: "game", name: "Game", pokedex: "dex.json" }],
        languages: [{ profile: "game", code: "ru", index: "game/ru/index.json", texts: "game/ru/texts.json" }] },
      [base + "site/catalogs.json"]: { schema: 1, catalogs: [] },
      [base + "dex.json"]: { species: [1, 2, 3].map(slot => ({ slot, dex: slot, name: "species:" + slot, types: [] })) },
      [base + "game/ru/texts.json"]: { schema: 1, profile: "game", language: "ru",
        texts: { "species:1": ["First", ""], "species:2": ["Second", ""], "species:3": ["Third", ""] } },
    });
    const gate = { registry: "/registry.json", dataset: "/dex.json" }[${JSON.stringify(phase)}];
    let release = null;
    // the page's own request, after it listens for addresses
    const fetch = (url, ...args) => String(url).endsWith(gate) && !release && window.listeners.hashchange
      ? new Promise(resolve => { release = () => fetcher(url, ...args).then(resolve); }) : fetcher(url, ...args);
    const errors = [];
    process.on("unhandledRejection", error => errors.push(error));
    Object.assign(globalThis, { document, location, history, fetch, localStorage: fakeStorage(),
      window: { ...fake(), scrollY: 0, innerWidth: 1400, scrollTo() {} }, requestAnimationFrame: fn => fn() });
    $("x-history-back").parentElement = fake();
    const settle = async () => { for (let i = 0; i < 100; i++) await new Promise(resolve => setImmediate(resolve)); };
    let ready = false;
    window.addEventListener = (name, fn) => { if (name === "pokedex-ready") ready = true; window.listeners[name] = fn; };
    window.dispatchEvent = event => { if (event.type === "pokedex-ready") ready = true; };
    await import(${JSON.stringify(new URL(`../js/dexpage.js?startup-route-${phase}`, import.meta.url).href)});
    for (let i = 0; i < 100 && !release; i++) await new Promise(resolve => setImmediate(resolve));
    assert.ok(release, "the start-up request must be pending");
    location.hash = "#game/en/species/2";
    window.listeners.hashchange();
    location.hash = "#game/en/species/3";
    window.listeners.popstate();
    window.listeners.hashchange();
    release();
    await settle();
    assert.deepEqual(errors, []);
    assert.equal($("banner").textContent, "");
    assert.ok(ready);
    assert.equal($("x-name").textContent, "#003 Third");
    assert.equal(location.hash, "#game/en/species/3");
    `;
    const result = spawnSync(process.execPath, ["--input-type=module", "-e", script], { encoding: "utf8", timeout: 10000 });
    assert.equal(result.status, 0, phase + ": " + (result.stderr || result.error?.message));
  }
});

test("page startup replaces the saved route, Back restores its card and scroll, refills keep the title", async (context) => {
  const fake = () => ({
    children: [], value: "", textContent: "", dataset: {}, style: {}, hidden: false,
    scrollTop: 0, clientHeight: 400, listeners: {}, classList: { toggle() {} },
    get options() { return this.children; },
    addEventListener(event, fn) { this.listeners[event] = fn; },
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
    setLabelSource(null);
  });
  const elements = new Map();
  const $ = (id) => {
    if (!elements.has(id)) elements.set(id, fake());
    return elements.get(id);
  };
  // the page's <title>: its key, refilled like any keyed element
  const title = { ...fake(), dataset: { ui: "Pokédex · gen3tl" }, textContent: "Pokédex · gen3tl" };
  const document = { ...fake(), baseURI: "http://fixture/", getElementById: $,
    querySelector: (selector) => (selector === "title" ? title : null),
    querySelectorAll: (selector) => selector === "[data-ui]" ? [title] : selector === "[data-tool]"
      ? ["patch", "editor", "save"].map((tool) => Object.assign($(`link-${tool}`), { dataset: { tool } })) : [],
    get title() { return title.textContent; }, set title(text) { title.textContent = text; },
    createElement: fake, createTextNode: (text) => ({ text }) };
  const window = { ...fake(), scrollY: 0, innerWidth: 1400, scrollTo(_x, y) { this.scrollY = y; } };
  const location = new URL("http://fixture/pokedex#game/en");
  const entries = [{ url: location.href, state: null }];
  let index = 0;
  const history = {
    get state() { return entries[index].state; },
    replaceState(state, _title, url) {
      location.href = new URL(url, location.href).href;
      entries[index] = { state, url: location.href };
    },
    pushState(state, _title, url) {
      location.href = new URL(url, location.href).href;
      entries.splice(++index, entries.length, { state, url: location.href });
    },
    back(events = ["popstate"]) {
      index -= 1;
      location.href = entries[index].url;
      for (const event of events) window.listeners[event]();
    },
    forward(events = ["popstate"]) {
      index += 1;
      location.href = entries[index].url;
      for (const event of events) window.listeners[event]();
    },
  };
  const storage = fakeStorage();
  const memory = new DexMemory("game", "en", () => storage);
  memory.state.modes.species.filters = { type: "12" };
  memory.open("species", 2);
  memory.pin("species", 1);
  const dataset = { species: [1, 2, 3].map((slot) => ({ slot, dex: slot, name: `species:${slot}`, types: [12] })),
    trainer_difficulty: TRAINER_DIFFICULTY,
    trainers: [{ id: 7, name: "trainer_names:7", class: "trainer_classes:1", party: [{ species: 3, level: 5 }] }] };
  const fetcher = fakeFetch({
    "http://fixture/data/registry.json": { schema: 1, games: [{ id: "game", name: "Game", stage: "translation", languages: ["ru", "de", "fr"], data: "./" }] },
    "http://fixture/data/languages.json": { profiles: [{ id: "game", name: "Game", pokedex: "pokedex.json" }],
      languages: [{ profile: "game", code: "ru", index: "index.json", texts: "texts.json" },
        { profile: "game", code: "de", index: "de/index.json", texts: "de/texts.json" }] },
    "http://fixture/data/site/catalogs.json": { schema: 1, catalogs: [{ code: "ru" }] },
    "http://fixture/data/site/ru.json": { schema: 1, language: "ru", strings: {
      "Pokédex · {game} · {language}": "Dex RU · {game} · {language}",
    } },
    "http://fixture/data/texts.json": { schema: 1, profile: "game", language: "ru", texts: {
      "species:1": ["First", "First RU"], "species:2": ["Second", "Second RU"], "species:3": ["Third", "Third RU"], "types:12": ["Grass", ""], "trainer_names:7": ["Trainer", ""], "trainer_classes:1": ["Class", ""],
    } },
    "http://fixture/data/de/texts.json": { schema: 1, profile: "game", language: "de", texts: {
      "species:1": ["First", "First DE"], "species:2": ["Second", "Second DE"], "species:3": ["Third", "Third DE"], "types:12": ["Grass", ""],
    } },
    "http://fixture/data/pokedex.json": dataset,
  });
  Object.assign(globalThis, { document, window, location, history, localStorage: storage,
    requestAnimationFrame: (fn) => fn(), fetch: fetcher });
  $("x-history-back").disabled = true;
  $("x-history-back").parentElement = fake();
  const settle = async () => { for (let i = 0; i < 20; i += 1) await new Promise((resolve) => setImmediate(resolve)); };
  await import("../js/dexpage.js?page-history-test");
  await settle();
  assert.equal($("banner").textContent, "");
  assert.equal(document.title, "Pokédex · Game · English"); // after the view's own refill
  fillPage();
  assert.equal(document.title, "Pokédex · Game · English");
  assert.equal($("x-name").textContent, "#002 Second");
  assert.equal(location.hash, "#game/en/species/2");
  assert.equal(location.pathname, "/share/game/en/species/2/");
  assert.equal(entries.length, 1);
  assert.equal($("x-history-back").disabled, true);
  $("x-list").listeners.scroll();
  assert.equal(location.pathname, "/share/game/en/species/2/");
  assert.equal(entries.length, 1);
  const find = (node, id) => (node.id === id ? node : (node.children ?? []).map((child) => find(child, id)).find(Boolean));
  assert.equal(find($("x-filters"), "x-filter-species-type").value, "12");
  assert.equal($("x-favourites").hidden, false);
  window.scrollY = 125;
  $("x-detail").scrollTop = 450;
  document.listeners.pointerdown();
  $("x-next").listeners.click();
  assert.equal(location.hash, "#game/en/species/3");
  assert.equal(location.pathname, "/share/game/en/species/3/");
  assert.equal(entries.length, 2);
  assert.equal($("x-history-back").disabled, false);
  window.scrollY = 0;
  $("x-detail").scrollTop = 0;
  const goTo = globalThis.pokedexPage.view.goTo.bind(globalThis.pokedexPage.view);
  let routeReplays = 0;
  globalThis.pokedexPage.view.goTo = (...args) => {
    routeReplays += 1;
    return goTo(...args);
  };
  $("x-history-back").listeners.click();
  await settle();
  assert.equal(routeReplays, 1, "popstate-only traversal replays the route");
  assert.equal($("x-name").textContent, "#002 Second");
  assert.equal(location.pathname, "/share/game/en/species/2/");
  assert.equal(entries.length, 2);
  assert.equal(window.scrollY, 125);
  assert.equal($("x-detail").scrollTop, 450);
  assert.equal($("x-history-back").disabled, true);
  const walk = (node) => [node, ...(node.children ?? []).flatMap(walk)];
  $("x-detail").scrollTop = 300;
  $("x-list").scrollTop = 40;
  $("x-mode-trainers").listeners.click();
  await settle();
  assert.equal(location.hash, "#game/en/trainers/7");
  assert.equal(location.pathname, "/pokedex");
  assert.equal(entries.length, 2);
  $("x-detail").scrollTop = 180;
  const speciesLink = walk($("x-data-content")).find((node) => node.title === "species:3" && node.listeners?.click);
  assert.ok(speciesLink);
  speciesLink.listeners.click();
  await settle();
  assert.equal(location.hash, "#game/en/species/3");
  assert.equal(location.pathname, "/share/game/en/species/3/");
  assert.equal(entries.length, 3);
  $("x-history-back").listeners.click();
  await settle();
  assert.equal(location.hash, "#game/en/trainers/7");
  assert.equal(location.pathname, "/pokedex");
  assert.equal($("x-detail").scrollTop, 180);
  routeReplays = 0;
  $("x-history-back").listeners.click();
  await settle();
  assert.equal(routeReplays, 1, "a later traversal to the same URL still replays");
  assert.equal($("x-name").textContent, "#002 Second");
  assert.equal(location.hash, "#game/en/species/2");
  assert.equal(location.pathname, "/share/game/en/species/2/");
  assert.equal($("x-detail").scrollTop, 300);
  assert.equal($("x-list").scrollTop, 40);
  routeReplays = 0;
  history.forward(["popstate", "hashchange"]);
  await settle();
  assert.equal(routeReplays, 1, "paired events share one asynchronous route replay");
  assert.equal(location.hash, "#game/en/trainers/7");
  assert.equal(location.pathname, "/pokedex");
  assert.equal($("x-detail").scrollTop, 180);
  assert.equal($("x-history-back").disabled, false);
  history.back();
  await settle();
  location.hash = "#game/en/1";
  window.listeners.hashchange();
  await settle();
  assert.equal($("x-name").textContent, "#001 First");
  assert.equal(location.pathname, "/share/game/en/species/1/");
  assert.equal(JSON.parse(storage.getItem(memory.key)).modes.species.last, 1);
  const choose = async (code) => {
    const button = { dataset: { siteLang: code }, closest: (selector) => selector === "[data-site-lang]" ? button : null };
    document.listeners.click({ target: button });
    await settle();
  };
  await choose("ru");
  assert.equal($("x-name").textContent, "#001 First RU");
  assert.equal(document.title, "Dex RU · Game · Russian");
  assert.equal(uiLanguage(), "ru");
  assert.equal(location.hash, "#game/ru/species/1");
  assert.equal(location.pathname, "/share/game/ru/species/1/");
  assert.equal(storage.getItem(SITE_LANG_KEY), "ru");
  await choose("de");
  assert.equal($("x-name").textContent, "#001 First DE");
  assert.equal(document.title, "Pokédex · Game · German");
  assert.equal(uiLanguage(), "en");
  assert.equal(chosenLanguage(), "de");
  assert.equal(location.hash, "#game/de/species/1");
  assert.equal(location.pathname, "/share/game/de/species/1/");
  assert.equal(storage.getItem(SITE_LANG_KEY), "de");
  assert.equal($("data-lang").textContent, "Game text: German");
  await choose("fr");
  assert.equal($("x-name").textContent, "#001 First");
  assert.equal(chosenLanguage(), "fr");
  assert.equal(location.hash, "#game/fr/species/1");
  assert.equal(location.pathname, "/pokedex");
  assert.equal($("data-lang").textContent, "Game text: English");
  assert.equal($("data-note").hidden, false);
  assert.match($("data-note").textContent, /No French catalog/);
  assert.equal($("link-editor").href, "editor#game/fr");
  location.hash = "#game/en/species/1";
  window.listeners.hashchange();
  await settle();
  assert.equal(chosenLanguage(), "en");
  assert.equal(location.pathname, "/share/game/en/species/1/");
  assert.equal($("data-note").hidden, true);
  Object.defineProperty(globalThis, "localStorage", { configurable: true, get() { throw new Error("refused"); } });
  location.hash = "#game/ru/species/3";
  window.listeners.hashchange();
  await settle();
  assert.equal($("x-name").textContent, "#003 Third RU");
  assert.equal(location.pathname, "/share/game/ru/species/3/");
  assert.equal(document.title, "Dex RU · Game · Russian");
  location.hash = "#game/ru/species/999";
  window.listeners.hashchange();
  await settle();
  assert.equal($("x-name").textContent, "#001 First RU");
  assert.equal(location.hash, "#game/ru/species/1");
  assert.equal(location.pathname, "/share/game/ru/species/1/");
  let reloaded = null;
  location.reload = () => { reloaded = location.href; };
  location.hash = "#other/en/species/2";
  window.listeners.hashchange();
  assert.equal(location.pathname, "/pokedex");
  assert.equal(reloaded, "http://fixture/pokedex#other/en/species/2");
  for (const id of ["x-recent", "x-favourites", "x-pin"]) assert.equal($(id).hidden, true);
});

test("every mode round trips through its canonical route", () => {
  for (const kind of ["species", "moves", "items", "trainers", "locations", "types"]) {
    const id = kind === "locations" ? "3.69" : "12";
    assert.deepEqual(pageWant([kind, id]), { kind, id });
    assert.equal(pageHash("game", "ru", `${kind}/${id}`), `#game/ru/${kind}/${id}`);
  }
  assert.equal(pageHash("game", "ru", "types"), "#game/ru/types/0");
  assert.equal(pageHash("game", "ru", "6"), "#game/ru/species/6");
});

test("legacy memory migrates into independent mode records", () => {
  const storage = fakeStorage();
  storage.setItem("gen3tl-dex-last:game:ru", JSON.stringify({ mode: "moves", last: { species: 25, moves: 85, types: 0 },
    filters: { "x-search": "thunder", "x-type-filter": "13", "x-mf-power": "90", "unknown": "value" },
    favourites: [25], recent: [25, 26] }));
  const memory = new DexMemory("game", "ru", () => storage);
  assert.deepEqual(memory.want(null), { kind: "moves", id: 85 });
  assert.deepEqual(memory.state.modes.species, { last: 25, filters: { search: "thunder", type: "13" }, sort: { key: "dex", direction: "asc" }, filtersOpen: true,
    favourites: [25], recent: [26] });
  assert.deepEqual(memory.state.modes.moves, { last: 85, filters: { search: "thunder", power: { op: ">=", a: "90" } }, filtersOpen: true,
    favourites: [], recent: [] });
  memory.state.modes.moves.filters.search = "bolt";
  for (const kind of Object.keys(memory.state.modes)) {
    memory.open(kind, kind === "locations" ? "3.69" : 2);
    memory.pin(kind, kind === "locations" ? "3.69" : 2);
  }
  const restored = new DexMemory("game", "ru", () => storage);
  assert.deepEqual(restored.state, memory.state);
  assert.equal(restored.state.modes.species.filters.search, "thunder");
  assert.equal(restored.state.modes.moves.filters.search, "bolt");
});
