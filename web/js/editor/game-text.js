import { t } from "../ui.js";
import { isDraft } from "../draft.js";
import { applyGlossary, searchHint, withEnglish } from "../gametext.js";
import { latinWidths, romFonts } from "../font.js";
import { draftFonts, styleGlyphs } from "../fonts.js";
import { $, state } from "./state.js";




export function latin() {
  if (isDraft(state.index)) return null;
  return state.rom && state.index && state.rom.sha1 === state.index.rom.sha1
    ? latinWidths(state.index.fonts, state.rom.bytes)
    : null;
}




/** The index with browser glyph drafts over the pack's art. */
export function drafted() {
  return draftFonts(state.index, state.glyphEdits?.all() ?? []);
}





export function buildGlyphs() {
  const lat = latin();
  state.glyphs = styleGlyphs(drafted(), lat, lat ? romFonts(state.index.fonts, state.rom.bytes) : null);
}




/** Visible rows need glossary results before background completion. */
export function prepareRow(row) {
  if (!row || state.ownEnglish || !state.english || row.terms) return;
  applyGlossary([row], state.english.texts, state.index?.glossary ?? null, state.index?.language_rules?.fold ?? {});
}




/** The English and glossary terms of the open entry. */
export function withGameText(entry, i) {
  if (state.ownEnglish) return entry;
  prepareRow(state.rows[i]);
  withEnglish(entry, state.english?.texts ?? null);
  if (state.english && state.rows[i]?.terms) entry.terms = state.rows[i].terms;
  else delete entry.terms;
  return entry;
}




/** The search field names the English where rows hold it. */
export function showSearchHint() {
  const key = searchHint(state.rows);
  $("search").dataset.uiPlaceholder = key;
  $("search").placeholder = t(key);
}
