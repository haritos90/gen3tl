import assert from "node:assert/strict";

import { test } from "node:test";

import { fillPage, fragmentOf, gameLabel, setLabelSource, setUiLanguage, t, uiLanguage } from "../js/ui.js";

import { domPage, NEEDS_DOM, observeRemovals } from "./fixture.mjs";

import { chosenLanguage, prepareSiteLanguage, useSiteLanguage } from "../js/ui.js";

import { el, keepPosition, renderChildren, updateChildren } from "../js/dom.js";

test("language preparation commits only the latest complete request", async () => {
  const pending = new Map(), committed = [];
  const off = prepareSiteLanguage((code) => new Promise((resolve) => pending.set(code,
    () => resolve(() => committed.push(code)))));
  const fetcher = async (url) => {
    const code = url.match(/([^/]+)\.json$/)[1];
    return { ok: true, json: async () => ({ schema: 1, language: code, strings: { Ready: code } }) };
  };
  const root = { baseURI: "https://site.test/", querySelectorAll: () => [] };
  try {
    const old = chosenLanguage();
    const first = useSiteLanguage("de", { fetcher, root });
    const last = useSiteLanguage("fr", { fetcher, root });
    assert.equal(chosenLanguage(), old);
    pending.get("fr")();
    await last;
    assert.equal(t("Ready"), "fr");
    pending.get("de")();
    await first;
    assert.deepEqual(committed, ["fr"]);
    assert.equal(chosenLanguage(), "fr");
  } finally {
    off();
    await useSiteLanguage("en", { root });
  }
});

test("failed page texts keep the previous language and content", async () => {
  const root = { baseURI: "https://site.test/", querySelectorAll: () => [] };
  const before = chosenLanguage();
  const off = prepareSiteLanguage(async () => { throw new Error("text fetch failed"); });
  try {
    await assert.rejects(useSiteLanguage("en", { root }), /text fetch failed/);
    assert.equal(chosenLanguage(), before);
  } finally { off(); }
});

test("a language preparer gives its commit step or nothing", async () => {
  const fetcher = async (url) => {
    const code = url.match(/([^/]+)\.json$/)[1];
    return { ok: true, json: async () => ({ schema: 1, language: code, strings: { Ready: code } }) };
  };
  const root = { baseURI: "https://site.test/", querySelectorAll: () => [] };
  const committed = [];
  const offs = [prepareSiteLanguage(async () => {}), prepareSiteLanguage(() => null),
    prepareSiteLanguage(async (code) => () => committed.push(code))];
  try {
    await useSiteLanguage("de", { fetcher, root });
    assert.deepEqual(committed, ["de"]);
    const loadTexts = async () => ({ texts: {} });
    offs.push(prepareSiteLanguage(loadTexts));
    await assert.rejects(useSiteLanguage("fr", { fetcher, root }),
      { name: "TypeError", message: "Language preparer loadTexts: a commit function or nothing expected, got object" });
    assert.deepEqual(committed, ["de"]);
    assert.equal(chosenLanguage(), "de");
    assert.equal(t("Ready"), "de");
  } finally {
    offs.forEach((off) => off());
    await useSiteLanguage("en", { root });
  }
});

test("text updates preserve nodes, controls, focus and scroll", { skip: NEEDS_DOM }, (context) => {
  const win = domPage(context, "editor");
  const parent = document.getElementById("rules");
  const make = (text) => el("li", {}, el("details", {}, el("summary", {}, text), el("button", {}, text)));
  updateChildren(parent, make("Before"));
  const details = parent.querySelector("details"), button = parent.querySelector("button");
  details.open = true;
  button.focus();
  parent.scrollTop = 117;
  win.scrollTo(12, 321);
  const removed = observeRemovals(win);
  keepPosition(() => {
    renderChildren(parent, (draft) => draft.append(make("After")));
    parent.scrollTop = 0;
    win.scrollTo(0, 0);
  });
  assert.equal(document.activeElement, button);
  assert.equal(details.open, true);
  assert.equal(parent.scrollTop, 117);
  assert.deepEqual([win.scrollX, win.scrollY], [12, 321]);
  assert.equal(button.textContent, "After");
  assert.equal(removed().length, 0);
});

test("child updates remove only missing keyed nodes", { skip: NEEDS_DOM }, (context) => {
  domPage(context, "editor");
  const parent = document.getElementById("rules");
  const make = (key) => el("span", { "data-key": key, textContent: key });
  updateChildren(parent, make("status"), make("warning"), make("kind"));
  const status = parent.firstChild, kind = parent.lastChild;
  const removed = observeRemovals(window);
  updateChildren(parent, make("status"), make("kind"));
  assert.equal(parent.firstChild, status);
  assert.equal(parent.lastChild, kind);
  assert.deepEqual(removed().map((node) => node.dataset.key), ["warning"]);
});

test("a keyed child with a new key is replaced, not restyled", { skip: NEEDS_DOM }, (context) => {
  domPage(context, "editor");
  const parent = document.getElementById("rules");
  const icon = (category) => el("span", { class: `dex-cat-icon dex-cat-${category}`, "data-key": `category-${category}`, style: `--icon:url("icons/${category}.png")` });
  updateChildren(parent, icon(0));
  const first = parent.firstChild;
  updateChildren(parent, icon(1));
  assert.notEqual(parent.firstChild, first);
  assert.equal(parent.childNodes.length, 1);
  assert.equal(parent.firstChild.getAttribute("style"), '--icon:url("icons/1.png")');
});

class StyledNode {
  constructor(name, attrs = {}, nodeType = 1) {
    this.nodeType = nodeType; this.nodeName = name; this.tagName = name; this.attrs = new Map(Object.entries(attrs));
    this.childNodes = []; this.parentNode = null; this.writes = [];
    const node = this;
    this.style = { get cssText() { return node.attrs.get("style") ?? ""; }, set cssText(v) { node.writes.push("cssom"); node.attrs.set("style", v); } };
  }
  get attributes() { return [...this.attrs].map(([name, value]) => ({ name, value })); }
  getAttribute(n) { return this.attrs.has(n) ? this.attrs.get(n) : null; }
  hasAttribute(n) { return this.attrs.has(n); }
  setAttribute(n, v) { if (n === "style") this.writes.push("attribute"); this.attrs.set(n, v); }
  removeAttribute(n) { this.attrs.delete(n); }
  get firstChild() { return this.childNodes[0] ?? null; }
  get nextSibling() { const kids = this.parentNode?.childNodes ?? []; return kids[kids.indexOf(this) + 1] ?? null; }
  insertBefore(node, ref) { node.parentNode = this; const at = ref ? this.childNodes.indexOf(ref) : this.childNodes.length; this.childNodes.splice(at, 0, node); return node; }
  remove() { const kids = this.parentNode?.childNodes; if (kids) kids.splice(kids.indexOf(this), 1); this.parentNode = null; }
  cloneNode() { return new StyledNode(this.nodeName, Object.fromEntries(this.attrs)); }
  get ownerDocument() { return { createTextNode: (data) => ({ nodeType: 3, nodeName: "#text", data }) }; }
}

test("a patched style is written through the CSSOM, never the attribute", () => {
  const parent = new StyledNode("DIV");
  const row = (top) => new StyledNode("DIV", { "data-key": "a", style: `top:${top}px` });
  updateChildren(parent, row(0));
  const kept = parent.firstChild;
  updateChildren(parent, row(34));
  assert.equal(parent.firstChild, kept);
  assert.equal(kept.style.cssText, "top:34px");
  assert.deepEqual(kept.writes, ["cssom"]);
  const target = new StyledNode("DIV", { style: "top:0px" });
  renderChildren(target, (draft) => draft.setAttribute("style", "top:68px"));
  assert.equal(target.style.cssText, "top:68px");
  assert.deepEqual(target.writes, ["cssom"]);
});

test("without a label source a key shows itself with its holes filled", () => {
  setLabelSource(null);
  assert.equal(t("Base stats"), "Base stats");
  assert.equal(t("Level {n}", { n: 16 }), "Level 16");
  assert.equal(t("Use {item} while holding {item2}", { item: "A" }), "Use A while holding {item2}");
  assert.equal(setUiLanguage("ru"), "ru");
  assert.equal(uiLanguage(), "ru");
  setUiLanguage("en");
});

test("a game string becomes a label: placeholders take the holes, codes go, the rest follows", () => {
  assert.equal(gameLabel("Location: {STR_VAR_1}", { place: "Route 1" }), "Location: Route 1");
  assert.equal(gameLabel("Location: {STR_VAR_1}"), "Location");
  assert.equal(gameLabel("Power: "), "Power");
  assert.equal(gameLabel("{F8 00}Next"), "Next");
  assert.equal(gameLabel("{COLOR 0F}{SHADOW 0E}Search"), "Search");
  assert.equal(gameLabel("Lv. ", { n: 16 }), "Lv. 16");
  assert.equal(gameLabel("Battle", { n: 2 }), "Battle 2");
  assert.equal(gameLabel("{PK}{MN} List"), "Pokémon List");
  assert.equal(gameLabel("Level-Up\\nMoves"), "Level-Up Moves");
});

test("the label source decides: a game string, a symbol, or the key when unmapped", () => {
  const labels = { "Base stats": { text: "Параметры", game: true }, "Lv {n}": { text: "Ур. ", game: true },
    "Total": { text: "Σ", game: false }, "{shown} of {total} species": { text: "{shown} / {total}", game: false } };
  setLabelSource((key) => labels[key] ?? null);
  assert.equal(t("Base stats"), "Параметры");
  assert.equal(t("Lv {n}", { n: 7 }), "Ур. 7");
  assert.equal(t("Total"), "Σ");
  assert.equal(t("{shown} of {total} species", { shown: 5, total: 10 }), "5 / 10");
  assert.equal(t("Catch rate"), "Catch rate");
  setLabelSource(null);
});

test("fillPage resolves data-ui text, placeholders, titles and labels", () => {
  const node = (dataset, tag = "span") => ({ tagName: tag, dataset, textContent: "", attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } });
  const heading = node({ ui: "Base stats" });
  const search = node({ uiPlaceholder: "Search species", uiLabel: "Search species" }, "input");
  const button = node({ uiTitle: "Shiny colours" }, "button");
  const root = { querySelectorAll: (selector) => ({ "[data-ui]": [heading], "[data-ui-placeholder]": [search],
    "[data-ui-title]": [button], "[data-ui-label]": [search] })[selector] ?? [] };
  setLabelSource((key) => ({ "Base stats": { text: "Параметры", game: true }, "Search species": { text: "Поиск", game: true } })[key] ?? null);
  fillPage(root);
  assert.equal(heading.textContent, "Параметры");
  assert.equal(search.attrs.placeholder, "Поиск");
  assert.equal(search.attrs["aria-label"], "Поиск");
  assert.equal(button.attrs.title, "Shiny colours");
  setLabelSource(null);
  fillPage(root);
  assert.equal(heading.textContent, "Base stats");
});

test("a game sentence becomes a label: first page, battle buffers filled, trailing punctuation dropped", () => {
  assert.equal(gameLabel("It levels up to {LV} {B_COPY_VAR_2}!", { n: 16 }), "It levels up to 16");
  assert.equal(gameLabel("Повысит уровень до {LV} {B_COPY_VAR_2}\\nпри Атаке выше Защиты!", { n: 20 }), "Повысит уровень до 20 при Атаке выше Защиты");
  assert.equal(gameLabel("It levels up when it has high\\nfriendship!\\pYou’ll be able to tell it’s ready."), "It levels up when it has high friendship");
  assert.equal(gameLabel("It levels up to {LV} {B_COPY_VAR_2} between\\n{B_COPY_VAR_3} and {B_PLAYER_MON2_NAME}!", { n: 30, from: 7, to: 9 }), "It levels up to 30 between 7 and 9");
});

test("standalone casing follows game cleanup and preserves later letters", () => {
  assert.equal(gameLabel("{COLOR RED}éCHO!", null, true), "ÉCHO");
  assert.equal(gameLabel("{COLOR RED}éCHO!"), "éCHO");
  assert.equal(gameLabel("HP", null, true), "HP");
  assert.equal(gameLabel("", null, true), "");
});

test("a fragment rule picks the first word, the word before a placeholder or the word after it", () => {
  assert.equal(fragmentOf("PP was restored.{PAUSE_UNTIL_PRESS}", "^"), "PP");
  assert.equal(fragmentOf("ОС восполнены.{PAUSE_UNTIL_PRESS}", "^"), "ОС");
  assert.equal(fragmentOf("{B_BUFF1} Mega Evolved into \\nMega {B_COPY_VAR_3}!", "B_COPY_VAR_3"), "Mega {B_COPY_VAR_3}");
  assert.equal(fragmentOf("{B_BUFF1}:\\nмегаэволюция! Это Мега-{B_COPY_VAR_3}!", "B_COPY_VAR_3"), "Мега-{B_COPY_VAR_3}");
  assert.equal(gameLabel(fragmentOf("{B_BUFF1}:\\nмегаэволюция! Это Мега-{B_COPY_VAR_3}!", "B_COPY_VAR_3"), { species: "Чаризард" }), "Мега-Чаризард");
  assert.equal(fragmentOf("{B_ATK_NAME_WITH_PREFIX} flinched!", "B_ATK_NAME_WITH_PREFIX+"), "flinched");
  assert.equal(fragmentOf("{B_ATK_NAME_WITH_PREFIX} вздрагивает!", "B_ATK_NAME_WITH_PREFIX+"), "вздрагивает");
  assert.equal(fragmentOf("Shiny Charm", "^"), "Shiny");
  assert.equal(fragmentOf("Crater Pokémon Gym\\nLeader: Véga", "~"), "Crater Pokémon Gym");
  assert.equal(fragmentOf("Shadow Warrior Training Area", "~"), "Shadow Warrior Training Area");
  assert.equal(fragmentOf("No placeholder here", "B_COPY_VAR_3"), null);
});
