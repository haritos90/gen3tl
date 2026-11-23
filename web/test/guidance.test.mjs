import assert from "node:assert/strict";
import { test } from "node:test";
import { notesStatements, notesText, renderEntryNotes, renderGuidance, renderNoteDrafts, renderNotes } from "../js/guidance.js";
import { NoteEdits } from "../js/store.js";
import { setCatalog } from "../js/ui.js";
import { evidenceFile, fakeDocument, fakeStorage, noteCollection, stubPage } from "./fixture.mjs";

globalThis.document = fakeDocument();

test("note text preserves prose and nested metadata in projection order", () => {
  const collection = { schema: "3.0.0", note: [{
    id: "shared", subject: { kind: "graphic", id: "G01" }, scope: { kind: "shared" }, status: "unreviewed",
    reason: ["Keep the layout."], facts: ["Two tiles."], guidance: ["Retain spacing.", "Keep case."],
    reviewed_at: "2026-11-13", reviewed_by: "Reviewer", updated: "2026-11-12", created: "2026-11-11", author: "Author",
    related_subjects: [{ kind: "graphic", id: "G02" }], related_notes: ["reference", "another"],
    evidence: [{ method: "measurement", verification: "checked",
      locations: [{ kind: "rom_offset", value: 32 }],
      measurements: [{ name: "width", value: 12.5, unit: "px" }, { name: "offset", value: 0, unit: "px" }] }],
    sources: [{ name: "Manual", kind: "publication", address: "ref:manual", quote: "One.\nTwo.",
      verification: "recorded" }],
  }, { id: "local", subject: { kind: "graphic", id: "G01" }, scope: { kind: "language", language: "xx" },
    status: "accepted", facts: ["Local layout."] }] };
  const expected = ["Retain spacing.", "Keep case.", "Two tiles.", "Keep the layout.",
    "status: unreviewed; sources: name: Manual; kind: publication; address: ref:manual; quote: One.",
    "Two.; verification: recorded; evidence: method: measurement; verification: checked; locations: kind: rom_offset; value: 32; measurements: name: width; value: 12.5; unit: px",
    "name: offset; value: 0; unit: px; related notes: reference", "another; related subjects: kind: graphic; id: G02; author: Author; created: 2026-11-11; updated: 2026-11-12; reviewed by: Reviewer; reviewed at: 2026-11-13",
    "Local layout.", "status: accepted"].join("\n");
  assert.equal(notesText(["shared", "local"], collection, "xx"), expected);
  assert.equal(notesText(["local", "shared"], collection, "xx"),
    "Local layout.\nstatus: accepted\n" + expected.split("\nLocal layout.")[0]);
  assert.equal(notesText(["local"], collection, "yy"), "");
  assert.equal(notesText(["missing"], collection, "xx"), null);
  assert.equal(notesText([], collection, "xx"), null);
  assert.equal(notesText(["shared"], undefined, "xx"), null);
  assert.equal(notesText(["shared"], { schema: "1.0.0" }, "xx"), null);
});

test("research summaries replace copied facts in display and search only", (context) => {
  stubPage(context, "editor");
  const note = { id: "term:reference", subject: { kind: "term", id: "term:one" },
    scope: { kind: "language", language: "xx" }, status: "unreviewed",
    evidence: [{ method: "research-assembly", verification: "recorded" }],
    related_subjects: [{ kind: "research", id: "finding:one" }] };
  const facts = ["role: Recorded role", "evidence: Recorded evidence", "Independent fact."];
  const before = { schema: "3.0.0", note: [{ ...note, facts }] };
  const after = { schema: "3.0.0", note: [{ ...note, facts: ["Independent fact."] }], summaries: { [note.id]: facts } };
  const saved = structuredClone(after);
  assert.equal(notesText([note.id], after, "xx"), notesText([note.id], before, "xx"));
  assert.equal(notesStatements([note.id], after, "xx"), facts.join("\n"));
  assert.equal(renderNotes([note.id], after, "xx").textContent, renderNotes([note.id], before, "xx").textContent);
  assert.equal(notesText([note.id], after, "yy"), "");
  assert.deepEqual(after, saved);
  after.note[0].status = "proposed";
  assert.equal(notesText([note.id], after, "xx"), "");
});

test("guidance is visible with evidence and a distinct heading", () => {
  const view = renderGuidance([{ text: "PLAYER names the finder.", evidence: "Acquisition script." }]);
  assert.equal(view.tagName, "SECTION");
  assert.equal(view.firstChild.textContent, "Translator notes");
  assert.match(view.textContent, /PLAYER names the finder/);
  assert.match(view.textContent, /Evidence: Acquisition script/);
  assert.equal(view.childNodes.length, 2);
});

test("statement summaries distinguish unavailable notes from notes without active statements", () => {
  const collection = noteCollection();
  assert.equal(notesStatements(["note:shared"], collection, "xx"),
    "Preserve the placeholder.\nKeep line groups.\nThe name varies.\nThe slot is shared.");
  assert.equal(notesStatements(["note:wording", "note:research"], collection, "xx"), "");
  assert.equal(notesStatements(["missing"], collection, "xx"), null);
  assert.equal(notesStatements([], collection, "xx"), null);
  assert.equal(notesStatements(["note:shared"], undefined, "xx"), null);
  assert.equal(notesStatements(["note:shared"], { schema: "1.0.0" }, "xx"), null);
  const empty = { ...collection.note[1], status: "accepted", guidance: [] };
  assert.equal(notesStatements([empty.id], { schema: "3.0.0", note: [empty] }, "xx"), "");
});

test("note markup remains literal text", () => {
  const text = '<img src=x onerror="alert(1)">';
  const view = renderGuidance([{ text, evidence: "<script>bad()</script>" }]);
  assert.match(view.textContent, /<img src=x/);
  assert.match(view.textContent, /<script>bad\(\)<\/script>/);
  const tags = (node) => [node.tagName, ...(node.childNodes ?? []).flatMap(tags)].filter(Boolean);
  assert.ok(!tags(view).includes("IMG"));
  assert.ok(!tags(view).includes("SCRIPT"));
});

test("entries without guidance need no extra panel", () => {
  assert.equal(renderGuidance(), null);
  assert.equal(renderGuidance([]), null);
});

const descendants = (node) => [node, ...(node.childNodes ?? []).flatMap(descendants)];
const click = (node, text) => descendants(node).find((child) => child.tagName === "BUTTON" && child.textContent === text).listeners.click();
const input = (node, label, value, event = "input") => {
  const control = descendants(node).find((child) => child.tagName === "LABEL" && child.firstChild.textContent === label).childNodes.at(-1);
  control.value = value;
  control.listeners[event]({ target: control });
};

test("entry draft controls add, edit, reload, select scope and revert independently", (context) => {
  stubPage(context, "editor");
  const template = noteCollection().note[1], storage = fakeStorage(), index = { language: template.scope.language };
  const edits = new NoteEdits(storage, index.language, "project");
  const view = renderNoteDrafts(template.subject, template.binding, index, edits, [index.language, "other"]);
  click(view, "Add note");
  input(view, "Guidance", "Instruction.\nKeep this together.");
  input(view, "Message to reviewers", "Check the citation.");
  input(view, "Note scope", "shared", "change");
  const draft = edits.forSubject(template.subject)[0];
  assert.deepEqual(draft.note.guidance, ["Instruction.\nKeep this together."]);
  assert.equal(draft.message, "Check the citation.");
  assert.deepEqual(draft.note.scope, { kind: "shared" });
  assert.ok(view.textContent.includes(`all language projects: ${index.language}, other`));
  assert.ok(view.textContent.includes("Note draft saved in this browser."));
  const restored = renderNoteDrafts(template.subject, template.binding, index, new NoteEdits(storage, index.language, "project"));
  const guidance = descendants(restored).find((child) => child.tagName === "TEXTAREA");
  assert.equal(guidance.value, "Instruction.\nKeep this together.");
  click(view, "Revert note draft");
  assert.equal(edits.size, 0);
  assert.ok(!view.textContent.includes("Draft note"));
});

test("unsupported term claims stay drafts with separate citation fields", (context) => {
  stubPage(context, "editor");
  const template = noteCollection("term").note[1], index = { language: template.scope.language };
  const edits = new NoteEdits(fakeStorage(), index.language, "project");
  const view = renderNoteDrafts(template.subject, null, index, edits);
  click(view, "Add note");
  input(view, "Facts", "The reference uses this spelling.");
  assert.match(view.textContent, /Term claims need/);
  click(view, "Add source");
  input(view, "Name", "Reference");
  input(view, "Address", "https://example.test/source");
  input(view, "Quoted term", "Exact spelling");
  input(view, "Locator", "Page 12");
  assert.match(view.textContent, /retrieval date/);
  input(view, "Retrieved", "2026-01-01");
  assert.doesNotMatch(view.textContent, /Term claims need|retrieval date/);
  const draft = edits.forSubject(template.subject)[0];
  assert.equal(draft.note.status, "proposed");
  assert.deepEqual(draft.note.sources, [{ kind: "web", name: "Reference", address: "https://example.test/source",
    quoted_term: "Exact spelling", locator: "Page 12", retrieved: "2026-01-01", verification: "unresolved" }]);
  click(view, "Remove source");
  assert.match(view.textContent, /Term claims need/);
  assert.equal(edits.size, 1);
});

test("conflict recovery displays base, current and proposed notes without replacing guidance", (context) => {
  stubPage(context, "editor");
  const collection = noteCollection(), note = collection.note[1], edits = new NoteEdits(fakeStorage(), note.scope.language, "project");
  edits.set(note.subject, note.binding, { ...note, guidance: ["Local suggestion."] }, note);
  const current = { ...note, guidance: ["Published correction."] };
  collection.note[1] = current;
  const before = structuredClone(collection);
  const view = renderNoteDrafts(note.subject, note.binding, { notes: collection, language: note.scope.language }, edits);
  for (const text of ["Saved base", "Current note", "Your draft", "Published correction.", "Local suggestion."]) {
    assert.ok(view.textContent.includes(text), text);
  }
  click(view, "Keep draft against current version");
  assert.equal(edits.conflict(note.id, current, note.binding), false);
  assert.deepEqual(edits.get(note.id).note.guidance, ["Local suggestion."]);
  assert.deepEqual(collection, before);
  assert.doesNotMatch(view.textContent, /Compare the versions/);
  assert.ok(!descendants(view).some((node) => node.tagName === "STYLE" || node.getAttribute?.("style")));
});

test("draft forms explain missing bindings and unavailable storage", (context) => {
  stubPage(context, "editor");
  const note = noteCollection().note[1], edits = new NoteEdits(null, note.scope.language, "project");
  const index = { language: note.scope.language };
  const missing = renderNoteDrafts(note.subject, null, index, edits);
  assert.equal(descendants(missing).find((node) => node.tagName === "BUTTON").disabled, true);
  const view = renderNoteDrafts(note.subject, note.binding, index, edits);
  click(view, "Add note");
  input(view, "Guidance", "Unsaved instruction.");
  assert.match(view.textContent, /Storage is unavailable/);
  assert.equal(edits.size, 1);
});

test("a draft moving to shared scope never treats an unloaded language note as deleted", (context) => {
  stubPage(context, "editor");
  const note = noteCollection().note[1], storage = fakeStorage();
  const edits = new NoteEdits(storage, note.scope.language, "project");
  edits.set(note.subject, note.binding, { ...note, scope: { kind: "shared" } }, note);
  const view = renderNoteDrafts(note.subject, note.binding, { language: "other" }, new NoteEdits(storage, "other", "project"));
  assert.ok(view.textContent.includes(`Open the original language to compare this note: ${note.scope.language}`));
  assert.ok(!descendants(view).some((node) => node.tagName === "BUTTON" && node.textContent === "Keep draft against current version"));
  assert.equal(edits.get(note.id).base.id, note.id);
});

test("picture note forms attach captures, edit metadata and retain evidence through text edits", async (context) => {
  stubPage(context, "editor");
  const notes = noteCollection("graphic", "G01"), note = notes.note[1], before = structuredClone(notes);
  const storage = fakeStorage(), edits = new NoteEdits(storage, "xx", "project");
  const index = { language: "xx", notes };
  const view = renderNoteDrafts(note.subject, note.binding, index, edits, ["xx", "yy"]);
  const panel = () => descendants(view).find((node) => node.getAttribute?.("data-key") === note.id);
  const upload = async (role, build) => {
    input(panel(), "Version label", role, "change");
    input(panel(), "Build identity", build);
    input(panel(), "Screen context", "Menu, page 1");
    const control = descendants(panel()).find((node) => node.tagName === "INPUT" && node.getAttribute("type") === "file");
    await control.listeners.change({ target: { files: [evidenceFile()], value: "capture.png" } });
  };
  await upload("original", "1.0.0");
  assert.equal(edits.get(note.id).attachments.length, 1);
  input(panel(), "Guidance", "Keep the label readable.");
  input(panel(), "Message to reviewers", "Compare the menu.");
  assert.equal(edits.get(note.id).attachments.length, 1);
  input(panel(), "Build identity", "1.0.1");
  assert.equal(edits.get(note.id).attachments[0].build, "1.0.1");
  assert.match(edits.get(note.id).note.evidence[0].captures[0].locator, /1.0.1/);
  const restored = renderNoteDrafts(note.subject, note.binding, index, new NoteEdits(storage, "xx", "project"));
  assert.match(restored.textContent, /Original · 1.0.1 · Menu, page 1/);
  assert.match(restored.textContent, /SHA-256/);
  assert.equal(descendants(restored).filter((node) => node.tagName === "IMG").length, 1);
  assert.ok(!descendants(restored).some((node) => node.tagName === "STYLE" || node.getAttribute?.("style")));
  click(panel(), "Remove evidence attachment");
  assert.equal(edits.get(note.id).attachments, undefined);
  assert.equal(edits.get(note.id).message, "Compare the menu.");
  click(panel(), "Revert note draft");
  assert.equal(edits.size, 0);
  assert.deepEqual(notes, before);
});

test("invalid capture uploads show a readable error without changing notes", async (context) => {
  stubPage(context, "editor");
  const note = noteCollection("graphic", "G01").note[1], edits = new NoteEdits(fakeStorage(), "xx", "project");
  const view = renderNoteDrafts(note.subject, note.binding, { language: "xx" }, edits);
  click(view, "Add note");
  const control = descendants(view).find((node) => node.tagName === "INPUT" && node.getAttribute("type") === "file");
  await control.listeners.change({ target: { files: [{ size: 6 * 1024 * 1024 }], value: "" } });
  assert.match(view.textContent, /Evidence images must be at most 3 MB/);
  assert.equal(edits.forSubject(note.subject)[0].attachments, undefined);
});

test("structured notes keep fields, provenance, verification and dates distinct", (context) => {
  stubPage(context, "editor");
  const collection = noteCollection(), before = structuredClone(collection);
  const view = renderNotes(["note:shared", "note:wording", "note:shared"], collection, "xx");
  const nodes = descendants(view), text = view.textContent;
  for (const value of ["Shared game notes", "Language: xx", "Review: Unreviewed migration", "Review: Proposed",
    "Guidance", "Facts", "Reason", "Sources", "Evidence", "Verification: Recorded", "Verification: Checked",
    "Verification: Unresolved", "Retrieved: not recorded", "Retrieved: 2026-10-18", "Locator: Section 4",
    "Quoted term: Exact term", "Quotation: Exact quotation.\nSecond line.", "ROM offset: Text: 0x00000020",
    "CPU address: 0x08000020", "Symbol: NAME_SLOT", "File: reference.txt", "Width: 12 px", "Occurrences: 0 count",
    "Capture: https://example.test/capture.png", "Original, 1.0.0", "SHA-256: " + "b".repeat(64),
    "Related subject: Entry: sym:two", "Subject: Research: not recorded", "Related attestation.",
    "Author: Contributor", "Created: 2026-11-11", "Reviewed by: Reviewer", "Reviewed at: 2026-11-11T16:20:00Z"]) {
    assert.ok(text.includes(value), value);
  }
  assert.equal(nodes.filter((node) => node.tagName === "ARTICLE").length, 3);
  assert.equal(text.split("Related attestation.").length, 5);
  const evidence = nodes.find((node) => node.tagName === "DETAILS" && node.firstChild.textContent === "Evidence");
  assert.ok(evidence);
  assert.ok(!evidence.open);
  assert.ok(!nodes.some((node) => node.tagName === "STYLE" || node.getAttribute?.("style")));
  assert.deepEqual(collection, before);
});

test("only safe web references become links and all prose remains text", (context) => {
  stubPage(context, "editor");
  const collection = noteCollection();
  const note = collection.note[0];
  note.guidance = ['<img src=x onerror="alert(1)">'];
  note.sources = ["javascript:alert(1)", "data:text/html,<script>bad()</script>", "file:///secret", "//example.test/path",
    "https://example.test/\"?q=<script>", "http://example.test/path"].map((address) => ({
    name: "<script>bad()</script>", address, kind: "web", verification: "unresolved",
  }));
  note.evidence[0].captures = [{ address: "javascript:alert(2)" }];
  const view = renderNotes([note.id], collection, "xx"), nodes = descendants(view);
  assert.equal(nodes.filter((node) => node.tagName === "A").length, 2);
  assert.ok(!nodes.some((node) => ["SCRIPT", "IMG", "IFRAME"].includes(node.tagName)));
  assert.match(view.textContent, /<img src=x/);
  for (const link of nodes.filter((node) => node.tagName === "A")) {
    assert.match(link.getAttribute("href"), /^https?:/);
    assert.equal(link.getAttribute("rel"), "noopener noreferrer");
  }
});

test("scope and review status do not confer approval or invent attribution", (context) => {
  stubPage(context, "editor");
  for (const [status, label] of [["unreviewed", "Unreviewed migration"], ["proposed", "Proposed"],
    ["accepted", "Accepted"], ["needs_review", "Needs review"], ["superseded", "Superseded"]]) {
    const collection = noteCollection();
    collection.note[0].status = status;
    const view = renderNotes(["note:shared", "note:wording"], collection, "yy");
    assert.ok(view.textContent.includes(`Review: ${label}`));
    assert.ok(view.textContent.includes("Preserve the placeholder."));
    assert.ok(!view.textContent.includes("Proposed wording."));
    assert.ok(!view.textContent.includes("Related attestation."));
    assert.ok(!view.textContent.includes("Author:"));
    assert.ok(!view.textContent.includes("Created:"));
  }
  assert.equal(renderNotes([], noteCollection(), "xx"), null);
  assert.equal(renderNotes(["missing"], noteCollection(), "xx"), null);
  assert.equal(renderNotes(["note:shared"], undefined, "xx"), null);
  assert.equal(renderNotes(["note:shared"], { schema: "1.0.0" }, "xx"), null);
});

test("entry compatibility preserves unmigrated notes without duplicate projections", (context) => {
  stubPage(context, "editor");
  const collection = noteCollection();
  const entry = { translator_notes: [{ text: "Legacy instruction.", evidence: "Legacy evidence." }], note: "Legacy source." };
  const legacy = renderEntryNotes(entry, {});
  for (const text of ["Legacy instruction.", "Legacy evidence.", "Legacy source."]) assert.ok(legacy.textContent.includes(text));
  const shared = renderEntryNotes({ ...entry, note_ids: ["note:shared"] }, { notes: collection, language: "xx" });
  assert.ok(shared.textContent.includes("Legacy source."));
  assert.ok(!shared.textContent.includes("Legacy instruction."));
  const wording = renderEntryNotes({ ...entry, note_ids: ["note:wording"] }, { notes: collection, language: "xx" });
  assert.ok(wording.textContent.includes("Legacy instruction."));
  assert.ok(!wording.textContent.includes("Legacy source."));
  const { note, ...withoutSnapshot } = entry;
  const missing = renderEntryNotes({ ...withoutSnapshot, note_ids: ["note:wording"] }, { notes: collection, language: "xx" });
  assert.equal(missing.textContent, wording.textContent);
  const index = { notes: collection, language: "xx" };
  const migrated = { ...entry, note_ids: ["note:shared", "note:wording"] };
  const { translator_notes, ...sharedRemoved } = migrated;
  const { note: snapshot, ...bothRemoved } = sharedRemoved;
  assert.equal(renderEntryNotes(migrated, index).textContent, renderEntryNotes(bothRemoved, index).textContent);
  assert.equal(renderEntryNotes({ ...entry, note_ids: ["note:shared"] }, index).textContent,
    renderEntryNotes({ note: entry.note, note_ids: ["note:shared"] }, index).textContent);
});

test("note labels follow the interface catalog without translating note content", (context) => {
  stubPage(context, "editor");
  setCatalog({ "Guidance": "Instructions", "Review: {status}": "State: {status}", "Proposed": "Pending" });
  context.after(() => setCatalog(null));
  const view = renderNotes(["note:wording"], noteCollection(), "xx");
  assert.ok(view.textContent.includes("Instructions"));
  assert.ok(view.textContent.includes("State: Pending"));
  assert.ok(view.textContent.includes("Proposed wording."));
});

test("related note references show subjects and shortened statements without note ids", (context) => {
  stubPage(context, "editor");
  const collection = noteCollection();
  collection.note[2].facts = ["A related finding. ".repeat(30), "Another finding."];
  collection.note[0].related_notes.push("note:missing", "note:foreign", "note:subject");
  collection.note.push({ ...collection.note[2], id: "note:foreign", scope: { kind: "language", language: "yy" },
    facts: ["Other language finding."] },
  { ...collection.note[2], id: "note:subject", subject: { kind: "entry", id: "hash:12345678" }, facts: [] });
  const before = structuredClone(collection);
  const view = renderNotes(["note:shared"], collection, "xx");
  const reference = descendants(view).find((node) => node.tagName === "SPAN" && node.textContent.startsWith("Research:"));
  assert.match(reference.textContent, /^Research: not recorded · A related finding/);
  assert.ok(reference.textContent.endsWith("… Copy reference"));
  assert.ok(reference.textContent.length < 200);
  assert.ok(!reference.textContent.includes("Another finding."));
  assert.ok(view.textContent.includes("Related note: Entry: hash:12345678"));
  assert.ok(view.textContent.includes("Related note: Note unavailable"));
  assert.doesNotMatch(view.textContent, /\bnote:\S|Other language finding/);
  assert.deepEqual(collection, before);
});

test("wording rule subjects resolve in notes, evidence and draft conflict panels", (context) => {
  stubPage(context, "editor");
  const collection = noteCollection(), note = collection.note[1];
  const rule = { ...noteCollection("wording_rule", "rule:one").note[1], id: "note:rule", guidance: ["Use the complete wording."] };
  collection.note.push(rule);
  collection.note[0].related_subjects.push(rule.subject, { kind: "wording_rule", id: "rule:missing" });
  collection.note[0].evidence[0].related_subjects.push(rule.subject);
  note.related_notes = [rule.id];
  note.evidence = collection.note[0].evidence;
  collection.note[2].related_subjects = [rule.subject];
  const index = { notes: collection, language: "xx" };
  const edits = new NoteEdits(fakeStorage(), "xx", "project");
  edits.set(note.subject, note.binding, { ...note, guidance: ["Local correction."] }, note);
  collection.note[1] = { ...note, guidance: ["Published correction."] };
  const before = structuredClone(collection);
  for (const view of [renderNotes(["note:shared", rule.id], collection, "xx"),
    renderNoteDrafts(note.subject, note.binding, index, edits)]) {
    assert.ok(view.textContent.includes("Wording rule: Use the complete wording."));
    assert.doesNotMatch(view.textContent, /\b(?:note|rule):\S/);
  }
  const view = renderNotes(["note:shared"], collection, "xx");
  assert.ok(view.textContent.includes("Related subject: Wording rule: not recorded"));
  assert.deepEqual(collection, before);
});

test("reference and proposal labels follow the interface language in every subject panel", (context) => {
  stubPage(context, "editor");
  setCatalog({ "Related note": "Linked finding", "Research": "Study", "Wording rule": "Wording instruction",
    "Suggest edit": "Propose correction", "Draft note": "Draft finding", "Note unavailable": "Finding unavailable" });
  context.after(() => setCatalog(null));
  for (const kind of ["entry", "term", "graphic"]) {
    const collection = noteCollection(kind);
    const rule = { ...collection.note[1], id: "note:rule", subject: { kind: "wording_rule", id: "rule:one" } };
    collection.note.push(rule);
    collection.note[0].related_subjects.push(rule.subject);
    collection.note[0].related_notes.push("note:missing");
    const note = collection.note[1], index = { notes: collection, language: "xx" };
    const edits = new NoteEdits(fakeStorage(), "xx", "project");
    const view = renderNotes(["note:shared"], collection, "xx");
    assert.ok(view.textContent.includes("Linked finding: Study: not recorded · Related attestation."));
    assert.ok(view.textContent.includes("Linked finding: Finding unavailable"));
    assert.ok(view.textContent.includes("Wording instruction: Proposed wording."));
    const proposals = renderNoteDrafts(note.subject, note.binding, index, edits);
    assert.ok(proposals.textContent.includes("Propose correction"));
    assert.doesNotMatch(proposals.textContent, /\b(?:note|rule):\S/);
    edits.set(note.subject, note.binding, { ...note, guidance: ["Local correction."] }, note);
    const drafts = renderNoteDrafts(note.subject, note.binding, index, edits);
    assert.ok(drafts.textContent.includes("Draft finding"));
    assert.doesNotMatch(drafts.textContent, /\b(?:note|rule):\S/);
  }
});

test("research references copy identifiers and offer a copy field when the clipboard fails", async (context) => {
  stubPage(context, "editor");
  const collection = noteCollection(), copied = [];
  const saved = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  Object.defineProperty(globalThis, "navigator", { configurable: true,
    value: { clipboard: { writeText: async (id) => copied.push(id) } } });
  context.after(() => {
    if (saved) Object.defineProperty(globalThis, "navigator", saved);
    else delete globalThis.navigator;
  });
  const view = renderNotes(["note:shared"], collection, "xx");
  const buttons = descendants(view).filter((node) => node.tagName === "BUTTON" && node.textContent === "Copy reference");
  await buttons[0].listeners.click();
  await buttons[1].listeners.click();
  assert.deepEqual(copied, ["research:one", "note:research"]);
  assert.equal(buttons[0].textContent, "Reference copied");
  navigator.clipboard.writeText = async () => { throw new Error("Clipboard unavailable"); };
  const create = document.createElement, selected = [];
  document.createElement = (tag) => Object.assign(create(tag), { readOnly: false, select() { selected.push(this.value); } });
  await buttons[2].listeners.click();
  const input = descendants(view).find((node) => node.tagName === "INPUT");
  assert.equal(input.value, "research:one");
  assert.equal(input.readOnly, true);
  assert.equal(input.getAttribute("aria-label"), "Copy this reference");
  assert.deepEqual(selected, ["research:one"]);
  assert.doesNotMatch(view.textContent, /\b(?:research|note):\S/);
});

test("research source addresses resolve alone and missing or foreign findings stay unnamed", (context) => {
  stubPage(context, "editor");
  const collection = noteCollection(), note = collection.note[0];
  note.related_notes = [];
  note.related_subjects = [];
  note.sources = ["research:one", "research:missing", "research:foreign"].map((address) => ({
    kind: "project", name: "Research table", address, verification: "recorded",
  }));
  collection.note.push({ ...collection.note[2], id: "note:foreign", subject: { kind: "research", id: "research:foreign" },
    scope: { kind: "language", language: "yy" }, facts: ["Foreign statement."] });
  const view = renderNotes([note.id], collection, "xx", { "research:one": { table: "terms.tsv", name: "Twin Tap" } });
  assert.ok(view.textContent.includes("Address: Research: terms.tsv · Twin Tap · Related attestation."));
  assert.ok(view.textContent.includes("Address: Research: not recorded"));
  assert.equal(descendants(view).filter((node) => node.tagName === "ARTICLE").length, 2);
  assert.doesNotMatch(view.textContent, /\bresearch:\S|Foreign statement/);
});

test("published notes can propose removal and discard it without deleting guidance", (context) => {
  stubPage(context, "editor");
  const base = noteCollection().note[1], before = structuredClone(base);
  const index = { language: "xx", notes: { schema: "3.0.0", note: [base] } };
  const edits = new NoteEdits(fakeStorage(), "xx", "project");
  const view = renderNoteDrafts(base.subject, base.binding, index, edits);
  click(view, "Suggest removal");
  assert.match(view.textContent, /Removal proposed/);
  input(view, "Message to reviewers", "No longer applicable.");
  assert.equal(edits.changes()[0].operation, "remove");
  assert.equal(edits.changes()[0].message, "No longer applicable.");
  assert.equal(edits.changes()[0].proposed, null);
  click(view, "Revert note draft");
  assert.equal(edits.size, 0);
  assert.match(view.textContent, /Suggest removal/);
  assert.deepEqual(base, before);
});
