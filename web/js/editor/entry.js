import { el, renderChildren, updateChildren, setText } from "../dom.js";
import { t, languageName, N_, showText } from "../ui.js";
import { statusLabel, loadIndex, verifyProjectIndex } from "../data.js";
import { keepUnits, limitText, checkText, missingTerms } from "../checktext.js";
import { countLabel, countsOf, extentsOf, meterOf, sourceOf, arrowConsumer, consumerOf, waitingWindows } from "../limits.js";
import { isDraft, draftText } from "../draft.js";
import { renderUnits } from "../field.js";
import { parse } from "../text.js";
import { englishBlock, problemsHeading, unavailableNote } from "../gametext.js";
import { Codec, entryMeasure } from "../font.js";
import { drawPreview, droppedNote, fixNote } from "../preview.js";
import { latin } from "./game-text.js";
import { $, state, isSite, DATA } from "./state.js";




/** The English of the entry, or why it is hidden. */
export function renderEnglish(entry) {
  $("english-block").hidden = Boolean(state.settings?.referenceLanguage) && !state.ownEnglish && !state.english;
  const shown = state.ownEnglish ? { source_text: entry.source_text ?? "" } : englishBlock(entry, Boolean(state.english));
  const box = $("english");
  box.classList.toggle("muted", shown.source_text === undefined);
  if (shown.source_text === undefined) setText(box, shown.note);
  else renderUnits(box, parse(shown.source_text), state.codec);
}




export function referenceLanguages() {
  const game = state.registry?.games.find((g) => g.id === state.profile?.id);
  return (state.profile?.languages ?? []).filter((lang) => lang.code !== state.lang?.code
    && (!game || game.languages.includes(lang.code)));
}




export function fillReferenceLanguages() {
  const languages = referenceLanguages();
  const select = $("reference-language");
  updateChildren(select, el("option", { value: "", "data-ui": "None", textContent: t("None") }),
    ...languages.map((lang) => el("option", { value: lang.code, textContent: languageName(lang.code) })));
  if (!languages.some((lang) => lang.code === state.settings.referenceLanguage)) {
    state.settings.setReferenceLanguage("");
  }
  select.value = state.settings.referenceLanguage;
  select.disabled = !languages.length;
}




export function changeReferenceLanguage() {
  if (!state.settings) return;
  const code = $("reference-language").value;
  state.settings.setReferenceLanguage(referenceLanguages().some((lang) => lang.code === code) ? code : "");
  if (state.entry) renderEnglish(state.entry);
  return loadReference();
}




/** Fetch only the chosen catalog's translations. */
export function referenceIndex(lang) {
  const profile = state.profile.id;
  const url = new URL(lang.index, DATA()).href;
  if (!state.referenceIndexes.has(url)) {
    const pending = loadIndex(url).then((index) => {
      verifyProjectIndex(index, profile, lang.code);
      return { texts: new Map(index.list.map((row) => [row.id, row.translation])),
        codec: new Codec(index.codec, index.charmap, index.language_rules) };
    });
    pending.catch(() => state.referenceIndexes.delete(url));
    state.referenceIndexes.set(url, pending);
  }
  return state.referenceIndexes.get(url);
}




export async function loadReference() {
  const request = ++state.referenceGeneration;
  const generation = state.generation;
  const entry = state.entry;
  const code = state.settings?.referenceLanguage;
  const lang = referenceLanguages().find((item) => item.code === code);
  state.reference = entry && lang ? { code, note: N_("Loading…") } : null;
  renderReference();
  if (!state.reference) return;
  try {
    const { texts, codec } = await referenceIndex(lang);
    if (request !== state.referenceGeneration || generation !== state.generation || entry !== state.entry) return;
    const text = texts.get(entry.id);
    state.reference = text ? { code, text, codec } : { code, note: N_("No translation for this entry.") };
  } catch {
    if (request !== state.referenceGeneration || generation !== state.generation || entry !== state.entry) return;
    state.reference = { code, note: N_("Reference translation unavailable.") };
  }
  renderReference();
}




export function renderReference() {
  const reference = state.reference;
  $("reference-block").hidden = !reference;
  const box = $("reference");
  if (!reference) {
    box.replaceChildren();
    return;
  }
  setText($("reference-label"), t("Reference · {language}", { language: languageName(reference.code) }));
  box.lang = reference.code;
  box.classList.toggle("muted", Boolean(reference.note));
  if (reference.note) setText(box, t(reference.note));
  else renderUnits(box, parse(reference.text), reference.codec);
}




/** Describe the entry's window in its header. */
export function showWindowChip(entry) {
  const w = entry.window;
  const wtext = isSite() ? "" : isDraft(state.index) ? t("window not measured")
    : w ? [w.kind, w.keys != null ? t("{n} keys", { n: w.keys }) : w.width ? `${w.width} px` : ""].filter(Boolean).join(" · ") : "";
  setText($("e-window"), keepUnits(wtext));
  $("e-window").hidden = !wtext;
}




export function showStatus() {
  const e = state.entry;
  const edited = state.edits.get(e.id);
  const s = $("e-status");
  setText(s, e.owner ? t("Language pack · read-only")
    : edited ? t("{status}, edited here", { status: statusLabel(e.status) }) : statusLabel(e.status));
  s.className = `badge ${edited ? "edited" : e.status}`;
  setText($("e-draft"), e.owner ? t("read-only") : edited ? t("Draft · changed in this browser") : "");
  $("revert").disabled = Boolean(e.owner) || !edited;
}




export function renderBreakControls(entry) {
  const reasons = (entry.window?.consumers ?? []).filter((c) => c.instant_print).map((c) =>
    t("scroll and page codes are not supported in {where}: this window prints instantly and drops the remaining text",
      { where: limitText(c.name) }));
  const reason = [...new Set(reasons)].join("; ");
  for (const button of document.querySelectorAll("[data-break]")) {
    const blocked = Boolean(reason) && ["\\l", "\\p"].includes(button.dataset.break);
    button.disabled = Boolean(entry.owner) || blocked;
    button.title = blocked ? reason : t(button.dataset.uiTitle);
    if (blocked) button.setAttribute("aria-describedby", "break-note");
    else button.removeAttribute("aria-describedby");
  }
  setText($("break-note"), reason);
  $("break-note").hidden = !reason;
}




/** Estimate layout locally while checks are unavailable. */
export function estimate(entry, text) {
  const laid = state.index.version ? text.replaceAll("@VERSION@", state.index.version) : text;
  const m = entryMeasure(state.index, state.glyphs, state.codec, entry.window);
  const waiting = waitingWindows(laid, entry.window);
  const lines = laid.split("\\p").map((p, bi) => p.split(/\\[nl]/).map((l, li) => {
    let width = null;
    try {
      width = m.lineWidth(l);
    } catch {
      width = null;
    }
    const consumer = waiting[bi]?.[li];
    return { text: l, width, ...(consumer ? { room: consumer.width - consumer.arrow, consumer: consumer.name } : {}) };
  }));
  return { id: entry.id, text: laid, laid, problems: [], errors: 0, warnings: 0, lines, exact: false, estimate: true };
}




/** A meter of the text in another window. */
export function extentRow(x) {
  const m = meterOf({ width: x.value, shadow: x.shadow }, { width: x.limit });
  const fill = el("i");
  fill.style.width = `${m.fill}%`;
  return el("div", { class: `ln other ${m.state}`, title: x.title },
    el("span", { textContent: x.label }), el("span", { class: "meter" }, fill), el("span", { class: "px", textContent: m.label }));
}




/** Display width meters for each text line. */
export function renderBars(entry, res) {
  const w = entry.window;
  return renderChildren($("bars"), (bars) => {
    if (res.draft) {
      setText($("lines-note"), isSite() ? t("Not measured: the page lays this text out.")
        : t("Not measured: game font and window limits are unavailable."));
      return;
    }
    const note = res.estimate ? t("estimated by the page (check not loaded)") : res.exact ? t("exact") : t("approximate (no ROM)");
    const width = w?.keys == null ? sourceOf(w, "width") : null;
    const unmeasured = width?.unmeasured ? ` ${t("(unmeasured: the English width)")}`
      : width?.unverified ? ` ${t("(unverified estimate)")}` : "";
    const extent = w && [w.keys != null ? t("{n} keys", { n: w.keys }) : `${w.width} px${unmeasured}`,
      w.lines ? t("{n} lines", { n: w.lines }) : ""].filter(Boolean).join(", ");
    setText($("lines-note"), keepUnits(w ? t("Widths {note}; window {size}.", { note, size: extent })
      : t("Widths {note}; no window recorded.", { note })));
    const multi = res.lines.length > 1;
    res.lines.forEach((box, b) => {
      if (multi) bars.append(el("div", { class: "ln-box", textContent: t("Box {n}", { n: b + 1 }) }));
      box.forEach((l, i) => {
        const m = meterOf(l, w);
        const fill = el("i");
        fill.style.width = `${m.fill}%`;
        const meter = el("span", { class: "meter" }, fill);
        if (w?.target && w.keys == null && w.target < m.limit) {
          const target = el("span", { class: "target", title: keepUnits(t("wrap target {n} px", { n: w.target })) });
          target.style.left = `${(w.target / m.limit) * 100}%`;
          meter.append(target);
        }
        const consumer = consumerOf(w, l.consumer) ?? arrowConsumer(w);
        const arrowNote = m.arrow ? keepUnits(t("The prompt arrow follows this line: {n} px of the {width} px window stay free for it.",
          { n: consumer?.arrow ?? w.arrow, width: consumer?.width ?? w.width })) : "";
        bars.append(el("div", {
          class: `ln ${m.state}`,
          title: m.shadow ? `${l.text}\n${keepUnits(t("1 px over: the cut column holds only shadow"))}`
            : arrowNote ? `${l.text}\n${arrowNote}` : l.text,
        },
        el("span", { textContent: t("line {n}", { n: multi ? `${b + 1}.${i + 1}` : i + 1 }) }),
        meter,
        el("span", { class: "px", textContent: m.label })));
      });
    });
    for (const x of extentsOf(res)) bars.append(extentRow(x));
    let size = null;
    try {
      size = state.codec.encode(res.text).length;
    } catch {
      size = null;
    }
    const counts = countsOf(res, w, size);
    if (counts.length) {
      bars.append(el("div", { class: "counts" }, ...counts.map((c) =>
        el("span", { class: `count-item${c.over ? " over" : ""}`, textContent: countLabel(c) }))));
    }
    if (res.laid !== res.text) {
      bars.append(el("div", { class: "ln-box", textContent: t("The build lays the text out with other breaks; the meters and the preview show that layout.") }));
    }
  });
}




export function renderProblems(res) {
  return renderChildren($("problems"), (ul) => {
    const heading = problemsHeading(res, state.rom);
    showText($("problems-head"), heading.key, heading.vars);
    if (!res.problems.length) {
      ul.append(el("li", { class: "none", textContent: res.estimate ? t("No check result.") : t("No problems.") }));
    }
    for (const p of res.problems) ul.append(el("li", { class: p.severity, textContent: checkText(p.message) }));
    const unavailable = unavailableNote(res);
    if (unavailable) ul.append(el("li", { class: "none", textContent: unavailable }));
    const missing = new Set(missingTerms(res.problems).map((m) => `glossary term ${m.term} -> ${m.translation} `));
    for (const term of document.querySelectorAll("#glossary .term")) term.classList.toggle("missing", missing.has(term.dataset.key));
  });
}




export function speakerInk(entry) {
  const g = entry.context.speakers.find((s) => s.source === "text colour")?.gender;
  return g === "male" || g === "female" ? g : "plain";
}




/** Show game boxes, clipping and layout fix guidance. */
export function renderPreview(entry, res) {
  const english = typeof entry.source_text === "string";
  $("pv-en-label").hidden = !english;
  for (const [id, text] of [["pv-en", entry.source_text ?? ""], ["pv-tr", res.laid]]) {
    const canvas = $(id);
    canvas.setAttribute("aria-label", `${id === "pv-en" ? t("English") : t("Translation")}: ${text.replace(/\\[nlp]/g, " ")}`);
    let plain = $(`${id}-text`);
    if (!plain) {
      plain = el("pre", { id: `${id}-text`, class: "draft-preview" });
      canvas.after(plain);
    }
    canvas.hidden = Boolean(res.draft) || (id === "pv-en" && !english);
    plain.hidden = !res.draft || (id === "pv-en" && !english);
    if (res.draft) plain.textContent = draftText(text);
  }
  $("clip-chip").hidden = true;
  $("fix-note").hidden = true;
  if (res.draft) {
    setText($("preview-cap"), "");
    setText($("preview-note"), isSite() ? t("Text only: the page shows it in its own font.")
      : t("Text only; game font and wrapping are not verified."));
    return;
  }
  const zoom = state.zoom;
  const style = entry.window?.font ?? state.index.fonts.main;
  const glyphs = state.glyphs[style] ?? state.glyphs[state.index.fonts.main];
  const m = entryMeasure(state.index, state.glyphs, state.codec, entry.window);
  const opts = { window: entry.window, box: entry.context.boxes[0] ?? null, measure: m, glyphs, styles: state.glyphs,
    speaker: speakerInk(entry), zoom,
    arrowColor: document.defaultView?.getComputedStyle?.(document.documentElement)?.getPropertyValue("--accent")?.trim() || null };
  const en = english ? drawPreview($("pv-en"), entry.source_text, opts) : { unknown: 0 };
  const fits = new Set(res.lines.flat().filter((l) => l.shadow).map((l) => l.text));
  const tr = drawPreview($("pv-tr"), res.laid, { ...opts, shadow: (t) => fits.has(t) });
  const w = entry.window;
  setText($("preview-cap"), keepUnits(w
    ? [t("Font {font}", { font: style }), t("{kind} window", { kind: w.kind }), w.width ? t("{n} px wide", { n: w.width }) : "",
      w.lines ? t("{n} lines", { n: w.lines }) : "", w.arrow ? t("arrow {n} px", { n: w.arrow }) : ""].filter(Boolean).join(" · ")
    : `${t("Font {font}", { font: style })} · ${t("no window recorded")}`));
  const widest = tr.clipped.reduce((a, c) => Math.max(a, c.clipped), 0);
  const arrowCut = tr.arrowCut.reduce((a, c) => Math.max(a, c.cut), 0);
  $("clip-chip").hidden = !widest && !arrowCut;
  setText($("clip-chip"), keepUnits(widest ? t("{n} px clipped", { n: widest }) : t("arrow cut by {n} px", { n: arrowCut })));
  const note = [fixNote(tr), droppedNote(res.problems)].filter(Boolean).join(" ");
  setText($("fix-text"), keepUnits(note));
  $("fix-note").hidden = !note;
  const unknown = en.unknown + tr.unknown;
  setText($("preview-note"), unknown
    ? t("{n} project-drawn stand-ins; preview and widths are approximate (no ROM).", { n: unknown })
    : latin() ? t("Game font from your ROM.") : "");
}
