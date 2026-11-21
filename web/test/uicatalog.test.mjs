// The site catalog: markup keys, fallback order, language switch.
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { test } from "node:test";

import {
  SITE_LANG_KEY, catalogURL, chosenLanguage, fitTools, openTools, errorText, fillPage, formatNumber, indexCodes, languageName, loadCatalog,
  onSiteLanguage,
  plainText, preferredLanguage, readCatalog, recallSiteLanguage, rememberSiteLanguage, setCatalog, setLabelSource,
  setUiLanguage, showPlaceholder, showText, siteLanguages, siteText, startSite, t, textParts, uiError, uiLanguage, useSiteLanguage,
} from "../js/ui.js";

const page = (name) => readFileSync(new URL(`../${name}`, import.meta.url), "utf8");
/** The English guide and its translations. */
const guidePages = () => readdirSync(new URL("..", import.meta.url)).filter((name) => /^guide(\.[a-z-]+)?\.html$/.test(name)).sort();

// --- a small DOM: elements with dataset, attributes and closest() ---

function node(tag, dataset = {}, parent = null) {
  return {
    tagName: tag.toUpperCase(), dataset, parent, textContent: "", attrs: {},
    setAttribute(k, v) { this.attrs[k] = v; },
    closest(selector) {
      for (let n = this; n; n = n.parent) if (selector === "header.mast" && n.tagName === "HEADER" && n.mast) return n;
      return null;
    },
  };
}

function root(nodes, extra = {}) {
  const selectors = {
    "[data-ui]": (n) => n.dataset.ui !== undefined, "[data-ui-placeholder]": (n) => n.dataset.uiPlaceholder !== undefined,
    "[data-ui-title]": (n) => n.dataset.uiTitle !== undefined, "[data-ui-label]": (n) => n.dataset.uiLabel !== undefined,
    "[data-site-lang]": (n) => n.dataset.siteLang !== undefined, "[data-lang-name]": (n) => n.dataset.langName !== undefined,
    "[data-lang-code]": (n) => n.dataset.langCode !== undefined, "[data-lang-list]": (n) => n.dataset.langList !== undefined,
  };
  const all = () => [...nodes, ...nodes.flatMap((n) => n.items ?? []).flatMap((item) => [item, ...item.kids])]; // the menu, once filled
  return { querySelectorAll: (s) => all().filter(selectors[s] ?? (() => false)), baseURI: "https://site.test/editor",
    addEventListener(type, fn) { this[type] = fn; }, ...extra };
}

/** A menu list holding the items fillMenu makes. */
function menuList() {
  const make = (tag) => Object.assign(node(tag), { kids: [], append(...more) { this.kids.push(...more); } });
  return Object.assign(node("div", { langList: "" }), { ownerDocument: { createElement: make },
    replaceChildren(...items) { this.items = items; } });
}

const memory = (entries = []) => {
  const store = new Map(entries);
  return { store, getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v), removeItem: (k) => store.delete(k) };
};

const RU = { "Patch": "Патч", "Loading {game}…": "Загрузка {game}…", "Site language": "Язык сайта" };
const reply = (doc, ok = true, status = 200) => ({ ok, status, json: async () => doc });

test("a key resolves through the label source, then the site catalog, then itself", () => {
  setCatalog(RU);
  assert.equal(t("Patch"), "Патч");
  assert.equal(t("Loading {game}…", { game: "Unbound" }), "Загрузка Unbound…");
  assert.equal(t("Not in the catalog {n}", { n: 2 }), "Not in the catalog 2");
  setLabelSource((key) => (key === "Patch" ? { text: "ПАТЧ", game: true } : null));
  assert.equal(t("Patch"), "ПАТЧ");
  assert.equal(siteText("Patch"), "Патч");
  assert.equal(t("Site language"), "Язык сайта");
  setLabelSource(null);
  setCatalog({ Patch: "" });
  assert.equal(t("Patch"), "Patch");
  setCatalog(null);
  assert.equal(t("Patch"), "Patch");
});

test("fillPage fills text and attributes; the masthead and marked elements skip the page's label source", () => {
  const header = Object.assign(node("header"), { mast: true });
  const nav = node("a", { ui: "Patch" }, header);
  const body = node("button", { ui: "Patch", uiTitle: "Site language" });
  const marked = node("button", { ui: "Patch", uiLabel: "Patch", uiSite: "" });
  const dom = root([nav, body, marked]);
  setCatalog(RU);
  setLabelSource((key) => (key === "Patch" ? { text: "ПАТЧ", game: true } : null));
  fillPage(dom);
  assert.equal(nav.textContent, "Патч");
  assert.equal(body.textContent, "ПАТЧ");
  assert.equal(marked.textContent, "Патч");
  assert.equal(marked.attrs["aria-label"], "Патч");
  assert.equal(body.attrs.title, "Язык сайта");
  setLabelSource(null);
  setCatalog(null);
  fillPage(dom);
  assert.equal(nav.textContent, "Patch");
  assert.equal(body.attrs.title, "Site language");
});

test("showText keeps a key and its values across fills; plainText drops the key", () => {
  const status = node("p", { ui: "Loading…" });
  const name = node("span", { ui: "Game" });
  const dom = root([status, name]);
  showText(status, "Loading {game}…", { game: "Unbound" });
  plainText(name, "Pokémon Unbound");
  setCatalog(RU);
  fillPage(dom);
  assert.equal(status.textContent, "Загрузка Unbound…");
  assert.equal(name.textContent, "Pokémon Unbound");
  assert.equal(name.dataset.ui, undefined);
  setCatalog(null);
});

test("a keyed element's marked children take the places of the key's holes", () => {
  const link = Object.assign(node("a", { ui: "convert the save", uiHole: "link" }), { tag: "a" });
  const item = Object.assign(node("li", { ui: "Its names stay English: {link} to see them." }), { children: [link] });
  item.replaceChildren = (...parts) => { item.parts = parts; };
  setCatalog({ "Its names stay English: {link} to see them.": "Имена остаются английскими: {link}, чтобы их увидеть.",
    "convert the save": "конвертируйте сохранение" });
  fillPage(root([item, link]));
  assert.deepEqual(item.parts, ["Имена остаются английскими: ", link, ", чтобы их увидеть."]);
  assert.equal(link.textContent, "конвертируйте сохранение");
  setCatalog(null);
  assert.deepEqual(textParts("Open {a} or {b}; {c} stays", { a: 1, b: 2 }), ["Open ", 1, " or ", 2, "; {c} stays"]);
});

test("errors carry their key and values", () => {
  const error = uiError("Wrong ROM SHA-1: {sha1}.", { sha1: "ab" });
  assert.equal(error.message, "Wrong ROM SHA-1: ab.");
  setCatalog({ "Wrong ROM SHA-1: {sha1}.": "Неверный SHA-1 ROM: {sha1}.", "Invalid release list.": "Неверный список выпусков." });
  assert.equal(errorText(error), "Неверный SHA-1 ROM: ab.");
  assert.equal(errorText(new Error("Invalid release list.")), "Неверный список выпусков.");
  assert.equal(errorText("Failed to fetch"), "Failed to fetch");
  setCatalog(null);
});

test("the opening language: the remembered choice, else the browser's, among the offered ones", () => {
  assert.equal(preferredLanguage(["en", "ru"], "ru", ["en-US"]), "ru");
  assert.equal(preferredLanguage(["en", "ru"], null, ["ru-RU", "en"]), "ru");
  assert.equal(preferredLanguage(["en", "pt-BR", "pt"], null, ["pt-BR"]), "pt-BR");
  assert.equal(preferredLanguage(["en", "pt-BR", "pt"], null, ["pt-PT"]), "pt");
  assert.equal(preferredLanguage(["en", "pt-BR", "pt"], "pt-PT", []), "pt");
  assert.equal(preferredLanguage(["en", "ru"], "fr", ["de-DE"]), "en");
  assert.equal(preferredLanguage(["en", "ru"], null, undefined), "en");
  const store = new Map();
  const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) };
  assert.equal(recallSiteLanguage(storage), null);
  rememberSiteLanguage("ru", storage);
  assert.equal(store.get(SITE_LANG_KEY), "ru");
  assert.equal(recallSiteLanguage(storage), "ru");
  const broken = { getItem: () => { throw new Error("denied"); }, setItem: () => { throw new Error("denied"); } };
  assert.equal(recallSiteLanguage(broken), null);
  rememberSiteLanguage("ru", broken);
});

test("one key holds the choice: the Pokédex's former choice is read once and folded in", () => {
  const former = memory([["gen3tl-dex-lang", "de"]]);
  assert.equal(recallSiteLanguage(former), "de");
  rememberSiteLanguage("de", former);
  assert.deepEqual([...former.store], [[SITE_LANG_KEY, "de"]]);
  assert.equal(recallSiteLanguage(memory([["gen3tl-dex-lang", "translation"]])), null);
  assert.equal(recallSiteLanguage(memory([[SITE_LANG_KEY, "ru"], ["gen3tl-dex-lang", "en"]])), "ru");
});

test("the site serves English, the registry's game languages and its interface catalogs, by English name", () => {
  const registry = { games: [{ languages: ["ru", "de"] }, { languages: ["ru", "es", "../x"] }, { languages: null }] };
  const manifest = { languages: [{ code: "ru", profile: "site" }, { code: "fr", profile: "unbound" }, { code: "uk", profile: "site" }] };
  assert.deepEqual(siteLanguages(registry, manifest), [
    { code: "en", name: "English", interface: true, guide: true }, { code: "de", name: "German", interface: false, guide: null },
    { code: "ru", name: "Russian", interface: true, guide: null }, { code: "es", name: "Spanish", interface: false, guide: null },
    { code: "uk", name: "Ukrainian", interface: true, guide: null },
  ], "no index: whether a guide exists is unknown");
  assert.deepEqual(siteLanguages(null, manifest).map((l) => l.code), ["en", "fr", "ru", "uk"]); // older data: no registry
  assert.deepEqual(siteLanguages(registry, null).map((l) => [l.code, l.interface]),
    [["en", true], ["de", null], ["ru", null], ["es", null]]);
});

test("the catalogs index names the interface languages, before the manifest's site rows", () => {
  const registry = { games: [{ id: "game", name: "Game", stage: "catalog", data: "./", languages: ["ru", "de"] }] };
  const manifest = { languages: [{ code: "ru", profile: "site" }, { code: "uk", profile: "site" }] };
  const index = { schema: 1, catalogs: [{ code: "ru", keys: 3, translated: 3 }, { code: "it", keys: 3, translated: 0 }] };
  assert.deepEqual(siteLanguages(registry, manifest, index).map((l) => [l.code, l.interface]),
    [["en", true], ["de", false], ["it", true], ["ru", true]]);
  assert.deepEqual(siteLanguages(registry, null, { schema: 1, catalogs: [] }).map((l) => [l.code, l.interface]),
    [["en", true], ["de", false], ["ru", false]]);
  assert.deepEqual(indexCodes({ schema: 2, catalogs: [{ code: "ru" }] }), null);
  assert.deepEqual(indexCodes({ schema: 1, catalogs: [{ code: "ru" }, null, { code: 3 }] }), ["ru"]);
  assert.deepEqual(siteLanguages(registry, manifest, { schema: 2 }).map((l) => l.code), ["en", "de", "ru", "uk"],
    "an unknown index: the manifest's site rows");
  assert.deepEqual(siteLanguages(registry, manifest, { ...index, guides: ["ru", 3] }).map((l) => [l.code, l.guide]),
    [["en", true], ["de", false], ["it", false], ["ru", true]], "the index names the translated guides");
  assert.deepEqual(siteLanguages(registry, manifest, index).map((l) => l.guide), [true, null, null, null],
    "an index without guides: unknown");
});

test("a catalog document is checked and fetched once", async () => {
  assert.deepEqual(readCatalog({ schema: 1, language: "xx", strings: { A: "Б" } }, "xx"), { A: "Б" });
  assert.throws(() => readCatalog({ schema: 1, language: "yy", strings: {} }, "xx"), /unknown schema/);
  assert.equal(catalogURL("xx", "https://site.test/pick"), "https://site.test/data/site/xx.json");
  let calls = 0;
  const fetcher = async (url) => {
    calls += 1;
    assert.equal(url, "https://site.test/data/site/xa.json");
    return reply({ schema: 1, language: "xa", strings: { A: "Б" } });
  };
  assert.deepEqual(await loadCatalog("xa", fetcher, "https://site.test/"), { A: "Б" });
  assert.deepEqual(await loadCatalog("xa", fetcher, "https://site.test/"), { A: "Б" });
  assert.equal(calls, 1);
  assert.equal(await loadCatalog("en", fetcher), null);
  const warn = console.warn;
  console.warn = () => {};
  try {
    assert.equal(await loadCatalog("xb", async () => reply(null, false, 404), "https://site.test/"), null);
  } finally {
    console.warn = warn;
  }
});

test("the menu lists the site's languages; its choice sets the interface and the data language", async () => {
  const name = node("span", { langName: "" });
  const code = node("span", { langCode: "" });
  const list = menuList();
  const label = node("a", { ui: "Patch" });
  const dom = root([name, code, list, label]);
  const storage = memory();
  const urls = [];
  const fetcher = async (url) => {
    urls.push(url);
    if (url.endsWith("/data/registry.json")) return reply({ schema: 1, games: [{ id: "game", name: "Game", stage: "catalog", data: "./", languages: ["ru", "de"] }] });
    if (url.endsWith("/data/site/catalogs.json")) return reply({ schema: 1, catalogs: [{ code: "ru" }, { code: "uk" }] });
    if (url.endsWith("/data/site/ru.json")) return reply({ schema: 1, language: "ru", strings: RU });
    return reply(null, false, 404);
  };
  const heard = [];
  const off = onSiteLanguage((shown, chosen) => heard.push([shown, chosen, label.textContent]));
  assert.equal(await startSite({ fetcher, storage, root: dom, browser: ["en-GB"] }), "en");
  const items = list.items;
  assert.deepEqual(items.map((b) => [b.dataset.siteLang, b.kids.map((kid) => kid.textContent)]),
    [["en", ["English"]], ["de", ["German"]], ["ru", ["Russian"]], ["uk", ["Ukrainian"]]],
    "names only, no note; an interface language of the index only");
  assert.deepEqual([name.textContent, code.textContent, items[0].attrs["aria-pressed"]], ["English", "EN", "true"]);
  const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
  dom.click({ target: { closest: (selector) => (selector === "[data-site-lang]" ? items[2] : null) } });
  await tick();
  assert.deepEqual([uiLanguage(), chosenLanguage(), label.textContent, name.textContent, code.textContent], ["ru", "ru", "Патч", "Russian", "RU"]);
  assert.deepEqual(items.map((b) => b.attrs["aria-pressed"]), ["false", "false", "true", "false"]);
  assert.equal(storage.store.get(SITE_LANG_KEY), "ru");
  // German: game data in German, the interface in English
  dom.click({ target: { closest: (selector) => (selector === "[data-site-lang]" ? items[1] : null) } });
  await tick();
  assert.deepEqual([uiLanguage(), chosenLanguage(), label.textContent, name.textContent], ["en", "de", "Patch", "German"]);
  assert.deepEqual(heard, [["en", "en", "Patch"], ["ru", "ru", "Патч"], ["en", "de", "Patch"]]);
  assert.equal(await useSiteLanguage("ru", { fetcher, root: dom }), "ru");
  assert.ok(urls.includes("https://site.test/data/site/catalogs.json"));
  assert.deepEqual(urls.filter((url) => url.includes("/site/") && !url.endsWith("/catalogs.json")),
    ["https://site.test/data/site/ru.json"],
    "a catalog is fetched once per page; a language without one is never fetched");
  off();
  await useSiteLanguage("en", { fetcher, root: dom });
  assert.equal(heard.length, 4);
  assert.equal(label.textContent, "Patch");
});

test("a tool row folds into its menu only without room; the toggle names the current tool", () => {
  const classes = () => {
    const set = new Set();
    return { set, toggle: (name, on) => (on ? set.add(name) : set.delete(name)), remove: (name) => set.delete(name),
      contains: (name) => set.has(name) };
  };
  const toggle = { attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } };
  const label = node("span", { toolsCurrent: "", ui: "Tools" });
  const current = { textContent: "Save converter" };
  const list = { scrollWidth: 420 };
  const nav = { classList: classes(), clientWidth: 300, visible: true, getClientRects() { return this.visible ? [{}] : []; },
    querySelector: (s) => ({ ".tools-list [aria-current]": current, "[data-tools-current]": label, ".tools-list": list,
      ".tools-toggle": toggle })[s] ?? null };
  fitTools(nav);
  assert.ok(nav.classList.contains("compact"), "420 px of tools in 300 px: the menu");
  assert.deepEqual([label.textContent, label.dataset.ui], ["Save converter", undefined]);
  openTools(nav, true);
  assert.deepEqual([nav.classList.contains("open"), toggle.attrs["aria-expanded"]], [true, "true"]);
  nav.clientWidth = 640;
  fitTools(nav);
  assert.ok(!nav.classList.contains("compact"), "room again: the row");
  assert.deepEqual([nav.classList.contains("open"), toggle.attrs["aria-expanded"]], [false, "false"]);
  nav.visible = false;
  nav.clientWidth = 0;
  fitTools(nav);
  assert.ok(!nav.classList.contains("compact"), "a hidden masthead keeps its form");
  nav.visible = true;
  current.textContent = "←Home";
  current.querySelector = () => ({ textContent: "Home" });
  fitTools(nav);
  assert.equal(label.textContent, "Home");
});

test("the compact navigation reaches Home by keyboard and restores toggle focus", async () => {
  const classes = new Set(), focused = [];
  const toggle = { setAttribute() {}, focus: () => focused.push("toggle") };
  const labels = ["Home", "Patcher", "Translation editor", "Save converter", "Pokédex", "Guide"];
  const links = labels.map((textContent, index) => ({ textContent,
    matches: () => index === 2, focus: () => focused.push(textContent),
    closest: (selector) => selector === ".tools.open" && classes.has("open") ? nav : null,
  }));
  const nav = {
    classList: { toggle: (key, on) => on ? classes.add(key) : classes.delete(key), remove: (key) => classes.delete(key) },
    clientWidth: 98, getClientRects: () => [{}], contains: (target) => links.includes(target),
    querySelectorAll: () => links,
    querySelector: (selector) => ({ ".tools-toggle": toggle, ".tools-list": { scrollWidth: 650 },
      ".tools-list [aria-current]": links[2], "[data-tools-current]": node("span") })[selector] ?? null,
  };
  const dom = root([], { querySelectorAll: (selector) => selector === ".mast .tools"
    || (selector === ".tools.open" && classes.has("open")) ? [nav] : [] });
  await startSite({ root: dom, storage: memory(), browser: ["en"], fetcher: async () => reply(null, false, 404) });
  assert.ok(classes.has("compact"));
  openTools(nav, true);
  const press = (target, key) => {
    let prevented = false;
    dom.keydown({ target, key, preventDefault() { prevented = true; } });
    assert.ok(prevented);
  };
  press(links[2], "ArrowUp");
  press(links[1], "ArrowUp");
  press(links[0], "ArrowUp");
  press(links[5], "ArrowDown");
  assert.deepEqual(focused, ["Patcher", "Home", "Guide", "Home"]);
  press(links[0], "Escape");
  assert.equal(focused.at(-1), "toggle");
  assert.ok(!classes.has("open"));
  openTools(nav, true);
  dom.click({ target: { closest: () => null } });
  assert.ok(!classes.has("open"));
});

test("a later switch wins over an earlier one still loading", async () => {
  let release;
  const slow = () => new Promise((resolve) => { release = () => resolve(reply({ schema: 1, language: "xs", strings: {} })); });
  const dom = root([]);
  const first = useSiteLanguage("xs", { fetcher: slow, root: dom });
  assert.equal(await useSiteLanguage("en", { root: dom }), "en");
  release();
  assert.equal(await first, "en");
  assert.equal(uiLanguage(), "en");
});

test("numbers follow the site language; language names stay English", () => {
  setUiLanguage("ru");
  assert.equal(languageName("en"), "English");
  assert.equal(languageName("ru"), "Russian");
  assert.equal(formatNumber(1234), (1234).toLocaleString("ru"));
  setUiLanguage("en");
  assert.equal(languageName("de"), "German");
  assert.equal(formatNumber(1234), "1,234");
});

// Every visible text and label must have a key.

const VOID = new Set(["input", "br", "img", "meta", "link", "hr", "source", "wbr", "col", "area"]);
const RAW = new Set(["script", "style", "code", "kbd", "svg"]);
const WORD = /[A-Za-z]{2,}/;
const NAMES = new Set(["gen3tl", "EN", "RU"]); // the brand and the switch's language codes
const TWINS = { title: "data-ui-title", "aria-label": "data-ui-label", placeholder: "data-ui-placeholder" };
const decode = (s) => s.replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(Number(n))).replace(/&times;/g, "×")
  .replace(/&middot;/g, "·").replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">");

/** Page markup text no data-ui key covers. */
export function unkeyed(html) {
  const found = [];
  const stack = [];
  const tokens = /<!--[^]*?-->|<![^>]*>|<(\/?)([a-zA-Z][\w-]*)((?:[^>"']|"[^"]*"|'[^']*')*)>|([^<]+)/g;
  for (const m of html.matchAll(tokens)) {
    if (m[0].startsWith("<!")) continue;
    const top = stack.at(-1);
    if (m[4] !== undefined) {
      const text = decode(m[4]).replace(/\s+/g, " ").trim();
      if (!text || !WORD.test(text) || NAMES.has(text) || stack.some((e) => RAW.has(e.tag))) continue;
      if (!top?.keyed && !top?.hole) found.push(`text "${text}" in <${top?.tag}>`);
      continue;
    }
    const [, close, name, attrs] = m;
    const tag = name.toLowerCase();
    if (close) {
      const at = stack.map((e) => e.tag).lastIndexOf(tag);
      if (at >= 0) stack.length = at;
      continue;
    }
    const hole = /\sdata-ui-hole="/.test(attrs);
    if (top?.keyed && !hole && !RAW.has(tag) && !stack.some((e) => RAW.has(e.tag))) found.push(`<${tag}> inside keyed <${top.tag}>`);
    if (!stack.some((e) => RAW.has(e.tag))) {
      for (const [attr, twin] of Object.entries(TWINS)) {
        const value = attrs.match(new RegExp(`\\s${attr}="([^"]*)"`))?.[1];
        if (value && WORD.test(value) && !attrs.includes(`${twin}=`)) found.push(`${attr} "${value}" on <${tag}>`);
      }
    }
    // a hole's text is a name unless keyed
    if (!VOID.has(tag) && !attrs.trimEnd().endsWith("/")) stack.push({ tag, keyed: /\sdata-ui="/.test(attrs), hole });
  }
  return found;
}

test("the audit finds unkeyed texts and labels", () => {
  assert.deepEqual(unkeyed('<p data-ui="A b">A b</p><p>Plain text</p><button title="Go on">→</button><code>gen3tl web server start --page editor</code>'),
    ['text "Plain text" in <p>', 'title "Go on" on <button>']);
  assert.deepEqual(unkeyed('<p data-ui="Read the guide">Read <a href="#">the guide</a></p>'),
    ["<a> inside keyed <p>", 'text "the guide" in <a>']);
  assert.deepEqual(unkeyed('<p data-ui="Use {tool}">Use <a data-ui-hole="tool">RomPatcher.js</a></p>'), []);
});

test("every page keys every interface text and label of its markup", () => {
  for (const name of ["index.html", "pick.html", "patch.html", "save.html", "editor.html", "pokedex.html", "submit.html"]) {
    assert.deepEqual(unkeyed(page(name)), [], name);
  }
  for (const name of guidePages()) { // a guide's body is a document
    for (const part of [...page(name).matchAll(/<header class="mast"[^>]*>[^]*?<\/header>|<footer class="foot"[^>]*>[^]*?<\/footer>/g)]) {
      assert.deepEqual(unkeyed(part[0]), [], `${name} chrome`);
    }
  }
});

test("Pokédex search and filters use the shared site catalog", () => {
  const catalog = page("i18n/ru.po");
  for (const key of ["Search", "Filters", "Type chart"]) {
    assert.ok(catalog.includes(`msgctxt "site:${key}"`), key);
  }
  assert.match(catalog, /msgid "Search"\nmsgstr "Поиск"/);
  assert.match(catalog, /msgid "Filters"\nmsgstr "Фильтры"/);
  assert.match(catalog, /msgid "Type chart"\nmsgstr "Таблица типов"/);
  assert.doesNotMatch(catalog, /msgid "(?:Search species|Search Pokedex entries|Search name, dex number, slot or id|Search entries, ids, types or species|Filter by type, values, effect or learner)"/);
});

test("localized search placeholders survive catalog refills", () => {
  const field = node("input", { uiPlaceholder: "Search {what}", uiLabel: "Search", uiSite: "" });
  showPlaceholder(field, "Search {what}", { what: "moves" });
  fillPage(root([field]));
  assert.equal(field.attrs.placeholder, "Search moves");
  assert.equal(field.attrs["aria-label"], "Search");
  setCatalog({ "Search": "Поиск", "Search {what}": "Поиск {what}" });
  showPlaceholder(field, "Search {what}", { what: "приёмов" });
  fillPage(root([field]));
  assert.equal(field.attrs.placeholder, "Поиск приёмов");
  assert.equal(field.attrs["aria-label"], "Поиск");
  setCatalog(null);
});
