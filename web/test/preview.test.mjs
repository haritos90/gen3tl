// Geometry of the box preview.
import assert from "node:assert/strict";
import { test } from "node:test";

import { Codec, Measure, entryMeasure } from "../js/font.js";
import { draftFonts, styleGlyphs } from "../js/fonts.js";
import { drawPreview, frameOf, layoutBoxes } from "../js/preview.js";
import { CODEC, PACK, STYLE } from "./fixture.mjs";

const m = new Measure(() => 6, new Codec(CODEC), { PLAYER: 42 }, 60);

function paintedPreview(text, window) {
  const tints = [];
  const canvas = { style: {}, getContext: () => ({
    clearRect() {},
    fillRect(x, y, width, height) {
      if (this.fillStyle === "rgba(232, 32, 32, 0.22)") tints.push({ x, y, width, height });
    },
  }) };
  const geo = drawPreview(canvas, text, { window, measure: m, glyphs: { get: () => ({ exact: true }) }, zoom: 1 });
  return { geo, tints };
}

test("frames by window and box kind", () => {
  assert.equal(frameOf("dialogue", "default"), "message");
  assert.equal(frameOf("dialogue", "sign"), "sign");
  assert.equal(frameOf("battle", null), "battle");
  assert.equal(frameOf("multiline", null), "panel");
  assert.equal(frameOf("single", null), "strip");
  assert.equal(frameOf("field", null), "strip");
});

test("a message box per \\p, two rows at least", () => {
  const geo = layoutBoxes("ab\\nba\\lab\\pa", { kind: "dialogue", width: 30, lines: 2 }, m);
  assert.equal(geo.boxes.length, 2);
  assert.deepEqual(geo.boxes.map((b) => [b.rows, b.room, b.lines.length]), [[3, 2, 3], [2, 2, 1]]);
  assert.deepEqual(geo.boxes[0].lines.map((l) => l.width), [12, 12, 12]);
  assert.equal(geo.width, 30 + 16);
  assert.equal(geo.boxes[1].y, 3 * 16 + 8 + 6);
});

test("overflow past the width and the line count", () => {
  const geo = layoutBoxes("aaaaaa\\nb\\nb", { kind: "multiline", width: 30, lines: 2 }, m);
  assert.equal(geo.boxes.length, 1);
  assert.deepEqual(geo.boxes[0].lines.map((l) => l.over), [true, false, false]);
  assert.deepEqual([geo.boxes[0].rows, geo.boxes[0].room], [3, 2]);
  assert.equal(geo.width, 36 + 16); // the widest line
});

test("single lines, keys and slots", () => {
  const one = layoutBoxes("a{PLAYER}", { kind: "single", width: 76, lines: 1 }, m);
  assert.deepEqual([one.frame, one.boxes[0].rows, one.boxes[0].lines[0].width], ["strip", 1, 48]);
  const keys = layoutBoxes("aaaaaaaaaaaaaaa", { kind: "single", width: 20, lines: 1, keys: 15 }, m);
  assert.equal(keys.boxes[0].lines[0].over, false); // keys, not pixels
  const none = layoutBoxes("ab", null, m);
  assert.equal(none.frame, "message");
});

test("a line 1 px over that clips only shadow fits", () => {
  const w = { kind: "single", width: 35, lines: 1 };
  assert.equal(layoutBoxes("aaaaaa", w, m).boxes[0].lines[0].over, true); // 36 px, no check result
  const fits = layoutBoxes("aaaaaa", w, m, null, (t) => t === "aaaaaa");
  assert.equal(fits.boxes[0].lines[0].over, false);
  const two = layoutBoxes("aaaaaa", { ...w, width: 34 }, m, null, () => true);
  assert.equal(two.boxes[0].lines[0].over, true); // 2 px: no shadow fit
});

// The clipped part and the fix note
import { clippedOf, crowdedOf, droppedNote, fixNote } from "../js/preview.js";

test("clipped lines name their overflow in pixels", () => {
  const geo = layoutBoxes("aaaaaa\\nb\\pbbbbbbbb", { kind: "dialogue", width: 30, lines: 2 }, m);
  assert.deepEqual(clippedOf(geo), [
    { box: 1, line: 1, text: "aaaaaa", width: 36, clipped: 6 },
    { box: 2, line: 1, text: "bbbbbbbb", width: 48, clipped: 18 }]);
  assert.equal(fixNote(geo), "Line 1 of box 2 is 18 px wider than the 30 px window; the game cuts the marked part. " +
    "Shorten the line or move a line break. 1 more line runs over too.");
  const fits = layoutBoxes("aaaaa", { kind: "single", width: 30, lines: 1 }, m);
  assert.deepEqual(clippedOf(fits), []);
  assert.equal(fixNote(fits), "");
  assert.deepEqual(clippedOf(layoutBoxes("aaaaaaaa", null, m)), []); // no window, no limit
  const shadow = layoutBoxes("aaaaaa", { kind: "single", width: 35, lines: 1 }, m, null, () => true);
  assert.deepEqual(clippedOf(shadow), []); // a shadow fit is no clip
});

test("lines past the box's room are crowded", () => {
  const geo = layoutBoxes("a\\nb\\nb", { kind: "multiline", width: 30, lines: 2 }, m);
  assert.deepEqual(crowdedOf(geo), [{ box: 1, rows: 3, room: 2 }]);
  assert.equal(fixNote(geo), "The text has 3 lines; the window shows 2. Move text to a new box or shorten it.");
});

test("a message box with a scroll break is not crowded", () => {
  const w = { kind: "dialogue", width: 30, lines: 2 };
  const text = "a\\nb\\lb\\pa\\nb\\nb";
  const geo = layoutBoxes(text, w, m);
  assert.deepEqual(crowdedOf(geo, text, "dialogue"), [{ box: 2, rows: 3, room: 2 }]);
  assert.deepEqual(crowdedOf(geo).map((c) => c.box), [1, 2]); // without the text, every box counts
});

for (const kind of ["dialogue", "battle"]) {
  test(`${kind} previews leave scrolled rows untinted`, () => {
    for (const arrow of [0, 10]) {
      const { geo, tints } = paintedPreview("a\\nb\\lc\\ld", { kind, width: 60, lines: 2, arrow });
      assert.deepEqual(geo.boxes[0].lines.map((l) => l.text), ["a", "b", "c", "d"]);
      assert.deepEqual(geo.crowded, []);
      assert.deepEqual(tints, []);
      assert.equal(fixNote(geo), "");
    }
  });

  test(`${kind} previews tint only the box without scrolling`, () => {
    const { geo, tints } = paintedPreview("a\\nb\\lc\\pa\\nb\\nc\\pa\\nb\\lc",
      { kind, width: 60, lines: 2, arrow: 10 });
    assert.deepEqual(geo.crowded, [{ box: 2, rows: 3, room: 2 }]);
    assert.deepEqual(tints, [{ x: 1, y: geo.boxes[1].y + 36, width: geo.width - 2, height: 16 }]);
    assert.equal(fixNote(geo), "Box 2 has 3 lines; the window shows 2. Move text to a new box or shorten it.");
  });
}

test("scrolling leaves width overflow tinted", () => {
  const { geo, tints } = paintedPreview("a\\nb\\l" + "c".repeat(11), { kind: "dialogue", width: 60, lines: 2 });
  assert.deepEqual(geo.crowded, []);
  assert.deepEqual(geo.clipped, [{ box: 1, line: 3, text: "c".repeat(11), width: 66, clipped: 6 }]);
  assert.ok(tints.some(({ x, y, width, height }) => x === 68 && y === 36 && width === 6 && height === 16));
  assert.ok(tints.every(({ x }) => x !== 1));
});

test("fixed multiline previews keep overflow tinted despite scroll codes", () => {
  const { geo, tints } = paintedPreview("a\\nb\\lc", { kind: "multiline", width: 60, lines: 2 });
  assert.deepEqual(geo.crowded, [{ box: 1, rows: 3, room: 2 }]);
  assert.deepEqual(tints, [{ x: 1, y: 36, width: geo.width - 2, height: 16 }]);
});


test("a box wrapped anew names its too-wide written line", () => {
  const message = "written line wider than the window: box 1: 122 > 118 px: 'ягоду, если она есть.'; " +
    "the build drops the written breaks of the box and re-wraps it";
  assert.equal(droppedNote([{ severity: "warning", message }]),
    "Your line breaks in box 1 are not used: the line “ягоду, если она есть.” is 122 px, wider than the " +
    "118 px window, so the game wraps the box anew. Shorten that line or move its break to keep your breaks.");
  assert.equal(droppedNote([{ severity: "error", message: "line too wide: 48 > 36 px: 'Абвгдежз'" }]), "");
  assert.equal(droppedNote(undefined), "");
});

test("a redrawn glyph draws and measures at its new width before any import", () => {
  const rows = Array.from({ length: 14 }, (_, y) => (y > 2 && y < 11 ? "_##s__" : "______"));
  const index = { fonts: { main: "main", styles: { main: STYLE } }, glyphs: { main: { top: 0, rows: 14, art: { 37: rows } } },
    placeholders: { widths: {}, default: 60 } };
  const wide = rows.map((r, y) => `${r}${y > 2 && y < 11 ? "#s" : "__"}`); // Б 8 px, ink in column 6
  const window = { kind: "multiline", width: 30, lines: 1 };
  const codec = new Codec(CODEC, PACK);
  const draw = (idx) => {
    const glyphs = styleGlyphs(idx);
    const calls = [];
    const canvas = { style: {}, getContext: () => ({ clearRect() {}, fillRect(x, y, w, h) { calls.push([this.fillStyle, x, y, w, h]); } }) };
    const geo = drawPreview(canvas, "БББББ", { window, measure: entryMeasure(idx, glyphs, codec, window), glyphs: glyphs.main,
      styles: glyphs, zoom: 1 });
    return { geo, ink: calls.some(([c, x, y, w, h]) => c === "#505058" && x === 8 + 6 && y === 4 + 3 && w === 1 && h === 1) };
  };
  const before = draw(index);
  const after = draw(draftFonts(index, [{ style: "main", code: "37", width: 8, rows: wide }]));
  assert.deepEqual([before.geo.boxes[0].lines[0].width, before.geo.clipped, before.ink], [30, [], false]);
  assert.deepEqual([after.geo.boxes[0].lines[0].width, after.geo.clipped.map((c) => c.clipped), after.ink], [40, [10], true]);
});

// The prompt arrow after a waiting line
import { arrowCutOf } from "../js/preview.js";

test("a waiting line leaves room for the arrow; the overlap is named", () => {
  const w = { kind: "dialogue", width: 60, lines: 2, arrow: 10 };
  const geo = layoutBoxes("aaaaaaaa\\nbbbbbbbbb\\pc", w, m); // 48 px and 54 px over a 50 px room
  assert.deepEqual(geo.boxes[0].lines.map((l) => [l.waits, l.crowded]), [[false, false], [true, true]]);
  assert.deepEqual(geo.boxes[1].lines.map((l) => l.waits), [false]);
  assert.equal(geo.arrow, 10);
  assert.equal(geo.width, 54 + 10 + 16); // the arrow is drawn past the line
  assert.deepEqual(arrowCutOf(geo), [{ box: 1, line: 2, text: "bbbbbbbbb", width: 54, room: 50, cut: 4 }]);
  assert.equal(fixNote({ ...geo, clipped: [], crowded: [] }), "Line 2 of box 1 is 54 px; a line before a scroll or a new box " +
    "may take 50 px, because the game draws the 10 px arrow that waits for a button right after it, and the window cuts 4 px " +
    "of the arrow. Move a word to the next line.");
  assert.deepEqual(arrowCutOf(layoutBoxes("aaaaaaaa\\nbbbbbbbbb\\pc", { ...w, arrow: undefined }, m)), []);
  assert.deepEqual(arrowCutOf(layoutBoxes("bbbbbbbbb", w, m)), []); // the last line waits for nothing
});


test("multiline previews use the binding consumer at scroll and page only", () => {
  const consumers = [{ name: "wide message", width: 100, arrow: 30 },
    { name: "narrow menu", width: 60, arrow: 0 }];
  const w = { kind: "multiline", width: 60, lines: 6, arrow: 30, consumers };
  const geo = layoutBoxes("a\\n" + "b".repeat(12) + "\\lc\\pd{PAUSE_UNTIL_PRESS}", w, m);
  assert.equal(geo.boxes.length, 2);
  assert.deepEqual(geo.boxes.map((b) => b.lines.map((l) => l.waits)), [[false, true, true], [false]]);
  assert.equal(geo.arrowLimit, 100);
  assert.equal(geo.arrow, 30);
  assert.equal(geo.consumer, "wide message");
  assert.deepEqual(arrowCutOf(geo), [{ box: 1, line: 2, text: "b".repeat(12), width: 72, room: 70, cut: 2 }]);
  const fitting = layoutBoxes("a\\n" + "b".repeat(10) + "\\pc", w, m);
  assert.deepEqual(arrowCutOf(fitting), []);
  for (const kind of ["dialogue", "battle", "multiline", "single", "field"]) {
    const declared = { ...w, kind, consumers: [{ name: "bitmap", width: 60, arrow: 0 }] };
    assert.equal(layoutBoxes("a\\lb\\pc", declared, m).arrow, 0);
    const fallback = { kind, width: 60, lines: 6, arrow: 10 };
    assert.deepEqual(layoutBoxes("a\\lb\\pc", fallback, m).boxes.map((b) => b.lines.map((l) => l.waits)),
      [[true, true], [false]]);
  }
});


test("runtime newlines show successive two-line scroll frames", () => {
  const window = { kind: "dialogue", width: 60, lines: 2, arrow: 12, newline_scroll_after: 1 };
  const text = "A\\nBBBBBBBBB\\nC\\nDDDDDDDDD";
  const { geo } = paintedPreview(text, window);
  assert.deepEqual(geo.boxes[0].lines.map((l) => l.waits), [false, true, true, false]);
  assert.deepEqual(geo.frames.map((b) => b.lines.map((l) => l.text)),
    [["A", "BBBBBBBBB"], ["BBBBBBBBB", "C"], ["C", "DDDDDDDDD"]]);
  assert.deepEqual(geo.frames.map((b) => b.rows), [2, 2, 2]);
  assert.deepEqual(geo.frames.map((b) => b.lines.map((l) => l.waits)), [[false, true], [false, true], [false, false]]);
  assert.deepEqual(geo.crowded, []);
  assert.deepEqual(geo.arrowCut.map((c) => [c.line, c.width, c.room]), [[2, 54, 48]]);
});

test("runtime arrows use only consumers that scroll that newline", () => {
  const window = { kind: "dialogue", width: 60, lines: 2, newline_scroll_after: 1, consumers: [
    { name: "ordinary", width: 60, arrow: 12 },
    { name: "scrolling", width: 70, arrow: 12, newline_scroll_after: 1 }] };
  const geo = paintedPreview("A\\nBBBBBBBBB\\nC\\pD\\nE", window).geo;
  assert.deepEqual(geo.boxes.map((b) => b.lines.map((l) => l.waits)), [[false, true, true], [true, false]]);
  assert.equal(geo.boxes[0].lines[1].arrowLimit, 70);
  assert.equal(geo.boxes[0].lines[2].arrowLimit, 60);
  assert.deepEqual(geo.arrowCut, []);
});
