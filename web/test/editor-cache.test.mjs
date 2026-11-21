import assert from "node:assert/strict";
import { test } from "node:test";
import { JoinedCache, joinedKey, joinedValue, validJoined } from "../js/editor-cache.js";
import { applyEnglish, joinRows } from "../js/gametext.js";

function fixture() {
  const index = { profile: "game", language: "fixture", version: "1.0.0", toolkit: "0.1.0",
    list: [{ id: "one", translation: "First", status: "approved", problems: [] }],
    glossary: { one: { source_text: "First", translation: "First" } }, language_rules: { fold: {} } };
  const source = { profile: "game", rows: { one: [0, 6, "normal", "pointer", "digest"] } };
  const english = { texts: { one: "First" }, missing: [], digests: { one: "digest" } };
  return { index, source, english };
}

function memoryDatabase({ abort = false, blocked = false } = {}) {
  const values = new Map();
  const api = { values, closed: 0, writes: 0, open() {
    const request = {};
    queueMicrotask(() => {
      if (blocked) { request.onblocked(); return; }
      request.result = { close() { api.closed++; }, createObjectStore() {}, transaction() {
        const tx = { objectStore: () => ({
          get(key) { return run(() => structuredClone(values.get(key))); },
          put(value, key) { return run(() => { values.set(key, structuredClone(value)); api.writes++; }); },
        }) };
        function run(action) {
          const result = {};
          queueMicrotask(() => {
            if (abort) { tx.onabort(); return; }
            result.result = action();
            tx.oncomplete();
          });
          return result;
        }
        return tx;
      } };
      request.onupgradeneeded();
      request.onsuccess();
    });
    return request;
  } };
  return api;
}

test("join cache keys bind ROM, catalog content, source and toolkit revisions", async () => {
  const { index, source } = fixture();
  const key = await joinedKey("original", index, source);
  assert.notEqual(await joinedKey("different", index, source), key);
  for (const change of [
    (i) => { i.schema = "2.0.0"; },
    (i) => { i.toolkit = "0.1.1"; }, (i) => { i.version = "1.0.1"; },
    (i) => { i.language = "other"; }, (i) => { i.profile = "other"; },
    (i) => { i.list[0].translation = "Changed"; }, (i) => { i.list[0].status = "fuzzy"; },
    (i) => { i.list[0].id = "other"; }, (i) => { i.list[0].problems.push({ severity: "warning", message: "Changed" }); },
    (i) => { i.glossary.one.translation = "Changed"; }, (i) => { i.language_rules.fold = { a: "b" }; },
  ]) {
    const next = structuredClone(index);
    change(next);
    assert.notEqual(await joinedKey("original", next, source), key);
  }
  const next = structuredClone(source);
  next.rows.one[0] = 4;
  assert.notEqual(await joinedKey("original", index, next), key);
  joinRows(index.list, { one: "First" }, index.glossary);
  assert.equal(await joinedKey("original", index, source), key);
});

test("IndexedDB cache reuses complete joins without catalog metadata or drafts", async () => {
  const { index, source, english } = fixture(), db = memoryDatabase(), cache = new JoinedCache(db);
  const key = await joinedKey("original", index, source);
  assert.equal(await cache.get(key), null);
  const rows = joinRows(structuredClone(index.list), english.texts, index.glossary);
  const value = joinedValue(english, rows);
  assert.equal(validJoined(value, index.list, index.glossary), true);
  assert.equal(await cache.put(key, value), true);
  const saved = await cache.get(key);
  assert.deepEqual(saved, value);
  assert.equal(db.writes, 1);
  assert.equal(db.closed, 3);
  assert.deepEqual(Object.keys(saved.rows[0]), ["id", "terms", "problems"]);
  const restored = structuredClone(index.list);
  applyEnglish(restored, saved.source_text.texts);
  restored.forEach((row, i) => { row._problems = row.problems; Object.assign(row, saved.rows[i]); });
  assert.deepEqual(restored, rows);
  assert.equal(validJoined({ ...saved, rows: [] }, restored, index.glossary), false);
  assert.equal(validJoined({ ...saved, rows: [{ id: "different", problems: [], terms: [] }] }, restored, index.glossary), false);
  assert.equal(validJoined({ ...saved, rows: [{ id: "one", terms: [] }] }, restored, index.glossary), false);
  assert.equal(validJoined({ ...saved, rows: [{ id: "one", problems: [] }] }, restored, index.glossary), false);
  assert.equal(validJoined(null, restored, index.glossary), false);
});

test("disabled, blocked, aborted and full storage fall back without rejecting", async () => {
  for (const db of [null, { open() { throw new Error("QuotaExceededError"); } },
    memoryDatabase({ abort: true }), memoryDatabase({ blocked: true })]) {
    const cache = new JoinedCache(db);
    assert.equal(await cache.get("key"), null);
    assert.equal(await cache.put("key", {}), null);
  }
});
