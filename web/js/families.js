// Compare related terms grouped by words, types or lists.
// Marks provide review hints; edits use the Terms view.

import { tableKey } from "./data.js";
import { el } from "./dom.js";
import { basePower } from "./moveeffects.js";
import { moveListName } from "./movelabels.js";
import { norm, termFields } from "./terms.js";
import { N_, formatNumber, t } from "./ui.js";

export const KINDS = { type: N_("Move type"), list: N_("Game move list"), word: N_("English word") };
const KIND_ORDER = Object.keys(KINDS);
// These lists group moves by their defining properties.
export const MOVE_LISTS = ["punching", "biting", "sound", "pulse_aura", "dance", "powder", "ballistic"];
// Generic words do not identify useful families.
export const GENERIC = new Set(["a", "an", "and", "at", "by", "for", "in", "into", "of", "off", "on", "out",
  "the", "to", "up", "with", "z", "move"]);
export const MIN_WORD = 3; // Word families need enough members for comparison.
export const MIN_DATA = 2; // Type and list families need comparable members.
export const DAMAGE = [N_("physical"), N_("special"), N_("status")];
const STEM = 4; // Short stems would group unrelated translation words.

const byText = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
const cap = (w) => w[0].toUpperCase() + w.slice(1);

/** Extract lowercase English words from a name. */
export function englishWords(s) {
  const spaced = (s ?? "").replace(/(\p{Ll})(\p{Lu})/gu, "$1 $2");
  return spaced.split(/[^\p{L}\p{N}']+/u).map((w) => w.replace(/'s$/i, "").replaceAll("'", "").toLowerCase())
    .filter((w) => w.length > 1 && /\p{L}/u.test(w));
}

/** Find dataset moves corresponding to each move term. */
export function moveFacts(terms, dataset) {
  const out = new Map();
  if (!dataset?.moves) return out;
  const byName = new Map(dataset.moves.map((m) => [tableKey(m.name), m]));
  for (const t of terms) {
    if (t.category !== "move") continue;
    const ms = (t.in_source ?? []).map(tableKey).filter((id) => /^table:moves\/\d+$/.test(id))
      .map((id) => byName.get(id)).filter(Boolean);
    if (ms.length) out.set(t.id, ms);
  }
  return out;
}

const push = (map, key, id) => {
  if (!map.has(key)) map.set(key, []);
  map.get(key).push(id);
};

/** Group terms into ordered word, type and list families. */
export function familiesOf(terms, facts = new Map(), typeName = (n) => t("Type {n}", { n }), fold = {}) {
  const words = new Map();
  const types = new Map();
  const lists = new Map();
  for (const term of terms) {
    for (const w of new Set(englishWords(term.source_text))) {
      if (!GENERIC.has(w)) push(words, `${term.category}:${w}`, term.id);
    }
    const ms = facts.get(term.id) ?? [];
    for (const ty of new Set(ms.map((m) => m.type))) push(types, ty, term.id);
    for (const k of new Set(ms.flatMap((m) => m.list_keys ?? []))) if (MOVE_LISTS.includes(k)) push(lists, k, term.id);
  }
  const out = [];
  for (const [key, members] of words) {
    if (members.length < MIN_WORD) continue;
    const [category, word] = [key.slice(0, key.lastIndexOf(":")), key.slice(key.lastIndexOf(":") + 1)];
    out.push({ id: `word:${key}`, kind: "word", category, title: cap(word), members });
  }
  for (const [ty, members] of types) {
    if (members.length >= MIN_DATA) {
      out.push({ id: `type:${ty}`, kind: "type", category: "move", title: t("{type} moves", { type: typeName(ty) }), members });
    }
  }
  for (const [k, members] of lists) {
    if (members.length >= MIN_DATA) {
      out.push({ id: `list:${k}`, kind: "list", category: "move", title: t("{list} moves", { list: moveListName(k) ?? k }),
        members });
    }
  }
  return out.sort((a, b) => KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind) ||
    byText(a.category, b.category) || byText(norm(a.title, fold), norm(b.title, fold)));
}

/** Look up a move's table facts, if available. */
export function moveOf(ms) {
  const m = ms?.[0];
  if (!m) return null;
  return { power: basePower(m), power_kind: m.power_kind, accuracy: m.accuracy, pp: m.pp,
    damage: DAMAGE[m.damage_category] ?? "", type: m.type };
}

/** Preserve undeclared records' existing variable display. */
const variable = (m) => m.power_kind === "computed" || (m.power_kind == null && m.power === 1);

/** Rank damaging, variable-power and status moves. */
const rank = (m) => (!m ? 3 : variable(m) ? 1 : m.power > 0 ? 0 : 2);

/** Build family rows with local edits, ordered by power. */
export function memberRows(family, term, facts = new Map(), current = termFields, edited = () => false) {
  const rows = family.members.map((id) => term(id)).filter(Boolean).map((t) => {
    const r = current(t);
    return { id: t.id, source_text: t.source_text ?? "", translation: r.translation ?? "", label: r.label ?? "",
      edited: edited(t.id), move: moveOf(facts.get(t.id)) };
  });
  return rows.sort((a, b) => rank(a.move) - rank(b.move) || (a.move?.power ?? 0) - (b.move?.power ?? 0) ||
    byText(norm(a.source_text), norm(b.source_text)) || byText(a.id, b.id));
}

/** Check whether text contains a hyphen. */
export const hasHyphen = (s) => /[-‐‑]/.test(s ?? "");

/** Check whether a label differs from its translation. */
export const isShortened = (r, fold = {}) => Boolean(r.label) && norm(r.label, fold) !== norm(r.translation, fold);

/** Extract translation words of at least three letters. */
export const translationWords = (s, fold = {}) => norm(s, fold).split(/[^\p{L}\p{N}]+/u).filter((w) => w.length >= 3);

/**
 * Find a translation word shared by half the rows.
 * Compound words participate in the comparison.
 */
export function sharedWord(rows, fold = {}) {
  const words = new Map();
  for (const r of rows) {
    for (const w of new Set(translationWords(r.translation, fold))) {
      const stem = w.slice(0, STEM);
      if (!words.has(stem)) words.set(stem, new Map());
      words.get(stem).set(w, (words.get(stem).get(w) ?? 0) + 1);
    }
  }
  let best = null;
  for (const stem of [...words.keys()].sort(byText)) {
    const ids = new Set(rows.filter((r) => norm(r.translation, fold).includes(stem)).map((r) => r.id));
    if (!best || ids.size > best.ids.size) best = { stem, ids };
  }
  if (!best || best.ids.size < 2 || best.ids.size * 2 < rows.length) return null;
  const word = [...words.get(best.stem)].sort(([a, x], [b, y]) => y - x || a.length - b.length || byText(a, b))[0][0];
  return { stem: best.stem, word, ids: best.ids };
}

/** Mark shortened labels, hyphens and shared-word exceptions. */
export function marksOf(rows, fold = {}) {
  const shared = sharedWord(rows, fold);
  return {
    hyphen: new Set(rows.filter((r) => hasHyphen(r.translation)).map((r) => r.id)),
    shortened: new Set(rows.filter((r) => isShortened(r, fold)).map((r) => r.id)),
    shared,
    apart: new Set(shared ? rows.filter((r) => !shared.ids.has(r.id)).map((r) => r.id) : []),
  };
}

/** Summarize the review marks for one family. */
export function marksText(marks, n) {
  return [
    marks.shared ? t("{shared} of {n} share «{word}»", { shared: marks.shared.ids.size, n, word: marks.shared.word })
      : t("no word shared by half"),
    t("{n} hyphenated", { n: marks.hyphen.size }),
    t("{n} shortened", { n: marks.shortened.size }),
  ].join(" · ");
}

/** Filter families by search, kind and category. */
export function filterFamilies(families, filters, term = () => null, fold = {}) {
  const normalize = (s) => norm(s, fold);
  const q = normalize((filters.search || "").trim());
  const out = [];
  families.forEach((f, i) => {
    if (filters.kind && f.kind !== filters.kind) return;
    if (filters.category && f.category !== filters.category) return;
    if (q && !normalize(f.title).includes(q) && f.id !== q && !f.members.some((id) => {
      const t = term(id);
      return t && (normalize(t.source_text).includes(q) || normalize(t.translation).includes(q));
    })) return;
    out.push(i);
  });
  return out;
}

/** Format move power, accuracy and PP for display. */
export function moveCells(m) {
  if (!m) return ["", "", "", ""];
  const power = variable(m) ? t("varies") : m.power > 0 ? String(m.power) : "—";
  const accuracy = m.accuracy ? `${m.accuracy}%` : "—";
  return [power, accuracy, String(m.pp ?? ""), m.damage ? t(m.damage) : ""];
}

/** Render family comparisons using shared terms and dataset access. */
export class FamiliesView {
  get fold() {
    return this.host.index?.()?.language_rules?.fold;
  }

  constructor(host, $) {
    this.host = host;
    this.$ = $;
    this.families = [];
    this.filtered = [];
    this.cur = null;
    this.note = null; // Keep the count translatable before a list loads.
  }

  /** Remember count parameters for language changes. */
  countNote(key, vars = null) {
    this.note = key ? { key, vars } : null;
    this.$("fam-count").textContent = key ? t(key, vars) : "";
  }

  /** Reset cached families after a language change. */
  reset(want = null) {
    this.host.reference?.(null);
    this.generation = (this.generation ?? 0) + 1;
    this.families = [];
    this.filtered = [];
    this.facts = new Map();
    this.byTerm = null;
    this.byId = null;
    this.cur = null;
    this.want = want;
    this.ready = null;
    this.listed = false;
    this.host.counted?.(0);
    this.countNote(null);
    this.$("fam-list").replaceChildren();
  }

  /** Load terms and move data, then compute families. */
  ensure() {
    if (this.ready) return this.ready;
    const generation = this.generation;
    this.ready = (async () => {
      await this.host.loadTerms();
      const dataset = await this.host.dataset().catch(() => null);
      if (generation !== this.generation) return false;
      const terms = this.host.terms() ?? [];
      this.byId = new Map(terms.map((t) => [t.id, t]));
      this.facts = moveFacts(terms, dataset);
      this.families = familiesOf(terms, this.facts, this.host.typeName, this.fold);
      this.byTerm = new Map();
      for (const f of this.families) for (const id of f.members) push(this.byTerm, id, f);
      this.noData = !dataset;
      this.host.counted?.(this.families.length);
      return true;
    })();
    this.ready.catch(() => {
      if (generation === this.generation) this.ready = null;
    });
    return this.ready;
  }

  /** Find every family containing a term. */
  async of(termId) {
    const generation = this.generation;
    if (!(await this.ensure()) || generation !== this.generation) return [];
    return this.byTerm.get(termId) ?? [];
  }

  term = (id) => this.byId?.get(id) ?? null;

  /** Read a term with its local edit applied. */
  current = (t) => this.host.edits()?.get(t.id)?.row ?? termFields(t);

  edited = (id) => Boolean(this.host.edits()?.get(id));

  rows(f) {
    return memberRows(f, this.term, this.facts, this.current, this.edited);
  }

  /** Open the requested family or the first match. */
  async show(id = null) {
    const generation = this.generation;
    if (!this.listed) this.countNote("Loading the families…");
    try {
      if (!(await this.ensure())) return;
    } catch (e) {
      if (generation !== this.generation) return;
      this.countNote("No families: {error}", { error: e.message || e });
      return;
    }
    if (generation !== this.generation) return;
    if (!this.listed) {
      this.listed = true;
      this.fillCategories();
    }
    this.applyFilters();
    const want = id ?? this.want ?? this.cur ?? this.families[this.filtered[0]]?.id;
    this.want = null;
    if (want && this.family(want)) this.open(want);
    else if (!this.families.length) this.renderEmpty();
  }

  family(id = this.cur) {
    return this.families.find((f) => f.id === id) ?? null;
  }

  fillCategories() {
    const sel = this.$("fam-category");
    const chosen = sel.value;
    sel.replaceChildren(el("option", { value: "", textContent: t("All categories") }));
    const n = new Map();
    for (const f of this.families) n.set(f.category, (n.get(f.category) ?? 0) + 1);
    for (const [c, k] of [...n].sort(([a], [b]) => byText(a, b))) {
      sel.append(el("option", { value: c, textContent: `${c} (${k})` }));
    }
    if (chosen && n.has(chosen)) sel.value = chosen;
  }

  filters() {
    const $ = this.$;
    return { search: $("fam-search").value, kind: $("fam-kind").value, category: $("fam-category").value };
  }

  applyFilters() {
    this.filtered = filterFamilies(this.families, this.filters(), this.term, this.fold);
    const count = { shown: formatNumber(this.filtered.length), total: formatNumber(this.families.length) };
    this.note = null;
    this.$("fam-count").textContent = this.noData
      ? t("{shown} of {total} families; no move data: word families only", count)
      : t("{shown} of {total} families", count);
    this.renderList();
  }

  renderList() {
    const kids = this.filtered.map((i) => this.rowElement(this.families[i]));
    this.$("fam-list").replaceChildren(...kids);
  }

  rowElement(f) {
    const rows = this.rows(f);
    const marks = marksOf(rows, this.fold);
    const edited = rows.some((r) => r.edited);
    return el("div", { class: `g-row${f.id === this.cur ? " sel" : ""}`, role: "option", "data-id": f.id,
      "aria-selected": String(f.id === this.cur), on: { click: () => this.open(f.id) } },
    el("div", { class: "l1" },
      el("span", { class: "mark uses", textContent: t(KINDS[f.kind]) }),
      el("span", { class: "muted", textContent: f.category }),
      el("span", { class: "muted", textContent: t("{n} terms", { n: rows.length }) }),
      edited ? el("span", { class: "mark edited", textContent: t("edited") }) : null),
    el("div", { class: "l2", textContent: f.title }),
    el("div", { class: "l3", textContent: marksText(marks, rows.length) }));
  }

  scrollToCurrent() {
    const row = this.$("fam-list").querySelector?.(`[data-id="${CSS.escape(this.cur)}"]`);
    row?.scrollIntoView?.({ block: "nearest" });
  }

  step(d) {
    if (!this.filtered.length) return;
    const p = this.filtered.findIndex((i) => this.families[i].id === this.cur);
    const q = p < 0 ? 0 : Math.min(this.filtered.length - 1, Math.max(0, p + d));
    this.open(this.families[this.filtered[q]].id);
  }

  open(id) {
    const f = this.family(id);
    if (!f) return;
    this.cur = id;
    this.host.setHash(id);
    for (const row of this.$("fam-list").children ?? []) {
      const on = row.dataset?.id === id;
      row.classList.toggle("sel", on);
      row.setAttribute("aria-selected", String(on));
    }
    this.scrollToCurrent();
    this.render(f);
  }

  renderEmpty() {
    this.$("fam-title").textContent = t("No family");
    this.$("fam-id").textContent = "";
    this.$("fam-table").replaceChildren();
    this.$("fam-marks").replaceChildren();
  }

  /** Render a family's comparison table and review marks. */
  render(f) {
    const $ = this.$;
    const rows = this.rows(f);
    const marks = marksOf(rows, this.fold);
    const moves = rows.some((r) => r.move);
    $("fam-title").textContent = f.title;
    $("fam-id").textContent = f.id;
    this.host.reference?.(f.id);
    Object.assign($("fam-kind-badge"), { textContent: t(KINDS[f.kind]), className: "badge" });
    $("fam-size").textContent = [f.category, t("{n} terms", { n: rows.length }), moves ? t("moves by power") : ""]
      .filter(Boolean).join(" · ");
    const n = rows.length;
    const shared = marks.shared && { shared: marks.shared.ids.size, n, word: marks.shared.word, apart: marks.apart.size };
    $("fam-marks").replaceChildren(
      el("div", { class: "ctx-item" }, el("b", { textContent: `${t("Shared word:")} ` }), !shared
        ? t("no translation word is shared by half of the terms")
        : shared.apart ? t("{shared} of {n} translations hold «{word}»; {apart} do not", shared)
          : t("{shared} of {n} translations hold «{word}»", shared)),
      el("div", { class: "ctx-item" }, el("b", { textContent: `${t("Hyphens:")} ` }),
        t("{count} of {n} translations hold a hyphen", { count: marks.hyphen.size, n })),
      el("div", { class: "ctx-item" }, el("b", { textContent: `${t("Shortened:")} ` }),
        t("{count} of {n} show a shortened label", { count: marks.shortened.size, n })));
    const head = [N_("English"), N_("Translation"), N_("Label"),
      ...(moves ? [N_("Power"), N_("Acc."), N_("PP"), N_("Category"), N_("Type")] : []), N_("Marks")];
    const table = el("table", { class: "table fam-table" },
      el("thead", {}, el("tr", {}, ...head.map((h) => el("th", { textContent: t(h),
        class: ["Power", "Acc.", "PP"].includes(h) ? "num" : "" })))));
    const body = el("tbody");
    for (const r of rows) {
      const cells = moveCells(r.move);
      const tags = [
        marks.apart.has(r.id) ? el("span", { class: "mark warn", textContent: t("no «{word}»", { word: marks.shared.word }) }) : null,
        marks.hyphen.has(r.id) ? el("span", { class: "mark uses", textContent: t("hyphen") }) : null,
        marks.shortened.has(r.id) ? el("span", { class: "mark uses", textContent: t("shortened") }) : null,
        r.edited ? el("span", { class: "mark edited", textContent: t("edited") }) : null,
      ].filter(Boolean);
      body.append(el("tr", { "data-id": r.id },
        el("td", {}, el("a", { class: "fam-open", textContent: r.source_text || r.id, title: t("Open {id} in Terms", { id: r.id }),
          on: { click: () => this.host.openTerm(r.id) } })),
        el("td", { textContent: r.translation || t("(no translation)") }),
        el("td", { textContent: r.label }),
        ...(moves ? [
          ...cells.slice(0, 3).map((c) => el("td", { class: "num", textContent: c })),
          el("td", { textContent: cells[3] }),
          el("td", { textContent: r.move ? this.host.typeName(r.move.type) : "" }),
        ] : []),
        el("td", { class: "fam-tags" }, ...tags)));
    }
    table.append(body);
    $("fam-table").replaceChildren(table);
  }

  /** Relabel the list and open family from cached state. */
  relabel() {
    if (!this.byId) {
      if (this.note) this.countNote(this.note.key, this.note.vars);
      return;
    }
    this.families = familiesOf(this.host.terms() ?? [], this.facts, this.host.typeName, this.fold);
    this.byTerm = new Map();
    for (const f of this.families) for (const id of f.members) push(this.byTerm, id, f);
    if (!this.listed) return;
    this.fillCategories();
    this.applyFilters();
    const f = this.family();
    if (f) this.render(f);
    else if (!this.families.length) this.renderEmpty();
  }

  wire() {
    const $ = this.$;
    $("fam-search").addEventListener("input", () => this.applyFilters());
    for (const id of ["fam-kind", "fam-category"]) $(id).addEventListener("change", () => this.applyFilters());
    $("fam-list").addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        this.step(e.key === "ArrowDown" ? 1 : -1);
      }
    });
    $("fam-prev").addEventListener("click", () => this.step(-1));
    $("fam-next").addEventListener("click", () => this.step(1));
  }
}
