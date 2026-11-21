import assert from "node:assert/strict";
import { test } from "node:test";
import { EngineRulesView } from "../js/enginerules.js";
import { placeOf } from "../js/data.js";
import { Edits, RuleEdits, exportDoc } from "../js/store.js";
import { submissionDoc, submissionRepository } from "../js/submit.js";
import { setCatalog, setLabelSource } from "../js/ui.js";
import { stubPage } from "./fixture.mjs";

function fixture(context) {
  const { $, storage } = stubPage(context, "editor");
  setLabelSource(null);
  setCatalog(null);
  context.after(() => setCatalog(null));
  const rows = [
    { id: "table:ending/0", translation: "", state: "empty", status: "approved" },
    { id: "table:copy/0", translation: "", state: "original", status: "untranslated" },
    { id: "sym:host", translation: "<b>Translated host</b>", state: "text", status: "fuzzy", reach: "never shown" },
    { id: "sym:name", translation: "Example name", state: "text", status: "approved" },
  ];
  const rule = { id: "ending/group", title: "Grouped ending", description: "One ending for two routines.",
    kind: "catalog", read_only: false, collapsed: false, limit_status: "verified", related: ["sym:host", "sym:missing"],
    examples: ["sym:name"], operands: [{ id: "suffix", bindings: [
      { entry: rows[0].id, routine: "first", offset: 32, byte_budget: 1, terminated: false },
      { entry: rows[1].id, routine: "second", offset: 64, byte_budget: 1, terminated: false },
    ] }], routines: [{ id: "first", buffer_bytes: 20 }, { id: "second", buffer_bytes: 32 }],
    references: [], branches: [], policies: [], evidence: "Verified synthetic operands.",
    sources: [...rows.map((row) => ({ entry: row.id, available: true, state: row.state, status: row.status })),
      { entry: "sym:missing", available: false, state: null }] };
  for (const row of rows) delete row.state;
  const fills = { ...rule, id: "fills", title: "Engine-filled text", kind: "engine_fills", read_only: true,
    description: "Values supplied by the engine.", collapsed: true, operands: [], related: [], examples: [],
    references: [{ section: "layout.code_fill", declarations: [{ id: "layout.code_fill/one", read_only: true,
      hosts: ["sym:host"], sources: ["sym:name"], unverified: true, facts: { slots: { VAR: { width: 60 } } } }] }] };
  const index = { engine_rules: { schema: "1.0.0", rules: [rule, fills] } };
  const edits = new Edits(storage, "aa", "fixture");
  const references = [], hashes = [], opened = [], views = [];
  const view = new EngineRulesView({ index: () => index, row: (id) => rows.find((row) => row.id === id),
    edits: () => edits, reference: (id) => references.push(id), setHash: (id) => hashes.push(id),
    openEntry: (id) => opened.push(id), openView: (name) => views.push(name) }, $);
  view.reset();
  return { $, view, index, rows, edits, references, hashes, opened, views };
}

test("declared grouped operands expose every binding and distinct catalog state without a ROM", (context) => {
  const { $, view, references, opened, index, rows } = fixture(context);
  const before = JSON.stringify({ index, rows });
  assert.equal(view.cur, "ending/group");
  assert.equal(references.at(-1), view.cur);
  const body = $("er-body");
  for (const value of ["Affected operands", "first", "second", "0x20", "0x40", "Byte budget: 1", "Immediate operand",
    "Affected texts", "Example texts", "Off", "Inherited original", "Custom", "Translated", "Never shown",
    "Original text unavailable without a ROM", "Example name", "<b>Translated host</b>"]) {
    assert.ok(body.textContent.includes(value), value);
  }
  assert.ok(body.textContent.includes("Check: unavailable"));
  assert.equal(body.querySelectorAll("button").find((button) => button.textContent === "Apply draft").disabled, true);
  assert.equal(body.querySelectorAll("b").length, 0);
  const buttons = body.querySelectorAll("button");
  for (const id of [rows[0].id, rows[1].id, "sym:host", "sym:name"]) {
    buttons.find((button) => button.textContent === id).listeners.click();
    assert.equal(opened.at(-1), id);
  }
  assert.equal(buttons.find((button) => button.textContent === "sym:missing").disabled, true);
  assert.equal(JSON.stringify({ index, rows }), before);
});

test("engine-filled text starts collapsed, opens by reference and retains unverified sources", (context) => {
  const { $, view, references } = fixture(context);
  assert.equal($("er-fills").open, false);
  assert.equal($("er-fills").hidden, false);
  view.show("fills");
  assert.equal($("er-fills").open, true);
  assert.equal(references.at(-1), "fills");
  const details = $("er-body").querySelectorAll("details")[0];
  assert.equal(Boolean(details.open), false);
  for (const value of ["Read-only", "Unverified limit", "sym:host", "Source texts", "sym:name", '"width": 60']) {
    assert.ok(details.textContent.includes(value), value);
  }
  view.reset();
  assert.equal($("er-fills").open, false);
  assert.equal(view.cur, "ending/group");
});

test("search includes rule identities and current catalog text and restores an empty state", (context) => {
  const { $, view } = fixture(context);
  $("er-search").value = "EXAMPLE NAME";
  $("er-search").listeners.input();
  assert.equal(view.filtered.length, 2);
  assert.equal($("er-fills").open, true);
  $("er-search").value = "ending/group";
  view.applyFilters();
  assert.equal(view.filtered.length, 1);
  assert.equal($("er-fills").hidden, true);
  $("er-search").value = "missing result";
  view.applyFilters();
  assert.equal(view.cur, null);
  assert.equal($("er-empty").textContent, "No matching rules");
  assert.equal($("er-detail").hidden, true);
  assert.equal($("er-body").textContent, "");
  $("er-search").value = "";
  view.applyFilters();
  assert.equal(view.cur, "ending/group");
});

test("ROM text and live drafts are read from the selected catalog without copying values", (context) => {
  const { $, view, rows, edits } = fixture(context);
  rows[1].source_text = "Original ending";
  view.relabel();
  assert.ok($("er-body").textContent.includes("Current: Original ending"));
  edits.set(rows[1], "Draft ending", "");
  view.show();
  assert.ok($("er-body").textContent.includes("Draft: Draft ending"));
  edits.remove(rows[1].id);
  delete rows[1].source_text;
  view.show();
  assert.ok(!$("er-body").textContent.includes("Original ending"));
  assert.ok(!$("er-body").textContent.includes("Draft ending"));
});

test("empty and older indexes clear selection and do not expose stale rule references", (context) => {
  const { $, view, index, references, hashes } = fixture(context);
  for (const model of [{ schema: "1.0.0", rules: [] }, undefined]) {
    index.engine_rules = model;
    view.reset();
    assert.equal(view.cur, null);
    assert.equal(references.at(-1), null);
    assert.equal(hashes.at(-1), null);
    assert.equal($("er-empty").textContent, "No rules declared for this game");
    assert.equal($("er-fills").hidden, true);
    assert.equal($("er-list").children.length, 0);
  }
});

test("related views come from declared capabilities and display relabeling retains scope", (context) => {
  const { $, view, index, views } = fixture(context);
  index.engine_rules.rules[0].references = [{ section: "sprite_font", declarations: [] },
    { section: "keyboard", declarations: [] }];
  index.keyboard = {};
  view.show();
  for (const label of ["Fonts", "Keyboards"]) {
    $("er-body").querySelectorAll("button").find((button) => button.textContent === label).listeners.click();
  }
  assert.deepEqual(views, ["fonts", "keyboards"]);
  $("er-search").value = "ending";
  setCatalog({ "Grouped ending": "Display title", "Affected texts": "Display sources" });
  view.relabel();
  assert.equal($("er-title").textContent, "Display title");
  assert.equal($("er-search").value, "ending");
  assert.equal(view.cur, "ending/group");
  assert.ok($("er-body").textContent.includes("Display sources"));
});

test("rule routes preserve slash-containing identifiers and an empty view", () => {
  assert.deepEqual(placeOf(["enginerules", "ending/group"]), { view: "enginerules", id: "ending/group" });
  assert.deepEqual(placeOf(["enginerules"]), { view: "enginerules", id: null });
});

const settle = () => new Promise((resolve) => setImmediate(resolve));

function editable(context) {
  const setup = fixture(context), { view, index, rows } = setup;
  const rule = index.engine_rules.rules[0];
  rule.operands[0].choices = ["original", "off", "custom"];
  rows[0].flags = ["empty"];
  rows[0].note = "Keep this note";
  rows[0].source_text = rows[1].source_text = "s";
  const calls = [];
  const result = (request) => {
    const selection = request.operands.suffix;
    const changes = selection ? rows.slice(0, 2).map((row) => ({ id: row.id,
      translation: selection.choice === "custom" ? selection.text : "", state: selection.choice === "off" ? "empty"
        : selection.choice === "custom" ? "text" : "original" })) : [];
    const value = { fields: [{ entry: rows[0].id, text: "s", used: 1, limit: 1, problems: [] }],
      results: [{ routine: "first", input: request.name ?? "Name", text: "Names", used: 6, limit: 20, problems: [] }],
      unavailable: [], problems: [] };
    return { original: value, current: value, proposed: value, changes, valid: selection?.text !== "invalid", exact: true };
  };
  view.host.loadEntry = async (id) => rows.find((row) => row.id === id);
  view.host.check = () => ({ ready: true, rulePreview: async (...args) => {
    calls.push(args);
    return result(args[3]);
  } });
  view.show();
  return { ...setup, calls, result };
}

const button = ($, text) => $("er-body").querySelectorAll("button").find((node) => node.textContent === text);

test("validated presets apply every binding together and retain existing notes", async (context) => {
  const { $, view, edits, rows, calls } = editable(context);
  await settle();
  for (const label of ["Original", "Current", "Proposed", "Quantity", "Example name", "Encoded bytes: 1/1", "Name → Names"]) {
    assert.ok($("er-body").textContent.includes(label), label);
  }
  button($, "Off").listeners.click();
  assert.equal(button($, "Apply draft").disabled, true);
  await settle();
  assert.equal(edits.size, 0);
  assert.equal(button($, "Apply draft").disabled, false);
  button($, "Apply draft").listeners.click();
  assert.equal(edits.get(rows[0].id), undefined);
  assert.equal(edits.get(rows[1].id).state, "empty");
  edits.set(rows[0], "x", "Draft note", null, false, "text");
  edits.set(rows[1], "x", "Historical context", null, false, "text");
  button($, "Off").listeners.click();
  await settle();
  button($, "Apply draft").listeners.click();
  assert.equal(edits.get(rows[0].id), undefined);
  assert.equal(edits.get(rows[1].id).note, "Historical context");
  await settle();
  assert.ok(calls.at(-1)[2].some((row) => row.id === rows[1].id && row.state === "empty"));
  button($, "Discard draft").listeners.click();
  assert.equal(edits.size, 0);
  view.show("fills");
  assert.equal($("er-body").querySelectorAll("input").length, 0);
});

test("grouped edits ignore omitted note snapshots and keep state changes", async (context) => {
  const { $, view, edits, rows } = editable(context);
  edits.set(rows[0], "x", rows[0].note, null, false, "text");
  edits.set(rows[1], "x", "Historical context", null, false, "text");
  for (const row of rows) delete row.note;
  view.show();
  await settle();
  button($, "Off").listeners.click();
  await settle();
  button($, "Apply draft").listeners.click();
  const restored = new Edits(edits.storage, "aa", "fixture");
  assert.deepEqual(restored.changes(), [{ id: rows[1].id, translation: "", state: "empty",
    note: "Historical context", base: "", base_state: "original" }]);
});

test("grouped view drafts and policy proposals survive one submission without copying unchanged bindings", async (context) => {
  const { $, view, edits, rows, index } = editable(context);
  const rule = index.engine_rules.rules[0];
  rule.capability_sha256 = "a".repeat(64);
  edits.index = { ...index, list: rows };
  await settle();
  button($, "Off").listeners.click();
  await settle();
  button($, "Apply draft").listeners.click();
  const restored = new Edits(edits.storage, "aa", "fixture", edits.index);
  assert.equal(restored.size, 1);
  assert.deepEqual(restored.get(rows[1].id).rule_baselines[rule.id].entries, {
    [rows[0].id]: { translation: "", state: "empty" },
    [rows[1].id]: { translation: "", state: "original" },
  });
  const policies = new RuleEdits(edits.storage, "aa", "fixture");
  policies.set({ id: "marker", capability_sha256: "b".repeat(64),
    base: { enabled: false }, value: { enabled: true } });
  const doc = exportDoc("aa", "fixture", restored.changes(), [], [], [], null, policies.changes());
  const submitted = submissionDoc(JSON.parse(JSON.stringify(doc)));
  assert.deepEqual(submitted.entries, [{ id: rows[1].id, translation: "", state: "empty",
    base: "", base_state: "original" }]);
  assert.deepEqual(submitted.engine_rules, policies.changes());
  assert.equal(submissionRepository(doc, "example/project"), "example/project");
  assert.equal(rows[1].translation, "");
  view.host.edits = () => restored;
  view.show();
  await settle();
  button($, "Discard draft").listeners.click();
  assert.equal(restored.size, 0);
  assert.equal(policies.size, 1);
});

test("invalid custom text stays out of drafts and newer previews win", async (context) => {
  const { $, view, edits, result } = editable(context);
  await settle();
  const select = $("er-body").querySelectorAll("select")[0];
  select.listeners.change({ target: { value: "custom" } });
  const input = $("er-body").querySelectorAll("input")[0];
  input.listeners.input({ target: { value: "invalid" } });
  await settle();
  assert.equal(button($, "Apply draft").disabled, true);
  assert.equal(edits.size, 0);
  const pending = [];
  view.host.check = () => ({ ready: true, rulePreview: (...args) => new Promise((resolve) => pending.push({ resolve, args })) });
  input.listeners.input({ target: { value: "old" } });
  await settle();
  input.listeners.input({ target: { value: "new" } });
  await settle();
  pending[1].resolve(result(pending[1].args[3]));
  await settle();
  pending[0].resolve({ ...result(pending[0].args[3]), valid: false });
  await settle();
  assert.equal(button($, "Apply draft").disabled, false);
  button($, "Apply draft").listeners.click();
  assert.equal(edits.get("table:ending/0").translation, "new");
});

test("quantity and example changes recheck without catalog mutations", async (context) => {
  const { $, edits, calls } = editable(context);
  await settle();
  const inputs = $("er-body").querySelectorAll("input");
  inputs[1].listeners.input({ target: { value: "3" } });
  inputs[2].listeners.input({ target: { value: "Test" } });
  await settle();
  assert.equal(calls.at(-1)[3].quantity, 3);
  assert.equal(calls.at(-1)[3].name, "Test");
  assert.equal(edits.size, 0);
});

function policySetup(context, units = false) {
  const setup = fixture(context), { view, index } = setup;
  const rule = index.engine_rules.rules[0];
  rule.kind = "policy";
  rule.operands = [];
  rule.capability_sha256 = "a".repeat(64);
  rule.choices = units ? ["original", "enabled"] : ["original", "off"];
  rule.policies = [{ kind: units ? "code_replacement" : "text_prefix", name: "capability",
    current: units ? { enabled: true, fields: { height_unit: "m" } } : { enabled: false },
    fields: units ? ["height_unit", "weight_unit"] : [],
    field_limits: { height_unit: { bytes: 4, pixels: 18 }, weight_unit: { bytes: 4, pixels: 18 } },
    capability: { required: ["height_unit"], fallbacks: { weight_unit: { pointer: 32 } } } }];
  const storage = new Map();
  const memory = { getItem: (key) => storage.get(key), setItem: (key, value) => storage.set(key, value),
    removeItem: (key) => storage.delete(key) };
  const rules = new RuleEdits(memory, "aa", "fixture"), calls = [];
  view.host.ruleEdits = () => rules;
  view.host.check = () => ({ ready: true, rulePreview: async (id, entries, drafts, request) => {
    calls.push(request);
    const current = rule.policies[0].current;
    const record = { id, capability_sha256: rule.capability_sha256, base: current, value: request.value };
    const valid = request.value.fields?.height_unit !== "invalid" &&
      (!request.draft || request.draft.capability_sha256 === rule.capability_sha256);
    return { original: units ? { enabled: false, fields: {} } : { enabled: false }, current,
      proposed: request.value, fields: [], problems: valid ? [] : ["Invalid proposal"], valid, exact: true,
      changes: valid && JSON.stringify(current) !== JSON.stringify(request.value) ? [record] : [] };
  } });
  view.reset();
  return { ...setup, rule, rules, calls, memory };
}

test("marker presets persist typed proposals and restore without catalog changes", async (context) => {
  const { $, view, rule, rules, edits, memory } = policySetup(context);
  await settle();
  assert.ok($("er-body").textContent.includes("Show number marker"));
  $("er-body").querySelectorAll("select")[0].listeners.change({ target: { value: "enabled" } });
  await settle();
  button($, "Apply draft").listeners.click();
  const stored = rules.get(rule.id);
  assert.deepEqual(stored.base, { enabled: false });
  assert.deepEqual(stored.value, { enabled: true });
  assert.equal(stored.capability_sha256, rule.capability_sha256);
  assert.equal(edits.size, 0);
  assert.deepEqual(new RuleEdits(memory, "aa", "fixture").changes(), [stored]);
  assert.equal(new RuleEdits(memory, "bb", "fixture").size, 0);
  assert.equal(new RuleEdits(memory, "aa", "other").size, 0);
  await settle();
  assert.ok($("er-body").textContent.includes("Draft"));
  $("er-body").querySelectorAll("select")[0].listeners.change({ target: { value: "original" } });
  await settle();
  button($, "Apply draft").listeners.click();
  assert.equal(rules.size, 0);
  view.reset();
  assert.equal($("er-body").querySelectorAll("select")[0].value, "original");
});

test("unit fields honor verified limits and keep fallback values absent", async (context) => {
  const { $, view, rules, rule, calls } = policySetup(context, true);
  await settle();
  const inputs = $("er-body").querySelectorAll("input");
  assert.equal(inputs[0].disabled, false);
  assert.equal(inputs[1].getAttribute("placeholder"), "Use catalog text");
  for (const label of ["Use original units", "Use metric units", "Height unit", "Weight unit"]) {
    assert.ok($("er-body").textContent.includes(label));
  }
  inputs[0].listeners.input({ target: { value: "cm" } });
  await settle();
  assert.deepEqual(calls.at(-1).value.fields, { height_unit: "cm" });
  button($, "Apply draft").listeners.click();
  assert.equal(rules.get(rule.id).value.fields.height_unit, "cm");
  await settle();
  rule.limit_status = "unverified";
  view.show();
  await settle();
  assert.ok($("er-body").querySelectorAll("input").every((node) => node.disabled));
  button($, "Discard draft").listeners.click();
  assert.equal(rules.size, 0);
});

test("invalid and stale policy proposals stay unapplied", async (context) => {
  const { $, view, rules, rule } = policySetup(context, true);
  await settle();
  $("er-body").querySelectorAll("input")[0].listeners.input({ target: { value: "invalid" } });
  await settle();
  assert.equal(button($, "Apply draft").disabled, true);
  assert.equal(rules.size, 0);
  rules.set({ id: rule.id, capability_sha256: "old", base: rule.policies[0].current,
    value: { enabled: false, fields: {} } });
  view.reset();
  await settle();
  assert.equal(button($, "Apply draft").disabled, true);
  assert.ok($("er-body").textContent.includes("Invalid proposal"));
  button($, "Discard draft").listeners.click();
  assert.equal(rules.size, 0);
});
