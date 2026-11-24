// The check's messages: codes, notes, values and site language.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { ENCODING, MESSAGES, NOTES, RULE_SOURCES, SLOT_ITEMS, WORDINGS, checkFailed, checkText, explanationText,
  extractionFailed, findingText, glossaryMissing, keepUnits, layoutFailed, limitReferences, limitText, missingTerms,
  parseFinding, sameExplanation, setMessages, setPlaces, setReasons, slotSource, unnoted, wordingText } from "../js/checktext.js";
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
  "check failed: {error}": "проверка не удалась: {error}",
  "Preserve the source placeholders and control codes.": "Сохраните подстановки и управляющие коды оригинала.",
};
// the site catalog's one-line entries
const SITE_RU = Object.fromEntries([...readFileSync(new URL("../i18n/ru.po", import.meta.url), "utf8")
  .matchAll(/^msgid (".*")\nmsgstr (".*")$/gm)].map(([, key, text]) => [JSON.parse(key), JSON.parse(text)]));
// every entry, its continued lines joined
const joined = (lines) => lines.trim().split("\n").map((line) => JSON.parse(line)).join("");
const SITE_ALL = Object.fromEntries([...readFileSync(new URL("../i18n/ru.po", import.meta.url), "utf8")
  .matchAll(/^msgid ((?:".*"\n)+)msgstr ((?:".*"(?:\n|$))+)/gm)].map(([, key, text]) => [joined(key), joined(text)]));

// the data's problem templates (gen3tl.check.provenance.MESSAGES)
const PROBLEMS = {
  not_encodable: "not encodable: {error}", too_many_lines: "too many lines: {n} > {limit}",
  line_wide: "line too wide: {width} > {limit} px: {text}",
  line_wide_window: "line too wide: {width} > {limit} px: {text}; window {where}",
  line_wide_slots: "line too wide: {width} > {limit} px: {text} with {slots}",
  other_window_wide: "line too wide: {width} > {limit} px in {where} ({font})",
  arrow_room_window: "line leaves no room for the prompt arrow: box {box}: {width} > {limit} px in {where}: {text}",
  instant_print: "scroll and page codes are not supported in {where}: "
    + "this window prints instantly and drops the remaining text",
  typography: "typography: {reason}", placeholders_differ: "placeholders differ",
  glossary_missing: "glossary term {term} -> {translation} not found",
  warning: "{message} (warning)", unmeasured_width: "{message} [unmeasured limit: the English width]",
  prefix_stripped: "{message} (prefix {prefix} stripped)",
  slot_item: "{token} = {width} px ({source})", slot_unavailable: "{source}; recorded width; sources unavailable",
  slot_parts: "{source}; {parts}", slot_part: "«{text}» {width} px", slot_no_prefix: "no prefix 0 px",
  unmapped_character: "character {char} (U+{unicode}) has no byte, in {text}",
  substitution_unavailable: "substitution measurements unavailable: {error}",
};
// the explanation templates with them
const table = () => ({ ...TEMPLATES, ...PROBLEMS });
const noted = (message, code, args = {}) => ({ code, message, ...args });
const item = (token, width, source) => ({ code: "slot_item", token, width, source });
const wide = { code: "line_wide_slots", width: 210, limit: 196, text: "'Привет, {PLAYER}! Как дела?'",
  slots: [item("{PLAYER}", 42, { code: "slot_profile" })] };

test("a problem is its code with its arguments, then its notes in order", (ctx) => {
  ctx.after(() => setMessages(null));
  setMessages(table());
  const message = noted(noted(wide, "prefix_stripped", { prefix: "'A: '" }), "unmeasured_width");
  assert.equal(checkText(message), "line too wide: 210 > 196 px: 'Привет, {PLAYER}! Как дела?' with {PLAYER} = 42 px "
    + "(profile width) (prefix 'A: ' stripped) [unmeasured limit: the English width]");
  assert.equal(explanationText(message), "line too wide: 210 > 196 px: 'Привет, {PLAYER}! Как дела?' with {PLAYER} = 42 px "
    + "(profile width) (prefix 'A: ' stripped) [unmeasured limit: the English width]");
  assert.equal(unnoted(message), wide);
  assert.equal(unnoted("placeholders differ"), "placeholders differ");
  const term = glossaryMissing("'Poké Ball'", "\"Покебол's\"");
  assert.deepEqual(missingTerms([{ message: term }, { message: wide }, { message: "Preserve the source placeholders and control codes." }]),
    [{ code: "glossary_missing", term: "'Poké Ball'", translation: "\"Покебол's\"" }]);
  assert.equal(checkText(term), "glossary term 'Poké Ball' -> \"Покебол's\" not found (warning)");
});

test("the character map's error inside a message is a code of its own", (ctx) => {
  ctx.after(() => { setMessages(null); setCatalog(null); });
  setMessages(table());
  const error = { code: "unmapped_character", char: "'ж'", unicode: "0436", text: "'жук'" };
  assert.equal(checkText({ code: "not_encodable", error }), "not encodable: character 'ж' (U+0436) has no byte, in 'жук'");
  setCatalog(RU);
  assert.equal(checkText({ code: "not_encodable", error }), "не кодируется: у символа 'ж' (U+0436) нет байта, в 'жук'");
  assert.equal(checkText({ code: "not_encodable", error: "no terminator after 0x10" }), "не кодируется: no terminator after 0x10");
});

test("the text follows the site catalog and keeps the values", (ctx) => {
  ctx.after(() => { setMessages(null); setCatalog(null); });
  setMessages(table());
  const slots = { ...wide, text: "'Как дела?'", slots: [...wide.slots, item("{STR_VAR_1}", 60, "widest of table items")] };
  setCatalog(RU);
  assert.equal(checkText(noted(slots, "warning")), "строка слишком широкая: 210 > 196 px: 'Как дела?' при {PLAYER} = 42 px "
    + "(ширина из профиля); {STR_VAR_1} = 60 px (widest of table items) (предупреждение)");
  assert.equal(checkText(glossaryMissing("'Potion'", "'Зелье'")), "термин глоссария 'Potion' -> 'Зелье' не найден (предупреждение)");
  assert.equal(checkText(checkFailed("no bundle")), "проверка не удалась: no bundle");
  assert.equal(checkText("Preserve the source placeholders and control codes."), "Сохраните подстановки и управляющие коды оригинала.");
  assert.equal(checkText("A message the check never gives"), "A message the check never gives");
  assert.equal(checkText("too many lines: 4 > 3"), "too many lines: 4 > 3", "a text is not read for its code");
  assert.equal(checkText({ code: "too_many_lines", n: 4, limit: 3 }), "too many lines: 4 > 3", "a code without a translation stays English");
  setCatalog(null);
  assert.equal(checkText(noted({ code: "line_wide", width: 210, limit: 196, text: "'x'" }, "warning")),
    "line too wide: 210 > 196 px: 'x' (warning)");
  setMessages(null);
  assert.equal(checkText(checkFailed("no bundle")), "check failed: no bundle", "the page's own problems need no table");
  assert.equal(checkText(glossaryMissing("'Potion'", "'Зелье'")), "glossary term 'Potion' -> 'Зелье' not found (warning)");
});

test("an inserted word's source in a message shows as in the slot rows, its extras after it", (ctx) => {
  ctx.after(() => { setMessages(null); setCatalog(null); setPlaces(null); setUiLanguage("en"); });
  setMessages(table());
  const label = { code: "slot_fill", fill: { code: "slot_label", label: "Trainer name" }, where: "battle text, name of the opposing trainer" };
  const message = { ...wide, slots: [item("{B_TRAINER1_NAME}", 60, label),
    item("{STR_VAR_1}", 42, { code: "slot_parts", source: { code: "slot_unavailable", source: { code: "slot_profile" } },
      parts: [{ code: "slot_part", text: "Зелье", width: 30 }, { code: "slot_no_prefix" }] })] };
  assert.equal(checkText(message), "line too wide: 210 > 196 px: 'Привет, {PLAYER}! Как дела?' with "
    + "{B_TRAINER1_NAME} = 60 px (Trainer name, battle text, name of the opposing trainer); "
    + "{STR_VAR_1} = 42 px (profile width; recorded width; sources unavailable; «Зелье» 30 px, no prefix 0 px)");
  setPlaces({ "battle text, name of the opposing trainer": { ru: "боевой текст, имя тренера противника" },
    "Trainer name": { ru: "Имя тренера" } });
  setUiLanguage("ru");
  setCatalog({ ...RU, "{label}, {where}": "{label}, {where}" });
  const rows = slotRows({ slot_details: { "{B_TRAINER1_NAME}": { label: "Trainer name", width: 60, state: "computed" } } });
  assert.equal(rows[0].label, "Имя тренера");
  assert.equal(checkText({ ...message, slots: message.slots.slice(0, 1) }), "строка слишком широкая: 210 > 196 px: "
    + "'Привет, {PLAYER}! Как дела?' при {B_TRAINER1_NAME} = 60 px (Имя тренера, боевой текст, имя тренера противника)");
  setCatalog(SITE_RU);
  assert.equal(checkText({ ...message, slots: message.slots.slice(1) }), "строка слишком широкая: 210 > 196\u00a0px: "
    + "'Привет, {PLAYER}! Как дела?' при {STR_VAR_1} = 42\u00a0px "
    + "(ширина из профиля; записанная ширина; источники недоступны; «Зелье» 30\u00a0px, без приставки 0\u00a0px)");
  assert.equal(checkText({ code: "substitution_unavailable", error: "fill_source: unmeasurable draft: s0000100" }),
    "измерения подстановок недоступны: fill_source: unmeasurable draft: s0000100", "its error stays as written");
});

test("every key's holes are named and every kind is distinct", () => {
  const keys = [...MESSAGES, ...ENCODING, ...Object.values(NOTES), ...SLOT_ITEMS];
  assert.equal(new Set(keys).size, keys.length);
  for (const key of keys) assert.match(key, /^(?:[^{}]|\{\w+\})*$/, key);
  for (const [code, template] of Object.entries(PROBLEMS)) {
    if (Object.hasOwn(NOTES, code)) continue;
    const shown = code.startsWith("slot_") ? SLOT_ITEMS : code === "unmapped_character" ? ENCODING : MESSAGES;
    assert.ok(shown.includes(template), code);
  }
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
  ctx.after(() => { setReasons(null); setCatalog(null); setUiLanguage("en"); setMessages(null); });
  setMessages(table());
  const message = { code: "typography", reason: "three dots: use the ellipsis character …" };
  setReasons({ "three dots: use the ellipsis character …": { ru: "три точки: используйте символ многоточия …" } });
  setUiLanguage("ru");
  setCatalog({ "typography: {reason}": "типографика: {reason}" });
  assert.equal(checkText(message), "типографика: три точки: используйте символ многоточия …");
  assert.equal(checkText({ code: "typography", reason: "double space" }), "типографика: double space", "a reason without a text stays");
  setUiLanguage("en");
  setCatalog(null);
  assert.equal(checkText(message), "typography: three dots: use the ellipsis character …");
});

test("a slot source in the site language: its codes, the reasons it is partly unknown, its words kept", (ctx) => {
  ctx.after(() => { setCatalog(null); setMessages(null); });
  setMessages(table());
  const source = { code: "slot_reasons", source: { code: "slot_profile_above", source: { code: "slot_record", kind: "species",
    word: { code: "slot_word", id: "table:species/499", text: "'Munchlax'" } }, width: 47 },
  reasons: [{ code: "slot_unfilled", unfilled: 1, uses: 1 }, { code: "slot_unwalked", references: 3 }] };
  setCatalog({
    "profile width, above the widest known {source} {width} px": "ширина из профиля, больше самого широкого известного: {source} {width} px",
    "{n} of {uses} walked paths set no value": "путей без значения: {n} из {uses} пройденных",
    "{n} script references not walked": "непройденных ссылок скриптов: {n}",
    "{width} px, {where}": "{width} px, {where}", "code fill": "заполнение кодом",
    "line too wide: {width} > {limit} px: {text} with {slots}": "строка слишком широкая: {width} > {limit} px: {text} при {slots}",
  });
  const codeFill = { code: "slot_code_fill", fill: { code: "slot_width", width: 66 } };
  assert.equal(slotSource(source), "ширина из профиля, больше самого широкого известного: species name table:species/499 "
    + "'Munchlax' 47 px (путей без значения: 1 из 1 пройденных; непройденных ссылок скриптов: 3)");
  assert.equal(slotSource(codeFill), "66 px, заполнение кодом");
  assert.equal(slotSource("66 px, code fill"), "66 px, code fill", "a text shows as written");
  assert.equal(checkText({ code: "line_wide_slots", width: 210, limit: 196, text: "'x'", slots: [item("{STR_VAR_1}", 60, codeFill)] }),
    "строка слишком широкая: 210 > 196 px: 'x' при {STR_VAR_1} = 60 px (66 px, заполнение кодом)");
  setCatalog(null);
  assert.equal(slotSource(source), "profile width, above the widest known species name table:species/499 'Munchlax' 47 px "
    + "(1 of 1 walked paths set no value; 3 script references not walked)"); // the fill kind shows its label
});

test("slot measurements use the translated unit without a break", (context) => {
  context.after(() => { setCatalog(null); setMessages(null); });
  setMessages(table());
  setCatalog({ px: "px", "code fill": "заполнение кодом", "{width} px, {where}": "{width} px, {where}" });
  const codeFill = { code: "slot_code_fill", fill: { code: "slot_width", width: 59 } };
  assert.equal(slotSource(codeFill), "59 px, заполнение кодом");
  assert.equal(checkText({ code: "line_wide", width: 119, limit: 110, text: "'x'" }), "line too wide: 119 > 110 px: 'x'");
  assert.ok(!slotSource(codeFill).includes("59 px"));
});

test("only a whole unit word joins its number, a word that begins with one stays", (context) => {
  context.after(() => setCatalog(null));
  const text = "3 keys, keystone item; 8 px, pxfont glyph; 2 px_wide; 1 keys";
  assert.equal(keepUnits(text), "3 keys, keystone item; 8 px, pxfont glyph; 2 px_wide; 1 keys");
  setCatalog({ keys: "клавиши", px: "пкс" });
  assert.equal(keepUnits(text), "3 клавиши, keystone item; 8 пкс, pxfont glyph; 2 px_wide; 1 клавиши");
  assert.equal(keepUnits("4 клавиши (клавишиtone) 5 пкс"), "4 клавиши (клавишиtone) 5 пкс",
    "the translated unit too");
});

test("a limit description in the site language, nested descriptions too", (ctx) => {
  ctx.after(() => setCatalog(null));
  const rule = { code: "centred", rule: { code: "host", host: "hash:00c6180b", rule: { code: "box_width", box: "dialogue" } },
    reference: "hash:47007de3" };
  setCatalog({ "Dialogue message window": "Окно диалога", "{rule}, centred": "{rule}, по центру" });
  assert.equal(limitText(rule), "Окно диалога, по центру");
  assert.equal(limitText({ code: "record_window", name: "journal" }), "Record text window");
  setCatalog(null);
  assert.equal(limitText(rule), "Dialogue message window, centred");
  assert.equal(limitText("[[layout.record_window]] journal"), "[[layout.record_window]] journal", "a text is not read for its rule");
});

test("profile rule sources use descriptions in words and retain identities as references", (context) => {
  context.after(() => { setPlaces(null); setCatalog(null); setUiLanguage("en"); });
  setPlaces({ "battle text, name of the opposing trainer": { ru: "боевой текст, имя тренера противника" } });
  const rule = { code: "rule_code_fill_where", name: "fill-20", where: "battle text, name of the opposing trainer" };
  assert.equal(limitText(rule), "battle text, name of the opposing trainer");
  assert.deepEqual(limitReferences(rule), ["layout.code_fill fill-20"]);
  setCatalog({ "battle text, name of the opposing trainer": "unused" });
  setUiLanguage("ru");
  assert.equal(limitText(rule), "боевой текст, имя тренера противника");
  setUiLanguage("en");
  for (const [source, label, reference] of [
    [{ code: "rule_droppable" }, "Droppable placeholders", "qa.droppable"],
    [{ code: "rule_pointer_table", table: "credits" }, "Pointer table", "strings.pointer_table credits"],
    [{ code: "rule_table_save_link", table: "species" }, "Name field", "table species"],
    [{ code: "rule_table", table: "item_plural_s" }, "Name field", "table item_plural_s"],
    [{ code: "rule_strings", key: "save_link" }, "Save or link text", "strings save_link"],
    [{ code: "rule_strings", key: "save_link_letters" }, "Save or link text", "strings save_link_letters"],
    [{ code: "rule_computed", pointer: "0x8123" }, "Computed string group", "strings.computed 0x8123"],
    [{ code: "rule_interior", pointer: "0x100" }, "Interior string", "strings.interior 0x100"],
    [{ code: "rule_code_fill", name: "s0123ABC" }, "Code fill", "layout.code_fill s0123ABC"],
  ]) {
    assert.equal(limitText(source), label);
    assert.deepEqual(limitReferences(source), [reference]);
  }
  // other rule sources are texts: a key or as written, their identifiers kept
  setCatalog({ "catalog context": "контекст каталога" });
  assert.equal(limitText("catalog context"), "контекст каталога");
  setCatalog(MARKED);
  assert.deepEqual(RULE_SOURCES.map((source) => limitText(source)), ["ru:catalog context", "ru:reach study, review"]);
  assert.deepEqual(limitReferences("catalog context"), []);
  assert.deepEqual(limitReferences("review of s0000100 and s0000100"), ["s0000100"]);
});

test("a rule's wording names its profile keys in words", (ctx) => {
  ctx.after(() => setCatalog(null));
  assert.equal(wordingText("Species keep the order of [pokedex.dex.map]."), "Species keep the order of Pokedex species map.");
  assert.equal(wordingText("[[qa.droppable]] and [[unknown.key]]"), "Droppable placeholders and [[unknown.key]]");
  setCatalog({ "Pokedex species map": "карта видов Покедекса" });
  assert.equal(wordingText("See [pokedex.dex.map]"), "See карта видов Покедекса");
});

// the data's wording templates (gen3tl.check.provenance.MESSAGES)
const WORDING_TEMPLATES = {
  wording_save_link: "Copied into save or link data: translated like all other text; in trades and link play with the "
    + "original ROM, unsupported glyphs may display incorrectly.",
  wording_computed: "Moves with {others} as one block: code reaches the group by pointer arithmetic (pool word "
    + "{pointer}). At most {bytes} bytes, the gap to {next} less the terminator; a longer translation stays English.",
  wording_computed_last: "Moves with {others} as one block: code reaches the group by pointer arithmetic (pool word "
    + "{pointer}). The last member: no byte gap binds it.",
  wording_interior: "Begins with {head}, the first {offset} bytes of the English; code reads the text from byte "
    + "{offset} (pool word {pointer}); kept in place: at most {bytes} bytes.",
  wording_code_char: "Character code written by game code: at most {n} characters; only that byte changes; empty "
    + "writes {pad}.",
  wording_code_char_one: "Character code written by game code: at most {n} character; only that byte changes; empty "
    + "writes {pad}.",
  wording_code_char_end: "Character code written by game code: at most {n} characters; only that byte changes; empty "
    + "writes 0xFF, the terminator.",
  wording_code_char_end_one: "Character code written by game code: at most {n} character; only that byte changes; "
    + "empty writes 0xFF, the terminator.",
  wording_code_fill: "The code fills {slot} with {fill}.",
  wording_code_fill_where: "The code fills {slot} with {fill} ({where}).",
  fill_measured: "text measured from {measured}",
  fill_strings: "one of {n} source strings, measured at their widest rendering",
  fill_strings_one: "one of {n} source string, measured at their widest rendering",
  fill_digits: "a number of {n} digits", fill_digits_one: "a number of {n} digit", fill_width: "text {width} px wide",
  wording_droppable: "May leave out {tokens}: the code fills it with text only the English needs.",
  wording_never_shown: "The game never shows this text: it is not translated, and an approved translation stays.",
  wording_hosts: "Inserted as {slot} into {n} texts ({entries}): one form serves every sentence it fills.",
  wording_hosts_one: "Inserted as {slot} into {n} text ({entries}): one form serves every sentence it fills.",
  wording_hosts_more: "Inserted as {slot} into {n} texts ({entries} and {more} more): one form serves every sentence "
    + "it fills.",
  wording_words: "{slot} takes {n} inserted words ({entries}): the sentence is built around their fixed forms.",
  wording_words_one: "{slot} takes {n} inserted word ({entries}): the sentence is built around their fixed forms.",
  wording_words_more: "{slot} takes {n} inserted words ({entries} and {more} more): the sentence is built around "
    + "their fixed forms.",
};
const FILL_WHERE = "battle text, name of the opposing trainer";
// a wording of each kind: English, Russian
const WORDING_TEXTS = [
  [{ code: "wording_save_link" }, WORDING_TEMPLATES.wording_save_link,
    "Копируется в данные сохранения или связи: переводится, как и весь остальной текст; при обменах и игре по связи "
    + "с оригинальным ROM неподдерживаемые глифы могут отображаться неправильно."],
  [{ code: "wording_computed", others: "s0000340", pointer: "0x9BDE20", bytes: 63, next: "s0000340" },
    "Moves with s0000340 as one block: code reaches the group by pointer arithmetic (pool word 0x9BDE20). At most 63 "
    + "bytes, the gap to s0000340 less the terminator; a longer translation stays English.",
    "Перемещается вместе с s0000340 одним блоком: код находит группу арифметикой указателей (слово литерального пула "
    + "0x9BDE20). Не больше 63 байт — промежуток до s0000340 без терминатора; при более длинном переводе остаётся "
    + "английский текст."],
  [{ code: "wording_code_char_end_one", n: 1 }, "Character code written by game code: at most 1 character; only that "
    + "byte changes; empty writes 0xFF, the terminator.", "Код символа записывает код игры: символов не больше 1; "
    + "меняется только этот байт; пустое значение записывает 0xFF — терминатор."],
  [{ code: "wording_code_fill_where", slot: "{B_TRAINER1_NAME}", fill: { code: "fill_measured", measured: "Trainer name" },
    where: FILL_WHERE }, `The code fills {B_TRAINER1_NAME} with text measured from trainer name (${FILL_WHERE}).`,
  "Код заполняет {B_TRAINER1_NAME}: текст, измеренный как имя тренера (боевой текст, имя тренера противника)."],
  [{ code: "wording_code_fill", slot: "{STR_VAR_2}", fill: { code: "fill_digits", n: 3 } },
    "The code fills {STR_VAR_2} with a number of 3 digits.", "Код заполняет {STR_VAR_2}: число из 3 цифр."],
  [{ code: "wording_never_shown" }, WORDING_TEMPLATES.wording_never_shown,
    "Игра никогда не показывает этот текст: он не переводится, а одобренный перевод сохраняется."],
  [{ code: "wording_hosts_more", slot: "{STR_VAR_1}", n: 6, entries: ["s0001000", "s0002000", "s0003000", "s0004000"],
    more: 2 }, "Inserted as {STR_VAR_1} into 6 texts (s0001000, s0002000, s0003000, s0004000 and 2 more): one form "
    + "serves every sentence it fills.", "Вставляется как {STR_VAR_1} в тексты, всего 6 (s0001000, s0002000, "
    + "s0003000, s0004000 и ещё 2): одна форма служит каждому предложению, которое она заполняет."],
  [{ code: "wording_words_one", slot: "{STR_VAR_2}", n: 1, entries: ["s0000200"] },
    "{STR_VAR_2} takes 1 inserted word (s0000200): the sentence is built around their fixed forms.",
    "В {STR_VAR_2} вставляется 1 слово (s0000200): предложение строится вокруг его неизменной формы."],
];

test("a toolkit rule's wording is its code: English from the message table, the site language from the catalog", (ctx) => {
  ctx.after(() => { setMessages(null); setCatalog(null); setPlaces(null); setUiLanguage("en"); });
  assert.deepEqual([...WORDINGS].sort(), Object.values(WORDING_TEMPLATES).sort());
  assert.deepEqual(WORDINGS.filter((key) => !SITE_ALL[key]), [], "a wording without a Russian entry");
  for (const key of WORDINGS) assert.match(key, /^(?:[^{}]|\{\w+\})*$/, key);
  setMessages(WORDING_TEMPLATES);
  setPlaces({ [FILL_WHERE]: { ru: "боевой текст, имя тренера противника" }, "Trainer name": { ru: "Имя тренера" } });
  for (const [wording, english] of WORDING_TEXTS) {
    assert.equal(wordingText(wording), english);
    assert.equal(explanationText(wording), english);
  }
  setUiLanguage("ru");
  setCatalog(SITE_ALL);
  for (const [wording, , russian] of WORDING_TEXTS) assert.equal(wordingText(wording), russian);
  // a text keeps its profile keys in words
  assert.equal(wordingText("Keep [[qa.droppable]]."), "Keep Пропускаемые подстановки.");
});

test("window references survive nested bases separately from their descriptions", () => {
  const rule = { code: "centred", rule: { code: "host", host: "sym:host",
    rule: { code: "table_window_where", name: "sample", where: "menu: text" } }, reference: "sym:centre" };
  assert.equal(limitText(rule), "menu: text, centred");
  assert.deepEqual(limitReferences(rule), ["layout.table_window sample", "sym:host", "sym:centre"]);
  const via = { code: "box_lines_via", via: { code: "table_window", name: "sample" } };
  assert.equal(limitText(via), "two lines per message box");
  assert.deepEqual(limitReferences(via), ["layout.table_window sample"]);
});

test("consumer diagnostics retain their binding window", (context) => {
  context.after(() => { setCatalog(null); setMessages(null); });
  setMessages(table());
  const where = { code: "table_window_where", name: "trade", where: "trade window" };
  const arrow = { code: "arrow_room_window", box: 1, width: 170, limit: 166, where, text: "'Draft'" };
  const ordinary = { code: "line_wide_window", width: 180, limit: 176, text: "'Draft'", where };
  setCatalog({
    "line leaves no room for the prompt arrow: box {box}: {width} > {limit} px in {where}: {text}":
      "Arrow: {width}/{limit}, {where}, {text}",
    "line too wide: {width} > {limit} px: {text}; window {where}": "Width: {width}/{limit}, {where}, {text}",
  });
  assert.equal(checkText(arrow), "Arrow: 170/166, trade window, 'Draft'");
  assert.equal(checkText(ordinary), "Width: 180/176, trade window, 'Draft'");
  assert.equal(checkText({ ...arrow, limit: -1 }), "Arrow: 170/-1, trade window, 'Draft'");
  setCatalog(null);
  assert.equal(checkText({ code: "instant_print", where: { code: "record_window", name: "banner" } }),
    "scroll and page codes are not supported in Record text window: this window prints instantly and drops the remaining text");
  assert.equal(checkText({ code: "other_window_wide", width: 99, limit: 98, where: "the bag", font: "unbound-small" }),
    "line too wide: 99 > 98 px in the bag (unbound-small)");
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
// each limit code's text in English and marked, and its references, as their phrases showed
const LIMIT_TEXTS = [
  ["Record text window", "ru:Record text window", ["layout.record_window journal"]],
  ["the journal", "the journal", ["layout.record_window journal"]],
  ["Table text window", "ru:Table text window", ["layout.table_window menu"]],
  ["the bag, hash:0000abcd", "the bag, hash:0000abcd", ["layout.table_window menu", "hash:0000abcd"]],
  ["Table text window", "ru:Table text window", ["language policy table_window.menu"]],
  ["the bag", "сумка", ["language policy table_window.menu"]],
  ["Text window", "ru:Text window", ["layout.window notice"]],
  ["Notice window", "Notice window", ["layout.window notice"]],
  ["Dialogue message window", "ru:Dialogue message window", ["layout dialogue_width"]],
  ["Minimum label width", "ru:Minimum label width", ["layout label_min_width"]],
  ["Battle message window", "ru:Battle message window", ["layout.box_width battle"]],
  ["Dialogue message window", "ru:Dialogue message window", ["layout.box_width dialogue", "layout.window_box s0000100"]],
  ["two lines per message box", "ru:two lines per message box", []],
  ["two lines per message box", "ru:two lines per message box", ["layout.table_window menu"]],
  ["Message window", "ru:Message window", ["layout.window_box s0000100"]],
  ["Text window width", "ru:Text window width", ["layout.window_width s0000100"]],
  ["Visible lines in the text window", "ru:Visible lines in the text window", ["layout.window_lines s0000100"]],
  ["Translation text budget", "ru:Translation text budget", ["language policy window_width.s0000100", "s0000100"]],
  ["the English: its widest line", "ru:the English: its widest line", []],
  ["the English: the widest line of po/tables/items.po", "ru:the English: the widest line of po/tables/items.po", []],
  ["the English: the widest line of its pointer-table run", "ru:the English: the widest line of its pointer-table run", []],
  ["the English: one line", "ru:the English: one line", []],
  ["the English: its line count", "ru:the English: its line count", []],
  ["the English: its box lines", "ru:the English: its box lines", []],
  ["the English: the most lines of po/maps/town.po", "ru:the English: the most lines of po/maps/town.po", []],
  ["held summary", "сводка", ["table items"]],
  ["the bag", "сумка", ["table items"]],
  ["a one-line field", "ru:a one-line field", ["table items"]],
  ["a field of 14 bytes with the terminator", "ru:a field of 14 bytes with the terminator", ["table items"]],
  ["a field of 12 bytes", "ru:a field of 12 bytes", ["table items"]],
  ["a field of 1 byte with the terminator", "ru:a field of 1 byte with the terminator", ["table keys"]],
  ["a field of 1 byte", "ru:a field of 1 byte", ["table keys"]],
  ["a long-name pointer of 18 bytes with the terminator", "ru:a long-name pointer of 18 bytes with the terminator", ["table names"]],
  ["Keyboard key columns", "ru:Keyboard key columns", ["keyboard columns"]],
  ["keyboard layout columns: the columns its pack keyboard reaches", "ru:keyboard layout columns: the columns its pack keyboard reaches", []],
  ["in place: its 12 bytes with the terminator, a reference the build cannot rewrite (script:vmessage, table-base)", "ru:in place: its 12 bytes with the terminator, a reference the build cannot rewrite (script:vmessage, table-base)", []],
  ["in place in its 8-byte field with the terminator", "ru:in place in its 8-byte field with the terminator", ["strings.table_base berries"]],
  ["read from byte 3, in place in its 12 bytes", "ru:read from byte 3, in place in its 12 bytes", ["strings.interior 0x1A2B"]],
  ["the bag, centred", "ru:сумка, centred", ["layout.table_window menu", "s0000200"]],
  ["Dialogue message window", "ru:Dialogue message window", ["layout.box_width dialogue", "s0000300"]],
  ["its host", "ru:its host", []],
];
// each slot code's text in English and marked, as their phrases showed; a label had no phrase to read
const SLOT_TEXTS = [
  ["profile width", "ru:profile width"],
  ["profile width, unverified estimate", "ru:profile width, unverified estimate"],
  ["default width", "ru:default width"],
  ["context records", "ru:context records"],
  ["profile width, unverified estimate (no fill known; shown by code)", "ru:profile width, unverified estimate (ru:no fill known; ru:shown by code)"],
  ["profile width, above the widest known species name table:species/499 (untranslated) 47\u00a0px (1 of 1 walked paths set no value; 3 script references not walked)", "ru:profile width, above the widest known ru:ru:species name table:species/499 (untranslated) 47\u00a0ru:px (ru:1 of 1 walked paths set no value; ru:3 script references not walked)"],
  ["profile width (a value the scripts do not name: nickname, number)", "ru:profile width (ru:a value the scripts do not name: ru:nickname, ru:number)"],
  ["table:species/499 'Munchlax' (no fill known)", "table:species/499 'Munchlax' (ru:no fill known)"],
  ["profile width, unverified estimate, above the widest known number of up to 5 digits (a variable) 30\u00a0px", "ru:profile width, unverified estimate, above the widest known ru:number of up to 5 digits (a variable) 30\u00a0ru:px"],
  ["table:species/499 'Munchlax'", "table:species/499 'Munchlax'"],
  ["s0000200 (untranslated)", "ru:s0000200 (untranslated)"],
  ["s0000200 '' (translated as empty)", "ru:s0000200 '' (translated as empty)"],
  ["item name table:species/499 'Munchlax'", "ru:item name table:species/499 'Munchlax'"],
  ["item name items:3 '' (translated as empty)", "ru:ru:item name items:3 '' (translated as empty)"],
  ["std_text table:species/499 'Munchlax'", "std_text table:species/499 'Munchlax'"],
  ["widest of table items: hash:0000abcd (untranslated)", "ru:widest of table items: ru:hash:0000abcd (untranslated)"],
  ["native table choices: hash:00000001 + hash:00000002", "ru:native table choices: hash:00000001 + hash:00000002"],
  ["number of up to 5 digits (a variable)", "ru:number of up to 5 digits (a variable)"],
  ["number 10", "ru:number 10"],
  ["number of 2 digits, the record's 10", "ru:number of 2 digits, the record's 10"],
  ["Battle name with prefix", null],
  ["widest source hash:0000abcd, Pokemon summary, trainer memo", "ru:widest source hash:0000abcd, ru:Pokemon summary, trainer memo"],
  ["widest source s0000200, English 50\u00a0px, the bag", "ru:widest source s0000200, English 50\u00a0ru:px, ru:the bag"],
  ["number of 1 digit, code fill", "ru:number of 1 digit, ru:code fill"],
  ["number of 3 digits, evolution conditions, damage (never shown)", "ru:number of 3 digits, ru:evolution conditions, damage (never shown)"],
  ["number of 3 digits, English 18\u00a0px, the bag", "ru:number of 3 digits, English 18\u00a0ru:px, ru:the bag"],
  ["66\u00a0px, battle text, name of the opposing trainer", "ru:66\u00a0ru:px, ru:battle text, name of the opposing trainer"],
  ["66\u00a0px, unverified estimate, code fill", "ru:66\u00a0ru:px, unverified estimate, ru:code fill"],
  ["66\u00a0px, unverified estimate, English 67\u00a0px, the bag", "ru:66\u00a0ru:px, unverified estimate, English 67\u00a0ru:px, ru:the bag"],
  ["66\u00a0px, English 67\u00a0px, the bag", "ru:66\u00a0ru:px, English 67\u00a0ru:px, ru:the bag"],
  ["Item name, the bag", null],
  ["Trainer name, English 60\u00a0px, battle text", null],
];

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
  assert.equal(LIMIT_TEXTS.length, LIMIT_CODES.length);
  for (const [n, [language, catalog]] of [["en", null], ["ru", MARKED]].entries()) {
    setUiLanguage(language);
    setCatalog(catalog);
    LIMIT_CODES.forEach((rule, i) => {
      assert.equal(limitText(rule), LIMIT_TEXTS[i][n], `${language}: ${explanationText(rule)}`);
      assert.deepEqual(limitReferences(rule), LIMIT_TEXTS[i][2], explanationText(rule));
    });
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
  assert.equal(SLOT_TEXTS.length, SLOT_CODES.length);
  for (const [n, [language, catalog]] of [["en", null], ["ru", MARKED]].entries()) {
    setUiLanguage(language);
    setCatalog(catalog);
    SLOT_CODES.forEach((source, i) => {
      if (language === "en" || !labelled(source)) assert.equal(slotSource(source), SLOT_TEXTS[i][n], `${language}: ${explanationText(source)}`);
    });
  }
  setCatalog({ "{word} (translated as empty)": "{word} (пусто)", "item name": "предмет", "{width} px, {where}": "{width} px, {where}",
    "code fill": "заполнение кодом", "number of {n} digits, English {english} px, {where}": "{n} цифры, английский {english} px, {where}" });
  assert.equal(slotSource(SLOT_CODES.find((c) => c.word?.code === "slot_empty")), "предмет items:3 '' (пусто)");
  assert.equal(slotSource({ code: "slot_code_fill", fill: { code: "slot_width", width: 66 } }), "66\u00a0px, заполнение кодом");
  assert.equal(slotSource(SLOT_CODES.find((c) => c.fill?.fill?.code === "slot_digits")), "3 цифры, английский 18\u00a0px, сумка");
});

test("a fill-source label only the project defines shows in the site language, as the slot rows show it", (ctx) => {
  ctx.after(() => { setCatalog(null); setPlaces(null); setUiLanguage("en"); });
  const label = { code: "slot_label", label: "Frostbite-curing berry" };
  const sources = [label, { code: "slot_reasons", source: label, reasons: [{ code: "slot_by_code" }] },
    { code: "slot_fill", fill: { code: "slot_label", label: "Nickname of up to ten letters" },
      where: "Mega Evolution and wish messages, unprefixed nickname" },
    { code: "slot_fill", fill: { code: "slot_english", fill: { code: "slot_label", label: "Trainer name" }, source_width: 67 },
      where: "battle text, name of the opposing trainer" },
    { code: "slot_code_fill", fill: { code: "slot_label", label: "Number" } }];
  const rowLabels = () => slotRows({ slot_details: { "{B_LAST_ITEM}": { label: label.label, width: 60, state: "computed" },
    "{STR_VAR_1}": { label: "Number", width: 18, state: "computed" } } }).map((row) => row.label);
  assert.deepEqual(sources.map(slotSource), ["Frostbite-curing berry", "Frostbite-curing berry (shown by code)",
    "Nickname of up to ten letters, Mega Evolution and wish messages, unprefixed nickname",
    "Trainer name, English 67 px, battle text, name of the opposing trainer", "Number, code fill"]);
  assert.deepEqual(rowLabels(), ["Frostbite-curing berry", "Number"]);
  // the profile's labels come with its descriptions, the toolkit's with the site catalog
  setPlaces({ "Mega Evolution and wish messages, unprefixed nickname": { ru: "сообщения мегаэволюции и желания, кличка без префикса" },
    "battle text, name of the opposing trainer": { ru: "боевой текст, имя тренера противника" },
    "Frostbite-curing berry": { ru: "Ягода от обморожения" }, "Nickname of up to ten letters": { ru: "Кличка длиной до десяти букв" },
    "Trainer name": { ru: "Имя тренера" } });
  setUiLanguage("ru");
  setCatalog({ "Number": "Число", "shown by code": "выводится кодом", "code fill": "заполнение кодом",
    "{label}, {where}": "{label}, {where}", "{label}, English {english} px, {where}": "{label}, в английском {english} px, {where}" });
  assert.deepEqual(sources.map(slotSource), ["Ягода от обморожения", "Ягода от обморожения (выводится кодом)",
    "Кличка длиной до десяти букв, сообщения мегаэволюции и желания, кличка без префикса",
    "Имя тренера, в английском 67 px, боевой текст, имя тренера противника", "Число, заполнение кодом"]);
  assert.deepEqual(rowLabels(), ["Ягода от обморожения", "Число"]);
});
