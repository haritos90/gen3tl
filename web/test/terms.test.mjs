// The Terms view's helpers.
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { test } from "node:test";

import {
  affected, allForms, candidates, editTerm, filterTerms, glossaryProblems, grammarText, previewKey, renames, reportEntries, senseRows,
  shortenedOf, staleForms, surfaces, termFields, usage, usesOf,
  TermsView,
} from "../js/terms.js";
import { fakeStorage, noteCollection, stubPage } from "./fixture.mjs";
import { exportDoc, NoteEdits, TermEdits } from "../js/store.js";
import { limitResearchList, renderNotes, renderResearchReferences } from "../js/guidance.js";
import { submitChanges } from "../js/submit.js";
import { setCatalog, setUiLanguage, SOURCE_LANGUAGE } from "../js/ui.js";

test("migrated term cards replace the projection with canonical note controls", (context) => {
  const { $ } = stubPage(context, "editor");
  const notes = noteCollection("term", "term:one");
  const term = { id: "term:one", note_ids: ["note:shared", "note:wording"], notes: "Legacy projection.", forms: {} };
  const before = structuredClone(term);
  const drafts = new NoteEdits(fakeStorage(), "xx", "project");
  const view = new TermsView({ index: () => ({ notes, language: "xx" }), edits: () => ({ get: () => null }),
    noteEdits: () => drafts }, $);
  view.renderFacts(term);
  view.renderForm(term);
  for (const text of ["Guidance", "Facts", "Reason", "Sources", "Evidence", "Review: Proposed", "Related attestation."]) {
    assert.ok($("t-facts").textContent.includes(text), text);
  }
  assert.equal($("t-notes").value, "Legacy projection.");
  assert.equal($("t-notes").readOnly, true);
  assert.equal($("t-notes-label").hidden, true);
  assert.equal($("t-notes-field").hidden, true);
  assert.equal($("t-note-controls").hidden, false);
  assert.ok($("t-note-controls").textContent.includes("Suggest edit"));
  assert.ok($("t-facts").textContent.includes("Related note: Research: not recorded · Related attestation."));
  assert.doesNotMatch($("t-facts").textContent + $("t-note-controls").textContent, /\b(?:note|rule|research):\S/);
  assert.equal($("t-facts").textContent.includes("Note drafts"), false);
  assert.deepEqual(term, before);
  const shown = $("t-facts").textContent;
  const { notes: projection, ...migrated } = term;
  view.renderFacts(migrated);
  view.renderForm(migrated);
  assert.equal($("t-facts").textContent, shown);
  assert.equal($("t-notes-field").hidden, true);
  view.renderForm({ ...term, note_ids: undefined, notes: "Unmigrated note." });
  assert.equal($("t-notes").value, "Unmigrated note.");
  assert.equal($("t-notes").readOnly, false);
  assert.equal($("t-notes-label").hidden, false);
  assert.equal($("t-notes-field").hidden, false);
  assert.equal($("t-note-controls").hidden, true);
  assert.equal($("t-note-controls").textContent, "");
});

test("term research links show the table, row name and shortened first statement", (context) => {
  const { $ } = stubPage(context, "editor"), notes = noteCollection("term", "term:one");
  const research = { "research:one": { table: "moves.tsv", name: "Twin Tap" } };
  notes.note[2].facts = ["A recorded finding. ".repeat(30), "Second finding."];
  const term = { id: "term:one", note_ids: ["note:shared"], forms: {} };
  const before = structuredClone({ notes, research, term });
  const view = new TermsView({ index: () => ({ notes, research, language: "xx" }) }, $);
  view.renderFacts(term);
  const fields = $("t-facts").querySelectorAll("div").filter((node) => node.className === "note-field");
  for (const label of ["Related subject", "Related note"]) {
    const field = fields.find((node) => node.textContent.startsWith(`${label}: Research:`));
    assert.ok(field.textContent.startsWith(`${label}: Research: moves.tsv · Twin Tap · A recorded finding.`));
    assert.ok(field.textContent.endsWith("… Copy reference"));
    assert.ok(field.textContent.length < 240);
    assert.ok(!field.textContent.includes("Second finding."));
  }
  assert.doesNotMatch($("t-facts").textContent, /\bresearch:\S/);
  assert.deepEqual({ notes, research, term }, before);
});

test("term research source addresses and three labels follow every interface catalog", (context) => {
  const { $ } = stubPage(context, "editor"), notes = noteCollection("term", "term:one");
  const research = { "research:one": { table: "roles.tsv", name: "Night Crew" } };
  const note = notes.note[1];
  note.sources = [{ kind: "project", name: "Terminology research", address: "research:one", verification: "recorded" }];
  note.related_notes = ["note:research"];
  note.related_subjects = [{ kind: "research", id: "research:one" }];
  const term = { id: "term:one", note_ids: [note.id], forms: {} };
  const view = new TermsView({ index: () => ({ notes, research, language: "xx" }) }, $);
  const directory = new URL("../i18n/", import.meta.url), catalogs = [[SOURCE_LANGUAGE, {}]];
  for (const file of readdirSync(directory).filter((name) => name.endsWith(".po"))) {
    const po = readFileSync(new URL(file, directory), "utf8");
    catalogs.push([file.slice(0, -3), Object.fromEntries([...po.matchAll(/^msgid ("[^\n]*")\nmsgstr ("[^\n]*")$/gm)]
      .map((match) => [JSON.parse(match[1]), JSON.parse(match[2])]))]);
  }
  assert.ok(catalogs.length >= 2);
  context.after(() => { setCatalog(null); setUiLanguage(null); });
  for (const [code, catalog] of catalogs) {
    setCatalog(catalog);
    setUiLanguage(code);
    view.renderFacts(term);
    const text = $("t-facts").textContent;
    for (const label of ["Related subject", "Related note", "Address"]) {
      if (code !== SOURCE_LANGUAGE) assert.ok(catalog[label], `${code}: ${label}`);
      assert.ok(text.includes(`${catalog[label] ?? label}: ${catalog.Research ?? "Research"}: roles.tsv · Night Crew · Related attestation.`));
    }
    assert.doesNotMatch(text, /\bresearch:\S/);
    const descendants = (node) => [node, ...node.children.flatMap(descendants)];
    assert.ok(!descendants($("t-facts")).some((node) => node.tagName === "STYLE" || node.getAttribute("style")));
    const generated = referenceFixture(3);
    generated.notes.summaries["reference:1"] = generated.notes.summaries["reference:0"];
    const generatedView = new TermsView({ index: () => generated }, $);
    generatedView.renderFacts({ id: "term:one", note_ids: generated.notes.note.map((note) => note.id) });
    const list = $("t-facts");
    assert.ok(list.textContent.includes(catalog["Research references"] || "Research references"));
    for (const key of ["Research references", "Copy reference", "Copy reference {n}"]) {
      if (code !== SOURCE_LANGUAGE) assert.ok(catalog[key], `${code}: ${key}`);
    }
    const controls = list.querySelectorAll("ul")[0].querySelectorAll("button");
    for (const [i, control] of controls.entries()) {
      const label = i < 2 ? (catalog["Copy reference {n}"] || "Copy reference {n}").replace("{n}", i + 1)
        : catalog["Copy reference"] || "Copy reference";
      assert.equal(control.getAttribute("aria-label"), label);
      assert.equal(control.title, label);
      assert.equal(control.textContent, i < 2 ? String(i + 1) : "");
    }
  }
});

test("absent projections keep term edits separate from canonical note drafts", (context) => {
  const { $ } = stubPage(context, "editor"), notes = noteCollection("term", "term:one");
  const language = notes.note[1].scope.language, storage = fakeStorage();
  const drafts = new NoteEdits(storage, language, "project"), terms = new TermEdits(storage, language, "project");
  const term = { id: "term:one", translation: "Name", forms: { "nomn.sing": "Name" }, sources: "reference",
    note_ids: notes.note.map((note) => note.id) };
  const before = structuredClone(term);
  const view = new TermsView({ data: () => ({ terms: [term] }), index: () => ({ notes, language }),
    edits: () => terms, noteEdits: () => drafts }, $);
  view.cur = term.id;
  view.renderForm(term);
  assert.equal($("t-notes-field").hidden, true);
  const area = $("t-note-controls").querySelectorAll("textarea").find((node) => node.value === notes.note[1].guidance[0]);
  area.value = "Revised guidance.";
  area.listeners.input({ target: area });
  assert.equal(drafts.changes()[0].subject.kind, "term");
  assert.deepEqual(drafts.changes()[0].proposed.guidance, ["Revised guidance."]);
  assert.equal(terms.size, 0);
  assert.deepEqual(termFields(term).note_ids, term.note_ids);
  assert.deepEqual(editTerm(term, term, "notes", "Legacy replacement"), term);
  view.edit("notes", "Legacy replacement");
  assert.equal(terms.size, 0);
  const row = editTerm(term, term, "sources", "reference, context");
  terms.set(term, row);
  assert.deepEqual(terms.changes()[0].changes, { sources: "reference, context" });
  assert.equal(renames(term, row), false);
  assert.deepEqual(term, before);
});

test("term cards use glossary note bindings when the loaded row omits them", (context) => {
  const { $ } = stubPage(context, "editor"), notes = noteCollection("term", "term:one");
  const term = { id: "term:one", sources: "reference", forms: {} };
  const view = new TermsView({ index: () => ({ notes, language: "xx",
    glossary: { [term.id]: { note_ids: [notes.note[0].id] } } }), edits: () => null }, $);
  view.renderForm(term);
  assert.equal($("t-notes-field").hidden, true);
  assert.equal($("t-sources").value, "reference");
});

test("term note drafts leave names, forms, legacy notes and rename drafts unchanged", (context) => {
  const { $ } = stubPage(context, "editor"), notes = noteCollection("term", "term:one");
  const language = notes.note[1].scope.language, storage = fakeStorage();
  const drafts = new NoteEdits(storage, language, "project"), terms = new TermEdits(storage, language, "project");
  const term = { id: "term:one", translation: "Name", forms: { "nomn.sing": "Name" }, notes: "Legacy note." };
  const before = structuredClone(term);
  const view = new TermsView({ index: () => ({ notes, language }), edits: () => terms, noteEdits: () => drafts }, $);
  view.renderFacts(term);
  $("t-facts").querySelectorAll("button").find((node) => node.textContent === "Add note").listeners.click();
  const area = $("t-facts").querySelectorAll("textarea").at(-4);
  area.value = "Suggested usage.";
  area.listeners.input({ target: area });
  assert.equal(drafts.size, 1);
  assert.equal(terms.size, 0);
  assert.deepEqual(term, before);
  view.renderFacts(term);
  assert.ok($("t-facts").querySelectorAll("textarea").some((node) => node.value === "Suggested usage."));
});

function referenceFixture(count = 2) {
  const notes = { schema: "3.0.0", note: [], summaries: {} }, research = {};
  for (let i = 0; i < count; i++) {
    const note = { id: `reference:${i}`, subject: { kind: "term", id: "term:one" },
      scope: { kind: "language", language: "xx" }, status: "unreviewed",
      evidence: [{ method: "research-citation", verification: "recorded" }],
      related_subjects: [{ kind: "research", id: `finding:${i}` }] };
    notes.note.push(note);
    notes.summaries[note.id] = [`Archive: attested spelling ${i}.`];
    research[`finding:${i}`] = { table: "findings.tsv", name: "Example" };
  }
  return { notes, research, language: "xx" };
}

test("reference-only term cards retain editable notes without exporting references", async (context) => {
  const { $ } = stubPage(context, "editor"), index = referenceFixture();
  const notes = new NoteEdits(fakeStorage(), "xx", "project"), edits = new TermEdits(fakeStorage(), "xx", "project");
  const legacy = { id: "term:one", source_text: "Example", translation: "Target", notes: "Independent guidance.", forms: {} };
  const term = { ...legacy, note_ids: index.notes.note.map((note) => note.id) };
  const view = new TermsView({ index: () => index, edits: () => edits, noteEdits: () => notes }, $);
  view.renderForm(term);
  view.renderFacts(term);
  assert.equal($("t-notes").value, legacy.notes);
  assert.equal($("t-notes").readOnly, false);
  assert.equal($("t-notes-field").hidden, false);
  const facts = $("t-facts");
  assert.match(facts.textContent, /Research references/);
  assert.equal(facts.querySelectorAll("ul").length, 1);
  assert.equal(facts.querySelectorAll("li").length, 2);
  assert.equal(facts.querySelectorAll("article").length, 0);
  assert.deepEqual(facts.querySelectorAll("dt").map((node) => node.textContent), ["Research references", "Note drafts"]);
  const value = facts.children[1];
  assert.equal(value.tagName, "DD");
  assert.equal(value.querySelectorAll("ul").length, 1);
  assert.equal(value.querySelectorAll("h3").length, 0);
  assert.doesNotMatch(facts.textContent, /Review:|Sources|Evidence|Suggest edit|Related notes/);
  const changed = editTerm(term, term, "notes", "Edited guidance.");
  edits.set(term, changed);
  const after = exportDoc("xx", "project", [], [], edits.changes(), [], null, [], notes.changes());
  const legacyEdits = new TermEdits(fakeStorage(), "xx", "project");
  legacyEdits.set(legacy, editTerm(legacy, legacy, "notes", "Edited guidance."));
  const before = exportDoc("xx", "project", [], [], legacyEdits.changes());
  assert.deepEqual(after, before);
  assert.equal(JSON.stringify(after).includes("reference:"), false);
  const submitted = [];
  for (const doc of [before, after]) {
    const result = submitChanges(doc, "fixture/project", { open: (url) => submitted.push(url) });
    assert.equal(result.ok, true);
  }
  assert.equal(submitted[1], submitted[0]);
  const payload = JSON.parse(new URL(submitted[1]).searchParams.get("export"));
  assert.deepEqual(payload.terms, after.terms);
  assert.equal(payload.note_changes, undefined);
  assert.equal(notes.size, 0);
  edits.remove(term.id);
  view.renderForm({ ...term, notes: "" });
  assert.equal($("t-notes-field").hidden, false);
  assert.equal($("t-notes").value, "");
});

test("mixed term cards keep hand-written cards and combine duplicate references", async (context) => {
  const { $ } = stubPage(context, "editor"), index = referenceFixture(3);
  index.notes.summaries["reference:1"] = [...index.notes.summaries["reference:0"]];
  const independent = noteCollection("term", "term:one").note[0];
  independent.related_notes = [];
  independent.related_subjects = [];
  index.notes.note.unshift(independent);
  const term = { id: "term:one", note_ids: index.notes.note.map((note) => note.id), forms: {} };
  const drafts = new NoteEdits(fakeStorage(), "xx", "project");
  const view = new TermsView({ index: () => index, edits: () => null, noteEdits: () => drafts }, $);
  view.renderForm(term);
  view.renderFacts(term);
  const facts = $("t-facts"), list = facts.querySelectorAll("ul").find((node) => node.className === "research-reference-list");
  assert.equal(list.querySelectorAll("li").length, 2);
  assert.equal(list.textContent.split("Archive: attested spelling 0.").length - 1, 1);
  assert.equal(facts.querySelectorAll("article").length, 1);
  assert.deepEqual(facts.querySelectorAll("dt").map((node) => node.textContent), ["Notes", "Research references"]);
  assert.equal(facts.children[1].querySelectorAll("article").length, 1);
  assert.equal(facts.children[1].textContent.includes("Archive:"), false);
  assert.equal(facts.children[3].querySelectorAll("article").length, 0);
  assert.equal(facts.children[3].querySelectorAll("ul")[0], list);
  assert.equal(facts.querySelectorAll("h3").length, 0);
  assert.match(facts.textContent, /Preserve the placeholder/);
  assert.equal($("t-notes-field").hidden, true);
  assert.equal($("t-note-controls").textContent.split("Suggest edit").length - 1, 1);
  const controls = list.querySelectorAll("button");
  assert.deepEqual(controls.map((node) => node.textContent), ["1", "2", ""]);
  const copied = [];
  const saved = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: { clipboard: { writeText: async (id) => copied.push(id) } } });
  context.after(() => { if (saved) Object.defineProperty(globalThis, "navigator", saved); else delete globalThis.navigator; });
  for (const control of controls) await control.listeners.click();
  assert.deepEqual(copied, ["finding:0", "finding:1", "finding:2"]);
  for (const control of controls) {
    assert.equal(control.textContent, "Reference copied");
    assert.equal(control.getAttribute("aria-label"), "Reference copied");
    assert.equal(control.title, "Reference copied");
    assert.equal(control.getAttribute("aria-live"), "polite");
    assert.equal(control.getAttribute("aria-atomic"), "true");
  }
  navigator.clipboard.writeText = async () => { throw new Error("Clipboard unavailable"); };
  const create = document.createElement, selected = [];
  document.createElement = (tag) => Object.assign(create(tag), { readOnly: false, select() { selected.push(this.value); } });
  await controls[2].listeners.click();
  const fallback = list.querySelectorAll("input")[0];
  assert.equal(fallback.value, "finding:2");
  assert.equal(fallback.getAttribute("aria-label"), "Copy this reference");
  assert.equal(fallback.readOnly, true);
  assert.deepEqual(selected, ["finding:2"]);
});

test("research copy controls stay inline inside their summary line", (context) => {
  const { $ } = stubPage(context, "editor"), index = referenceFixture(3);
  index.notes.summaries["reference:1"] = index.notes.summaries["reference:0"];
  const view = new TermsView({ index: () => index }, $);
  view.renderFacts({ id: "term:one", note_ids: index.notes.note.map((note) => note.id) });
  const rows = $("t-facts").querySelectorAll("li");
  assert.equal(rows.length, 2);
  for (const [i, row] of rows.entries()) {
    assert.equal(row.childNodes[0].textContent, `Archive: attested spelling ${i * 2}.`);
    const controls = row.querySelectorAll("button");
    assert.equal(controls.length, i ? 1 : 2);
    for (const [j, control] of controls.entries()) {
      assert.equal(control.parentNode.tagName, "SPAN");
      assert.equal(control.parentNode.parentNode, row);
      assert.equal(control.className, "btn sm");
      const label = i ? "Copy reference" : `Copy reference ${j + 1}`;
      assert.equal(control.getAttribute("aria-label"), label);
      assert.equal(control.title, label);
      assert.equal(control.textContent, i ? "" : String(j + 1));
      const [svg] = control.querySelectorAll("svg");
      assert.ok(svg);
      assert.equal(svg.namespaceURI, "http://www.w3.org/2000/svg");
      assert.equal(svg.getAttribute("aria-hidden"), "true");
      assert.equal(svg.getAttribute("focusable"), "false");
      assert.equal(svg.getAttribute("width"), "14");
      assert.equal(svg.getAttribute("height"), "14");
      assert.equal(svg.querySelectorAll("path").length, 1);
    }
    assert.equal(row.querySelectorAll("br").length, 0);
  }
  const css = readFileSync(new URL("../site.css", import.meta.url), "utf8");
  assert.match(css, /\.research-reference-list \.research-reference \{[^}]*display: inline;/);
  assert.match(css, /\.research-reference-list \.btn\.sm \{[^}]*display: inline-flex;[^}]*padding: 0 4px;[^}]*vertical-align: baseline;[^}]*white-space: nowrap;/);
});

test("assembly references use research prose while independent facts retain note cards", (context) => {
  stubPage(context, "editor");
  const index = referenceFixture(2), [reference, independent] = index.notes.note;
  reference.evidence[0].method = "research-assembly";
  reference.facts = ["Terminology follows the linked research record."];
  delete index.notes.summaries[reference.id];
  index.notes.note.push({ id: "note:finding", subject: reference.related_subjects[0], scope: reference.scope,
    status: "accepted", facts: ["The archive confirms this form."] });
  independent.evidence[0].method = "research-assembly";
  independent.facts = ["Independent term guidance."];
  delete index.notes.summaries[independent.id];
  const view = renderNotes([reference.id, independent.id], index.notes, index.language, index.research);
  assert.equal(view.querySelectorAll("article").length, 1);
  assert.match(view.textContent, /Independent term guidance/);
  assert.match(view.textContent, /The archive confirms this form/);
  assert.doesNotMatch(view.textContent, /Terminology follows/);
});

test("long research lists collapse only beyond two screen heights", (context) => {
  const { $ } = stubPage(context, "editor"), index = referenceFixture(18);
  const card = $("term");
  card.replaceChildren(renderResearchReferences(index.notes.note.map((note) => note.id), index.notes, index.language, index.research));
  Object.defineProperty(card, "scrollHeight", { value: 2100, configurable: true });
  limitResearchList(card, 1050);
  assert.equal(card.querySelectorAll("li").filter((row) => row.hidden).length, 0);
  Object.defineProperty(card, "scrollHeight", { value: 2101 });
  limitResearchList(card, 1050);
  assert.equal(card.querySelectorAll("li").filter((row) => row.hidden).length, 10);
  const more = [...card.querySelectorAll("button")].find((button) => button.textContent === "Show all 18");
  more.listeners.click();
  assert.equal(card.querySelectorAll("li").filter((row) => row.hidden).length, 0);
  assert.equal(more.hidden, true);
  assert.equal(card.querySelectorAll("style").length, 0);
});

test("switching projects discards an unfinished glossary load", async () => {
  let finish;
  const data = new Promise((resolve) => { finish = resolve; });
  const view = new TermsView({ load: () => data }, () => ({ replaceChildren() {} }));
  view.reset();
  const pending = view.show();
  view.reset();
  finish();
  await pending;
  assert.equal(view.loaded, false);
  assert.equal(view.cur, null);
});

const GYM = {
  id: "term:gym", category: "term", source_text: "Gym", translation: "Гим", label: "", gender: "", declension: "auto",
  forms: { "nomn.sing": "Гим", "gent.sing": "Гима", "nomn.plur": "Гимы" }, sources: "", notes: "", status: "approved",
  in_source: ["s1", "s2", "s3"], in_translation: ["s1", "s4"],
};
const DASH = { id: "move:x", category: "move", source_text: "-", translation: "-", label: "", gender: "",
  declension: "fixed", forms: { "nomn.sing": "-", "gent.sing": "-" }, in_source: [], in_translation: [] };
const BAG = { id: "item:bag", category: "item", source_text: "Bag", translation: "Сумка", label: "Сум.", gender: "",
  declension: "fixed", forms: { "nomn.sing": "Сумка", "gent.sing": "Сумка" }, in_source: ["s5"], in_translation: ["s5"] };
const TERMS = [DASH, GYM, BAG];

const ids = (f, edits) => filterTerms(TERMS, f, edits).map((i) => TERMS[i].id);

test("the list: most used first, filters and search", () => {
  assert.deepEqual(TERMS.map(usesOf), [0, 4, 1]); // English or translation, each entry once
  assert.deepEqual(ids({}), ["term:gym", "item:bag", "move:x"]);
  assert.deepEqual(ids({ sort: "glossary" }), ["move:x", "term:gym", "item:bag"]);
  assert.deepEqual(ids({ sort: "english" }), ["move:x", "item:bag", "term:gym"]);
  assert.deepEqual(ids({ category: "item" }), ["item:bag"]);
  assert.deepEqual(ids({ status: "unused" }), ["move:x"]);
  assert.deepEqual(ids({ status: "used" }), ["term:gym", "item:bag"]);
  assert.deepEqual(ids({ search: "гим" }), ["term:gym"]);
  assert.deepEqual(ids({ search: "ITEM:BAG" }), ["item:bag"]);
  const edits = new Map([["item:bag", { row: { ...BAG, translation: "Рюкзак" } }]]);
  assert.deepEqual(ids({ status: "edited" }, edits), ["item:bag"]);
  assert.deepEqual(ids({ search: "рюкзак" }, edits), ["item:bag"]);
});

test("an edit: the nominative follows, fixed forms follow, hand-made forms are manual", () => {
  const base = termFields(GYM);
  assert.equal(base.in_source, undefined);
  let row = editTerm(base, base, "translation", "Стадион");
  assert.deepEqual(row.forms, { "nomn.sing": "Стадион", "gent.sing": "Гима", "nomn.plur": "Гимы" });
  assert.equal(row.declension, "manual"); // term-forms keeps them
  assert.deepEqual(staleForms(base, row), ["gent.sing", "nomn.plur"]);
  row = editTerm(base, row, "forms.gent.sing", "Стадиона");
  row = editTerm(base, row, "forms.nomn.plur", "");
  assert.deepEqual(row.forms, { "nomn.sing": "Стадион", "gent.sing": "Стадиона" });
  assert.deepEqual(staleForms(base, row), []);
  assert.ok(renames(base, row));
  const back = editTerm(base, editTerm(base, editTerm(base, row, "translation", "Гим"), "forms.gent.sing", "Гима"),
    "forms.nomn.plur", "Гимы");
  assert.equal(back.declension, "auto"); // the data's forms again
  const notes = editTerm(base, base, "notes", "x");
  assert.ok(!renames(base, notes));
  assert.ok(renames(base, editTerm(base, base, "gender", "male")));
  const bag = termFields(BAG);
  const fixed = editTerm(bag, bag, "translation", "Рюкзак");
  assert.deepEqual([fixed.forms, fixed.declension], [{ "nomn.sing": "Рюкзак", "gent.sing": "Рюкзак" }, "fixed"]);
});

test("the forms a rename looks for, and the entries that may hold them", () => {
  assert.deepEqual(allForms({ translation: "Сумка", label: "Сум.", forms: {} }), { "nomn.sing": "Сумка", label: "Сум." });
  assert.deepEqual(surfaces(BAG), ["сумка", "сум."]);
  assert.deepEqual(surfaces(DASH), []); // the dash is no form to look for
  const rows = [{ id: "s1", translation: "" }, { id: "s6", translation: "Нет Гима" }, { id: "s7", translation: "Гимн" },
    { id: "s8", translation: "Ёлка" }];
  const text = (r) => (r.id === "s1" ? "edited: гимы" : r.translation);
  const got = candidates(GYM, rows, text, { s6: [["s9", "STR_VAR_1"]] });
  assert.deepEqual([...got].sort(), ["s1", "s4", "s6", "s7", "s9"]); // a superset: s7 holds "гим" inside
});

test("the entries using a term: English, form, none in a translation", () => {
  const order = (id) => ({ s4: 0, s1: 1, s3: 2, s2: 3 })[id];
  assert.deepEqual(usage(GYM, order, (id) => id !== "s3"), [
    { id: "s4", in_source: false, translation: true, formOnly: false, shortened: false, otherTerms: [], missing: false },
    { id: "s1", in_source: true, translation: true, formOnly: false, shortened: false, otherTerms: [], missing: false },
    { id: "s3", in_source: true, translation: false, formOnly: false, shortened: false, otherTerms: [], missing: false }, // untranslated
    { id: "s2", in_source: true, translation: false, formOnly: false, shortened: false, otherTerms: [], missing: true },
  ]);
});

test("term cards accept another term's forms for the same English", (context) => {
  const { $ } = stubPage(context, "editor");
  const entries = [
    { id: "s1", source_text: "Guard", translation: "Watchman", status: "approved" },
    { id: "s2", source_text: "Guards", translation: "Wardens", status: "fuzzy" },
    { id: "s3", source_text: "Guard", translation: "Soldier", status: "approved" },
    { id: "s4", source_text: "Guard", translation: "", status: "untranslated" },
    { id: "s5", source_text: "Guard", translation: "Watchmanship", status: "approved" },
    { id: "s6", source_text: "Guard", translation: "Watchman and Warden", status: "approved" },
  ];
  const terms = [
    { id: "speaker:watchman", category: "speaker", source_text: "Guard", translation: "Watchman",
      forms: {}, in_source: entries.map((e) => e.id), in_translation: ["s1", "s6"] },
    { id: "speaker:warden", category: "speaker", source_text: "Guard", translation: "Warden",
      forms: { "nomn.plur": "Wardens" }, in_source: entries.map((e) => e.id), in_translation: ["s2", "s6"] },
    { id: "speaker:soldier", category: "speaker", source_text: "Soldier", translation: "Soldier", forms: {} },
    { id: "dex-category:guard", category: "dex-category", source_text: "Guard", translation: "Soldier", forms: {} },
  ];
  const glossary = Object.fromEntries(terms.map((term) => [term.id, term]));
  const row = (id) => entries.find((e) => e.id === id), order = (id) => entries.findIndex((e) => e.id === id);
  const view = new TermsView({ data: () => ({ terms }), row, order, edits: () => null,
    textOf: (entry) => entry.translation }, $);
  for (const term of terms.slice(0, 2)) {
    const uses = usage(term, order, (id) => row(id).status !== "untranslated",
      { terms, textOf: (id) => row(id).translation });
    assert.deepEqual(uses.filter((u) => u.missing).map((u) => u.id), ["s3", "s5"]);
    for (const use of uses) {
      const entry = row(use.id);
      assert.equal(use.missing, !!entry.translation && glossaryProblems(entry.source_text, entry.translation, glossary).length > 0);
    }
    const other = terms.find((x) => x.id !== term.id && x.category === "speaker" && x.source_text === term.source_text);
    assert.deepEqual(uses.flatMap((u) => u.otherTerms.map((x) => x.id)), [other.id]);
    view.renderUsage(term);
    const text = $("t-usage").textContent;
    assert.ok(text.includes("6 entries: 6 name the term in the English, 2 hold a form in the translation"));
    assert.ok(text.includes("2 translated without a form (glossary warning)"));
    assert.ok(text.includes(`form of «${other.translation}»`));
    assert.equal($("t-usage").querySelectorAll("span").filter((node) => node.className === "mark err").length, 2);
  }
  context.after(() => { setCatalog(null); setUiLanguage(null); });
  const directory = new URL("../i18n/", import.meta.url);
  for (const file of readdirSync(directory).filter((name) => name.endsWith(".po"))) {
    const po = readFileSync(new URL(file, directory), "utf8");
    const catalog = Object.fromEntries([...po.matchAll(/^msgid ("[^\n]*")\nmsgstr ("[^\n]*")$/gm)]
      .map((match) => [JSON.parse(match[1]), JSON.parse(match[2])]));
    const key = "form of «{term}»";
    assert.ok(catalog[key], `${file}: ${key}`);
    setCatalog(catalog);
    setUiLanguage(file.slice(0, -3));
    view.renderUsage(terms[0]);
    assert.ok($("t-usage").textContent.includes(catalog[key].replace("{term}", terms[1].translation)));
  }
});

test("shared English uses accept folded forms beyond the usage index", () => {
  const own = { id: "speaker:one", source_text: "Guard", translation: "Sentry", in_source: ["s1"] };
  const other = { id: "speaker:two", source_text: "Guard", translation: "Véilleur", forms: {},
    in_source: [], in_translation: [], form_only: [] };
  const terms = [own, other], textOf = () => "A veilleur.";
  assert.equal(usage(own, () => 0, () => true, { terms, textOf })[0].missing, true);
  const use = usage(own, () => 0, () => true, { terms, textOf, fold: { "é": "e" } })[0];
  assert.equal(use.missing, false);
  assert.deepEqual(use.otherTerms.map((x) => x.id), [other.id]);
});

// A full term whose label the catalog shows
test("shortened in use: the filter, the list and the usage cards", () => {
  const kiss = { id: "move:sweet-kiss", category: "move", source_text: "Sweet Kiss", translation: "Сладкий поцелуй",
    label: "Сладкий поц.", gender: "", declension: "auto", forms: { "nomn.sing": "Сладкий поцелуй" },
    in_source: ["moves:186", "s7"], in_translation: ["moves:186", "s7"], shortened: ["moves:186"] };
  const terms = [GYM, kiss, BAG];
  assert.deepEqual(filterTerms(terms, { status: "shortened" }).map((i) => terms[i].id), ["move:sweet-kiss"]);
  assert.equal(shortenedOf(BAG).length, 0); // a label nobody shows is no shortened use
  assert.deepEqual(usage(kiss, () => 0).map((u) => [u.id, u.shortened]), [["moves:186", true], ["s7", false]]);
  const html = readFileSync(new URL("../editor.html", import.meta.url), "utf8");
  const select = html.match(/<select id="t-status"[^]*?<\/select>/)[0];
  assert.deepEqual([...select.matchAll(/<option value="([^"]*)"/g)].map((m) => m[1]),
    ["", "used", "unused", "shortened", "edited"]);
});

// Homograph forms
const SEEL = { id: "species:seel", category: "species", source_text: "Seel", translation: "Сил", label: "", gender: "",
  declension: "manual", forms: { "nomn.sing": "Сил", "gent.sing": "Сила", "accs.sing": "Сила", "datv.sing": "Силу" },
  in_source: ["s1"], in_translation: ["s1"], form_only: ["s2", "s3"] };

test("form-only entries are listed apart and do not count", () => {
  const order = (id) => ({ s1: 0, s2: 1, s3: 2 })[id];
  assert.equal(usesOf(SEEL), 1);
  assert.deepEqual(usage(SEEL, order).map((u) => [u.id, u.in_source, u.translation, u.formOnly, u.missing]), [
    ["s1", true, true, false, false], ["s2", false, false, true, false], ["s3", false, false, true, false]]);
  const box = { ...SEEL, in_source: ["s4"], in_translation: [], form_only: ["s4"] }; // «ячейку» for "Box"
  assert.deepEqual(usage(box, () => 0).map((u) => u.missing), [false]); // a form, only not a use
  assert.equal(usesOf({ ...SEEL, in_source: [], in_translation: [], _uses: undefined }), 0); // form only: unused
});

test("the rename's scope: rows sharing a form or an id, the included entries", () => {
  const ball = { id: "item:poke-ball", category: "item", source_text: "Poké Ball", translation: "Покебол",
    forms: { "nomn.sing": "Покебол", "gent.sing": "Покебола" }, in_source: [], in_translation: [] };
  const noun = { ...ball, id: "term:poke-ball", category: "term", translation: "покебол",
    forms: { "nomn.sing": "покебол", "gent.sing": "покебола" } };
  const strength = { id: "move:strength", category: "move", source_text: "Strength", translation: "Мощь", forms: {} };
  const seel2 = { ...SEEL, id: "species:seel~2", forms: { "nomn.sing": "Сиил" } };
  const terms = [ball, noun, strength, SEEL, seel2];
  assert.deepEqual(senseRows(ball, terms).map((x) => x.id), ["term:poke-ball"]); // the same letters
  assert.deepEqual(senseRows(SEEL, terms).map((x) => x.id), ["species:seel~2"]); // the same term's row
  assert.equal(senseRows(ball, terms)[0].in_source, undefined); // the fields only
  assert.equal(previewKey({ a: 1 }, ["s2", "s1"]), previewKey({ a: 1 }, ["s1", "s2"]));
  assert.notEqual(previewKey({ a: 1 }, []), previewKey({ a: 1 }, ["s1"]));
});

test("rename preview includes enclosing translations, inflections and labels", async () => {
  const elixir = { id: "item:elixir", category: "item", source_text: "Elixir", translation: "Эликсир",
    label: "Эл.", forms: { "gent.sing": "Эликсира" } };
  const maxElixir = { id: "item:max-elixir", category: "item", source_text: "Max Elixir", translation: "Эликсир макс",
    label: "Эл. макс", forms: { "gent.sing": "Эликсира макс" } };
  const inflected = { id: "term:inflected", translation: "Запас", forms: { "gent.sing": "Запаса Эликсира" } };
  const label = { id: "item:label", translation: "Полное имя", label: "Эл. плюс" };
  const substring = { id: "term:substring", translation: "Суперэликсир", label: "Элх макс" };
  const terms = [elixir, maxElixir, inflected, label, substring, BAG];
  assert.deepEqual(senseRows(elixir, terms).map((t) => t.id), [maxElixir.id, inflected.id, label.id]);
  assert.deepEqual(senseRows(maxElixir, terms), []);
  const entry = { id: "s1", translation: "Эликсир и Эликсир макс" };
  const row = editTerm(elixir, elixir, "translation", "Нектар");
  let scope;
  const host = {
    data: () => ({ terms }),
    edits: () => ({ get: () => ({ row }), include: () => [entry.id] }),
    rows: () => [entry], row: () => entry, entry: async () => entry, textOf: (e) => e.translation,
    check: () => ({ rename: (...args) => { scope = args[4]; return {}; } }),
  };
  const view = new TermsView(host, () => ({}));
  view.cur = elixir.id;
  view.renderState = () => {};
  view.renderReport = () => {};
  await view.preview();
  assert.deepEqual(scope, { rows: [maxElixir, inflected, label], include: [entry.id] });
});

test("a report by status and findings", () => {
  const e = (id, changed, added = [], agreement = [], ambiguous = []) =>
    ({ id, changed, added, agreement, ambiguous, before: "", after: "" });
  const rep = { entries: [e("s1", true), e("s2", true, [{ message: "x", severity: "error" }]), e("s3", false, [{ message: "y", severity: "error" }]),
    e("s4", true, [], [{ words: ["Этот"] }])] };
  const st = { s1: "approved", s2: "fuzzy", s3: "approved", s4: "approved" };
  const statusOf = (id) => st[id];
  assert.deepEqual(affected(rep, statusOf), { lines: 3, approved: 2, translated: 1, limit: 1, flagged: 3 });
  assert.deepEqual(reportEntries(rep, "approved", statusOf).map((x) => x.id), ["s1", "s4"]);
  assert.deepEqual(reportEntries(rep, "flagged", statusOf).map((x) => x.id), ["s2", "s3", "s4"]);
  assert.equal(reportEntries(rep, "", statusOf).length, 4);
  const g = { old: { gender: "masc", number: "sing", declension: "2", animacy: "" },
    new: { gender: "femn", number: "sing", declension: "1", animacy: "" },
    changes: [{ property: "gender", old: "masc", new: "femn" }, { property: "declension", old: "2", new: "1" }] };
  assert.equal(grammarText(g), "gender masc → femn; declension 2 → 1 (masc, declension 2 → femn, declension 1)");
  assert.equal(grammarText({ ...g, changes: [] }), "unchanged (masc, declension 2)");
});

test("page: a Terms view next to Texts and Pictures", () => {
  const html = readFileSync(new URL("../editor.html", import.meta.url), "utf8");
  assert.match(html, /<button id="view-terms" type="button" aria-pressed="false" title="Glossary terms"[^>]*>(?:<span data-ui="Terms">)?Terms(?:<\/span>)? <span class="count" id="count-terms"><\/span><\/button>/);
  assert.match(html, /<main id="terms" class="panes two" hidden>/);
  for (const id of ["t-search", "t-category", "t-status", "t-sort", "t-list", "t-list-space", "t-translation", "t-label",
    "t-gender", "t-sources", "t-notes", "t-forms", "t-stale", "t-preview", "t-report-filter", "t-report", "t-usage",
    "t-facts", "t-revert", "t-declension"]) {
    assert.match(html, new RegExp(`id="${id}"`), id);
  }
});

test("relabel before any data draws nothing and throws nothing", () => {
  const view = new TermsView({ data: () => null, edits: () => null }, () => ({ replaceChildren() {} }));
  view.relabel();
  view.reset();
  view.relabel();
  assert.equal(view.loaded, false);
});
