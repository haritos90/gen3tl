// The Pictures view's logic.
import assert from "node:assert/strict";
import { createHash, webcrypto } from "node:crypto";
import { test } from "node:test";

import {
  appendedTiles, englishOf, filterGraphics, findingPixels, formatAreas, fromB64, parseAreas, parseTiles, rgbaOf,
  reasonOf, statusOf, summaryOf, toB64,
  PicturesView,
} from "../js/pictures.js";
import { evidenceFile, fakeDocument, fakeStorage, noteCollection, stubPage } from "./fixture.mjs";
import { NoteEdits, PictureEdits } from "../js/store.js";
import { fillPage, setCatalog } from "../js/ui.js";

test("picture search explains searchable note metadata in the interface language", (context) => {
  const { $ } = stubPage(context, "editor");
  const placeholder = "Search id, English, screen or notes";
  const help = "Search includes active notes and their review status, sources and evidence.";
  assert.equal($("p-search").getAttribute("aria-describedby"), "p-search-help");
  fillPage();
  assert.equal($("p-search").getAttribute("placeholder"), placeholder);
  assert.equal($("p-search-help").textContent, help);
  setCatalog({ [placeholder]: "Localized search", [help]: "Localized note search scope" });
  context.after(() => setCatalog(null));
  fillPage();
  assert.equal($("p-search").getAttribute("placeholder"), "Localized search");
  assert.equal($("p-search-help").textContent, "Localized note search scope");
});

test("picture search retains statement and metadata matches from flattened reasons", (context) => {
  const { $ } = stubPage(context, "editor");
  const record = (id, status, scope, text) => ({ id, subject: { kind: "graphic", id: "G01" }, status, scope, facts: [text] });
  const notes = { schema: "3.0.0", note: [
    record("shared", "unreviewed", { kind: "shared" }, "Shared fact."),
    record("local", "accepted", { kind: "language", language: "xx" }, "Local fact."),
    record("foreign", "accepted", { kind: "language", language: "yy" }, "Foreign fact."),
    ...["proposed", "needs_review", "superseded"].map((status) => record(status, status, { kind: "shared" }, `${status} fact.`)),
  ] };
  Object.assign(notes.note[0], { guidance: ["Retain spacing."], reason: ["Keep the layout."],
    sources: [{ name: "Manual", address: "ref:manual" }],
    evidence: [{ method: "measurement", verification: "checked" }] });
  const rows = [
    { id: "G01", category: "keep", screen: "Menu", note_ids: notes.note.map((note) => note.id),
      reason: "Retain spacing.\nShared fact.\nKeep the layout.\nstatus: unreviewed; sources: name: Manual; address: ref:manual; evidence: method: measurement; verification: checked\nLocal fact.\nstatus: accepted" },
    { id: "G02", category: "translate", screen: "", reason: "Legacy art." },
    { id: "G03", category: "keep", screen: "", note_ids: ["proposed"], reason: "" },
  ];
  const index = { language: "xx", notes, graphics: { inventory: rows, edits: {} } };
  const before = structuredClone(index);
  const view = new PicturesView({ index: () => index, uploads: () => ({ map: new Map() }) }, $);
  view.rows = rows;
  view.applyFilters();
  assert.deepEqual($("p-list").children.map((row) => row.children[2].textContent),
    ["Retain spacing.\nShared fact.\nKeep the layout.\nLocal fact.", "Legacy art.", ""]);
  const queries = ["", "spacing", "Shared", "layout", "Local", "Foreign", "status: unreviewed", "status: accepted", "Legacy",
    "Manual", "ref:manual", "measurement", "checked",
    "proposed", "needs_review", "superseded", "G01", "Menu", "absent"];
  const expected = queries.map((search) => filterGraphics(rows, { search }, index.graphics));
  for (const query of ["spacing", "Shared", "layout", "Local", "Manual", "measurement", "status: unreviewed"]) {
    assert.deepEqual(expected[queries.indexOf(query)], [0], query);
  }
  for (const row of rows.filter((row) => row.note_ids)) delete row.reason;
  for (const [i, query] of queries.entries()) {
    $("p-search").value = query;
    view.applyFilters();
    assert.deepEqual(view.filtered, expected[i], query);
  }
  assert.equal(reasonOf({ ...rows[0], reason: "Stale projection." }, index), before.graphics.inventory[0].reason);
  assert.equal(reasonOf({ ...rows[2], reason: "Inactive projection." }, index), "");
  assert.equal(reasonOf({ reason: "Legacy art." }, index), "Legacy art.");
  assert.equal(reasonOf({ note_ids: ["missing"], reason: "Old export." }, index), "Old export.");
  assert.equal(summaryOf({ ...rows[2], reason: "Inactive projection." }, index), "");
  index.language = "yy";
  $("p-search").value = "";
  view.applyFilters();
  assert.equal($("p-list").children[0].children[2].textContent,
    "Retain spacing.\nShared fact.\nKeep the layout.\nForeign fact.");
  assert.deepEqual(notes, before.notes);
});

test("migrated picture summaries show two active notes as statements and retain full details", (context) => {
  const { $ } = stubPage(context, "editor");
  const notes = noteCollection("graphic", "G01");
  Object.assign(notes.note[1], { status: "accepted", guidance: ["Use narrow spacing."],
    facts: ["The caption has two rows."], reason: ["The box is fixed."] });
  const row = { id: "G01", note_ids: ["note:shared", "note:wording"], category: "keep", screen: "Menu" };
  const index = { language: "xx", notes, graphics: { inventory: [row], edits: {}, palette: {}, sheet_width: {} } };
  const before = structuredClone(index);
  const view = new PicturesView({ index: () => index, uploads: () => ({ map: new Map() }) }, $);
  view.rows = [row];
  view.applyFilters();
  const summary = $("p-list").children[0].children[2].textContent;
  assert.equal(summary, ["Preserve the placeholder.", "Keep line groups.", "The name varies.", "The slot is shared.",
    "Use narrow spacing.", "The caption has two rows.", "The box is fixed."].join("\n"));
  assert.doesNotMatch(summary, /status|sources|evidence|unreviewed|accepted|measurement|Reference|note:/i);
  view.renderFacts(row);
  for (const text of ["Review: Unreviewed migration", "Review: Accepted", "Sources", "Reference", "Evidence",
    "Method: measurement", "Verification: Checked", "Author: Contributor", "Related attestation."]) {
    assert.ok($("p-facts").textContent.includes(text), text);
  }
  assert.deepEqual(index, before);
});

test("legacy picture summaries retain the reason without structured notes", (context) => {
  const { $ } = stubPage(context, "editor");
  const rows = [{ id: "G01", category: "keep", screen: "Menu", reason: "Keep the original border." },
    { id: "G02", category: "keep", screen: "Menu", note_ids: ["missing"], reason: "Keep the legacy caption." }];
  const index = { language: "xx", notes: noteCollection("graphic", "G03"), graphics: { inventory: rows, edits: {} } };
  const view = new PicturesView({ index: () => index, uploads: () => ({ map: new Map() }) }, $);
  view.rows = rows;
  view.applyFilters();
  assert.deepEqual($("p-list").children.map((row) => row.children[2].textContent), rows.map((row) => row.reason));
  assert.equal(summaryOf(rows[0]), rows[0].reason);
  assert.equal(summaryOf({}), "");
});

test("picture facts show shared and language notes with legacy fallback", (context) => {
  const { $ } = stubPage(context, "editor");
  const notes = noteCollection("graphic", "G01");
  const row = { id: "G01", note_ids: ["note:shared", "note:wording"], reason: "Legacy reason.", category: "keep" };
  const graphics = { inventory: [row], edits: {}, palette: {}, sheet_width: {} };
  const before = structuredClone(graphics);
  const research = { "research:one": { table: "terms.tsv", name: "Twin Tap" } };
  const view = new PicturesView({ index: () => ({ graphics, notes, research, language: "xx" }) }, $);
  view.renderFacts(row);
  for (const text of ["Shared game notes", "Language: xx", "Guidance", "Facts", "Reason", "Sources", "Evidence", "Review: Proposed"]) {
    assert.ok($("p-facts").textContent.includes(text), text);
  }
  assert.ok(!$("p-facts").textContent.includes("Legacy reason."));
  assert.ok($("p-facts").textContent.includes("Related note: Research: terms.tsv · Twin Tap · Related attestation."));
  assert.doesNotMatch($("p-facts").textContent, /\b(?:note|rule|research):\S/);
  assert.deepEqual(graphics, before);
  const shown = $("p-facts").textContent;
  const { reason, ...migrated } = row;
  view.renderFacts(migrated);
  assert.equal($("p-facts").textContent, shown);
  view.renderFacts({ ...row, note_ids: undefined });
  assert.ok($("p-facts").textContent.includes("Legacy reason."));
});

test("picture facts expose note-only drafts without a ROM or replacement upload", async (context) => {
  const { $ } = stubPage(context, "editor");
  const row = { id: "G01", reason: "Legacy reason.", category: "keep" };
  const graphics = { inventory: [row], edits: {}, palette: {}, sheet_width: {} };
  const before = structuredClone(graphics), storage = fakeStorage();
  const edits = new NoteEdits(storage, "xx", "project"), uploads = new PictureEdits(storage, "xx", "project");
  const view = new PicturesView({ index: () => ({ graphics, language: "xx" }), noteEdits: () => edits,
    languages: () => ["xx", "yy"], uploads: () => uploads }, $);
  view.renderFacts(row);
  const descendants = (node) => [node, ...(node.childNodes ?? []).flatMap(descendants)];
  descendants($("p-facts")).find((node) => node.tagName === "BUTTON" && node.textContent === "Add note").listeners.click();
  const draft = edits.forSubject({ kind: "graphic", id: "G01" })[0];
  assert.deepEqual(draft.note.binding, draft.note.subject);
  await edits.attach(draft.note.id, evidenceFile(), { role: "original", build: "1.0.0", context: "Menu" });
  view.renderFacts(row);
  assert.match($("p-facts").textContent, /Legacy reason/);
  assert.match($("p-facts").textContent, /Evidence attachments/);
  assert.match($("p-facts").textContent, /Original · 1.0.0 · Menu/);
  assert.equal(statusOf(row, graphics, uploads.map).uploaded, false);
  assert.equal(uploads.size, 0);
  assert.deepEqual(graphics, before);
});

test("switching projects discards an unfinished picture upload", async () => {
  let finish;
  const bytes = new Promise((resolve) => { finish = resolve; });
  let checked = false;
  const view = new PicturesView({ index: () => null, gfx: () => { checked = true; } }, () => ({ value: "" }));
  view.cur = "G01";
  view.generation = 1;
  const pending = view.upload({ name: "G01.png", arrayBuffer: () => bytes });
  view.generation++;
  finish(new ArrayBuffer(1));
  await pending;
  assert.equal(checked, false);
});

test("uploads retain the original project's area PNG hash through export", async () => {
  const oldDocument = globalThis.document;
  globalThis.document = fakeDocument();
  globalThis.crypto ??= webcrypto;
  try {
    const png = new Uint8Array([1, 2, 3]);
    const graphics = { edits: { G01: { png: toB64(png) } } };
    const uploads = new PictureEdits(null, "xx", "test");
    const result = { code: 0, changed: 1, areas: [[0, 0, 1, 1]], tiles: 1,
      area_png: new Uint8Array([4, 5, 6]), pixels: new Uint8Array([1]), lines: [] };
    const node = document.createElement("div");
    node.value = "";
    const view = new PicturesView({ index: () => ({ graphics }), uploads: () => uploads, changed() {},
      gfx: () => ({ check: () => ({ ...result }), edited: () => ({ pixels: new Uint8Array([0]) }) }) }, () => node);
    for (const method of ["renderStatus", "applyFilters", "renderPictures"]) view[method] = () => {};
    view.cur = "G01";
    const file = { name: "G01.png", arrayBuffer: async () => png.buffer };
    await view.upload(file);
    const hash = createHash("sha256").update(png).digest("hex");
    assert.equal(uploads.changes()[0].base, hash);
    graphics.edits.G01.png = toB64(new Uint8Array([7, 8, 9]));
    await view.upload(file);
    assert.equal(uploads.changes()[0].base, hash);
    uploads.remove("G01");
    delete graphics.edits.G01;
    await view.upload(file);
    assert.equal(uploads.changes()[0].base, null);
    const legacy = uploads.get("G01");
    delete legacy.base;
    await view.upload(file);
    assert.equal(uploads.changes()[0].base, undefined);
  } finally {
    globalThis.document = oldDocument;
  }
});

const ROWS = [
  { id: "G01", screen: "01-boot", source_text: "NOT FOR SALE", category: "keep", reason: "Copyright", rom_offset: "0x1" },
  { id: "G10", screen: "14-summary", source_text: "HP", category: "translate", reason: "HP bar label", rom_offset: "0x2" },
  { id: "G16", screen: "27-card-back", source_text: "LEAGUE BADGES", category: "translate", reason: "Header",
    rom_offset: "0x3" },
  { id: "G33", screen: "60-dex", source_text: "SEEN", category: "translate", reason: "Runtime text", rom_offset: "" },
  { id: "G45", screen: "70-pc", source_text: "BOX", category: "optional", reason: "Box label", rom_offset: "0x4" },
];
const DATA = { inventory: ROWS, edits: { G10: { areas: [[8, 16, 15, 6]], tiles: null }, G16: { areas: [], tiles: 210 } } };
const UPLOADS = new Map([["G45", {}], ["G16", {}]]);
const ids = (f) => filterGraphics(ROWS, { search: "", category: "", status: "", ...f }, DATA, UPLOADS).map((i) => ROWS[i].id);

test("edit status of a graphic", () => {
  assert.deepEqual(statusOf(ROWS[1], DATA, UPLOADS), { source: true, project: true, uploaded: false });
  assert.deepEqual(statusOf(ROWS[2], DATA, UPLOADS), { source: true, project: true, uploaded: true });
  assert.deepEqual(statusOf(ROWS[3], DATA), { source: false, project: false, uploaded: false });
  assert.deepEqual(statusOf(ROWS[0], null), { source: true, project: false, uploaded: false });
});

test("a translate row of a graphic another row edits shows that edit; a keep row of it shows none", () => {
  const rows = [
    { id: "G59", source_text: "CREDIT", category: "translate", rom_offset: "0x04659D0" },
    { id: "G60", source_text: "PAYOUT", category: "translate", rom_offset: "0x04659D0" },
    { id: "G04", source_text: "PRESS START", category: "translate", rom_offset: "0x1FD4854" },
    { id: "G05", source_text: "SKELI", category: "keep", rom_offset: "0x1FD4854" },
  ];
  const edit = { areas: [[0, 16, 30, 6], [32, 16, 30, 6]], tiles: null, evidence: "" };
  const data = { inventory: rows, tilemaps: [],
    edits: { G59: edit, G60: { ...edit, shared: "G59" }, G04: { areas: [[8, 8, 88, 8]], tiles: null } } };
  assert.deepEqual(rows.map((r) => statusOf(r, data).project), [true, true, true, false]);
  assert.deepEqual(filterGraphics(rows, { search: "", category: "", status: "edited" }, data).map((i) => rows[i].id),
    ["G59", "G60", "G04"]);
  const old = globalThis.document;
  globalThis.document = fakeDocument();
  try {
    const box = document.createElement("div");
    const view = new PicturesView({ index: () => ({ graphics: data }) }, () => box);
    view.renderProject(rows[1]);
    assert.match(box.textContent, /^The edit of G59: one edit covers every text area of this ROM graphic\./);
    assert.match(box.textContent, /Text areas: 0,16,30,6; 32,16,30,6/);
    view.renderProject(rows[0]);
    assert.doesNotMatch(box.textContent, /The edit of/, "the row that holds the edit");
    view.renderProject(rows[3]);
    assert.equal(box.textContent, "No edit in the project.");
  } finally {
    globalThis.document = old;
  }
});

test("filters: category, edit status, search", () => {
  assert.deepEqual(ids({}), ["G01", "G10", "G16", "G33", "G45"]);
  assert.deepEqual(ids({ category: "translate" }), ["G10", "G16", "G33"]);
  assert.deepEqual(ids({ status: "edited" }), ["G10", "G16"]);
  assert.deepEqual(ids({ status: "uploaded" }), ["G16", "G45"]);
  assert.deepEqual(ids({ status: "none" }), ["G01"]);
  assert.deepEqual(ids({ status: "nosource" }), ["G33"]);
  assert.deepEqual(ids({ search: "badges" }), ["G16"]);
  assert.deepEqual(ids({ search: "summary" }), ["G10"]);
  assert.deepEqual(ids({ search: " label " }), ["G10", "G45"]);
  assert.deepEqual(ids({ search: "g01" }), ["G01"]);
  assert.deepEqual(ids({ search: "label", category: "optional" }), ["G45"]);
});

test("a graphic's English comes from the loaded ROM by key or digest", () => {
  const read = { texts: { k1: "PRESS START", k2: "HP" }, digests: { k1: "a".repeat(64), k2: "b".repeat(64) } };
  assert.equal(englishOf({ id: "G04", source_key: "k1" }, read), "PRESS START");
  assert.equal(englishOf({ id: "G08", source_digest: "b".repeat(64) }, read), "HP");
  assert.equal(englishOf({ id: "G03", source_digest: "c".repeat(64) }, read), "");
  assert.equal(englishOf({ id: "G01", source_text: "SHIPPED" }, null), "SHIPPED");
  assert.equal(englishOf({ id: "G01", source_key: "k1" }, null), "");
});

test("filters search the English the ROM gives a row", () => {
  const rows = [{ id: "G04", screen: "03-title", source_key: "k1", category: "translate", reason: "Title",
    rom_offset: "0x1" }];
  const read = { texts: { k1: "PRESS START" }, digests: { k1: "a".repeat(64) } };
  const found = (english) => filterGraphics(rows, { search: "press", category: "", status: "" }, { edits: {} },
    new Map(), english).map((i) => rows[i].id);
  assert.deepEqual(found((r) => englishOf(r, read)), ["G04"]);
  assert.deepEqual(found(null), [], "without the ROM, the data has no English");
});

test("the view shows a graphic's English from the ROM and none without it", () => {
  const doc = fakeDocument();
  const create = doc.createElement;
  doc.createElement = (tag) => {
    const node = create(tag);
    node.setAttribute ??= (k, v) => { node[k] = v; };
    node.addEventListener ??= (type, fn) => { node[`on${type}`] = fn; };
    node.querySelector = () => null;
    return node;
  };
  const nodes = new Map();
  const $ = (id) => {
    if (!nodes.has(id)) nodes.set(id, doc.createElement("div"));
    return nodes.get(id);
  };
  globalThis.document = doc;
  try {
    let read = null;
    const row = { id: "G04", screen: "03-title", source_key: "k1", category: "translate", reason: "Title",
      rom_offset: "0x1" };
    const view = new PicturesView({
      index: () => ({ graphics: { inventory: [row], edits: {}, palette: {}, sheet_width: {} } }),
      english: () => read, uploads: () => new Map(), gfx: () => null,
    }, $);
    view.rows = [row];
    view.filtered = [0];
    view.cur = "G04";
    view.renderList();
    assert.equal($("p-list").childNodes[0].childNodes[1].textContent, "");
    read = { texts: { k1: "PRESS START" }, digests: { k1: "a".repeat(64) } };
    view.renderList();
    assert.equal($("p-list").childNodes[0].childNodes[1].textContent, "PRESS START");
    view.renderFacts(row);
    assert.match($("p-facts").textContent, /EnglishPRESS START/);
  } finally {
    delete globalThis.document;
  }
});

test("text areas parse from any separators and format back", () => {
  assert.deepEqual(parseAreas("29,7,72,1; 37,8,80,8"), [[29, 7, 72, 1], [37, 8, 80, 8]]);
  assert.deepEqual(parseAreas("[[8, 16, 15, 6]]"), [[8, 16, 15, 6]]);
  assert.deepEqual(parseAreas("8 16 15 6\n0,0,1,1"), [[8, 16, 15, 6], [0, 0, 1, 1]]);
  assert.deepEqual(parseAreas("  "), []);
  assert.equal(formatAreas([[29, 7, 72, 1], [37, 8, 80, 8]]), "29,7,72,1; 37,8,80,8");
  assert.equal(formatAreas(null), "");
  assert.deepEqual(parseAreas(formatAreas([[1, 2, 3, 4]])), [[1, 2, 3, 4]]);
  assert.throws(() => parseAreas("1,2,3"), /3 numbers/);
  assert.throws(() => parseAreas("1,2,0,4"), /empty or negative/);
  assert.throws(() => parseAreas("-1,2,3,4"), /empty or negative/);
  assert.throws(() => parseAreas("x=1"), /numbers only/);
});

test("tile count input", () => {
  assert.equal(parseTiles(""), null);
  assert.equal(parseTiles(" 210 "), 210);
  assert.equal(parseTiles(null), null);
  assert.throws(() => parseTiles("0"), /positive/);
  assert.throws(() => parseTiles("2.5"), /positive/);
});

test("base64 round trip of bytes, large ones too", () => {
  const b = new Uint8Array(200000).map((_, i) => (i * 7) & 255);
  assert.deepEqual(fromB64(toB64(b)), b);
  assert.equal(toB64(new Uint8Array([0x89, 0x50, 0x4e, 0x47])), "iVBORw==");
});

test("indices to RGBA with the palette", () => {
  const rgba = rgbaOf(new Uint8Array([0, 1, 2, 7]), ["#ff00ff", "#102030", "#ffffff"]);
  assert.deepEqual([...rgba], [255, 0, 255, 255, 16, 32, 48, 255, 255, 255, 255, 255, 0, 0, 0, 255]);
});

test("appended tiles and finding pixels for the marks", () => {
  // G16: 209 tiles, 16 per row
  assert.deepEqual(appendedTiles(16, 209, 210), [[8, 104, 8, 8]]);
  assert.deepEqual(appendedTiles(4, 12, null), []);
  assert.deepEqual(appendedTiles(4, 2, 5), [[16, 0, 8, 8], [24, 0, 8, 8], [0, 8, 8, 8]]);
  assert.deepEqual(appendedTiles(2, 2, 5), [[0, 8, 8, 8], [8, 8, 8, 8], [0, 16, 8, 8]]);
  assert.deepEqual(findingPixels([
    "pixel 30,7: index 3 -> 9 outside the text areas", "palette entry 2 is #000000, the original #ffffff",
    "pixel 1,120: index 0 -> 4 past the last tile (the graphic has 12 tiles)"]), [[30, 7], [1, 120]]);
  assert.deepEqual(findingPixels(undefined), []);
});

test("screen previews precede sheets and include project plus upload", async () => {
  const { fakeDocument } = await import("./fixture.mjs");
  const doc = fakeDocument();
  const create = doc.createElement;
  doc.createElement = (tag) => {
    const node = create(tag);
    node.setAttribute = (key, value) => { node[key] = value; };
    node.getContext = () => ({ putImageData() {}, drawImage() {}, setLineDash() {}, strokeRect() {} });
    return node;
  };
  const oldDoc = globalThis.document, oldImage = globalThis.ImageData;
  globalThis.document = doc;
  globalThis.ImageData = class { constructor(data) { this.data = data; } };
  try {
    const image = { width: 8, height: 8, pixels: new Uint8Array(64), palette: ["#000000"],
      width_tiles: 1, tiles: 1, areas: [], changed: 1, findings: [], png: new Uint8Array(1) };
    const calls = [];
    const gfx = { sheet: () => image, edited: () => image,
      preview: (...args) => { calls.push(args); return [{ ...image, label: "Header" }]; } };
    const nodes = new Map();
    const $ = (id) => {
      if (!nodes.has(id)) nodes.set(id, doc.createElement("div"));
      return nodes.get(id);
    };
    const data = { edits: { G01: { areas: [] } }, previews: { G01: [{}] } };
    const upload = { png: "AQ==", areas: [[0, 0, 1, 1]], tiles: 2, name: "label.png" };
    const view = new PicturesView({ index: () => ({ graphics: data }), gfx: () => gfx,
      uploads: () => ({ get: () => upload }) }, $);
    const box = doc.createElement("div");
    await view.drawAll({ id: "G01" }, box);
    assert.match(box.childNodes[0].textContent, /English.*Project translation.*Your upload/);
    assert.equal(box.childNodes[1].textContent, "Tile sheets");
    assert.deepEqual(calls.map((a) => a[1]), [false, true, true]);
    assert.deepEqual([...calls[2][2]], [1]);
    assert.deepEqual(calls[2].slice(3), [upload.areas, 2]);
    const descendants = (node) => [node, ...(node.childNodes ?? []).flatMap(descendants)];
    for (const canvas of descendants(box).filter((node) => node.tagName === "CANVAS")) {
      assert.equal(canvas.role, "img");
      assert.ok(canvas["aria-label"].length > 0);
    }
  } finally {
    globalThis.document = oldDoc;
    globalThis.ImageData = oldImage;
  }
});

test("relabel before a language loads draws nothing", () => {
  const view = new PicturesView({ index: () => null }, () => { throw new Error("no element yet"); });
  view.relabel();
  assert.equal(view.cur, null);
});
