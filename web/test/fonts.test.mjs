// The Fonts view: sheets, cells and samples.
import assert from "node:assert/strict";
import { test } from "node:test";

import { Codec, INK, OUTSIDE, SHADOW } from "../js/font.js";
import {
  EDIT_ZOOM, FontsView, SHEET_GAP, SpriteTiles, artColors, artTile, cellsOf, draftFonts, draftSheet, drawArt, drawRomGlyph,
  drawSheet, fontGlyphs, fontRuns, glyphArt, glyphLabel, isPack, paintArt, resizeArt, romGlyph, romSheet, romTile, sampleText,
  sheetCounts, sheetLayout, sheetsOf, spriteRuns, spriteStandIn, styleGlyphs,
} from "../js/fonts.js";
import { GlyphEdits, exportDoc } from "../js/store.js";
import { fakeDocument } from "./fixture.mjs";

globalThis.getComputedStyle ??= () => ({ getPropertyValue: () => "#a34700" });

const BASE = { chars: { " ": "00", A: "BB", ":": "F0" }, named: { LV: "34" }, controls: {}, placeholders: {},
  zero: "^\\{(COLOR|FONT)\\b" };
const CODEC = new Codec(BASE, { "Б": "37", "А": "87" });

const ROWS6 = Array.from({ length: 14 }, (_, y) => (y > 2 && y < 11 ? "_##s__" : "______"));
const FONT_GLYPHS = [
  { code: "00", tile: 0, char: " ", source: "base" },
  { code: "37", tile: 0x37, char: "Б", source: "added", width: 6 },
  { code: "87", tile: 0x87, char: "А", source: "copied", from: "BB" },
  { code: "BB", tile: 0xBB, char: "A", source: "base" },
  { code: "105", tile: 0x105, name: "F9 05", source: "changed", width: 10 },
];
const TABLE = Array.from({ length: 256 }, () => 0);
Object.assign(TABLE, { 0xBB: 1, 0x00: 2, 0x37: 3, 0x87: 1, 0xF0: 2 });
const HUD = {
  kind: "sprite", name: "hud", style: "hud", format: "8x8", count: 4, tiles: 3, advance: 4, fallback: 0,
  palette: { ink: 1, shadow: 12, background: 0 }, rom: { sheet: 0x3000 }, table: TABLE,
  art: { 3: ["sssss", "s###s", "s#sss", "s##ss", "s#s#s", "s#s#s", "s##ss", "ssss_"] },
  glyphs: [
    { code: "00", tile: 2, char: " ", source: "base", width: 4 },
    { code: "37", tile: 3, char: "Б", source: "added", width: 4 },
    { code: "87", tile: 1, char: "А", source: "copied", from: "BB", width: 4 },
    { code: "BB", tile: 1, char: "A", source: "base", width: 4 },
    { code: "F0", tile: 2, char: ":", source: "alias", from: "00", width: 4 },
  ],
};
const sheet = (name, glyphs, widths) => ({ kind: "font", name, style: "main", format: "16x16", count: 512,
  codes: ["01"], rom: { glyphs, widths }, glyphs: FONT_GLYPHS });
const INDEX = {
  charmap: { "Б": "37", "А": "87", "б": "42" },
  fonts: { main: "main", styles: { main: { format: "16x16", pack: { 37: 6, 105: 10 }, copy: { 87: "BB" },
    rom: { font: "f0", glyphs: 0x1000, widths: 0x200, count: 512 } } } },
  glyphs: { main: { top: 0, rows: 14, art: { 37: ROWS6, 105: ROWS6.map((r) => `${r}____`) } } },
  font_sheets: {
    pack: "assets/lang/xx",
    styles: { main: { top: 0, rows: 14, art: { source: "assets/lang/xx/fonts/main.txt", path: "fonts/main.txt" } },
      hud: { top: 0, rows: 8, art: null } },
    sheets: [sheet("f0", 0x1000, 0x200), sheet("f1", 0x9000, 0x600), HUD],
  },
};

/** A ROM: f0 and f1 differ in A. */
function rom() {
  const r = new Uint8Array(0x11000);
  r[0x200] = 3;
  r[0x200 + 0xBB] = 7;
  r[0x600 + 0xBB] = 9;
  r[0x1000 + 0xBB * 0x40 + 1] = 0x40; // A: one ink pixel at (0, 0)
  r[0x200 + 0x105] = 9; // the ROM's F9 05, which the pack redraws
  r[0x1000 + 0x105 * 0x40 + 1] = 0x80; // its shadow pixel at (0, 0)
  r[0x3000 + 32] = 0x1C; // tile 1: outline, then ink
  return r;
}

test("sheets, counts and labels", () => {
  assert.deepEqual(sheetsOf(INDEX).map((s) => s.name), ["f0", "f1", "hud"]);
  assert.deepEqual(sheetsOf(null), []);
  assert.deepEqual(sheetCounts(HUD), { glyphs: 5, pack: 2 });
  assert.deepEqual(INDEX.font_sheets.sheets[0].glyphs.filter(isPack).map((g) => g.code), ["37", "87", "105"]);
  assert.deepEqual(FONT_GLYPHS.map(glyphLabel), ["␣", "Б", "А", "A", "{F9 05}"]);
});

test("cells: every glyph, the pack's, every tile with its codes", () => {
  assert.equal(cellsOf(HUD).length, 5);
  assert.deepEqual(cellsOf(HUD, "pack").map((c) => c.glyph.char), ["Б", "А"]);
  const tiles = cellsOf(HUD, "tiles");
  assert.deepEqual(tiles.map((c) => c.tile), [0, 1, 2, 3]);
  assert.deepEqual(tiles.map((c) => c.codes.map((g) => g.code)), [[], ["87", "BB"], ["00", "F0"], ["37"]]);
  assert.equal(tiles[2].glyph.char, " "); // an alias never names its tile
  assert.equal(tiles[1].glyph.char, "A"); // nor a pack letter on a base tile
  assert.equal(tiles[0].glyph, null);
  assert.equal(cellsOf(INDEX.font_sheets.sheets[0], "tiles").length, 512);
});

test("the sample: the pack's capitals, then the rest", () => {
  assert.equal(sampleText(INDEX.charmap), "БА\nб");
  assert.equal(sampleText({}, new Codec(BASE)), "A"); // no pack: the base letters
});

test("font glyphs: pack art exact, the rest from each sheet's own ROM tables", () => {
  const [f0, f1] = INDEX.font_sheets.sheets;
  const bare = fontGlyphs(f0, INDEX);
  assert.deepEqual([bare.get(0x37).exact, bare.get(0x105).exact, bare.get(0xBB).exact, bare.get(0x87).exact],
    [true, true, false, false]);
  assert.equal(bare.width(0x105), 10);
  const image = rom();
  const g0 = fontGlyphs(f0, INDEX, image);
  const g1 = fontGlyphs(f1, INDEX, image);
  assert.deepEqual([g0.width(0xBB), g1.width(0xBB), g0.width(0x87)], [7, 9, 7]);
  assert.equal(g0.get(0xBB).pixels[0], INK);
  assert.equal(g1.get(0xBB).pixels[0], OUTSIDE); // f1's glyph table is its own
  assert.equal(g0.get(0x87).pixels[0], INK); // a copied letter draws its source glyph
  assert.deepEqual(romSheet(f0, null), null);
});

test("the ROM's original of a glyph the pack redraws", () => {
  const f0 = INDEX.font_sheets.sheets[0];
  const original = romGlyph(romSheet(f0, rom()), 0x105);
  assert.equal(original.width, 9);
  assert.deepEqual([original.pixels[0], original.pixels[1]], [SHADOW, OUTSIDE]);
  assert.notEqual(fontGlyphs(f0, INDEX, rom()).get(0x105).pixels[0], SHADOW, "the installed glyph is the pack's");
  const calls = [];
  const canvas = { getContext: () => ({ fillRect(x, y, w, h) { calls.push([this.fillStyle, x, y, w, h]); } }) };
  drawRomGlyph(canvas, f0, original, 2);
  assert.deepEqual([canvas.width, canvas.height], [32, 32]);
  assert.deepEqual(calls.at(-1), ["#e02020", 18, 0, 1, 32]); // its own width marked
});

test("a font sample: stand-ins without a ROM, exact with it", () => {
  const f0 = INDEX.font_sheets.sheets[0];
  const bare = fontRuns("ББ A", fontGlyphs(f0, INDEX), CODEC);
  assert.deepEqual(bare.runs.map((r) => [r.x, r.width, r.exact]), [[0, 6, true], [6, 6, true], [12, 6, false],
    [18, 6, false]]);
  assert.equal(bare.unknown, 2);
  assert.ok(bare.runs[3].pixels.includes(INK), "a stand-in A");
  const exact = fontRuns("ББ A{F9 05}Q", fontGlyphs(f0, INDEX, rom()), CODEC);
  assert.deepEqual(exact.runs.map((r) => r.width), [6, 6, 3, 7, 10, 6]);
  assert.equal(exact.unknown, 0);
  assert.ok(exact.runs[5].bad);
  assert.equal(exact.width, 38);
});

test("sprite tiles: art, ROM nibbles, stand-ins", () => {
  assert.deepEqual([...artTile(["s#_"]).slice(0, 4)], [SHADOW, INK, OUTSIDE, OUTSIDE]);
  assert.deepEqual([...romTile([0x1C, 0x05], HUD.palette).slice(0, 4)], [SHADOW, INK, 4, OUTSIDE]);
  const bare = new SpriteTiles(HUD);
  assert.deepEqual([bare.get(3).exact, bare.get(1).exact, bare.get(0).pixels], [true, false, null]);
  assert.ok(bare.get(1).pixels.includes(INK) && bare.get(1).pixels.includes(SHADOW), "an outlined stand-in");
  const stand = spriteStandIn("A");
  assert.ok([0, 4, 5, 6, 7].every((x) => [...Array(8).keys()].every((y) => stand[y * 8 + x] !== INK)),
    "ink stays in columns 1-3");
  const exact = new SpriteTiles(HUD, rom());
  assert.deepEqual([exact.get(1).exact, exact.get(1).pixels[0], exact.get(1).pixels[1]], [true, SHADOW, INK]);
});

test("a sprite sample: one advance per byte through the table", () => {
  const bare = spriteRuns("АБ:Q", new SpriteTiles(HUD), CODEC);
  assert.deepEqual(bare.runs.map((r) => [r.x, r.tile ?? null, r.exact ?? null, Boolean(r.bad)]),
    [[0, 1, false, false], [4, 3, true, false], [8, 2, false, false], [12, null, null, true]]);
  assert.equal(bare.width, 20);
  assert.equal(bare.unknown, 2);
  const exact = spriteRuns("А{COLOR 01}Б", new SpriteTiles(HUD, rom()), CODEC);
  assert.deepEqual(exact.runs.map((r) => r.tile), [1, 3]);
  assert.equal(exact.unknown, 0);
});

test("a stepping sprite sheet: each byte's own advance on a line with a pack tile", () => {
  const steps = Array.from({ length: 256 }, () => 4);
  Object.assign(steps, { 0x37: 6, 0x00: 2 });
  const tiles = new SpriteTiles({ ...HUD, advances: steps });
  const stepped = spriteRuns("АБ :", tiles, CODEC);
  assert.deepEqual(stepped.runs.map((r) => r.x), [0, 4, 10, 12]);
  assert.equal(stepped.width, 20);
  assert.equal(stepped.advance, 16); // the cursor's travel, as the check measures
  const fixed = spriteRuns("А :", tiles, CODEC); // no pack tile: the routine's own step
  assert.deepEqual(fixed.runs.map((r) => r.x), [0, 4, 8]);
  assert.equal(fixed.advance, 12);
});

test("the tile sheet: 16 tiles a row, pack tiles framed with the accent token", () => {
  const four = sheetLayout(4);
  assert.deepEqual([four.width, four.height, four.at], [42, 12, [[2, 2], [12, 2], [22, 2], [32, 2]]]);
  const many = sheetLayout(161);
  assert.deepEqual([many.width, many.height, many.at[16], many.at[160]], [162, 112, [2, 12], [2, 102]]);
  assert.equal(SHEET_GAP, 2);
  const calls = [];
  const canvas = { getContext: () => ({ fillRect(x, y, w, h) { calls.push([this.fillStyle, x, y, w, h]); } }) };
  const oldDocument = globalThis.document;
  const oldComputed = globalThis.getComputedStyle;
  let accent = "#a34700";
  globalThis.document = { documentElement: {} };
  globalThis.getComputedStyle = (root) => {
    assert.equal(root, globalThis.document.documentElement);
    return { getPropertyValue: (name) => {
      assert.equal(name, "--accent");
      return ` ${accent} `;
    } };
  };
  try {
    assert.equal(drawSheet(canvas, new SpriteTiles(HUD), 2), 3); // the ROM tiles without a ROM
    assert.deepEqual([canvas.width, canvas.height], [84, 24]);
    assert.deepEqual(calls.filter(([c]) => c === accent), [[accent, 62, 2, 20, 20]]); // tile 3 only
    accent = "#f2a541";
    calls.length = 0;
    assert.equal(drawSheet(canvas, new SpriteTiles(HUD), 2), 3);
    assert.deepEqual(calls.filter(([c]) => c === accent), [[accent, 62, 2, 20, 20]]);
    assert.equal(drawSheet(canvas, new SpriteTiles(HUD, rom()), 2), 0);
  } finally {
    globalThis.document = oldDocument;
    globalThis.getComputedStyle = oldComputed;
  }
});

/** The view over a fake page of canvas nodes. */
function page() {
  const doc = fakeDocument();
  const create = doc.createElement;
  doc.createElement = (tag) => {
    const node = create(tag);
    node.setAttribute = (key, value) => { node[key] = value; };
    node.addEventListener = (type, fn) => { node[`on${type}`] = fn; };
    node.focus = () => { doc.activeElement = node; };
    node.scrollIntoView = (options) => { node.scrolled = options; };
    node.getContext = () => {
      node.calls = [];
      return { fillRect(x, y, w, h) { node.calls.push([this.fillStyle, x, y, w, h]); } };
    };
    return node;
  };
  doc.documentElement = doc.createElement("html");
  doc.addEventListener = (type, fn) => { doc[`on${type}`] = fn; };
  doc.dispatchEvent = (event) => doc[`on${event.type}`]?.(event);
  const nodes = new Map();
  const $ = (id) => {
    if (!nodes.has(id)) nodes.set(id, Object.assign(doc.createElement("div"), { value: "" }));
    return nodes.get(id);
  };
  return { doc, $ };
}

test("the view lists the sheets, draws the sample, the grid and the table", () => {
  const { doc, $ } = page();
  const old = globalThis.document;
  globalThis.document = doc;
  try {
    const hashes = [];
    let image = null;
    const view = new FontsView({ index: () => INDEX, rom: () => image, codec: () => CODEC,
      dataUrl: (path) => `https://site.test/data/ru/${path}`, setHash: (id) => hashes.push(id) }, $);
    view.wire();
    view.reset();
    assert.equal(view.cur, "f0");
    assert.deepEqual(hashes, ["f0"]);
    assert.equal($("fo-list").childNodes.length, 3);
    assert.match($("fo-list").childNodes[2].textContent, /hud.*Sprite font.*5 glyphs, 2 from the pack/);
    assert.equal($("fo-grid").childNodes.length, 5);
    assert.deepEqual($("fo-grid").childNodes.map((c) => c.className.includes("pack")), [false, true, true, false, true]);
    assert.match($("fo-grid").childNodes[1].textContent, /^Б37 #556 pxadded$/);
    assert.match($("fo-grid").childNodes[3].textContent, /~6 px/, "a ROM width estimated without a ROM");
    assert.equal($("fo-sample-text").value, "БА\nб");
    assert.match($("fo-sample-note").textContent, /stand-ins/);
    const link = $("fo-facts").childNodes.at(-1).childNodes[0];
    assert.equal(link.href, "https://site.test/data/ru/fonts/main.txt");
    assert.equal(link.textContent, "assets/lang/xx/fonts/main.txt");
    assert.equal($("fo-table-block").hidden, true);
    assert.equal($("fo-sheet-block").hidden, true);
    $("fo-filter").value = "pack";
    $("fo-filter").onchange();
    assert.equal($("fo-grid").childNodes.length, 3);
    assert.ok($("fo-grid").childNodes.every((c) => !c.className.includes("with-rom")), "no original without a ROM");
    image = rom();
    view.refresh();
    const withRom = $("fo-grid").childNodes.filter((c) => c.className.includes("with-rom"));
    assert.equal(withRom.length, 1);
    assert.match(withRom[0].textContent, /^installedin the ROM\{F9 05\}105 #26110 pxchanged$/);
    assert.equal(withRom[0].childNodes[0].childNodes[1].title, "The glyph of your ROM before the pack redraws it: 9 px");
    assert.match($("fo-sample-note").textContent, /Exact glyphs: the pack's art and your ROM/);
    assert.doesNotMatch($("fo-sample-note").textContent, /Line widths/); // a font sheet's widths need the ROM
    view.step(2);
    assert.equal(view.cur, "hud");
    assert.match($("fo-sample-note").textContent, /Line widths as the check measures them: \d+, \d+ px\./);
    assert.equal($("fo-table-block").hidden, false);
    assert.equal($("fo-sheet-block").hidden, false);
    assert.equal($("fo-sheet-note").textContent, "3 ROM tiles and 1 pack tiles · Exact tiles: your ROM and the pack's art.");
    const [head, body] = $("fo-table").childNodes;
    assert.equal(head.childNodes[0].childNodes.length, 17);
    assert.equal(body.childNodes.length, 16);
    assert.equal(body.childNodes[3].childNodes[8].className, "pack"); // byte 37
    assert.equal(body.childNodes[0].childNodes[1].className, ""); // byte 00: a ROM glyph
    assert.equal(body.childNodes[0].childNodes[2].className, "fallback");
    assert.match($("fo-facts").textContent, /Advance4 px per character/);
    assert.match($("fo-facts").textContent, /none: the pack draws no glyph/);
    $("fo-sample-text").value = "Q";
    $("fo-sample-text").oninput({ target: $("fo-sample-text") });
    assert.equal(view.sample, "Q");
    view.step(5);
    assert.equal(view.cur, "hud");
  } finally {
    globalThis.document = old;
  }
});

test("a theme change redraws the visible sprite sheet with the new accent", () => {
  const { doc, $ } = page();
  const oldDocument = globalThis.document;
  const oldComputed = globalThis.getComputedStyle;
  let accent = "#a34700";
  globalThis.document = doc;
  globalThis.getComputedStyle = () => ({ getPropertyValue: () => accent });
  try {
    const view = new FontsView({ index: () => INDEX, rom: () => null, codec: () => CODEC, setHash() {} }, $);
    view.wire();
    view.reset("hud");
    assert.ok($("fo-sheet").calls.some(([color]) => color === accent));
    accent = "#f2a541";
    doc.dispatchEvent({ type: "gen3tl-theme-change" });
    assert.deepEqual($("fo-sheet").calls.filter(([color]) => color === accent), [[accent, 93, 3, 30, 30]]);
  } finally {
    globalThis.document = oldDocument;
    globalThis.getComputedStyle = oldComputed;
  }
});

test("without font sheets the view says how to get them; relabel before a language waits", () => {
  const { doc, $ } = page();
  const old = globalThis.document;
  globalThis.document = doc;
  try {
    const idle = new FontsView({ index: () => null }, () => { throw new Error("no element yet"); });
    idle.relabel();
    idle.refresh();
    assert.equal(idle.cur, null);
    const view = new FontsView({ index: () => ({}), rom: () => null, codec: () => CODEC, setHash() {} }, $);
    view.reset("f0");
    assert.equal(view.cur, null);
    assert.match($("fo-count").textContent, /gen3tl web data --split/);
  } finally {
    globalThis.document = old;
  }
});

test("glyph routes reveal and focus exact codes with and without ROM art", () => {
  const { doc, $ } = page();
  const old = globalThis.document;
  globalThis.document = doc;
  try {
    const hashes = [];
    const edits = new GlyphEdits(null, "xx", "p");
    let image = null;
    const view = new FontsView({ index: () => INDEX, rom: () => image, codec: () => CODEC,
      dataUrl: (path) => path, edits: () => edits, setHash: (...target) => hashes.push(target) }, $);
    view.reset(null, false);
    for (image of [null, rom()]) {
      for (const name of ["f0", "f1"]) {
        for (const [code, editable] of [["BB", false], ["87", image !== null], ["105", true]]) {
          $("fo-filter").value = "pack";
          view.open(name, code);
          assert.deepEqual(view.address, [name, "glyph", code]);
          assert.deepEqual(hashes.at(-1), [name, code]);
          const card = view.glyphCards.get(code);
          assert.equal(doc.activeElement, card);
          assert.deepEqual(card.scrolled, { block: "nearest" });
          assert.match(card.className, /\bsel\b/);
          assert.equal($("fo-filter").value, code === "BB" ? "" : "pack");
          assert.equal($("fo-edit-block").hidden, !editable);
          if (!editable) {
            assert.equal(card.tabIndex, -1);
            assert.equal(view.paintAt(0, 0), false);
          }
        }
      }
      $("fo-filter").value = "tiles";
      view.open("hud", "F0");
      assert.equal($("fo-filter").value, "tiles");
      const card = view.glyphCards.get("F0");
      assert.equal(doc.activeElement, card);
      assert.equal(card, view.glyphCards.get("00"));
      assert.match(card.className, /\bsel\b/);
      assert.equal($("fo-edit-block").hidden, true);
      view.select("BB");
      assert.deepEqual(view.address, ["hud", "glyph", "BB"]);
      assert.equal(doc.activeElement, view.glyphCards.get("BB"));
      assert.equal($("fo-edit-block").hidden, true);
    }
    const before = hashes.length;
    for (const [name, code] of [["missing", "BB"], ["f0", "AA"], ["f0", "bb"], ["f0", "0BB"]]) view.open(name, code);
    assert.equal(hashes.length, before);
    assert.deepEqual(view.address, ["hud", "glyph", "BB"]);
    view.open("f0", null);
    assert.deepEqual(view.address, ["f0"]);
    assert.equal($("fo-edit-block").hidden, true);
    assert.equal(edits.size, 0);
  } finally {
    globalThis.document = old;
  }
});

test("glyph art: the pack's glyphs, as the art file names them", () => {
  const [f0] = INDEX.font_sheets.sheets;
  const by = (sheet, code) => sheet.glyphs.find((g) => g.code === code);
  assert.deepEqual(glyphArt(INDEX, f0, by(f0, "37")), { id: "main:37", style: "main", code: "37", char: "Б", top: 0,
    cell: 14, widest: 16, width: 6, rows: ROWS6 });
  assert.deepEqual([glyphArt(INDEX, f0, by(f0, "105")).slot, glyphArt(INDEX, f0, by(f0, "105")).width], ["105", 10]);
  assert.equal(glyphArt(INDEX, f0, by(f0, "87")), null); // a copied ROM glyph
  assert.equal(glyphArt(INDEX, f0, by(f0, "BB")), null);
  assert.deepEqual(glyphArt(INDEX, HUD, by(HUD, "37")), { id: "hud:37", style: "hud", code: "37", char: "Б", top: 0,
    cell: 8, widest: 8, width: 5, rows: HUD.art[3] });
  assert.equal(glyphArt(INDEX, HUD, by(HUD, "87")), null);
});

test("painting and resizing art keep it inside the cell", () => {
  const art = { top: 1, cell: 2, widest: 4, width: 2, rows: ["#_", "s_"] };
  assert.deepEqual(paintArt(art, 1, 2, "#").rows, ["#_", "s#"]);
  assert.equal(paintArt(art, 1, 0, "#"), null, "above the cell rows");
  assert.equal(paintArt(art, 2, 1, "#"), null, "beyond the width");
  assert.equal(paintArt(art, 0, 1, "#"), null, "unchanged");
  assert.deepEqual(resizeArt(art, 3).rows, ["#__", "s__"]);
  assert.deepEqual(resizeArt(art, 1), { ...art, width: 1, rows: ["#", "s"] });
  assert.equal(resizeArt(art, 9).width, 4);
  assert.equal(resizeArt(art, 0).width, 1);
});

test("drafts redraw a sheet: font art and widths, sprite tiles and steps", () => {
  const [f0] = INDEX.font_sheets.sheets;
  const wide = ROWS6.map((r) => `${r}#`);
  const glyphs = fontGlyphs(f0, INDEX, null, [{ code: "37", width: 7, rows: wide }]);
  assert.equal(glyphs.width(0x37), 7);
  assert.equal(glyphs.get(0x37).pixels[3 * 16 + 6], INK);
  assert.equal(fontGlyphs(f0, INDEX).width(0x37), 6, "the data stays as it was");
  const steps = Array.from({ length: 256 }, () => 4);
  const stepped = { ...HUD, advances: steps };
  const art = HUD.art[3].map((r) => `${r}##`);
  const drafted = draftSheet(stepped, [{ code: "37", width: 7, rows: art }], 1);
  assert.deepEqual([drafted.art[3], drafted.advances[0x37], drafted.glyphs[1].width], [art, 6, 6]);
  assert.deepEqual([stepped.art[3], stepped.advances[0x37]], [HUD.art[3], 4]);
  assert.equal(draftSheet(HUD, [{ code: "37", width: 7, rows: art }]).glyphs[1].width, 4); // one advance for all
  assert.equal(draftSheet(HUD, []), HUD);
});

test("drafts over the index: widths, art, copies, shadow ends and sprite steps", () => {
  const steps = Array.from({ length: 256 }, () => 4);
  const index = { ...INDEX, fonts: { ...INDEX.fonts, sprites: { hud: { advance: 4, tiles: 3, table: TABLE, steps, shared: 1 } } } };
  const wide = ROWS6.map((r) => `${r}_`); // one column more, background
  const drafts = [{ style: "main", code: "37", width: 7, rows: wide },
    { style: "main", code: "87", width: 5, rows: ROWS6.map((r) => r.slice(0, 5)) }, // a copied letter drawn
    { style: "main", code: "105", width: 2, rows: ROWS6.map((r) => r.slice(0, 2)) }, // ink in its last column
    { style: "hud", code: "37", width: 7, rows: HUD.art[3].map((r) => `${r}##`) },
    { style: "hud", code: "87", width: 7, rows: HUD.art[3].map((r) => `${r}##`) }];
  const drafted = draftFonts(index, drafts);
  const main = drafted.fonts.styles.main;
  assert.deepEqual([main.pack, main.copy, main.shadow], [{ 37: 7, 87: 5, 105: 2 }, {}, ["37", "87"]]);
  assert.deepEqual([drafted.glyphs.main.art["37"], drafted.glyphs.main.art["87"].length], [wide, 14]);
  const glyphs = styleGlyphs(drafted).main;
  assert.deepEqual([glyphs.width(0x37), glyphs.width(0x87), glyphs.get(0x87).exact], [7, 5, true]);
  assert.equal(drafted.fonts.sprites.hud.steps[0x37], 6, "its width less the shared outline");
  assert.equal(drafted.fonts.sprites.hud.steps[0x87], 6, "a drawn copy takes its own step");
  assert.equal(drafted.fonts.sprites.hud.table[0x87], 4);
  assert.deepEqual([INDEX.fonts.styles.main.pack["37"], steps[0x37], INDEX.glyphs.main.art["37"]], [6, 4, ROWS6]);
  assert.equal(draftFonts(index, []), index);
  const fixed = { ...index, fonts: { ...index.fonts, sprites: { hud: { advance: 4, tiles: 3, table: TABLE } } } };
  assert.equal(draftFonts(fixed, drafts).fonts.sprites.hud.table[0x87], 4);
  assert.equal(draftFonts(fixed, drafts).fonts.sprites.hud.steps, undefined);
});

test("the editor grid: one square a pixel, outside the art dimmed, the width marked", () => {
  const calls = [];
  const canvas = { getContext: () => ({ fillRect(x, y, w, h) { calls.push([this.fillStyle, x, y, w, h]); } }) };
  drawArt(canvas, { top: 1, cell: 2, widest: 3, width: 2, rows: ["#s", "__"] }, false, 4);
  assert.deepEqual([canvas.width, canvas.height], [13, 65]);
  const at = (x, y) => calls.findLast(([, a, b, w, h]) => a <= x && x < a + w && b <= y && y < b + h)[0];
  const colors = artColors(false);
  assert.deepEqual([at(2, 6), at(6, 6), at(2, 10), at(10, 6), at(2, 2)], [colors["#"], colors.s, colors._, "#c8ccd4",
    "#c8ccd4"]);
  assert.equal(at(8, 30), "#e02020");
  assert.notEqual(artColors(true)._, colors._);
});

test("redrawing a glyph: a draft, the sample, the cell and the sheet follow it", () => {
  const { doc, $ } = page();
  const old = globalThis.document;
  globalThis.document = doc;
  try {
    const edits = new GlyphEdits(null, "ru", "p");
    let changed = 0;
    const view = new FontsView({ index: () => INDEX, rom: () => null, codec: () => CODEC, dataUrl: (path) => path,
      setHash() {}, edits: () => edits, changed: () => changed++ }, $);
    view.wire();
    view.reset("f0");
    assert.equal($("fo-edit-block").hidden, true);
    const cards = () => $("fo-grid").childNodes;
    assert.deepEqual(cards().map((c) => c.className.includes("editable")), [false, true, false, false, true]);
    cards()[4].onkeydown({ type: "keydown", key: "Enter" });
    assert.equal(view.editing, "105");
    cards()[1].onclick({ type: "click" });
    assert.equal(view.editing, "37");
    assert.equal($("fo-edit-block").hidden, false);
    assert.equal($("fo-edit-name").textContent, "Б · 37");
    assert.deepEqual($("fo-palette").childNodes.map((b) => b.textContent), ["ink", "shadow", "background"]);
    assert.deepEqual([$("fo-width").value, $("fo-width").max, $("fo-discard").disabled], ["6", "16", true]);
    assert.deepEqual([$("fo-art").width, $("fo-art").height], [16 * EDIT_ZOOM.font + 1, 16 * EDIT_ZOOM.font + 1]);
    assert.equal($("fo-art")["aria-label"], "Б · 37");
    assert.match($("fo-sample")["aria-label"], /^Sample text: /);
    assert.equal(cards()[1].childNodes[0].role, "img");
    assert.match(cards()[1].childNodes[0]["aria-label"], /Б/);
    assert.match(cards()[1].className, /\bsel\b/);
    const sample = $("fo-sample").calls;
    const cell = cards()[1].childNodes[0].calls;
    assert.equal(view.paintAt(0, 0), true); // Б's ink corner
    assert.equal(view.paintAt(0, 0), false, "the same pixel again");
    assert.equal(view.paintAt(9, 3), false, "beyond the width");
    assert.deepEqual(edits.get("main:37").rows[0], "#_____");
    assert.equal(changed, 1);
    assert.notDeepEqual($("fo-sample").calls, sample);
    assert.notDeepEqual(cards()[1].childNodes[0].calls, cell);
    assert.match(cards()[1].textContent, /redrawn$/);
    assert.match($("fo-edit-note").textContent, /^Redrawn in this browser.*writes it to assets\/lang\/xx\/fonts\/main\.txt\.$/);
    assert.equal($("fo-discard").disabled, false);
    view.setInk("s");
    assert.match($("fo-palette").childNodes[1].className, /\bon\b/);
    $("fo-width").value = "8";
    $("fo-width").onchange({ target: $("fo-width") });
    assert.deepEqual([edits.get("main:37").width, edits.get("main:37").rows[3]], [8, "_##s____"]);
    assert.match(cards()[1].textContent, /8 px/);
    view.paintEvent({ offsetX: 7 * EDIT_ZOOM.font + 3, offsetY: 4 * EDIT_ZOOM.font + 3 });
    assert.equal(edits.get("main:37").rows[4][7], "s");
    view.step(1);
    assert.equal(view.editing, "37", "the same style keeps the glyph in the editor");
    assert.match(cards()[1].textContent, /8 px.*redrawn$/);
    $("fo-discard").onclick();
    assert.equal(edits.size, 0);
    assert.doesNotMatch(cards()[1].textContent, /redrawn/);
    view.open("hud");
    assert.match($("fo-sheet")["aria-label"], /Tile sheet/);
    assert.equal(view.editing, null);
    assert.equal($("fo-edit-block").hidden, true);
    view.select("37");
    assert.deepEqual($("fo-palette").childNodes.map((b) => b.textContent),
      ["ink, index 1", "outline, index 12", "transparent, index 0"]);
    assert.deepEqual([$("fo-width").max, $("fo-art").width], ["8", 8 * EDIT_ZOOM.sprite + 1]);
    const sheet = $("fo-sheet").calls;
    view.setInk("#");
    assert.equal(view.paintAt(4, 7), true);
    assert.deepEqual(edits.changes(), [{ style: "hud", char: "Б", width: 5, rows: [...HUD.art[3].slice(0, 7), "ssss#"],
      base: { width: 5, rows: HUD.art[3] } }]);
    assert.notDeepEqual($("fo-sheet").calls, sheet);
    view.select("87"); // a sprite font's copied letter draws its base letter's tile
    assert.equal($("fo-edit-block").hidden, true);
  } finally {
    globalThis.document = old;
  }
});

test("a copied letter starts from the ROM glyph it copies and keeps its source", () => {
  const [f0] = INDEX.font_sheets.sheets;
  const be = f0.glyphs.find((g) => g.code === "87");
  const own = romSheet(f0, rom());
  const art = glyphArt(INDEX, f0, be, own);
  assert.deepEqual([art.id, art.char, art.copy, art.width, art.rows.length, art.rows[0]], ["main:87", "А", "BB", 7, 14, "#______"]);
  assert.equal(glyphArt(INDEX, f0, be), null, "without the ROM nothing to start from");
  const draft = { width: 3, rows: Array(14).fill("_#s") };
  assert.deepEqual(glyphArt(INDEX, f0, be, null, draft).rows, draft.rows, "its draft without the ROM");
  assert.deepEqual(glyphArt(INDEX, f0, be, own, draft).rows, art.rows, "the ROM glyph stays its base");
  const { doc, $ } = page();
  const old = globalThis.document;
  globalThis.document = doc;
  try {
    const edits = new GlyphEdits(null, "ru", "p");
    let image = null;
    const index = { ...INDEX, font_sheets: { ...INDEX.font_sheets, sheets: [...INDEX.font_sheets.sheets,
      { ...sheet("s0", 0x9000, 0x600), style: "small", format: "8x16" }] } };
    const view = new FontsView({ index: () => index, rom: () => image, codec: () => CODEC, dataUrl: (path) => path,
      setHash() {}, edits: () => edits, changed() {} }, $);
    view.wire();
    view.reset("f0");
    const card = () => $("fo-grid").childNodes[2];
    assert.match(card().title, /load your ROM to redraw it from the glyph it copies$/);
    assert.doesNotMatch(card().className, /editable/);
    image = rom();
    view.refresh();
    assert.match(card().className, /editable/);
    card().onclick({ type: "click" });
    assert.match($("fo-edit-note").textContent, /^A copy of the ROM glyph BB: draw on the grid to make it a letter of its own\. /);
    assert.match($("fo-edit-note").textContent, /sets its charmap source to glyph\. Draw it in hud, s0 too: a letter of its own needs art in every style\.$/);
    assert.equal(view.paintAt(1, 0), true);
    assert.deepEqual(edits.changes(), [{ style: "main", char: "А", width: 7, rows: ["##_____", ...art.rows.slice(1)],
      base: { source: "copy:BB" } }]);
    assert.match(card().textContent, /redrawn$/);
    image = null;
    view.refresh();
    view.select("87");
    assert.equal($("fo-edit-block").hidden, false, "its draft stays editable without the ROM");
    $("fo-discard").onclick();
    assert.equal(edits.size, 0);
  } finally {
    globalThis.document = old;
  }
});

test("a widened letter of a stepping sprite font steps its width less the shared column", () => {
  const { doc, $ } = page();
  const old = globalThis.document;
  globalThis.document = doc;
  try {
    const steps = Array.from({ length: 256 }, () => 4);
    const hud = { ...HUD, advances: steps };
    const index = { ...INDEX, fonts: { ...INDEX.fonts, sprites: { hud: { shared: 1 } } },
      font_sheets: { ...INDEX.font_sheets, sheets: [hud] } };
    const edits = new GlyphEdits(null, "ru", "p");
    const view = new FontsView({ index: () => index, rom: () => null, codec: () => CODEC, dataUrl: (path) => path,
      setHash() {}, edits: () => edits }, $);
    view.wire();
    view.reset("hud");
    view.select("37");
    view.setWidth(7);
    assert.equal(view.shown("hud").advances[0x37], 6);
    assert.match($("fo-grid").childNodes[1].textContent, /^Б37 #36 pxaddedredrawn$/);
    assert.equal(hud.advances[0x37], 4);
  } finally {
    globalThis.document = old;
  }
});

test("a copied sprite letter gets its own tile, steps and export record", () => {
  const { doc, $ } = page();
  const old = globalThis.document;
  globalThis.document = doc;
  try {
    const steps = Array(256).fill(4);
    const hud = { ...HUD, advances: steps };
    const index = { ...INDEX, fonts: { ...INDEX.fonts,
      sprites: { hud: { advance: 4, tiles: 3, table: TABLE, steps, shared: 1 } } },
    font_sheets: { ...INDEX.font_sheets, sheets: [hud] } };
    const edits = new GlyphEdits(null, "xx", "p");
    let image = rom();
    const view = new FontsView({ index: () => index, rom: () => image, codec: () => CODEC,
      dataUrl: (p) => p, setHash() {}, edits: () => edits }, $);
    view.wire();
    view.reset("hud");
    $("fo-grid").childNodes[2].onclick({ type: "click" });
    assert.equal(view.editing, "87");
    assert.equal(view.target().art.rows[0], "s#______");
    view.setWidth(6);
    assert.equal(view.paintAt(2, 0), true);
    assert.equal(view.paintAt(3, 0), true);
    const shown = view.shown("hud");
    assert.deepEqual([shown.count, shown.table[0x87], shown.table[0xBB], shown.advances[0x87]], [5, 4, 1, 5]);
    assert.deepEqual(shown.art[3], HUD.art[3]);
    assert.equal(shown.art[4][0], "s###__");
    assert.equal(spriteRuns("АА", new SpriteTiles(shown, image), CODEC).advance, 10);
    assert.equal($("fo-table").childNodes[1].childNodes[8].childNodes[8].textContent, "4");
    assert.match($("fo-facts").textContent, /Pack tiles2/);
    const records = exportDoc("xx", "p", [], [], [], edits.changes()).glyphs;
    assert.deepEqual(records, [{ style: "hud", char: "А", width: 6,
      rows: ["s###__", ...Array(7).fill("______")], base: { source: "copy:BB" } }]);
    const draft = draftFonts(index, edits.all()).fonts.sprites.hud;
    assert.deepEqual(draft.table, shown.table);
    assert.deepEqual(draft.steps, shown.advances);
    image = null;
    view.refresh();
    assert.equal($("fo-edit-block").hidden, false);
    view.discard();
    assert.equal(view.shown("hud"), hud);
    assert.equal(edits.size, 0);
  } finally {
    globalThis.document = old;
  }
});

test("sprite drafts allocate every drawn tile in byte order", () => {
  const hud = { ...HUD, glyphs: HUD.glyphs.map((g) => g.code === "87" ? { ...g, code: "10" } : g) };
  const drafted = draftSheet(hud, [{ code: "10", width: 3, rows: Array(8).fill("s#s") }]);
  assert.deepEqual([drafted.table[0x10], drafted.table[0x37]], [3, 4]);
  assert.deepEqual(drafted.art[4], HUD.art[3]);
  assert.equal(drafted.glyphs.find((g) => g.code === "37").tile, 4);
  assert.deepEqual([HUD.count, HUD.table[0x37], HUD.table[0x87]], [4, 3, 1]);
});

test("named base and F9 slots start art and export new slot blocks", () => {
  const { doc, $ } = page();
  const old = globalThis.document;
  globalThis.document = doc;
  try {
    const font = { ...INDEX.font_sheets.sheets[0], glyphs: [
      { code: "34", tile: 0x34, name: "LV", source: "base" },
      { code: "106", tile: 0x106, name: "F9 06", source: "base" },
    ] };
    const index = { ...INDEX, font_sheets: { ...INDEX.font_sheets, sheets: [font] } };
    const edits = new GlyphEdits(null, "xx", "p");
    const view = new FontsView({ index: () => index, rom: () => null, codec: () => CODEC,
      dataUrl: (p) => p, setHash() {}, edits: () => edits }, $);
    view.wire();
    view.reset();
    for (let i = 0; i < 2; i++) {
      $("fo-grid").childNodes[i].onclick({ type: "click" });
      assert.equal($("fo-edit-block").hidden, false);
      view.setWidth(3);
      assert.equal(view.paintAt(1, 2), true);
      const glyphs = fontGlyphs(font, index, null, edits.of("main"));
      assert.equal(glyphs.get(font.glyphs[i].tile).pixels[2 * 16 + 1], INK);
    }
    const records = exportDoc("xx", "p", [], [], [], edits.changes()).glyphs;
    const rows = Array.from({ length: 14 }, (_, i) => i === 2 ? "_#_" : "___");
    assert.deepEqual(records, ["106", "034"].map((slot) =>
      ({ style: "main", slot, width: 3, rows, base: { source: "rom" } })));
    view.discard();
    assert.equal(edits.size, 1);
    assert.equal(view.target().draft, false);
  } finally {
    globalThis.document = old;
  }
});

test("declared base letters export slot art without changing character encoding", () => {
  const font = INDEX.font_sheets.sheets[0];
  const original = font.glyphs.find((g) => g.code === "BB");
  const declared = { ...original, redraw: true };
  assert.equal(glyphArt(INDEX, font, original, romSheet(font, rom())), null);
  const art = glyphArt(INDEX, font, declared, romSheet(font, rom()));
  assert.equal(art.slot, "0BB");
  assert.equal(art.char, undefined);
  assert.equal(art.source, "rom");
  assert.equal(art.rows[0][0], "#");
  const edits = new GlyphEdits(null, "xx", "p");
  edits.set(art, paintArt(art, 2, 2, "#"));
  const record = exportDoc("xx", "p", [], [], [], edits.changes()).glyphs[0];
  assert.deepEqual({ style: record.style, slot: record.slot, base: record.base },
    { style: "main", slot: "0BB", base: { source: "rom" } });
  assert.equal(CODEC.encode("A")[0], 0xBB);
  const again = glyphArt(INDEX, font, declared, null, edits.get(art.id));
  assert.deepEqual(again.rows, record.rows);
  assert.equal(glyphArt(INDEX, HUD, { ...HUD.glyphs[3], redraw: true }, romSheet(HUD, rom())), null);
});
