import test from "node:test";
import assert from "node:assert/strict";
import { checkFailed, checkText } from "../js/checktext.js";
import { draftCheck, draftText, draftValue, isDraft, sourceTokens, translationState } from "../js/draft.js";
import { projectProfiles } from "../js/data.js";
import { Edits, exportDoc } from "../js/store.js";
import { setCatalog, setUiLanguage } from "../js/ui.js";
import { fakeStorage } from "./fixture.mjs";

test("catalog projects are editable without claiming build capability", () => {
  const index = { capabilities: { check: "tokens", build: false } };
  assert.equal(isDraft(index), true);
  assert.equal(isDraft({}), false);
  const profiles = projectProfiles({ profiles: [{ id: "test", support: "catalog" }],
    languages: [{ profile: "test", code: "ru", index: "test/ru/index.json" }] });
  assert.equal(profiles[0].languages.length, 1);
  const entry = { id: "source", source_text: "Hello {PLAYER}", translation: "", note: "" };
  const check = draftCheck(entry, "Draft {PLAYER}");
  assert.equal(check.errors, 0);
  assert.equal(check.exact, false);
  assert.equal(check.lines[0][0].width, null);
  assert.equal(check.warnings, 1);
  const edits = new Edits(null, "ru", "test");
  edits.set(entry, check.text, "", check.problems, check.exact);
  assert.deepEqual(exportDoc("ru", "test", edits.changes()).entries,
    [{ id: "source", translation: "Draft {PLAYER}", state: "text", note: "", base: "", base_state: "original" }]);
});

test("draft checking catches missing, repeated and malformed control tokens", () => {
  const entry = { id: "s1", source_text: "{PLAYER} {COLOR 01}" };
  for (const text of ["Draft", "{PLAYER} {PLAYER} {COLOR 01}", "{PLAYER} {COLOR 02}",
    "{PLAYER} {COLOR 01} {", "{PLAYER} {COLOR 01}\0"]) {
    assert.ok(draftCheck(entry, text).errors, text);
  }
  assert.equal(draftCheck(entry, "").errors, 0);
  assert.equal(draftText("A\\nB\\lC\\pD"), "A\nB\nC\n\nD");
});

test("source token metadata checks preserved counts without English", () => {
  const entry = { id: "key", tokens: ["{PLAYER}", "{COLOR 01}", "{PLAYER}"] };
  assert.equal(draftCheck(entry, "{PLAYER} {PLAYER} {COLOR 01}").errors, 0);
  for (const text of ["{PLAYER} {COLOR 01}", "{PLAYER} {PLAYER} {COLOR 02}", "{PLAYER} {PLAYER} {COLOR 01} {"]) {
    assert.ok(draftCheck(entry, text).errors, text);
  }
  assert.equal(draftCheck({ id: "empty", tokens: [] }, "Plain draft").errors, 0);
  assert.ok(draftCheck({ id: "empty", tokens: [] }, "{PLAYER}").errors);
  assert.equal(draftCheck(entry, "").errors, 0);
});

test("absent or malformed source evidence is a check error", () => {
  for (const entry of [{ id: "key" }, { id: "key", tokens: null }, { id: "key", tokens: [42] },
    { id: "key", tokens: ["{PLAYER}", null] }, { id: "key", tokens: {} },
    { id: "key", tokens: "{PLAYER}" }, { id: "key", tokens: ["{PLAYER}\n"] },
    { id: "key", tokens: ["{PLAYER"] }]) {
    assert.equal(sourceTokens(entry), null);
    const result = draftCheck(entry, "Draft");
    assert.equal(result.errors, 1);
    assert.match(result.problems[0].message, /Regenerate the editor data/);
    assert.ok(draftCheck(entry, "").errors);
  }
  assert.equal(draftCheck({ id: "key", source_text: "Source {PLAYER}", tokens: null }, "Draft {PLAYER}").errors, 0);
  assert.deepEqual(sourceTokens({ source_text: "Source {PLAYER}", tokens: [null] }), ["{PLAYER}"]);
  assert.deepEqual(sourceTokens({ tokens: [] }), []);
});

test("a draft checked in Russian stores and exports English; its problems show in the site language", (ctx) => {
  ctx.after(() => { setCatalog(null); setUiLanguage("en"); });
  const RU = {
    "Preserve the source placeholders and control codes.": "Сохраните подстановки и управляющие коды оригинала.",
    "Game encoding, font and layout are not verified for this catalog.": "Кодировка, шрифт и раскладка игры для этого каталога не проверены.",
    "check failed: {error}": "проверка не удалась: {error}",
  };
  setUiLanguage("ru");
  setCatalog(RU);
  const entry = { id: "s1", source_text: "Hello {PLAYER}", translation: "", note: "" };
  const check = draftCheck(entry, "Привет");
  const problems = [...check.problems, { message: checkFailed("no bundle"), severity: "error" }];
  const storage = fakeStorage();
  new Edits(storage, "ru", "p").set(entry, check.text, "", problems, check.exact);
  const stored = new Edits(storage, "ru", "p").get("s1").problems;
  assert.deepEqual(stored.map((p) => p.message), ["Preserve the source placeholders and control codes.",
    "Game encoding, font and layout are not verified for this catalog.", { code: "check_failed", error: "no bundle" }]);
  assert.doesNotMatch(storage.map.get("gen3tl-editor:edits:ru:p"), /Сохраните|проверка/);
  const exported = JSON.stringify(exportDoc("ru", "p", new Edits(storage, "ru", "p").changes()));
  assert.equal(exported, JSON.stringify({ version: "4.0.0", language: "ru", profile: "p",
    entries: [{ id: "s1", translation: "Привет", state: "text", note: "", base: "", base_state: "original" }],
    pictures: [], terms: [], glyphs: [] }));
  assert.deepEqual(stored.map((p) => checkText(p.message)), ["Сохраните подстановки и управляющие коды оригинала.",
    "Кодировка, шрифт и раскладка игры для этого каталога не проверены.", "проверка не удалась: no bundle"]);
  setUiLanguage("en");
  setCatalog(null);
  assert.deepEqual(stored.map((p) => checkText(p.message)), ["Preserve the source placeholders and control codes.",
    "Game encoding, font and layout are not verified for this catalog.", "check failed: no bundle"]);
});

test("catalog flags and explicit draft states distinguish blank values", () => {
  const original = { translation: "", flags: [] };
  const off = { translation: "", flags: ["empty", "fuzzy"] };
  assert.equal(translationState(original), "original");
  assert.equal(translationState(off), "empty");
  assert.equal(translationState({ translation: "x", flags: ["fuzzy"] }), "text");
  assert.deepEqual(draftValue(original, ""), { translation: "", state: "original" });
  assert.deepEqual(draftValue(off, ""), { translation: "", state: "empty" });
  assert.deepEqual(draftValue(off, "x"), { translation: "x", state: "text" });
  assert.deepEqual(draftValue(off, "", "original"), { translation: "", state: "original" });
  assert.equal(translationState({ ...off, state: "original" }), "original");
  for (const [text, state] of [["", "text"], ["x", "empty"], ["x", "original"], ["", "unknown"], [null, "original"]]) {
    assert.throws(() => draftValue(original, text, state), /invalid translation state/);
  }
});
