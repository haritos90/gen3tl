// Seeded packs exercise shared editor and patcher contracts.
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { test } from "node:test";
import { CODEC, STYLE } from "./fixture.mjs";
import { Codec, Glyphs, Measure } from "../js/font.js";
import { fontRuns } from "../js/fonts.js";
import { candidates, filterTerms, norm, surfaces } from "../js/terms.js";
import { TermEdits } from "../js/store.js";
import { ORIGINAL, outputName, pairOf, rowState, sidesOf } from "../js/save.js";
import { languagesOf } from "../js/patch.js";
import { digest, patchRelease } from "../js/patcher-core.js";
import { patchFixture } from "./patch-fixture.mjs";

const pack = (code) => {
  const root = new URL(`../../assets/lang/${code}/`, import.meta.url);
  const metadata = JSON.parse(readFileSync(new URL("language.json", root)));
  const chars = Object.fromEntries(readFileSync(new URL("charmap.tsv", root), "utf8").trim().split("\n")
    .slice(1).map((line) => line.split("\t").slice(0, 2)));
  return { metadata, chars };
};

for (const [code, name] of [["es", "Ñandú"], ["de", "Bär"], ["fr", "Élodie"]]) {
  test(`${code}: editor glyphs, search and glossary export use the selected pack`, () => {
    const { metadata, chars } = pack(code);
    const codec = new Codec(CODEC, chars, metadata);
    const glyphs = new Glyphs({ ...STYLE, pack: {} }, null);
    const measure = new Measure((b) => glyphs.width(b), codec, {});
    assert.equal(codec.encode(name).length, [...name].length);
    const keyboard = readFileSync(new URL(`../../assets/lang/${code}/keyboard.toml`, import.meta.url), "utf8");
    const rows = [...keyboard.matchAll(/^(?:lower|upper|symbols)_keys_\d = "(.*)"$/gm)];
    assert.equal(rows.length, 12);
    for (const [, row] of rows) assert.equal(codec.encode(row).length, 8);
    const sample = fontRuns(name, glyphs, codec);
    assert.equal(sample.unknown, name.length);
    assert.ok(sample.runs.every((r) => r.pixels?.some((p) => p === 1)));
    assert.equal(sample.width, codec.encode(name).reduce((sum, byte) => sum + measure.glyph(byte), 0));
    const term = { id: "name", translation: name, forms: {}, source_text: "Name" };
    assert.deepEqual(filterTerms([term], { search: name.toUpperCase() }, new Map(), metadata.fold), [0]);
    assert.deepEqual(surfaces({ translation: name[0] }, metadata.fold), [name[0].toLowerCase()]);
    assert.deepEqual([...candidates(term, [{ id: "line", text: name }], (r) => r.text, {}, metadata.fold)], ["line"]);
    const edits = new TermEdits(null, code, "fixture");
    edits.set(term, { ...term, translation: name + "x" });
    assert.deepEqual(edits.changes()[0].changes, { translation: name + "x" });
  });

  test(`${code}: save names and ROM patches use the selected release`, async () => {
    const { metadata, chars } = pack(code);
    const { source, result, patch } = patchFixture();
    const release = { profile: "fixture", profile_name: "Fixture", language: code, version: "1.0.0",
      source_sha1: await digest(source), source_size: source.length,
      result_sha1: await digest(result), result_size: result.length,
      patch: `fixture/${code}/1.0.0.ups`, patch_sha256: await digest(patch, "SHA-256"), patch_size: patch.length,
      output: `fixture-${code}-1.0.0.gba`, names: `fixture/${code}/1.0.0.names.json`, names_sha256: "0".repeat(64) };
    assert.deepEqual(await patchRelease(source, patch, release), result);
    assert.equal(languagesOf([release])[0].code, code);
    const pair = pairOf(ORIGINAL, `${code}@1.0.0`, sidesOf([release]));
    assert.equal(outputName("game.sav", pair), `game-${code}.sav`);
    assert.equal(rowState({ limit: 7, after: name, kind: "kept" }, name, Object.keys(chars).join("")).bad, false);
    assert.deepEqual(metadata.save.transliteration, {});
  });
}

test("normalization keeps separate pack rules across switches", () => {
  const rules = pack("ru").metadata.fold;
  assert.equal(norm("Ёлка", rules), "елка");
  assert.equal(norm("Ёлка", pack("fr").metadata.fold), "ёлка");
});

const TARGET_LANGUAGE_POLICY = /Cyrillic|Russian|\\u04|[А-Яа-яЁё]|["']ru["']/i;

test("page modules contain no target-language policy literals", () => {
  const root = new URL("../js/", import.meta.url);
  for (const name of readdirSync(root).filter((name) => name.endsWith(".js"))) {
    const source = readFileSync(new URL(name, root), "utf8");
    // Stored drafts need one conversion boundary.
    const policy = source.replace(/function migrateTermDraft\(draft\) \{[\s\S]*?\n\}/, "");
    assert.doesNotMatch(policy, TARGET_LANGUAGE_POLICY, name);
  }
});

test("language-pack policy guard rejects target-language literals", () => {
  for (const literal of ["const pack = \"ru\";", "const label = \"Russian\";", "const range = /[А-Яа-яЁё]/;",
    "const escaped = \"\\u0401\";", "const encoding = \"Cyrillic\";"]) {
    assert.match(literal, TARGET_LANGUAGE_POLICY, literal);
  }
});
