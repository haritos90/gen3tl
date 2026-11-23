// Store browser edits separately by language and profile.
// Unavailable storage leaves edits in memory only.

import { N_, uiError } from "./ui.js";
import { draftValue, translationState } from "./draft.js";
import { digest } from "./patcher-core.js";

/** Return usable browser storage, or null when refused. */
export function browserStorage() {
  try {
    const s = globalThis.localStorage;
    const k = "gen3tl-editor:probe";
    s.setItem(k, "1");
    s.removeItem(k);
    return s;
  } catch {
    return null;
  }
}

export class WorkspaceSettings {
  constructor(storage, lang, profile) {
    this.storage = storage;
    this.key = `gen3tl-editor:workspace:${lang}:${profile}`;
    this.referenceLanguage = "";
    try {
      const saved = JSON.parse(storage?.getItem(this.key) ?? "null");
      if (typeof saved?.referenceLanguage === "string") this.referenceLanguage = saved.referenceLanguage;
    } catch {
      // Invalid preferences keep the default.
    }
  }

  setReferenceLanguage(code) {
    this.referenceLanguage = code;
    try {
      if (!this.storage) return false;
      this.storage.setItem(this.key, JSON.stringify({ referenceLanguage: code }));
      return true;
    } catch {
      return false;
    }
  }
}

/** Object ordering must not create note conflicts. */
export function noteRevision(value) {
  const ordered = (item) => Array.isArray(item) ? item.map(ordered)
    : item && typeof item === "object" ? Object.fromEntries(Object.keys(item).sort().map((key) => [key, ordered(item[key])])) : item;
  return JSON.stringify(ordered(value));
}

const noteText = (value) => typeof value === "string" && Boolean(value.trim());
const sameNote = (a, b) => noteRevision(a) === noteRevision(b);
const noteScope = (scope, language) => scope?.kind === "shared"
  || scope?.kind === "language" && scope.language === language;
const NOTE_FIELDS = ["guidance", "facts", "reason", "sources", "scope", "evidence"];
const NOTE_KINDS = ["entry", "term", "graphic"];
export const NOTE_CHANGES_SCHEMA = "3.0.0";
export const CAPTURE_LIMIT = 3 * 1024 * 1024; // its base64 fits the 5 MiB export
export const CAPTURE_ROLES = {
  original: N_("Original"), current: N_("Released/current"), proposed: N_("Proposed"), failed: N_("Failed experiment"),
};
const captureAddress = (attachment) => `attachment:${attachment.id}`;
const captureLocator = (attachment) => `${attachment.role} · ${attachment.build} · ${attachment.context}`;
const capturesOf = (note) => (note.evidence ?? []).flatMap((evidence) => evidence.captures ?? []);

/** Incomplete citations remain drafts until resolved. */
export function noteProblems(note, base = null, attachments = []) {
  const problems = [];
  if (!["guidance", "facts", "reason"].some((key) => note[key]?.some(noteText))) {
    problems.push(N_("Add guidance, a fact or a reason."));
  }
  if (note.subject.kind === "entry" && !/^[a-f0-9]{64}$/.test(note.binding?.source_sha256 ?? "")) {
    problems.push(N_("The entry needs its current English digest."));
  }
  if (note.subject.kind === "graphic" && !sameNote(note.binding, note.subject)) {
    problems.push(N_("The picture needs its current inventory identity."));
  }
  const sources = note.sources ?? [];
  for (const source of sources) {
    if (!noteText(source.name) || !noteText(source.address)) problems.push(N_("Each source needs a name and an address or document reference."));
    if (source.kind === "web") {
      if (!/^https?:\/\/[^\s]+$/.test(source.address ?? "")) problems.push(N_("Web sources need an HTTP or HTTPS address."));
      const existing = base?.sources?.some((old) => sameNote(old, source));
      const date = source.retrieved;
      const valid = typeof date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(date)
        && Number.isFinite(Date.parse(date)) && new Date(date).toISOString().slice(0, 10) === date
        && date <= new Date().toISOString().slice(0, 10);
      if (!existing && !valid) problems.push(N_("New or changed web citations need the actual retrieval date."));
    }
  }
  if (note.subject.kind === "term" && !sources.some((source) => noteText(source.name) && noteText(source.address)
    && (noteText(source.quoted_term) || noteText(source.quote)))) {
    problems.push(N_("Term claims need a named source, a reference and the supporting term or quotation."));
  }
  if ((note.scope.kind === "shared" || note.subject.kind === "graphic") && note.facts?.some(noteText) && !sources.some((source) =>
    ["rom", "project"].includes(source.kind) && noteText(source.name) && noteText(source.address) && noteText(source.locator))) {
    problems.push(N_("Game observations need a ROM or build reference and a reproducible location or screen context."));
  }
  for (const capture of capturesOf(note).filter((item) => item.address.startsWith("attachment:"))) {
    if (!attachments.some((item) => capture.address === captureAddress(item) && capture.sha256 === item.sha256)) {
      problems.push(N_("An evidence attachment is missing. Attach the capture again."));
    }
  }
  for (const attachment of attachments) {
    if (!Object.hasOwn(CAPTURE_ROLES, attachment.role) || !noteText(attachment.build) || !noteText(attachment.context)) {
      problems.push(N_("Each capture needs a version label, a build identity and reproducible screen context."));
    }
    if (attachment.role !== "original" && !attachments.some((item) => item.role === "original"
      && noteText(item.build) && noteText(item.context) && item.context === attachment.context)) {
      problems.push(N_("Attach a matching original capture with the same screen context for each comparison."));
    }
  }
  return [...new Set(problems)];
}

/** Note drafts never enter translation or rename stores. */
export class NoteEdits {
  constructor(storage, language, project) {
    this.storage = storage;
    this.language = language;
    this.key = `gen3tl-editor:notes:${project}`;
    this.map = new Map();
    this.pending = new Set();
    this.attachmentEpochs = new Map();
    this.saved = true;
    try {
      const saved = JSON.parse(storage?.getItem(this.key) ?? "null");
      const migrated = saved?.schema === "1.0.0";
      if (saved?.schema === "1.0.0") {
        for (const draft of saved.drafts) {
          const valid = draft.base_revision === noteRevision(draft.base);
          migrateSourceDraft(draft);
          if (valid) draft.base_revision = noteRevision(draft.base);
        }
        saved.schema = "2.0.0";
      }
      if (saved?.schema === "2.0.0") for (const draft of saved.drafts) {
        if (typeof draft.note?.id === "string" && NOTE_KINDS.includes(draft.note.subject?.kind)
          && typeof draft.note.subject.id === "string" && draft.note.scope
          && draft.base_revision === noteRevision(draft.base)) this.map.set(draft.note.id, draft);
      }
      if (migrated) storage.setItem(this.key, JSON.stringify(saved));
    } catch {
      this.saved = false;
    }
  }

  get size() {
    return [...this.map.values()].filter((draft) => noteScope(draft.note.scope, this.language)).length;
  }

  get(id) {
    const draft = this.map.get(id);
    return draft && noteScope(draft.note.scope, this.language) ? structuredClone(draft) : null;
  }

  forSubject(subject) {
    return [...this.map.values()].filter((draft) => sameNote(draft.note.subject, subject)
      && noteScope(draft.note.scope, this.language)).map((draft) => structuredClone(draft));
  }

  changes() {
    return [...this.map.values()].filter((draft) => noteScope(draft.note.scope, this.language))
      .sort((a, b) => a.note.id.localeCompare(b.note.id)).map((draft) => {
        const { note, base, base_revision, message, attachments } = structuredClone(draft);
        const operation = draft.operation === "remove" ? "remove" : base ? "edit" : "add";
        return { operation, id: note.id, subject: note.subject, scope: note.scope,
          ...(note.binding ? { binding: note.binding } : {}), base_revision, base,
          proposed: operation === "remove" ? null : note,
          ...(message ? { message } : {}), ...(attachments?.length ? { attachments } : {}) };
      });
  }

  /** Removing published guidance still requires review. */
  proposeRemoval(base, message = "") {
    const previous = this.get(base.id);
    base = previous?.base ?? base;
    if (!NOTE_KINDS.includes(base.subject.kind) || !noteScope(base.scope, this.language)) {
      throw uiError("Invalid note subject or scope.");
    }
    this.map.set(base.id, { note: structuredClone(base), base: structuredClone(base),
      base_revision: noteRevision(base), operation: "remove", message, time: new Date().toISOString() });
    this.attachmentEpochs.set(base.id, (this.attachmentEpochs.get(base.id) ?? 0) + 1);
    this.pending.add(base.id);
    return this.save();
  }

  set(subject, binding, fields, base = null, id = base?.id ?? `note:${crypto.randomUUID()}`, message = "", attachments = null) {
    if (!NOTE_KINDS.includes(subject.kind) || !noteText(subject.id)
      || subject.kind === "graphic" && !sameNote(subject, binding)
      || !noteScope(fields.scope, this.language) || base && !sameNote(base.subject, subject)) {
      throw uiError("Invalid note subject or scope.");
    }
    const previous = this.get(id);
    base = previous ? previous.base : base;
    const note = structuredClone(previous?.note ?? base ?? { id, subject, ...(binding ? { binding } : {}) });
    if (["entry", "graphic"].includes(subject.kind)) note.binding = structuredClone(previous?.note.binding ?? binding);
    for (const key of NOTE_FIELDS) {
      if (fields[key] !== undefined && (!Array.isArray(fields[key]) || fields[key].length)) note[key] = structuredClone(fields[key]);
      else delete note[key];
    }
    note.status = "proposed";
    delete note.reviewed_by;
    delete note.reviewed_at;
    if (note.sources) note.sources = note.sources.map((source) => base?.sources?.some((old) => sameNote(old, source))
      ? source : { ...source, verification: "unresolved" });
    if (note.evidence) note.evidence = note.evidence.map((evidence) => base?.evidence?.some((old) => sameNote(old, evidence))
      ? evidence : { ...evidence, verification: "unresolved" });
    attachments = structuredClone(attachments ?? previous?.attachments ?? []).filter((attachment) =>
      capturesOf(note).some((capture) => capture.address === captureAddress(attachment)));
    const unchanged = base && NOTE_FIELDS.every((key) => sameNote(note[key], base[key]));
    if (unchanged && !message) this.map.delete(id);
    else this.map.set(id, { note, base: structuredClone(base), base_revision: noteRevision(base), message,
      ...(attachments.length ? { attachments } : {}), time: new Date().toISOString() });
    this.pending.add(id);
    this.save();
    return id;
  }

  conflict(id, current, binding = null) {
    const draft = this.get(id);
    return Boolean(draft && (draft.base_revision !== noteRevision(current)
      || ["entry", "graphic"].includes(draft.note.subject.kind) && !sameNote(draft.note.binding, binding)));
  }

  /** Explicit recovery keeps proposed fields for another review. */
  rebase(id, current, binding = null) {
    const draft = this.get(id);
    if (!draft || current && !sameNote(current.subject, draft.note.subject)) throw uiError("Invalid note subject or scope.");
    if (draft.operation === "remove") {
      this.remove(id);
      if (current) this.proposeRemoval(current, draft.message);
      return current?.id ?? null;
    }
    const fields = Object.fromEntries(NOTE_FIELDS.map((key) => [key, draft.note[key]]));
    this.remove(id);
    return this.set(draft.note.subject, binding, fields, current,
      current?.id ?? (draft.base ? `note:${crypto.randomUUID()}` : id), draft.message, draft.attachments);
  }

  /** Captures stay separate from replacement artwork. */
  async attach(id, file, metadata, base = null) {
    if (!this.get(id) && !base) return false;
    const epoch = this.attachmentEpochs.get(id);
    if (file.size > CAPTURE_LIMIT) throw uiError("Evidence images must be at most 3 MB.");
    const bytes = new Uint8Array(await file.arrayBuffer());
    if (bytes.length > CAPTURE_LIMIT) throw uiError("Evidence images must be at most 3 MB.");
    const starts = (...signature) => signature.every((byte, index) => bytes[index] === byte);
    const type = starts(137, 80, 78, 71, 13, 10, 26, 10) ? "image/png"
      : starts(255, 216, 255) ? "image/jpeg" : null;
    if (!type) throw uiError("Evidence attachments must be PNG or JPEG images.");
    const sha256 = await digest(bytes, "SHA-256");
    const draft = this.get(id) ?? (base ? { note: base, base, message: "" } : null);
    if (!draft || epoch !== this.attachmentEpochs.get(id)) return false;
    let binary = "";
    for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    const attachment = { id: crypto.randomUUID(), name: file.name, type, size: bytes.length, sha256, data: btoa(binary),
      role: metadata.role, build: metadata.build, context: metadata.context };
    this.putAttachment(id, attachment, draft);
    return true;
  }

  putAttachment(id, attachment, fallback = null) {
    const draft = this.get(id) ?? fallback;
    if (!draft) return;
    const address = captureAddress(attachment);
    const attachments = (draft.attachments ?? []).filter((item) => item.id !== attachment.id);
    attachments.push(attachment);
    const evidence = this.withoutCapture(draft.note, address);
    evidence.push({ method: "screenshot", verification: "unresolved",
      captures: [{ address, sha256: attachment.sha256, locator: captureLocator(attachment) }] });
    this.set(draft.note.subject, draft.note.binding, { ...draft.note, evidence }, draft.base, id, draft.message, attachments);
  }

  withoutCapture(note, address) {
    return (note.evidence ?? []).flatMap((evidence) => {
      if (!evidence.captures?.some((capture) => capture.address === address)) return [evidence];
      const result = { ...evidence, captures: evidence.captures.filter((capture) => capture.address !== address) };
      if (!result.captures.length) delete result.captures;
      return result.method === "screenshot" && Object.keys(result).length === 2 ? [] : [result];
    });
  }

  removeAttachment(id, attachmentId) {
    const draft = this.get(id);
    if (!draft) return;
    const evidence = this.withoutCapture(draft.note, captureAddress({ id: attachmentId }));
    this.set(draft.note.subject, draft.note.binding, { ...draft.note, evidence }, draft.base, id, draft.message,
      (draft.attachments ?? []).filter((item) => item.id !== attachmentId));
  }

  remove(id) {
    this.attachmentEpochs.set(id, (this.attachmentEpochs.get(id) ?? 0) + 1);
    if (this.get(id)) {
      this.map.delete(id);
      this.pending.add(id);
    }
    return this.save();
  }

  save() {
    try {
      if (!this.storage) throw new Error("no storage");
      const stored = JSON.parse(this.storage.getItem(this.key) ?? "null");
      if (stored && (stored.schema !== "2.0.0" || !Array.isArray(stored.drafts))) throw new Error("invalid drafts");
      const merged = new Map((stored?.drafts ?? []).map((draft) => [draft.note.id, draft]));
      for (const id of this.pending) {
        if (this.map.has(id)) merged.set(id, this.map.get(id));
        else merged.delete(id);
      }
      if (merged.size) this.storage.setItem(this.key, JSON.stringify({ schema: "2.0.0", drafts: [...merged.values()] }));
      else this.storage.removeItem(this.key);
      this.map = merged;
      this.pending.clear();
      this.saved = true;
    } catch { this.saved = false; }
    return this.saved;
  }
}

export class Edits {
  constructor(storage, lang, profile, index = null) {
    this.storage = storage;
    this.index = index;
    this.key = `gen3tl-editor:edits:${lang}:${profile}`;
    this.map = new Map();
    this.saved = true;
    try {
      const raw = storage?.getItem(this.key);
      let migrated = false;
      if (raw) for (const [id, e] of Object.entries(JSON.parse(raw))) {
        migrated = migrateSourceDraft(e) || migrated;
        this.map.set(id, { ...e, ...draftValue(e, e.translation, e.state ?? null),
          base_state: e.base_state ?? translationState({ translation: e.base }) });
      }
      if (migrated) this.save();
    } catch {
      this.map = new Map();
    }
  }

  get size() {
    return this.map.size;
  }

  get(id) {
    return this.map.get(id);
  }

  /** Snapshot every operand before grouped edits. */
  ruleBaselines(entry) {
    const baselines = {};
    for (const rule of this.index?.engine_rules?.rules ?? []) {
      const ids = [...new Set((rule.operands ?? []).flatMap((operand) => operand.bindings.map((b) => b.entry)))];
      if (!ids.includes(entry.id)) continue;
      const rows = new Map((this.index?.list ?? []).filter((row) => ids.includes(row.id)).map((row) => [row.id, row]));
      rows.set(entry.id, entry);
      const held = ids.map((id) => this.get(id)?.rule_baselines?.[rule.id]).find(Boolean);
      baselines[rule.id] = held ?? { capability_sha256: rule.capability_sha256,
        entries: Object.fromEntries(ids.map((id) => {
          const row = rows.get(id), draft = this.get(id);
          const state = row?.state ?? rule.sources?.find((source) => source.entry === id)?.state
            ?? translationState(row ?? {});
          return [id, row ? { translation: draft?.base ?? row.translation, state: draft?.base_state ?? state } : null];
        })) };
    }
    return structuredClone(baselines);
  }

  /** Preserve state changes even when text matches. */
  set(entry, translation, note, problems = null, exact = false, state = null) {
    return this.setMany([{ entry, translation, note, problems, exact, state }]);
  }

  /** Grouped operands persist together after validation. */
  setMany(changes) {
    const next = new Map(this.map);
    for (const { entry, translation, note, problems = null, exact = false, state = null } of changes) {
      if (entry.owner) throw uiError("Language-pack entries are read-only.");
      const previous = next.get(entry.id);
      const value = draftValue(previous ?? entry, translation, state);
      const base = previous?.base ?? entry.translation;
      const base_state = previous?.base_state ?? translationState(entry);
      if (translation === base && value.state === base_state) next.delete(entry.id);
      else next.set(entry.id, {
        ...value, note: note ?? previous?.note, base, base_state, source_text: previous?.source_text ?? entry.source_text,
        rule_baselines: previous ? previous.rule_baselines : this.ruleBaselines(entry),
        problems, exact, time: new Date().toISOString(),
      });
    }
    this.map = next;
    this.invalidateSources(changes.map(({ entry }) => entry.id));
    return this.save();
  }

  /** Source drafts invalidate dependent measurements. */
  invalidateSources(ids) {
    const dependencies = Object.values(this.index?.fill_sources?.dependencies ?? {}).flat();
    if (!ids.some((id) => dependencies.includes(id))) return;
    for (const draft of this.map.values()) Object.assign(draft, { problems: null, exact: false });
  }

  removeMany(ids) {
    for (const id of ids) this.map.delete(id);
    this.invalidateSources(ids);
    return this.save();
  }

  /** Keep check results for edited-entry list filters. */
  checked(id, problems, exact) {
    const e = this.map.get(id);
    if (!e) return true;
    Object.assign(e, { problems, exact });
    return this.save();
  }

  remove(id) {
    this.map.delete(id);
    this.invalidateSources?.([id]);
    return this.save();
  }

  clear() {
    this.map.clear();
    return this.save();
  }

  save() {
    try {
      if (!this.storage) throw new Error("no storage");
      if (this.map.size) this.storage.setItem(this.key, JSON.stringify(Object.fromEntries(this.map)));
      else this.storage.removeItem(this.key);
      this.saved = true;
    } catch {
      this.saved = false;
    }
    return this.saved;
  }

  /** Export changed entries in the requested order. */
  changes(order = null) {
    const ids = order ? order.filter((id) => this.map.has(id)) : [...this.map.keys()];
    return ids.map((id) => {
      const { translation, state, note, base, base_state } = this.map.get(id);
      return { id, translation, state, note, base, base_state };
    });
  }
}

/** Keep policy proposals separate from catalog entries. */
export class RuleEdits {
  constructor(storage, lang, profile) {
    this.storage = storage;
    this.key = `gen3tl-editor:rules:${lang}:${profile}`;
    this.saved = true;
    try {
      this.map = new Map(Object.entries(JSON.parse(storage?.getItem(this.key) ?? "{}")));
    } catch {
      this.map = new Map();
    }
  }

  get size() { return this.map.size; }
  get(id) { return this.map.get(id); }

  set(record) {
    this.map.set(record.id, structuredClone(record));
    return this.save();
  }

  remove(id) { return Edits.prototype.remove.call(this, id); }
  clear() { return Edits.prototype.clear.call(this); }
  save() { return Edits.prototype.save.call(this); }
  changes() { return [...this.map.values()].map((record) => structuredClone(record)); }
}

/** Store uploaded graphics that passed validation. */
export class PictureEdits {
  constructor(storage, lang, profile) {
    this.storage = storage;
    this.key = `gen3tl-editor:pictures:${lang}:${profile}`;
    this.map = new Map();
    this.saved = true;
    try {
      const raw = storage?.getItem(this.key);
      if (raw) for (const [id, e] of Object.entries(JSON.parse(raw))) this.map.set(id, e);
    } catch {
      this.map = new Map();
    }
  }

  get size() {
    return this.map.size;
  }

  get(id) {
    return this.map.get(id);
  }

  /** Keep validated uploads in area form. */
  set(id, upload) {
    this.map.set(id, { ...upload, time: new Date().toISOString() });
    return this.save();
  }

  remove(id) {
    this.map.delete(id);
    return this.save();
  }

  save() {
    return Edits.prototype.save.call(this);
  }

  /** Export uploaded graphics in the requested order. */
  changes(order = null) {
    const ids = order ? order.filter((id) => this.map.has(id)) : [...this.map.keys()];
    return ids.map((id) => {
      const { base, areas, tiles, png } = this.map.get(id);
      return { id, base, areas, tiles, png };
    });
  }
}

/** Store pack keyboard edits: rows and page order. */
export class KeyboardEdits {
  constructor(storage, lang, profile) {
    this.storage = storage;
    this.key = `gen3tl-editor:keyboard:${lang}:${profile}`;
    this.map = new Map(); // row name -> {text, base}; "pages" -> {pages, base}
    this.saved = true;
    try {
      const raw = storage?.getItem(this.key);
      if (raw) for (const [id, e] of Object.entries(JSON.parse(raw))) this.map.set(id, e);
    } catch {
      this.map = new Map();
    }
  }

  /** Changed keys rows and page order: the count shown. */
  get size() {
    return [...this.map.values()].filter((e) => e.keys || e.pages).length;
  }

  /** The draft text of a row, or undefined. */
  row(name) {
    return this.map.get(`row:${name}`)?.text;
  }

  /** The draft page order (names), or null. */
  pages() {
    return this.map.get("pages")?.pages ?? null;
  }

  /** Keep a row's text until it equals the data's. */
  setRow(name, text, base, keys = false) {
    const id = `row:${name}`;
    const was = this.map.get(id)?.base ?? base;
    if (text === was) this.map.delete(id);
    else this.map.set(id, { text, base: was, keys, time: new Date().toISOString() });
    return this.save();
  }

  /** Keep a page order until it equals the data's. */
  setPages(pages, base) {
    const was = this.map.get("pages")?.base ?? base;
    if (pages.join("\n") === was.join("\n")) this.map.delete("pages");
    else this.map.set("pages", { pages, base: was, time: new Date().toISOString() });
    return this.save();
  }

  clear() {
    this.map.clear();
    return this.save();
  }

  save() {
    return Edits.prototype.save.call(this);
  }

  /** The export's keyboard: {rows, pages, base}, or null without edits. */
  changes() {
    if (!this.map.size) return null;
    const rows = {}, base = { rows: {} };
    const out = { rows, base };
    for (const [id, e] of this.map) {
      if (id === "pages") {
        out.pages = e.pages;
        base.pages = e.base;
      } else {
        rows[id.slice(4)] = e.text;
        base.rows[id.slice(4)] = e.base;
      }
    }
    return out;
  }
}

// Editor fields must match glossary column names.
export const TERM_COLUMNS = {
  translation: "translation", label: "label", gender: "gender", declension: "declension", forms: "forms",
  notes: "notes", sources: "sources",
};

const sortedForms = (f) => Object.entries(f ?? {}).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));

/** Compare term fields independently of form key order. */
export function sameField(field, a, b) {
  if (field === "forms") return JSON.stringify(sortedForms(a)) === JSON.stringify(sortedForms(b));
  return (a ?? "") === (b ?? "");
}


/**
 * Export term differences and explicitly included form-only entries.
 * Unchanged terms produce no export record.
 */
export function termRecord(base, row, include = [], columns = TERM_COLUMNS) {
  const rec = { id: base.id, source_text: base.source_text, base: {}, changes: {} };
  for (const [field, col] of Object.entries(columns)) {
    // Missing projections are not deletion requests.
    if (field === "notes" && (base.note_ids?.length || row.note_ids?.length)
      && (base.notes === undefined || row.notes === undefined)) continue;
    if (sameField(field, base[field], row[field])) continue;
    rec.base[col] = base[field] ?? (field === "forms" ? {} : "");
    rec.changes[col] = row[field] ?? (field === "forms" ? {} : "");
  }
  if (!Object.keys(rec.changes).length) return null;
  if (include?.length) rec.include = [...new Set(include)].sort();
  return rec;
}

/** Source caches follow their current field names. */
function migrateSourceDraft(value) {
  if (!value || typeof value !== "object") return false;
  let changed = false;
  for (const [old, current] of [["english", "source_text"], ["english_sha256", "source_sha256"], ["in_english", "in_source"]]) {
    if (Object.hasOwn(value, old)) {
      value[current] = value[old];
      delete value[old];
      changed = true;
    }
  }
  if (value.citation?.columns) {
    for (const [role, column] of Object.entries(value.citation.columns)) {
      if (column === "english") {
        value.citation.columns[role] = "source_text";
        changed = true;
      }
    }
  }
  for (const item of Object.values(value)) changed = migrateSourceDraft(item) || changed;
  return changed;
}

/** Preserve draft values across field renames. */
function migrateTermDraft(draft) {
  let changed = migrateSourceDraft(draft);
  for (const row of [draft, draft.base, draft.row, draft.changes]) {
    if (row && Object.hasOwn(row, "russian")) {
      row.translation = row.russian;
      delete row.russian;
      changed = true;
    }
  }
  return changed;
}

/** Store glossary term edits for export. */
export class TermEdits {
  constructor(storage, lang, profile, columns = TERM_COLUMNS) {
    this.columns = columns;
    this.storage = storage;
    this.key = `gen3tl-editor:terms:${lang}:${profile}`;
    this.map = new Map();
    this.saved = true;
    try {
      const raw = storage?.getItem(this.key);
      let migrated = false;
      if (raw) for (const [id, e] of Object.entries(JSON.parse(raw))) {
        migrated = migrateTermDraft(e) || migrated;
        this.map.set(id, e);
      }
      if (migrated) this.save();
    } catch {
      this.map = new Map();
    }
  }

  get size() {
    return this.map.size;
  }

  get(id) {
    return this.map.get(id);
  }

  /** Keep term edits until they equal the original data. */
  set(base, row) {
    const was = this.map.get(base.id);
    const from = was?.base ?? base;
    if (base.note_ids?.length) {
      row = { ...row, note_ids: base.note_ids };
      // Historical note edits remain reviewable.
      if (row.notes === undefined && was?.row.notes !== undefined) row.notes = was.row.notes;
    }
    if (!termRecord(from, row, [], this.columns)) this.map.delete(base.id);
    else this.map.set(base.id, { row, base: from, time: new Date().toISOString(), include: was?.include ?? [] });
    return this.save();
  }

  /** Read form-only entries explicitly included in a rename. */
  include(id) {
    return this.map.get(id)?.include ?? [];
  }

  /** Keep included entries only for an edited term. */
  setInclude(id, ids) {
    const e = this.map.get(id);
    if (!e) return false;
    e.include = [...new Set(ids)].sort();
    return this.save();
  }

  remove(id) {
    this.map.delete(id);
    return this.save();
  }

  save() {
    return Edits.prototype.save.call(this);
  }

  /** Export term edits in the requested order. */
  changes(order = null) {
    const ids = order ? order.filter((id) => this.map.has(id)) : [...this.map.keys()];
    return ids.map((id) => termRecord(this.map.get(id).base, this.map.get(id).row, this.map.get(id).include, this.columns))
      .filter(Boolean);
  }
}

/** Same art: equal width and rows. */
const sameArt = (a, b) => a.width === b.width && a.rows.length === b.rows.length && a.rows.every((r, i) => r === b.rows[i]);

/** Store glyph art redrawn in the Fonts view. */
export class GlyphEdits {
  constructor(storage, lang, profile) {
    this.storage = storage;
    this.key = `gen3tl-editor:glyphs:${lang}:${profile}`;
    this.map = new Map();
    this.saved = true;
    try {
      const raw = storage?.getItem(this.key);
      if (raw) for (const [id, e] of Object.entries(JSON.parse(raw))) this.map.set(id, e);
    } catch {
      this.map = new Map();
    }
  }

  get size() {
    return this.map.size;
  }

  get(id) {
    return this.map.get(id);
  }

  /** Every draft: {style, code, width, rows}. */
  all() {
    return [...this.map.values()];
  }

  /** The drafts of one style. */
  of(style) {
    return this.all().filter((e) => e.style === style);
  }

  /** Keep `art` over `base`; equal art drops the draft. */
  set(base, art) {
    if (sameArt(base, art)) this.map.delete(base.id);
    else {
      const key = base.char !== undefined ? { char: base.char } : { slot: base.slot };
      const was = base.copy !== undefined ? { copy: base.copy }
        : { base: base.source ? { source: base.source } : { width: base.width, rows: [...base.rows] } };
      this.map.set(base.id, { style: base.style, code: base.code, ...key, width: art.width, rows: [...art.rows],
        ...was, time: new Date().toISOString() });
    }
    return this.save();
  }

  remove(id) {
    this.map.delete(id);
    return this.save();
  }

  save() {
    return Edits.prototype.save.call(this);
  }

  /** Export redrawn glyphs as the art file names them. */
  changes() {
    return [...this.map.keys()].sort().map((id) => {
      const { style, char, slot, width, rows, base, copy } = this.map.get(id);
      return { style, ...(char !== undefined ? { char } : { slot }), width, rows,
        base: copy !== undefined ? { source: `copy:${copy}` } : base };
    });
  }
}

export const EXPORT_VERSION = "4.0.0";

/** Catalog, policy and note proposals travel together. */
export function exportDoc(lang, profile, changes, pictures = [], terms = [], glyphs = [], keyboard = null, rules = [],
  notes = []) {
  return { version: EXPORT_VERSION, language: lang, profile, entries: changes, pictures, terms, glyphs,
    ...(keyboard ? { keyboard } : {}),
    ...(rules.length ? { engine_rules: rules } : {}),
    ...(notes.length ? { note_changes_schema: NOTE_CHANGES_SCHEMA, note_changes: structuredClone(notes) } : {}) };
}

/** Name an export by language and time. */
export function exportName(lang, now = new Date()) {
  const t = now.toISOString().slice(0, 16).replace(/[-:T]/g, "");
  return `gen3tl-${lang}-edits-${t}.json`;
}
