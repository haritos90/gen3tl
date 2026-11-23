// Limit sources and counts of the entry view.
// Each window limit has a kind and a rule.
// Each rule shows in the site language.

import { keepUnits, limitReferences, limitText, sameExplanation, unitName } from "./checktext.js";
import { N_, t } from "./ui.js";

// keys: shown with t()
export const UNMEASURED_ADVICE = N_("Unmeasured: the limit is the width of the English text. The game may give more room, so keep a word whole where only this limit is in the way.");

export const UNVERIFIED_ADVICE = N_("Unverified: this limit is an estimate for this language, to be measured with its text in the game. Keep within it, and report a line the game cuts or a limit that proves too strict.");

const LABEL = { measured: N_("measured"), profile: N_("profile default"), source: N_("unmeasured") };
// the count names countsOf gives
const COUNTS = { characters: N_("characters"), bytes: N_("bytes"), lines: N_("lines"), boxes: N_("boxes") };

/** The source of a window's limit field, or null. */
export function sourceOf(window, field) {
  const s = window?.sources?.[field];
  if (!s) return null;
  const unverified = s.unverified === true;
  const references = limitReferences(s.rule);
  return { text: limitText(s.rule, Object.values(window.sources)), kind: s.kind,
    label: unverified ? t("unverified estimate") : LABEL[s.kind] ? t(LABEL[s.kind]) : s.kind,
    evidence: s.evidence ?? "", unmeasured: s.kind === "source", unverified,
    ...(references.length ? { references } : {}) };
}

/** The consumer of a window by its name, as the check names it. */
export const consumerOf = (window, name) => window?.consumers?.find((c) => sameExplanation(c.name, name));

/** The source of a consumer where its rule is not the window width's. */
export function consumerSource(window, consumer) {
  return sameExplanation(consumer.source?.rule, window.sources?.width?.rule) ? null
    : sourceOf({ sources: { width: consumer.source } }, "width");
}

/** Count of policy values that are estimates. */
export function unverifiedCount(index) {
  return Object.values(index?.unverified ?? {}).reduce((n, v) => n + (Array.isArray(v) ? v.length : v ? 1 : 0), 0);
}

/** The consumer whose arrow leaves the least room. */
export function arrowConsumer(window) {
  if (window?.keys != null) return null;
  const candidates = (window?.consumers ?? []).filter((c) => c.arrow > 0);
  return candidates.reduce((bound, c) => !bound || c.width - c.arrow < bound.width - bound.arrow ? c : bound, null);
}

/** Waiting room stays separate from ordinary width. */
export function arrowRoom(window) {
  if (window?.keys != null) return null;
  const consumer = arrowConsumer(window);
  if (consumer) return consumer.width - consumer.arrow;
  return !window?.consumers && window?.arrow > 0 ? window.width - window.arrow : null;
}

/** Resolve waiting behavior separately for each consumer. */
export function waitingWindows(text, window) {
  const consumers = window?.consumers ?? [{ width: window?.width, arrow: window?.arrow ?? 0,
    newline_scroll_after: window?.newline_scroll_after }];
  const out = [];
  let box = [], newlines = 0;
  for (const sep of [...text.matchAll(/\\[nlp]/g)].map((m) => m[0]).concat(null)) {
    if (sep === "\\n") newlines++;
    const candidates = consumers.filter((c) => c.arrow > 0 && window?.keys == null &&
      (sep === "\\l" || sep === "\\p" || (sep === "\\n" && c.newline_scroll_after != null && newlines > c.newline_scroll_after)));
    box.push(candidates.reduce((bound, c) => !bound || c.width - c.arrow < bound.width - bound.arrow ? c : bound, null));
    if (sep === "\\p" || sep === null) { out.push(box); box = []; }
  }
  return out;
}

/** Apply runtime breaks while preserving the draft. */
export function runtimeText(text, window) {
  if (window?.newline_scroll_after == null) return text;
  let newlines = 0;
  return text.replace(/\\n/g, () => ++newlines > window.newline_scroll_after ? "\\l" : "\\n");
}

/**
 * The bar of a checked line against its window.
 * A line 1 px over with shadow-only cut fits.
 * Prompt arrows limit waiting lines to their remaining room.
 */
export function barOf(line, window) {
  const keys = window?.keys != null;
  const arrow = !keys && line.room != null;
  const limit = keys ? window.keys : arrow ? Math.min(line.room, window?.width ?? line.room) : window?.width ?? 0;
  const value = keys ? line.keys ?? 0 : line.width;
  const shadow = !keys && line.shadow === true;
  const over = value === null || ((limit > 0 || arrow || keys) && value > limit && !shadow);
  const unit = keys ? "keys" : "px";
  const vars = { value, limit, unit: unitName(unit) };
  let label = value === null ? t("not encodable")
    : shadow ? t("{value} / {limit} {unit}, fits: shadow clipped", vars)
      : arrow && line.room <= (window?.width ?? line.room)
        ? t("{value} / {limit} {unit}, arrow after it", vars) : t("{value} / {limit} {unit}", vars);
  const consumer = arrow && line.room <= window.width ? consumerOf(window, line.consumer) ?? arrowConsumer(window)
    : window?.consumers?.find((c) => c.width === window.width);
  const sources = Object.values(window?.sources ?? {});
  const name = consumer ? limitText(consumer.name, sources) : null;
  const main = window?.sources?.width?.rule;
  if (consumer && (window.consumers.length > 1 || (main && name !== limitText(main, sources)))) {
    label += t(" · window: {name}", { name });
  }
  return { value, limit, unit, over, shadow, arrow, label: keepUnits(label) };
}

/**
 * Counts of a checked text against the window's limits.
 * A box with a scroll break may hold more lines.
 */
export function countsOf(res, window, size) {
  const out = [];
  if (!window) return out;
  for (const [name, limit] of [["characters", window.chars], ["bytes", window.bytes]]) {
    if (limit != null) out.push({ name: t(COUNTS[name]), value: size, limit, over: size === null || size > limit, note: "" });
  }
  const pars = runtimeText(res.laid, window).split("\\p");
  if ((window.kind === "dialogue" || window.kind === "battle" || window.newline_scroll_after != null) && window.lines) {
    res.lines.forEach((box, i) => {
      const scrolls = (pars[i] ?? "").includes("\\l") && box.length > window.lines;
      out.push({ name: t("lines, box {n}", { n: i + 1 }), value: box.length, limit: window.lines,
        over: box.length > window.lines && !scrolls, note: scrolls ? t("scrolls") : "" });
    });
  } else if (window.kind === "multiline" && window.lines != null) {
    const n = res.lines.reduce((s, box) => s + box.length, 0);
    out.push({ name: t(COUNTS.lines), value: n, limit: window.lines, over: n > window.lines, note: "" });
  }
  const en = window.source_lines?.length ?? 1;
  if (res.lines.length > 1 || en > 1) {
    out.push({ name: t(COUNTS.boxes), value: res.lines.length, limit: null, over: false,
      note: t("the English has {n}", { n: en }) });
  }
  return out;
}

/** The meters beside a checked text's lines. */
export function extentsOf(res) {
  const out = (res.others ?? []).map((o) => ({ label: limitText(o.where), value: o.value, limit: o.width, shadow: o.shadow === true,
    title: keepUnits(t("{n} px in {where} ({font})", { n: o.width, where: limitText(o.where), font: o.font })) }));
  const p = res.pair;
  if (p) {
    out.push({ label: t("pair"), value: p.value, limit: p.width, shadow: false,
      title: keepUnits(t("{where}: with «{text}», {n} px, for «{species}»",
        { where: limitText(p.where), text: p.partner.text ?? p.partner.id, n: p.partner.width,
          species: p.partner.species.text ?? p.partner.species.id })) });
  }
  return out;
}

/** A window's pair as the entry view lists it. */
export function pairLabel(pair) {
  const p = pair.partner;
  return keepUnits(t("{n} px in {where} with «{text}», {width} px, for «{species}»",
    { n: pair.width, where: limitText(pair.where), text: p.text ?? p.id, width: p.width, species: p.species.text ?? p.species.id }));
}

/** A count as the bars show it: "12 / 15 bytes". */
export function countLabel(c) {
  const value = c.value ?? "?";
  const main = c.limit == null ? t("{value} {name}", { value, name: c.name })
    : t("{name}: {value} / {limit}", { name: c.name, value, limit: c.limit });
  return c.note ? t("{count} ({note})", { count: main, note: c.note }) : main;
}

/** The line meter of the workspace: fill, state and overrun. */
export function meterOf(line, window) {
  const bar = barOf(line, window);
  const fill = bar.limit ? Math.min(100, Math.round(((bar.value ?? bar.limit) / bar.limit) * 100)) : 0;
  const near = !bar.over && !bar.shadow && window?.target && window.keys == null && bar.value > window.target;
  const state = bar.over ? "over" : bar.shadow ? "shadow" : near ? "near" : "ok";
  const by = bar.over && bar.value !== null && bar.limit ? bar.value - bar.limit : 0;
  return { ...bar, fill, state, by,
    label: keepUnits(by ? t("{label} · {by} {unit} over", { label: bar.label, by, unit: unitName(bar.unit) }) : bar.label) };
}
