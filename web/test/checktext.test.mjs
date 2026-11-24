// The check's messages: kinds, notes, values and site language.
import assert from "node:assert/strict";
import { test } from "node:test";

import { ENCODING, MESSAGES, NOTES, checkText, explanationText, extractionFailed, findingText, layoutFailed, limitReferences,
  limitText, parseFinding, parseMessage, sameExplanation, setMessages, setPlaces, setReasons, slotSource } from "../js/checktext.js";
import { slotRows } from "../js/slots.js";
import { setCatalog, setUiLanguage, t } from "../js/ui.js";

const RU = {
  "line too wide: {width} > {limit} px: {text}": "строка слишком широкая: {width} > {limit} px: {text}",
  "line too wide: {width} > {limit} px: {text} with {slots}": "строка слишком широкая: {width} > {limit} px: {text} при {slots}",
  "not encodable: {error}": "не кодируется: {error}",
  "character {char} (U+{unicode}) has no byte, in {text}": "у символа {char} (U+{unicode}) нет байта, в {text}",
  "glossary term {term} -> {translation} not found": "термин глоссария {term} -> {translation} не найден",
  "(warning)": "(предупреждение)",
  "[unmeasured limit: the English width]": "[предел не измерен: ширина английского текста]",
  "(prefix {prefix} stripped)": "(префикс {prefix} отброшен)",
  "profile width": "ширина из профиля",
  "Preserve the source placeholders and control codes.": "Сохраните подстановки и управляющие коды оригинала.",
};

test("a message is its kind with its values, then its notes in order", () => {
  assert.deepEqual(parseMessage("line too wide: 210 > 196 px: 'Привет, {PLAYER}! Как дела?' with {PLAYER} = 42 px (profile width) (prefix 'A: ' stripped) [unmeasured limit: the English width]"), {
    key: "line too wide: {width} > {limit} px: {text} with {slots}",
    vars: { width: "210", limit: "196", text: "'Привет, {PLAYER}! Как дела?'", slots: "{PLAYER} = 42 px (profile width)" },
    notes: [{ key: "(prefix {prefix} stripped)", vars: { prefix: "'A: '" } },
      { key: "[unmeasured limit: the English width]", vars: {} }],
  });
  assert.deepEqual(parseMessage("glossary term 'Poké Ball' -> \"Покебол's\" not found (warning)").vars,
    { term: "'Poké Ball'", translation: "\"Покебол's\"" });
  assert.equal(parseMessage("placeholders differ").key, "placeholders differ");
  assert.equal(parseMessage("line too wide: 99 > 98 px in the bag (unbound-small)").vars.where, "the bag");
  assert.equal(parseMessage("A message the check never gives"), null);
  assert.equal(parseMessage("line too wide: wide > 196 px: 'x'"), null, "a number hole takes digits only");
});

test("the character map's error inside a message is a kind of its own", () => {
  const parts = parseMessage("not encodable: character 'ж' (U+0436) has no byte, in 'жук'");
  assert.deepEqual(parts.error, { key: "character {char} (U+{unicode}) has no byte, in {text}",
    vars: { char: "'ж'", unicode: "0436", text: "'жук'" } });
  assert.equal(parseMessage("not encodable: no terminator after 0x10").error, undefined);
});

test("the text follows the site catalog and keeps the values", () => {
  setCatalog(RU);
  try {
    assert.equal(checkText("line too wide: 210 > 196 px: 'Как дела?' with {PLAYER} = 42 px (profile width); {STR_VAR_1} = 60 px (widest of table items) (warning)"),
      "строка слишком широкая: 210 > 196\u00a0px: 'Как дела?' при {PLAYER} = 42\u00a0px (ширина из профиля); {STR_VAR_1} = 60\u00a0px (widest of table items) (предупреждение)");
    assert.equal(checkText("not encodable: character 'ж' (U+0436) has no byte, in 'жук'"),
      "не кодируется: у символа 'ж' (U+0436) нет байта, в 'жук'");
    assert.equal(checkText("not encodable: no terminator after 0x10"), "не кодируется: no terminator after 0x10");
    assert.equal(checkText("glossary term 'Potion' -> 'Зелье' not found (warning)"),
      "термин глоссария 'Potion' -> 'Зелье' не найден (предупреждение)");
    assert.equal(checkText("Preserve the source placeholders and control codes."), "Сохраните подстановки и управляющие коды оригинала.");
    assert.equal(checkText("A message the check never gives"), "A message the check never gives");
    assert.equal(checkText("too many lines: 4 > 3"), "too many lines: 4 > 3", "a kind without a translation stays English");
  } finally {
    setCatalog(null);
  }
  assert.equal(checkText("line too wide: 210 > 196 px: 'x' (warning)"), "line too wide: 210 > 196\u00a0px: 'x' (warning)");
});

test("every key's holes are named and every kind is distinct", () => {
  const keys = [...MESSAGES, ...ENCODING, ...NOTES];
  assert.equal(new Set(keys).size, keys.length);
  for (const key of keys) assert.match(key, /^(?:[^{}]|\{\w+\})*$/, key);
});

test("a graphics finding or report line is its kind with its values, in the site language", () => {
  assert.deepEqual(parseFinding("pixel 30,2: index 4 outside the text areas of the area form"),
    { key: "pixel {pixel}: index {index} outside the text areas of the area form", vars: { pixel: "30,2", index: "4" } });
  assert.deepEqual(parseFinding("T1: 15 tiles is below the original 16; tiles can only be appended"),
    { key: "{tiles} tiles is below the original {original}; tiles can only be appended",
      vars: { tiles: "15", original: "16" }, graphic: "T1" });
  assert.equal(parseFinding("pixel 3,4: index x -> 2 outside the text areas"), null, "a number hole takes digits only");
  setCatalog({
    "FAIL": "ОШИБКА",
    "pixel {pixel}: index {was} -> {index} outside the text areas": "пиксель {pixel}: индекс {was} -> {index} вне текстовых областей",
    "{tiles} tiles is below the original {original}; tiles can only be appended": "тайлов {tiles} — меньше исходных {original}; тайлы можно только добавлять",
    "encoded  {bytes} bytes, slot {slot}: relocated": "закодировано байт: {bytes}, слот {slot}: перенесено",
  });
  try {
    assert.equal(findingText("FAIL     pixel 0,0: index 1 -> 2 outside the text areas"),
      "ОШИБКА     пиксель 0,0: индекс 1 -> 2 вне текстовых областей");
    assert.equal(findingText("FAIL     T1: 15 tiles is below the original 16; tiles can only be appended"),
      "ОШИБКА     T1: тайлов 15 — меньше исходных 16; тайлы можно только добавлять");
    assert.equal(findingText("encoded  143 bytes, slot 142: relocated"), "закодировано байт: 143, слот 142: перенесено");
    assert.equal(findingText("LZ77 block at 0x1000 runs past the end of the data"), "LZ77 block at 0x1000 runs past the end of the data");
  } finally {
    setCatalog(null);
  }
  assert.equal(findingText("encoded  143 bytes, slot 142: relocated"), "encoded  143 bytes, slot 142: relocated");
});

test("graphics extraction errors translate only the toolkit's phrases", (ctx) => {
  ctx.after(() => setCatalog(null));
  setCatalog({
    "Extraction failed: {error}": "Извлечь не удалось: {error}",
    "{title} layout: {error}": "{title}, вид на экране: {error}",
  });
  const traceback = "Traceback (most recent call last):\n  File \"<exec>\", line 1\nValueError: bad graphic";
  assert.equal(extractionFailed(traceback), `Извлечь не удалось: ${traceback}`);
  assert.equal(layoutFailed("Английский", traceback), `Английский, вид на экране: ${traceback}`);
});

test("a typography reason takes the policy's text in the site language", (ctx) => {
  ctx.after(() => { setReasons(null); setCatalog(null); setUiLanguage("en"); });
  const message = "typography: three dots: use the ellipsis character …";
  setReasons({ "three dots: use the ellipsis character …": { ru: "три точки: используйте символ многоточия …" } });
  setUiLanguage("ru");
  setCatalog({ "typography: {reason}": "типографика: {reason}" });
  assert.equal(checkText(message), "типографика: три точки: используйте символ многоточия …");
  assert.equal(checkText("typography: double space"), "типографика: double space", "a reason without a text stays");
  setUiLanguage("en");
  setCatalog(null);
  assert.equal(checkText(message), message);
});

test("a slot source in the site language: its phrases, the reasons it is partly unknown, its words kept", (ctx) => {
  ctx.after(() => setCatalog(null));
  const source = "profile width, above the widest known species table:species/499 'Munchlax' (untranslated) 47 px "
    + "(1 of 1 walked paths set no value; 3 script references not walked)";
  setCatalog({
    "profile width, above the widest known {source} {width} px": "ширина из профиля, больше самого широкого известного: {source} {width} px",
    "{word} (untranslated)": "{word} (не переведено)",
    "{n} of {uses} walked paths set no value": "путей без значения: {n} из {uses} пройденных",
    "{n} script references not walked": "непройденных ссылок скриптов: {n}",
    "{width} px, {where}": "{width} px, {where}", "code fill": "заполнение кодом",
    "line too wide: {width} > {limit} px: {text} with {slots}": "строка слишком широкая: {width} > {limit} px: {text} при {slots}",
  });
  assert.equal(slotSource(source), "ширина из профиля, больше самого широкого известного: species name table:species/499 "
    + "'Munchlax' (не переведено) 47\u00a0px (путей без значения: 1 из 1 пройденных; непройденных ссылок скриптов: 3)");
  assert.equal(slotSource("66 px, code fill"), "66\u00a0px, заполнение кодом");
  assert.equal(slotSource("66 px, battle text, name of the opposing trainer"), "66\u00a0px, battle text, name of the opposing trainer");
  assert.equal(slotSource("number of up to 5 digits (a variable)"), "number of up to 5 digits (a variable)");
  assert.equal(checkText("line too wide: 210 > 196 px: 'x' with {STR_VAR_1} = 60 px (66 px, code fill)"),
    "строка слишком широкая: 210 > 196\u00a0px: 'x' при {STR_VAR_1} = 60\u00a0px (66\u00a0px, заполнение кодом)");
  setCatalog(null);
  assert.equal(slotSource(source), source.replace("known species table", "known species name table").replaceAll(" px", "\u00a0px")); // the fill kind shows its label
});

test("slot measurements use the translated unit without a break", (context) => {
  context.after(() => setCatalog(null));
  setCatalog({ px: "px", "code fill": "заполнение кодом", "{width} px, {where}": "{width} px, {where}" });
  assert.equal(slotSource("59 px, code fill"), "59\u00a0px, заполнение кодом");
  assert.equal(checkText("line too wide: 119 > 110 px: 'x'"), "line too wide: 119 > 110\u00a0px: 'x'");
  assert.ok(!slotSource("59 px, code fill").includes("59 px"));
});

test("a limit description in the site language, nested descriptions too", (ctx) => {
  ctx.after(() => setCatalog(null));
  const rule = "the window of hash:00c6180b, which inserts it: [layout.box_width] dialogue: the message box, measured in game, "
    + "centred against hash:47007de3";
  setCatalog({ "Dialogue message window": "Окно диалога", "{rule}, centred": "{rule}, по центру" });
  assert.equal(limitText(rule), "Окно диалога, по центру");
  assert.equal(limitText("[[layout.record_window]] journal"), "Record text window");
  setCatalog(null);
  assert.equal(limitText(rule), "Dialogue message window, centred");
});

test("profile sources use descriptions in words and retain identities as references", (context) => {
  context.after(() => { setPlaces(null); setCatalog(null); setUiLanguage("en"); });
  setPlaces({ "battle text, name of the opposing trainer": { ru: "боевой текст, имя тренера противника" } });
  const rule = "profile [[layout.code_fill]] fill-20: battle text, name of the opposing trainer";
  assert.equal(limitText(rule), "battle text, name of the opposing trainer");
  assert.deepEqual(limitReferences(rule), ["layout.code_fill fill-20"]);
  setCatalog({ "battle text, name of the opposing trainer": "unused" });
  setUiLanguage("ru");
  assert.equal(limitText(rule), "боевой текст, имя тренера противника");
  setUiLanguage("en");
  for (const [source, label, reference] of [
    ["profile [[qa.droppable]]", "Droppable placeholders", "qa.droppable"],
    ["profile [[strings.pointer_table]] credits", "Pointer table", "strings.pointer_table credits"],
    ["profile [[table]] species save_link", "Name field", "table species"],
    ["profile [strings] save_link", "Save or link text", "strings save_link"],
    ["profile [strings] save_link_letters", "Save or link text", "strings save_link_letters"],
    ["profile [pokedex.dex.map]", "Pokedex species map", "pokedex.dex.map"],
  ]) {
    assert.equal(limitText(source), label);
    assert.deepEqual(limitReferences(source), [reference]);
  }
});

test("window references survive nested bases separately from their descriptions", () => {
  const rule = "the window of sym:host, which inserts it: [[layout.table_window]] sample: menu: text, centred against sym:centre";
  assert.equal(limitText(rule), "menu: text, centred");
  assert.deepEqual(limitReferences(rule), ["layout.table_window sample", "sym:host", "sym:centre"]);
  const via = "two lines per message box ([[layout.table_window]] sample)";
  assert.equal(limitText(via), "two lines per message box");
  assert.equal(limitText(via, [{ rule: "[[layout.table_window]] sample: menu: text" }]),
    "two lines per message box");
  assert.deepEqual(limitReferences(via), ["layout.table_window sample"]);
  for (const [rule, text, reference] of [
    ["[[table]] names: a field of 1 byte", "a field of 1 byte", "table names"],
    ["[[table]] names: a field of 1 byte with the terminator", "a field of 1 byte with the terminator", "table names"],
    ["[[table]] names: a field of 12 bytes", "a field of 12 bytes", "table names"],
    ["[[table]] names: a long-name pointer of 24 bytes with the terminator", "a long-name pointer of 24 bytes with the terminator", "table names"],
    ["[[table]] names buffer: menu: field", "menu: field", "table names"],
    ["[language policy] table_window.sample: menu: text", "menu: text", "language policy table_window.sample"],
    ["[language policy] table_window.sample", "Table text window", "language policy table_window.sample"],
    ["[[strings.table_base]] sample: in place in its 8-byte field with the terminator", "in place in its 8-byte field with the terminator", "strings.table_base sample"],
    ["[[strings.interior]] 0x100: read from byte 4, in place in its 12 bytes", "read from byte 4, in place in its 12 bytes", "strings.interior 0x100"],
  ]) {
    assert.equal(limitText(rule), text);
    assert.deepEqual(limitReferences(rule), [reference]);
  }
});


test("consumer diagnostics retain their binding window", (context) => {
  context.after(() => setCatalog(null));
  const arrow = "line leaves no room for the prompt arrow: box 1: 170 > 166 px in trade window: 'Draft'";
  const ordinary = "line too wide: 180 > 176 px: 'Draft'; window trade window";
  assert.equal(parseMessage(arrow).vars.where, "trade window");
  assert.equal(parseMessage(ordinary).vars.where, "trade window");
  setCatalog({
    "line leaves no room for the prompt arrow: box {box}: {width} > {limit} px in {where}: {text}":
      "Arrow: {width}/{limit}, {where}, {text}",
    "line too wide: {width} > {limit} px: {text}; window {where}": "Width: {width}/{limit}, {where}, {text}",
  });
  assert.equal(checkText(arrow), "Arrow: 170/166, trade window, 'Draft'");
  assert.equal(checkText(ordinary), "Width: 180/176, trade window, 'Draft'");
  assert.equal(parseMessage(arrow.replace("166", "-1")).vars.limit, "-1");
});

// the data's explanation templates (gen3tl.check.provenance.MESSAGES)
const TEMPLATES = {
  record_window: "[[layout.record_window]] {name}", record_window_where: "[[layout.record_window]] {name}: {where}",
  table_window: "[[layout.table_window]] {name}", table_window_where: "[[layout.table_window]] {name}: {where}",
  policy_table_window: "[language policy] table_window.{name}",
  policy_table_window_where: "[language policy] table_window.{name}: {where}",
  layout_window: "[[layout.window]] {name}", layout_window_where: "[[layout.window]] {name}: {where}",
  layout_default: "[layout] {key}", box_width: "[layout.box_width] {box}: the message box, measured in game",
  box_width_via: "[layout.box_width] {box}: the message box, measured in game ({via})",
  box_lines: "two lines per message box", box_lines_via: "two lines per message box ({via})",
  window_box: "[layout.window_box] {entry}", window_width: "[layout.window_width] {entry}: measured in game",
  window_lines: "[layout.window_lines] {entry}: measured in game",
  policy_window_width: "[language policy] window_width.{entry}: text budget",
  english_widest: "the English: its widest line", english_file_widest: "the English: the widest line of {file}",
  english_run_widest: "the English: the widest line of its pointer-table run",
  english_one_line: "the English: one line", english_line_count: "the English: its line count",
  english_box_lines: "the English: its box lines", english_file_lines: "the English: the most lines of {file}",
  table_field_window: "[[table]] {table} window: {where}", table_buffer: "[[table]] {table} buffer: {where}",
  table_one_line: "[[table]] {table}: a one-line field",
  table_field: "[[table]] {table}: a field of {bytes} bytes with the terminator",
  table_field_unterminated: "[[table]] {table}: a field of {bytes} bytes",
  table_field_byte: "[[table]] {table}: a field of {bytes} byte with the terminator",
  table_field_byte_unterminated: "[[table]] {table}: a field of {bytes} byte",
  table_long_name: "[[table]] {table}: a long-name pointer of {bytes} bytes with the terminator",
  keyboard_columns: "[keyboard] columns: the key cells of its keyboard",
  pack_keyboard_columns: "keyboard layout columns: the columns its pack keyboard reaches",
  in_place: "in place: its {bytes} bytes with the terminator, a reference the build cannot rewrite ({references})",
  table_base: "[[strings.table_base]] {id}: in place in its {bytes}-byte field with the terminator",
  interior: "[[strings.interior]] {pointer}: read from byte {offset}, in place in its {bytes} bytes",
  centred: "{rule}, centred against {reference}", host: "the window of {host}, which inserts it: {rule}",
  host_default: "its host", reach_study: "reach study: {class}", reach_study_root: "reach study: {class} ({root})",
  slot_profile: "profile width", slot_profile_estimate: "profile width, unverified estimate",
  slot_default: "default width", slot_context: "context records",
  slot_profile_above: "profile width, above the widest known {source} {width} px",
  slot_profile_estimate_above: "profile width, unverified estimate, above the widest known {source} {width} px",
  slot_reasons: "{source} ({reasons})", slot_no_fill: "no fill known",
  slot_unfilled: "{unfilled} of {uses} walked paths set no value",
  slot_unnamed: "a value the scripts do not name: {kinds}",
  slot_unwalked: "{references} script references not walked", slot_by_code: "shown by code", slot_word: "{id} {text}",
  slot_untranslated: "{id} (untranslated)", slot_empty: "{id} '' (translated as empty)", slot_record: "{kind} {word}",
  slot_table: "widest of table {table}: {word}", slot_native: "native table {table}: {ids}",
  slot_number_variable: "number of up to {digits} digits (a variable)", slot_number: "number {value}",
  slot_record_number: "number of {digits} digits, the record's {value}", slot_label: "{label}",
  slot_widest_source: "widest source {id}", slot_digit: "number of {digits} digit",
  slot_digits: "number of {digits} digits", slot_width: "{width} px",
  slot_width_estimate: "{width} px, unverified estimate", slot_english: "{fill}, English {source_width} px",
  slot_fill: "{fill}, {where}", slot_code_fill: "{fill}, code fill",
};
const word = { code: "slot_word", id: "table:species/499", text: "'Munchlax'" };
const LIMIT_CODES = [
  { code: "record_window", name: "journal" }, { code: "record_window_where", name: "journal", where: "the journal" },
  { code: "table_window", name: "menu" }, { code: "table_window_where", name: "menu", where: "the bag, hash:0000abcd" },
  { code: "policy_table_window", name: "menu" }, { code: "policy_table_window_where", name: "menu", where: "the bag" },
  { code: "layout_window", name: "notice" }, { code: "layout_window_where", name: "notice", where: "Notice window" },
  { code: "layout_default", key: "dialogue_width" }, { code: "layout_default", key: "label_min_width" },
  { code: "box_width", box: "battle" }, { code: "box_width_via", box: "dialogue", via: { code: "window_box", entry: "s0000100" } },
  { code: "box_lines" }, { code: "box_lines_via", via: { code: "table_window", name: "menu" } },
  { code: "window_box", entry: "s0000100" }, { code: "window_width", entry: "s0000100" },
  { code: "window_lines", entry: "s0000100" }, { code: "policy_window_width", entry: "s0000100" },
  { code: "english_widest" }, { code: "english_file_widest", file: "po/tables/items.po" }, { code: "english_run_widest" },
  { code: "english_one_line" }, { code: "english_line_count" }, { code: "english_box_lines" },
  { code: "english_file_lines", file: "po/maps/town.po" },
  { code: "table_field_window", table: "items", where: "held summary" }, { code: "table_buffer", table: "items", where: "the bag" },
  { code: "table_one_line", table: "items" }, { code: "table_field", table: "items", bytes: 14 },
  { code: "table_field_unterminated", table: "items", bytes: 12 }, { code: "table_field_byte", table: "keys", bytes: 1 },
  { code: "table_field_byte_unterminated", table: "keys", bytes: 1 }, { code: "table_long_name", table: "names", bytes: 18 },
  { code: "keyboard_columns" }, { code: "pack_keyboard_columns" },
  { code: "in_place", bytes: 12, references: "script:vmessage, table-base" }, { code: "table_base", id: "berries", bytes: 8 },
  { code: "interior", pointer: "0x1A2B", offset: 3, bytes: 12 },
  { code: "centred", rule: { code: "table_window_where", name: "menu", where: "the bag" }, reference: "s0000200" },
  { code: "host", host: "s0000300", rule: { code: "box_width", box: "dialogue" } }, { code: "host_default" },
];
const SLOT_CODES = [
  { code: "slot_profile" }, { code: "slot_profile_estimate" }, { code: "slot_default" }, { code: "slot_context" },
  { code: "slot_reasons", source: { code: "slot_profile_estimate" }, reasons: [{ code: "slot_no_fill" }, { code: "slot_by_code" }] },
  { code: "slot_reasons", source: { code: "slot_profile_above", source: { code: "slot_record", kind: "species",
    word: { code: "slot_untranslated", id: "table:species/499" } }, width: 47 },
  reasons: [{ code: "slot_unfilled", unfilled: 1, uses: 1 }, { code: "slot_unwalked", references: 3 }] },
  { code: "slot_reasons", source: { code: "slot_profile" }, reasons: [{ code: "slot_unnamed", kinds: ["nickname", "number"] }] },
  { code: "slot_reasons", source: word, reasons: [{ code: "slot_no_fill" }] },
  { code: "slot_profile_estimate_above", source: { code: "slot_number_variable", digits: 5 }, width: 30 },
  word, { code: "slot_untranslated", id: "s0000200" }, { code: "slot_empty", id: "s0000200" },
  { code: "slot_record", kind: "item", word }, { code: "slot_record", kind: "item", word: { code: "slot_empty", id: "items:3" } },
  { code: "slot_record", kind: "std_text", word },
  { code: "slot_table", table: "items", word: { code: "slot_untranslated", id: "hash:0000abcd" } },
  { code: "slot_native", table: "choices", ids: ["hash:00000001", "hash:00000002"] },
  { code: "slot_number_variable", digits: 5 }, { code: "slot_number", value: 10 }, { code: "slot_record_number", digits: 2, value: 10 },
  { code: "slot_label", label: "Battle name with prefix" },
  { code: "slot_fill", fill: { code: "slot_widest_source", id: "hash:0000abcd" }, where: "Pokemon summary, trainer memo" },
  { code: "slot_fill", fill: { code: "slot_english", fill: { code: "slot_widest_source", id: "s0000200" }, source_width: 50 },
    where: "the bag" },
  { code: "slot_code_fill", fill: { code: "slot_digit", digits: 1 } },
  { code: "slot_fill", fill: { code: "slot_digits", digits: 3 }, where: "evolution conditions, damage (never shown)" },
  { code: "slot_fill", fill: { code: "slot_english", fill: { code: "slot_digits", digits: 3 }, source_width: 18 }, where: "the bag" },
  { code: "slot_fill", fill: { code: "slot_width", width: 66 }, where: "battle text, name of the opposing trainer" },
  { code: "slot_code_fill", fill: { code: "slot_width_estimate", width: 66 } },
  { code: "slot_fill", fill: { code: "slot_english", fill: { code: "slot_width_estimate", width: 66 }, source_width: 67 },
    where: "the bag" },
  { code: "slot_fill", fill: { code: "slot_english", fill: { code: "slot_width", width: 66 }, source_width: 67 }, where: "the bag" },
  { code: "slot_fill", fill: { code: "slot_label", label: "Item name" }, where: "the bag" },
  { code: "slot_fill", fill: { code: "slot_english", fill: { code: "slot_label", label: "Trainer name" }, source_width: 60 },
    where: "battle text" },
];
// every key shown through the catalog, marked
const MARKED = new Proxy({}, { get: (_, key) => `ru:${String(key)}` });

test("an explanation's English text comes from the data's message table", (ctx) => {
  ctx.after(() => setMessages(null));
  setMessages(TEMPLATES);
  assert.equal(explanationText(LIMIT_CODES.at(-3)), "[[layout.table_window]] menu: the bag, centred against s0000200");
  assert.equal(explanationText(SLOT_CODES[5]), "profile width, above the widest known species table:species/499 "
    + "(untranslated) 47 px (1 of 1 walked paths set no value; 3 script references not walked)");
  assert.equal(explanationText(SLOT_CODES.find((c) => c.code === "slot_native")), "native table choices: hash:00000001 + hash:00000002");
  assert.equal(explanationText("review: by hand"), "review: by hand");
  assert.equal(explanationText(undefined), "");
  assert.ok(sameExplanation({ code: "table_window", name: "menu" }, { name: "menu", code: "table_window" }));
  assert.ok(sameExplanation({ code: "slot_native", ids: ["a", "b"] }, { ids: ["a", "b"], code: "slot_native" }));
  assert.ok(!sameExplanation({ code: "slot_native", ids: ["a", "b"] }, { code: "slot_native", ids: ["b", "a"] }));
  assert.ok(sameExplanation("menu", "menu") && !sameExplanation("menu", { code: "menu" }) && !sameExplanation(null, {}));
});

test("a coded limit shows and names its references as its phrase did", (ctx) => {
  ctx.after(() => { setMessages(null); setCatalog(null); setPlaces(null); setUiLanguage("en"); });
  setMessages(TEMPLATES);
  setPlaces({ "the bag": { ru: "сумка" }, "held summary": { ru: "сводка" } });
  for (const [language, catalog] of [["en", null], ["ru", MARKED]]) {
    setUiLanguage(language);
    setCatalog(catalog);
    for (const rule of LIMIT_CODES) {
      const phrase = explanationText(rule);
      assert.equal(limitText(rule), limitText(phrase), `${language}: ${phrase}`);
      assert.deepEqual(limitReferences(rule), limitReferences(phrase), phrase);
    }
  }
  assert.equal(limitText(LIMIT_CODES.at(-3)), "ru:{rule}, centred".replace("{rule}", "сумка"));
  assert.equal(limitText({ code: "table_field", table: "items", bytes: 14 }), "ru:a field of 14 bytes with the terminator");
  setCatalog(null);
  setUiLanguage("en");
  assert.deepEqual(limitReferences(LIMIT_CODES.at(-2)), ["layout.box_width dialogue", "s0000300"]);
  assert.deepEqual(limitReferences({ code: "policy_window_width", entry: "s0000100" }),
    ["language policy window_width.s0000100", "s0000100"]);
});

test("a coded window without a description takes another source's of the same window", (ctx) => {
  ctx.after(() => { setPlaces(null); setUiLanguage("en"); });
  const bare = { code: "policy_table_window", name: "menu" };
  const sources = [{ rule: bare }, { rule: { code: "table_window_where", name: "menu", where: "the bag" } }];
  assert.equal(limitText(bare, sources), "the bag");
  assert.equal(limitText({ code: "centred", rule: bare, reference: "s0000200" }, sources), "the bag, centred");
  assert.equal(limitText(bare, [{ rule: { code: "table_window_where", name: "list", where: "the bag" } }]), "Table text window");
  assert.equal(limitText({ code: "record_window", name: "journal" }, [{ rule: "[[layout.record_window]] journal: old" }]),
    "Record text window");
  const table = [{ rule: { code: "table_buffer", table: "items", where: "the bag" } },
    { rule: { code: "table_field_window", table: "items", where: "held summary" } }];
  assert.equal(limitText({ code: "table_field_window", table: "items", where: "" }, table), "held summary");
  setPlaces({ "the bag": { ru: "сумка" } });
  setUiLanguage("ru");
  assert.equal(limitText(bare, sources), "сумка");
});

// a fill-source label: the phrase has none to read
const labelled = (source) => [source, source.fill, source.fill?.fill].some((part) => part?.code === "slot_label");

test("a coded slot source shows as its phrase did, its words and identifiers kept", (ctx) => {
  ctx.after(() => { setMessages(null); setCatalog(null); setPlaces(null); setUiLanguage("en"); });
  setMessages(TEMPLATES);
  setPlaces({ "the bag": { ru: "сумка" } });
  for (const [language, catalog] of [["en", null], ["ru", MARKED]]) {
    setUiLanguage(language);
    setCatalog(catalog);
    for (const source of SLOT_CODES.filter((code) => language === "en" || !labelled(code))) {
      const phrase = explanationText(source);
      assert.equal(slotSource(source), slotSource(phrase), `${language}: ${phrase}`);
    }
  }
  setCatalog({ "{word} (translated as empty)": "{word} (пусто)", "item name": "предмет", "{width} px, {where}": "{width} px, {where}",
    "code fill": "заполнение кодом", "number of {n} digits, English {english} px, {where}": "{n} цифры, английский {english} px, {where}" });
  assert.equal(slotSource(SLOT_CODES.find((c) => c.word?.code === "slot_empty")), "предмет items:3 '' (пусто)");
  assert.equal(slotSource({ code: "slot_code_fill", fill: { code: "slot_width", width: 66 } }), "66\u00a0px, заполнение кодом");
  assert.equal(slotSource(SLOT_CODES.find((c) => c.fill?.fill?.code === "slot_digits")), "3 цифры, английский 18\u00a0px, сумка");
});

test("a fill-source label shows in the site language, its where text too, as the slot rows show it", (ctx) => {
  ctx.after(() => { setCatalog(null); setPlaces(null); setUiLanguage("en"); });
  const label = { code: "slot_label", label: "Battle name with prefix" };
  const sources = [label, { code: "slot_reasons", source: label, reasons: [{ code: "slot_by_code" }] },
    { code: "slot_fill", fill: { code: "slot_label", label: "Nickname of up to ten letters" },
      where: "Mega Evolution and wish messages, unprefixed nickname" },
    { code: "slot_fill", fill: { code: "slot_english", fill: { code: "slot_label", label: "Trainer name" }, source_width: 67 },
      where: "battle text, name of the opposing trainer" },
    { code: "slot_code_fill", fill: { code: "slot_label", label: "Item name" } }];
  const rowLabel = () => slotRows({ slot_details: { "{B_BUFF1}": { label: label.label, width: 60, state: "computed" } } })[0].label;
  assert.deepEqual(sources.map(slotSource), ["Battle name with prefix", "Battle name with prefix (shown by code)",
    "Nickname of up to ten letters, Mega Evolution and wish messages, unprefixed nickname",
    "Trainer name, English 67\u00a0px, battle text, name of the opposing trainer", "Item name, code fill"]);
  setPlaces({ "Mega Evolution and wish messages, unprefixed nickname": { ru: "сообщения мегаэволюции и желания, кличка без префикса" },
    "battle text, name of the opposing trainer": { ru: "боевой текст, имя тренера противника" } });
  setUiLanguage("ru");
  setCatalog({ "Battle name with prefix": "Имя с приставкой в бою", "Nickname of up to ten letters": "Кличка длиной до десяти букв",
    "Trainer name": "Имя тренера", "Item name": "Название предмета", "shown by code": "выводится кодом",
    "code fill": "заполнение кодом", "{label}, {where}": "{label}, {where}",
    "{label}, English {english} px, {where}": "{label}, в английском {english} px, {where}" });
  assert.deepEqual(sources.map(slotSource), ["Имя с приставкой в бою", "Имя с приставкой в бою (выводится кодом)",
    "Кличка длиной до десяти букв, сообщения мегаэволюции и желания, кличка без префикса",
    "Имя тренера, в английском 67\u00a0px, боевой текст, имя тренера противника", "Название предмета, заполнение кодом"]);
  assert.equal(slotSource(label), rowLabel());
});
