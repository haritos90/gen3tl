// The Keyboards view: page order, cells, window, view.
import assert from "node:assert/strict";
import { test } from "node:test";

import { CODEC, PACK, STYLE, fakeDocument, stubPage } from "./fixture.mjs";
import { Codec, Glyphs } from "../js/font.js";
import { draftFonts, styleGlyphs } from "../js/fonts.js";
import {
  KeyboardsView, SOURCES, chatRow, drawPage, keyCell, keyEdit, keyboardWithEnglish, marksOf, measuredWidths, nextPage, pageCells,
  pageOrder, printedGlyphs,
} from "../js/keyboards.js";
import { GlyphEdits, KeyboardEdits, exportDoc } from "../js/store.js";
import { t } from "../js/ui.js";

const key = (char, code, source = "base", extra = {}) => ({ char, code, source, ...extra });
const glyph = (char, code, x, width = 6) => ({ char, code, x, width });
const row = (keys, display, cells) => ({ keys: { id: keys, text: "" }, display: { id: display, text: "" }, cells });

// Two rows of three keys; symbols has two columns.
const KB = {
  first_page: 1, style: "main", window: { width: 152, height: 64, row_height: 16, top: 1, cursor: { left: 8, width: 12 } },
  pages: [{ name: "symbols", keyboard: 1, look: 0 }, { name: "pack", keyboard: 0, look: 2 },
    { name: "LATIN", keyboard: 2, look: 1 }],
  keyboards: [
    { name: "pack", like: 0, base: false, columns: 3, column_x: [0, 12, 24], rows: [
      row("k0", "d0", [
        { key: key("Б", "37", "glyph"), glyph: glyph("Б", "37", 11) },
        { key: key("б", "42", "glyph"), glyph: glyph("б", "42", 23, 5) },
        { key: key("A", "BB"), glyph: glyph("B", "BC", 35) }]),
      row("k1", "d1", [
        { key: key("ж", null, "none"), glyph: glyph("ж", null, 11, 0) },
        { key: key(" ", "00"), glyph: null },
        { key: key("Б", "37", "glyph"), glyph: glyph("Б", "37", 148) }])] },
    { name: "symbols", like: 2, base: false, columns: 2, column_x: [0, 22], rows: [
      row("k8", "d8", [{ key: key("1", "A2"), glyph: glyph("1", "A2", 11) }, { key: key("!", "AB"), glyph: glyph("!", "AB", 33) },
        { key: key(" ", "00"), glyph: null }])] },
    { name: "LATIN", like: 1, base: true, columns: null, rows: [
      row("k4", "d4", [{ key: key("A", "BB"), glyph: glyph("A", "BB", 11) }])] },
  ],
};

const ART = { top: 0, rows: 14, art: { 37: ["######", "#____s", "#____s", "#####s", "#____#", "#____#", "######"] } };

const CHAT = {
  style: "small", columns: 5, column_width: 8,
  pages: ["chat_upper", "chat_lower", "chat_symbols"].map((name, p) => ({
    name, rows: Array.from({ length: 10 }, (_, r) => ({
      name: `${name}_${r + 1}`, id: `${name}/${r}`, text: ["БAБA ", "бaбa ", "{F9 F1}A.1 "][p],
      widths: { "37": 5, "42": 4, BB: 5, D5: 5, "1F1": 8, AD: 3, A2: 5, "00": 3 }, bytes: p === 2 ? 10 : 5,
    })),
  })),
};

test("keyboard targets distinguish numeric names and focus read-only chat rows", (context) => {
  const { $ } = stubPage(context, "editor");
  const create = document.createElement;
  document.createElement = (tag) => {
    const node = create(tag);
    node.focus = () => { document.activeElement = node; };
    node.scrollIntoView = () => { node.revealed = true; };
    return node;
  };
  const keyboard = { ...KB, pages: KB.pages.map((page, i) => i === 2 ? { ...page, name: "0" } : page), chat: CHAT };
  const hashes = [];
  let edits = null;
  const view = new KeyboardsView({
    index: () => ({ keyboard }), codec: () => new Codec(CODEC, PACK), glyphs: () => null,
    keyboardEdits: () => edits, setHash: (target) => hashes.push(target), changed() {},
  }, $);
  view.reset(null, false);
  view.openTarget("chat_lower_2", "chat");
  const rows = $("k-chat").querySelectorAll("tbody")[1].children;
  assert.equal(document.activeElement, rows[1]);
  assert.equal(rows[1].getAttribute("tabindex"), "-1");
  assert.equal(rows[1].revealed, true);
  assert.equal(rows[1].querySelector("input").disabled, true);
  assert.deepEqual(hashes.at(-1), ["chat", "chat_lower_2"]);
  view.openTarget("CHAT_LOWER_2", "chat");
  assert.equal(document.activeElement, rows[1]);
  assert.deepEqual(view.target, ["chat", "chat_lower_2"]);
  view.openTarget("0", "page");
  assert.equal(view.cur, 2);
  assert.deepEqual(view.target, ["page", "0"]);
  view.openTarget("0");
  assert.equal(view.cur, 0);
  assert.deepEqual(view.target, ["page", "symbols"]);
  edits = new KeyboardEdits(null, "xx", "p");
  view.openTarget("chat_lower_2", "chat");
  view.discard();
  assert.deepEqual(view.target, ["chat", "chat_lower_2"]);
  assert.equal(document.activeElement, $("k-chat").querySelectorAll("tbody")[1].children[1].querySelector("input"));
});

test("chat rows fit five cursor slots with extended symbols and spaces", () => {
  const codec = new Codec(CODEC, PACK);
  const row = CHAT.pages[2].rows[0];
  const widthOf = (code) => row.widths[code.toString(16).toUpperCase().padStart(2, "0")] ?? 5;
  const result = chatRow(row.text, CHAT, row, codec, widthOf);
  assert.equal(result.bytes, 6);
  assert.equal(result.width, 40);
  assert.deepEqual(result.glyphs.map((g) => [g.code, g.x]), [["1F1", 0], ["BB", 8], ["AD", 16], ["A2", 24], ["00", 32]]);
  assert.throws(() => chatRow("AAAAAA", CHAT, row, codec, widthOf), /Expected 5 keys, got 6/);
  assert.throws(() => chatRow("AAAA", CHAT, row, codec, widthOf), /Expected 5 keys, got 4/);
  assert.throws(() => chatRow("AAAA  ", CHAT, row, codec, widthOf), /got 6/);
  assert.throws(() => chatRow("AAAAA", CHAT, row, codec, () => 9), /45 exceeds 40 px/);
  assert.throws(() => chatRow(row.text, CHAT, { ...row, bytes: 5 }, codec, widthOf), /6 bytes.*holds 5/);
  assert.throws(() => chatRow("{PLAYER}AAAA", CHAT, row, codec, widthOf), /not a keyboard glyph/);
  assert.throws(() => chatRow("{COLOR 01}AAAA", CHAT, row, codec, widthOf), /not a keyboard glyph/);
});

test("loaded chat pages render, reject overflow, and export accepted row drafts", (context) => {
  const { $ } = stubPage(context, "editor");
  const doc = globalThis.document;
  const create = doc.createElement;
  doc.createElement = (tag) => tag === "canvas" ? canvasNode({ createElement: create }) : create(tag);
  const store = new Map();
  const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v), removeItem: (k) => store.delete(k) };
  let edits = new KeyboardEdits(storage, "xx", "p");
  let index = { keyboard: { ...KB, chat: CHAT } };
  const fonts = { fonts: { styles: { small: { ...STYLE, format: "8x16", pack: { "37": 5, "42": 4 } } } },
    glyphs: { small: ART } };
  const drafts = new GlyphEdits(null, "xx", "p");
  let changed = 0;
  const view = new KeyboardsView({
    index: () => index, glyphs: (style) => style === "small" ? styleGlyphs(draftFonts(fonts, drafts.all())).small : null,
    codec: () => new Codec(CODEC, PACK),
    keyboardEdits: () => edits, changed: () => changed++, setHash: () => {},
  }, $);
  view.reset();
  const tables = () => $("k-chat").querySelectorAll("table");
  const input = () => $("k-chat").querySelectorAll("input")[0];
  const apply = () => $("k-chat").querySelectorAll("button")[0].listeners.click();
  assert.equal($("count-keyboards").textContent, "6");
  assert.equal(tables().length, 3);
  assert.deepEqual(tables().map((table) => table.querySelector("tbody").children.length), [10, 10, 10]);
  assert.deepEqual($("k-chat").querySelectorAll("input").map((n) => n.value), CHAT.pages.flatMap((p) => p.rows.map((r) => r.text)));
  assert.match($("k-chat").textContent, /Chat page 1.*Chat page 2.*Chat page 3/);
  assert.match($("k-chat").textContent, /5 keys · 40\/40 px · 6\/10 bytes/);
  assert.equal($("k-chat").querySelector("canvas").style.width, "80px");
  assert.equal($("k-chat").querySelector("canvas").getAttribute("role"), "img");
  assert.match($("k-chat").querySelector("canvas").getAttribute("aria-label"), /Chat page 1, row 1/);
  assert.equal($("k-preview").querySelector("canvas").getAttribute("role"), "img");
  assert.match($("k-preview").querySelector("canvas").getAttribute("aria-label"), /Screen preview/);
  assert.equal($("k-grid").querySelector("canvas").getAttribute("aria-hidden"), "true");
  input().value = "AAAAAA";
  apply();
  assert.match($("k-chat").textContent, /Expected 5 keys, got 6/);
  assert.equal(input().value, "AAAAAA");
  assert.equal(edits.size, 0);
  assert.equal(changed, 0);
  input().value = "ББББ ";
  apply();
  assert.equal(edits.size, 1);
  assert.equal(changed, 1);
  assert.equal(edits.row("chat_upper_1"), "ББББ ");
  assert.deepEqual(exportDoc("xx", "p", [], [], [], [], edits.changes()).keyboard,
    { rows: { chat_upper_1: "ББББ " }, base: { rows: { chat_upper_1: "БAБA " } } });
  edits = new KeyboardEdits(storage, "xx", "p");
  view.refresh();
  assert.equal(input().value, "ББББ ");
  drafts.set({ id: "small:37", style: "small", code: "37", char: "Б", width: 5, rows: ART.art[37] },
    { width: 9, rows: ART.art[37].map((r) => r.padEnd(9, "_")) });
  view.refresh();
  assert.match($("k-chat").textContent, /Row width 44 exceeds 40 px/);
  input().value = "БAAAA";
  apply();
  assert.match($("k-chat").textContent, /Row width 41 exceeds 40 px/);
  assert.equal(edits.row("chat_upper_1"), "ББББ ");
  view.discard();
  assert.equal(edits.size, 0);
  drafts.remove("small:37");
  view.refresh();
  assert.doesNotMatch($("k-chat").textContent, /exceeds/);
  index = { keyboard: { ...KB, chat: { ...CHAT, pages: CHAT.pages.map((p) => ({ ...p,
    rows: p.rows.map((r) => ({ ...r, text: "AAAA " })),
  })) } } };
  view.reset();
  assert.equal(input().value, "AAAA ");
  index = { keyboard: KB };
  view.reset();
  assert.equal($("k-chat").textContent, "");
});

/** A canvas whose context paints into a colour grid. */
function canvasNode(doc) {
  const node = doc.createElement("canvas");
  node.style = {};
  node.getContext = () => {
    const grid = Array.from({ length: node.height }, () => Array(node.width).fill(null));
    node.grid = grid;
    return {
      fillStyle: null,
      fillRect(x, y, w, h) {
        for (let j = y; j < y + h; j++) for (let i = x; i < x + w; i++) if (grid[j]?.[i] !== undefined) grid[j][i] = this.fillStyle;
      },
    };
  };
  return node;
}

test("pages follow Select from the page the screen opens on", () => {
  assert.deepEqual(pageOrder(KB), [1, 2, 0]);
  assert.equal(nextPage(KB, 2), 0);
  assert.equal(nextPage(KB, 1), 2);
  assert.deepEqual(pageOrder({ ...KB, first_page: 0 }), [0, 1, 2]);
});

test("key cells carry position, tile, reach and review marks", () => {
  const rows = pageCells(KB, 1);
  assert.deepEqual([rows[0][1].glyph.x, rows[0][1].y, rows[0][1].tile], [23, 1, [2, 0]]);
  assert.deepEqual(rows[1][2].tile, [18, 2]);
  assert.equal(rows[0][2].differs, true); // enters A, prints B
  assert.equal(rows[1][0].missing, true);
  assert.equal(rows[1][1].unprinted, true);
  const symbols = pageCells(KB, 0)[0];
  assert.deepEqual(symbols.map((c) => c.reachable), [true, true, false]);
  assert.equal(symbols[2].unprinted, false); // outside the columns
  assert.deepEqual(marksOf(KB, 1), { keys: 6, missing: 1, differs: 2, misplaced: 1 });
  assert.deepEqual(marksOf(KB, 2), { keys: 1, missing: 0, differs: 0, misplaced: 0 });
});

test("a glyph outside its cursor frame is marked", () => {
  const rows = pageCells(KB, 1);
  assert.deepEqual(rows[0].map((c) => c.cursor), [[8, 20], [20, 32], [32, 44]]);
  assert.deepEqual(rows[0].map((c) => c.misplaced), [false, false, false]); // x 11, 23 and 35
  assert.equal(rows[1][2].misplaced, true); // Б at x 148 under the frame 32-43
  assert.equal(rows[1][1].misplaced, false); // a key without a printed glyph
  assert.deepEqual(pageCells(KB, 2)[0].map((c) => [c.cursor, c.misplaced]), [[null, false]]); // no column table
  const shifted = structuredClone(KB);
  shifted.keyboards[1].rows[0].cells[1].glyph.x = 29; // ! one pixel past the frame 30-41
  assert.equal(pageCells(shifted, 0)[0][1].misplaced, true);
  shifted.keyboards[1].rows[0].cells[1].glyph.x = 30;
  assert.equal(pageCells(shifted, 0)[0][1].misplaced, false);
});

test("the window is drawn with pack art, stand-ins and the page colours", () => {
  const doc = fakeDocument();
  globalThis.document = doc;
  try {
    const glyphs = new Glyphs(STYLE, ART);
    const codec = new Codec(CODEC, PACK);
    const canvas = canvasNode(doc);
    const { approximate } = drawPage(canvas, KB, 1, glyphs, codec, 3);
    assert.deepEqual([canvas.width, canvas.height, canvas.style.width], [160, 72, "480px"]);
    const at = (x, y) => canvas.grid[4 + y][4 + x]; // inside the frame strip
    assert.equal(canvas.grid[0][0], "#f0c8a0"); // look 2
    assert.equal(at(0, 0), "#d09870"); // base keyboard 0
    assert.equal(at(11, 1), "#f8f8f8"); // Б, first ink pixel
    assert.equal(at(16, 2), "#606060"); // its shadow column
    assert.equal(at(151, 17), "#f8f8f8"); // the last column inside
    assert.equal(at(152, 17), "#f0c8a0"); // clipped: the frame strip stays
    assert.equal(approximate, 2); // б has no art here, B is a ROM glyph
    drawPage(canvas, KB, 0, glyphs, codec, 1);
    assert.equal(canvas.grid[0][0], "#c0e098");
    assert.equal(canvas.grid[4][4], "#90b868");
  } finally {
    delete globalThis.document;
  }
});

test("the view lists the pages in Select order and renders the chosen one", () => {
  const doc = fakeDocument();
  const create = doc.createElement;
  doc.createElement = (tag) => {
    const node = tag === "canvas" ? canvasNode({ createElement: create }) : create(tag);
    node.setAttribute ??= (k, v) => { node[k] = v; };
    node.addEventListener = (type, fn) => { node[`on${type}`] = fn; };
    node.querySelector = () => null;
    return node;
  };
  globalThis.document = doc;
  const nodes = new Map();
  const $ = (id) => {
    if (!nodes.has(id)) nodes.set(id, doc.createElement("div"));
    return nodes.get(id);
  };
  try {
    const hashes = [];
    const opened = [];
    const glyphs = new Glyphs(STYLE, ART);
    let index = { keyboard: KB };
    const view = new KeyboardsView({
      index: () => index, glyphs: () => glyphs, codec: () => new Codec(CODEC, PACK), exact: () => false,
      loadRom: () => {}, openEntry: (id) => opened.push(id), setHash: (n) => hashes.push(n),
    }, $);
    view.reset();
    assert.equal(view.cur, 1);
    assert.equal($("count-keyboards").textContent, "3");
    const list = $("k-list").childNodes;
    assert.deepEqual(list.map((r) => r.childNodes[0].childNodes[0].textContent), ["1. pack", "2. LATIN", "3. symbols"]);
    assert.match(list[0].textContent, /opens here/);
    assert.match(list[0].textContent, /1 without glyph/);
    assert.match(list[1].textContent, /base ROM keys/);
    assert.equal($("k-name").textContent, "pack");
    assert.equal($("k-switch").textContent, t("Select → {next}", { next: "LATIN" }));
    assert.match($("k-note").textContent, /2 glyphs are project-drawn stand-ins/);
    const cells = $("k-grid").childNodes[0].childNodes[1].childNodes[0].childNodes;
    assert.equal(cells[1].title, `${t(SOURCES.glyph)} · ${t("cursor frame x {from}–{to}", { from: 8, to: 19 })}`);
    assert.match(cells[1].textContent, /Б0x37x 11tile 1,0pack glyph/);
    assert.match(cells[3].textContent, /prints B/);
    assert.match($("k-facts").textContent, /Columns3/);
    assert.match($("k-facts").textContent, /Cursor columnsx 0, x 12, x 24/);
    assert.match(list[0].textContent, /1 off the cursor/);
    const second = $("k-grid").childNodes[0].childNodes[1].childNodes[1].childNodes;
    assert.match(second[3].textContent, /off the cursor/); // row 2, column 3
    assert.doesNotMatch(cells[3].textContent, /off the cursor/);
    const link = $("k-rows").childNodes[0].childNodes[1].childNodes[0].childNodes[1].childNodes[0];
    link.onclick();
    assert.deepEqual(opened, ["k0"]);
    view.step(1);
    view.step(5);
    assert.deepEqual([view.cur, hashes], [0, [["page", "LATIN"], ["page", "symbols"]]]);
    assert.match($("k-facts").textContent, /Columns2/);
    view.reset("2");
    assert.equal(view.cur, 2);
    assert.match($("k-source-badge").textContent, /base ROM/);
    view.reset("9");
    assert.equal(view.cur, 1);
    view.reset("");
    assert.equal(view.cur, 1); // an empty hash tail opens the first page
    index = null;
    view.reset();
    view.refresh();
    assert.equal(view.cur, null);
    assert.equal($("count-keyboards").textContent, "");
    assert.match($("k-preview").textContent, /no naming keyboard/);
  } finally {
    delete globalThis.document;
  }
});

test("each page shows its label graphic from the Pictures data", () => {
  const doc = fakeDocument();
  const create = doc.createElement;
  doc.createElement = (tag) => {
    const node = create(tag);
    node.setAttribute ??= (k, v) => { node[k] = v; };
    node.addEventListener = (type, fn) => { node[`on${type}`] = fn; };
    node.querySelector = () => null;
    if (tag === "canvas") {
      node.style = {};
      node.getContext = () => ({
        putImageData(image) { node.image = image; },
        drawImage(source, x, y, w, h) { node.drawn = { image: source.image, w, h }; },
        setLineDash() {}, strokeRect() {}, fillRect() {},
      });
    }
    return node;
  };
  globalThis.document = doc;
  globalThis.ImageData = class { constructor(data, width, height) { Object.assign(this, { data, width, height }); } };
  const nodes = new Map();
  const $ = (id) => {
    if (!nodes.has(id)) nodes.set(id, doc.createElement("div"));
    return nodes.get(id);
  };
  try {
    const calls = [];
    const gfx = { rom: true, preview: (id, edited) => {
      calls.push([id, edited]);
      return [{ label: "Screen layout", width: 2, height: 1, pixels: new Uint8Array([0, 1]), palette: ["#20242b", "#f8f8f8"] }];
    } };
    let state = "ready";
    const opened = [];
    const index = {
      keyboard: { ...KB, labels: ["G3", "G1", "G2"] },
      graphics: { inventory: [{ id: "G1", rom_offset: "0x10" }, { id: "G2", rom_offset: "0x20" }, { id: "G3", rom_offset: "" }],
        edits: { G2: { areas: [[0, 0, 2, 1]] } } },
    };
    const view = new KeyboardsView({
      index: () => index, glyphs: () => null, codec: () => new Codec(CODEC, PACK), exact: () => false,
      gfx: () => gfx, gfxState: () => state, loadRom: () => {}, openEntry: () => {},
      openPicture: (id) => opened.push(id), setHash: () => {},
    }, $);
    view.reset(); // the pack page, look 2
    const box = $("k-label");
    assert.deepEqual(calls, [["G2", true]]);
    assert.match(box.textContent, /G2/);
    assert.match(box.textContent, new RegExp(t("translated in the project")));
    assert.match(box.textContent, new RegExp(t("Shown on the Select button of {page}", { page: "symbols" })));
    const canvas = box.childNodes[1];
    assert.equal(canvas.tagName, "CANVAS");
    assert.deepEqual([canvas.width, canvas.height, canvas.drawn.w, canvas.drawn.h], [4, 2, 4, 2]); // zoom 2
    assert.deepEqual([...canvas.drawn.image.data.slice(4, 8)], [248, 248, 248, 255]);
    box.childNodes[0].childNodes[0].onclick();
    assert.deepEqual(opened, ["G2"]);
    view.open(2); // LATIN, look 1: the ROM's label
    assert.deepEqual(calls.at(-1), ["G1", false]);
    assert.match(box.textContent, new RegExp(t("the ROM's label")));
    view.open(0); // symbols, look 0: no ROM source
    assert.equal(calls.length, 2);
    assert.match(box.textContent, /no ROM source/);
    gfx.rom = false;
    view.open(1);
    assert.match(box.textContent, /Load your ROM to see the label/);
    state = "loading";
    view.open(1);
    assert.match(box.textContent, /Loading the gen3tl bundle/);
    index.keyboard = { ...KB };
    view.reset();
    assert.match(box.textContent, /names no label graphic/);
  } finally {
    delete globalThis.document;
    delete globalThis.ImageData;
  }
});

// A pack keyboard of three printed keys.
const EDIT_KB = {
  first_page: 0, style: "main", labels: null,
  window: { width: 152, height: 64, row_height: 16, top: 1, cursor: { left: 8, width: 12 } },
  pages: [{ name: "pack", keyboard: 0, look: 2 }, { name: "LATIN", keyboard: 1, look: 1 }],
  keyboards: [
    { name: "pack", like: 0, base: false, columns: 3, column_x: [0, 12, 24], rows: [{
      keys: { id: "k0", text: "Ббa", name: "lower_keys_1" },
      display: { id: "d0", text: "{CLEAR 0B}Б{CLEAR 06}б{CLEAR 07}a", name: "lower_display_1" },
      cells: [{ key: key("Б", "37", "glyph"), glyph: glyph("Б", "37", 11) }, { key: key("б", "42", "glyph"), glyph: glyph("б", "42", 23, 5) },
        { key: key("a", "D5"), glyph: glyph("a", "D5", 35) }] }] },
    { name: "LATIN", like: 1, base: true, columns: 3, column_x: [0, 12, 24], rows: [{
      keys: { id: "k4", text: "AB.", name: "upper_keys_1" },
      display: { id: "d4", text: "{CLEAR 0B}A{CLEAR 06}B{CLEAR 06}.", name: "upper_display_1" },
      cells: [{ key: key("A", "BB"), glyph: glyph("A", "BB", 11) }, { key: key("B", "BC"), glyph: glyph("B", "BC", 23) },
        { key: key(".", "AD"), glyph: glyph(".", "AD", 35) }] }] },
  ],
};
const widthOf = (code) => ({ 0x37: 6, 0x42: 5, 0xbb: 6, 0xbc: 6, 0xd5: 6, 0xab: 3, 0xad: 6 })[code] ?? 6;

test("a widened glyph draft marks its key before any keyboard edit or import", () => {
  const doc = fakeDocument();
  const create = doc.createElement;
  doc.createElement = (tag) => {
    const node = tag === "canvas" ? canvasNode({ createElement: create }) : create(tag);
    node.setAttribute ??= (k, v) => { node[k] = v; };
    node.addEventListener = (type, fn) => { node[`on${type}`] = fn; };
    node.querySelector = () => null;
    return node;
  };
  globalThis.document = doc;
  const nodes = new Map();
  const $ = (id) => {
    if (!nodes.has(id)) nodes.set(id, doc.createElement("div"));
    return nodes.get(id);
  };
  try {
    const keyboard = structuredClone(EDIT_KB);
    keyboard.keyboards[0].rows[0].cells[2].glyph.width = 3;
    const index = { keyboard, fonts: { main: "main", styles: { main: STYLE } }, glyphs: { main: ART } };
    const drafts = new GlyphEdits(null, "xx", "p");
    const edits = new KeyboardEdits(null, "xx", "p");
    const view = new KeyboardsView({
      index: () => index, glyphs: (style) => styleGlyphs(draftFonts(index, drafts.all()))[style],
      codec: () => new Codec(CODEC, PACK), exact: () => false, keyboardEdits: () => edits,
      loadRom: () => {}, openEntry: () => {}, setHash: () => {},
    }, $);
    const grid = () => $("k-grid").childNodes[0].childNodes[1].childNodes[0].childNodes;
    view.reset();
    assert.doesNotMatch($("k-list").textContent, /off the cursor/);
    assert.doesNotMatch(grid()[1].textContent, /off the cursor/);
    drafts.set({ id: "main:37", style: "main", code: "37", char: "Б", width: 6, rows: ART.art[37] },
      { width: 10, rows: ART.art[37].map((row) => `${row}____`) });
    view.refresh();
    assert.match(grid()[1].textContent, /off the cursor/);
    assert.match($("k-list").childNodes[0].textContent, /1 off the cursor/);
    assert.deepEqual(view.kb.keyboards[0].rows[0].cells.map((c) => [c.glyph.x, c.glyph.width]),
      [[11, 10], [27, 5], [39, 3]]);
    assert.doesNotMatch(grid()[2].textContent, /off the cursor/);
    assert.doesNotMatch(grid()[3].textContent, /off the cursor/);
    assert.equal(edits.size, 0);
    assert.equal(keyboard.keyboards[0].rows[0].cells[0].glyph.width, 6);
    drafts.remove("main:37");
    view.refresh();
    assert.doesNotMatch($("k-list").textContent, /off the cursor/);
    assert.doesNotMatch(grid()[1].textContent, /off the cursor/);
    assert.deepEqual(view.kb.keyboards[0].rows[0].cells.map((c) => [c.glyph.x, c.glyph.width]),
      [[11, 6], [23, 5], [35, 3]]);
  } finally {
    delete globalThis.document;
  }
});

const MOVABLE_KB = {
  ...EDIT_KB, first_page: 1,
  opening: [{ page: 1, keyboard: 1, look: 1 }, { page: 2, keyboard: 0, look: 2 }],
  pages: [{ name: "others", keyboard: 0, look: 0 }, { name: "upper", keyboard: 1, look: 1 },
    { name: "lower", keyboard: 0, look: 2 }, ...EDIT_KB.pages],
};

test("the tab refuses moves that change an opening keyboard or look", () => {
  const doc = fakeDocument();
  const create = doc.createElement;
  doc.createElement = (tag) => {
    const node = create(tag);
    node.setAttribute ??= (k, v) => { node[k] = v; };
    node.addEventListener = (type, fn) => { node[`on${type}`] = fn; };
    node.querySelector = () => null;
    return node;
  };
  globalThis.document = doc;
  try {
    for (const [first, keyboard, look, selected, slot] of [
      [1, 0, 2, 1, 1], [1, 1, 2, 3, 2], [1, 0, 1, 3, 2], [4, 0, 1, 1, 0],
    ]) {
      const kb = structuredClone(MOVABLE_KB);
      const order = pageOrder({ ...kb, first_page: first });
      kb.first_page = first;
      kb.opening = [{ page: order[0], keyboard: 1, look: 1 }, { page: order[1], keyboard: 0, look: 2 }];
      for (const p of kb.opening) Object.assign(kb.pages[p.page], { keyboard: p.keyboard, look: p.look });
      Object.assign(kb.pages[order[2]], { keyboard, look });
      const nodes = new Map();
      const $ = (id) => {
        if (!nodes.has(id)) nodes.set(id, doc.createElement("div"));
        return nodes.get(id);
      };
      const store = new Map();
      const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v), removeItem: (k) => store.delete(k) };
      const edits = new KeyboardEdits(storage, "xx", "p");
      const hashes = [];
      let changed = 0;
      const view = new KeyboardsView({
        index: () => ({ keyboard: kb }), glyphs: () => null, codec: () => new Codec(CODEC, PACK),
        keyboardEdits: () => edits, changed: () => changed++, setHash: (value) => hashes.push(value),
      }, $);
      view.reset(String(selected));
      const position = order.indexOf(selected);
      const buttons = $("k-order").childNodes[position].childNodes.at(-1).childNodes;
      buttons[position === 0 ? 1 : 0].onclick();
      const required = kb.opening.find((p) => p.page === slot);
      const message = `Opening slot ${slot} requires keyboard ${required.keyboard} and base page look ${required.look}.`;
      assert.equal($("k-order").childNodes.at(-1).textContent, message);
      assert.equal($("k-order").childNodes.at(-1).role, "alert");
      assert.equal($("k-order").childNodes.at(-1).className, "error");
      assert.equal(edits.size, 0);
      assert.equal(store.size, 0);
      assert.equal(changed, 0);
      assert.equal(view.cur, selected);
      assert.deepEqual(hashes, []);
      assert.deepEqual(view.kb.pages, kb.pages);
      view.movePage(order[2], 1);
      assert.equal(changed, 1);
      assert.equal(view.moveError, null);
      assert.doesNotMatch($("k-order").textContent, /requires keyboard/);
    }
  } finally {
    delete globalThis.document;
  }
});

test("a key change keeps its neighbours and centres the glyph in the cursor frame", () => {
  const codec = new Codec(CODEC, PACK);
  const board = EDIT_KB.keyboards[0];
  assert.deepEqual(keyEdit(EDIT_KB, board, 0, 1, "B", codec, widthOf),
    { keys: "БBa", display: "{CLEAR 0B}Б{CLEAR 06}B{CLEAR 06}a" });
  assert.deepEqual(keyEdit(EDIT_KB, board, 0, 1, "!", codec, widthOf), // 3 px, at 24 in the frame 20-31
    { keys: "Б!a", display: "{CLEAR 0B}Б{CLEAR 07}!{CLEAR 08}a" });
  assert.deepEqual(keyEdit(EDIT_KB, board, 0, 2, "b", codec, widthOf),
    { keys: "Ббb", display: "{CLEAR 0B}Б{CLEAR 06}б{CLEAR 07}b" });
  assert.throws(() => keyEdit(EDIT_KB, board, 0, 1, "ab", codec, widthOf), /one character/);
  assert.throws(() => keyEdit(EDIT_KB, board, 0, 1, "ж", codec, widthOf), /has no byte/);
  assert.deepEqual(printedGlyphs("{CLEAR 0B}Б{CLEAR 06}B", codec, widthOf).map((g) => [g.char, g.code, g.x, g.width]),
    [["Б", "37", 11, 6], ["B", "BC", 23, 6]]);
  assert.deepEqual(keyCell("Б", codec, STYLE), { char: "Б", code: "37", source: "glyph" });
  assert.deepEqual(keyCell("ж", codec, STYLE), { char: "ж", code: null, source: "none" });
  assert.deepEqual(keyCell("a", codec, { ...STYLE, copy: { D5: "BB" } }), { char: "a", code: "D5", source: "copy", copy: "BB" });
  assert.equal(measuredWidths(EDIT_KB).get(0x42), 5);
});

// English rows are keyed: no cells until a ROM loads.
const KEYED_KB = {
  ...EDIT_KB,
  keyboards: [
    EDIT_KB.keyboards[0],
    { ...EDIT_KB.keyboards[1], rows: [{
      keys: { id: "k4", text: null, name: "upper_keys_1" },
      display: { id: "d4", text: null, name: "upper_display_1" },
      cells: [],
    }] },
  ],
};
const TEXTS = { k4: "AB.", d4: "{CLEAR 0B}A{CLEAR 06}B{CLEAR 06}." };

test("keyed keyboard rows take the ROM English and stay empty without it", () => {
  const codec = new Codec(CODEC, PACK);
  const without = keyboardWithEnglish(KEYED_KB, null, codec, STYLE, widthOf);
  assert.equal(without.keyboards[1].rows[0].keys.text, null);
  assert.deepEqual(without.keyboards[1].rows[0].cells, []);
  assert.equal(without.keyboards[0].rows[0].keys.text, "Ббa"); // translations stay in the data
  const withRom = keyboardWithEnglish(KEYED_KB, { texts: TEXTS }, codec, STYLE, widthOf);
  assert.equal(withRom.keyboards[1].rows[0].keys.text, "AB.");
  assert.equal(withRom.keyboards[1].rows[0].display.text, "{CLEAR 0B}A{CLEAR 06}B{CLEAR 06}.");
  assert.deepEqual(withRom.keyboards[1].rows[0].cells.map((c) => c.key.char), ["A", "B", "."]);
  assert.deepEqual(withRom.keyboards[1].rows[0].cells.map((c) => c.glyph.x), [11, 23, 35]);
  assert.equal(withRom.keyboards[0].rows[0].keys.text, "Ббa");
});

test("the view restores keyed rows with a ROM and leaves them empty without one", () => {
  const doc = fakeDocument();
  const nodes = new Map();
  const $ = (id) => {
    if (!nodes.has(id)) nodes.set(id, doc.createElement("div"));
    return nodes.get(id);
  };
  globalThis.document = doc;
  try {
    let read = null;
    const index = { keyboard: KEYED_KB, fonts: { styles: { main: STYLE } } };
    const view = new KeyboardsView({
      index: () => index, english: () => read, glyphs: () => new Glyphs(STYLE, ART),
      codec: () => new Codec(CODEC, PACK), exact: () => false,
      loadRom: () => {}, openEntry: () => {}, setHash: () => {},
    }, $);
    assert.equal(view.kb.keyboards[1].rows[0].keys.text, null);
    assert.deepEqual(view.kb.keyboards[1].rows[0].cells, []);
    read = { texts: TEXTS };
    assert.equal(view.kb.keyboards[1].rows[0].keys.text, "AB.");
    assert.equal(view.kb.keyboards[1].rows[0].cells[0].key.char, "A");
  } finally {
    delete globalThis.document;
  }
});

test("the tab keeps a key change and a page move as a draft, previews them and exports them", () => {
  const doc = fakeDocument();
  const create = doc.createElement;
  doc.createElement = (tag) => {
    const node = tag === "canvas" ? canvasNode({ createElement: create }) : create(tag);
    node.setAttribute ??= (k, v) => { node[k] = v; };
    node.addEventListener = (type, fn) => { node[`on${type}`] = fn; };
    node.querySelector = () => null;
    return node;
  };
  globalThis.document = doc;
  const nodes = new Map();
  const $ = (id) => {
    if (!nodes.has(id)) nodes.set(id, doc.createElement("div"));
    return nodes.get(id);
  };
  const store = new Map();
  const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v), removeItem: (k) => store.delete(k) };
  try {
    const glyphs = new Glyphs(STYLE, ART);
    const index = { keyboard: MOVABLE_KB, fonts: { styles: { main: STYLE } } };
    let edits = new KeyboardEdits(storage, "xx", "p");
    let changed = 0;
    const view = new KeyboardsView({
      index: () => index, glyphs: () => glyphs, codec: () => new Codec(CODEC, PACK), exact: () => false,
      keyboardEdits: () => edits, changed: () => changed++, loadRom: () => {}, openEntry: () => {}, setHash: () => {},
    }, $);
    view.reset("3");
    const before = JSON.stringify($("k-preview").childNodes[0].grid);
    const grid = () => $("k-grid").childNodes[0].childNodes[1].childNodes[0].childNodes;
    grid()[2].onclick(); // row 1, column 2: б
    assert.deepEqual(view.sel, { row: 0, column: 1 });
    assert.match($("k-edit").textContent, /Row 1, column 2 enters/);
    view.setKey("B");
    assert.equal(changed, 1);
    assert.equal(edits.size, 1);
    assert.equal(edits.row("lower_keys_1"), "БBa");
    assert.equal(edits.row("lower_display_1"), "{CLEAR 0B}Б{CLEAR 06}B{CLEAR 06}a");
    assert.notEqual(JSON.stringify($("k-preview").childNodes[0].grid), before); // the preview follows
    assert.match(grid()[2].textContent, /B0xBCx 23.*edited/);
    assert.match($("k-list").childNodes[2].textContent, /edited/);
    assert.match($("k-edit").textContent, /1 keyboard changes kept in this browser/);
    view.setKey("ж");
    assert.match($("k-edit").textContent, /has no byte/);
    assert.equal(edits.row("lower_keys_1"), "БBa");
    edits = new KeyboardEdits(storage, "xx", "p"); // kept in the browser
    view.refresh();
    assert.equal(view.kb.keyboards[0].rows[0].keys.text, "БBa");
    $("k-order").childNodes[2].childNodes[1].childNodes[1].onclick();
    assert.deepEqual(edits.pages(), ["others", "upper", "lower", "LATIN", "pack"]);
    assert.equal(view.cur, 4);
    assert.equal($("k-name").textContent, "pack");
    assert.match(grid()[2].textContent, /edited/);
    assert.doesNotMatch(grid()[1].textContent, /edited/); // compared with the same keyboard
    view.select(0, 2);
    view.setKey("b"); // a second key of the moved page
    assert.equal(edits.row("lower_keys_1"), "БBb");
    assert.equal(edits.changes().base.rows.lower_keys_1, "Ббa");
    view.select(0, 2);
    view.setKey("a");
    assert.deepEqual(exportDoc("xx", "p", [], [], [], [], edits.changes()).keyboard, {
      rows: { lower_keys_1: "БBa", lower_display_1: "{CLEAR 0B}Б{CLEAR 06}B{CLEAR 06}a" },
      base: { rows: { lower_keys_1: "Ббa", lower_display_1: "{CLEAR 0B}Б{CLEAR 06}б{CLEAR 07}a" },
        pages: ["others", "upper", "lower", "pack", "LATIN"] },
      pages: ["others", "upper", "lower", "LATIN", "pack"],
    });
    view.open(3); // English keys are not editable
    assert.equal(grid()[1].onclick, undefined);
    view.setKey("a");
    view.discard();
    assert.equal(edits.size, 0);
    assert.deepEqual(view.kb, MOVABLE_KB);
    assert.equal(exportDoc("xx", "p", []).keyboard, undefined);
  } finally {
    delete globalThis.document;
  }
});
