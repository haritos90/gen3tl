import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { OBJECT_KINDS, ObjectReferenceError, formatReference, parseLookup, parseReference } from "../js/objectref.js";
import { gameHash, readRegistry } from "../js/registry.js";

const fixtures = JSON.parse(readFileSync(new URL("./objectref-fixtures.json", import.meta.url), "utf8"));
const registry = readRegistry(fixtures.registry, "https://example.test/data/registry.json");
const workspace = fixtures.workspace;
const referenceOf = (kind, target) => `gen3tl:${workspace.game}/${workspace.language}/${kind}/${target}`;
const text = (id) => ({ ...workspace, kind: "text", id });
const errorIs = (code, params = {}) => (error) => {
  assert.ok(error instanceof ObjectReferenceError);
  assert.equal(error.code, code);
  assert.deepEqual(error.params, params);
  return true;
};

test("lookup accepts references, contextual keys and editor addresses locally", () => {
  for (const { reference, object } of fixtures.references) assert.deepEqual(parseLookup(` ${reference}\n`, registry), object);
  assert.deepEqual(parseLookup("table:moves/5", registry, workspace), text("table:moves/5"));
  const tail = gameHash(workspace.game, workspace.language, "map:1.110/trigger:0/90");
  for (const prefix of ["", "editor", "https://pasted-host.test/path/editor?unused=1",
    "editor.html", "https://pasted-host.test/path/editor.html?unused=1"]) {
    assert.deepEqual(parseLookup(prefix + tail, registry), text("map:1.110/trigger:0/90"));
  }
  assert.deepEqual(parseLookup(`#${workspace.language}/table:moves/5`, registry), text("table:moves/5"));
  for (const [view, kind] of [["terms", "term"], ["families", "family"], ["pictures", "picture"], ["fonts", "font"],
    ["enginerules", "engine-rule"]]) {
    const id = "Mixed/Case %2F ?#é";
    assert.deepEqual(parseLookup(gameHash(workspace.game, workspace.language, view, id), registry), { ...workspace, kind, id });
  }
  assert.deepEqual(parseLookup(gameHash(workspace.game, workspace.language, "keyboards", "0"), registry),
    { ...workspace, kind: "keyboard-page", id: "0", index: 0 });
});

test("lookup rejects missing objects, malformed addresses and unknown identities", () => {
  for (const value of ["", "#", "https://pasted-host.test/", "other.html#game/xx/key", "javascript:alert(1)",
    gameHash(workspace.game, workspace.language), gameHash(workspace.game, workspace.language, "terms"),
    gameHash(workspace.game, workspace.language, "terms", "id", "extra"),
    gameHash(workspace.game, workspace.language, "keyboards", "-1"),
    `#${workspace.game}//${workspace.language}/sym:key`]) {
    assert.throws(() => parseLookup(value, registry), errorIs("invalid-reference"), value);
  }
  assert.throws(() => parseLookup("#missing/xx/sym:key", registry), errorIs("unknown-game", { game: "missing" }));
  assert.throws(() => parseLookup(gameHash(workspace.game, "missing", "sym:key"), registry),
    errorIs("catalog-unavailable", { language: "missing" }));
  assert.throws(() => parseLookup("sym:key", registry), errorIs("workspace-required"));
});

test("keyboard addresses distinguish page names, chat bindings and positional links", () => {
  for (const [route, kind] of [["page", "keyboard-page"], ["chat", "chat-row"]]) {
    for (const id of ["0", "Mixed/Case %2F ?#é"]) {
      const tail = ["keyboards", route, id];
      const reference = { ...workspace, kind, id };
      assert.deepEqual(parseLookup(gameHash(workspace.game, workspace.language, ...tail), registry), reference);
      assert.deepEqual(parseLookup(`#${workspace.language}/${tail.map(encodeURIComponent).join("/")}`, registry), reference);
    }
    for (const tail of [["keyboards", route], ["keyboards", route, "id", "extra"]]) {
      assert.throws(() => parseLookup(gameHash(workspace.game, workspace.language, ...tail), registry), errorIs("invalid-reference"));
    }
  }
});

test("glyph addresses decode sheet identities once and preserve hexadecimal codes", () => {
  const sheet = "Sheet/one %2F ?#é";
  for (const code of ["BB", "105", "0BB"]) {
    const tail = ["fonts", sheet, "glyph", code];
    const reference = { ...workspace, kind: "glyph", sheet, code };
    for (const prefix of ["", "editor.html", "https://pasted-host.test/editor"]) {
      assert.deepEqual(parseLookup(prefix + gameHash(workspace.game, workspace.language, ...tail), registry), reference);
    }
    assert.deepEqual(parseLookup(`#${workspace.language}/${tail.map(encodeURIComponent).join("/")}`, registry), reference);
  }
  for (const suffix of [[], ["B"], ["bb"], ["0xBB"], ["1000"], ["BB", "extra"]]) {
    const hash = gameHash(workspace.game, workspace.language, "fonts", sheet, "glyph", ...suffix);
    assert.throws(() => parseLookup(hash, registry), errorIs("invalid-reference"));
  }
});

test("reference fixtures parse and format all eight kinds without changing identities", () => {
  assert.deepEqual(new Set(fixtures.references.map((row) => row.object.kind)), new Set(OBJECT_KINDS));
  for (const { reference, object } of fixtures.references) {
    assert.deepEqual(parseReference(reference, registry), object, reference);
    assert.equal(formatReference(object, registry), reference);
    assert.deepEqual(parseReference(formatReference(object, registry), registry), object);
  }
});

test("every component keeps delimiters and Unicode through exactly one decoding", () => {
  const id = "Mixed/Case:%2F ?#%+\t\r\n\u00e9 e\u0301 \ud83d\ude00 !'()*~._-";
  for (const kind of OBJECT_KINDS) {
    const object = kind === "glyph" ? { ...workspace, kind, sheet: id, code: "1AB" }
      : { ...workspace, kind, id };
    const reference = formatReference(object, registry);
    assert.deepEqual(parseReference(reference, registry), object);
    assert.ok(reference.includes("%252F%20%3F%23%25%2B%09%0D%0A%C3%A9%20e%CC%81%20%F0%9F%98%80"));
    assert.ok(reference.includes(kind === "text" ? "Mixed/Case:" : "Mixed%2FCase:"));
  }
});

test("escaped literal characters canonicalize without decoding nested escapes", () => {
  const inputs = [
    ["text", "table%3amoves%2f5", { id: "table:moves/5" }, "table:moves/5"],
    ["text", "site:%252F/%2525", { id: "site:%2F/%25" }, "site:%252F/%2525"],
    ["term", "name%2fpart%3aid", { id: "name/part:id" }, "name%2Fpart:id"],
    ["glyph", "sheet%2fpart/%42%42", { sheet: "sheet/part", code: "BB" }, "sheet%2Fpart/BB"],
  ];
  for (const [kind, target, identity, canonical] of inputs) {
    const parsed = parseReference(referenceOf(kind, target), registry);
    assert.deepEqual(parsed, { ...workspace, kind, ...identity });
    assert.equal(formatReference(parsed, registry), referenceOf(kind, canonical));
  }
});

test("bare catalog keys and legacy identities require an explicit workspace", () => {
  for (const key of fixtures.bareKeys) {
    const object = text(key);
    assert.deepEqual(parseReference(key, registry, workspace), object);
    assert.deepEqual(parseReference(formatReference(object, registry), registry), object);
    for (const missing of [null, {}, { game: workspace.game }, { language: workspace.language }]) {
      assert.throws(() => parseReference(key, registry, missing), errorIs("workspace-required"));
    }
  }
});

test("explicit reference identities override the selected workspace", () => {
  for (const { reference, object } of fixtures.references) {
    assert.deepEqual(parseReference(reference, registry, workspace), object);
    assert.deepEqual(parseReference(reference, registry, { game: "missing", language: "missing" }), object);
  }
});

test("unknown games never fall back to a default or a similar profile", () => {
  for (const game of fixtures.unknownGames) {
    const object = { ...text("battle:120"), game };
    const reference = `gen3tl:${encodeURIComponent(game)}/${workspace.language}/text/battle:120`;
    assert.throws(() => parseReference(reference, registry, workspace), errorIs("unknown-game", { game }));
    assert.throws(() => formatReference(object, registry), errorIs("unknown-game", { game }));
    assert.throws(() => parseReference(object.id, registry, object), errorIs("unknown-game", { game }));
  }
});

test("catalog languages are matched exactly within the referenced game", () => {
  for (const language of fixtures.unavailableLanguages) {
    const object = { ...text("battle:120"), language };
    const reference = `gen3tl:${workspace.game}/${encodeURIComponent(language)}/text/battle:120`;
    assert.throws(() => parseReference(reference, registry), errorIs("catalog-unavailable", { language }));
    assert.throws(() => formatReference(object, registry), errorIs("catalog-unavailable", { language }));
    assert.throws(() => parseReference(object.id, registry, object), errorIs("catalog-unavailable", { language }));
  }
  const [first, second] = registry.games;
  const language = first.languages.find((code) => !second.languages.includes(code));
  assert.throws(() => formatReference({ ...text("battle:120"), game: second.id, language }, registry),
    errorIs("catalog-unavailable", { language }));
});

test("invalid reference syntax and malformed escapes are rejected", () => {
  for (const value of [...fixtures.invalid, null, undefined, 1, [], {}]) {
    assert.throws(() => parseReference(value, registry, workspace), errorIs("invalid-reference"), String(value));
  }
});

test("formatting rejects missing identities and invalid glyph codes", () => {
  const glyph = { ...workspace, kind: "glyph", sheet: "font0", code: "BB" };
  const invalid = [null, undefined, 1, [], {}, { ...text("id"), kind: "unknown" }];
  for (const field of ["game", "language", "id"]) {
    for (const value of [undefined, null, "", 1, "\ud800"]) invalid.push({ ...text("id"), [field]: value });
  }
  for (const sheet of [undefined, null, "", 1, "\ud800"]) invalid.push({ ...glyph, sheet });
  for (const code of [undefined, null, "", 187, "bb", "B", "00BB", "0xBB", "GG", "BB\n"]) {
    invalid.push({ ...glyph, code });
  }
  for (const object of invalid) assert.throws(() => formatReference(object, registry), errorIs("invalid-reference"));
  assert.throws(() => parseReference("site:\ud800", registry, workspace), errorIs("invalid-reference"));
});

test("native identities are opaque and remain case-sensitive", () => {
  for (const id of ["G03", "g03", "001", "01", "é", "e\u0301", "/leading//trailing/"]) {
    const object = text(id);
    assert.deepEqual(parseReference(formatReference(object, registry), registry), object);
  }
  const glyph = { ...workspace, kind: "glyph", sheet: "Font0", code: "0BB" };
  assert.deepEqual(parseReference(formatReference(glyph, registry), registry), glyph);
});

test("parsing and formatting leave registry and caller objects intact", () => {
  const before = JSON.stringify({ registry, fixtures });
  for (const { object, reference } of fixtures.references) {
    formatReference(object, registry);
    parseReference(reference, registry, workspace);
  }
  assert.equal(JSON.stringify({ registry, fixtures }), before);
});
