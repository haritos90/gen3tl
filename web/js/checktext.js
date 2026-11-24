// The check's messages in the site language.
// A message is its kind, then its separately translated notes.
// Problems are stored in English; explanations as codes.

import { N_, t, uiLanguage } from "./ui.js";

const NUMBER = "\\d+";
const QUOTED = `'(?:[^'\\\\]|\\\\.)*'|"(?:[^"\\\\]|\\\\.)*"`; // a Python repr
const CODE = "\\{[^{}]*\\}";
// what a hole matches, by its name; any text otherwise
const HOLES = {
  n: NUMBER, width: NUMBER, limit: "-?\\d+", box: NUMBER, row: NUMBER, lines: NUMBER, window: NUMBER, byte: NUMBER,
  at: NUMBER, outline: NUMBER, partner_width: NUMBER, unicode: "[0-9A-F]{4,6}", escape: "\\\\.",
  text: QUOTED, shown: QUOTED, prefix: QUOTED, term: QUOTED, translation: QUOTED, char: QUOTED,
  column: CODE, code: CODE, was: CODE, token: CODE,
};

/** The kinds of the check's messages. */
export const MESSAGES = [
  N_("not encodable: {error}"),
  N_("field overflow: {n} > {limit} characters"),
  N_("in-place overflow: {n} > {limit} bytes"),
  N_("must begin with {head}: the code reads the text from byte {byte}"),
  N_("line break in a single-line field"),
  N_("{n} lines in one box without scrolling"),
  N_("too many keys: {n} > {limit}: {text}"),
  N_("line too wide: {width} > {limit} px: {text}"),
  N_("line too wide: {width} > {limit} px: {text}; window {where}"),
  N_("line too wide: {width} > {limit} px: {text} with {slots}; window {where}"),
  N_("line too wide: {width} > {limit} px: {text} with {slots}"),
  N_("line too wide: {width} > {limit} px before the column {column}: {text}"),
  N_("line too wide: {width} > {limit} px before the column {column}: {text} with {slots}"),
  N_("too many lines: {n} > {limit}"),
  N_("empty translation of a non-empty English: imports as an intended empty translation"),
  N_("placeholders differ"),
  N_("scroll and page codes are not supported in {where}: this window prints instantly and drops the remaining text"),
  N_("line too wide: {width} > {limit} px in {where} ({font})"),
  N_("pair too wide: {width} > {limit} px in {where} ({font}) with «{partner}» {partner_id}, {partner_width} px, outline {outline} px, for «{species}» {species_id}"),
  N_("line starts with a spaced hyphen after an automatic wrap: box {box}: {text}"),
  N_("line leaves no room for the prompt arrow: box {box}: {width} > {limit} px: {text}"),
  N_("line leaves no room for the prompt arrow: box {box}: {width} > {limit} px in {where}: {text}"),
  N_("line wider than the window with the inserted words: box {box}: {width} > {limit} px: {text}; the build re-wraps the box"),
  N_("line wider than the window with the inserted words: box {box}: {width} > {limit} px: {text} with {slots}; the build re-wraps the box"),
  N_("written line wider than the window: box {box}: {width} > {limit} px: {text}; the build drops the written breaks of the box and re-wraps it as {shown}"),
  N_("written line ends with «{word}»: box {box}, line {row}; the break stays as written"),
  N_("shown break splits «{word}» from the name after it: box {box}, line {row}"),
  N_("shown break splits a count from «{unit}»: box {box}, line {row}"),
  N_("box {box} wraps to {n} lines, the English box has {lines}"),
  N_("typography: {reason}"),
  N_("not centred in the {window} px window: lead with {code}"),
  N_("glossary term {term} -> {translation} not found"),
  N_("no inventory entry for this id"),
];

/** The character map's errors, the {error} of "not encodable". */
export const ENCODING = [
  N_("character {char} (U+{unicode}) has no byte, in {text}"),
  N_("unclosed brace at {at} in {text}"),
  N_("unknown escape {escape} at {at} in {text}"),
  N_("empty token in {text}"),
  N_("{token} needs {n} hex argument(s), in {text}"),
  N_("unknown token {token} in {text}"),
];

/** The notes a message may end with. */
export const NOTES = [
  N_("(warning)"),
  N_("[unmeasured limit: the English width]"),
  N_("[unmeasured limit: the English line count]"),
  N_("[unverified limit: an estimate for this language]"),
  N_("(prefix {prefix} stripped)"),
];

/** The graphics check's findings. */
export const FINDINGS = [
  N_("{tiles} tiles is below the original {original}; tiles can only be appended"),
  N_("tiles can only be appended to an LZ77 graphic; the size of a raw graphic is fixed by its loader"),
  N_("the graphic holds {n} bytes after its last tile; appended tiles would not follow it"),
  N_("text area {area} is empty or leaves the {size} sheet"),
  N_("text area {spec} is not x,y,w,h"),
  N_("dimensions {size} differ from the original {expected}"),
  N_("dimensions {size} differ from the {tiles}-tile sheet's {expected}"),
  N_("palette has {n} entries, the original {original}"),
  N_("palette entry {index} is {colour}, the original {original_colour}"),
  N_("pixel {pixel}: index {index} is outside the {n}-colour palette"),
  N_("pixel {pixel}: colour {colour} is not in the palette"),
  N_("pixel {pixel}: colour {colour} is palette entries {entries}; save the sheet as an indexed PNG"),
  N_("pixel {pixel}: index {was} -> {index} past the last tile (the graphic has {tiles} tiles)"),
  N_("pixel {pixel}: index {was} -> {index} outside the text areas"),
  N_("pixel {pixel}: index {index} outside the text areas of the area form"),
  N_("the area form is not an indexed PNG"),
  N_("{graphic} has no edit in the project"),
  N_("{graphic} is not in the graphics inventory"),
  N_("{graphic} has no ROM source in the graphics inventory"),
  N_("no ROM loaded"),
  N_("{file}: not a PNG file"),
  N_("{file}: no IHDR chunk"),
  N_("{file}: unsupported PNG (colour type {type}, bit depth {depth}, interlace {interlace})"),
  N_("{file}: indexed PNG without a palette"),
  N_("{file}: unknown filter type {filter} in row {row}"),
  N_("{file}: unreadable PNG ({error})"),
];

/** The graphics check's report lines besides its findings. */
export const REPORT = [
  N_("{graphic}: {changed} changed pixels inside {areas} text area(s), {findings} finding(s)"),
  N_("{graphic}: {changed} changed pixels inside {areas} text area(s), {set} set in {appended} appended tile(s), {findings} finding(s)"),
  N_("encoded  {bytes} bytes, slot {slot}: written in place"),
  N_("encoded  {bytes} bytes, slot {slot}: relocated"),
];

const SIZE = "\\d+x\\d+";
const COLOUR = "#[0-9a-f]{6}";
// the holes of the findings and report lines
const GFX_HOLES = {
  tiles: NUMBER, original: NUMBER, n: NUMBER, index: NUMBER, was: NUMBER, type: NUMBER, depth: NUMBER,
  interlace: NUMBER, filter: NUMBER, row: NUMBER, changed: NUMBER, areas: NUMBER, set: NUMBER, appended: NUMBER,
  findings: NUMBER, bytes: NUMBER, slot: NUMBER, size: SIZE, expected: SIZE, colour: COLOUR, original_colour: COLOUR,
  pixel: "\\d+,\\d+", area: "-?\\d+,-?\\d+,-?\\d+,-?\\d+", entries: "\\d+(?:, \\d+)+",
};

/** The sources of a slot width. */
export const SLOTS = [
  N_("profile width"),
  N_("profile width, unverified estimate"),
  N_("default width"),
  N_("context records"),
  N_("profile width, above the widest known {source} {width} px"),
  N_("profile width, unverified estimate, above the widest known {source} {width} px"),
  N_("widest of table {table}: {word}"),
  N_("native table {table}: {ids}"),
  N_("number of up to {n} digits (a variable)"),
  N_("number of {n} digits, the record's {value}"),
  N_("number {value}"),
  N_("{word} (untranslated)"),
  N_("{word} (translated as empty)"),
  N_("widest source {id}, English {english} px, {where}"),
  N_("widest source {id}, {where}"),
  N_("number of {n} digit, English {english} px, {where}"),
  N_("number of {n} digit, {where}"),
  N_("number of {n} digits, English {english} px, {where}"),
  N_("number of {n} digits, {where}"),
  N_("{width} px, unverified estimate, English {english} px, {where}"),
  N_("{width} px, unverified estimate, {where}"),
  N_("{width} px, English {english} px, {where}"),
  N_("{width} px, {where}"),
];

/** Why a slot width is partly unknown. */
export const SLOT_REASONS = [
  N_("no fill known"),
  N_("{n} of {uses} walked paths set no value"),
  N_("a value the scripts do not name: {kinds}"),
  N_("{n} script references not walked"),
  N_("shown by code"),
];

const CODE_FILL = N_("code fill");
const UNITS = { px: N_("px"), keys: N_("keys") };

export const unitName = (unit) => t(UNITS[unit] ?? unit);

/** Keep measurements intact when a narrow column wraps. */
export function keepUnits(text) {
  let out = String(text);
  for (const unit of Object.keys(UNITS)) {
    const name = unitName(unit);
    out = out.replaceAll(` ${unit}`, `\u00a0${name}`);
    if (name !== unit) out = out.replaceAll(` ${name}`, `\u00a0${name}`);
  }
  return out;
}

const FILL_KINDS = {
  species: N_("species name"), item: N_("item name"), move: N_("move name"), nickname: N_("nickname"),
  number: N_("number"), std: N_("standard text"), box: N_("box name"), string: N_("string"), native: N_("native fill"),
};
const WINDOW_KINDS = {
  "layout.window": N_("Text window"),
  table: N_("Name field"),
  "layout.table_window": N_("Table text window"),
  "layout.record_window": N_("Record text window"),
  "layout.code_fill": N_("Code fill"),
  "strings.computed": N_("Computed string group"),
  "strings.interior": N_("Interior string"),
  "strings.pointer_table": N_("Pointer table"),
  "qa.droppable": N_("Droppable placeholders"),
  "pokedex.dex.map": N_("Pokedex species map"),
};
const BOX_NAMES = { dialogue: N_("Dialogue message window"), battle: N_("Battle message window") };
const DEFAULT_NAMES = { dialogue_width: BOX_NAMES.dialogue, battle_width: BOX_NAMES.battle,
  label_min_width: N_("Minimum label width") };
const PROFILE_ROW = /^\[\[([^\[\]]+)\]\] ([^\s:]+)(?: (window|buffer|save_link))?(?::\s*(.*))?$/s;
const PROFILE_KEY = /^\[\[?([^\[\]]+)\]\]?$/;
const PROFILE_STRINGS = /^\[strings\] (save_link(?:_[^\s]+)?)$/;
const POLICY_WINDOW = /^\[language policy\] table_window\.([^\s:]+)(?::\s*(.*))?$/s;

let places = {};

/** Descriptions follow the site language across game projects. */
export function setPlaces(table) {
  places = table && typeof table === "object" ? table : {};
}

export const whereText = (where) => places[where]?.[uiLanguage()] || where;
const fillKind = (kind) => FILL_KINDS[kind] ? t(FILL_KINDS[kind]) : kind;
const fillKinds = (kinds) => kinds.split(", ").map(fillKind).join(", ");

let messages = {}; // the data's explanation templates: code -> English

/** The explanation templates of the shown data; null clears them. */
export function setMessages(table) {
  messages = table && typeof table === "object" ? table : {};
}

// arguments holding explanations; lists and their separators
const NESTED = new Set(["rule", "via", "source", "word", "fill"]);
const JOINED = { reasons: "; ", kinds: ", ", ids: " + " };

/** Whether a value is an explanation: a code with its arguments. */
export const isExplanation = (value) => typeof value?.code === "string";

/** The English text of an explanation, from the message table; a text is its own. */
export function explanationText(value) {
  if (!isExplanation(value)) return value ?? "";
  return messages[value.code].replace(/\{(\w+)\}/g, (_, name) => (name in JOINED
    ? value[name].map(explanationText).join(JOINED[name])
    : NESTED.has(name) ? explanationText(value[name]) : String(value[name])));
}

/** Whether two explanations or texts are the same, in any key order. */
export function sameExplanation(a, b) {
  if (a === b) return true;
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) return false;
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length && keys.every((key) => sameExplanation(a[key], b[key]));
}

/** The descriptions of a window's limits. */
export const LIMITS = [
  N_("the window of {host}, which inserts it: {rule}"),
  N_("{rule}, centred against {ref}"),
  N_("two lines per message box ({via})"),
  N_("two lines per message box"),
  N_("[layout.box_width] {name}: the message box, measured in game ({via})"),
  N_("[layout.box_width] {name}: the message box, measured in game"),
  N_("the English: its widest line"),
  N_("the English: one line"),
  N_("the English: its line count"),
  N_("the English: its box lines"),
  N_("the English: the widest line of its pointer-table run"),
  N_("the English: the widest line of {file}"),
  N_("the English: the most lines of {file}"),
  N_("a one-line field"),
  N_("a field of {n} bytes with the terminator"),
  N_("a field of {n} bytes"),
  N_("a field of {n} byte with the terminator"),
  N_("a field of {n} byte"),
  N_("a long-name pointer of {n} bytes with the terminator"),
  N_("[language policy] window_width.{id}: text budget"),
  N_("[layout.window_width] {id}: measured in game"),
  N_("[layout.window_lines] {id}: measured in game"),
  N_("[keyboard] columns: the key cells of its keyboard"),
  N_("keyboard layout columns: the columns its pack keyboard reaches"),
  N_("in place: its {n} bytes with the terminator, a reference the build cannot rewrite ({kinds})"),
  N_("in place in its {n}-byte field with the terminator"),
  N_("read from byte {byte}, in place in its {n} bytes"),
  N_("its host"),
];

const SLOT_HOLES = { n: NUMBER, width: NUMBER, english: NUMBER, uses: NUMBER, value: NUMBER };
const LIMIT_HOLES = { n: NUMBER, byte: NUMBER };

const literal = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** A key as a pattern: holes as groups. */
function compile(key, head = "", holes = HOLES) {
  const names = [];
  let source = "";
  let last = 0;
  for (const m of key.matchAll(/\{(\w+)\}/g)) {
    source += `${literal(key.slice(last, m.index))}(${holes[m[1]] ?? ".+?"})`;
    names.push(m[1]);
    last = m.index + m[0].length;
  }
  return { key, names, pattern: new RegExp(`^${head}${source}${literal(key.slice(last))}$`, "s") };
}

const KINDS = MESSAGES.map((key) => compile(key));
const ERRORS = ENCODING.map((key) => compile(key));
const TAILS = NOTES.map((key) => compile(key, "(.*) "));
const CHECK_FAILED = N_("check failed: {error}");
const PAGE = [compile(CHECK_FAILED)]; // the page's own problems with values
const FOUND = FINDINGS.map((key) => compile(key, "", GFX_HOLES));
const LINES = REPORT.map((key) => compile(key, "", GFX_HOLES));
const SOURCES = SLOTS.map((key) => compile(key, "", SLOT_HOLES));
const WHYS = SLOT_REASONS.map((key) => compile(key, "", SLOT_HOLES));
const RULES = LIMITS.map((key) => compile(key, "", LIMIT_HOLES));

const valuesOf = (kind, groups) => Object.fromEntries(kind.names.map((name, i) => [name, groups[i]]));

function kindOf(text, kinds) {
  for (const kind of kinds) {
    const m = kind.pattern.exec(text);
    if (m) return { key: kind.key, vars: valuesOf(kind, m.slice(1)) };
  }
  return null;
}

/** A message as {key, vars, notes}; null if unknown. */
export function parseMessage(message) {
  let rest = String(message);
  const notes = [];
  for (let found = true; found;) {
    found = false;
    for (const tail of TAILS) {
      const m = tail.pattern.exec(rest);
      if (!m) continue;
      notes.unshift({ key: tail.key, vars: valuesOf(tail, m.slice(2)) });
      rest = m[1];
      found = true;
      break;
    }
  }
  const kind = kindOf(rest, KINDS);
  if (!kind) return null;
  const error = kind.vars.error === undefined ? null : kindOf(kind.vars.error, ERRORS);
  return { ...kind, notes, ...(error ? { error } : {}) };
}

/** A text translated by its first matching kind. */
function phrase(text, kinds, nested = {}) {
  const kind = kindOf(text, kinds);
  if (!kind) return text;
  const vars = { ...kind.vars };
  for (const [name, inner] of Object.entries(nested)) if (vars[name] !== undefined) vars[name] = inner(vars[name]);
  return t(kind.key, vars);
}

const fillPlace = (where) => (where === CODE_FILL ? t(CODE_FILL) : limitText(where));
const slotHead = (text) => {
  const first = /^(\w+) (.+)$/s.exec(text);
  if (first && Object.hasOwn(FILL_KINDS, first[1]) && !kindOf(text, SOURCES)) {
    return `${fillKind(first[1])} ${slotHead(first[2])}`;
  }
  return phrase(text, SOURCES, { source: slotHead, word: slotHead, where: fillPlace });
};
// the reasons a slot width is partly unknown
const WHY = /^(.*) \(([^()]*)\)$/s;

// a word's note covers its record kind
const WORD_NOTES = { slot_untranslated: N_("{word} (untranslated)"), slot_empty: N_("{word} (translated as empty)") };
const wordHead = (word) => (word.code === "slot_empty" ? `${word.id} ''` : word.id);

// the code-fill descriptions: alone, then with the English width
const FILLS = {
  slot_widest_source: [N_("widest source {id}, {where}"), N_("widest source {id}, English {english} px, {where}")],
  slot_digit: [N_("number of {n} digit, {where}"), N_("number of {n} digit, English {english} px, {where}")],
  slot_digits: [N_("number of {n} digits, {where}"), N_("number of {n} digits, English {english} px, {where}")],
  slot_width: [N_("{width} px, {where}"), N_("{width} px, English {english} px, {where}")],
  slot_width_estimate: [N_("{width} px, unverified estimate, {where}"),
    N_("{width} px, unverified estimate, English {english} px, {where}")],
  slot_label: [N_("{label}, {where}"), N_("{label}, English {english} px, {where}")],
};

/** A code-fill slot source in the site language; a profile label shows as in the slot rows. */
function fillText(source) {
  let fill = source.fill;
  const english = fill.code === "slot_english" ? fill.source_width : null;
  if (english !== null) fill = fill.fill;
  if (!FILLS[fill.code]) return explanationText(source);
  const where = source.code === "slot_code_fill" ? t(CODE_FILL) : fillPlace(source.where);
  return t(FILLS[fill.code][english === null ? 0 : 1],
    { id: fill.id, n: fill.digits, width: fill.width, label: fill.label && limitText(fill.label), english, where });
}

/** A coded slot source in the site language, before its reasons. */
function codedSlot(source) {
  const head = (inner) => codedSlot(inner);
  switch (source.code) {
    case "slot_profile": return t("profile width");
    case "slot_profile_estimate": return t("profile width, unverified estimate");
    case "slot_default": return t("default width");
    case "slot_context": return t("context records");
    case "slot_profile_above":
      return t("profile width, above the widest known {source} {width} px", { source: head(source.source), width: source.width });
    case "slot_profile_estimate_above":
      return t("profile width, unverified estimate, above the widest known {source} {width} px",
        { source: head(source.source), width: source.width });
    case "slot_table": return t("widest of table {table}: {word}", { table: source.table, word: head(source.word) });
    case "slot_native": return t("native table {table}: {ids}", { table: source.table, ids: source.ids.join(" + ") });
    case "slot_number_variable": return t("number of up to {n} digits (a variable)", { n: source.digits });
    case "slot_number": return t("number {value}", { value: source.value });
    case "slot_record_number": return t("number of {n} digits, the record's {value}", { n: source.digits, value: source.value });
    case "slot_untranslated": case "slot_empty": return t(WORD_NOTES[source.code], { word: wordHead(source) });
    case "slot_record": {
      const word = source.word, kind = fillKind(source.kind);
      return WORD_NOTES[word.code] ? t(WORD_NOTES[word.code], { word: `${kind} ${wordHead(word)}` }) : `${kind} ${head(word)}`;
    }
    case "slot_fill": case "slot_code_fill": return fillText(source);
    case "slot_label": return limitText(source.label);
    default: return explanationText(source);
  }
}

const whyText = (why) => {
  switch (why.code) {
    case "slot_no_fill": return t("no fill known");
    case "slot_unfilled": return t("{n} of {uses} walked paths set no value", { n: why.unfilled, uses: why.uses });
    case "slot_unnamed": return t("a value the scripts do not name: {kinds}", { kinds: why.kinds.map(fillKind).join(", ") });
    case "slot_unwalked": return t("{n} script references not walked", { n: why.references });
    case "slot_by_code": return t("shown by code");
    default: return explanationText(why);
  }
};

/** Translate descriptions while preserving inserted words and identifiers. */
export function slotSource(text) {
  if (isExplanation(text)) {
    return keepUnits(text.code === "slot_reasons"
      ? `${codedSlot(text.source)} (${text.reasons.map(whyText).join("; ")})` : codedSlot(text));
  }
  const source = String(text);
  const m = WHY.exec(source);
  const whys = m ? m[2].split("; ") : [];
  if (m && whys.every((why) => kindOf(why, WHYS))) {
    return keepUnits(`${slotHead(m[1])} (${whys.map((why) => phrase(why, WHYS, { kinds: fillKinds })).join("; ")})`);
  }
  return keepUnits(slotHead(source));
}

// the profile row an explanation names: its section, the argument naming it, and a prefix
const PROFILE_ROWS = {
  record_window: ["layout.record_window", "name"], record_window_where: ["layout.record_window", "name"],
  table_window: ["layout.table_window", "name"], table_window_where: ["layout.table_window", "name"],
  policy_table_window: ["language policy", "name", "table_window."],
  policy_table_window_where: ["language policy", "name", "table_window."],
  layout_window: ["layout.window", "name"], layout_window_where: ["layout.window", "name"],
  layout_default: ["layout", "key"], box_width: ["layout.box_width", "box"], box_width_via: ["layout.box_width", "box"],
  window_box: ["layout.window_box", "entry"], window_width: ["layout.window_width", "entry"],
  window_lines: ["layout.window_lines", "entry"], policy_window_width: ["language policy", "entry", "window_width."],
  table_field_window: ["table", "table"], table_buffer: ["table", "table"], table_one_line: ["table", "table"],
  table_field: ["table", "table"], table_field_unterminated: ["table", "table"], table_field_byte: ["table", "table"],
  table_field_byte_unterminated: ["table", "table"], table_long_name: ["table", "table"],
  keyboard_columns: ["keyboard", null, "columns"],
  table_base: ["strings.table_base", "id"], interior: ["strings.interior", "pointer"],
};
const IDS = /\bs[0-9A-F]{7}\b|\b0x[0-9A-Fa-f]+\b|\b(?:hash|map|table|sym):[^\s,)]+/g;

// the profile rows and identifiers of an explanation, in the order of its text
function named(rule, rows, ids) {
  const row = PROFILE_ROWS[rule.code];
  if (row) rows.push(`${row[0]} ${row[2] ?? ""}${row[1] ? rule[row[1]] : ""}`);
  for (const [name, value] of Object.entries(rule)) {
    if (name === "code") continue;
    if (NESTED.has(name)) named(value, rows, ids);
    else ids.push(...[...String(value).matchAll(IDS)].map((match) => match[0]));
  }
}

/** Keep profile identities behind the evidence disclosure. */
export function limitReferences(rule) {
  if (isExplanation(rule)) {
    const rows = [], ids = [];
    named(rule, rows, ids);
    return [...new Set([...rows, ...ids.filter((id) => !rows.some((row) => row.endsWith(` ${id}`)))])];
  }
  const source = String(rule);
  const rows = [...source.matchAll(/\[\[?([^\[\]]+)\]\]? ([^\s,)]+?)(?=:\s|[\s,)]|$)/g)]
    .map((match) => `${match[1]} ${match[2]}`);
  const keys = [...source.matchAll(/\[\[?([^\[\]]+)\]\]?/g)].map((match) => match[1]);
  const ids = [...source.matchAll(IDS)].map((match) => match[0]);
  return [...new Set([...rows, ...keys.filter((key) => !rows.some((row) => row.startsWith(`${key} `))),
    ...ids.filter((id) => !rows.some((row) => row.endsWith(` ${id}`)))])];
}

// the windows an explanation describes: profile section, name and description
const WINDOWS = {
  record_window: "layout.record_window", record_window_where: "layout.record_window",
  table_window: "layout.table_window", table_window_where: "layout.table_window",
  policy_table_window: "layout.table_window", policy_table_window_where: "layout.table_window",
  layout_window: "layout.window", layout_window_where: "layout.window",
};

function windowOf(rule) {
  if (WINDOWS[rule.code]) return { section: WINDOWS[rule.code], name: rule.name, where: rule.where };
  if (rule.code === "table_field_window") return { section: "table", name: rule.table, where: rule.where, field: true };
  if (rule.code === "table_buffer") return { section: "table", name: rule.table, where: rule.where };
  return null;
}

// the limit codes shown as one text
const RULE_TEXTS = {
  window_width: N_("Text window width"), window_lines: N_("Visible lines in the text window"),
  window_box: N_("Message window"), policy_window_width: N_("Translation text budget"),
  keyboard_columns: N_("Keyboard key columns"), box_lines: N_("two lines per message box"),
  box_lines_via: N_("two lines per message box"), table_one_line: N_("a one-line field"),
  english_widest: N_("the English: its widest line"), english_one_line: N_("the English: one line"),
  english_line_count: N_("the English: its line count"), english_box_lines: N_("the English: its box lines"),
  english_run_widest: N_("the English: the widest line of its pointer-table run"),
  pack_keyboard_columns: N_("keyboard layout columns: the columns its pack keyboard reaches"),
  host_default: N_("its host"),
};
// the limit codes shown with their sizes
const SIZES = {
  table_field: N_("a field of {n} bytes with the terminator"), table_field_unterminated: N_("a field of {n} bytes"),
  table_field_byte: N_("a field of {n} byte with the terminator"), table_field_byte_unterminated: N_("a field of {n} byte"),
  table_long_name: N_("a long-name pointer of {n} bytes with the terminator"),
  table_base: N_("in place in its {n}-byte field with the terminator"),
};

/** A coded limit in the site language; a window without a description takes another source's. */
function ruleText(rule, sources) {
  if (RULE_TEXTS[rule.code]) return t(RULE_TEXTS[rule.code]);
  if (SIZES[rule.code]) return t(SIZES[rule.code], { n: rule.bytes });
  switch (rule.code) {
    case "host": return ruleText(rule.rule, sources);
    case "centred": return t("{rule}, centred", { rule: ruleText(rule.rule, sources) });
    case "box_width": case "box_width_via": return t(BOX_NAMES[rule.box] ?? "Text window");
    case "layout_default": return t(DEFAULT_NAMES[rule.key] ?? "Text window");
    case "english_file_widest": return t("the English: the widest line of {file}", { file: rule.file });
    case "english_file_lines": return t("the English: the most lines of {file}", { file: rule.file });
    case "in_place": return t("in place: its {n} bytes with the terminator, a reference the build cannot rewrite ({kinds})",
      { n: rule.bytes, kinds: rule.references });
    case "interior": return t("read from byte {byte}, in place in its {n} bytes", { byte: rule.offset, n: rule.bytes });
    default: break;
  }
  const window = windowOf(rule);
  if (!window) return explanationText(rule);
  if (window.where) return whereText(window.where);
  const described = sources.map((s) => (isExplanation(s.rule) ? windowOf(s.rule) : null)).find((other) =>
    other?.section === window.section && other.name === window.name && other.where
    && (other.section !== "table" || other.field));
  return described ? whereText(described.where) : t(WINDOW_KINDS[window.section]);
}

const windowRule = (rule) => String(rule).replace(POLICY_WINDOW, (_, name, where) =>
  `[[layout.table_window]] ${name}${where ? `: ${where}` : ""}`);

/** Show descriptions without profile section notation; `sources` describe a coded window. */
export function limitText(rule, sources = []) {
  if (isExplanation(rule)) return ruleText(rule, sources);
  const source = windowRule(rule).replace(/^profile /, "");
  const host = /^the window of .+?, which inserts it: (.*)$/s.exec(source);
  if (host) return limitText(host[1]);
  const centred = /^(.*), centred against \S+$/s.exec(source);
  if (centred) return t("{rule}, centred", { rule: limitText(centred[1]) });
  const box = /^\[layout.box_width\] ([^:]+):/.exec(source);
  if (box) return t(BOX_NAMES[box[1]] ?? "Text window");
  const layout = /^\[layout\] (\w+)$/.exec(source);
  if (layout) return t(DEFAULT_NAMES[layout[1]] ?? "Text window");
  if (/^\[layout.window_width\]/.test(source)) return t("Text window width");
  if (/^\[layout.window_lines\]/.test(source)) return t("Visible lines in the text window");
  if (/^\[layout.window_box\]/.test(source)) return t("Message window");
  if (/^\[language policy\] window_width\./.test(source)) return t("Translation text budget");
  if (/^\[keyboard\]/.test(source)) return t("Keyboard key columns");
  if (source.startsWith("two lines per message box (")) return t("two lines per message box");
  if (!kindOf(source, RULES)) {
    const bare = PROFILE_KEY.exec(source);
    if (bare && Object.hasOwn(WINDOW_KINDS, bare[1])) return t(WINDOW_KINDS[bare[1]]);
    if (PROFILE_STRINGS.test(source)) return t("Save or link text");
    const row = PROFILE_ROW.exec(source);
    // their descriptions are phrases, not where texts
    if (row && ["strings.table_base", "strings.interior"].includes(row[1]) && row[4]) {
      return phrase(row[4], RULES);
    }
    if (row && Object.hasOwn(WINDOW_KINDS, row[1])) {
      if (row[4]?.trim()) return row[1] === "table" && !row[3]
        ? phrase(row[4], RULES) : whereText(row[4]);
      return t(WINDOW_KINDS[row[1]]);
    }
  }
  return phrase(whereText(t(source)), RULES, { rule: limitText, via: limitText, where: limitText });
}

let reasons = {}; // the policy's typography reasons: reason -> {code: text}

/** The project's typography reasons in site languages; null clears them. */
export function setReasons(table) {
  reasons = table && typeof table === "object" ? table : {};
}

const reasonText = (reason) => reasons[reason]?.[uiLanguage()] ?? reason;

// one slot of a width note
const SLOT = /^(\{[^{}]*\} = \d+ px \()(.*)\)$/s;
const slotText = (slots) => slots.split("; ").map((item) => {
  const m = SLOT.exec(item);
  return m ? `${m[1]}${slotSource(m[2])})` : item;
}).join("; ");

/** The problem of a failed check, as stored. */
export const checkFailed = (error) => CHECK_FAILED.replace("{error}", () => String(error));

/** A check message in the site language, values kept. */
export function checkText(message) {
  const parts = parseMessage(message);
  if (!parts) {
    const page = kindOf(String(message), PAGE);
    return keepUnits(page ? t(page.key, page.vars) : t(String(message)));
  }
  const vars = { ...parts.vars };
  if (parts.error) vars.error = t(parts.error.key, parts.error.vars);
  if (vars.slots !== undefined) vars.slots = slotText(vars.slots);
  if (vars.reason !== undefined) vars.reason = reasonText(vars.reason);
  if (vars.where !== undefined) vars.where = limitText(vars.where);
  return keepUnits([t(parts.key, vars), ...parts.notes.map((note) => t(note.key, note.vars))].join(" "));
}

// a failed line of the report
export const FAIL = N_("FAIL");
const FAIL_PREFIX = `${FAIL}     `;
// a finding the report names its graphic before
const NAMED = /^([^\s:]+): (.*)$/s;

/** A graphics check line as {key, vars}; null if unknown. */
export function parseFinding(text) {
  const kind = kindOf(text, FOUND) ?? kindOf(text, LINES);
  if (kind) return kind;
  const m = NAMED.exec(text);
  const inner = m && kindOf(m[2], FOUND);
  return inner ? { ...inner, graphic: m[1] } : null;
}

/** A graphics report line in the site language. */
export function findingText(text) {
  const line = String(text);
  if (line.startsWith(FAIL_PREFIX)) return `${t(FAIL)}     ${findingText(line.slice(FAIL_PREFIX.length))}`;
  const parts = parseFinding(line);
  if (!parts) return t(line);
  const shown = t(parts.key, parts.vars);
  return parts.graphic ? `${parts.graphic}: ${shown}` : shown;
}

/** The extraction phrase; the traceback stays a value. */
export function extractionFailed(error) {
  return t(N_("Extraction failed: {error}"), { error });
}

/** The layout phrase; its title and error stay values. */
export function layoutFailed(title, error) {
  return t(N_("{title} layout: {error}"), { title, error });
}
