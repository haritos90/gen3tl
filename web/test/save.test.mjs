import assert from "node:assert/strict";

import { test } from "node:test";

import { readFileSync } from "node:fs";

import { CONTAINER_NAMES, editSummaryText, foldOf, groupsText, KIND_NOTES, missingLetters, namesNote, overBy, placeLabel, requestsOf, rowState, shownName, summaryText } from "../js/save.js";

import { setCatalog, setUiLanguage } from "../js/ui.js";

test("save placeholders reuse final markup and cannot match another page", () => {
  const html = readFileSync(new URL("../save.html", import.meta.url), "utf8");
  assert.match(html, /<body class="save-page">/);
  // An error note reserves no room:
  // a working converter must not move the page.
  assert.doesNotMatch(html, /id="engine-loading"/);
  assert.match(html, /<p class="summary script-placeholder loading-copy" id="direction-summary-loading"/);
  assert.doesNotMatch(html, /data-reserve/);
  for (const name of ["index.html", "pick.html", "patch.html", "guide.html"]) {
    assert.doesNotMatch(readFileSync(new URL(`../${name}`, import.meta.url), "utf8"), /<body class="save-page">/, name);
  }
  const css = readFileSync(new URL("../site.css", import.meta.url), "utf8");
  const start = css.indexOf("body.save-page .script-placeholder"), end = css.indexOf("body.picker-page #games-placeholders", start);
  const rules = css.slice(start, end).split("\n").filter((line) => line.includes("{"));
  assert.ok(rules.length > 0);
  for (const line of rules) assert.match(line.trim(), /^(?:html\[data-scripted\] )?body\.save-page /, line);
  assert.doesNotMatch(css.slice(start, end), /min-height|data-reserve/);
});

test("every container the converter reads has a name on the page", () => {
  const source = readFileSync(new URL("../../src/gen3tl/verify/savefile.py", import.meta.url), "utf8");
  const table = source.match(/\nCONTAINERS = \{\n([^]*?)\n\}/)[1];
  const kinds = [...table.matchAll(/^ {4}"(\w+)":/gm)].map((m) => m[1]);
  assert.deepEqual(Object.keys(CONTAINER_NAMES), kinds);
  assert.equal(kinds.length, 11);
});

test("renames: only fields that differ, and every name that does not fit", () => {
  const rows = [{ place: "player", after: "Sasha", kind: "transliterated", limit: 7 },
    { place: "party 2", after: "Shchekochushchij", kind: "over", limit: 10 },
    { place: "box 3", after: "Team", kind: "kept", limit: 8 }];
  assert.deepEqual(requestsOf(rows, ["Sasha", "Tickles", " Team "]), [["party 2", "Tickles"]]);
  assert.deepEqual(requestsOf(rows, ["Ash", undefined, "Base"]),
    [["player", "Ash"], ["party 2", "Shchekochushchij"], ["box 3", "Base"]]);
  assert.equal(overBy("Shchekochushchij", 10), 6);
  assert.equal(overBy("Искра", 10), 0);
  assert.equal(KIND_NOTES.default, "default name, converted");
});

test("rows: trainer, rival, party, other names, then boxes; kept boxes fold", async () => {
  const { orderOf } = await import("../js/save.js");
  const rows = ["box 10", "party 2", "box 2", "player", "mail 1", "party 1", "rival"].map((place) => ({ place, kind: "kept" }));
  assert.deepEqual(orderOf(rows).map((i) => rows[i].place),
    ["player", "rival", "party 1", "party 2", "mail 1", "box 2", "box 10"]);
  const more = ["party 3 OT", "box 20 slot 1", "box 1 slot 1", "box 1 slot 1 OT", "mail 1"].map((place) => ({ place, kind: "kept" }));
  assert.deepEqual(orderOf([{ place: "box 2", kind: "kept" }, ...more, { place: "party 3", kind: "kept" }]).slice(1, 6),
    [1, 2, 3, 4, 5], "the other names in the converter's order");
  assert.equal(foldOf({ place: "box 3", kind: "kept" }), 0);
  assert.equal(foldOf({ place: "box 3", kind: "over" }), -1);
  assert.equal(foldOf({ place: "party 1", kind: "kept" }), -1);
});

test("edit only: every stored name is a row; untouched fields are never written", (ctx) => {
  const rows = [{ place: "player", after: "Sasha", kind: "kept", limit: 7 }, { place: "party 2", after: "", kind: "kept", limit: 10 },
    { place: "box 3 slot 5", after: "Rex ", kind: "kept", limit: 10 }, { place: "box 4", after: "Team", kind: "kept", limit: 8 }];
  assert.deepEqual(requestsOf(rows, rows.map((r) => r.after)), []);
  assert.deepEqual(requestsOf(rows, ["Sasha", "Искра", "Rex ", "Base"]), [["party 2", "Искра"], ["box 4", "Base"]]);
  assert.equal(overBy("Schekochuschiy", 10), 4);
  assert.deepEqual(rows.map(foldOf), [-1, -1, -1, 0]);
  assert.equal(placeLabel("box 3 slot 5 OT"), "box 3, slot 5: original trainer");
  assert.equal(placeLabel("daycare 1 mail OT"), "daycare 1: mail, original trainer");
  assert.equal(placeLabel("hall of fame 1 slot 2"), "Hall of Fame 1, slot 2");
  ctx.after(() => { setUiLanguage("en"); setCatalog(null); });
  setUiLanguage("ru");
  setCatalog({ "daycare {n}": "питомник {n}", "mail {n}": "письмо {n}", "link record {n}": "связь {n}",
    "{place}: mail author": "{place}: автор письма", "{place}: mail, Pokémon": "{place}: письмо, покемон",
    "slot {n}: {place}": "слот {n}: {place}", "box {box}, slot {slot}": "ящик {box}, место {slot}",
    "{place}: original trainer": "{place}: тренер" });
  assert.deepEqual(["daycare 1", "mail 2", "link record 1", "daycare 1 mail", "daycare 1 mail nickname",
    "slot 0 box 20 slot 1 OT"].map(placeLabel), ["питомник 1", "письмо 2", "связь 1", "питомник 1: автор письма",
    "питомник 1: письмо, покемон", "слот 0: ящик 20, место 1: тренер"]);
  assert.equal(foldOf({ place: "daycare 1", kind: "kept" }), -1);
  assert.equal(foldOf({ place: "box 3 slot 5", kind: "renamed" }), -1);
});

test("edit only: the success line counts the names written", () => {
  const groups = [{ kind: "trainer record", count: 2, older_slot: 1 }, { kind: "original trainer of a Pokémon", count: 3, older_slot: 1 }];
  assert.equal(editSummaryText({ renamed: 5, renamed_groups: groups }, "x-edited.sav"),
    "Names written: 5 (2 × trainer record (1 in the older save slot), 3 × original trainer of a Pokémon (1 in the older save slot)). "
    + "A renamed player is also the original trainer on every Pokémon the player caught, so each copy is renamed. "
    + "Load x-edited.sav with the same game as before.");
  assert.equal(editSummaryText({ renamed: 1, renamed_groups: [{ kind: "box name", count: 1, older_slot: 0 }] }, "x.sav"),
    "Names written: 1. Load x.sav with the same game as before.");
  assert.equal(editSummaryText({ renamed: 0, renamed_groups: [] }, "x.sav"), "No name changed: x.sav is the save as it was.");
});

test("the success line explains what the counts are made of", () => {
  const groups = [{ kind: "trainer record", count: 2, older_slot: 1 }, { kind: "original trainer of a Pokémon", count: 58, older_slot: 0 }];
  assert.equal(groupsText(groups), "2 × trainer record (1 in the older save slot), 58 × original trainer of a Pokémon");
  const report = { defaults: 3, transliterated: 1, renamed: 60, renamed_groups: groups, transliterated_groups: [{ kind: "rival", count: 1, older_slot: 0 }] };
  assert.equal(summaryText(report, "game-ru.sav", false),
    "3 default names converted, 1 written in Latin letters, 60 renamed (2 × trainer record (1 in the older save slot), "
    + "58 × original trainer of a Pokémon). A renamed player is also the original trainer on every Pokémon the player caught, "
    + "so each copy is renamed. Load game-ru.sav with the translated ROM.");
  assert.equal(summaryText({ defaults: 0, transliterated: 0, renamed: 1, renamed_groups: [{ kind: "box name", count: 1, older_slot: 0 }] }, "x.sav", true),
    "0 default names converted, 0 written in Latin letters, 1 renamed. Load x.sav with the English ROM.");
  assert.equal(summaryText({ defaults: 2, transliterated: 4, renamed: 0 }, "x.sav", false),
    "2 default names converted, 4 written in Latin letters, 0 renamed. Load x.sav with the translated ROM.");
});

test("an egg row: read-only in both modes, shown as an egg, never renamed", (ctx) => {
  // both passes report an egg: no name, whatever its bytes
  const converting = { place: "party 3", stored: "", after: "", kind: "kept", limit: 10, egg: true };
  const editing = { ...converting, place: "party 5" };
  for (const row of [converting, editing]) {
    assert.deepEqual(rowState(row, shownName(row, row.after)),
      { note: "egg: the name is set at hatching", bad: false, locked: true, missing: [] });
    assert.equal(rowState(row, "Шарик©", new Set("Шарик")).locked, true);
    assert.deepEqual([shownName(row, row.stored), shownName(row, row.after)], ["Egg", "Egg"]);
  }
  assert.deepEqual(requestsOf([converting, editing], ["Egg", "Малыш"]), []);
  assert.equal(KIND_NOTES.egg, undefined);
  const named = { place: "party 2", stored: "Искра", after: "Искра", kind: "kept", limit: 10, egg: false };
  assert.equal(shownName(named, named.stored), "Искра");
  assert.deepEqual(rowState(named, "Искра"), { note: "kept as it is", bad: false, locked: false, missing: [] });
  assert.deepEqual(rowState(named, "Искорка-молния"),
    { note: "4 letters over the 10 the game allows", bad: true, locked: false, missing: [] });
  assert.deepEqual(requestsOf([converting, named], ["Egg", "Малыш"]), [["party 2", "Малыш"]]);
  ctx.after(() => { setUiLanguage("en"); setCatalog(null); });
  setUiLanguage("ru");
  setCatalog({ Egg: "Яйцо" });
  assert.equal(shownName(converting, converting.stored), "Яйцо");
});

test("an over-limit note is a key of the site catalog, one letter or more", (ctx) => {
  ctx.after(() => setCatalog(null));
  setCatalog({ "{by} letter over the {limit} the game allows": "лишняя буква: {by}, игра допускает {limit}",
    "{by} letters over the {limit} the game allows": "лишних букв: {by}, игра допускает {limit}" });
  const row = { place: "party 2", stored: "Искра", after: "Искра", kind: "kept", limit: 10 };
  assert.equal(rowState(row, "Искорка-молния").note, "лишних букв: 4, игра допускает 10");
  assert.equal(rowState(row, "Искорка-мол").note, "лишняя буква: 1, игра допускает 10");
  setCatalog(null);
  assert.equal(rowState(row, "Искорка-мол").note, "1 letter over the 10 the game allows");
});

test("notes: cleared without a readable save, the version the save is read for, the drop line per mode", () => {
  const report = { defaults: 12, listed_defaults: 3 };
  assert.equal(namesNote(null, false), "");
  assert.equal(namesNote(null, true), "");
  assert.equal(namesNote(report, false), "Only custom Pokémon nicknames are editable. Default species names convert automatically.");
  assert.equal(namesNote(report, true), "Only custom Pokémon nicknames are editable. Default species names stay unchanged.");
});

test("a letter the target game cannot show marks the field as it is typed, in both modes", () => {
  const latin = new Set(" ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!?.-");
  const cyrillic = new Set([...latin, ..."АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя"]);
  assert.deepEqual(missingLetters(" Искра ", latin), ["И", "с", "к", "р", "а"]);
  assert.deepEqual(missingLetters("Rex©©", latin), ["©"]);
  // English game: a Cyrillic letter typed in a Latin name
  const back = { place: "party 2", stored: "Искра", after: "Iskra", kind: "transliterated", limit: 10 };
  assert.deepEqual(rowState(back, "Iskra", latin), { note: "written in Latin letters", bad: false, locked: false, missing: [] });
  assert.deepEqual(rowState(back, "Iskrа", latin),
    { note: "the game cannot show а", bad: true, locked: false, missing: ["а"] });
  const over = { place: "party 2", stored: "Щекочущий", after: "Schekochuschiy", kind: "over", limit: 10 };
  assert.equal(rowState(over, "Щекотун", latin).note, "the game cannot show Щ е к о т у н");
  assert.deepEqual(rowState(over, "Tickles", latin), { note: "renamed here", bad: false, locked: false, missing: [] });
  // editing for the English game, then for the translated one
  const english = { place: "player", stored: "Ash", after: "Ash", kind: "kept", limit: 7 };
  assert.equal(rowState(english, "Саша", latin).bad, true);
  assert.equal(rowState(english, "Sasha", latin).bad, false);
  const russian = { place: "party 1", stored: "Шарик", after: "Шарик", kind: "kept", limit: 10 };
  assert.deepEqual(rowState(russian, "Шарик©", cyrillic),
    { note: "the game cannot show ©", bad: true, locked: false, missing: ["©"] });
  assert.equal(rowState(russian, "Шарик 2", cyrillic).bad, false);
  assert.equal(rowState(russian, "Шарик-Барбарис©", cyrillic).note, "the game cannot show ©", "letters first, then length");
  // a stored name is never checked, only typed ones
  const stored = { place: "box 2 slot 1", stored: "Ёж©", after: "Ёж©", kind: "kept", limit: 10 };
  assert.equal(rowState(stored, "Ёж©", latin).bad, false);
  assert.equal(rowState(english, "Саша", null).bad, false);
  assert.equal(rowState({ ...russian, egg: true }, "Шарик©", cyrillic).locked, true);
});

test("default and egg rows cannot create manual nickname requests", () => {
  const rows = [{ place: "party 1", after: "Bulbasaur", kind: "default", renameable: false },
    { place: "box 1 slot 2", after: "Buddy", kind: "kept", renameable: true },
    { place: "party 2", after: "Egg", egg: true }];
  assert.deepEqual(requestsOf(rows, ["New", "Bulbasaur", "New"]), [["box 1 slot 2", "Bulbasaur"]]);
  assert.equal(foldOf(rows[1]), -1, "PC nicknames use their own selector");
});
