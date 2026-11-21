// Catalog text as editing units.
// A unit is a character, a break or a token.
// Joining the units gives the text back.

export const BREAKS = ["\\n", "\\l", "\\p"];
const TOKEN = /(\\[nlp]|\{[^}]*\})/g; // layout.TOKEN

export const isBreak = (u) => u.length === 2 && u[0] === "\\";
export const isToken = (u) => u.length > 1 && u[0] === "{";

/** Units of a catalog text. */
export function parse(text) {
  const out = [];
  let pos = 0;
  for (const m of text.matchAll(TOKEN)) {
    for (const c of text.slice(pos, m.index)) out.push(...splitChar(c));
    out.push(m[0]);
    pos = m.index + m[0].length;
  }
  for (const c of text.slice(pos)) out.push(...splitChar(c));
  return out;
}

// code units: the DOM counts UTF-16 offsets
function splitChar(c) {
  return c.length === 1 ? [c] : c.split("");
}

/** The catalog text of units. */
export const join = (units) => units.join("");

/** Units of typed text: no breaks, no backslash. */
export function typed(text) {
  return [...text]
    .filter((c) => c.length === 1 && c !== "\\" && c >= " " && c !== "\u200b")
    .map((c) => (c === "\u00a0" ? " " : c));
}

/** Units of pasted text: real newlines become \n. */
export function pasted(text) {
  const t = text.replace(/\r\n?/g, "\n").replace(/\n/g, "\\n");
  const out = [];
  for (const u of parse(t)) {
    if (u.length > 1) out.push(u);
    else out.push(...typed(u));
  }
  return out;
}

/** Units as parsed, caret kept; typed tokens become one unit. */
export function normalize(units, caret) {
  const at = join(units.slice(0, caret)).length;
  const out = parse(join(units));
  let n = 0;
  let len = 0;
  while (n < out.length && len + out[n].length <= at) len += out[n++].length;
  return { units: out, caret: len < at ? n + 1 : n };
}

/** Replace units [start, end) with `add`; returns {units, caret}. */
export function splice(units, start, end, add = []) {
  const out = units.slice(0, start).concat(add, units.slice(end));
  return { units: out, caret: start + add.length };
}

/** Delete one unit or the selection, backwards or forwards. */
export function erase(units, start, end, forward = false, word = false) {
  if (start !== end) return splice(units, start, end);
  if (word) {
    const [a, b] = forward ? [start, wordEnd(units, start)] : [wordStart(units, start), start];
    return splice(units, a, b);
  }
  if (forward) return splice(units, start, Math.min(units.length, start + 1));
  return splice(units, Math.max(0, start - 1), start);
}

const space = (u) => u === " " || isBreak(u);

/** Start of the word before `at` (Ctrl+Backspace). */
export function wordStart(units, at) {
  let i = at;
  while (i > 0 && space(units[i - 1])) i--;
  if (i > 0 && isToken(units[i - 1])) return i - 1;
  while (i > 0 && !space(units[i - 1]) && !isToken(units[i - 1])) i--;
  return i === at ? Math.max(0, at - 1) : i;
}

/** End of the word after `at` (Ctrl+Delete). */
export function wordEnd(units, at) {
  let i = at;
  while (i < units.length && space(units[i])) i++;
  if (i < units.length && isToken(units[i])) return i + 1;
  while (i < units.length && !space(units[i]) && !isToken(units[i])) i++;
  return i === at ? Math.min(units.length, at + 1) : i;
}

/**
 * The break Enter inserts at `at`.
 * Gen 3 boxes: first break \n, later ones scroll.
 */
export function enterBreak(units, at, kind) {
  if (kind !== "dialogue" && kind !== "battle") return "\\n";
  for (let i = at - 1; i >= 0; i--) {
    if (units[i] === "\\p") break;
    if (units[i] === "\\n" || units[i] === "\\l") return "\\l";
  }
  return "\\n";
}

/** Boxes and lines of a text: [[line, ...], ...]. */
export function boxes(text) {
  return text.split("\\p").map((p) => p.split(/\\[nl]/));
}

/** Plain one-line form for the entry list. */
export function flat(text) {
  return text.replace(/\\p/g, " ¶ ").replace(/\\[nl]/g, " ⏎ ");
}

/** Search form: breaks as spaces, lower case. */
export function searchable(text) {
  return text.replace(/\\[nlp]/g, " ").toLowerCase();
}

/** Kind of a brace token, for its chip. */
export function tokenKind(tok, codec) {
  const inner = tok.slice(1, -1).trim();
  const [head, ...args] = inner.split(/\s+/);
  const hex = (a) => /^[0-9A-F]{2}$/.test(a);
  if (inner === "") return "unknown";
  if (args.length === 0 && hex(head)) return "symbol";
  if (head === "FD" && args.length === 1 && args.every(hex)) return "placeholder";
  if (head === "FC" && args.every(hex)) return "control";
  if (["F7", "F8", "F9"].includes(head) && args.length === 1 && args.every(hex)) return "symbol";
  if (head in codec.controls) return args.length === codec.controls[head][1] && args.every(hex) ? "control" : "unknown";
  if (args.length === 0 && head in codec.placeholders) return "placeholder";
  if (args.length === 0 && head in codec.named) return "symbol";
  return "unknown";
}

/** Undo history of {units, caret} states. */
export class History {
  constructor(limit = 200) {
    this.limit = limit;
    this.past = [];
    this.future = [];
    this.lastKind = null;
  }

  /** Record the state before a change; typing runs merge. */
  record(state, kind = null) {
    if (kind !== null && kind === this.lastKind && this.past.length) {
      this.future = [];
      return;
    }
    this.past.push(state);
    if (this.past.length > this.limit) this.past.shift();
    this.future = [];
    this.lastKind = kind;
  }

  undo(current) {
    if (!this.past.length) return null;
    this.future.push(current);
    this.lastKind = null;
    return this.past.pop();
  }

  redo(current) {
    if (!this.future.length) return null;
    this.past.push(current);
    this.lastKind = null;
    return this.future.pop();
  }

  clear() {
    this.past = [];
    this.future = [];
    this.lastKind = null;
  }
}
