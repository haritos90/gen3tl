import { readFileSync } from "node:fs";

import { createRequire } from "node:module";

import { EDITOR_SCHEMA } from "../js/data.js";

let JSDOM = null;

try { ({ JSDOM } = createRequire(import.meta.url)("jsdom")); }
catch (error) { if (error.code !== "MODULE_NOT_FOUND") throw error; }

export const NEEDS_DOM = JSDOM ? false : "jsdom is unavailable";

export function evidenceFile() {
  const data = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jB9kAAAAASUVORK5CYII=";
  const bytes = Uint8Array.from(atob(data), (letter) => letter.charCodeAt(0));
  return { name: "capture.png", size: bytes.length, arrayBuffer: async () => bytes.buffer };
}

export function noteCollection(kind = "entry", id = "sym:one", language = "xx") {
  const subject = { kind, id };
  const binding = ["entry", "graphic"].includes(kind) ? { ...subject,
    ...(kind === "entry" ? { source_sha256: "a".repeat(64) } : {}) } : null;
  return { schema: "3.0.0", note: [{
    id: "note:shared", subject, ...(binding ? { binding } : {}), scope: { kind: "shared" }, status: "unreviewed",
    guidance: ["Preserve the placeholder.", "Keep line groups."], facts: ["The name varies."], reason: ["The slot is shared."],
    sources: [{ kind: "web", name: "Reference", address: "https://example.test/source?q=1&b=2",
      locator: "Section 4", quoted_term: "Exact term", quote: "Exact quotation.\nSecond line.",
      relation: "context", verification: "recorded" }],
    evidence: [{ method: "measurement", verification: "checked",
      locations: [{ kind: "rom_offset", value: 32, label: "Text" }, { kind: "cpu_address", value: 0x08000020 },
        { kind: "symbol", value: "NAME_SLOT" }, { kind: "file", value: "reference.txt" }],
      measurements: [{ name: "Width", value: 12, unit: "px" }, { name: "Occurrences", value: 0, unit: "count" }],
      related_subjects: [{ kind: "entry", id: "sym:two" }],
      captures: [{ address: "https://example.test/capture.png", locator: "Original, 1.0.0", sha256: "b".repeat(64) }] }],
    related_notes: ["note:research"], related_subjects: [{ kind: "research", id: "research:one" }],
  }, {
    id: "note:wording", subject, ...(binding ? { binding } : {}), scope: { kind: "language", language },
    status: "proposed", guidance: ["Proposed wording."], author: "Contributor", created: "2026-11-11",
    updated: "2026-11-11T15:20:00Z", reviewed_by: "Reviewer", reviewed_at: "2026-11-11T16:20:00Z",
  }, {
    id: "note:research", subject: { kind: "research", id: "research:one" }, scope: { kind: "language", language },
    status: "needs_review", facts: ["Related attestation."], related_notes: ["note:shared"],
    sources: [{ kind: "publication", name: "Reference book", address: "isbn:123", retrieved: "2026-10-18",
      verification: "unresolved", relation: "alternative" }],
  }] };
}

export function domPage(context, page, hash = "") {
  const dom = new JSDOM(readFileSync(new URL(`${page}.html`, process.env.GEN3TL_WEB_ROOT_URL), "utf8"), {
    url: `https://site.test/${page === "index" ? "" : page}${hash}`, pretendToBeVisual: true,
  });
  const win = dom.window;
  const keys = ["window", "document", "location", "history", "localStorage", "getComputedStyle", "requestAnimationFrame", "fetch"];
  const saved = keys.map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]);
  for (const key of keys.filter((key) => key !== "fetch")) {
    const value = key === "window" ? win : typeof win[key] === "function" ? win[key].bind(win) : win[key];
    Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
  }
  win.scrollTo = (x, y) => { win.scrollX = x; win.scrollY = y; };
  Object.defineProperty(win.HTMLElement.prototype, "clientHeight", { get: () => 348 });
  win.HTMLCanvasElement.prototype.getContext = () => null;
  context.after(() => {
    win.close();
    for (const [key, descriptor] of saved) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  });
  return win;
}

export function observeRemovals(win) {
  const removed = [];
  const collect = (records) => records.forEach((record) => removed.push(...record.removedNodes));
  const observer = new win.MutationObserver(collect);
  observer.observe(win.document, { childList: true, subtree: true });
  return () => { collect(observer.takeRecords()); observer.disconnect(); return removed; };
}

export const TRAINER_DIFFICULTY = {
  settings: ["difficult", "vanilla", "expert", "insane"],
  default: 0, order: [1, 0, 2, 3], labels: ["Difficult", "Vanilla", "Expert", "Insane"],
};

export const STANDALONE_LABELS = {
  labels: { "Flinch chance": "s0000001@B_ATK_NAME_WITH_PREFIX+", sleep: "s0000002",
    paralysis: "s0000003", burn: "s0000004", poison: "s0000005", Sentence: "s0000001" },
  texts: [
    ["s0000001", "{B_ATK_NAME_WITH_PREFIX} flinched!", "{B_ATK_NAME_WITH_PREFIX} вздрагивает!"],
    ["s0000002", "sleep", "сон"], ["s0000003", "paralysis", "паралич"],
    ["s0000004", "burn", "ожог"], ["s0000005", "poison", "отравление"],
  ],
  languages: [
    { code: "ru", translated: true, labels: ["Вздрагивает", "Сон", "Паралич", "Ожог", "Отравление"] },
    ...["en", "es", "de", "fr"].map((code) => ({ code, translated: false,
      labels: ["Flinched", "Sleep", "Paralysis", "Burn", "Poison"] })),
  ],
};

export const ENCOUNTER_LABELS = [
  { code: "en", headings: ["Location", "Area", "Method and conditions", "Levels", "Chance"],
    times: ["DAY", "MORNING", "evening", "NIGHT"], display: "Day / Morning / Evening / Night" },
  { code: "ru", headings: ["Место", "Ареал", "Способ и условия", "Уровни", "Шанс"],
    times: ["ДЕНЬ", "УТРО", "вечер", "НОЧЬ"], display: "День / Утро / Вечер / Ночь" },
  { code: "es", headings: ["Ubicación", "Zona", "Método y condiciones", "Niveles", "Probabilidad"],
    times: ["DÍA", "MAÑANA", "tarde", "NOCHE"], display: "Día / Mañana / Tarde / Noche" },
  { code: "de", headings: ["Ort", "Gebiet", "Methode und Bedingungen", "Level", "Chance"],
    times: ["TAG", "MORGEN", "abend", "NACHT"], display: "Tag / Morgen / Abend / Nacht" },
  { code: "fr", headings: ["Lieu", "Zone", "Méthode et conditions", "Niveaux", "Probabilité"],
    times: ["JOUR", "MATIN", "soir", "NUIT"], display: "Jour / Matin / Soir / Nuit" },
];

export const LEARNSET_LABELS = [
  { code: "en", headings: ["Level", "Move", "Type", "Category", "Pow.", "Acc.", "PP"], machine: "TM and HM" },
  { code: "ru", headings: ["Повышение уровня", "Приёмы", "Тип", "Категория", "Сила", "Точность", "ОС"], machine: "ТМ/НМ" },
  { code: "es", headings: ["Por nivel", "Moves", "Type", "Categoría", "Power", "Acc", "PP"], machine: "TMs/HMs" },
  { code: "de", headings: ["Mit Level", "Moves", "Type", "Kategorie", "Power", "Acc", "PP"], machine: "TMs/HMs" },
  { code: "fr", headings: ["Par niveau", "Moves", "Type", "Catégorie", "Power", "Acc", "PP"], machine: "TMs/HMs" },
];

export const CODEC = {
  chars: { " ": "00", A: "BB", B: "BC", a: "D5", b: "D6", e: "D9", l: "E0", o: "E3", ".": "AD", "!": "AB", "1": "A2" },
  named: { LV: "34", UP_ARROW: "79" },
  controls: { COLOR: ["01", 1], PAUSE: ["08", 1], PAUSE_UNTIL_PRESS: ["09", 0], JPN: ["15", 0] },
  placeholders: { PLAYER: "01", STR_VAR_1: "02", B_BUFF1: "00", B_ATK_NAME_WITH_PREFIX: "0F" },
  zero: "^\\{(COLOR|HIGHLIGHT|SHADOW|COLOR_HIGHLIGHT_SHADOW|PALETTE|FONT|RESET_FONT|PAUSE|PAUSE_UNTIL_PRESS|" +
    "WAIT_SE|PLAY_BGM|PLAY_SE|PAUSE_MUSIC|RESUME_MUSIC|ESCAPE|SHIFT_RIGHT|SHIFT_DOWN|FILL_WINDOW|CLEAR|SKIP|" +
    "CLEAR_TO|MIN_LETTER_SPACING|FC)\\b",
};

export const PACK = { "Б": "37", "б": "42" };

export const STYLE = {
  format: "16x16",
  pack: { "37": 6, "42": 5, "43": 6 },
  rom: { font: "font0", glyphs: 0x100, widths: 0x40, count: 4 },
};

class Node {
  constructor(doc) {
    this.ownerDocument = doc;
    this.parentNode = null;
  }

  get nextSibling() {
    const k = this.parentNode?.childNodes;
    return k ? k[k.indexOf(this) + 1] ?? null : null;
  }
}

class Text extends Node {
  constructor(doc, data) {
    super(doc);
    this.nodeType = 3;
    this.data = data;
  }

  get textContent() {
    return this.data;
  }
}

class Element extends Node {
  constructor(doc, tag) {
    super(doc);
    this.nodeType = 1;
    this.tagName = tag.toUpperCase();
    this.childNodes = [];
    this.dataset = {};
    this.className = "";
    this.title = "";
    this.contentEditable = "inherit";
  }

  get firstChild() {
    return this.childNodes[0] ?? null;
  }

  append(...kids) {
    for (const k of kids) {
      const n = typeof k === "string" ? new Text(this.ownerDocument, k) : k;
      n.parentNode = this;
      this.childNodes.push(n);
    }
  }

  replaceChildren(...kids) {
    this.childNodes = [];
    this.append(...kids);
  }

  prepend(...kids) {
    this.replaceChildren(...kids, ...this.childNodes);
  }

  get textContent() {
    return this.childNodes.map((c) => c.textContent).join("");
  }

  set textContent(t) {
    this.replaceChildren(t);
  }

  contains(n) {
    for (let x = n; x; x = x.parentNode) if (x === this) return true;
    return false;
  }
}

export function fakeDocument() {
  const doc = {
    createElement: (tag) => new Element(doc, tag),
    createTextNode: (data) => new Text(doc, data),
  };
  return doc;
}

export function stubPage(context, page, hash = "", source = null) {
  const doc = fakeDocument(), create = doc.createElement;
  const matches = (node, selector) => {
    if (selector.startsWith(".")) return node.className.split(" ").includes(selector.slice(1));
    const attr = selector.match(/^(\w+)?\[([\w-]+)(?:="([^"]*)")?\]$/);
    if (attr) {
      const value = attr[2].startsWith("data-")
        ? node.dataset[attr[2].slice(5).replace(/-([a-z])/g, (_, char) => char.toUpperCase())] ?? null
        : node.getAttribute(attr[2]);
      return (!attr[1] || node.tagName === attr[1].toUpperCase()) && value !== null
        && (attr[3] === undefined || value === attr[3]);
    }
    return node.tagName === selector.toUpperCase();
  };
  doc.createElement = (tag) => {
    const node = create(tag), attrs = new Map();
    Object.assign(node, { value: "", hidden: false, disabled: false,
      style: { setProperty(key, value) { this[key] = String(value); }, getPropertyValue(key) { return this[key] ?? ""; } },
      scrollTop: 0, clientHeight: 0,
      listeners: {}, classList: { toggle() {}, add() {}, remove() {} },
      addEventListener(event, fn) { this.listeners[event] = fn; },
      setAttribute(key, value) {
        attrs.set(key, String(value));
        if (key === "class") this.className = String(value);
        if (key.startsWith("data-")) this.dataset[key.slice(5).replace(/-([a-z])/g, (_, char) => char.toUpperCase())] = String(value);
      },
      getAttribute(key) { return key === "class" ? this.className || null : attrs.get(key) ?? null; },
      hasAttribute(key) { return attrs.has(key); },
      removeAttribute(key) {
        attrs.delete(key);
        if (key.startsWith("data-")) delete node.dataset[key.slice(5).replace(/-([a-z])/g, (_, char) => char.toUpperCase())];
      },
      querySelectorAll(selector) { return this.children.flatMap((child) =>
        [...(matches(child, selector) ? [child] : []), ...child.querySelectorAll(selector)]); },
      querySelector(selector) { return this.querySelectorAll(selector)[0] ?? null; },
      closest(selector) { return matches(this, selector) ? this : this.parentNode?.closest?.(selector) ?? null; },
      cloneNode(deep = false) {
        const copy = doc.createElement(tag);
        for (const [key, value] of attrs) copy.setAttribute(key, value);
        copy.namespaceURI = this.namespaceURI;
        if (deep) copy.append(...this.childNodes.map((child) => child.nodeType === 3
          ? doc.createTextNode(child.data) : child.cloneNode(true)));
        return copy;
      },
    });
    Object.defineProperties(node, {
      children: { get: () => node.childNodes.filter((child) => child.nodeType === 1) },
      options: { get: () => node.querySelectorAll("option") },
      attributes: { get: () => [...attrs].map(([name, value]) => ({ name, value })) },
    });
    if (tag === "template") node.content = doc.createElement("fragment");
    return node;
  };
  doc.createElementNS = (namespace, tag) => Object.assign(doc.createElement(tag), { namespaceURI: namespace });
  const nodes = [], ids = new Map();
  const html = source ?? readFileSync(new URL(`${page}.html`, process.env.GEN3TL_WEB_ROOT_URL), "utf8");
  const element = (tag, attributes) => {
    const node = doc.createElement(tag);
    for (const attr of attributes.matchAll(/\s([\w-]+)(?:="([^"]*)")?/g)) {
      const value = attr[2] ?? "";
      node.setAttribute(attr[1], value);
      if (attr[1] === "id") ids.set(value, node);
      if (attr[1].startsWith("data-")) {
        node.dataset[attr[1].slice(5).replace(/-([a-z])/g, (_, char) => char.toUpperCase())] = value;
      }
    }
    node.hidden = /\bhidden\b/.test(attributes);
    return node;
  };
  // Static controls need their parsed descendants.
  const remainder = html.replace(/<(header|footer)\b[^>]*data-site-(?:mast|footer)[^>]*>[^]*?<\/\1>|<template\b[^>]*>[^]*?<\/template>/g, (fragment) => {
    const stack = [];
    for (const token of fragment.match(/<[^>]+>|[^<]+/g)) {
      if (token.startsWith("</")) stack.pop();
      else if (token.startsWith("<")) {
        const [, tag, attributes] = token.match(/^<([\w-]+)([^>]*)>/);
        const node = element(tag, attributes);
        if (tag === "svg" || stack.at(-1)?.namespaceURI === "http://www.w3.org/2000/svg") {
          node.namespaceURI = "http://www.w3.org/2000/svg";
        }
        if (stack.length) stack.at(-1).append(node);
        else nodes.push(node);
        if (!token.endsWith("/>")) stack.push(node.content ?? node);
      } else stack.at(-1)?.append(token);
    }
    return "";
  });
  for (const match of remainder.matchAll(/<([a-z][a-z0-9]*)\b([^>]*)>/g)) {
    nodes.push(element(match[1], match[2]));
  }
  const walk = (node) => [node, ...node.children.flatMap(walk)];
  const all = () => nodes.flatMap(walk);
  Object.assign(doc, { baseURI: "https://site.test/", URL: `https://site.test/${page === "index" ? "" : page}`,
    body: doc.createElement("body"),
    documentElement: doc.createElement("html"), listeners: {},
    getElementById: (id) => ids.get(id) ?? null,
    querySelector: (selector) => nodes.find((node) => node.tagName === selector.toUpperCase()) ?? null,
    querySelectorAll: (selector) => /^\[data-[\w-]+\]$/.test(selector) || selector === "a[href]"
      ? all().filter((node) => matches(node, selector)) : [],
    addEventListener(event, fn) { this.listeners[event] = fn; },
  });
  const location = { hash }, storage = fakeStorage();
  const history = { state: null,
    replaceState(state, _title, hash) { this.state = state; location.hash = hash; },
    pushState(state, title, hash) { this.replaceState(state, title, hash); },
  };
  const values = { document: doc, window: { addEventListener() {} }, location, history,
    localStorage: storage, requestAnimationFrame: (fn) => fn(), fetch: undefined,
    Option: function(text, value) { return Object.assign(doc.createElement("option"), { textContent: text, value }); } };
  const saved = Object.keys(values).map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]);
  for (const [key, value] of Object.entries(values)) Object.defineProperty(globalThis, key,
    { value, configurable: true, writable: true });
  context.after(() => {
    for (const [key, descriptor] of saved) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  });
  return { $: doc.getElementById, storage, choose(code) {
    const button = { dataset: { siteLang: code }, closest: (selector) => selector === "[data-site-lang]" ? button : null };
    doc.listeners.click({ target: button });
  } };
}

// minimal stored languages for page tests
const LIST_ONLY = new Set(["id", "chunk", "status", "translation", "owner", "kind", "reach", "maps", "shortened", "problems",
  "file", "window", "tokens", "source_sha256", "context"]);
const SHARED_CONTEXT = ["comments", "events", "boxes", "uses", "speakers", "hosts", "identity"];
const stored = (value) => JSON.stringify(value);

/**
 * The stored files {url: document} of a language index at `url`.
 * `index.list` rows and `entries` are decoded records, chunked by `index.chunks`;
 * `source` the ROM locations of their English.
 */
export function storedLanguage(url, index, entries = index.list, source = null) {
  const { list = entries, chunks = [{ file: "po/test.po", path: "entries/0.json", entries: list.length }],
    language = "xx", ...fields } = index;
  const tables = { windows: [], limit_sources: [], consumers: [], slot_details: [], rules: [], reach: [] };
  const at = (name, value) => {
    const table = tables[name];
    let n = table.findIndex((row) => stored(row) === stored(value));
    if (n < 0) n = table.push(value) - 1;
    return n;
  };
  const limit = (s) => at("limit_sources", { kind: s.kind, rule: s.rule, ...(s.evidence ? { evidence: s.evidence } : {}),
    ...(s.unverified ? { unverified: true } : {}) });
  const windowOf = (w) => {
    const out = structuredClone(w);
    if (w.sources) out.sources = Object.fromEntries(Object.entries(w.sources).map(([k, s]) => [k, limit(s)]));
    if (w.consumers) out.consumers = w.consumers.map(({ source: s, ...c }) => at("consumers", { ...c, source: limit(s) }));
    if (w.slot_details) out.slot_details = Object.fromEntries(Object.entries(w.slot_details).map(([k, d]) => [k, at("slot_details", d)]));
    return at("windows", out);
  };
  const files = {}, game = [], rows = [];
  const shared = `../shared/${language}.json`;
  let n = 0;
  const descriptors = chunks.map((chunk, c) => {
    const own = [], parts = [];
    for (let i = 0; i < chunk.entries; i++, n++) {
      const row = list[n], e = { ...row, ...entries[n] }, context = e.context ?? {};
      const reach = typeof e.reach === "object" ? e.reach : e.reach && e.reach !== "reachable" ? { class: e.reach } : null;
      const maps = e.maps ?? context.maps ?? [];
      game.push({ id: e.id, ...(reach ? { reach: at("reach", reach) } : {}), ...(maps.length ? { maps } : {}) });
      rows.push({ ...(e.status ? { status: e.status } : {}), ...(e.translation ? { translation: e.translation } : {}),
        ...(e.owner ? { owner: e.owner } : {}), ...(e.window ? { window: windowOf(e.window) } : {}),
        ...(e.problems?.length ? { problems: e.problems } : {}), ...(e.shortened ? { shortened: e.shortened } : {}) });
      const sharedContext = Object.fromEntries(SHARED_CONTEXT.filter((k) => k in context).map((k) => [k, context[k]]));
      parts.push({ ...(e.tokens?.length ? { tokens: e.tokens } : {}), ...(e.source_sha256 ? { source_sha256: e.source_sha256 } : {}),
        ...(Object.keys(sharedContext).length ? { context: sharedContext } : {}) });
      own.push(Object.fromEntries(Object.entries({ ...e, inserted: context.inserted, words: context.words,
        rules: e.rules?.map((r) => at("rules", r)) }).filter(([k, v]) => v !== undefined && !LIST_ONLY.has(k))));
    }
    const part = `../shared/${language}-${c}.json`;
    files[new URL(chunk.path, url).href] = { schema: EDITOR_SCHEMA, entries: own };
    files[new URL(part, url).href] = { schema: EDITOR_SCHEMA, part: "entries", entries: parts };
    return { ...chunk, part };
  });
  const { reach, ...language_tables } = tables;
  files[url] = { schema: EDITOR_SCHEMA, shared, language, ...fields, chunks: descriptors, list: rows, ...language_tables };
  files[new URL(shared, url).href] = { schema: EDITOR_SCHEMA, part: "index", messages: {}, list: game, reach, notes: [],
    ...(source ? { source: `${language}-source.json` } : {}) };
  if (source) files[new URL(`../shared/${language}-source.json`, url).href] = { schema: EDITOR_SCHEMA, part: "source", ...source };
  return files;
}

export function fakeFetch(files) {
  const f = async (url) => {
    f.calls.push(url);
    if (!(url in files)) return { ok: false, status: 404, json: async () => null };
    return { ok: true, status: 200, json: async () => structuredClone(files[url]) };
  };
  f.calls = [];
  return f;
}

export function fakeStorage(broken = false) {
  const m = new Map();
  return {
    map: m,
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => {
      if (broken) throw new Error("QuotaExceededError");
      m.set(k, String(v));
    },
    removeItem: (k) => m.delete(k),
  };
}
