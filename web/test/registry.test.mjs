import assert from "node:assert/strict";

import { test } from "node:test";

import { readFileSync } from "node:fs";

import { gameModel } from "../js/landing.js";
import { actionsOf, gamesModel, loadSources, verifyManifest } from "../js/site-model.js";
import { projectProfiles } from "../js/data.js";

import { languageRows } from "../js/pick.js";

import { loadRegistry, pokedexLink, readRegistry } from "../js/registry.js";

import { loadSiteLanguages, setUiLanguage } from "../js/ui.js";

const BASE = "https://example.org/data/registry.json";

const FIXTURES = JSON.parse(readFileSync(new URL("./registry-fixtures.json", import.meta.url), "utf8"));

const game = (id, stage, languages) => ({ id, name: id, stage, engine: "emerald", languages, data: "./" });

function registries(doc, files) {
  const calls = [];
  const fetcher = async (url, options) => {
    calls.push(url);
    assert.equal(options.cache, "no-cache");
    assert.equal(options.credentials, "omit");
    const value = url === BASE ? doc : files[url];
    if (value instanceof Error) throw value;
    return { ok: value !== undefined, status: value === undefined ? 404 : 200, json: async () => value };
  };
  return { fetcher, calls };
}

test("includes resolve relative and absolute data and links at their own registry", async () => {
  const { fetcher } = registries({ schema: 1, games: [game("own", "catalog", [])],
    include: ["../../game/data/registry.json", "https://other.test/catalog.json"] }, {
    "https://example.org/game/data/registry.json": { schema: 1,
      games: [{ ...game("relative", "catalog", ["ru"]), links: { project: "../" } }] },
    "https://other.test/catalog.json": { schema: 1,
      games: [{ ...game("absolute", "catalog", ["de"]), data: "https://data.test/", links: { project: "/project" } }] },
  });
  const registry = await loadRegistry(BASE, fetcher);
  assert.deepEqual(registry.games.map((g) => [g.id, g.data, g.links.project]), [
    ["own", "https://example.org/data/", undefined],
    ["relative", "https://example.org/game/data/", "https://example.org/game/"],
    ["absolute", "https://data.test/", "https://other.test/project"],
  ]);
  assert.deepEqual(registry.skipped, []);
});

test("included site and default are ignored and nested includes are never fetched", async () => {
  const { fetcher, calls } = registries({ schema: 1, games: [], site: { name: "Application" }, include: ["one"] }, {
    "https://example.org/data/one": { schema: 1, games: [game("first", "catalog", []), game("second", "catalog", [])],
      site: { name: "Game" }, default: "second", include: ["nested"] },
  });
  const registry = await loadRegistry(BASE, fetcher);
  assert.deepEqual(registry.site, { name: "Application" });
  assert.equal(registry.default, "first");
  assert.deepEqual(calls, [BASE, "https://example.org/data/one"]);
});

test("the first game id wins across own and included registries", async () => {
  const own = game("own", "catalog", []);
  const first = game("first", "catalog", []);
  const { fetcher } = registries({ schema: 1, games: [own], include: ["one", "two"] }, {
    "https://example.org/data/one": { schema: 1, games: [{ ...own, name: "Duplicate" }, first] },
    "https://example.org/data/two": { schema: 1, games: [{ ...first, name: "Duplicate" }] },
  });
  assert.deepEqual((await loadRegistry(BASE, fetcher)).games.map((g) => g.name), ["own", "first"]);
});

test("failed and invalid included registries are reported while later games work", async () => {
  const addresses = ["missing", "offline", "invalid", "good"];
  const { fetcher } = registries({ schema: 1, games: [], include: addresses }, {
    "https://example.org/data/offline": new Error("offline"),
    "https://example.org/data/invalid": { schema: 2, games: [] },
    "https://example.org/data/good": { schema: 1, games: [game("good", "catalog", [])] },
  });
  const registry = await loadRegistry(BASE, fetcher);
  assert.deepEqual(registry.games.map((g) => g.id), ["good"]);
  assert.deepEqual(registry.skipped, addresses.slice(0, 3).map((p) => new URL(p, BASE).href));
});

test("include rejects nonlists and unsafe addresses", () => {
  for (const include of [null, "one", [null], [""], ["//other.test/"], ["file:///data"], ["javascript:alert(1)"]]) {
    assert.throws(() => readRegistry({ schema: 1, games: [], include }, BASE), /invalid include/);
  }
});

test("masthead languages and onRegistry receive expanded games", async () => {
  const files = {
    [BASE]: { schema: 1, games: [], include: ["remote"] },
    "https://example.org/data/remote": { schema: 1, games: [game("remote", "catalog", ["de", "fr"])] },
    "https://example.org/data/site/catalogs.json": { schema: 1, catalogs: [{ code: "ru" }] },
  };
  let received;
  const languages = await loadSiteLanguages(async (url) => ({ ok: Boolean(files[url]), json: async () => files[url] }),
    "https://example.org/", (registry) => { received = registry; });
  assert.deepEqual(new Set(languages.map((row) => row.code)), new Set(["en", "ru", "de", "fr"]));
  assert.equal(received.games[0].id, "remote");
});

test("unsupported game manifests show unavailability without hiding compatible games", async () => {
  const registry = readRegistry({ schema: 1, games: [game("old", "catalog", ["ru"]),
    { ...game("new", "catalog", ["fr"]), data: "https://other.test/data/" }] }, BASE);
  const sources = await loadSources(registry, async (url) => ({ ok: true, json: async () =>
    url.includes("other.test") ? { schema: "99.0.0" } : url.endsWith("languages.json")
      ? { schema: "9.0.0", profiles: [{ id: "old" }], languages: [{ profile: "old", code: "ru", index: "old/ru/index.json" }] }
      : { schema: 1, releases: [] } }));
  const [old, future] = gamesModel(registry, sources);
  assert.ok(actionsOf(old).includes("editor"));
  assert.deepEqual(actionsOf(future), []);
  assert.equal(future.error, "This game's data format is not supported by this site.");
  assert.throws(() => projectProfiles({ schema: "99.0.0" }), /not supported/);
  for (const schema of [undefined, 1, "8.0.0", "9.0.0"]) assert.doesNotThrow(() => verifyManifest({ schema }));
  for (const schema of ["7.2.0", "8.1.0", "10.0.0"]) {
    assert.throws(() => verifyManifest({ schema }), /This game's data format is not supported by this site\./);
  }
});

test("interface data is loaded without a release request", async () => {
  const asked = [];
  const sources = await loadSources({ games: [{ data: "https://example.org/data/" }] }, async (url) => {
    asked.push(url);
    return { ok: true, json: async () => ({ schema: "9.0.0", profiles: [{ id: "site", support: "interface" }], languages: [] }) };
  });
  assert.deepEqual(asked, ["https://example.org/data/languages.json"]);
  assert.deepEqual(sources.get("https://example.org/data/").releases, []);
});

test("generated metadata resolves links against the registry location", () => {
  const fixture = FIXTURES.find((f) => f.name === "present");
  const registry = readRegistry(fixture.registry, BASE);
  assert.deepEqual(registry.site, {
    name: "Example translations", brand: "https://example.org/brand.svg", home: "https://example.org/",
    repository: "example/toolkit", new_language_url: "https://example.org/languages/new",
    licenses: [{ label_key: "Toolkit license", url: "https://example.org/license" },
      { label_key: "Font licenses", url: "https://example.org/fonts/license" }],
  });
  const [game] = registry.games;
  assert.equal(game.sourceLanguage, "pt-BR");
  assert.equal(game.engineLabel, "Sample engine");
  assert.deepEqual(game.links, { project: "https://example.org/game", new_language: "https://example.org/request?game=sample",
    content_license: "https://example.org/content-license" });
  assert.deepEqual(game.compatibility, fixture.registry.games[0].compatibility);
  assert.deepEqual(game.landing, fixture.registry.games[0].landing);
  assert.equal(fixture.registry.site.brand, "../brand.svg");
  registry.site.licenses[0].label_key = "Changed";
  game.compatibility.saves = "Changed";
  assert.equal(fixture.registry.site.licenses[0].label_key, "Toolkit license");
  assert.equal(fixture.registry.games[0].compatibility.saves, "Declared save support");
});

test("absent metadata retains defaults and null links stay unavailable", () => {
  const absent = readRegistry(FIXTURES.find((f) => f.name === "absent").registry, BASE);
  assert.equal(absent.site, null);
  assert.equal(absent.games[0].sourceLanguage, "en");
  assert.equal(absent.games[0].engineLabel, "sample-engine");
  assert.deepEqual(absent.games[0].links, {});
  assert.deepEqual(absent.games[0].compatibility, {});
  assert.deepEqual(absent.games[0].landing, {});
  const unavailable = readRegistry(FIXTURES.find((f) => f.name === "unavailable").registry, BASE);
  assert.deepEqual(unavailable.site, { repository: null, new_language_url: null, licenses: [] });
  assert.deepEqual(unavailable.games[0].links, { project: null, new_language: null, content_license: null });
  assert.deepEqual(unavailable.games[0].compatibility, {});
});

test("metadata rejects malformed objects and unsupported fields", () => {
  for (const [key, values] of Object.entries({
    site: [null, [], "site", { unknown: "x" }, { name: " " }, { brand: false },
      { home: "javascript:alert(1)" }, { repository: "owner" }, { repository: false },
      { new_language_url: "data:text/html,x" }, { licenses: {} }, { licenses: [null] },
      { licenses: [{ label_key: "License" }] }, { licenses: [{ url: "license" }] },
      { licenses: [{ label_key: " ", url: "license" }] },
      { licenses: [{ label_key: "License", url: "file:///license" }] },
      { licenses: [{ label_key: "License", url: "license", other: true }] }],
    landing: [null, [], "yes", { unknown: "x" }, { editor: true }, { review: " " }],
    engine_label: [null, false, [], " "],
    links: [null, [], "link", { unknown: "x" }, { project: 1 }, { new_language: false }],
    compatibility: [null, [], "yes", { unknown: "x" }, { saves: true }, { link_play: " " }, { changed_bytes: ["text"] }],
  })) {
    for (const value of values) {
      const doc = { schema: 1, games: [game("sample", "catalog", [])] };
      (key === "site" ? doc : doc.games[0])[key] = value;
      assert.throws(() => readRegistry(doc, BASE), /invalid/, JSON.stringify({ key, value }));
    }
  }
});

test("metadata links require usable web URLs or relative references", () => {
  for (const url of ["", " ", "javascript:alert(1)", "data:text/html,x", "file:///tmp/a", "//example.org/a",
    "https:example.org", "https://", "https://[invalid", "https://example.org:99999",
    "https://example.org/a\nb", "../a b", "..\\a"]) {
    for (const key of ["project", "new_language", "content_license"]) {
      const doc = { schema: 1, games: [{ ...game("sample", "catalog", []), links: { [key]: url } }] };
      assert.throws(() => readRegistry(doc, BASE), /invalid links/, JSON.stringify({ key, url }));
    }
  }
});

test("picker language rows keep English names in every site language", (ctx) => {
  ctx.after(() => setUiLanguage("en"));
  for (const locale of ["en", "ru", "de"]) {
    setUiLanguage(locale);
    const model = gameModel(game("game", "translation", ["ru", "de", "es", "fr"]), null);
    assert.deepEqual(languageRows(model).map((row) => [row.name, row.status, row.percent]),
      ["French", "German", "Russian", "Spanish"].map((name) => [name, "Not available", "—"]));
  }
});

test("Pokédex links normalize species and chart routes", () => {
  assert.equal(pokedexLink("game", "en", ["25"]), "pokedex#game/en/species/25");
  assert.equal(pokedexLink("game", "en", ["types"]), "pokedex#game/en/types/0");
  for (const mode of ["species", "moves", "items", "trainers", "locations", "types"]) {
    assert.equal(pokedexLink("game", "en", [mode, "2"]), `pokedex#game/en/${mode}/2`);
  }
});
