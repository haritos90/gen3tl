import { toolURL } from "../routes.js";
import { el } from "../dom.js";
import { exportDoc, exportName } from "../store.js";
import { t, errorText } from "../ui.js";
import { showBanner } from "./rom.js";
import { $, local, state } from "./state.js";
import { updateEditCount } from "./status.js";
import { field, refreshDraft } from "./texts.js";
import { loadLanguage } from "./workspace.js";




export async function saveLocal() {
  const generation = state.generation;
  const index = state.index, edits = state.edits, chunks = state.chunks;
  const retained = { edits, uploads: state.uploads, termEdits: state.termEdits, glyphEdits: state.glyphEdits,
    keyboardEdits: state.keyboardEdits, ruleEdits: state.ruleEdits, noteEdits: state.noteEdits };
  const rows = new Map(state.rows.map((row) => [row.id, row]));
  try {
    const saving = local.save(index, edits, (id) => chunks.entry(rows.get(id)), state.noteEdits);
    updateEditCount();
    const result = await saving;
    if (generation !== state.generation) return;
    await loadLanguage(index.language, retained);
    showBanner(() => result.notes
      ? t("Saved {n} text drafts and {notes} note proposals. Independent review is still required.",
        { n: result.saved, notes: result.notes })
      : t("Saved {n} text drafts. Independent review is still required.", { n: result.saved }));
    $("banner").hidden = false;
  } catch (error) {
    if (generation === state.generation) {
      showBanner(() => errorText(error));
      $("banner").hidden = false;
    }
  } finally {
    updateEditCount();
  }
}




export function discardDraft() {
  const e = state.entry;
  if (!e || e.owner) return;
  state.edits.remove(e.id);
  field.set(e.translation);
  refreshDraft();
}





export function changesDoc() {
  const order = state.rows.map((r) => r.id);
  const gfx = (state.index.graphics?.inventory ?? []).map((r) => r.id);
  const glossary = state.termsData?.terms.map((t) => t.id) ?? null;
  return exportDoc(state.lang.code, state.index.profile, state.edits.changes(order), state.uploads.changes(gfx),
    state.termEdits.changes(glossary), state.glyphEdits.changes(), state.keyboardEdits.changes(), state.ruleEdits.changes(),
    state.noteEdits.changes());
}




/** Download JSON and return the saved file name. */
export function downloadJson(doc) {
  const name = exportName(state.lang.code);
  const blob = new Blob([JSON.stringify(doc, null, 1)], { type: "application/json" });
  const a = el("a", { href: URL.createObjectURL(blob), download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  return name;
}




export function exportChanges() {
  downloadJson(changesDoc());
}




/** Open draft review before submitting an issue. */
export function submit() {
  location.href = toolURL("submit", state.profile.id, state.lang.code);
}
