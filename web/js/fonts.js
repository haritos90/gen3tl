// Review every glyph sheet the translation installs.
// Pack art exact, ROM glyphs from the user's ROM;
// the pack's glyphs redrawn as browser drafts.

import { BACKGROUND, Glyphs, INK, Measure, OUTSIDE, SHADOW, decodeGlyph, glyphSize, runs, splitTokens } from "./font.js";
import { el } from "./dom.js";
import { standIn, standInName } from "./standin.js";
import { N_, formatNumber, t } from "./ui.js";

const OTHER = 4; // sprite palette indices besides ink, outline and background
export const SOURCES = {
  base: N_("ROM glyph"), added: N_("added by the pack"), copied: N_("copied by the pack"),
  changed: N_("changed by the pack"), alias: N_("shares a tile"),
};
// Short labels of the cell marks
const MARKS = { added: N_("added"), copied: N_("copied"), changed: N_("changed"), alias: N_("shared tile") };
const PACK = new Set(["added", "copied", "changed"]);
const KINDS = { font: N_("Font"), sprite: N_("Sprite font") };
const COLORS = { [INK]: "#303038", [SHADOW]: "#b0b0c0", [BACKGROUND]: "#f8f8f8", [OTHER]: "#8890a0" };
const OUTSIDE_FIELD = "#c8ccd4"; // beyond the glyph's cell
const SPRITE_COLORS = { [INK]: "#f0f0f0", [SHADOW]: "#383840", [OTHER]: "#c8b048" };
const SPRITE_FIELD = "#6878a0"; // field behind sprite text
const OUTLINES = { [SHADOW]: SPRITE_COLORS[SHADOW], [OTHER]: SPRITE_COLORS[OTHER] };
const LIMIT = "#e02020";
// Same tile sheet layout as the toolkit's image
export const SHEET_COLUMNS = 16;
export const SHEET_GAP = 2;
export const SHEET_ZOOM = 3;
// The glyph editor: art characters and px per pixel
export const ART_PIXELS = ["#", "s", "_"];
export const EDIT_ZOOM = { font: 16, sprite: 24 };
const GRID_LINE = "#9098a8";

export const sheetsOf = (index) => index?.font_sheets?.sheets ?? [];
export const isPack = (g) => PACK.has(g.source);
export const hex = (n, digits = 2) => n.toString(16).toUpperCase().padStart(digits, "0");

/** The label of a glyph: its character, else its name. */
export function glyphLabel(g) {
  if (!g) return "";
  if (g.char !== undefined) return g.char === " " ? "␣" : g.char;
  return g.name ? `{${g.name}}` : "";
}

/** Count a sheet's glyphs, its pack glyphs among them. */
export function sheetCounts(sheet) {
  return { glyphs: sheet.glyphs.length, pack: sheet.glyphs.filter(isPack).length };
}

/** The cells a sheet shows for a filter. */
export function cellsOf(sheet, filter = "") {
  if (filter === "pack") return sheet.glyphs.filter(isPack).map((g) => ({ tile: g.tile, glyph: g, codes: [g] }));
  if (filter !== "tiles") return sheet.glyphs.map((g) => ({ tile: g.tile, glyph: g, codes: [g] }));
  const by = new Map();
  for (const g of sheet.glyphs) {
    if (!by.has(g.tile)) by.set(g.tile, []);
    by.get(g.tile).push(g);
  }
  return Array.from({ length: sheet.count }, (_, tile) => {
    const codes = by.get(tile) ?? [];
    const named = codes.find((g) => g.source === "base") ?? codes.find((g) => g.source !== "alias");
    return { tile, glyph: named ?? codes[0] ?? null, codes };
  });
}

/** The default sample: the pack's capitals, then its other characters. */
export function sampleText(charmap, codec = null) {
  let chars = Object.keys(charmap ?? {});
  if (!chars.length && codec) chars = [...codec.chars.keys()].filter((c) => /\p{L}/u.test(c));
  const upper = chars.filter((c) => c !== c.toLowerCase());
  const rest = chars.filter((c) => c === c.toLowerCase());
  return [upper.join(""), rest.join("")].filter(Boolean).join("\n");
}

/** A sheet's own ROM tables, read at its offsets. */
export function romSheet(sheet, rom) {
  if (!rom) return null;
  if (sheet.kind === "sprite") return { tiles: rom.slice(sheet.rom.sheet, sheet.rom.sheet + sheet.tiles * 32) };
  const size = glyphSize(sheet.format);
  return {
    widths: rom.slice(sheet.rom.widths, sheet.rom.widths + Math.max(sheet.count, 256)),
    glyphs: { format: sheet.format, size, glyphs: rom.slice(sheet.rom.glyphs, sheet.rom.glyphs + sheet.count * size) },
  };
}

/** The ROM's glyph and width at code `b`. */
export function romGlyph(own, b) {
  const { format, size, glyphs } = own.glyphs;
  return { pixels: decodeGlyph(glyphs.subarray(b * size, (b + 1) * size), format), width: own.widths[b] };
}

// The check's shadow end: no ink in the last column
const shadowEnd = (d) => d.width > 0 && d.rows.every((r) => r[d.width - 1] !== "#");
// A drawn sprite letter steps its width less shared columns
const spriteStep = (width, shared) => Math.max(width - shared, 1);

/** A font style with `drafts` drawn over its art. */
export function draftStyle(style, art, drafts = []) {
  if (!drafts.length) return { style, art };
  const s = { ...style, pack: { ...style.pack }, copy: { ...style.copy } };
  const a = { ...art, art: { ...art?.art } };
  const shadow = new Set(style.shadow ?? []);
  for (const d of drafts) {
    [s.pack[d.code], a.art[d.code]] = [d.width, d.rows];
    delete s.copy[d.code];
    if (shadowEnd(d)) shadow.add(d.code);
    else shadow.delete(d.code);
  }
  s.shadow = [...shadow].sort();
  return { style: s, art: a };
}

/** The index with `drafts` over the pack's art. */
export function draftFonts(index, drafts = []) {
  if (!drafts.length || !index?.fonts) return index;
  const styles = { ...index.fonts.styles };
  const glyphs = { ...index.glyphs };
  for (const name of Object.keys(styles)) {
    const mine = drafts.filter((d) => d.style === name);
    if (mine.length) ({ style: styles[name], art: glyphs[name] } = draftStyle(styles[name], glyphs[name], mine));
  }
  const fonts = { ...index.fonts, styles };
  if (index.fonts.sprites) {
    fonts.sprites = { ...index.fonts.sprites };
    for (const sheet of sheetsOf(index)) {
      const m = fonts.sprites[sheet.name];
      if (sheet.kind !== "sprite" || !m) continue;
      const mine = drafts.filter((d) => d.style === sheet.style);
      if (!mine.length) continue;
      const planned = draftSheet({ ...sheet, ...(m.steps ? { advances: m.steps } : {}) }, mine, m.shared ?? 0);
      fonts.sprites[sheet.name] = { ...m, table: planned.table, ...(m.steps ? { steps: planned.advances } : {}) };
    }
  }
  return { ...index, fonts, glyphs };
}

/** Glyphs per font style: pack art, ROM widths and glyphs. */
export function styleGlyphs(index, latin = null, rom = null) {
  return Object.fromEntries(Object.entries(index.fonts.styles).map(([style, f]) =>
    [style, new Glyphs(f, index.glyphs?.[style], latin?.[style] ?? null, rom?.[style] ?? null)]));
}

/** The glyph source of a font sheet. */
export function fontGlyphs(sheet, index, rom = null, drafts = []) {
  const { style, art } = draftStyle(index.fonts.styles[sheet.style], index.glyphs?.[sheet.style], drafts);
  const own = romSheet(sheet, rom);
  return new Glyphs({ ...style, format: sheet.format, rom: { ...style.rom, count: sheet.count } },
    art, own?.widths ?? null, own?.glyphs ?? null);
}

/** A sprite sheet with `drafts` over its pack tiles. */
export function draftSheet(sheet, drafts = [], shared = 0) {
  if (sheet.kind !== "sprite" || !drafts.length) return sheet;
  const by = new Map(drafts.map((d) => [d.code, d]));
  const step = (width) => (sheet.advances ? spriteStep(width, shared) : sheet.advance);
  const art = {};
  const table = [...sheet.table];
  const advances = sheet.advances ? [...sheet.advances] : null;
  const planned = new Map();
  let count = sheet.tiles;
  for (const g of [...sheet.glyphs].sort((a, b) => parseInt(a.code, 16) - parseInt(b.code, 16))) {
    const d = by.get(g.code);
    if (g.source !== "added" && !(g.source === "copied" && d)) continue;
    const tile = count++;
    const width = d ? step(d.width) : g.width;
    art[String(tile)] = d?.rows ?? sheet.art[String(g.tile)];
    table[parseInt(g.code, 16)] = tile;
    if (advances) advances[parseInt(g.code, 16)] = width;
    const { from, ...drawn } = g;
    planned.set(g.code, { ...drawn, source: "added", tile, width });
  }
  const glyphs = sheet.glyphs.map((g) => planned.get(g.code) ?? g);
  return { ...sheet, count, table, art, glyphs, ...(advances ? { advances } : {}) };
}

/** Glyph pixels as art rows of `cell` from `top`. */
function pixelRows(pixels, top, cell, width) {
  return Array.from({ length: cell }, (_, r) => Array.from({ length: width },
    (_, x) => ({ [INK]: "#", [SHADOW]: "s" })[pixels[(top + r) * 16 + x]] ?? "_").join(""));
}

/** Editable art starts from pack pixels, ROM pixels or blanks. */
export function glyphArt(index, sheet, g, own = null, draft = null) {
  if (!g) return null;
  let rows = null;
  let where = { top: 0, cell: 8, widest: 8 };
  let copy = {};
  if (sheet.kind === "sprite") {
    where.cell = index?.font_sheets?.styles?.[sheet.style]?.rows ?? 8;
    if (g.source === "added") rows = sheet.art?.[String(g.tile)];
    else if (g.source === "copied") {
      copy = { copy: g.from };
      if (own) {
        const pixels = romTile(own.tiles.subarray(g.tile * 32, g.tile * 32 + 32), sheet.palette);
        rows = Array.from({ length: where.cell }, (_, y) => Array.from({ length: 8 }, (_, x) =>
          ({ [INK]: "#", [SHADOW]: "s" })[pixels[y * 8 + x]] ?? "_").join(""));
      } else rows = draft?.rows;
    }
  } else if (g.source === "added" || g.source === "changed") {
    const art = index?.glyphs?.[sheet.style];
    rows = art?.art?.[g.code];
    where = { top: art?.top, cell: art?.rows, widest: sheet.format === "8x16" ? 8 : 16 };
  } else if (g.source === "copied") {
    const cell = index?.glyphs?.[sheet.style] ?? index?.font_sheets?.styles?.[sheet.style];
    where = { top: cell?.top ?? 0, cell: cell?.rows ?? 16, widest: sheet.format === "8x16" ? 8 : 16 };
    copy = { copy: g.from };
    if (own) {
      const rom = romGlyph(own, parseInt(g.from, 16));
      rows = pixelRows(rom.pixels, where.top, where.cell, Math.min(Math.max(rom.width, 1), where.widest));
    } else rows = draft?.rows;
  } else if (g.source === "base" && (g.name || g.redraw)) {
    const cell = index?.glyphs?.[sheet.style] ?? index?.font_sheets?.styles?.[sheet.style];
    where = { top: cell?.top ?? 0, cell: cell?.rows ?? 16, widest: sheet.format === "8x16" ? 8 : 16 };
    copy = { source: "rom" };
    if (own) {
      const rom = romGlyph(own, g.tile);
      rows = pixelRows(rom.pixels, where.top, where.cell, Math.min(Math.max(rom.width || 8, 1), where.widest));
    } else rows = draft?.rows ?? Array(where.cell).fill("_".repeat(Math.min(8, where.widest)));
  }
  if (!rows?.length) return null;
  const key = g.source === "changed" || g.name || g.redraw ? { slot: g.code.padStart(3, "0") } : { char: g.char };
  return { id: `${sheet.style}:${g.code}`, style: sheet.style, code: g.code, ...key, ...copy, ...where,
    width: rows[0].length, rows: [...rows] };
}

/** Art with pixel (x, y) set; null if unchanged. */
export function paintArt(art, x, y, ch) {
  const r = y - art.top;
  if (r < 0 || r >= art.cell || x < 0 || x >= art.width || art.rows[r][x] === ch) return null;
  return { ...art, rows: art.rows.map((row, i) => (i === r ? row.slice(0, x) + ch + row.slice(x + 1) : row)) };
}

/** Art at another width; cut columns are dropped. */
export function resizeArt(art, width) {
  const w = Math.max(1, Math.min(art.widest, Math.round(width) || 1));
  return { ...art, width: w, rows: art.rows.map((row) => (row.length < w ? row.padEnd(w, "_") : row.slice(0, w))) };
}

/** The colour of each art character in the editor's grid. */
export function artColors(sprite) {
  return sprite ? { "#": SPRITE_COLORS[INK], s: SPRITE_COLORS[SHADOW], _: SPRITE_FIELD }
    : { "#": COLORS[INK], s: COLORS[SHADOW], _: COLORS[BACKGROUND] };
}

/** Draw art on its cell, one square per pixel. */
export function drawArt(canvas, art, sprite, zoom) {
  const high = sprite ? 8 : 16;
  canvas.width = art.widest * zoom + 1;
  canvas.height = high * zoom + 1;
  const g = canvas.getContext("2d");
  rect(g, 0, 0, canvas.width, canvas.height, GRID_LINE);
  const colors = artColors(sprite);
  for (let y = 0; y < high; y++) {
    const r = y - art.top;
    for (let x = 0; x < art.widest; x++) {
      const inside = r >= 0 && r < art.cell && x < art.width;
      rect(g, x * zoom + 1, y * zoom + 1, zoom - 1, zoom - 1, inside ? colors[art.rows[r][x]] : OUTSIDE_FIELD);
    }
  }
  rect(g, art.width * zoom, 0, 1, canvas.height, LIMIT);
}

/** Pack art rows as 8x8 sprite values. */
export function artTile(rows) {
  const px = new Uint8Array(64);
  rows.slice(0, 8).forEach((row, y) => {
    for (let x = 0; x < Math.min(8, row.length); x++) px[y * 8 + x] = { "#": INK, s: SHADOW }[row[x]] ?? OUTSIDE;
  });
  return px;
}

/** 4bpp tile bytes as 8x8 sprite values. */
export function romTile(bytes, palette) {
  const px = new Uint8Array(64);
  for (let i = 0; i < 64; i++) {
    const v = (bytes[i >> 1] >> ((i & 1) * 4)) & 15;
    px[i] = v === palette.ink ? INK : v === palette.shadow ? SHADOW : v === palette.background ? OUTSIDE : OTHER;
  }
  return px;
}

/** A stand-in letter as an outlined 8x8 sprite tile. */
export function spriteStandIn(name, aliases = {}) {
  const big = standIn(name, 4, true, aliases);
  const px = new Uint8Array(64);
  for (let y = 0; y < 8; y++) {
    for (let x = 0; x < 3; x++) if (big[(y + 3) * 16 + x] === INK) px[y * 8 + x + 1] = INK;
  }
  for (let y = 0; y < 8; y++) {
    for (let x = 0; x < 8; x++) {
      if (px[y * 8 + x] === INK) continue;
      const near = [-1, 0, 1].some((dy) => [-1, 0, 1].some((dx) => {
        const [a, b] = [x + dx, y + dy];
        return a >= 0 && a < 8 && b >= 0 && b < 8 && px[b * 8 + a] === INK;
      }));
      if (near) px[y * 8 + x] = SHADOW;
    }
  }
  return px;
}

/** The tiles of a sprite sheet, with stand-ins. */
export class SpriteTiles {
  constructor(sheet, rom = null, aliases = {}) {
    this.aliases = aliases;
    this.sheet = sheet;
    this.rom = romSheet(sheet, rom);
    this.cache = new Map();
    this.names = new Map();
    for (const g of sheet.glyphs) {
      if (g.source === "base" && !this.names.has(g.tile)) this.names.set(g.tile, g.char);
    }
  }

  /** {pixels, exact} of a tile; pixels null when unknown. */
  get(tile) {
    if (!this.cache.has(tile)) {
      const art = this.sheet.art[String(tile)];
      let out = { pixels: null, exact: false };
      if (art) out = { pixels: artTile(art), exact: true };
      else if (this.rom && tile < this.sheet.tiles) {
        out = { pixels: romTile(this.rom.tiles.subarray(tile * 32, tile * 32 + 32), this.sheet.palette), exact: true };
      } else if (this.names.has(tile)) out = { pixels: spriteStandIn(this.names.get(tile), this.aliases), exact: false };
      this.cache.set(tile, out);
    }
    return this.cache.get(tile);
  }
}

/** Where each glyph of a sample line goes. */
export function fontRuns(line, glyphs, codec, small = false) {
  const measure = new Measure((b) => glyphs.width(b), codec, {}, 0);
  let x = 0;
  let unknown = 0;
  const out = [];
  for (const r of runs(line, measure)) {
    if (r.kind === "glyph") {
      const g = glyphs.get(r.byte);
      let pixels = g.pixels;
      if (!g.exact) unknown++;
      if (!pixels) pixels = standIn(standInName(r.text, r.byte, codec), r.width, small, codec.standIns);
      out.push({ x, width: r.width, pixels, exact: g.exact });
    } else if (r.kind === "bad") out.push({ x, width: r.width, pixels: null, bad: true });
    x += r.width || 0;
  }
  return { width: x, advance: x, runs: out, unknown };
}

/** Where each tile of a sample line goes. */
export function spriteRuns(line, tiles, codec) {
  const sheet = tiles.sheet;
  const bytes = [];
  for (const tok of splitTokens(line)) {
    let b;
    try {
      b = codec.encode(tok);
    } catch {
      b = null;
    }
    if (tok.length > 1 && tok.startsWith("{") && b?.length !== 1) continue; // codes draw nothing here
    bytes.push(b && b.length === 1 ? b[0] : null);
  }
  const stepped = Boolean(sheet.advances) && bytes.some((b) => b !== null && sheet.table[b] >= sheet.tiles);
  const step = (b) => (stepped && b !== null ? sheet.advances[b] : sheet.advance);
  const out = [];
  let unknown = 0;
  let x = 0;
  for (const b of bytes) {
    if (b === null) out.push({ x, width: sheet.advance, pixels: null, bad: true });
    else {
      const tile = sheet.table[b];
      const g = tiles.get(tile);
      if (!g.exact) unknown++;
      out.push({ x, width: 8, tile, pixels: g.pixels, exact: g.exact });
    }
    x += step(b);
  }
  const width = out.length ? out[out.length - 1].x + 8 : 0;
  return { width, advance: x, runs: out, unknown };
}

function rect(g, x, y, w, h, color) {
  g.fillStyle = color;
  g.fillRect(x, y, w, h);
}

/** Paint pixel values at a zoom. */
function paint(g, px, ox, oy, zoom, colors, side = 16, width = side) {
  if (!px) return;
  const rows = px.length / side;
  for (let y = 0; y < rows; y++) {
    for (let x = 0; x < Math.min(width, side); x++) {
      const c = colors[px[y * side + x]];
      if (c) rect(g, ox + x * zoom, oy + y * zoom, zoom, zoom, c);
    }
  }
}

/** Draw sample lines; returns stand-in count and line widths. */
export function drawSample(canvas, sheet, lines, source, codec, zoom) {
  const sprite = sheet.kind === "sprite";
  const rowHeight = sprite ? 10 : 16;
  const laid = lines.map((l) => (sprite ? spriteRuns(l, source, codec)
    : fontRuns(l, source, codec, sheet.format === "8x16")));
  const width = Math.max(1, ...laid.map((l) => l.width)) + 8;
  canvas.width = width * zoom;
  canvas.height = (laid.length * rowHeight + 4) * zoom;
  const g = canvas.getContext("2d");
  rect(g, 0, 0, canvas.width, canvas.height, sprite ? SPRITE_FIELD : COLORS[BACKGROUND]);
  laid.forEach((l, i) => {
    const y = (2 + i * rowHeight + (sprite ? 1 : 0)) * zoom;
    for (const r of l.runs) {
      const x = (4 + r.x) * zoom;
      if (r.bad) rect(g, x, y + 2 * zoom, (r.width - 1) * zoom, (rowHeight - 5) * zoom, LIMIT);
      else if (sprite) paint(g, r.pixels, x, y, zoom, OUTLINES, 8);
      else paint(g, r.pixels, x, y, zoom, { [INK]: COLORS[INK], [SHADOW]: COLORS[SHADOW] }, 16, r.width);
    }
    if (!sprite) return;
    for (const r of l.runs) { // ink over neighbouring outlines, as the HUD draws
      if (!r.bad) paint(g, r.pixels, (4 + r.x) * zoom, y, zoom, { [INK]: SPRITE_COLORS[INK] }, 8);
    }
  });
  return { unknown: laid.reduce((n, l) => n + l.unknown, 0), widths: laid.map((l) => l.advance) };
}

/** Size of a sheet and each tile's corner. */
export function sheetLayout(count, columns = SHEET_COLUMNS) {
  const pitch = 8 + SHEET_GAP;
  const at = Array.from({ length: count }, (_, t) => [SHEET_GAP + (t % columns) * pitch,
    SHEET_GAP + Math.floor(t / columns) * pitch]);
  return { width: SHEET_GAP + Math.min(count, columns) * pitch, height: SHEET_GAP + Math.ceil(count / columns) * pitch, at };
}

/** The frame colour of the shown theme. */
const frameColour = () => getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();

/** Draw a sprite sheet in tile order. */
export function drawSheet(canvas, tiles, zoom = SHEET_ZOOM, mark = frameColour()) {
  const sheet = tiles.sheet;
  const { width, height, at } = sheetLayout(sheet.count);
  canvas.width = width * zoom;
  canvas.height = height * zoom;
  const g = canvas.getContext("2d");
  rect(g, 0, 0, canvas.width, canvas.height, OUTSIDE_FIELD);
  let unknown = 0;
  at.forEach(([x, y], tile) => {
    if (tile >= sheet.tiles) rect(g, (x - 1) * zoom, (y - 1) * zoom, 10 * zoom, 10 * zoom, mark);
    rect(g, x * zoom, y * zoom, 8 * zoom, 8 * zoom, SPRITE_FIELD);
    const t = tiles.get(tile);
    if (!t.exact) unknown++;
    paint(g, t.pixels, x * zoom, y * zoom, zoom, SPRITE_COLORS, 8);
  });
  return unknown;
}

/** Draw one cell: glyph, width mark; true when exact. */
export function drawCell(canvas, sheet, cell, source, codec, zoom = 3, rows = { top: 0, rows: 16 }) {
  const sprite = sheet.kind === "sprite";
  const side = sprite ? 8 : 16;
  const wide = sheet.format === "8x16" ? 8 : side;
  canvas.width = wide * zoom;
  canvas.height = (sprite ? 8 : 16) * zoom;
  const g = canvas.getContext("2d");
  if (sprite) {
    const tile = source.get(cell.tile);
    rect(g, 0, 0, canvas.width, canvas.height, SPRITE_FIELD);
    paint(g, tile.pixels, 0, 0, zoom, SPRITE_COLORS, 8);
    rect(g, (cell.glyph?.width ?? sheet.advance) * zoom, 0, 1, canvas.height, LIMIT);
    return tile.exact;
  }
  const b = cell.tile;
  const glyph = source.get(b);
  const width = source.width(b);
  let px = glyph.pixels;
  rect(g, 0, 0, canvas.width, canvas.height, OUTSIDE_FIELD);
  if (!px && cell.glyph) {
    px = standIn(standInName(cell.glyph.char ?? glyphLabel(cell.glyph), b, codec), width, sheet.format === "8x16", codec.standIns);
    rect(g, 0, rows.top * zoom, Math.min(width, wide) * zoom, rows.rows * zoom, COLORS[BACKGROUND]);
  }
  paintGlyph(g, px, width, wide, zoom);
  return glyph.exact;
}

function paintGlyph(g, px, width, wide, zoom) {
  paint(g, px, 0, 0, zoom, COLORS, 16, wide);
  if (width < wide) rect(g, width * zoom, 0, 1, 16 * zoom, LIMIT);
}

/** Draw a ROM font glyph as drawCell draws a cell. */
export function drawRomGlyph(canvas, sheet, glyph, zoom = 3) {
  const wide = sheet.format === "8x16" ? 8 : 16;
  canvas.width = wide * zoom;
  canvas.height = 16 * zoom;
  const g = canvas.getContext("2d");
  rect(g, 0, 0, canvas.width, canvas.height, OUTSIDE_FIELD);
  paintGlyph(g, glyph.pixels, glyph.width, wide, zoom);
}

/** The Fonts view: sheet list, sample and glyph grid. */
export class FontsView {
  constructor(host, $) {
    this.host = host;
    this.$ = $;
    this.cur = null;
    this.zoom = 2;
    this.sources = new Map();
    this.sample = null;
    this.editing = null;
    this.ink = "#";
    this.cells = new Map();
    this.glyphCards = new Map();
  }

  get sheets() {
    return sheetsOf(this.host.index());
  }

  sheet(name) {
    return this.sheets.find((s) => s.name === name) ?? null;
  }

  glyph(name, code) {
    return this.sheet(name)?.glyphs.find((g) => g.code === code) ?? null;
  }

  get address() {
    if (this.cur === null) return [];
    return this.editing === null ? [this.cur] : [this.cur, "glyph", this.editing];
  }

  /** The browser drafts of a style. */
  drafts(style) {
    return this.host.edits?.()?.of(style) ?? [];
  }

  /** A sheet as shown: its data with its style's drafts. */
  shown(name) {
    const s = this.sheet(name);
    return s && draftSheet(s, this.drafts(s.style), this.host.index()?.fonts?.sprites?.[s.name]?.shared ?? 0);
  }

  /** Load the language's sheets; open `want` or the first. */
  reset(want = null, select = true) {
    this.host.reference?.(null);
    this.generation = (this.generation ?? 0) + 1;
    this.sources.clear();
    this.sample = null;
    this.cur = null;
    this.editing = null;
    const first = this.sheet(want)?.name ?? this.sheets[0]?.name;
    this.renderList();
    if (select && first) this.open(first);
    else this.renderEmpty();
  }

  /** Redraw after the ROM changes. */
  refresh() {
    this.sources.clear();
    if (this.generation === undefined) return;
    this.renderList();
    if (this.cur) this.render();
  }

  /** The glyph source of a sheet for the current ROM. */
  source(sheet) {
    if (!this.sources.has(sheet.name)) {
      const rom = this.host.rom();
      this.sources.set(sheet.name, sheet.kind === "sprite" ? new SpriteTiles(sheet, rom, this.host.index()?.language_rules?.stand_ins)
        : fontGlyphs(sheet, this.host.index(), rom, this.drafts(sheet.style)));
    }
    return this.sources.get(sheet.name);
  }

  renderList() {
    const $ = this.$;
    const sheets = this.sheets;
    $("fo-count").textContent = sheets.length ? t("{n} glyph sheets", { n: formatNumber(sheets.length) })
      : t("The editor data has no font sheets. Generate it again with gen3tl web data --split.");
    $("fo-list").replaceChildren(...sheets.map((raw) => {
      const s = this.shown(raw.name);
      const n = sheetCounts(s);
      return el("div", { class: `g-row${s.name === this.cur ? " sel" : ""}`, role: "option",
        on: { click: () => this.open(s.name) } },
      el("div", { class: "l1" },
        el("span", { class: "eid", textContent: s.name }),
        el("span", { class: `mark kind-${s.kind}`, textContent: t(KINDS[s.kind]) }),
        el("span", { class: "muted", textContent: s.style })),
      el("div", { class: "l2", textContent: t("{n} glyphs, {pack} from the pack", { n: n.glyphs, pack: n.pack }) }),
      el("div", { class: "l3", textContent: s.kind === "font"
        ? t("{format} glyphs; font ids {codes}", { format: s.format, codes: s.codes.join(", ") || "—" })
        : t("{tiles} ROM tiles and {added} pack tiles", { tiles: s.tiles, added: s.count - s.tiles }) }));
    }));
  }

  renderEmpty() {
    const $ = this.$;
    this.glyphCards.clear();
    for (const id of ["fo-name", "fo-kind", "fo-style"]) $(id).textContent = "";
    for (const id of ["fo-facts", "fo-grid", "fo-table", "fo-sample-note", "fo-note"]) $(id).replaceChildren();
    $("fo-table-block").hidden = true;
    $("fo-sheet-block").hidden = true;
    $("fo-edit-block").hidden = true;
  }

  step(d) {
    const names = this.sheets.map((s) => s.name);
    if (!names.length) return;
    const p = names.indexOf(this.cur);
    this.open(names[p < 0 ? 0 : Math.min(names.length - 1, Math.max(0, p + d))]);
  }

  open(name, code = undefined) {
    if (!this.sheet(name)) return;
    if (code != null && !this.glyph(name, code)) return;
    if (code !== undefined) this.editing = code;
    else if (this.sheet(name).style !== this.sheet(this.cur)?.style || !this.glyph(name, this.editing)) this.editing = null;
    this.cur = name;
    this.revealGlyph();
    this.host.setHash(name, this.editing);
    this.renderList();
    this.$("fo-list").querySelector?.(".g-row.sel")?.scrollIntoView?.({ block: "nearest" });
    this.render();
    this.focusGlyph();
  }

  revealGlyph() {
    if (this.editing === null) return;
    const cells = cellsOf(this.shown(this.cur), this.$("fo-filter").value);
    if (!cells.some((cell) => cell.codes.some((g) => g.code === this.editing))) this.$("fo-filter").value = "";
  }

  focusGlyph() {
    const card = this.glyphCards.get(this.editing);
    card?.scrollIntoView?.({ block: "nearest" });
    card?.focus?.();
  }

  render() {
    const s = this.shown(this.cur);
    if (!s) {
      this.renderEmpty();
      return;
    }
    const $ = this.$;
    $("fo-name").textContent = s.name;
    this.host.reference?.(s.name);
    Object.assign($("fo-kind"), { textContent: t(KINDS[s.kind]), className: `badge kind-${s.kind}` });
    $("fo-style").textContent = s.style;
    this.renderFacts(s);
    this.renderSample(s);
    this.renderSheet(s);
    this.renderGrid(s);
    this.renderTable(s);
    this.renderEditor(s);
  }

  renderFacts(s) {
    const style = this.host.index()?.font_sheets?.styles?.[s.style] ?? {};
    const off = (n) => `0x${hex(n, 6)}`;
    const facts = [[N_("Kind"), t(KINDS[s.kind])], [N_("Style"), s.style], [N_("Format"), s.format]];
    if (s.kind === "font") {
      facts.push([N_("Font ids"), s.codes.map((c) => `{FONT ${c}}`).join(", ")], [N_("Glyphs"), String(s.count)],
        [N_("Glyph table"), off(s.rom.glyphs)], [N_("Width table"), off(s.rom.widths)]);
    } else {
      facts.push([N_("ROM tiles"), String(s.tiles)], [N_("Pack tiles"), String(s.count - s.tiles)],
        [N_("Advance"), t("{n} px per character", { n: s.advance })], [N_("Fallback tile"), String(s.fallback)],
        [N_("Palette indices"), t("ink {ink}, outline {shadow}, background {background}", s.palette)],
        [N_("Sheet"), off(s.rom.sheet)]);
    }
    if (style.rows) facts.push([N_("Cell rows"), t("{rows} from row {top}", style)]);
    const dl = this.$("fo-facts");
    dl.replaceChildren();
    for (const [k, v] of facts) if (v) dl.append(el("dt", { textContent: t(k) }), el("dd", { textContent: v }));
    dl.append(el("dt", { textContent: t("Art file") }), el("dd", {}, style.art
      ? el("a", { href: this.host.dataUrl(style.art.path), target: "_blank", rel: "noopener", class: "mono",
        textContent: style.art.source })
      : t("none: the pack draws no glyph in this style")));
  }

  sampleLines() {
    if (this.sample === null) {
      const idx = this.host.index();
      this.sample = sampleText(idx?.charmap, this.host.codec());
      this.$("fo-sample-text").value = this.sample;
    }
    return this.sample.split("\n");
  }

  renderSample(s) {
    const $ = this.$;
    const lines = this.sampleLines();
    $("fo-sample").setAttribute("aria-label", `${t("Sample text")}: ${lines.join(" / ")}`);
    const rom = this.host.rom();
    let drawn;
    try {
      drawn = drawSample($("fo-sample"), s, lines, this.source(s), this.host.codec(), this.zoom);
    } catch (e) {
      console.error(e);
      $("fo-sample-note").textContent = t("The sample cannot be drawn: {error}", { error: e.message || e });
      return;
    }
    const { unknown, widths } = drawn;
    $("fo-sample-note").textContent = [unknown
      ? t("{n} glyphs are stand-ins drawn by the project: they exist only in the ROM. Load your ROM for the exact glyphs.", { n: unknown })
      : rom ? t("Exact glyphs: the pack's art and your ROM.") : t("Exact glyphs: the pack's art."),
    s.kind === "sprite" ? t("Line widths as the check measures them: {widths} px.", { widths: widths.join(", ") }) : ""]
      .filter(Boolean).join(" ");
  }

  renderSheet(s) {
    const $ = this.$;
    const sprite = s.kind === "sprite";
    $("fo-sheet-block").hidden = !sprite;
    if (!sprite) return;
    $("fo-sheet").setAttribute("aria-label", `${s.name}: ${t("Tile sheet")}`);
    const unknown = drawSheet($("fo-sheet"), this.source(s));
    $("fo-sheet-note").textContent = [t("{tiles} ROM tiles and {added} pack tiles", { tiles: s.tiles, added: s.count - s.tiles }),
      unknown ? t("{n} tiles are stand-ins drawn by the project: load your ROM for the exact tiles.", { n: unknown })
        : t("Exact tiles: your ROM and the pack's art.")].join(" · ");
  }

  renderGrid(s) {
    const $ = this.$;
    const filter = $("fo-filter").value;
    const cells = cellsOf(s, filter);
    const source = this.source(s);
    const style = this.host.index()?.font_sheets?.styles?.[s.style];
    const own = romSheet(s, this.host.rom());
    const raw = this.sheet(s.name);
    const edits = this.host.edits?.();
    this.cells.clear();
    this.glyphCards.clear();
    let unknown = 0;
    const cards = cells.map((cell) => {
      const g = cell.glyph;
      const canvas = el("canvas", {});
      if (!drawCell(canvas, s, cell, source, this.host.codec(), 3, style)) unknown++;
      this.cells.set(cell.tile, { cell, canvas });
      const art = edits ? cell.codes.map((x) => glyphArt(this.host.index(), raw,
        raw.glyphs.find((g) => g.code === x.code), own, edits.get(`${raw.style}:${x.code}`)))
        .find(Boolean) : null;
      const locked = edits && !art && cell.codes.some((x) => x.source === "copied");
      const width = s.kind === "sprite" ? (g?.width ?? s.advance) : source.width(cell.tile);
      const exactWidth = s.kind === "sprite" || g?.width !== undefined || this.host.rom() !== null;
      const codes = cell.codes.map((x) => x.code).join(" ");
      const marks = cell.codes.filter((x) => x.source !== "base");
      const where = s.kind === "font"
        ? t("tile {tile}: row {row}, column {col} of 16", { tile: cell.tile, row: cell.tile >> 4, col: cell.tile & 15 })
        : t("tile {tile}", { tile: cell.tile });
      const title = [glyphLabel(g), codes && t("code {code}", { code: codes }), where,
        t("{n} px", { n: width }), ...marks.map((x) => `${glyphLabel(x)}: ${t(SOURCES[x.source])}${x.from ? ` (${x.from})` : ""}`)]
        .filter(Boolean).join(" · ");
      canvas.setAttribute("role", "img");
      canvas.setAttribute("aria-label", title);
      const original = s.kind === "font" && own && marks.some((x) => x.source === "changed") ? romGlyph(own, cell.tile) : null;
      const card = `glyph-card${marks.some(isPack) ? " pack" : ""}${original ? " with-rom" : ""}${art ? " editable" : ""}`;
      const pick = (e) => {
        if (e.type === "click" || e.key === "Enter" || e.key === " ") {
          e.preventDefault?.();
          this.select(art.code);
        }
      };
      const selected = cell.codes.some((x) => x.code === this.editing);
      const node = el("div", { class: `${card}${selected ? " sel" : ""}`,
        title: art ? `${title} · ${t("click to redraw")}`
          : locked ? `${title} · ${t("load your ROM to redraw it from the glyph it copies")}` : title,
        ...(art ? { tabIndex: 0, role: "button", on: { click: pick, keydown: pick } } : { tabIndex: -1 }) },
        original ? this.pair(s, canvas, original) : canvas,
        el("div", { class: "gc-char", textContent: glyphLabel(g) || "·" }),
        el("div", { class: "gc-meta mono" },
          el("span", { textContent: codes || "—" }), " ",
          el("span", { class: "muted", textContent: `#${cell.tile}` })),
        el("div", { class: "gc-meta mono", textContent: exactWidth ? t("{n} px", { n: width }) : t("~{n} px", { n: width }) }),
        ...marks.map((x) => el("span", { class: `mark src-${x.source}`, textContent: t(MARKS[x.source]) })),
        art && edits.get(art.id) ? el("span", { class: "mark src-draft", textContent: t("redrawn") }) : null);
      for (const glyph of cell.codes) this.glyphCards.set(glyph.code, node);
      return node;
    });
    $("fo-grid").replaceChildren(...cards);
    $("fo-note").textContent = [
      t("{n} cells", { n: formatNumber(cells.length) }),
      unknown ? t("{n} without your ROM: stand-ins or blank", { n: unknown }) : "",
    ].filter(Boolean).join(" · ");
  }

  /** The glyph in the editor: art and draft state. */
  target() {
    const raw = this.sheet(this.cur);
    const edits = this.host.edits?.();
    if (this.editing === null || !raw || !edits) return null;
    const g = raw.glyphs.find((x) => x.code === this.editing);
    const own = romSheet(raw, this.host.rom());
    const base = glyphArt(this.host.index(), raw, g, own, edits.get(`${raw.style}:${this.editing}`));
    if (!base) return null;
    const d = edits.get(base.id);
    return { g, base, art: d ? { ...base, width: d.width, rows: [...d.rows] } : base, draft: Boolean(d) };
  }

  select(code) {
    if (!this.glyph(this.cur, code)) return;
    this.editing = code;
    this.revealGlyph();
    this.host.setHash(this.cur, code);
    this.render();
    this.focusGlyph();
  }

  setInk(ch) {
    this.ink = ch;
    const s = this.shown(this.cur);
    if (s) this.renderEditor(s);
  }

  /** Paint pixel (x, y); true when the art changed. */
  paintAt(x, y) {
    const target = this.target();
    const art = target && paintArt(target.art, x, y, this.ink);
    if (!art) return false;
    this.store(target, art);
    return true;
  }

  setWidth(width) {
    const target = this.target();
    const art = target && resizeArt(target.art, width);
    if (art && art.width !== target.art.width) this.store(target, art);
    else if (target) this.renderEditor(this.shown(this.cur));
  }

  discard() {
    const target = this.target();
    if (!target?.draft) return;
    this.host.edits().remove(target.base.id);
    this.host.changed?.();
    this.sources.clear();
    this.renderList();
    this.render();
  }

  /** Keep the art as a draft and redraw the views. */
  store(target, art) {
    this.host.edits().set(target.base, art);
    this.host.changed?.();
    this.sources.clear();
    const s = this.shown(this.cur);
    this.renderSample(s);
    this.renderSheet(s);
    const tile = s.glyphs.find((g) => g.code === target.g.code)?.tile;
    const card = this.cells.get(tile);
    if (art.width !== target.art.width || target.draft !== Boolean(this.host.edits().get(target.base.id)) || !card) {
      this.renderGrid(s);
    } else {
      drawCell(card.canvas, s, card.cell, this.source(s), this.host.codec(), 3,
        this.host.index()?.font_sheets?.styles?.[s.style]);
    }
    if (s.kind === "sprite") {
      this.renderList();
      this.renderFacts(s);
      this.renderTable(s);
    }
    this.renderEditor(s);
  }

  renderEditor(s) {
    const $ = this.$;
    const target = this.target();
    $("fo-edit-block").hidden = !target;
    if (!target) return;
    const sprite = s.kind === "sprite";
    const { art, g } = target;
    const p = s.palette;
    const names = sprite
      ? { "#": t("ink, index {n}", { n: p.ink }), s: t("outline, index {n}", { n: p.shadow }),
        _: t("transparent, index {n}", { n: p.background }) }
      : { "#": t("ink"), s: t("shadow"), _: t("background") };
    const colors = artColors(sprite);
    $("fo-edit-name").textContent = `${glyphLabel(g)} · ${g.code}`;
    $("fo-palette").replaceChildren(...ART_PIXELS.map((ch) => el("button", {
      type: "button", class: `btn sm fo-ink${ch === this.ink ? " on" : ""}`, "aria-pressed": String(ch === this.ink),
      on: { click: () => this.setInk(ch) },
    }, el("span", { class: "fo-swatch", style: `background: ${colors[ch]}` }), names[ch])));
    Object.assign($("fo-width"), { max: String(art.widest), value: String(art.width) });
    $("fo-art").setAttribute("aria-label", `${glyphLabel(g)} · ${g.code}`);
    drawArt($("fo-art"), art, sprite, EDIT_ZOOM[s.kind]);
    $("fo-discard").disabled = !target.draft;
    const file = this.host.index()?.font_sheets?.styles?.[s.style]?.art?.source;
    const copied = target.base.copy !== undefined;
    const others = copied && !sprite ? this.copiedElsewhere(g.char, s.style) : [];
    $("fo-edit-note").textContent = [target.draft
      ? t("Redrawn in this browser: Export and the JSON file of the submit page carry it.")
      : copied ? t("A copy of the ROM glyph {code}: draw on the grid to make it a letter of its own.", { code: target.base.copy })
        : target.base.source === "rom" ? t("Draw on the grid to create art for this slot.")
        : t("The pack's art: draw on the grid to redraw it."),
    file && copied && !sprite ? t("gen3tl web import writes it to {file} and sets its charmap source to glyph.", { file })
      : file ? t("gen3tl web import writes it to {file}.", { file }) : "",
    others.length ? t("Draw it in {sheets} too: a letter of its own needs art in every style.", { sheets: others.join(", ") })
      : ""].filter(Boolean).join(" ");
  }

  /** The first sheet of each other style copying `char`. */
  copiedElsewhere(char, style) {
    const edits = this.host.edits();
    const out = new Map();
    for (const s of this.sheets) {
      const g = s.style !== style && !out.has(s.style) ? s.glyphs.find((x) => x.char === char && x.source === "copied") : null;
      if (g && !edits.get(`${s.style}:${g.code}`)) out.set(s.style, s.name);
    }
    return [...out.values()];
  }

  /** Paint at a pointer event on the editor's grid. */
  paintEvent(e) {
    const canvas = this.$("fo-art");
    const scale = canvas.width / (canvas.clientWidth || canvas.width);
    const zoom = EDIT_ZOOM[this.sheet(this.cur)?.kind] ?? EDIT_ZOOM.font;
    this.paintAt(Math.floor((e.offsetX * scale) / zoom), Math.floor((e.offsetY * scale) / zoom));
  }

  /** The installed glyph beside the ROM's original. */
  pair(s, canvas, original) {
    const rom = el("canvas", { role: "img", "aria-label": t("ROM glyph") });
    drawRomGlyph(rom, s, original);
    return el("div", { class: "gc-pair" },
      el("figure", {}, canvas, el("figcaption", { textContent: t("installed") })),
      el("figure", { title: t("The glyph of your ROM before the pack redraws it: {n} px", { n: original.width }) },
        rom, el("figcaption", { textContent: t("in the ROM") })));
  }

  renderTable(s) {
    const $ = this.$;
    const sprite = s.kind === "sprite";
    $("fo-table-block").hidden = !sprite;
    if (!sprite) {
      $("fo-table").replaceChildren();
      return;
    }
    const by = new Map(s.glyphs.map((g) => [parseInt(g.code, 16), g]));
    const head = el("tr", {}, el("th", {}), ...Array.from({ length: 16 }, (_, c) => el("th", { textContent: hex(c, 1) })));
    const rows = Array.from({ length: 16 }, (_, r) => el("tr", {}, el("th", { textContent: `${hex(r, 1)}0` }),
      ...Array.from({ length: 16 }, (_, c) => {
        const b = r * 16 + c;
        const g = by.get(b);
        const cls = !g ? "fallback" : isPack(g) ? "pack" : "";
        return el("td", { class: cls, textContent: String(s.table[b]),
          title: `${hex(b)} → ${s.table[b]}${g ? ` · ${glyphLabel(g)} · ${t(SOURCES[g.source])}` : ""}` });
      })));
    $("fo-table").replaceChildren(el("thead", {}, head), el("tbody", {}, ...rows));
  }

  relabel() {
    if (this.generation === undefined) return;
    this.renderList();
    if (this.cur) this.render();
  }

  wire() {
    const $ = this.$;
    document.addEventListener("gen3tl-theme-change", () => {
      if (this.cur) this.renderSheet(this.shown(this.cur));
    });
    $("fo-prev").addEventListener("click", () => this.step(-1));
    $("fo-next").addEventListener("click", () => this.step(1));
    $("fo-filter").addEventListener("change", () => this.cur && this.render());
    $("fo-zoom").addEventListener("change", (e) => {
      this.zoom = Number(e.target.value);
      if (this.cur) this.render();
    });
    $("fo-sample-text").addEventListener("input", (e) => {
      this.sample = e.target.value;
      const s = this.shown(this.cur);
      if (s) this.renderSample(s);
    });
    const art = $("fo-art");
    art.addEventListener("pointerdown", (e) => {
      this.drawing = true;
      art.setPointerCapture?.(e.pointerId);
      this.paintEvent(e);
    });
    art.addEventListener("pointermove", (e) => {
      if (this.drawing) this.paintEvent(e);
    });
    for (const type of ["pointerup", "pointercancel"]) art.addEventListener(type, () => { this.drawing = false; });
    $("fo-width").addEventListener("change", (e) => this.setWidth(Number(e.target.value)));
    $("fo-discard").addEventListener("click", () => this.discard());
    $("fo-list").addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        this.step(e.key === "ArrowDown" ? 1 : -1);
      }
    });
  }
}
