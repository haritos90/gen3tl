import { setText } from "../dom.js";
import { t, formatNumber, N_, showText } from "../ui.js";
import { versionLabel, statusCounts } from "../data.js";
import { isDraft } from "../draft.js";
import { sheetsOf } from "../fonts.js";
import { percentOf, progressTitle, translatedOf } from "../landing.js";
import { PYODIDE_VERSION } from "../check.js";
import { latin } from "./game-text.js";
import { $, local, state, isSite } from "./state.js";





export const CHECK_TEXT = {
  idle: ["plain", N_("Check: no catalog")],
  draft: ["warn", N_("Check: placeholders only")],
  loading: ["plain", N_("Check: loading")],
  failed: ["err", N_("Check: unavailable")],
};




export function setCheckStatus(s, detail = "") {
  state.checkState = s;
  state.checkDetail = detail;
  const st = $("check-status");
  const [cls, key] = s === "ready"
    ? ["ok", state.check?.exact ? N_("Check: exact") : N_("Check: approximate")]
    : CHECK_TEXT[s];
  showText(st, key);
  st.className = `chip ${cls}`;
  st.title = detail ? t("gen3tl layout check in Pyodide {version}: {detail}", { version: PYODIDE_VERSION, detail })
    : t("gen3tl layout check in Pyodide {version}", { version: PYODIDE_VERSION });
}




export function renderRomProgress() {
  const start = !$("start").hidden;
  for (const [id, screen] of [["start-rom-progress", "start"], ["rom-progress", "workspace"]]) {
    const node = $(id);
    node.hidden = !state.romLoading || (screen === "start") !== start;
    showText(node, state.romProgress);
  }
}




export function showRomState() {
  const chip = $("rom-state");
  const draft = isDraft(state.index);
  const exact = latin() !== null;
  const matches = state.rom && state.index && state.rom.sha1 === state.index.rom.sha1;
  const [cls, key] = isSite() ? ["plain", N_("No ROM needed")]
    : draft ? ["warn", matches ? N_("Draft catalog · ROM verified") : N_("Draft catalog")]
    : exact ? ["ok", N_("ROM loaded · exact glyphs")]
    : ["plain", N_("No ROM · no game text")];
  chip.className = `chip ${cls}`;
  chip.title = matches ? state.rom.name : "";
  showText(chip, key);
  const button = $("rom-button");
  const load = !(matches || state.english);
  showText(button, load ? N_("Load your ROM") : N_("Forget the ROM"));
  button.className = `btn sm${load ? " primary" : ""}`;
  button.hidden = isSite();
  $("app").classList.toggle("no-rom", load && !isSite() && !isDraft(state.index));
}




export function showCounts() {
  const n = statusCounts(state.rows, state.edits?.map ?? new Map());
  for (const k of ["approved", "fuzzy", "untranslated", "problems", "edited"]) {
    setText($(`sb-${k}`), formatNumber(n[k]));
  }
  setText($("sb-never"), formatNumber(n["never shown"]));
  const progress = { approved: n.approved, fuzzy: n.fuzzy, untranslated: n.untranslated, nothing: n.nothing,
    "never shown": n["never shown"] };
  const pct = translatedOf(progress);
  $("progress-more").style.width = `${pct}%`;
  $("progress-bar").style.width = `${percentOf(progress)}%`;
  setText($("progress-pct"), `${pct}%`);
  $("progress").setAttribute("aria-valuenow", String(pct));
  $("progress").title = progressTitle(progress);
  setText($("count-texts"), state.rows.length ? formatNumber(state.rows.length) : "");
  setText($("count-pictures"), state.index?.graphics?.inventory?.length || "");
  setText($("count-terms"), state.index?.terms?.count ? formatNumber(state.index.terms.count) : "");
  setText($("count-fonts"), sheetsOf(state.index).length || "");
}





export function showVersion() {
  const v = versionLabel(state.index?.toolkit ?? state.manifest?.toolkit);
  $("sb-version").title = v.title;
  const release = state.index?.version ? t("translation {version}", { version: state.index.version }) : "";
  setText($("sb-version"), [v.text ? `gen3tl ${v.text}` : "", release].filter(Boolean).join(" · "));
}




export function updateEditCount() {
  setText($("edit-count"), String((state.edits?.size ?? 0) + (state.uploads?.size ?? 0) + (state.termEdits?.size ?? 0)
    + (state.glyphEdits?.size ?? 0) + (state.keyboardEdits?.size ?? 0) + (state.noteEdits?.size ?? 0)));
  $("save-local").hidden = !local.session;
  $("save-local").disabled = local.busy || !local.matches(state.index) || !(state.edits?.size || state.noteEdits?.size);
  showText($("save-local"), local.busy ? N_("Saving and refreshing…") : N_("Save drafts"));
  $("link-start").setAttribute("aria-disabled", String(local.busy));
  $("catalog-language").disabled = local.busy || !$("catalog-language").options.length;
  for (const button of document.querySelectorAll("[data-site-lang]")) button.disabled = local.busy;
  showCounts();
}
