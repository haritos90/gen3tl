// The editor entry flow and the workspace counts.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { shortFile, statusCounts } from "../js/data.js";
import { gameModel } from "../js/landing.js";
import { TEXT_NOTE } from "../js/gametext.js";
import { STAGE_NOTES, gameOptions, patchHint, patchPicker, startSummary, workspaceLanguage } from "../js/start.js";
import { fakeDocument } from "./fixture.mjs";
import { setUiLanguage } from "../js/ui.js";

const SHA1 = "b4776b82a4c7915d0fadeaa27e013523f99dfd94";
const game = (id, stage, languages) => ({ id, name: id, stage, engine: "firered-cfru", languages, data: "./" });
const MANIFEST = {
  languages: [
    { code: "ru", profile: "unbound", index: "a", entries: 100, progress: { approved: 87, untranslated: 13 } },
    { code: "de", profile: "unbound", index: "b", entries: 24, progress: { approved: 24 } },
    { code: "ru", profile: "draft", index: "c", entries: 14000, progress: { untranslated: 14000 } },
  ],
  profiles: [{ id: "unbound", sha1: SHA1 }, { id: "draft", sha1: "9".repeat(40) }],
};
const RELEASE = { profile: "unbound", language: "ru", version: "0.1.25", source_sha1: SHA1 };
const [unbound, draft, bare] = [game("unbound", "translation", ["de", "ru"]), game("draft", "catalog", ["ru"]),
  game("bare", "catalog", [])].map((g) => gameModel(g, MANIFEST, [RELEASE]));

test("game options: translation stage first, stage chips, no catalog muted", () => {
  const opts = gameOptions([bare, draft, unbound], "draft");
  assert.deepEqual(opts.map((o) => o.id), ["unbound", "bare", "draft"]);
  assert.deepEqual(opts[0].chip, ["ok", "Translation stage"]);
  assert.equal(opts[0].meta, "firered-cfru · 100 texts");
  assert.equal(opts[2].meta, "firered-cfru · 14,000 texts · drafting only");
  assert.deepEqual(opts.map((o) => [o.selected, o.muted]), [[false, false], [false, true], [true, false]]);
  assert.match(STAGE_NOTES.catalog, /widths are estimates/);
});

test("workspace languages are editable catalogs, independent of display language", (ctx) => {
  ctx.after(() => setUiLanguage("en"));
  for (const locale of ["en", "ru", "de"]) {
    setUiLanguage(locale);
    assert.equal(workspaceLanguage(unbound, "ru").name, "Russian");
    assert.equal(workspaceLanguage(unbound, "de").name, "German");
    assert.equal(workspaceLanguage(unbound, "fr"), null);
    assert.equal(workspaceLanguage(bare, "ru"), null);
    assert.equal(workspaceLanguage(null, "ru"), null);
  }
});

test("the summary names the verified ROM choice", () => {
  assert.equal(startSummary(null, null, null), "Choose a game.");
  assert.equal(startSummary(unbound, null, null), "unbound · choose a language");
  assert.equal(startSummary(unbound, workspaceLanguage(unbound, "ru"), null), "unbound · Russian · without a ROM");
  assert.equal(startSummary(unbound, workspaceLanguage(unbound, "ru"), { sha1: SHA1 }), "unbound · Russian · with your ROM");
  assert.equal(startSummary(unbound, workspaceLanguage(unbound, "ru"), { sha1: SHA1, release: RELEASE }),
    "unbound · Russian · with a patched ROM");
});

test("the game model carries clean base identities from its own profile", () => {
  const bases = [{ name: "Fixture base 1.0", sha1: "a".repeat(40) }];
  const manifest = { ...MANIFEST, profiles: [...MANIFEST.profiles,
    { id: "paired", sha1: SHA1, size: 32, patch_bases: bases }] };
  const model = gameModel(game("paired", "translation", ["ru"]), manifest);
  assert.equal(model.size, 32);
  assert.deepEqual(model.patch_bases, bases);
  assert.match(patchHint(model), /Fixture base 1.0.*paired UPS patch/);
  assert.deepEqual(draft.patch_bases, []);
  assert.equal(patchHint(draft), "");
});

test("the shared patch picker selects a local UPS, cancels and disables while checking", (ctx) => {
  const previous = globalThis.document;
  ctx.after(() => { globalThis.document = previous; });
  const doc = fakeDocument(), create = doc.createElement;
  doc.createElement = (tag) => {
    const node = create(tag);
    node.events = {};
    node.setAttribute = (key, value) => { node[key] = value; };
    node.addEventListener = (key, listener) => { node.events[key] = listener; };
    node.click = () => { node.clicked = true; };
    return node;
  };
  globalThis.document = doc;
  const chosen = [];
  let cancelled = 0;
  const picker = (busy) => patchPicker({ name: "Fixture base 1.0" }, { name: "Fixture", sha1: SHA1 },
    (file) => chosen.push(file), () => { cancelled++; }, busy);
  const node = picker(false);
  assert.match(node.textContent, /Fixture base 1.0 recognized.*UPS patch for Fixture/);
  assert.ok(node.textContent.includes(SHA1));
  assert.match(node.textContent, /read in this browser and stay local\./);
  const [input] = node.childNodes.filter((n) => n.tagName === "INPUT");
  const [choose, cancel] = node.childNodes.filter((n) => n.tagName === "BUTTON");
  assert.match(input.accept, /\.ups/);
  choose.events.click();
  assert.equal(input.clicked, true);
  const patch = { name: "fixture.ups" };
  input.files = [patch];
  input.events.change({ target: input });
  assert.equal(input.value, "");
  assert.deepEqual(chosen, [patch]);
  input.files = [];
  input.events.change({ target: input });
  assert.deepEqual(chosen, [patch]);
  cancel.events.click();
  assert.equal(cancelled, 1);
  assert.equal(picker(true).childNodes.find((n) => n.tagName === "BUTTON").disabled, true);
});

test("the start screen says the game's text needs the ROM, original or patched", () => {
  const html = readFileSync(new URL("../editor.html", import.meta.url), "utf8");
  const step = html.match(/<article class="step is-locked" id="start-rom">[^]*?<\/article>/)[0];
  assert.ok(step.includes(`data-ui="${TEXT_NOTE}"`));
  assert.equal(TEXT_NOTE, "The game's text appears when the ROM, original or patched, is loaded.");
  const cards = [...step.matchAll(/<p data-ui="([^"]+)">/g)].map((m) => m[1]);
  assert.match(cards[0], /^The English of every line appears beside its translation/); // with the ROM
  assert.match(cards[1], /^The editor shows the keys and the translations with their states; the English, the search by it/);
});

test("status bar counts and catalog paths", () => {
  const rows = [
    { id: "a", status: "approved", problems: [] },
    { id: "b", status: "fuzzy", problems: [{ severity: "error", message: "line too wide" }] },
    { id: "c", status: "untranslated", problems: [{ severity: "warning", message: "x" }] },
    { id: "d", status: "nothing", problems: [] },
    { id: "e", status: "untranslated", reach: "never shown", problems: [] },
    { id: "f", status: "approved", reach: "never shown", problems: [{ severity: "error", message: "z" }] },
  ];
  assert.deepEqual(statusCounts(rows),
    { approved: 1, fuzzy: 1, untranslated: 1, nothing: 1, "never shown": 2, problems: 2, edited: 0 });
  const edits = new Map([["b", { problems: [] }], ["c", { problems: [{ severity: "error", message: "y" }] }]]);
  assert.deepEqual(statusCounts(rows, edits),
    { approved: 1, fuzzy: 1, untranslated: 1, nothing: 1, "never shown": 2, problems: 2, edited: 2 });
  assert.equal(shortFile("/home/x/translation/po/maps/route-10.po"), "po/maps/route-10.po");
  assert.equal(shortFile("maps/route-10.po"), "maps/route-10.po");
});
