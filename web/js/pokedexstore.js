// The stored Pokédex datasets: each repeated value once, defaults absent.
// Decoding gives back the documents gen3tl.data.pokedexdata wrote.
// See gen3tl.data.pokedexstore and gen3tl.data.pokedextexts.

export const TRAINER_IV_SCHEMAS = [3, "4.0.0"];
export const TEXTS_SCHEMAS = [1, "2.0.0"];

const NAMES = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";
const PLACE = "{}";
const has = Object.hasOwn;

// Records never share a value: pages may change one.
function copy(value) {
  if (Array.isArray(value)) return value.map(copy);
  if (value === null || typeof value !== "object") return value;
  const out = {};
  for (const key of Object.keys(value)) out[key] = copy(value[key]);
  return out;
}

function row(table, n, where) {
  if (!Number.isInteger(n) || n < 0 || n >= table.length) throw new Error(`Pokédex dataset: no row ${n} in ${where}`);
  return copy(table[n]);
}

/** The path below `path` through `key`; decimal keys share one. */
export function step(path, key) {
  const part = /^\d+$/.test(key) ? "*" : key.replaceAll("~", "~0").replaceAll("/", "~1");
  return path ? `${path}/${part}` : part;
}

/** The stored name of the n-th key of a collection. */
export function keyName(n) {
  const size = NAMES.length;
  return n < size ? NAMES[n] : NAMES[Math.floor(n / size) - 1] + NAMES[n % size];
}

function hex(rule, number) {
  const width = Number(rule.hex.slice(0, -1)), upper = rule.hex.endsWith("X");
  const digits = number.toString(16);
  return `0x${(upper ? digits.toUpperCase() : digits).padStart(width, "0")}`;
}

function fill(value, text) {
  if (typeof value === "string") return value.replaceAll(PLACE, text);
  if (Array.isArray(value)) return value.map((item) => fill(item, text));
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, fill(v, text)]));
  return value;
}

function records(rows, path, head, codec) {
  const defaults = head.defaults ?? {}, tables = head.tables ?? {}, derived = head.derived ?? {};
  const fields = head.keys.map((key, n) => ({ key, alias: keyName(n), path: step(path, key),
    table: has(tables, key) ? tables[key] : null, rule: has(derived, key) ? derived[key] : null,
    fallback: has(defaults, key), value: defaults[key] }));
  const offsets = fields.filter((field) => field.rule && has(field.rule, "hex"));
  const templates = fields.filter((field) => field.rule && has(field.rule, "text"));
  const plain = fields.filter((field) => !field.rule);
  const last = new Map();
  return rows.map((stored) => {
    let seen = 0;
    const record = {};
    for (const field of plain) {
      if (has(stored, field.alias)) {
        seen += 1;
        const value = stored[field.alias];
        record[field.key] = field.table ? row(field.table, value, field.path) : decodeValue(value, field.path, codec);
      } else if (field.fallback) record[field.key] = copy(field.value);
    }
    for (const field of offsets) { // offsets first: templates may read them
      const present = has(stored, field.alias);
      seen += present;
      const delta = present ? stored[field.alias] : field.rule.step;
      if (delta === null) {
        record[field.key] = null;
        continue;
      }
      last.set(field.key, (last.get(field.key) ?? 0) + delta);
      record[field.key] = hex(field.rule, last.get(field.key));
    }
    for (const field of templates) {
      const present = has(stored, field.alias);
      seen += present;
      record[field.key] = present ? decodeValue(stored[field.alias], field.path, codec)
        : fill(field.rule.text, String(record[field.rule.of]));
    }
    if (seen !== Object.keys(stored).length) throw new Error(`Pokédex dataset: an unknown key at ${path}`);
    return record;
  });
}


function rowsOf(rows, head) {
  const defaults = head.defaults ?? {}, tables = head.tables ?? {}, derived = head.derived ?? {};
  const columns = Array.from({ length: head.columns }, (_, n) => String(n)).map((key) => ({
    rule: has(derived, key) ? derived[key] : null, table: has(tables, key) ? tables[key] : null,
    fallback: has(defaults, key), value: defaults[key], where: `column ${key}` }));
  const last = columns.map(() => 0);
  return rows.map((stored) => columns.map((column, n) => {
    if (column.rule) {
      const delta = n < stored.length ? stored[n] : column.rule.step;
      if (delta === null) return null;
      last[n] += delta;
      return hex(column.rule, last[n]);
    }
    if (n < stored.length) return column.table ? row(column.table, stored[n], column.where) : stored[n];
    if (column.fallback) return copy(column.value);
    throw new Error(`Pokédex dataset: a row lacks ${column.where}`);
  }));
}


function decodeValue(value, path, codec) {
  if (Array.isArray(value)) {
    const head = has(codec, path) ? codec[path] : null;
    if (head && value.length) {
      if (has(head, "columns") && value.every(Array.isArray)) return rowsOf(value, head);
      if (has(head, "keys") && value.every((item) => item && typeof item === "object" && !Array.isArray(item))) {
        return records(value, path, head, codec);
      }
    }
    return value.map((item) => decodeValue(item, path, codec));
  }
  if (value && typeof value === "object") {
    const out = {};
    for (const key of Object.keys(value)) out[key] = decodeValue(value[key], step(path, key), codec);
    return out;
  }
  return value;
}

/** The document of a stored dataset file; a document without a codec is its own. */
export function decodeDataset(stored) {
  if (!stored || typeof stored !== "object" || Array.isArray(stored)) throw new Error("Pokédex dataset: not an object");
  if (!has(stored, "codec")) return stored;
  const { codec, ...fields } = stored;
  return Object.fromEntries(Object.entries(fields).map(([key, value]) => [key, decodeValue(value, step("", key), codec)]));
}

/** The address of the English part a texts file names, or null. */
export function textsPartUrl(stored, url) {
  return typeof stored?.source === "string" ? new URL(stored.source, url).href : null;
}

/**
 * The texts of a language: [English, translation] per catalog id.
 * The English comes from the game part; a previous texts file holds both.
 */
export function decodeTexts(stored, part = null) {
  if (!has(stored, "source")) return stored;
  if (part?.part !== "pokedex-texts" || part.schema !== stored.schema) throw new Error("Pokédex texts: not their English part");
  const { source, texts: own = {}, page: ownPage = {}, ...rest } = stored;
  const texts = {};
  for (const [id, english] of Object.entries(part.texts ?? {})) texts[id] = [english, has(own, id) ? own[id] : ""];
  for (const id of Object.keys(own)) if (!has(texts, id)) throw new Error(`Pokédex texts: ${id} has no English`);
  const page = {};
  for (const [key, english] of Object.entries(part.page ?? {})) page[key] = [english, has(ownPage, key) ? ownPage[key] : ""];
  for (const [key, value] of Object.entries(ownPage)) if (!has(page, key)) page[key] = value;
  const doc = { ...rest, texts };
  if (Object.keys(page).length) doc.page = Object.fromEntries(Object.keys(page).sort().map((key) => [key, page[key]]));
  return doc;
}
