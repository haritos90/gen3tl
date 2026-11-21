import assert from "node:assert/strict";

import { readFileSync } from "node:fs";

import { spawnSync } from "node:child_process";

import { test } from "node:test";

import { runInNewContext } from "node:vm";

import { pokedexRouteURL, speciesCardURL, speciesShareURL } from "../js/pokedexshareurl.js";

import { domPage, fakeFetch, NEEDS_DOM } from "./fixture.mjs";

import { setLabelSource } from "../js/ui.js";

test("share addresses use the sibling of local or separately published game data", () => {
  for (const data of ["https://site.test/app/data/", "https://site.test/game/data/", "https://game.test/data/"]) {
    assert.equal(speciesShareURL(data, "game", "ru", 7), new URL("../share/game/ru/species/7/", data).href);
  }
});

test("card addresses become share addresses only on the page origin", () => {
  const base = "https://site.test/app/";
  const hash = "#game/ru/species/7";
  const current = `${base}pokedex?keep=yes${hash}`;
  for (const data of ["https://site.test/app/data/", "https://site.test/game/data/"]) {
    const species = { data, game: "game", language: "ru", slot: 7 };
    assert.equal(pokedexRouteURL(base, current, hash, species), `${speciesShareURL(data, "game", "ru", 7)}?keep=yes${hash}`);
  }
  const data = "https://game.test/data/";
  assert.equal(pokedexRouteURL(base, current, hash, { data, game: "game", language: "ru", slot: 7 }), current);
  assert.equal(speciesShareURL(data, "game", "ru", 7), "https://game.test/share/game/ru/species/7/");
});

test("a game share page forwards across origins with the requested fallback language", () => {
  const source = readFileSync(new URL("../share-target.js", import.meta.url), "utf8");
  let target;
  runInNewContext(source, {
    URL, localStorage: { getItem: () => null },
    document: { baseURI: "https://game.test/share/game/en/species/7/?lang=pt-BR", documentElement: { setAttribute() {} },
      currentScript: { dataset: { destination: "https://site.test/app/pokedex?share=1#game/en/species/7" } } },
    history: { replaceState() { assert.fail("cross-origin history write"); } },
    window: { setTimeout() {}, location: { replace(url) { target = url; } } },
  });
  assert.equal(target, "https://site.test/app/pokedex?share=1#game/pt-BR/species/7");
});

test("languages without pages share the English target with their language", () => {
  const base = "https://site.test/sub/";
  for (const code of ["xx", "pt-BR"]) {
    assert.equal(speciesShareURL(new URL("data/", base).href, "game", code, 1, { en: "all", ru: "all", xx: [] }),
      `${base}share/game/en/species/1/?lang=${code}`);
  }
});

test("languages with pages retain their existing share URLs", () => {
  for (const code of ["en", "ru"]) {
    assert.equal(speciesShareURL("https://site.test/data/", "game", code, 1, { en: "all", ru: "all", xx: [] }),
      speciesShareURL("https://site.test/data/", "game", code, 1));
  }
});

test("partial languages route and forward each species in the selected language", () => {
  const base = "https://site.test/sub/";
  const pages = { en: "all", xx: [2] };
  const source = readFileSync(new URL("../share-target.js", import.meta.url), "utf8");
  for (const [slot, path] of [[1, "en/species/1/?lang=xx"], [2, "xx/species/2/"]]) {
    const url = speciesShareURL(new URL("data/", base).href, "game", "xx", slot, pages);
    assert.equal(url, `${base}share/game/${path}`);
    const language = slot === 1 ? "en" : "xx";
    let forwarded;
    runInNewContext(source, {
      URL, localStorage: { getItem: () => null },
      document: { baseURI: url, documentElement: { setAttribute() {} },
        currentScript: { dataset: { destination: `../../../../../pokedex?share=1#game/${language}/species/${slot}` } } },
      history: { replaceState(_state, _title, target) { forwarded = target; } },
      window: { setTimeout() {}, location: { reload() {} } },
    });
    assert.equal(forwarded, `${base}pokedex?share=1#game/xx/species/${slot}`);
  }
  const hash = "#game/xx/species/2";
  const route = pokedexRouteURL(base, `${base}share/game/en/species/1/?lang=xx&keep=yes`, hash,
    { data: new URL("data/", base).href, game: "game", language: "xx", slot: 2, pages });
  assert.equal(route, `${base}share/game/xx/species/2/?keep=yes${hash}`);
});

test("fallback routes preserve the requested language and other query values", () => {
  const base = "https://site.test/sub/";
  const hash = "#game/xx/species/1";
  const species = { data: new URL("data/", base).href, game: "game", language: "xx", slot: 1, pages: { en: "all", ru: "all", xx: [] } };
  const current = `${base}pokedex?share=1&keep=yes${hash}`;
  const fallback = pokedexRouteURL(base, current, hash, species);
  assert.equal(fallback, `${base}share/game/en/species/1/?lang=xx&share=1&keep=yes${hash}`);
  assert.equal(pokedexRouteURL(base, fallback, hash, species), fallback);
  const russian = pokedexRouteURL(base, fallback, "#game/ru/species/1", { ...species, language: "ru" });
  assert.equal(russian, `${base}share/game/ru/species/1/?share=1&keep=yes#game/ru/species/1`);
});

test("fallback language validation precedes URL construction", () => {
  for (const code of ["../en", "x", "en/ru", "ru?next=bad", "ru#bad"]) {
    assert.throws(() => speciesShareURL("https://site.test/data/", "game", code, 1, { en: "all" }),
      /Invalid species share identity/);
  }
});

test("static handoff forwards a validated query language into the card route", { skip: NEEDS_DOM }, (context) => {
  const win = domPage(context, "pokedex");
  const script = win.document.createElement("script");
  script.dataset.destination = "../../../../../pokedex?share=1#game/en/species/1";
  Object.defineProperty(win.document, "currentScript", { value: script });
  const source = readFileSync(new URL("../share-target.js", import.meta.url), "utf8");
  for (const [query, language] of [["?lang=xx", "xx"], ["?lang=pt-BR", "pt-BR"],
    ["?lang=../ru", "en"], ["?lang=ru%2Fspecies%2F2", "en"], ["?lang=", "en"], ["", "en"]]) {
    win.history.replaceState({ depth: 2 }, "", `https://site.test/sub/share/game/en/species/1/${query}`);
    let reloaded = false;
    runInNewContext(source, { URL, document: win.document, history: win.history, localStorage: win.localStorage,
      window: { setTimeout() {}, location: { reload() { reloaded = true; } } } });
    assert.equal(win.location.href, `https://site.test/sub/pokedex?share=1#game/${language}/species/1`);
    assert.deepEqual(win.history.state, { depth: 2 });
    assert.equal(reloaded, true);
  }
});

test("the share button reads the published profile languages", { skip: NEEDS_DOM }, async (context) => {
  const win = domPage(context, "pokedex");
  const shared = [];
  const saved = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  Object.defineProperty(globalThis, "navigator", { configurable: true,
    value: { share: async (value) => shared.push(value) } });
  const previous = globalThis.pokedexExplorer;
  const state = { game: { id: "game", data: "https://site.test/data/" }, code: "xx", profile: { id: "game", share_pages: { en: "all", ru: "all", xx: [] },
    languages: [{ code: "ru" }, { code: "xx" }] } };
  globalThis.pokedexExplorer = { refined: true, state, view: { loaded: true, mode: "species", cur: 1,
    record: () => ({ slot: 1 }), speciesName: () => "Sample", renderSpecies() {}, setLang() {} } };
  const timers = [];
  const timeout = globalThis.setTimeout;
  globalThis.setTimeout = (callback, delay) => { const timer = timeout(callback, delay); timers.push(timer); return timer; };
  context.after(() => {
    for (const timer of timers) clearTimeout(timer);
    globalThis.setTimeout = timeout;
    globalThis.pokedexExplorer = previous;
    if (saved) Object.defineProperty(globalThis, "navigator", saved);
    else delete globalThis.navigator;
  });
  await import(`../js/pokedexshare.js?profile-languages=${Date.now()}`);
  for (const code of ["xx", "ru", "en"]) {
    state.code = code;
    win.document.getElementById("c-share").click();
    await new Promise((resolve) => setImmediate(resolve));
  }
  assert.deepEqual(shared.map((value) => value.url), ["https://site.test/share/game/en/species/1/?lang=xx",
    "https://site.test/share/game/ru/species/1/", "https://site.test/share/game/en/species/1/"]);
});

test("the Pokedex routes an untranslated catalog through its published English pages", { skip: NEEDS_DOM }, async (context) => {
  const win = domPage(context, "pokedex", "#game/xx/species/1");
  const saved = ["Event", "CustomEvent", "pokedexPage"].map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]);
  Object.assign(globalThis, { Event: win.Event, CustomEvent: win.CustomEvent });
  context.after(() => {
    setLabelSource(null);
    for (const [key, descriptor] of saved) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  });
  const base = "https://site.test/data/";
  globalThis.fetch = fakeFetch({
    [`${base}registry.json`]: { schema: 1, default: "game", games: [
      { id: "game", name: "Sample", stage: "translation", languages: ["xx"], data: "./" },
    ] },
    [`${base}languages.json`]: { profiles: [{ id: "game", name: "Sample", pokedex: "dex.json", share_pages: { en: "all" } }],
      languages: [{ profile: "game", code: "xx", index: "xx/index.json", texts: "xx/texts.json" }] },
    [`${base}site/catalogs.json`]: { schema: 1, catalogs: [] },
    [`${base}dex.json`]: { species: [{ slot: 1, dex: 1, name: "species:1", types: [1], learnset: [] }],
      types: [{ id: 1 }], abilities: [], moves: [], items: [] },
    [`${base}xx/texts.json`]: { schema: 1, profile: "game", language: "xx",
      texts: { "species:1": ["Sample", ""], "types:1": ["Grass", ""] } },
  });
  await import(`../js/dexpage.js?share-languages=${Date.now()}`);
  for (let i = 0; i < 100 && !globalThis.pokedexPage?.ready; i++) {
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.equal(globalThis.pokedexPage?.ready, true);
  assert.equal(win.location.href, "https://site.test/share/game/en/species/1/?lang=xx#game/xx/species/1");
  assert.equal(win.document.getElementById("x-name").textContent, "#001 Sample");
});

test("share identities reject unsafe or unknown forms", () => {
  for (const [game, language, slot] of [["../game", "en", 1], ["game", "../en", 1], ["game", "en", 0], ["game", "en", 1.5]]) {
    assert.throws(() => speciesShareURL("https://site.test/data/", game, language, slot), /Invalid species share identity/);
    assert.throws(() => speciesCardURL("https://site.test/", game, language, slot), /Invalid species share identity/);
  }
});

const STARTUP_TEXT = {
  "Game and language setup": "Настройка игры и языка",
  "Loading interface language…": "Загрузка языка интерфейса…",
  "Stage {current} of {total} · {completed} completed": "Этап {current} из {total} · завершено: {completed}",
  "Pokédex loading progress": "Прогресс загрузки Покедекса",
  "Stage {current} of {total}: {stage}; {completed} of {total} completed":
    "Этап {current} из {total}: {stage}; завершено {completed} из {total}",
};

function openStartup(location, storage = null, catalogs = { ru: STARTUP_TEXT }) {
  const nodes = new Map(["c-startup-message", "c-startup-detail", "c-startup-count", "c-startup-retry", "c-startup-close",
    "c-startup-progress", "c-startup-fill", "app"].map((id) => [id, {
    textContent: "", style: {}, attributes: {}, inert: false,
    setAttribute(name, value) { this.attributes[name] = value; },
  }]));
  const document = {
    head: { prepend() {} }, documentElement: { lang: "en" },
    createElement: (tag) => ({ tagName: tag.toUpperCase() }), getElementById: (id) => nodes.get(id) ?? null,
    addEventListener() {},
  };
  const context = {
    document, location, URL, URLSearchParams, window: { addEventListener() {} },
    MutationObserver: class { observe() {} disconnect() {} },
  };
  Object.defineProperty(context, "localStorage", { get() {
    if (storage === "refused") throw new Error("refused");
    return storage;
  } });
  runInNewContext(readFileSync(new URL("js/language-tags.js", process.env.GEN3TL_WEB_ROOT_URL), "utf8"), context);
  runInNewContext(`globalThis.pokedexStartupCatalog = ${JSON.stringify(catalogs)};`, context);
  runInNewContext(readFileSync(new URL("js/pokedex-startup.js", process.env.GEN3TL_WEB_ROOT_URL), "utf8"), context);
  return { context, nodes };
}

test("startup keeps synchronous site wording with routed, remembered, and refused storage", () => {
  const remembered = (code) => ({ getItem: () => code });
  const cases = [
    ["https://site.test/pokedex#game/ru", remembered("en"), "ru"],
    ["https://site.test/pokedex?lang=ru", remembered("en"), "ru"],
    ["https://site.test/pokedex?lang=ru-RU", remembered("en"), "ru"],
    ["https://site.test/pokedex#game/de", remembered("ru"), "ru"],
    ["https://site.test/pokedex#game/ru", "refused", "ru"],
    ["https://site.test/pokedex#game/de", "refused", "en"],
  ];
  for (const [url, storage, language] of cases) {
    const { context, nodes } = openStartup(new URL(url), storage);
    const russian = language === "ru";
    assert.equal(nodes.get("app").inert, true);
    assert.equal(nodes.get("app").attributes["aria-busy"], "true");
    assert.equal(context.pokedexStartup.snapshot().language, language, url);
    assert.equal(context.document.documentElement.lang, language, url);
    assert.equal(nodes.get("c-startup-message").textContent,
      russian ? "Настройка игры и языка" : "Game and language setup", url);
    assert.equal(nodes.get("c-startup-detail").textContent,
      russian ? "Загрузка языка интерфейса…" : "Loading interface language…", url);
    assert.equal(nodes.get("c-startup-count").textContent,
      russian ? "Этап 1 из 5 · завершено: 0" : "Stage 1 of 5 · 0 completed", url);
    assert.equal(nodes.get("c-startup-progress").attributes["aria-label"],
      russian ? "Прогресс загрузки Покедекса" : "Pokédex loading progress", url);
    assert.equal(nodes.get("c-startup-progress").attributes["aria-valuetext"], russian
      ? "Этап 1 из 5: Настройка игры и языка; завершено 0 из 5"
      : "Stage 1 of 5: Game and language setup; 0 of 5 completed", url);
  }
});

test("startup site copy follows the shown interface rather than an unavailable game catalog", () => {
  const { context, nodes } = openStartup(new URL("https://site.test/pokedex#game/de"),
    { getItem: () => "ru" });
  assert.equal(context.pokedexStartup.snapshot().language, "ru");
  context.pokedexStartup.setLanguage("de");
  assert.equal(context.pokedexStartup.snapshot().language, "en");
  assert.equal(nodes.get("c-startup-message").textContent, "Game and language setup");
  context.pokedexStartup.setLanguage("ru");
  assert.equal(context.pokedexStartup.snapshot().language, "ru");
  assert.equal(nodes.get("c-startup-message").textContent, "Настройка игры и языка");
});

test("startup freezes a same-origin application base under hosting subpaths", () => {
  const children = [];
  const document = {
    head: { prepend: (node) => children.unshift(node) }, documentElement: {},
    createElement: (tag) => ({ tagName: tag.toUpperCase() }), getElementById: () => null, addEventListener() {},
  };
  const location = new URL("https://site.test/subpath/pokedex?share=1#game/ru/species/981");
  const context = {
    document, location, URL, URLSearchParams, window: { addEventListener() {} }, localStorage: { getItem: () => null },
    MutationObserver: class { observe() {} disconnect() {} },
  };
  runInNewContext(readFileSync(new URL("../js/language-tags.js", import.meta.url), "utf8"), context);
  runInNewContext(readFileSync(new URL("../js/pokedex-startup.js", import.meta.url), "utf8"), context);
  assert.equal(children.length, 1);
  assert.equal(children[0].tagName, "BASE");
  assert.equal(children[0].href, "https://site.test/subpath/");
  assert.equal(new URL(children[0].href).origin, location.origin);
  const html = readFileSync(new URL("../pokedex.html", import.meta.url), "utf8");
  assert.match(html, /Content-Security-Policy[^>]*base-uri 'self'/);
  assert.doesNotMatch(html, /Content-Security-Policy[^>]*base-uri 'none'/);
  assert.ok(html.indexOf('src="js/language-tags.js"') < html.indexOf('src="data/site/startup.js"'));
  assert.ok(html.indexOf('src="data/site/startup.js"') < html.indexOf('src="js/pokedex-startup.js"'));
  assert.ok(html.indexOf('src="js/pokedex-startup.js"') < html.indexOf('type="module"'));
});

test("share landing cleanup opens cards but retains a restored list", () => {
  const script = `
    import assert from "node:assert/strict";
    const pageModule = ${JSON.stringify(new URL("../js/pokedexshare.js", import.meta.url).href)};
    for (const [index, state, expected] of [[0, null, 1], [1, { cScreen: "card" }, 1], [2, { cScreen: "list" }, 0]]) {
      const node = () => ({
        children: [], textContent: "", disabled: false,
        addEventListener() {}, append(...children) { this.children.push(...children); },
        replaceChildren(...children) { this.children = children; },
        setAttribute(key, value) { this[key] = value; },
      });
      const pin = { after() {} };
      const document = {
        baseURI: "https://fixture/", getElementById: id => id === "x-pin" ? pin : null,
        createElement: node, createElementNS: () => node(),
      };
      const location = new URL("https://fixture/pokedex?share=1#game/en/species/278");
      const history = { state, replaceState(next, _title, url) {
        this.state = next; location.href = new URL(url, location.href).href;
      } };
      let opened = 0;
      const view = { loaded: true, mode: "species", cur: 278, record: slot => ({ slot }),
        renderSpecies() {}, setLang() {} };
      globalThis.pokedexExplorer = { refined: true, view, state: { profile: { languages: [] } },
        open(want) { opened += 1; assert.deepEqual(want, { kind: "species", id: 278 }); } };
      Object.assign(globalThis, { document, location, history, window: { addEventListener() {} } });
      Object.defineProperty(globalThis, "navigator", { value: {}, configurable: true });
      await import(pageModule + "?share-landing=" + index);
      assert.equal(location.search, "");
      assert.equal(opened, expected, JSON.stringify(state));
    }
  `;
  const result = spawnSync(process.execPath, ["--input-type=module", "-e", script], { encoding: "utf8", timeout: 10000 });
  assert.equal(result.status, 0, result.stderr || result.error?.message);
});

test("static share handoff hides the card until forwarding finishes or falls back", () => {
  const source = readFileSync(new URL("../share-target.js", import.meta.url), "utf8");
  for (const [query, code] of [["", "en"], ["?lang=xx", "xx"], ["?lang=pt-BR", "pt-BR"], ["?lang=../ru", "en"]]) {
    for (const state of [null, { cScreen: "list", depth: 2, scroll: { window: 0, panes: { "x-list": 640 } } }]) {
      const destination = `https://fixture/sub/pokedex?share=1#game/${code}/species/278`;
      const calls = [];
      const timers = [];
      const attributes = {};
      const history = { state, replaceState(next, title, url) {
        assert.equal(next, state);
        assert.equal(title, "");
        assert.equal(url, destination);
        assert.equal(attributes["data-forwarding"], "");
        calls.push("replace");
      } };
      runInNewContext(source, {
        URL, history, localStorage: { getItem: () => null },
        document: {
          baseURI: `https://fixture/sub/share/game/en/species/278/${query}`,
          currentScript: { dataset: { destination: "../../../../../pokedex?share=1#game/en/species/278" } },
          documentElement: {
            setAttribute(name, value) { attributes[name] = value; calls.push(name); },
            removeAttribute(name) { delete attributes[name]; calls.push(`remove:${name}`); },
          },
        },
        window: {
          setTimeout(callback, delay) { timers.push({ callback, delay }); },
          location: { reload() { calls.push("reload"); } },
        },
      });
      assert.deepEqual(calls, ["data-forwarding", "replace", "reload"]);
      assert.equal(timers.length, 1);
      assert.equal(timers[0].delay, 4000);
      timers[0].callback();
      assert.equal("data-forwarding" in attributes, false);
      assert.deepEqual(calls, ["data-forwarding", "replace", "reload", "remove:data-forwarding"]);
    }
  }
});

test("static share handoff without a destination leaves the card alone", () => {
  const source = readFileSync(new URL("../share-target.js", import.meta.url), "utf8");
  for (const currentScript of [null, { dataset: {} }]) {
    runInNewContext(source, {
      document: {
        currentScript,
        documentElement: {
          setAttribute() { assert.fail("forwarding state set"); },
          removeAttribute() { assert.fail("forwarding state removed"); },
        },
      },
      history: { replaceState() { assert.fail("address rewritten"); } },
      window: { setTimeout() { assert.fail("fallback armed"); }, location: { reload() { assert.fail("reloaded"); } } },
    });
  }
});

test("static share handoff applies the stored theme before it reloads", () => {
  const source = readFileSync(new URL("../share-target.js", import.meta.url), "utf8");
  for (const [stored, expected] of [["dark", ["data-theme", "dark"]], ["light", ["data-theme", "light"]], ["other", null], [null, null]]) {
    const applied = [];
    runInNewContext(source, {
      URL, history: { state: null, replaceState() { applied.push("replace"); } },
      localStorage: { getItem: (key) => (key === "gen3tl-theme" ? stored : null) },
      document: {
        baseURI: "https://fixture/share/game/en/species/1/",
        currentScript: { dataset: { destination: "../../../../../pokedex" } },
        documentElement: { setAttribute: (name, value) => { if (name === "data-theme") applied.push([name, value]); } },
      },
      window: { setTimeout() {}, location: { reload() { applied.push("reload"); } } },
    });
    assert.deepEqual(applied, expected ? [expected, "replace", "reload"] : ["replace", "reload"]);
  }
  const calls = [];
  runInNewContext(source, {
    URL, history: { replaceState() { calls.push("replace"); } }, localStorage: { getItem() { throw new Error("blocked"); } },
    document: { baseURI: "https://fixture/", currentScript: { dataset: { destination: "pokedex" } },
      documentElement: { setAttribute(name) { if (name === "data-theme") calls.push("theme"); } } },
    window: { setTimeout() {}, location: { reload() { calls.push("reload"); } } },
  });
  assert.deepEqual(calls, ["replace", "reload"]);
});

test("the share stylesheet hides the card only while forwarding", () => {
  const css = readFileSync(new URL("../share-target.css", import.meta.url), "utf8");
  const hidden = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .filter((match) => /visibility\s*:\s*hidden/.test(match[2]))
    .map((match) => match[1].trim());
  assert.deepEqual(hidden, ["html[data-forwarding] main"]);
});

test("the compatibility page keeps query and card state on its short destination", () => {
  const source = readFileSync(new URL("../pokedex-redirect.js", import.meta.url), "utf8");
  const location = new URL("https://site.test/sub/pokedex-c/?share=1#game/xx/species/7");
  const targets = [];
  runInNewContext(source, {
    URL, document: { baseURI: location.href },
    location: { search: location.search, hash: location.hash, replace: (url) => targets.push(url) },
  });
  assert.deepEqual(targets, ["https://site.test/sub/pokedex?share=1#game/xx/species/7"]);
  const page = readFileSync(new URL("../pokedex-c/index.html", import.meta.url), "utf8");
  assert.match(page, /href="\.\.\/pokedex"/);
});
