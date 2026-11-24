// Shared game data drives every surface.

import { validateManifest } from "./patcher-core.js";
import { N_, SOURCE_LANGUAGE, formatNumber, languageName, t, uiError } from "./ui.js";
import { PAGES, toolURL } from "./routes.js";

export { languageName };
export { gameHash, PAGES } from "./routes.js";

export const EDITOR_SCHEMA = "11.0.0";
// the page also reads text rule wordings and never-shown evidence
export const EDITOR_SCHEMAS = ["10.0.0", EDITOR_SCHEMA];

/** Reject manifests before interpreting versioned game data. */
export function verifyManifest(doc) {
  if (!doc || ![undefined, 1, ...EDITOR_SCHEMAS].includes(doc.schema)) {
    throw uiError("This game's data format is not supported by this site.");
  }
}

export const STAGE_DETAILS = {
  translation: { tone: "ok", label: N_("Translation stage"),
    description: N_("Texts are checked against the measured windows in the game font.") },
  catalog: { tone: "warn", label: N_("Catalog stage"),
    description: N_("At catalog stage the texts can be drafted and exported; window sizes and the game font are not verified, so widths are estimates.") },
  discovery: { tone: "plain", label: N_("Discovery stage"),
    description: N_("Discovery stage: the toolkit reads the game; the translation catalog comes later.") },
  legacy: { tone: "plain", label: N_("Profiled"),
    description: N_("A profiled game without a support stage.") },
  interface: { tone: "plain", label: N_("Site interface"),
    description: N_("The texts of this site: its pages, the patcher, the Pokédex chrome and this editor. No ROM is needed.") },
};

export const STAGES = Object.keys(STAGE_DETAILS).filter((stage) => stage !== "interface");
export const STAGE_NOTES = Object.fromEntries(Object.entries(STAGE_DETAILS).map(([key, stage]) => [key, stage.description]));

export const LANGUAGE_STATES = {
  released: { tone: "ok", label: N_("Released") },
  open: { tone: "plain", label: N_("Open for translation") },
  drafting: { tone: "warn", label: N_("Drafting") },
  readable: { tone: "plain", label: N_("Read only") },
  unavailable: { tone: "plain", label: N_("Not available") },
};

/** Compare semantic versions by their numeric components. */
export function compareVersions(a, b) {
  const x = a.split(".").map(Number);
  const y = b.split(".").map(Number);
  for (let i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i] - y[i];
  return 0;
}

/** Exclude entries without displayed translatable text. */
export function measured(progress) {
  if (!progress) return 0;
  return Object.values(progress).reduce((s, n) => s + n, 0) - (progress.nothing ?? 0) - (progress["never shown"] ?? 0);
}

const share = (count, total) => (total > 0 ? Math.floor((100 * count) / total) : 0);
const withText = (progress) => (progress?.approved ?? 0) + (progress?.fuzzy ?? 0);

/** Round down approval percentages of the measured entries. */
export function percentOf(progress) {
  return share(progress?.approved ?? 0, measured(progress));
}

/** Round down the share of measured entries that carry a translation. */
export function translatedOf(progress) {
  return share(withText(progress), measured(progress));
}

/** What the progress counts and what it leaves out. */
export function progressTitle(progress) {
  return t("{translated} of {total} entries the game shows are translated, {approved} of them approved; left out: {never} never shown (reach study and reviews), {nothing} with nothing to translate", {
    translated: formatNumber(withText(progress)), approved: formatNumber(progress?.approved ?? 0),
    total: formatNumber(measured(progress)),
    never: formatNumber(progress?.["never shown"] ?? 0), nothing: formatNumber(progress?.nothing ?? 0) });
}

/** Published text needs no editable catalog. */
export function catalogCapabilities(entry, stage) {
  const path = (value) => typeof value === "string" && !!value.trim();
  // Catalogs expose readable text through indexes.
  return { editable: path(entry?.index) && stage !== "discovery", readable: path(entry?.texts) || path(entry?.index) };
}

/** Keep availability separate from release progress. */
export function languageModel(game, code, entry, releases) {
  const own = releases.filter((r) => r.profile === game.id && r.language === code)
    .sort((a, b) => compareVersions(b.version, a.version));
  const release = own[0] ?? null;
  const { editable, readable } = catalogCapabilities(entry, game.stage);
  const state = release ? "released" : editable ? (game.stage === "catalog" ? "drafting" : "open")
    : readable ? "readable" : "unavailable";
  return {
    code, name: languageName(code), entries: entry?.entries ?? 0, progress: entry?.progress ?? null,
    translated: translatedOf(entry?.progress), percent: percentOf(entry?.progress), release, releases: own, state,
    editable, readable,
  };
}

const STATE_ORDER = Object.fromEntries(Object.keys(LANGUAGE_STATES).map((key, index) => [key, index]));

/** Build a game summary from manifest and releases. */
export function gameModel(game, manifest, releases = []) {
  const profile = manifest?.profiles?.find((p) => p.id === game.id) ?? null;
  const rows = new Map((manifest?.languages ?? []).filter((l) => l.profile === game.id).map((l) => [l.code, l]));
  const codes = [...new Set([...game.languages, ...rows.keys(), ...releases.filter((r) => r.profile === game.id).map((r) => r.language)])];
  const languages = codes.map((code) => languageModel(game, code, rows.get(code) ?? null, releases))
    .sort((a, b) => STATE_ORDER[a.state] - STATE_ORDER[b.state] || b.translated - a.translated || b.percent - a.percent
      || a.name.localeCompare(b.name));
  const latest = languages.find((l) => l.release)?.release ?? null;
  return {
    id: game.id, name: game.name, stage: game.stage, engine: game.engine, engineLabel: game.engineLabel ?? game.engine ?? "",
    data: game.data, sha1: profile?.sha1 ?? latest?.source_sha1 ?? null,
    size: profile?.size ?? latest?.source_size ?? null, patch_bases: profile?.patch_bases ?? [],
    entries: Math.max(0, ...languages.map((l) => l.entries)),
    sourceEntries: profile?.source_catalog?.entries ?? null, pokedex: profile?.pokedex ?? null,
    languages, released: Boolean(latest), repository: game.repository ?? null, sourceLanguage: game.sourceLanguage ?? SOURCE_LANGUAGE,
    links: game.links ?? {}, compatibility: game.compatibility ?? {}, landing: game.landing ?? {},
  };
}

async function getJson(url, fetcher) {
  const response = await fetcher(url, { credentials: "omit", cache: "no-store" });
  if (!response.ok) {
    const error = new Error(`${url}: HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

/** Load each data directory's manifest and releases once. */
export async function loadSources(registry, fetcher = fetch) {
  const sources = new Map();
  await Promise.all([...new Set(registry.games.map((g) => g.data))].map(async (data) => {
    const source = { manifest: null, releases: [], error: null };
    sources.set(data, source);
    try {
      source.manifest = await getJson(new URL("languages.json", data).href, fetcher);
      verifyManifest(source.manifest);
    } catch (e) {
      source.error = String(e.message || e);
      if (source.manifest) {
        source.manifest = null;
        source.unavailable = true;
        return;
      }
    }
    // interface data carries no releases
    if (source.manifest?.profiles?.length && source.manifest.profiles.every((p) => p.support === "interface")) return;
    try {
      source.releases = validateManifest(await getJson(new URL("releases/releases.json", data).href, fetcher));
    } catch (e) {
      if (e.status !== 404) source.error ??= String(e.message || e);
    }
  }));
  return sources;
}

/** Build page links for one game language. */
export function hrefs(game, lang) {
  return Object.fromEntries(Object.keys(PAGES).map((tool) => [tool, toolURL(tool, game.id, lang.code)]));
}

/** Summarize language release and translation progress. */
export function languageMeta(lang) {
  const parts = [];
  if (lang.release) parts.push(t("Translation {version}", { version: lang.release.version }));
  if (lang.entries) {
    parts.push(t("{entries} entries · {translated}% translated · {approved}% approved", {
      entries: formatNumber(lang.entries), translated: lang.translated, approved: lang.percent }));
  }
  return parts.join(" · ");
}

/** Join each game with its published data. */
export function gamesModel(registry, sources) {
  return registry.games.map((game) => {
    const source = sources.get(game.data) ?? {};
    return { ...gameModel(game, source.manifest, source.releases), error: source.error ?? null };
  });
}

/** Source text accompanies every readable catalog. */
export function pokedexLanguages(game) {
  const readable = game.languages.filter((language) => language.readable);
  return readable.length ? [...new Set([game.sourceLanguage ?? SOURCE_LANGUAGE, ...readable.map((language) => language.code)])] : [];
}

const anyLanguage = (game, language, allowed) => language ? allowed(language) : game.languages.some(allowed);

export const SURFACES = {
  patch: {
    label: N_("Patch"), title: N_("Patch"),
    allowed: (game, language) => anyLanguage(game, language, (row) => Boolean(row.release)),
    url: (game) => toolURL("patch", game.id),
  },
  editor: {
    label: N_("Open the editor"), title: N_("Translation editor"),
    allowed: (game, language) => anyLanguage(game, language, (row) => row.editable),
    url: (game) => toolURL("editor", game.id),
  },
  save: {
    label: N_("Convert a save"), title: N_("Save converter"),
    allowed: (game, language) => anyLanguage(game, language, (row) => row.releases.some((release) => release.names)),
    url: (game) => toolURL("save", game.id),
  },
  pokedex: {
    label: N_("Open the Pokédex"), title: N_("Pokédex"),
    allowed: (game, language) => Boolean(game.pokedex) && anyLanguage(game, language, (row) => row.readable),
    url: (game, code = pokedexLanguages(game)[0] ?? game.sourceLanguage ?? SOURCE_LANGUAGE) => hrefs(game, { code }).pokedex,
  },
};

/** Keep the requested available surface first. */
export function actionsOf(game, next) {
  const keys = Object.keys(SURFACES).filter((key) => SURFACES[key].allowed(game));
  return next && keys.includes(next) ? [next, ...keys.filter((key) => key !== next)] : keys;
}
