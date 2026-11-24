// Editor data: languages, index and chunks.
// Chunks load when an entry of theirs opens.
// Every file decodes through editorstore.js.

import { chunkPartUrl, decodeChunk, decodeIndex, decodeSource, decodeTerms, sourceUrl } from "./editorstore.js";
import { explanationText, unnoted } from "./checktext.js";
import { searchable } from "./text.js";
import { catalogCapabilities, EDITOR_SCHEMA, EDITOR_SCHEMAS, verifyManifest } from "./site-model.js";
import { N_, t, uiError } from "./ui.js";

export { EDITOR_SCHEMA };

/** Reject incompatible cached documents before combining them. */
export function verifyEditorSchema(doc) {
  if (!EDITOR_SCHEMAS.includes(doc.schema)) throw new Error(`Catalog schema does not match ${EDITOR_SCHEMA}`);
}

/** JSON from a URL, revalidating cached responses. */
export async function getJson(url, fetcher = fetch) {
  const r = await fetcher(url, { cache: "no-cache" });
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}

/**
 * Top bar version of the data's toolkit:
 * {text, title} from "0.2.0+1a2b3c4[.dirty]"
 * (editordata.toolkit_version); empty without one.
 */
export function versionLabel(toolkit) {
  if (!toolkit) return { text: "", title: "" };
  const [version, build = ""] = toolkit.split("+");
  const [commit, ...rest] = build.split(".");
  const from = commit ? t("commit {commit}", { commit }) : t("an unknown commit");
  const title = rest.includes("dirty") ? t("gen3tl {version}: editor data generated at {from} with uncommitted changes", { version, from })
    : t("gen3tl {version}: editor data generated at {from}", { version, from });
  return { text: version, title };
}

/** Base URL of a language's index, for its chunks. */
export const dirOf = (path) => path.slice(0, path.lastIndexOf("/") + 1);

/** The decoded index of a language, with its game part. */
export async function loadIndex(url, fetcher = fetch) {
  const stored = await getJson(url, fetcher);
  verifyEditorSchema(stored);
  const game = await getJson(new URL(stored.shared, url).href, fetcher);
  verifyEditorSchema(game);
  return decodeIndex(stored, game, url);
}

/** The terms file of a decoded index at `url`. */
export async function loadTerms(index, url, fetcher = fetch) {
  const stored = await getJson(url, fetcher);
  verifyEditorSchema(stored);
  return decodeTerms(index, stored);
}

/** The ROM locations of the English of a decoded index. */
export async function loadSource(index, fetcher = fetch) {
  const url = sourceUrl(index);
  if (!url) throw new Error("Catalog has no source rows");
  return decodeSource(await getJson(url, fetcher));
}

/** Profiles and catalogs of a game. */
export function projectProfiles(manifest, { readOnly = false } = {}) {
  verifyManifest(manifest);
  const profiles = new Map((manifest.profiles ?? []).map((p) => [p.id, { ...p, languages: [] }]));
  const pairs = new Set();
  for (const lang of manifest.languages ?? []) {
    if (!lang.profile || !lang.code || !catalogCapabilities(lang).readable) throw uiError("Invalid editor project identity");
    const key = `${lang.profile}/${lang.code}`;
    if (pairs.has(key)) throw uiError("Duplicate editor project: {key}", { key });
    pairs.add(key);
    if (!profiles.has(lang.profile)) {
      profiles.set(lang.profile, { id: lang.profile, name: lang.profile, support: "legacy", languages: [] });
    }
    const profile = profiles.get(lang.profile);
    const capabilities = catalogCapabilities(lang, profile.support);
    if (readOnly ? capabilities.readable : capabilities.editable) profile.languages.push(lang);
  }
  return [...profiles.values()];
}

/** Reject a catalog fetched for another project. */
export function verifyProjectIndex(index, profile, language) {
  if (index.profile !== profile || index.language !== language) {
    throw new Error(`Catalog identity does not match ${profile}/${language}`);
  }
  verifyEditorSchema(index);
}

/** Chunk cache of one language: get(n) loads once. */
export class Chunks {
  constructor(base, index, fetcher = fetch) {
    verifyEditorSchema(index);
    this.base = base;
    this.index = index;
    this.list = index.chunks;
    this.fetcher = fetcher;
    this.loaded = new Map();
  }

  get(n) {
    if (!this.loaded.has(n)) {
      const p = Promise.all([getJson(this.base + this.list[n].path, this.fetcher),
        getJson(chunkPartUrl(this.index, n), this.fetcher)]).then(([stored, part]) => {
        verifyEditorSchema(stored);
        verifyEditorSchema(part);
        const c = decodeChunk(this.index, n, stored, part);
        const byId = new Map(c.entries.map((e) => [e.id, e]));
        return { file: c.file, byId };
      });
      p.catch(() => this.loaded.delete(n)); // retry later
      this.loaded.set(n, p);
    }
    return this.loaded.get(n);
  }

  /** The full entry of a list row. */
  async entry(row) {
    const c = await this.get(row.chunk);
    const e = c.byId.get(row.id);
    if (!e) throw new Error(`${row.id} not in ${this.list[row.chunk].path}`);
    return e;
  }
}

/** PO files grouped by folder: [{group, files: [{chunk, name, entries}]}]. */
export function groupsOf(chunks) {
  const groups = new Map();
  chunks.forEach((c, n) => {
    const parts = c.file.replace(/\.po$/, "").split("/");
    const at = parts.indexOf("po");
    const rel = at >= 0 ? parts.slice(at + 1) : parts.slice(-2);
    const group = rel.length > 1 ? rel.slice(0, -1).join("/") : "(top)";
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push({ chunk: n, name: rel.at(-1), entries: c.entries });
  });
  return [...groups].map(([group, files]) => ({ group, files }));
}

/** Map names of the rows, sorted. */
export function mapsOf(rows) {
  return [...new Set(rows.flatMap((r) => r.maps ?? []))].sort();
}

// size problems: too wide, too many lines or keys
const OVERFLOW = new Set(["line_wide", "line_wide_window", "line_wide_slots", "line_wide_slots_window", "column_wide",
  "column_wide_slots", "other_window_wide", "too_many_lines", "box_unscrolled", "field_overflow", "in_place_overflow",
  "too_many_keys", "single_line_break", "dropped_breaks", "slot_rewrap", "slot_rewrap_slots", "pair_wide"]);

export const isOverflow = (p) => OVERFLOW.has(unnoted(p.message)?.code);

/** The words of an entry status. */
export const STATUS_LABEL = {
  approved: N_("approved"), fuzzy: N_("translated"), untranslated: N_("untranslated"), nothing: N_("nothing to translate"),
};
export const statusLabel = (status) => (STATUS_LABEL[status] ? t(STATUS_LABEL[status]) : status);

// the reach classes of editordata.reach_data
const REACH_LABEL = { reachable: N_("reachable"), "possibly reachable": N_("possibly reachable"),
  "never shown": N_("never shown"), unknown: N_("unknown") };
const REACH_SOURCE = { "not studied": N_("not studied"), "reach study": N_("reach study"), review: N_("review") };

/**
 * Reach of an entry as {text, title, never}.
 * Data without reach classes reads as reachable.
 */
export function reachLabel(reach) {
  const r = reach ?? { class: "reachable", source: "not studied", evidence: "" };
  const name = REACH_LABEL[r.class] ? t(REACH_LABEL[r.class]) : r.class;
  const source = r.source.split(", ").map((part) => REACH_SOURCE[part] ? t(REACH_SOURCE[part]) : part).join(", ");
  return { text: `${name} (${source})`, title: explanationText(r.evidence) || name, never: r.class === "never shown" };
}

/** Indices of the rows the filters keep. */
export function filterRows(rows, filters, edits = new Map(), groups = null) {
  const f = filters;
  const q = (f.search || "").trim().toLowerCase();
  let chunks = null;
  if (f.group && groups) {
    const g = groups.find((x) => x.group === f.group);
    chunks = new Set(g ? g.files.map((x) => x.chunk) : []);
  }
  const out = [];
  rows.forEach((r, i) => {
    const ed = edits.get(r.id);
    if (f.status === "edited" ? !ed : f.status && r.status !== f.status) return;
    if (f.chunk !== undefined && f.chunk !== "" && r.chunk !== Number(f.chunk)) return;
    if (chunks && !chunks.has(r.chunk)) return;
    if (f.map && !(r.maps ?? []).includes(f.map)) return;
    if (f.kind && r.kind !== f.kind) return;
    if (f.reach && (r.reach ?? "reachable") !== f.reach) return;
    if (f.terms === "shortened" && !(r.shortened ?? []).length) return;
    if (f.problems) {
      const probs = ed?.problems ?? r.problems;
      if (f.problems === "overflow" && !probs.some(isOverflow)) return;
      if (f.problems === "errors" && !probs.some((p) => p.severity === "error")) return;
      if (f.problems === "warnings" && !probs.some((p) => p.severity === "warning")) return;
    }
    if (q) {
      if (r._en === undefined) [r._en, r._tr] = [searchable(r.source_text ?? ""), searchable(r.translation)];
      const tr = ed ? searchable(ed.translation) : r._tr;
      if (!(r._en.includes(q) || tr.includes(q) || r.id.toLowerCase() === q)) return;
    }
    out.push(i);
  });
  return out;
}

/**
 * The translation rules of an entry:
 * [{text, source, evidence}] (editordata, gen3tl.check.rules).
 */
export const rulesOf = (entry) => entry?.rules ?? [];

/** Catalog key forms; shared with gen3tl.catalog.keys. */
export const KEY_FORMS = "map:\\d+\\.\\d+/(?:npc|trigger|sign|mapscript):\\d+/\\d+|table:[a-z0-9_-]+/\\d+(?:/[a-z0-9_]+)?"
  + "|battle:\\d+|code:[A-Za-z0-9_]+/\\d+|sym:[A-Za-z0-9_]+|hash:[0-9a-f]{8}";
const ID_IN_TEXT = new RegExp(`\\bs[0-9A-F]{7}\\b|(?<![\\w-])(?:${KEY_FORMS})(?![\\w-]|/\\d)`, "g");

const FULL_KEY = new RegExp(`^(?:${KEY_FORMS})$`);

/** A fixed table row's inventory id as its key. */
export const tableKey = (id) => (FULL_KEY.test(String(id)) ? String(id)
  : String(id).replace(/^([a-z][a-z0-9_]*):(\d+)$/, "table:$1/$2"));

/** The entry a route tail names. */
export const tailId = (tail) => tail.join("/");

/** Reserved view prefixes prevent text identifiers from capturing routes. */
const PLACE_VIEWS = ["pictures", "terms", "families", "keyboards", "fonts", "enginerules"];

/** The view and item a route tail names. */
export function placeOf(tail) {
  if (tail[0] === "keyboards" && ["page", "chat"].includes(tail[1])) {
    return { view: "keyboards", kind: tail[1], id: tail[2] ?? null };
  }
  if (tail[0] === "fonts" && tail[2] === "glyph") {
    return { view: "fonts", kind: "glyph", id: tail[1], code: tail.length === 4 ? tail[3] : null };
  }
  if (PLACE_VIEWS.includes(tail[0])) return { view: tail[0], id: tail[1] ?? null };
  return { view: "texts", id: tailId(tail) || null };
}

/** The way back between editor views, kept in browser history. */
export class Trail {
  constructor(history, views) {
    this.history = history;
    this.views = views;
  }

  leave(view, hash) {
    this.history.pushState({ from: view }, "", hash);
  }

  replace(hash) {
    this.history.replaceState(this.history.state, "", hash);
  }

  get from() {
    const view = this.history.state?.from;
    return this.views.includes(view) ? view : null;
  }
}

/** Inventory id -> key of a profile's alias file. */
export async function loadAliases(url, fetcher = fetch) {
  try {
    const doc = await getJson(url, fetcher);
    return new Map(Object.entries(doc.ids ?? {}));
  } catch {
    return new Map();
  }
}

/**
 * A rule's text split at the entry ids it names:
 * [{text} | {id}], ids those `known` holds:
 * keys, and the inventory ids of older text.
 */
export function idParts(text, known) {
  const out = [];
  let at = 0;
  for (const m of text.matchAll(ID_IN_TEXT)) {
    if (!known(m[0])) continue;
    if (m.index > at) out.push({ text: text.slice(at, m.index) });
    out.push({ id: m[0] });
    at = m.index + m[0].length;
  }
  if (at < text.length) out.push({ text: text.slice(at) });
  return out;
}

/** A catalog path from its po/ folder, for reading. */
export function shortFile(path) {
  const at = path.lastIndexOf("/po/");
  return at >= 0 ? path.slice(at + 1) : path;
}

/** Entry counts by status, with edits and error rows. */
export function statusCounts(rows, edits = new Map()) {
  const n = { approved: 0, fuzzy: 0, untranslated: 0, nothing: 0, "never shown": 0, problems: 0, edited: 0 };
  for (const r of rows) {
    const key = r.reach === "never shown" ? "never shown" : r.status;
    n[key] = (n[key] ?? 0) + 1;
    const ed = edits.get(r.id);
    if ((ed?.problems ?? r.problems).some((p) => p.severity === "error")) n.problems++;
    if (ed) n.edited++;
  }
  return n;
}
