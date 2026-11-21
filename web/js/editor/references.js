import { errorText, N_, showText, plainText } from "../ui.js";
import { gameHash } from "../registry.js";
import { workspaceLanguage } from "../start.js";
import { formatReference, ObjectReferenceError, parseLookup } from "../objectref.js";
import { entryKey } from "./catalog.js";
import { showStart, showBack } from "./controls.js";
import { applyFilters } from "./list.js";
import { $, local, state, REFERENCE_VIEWS } from "./state.js";
import { engineRules, families, fontSheets, keyboards, pictures, showPlace, terms } from "./texts.js";
import { openWorkspace } from "./workspace.js";



export const REFERENCE_ERRORS = {
  "invalid-reference": N_("Invalid reference"),
  "unknown-game": N_("Unknown game: {game}"),
  "catalog-unavailable": N_("Catalog unavailable: {language}"),
  "workspace-required": N_("Choose a game and catalog language for this ID"),
  "object-not-found": N_("Object not found: {id}"),
};




export function referenceError(error) {
  return { key: REFERENCE_ERRORS[error.code] ?? N_("Could not open reference: {error}"),
    vars: error.params ?? { error: errorText(error) } };
}




export async function copyObjectReference(kind) {
  const id = state.objectReferences.get(kind);
  if (!id || !state.lang) return;
  const value = formatReference({ game: state.profile.id, language: state.lang.code, kind, id }, state.registry);
  const generation = state.generation;
  $("copy-fallback").hidden = true;
  plainText($("copy-status"), "");
  try {
    await navigator.clipboard.writeText(value);
    if (generation === state.generation) showText($("copy-status"), "Reference copied");
  } catch {
    if (generation !== state.generation) return;
    $("copy-fallback").hidden = false;
    $("copy-value").value = value;
    showText($("copy-status"), "Copy this reference");
    $("copy-value").focus();
    $("copy-value").select();
  }
}




export async function locateReference(reference) {
  const { kind, id } = reference;
  let target = id;
  let found = false;
  if (kind === "text") {
    target = await entryKey(id);
    found = Boolean(target);
    if (found) await state.chunks.entry(state.rows[state.byId.get(target)]);
  }
  if (kind === "term") {
    await terms.host.load();
    found = Boolean(terms.term(id));
  }
  if (kind === "family") {
    await families.ensure();
    found = Boolean(families.family(id));
  }
  if (kind === "picture") found = Boolean(pictures.row(id));
  if (kind === "font") found = Boolean(fontSheets.sheet(id));
  if (kind === "glyph") {
    const { sheet, code } = reference;
    if (fontSheets.glyph(sheet, code)) return { view: "fonts", kind, id: sheet, code };
    throw new ObjectReferenceError("object-not-found", { id: `${sheet}/${code}` });
  }
  if (kind === "engine-rule") found = Boolean(engineRules.rule(id));
  if (kind === "keyboard-page") {
    const pages = keyboards.kb?.pages ?? [];
    target = reference.index ?? pages.findIndex((page) => page.name === id);
    if (pages[target]) return { view: "keyboards", kind: "page", id: pages[target].name };
  }
  if (kind === "chat-row" && keyboards.chatRow(id)) return { view: "keyboards", kind: "chat", id };
  if (!found) throw new ObjectReferenceError("object-not-found", { id });
  return { view: REFERENCE_VIEWS[kind], id: target };
}




export async function revealReference(place) {
  const controls = {
    texts: ["search", "f-status", "f-problems", "f-file", "f-map", "f-kind", "f-reach", "f-terms"],
    terms: ["t-search", "t-category", "t-status"],
    families: ["fam-search", "fam-kind", "fam-category"],
    pictures: ["p-search", "p-category", "p-status"],
    enginerules: ["er-search"],
  };
  for (const id of controls[place.view] ?? []) $(id).value = "";
  if (place.view === "texts") applyFilters();
  if (place.view === "terms") terms.applyFilters();
  if (place.view === "families") families.applyFilters();
  if (place.view === "pictures") pictures.applyFilters();
  if (place.view === "enginerules") engineRules.applyFilters();
  await showPlace(place);
  if (place.view === "keyboards" && place.kind === "chat") return;
  if (place.view === "fonts" && place.kind === "glyph") return;
  const kind = Object.keys(REFERENCE_VIEWS).find((key) => REFERENCE_VIEWS[key] === place.view);
  $(`copy-${kind}`).focus();
}




export async function openLookup(prefix) {
  if (local.busy) return;
  const serial = ++state.lookupGeneration;
  const input = $(`${prefix}-reference`);
  input.removeAttribute("aria-invalid");
  plainText($(`${prefix}-lookup-status`), "");
  let switched = false;
  try {
    const workspace = prefix === "start" ? state.pick : { game: state.profile?.id, language: state.lang?.code };
    const reference = parseLookup(input.value, state.registry, workspace);
    if (!REFERENCE_VIEWS[reference.kind] && !["chat-row", "glyph"].includes(reference.kind)) throw new ObjectReferenceError("invalid-reference");
    showText($(`${prefix}-lookup-status`), "Loading…");
    const previous = $("start").hidden ? state.view : null;
    const same = !state.lookupLoading && state.profile?.id === reference.game && state.lang?.code === reference.language;
    const open = async () => {
      const generation = state.generation;
      const place = await locateReference(reference);
      if (serial !== state.lookupGeneration || generation !== state.generation) return;
      if (!switched) history.pushState({ from: previous }, "", location.hash);
      showStart(false);
      await revealReference(place);
      if (serial !== state.lookupGeneration || generation !== state.generation) return;
      showBack();
      $("workspace-reference").removeAttribute("aria-invalid");
      showText($("workspace-lookup-status"), "Opened {id}", { id: reference.id ?? `${reference.sheet}/${reference.code}` });
    };
    if (same) await open();
    else {
      const game = state.games.find((item) => item.id === reference.game);
      if (!workspaceLanguage(game, reference.language)) {
        throw new ObjectReferenceError("catalog-unavailable", { language: reference.language });
      }
      switched = true;
      state.lookupLoading = true;
      showText($("workspace-lookup-status"), "Loading…");
      history.pushState({ from: previous }, "", gameHash(reference.game, reference.language));
      await openWorkspace(reference.game, reference.language, open);
    }
  } catch (error) {
    if (serial !== state.lookupGeneration) return;
    const shown = switched ? "workspace" : prefix;
    const { key, vars } = referenceError(error);
    $(`${shown}-reference`).value = input.value;
    $(`${shown}-reference`).setAttribute("aria-invalid", "true");
    showText($(`${shown}-lookup-status`), key, vars);
    $(`${shown}-reference`).focus();
  } finally {
    if (serial === state.lookupGeneration) state.lookupLoading = false;
  }
}
