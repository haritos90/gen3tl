// Glyph and width lookup of the page.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  BACKGROUND, Codec, CodecError, Glyphs, INK, Measure, OUTSIDE, SHADOW, artGlyph, decodeGlyph, entryMeasure,
  fallbackWidth, glyphSize, latinCells, latinWidths, romFonts, runs, sha1Hex, splitTokens, widthTable,
} from "../js/font.js";
import { CODEC, PACK, STYLE } from "./fixture.mjs";

const codec = new Codec(CODEC, PACK);

test("extended glyphs keep their IDs, metrics and pack art", () => {
  const style = { pack: { "105": 10 }, rom: { count: 512 } };
  const art = { top: 0, art: { "105": ["#_________"] } };
  const glyphs = new Glyphs(style, art);
  assert.equal(glyphs.table.widths.length, 512);
  assert.equal(glyphs.get(0x105).pixels[0], INK);
  assert.equal(glyphs.get(0x105).exact, true);
  const measure = new Measure((b) => glyphs.width(b), codec, { "F9 05": 99 });
  assert.equal(measure.tokenWidth("{F9 05}"), 10);
  assert.equal(measure.tokenWidth("{F9  05}"), 10);
  assert.deepEqual(runs("{F9 05}", measure), [{ kind: "glyph", byte: 0x105, width: 10, text: "{F9 05}" }]);
  assert.equal(runs("{F8 00}", measure)[0].kind, "slot");
  const small = new Measure((b) => glyphs.width(b), codec, {}, 60,
    { small: (b) => b === 0x105 ? 8 : 5 }, { "00": "small" });
  assert.equal(small.lineWidth("{FONT 00}{F9 05}A"), 13);
});

// font.encode_glyph, to build ROM glyphs
function encodeGlyph(px, fmt) {
  const tiles = fmt === "16x16" ? [[0, 0], [8, 0], [0, 8], [8, 8]] : [[0, 0], [0, 8]];
  const out = new Uint8Array(glyphSize(fmt));
  tiles.forEach(([ox, oy], t) => {
    for (let r = 0; r < 8; r++) {
      let hi = 0;
      let lo = 0;
      for (let k = 0; k < 4; k++) {
        hi |= (px[(oy + r) * 16 + ox + k] & 3) << (6 - 2 * k);
        lo |= (px[(oy + r) * 16 + ox + 4 + k] & 3) << (6 - 2 * k);
      }
      out[t * 0x10 + 2 * r] = lo;
      out[t * 0x10 + 2 * r + 1] = hi;
    }
  });
  return out;
}

function pattern(seed, fmt) {
  const px = new Uint8Array(256);
  const w = fmt === "16x16" ? 16 : 8;
  for (let y = 0; y < 16; y++) for (let x = 0; x < w; x++) px[y * 16 + x] = (x * 7 + y * 3 + seed) & 3;
  return px;
}

// a ROM image: widths at 0x40, glyphs at 0x100 (STYLE)
function rom() {
  const r = new Uint8Array(0x100 + 4 * 0x40);
  r.set([3, 4, 5, 6], 0x40);
  r.fill(9, 0x44, 0x140);
  for (let g = 0; g < 4; g++) r.set(encodeGlyph(pattern(g, "16x16"), "16x16"), 0x100 + g * 0x40);
  return r;
}

test("codec encodes characters, breaks and tokens", () => {
  assert.deepEqual(codec.encode("Ab Б"), [0xbb, 0xd6, 0x00, 0x37]);
  assert.deepEqual(codec.encode("a\\nb\\lc\\p".replace("c", "e")), [0xd5, 0xfe, 0xd6, 0xfa, 0xd9, 0xfb]);
  assert.deepEqual(codec.encode("{PLAYER}{LV}{COLOR 06}{FD 3D}{F8 00}{1F}"),
    [0xfd, 0x01, 0x34, 0xfc, 0x01, 0x06, 0xfd, 0x3d, 0xf8, 0x00, 0x1f]);
  assert.deepEqual(codec.encode("{JPN}"), [0xfc, 0x15]);
  assert.throws(() => codec.encode("Ж"), CodecError);
  assert.throws(() => codec.encode("{NOPE}"), CodecError);
  assert.throws(() => codec.encode("{COLOR}"), CodecError);
  assert.throws(() => codec.encode("{PLAYER"), CodecError);
  assert.throws(() => codec.encode("\\x"), CodecError);
});

test("fallback width is the most common pack width", () => {
  assert.equal(fallbackWidth(STYLE), 6);
  assert.equal(fallbackWidth({ pack: { "01": 4, "02": 5, "03": 5 } }), 5);
});

test("width table: pack over ROM, fallback without ROM", () => {
  const latin = new Uint8Array(256).fill(7);
  latin[0x10] = 3;
  const exact = widthTable(STYLE, latin);
  assert.equal(exact.widths[0x37], 6); // pack
  assert.equal(exact.widths[0x42], 5);
  assert.equal(exact.widths[0x10], 3); // ROM
  assert.ok(exact.known.every(Boolean));
  const approx = widthTable(STYLE, null);
  assert.equal(approx.widths[0x37], 6);
  assert.equal(approx.widths[0x10], 6); // fallback
  assert.equal(approx.known[0x37], true);
  assert.equal(approx.known[0x10], false);
});

test("a copied letter takes its source glyph's width", () => {
  const latin = new Uint8Array(256).fill(7);
  latin[0xbb] = 4;
  const t = widthTable({ ...STYLE, copy: { "87": "BB" } }, latin);
  assert.equal(t.widths[0x87], 4);
  assert.equal(t.widths[0x88], 7);
});

test("latin widths are read at the data's offsets", () => {
  const r = new Uint8Array(0x400);
  for (let i = 0; i < r.length; i++) r[i] = i & 0xff;
  const w = latinWidths({ styles: { unbound: STYLE } }, r).unbound;
  assert.equal(w.length, 256); // at least one per byte value
  assert.equal(w[0], 0x40);
  assert.equal(w[5], 0x45);
});

test("latin cells are read at the data's offsets", () => {
  const r = new Uint8Array(0x300);
  for (let i = 0; i < r.length; i++) r[i] = i & 0xff;
  const c = latinCells({ styles: { unbound: STYLE } }, r).unbound;
  assert.equal(c.length, 4 * 0x40); // count glyphs of the format
  assert.equal(c[0], 0x00);
  assert.equal(c[0x41], 0x41);
});

test("glyphs decode as font.decode_glyph", () => {
  for (const fmt of ["16x16", "8x16"]) {
    const px = pattern(1, fmt);
    assert.deepEqual(decodeGlyph(encodeGlyph(px, fmt), fmt), px);
  }
  const one = new Uint8Array(0x40);
  one[1] = 0b01000000; // tile 0 row 0: hi byte, first pixel INK
  const px = decodeGlyph(one);
  assert.equal(px[0], INK);
  assert.equal(px[1], OUTSIDE);
});

test("pack art sits at the style's top row", () => {
  const px = artGlyph(["_#s", "#__"], 1);
  assert.deepEqual([...px.slice(16, 19)], [BACKGROUND, INK, SHADOW]);
  assert.deepEqual([...px.slice(32, 35)], [INK, BACKGROUND, BACKGROUND]);
  assert.equal(px[0], OUTSIDE);
  assert.equal(px[19], OUTSIDE); // beyond the width
});

test("glyph lookup: pack exact, ROM exact with a ROM, unknown without", () => {
  const art = { top: 0, rows: 2, art: { "37": ["##", "s_"] } };
  const image = rom();
  const lat = latinWidths({ styles: { s: STYLE } }, image).s;
  const fonts = romFonts({ styles: { s: STYLE } }, image).s;
  const without = new Glyphs(STYLE, art);
  assert.equal(without.get(0x37).exact, true);
  assert.equal(without.get(0x37).pixels[0], INK);
  assert.deepEqual([without.get(0x02).pixels, without.get(0x02).exact, without.get(0x02).width], [null, false, 6]);
  const withRom = new Glyphs(STYLE, art, lat, fonts);
  assert.deepEqual(withRom.get(0x02), { pixels: pattern(2, "16x16"), width: 5, exact: true });
  assert.equal(withRom.get(0x37).width, 6); // the pack still wins
  const copied = new Glyphs({ ...STYLE, copy: { "03": "01" } }, art, lat, fonts);
  assert.deepEqual(copied.get(0x03), { pixels: pattern(1, "16x16"), width: 4, exact: true });
});

test("measure follows layout.Measure", () => {
  const widths = new Uint8Array(256).fill(6);
  widths[0x00] = 3; // space
  widths[0x34] = 9; // {LV}
  widths[0xf8] = 1;
  const ph = { PLAYER: 42, "F8 00": 8, STR_VAR_1: 30 };
  const m = new Measure((b) => widths[b], codec, ph, 60);
  assert.equal(m.tokenWidth("{COLOR 06}"), 0);
  assert.equal(m.tokenWidth("{PAUSE_UNTIL_PRESS}"), 0);
  assert.equal(m.tokenWidth("{F8 00}"), 8); // full name
  assert.equal(m.tokenWidth("{PLAYER}"), 42);
  assert.equal(m.tokenWidth("{STR_VAR_1}"), 30);
  assert.equal(m.tokenWidth("{B_ATK_NAME_WITH_PREFIX}"), 60); // default
  assert.equal(m.tokenWidth("{FD 3D}"), 60);
  assert.equal(m.tokenWidth("{LV}"), 9); // a byte
  assert.equal(m.tokenWidth("{F8 01}"), 1 + 6); // its bytes
  assert.equal(m.tokenWidth("{NOPE}"), 60);
  assert.equal(m.tokenWidth("A"), 6);
  assert.throws(() => m.tokenWidth("Ж"), CodecError);
  assert.equal(m.lineWidth("Ab {PLAYER}  {COLOR 08} "), 6 + 6 + 3 + 42);
  assert.deepEqual(splitTokens("a{X}\\nb"), ["a", "{X}", "\\n", "b"]);
});

test("entry measure: window style and slot widths", () => {
  const index = { fonts: { main: "unbound" }, placeholders: { widths: { PLAYER: 42 }, default: 60 } };
  const g = { unbound: { width: () => 6 }, small: { width: () => 4 } };
  const m = entryMeasure(index, g, codec, { font: "small", placeholders: { PLAYER: 50 } });
  assert.equal(m.lineWidth("ab{PLAYER}"), 4 + 4 + 50);
  assert.equal(entryMeasure(index, g, codec, null).lineWidth("ab{PLAYER}"), 6 + 6 + 42);
});

test("entry measure: the printer's letter spacing widens key icons only", () => {
  const index = { fonts: { main: "unbound" }, placeholders: { widths: { "F8 00": 8, PLAYER: 42 }, default: 60 } };
  const g = { unbound: { width: () => 6 } };
  const m = entryMeasure(index, g, codec, { icon_spacing: 1 });
  assert.equal(m.lineWidth("a{F8 00}{PLAYER}"), 6 + 9 + 42);
  assert.equal(entryMeasure(index, g, codec, {}).lineWidth("a{F8 00}"), 6 + 8);
});

test("entry measure: explicit window spacing survives font switches", () => {
  const index = { fonts: { main: "main", codes: { "00": "small" } },
    placeholders: { widths: { "F8 00": 8, "F8 03": 16 }, default: 60 } };
  const g = { main: { width: () => 6 }, small: { width: () => 4 } };
  const m = entryMeasure(index, g, codec, { icon_spacing: 2 });
  assert.equal(m.lineWidth("a{F8 00}{FONT 00}b{F8 03}"), 6 + 10 + 4 + 18);
  assert.equal(entryMeasure(index, g, codec, { icon_spacing: 0 }).lineWidth("a{F8 00}"), 6 + 8);
});

test("runs: what a line draws", () => {
  const m = new Measure(() => 6, codec, { PLAYER: 42 }, 60);
  assert.deepEqual(runs("A{COLOR 06}{PLAYER}{LV}Ж", m), [
    { kind: "glyph", byte: 0xbb, width: 6, text: "A" },
    { kind: "zero", token: "{COLOR 06}" },
    { kind: "slot", token: "{PLAYER}", width: 42 },
    { kind: "glyph", byte: 0x34, width: 6, text: "LV" },
    { kind: "bad", text: "Ж", width: 6 },
  ]);
});

test("columns and font switches", () => {
  const styles = { unbound: () => 6, small: () => 5 };
  const m = new Measure(() => 6, codec, { STR_VAR_1: 60 }, 60, styles, { "00": "small", "02": "unbound" });
  assert.equal(m.lineWidth("Ab{CLEAR_TO 1E}A"), 30 + 6);
  assert.equal(m.lineWidth("Abeloab{CLEAR_TO 1E}A"), 42 + 6); // past the column
  assert.equal(m.lineWidth("Ab{CLEAR_TO 1E}"), 12); // a trailing column is not shown
  assert.equal(m.lineWidth("Abe{CLEAR_TO 1E} {FONT 00}1 BB"), 30 + 6 + 20);
  assert.equal(m.lineWidth("{FONT 00}Ab{FONT 02}ab"), 10 + 12);
  assert.equal(m.lineWidth("Ab{FONT 07}ab"), 24); // unknown font
  assert.equal(new Measure(() => 6, codec, {}, 60).lineWidth("Ab{FONT 00}ab"), 24);
  assert.deepEqual(runs("A{CLEAR_TO 0A}{FONT 00}B", m), [
    { kind: "glyph", byte: 0xbb, width: 6, text: "A" },
    { kind: "column", token: "{CLEAR_TO 0A}", width: 4 },
    { kind: "zero", token: "{FONT 00}" },
    { kind: "glyph", byte: 0xbc, width: 5, text: "B", style: "small" },
  ]);
  assert.equal(runs("Abe{CLEAR_TO 0A}B", m)[3].width, 0);
  // the measure takes page styles and the data's codes
  const index = { fonts: { main: "unbound", codes: { "00": "small" } }, placeholders: { widths: {}, default: 60 } };
  const g = { unbound: { width: () => 6 }, small: { width: () => 4 } };
  assert.equal(entryMeasure(index, g, codec, null).lineWidth("ab{FONT 00}ab"), 12 + 8);
});

test("SHA-1 of the ROM", async () => {
  assert.equal(await sha1Hex(new TextEncoder().encode("abc")), "a9993e364706816aba3e25717850c26c9cd0d89d");
});
