import assert from "node:assert/strict";
import { test } from "node:test";

import { chunkPartUrl, decodeChunk, decodeIndex, decodeSource, decodeTerms, defaultStatus, messagesOf, sourceUrl }
  from "../js/editorstore.js";

const SCHEMA = "9.0.0";
const URL_INDEX = "https://site.test/data/game/ru/index.json";
const MESSAGES = {
  box_width: "[layout.box_width] {box}: the message box, measured in game",
  box_lines_via: "two lines per message box ({via})",
  table_window: "[[layout.table_window]] {name}",
  centred: "{rule}, centred against {reference}",
  table_field: "[[table]] {table}: a field of {bytes} bytes with the terminator",
  reach_study_root: "reach study: {class} ({root})",
};

const game = {
  schema: SCHEMA, part: "index", messages: MESSAGES, where_texts: { menu: { ru: "меню" } }, map_order: {}, codec: { chars: {} },
  list: [{ id: "s1", reach: 0, maps: ["hash:1"] }, { id: "s2" }],
  reach: [{ source: "reach study", evidence: { code: "reach_study_root", class: "reachable", root: "pool 0x10" } }],
  notes: [{ id: "note:shared", scope: { kind: "shared" } }],
  fill_sources: { contexts: {}, dependencies: { names: ["s2"] } },
  source: "source.json",
};
const stored = {
  schema: SCHEMA, shared: "../shared/index.json", toolkit: "1.1.0", language: "ru", profile: "game", glossary: {
    "term:bag": { category: "item", source_text: "Bag", translation: "Сумка" } },
  fill_sources: { sources: { names: {} }, alphabet: "АБ", values: { s2: { translation: "", flags: [] } } },
  notes: { schema: "3.0.0", note: [{ id: "note:own", scope: { kind: "language", language: "ru" } }, 0] },
  chunks: [{ file: "po/a.po", path: "entries/0.json", entries: 2, part: "../shared/a.json" }],
  list: [{ translation: "Привет", window: 0, problems: [{ message: "x", severity: "warning" }] }, { status: "nothing" }],
  windows: [{ kind: "dialogue", width: 208, lines: 2, sources: { width: 0, lines: 1 }, consumers: [0, 1],
    slot_details: { STR_VAR_1: 0 }, font: "main" }],
  limit_sources: [{ kind: "measured", rule: { code: "box_width", box: "dialogue" } },
    { kind: "measured", rule: { code: "centred", rule: { code: "box_lines_via", via: { code: "table_window", name: "menu" } },
      reference: "s9" }, evidence: "Measured.", unverified: true }],
  consumers: [{ width: 208, arrow: 10, arrow_source: "default", arrow_confirmed: false, source: 0 },
    { name: "battle menu", width: 200, arrow: 0, arrow_source: "declared", arrow_confirmed: true, source: 1 }],
  slot_details: [{ width: 40, state: "computed", dependencies: ["s2"] }],
  rules: [{ text: "Keep it short.", source: "review", evidence: "" }],
  progress: { approved: 1, nothing: 1 },
};
const part = { schema: SCHEMA, part: "entries", entries: [
  { tokens: ["{STR_VAR_1}"], source_sha256: "a".repeat(64), context: { events: ["npc"], uses: 1 } },
  { tokens: null },
] };
const own = { schema: SCHEMA, entries: [
  { terms: ["term:bag"], rules: [0, 0], note_ids: ["note:own"], inserted: [{ token: "{STR_VAR_1}", width: 40,
    source: { code: "slot_word", id: "s2", text: "'Bag'" } }] },
  { note: null, translator_notes: null, source_text: "kept as written" },
] };

test("the message table of explanations comes with the game part", () => {
  const index = decodeIndex(structuredClone(stored), structuredClone(game), URL_INDEX);
  assert.deepEqual(messagesOf(index), MESSAGES);
  assert.ok(!("messages" in index));
  assert.deepEqual(messagesOf({}), {});
  assert.equal(defaultStatus("Текст"), "approved");
  assert.equal(defaultStatus(""), "untranslated");
});

test("an index takes rows, notes and fill sources from its game part", () => {
  const index = decodeIndex(structuredClone(stored), structuredClone(game), URL_INDEX);
  assert.deepEqual(index.list, [
    { id: "s1", chunk: 0, status: "approved", translation: "Привет", kind: "dialogue", reach: "reachable",
      maps: ["hash:1"], problems: [{ message: "x", severity: "warning" }] },
    { id: "s2", chunk: 0, status: "nothing", translation: "", kind: null, reach: "reachable", maps: [], problems: [] },
  ]);
  assert.deepEqual(Object.keys(index.fill_sources), ["sources", "alphabet", "contexts", "dependencies", "values"]);
  assert.deepEqual(index.notes.note.map((note) => note.id), ["note:own", "note:shared"]);
  assert.deepEqual(index.chunks, [{ file: "po/a.po", path: "entries/0.json", entries: 2 }]);
  assert.deepEqual(index.where_texts, game.where_texts);
  for (const field of ["shared", "windows", "limit_sources", "consumers", "slot_details", "rules"]) assert.ok(!(field in index));
  assert.equal(chunkPartUrl(index, 0), "https://site.test/data/game/shared/a.json");
  assert.equal(sourceUrl(index), "https://site.test/data/game/shared/source.json");
  assert.deepEqual(decodeSource({ schema: SCHEMA, part: "source", profile: "game", rows: {} }), { schema: SCHEMA, profile: "game", rows: {} });
});

test("entries join the list row, the game part and the language part in the generator's key order", () => {
  const index = decodeIndex(structuredClone(stored), structuredClone(game), URL_INDEX);
  const chunk = decodeChunk(index, 0, structuredClone(own), structuredClone(part));
  const [first, second] = chunk.entries;
  assert.equal(chunk.file, "po/a.po");
  assert.deepEqual(Object.keys(first), ["id", "file", "tokens", "translation", "flags", "status", "context", "window", "terms",
    "approved", "note", "reach", "translator_notes", "rules", "source_sha256", "note_ids"]);
  assert.deepEqual(Object.keys(first.context), ["comments", "events", "boxes", "uses", "speakers", "inserted", "words", "hosts",
    "identity", "maps"]);
  // explanations stay codes, each entry its own copy
  assert.deepEqual(first.reach, { class: "reachable", source: "reach study",
    evidence: { code: "reach_study_root", class: "reachable", root: "pool 0x10" } });
  assert.deepEqual(first.window.sources.width, { kind: "measured", rule: { code: "box_width", box: "dialogue" }, evidence: "" });
  assert.notEqual(first.window.sources.width.rule, stored.limit_sources[0].rule);
  assert.deepEqual(first.window.sources.lines.rule, stored.limit_sources[1].rule);
  assert.equal(first.window.sources.lines.unverified, true);
  assert.deepEqual(first.window.consumers.map((c) => c.name), [{ code: "box_width", box: "dialogue" }, "battle menu"]);
  assert.equal(first.window.consumers[0].name, first.window.consumers[0].source.rule);
  assert.deepEqual(first.context.inserted, own.entries[0].inserted);
  assert.deepEqual(Object.keys(first.window.consumers[0]), ["name", "width", "arrow", "arrow_source", "arrow_confirmed", "source"]);
  assert.deepEqual(first.window.slot_details, { STR_VAR_1: { width: 40, state: "computed", dependencies: ["s2"] } });
  assert.deepEqual(first.rules, [stored.rules[0], stored.rules[0]]);
  assert.notEqual(first.rules[0], first.rules[1]); // each entry owns its records
  assert.deepEqual(Object.keys(second), ["id", "file", "translation", "flags", "status", "context", "window", "terms",
    "approved", "reach", "rules", "source_text"]);
  assert.equal(second.window, null);
  assert.equal(second.source_text, "kept as written");
  assert.throws(() => decodeChunk(index, 0, { schema: SCHEMA, entries: [] }, part), /entry count/);
});

test("terms take glossary fields from the index and rebuild slots in generation order", () => {
  const index = decodeIndex(structuredClone(stored), structuredClone(game), URL_INDEX);
  const terms = decodeTerms(index, { schema: SCHEMA, terms: [
    { id: "term:bag", declension: "fixed", sources: "", variants: "", confidence: "high", in_source: ["s1"] },
    { id: "term:other", category: "term", source_text: "Other", declension: "auto", sources: "", variants: "", confidence: "low" },
  ], slots: { groups: [["s2", "s3"]], lines: [
    { line: "s1", slot: "STR_VAR_1", words: ["s3"] }, { line: "s1", slot: "STR_VAR_1", group: 0 },
    { line: "s4", slot: "STR_VAR_2", group: 0 },
  ] } });
  assert.deepEqual(terms.terms[0], { id: "term:bag", category: "item", source_text: "Bag", translation: "Сумка",
    declension: "fixed", sources: "", variants: "", confidence: "high", in_source: ["s1"], in_translation: [], form_only: [],
    shortened: [] });
  assert.deepEqual(Object.keys(terms.terms[1]).slice(0, 3), ["id", "category", "source_text"]);
  assert.deepEqual(terms.slots, { s3: [["s1", "STR_VAR_1"], ["s4", "STR_VAR_2"]], s2: [["s1", "STR_VAR_1"], ["s4", "STR_VAR_2"]] });
  assert.deepEqual(Object.keys(terms.slots), ["s3", "s2"]);
});
