// The site registry: registry.json lists the games served.
// Each game's data holds languages.json, releases and Pokedex datasets.

import { STAGES } from "./site-model.js";
import { toolURL } from "./routes.js";
import { canonicalLanguage, SOURCE_LANGUAGE, uiError } from "./ui.js";

export { gameHash, STAGES } from "./site-model.js";
export { pickerURL } from "./routes.js";

export const REGISTRY_SCHEMA = 1;
const ID = /^[a-zA-Z0-9][a-zA-Z0-9._-]*$/;
export const REPOSITORY = /^[A-Za-z0-9][A-Za-z0-9-]*\/(?!\.\.?$)[A-Za-z0-9._-]+$/;
export const TOOLKIT_REPOSITORY = "haritos90/gen3tl";

const text = (value) => typeof value === "string" && !!value.trim();
const object = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const nullable = (check) => (value) => value === null || check(value);

function metadataURL(value) {
  if (!text(value) || /[\s\\\x00-\x1f\x7f]/.test(value) || value.startsWith("//")) return false;
  if (/^[^/?#]*:/.test(value) && !/^https?:\/\//i.test(value)) return false;
  try {
    const url = new URL(value, "https://example.invalid/");
    return ["http:", "https:"].includes(url.protocol) && !!url.hostname && url.port !== "0";
  } catch {
    return false;
  }
}

function metadataFields(value, fields, where) {
  if (!object(value)) throw uiError("registry.json: invalid {field}", { field: where });
  for (const [key, item] of Object.entries(value)) {
    if (!Object.hasOwn(fields, key) || !fields[key](item)) {
      throw uiError("registry.json: invalid {field}", { field: `${where}.${key}` });
    }
  }
}

function readSite(site, base) {
  metadataFields(site, {
    name: text, brand: metadataURL, home: metadataURL,
    repository: nullable((value) => typeof value === "string" && REPOSITORY.test(value)),
    new_language_url: nullable(metadataURL), licenses: Array.isArray,
  }, "site");
  const result = { ...site };
  for (const key of ["brand", "home", "new_language_url"]) {
    if (site[key] != null) result[key] = new URL(site[key], base).href;
  }
  if (site.licenses !== undefined) {
    result.licenses = site.licenses.map((license) => {
      metadataFields(license, { label_key: text, url: metadataURL }, "site.licenses");
      if (!text(license.label_key) || !metadataURL(license.url)) {
        throw uiError("registry.json: invalid {field}", { field: "site.licenses" });
      }
      return { ...license, url: new URL(license.url, base).href };
    });
  }
  return result;
}

/** Validated games of a registry; data URLs resolved against `base`. */
export function readRegistry(doc, base) {
  if (!doc || doc.schema !== REGISTRY_SCHEMA || !Array.isArray(doc.games)) {
    throw uiError("registry.json: unknown schema");
  }
  if (doc.include !== undefined && (!Array.isArray(doc.include) || doc.include.some((url) => !metadataURL(url)))) {
    throw uiError("registry.json: invalid {field}", { field: "include" });
  }
  const site = doc.site === undefined ? null : readSite(doc.site, base);
  const seen = new Set();
  const games = doc.games.map((g) => {
    if (!g || typeof g.id !== "string" || !ID.test(g.id)) throw uiError("registry.json: invalid game id");
    if (seen.has(g.id)) throw uiError("registry.json: duplicate game {game}", { game: g.id });
    seen.add(g.id);
    if (typeof g.name !== "string" || !g.name.trim()) throw uiError("registry.json: {game} has no name", { game: g.id });
    if (!STAGES.includes(g.stage)) throw uiError("registry.json: {game} has an unknown stage", { game: g.id });
    if (!Array.isArray(g.languages) || g.languages.some((c) => typeof c !== "string" || !ID.test(c))) {
      throw uiError("registry.json: {game} has invalid languages", { game: g.id });
    }
    if (typeof g.data !== "string") throw uiError("registry.json: {game} has no data URL", { game: g.id });
    if (g.repository !== undefined && !(typeof g.repository === "string" && REPOSITORY.test(g.repository))) {
      throw uiError("registry.json: {game} has an invalid repository", { game: g.id });
    }
    if (g.source_language !== undefined && !canonicalLanguage(g.source_language)) {
      throw uiError("registry.json: {game} has an invalid source language", { game: g.id });
    }
    if (g.engine_label !== undefined && !text(g.engine_label)) {
      throw uiError("registry.json: invalid {field}", { field: "engine_label" });
    }
    const links = g.links === undefined ? {} : g.links;
    metadataFields(links, { project: nullable(metadataURL), new_language: nullable(metadataURL),
      content_license: nullable(metadataURL) }, "links");
    const compatibility = g.compatibility === undefined ? {} : g.compatibility;
    metadataFields(compatibility, { changed_bytes: text, saves: text, link_play: text }, "compatibility");
    const landing = g.landing === undefined ? {} : g.landing;
    metadataFields(landing, { intro: text, editor: text, changes_title: text, changes: text,
      review_title: text, review: text }, "landing");
    const labels = g.interface_labels;
    if (labels !== undefined && (!object(labels)
        || Object.entries(labels).some(([code, path]) => !ID.test(code) || !metadataURL(path)))) {
      throw uiError("registry.json: invalid {field}", { field: "interface_labels" });
    }
    const data = new URL(g.data || "./", base).href;
    const engine = typeof g.engine === "string" ? g.engine : "unspecified";
    const folder = data.endsWith("/") ? data : `${data}/`;
    return {
      id: g.id, name: g.name, stage: g.stage, engine, engineLabel: g.engine_label ?? engine,
      languages: [...g.languages], data: folder, repository: g.repository ?? null,
      sourceLanguage: g.source_language ?? SOURCE_LANGUAGE,
      links: Object.fromEntries(Object.entries(links).map(([key, value]) => [key, value === null ? null : new URL(value, base).href])),
      compatibility: { ...compatibility },
      landing: { ...landing },
      // null: data published before games carried their texts
      interfaceLabels: labels === undefined ? null
        : Object.fromEntries(Object.entries(labels).map(([code, path]) => [code, new URL(path, folder).href])),
    };
  });
  if (doc.default !== undefined && !seen.has(doc.default)) throw uiError("registry.json: unknown default game");
  const fallback = games.find((g) => g.stage === "translation") ?? games[0] ?? null;
  return { games, default: doc.default ?? fallback?.id ?? null, site };
}

/** Expand one level of registries in declaration order. */
export async function loadRegistry(url, fetcher = fetch) {
  const get = async (address) => {
    const response = await fetcher(address, { credentials: "omit", cache: "no-cache" });
    if (!response.ok) throw uiError("registry.json: HTTP {status}", { status: response.status });
    return { doc: await response.json(), base: response.url || address };
  };
  const { doc, base } = await get(url);
  const registry = readRegistry(doc, base);
  registry.skipped = [];
  const seen = new Set(registry.games.map((game) => game.id));
  for (const address of doc.include ?? []) {
    const target = new URL(address, base).href;
    try {
      const included = await get(target);
      const games = readRegistry({ schema: included.doc?.schema, games: included.doc?.games }, included.base).games;
      for (const game of games) {
        if (seen.has(game.id)) continue;
        seen.add(game.id);
        registry.games.push(game);
      }
    } catch {
      registry.skipped.push(target);
    }
  }
  registry.default = doc.default ?? registry.games.find((game) => game.stage === "translation")?.id ?? registry.games[0]?.id ?? null;
  return registry;
}


export const GAME_KEY = "gen3tl-game";

/** The remembered game id, or null. */
export function recallGame(storage = globalThis.localStorage) {
  try {
    return storage?.getItem(GAME_KEY) ?? null;
  } catch {
    return null;
  }
}

/** Remember a game for every surface. */
export function rememberGame(id, storage = globalThis.localStorage) {
  try {
    storage?.setItem(GAME_KEY, id);
  } catch {
    // this page only
  }
}

/**
 * A site route: #game/language/tail.
 * - an older #language/tail link resolves to the default game
 * - anything else gives no game
 */
export function siteRoute(hash, registry) {
  let parts;
  try {
    parts = hash.replace(/^#/, "").split("/").filter(Boolean).map(decodeURIComponent);
  } catch {
    return { game: null, language: null, tail: [] };
  }
  if (registry.games.some((g) => g.id === parts[0])) {
    return { game: parts[0], language: parts[1] ?? null, tail: parts.slice(2) };
  }
  const main = registry.games.find((g) => g.id === registry.default);
  if (parts[0] && main?.languages.includes(parts[0])) {
    return { game: main.id, language: parts[0], tail: parts.slice(1) };
  }
  return { game: null, language: null, tail: [] };
}

/** Normalize older card links before writing routes. */
export function pokedexTail(tail) {
  if (!tail.length) return [];
  const [mode, id] = tail;
  if (["species", "moves", "items", "trainers", "locations", "types"].includes(mode)) {
    return [mode, id ?? (mode === "types" ? "0" : undefined)];
  }
  return ["species", mode];
}

/** Keep editor links on canonical card routes. */
export function pokedexLink(game, language, tail = []) {
  return toolURL("pokedex", game, language, ...pokedexTail(tail));
}

/** Where the editor's earlier card hash leads, or null. */
export function pokedexRedirect(route, game, language) {
  if (route.game !== game || route.language !== language || route.tail[0] !== "pokedex") return null;
  return pokedexLink(game, language, route.tail.slice(1));
}

/**
 * Explicit and remembered choices precede the registry default.
 * A sole eligible game remains the final fallback.
 * Only eligible games provide context.
 */
export function chosenGame(registry, hash, storage, allowed = () => true) {
  const games = registry.games.filter(allowed);
  const route = siteRoute(hash, registry);
  const ids = [route.game, recallGame(storage), registry.default];
  for (const id of ids) {
    const game = games.find((g) => g.id === id);
    if (game) return { game, route: route.game === game.id ? route : { game: game.id, language: null, tail: [] } };
  }
  if (games.length === 1) return { game: games[0], route: { game: games[0].id, language: null, tail: [] } };
  return { game: null, route };
}
