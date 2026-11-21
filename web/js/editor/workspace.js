import { setText } from "../dom.js";
import { Edits, GlyphEdits, KeyboardEdits, NoteEdits, PictureEdits, RuleEdits, TermEdits, WorkspaceSettings } from "../store.js";
import { t, errorText, N_, showText, plainText, uiError } from "../ui.js";
import { tableKey, getJson, loadIndex, verifyProjectIndex, dirOf, projectProfiles, placeOf, Chunks, groupsOf } from "../data.js";
import { gameHash, siteRoute, pokedexRedirect, rememberGame } from "../registry.js";
import { setPlaces, setReasons } from "../checktext.js";
import { isDraft } from "../draft.js";
import { workspaceLanguage } from "../start.js";
import { shipsEnglish } from "../gametext.js";
import { Codec } from "../font.js";
import { aliases } from "./catalog.js";
import { showStart, showBack, showEmpty, showTitle, preferredCatalog, fillCatalogSelect, showSubmit } from "./controls.js";
import { fillReferenceLanguages, renderReference } from "./entry.js";
import { buildGlyphs, showSearchHint } from "./game-text.js";
import { applyFilters, fillFilters } from "./list.js";
import { startCheck, configureCheck, pickGame, showBanner, showRomPatch } from "./rom.js";
import { $, local, state, SITE, VIEWS, isSite, DATA, REFERENCE_VIEWS, setObjectReference, remember, recall } from "./state.js";
import { setCheckStatus, showCounts, showVersion, updateEditCount } from "./status.js";
import { engineRules, families, fontSheets, keyboards, openId, openRow, pictures, renderList, setView, showPlace, terms, viewHash } from "./texts.js";




/** Clear the workspace and show a translatable message. */
export function clearProject(message) {
  state.generation++;
  state.configureGeneration++;
  state.checkRequest++;
  state.romLoading = false;
  state.romPatch = null;
  showRomPatch();
  state.lang = null;
  state.index = null;
  state.entry = null;
  setObjectReference("text", null);
  $("copy-fallback").hidden = true;
  plainText($("copy-status"), "");
  state.result = null;
  state.rows = [];
  state.filtered = [];
  state.byId = new Map();
  state.cur = -1;
  state.termsData = null;
  state.english = null;
  state.ownEnglish = false;
  state.edits = null;
  state.settings = null;
  state.reference = null;
  state.referenceGeneration++;
  renderReference();
  state.uploads = null;
  state.termEdits = null;
  state.keyboardEdits = null;
  state.pokedex = null;
  pictures.reset();
  terms.reset();
  families.reset();
  keyboards.reset();
  fontSheets.reset();
  engineRules.reset();
  $("banner").replaceChildren();
  $("banner").hidden = true;
  setText($("edit-count"), "0");
  state.empty = message;
  showEmpty();
  $("project-empty").hidden = false;
  for (const view of VIEWS) {
    $(view).hidden = true;
    $(`view-${view}`).disabled = true;
  }
  for (const id of ["rom-button", "export", "submit"]) $(id).disabled = true;
  $("save-local").disabled = true;
  showCounts();
  showBack();
  setCheckStatus("idle");
}




/** Drafts under older inventory ids move to the keys. */
export async function migrateEdits() {
  const edits = state.edits;
  const old = [...edits.map.keys()].filter((id) => !state.byId.has(id));
  if (!old.length) return;
  const generation = state.generation;
  const keys = await aliases();
  if (generation !== state.generation) return;
  let moved = 0;
  for (const id of old) {
    const key = keys.get(id) ?? tableKey(id);
    if (key !== id && state.byId.has(key) && !edits.map.has(key)) {
      edits.map.set(key, edits.map.get(id));
      edits.map.delete(id);
      moved++;
    }
  }
  if (moved) {
    edits.save();
    updateEditCount();
    renderList();
  }
}




export async function loadProfile(id, wantedLanguage = null, locate = null) {
  if (local.busy) return;
  const profile = state.profiles.find((p) => p.id === id);
  if (!profile) throw uiError("Unknown ROM profile: {id}", { id });
  const game = state.games.find((g) => g.id === id);
  wantedLanguage ??= preferredCatalog(game);
  state.profile = profile;
  remember("profile", id);
  if (id !== SITE) rememberGame(id); // Other pages accept game projects only.
  fillCatalogSelect("catalog-language", game, wantedLanguage);
  showTitle();
  if (!profile.languages.length) {
    clearProject({ key: profile.support === "discovery"
      ? N_("{game}: discovery support only. Translation catalogs are not available yet.")
      : N_("{game}: no language catalog is available yet."), vars: { game: profile.name }, links: profile });
    state.titleLanguage = wantedLanguage;
    showTitle();
    history.replaceState(null, "", gameHash(id, wantedLanguage));
    showVersion();
    return;
  }
  if (wantedLanguage && !profile.languages.some((l) => l.code === wantedLanguage)) {
    clearProject({ key: N_("{game}: no {language} catalog is available yet."), vars: { game: profile.name },
      language: wantedLanguage });
    state.titleLanguage = wantedLanguage;
    showTitle();
    history.replaceState(null, "", gameHash(id, wantedLanguage));
    showVersion();
    return;
  }
  remember(`lang:${id}`, wantedLanguage);
  await loadLanguage(wantedLanguage, null, locate);
}




export async function loadLanguage(code, retained = null, locate = null) {
  if (local.busy) return;
  const profile = state.profile;
  const lang = profile.languages.find((l) => l.code === code);
  if (!lang) throw uiError("No catalog for {project}", { project: `${profile.id}/${code}` });
  for (const kind of Object.keys(REFERENCE_VIEWS)) setObjectReference(kind, null);
  $("copy-fallback").hidden = true;
  plainText($("copy-status"), "");
  if (locate) for (const view of VIEWS) $(view).hidden = true;
  const switching = Boolean(state.index && state.lang && state.lang.code !== code);
  if (switching) {
    state.generation++;
    state.empty = null;
  } else {
    clearProject({ key: N_("Loading {game} / {language}…"), vars: { game: isSite() ? t("Site interface") : profile.name },
      language: code });
    setText($("count"), t("Loading…"));
  }
  const generation = state.generation;
  state.titleLanguage = code;
  showTitle();
  let index;
  try {
    index = await loadIndex(new URL(lang.index, DATA()).href);
  } catch (e) {
    if (generation !== state.generation) return;
    throw e;
  }
  if (generation !== state.generation) return;
  verifyProjectIndex(index, profile.id, code);
  state.lang = lang;
  state.index = index;
  setReasons(index.check_rules?.reasons);
  setPlaces(index.where_texts);
  showTitle();
  if (!state.check) setCheckStatus(state.pythonState === "loading" ? "loading" : "failed");
  $("project-empty").hidden = true;
  for (const view of VIEWS) $(`view-${view}`).disabled = false;
  if (isDraft(index)) {
    $("view-pictures").disabled = true;
    $("view-terms").disabled = true;
    $("view-families").disabled = true;
    $("view-fonts").disabled = true;
    state.view = "texts";
  }
  $("view-keyboards").disabled = !index.keyboard;
  for (const id of ["rom-button", "export"]) $(id).disabled = false;
  showSubmit();
  showVersion();
  state.rows = index.list;
  state.ownEnglish = shipsEnglish(index);
  showSearchHint();
  state.byId = new Map(index.list.map((r, i) => [r.id, i]));
  state.groups = groupsOf(index.chunks);
  state.chunks = new Chunks(new URL(dirOf(lang.index), DATA()).href, index);
  state.codec = new Codec(index.codec, index.charmap, index.language_rules);
  state.edits = retained?.edits ?? new Edits(state.storage, lang.code, index.profile);
  state.edits.index = index;
  state.ruleEdits = retained?.ruleEdits ?? new RuleEdits(state.storage, lang.code, index.profile);
  state.settings = new WorkspaceSettings(state.storage, lang.code, index.profile);
  state.reference = null;
  state.referenceGeneration++;
  fillReferenceLanguages();
  renderReference();
  state.aliases = null;
  migrateEdits().catch(() => {});
  for (const row of index.list) if (row.owner) state.edits.remove(row.id);
  state.uploads = retained?.uploads ?? new PictureEdits(state.storage, lang.code, index.profile);
  state.termEdits = retained?.termEdits ?? new TermEdits(state.storage, lang.code, index.profile, index.term_columns);
  state.noteEdits = retained?.noteEdits ?? new NoteEdits(state.storage, lang.code, profile.id);
  state.glyphEdits = retained?.glyphEdits ?? new GlyphEdits(state.storage, lang.code, index.profile);
  state.keyboardEdits = retained?.keyboardEdits ?? new KeyboardEdits(state.storage, lang.code, index.profile);
  state.termsData = null;
  $("field").lang = lang.code;
  buildGlyphs();
  showBanner();
  fillFilters();
  updateEditCount();
  const route = siteRoute(location.hash, state.registry);
  const dexPage = pokedexRedirect(route, profile.id, lang.code); // Embedded-view links redirect to the standalone Pokédex page.
  if (dexPage) {
    location.replace(dexPage);
    return;
  }
  const place = route.game === profile.id && route.language === lang.code ? placeOf(route.tail) : { view: null, id: null };
  const wanted = (view) => (place.view === view ? place.id : null);
  const configuring = configureCheck();
  terms.reset(wanted("terms"));
  families.reset(wanted("families"));
  fontSheets.reset(wanted("fonts"), !locate && !(place.view === "fonts" && place.kind === "glyph"));
  engineRules.reset(wanted("enginerules"));
  await configuring;
  if (generation !== state.generation) return;
  pictures.reset(wanted("pictures"), !locate);
  keyboards.reset(wanted("keyboards"), !locate && !(place.view === "keyboards" && place.kind));
  applyFilters();
  if (locate) {
    state.cur = -1;
    state.entry = null;
    setObjectReference("text", null);
    for (const view of VIEWS) $(view).hidden = true;
    await locate();
    showCounts();
    showBack();
    return;
  }
  if ((place.view === "keyboards" && place.kind) || (place.view === "fonts" && place.kind === "glyph")) {
    await showPlace(place);
    if (state.view === place.view && state.filtered[0] !== undefined) await openRow(state.filtered[0], false);
  }
  else {
    const named = wanted("texts") ?? "";
    const at = state.byId.has(named) ? state.byId.get(named) : state.filtered[0];
    if (at !== undefined) await openRow(at, !switching);
    if (named && !state.byId.has(named)) openId(named);
    setView(place.view && place.view !== "texts" ? place.view : state.view, !switching);
  }
  showCounts();
  showBack();
}




export async function openWorkspace(gameId, language, locate = null) {
  showStart(false);
  state.startReplacing = false;
  const game = state.games.find((g) => g.id === gameId);
  await useGame(game);
  if (state.rom && state.rom.sha1 !== game?.sha1) state.rom = null;
  if (state.textRom && state.textRom.game !== gameId) state.textRom = null;
  if (gameId !== SITE) startCheck(); // Site text has no game layout constraints.
  await loadProfile(gameId, language, locate);
}




/** Open the entry flow or workspace from the hash. */
export async function route() {
  if (local.busy) return;
  const loadingReference = state.lookupLoading;
  state.lookupGeneration++;
  state.lookupLoading = false;
  if (loadingReference) state.generation++;
  if (location.hash === "#start") {
    if ($("start").hidden) enterStart();
    return;
  }
  const r = siteRoute(location.hash, state.registry);
  if (!r.game) {
    if (!location.hash) return;
    const game = location.hash.replace(/^#/, "").split("/").filter(Boolean)[0] ?? "";
    routeError(game);
    return;
  }
  const game = state.games.find((g) => g.id === r.game);
  if (!game || (r.language && !workspaceLanguage(game, r.language))) {
    routeError(r.game, game ? r.language : null);
    return;
  }
  if (!r.language) {
    enterStart(r.game);
    return;
  }
  if (!loadingReference && state.profile?.id === r.game && state.lang?.code === r.language && $("start").hidden) {
    const dexPage = pokedexRedirect(r, r.game, r.language);
    if (dexPage) location.replace(dexPage);
    else if (location.hash !== viewHash()) await showPlace(placeOf(r.tail));
    showBack();
    return;
  }
  await openWorkspace(r.game, r.language).catch(showLoadError);
}




export function enterStart(preselect = null) {
  const id = preselect ?? state.profile?.id ?? recall("profile") ?? state.registry?.default;
  const game = state.games.some((g) => g.id === id) ? id : null;
  const language = state.profile?.id === game ? state.lang?.code : null;
  showStart(true);
  pickGame(game, language);
}




export function routeError(game, language = null) {
  enterStart(game);
  const error = $("start-error");
  if (language) showText(error, "Catalog unavailable: {language}", { language });
  else showText(error, "Unknown game: {game}", { game });
  error.hidden = false;
}




export function showLoadError(e) {
  console.error(e);
  state.loadError = e;
  showStart(false);
  renderLoadError();
}




export function renderLoadError() {
  const b = $("banner");
  b.hidden = false;
  $("project-empty").hidden = true;
  b.className = "banner error";
  setText(b, t("No editor data: {error}. Generate it with gen3tl web data --split and gen3tl web bundle, then serve the page with gen3tl web server start --page site.",
    { error: errorText(state.loadError) }));
  setText($("count"), "");
}




/** Load a game's manifest and profiles. */
export async function useGame(game) {
  const base = game?.data ?? new URL("data/", document.baseURI).href;
  if (state.base === base && state.manifest) return;
  const source = state.sources.get(base);
  state.base = base;
  state.manifest = source?.manifest ?? await getJson(new URL("languages.json", base).href);
  state.profiles = projectProfiles(state.manifest);
}
