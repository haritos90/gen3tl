// The submit page: rows, widths and counts of an export.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { checkFailed, setMessages } from "../js/checktext.js";
import { Codec, entryMeasure } from "../js/font.js";
import { TOOLKIT_REPOSITORY } from "../js/registry.js";
import { Edits, NoteEdits, RuleEdits, exportDoc, termRecord } from "../js/store.js";
import { issueLink, submissionDoc, submissionParts, submitChanges } from "../js/submit.js";
import {
  appendedLabel, areasLabel, countsLabel, draftOrder, exportCounts, fileDoc, glyphsOf, headingOf, issueBlocked, keyboardRows,
  lineWidths, links, noteCounts, noteIssues, noteItem, noteRows, pictureChange, ruleChanges, ruleItem, siteGame, slotsOf, termRows,
  textChange, widthCell, windowLabel,
} from "../js/submitpage.js";
import { setCatalog, setUiLanguage } from "../js/ui.js";
import { CODEC, PACK, STYLE, fakeFetch, fakeStorage, noteCollection, storedLanguage, stubPage } from "./fixture.mjs";

const REPO = "example-owner/example-repo";
const INDEX = {
  fonts: { main: "main", styles: { main: STYLE } },
  placeholders: { widths: { PLAYER: 42 }, default: 60 },
};
const codec = new Codec(CODEC, PACK);
const measure = entryMeasure(INDEX, glyphsOf(INDEX), codec, null);
const read = (name) => readFileSync(new URL(name, import.meta.url), "utf8");

test("every envelope writer versions game, site, pack and mixed exports", () => {
  const entries = [{ id: "entry", translation: "Draft" }], glyphs = [{ style: "main", char: "A" }];
  for (const [profile, texts, art] of [["fixture", entries, []], ["site", entries, []],
    ["fixture", [], glyphs], ["fixture", entries, glyphs]]) {
    const exported = exportDoc("xx", profile, texts, [], [], art);
    assert.equal(exported.version, "4.0.0");
    const { version, ...legacy } = exported;
    for (const source of [exported, legacy]) {
      assert.equal(submissionDoc(source).version, "4.0.0");
      assert.equal(fileDoc(source).version, "4.0.0");
      const parts = submissionParts(source, REPO);
      assert.equal(parts.length, texts.length && art.length ? 2 : 1);
      for (const part of parts) assert.equal(part.doc.version, "4.0.0");
    }
  }
});

test("submit header keeps English language names in every site language", (ctx) => {
  const { $: node } = stubPage(ctx, "submit");
  ctx.after(() => setUiLanguage("en"));
  for (const locale of ["en", "ru", "de"]) {
    setUiLanguage(locale);
    for (const [code, name] of [["ru", "Russian"], ["de", "German"], ["es", "Spanish"], ["fr", "French"]]) {
      links({ id: "game", name: "Game" }, code);
      assert.equal(node("eyebrow").textContent, `Game · ${name}`);
      assert.equal(node("link-editor").getAttribute("href"), `editor#game/${code}`);
    }
  }
});

test("the site project from the manifest submits to the toolkit repository", () => {
  const manifest = { profiles: [{ id: "site", name: "Site interface", support: "interface" }],
    languages: [{ profile: "site", code: "xx" }, { profile: "game", code: "xx" }] };
  assert.deepEqual(siteGame(manifest, "https://example.org/data/"), {
    id: "site", name: "", stage: "interface", engine: "unspecified", languages: ["xx"],
    data: "https://example.org/data/", repository: TOOLKIT_REPOSITORY });
  assert.equal(siteGame({ profiles: [], languages: [] }, "https://example.org/data/"), null);
});

test("lines are measured per box with the pack widths and stand-ins", () => {
  // Б 6 px and б 5 px from the pack
  assert.deepEqual(lineWidths("Бб\\nAA {PLAYER}", measure), [[{ text: "Бб", width: 11 }, { text: "AA {PLAYER}", width: 60 }]]);
  assert.deepEqual(lineWidths("A\\pЖ", measure), [[{ text: "A", width: 6 }], [{ text: "Ж", width: null }]]);
  assert.deepEqual(lineWidths("@VERSION@", measure, "1"), [[{ text: "1", width: 6 }]]);
});

test("a redrawn glyph is measured at its new width before any import", () => {
  const drafted = entryMeasure(INDEX, glyphsOf(INDEX, [{ style: "main", code: "37", width: 9, rows: [] }]), codec, null);
  assert.deepEqual(lineWidths("Бб", drafted), [[{ text: "Бб", width: 14 }]]); // Б 9 px drawn, б 5 px
  assert.deepEqual(lineWidths("Бб", measure), [[{ text: "Бб", width: 11 }]]);
  const window = { kind: "dialogue", width: 12 };
  assert.deepEqual([widthCell("Бб", lineWidths("Бб", measure), window).state, widthCell("Бб", lineWidths("Бб", drafted), window).state],
    ["ok", "over"]);
});

test("the width cell shows the widest line against the window", () => {
  const lines = lineWidths("Бб\\nAA", measure);
  assert.deepEqual(widthCell("Бб\\nAA", lines, { kind: "dialogue", width: 11 }),
    { label: "12 px", state: "over", title: "line 1: 11 / 11 px\nline 2: 12 / 11 px" });
  assert.equal(widthCell("Бб\\nAA", lines, { kind: "dialogue", width: 12 }).state, "ok");
  assert.equal(widthCell("Бб\\nAA", lines, null).state, "plain");
  assert.equal(widthCell("Бб\\nAA", lines, { kind: "naming", keys: 8, width: 5 }).state, "plain");
  assert.match(widthCell("A\\pБ", lineWidths("A\\pБ", measure), null).title, /^line 1\.1: 6 px\nline 2\.1: 6 px$/);
  assert.deepEqual(widthCell("Ж", lineWidths("Ж", measure), null), { label: "not encodable", state: "over", title: "line 1: not encodable" });
  assert.equal(widthCell("", null).label, "—");
  assert.deepEqual(widthCell("Бб", null, { width: 10 }), { label: "—", state: "plain", title: "not measured: no game font for this catalog" });
});

test("the entry head names the window and the inserted values", () => {
  assert.equal(windowLabel({ kind: "dialogue", width: 208 }), "dialogue · 208 px");
  assert.equal(windowLabel({ kind: "naming", keys: 7, width: 60 }), "naming · 7 keys");
  assert.equal(windowLabel(null), "");
  assert.deepEqual(slotsOf("{PLAYER}, {COLOR 01}{STR_VAR_1} {PLAYER}{LV}", codec), ["{PLAYER}", "{STR_VAR_1}"]);
});

test("a changed text: before, after, new, stale and the editor's check", () => {
  const row = { id: "s1", source_text: "Hi", translation: "Привет", maps: ["Town"] };
  const edit = { translation: "Здравствуй", base: "Привет", source_text: "Hi", note: "",
    problems: [{ message: "line too wide", severity: "error" }, { message: "w", severity: "warning" }] };
  assert.deepEqual(textChange("s1", edit, row), {
    id: "s1", source_text: "Hi", before: "Привет", after: "Здравствуй", maps: ["Town"], fresh: false, stale: false,
    beforeState: "text", afterState: "text",
    errors: 1, warnings: 1, problems: ["line too wide", "w"],
  });
  const fresh = textChange("s2", { translation: "Да", base: "", problems: null }, { source_text: "Yes", translation: "", maps: [] });
  assert.equal(fresh.fresh, true);
  assert.equal(fresh.errors, 0);
  assert.equal(textChange("s1", { ...edit, base: "Прежде" }, row).stale, true);
  assert.equal(textChange("s1", { ...edit, source_text: "Hello" }, row).stale, true);
  assert.equal(textChange("s9", edit, undefined).stale, false);
});

test("a changed text's problems follow the site language", (ctx) => {
  ctx.after(() => { setCatalog(null); setMessages(null); });
  // the data's templates, as the page sets them from its index
  setMessages({ line_wide: "line too wide: {width} > {limit} px: {text}", warning: "{message} (warning)" });
  const edit = { translation: "Здравствуй", base: "Привет", problems: [{ severity: "warning",
    message: { code: "warning", message: { code: "line_wide", width: 210, limit: 196, text: "'Здравствуй'" } } }] };
  setCatalog({ "line too wide: {width} > {limit} px: {text}": "строка слишком широкая: {width} > {limit} px: {text}",
    "(warning)": "(предупреждение)" });
  assert.deepEqual(textChange("s1", edit, null).problems,
    ["строка слишком широкая: 210 > 196\u00a0px: 'Здравствуй' (предупреждение)"]);
  setCatalog(null);
  assert.deepEqual(textChange("s1", edit, null).problems, ["line too wide: 210 > 196\u00a0px: 'Здравствуй' (warning)"]);
});

test("a stored draft problem is English and shows in the site language", (ctx) => {
  ctx.after(() => setCatalog(null));
  const edit = { translation: "Привет", base: "", problems: [
    { message: "Preserve the source placeholders and control codes.", severity: "error" },
    { message: checkFailed("no bundle"), severity: "error" },
    { message: "line too wide: 210 > 196 px: 'Привет' (warning)", severity: "warning" }] };
  setCatalog({ "Preserve the source placeholders and control codes.": "Сохраните подстановки и управляющие коды оригинала.",
    "check failed: {error}": "проверка не удалась: {error}" });
  // a problem an older page stored as text shows as stored
  assert.deepEqual(textChange("s1", edit, null).problems, ["Сохраните подстановки и управляющие коды оригинала.",
    "проверка не удалась: no bundle", "line too wide: 210 > 196\u00a0px: 'Привет' (warning)"]);
  setCatalog(null);
  assert.deepEqual(textChange("s1", edit, null).problems, ["Preserve the source placeholders and control codes.",
    "check failed: no bundle", "line too wide: 210 > 196\u00a0px: 'Привет' (warning)"]);
});

test("drafts follow the catalog order; language-pack rows and lost ids stay out", () => {
  const rows = [{ id: "a" }, { id: "k", owner: "pack" }, { id: "b" }, { id: "c" }];
  assert.deepEqual(draftOrder(rows, ["c", "gone", "a", "k"]), { order: ["a", "c"], missing: ["gone"] });
});

test("a changed picture: the project's area form before, the upload after", () => {
  const graphics = {
    inventory: [{ id: "G03", source_text: "START", category: "translate", screen: "title", tiles: "8" }],
    edits: { G03: { areas: [[0, 0, 64, 8]], tiles: null, png: "AAAA" } },
  };
  const upload = { areas: [[0, 0, 60, 8], [0, 8, 30, 8]], tiles: 10, png: "BBBB", name: "start.png" };
  const p = pictureChange("G03", upload, graphics);
  assert.deepEqual(p, {
    id: "G03", source_text: "START", category: "translate", screen: "title", tiles: 8,
    before: { png: "AAAA", areas: [[0, 0, 64, 8]], tiles: null, name: "project's edit" },
    after: { png: "BBBB", areas: upload.areas, tiles: 10, name: "start.png" },
  });
  assert.equal(pictureChange("G09", upload, graphics).before, null);
  assert.deepEqual(areasLabel(p.before.areas), { label: "64×8 px", state: "plain", title: "64×8 px at 0,0" });
  assert.deepEqual(areasLabel(p.after.areas), { label: "2 areas", state: "plain", title: "60×8 px at 0,0\n30×8 px at 0,8" });
  assert.equal(areasLabel(null).label, "—");
  assert.equal(appendedLabel(10, 8), "2 tiles appended");
  assert.equal(appendedLabel(null, 8), "");
});

test("a changed term: each field by the editor's name, forms by key", () => {
  const base = { id: "t1", source_text: "Potion", translation: "Зелье", label: "", gender: "neutral",
    forms: { "gent.sing": "Зелья", "datv.sing": "Зелью" }, notes: "" };
  const row = { ...base, translation: "Снадобье", forms: { "gent.sing": "Снадобья", "datv.sing": "Зелью" }, notes: "item" };
  const rec = termRecord(base, row);
  assert.deepEqual(termRows(rec), [
    { field: "translation", before: "Зелье", after: "Снадобье", text: true },
    { field: "form gent.sing", before: "Зелья", after: "Снадобья", text: true },
    { field: "notes", before: "", after: "item", text: false },
  ]);
  const columns = { translation: "target" };
  assert.deepEqual(termRows(termRecord(base, row, [], columns), columns),
    [{ field: "translation", before: "Зелье", after: "Снадобье", text: true }]);
});

test("term review preserves source keys and multiline historical note edits", () => {
  const columns = { translation: "target", notes: "annotation", sources: "references" };
  const base = { id: "term:one", sources: "reference", note_ids: ["note:one"] };
  assert.deepEqual(termRows(termRecord(base, { ...base, sources: "context" }, [], columns), columns),
    [{ field: "sources", before: "reference", after: "context", text: false }]);
  assert.deepEqual(termRows({ base: { annotation: "First.\nSecond." }, changes: { annotation: "Revised.\nSecond." } }, columns),
    [{ field: "notes", before: "First.\nSecond.", after: "Revised.\nSecond.", text: false }]);
});

test("counts and the heading of the page", () => {
  const doc = exportDoc("xx", "p", [{ id: "a" }, { id: "b" }, { id: "c" }], [], [{ id: "t" }]);
  const n = exportCounts(doc);
  assert.deepEqual(n, { texts: 3, pictures: 0, terms: 1, glyphs: 0, keyboard: 0, rules: 0, notes: 0, total: 4 });
  assert.equal(headingOf(n), "Submit 4 changes");
  assert.equal(headingOf(exportCounts(exportDoc("xx", "p", [{ id: "a" }]))), "Submit 1 change");
  assert.equal(headingOf(exportCounts(exportDoc("xx", "p", []))), "Nothing to submit");
  assert.equal(countsLabel(n), "3 texts, 0 pictures, 1 term");
  const glyphs = exportCounts(exportDoc("xx", "p", [], [], [], [{ style: "main", char: "Б" }]));
  assert.deepEqual([glyphs.glyphs, glyphs.total, countsLabel(glyphs)], [1, 1, "0 texts, 0 pictures, 0 terms, 1 glyph"]);
  const keyboard = { rows: { lower_keys_1: "xб", lower_display_1: "{CLEAR 0B}x" },
    base: { rows: { lower_keys_1: "аб", lower_display_1: "{CLEAR 0B}а" }, pages: ["b", "a"] }, pages: ["a", "b"] };
  const k = exportCounts(exportDoc("xx", "p", [], [], [], [], keyboard));
  assert.deepEqual(k, { texts: 0, pictures: 0, terms: 0, glyphs: 0, keyboard: 3, rules: 0, notes: 0, total: 3 });
  assert.equal(countsLabel(k), "0 texts, 0 pictures, 0 terms, 3 keyboard changes");
  assert.deepEqual(keyboardRows(keyboard).map((r) => [r.id, r.before, r.after]), [
    ["lower_keys_1", "аб", "xб"], ["lower_display_1", "{CLEAR 0B}а", "{CLEAR 0B}x"], ["page order", "b → a", "a → b"]]);
  assert.deepEqual(fileDoc(exportDoc("xx", "p", [], [], [], [], keyboard), () => undefined).keyboard, keyboard);
});

test("the JSON file and the issue carry the same text entries, term edits and notes", () => {
  const changes = [{ id: "s0000100", translation: "Да", note: "" }, { id: "s0000200", translation: "Нет", note: "game" }];
  const bases = { s0000100: "Прежний", s0000200: "" };
  const doc = { ...exportDoc("xx", "test-1.0", changes, [{ id: "G01", base: null, png: "AAAA" }], [{ id: "t1" }]),
    notes: "  The box clipped.  " };
  const file = fileDoc(doc, (id) => bases[id]);
  const sub = submissionDoc(doc, (id) => bases[id]);
  assert.deepEqual(file, { ...sub, pictures: [{ id: "G01", base: null, png: "AAAA" }], glyphs: [] });
  assert.deepEqual(submissionDoc(doc, (id) => bases[id], () => "ef".repeat(32)).pictures,
    [{ id: "G01", base: null, areas: undefined, sha256: "ef".repeat(32) }]);
  assert.deepEqual(sub, {
    version: "4.0.0", language: "xx", profile: "test-1.0", notes: "The box clipped.", terms: [{ id: "t1" }], entries: [
      { id: "s0000100", base: "Прежний", translation: "Да" },
      { id: "s0000200", base: "", translation: "Нет", note: "game" },
    ],
  });
  assert.deepEqual(JSON.parse(new URL(issueLink(sub, REPO).url).searchParams.get("export")), sub);
  const opened = [];
  submitChanges(doc, REPO, { base: (id) => bases[id], download: () => "x.json", open: (url) => opened.push(url) });
  assert.deepEqual(JSON.parse(new URL(opened[0]).searchParams.get("export")), sub);
  const plain = fileDoc(exportDoc("xx", "test-1.0", changes), (id) => bases[id]);
  assert.deepEqual(Object.keys(plain), ["version", "language", "profile", "entries", "pictures", "terms", "glyphs"]);
  assert.deepEqual([plain.pictures, plain.terms, plain.glyphs], [[], [], []]);
  const glyph = { style: "main", char: "Б", width: 3, rows: ["#s_"], base: { width: 3, rows: ["#__"] } };
  assert.deepEqual(fileDoc(exportDoc("xx", "test-1.0", changes, [], [], [glyph]), (id) => bases[id]).glyphs, [glyph]);
});

test("the issue step needs a repository and a change", () => {
  assert.match(issueBlocked(null, { texts: 2 }), /names no repository/);
  assert.equal(issueBlocked(REPO, { texts: 0, terms: 0, pictures: 0, glyphs: 1 }), "");
  assert.equal(issueBlocked(REPO, { texts: 1, terms: 0, pictures: 0 }), "");
  assert.equal(issueBlocked(REPO, { texts: 0, terms: 1, pictures: 0 }), "");
  assert.equal(issueBlocked(REPO, { texts: 0, terms: 0, pictures: 1 }), "");
  assert.match(issueBlocked(REPO, { texts: 0, terms: 0, pictures: 0, glyphs: 0, keyboard: 0 }), /Nothing to submit/);
});

test("the page holds every element its script uses, the steps and the account note", () => {
  const html = read("../submit.html");
  const ids = [...html.matchAll(/id="([^"]+)"/g)].map((m) => m[1]);
  assert.equal(new Set(ids).size, ids.length, "duplicate ids");
  const used = [...read("../js/submitpage.js").matchAll(/\$\("([a-z0-9-]+)"\)/g)].map((m) => m[1]);
  assert.deepEqual([...new Set(used)].filter((id) => !ids.includes(id)), []);
  assert.doesNotMatch(html, /style="|<style/);
  assert.match(html, /A GitHub account is needed only for the issue step\./);
  const steps = [...html.match(/<ol class="next">[^]*?<\/ol>/)[0].matchAll(/<b[^>]*>([^<]+)<\/b>/g)].map((m) => m[1]);
  assert.deepEqual(steps, ["The issue is checked", "A pull request opens", "Checks render the boxes",
    "A maintainer merges it", "A release includes it", "A playtest approves it"]);
  assert.match(read("../editor.html"), /<button id="submit"[^>]*>Submit<\/button>/);
  assert.match(read("../js/editor/actions.js"), /location\.href = toolURL\("submit", state\.profile\.id, state\.lang\.code\)/);
});

function ruleFixture() {
  const storage = fakeStorage();
  const rows = [
    { id: "table:ending/0", translation: "", note: "", source_text: "s" },
    { id: "table:copy/0", translation: "", note: "" },
    { id: "sym:host", translation: "Count", note: "" },
  ];
  const catalog = { id: "ending/group", title: "Grouped ending", kind: "catalog", capability_sha256: "a".repeat(64),
    related: ["sym:host"], operands: [{ id: "suffix", bindings: rows.slice(0, 2).map((row) => ({ entry: row.id })) }],
    sources: [{ entry: rows[0].id, state: "original" }, { entry: rows[1].id, state: "empty" }] };
  const policy = { id: "units", title: "Measurement units", kind: "policy", capability_sha256: "b".repeat(64),
    operands: [], related: ["sym:host"], policies: [{ kind: "code_replacement", fields: ["height_unit", "weight_unit"],
      current: { enabled: false, fields: {} } }] };
  const index = { list: rows, engine_rules: { rules: [catalog, policy] } };
  const edits = new Edits(storage, "aa", "fixture", index);
  const rules = new RuleEdits(storage, "aa", "fixture");
  const record = { id: policy.id, capability_sha256: policy.capability_sha256, base: { enabled: false, fields: {} },
    value: { enabled: true, fields: { height_unit: "m" } } };
  return { storage, rows, catalog, policy, index, edits, rules, record };
}

test("rule review groups changed operands and linked hosts without duplicating exports", (ctx) => {
  stubPage(ctx, "submit");
  const { index, rows, edits, rules, record } = ruleFixture();
  edits.set(rows[0], "", "", [], true, "empty");
  edits.set(rows[2], "New host", "");
  rules.set(record);
  const changes = ruleChanges(index, edits, rules.changes());
  assert.equal(changes.length, 2);
  const catalog = changes.find((row) => row.id === "ending/group");
  assert.deepEqual(catalog.entries, [rows[0].id, rows[2].id]);
  assert.deepEqual(catalog.operands.map((row) => [row.current.state, row.proposed.state]),
    [["original", "empty"], ["empty", "empty"]]);
  assert.equal(catalog.stale, false);
  assert.equal(catalog.baselineMissing, false);
  const article = ruleItem(catalog, "fixture", "aa");
  for (const text of ["Original", "Current", "Proposed", "Inherited original", "Off", "Also changes 2 catalog entries",
    "Original text unavailable without a ROM"]) assert.ok(article.textContent.includes(text), text);
  assert.equal(article.querySelectorAll("a")[0].getAttribute("href"), "editor#fixture/aa/enginerules/ending%2Fgroup");
  assert.ok(article.querySelectorAll("a").some((a) => a.getAttribute("href") === "editor#fixture/aa/table%3Aending%2F0"));
  const policy = changes.find((row) => row.id === "units");
  assert.deepEqual(policy.entries, [rows[2].id]);
  const policyView = ruleItem(policy, "fixture", "aa");
  for (const text of ["Use original units", "Use metric units", "Height unit: m", "Weight unit: Use catalog text"]) {
    assert.ok(policyView.textContent.includes(text), text);
  }
  const doc = exportDoc("aa", "fixture", edits.changes(), [], [], [], null, rules.changes());
  assert.deepEqual(exportCounts(doc), { texts: 2, pictures: 0, terms: 0, glyphs: 0, keyboard: 0, rules: 1, notes: 0, total: 3 });
  assert.equal(doc.entries.length, 2);
  assert.deepEqual(fileDoc(doc).engine_rules, [record]);
});

test("catalog rule baselines warn on changed capabilities, operands, states and removed rules", (ctx) => {
  stubPage(ctx, "submit");
  for (const mutate of [
    ({ catalog }) => { catalog.capability_sha256 = "changed"; },
    ({ rows }) => { rows[1].translation = "x"; },
    ({ catalog }) => { catalog.sources[1].state = "original"; },
    ({ index }) => { index.list = index.list.slice(0, 1); },
    ({ index }) => { index.engine_rules.rules = []; },
  ]) {
    const data = ruleFixture();
    data.edits.set(data.rows[0], "", "", [], true, "empty");
    mutate(data);
    const restored = new Edits(data.storage, "aa", "fixture", data.index);
    const [change] = ruleChanges(data.index, restored);
    assert.equal(change.stale, true);
    const item = ruleItem(change, "fixture", "aa");
    assert.ok(item.textContent.includes("The rule changed since this draft was created"));
  }
});

test("policy rule review retains stale proposals, including missing declarations", () => {
  for (const mutate of [
    ({ policy }) => { policy.capability_sha256 = "changed"; },
    ({ policy }) => { policy.policies[0].current.enabled = true; },
    ({ index }) => { index.engine_rules.rules = []; },
  ]) {
    const data = ruleFixture();
    data.rules.set(data.record);
    mutate(data);
    const restored = new RuleEdits(data.storage, "aa", "fixture");
    const [change] = ruleChanges(data.index, data.edits, restored.changes());
    assert.equal(change.stale, true);
    assert.deepEqual(change.policy, data.record);
    assert.deepEqual(fileDoc(exportDoc("aa", "fixture", [], [], [], [], null, restored.changes())).engine_rules, [data.record]);
  }
});

test("older drafts disclose missing rule baselines and note-only edits do not change rules", () => {
  const { storage, rows, edits, index } = ruleFixture();
  edits.set(rows[0], "", "Context note");
  assert.deepEqual(ruleChanges(index, edits), []);
  const older = new Edits(storage, "aa", "fixture");
  older.clear();
  older.set(rows[0], "x", "");
  const [change] = ruleChanges(index, older);
  assert.equal(change.baselineMissing, true);
  assert.equal(change.stale, false);
  assert.deepEqual(ruleChanges({}, new Edits(storage, "bb", "fixture")), []);
});

test("policy comparison ignores field order and rule-only drafts can be submitted", () => {
  const { index, edits, policy, record } = ruleFixture();
  policy.policies[0].current = { fields: {}, enabled: false };
  assert.equal(ruleChanges(index, edits, [record])[0].stale, false);
  const counts = exportCounts(exportDoc("aa", "fixture", [], [], [], [], null, [record]));
  assert.equal(counts.total, 1);
  assert.equal(headingOf(counts), "Submit 1 change");
  assert.equal(countsLabel(counts), "0 texts, 0 pictures, 0 terms, 1 engine rule");
  assert.equal(issueBlocked(REPO, counts), "");
  assert.equal(issueBlocked(REPO, { rules: 1 }), "");
});

test("blank states stay distinct in text review and detect a stale intentional empty", () => {
  const edit = { translation: "", state: "empty", base: "", base_state: "original" };
  const row = { translation: "", flags: [] };
  assert.equal(textChange("sym:ending", edit, row).stale, false);
  assert.deepEqual([textChange("sym:ending", edit, row).beforeState, textChange("sym:ending", edit, row).afterState],
    ["original", "empty"]);
  assert.equal(textChange("sym:ending", edit, { ...row, flags: ["empty"] }).stale, true);
  assert.equal(textChange("sym:ending", { ...edit, base_state: "empty", state: "original" },
    { ...row, flags: ["empty"] }).fresh, false);
});

test("rule review escapes submitted text and relabels without changing catalog scope", (ctx) => {
  stubPage(ctx, "submit");
  ctx.after(() => setCatalog(null));
  const { rows, edits, index } = ruleFixture();
  edits.set(rows[0], "<b>value</b>", "");
  const [change] = ruleChanges(index, edits);
  setCatalog({ "Changed engine rules": "Rules", "Proposed": "Proposal", "Grouped ending": "Ending" });
  const item = ruleItem(change, "fixture", "aa");
  assert.ok(item.textContent.includes("Proposal"));
  assert.ok(item.textContent.includes("<b>value</b>"));
  assert.equal(item.querySelectorAll("b").length, 0);
  assert.ok(item.querySelectorAll("a").every((a) => a.getAttribute("href").startsWith("editor#fixture/aa/")));
});

test("note changes count additions, edits and removals independently of game changes", () => {
  const base = noteCollection().note[1], drafts = new NoteEdits(fakeStorage(), "xx", "project");
  drafts.set(base.subject, base.binding, { scope: base.scope, guidance: ["New guidance."] });
  drafts.set(base.subject, base.binding, { ...base, guidance: ["Changed guidance."] }, base);
  const removed = { ...base, id: "note:removed" };
  drafts.proposeRemoval(removed);
  const doc = exportDoc("xx", "project", [], [], [], [], null, [], drafts.changes());
  assert.deepEqual(noteCounts(doc.note_changes), { add: 1, edit: 1, remove: 1 });
  assert.deepEqual(exportCounts(doc), { texts: 0, pictures: 0, terms: 0, glyphs: 0, keyboard: 0, rules: 0, notes: 3, total: 3 });
  assert.equal(headingOf(exportCounts(doc)), "Submit 3 changes");
  assert.match(countsLabel(exportCounts(doc)), /3 note changes$/);
  assert.equal(issueBlocked(REPO, { notes: 3 }), "");
  assert.deepEqual(fileDoc(doc).note_changes, doc.note_changes);
  assert.equal(fileDoc(doc).note_changes_schema, "3.0.0");
  const rows = noteRows(doc.note_changes.find((change) => change.operation === "remove"));
  assert.ok(rows.length > 0 && rows.every((row) => row.after === null));
});

test("note review shows exact field changes, citations, scope and separate reviewer context", (context) => {
  stubPage(context, "submit");
  const base = { ...noteCollection().note[0], facts: undefined };
  delete base.facts;
  const drafts = new NoteEdits(fakeStorage(), "xx", "project");
  const quote = '<script>literal quotation</script>\nSecond line.';
  drafts.set(base.subject, base.binding, { ...base, scope: { kind: "language", language: "xx" },
    sources: [{ ...base.sources[0], quote, retrieved: "2026-01-01", locator: "Page 7" }] }, base, base.id, "Check the context.");
  const change = drafts.changes()[0], index = { language: "xx", notes: { schema: "3.0.0", note: [base] } };
  const entries = new Map([[base.subject.id, { source_sha256: base.binding.source_sha256 }]]);
  const rows = noteRows(change);
  assert.deepEqual(rows.find((row) => row.path.join(".") === "sources.1.locator"),
    { path: ["sources", 1, "locator"], before: "Section 4", after: "Page 7" });
  assert.equal(rows.find((row) => row.path.join(".") === "sources.1.quote").after, quote);
  assert.deepEqual(noteIssues(change, index, entries), []);
  const view = noteItem(change, index, entries, ["xx", "other"]);
  assert.match(view.textContent, /Sources · 1 · Locator/);
  assert.ok(view.textContent.includes(quote));
  assert.match(view.textContent, /Subject: entry · sym:one/);
  assert.match(view.textContent, /Language: xx/);
  assert.match(view.textContent, /Message to reviewers: Check the context/);
  assert.equal(view.querySelectorAll("script").length, 0);
  const updated = { ...index, notes: { ...index.notes, note: [{ ...base, guidance: ["Remote change."] }] } };
  assert.match(noteIssues(change, updated, entries).join(" "), /published note or entry changed/);
  assert.match(noteIssues(change, index, new Map()).join(" "), /published note or entry changed/);
  assert.match(noteIssues(change, index, new Map([[base.subject.id, { source_sha256: "b".repeat(64) }]])).join(" "), /published note or entry changed/);
  const missing = { ...index, notes: { schema: "3.0.0", note: [] } };
  assert.match(noteIssues(change, missing, entries).join(" "), /published note or entry changed/);
  drafts.proposeRemoval(base);
  assert.deepEqual(noteIssues(drafts.changes()[0], index, entries), []);
});

test("Submit loads note-only drafts and blocks stale notes while keeping JSON available", async (context) => {
  const { $, storage } = stubPage(context, "submit", "#project/xx");
  const source = read("../js/submitpage.js")
    .replace(/from "(\.\/[^\"]+)"/g, (_, path) => `from "${new URL(`../js/${path.slice(2)}`, import.meta.url).href}"`)
    .replace(/if \(typeof document[^\n]+main\(\);/, "export { state, load, render, wire };");
  const page = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}#${encodeURIComponent(context.name)}`);
  const note = noteCollection().note[1];
  const entry = { id: note.subject.id, source_sha256: note.binding.source_sha256, translation: "", chunk: 0 };
  const stored = storedLanguage("https://site.test/data/xx/index.json", { profile: "project", language: "xx", codec: CODEC,
    charmap: PACK, capabilities: { check: "tokens" }, chunks: [{ file: "source.po", path: "chunk.json", entries: 1 }],
    notes: { schema: "3.0.0", note: [note] } }, [entry]);
  globalThis.fetch = fakeFetch({ ...stored,
    "https://site.test/data/registry.json": { schema: 1, games: [
      { id: "project", name: "Game", stage: "translation", languages: ["xx", "other"], data: "./", repository: REPO }] },
    "https://site.test/data/languages.json": { profiles: [{ id: "project", support: "translation" }],
      languages: [{ profile: "project", code: "xx", index: "xx/index.json" }] },
  });
  const drafts = new NoteEdits(storage, "xx", "project");
  drafts.set(note.subject, note.binding, { ...note, guidance: ["Changed guidance."] }, note);
  page.state.storage = storage;
  await page.load();
  page.render();
  assert.equal($("heading").textContent, "Submit 1 change");
  assert.equal($("note-changes").hidden, false);
  assert.equal($("texts").hidden, true);
  assert.equal($("note-counts").textContent, "0 additions, 1 edits, 0 removals");
  assert.match($("note-list").textContent, /Changed guidance/);
  assert.equal($("issue").disabled, false);
  assert.equal($("download").disabled, false);
  assert.equal(page.state.entries.get(note.subject.id).source_sha256, note.binding.source_sha256);
  page.state.entries.set(note.subject.id, { ...entry, source_sha256: "b".repeat(64) });
  page.render();
  assert.equal($("issue").disabled, true);
  assert.equal($("download").disabled, false);
  assert.match($("issue-note").textContent, /Resolve the note draft problems/);
  page.state.doc.keyboard = { rows: { lower: "xy" }, base: { rows: { lower: "xx" } } };
  page.render();
  assert.equal($("split-issues").hidden, false);
  assert.equal($("toolkit-issue").hidden, false);
  assert.equal($("toolkit-issue").disabled, false);
  page.wire();
  const opened = [];
  window.open = (url) => opened.push(new URL(url));
  $("toolkit-issue").listeners.click();
  assert.equal(opened[0].pathname, `/${TOOLKIT_REPOSITORY}/issues/new`);
  const payload = JSON.parse(opened[0].searchParams.get("export"));
  assert.equal(payload.note_changes, undefined);
  assert.deepEqual(payload.keyboard, page.state.doc.keyboard);
});
