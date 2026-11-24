// The check's messages in the site language.
// Problems and explanations are stored as codes with arguments;
// a problem's template comes from the data's message table.

import { N_, t, uiLanguage } from "./ui.js";

const NUMBER = "\\d+";

/** The templates of the check's problems, the keys they show with. */
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
  N_("not centred in the {window} px window: lead with {token}"),
  N_("glossary term {term} -> {translation} not found"),
  N_("no inventory entry for this id"),
  N_("substitution measurements unavailable: {error}"),
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

/** The notes a problem may end with, by their codes. */
export const NOTES = {
  warning: N_("(warning)"),
  unmeasured_width: N_("[unmeasured limit: the English width]"),
  unmeasured_lines: N_("[unmeasured limit: the English line count]"),
  unverified_width: N_("[unverified limit: an estimate for this language]"),
  prefix_stripped: N_("(prefix {prefix} stripped)"),
};

/** The templates of an inserted word in a problem, with its extras. */
export const SLOT_ITEMS = [
  N_("{token} = {width} px ({source})"),
  N_("{source}; recorded width; sources unavailable"),
  N_("{source}; {parts}"),
  N_("«{text}» {width} px"),
  N_("no prefix 0 px"),
];

/** The wordings of the toolkit's rules and their fills, the keys they show with. */
export const WORDINGS = [
  N_("Copied into save or link data: translated like all other text; in trades and link play with the original ROM, unsupported glyphs may display incorrectly."),
  N_("Moves with {others} as one block: code reaches the group by pointer arithmetic (pool word {pointer}). At most {bytes} bytes, the gap to {next} less the terminator; a longer translation stays English."),
  N_("Moves with {others} as one block: code reaches the group by pointer arithmetic (pool word {pointer}). The last member: no byte gap binds it."),
  N_("Begins with {head}, the first {offset} bytes of the English; code reads the text from byte {offset} (pool word {pointer}); kept in place: at most {bytes} bytes."),
  N_("Character code written by game code: at most {n} characters; only that byte changes; empty writes {pad}."),
  N_("Character code written by game code: at most {n} character; only that byte changes; empty writes {pad}."),
  N_("Character code written by game code: at most {n} characters; only that byte changes; empty writes 0xFF, the terminator."),
  N_("Character code written by game code: at most {n} character; only that byte changes; empty writes 0xFF, the terminator."),
  N_("The code fills {slot} with {fill}."),
  N_("The code fills {slot} with {fill} ({where})."),
  N_("text measured from {measured}"),
  N_("one of {n} source strings, measured at their widest rendering"),
  N_("one of {n} source string, measured at their widest rendering"),
  N_("a number of {n} digits"),
  N_("a number of {n} digit"),
  N_("text {width} px wide"),
  N_("May leave out {tokens}: the code fills it with text only the English needs."),
  N_("The game never shows this text: it is not translated, and an approved translation stays."),
  N_("Inserted as {slot} into {n} texts ({entries}): one form serves every sentence it fills."),
  N_("Inserted as {slot} into {n} text ({entries}): one form serves every sentence it fills."),
  N_("Inserted as {slot} into {n} texts ({entries} and {more} more): one form serves every sentence it fills."),
  N_("{slot} takes {n} inserted words ({entries}): the sentence is built around their fixed forms."),
  N_("{slot} takes {n} inserted word ({entries}): the sentence is built around their fixed forms."),
  N_("{slot} takes {n} inserted words ({entries} and {more} more): the sentence is built around their fixed forms."),
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
const PROFILE_KEY = /^\[\[?([^\[\]]+)\]\]?$/;

let places = {};

/** Descriptions follow the site language across game projects. */
export function setPlaces(table) {
  places = table && typeof table === "object" ? table : {};
}

export const whereText = (where) => places[where]?.[uiLanguage()] || where;
const fillKind = (kind) => FILL_KINDS[kind] ? t(FILL_KINDS[kind]) : kind;

let messages = {}; // the data's templates: code -> English

/** The explanation and problem templates of the shown data; null clears them. */
export function setMessages(table) {
  messages = table && typeof table === "object" ? table : {};
}

const CHECK_FAILED = N_("check failed: {error}");
// the page's own problems, also for data without their templates
const OWN = { check_failed: CHECK_FAILED, glossary_missing: N_("glossary term {term} -> {translation} not found") };
const templateOf = (code) => messages[code] ?? OWN[code] ?? code;

// arguments holding explanations; lists and their separators
const NESTED = new Set(["rule", "via", "source", "word", "fill", "where", "error", "message"]);
const JOINED = { reasons: "; ", kinds: ", ", ids: " + ", slots: "; ", parts: ", ", entries: ", " };
// a profile label inside a sentence
const LOWERED = new Set(["measured"]);

/** Whether a value is an explanation: a code with its arguments. */
export const isExplanation = (value) => typeof value?.code === "string";

/** The English text of an explanation, from the message table; a text is its own. */
export function explanationText(value) {
  if (!isExplanation(value)) return value ?? "";
  return templateOf(value.code).replace(/\{(\w+)\}/g, (_, name) => (name in JOINED
    ? value[name].map(explanationText).join(JOINED[name])
    : LOWERED.has(name) ? String(value[name]).toLowerCase()
      : NESTED.has(name) ? explanationText(value[name]) : String(value[name])));
}

/** Whether two explanations or texts are the same, in any key order. */
export function sameExplanation(a, b) {
  if (a === b) return true;
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) return false;
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length && keys.every((key) => sameExplanation(a[key], b[key]));
}

const literal = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** A graphics key as a pattern: holes as groups. */
function compile(key, head = "", holes = GFX_HOLES) {
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

const FOUND = FINDINGS.map((key) => compile(key));
const LINES = REPORT.map((key) => compile(key));

const valuesOf = (kind, groups) => Object.fromEntries(kind.names.map((name, i) => [name, groups[i]]));

function kindOf(text, kinds) {
  for (const kind of kinds) {
    const m = kind.pattern.exec(text);
    if (m) return { key: kind.key, vars: valuesOf(kind, m.slice(1)) };
  }
  return null;
}

const fillPlace = (where) => (where === CODE_FILL ? t(CODE_FILL) : limitText(where));

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

/** Translate descriptions while preserving inserted words and identifiers; a text shows as written. */
export function slotSource(text) {
  if (!isExplanation(text)) return keepUnits(String(text));
  return keepUnits(text.code === "slot_reasons"
    ? `${codedSlot(text.source)} (${text.reasons.map(whyText).join("; ")})` : codedSlot(text));
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
  rule_table: ["table", "table"], rule_table_save_link: ["table", "table"], rule_strings: ["strings", "key"],
  rule_computed: ["strings.computed", "pointer"], rule_interior: ["strings.interior", "pointer"],
  rule_code_fill: ["layout.code_fill", "name"], rule_code_fill_where: ["layout.code_fill", "name"],
  rule_droppable: ["qa.droppable", null], rule_pointer_table: ["strings.pointer_table", "table"],
};
const IDS = /\bs[0-9A-F]{7}\b|\b0x[0-9A-Fa-f]+\b|\b(?:hash|map|table|sym):[^\s,)]+/g;

// the profile rows and identifiers of an explanation, in the order of its text
function named(rule, rows, ids) {
  const row = PROFILE_ROWS[rule.code];
  if (row) rows.push([row[0], `${row[2] ?? ""}${row[1] ? rule[row[1]] : ""}`].filter(Boolean).join(" "));
  for (const [name, value] of Object.entries(rule)) {
    if (name === "code") continue;
    if (NESTED.has(name) && isExplanation(value)) named(value, rows, ids);
    else ids.push(...[...String(value).matchAll(IDS)].map((match) => match[0]));
  }
}

/** Keep profile identities behind the evidence disclosure; a text names only its identifiers. */
export function limitReferences(rule) {
  if (!isExplanation(rule)) return [...new Set([...String(rule).matchAll(IDS)].map((match) => match[0]))];
  const rows = [], ids = [];
  named(rule, rows, ids);
  return [...new Set([...rows, ...ids.filter((id) => !rows.some((row) => row.endsWith(` ${id}`)))])];
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
  rule_table: WINDOW_KINDS.table, rule_table_save_link: WINDOW_KINDS.table, rule_strings: N_("Save or link text"),
  rule_computed: WINDOW_KINDS["strings.computed"], rule_interior: WINDOW_KINDS["strings.interior"],
  rule_code_fill: WINDOW_KINDS["layout.code_fill"], rule_droppable: WINDOW_KINDS["qa.droppable"],
  rule_pointer_table: WINDOW_KINDS["strings.pointer_table"],
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
    case "rule_code_fill_where": return whereText(rule.where);
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

/** The rule sources the toolkit writes as text, shown by their keys. */
export const RULE_SOURCES = [N_("catalog context"), N_("reach study, review")];

/** A limit, a window or a rule source in words, without profile notation; `sources` describe a coded window. */
export function limitText(rule, sources = []) {
  return isExplanation(rule) ? ruleText(rule, sources) : whereText(t(String(rule)));
}

// a wording's argument in the site language
function wordingArgument(name, value) {
  switch (name) {
    case "fill": return codedWording(value);
    case "where": return whereText(value);
    case "measured": return whereText(value).toLowerCase();
    case "entries": return value.join(JOINED.entries);
    default: return value;
  }
}

/** A coded wording in the site language: its template from the message table. */
function codedWording(wording) {
  const vars = {};
  for (const [name, value] of Object.entries(wording)) if (name !== "code") vars[name] = wordingArgument(name, value);
  return t(templateOf(wording.code), vars);
}

/** A rule's wording in the site language; a text shows its profile keys in words. */
export function wordingText(text) {
  if (isExplanation(text)) return codedWording(text);
  return text.replace(/\[\[?[^\[\]\n]+\]\]?/g, (key) => {
    const bare = PROFILE_KEY.exec(key);
    return bare && Object.hasOwn(WINDOW_KINDS, bare[1]) ? t(WINDOW_KINDS[bare[1]]) : whereText(t(key));
  });
}

let reasons = {}; // the policy's typography reasons: reason -> {code: text}

/** The project's typography reasons in site languages; null clears them. */
export function setReasons(table) {
  reasons = table && typeof table === "object" ? table : {};
}

const reasonText = (reason) => reasons[reason]?.[uiLanguage()] ?? reason;

/** The problem of a failed check, as stored. */
export const checkFailed = (error) => ({ code: "check_failed", error: String(error) });

/** The problem of a glossary term the translation lacks, as the check words it. */
export const glossaryMissing = (term, translation) =>
  ({ code: "warning", message: { code: "glossary_missing", term, translation } });

/** A problem without the notes after it. */
export function unnoted(message) {
  let own = message;
  while (isExplanation(own) && Object.hasOwn(NOTES, own.code)) own = own.message;
  return own;
}

/** The glossary terms the problems name as not found: {term, translation} as quoted. */
export const missingTerms = (problems) => problems.map((p) => unnoted(p.message))
  .filter((message) => message?.code === "glossary_missing");

// the wrappers of an inserted word's source in a message
const SLOT_EXTRAS = new Set(["slot_unavailable", "slot_parts"]);

/** A problem argument in the site language. */
function argumentText(name, value) {
  switch (name) {
    case "where": return limitText(value);
    case "slots": return value.map(problemText).join("; ");
    case "parts": return value.map(problemText).join(", ");
    case "source": return SLOT_EXTRAS.has(value?.code) ? problemText(value) : slotSource(value);
    case "reason": return reasonText(value);
    case "error": case "message": return isExplanation(value) ? problemText(value) : String(value);
    default: return value;
  }
}

/** A coded problem in the site language: its template from the message table, then its notes. */
function problemText(problem) {
  if (Object.hasOwn(NOTES, problem.code)) {
    return `${argumentText("message", problem.message)} ${t(NOTES[problem.code], { prefix: problem.prefix })}`;
  }
  const vars = {};
  for (const [name, value] of Object.entries(problem)) if (name !== "code") vars[name] = argumentText(name, value);
  return t(templateOf(problem.code), vars);
}

/** A check message in the site language, values kept; a text is a key or shows as written. */
export function checkText(message) {
  return keepUnits(isExplanation(message) ? problemText(message) : t(String(message)));
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
