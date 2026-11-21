import { el, renderChildren, setText } from "../dom.js";
import { t, errorText, showText } from "../ui.js";
import { loadSource } from "../data.js";
import { unverifiedCount } from "../limits.js";
import { isDraft } from "../draft.js";
import { setToolContext } from "../site-shell.js";
import { workspaceLanguage, patchHint, patchPicker, STAGE_NOTES, gameOptions, startSummary } from "../start.js";
import { TEXT_NOTE, applyEnglish, joinRows } from "../gametext.js";
import { joinedKey, joinedValue, validJoined } from "../editor-cache.js";
import { option, sourceLinks, preferredCatalog, fillCatalogSelect } from "./controls.js";
import { renderEnglish } from "./entry.js";
import { latin, buildGlyphs, withGameText, showSearchHint } from "./game-text.js";
import { applyFilters, fillFilters, step } from "./list.js";
import { $, runtime, joinedCache, state, isSite, DATA } from "./state.js";
import { setCheckStatus, renderRomProgress, showRomState } from "./status.js";
import { configureLayout, engineRules, fontSheets, keyboards, pictures, renderContext, renderTokens, rerun } from "./texts.js";




export async function startCheck() {
  if (state.checkStarted) return;
  state.checkStarted = true;
  setCheckStatus("loading");
  try {
    await runtime.start(new URL(state.manifest.bundle, DATA()).href);
    state.check = runtime.check;
    state.gfx = runtime.gfx;
    state.pythonState = "ready";
    await configureCheck();
  } catch (e) {
    console.error(e);
    state.check = null;
    state.gfx = null;
    state.pythonState = "failed";
    setCheckStatus("failed", String(e.message || e).slice(0, 80));
    state.gfxState = String(e.message || e).slice(0, 200);
    pictures.refresh();
    keyboards.refresh();
    rerun();
  }
}




export async function configureCheck() {
  const check = state.check;
  if (!check || !state.index) return;
  const generation = state.generation, request = state.romGeneration;
  check.ready = check.exact = false;
  setCheckStatus("loading");
  if (!state.english) await readGameText();
  await new Promise((resolve) => setTimeout(resolve, 0));
  if (generation !== state.generation || request !== state.romGeneration || check !== state.check) return;
  await configureLayout();
}




export function forgetRom() {
  state.romGeneration++;
  state.romLoading = false;
  state.romPatch = null;
  state.rom = null;
  state.textRom = null;
  state.english = null;
  renderRomProgress();
  if (state.index) {
    showGameText();
    setupGlyphs();
    configureCheck();
  }
}




export async function loadRom(file, patch = null) {
  const generation = state.generation;
  const request = ++state.romGeneration;
  const game = state.games.find((g) => g.id === state.profile?.id);
  if (!game || !state.index) return;
  if (!patch) state.romPatch = null;
  state.romLoading = true;
  state.romProgress = "Verifying the ROM and any patches…";
  renderRomProgress();
  showRomPatch();
  try {
    const rom = await runtime.readRom(file, { ...game, sha1: state.index.rom.sha1 }, state.sources.get(game.data)?.releases,
      fetch, patch);
    if (generation !== state.generation || request !== state.romGeneration || !state.index) return;
    state.rom = state.textRom = rom;
    state.romPatch = null;
    state.english = null;
    showGameText();
    setupGlyphs();
    await configureCheck();
  } catch (e) {
    if (generation !== state.generation || request !== state.romGeneration || !state.index) return;
    if (e.patchBase) {
      state.romPatch = { file, base: e.patchBase, game, screen: "workspace" };
      showBanner();
    } else showBanner(() => `${patch?.name ?? file.name}: ${errorText(e)}`);
  } finally {
    if (generation === state.generation && request === state.romGeneration) {
      state.romLoading = false;
      renderRomProgress();
      showRomPatch();
    }
  }
}




export function pickGame(id, language = null) {
  state.romGeneration++;
  state.romLoading = false;
  state.romPatch = null;
  state.startReplacing = false;
  $("start-rom-error").hidden = true;
  const game = state.games.find((g) => g.id === id);
  state.pick = { game: game?.id ?? null, language: preferredCatalog(game, language) };
  if (game) {
    if (state.rom && game.sha1 && state.rom.sha1 !== game.sha1) state.rom = null;
    if (state.textRom && state.textRom.game !== game.id) state.textRom = null;
  }
  renderStart();
}




/** Read text only after verifying the original bytes. */
export async function readGameText() {
  const rom = state.textRom;
  if (!rom || !state.check?.englishFn || !state.index || state.ownEnglish || isSite() || isDraft(state.index)) return;
  const generation = state.generation;
  const request = state.romGeneration;
  const index = state.index, rows = state.rows, check = state.check;
  state.romLoading = true;
  state.romProgress = "Extracting the game text…";
  renderRomProgress();
  try {
    const source = await loadSource(index);
    const key = await joinedKey(rom.sha1, index, source);
    const stored = await joinedCache.get(key);
    if (generation !== state.generation || request !== state.romGeneration || state.textRom !== rom) return;
    const cached = validJoined(stored, rows, index.glossary) ? stored : null;
    const read = cached?.source_text ?? await check.english(rom.bytes, source);
    if (generation !== state.generation || request !== state.romGeneration || state.textRom !== rom) return;
    if (!Object.keys(read.texts).length) { // Another game: no line matches
      state.textRom = null;
      const expected = state.index.rom.sha1, profile = state.index.profile;
      showBanner(() => t("{file}: SHA-1 {sha1} is not the base ROM of the editor data ({profile}, expected {expected}).",
        { file: rom.name, sha1: rom.sha1, profile, expected }));
      return;
    }
    state.english = read;
    state.romProgress = "Joining English and glossary data…";
    renderRomProgress();
    showGameText(cached, key);
  } catch (e) {
    if (generation !== state.generation || request !== state.romGeneration || state.textRom !== rom) return;
    showBanner(() => `${rom.name}: ${errorText(e)}`);
  } finally {
    if (generation === state.generation && request === state.romGeneration) {
      state.romLoading = false;
      renderRomProgress();
      renderStart();
    }
  }
}




export function renderStart() {
  showRomPatch();
  renderRomProgress();
  const game = state.games.find((g) => g.id === state.pick.game) ?? null;
  if (!$("start").hidden) setToolContext(game?.id, state.pick.language);
  $("start-games").replaceChildren(...gameOptions(state.games, state.pick.game).map((o) => option(o, () => pickGame(o.id))));
  Object.assign($("start-game-chip"), { hidden: !game, textContent: game?.name ?? "" });
  setText($("start-game-note"), game ? (STAGE_NOTES[game.stage] ? t(STAGE_NOTES[game.stage]) : "")
    : t("Choose the game you play."));
  const step = (id, mode) => {
    $(id).classList.toggle("is-done", mode === "done");
    $(id).classList.toggle("is-active", mode === "active");
    $(id).classList.toggle("is-locked", mode === "locked");
  };
  step("start-game", game ? "done" : "active");
  const lang = workspaceLanguage(game, state.pick.language);
  fillCatalogSelect("start-lang", game, state.pick.language);
  const source = $("start-source");
  source.replaceChildren();
  const profile = game ? state.profiles.find((p) => p.id === game.id) : null;
  if (game && !lang) {
    source.append(el("p", { textContent: t("{game}: no language catalog is available yet.", { game: game.name }) }));
    if (profile) sourceLinks(profile, source);
  }
  source.hidden = !source.childNodes.length;
  const loaded = Boolean(state.textRom);
  step("start-rom", !lang ? "locked" : loaded ? "done" : "active");
  $("start-rom").hidden = game?.stage === "interface"; // Site text editing does not require a ROM.
  $("start-rom-file").hidden = !loaded;
  setText($("start-rom-name"), state.textRom?.name ?? "");
  $("start-replace").disabled = state.romLoading;
  $("start-drop").hidden = Boolean(state.romPatch) || (loaded && !state.startReplacing);
  $("start-replace-cancel").hidden = !(loaded && state.startReplacing);
  $("start-rom-help").hidden = loaded;
  $("start-rom-options").hidden = loaded;
  $("start-browse").disabled = state.romLoading;
  setText($("start-expected"), game?.sha1 ? t("Expected SHA-1 {sha1}", { sha1: game.sha1 }) : "");
  const hint = patchHint(game);
  Object.assign($("start-patch-note"), { hidden: !hint || loaded, textContent: hint });
  if (state.romLoading) showText($("start-drop-title"), "Checking…");
  else if (game) showText($("start-drop-title"), "Drop your {game} ROM here", { game: game.name });
  else showText($("start-drop-title"), "Drop your ROM here");
  $("start-open").disabled = !(game && lang) || state.romLoading || Boolean(state.romPatch);
  setText($("start-summary"), [game?.error ? t(game.error) : startSummary(game, lang, state.textRom),
    state.registry?.skipped?.length ? t("A game could not be loaded. Reload to try again.") : ""].filter(Boolean).join(" "));
}




export function setupGlyphs() {
  buildGlyphs();
  keyboards.refresh();
  fontSheets.refresh();
  showBanner();
}




/** Display a banner with an optionally deferred translation. */
export function showBanner(notice = null) {
  showRomPatch();
  state.notice = notice;
  const error = typeof notice === "function" ? notice() : notice;
  return renderChildren($("banner"), (b) => {
    const exact = latin() !== null;
    b.className = `banner${error ? " error" : exact ? " exact" : ""}`;
    showRomState();
    if (error) b.append(error, " ");
    if (isSite()) {
      b.append(t("Site interface: the texts of this site's pages, the patcher, the Pokédex chrome and this editor. Edits stay in this browser until you export them as JSON; keep every placeholder in braces as it is."));
      b.hidden = false;
      return;
    }
    if (isDraft(state.index)) {
      b.append(t("Draft catalog: edit and export translations. Game previews, encoding and layout checks are not available yet; translation builds remain disabled."), " ");
      if (state.rom?.sha1 === state.index.rom.sha1) {
        b.append(t("Your ROM matches this catalog."), " ",
          el("button", { type: "button", class: "btn sm", textContent: t("Forget the ROM"), on: { click: forgetRom } }));
      } else {
        b.append(el("button", { type: "button", class: "btn sm", textContent: t("Verify your ROM"),
          on: { click: () => $("rom-file").click() } }));
      }
      b.hidden = false;
      return;
    }
    const estimates = unverifiedCount(state.index);
    if (estimates) {
      b.append(`${t("{n} layout values of this language are estimates, to be measured with its text in the game; the limits and widths that use them say so.", { n: estimates })} `);
    }
    if (exact) {
      // The masthead covers ROM state; reserve strips for errors.
      b.hidden = !error && !estimates;
      return;
    }
    const body = el("div", { class: "rom-note-body" });
    body.append(t("No ROM loaded: the glyphs of the language pack are exact, but the Latin glyphs and the letters shared with them exist only in the game ROM. They use project-drawn stand-ins and are measured with a fallback width, so line widths and the check are approximate."), " ");
    if (!state.ownEnglish) body.append(t(TEXT_NOTE), " ");
    const hint = patchHint(state.games.find((g) => g.id === state.profile?.id));
    if (hint) body.append(hint, " ");
    body.append(el("span", { class: "muted", textContent: t("The file is read in this browser and stays local.") }));
    b.append(el("details", { class: "rom-note" },
      el("summary", { textContent: t("Without a ROM, line widths and the check are approximate") }), body));
    b.hidden = false;
  });
}




/** Show loaded English in the list and entry. */
export function showGameText(cached = null, key = null) {
  if (state.ownEnglish) return;
  const generation = state.generation, request = state.romGeneration, english = state.english;
  const texts = english?.texts ?? null;
  const glossary = state.index?.glossary ?? null, fold = state.index?.language_rules?.fold ?? {};
  const current = () => generation === state.generation && request === state.romGeneration && english === state.english;
  applyEnglish(state.rows, texts);
  if (cached) {
    state.rows.forEach((row, i) => {
      row._problems ??= row.problems;
      row.problems = cached.rows[i].problems;
      if (cached.rows[i].terms) row.terms = cached.rows[i].terms;
      else delete row.terms;
    });
  } else if (!texts) joinRows(state.rows, null, glossary, fold);
  const show = () => {
    if (generation !== state.generation || request !== state.romGeneration || english !== state.english) return;
    fillFilters();
    showSearchHint();
    engineRules.relabel();
    showBanner();
    applyFilters();
    if (state.entry) {
      withGameText(state.entry, state.cur);
      renderEnglish(state.entry);
      renderContext(state.entry);
      renderTokens();
      rerun();
    }
  };
  show();
  if (!texts || cached) return;
  const rows = state.rows;
  state.joining = (async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    if (!current()) return;
    const joined = await runtime.join(rows, texts, glossary, fold);
    if (!current()) return;
    state.rows = joined;
    show();
    if (key) await joinedCache.put(key, joinedValue(english, joined));
  })().catch((error) => {
    if (current()) showBanner(() => errorText(error));
  });
}




export function showRomPatch() {
  const pending = state.romPatch;
  for (const [id, screen] of [["start-patch", "start"], ["rom-patch", "workspace"]]) {
    const node = $(id);
    node.hidden = pending?.screen !== screen;
    node.replaceChildren();
    if (node.hidden) continue;
    node.append(patchPicker(pending.base, pending.game, (patch) => {
      if (pending !== state.romPatch) return;
      if (screen === "start") startRom(pending.file, patch);
      else loadRom(pending.file, patch);
    }, () => {
      state.romGeneration++;
      state.romLoading = false;
      state.romPatch = null;
      if (screen === "start") state.startReplacing = false;
      $("start-rom-error").hidden = true;
      renderStart();
      if (state.index) showBanner();
    }, state.romLoading));
  }
}




export async function startRom(file, patch = null) {
  const game = state.games.find((g) => g.id === state.pick.game);
  if (!game) return;
  const request = ++state.romGeneration;
  const generation = state.generation;
  if (!patch) state.romPatch = null;
  state.romLoading = true;
  state.romProgress = "Verifying the ROM and any patches…";
  $("start-rom-error").hidden = true;
  renderStart();
  try {
    const rom = await runtime.readRom(file, game, state.sources.get(game.data)?.releases, fetch, patch);
    if (request !== state.romGeneration || generation !== state.generation) return;
    state.rom = state.textRom = rom;
    state.romPatch = null;
    state.startReplacing = false;
  } catch (e) {
    if (request !== state.romGeneration || generation !== state.generation) return;
    if (e.patchBase) state.romPatch = { file, base: e.patchBase, game, screen: "start" };
    else Object.assign($("start-rom-error"), { hidden: false, className: "note err",
      textContent: `${patch?.name ?? file.name}: ${errorText(e)}` });
  } finally {
    if (request === state.romGeneration && generation === state.generation) {
      state.romLoading = false;
      renderStart();
    }
  }
}
