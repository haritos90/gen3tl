// Edit glossary terms and preview their renames.
// Form-only matches require explicit inclusion in renames.

import { checkText } from "./checktext.js";
import { el } from "./dom.js";
import { hasIndependentNotes, limitResearchList, renderNoteDrafts, renderNotes, renderResearchReferences } from "./guidance.js";
import { sameField } from "./store.js";
import { flat } from "./text.js";
import { N_, formatNumber, t } from "./ui.js";

export const CASES = ["nomn", "gent", "datv", "accs", "ablt", "loct"];
export const NUMBERS = ["sing", "plur"];
export const CASE_NAMES = {
  nomn: N_("nominative"), gent: N_("genitive"), datv: N_("dative"), accs: N_("accusative"), ablt: N_("instrumental"),
  loct: N_("prepositional"),
};
export const GENDERS = ["", "male", "female", "neutral", "plural"];
// Display labels must preserve stored glossary values.
const GENDER_NAMES = { male: N_("male"), female: N_("female"), neutral: N_("neutral"), plural: N_("plural") };
const NUMBER_NAMES = { sing: N_("singular"), plur: N_("plural") };
export const RENAMING = ["translation", "label", "gender", "forms"];
const ROW_FIELDS = ["id", "category", "source_text", "translation", "label", "gender", "declension", "forms",
  "sources", "variants", "confidence", "address", "notes", "status", "note_ids"];
const ROW = 58;
const SHOWN = 100;

/** Normalize term searches with the selected pack's folds. */
export function norm(s, fold = {}) {
  let text = (s ?? "").toLowerCase();
  for (const [before, after] of Object.entries(fold)) text = text.replaceAll(before, after);
  return text;
}

/** Extract term fields without entry usage lists. */
export const termFields = (t) => Object.fromEntries(ROW_FIELDS.filter((k) => k in t).map((k) => [k, t[k]]));

/** Fill missing nominative and label forms. */
export function allForms(t) {
  const f = { ...(t.forms ?? {}) };
  if (!("nomn.sing" in f)) f["nomn.sing"] = t.translation ?? "";
  if (t.label && !("label" in f)) f.label = t.label;
  return f;
}

// The browser's glossary check, ported from termbase.TermMatcher:
// the same English index, plural forms and form matching.

const CODE = /\{[^}]*\}|\\[nlp]/g;
const TABLE_ONLY = new Set(["dex-category"]);

/** termbase.strip_codes: placeholders and break codes as spaces. */
export function stripCodes(text) {
  return (text ?? "").replace(CODE, " ");
}

/** A Python repr enough for the check's quoted messages. */
export function pyRepr(s) {
  const q = s.includes("'") && !s.includes('"') ? '"' : "'";
  return q + s.replaceAll("\\", "\\\\").replaceAll(q, `\\${q}`) + q;
}

/** The literal name and its regular final-word plural (termbase.regular_english_forms). */
function englishForms(name) {
  const word = name.match(/[A-Za-z]+$/)?.[0];
  if (!word) return [name];
  let suffix = "s";
  let stem = name;
  if (/[^aeiou]y$/i.test(word)) {
    stem = name.slice(0, -1);
    suffix = "ies";
  } else if (/(?:s|x|z|ch|sh)$/i.test(word)) {
    suffix = "es";
  }
  return [name, stem + (word === word.toUpperCase() ? suffix.toUpperCase() : suffix)];
}

const matchers = new WeakMap();

/** Index literal prefixes once per glossary. */
export function glossaryMatcher(glossary) {
  let m = matchers.get(glossary);
  if (m) return m;
  const byEnglish = new Map();
  for (const [id, t] of Object.entries(glossary)) {
    const term = t.id ? t : { ...t, id };
    if (term.source_text && term.source_text.length > 2 && !TABLE_ONLY.has(term.category)) {
      for (const name of englishForms(term.source_text)) {
        if (!byEnglish.has(name)) byEnglish.set(name, []);
        byEnglish.get(name).push(term);
      }
    }
  }
  const root = new Map();
  for (const [name, terms] of byEnglish) {
    let node = root;
    for (let i = 0; i < name.length; i++) {
      if (!node.has(name[i])) node.set(name[i], new Map());
      node = node.get(name[i]);
    }
    node.terms = terms;
  }
  const letter = (char) => char >= "A" && char <= "Z" || char >= "a" && char <= "z";
  const find = (english) => {
    const text = stripCodes(english), found = [];
    for (let i = 0; i < text.length; i++) {
      if (letter(text[i - 1])) continue;
      let node = root, terms = null, end = i;
      for (let j = i; j < text.length; j++) {
        node = node.get(text[j]);
        if (!node) break;
        if (node.terms && !letter(text[j + 1])) {
          terms = node.terms;
          end = j;
        }
      }
      if (terms) {
        found.push(...terms);
        i = end;
      }
    }
    return found;
  };
  m = { find, forms: new Map() };
  matchers.set(glossary, m);
  return m;
}

/** Glossary terms found in one English line. */
export function englishTerms(english, glossary) {
  return [...new Set(glossaryMatcher(glossary).find(english).map((term) => term.id))];
}

/** Match any term form using termbase.find_forms semantics. */
export function hasTermForm(text, term, fold = {}) {
  const low = norm(stripCodes(text), fold);
  return Object.values(allForms(term)).some((form) => form && containsForm(low, norm(form, fold)));
}

const wordCharacter = /[\p{L}\p{N}]/u;

/** Literal forms share Unicode boundary checks. */
function containsForm(text, form) {
  if (!form) return false;
  for (let at = text.indexOf(form); at !== -1; at = text.indexOf(form, at + 1)) {
    const end = at + form.length;
    const paired = (text.charCodeAt(at - 1) & 0xfc00) === 0xdc00 && (text.charCodeAt(at - 2) & 0xfc00) === 0xd800;
    const before = paired ? text.slice(at - 2, at) : text[at - 1] ?? "";
    const after = end < text.length ? String.fromCodePoint(text.codePointAt(end)) : "";
    if (!wordCharacter.test(before) && !wordCharacter.test(after)) return true;
  }
  return false;
}

/** Glossary check problems of one entry. */
export function glossaryProblems(english, translation, glossary, fold = {}, found = null) {
  const matcher = glossaryMatcher(glossary);
  found ??= matcher.find(english);
  if (!found.length) return [];
  const key = JSON.stringify(fold);
  if (!matcher.forms.has(key)) matcher.forms.set(key, new Map());
  const forms = matcher.forms.get(key), low = norm(stripCodes(translation), fold);
  const present = new Set();
  for (const term of new Set(found)) {
    if (!forms.has(term)) {
      forms.set(term, [...new Set(Object.values(allForms(term)).filter(Boolean).map((form) => norm(form, fold)))]
        .filter(Boolean));
    }
    if (forms.get(term).some((form) => containsForm(low, form))) present.add(term.source_text);
  }
  return found.filter((t) => !present.has(t.source_text))
    .map((t) => ({ severity: "warning",
      message: `glossary term ${pyRepr(t.source_text)} -> ${pyRepr(t.translation)} not found (warning)` }));
}

/** Find entries naming a term or containing its forms. */
export function usesOf(t) {
  if (t._uses === undefined) t._uses = new Set([...(t.in_source ?? []), ...(t.in_translation ?? [])]).size;
  return t._uses;
}

const byText = (a, b) => (a < b ? -1 : a > b ? 1 : 0);

/** Find entries displaying the label instead of full form. */
export const shortenedOf = (t) => t.shortened ?? [];

/** Filter and sort glossary terms with local edits. */
export function filterTerms(terms, filters, edits = new Map(), fold = {}) {
  const normalize = (s) => norm(s, fold);
  const q = normalize((filters.search || "").trim());
  const out = [];
  terms.forEach((t, i) => {
    if (filters.category && t.category !== filters.category) return;
    const ed = edits.get(t.id);
    if (filters.status === "edited" && !ed) return;
    if (filters.status === "used" && !usesOf(t)) return;
    if (filters.status === "unused" && usesOf(t)) return;
    if (filters.status === "shortened" && !shortenedOf(t).length) return;
    if (q && !(normalize(t.id) === q || normalize(t.source_text).includes(q) || normalize(t.translation).includes(q) ||
      (ed && normalize(ed.row.translation).includes(q)))) return;
    out.push(i);
  });
  if ((filters.sort || "uses") === "uses") out.sort((a, b) => usesOf(terms[b]) - usesOf(terms[a]) || a - b);
  else if (filters.sort === "english") out.sort((a, b) => byText(normalize(terms[a].source_text), normalize(terms[b].source_text)) || a - b);
  return out;
}

/**
 * Update a term field and dependent forms.
 * Matching nominatives follow translations; fixed rows update every form.
 * Empty forms disappear; edited automatic forms become manual.
 */
export function editTerm(base, row, field, value) {
  if (field === "notes" && base.note_ids?.length && base.notes === undefined) return row;
  const out = { ...row, forms: { ...(row.forms ?? {}) } };
  if (field === "translation") {
    if (out.declension === "fixed") for (const k of Object.keys(out.forms)) out.forms[k] = value;
    else if (out.forms["nomn.sing"] === row.translation) out.forms["nomn.sing"] = value;
    out.translation = value;
  } else if (field.startsWith("forms.")) {
    const key = field.slice(6);
    if (value === "") delete out.forms[key];
    else out.forms[key] = value;
  } else out[field] = value;
  const formsChanged = !sameField("forms", base.forms, out.forms);
  if (base.declension === "auto") out.declension = formsChanged ? "manual" : "auto";
  return out;
}

/** Find forms retaining the translation's previous value. */
export function staleForms(base, row, fold = {}) {
  const normalize = (s) => norm(s, fold);
  if (normalize(base.translation) === normalize(row.translation)) return [];
  return Object.keys(base.forms ?? {}).filter((k) => row.forms?.[k] && row.forms[k] === base.forms[k]);
}

/** Check whether changed fields rename the term. */
export const renames = (base, row) => RENAMING.some((f) => !sameField(f, base[f], row[f]));

/** Find usable term forms containing letters or digits. */
export function surfaces(t, fold = {}) {
  const normalize = (s) => norm(s, fold);
  return [...new Set(Object.values(allForms(t)).map(normalize).filter((s) => /[\p{L}\p{N}]/u.test(s)))];
}

/** Find entries and filled slots potentially affected by renaming. */
export function candidates(t, rows, textOf, slots = {}, fold = {}) {
  const normalize = (s) => norm(s, fold);
  const forms = surfaces(t, fold);
  const out = new Set(t.in_translation ?? []);
  if (forms.length) {
    for (const r of rows) {
      const x = normalize(textOf(r));
      if (x && forms.some((f) => x.includes(f))) out.add(r.id);
    }
  }
  for (const id of [...out]) for (const [host] of slots[id] ?? []) out.add(host);
  return out;
}



/**
 * List term uses and form-only matches in display order.
 * Shortened matches show the label instead of full form.
 */
export function usage(t, order, translated = () => true, { terms = [], textOf = () => "", fold = {} } = {}) {
  const eng = new Set(t.in_source ?? []);
  const tr = new Set(t.in_translation ?? []);
  const fo = new Set(t.form_only ?? []);
  const sh = new Set(shortenedOf(t));
  const peers = terms.filter((x) => x.id !== t.id && x.source_text === t.source_text &&
    x.source_text?.length > 2 && !TABLE_ONLY.has(x.category));
  const ids = [...new Set([...eng, ...tr, ...fo])].sort((a, b) => (order(a) ?? 1e9) - (order(b) ?? 1e9));
  return ids.map((id) => {
    const needsForm = eng.has(id) && !tr.has(id) && !fo.has(id) && translated(id);
    const otherTerms = needsForm ? peers.filter((x) => hasTermForm(textOf(id), x, fold)) : [];
    return { id, in_source: eng.has(id), translation: tr.has(id), formOnly: fo.has(id),
      shortened: sh.has(id), otherTerms, missing: needsForm && !otherTerms.length };
  });
}

/** Include enclosing terms to protect their names. */
export function senseRows(t, terms, fold = {}) {
  const own = surfaces(t, fold).map((f) => {
    const escaped = f.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return new RegExp(`(?<![\\p{L}\\p{N}])${escaped}(?![\\p{L}\\p{N}])`, "u");
  });
  const base = t.id.split("~")[0];
  return terms.filter((x) => x.id !== t.id &&
    (x.id.split("~")[0] === base || surfaces(x, fold).some((f) => own.some((p) => p.test(f)))))
    .map(termFields);
}

/** Key previews by edited term and included entries. */
export const previewKey = (row, include) => JSON.stringify([row, [...include].sort()]);

/** Group rename report lines by approval and problems. */
export function affected(report, statusOf) {
  const lines = report.entries.filter((e) => e.changed);
  const flagged = (e) => e.added.length || e.agreement.length || e.ambiguous.length;
  return {
    lines: lines.length,
    approved: lines.filter((e) => statusOf(e.id) === "approved").length,
    translated: lines.filter((e) => statusOf(e.id) === "fuzzy").length,
    limit: report.entries.filter((e) => !e.changed).length,
    flagged: report.entries.filter(flagged).length,
  };
}

/** Filter rename results by approval or flagged problems. */
export function reportEntries(report, filter, statusOf) {
  if (filter === "approved") return report.entries.filter((e) => e.changed && statusOf(e.id) === "approved");
  if (filter === "flagged") return report.entries.filter((e) => e.added.length || e.agreement.length || e.ambiguous.length);
  return report.entries;
}

/** Summarize grammar changes in a rename report. */
export function grammarText(g) {
  const props = (x) => [x.number === "plur" ? t("plural") : x.gender || t("gender unknown"),
    x.declension && t("declension {declension}", { declension: x.declension }), x.animacy].filter(Boolean).join(", ");
  if (!g.changes.length) return t("unchanged ({grammar})", { grammar: props(g.old) });
  return `${g.changes.map((c) => `${c.property} ${c.old} → ${c.new}`).join("; ")} (${props(g.old)} → ${props(g.new)})`;
}

/** Render glossary edits using shared entry and check access. */
export class TermsView {
  get fold() {
    return this.host.index?.()?.language_rules?.fold;
  }

  constructor(host, $) {
    this.host = host;
    this.$ = $;
    this.filtered = [];
    this.cur = null;
    this.report = null;
    this.reportFilter = "";
    this.showAll = { usage: false, apart: false, report: false };
    this.note = null; // Keep the count translatable before a list loads.
  }

  /** Remember count parameters for language changes. */
  countNote(key, vars = null) {
    this.note = key ? { key, vars } : null;
    this.$("t-count").textContent = key ? t(key, vars) : "";
  }

  get terms() {
    return this.host.data()?.terms ?? [];
  }

  term(id = this.cur) {
    if (!this.byId) this.byId = new Map(this.terms.map((term, i) => [term.id, i]));
    return this.terms[this.byId.get(id)] ?? null;
  }

  /** Read local edits or the original term. */
  current(term = this.term()) {
    return this.host.edits()?.get(term.id)?.row ?? termFields(term);
  }

  noteIds(term) {
    return term.note_ids ?? this.host.index?.()?.glossary?.[term.id]?.note_ids ?? [];
  }

  structuredNotes(term) {
    return hasIndependentNotes(this.noteIds(term), this.host.index?.()?.notes);
  }

  /** Reset cached terms after a language change. */
  reset(want = null) {
    this.host.reference?.(null);
    this.generation = (this.generation ?? 0) + 1;
    this.byId = null;
    this.cur = null;
    this.report = null;
    this.want = want;
    this.loaded = false;
    this.countNote(null);
    this.$("t-list-space").replaceChildren();
  }

  /** Load terms once and open the requested term. */
  async show(id = null) {
    const generation = this.generation;
    if (!this.loaded) {
      this.countNote("Loading the glossary…");
      try {
        await this.host.load();
        if (generation !== this.generation) return;
      } catch (e) {
        if (generation !== this.generation) return;
        this.countNote("No glossary terms: {error}", { error: e.message || e });
        return;
      }
      this.loaded = true;
      this.byId = null;
      this.fillCategories();
      this.applyFilters();
    }
    if (!this.host.data()) {
      this.countNote("The editor data has no glossary terms (gen3tl web data --split with a glossary).");
      return;
    }
    const want = id ?? this.want ?? this.cur ?? this.terms[this.filtered[0]]?.id;
    this.want = null;
    if (want && this.term(want)) this.open(want);
  }

  fillCategories() {
    const sel = this.$("t-category");
    const chosen = sel.value;
    sel.replaceChildren(el("option", { value: "", textContent: t("All categories") }));
    const n = new Map();
    for (const term of this.terms) n.set(term.category, (n.get(term.category) ?? 0) + 1);
    for (const [c, k] of [...n].sort(([a], [b]) => byText(a, b))) {
      sel.append(el("option", { value: c, textContent: `${c} (${k})` }));
    }
    if (chosen && n.has(chosen)) sel.value = chosen;
  }

  filters() {
    const $ = this.$;
    return { search: $("t-search").value, category: $("t-category").value, status: $("t-status").value,
      sort: $("t-sort").value };
  }

  applyFilters() {
    this.filtered = filterTerms(this.terms, this.filters(), this.host.edits()?.map, this.fold);
    this.note = null;
    this.$("t-count").textContent = t("{shown} of {total} terms",
      { shown: formatNumber(this.filtered.length), total: formatNumber(this.terms.length) });
    this.$("t-list-space").style.height = `${this.filtered.length * ROW}px`;
    this.renderList();
  }

  renderList() {
    const list = this.$("t-list");
    const top = list.scrollTop;
    const first = Math.max(0, Math.floor(top / ROW) - 4);
    const last = Math.min(this.filtered.length, Math.ceil((top + (list.clientHeight || 600)) / ROW) + 4);
    const kids = [];
    for (let p = first; p < last; p++) kids.push(this.rowElement(this.terms[this.filtered[p]], p));
    this.$("t-list-space").replaceChildren(...kids);
  }

  rowElement(term, pos) {
    const ed = this.host.edits()?.get(term.id);
    const n = usesOf(term);
    const cut = shortenedOf(term).length;
    return el("div", { class: `row${term.id === this.cur ? " sel" : ""}`, role: "option", style: `top:${pos * ROW}px`,
      on: { click: () => this.open(term.id) } },
    el("div", { class: "l1" },
      el("span", { class: "eid", textContent: term.id }),
      el("span", { class: "muted", textContent: term.category }),
      el("span", { class: `mark ${n ? "uses" : "warn"}`,
        textContent: !n ? t("unused") : n > 1 ? t("{n} entries", { n }) : t("{n} entry", { n }) }),
      cut ? el("span", { class: "mark warn", textContent: t("shortened ×{n}", { n: cut }),
        title: cut > 1 ? t("{n} entries show the label «{label}»", { n: cut, label: term.label })
          : t("{n} entry shows the label «{label}»", { n: cut, label: term.label }) }) : null,
      ed ? el("span", { class: "mark edited", textContent: t("edited") }) : null),
    el("div", { class: "l2", textContent: term.source_text || t("(no English)") }),
    el("div", { class: "l3", textContent: (ed ? ed.row.translation : term.translation) || t("(no translation)") }));
  }

  scrollToCurrent() {
    const p = this.filtered.findIndex((i) => this.terms[i].id === this.cur);
    if (p < 0) return;
    const list = this.$("t-list");
    if (p * ROW < list.scrollTop) list.scrollTop = p * ROW;
    else if ((p + 1) * ROW > list.scrollTop + list.clientHeight) list.scrollTop = (p + 1) * ROW - list.clientHeight;
  }

  step(d) {
    if (!this.filtered.length) return;
    const p = this.filtered.findIndex((i) => this.terms[i].id === this.cur);
    const q = p < 0 ? 0 : Math.min(this.filtered.length - 1, Math.max(0, p + d));
    this.open(this.terms[this.filtered[q]].id);
  }

  open(id) {
    const term = this.term(id);
    if (!term) return;
    if (this.cur !== id) {
      this.report = null;
      this.showAll = { usage: false, apart: false, report: false };
    }
    this.cur = id;
    this.host.setHash(id);
    this.renderList();
    this.scrollToCurrent();
    const $ = this.$;
    $("t-id").textContent = term.id;
    this.host.reference?.(term.id);
    Object.assign($("t-category-badge"), { textContent: term.category, className: "badge" });
    this.renderDetail(term);
  }

  /** Render the open term's panes from held state. */
  renderDetail(term) {
    this.$("t-uses").textContent = t("{n} entries use it", { n: usesOf(term) });
    this.renderForm(term);
    this.renderFacts(term);
    this.renderFamilies(term);
    this.renderUsage(term);
    this.renderState(term);
    requestAnimationFrame(() => { if (this.cur === term.id) limitResearchList(this.$("term"), window.innerHeight); });
  }

  /** Link the term's families to their comparison view. */
  async renderFamilies(term) {
    const box = this.$("t-families");
    if (!this.host.familiesOf) return;
    box.replaceChildren(el("div", { class: "muted", textContent: t("Loading…") }));
    let list;
    try {
      list = await this.host.familiesOf(term.id);
    } catch (e) {
      list = null;
    }
    if (this.cur !== term.id) return;
    if (!list?.length) {
      box.replaceChildren(el("div", { class: "muted", textContent: list ? t("In no family") : t("Families unavailable") }));
      return;
    }
    box.replaceChildren(...list.map((f) => el("div", { class: "ctx-item" },
      el("a", { class: "fam-link", href: "#", textContent: f.title, on: { click: (e) => {
        e.preventDefault();
        this.host.openFamily(f.id);
      } } }),
      el("span", { class: "muted",
        textContent: ` · ${t("{category} · {n} terms", { category: f.category, n: f.members.length })}` }))));
  }

  /** Fill form inputs using the edited term. */
  renderForm(term) {
    const $ = this.$;
    const row = this.current(term);
    $("t-english").textContent = term.source_text || t("(no English)");
    $("t-translation").value = row.translation ?? "";
    $("t-label").value = row.label ?? "";
    const g = $("t-gender");
    g.replaceChildren(...[...new Set([...GENDERS, row.gender ?? "", term.gender ?? ""])].map((v) =>
      el("option", { value: v, textContent: v ? (GENDER_NAMES[v] ? t(GENDER_NAMES[v]) : v) : t("(none)") })));
    g.value = row.gender ?? "";
    $("t-sources").value = row.sources ?? "";
    const migrated = this.structuredNotes(term);
    $("t-notes").value = row.notes ?? "";
    $("t-notes").readOnly = migrated;
    $("t-notes-label").hidden = migrated;
    $("t-notes-field").hidden = migrated;
    const controls = $("t-note-controls");
    controls.hidden = !migrated;
    controls.replaceChildren();
    if (migrated && this.host.noteEdits?.()) controls.append(renderNoteDrafts({ kind: "term", id: term.id }, null,
      this.host.index(), this.host.noteEdits?.(), this.host.languages?.()));
    const table = $("t-forms");
    table.replaceChildren(el("tr", {}, el("th", { textContent: "" }),
      ...NUMBERS.map((n) => el("th", { textContent: t(NUMBER_NAMES[n]) }))));
    for (const c of CASES) {
      table.append(el("tr", {}, el("th", { textContent: t(CASE_NAMES[c]) }),
        ...NUMBERS.map((n) => el("td", {}, el("input", {
          type: "text", value: row.forms?.[`${c}.${n}`] ?? "", spellcheck: false,
          "data-key": `${c}.${n}`, "aria-label": `${t(CASE_NAMES[c])} ${t(NUMBER_NAMES[n])}`,
          on: { input: (e) => this.edit(`forms.${c}.${n}`, e.target.value) },
        })))));
    }
  }

  /** Show edit status, stale forms and preview freshness. */
  renderState(term = this.term()) {
    const $ = this.$;
    const ed = this.host.edits()?.get(term.id);
    const row = this.current(term);
    Object.assign($("t-status-badge"), {
      textContent: ed ? t("{status}, edited here", { status: term.status }) : term.status,
      className: `badge ${ed ? "edited" : term.status}`,
    });
    $("t-declension").textContent = row.declension === term.declension
      ? row.declension : t("{declension} (was {was}: hand-made forms)", { declension: row.declension, was: term.declension });
    for (const inp of $("t-forms").querySelectorAll?.("input") ?? []) {
      inp.classList.toggle("changed", (row.forms?.[inp.dataset.key] ?? "") !== (term.forms?.[inp.dataset.key] ?? ""));
    }
    const stale = staleForms(term, row, this.fold);
    const include = this.host.edits()?.include(term.id) ?? [];
    const old = { n: stale.length, forms: stale.join(", ") };
    $("t-stale").textContent = !stale.length ? ""
      : stale.length > 1 ? t("The translation changed, but {n} forms keep the old text ({forms}): a rename replaces each old form with the form given here.", old)
        : t("The translation changed, but {n} form keeps the old text ({forms}): a rename replaces each old form with the form given here.", old);
    const rn = renames(termFields(term), row);
    $("t-preview").disabled = !rn;
    for (const box of $("t-usage").querySelectorAll?.("input[type=checkbox]") ?? []) box.disabled = !rn;
    const fresh = this.report && this.report.key === previewKey(row, include);
    $("t-preview-note").textContent = !rn
      ? (ed ? t("notes and sources only") : t("edit the term to preview a rename"))
      : fresh ? "" : this.report ? t("the term changed since this preview: check again") : "";
    if (!this.report) this.renderReport(null);
  }

  /** Apply one field change from the form. */
  edit(field, value) {
    const term = this.term();
    if (!term) return;
    if (field === "notes" && this.structuredNotes(term)) return;
    const row = editTerm(termFields(term), this.current(term), field, value);
    this.host.edits().set(termFields(term), row);
    if (field === "translation" || field.startsWith("forms.")) {
      for (const inp of this.$("t-forms").querySelectorAll?.("input") ?? []) {
        if (inp !== document.activeElement) inp.value = row.forms?.[inp.dataset.key] ?? "";
      }
    }
    this.host.changed();
    this.renderState(term);
    this.renderList();
  }

  revert() {
    const term = this.term();
    if (!term) return;
    this.host.edits().remove(term.id);
    this.report = null;
    this.host.changed();
    this.open(term.id);
  }

  renderFacts(term) {
    const dl = this.$("t-facts");
    dl.replaceChildren();
    const facts = [[t("Category"), term.category], [t("Status"), term.status], [t("Confidence"), term.confidence],
      [t("Address"), term.address], [t("Variants"), term.variants]];
    for (const [k, v] of facts) if (v) dl.append(el("dt", { textContent: k }), el("dd", { textContent: v }));
    const index = this.host.index();
    const ids = this.noteIds(term);
    const notes = renderNotes(ids, index?.notes, index?.language, index?.research, { includeResearch: false });
    if (notes) dl.append(el("dt", { textContent: t("Notes") }), el("dd", {}, notes));
    const research = renderResearchReferences(ids, index?.notes, index?.language, index?.research);
    if (research) dl.append(el("dt", { textContent: t("Research references") }), el("dd", {}, research));
    if (!this.structuredNotes(term) && this.host.noteEdits?.()) dl.append(el("dt", { textContent: t("Note drafts") }), el("dd", {},
      renderNoteDrafts({ kind: "term", id: term.id }, null, index, this.host.noteEdits(), this.host.languages?.())));
  }


  renderUsage(term) {
    const box = this.$("t-usage");
    const statusOf = (id) => this.host.row(id)?.status;
    const translated = (id) => ["approved", "fuzzy"].includes(statusOf(id));
    const every = usage(term, this.host.order, translated, { terms: this.terms,
      textOf: (id) => this.host.row(id)?.translation ?? "", fold: this.fold });
    const all = every.filter((u) => u.in_source || u.translation);
    const apart = every.filter((u) => !u.in_source && !u.translation);
    const missing = all.filter((u) => u.missing).length;
    const cut = shortenedOf(term).length;
    box.replaceChildren(el("div", { class: "ctx-item" },
      t("{n} entries: {english} name the term in the English, {translation} hold a form in the translation",
        { n: all.length, english: term.in_source?.length ?? 0, translation: term.in_translation?.length ?? 0 }),
      cut ? el("span", { class: "warn-line",
        textContent: `; ${t("{n} show the label «{label}» for the full form", { n: cut, label: term.label })}` }) : "",
      missing ? el("span", { class: "error",
        textContent: `; ${t("{n} translated without a form (glossary warning)", { n: missing })}` }) : ""));
    const shown = this.showAll.usage ? all : all.slice(0, SHOWN);
    for (const u of shown) box.append(this.useElement(term, u));
    if (shown.length < all.length) {
      box.append(el("button", { type: "button", textContent: t("Show all {n}", { n: all.length }), on: { click: () => {
        this.showAll.usage = true;
        this.renderUsage(term);
      } } }));
    }
    if (!apart.length) return;
    box.append(el("div", { class: "ctx-item apart" },
      el("b", { textContent: `${t("Form only, English does not name the term:")} ` }),
      t("{n} entries hold a form of the term where the English does not name it; a rename leaves them as they are unless included.",
        { n: apart.length })));
    const more = this.showAll.apart ? apart : apart.slice(0, SHOWN);
    for (const u of more) box.append(this.useElement(term, u));
    if (more.length < apart.length) {
      box.append(el("button", { type: "button", textContent: t("Show all {n}", { n: apart.length }), on: { click: () => {
        this.showAll.apart = true;
        this.renderUsage(term);
      } } }));
    }
  }

  /** Render usage entries with optional form-only rename inclusion. */
  useElement(term, u) {
    const r = this.host.row(u.id);
    const edits = this.host.edits();
    const edited = renames(termFields(term), this.current(term));
    const include = new Set(edits?.include(term.id) ?? []);
    return el("div", { class: "use" },
      el("div", { class: "l1" },
        el("span", { class: `dot ${r?.status ?? ""}`, title: r?.status ?? "" }),
        el("a", { class: "id", textContent: u.id, on: { click: () => this.host.openEntry(u.id) } }),
        u.in_source ? el("span", { class: "mark", textContent: t("English") }) : null,
        u.translation ? el("span", { class: "mark uses", textContent: t("form") }) : null,
        ...u.otherTerms.map((other) => el("span", { class: "mark uses",
          textContent: t("form of «{term}»", { term: other.translation }) })),
        u.shortened ? el("span", { class: "mark warn", textContent: t("shortened"),
          title: t("shows «{label}»", { label: term.label }) }) : null,
        u.formOnly ? el("span", { class: "mark warn", textContent: t("form only") }) : null,
        u.missing ? el("span", { class: "mark err", textContent: t("no form") }) : null,
        u.formOnly ? el("label", { class: "muted", title: edited ? t("rename its form-only occurrences too")
          : t("edit the term first") },
        el("input", { type: "checkbox", checked: include.has(u.id), disabled: !edited, on: { change: (e) => {
          const ids = new Set(edits.include(term.id));
          if (e.target.checked) ids.add(u.id);
          else ids.delete(u.id);
          edits.setInclude(term.id, [...ids]);
          this.host.changed();
          this.renderState(term);
        } } }), ` ${t("include in the rename")}`) : null),
      r?.source_text ? el("div", { class: "muted clip", textContent: flat(r.source_text), title: flat(r.source_text) }) : null,
      r ? el("div", { class: "clip", textContent: flat(this.host.textOf(r)) || t("untranslated"),
        title: flat(this.host.textOf(r)) }) : null);
  }

  /** Check every translation line affected by the rename. */
  async preview() {
    const generation = this.generation;
    const term = this.term();
    const $ = this.$;
    const check = this.host.check();
    if (!term) return;
    if (!check) {
      $("t-preview-note").textContent = t("The preview needs the check: {note}", { note: this.host.checkNote() });
      return;
    }
    const row = this.current(term);
    const include = this.host.edits()?.include(term.id) ?? [];
    const key = previewKey(row, include);
    const slots = this.host.data().slots ?? {};
    const ids = candidates(term, this.host.rows(), this.host.textOf, slots, this.fold);
    $("t-preview").disabled = true;
    $("t-preview-note").textContent = t("Loading {n} entries…", { n: ids.size });
    try {
      const entries = await Promise.all([...ids].filter((id) => this.host.row(id)).map((id) => this.host.entry(id)));
      if (generation !== this.generation) return;
      const items = entries.map((e) => ({ entry: e, text: this.host.textOf(this.host.row(e.id)) }));
      const sl = Object.fromEntries([...ids].filter((id) => slots[id]).map((id) => [id, slots[id]]));
      $("t-preview-note").textContent = t("Checking {n} entries…", { n: items.length });
      await new Promise((ok) => setTimeout(ok, 30)); // Paint the progress state before running checks.
      if (this.cur !== term.id || generation !== this.generation) return;
      // Without the English, the index names the term's uses
      const old = { ...termFields(term), in_source: term.in_source ?? [] };
      const rep = await check.rename(old, row, items, sl, { rows: senseRows(term, this.terms, this.fold), include });
      this.report = { key, rep };
      this.reportFilter = $("t-report-filter").value;
      this.renderState(term);
      this.renderReport(rep);
    } catch (e) {
      if (generation !== this.generation) return;
      console.error(e);
      $("t-preview-note").textContent = t("Preview failed: {error}", { error: e.message || e });
    } finally {
      if (generation === this.generation) $("t-preview").disabled = !renames(termFields(term), this.current(term));
    }
  }

  renderReport(rep) {
    const $ = this.$;
    const box = $("t-report");
    box.replaceChildren();
    if (rep === null) {
      if (this.report && this.report.rep === null) {
        box.append(el("div", { class: "muted", textContent: t("The edit changes no form: no line changes.") }));
      }
      return;
    }
    const statusOf = (id) => this.host.row(id)?.status;
    const a = affected(rep, statusOf);
    const s = rep.summary;
    const g = rep.renames[0];
    const lines = [t("{lines} lines change ({approved} approved, {translated} translated); each line keeps its status", a),
      s.limit_checked ? t("{n} unchanged lines checked with a wider slot", { n: s.limit_checked }) : ""];
    const checks = [t("{errors} new errors in {error_lines} lines, {warnings} new warnings in {warning_lines} lines, {agreement_lines} lines to check for agreement, {ambiguous_lines} with ambiguous forms.", s),
      s.blocked ? t("term-rename refuses this rename without --allow-errors.") : ""];
    box.append(...[
      el("div", { class: "ctx-item" }, el("b", { textContent: `${t("Rename:")} ` }), `${g.old} → ${g.new}`),
      el("div", { class: "ctx-item" }, el("b", { textContent: `${t("Grammar:")} ` }), grammarText(g.grammar)),
      el("div", { class: "ctx-item" }, el("b", { textContent: `${t("Lines:")} ` }), lines.filter(Boolean).join("; ")),
      el("div", { class: `ctx-item${s.blocked ? " error" : ""}` }, el("b", { textContent: `${t("Checks:")} ` }),
        checks.filter(Boolean).join(" ")),
      rep.exact ? null : el("div", { class: "muted", textContent: t("Widths approximate: load your ROM for the exact check.") }),
      this.formOnlyNote(),
    ].filter(Boolean));
    const list = reportEntries(rep, $("t-report-filter").value, statusOf);
    const shown = this.showAll.report ? list : list.slice(0, SHOWN);
    for (const e of shown) {
      const st = statusOf(e.id);
      box.append(el("div", { class: "change" },
        el("div", { class: "l1" },
          el("a", { class: "id", textContent: e.id, on: { click: () => this.host.openEntry(e.id) } }),
          el("span", { class: `badge ${st ?? ""}`, textContent: st ?? "" }),
          e.changed ? null : el("span", { class: "mark warn", textContent: t("slot wider") }),
          el("span", { class: "muted", textContent: e.file })),
        el("div", { class: `clip${e.changed ? " before" : ""}`, textContent: flat(e.before), title: flat(e.before) }),
        e.changed ? el("div", { class: "after clip", textContent: flat(e.after), title: flat(e.after) }) : null,
        e.added.length ? el("ul", { class: "problems" }, ...e.added.map((p) => el("li", { class: p.severity, textContent: checkText(p.message) }))) : null,
        ...e.agreement.map((x) => el("div", { class: "warn-line",
          textContent: t("check agreement with {match} ({form}): {words}", { match: x.match, form: x.form, words: x.words.join(", ") }) })),
        ...e.ambiguous.map((x) => el("div", { class: "warn-line", textContent: t("ambiguous form: {form}", { form: x }) }))));
    }
    if (!list.length) box.append(el("div", { class: "muted", textContent: t("No line in this selection.") }));
    if (shown.length < list.length) {
      box.append(el("button", { type: "button", textContent: t("Show all {n}", { n: list.length }), on: { click: () => {
        this.showAll.report = true;
        this.renderReport(rep);
      } } }));
    }
  }

  /** Report form-only entries excluded from the rename. */
  formOnlyNote(term = this.term()) {
    const fo = term?.form_only ?? [];
    if (!fo.length) return null;
    const inc = (this.host.edits()?.include(term.id) ?? []).filter((id) => fo.includes(id)).length;
    const vars = { n: fo.length - inc, included: inc };
    return el("div", { class: "ctx-item" }, el("b", { textContent: `${t("Form only:")} ` }), inc
      ? t("{n} entries with a form where the English does not name the term are left out; {included} included.", vars)
      : t("{n} entries with a form where the English does not name the term are left out.", vars));
  }

  /** Relabel the list, open term and cached report. */
  relabel() {
    if (!this.loaded) {
      if (this.note) this.countNote(this.note.key, this.note.vars);
      return;
    }
    if (!this.host.data()) {
      if (this.note) this.countNote(this.note.key, this.note.vars);
      return;
    }
    this.fillCategories();
    this.applyFilters();
    const term = this.term();
    if (!term) return;
    this.renderDetail(term);
    if (this.report?.rep) this.renderReport(this.report.rep);
  }

  wire() {
    const $ = this.$;
    $("t-search").addEventListener("input", () => this.applyFilters());
    for (const id of ["t-category", "t-status", "t-sort"]) $(id).addEventListener("change", () => this.applyFilters());
    $("t-list").addEventListener("scroll", () => requestAnimationFrame(() => this.renderList()));
    $("t-list").addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        this.step(e.key === "ArrowDown" ? 1 : -1);
      }
    });
    $("t-prev").addEventListener("click", () => this.step(-1));
    $("t-next").addEventListener("click", () => this.step(1));
    for (const f of ["translation", "label", "gender", "sources", "notes"]) {
      $(`t-${f}`).addEventListener(f === "gender" ? "change" : "input", (e) => this.edit(f, e.target.value));
    }
    $("t-revert").addEventListener("click", () => this.revert());
    $("t-preview").addEventListener("click", () => this.preview());
    $("t-report-filter").addEventListener("change", () => {
      if (this.report?.rep) this.renderReport(this.report.rep);
    });
  }
}
