// The check's messages in the site language.
// A message is its kind, then its separately translated notes.
// Problems are stored in English.

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

/** Translate descriptions while preserving inserted words and identifiers. */
export function slotSource(text) {
  const source = String(text);
  const m = WHY.exec(source);
  const whys = m ? m[2].split("; ") : [];
  if (m && whys.every((why) => kindOf(why, WHYS))) {
    return keepUnits(`${slotHead(m[1])} (${whys.map((why) => phrase(why, WHYS, { kinds: fillKinds })).join("; ")})`);
  }
  return keepUnits(slotHead(source));
}

/** Keep profile identities behind the evidence disclosure. */
export function limitReferences(rule) {
  const source = String(rule);
  const rows = [...source.matchAll(/\[\[?([^\[\]]+)\]\]? ([^\s,)]+?)(?=:\s|[\s,)]|$)/g)]
    .map((match) => `${match[1]} ${match[2]}`);
  const keys = [...source.matchAll(/\[\[?([^\[\]]+)\]\]?/g)].map((match) => match[1]);
  const ids = [...source.matchAll(/\bs[0-9A-F]{7}\b|\b0x[0-9A-Fa-f]+\b|\b(?:hash|map|table|sym):[^\s,)]+/g)]
    .map((match) => match[0]);
  return [...new Set([...rows, ...keys.filter((key) => !rows.some((row) => row.startsWith(`${key} `))),
    ...ids.filter((id) => !rows.some((row) => row.endsWith(` ${id}`)))])];
}

const windowRule = (rule) => String(rule).replace(POLICY_WINDOW, (_, name, where) =>
  `[[layout.table_window]] ${name}${where ? `: ${where}` : ""}`);

/** Show descriptions without profile section notation. */
export function limitText(rule, sources = []) {
  const source = windowRule(rule).replace(/^profile /, "");
  const host = /^the window of .+?, which inserts it: (.*)$/s.exec(source);
  if (host) return limitText(host[1], sources);
  const centred = /^(.*), centred against \S+$/s.exec(source);
  if (centred) return t("{rule}, centred", { rule: limitText(centred[1], sources) });
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
    if (row && Object.hasOwn(WINDOW_KINDS, row[1])) {
      if (row[4]?.trim()) return row[1] === "table" && !row[3]
        ? phrase(row[4], RULES) : whereText(row[4]);
      const described = sources.map((s) => PROFILE_ROW.exec(windowRule(s.rule))).find((other) =>
        other?.[1] === row[1] && other[2] === row[2] && other[4]?.trim()
        && (other[1] !== "table" || other[3] === "window"));
      if (described) return whereText(described[4]);
      return t(WINDOW_KINDS[row[1]]);
    }
    if (row && ["strings.table_base", "strings.interior"].includes(row[1]) && row[4]) {
      return phrase(row[4], RULES);
    }
  }
  const nested = (text) => limitText(text, sources);
  return phrase(whereText(t(source)), RULES, { rule: nested, via: nested, where: nested });
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
