// Submission of edits as a prefilled GitHub issue.
// A small submission travels in the link itself.
// A larger one is downloaded, then attached.

import { REPOSITORY, TOOLKIT_REPOSITORY } from "./registry.js";
import { EXPORT_VERSION, NOTE_CHANGES_SCHEMA, noteProblems, noteRevision } from "./store.js";
import { t } from "./ui.js";

export const TEMPLATE = "submission.yml";
export const LINK_LIMIT = 8000; // browsers and GitHub refuse longer links

/** Whether `repository` (account/name) can receive submissions. */
export function submitAvailable(repository) {
  return typeof repository === "string" && REPOSITORY.test(repository);
}

/** The repository that receives an export. */
export function submissionRepository(doc, repository) {
  if (doc.profile === "site") return TOOLKIT_REPOSITORY;
  const carried = gameChanges(doc);
  if (!carried && (doc.glyphs?.length || doc.keyboard)) return TOOLKIT_REPOSITORY;
  return repository;
}

const gameChanges = (doc) => doc.entries.length + (doc.terms?.length ?? 0) + (doc.pictures?.length ?? 0)
  + (doc.engine_rules?.length ?? 0) + (doc.note_changes?.length ?? 0);

/** Each repository receives only its own changes. */
export function submissionParts(doc, repository) {
  doc = { ...doc, version: EXPORT_VERSION };
  if (doc.profile !== "site" && gameChanges(doc) && (doc.glyphs?.length || doc.keyboard)) {
    const { glyphs, keyboard, ...game } = doc;
    return [{ repository, doc: game }, { repository: TOOLKIT_REPOSITORY,
      doc: { version: EXPORT_VERSION, language: doc.language, profile: doc.profile, entries: [],
        ...(glyphs?.length ? { glyphs } : {}), ...(keyboard ? { keyboard } : {}),
        ...(doc.notes ? { notes: doc.notes } : {}) } }];
  }
  return [{ repository: submissionRepository(doc, repository), doc }];
}

/** Incomplete notes remain downloadable drafts. */
export function submissionProblem(doc) {
  if (!doc.note_changes?.length) return "";
  if (doc.note_changes_schema !== NOTE_CHANGES_SCHEMA) return t("Unsupported note changes schema.");
  for (const change of doc.note_changes) {
    if (!["add", "edit", "remove"].includes(change.operation)
      || change.base_revision !== noteRevision(change.base)
      || (change.operation === "add") !== (change.base === null)
      || (change.operation === "remove" ? change.proposed !== null : !change.proposed)) {
      return t("Invalid note change: {id}", { id: change.id });
    }
    if (change.operation !== "remove") {
      const problems = noteProblems(change.proposed, change.base, change.attachments);
      if (problems.length) return t("Complete the note draft before submitting: {id}", { id: change.id });
    }
  }
  return "";
}

/** The keyboard changes an issue document carries. */
export function keyboardChanges(keyboard) {
  if (!keyboard) return 0;
  return Object.keys(keyboard.rows ?? {}).length + (keyboard.pages ? 1 : 0);
}

/** The submitted document built from the export's edits. */
export function submissionDoc(doc, base = () => undefined, sha256 = () => undefined) {
  const entries = doc.entries.map(({ id, translation, state, note, base: baseline, base_state }) => {
    const entry = { id };
    const was = baseline ?? base(id);
    if (typeof was === "string") entry.base = was;
    entry.translation = translation;
    if (state !== undefined) entry.state = state;
    if (base_state !== undefined) entry.base_state = base_state;
    if (note) entry.note = note;
    return entry;
  });
  const sub = { version: EXPORT_VERSION, language: doc.language, profile: doc.profile, entries };
  if (doc.engine_rules?.length) sub.engine_rules = doc.engine_rules;
  if (doc.note_changes?.length) {
    sub.note_changes_schema = doc.note_changes_schema;
    sub.note_changes = structuredClone(doc.note_changes);
  }
  if (doc.terms?.length) sub.terms = doc.terms;
  const pictures = (doc.pictures ?? []).filter((p) => sha256(p.id))
    .map(({ id, base, areas, tiles }) => ({ id, base, areas, ...(tiles == null ? {} : { tiles }), sha256: sha256(id) }));
  if (pictures.length) sub.pictures = pictures;
  // A pack-only export is the issue's document.
  const carried = gameChanges(doc);
  if (!carried) {
    if (doc.glyphs?.length) sub.glyphs = doc.glyphs;
    if (doc.keyboard) sub.keyboard = doc.keyboard;
  }
  const notes = typeof doc.notes === "string" ? doc.notes.trim() : "";
  if (notes) sub.notes = notes;
  return sub;
}

/** The issue link; `attach` when the document does not fit. */
export function issueLink(sub, repository, limit = LINK_LIMIT) {
  const n = sub.entries.length, terms = sub.terms?.length ?? 0, pictures = sub.pictures?.length ?? 0;
  const glyphs = sub.glyphs?.length ?? 0, keyboard = keyboardChanges(sub.keyboard);
  const rules = sub.engine_rules?.length ?? 0, notes = sub.note_changes?.length ?? 0;
  const counts = [n ? `${n} ${n === 1 ? "entry" : "entries"}` : "",
    terms ? `${terms} ${terms === 1 ? "term" : "terms"}` : "",
    pictures ? `${pictures} ${pictures === 1 ? "picture" : "pictures"}` : "",
    rules ? `${rules} engine ${rules === 1 ? "rule" : "rules"}` : "",
    glyphs ? `${glyphs} ${glyphs === 1 ? "glyph" : "glyphs"}` : "",
    keyboard ? `${keyboard} keyboard ${keyboard === 1 ? "change" : "changes"}` : "",
    notes ? `${notes} note ${notes === 1 ? "change" : "changes"}` : ""].filter(Boolean).join(", ");
  const title = `Editor submission: ${sub.profile}/${sub.language}, ${counts}`;
  const form = `https://github.com/${repository}/issues/new?template=${TEMPLATE}&title=${encodeURIComponent(title)}`;
  const inline = `${form}&export=${encodeURIComponent(JSON.stringify(sub))}`;
  const evidence = sub.note_changes?.some((change) => change.attachments?.length);
  return !evidence && inline.length <= limit ? { url: inline, attach: false } : { url: form, attach: true };
}

/**
 * Submit the changed entries, terms and pictures of an export.
 * Returns {ok, message, attach} or {ok: false, reason}.
 */
export function submitChanges(doc, repository, { base, sha256, download, picture, open }) {
  const problem = submissionProblem(doc);
  if (problem) return { ok: false, reason: problem };
  const target = submissionRepository(doc, repository);
  if (!submitAvailable(target)) {
    return { ok: false, reason: t("This site names no repository for submissions: export the changes as JSON instead.") };
  }
  const sub = submissionDoc(doc, base, sha256);
  const n = sub.entries.length + (sub.terms?.length ?? 0) + (sub.pictures?.length ?? 0)
    + (sub.glyphs?.length ?? 0) + keyboardChanges(sub.keyboard) + (sub.engine_rules?.length ?? 0)
    + (sub.note_changes?.length ?? 0);
  if (!n) return { ok: false, reason: t("No text, term, picture, glyph, keyboard, engine rule or note changes to submit.") };
  const link = issueLink(sub, target);
  let message = n === 1
    ? t("The issue form on {repository} opened with {n} change: submit it there with a GitHub account.", { repository: target, n })
    : t("The issue form on {repository} opened with {n} changes: submit it there with a GitHub account.", { repository: target, n });
  if (link.attach) {
    const name = download(sub);
    message = sub.note_changes?.some((change) => change.attachments?.length)
      ? t("The notes include evidence images: attach the downloaded {name} to the Export file field of the issue form on {repository}, then submit it with a GitHub account.",
        { name, repository: target })
      : t("The changes are too long for a link: attach the downloaded {name} to the Export file field of the issue form on {repository}, then submit it with a GitHub account.",
        { name, repository: target });
  }
  const files = (sub.pictures ?? []).map((p) => picture(p.id));
  open(link.url);
  if (files.length) {
    message += ` ${t("Before submitting, attach the downloaded {names} to the Pictures field: the issue states the SHA-256 of each.",
      { names: files.join(", ") })}`;
  }
  if ((doc.pictures?.length ?? 0) > files.length) {
    message += ` ${t("The pictures travel in the JSON file.")}`;
  }
  if ((doc.glyphs?.length ?? 0) > (sub.glyphs?.length ?? 0)) {
    message += ` ${t("The glyph redraws travel in the JSON file.")}`;
  }
  if (doc.keyboard && !sub.keyboard) message += ` ${t("The keyboard changes travel in the JSON file.")}`;
  return { ok: true, message, attach: link.attach };
}
