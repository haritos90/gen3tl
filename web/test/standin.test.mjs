// ROM-free preview contracts.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { Codec, Glyphs, INK, SHADOW, Measure } from "../js/font.js";
import { drawPreview } from "../js/preview.js";
import { standIn, standInName } from "../js/standin.js";
import { CODEC, STYLE } from "./fixture.mjs";

const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
const symbols = "ÀÁÂÇÈÉÊËÌÎÏÒÓÔŒÙÚÛÑßàáçèéêëìîïòóôœùúûñºª&+=;¿¡Í%()âí<>!?.,-·…“”‘’♂♀¥×/▶:ÄÖÜäöü";

test("drawn characters have bounded ink and shadow in both cells", () => {
  for (const small of [false, true]) for (const ch of alphabet + symbols) {
    const width = small ? 5 : 6;
    const px = standIn(ch, width, small);
    assert.equal(px.length, 256);
    assert.ok(px.includes(INK), ch);
    assert.ok(px.includes(SHADOW), ch);
    for (let i = 0; i < px.length; i++) if (px[i]) assert.ok(i % 16 < width, `${ch}: column ${i % 16}`);
  }
  assert.ok(standIn(" ", 6).every((p) => p === 0));
});

test("look-alikes reuse only project-drawn Latin shapes", () => {
  const aliases = JSON.parse(readFileSync(new URL("../../assets/lang/ru/standins.json", import.meta.url)));
  assert.deepEqual(standIn("А", 6, false, aliases), standIn("A", 6));
  assert.deepEqual(standIn("р", 5, true, aliases), standIn("p", 5, true));
  assert.deepEqual(standIn("Ё", 6, false, aliases), standIn("Ë", 6));
  assert.notDeepEqual(standIn("Ё", 6, false, aliases), standIn("Е", 6, false, aliases));
  assert.notDeepEqual(standIn("a", 6), standIn("o", 6));
  assert.notDeepEqual(standIn("i", 6), standIn("í", 6));
});

test("named glyphs resolve by codec identity, including raw tokens", () => {
  const codec = new Codec(CODEC);
  assert.equal(standInName("UP", 0x79, codec), "↑");
  assert.equal(standInName("BB", 0xbb, codec), "A");
  assert.equal(standInName("LV", 0x34, codec), "Lv");
  assert.equal(standInName("{F9 40}", 0x140, codec), "?");
});

function canvas() {
  const colors = new Set();
  const context = { clearRect() {}, fillRect() { colors.add(this.fillStyle); } };
  return { colors, style: {}, getContext: () => context };
}

test("no-ROM preview draws without system fonts and remains approximate", () => {
  const codec = new Codec(CODEC);
  const glyphs = new Glyphs(STYLE, null);
  const measure = new Measure((b) => glyphs.width(b), codec, {}, 60);
  const before = [...glyphs.table.widths];
  for (const zoom of [1, 2, 3]) {
    const target = canvas();
    const result = drawPreview(target, "Ab 1.!", { window: { kind: "single", width: 60 }, measure, glyphs, zoom });
    assert.equal(result.unknown, 6);
    assert.equal(target.style.width, `${target.width * zoom}px`);
    assert.ok(target.colors.has("#505058"));
    assert.ok(target.colors.has("#d0d0c8"));
    assert.ok(!target.colors.has("#8a5cc8"));
  }
  assert.deepEqual([...glyphs.table.widths], before);
  assert.equal(glyphs.get(0xbb).exact, false);
});

test("exact art wins; battle stand-ins use battle ink", () => {
  const codec = new Codec(CODEC);
  const glyphs = new Glyphs({ ...STYLE, pack: { BB: 6 } }, { top: 0, art: { BB: ["#s"] } });
  const measure = new Measure((b) => glyphs.width(b), codec, {}, 60);
  const target = canvas();
  const result = drawPreview(target, "AB", { window: { kind: "battle", width: 60 }, measure, glyphs, zoom: 1 });
  assert.equal(result.unknown, 1);
  assert.equal(glyphs.get(0xbb).pixels[0], INK);
  assert.ok(target.colors.has("#f8f8f8"));
  assert.ok(target.colors.has("#686078"));
});
