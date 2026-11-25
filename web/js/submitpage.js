// The submit page: drafts before and after, with widths.
// The issue carries what its repository accepts.
// The JSON file carries all.

import { setToolContext, startPage } from "./site-shell.js";
import { checkText, setMessages, setReasons } from "./checktext.js";
import { Chunks, dirOf, getJson, loadIndex, projectProfiles, verifyProjectIndex, versionLabel } from "./data.js";
import { download, el } from "./dom.js";
import { isDraft, translationState } from "./draft.js";
import { messagesOf } from "./editorstore.js";
import { Codec, entryMeasure } from "./font.js";
import { draftFonts, drawArt, sheetsOf, styleGlyphs } from "./fonts.js";
import { languageName, loadGameTexts } from "./landing.js";
import { digest } from "./patcher-core.js";
import { fromB64 } from "./pictures.js";
import { chosenGame, gameHash, loadRegistry, TOOLKIT_REPOSITORY } from "./registry.js";
import { CAPTURE_ROLES, Edits, GlyphEdits, KeyboardEdits, NoteEdits, PictureEdits, RuleEdits, TERM_COLUMNS, TermEdits,
  browserStorage, exportDoc, exportName, noteProblems, noteRevision } from "./store.js";
import { gameText } from "./site-model.js";
import { submissionDoc, submissionParts, submissionProblem, submitAvailable, submitChanges } from "./submit.js";
import { N_, errorText, onSiteLanguage, plainText, prepareSiteLanguage, showText, t, uiError, uiLanguage } from "./ui.js";

// Page model without DOM dependencies.

const SITE = "site";

/** The site route of a hash. */
function siteRouteOf(hash) {
  try {
    const parts = hash.replace(/^#/, "").split("/").filter(Boolean).map(decodeURIComponent);
    return parts[0] === SITE ? { game: SITE, language: parts[1] ?? null, tail: parts.slice(2) } : null;
  } catch {
    return null;
  }
}

/** The site project from a languages manifest. */
export function siteGame(manifest, data) {
  const profile = manifest?.profiles?.find((p) => p.id === SITE);
  const languages = [...new Set((manifest?.languages ?? []).filter((l) => l.profile === SITE).map((l) => l.code))];
  if (!profile || !languages.length) return null;
  return { id: SITE, name: "", stage: "interface", engine: "unspecified", languages, data,
    repository: TOOLKIT_REPOSITORY };
}

// a count in the shown language
const plural = (n, one, many) => t(n === 1 ? one : many, { n });

/**
 * Widths by box and line: [[{text, width}]].
 * - width null: not encodable
 */
export function lineWidths(text, measure, version = "") {
  const t = version ? text.replaceAll("@VERSION@", version) : text;
  return t.split("\\p").map((box) => box.split(/\\[nl]/).map((line) => {
    let width = null;
    try {
      width = measure.lineWidth(line);
    } catch {
      width = null;
    }
    return { text: line, width };
  }));
}

// keyboard windows count keys, not pixels
const limitOf = (window) => (window && window.keys == null && window.width ? window.width : null);

/** The width cell: the widest line against the window. */
export function widthCell(text, lines, window = null) {
  if (!text) return { label: "—", state: "plain", title: t("no text") };
  if (!lines) return { label: "—", state: "plain", title: t("not measured: no game font for this catalog") };
  const limit = limitOf(window);
  const multi = lines.length > 1;
  const all = lines.flatMap((box, b) => box.map((l, i) =>
    ({ ...l, name: multi ? t("line {box}.{n}", { box: b + 1, n: i + 1 }) : t("line {n}", { n: i + 1 }) })));
  const bad = all.some((l) => l.width === null);
  const widest = Math.max(0, ...all.map((l) => l.width ?? 0));
  const over = bad || (limit !== null && all.some((l) => l.width > limit));
  const px = (w) => (w === null ? t("not encodable") : `${w}${limit ? ` / ${limit}` : ""} px`);
  const title = all.map((l) => `${l.name}: ${px(l.width)}`).join("\n");
  return { label: bad ? t("not encodable") : `${widest} px`, state: over ? "over" : limit === null ? "plain" : "ok",
    title };
}

/** The window as the entry head names it. */
export function windowLabel(window) {
  if (!window) return "";
  const size = window.keys != null ? t("{n} keys", { n: window.keys }) : window.width ? `${window.width} px` : "";
  return [window.kind, size].filter(Boolean).join(" · ");
}

/** Inserted values: the {NAME} tokens the game fills. */
export function slotsOf(text, codec) {
  const out = new Set();
  for (const m of text.matchAll(/\{([^{}]*)\}/g)) {
    const name = m[1].trim().split(/\s+/)[0];
    if (Object.hasOwn(codec?.placeholders ?? {}, name)) out.add(m[0]);
  }
  return [...out];
}

/** A changed text with its before, after and check state. */
export function textChange(id, edit, row) {
  const before = typeof edit.base === "string" ? edit.base : row?.translation ?? "";
  const beforeState = edit.base_state ?? translationState({ translation: before });
  const problems = edit.problems ?? [];
  return {
    id, source_text: row?.source_text ?? edit.source_text ?? "", before, after: edit.translation, maps: row?.maps ?? [],
    beforeState, afterState: translationState(edit), fresh: beforeState === "original",
    stale: Boolean(row) && (row.translation !== before
      || translationState(row) !== beforeState
      || (typeof edit.source_text === "string" && edit.source_text !== row.source_text)),
    errors: problems.filter((p) => p.severity === "error").length,
    warnings: problems.filter((p) => p.severity === "warning").length,
    problems: problems.map((p) => checkText(p.message)),
  };
}

const valueOf = (row) => row ? { translation: row.translation, state: translationState(row) } : null;
const stable = (value) => JSON.stringify(value, (_, item) => item && typeof item === "object" && !Array.isArray(item)
  ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => a.localeCompare(b))) : item);
const sameValue = (a, b) => stable(a) === stable(b);
const operandIds = (rule) => [...new Set((rule?.operands ?? []).flatMap((operand) => operand.bindings.map((b) => b.entry)))];

/** Group backing edits without duplicating catalog records. */
export function ruleChanges(index, edits, proposals = []) {
  const rows = new Map((index.list ?? []).map((row) => [row.id, row]));
  const rules = new Map((index.engine_rules?.rules ?? []).map((rule) => [rule.id, rule]));
  const policies = new Map(proposals.map((record) => [record.id, record]));
  const changed = new Map([...edits.map].filter(([, edit]) => !sameValue(valueOf(edit),
    { translation: edit.base, state: edit.base_state ?? translationState({ translation: edit.base }) })));
  const ids = new Set(policies.keys());
  for (const rule of rules.values()) {
    if (rule.kind === "catalog" && operandIds(rule).some((id) => changed.has(id))) ids.add(rule.id);
  }
  for (const edit of changed.values()) for (const id of Object.keys(edit.rule_baselines ?? {})) ids.add(id);
  return [...ids].map((id) => {
    const rule = rules.get(id), policy = policies.get(id);
    const baselines = [...changed.values()].map((edit) => edit.rule_baselines?.[id]).filter(Boolean);
    const current = (sid) => {
      const row = rows.get(sid);
      return row ? valueOf({ ...row, state: row.state ?? rule?.sources?.find((source) => source.entry === sid)?.state }) : null;
    };
    const bindings = [...new Set([...operandIds(rule), ...baselines.flatMap((base) => Object.keys(base.entries))])];
    const linked = new Set([...bindings, ...(rule?.related ?? [])]);
    const entries = [...edits.map.keys()].filter((sid) => linked.has(sid));
    const stale = !rule || (policy ? policy.capability_sha256 !== rule.capability_sha256
      || rule.policies?.length !== 1 || !sameValue(policy.base, rule.policies[0].current) : false)
      || baselines.some((base) => base.capability_sha256 !== rule.capability_sha256
        || Object.entries(base.entries).some(([sid, value]) => !sameValue(value, current(sid))))
      || bindings.some((sid) => changed.has(sid) && !sameValue(current(sid),
        { translation: changed.get(sid).base, state: changed.get(sid).base_state }));
    return { id, title: rule?.title ?? id, rule, policy, entries, stale,
      baselineMissing: !policy && bindings.some((sid) => changed.has(sid) && !changed.get(sid).rule_baselines?.[id]),
      operands: bindings.map((sid) => ({ id: sid, original: rows.get(sid)?.source_text ?? null,
        current: current(sid), proposed: valueOf(edits.get(sid)) ?? current(sid) })) };
  });
}

/** Draft ids in catalog order; ids the catalog lacks apart. */
export function draftOrder(rows, ids) {
  const want = new Set(ids);
  const known = new Set(rows.map((r) => r.id));
  return {
    order: rows.filter((r) => !r.owner && want.has(r.id)).map((r) => r.id),
    missing: ids.filter((id) => !known.has(id)),
  };
}

/** Text areas as the width cell shows them: {label, title}. */
export function areasLabel(areas) {
  const a = areas ?? [];
  if (!a.length) return { label: "—", state: "plain", title: t("no text area") };
  return {
    label: a.length === 1 ? `${a[0][2]}×${a[0][3]} px` : plural(a.length, N_("{n} area"), N_("{n} areas")),
    state: "plain",
    title: a.map(([x, y, w, h]) => t("{w}×{h} px at {x},{y}", { w, h, x, y })).join("\n"),
  };
}

/** A changed picture with its before and after sides. */
export function pictureChange(id, upload, graphics) {
  const row = graphics?.inventory?.find((r) => r.id === id) ?? null;
  const project = graphics?.edits?.[id] ?? null;
  const side = (s, name) => ({ png: s.png ?? null, areas: s.areas ?? [], tiles: s.tiles ?? null, name });
  return {
    id, source_text: row?.source_text ?? "", category: row?.category ?? "", screen: row?.screen ?? "",
    tiles: Number(row?.tiles) || null,
    before: project ? side(project, t("project's edit")) : null,
    after: side(upload, upload.name ?? t("upload")),
  };
}

/** Tiles past the graphic's own: "3 tiles appended", or "". */
export function appendedLabel(tiles, own) {
  const n = tiles && own ? tiles - own : 0;
  return n > 0 ? plural(n, N_("{n} tile appended"), N_("{n} tiles appended")) : "";
}

/** The changed fields of a term record. */
export function termRows(record, columns = TERM_COLUMNS) {
  const fieldOf = Object.fromEntries(Object.entries(columns).map(([f, c]) => [c, f]));
  const out = [];
  for (const [col, after] of Object.entries(record.changes)) {
    const field = fieldOf[col] ?? col;
    const before = record.base[col];
    if (field === "forms") {
      const keys = [...new Set([...Object.keys(before ?? {}), ...Object.keys(after ?? {})])];
      for (const k of keys) {
        const [a, b] = [before?.[k] ?? "", after?.[k] ?? ""];
        if (a !== b) out.push({ field: `form ${k}`, before: a, after: b, text: true });
      }
    } else {
      out.push({ field, before: before ?? "", after: after ?? "", text: field === "translation" || field === "label" });
    }
  }
  return out;
}

/** The keyboard changes of an export. */
export function keyboardRows(keyboard) {
  if (!keyboard) return [];
  const out = Object.entries(keyboard.rows ?? {}).map(([id, after]) => ({ id, before: keyboard.base.rows[id], after }));
  if (keyboard.pages) out.push({ id: t("page order"), before: keyboard.base.pages.join(" → "), after: keyboard.pages.join(" → ") });
  return out;
}

/** Nested fields expose exact citation and evidence differences. */
export function noteRows(change) {
  const rows = [];
  const walk = (before, after, path) => {
    if (noteRevision(before) === noteRevision(after)) return;
    if (Array.isArray(before) || Array.isArray(after)) {
      for (let i = 0; i < Math.max(before?.length ?? 0, after?.length ?? 0); i++) {
        walk(before?.[i], after?.[i], [...path, i + 1]);
      }
    } else if (before && typeof before === "object" || after && typeof after === "object") {
      for (const key of new Set([...Object.keys(before ?? {}), ...Object.keys(after ?? {})])) {
        walk(before?.[key], after?.[key], [...path, key]);
      }
    } else rows.push({ path, before: before ?? null, after: after ?? null });
  };
  walk(change.base, change.proposed, []);
  return rows;
}

/** Published revisions and English bindings must still match. */
export function noteIssues(change, index, entries = new Map()) {
  const problems = change.operation === "remove" ? [] : noteProblems(change.proposed, change.base, change.attachments);
  const current = index.notes?.note.find((note) => note.id === change.id) ?? null;
  const entry = change.subject.kind === "entry" ? entries.get(change.subject.id) : null;
  if (change.base_revision !== noteRevision(current) || change.subject.kind === "entry"
    && (!entry || change.binding?.source_sha256 !== entry.source_sha256)) {
    problems.push(N_("The published note or entry changed. Compare the versions before keeping your draft."));
  }
  return problems;
}

export function noteCounts(changes = []) {
  return Object.fromEntries(["add", "edit", "remove"].map((operation) =>
    [operation, changes.filter((change) => change.operation === operation).length]));
}

/** Counts of an export document. */
export function exportCounts(doc) {
  const n = { texts: doc.entries.length, pictures: doc.pictures?.length ?? 0, terms: doc.terms?.length ?? 0,
    glyphs: doc.glyphs?.length ?? 0, keyboard: keyboardRows(doc.keyboard).length, rules: doc.engine_rules?.length ?? 0,
    notes: doc.note_changes?.length ?? 0 };
  return { ...n, total: n.texts + n.pictures + n.terms + n.glyphs + n.keyboard + n.rules + n.notes };
}

/** "Submit 3 changes". */
export const headingOf = (counts) => (counts.total ? plural(counts.total, N_("Submit {n} change"), N_("Submit {n} changes"))
  : t("Nothing to submit"));

/** The change counts, as "3 texts, 0 pictures, 0 terms". */
export const countsLabel = (c) => [plural(c.texts, N_("{n} text"), N_("{n} texts")),
  plural(c.pictures, N_("{n} picture"), N_("{n} pictures")), plural(c.terms, N_("{n} term"), N_("{n} terms")),
  c.glyphs ? plural(c.glyphs, N_("{n} glyph"), N_("{n} glyphs")) : null,
  c.rules ? plural(c.rules, N_("{n} engine rule"), N_("{n} engine rules")) : null,
  c.keyboard ? plural(c.keyboard, N_("{n} keyboard change"), N_("{n} keyboard changes")) : null,
  c.notes ? plural(c.notes, N_("{n} note change"), N_("{n} note changes")) : null].filter(Boolean).join(", ");

/** The JSON file: the issue's document with all edits. */
export function fileDoc(doc, base) {
  return { ...submissionDoc(doc, base), pictures: doc.pictures ?? [], terms: doc.terms ?? [], glyphs: doc.glyphs ?? [],
    ...(doc.keyboard ? { keyboard: doc.keyboard } : {}) };
}

/** Why the issue step is unavailable, or "". */
export function issueBlocked(repository, counts) {
  if (!submitAvailable(repository)) {
    return t("This site names no repository for submissions: send the JSON file to the project instead.");
  }
  const total = counts.total ?? (counts.texts ?? 0) + (counts.terms ?? 0) + (counts.pictures ?? 0)
    + (counts.glyphs ?? 0) + (counts.keyboard ?? 0) + (counts.rules ?? 0) + (counts.notes ?? 0);
  if (!total) {
    return t("Nothing to submit: make an edit first.");
  }
  return "";
}

/** Glyph widths without a ROM, with `drafts` as redrawn. */
export function glyphsOf(index, drafts = []) {
  return styleGlyphs(draftFonts(index, drafts));
}


const $ = (id) => document.getElementById(id);

const state = {
  storage: null,
  game: null,
  code: null,
  index: null,
  codec: null,
  glyphs: null,
  rows: new Map(),
  entries: new Map(),
  edits: null,
  uploads: null,
  termEdits: null,
  glyphEdits: null,
  keyboardEdits: null,
  ruleEdits: null,
  noteEdits: null,
  digests: new Map(),
  doc: null,
  missing: [],
  loadError: null,
};

function recall(key) {
  try {
    return state.storage?.getItem(`gen3tl-editor:${key}`) ?? null;
  } catch {
    return null;
  }
}

function remember(key, value) {
  try {
    if (value) state.storage?.setItem(`gen3tl-editor:${key}`, value);
    else state.storage?.removeItem(`gen3tl-editor:${key}`);
  } catch {
    // this page only
  }
}

const notesKey = () => `submit-notes:${state.code}:${state.index.profile}`;
const base = (id) => state.edits.get(id)?.base;
const currentDoc = () => ({ ...state.doc, notes: $("notes").value });

const chip = (cls, text, title = "") => el("span", { class: `chip ${cls}`, textContent: text, title });

function widthSpan(cell) {
  return el("span", { class: { ok: "w ok", over: "w err", plain: "w" }[cell.state], textContent: cell.label,
    title: cell.title });
}

const oldText = (text, empty) => (text ? el("span", { class: "old", textContent: text })
  : el("span", { class: "old muted", textContent: empty }));

const newText = (text) => el("span", {}, text ? el("span", { class: "new", textContent: text })
  : el("span", { class: "muted", textContent: t("empty") }));

const key = (k) => el("span", { class: "k", textContent: k });
const TEXT_STATES = { original: N_("Inherited original"), empty: N_("Off"), text: N_("Custom") };
const textValue = (value) => value ? value.translation || t(TEXT_STATES[value.state]) : t("Text unavailable");

/** Review the combined rule and linked catalog edits. */
export function ruleItem(change, game, language) {
  const { rule, policy } = change;
  const editorLink = (id, engine = false) => `editor${gameHash(game, language,
    ...(engine ? ["enginerules", id] : [id]))}`;
  const policyText = (value) => {
    if (!value || !rule) return t("Text unavailable");
    const units = rule?.policies?.[0]?.kind === "code_replacement";
    const label = units ? value.enabled ? t("Use metric units") : t("Use original units")
      : value.enabled ? t("Off") : t("Show number marker");
    const fields = { height_unit: N_("Height unit"), weight_unit: N_("Weight unit") };
    return [label, ...(units && value.enabled ? (rule.policies[0].fields ?? []).map((field) =>
      `${t(fields[field] ?? field)}: ${value.fields?.[field] ?? t("Use catalog text")}`) : [])].join(" · ");
  };
  const modes = [["original", N_("Original")], ["current", N_("Current")], ["proposed", N_("Proposed")]];
  return el("article", { class: "submit-item" },
    el("div", { class: "top" }, el("a", { href: editorLink(change.id, true), textContent: gameText(state.game, change.title) }),
      el("span", { class: "id", textContent: change.id }),
      chip("plain", t("Also changes {n} catalog entries", { n: change.entries.length }))),
    change.stale ? el("p", { class: "note warn", role: "alert",
      textContent: t("The rule changed since this draft was created") }) : null,
    change.baselineMissing ? el("p", { class: "note warn",
      textContent: t("Rule baseline unavailable; review the current rule before submitting.") }) : null,
    el("div", { class: "er-comparison" }, modes.map(([mode, label]) => el("section", { class: "er-state" },
      el("h2", { class: "submit-subhead", textContent: t(label) }),
      policy ? el("p", { textContent: policyText(mode === "original" ? { enabled: false, fields: {} }
        : mode === "current" ? rule?.policies?.[0]?.current : policy.value) }) : null,
      change.operands.map((operand) => el("div", { class: "er-value" },
        el("a", { href: editorLink(operand.id), textContent: operand.id }),
        el("p", { class: "er-text", textContent: mode === "original"
          ? operand.original ?? t("Original text unavailable without a ROM") : textValue(operand[mode]) })))))),
    change.entries.length ? el("ul", {}, change.entries.map((id) => el("li", {},
      el("a", { href: editorLink(id), textContent: id })))) : null);
}

function measureOf(window) {
  return state.glyphs ? entryMeasure(state.index, state.glyphs, state.codec, window) : null;
}

function textItem(c) {
  const w = state.entries.get(c.id)?.window ?? null;
  const m = measureOf(w);
  const cell = (t) => widthCell(t, m && t ? lineWidths(t, m, state.index.version) : null, w);
  const maps = c.maps.length
    ? chip("plain", c.maps.length > 1 ? `${c.maps[0]} +${c.maps.length - 1}` : c.maps[0], c.maps.join(", ")) : null;
  const slots = slotsOf(c.source_text, state.codec).map((s) => {
    let px = null;
    try {
      px = m?.tokenWidth(s) ?? null;
    } catch {
      px = null;
    }
    return chip("ph", s, px === null ? t("inserted value") : t("inserted value, measured as {px} px", { px }));
  });
  const top = el("div", { class: "top" }, el("span", { class: "id", textContent: c.id }),
    windowLabel(w) ? chip("plain", windowLabel(w)) : null, maps,
    c.fresh ? chip("accent", t("new translation")) : null, ...slots,
    c.stale ? chip("warn", t("catalog changed since the edit"),
      t("The catalog text changed after this edit; the submission check refuses it until the entry is edited again.")) : null,
    c.errors ? chip("err", plural(c.errors, N_("{n} error"), N_("{n} errors")), c.problems.join("\n"))
      : c.warnings ? chip("warn", plural(c.warnings, N_("{n} warning"), N_("{n} warnings")), c.problems.join("\n")) : null);
  return el("article", { class: "submit-item" }, top,
    c.source_text ? el("p", { class: "en", textContent: c.source_text.replace(/\\[nlp]/g, " ") }) : null,
    el("div", { class: "diff" },
      key(t("old")), oldText(c.before, t(TEXT_STATES[c.beforeState])), widthSpan(cell(c.before)),
      key(t("new")), newText(c.after || t(TEXT_STATES[c.afterState])), widthSpan(cell(c.after))));
}

function areaForm(side, own, was) {
  const img = side.png ? el("img", { src: `data:image/png;base64,${side.png}`, alt: t("{name}: area form", { name: side.name }),
    title: t("Area form: the text pixels; magenta lies outside the text areas") }) : null;
  img?.addEventListener("load", () => {
    img.width = img.naturalWidth * 2;
  });
  const caption = [side.name, appendedLabel(side.tiles, own)].filter(Boolean).join(" · ");
  return el("span", { class: `pic${was ? " was" : ""}` }, img, el("span", { class: "cap", textContent: caption }));
}

function pictureItem(p) {
  const top = el("div", { class: "top" }, el("span", { class: "id", textContent: p.id }),
    p.category ? chip("plain", p.category) : null, p.screen ? chip("plain", p.screen) : null);
  const before = p.before ? areaForm(p.before, p.tiles, true)
    : el("span", { class: "old muted", textContent: t("original artwork"),
      title: t("No project edit: the ROM's own artwork, which the editor shows with your ROM") });
  return el("article", { class: "submit-item" }, top,
    p.source_text ? el("p", { class: "en", textContent: p.source_text }) : null,
    el("div", { class: "diff" },
      key(t("old")), before, widthSpan(areasLabel(p.before?.areas)),
      key(t("new")), areaForm(p.after, p.tiles, false), widthSpan(areasLabel(p.after.areas))));
}

// The art of a glyph record, drawn small
const GLYPH_ZOOM = 6;

function artPicture(art, top, sprite, name) {
  const canvas = el("canvas", { class: "glyph-art", role: "img", "aria-label": name });
  drawArt(canvas, { ...art, top, cell: art.rows.length, widest: art.width }, sprite, GLYPH_ZOOM);
  return canvas;
}

function glyphItem(rec) {
  const sprite = sheetsOf(state.index).some((s) => s.style === rec.style && s.kind === "sprite");
  const top = state.index.font_sheets?.styles?.[rec.style]?.top ?? 0;
  const name = rec.char ?? t("slot {slot}", { slot: rec.slot });
  const px = (art) => widthSpan({ label: art.width ? `${art.width} px` : "—", state: "plain", title: "" });
  const copied = rec.base.source?.replace(/^copy:/, "");
  return el("article", { class: "submit-item" },
    el("div", { class: "top" }, el("span", { class: "id", textContent: name }), chip("plain", rec.style)),
    el("div", { class: "diff" },
      key(t("old")), copied ? oldText("", t("a copy of the ROM glyph {code}", { code: copied }))
        : artPicture(rec.base, top, sprite, name), px(rec.base),
      key(t("new")), artPicture(rec, top, sprite, name), px(rec)));
}

function keyboardItem(row) {
  return el("article", { class: "submit-item" },
    el("div", { class: "top" }, el("span", { class: "id", textContent: row.id })),
    el("div", { class: "diff" },
      key(t("old")), oldText(row.before, t("empty")), el("span", {}),
      key(t("new")), newText(row.after), el("span", {})));
}

const NOTE_OPERATIONS = { add: N_("Add note"), edit: N_("Edit note"), remove: N_("Remove note") };
const NOTE_FIELDS = {
  id: N_("ID"), subject: N_("Subject"), scope: N_("Note scope"), binding: N_("Binding"), kind: N_("Kind"),
  language: N_("Language"), guidance: N_("Guidance"), facts: N_("Facts"), reason: N_("Reason"), sources: N_("Sources"),
  name: N_("Name"), address: N_("Address"), locator: N_("Locator"), retrieved: N_("Retrieved"), quote: N_("Quotation"),
  quoted_term: N_("Quoted term"), relation: N_("Relation"), verification: N_("Verification"), evidence: N_("Evidence"),
  method: N_("Method"), locations: N_("Locations"), measurements: N_("Measurements"), captures: N_("Captures"),
  value: N_("Value"), label: N_("Label"), unit: N_("Unit"), sha256: N_("SHA-256"), status: N_("Status"),
  related_notes: N_("Related notes"), related_subjects: N_("Related subjects"), author: N_("Author"), created: N_("Created"),
  updated: N_("Updated"), reviewed_by: N_("Reviewed by"), reviewed_at: N_("Reviewed at"),
  source_sha256: N_("English SHA-256"), value_sha256: N_("Value SHA-256"), field: N_("Field"),
};

/** Review text stays text, including quotations and locators. */
export function noteItem(change, index, entries, languages = []) {
  const scope = change.scope.kind === "shared"
    ? t("Shared game fact · applies to all language projects: {languages}", { languages: languages.join(", ") || index.language })
    : t("Language: {language}", { language: change.scope.language });
  const diff = el("div", { class: "diff note-diff" });
  const value = (text) => text === null ? t("not recorded") : String(text);
  for (const row of noteRows(change)) {
    diff.append(el("span", { class: "f", textContent: row.path.map((part) => t(NOTE_FIELDS[part] ?? String(part))).join(" · ") }),
      key(t("old")), oldText(value(row.before), t("empty")), el("span", {}),
      key(t("new")), newText(value(row.after)), el("span", {}));
  }
  const problems = noteIssues(change, index, entries);
  const attachments = (change.attachments ?? []).map((capture) => {
    const caption = `${t(CAPTURE_ROLES[capture.role] ?? "Capture")} · ${capture.build} · ${capture.context}`;
    const image = ["image/png", "image/jpeg"].includes(capture.type) && /^[A-Za-z0-9+/]+={0,2}$/.test(capture.data)
      ? el("img", { src: `data:${capture.type};base64,${capture.data}`, alt: caption }) : null;
    return el("figure", {}, image, el("figcaption", { textContent: caption }),
      el("div", { textContent: `${t("SHA-256")}: ${capture.sha256}` }));
  });
  return el("article", { class: "submit-item" },
    el("div", { class: "top" }, el("span", { class: "id", textContent: change.id }),
      chip("plain", t(NOTE_OPERATIONS[change.operation]))),
    el("p", { textContent: `${t("Subject")}: ${change.subject.kind} · ${change.subject.id}` }),
    el("p", { textContent: scope }), diff,
    problems.length ? el("ul", { class: "warn-line" }, ...problems.map((text) => el("li", { textContent: t(text) }))) : null,
    change.message ? el("p", { class: "note-review-message", textContent: `${t("Message to reviewers")}: ${change.message}` }) : null,
    attachments.length ? el("section", {}, el("h2", { class: "submit-subhead", textContent: t("Evidence attachments") }),
      el("div", { class: "note-captures" }, ...attachments)) : null);
}

function termItem(rec) {
  const stored = state.termEdits.get(rec.id);
  const m = measureOf(null);
  const cell = (r, t) => (r.text ? widthCell(t, m && t ? lineWidths(t, m) : null, null)
    : { label: "", state: "plain", title: "" });
  const diff = el("div", { class: "diff" });
  for (const r of termRows(rec, state.index.term_columns ?? TERM_COLUMNS)) {
    const form = r.field.match(/^form (.+)$/);
    diff.append(el("span", { class: "f", textContent: form ? t("form {key}", { key: form[1] }) : t(FIELDS[r.field] ?? r.field) }),
      key(t("old")), oldText(r.before, t("empty")), widthSpan(cell(r, r.before)),
      key(t("new")), newText(r.after), widthSpan(cell(r, r.after)));
  }
  const n = rec.include?.length ?? 0;
  const include = n
    ? chip("plain", plural(n, N_("{n} form-only entry renamed"), N_("{n} form-only entries renamed")), rec.include.join(", ")) : null;
  return el("article", { class: "submit-item" },
    el("div", { class: "top" }, el("span", { class: "id", textContent: rec.id }),
      stored?.row?.category ? chip("plain", stored.row.category) : null, include),
    rec.source_text ? el("p", { class: "en", textContent: rec.source_text }) : null, diff);
}

// the term fields as the editor's Terms view names them
const FIELDS = { translation: N_("Translation"), label: N_("Label"), gender: N_("Gender"), declension: N_("Declension"),
  sources: N_("Sources"), notes: N_("Notes") };

export function links(game, code) {
  const name = code ? languageName(code) : t("Language");
  setToolContext(game.id, code);
  plainText($("eyebrow"), code ? `${game.name} · ${name}` : game.name);
}

async function load() {
  const registry = await loadRegistry(new URL("data/registry.json", document.baseURI).href);
  const direct = siteRouteOf(location.hash);
  let game = null;
  let route = null;
  let manifest = null;
  if (direct) {
    const data = new URL("data/", document.baseURI).href;
    manifest = await getJson(new URL("languages.json", data).href);
    game = siteGame(manifest, data);
    route = direct;
  } else {
    ({ game, route } = chosenGame(registry, location.hash, state.storage));
  }
  if (!game) throw uiError("No game is chosen: open the editor, choose a game and a language, then Submit.");
  if (game.id === SITE) game.name = t("Site interface");
  const code = route.language ?? recall(`lang:${game.id}`);
  state.game = game;
  links(game, code);
  await loadGameTexts([game], uiLanguage());
  if (!code) throw uiError("{game}: no language is chosen. Open the editor, choose a language, then Submit.", { game: game.name });
  history.replaceState(null, "", gameHash(game.id, code));
  manifest = manifest ?? await getJson(new URL("languages.json", game.data).href);
  const lang = projectProfiles(manifest).find((p) => p.id === game.id)?.languages.find((l) => l.code === code);
  if (!lang) throw uiError("{game}: no {language} catalog is available.", { game: game.name, language: languageName(code) });
  const index = await loadIndex(new URL(lang.index, game.data).href);
  verifyProjectIndex(index, game.id, code);
  Object.assign(state, { code, index, codec: new Codec(index.codec, index.charmap, index.language_rules) });
  setReasons(index.check_rules?.reasons);
  setMessages(messagesOf(index));
  state.edits = new Edits(state.storage, code, index.profile);
  state.ruleEdits = new RuleEdits(state.storage, code, index.profile);
  state.uploads = new PictureEdits(state.storage, code, index.profile);
  state.termEdits = new TermEdits(state.storage, code, index.profile, index.term_columns);
  state.glyphEdits = new GlyphEdits(state.storage, code, index.profile);
  state.glyphs = isDraft(index) ? null : glyphsOf(index, state.glyphEdits.all());
  state.keyboardEdits = new KeyboardEdits(state.storage, code, index.profile);
  state.noteEdits = new NoteEdits(state.storage, code, index.profile);
  const { order, missing } = draftOrder(index.list, [...state.edits.map.keys()]);
  const gfx = (index.graphics?.inventory ?? []).map((r) => r.id);
  state.doc = exportDoc(code, index.profile, state.edits.changes(order), state.uploads.changes(gfx),
    state.termEdits.changes(), state.glyphEdits.changes(), state.keyboardEdits.changes(), state.ruleEdits.changes(),
    state.noteEdits.changes());
  state.missing = missing;
  state.digests = new Map(await Promise.all(state.doc.pictures.map(async (p) => {
    try {
      return [p.id, await digest(fromB64(p.png), "SHA-256")];
    } catch {
      return [p.id, undefined]; // the picture stays in the JSON file
    }
  })));
  state.rows = new Map(index.list.map((r) => [r.id, r]));
  for (const rule of index.engine_rules?.rules ?? []) for (const source of rule.sources ?? []) {
    const row = state.rows.get(source.entry);
    if (row && source.state) state.rows.set(source.entry, { ...row, state: source.state });
  }
  const chunks = new Chunks(new URL(dirOf(lang.index), game.data).href, index);
  const noteEntries = (state.doc.note_changes ?? []).filter((change) => change.subject.kind === "entry").map((change) => change.subject.id);
  state.entries = new Map(await Promise.all([...new Set([...order, ...noteEntries])].map(async (id) =>
    [id, state.rows.has(id) ? await chunks.entry(state.rows.get(id)).catch(() => null) : null])));
  for (const [id, entry] of state.entries) if (entry) state.rows.set(id, { ...state.rows.get(id), ...entry });
}

function renderIncluded(counts) {
  const v = versionLabel(state.index.toolkit);
  const catalog = [state.index.version ? t("translation {version}", { version: state.index.version }) : "",
    v.text ? `gen3tl ${v.text}` : ""].filter(Boolean).join(" · ");
  const repo = submissionParts(state.doc, state.game.repository).map((part) => part.repository).filter(submitAvailable).join(", ");
  const rows = [[t("Game"), state.game.id], [t("Language"), state.code], [t("Catalog"), catalog || t("no version recorded")],
    [t("Changes"), countsLabel(counts)],
    [t("Issue"), repo ? t("{repository}", { repository: repo })
      : t("no repository named")]];
  $("included").replaceChildren(...rows.flatMap(([k, value]) =>
    [el("dt", { textContent: k }), el("dd", { textContent: value })]));
}

function render() {
  const counts = exportCounts(state.doc);
  const heading = headingOf(counts);
  plainText($("heading"), heading);
  document.title = `${heading} · ${state.game.name}`;
  const texts = state.doc.entries.map((e) => textItem(textChange(e.id, state.edits.get(e.id), state.rows.get(e.id))));
  const pictures = state.doc.pictures.map((p) => pictureItem(pictureChange(p.id, state.uploads.get(p.id),
    state.index.graphics)));
  const terms = state.doc.terms.map(termItem);
  const glyphs = state.doc.glyphs.map(glyphItem);
  const keyboard = keyboardRows(state.doc.keyboard).map(keyboardItem);
  const rules = ruleChanges({ ...state.index, list: [...state.rows.values()] }, state.edits, state.doc.engine_rules)
    .map((change) => ruleItem(change, state.game.id, state.code));
  const notes = (state.doc.note_changes ?? []).map((change) => noteItem(change, state.index, state.entries, state.game.languages));
  $("text-list").replaceChildren(...texts);
  $("picture-list").replaceChildren(...pictures);
  $("term-list").replaceChildren(...terms);
  $("glyph-list").replaceChildren(...glyphs);
  $("keyboard-list").replaceChildren(...keyboard);
  $("rule-list").replaceChildren(...rules);
  $("note-list").replaceChildren(...notes);
  const operations = noteCounts(state.doc.note_changes);
  $("note-counts").textContent = t("{add} additions, {edit} edits, {remove} removals", operations);
  $("texts").hidden = !texts.length && !state.missing.length;
  $("pictures").hidden = !pictures.length;
  $("terms").hidden = !terms.length;
  $("glyphs").hidden = !glyphs.length;
  $("keyboard").hidden = !keyboard.length;
  $("rules").hidden = !rules.length;
  $("note-changes").hidden = !notes.length;
  const groups = [texts.length, pictures.length, terms.length, glyphs.length, keyboard.length, rules.length, notes.length]
    .filter(Boolean).length;
  for (const h of document.querySelectorAll(".submit-head")) h.hidden = groups < 2;
  Object.assign($("texts-left"), {
    hidden: !state.missing.length,
    textContent: plural(state.missing.length, N_("{n} draft of ids the catalog no longer holds stays out of the export: {ids}"),
      N_("{n} drafts of ids the catalog no longer holds stay out of the export: {ids}")).replace("{ids}", state.missing.join(", ")),
  });
  const parts = submissionParts(state.doc, state.game.repository);
  $("split-issues").hidden = parts.length < 2;
  $("toolkit-issue").hidden = parts.length < 2;
  $("toolkit-issue").disabled = parts.length < 2 || !submitAvailable(parts[1].repository);
  const lang = languageName(state.code);
  Object.assign($("empty"), { hidden: counts.total > 0,
    textContent: t("No drafts in this browser for {game} in {language}. Edits made in the editor appear here.",
      { game: state.game.name, language: lang }) });
  Object.assign($("width-note"), {
    hidden: !counts.texts && !counts.terms,
    textContent: state.glyphs
      ? t("Widths are measured here without a ROM: the glyphs of the language pack are exact, while Latin letters and the signs shared with them take a stand-in width, so widths are approximate. The editor measures exactly with your ROM loaded.")
      : t("Draft catalog: game font and window limits are unavailable, so widths are not measured."),
  });
  const stale = (state.doc.note_changes ?? []).some((change) => noteIssues(change, state.index, state.entries).length);
  const blocked = issueBlocked(parts[0].repository, exportCounts(parts[0].doc)) || submissionProblem(parts[0].doc)
    || (stale ? t("Resolve the note draft problems in the editor before submitting.") : "");
  $("issue").disabled = Boolean(blocked) || !counts.total;
  Object.assign($("issue-note"), { hidden: !blocked || !counts.total, textContent: blocked });
  $("download").disabled = !counts.total;
  renderIncluded(counts);
  $("content").hidden = false;
}

function status(text, error = false) {
  Object.assign($("status"), { hidden: !text, textContent: text, className: `note${error ? " err" : ""}` });
}

function saveJson(doc) {
  const name = exportName(state.code);
  download(JSON.stringify(doc, null, 1), name, "application/json");
  return name;
}

function savePicture(id) {
  const name = `${id}.png`;
  download(fromB64(state.doc.pictures.find((p) => p.id === id).png), name, "image/png");
  return name;
}

function openIssue(part = 0) {
  const target = submissionParts(currentDoc(), state.game.repository)[part];
  if (!target) return;
  const res = submitChanges(target.doc, target.repository, {
    base,
    sha256: (id) => state.digests.get(id),
    download: saveJson,
    picture: savePicture,
    open: (url) => window.open(url, "_blank", "noopener"),
  });
  status(res.ok ? res.message : res.reason, !res.ok);
}

function wire() {
  $("download").addEventListener("click", () => {
    const name = saveJson(fileDoc(currentDoc(), base));
    status(t("Downloaded {name}: {counts}.", { name, counts: countsLabel(exportCounts(state.doc)) }));
  });
  $("issue").addEventListener("click", () => openIssue());
  $("toolkit-issue").addEventListener("click", () => openIssue(1));
  $("notes").addEventListener("input", () => remember(notesKey(), $("notes").value));
}

/** Everything drawn from held drafts, in the shown language. */
function relabel() {
  if (state.loadError) showLoadError();
  if (!state.game) return;
  if (state.game.id === SITE) state.game.name = t("Site interface");
  links(state.game, state.code);
  if (state.doc) render();
}

function showLoadError() {
  if (!state.game) showText($("eyebrow"), "Translation editor");
  Object.assign($("load-error"), { hidden: false, textContent: errorText(state.loadError) });
}

async function main() {
  state.storage = browserStorage();
  onSiteLanguage(relabel);
  // the game's rule texts arrive with the interface
  prepareSiteLanguage((code) => loadGameTexts(state.game ? [state.game] : [], code));
  await startPage();
  try {
    await load();
    $("notes").value = recall(notesKey()) ?? "";
    wire();
    render();
  } catch (e) {
    console.error(e);
    state.loadError = e;
    showLoadError();
  }
}

if (typeof document !== "undefined" && document.getElementById("submit-page")) main();
