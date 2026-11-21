// The site's interface strings; every key is its English wording.
// Lookup order: label source, site catalog, then the key.
// The masthead language menu sets interface and game data language.

import { el, keepPosition, setText, updateChildren } from "./dom.js";
import { installSiteFonts } from "./site-fonts.js";
import "./language-tags.js";

export const SOURCE_LANGUAGE = "en";
export const SITE_LANG_KEY = "gen3tl-site-lang";
export const SITE_CATALOG_KEY = "gen3tl-site-catalog";
export const { canonicalLanguage, matchLanguage } = globalThis.gen3tlLanguages;

let current = SOURCE_LANGUAGE;
let source = null;
let catalog = null; // key -> text of the shown language
const holes = new WeakMap(); // node -> the values of its key
const listeners = new Set();
const preparers = new Set();

/** The interface language code, for the document's lang attribute. */
export function setUiLanguage(code) {
  current = code || SOURCE_LANGUAGE;
  return current;
}

export const uiLanguage = () => current;

/** The label source: (key) => {text, game}, or null; null clears. */
export function setLabelSource(resolver) {
  source = resolver;
}

// the game's text variables
const PLACEHOLDER = /\{(?:STR_VAR_\d|B_COPY_VAR_\d|B_BUFF\d|B_PLAYER_MON\d_NAME|B_[A-Z_]*NAME[A-Z_]*|F7 0\d)\}/g;
const CODE = /\{[^}]*\}/g; // every other control code
const BREAK = /\\[nl]/g; // the game's line breaks
const PAGE = /\\p/; // a page break: a label is the first page

/** A game string as a label: first page, codes dropped. */
export function gameLabel(text, vars = null, standalone = false) {
  const values = vars ? Object.values(vars).map(String) : [];
  let used = 0;
  let label = text.split(PAGE)[0].replaceAll("{PK}{MN}", "Pokémon")
    .replace(PLACEHOLDER, () => (used < values.length ? values[used++] : ""))
    .replace(CODE, "").replace(BREAK, " ").replace(/\s+/g, " ").trim()
    .replace(/[:!]\s*$/, ""); // a sentence end, not the dot of "Lv."
  if (standalone) label = label.replace(/^\p{Ll}/u, (letter) => letter.toLocaleUpperCase(current));
  const rest = values.slice(used);
  return rest.length ? `${label} ${rest.join(" ")}`.trim() : label;
}

/** A fragment of a game string by a mapping rule. */
export function fragmentOf(text, rule) {
  const flat = text.split(PAGE)[0].replace(BREAK, " ");
  const bare = (word) => word.replace(/[!.,:;?]+$/, "");
  if (rule === "^") {
    const first = flat.replace(CODE, " ").trim().split(/\s+/)[0];
    return first ? bare(first) : null;
  }
  if (rule === "~") return text.split(PAGE)[0].split(BREAK)[0].replace(CODE, "").trim() || null;
  const after = rule.endsWith("+");
  const name = after ? rule.slice(0, -1) : rule;
  const match = flat.match(after ? new RegExp(`\\{${name}\\}\\s*([^\\s{]+)`) : new RegExp(`(\\S+\\s?)\\{${name}\\}`));
  if (!match) return null;
  return after ? bare(match[1]) : match[0];
}

/** `{name}` holes of a key or symbol, filled by name. */
const fillHoles = (text, vars) => (vars
  ? text.replace(/\{(\w+)\}/g, (hole, name) => (name in vars ? String(vars[name]) : hole)) : text);

/** A key the catalog translates where shown, as gettext's N_. */
export const N_ = (key) => key;

/** The site catalog of the shown language; null shows keys. */
export function setCatalog(strings) {
  catalog = strings && typeof strings === "object" ? strings : null;
}

/** The site catalog's string of a key, else the key. */
export function siteText(key, vars = null) {
  const text = catalog?.[key];
  return fillHoles(typeof text === "string" && text ? text : key, vars);
}

/** The string of a key: game, symbol, catalog or key. */
export function t(key, vars = null) {
  const label = source?.(key) ?? null;
  if (label == null) return siteText(key, vars);
  return label.game ? gameLabel(label.text, vars, label.standalone) : fillHoles(label.text, vars);
}

/** An error whose message is a key. */
export function uiError(key, vars = null) {
  const error = new Error(fillHoles(key, vars));
  error.key = key;
  error.vars = vars;
  return error;
}

/** The text of an error or message, its key translated. */
export function errorText(error) {
  checkSiteBuild();
  if (error?.key) return t(error.key, error.vars);
  return t(String(error?.message ?? error));
}

/** A number in the site language. */
export const formatNumber = (n) => Number(n).toLocaleString(current);

/** A language's name, always in English. */
export function languageName(code) {
  try {
    return new Intl.DisplayNames([SOURCE_LANGUAGE], { type: "language" }).of(code) ?? code;
  } catch {
    return code;
  }
}

// data attribute, dataset key, the attribute it fills
const ATTRIBUTES = [["[data-ui-placeholder]", "uiPlaceholder", "placeholder"], ["[data-ui-title]", "uiTitle", "title"],
  ["[data-ui-label]", "uiLabel", "aria-label"]];

// the masthead and marked elements are the site's
const resolve = (node, key, vars = null) => (node.closest?.("header.mast") || node.dataset?.uiSite !== undefined
  ? siteText(key, vars) : t(key, vars));

/** A text split at its {name} holes, nodes in each. */
export function textParts(text, nodes) {
  const parts = [];
  let last = 0;
  for (const m of text.matchAll(/\{(\w+)\}/g)) {
    if (!(m[1] in nodes)) continue;
    if (m.index > last) parts.push(text.slice(last, m.index));
    parts.push(nodes[m[1]]);
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

// children marked data-ui-hole="name" stand in the key's {name} holes
function fillText(node, text) {
  const kids = [...(node.children ?? [])].filter((child) => child.dataset?.uiHole);
  if (!kids.length) setText(node, text);
  else updateChildren(node, ...textParts(text, Object.fromEntries(kids.map((kid) => [kid.dataset.uiHole, kid]))));
}

/** Show a key in a node; fillPage keeps its language. */
export function showText(node, key, vars = null) {
  node.dataset.ui = key;
  if (vars) holes.set(node, vars);
  else holes.delete(node);
  fillText(node, resolve(node, key, vars));
}

/** Keep dynamic prompts intact across language refills. */
export function showPlaceholder(node, key, vars) {
  node.setAttribute("data-ui-placeholder", key);
  holes.set(node, vars);
  node.setAttribute("placeholder", resolve(node, key, vars));
}

/** Show data in a node that may hold a key. */
export function plainText(node, text) {
  delete node.dataset.ui;
  holes.delete(node);
  setText(node, text);
}

/** Resolve the data-ui keys of a document or element. */
export function fillPage(root = document) {
  for (const node of root.querySelectorAll("[data-ui]")) fillText(node, resolve(node, node.dataset.ui, holes.get(node)));
  for (const [selector, key, attribute] of ATTRIBUTES) {
    for (const node of root.querySelectorAll(selector)) node.setAttribute(attribute, resolve(node, node.dataset[key], holes.get(node)));
  }
  if (root === globalThis.document && document.documentElement) document.documentElement.lang = current;
}


const FORMER_KEYS = ["gen3tl-dex-lang"];
const LANG_CODE = /^[a-z]{2,3}(?:-[A-Za-z0-9]+)?$/;
const SITE_PROJECT = "site"; // the interface catalogs in older languages.json

let chosen = SOURCE_LANGUAGE;
let offered = null; // the menu's languages, once loaded

/** The masthead's choice: game data, and interface with a catalog. */
export const chosenLanguage = () => chosen;

/** This device's storage, or null where the browser refuses it. */
function deviceStorage() {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

/** The remembered choice, or null; a former Pokédex choice counts. */
export function recallSiteLanguage(storage = deviceStorage()) {
  try {
    const own = storage?.getItem(SITE_LANG_KEY);
    if (own) return own;
    for (const key of FORMER_KEYS) {
      const code = storage?.getItem(key);
      if (code && LANG_CODE.test(code)) return code;
    }
    return null;
  } catch {
    return null;
  }
}

/** Remember the choice on this device under its one key. */
export function rememberSiteLanguage(code, storage = deviceStorage()) {
  try {
    storage?.setItem(SITE_LANG_KEY, code);
    for (const key of FORMER_KEYS) storage?.removeItem?.(key);
  } catch {
    // this page only
  }
}

/** The language a page opens in: remembered, browser, English. */
export function preferredLanguage(offered, stored = null, browser = []) {
  return globalThis.gen3tlLanguages.preferredLanguage(offered, stored, browser, SOURCE_LANGUAGE);
}

/** The language codes of the catalogs index, else null. */
export function indexCodes(index) {
  if (index?.schema !== 1 || !Array.isArray(index.catalogs)) return null;
  return index.catalogs.map((c) => c?.code).filter((code) => typeof code === "string");
}

/** The languages of the catalogs index with a translated guide. */
export function indexGuides(index) {
  if (!indexCodes(index) || !Array.isArray(index.guides)) return null;
  return index.guides.filter((code) => typeof code === "string");
}

/**
 * The languages the site serves, by English name.
 * `interface` and `guide` are null when unknown.
 */
export function siteLanguages(registry, manifest, index = null) {
  const rows = Array.isArray(manifest?.languages) ? manifest.languages.filter((l) => l && typeof l === "object") : [];
  const listed = indexCodes(index) ?? (manifest ? rows.filter((l) => l.profile === SITE_PROJECT).map((l) => l.code) : null);
  const catalogs = listed ? new Set(listed) : null;
  const guided = indexGuides(index);
  const guides = guided ? new Set(guided) : null;
  const games = Array.isArray(registry?.games) ? registry.games.flatMap((g) => (Array.isArray(g?.languages) ? g.languages : []))
    : rows.map((l) => l.code);
  const codes = new Set([SOURCE_LANGUAGE, ...(catalogs ?? []), ...games]);
  return [...codes].filter((code) => typeof code === "string" && LANG_CODE.test(code))
    .map((code) => ({ code, name: languageName(code),
      interface: code === SOURCE_LANGUAGE || (catalogs ? catalogs.has(code) : null),
      guide: code === SOURCE_LANGUAGE || (guides ? guides.has(code) : null) }))
    .sort((a, b) => a.name.localeCompare(b.name, SOURCE_LANGUAGE));
}

/** The site's languages from registry, manifest and catalogs index. */
export async function loadSiteLanguages(fetcher = fetch, base = globalThis.document?.baseURI, onRegistry = () => {}) {
  const get = async (name) => {
    try {
      const response = await fetcher(new URL(`data/${name}`, base).href, { credentials: "omit", cache: "no-cache" });
      return response.ok ? await response.json() : null;
    } catch {
      return null;
    }
  };
  const { loadRegistry } = await import("./registry.js");
  const [registry, manifest, index] = await Promise.all([
    loadRegistry(new URL("data/registry.json", base).href, fetcher).catch(() => null), get("languages.json"),
    get("site/catalogs.json")]);
  onRegistry(registry);
  return registry || manifest || index ? siteLanguages(registry, manifest, index) : null;
}

export const catalogURL = (code, base = globalThis.document?.baseURI) => new URL(`data/site/${code}.json`, base).href;

const catalogs = new Map();
// Deployed paths include the toolkit version.
const catalogVersion = new URL(".", import.meta.url).href;

function cachedCatalog(code, storage, base) {
  if (code === SOURCE_LANGUAGE) return null;
  try {
    const saved = JSON.parse(storage?.getItem(SITE_CATALOG_KEY) ?? "null");
    if (saved?.version !== catalogVersion || saved?.url !== catalogURL(code, base)) return null;
    return readCatalog(saved.catalog, code);
  } catch {
    return null;
  }
}

/** The strings of a language, fetched once. */
export async function loadCatalog(code, fetcher = fetch, base = undefined, storage = deviceStorage()) {
  if (code === SOURCE_LANGUAGE) return null;
  const url = catalogURL(code, base);
  if (!catalogs.has(url)) {
    const cached = cachedCatalog(code, storage, base);
    const fresh = (async () => {
      try {
        const early = globalThis.document?.siteStartup?.catalog;
        let response = early?.url === url ? await early.response : null;
        if (!response?.ok) response = await fetcher(url, { credentials: "omit", cache: "no-cache" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const doc = await response.json();
        const strings = readCatalog(doc, code);
        try {
          storage?.setItem(SITE_CATALOG_KEY, JSON.stringify({ version: catalogVersion, url, catalog: doc }));
        } catch { /* The current page needs no persistent storage. */ }
        return strings;
      } catch (e) {
        console.warn(`site catalog ${code}: ${e.message || e}`);
        return null;
      }
    })();
    catalogs.set(url, cached ? Promise.resolve(cached) : fresh);
  }
  return catalogs.get(url);
}

/** The strings of a site catalog document, else an Error. */
export function readCatalog(doc, code) {
  if (!doc || doc.schema !== 1 || doc.language !== code || !doc.strings || typeof doc.strings !== "object"
      || Array.isArray(doc.strings) || Object.values(doc.strings).some((text) => typeof text !== "string")) {
    throw new Error(`site catalog ${code}: unknown schema`);
  }
  return doc.strings;
}

// Guides have one page per language and family.

/** The guide page of a language within its family. */
export const guidePage = (code, family = "guide") => (code === SOURCE_LANGUAGE ? family : `${family}.${code}`);

const GUIDE_HREF = /^((?:save-)?guide)(?:\.[a-z]{2,3}(?:-[A-Za-z0-9]+)?)?(?:\.html)?(?=[?#]|$)/;
const PAGE_HREF = /^(index|patch|editor|save|pokedex|submit|pick|(?:save-)?guide(?:\.[a-z]{2,3}(?:-[A-Za-z0-9]+)?)?)\.html(?=[?#]|$)/;

/** The guide page of a choice: its translation, else English. */
export function guideFor(code, languages = offered, family = "guide") {
  if (code === SOURCE_LANGUAGE) return guidePage(code, family);
  const guide = languages?.find((l) => l.code === code)?.guide;
  return guide == null ? null : guidePage(guide ? code : SOURCE_LANGUAGE, family);
}

/** The guide page a guide in `own` moves to. */
export function guideMove(own, code, languages = offered, family = "guide") {
  const target = guideFor(code, languages, family);
  return target && target !== guidePage(own, family) ? target : null;
}

/** Point guide links at the chosen language's family. */
export function followGuide(root, code) {
  for (const link of root?.querySelectorAll?.("a[href]") ?? []) {
    const href = link.getAttribute("href") ?? "";
    const family = href.match(GUIDE_HREF)?.[1];
    const target = family && guideFor(code, offered, family);
    if (target) link.setAttribute("href", href.replace(GUIDE_HREF, target));
  }
}

/** Notify listeners after each language switch.
 * Pass displayed and chosen languages; return the unsubscribe function. */
export function onSiteLanguage(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Load page texts before committing a language switch. */
export function prepareSiteLanguage(prepare) {
  preparers.add(prepare);
  return () => preparers.delete(prepare);
}


/** Show a choice: the menu's label and pressed item. */
function markChoice(root, code) {
  if (!root?.querySelectorAll) return;
  const name = offered?.find((l) => l.code === code)?.name ?? languageName(code);
  for (const node of root.querySelectorAll("[data-lang-name]")) setText(node, name);
  for (const node of root.querySelectorAll("[data-lang-code]")) setText(node, code.toUpperCase());
  for (const button of root.querySelectorAll("[data-site-lang]")) {
    button.setAttribute("aria-pressed", String(button.dataset.siteLang === code));
  }
}

/** One item per language, by its English name. */
function fillMenu(root, languages) {
  for (const list of root.querySelectorAll?.("[data-lang-list]") ?? []) {
    const doc = list.ownerDocument;
    list.replaceChildren(...languages.map((language) => {
      const button = Object.assign(doc.createElement("button"), { type: "button", className: "lang-item" });
      button.dataset.siteLang = language.code;
      button.append(Object.assign(doc.createElement("span"), { className: "name", textContent: language.name }));
      return button;
    }));
  }
}

/** Normalize retained file links to routes. */
export function prepareHeaders(root) {
  for (const link of root.querySelectorAll?.("a[href]") ?? []) {
    const href = link.getAttribute("href") ?? "";
    const short = href.replace(PAGE_HREF, (_, page) => page === "index" ? "./" : page);
    if (short !== href) link.setAttribute("href", short);
  }
}

/** Open or close a tool row folded into its menu. */
export function openTools(nav, open) {
  nav.classList.toggle("open", open);
  nav.querySelector(".tools-toggle")?.setAttribute("aria-expanded", String(open));
}

/** Fold a tool row into its menu without room. */
export function fitTools(nav) {
  const current = nav.querySelector(".tools-list [aria-current]");
  const label = nav.querySelector("[data-tools-current]");
  if (current && label) plainText(label, current.querySelector?.("[data-ui]")?.textContent ?? current.textContent);
  if (!nav.getClientRects().length) return; // a hidden masthead
  nav.classList.remove("compact");
  const compact = nav.querySelector(".tools-list").scrollWidth > nav.clientWidth + 1;
  nav.classList.toggle("compact", compact);
  if (!compact) openTools(nav, false);
}

/** Refit the tool rows when masthead or fonts change. */
function watchTools(root) {
  const navs = [...(root.querySelectorAll?.(".mast .tools") ?? [])];
  if (!navs.length) return;
  const fit = () => navs.forEach(fitTools);
  if (globalThis.ResizeObserver) {
    const observer = new ResizeObserver(fit);
    for (const nav of navs) observer.observe(nav.closest(".mast"));
  }
  globalThis.document?.fonts?.ready?.then(fit);
}

/** The masthead menus' keys and clicks. */
function captureFocus(root) {
  const active = root?.activeElement ?? root?.defaultView?.document?.activeElement;
  const selection = root?.getSelection?.() ?? root?.defaultView?.getSelection?.();
  let range = null;
  const position = (node, offset) => {
    const path = [];
    while (node && node !== active) {
      const parent = node.parentNode;
      if (!parent) return null;
      path.unshift([...parent.childNodes].indexOf(node));
      node = parent;
    }
    return node === active ? { path, offset } : null;
  };
  if (active && selection?.rangeCount) {
    const current = selection.getRangeAt(0);
    const start = position(current.startContainer, current.startOffset);
    const end = position(current.endContainer, current.endOffset);
    if (start && end) range = { start, end };
  }
  return { active, range };
}

function restoreFocus(root, saved) {
  const active = saved?.active;
  if (!active?.isConnected) return;
  active.focus({ preventScroll: true });
  if (!saved.range) return;
  const doc = root?.ownerDocument ?? root?.defaultView?.document ?? root;
  const selection = root?.getSelection?.() ?? root?.defaultView?.getSelection?.();
  const locate = ({ path, offset }) => {
    let node = active;
    for (const index of path) node = node?.childNodes[index];
    if (!node) return null;
    const length = node.nodeType === 3 ? node.data.length : node.childNodes.length;
    return [node, Math.min(offset, length)];
  };
  const start = locate(saved.range.start), end = locate(saved.range.end);
  if (!start || !end) return;
  const range = doc.createRange();
  range.setStart(...start);
  range.setEnd(...end);
  selection?.removeAllRanges();
  selection?.addRange(range);
}

function wireMenu(root, fetcher, storage) {
  let focus = null;
  root.addEventListener("pointerdown", (event) => {
    if (event.target.closest?.(".lang-menu") && !event.target.closest?.("[data-site-lang]")) focus = captureFocus(root);
  });
  root.addEventListener("click", (event) => {
    const toggle = event.target.closest?.(".tools-toggle");
    for (const nav of root.querySelectorAll?.(".tools.open") ?? []) {
      if (nav.contains(event.target) && !event.target.closest?.("a")) continue;
      openTools(nav, false);
    }
    const button = event.target.closest?.("[data-site-lang]");
    if (button) {
      if (!root.activeElement?.closest?.(".lang-menu")) focus = captureFocus(root);
      else focus ??= captureFocus(root);
    }
    for (const menu of root.querySelectorAll?.(".lang-menu[open]") ?? []) {
      if (!button && menu.contains(event.target)) continue;
      menu.open = false;
      if (button && menu.contains(button)) menu.querySelector("summary")?.focus();
    }
    if (toggle) {
      const nav = toggle.closest(".tools");
      openTools(nav, !nav.classList.contains("open"));
    }
    if (!button) return;
    const restore = focus;
    focus = null;
    rememberSiteLanguage(button.dataset.siteLang, storage);
    useSiteLanguage(button.dataset.siteLang, { fetcher, root, storage }).then(() => restoreFocus(root, restore)).catch(console.error);
  });
  root.addEventListener("keydown", (event) => {
    const tools = event.target.closest?.(".tools.open");
    const menu = tools ?? event.target.closest?.(".lang-menu[open]");
    if (!menu) return;
    if (event.key === "Escape") {
      event.preventDefault();
      if (tools) openTools(tools, false);
      else menu.open = false;
      menu.querySelector(tools ? ".tools-toggle" : "summary")?.focus();
      return;
    }
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    const items = [...menu.querySelectorAll(tools ? ".tools-list a" : "[data-site-lang]")];
    if (!items.length) return;
    event.preventDefault();
    const at = items.indexOf(event.target);
    const step = event.key === "ArrowDown" ? 1 : items.length - 1;
    const marked = items.findIndex((item) => item.matches("[aria-current], [aria-pressed='true']"));
    items[at < 0 ? Math.max(0, marked) : (at + step) % items.length].focus();
  });
}

let generation = 0;

/** Commit ready catalogs together, preserving the reading position. */
export async function useSiteLanguage(code, { fetcher = fetch, root = globalThis.document, storage = deviceStorage() } = {}) {
  const mine = ++generation;
  const known = offered?.find((l) => l.code === code);
  const [strings, commits] = await Promise.all([
    known?.interface === false ? null : loadCatalog(code, fetcher, root?.baseURI, storage),
    Promise.all([...preparers].map((prepare) => prepare(code))),
  ]);
  if (mine !== generation) return current;
  return applySiteLanguage(code, strings, commits, root);
}

async function applySiteLanguage(code, strings, commits, root) {
  const shown = strings ? code : SOURCE_LANGUAGE;
  const pending = [];
  keepPosition(() => {
    chosen = code;
    setCatalog(strings);
    setUiLanguage(shown);
    for (const commit of commits) commit?.();
    if (root) {
      markChoice(root, code);
      followGuide(root, code);
      fillPage(root);
      for (const nav of root.querySelectorAll?.(".mast .tools") ?? []) fitTools(nav);
    }
    for (const listener of listeners) {
      try { pending.push(listener(shown, code)); } catch (e) { console.error(e); }
    }
  }, root);
  await Promise.all(pending);
  return shown;
}

function beforeDeadline(promise, deadline, fallback) {
  const remaining = Math.max(0, deadline - Date.now());
  if (!remaining) return Promise.resolve(fallback);
  let timer;
  return Promise.race([promise, new Promise((resolve) => {
    timer = setTimeout(() => resolve(fallback), remaining);
  })]).finally(() => clearTimeout(timer));
}

const builds = new WeakMap();

/** Only two readable identities establish an update. */
export function siteBuildChanged(own, published) {
  const valid = (id) => typeof id === "string" && /^[a-f0-9]{64}$/.test(id);
  return valid(own) && valid(published?.id) && own !== published.id;
}

/** A site of the earlier form names no version and keeps none. */
export function siteVersionKept(version, published) {
  if (typeof version !== "string" || typeof published?.version !== "string") return false;
  return version === published.version || (Array.isArray(published.kept) && published.kept.includes(version));
}

/** Recheck failures without loading any versioned code. */
export function checkSiteBuild(root = globalThis.document) {
  return builds.get(root)?.();
}

/** Detect updates when a page resumes or fails. */
export function watchSiteBuild(root, fetcher = fetch) {
  if (builds.has(root)) return;
  const meta = root.querySelector('meta[name="site-build"]');
  if (!meta?.content) return;
  const own = meta.content, url = new URL("site-build.json", root.baseURI).href;
  const version = root.querySelector('meta[name="site-version"]')?.content;
  let pending = null, notice = null, failed = false;
  const check = (resumed = false) => {
    // A failure joins a request already running.
    if (!resumed) failed = true;
    if (notice || pending) return pending;
    pending = Promise.resolve().then(async () => {
      try {
        const response = await fetcher(url, { cache: "no-store", credentials: "omit" });
        if (!response.ok) return;
        const published = await response.json();
        if (!siteBuildChanged(own, published)) return;
        // A resumed page of a kept version still loads its scripts.
        if (!failed && siteVersionKept(version, published)) return;
        notice = root.createElement("dialog");
        notice.className = "dex-dialog";
        notice.setAttribute("role", "alertdialog");
        notice.setAttribute("aria-labelledby", "site-update-message");
        const message = root.createElement("p"), button = root.createElement("button");
        message.id = "site-update-message";
        message.dataset.uiSite = button.dataset.uiSite = "";
        showText(message, "The site was updated. Reload this page to continue.");
        button.type = "button";
        button.className = "btn primary";
        showText(button, "Reload");
        button.addEventListener("click", () => root.defaultView.location.reload());
        notice.addEventListener("cancel", (event) => event.preventDefault());
        notice.append(message, button);
        root.body.append(notice);
        notice.showModal();
      } catch {
        // Offline checks retry on the next trigger.
      }
    }).finally(() => { pending = null; failed = false; });
    return pending;
  };
  const failure = () => check();
  builds.set(root, failure);
  let hidden = root.visibilityState === "hidden";
  root.defaultView.addEventListener("visibilitychange", () => {
    if (root.visibilityState === "hidden") hidden = true;
    else if (root.visibilityState === "visible" && hidden) {
      hidden = false;
      check(true);
    }
  });
  root.defaultView.addEventListener("error", failure, true);
  root.defaultView.addEventListener("unhandledrejection", failure);
}

/** Open a page and wire the masthead menu. */
export async function startSite({ fetcher = fetch, storage = deviceStorage(), root = globalThis.document,
  browser = globalThis.navigator?.languages, reveal = () => root?.siteStartup?.reveal(), onRegistry = () => {} } = {}) {
  const mine = ++generation;
  const startup = root?.siteStartup;
  const deadline = startup?.deadline ?? Date.now() + 1500;
  if (root) prepareHeaders(root);
  installSiteFonts(root, fetcher);
  const stored = recallSiteLanguage(storage);
  const detected = Array.from(browser ?? [], (tag) => String(tag).toLowerCase().split("-")[0]);
  const initial = startup?.language ?? (stored && LANG_CODE.test(stored) ? stored
    : detected.find((code) => LANG_CODE.test(code)) ?? SOURCE_LANGUAGE);
  const cached = startup?.expired ? null : cachedCatalog(initial, storage, root?.baseURI);
  if (cached) {
    chosen = initial;
    setCatalog(cached);
    setUiLanguage(initial);
    fillPage(root);
    reveal();
  }
  markChoice(root, initial);
  wireMenu(root, fetcher, storage);
  watchTools(root);
  const languages = loadSiteLanguages(fetcher, root?.baseURI, (doc) => onRegistry(doc, storage));
  const firstCatalog = loadCatalog(initial, fetcher, root?.baseURI, storage);
  const fallback = { code: initial, strings: cached, commits: [], languages: null };
  const loading = (async () => {
    const listed = await languages;
    const codes = listed ? listed.map((l) => l.code) : [SOURCE_LANGUAGE, initial];
    const code = preferredLanguage(codes, stored, browser);
    const known = listed?.find((l) => l.code === code);
    const [strings, commits] = await Promise.all([
      known?.interface === false ? null : code === initial ? firstCatalog : loadCatalog(code, fetcher, root?.baseURI, storage),
      Promise.all([...preparers].map((prepare) => prepare(code))),
    ]);
    return { code, strings, commits, languages: listed };
  })().catch(() => fallback);
  const ready = await beforeDeadline(loading, deadline, fallback);
  if (mine !== generation) return current;
  offered = ready.languages;
  if (offered) fillMenu(root, offered);
  const applied = applySiteLanguage(ready.code, ready.strings, ready.commits, root);
  reveal();
  if (!offered) languages.then((listed) => {
    if (mine !== generation || !listed) return;
    offered = listed;
    fillMenu(root, offered);
    markChoice(root, chosen);
    followGuide(root, chosen);
  });
  if (ready === fallback && !cached) loading.then((late) => {
    if (mine !== generation || late === fallback || !late.strings) return;
    offered = late.languages ?? offered;
    if (offered) fillMenu(root, offered);
    return applySiteLanguage(late.code, late.strings, late.commits, root);
  });
  return applied;
}

/** Open a guide page written in `own`. */
export function startGuide({ own = globalThis.document?.documentElement?.lang || SOURCE_LANGUAGE, family = "guide",
  go = (page) => globalThis.location.replace(page + globalThis.location.hash), start = startSite, ...options } = {}) {
  let moving = false;
  onSiteLanguage((shown, code) => {
    const target = guideMove(own, code, offered, family);
    if (target) {
      moving = true;
      go(target);
    }
  });
  return start({ ...options, reveal: () => {
    if (!moving && (chosen === own || guideFor(chosen, offered, family) === guidePage(own, family))) {
      (options.root ?? globalThis.document)?.siteStartup?.reveal();
    }
  } });
}
