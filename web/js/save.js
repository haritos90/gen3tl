// Convert saves locally using published release name tables.
// Conversion matches the toolkit for identical inputs.

import { setToolContext, startPage } from "./site-shell.js";
import { startPython } from "./check.js";
import { el } from "./dom.js";
import { compareVersions, languageName, loadSources } from "./landing.js";
import { digest } from "./patcher-core.js";
import { chosenGame, gameHash, loadRegistry, pickerURL, rememberGame } from "./registry.js";
import { N_, SOURCE_LANGUAGE, chosenLanguage, errorText, formatNumber, guideFor, onSiteLanguage, plainText,
  rememberSiteLanguage, showText, t, textParts, uiError, useSiteLanguage } from "./ui.js";

const GLUE = `
import json
import sys
if "/bundle" not in sys.path:
    sys.path.insert(0, "/bundle")
from gen3tl.verify import savefile

with open("/bundle/save_layouts.json", encoding="utf-8") as fh:
    _LAYOUTS = json.load(fh)
_out = None


def save_convert(data, profile, names_json, back, requested_json, output, tables_json):
    global _out
    _out = None
    names = json.loads(names_json)
    savefile.SAVE_RULES = names["rules"]
    src, dst = savefile.names_of(names["source"]), savefile.names_of(names["result"])
    if back:
        src, dst = dst, src
    tables = [savefile.names_of(table[side]) for text in json.loads(tables_json)
              for table in [json.loads(text)] for side in ("source", "result")]
    requested = [tuple(x) for x in json.loads(requested_json)]
    try:
        report, _out = savefile.browser_convert(bytes(data.to_py()), _LAYOUTS[profile], src, dst,
                                                names["charmap"], {}, requested, output, tables)
    except savefile.SaveError as e:
        return json.dumps({"error": str(e)})
    return json.dumps(report, ensure_ascii=False)


def save_edit(data, profile, names_json, translated, requested_json, output, tables_json):
    global _out
    _out = None
    names = json.loads(names_json)
    savefile.SAVE_RULES = names["rules"]
    tables = [savefile.names_of(table[side]) for text in json.loads(tables_json)
              for table in [json.loads(text)] for side in ("source", "result")]
    requested = [tuple(x) for x in json.loads(requested_json)]
    try:
        report, _out = savefile.browser_edit(bytes(data.to_py()), _LAYOUTS[profile], names["charmap"], {},
                                             bool(translated), requested, output, tables)
    except savefile.SaveError as e:
        return json.dumps({"error": str(e)})
    return json.dumps(report, ensure_ascii=False)


def save_matches(data, profile, tables_json):
    tables = [json.loads(t) for t in json.loads(tables_json)]
    try:
        save = savefile.SaveFile(bytes(data.to_py()), savefile.Layout.from_dict(_LAYOUTS[profile]))
    except savefile.SaveError as e:
        return json.dumps({"error": str(e)})
    sets = [savefile.names_of(tables[0]["source"])] + [savefile.names_of(t["result"]) for t in tables]
    return json.dumps({"matches": savefile.default_matches(save, sets)})


def save_output():
    return _out
`;

export const ORIGINAL = "original";
export const SAME = "same";

/** List original and released conversion targets, newest first. */
export function sidesOf(releases, gameName = null) {
  const named = releases.filter((r) => r.names)
    .sort((a, b) => a.language.localeCompare(b.language) || compareVersions(b.version, a.version));
  const originalName = gameName ?? releases.find((r) => r.profile_name)?.profile_name ?? "Original game";
  return [{ id: ORIGINAL, label: t("{game} · English", { game: originalName }) },
    ...named.map((r) => ({ id: `${r.language}@${r.version}`,
      label: t("{language} · translation {version}", { language: languageName(r.language), version: r.version }),
      release: r }))];
}

/** Resolve conversions from the original or for the source game. */
export function pairOf(from, to, sides) {
  const side = (id) => sides.find((s) => s.id === id);
  if (to === SAME && side(from)) {
    const release = side(from).release ?? sides.find((s) => s.release)?.release;   // English names and letters
    return release ? { release, edit: true, translated: from !== ORIGINAL } : null;
  }
  if (from === ORIGINAL && side(to)?.release) return { release: side(to).release, back: false };
  if (to === ORIGINAL && side(from)?.release) return { release: side(from).release, back: true };
  return null;
}

/** Name output for its target and selected extension. */
export function outputName(name, pair, output = "original") {
  if (!["original", "sav", "srm"].includes(output)) throw new Error(`Unknown save output format: ${output}`);
  const original = name.match(/\.[A-Za-z][A-Za-z0-9]*$/)?.[0] ?? ".sav";
  const ext = output === "original" ? original : `.${output}`;
  const stem = (name.endsWith(original) ? name.slice(0, -original.length) : name) || "save";
  return `${stem}-${pair.edit ? "edited" : pair.back ? "en" : pair.release.language}${ext}`;
}

/** The game whose default names match the most Pokémon. */
export function detectedSide(matches, ids) {
  const best = Math.max(0, ...matches);
  return best > 0 && matches.filter((m) => m === best).length === 1 ? ids[matches.indexOf(best)] : null;
}

/** The side a detected save is read for. */
export function readSide(detected, chosen, sides) {
  const language = (id) => sides.find((s) => s.id === id)?.release?.language;
  return detected && language(detected) === language(chosen) ? chosen : detected;
}

/** The detection note for the read side; null before detection. */
export function detectionText(side, sides) {
  if (side === undefined) return null;
  const game = sides.find((s) => s.id === side)?.label;
  return game
    ? t("The default names in this save are those of {game}, so it is read for that game. Choose another game above if the save comes from another one.", { game })
    : t("No default name in this save tells the games apart: choose the game it was made in above.");
}

/** The line under the drop zone. */
export const dropLine = () => N_("or {browse}. The converted file downloads separately; the original stays as it is.");

/** The note under the names table. */
export function namesNote(report, edit) {
  if (!report) return "";
  return edit ? t("Only custom Pokémon nicknames are editable. Default species names stay unchanged.")
    : t("Only custom Pokémon nicknames are editable. Default species names convert automatically.");
}

/** The containers the converter reads, by the kind it reports. */
export const CONTAINER_NAMES = {
  raw: N_("raw save"),
  clock: N_("raw save with clock data"),
  mister: N_("MiSTer save with clock data"),
  meteor: N_("meteor save RAM"),
  libretro: N_("VBA Next or Beetle GBA save RAM"),
  padded: N_("padded raw save"),
  bizhawk: N_("BizHawk 1.x battery file"),
  sharkport: N_("GameShark, Action Replay or Xploder file"),
  gsv: N_("GameShark SP file"),
  nocash: N_("NO$GBA save file"),
  rzip: N_("RetroArch compressed save"),
};

export const KIND_NOTES = {
  default: N_("default name, converted"),
  kept: N_("kept as it is"),
  transliterated: N_("written in Latin letters"),
  renamed: N_("renamed"),
  over: N_("too long for the game: shorten it"),
};

export const GROUP_KINDS = [N_("trainer record"), N_("rival"), N_("mail original trainer"),
  N_("original trainer of a Pokémon"), N_("mail author"), N_("link record"), N_("box name"), N_("Pokémon nickname")];

const PLACE_PARTS = { "mail OT": N_("{place}: mail, original trainer"), "mail nickname": N_("{place}: mail, Pokémon"),
  mail: N_("{place}: mail author"), OT: N_("{place}: original trainer") };
const NUMBERED_PLACES = { party: N_("party {n}"), box: N_("box {n}"), daycare: N_("daycare {n}"), mail: N_("mail {n}"),
  "link record": N_("link record {n}") };

/** Translate a converter row's save location. */
export function placeLabel(place) {
  const slot = place.match(/^slot (\d+) (.+)$/);
  if (slot) return t("slot {n}: {place}", { n: slot[1], place: placeLabel(slot[2]) });
  const part = place.match(/^(.+?) (mail OT|mail nickname|mail|OT)$/);
  if (part) return t(PLACE_PARTS[part[2]], { place: placeLabel(part[1]) });
  const numbered = place.match(/^(party|box|daycare|mail|link record) (\d+)$/);
  if (numbered) return t(NUMBERED_PLACES[numbered[1]], { n: numbered[2] });
  const boxed = place.match(/^box (\d+) slot (\d+)$/);
  if (boxed) return t("box {box}, slot {slot}", { box: boxed[1], slot: boxed[2] });
  const fame = place.match(/^hall of fame (\d+) slot (\d+)$/);
  if (fame) return t("Hall of Fame {team}, slot {slot}", { team: fame[1], slot: fame[2] });
  if (place === "player") return t("player");
  if (place === "rival") return t("rival");
  return place;
}

/** Collect names differing from the plan; eggs stay unnamed. */
export function requestsOf(rows, values) {
  const out = [];
  rows.forEach((r, i) => {
    if (r.egg || r.renameable === false) return;
    const raw = values[i] ?? r.after;
    const v = raw.trim();
    // an untouched field keeps its spaces
    if (r.kind === "over" || raw !== r.after && v !== r.after) out.push([r.place, v]);
  });
  return out;
}

/** Order trainer, rival, party, other names and boxes. */
export function orderOf(rows) {
  const rank = (place) => (place === "player" ? 0 : place === "rival" ? 1 : /^party \d+$/.test(place) ? 2
    : /^box \d+$/.test(place) ? 4 : 3);
  // other names keep the converter's order
  const num = (place) => (rank(place) === 3 ? 0 : Number(place.match(/\d+/)?.[0] ?? 0));
  return rows.map((_, i) => i).sort((a, b) => rank(rows[a].place) - rank(rows[b].place)
    || num(rows[a].place) - num(rows[b].place) || a - b);
}

/** Unchanged rows folded until requested. */
export const FOLDS = [
  { test: (row) => /^box \d+$/.test(row.place), show: N_("Show {n} box names, kept as they are"),
    hide: N_("Hide the box names") },
  { test: (row) => !/^(player|rival|party \d+|daycare \d+)$/.test(row.place.replace(/^slot \d+ /, "")),
    show: N_("Show {n} other names: original trainers, mail, link records, the Hall of Fame"),
    hide: N_("Hide these names") },
];

/** Identify PC nickname rows independently of other names. */
export const boxOf = (row) => Number(row.place.match(/^box (\d+) slot \d+$/)?.[1]) || null;
/** Keep PC nicknames outside the miscellaneous fold. */
export const foldOf = (row) => (row.kind === "kept" && !boxOf(row) ? FOLDS.findIndex((f) => f.test(row)) : -1);

/** Count letters exceeding the field's character limit. */
export const overBy = (text, limit) => Math.max(0, [...text.trim()].length - limit);

/** Typed name letters the target game cannot show. */
export const missingLetters = (text, letters) => [...new Set([...text.trim()].filter((ch) => !letters.has(ch)))];

export const MISSING = N_("the game cannot show {letters}");

/** A names row for a typed value. */
export function rowState(row, value, letters = null) {
  // the game names an egg itself
  if (row.egg) return { note: t("egg: the name is set at hatching"), bad: false, locked: true, missing: [] };
  // only a name that would be written is checked
  const typed = row.kind === "over" || value.trim() !== row.after;
  const missing = typed && letters ? missingLetters(value, letters) : [];
  if (missing.length) return { note: t(MISSING, { letters: missing.join(" ") }), bad: true, locked: false, missing };
  const by = overBy(value, row.limit);
  if (by) {
    const note = by > 1 ? t("{by} letters over the {limit} the game allows", { by, limit: row.limit })
      : t("{by} letter over the {limit} the game allows", { by, limit: row.limit });
    return { note, bad: true, locked: false, missing };
  }
  return { note: typed ? t("renamed here") : t(KIND_NOTES[row.kind] ?? row.kind), bad: false, locked: false, missing };
}

/** The name a row shows; eggs show as eggs. */
export const shownName = (row, name) => (row.egg ? t("Egg") : name);

/** Summarize affected name records and older-slot counts. */
export const groupsText = (groups) => groups.map((g) => (g.older_slot
  ? t("{count} × {kind} ({older} in the older save slot)", { count: g.count, kind: t(g.kind), older: g.older_slot })
  : t("{count} × {kind}", { count: g.count, kind: t(g.kind) }))).join(", ");

const made = (count, groups) => (count > 1 && groups?.length ? ` (${groupsText(groups)})` : "");
const playerNote = (report) => (report.renamed_groups?.some((g) => g.kind === "original trainer of a Pokémon")
  ? t(" A renamed player is also the original trainer on every Pokémon the player caught, so each copy is renamed.")
  : "");

/** Summarize conversion counts and the compatible ROM. */
export function summaryText(report, name, back) {
  const player = playerNote(report);
  const counts = t("{defaults} default names converted, {transliterated} written in Latin letters{made}", {
    defaults: report.defaults, transliterated: report.transliterated,
    made: made(report.transliterated, report.transliterated_groups) });
  const renamed = t(", {renamed} renamed{made}.", { renamed: report.renamed, made: made(report.renamed, report.renamed_groups) });
  const load = back ? t(" Load {name} with the English ROM.", { name }) : t(" Load {name} with the translated ROM.", { name });
  return `${counts}${renamed}${player}${load}`;
}

/** Summarize the names an edit wrote. */
export function editSummaryText(report, name) {
  if (!report.renamed) return t("No name changed: {name} is the save as it was.", { name });
  return t("Names written: {renamed}{made}.", { renamed: report.renamed, made: made(report.renamed, report.renamed_groups) })
    + playerNote(report) + t(" Load {name} with the same game as before.", { name });
}

/** Format a byte count for display. */
const kib = (n) => t("{n} KiB", { n: formatNumber(+(n / 1024).toFixed(n % 1024 ? 1 : 0)) });

const $ = (id) => document.getElementById(id);

// the report's target letters, read once per report
const letterSets = new WeakMap();
function lettersOf(report) {
  if (!report.letters) return null;
  if (!letterSets.has(report)) letterSets.set(report, new Set(report.letters));
  return letterSets.get(report);
}

const state = {
  game: null, sides: [], named: [], base: null, py: null, ready: false,
  file: null, bytes: null, report: null, values: [], url: null, generation: 0, names: new Map(),
  engine: "loading", fileState: null, fileError: null, status: null, loadError: null,
  detect: false, detected: undefined, renameInitialized: false, box: "all",
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

function pair() {
  return $("rename-only").checked
    ? pairOf($("edit-game").value, SAME, state.sides)
    : pairOf($("from").value, $("to").value, state.sides);
}

/** Load release name tables verified against the manifest. */
async function namesOf(release) {
  if (state.names.has(release.names)) return state.names.get(release.names);
  const response = await fetch(new URL(release.names, state.base).href, { credentials: "omit", cache: "no-store" });
  if (!response.ok) throw new Error(`${release.names}: HTTP ${response.status}`);
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (await digest(bytes, "SHA-256") !== release.names_sha256) {
    throw uiError("{file} differs from the release.", { file: release.names });
  }
  const text = new TextDecoder().decode(bytes);
  state.names.set(release.names, text);
  return text;
}

async function run(requested = [], output = "original") {
  const p = pair();
  const tables = JSON.stringify(await Promise.all(state.named.map(namesOf)));
  if (p.edit) {
    const edit = state.py.globals.get("save_edit");
    const report = JSON.parse(edit(state.bytes, state.game.id, await namesOf(p.release), p.translated,
      JSON.stringify(requested), output, tables));
    edit.destroy();
    return report;
  }
  const convert = state.py.globals.get("save_convert");
  const report = JSON.parse(convert(state.bytes, state.game.id, await namesOf(p.release), p.back,
    JSON.stringify(requested), output, tables));
  convert.destroy();
  return report;
}

/** Pick the source game by its default names. */
async function detect() {
  state.detected = undefined;
  const newest = state.sides.filter((s, i) => s.release
    && state.sides.findIndex((o) => o.release?.language === s.release.language) === i);
  if (!newest.length) return;
  const tables = await Promise.all(newest.map((s) => namesOf(s.release)));
  const matches = state.py.globals.get("save_matches");
  const result = JSON.parse(matches(state.bytes, state.game.id, JSON.stringify(tables)));
  matches.destroy();
  if (result.error) return;
  const id = readSide(detectedSide(result.matches, [ORIGINAL, ...newest.map((s) => s.id)]), $("edit-game").value, state.sides);
  state.detected = id;
  if (id) $("edit-game").value = id;
}

function clearDownload() {
  if (state.url) URL.revokeObjectURL(state.url);
  state.url = null;
  $("download").hidden = true;
  $("download").removeAttribute("href");
}

function renderDirection() {
  const rename = $("rename-only").checked;
  const p = pair();
  const lang = p ? languageName(p.release.language) : "";
  $("direction-pair").hidden = rename;
  $("edit-game-field").hidden = !rename;
  $("rename-note").hidden = !rename;
  $("from").disabled = !state.game || rename;
  $("to").disabled = !state.game || rename;
  $("edit-game").disabled = !state.game || !rename;
  $("direction-summary").textContent = !p ? t("Choose the English game and a translation.")
    : p.edit ? t("Rename only: the names you edit are written.")
    : p.back ? t("Default names become the English ones. Unsupported name letters follow the translation pack’s conversion table; a name that becomes too long must be shortened.")
      : t("Species names stored in the save become their {language} names. Nicknames you typed yourself stay as they are unless you rename them below.",
        { language: lang });
  if (state.game) $("direction-summary-loading").removeAttribute("data-loading");
  showText($("file-drop-line"), dropLine());
  setStep("step-direction", p ? "done" : "active");
}

function renderNames() {
  const r = state.report;
  const body = $("names");
  body.replaceChildren();
  const edit = pair()?.edit;
  if (edit) showText($("names-after"), "New name");
  else showText($("names-after"), "After conversion");
  $("names-note").textContent = namesNote(r, edit);
  if (!r) return;
  const letters = lettersOf(r);
  const hidden = FOLDS.map(() => []);
  const boxed = [];
  for (const i of orderOf(r.rows)) {
    const row = r.rows[i];
    if (row.renameable === false) continue;
    const input = el("input", { class: "input", value: shownName(row, state.values[i] ?? row.after),
      "aria-label": placeLabel(row.place), spellcheck: false, disabled: rowState(row, row.after).locked });
    const note = el("span", { class: "auto" });
    const update = () => {
      state.values[i] = input.value;
      const { note: text, bad, missing } = rowState(row, input.value, letters);
      input.classList.toggle("over", bad);
      if (missing.length) note.replaceChildren(...textParts(t(MISSING), { letters: el("b", {}, missing.join(" ")) }));
      else note.textContent = text;
      note.classList.toggle("bad", bad);
      clearDownload();
      renderConvert();
    };
    input.addEventListener("input", update);
    const tr = el("tr", { "data-place": row.place }, el("td", { textContent: placeLabel(row.place) }),
      el("td", { class: "old", textContent: shownName(row, row.stored) }),
      el("td", {}, input, " ", note));
    if (boxOf(row)) boxed.push({ box: boxOf(row), tr });
    const fold = foldOf(row);
    if (fold >= 0 && !rowState(row, input.value, letters).bad) {
      tr.hidden = true;
      hidden[fold].push(tr);
    }
    body.append(tr);
    update();
  }
  if (boxed.length) {
    const select = el("select", { class: "input", "aria-label": t("PC box") },
      el("option", { value: "all", textContent: t("All boxes") }));
    for (const box of [...new Set(boxed.map((row) => row.box))].sort((a, b) => a - b)) {
      select.append(el("option", { value: String(box), textContent: placeLabel(`box ${box}`) }));
    }
    select.value = [...select.options].some((o) => o.value === state.box) ? state.box : "all";
    const filter = () => {
      state.box = select.value;
      for (const row of boxed) row.tr.hidden = state.box !== "all" && row.box !== Number(state.box);
    };
    select.addEventListener("change", filter);
    filter();
    body.append(el("tr", { class: "fold" }, el("td", { colSpan: 3 },
      el("strong", {}, t("PC nicknames")), " ", select)));
    for (const row of boxed) body.append(row.tr);
  }

  FOLDS.forEach((fold, k) => {
    const rows = hidden[k];
    if (!rows.length) return;
    const showLabel = t(fold.show, { n: rows.length });
    const toggle = el("button", { type: "button", class: "btn sm ghost", textContent: showLabel });
    toggle.addEventListener("click", () => {
      const show = rows[0].hidden;
      for (const tr of rows) tr.hidden = !show;
      toggle.textContent = show ? t(fold.hide) : showLabel;
    });
    rows[0].before(el("tr", { class: "fold" }, el("td", { colSpan: 3 }, toggle)));
  });
}

function renderConvert() {
  const r = state.report;
  const p = pair();
  const letters = r && lettersOf(r);
  const over = r ? r.rows.some((row, i) => row.renameable !== false && rowState(row, state.values[i] ?? row.after, letters).bad) : true;
  if (p?.edit) {
    showText($("convert-title"), "Write and download");
    showText($("convert"), "Write the names");
  } else {
    showText($("convert-title"), "Convert and download");
    showText($("convert"), "Convert save");
  }
  $("output-format").disabled = !r;
  $("convert").disabled = !state.ready || !r || over;
  $("convert").hidden = Boolean(state.url);
  $("convert-note").textContent = r && p
    ? t("Writes {file} · checksums updated · original untouched",
      { file: outputName(state.file.name, p, $("output-format").value) }) : "";
  setStep("step-names", !r ? "locked" : over ? "active" : "done");
  setStep("step-convert", !r ? "locked" : state.url ? "done" : over ? "locked" : "active");
}

async function plan() {
  if (!state.bytes || !state.ready || !pair()) return;
  const generation = ++state.generation;
  clearDownload();
  $("file-error").hidden = true;
  setStatus(null);
  let report;
  try {
    if (pair().edit && state.detect) {
      state.detect = false;
      await detect();
      if (generation !== state.generation) return;
      renderDirection();
    }
    report = await run();
  } catch (e) {
    report = { error: errorText(e) };
  }
  if (generation !== state.generation) return;
  if (report.error) {
    state.report = null;
    state.fileState = "unreadable";
    state.fileError = report.error;
  } else {
    state.report = report;
    state.values = report.rows.map((row) => row.after);
    state.fileState = "read";
  }
  renderFile();
  renderNames();
  renderConvert();
}

const FILE_CHIPS = { unreadable: ["err", N_("Not readable")], read: ["ok", N_("Read")] };

/** Render the file step from conversion results. */
function renderFile() {
  if (!state.file) return;
  $("file-drop-title").textContent = t("Drop another save file here");
  const report = state.report;
  if (!state.fileState || state.fileState === "reading") {
    chip("file-chip", "plain", state.ready ? t("Reading…") : t("Waiting for the converter"));
    $("file-meta").textContent = t("{size} · read on this device", { size: kib(state.bytes.length) });
    return;
  }
  const [cls, label] = FILE_CHIPS[state.fileState];
  chip("file-chip", cls, t(label));
  if (!report) {
    $("file-note").hidden = true;
    Object.assign($("file-error"), { hidden: false, textContent: `${state.file.name}: ${state.fileError}` });
    setStep("step-file", "active");
    return;
  }
  setStep("step-file", "done");
  const p = pair();
  const slots = report.slots.map((s) => t("slot {index} save #{counter}", { index: s.index, counter: s.counter })).join(", ");
  const container = CONTAINER_NAMES[report.container];
  $("file-meta").textContent = [kib(state.bytes.length), container ? t(container) : "",
    report.player ? t("trainer {name}", { name: report.player }) : "",
    t("{n} in the party", { n: report.party }), slots, t("read on this device")].filter(Boolean).join(" · ");
  if (p.edit) {
    const text = detectionText(state.detected, state.sides);
    Object.assign($("file-note"), { hidden: text === null, className: "note", textContent: text ?? "" });
    return;
  }
  const wrong = report.made.source === 0 && report.made.target > 0;
  const from = p.back ? t("{language} translation", { language: languageName(p.release.language) }) : t("English game");
  Object.assign($("file-note"), { hidden: !wrong, className: "note warn", textContent: wrong
    ? t("No default name in this save matches the {from}; {n} match the other side. The save may come from the other ROM: swap the direction above.",
      { from, n: report.made.target }) : "" });
}

/** Translate conversion status or display converter messages. */
function setStatus(status) {
  state.status = status;
  renderStatus();
}

function renderStatus() {
  const s = state.status;
  const text = !s ? "" : s.converting ? t("Converting…") : s.error ? s.error
    : s.over ? t("Shorten these names first: {names}.", { names: s.over.map(placeLabel).join(", ") })
      : s.edit ? editSummaryText(s.report, s.name) : summaryText(s.report, s.name, s.back);
  $("convert-status").textContent = text;
  $("convert-status").className = s && (s.error || s.over) ? "small note err" : "small";
}

async function readFile(file) {
  state.file = file;
  state.bytes = new Uint8Array(await file.arrayBuffer());
  $("file-card").hidden = false;
  $("file-name").textContent = file.name;
  $("file-drop").classList.add("compact");
  state.fileState = "reading";
  state.detect = true;
  state.detected = undefined;
  renderFile();
  await plan();
}

async function convert() {
  const p = pair();
  if (!state.report || !p) return;
  const generation = ++state.generation;
  const output = $("output-format").value;
  $("convert").disabled = true;
  setStatus({ converting: true });
  let report;
  try {
    report = await run(requestsOf(state.report.rows, state.values), output);
  } catch (e) {
    report = { error: errorText(e) };
  }
  if (generation !== state.generation) return;
  if (report.error || report.over.length) {
    setStatus(report.error ? { error: report.error } : { over: report.over });
    renderConvert();
    return;
  }
  const out = state.py.globals.get("save_output")();
  const bytes = out.toJs();
  out.destroy();
  state.url = URL.createObjectURL(new Blob([bytes], { type: "application/octet-stream" }));
  const name = outputName(state.file.name, p, report.output?.format ?? output);
  Object.assign($("download"), { href: state.url, download: name, hidden: false });
  showText($("download"), "Download {file}", { file: name });
  setStatus({ report, name, back: p.back, edit: p.edit });
  renderConvert();
  $("download").focus();
}

function fillSides() {
  state.sides = sidesOf(state.named, state.game?.name);
  const opts = (select, keep) => {
    const prev = select.value;
    select.replaceChildren(...state.sides.map((s) => new Option(s.label, s.id)));
    select.value = state.sides.some((s) => s.id === prev) ? prev : keep;
  };
  opts($("from"), ORIGINAL);
  opts($("to"), state.sides[1]?.id);
  opts($("edit-game"), ORIGINAL);
}

function wire() {
  for (const [id, other] of [["from", "to"], ["to", "from"]]) {
    $(id).addEventListener("change", () => {
      if (!pair()) $(other).value = $(id).value === ORIGINAL ? state.sides[1].id : ORIGINAL;
      state.detect = false;
      if (id === "from") state.detected = undefined;
      const p = pair();
      if (p) {
        history.replaceState(null, "", gameHash(state.game.id, p.release.language));
        setToolContext(state.game.id, p.release.language);
      }
      renderDirection();
      plan();
    });
  }
  $("rename-only").addEventListener("change", () => {
    const rename = $("rename-only").checked;
    if (rename && !state.renameInitialized) {
      $("edit-game").value = $("from").value;
      state.renameInitialized = true;
    }
    state.detect = rename;
    state.detected = undefined;
    renderDirection();
    plan();
  });
  $("edit-game").addEventListener("change", () => {
    state.detect = false;
    state.detected = undefined;
    renderDirection();
    plan();
  });
  $("output-format").addEventListener("change", () => {
    clearDownload();
    setStatus(null);
    renderConvert();
  });
  const pick = () => $("save-file").click();
  $("file-browse").addEventListener("click", pick);
  $("file-replace").addEventListener("click", pick);
  $("save-file").addEventListener("change", (e) => {
    const f = e.target.files[0];
    if (f) readFile(f);
    e.target.value = "";
  });
  const drop = $("file-drop");
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
    if (f) readFile(f);
  });
  $("convert").addEventListener("click", convert);
  window.addEventListener("pagehide", clearDownload);
}

async function startConverter(bundleURL) {
  try {
    const [py, zip] = await Promise.all([startPython(), fetch(bundleURL).then((r) => {
      if (!r.ok) throw uiError("the check bundle: HTTP {status}", { status: r.status });
      return r.arrayBuffer();
    })]);
    py.unpackArchive(new Uint8Array(zip), "zip", { extractDir: "/bundle" });
    py.runPython(GLUE);
    state.py = py;
    state.ready = true;
    state.engine = "ready";
    renderEngine();
    setStep("step-file", state.report ? "done" : "active");
    if (state.bytes) plan();
  } catch (e) {
    console.error(e);
    state.engine = "failed";
    state.loadError = uiError("The converter could not start: {error}. It runs gen3tl in Pyodide, loaded from its CDN.",
      { error: errorText(e) });
    renderEngine();
  }
  renderConvert();
}

const ENGINE_CHIPS = { loading: ["plain", N_("Converter: loading")], ready: ["ok", N_("Converter ready")],
  failed: ["err", N_("Converter unavailable")], none: ["plain", ""] };

function renderEngine() {
  const [cls, label] = ENGINE_CHIPS[state.engine];
  if (state.engine !== "loading") chip("engine-chip", cls, label && t(label));
  if (state.loadError) Object.assign($("load-error"), { hidden: false, textContent: errorText(state.loadError) });
}

/** Explain when the masthead language lacks a release. */
function showLanguageNote() {
  const want = chosenLanguage();
  const missing = Boolean(state.game) && want !== SOURCE_LANGUAGE && !state.named.some((r) => r.language === want);
  $("language-note").hidden = !missing;
  if (missing) {
    showText($("language-note"), "No {language} translation of {game} is released yet.",
      { language: languageName(want), game: state.game.name });
  }
}

/** Apply masthead language changes to the translated side. */
function followLanguage() {
  const want = chosenLanguage();
  const p = pair();
  const side = state.sides.find((s) => s.release?.language === want);
  if (!p || p.edit) return;
  history.replaceState(null, "", gameHash(state.game.id, want));
  setToolContext(state.game.id, want);
  if (!side) {
    if (p.back) return;
    $("from").value = $("to").value;
    $("to").value = ORIGINAL;
  } else {
    if (!p.back && p.release.language === want) return;
    $("from").value = ORIGINAL;
    $("to").value = side.id;
  }
  renderDirection();
  plan();
}

function renderGuide() {
  const guide = guideFor(chosenLanguage());
  if (guide) $("save-guide").href = guide.replace(/^guide/, "save-guide");
}

/** Relabel the page using held conversion state. */
function relabel() {
  renderEngine();
  renderGuide();
  if (!state.game) return;
  fillSides();
  renderDirection();
  renderFile();
  renderNames();
  renderConvert();
  renderStatus();
  followLanguage();
  showLanguageNote();
}

async function main() {
  wire();
  onSiteLanguage(relabel);
  await startPage();
  renderGuide();
  try {
    const registry = await loadRegistry(new URL("data/registry.json", document.baseURI).href);
    const sources = await loadSources(registry);
    const selected = chosenGame(registry, location.hash).game;
    if (sources.get(selected?.data)?.unavailable) throw new Error(sources.get(selected.data).error);
    const named = (g) => (sources.get(g.data)?.releases ?? []).filter((r) => r.profile === g.id && r.names);
    const games = registry.games.filter((g) => named(g).length);
    if (!games.length) throw uiError("No release on this site publishes the name tables a conversion needs yet.");
    const { game, route } = chosenGame(registry, location.hash, globalThis.localStorage, (g) => games.includes(g));
    if (!game) {
      location.replace(pickerURL("save"));
      return;
    }
    if (route.language) {
      await useSiteLanguage(route.language);
      rememberSiteLanguage(route.language);
    }
    rememberGame(game.id);
    state.game = game;
    state.base = new URL("releases/releases.json", game.data).href;
    state.named = named(game);
    state.sides = sidesOf(state.named, game.name);
    plainText($("game-name"), game.name);
    $("change-game").hidden = games.length < 2;
    fillSides();
    const wanted = state.sides.find((s) => s.release?.language === route.language)
      ?? state.sides.find((s) => s.release?.language === chosenLanguage());
    if (wanted) $("to").value = wanted.id;
    followLanguage();
    showLanguageNote();
    $("from").disabled = false;
    $("to").disabled = false;
    renderDirection();
    setStep("step-file", "active");
    const manifest = sources.get(game.data)?.manifest;
    startConverter(new URL(manifest?.bundle ?? "gen3tl-check.zip", game.data).href);
  } catch (e) {
    console.error(e);
    state.loadError = e;
    state.engine = "none";
    $("direction-summary-loading").removeAttribute("data-loading");
    renderEngine();
  }
}

if (typeof document !== "undefined" && document.getElementById("step-direction")) main();
