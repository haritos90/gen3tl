// Cache derived text without replacing catalog metadata or drafts.

/** Content revisions invalidate unreleased catalog changes too. */
export async function joinedKey(sha1, index, source) {
  const rows = index.list.map((row) => [row.id, row.status, row.translation, row._problems ?? row.problems]);
  const content = JSON.stringify([index.schema, index.profile, index.language, index.version, rows,
    index.glossary, index.language_rules?.fold, source]);
  const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(content));
  const revision = [...new Uint8Array(hash)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  return JSON.stringify([2, sha1, index.toolkit, revision]);
}

export class JoinedCache {
  constructor(storage = globalThis.indexedDB) {
    this.storage = storage;
  }

  /** Unavailable storage must not prevent ROM loading. */
  transaction(mode, key, value) {
    return new Promise((resolve) => {
      let db = null, settled = false;
      const timer = setTimeout(() => finish(), 1000);
      const finish = (result = null) => {
        settled = true;
        clearTimeout(timer);
        db?.close();
        resolve(result);
      };
      try {
        const opening = this.storage.open("gen3tl-editor-joined", 1);
        opening.onupgradeneeded = () => opening.result.createObjectStore("joined");
        opening.onerror = opening.onblocked = () => finish();
        opening.onsuccess = () => {
          db = opening.result;
          if (settled) { db.close(); return; }
          try {
            const tx = db.transaction("joined", mode), store = tx.objectStore("joined");
            const request = mode === "readonly" ? store.get(key) : store.put(value, key);
            tx.oncomplete = () => finish(mode === "readonly" ? request.result ?? null : true);
            tx.onerror = tx.onabort = () => finish();
          } catch { finish(); }
        };
      } catch { finish(); }
    });
  }

  get(key) {
    return this.transaction("readonly", key);
  }

  put(key, value) {
    return this.transaction("readwrite", key, value);
  }
}

/** Save only derived fields after a complete join. */
export function joinedValue(english, rows) {
  return { source_text: english, rows: rows.map((row) => ({ id: row.id, terms: row.terms, problems: row.problems })) };
}

/** Partial or damaged caches fall back to recomputation. */
export function validJoined(value, rows, glossary) {
  return Boolean(value?.source_text?.texts && Array.isArray(value.source_text.missing) &&
    value.rows?.length === rows.length && rows.every((row, i) => {
      const saved = value.rows[i];
      return saved?.id === row.id && Array.isArray(saved.problems) &&
        (!glossary || typeof value.source_text.texts[row.id] !== "string" || Array.isArray(saved.terms));
    }));
}
