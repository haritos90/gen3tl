import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { test } from "node:test";
import { runInNewContext } from "node:vm";

import { stubPage } from "./fixture.mjs";

const theme = readFileSync(new URL("../js/theme.js", import.meta.url), "utf8");
const pages = readdirSync(new URL("..", import.meta.url)).filter((name) => name.endsWith(".html"));
const version = new URL("../js/", import.meta.url).href;
const langKey = "gen3tl-site-lang", cacheKey = "gen3tl-site-catalog";
const language = "ru";
const reply = (doc) => ({ ok: true, json: async () => doc });
const settle = async () => { for (let n = 0; n < 30; n++) await Promise.resolve(); };
let serial = 0;

async function fixture(context, page = "index.html", { stored = language, browser = ["en"], cached = false,
  broken = false, early = false } = {}) {
  context.mock.timers.enable({ apis: ["setTimeout", "Date"], now: 1000 });
  const { storage } = stubPage(context, page.slice(0, -5));
  const root = document.documentElement;
  const labels = document.querySelectorAll("[data-ui]");
  for (const node of labels) node.textContent = node.dataset.ui;
  const strings = Object.fromEntries(labels.map((node, index) => [node.dataset.ui, `Строка ${index}`]));
  const catalog = { schema: 1, language, strings };
  if (stored) storage.setItem(langKey, stored);
  if (cached) storage.setItem(cacheKey, JSON.stringify({ version,
    url: "https://site.test/data/site/ru.json", catalog }));
  const reveals = [];
  const remove = root.removeAttribute;
  root.removeAttribute = (key) => {
    if (key === "data-ui-pending" && root.hasAttribute(key)) reveals.push(labels.map((node) => node.textContent));
    remove(key);
  };
  const earlyCalls = [];
  const sandbox = { document, Event, navigator: { languages: browser }, Date, URL, setTimeout, clearTimeout,
    fetch: (url, options) => {
      earlyCalls.push({ url, options });
      return early ? Promise.resolve(reply(catalog)) : Promise.reject(new Error("Early catalog unavailable"));
    } };
  Object.defineProperty(sandbox, "localStorage", { get() {
    if (broken) throw new Error("Storage refused");
    return storage;
  } });
  runInNewContext(theme, sandbox);
  const ui = await import(`../js/ui.js?startup=${serial++}`);
  const warnings = [];
  context.mock.method(console, "warn", (text) => warnings.push(text));
  const metadata = async (url) => url.endsWith("catalogs.json")
    ? reply({ schema: 1, catalogs: [{ code: language }] }) : reply(null);
  return { ui, storage, root, labels, catalog, strings, reveals, warnings, earlyCalls,
    pending: () => root.hasAttribute("data-ui-pending"),
    original: () => labels.map((node) => node.dataset.ui),
    translated: () => labels.map((node) => strings[node.dataset.ui]),
    start: (fetcher) => ui.startSite({ fetcher, storage: broken ? null : storage, root: document, browser }),
    fetcher: (load) => (url) => url.endsWith(`/site/${language}.json`) ? load() : metadata(url),
  };
}

test("every catalog page runs the bootstrap before parsing its body", () => {
  for (const page of pages) {
    const html = readFileSync(new URL(`../${page}`, import.meta.url), "utf8");
    const head = html.slice(0, html.indexOf("</head>"));
    assert.match(head, /<script src="js\/theme\.js"><\/script>/, page);
    assert.ok(head.indexOf("js/theme.js") < head.indexOf('type="module"'), page);
    if (head.includes("Content-Security-Policy")) assert.match(head, /script-src 'self'(?: 'wasm-unsafe-eval')?;/, page);
    assert.doesNotMatch(head, /unsafe-inline|<style\b|\sstyle=/, page);
  }
  const css = readFileSync(new URL("../site.css", import.meta.url), "utf8");
  assert.match(css, /html\[data-ui-pending\] body\s*\{\s*visibility: hidden;\s*\}/);
});

test("theme switches announce the applied theme", async (context) => {
  const f = await fixture(context, "index.html", { stored: "en" });
  const events = [];
  document.dispatchEvent = (event) => events.push(event.type);
  f.root.setAttribute("data-theme", "light");
  document.listeners.click({ target: { closest: () => ({}) } });
  assert.equal(f.root.getAttribute("data-theme"), "dark");
  assert.deepEqual(events, ["gen3tl-theme-change"]);
  assert.equal(f.storage.getItem("gen3tl-theme"), "dark");
});

for (const page of pages) {
  test(`${page}: saved language and cache reveal translated text before any network response`, async (context) => {
    const f = await fixture(context, page, { cached: true });
    assert.equal(document.siteStartup.language, language);
    assert.equal(f.pending(), true);
    const startup = f.start(() => new Promise(() => {}));
    assert.equal(f.pending(), false);
    assert.deepEqual(f.reveals, [f.translated()]);
    context.mock.timers.tick(1500);
    assert.equal(await startup, language);
    assert.deepEqual(f.labels.map((node) => node.textContent), f.translated());
  });
}

test("the bootstrap request supplies the catalog without a module request", async (context) => {
  const f = await fixture(context, "index.html", { early: true });
  assert.equal(f.earlyCalls.length, 1);
  assert.equal(f.earlyCalls[0].url, "https://site.test/data/site/ru.json");
  assert.deepEqual({ ...f.earlyCalls[0].options }, { credentials: "omit", cache: "no-cache" });
  let moduleRequests = 0;
  const shown = await f.start(f.fetcher(async () => {
    moduleRequests += 1;
    return reply(f.catalog);
  }));
  assert.equal(shown, language);
  assert.equal(moduleRequests, 0);
  assert.deepEqual(f.reveals, [f.translated()]);
});

test("a failed bootstrap request falls back to the module request", async (context) => {
  const f = await fixture(context);
  assert.equal(f.earlyCalls.length, 1);
  let moduleRequests = 0;
  const shown = await f.start(f.fetcher(async () => {
    moduleRequests += 1;
    return reply(f.catalog);
  }));
  assert.equal(shown, language);
  assert.equal(moduleRequests, 1);
  assert.deepEqual(f.reveals, [f.translated()]);
});

test("the source language starts no catalog request", async (context) => {
  const f = await fixture(context, "index.html", { stored: "en", browser: [language], early: true });
  assert.deepEqual(f.earlyCalls, []);
  const urls = [];
  const shown = await f.start(async (url) => {
    urls.push(url);
    return url.endsWith("catalogs.json") ? reply({ schema: 1, catalogs: [{ code: language }] }) : reply(null);
  });
  assert.equal(shown, "en");
  assert.deepEqual(urls.filter((url) => /\/data\/site\/(?!catalogs\.json$)[^/]+\.json$/.test(url)), []);
});

test("a fresh catalog is applied before reveal and stored for refresh", async (context) => {
  const f = await fixture(context);
  const startup = f.start(f.fetcher(async () => reply(f.catalog)));
  assert.equal(f.pending(), true);
  assert.equal(await startup, language);
  assert.deepEqual(f.reveals, [f.translated()]);
  assert.deepEqual(JSON.parse(f.storage.getItem(cacheKey)), { version,
    url: "https://site.test/data/site/ru.json", catalog: f.catalog });
});

test("the cached catalog refreshes without repainting visible text", async (context) => {
  const f = await fixture(context, "index.html", { cached: true });
  let release;
  const startup = f.start(f.fetcher(() => new Promise((resolve) => { release = resolve; })));
  assert.equal(await startup, language);
  const fresh = { ...f.catalog, strings: { ...f.strings, [f.labels[0].dataset.ui]: "Новый текст" } };
  release(reply(fresh));
  await settle();
  assert.deepEqual(JSON.parse(f.storage.getItem(cacheKey)).catalog, fresh);
  assert.deepEqual(f.labels.map((node) => node.textContent), f.translated());
  assert.deepEqual(f.reveals, [f.translated()]);
});

test("a failed background refresh keeps the cached translation", async (context) => {
  const f = await fixture(context, "index.html", { cached: true });
  assert.equal(await f.start(f.fetcher(async () => { throw new Error("Offline"); })), language);
  assert.deepEqual(f.reveals, [f.translated()]);
  assert.deepEqual(JSON.parse(f.storage.getItem(cacheKey)).catalog, f.catalog);
});

test("a guide redirect holds its original prose until navigation", async (context) => {
  const f = await fixture(context, "guide.html", { cached: true });
  const moves = [];
  const startup = f.ui.startGuide({ own: "en", root: document, storage: f.storage, browser: ["en"],
    go: (target) => moves.push(target), fetcher: async (url) => url.endsWith("catalogs.json")
      ? reply({ schema: 1, catalogs: [{ code: language }], guides: [language] }) : reply(f.catalog) });
  assert.equal(f.pending(), true);
  assert.equal(await startup, language);
  assert.deepEqual(moves, [`guide.${language}`]);
  assert.equal(f.pending(), true);
  assert.deepEqual(f.reveals, []);
});

test("a guide in the selected language reveals its cached interface immediately", async (context) => {
  const f = await fixture(context, `guide.${language}.html`, { cached: true });
  const startup = f.ui.startGuide({ own: language, root: document, storage: f.storage, browser: ["en"],
    go: () => assert.fail("Unexpected navigation"), fetcher: () => new Promise(() => {}) });
  assert.equal(f.pending(), false);
  assert.deepEqual(f.reveals, [f.translated()]);
  context.mock.timers.tick(1500);
  assert.equal(await startup, language);
});

test("a slow catalog reveals English at the deadline and applies the catalog when it arrives", async (context) => {
  const f = await fixture(context);
  let release;
  const startup = f.start(f.fetcher(() => new Promise((resolve) => { release = resolve; })));
  await settle();
  context.mock.timers.tick(1499);
  assert.equal(f.pending(), true);
  context.mock.timers.tick(1);
  assert.equal(f.pending(), false);
  assert.equal(await startup, "en");
  assert.deepEqual(f.reveals, [f.original()]);
  assert.deepEqual(f.labels.map((node) => node.textContent), f.original());
  release(reply(f.catalog));
  await settle();
  assert.equal(f.ui.uiLanguage(), language);
  assert.deepEqual(f.labels.map((node) => node.textContent), f.translated());
  assert.deepEqual(JSON.parse(f.storage.getItem(cacheKey)).catalog, f.catalog);
  assert.equal(f.storage.getItem(langKey), language);
});

test("a language chosen before the late catalog ends is not overwritten", async (context) => {
  const f = await fixture(context);
  let release;
  const startup = f.start(f.fetcher(() => new Promise((resolve) => { release = resolve; })));
  await settle();
  context.mock.timers.tick(1500);
  assert.equal(await startup, "en");
  assert.equal(await f.ui.useSiteLanguage("en", { fetcher: f.fetcher(async () => reply(null)), root: document,
    storage: f.storage }), "en");
  release(reply(f.catalog));
  await settle();
  assert.equal(f.ui.uiLanguage(), "en");
  assert.deepEqual(f.labels.map((node) => node.textContent), f.original());
});

test("a failed catalog reveals English immediately after failure", async (context) => {
  const f = await fixture(context);
  const shown = await f.start(f.fetcher(async () => { throw new Error("Offline"); }));
  assert.equal(shown, "en");
  assert.equal(f.pending(), false);
  assert.deepEqual(f.reveals, [f.original()]);
  assert.equal(f.warnings.length, 1);
});

test("slow metadata shares the bootstrap deadline", async (context) => {
  const f = await fixture(context);
  context.mock.timers.tick(900);
  const startup = f.start(() => new Promise(() => {}));
  context.mock.timers.tick(600);
  assert.equal(await startup, "en");
  assert.deepEqual(f.reveals, [f.original()]);
});

test("a missing module reveals the page and a late module applies its catalog", async (context) => {
  const f = await fixture(context, "index.html", { cached: true });
  context.mock.timers.tick(1500);
  assert.equal(f.pending(), false);
  assert.deepEqual(f.reveals, [f.original()]);
  assert.equal(await f.start(f.fetcher(async () => reply(f.catalog))), "en");
  await settle();
  assert.deepEqual(f.labels.map((node) => node.textContent), f.translated());
});

test("English skips pending even while metadata is unavailable", async (context) => {
  const f = await fixture(context, "index.html", { stored: "en", browser: [language], cached: true });
  assert.equal(document.siteStartup.language, "en");
  assert.equal(f.pending(), false);
  const startup = f.start(() => new Promise(() => {}));
  assert.equal(f.pending(), false);
  context.mock.timers.tick(1500);
  assert.equal(await startup, "en");
  assert.deepEqual(f.reveals, []);
});

test("browser detection holds text when storage is unavailable", async (context) => {
  const f = await fixture(context, "index.html", { stored: null, browser: [`${language}-RU`, "en"], broken: true });
  assert.equal(document.siteStartup.language, language);
  assert.equal(f.pending(), true);
  assert.equal(await f.start(f.fetcher(async () => reply(f.catalog))), language);
  assert.deepEqual(f.reveals, [f.translated()]);
});

for (const corrupt of ["version", "json", "schema", "language", "strings", "url"]) {
  test(`an invalid ${corrupt} cache is ignored`, async (context) => {
    const f = await fixture(context, "index.html", { cached: true });
    const cached = JSON.parse(f.storage.getItem(cacheKey));
    if (corrupt === "version") cached.version = "old";
    if (corrupt === "schema") cached.catalog.schema = 2;
    if (corrupt === "language") cached.catalog.language = "de";
    if (corrupt === "strings") cached.catalog.strings = [];
    if (corrupt === "url") cached.url = "https://elsewhere.test/data/site/ru.json";
    f.storage.setItem(cacheKey, corrupt === "json" ? "{" : JSON.stringify(cached));
    const startup = f.start(f.fetcher(async () => reply(f.catalog)));
    assert.equal(f.pending(), true);
    assert.equal(await startup, language);
    assert.deepEqual(f.reveals, [f.translated()]);
  });
}
