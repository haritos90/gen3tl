// Site typography follows declared Unicode script coverage.
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { test } from "node:test";

import { fontRules, fontScript, fontStack, installFontRules, refreshSiteFonts } from "../js/site-fonts.js";

const manifest = JSON.parse(readFileSync(new URL("../fonts/coverage.json", import.meta.url), "utf8"));
const script = (id) => manifest.scripts.find((item) => item.id === id);

test("bundled subsets exist and every script has explicit role fallbacks", () => {
  for (const item of manifest.scripts) {
    assert.ok(item.ranges.length);
    for (const role of Object.keys(manifest.roles)) {
      const stack = fontStack(manifest, role, item.id);
      assert.ok(stack.length, `${item.id}: ${role}`);
      if (role === "display" && !item.subsets[role]) assert.doesNotMatch(stack, /Bricolage/);
      for (const file of item.subsets[role] ?? []) {
        assert.ok(existsSync(new URL(`../fonts/${file}`, import.meta.url)), file);
      }
    }
  }
  assert.deepEqual(script("latn").subsets.display, ["bricolage-grotesque-latin.woff2"]);
  assert.equal(script("cyrl").subsets.display, undefined);
  assert.deepEqual(script("cyrl").fallbacks.display.slice(0, 2), ["IBM Plex Sans", "system-ui"]);
});

test("text detection uses Unicode ranges and prefers an uncovered script", () => {
  assert.equal(fontScript("A heading", manifest, "display"), "latn");
  assert.equal(fontScript("Заголовок", manifest, "display"), "cyrl");
  assert.equal(fontScript("Mixed текст", manifest, "display"), "cyrl");
  assert.equal(fontScript("Mixed текст", manifest, "body"), "latn");
  assert.equal(fontScript("Mixed Ελληνικά", manifest, "body"), "grek");
  assert.equal(fontScript("123 …", manifest, "display"), null);
});

test("unsupported headings and mono text use one declared stack", () => {
  assert.match(fontStack(manifest, "display", "latn"), /^"Bricolage Grotesque"/);
  assert.match(fontStack(manifest, "display", "cyrl"), /^"IBM Plex Sans"/);
  assert.match(fontStack(manifest, "display", "grek"), /^system-ui/);
  assert.match(fontStack(manifest, "body", "cyrl"), /^"IBM Plex Sans"/);
  assert.match(fontStack(manifest, "mono", "grek"), /^ui-monospace/);
  const rules = fontRules(manifest);
  assert.ok(rules.some((rule) => rule.includes('h1[data-font-script="cyrl"]')
    && rule.includes('font-family: "IBM Plex Sans"') && !rule.includes("Bricolage")));
  assert.ok(rules.some((rule) => rule.includes('code[data-font-script="grek"]')
    && rule.includes("font-family: ui-monospace")));
  assert.doesNotMatch(rules.join("\n"), /:lang|\[lang/i);
});

test("shared elements receive script attributes and stylesheet rules", () => {
  const nodes = new Map();
  const node = (text) => ({
    textContent: text,
    attributes: new Map(),
    setAttribute(key, value) { this.attributes.set(key, value); },
    removeAttribute(key) { this.attributes.delete(key); },
  });
  nodes.set(manifest.roles.display.selector, [node("Заголовок"), node("123")]);
  nodes.set(manifest.roles.mono.selector, [node("κώδικας")]);
  const root = { querySelectorAll: (selector) => nodes.get(selector) ?? [] };
  refreshSiteFonts(root, manifest);
  assert.equal(nodes.get(manifest.roles.display.selector)[0].attributes.get("data-font-script"), "cyrl");
  assert.equal(nodes.get(manifest.roles.display.selector)[1].attributes.has("data-font-script"), false);
  assert.equal(nodes.get(manifest.roles.mono.selector)[0].attributes.get("data-font-script"), "grek");
  const inserted = [];
  const sheet = { href: "https://site.test/assets/0.0.0/site.css", cssRules: [],
    insertRule(rule, at) { inserted.push([rule, at]); this.cssRules.push(rule); } };
  assert.equal(installFontRules({ baseURI: "https://site.test/", styleSheets: [sheet] }, fontRules(manifest)), true);
  assert.equal(inserted.length, fontRules(manifest).length);
  assert.equal(installFontRules({ baseURI: "https://site.test/", styleSheets: [] }, []), false);
});

test("startup loads metadata and follows later element text", async () => {
  const { FONT_MANIFEST_URL, installSiteFonts } = await import("../js/site-fonts.js");
  const heading = {
    textContent: "Заголовок",
    attributes: new Map(),
    setAttribute(key, value) { this.attributes.set(key, value); },
    removeAttribute(key) { this.attributes.delete(key); },
  };
  const inserted = [];
  const sheet = { href: "https://site.test/assets/0.0.0/site.css", cssRules: [],
    insertRule(rule) { inserted.push(rule); this.cssRules.push(rule); } };
  const observers = [];
  class Observer {
    constructor(callback) { this.callback = callback; observers.push(this); }
    observe(target, options) { Object.assign(this, { target, options }); }
  }
  const root = {
    baseURI: "https://site.test/",
    styleSheets: [sheet],
    body: {},
    defaultView: { MutationObserver: Observer },
    querySelectorAll: (selector) => selector === manifest.roles.display.selector ? [heading] : [],
  };
  const requests = [];
  const loaded = await installSiteFonts(root, async (url, options) => {
    requests.push([url, options]);
    return { ok: true, json: async () => manifest };
  });
  assert.equal(loaded, true);
  assert.deepEqual(requests, [[FONT_MANIFEST_URL.href, { credentials: "omit", cache: "no-cache" }]]);
  assert.equal(heading.attributes.get("data-font-script"), "cyrl");
  assert.equal(inserted.length, fontRules(manifest).length);
  assert.equal(observers.length, 1);
  assert.deepEqual(observers[0].options, { childList: true, subtree: true, characterData: true });
  heading.textContent = "A heading";
  observers[0].callback();
  assert.equal(heading.attributes.get("data-font-script"), "latn");
});
