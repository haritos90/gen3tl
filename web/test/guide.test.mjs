// The contributor guide per language: links follow the choice.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  SITE_LANG_KEY, followGuide, guideFor, guideMove, guidePage, siteLanguages, startGuide, startSite, useSiteLanguage,
} from "../js/ui.js";

const reply = (doc, ok = true, status = 200) => ({ ok, status, json: async () => doc });
const REGISTRY = { schema: 1, games: [{ id: "game", name: "Game", stage: "catalog", data: "./", languages: ["ru", "de"] }] };
const INDEX = { schema: 1, catalogs: [{ code: "ru" }, { code: "uk" }], guides: ["ru"] };

/** fetch over the registry, an index and two catalogs. */
function site(index = INDEX) {
  return async (url) => {
    if (url.endsWith("/data/registry.json")) return reply(REGISTRY);
    if (url.endsWith("/data/site/catalogs.json")) return reply(index);
    for (const code of ["ru", "uk"]) {
      if (url.endsWith(`/data/site/${code}.json`)) return reply({ schema: 1, language: code, strings: {} });
    }
    return reply(null, false, 404);
  };
}

/** A link with an href. */
const link = (href) => ({ attrs: { href }, getAttribute(k) { return this.attrs[k] ?? null; }, setAttribute(k, v) { this.attrs[k] = v; } });

/** A page holding links: only a[href] matches. */
function page(links) {
  return { baseURI: "https://site.test/editor", querySelectorAll: (s) => (s === "a[href]" ? links : []),
    addEventListener() {} };
}

const memory = (code) => {
  const store = new Map(code ? [[SITE_LANG_KEY, code]] : []);
  return { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v), removeItem: (k) => store.delete(k) };
};

test("a language's guide is its translation where the index lists one, else the English page", () => {
  assert.equal(guidePage("en"), "guide");
  assert.equal(guidePage("ru"), "guide.ru");
  assert.equal(guidePage("en", "save-guide"), "save-guide");
  assert.equal(guidePage("ru", "save-guide"), "save-guide.ru");
  const languages = siteLanguages(REGISTRY, null, INDEX);
  const codes = ["en", "ru", "uk", "de", "fr"];
  assert.deepEqual(codes.map((code) => guideFor(code, languages)),
    ["guide", "guide.ru", "guide", "guide", null], "a language the site does not serve: unknown");
  assert.deepEqual(codes.map((code) => guideFor(code, languages, "save-guide")),
    ["save-guide", "save-guide.ru", "save-guide", "save-guide", null]);
  const older = siteLanguages(REGISTRY, null, { schema: 1, catalogs: [{ code: "ru" }] });
  assert.deepEqual(["en", "ru"].map((code) => guideFor(code, older)), ["guide", null], "an index without guides");
  assert.equal(guideFor("ru", null), null);
});

test("a guide page moves to the guide of the choice and stays on its own", () => {
  const languages = siteLanguages(REGISTRY, null, INDEX);
  assert.equal(guideMove("en", "ru", languages), "guide.ru");
  assert.equal(guideMove("en", "de", languages), null, "no German guide: the English page stays");
  assert.equal(guideMove("ru", "uk", languages), "guide", "a catalog without a guide: English");
  assert.equal(guideMove("ru", "en", languages), "guide");
  assert.equal(guideMove("ru", "ru", languages), null);
  assert.equal(guideMove("ru", "uk", languages, "save-guide"), "save-guide");
  assert.equal(guideMove("en", "ru", languages, "save-guide"), "save-guide.ru");
  assert.equal(guideMove("ru", "ru", siteLanguages(REGISTRY, null, { schema: 1, catalogs: [], guides: [] })), "guide",
    "the index lists no Russian guide");
  assert.equal(guideMove("ru", "de", siteLanguages(REGISTRY, null, { schema: 1, catalogs: [] })), null,
    "an index without guides: the page stays");
});

test("guide links follow the switch within each family, with English fallback", async () => {
  const links = [link("guide"), link("guide#submit"), link("save-guide"), link("save-guide.ru#use"),
    link("save-guides.html"), link("editor"), link("#keys"), link("guides.html")];
  const dom = page(links);
  const fetcher = site();
  const hrefs = () => links.map((l) => l.attrs.href);
  await useSiteLanguage("ru", { fetcher, root: dom });
  assert.deepEqual(hrefs(), ["guide", "guide#submit", "save-guide", "save-guide.ru#use",
    "save-guides.html", "editor", "#keys", "guides.html"], "languages not loaded: links stay as written");
  await startSite({ fetcher, storage: memory("ru"), root: dom, browser: [] });
  assert.deepEqual(hrefs(), ["guide.ru", "guide.ru#submit", "save-guide.ru", "save-guide.ru#use",
    "save-guides.html", "editor", "#keys", "guides.html"]);
  await useSiteLanguage("uk", { fetcher, root: dom });
  assert.deepEqual(hrefs().slice(0, 4), ["guide", "guide#submit", "save-guide", "save-guide#use"],
    "Ukrainian interface, no Ukrainian guide");
  await useSiteLanguage("ru", { fetcher, root: dom });
  await useSiteLanguage("de", { fetcher, root: dom });
  assert.deepEqual(hrefs().slice(0, 4), ["guide", "guide#submit", "save-guide", "save-guide#use"],
    "a game language only: English");
  followGuide(dom, "fr");
  assert.deepEqual(hrefs().slice(0, 4), ["guide", "guide#submit", "save-guide", "save-guide#use"],
    "an unknown language changes nothing");
});

test("the Russian guide stays for Russian and moves to the English page for other choices", async () => {
  const moves = [];
  const dom = page([link("guide.ru")]);
  const fetcher = site();
  assert.equal(await startGuide({ own: "ru", go: (target) => moves.push(target), fetcher, storage: memory("ru"), root: dom,
    browser: [] }), "ru");
  assert.deepEqual(moves, [], "the remembered Russian: it stays");
  await useSiteLanguage("uk", { fetcher, root: dom });
  await useSiteLanguage("ru", { fetcher, root: dom });
  await useSiteLanguage("en", { fetcher, root: dom });
  assert.deepEqual(moves, ["guide", "guide"]);
});

test("save-guide startup changes language without entering the contributor guide", async () => {
  const moves = [];
  const dom = page([link("save-guide.ru")]);
  const fetcher = site();
  await startGuide({ own: "ru", family: "save-guide", go: (target) => moves.push(target), fetcher,
    storage: memory("ru"), root: dom, browser: [] });
  assert.deepEqual(moves, []);
  await useSiteLanguage("uk", { fetcher, root: dom });
  await useSiteLanguage("ru", { fetcher, root: dom });
  await useSiteLanguage("en", { fetcher, root: dom });
  assert.deepEqual(moves, ["save-guide", "save-guide"], "Russian is already the page's own language");
});

test("legacy guide links keep their query and fragment across language changes", async () => {
  const links = [link("guide.html?lang=en#keys"), link("save-guide.ru.html#use")];
  const dom = page(links);
  await startSite({ fetcher: site(), storage: memory("ru"), root: dom, browser: [] });
  assert.deepEqual(links.map((l) => l.attrs.href), ["guide.ru?lang=en#keys", "save-guide.ru#use"]);
  await useSiteLanguage("en", { fetcher: site(), root: dom });
  assert.deepEqual(links.map((l) => l.attrs.href), ["guide?lang=en#keys", "save-guide#use"]);
});
