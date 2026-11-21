import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { EditorRuntime } from "../js/editor-runtime.js";
import { checkSiteBuild, errorText, fillPage, setCatalog, siteBuildChanged, siteVersionKept,
  watchSiteBuild } from "../js/ui.js";
import { stubPage } from "./fixture.mjs";

const own = "a".repeat(64), next = "b".repeat(64);
const message = "The site was updated. Reload this page to continue.";

for (const [name, page, published, changed] of [
  ["same build", own, { id: own }, false],
  ["different build", own, { id: next }, true],
  ["unreadable identity", own, null, false],
  ["absent published identity", own, {}, false],
  ["absent page identity", undefined, { id: next }, false],
  ["invalid page identity", "old", { id: next }, false],
  ["invalid published identity", own, { id: "new" }, false],
]) {
  test(`${name}: update decision is ${changed}`, () => {
    assert.equal(siteBuildChanged(page, published), changed);
  });
}

for (const [name, version, published, kept] of [
  ["the published version", "2.0.0", { version: "2.0.0", kept: ["1.1.0", "1.0.0"] }, true],
  ["a kept version", "1.0.0", { version: "2.0.0", kept: ["1.1.0", "1.0.0"] }, true],
  ["a dropped version", "0.9.0", { version: "2.0.0", kept: ["1.1.0", "1.0.0"] }, false],
  ["a site that keeps none", "1.0.0", { version: "2.0.0", kept: [] }, false],
  ["a site without a kept list", "1.0.0", { version: "2.0.0" }, false],
  ["a site of the earlier form", "1.0.0", { kept: ["1.0.0"] }, false],
  ["an unreadable site", "1.0.0", null, false],
  ["a page of the earlier form", undefined, { version: "2.0.0", kept: ["1.0.0"] }, false],
]) {
  test(`${name}: kept decision is ${kept}`, () => {
    assert.equal(siteVersionKept(version, published), kept);
  });
}

function fixture(context, fetcher, identity = own, version = "1.0.0") {
  stubPage(context, "editor");
  context.after(() => setCatalog(null));
  const root = document, query = root.querySelector, create = root.createElement;
  const metas = { 'meta[name="site-build"]': identity, 'meta[name="site-version"]': version };
  root.querySelector = (selector) => selector in metas ? metas[selector] && { content: metas[selector] }
    : query(selector);
  root.baseURI = "https://site.test/subpath/editor";
  root.visibilityState = "visible";
  root.createElement = (tag) => {
    const node = create(tag);
    if (tag === "dialog") node.showModal = () => { node.open = true; };
    return node;
  };
  let reloads = 0;
  root.defaultView = { location: { reload: () => { reloads++; } }, listeners: {},
    addEventListener(type, listener, capture = false) {
      assert.equal(this.listeners[type], undefined);
      if (type === "error") assert.equal(capture, true);
      this.listeners[type] = listener;
    } };
  watchSiteBuild(root, fetcher);
  return { root, reloads: () => reloads, notice: () => root.body.querySelector("dialog") };
}

const response = (id, version, kept) => ({ ok: true, json: async () => ({ id, version, kept }) });
const settle = () => new Promise(setImmediate);
const resume = (root) => {
  root.visibilityState = "hidden";
  root.defaultView.listeners.visibilitychange();
  root.visibilityState = "visible";
  root.defaultView.listeners.visibilitychange();
};

const triggers = [
  ["a resumed tab", resume],
  ...["SCRIPT", "LINK", "IMG"].map((tagName) => [`a failed ${tagName.toLowerCase()} resource`,
    (root) => root.defaultView.listeners.error({ target: { tagName } })]),
  ["an uncaught error", (root) => root.defaultView.listeners.error({ target: root.defaultView })],
  ["an unhandled rejection", (root) => root.defaultView.listeners.unhandledrejection({ reason: new Error("failed") })],
  ["a displayed error", () => assert.equal(errorText(new Error("failed")), "failed")],
];

for (const [name, trigger] of triggers) {
  test(`${name} starts one build check`, async (context) => {
    let calls = 0;
    const { root, notice } = fixture(context, async () => { calls++; return response(next); });
    trigger(root);
    await settle();
    assert.equal(calls, 1);
    assert.ok(notice());
  });
}

const failures = triggers.slice(1);

for (const [state, published] of [
  ["the published version", ["1.0.0", ["0.9.0"]]],
  ["a kept version", ["2.0.0", ["1.1.0", "1.0.0"]]],
]) {
  test(`a resumed tab of ${state} stays silent after an update`, async (context) => {
    let calls = 0;
    const { root, notice } = fixture(context, async () => { calls++; return response(next, ...published); });
    resume(root);
    await settle();
    resume(root);
    await settle();
    assert.equal(calls, 2);
    assert.equal(notice(), null);
  });

  for (const [name, trigger] of failures) {
    test(`${name} on a page of ${state} opens the notice after an update`, async (context) => {
      let calls = 0;
      const { root, notice } = fixture(context, async () => { calls++; return response(next, ...published); });
      resume(root);
      await settle();
      assert.equal(notice(), null);
      trigger(root);
      await settle();
      assert.equal(calls, 2);
      assert.equal(notice().open, true);
    });
  }
}

for (const [name, version, published] of [
  ["a dropped version", "1.0.0", ["4.0.0", ["3.0.0", "2.0.0"]]],
  ["a version of a site that keeps none", "1.0.0", ["2.0.0", []]],
  ["a page that names no version", null, ["2.0.0", ["1.0.0"]]],
  ["a site of the earlier form", "1.0.0", []],
]) {
  for (const [trigger, run] of [triggers[0], triggers.at(-1)]) {
    test(`${trigger} on ${name} opens the notice after an update`, async (context) => {
      const { root, notice } = fixture(context, async () => response(next, ...published), own, version);
      run(root);
      await settle();
      assert.equal(notice().open, true);
    });
  }
}

test("an unchanged build stays silent whatever the published versions are", async (context) => {
  const { root, notice } = fixture(context, async () => response(own, "4.0.0", []));
  resume(root);
  await settle();
  for (const [, trigger] of failures) trigger(root);
  await settle();
  assert.equal(notice(), null);
});

test("a failure during the check of a resumed tab opens the notice with one request", async (context) => {
  let calls = 0, finish;
  const { root, notice } = fixture(context, () => {
    calls++;
    return new Promise((resolve) => { finish = resolve; });
  });
  resume(root);
  await Promise.resolve();
  const joined = checkSiteBuild(root);
  finish(response(next, "2.0.0", ["1.0.0"]));
  await joined;
  assert.equal(calls, 1);
  assert.equal(notice().open, true);
});

test("a failure without an update does not mark the next resumed tab", async (context) => {
  const published = [response(own, "1.0.0", []), response(next, "2.0.0", ["1.0.0"])];
  const { root, notice } = fixture(context, async () => published.shift());
  await checkSiteBuild(root);
  resume(root);
  await settle();
  assert.equal(published.length, 0);
  assert.equal(notice(), null);
});

test("a resumed tab reads the identity without cache and prompts only once", async (context) => {
  const calls = [];
  const { root, notice, reloads } = fixture(context, async (...args) => {
    calls.push(args);
    return response(next);
  });
  watchSiteBuild(root, () => assert.fail("watcher already installed"));
  assert.equal(root.listeners.error, undefined);
  root.defaultView.listeners.visibilitychange();
  await settle();
  assert.equal(calls.length, 0);
  root.visibilityState = "hidden";
  root.defaultView.listeners.visibilitychange();
  await settle();
  assert.equal(calls.length, 0);
  root.visibilityState = "visible";
  root.defaultView.listeners.visibilitychange();
  await settle();
  assert.deepEqual(calls, [["https://site.test/subpath/site-build.json", { cache: "no-store", credentials: "omit" }]]);
  assert.equal(notice().open, true);
  assert.equal(notice().getAttribute("role"), "alertdialog");
  const [text, button] = notice().children;
  assert.equal(notice().getAttribute("aria-labelledby"), text.id);
  assert.equal(text.textContent, message);
  assert.equal(button.textContent, "Reload");
  assert.equal(notice().querySelectorAll("button").length, 1);
  assert.equal(notice().querySelectorAll("[style]").length, 0);
  assert.equal(notice().className, "dex-dialog");
  assert.equal(button.className, "btn primary");
  let prevented = false;
  notice().listeners.cancel({ preventDefault: () => { prevented = true; } });
  assert.equal(prevented, true);
  button.listeners.click();
  assert.equal(reloads(), 1);
  for (const [, trigger] of triggers) trigger(root);
  await settle();
  assert.equal(root.body.querySelectorAll("dialog").length, 1);
  assert.equal(calls.length, 1);
});

test("a notice uses the site catalog and survives language changes", async (context) => {
  const catalog = Object.fromEntries([...readFileSync(new URL("../i18n/ru.po", import.meta.url), "utf8")
    .matchAll(/^msgid (".*")\nmsgstr (".*")$/gm)].map(([, key, text]) => [JSON.parse(key), JSON.parse(text)]));
  const { root, notice } = fixture(context, async () => response(next));
  setCatalog(catalog);
  await checkSiteBuild(root);
  assert.equal(notice().children[0].textContent, "Сайт обновился. Перезагрузите страницу, чтобы продолжить.");
  assert.equal(notice().children[1].textContent, "Перезагрузить");
  setCatalog(null);
  fillPage(notice());
  assert.equal(notice().children[0].textContent, message);
  assert.equal(notice().children[1].textContent, "Reload");
  assert.equal(notice().open, true);
});

for (const [name, read] of [
  ["same build", async () => response(own)],
  ["offline", async () => { throw new Error("offline"); }],
  ["synchronous fetch error", () => { throw new Error("unavailable"); }],
  ["error status", async () => ({ ok: false, json: () => assert.fail("unreadable response") })],
  ["invalid JSON", async () => ({ ok: true, json: async () => { throw new Error("invalid JSON"); } })],
  ["missing identity", async () => response(undefined)],
]) {
  test(`${name}: remains silent and retries at the next trigger`, async (context) => {
    let calls = 0;
    const { root, notice } = fixture(context, () => ++calls === 1 ? read() : response(next));
    await checkSiteBuild(root);
    assert.equal(notice(), null);
    assert.equal(calls, 1);
    await checkSiteBuild(root);
    assert.equal(calls, 2);
    assert.ok(notice());
  });
}

test("old pages and unassembled sources do not check or prompt", async (context) => {
  const { root, notice } = fixture(context, () => assert.fail("no identity request"), null);
  assert.deepEqual(root.defaultView.listeners, {});
  await checkSiteBuild(root);
  assert.equal(notice(), null);
});

test("simultaneous triggers share one request", async (context) => {
  let calls = 0, finish;
  const { root, notice } = fixture(context, () => {
    calls++;
    return new Promise((resolve) => { finish = resolve; });
  });
  for (const [, trigger] of triggers) trigger(root);
  const first = checkSiteBuild(root);
  assert.equal(checkSiteBuild(root), first);
  await Promise.resolve();
  assert.equal(calls, 1);
  finish(response(next));
  await first;
  assert.equal(calls, 1);
  assert.ok(notice());
});

test("an editor worker error checks using already loaded page code", async (context) => {
  let calls = 0;
  const { notice } = fixture(context, async () => { calls++; return response(next); });
  const saved = globalThis.Worker;
  context.after(() => { globalThis.Worker = saved; });
  globalThis.Worker = class {};
  const runtime = new EditorRuntime();
  runtime.ensure();
  runtime.worker.onerror({ message: "asset directory removed" });
  await settle();
  assert.equal(calls, 1);
  assert.ok(notice());
});
