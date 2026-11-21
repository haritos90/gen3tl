// Break and token handling of the editing units.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  History, boxes, enterBreak, erase, flat, isBreak, isToken, join, normalize, parse, pasted, searchable, splice,
  tokenKind, typed,
} from "../js/text.js";
import { CODEC } from "./fixture.mjs";

const SAMPLES = [
  "",
  "Hello!",
  "Hi, {PLAYER}!\\nTake this.\\lAnd this.\\pNew box.",
  "{COLOR 06}Red{COLOR 08} text\\n\\nAfter a blank line",
  "\\p\\p",
  "{B_ATK_NAME_WITH_PREFIX} used {B_CURRENT_MOVE}!",
  "A lone { brace and a } close",
  "Unknown \\x escape and a trailing \\",
  "Ёлка {LV}5 — «кавычки»",
  "@VERSION@ {FD 3D} {F8 00}",
];

test("catalog text and units round trip", () => {
  for (const s of SAMPLES) assert.equal(join(parse(s)), s, s);
});

test("breaks and tokens are single units", () => {
  assert.deepEqual(parse("a\\nb\\l{PLAYER}\\p"), ["a", "\\n", "b", "\\l", "{PLAYER}", "\\p"]);
  assert.deepEqual(parse("\\x"), ["\\", "x"]);
  assert.ok(isBreak("\\n") && isBreak("\\l") && isBreak("\\p"));
  assert.ok(!isBreak("\\") && !isBreak("n"));
  assert.ok(isToken("{PLAYER}") && isToken("{}") && !isToken("{"));
});

test("typed text never makes a break or an escape", () => {
  assert.deepEqual(typed("a\\nb"), ["a", "n", "b"]);
  assert.deepEqual(typed("x\ny\tz​"), ["x", "y", "z"]);
  assert.deepEqual(typed("a b"), ["a", " ", "b"]);
  assert.deepEqual(typed("😀a"), ["a"]); // no byte in any pack
  assert.equal(join(typed("\\p")), "p");
});

test("pasted text keeps breaks and tokens", () => {
  assert.deepEqual(pasted("one\ntwo\r\nthree"), ["o", "n", "e", "\\n", "t", "w", "o", "\\n", "t", "h", "r", "e", "e"]);
  assert.equal(join(pasted("Hi, {PLAYER}!\\pBye")), "Hi, {PLAYER}!\\pBye");
  assert.equal(join(pasted("a\\xb")), "axb"); // no stray backslash
});

test("deleting removes a whole break or token", () => {
  const u = parse("ab\\n{PLAYER}c");
  assert.equal(join(erase(u, 3, 3).units), "ab{PLAYER}c"); // backspace after \n
  assert.equal(join(erase(u, 4, 4).units), "ab\\nc"); // backspace after the token
  assert.equal(join(erase(u, 2, 2, true).units), "ab{PLAYER}c"); // delete before \n
  assert.equal(join(erase(u, 0, 0).units), "ab\\n{PLAYER}c"); // nothing before
  assert.deepEqual(erase(u, 1, 4), { units: ["a", "c"], caret: 1 });
});

test("word deletion", () => {
  const u = parse("one two {PLAYER}");
  assert.equal(join(erase(u, u.length, u.length, false, true).units), "one two ");
  assert.equal(join(erase(u, 7, 7, false, true).units), "one  {PLAYER}");
  assert.equal(join(erase(u, 0, 0, true, true).units), " two {PLAYER}");
});

test("a typed token becomes one unit, the caret stays", () => {
  const u = [..."a{PLAYER", "}", "b"];
  assert.deepEqual(normalize(u, 9), { units: ["a", "{PLAYER}", "b"], caret: 2 });
  assert.deepEqual(normalize(u, 10), { units: ["a", "{PLAYER}", "b"], caret: 3 });
  assert.deepEqual(normalize(u, 3), { units: ["a", "{PLAYER}", "b"], caret: 2 }); // inside: after it
  assert.deepEqual(normalize(["a", "\\n", "b"], 2), { units: ["a", "\\n", "b"], caret: 2 });
  assert.deepEqual(normalize([..."{X"], 2), { units: ["{", "X"], caret: 2 }); // still open
});

test("splice puts the caret after the insertion", () => {
  assert.deepEqual(splice(["a", "b"], 1, 1, ["\\p"]), { units: ["a", "\\p", "b"], caret: 2 });
  assert.deepEqual(splice(["a", "b", "c"], 0, 2, ["{PLAYER}"]), { units: ["{PLAYER}", "c"], caret: 1 });
});

test("Enter follows the Gen 3 box convention", () => {
  const u = parse("one\\ntwo three\\pfour");
  assert.equal(enterBreak(u, 3, "dialogue"), "\\n"); // first break of box 1
  assert.equal(enterBreak(u, 7, "dialogue"), "\\l"); // after the \n: scroll
  assert.equal(enterBreak(u, u.length, "battle"), "\\n"); // box 2 has none yet
  assert.equal(enterBreak(u, 7, "multiline"), "\\n");
  assert.equal(enterBreak(u, 7, "single"), "\\n");
});

test("boxes, list and search forms", () => {
  assert.deepEqual(boxes("a\\nb\\lc\\pd"), [["a", "b", "c"], ["d"]]);
  assert.equal(flat("a\\nb\\pc"), "a ⏎ b ¶ c");
  assert.equal(searchable("Take\\nThis"), "take this");
});

test("token chips by kind", () => {
  const kind = (t) => tokenKind(t, CODEC);
  assert.equal(kind("{PLAYER}"), "placeholder");
  assert.equal(kind("{FD 3D}"), "placeholder");
  assert.equal(kind("{COLOR 06}"), "control");
  assert.equal(kind("{PAUSE_UNTIL_PRESS}"), "control");
  assert.equal(kind("{FC 1A}"), "control");
  assert.equal(kind("{LV}"), "symbol");
  assert.equal(kind("{F8 00}"), "symbol");
  assert.equal(kind("{1F}"), "symbol");
  assert.equal(kind("{COLOR}"), "unknown"); // needs an argument
  assert.equal(kind("{NOPE}"), "unknown");
  assert.equal(kind("{}"), "unknown");
});

test("undo history merges a typing run", () => {
  const h = new History();
  const s = (t) => ({ units: parse(t), caret: t.length });
  h.record(s(""), "type");
  h.record(s("a"), "type"); // same run
  h.record(s("ab"), null); // a break
  assert.equal(join(h.undo(s("ab\\n")).units), "ab");
  assert.equal(join(h.undo(s("ab")).units), "");
  assert.equal(h.undo(s("")), null);
  assert.equal(join(h.redo(s("")).units), "ab");
  h.record(s("ab"), "type");
  assert.equal(h.redo(s("abc")), null); // a new edit drops the redo
});
