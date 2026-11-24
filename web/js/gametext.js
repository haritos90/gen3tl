// Recover original bytes before reading the game's text.

import { glossaryMatcher, glossaryProblems } from "./terms.js";
import { digest, reverseRelease, validateManifest } from "./patcher-core.js";
import { releasePatch } from "./patch-engine.js";
import { N_, errorText, t, uiError } from "./ui.js";

export const TEXT_NOTE = N_("The game's text appears when the ROM, original or patched, is loaded.");
const DIFFERS = N_("This line differs in the loaded ROM; the original ROM gives its English.");
const UNAVAILABLE = N_("Typography and glossary checks need the game's text: load the ROM, original or patched.");

/** Whether the data ships its English. */
export function shipsEnglish(index) {
  return Boolean(index?.list?.some((r) => typeof r.source_text === "string"));
}

/** Only verified original bytes reach text and glyph extraction. */
export async function readRom(file, game, releases = [], fetcher = fetch, patchFile = null) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  const sha1 = await digest(bytes);
  const expected = game.sha1?.toLowerCase();
  if (sha1 === expected) return { bytes, sha1, name: file.name, game: game.id };
  const base = game.patch_bases?.find((b) => b.sha1.toLowerCase() === sha1);
  if (base) {
    if (!patchFile) {
      throw Object.assign(uiError("{base} recognized. Choose the UPS patch for {game}. Expected SHA-1 {expected}.",
        { base: base.name, game: game.name, expected }), { patchBase: base });
    }
    let source;
    try {
      const patch = new Uint8Array(await patchFile.arrayBuffer());
      source = await releasePatch(bytes, patch, game.size);
    } catch (e) {
      throw uiError("The UPS patch could not produce {game}: {reason} Expected SHA-1 {expected}.",
        { game: game.name, reason: errorText(e), expected });
    }
    const actual = await digest(source);
    if (actual !== expected) {
      throw uiError("The UPS patch produced SHA-1 {sha1}. Expected SHA-1 {expected}.",
        { sha1: actual, game: game.name, expected });
    }
    return { bytes: source, sha1: actual, name: file.name, game: game.id };
  }
  validateManifest({ schema: 1, releases });
  const release = releases.find((r) => r.profile === game.id && r.source_sha1 === expected && r.result_sha1 === sha1);
  if (!release) {
    throw uiError("SHA-1 {sha1} matches neither the original {game} ROM nor a published release. Expected original SHA-1 {expected}.",
      { sha1, game: game.name, expected });
  }
  const url = new URL(release.patch, new URL("releases/", game.data)).href;
  const response = await fetcher(url, { credentials: "omit", redirect: "error", cache: "no-store" });
  if (!response.ok) throw uiError("Patch download failed ({status}).", { status: response.status });
  const patch = new Uint8Array(await response.arrayBuffer());
  const source = await reverseRelease(bytes, patch, release);
  return { bytes: source, sha1: release.source_sha1, name: file.name, game: game.id, release };
}

/** The problems heading's catalog key and values. */
export function problemsHeading(res, rom) {
  if (res.draft) return { key: N_("Problems: placeholders only") };
  if (res.estimate) return { key: N_("Problems: check not loaded") };
  if (rom) return { key: N_("Problems: original ROM") };
  return { key: N_("Problems: ROM not loaded") };
}

/** The English a catalog key names in the ROM. */
export function englishOf(texts, key) {
  return typeof texts?.[key] === "string" ? texts[key] : key;
}

/** Give list rows the English of `texts`; null removes it. */
export function applyEnglish(rows, texts) {
  let n = 0;
  for (const r of rows) {
    const english = texts?.[r.id];
    if (typeof english === "string") {
      r.source_text = english;
      n++;
    } else delete r.source_text;
    delete r._en;
    delete r._tr;
  }
  return n;
}

/** Give rows glossary terms and problems; null removes them. */
export function applyGlossary(rows, texts, glossary, fold = {}) {
  let added = 0;
  const matcher = glossary ? glossaryMatcher(glossary) : null;
  for (const r of rows) {
    const base = r._problems ?? r.problems;
    r._problems = base;
    const english = texts?.[r.id];
    if (typeof english === "string" && glossary) {
      const found = matcher.find(english);
      r.terms = [...new Set(found.map((term) => term.id))];
      const problems = r.status === "untranslated" || r.status === "nothing" ? []
        : glossaryProblems(english, r.translation, glossary, fold, found);
      r.problems = [...base, ...problems];
      added += problems.length;
    } else {
      delete r.terms;
      r.problems = base;
    }
  }
  return added;
}

/** Join both English-derived row models in one worker job. */
export function joinRows(rows, texts, glossary, fold = {}) {
  applyEnglish(rows, texts);
  applyGlossary(rows, texts, glossary, fold);
  return rows;
}

/** An entry with the English of `texts`, or without one. */
export function withEnglish(entry, texts) {
  const english = texts?.[entry.id];
  if (typeof english === "string") entry.source_text = english;
  else delete entry.source_text;
  return entry;
}

/** What the English block shows: {english} or {note}. */
export function englishBlock(entry, read) {
  if (typeof entry.source_text === "string") return { source_text: entry.source_text };
  return { note: t(read ? DIFFERS : TEXT_NOTE) };
}

/** The search placeholder: English only where rows hold it. */
export function searchHint(rows) {
  return rows.some((r) => typeof r.source_text === "string") ? N_("Search English, translation or id")
    : N_("Search translation or id");
}

/** The note of a check run without the English, or "". */
export function unavailableNote(res) {
  return res?.unavailable?.length ? t(UNAVAILABLE) : "";
}
