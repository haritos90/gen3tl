import { el, renderChildren, updateChildren } from "../dom.js";
import { t } from "../ui.js";
import { reachText, statusLabel, shortFile } from "../data.js";
import { explanationText, keepUnits } from "../checktext.js";
import { renderEntryNotes, renderNoteDrafts } from "../guidance.js";
import { UNMEASURED_ADVICE, UNVERIFIED_ADVICE } from "../limits.js";
import { translationState } from "../draft.js";
import { $, local, state, isSite, TRANSLATION_STATES } from "./state.js";




export function dl(target, pairs) {
  renderChildren(target, (target) => {
    for (const [k, v] of pairs) {
      if (v === null || v === undefined || v === "") continue;
      target.append(el("dt", { textContent: t(k) }), el("dd", {}, typeof v === "string" ? keepUnits(v) : v));
    }
  });
}




export function withSource(value, src, advice = false, described = "") {
  if (value === null || value === undefined || value === "") return null;
  if (!src) return String(value);
  return el("div", {}, String(value),
    src.text !== described ? el("div", { class: `src ${src.kind}${src.unverified ? " unverified" : ""}` },
      keepUnits(`${src.label}: ${src.text}`)) : null,
    src.evidence || src.references?.length ? contextDisclosure(t("evidence"), el("div", {},
      ...(src.references ?? []).map((reference) => el("div", { textContent: reference })),
      src.evidence ? el("div", { textContent: src.evidence }) : null), "src") : null,
    advice && src.unmeasured ? el("div", { class: "unmeasured", textContent: t(UNMEASURED_ADVICE) }) : null,
    advice && src.unverified ? el("div", { class: "unmeasured", textContent: t(UNVERIFIED_ADVICE) }) : null);
}




export function contextDisclosure(label, content, className = "") {
  return el("details", { class: `ctx-disclosure ${className}`.trim() },
    el("summary", { class: "ctx-summary" }, el("span", { class: "ctx-summary-text" }, label)), content);
}




/** Estimate wrapped lines before the panel is mounted. */
export function renderReachEvidence(evidence) {
  const shown = reachText(evidence);
  if (!shown) return null;
  const lines = shown.split(/\r?\n/);
  const text = el("div", { class: "muted reach-text", textContent: shown });
  if (lines.reduce((count, line) => count + Math.max(1, Math.ceil(line.length / 60)), 0) <= 4) return text;
  const preview = lines[0].length > 80 ? `${lines[0].slice(0, 77).trimEnd()}…` : lines[0];
  return contextDisclosure([t("Evidence"), el("span", { class: "reach-preview", textContent: `: ${preview}` })],
    text, "reach-evidence");
}




/** Display translator guidance, source notes and catalog comments. */
export function renderNote(entry) {
  renderChildren($("note"), (box) => {
    box.append(renderEntryNotes(entry, state.index));
    const evidence = explanationText(entry.reach?.evidence);
    const comments = entry.context.comments.filter((comment) => !evidence
      || (comment !== evidence && comment !== `reach: ${entry.reach.class} (${evidence})`));
    if (comments.length) {
      box.append(contextDisclosure(t("PO comments ({n})", { n: comments.length }),
        el("pre", { class: "comments", textContent: comments.join("\n") })));
    }
  });
  if (!isSite()) $("note").append(renderNoteDrafts({ kind: "entry", id: entry.id },
    { kind: "entry", id: entry.id, source_sha256: entry.source_sha256 }, state.index, state.noteEdits,
    state.profile.languages.map((language) => language.code)));
}




/** Compare browser draft history with catalog state. */
export function renderHistory(entry) {
  const box = $("history");
  const ed = state.edits.get(entry.id);
  const rows = [
    [t("draft"), ed ? `${t(TRANSLATION_STATES[translationState(ed)])} · ${t("changed in this browser · included in Export")}`
      : t("none in this browser")],
    [t("catalog"), `${t(TRANSLATION_STATES[translationState(entry)])} · ${statusLabel(entry.status)} · ${shortFile(entry.file)}`],
  ];
  if (local.matches(state.index)) {
    rows.push([t("local"), t("Save drafts writes text to the catalog and keeps note proposals inactive; a new translation is saved as translated, an approved text stays approved")]);
  }
  updateChildren(box, ...rows.map(([k, v]) => el("div", {}, el("span", { textContent: k }), el("span", { textContent: v }))));
}
