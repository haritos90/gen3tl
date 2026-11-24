import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { textsLoader, verifyProjectTexts } from "../js/dexpage.js";
import { inlineTrainerIvs, PokedexDataView, trainerIvMember } from "../js/pokedexdataview/pokedexdataview.js";
import { decodeDataset, decodeTexts, keyName, step, TEXTS_SCHEMAS, TRAINER_IV_SCHEMAS, textsPartUrl } from "../js/pokedexstore.js";
import { fakeFetch } from "./fixture.mjs";

// documents and their stored forms, written by gen3tl.data.pokedexstore
const FIXTURES = JSON.parse(readFileSync(new URL("./pokedexstore-fixtures.json", import.meta.url), "utf8"));
const previous = (doc, schema) => ({ ...structuredClone(doc), schema });

test("stored datasets decode to their documents, previous ones are their own", () => {
  for (const name of ["dataset", "learnsets", "trainer_ivs"]) {
    const { document, stored } = FIXTURES[name];
    assert.ok("codec" in stored, name);
    assert.deepEqual(decodeDataset(structuredClone(stored)), document, name);
    const old = previous(document, name === "trainer_ivs" ? 3 : 1);
    assert.equal(decodeDataset(old), old, name);
  }
  const decoded = decodeDataset(structuredClone(FIXTURES.dataset.stored));
  decoded.species[0].sprite.front = "changed";
  assert.equal(decoded.species[1].sprite.front, "sprites/2-front.png");
  assert.equal(decoded.species[0].form_browsing, undefined);
  assert.throws(() => decodeDataset([]), /not an object/);
});

test("stored keys and paths follow the Python names", () => {
  assert.deepEqual([0, 25, 26, 51, 52, 53, 104].map(keyName), ["a", "z", "A", "Z", "aa", "ab", "ba"]);
  assert.equal(step("", "species"), "species");
  assert.equal(step("trainers", "party"), "trainers/party");
  assert.equal(step("species", "1293"), "species/*");
  assert.equal(step("members", "0/1"), "members/0~11");
  const stored = structuredClone(FIXTURES.dataset.stored);
  stored.species[0].zz = 1;
  assert.throws(() => decodeDataset(stored), /unknown key/);
});

test("texts read the shared English part once; previous texts files read as themselves", async () => {
  const { document, stored, part } = FIXTURES.texts;
  assert.deepEqual(decodeTexts(stored, part), document);
  assert.throws(() => decodeTexts(stored, { ...part, part: "index" }), /not their English part/);
  const old = previous(document, 1);
  assert.equal(decodeTexts(old), old);
  for (const schema of TEXTS_SCHEMAS) verifyProjectTexts({ ...document, schema }, "game", "ru");
  const base = "https://site.test/data/game/";
  const partUrl = textsPartUrl(stored, `${base}ru/pokedex-texts.json`);
  assert.equal(partUrl, `${base}shared/${"0".repeat(64)}.json`);
  const files = {
    [`${base}ru/pokedex-texts.json`]: stored,
    [`${base}de/pokedex-texts.json`]: { ...stored, language: "de", texts: {} },
    [`${base}fr/pokedex-texts.json`]: { ...old, language: "fr" },
    [partUrl]: part,
  };
  const fetcher = fakeFetch(files);
  const read = textsLoader(async (url) => (await fetcher(url)).json());
  assert.deepEqual(await read(`${base}ru/pokedex-texts.json`), document);
  const german = await read(`${base}de/pokedex-texts.json`);
  assert.deepEqual(german.texts, { "species:1": ["Bulbasaur", ""], "species:2": ["Ivysaur", ""] });
  assert.deepEqual(german.page, { "page:power:flail": ["Power from remaining HP", ""], PP: "ОС" });
  assert.deepEqual(await read(`${base}fr/pokedex-texts.json`), { ...old, language: "fr" });
  assert.equal(fetcher.calls.filter((url) => url === partUrl).length, 1);
  await assert.rejects(read(`${base}ru/pokedex-texts.json`, () => { throw new Error("checked"); }), /checked/);
});

test("the trainer view reads stored and previous trainer IV companions", async () => {
  assert.deepEqual(TRAINER_IV_SCHEMAS, [3, "4.0.0"]);
  const { document, stored } = FIXTURES.trainer_ivs;
  for (const [file, expected] of [[stored, document], [previous(document, 3), previous(document, 3)]]) {
    const data = { profile_block: "abc", trainer_ivs: "trainer-ivs.json" };
    const fetcher = fakeFetch({ "data/trainer-ivs.json": file });
    const view = new PokedexDataView({ texts: new Map(), generation: 0,
      host: { data: () => data, datasetFile: (name) => `data/${name}`, fetcher } });
    await view.loadTrainerIvs();
    assert.equal(view.trainerIvsFailed, false);
    assert.deepEqual(data.trainer_ivs, expected);
    assert.deepEqual(trainerIvMember(data, 2, 0), expected.members[2]);
  }
  assert.throws(() => inlineTrainerIvs({ profile_block: "abc" }, previous(document, 2)), /unknown format/);
});

test("the species view inlines stored and previous learnsets", async () => {
  const fake = () => ({ children: [], style: {}, listeners: {}, classList: { toggle() {} },
    addEventListener(name, listener) { this.listeners[name] = listener; }, setAttribute() {},
    append() {}, replaceChildren() {} });
  globalThis.document ??= { createElement: () => fake(), createTextNode: (text) => ({ text }) };
  const { PokedexView } = await import("../js/pokedex/pokedex.js");
  const { document, stored } = FIXTURES.learnsets;
  for (const file of [stored, previous(document, 1)]) {
    const data = decodeDataset(structuredClone(FIXTURES.dataset.stored));
    const fetcher = fakeFetch({ "data/learnsets.json": file });
    const elements = new Map();
    const $ = (id) => elements.get(id) ?? elements.set(id, fake()).get(id);
    const view = new PokedexView({ data: () => data, datasetFile: (name) => `data/${name}`, fetcher }, $);
    assert.equal(await view.ensureLearnsets(), true);
    assert.deepEqual(data.species[1].learnset, document.species["2"].map(([method, move, level, index, offset]) =>
      ({ species: 2, method, move, level, index, offset })));
  }
});
