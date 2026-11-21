import { el } from "./dom.js";
import { N_, errorText, t } from "./ui.js";
import { CAPTURE_ROLES, noteProblems } from "./store.js";

const STATUSES = {
  unreviewed: N_("Unreviewed migration"), proposed: N_("Proposed"), accepted: N_("Accepted"),
  needs_review: N_("Needs review"), superseded: N_("Superseded"),
};
const VERIFICATIONS = { recorded: N_("Recorded"), checked: N_("Checked"), unresolved: N_("Unresolved") };
const KINDS = {
  entry: N_("Entry"), term: N_("Term"), research: N_("Research"), graphic: N_("Graphic"),
  profile_fact: N_("Profile fact"), wording_rule: N_("Wording rule"),
  web: N_("Web"), publication: N_("Publication"), rom: N_("ROM"), project: N_("Project"),
  rom_offset: N_("ROM offset"), cpu_address: N_("CPU address"), symbol: N_("Symbol"), file: N_("File"),
};
const RELATIONS = { selected_form: N_("Selected form"), alternative: N_("Alternative"), context: N_("Context") };

function field(label, value) {
  return el("div", { class: "note-field" }, el("b", { textContent: `${t(label)}: ` }), value);
}

const inScope = (note, language) => note && (note.scope.kind === "shared" || note.scope.language === language);
const noteItems = (note, key, summaries) => key === "facts" ? summaries?.[note?.id] ?? note?.facts ?? [] : note?.[key] ?? [];
const firstStatement = (note, summaries) => ["guidance", "facts", "reason"].flatMap((key) => noteItems(note, key, summaries))
  .find((text) => text.trim())?.replace(/\s+/g, " ").trim() ?? "";
const shortened = (text) => text.length > 160 ? `${text.slice(0, 157).trimEnd()}…` : text;

function subjectText(subject, references, note = null) {
  if (subject.kind === "research") {
    const record = note ?? references.research.get(subject.id)?.find((row) => inScope(row, references.language));
    const row = references.subjects[subject.id];
    const name = row ? [row.table, row.name || t("not recorded")].join(" · ") : t("not recorded");
    return [`${t("Research")}: ${name}`, shortened(firstStatement(record))].filter(Boolean).join(" · ");
  }
  const rule = subject.kind === "wording_rule"
    ? references.wording.get(subject.id)?.find((record) => inScope(record, references.language)) : null;
  const value = subject.kind === "wording_rule" ? firstStatement(rule ?? note) || t("not recorded") : subject.id;
  return `${t(KINDS[subject.kind] ?? subject.kind)}: ${value}`;
}

function subjectReference(subject, references, note = null) {
  if (subject.kind === "research") return researchReference(subject.id, subjectText(subject, references, note));
  return el("span", { title: subject.id, textContent: subjectText(subject, references, note) });
}

function referenceIcon() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  const attributes = {
    viewBox: "0 0 24 24", width: "14", height: "14", fill: "none", stroke: "currentColor",
    "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", focusable: "false",
  };
  for (const [key, value] of Object.entries(attributes)) svg.setAttribute(key, value);
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", "M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-2 2M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l2-2");
  svg.append(path);
  return svg;
}

function researchReference(id, text, { icon = false, number = null } = {}) {
  const label = number === null ? t("Copy reference") : t("Copy reference {n}", { n: number });
  const box = el("span", { class: "research-reference" }, ...(text ? [text, " "] : []));
  const copy = el("button", { type: "button", class: "btn sm", title: icon ? label : id,
    ...(icon ? { "aria-label": label, "aria-live": "polite", "aria-atomic": "true" } : {}), on: { click: async () => {
    try {
      await navigator.clipboard.writeText(id);
      copy.textContent = t("Reference copied");
      if (icon) {
        copy.setAttribute("aria-label", t("Reference copied"));
        copy.title = t("Reference copied");
      }
    } catch {
      const input = el("input", { readOnly: true, value: id, "aria-label": t("Copy this reference") });
      box.replaceChildren(text, " ", input);
      input.select();
    }
  } } });
  if (icon) copy.append(referenceIcon(), ...(number === null ? [] : [String(number)]));
  else copy.textContent = label;
  box.append(copy);
  return box;
}

function noteReference(id, references) {
  const note = references.byId.get(id);
  let text = t("Note unavailable");
  if (inScope(note, references.language)) {
    const subject = subjectText(note.subject, references, note);
    if (note.subject.kind === "research") return researchReference(id, subject);
    text = note.subject.kind === "wording_rule" ? shortened(subject)
      : [subject, shortened(firstStatement(note, references.summaries))].filter(Boolean).join(" · ");
  }
  return el("span", { title: id, textContent: text });
}

/** Research references retain names; web addresses retain links. */
function address(value, references) {
  if (references && (value.startsWith("research:") || references.subjects[value] || references.research.has(value))) {
    return subjectReference({ kind: "research", id: value }, references);
  }
  try {
    const url = new URL(value);
    if (["http:", "https:"].includes(url.protocol)) {
      return el("a", { href: url.href, rel: "noopener noreferrer", textContent: value });
    }
  } catch {}
  return value;
}

function verification(value) {
  return field(N_("Verification"), t(VERIFICATIONS[value] ?? value));
}

function renderSource(source, references) {
  const box = el("li", {},
    field(N_("Name"), source.name), field(N_("Kind"), t(KINDS[source.kind] ?? source.kind)),
    field(N_("Address"), address(source.address, references)),
    field(N_("Locator"), source.locator ?? t("not recorded")),
    field(N_("Retrieved"), source.retrieved ?? t("not recorded")), verification(source.verification));
  if (source.relation) box.append(field(N_("Relation"), t(RELATIONS[source.relation] ?? source.relation)));
  if (source.quoted_term) box.append(field(N_("Quoted term"), source.quoted_term));
  if (source.quote) box.append(field(N_("Quotation"), source.quote));
  return box;
}

function renderEvidence(evidence, references) {
  const box = el("li", {}, field(N_("Method"), evidence.method), verification(evidence.verification));
  for (const location of evidence.locations ?? []) {
    const numeric = ["rom_offset", "cpu_address"].includes(location.kind);
    const value = numeric ? `0x${location.value.toString(16).toUpperCase().padStart(8, "0")}` : location.value;
    box.append(field(KINDS[location.kind] ?? location.kind,
      `${location.label ? `${location.label}: ` : ""}${value}`));
  }
  for (const measurement of evidence.measurements ?? []) {
    box.append(field(N_("Measurement"), `${measurement.name}: ${measurement.value} ${measurement.unit}`));
  }
  for (const subject of evidence.related_subjects ?? []) box.append(field(N_("Related subject"), subjectReference(subject, references)));
  for (const capture of evidence.captures ?? []) {
    box.append(field(N_("Capture"), address(capture.address)));
    if (capture.locator) box.append(field(N_("Locator"), capture.locator));
    if (capture.sha256) box.append(field(N_("SHA-256"), capture.sha256));
  }
  return box;
}

function renderRecord(note, references) {
  const status = note.status === "unreviewed" && note.author ? N_("Unreviewed") : STATUSES[note.status];
  const box = el("article", { class: "structured-note", "data-note-id": note.id },
    el("header", { class: "note-heading" },
      el("b", { textContent: note.scope.kind === "shared" ? t("Shared game notes")
        : t("Language: {language}", { language: note.scope.language }) }),
      el("span", { class: "mark", textContent: t("Review: {status}", { status: t(status ?? note.status) }) })),
    field(N_("Subject"), subjectReference(note.subject, references, note)));
  for (const [key, label] of [["guidance", N_("Guidance")], ["facts", N_("Facts")], ["reason", N_("Reason")]]) {
    const items = noteItems(note, key, references.summaries);
    if (items.length) box.append(el("section", {}, el("b", { textContent: t(label) }),
      el("ul", {}, ...items.map((text) => el("li", { textContent: text })))));
  }
  box.append(el("section", {}, el("b", { textContent: t("Sources") }),
    note.sources?.length ? el("ul", {}, ...note.sources.map((source) => renderSource(source, references)))
      : el("div", { class: "muted", textContent: t("not recorded") })));
  if (note.evidence?.length) box.append(el("details", {}, el("summary", { textContent: t("Evidence") }),
    el("ul", {}, ...note.evidence.map((evidence) => renderEvidence(evidence, references)))));
  for (const subject of note.related_subjects ?? []) box.append(field(N_("Related subject"), subjectReference(subject, references)));
  for (const id of note.related_notes ?? []) box.append(field(N_("Related note"), noteReference(id, references)));
  for (const [key, label] of [["author", N_("Author")], ["created", N_("Created")], ["updated", N_("Updated")],
    ["reviewed_by", N_("Reviewed by")], ["reviewed_at", N_("Reviewed at")]]) {
    if (note[key]) box.append(field(label, note[key]));
  }
  return box;
}

const indexes = new WeakMap();

/** Index canonical notes once per loaded collection. */
function noteIndex(collection) {
  if (!indexes.has(collection)) {
    const byId = new Map(), research = new Map(), wording = new Map();
    for (const note of collection.note) {
      byId.set(note.id, note);
      if (note.subject.kind === "research") {
        const rows = research.get(note.subject.id) ?? [];
        rows.push(note);
        research.set(note.subject.id, rows);
      }
      if (note.subject.kind === "wording_rule") {
        const rows = wording.get(note.subject.id) ?? [];
        rows.push(note);
        wording.set(note.subject.id, rows);
      }
    }
    indexes.set(collection, { byId, research, wording });
  }
  return indexes.get(collection);
}

function noteReferences(collection, language, subjects = {}) {
  return { ...(collection?.schema === "3.0.0" ? noteIndex(collection)
    : { byId: new Map(), research: new Map(), wording: new Map() }), language, subjects, summaries: collection?.summaries };
}

/** Generated references contain no independent term prose. */
export function isResearchReference(note) {
  if (note?.subject?.kind !== "term") return false;
  const methods = (note.evidence ?? []).map((item) => item.method);
  const facts = (note.facts ?? []).filter((text) =>
    !methods.includes("research-assembly") || text !== "Terminology follows the linked research record.");
  return methods.some((method) => ["research-citation", "research-assembly"].includes(method))
    && !facts.length && !note.guidance?.length && !note.reason?.length;
}

/** Unknown references keep legacy edit protection. */
export function hasIndependentNotes(ids = [], collection) {
  const records = collection?.schema === "3.0.0" ? noteIndex(collection).byId : new Map();
  return ids.some((id) => !isResearchReference(records.get(id)));
}

/** Identical summaries retain every distinct row reference. */
function researchList(notes, references) {
  const lines = new Map();
  for (const note of notes) {
    const subject = note.related_subjects?.find((subject) => subject.kind === "research");
    if (!subject) continue;
    let facts = noteItems(note, "facts", references.summaries);
    if (!facts.length || facts.every((text) => text === "Terminology follows the linked research record.")) {
      facts = (references.research.get(subject.id) ?? []).filter((record) => inScope(record, references.language)
        && ["unreviewed", "accepted"].includes(record.status))
        .flatMap((record) => ["guidance", "facts", "reason"].flatMap((key) => record[key] ?? []));
    }
    const row = references.subjects[subject.id];
    const text = facts.join(" · ") || [row?.table, row?.name].filter(Boolean).join(" · ") || t("not recorded");
    if (!lines.has(text)) lines.set(text, new Set());
    lines.get(text).add(subject.id);
  }
  if (!lines.size) return null;
  return el("div", { class: "research-references" },
    el("ul", { class: "research-reference-list" }, ...[...lines].map(([text, ids]) => el("li", {},
      text, ...[...ids].flatMap((id, index) => [" ", researchReference(id, "",
        { icon: true, number: ids.size > 1 ? index + 1 : null })])))));
}

/** References occupy their own glossary value. */
export function renderResearchReferences(ids = [], collection, language, subjects = {}) {
  const references = noteReferences(collection, language, subjects);
  const generated = ids.map((id) => references.byId.get(id))
    .filter((note) => inScope(note, language) && isResearchReference(note));
  return researchList(generated, references);
}

/** Collapse only cards exceeding two viewport heights. */
export function limitResearchList(card, height) {
  if (!card || !(height > 0) || !(card.scrollHeight > 2 * height)) return;
  for (const list of card.querySelectorAll(".research-reference-list")) {
    const rows = [...list.children];
    if (rows.length <= 8 || rows.some((row) => row.hidden)) continue;
    for (const row of rows.slice(8)) row.hidden = true;
    const more = el("button", { type: "button", class: "btn sm", textContent: t("Show all {n}", { n: rows.length }),
      on: { click: () => { for (const row of rows) row.hidden = false; more.hidden = true; } } });
    list.parentNode.append(more);
  }
}

function activeNotes(ids, collection, language) {
  if (!ids.length || collection?.schema !== "3.0.0") return null;
  const records = ids.map((id) => noteIndex(collection).byId.get(id)).filter(Boolean);
  if (!records.length) return null;
  return records.filter((note) => ["unreviewed", "accepted"].includes(note.status) && inScope(note, language));
}

/** List summaries omit metadata but preserve statement order. */
export function notesStatements(ids = [], collection, language) {
  return activeNotes(ids, collection, language)?.flatMap((note) =>
    ["guidance", "facts", "reason"].flatMap((key) => noteItems(note, key, collection.summaries))).join("\n") ?? null;
}

/** Preserve searchable metadata and its original field order. */
export function notesText(ids = [], collection, language) {
  const records = activeNotes(ids, collection, language);
  if (!records) return null;
  const details = (value) => {
    if (Array.isArray(value)) return value.map(details).join("\n");
    if (value && typeof value === "object") return Object.entries(value)
      .map(([key, item]) => `${key.replaceAll("_", " ")}: ${details(item)}`).join("; ");
    return String(value);
  };
  return records.map((note) => {
    const prose = ["guidance", "facts", "reason"].flatMap((key) => noteItems(note, key, collection.summaries));
    const metadata = Object.fromEntries(["status", "sources", "evidence", "related_notes", "related_subjects",
      "author", "created", "updated", "reviewed_by", "reviewed_at"]
      .filter((key) => key in note).map((key) => [key, note[key]]));
    return [...prose, details(metadata)].join("\n");
  }).join("\n");
}

/** Related findings retain their own provenance and review state. */
export function renderNotes(ids = [], collection, language, subjects = {}, { includeResearch = true } = {}) {
  if (!ids.length || collection?.schema !== "3.0.0") return null;
  const references = noteReferences(collection, language, subjects);
  const { byId, research } = references;
  const allowed = (note) => inScope(note, language);
  const direct = [...new Set(ids)].map((id) => byId.get(id)).filter(allowed);
  if (!direct.length) return null;
  const generated = direct.filter(isResearchReference), independent = direct.filter((note) => !isResearchReference(note));
  if (!independent.length && !includeResearch) return null;
  const seen = new Set(direct.map((note) => note.id)), queue = [...independent], related = [];
  for (let i = 0; i < queue.length; i++) {
    const note = queue[i];
    const references = [...(note.related_notes ?? []).map((id) => byId.get(id)),
      ...(note.related_subjects ?? []).filter((subject) => subject.kind === "research")
        .flatMap((subject) => research.get(subject.id) ?? []),
      ...(note.sources ?? []).flatMap((source) => research.get(source.address) ?? [])];
    for (const target of references) {
      if (!allowed(target) || seen.has(target.id) || isResearchReference(target)) continue;
      seen.add(target.id);
      queue.push(target);
      related.push(target);
    }
  }
  const record = (note) => renderRecord(note, references);
  const list = includeResearch ? researchList(generated, references) : null;
  return el("div", { class: "structured-notes" }, ...independent.map(record),
    related.length ? el("details", {}, el("summary", { textContent: t("Related notes") }), ...related.map(record)) : null,
    list ? el("section", {}, el("h3", { class: "ctx-subtitle", textContent: t("Research references") }), list) : null);
}

/** Keep unmigrated guidance beside canonical entry notes. */
export function renderEntryNotes(entry, index) {
  const notes = renderNotes(entry.note_ids, index.notes, index.language, index.research);
  const records = notes ? (entry.note_ids ?? []).map((id) => noteIndex(index.notes).byId.get(id))
    .filter((note) => note?.subject.kind === "entry") : [];
  const shared = records.some((note) => note.scope.kind === "shared");
  const wording = records.some((note) => note.scope.language === index.language);
  return el("div", {}, notes, shared ? null : renderGuidance(entry.translator_notes),
    wording ? null : el("div", { class: "source-note" }, el("b", { textContent: `${t("Source note")}: ` }),
      entry.note || t("none")),
    wording ? null : el("div", { class: "muted small", textContent: t("The source note is read-only.") }));
}

/** Shared source guidance, rendered as text. */
export function renderGuidance(notes = []) {
  if (!notes.length) return null;
  return el("section", { class: "translator-notes" },
    el("b", { textContent: t("Translator notes") }),
    ...notes.map((note) => el("div", { class: "ctx-item" },
      el("div", { class: "comments", textContent: note.text }),
      el("div", { class: "muted", textContent: t("Evidence: {evidence}", { evidence: note.evidence }) }))));
}

const CONTENT_FIELDS = [["guidance", N_("Guidance")], ["facts", N_("Facts")], ["reason", N_("Reason")]];
const SOURCE_FIELDS = [["name", N_("Name")], ["address", N_("Address")], ["locator", N_("Locator")],
  ["retrieved", N_("Retrieved")], ["quoted_term", N_("Quoted term")], ["quote", N_("Quotation")]];

const noteButton = (label, click) => el("button", { type: "button", class: "btn sm", textContent: t(label), on: { click } });
const noteInput = (label, control) => el("label", { class: "note-input" }, el("span", { textContent: t(label) }), control);

/** Draft controls leave published guidance and translations untouched. */
export function renderNoteDrafts(subject, binding, index, edits, languages = []) {
  if (!edits) return null;
  const references = noteReferences(index.notes, index.language, index.research);
  const box = el("section", { class: "note-drafts", "data-key": `note-drafts:${subject.kind}:${subject.id}` });
  const records = (index.notes?.note ?? []).filter((note) => note.subject.kind === subject.kind && note.subject.id === subject.id
    && (note.scope.kind === "shared" || note.scope.language === index.language) && !isResearchReference(note));
  const byId = new Map(records.map((note) => [note.id, note]));
  const scope = { kind: "language", language: index.language };
  const refresh = (open = null) => {
    box.replaceChildren(el("h3", { class: "ctx-subtitle", textContent: t("Note drafts") }),
      el("p", { class: "muted small", textContent: t("Note drafts stay in this browser. They become guidance only after review and merge.") }));
    const add = noteButton(N_("Add note"), () => {
      const id = edits.set(subject, binding, { scope });
      refresh(id);
    });
    add.disabled = subject.kind === "entry" && !binding?.source_sha256;
    box.append(add);
    if (add.disabled) box.append(el("p", { class: "muted", textContent: t("The entry needs its current English digest.") }));
    const drafts = edits.forSubject(subject).filter((draft) => !isResearchReference(draft.note) && !isResearchReference(draft.base));
    for (const id of new Set([...records.map((note) => note.id), ...drafts.map((draft) => draft.note.id)])) {
      const current = byId.get(id) ?? null, draft = edits.get(id);
      const panel = el("details", { class: "note-draft", "data-key": id });
      panel.open = open === id || Boolean(draft);
      panel.append(el("summary", { title: id, textContent: draft ? t("Draft note") : t("Suggest edit") }));
      panel.append(noteForm(subject, binding, index, edits, current, draft, id, languages, refresh, references));
      box.append(panel);
    }
  };
  refresh();
  return box;
}

function noteForm(subject, binding, index, edits, current, draft, id, languages, refresh, references) {
  if (draft?.operation === "remove") {
    const conflict = edits.conflict(id, current, binding);
    const otherLanguage = !current && draft.base.scope.kind === "language" && draft.base.scope.language !== index.language;
    return el("div", { class: "note-form" },
      el("p", { textContent: t("Removal proposed. Published guidance stays available until review and merge.") }),
      renderRecord(draft.base, references),
      noteInput(N_("Message to reviewers"), el("textarea", { rows: 2, value: draft.message,
        on: { input: (event) => edits.proposeRemoval(draft.base, event.target.value) } })),
      conflict ? el("p", { class: "error", textContent: t("The published note or entry changed. Compare the versions before keeping your draft.") }) : null,
      otherLanguage ? el("p", { textContent: t("Open the original language to compare this note: {language}",
        { language: draft.base.scope.language }) }) : null,
      conflict && current ? renderRecord(current, references) : null,
      conflict && !otherLanguage ? noteButton(N_("Keep draft against current version"), () => refresh(edits.rebase(id, current, binding))) : null,
      noteButton(N_("Revert note draft"), () => { edits.remove(id); refresh(); }));
  }
  const note = draft?.note ?? current;
  const values = structuredClone(note), message = { value: draft?.message ?? "" };
  for (const [key] of CONTENT_FIELDS) values[key] ??= [""];
  const form = el("div", { class: "note-form" });
  const status = el("div", { role: "status", "aria-live": "polite" });
  const problems = el("ul", { class: "warn-line" });
  const scopeHint = el("p", { class: "muted small" });
  const conflicts = el("div", { class: "note-conflict" });
  const updateStatus = () => {
    const saved = edits.get(id);
    status.textContent = !edits.saved ? t("Storage is unavailable. Keep this page open to retain note drafts.")
      : saved ? t("Note draft saved in this browser.") : t("No note changes.");
    problems.replaceChildren(...noteProblems(values, saved?.base ?? current, saved?.attachments).map((text) => el("li", { textContent: t(text) })));
    scopeHint.textContent = values.scope.kind === "shared"
      ? t("Shared game fact · applies to all language projects: {languages}", { languages: languages.join(", ") || index.language })
      : t("Language: {language}", { language: index.language });
    conflicts.replaceChildren();
    if (edits.conflict(id, current, binding)) {
      const otherLanguage = !current && saved.base?.scope.kind === "language" && saved.base.scope.language !== index.language;
      conflicts.append(el("p", { class: "error", textContent: t("The published note or entry changed. Compare the versions before keeping your draft.") }));
      if (otherLanguage) conflicts.append(el("p", { textContent: t("Open the original language to compare this note: {language}",
        { language: saved.base.scope.language }) }));
      if (subject.kind === "entry") conflicts.append(
        field(N_("Saved English SHA-256"), saved.note.binding?.source_sha256 ?? t("not recorded")),
        field(N_("Current English SHA-256"), binding?.source_sha256 ?? t("not recorded")));
      for (const [label, record] of [[N_("Saved base"), saved.base], [N_("Current note"), current], [N_("Your draft"), saved.note]]) {
        conflicts.append(el("details", {}, el("summary", { textContent: t(label) }),
          record ? renderRecord(record, references) : el("p", { textContent: t("No note") })));
      }
      if (!otherLanguage && (subject.kind !== "entry" || binding?.source_sha256)) {
        conflicts.append(noteButton(N_("Keep draft against current version"), () => refresh(edits.rebase(id, current, binding))));
      }
    }
  };
  const persist = () => {
    const fields = { ...values };
    for (const [key] of CONTENT_FIELDS) fields[key] = (values[key] ?? []).filter((text) => text.trim());
    edits.set(subject, binding, fields, current, id, message.value);
    updateStatus();
  };
  const select = el("select", { "aria-label": t("Note scope"), on: { change: (event) => {
    values.scope = event.target.value === "shared" ? { kind: "shared" } : { kind: "language", language: index.language };
    persist();
  } } }, el("option", { value: "language", textContent: t("Language: {language}", { language: index.language }) }),
  el("option", { value: "shared", textContent: t("Shared game fact") }));
  select.value = values.scope.kind;
  form.append(noteInput(N_("Note scope"), select), scopeHint);
  for (const [key, label] of CONTENT_FIELDS) {
    const rows = el("div", { class: "note-items" });
    const draw = () => rows.replaceChildren(...(values[key] ?? []).map((value, number) => el("div", {},
      noteInput(label, el("textarea", { rows: 3, value, on: { input: (event) => { values[key][number] = event.target.value; persist(); } } })),
      noteButton(N_("Remove item"), () => { values[key].splice(number, 1); persist(); draw(); }))));
    draw();
    form.append(el("section", {}, el("b", { textContent: t(label) }), rows,
      noteButton(N_("Add item"), () => { (values[key] ??= []).push(""); draw(); })));
  }
  const sources = el("div", { class: "note-items" });
  const drawSources = () => {
    sources.replaceChildren(...(values.sources ?? []).map((source, number) => {
      const row = el("fieldset", {}, el("legend", { textContent: t("Source {n}", { n: number + 1 }) }));
      const kind = el("select", { on: { change: (event) => { source.kind = event.target.value; persist(); } } },
        ...["web", "publication", "rom", "project"].map((value) => el("option", { value, textContent: t(KINDS[value]) })));
      kind.value = source.kind;
      row.append(noteInput(N_("Kind"), kind));
      for (const [key, label] of SOURCE_FIELDS) {
        const input = key === "quote" ? el("textarea", { rows: 3 }) : el("input", { type: key === "retrieved" ? "date" : "text" });
        input.value = source[key] ?? "";
        input.addEventListener("input", (event) => {
          if (event.target.value) source[key] = event.target.value;
          else delete source[key];
          persist();
        });
        row.append(noteInput(label, input));
      }
      const relation = el("select", { on: { change: (event) => {
        if (event.target.value) source.relation = event.target.value;
        else delete source.relation;
        persist();
      } } }, el("option", { value: "", textContent: t("(none)") }),
      ...Object.entries(RELATIONS).map(([value, label]) => el("option", { value, textContent: t(label) })));
      relation.value = source.relation ?? "";
      row.append(noteInput(N_("Relation"), relation), noteButton(N_("Remove source"), () => {
        values.sources.splice(number, 1); persist(); drawSources();
      }));
      return row;
    }));
  };
  drawSources();
  form.append(el("section", {}, el("b", { textContent: t("Sources") }), sources,
    el("p", { class: "muted small", textContent: t("Cite the exact term or quotation. For an original rendering, cite its meaning or context as a project proposal.") }),
    noteButton(N_("Add source"), () => {
      (values.sources ??= []).push({ kind: "web", name: "", address: "", verification: "unresolved" });
      persist(); drawSources();
    })), evidenceForm(edits, id, values, current, persist, updateStatus, refresh, references),
  noteInput(N_("Message to reviewers"), el("textarea", { rows: 2, value: message.value,
    on: { input: (event) => { message.value = event.target.value; persist(); } } })),
  status, problems, conflicts, noteButton(N_("Revert note draft"), () => { edits.remove(id); refresh(); }));
  if (current) form.append(noteButton(N_("Suggest removal"), () => {
    edits.proposeRemoval(current, message.value); refresh(id);
  }));
  updateStatus();
  return form;
}

/** Evidence images retain context without changing graphic pixels. */
function evidenceForm(edits, id, values, current, persist, updateStatus, refresh, references) {
  const rows = el("div", { class: "note-captures" });
  const uploadStatus = el("p", { role: "status", "aria-live": "polite" });
  const metadata = { role: "original", build: "", context: "" };
  const metadataFields = (item, change) => {
    const role = el("select", { on: { change: (event) => { item.role = event.target.value; change(); } } },
      ...Object.entries(CAPTURE_ROLES).map(([value, label]) => el("option", { value, textContent: t(label) })));
    role.value = item.role;
    return [noteInput(N_("Version label"), role),
      ...[["build", N_("Build identity")], ["context", N_("Screen context")]].map(([key, label]) =>
        noteInput(label, el("input", { type: "text", value: item[key], on: { input: (event) => {
          item[key] = event.target.value; change();
        } } })))];
  };
  const sync = () => {
    values.evidence = edits.get(id)?.note.evidence ?? current?.evidence ?? [];
    updateStatus();
  };
  const draw = () => {
    const attachments = edits.get(id)?.attachments ?? [];
    const order = Object.keys(CAPTURE_ROLES);
    attachments.sort((a, b) => a.context.localeCompare(b.context) || order.indexOf(a.role) - order.indexOf(b.role));
    rows.replaceChildren(...attachments.map((attachment) => {
      const label = `${t(CAPTURE_ROLES[attachment.role] ?? "Capture")} · ${attachment.build} · ${attachment.context}`;
      const caption = el("figcaption", { textContent: label });
      const image = ["image/png", "image/jpeg"].includes(attachment.type) && /^[A-Za-z0-9+/]+={0,2}$/.test(attachment.data)
        ? el("img", { src: `data:${attachment.type};base64,${attachment.data}`, alt: label }) : null;
      return el("fieldset", {}, el("legend", { textContent: attachment.name }),
        el("figure", {}, image, caption), field(N_("SHA-256"), attachment.sha256),
        field(N_("Bytes"), String(attachment.size)), ...metadataFields(attachment, () => {
          edits.putAttachment(id, attachment);
          caption.textContent = `${t(CAPTURE_ROLES[attachment.role])} · ${attachment.build} · ${attachment.context}`;
          if (image) image.alt = caption.textContent;
          sync();
        }), noteButton(N_("Remove evidence attachment"), () => {
          edits.removeAttachment(id, attachment.id); sync(); draw();
        }));
    }));
  };
  draw();
  const file = el("input", { type: "file", accept: "image/png,image/jpeg", on: { change: async (event) => {
    const upload = event.target.files?.[0];
    event.target.value = "";
    if (!upload) return;
    persist();
    file.disabled = true;
    uploadStatus.textContent = t("Reading evidence attachment…");
    try {
      if (await edits.attach(id, upload, { ...metadata }, current)) refresh(id);
      uploadStatus.textContent = "";
    } catch (error) {
      uploadStatus.textContent = errorText(error);
    } finally { file.disabled = false; }
  } } });
  return el("section", {}, el("h3", { class: "ctx-subtitle", textContent: t("Evidence attachments") }),
    el("p", { class: "muted small", textContent: t("Captures document a note; they do not replace graphics. Use matching screen context for original and current, proposed or failed captures, with build versions.") }),
    values.evidence?.length ? el("details", {}, el("summary", { textContent: t("Recorded evidence") }),
      el("ul", {}, ...values.evidence.map((evidence) => renderEvidence(evidence, references)))) : null,
    rows, el("fieldset", {}, el("legend", { textContent: t("Attach a capture") }), ...metadataFields(metadata, () => {}),
      noteInput(N_("Evidence image"), file)), uploadStatus);
}
