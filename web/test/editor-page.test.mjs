// The editor page markup: the modules' elements.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { test } from "node:test";
import { runInNewContext } from "node:vm";
import { Codec } from "../js/font.js";
import { Edits } from "../js/store.js";
import { EDITOR_SCHEMA, groupsOf, loadIndex } from "../js/data.js";
import { joinRows } from "../js/gametext.js";
import { joinedValue } from "../js/editor-cache.js";
import { chosenLanguage, onSiteLanguage, setCatalog, setLabelSource, setUiLanguage, SITE_LANG_KEY, startSite, t, useSiteLanguage } from "../js/ui.js";
import { missingTerms, setPlaces } from "../js/checktext.js";
import { CODEC, domPage, fakeFetch, NEEDS_DOM, noteCollection, observeRemovals, PACK, STYLE, storedLanguage, stubPage } from "./fixture.mjs";

const PAGE = "../editor.html";
const html = readFileSync(new URL(PAGE, import.meta.url), "utf8");
const ids = [...html.matchAll(/id="([^"]+)"/g)].map((m) => m[1]);
const options = (id) => [...html.match(new RegExp(`<select id="${id}"[^]*?</select>`))[0]
  .matchAll(/<option value="([^"]*)"/g)].map((m) => m[1]);

function editorSource() {
  const imports = new Map();
  const bodies = [];
  for (const name of ["state", "controls", "catalog", "game-text", "status", "context", "entry", "texts", "list", "rom", "workspace",
    "references", "actions", "start", "editor"]) {
    const file = new URL(`../js/editor/${name}.js`, import.meta.url);
    let source = readFileSync(file, "utf8");
    source = source.replace(/import\s*\{([\s\S]*?)\}\s*from\s*"([^"]+)";/g, (statement, names, path) => {
      if (path.startsWith("./")) return "";
      const target = new URL(path, file).href;
      if (!imports.has(target)) imports.set(target, new Set());
      for (const name of names.split(",").map(value => value.trim()).filter(Boolean)) imports.get(target).add(name);
      return "";
    });
    bodies.push(source.replace(/^export /gm, ""));
  }
  const external = [...imports].map(([target, names]) => `import { ${[...names].join(", ")} } from ${JSON.stringify(target)};`);
  return [...external, ...bodies].join("\n");
}

async function objectWorkspace(context) {
  const page = await referenceWorkspace(context);
  const { $, files, base } = page;
  const focus = (node) => {
    node.focus = () => { document.activeElement = node; };
    node.select = () => { node.selected = true; };
    return node;
  };
  const create = document.createElement;
  document.createElement = (tag) => {
    const node = focus(create(tag));
    node.getContext = () => new Proxy({}, { get: () => () => {} });
    return node;
  };
  for (const id of ids) focus($(id));
  for (const id of ["fo-sample", "fo-sheet", "fo-art"]) $(id).getContext = () => new Proxy({}, { get: () => () => {} });
  const terms = ["Red", "Blue", "Green"].map((name) => ({ id: `item:${name}/gem`, category: "item",
    source_text: `${name} Gem`, translation: name, status: "approved", declension: "fixed", in_source: [], in_translation: [], forms: {}, notes: "", sources: "" }));
  for (const language of ["es", "de"]) {
    const index = files[`${base}${language}/index.json`];
    index.terms = { path: "terms.json" };
    files[`${base}${language}/terms.json`] = { schema: EDITOR_SCHEMA, terms, slots: { groups: [], lines: [] } };
    index.graphics = { inventory: [{ id: "P/1", category: "keep", screen: "Sample", reason: "", source_text: "" }],
      edits: {}, palette: {}, sheet_width: {}, tilemaps: [] };
    index.font_sheets = { styles: {}, sheets: ["sheet/one", "sheet %2F"].map((name) => ({ name, kind: "font", style: "normal",
      format: "16x16", codes: ["01"], count: 512, rom: { glyphs: 0, widths: 0 }, glyphs: [
        { code: "BB", tile: 0xBB, char: "A", source: "base" },
        { code: "105", tile: 0x105, name: "F9 05", source: "base" },
      ] })) };
    index.engine_rules = { schema: "1.0.0", rules: [{ id: "ending/group", title: "Grouped ending", kind: "catalog",
      description: "Shared ending.", operands: [], related: ["sym:two"], examples: [], references: [],
      sources: [{ entry: "sym:two", available: true, state: "text" }] }] };
    index.keyboard = { style: "absent", first_page: 1,
      window: { width: 152, height: 64, row_height: 16, top: 1, cursor: { left: 8, width: 12 } },
      pages: [{ name: "page/zero %", keyboard: 0, look: 0 }, { name: "page one", keyboard: 0, look: 0 }],
      keyboards: [{ name: "base", source_text: true, columns: 1, rows: [] }],
      chat: { style: "absent", columns: 1, column_width: 8, pages: [{ name: "chat", rows: [
        { name: "chat/upper %2F", id: "sym:one", text: "A", widths: { BB: 6 }, bytes: 1 },
        { name: "chat_lower_1", id: "sym:two", text: "a", widths: { D5: 6 }, bytes: 1 },
      ] }] } };
  }
  await page.openWorkspace("game", "es");
  const previousCSS = globalThis.CSS;
  globalThis.CSS = { escape: (value) => value };
  context.after(() => {
    if (previousCSS) globalThis.CSS = previousCSS;
    else delete globalThis.CSS;
  });
  const writes = [];
  const previous = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  Object.defineProperty(globalThis, "navigator", { value: { clipboard: { writeText: async (value) => writes.push(value) } }, configurable: true });
  context.after(() => {
    if (previous) Object.defineProperty(globalThis, "navigator", previous);
    else delete globalThis.navigator;
    setCatalog(null);
    setUiLanguage("en");
  });
  const lookup = async (value, prefix = "workspace") => {
    $(`${prefix}-reference`).value = value;
    await page.openLookup(prefix);
  };
  return { ...page, lookup, writes };
}

test("every scoped view copies its native identity and lookup reveals it without a ROM", async (context) => {
  const { $, state, lookup, copyObjectReference, writes, wireObjectReferences } = await objectWorkspace(context);
  wireObjectReferences();
  const cases = [
    ["text", "sym:two", "texts", "e-id", "search"],
    ["term", "item:Red/gem", "terms", "t-id", "t-search"],
    ["family", "word:item:gem", "families", "fam-id", "fam-search"],
    ["picture", "P/1", "pictures", "p-id", "p-search"],
    ["keyboard-page", "page/zero %", "keyboards", "k-name"],
    ["font", "sheet/one", "fonts", "fo-name"],
    ["engine-rule", "ending/group", "enginerules", "er-id", "er-search"],
  ];
  const historyCalls = [];
  const push = history.pushState.bind(history);
  history.pushState = (...args) => { historyCalls.push(location.hash); push(...args); };
  for (const [kind, id, view, native, search] of cases) {
    if (search) $(search).value = "no match";
    const reference = `gen3tl:game/es/${kind}/${encodeURIComponent(id).replace(/%3A/g, ":")}`;
    const before = location.hash;
    await lookup(reference);
    assert.equal($("workspace-lookup-status").textContent, `Opened ${id}`);
    assert.equal(state.view, view);
    assert.equal($(native).textContent, id);
    assert.equal($(view).hidden, false);
    assert.equal($(`copy-${kind}`).disabled, false);
    assert.equal(document.activeElement, $(`copy-${kind}`));
    assert.equal(historyCalls.at(-1), before);
    if (search) assert.equal($(search).value, "");
    assert.equal(typeof $(`copy-${kind}`).listeners.click, "function");
    await copyObjectReference(kind);
    assert.equal(writes.at(-1), reference);
    assert.equal($("copy-status").textContent, "Reference copied");
    const hash = location.hash;
    await lookup(`https://untrusted.test/editor${hash}`);
    assert.equal($(native).textContent, id);
    assert.equal(location.hash, hash);
  }
  assert.equal(state.rom, null);
  assert.equal($("e-id").textContent, "sym:two");
  assert.ok(!ids.includes("copy-glyph") && !ids.includes("copy-chat-row"));
});

test("lookup supports start selection, catalog switches, aliases and Back restoration", async (context) => {
  const { $, state, lookup, route, files, base, fetcher, buildGames, copyObjectReference, writes } = await objectWorkspace(context);
  files[`${base}keys.json`] = { ids: { s0000001: "sym:two" } };
  $("start").hidden = false;
  state.pick = { game: null, language: null };
  await lookup("sym:two", "start");
  assert.equal($("start-lookup-status").textContent, "Choose a game and catalog language for this ID");
  state.pick = { game: "game", language: "es" };
  await lookup("sym:two", "start");
  assert.equal(state.entry.id, "sym:two");
  assert.equal($("start").hidden, true);
  state.edits.set(state.entry, "Local draft", "");
  const back = location.hash;
  await lookup("gen3tl:game/de/term/item:Red%2Fgem");
  assert.equal(state.lang.code, "de");
  assert.equal($("t-id").textContent, "item:Red/gem");
  history.replaceState(null, "", back);
  await route();
  assert.equal(state.lang.code, "es");
  assert.equal(state.entry.id, "sym:two");
  assert.equal(state.edits.get("sym:two").translation, "Local draft");
  await lookup("s0000001");
  assert.equal(state.entry.id, "sym:two");
  assert.match(location.hash, /sym%3Atwo$/);
  await lookup("editor#es/keyboards/0");
  assert.equal($("k-name").textContent, "page/zero %");
  const otherBase = "https://site.test/other/";
  state.sources.set(otherBase, { manifest: { profiles: [{ id: "other", name: "Other game", support: "catalog" }],
    languages: [{ profile: "other", code: "ja", index: "ja/index.json" }] }, releases: [] });
  const { chunks, ...spanish } = await loadIndex(`${base}es/index.json`, fakeFetch(files));
  Object.assign(files, storedLanguage(`${otherBase}ja/index.json`, { ...spanish, chunks, profile: "other", language: "ja" },
    await Promise.all(spanish.list.map((row) => state.chunks.entry(row)))));
  buildGames();
  await lookup("gen3tl:other/ja/text/sym:two");
  assert.equal(state.profile.id, "other");
  assert.equal(state.lang.code, "ja");
  await copyObjectReference("text");
  assert.equal(writes.at(-1), "gen3tl:other/ja/text/sym:two");
  assert.ok(fetcher.calls.every((url) => url.startsWith(base) || url.startsWith(otherBase)));
});

test("naming page routes survive draft reordering and retain numeric positions", async (context) => {
  const { $, state, lookup, route, openWorkspace } = await objectWorkspace(context);
  const named = "#game/es/keyboards/page/page%2Fzero%20%25";
  history.replaceState(null, "", named);
  await openWorkspace("game", "es");
  assert.equal($("k-name").textContent, "page/zero %");
  assert.equal(location.hash, named);
  const original = state.index.keyboard.pages.map((page) => page.name);
  state.keyboardEdits.setPages([...original].reverse(), original);
  await lookup(named);
  assert.equal($("k-name").textContent, "page/zero %");
  assert.equal(location.hash, named);
  await lookup("#es/keyboards/0");
  assert.equal($("k-name").textContent, "page one");
  const positional = location.hash;
  history.replaceState(null, "", named);
  await route();
  assert.equal($("k-name").textContent, "page/zero %");
  history.replaceState(null, "", positional);
  await route();
  assert.equal($("k-name").textContent, "page one");
  history.replaceState(null, "", "#game/es/keyboards/1");
  await openWorkspace("game", "es");
  assert.equal($("k-name").textContent, "page/zero %");
  assert.equal(location.hash, named);
  await lookup("gen3tl:game/es/keyboard-page/page%20one");
  const before = location.hash;
  const selected = $("k-order").children.find((row) => row.querySelector("a").textContent === "page one");
  selected.querySelectorAll("button").find((button) => !button.disabled).listeners.click();
  assert.equal($("k-name").textContent, "page one");
  assert.equal(location.hash, before);
});

test("chat binding routes focus rows without a ROM and restore across views and catalogs", async (context) => {
  const { $, state, lookup, route, openWorkspace, setView } = await objectWorkspace(context);
  const chat = "#game/es/keyboards/chat/chat%2Fupper%20%252F";
  history.replaceState(null, "", chat);
  await openWorkspace("game", "es");
  const input = (index = 0) => $("k-chat").querySelectorAll("input")[index];
  assert.equal(state.view, "keyboards");
  assert.equal($("keyboards").hidden, false);
  assert.equal(document.activeElement, input());
  assert.equal(location.hash, chat);
  assert.equal(state.rom, null);
  input(1).listeners.focus();
  assert.equal(location.hash, "#game/es/keyboards/chat/chat_lower_1");
  await lookup("gen3tl:game/es/chat-row/chat%2Fupper%20%252F");
  assert.equal(document.activeElement, input());
  assert.equal(location.hash, chat);
  setView("texts");
  assert.equal(state.entry.id, "sym:one");
  setView("keyboards");
  assert.equal(location.hash, chat);
  await lookup("sym:two");
  const text = location.hash;
  history.replaceState(null, "", chat);
  await route();
  assert.equal(document.activeElement, input());
  history.replaceState(null, "", text);
  await route();
  assert.equal(state.view, "texts");
  assert.equal(state.entry.id, "sym:two");
  await lookup("gen3tl:game/de/chat-row/chat_lower_1");
  assert.equal(document.activeElement, input(1));
  history.replaceState(null, "", chat);
  await route();
  assert.equal(state.lang.code, "es");
  assert.equal(document.activeElement, input());
  assert.equal(location.hash, chat);
  await lookup("#es/keyboards/chat/chat_lower_1");
  assert.equal(document.activeElement, input(1));
  await lookup("gen3tl:game/es/chat-row/sym:two");
  assert.equal($("workspace-lookup-status").textContent, "Object not found: sym:two");
  assert.equal(location.hash, "#game/es/keyboards/chat/chat_lower_1");
});

test("unknown keyboard targets never select a fallback page", async (context) => {
  const { $, route, openWorkspace } = await objectWorkspace(context);
  for (const kind of ["page", "chat"]) {
    const hash = `#game/es/keyboards/${kind}/missing`;
    history.replaceState(null, "", hash);
    await openWorkspace("game", "es");
    assert.equal($("workspace-lookup-status").textContent, "Object not found: missing");
    assert.equal($("k-name").textContent, "");
    assert.equal(location.hash, hash);
    history.replaceState(null, "", "#game/es/keyboards/page/page%20one");
    await route();
    assert.equal($("k-name").textContent, "page one");
    assert.equal($("workspace-lookup-status").textContent, "");
    history.replaceState(null, "", hash);
    await route();
    assert.equal($("k-name").textContent, "page one");
    assert.equal(location.hash, hash);
  }
});

test("glyph references reveal filtered slots and restore through views, catalogs and Back", async (context) => {
  const { $, state, lookup, route, openWorkspace, setView } = await objectWorkspace(context);
  const glyph = "#game/es/fonts/sheet%2Fone/glyph/BB";
  const selected = () => $("fo-grid").children.find((card) => /\bsel\b/.test(card.className));
  $("fo-filter").value = "pack";
  history.replaceState(null, "", glyph);
  await openWorkspace("game", "es");
  assert.equal(state.rom, null);
  assert.equal(state.view, "fonts");
  assert.equal($("fo-name").textContent, "sheet/one");
  assert.equal($("fo-filter").value, "");
  assert.equal(document.activeElement, selected());
  assert.match(selected().textContent, /BB/);
  assert.equal(selected().getAttribute("tabIndex"), "-1");
  assert.equal($("fo-edit-block").hidden, true);
  assert.equal(location.hash, glyph);
  setView("texts");
  setView("fonts");
  assert.equal(location.hash, glyph);
  await lookup("sym:two");
  history.replaceState(null, "", glyph);
  await route();
  assert.equal(document.activeElement, selected());
  assert.equal(location.hash, glyph);
  $("fo-filter").value = "pack";
  await lookup("gen3tl:game/es/glyph/sheet%20%252F/105");
  assert.equal($("fo-name").textContent, "sheet %2F");
  assert.equal($("workspace-lookup-status").textContent, "Opened sheet %2F/105");
  assert.equal(document.activeElement, selected());
  assert.match(selected().textContent, /105/);
  assert.equal($("fo-edit-block").hidden, false);
  await lookup("gen3tl:game/de/glyph/sheet%2Fone/BB");
  assert.equal(state.lang.code, "de");
  assert.equal(document.activeElement, selected());
  history.replaceState(null, "", glyph);
  await route();
  assert.equal(state.lang.code, "es");
  assert.equal(document.activeElement, selected());
  await lookup("editor.html#es/fonts/sheet%20%252F/glyph/BB");
  assert.equal($("fo-name").textContent, "sheet %2F");
  assert.equal(location.hash, "#game/es/fonts/sheet%20%252F/glyph/BB");
  await lookup("#es/fonts/sheet%2Fone");
  assert.equal($("fo-name").textContent, "sheet/one");
  assert.equal(selected(), undefined);
  assert.equal(location.hash, "#game/es/fonts/sheet%2Fone");
  assert.equal(state.glyphEdits.size, 0);
});

test("missing glyph routes preserve their address without a fallback selection", async (context) => {
  const { $, lookup, route, openWorkspace } = await objectWorkspace(context);
  for (const [sheet, code] of [["missing", "BB"], ["sheet/one", "AA"], ["sheet/one", "0BB"]]) {
    const hash = `#game/es/fonts/${encodeURIComponent(sheet)}/glyph/${code}`;
    const error = `Object not found: ${sheet}/${code}`;
    history.replaceState(null, "", hash);
    await openWorkspace("game", "es");
    assert.equal($("workspace-lookup-status").textContent, error);
    assert.equal($("fo-name").textContent, "");
    assert.equal(location.hash, hash);
    await lookup("gen3tl:game/es/glyph/sheet%2Fone/BB");
    const selection = document.activeElement;
    const valid = location.hash;
    await lookup(hash);
    assert.equal($("workspace-lookup-status").textContent, error);
    assert.equal(location.hash, valid);
    history.replaceState(null, "", hash);
    await route();
    assert.equal($("workspace-lookup-status").textContent, error);
    assert.equal($("fo-name").textContent, "sheet/one");
    assert.ok($("fo-grid").children.includes(selection));
    assert.equal(location.hash, hash);
  }
});

test("lookup errors preserve selections and never replace missing objects with a first match", async (context) => {
  const { $, state, lookup, setView, files, base } = await objectWorkspace(context);
  await lookup("sym:two");
  const hash = location.hash;
  for (const [reference, error] of [
    ["gen3tl:missing/es/text/sym:two", "Unknown game: missing"],
    ["gen3tl:game/missing/text/sym:two", "Catalog unavailable: missing"],
    ["gen3tl:game/it/text/sym:two", "Catalog unavailable: it"],
    ["editor#game/es/fonts/sheet/glyph/BB", "Object not found: sheet/BB"],
    ["gen3tl:game/es/glyph/sheet/BB", "Object not found: sheet/BB"],
    ...["text", "term", "family", "picture", "keyboard-page", "chat-row", "font", "engine-rule"].map((kind) =>
      [`gen3tl:game/es/${kind}/missing`, "Object not found: missing"]),
  ]) {
    await lookup(reference);
    assert.equal($("workspace-lookup-status").textContent, error);
    assert.equal($("workspace-reference").getAttribute("aria-invalid"), "true");
    assert.equal(state.entry.id, "sym:two");
    assert.equal(location.hash, hash);
  }
  await lookup("gen3tl:game/de/font/missing");
  assert.equal($("workspace-lookup-status").textContent, "Object not found: missing");
  assert.equal(state.entry, null);
  for (const view of ["texts", "terms", "families", "pictures", "keyboards", "fonts", "enginerules"]) assert.equal($(view).hidden, true);
  setView("keyboards");
  assert.equal($("k-name").textContent, "page one");
  assert.ok($("k-list").children.length);
  setView("pictures");
  assert.equal($("p-id").textContent, "P/1");
  setView("fonts");
  assert.equal($("fo-name").textContent, "sheet/one");
  await lookup("sym:one");
  assert.equal(state.entry.id, "sym:one");
  assert.equal($("workspace-reference").hasAttribute("aria-invalid"), false);
  delete files[`${base}ru/entries/0.json`];
  await lookup("gen3tl:game/ru/text/sym:two");
  assert.match($("workspace-lookup-status").textContent, /Could not open reference:.*HTTP 404/);
  assert.equal(state.entry, null);
  assert.equal($("copy-text").disabled, true);
});

test("rule source navigation returns through history and empty profiles keep the rule view", async (context) => {
  const { $, state, lookup, route, setView } = await objectWorkspace(context);
  const reference = `gen3tl:${state.profile.id}/${state.lang.code}/engine-rule/ending%2Fgroup`;
  await lookup(reference);
  const back = location.hash;
  $("er-body").querySelectorAll("button").find((button) => button.textContent === "sym:two").listeners.click();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(state.view, "texts");
  assert.equal(state.entry.id, "sym:two");
  assert.equal(history.state.from, "enginerules");
  assert.equal($("go-back").textContent, "Engine language rules");
  history.replaceState(null, "", back);
  await route();
  assert.equal(state.view, "enginerules");
  assert.equal($("er-id").textContent, "ending/group");
  delete state.index.engine_rules;
  state.index.capabilities = { check: "tokens" };
  setView("enginerules");
  assert.equal(state.view, "enginerules");
  assert.equal($("er-empty").textContent, "No rules declared for this game");
  assert.equal($("copy-engine-rule").disabled, true);
  assert.match(location.hash, /\/enginerules$/);
});

test("clipboard denial and absence offer focused selectable text with an accessible status", async (context) => {
  const { $, lookup, copyObjectReference } = await objectWorkspace(context);
  await lookup("sym:two");
  for (const clipboard of [undefined, { writeText: async () => { throw new Error("denied"); } }]) {
    navigator.clipboard = clipboard;
    await copyObjectReference("text");
    assert.equal($("copy-status").textContent, "Copy this reference");
    assert.equal($("copy-fallback").hidden, false);
    assert.equal($("copy-value").value, "gen3tl:game/es/text/sym:two");
    assert.equal(document.activeElement, $("copy-value"));
    assert.equal($("copy-value").selected, true);
  }
  for (const id of ["copy-status", "start-lookup-status", "workspace-lookup-status"]) {
    assert.equal($(id).getAttribute("role"), "status");
    assert.equal($(id).getAttribute("aria-live"), "polite");
  }
});

test("new lookup and Back navigation supersede an unfinished catalog lookup", async (context) => {
  const { $, state, lookup, base, fetcher, route } = await objectWorkspace(context);
  for (const viaBack of [false, true]) {
    let release;
    globalThis.fetch = (url) => url === `${base}de/index.json`
      ? new Promise((resolve) => { release = () => resolve(fetcher(url)); }) : fetcher(url);
    const pending = lookup("gen3tl:game/de/text/sym:one");
    for (let i = 0; i < 20 && !release; i++) await new Promise((resolve) => setImmediate(resolve));
    assert.ok(release);
    assert.equal($("copy-text").disabled, true);
    if (viaBack) {
      history.replaceState(null, "", "#game/es/sym%3Atwo");
      await route();
    } else await lookup("gen3tl:game/es/text/sym:two");
    release();
    await pending;
    assert.equal(state.lang.code, "es");
    assert.equal(state.entry.id, "sym:two");
    assert.equal(location.hash, "#game/es/sym%3Atwo");
  }
});

test("lookup forms submit from the keyboard and messages follow display language", async (context) => {
  const { $, state, wireObjectReferences, copyObjectReference } = await objectWorkspace(context);
  wireObjectReferences();
  let prevented = false;
  $("workspace-reference").value = "sym:two";
  $("workspace-lookup").listeners.submit({ preventDefault() { prevented = true; } });
  for (let i = 0; i < 20; i++) await new Promise((resolve) => setImmediate(resolve));
  assert.equal(prevented, true);
  assert.equal(state.entry.id, "sym:two");
  await copyObjectReference("text");
  await useSiteLanguage("de", { fetcher: fakeFetch({
    "https://site.test/data/site/de.json": { schema: 1, language: "de", strings: {
      "Opened {id}": "Opened DE {id}", "Reference copied": "Copied DE", "Copy reference": "Copy DE",
    } },
  }) });
  assert.equal($("workspace-lookup-status").textContent, "Opened DE sym:two");
  assert.equal($("copy-status").textContent, "Copied DE");
  assert.equal($("copy-text").textContent, "Copy DE");
  assert.equal(state.lang.code, "es");
});

async function referenceWorkspace(context) {
  const page = stubPage(context, "editor", "#game/es");
  const previews = new Map();
  document.getElementById = (id) => page.$(id) ?? previews.get(id) ?? null;
  for (const id of ["pv-en", "pv-tr"]) {
    page.$(id).after = (node) => previews.set(node.id, node);
    page.$(id).getContext = () => ({ clearRect() {}, fillRect() {} });
  }
  const base = "https://site.test/data/";
  const manifest = { profiles: [{ id: "game", name: "Test game", support: "catalog" }],
    languages: ["es", "ru", "de", "fr"].map((code) => ({ profile: "game", code, index: `${code}/index.json` })) };
  const registry = { schema: 1, default: "game", games: [
    { id: "game", name: "Test game", stage: "catalog", languages: ["es", "ru", "de", "fr", "it"], data: base },
    { id: "other", name: "Other game", stage: "catalog", languages: ["ja"], data: "https://site.test/other/" },
  ] };
  const rows = ["one", "two", "empty", "missing"].map((name) => ({ id: `sym:${name}`, status: "approved",
    translation: `Spanish ${name}`, file: "po/test.po", chunk: 0, problems: [],
    context: { speakers: [], events: [], boxes: [], inserted: [], words: [], hosts: [], comments: [] },
    approved: [], rules: [], terms: [], tokens: [], note: "" }));
  const files = {};
  for (const code of ["es", "ru", "de", "fr"]) {
    const list = code === "es" ? rows : [
      { ...rows[1], translation: `${code} second` }, { ...rows[0], translation: "Привет, {PLAYER}!" },
      { ...rows[2], translation: "" },
    ];
    Object.assign(files, storedLanguage(`${base}${code}/index.json`, {
      profile: "game", language: code, codec: CODEC, charmap: PACK,
      fonts: { main: "normal", styles: { normal: STYLE } }, rom: { sha1: "fixture" },
      placeholders: { widths: {}, default: 60 }, list,
    }, list, { profile: "game", rows: {} }));
  }
  const fetcher = fakeFetch(files);
  globalThis.fetch = fetcher;
  setLabelSource(null);
  const source = editorSource()
    .replace(/\nmain\(\);\s*$/, "\nexport { state, buildGames, openWorkspace, openRow, showGameText, showBanner, changeCatalog, changeReferenceLanguage, renderWindow, renderBreakControls, renderContext, renderGlossary, renderProblems, openLookup, copyObjectReference, route, wireObjectReferences, local, setView, runtime, joinedCache, configureCheck, field, onEdit, discardDraft };\n");
  assert.match(source, /\$\("reference-language"\)\.addEventListener\("change", changeReferenceLanguage\)/);
  const editor = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}#${encodeURIComponent(context.name)}`);
  Object.assign(editor.state, { registry, sources: new Map([[base, { manifest, releases: [] }]]), checkStarted: true });
  editor.buildGames();
  await editor.openWorkspace("game", "es");
  const entries = await Promise.all(editor.state.rows.map((row) => editor.state.chunks.entry(row)));
  const choose = async (code) => {
    page.$("reference-language").value = code;
    await editor.changeReferenceLanguage();
  };
  return { ...page, ...editor, base, files, fetcher, choose, entries };
}

test("one accent ROM control and a closed disclosure give way to a plain loaded state", async (context) => {
  const { $, state, showBanner } = await referenceWorkspace(context);
  showBanner();
  assert.equal($("rom-button").textContent, "Load your ROM");
  assert.ok($("rom-button").className.split(" ").includes("primary"));
  assert.equal($("rom-button").hidden, false);
  assert.deepEqual($("banner").querySelectorAll("button"), []);
  const disclosure = $("banner").querySelector("details");
  assert.equal(disclosure.tagName, "DETAILS");
  assert.equal(disclosure.className, "rom-note");
  assert.ok(!disclosure.open);
  assert.equal(disclosure.firstChild.tagName, "SUMMARY");
  assert.equal(disclosure.firstChild.textContent, "Without a ROM, line widths and the check are approximate");
  assert.ok(disclosure.textContent.includes("No ROM loaded: the glyphs of the language pack are exact"));
  assert.ok(disclosure.textContent.includes("The file is read in this browser and stays local."));

  state.rom = { sha1: "fixture", bytes: new Uint8Array(512), name: "fixture.gba" };
  showBanner();
  assert.equal($("rom-button").textContent, "Forget the ROM");
  assert.ok(!$("rom-button").className.split(" ").includes("primary"));
  assert.equal($("banner").querySelector("details"), null);
});

test("only the no-ROM phone workspace scrolls with the document", () => {
  const css = readFileSync(new URL("../site.css", import.meta.url), "utf8");
  assert.match(css, /body\.editor-page:not\(\.in-start\):has\(\.app\.no-rom\) \{ height: auto; overflow: visible; \}/);
  assert.match(css, /\.app\.no-rom \{ height: auto; min-height: 100vh; min-height: 100dvh;/);
  assert.match(css, /\.app\.no-rom \.notices \{ min-width: 0; grid-template-columns: minmax\(0, 1fr\); \}/);
  assert.match(css, /\.app\.no-rom \.panes \{ height: auto; \}/);
  assert.match(css, /\.app\.no-rom \.panes \.pane\.main \{ overflow: visible; \}/);
  const summary = css.match(/\.banner \.rom-note > summary \{[^}]*\}/)[0];
  assert.match(summary, /cursor: pointer;/);
  assert.doesNotMatch(summary, /white-space: nowrap|overflow: hidden|text-overflow: ellipsis/);
  assert.doesNotMatch(html, /style="/);
});

test("entry and glossary cards share structured notes without changing translations", async (context) => {
  const { $, state, openRow, entries } = await referenceWorkspace(context);
  const row = state.rows[0], notes = noteCollection("entry", row.id, state.index.language);
  const term = noteCollection("term", "term:one", state.index.language).note[1];
  term.id = "note:term";
  notes.note.push(term);
  state.ownEnglish = true;
  state.index.notes = notes;
  state.index.research = { "research:one": { table: "terms.tsv", name: "Twin Tap" } };
  state.index.glossary = { "term:one": { source_text: "Term", translation: "Label", note_ids: [term.id], forms: {} } };
  const entry = { ...entries[0], source_text: "Term", note_ids: ["note:shared", "note:wording"], terms: ["term:one"],
    note: "Source projection.", translator_notes: [{ text: "Guidance projection.", evidence: "Projection." }] };
  const before = structuredClone(entry);
  state.chunks = { entry: async () => structuredClone(entry) };
  await openRow(0);
  assert.equal($("pv-en").getAttribute("aria-label"), "English: Term");
  assert.equal($("pv-tr").getAttribute("aria-label"), "Translation: Spanish one");
  for (const text of ["Guidance", "Facts", "Reason", "Sources", "Evidence", "Review: Proposed", "Related attestation."]) {
    assert.ok($("note").textContent.includes(text), text);
  }
  assert.ok(!$("note").textContent.includes("Source projection."));
  assert.ok(!$("note").textContent.includes("Guidance projection."));
  assert.ok($("glossary").textContent.includes("Proposed wording."));
  assert.ok($("glossary").textContent.includes("Review: Proposed"));
  assert.ok($("note").textContent.includes("Related note: Research: terms.tsv · Twin Tap · Related attestation."));
  assert.doesNotMatch($("note").textContent + $("glossary").textContent, /\b(?:note|rule|research):\S/);
  assert.deepEqual(entry, before);
  assert.equal(state.edits.get(row.id), undefined);
});

test("entry notes name wording rule subjects by their loaded wording", async (context) => {
  const { $, state, openRow, entries } = await referenceWorkspace(context);
  const notes = noteCollection("wording_rule", "rule:one", state.index.language);
  state.index.notes = notes;
  state.chunks = { entry: async (row) => ({ ...entries[state.byId.get(row.id)], note_ids: ["note:wording"] }) };
  await openRow(0);
  assert.ok($("note").textContent.includes("Subject: Wording rule: Preserve the placeholder."));
  assert.doesNotMatch($("note").textContent, /\b(?:note|rule):\S/);
});

test("never-shown entries collapse long reach evidence and omit its catalog duplicate", async (context) => {
  const { $, state, openRow, entries } = await referenceWorkspace(context);
  const lines = Array.from({ length: 23 }, (_, i) => `Finding ${i + 1}: the branch cannot display this entry.`);
  for (const evidence of [lines.join("\n"), lines.join(" ")]) {
    const entry = { ...entries[0], reach: { class: "never shown", source: "review", evidence },
      rules: [{ text: "Keep untranslated.", source: "review", evidence },
        { text: "Keep the placeholder.", source: "review", evidence: "Independent rule evidence." }],
      context: { ...entries[0].context, comments: [`reach: never shown (${evidence})`, "Independent catalog comment."] } };
    const before = structuredClone(entry);
    state.chunks = { entry: async () => structuredClone(entry) };
    await openRow(0);
    const disclosure = $("context").querySelectorAll("details").find((node) => node.className.split(" ").includes("reach-evidence"));
    assert.ok(disclosure);
    assert.ok(!disclosure.open);
    assert.ok(disclosure.firstChild.textContent.startsWith(`Evidence: ${lines[0]}`));
    assert.ok(disclosure.firstChild.textContent.length < 100);
    assert.equal(disclosure.childNodes[1].textContent, evidence);
    assert.equal($("context").textContent.split(evidence).length, 2);
    assert.ok($("context").textContent.includes("Reach: never shown (review)"));
    assert.doesNotMatch($("note").textContent, /Finding \d/);
    assert.doesNotMatch($("rules").textContent, /Finding \d/);
    assert.ok($("rules").textContent.includes("Keep untranslated."));
    assert.ok($("rules").textContent.includes("Independent rule evidence."));
    assert.ok($("note").textContent.includes("PO comments (1)"));
    assert.ok($("note").textContent.includes("Independent catalog comment."));
    const descendants = (node) => [node, ...node.children.flatMap(descendants)];
    assert.ok(!descendants($("context")).some((node) => node.tagName === "STYLE" || node.getAttribute?.("style")));
    assert.deepEqual(entry, before);
  }
});

test("context disclosures share a shrinkable summary child and retain unbroken evidence", async (context) => {
  const { $, state, openRow, entries } = await referenceWorkspace(context);
  const address = `https://example.test/${"address".repeat(50)}`;
  const bytes = "0123456789ABCDEF".repeat(40);
  const evidence = `Study trace: ${address}\nBytes: ${bytes}`;
  const comment = `Catalog trace: ${address}\n${bytes}`;
  const ruleEvidence = `Rule trace: ${address}\n${bytes}`;
  const windowEvidence = `Window trace: ${address}\n${bytes}`;
  const entry = { ...entries[0], reach: { class: "never shown", source: "reach study, review", evidence },
    context: { ...entries[0].context, comments: [comment] },
    rules: [{ text: "Keep the placeholder.", source: "review", evidence: ruleEvidence }],
    window: { kind: "field", width: 80, font: "normal",
      sources: { width: { kind: "measured", rule: "Synthetic width measurement", evidence: windowEvidence } } } };
  state.chunks = { entry: async () => structuredClone(entry) };
  await openRow(0);
  assert.match(html, /<aside class="pane ctx"/);
  assert.match($("context").firstChild.className, /\bctx-item\b/);
  assert.ok($("context").textContent.includes("Reach: never shown (reach study, review)"));
  const descendants = (node) => [node, ...node.children.flatMap(descendants)];
  const sections = [...html.matchAll(/<section class="ctx-sec">[^]*?<\/section>/g)].map((match) => match[0]);
  for (const [id, body] of [["context", evidence], ["note", comment], ["rules", ruleEvidence], ["window", windowEvidence]]) {
    assert.ok(sections.some((section) => section.includes(`id="${id}"`)), id);
    const disclosures = $(id).querySelectorAll("details").filter((node) => node.className.split(" ").includes("ctx-disclosure"));
    assert.equal(disclosures.length, 1, id);
    const [disclosure] = disclosures;
    const summary = disclosure.firstChild;
    assert.equal(summary.tagName, "SUMMARY");
    assert.equal(summary.className, "ctx-summary");
    assert.equal(summary.childNodes.length, 1);
    assert.equal(summary.firstChild.tagName, "SPAN");
    assert.equal(summary.firstChild.className, "ctx-summary-text");
    assert.equal(summary.firstChild.textContent, summary.textContent);
    assert.ok(!disclosure.open);
    disclosure.open = true;
    assert.equal(disclosure.childNodes[1].textContent, body);
    assert.ok(!descendants($(id)).some((node) => node.tagName === "STYLE" || node.getAttribute?.("style")));
  }
});

test("rule sources and slot widths use words, one unit and unbroken measurements", async (context) => {
  const { $, state, openRow, renderWindow, entries } = await referenceWorkspace(context);
  const where = "battle text, name of the opposing trainer";
  const entry = { ...entries[0],
    rules: [{ text: "The code fills {STR_VAR_1} with text.", source: { code: "rule_code_fill_where", name: "fill-20", where },
      evidence: "Measured code fill." },
    { text: "The [[strings.pointer_table]] binds this text.", source: { code: "rule_pointer_table", table: "credits" }, evidence: "" }],
    window: { kind: "single", width: 119, font: "normal",
      sources: { width: { kind: "profile", rule: { code: "layout_window_where", name: "main", where: "battle text" }, evidence: "" } },
      slot_details: { text: { source: "fill-20", label: "Inserted text", state: "computed", computed: 59, width: 59,
        contributors: [{ label: "Source text", width: 59 }], winners: [] } } } };
  state.chunks = { entry: async () => structuredClone(entry) };
  context.after(() => { setPlaces(null); setCatalog(null); setUiLanguage("en"); });
  setPlaces({ [where]: { ru: "боевой текст, имя тренера противника" } });
  await openRow(0);
  const sources = $("rules").querySelectorAll(".source");
  assert.deepEqual(sources.map((node) => node.textContent), [where, "Pointer table"]);
  assert.ok(!$("rules").querySelector("li").firstChild.textContent.includes("[["));
  const disclosure = $("rules").querySelector("details");
  assert.ok(!disclosure.open);
  assert.ok(disclosure.textContent.includes("layout.code_fill fill-20"));
  assert.ok(disclosure.textContent.includes("Measured code fill."));
  assert.ok($("window").textContent.includes("Inserted text: 59\u00a0px"));
  assert.ok($("window").textContent.includes("Source text — 59\u00a0px"));
  assert.doesNotMatch($("window").textContent, /\d (?:px|пкс)/);
  setUiLanguage("ru");
  setCatalog({ px: "px", "Inserted text": "Подставляемый текст", "Source text": "Исходный текст",
    "{name}: {width} px": "{name}: {width} px", computed: "вычислено" });
  renderWindow(state.entry);
  assert.ok($("window").textContent.includes("Подставляемый текст: 59\u00a0px"));
  assert.ok($("window").textContent.includes("Исходный текст — 59\u00a0px"));
  assert.ok(!$("window").textContent.includes("пкс"));
});

test("context styles constrain grid widths, shorten summaries and wrap opened text", () => {
  const css = readFileSync(new URL("../site.css", import.meta.url), "utf8");
  const rule = (selector) => css.split("\n").find((line) => line.startsWith(`${selector} {`));
  assert.match(rule(".ctx-sec"), /grid-template-columns: minmax\(0, 1fr\);/);
  assert.match(rule(".ctx-sec"), /overflow-wrap: anywhere;/);
  assert.match(rule(".ctx-sec > *"), /min-width: 0;/);
  assert.match(rule(".ctx-item"), /overflow-wrap: anywhere;/);
  assert.match(rule(".ctx-disclosure"), /min-width: 0;.*overflow-wrap: anywhere;/);
  assert.match(rule(".ctx-summary"), /display: flex;/);
  for (const declaration of ["min-width: 0;", "white-space: nowrap;", "overflow: hidden;", "text-overflow: ellipsis;"]) {
    assert.ok(rule(".ctx-summary-text").includes(declaration), declaration);
  }
  assert.match(rule(".ctx-summary::before"), /flex: none;/);
  assert.ok(rule(".ctx-disclosure[open] > .ctx-summary::before"));
  assert.match(rule(".reach-evidence[open] .reach-preview"), /display: none;/);
  for (const selector of [".reach-text", "pre.comments, .translator-notes .comments", ".rules details div", ".kv details.src div"]) {
    assert.match(rule(selector), /white-space: pre-wrap;/, selector);
    assert.match(rule(selector), /overflow-wrap: anywhere;/, selector);
  }
});

test("never-shown entries keep short reach evidence visible once and preserve other comments", async (context) => {
  const { $, state, openRow, entries } = await referenceWorkspace(context);
  const evidence = "review: The menu bypasses this branch.";
  const entry = { ...entries[0], reach: { class: "never shown", source: "review", evidence },
    rules: [{ text: "Keep untranslated.", source: "review", evidence }],
    context: { ...entries[0].context, comments: [`reach: never shown (${evidence})`] } };
  state.chunks = { entry: async () => structuredClone(entry) };
  await openRow(0);
  assert.equal($("context").querySelectorAll("details").length, 0);
  assert.equal($("context").textContent.split(evidence).length, 2);
  assert.ok(!$("note").textContent.includes(evidence));
  assert.ok(!$("rules").textContent.includes(evidence));
  assert.ok(!$("note").textContent.includes("PO comments"));
  entry.context.comments.push("reach: never shown (Independent evidence.)");
  await openRow(0);
  assert.ok($("note").textContent.includes("Independent evidence."));
  entry.reach.evidence = "";
  await openRow(0);
  assert.ok($("note").textContent.includes(evidence));
  assert.ok($("rules").textContent.includes(evidence));
  assert.ok($("note").textContent.includes("PO comments (2)"));
});

test("entry note drafts survive navigation without changing translation drafts", async (context) => {
  const { $, state, openRow, entries } = await referenceWorkspace(context);
  const rows = structuredClone(state.rows);
  state.chunks = { entry: async (row) => ({ ...entries[state.byId.get(row.id)], source_sha256: "a".repeat(64) }) };
  await openRow(0);
  $("note").querySelectorAll("button").find((node) => node.textContent === "Add note").listeners.click();
  const form = $("note").querySelectorAll("textarea")[0];
  form.value = "Note-only suggestion.";
  form.listeners.input({ target: form });
  assert.equal(state.noteEdits.size, 1);
  assert.equal(state.edits.size, 0);
  assert.equal(state.termEdits.size, 0);
  await openRow(1);
  await openRow(0);
  assert.equal($("note").querySelectorAll("textarea")[0].value, "Note-only suggestion.");
  assert.deepEqual(state.rows, rows);
  $("note").querySelectorAll("button").find((node) => node.textContent === "Revert note draft").listeners.click();
  assert.equal(state.noteEdits.size, 0);
  assert.equal(state.edits.size, 0);
});

test("rule drafts preserve Off, Custom and inheritance until discarded", async (context) => {
  const { $, state, openRow, field, onEdit, discardDraft, entries } = await referenceWorkspace(context);
  state.index.capabilities = { check: "tokens" };
  const row = state.rows[0];
  Object.assign(row, { translation: "", flags: [], status: "untranslated" });
  state.chunks = { entry: async () => ({ ...entries[0], translation: row.translation, flags: row.flags, status: row.status }) };
  await openRow(0);
  assert.equal($("revert").disabled, true);
  assert.match($("history").textContent, /Inherited original/);
  onEdit("", "empty");
  assert.equal($("revert").disabled, false);
  assert.match($("history").textContent, /Off.*included in Export/);
  state.edits = new Edits(state.storage, state.lang.code, state.index.profile);
  await openRow(0);
  assert.equal(field.text, "");
  onEdit(field.text);
  assert.equal(state.edits.get(row.id).state, "empty");
  assert.equal(state.edits.changes()[0].base_state, "original");
  field.set("z");
  onEdit(field.text);
  assert.match($("history").textContent, /Custom.*included in Export/);
  assert.equal(state.edits.get(row.id).state, "text");
  discardDraft();
  assert.equal(field.text, "");
  assert.deepEqual(state.edits.changes(), []);
  assert.equal($("revert").disabled, true);
  Object.assign(row, { flags: ["empty"], status: "approved" });
  await openRow(0);
  assert.match($("history").textContent, /Off.*approved/);
  onEdit("", "original");
  assert.equal(state.edits.get(row.id).base_state, "empty");
  await openRow(0);
  assert.match($("history").textContent, /Inherited original.*included in Export/);
  discardDraft();
  assert.deepEqual(new Edits(state.storage, state.lang.code, state.index.profile).changes(), []);
  assert.match($("history").textContent, /Off.*approved/);
  await new Promise((resolve) => setTimeout(resolve, 220));
});

test("text edits retain historical review context without recreating drafts from missing notes", async (context) => {
  const { state, openRow, onEdit, entries } = await referenceWorkspace(context);
  state.index.capabilities = { check: "tokens" };
  const row = state.rows[0];
  Object.assign(row, { translation: "Published", note: "Current context" });
  state.chunks = { entry: async () => {
    const entry = { ...entries[0], translation: row.translation };
    if ("note" in row) entry.note = row.note;
    else delete entry.note;
    return entry;
  } };
  await openRow(0);
  state.edits.set(state.entry, "Draft", "Historical context");
  onEdit("Updated draft");
  assert.equal(state.edits.get(row.id).note, "Historical context");
  delete row.note;
  await openRow(0);
  onEdit("Another draft");
  assert.equal(state.edits.get(row.id).note, "Historical context");
  onEdit("Published");
  assert.deepEqual(state.edits.changes(), []);
  await new Promise((resolve) => setTimeout(resolve, 220));
});

test("visible glossary rows precede the background join and stale results are ignored", async (context) => {
  const { $, state, runtime, showGameText, entries } = await referenceWorkspace(context);
  $("list").clientHeight = 58;
  const rows = Array.from({ length: 40 }, (_, i) => ({ ...entries[0], id: `sym:${i}`, problems: [] }));
  rows.forEach((row) => delete row.terms);
  state.rows = state.index.list = rows;
  state.entry = null;
  state.index.glossary = { first: { source_text: "First", translation: "First" } };
  const english = { texts: Object.fromEntries(rows.map((row) => [row.id, "First"])), missing: [] };
  state.english = english;
  const expected = joinRows(structuredClone(rows), english.texts, state.index.glossary);
  let finish, calls = 0;
  runtime.join = () => { calls++; return new Promise((resolve) => { finish = resolve; }); };
  showGameText();
  assert.equal(calls, 0);
  assert.equal(state.rows[0].source_text, "First");
  assert.deepEqual(state.rows[0].terms, ["first"]);
  assert.equal(state.rows.at(-1).terms, undefined);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(calls, 1);
  finish(expected);
  await state.joining;
  assert.deepEqual(state.rows, expected);
  state.english = null;
  showGameText();
  state.english = english;
  showGameText();
  await new Promise((resolve) => setTimeout(resolve, 0));
  state.romGeneration++;
  state.english = null;
  showGameText();
  finish(expected);
  await state.joining;
  assert.ok(state.rows.every((row) => !("english" in row) && !("terms" in row)));
});

test("cached text becomes usable before exact configuration without extraction or joining", async (context) => {
  const { state, files, base, runtime, joinedCache, configureCheck } = await referenceWorkspace(context);
  state.entry = null;
  state.rows.forEach((row) => delete row.terms);
  state.index.glossary = { first: { source_text: "First", translation: "First" } };
  const english = { texts: Object.fromEntries(state.rows.map((row) => [row.id, "First"])), missing: [] };
  const expected = joinRows(structuredClone(state.rows), english.texts, state.index.glossary);
  joinedCache.get = async () => joinedValue(english, expected);
  runtime.join = () => assert.fail("A complete cached join needs no matching");
  state.textRom = { sha1: "fixture", bytes: new Uint8Array(), name: "fixture.gba" };
  let finish;
  state.check = { englishFn: true, ready: true, exact: true, source_text: () => assert.fail("Cached text needs no extraction"),
    configure: () => {
      assert.equal(state.romLoading, false);
      assert.deepEqual(state.rows, expected);
      return new Promise((resolve) => { finish = resolve; });
    } };
  const configuring = configureCheck();
  assert.equal(state.check.ready, false);
  assert.equal(state.check.exact, false);
  assert.equal(state.checkState, "loading");
  for (let i = 0; i < 100 && !finish; i++) await new Promise((resolve) => setTimeout(resolve, 1));
  assert.equal(typeof finish, "function");
  finish();
  await configuring;
});

test("context separates participant evidence from English wording in every catalog", async (context) => {
  const { $, state, files, base, renderContext, openWorkspace } = await referenceWorkspace(context);
  const source = (source, gender, value) => ({ source, gender, value, evidence: "scene trace" });
  for (const code of ["es", "de", "fr"]) {
    await openWorkspace("game", code);
    const original = await state.chunks.entry(state.rows[0]);
    const entry = { ...original, context: { ...original.context, events: ["npc"],
      identity: { schema: "2.0.0",
        speaker: { in_source: "not-stated", basis: "", sources: [source("sprite", "female", "7"),
          source("trainer", "female", "9 / 5"), source("text colour", "male", "0")] },
        addressee: { in_source: "stated", basis: "gendered word", sources: [source("sprite", "unknown", "8")] } } } };
    renderContext(entry);
    const shown = $("context").textContent;
    for (const text of ["Speaker gender", "Overworld sprite 7: Female", "Trainer record / class 9 / 5: Female",
      "Text colour 0: Male", "English does not state the gender. Wording stays neutral.", "Addressee gender",
      "Overworld sprite 8: Unknown", "English states the gender: gendered word.", "scene trace"]) {
      assert.ok(shown.includes(text), `${code}: ${text}`);
    }
    entry.context.identity.addressee.in_source = "unknown";
    renderContext(entry);
    assert.ok($("context").textContent.includes("English gender needs review. Wording stays neutral until verified."));
    entry.context.identity.addressee.sources = [source("player", "variable", "")];
    renderContext(entry);
    assert.ok($("context").textContent.includes("Player wording stays neutral."));
    entry.context.identity.addressee.sources = [];
    entry.context.identity.addressee.in_source = "stated";
    renderContext(entry);
    assert.ok($("context").textContent.includes("Addressee gender"));
    assert.ok($("context").textContent.includes("English states the gender: gendered word."));
  }
  setCatalog({ "Speaker gender": "Translated role", "Unknown": "Translated unknown" });
  context.after(() => setCatalog(null));
  const original = await state.chunks.entry(state.rows[0]);
  renderContext({ ...original, context: { ...original.context, events: ["npc"] } });
  assert.ok($("context").textContent.includes("Translated role"));
  assert.ok($("context").textContent.includes("Translated unknown"));
});

test("context reach qualifiers follow the interface catalog", async (context) => {
  const { $, state, renderContext } = await referenceWorkspace(context);
  context.after(() => { setCatalog(null); setUiLanguage(null); });
  const directory = new URL("../i18n/", import.meta.url);
  for (const file of readdirSync(directory).filter((name) => name.endsWith(".po"))) {
    const po = readFileSync(new URL(file, directory), "utf8");
    const catalog = Object.fromEntries([...po.matchAll(/^msgid ("[^\n]*")\nmsgstr ("[^\n]*")$/gm)]
      .map((m) => [JSON.parse(m[1]), JSON.parse(m[2])]));
    setUiLanguage(file.slice(0, -3));
    setCatalog(catalog);
    const study = catalog["reach study"], review = catalog.review, unstudied = catalog["not studied"];
    assert.ok(study && review && unstudied, file);
    const cases = [
      ["reachable", "not studied", unstudied],
      ["possibly reachable", "reach study", study],
      ["never shown", "review", review],
      ["never shown", "reach study, review", `${study}, ${review}`],
      ["never shown", "review, reach study", `${review}, ${study}`],
    ];
    for (const [kind, source, qualifier] of cases) {
      const evidence = "review: caller evidence";
      renderContext({ ...state.entry, reach: { class: kind, source, evidence } });
      assert.equal($("context").firstChild.textContent,
        `${catalog.Reach}: ${catalog[kind]} (${qualifier})${evidence}`, `${file}: ${source}`);
    }
    renderContext({ ...state.entry, reach: undefined });
    assert.equal($("context").firstChild.textContent,
      `${catalog.Reach}: ${catalog.reachable} (${unstudied})`, file);
  }
  setCatalog(null);
  renderContext({ ...state.entry, reach: { class: "never shown", source: "reach study, review", evidence: "" } });
  assert.equal($("context").firstChild.textContent, "Reach: never shown (reach study, review)");
});

test("window facts follow the site language and preserve citations", async (context) => {
  context.after(() => { setCatalog(null); setUiLanguage("en"); setPlaces(null); });
  const { $, state, files, base, openWorkspace, renderWindow } = await referenceWorkspace(context);
  const where = "battle move menu";
  // the project translates its own labels
  files[`${base}es/index.json`].where_texts = { [where]: { ru: "меню приёмов в бою" },
    "Species name": { ru: "Название вида" }, "Item name": { ru: "Название предмета" }, "Move name": { ru: "Название приёма" } };
  await openWorkspace("game", "es");
  const window = { kind: "field", width: 63, lines: 1, font: "normal",
    placeholders: { STR_VAR_1: 60, STR_VAR_2: 50, STR_VAR_3: 40 },
    slot_details: Object.fromEntries(["Species name", "Item name", "Move name"].map((label, i) =>
      [`STR_VAR_${i + 1}`, { width: 60 - i * 10, label, state: "computed", contributors: [
        { id: `word:${i}`, width: 60 - i * 10, original: false }], dependencies: [`word:${i}`] }])),
    sources: { width: { kind: "measured", rule: { code: "table_window_where", name: "move-menu", where },
      evidence: "WindowTemplate at 0x1234, width measured in English" } },
    other: [{ font: "small", width: 60, where }] };
  const po = readFileSync(new URL("../i18n/ru.po", import.meta.url), "utf8");
  const catalog = Object.fromEntries([...po.matchAll(/^msgid ("[^\n]*")\nmsgstr ("[^\n]*")$/gm)]
    .map((m) => [JSON.parse(m[1]), JSON.parse(m[2])]));
  setUiLanguage("ru");
  setCatalog(catalog);
  renderWindow({ window });
  assert.equal(state.lang.code, "es");
  for (const text of ["Название вида", "Название предмета", "Название приёма",
    "меню приёмов в бою", "layout.table_window move-menu", "60\u00a0px", window.sources.width.evidence]) {
    assert.ok($("window").textContent.includes(text), text);
  }
  assert.ok(!$("window").textContent.includes(where));
  const links = (node) => [...(node.tagName === "A" ? [node] : []), ...node.children.flatMap(links)];
  assert.equal(links($("window")).length, 3);
  assert.ok(links($("window")).every((link) => !link.textContent.includes("word:")));
  for (const code of ["en", "es", "de", "fr"]) {
    setUiLanguage(code);
    setCatalog(null);
    renderWindow({ window });
    for (const text of [where, "layout.table_window move-menu", "Species name", "Item name",
      "Move name", window.sources.width.evidence]) assert.ok($("window").textContent.includes(text), text);
  }
  await openWorkspace("game", "de");
  setUiLanguage("ru");
  renderWindow({ window });
  assert.ok($("window").textContent.includes(where));
});

test("window bases use descriptions and disclose profile rows in every interface catalog", async (context) => {
  context.after(() => { setCatalog(null); setUiLanguage("en"); setPlaces(null); });
  const { $, renderWindow } = await referenceWorkspace(context);
  const directory = new URL("../i18n/", import.meta.url);
  const catalogs = [["en", {}], ...readdirSync(directory).filter((name) => name.endsWith(".po")).map((file) => {
    const po = readFileSync(new URL(file, directory), "utf8");
    return [file.slice(0, -3), Object.fromEntries([...po.matchAll(/^msgid ("[^\n]*")\nmsgstr ("[^\n]*")$/gm)]
      .map((m) => [JSON.parse(m[1]), JSON.parse(m[2])]))];
  })];
  const descendants = (node) => [node, ...node.children.flatMap(descendants)];
  const bases = () => descendants($("window")).filter((node) => node.tagName === "DIV"
    && node.className.split(" ").includes("src")).map((node) => node.textContent);
  const visible = (node) => node.tagName === "DETAILS" ? node.firstChild.textContent
    : node.childNodes?.length ? node.childNodes.map(visible).join("") : node.textContent;
  for (const [language, catalog] of catalogs) {
    setUiLanguage(language);
    setCatalog(catalog);
    if (language !== "en") {
      for (const key of ["measured", "two lines per message box", "a one-line field",
        "Table text window", "Record text window", "Name field"]) {
        assert.ok(catalog[key]?.trim(), `${language}: ${key}`);
      }
    }
    const where = "sample menu: text window", translated = `Sample window (${language})`;
    setPlaces({ [where]: { [language]: translated } });
    const menu = { code: "table_window", name: "sample-menu" }, records = { code: "record_window", name: "sample-records" };
    // each window: its described rule, its lines rule, its rules without a description
    for (const [section, name, described, counted, bare, fallback] of [
      ["layout.table_window", "sample-menu", { ...menu, code: "table_window_where", where },
        { code: "box_lines_via", via: menu }, [menu, { ...menu, code: "table_window_where", where: "" }], "Table text window"],
      ["layout.record_window", "sample-records", { ...records, code: "record_window_where", where }, null,
        [records, { ...records, code: "record_window_where", where: "" }], "Record text window"],
      ["table", "sample-names", { code: "table_field_window", table: "sample-names", where },
        { code: "table_one_line", table: "sample-names" },
        [{ code: "table_field_window", table: "sample-names", where: "" }, { code: "table_buffer", table: "sample-names", where: "" }],
        "Name field"],
    ]) {
      const reference = `${section} ${name}`;
      const width = { kind: "measured", rule: described, evidence: "Window trace at 0x100." };
      const lines = { kind: "measured", rule: counted ?? described, evidence: "" };
      const window = { kind: "multiline", width: 200, lines: 2, sources: { width, lines } };
      const before = structuredClone(window);
      renderWindow({ window });
      assert.deepEqual(bases(), [translated, section === "table" ? t("a one-line field")
        : section === "layout.table_window" ? t("two lines per message box") : null].filter(Boolean)
        .map((text) => `${t("measured")}: ${text}`), `${language}: ${section}`);
      assert.ok(!visible($("window")).includes(name));
      assert.doesNotMatch(visible($("window")), /\[\[|\]\]/);
      const disclosures = $("window").querySelectorAll("details");
      assert.equal(disclosures.length, 2);
      for (const disclosure of disclosures) {
        assert.ok(!disclosure.open);
        disclosure.open = true;
        assert.ok(disclosure.textContent.includes(reference));
      }
      assert.ok(disclosures[0].textContent.includes(width.evidence));
      assert.deepEqual(window, before);
      for (const rule of bare) {
        renderWindow({ window: { width: 200, sources: { width: { kind: "measured", rule } } } });
        assert.deepEqual(bases(), [`${t("measured")}: ${t(fallback, { name })}`]);
        assert.doesNotMatch(visible($("window")), /\[\[|\]\]/);
        assert.ok($("window").querySelectorAll("details")[0].textContent.includes(reference));
      }
    }
  }
});

test("workspace references show another catalog by key, persist, and hide with none", async (context) => {
  const { $, storage, state, openWorkspace, openRow, showGameText, fetcher, base, choose } = await referenceWorkspace(context);
  assert.deepEqual($("reference-language").options.map((o) => [o.value, o.textContent]),
    [["", "None"], ["ru", "Russian"], ["de", "German"], ["fr", "French"]]);
  assert.equal($("reference-language").value, "");
  assert.equal($("reference-block").hidden, true);
  assert.equal(fetcher.calls.filter((url) => url.includes("/ru/")).length, 0);
  const list = $("list-space").textContent, row = $("list-space").firstChild;
  const translation = $("field").textContent;
  await choose("ru");
  assert.equal($("reference-block").hidden, false);
  assert.equal($("english-block").hidden, true);
  assert.equal($("reference-label").textContent, "Reference · Russian");
  assert.equal($("reference").textContent, "Привет, PLAYER!");
  assert.equal($("reference").lang, "ru");
  assert.equal($("reference").getAttribute("contenteditable"), "false");
  assert.equal($("reference").getAttribute("aria-readonly"), "true");
  assert.equal($("field").textContent, translation);
  assert.equal($("list-space").textContent, list);
  assert.equal($("list-space").firstChild, row);
  assert.equal(state.edits.size, 0);
  state.english = { texts: { "sym:one": "Hello, {PLAYER}!" } };
  showGameText();
  assert.equal($("english-block").hidden, false);
  assert.equal($("english").textContent, "Hello, PLAYER!");
  assert.equal($("reference").textContent, "Привет, PLAYER!");
  for (const [i, expected] of [[1, "ru second"], [2, "No translation for this entry."], [3, "No translation for this entry."]]) {
    await openRow(i);
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal($("reference").textContent, expected);
  }
  assert.deepEqual(fetcher.calls.filter((url) => url.includes("/ru/")), [`${base}ru/index.json`]);
  await openWorkspace("game", "de");
  assert.equal($("reference-language").value, "");
  assert.equal($("reference-block").hidden, true);
  await openWorkspace("game", "es");
  assert.equal($("reference-language").value, "ru");
  await choose("");
  assert.equal($("reference-block").hidden, true);
  assert.equal($("reference").textContent, "");
  state.english = null;
  showGameText();
  assert.equal($("english-block").hidden, false);
  assert.match($("english").textContent, /ROM/);
  await openWorkspace("game", "es");
  assert.equal($("reference-language").value, "");
  assert.equal($("reference-block").hidden, true);
  const saved = JSON.parse(storage.getItem("gen3tl-editor:workspace:es:game"));
  assert.equal(saved.referenceLanguage, "");
});

test("reference loads cannot replace a newer choice or entry and can retry failures", async (context) => {
  const { $, state, openRow, fetcher, base, files, choose } = await referenceWorkspace(context);
  let release;
  globalThis.fetch = (url) => url === `${base}ru/index.json`
    ? new Promise((resolve) => { release = () => resolve(fetcher(url)); }) : fetcher(url);
  const pending = choose("ru");
  assert.equal($("reference").textContent, "Loading…");
  await openRow(1);
  release();
  await pending;
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal($("reference").textContent, "ru second");
  globalThis.fetch = (url) => url === `${base}de/index.json`
    ? new Promise((resolve) => { release = () => resolve(fetcher(url)); }) : fetcher(url);
  const switched = choose("de");
  await choose("");
  release();
  await switched;
  assert.equal($("reference-block").hidden, true);
  assert.equal($("reference").textContent, "");
  globalThis.fetch = fetcher;
  files[`${base}fr/index.json`].profile = "other";
  await choose("fr");
  assert.equal($("reference").textContent, "Reference translation unavailable.");
  assert.equal($("field").textContent, "Spanish two");
  files[`${base}fr/index.json`].profile = "game";
  await choose("");
  await choose("fr");
  assert.equal($("reference").textContent, "fr second");
  assert.equal(state.edits.size, 0);
});

test("entry sorting preserves selection, drafts, scroll, filters and chips", async (context) => {
  const { $, storage } = stubPage(context, "editor");
  const source = editorSource()
    .replace(/\nmain\(\);\s*$/, "\nexport { state, fillFilters, applyFilters, sortList, listNeverShown };\n");
  const { state, fillFilters, applyFilters, sortList, listNeverShown } = await import(
    `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
  const chunks = ["battle.po", "maps/late.po", "maps/early.po", "system/menu.po", "tables/names.po"]
    .map((file) => ({ file: `translation/po/${file}`, entries: 2 }));
  const rows = [
    { id: "hidden", chunk: 0, reach: "never shown" }, { id: "battle", chunk: 0 },
    { id: "late", chunk: 1, maps: ["late-name"] }, { id: "early", chunk: 2, maps: ["early-name"] },
    { id: "system", chunk: 3 }, { id: "table", chunk: 4 },
    { id: "hidden-map", chunk: 2, maps: ["early-name"], reach: "never shown" },
  ].map((row) => ({ translation: "matching text", status: "approved", kind: "dialogue", problems: [], ...row }));
  Object.assign(state, { rows, index: { chunks, map_order: {
    "translation/po/maps/early.po": 0, "translation/po/maps/late.po": 1 } },
    byId: new Map(rows.map((row, i) => [row.id, i])), groups: groupsOf(chunks),
    edits: new Edits(storage, "ru", "game"), cur: 3, entry: rows[3] });
  state.edits.set({ ...rows[3], note: "" }, "matching draft", "");
  state.edits.map.get("early").problems = [{ message: { code: "line_wide", width: 9, limit: 8, text: "'x'" }, severity: "error" }];
  $("field").textContent = "matching draft";
  $("list-sort").value = "relevance";
  $("list").clientHeight = 58;
  fillFilters();
  applyFilters();
  const listed = () => state.filtered.map((i) => rows[i].id);
  assert.deepEqual(listed(), ["early", "late", "system", "table", "battle", "hidden", "hidden-map"]);
  const chosen = state.entry;
  $("list").scrollTop = 72;
  for (const mode of ["file", "relevance"]) {
    $("list-sort").value = mode;
    sortList();
    assert.equal(state.cur, 3);
    assert.equal(state.entry, chosen);
    assert.equal($("field").textContent, "matching draft");
    assert.equal($("list").scrollTop, 72);
    assert.deepEqual(listed(), mode === "file" ? rows.map((r) => r.id)
      : ["early", "late", "system", "table", "battle", "hidden", "hidden-map"]);
    const rendered = $("list-space").children;
    assert.match(rendered.find((r) => r.getAttribute("data-key") === "early").textContent, /edited.*overflow/);
    assert.equal(rendered.find((r) => r.getAttribute("data-key") === "early").getAttribute("aria-selected"), "true");
    assert.match(rendered.find((r) => r.getAttribute("data-key") === "hidden").textContent, /never shown/);
    $("search").value = "matching";
    $("f-file").value = "g:maps";
    applyFilters();
    assert.deepEqual(listed(), mode === "file" ? ["late", "early", "hidden-map"] : ["early", "late", "hidden-map"]);
    $("f-map").value = "early-name";
    $("f-status").value = "edited";
    $("f-problems").value = "overflow";
    applyFilters();
    assert.deepEqual(listed(), ["early"]);
    for (const id of ["f-file", "f-map", "f-status", "f-problems"]) $(id).value = "";
    listNeverShown();
    assert.deepEqual(listed(), ["hidden", "hidden-map"]);
    $("search").value = "absent";
    applyFilters();
    assert.deepEqual($("list-space").children, []);
    $("search").value = $("f-reach").value = "";
    applyFilters();
  }
});

test("editor bootstrap reserves a routed workspace and preserves the unrouted start screen", () => {
  const startup = readFileSync(new URL("../js/editor-startup.js", import.meta.url), "utf8");
  const boot = (hash) => {
    const attributes = new Set();
    const root = { setAttribute: (name) => attributes.add(name), hasAttribute: (name) => attributes.has(name) };
    runInNewContext(startup, { document: { documentElement: root }, location: { hash } });
    return root.hasAttribute("data-editor-route");
  };
  for (const hash of ["", "#start", "#game"]) assert.equal(boot(hash), false, hash);
  for (const hash of ["#game/es", "#game/es/text/sym%3Aone", "#missing/xx", "#es/sym%3Aone"]) {
    assert.equal(boot(hash), true, hash);
  }
  const css = readFileSync(new URL("../site.css", import.meta.url), "utf8");
  assert.match(css, /html\[data-editor-route\] body\.editor-page #start \{ display: none !important; \}/);
  assert.match(css, /html:not\(\[data-editor-route\]\) body\.editor-page #app \{ display: none; \}/);
  assert.ok(html.indexOf("js/theme.js") < html.indexOf("js/editor-startup.js"));
  assert.ok(html.indexOf("js/editor-startup.js") < html.indexOf('type="module" src="js/editor/editor.js"'));
  assert.match(html, /<div id="start" class="start">/);
  assert.match(html, /<div id="app" class="app">/);
  assert.match(html, /<section id="project-empty" class="banner" aria-live="polite" data-ui="Loading…">Loading…<\/section>/);
});

test("unknown routed games and languages return to the start screen with a message", async (context) => {
  const { $, route } = await referenceWorkspace(context);
  for (const [hash, message] of [
    ["#missing/es", "Unknown game: missing"],
    ["#game/it", "Catalog unavailable: it"],
  ]) {
    history.replaceState(null, "", hash);
    await route();
    assert.equal($("start").hidden, false);
    assert.equal($("app").hidden, true);
    assert.equal(document.documentElement.hasAttribute("data-editor-route"), false);
    assert.equal($("start-error").hidden, false);
    assert.equal($("start-error").textContent, message);
  }
  history.replaceState(null, "", "#game/es");
  await route();
  assert.equal($("start").hidden, true);
  assert.equal($("start-error").hidden, true);
  assert.equal($("start-error").textContent, "");
});

test("display and catalog choices stay independent across start, workspace and deep links", async (context) => {
  const { $, choose, storage } = stubPage(context, "editor", "#start");
  const base = "https://site.test/data/";
  const manifest = { profiles: [{ id: "game", name: "Test game", support: "catalog" }],
    languages: ["ru", "de"].map((code) => ({ profile: "game", code, index: `${code}/index.json` })) };
  const registry = { schema: 1, default: "game", games: [
    { id: "game", name: "Test game", stage: "catalog", languages: ["ru", "de"], data: base },
  ] };
  const files = {
    [`${base}registry.json`]: registry, [`${base}languages.json`]: manifest,
    [`${base}site/catalogs.json`]: { schema: 1, catalogs: [{ code: "ru" }] },
    [`${base}site/ru.json`]: { schema: 1, language: "ru", strings: { "Translation editor": "Editor RU",
      approved: "Approved RU", "All files": "Files RU", "Source note": "Note RU", draft: "Draft RU" } },
  };
  for (const language of ["ru", "de"]) Object.assign(files, storedLanguage(`${base}${language}/index.json`, {
    profile: "game", language, toolkit: "0.127.0", capabilities: { check: "tokens" },
    codec: CODEC, charmap: PACK, fonts: { styles: {} }, list: [], rom: { sha1: "fixture" },
  }));
  globalThis.fetch = fakeFetch(files);
  setLabelSource(null);
  const source = editorSource()
    .replace(/\nmain\(\);\s*$/, "\nexport { state, buildGames, changeCatalog, changeStartCatalog, enterStart, returnToStart, local, openWorkspace, route, followLanguage };\n");
  const { state, buildGames, changeCatalog, changeStartCatalog, enterStart, returnToStart, local, openWorkspace, route, followLanguage } = await import(
    `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
  await startSite({ browser: ["en"] });
  Object.assign(state, { registry, sources: new Map([[base, { manifest, releases: [] }]]), checkStarted: true });
  buildGames();
  context.after(onSiteLanguage(followLanguage));
  const settle = async () => { for (let i = 0; i < 20; i++) await new Promise((resolve) => setImmediate(resolve)); };
  storage.setItem("gen3tl-editor:lang:game", "ru");
  enterStart("game");
  assert.equal($("start-open").disabled, false);
  assert.equal($("start-source").hidden, true);
  assert.deepEqual($("start-lang").options.map((option) => option.value).sort(), ["de", "ru"]);
  assert.equal($("start-lang").value, "ru");
  assert.match($("start-summary").textContent, /Russian/);
  assert.equal(document.documentElement.lang, "en");
  choose("de");
  await settle();
  assert.equal(state.pick.language, "ru");
  assert.match($("start-summary").textContent, /Russian/);
  assert.equal(document.documentElement.lang, "en");
  assert.equal(t("Translation editor"), "Translation editor");
  $("start-lang").value = "de";
  changeStartCatalog();
  assert.equal(state.pick.language, "de");
  assert.equal(chosenLanguage(), "de");
  assert.equal(storage.getItem("gen3tl-editor:lang:game"), "de");
  assert.match($("start-summary").textContent, /German/);
  history.replaceState(null, "", "#game/de");
  await openWorkspace("game", state.pick.language);
  assert.equal(state.index.language, "de");
  assert.equal($("field").lang, "de");
  assert.equal(document.querySelector("title").textContent, "German · Test game · Translation editor");
  assert.equal($("catalog-language").value, "de");
  state.edits.set({ id: "key", translation: "", note: "" }, "German draft", "");
  choose("ru");
  await settle();
  assert.equal(state.loadError, null);
  assert.equal(state.index.language, "de");
  assert.equal(document.documentElement.lang, "ru");
  assert.equal(t("Translation editor"), "Editor RU");
  assert.equal(location.hash, "#game/de");
  assert.equal(state.edits.get("key").translation, "German draft");
  assert.equal(storage.getItem(SITE_LANG_KEY), "ru");
  choose("en");
  await settle();
  assert.equal(state.index.language, "de");
  assert.equal(document.documentElement.lang, "en");
  assert.equal(storage.getItem(SITE_LANG_KEY), "en");
  $("catalog-language").value = "ru";
  await changeCatalog();
  assert.equal(state.index.language, "ru");
  assert.equal(chosenLanguage(), "en");
  assert.equal(document.documentElement.lang, "en");
  assert.equal(location.hash, "#game/ru");
  assert.equal(state.edits.get("key"), undefined);
  assert.equal(storage.getItem("gen3tl-editor:lang:game"), "ru");
  history.replaceState(null, "", "#game/de");
  await route();
  assert.equal(state.index.language, "de");
  assert.equal(chosenLanguage(), "en");
  assert.equal(document.documentElement.lang, "en");
  assert.equal(storage.getItem(SITE_LANG_KEY), "en");
  assert.equal(state.edits.get("key").translation, "German draft");
  history.replaceState(null, "", "#game/en");
  await route();
  assert.equal($("start").hidden, false);
  assert.equal($("app").hidden, true);
  assert.equal($("start-error").textContent, "Catalog unavailable: en");
  assert.equal(location.hash, "#game/en");
  assert.equal(document.querySelector("title").textContent, "Open the editor");
  history.replaceState(null, "", "#game/ru");
  await route();
  assert.equal(state.index.language, "ru");
  assert.equal(chosenLanguage(), "en");
  let release;
  const fetched = globalThis.fetch;
  globalThis.fetch = (url) => url === `${base}de/index.json`
    ? new Promise((resolve) => { release = () => resolve(fetched(url)); }) : fetched(url);
  $("catalog-language").value = "de";
  const pending = changeCatalog();
  await settle();
  assert.ok(release);
  assert.equal(state.index.language, "ru");
  assert.equal(location.hash, "#game/de");
  $("catalog-language").value = "ru";
  const restored = changeCatalog();
  release();
  await Promise.all([pending, restored]);
  assert.equal(state.index.language, "ru");
  assert.equal(location.hash, "#game/ru");
  assert.equal(chosenLanguage(), "en");
  const workspaceHash = location.hash;
  let prevented = false;
  const event = { preventDefault() { prevented = true; } };
  returnToStart({ ...event, ctrlKey: true });
  assert.equal(prevented, false);
  assert.equal(location.hash, workspaceHash);
  local.busy = true;
  returnToStart(event);
  assert.equal(prevented, true);
  assert.equal(location.hash, workspaceHash);
  local.busy = false;
  returnToStart(event);
  assert.equal(location.hash, "#start");
  assert.equal($("start").hidden, false);
  assert.equal($("start-lang").value, "ru");
  history.replaceState(null, "", workspaceHash);
  await route();
  assert.equal($("start").hidden, true);
  assert.equal(state.index.language, "ru");
  returnToStart(event);
  assert.match($("start-summary").textContent, /Russian/);
  assert.equal(document.documentElement.lang, "en");
});

test("catalog changes configure edited graphics before drawing them with an entry open", async (context) => {
  const { $, state, files, base, openWorkspace, openRow, changeCatalog } = await objectWorkspace(context);
  const previousImageData = Object.getOwnPropertyDescriptor(globalThis, "ImageData");
  globalThis.ImageData = class { constructor(data, width, height) { Object.assign(this, { data, width, height }); } };
  context.after(() => {
    if (previousImageData) Object.defineProperty(globalThis, "ImageData", previousImageData);
    else delete globalThis.ImageData;
  });
  for (const code of ["es", "de"]) {
    const index = files[`${base}${code}/index.json`];
    index.keyboard.labels = ["G21", "G21"];
    index.graphics = { inventory: [{ id: "G21", category: "keep", screen: "Keyboard", reason: "", source_text: "", rom_offset: "0x10" }],
      edits: code === "de" ? { G21: { areas: [[0, 0, 1, 1]] } } : {},
      palette: {}, sheet_width: {}, tilemaps: [], previews: { G21: [{}] } };
  }
  let configured = null;
  const failures = [], previews = [];
  const view = { label: "Keyboard", width: 1, height: 1, width_tiles: 1, tiles: 1,
    pixels: new Uint8Array([0]), palette: ["#20242b"] };
  state.check = {
    async configure(_fonts, _latin, _cells, graphics) {
      await new Promise((resolve) => setImmediate(resolve));
      configured = graphics;
    },
  };
  state.gfx = {
    rom: true,
    sheet: async () => view,
    edited: async () => ({ pixels: null, findings: [], png: null }),
    preview: (id, edited) => {
      previews.push([id, edited, configured]);
      if (edited && !configured?.edits?.[id]) {
        failures.push(id);
        throw new Error(`${id} has no edit in the project`);
      }
      return [view];
    },
  };
  await openWorkspace("game", "es");
  for (let i = 0; i < 20 && state.gfxState !== "ready"; i++) await new Promise((resolve) => setImmediate(resolve));
  assert.equal(state.gfxState, "ready");
  await openRow(0);
  assert.ok(state.entry);
  failures.length = previews.length = 0;
  $("catalog-language").value = "de";
  await changeCatalog();
  assert.equal(state.lang.code, "de");
  assert.ok(state.entry);
  assert.deepEqual(failures, []);
  assert.ok(previews.some(([id, edited, graphics]) => id === "G21" && edited && graphics === state.index.graphics));
});

test("language changes retain workspace nodes, drafts, selection and offsets", { skip: NEEDS_DOM }, async (context) => {
  const win = domPage(context, "editor", "#game/ru/table:test%2F20");
  const $ = (id) => document.getElementById(id);
  setLabelSource(null);
  const source = editorSource()
    .replace(/\nmain\(\);\s*$/, "\nexport { state, field, openRow, fillFilters, applyFilters, relabel };\n");
  const { state, field, openRow, fillFilters, applyFilters, relabel } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
  const rows = Array.from({ length: 40 }, (_, i) => ({ id: `table:test/${i + 1}`, status: "approved",
    source_text: "Sample source", translation: "Sample draft", file: "po/test.po", chunk: 0, problems: [],
    context: { speakers: [], events: [], boxes: [], inserted: [], words: [], hosts: [], comments: ["Sample comment"] },
    approved: [], rules: [], terms: [], tokens: [], note: "Sample note" }));
  Object.assign(state, {
    profile: { id: "game", name: "Test game" }, lang: { code: "ru" }, ownEnglish: true,
    index: { profile: "game", language: "ru", capabilities: { check: "tokens" }, list: rows,
      toolkit: "0.127.0", rom: { sha1: "fixture" } },
    rows, byId: new Map(rows.map((row, i) => [row.id, i])), codec: new Codec(CODEC, PACK),
    edits: new Edits(win.localStorage, "ru", "game"), chunks: { entry: async (row) => structuredClone(row) },
    titleLanguage: "ru", checkState: "draft",
  });
  $("start").hidden = true;
  $("app").hidden = false;
  $("texts").hidden = false;
  $("project-empty").hidden = true;
  fillFilters();
  applyFilters();
  await openRow(19);
  field.replace("Edited draft");
  $("list").scrollTop = 406;
  $("list").dispatchEvent(new win.Event("scroll"));
  $("field").focus();
  const text = $("field").firstChild;
  const range = document.createRange();
  range.setStart(text, 1);
  range.setEnd(text, 7);
  document.getSelection().removeAllRanges();
  document.getSelection().addRange(range);
  const details = $("note").querySelector("details");
  details.open = true;
  $("note").scrollTop = 81;
  win.scrollTo(9, 230);
  const offsets = () => [win.scrollX, win.scrollY, $("list").scrollTop, $("note").scrollTop];
  const before = offsets(), row = $("list-space").firstChild;
  const removed = observeRemovals(win);
  const off = onSiteLanguage(relabel);
  context.after(off);
  await useSiteLanguage("de", { fetcher: fakeFetch({
    "https://site.test/data/site/de.json": { schema: 1, language: "de", strings: {
      approved: "Approved DE", "All files": "Files DE", "Source note": "Note DE", draft: "Draft DE",
    } },
  }) });
  assert.equal($("e-id").textContent, "table:test/20");
  assert.match($("e-status").textContent, /Approved DE/);
  assert.equal($("f-file").options[0].textContent, "Files DE");
  assert.equal(field.text, "Edited draft");
  assert.equal(state.edits.get(state.entry.id).translation, "Edited draft");
  assert.equal($("list-space").firstChild, row);
  assert.equal($("field").firstChild, text);
  assert.equal(document.activeElement, $("field"));
  assert.deepEqual(field.selection(), [1, 7]);
  assert.equal(details.open, true);
  assert.equal($("project-empty").hidden, true);
  assert.deepEqual(offsets(), before);
  assert.equal(removed().length, 0);
  context.diagnostic(`offsets ${JSON.stringify({ before, after: offsets() })}`);
  context.diagnostic("removed 0");
});

test("every element the views and modules use exists once", () => {
  assert.equal(new Set(ids).size, ids.length, "duplicate ids");
  const used = new Set();
  for (const file of ["editor/state", "editor/controls", "editor/catalog", "editor/game-text", "editor/status", "editor/context",
    "editor/entry", "editor/texts", "editor/list", "editor/rom", "editor/workspace", "editor/references", "editor/actions", "editor/start",
    "editor/editor", "pictures", "terms", "keyboards", "fonts", "enginerules"]) {
    const src = readFileSync(new URL(`../js/${file}.js`, import.meta.url), "utf8")
      .split("\n").filter((line) => !/^\s*\/\//.test(line)).join("\n"); // ids in comments do not count
    for (const m of src.matchAll(/\$\("([a-z0-9-]+)"\)/g)) used.add(m[1]);
  }
  const made = new Set(["start-file", "pv-en-text", "pv-tr-text"]); // created by the script
  const missing = [...used].filter((id) => !ids.includes(id) && !made.has(id) && !id.includes("${"));
  assert.deepEqual(missing, []);
});

test("catalog selectors are separate from the display-language menu", () => {
  assert.ok(!ids.includes("start-langs") && !ids.includes("crumb-lang"));
  assert.doesNotMatch(html, /id="crumb-|id="version"/);
  assert.match(html, /<a href="#start" id="link-start" aria-current="page" data-ui="Translation editor" data-ui-site="">/);
  assert.ok(!ids.includes("profile") && !ids.includes("lang"));
  for (const id of ["start", "start-games", "start-lang", "catalog-language", "start-drop", "start-browse",
    "start-rom-file", "start-rom-name", "start-replace", "start-replace-cancel", "start-open", "app"]) {
    assert.ok(ids.includes(id), id);
  }
});

test("filters keep their values; tabs, meters, preview note and drawers exist", () => {
  assert.deepEqual(options("list-sort"), ["relevance", "file"]);
  const script = readFileSync(new URL("../js/editor/start.js", import.meta.url), "utf8");
  assert.match(script, /\$\("list-sort"\)\.addEventListener\("change", sortList\)/);
  assert.deepEqual(options("f-status"), ["", "untranslated", "approved", "fuzzy", "nothing", "edited"]);
  assert.deepEqual(options("f-reach"), ["", "reachable", "possibly reachable", "never shown"]);
  assert.deepEqual(options("f-problems"), ["", "overflow", "errors", "warnings"]);
  assert.deepEqual(options("f-terms"), ["", "shortened"]);
  assert.match(html, /<span id="e-reach" class="badge never" hidden data-ui="never shown">never shown<\/span>/);
  const tabs = html.match(/<nav class="tabs"[^]*?<\/nav>/)[0];
  assert.deepEqual([...tabs.matchAll(/<button id="view-([^"]+)"/g)].map((m) => m[1]),
    ["texts", "terms", "families", "pictures", "keyboards", "fonts", "enginerules"]);
  assert.ok(ids.includes("link-pokedex"));
  assert.ok(!ids.includes("view-pokedex"));
  for (const id of ["bars", "clip-chip", "fix-note", "fix-text", "preview-cap", "history", "show-list", "show-ctx",
    "sb-approved", "sb-never", "sb-never-list", "progress", "rom-state", "check-status"]) assert.ok(ids.includes(id), id);
  assert.match(html, /<button id="sb-never-list" type="button" title="List the entries progress leaves out: the game never shows them \(reach study and reviews\)"[^>]*><span data-ui="Never shown">Never shown<\/span> <b id="sb-never">0<\/b><\/button>/);
  assert.match(html, /<section class="ctx-sec"><h2 class="ctx-title"><span data-ui="Rules">Rules<\/span> <span id="rules-note" class="muted"><\/span><\/h2><ul id="rules" class="rules"><\/ul><\/section>/);
  assert.doesNotMatch(html, /style="/);
});

test("the back control leads the view tabs, hidden until a place is opened from another view", () => {
  const tabs = html.match(/<nav class="tabs"[^]*?<\/nav>/)[0];
  assert.match(tabs, /^<nav class="tabs"[^>]*>\s*<span class="tabs-back"><button id="go-back" class="btn sm go-back" type="button" hidden><\/button><\/span>\s*<button id="view-texts"/);
  const src = readFileSync(new URL("../js/editor/texts.js", import.meta.url), "utf8");
  const opens = [...src.matchAll(/\b(openEntry|openFamily|openTerm|openPicture): \(id\) => ([^\n]*)/g)]; // the views' hosts
  assert.deepEqual(opens.map((m) => m[1]), ["openEntry", "openFamily", "openTerm", "openEntry", "openPicture", "openEntry"]);
  for (const m of opens) assert.match(m[2], /^openPlace\(\{ view: "[a-z]+", id \}\),$/, m[1]);
  assert.doesNotMatch(src, /history\.replaceState\(null, "", (?:projectHash|entryHash)/); // the way back survives
});

test("the context panel resolves map and speaker keys through englishOf", () => {
  const src = readFileSync(new URL("../js/editor/texts.js", import.meta.url), "utf8");
  const context = src.match(/function renderContext\(entry\) \{[\s\S]*?\n\}/)?.[0];
  assert.ok(context, "renderContext present");
  assert.match(context, /s\.name \? englishOf\(texts, s\.name\) : null/);
  assert.match(context, /c\.maps\?\.length\) item\(t\("Maps"\), c\.maps\.map\(\(k\) => englishOf\(texts, k\)\)/);
});

test("the meters, their notes and the problems share one font size; the report keeps its own", () => {
  const css = readFileSync(new URL("../site.css", import.meta.url), "utf8");
  const rule = (selector) => css.match(new RegExp(`^${selector.replace(/\./g, "\\.")} \\{[^}]*\\}`, "m"))[0];
  const size = (selector) => rule(selector).match(/font-size: ([^;]+);/)?.[1];
  assert.equal(size(".lines"), "13px"); // the meters and the counts line
  assert.equal(size(".small"), "13px"); // the width note
  assert.equal(size(".problems"), "13px");
  assert.equal(size(".problems li"), "inherit"); // error entries too
  assert.equal(size(".lines .ln-box"), undefined); // the layout note
  assert.equal(size(".report .problems"), "12px");
  assert.match(html, /<div id="bars" class="lines"[^>]*><\/div>\s*<p id="lines-note" class="small muted"><\/p>/);
  assert.match(html, /<div class="lbl"><span id="problems-head" data-ui="Problems">Problems<\/span><\/div>/);
});

test("workspace list rows are scoped: other pages use .row too", () => {
  const css = readFileSync(new URL("../site.css", import.meta.url), "utf8");
  assert.doesNotMatch(css, /^\.row\b/m); // the landing's More games rows broke on it
  assert.match(css, /^\.app \.row \{ position: absolute;/m);
});

test("verified ROM card replaces the dropzone and failed replacement keeps the source", async (context) => {
  const { $ } = stubPage(context, "editor", "#start");
  const bytes = new Uint8Array(Array.from({ length: 256 }, (_, i) => i));
  const sha1 = createHash("sha1").update(bytes).digest("hex");
  const base = "https://site.test/data/";
  const manifest = { profiles: [{ id: "game", name: "Test game", support: "translation", sha1 }],
    languages: [{ profile: "game", code: "ru", index: "ru/index.json" }] };
  const registry = { schema: 1, default: "game", games: [
    { id: "game", name: "Test game", stage: "translation", languages: ["ru"], data: base },
  ] };
  setLabelSource(null);
  const source = editorSource()
    .replace(/\nmain\(\);\s*$/, "\nexport { state, buildGames, pickGame, renderStart, startRom };\n");
  const { state, buildGames, pickGame, renderStart, startRom } = await import(
    `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
  Object.assign(state, { registry, sources: new Map([[base, { manifest, releases: [] }]]) });
  buildGames();
  pickGame("game");
  assert.equal($("start-open").disabled, false);
  assert.equal($("start-drop").hidden, false);
  assert.equal($("start-rom-file").hidden, true);
  const file = (name, data) => ({ name, arrayBuffer: async () => data.buffer });
  await startRom(file("original.gba", bytes));
  const verified = state.textRom;
  assert.equal(verified.sha1, sha1);
  assert.equal($("start-rom-file").hidden, false);
  assert.equal($("start-rom-name").textContent, "original.gba");
  assert.equal($("start-drop").hidden, true);
  assert.equal($("start-rom-options").hidden, true);
  assert.match($("start-summary").textContent, /with your ROM/);
  state.startReplacing = true;
  renderStart();
  assert.equal($("start-drop").hidden, false);
  assert.equal($("start-replace-cancel").hidden, false);
  await startRom(file("bad.gba", new Uint8Array([1, 2, 3])));
  assert.equal(state.textRom, verified);
  assert.equal(state.rom, verified);
  assert.equal($("start-rom-file").hidden, false);
  assert.equal($("start-drop").hidden, false);
  assert.equal($("start-rom-error").hidden, false);
  assert.match($("start-rom-error").textContent, /matches neither the original/);
  assert.equal($("start-replace-cancel").hidden, false);
});


test("window panel identifies unconfirmed default arrows per consumer", async (context) => {
  context.after(() => { setCatalog(null); setUiLanguage("en"); });
  const { $, renderWindow } = await referenceWorkspace(context);
  const consumers = [
    { name: "standard message", width: 224, arrow: 10, arrow_source: "default", arrow_confirmed: true },
    { name: "secondary menu", width: 176, arrow: 10, arrow_source: "default", arrow_confirmed: false },
    { name: "bitmap hint", width: 208, arrow: 0, arrow_source: "declared", arrow_confirmed: true },
  ];
  renderWindow({ window: { kind: "multiline", width: 176, lines: 2, arrow: 10, consumers } });
  assert.match($("window").textContent, /secondary menu: arrow 10\u00a0px/);
  assert.match($("window").textContent, /bitmap hint: arrow 0\u00a0px/);
  assert.equal($("window").textContent.split("Default prompt arrow; not confirmed in this window in the game.").length, 2);
  const disclosures = $("window").querySelectorAll("details");
  assert.equal(disclosures.length, 1);
  assert.ok(!disclosures[0].open);
  assert.match(disclosures[0].textContent, /not confirmed/);
  assert.doesNotMatch(disclosures[0].firstChild.textContent, /not confirmed/);
  setCatalog({ "Default prompt arrow; not confirmed in this window in the game.": "Default pending measurement." });
  renderWindow({ window: { kind: "multiline", width: 176, lines: 2, consumers } });
  assert.match($("window").textContent, /Default pending measurement/);
});


test("instant consumers disable scroll and page insertion with a localized reason", async (context) => {
  context.after(() => { setCatalog(null); setUiLanguage("en"); setPlaces(null); });
  const { $, state, renderBreakControls } = await referenceWorkspace(context);
  const buttons = ["\\n", "\\l", "\\p"].map((code) => {
    const button = document.createElement("button");
    button.dataset.break = code;
    button.dataset.uiTitle = `Insert ${code}`;
    return button;
  });
  const query = document.querySelectorAll.bind(document);
  document.querySelectorAll = (selector) => selector === "[data-break]" ? buttons : query(selector);
  const key = "scroll and page codes are not supported in {where}: this window prints instantly and drops the remaining text";
  const instant = { name: "banner window", width: 80, arrow: 10, instant_print: true };
  for (const shared of [false, true]) {
    const consumers = shared ? [{ name: "ordinary window", width: 60, arrow: 10 }, instant] : [instant];
    renderBreakControls({ window: { consumers } });
    assert.deepEqual(buttons.map((button) => button.disabled), [false, true, true]);
    assert.equal(buttons[1].title, key.replace("{where}", "banner window"));
    assert.equal(buttons[2].getAttribute("aria-describedby"), "break-note");
    assert.equal($("break-note").hidden, false);
    assert.match($("break-note").textContent, /banner window.*drops the remaining text/);
  }
  const code = state.index.language;
  setUiLanguage(code);
  setCatalog({ [key]: "{where}: remaining text is lost.", "Insert \\p": "Page" });
  setPlaces({ "banner window": { [code]: "Notice window" } });
  renderBreakControls({ window: { consumers: [instant] } });
  assert.equal(buttons[1].title, "Notice window: remaining text is lost.");
  assert.equal($("break-note").textContent, buttons[1].title);
  for (const entry of [{}, { window: { consumers: [{ ...instant, instant_print: false }] } }]) {
    renderBreakControls(entry);
    assert.deepEqual(buttons.map((button) => button.disabled), [false, false, false]);
    assert.equal(buttons[2].title, "Page");
    assert.equal(buttons[2].getAttribute("aria-describedby"), null);
    assert.equal($("break-note").hidden, true);
  }
  renderBreakControls({ owner: "pack" });
  assert.deepEqual(buttons.map((button) => button.disabled), [true, true, true]);
});

test("a glossary term the check finds missing matches its row, coded problems shown in words", async (context) => {
  const { $, state, renderGlossary, renderProblems } = await referenceWorkspace(context);
  state.index.glossary = { "term:potion": { source_text: "Potion", translation: "Зелье", forms: {} },
    "term:ball": { source_text: "Poké Ball", translation: "Покебол", forms: {} } };
  renderGlossary({ terms: ["term:potion", "term:ball"] });
  const missing = { code: "warning", message: { code: "glossary_missing", term: "'Potion'", translation: "'Зелье'" } };
  renderProblems({ problems: [{ severity: "warning", message: missing },
    { severity: "error", message: "Preserve the source placeholders and control codes." }] });
  // the problem quotes the term as the glossary row keys it
  const [term] = missingTerms([{ message: missing }]);
  assert.deepEqual($("glossary").querySelectorAll(".term").map((node) => node.getAttribute("data-key")),
    [`glossary term ${term.term} -> ${term.translation} `, "glossary term 'Poké Ball' -> 'Покебол' "]);
  assert.deepEqual($("problems").querySelectorAll("li").map((node) => node.textContent),
    ["glossary term 'Potion' -> 'Зелье' not found (warning)", "Preserve the source placeholders and control codes."]);
});

test("one consumer is named once in the panel and two name the binding bar", async (context) => {
  context.after(() => { setCatalog(null); setPlaces(null); setUiLanguage("en"); });
  const { $, renderWindow } = await referenceWorkspace(context);
  const { barOf } = await import("../js/limits.js");
  const rule = { code: "layout_window_where", name: "notice", where: "Notice message window" };
  const source = { kind: "measured", rule, evidence: "Printer observation." };
  const window = { kind: "multiline", width: 80, lines: 2, sources: { width: source, lines: source },
    consumers: [{ name: rule, width: 80, arrow: 10, arrow_confirmed: true, source }] };
  const visible = (node) => node.tagName === "DETAILS" ? node.firstChild.textContent
    : node.childNodes?.length ? node.childNodes.map(visible).join("") : node.textContent;
  renderWindow({ window });
  assert.equal(visible($("window")).split("Notice message window").length, 2);
  assert.doesNotMatch(visible($("window")), /\[|notice:/);
  assert.doesNotMatch(barOf({ width: 60, room: 70 }, window).label, /window/i);
  window.consumers.push({ name: "Second message window", width: 90, arrow: 30 });
  renderWindow({ window });
  assert.match(visible($("window")), /Second message window/);
  assert.match(barOf({ width: 60, room: 60, consumer: "Second message window" }, window).label,
    /window: Second message window/);
});

test("instant windows omit arrow rows while other consumers retain theirs", async (context) => {
  context.after(() => { setCatalog(null); setPlaces(null); setUiLanguage("en"); });
  const { $, renderWindow } = await referenceWorkspace(context);
  const instant = { name: "Instant notice", width: 80, arrow: 10, instant_print: true,
    arrow_source: "default", arrow_confirmed: false };
  const window = { kind: "multiline", width: 80, lines: 2, arrow: 10, consumers: [instant] };
  renderWindow({ window });
  assert.doesNotMatch($("window").textContent, /Prompt arrow|arrow 10|not confirmed/);
  window.consumers.push({ ...instant, name: "Menu list" });
  renderWindow({ window });
  assert.doesNotMatch($("window").textContent, /Prompt arrow|arrow 10|not confirmed/);
  window.consumers[1].instant_print = false;
  renderWindow({ window });
  assert.match($("window").textContent, /Menu list: arrow 10\u00a0px/);
  assert.doesNotMatch($("window").textContent, /Instant notice: arrow/);
});

test("additional instant consumers retain names and disclosed row evidence", async (context) => {
  context.after(() => { setCatalog(null); setPlaces(null); setUiLanguage("en"); });
  const { $, renderWindow } = await referenceWorkspace(context);
  const main = { kind: "measured", rule: { code: "record_window_where", name: "list", where: "Menu list" }, evidence: "List trace." };
  const source = { kind: "measured", rule: { code: "table_window_where", name: "banner", where: "Notice banner" },
    evidence: "Printer trace." };
  renderWindow({ window: { width: 70, sources: { width: main }, consumers: [
    { name: main.rule, width: 70, arrow: 10, source: main },
    { name: source.rule, width: 80, arrow: 10, instant_print: true, source },
  ] } });
  const visible = (node) => node.tagName === "DETAILS" ? node.firstChild.textContent
    : node.childNodes?.length ? node.childNodes.map(visible).join("") : node.textContent;
  assert.match(visible($("window")), /Notice banner: 80\u00a0px/);
  assert.doesNotMatch(visible($("window")), /Notice banner: arrow|layout.table_window|Printer trace/);
  const disclosure = $("window").querySelectorAll("details").find(node => node.textContent.includes("Printer trace."));
  assert.ok(disclosure);
  assert.match(disclosure.textContent, /layout.table_window banner/);
  assert.ok(!disclosure.open);
});

test("routed start rules apply to the editor page only", () => {
  const css = readFileSync(new URL("../site.css", import.meta.url), "utf8");
  const rules = css.split("\n").filter((line) => line.includes("data-editor-route"));
  assert.equal(rules.length, 2);
  for (const rule of rules) assert.match(rule, /body\.editor-page /);
  // other pages carry the same element id
  assert.match(readFileSync(new URL("../pokedex.html", import.meta.url), "utf8"), /<div id="app"/);
  assert.match(html, /<body class="editor-page">/);
});
