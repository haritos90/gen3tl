// Local draft saves through the editor session.

import { uiError } from "./ui.js";
import { translationState } from "./draft.js";
import { NOTE_CHANGES_SCHEMA, noteRevision } from "./store.js";

export class LocalSave {
  constructor(fetcher = (...args) => fetch(...args)) {
    this.fetcher = fetcher;
    this.session = null;
    this.busy = false;
  }

  async connect() {
    try {
      const response = await this.fetcher("api/session", { cache: "no-store" });
      if (response.ok) this.session = await response.json();
    } catch {
      this.session = null;
    }
  }

  matches(index) {
    return !!(this.session && index && this.session.profile === index.profile &&
      this.session.language === index.language && this.session.rom_sha1 === index.rom?.sha1);
  }

  async save(index, edits, entryFor, noteEdits = null) {
    if (this.busy || !this.matches(index)) throw uiError("This catalog is not writable in this session.");
    this.busy = true;
    try {
      const captured = [...edits.map].map(([id, edit]) => [id, { ...edit }]);
      const notes = structuredClone(noteEdits?.changes() ?? []);
      const records = await Promise.all(captured.map(async ([id, edit]) => {
        const entry = await entryFor(id);
        return { id, source_text: edit.source_text ?? entry.source_text, base: edit.base,
          base_state: edit.base_state ?? translationState({ translation: edit.base }),
          translation: edit.translation, state: translationState(edit) };
      }));
      const { profile, language, rom_sha1, token } = this.session;
      const response = await this.fetcher("api/save", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Editor-Token": token },
        body: JSON.stringify({ profile, language, rom_sha1, entries: records,
          ...(notes.length ? { note_changes_schema: NOTE_CHANGES_SCHEMA, note_changes: notes } : {}) }),
      });
      const result = await response.json();
      if (!response.ok) throw result.error ? new Error(result.error) : uiError("The drafts could not be saved.");
      for (const record of records) {
        const current = edits.get(record.id);
        if (!current) {
          if (record.base !== record.translation || record.base_state !== record.state) {
            const original = captured.find(([id]) => id === record.id)[1];
            edits.map.set(record.id, { ...original, translation: record.base, state: record.base_state,
              base: record.translation, base_state: record.state });
          }
          continue;
        }
        if (current.translation === record.translation && translationState(current) === record.state) edits.map.delete(record.id);
        else {
          current.base = record.translation;
          current.base_state = record.state;
          current.source_text = record.source_text;
        }
      }
      const currentNotes = new Map((noteEdits?.changes() ?? []).map((change) => [change.id, change]));
      for (const change of notes) {
        if (noteRevision(currentNotes.get(change.id)) === noteRevision(change)) noteEdits.remove(change.id);
      }
      edits.save();
      return result;
    } finally {
      this.busy = false;
    }
  }
}
