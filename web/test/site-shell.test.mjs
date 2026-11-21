import assert from "node:assert/strict";
import { cpSync, existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

import { gameHash, PAGES, toolURL } from "../js/routes.js";
import { hrefs, SURFACES } from "../js/site-model.js";
import { setToolContext, startPage } from "../js/site-shell.js";
import { fillPage, prepareHeaders, setCatalog, setLabelSource, SOURCE_LANGUAGE, startGuide, useSiteLanguage } from "../js/ui.js";
import { fakeFetch, stubPage } from "./fixture.mjs";

const pages = ["index", "pick", "patch", "save", "editor", "pokedex", "submit"];
const defaults = ["license-gen3tl.txt", "fonts/OFL.txt"];
const game = (id) => ({ id, name: `Game ${id}`, stage: "translation", languages: ["xx", "yy"], data: "./" });
const metadata = (games, site) => ({ schema: 1, games, ...(site ? { site } : {}) });
const anchors = (footer) => footer.querySelectorAll("a");
const href = (node) => node.getAttribute("href");
const styledPages = readdirSync(new URL("../", import.meta.url)).filter((name) => name.endsWith(".html"))
  .map((name) => name.slice(0, -5)).sort();
const catalogs = [null, ...readdirSync(new URL("../i18n/", import.meta.url)).filter((name) => name.endsWith(".po"))
  .map((name) => Object.fromEntries([...readFileSync(new URL(`../i18n/${name}`, import.meta.url), "utf8")
    .matchAll(/^msgid (".*")\nmsgstr (".*")$/gm)].map(([, key, value]) => [JSON.parse(key), JSON.parse(value)])))];

test("the shell command checks the pages and repairs a changed region", (context) => {
  const root = fileURLToPath(new URL("../../", import.meta.url));
  const localPython = join(root, ".venv/bin/python");
  const python = process.env.PYTHON ?? (existsSync(localPython) ? localPython : "python3");
  const run = (...args) => spawnSync(python, ["-m", "gen3tl", "web", "site-shell", ...args], {
    cwd: root, encoding: "utf8", env: { ...process.env, PYTHONPATH: join(root, "src") },
  });
  const check = run();
  assert.equal(check.status, 0, check.stderr || check.error?.message || check.stdout);
  const web = mkdtempSync(join(tmpdir(), "site-shell-"));
  context.after(() => rmSync(web, { recursive: true, force: true }));
  cpSync(new URL("../fragments/", import.meta.url), join(web, "fragments"), { recursive: true });
  const original = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  writeFileSync(join(web, "index.html"), original.replace('>gen3tl</a>', '>Changed</a>'));
  const failed = run("--web", web);
  assert.equal(failed.status, 1);
  assert.match(failed.stdout, /index.html: out of sync/);
  assert.equal(run("--web", web, "--sync").status, 0);
  assert.equal(readFileSync(join(web, "index.html"), "utf8"), original);
});

function pageFixture(context, page, hash = "") {
  const fixture = stubPage(context, page, hash);
  const query = document.querySelectorAll;
  const footer = query("[data-site-footer]")[0];
  context.after(() => { setCatalog(null); setLabelSource(null); });
  return { ...fixture, footer, tools: query("[data-tool]") };
}

for (const page of styledPages) {
  test(`${page}: static chrome contains links, controls and translated labels`, (context) => {
    stubPage(context, page);
    context.after(() => { setCatalog(null); setLabelSource(null); });
    setCatalog(null);
    const masts = document.querySelectorAll("[data-site-mast]");
    assert.equal(masts.length, page === "editor" ? 2 : 1);
    const routes = masts.map((mast) => anchors(mast.querySelector(".tools-list"))
      .filter((node) => !node.parentNode.hasAttribute("data-site-licenses")).map(href));
    const labels = ["Home", "Patcher", "Translation editor", "Save converter", "Pokédex", "Guide"];
    for (const mast of masts) {
      assert.equal(mast.querySelectorAll("button").length, 2);
      assert.equal(mast.querySelectorAll("nav").length, 1);
    }
    const footer = document.querySelectorAll("[data-site-footer]")[0];
    const declared = anchors(footer).map(href);
    const guide = /^(?:save-)?guide(?:\.|$)/.test(page);
    assert.equal(declared.length, guide ? 3 : 2);
    prepareHeaders(document);
    for (const [index, mast] of masts.entries()) {
      assert.deepEqual(mast.children.map((node) => node.className), ["brand", "tools", "mast-controls"]);
      assert.equal(href(mast.children[0]), "./");
      assert.equal(mast.children[0].textContent, "gen3tl");
      const nav = mast.querySelector(".tools"), list = nav.querySelector(".tools-list");
      assert.deepEqual(nav.children.map((node) => node.className), ["tools-toggle", "tools-list"]);
      assert.equal(nav.children[0].getAttribute("aria-expanded"), "false");
      const links = anchors(list).filter((node) => !node.parentNode.hasAttribute("data-site-licenses"));
      assert.equal(links.length, 6);
      assert.deepEqual(links.map(href), routes[index].map((url) => url.replace(/\.html$/, "")));
      const home = links[0];
      assert.equal(home.className, "brand c-home");
      assert.equal(home.children[0].textContent, "←");
      assert.equal(home.children[0].getAttribute("aria-hidden"), "true");
      const active = guide ? "Guide" : ({ index: "Home", patch: "Patcher", editor: "Translation editor",
        save: "Save converter", pokedex: "Pokédex" })[page];
      assert.deepEqual(links.filter((node) => node.hasAttribute("aria-current"))
        .map((node) => node.dataset.ui ?? node.dataset.uiLabel), active ? [active] : []);
      const controls = mast.querySelector(".mast-controls");
      assert.deepEqual(controls.children.map((node) => node.className), ["lang-menu", "theme"]);
      assert.equal(controls.querySelector("summary").dataset.uiTitle,
        page === "editor" ? "Display language" : "Site and game language");
      assert.equal(controls.querySelectorAll("[data-lang-list]").length, 1);
      assert.equal(controls.querySelectorAll("[data-toggle-theme]").length, 1);
      assert.equal(mast.querySelectorAll("svg").length, 5);
      for (const node of mast.querySelectorAll("svg")) {
        assert.equal(node.namespaceURI, "http://www.w3.org/2000/svg");
        assert.equal(node.getAttribute("aria-hidden"), "true");
      }
      for (const tag of ["h1", "h2", "style"]) assert.equal(mast.querySelectorAll(tag).length, 0);
      assert.equal(mast.querySelectorAll("[style]").length, 0);
      assert.equal(mast.querySelectorAll("[data-site-licenses]").length, page === "pokedex" ? 1 : 0);
    }
    assert.equal(footer.children.length, 1);
    assert.equal(footer.children[0].className, "wrap");
    assert.deepEqual(anchors(footer).map(href), guide ? declared : defaults);
    setLabelSource(() => ({ text: "Game text", game: true }));
    for (const catalog of catalogs) {
      setCatalog(catalog);
      fillPage();
      for (const mast of masts) {
        const list = mast.querySelector(".tools-list");
        assert.deepEqual(list.children.slice(0, 6).map((link) => link.querySelector("[data-ui]")?.textContent ?? link.textContent),
          labels.map((key) => catalog?.[key] || key));
        assert.equal(mast.querySelector(".c-home").getAttribute("aria-label"), catalog?.Home || "Home");
        assert.equal(mast.querySelector(".theme").getAttribute("aria-label"), catalog?.["Toggle dark mode"] || "Toggle dark mode");
      }
      for (const link of anchors(footer)) assert.equal(link.textContent, catalog?.[link.dataset.ui] || link.dataset.ui);
    }
  });
}

test("the editor retains workspace destinations and guide tab behavior", (context) => {
  const { $ } = pageFixture(context, "editor");
  const masts = document.querySelectorAll("[data-site-mast]");
  assert.equal(href($("link-start")), "#start");
  assert.equal($("link-start").hasAttribute("data-tool"), false);
  assert.equal(href($("link-pokedex")), "pokedex");
  const guide = masts[1].querySelector('[data-ui="Guide"]');
  assert.equal(guide.getAttribute("target"), "_blank");
  assert.equal(guide.getAttribute("rel"), "noopener");
  assert.equal(guide.getAttribute("data-ui-title"), "Contributor guide, in a new tab");
  assert.equal(masts[0].querySelector('[data-ui="Guide"]').getAttribute("target"), null);
});

test("the Pokédex base URL does not mark Home current", (context) => {
  const { $ } = pageFixture(context, "pokedex");
  const mast = document.querySelectorAll("[data-site-mast]")[0];
  assert.equal(mast.querySelector(".c-home").getAttribute("aria-current"), null);
  assert.equal(mast.querySelector('[data-tool="pokedex"]').getAttribute("aria-current"), "page");
  for (const tool of ["patch", "editor", "save"]) assert.equal($(`link-${tool}`).dataset.tool, tool);
});

for (const page of styledPages.filter((name) => /^(?:save-)?guide(?:\.|$)/.test(name))) {
  test(`${page}: guide entry point starts the shared shell`, async (context) => {
    const { storage } = stubPage(context, page);
    const own = page.includes(".") ? page.split(".")[1] : SOURCE_LANGUAGE;
    document.documentElement.lang = own;
    document.body.dataset.guide = page.split(".")[0];
    storage.setItem("gen3tl-site-lang", own);
    const fetcher = fakeFetch({
      "https://site.test/data/registry.json": metadata([], { licenses: [] }),
      "https://site.test/data/site/catalogs.json": { schema: 1, catalogs: [{ code: own }], guides: [own] },
      [`https://site.test/data/site/${own}.json`]: { schema: 1, language: own, strings: {} },
    });
    const source = readFileSync(new URL("../js/guide.js", import.meta.url), "utf8");
    assert.match(source, /import \{ startPage \} from "\.\/site-shell.js"/);
    assert.match(source, /startGuide\(\{ start: startPage,/);
    const moves = [];
    context.after(() => setCatalog(null));
    await startGuide({ start: startPage, root: document, own, family: document.body.dataset.guide,
      storage, fetcher, browser: [], go: (target) => moves.push(target) });
    assert.deepEqual(moves, []);
    assert.equal(document.querySelectorAll("[data-lang-list]").length, 1);
    assert.equal(document.querySelectorAll("[data-toggle-theme]").length, 1);
    const footer = document.querySelectorAll("[data-site-footer]")[0];
    assert.equal(footer.children[0].className, "wrap");
    assert.equal(anchors(footer).length, 3);
  });
}

test("one builder encodes tool context and routes missing games to selection", () => {
  for (const tool of Object.keys(PAGES)) {
    assert.equal(toolURL(tool), `pick?next=${tool}`);
    assert.equal(toolURL(tool, "game /%", "xx-Latn-AA"), `${tool}#game%20%2F%25/xx-Latn-AA`);
    assert.equal(hrefs({ id: "game" }, { code: "xx" })[tool], toolURL(tool, "game", "xx"));
    assert.equal(SURFACES[tool].url({ id: "game" }, "xx"), toolURL(tool, "game", tool === "pokedex" ? "xx" : null));
  }
  assert.equal(toolURL("submit", "site", "xx"), "submit#site/xx");
  assert.equal(toolURL("editor", "site", "xx"), "editor#site/xx");
  assert.equal(toolURL("pokedex", "site", "xx"), "pick?next=pokedex");
  assert.equal(toolURL("pokedex", "game", "xx", "species", "1/2"), "pokedex#game/xx/species/1%2F2");
  assert.equal(gameHash("game", "xx", "text /%"), "#game/xx/text%20%2F%25");
});

for (const page of pages) {
  test(`${page}: shared footer defaults and header context survive project changes`, (context) => {
    const { footer, tools } = pageFixture(context, page);
    assert.deepEqual(anchors(footer).map(href), defaults);
    assert.ok(tools.length >= 4);
    for (const [game, language] of [[null, null], ["first", "xx"], ["second", "yy"], ["site", "xx"]]) {
      setToolContext(game, language);
      for (const link of tools) assert.equal(href(link), toolURL(link.dataset.tool, game, language));
    }
  });
}

test("the Pokédex menu group carries the same licence links as the footer", (context) => {
  const { footer } = pageFixture(context, "pokedex");
  const group = document.querySelectorAll("[data-site-licenses]")[0];
  assert.ok(group);
  assert.deepEqual(anchors(group).map(href), defaults);
  assert.deepEqual(anchors(group).map((node) => node.textContent), anchors(footer).map((node) => node.textContent));
});

test("the browser shell never constructs page chrome", () => {
  const source = readFileSync(new URL("../js/site-shell.js", import.meta.url), "utf8");
  assert.doesNotMatch(source, /createElement|replaceChildren|innerHTML|insertAdjacentHTML|\bel\(/);
});

for (const [name, games, hash, remembered, expected] of [
  ["single game", [game("first")], "", null, "first"],
  ["explicit game", [game("first"), game("second")], "#second/xx/species/4", null, "second"],
  ["remembered game", [game("first"), game("second")], "", "second", "second"],
  ["default game", [game("first"), game("second")], "", null, "first"],
  ["empty registry", [], "", null, null],
]) {
  test(`startup uses ${name} context without rebuilding shared markup`, async (context) => {
    const { footer, tools, storage } = pageFixture(context, "index", hash);
    if (remembered) storage.setItem("gen3tl-game", remembered);
    const fetcher = fakeFetch({
      "https://site.test/data/registry.json": metadata(games, { licenses: [{ label_key: "Content licence", url: "terms" }] }),
      "https://site.test/data/languages.json": { profiles: [], languages: [] },
      "https://site.test/data/site/catalogs.json": { schema: 1, catalogs: [] },
    });
    await startPage({ fetcher, storage, browser: [SOURCE_LANGUAGE] });
    assert.equal(fetcher.calls.filter((url) => url.endsWith("registry.json")).length, 1);
    assert.deepEqual(anchors(footer).map(href), defaults);
    for (const link of tools) assert.equal(href(link), toolURL(link.dataset.tool, expected, hash ? "xx" : SOURCE_LANGUAGE));
    setToolContext("second", "yy");
    await useSiteLanguage(SOURCE_LANGUAGE);
    for (const link of tools) assert.equal(href(link), toolURL(link.dataset.tool, "second", "yy"));
  });
}

test("invalid and unavailable registries retain safe defaults", async (context) => {
  const { footer, tools, storage } = pageFixture(context, "patch");
  const fetcher = fakeFetch({ "https://site.test/data/registry.json": metadata([game("first")], {
    licenses: [{ label_key: "Unsafe", url: "javascript:alert(1)" }],
  }) });
  await startPage({ fetcher, storage, browser: [SOURCE_LANGUAGE] });
  assert.deepEqual(anchors(footer).map(href), defaults);
  for (const link of tools) assert.equal(href(link), `pick?next=${link.dataset.tool}`);
  await startPage({ fetcher: fakeFetch({}), storage, browser: [SOURCE_LANGUAGE] });
  assert.deepEqual(anchors(footer).map(href), defaults);
});

test("the editor start footer stays outside the workspace status bar", () => {
  const html = readFileSync(new URL("../editor.html", import.meta.url), "utf8");
  const start = html.slice(html.indexOf('<div id="start"'), html.indexOf('<div id="app"'));
  const workspace = html.slice(html.indexOf('<div id="app"'));
  assert.match(start, /data-site-footer/);
  assert.doesNotMatch(workspace, /data-site-footer/);
  assert.match(workspace, /<footer class="statusbar">/);
  assert.match(workspace, /href="#start" id="link-start"/);
});
