import assert from "node:assert/strict";

import { test } from "node:test";

import { EntryList, ROW, orderRows, vimStep } from "../js/listview.js";

import { filterRows } from "../js/data.js";

test("relevance follows profile maps, system, tables and battle before never-shown rows", () => {
  const chunks = ["battle.po", "maps/late.po", "maps/early.po", "system/menu.po", "tables/names.po", "script/other.po"]
    .map((file) => ({ file: `translation/po/${file}` }));
  const rows = [
    { chunk: 0, reach: "never shown" }, { chunk: 0 }, { chunk: 1, maps: ["late"] },
    { chunk: 2, maps: ["early"] }, { chunk: 2, maps: ["early"] },
    { chunk: 3 }, { chunk: 4 }, { chunk: 1, maps: ["early"], reach: "never shown" },
    { chunk: 1, maps: ["unknown"] }, { chunk: 5 },
    { chunk: 1, maps: ["late", "early"], reach: "possibly reachable" },
  ].map((row, i) => ({ id: String(i), translation: "matching text", problems: [], ...row }));
  const indices = rows.map((_, i) => i), maps = { "translation/po/maps/early.po": 0, "translation/po/maps/late.po": 1 };
  const order = (filtered = indices, mode) => orderRows(rows, filtered, chunks, maps, mode);
  assert.deepEqual(order(), [3, 4, 2, 8, 10, 5, 6, 9, 1, 0, 7]);
  assert.deepEqual(order(indices, "file"), indices);
  assert.deepEqual(indices, rows.map((_, i) => i));
  assert.deepEqual(order(filterRows(rows, { search: "matching", map: "early" })), [3, 4, 10, 7]);
  assert.deepEqual(order(filterRows(rows, { reach: "never shown" })), [0, 7]);
  assert.deepEqual(order(filterRows(rows, { search: "absent" })), []);
  assert.deepEqual(orderRows(rows.slice(0, 4).map(({ id }) => ({ id })), [0, 1, 2, 3]), [0, 1, 2, 3]);
});

const HEIGHT = 900;

const idOf = (p) => `s${String(p).padStart(5, "0")}`;

function fakeList() {
  return {
    scrollTop: 0,
    clientHeight: 0,
    listeners: {},
    addEventListener(ev, fn) { this.listeners[ev] = fn; },
    show(height = HEIGHT) {
      this.clientHeight = height;
    },
    hide() {
      this.clientHeight = 0;
      this.scrollTop = 0;
    },
  };
}

function fakeSpace() {
  return {
    style: { height: "" },
    rows: [],
    replaceChildren(...rows) {
      this.rows = rows;
    },
  };
}

function page(count = 0) {
  const list = fakeList();
  const space = fakeSpace();
  const view = new EntryList(list, space, (p) => ({ p, id: idOf(p) }));
  view.setCount(count);
  return { list, space, view };
}

const positions = (space) => space.rows.map((r) => r.p);

test("scroll renders new rows without a boundary click", () => {
  const { list, space, view } = page(100);
  list.show();
  view.shown(0);
  list.scrollTop = 50 * ROW;
  list.listeners.scroll();
  assert.ok(positions(space).includes(50));
  assert.ok(!positions(space).includes(0));
});

function documentList() {
  const { list, space, view } = page(10000);
  const listeners = new Map();
  const win = { scrollX: 0, scrollY: 0, innerHeight: 800,
    addEventListener: (name, fn) => listeners.set(name, fn),
    removeEventListener: (name) => listeners.delete(name),
    scrollTo(_x, y) { this.scrollY = Math.max(0, y); },
  };
  const layout = { top: 1200, hidden: false, inset: 128, bottom: 70 };
  space.getClientRects = () => layout.hidden ? [] : [space.getBoundingClientRect()];
  space.getBoundingClientRect = () => ({ top: layout.top - win.scrollY,
    bottom: layout.top - win.scrollY + view.count * view.row });
  view.usePageScroll(win, () => ({ top: layout.inset, bottom: layout.bottom }));
  return { list, space, view, win, layout, listeners };
}

test("document scrolling renders only rows between the sticky bar and navigation", () => {
  const { list, space, view, win, layout, listeners } = documentList();
  assert.deepEqual(view.render(), [0, 0]);
  win.scrollY = 1000;
  assert.equal(view.viewport, 530);
  assert.deepEqual(view.render(), [0, 14]);
  win.scrollY = layout.top + 500 * ROW;
  listeners.get("scroll")();
  assert.deepEqual(positions(space), Array.from({ length: 19 }, (_, i) => i + 498));
  assert.equal(list.scrollTop, 0);
  layout.inset = 208;
  win.innerHeight = 600;
  view.render();
  assert.ok(space.rows.length < 20);
  assert.ok(positions(space).includes(504));
  win.scrollY = layout.top + 10000 * ROW - 500;
  assert.equal(view.render()[1], 10000);
});

test("document rows survive card hiding and restore at the saved page position", () => {
  const { list, space, view, win, layout } = documentList();
  win.scrollY = 16000;
  view.render();
  const saved = view.scrollTop, rows = positions(space);
  layout.hidden = true;
  win.scrollY = 0;
  view.shown(0);
  assert.equal(win.scrollY, 0);
  assert.deepEqual(positions(space), rows);
  layout.hidden = false;
  view.scrollTop = saved;
  view.render();
  assert.equal(win.scrollY, 16000);
  assert.equal(list.scrollTop, 0);
  assert.deepEqual(positions(space), rows);
});

test("document reset retains visible filters and reveals results below the bar", () => {
  const { space, view, win, layout } = documentList();
  win.scrollY = 500;
  view.reset();
  assert.equal(win.scrollY, 500);
  win.scrollY = 16000;
  view.reset();
  assert.equal(win.scrollY, layout.top - layout.inset);
  view.setCount(3);
  assert.deepEqual(view.render(), [0, 3]);
  view.setCount(0);
  view.render();
  assert.deepEqual(space.rows, []);
  view.setCount(10000);
  view.shown(100);
  const top = space.getBoundingClientRect().top + 100 * ROW;
  assert.equal(top + ROW, win.innerHeight - layout.bottom);
  view.scrollTo(10);
  assert.equal(space.getBoundingClientRect().top + 10 * ROW, layout.inset);
});

test("switching to a pane detaches document scrolling", () => {
  const { list, view, listeners } = documentList();
  view.usePageScroll(null);
  assert.equal(listeners.has("scroll"), false);
  list.show();
  list.scrollTop = ROW * 50;
  assert.deepEqual(view.render(), [46, 70]);
  view.reset();
  assert.equal(list.scrollTop, 0);
});

test("vim keys: j next, k previous, never while typing", () => {
  const key = (code, target = { tagName: "DIV" }, mods = {}) => vimStep({ code, target, ...mods });
  assert.deepEqual([key("KeyJ"), key("KeyK"), key("KeyL")], [1, -1, 0]);
  assert.equal(key("KeyJ", { tagName: "DIV" }), 1); // a Cyrillic layout: same key position
  for (const tagName of ["INPUT", "TEXTAREA", "SELECT"]) assert.equal(key("KeyJ", { tagName }), 0, tagName);
  assert.equal(key("KeyK", { tagName: "DIV", isContentEditable: true }), 0); // the translation field
  for (const m of ["ctrlKey", "metaKey", "altKey", "shiftKey"]) assert.equal(key("KeyJ", undefined, { [m]: true }), 0, m);
});
