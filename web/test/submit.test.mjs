// Submission as a prefilled GitHub issue.
import assert from "node:assert/strict";
import { test } from "node:test";

import { TOOLKIT_REPOSITORY, readRegistry } from "../js/registry.js";
import { NoteEdits, exportDoc, noteRevision } from "../js/store.js";
import { LINK_LIMIT, TEMPLATE, issueLink, submissionDoc, submissionRepository, submissionParts, submissionProblem, submitAvailable, submitChanges } from "../js/submit.js";

import { evidenceFile, fakeStorage, noteCollection } from "./fixture.mjs";

const REPO = "example-owner/example-repo";
const changes = [{ id: "s0000100", translation: "Да", note: "" }, { id: "s0000200", translation: "Нет", note: "game" }];
const doc = exportDoc("xx", "test-1.0", changes);
const bases = { s0000100: "Прежний", s0000200: "Другой" };

function harness(digests = {}) {
  const calls = { open: [], download: [], picture: [] };
  return {
    calls,
    hooks: {
      base: (id) => bases[id],
      sha256: (id) => digests[id],
      download: (d) => calls.download.push(d) && "gen3tl-xx-edits.json",
      picture: (id) => calls.picture.push(id) && `${id}.png`,
      open: (url) => calls.open.push(url),
    },
  };
}

test("the submission holds the text entries with their base", () => {
  const sub = submissionDoc(doc, (id) => bases[id]);
  assert.deepEqual(sub, {
    version: "4.0.0", language: "xx", profile: "test-1.0", entries: [
      { id: "s0000100", base: "Прежний", translation: "Да" },
      { id: "s0000200", base: "Другой", translation: "Нет", note: "game" },
    ],
  });
  assert.deepEqual(submissionDoc(doc).entries[0], { id: "s0000100", translation: "Да" });
});

test("entry exports accept absent notes and retain old notes as review context", () => {
  for (const context of [{}, { note: "Historical context" }]) {
    const entry = { id: "entry", base: "Old", base_state: "text", translation: "Draft", state: "text", ...context };
    const exported = JSON.parse(JSON.stringify(exportDoc("xx", "fixture", [entry])));
    const submitted = submissionDoc(exported);
    assert.deepEqual(submitted.entries, [entry]);
    assert.equal(submitted.note_changes, undefined);
    assert.deepEqual(exported.entries, [entry]);
  }
});

test("a short submission travels in the export field of the form link", () => {
  const sub = submissionDoc(doc, (id) => bases[id]);
  const link = issueLink(sub, REPO);
  assert.equal(link.attach, false);
  const url = new URL(link.url);
  assert.equal(`${url.origin}${url.pathname}`, `https://github.com/${REPO}/issues/new`);
  assert.equal(url.searchParams.get("template"), TEMPLATE);
  assert.equal(url.searchParams.get("title"), "Editor submission: test-1.0/xx, 2 entries");
  assert.deepEqual(JSON.parse(url.searchParams.get("export")), sub);
  assert.ok(link.url.length <= LINK_LIMIT);
});

test("a long submission opens the empty form and is downloaded for attaching", () => {
  const long = exportDoc("xx", "test-1.0", Array.from({ length: 200 }, (_, i) => ({
    id: `s${String(i).padStart(7, "0")}`, translation: "Длинная строка перевода", note: "" })));
  const { calls, hooks } = harness();
  const res = submitChanges(long, REPO, hooks);
  assert.equal(res.ok, true);
  assert.equal(res.attach, true);
  assert.equal(calls.download.length, 1);
  assert.equal(calls.download[0].entries.length, 200);
  const url = new URL(calls.open[0]);
  assert.equal(url.searchParams.get("export"), null);
  assert.equal(url.searchParams.get("template"), TEMPLATE);
  assert.match(res.message, /attach the downloaded gen3tl-xx-edits\.json to the Export file field/);
});

test("submitting opens the link with the term edits; pictures without a digest stay with the export", () => {
  const { calls, hooks } = harness();
  const term = { id: "t", base: { translation: "Зелье" }, changes: { translation: "Снадобье" } };
  const res = submitChanges(exportDoc("xx", "test-1.0", changes, [{ id: "G01" }], [term]), REPO, hooks);
  assert.equal(res.ok, true);
  assert.equal(calls.download.length, 0);
  assert.equal(calls.open.length, 1);
  const url = new URL(calls.open[0]);
  const sub = JSON.parse(url.searchParams.get("export"));
  assert.deepEqual(Object.keys(sub), ["version", "language", "profile", "entries", "terms"]);
  assert.deepEqual(sub.terms, [term]);
  assert.equal(url.searchParams.get("title"), "Editor submission: test-1.0/xx, 2 entries, 1 term");
  assert.match(res.message, /opened with 3 changes: .* The pictures travel in the JSON file\.$/);
  assert.doesNotMatch(res.message, /glyph/);
  const terms = submitChanges(exportDoc("xx", "test-1.0", [], [], [term]), REPO, hooks);
  assert.equal(terms.ok, true);
  assert.equal(new URL(calls.open[1]).searchParams.get("title"), "Editor submission: test-1.0/xx, 1 term");
  const glyphs = submitChanges(exportDoc("xx", "test-1.0", changes, [], [], [{ style: "main", char: "Б" }]), REPO, hooks);
  assert.match(glyphs.message, /\.$/);
  assert.match(glyphs.message, / The glyph redraws travel in the JSON file\.$/);
  assert.deepEqual(Object.keys(JSON.parse(new URL(calls.open[2]).searchParams.get("export"))),
    ["version", "language", "profile", "entries"]);
});

test("a picture travels by its SHA-256 and its PNG is downloaded for attaching", () => {
  const sha = "ab".repeat(32);
  const { calls, hooks } = harness({ G03: sha });
  const pictures = [{ id: "G03", base: null, areas: [[0, 0, 64, 8]], tiles: null, png: "AAAA" },
    { id: "G16", base: "ef".repeat(32), areas: [], tiles: 210, png: "BBBB" }];
  const res = submitChanges(exportDoc("xx", "test-1.0", [], pictures), REPO, hooks);
  assert.equal(res.ok, true);
  const url = new URL(calls.open[0]);
  assert.deepEqual(JSON.parse(url.searchParams.get("export")).pictures, [{ id: "G03", base: null, areas: [[0, 0, 64, 8]], sha256: sha }]);
  assert.equal(url.searchParams.get("title"), "Editor submission: test-1.0/xx, 1 picture");
  assert.deepEqual(calls.picture, ["G03"]);
  assert.match(res.message, /opened with 1 change: .* Before submitting, attach the downloaded G03\.png to the Pictures field: the issue states the SHA-256 of each\. The pictures travel in the JSON file\.$/);
  const both = harness({ G03: sha, G16: "cd".repeat(32) });
  submitChanges(exportDoc("xx", "test-1.0", changes, pictures), REPO, both.hooks);
  assert.deepEqual(JSON.parse(new URL(both.calls.open[0]).searchParams.get("export")).pictures[1],
    { id: "G16", base: "ef".repeat(32), areas: [], tiles: 210, sha256: "cd".repeat(32) });
  assert.deepEqual(both.calls.picture, ["G03", "G16"]);
});

test("no repository or no text change gives a reason and opens nothing", () => {
  const { calls, hooks } = harness();
  assert.equal(submitChanges(doc, null, hooks).ok, false);
  assert.match(submitChanges(doc, "not a repository", hooks).reason, /names no repository/);
  assert.equal(submitChanges(exportDoc("xx", "test-1.0", [], [{ id: "G01" }]), REPO, hooks).reason,
    "No text, term, picture, glyph, keyboard, engine rule or note changes to submit.");
  assert.deepEqual(calls, { open: [], download: [], picture: [] });
  assert.equal(submitAvailable(REPO), true);
  assert.equal(submitAvailable("owner/.."), false);
});

test("the repository follows the export kind", () => {
  assert.equal(submissionRepository(exportDoc("xx", "site", changes), REPO), TOOLKIT_REPOSITORY);
  const glyphs = exportDoc("xx", "test-1.0", [], [], [], [{ style: "main", char: "Б" }]);
  assert.equal(submissionRepository(glyphs, REPO), TOOLKIT_REPOSITORY);
  assert.equal(submissionRepository(exportDoc("xx", "test-1.0", changes, [], [], [{ style: "main", char: "Б" }]), REPO),
    REPO);
  assert.equal(submissionRepository(doc, REPO), REPO);
});

test("a site export opens the toolkit issue form", () => {
  const { calls, hooks } = harness();
  const res = submitChanges(exportDoc("xx", "site", changes), REPO, hooks);
  assert.equal(res.ok, true);
  const url = new URL(calls.open[0]);
  assert.equal(`${url.origin}${url.pathname}`, `https://github.com/${TOOLKIT_REPOSITORY}/issues/new`);
  assert.equal(url.searchParams.get("template"), TEMPLATE);
  assert.match(res.message, new RegExp(`on ${TOOLKIT_REPOSITORY} opened with 2 changes`));
});

test("a pack-only export opens the toolkit issue form with its glyphs and keyboard", () => {
  const { calls, hooks } = harness();
  const pack = exportDoc("xx", "test-1.0", [], [], [], [{ style: "main", char: "Б" }],
    { rows: { lower_keys_1: "АБВ" }, base: { rows: { lower_keys_1: "АБ" } } });
  const res = submitChanges(pack, REPO, hooks);
  assert.equal(res.ok, true);
  const url = new URL(calls.open[0]);
  assert.equal(`${url.origin}${url.pathname}`, `https://github.com/${TOOLKIT_REPOSITORY}/issues/new`);
  assert.equal(url.searchParams.get("template"), TEMPLATE);
  assert.equal(url.searchParams.get("title"), "Editor submission: test-1.0/xx, 1 glyph, 1 keyboard change");
  const sub = JSON.parse(url.searchParams.get("export"));
  assert.deepEqual(sub.glyphs, pack.glyphs);
  assert.deepEqual(sub.keyboard, pack.keyboard);
  assert.deepEqual(sub.entries, []);
  assert.match(res.message, new RegExp(`on ${TOOLKIT_REPOSITORY} opened with 2 changes`));
});

test("the registry carries the submission repository of a game", () => {
  const game = { id: "a", name: "A", stage: "translation", languages: ["xx"], data: "./" };
  const reg = readRegistry({ schema: 1, games: [{ ...game, repository: REPO }, { ...game, id: "b" }] },
    "https://example.org/registry.json");
  assert.deepEqual(reg.games.map((g) => g.repository), [REPO, null]);
  assert.throws(() => readRegistry({ schema: 1, games: [{ ...game, repository: "x" }] }, "https://example.org/"),
    /invalid repository/);
});

const rule = { id: "units", capability_sha256: "a".repeat(64), base: { enabled: false, fields: {} },
  value: { enabled: true, fields: { height_unit: "m" } } };

test("policy-only and mixed rule proposals route to the project with typed payloads", () => {
  for (const texts of [[], [{ id: "sym:ending", base: "", base_state: "original", state: "empty", translation: "" }]]) {
    const proposal = exportDoc("aa", "fixture", texts, [], [], [{ style: "main", char: "A" }],
      { rows: { keys: "abc" }, base: { rows: { keys: "ab" } } }, [rule]);
    const { hooks, calls } = harness();
    assert.equal(submissionRepository(proposal, REPO), REPO);
    const sub = submissionDoc(proposal);
    assert.deepEqual(sub.engine_rules, [rule]);
    assert.deepEqual(sub.entries, texts);
    assert.equal(sub.glyphs, undefined);
    assert.equal(sub.keyboard, undefined);
    const result = submitChanges(proposal, REPO, hooks);
    assert.equal(result.ok, true);
    const url = new URL(calls.open[0]);
    assert.equal(url.pathname, `/${REPO}/issues/new`);
    assert.match(url.searchParams.get("title"), /1 engine rule/);
    assert.deepEqual(JSON.parse(url.searchParams.get("export")), sub);
    assert.match(result.message, /The glyph redraws travel in the JSON file/);
    assert.match(result.message, /The keyboard changes travel in the JSON file/);
  }
});

test("original, Off and custom states survive issue and attachment export with their first baseline", () => {
  const entries = [
    { id: "sym:first", base: "", base_state: "original", translation: "", state: "empty" },
    { id: "sym:second", base: "", base_state: "empty", translation: "", state: "original" },
    { id: "sym:third", base: "s", base_state: "text", translation: "x", state: "text" },
  ];
  const proposal = exportDoc("aa", "fixture", entries, [], [], [], null, [rule]);
  assert.deepEqual(submissionDoc(proposal, () => "new catalog value").entries, entries);
  const { hooks, calls } = harness();
  submitChanges({ ...proposal, notes: "Long review note. ".repeat(1000) }, REPO, hooks);
  assert.equal(calls.download.length, 1);
  assert.deepEqual(calls.download[0].entries, entries);
  assert.deepEqual(calls.download[0].engine_rules, [rule]);
  assert.equal(new URL(calls.open[0]).searchParams.get("export"), null);
});

test("note-only exports preserve bases, attribution and reviewer messages through the issue", () => {
  const drafts = new NoteEdits(fakeStorage(), "xx", "project");
  const base = noteCollection().note[1], before = structuredClone(base);
  drafts.set(base.subject, base.binding, { ...base, guidance: ["Changed guidance."] }, base, base.id, "Review this note.");
  const added = drafts.set(base.subject, base.binding, { scope: base.scope, guidance: ["New guidance."] });
  const doc = { ...exportDoc("xx", "project", [], [], [], [], null, [], drafts.changes()), notes: "Review the submission." };
  assert.equal(doc.note_changes_schema, "3.0.0");
  assert.deepEqual(doc.entries, []);
  const edited = doc.note_changes.find((change) => change.id === base.id);
  assert.equal(edited.operation, "edit");
  assert.equal(edited.base_revision, noteRevision(base));
  assert.deepEqual(edited.base, base);
  assert.equal(edited.proposed.author, base.author);
  assert.equal(edited.proposed.created, base.created);
  assert.equal(edited.proposed.status, "proposed");
  assert.equal(edited.proposed.reviewed_at, undefined);
  assert.equal(doc.note_changes.find((change) => change.id === added).proposed.author, undefined);
  assert.equal(doc.note_changes.find((change) => change.id === added).proposed.created, undefined);
  const { calls, hooks } = harness();
  assert.equal(submitChanges(doc, REPO, hooks).ok, true);
  const link = new URL(calls.open[0]);
  assert.equal(link.pathname, `/${REPO}/issues/new`);
  assert.match(link.searchParams.get("title"), /2 note changes$/);
  const sub = calls.download[0] ?? JSON.parse(link.searchParams.get("export"));
  assert.deepEqual(sub.note_changes, doc.note_changes);
  assert.equal(sub.notes, "Review the submission.");
  assert.equal(sub.note_changes.find((change) => change.id === base.id).message, "Review this note.");
  assert.deepEqual(base, before);
});

test("all note subjects route to the game repository that owns the profile", () => {
  const base = noteCollection().note[1];
  const drafts = new NoteEdits(fakeStorage(), "xx", "project");
  drafts.set(base.subject, base.binding, { scope: base.scope, guidance: ["New guidance."] });
  const doc = exportDoc("xx", "project", [], [], [], [], null, [], drafts.changes());
  for (const kind of ["entry", "term", "graphic", "profile_fact", "wording_rule", "research"]) {
    const changed = structuredClone(doc);
    changed.note_changes[0].subject.kind = kind;
    changed.note_changes[0].destination = "../../wrong";
    assert.equal(submissionRepository(changed, REPO), REPO);
    assert.equal(submissionRepository(changed, null), null);
  }
  doc.glyphs = [{ style: "main", char: "X" }];
  doc.keyboard = { rows: { lower: "xy" }, base: { rows: { lower: "xx" } } };
  doc.notes = "Reviewer context.";
  const [game, pack] = submissionParts(doc, REPO);
  assert.equal(game.repository, REPO);
  assert.equal(game.doc.note_changes.length, 1);
  assert.equal(game.doc.keyboard, undefined);
  assert.equal(game.doc.glyphs, undefined);
  assert.equal(pack.repository, TOOLKIT_REPOSITORY);
  assert.equal(pack.doc.note_changes, undefined);
  assert.equal(pack.doc.note_changes_schema, undefined);
  assert.deepEqual(pack.doc.glyphs, doc.glyphs);
  assert.deepEqual(pack.doc.keyboard, doc.keyboard);
  assert.equal(pack.doc.notes, doc.notes);
  assert.deepEqual(pack.doc.entries, []);
  const { calls, hooks } = harness();
  for (const part of [game, pack]) assert.equal(submitChanges(part.doc, part.repository, hooks).ok, true);
  assert.deepEqual(calls.open.map((url) => new URL(url).pathname), [`/${REPO}/issues/new`, `/${TOOLKIT_REPOSITORY}/issues/new`]);
  assert.equal(doc.note_changes.length, 1);
  assert.equal(doc.glyphs.length, 1);
});

test("evidence attachments stay in note JSON and never become replacement pictures", async () => {
  const base = noteCollection("graphic", "G01").note[1];
  const drafts = new NoteEdits(fakeStorage(), "xx", "project");
  const id = drafts.set(base.subject, base.binding, { scope: base.scope, guidance: ["Keep the complete label."] });
  await drafts.attach(id, evidenceFile(), { role: "original", build: "1.0.0", context: "Title" });
  await drafts.attach(id, evidenceFile(), { role: "proposed", build: "1.1.0", context: "Title" });
  const doc = exportDoc("xx", "project", [], [], [], [], null, [], drafts.changes());
  const { calls, hooks } = harness();
  const result = submitChanges(doc, REPO, hooks);
  assert.equal(result.ok, true);
  assert.equal(result.attach, true);
  assert.equal(calls.download.length, 1);
  assert.deepEqual(calls.download[0].note_changes, doc.note_changes);
  assert.equal(calls.download[0].pictures, undefined);
  assert.deepEqual(calls.picture, []);
  assert.equal(new URL(calls.open[0]).searchParams.get("export"), null);
});

test("incomplete notes and unsupported schemas cannot open an issue", () => {
  const base = noteCollection("term").note[1];
  const drafts = new NoteEdits(fakeStorage(), "xx", "project");
  drafts.set(base.subject, null, { scope: base.scope, guidance: ["Use this term."] });
  const doc = exportDoc("xx", "project", [], [], [], [], null, [], drafts.changes());
  const { calls, hooks } = harness();
  assert.equal(submitChanges(doc, REPO, hooks).ok, false);
  assert.match(submissionProblem(doc), /Complete the note draft/);
  assert.deepEqual(calls.open, []);
  assert.equal(submissionDoc(doc).note_changes.length, 1);
  assert.match(submissionProblem({ ...doc, note_changes_schema: "1.0.0" }), /Unsupported/);
  doc.note_changes[0].operation = "unknown";
  assert.match(submissionProblem(doc), /Invalid note change/);
});
