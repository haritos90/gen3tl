// Pack keyboard drafts travel with exported edits.

import { el } from "./dom.js";
import { INK, SHADOW, splitTokens } from "./font.js";
import { drawSheet } from "./pictures.js";
import { standIn, standInName } from "./standin.js";
import { N_, errorText, formatNumber, t, uiError } from "./ui.js";

// Preview colours approximate the game's keyboard palette.
const FILL = ["#d09870", "#78a8c0", "#90b868"]; // by base keyboard
const FRAME = ["#c0e098", "#98c8e0", "#f0c8a0"]; // by look
const INKS = { [INK]: "#f8f8f8", [SHADOW]: "#606060" };
const GREY = "#a8a8b0";
const MARGIN = 4; // A frame strip shows the page's look.
const TILE = 8;

export const SOURCES = { glyph: N_("pack glyph"), copy: N_("copy of {code}"), base: N_("base glyph"), none: N_("no glyph") };

/** Pages in Select order, from the opening page. */
export function pageOrder(kb) {
  const n = kb.pages.length;
  const first = Math.min(Math.max(kb.first_page ?? 0, 0), n - 1);
  return Array.from({ length: n }, (_, i) => (first + i) % n);
}

/** The page Select switches to. */
export const nextPage = (kb, i) => (i + 1) % kb.pages.length;

/** The page Select switches from. */
export const previousPage = (kb, i) => (i + kb.pages.length - 1) % kb.pages.length;

/** The graphic id of a page's label, or null. */
export const labelOf = (kb, i) => kb.labels?.[kb.pages[i].look] ?? null;

/** The keyboard a page shows. */
export const boardOf = (kb, i) => kb.keyboards[kb.pages[i].keyboard];

/** The cursor frame of a key column, or null. */
export function cursorFrame(kb, board, column) {
  const x = board.column_x?.[column];
  const frame = kb.window.cursor;
  return x == null || !frame ? null : [x + frame.left, x + frame.left + frame.width];
}

/** Key cells of a page by row. */
export function pageCells(kb, i) {
  const board = boardOf(kb, i);
  const w = kb.window;
  return board.rows.map((row, r) => row.cells.map((c, column) => {
    const y = w.top + r * w.row_height;
    const g = c.glyph;
    const reachable = board.columns == null || column < board.columns;
    const cursor = reachable ? cursorFrame(kb, board, column) : null;
    return {
      row: r, column, key: c.key, glyph: g, y, reachable, cursor,
      tile: g ? [Math.floor(g.x / TILE), Math.floor(y / TILE)] : null,
      differs: Boolean(c.key && g && c.key.char !== g.char),
      unprinted: reachable && Boolean(c.key) && !g,
      missing: reachable && c.key?.source === "none",
      misplaced: Boolean(g && cursor) && (g.x < cursor[0] || g.x + g.width > cursor[1]),
    };
  }));
}

/** Count review marks of a page. */
export function marksOf(kb, i) {
  const cells = pageCells(kb, i).flat();
  return {
    keys: cells.filter((c) => c.reachable && c.key).length,
    missing: cells.filter((c) => c.missing).length,
    differs: cells.filter((c) => c.reachable && (c.differs || c.unprinted)).length,
    misplaced: cells.filter((c) => c.misplaced).length,
  };
}

function rect(g, x, y, w, h, colour) {
  g.fillStyle = colour;
  g.fillRect(x, y, w, h);
}

/** Glyph pixels from the pack, the ROM or a stand-in. */
function pixelsOf(glyph, glyphs, codec, cache) {
  const code = parseInt(glyph.code, 16);
  const found = glyphs.get(code);
  if (found.pixels) return { pixels: found.pixels, exact: found.exact };
  const name = standInName(glyph.char, code, codec);
  const key = `${glyphs.format}:${glyph.width}:${name}`;
  if (!cache.has(key)) cache.set(key, standIn(name, glyph.width, glyphs.format === "8x16", codec.standIns));
  return { pixels: cache.get(key), exact: false };
}

const hex2 = (n) => n.toString(16).toUpperCase().padStart(2, "0");
const CLEAR = /^\{CLEAR ([0-9A-Fa-f]+)\}$/;
const CLEAR_TO = /^\{CLEAR_TO ([0-9A-Fa-f]+)\}$/;
const SKIP = /^\{SKIP ([0-9A-Fa-f]+)\}$/;

/** Measure every key against its cursor slot. */
export function chatRow(text, chat, row, codec, widthOf) {
  const tokens = splitTokens(text).filter(Boolean);
  if (tokens.length !== chat.columns) throw uiError("Expected {n} keys, got {actual}.", { n: chat.columns, actual: tokens.length });
  let width = 0;
  let bytes = 0;
  const glyphs = tokens.map((char) => {
    const raw = codec.encode(char);
    const code = raw.length === 1 && raw[0] < 0xF7 ? raw[0]
      : raw.length === 2 && raw[0] === 0xF9 ? 0x100 + raw[1] : null;
    if (code === null) throw uiError("{char} is not a keyboard glyph.", { char });
    const advance = widthOf(code);
    const glyph = { char, code: hex2(code), x: width, width: advance };
    bytes += raw.length;
    width += Math.max(chat.column_width, advance);
    return glyph;
  });
  const limit = chat.columns * chat.column_width;
  if (width > limit) throw uiError("Row width {width} exceeds {limit} px.", { width, limit });
  if (bytes > row.bytes) throw uiError("Row needs {bytes} bytes; its slot holds {limit}.", { bytes, limit: row.bytes });
  return { glyphs, width, bytes };
}

/** The byte of one character, or null. */
function codeOf(char, codec) {
  try {
    const b = codec.encode(char);
    return b.length === 1 ? b[0] : null;
  } catch {
    return null;
  }
}

/** The character a key enters, its byte and glyph source. */
export function keyCell(char, codec, style) {
  const code = codeOf(char, codec);
  const h = code === null ? null : hex2(code);
  const source = h === null ? "none" : h in (style?.pack ?? {}) ? "glyph" : h in (style?.copy ?? {}) ? "copy" : "base";
  return { char, code: h, source, ...(source === "copy" ? { copy: style.copy[h] } : {}) };
}

/** Glyph widths the data measured, by byte. */
export function measuredWidths(kb) {
  const out = new Map();
  for (const board of kb.keyboards) {
    for (const row of board.rows) for (const c of row.cells) if (c.glyph?.code) out.set(parseInt(c.glyph.code, 16), c.glyph.width);
  }
  return out;
}

/** The glyphs of a printed row, placed by the printer. */
export function printedGlyphs(text, codec, widthOf) {
  let x = 0;
  const out = [];
  for (const tok of splitTokens(text)) {
    if (!tok) continue;
    const m = CLEAR.exec(tok) ?? CLEAR_TO.exec(tok) ?? SKIP.exec(tok);
    if (m) {
      const n = parseInt(m[1], 16);
      x = tok.startsWith("{CLEAR ") ? x + n : tok.startsWith("{CLEAR_TO") ? Math.max(x, n) : n;
      continue;
    }
    if (tok.length > 2 && tok.startsWith("{") && codec.zero.test(tok)) continue;
    const code = codeOf(tok, codec);
    const width = code === null ? 0 : widthOf(code);
    out.push({ char: tok, code: code === null ? null : hex2(code), x, width });
    x += width;
  }
  return out;
}

/** The cells of a row from keys and printed texts. */
export function rowCells(keys, display, codec, style, widthOf) {
  const k = splitTokens(keys).filter(Boolean).map((c) => keyCell(c, codec, style));
  const g = printedGlyphs(display, codec, widthOf);
  return Array.from({ length: Math.max(k.length, g.length) }, (_, i) => ({ key: k[i] ?? null, glyph: g[i] ?? null }));
}

/** The English of a keyed row part. */
function restoredText(part, texts) {
  return part.text ?? (part.id != null && texts?.[part.id] != null ? texts[part.id] : null);
}

/** Rebuild rows from available text and current glyph widths. */
export function keyboardWithEnglish(kb, english, codec, style, widthOf) {
  const texts = english?.texts ?? null;
  return {
    ...kb,
    keyboards: kb.keyboards.map((board) => ({
      ...board,
      rows: board.rows.map((row) => {
        const keys = { ...row.keys, text: restoredText(row.keys, texts) };
        const display = { ...row.display, text: restoredText(row.display, texts) };
        const cells = keys.text || display.text
          ? rowCells(keys.text ?? "", display.text ?? "", codec, style, widthOf)
          : row.cells;
        return { ...row, keys, display, cells };
      }),
    })),
  };
}

/** The keyboard with the browser's edits. */
export function editedKeyboard(kb, edits, codec, style, widthOf) {
  const order = edits.pages();
  let rows = 0;
  const out = { ...kb, keyboards: kb.keyboards.map((board) => (board.base ? board : {
    ...board,
    rows: board.rows.map((row) => {
      const keys = row.keys.name ? edits.row(row.keys.name) : undefined;
      const display = row.display.name ? edits.row(row.display.name) : undefined;
      if (keys === undefined && display === undefined) return row;
      rows++;
      const texts = { keys: keys ?? row.keys.text, display: display ?? row.display.text };
      return { keys: { ...row.keys, text: texts.keys }, display: { ...row.display, text: texts.display },
        cells: rowCells(texts.keys, texts.display, codec, style, widthOf), edited: true };
    }),
  })) };
  const byName = new Map(kb.pages.map((p) => [p.name, p]));
  if (order && order.length === kb.pages.length && order.every((n) => byName.has(n))) {
    out.pages = order.map((n) => byName.get(n));
    out.reordered = true;
  }
  out.edited = rows > 0 || Boolean(out.reordered);
  return out;
}

/** The texts of a row after a key enters `char`. */
export function keyEdit(kb, board, r, column, char, codec, widthOf) {
  const row = board.rows[r];
  if ([...char].length !== 1) throw uiError("Type one character.");
  const code = codeOf(char, codec);
  if (code === null) throw uiError("{char} has no byte in this language.", { char });
  const keys = splitTokens(row.keys.text ?? "").filter(Boolean);
  if (column >= keys.length) throw uiError("The row has no key in column {n}.", { n: column + 1 });
  keys[column] = char;
  const toks = splitTokens(row.display.text ?? "").filter(Boolean);
  const glyphs = [];
  let x = 0;
  toks.forEach((tok, i) => {
    const m = CLEAR.exec(tok);
    if (m) x += parseInt(m[1], 16);
    else if (!(tok.length > 2 && tok.startsWith("{"))) {
      const measured = row.cells[glyphs.length]?.glyph;
      const known = measured?.char === tok;
      const width = known ? measured.width : widthOf(codeOf(tok, codec) ?? 0);
      glyphs.push({ i, x: known ? measured.x : x, width });
      x = glyphs.at(-1).x + width;
    }
  });
  const g = glyphs[column];
  const clear = g && CLEAR.exec(toks[g.i - 1] ?? "");
  if (!clear) throw uiError("The printed row places no glyph after a CLEAR code in column {n}.", { n: column + 1 });
  const start = g.x - parseInt(clear[1], 16);
  const width = widthOf(code);
  const frame = cursorFrame(kb, board, column);
  const at = frame ? frame[0] + Math.floor((frame[1] - frame[0] - width) / 2) : g.x;
  if (at < start) throw uiError("{char} does not fit beside the key before it.", { char });
  toks[g.i - 1] = `{CLEAR ${hex2(at - start)}}`;
  toks[g.i] = char;
  const next = glyphs[column + 1];
  if (next) {
    const gap = CLEAR.exec(toks[next.i - 1] ?? "");
    if (!gap || next.i - 1 !== g.i + 1 || next.x < at + width) {
      throw uiError("{char} does not fit beside the key after it.", { char });
    }
    toks[next.i - 1] = `{CLEAR ${hex2(next.x - at - width)}}`;
  }
  return { keys: keys.join(""), display: toks.join("") };
}

/** Draw ink and shadow inside a clip box. */
function drawGlyph(g, px, x, y, width, clip) {
  for (let gy = 0; gy < 16; gy++) {
    for (let gx = 0; gx < Math.min(width, 16); gx++) {
      const v = px[gy * 16 + gx];
      const [cx, cy] = [x + gx, y + gy];
      if ((v === INK || v === SHADOW) && cx >= clip.x && cy >= clip.y && cx < clip.x + clip.w && cy < clip.y + clip.h) {
        rect(g, cx, cy, 1, 1, INKS[v]);
      }
    }
  }
}

/**
 * Draw a page's key window as the game prints it.
 * Returns the count of approximate glyphs.
 */
export function drawPage(canvas, kb, i, glyphs, codec, zoom = 2) {
  const page = kb.pages[i];
  const board = boardOf(kb, i);
  const w = kb.window;
  canvas.width = w.width + 2 * MARGIN;
  canvas.height = w.height + 2 * MARGIN;
  canvas.style.width = `${canvas.width * zoom}px`;
  canvas.style.height = `${canvas.height * zoom}px`;
  const g = canvas.getContext("2d");
  rect(g, 0, 0, canvas.width, canvas.height, FRAME[page.look] ?? GREY);
  rect(g, MARGIN, MARGIN, w.width, w.height, FILL[board.like] ?? GREY);
  const clip = { x: MARGIN, y: MARGIN, w: w.width, h: w.height };
  const cache = new Map();
  let approximate = 0;
  board.rows.forEach((row, r) => {
    const y = MARGIN + w.top + r * w.row_height;
    for (const c of row.cells) {
      if (!c.glyph?.code) continue;
      const { pixels, exact } = pixelsOf(c.glyph, glyphs, codec, cache);
      if (!exact && c.glyph.char.trim()) approximate++;
      drawGlyph(g, pixels, MARGIN + c.glyph.x, y, c.glyph.width, clip);
    }
  });
  return { approximate };
}

/** Draw one key's glyph on its page colour. */
export function drawKey(canvas, kb, i, glyph, glyphs, codec, zoom = 2) {
  canvas.width = 16;
  canvas.height = 16;
  canvas.style.width = `${16 * zoom}px`;
  canvas.style.height = `${16 * zoom}px`;
  const g = canvas.getContext("2d");
  rect(g, 0, 0, 16, 16, FILL[boardOf(kb, i).like] ?? GREY);
  if (!glyph?.code) return;
  const { pixels } = pixelsOf(glyph, glyphs, codec, new Map());
  drawGlyph(g, pixels, Math.max(0, Math.floor((16 - glyph.width) / 2)), 0, glyph.width, { x: 0, y: 0, w: 16, h: 16 });
}

const shown = (ch) => (ch === " " ? t("space") : ch);

/** Show the naming keyboard pages of a language. */
export class KeyboardsView {
  constructor(host, $) {
    this.host = host;
    this.$ = $;
    this.cur = null;
    this.chatCur = null;
    this.chatNodes = new Map();
    this.zoom = 2;
    this.sel = null; // the key being edited: {row, column}
    this.error = null;
    this.moveError = null;
    this.chatErrors = new Map();
  }

  /** The data's keyboard. */
  get data() {
    return this.host.index()?.keyboard ?? null;
  }

  /** Restore English and measure with current glyph widths. */
  get baseKb() {
    const kb = this.data;
    const english = this.host.english?.() ?? null;
    if (!kb) return kb;
    return keyboardWithEnglish(kb, english, this.host.codec(), this.style(), this.widthOf());
  }

  /** The keyboard with this browser's edits. */
  get kb() {
    const kb = this.baseKb;
    const edits = this.host.keyboardEdits?.();
    if (!kb || !edits?.map.size) return kb;
    return editedKeyboard(kb, edits, this.host.codec(), this.style(), this.widthOf());
  }

  get target() {
    if (this.chatCur !== null) return ["chat", this.chatCur];
    const page = this.kb?.pages[this.cur];
    return page ? ["page", page.name] : [];
  }

  pageIndex(id, kind = null) {
    if (kind === "page") return this.kb?.pages.findIndex((page) => page.name === id) ?? -1;
    return /^\d+$/.test(String(id)) ? Number(id) : -1;
  }

  chatRow(name) {
    return this.data?.chat?.pages.flatMap((page) => page.rows).find((row) => row.name === name);
  }

  openTarget(id, kind = null) {
    if (kind === "chat") this.openChat(id);
    else this.open(this.pageIndex(id, kind));
  }

  openChat(name) {
    if (!this.chatRow(name)) return;
    if (this.cur === null) this.cur = pageOrder(this.kb)[0];
    this.chatCur = name;
    this.sel = null;
    this.host.setHash(this.target);
    this.renderList();
    this.render();
    const { row, input } = this.chatNodes.get(name);
    row.scrollIntoView?.({ block: "nearest" });
    (input.disabled ? row : input).focus?.();
  }

  /** The font style of the keys. */
  style() {
    return this.host.index()?.fonts?.styles?.[this.data?.style] ?? null;
  }

  /** Prefer known draft widths; preserve measured fallback widths. */
  widthOf() {
    const measured = measuredWidths(this.data);
    const glyphs = this.host.glyphs(this.data.style);
    return (code) => glyphs?.table.known[code] ? glyphs.width(code) : measured.get(code) ?? glyphs?.width(code) ?? 6;
  }

  /** A key can be edited: a pack key in reach. */
  editable(board, cell) {
    return Boolean(this.host.keyboardEdits?.()) && !board.base && cell.reachable && Boolean(cell.glyph && cell.key);
  }

  select(row, column) {
    this.chatCur = null;
    this.host.setHash(this.target);
    this.sel = { row, column };
    this.error = null;
    this.render();
    this.$("k-edit").querySelector?.("input")?.focus?.();
  }

  /** Enter `char` at the selected key; the preview follows. */
  setKey(char) {
    const kb = this.kb;
    const board = boardOf(kb, this.cur);
    const cell = this.sel && pageCells(kb, this.cur)[this.sel.row]?.[this.sel.column];
    if (!cell || !this.editable(board, cell)) return;
    const { row, column } = this.sel;
    const r = board.rows[row];
    const original = this.baseKb.keyboards[kb.pages[this.cur].keyboard].rows[row];
    try {
      const texts = keyEdit(kb, board, row, column, char, this.host.codec(), this.widthOf());
      const edits = this.host.keyboardEdits();
      edits.setRow(r.keys.name, texts.keys, original.keys.text, true);
      edits.setRow(r.display.name, texts.display, original.display.text);
      this.error = null;
      this.host.changed();
    } catch (e) {
      this.error = e;
    }
    this.renderList();
    this.render();
  }

  /** Draft widths override the loaded pack's measurements. */
  chatWidthOf() {
    const chat = this.data.chat;
    const glyphs = this.host.glyphs(chat.style);
    const measured = Object.assign({}, ...chat.pages.flatMap((p) => p.rows.map((r) => r.widths)));
    return (code) => glyphs?.table.known[code] ? glyphs.width(code) : measured[hex2(code)] ?? glyphs?.width(code) ?? 6;
  }

  /** Reject invalid rows before changing the draft. */
  setChatRow(row, text) {
    try {
      chatRow(text, this.data.chat, row, this.host.codec(), this.chatWidthOf());
      this.host.keyboardEdits().setRow(row.name, text, row.text, true);
      this.chatErrors.delete(row.name);
      this.host.changed();
      this.renderEdit();
      this.renderChat();
    } catch (error) {
      this.chatErrors.set(row.name, { error, text });
      this.renderChat();
    }
  }

  /** Move a page one place in Select order. */
  movePage(i, d) {
    const kb = this.kb;
    const n = kb.pages.length;
    const j = (i + d + n) % n;
    const pages = [...kb.pages];
    [pages[i], pages[j]] = [pages[j], pages[i]];
    const broken = kb.opening?.find((p) => pages[p.page].keyboard !== p.keyboard || pages[p.page].look !== p.look);
    if (broken) {
      this.moveError = uiError("Opening slot {slot} requires keyboard {keyboard} and base page look {look}.",
        { slot: broken.page, keyboard: broken.keyboard, look: broken.look });
      this.renderOrder();
      return;
    }
    this.moveError = null;
    const names = pages.map((p) => p.name);
    this.host.keyboardEdits().setPages(names, this.data.pages.map((p) => p.name));
    this.host.changed();
    this.cur = j;
    this.chatCur = null;
    this.sel = null;
    this.host.setHash(this.target);
    this.renderList();
    this.render();
  }

  /** Drop every keyboard edit of this browser. */
  discard() {
    const [kind, id] = this.target;
    this.host.keyboardEdits().clear();
    this.host.changed();
    this.sel = null;
    this.error = null;
    this.chatErrors.clear();
    this.reset();
    this.openTarget(id, kind);
  }

  /** Select a page after a language loads. */
  reset(want = null, select = true) {
    this.host.reference?.(null);
    const kb = this.kb;
    this.cur = null;
    this.chatCur = null;
    this.moveError = null;
    this.chatErrors.clear();
    this.$("count-keyboards").textContent = kb ? formatNumber(kb.pages.length + (kb.chat?.pages.length ?? 0)) : "";
    if (!kb || !select) {
      this.renderEmpty();
      return;
    }
    const n = /^\d+$/.test(String(want)) ? Number(want) : -1;
    this.cur = n >= 0 && n < kb.pages.length ? n : pageOrder(kb)[0];
    this.renderList();
    this.render();
  }

  /** Redraw after the glyphs change. */
  refresh() {
    const kb = this.kb;
    if (!kb || this.cur === null || this.cur >= kb.pages.length) return;
    this.renderList();
    this.render();
  }

  relabel() {
    this.refresh();
  }

  step(d) {
    const kb = this.kb;
    if (!kb) return;
    const order = pageOrder(kb);
    const p = order.indexOf(this.cur);
    this.open(order[Math.min(order.length - 1, Math.max(0, (p < 0 ? 0 : p) + d))]);
  }

  open(i) {
    const kb = this.kb;
    if (!kb || !(i >= 0 && i < kb.pages.length)) return;
    if (i !== this.cur) this.sel = null;
    this.cur = i;
    this.chatCur = null;
    this.host.setHash(this.target);
    this.renderList();
    this.render();
  }

  renderEmpty() {
    this.chatNodes.clear();
    const $ = this.$;
    for (const id of ["k-name", "k-board-badge", "k-source-badge", "k-switch", "k-note", "k-count"]) $(id).textContent = "";
    for (const id of ["k-list", "k-grid", "k-edit", "k-label", "k-order", "k-facts", "k-rows", "k-chat"]) $(id).replaceChildren();
    $("k-preview").replaceChildren(el("div", { class: "muted", textContent: t("This project declares no naming keyboard.") }));
  }

  renderList() {
    const kb = this.kb;
    const order = pageOrder(kb);
    this.$("k-count").textContent = t("{n} pages in Select order", { n: kb.pages.length });
    this.$("k-list").replaceChildren(...order.map((i, k) => {
      const board = boardOf(kb, i);
      const m = marksOf(kb, i);
      return el("div", { class: `g-row${i === this.cur ? " sel" : ""}`, role: "option", on: { click: () => this.open(i) } },
        el("div", { class: "l1" },
          el("span", { class: "eid", textContent: `${k + 1}. ${kb.pages[i].name}` }),
          k === 0 ? el("span", { class: "mark uses", textContent: t("opens here") }) : null,
          board.base ? el("span", { class: "mark", textContent: t("base ROM keys") }) : null,
          board.rows.some((r) => r.edited) ? el("span", { class: "mark edited", textContent: t("edited") }) : null,
          m.missing ? el("span", { class: "mark err", textContent: t("{n} without glyph", { n: m.missing }) }) : null,
          m.differs ? el("span", { class: "mark warn", textContent: t("{n} printed otherwise", { n: m.differs }) }) : null,
          m.misplaced ? el("span", { class: "mark warn", textContent: t("{n} off the cursor", { n: m.misplaced }) }) : null),
        el("div", { class: "l2", textContent: board.rows.map((r) => r.keys.text ?? "").join(" ").replace(/\s+/g, " ").trim() }),
        el("div", { class: "l3", textContent: t("{n} keys · Select → {next}", { n: m.keys, next: kb.pages[nextPage(kb, i)].name }) }));
    }));
    this.$("k-list").querySelector(".g-row.sel")?.scrollIntoView?.({ block: "nearest" });
  }

  render() {
    const kb = this.kb;
    const i = this.cur;
    if (!kb || i === null) return;
    const $ = this.$;
    const page = kb.pages[i];
    const board = boardOf(kb, i);
    $("k-name").textContent = page.name;
    this.host.reference?.(page.name);
    Object.assign($("k-board-badge"), { textContent: t("keyboard {name}", { name: board.name }), className: "badge" });
    Object.assign($("k-source-badge"), {
      textContent: board.base ? t("keys of the base ROM") : t("keys of the language pack"),
      className: `badge${board.base ? "" : " approved"}`,
    });
    $("k-switch").textContent = t("Select → {next}", { next: kb.pages[nextPage(kb, i)].name });
    this.renderPreview();
    this.renderGrid();
    this.renderEdit();
    this.renderLabel();
    this.renderOrder();
    this.renderFacts();
    this.renderRows();
    this.renderChat();
  }

  renderChat() {
    const box = this.$("k-chat");
    const chat = this.data?.chat;
    box.replaceChildren();
    this.chatNodes.clear();
    if (!chat) return;
    const edits = this.host.keyboardEdits?.();
    const glyphs = this.host.glyphs(chat.style);
    const codec = this.host.codec();
    const widthOf = this.chatWidthOf();
    box.append(el("h2", { textContent: t("Union Room chat") }));
    for (const [p, page] of chat.pages.entries()) {
      const rows = page.rows.map((row, r) => {
        const kept = edits?.row(row.name) ?? row.text;
        const failed = this.chatErrors.get(row.name);
        const label = t("Chat page {page}, row {row}", { page: p + 1, row: r + 1 });
        const input = el("input", { value: failed?.text ?? kept, "aria-label": label, disabled: !edits,
          on: {
            focus: () => {
              this.chatCur = row.name;
              this.host.setHash(this.target);
            },
            keydown: (e) => { if (e.key === "Enter" && edits) this.setChatRow(row, input.value); },
          } });
        const preview = el("div", { class: "k-preview" });
        const status = el("div", { class: "muted" });
        try {
          const result = chatRow(kept, chat, row, codec, widthOf);
          status.textContent = t("{keys} keys · {width}/{limit} px · {bytes}/{capacity} bytes", {
            keys: result.glyphs.length, width: result.width, limit: chat.columns * chat.column_width,
            bytes: result.bytes, capacity: row.bytes,
          });
          if (glyphs) {
            const canvas = el("canvas", { role: "img", "aria-label": `${label}: ${kept}` });
            canvas.width = chat.columns * chat.column_width;
            canvas.height = 16;
            canvas.style.width = `${canvas.width * this.zoom}px`;
            canvas.style.height = `${canvas.height * this.zoom}px`;
            const g = canvas.getContext("2d");
            rect(g, 0, 0, canvas.width, canvas.height, GREY);
            const cache = new Map();
            for (const glyph of result.glyphs) {
              const { pixels } = pixelsOf(glyph, glyphs, codec, cache);
              drawGlyph(g, pixels, glyph.x, 0, glyph.width, { x: 0, y: 0, w: canvas.width, h: canvas.height });
            }
            preview.append(canvas);
          }
        } catch (error) {
          status.className = "error";
          status.setAttribute("role", "alert");
          status.textContent = errorText(error);
        }
        const element = el("tr", { tabindex: "-1", "aria-label": label }, el("th", { textContent: String(r + 1), scope: "row" }),
          el("td", {}, el("div", { class: "k-edit-key" }, input,
            edits ? el("button", { type: "button", class: "btn sm", textContent: t("Apply"),
              on: { click: () => this.setChatRow(row, input.value) } }) : null,
            kept !== row.text ? el("span", { class: "mark edited", textContent: t("edited") }) : null),
          status, failed ? el("div", { class: "error", role: "alert", textContent: errorText(failed.error) }) : null),
          el("td", {}, preview));
        this.chatNodes.set(row.name, { row: element, input });
        return element;
      });
      box.append(el("h3", { textContent: t("Chat page {n}", { n: p + 1 }) }),
        el("div", { class: "k-wrap" }, el("table", { class: "mini k-rows" },
          el("thead", {}, el("tr", {}, ...["Row", "Keys", "Printed row"].map((name) => el("th", { textContent: t(name) })))),
          el("tbody", {}, ...rows))));
    }
  }

  renderPreview() {
    const kb = this.kb;
    const glyphs = this.host.glyphs(kb.style);
    const box = this.$("k-preview");
    const canvas = el("canvas", { class: "k-screen", role: "img",
      "aria-label": `${kb.pages[this.cur].name}: ${t("Screen preview")}` });
    box.replaceChildren(canvas);
    if (!glyphs) {
      this.$("k-note").textContent = "";
      return;
    }
    const { approximate } = drawPage(canvas, kb, this.cur, glyphs, this.host.codec(), this.zoom);
    const note = this.$("k-note");
    note.replaceChildren();
    if (approximate) {
      note.append(t("{n} glyphs are project-drawn stand-ins: the game draws them from your ROM.", { n: approximate }), " ",
        el("button", { type: "button", class: "btn sm", textContent: t("Load your ROM"), on: { click: () => this.host.loadRom() } }));
    } else note.textContent = this.host.exact() ? t("Game font from your ROM.") : t("Language pack glyphs.");
  }

  renderGrid() {
    const kb = this.kb;
    const i = this.cur;
    const glyphs = this.host.glyphs(kb.style);
    const codec = this.host.codec();
    const rows = pageCells(kb, i);
    const head = el("tr", {}, el("th", {}), ...Array.from({ length: Math.max(0, ...rows.map((r) => r.length)) },
      (_, c) => el("th", { textContent: t("column {n}", { n: c + 1 }) })));
    const body = rows.map((cells, r) => el("tr", {}, el("th", { textContent: t("row {n}", { n: r + 1 }) }),
      ...cells.map((c) => {
        const canvas = el("canvas", { class: "k-glyph", "aria-hidden": "true" });
        const code = c.key?.code ? parseInt(c.key.code, 16) : null;
        const shownGlyph = c.glyph ?? (code === null || !glyphs ? null : { ...c.key, width: glyphs.width(code) });
        if (glyphs) drawKey(canvas, kb, i, shownGlyph, glyphs, codec, 2);
        const marks = [];
        if (c.missing) marks.push(el("span", { class: "mark err", textContent: t("no glyph") }));
        if (c.differs) marks.push(el("span", { class: "mark warn", textContent: t("prints {char}", { char: shown(c.glyph.char) }) }));
        if (c.unprinted) marks.push(el("span", { class: "mark warn", textContent: t("not printed") }));
        if (c.misplaced) marks.push(el("span", { class: "mark warn", textContent: t("off the cursor") }));
        const key = c.key;
        const source = key ? t(SOURCES[key.source], { code: key.copy ?? "" }) : "";
        const frame = c.cursor ? t("cursor frame x {from}–{to}", { from: c.cursor[0], to: c.cursor[1] - 1 }) : "";
        const title = [source, frame, c.reachable ? "" : t("outside the columns the cursor reaches")].filter(Boolean)
          .join(" · ");
        const was = this.baseKb.keyboards[kb.pages[i].keyboard].rows[r]?.cells[c.column]?.key?.char; // pages may move
        if (key && was !== undefined && key.char !== was) marks.push(el("span", { class: "mark edited", textContent: t("edited") }));
        const editable = this.editable(boardOf(kb, i), c);
        const chosen = this.sel?.row === r && this.sel?.column === c.column;
        return el("td", { class: `k-cell${c.reachable ? "" : " off"}${editable ? " edit" : ""}${chosen ? " sel" : ""}`, title,
          on: editable ? { click: () => this.select(r, c.column) } : {} },
          canvas,
          el("div", { class: "k-char", textContent: key ? shown(key.char) : "—" }),
          el("div", { class: "k-code", textContent: key?.code ? `0x${key.code}` : t("no code") }),
          c.glyph ? el("div", { class: "k-pos" }, el("div", { textContent: t("x {x}", { x: c.glyph.x }) }),
            el("div", { textContent: t("tile {tx},{ty}", { tx: c.tile[0], ty: c.tile[1] }) }))
            : el("div", { class: "k-pos", textContent: t("not printed") }),
          el("div", { class: "k-src", textContent: source }),
          marks.length ? el("div", { class: "k-marks" }, ...marks) : null);
      })));
    this.$("k-grid").replaceChildren(el("table", { class: "k-table" }, el("thead", {}, head), el("tbody", {}, ...body)));
  }

  /** The selected key's editor and the browser's keyboard edits. */
  renderEdit() {
    const box = this.$("k-edit");
    const edits = this.host.keyboardEdits?.();
    if (!edits) {
      box.replaceChildren();
      return;
    }
    const kb = this.kb;
    const board = boardOf(kb, this.cur);
    const parts = [];
    const cell = this.sel && pageCells(kb, this.cur)[this.sel.row]?.[this.sel.column];
    if (cell && this.editable(board, cell)) {
      const input = el("input", { class: "k-char-input", value: cell.key.char,
        title: t("Character"), on: { keydown: (e) => {
          if (e.key === "Enter") this.setKey(e.target.value);
        } } });
      parts.push(el("div", { class: "k-edit-key" },
        el("span", { textContent: t("Row {row}, column {column} enters", { row: this.sel.row + 1, column: this.sel.column + 1 }) }),
        input,
        el("button", { type: "button", class: "btn sm", textContent: t("Apply"), on: { click: () => this.setKey(input.value) } })));
      if (this.error) parts.push(el("div", { class: "error", textContent: errorText(this.error) }));
    } else if (!board.base) {
      parts.push(el("div", { class: "muted", textContent: t("Select a key to change the character it enters.") }));
    }
    if (edits.size) {
      parts.push(el("div", { class: "k-edit-state" },
        el("span", { textContent: t("{n} keyboard changes kept in this browser · included in Export", { n: edits.size }) }),
        el("button", { type: "button", class: "btn sm", textContent: t("Discard keyboard changes"),
          on: { click: () => this.discard() } })));
    }
    box.replaceChildren(...parts);
  }

  /** Why the label cannot be drawn: message, "rom" or null. */
  labelBlocked(gid) {
    const row = this.host.index()?.graphics?.inventory?.find((r) => r.id === gid);
    if (!row) return t("The graphics inventory lists no {id}.", { id: gid });
    if (!row.rom_offset) return t("The inventory traces no ROM source for this graphic: it has no pixels to show.");
    const st = this.host.gfxState();
    if (st === "loading") return t("Loading the gen3tl bundle in Pyodide…");
    if (st !== "ready") return t("The graphics need the gen3tl bundle in Pyodide, which is unavailable: {state}", { state: st });
    return this.host.gfx()?.rom ? null : "rom";
  }

  /** The page's label graphic as the page-swap button shows it. */
  renderLabel() {
    const request = this.labelRequest = (this.labelRequest ?? 0) + 1;
    const kb = this.kb;
    const i = this.cur;
    const box = this.$("k-label");
    const gid = labelOf(kb, i);
    if (!gid) {
      box.replaceChildren(el("div", { class: "muted", textContent: t("The profile names no label graphic for this page.") }));
      return;
    }
    const edited = Boolean(this.host.index()?.graphics?.edits?.[gid]);
    const head = el("div", { class: "k-label-head" },
      el("a", { class: "fam-link", textContent: gid, title: t("Open {id}", { id: gid }),
        on: { click: () => this.host.openPicture(gid) } }),
      el("span", { class: `badge${edited ? " approved" : ""}`,
        textContent: edited ? t("translated in the project") : t("the ROM's label") }));
    const where = el("div", { class: "muted",
      textContent: t("Shown on the Select button of {page}", { page: kb.pages[previousPage(kb, i)].name }) });
    const why = this.labelBlocked(gid);
    if (why === "rom") {
      box.replaceChildren(head, el("div", { class: "muted" },
        `${t("Load your ROM to see the label: its pixels come from your ROM, which stays in this browser.")} `,
        el("button", { type: "button", class: "btn sm", textContent: t("Load your ROM"), on: { click: () => this.host.loadRom() } })),
      where);
      return;
    }
    if (why) {
      box.replaceChildren(head, el("div", { class: "muted", textContent: why }), where);
      return;
    }
    const show = (views) => {
      if (request !== this.labelRequest) return;
      const [view] = views;
      if (!view) throw new Error(t("no screen layout"));
      const canvas = el("canvas", { class: "k-label-img", title: `${gid}: ${view.label}`,
        role: "img", "aria-label": `${gid}: ${view.label}` });
      drawSheet(canvas, view, view.palette, this.zoom);
      box.replaceChildren(head, canvas, where);
    };
    const failed = (e) => {
      if (request !== this.labelRequest) return;
      console.error(e);
      box.replaceChildren(head, el("div", { class: "error", textContent: t("Label: {error}", { error: e.message || e }) }), where);
    };
    try {
      const result = this.host.gfx().preview(gid, edited);
      if (result?.then) result.then(show).catch(failed);
      else show(result);
    } catch (e) {
      failed(e);
    }
  }

  renderOrder() {
    const kb = this.kb;
    const order = pageOrder(kb);
    const edit = Boolean(this.host.keyboardEdits?.());
    const move = (i, d, label, off) => el("button", { type: "button", class: "btn sm", textContent: label, disabled: off,
      title: d < 0 ? t("Earlier in Select order") : t("Later in Select order"), on: { click: () => this.movePage(i, d) } });
    this.$("k-order").replaceChildren(...order.map((i, k) => el("li", { class: i === this.cur ? "sel" : "" },
      el("a", { class: "fam-link", textContent: kb.pages[i].name, on: { click: () => this.open(i) } }),
      k === 0 ? el("span", { class: "muted", textContent: ` · ${t("opens here")}` }) : null,
      edit ? el("span", { class: "k-move" }, move(i, -1, "↑", k === 0), move(i, 1, "↓", k === order.length - 1)) : null)));
    if (this.moveError) this.$("k-order").append(el("li", { class: "error", role: "alert",
      textContent: errorText(this.moveError) }));
  }

  renderFacts() {
    const kb = this.kb;
    const i = this.cur;
    const page = kb.pages[i];
    const board = boardOf(kb, i);
    const order = pageOrder(kb);
    const facts = [
      [N_("Place in Select order"), t("{n} of {total}", { n: order.indexOf(i) + 1, total: order.length })],
      [N_("Page index"), String(i)],
      [N_("Keyboard"), `${board.name} (${page.keyboard})`],
      [N_("Base keyboard"), String(board.like)],
      [N_("Look"), t("base page {n}", { n: page.look })],
      [N_("Columns"), board.columns == null ? t("not measured") : String(board.columns)],
      [N_("Cursor columns"), board.column_x ? board.column_x.map((x) => t("x {x}", { x })).join(", ") : t("not measured")],
      [N_("Rows"), String(board.rows.length)],
      [N_("Next page"), kb.pages[nextPage(kb, i)].name],
    ];
    const dl = this.$("k-facts");
    dl.replaceChildren();
    for (const [k, v] of facts) dl.append(el("dt", { textContent: t(k) }), el("dd", { textContent: v }));
  }

  renderRows() {
    const kb = this.kb;
    const board = boardOf(kb, this.cur);
    const link = (part) => (part.id
      ? el("a", { class: "fam-link", textContent: part.id, title: t("Open {id}", { id: part.id }),
        on: { click: () => this.host.openEntry(part.id) } })
      : el("span", { class: "muted", textContent: t("no entry") }));
    const rows = board.rows.map((row, r) => el("tr", {},
      el("th", { textContent: String(r + 1) }),
      el("td", {}, link(row.keys), el("div", { class: "k-text", textContent: row.keys.text ?? "" })),
      el("td", {}, link(row.display), el("div", { class: "k-text", textContent: row.display.text ?? "" }))));
    this.$("k-rows").replaceChildren(el("table", { class: "mini k-rows" },
      el("thead", {}, el("tr", {}, el("th", { textContent: t("Row") }), el("th", { textContent: t("Keys") }),
        el("th", { textContent: t("Printed row") }))),
      el("tbody", {}, ...rows)));
  }

  wire() {
    const $ = this.$;
    $("k-prev").addEventListener("click", () => this.step(-1));
    $("k-next").addEventListener("click", () => this.step(1));
    $("k-zoom").addEventListener("change", (e) => {
      this.zoom = Number(e.target.value);
      this.refresh();
    });
  }
}
