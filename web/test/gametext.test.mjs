// The workspace without a ROM and with the fixture ROM.
import assert from "node:assert/strict";
import "./patch-fixture.mjs";
import { createHash } from "node:crypto";
import { test } from "node:test";

import { filterRows } from "../js/data.js";
import { decodeGlyph } from "../js/font.js";
import { TEXT_NOTE, applyEnglish, applyGlossary, englishBlock, englishOf, joinRows, problemsHeading, readRom,
  searchHint, shipsEnglish, unavailableNote, withEnglish } from "../js/gametext.js";
import { digest as romDigest, patchRelease } from "../js/patcher-core.js";
import { setCatalog, t } from "../js/ui.js";
import { allForms, englishTerms, glossaryProblems, hasTermForm, norm, pyRepr, stripCodes } from "../js/terms.js";
import { EditorRuntime } from "../js/editor-runtime.js";

// The fixture ROM: two script texts and a name table
const LINES = [
  ["s0012001", 0x12001, "Hello {PLAYER}!", "c2 d9 e0 e0 e3 00 fd 01 ab ff"],
  ["s0012101", 0x12101, "Where are you going?\\nCome back!",
    "d1 dc d9 e6 d9 00 d5 e6 d9 00 ed e3 e9 00 db e3 dd e2 db ac fe bd e3 e1 d9 00 d6 d5 d7 df ab ff"],
  ["items:0", 0x15000, "POTION", "ca c9 ce c3 c9 c8 ff"],
  ["items:1", 0x15008, "ETHER", "bf ce c2 bf cc ff"],
];
const bytes = (hex) => hex.split(" ").map((b) => parseInt(b, 16));

function fixtureRom() {
  const rom = new Uint8Array(0x20000).fill(0xff);
  for (const [, at, , hex] of LINES) rom.set(bytes(hex), at);
  return rom;
}

// This patch overwrites dialogue and relocates a name.
const PATCH = new Uint8Array(Buffer.from(
  "55505331007f86007f86013f83083f3d0a3ae80078de3536313c363700799e3536313c36370091fb8c005753967723758743", "hex"));
const fileOf = (rom) => ({ name: "fixture.gba", arrayBuffer: async () => rom.slice().buffer });

const BASE_PATCH = new Uint8Array(Buffer.from(
  "55505331007f86007f86013f833d261f1f1cff02fe540075de3536313c36370018dde83891fb8c00c707e294", "hex"));

async function fixturePair() {
  const original = fixtureRom(), clean = original.slice();
  clean.fill(0xff, 0x12001, 0x1200a);
  clean.fill(0xff, 0x15000, 0x15006);
  const base = { name: "Fixture base 1.0", sha1: await romDigest(clean) };
  const game = { id: "game", name: "Fixture", sha1: await romDigest(original), size: original.length,
    patch_bases: [base] };
  return { original, clean, base, game };
}

const noNetwork = () => assert.fail("Local ROM and UPS files require no network request");

test("a recognized clean base requests a patch and remains unverified", async () => {
  const { clean, base, game } = await fixturePair();
  await assert.rejects(readRom(fileOf(clean), game, [], noNetwork), (error) => {
    assert.equal(error.patchBase, base);
    assert.match(error.message, /Choose the UPS patch/);
    assert.ok(error.message.includes(game.sha1));
    return true;
  });
});

test("the clean base and local UPS supply identical original text and glyphs", async () => {
  const { original, clean, game } = await fixturePair();
  const before = clean.slice(), patchBefore = BASE_PATCH.slice();
  const loaded = await readRom(fileOf(clean), game, [], noNetwork, fileOf(BASE_PATCH));
  const direct = await readRom(fileOf(original), game, [], noNetwork);
  assert.deepEqual(loaded, direct);
  assert.deepEqual(clean, before);
  assert.deepEqual(BASE_PATCH, patchBefore);
  assert.deepEqual(decodeGlyph(loaded.bytes.subarray(0x100, 0x140)), decodeGlyph(original.subarray(0x100, 0x140)));
  const read = check.english(loaded.bytes, SOURCE);
  assert.deepEqual(read.missing, []);
  const list = rows();
  assert.equal(applyEnglish(list, read.texts), 4);
  assert.deepEqual(list.map((r) => r.source_text), LINES.map(([, , text]) => text));
  assert.deepEqual(found(list, "hello"), ["s0012001"]);
});

test("a local UPS can expand the clean ROM to the profile size", async () => {
  const clean = new Uint8Array([1, 2, 3]), original = new Uint8Array([1, 8, 3, 4, 5]);
  const patch = new Uint8Array(Buffer.from("555053318385810a00800405001d80bc559079b628a0dd6088", "hex"));
  const game = { id: "game", name: "Fixture", sha1: await romDigest(original), size: original.length,
    patch_bases: [{ name: "Fixture base", sha1: await romDigest(clean) }] };
  const loaded = await readRom(fileOf(clean), game, [], noNetwork, fileOf(patch));
  assert.deepEqual(loaded.bytes, original);
  assert.equal(loaded.sha1, game.sha1);
});

test("unknown bases and a patch without its base are refused with the expected hash", async () => {
  const { clean, game } = await fixturePair();
  const unknown = clean.slice();
  unknown[0] ^= 1;
  const unreadPatch = { arrayBuffer: () => assert.fail("A wrong base cannot read the patch") };
  for (const bytes of [unknown, BASE_PATCH]) {
    await assert.rejects(readRom(fileOf(bytes), game, [], noNetwork, unreadPatch), (error) => {
      assert.equal(error.patchBase, undefined);
      assert.match(error.message, /matches neither/);
      assert.ok(error.message.includes(game.sha1));
      return true;
    });
  }
});

test("a recognized revision cannot use another revision's UPS", async () => {
  const { clean, game } = await fixturePair();
  clean[0] ^= 1;
  game.patch_bases.push({ name: "Fixture base 1.1", sha1: await romDigest(clean) });
  await assert.rejects(readRom(fileOf(clean), game, [], noNetwork, fileOf(BASE_PATCH)),
    new RegExp(`Source ROM does not match.*Expected SHA-1 ${game.sha1}`));
});

test("wrong patch versions, sizes and corrupt patches name the expected ROM hash", async () => {
  const { clean, game } = await fixturePair();
  const broken = BASE_PATCH.slice();
  broken[10] ^= 1;
  const cases = [
    [game, broken, /Patch CRC32 mismatch/],
    [game, new Uint8Array([1, 2, 3]), /Not a UPS patch/],
    [{ ...game, size: game.size + 1 }, BASE_PATCH, /UPS output size/],
    [{ ...game, sha1: "0".repeat(40) }, BASE_PATCH, /patch produced SHA-1/],
  ];
  for (const [profile, patch, reason] of cases) {
    await assert.rejects(readRom(fileOf(clean), profile, [], noNetwork, fileOf(patch)), (error) => {
      assert.match(error.message, reason);
      assert.ok(error.message.includes(`Expected SHA-1 ${profile.sha1}`));
      return true;
    });
  }
});

async function fixtureRelease() {
  const base = fixtureRom(), result = base.slice();
  result.set(bytes("ca e6 dd ea d9 e8"), 0x12001);
  result.fill(0xff, 0x15000, 0x15007);
  result.set(base.subarray(0x15000, 0x15007), 0x16000);
  const game = { id: "game", name: "Fixture", sha1: await romDigest(base), data: "https://site.test/data/game/" };
  const release = { profile: game.id, profile_name: game.name, language: "ru", version: "1.0.0",
    source_sha1: game.sha1, source_size: base.length, result_sha1: await romDigest(result), result_size: result.length,
    patch: "game/ru/1.0.0.ups", patch_sha256: await romDigest(PATCH, "SHA-256"), patch_size: PATCH.length,
    output: "game-ru-1.0.0.gba" };
  return { base, result, game, release };
}

const digest = (text) => createHash("sha256").update(text, "utf8").digest("hex");
const SOURCE = { schema: 1, profile: "game", rom_sha1: "0".repeat(40),
  rows: Object.fromEntries(LINES.map(([key, at, text, hex]) =>
    [key, [at, bytes(hex).length, "field", key.startsWith("items") ? "fixed" : "pointer", digest(text)]])) };

// catalog.source.read: lines decoded, kept on digest match
const DECODE = new Map([[0x00, " "], [0xab, "!"], [0xac, "?"], [0xfe, "\\n"],
  ...[..."ABCDEFGHIJKLMNOPQRSTUVWXYZ"].map((c, i) => [0xbb + i, c]),
  ...[..."abcdefghijklmnopqrstuvwxyz"].map((c, i) => [0xd5 + i, c])]);

function decode(rom, at) {
  let out = "";
  for (let i = at; rom[i] !== 0xff; i++) {
    if (rom[i] === 0xfd && rom[i + 1] === 0x01) {
      out += "{PLAYER}";
      i++;
    } else if (DECODE.has(rom[i])) out += DECODE.get(rom[i]);
    else return null;
  }
  return out;
}

const check = { english(rom, source) {
  const texts = {}, missing = [];
  for (const [key, [at, , , , expected]] of Object.entries(source.rows)) {
    const text = decode(rom, at);
    if (text !== null && digest(text) === expected) texts[key] = text;
    else missing.push(key);
  }
  return { texts, missing };
} };

/** The editor data's list rows, no English. */
const rows = () => [
  { id: "s0012001", chunk: 0, status: "approved", translation: "Привет, {PLAYER}!", problems: [] },
  { id: "s0012101", chunk: 0, status: "untranslated", translation: "", problems: [] },
  { id: "items:0", chunk: 1, status: "approved", translation: "ЗЕЛЬЕ", problems: [] },
  { id: "items:1", chunk: 1, status: "untranslated", translation: "", problems: [] },
];
/** The fixture glossary in the editor data's shape. */
const GLOSSARY = {
  "term:hello": { category: "term", source_text: "Hello", translation: "Привет", label: "", gender: "", address: "",
    status: "approved", notes: "", forms: { "nomn.sing": "Привет" } },
  "term:back": { category: "term", source_text: "Come back", translation: "Вернись", label: "", gender: "", address: "",
    status: "approved", notes: "", forms: { "nomn.sing": "Вернись" } },
  "term:potion": { category: "item", source_text: "POTION", translation: "Зелье", label: "", gender: "", address: "",
    status: "approved", notes: "", forms: { "nomn.sing": "Зелье" } },
  "term:ether": { category: "item", source_text: "ETHER", translation: "Эфир", label: "", gender: "", address: "",
    status: "approved", notes: "", forms: { "nomn.sing": "Эфир" } },
};

const found = (list, search) => filterRows(list, { search }).map((i) => list[i].id);

test("without a ROM: keys, translations and states; no English, no search by it", () => {
  const list = rows();
  assert.equal(shipsEnglish({ list }), false);
  assert.equal(searchHint(list), "Search translation or id");
  assert.deepEqual(found(list, "hello"), []);
  assert.deepEqual(found(list, "привет"), ["s0012001"]);
  assert.deepEqual(found(list, "items:1"), ["items:1"]);
  const entry = withEnglish({ id: "s0012001", tokens: ["{PLAYER}"], translation: "Привет, {PLAYER}!" }, null);
  assert.equal("english" in entry, false); // the check runs on the tokens
  assert.deepEqual(englishBlock(entry, false), { note: TEXT_NOTE });
  assert.equal(unavailableNote({ problems: [], unavailable: ["typography", "glossary"] }),
    "Typography and glossary checks need the game's text: load the ROM, original or patched.");
  assert.equal(unavailableNote({ problems: [] }), "");
  assert.deepEqual(problemsHeading({ exact: false }, null, null), { key: "Problems: ROM not loaded" });
  assert.deepEqual(problemsHeading({ draft: true }, null, null), { key: "Problems: placeholders only" });
  assert.deepEqual(problemsHeading({ estimate: true }, null, null), { key: "Problems: check not loaded" });
  // the site interface and draft catalogs ship their own English
  assert.equal(shipsEnglish({ list: [{ id: "a", source_text: "Patch", translation: "" }] }), true);
});

test("context names resolve through the loaded English, else show their keys", () => {
  const texts = { "map:1.0/npc:0/0": "Bellin Town", "table:trainer_names/117": "Sally" };
  assert.equal(englishOf(texts, "map:1.0/npc:0/0"), "Bellin Town");
  assert.equal(englishOf(texts, "table:trainer_names/117"), "Sally");
  assert.equal(englishOf(null, "map:1.0/npc:0/0"), "map:1.0/npc:0/0");
  assert.equal(englishOf({}, "map:1.0/npc:0/0"), "map:1.0/npc:0/0");
  assert.equal(englishOf(texts, "map:9.9/npc:0/0"), "map:9.9/npc:0/0"); // the key stands in
});

test("with the original fixture ROM: the English of every line, its search and its check", async () => {
  const { base, game } = await fixtureRelease();
  const loaded = await readRom(fileOf(base), { ...game, sha1: game.sha1.toUpperCase() }, [], () => assert.fail("No patch needed"));
  const rom = loaded.bytes;
  assert.equal(loaded.sha1, game.sha1);
  assert.equal(loaded.release, undefined);
  const read = check.english(rom, SOURCE);
  assert.deepEqual(read.missing, []);
  const list = rows();
  assert.equal(applyEnglish(list, read.texts), 4);
  assert.deepEqual(list.map((r) => r.source_text), LINES.map(([, , text]) => text));
  assert.equal(searchHint(list), "Search English, translation or id");
  assert.deepEqual(found(list, "come back"), ["s0012101"]);
  const entry = withEnglish({ id: "s0012101", tokens: [], translation: "" }, read.texts);
  assert.deepEqual(englishBlock(entry, true), { source_text: "Where are you going?\\nCome back!" });
  assert.deepEqual(problemsHeading({ exact: true }, rom, read), { key: "Problems: original ROM" });
  assert.equal(applyEnglish(list, null), 0); // the ROM forgotten
  assert.deepEqual(found(list, "come back"), []);
});

test("with the fixture ROM: glossary terms and problems follow the loaded English", async () => {
  const rom = fixtureRom();
  const read = check.english(rom, SOURCE);
  const list = rows();
  list[2].translation = "ВОДА"; // The POTION entry without a glossary form.
  applyEnglish(list, read.texts);
  assert.equal(applyGlossary(list, read.texts, GLOSSARY), 1);
  assert.deepEqual(list[0].terms, ["term:hello"]);
  assert.deepEqual(list[1].terms, ["term:back"]);
  assert.deepEqual(list[2].terms, ["term:potion"]);
  assert.deepEqual(list[2].problems, [{
    severity: "warning",
    message: "glossary term 'POTION' -> 'Зелье' not found (warning)",
  }]);
  assert.deepEqual(list[0].problems, [], "a present form leaves no glossary problem");
  assert.deepEqual(list[1].problems, [], "untranslated rows keep the build's marks");
  assert.equal(applyGlossary(list, null, GLOSSARY), 0); // The ROM forgotten.
  assert.equal("terms" in list[0], false);
  assert.deepEqual(list[2].problems, []);
});

test("the combined worker join preserves every loaded row field", async () => {
  const read = check.english(fixtureRom(), SOURCE);
  const expected = rows();
  applyEnglish(expected, read.texts);
  applyGlossary(expected, read.texts, GLOSSARY);
  assert.deepEqual(joinRows(rows(), read.texts, GLOSSARY), expected);
});

test("indexed glossary matches and warnings equal the plain matcher", () => {
  const glossary = { ...GLOSSARY,
    alpha: { source_text: "Alpha", translation: "One" },
    other: { source_text: "Alpha", translation: "Alternative" },
    long: { source_text: "Alpha Beta", translation: "Two" },
    berry: { source_text: "Berry", translation: "Fruit" },
    box: { source_text: "BOX", translation: "Crate" },
    plus: { source_text: "A+B", translation: "A+B" },
    accent: { source_text: "éclair", translation: "Éclair" },
    short: { source_text: "HP", translation: "Health" },
    table: { source_text: "Ignored", translation: "Ignored", category: "dex-category" },
  };
  const plurals = { Alpha: "Alphas", "Alpha Beta": "Alpha Betas", Berry: "Berries", BOX: "BOXES",
    "A+B": "A+BS", éclair: "éclairs", Hello: "Hellos", "Come back": "Come backs", POTION: "POTIONS", ETHER: "ETHERS" };
  const byName = new Map();
  for (const [id, term] of Object.entries(glossary)) {
    if (!(term.source_text in plurals)) continue;
    for (const name of [term.source_text, plurals[term.source_text]]) {
      if (!byName.has(name)) byName.set(name, []);
      byName.get(name).push({ ...term, id });
    }
  }
  const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const names = [...byName.keys()].sort((a, b) => b.length - a.length);
  const pattern = new RegExp(`(?<![A-Za-z])(${names.map(escape).join("|")})(?![A-Za-z])`, "g");
  const entries = [...LINES.map(([, , text]) => text), "Alpha Beta Alpha Alphas Berries BOXES A+Bs éclairs",
    "{Alpha}\\nAlpha\\lBeta\\pBerry", "Alpha BetaX Alpha BetasX", "Alpha Alpha HP Ignored", "alpha ALPHA"];
  for (const left of ["", "x", "1", "_", "é", "𐐀", "! "])
    for (const right of ["", "x", "1", "_", "é", "𐐀", " !"])
      entries.push(`${left}Alpha Beta${right} ${left}A+B${right}`);
  const fold = { "é": "e" };
  for (const english of entries) {
    const matches = [...stripCodes(english).matchAll(pattern)].flatMap((match) => byName.get(match[1]));
    assert.deepEqual(englishTerms(english, glossary), [...new Set(matches.map((term) => term.id))], english);
    for (const translation of ["", "One Two Fruit Crate Éclair A+B", "Alternative", "Someone", "𐐀One One𐐀", "One1 _One_ One"]) {
      const low = norm(stripCodes(translation), fold);
      const present = new Set(matches.filter((term) => Object.values(allForms(term)).some((form) => form &&
        new RegExp(`(?<![\\p{L}\\p{N}])${escape(norm(form, fold))}(?![\\p{L}\\p{N}])`, "u").test(low)))
        .map((term) => term.source_text));
      const expected = matches.filter((term) => !present.has(term.source_text)).map((term) => ({ severity: "warning",
        message: `glossary term ${pyRepr(term.source_text)} -> ${pyRepr(term.translation)} not found (warning)` }));
      assert.deepEqual(glossaryProblems(english, translation, glossary, fold), expected, `${english}: ${translation}`);
    }
  }
  assert.deepEqual(englishTerms("Anything", {}), []);
});

test("literal term forms retain Unicode boundaries, folds and overlapping matches", () => {
  const term = { translation: "A+A", forms: { empty: "", accent: "Éclair" }, label: "Box." };
  for (const [text, expected] of [["A+A+A", true], ["xA+A+A", true], ["𐐀A+A", false], ["A+A𐐀", false],
    ["A+A１", false], ["_A+A_", true], ["X\udc00A+A", true], ["éclair", true], ["Eclair", true], ["Box.", true], ["Box.es", false]]) {
    assert.equal(hasTermForm(text, term, { é: "e" }), expected, text);
  }
});

test("fallback joins yield between slices and never mutate active rows", async () => {
  const runtime = new EditorRuntime();
  const original = Array.from({ length: 600 }, (_, i) => ({ id: String(i), translation: "", status: "untranslated", problems: [] }));
  const texts = Object.fromEntries(original.map((row) => [row.id, "Hello"]));
  const pending = runtime.join(original, texts, GLOSSARY);
  let completed = false;
  pending.then(() => { completed = true; });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(completed, false);
  assert.ok(original.every((row) => !("english" in row)));
  const joined = await pending;
  assert.deepEqual(joined, joinRows(structuredClone(original), texts, GLOSSARY));
});

test("a released fixture ROM restores every English line before extraction", async () => {
  const { base, result, game, release } = await fixtureRelease();
  const patched = await patchRelease(base, PATCH, release);
  assert.deepEqual(patched, result);
  const partial = check.english(patched, SOURCE);
  assert.deepEqual(partial.missing, ["s0012001", "items:0"]);
  const calls = [];
  const other = { ...release, language: "de", result_sha1: "0".repeat(40),
    patch: "game/de/1.0.0.ups", output: "game-de-1.0.0.gba" };
  const loaded = await readRom(fileOf(patched), game, [other, release], async (url, options) => {
    calls.push(url);
    assert.deepEqual(options, { credentials: "omit", redirect: "error", cache: "no-store" });
    return { ok: true, arrayBuffer: async () => PATCH.slice().buffer };
  });
  assert.deepEqual(calls, [`${game.data}releases/${release.patch}`]);
  assert.deepEqual(loaded.bytes, base);
  assert.deepEqual(patched, result);
  assert.equal(loaded.sha1, game.sha1);
  assert.equal(loaded.release, release);
  const read = check.english(loaded.bytes, SOURCE);
  assert.deepEqual(read.missing, []);
  const list = rows();
  assert.equal(applyEnglish(list, read.texts), 4);
  assert.deepEqual(list.map((r) => r.source_text), LINES.map(([, , text]) => text));
  assert.deepEqual(found(list, "hello"), ["s0012001"]);
  assert.deepEqual(found(list, "potion"), ["items:0"]);
  const entry = withEnglish({ id: "s0012001", tokens: ["{PLAYER}"], translation: "Привет, {PLAYER}!" }, read.texts);
  assert.deepEqual(englishBlock(entry, true), { source_text: "Hello {PLAYER}!" });
  assert.deepEqual(problemsHeading({ exact: true }, loaded), { key: "Problems: original ROM" });
});

test("unreleased or wrong-game ROMs are refused before downloading a patch", async () => {
  const { result, game, release } = await fixtureRelease();
  const noFetch = () => assert.fail("Unrecognized ROMs must not download patches");
  for (const releases of [[], [{ ...release, source_sha1: "0".repeat(40) }]]) {
    await assert.rejects(readRom(fileOf(result), game, releases, noFetch),
      new RegExp(`SHA-1 ${release.result_sha1} matches neither.*published release.*${game.sha1}`));
  }
  await assert.rejects(readRom(fileOf(result), { ...game, id: "other" }, [release], noFetch), /matches neither/);
  result[0] ^= 1;
  await assert.rejects(readRom(fileOf(result), game, [release], noFetch), /matches neither/);
});

test("failed or mismatched release patch downloads are refused", async () => {
  const { result, game, release } = await fixtureRelease();
  await assert.rejects(readRom(fileOf(result), game, [release], async () => ({ ok: false, status: 404 })),
    /Patch download failed \(404\)/);
  const broken = PATCH.slice();
  broken[10] ^= 1;
  await assert.rejects(readRom(fileOf(result), game, [release], async () =>
    ({ ok: true, arrayBuffer: async () => broken.buffer })), /Patch download differs/);
});

test("the problems heading resolves verified and absent ROMs through the page catalog", () => {
  const cases = [
    [problemsHeading({}, null, null), "Проблемы: не загружен ROM"],
    [problemsHeading({}, {}, null), "Проблемы: оригинальный ROM"],
  ];
  setCatalog({
    "Problems: ROM not loaded": "Проблемы: не загружен ROM",
    "Problems: original ROM": "Проблемы: оригинальный ROM",
  });
  try {
    for (const [heading, expected] of cases) assert.equal(t(heading.key, heading.vars), expected);
  } finally {
    setCatalog(null);
  }
});
