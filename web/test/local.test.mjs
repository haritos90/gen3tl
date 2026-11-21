import assert from "node:assert/strict";
import test from "node:test";
import { LocalSave } from "../js/local.js";
import { Edits, NoteEdits } from "../js/store.js";

const session = { profile: "fixture", language: "fr", rom_sha1: "base", token: "nonce" };
const index = { profile: "fixture", language: "fr", rom: { sha1: "base" } };
const entry = { id: "s1", source_text: "Hello", translation: "Bonjour", note: "source" };
const response = (data, ok = true) => ({ ok, json: async () => data });

test("static preview remains read-only", async () => {
  const local = new LocalSave(async () => response({}, false));
  await local.connect();
  assert.equal(local.matches(index), false);
  await assert.rejects(() => local.save(index, null, null), /not writable/);
});

test("successful save supplies identity and stale-edit snapshots", async () => {
  const edits = new Edits(null, "fr", "fixture");
  edits.set(entry, "Salut", entry.note);
  const local = new LocalSave(async (path, request) => {
    if (path === "api/session") return response(session);
    assert.equal(request.headers["X-Editor-Token"], "nonce");
    assert.deepEqual(JSON.parse(request.body), { profile: "fixture", language: "fr", rom_sha1: "base",
      entries: [{ id: "s1", source_text: "Hello", base: "Bonjour", base_state: "text", translation: "Salut", state: "text" }] });
    return response({ saved: 1 });
  });
  await local.connect();
  assert.equal(local.matches({ ...index, language: "other" }), false);
  await local.save(index, edits, async () => entry);
  assert.equal(edits.size, 0);
  assert.equal(local.busy, false);
});

test("failed saves preserve the browser draft", async () => {
  const edits = new Edits(null, "fr", "fixture");
  edits.set(entry, "Salut", entry.note);
  const local = new LocalSave(async () => response({ error: "stale source" }, false));
  local.session = session;
  await assert.rejects(() => local.save(index, edits, async () => entry), /stale source/);
  assert.equal(edits.get("s1").translation, "Salut");
  assert.equal(local.busy, false);
});

for (const next of ["Encore", "Bonjour"]) {
  test(`edits made during saving survive: ${next}`, async () => {
    const edits = new Edits(null, "fr", "fixture");
    edits.set(entry, "Salut", entry.note);
    const local = new LocalSave(async () => {
      edits.set(entry, next, entry.note);
      return response({ saved: 1 });
    });
    local.session = session;
    await local.save(index, edits, async () => entry);
    assert.equal(edits.get("s1").translation, next);
    assert.equal(edits.get("s1").base, "Salut");
  });
}

for (const [before, after] of [["original", "empty"], ["empty", "original"]]) {
  test(`state-only save carries its baseline: ${before} to ${after}`, async () => {
    const blank = { ...entry, translation: "", flags: before === "empty" ? ["empty"] : [] };
    const edits = new Edits(null, "xx", "fixture");
    edits.set(blank, "", blank.note, null, false, after);
    const local = new LocalSave(async (path, request) => {
      const [record] = JSON.parse(request.body).entries;
      assert.equal(record.state, after);
      assert.equal(record.base_state, before);
      assert.equal(record.translation, "");
      assert.equal(record.base, "");
      return response({ saved: 1 });
    });
    local.session = session;
    await local.save(index, edits, async () => blank);
    assert.equal(edits.size, 0);
  });

  for (const discard of [false, true]) {
    test(`state-only reversal during save survives: ${after}, discard ${discard}`, async () => {
      const blank = { ...entry, translation: "", flags: before === "empty" ? ["empty"] : [] };
      const edits = new Edits(null, "xx", "fixture");
      edits.set(blank, "", blank.note, null, false, after);
      const local = new LocalSave(async () => {
        if (discard) edits.map.delete(blank.id);
        else edits.set(blank, "", "new note", null, false, before);
        return response({ saved: 1 });
      });
      local.session = session;
      await local.save(index, edits, async () => blank);
      assert.equal(edits.get(blank.id).state, before);
      assert.equal(edits.get(blank.id).base_state, after);
    });
  }
}


function noteDraft() {
  const storage = { value: null,
    getItem() { return this.value; },
    setItem(key, value) { this.value = value; },
    removeItem() { this.value = null; } };
  const notes = new NoteEdits(storage, "fr", "fixture");
  const subject = { kind: "entry", id: "s1" };
  const binding = { kind: "entry", id: "s1", source_sha256: "a".repeat(64) };
  const fields = { guidance: ["Preserve the context."], scope: { kind: "language", language: "fr" } };
  const id = notes.set(subject, binding, fields);
  return { notes, id, subject, binding, fields };
}

test("note-only local save carries and clears submitted proposals", async () => {
  const { notes } = noteDraft();
  const edits = new Edits(null, "fr", "fixture");
  const local = new LocalSave(async (path, request) => {
    const doc = JSON.parse(request.body);
    assert.equal(doc.entries.length, 0);
    assert.equal(doc.note_changes_schema, "3.0.0");
    assert.equal(doc.note_changes.length, 1);
    assert.equal(doc.note_changes[0].proposed.guidance[0], "Preserve the context.");
    return response({ saved: 0, notes: 1 });
  });
  local.session = session;
  await local.save(index, edits, async () => entry, notes);
  assert.equal(notes.size, 0);
});

test("note edits made during local save remain drafts", async () => {
  const { notes, id, subject, binding, fields } = noteDraft();
  const local = new LocalSave(async () => {
    notes.set(subject, binding, { ...fields, guidance: ["Changed during save."] }, null, id);
    return response({ saved: 0, notes: 1 });
  });
  local.session = session;
  await local.save(index, new Edits(null, "fr", "fixture"), async () => entry, notes);
  assert.equal(notes.size, 1);
  assert.equal(notes.get(id).note.guidance[0], "Changed during save.");
});
