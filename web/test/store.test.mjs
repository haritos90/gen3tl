// Edits in the browser and their export.
import assert from "node:assert/strict";
import { test } from "node:test";
import { createHash } from "node:crypto";

import { Edits, GlyphEdits, KeyboardEdits, NoteEdits, PictureEdits, TermEdits, WorkspaceSettings, exportDoc, exportName, sameField, termRecord,
  noteProblems, noteRevision } from "../js/store.js";
import { evidenceFile, fakeStorage, noteCollection } from "./fixture.mjs";

const E1 = { id: "s1", translation: "Да", note: "" };
const E2 = { id: "s2", translation: "", note: "" };

test("stored note drafts migrate bindings and revisions once without hiding conflicts", () => {
  const storage = fakeStorage(), current = noteCollection().note[1];
  const base = structuredClone(current);
  base.binding.english_sha256 = base.binding.source_sha256;
  delete base.binding.source_sha256;
  const draft = { base, base_revision: noteRevision(base), note: { ...structuredClone(base), guidance: ["Local guidance."] },
    message: "Review separately.", time: "2026-01-01T00:00:00Z" };
  const invalid = { ...structuredClone(draft), note: { ...base, id: "invalid" }, base_revision: "invalid" };
  storage.setItem("gen3tl-editor:notes:project", JSON.stringify({ schema: "1.0.0", drafts: [draft, invalid] }));
  const edits = new NoteEdits(storage, current.scope.language, "project");
  assert.equal(edits.size, 1);
  assert.equal(edits.conflict(current.id, current, current.binding), false);
  const changed = { ...current, guidance: ["New published guidance."] };
  assert.equal(edits.conflict(current.id, changed, current.binding), true);
  assert.deepEqual(edits.get(current.id).note.guidance, draft.note.guidance);
  assert.equal(edits.get(current.id).message, draft.message);
  assert.equal(edits.get(current.id).time, draft.time);
  assert.equal(edits.get(current.id).base_revision, noteRevision(current));
  assert.equal(edits.changes()[0].binding.source_sha256, current.binding.source_sha256);
  const saved = storage.getItem(edits.key);
  assert.equal(JSON.parse(saved).schema, "2.0.0");
  assert.equal(saved.includes("english_sha256"), false);
  assert.equal(new NoteEdits(storage, current.scope.language, "project").size, 1);
  assert.equal(storage.getItem(edits.key), saved);
});

test("note-only drafts persist by project, subject and scope without translation edits", () => {
  const storage = fakeStorage(), note = noteCollection().note[1], before = structuredClone(note);
  const drafts = new NoteEdits(storage, note.scope.language, "project");
  const fields = { ...note, guidance: ["First line.\nSecond line."] };
  const id = drafts.set(note.subject, note.binding, fields, null);
  assert.equal(drafts.size, 1);
  const reload = new NoteEdits(storage, note.scope.language, "project");
  assert.deepEqual(reload.get(id).note.guidance, fields.guidance);
  assert.equal(reload.get(id).note.status, "proposed");
  assert.equal(reload.get(id).base_revision, "null");
  assert.equal(reload.get(id).note.author, undefined);
  assert.equal(reload.get(id).note.created, undefined);
  assert.equal(reload.forSubject({ kind: "term", id: note.subject.id }).length, 0);
  assert.equal(new NoteEdits(storage, "other", "project").size, 0);
  assert.equal(new NoteEdits(storage, note.scope.language, "other").size, 0);
  reload.set(note.subject, note.binding, { ...fields, scope: { kind: "shared" } }, null, id);
  assert.equal(new NoteEdits(storage, "other", "project").get(id).note.scope.kind, "shared");
  assert.equal(new Edits(storage, note.scope.language, "project").size, 0);
  assert.equal(new TermEdits(storage, note.scope.language, "project").size, 0);
  assert.deepEqual(note, before);
  reload.remove(id);
  assert.equal(new NoteEdits(storage, note.scope.language, "project").size, 0);
});

test("saving one language preserves another open workspace's note drafts", () => {
  const storage = fakeStorage(), note = noteCollection().note[1];
  const first = new NoteEdits(storage, note.scope.language, "project"), second = new NoteEdits(storage, "other", "project");
  const one = first.set(note.subject, note.binding, note);
  const two = second.set(note.subject, note.binding, { ...note, scope: { kind: "language", language: "other" } });
  assert.equal(new NoteEdits(storage, note.scope.language, "project").get(one).note.id, one);
  first.remove(one);
  assert.equal(new NoteEdits(storage, "other", "project").get(two).note.id, two);
});

test("note edits retain the first base and recover explicitly from changes or deletion", () => {
  const note = noteCollection().note[1], before = structuredClone(note);
  const drafts = new NoteEdits(fakeStorage(), note.scope.language, "project");
  const fields = { ...note, guidance: ["Local change."] };
  drafts.set(note.subject, note.binding, fields, note, note.id, "Review this separately.");
  const current = { ...note, guidance: ["Published change."] };
  drafts.set(note.subject, note.binding, { ...fields, reason: ["Local reason."] }, current, note.id, "Review this separately.");
  assert.deepEqual(drafts.get(note.id).base, before);
  assert.equal(drafts.get(note.id).message, "Review this separately.");
  assert.equal(drafts.conflict(note.id, current, note.binding), true);
  assert.equal(drafts.conflict(note.id, Object.fromEntries(Object.entries(note).reverse()), note.binding), false);
  assert.equal(drafts.get(note.id).note.reviewed_by, undefined);
  drafts.rebase(note.id, current, note.binding);
  assert.equal(drafts.conflict(note.id, current, note.binding), false);
  assert.deepEqual(drafts.get(note.id).note.guidance, ["Local change."]);
  const binding = { ...note.binding, source_sha256: "b".repeat(64) };
  assert.equal(drafts.conflict(note.id, current, binding), true);
  drafts.rebase(note.id, current, binding);
  assert.equal(drafts.conflict(note.id, current, binding), false);
  assert.deepEqual(drafts.get(note.id).note.binding, binding);
  assert.equal(drafts.conflict(note.id, null, binding), true);
  const replacement = drafts.rebase(note.id, null, binding);
  assert.notEqual(replacement, note.id);
  assert.equal(drafts.get(note.id), null);
  assert.equal(drafts.get(replacement).base, null);
  assert.equal(drafts.get(replacement).note.author, undefined);
  assert.deepEqual(note, before);
});

test("returning note fields to their base removes only the note draft", () => {
  const note = noteCollection("term").note[1], drafts = new NoteEdits(fakeStorage(), note.scope.language, "project");
  drafts.set(note.subject, null, { ...note, reason: ["Reason."] }, note);
  assert.equal(drafts.size, 1);
  drafts.set(note.subject, null, note, note);
  assert.equal(drafts.size, 0);
  assert.throws(() => drafts.set({ kind: "graphic", id: "one" }, null, note), /Invalid note subject/);
  assert.throws(() => drafts.set(note.subject, null, { ...note, scope: { kind: "language", language: "other" } }), /scope/);
});

test("note drafts survive unavailable storage and isolate returned values", () => {
  const note = noteCollection().note[1];
  for (const storage of [null, fakeStorage(true)]) {
    const drafts = new NoteEdits(storage, note.scope.language, "project");
    drafts.set(note.subject, note.binding, { ...note, facts: ["Fact."] }, note);
    assert.equal(drafts.saved, false);
    drafts.get(note.id).note.facts.push("Not saved.");
    drafts.forSubject(note.subject)[0].base.guidance.push("Not saved.");
    assert.deepEqual(drafts.get(note.id).note.facts, ["Fact."]);
    assert.deepEqual(drafts.get(note.id).base, note);
  }
});

test("term citations require supporting quotations and real web retrieval dates", () => {
  const note = { ...noteCollection("term").note[1], sources: [] };
  assert.match(noteProblems(note).join(" "), /Term claims need/);
  const source = { kind: "web", name: "Reference", address: "https://example.test/term", quoted_term: "Term",
    retrieved: "2026-01-01", verification: "unresolved" };
  note.sources = [source];
  assert.deepEqual(noteProblems(note), []);
  for (const retrieved of [undefined, "2026-02-30", "2026-1-1", "2999-01-01"]) {
    note.sources = [{ ...source, retrieved }];
    assert.match(noteProblems(note).join(" "), /retrieval date/);
  }
  note.sources = [{ ...source, address: "javascript:alert(1)" }];
  assert.match(noteProblems(note).join(" "), /HTTP or HTTPS/);
  note.sources = [{ kind: "project", name: "Meaning", address: "context:one", quote: "Supporting context.", relation: "context" }];
  assert.deepEqual(noteProblems(note), []);
  note.guidance = [];
  assert.match(noteProblems(note).join(" "), /Add guidance/);
});

test("unchanged historical sources retain unknown dates and edited citations lose verification", () => {
  const note = noteCollection("term").note[0], drafts = new NoteEdits(fakeStorage(), "xx", "project");
  assert.ok(!noteProblems(note, note).some((text) => text.includes("retrieval date")));
  const fields = { ...note, guidance: ["New guidance."], sources: [{ ...note.sources[0], verification: "checked" }] };
  drafts.set(note.subject, null, fields, note);
  assert.equal(drafts.get(note.id).note.sources[0].verification, "unresolved");
  assert.match(noteProblems(drafts.get(note.id).note, note).join(" "), /retrieval date/);
  assert.equal(drafts.get(note.id).note.author, undefined);
  assert.equal(drafts.get(note.id).note.created, undefined);
});

test("shared observations retain their source identity and reproducible context", () => {
  const note = noteCollection().note[0];
  assert.match(noteProblems(note).join(" "), /ROM or build reference/);
  note.sources = [{ kind: "rom", name: "Original", address: "sha1:123", locator: "Map 2, sign 3", verification: "recorded" }];
  assert.deepEqual(noteProblems(note), []);
  assert.notEqual(noteRevision(note), noteRevision({ ...note, evidence: [] }));
});

test("picture captures persist with hashes and metadata outside replacement exports", async () => {
  const storage = fakeStorage(), note = noteCollection("graphic", "G01").note[1];
  const edits = new NoteEdits(storage, note.scope.language, "project");
  const pictures = new PictureEdits(storage, note.scope.language, "project");
  pictures.set("G01", { base: "base", areas: [[0, 0, 1, 1]], tiles: null, png: "replacement" });
  const exported = exportDoc(note.scope.language, "project", [], pictures.changes());
  const id = edits.set(note.subject, note.binding, note);
  const file = evidenceFile();
  await edits.attach(id, file, { role: "proposed", build: "1.2.3", context: "Menu, page 1" });
  await edits.attach(id, file, { role: "original", build: "1.0.0", context: "Menu, page 1" });
  const reload = new NoteEdits(storage, note.scope.language, "project"), draft = reload.get(id);
  assert.equal(draft.attachments.length, 2);
  assert.notEqual(draft.attachments[0].id, draft.attachments[1].id);
  const hash = createHash("sha256").update(new Uint8Array(await file.arrayBuffer())).digest("hex");
  for (const attachment of draft.attachments) {
    assert.equal(attachment.sha256, hash);
    assert.equal(attachment.size, file.size);
    assert.equal(attachment.type, "image/png");
    assert.equal(Buffer.from(attachment.data, "base64").length, file.size);
    assert.ok(draft.note.evidence.some((row) => row.captures[0].address === `attachment:${attachment.id}`));
  }
  assert.deepEqual(noteProblems(draft.note, null, draft.attachments), []);
  assert.deepEqual(exportDoc(note.scope.language, "project", [], pictures.changes()), exported);
  assert.equal(new NoteEdits(storage, "other", "project").size, 0);
  reload.set(note.subject, note.binding, { ...draft.note, scope: { kind: "shared" } }, null, id);
  assert.equal(new NoteEdits(storage, "other", "project").get(id).attachments.length, 2);
  reload.removeAttachment(id, draft.attachments[1].id);
  const remaining = reload.get(id);
  assert.equal(remaining.attachments.length, 1);
  assert.match(noteProblems(remaining.note, null, remaining.attachments).join(" "), /matching original capture/);
  reload.remove(id);
  assert.equal(new NoteEdits(storage, note.scope.language, "project").size, 0);
  assert.deepEqual(exportDoc(note.scope.language, "project", [], pictures.changes()), exported);
});

test("capture-only edits preserve published evidence and survive conflict recovery", async () => {
  const note = noteCollection("graphic", "G01").note[0], before = structuredClone(note);
  const edits = new NoteEdits(fakeStorage(), "xx", "project");
  const metadata = { role: "original", build: "1.0.0", context: "Menu" };
  await edits.attach(note.id, evidenceFile(), metadata, note);
  let draft = edits.get(note.id);
  assert.deepEqual(draft.note.evidence[0], note.evidence[0]);
  assert.equal(draft.note.evidence[1].verification, "unresolved");
  edits.removeAttachment(note.id, draft.attachments[0].id);
  assert.equal(edits.get(note.id), null);
  await edits.attach(note.id, evidenceFile(), metadata, note);
  const current = { ...note, reason: ["Updated reason."] };
  assert.equal(edits.conflict(note.id, current, note.binding), true);
  edits.rebase(note.id, current, note.binding);
  assert.equal(edits.conflict(note.id, current, note.binding), false);
  draft = edits.get(note.id);
  assert.equal(draft.attachments.length, 1);
  const replacement = edits.rebase(note.id, null, note.binding);
  assert.notEqual(replacement, note.id);
  assert.equal(edits.get(replacement).attachments.length, 1);
  assert.deepEqual(note, before);
});

test("capture validation keeps incomplete metadata and unavailable storage visible", async () => {
  const note = noteCollection("graphic", "G01").note[1];
  const edits = new NoteEdits(null, note.scope.language, "project");
  const id = edits.set(note.subject, note.binding, { ...note, facts: ["Clipped label."] });
  await edits.attach(id, evidenceFile(), { role: "proposed", build: "", context: "" });
  const draft = edits.get(id);
  assert.equal(edits.saved, false);
  const problems = noteProblems(draft.note, null, draft.attachments).join(" ");
  assert.match(problems, /build identity/);
  assert.match(problems, /ROM or build reference/);
  assert.match(problems, /matching original capture/);
  assert.match(noteProblems(draft.note).join(" "), /attachment is missing/);
  await assert.rejects(edits.attach(id, { size: 6 * 1024 * 1024 }, {}), /at most 3 MiB/);
  await assert.rejects(edits.attach(id, { size: 4 * 1024 * 1024 }, {}), /at most 3 MiB/);
  await assert.rejects(edits.attach(id, { arrayBuffer: async () => new TextEncoder().encode("<svg />").buffer }, {}), /PNG or JPEG/);
  assert.equal(edits.get(id).attachments.length, 1);
  assert.throws(() => edits.set(note.subject, { kind: "graphic", id: "G02" }, note), /Invalid note subject/);
});

test("reverting while a capture loads never resurrects the draft", async () => {
  const note = noteCollection("graphic", "G01").note[1], edits = new NoteEdits(fakeStorage(), "xx", "project");
  let finish;
  const file = evidenceFile(), bytes = new Promise((resolve) => { finish = resolve; });
  const pending = edits.attach(note.id, { ...file, arrayBuffer: () => bytes }, {}, note);
  edits.remove(note.id);
  finish(await file.arrayBuffer());
  assert.equal(await pending, false);
  assert.equal(edits.size, 0);
});

test("reference preferences belong to one workspace and tolerate unavailable storage", () => {
  const storage = fakeStorage();
  const settings = new WorkspaceSettings(storage, "es", "game");
  assert.equal(settings.referenceLanguage, "");
  assert.equal(settings.setReferenceLanguage("ru"), true);
  assert.equal(new WorkspaceSettings(storage, "es", "game").referenceLanguage, "ru");
  assert.equal(new WorkspaceSettings(storage, "de", "game").referenceLanguage, "");
  assert.equal(new WorkspaceSettings(storage, "es", "other").referenceLanguage, "");
  for (const value of ["invalid", '{"referenceLanguage":3}']) {
    storage.setItem(settings.key, value);
    assert.equal(new WorkspaceSettings(storage, "es", "game").referenceLanguage, "");
  }
  for (const unavailable of [null, fakeStorage(true), { getItem() { throw new Error("denied"); } }]) {
    const temporary = new WorkspaceSettings(unavailable, "es", "game");
    assert.equal(temporary.referenceLanguage, "");
    assert.equal(temporary.setReferenceLanguage("ru"), false);
    assert.equal(temporary.referenceLanguage, "ru");
  }
});

test("edits persist per language and profile", () => {
  const st = fakeStorage();
  const a = new Edits(st, "ru", "p");
  assert.ok(a.set(E1, "Да!", ""));
  assert.ok(a.set(E2, "Нет", ""));
  assert.deepEqual([...st.map.keys()], ["gen3tl-editor:edits:ru:p"]);
  const b = new Edits(st, "ru", "p");
  assert.equal(b.size, 2);
  assert.equal(b.get("s1").translation, "Да!");
  assert.equal(b.get("s1").base, "Да");
  assert.equal(new Edits(st, "de", "p").size, 0);
});

test("an edit back to the data's text is no edit", () => {
  const st = fakeStorage();
  const a = new Edits(st, "ru", "p");
  a.set(E1, "Да!", "");
  a.set(E1, "Да", "");
  assert.equal(a.size, 0);
  assert.equal(st.map.size, 0);
});

test("source-note snapshots never create text drafts", () => {
  for (const published of [undefined, "", "Published context"]) {
    const entry = { id: "entry", translation: "Text", ...(published === undefined ? {} : { note: published }) };
    for (const saved of [undefined, "", "Historical context"]) {
      const storage = fakeStorage(), edits = new Edits(storage, "xx", "fixture");
      edits.set(entry, "Text", saved);
      assert.equal(edits.size, 0);
      edits.set(entry, "Draft", saved);
      assert.equal(edits.size, 1);
      const restored = new Edits(storage, "xx", "fixture");
      assert.equal(restored.changes()[0].note, saved);
      restored.set(entry, "Text", saved);
      assert.deepEqual(restored.changes(), []);
      assert.equal(storage.map.size, 0);
    }
  }
});

test("stale text drafts retain their baseline and historical notes when projections disappear", () => {
  const storage = fakeStorage(), edits = new Edits(storage, "xx", "fixture");
  const original = { id: "entry", source_text: "Source", translation: "Old", note: "Historical context" };
  edits.set(original, "Draft", original.note);
  const restored = new Edits(storage, "xx", "fixture");
  const current = { id: original.id, source_text: "Updated source", translation: "Published" };
  restored.set(current, "Published", undefined);
  assert.deepEqual(restored.changes(), [{ id: original.id, translation: "Published", state: "text",
    note: original.note, base: "Old", base_state: "text" }]);
  assert.equal(restored.get(original.id).source_text, "Source");
  restored.set(current, "Old", undefined);
  assert.deepEqual(restored.changes(), []);
});

test("historical note-only drafts remain recoverable until explicitly reverted", () => {
  const storage = fakeStorage();
  storage.setItem("gen3tl-editor:edits:xx:fixture", JSON.stringify({
    entry: { translation: "Text", base: "Text", note: "Historical context" },
  }));
  const edits = new Edits(storage, "xx", "fixture");
  assert.equal(edits.changes()[0].note, "Historical context");
  edits.remove("entry");
  assert.equal(new Edits(storage, "xx", "fixture").size, 0);
});

test("check results are kept with the edit", () => {
  const a = new Edits(fakeStorage(), "ru", "p");
  a.set(E1, "Да!", "");
  a.checked("s1", [{ message: "x", severity: "error" }], true);
  assert.deepEqual([a.get("s1").problems, a.get("s1").exact], [[{ message: "x", severity: "error" }], true]);
  assert.ok(a.checked("s9", [], true)); // no edit: nothing
});

test("storage failures keep the edits for the session", () => {
  const broken = new Edits(fakeStorage(true), "ru", "p");
  assert.equal(broken.set(E1, "Да!", ""), false);
  assert.equal(broken.get("s1").translation, "Да!");
  const none = new Edits(null, "ru", "p");
  assert.equal(none.set(E1, "Да!", ""), false);
  assert.equal(none.size, 1);
  const st = fakeStorage();
  st.setItem("gen3tl-editor:edits:ru:p", "{not json");
  assert.equal(new Edits(st, "ru", "p").size, 0);
});

test("export lists changed entries in catalog order", () => {
  const a = new Edits(fakeStorage(), "ru", "p");
  a.set(E2, "Нет", "");
  a.set(E1, "Да!", "");
  assert.deepEqual(a.changes(["s1", "s2", "s3"]), [
    { id: "s1", translation: "Да!", state: "text", note: "", base: "Да", base_state: "text" },
    { id: "s2", translation: "Нет", state: "text", note: "", base: "", base_state: "original" },
  ]);
  assert.deepEqual(exportDoc("ru", "p", a.changes()), {
    version: "4.0.0", language: "ru", profile: "p",
    entries: [{ id: "s2", translation: "Нет", state: "text", note: "", base: "", base_state: "original" },
      { id: "s1", translation: "Да!", state: "text", note: "", base: "Да", base_state: "text" }],
    pictures: [], terms: [], glyphs: [],
  });
  assert.equal(exportName("ru", new Date("2026-10-21T19:30:12Z")), "gen3tl-ru-edits-202610211930.json");
  a.clear();
  assert.equal(a.size, 0);
});

const RULE_VALUES = [
  { state: "original", translation: "", flags: [] },
  { state: "empty", translation: "", flags: ["empty"] },
  { state: "text", translation: "x", flags: [] },
];

for (const from of RULE_VALUES) for (const to of RULE_VALUES) {
  test(`rule state ${from.state} to ${to.state} survives reload and export`, () => {
    const storage = fakeStorage();
    const entry = { id: "rule", translation: from.translation, flags: from.flags, note: "source" };
    const edits = new Edits(storage, "xx", "fixture");
    edits.set(entry, to.translation, entry.note, null, false, to.state);
    const restored = new Edits(storage, "xx", "fixture");
    const expected = from.state === to.state ? [] : [{ id: "rule", translation: to.translation,
      state: to.state, note: "source", base: from.translation, base_state: from.state }];
    assert.deepEqual(exportDoc("xx", "fixture", restored.changes()).entries, expected);
    if (expected.length) {
      restored.checked("rule", [], true);
      assert.deepEqual(new Edits(storage, "xx", "fixture").changes(), expected);
    }
    restored.remove("rule");
    assert.deepEqual(new Edits(storage, "xx", "fixture").changes(), []);
    assert.equal(storage.map.size, 0);
  });
}

test("a state edit retains its first baseline across later catalog changes", () => {
  const storage = fakeStorage();
  const original = { id: "rule", translation: "", note: "", flags: [] };
  const edits = new Edits(storage, "xx", "fixture");
  edits.set(original, "", "", null, false, "empty");
  const next = new Edits(storage, "xx", "fixture");
  next.set({ ...original, flags: ["empty"] }, "z", "");
  assert.deepEqual(next.changes(), [{ id: "rule", translation: "z", state: "text", note: "", base: "", base_state: "original" }]);
  next.set(original, "", "", null, false, "original");
  assert.equal(next.size, 0);
});

test("note snapshots cannot create Off drafts and inheritance remains separate from discard", () => {
  const entry = { id: "rule", translation: "", note: "", flags: ["empty"] };
  const edits = new Edits(fakeStorage(), "xx", "fixture");
  edits.set(entry, "", "source");
  assert.equal(edits.size, 0);
  edits.set(entry, "", "", null, false, "original");
  assert.equal(edits.get("rule").state, "original");
  assert.equal(edits.get("rule").base_state, "empty");
  edits.remove("rule");
  assert.equal(edits.size, 0);
  assert.deepEqual(entry.flags, ["empty"]);
});

test("grouped writes validate every entry before persisting any change", () => {
  const storage = fakeStorage(), edits = new Edits(storage, "xx", "fixture");
  const first = { id: "first", translation: "", note: "first note" };
  const second = { id: "second", translation: "", note: "second note" };
  const change = (entry) => ({ entry, translation: "", state: "empty", note: entry.note });
  assert.throws(() => edits.setMany([change(first), change({ ...second, owner: "pack" })]), /read-only/);
  assert.equal(edits.size, 0);
  assert.equal(new Edits(storage, "xx", "fixture").size, 0);
  edits.setMany([change(first), change(second)]);
  const loaded = new Edits(storage, "xx", "fixture");
  assert.equal(loaded.size, 2);
  assert.deepEqual(loaded.changes().map((row) => [row.state, row.base_state, row.note]),
    [["empty", "original", "first note"], ["empty", "original", "second note"]]);
  loaded.removeMany([first.id, second.id]);
  assert.equal(new Edits(storage, "xx", "fixture").size, 0);
});

test("legacy stored drafts migrate without inventing intentional empties", () => {
  const storage = fakeStorage();
  storage.setItem("gen3tl-editor:edits:xx:fixture", JSON.stringify({
    rule: { translation: "", note: "", base: "x" },
    word: { translation: "y", note: "", base: "" },
  }));
  const edits = new Edits(storage, "xx", "fixture");
  assert.deepEqual(edits.changes(), [
    { id: "rule", translation: "", state: "original", note: "", base: "x", base_state: "text" },
    { id: "word", translation: "y", state: "text", note: "", base: "", base_state: "original" },
  ]);
  edits.save();
  assert.deepEqual(new Edits(storage, "xx", "fixture").changes(), edits.changes());
});

test("state-only drafts survive unavailable storage and reject invalid updates", () => {
  const entry = { id: "rule", translation: "", note: "" };
  const edits = new Edits(fakeStorage(true), "xx", "fixture");
  assert.equal(edits.set(entry, "", "", null, false, "empty"), false);
  const expected = edits.changes();
  assert.equal(expected[0].state, "empty");
  assert.throws(() => edits.set(entry, "x", "", null, false, "empty"), /invalid translation state/);
  assert.deepEqual(edits.changes(), expected);
});

// Uploaded graphics in their area form
const UP = { base: "ab".repeat(32), areas: [[8, 16, 15, 6]], tiles: null, png: "iVBORw0KGgo=", name: "G10.png", lines: ["G10: ok"] };

test("picture uploads persist apart from the text edits", () => {
  const st = fakeStorage();
  const a = new PictureEdits(st, "ru", "p");
  assert.ok(a.set("G10", UP));
  assert.deepEqual([...st.map.keys()], ["gen3tl-editor:pictures:ru:p"]);
  const b = new PictureEdits(st, "ru", "p");
  assert.equal(b.size, 1);
  assert.equal(b.get("G10").name, "G10.png");
  assert.equal(b.changes()[0].base, UP.base);
  assert.match(b.get("G10").time, /^\d{4}-\d\d-\d\dT/);
  assert.equal(new Edits(st, "ru", "p").size, 0);
  assert.equal(new PictureEdits(st, "de", "p").size, 0);
  assert.ok(b.remove("G10"));
  assert.equal(st.map.size, 0);
});

test("picture uploads survive broken storage for the session", () => {
  const broken = new PictureEdits(fakeStorage(true), "ru", "p");
  assert.equal(broken.set("G10", UP), false);
  assert.equal(broken.size, 1);
  const st = fakeStorage();
  st.setItem("gen3tl-editor:pictures:ru:p", "{not json");
  assert.equal(new PictureEdits(st, "ru", "p").size, 0);
});

test("the export lists the uploads in inventory order, without ROM art", () => {
  const a = new PictureEdits(fakeStorage(), "ru", "p");
  a.set("G16", { ...UP, areas: [[29, 7, 72, 1]], tiles: 210, name: "G16.png" });
  a.set("G10", UP);
  const pics = a.changes(["G01", "G10", "G16"]);
  assert.deepEqual(pics, [
    { id: "G10", base: UP.base, areas: [[8, 16, 15, 6]], tiles: null, png: UP.png },
    { id: "G16", base: UP.base, areas: [[29, 7, 72, 1]], tiles: 210, png: UP.png },
  ]);
  assert.deepEqual(exportDoc("ru", "p", [], pics).pictures, pics);
});

// Glyph art redrawn in the Fonts view
const BE = { id: "main:37", style: "main", code: "37", char: "Б", width: 3, rows: ["#s_", "s__"] };
const LV = { id: "main:34", style: "main", code: "34", slot: "034", width: 2, rows: ["#s", "s_"] };

test("glyph redraws persist by style and code, and export as the art file names them", () => {
  const st = fakeStorage();
  const a = new GlyphEdits(st, "ru", "p");
  assert.ok(a.set(BE, { width: 4, rows: ["##s_", "ss__"] }));
  assert.ok(a.set(LV, { width: 2, rows: ["#s", "ss"] }));
  assert.deepEqual([...st.map.keys()], ["gen3tl-editor:glyphs:ru:p"]);
  const b = new GlyphEdits(st, "ru", "p");
  assert.equal(b.size, 2);
  assert.deepEqual(b.of("main").map((e) => e.code), ["37", "34"]);
  assert.deepEqual(b.of("hud"), []);
  assert.deepEqual(b.changes(), [
    { style: "main", slot: "034", width: 2, rows: ["#s", "ss"], base: { width: 2, rows: ["#s", "s_"] } },
    { style: "main", char: "Б", width: 4, rows: ["##s_", "ss__"], base: { width: 3, rows: ["#s_", "s__"] } },
  ]);
  assert.deepEqual(exportDoc("ru", "p", [], [], [], b.changes()).glyphs, b.changes());
  b.set(LV, { width: 2, rows: ["#s", "s_"] }); // back to the pack's art
  assert.deepEqual(b.changes().map((g) => g.char), ["Б"]);
  b.remove("main:37");
  assert.equal(new GlyphEdits(st, "ru", "p").size, 0);
  assert.equal(new GlyphEdits(null, "ru", "p").set(BE, { width: 3, rows: ["###", "s__"] }), false); // memory only
});

test("a copied letter drawn anew keeps the byte it copied and exports it as its source", () => {
  const st = fakeStorage();
  const a = new GlyphEdits(st, "ru", "p");
  const A = { id: "main:87", style: "main", code: "87", char: "А", copy: "BB", width: 2, rows: ["#s", "s_"] };
  assert.ok(a.set(A, { width: 3, rows: ["#s_", "s__"] }));
  assert.deepEqual(new GlyphEdits(st, "ru", "p").changes(),
    [{ style: "main", char: "А", width: 3, rows: ["#s_", "s__"], base: { source: "copy:BB" } }]);
  a.set(A, { width: 2, rows: ["#s", "s_"] }); // the glyph it copies again
  assert.equal(a.size, 0);
});

const GYM = {
  id: "term:gym", category: "term", source_text: "Gym", translation: "гим", label: "", gender: "", declension: "auto",
  forms: { "nomn.sing": "гим", "gent.sing": "гима" }, sources: "", notes: "", status: "approved",
};

test("a term edit's record names the glossary columns it changes", () => {
  assert.equal(termRecord(GYM, { ...GYM }), null);
  assert.equal(termRecord(GYM, { ...GYM, forms: { "gent.sing": "гима", "nomn.sing": "гим" } }), null); // key order
  const row = { ...GYM, translation: "стадион", declension: "manual", notes: "Gym",
    forms: { "nomn.sing": "стадион", "gent.sing": "стадиона" } };
  assert.deepEqual(termRecord(GYM, row), {
    id: "term:gym", source_text: "Gym",
    base: { translation: "гим", declension: "auto", forms: GYM.forms, notes: "" },
    changes: { translation: "стадион", declension: "manual", forms: row.forms, notes: "Gym" },
  });
  assert.ok(sameField("label", undefined, ""));
  assert.ok(!sameField("forms", { a: "1" }, { a: "2" }));
});

test("missing migrated projections produce no phantom note changes", () => {
  const base = { id: "term:one", source_text: "Name", sources: "reference", notes: "First line.\nSecond line.",
    note_ids: ["note:one"] };
  const { notes, ...bare } = base;
  assert.equal(termRecord(base, bare), null);
  assert.equal(termRecord(bare, base), null);
  const edits = new TermEdits(fakeStorage(), "xx", "project");
  edits.set(base, { ...bare, sources: "context" });
  assert.deepEqual(edits.changes(), [{ id: base.id, source_text: base.source_text,
    base: { sources: "reference" }, changes: { sources: "context" } }]);
  edits.set(bare, bare);
  assert.equal(edits.size, 0);
  assert.deepEqual(termRecord({ ...base, note_ids: [] }, { ...bare, note_ids: [] }).changes, { notes: "" });
});

test("stale term drafts retain explicit notes and original non-note bases", () => {
  const storage = fakeStorage();
  const old = { id: "term:one", source_text: "Name", translation: "Old", sources: "old reference", notes: "Old note." };
  const edits = new TermEdits(storage, "xx", "project");
  edits.set(old, { ...old, translation: "New", notes: "Saved note edit.\nSecond line." });
  const saved = edits.changes();
  const loaded = new TermEdits(storage, "xx", "project");
  assert.deepEqual(loaded.changes(), saved);
  const { notes, ...current } = { ...old, translation: "Published", sources: "new reference", note_ids: ["note:one"] };
  loaded.set(current, { ...current, translation: "New" });
  assert.deepEqual(loaded.changes()[0].base, { translation: "Old", notes: "Old note.", sources: "old reference" });
  assert.deepEqual(loaded.changes()[0].changes,
    { translation: "New", notes: "Saved note edit.\nSecond line.", sources: "new reference" });
  assert.deepEqual(new TermEdits(storage, "xx", "project").changes(), loaded.changes());
});

test("term edits persist apart, keep their first base and export in glossary order", () => {
  const st = fakeStorage();
  const a = new TermEdits(st, "ru", "p");
  assert.ok(a.set(GYM, { ...GYM, notes: "x" }));
  assert.deepEqual([...st.map.keys()], ["gen3tl-editor:terms:ru:p"]);
  const newer = { ...GYM, notes: "changed in the data" };
  a.set(newer, { ...GYM, notes: "y" }); // the base of the first edit stays
  const b = new TermEdits(st, "ru", "p");
  assert.equal(b.size, 1);
  assert.deepEqual(b.get("term:gym").base, GYM);
  b.set(GYM, { ...GYM, sources: "s" });
  b.set({ ...GYM, id: "item:bag", source_text: "Bag" }, { ...GYM, id: "item:bag", source_text: "Bag", label: "Сум." });
  assert.deepEqual(b.changes(["item:bag", "term:gym"]).map((r) => [r.id, r.changes]),
    [["item:bag", { label: "Сум." }], ["term:gym", { sources: "s" }]]);
  b.set(GYM, { ...GYM }); // back to the data: no edit
  assert.equal(b.size, 1);
  assert.deepEqual(exportDoc("ru", "p", [], [], b.changes()).terms.map((r) => r.id), ["item:bag"]);
  assert.equal(new TermEdits(fakeStorage(true), "ru", "p").set(GYM, { ...GYM, notes: "z" }), false);
});

test("a term edit keeps and exports the entries its rename includes", () => {
  // form-only occurrences renamed only where included
  const row = { ...GYM, translation: "стадион" };
  assert.deepEqual(termRecord(GYM, row, ["s2", "s1", "s2"]).include, ["s1", "s2"]);
  assert.equal(termRecord(GYM, row).include, undefined);
  assert.equal(termRecord(GYM, { ...GYM }, ["s1"]), null); // no edit, nothing to include
  const st = fakeStorage();
  const a = new TermEdits(st, "ru", "p");
  assert.equal(a.setInclude("term:gym", ["s1"]), false); // only an edited term
  a.set(GYM, row);
  assert.ok(a.setInclude("term:gym", ["s3", "s1"]));
  a.set(GYM, { ...row, notes: "x" }); // a later edit keeps them
  const b = new TermEdits(st, "ru", "p");
  assert.deepEqual(b.include("term:gym"), ["s1", "s3"]);
  assert.deepEqual(b.changes()[0].include, ["s1", "s3"]);
  assert.deepEqual(b.include("item:bag"), []);
});



test("term drafts migrate target fields once without losing values", () => {
  const storage = fakeStorage(), key = "gen3tl-editor:terms:xx:p";
  const base = { id: "t", english: "Berry", russian: "fruit", forms: {} };
  const draft = { base, row: { ...base, russian: "berry" }, changes: { russian: "berry" },
    include: ["line"], time: "2026-01-01", preview: { old: { in_english: ["line"] } } };
  storage.setItem(key, JSON.stringify({ t: draft }));
  const edits = new TermEdits(storage, "xx", "p");
  assert.equal(edits.get("t").base.translation, "fruit");
  assert.equal(edits.get("t").row.translation, "berry");
  assert.deepEqual(edits.get("t").changes, { translation: "berry" });
  assert.deepEqual(edits.get("t").preview, { old: { in_source: ["line"] } });
  assert.deepEqual(edits.changes(), [{ id: "t", source_text: "Berry", base: { translation: "fruit" },
    changes: { translation: "berry" }, include: ["line"] }]);
  const saved = storage.getItem(key);
  assert.equal(saved.includes('"russian"'), false);
  assert.equal(saved.includes('"english"'), false);
  assert.deepEqual(new TermEdits(storage, "xx", "p").get("t"), edits.get("t"));
  assert.equal(storage.getItem(key), saved);
});


test("language-pack rows cannot create local drafts", () => {
  const edits = new Edits(fakeStorage(), "ru", "p");
  assert.throws(() => edits.set({ ...E1, owner: "keyboard" }, "changed", ""), /read-only/);
  assert.equal(edits.size, 0);
});

test("keyboard edits keep rows and the page order until they equal the data", () => {
  const store = new Map();
  const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v), removeItem: (k) => store.delete(k) };
  const edits = new KeyboardEdits(storage, "xx", "p");
  assert.equal(edits.changes(), null);
  edits.setRow("lower_keys_1", "xб", "аб", true);
  edits.setRow("lower_display_1", "{CLEAR 0B}x", "{CLEAR 0B}а");
  edits.setPages(["b", "a"], ["a", "b"]);
  assert.equal(edits.size, 2); // a keys row and the order
  assert.ok(store.has("gen3tl-editor:keyboard:xx:p"));
  const kept = new KeyboardEdits(storage, "xx", "p");
  assert.deepEqual(kept.changes(), {
    rows: { lower_keys_1: "xб", lower_display_1: "{CLEAR 0B}x" },
    base: { rows: { lower_keys_1: "аб", lower_display_1: "{CLEAR 0B}а" }, pages: ["a", "b"] }, pages: ["b", "a"],
  });
  kept.setRow("lower_keys_1", "yб", "xб", true); // the base stays the data's
  assert.equal(kept.changes().base.rows.lower_keys_1, "аб");
  kept.setRow("lower_keys_1", "аб", "yб", true);
  kept.setRow("lower_display_1", "{CLEAR 0B}а", "");
  kept.setPages(["a", "b"], ["b", "a"]);
  assert.equal(kept.size, 0);
  assert.equal(kept.changes(), null);
  assert.ok(!store.has("gen3tl-editor:keyboard:xx:p"));
});

test("catalog drafts retain all operand baselines across reload and subsequent grouped edits", () => {
  const storage = fakeStorage();
  const rows = [{ id: "sym:first", translation: "", note: "" }, { id: "sym:second", translation: "", note: "" }];
  const rule = { id: "group", capability_sha256: "a".repeat(64),
    operands: [{ id: "suffix", bindings: rows.map((row) => ({ entry: row.id })) }],
    sources: [{ entry: rows[0].id, state: "original" }, { entry: rows[1].id, state: "empty" }] };
  const index = { list: rows, engine_rules: { rules: [rule] } };
  const edits = new Edits(storage, "aa", "fixture", index);
  edits.set(rows[0], "", "", [], true, "empty");
  const expected = { capability_sha256: "a".repeat(64), entries: {
    "sym:first": { translation: "", state: "original" }, "sym:second": { translation: "", state: "empty" } } };
  assert.deepEqual(edits.get(rows[0].id).rule_baselines.group, expected);
  rule.capability_sha256 = "b".repeat(64);
  rows[1].translation = "z";
  const restored = new Edits(storage, "aa", "fixture", index);
  restored.setMany(rows.map((entry) => ({ entry, translation: "x", note: "" })));
  for (const row of rows) assert.deepEqual(restored.get(row.id).rule_baselines.group, expected);
  assert.ok(restored.changes().every((row) => !Object.hasOwn(row, "rule_baselines")));
  assert.equal(new Edits(storage, "bb", "fixture", index).size, 0);
  restored.removeMany(rows.map((row) => row.id));
  restored.set(rows[0], "x", "");
  assert.equal(restored.get(rows[0].id).rule_baselines.group.capability_sha256, "b".repeat(64));
});

test("removal proposals persist separately from discarding drafts and recover explicitly", () => {
  const storage = fakeStorage(), base = noteCollection().note[1], before = structuredClone(base);
  const drafts = new NoteEdits(storage, "xx", "project");
  drafts.proposeRemoval(base, "Obsolete guidance.");
  const restored = new NoteEdits(storage, "xx", "project");
  const change = restored.changes()[0];
  assert.equal(change.operation, "remove");
  assert.equal(change.proposed, null);
  assert.deepEqual(change.base, base);
  assert.deepEqual(change.subject, base.subject);
  assert.deepEqual(change.scope, base.scope);
  assert.deepEqual(change.binding, base.binding);
  assert.equal(change.base_revision, noteRevision(base));
  assert.equal(change.time, undefined);
  assert.equal(new NoteEdits(storage, "other", "project").changes().length, 0);
  assert.equal(restored.conflict(base.id, base, base.binding), false);
  const current = { ...base, guidance: ["Changed remotely."] };
  assert.equal(restored.conflict(base.id, current, base.binding), true);
  restored.rebase(base.id, current, current.binding);
  assert.equal(restored.changes()[0].operation, "remove");
  assert.deepEqual(restored.changes()[0].base, current);
  restored.remove(base.id);
  assert.deepEqual(restored.changes(), []);
  assert.deepEqual(base, before);
});

test("note exports include shared and selected language drafts without timestamps or aliasing", () => {
  const storage = fakeStorage(), base = noteCollection().note[1];
  const selected = new NoteEdits(storage, "xx", "project"), other = new NoteEdits(storage, "other", "project");
  selected.set(base.subject, base.binding, { scope: base.scope, guidance: ["Selected wording."] });
  selected.set(base.subject, base.binding, { scope: { kind: "shared" }, guidance: ["Shared guidance."] });
  other.set(base.subject, base.binding, { scope: { kind: "language", language: "other" }, guidance: ["Other wording."] });
  const changes = new NoteEdits(storage, "xx", "project").changes();
  assert.equal(changes.length, 2);
  assert.ok(changes.every((change) => change.operation === "add" && change.base === null));
  assert.ok(changes.every((change) => change.base_revision === "null" && change.time === undefined));
  const doc = exportDoc("xx", "project", [], [], [], [], null, [], changes);
  doc.note_changes[0].proposed.guidance[0] = "Changed export.";
  assert.notEqual(changes[0].proposed.guidance[0], "Changed export.");
  assert.deepEqual([doc.entries, doc.pictures, doc.terms, doc.glyphs], [[], [], [], []]);
  assert.equal(doc.keyboard, undefined);
});


test("entry drafts migrate source caches and persist once", () => {
  const storage = fakeStorage(), key = "gen3tl-editor:edits:xx:p";
  const original = { english: "Source", translation: "Draft", base: "Published", state: "text", note: "Review" };
  storage.setItem(key, JSON.stringify({ entry: original }));
  const edits = new Edits(storage, "xx", "p");
  assert.equal(edits.get("entry").source_text, "Source");
  assert.equal(edits.get("entry").translation, "Draft");
  assert.equal(edits.get("entry").base, "Published");
  const saved = storage.getItem(key);
  assert.equal(saved.includes('"english"'), false);
  assert.deepEqual(new Edits(storage, "xx", "p").get("entry"), edits.get("entry"));
  assert.equal(storage.getItem(key), saved);
});
