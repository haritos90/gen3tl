import { el, renderChildren, setText } from "../dom.js";
import { t, errorText, formatNumber, N_, showText, plainText } from "../ui.js";
import { tableKey, statusLabel, shortFile, getJson, dirOf, idParts, reachLabel, rulesOf, isOverflow, loadTerms } from "../data.js";
import { gameHash } from "../registry.js";
import { keepUnits, limitText, limitReferences, slotSource, checkFailed } from "../checktext.js";
import { renderNotes } from "../guidance.js";
import { sourceOf, pairLabel } from "../limits.js";
import { translationState, isDraft, draftCheck, sourceTokens } from "../draft.js";
import { renderUnits, Field } from "../field.js";
import { parse, flat } from "../text.js";
import { englishOf } from "../gametext.js";
import { latinCells } from "../font.js";
import { FontsView } from "../fonts.js";
import { EntryList } from "../listview.js";
import { debounce } from "../check.js";
import { slotRows, loadSlotSources } from "../slots.js";
import { pyRepr, TermsView } from "../terms.js";
import { PicturesView } from "../pictures.js";
import { KeyboardsView } from "../keyboards.js";
import { FamiliesView } from "../families.js";
import { EngineRulesView } from "../enginerules.js";
import { entryHash, entryKey } from "./catalog.js";
import { dl, withSource, contextDisclosure, renderReachEvidence, renderNote, renderHistory } from "./context.js";
import { showBack } from "./controls.js";
import { renderEnglish, loadReference, showWindowChip, showStatus, renderBreakControls, estimate, renderBars, renderProblems, renderPreview } from "./entry.js";
import { latin, drafted, buildGlyphs, prepareRow, withGameText } from "./game-text.js";
import { $, state, VIEWS, trail, isSite, DATA, DOTS, TRANSLATION_STATES, setObjectReference } from "./state.js";
import { setCheckStatus, showCounts, updateEditCount } from "./status.js";




export const checkSoon = debounce(() => runCheck(), 180);




export async function configureLayout() {
  if (!state.check || !state.index) return;
  const request = ++state.configureGeneration;
  const draft = isDraft(state.index);
  try {
    const lat = latin();
    await state.check.configure(drafted(), lat, lat ? latinCells(state.index.fonts, state.rom.bytes) : null,
      state.index.graphics, lat !== null, draft);
    if (request !== state.configureGeneration) return;
    state.gfxState = "ready";
    setCheckStatus(draft ? "draft" : "ready");
  } catch (e) {
    if (request !== state.configureGeneration) return;
    console.error(e);
    state.gfxState = String(e.message || e).slice(0, 200);
    setCheckStatus("failed", String(e.message || e).slice(0, 80));
  }
  pictures.refresh();
  keyboards.refresh();
  if (state.view === "enginerules") engineRules.refresh?.();
  rerun();
}




/** Toggle list or context drawers on narrow screens. */
export function drawer(which) {
  const app = $("app");
  if (which) app.dataset.drawer = which;
  else delete app.dataset.drawer;
  $("show-list").setAttribute("aria-expanded", String(which === "list"));
  $("show-ctx").setAttribute("aria-expanded", String(which === "ctx"));
  if (which === "list") {
    requestAnimationFrame(() => {
      if (state.view === "texts") entries.shown(state.filtered.indexOf(state.cur));
      else if (state.view === "terms") terms.renderList();
    });
  }
}




export const entries = new EntryList($("list"), $("list-space"), (p) => rowElement(state.filtered[p], p));




/** Measure and check the texts with the redrawn glyphs. */
export function glyphsChanged() {
  state.edits.invalidateSources(Object.values(state.index.fill_sources?.dependencies ?? {}).flat());
  state.edits.save();
  updateEditCount();
  buildGlyphs();
  relayout();
}




export function idLink(id) {
  return el("a", { class: "id", textContent: id, title: t("Open {id}", { id }), on: { click: () => openId(id) } });
}




export function onEdit(text, valueState = null) {
  const e = state.entry;
  if (!e || e.owner) return;
  state.edits.set(e, text, state.edits.get(e.id)?.note ?? e.note, state.result?.problems ?? null,
    !isDraft(state.index) && (state.check?.exact ?? false), valueState);
  refreshDraft();
}





export function openId(id) {
  if (state.byId.has(id)) return openRow(state.byId.get(id));
  return entryKey(id).then((key) => key && openRow(state.byId.get(key))).catch(() => {});
}




/** Open a place of another view; history keeps the way. */
export function openPlace(place) {
  trail.leave(state.view, location.hash);
  showPlace(place);
  showBack();
}




export async function openRow(i, reveal = true) {
  const generation = state.generation;
  state.cur = i;
  const row = state.rows[i];
  if (reveal) entries.scrollTo(state.filtered.indexOf(state.cur));
  renderList();
  if (state.view === "texts") trail.replace(entryHash(row.id));
  if ($("app").dataset.drawer === "list") drawer(null);
  let entry;
  try {
    entry = await state.chunks.entry(row);
  } catch (e) {
    if (generation !== state.generation) return;
    setText($("e-file"), errorText(e));
    return;
  }
  if (state.cur !== i || generation !== state.generation) return;
  withGameText(entry, i);
  state.entry = entry;
  const ed = state.edits.get(entry.id);
  setText($("e-id"), entry.id);
  setObjectReference("text", entry.id);
  setText($("e-file"), shortFile(entry.file));
  $("e-file").title = entry.file;
  showStatus();
  const reach = reachLabel(entry.reach);
  Object.assign($("e-reach"), { hidden: !reach.never, title: reach.title });
  showWindowChip(entry);
  renderEnglish(entry);
  loadReference();
  field.setReadOnly(Boolean(entry.owner));
  renderBreakControls(entry);
  field.set(ed ? ed.translation : entry.translation);
  renderTokens();
  renderRules(entry);
  renderWindow(entry);
  renderContext(entry);
  renderApproved(entry);
  renderNote(entry);
  renderHistory(entry);
  state.result = null;
  runCheck();
}




export function refreshDraft() {
  state.checkRequest++;
  updateEditCount();
  showStatus();
  renderTokens();
  renderList();
  renderHistory(state.entry);
  checkSoon();
}




// One check setup after a run of strokes
export const relayout = debounce(() => (state.check ? configureLayout() : rerun()), 400);




export function renderApproved(entry) {
  return renderChildren($("approved"), (box) => {
    if (!entry.approved.length) {
      box.append(el("div", { class: "muted", textContent: t("None.") }));
      return;
    }
    for (const a of entry.approved) {
      const text = el("div", { class: "text ro" });
      renderUnits(text, parse(a.translation), state.codec);
      const ids = a.ids.slice(0, 8).flatMap((id) => [idLink(id), " "]);
      box.append(el("div", { class: "approved-item" }, text,
        el("div", { class: "muted" }, `${t("used by {n}:", { n: a.ids.length })} `, ...ids, a.ids.length > 8 ? "…" : ""),
        el("button", { type: "button", class: "btn sm", disabled: Boolean(entry.owner), textContent: t("Use"),
          on: { click: () => field.replace(a.translation) } })));
    }
  });
}




export function renderContext(entry) {
  const c = entry.context;
  return renderChildren($("context"), (box) => {
    const texts = state.english?.texts ?? null;
    const item = (label, ...value) => box.append(el("div", { class: "ctx-item" }, el("b", { textContent: `${label}: ` }), ...value));
    const names = c.speakers.map((s) => [s.name ? englishOf(texts, s.name) : null,
        s.class ? `(${englishOf(texts, s.class)})` : null]
        .filter(Boolean).join(" ")).filter(Boolean);
    if (names.length) item(t("Speakers"), [...new Set(names)].join("; "));
    const sourceLabels = { sprite: t("Overworld sprite"), trainer: t("Trainer record / class"),
      "text colour": t("Text colour"), label: t("Speaker label colour"), record: t("Record"), player: t("Player") };
    const genders = { male: t("Male"), female: t("Female"), neutral: t("Neutral"), variable: t("Variable"), unknown: t("Unknown") };
    const bases = { pronoun: t("pronoun"), "gendered word": t("gendered word"), "established character": t("established character") };
    for (const [role, label] of [["speaker", t("Speaker gender")], ["addressee", t("Addressee gender")]]) {
      const person = c.identity?.schema === "2.0.0" ? c.identity[role] : null;
      if (!person?.sources.length && person?.in_source !== "stated"
        && !(role === "speaker" && (c.events.length || c.speakers.length))) continue;
      const evidence = el("div");
      const sources = [...(person?.sources ?? [])];
      for (const source of ["sprite", "trainer", "text colour"]) {
        if (!sources.some((s) => s.source === source || (source === "trainer" && s.source === "record"))) {
          sources.push({ source, gender: "unknown" });
        }
      }
      for (const source of sources) {
        evidence.append(el("div", { textContent: `${sourceLabels[source.source] ?? t("Unknown")}${source.value ? ` ${source.value}` : ""}: ${genders[source.gender] ?? t("Unknown")}` },
          source.evidence ? el("div", { class: "muted", textContent: source.evidence }) : null));
      }
      const wording = person?.in_source === "stated" ? t("English states the gender: {basis}.", { basis: bases[person.basis] ?? t("Unknown") })
          : person?.in_source === "not-stated" ? t("English does not state the gender. Wording stays neutral.")
            : t("English gender needs review. Wording stays neutral until verified.");
      evidence.append(el("div", { class: "muted", textContent: wording }));
      if (person?.sources.some((s) => s.source === "player")) {
        evidence.append(el("div", { class: "muted", textContent: t("Player wording stays neutral.") }));
      }
      item(label, evidence);
    }
    if (c.maps?.length) item(t("Maps"), c.maps.map((k) => englishOf(texts, k)).join(", "));
    if (c.events.length) item(t("Events"), c.events.join(", "));
    if (c.boxes.length) item(t("Boxes"), c.boxes.join(", "));
    if (c.uses) item(t("Uses"), String(c.uses));
    if (c.inserted.length) {
      const table = el("table", { class: "mini" }, el("tr", {}, el("th", { textContent: t("Inserted") }),
        el("th", { textContent: t("Width") }), el("th", { textContent: t("Source") })));
      for (const x of c.inserted) {
        table.append(el("tr", {}, el("td", { textContent: x.token }), el("td", { textContent: keepUnits(`${x.width} px`) }),
          el("td", { textContent: slotSource(x.source) })));
      }
      box.append(table);
    }
    for (const w of c.words) {
      item(t("{slot} may be", { slot: w.slot }), idLink(w.id), ` ${w.source_text ? `${w.source_text} ` : ""}→ ${w.translation || t("(untranslated)")}`);
    }
    for (const h of c.hosts) item(t("Inserted as {slot} into", { slot: h.slot }), idLink(h.id));
    if (!box.childNodes.length) box.append(el("div", { class: "muted", textContent: t("No context recorded.") }));
    const reach = reachLabel(entry.reach);
    box.prepend(el("div", { class: "ctx-item" }, el("b", { textContent: `${t("Reach")}: ` }), reach.text,
      renderReachEvidence(entry.reach?.evidence)));
    renderGlossary(entry);
  });
}




export function renderGlossary(entry) {
  return renderChildren($("glossary"), (box) => {
    if (!entry.terms?.length) {
      box.append(el("div", { class: "muted", textContent: entry.terms ? t("No glossary terms in the English.")
        : t("The editor data names no terms of the English.") }));
      return;
    }
    for (const id of entry.terms) {
      const term = state.index.glossary[id];
      if (!term) continue;
      const forms = Object.entries(term.forms ?? {}).map(([k, v]) => `${k} ${v}`).join(" · ");
      box.append(el("div", { class: "term", "data-key": `glossary term ${pyRepr(term.source_text)} -> ${pyRepr(term.translation)} ` },
        el("div", { class: "term-head" }, el("b", { textContent: term.source_text }), " → ", term.translation,
          el("span", { class: "muted", textContent: ` ${[term.category, term.gender, term.status].filter(Boolean).join(", ")} ` }),
          el("a", { class: "id", textContent: t("edit term"), title: t("Open {id} in Terms", { id }),
            on: { click: () => openPlace({ view: "terms", id }) } })),
        forms ? el("div", { class: "forms", textContent: forms }) : null,
        term.notes ? el("div", { class: "forms", textContent: term.notes }) : null,
        renderNotes(term.note_ids, state.index.notes, state.index.language, state.index.research)));
    }
  });
}




export function renderList() {
  entries.render();
}




export function renderRules(entry) {
  return renderChildren($("rules"), (ul) => {
    const rules = rulesOf(entry);
    setText($("rules-note"), rules.length ? String(rules.length) : "");
    if (!rules.length) ul.append(el("li", { class: "none", textContent: t("No rules recorded.") }));
    for (const r of rules) {
      const wording = r.text.replace(/\[\[?[^\[\]\n]+\]\]?/g, (key) => limitText(`profile ${key}`));
      const text = idParts(keepUnits(wording), (id) => state.byId.has(id)).map((p) => (p.id ? idLink(p.id) : p.text));
      const references = limitReferences(r.source);
      const evidence = r.evidence && r.evidence !== entry.reach?.evidence ? r.evidence : "";
      ul.append(el("li", {}, el("div", {}, ...text), el("div", { class: "source", textContent: limitText(r.source) }),
        references.length || evidence ? contextDisclosure(t("evidence"), el("div", {},
          ...references.map((reference) => el("div", { textContent: reference })),
          evidence ? el("div", { textContent: evidence }) : null)) : null));
    }
  });
}




export function renderTokens() {
  return renderChildren($("tokens"), (box) => {
    const tokens = [...new Set(sourceTokens(state.entry) ?? [])];
    const have = new Set(parse(field.text));
    for (const token of tokens) {
      const chip = el("span", { class: "chip", "data-key": token, title: t("Insert {token}", { token }), textContent: token.slice(1, -1) });
      const probe = document.createElement("span");
      renderUnits(probe, [token], state.codec);
      chip.className = `${probe.firstChild.className}${have.has(token) ? "" : " missing"}`;
      chip.addEventListener("mousedown", (e) => e.preventDefault());
      chip.addEventListener("click", () => field.insert([token]));
      box.append(chip);
    }
  });
}




export function renderWindow(entry) {
  const w = entry.window;
  if (isSite()) {
    dl($("window"), [[N_("Window"), t("None: the page lays this text out")]]);
    return;
  }
  if (isDraft(state.index)) {
    dl($("window"), [[N_("Window"), t("Not measured")], [N_("Encoding"), t("Not verified")], [N_("Build"), t("Unavailable")]]);
    return;
  }
  if (!w) {
    dl($("window"), [[N_("Window"), t("none: no inventory entry for this id")]]);
    return;
  }
  const rows = slotRows(w, state.slotSources, state.slotDrafts);
  const slots = rows.length ? el("div", { class: "slot-sources" }, ...rows.map((row) =>
    el("div", { class: "slot-source" },
      el("b", { textContent: keepUnits(t("{name}: {width} px", { name: row.label, width: row.width })) }),
      el("div", { class: "muted", textContent: row.state }),
      ...row.parts.map((part) => el("div", {}, part.id
        ? el("a", { href: "#", textContent: part.label, on: { click: (event) => { event.preventDefault(); openId(part.id); } } })
        : part.label, keepUnits(` — ${part.width} px`), part.note ? ` (${part.note})` : "")),
      row.alternatives.length ? el("div", {}, el("span", { textContent: t("Other sources at the same maximum") }),
        ...row.alternatives.map((part) => el("div", {}, part.id
          ? el("a", { href: "#", textContent: part.label, on: { click: (event) => { event.preventDefault(); openId(part.id); } } })
          : part.label, keepUnits(` — ${part.width} px`)))) : null,
      row.reason ? el("div", { textContent: keepUnits(t("Computed: {width} px. Override: {reason}",
        { width: row.computed, reason: row.reason })) }) : null))) : null;
  const widthSource = sourceOf(w, "width");
  const consumerReason = (c) => {
    const src = c.source?.rule !== w.sources?.width?.rule ? sourceOf({ sources: { width: c.source } }, "width") : null;
    const unknown = !c.instant_print && c.arrow_source === "default" && !c.arrow_confirmed;
    return src?.references?.length || src?.evidence || unknown
      ? contextDisclosure(t("evidence"), el("div", {},
        ...(src?.references ?? []).map((reference) => el("div", { textContent: reference })),
        src?.evidence ? el("div", { textContent: src.evidence }) : null,
        unknown ? el("div", { class: "unmeasured",
          textContent: t("Default prompt arrow; not confirmed in this window in the game.") }) : null), "src") : null;
  };
  const others = (w.other ?? []).map((o) => keepUnits(t("{n} px in {where} ({font})",
    { n: o.width, where: limitText(o.where), font: o.font })));
  for (const c of w.consumers ?? []) {
    if (c.instant_print && limitText(c.name) !== widthSource?.text) {
      others.push(el("div", {}, keepUnits(t("{name}: {width} px", { name: limitText(c.name), width: c.width })), consumerReason(c)));
    }
  }
  const pair = w.pair ? pairLabel(w.pair) : null;
  const arrows = (w.consumers ?? []).filter((c) => !c.instant_print).map((c) => el("div", {},
    keepUnits(w.consumers.length === 1 ? `${c.arrow} px` : t("{name}: arrow {n} px", { name: limitText(c.name), n: c.arrow })),
    consumerReason(c)));
  dl($("window"), [
    [N_("Kind"), w.kind],
    [N_("Width"), w.keys != null ? null : withSource(keepUnits(`${w.width} px`), widthSource, true)],
    [N_("Keys"), withSource(w.keys, sourceOf(w, "keys"))],
    [N_("Lines"), w.lines != null ? withSource(w.lines, sourceOf(w, "lines"), false, widthSource?.text) : t("no limit")],
    [N_("Wrap target"), w.target ? `${w.target} px` : null],
    [N_("Prompt arrow"), arrows.length ? el("div", {}, ...arrows)
      : !w.consumers?.length && w.arrow ? t("{n} px after a line before a scroll or a new box", { n: w.arrow }) : null],
    [N_("Runtime scrolling"), w.newline_scroll_after != null
      ? t("The game keeps the first {n} newline breaks and scrolls at every later newline. The stored text keeps its breaks.",
        { n: w.newline_scroll_after }) : null],
    [N_("Characters"), withSource(w.chars, sourceOf(w, "chars"))],
    [N_("Bytes"), withSource(w.bytes, sourceOf(w, "bytes"))],
    [N_("Font"), w.font],
    [N_("English lines"), w.source_lines ? w.source_lines.join(", ") : null],
    [N_("Shown in"), w.host ? idLink(w.host) : null],
    [N_("Slot widths"), slots],
    [N_("Other windows"), others.length ? el("div", {}, ...others) : null],
    [N_("Pair"), pair],
  ]);
}




export function rerun() {
  if (state.entry) runCheck();
}




export function rowElement(i, pos) {
  const r = state.rows[i];
  prepareRow(r);
  const ed = state.edits.get(r.id);
  const probs = ed?.problems ?? r.problems;
  const errors = probs.filter((p) => p.severity === "error").length;
  const warnings = probs.length - errors;
  const tr = ed ? ed.translation : r.translation;
  const l1 = el("div", { class: "l1" },
    el("span", { class: `dot ${DOTS[r.status] ?? ""}`, title: statusLabel(r.status), "data-key": "status" }),
    el("span", { class: "eid", textContent: r.id, "data-key": "id" }),
    ed ? el("span", { class: "mark edited", textContent: t("edited"), "data-key": "edited" }) : null,
    probs.some(isOverflow) ? el("span", { class: "mark err", textContent: t("overflow"), "data-key": "overflow" }) : null,
    errors ? el("span", { class: "mark err", "data-key": "errors",
      textContent: errors > 1 ? t("{n} errors", { n: errors }) : t("{n} error", { n: errors }) }) : null,
    warnings ? el("span", { class: "mark warn", "data-key": "warnings",
      textContent: warnings > 1 ? t("{n} warnings", { n: warnings }) : t("{n} warning", { n: warnings }) }) : null,
    r.reach === "never shown" ? el("span", { class: "mark never", textContent: t("never shown"), "data-key": "reach" }) : null,
    r.shortened?.length ? el("span", { class: "mark warn", textContent: t("shortened"), "data-key": "shortened",
      title: t("shows the label of {terms}", { terms: r.shortened.join(", ") }) }) : null,
    r.kind ? el("span", { class: "kind", textContent: r.kind, "data-key": "kind" }) : null);
  return el("div", {
    class: `row${i === state.cur ? " sel" : ""}`,
    role: "option",
    "data-key": r.id,
    "aria-selected": String(i === state.cur),
    style: `top:${pos * entries.row}px`,
    on: { click: () => openRow(i) },
  },
  l1,
  typeof r.source_text === "string" ? el("div", { class: "l2", textContent: flat(r.source_text) }) : null,
  el("div", { class: `l3${tr ? "" : " none"}`, textContent: tr ? flat(tr)
    : ed ? t(TRANSLATION_STATES[translationState(ed)]) : statusLabel(r.status === "nothing" ? r.status : "untranslated") }));
}




export function runCheck() {
  const e = state.entry;
  if (!e) return;
  const request = ++state.checkRequest;
  const text = field.text;
  const show = (res) => {
    if (request !== state.checkRequest || state.entry !== e) return;
    state.result = res;
    if (res.window) {
      e.window = res.window;
      renderWindow(e);
      showWindowChip(e);
    }
    if (state.edits.get(e.id)) {
      state.edits.checked(e.id, res.problems, res.exact);
      renderList();
      showCounts();
    }
    renderBars(e, res);
    renderProblems(res);
    renderPreview(e, res);
  };
  const failed = (err) => {
    console.error(err);
    show({ ...estimate(e, text), problems: [{ message: checkFailed(err.message || err), severity: "error" }] });
  };
  if (isDraft(state.index)) {
    show(draftCheck(e, text, { game: !isSite() }));
  } else if (state.check?.ready) {
    try {
      const generation = state.generation;
      const load = async (id) => {
        const i = state.byId.get(id);
        if (i == null) throw new Error(`Substitution source unavailable: ${id}`);
        const source = await state.chunks.entry(state.rows[i]);
        if (generation !== state.generation) return null;
        return withGameText(source, i);
      };
      loadSlotSources(e, state.index.fill_sources, load, state.edits).then(({ sources, drafts }) => {
        if (request !== state.checkRequest || state.entry !== e) return;
        state.slotSources = sources;
        state.slotDrafts = drafts;
        return Promise.resolve(state.check.run(e, text, sources, drafts)).then(show);
      }).catch(failed);
    } catch (err) {
      failed(err);
    }
  } else show(estimate(e, text));
}




export function setView(view, reveal = true, select = true) {
  if (!state.lang) return;
  const pages = view === "keyboards" && Boolean(state.index?.keyboard); // Needs only the keyboard section.
  if (isDraft(state.index) && view !== "texts" && view !== "enginerules" && !pages) view = "texts";
  if (view === "keyboards" && !pages) view = "texts";
  state.view = view;
  for (const v of VIEWS) {
    $(v).hidden = v !== view;
    $(`view-${v}`).classList.toggle("on", v === view);
    $(`view-${v}`).setAttribute("aria-pressed", String(v === view));
  }
  $("show-ctx").hidden = view !== "texts";
  drawer(null);
  if (view === "texts") {
    if (reveal) entries.shown(state.filtered.indexOf(state.cur));
    else entries.render();
  }
  if (select && view === "terms") terms.show();
  if (select && view === "families") families.show();
  if (select && view === "enginerules") engineRules.show();
  if (select && view === "pictures" && pictures.cur === null) pictures.reset();
  if (select && view === "keyboards" && keyboards.cur === null) keyboards.reset();
  if (select && view === "fonts" && fontSheets.cur === null) fontSheets.reset();
  trail.replace(viewHash());
}




/** Show the place a hash names in its view. */
export async function showPlace(place) {
  if (place.view === "fonts" && place.kind === "glyph") {
    if (!fontSheets.glyph(place.id, place.code)) {
      showText($("workspace-lookup-status"), "Object not found: {id}", { id: `${place.id}/${place.code ?? ""}` });
      return;
    }
    plainText($("workspace-lookup-status"), "");
  }
  if (place.view === "keyboards" && (place.kind || place.id !== null)) {
    const found = place.kind === "chat" ? keyboards.chatRow(place.id)
      : keyboards.kb?.pages[keyboards.pageIndex(place.id, place.kind)];
    if (!found) {
      showText($("workspace-lookup-status"), "Object not found: {id}", { id: place.id ?? "" });
      return;
    }
    plainText($("workspace-lookup-status"), "");
  }
  setView(place.view, true, place.id === null);
  if (state.view !== place.view || place.id === null) return;
  const open = { texts: openId, terms: (id) => terms.show(id), families: (id) => families.show(id),
    pictures: (id) => pictures.open(id), keyboards: (id) => keyboards.openTarget(id, place.kind),
    fonts: (id) => fontSheets.open(id, place.kind === "glyph" ? place.code : null),
    enginerules: (id) => engineRules.show(id) };
  await open[place.view](place.id);
}




/** The hash of the open view at its current item. */
export function viewHash() {
  const view = state.view;
  if (view === "keyboards") return gameHash(state.profile.id, state.lang.code, view, ...keyboards.target);
  if (view === "fonts") return gameHash(state.profile.id, state.lang.code, view, ...fontSheets.address);
  const id = view === "pictures" ? pictures.cur
    : view === "terms" ? terms.cur
    : view === "families" ? families.cur
    : view === "enginerules" ? engineRules.cur
    : null;
  const entry = view === "texts" ? state.rows[state.cur]?.id : null;
  return entry ? entryHash(entry) : gameHash(state.profile.id, state.lang.code, ...(id !== null ? [view, id] : view === "enginerules" ? [view] : []));
}





export const pictures = new PicturesView({
  reference: (id) => setObjectReference("picture", id),
  index: () => state.index,
  gfx: () => state.gfx,
  gfxState: () => state.gfxState,
  uploads: () => state.uploads,
  noteEdits: () => state.noteEdits,
  languages: () => state.profile.languages.map((language) => language.code),
  english: () => state.english,
  changed: updateEditCount,
  loadRom: () => $("rom-file").click(),
  setHash: (id) => {
    if (state.view === "pictures" && state.lang) trail.replace(gameHash(state.profile.id, state.lang.code, "pictures", id));
  },
}, $);




export const terms = new TermsView({
  reference: (id) => setObjectReference("term", id),
  index: () => state.index,
  data: () => state.termsData,
  load: async () => {
    if (!state.index?.terms) return;
    if (state.termsData) return; // Reuse terms already loaded by the Families view.
    const generation = state.generation;
    const data = await loadTerms(state.index, new URL(dirOf(state.lang.index) + state.index.terms.path, DATA()).href);
    if (generation === state.generation) state.termsData = data;
  },
  edits: () => state.termEdits,
  noteEdits: () => state.noteEdits,
  languages: () => state.profile.languages.map((language) => language.code),
  rows: () => state.rows,
  order: (id) => state.byId.get(id),
  row: (id) => state.rows[state.byId.get(id)],
  textOf: (row) => state.edits.get(row.id)?.translation ?? row.translation,
  entry: async (id) => {
    const i = state.byId.get(id);
    const entry = await state.chunks.entry(state.rows[i]);
    return withGameText(entry, i);
  },
  check: () => (state.check?.ready && state.check.renameFn ? state.check : null),
  checkNote: () => t({ loading: N_("it is loading"), failed: N_("it is unavailable") }[state.checkState] ??
    N_("the bundle has no rename preview (gen3tl web bundle)")),
  openEntry: (id) => openPlace({ view: "texts", id }),
  changed: updateEditCount,
  setHash: (id) => {
    if (state.view === "terms" && state.lang) trail.replace(gameHash(state.profile.id, state.lang.code, "terms", id));
  },
  familiesOf: (id) => families.of(id),
  openFamily: (id) => openPlace({ view: "families", id }),
}, $);




export const families = new FamiliesView({
  reference: (id) => setObjectReference("family", id),
  index: () => state.index,
  terms: () => state.termsData?.terms ?? null,
  loadTerms: () => terms.host.load(),
  dataset: () => (state.profile?.pokedex ? getJson(new URL(state.profile.pokedex, DATA()).href) : Promise.resolve(null)),
  edits: () => state.termEdits,
  typeName: (n) => state.rows[state.byId.get(tableKey(`types:${n}`))]?.source_text || t("Type {n}", { n }),
  openTerm: (id) => openPlace({ view: "terms", id }),
  setHash: (id) => {
    if (state.view === "families" && state.lang) trail.replace(gameHash(state.profile.id, state.lang.code, "families", id));
  },
  counted: (n) => {
    setText($("count-families"), n ? formatNumber(n) : "");
  },
}, $);





export const keyboards = new KeyboardsView({
  reference: (id) => setObjectReference("keyboard-page", id),
  index: () => state.index,
  glyphs: (style) => state.glyphs[style] ?? state.glyphs[state.index?.fonts?.main] ?? null,
  codec: () => state.codec,
  exact: () => latin() !== null,
  english: () => state.english,
  gfx: () => state.gfx,
  gfxState: () => state.gfxState,
  keyboardEdits: () => state.keyboardEdits,
  changed: updateEditCount,
  loadRom: () => $("rom-file").click(),
  openEntry: (id) => openPlace({ view: "texts", id }),
  openPicture: (id) => openPlace({ view: "pictures", id }),
  setHash: (target) => {
    if (state.view === "keyboards" && state.lang) trail.replace(gameHash(state.profile.id, state.lang.code, "keyboards", ...target));
  },
}, $);




export const fontSheets = new FontsView({
  reference: (id) => setObjectReference("font", id),
  index: () => state.index,
  rom: () => (latin() ? state.rom.bytes : null),
  codec: () => state.codec,
  edits: () => state.glyphEdits,
  changed: glyphsChanged,
  dataUrl: (path) => new URL(dirOf(state.lang.index) + path, DATA()).href,
  setHash: (id, code) => {
    if (state.view === "fonts" && state.lang) {
      trail.replace(gameHash(state.profile.id, state.lang.code, "fonts", id, ...(code === null ? [] : ["glyph", code])));
    }
  },
}, $);




export const engineRules = new EngineRulesView({
  reference: (id) => setObjectReference("engine-rule", id),
  index: () => state.index,
  row: (id) => state.rows[state.byId.get(id)],
  edits: () => state.edits,
  ruleEdits: () => state.ruleEdits,
  check: () => state.check,
  loadEntry: async (id) => {
    const generation = state.generation;
    const i = state.byId.get(id);
    const entry = await state.chunks.entry(state.rows[i]);
    if (generation !== state.generation) return null;
    withGameText(entry, i);
    return entry;
  },
  changed: () => { updateEditCount(); renderList(); },
  openEntry: (id) => openPlace({ view: "texts", id }),
  openView: (view) => openPlace({ view, id: null }),
  setHash: (id) => {
    if (state.view === "enginerules" && state.lang) {
      trail.replace(gameHash(state.profile.id, state.lang.code, "enginerules", ...(id === null ? [] : [id])));
    }
  },
}, $);




export const field = new Field($("field"), {
  get codec() {
    return state.codec;
  },
  kind: () => state.entry?.window?.kind ?? "dialogue",
  onChange: onEdit,
});
