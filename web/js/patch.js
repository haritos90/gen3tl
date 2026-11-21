// Apply local patches or verified translation releases.

import { setToolContext, startPage } from "./site-shell.js";
import { el } from "./dom.js";
import { compareVersions, languageName, loadSources } from "./landing.js";
import { digest } from "./patcher-core.js";
import { ROM_LIMIT } from "./patch-engine.js";
import { chosenGame, gameHash, loadRegistry, rememberGame } from "./registry.js";
import { checkSiteBuild, N_, SOURCE_LANGUAGE, chosenLanguage, errorText, formatNumber, onSiteLanguage, showText, t,
  uiError } from "./ui.js";

/** List languages with releases, ordered by name. */
export function languagesOf(releases) {
  return [...new Set(releases.map((r) => r.language))]
    .map((code) => ({ code, name: languageName(code) }))
    .sort((a, b) => a.name.localeCompare(b.name));
}

/** List language releases in newest-first order. */
export const versionsOf = (releases, code) => releases.filter((r) => r.language === code)
  .sort((a, b) => compareVersions(b.version, a.version));

/** Format a byte count for display. */
export function sizeLabel(n) {
  if (n >= 1048576) return t("{n} MiB", { n: formatNumber(+(n / 1048576).toFixed(n % 1048576 ? 1 : 0)) });
  if (n >= 1024) return t("{n} KiB", { n: formatNumber(+(n / 1024).toFixed(n % 1024 ? 1 : 0)) });
  return t("{n} bytes", { n });
}

/** Explain why a ROM fails release checks. */
export function romProblem(size, sha1, release) {
  if (size !== release.source_size) {
    return t("The file has {size}; the original {game} ROM has {expected}. Expected SHA-1 {sha1}.", {
      size: sizeLabel(size), game: release.profile_name, expected: sizeLabel(release.source_size), sha1: release.source_sha1 });
  }
  if (sha1 !== release.source_sha1) {
    return t("SHA-1 {sha1} is not the original {game} ROM. Expected SHA-1 {expected}.",
      { sha1, game: release.profile_name, expected: release.source_sha1 });
  }
  return null;
}

/** Name the download for a release's patch. */
export const patchName = (release) => release.output.replace(/\.gba$/, ".ups");

/** Keep explicit game arrivals on their translation. */
export function patchSource(hasReleases, game, requested = null) {
  return hasReleases ? requested ?? (game ? "translation" : "own") : "own";
}

/** Use the patch stem and the ROM extension. */
export function resultName(rom, patch) {
  const stem = (name) => name.lastIndexOf(".") > 0 ? name.slice(0, name.lastIndexOf(".")) : name;
  return stem(patch) + rom.slice(stem(rom).length);
}

// The module names some formats by their container.
const FORMAT_NAMES = { VCDIFF: "xdelta", "APS-GBA": "APS (GBA)", "APS-N64": "APS (N64)" };

/** Name a patch format as visitors know it. */
export const formatName = (format) => FORMAT_NAMES[format] ?? format;

/** Tell whether a format never carries a checksum of the result. */
export const lacksChecksums = (format) => ["IPS", "EBP", "PPF", "APS-N64"].includes(format);

/** Show only sizes stated by the format. */
export function patchSummary(info) {
  if (!info) return "";
  const facts = [t("Format: {format}", { format: formatName(info.format) })];
  if (info.sourceSize !== null) facts.push(t("Original: {size}", { size: sizeLabel(info.sourceSize) }));
  if (info.resultSize !== null) facts.push(t("Result: {size}", { size: sizeLabel(info.resultSize) }));
  return facts.join(" · ");
}

/** Only inspected patches become chosen files. */
export function patchStep(file, info, error = null) {
  const chosen = Boolean(file && info && !error);
  return { file: chosen ? file : null, meta: chosen ? patchSummary(info) : "",
    problem: error ? t(error.key, error.vars) : "", mode: chosen ? "done" : "active" };
}

/** Read verified public notes for one release. */
export async function loadReleaseNotes(release, base, fetcher = fetch) {
  if (!release.notes) return [];
  const url = new URL(release.notes, base).href;
  const response = await fetcher(url, { credentials: "omit", cache: "no-store" });
  if (!response.ok) throw uiError("Release notes: HTTP {status}.", { status: response.status });
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (await digest(bytes, "SHA-256") !== release.notes_sha256)
    throw uiError("Release notes differ from the release. Reload and try again.");
  const doc = JSON.parse(new TextDecoder().decode(bytes));
  const fields = "language,notes,profile,release_label,schema,version";
  if (!doc || doc.schema !== 1 || Object.keys(doc).sort().join() !== fields ||
      doc.profile !== release.profile || doc.language !== release.language || doc.version !== release.version ||
      typeof doc.release_label !== "string" || !doc.release_label.trim() || !Array.isArray(doc.notes) ||
      doc.notes.some((note) => typeof note !== "string" || !note.trim()))
    throw uiError("Invalid release notes.");
  return doc.notes;
}

/** Render release notes as plain list text. */
export const noteItems = (notes) => notes.map((text) => el("li", { textContent: text }));

const $ = (id) => document.getElementById(id);

const state = {
  game: null, games: [], sources: null, releases: [], base: null, release: null,
  file: null, check: null, generation: 0, notesGeneration: 0, worker: null, url: null,
  notesFailed: false, loadError: null, source: "translation", patchFile: null, patchInfo: null, result: null,
  patchError: null,
};

function setStep(id, mode) {
  const step = $(id);
  step.classList.toggle("is-done", mode === "done");
  step.classList.toggle("is-active", mode === "active");
  step.classList.toggle("is-locked", mode === "locked");
}

function chip(id, cls, text) {
  Object.assign($(id), { className: `chip ${cls}`, textContent: text, hidden: !text });
}

/** Keep status parameters translatable across language changes. */
function status(key, vars = null, error = false) {
  showText($("status"), key, vars);
  $("status").className = `small${error ? " note err" : ""}`;
}

/** Translate the reason a file cannot be patched. */
function problemOf(check) {
  if (!check || check.ok) return null;
  if (check.error) return `${state.file.name}: ${errorText(check.error)}`;
  return romProblem(state.file.size, check.sha1, state.release);
}

function stopWork() {
  if (state.worker) state.worker.terminate();
  state.worker = null;
  if (state.url) URL.revokeObjectURL(state.url);
  state.url = null;
  state.result = null;
  $("download").hidden = true;
  $("download").removeAttribute("href");
  $("cancel").hidden = true;
}

function renderFile(kind, file, meta, problem, dropTitle) {
  const card = $(`${kind}-file-card`);
  card.hidden = !file;
  card.querySelector(".n").textContent = file?.name ?? "";
  card.querySelector(".m").replaceChildren(...(file ? meta : []));
  $(`${kind}-drop`).classList.toggle("compact", Boolean(file));
  $(`${kind}-drop-title`).textContent = file ? t("Drop another file here") : dropTitle;
  $(`${kind}-error`).hidden = !problem;
  $(`${kind}-error`).textContent = problem ?? "";
}

function render() {
  const r = state.release, own = state.source === "own";
  const patch = patchStep(state.patchFile, state.patchInfo, state.patchError);
  const ready = Boolean(state.check?.ok && (own ? state.patchInfo : r));
  $("patch-source").value = state.source;
  $("translation").hidden = own;
  $("patch-download").hidden = own;
  $("patch-options-title").hidden = own;
  $("step-patch").hidden = !own;
  $("rom-step-number").textContent = own ? "2" : "1";
  $("apply-step-number").textContent = own ? "3" : "2";
  showText($("rom-heading"), own ? N_("Your ROM") : N_("Your original ROM"));
  showText($("apply"), own ? N_("Apply patch") : N_("Apply translation"));
  showText($("result-hash-label"), own ? N_("Result SHA-1") : N_("Expected SHA-1"));
  if (own) $("rom-file").removeAttribute("accept");
  else $("rom-file").setAttribute("accept", ".gba,.bin,application/octet-stream");
  setStep("step-patch", patch.mode);
  setStep("step-rom", !own && !r ? "locked" : state.check?.ok ? "done" : "active");
  setStep("step-apply", ready ? "active" : "locked");
  $("apply").hidden = Boolean(state.url);
  $("apply").disabled = !ready || Boolean(state.worker);
  $("rom-expected").hidden = own;
  $("patch-facts").hidden = own;
  $("patch-facts-label").hidden = own;
  renderFile("patch", patch.file, [patch.meta], patch.problem, t("Drop your patch here"));
  const unverified = own && lacksChecksums(state.patchInfo?.format);
  $("unverified-note").hidden = !unverified;
  if (unverified) {
    showText($("unverified-note"), N_("{format} patches carry no checksum of the result, so the result cannot be confirmed."),
      { format: formatName(state.patchInfo.format) });
  }
  if (own || r) {
    for (const id of ["release-summary", "rom-expected", "release-summary-placeholder", "rom-expected-placeholder"])
      $(id).removeAttribute("data-loading");
    $("release-summary-placeholder").hidden = true;
    $("rom-expected-placeholder").hidden = true;
  }
  if (own) {
    const name = state.file && state.patchFile ? resultName(state.file.name, state.patchFile.name) : "";
    $("result-name").textContent = state.result ? `${name} · ${sizeLabel(state.result.size)}` : name;
    $("result-sha1").textContent = state.result?.sha1 ?? "";
  } else if (r) {
    $("release-summary").replaceChildren(t("Requires an unmodified {game} ROM · ", { game: r.profile_name }),
      el("span", { class: "hash", textContent: `SHA-1 ${r.source_sha1}` }));
    Object.assign($("patch-link"), { href: new URL(r.patch, state.base).href, download: patchName(r) });
    showText($("patch-link"), "Download UPS patch");
    $("rom-expected").textContent = t("Expected SHA-1 {sha1}", { sha1: r.source_sha1 });
    $("result-name").textContent = `${r.output} · ${sizeLabel(r.result_size)}`;
    $("result-sha1").textContent = r.result_sha1;
    $("patch-facts").textContent = `${patchName(r)} · ${sizeLabel(r.patch_size)}`;
  }
  const c = state.check;
  if (!state.file || own) chip("rom-chip", "plain", "");
  else if (!c) chip("rom-chip", "plain", t("Checking…"));
  else chip("rom-chip", c.ok ? "ok" : "err", c.ok ? t("Verified") : t("Not the original"));
  const meta = state.file ? [t("{size} · read on this device", { size: sizeLabel(state.file.size) }),
      c?.sha1 ? el("span", {}, " · ", el("span", { class: "hash", textContent: `${c.sha1.slice(0, 8)}…${c.sha1.slice(-8)}` }),
        c.ok ? t(" matches the original") : t(" differs")) : ""] : [];
  renderFile("rom", state.file, meta, problemOf(c), own ? t("Drop your ROM here")
    : r ? t("Drop your original {game} ROM here", { game: r.profile_name }) : t("Drop your original ROM here"));
}

async function showReleaseNotes(release, generation) {
  try {
    const notes = await loadReleaseNotes(release, state.base);
    if (generation !== state.notesGeneration || release !== state.release) return;
    state.notesFailed = false;
    $("release-notes-list").replaceChildren(...noteItems(notes));
    $("release-notes").hidden = !notes.length;
  } catch (e) {
    if (generation !== state.notesGeneration || release !== state.release) return;
    console.error(e);
    state.notesFailed = true;
    notesFailed();
    $("release-notes").hidden = false;
  } finally {
    if (generation === state.notesGeneration && release === state.release) {
      $("release-notes-loading").hidden = true;
      $("release-notes-loading").removeAttribute("data-loading");
    }
  }
}

function notesFailed() {
  $("release-notes-list").replaceChildren(el("li", { textContent: t("Release notes could not be loaded.") }));
}

function chooseRelease() {
  stopWork();
  state.release = state.releases.find((r) => r.language === $("language").value && r.version === $("release").value) ?? null;
  const generation = ++state.notesGeneration;
  state.notesFailed = false;
  $("release-notes").hidden = true;
  $("release-notes-list").replaceChildren();
  history.replaceState(null, "", gameHash(state.game.id, $("language").value));
  setToolContext(state.game.id, $("language").value);
  if (state.file) checkFile(state.file);
  status("");
  render();
  if (state.release) showReleaseNotes(state.release, generation);
}

/** Render release options while preserving the selection. */
function fillVersions() {
  const previous = $("release").value;
  const versions = versionsOf(state.releases, $("language").value);
  $("release").replaceChildren(...versions.map((r, i) =>
    new Option(i ? r.version : t("{version} · latest", { version: r.version }), r.version)));
  if (versions.some((r) => r.version === previous)) $("release").value = previous;
}

function chooseLanguage() {
  fillVersions();
  chooseRelease();
}

/** Explain when the masthead language lacks a release. */
function showLanguageNote() {
  const want = chosenLanguage();
  const missing = Boolean(state.game) && want !== SOURCE_LANGUAGE && !state.releases.some((r) => r.language === want);
  $("language-note").hidden = !missing;
  if (missing) {
    showText($("language-note"), "No {language} translation of {game} is released yet.",
      { language: languageName(want), game: state.game.name });
  }
}

/** Follow masthead language choices with available releases. */
function followLanguage() {
  const want = chosenLanguage();
  if (state.source === "own" || state.worker || $("language").value === want || !state.releases.some((r) => r.language === want)) return;
  $("language").value = want;
  chooseLanguage();
}

async function checkFile(file) {
  const generation = ++state.generation;
  stopWork();
  state.file = file;
  state.check = null;
  render();
  const r = state.release;
  let check;
  try {
    if (state.source === "own") {
      if (file.size > ROM_LIMIT)
        throw uiError("ROM has {size} bytes; the page limit is {limit} bytes.", { size: file.size, limit: ROM_LIMIT });
      check = { ok: true };
    } else if (file.size !== r.source_size) check = { ok: false, sha1: null };
    else {
      const sha1 = await digest(new Uint8Array(await file.arrayBuffer()));
      check = { ok: !romProblem(file.size, sha1, r), sha1 };
    }
  } catch (e) {
    check = { ok: false, sha1: null, error: e };
  }
  if (generation !== state.generation || r !== state.release) return;
  state.check = check;
  if (state.source === "own" && state.patchFile && !state.patchInfo) checkPatch(state.patchFile);
  status(check.ok && state.source === "translation" ? N_("ROM verified. Apply the translation to continue.") : "");
  render();
}

function runWorker(message, done, initialStatus, failed = (error) => status(error.key, error.vars, true)) {
  stopWork();
  const running = new Worker(new URL("./patcher-worker.js", import.meta.url), { type: "module" });
  state.worker = running;
  $("cancel").hidden = false;
  status(initialStatus);
  render();
  running.onmessage = ({ data }) => {
    if (state.worker !== running) return;
    if (data.status) {
      status(data.status);
      return;
    }
    state.worker = null;
    running.terminate();
    $("cancel").hidden = true;
    if (data.error) failed(data);
    else done(data);
    render();
  };
  running.onerror = () => {
    if (state.worker !== running) return;
    checkSiteBuild();
    stopWork();
    failed({ key: N_("The patch worker could not run. Reload this page in a current browser over HTTPS or localhost.") });
    render();
  };
  running.postMessage(message);
}

function checkPatch(file) {
  state.patchFile = file;
  state.patchInfo = null;
  state.patchError = null;
  runWorker({ patchFile: file, inspect: true }, (data) => {
    state.patchInfo = data.info;
    status("");
  }, N_("Reading the patch…"), (error) => {
    state.patchFile = null;
    state.patchError = error;
    status("");
  });
}

function chooseSource(source) {
  ++state.generation;
  stopWork();
  state.source = patchSource(state.games.length > 0, state.game, source);
  status("");
  if (state.file) checkFile(state.file);
  render();
}

function apply() {
  const release = state.release, own = state.source === "own";
  if (!state.check?.ok || !(own ? state.patchInfo : release)) return;
  const name = own ? resultName(state.file.name, state.patchFile.name) : release.output;
  const message = own ? { file: state.file, patchFile: state.patchFile }
    : { file: state.file, release, patchURL: new URL(release.patch, state.base).href };
  runWorker(message, (data) => {
    state.result = { size: data.size, sha1: data.sha1 };
    state.url = URL.createObjectURL(new Blob([data.result], { type: "application/octet-stream" }));
    Object.assign($("download"), { href: state.url, download: name, hidden: false });
    showText($("download"), "Download {file}", { file: name });
    if (own) status(data.verified ? N_("Result confirmed by the checksum in the patch.")
      : N_("Result not confirmed: the patch carries no checksum of the whole result."));
    else status(N_("Verified {version}: the result has SHA-1 {sha1}, as released."),
      { version: release.version, sha1: release.result_sha1 });
    $("download").focus();
  }, own ? N_("Applying the patch…") : N_("Checking the ROM SHA-1…"));
}

function wire() {
  $("patch-source").addEventListener("change", () => chooseSource($("patch-source").value));
  $("game").addEventListener("change", () => {
    const game = state.games.find((g) => g.id === $("game").value);
    if (game) chooseGame(game, $("language").value);
  });
  $("language").addEventListener("change", chooseLanguage);
  $("release").addEventListener("change", chooseRelease);
  for (const kind of ["rom", "patch"]) {
    const pick = () => $(`${kind}-file`).click();
    const card = $(`${kind}-file-card`);
    card.replaceChildren($("file-card").content.cloneNode(true));
    $(`${kind}-browse`).addEventListener("click", pick);
    card.querySelector("button").addEventListener("click", pick);
    const receive = (file) => {
      if (!file) return;
      if (kind === "patch") checkPatch(file);
      else if (state.source === "own" || state.release) checkFile(file);
    };
    $(`${kind}-file`).addEventListener("change", (e) => {
      receive(e.target.files[0]);
      e.target.value = "";
    });
    const drop = $(`${kind}-drop`);
    for (const name of ["dragenter", "dragover", "dragleave", "drop"]) {
      drop.addEventListener(name, (e) => {
        e.preventDefault();
        drop.classList.toggle("is-over", name === "dragenter" || name === "dragover");
        if (name === "drop") receive(e.dataTransfer?.files?.[0]);
      });
    }
  }
  $("apply").addEventListener("click", apply);
  $("cancel").addEventListener("click", () => {
    stopWork();
    const inspecting = state.source === "own" && !state.patchInfo;
    if (inspecting) state.patchFile = null;
    status(inspecting ? N_("Cancelled. Choose a patch to continue.") : N_("Cancelled. Apply again when ready."));
    render();
  });
  window.addEventListener("pagehide", stopWork);
}

/** Relabel the page using held patching state. */
function relabel() {
  if (!state.game) {
    render();
    if (state.loadError) showLoadError(state.loadError);
    return;
  }
  const code = $("language").value;
  for (const option of $("language").options) option.textContent = languageName(option.value);
  $("language").value = code;
  fillVersions();
  render();
  if (state.notesFailed) notesFailed();
  followLanguage();
  showLanguageNote();
}

function showLoadError(e) {
  $("load-error").textContent = errorText(e);
  $("load-error").hidden = false;
  showText($("release-summary"), "No release could be loaded.");
}

/** Reset release state and recheck any loaded ROM. */
function chooseGame(game, language) {
  stopWork();
  ++state.generation;
  state.check = null;
  rememberGame(game.id);
  Object.assign(state, { game, releases: state.sources.get(game.data).releases.filter((r) => r.profile === game.id),
    base: new URL("releases/releases.json", game.data).href });
  $("game").value = game.id;
  const languages = languagesOf(state.releases);
  $("language").replaceChildren(...languages.map((l) => new Option(l.name, l.code)));
  const want = [language, chosenLanguage()].find((code) => languages.some((l) => l.code === code));
  if (want) $("language").value = want;
  $("language").disabled = false;
  $("release").disabled = false;
  chooseLanguage();
  showLanguageNote();
}

async function main() {
  wire();
  onSiteLanguage(relabel);
  await startPage();
  try {
    const registry = await loadRegistry(new URL("data/registry.json", document.baseURI).href);
    const sources = await loadSources(registry);
    const selected = chosenGame(registry, location.hash).game;
    if (sources.get(selected?.data)?.unavailable) throw new Error(sources.get(selected.data).error);
    const releasesOf = (g) => (sources.get(g.data)?.releases ?? []).filter((r) => r.profile === g.id);
    const released = registry.games.filter((g) => releasesOf(g).length);
    const { game, route } = chosenGame(registry, location.hash, globalThis.localStorage, (g) => released.includes(g));
    state.games = released;
    state.sources = sources;
    $("game").replaceChildren(...released.map((g) => new Option(g.name, g.id)));
    $("game").disabled = false;
    state.source = patchSource(released.length > 0, game);
    $("source-choice").hidden = !released.length;
    if (released.length) chooseGame(game ?? released[0], route.language);
    render();
  } catch (e) {
    console.error(e);
    state.loadError = e;
    state.source = "own";
    render();
    showLoadError(e);
  }
}

if (typeof document !== "undefined" && document.getElementById("translation")) main();
