// The stored editor data: game parts once, each language what differs.
// Decoding gives back the records gen3tl.web.editordata made.
// See gen3tl.web.editorstore.

// the order of a decoded entry and of its context
const ENTRY = ["id", "file", "tokens", "translation", "flags", "status", "context", "owner", "window", "terms",
  "approved", "note", "reach", "translator_notes", "rules", "source_sha256", "note_ids"];
const LANGUAGE = new Set(["flags", "terms", "approved", "note", "translator_notes", "rules", "note_ids", "inserted", "words"]);
const REACH = { class: "reachable", source: "not studied", evidence: "" };
const unstated = () => ({ in_source: "not-stated", basis: "", sources: [] });
const STORED = new WeakMap(); // decoded index -> what decoding its chunks needs

/** The status a list row omits: approved with a translation. */
export const defaultStatus = (translation) => (translation ? "approved" : "untranslated");

function limitSource(row) {
  return { kind: row.kind, rule: structuredClone(row.rule), evidence: row.evidence ?? "",
    ...(row.unverified ? { unverified: true } : {}) };
}

function reachOf(n, game) {
  if (n === undefined) return { ...REACH };
  const row = game.reach[n];
  const out = { ...REACH, ...row };
  return { class: out.class, source: out.source, evidence: structuredClone(out.evidence) };
}

function windowOf(n, tables) {
  const out = structuredClone(tables.windows[n]);
  if (out.sources) {
    out.sources = Object.fromEntries(Object.entries(out.sources)
      .map(([field, k]) => [field, limitSource(tables.limit_sources[k])]));
  }
  if (out.consumers) {
    out.consumers = out.consumers.map((k) => {
      const { name, source, ...rest } = structuredClone(tables.consumers[k]);
      const decoded = limitSource(tables.limit_sources[source]);
      return { name: name ?? decoded.rule, ...rest, source: decoded };
    });
  }
  if (out.slot_details) {
    out.slot_details = Object.fromEntries(Object.entries(out.slot_details)
      .map(([token, k]) => [token, structuredClone(tables.slot_details[k])]));
  }
  return out;
}

/** The index of a stored language index and its game part; chunks decode through it. */
export function decodeIndex(stored, game, url) {
  const { shared, chunks, list, notes, fill_sources: fills, windows, limit_sources: sources, consumers,
    slot_details: details, rules, ...rest } = stored;
  const tables = { windows, limit_sources: sources, consumers, slot_details: details, rules };
  const offsets = [];
  const rows = [];
  chunks.forEach((chunk, n) => {
    offsets.push(rows.length);
    for (let i = 0; i < chunk.entries; i++) {
      const at = rows.length, own = game.list[at], row = list[at];
      const translation = row.translation ?? "";
      rows.push({ id: own.id, chunk: n, status: row.status ?? defaultStatus(translation),
        ...(row.owner ? { owner: row.owner } : {}), translation,
        kind: row.window === undefined ? null : windows[row.window].kind ?? null,
        reach: reachOf(own.reach, game).class, maps: [...(own.maps ?? [])],
        ...(row.shortened ? { shortened: row.shortened } : {}), problems: row.problems ?? [] });
    }
  });
  if (rows.length !== list.length || rows.length !== game.list.length) throw new Error("Catalog rows do not match their chunks");
  const index = { ...rest };
  for (const key of ["where_texts", "map_order", "codec"]) if (key in game) index[key] = game[key];
  if (fills) {
    const { values, ...head } = fills; // the language's values follow the shared graph
    index.fill_sources = { ...head, ...game.fill_sources, ...(values === undefined ? {} : { values }) };
  } else if (fills !== undefined) index.fill_sources = fills;
  if (notes) index.notes = { ...notes, note: notes.note.map((note) => (typeof note === "number" ? game.notes[note] : note)) };
  index.chunks = chunks.map(({ part, ...chunk }) => chunk);
  index.list = rows;
  STORED.set(index, { url, gameUrl: new URL(shared, url).href, game, tables, offsets, chunks, list });
  return index;
}

/** The explanation templates of a decoded index, by code. */
export function messagesOf(index) {
  return STORED.get(index)?.game.messages ?? {};
}

/** A chunk {schema, file, entries} of its stored language part and game part. */
export function decodeChunk(index, n, stored, part) {
  const s = STORED.get(index);
  if (!s) throw new Error("Catalog index was not decoded");
  const descriptor = s.chunks[n];
  if (stored.entries.length !== descriptor.entries || part.entries.length !== descriptor.entries) {
    throw new Error(`${descriptor.path}: entry count does not match the index`);
  }
  const entries = stored.entries.map((own, i) => {
    const at = s.offsets[n] + i, game = part.entries[i], row = s.list[at], listed = s.game.list[at];
    const shared = game.context ?? {};
    const translation = row.translation ?? "";
    const values = {
      id: listed.id, file: descriptor.file, tokens: game.tokens === null ? undefined : game.tokens ?? [], translation,
      flags: own.flags ?? [],
      status: row.status ?? defaultStatus(translation),
      context: { comments: shared.comments ?? [], events: shared.events ?? [], boxes: shared.boxes ?? [],
        uses: shared.uses ?? 0, speakers: shared.speakers ?? [], inserted: own.inserted ?? [], words: own.words ?? [],
        hosts: shared.hosts ?? [],
        identity: shared.identity ?? { schema: "2.0.0", speaker: unstated(), addressee: unstated() },
        maps: [...(listed.maps ?? [])] },
      owner: row.owner, window: row.window === undefined ? null : windowOf(row.window, s.tables),
      terms: own.terms ?? [], approved: own.approved ?? [], note: own.note === null ? undefined : own.note ?? "",
      reach: reachOf(listed.reach, s.game),
      translator_notes: own.translator_notes === null ? undefined : own.translator_notes ?? [],
      rules: (own.rules ?? []).map((k) => structuredClone(s.tables.rules[k])),
      source_sha256: game.source_sha256, note_ids: own.note_ids,
    };
    const entry = {};
    for (const key of ENTRY) if (values[key] !== undefined) entry[key] = values[key];
    for (const [key, value] of Object.entries(own)) if (!LANGUAGE.has(key)) entry[key] = value;
    return entry;
  });
  return { schema: stored.schema, file: descriptor.file, entries };
}

/** terms.json {schema, terms, slots} of its stored form; term rows take their glossary fields from the index. */
export function decodeTerms(index, stored) {
  const glossary = index.glossary ?? {};
  const terms = stored.terms.map(({ id, declension, sources, variants, confidence, in_source = [], in_translation = [],
    form_only = [], shortened = [], ...own }) => ({ id, ...(Object.hasOwn(glossary, id) ? structuredClone(glossary[id]) : own),
    declension, sources, variants, confidence, in_source, in_translation, form_only, shortened }));
  const slots = new Map(); // word -> {lines, seen}
  for (const line of stored.slots.lines) {
    for (const word of line.group === undefined ? line.words : stored.slots.groups[line.group]) {
      if (!slots.has(word)) slots.set(word, { lines: [], seen: new Set() });
      const filled = slots.get(word), key = `${line.line}\n${line.slot}`;
      if (!filled.seen.has(key)) {
        filled.seen.add(key);
        filled.lines.push([line.line, line.slot]);
      }
    }
  }
  return { schema: stored.schema, terms, slots: Object.fromEntries([...slots].map(([word, { lines }]) => [word, lines])) };
}

/** The URL of the source rows (ROM locations of the English) of a decoded index; null without them. */
export function sourceUrl(index) {
  const s = STORED.get(index);
  return s?.game.source ? new URL(s.game.source, s.gameUrl).href : null;
}

/** The URL of chunk `n`'s game part. */
export function chunkPartUrl(index, n) {
  const s = STORED.get(index);
  if (!s) throw new Error("Catalog index was not decoded");
  return new URL(s.chunks[n].part, s.url).href;
}

/** The source document of its stored part. */
export function decodeSource(part) {
  const source = { ...part };
  delete source.part;
  return source;
}
