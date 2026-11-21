// The check's messages: kinds, notes, values and site language.
import assert from "node:assert/strict";
import { test } from "node:test";

import { ENCODING, MESSAGES, NOTES, checkText, extractionFailed, findingText, layoutFailed, limitReferences, limitText, parseFinding,
  parseMessage, setPlaces, setReasons, slotSource } from "../js/checktext.js";
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
