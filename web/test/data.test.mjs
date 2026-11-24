// Chunk loading and list filters.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { Chunks, dirOf, filterRows, getJson, groupsOf, idParts, isOverflow, mapsOf, reachLabel, rulesOf, versionLabel }
  from "../js/data.js";
import { fakeFetch, storedLanguage } from "./fixture.mjs";
import { EDITOR_SCHEMA, loadIndex, verifyEditorSchema, projectProfiles, verifyProjectIndex } from "../js/data.js";
import { loadAliases, placeOf, tableKey, tailId } from "../js/data.js";
import { checkDoc } from "../js/check.js";

test("keyboard routes retain their target kind and decoded identity", () => {
  for (const kind of ["page", "chat"]) {
    const id = "Mixed/name %2F";
    assert.deepEqual(placeOf(["keyboards", kind, id]), { view: "keyboards", kind, id });
  }
  assert.deepEqual(placeOf(["keyboards", "0"]), { view: "keyboards", id: "0" });
});

test("glyph routes retain the sheet and code without changing sheet addresses", () => {
  const id = "sheet/one %2F";
  for (const code of ["BB", "105"]) {
    assert.deepEqual(placeOf(["fonts", id, "glyph", code]), { view: "fonts", kind: "glyph", id, code });
  }
  assert.deepEqual(placeOf(["fonts", id]), { view: "fonts", id });
  for (const tail of [["fonts", id, "glyph"], ["fonts", id, "glyph", "BB", "extra"]]) {
    assert.deepEqual(placeOf(tail), { view: "fonts", kind: "glyph", id, code: null });
  }
});

test("checks receive the selected project's rules and preserve legacy fallback", () => {
  const first = { check_rules: { forbidden: { x: "first" } } };
  const second = { check_rules: {} };
  assert.deepEqual(JSON.parse(checkDoc(first)).check_rules, first.check_rules);
  assert.deepEqual(JSON.parse(checkDoc(second)).check_rules, {});
  assert.equal(Object.hasOwn(JSON.parse(checkDoc({})), "check_rules"), false);
});

test("projects separate identical language codes and reject identity mismatches", () => {
  const profiles = projectProfiles({
    profiles: [{ id: "first", name: "First", support: "translation" },
      { id: "second", name: "Second", support: "translation" },
      { id: "starter", name: "Starter", support: "discovery" }],
    languages: [{ profile: "first", code: "ru", index: "first/ru/index.json" },
      { profile: "second", code: "ru", index: "second/ru/index.json" },
      { profile: "starter", code: "ru", index: "stale/index.json" }],
  });
  assert.equal(profiles[0].languages[0].index, "first/ru/index.json");
  assert.equal(profiles[1].languages[0].index, "second/ru/index.json");
  assert.deepEqual(profiles[2].languages, []);
  assert.doesNotThrow(() => verifyProjectIndex({ schema: EDITOR_SCHEMA, profile: "first", language: "ru" }, "first", "ru"));
  assert.throws(() => verifyProjectIndex({ profile: "second", language: "ru" }, "first", "ru"), /identity/);
  assert.throws(() => verifyProjectIndex({ profile: "first", language: "fr" }, "first", "ru"), /identity/);
});

test("legacy manifests remain usable", () => {
  const language = { profile: "first", code: "ru", index: "ru/index.json" };
  const profiles = projectProfiles({ languages: [language] });
  assert.deepEqual(profiles, [{ id: "first", name: "first", support: "legacy", languages: [language] }]);
  assert.throws(() => projectProfiles({ languages: [language, language] }), /Duplicate editor project/);
  assert.throws(() => projectProfiles({ languages: [{ code: "ru" }] }), /identity/);
});

const INDEX = {
  schema: EDITOR_SCHEMA,
  chunks: [
    { file: "translation/po/maps/bellin-town.po", path: "entries/0.json", entries: 2 },
    { file: "translation/po/battle.po", path: "entries/1.json", entries: 1 },
    { file: "translation/po/maps/seaport.po", path: "entries/2.json", entries: 1 },
  ],
};

const ERR = (m) => ({ message: m, severity: "error" });
const WARN = (m) => ({ message: m, severity: "warning" });

const ROWS = [
  { id: "s0000100", chunk: 0, status: "approved", source_text: "Hi,\\nthere!", translation: "Привет,\\nтам!",
    kind: "dialogue", maps: ["Bellin Town"], reach: "reachable", problems: [] },
  { id: "s0000200", chunk: 0, status: "untranslated", source_text: "Potion", translation: "", kind: "single",
    maps: ["Bellin Town"], reach: "never shown", problems: [] },
  { id: "s0000300", chunk: 1, status: "fuzzy", source_text: "It's super effective!", translation: "Очень эффективно!",
    kind: "battle", maps: [], reach: "possibly reachable", problems: [ERR("line too wide: 230 > 208 px: 'x'")] },
  { id: "s0000400", chunk: 2, status: "approved", source_text: "Seaport", translation: "Порт", kind: "single",
    maps: ["Seaport City"], problems: [WARN("glossary term 'Seaport' -> 'Сипорт' not found (warning)")] },
];

const ids = (f, edits) => filterRows(ROWS, f, edits, groupsOf(INDEX.chunks)).map((i) => ROWS[i].id);

const DATA = "https://site.test/data/ru/";

test("chunks load once and give entries by row", async () => {
  const files = storedLanguage(`${DATA}index.json`, { language: "ru", chunks: INDEX.chunks, list: ROWS });
  delete files[`${DATA}entries/1.json`];
  const fetch = fakeFetch(files);
  const index = await loadIndex(`${DATA}index.json`, fetch);
  const c = new Chunks(DATA, index, fetch);
  fetch.calls.length = 0;
  assert.equal((await c.entry(index.list[0])).id, "s0000100");
  assert.equal((await c.entry(index.list[1])).translation, "");
  assert.deepEqual(fetch.calls, [`${DATA}entries/0.json`, "https://site.test/data/shared/ru-0.json"]);
  await assert.rejects(c.entry(index.list[2]), /HTTP 404/);
  await assert.rejects(c.entry({ id: "s0000999", chunk: 0 }), /not in entries\/0.json/);
  await assert.rejects(c.entry(index.list[2]), /HTTP 404/); // retried, not cached
  assert.equal(fetch.calls.filter((u) => u.endsWith("entries/1.json")).length, 2);
});

test("decoded rows and entries take their defaults from the format", async () => {
  const files = storedLanguage(`${DATA}index.json`, { language: "ru", chunks: INDEX.chunks, list: ROWS });
  const fetch = fakeFetch(files);
  const index = await loadIndex(`${DATA}index.json`, fetch);
  assert.deepEqual(index.list.map(({ id, chunk, status, kind, reach, maps }) => ({ id, chunk, status, kind, reach, maps })),
    ROWS.map(({ id, chunk, status, reach = "reachable", maps }) => ({ id, chunk, status, kind: null, reach, maps })));
  const entry = await new Chunks(DATA, index, fetch).entry(index.list[3]);
  assert.deepEqual(Object.keys(entry), ["id", "file", "tokens", "translation", "flags", "status", "context", "window",
    "terms", "approved", "note", "reach", "translator_notes", "rules", "source_text"]);
  assert.deepEqual(entry.context, { comments: [], events: [], boxes: [], uses: 0, speakers: [], inserted: [], words: [],
    hosts: [], identity: { schema: "2.0.0", speaker: { in_source: "not-stated", basis: "", sources: [] },
      addressee: { in_source: "not-stated", basis: "", sources: [] } }, maps: ["Seaport City"] });
  assert.deepEqual(entry.reach, { class: "reachable", source: "not studied", evidence: "" });
});

test("old indexes, term files and chunks cannot mix with current data", async () => {
  for (const schema of [undefined, 1, "1.0.0", "2.0.0", "2.1.0", "7.4.0", "8.1.0", "10.0.0"]) {
    assert.throws(() => verifyProjectIndex({ schema, profile: "fixture", language: "xx" }, "fixture", "xx"), /schema/);
    assert.throws(() => verifyEditorSchema({ schema, terms: [] }), /schema/);
    assert.throws(() => new Chunks("", { schema, chunks: [] }), /schema/);
    const files = storedLanguage(`${DATA}index.json`, { language: "ru", list: [{ id: "s1", status: "approved" }] });
    const index = await loadIndex(`${DATA}index.json`, fakeFetch(files));
    const current = files[`${DATA}entries/0.json`];
    const documents = [{ ...current, schema }, current];
    const chunks = new Chunks(DATA, index, async (url) => ({
      ok: true, json: async () => (url.endsWith("entries/0.json") ? documents.shift() : files[url]),
    }));
    await assert.rejects(chunks.get(0), /schema/);
    assert.equal((await chunks.get(0)).byId.get("s1").id, "s1");
    await assert.rejects(loadIndex(`${DATA}index.json`, fakeFetch({ ...files, [`${DATA}index.json`]:
      { ...files[`${DATA}index.json`], schema } })), /schema/);
  }
  assert.equal(EDITOR_SCHEMA, "9.0.0");
  // the data of the previous schema still loads
  const previous = storedLanguage(`${DATA}index.json`, { language: "ru", list: [{ id: "s1", status: "approved" }] });
  for (const doc of Object.values(previous)) if (doc.schema === EDITOR_SCHEMA) doc.schema = "8.0.0";
  const index = await loadIndex(`${DATA}index.json`, fakeFetch(previous));
  assert.equal(index.schema, "8.0.0");
  assert.equal((await new Chunks(DATA, index, fakeFetch(previous)).get(0)).byId.get("s1").id, "s1");
  for (const schema of ["8.0.0", "9.0.0"]) assert.doesNotThrow(() => verifyEditorSchema({ schema, terms: [] }));
  assert.throws(() => verifyEditorSchema({ schema: "7.4.0", terms: [] }), /Catalog schema does not match 9\.0\.0/);
});

test("JSON and paths", async () => {
  assert.deepEqual(await getJson("x", fakeFetch({ x: { a: 1 } })), { a: 1 });
  const calls = [];
  const fetcher = async (...args) => {
    calls.push(args);
    return { ok: true, json: async () => ({ fresh: true }) };
  };
  assert.deepEqual(await getJson("data.json", fetcher), { fresh: true });
  assert.deepEqual(calls, [["data.json", { cache: "no-cache" }]]);
  assert.equal(dirOf("ru/index.json"), "ru/");
});

test("PO files grouped by folder", () => {
  assert.deepEqual(groupsOf(INDEX.chunks), [
    { group: "maps", files: [{ chunk: 0, name: "bellin-town", entries: 2 }, { chunk: 2, name: "seaport", entries: 1 }] },
    { group: "(top)", files: [{ chunk: 1, name: "battle", entries: 1 }] },
  ]);
  assert.deepEqual(mapsOf(ROWS), ["Bellin Town", "Seaport City"]);
});

test("filters: status, file, map and window", () => {
  assert.deepEqual(ids({}), ["s0000100", "s0000200", "s0000300", "s0000400"]);
  assert.deepEqual(ids({ status: "untranslated" }), ["s0000200"]);
  assert.deepEqual(ids({ status: "fuzzy" }), ["s0000300"]);
  assert.deepEqual(ids({ group: "maps" }), ["s0000100", "s0000200", "s0000400"]);
  assert.deepEqual(ids({ chunk: "1" }), ["s0000300"]);
  assert.deepEqual(ids({ map: "Bellin Town" }), ["s0000100", "s0000200"]);
  assert.deepEqual(ids({ kind: "single" }), ["s0000200", "s0000400"]);
});

test("filters: problems, with the page's edits first", () => {
  assert.ok(isOverflow(ERR("too many lines: 4 > 3")));
  assert.ok(isOverflow(WARN("written line wider than the window: box 1: 220 > 208 px")));
  assert.ok(!isOverflow(WARN("box 1 wraps to 3 lines, the English box has 2 (warning)")));
  assert.ok(isOverflow(ERR("pair too wide: 117 > 111 px in HUD (hud) with «A» table:abilities/1, 61 px, for «B» table:species/1")));
  assert.deepEqual(ids({ problems: "overflow" }), ["s0000300"]);
  assert.deepEqual(ids({ problems: "errors" }), ["s0000300"]);
  assert.deepEqual(ids({ problems: "warnings" }), ["s0000400"]);
  const edits = new Map([["s0000300", { translation: "Эффективно!", problems: [] }],
    ["s0000100", { translation: "Ну\\nи ну", problems: [ERR("3 lines in one box without scrolling")] }]]);
  assert.deepEqual(ids({ problems: "overflow" }, edits), ["s0000100"]);
  assert.deepEqual(ids({ status: "edited" }, edits), ["s0000100", "s0000300"]);
});

test("filters: reach class", () => {
  assert.deepEqual(ids({ reach: "never shown" }), ["s0000200"]);
  assert.deepEqual(ids({ reach: "possibly reachable" }), ["s0000300"]);
  assert.deepEqual(ids({ reach: "reachable" }), ["s0000100", "s0000400"]); // no class: reachable
  assert.deepEqual(ids({ reach: "never shown", status: "approved" }), []);
  const html = readFileSync(new URL("../editor.html", import.meta.url), "utf8");
  const select = html.match(/<select id="f-reach"[^]*?<\/select>/)[0];
  assert.deepEqual([...select.matchAll(/<option value="([^"]*)"/g)].map((m) => m[1]),
    ["", "reachable", "possibly reachable", "never shown"]);
  assert.match(html, /<span id="e-reach" class="badge never" hidden data-ui="never shown">never shown<\/span>/);
});

// Entries showing a term's label for the full form
test("filters: shortened term forms", () => {
  const rows = [...ROWS, { id: "moves:186", chunk: 1, status: "approved", source_text: "Sweet Kiss",
    translation: "Сладкий поц.", kind: "field", maps: [], shortened: ["move:sweet-kiss"], problems: [] }];
  const keep = (f) => filterRows(rows, f).map((i) => rows[i].id);
  assert.deepEqual(keep({ terms: "shortened" }), ["moves:186"]);
  assert.deepEqual(keep({ terms: "shortened", status: "fuzzy" }), []);
  assert.equal(keep({ terms: "" }).length, rows.length);
});

test("reach label: class, source and evidence", () => {
  const ev = "review: Teachy TV: no code reads item 366";
  assert.deepEqual(reachLabel({ class: "never shown", source: "review", evidence: ev }),
    { text: "never shown (review)", title: ev, never: true });
  assert.deepEqual(reachLabel({ class: "possibly reachable", source: "reach study",
    evidence: "reach study: possibly reachable (unaligned 0x10b2e53)" }),
  { text: "possibly reachable (reach study)", title: "reach study: possibly reachable (unaligned 0x10b2e53)",
    never: false });
  assert.deepEqual(reachLabel({ class: "reachable", source: "not studied", evidence: "" }),
    { text: "reachable (not studied)", title: "reachable", never: false });
  assert.deepEqual(reachLabel(undefined), reachLabel({ class: "reachable", source: "not studied", evidence: "" }));
});

test("search: English, translation or id, across breaks", () => {
  assert.deepEqual(ids({ search: "hi, there" }), ["s0000100"]);
  assert.deepEqual(ids({ search: "ПОРТ" }), ["s0000400"]);
  assert.deepEqual(ids({ search: "s0000300" }), ["s0000300"]);
  assert.deepEqual(ids({ search: "эффективно" }, new Map([["s0000300", { translation: "Супер!", problems: [] }]])), []);
});

test("status bar: the toolkit version and generating commit", () => {
  const html = readFileSync(new URL("../editor.html", import.meta.url), "utf8");
  assert.match(html, /id="sb-version"/);
  assert.doesNotMatch(html, /id="version"/);
  assert.deepEqual(versionLabel("0.2.0+1a2b3c4"),
    { text: "0.2.0", title: "gen3tl 0.2.0: editor data generated at commit 1a2b3c4" });
  assert.deepEqual(versionLabel("0.2.0+1a2b3c4.dirty"),
    { text: "0.2.0", title: "gen3tl 0.2.0: editor data generated at commit 1a2b3c4 with uncommitted changes" });
  assert.deepEqual(versionLabel("0.2.0"),
    { text: "0.2.0", title: "gen3tl 0.2.0: editor data generated at an unknown commit" });
  assert.deepEqual(versionLabel(undefined), { text: "", title: "" }); // data without a version
});

test("rules: the entry's list, ids in the text linked", () => {
  const rule = { text: "Moves with s03FD407 as one block.", source: "profile [[strings.computed]] 0x9BDE20",
    evidence: "ldr r2 at 0x89BDD40" };
  assert.deepEqual(rulesOf({ id: "s03FD3C7", rules: [rule] }), [rule]);
  assert.deepEqual(rulesOf({ id: "s03FD3C7" }), []); // data without rules
  const known = (id) => ["s03FD407", "s03FD535"].includes(id);
  assert.deepEqual(idParts(rule.text, known), [{ text: "Moves with " }, { id: "s03FD407" }, { text: " as one block." }]);
  assert.deepEqual(idParts("as s03FD535 and s03FD4FA do.", known),
    [{ text: "as " }, { id: "s03FD535" }, { text: " and s03FD4FA do." }]); // s03FD4FA unknown
  assert.deepEqual(idParts("s03FD535", known), [{ id: "s03FD535" }]);
  assert.deepEqual(idParts("No ids.", known), [{ text: "No ids." }]);
  const html = readFileSync(new URL("../editor.html", import.meta.url), "utf8");
  assert.match(html, /<section class="ctx-sec"><h2 class="ctx-title"><span data-ui="Rules">Rules<\/span> <span id="rules-note" class="muted"><\/span><\/h2><ul id="rules" class="rules"><\/ul><\/section>/);
});

test("status: nothing to translate is no untranslated entry", async () => {
  const { statusLabel } = await import("../js/data.js");
  const rows = [...ROWS, { id: "items:54", chunk: 1, status: "nothing", source_text: "", translation: "", kind: "field",
    maps: [], problems: [] }];
  const keep = (f) => filterRows(rows, f).map((i) => rows[i].id);
  assert.deepEqual(keep({ status: "untranslated" }), ["s0000200"]);
  assert.deepEqual(keep({ status: "nothing" }), ["items:54"]);
  assert.equal(keep({}).length, 5);
  assert.deepEqual(["approved", "fuzzy", "untranslated", "nothing"].map(statusLabel),
    ["approved", "translated", "untranslated", "nothing to translate"]);
  const html = readFileSync(new URL("../editor.html", import.meta.url), "utf8");
  const select = html.match(/<select id="f-status"[^]*?<\/select>/)[0];
  assert.deepEqual([...select.matchAll(/<option value="([^"]*)"/g)].map((m) => m[1]),
    ["", "untranslated", "approved", "fuzzy", "nothing", "edited"]);
});


test("catalog keys: linked in text, older ids resolved", async () => {
  const known = (id) => ["map:3.48/npc:0/1", "hash:0a1b2c3d", "table:species/25", "s03FD535"].includes(id);
  assert.deepEqual(idParts("prev map:3.48/npc:0/1: Hi, hash:0a1b2c3d and table:species/25.", known), [
    { text: "prev " }, { id: "map:3.48/npc:0/1" }, { text: ": Hi, " }, { id: "hash:0a1b2c3d" }, { text: " and " },
    { id: "table:species/25" }, { text: "." }]);
  assert.deepEqual(idParts("as s03FD535 do", known), [{ text: "as " }, { id: "s03FD535" }, { text: " do" }]);
  assert.equal(tableKey("moves:5"), "table:moves/5");
  assert.equal(tableKey("battle:5"), "battle:5");
  assert.equal(tableKey("s03FD535"), "s03FD535");
  assert.equal(tailId(["map:3.48", "npc:0", "1"]), "map:3.48/npc:0/1");
  assert.equal(tailId(["hash:0a1b2c3d"]), "hash:0a1b2c3d");
  const fetcher = fakeFetch({ "data/p/keys.json": { schema: 1, ids: { s03FD535: "hash:0a1b2c3d" } } });
  assert.equal((await loadAliases("data/p/keys.json", fetcher)).get("s03FD535"), "hash:0a1b2c3d");
  assert.equal((await loadAliases("data/q/keys.json", fetcher)).size, 0);
});

test("readable catalogs open in the Pokédex while editor choices require indexes", () => {
  const manifest = {
    profiles: [{ id: "sample", support: "catalog" }, { id: "candidate", support: "discovery" }],
    languages: [
      { profile: "sample", code: "de", texts: "de/texts.json" },
      { profile: "sample", code: "fr", index: "fr/index.json" },
      { profile: "candidate", code: "es", index: "es/index.json" },
    ],
  };
  const editable = projectProfiles(manifest);
  assert.deepEqual(editable.map((profile) => profile.languages.map((language) => language.code)), [["fr"], []]);
  const readable = projectProfiles(manifest, { readOnly: true });
  assert.deepEqual(readable.map((profile) => profile.languages.map((language) => language.code)), [["de", "fr"], ["es"]]);
  for (const readOnly of [true, false]) {
    assert.throws(() => projectProfiles({ languages: [{ profile: "sample", code: "de" }] }, { readOnly }), /identity/);
    assert.throws(() => projectProfiles({ languages: [manifest.languages[0], manifest.languages[0]] }, { readOnly }), /Duplicate/);
  }
});
