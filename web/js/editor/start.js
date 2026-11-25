import { el, keepPosition } from "../dom.js";
import { t, onSiteLanguage, prepareSiteLanguage, uiError, uiLanguage } from "../ui.js";
import { getJson, projectProfiles } from "../data.js";
import { gameHash, loadRegistry, readRegistry, siteRoute } from "../registry.js";
import { startPage } from "../site-shell.js";
import { workspaceLanguage } from "../start.js";
import { vimStep } from "../listview.js";
import { loadGameTexts, loadSources, gameModel } from "../landing.js";
import { debounce } from "../check.js";
import { saveLocal, discardDraft, exportChanges, submit } from "./actions.js";
import { renderNote, renderHistory } from "./context.js";
import { showBack, showEmpty, showTitle, preferredCatalog, fillCatalogSelect, showSubmit } from "./controls.js";
import { changeReferenceLanguage, renderReference, showWindowChip, showStatus, renderBreakControls, renderBars, renderProblems, renderPreview } from "./entry.js";
import { applyFilters, listNeverShown, relabelFilters, showListCount, sortList, step } from "./list.js";
import { copyObjectReference, openLookup } from "./references.js";
import { forgetRom, loadRom, renderStart, showBanner, startRom } from "./rom.js";
import { $, local, runtime, state, SITE, REFERENCE_VIEWS, remember, recall } from "./state.js";
import { setCheckStatus, renderRomProgress, showCounts, showVersion, updateEditCount } from "./status.js";
import { drawer, engineRules, entries, families, field, fontSheets, keyboards, pictures, renderApproved, renderContext, renderList, renderRules, renderTokens, renderWindow, setView, terms } from "./texts.js";
import { loadProfile, openWorkspace, route, enterStart, showLoadError, renderLoadError } from "./workspace.js";




/**
 * Build entry-flow models from cached registry and sources.
 * Append the site catalog as the final project.
 */
export function buildGames() {
  let site = state.registry.games.find((g) => g.id === SITE) ?? null;
  for (const [data, source] of site ? [] : state.sources) {
    const languages = [...new Set((source.manifest?.languages ?? []).filter((l) => l.profile === SITE).map((l) => l.code))];
    if (!languages.length || !source.manifest.profiles?.some((p) => p.id === SITE && p.support === "interface")) continue;
    site = { id: SITE, name: "", stage: "interface", engine: "unspecified", languages, data, repository: null };
    state.registry.games.push(site);
    break;
  }
  if (site) site.name = t("Site interface");
  state.games = state.registry.games.map((g) => ({ ...gameModel(g, state.sources.get(g.data)?.manifest,
    state.sources.get(g.data)?.releases ?? []), error: state.sources.get(g.data)?.error }));
}




export async function changeCatalog() {
  if (local.busy || !state.profile) return;
  const game = state.games.find((g) => g.id === state.profile.id);
  const code = $("catalog-language").value;
  const route = siteRoute(location.hash, state.registry);
  if (code === state.lang?.code && route.language === code) return;
  if (!workspaceLanguage(game, code)) {
    fillCatalogSelect("catalog-language", game, state.lang?.code);
    return;
  }
  history.pushState(null, "", gameHash(state.profile.id, code, ...route.tail));
  await keepPosition(() => loadProfile(state.profile.id, code).catch(showLoadError));
}





export function changeStartCatalog() {
  const game = state.games.find((g) => g.id === state.pick.game);
  if (!workspaceLanguage(game, $("start-lang").value)) return;
  state.pick.language = $("start-lang").value;
  remember(`lang:${game.id}`, state.pick.language);
  renderStart();
}




/** Relabel the editor without replacing its catalog. */
export function followLanguage() {
  relabel();
}




/** Relabel held page data without refetching. */
export function relabel() {
  if (state.registry && state.sources) buildGames();
  if (!$("start").hidden) renderStart();
  showTitle();
  if (state.loadError) {
    renderLoadError();
    return;
  }
  setCheckStatus(state.checkState, state.checkDetail);
  renderRomProgress();
  showVersion();
  showSubmit();
  if (!state.index) {
    if (state.empty) showEmpty();
    showCounts();
    return;
  }
  showBanner(state.notice);
  relabelFilters();
  renderReference();
  showListCount();
  entries.render();
  updateEditCount();
  const entry = state.entry;
  if (entry) {
    showStatus();
    showWindowChip(entry);
    renderBreakControls(entry);
    renderTokens();
    renderRules(entry);
    renderWindow(entry);
    renderContext(entry);
    renderApproved(entry);
    renderNote(entry);
    renderHistory(entry);
    if (state.result) {
      renderBars(entry, state.result);
      renderProblems(state.result);
      renderPreview(entry, state.result);
    }
  }
  pictures.relabel?.();
  terms.relabel?.();
  families.relabel?.();
  keyboards.relabel();
  fontSheets.relabel?.();
  engineRules.relabel();
  showBack();
}




/** Keep drafts while returning to project selection. */
export function returnToStart(event) {
  if (event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  if (local.busy) return;
  history.pushState(null, "", "#start");
  enterStart();
}




/** Load the registry. */
export async function registryOf() {
  const url = new URL("data/registry.json", document.baseURI).href;
  try {
    return await loadRegistry(url);
  } catch (e) {
    if (!/HTTP 404/.test(String(e.message))) throw e;
    const manifest = await getJson(new URL("data/languages.json", document.baseURI).href);
    return readRegistry({
      schema: 1,
      games: (manifest.profiles ?? []).filter((p) => p.support !== "interface").map((p) => ({ id: p.id, name: p.name, stage: ["translation", "catalog",
        "discovery"].includes(p.support) ? p.support : "legacy", engine: p.engine, data: "./",
      languages: [...new Set((manifest.languages ?? []).filter((l) => l.profile === p.id).map((l) => l.code))] })),
    }, url);
  }
}




export function wireStart() {
  $("start-lang").addEventListener("change", changeStartCatalog);
  $("start-open").addEventListener("click", () => {
    const { game, language } = state.pick;
    if (!workspaceLanguage(state.games.find((g) => g.id === game), language)) return;
    history.pushState(null, "", gameHash(game, language));
    openWorkspace(game, language).catch(showLoadError);
  });
  $("start-replace").addEventListener("click", () => {
    state.startReplacing = true;
    $("start-rom-error").hidden = true;
    renderStart();
    $("start-browse").focus();
  });
  $("start-replace-cancel").addEventListener("click", () => {
    state.romGeneration++;
    state.romLoading = false;
    state.romPatch = null;
    state.startReplacing = false;
    $("start-rom-error").hidden = true;
    renderStart();
    $("start-replace").focus();
  });
  const pick = () => $("start-file").click();
  const input = el("input", { id: "start-file", type: "file", accept: ".gba,.bin,application/octet-stream", hidden: true });
  $("start-drop").after(input);
  $("start-browse").addEventListener("click", pick);
  input.addEventListener("change", (e) => {
    const f = e.target.files[0];
    if (f) startRom(f);
    e.target.value = "";
  });
  const drop = $("start-drop");
  for (const name of ["dragenter", "dragover"]) {
    drop.addEventListener(name, (e) => {
      e.preventDefault();
      drop.classList.add("is-over");
    });
  }
  for (const name of ["dragleave", "drop"]) {
    drop.addEventListener(name, (e) => {
      e.preventDefault();
      drop.classList.remove("is-over");
    });
  }
  drop.addEventListener("drop", (e) => {
    const f = e.dataTransfer?.files?.[0];
    if (f) startRom(f);
  });
}




export function wireObjectReferences() {
  for (const prefix of ["start", "workspace"]) $(`${prefix}-lookup`).addEventListener("submit", (event) => {
    event.preventDefault();
    openLookup(prefix);
  });
  for (const kind of Object.keys(REFERENCE_VIEWS)) $(`copy-${kind}`).addEventListener("click", () => copyObjectReference(kind));
}




export function wire() {
  wireStart();
  wireObjectReferences();
  $("list").addEventListener("scroll", () => requestAnimationFrame(renderList));
  window.addEventListener("resize", () => renderList());
  const refilter = debounce(applyFilters, 150);
  $("search").addEventListener("input", refilter);
  for (const id of ["f-status", "f-problems", "f-file", "f-map", "f-kind", "f-reach", "f-terms"]) {
    $(id).addEventListener("change", applyFilters);
  }
  $("list-sort").addEventListener("change", sortList);
  $("catalog-language").addEventListener("change", changeCatalog);
  $("reference-language").addEventListener("change", changeReferenceLanguage);
  $("sb-never-list").addEventListener("click", listNeverShown);
  $("rom-button").addEventListener("click", () => {
    if (state.english || (state.rom && state.index && state.rom.sha1 === state.index.rom.sha1)) forgetRom();
    else $("rom-file").click();
  });
  $("rom-file").addEventListener("change", (e) => {
    const f = e.target.files[0];
    if (f) loadRom(f);
    e.target.value = "";
  });
  $("export").addEventListener("click", exportChanges);
  $("save-local").addEventListener("click", saveLocal);
  $("view-texts").addEventListener("click", () => setView("texts"));
  $("view-pictures").addEventListener("click", () => setView("pictures"));
  pictures.wire();
  $("view-terms").addEventListener("click", () => setView("terms"));
  terms.wire();
  $("view-families").addEventListener("click", () => setView("families"));
  families.wire();
  $("view-keyboards").addEventListener("click", () => setView("keyboards"));
  keyboards.wire();
  $("view-fonts").addEventListener("click", () => setView("fonts"));
  fontSheets.wire();
  $("submit").addEventListener("click", submit);
  $("go-back").addEventListener("click", () => history.back());
  $("prev").addEventListener("click", () => step(-1));
  $("next").addEventListener("click", () => step(1));
  $("revert").addEventListener("click", discardDraft);
  $("zoom").addEventListener("change", (e) => {
    state.zoom = Number(e.target.value);
    remember("zoom", e.target.value);
    if (state.entry && state.result) renderPreview(state.entry, state.result);
  });
  for (const b of document.querySelectorAll("[data-break]")) {
    b.addEventListener("mousedown", (e) => e.preventDefault());
    b.addEventListener("click", () => field.insertBreak(b.dataset.break));
  }
  $("list").addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      step(e.key === "ArrowDown" ? 1 : -1);
    } else if (e.key === "Enter") $("field").focus();
  });
  $("show-list").addEventListener("click", () => drawer($("app").dataset.drawer === "list" ? null : "list"));
  $("show-ctx").addEventListener("click", () => drawer($("app").dataset.drawer === "ctx" ? null : "ctx"));
  for (const b of document.querySelectorAll("[data-drawer-close]")) b.addEventListener("click", () => drawer(null));
  $("link-start").addEventListener("click", returnToStart);
  window.addEventListener("hashchange", route);
  window.addEventListener("popstate", route);
  window.addEventListener("pagehide", () => runtime.close());
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && $("app").dataset.drawer) drawer(null);
    if (!state.lang || !$("start").hidden) return;
    if (e.altKey && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      e.preventDefault();
      const views = { pictures: (d) => pictures.step(d), terms: (d) => terms.step(d), families: (d) => families.step(d),
        keyboards: (d) => keyboards.step(d), fonts: (d) => fontSheets.step(d), enginerules: (d) => engineRules.step(d) };
      (views[state.view] ?? step)(e.key === "ArrowDown" ? 1 : -1);
    }
    const d = vimStep(e);
    if (d) {
      e.preventDefault();
      const views = { pictures: (n) => pictures.step(n), terms: (n) => terms.step(n), families: (n) => families.step(n),
        keyboards: (n) => keyboards.step(n), fonts: (n) => fontSheets.step(n), enginerules: (n) => engineRules.step(n) };
      (views[state.view] ?? step)(d);
    }
  });
}



export async function main() {
  // the games' rule texts arrive with the interface
  prepareSiteLanguage((code) => loadGameTexts(state.registry?.games, code));
  await startPage();
  onSiteLanguage(followLanguage);
  wire();
  await local.connect();
  const z = recall("zoom");
  if (z) [state.zoom, $("zoom").value] = [Number(z), z];
  try {
    state.registry = await registryOf();
    state.sources = await loadSources({ games: [{ data: new URL("data/", document.baseURI).href }, ...state.registry.games] });
    buildGames();
    if (!state.registry.games.length) throw uiError("the registry lists no game");
    await loadGameTexts(state.registry.games, uiLanguage());
    const first = state.sources.get(state.registry.games[0].data);
    if (first?.manifest) {
      state.base = state.registry.games[0].data;
      state.manifest = first.manifest;
      state.profiles = projectProfiles(state.manifest);
    }
  } catch (e) {
    showLoadError(e);
    setCheckStatus("failed", t("no data"));
    return;
  }
  if (location.hash && location.hash !== "#start") {
    route();
    return;
  }
  const profile = recall("profile");
  const game = state.games.find((g) => g.id === profile);
  const language = preferredCatalog(game);
  if (!location.hash && workspaceLanguage(game, language)) {
    history.replaceState(null, "", gameHash(game.id, language));
    openWorkspace(game.id, language).catch(showLoadError);
    return;
  }
  enterStart();
}
