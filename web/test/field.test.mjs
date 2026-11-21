// The field's chips, break marks and caret mapping.
import assert from "node:assert/strict";
import { test } from "node:test";

import { Field, BREAK_MARK, offsetOf, positionOf, readUnits, renderUnits } from "../js/field.js";
import { parse } from "../js/text.js";
import { CODEC, fakeDocument } from "./fixture.mjs";

const doc = fakeDocument();
const ZW = "​";

function field(text, editable = true) {
  const el = doc.createElement("div");
  renderUnits(el, parse(text), CODEC, editable);
  return el;
}

test("tokens are non-editable chips by kind", () => {
  const el = field("Hi, {PLAYER}{COLOR 06}{LV}{NOPE}!");
  const chips = el.childNodes.filter((c) => c.nodeType === 1);
  assert.deepEqual(chips.map((c) => [c.className, c.contentEditable, c.textContent, c.dataset.u]), [
    ["chip chip-placeholder", "false", "PLAYER", "{PLAYER}"],
    ["chip chip-control", "false", "COLOR 06", "{COLOR 06}"],
    ["chip chip-symbol", "false", "LV", "{LV}"],
    ["chip chip-unknown", "false", "NOPE", "{NOPE}"],
  ]);
  assert.match(chips[0].title, /inserted by the game/);
  assert.equal(el.childNodes[0].data, "Hi, ");
});

test("breaks are marks, never escape text", () => {
  const el = field("a\\nb\\lc\\pd");
  const marks = el.childNodes.filter((c) => c.nodeType === 1);
  assert.deepEqual(marks.map((c) => [c.className, c.textContent]), [
    ["brk brk-n", BREAK_MARK["\\n"]], ["brk brk-l", BREAK_MARK["\\l"]], ["brk brk-p", BREAK_MARK["\\p"]],
  ]);
  assert.ok(!el.textContent.includes("\\"));
});

test("a caret holder follows a break with no text after it", () => {
  const el = field("a\\n\\p");
  assert.deepEqual(el.childNodes.map((c) => (c.nodeType === 3 ? c.data : c.dataset.u)), ["a", "\\n", ZW, "\\p", ZW]);
  const ro = field("a\\n\\p", false);
  assert.equal(ro.childNodes.length, 3);
});

test("DOM and units round trip", () => {
  for (const t of ["", "plain", "a\\nb", "{PLAYER}\\l\\l{COLOR 06}x\\p", "\\p", "Б{LV}б\\n "]) {
    assert.deepEqual(readUnits(field(t)), parse(t), t);
  }
});

test("caret offsets map both ways", () => {
  for (const t of ["ab\\ncd", "{PLAYER}\\n\\l{LV}", "\\pa", "x{COLOR 06}y\\p"]) {
    const el = field(t);
    const n = parse(t).length;
    for (let i = 0; i <= n; i++) {
      const [node, off] = positionOf(el, i);
      assert.equal(offsetOf(el, node, off), i, `${t} at ${i}`);
    }
  }
});

test("caret after a break sits on the next line", () => {
  const el = field("ab\\n");
  const [node, off] = positionOf(el, 3);
  assert.equal(node.data, ZW); // the holder after the mark
  assert.equal(off, 0);
  const [n2, o2] = positionOf(el, 2);
  assert.deepEqual([n2.data, o2], ["ab", 2]);
});

test("a position inside a chip counts the chip", () => {
  const el = field("a{PLAYER}b");
  const chip = el.childNodes[1];
  assert.equal(offsetOf(el, chip.firstChild, 3), 2);
  assert.equal(offsetOf(el, chip.firstChild, 0), 1);
  assert.equal(offsetOf(el, el, 2), 2);
});


test("read-only fields refuse edits and composition changes", () => {
  const el = field("original");
  el.setAttribute = (key, value) => { el[key] = value; };
  const f = Object.create(Field.prototype);
  Object.assign(f, { el, units: parse("original"), opts: { codec: CODEC }, history: { clear() {} } });
  f.setReadOnly(true);
  assert.equal(el.contentEditable, "false");
  assert.equal(el["aria-readonly"], "true");
  f.replace("changed");
  f.restore({ units: parse("changed"), caret: 0 });
  let prevented = false;
  f.beforeInput({ inputType: "insertCompositionText", preventDefault() { prevented = true; } });
  assert.ok(prevented);
  el.textContent = "changed";
  f.resync();
  assert.equal(f.text, "original");
  assert.equal(el.textContent, "original");
  f.setReadOnly(false);
  assert.equal(el.contentEditable, "true");
});
