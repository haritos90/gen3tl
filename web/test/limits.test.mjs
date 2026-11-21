// Limit sources, bars and counts of the entry view.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { UNMEASURED_ADVICE, UNVERIFIED_ADVICE, barOf, countLabel, countsOf, sourceOf, unverifiedCount }
  from "../js/limits.js";
import { setCatalog } from "../js/ui.js";

const WINDOW = {
  kind: "multiline", width: 95, lines: 3, chars: 12, bytes: 15, source_lines: [2],
  sources: {
    width: { kind: "source", rule: "the English: the widest line of translation/po/tables/items.po", evidence: "" },
    lines: { kind: "measured", rule: "[layout.window_lines] s1: measured in game", evidence: "" },
    chars: { kind: "measured", rule: "[[table]] items: a field of 13 bytes with the terminator", evidence: "rows" },
  },
};

test("sources: kind, label, evidence; unmeasured from the English", () => {
  assert.deepEqual(sourceOf(WINDOW, "width"), {
    text: "the English: the widest line of translation/po/tables/items.po", kind: "source",
    label: "unmeasured", evidence: "", unmeasured: true, unverified: false });
  assert.deepEqual(sourceOf(WINDOW, "chars"), {
    text: "a field of 13 bytes with the terminator", kind: "measured", label: "measured",
    evidence: "rows", unmeasured: false, unverified: false, references: ["table items"] });
  assert.equal(sourceOf(WINDOW, "keys"), null);
  assert.equal(sourceOf({ kind: "single", width: 60, lines: 1 }, "width"), null); // data without limit sources
  assert.equal(sourceOf(null, "width"), null);
  assert.match(UNMEASURED_ADVICE, /keep a word whole where only this limit is in the way/);
});

test("sources: the description of a limit in the site language, the profile's names kept", (ctx) => {
  ctx.after(() => setCatalog(null));
  setCatalog({ "the English: the widest line of {file}": "английский текст: самая широкая строка в {file}",
    "unmeasured": "не измерено" });
  assert.equal(sourceOf(WINDOW, "width").text, "английский текст: самая широкая строка в translation/po/tables/items.po");
  assert.equal(sourceOf(WINDOW, "width").label, "не измерено");
  assert.equal(sourceOf(WINDOW, "chars").text, "a field of 13 bytes with the terminator",
    "a description without a translation stays English");
  setCatalog({ "keyboard layout columns: the columns its pack keyboard reaches": "столбцы клавиатуры пакета" });
  const keys = { kind: "measured", rule: "keyboard layout columns: the columns its pack keyboard reaches", evidence: "" };
  assert.equal(sourceOf({ kind: "naming", keys: 9, sources: { keys } }, "keys").text, "столбцы клавиатуры пакета");
});

test("sources: a policy estimate is unverified", () => {
  const rule = "[language policy] window_width.s1: text budget";
  const w = { kind: "single", width: 83, lines: 1, sources: { width: { kind: "profile", rule, evidence: "", unverified: true } } };
  assert.deepEqual(sourceOf(w, "width"), { text: "Translation text budget", kind: "profile", references: ["language policy window_width.s1"], label: "unverified estimate", evidence: "",
    unmeasured: false, unverified: true });
  w.sources.width.unverified = undefined;
  assert.equal(sourceOf(w, "width").label, "profile default");
  assert.match(UNVERIFIED_ADVICE, /to be measured with its text/);
  assert.equal(unverifiedCount({ unverified: { window_width: ["s1", "s2"], placeholders: ["PLAYER"],
    label_min_width: true } }), 4);
  assert.equal(unverifiedCount({}), 0);
  assert.equal(unverifiedCount(null), 0);
});

test("bars: a shadow fit is no overflow", () => {
  const w = { kind: "single", width: 60, lines: 1 };
  assert.deepEqual(barOf({ text: "a", width: 61, shadow: true }, w),
    { value: 61, limit: 60, unit: "px", over: false, shadow: true, arrow: false, label: "61 / 60\u00a0px, fits: shadow clipped" });
  assert.equal(barOf({ text: "a", width: 61, shadow: false }, w).over, true);
  assert.equal(barOf({ text: "a", width: 61 }, w).over, true); // the page's estimate: no flag
  assert.equal(barOf({ text: "a", width: 60, shadow: false }, w).label, "60 / 60\u00a0px");
  assert.deepEqual(barOf({ text: "a", width: null, shadow: false }, w).label, "not encodable");
  const keys = barOf({ text: "abc", width: 20, keys: 9 }, { ...w, keys: 8 });
  assert.deepEqual([keys.value, keys.limit, keys.unit, keys.over], [9, 8, "keys", true]);
});

test("bars: a line the prompt arrow follows is measured against its room", () => {
  const w = { kind: "dialogue", width: 208, arrow: 10, lines: 2 };
  const crowded = barOf({ text: "a", width: 201, shadow: false, room: 198 }, w);
  assert.deepEqual([crowded.limit, crowded.over, crowded.arrow, crowded.label], [198, true, true, "201 / 198\u00a0px, arrow after it"]);
  assert.equal(barOf({ text: "a", width: 198, shadow: false, room: 198 }, w).over, false);
  assert.equal(barOf({ text: "a", width: 201, shadow: false }, w).over, false); // the last line: no arrow
  const m = meterOf({ text: "a", width: 201, room: 198 }, w);
  assert.deepEqual([m.state, m.by, m.label], ["over", 3, "201 / 198\u00a0px, arrow after it · 3\u00a0px over"]);
});

test("counts: characters, bytes, lines and boxes with their limits", () => {
  const res = { laid: "a\\nb\\nc\\nd", lines: [[{ width: 6 }, { width: 6 }, { width: 6 }, { width: 6 }]] };
  assert.deepEqual(countsOf(res, WINDOW, 13).map((c) => [countLabel(c), c.over]), [
    ["characters: 13 / 12", true], ["bytes: 13 / 15", false], ["lines: 4 / 3", true]]); // one box: no box count
  assert.deepEqual(countsOf(res, WINDOW, null)[0], { name: "characters", value: null, limit: 12, over: true, note: "" });
  // message boxes: two lines per box, more with scroll
  const box = { kind: "dialogue", width: 208, lines: 2, source_lines: [2, 1] };
  const two = { laid: "a\\nb\\lc\\pd", lines: [[{}, {}, {}], [{}]] };
  assert.deepEqual(countsOf(two, box, 4).map(countLabel), ["lines, box 1: 3 / 2 (scrolls)", "lines, box 2: 1 / 2",
    "2 boxes (the English has 2)"]);
  const stuck = { laid: "a\\nb\\nc", lines: [[{}, {}, {}]] };
  assert.deepEqual(countsOf(stuck, box, 3).map((c) => c.over), [true, false]);
  assert.deepEqual(countsOf(res, null, 3), []);
});

test("page: the module and the classes it draws", () => {
  const app = readFileSync(new URL("../js/editor/entry.js", import.meta.url), "utf8");
  assert.match(app, /import \{ countLabel, countsOf, extentsOf, meterOf, sourceOf, arrowConsumer, waitingWindows \} from "\.\.\/limits\.js";/);
  const css = readFileSync(new URL("../site.css", import.meta.url), "utf8");
  for (const c of [".lines .ln.shadow", ".lines .ln.over", ".lines .ln.other", ".count-item.over", ".kv .src.english",
    ".kv .src.unverified", ".unmeasured"]) {
    assert.ok(css.includes(c), c);
  }
});

// The line meters of the workspace
import { meterOf } from "../js/limits.js";

test("line meters: fill, state and the overflow", () => {
  const w = { kind: "dialogue", width: 208, target: 200, lines: 2 };
  assert.deepEqual(meterOf({ text: "a", width: 171 }, w),
    { value: 171, limit: 208, unit: "px", over: false, shadow: false, arrow: false, label: "171 / 208\u00a0px", fill: 82, state: "ok", by: 0 });
  const over = meterOf({ text: "b", width: 214 }, w);
  assert.deepEqual([over.fill, over.state, over.by, over.label], [100, "over", 6, "214 / 208\u00a0px · 6\u00a0px over"]);
  assert.equal(meterOf({ text: "c", width: 204 }, w).state, "near");
  assert.equal(meterOf({ text: "d", width: 209, shadow: true }, w).state, "shadow");
  const bad = meterOf({ text: "e", width: null }, w);
  assert.deepEqual([bad.state, bad.fill, bad.by, bad.label], ["over", 100, 0, "not encodable"]);
  const keys = meterOf({ text: "f", keys: 9 }, { kind: "single", width: 60, keys: 7 });
  assert.deepEqual([keys.state, keys.by, keys.label], ["over", 2, "9 / 7\u00a0keys · 2\u00a0keys over"]);
  assert.deepEqual([meterOf({ text: "g", width: 50 }, null).fill, meterOf({ text: "g", width: 50 }, null).state], [0, "ok"]);
});

import { extentsOf, pairLabel } from "../js/limits.js";

const PAIR = { font: "hud", width: 111, where: "HUD before the eye icon", suffix: "",
  partner: { id: "table:moves/252", text: "Ошарашивание/", width: 57, species: { id: "table:species/86", text: "Сил" } } };

test("extents: the other windows, a sprite canvas among them, then the pair", () => {
  const res = {
    others: [{ font: "small", where: "battle menu", width: 63, value: 64, over: false, shadow: true },
      { font: "hud", where: "HUD ability", width: 64, value: 62, over: false, shadow: false }],
    pair: { value: 119, width: 111, where: PAIR.where, partner: PAIR.partner },
  };
  const rows = extentsOf(res);
  assert.deepEqual(rows.map((r) => [r.label, r.value, r.limit, r.shadow]),
    [["battle menu", 64, 63, true], ["HUD ability", 62, 64, false], ["pair", 119, 111, false]]);
  assert.equal(rows[1].title, "64\u00a0px in HUD ability (hud)");
  assert.equal(rows[2].title, "HUD before the eye icon: with «Ошарашивание/», 57\u00a0px, for «Сил»");
  assert.deepEqual(meterOf({ width: rows[2].value }, { width: rows[2].limit }).label, "119 / 111\u00a0px · 8\u00a0px over");
  assert.deepEqual(extentsOf({ lines: [] }), []);
  assert.equal(pairLabel(PAIR), "111\u00a0px in HUD before the eye icon with «Ошарашивание/», 57\u00a0px, for «Сил»");
});


test("multiline meters name the separate ordinary and waiting consumers", () => {
  const w = { kind: "multiline", width: 176, lines: 2, arrow: 10,
    consumers: [{ name: "party window", width: 224, arrow: 10 }, { name: "trade window", width: 176, arrow: 10 }] };
  const crowded = meterOf({ width: 170, room: 166 }, w);
  assert.equal(crowded.limit, 166);
  assert.equal(crowded.state, "over");
  assert.match(crowded.label, /trade window/);
  assert.equal(meterOf({ width: 166, room: 166 }, w).over, false);
  w.consumers[1].arrow = 0;
  assert.equal(meterOf({ width: 177, room: 214 }, w).limit, 176);
  assert.match(meterOf({ width: 177, room: 214 }, w).label, /trade window/);
  assert.equal(meterOf({ width: 1, room: 0 }, w).over, true);
});

test("runtime newlines count as scrolls without changing the draft", () => {
  const laid = "A\\nB\\nC\\nD";
  const window = { kind: "dialogue", width: 60, lines: 2, newline_scroll_after: 1 };
  const result = { laid, lines: [[{}, {}, {}, {}]] };
  const counts = countsOf(result, window, 7);
  assert.equal(counts[0].over, false);
  assert.equal(counts[0].note, "scrolls");
  assert.equal(result.laid, laid);
});

test("bars name only multiple consumers or a different binding window", () => {
  const rule = "[[layout.window]] notice: Notice message window";
  const source = { kind: "measured", rule };
  const window = { width: 80, sources: { width: source },
    consumers: [{ name: "Notice message window", width: 80, arrow: 10, source }] };
  assert.equal(barOf({ width: 60 }, window).label, "60 / 80\u00a0px");
  assert.equal(barOf({ width: 60, room: 70 }, window).label, "60 / 70\u00a0px, arrow after it");
  window.consumers.push({ name: "Second message window", width: 90, arrow: 30 });
  const bound = barOf({ width: 60, room: 60, consumer: "Second message window" }, window);
  assert.equal(bound.label, "60 / 60\u00a0px, arrow after it · window: Second message window");
  window.consumers = [window.consumers[1]];
  assert.equal(barOf({ width: 60, room: 60, consumer: "Second message window" }, window).label, bound.label);
});
