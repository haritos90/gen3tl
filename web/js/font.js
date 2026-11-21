// Encode, measure and draw game text in the browser.
// Unknown glyphs use fallback widths and stand-ins.

export const OUTSIDE = 0;
export const INK = 1;
export const SHADOW = 2;
export const BACKGROUND = 3;

const HEX2 = /^[0-9A-F]{2}$/;
const hex = (h) => parseInt(h, 16);

export class CodecError extends Error {}

/** Encode characters and tokens using base and pack tables. */
export class Codec {
  constructor(codec, pack = {}, rules = {}) {
    this.standIns = rules.stand_ins ?? {};
    this.chars = new Map();
    for (const [c, b] of Object.entries(codec.chars)) this.chars.set(c, hex(b));
    for (const [c, b] of Object.entries(pack)) this.chars.set(c, hex(b));
    this.named = codec.named;
    this.controls = codec.controls;
    this.placeholders = codec.placeholders;
    this.zero = new RegExp(codec.zero);
  }

  /** Encode the contents of one brace token. */
  token(inner) {
    const parts = inner.split(/\s+/).filter(Boolean);
    if (!parts.length) throw new CodecError(`empty token {${inner}}`);
    const [head, ...args] = parts;
    if (parts.length === 1 && HEX2.test(head)) return [hex(head)];
    if (["FC", "FD", "F7", "F8", "F9"].includes(head) && args.every((a) => HEX2.test(a))) {
      return [hex(head), ...args.map(hex)];
    }
    if (head in this.controls) {
      const [code, n] = this.controls[head];
      if (args.length !== n || !args.every((a) => HEX2.test(a))) {
        throw new CodecError(`{${inner}} needs ${n} hex argument(s)`);
      }
      return [0xfc, hex(code), ...args.map(hex)];
    }
    if (!args.length && head in this.placeholders) return [0xfd, hex(this.placeholders[head])];
    if (!args.length && head in this.named) return [hex(this.named[head])];
    throw new CodecError(`unknown token {${inner}}`);
  }

  /** Encode text bytes without the terminator. */
  encode(text) {
    const out = [];
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (c === "\\") {
        const k = { n: 0xfe, l: 0xfa, p: 0xfb }[text[i + 1]];
        if (k === undefined) throw new CodecError(`unknown escape \\${text[i + 1] ?? ""}`);
        out.push(k);
        i++;
      } else if (c === "{") {
        const j = text.indexOf("}", i);
        if (j < 0) throw new CodecError("unclosed brace");
        out.push(...this.token(text.slice(i + 1, j)));
        i = j;
      } else {
        const b = this.chars.get(c);
        if (b === undefined) throw new CodecError(`character ${JSON.stringify(c)} has no byte`);
        out.push(b);
      }
    }
    return out;
  }
}

/** Use the most common width for unknown glyphs. */
export function fallbackWidth(style) {
  const n = new Map();
  for (const px of Object.values(style.pack)) n.set(px, (n.get(px) || 0) + 1);
  let best = 6;
  let most = 0;
  for (const [px, k] of n) if (k > most || (k === most && px < best)) [best, most] = [px, k];
  return best;
}

/** Map copied glyphs to their ROM sources. */
export function copiesOf(style) {
  return new Map(Object.entries(style.copy ?? {}).map(([b, s]) => [hex(b), hex(s)]));
}

/**
 * Resolve widths from pack overrides, ROM or fallback.
 * Copied letters use their source glyph's width.
 */
export function widthTable(style, latin = null, fallback = fallbackWidth(style)) {
  const count = Math.max(256, style.rom?.count ?? 256, latin?.length ?? 0);
  const widths = new Uint8Array(count);
  const known = new Array(count).fill(false);
  const pack = new Map(Object.entries(style.pack).map(([b, px]) => [hex(b), px]));
  const copy = copiesOf(style);
  for (let b = 0; b < count; b++) {
    if (pack.has(b)) [widths[b], known[b]] = [pack.get(b), true];
    else if (latin) [widths[b], known[b]] = [latin[copy.get(b) ?? b], true];
    else widths[b] = fallback;
  }
  return { widths, known };
}

/** Read each style's width table from the ROM. */
export function latinWidths(fonts, rom) {
  const out = {};
  for (const [style, f] of Object.entries(fonts.styles)) {
    const at = f.rom.widths;
    out[style] = rom.slice(at, at + Math.max(f.rom.count, 256));
  }
  return out;
}

const TILES = { "16x16": [[0, 0], [8, 0], [0, 8], [8, 8]], "8x16": [[0, 0], [0, 8]] };

export const glyphSize = (fmt) => 0x10 * TILES[fmt].length;

/** Read each style's glyph table from the ROM. */
export function latinCells(fonts, rom) {
  const out = {};
  for (const [style, f] of Object.entries(fonts.styles)) {
    const at = f.rom.glyphs;
    out[style] = rom.slice(at, at + f.rom.count * glyphSize(f.format));
  }
  return out;
}

/** Decode one glyph into 16×16 pixel values. */
export function decodeGlyph(raw, fmt = "16x16") {
  const px = new Uint8Array(256);
  TILES[fmt].forEach(([ox, oy], t) => {
    const base = t * 0x10;
    for (let r = 0; r < 8; r++) {
      const lo = raw[base + 2 * r];
      const hi = raw[base + 2 * r + 1];
      let x = ox;
      for (const byte of [hi, lo]) {
        for (let k = 0; k < 4; k++) px[(oy + r) * 16 + x++] = (byte >> (6 - 2 * k)) & 3;
      }
    }
  });
  return px;
}

/** Extract each font style's ROM glyph data. */
export function romFonts(fonts, rom) {
  const out = {};
  for (const [style, f] of Object.entries(fonts.styles)) {
    const n = glyphSize(f.format);
    const at = f.rom.glyphs;
    out[style] = { format: f.format, size: n, glyphs: rom.slice(at, at + f.rom.count * n) };
  }
  return out;
}

const ART = { _: BACKGROUND, "#": INK, s: SHADOW };

/** Convert pack art into 16×16 pixel values. */
export function artGlyph(rows, top) {
  const px = new Uint8Array(256);
  rows.forEach((row, y) => {
    for (let x = 0; x < row.length; x++) px[(top + y) * 16 + x] = ART[row[x]] ?? OUTSIDE;
  });
  return px;
}

/** Look up glyphs, preferring pack art over ROM data. */
export class Glyphs {
  constructor(style, art, latin = null, rom = null) {
    this.table = widthTable(style, latin);
    this.copy = copiesOf(style);
    this.format = style.format;
    this.art = art;
    this.rom = rom;
    this.cache = new Map();
  }

  width(b) {
    return this.table.widths[b];
  }

  get(b) {
    if (!this.cache.has(b)) {
      const key = b.toString(16).toUpperCase().padStart(2, "0");
      let pixels = null;
      if (this.art && key in this.art.art) pixels = artGlyph(this.art.art[key], this.art.top);
      else if (this.rom) {
        const src = this.copy.get(b) ?? b;
        pixels = decodeGlyph(this.rom.glyphs.subarray(src * this.rom.size), this.rom.format);
      }
      this.cache.set(b, { pixels, width: this.width(b), exact: this.table.known[b] && pixels !== null });
    }
    return this.cache.get(b);
  }
}

const NAMED_SLOT = ["PLAYER", "RIVAL", "KUN"];
const BAD_WIDTH = 6; // Unencodable characters still need visible preview width.
const SPLIT = /(\\[nlp]|\{[^}]*\})/g;

/** Split text into characters and control tokens. */
export function splitTokens(text) {
  const out = [];
  let pos = 0;
  for (const m of text.matchAll(SPLIT)) {
    out.push(...text.slice(pos, m.index), m[0]);
    pos = m.index + m[0].length;
  }
  out.push(...text.slice(pos));
  return out;
}

const CLEAR_TO = /^\{CLEAR_TO ([0-9A-Fa-f]+)\}$/;
const FONT = /^\{FONT ([0-9A-Fa-f]+)\}$/;
const code2 = (h) => parseInt(h, 16).toString(16).toUpperCase().padStart(2, "0");

/** Measure text with entry-specific glyph and placeholder widths. */
export class Measure {
  constructor(width, codec, placeholders, fallback = 60, styles = {}, fonts = {}) {
    this.glyph = width;
    this.codec = codec;
    this.ph = placeholders;
    this.fallback = fallback;
    this.styles = styles;
    this.fonts = fonts;
    this.style = null;
  }

  /** Apply font switches, retaining the measure for unknown fonts. */
  fontMeasure(arg) {
    const style = this.fonts[code2(arg)];
    if (!(style in this.styles)) return this;
    const m = new Measure(this.styles[style], this.codec, this.ph, this.fallback, this.styles, this.fonts);
    m.style = style;
    return m;
  }

  /** Advance through tokens, columns and font switches. */
  advance(toks) {
    let x = 0;
    let m = this;
    for (const t of toks) {
      const c = CLEAR_TO.exec(t);
      const f = c ? null : FONT.exec(t);
      if (c) x = Math.max(x, parseInt(c[1], 16));
      else if (f) m = this.fontMeasure(f[1]);
      else x += m.tokenWidth(t);
    }
    return x;
  }

  bytesWidth(bytes) {
    return bytes.reduce((s, b) => s + this.glyph(b), 0);
  }

  tokenWidth(tok) {
    if (tok.startsWith("{") && tok.endsWith("}") && tok.length > 2) {
      const inner = tok.slice(1, -1);
      const name = inner.split(/\s+/).filter(Boolean)[0] ?? "";
      if (this.codec.zero.test(tok)) return 0;
      const extended = /^F9\s+([0-9A-Fa-f]{2})$/.exec(inner);
      if (extended) return this.glyph(0x100 + hex(extended[1]));
      if (inner in this.ph) return this.ph[inner];
      if (name in this.ph) return this.ph[name];
      if (name === "FD" || name === "B_BUFF1" || name.startsWith("B_") || name.startsWith("STR_VAR") ||
          NAMED_SLOT.includes(name)) {
        return this.ph[name] ?? this.fallback;
      }
      try {
        return this.bytesWidth(this.codec.encode(tok));
      } catch {
        return this.fallback;
      }
    }
    return this.bytesWidth(this.codec.encode(tok));
  }

  /** Measure the width of a displayed line. */
  lineWidth(line) {
    const toks = splitTokens(line);
    while (toks.length && (toks.at(-1) === " " || this.codec.zero.test(toks.at(-1)))) toks.pop();
    return this.advance(toks);
  }
}

const KEY_ICON = "F8"; // control code of a key icon

/** Build measurements from window style and placeholder widths. */
export function entryMeasure(index, glyphs, codec, window) {
  const style = window?.font ?? index.fonts.main;
  const g = glyphs[style] ?? glyphs[index.fonts.main];
  const ph = { ...index.placeholders.widths, ...(window?.placeholders ?? {}) };
  // the printer adds its letter spacing to key icons only
  if (window?.icon_spacing) {
    for (const k of Object.keys(ph)) if (k.startsWith(`${KEY_ICON} `)) ph[k] += window.icon_spacing;
  }
  const styles = Object.fromEntries(Object.entries(glyphs).map(([s, x]) => [s, (b) => x.width(b)]));
  return new Measure((b) => g.width(b), codec, ph, index.placeholders.default, styles, index.fonts.codes ?? {});
}

/**
 * Split a line into drawable glyphs, slots and controls.
 * Font switches apply to subsequent glyphs.
 */
export function runs(line, measure) {
  const out = [];
  const m0 = measure;
  for (const t of splitTokens(line)) {
    const c = CLEAR_TO.exec(t);
    const f = c ? null : FONT.exec(t);
    if (c) {
      const x = out.reduce((s, r) => s + (r.width || 0), 0);
      out.push({ kind: "column", token: t, width: Math.max(0, parseInt(c[1], 16) - x) });
      continue;
    }
    if (f) {
      measure = m0.fontMeasure(f[1]);
      out.push({ kind: "zero", token: t });
      continue;
    }
    const style = measure.style ? { style: measure.style } : {};
    if (t.length > 1 && t.startsWith("{")) {
      if (measure.codec.zero.test(t)) {
        out.push({ kind: "zero", token: t });
        continue;
      }
      let bytes = null;
      try {
        bytes = measure.codec.encode(t);
      } catch {
        bytes = null;
      }
      if (bytes && bytes.length === 2 && bytes[0] === 0xF9) {
        const b = 0x100 + bytes[1];
        out.push({ kind: "glyph", byte: b, width: measure.glyph(b), text: t, ...style });
      }
      else if (bytes && bytes.length === 1) {
        out.push({ kind: "glyph", byte: bytes[0], width: measure.glyph(bytes[0]), text: t.slice(1, 3), ...style });
      }
      else out.push({ kind: "slot", token: t, width: measure.tokenWidth(t) });
      continue;
    }
    const b = measure.codec.chars.get(t);
    if (b === undefined) out.push({ kind: "bad", text: t, width: BAD_WIDTH });
    else out.push({ kind: "glyph", byte: b, width: measure.glyph(b), text: t, ...style });
  }
  return out;
}

/** Hash ROM bytes to verify their identity. */
export async function sha1Hex(buffer) {
  const d = await crypto.subtle.digest("SHA-1", buffer);
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
