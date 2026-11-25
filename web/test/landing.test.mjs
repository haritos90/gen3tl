import assert from "node:assert/strict";

import { readFileSync } from "node:fs";

import { test } from "node:test";

import { gameModel, landingModel, landingRedirect, languageRequestURL, loadGameTexts, measured, percentOf, progressTitle, translatedOf, render, renderLoading } from "../js/landing.js";

import { actionsOf, languageRows, pokedexLanguages, SURFACES } from "../js/pick.js";

import { chosenGame, readRegistry, recallGame, rememberGame } from "../js/registry.js";

import { gamesModel, STAGE_DETAILS } from "../js/site-model.js";

import { gameOptions, STAGE_NOTES } from "../js/start.js";

import { chosenLanguage, languageName, setCatalog, setUiLanguage, SITE_LANG_KEY, SOURCE_LANGUAGE, uiLanguage, useSiteLanguage } from "../js/ui.js";

import { fakeFetch, fakeStorage, stubPage } from "./fixture.mjs";

const BASE = "https://example.org/site/data/registry.json";

const SHA1 = "b4776b82a4c7915d0fadeaa27e013523f99dfd94";

test("Pokédex entry offers English and the available catalogs", () => {
  const game = { id: "sample-1.0", languages: [
    { code: "ru", editable: true, readable: true }, { code: "en", editable: true, readable: true }, { code: "de", editable: false, readable: false },
  ] };
  assert.deepEqual(pokedexLanguages(game), ["en", "ru"]);
  assert.equal(SURFACES.pokedex.url(game, "en"), "pokedex#sample-1.0/en");
  assert.equal(SURFACES.pokedex.url(game, "ru"), "pokedex#sample-1.0/ru");
});

test("Pokédex entry offers the declared source language first", () => {
  const game = { id: "sample-1.0", sourceLanguage: "es", languages: [
    { code: "ru", editable: true, readable: true }, { code: "es", editable: true, readable: true },
  ] };
  assert.deepEqual(pokedexLanguages(game), ["es", "ru"]);
  assert.equal(SURFACES.pokedex.url(game), "pokedex#sample-1.0/es");
  assert.equal(gameModel({ id: "sample-1.0", languages: [] }, null).sourceLanguage, "en");
});

test("language requests honor metadata precedence and translate issue templates", (context) => {
  context.after(() => setCatalog(null));
  const game = { name: "Example & friends", repository: "example/game" };
  const site = { repository: "example/site", new_language_url: "https://site.test/request" };
  assert.equal(languageRequestURL({ ...game, links: { new_language: "https://game.test/request" } }, site),
    "https://game.test/request");
  assert.equal(languageRequestURL({ ...game, links: { new_language: null } }, site), null);
  assert.equal(languageRequestURL(game, site), site.new_language_url);
  assert.equal(languageRequestURL(game, { new_language_url: null }), null);
  assert.equal(languageRequestURL({ name: game.name }, { repository: null }), null);
  setCatalog({ "New translation language for {game}": "Request: {game}",
    "Language:\n\nHow I would like to contribute:\n": "Requested language:\n\nContribution:\n" });
  const url = new URL(languageRequestURL(game));
  assert.equal(url.searchParams.get("title"), "Request: Example & friends");
  assert.equal(url.searchParams.get("body"), "Requested language:\n\nContribution:\n");
  assert.equal(new URL(languageRequestURL({ name: game.name }, { repository: "example/site" })).pathname,
    "/example/site/issues/new");
});

test("landing copy and links follow declarations and clear when the game changes", (context) => {
  const { $ } = stubPage(context, "index");
  context.after(() => setCatalog(null));
  const fixture = JSON.parse(readFileSync(new URL("./registry-fixtures.json", import.meta.url), "utf8"))
    .find((entry) => entry.name === "present");
  const registry = readRegistry({ ...fixture.registry, games: [fixture.registry.games[0], {
    id: "other", name: "Other", stage: "catalog", languages: [], data: "./", source_language: "xx",
  }] }, BASE);
  const sources = new Map([[registry.games[0].data, { releases: [{ profile: "other", language: "yy", version: "1.0.0" }] }]]);
  render(landingModel(registry, sources, "#sample", null), $);
  assert.equal($("landing-intro").textContent, "Declared project introduction");
  assert.equal($("landing-editor").textContent, "Declared editor measurement");
  assert.equal($("how-changes").hidden, false);
  assert.equal($("how-changes-title").textContent, "Release changes");
  assert.equal($("how-changes-text").textContent, "Declared release changes");
  assert.equal($("how-review-text").textContent, "Declared review policy");
  const [declared, absent] = $("games-list").children;
  assert.match(declared.textContent, /Declared byte changes Declared save support Declared link support/);
  const urls = declared.querySelectorAll("a").map((link) => link.getAttribute("href"));
  assert.ok(urls.includes("https://example.org/game"));
  assert.ok(urls.includes("https://example.org/site/content-license"));
  assert.ok(urls.includes("https://example.org/site/request?game=sample"));
  assert.doesNotMatch(absent.textContent, /Compatibility|Declared|Content licence/);
  setCatalog({ "Declared review policy": "Localized review policy", "Declared save support": "Localized save support" });
  render(landingModel(registry, sources, "#sample", null), $);
  assert.equal($("how-review-text").textContent, "Localized review policy");
  assert.match($("games-list").firstChild.textContent, /Localized save support/);
  render(landingModel(registry, sources, "#other", null), $);
  assert.equal($("landing-intro").textContent, "Translation patches for Game Boy Advance ROM hacks. Runs locally in your browser.");
  assert.equal($("landing-editor").textContent, "Translate in the browser. Start a new language or improve one.");
  for (const section of ["changes", "review"]) {
    assert.equal($(`how-${section}`).hidden, true);
    assert.equal($(`how-${section}-title`).textContent, "");
    assert.equal($(`how-${section}-text`).textContent, "");
  }
});

test("a game's own texts translate its declarations; earlier data reads its site catalog", async (context) => {
  const { $ } = stubPage(context, "index");
  context.after(() => {
    setCatalog(null);
    setUiLanguage(SOURCE_LANGUAGE);
  });
  const game = { id: "sample", name: "Sample", stage: "translation", engine: "emerald", languages: ["xx"],
    compatibility: { saves: "Declared save support" },
    landing: { review_title: "Public review", review: "Declared review policy" } };
  const current = readRegistry({ schema: 1, games: [{ ...game, data: "current/",
    interface_labels: { xx: "sample/xx/interface-labels.json" } }] }, BASE);
  const previous = readRegistry({ schema: 1, games: [{ ...game, data: "previous/" }] }, BASE);
  const fetcher = fakeFetch({
    "https://example.org/site/data/current/sample/xx/interface-labels.json": { schema: 1, language: "xx",
      strings: { "Declared review policy": "Own review policy", "Declared save support": "Own save support" } },
    "https://example.org/site/data/previous/site/xx.json": { schema: 1, language: "xx",
      strings: { "Declared review policy": "Earlier review policy", "Public review": "Earlier review" } },
  });
  setCatalog({ "Public review": "Site review", "Declared save support": "Site save support" });
  setUiLanguage("xx");
  await loadGameTexts([...current.games, ...previous.games], "xx", fetcher);
  await loadGameTexts([...current.games, ...previous.games], "en", fetcher);
  await loadGameTexts(current.games, "yy", fetcher);
  assert.deepEqual(fetcher.calls, ["https://example.org/site/data/current/sample/xx/interface-labels.json",
    "https://example.org/site/data/previous/site/xx.json"]);
  render(landingModel(current, new Map(), "#sample", null), $);
  assert.equal($("how-review-text").textContent, "Own review policy");
  assert.equal($("how-review-title").textContent, "Site review");
  assert.match($("games-list").firstChild.textContent, /Own save support/);
  render(landingModel(previous, new Map(), "#sample", null), $);
  assert.equal($("how-review-text").textContent, "Earlier review policy");
  assert.equal($("how-review-title").textContent, "Earlier review");
  assert.match($("games-list").firstChild.textContent, /Site save support/);
  setUiLanguage("yy");
  render(landingModel(current, new Map(), "#sample", null), $);
  assert.equal($("how-review-text").textContent, "Declared review policy");
  setUiLanguage(SOURCE_LANGUAGE);
  setCatalog(null);
  render(landingModel(current, new Map(), "#sample", null), $);
  assert.equal($("how-review-text").textContent, "Declared review policy");
  assert.match($("games-list").firstChild.textContent, /Declared save support/);
});

test("landing placeholders follow registry structure and cannot match the picker", (context) => {
  const { $ } = stubPage(context, "index");
  const registry = readRegistry({ schema: 1, games: [{ id: "sample", name: "Sample", stage: "catalog",
    engine: "engine", languages: ["xx", "yy"], data: "./" }] }, BASE);
  renderLoading(registry, $);
  assert.equal($("games-placeholders").children.length, 1);
  assert.equal($("games-placeholders").firstChild.querySelector(".langs").children.length, 2);
  assert.equal($("doors").children.every((node) => !node.hidden), true);
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const picker = readFileSync(new URL("../pick.html", import.meta.url), "utf8");
  assert.match(html, /<body class="landing-page">/);
  for (const file of ["bricolage-grotesque-latin.woff2", "ibm-plex-sans-latin.woff2", "ibm-plex-sans-cyrillic.woff2"]) {
    assert.match(html, new RegExp(`<link rel="preload" href="fonts/${file}" as="font" type="font/woff2" crossorigin>`));
    assert.match(picker, new RegExp(`<link rel="preload" href="fonts/${file}" as="font" type="font/woff2" crossorigin>`));
  }
  assert.doesNotMatch(html, /data-reserve|class="hero" data|class="door[^>]*hidden/);
  assert.doesNotMatch(picker, /<body class="landing-page">/);
  const css = readFileSync(new URL("../site.css", import.meta.url), "utf8");
  const start = css.indexOf("body.landing-page .dynamic-copy"), end = css.indexOf("body.patcher-page .script-placeholder", start);
  const rules = css.slice(start, end).split("\n").filter((line) => line.includes("{"));
  assert.ok(rules.length > 0);
  for (const line of rules) assert.match(line.trim(), /^(?:html\[data-scripted\] )?body\.landing-page[. ]/, line);
  assert.doesNotMatch(css.slice(start, end), /min-height|data-reserve/);
});

test("landing placeholders show the stage description in the interface language", (context) => {
  context.after(() => setCatalog(null));
  const { $ } = stubPage(context, "index");
  const registry = readRegistry({ schema: 1, games: [{ id: "sample", name: "Sample", stage: "translation",
    languages: ["xx"], data: "./" }] }, BASE);
  setCatalog({ [STAGE_DETAILS.translation.description]: "Stage text, translated" });
  renderLoading(registry, $);
  const texts = $("games-placeholders").firstChild.querySelectorAll(".muted").map((node) => node.textContent);
  assert.ok(texts.includes("Stage text, translated"), JSON.stringify(texts));
  assert.ok(!texts.includes(STAGE_DETAILS.translation.description));
});

test("picker loading rules stay scoped away from the landing page", () => {
  const html = readFileSync(new URL("../pick.html", import.meta.url), "utf8");
  assert.match(html, /<body class="picker-page">/);
  assert.match(html, /<div id="games-placeholders" class="picker"/);
  const css = readFileSync(new URL("../site.css", import.meta.url), "utf8");
  const start = css.indexOf("body.picker-page #games-placeholders"), end = css.indexOf(".picker {", start);
  const rules = css.slice(start, end).split("\n").filter((line) => line.includes("{"));
  assert.ok(rules.length > 0);
  for (const line of rules) assert.match(line.trim(), /^(?:html\[data-scripted\] )?body\.picker-page /, line);
  assert.doesNotMatch(css.slice(start, end), /min-height|data-reserve/);
  const landing = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  assert.doesNotMatch(landing, /<body class="picker-page">/);
});

test("incomplete landing declarations stay hidden and metadata remains plain text", (context) => {
  const { $ } = stubPage(context, "index");
  const registry = readRegistry({ schema: 1, games: [{ id: "sample", name: "Sample", stage: "catalog", languages: [],
    data: "./", landing: { changes_title: "Changes", review: "Review", editor: "<img src=x>" } }] }, BASE);
  render(landingModel(registry, new Map(), "", null), $);
  assert.equal($("how-changes").hidden, true);
  assert.equal($("how-review").hidden, true);
  assert.equal($("landing-editor").textContent, "<img src=x>");
  assert.equal($("landing-editor").querySelectorAll("img").length, 0);
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  assert.doesNotMatch(html, /English game|stay English|English is used/);
});

test("progress counts approved entries among those that need text", () => {
  assert.equal(percentOf({ approved: 90, fuzzy: 2, nothing: 5, untranslated: 3 }), 94);
  assert.equal(percentOf({ approved: 999, untranslated: 1 }), 99);
  assert.equal(percentOf({ nothing: 3 }), 0);
  assert.equal(percentOf(null), 0);
});

test("the translated share counts translated and approved entries", () => {
  assert.equal(translatedOf({ approved: 90, fuzzy: 2, nothing: 5, untranslated: 3 }), 96);
  assert.equal(translatedOf({ fuzzy: 999, untranslated: 1 }), 99);
  assert.equal(translatedOf({ approved: 1, fuzzy: 1, "never shown": 4 }), 100);
  assert.equal(translatedOf({ nothing: 3 }), 0);
  assert.equal(translatedOf(null), 0);
});

test("progress leaves out the entries the game never shows", () => {
  // the fixture's counts: progress reads 3 of 4, 75%
  const progress = { approved: 3, "never shown": 2, nothing: 1, untranslated: 1 };
  assert.equal(measured(progress), 4);
  assert.equal(percentOf(progress), 75);
  assert.equal(percentOf({ approved: 21712, "never shown": 4134, nothing: 7, untranslated: 3 }), 99);
  assert.equal(percentOf({ approved: 21715, "never shown": 4134, nothing: 7 }), 100);
  assert.equal(percentOf({ "never shown": 5 }), 0);
  setUiLanguage("en");
  assert.equal(progressTitle(progress), "3 of 4 entries the game shows are translated, 3 of them approved; "
    + "left out: 2 never shown (reach study and reviews), 1 with nothing to translate");
  assert.equal(progressTitle({ approved: 1, fuzzy: 2, untranslated: 1 }), "3 of 4 entries the game shows are "
    + "translated, 1 of them approved; left out: 0 never shown (reach study and reviews), 0 with nothing to translate");
  assert.equal(progressTitle(null), "0 of 0 entries the game shows are translated, 0 of them approved; "
    + "left out: 0 never shown (reach study and reviews), 0 with nothing to translate");
});

const SHARED = {
  game: { id: "example", name: "Example", stage: "translation", engine: "custom-engine", engine_label: "Example engine",
    languages: ["de", "fr"], data: "./", source_language: "pt-BR" },
  manifest: {
    profiles: [{ id: "example", pokedex: "dex/example.json" }],
    languages: [
      { profile: "example", code: "de", entries: 10, progress: { approved: 4, untranslated: 6 }, index: "de/index.json" },
      { profile: "example", code: "es", entries: 8, texts: "es/texts.json" },
      { profile: "example", code: "it", entries: 4 },
      { profile: "elsewhere", code: "ja", index: "ja/index.json" },
    ],
  },
  releases: [
    { profile: "example", language: "ru", version: "1.1.0" },
    { profile: "example", language: "ru", version: "1.0.0", names: "ru/names.json" },
    { profile: "elsewhere", language: "zh", version: "2.0.0" },
  ],
};

function shared(stage = "translation", manifest = SHARED.manifest, releases = SHARED.releases) {
  const registry = readRegistry({ schema: 1, games: [{ ...SHARED.game, stage }] }, BASE);
  const sources = new Map([[registry.games[0].data, { manifest, releases }]]);
  return { registry, sources, game: gamesModel(registry, sources)[0] };
}

test("one model joins registry, catalog and release languages without unrelated data", () => {
  const { game } = shared();
  const rows = Object.fromEntries(game.languages.map((language) => [language.code, language]));
  assert.deepEqual(Object.keys(rows), ["ru", "de", "es", "fr", "it"]);
  assert.deepEqual(game.languages.map((language) => [language.state, language.editable, language.readable]), [
    ["released", false, false], ["open", true, true], ["readable", false, true],
    ["unavailable", false, false], ["unavailable", false, false],
  ]);
  assert.equal(game.engineLabel, "Example engine");
  assert.deepEqual(actionsOf(game), ["patch", "editor", "save", "pokedex"]);
  assert.deepEqual(pokedexLanguages(game), ["pt-BR", "de", "es"]);
  assert.equal(SURFACES.pokedex.url(game), "pokedex#example/pt-BR");
  assert.equal(SURFACES.patch.allowed(game, rows.de), false);
  assert.equal(SURFACES.save.allowed(game, rows.ru), true);
  assert.equal(SURFACES.save.allowed(game, rows.de), false);
  assert.equal(SURFACES.editor.allowed(game, rows.es), false);
  assert.equal(SURFACES.pokedex.allowed(game, rows.es), true);
  assert.equal(SURFACES.pokedex.allowed(game, rows.fr), false);
  assert.equal(SURFACES.pokedex.allowed(game, rows.ru), false);
  assert.deepEqual(languageRows(game).map((row) => [row.name, row.tone, row.status, row.translated, row.percent]), [
    ["Russian", "ok", "Released", "—", "—"], ["German", "plain", "Open for translation", "40%", "40%"],
    ["Spanish", "plain", "Read only", "0%", "0%"], ["French", "plain", "Not available", "—", "—"],
    ["Italian", "plain", "Not available", "0%", "0%"],
  ]);
  assert.equal(languageRows(game)[1].title, progressTitle(rows.de.progress));
  assert.equal(languageRows(game)[3].title, "");
});

test("surfaces depend on data at every stage and retain index text compatibility", () => {
  for (const stage of ["translation", "catalog", "discovery", "legacy"]) {
    const { game } = shared(stage);
    assert.deepEqual(actionsOf(game), stage === "discovery" ? ["patch", "save", "pokedex"] : ["patch", "editor", "save", "pokedex"]);
    assert.equal(game.languages.find((language) => language.code === "de").state,
      stage === "catalog" ? "drafting" : stage === "discovery" ? "readable" : "open");
    const { tone, label, description } = STAGE_DETAILS[stage];
    assert.deepEqual(gameOptions([game])[0].chip, [tone, label]);
    assert.equal(STAGE_NOTES[stage], description);
  }
  const { game: datasetOnly } = shared("catalog", { profiles: SHARED.manifest.profiles });
  assert.deepEqual(actionsOf(datasetOnly), ["patch", "save"]);
  assert.deepEqual(pokedexLanguages(datasetOnly), []);
  const { game: textOnly } = shared("catalog", {
    profiles: SHARED.manifest.profiles, languages: [SHARED.manifest.languages[1]],
  }, []);
  assert.deepEqual(actionsOf(textOnly), ["pokedex"]);
  const { game: noDataset } = shared("translation", { languages: SHARED.manifest.languages }, []);
  assert.deepEqual(actionsOf(noDataset), ["editor"]);
  const { game: unavailable } = shared("translation", null, []);
  assert.deepEqual(actionsOf(unavailable), []);
  assert.ok(unavailable.languages.every((language) => language.state === "unavailable"));
});

test("landing current game follows the registry default without reordering cards", () => {
  const registry = readRegistry({ schema: 1, default: "second", games: [SHARED.game, { ...SHARED.game, id: "second" }] }, BASE);
  const storage = fakeStorage();
  const model = () => landingModel(registry, new Map(), "", storage);
  assert.equal(model().current.id, "second");
  assert.deepEqual(model().games.map((game) => [game.id, game.selected]), [["example", false], ["second", true]]);
  rememberGame("missing", storage);
  assert.equal(model().current.id, "second");
  rememberGame("example", storage);
  assert.equal(model().current.id, "example");
  assert.equal(landingModel(registry, new Map(), "#second/fr", storage).current.id, "second");
  assert.equal(chosenGame(registry, "", fakeStorage()).game.id, "second");
});

test("landing rows and doors use the same capabilities as the picker", (context) => {
  const { $ } = stubPage(context, "index");
  const { registry, sources, game } = shared();
  render(landingModel(registry, sources, "", null), $);
  for (const [key, surface] of Object.entries(SURFACES)) assert.equal($(`door-${key}`).hidden, !surface.allowed(game));
  const card = $("games-list").firstChild;
  assert.equal(card.getAttribute("aria-current"), "true");
  const languages = card.children.find((node) => node.className === "langs").children;
  const links = (node) => node.querySelectorAll("a").map((link) => link.getAttribute("href"));
  assert.deepEqual(links(languages[0]), ["patch#example/ru", "save#example/ru"]);
  assert.deepEqual(links(languages[1]), ["pokedex#example/de", "editor#example/de"]);
  assert.deepEqual(links(languages[2]), ["pokedex#example/es"]);
  assert.deepEqual(links(languages[3]), []);
  assert.match(languages[3].textContent, /Not available/);
  const missing = shared("translation", null, []);
  render(landingModel(missing.registry, missing.sources, "", null), $);
  for (const key of Object.keys(SURFACES)) assert.equal($(`door-${key}`).hidden, true);
  const draft = shared("catalog", SHARED.manifest, []);
  render(landingModel(draft.registry, draft.sources, "", null), $);
  const summary = $("games-list").firstChild.firstChild;
  const stage = summary.children.find((node) => node.className.startsWith("chip "));
  assert.equal(stage.textContent, STAGE_DETAILS.catalog.label);
  assert.ok(summary.textContent.includes(STAGE_DETAILS.catalog.description));
});

test("catalog cards promote available tools without a release", (context) => {
  const { $ } = stubPage(context, "index");
  const { registry, sources } = shared("catalog", SHARED.manifest, []);
  render(landingModel(registry, sources, "", null), $);
  const card = $("games-list").firstChild;
  const actions = card.firstChild.querySelectorAll("a");
  assert.deepEqual(actions.map((link) => link.getAttribute("href")), ["editor#example", "pokedex#example/pt-BR"]);
  assert.match(actions[0].className, /primary/);
  assert.match(card.textContent, /Drafting/);
  assert.doesNotMatch(card.textContent, /No surface yet/);
  assert.equal($("games-empty").hidden, true);
});

test("readable catalogs expose the source Pokédex without an editor", (context) => {
  const { $ } = stubPage(context, "index");
  const { registry, sources } = shared("catalog", {
    profiles: SHARED.manifest.profiles, languages: [SHARED.manifest.languages[1]],
  }, []);
  render(landingModel(registry, sources, "", null), $);
  const card = $("games-list").firstChild;
  assert.deepEqual(card.querySelectorAll("a").map((link) => link.getAttribute("href")),
    ["pokedex#example/pt-BR", "pokedex#example/es"]);
  assert.match(card.firstChild.querySelector("a").className, /primary/);
});

test("an empty registry clears cards and shows the empty state", (context) => {
  const { $ } = stubPage(context, "index");
  const { registry, sources } = shared();
  render(landingModel(registry, sources, "", null), $);
  render(landingModel({ games: [] }, new Map(), "", null), $);
  assert.equal($("games-list").children.length, 0);
  assert.equal($("games-list").hidden, true);
  assert.equal($("games-empty").hidden, false);
  for (const key of Object.keys(SURFACES)) assert.equal($(`door-${key}`).hidden, true);
});

test("picker selects the default game and keeps language progress without a stage label", async (context) => {
  const { $ } = stubPage(context, "pick");
  const registry = { schema: 1, default: "example", games: [{ ...SHARED.game, id: "other" }, SHARED.game] };
  globalThis.location.search = "";
  globalThis.fetch = fakeFetch({
    "https://site.test/data/registry.json": registry,
    "https://site.test/data/languages.json": SHARED.manifest,
    "https://site.test/data/site/catalogs.json": { schema: 1, catalogs: [] },
    "https://site.test/data/releases/releases.json": { schema: 1, releases: [] },
  });
  await loadPage(context, "pick");
  for (let step = 0; step < 100 && !$("games").children.length; step++) await new Promise((resolve) => setImmediate(resolve));
  assert.equal($("games-error").hidden, true);
  assert.equal($("games").children.length, 2);
  const [other, selected] = $("games").children;
  assert.doesNotMatch(other.className, /is-selected/);
  assert.match(selected.className, /is-selected/);
  assert.match(selected.textContent, /LanguageStatusTranslatedApproved/);
  assert.match(selected.textContent, /GermanOpen for translation40%40%/);
  assert.match(selected.textContent, /FrenchNot available—/);
  assert.doesNotMatch(selected.textContent, /entries/);
  assert.doesNotMatch(selected.textContent, /Translation stage/);
  assert.equal(selected.querySelector("h2").title, STAGE_DETAILS.translation.description);
});

test("central landing requires a valid choice while single-game landing skips selection", () => {
  const registry = readRegistry({ schema: 1, default: "other", games: [SHARED.game, { ...SHARED.game, id: "other" }] }, BASE);
  const storage = fakeStorage();
  assert.equal(landingRedirect(registry, "", storage), "pick?next=home");
  assert.equal(landingRedirect(registry, "#example", storage), null);
  assert.equal(landingRedirect(registry, "#missing", storage), "pick?next=home");
  assert.equal(landingRedirect(registry, "#%broken", storage), "pick?next=home");
  rememberGame("missing", storage);
  assert.equal(landingRedirect(registry, "", storage), "pick?next=home");
  rememberGame("example", storage);
  assert.equal(landingRedirect(registry, "", storage), null);
  assert.equal(landingRedirect(registry, "", { getItem() { throw new Error("Unavailable"); } }), "pick?next=home");
  assert.equal(landingRedirect({ games: [registry.games[0]] }, "", null), null);
  assert.equal(landingRedirect({ games: [] }, "", null), null);
});

async function loadPage(context, page) {
  const source = readFileSync(new URL(`js/${page}.js`, process.env.GEN3TL_WEB_ROOT_URL), "utf8")
    .replace(/from "\.\/(.*?)"/g, (_, path) => `from "${new URL(`js/${path}`, process.env.GEN3TL_WEB_ROOT_URL).href}"`)
    .replace("onSiteLanguage,", "onSiteLanguage as subscribeSiteLanguage,");
  const lifecycle = `
    const subscriptions = [];
    const onSiteLanguage = (listener) => subscriptions.push(subscribeSiteLanguage(listener));
    export const dispose = () => subscriptions.forEach((unsubscribe) => unsubscribe());
  `;
  const module = await import(`data:text/javascript;base64,${Buffer.from(lifecycle + source).toString("base64")}#${encodeURIComponent(context.name)}`);
  context.after(module.dispose);
}

test("a language row keeps its text readable beside its actions", () => {
  const css = readFileSync(new URL("../site.css", import.meta.url), "utf8");
  assert.match(css, /\.lang \{ display: grid; grid-template-columns: minmax\(min\(100%, 13rem\), 1fr\) minmax\(0, auto\);/);
  assert.match(css, /\.lang \.acts \{[^}]*flex-wrap: wrap;[^}]*min-width: 0; \}/);
});

const DEPLOYMENTS = JSON.parse(readFileSync(new URL("./deployment-fixtures.json", import.meta.url), "utf8"));

function deploymentData(spec, base) {
  const games = [DEPLOYMENTS.game, ...(spec.peer ? [DEPLOYMENTS.peer] : [])]
    .map((game) => ({ ...game, languages: spec.languages }));
  const files = {
    [new URL("data/registry.json", base).href]: { schema: 1, default: games.at(-1).id, games },
    [new URL("data/languages.json", base).href]: { schema: 1, languages: [], profiles: [] },
    [new URL("data/site/catalogs.json", base).href]: { schema: 1, catalogs: spec.catalogs.map((code) => ({ code })) },
  };
  for (const code of spec.catalogs) {
    files[new URL(`data/site/${code}.json`, base).href] = { schema: 1, language: code,
      strings: { "Games and languages": `Catalog ${code}`, "gen3tl licence": `Licence ${code}` } };
  }
  for (const [index, game] of games.entries()) {
    const data = new URL(game.data, new URL("data/registry.json", base));
    files[new URL("languages.json", data).href] = {
      schema: 1, toolkit: "1.0.0",
      profiles: [{ id: game.id, sha1: SHA1, pokedex: `${game.id}/pokedex.json` }],
      languages: spec.languages.map((code, rank) => {
        const approved = code === spec.released ? 0 : rank * 5;
        return { code, profile: game.id, index: `${game.id}/${code}/index.json`, entries: 20,
          progress: { approved, untranslated: 20 - approved } };
      }),
    };
    const code = index === 0 ? spec.released : null;
    files[new URL("releases/releases.json", data).href] = { schema: 1, releases: code ? [{
      profile: game.id, profile_name: game.name, language: code, version: "1.2.3",
      source_sha1: SHA1, source_size: 32, result_sha1: "2".repeat(40), result_size: 32,
      patch: `${game.id}/${code}/1.2.3.ups`, patch_sha256: "3".repeat(64), patch_size: 24,
      output: `${game.id}-${code}.gba`, names: `${game.id}/${code}/1.2.3.names.json`, names_sha256: "4".repeat(64),
    }] : [] };
  }
  return { games, fetcher: fakeFetch(files) };
}

function deploymentPage(context, index, spec) {
  const page = stubPage(context, "index");
  document.baseURI = `https://site.test/matrix-${index}/`;
  const navigator = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: { languages: spec.browser } });
  context.after(() => {
    if (navigator) Object.defineProperty(globalThis, "navigator", navigator);
    else delete globalThis.navigator;
    setCatalog(null);
    setUiLanguage(SOURCE_LANGUAGE);
  });
  if (spec.stored) page.storage.setItem(SITE_LANG_KEY, spec.stored);
  return page;
}

for (const [index, spec] of DEPLOYMENTS.cases.entries()) {
  test(`deployment matrix: ${spec.name}`, async (context) => {
    const { $, storage } = deploymentPage(context, index, spec);
    const { games, fetcher } = deploymentData(spec, document.baseURI);
    const errors = [], warnings = [], redirects = [], events = {};
    context.mock.method(console, "error", (...args) => errors.push(args));
    context.mock.method(console, "warn", (...args) => warnings.push(args));
    globalThis.fetch = fetcher;
    globalThis.location.replace = (url) => redirects.push(url);
    globalThis.window.addEventListener = (event, listener) => { events[event] = listener; };
    await loadPage(context, "home");
    if (spec.peer) {
      assert.deepEqual(redirects, ["pick?next=home"]);
      assert.equal($("games-list").children.length, 0);
      assert.equal(recallGame(storage), null);
      assert.ok(!fetcher.calls.some((url) => url.endsWith("releases.json")));
      globalThis.location.hash = `#${games[0].id}`;
      await loadPage({ name: `${context.name} selected`, after: (fn) => context.after(fn) }, "home");
    } else assert.deepEqual(redirects, []);
    assert.deepEqual(errors, []);
    assert.equal(warnings.length, spec.catalogs.length ? 0 : 1);
    assert.equal($("games-error").hidden, true);
    assert.equal($("games-loading").hidden, true);
    assert.equal($("games-empty").hidden, true);
    assert.equal(recallGame(storage), games[0].id);
    assert.equal(chosenLanguage(), spec.chosen);
    assert.equal(uiLanguage(), spec.shown);
    assert.equal(document.documentElement.lang, spec.shown);
    assert.equal($("games").textContent, spec.shown === SOURCE_LANGUAGE ? "Games and languages" : `Catalog ${spec.shown}`);
    const menu = document.querySelectorAll("[data-site-lang]");
    assert.deepEqual(menu.map((button) => button.dataset.siteLang).sort(),
      [...new Set([SOURCE_LANGUAGE, ...spec.languages, ...spec.catalogs])].sort());
    assert.deepEqual(menu.filter((button) => button.getAttribute("aria-pressed") === "true")
      .map((button) => button.dataset.siteLang), [spec.chosen]);
    const footer = document.querySelectorAll("[data-site-footer]")[0];
    assert.equal(footer.querySelector("a").textContent,
      spec.shown === SOURCE_LANGUAGE ? "gen3tl licence" : `Licence ${spec.shown}`);
    const cards = $("games-list").children;
    for (const card of cards) assert.doesNotMatch(card.textContent, /Data unavailable/);
    assert.deepEqual(cards.map((card) => card.querySelector("h3").textContent), games.map((game) => game.name));
    assert.equal(cards[0].getAttribute("aria-current"), "true");
    const rows = cards[0].children.find((node) => node.className === "langs").children;
    assert.deepEqual(rows.map((row) => row.firstChild.firstChild.textContent), spec.order.map(languageName));
    assert.deepEqual(rows.map((row) => row.firstChild.children[1].textContent), spec.states);
    for (const [rank, row] of rows.entries()) {
      const code = spec.order[rank];
      assert.deepEqual(row.querySelectorAll("a").map((link) => link.getAttribute("href")),
        [...(code === spec.released ? ["patch"] : []), "pokedex", "editor", ...(code === spec.released ? ["save"] : [])]
          .map((tool) => `${tool}#${games[0].id}/${code}`));
    }
    const visibleDoors = () => $("doors").children.filter((door) => !door.hidden);
    const expectedDoors = (game, released) => (released ? ["patch", "editor", "save", "pokedex"] : ["editor", "pokedex"])
      .map((tool) => `${tool}#${game.id}${tool === "pokedex" ? `/${game.source_language}` : ""}`);
    assert.deepEqual(visibleDoors().map((door) => door.getAttribute("href")), expectedDoors(games[0], spec.released));
    assert.equal(visibleDoors()[0].className, "door primary");
    for (const door of $("doors").children.filter((door) => door.hidden)) assert.equal(door.getAttribute("href"), null);
    for (const link of document.querySelectorAll("[data-tool]")) {
      assert.equal(link.getAttribute("href"), `${link.dataset.tool}#${games[0].id}/${spec.chosen}`);
    }
    for (const game of games) {
      const releases = new URL("releases/releases.json", new URL(game.data, new URL("data/registry.json", document.baseURI))).href;
      assert.equal(fetcher.calls.filter((url) => url === releases).length, 1);
    }
    if (spec.peer) {
      assert.equal(cards[1].getAttribute("aria-current"), null);
      assert.match(cards[1].textContent, /Drafting/);
      for (const link of cards[1].querySelectorAll("a")) assert.equal(new URL(link.getAttribute("href"), document.baseURI)
        .hash.split("/")[0], `#${games[1].id}`);
      globalThis.location.hash = `#${games[1].id}`;
      events.hashchange();
      assert.equal(recallGame(storage), games[1].id);
      assert.equal($("games-list").children[1].getAttribute("aria-current"), "true");
      assert.deepEqual(visibleDoors().map((door) => door.getAttribute("href")), expectedDoors(games[1], false));
    }
    await useSiteLanguage(SOURCE_LANGUAGE);
    assert.equal($("games").textContent, "Games and languages");
    assert.equal(document.documentElement.lang, SOURCE_LANGUAGE);
    assert.equal($("games-list").children.length, games.length);
  });
}
