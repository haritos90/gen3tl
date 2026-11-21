import assert from "node:assert/strict";
import { test } from "node:test";
import { loadSlotSources, slotRows, sourceIds } from "../js/slots.js";
import { Check } from "../js/check.js";

const graph = { dependencies: { name: ["prefix-a", "prefix-b", "name-a"] } };
const window = { slot_details: { name: { source: "name", label: "Battle name with prefix", state: "computed", width: 32,
  contributors: [{ id: "prefix-a", width: 20, original: false }, { id: "name-a", width: 12, original: false }] } } };
const entries = { "prefix-a": { translation: "ABC " }, "prefix-b": { translation: "D " }, "name-a": { translation: "AB" } };

test("all candidates load before checking, including a shorter prefix", async () => {
  const loaded = [];
  const drafts = new Map([["prefix-b", { translation: "ABCD ", state: "text" }]]);
  const result = await loadSlotSources({ window }, graph, async (id) => { loaded.push(id); return entries[id]; }, drafts);
  assert.deepEqual(loaded, sourceIds(window, graph));
  assert.deepEqual(result.drafts, { "prefix-b": { translation: "ABCD ", state: "text" } });
  assert.equal(Object.keys(result.sources).length, 3);
  await assert.rejects(loadSlotSources({ window }, graph, async () => null, drafts), /unavailable/);
});

test("the panel follows Python's changed winner and undo", () => {
  const before = slotRows(window, entries)[0];
  assert.equal(before.parts[0].label, "«ABC·»");
  assert.equal(before.parts[0].id, "prefix-a");
  const changed = structuredClone(window);
  changed.slot_details.name.width = 38;
  changed.slot_details.name.contributors[0] = { id: "prefix-b", width: 26, original: false };
  const after = slotRows(changed, entries, { "prefix-b": { translation: "ABCD ", state: "text" } })[0];
  assert.equal(after.width, 38);
  assert.equal(after.parts[0].label, "«ABCD·»");
  assert.equal(after.parts[0].id, "prefix-b");
  assert.deepEqual(slotRows(window, entries)[0], before);
});

test("the bridge sends source states to the shared Python checker", () => {
  const calls = [];
  const py = { unpackArchive() {}, runPython() {}, globals: { get: (name) => (...args) => {
    calls.push({ name, args });
    return JSON.stringify({ window });
  } } };
  const check = new Check(py, new Uint8Array());
  const drafts = { "prefix-b": { translation: "ABCD ", state: "text" } };
  check.run({ id: "host", window }, "message", entries, drafts);
  const [host, text, sources, sent] = calls.at(-1).args;
  assert.equal(JSON.parse(host).id, "host");
  assert.equal(text, "message");
  assert.deepEqual(JSON.parse(sources), entries);
  assert.deepEqual(JSON.parse(sent), drafts);
});

test("recorded and override widths retain their visible states", () => {
  const result = slotRows({ slot_details: {
    first: { width: 60, state: "recorded" },
    second: { width: 62, computed: 60, state: "override", reason: "Printer reserves two pixels" },
    third: { width: 70, state: "recorded", unavailable: "Incomplete name table" },
  } });
  assert.match(result[0].state, /recorded/);
  assert.equal(result[1].computed, 60);
  assert.equal(result[1].reason, "Printer reserves two pixels");
  assert.equal(result[2].width, 70);
  assert.equal(result[2].state, "recorded; sources unavailable");
});

test("source edits and grouped undo invalidate saved consumer checks", async () => {
  const { Edits } = await import("../js/store.js");
  const store = { setItem() {}, removeItem() {}, getItem() { return null; } };
  const edits = new Edits(store, "xx", "fixture", { fill_sources: graph });
  const host = { id: "host", translation: "before", state: "text" };
  const prefix = { id: "prefix-b", translation: "D ", state: "text" };
  edits.set(host, "after", "", [], true);
  edits.set(prefix, "ABCD ", "", [], true);
  assert.equal(edits.get("host").problems, null);
  assert.equal(edits.get("host").exact, false);
  edits.checked("host", [], true);
  edits.removeMany(["prefix-b"]);
  assert.equal(edits.get("host").problems, null);
  assert.equal(edits.get("host").exact, false);
});


test("restricted and unrestricted panels name the winning prefix", () => {
  const name = { label: "Typed name", count: 2, glyph: "A", width: 12, original: false };
  const result = (width, part) => ({ slot_details: { name: {
    label: "Battle name with prefix", width, state: "computed", contributors: [part, name],
  } } });
  const trainer = slotRows(result(20, { id: "prefix-b", width: 8 }), entries)[0];
  assert.equal(trainer.width, 20);
  assert.equal(trainer.parts[0].label, "«D·»");
  assert.equal(trainer.parts[0].width, 8);
  const player = slotRows(result(12, { label: "No prefix", width: 0 }), entries)[0];
  assert.equal(player.width, 12);
  assert.equal(player.parts[0].label, "No prefix");
  assert.equal(player.parts[0].width, 0);
  const unrestricted = slotRows(result(32, { id: "prefix-a", width: 20 }), entries)[0];
  assert.equal(unrestricted.parts[0].label, "«ABC·»");
  const changed = slotRows(result(44, { id: "prefix-b", width: 32 }), entries,
    { "prefix-b": { translation: "ABCDE " } })[0];
  assert.equal(changed.parts[0].label, "«ABCDE·»");
  assert.equal(changed.width, 44);
});

test("a table subset loads and displays only its permitted contributors", async () => {
  const subsetGraph = { dependencies: { subset: ["short", "pointer-name"] } };
  const values = { short: { translation: "AB" }, "pointer-name": { translation: "C" },
    excluded: { translation: "ABCDEFG" } };
  const subsetWindow = { slot_details: { item: { source: "subset", label: "Item name", state: "computed",
    width: 12, contributors: [{ id: "short", width: 12, original: false }] } } };
  const loaded = [];
  const edits = new Map([["pointer-name", { translation: "ABCD", state: "text" }],
    ["excluded", { translation: "ABCDEFGH", state: "text" }]]);
  const { sources, drafts } = await loadSlotSources({ window: subsetWindow }, subsetGraph,
    async (id) => { loaded.push(id); return values[id]; }, edits);
  assert.deepEqual(loaded, ["short", "pointer-name"]);
  assert.deepEqual(Object.keys(drafts), ["pointer-name"]);
  assert.equal(slotRows(subsetWindow, sources)[0].parts[0].label, "«AB»");
  const changed = structuredClone(subsetWindow);
  changed.slot_details.item.width = 24;
  changed.slot_details.item.contributors = [{ id: "pointer-name", width: 24, original: false }];
  const row = slotRows(changed, sources, drafts)[0];
  assert.equal(row.width, 24);
  assert.equal(row.parts[0].id, "pointer-name");
  assert.equal(row.parts[0].label, "«ABCD»");
  assert.deepEqual(slotRows(subsetWindow, sources), slotRows(subsetWindow, values));
});
