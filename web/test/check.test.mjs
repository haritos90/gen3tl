// The browser check: configuration, widths and English reader.
import assert from "node:assert/strict";
import { test } from "node:test";

import { Check, englishOf } from "../js/check.js";
import { draftFonts } from "../js/fonts.js";
import { STYLE } from "./fixture.mjs";

/** A Check over a Pyodide stand-in recording call arguments. */
function stub() {
  const calls = [];
  const py = { unpackArchive() {}, runPython() {},
    globals: { get: (name) => (...args) => calls.push({ name, args }) && "{}" } };
  return { check: new Check(py, new Uint8Array()), calls };
}

test("the check measures a redrawn glyph and a sprite letter's step before any import", () => {
  const table = Array.from({ length: 256 }, (_, b) => (b === 0x37 ? 3 : 0));
  const steps = Array.from({ length: 256 }, () => 4);
  const hud = { kind: "sprite", name: "hud", style: "hud", tiles: 3, table,
    glyphs: [{ code: "37", tile: 3, char: "Б", source: "added", width: 4 },
      { code: "87", tile: 0, char: "А", source: "copied", from: "BB", width: 4 }] };
  const index = { language: "ru", charmap: { "Б": "37" }, placeholders: { widths: {}, default: 60 },
    fonts: { main: "main", styles: { main: STYLE }, sprites: { hud: { advance: 4, tiles: 3, table, steps, shared: 1 } } },
    glyphs: { main: { top: 0, rows: 14, art: {} } }, font_sheets: { sheets: [hud] } };
  const { check, calls } = stub();
  check.configure(draftFonts(index, [{ style: "main", code: "37", width: 9, rows: Array(14).fill("_###s____") },
    { style: "hud", code: "37", width: 7, rows: Array(8).fill("s#####s") },
    { style: "hud", code: "87", width: 6, rows: Array(8).fill("s####s") }]));
  const [doc, widths] = calls.find((c) => c.name === "configure").args;
  const fonts = JSON.parse(doc).fonts;
  assert.deepEqual([fonts.styles.main.pack["37"], fonts.styles.main.shadow], [9, ["37"]]);
  assert.deepEqual([fonts.sprites.hud.steps[0x37], fonts.sprites.hud.steps[0x38]], [6, 4]);
  assert.deepEqual([fonts.sprites.hud.table[0x87], fonts.sprites.hud.steps[0x87]], [4, 5]);
  assert.equal(widths.main.length, 256);
  check.configure(index);
  assert.equal(JSON.parse(calls.at(-1).args[0]).fonts.styles.main.pack["37"], 6, "without drafts, the pack's width");
});

/** A Pyodide stand-in whose english() counts its calls. */
function fakePython() {
  const calls = [];
  const english = (rom, rows) => {
    calls.push([rom, rows]);
    return JSON.stringify({ texts: { "table:items/1": "Potion" }, missing: Object.keys(JSON.parse(rows)).slice(1) });
  };
  const py = { unpackArchive() {}, runPython() {}, globals: { get: (name) => (name === "english" ? english : null) } };
  return { py, calls };
}

const SOURCE = { profile: "game", rom_sha1: "0".repeat(40),
  rows: { "table:items/1": [16, 7, "field", "fixed", "a".repeat(64)], "map:1.1/npc:0/0": [32, 9, "field", "pointer", "b".repeat(64)] } };

test("the bundle reads the source rows from the ROM", () => {
  const { py, calls } = fakePython();
  const out = new Check(py, new Uint8Array()).english(new Uint8Array([1, 2]), SOURCE);
  assert.deepEqual(out, { texts: { "table:items/1": "Potion" }, missing: ["map:1.1/npc:0/0"] });
  assert.deepEqual(JSON.parse(calls[0][1]), SOURCE.rows);
});

test("a ROM is read once per page session", async () => {
  const { py, calls } = fakePython();
  const check = new Check(py, new Uint8Array());
  const rom = new Uint8Array([7, 7, 7]);
  const first = await englishOf(check, rom, SOURCE);
  assert.equal(await englishOf(check, new Uint8Array([7, 7, 7]), SOURCE), first);   // the same bytes
  assert.equal(calls.length, 1);
  await englishOf(check, new Uint8Array([8]), SOURCE);   // another ROM
  assert.equal(calls.length, 2);
});

test("rule previews use configured metadata and current catalog drafts", () => {
  const { check, calls } = stub();
  const engine_rules = { schema: "1.0.0", rules: [{ id: "ending", operands: [] }] };
  check.configure({ fonts: { styles: { main: STYLE } }, charmap: {}, placeholders: {}, engine_rules });
  assert.deepEqual(JSON.parse(calls[0].args[0]).engine_rules, engine_rules);
  const entries = [{ id: "table:ending/0", translation: "", flags: ["empty"] }];
  const drafts = [{ id: entries[0].id, translation: "s", state: "text" }];
  const request = { quantity: 2, name: "Name", operands: {} };
  assert.equal(check.rulePreview("ending", entries, drafts, request).exact, false);
  const last = calls.at(-1);
  assert.equal(last.name, "rule_preview");
  assert.equal(last.args[0], "ending");
  assert.deepEqual(last.args.slice(1).map(JSON.parse), [entries, drafts, request]);
});
