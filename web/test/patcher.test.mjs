import assert from "node:assert/strict";

import { readFileSync } from "node:fs";

import { test } from "node:test";

import { formatName, lacksChecksums, patchSource, patchStep, patchSummary, resultName, sizeLabel } from "../js/patch.js";

test("sizes read in MiB and KiB", () => {
  assert.equal(sizeLabel(33554432), "32 MiB");
  assert.equal(sizeLabel(1332576), "1.3 MiB");
  assert.equal(sizeLabel(131072), "128 KiB");
  assert.equal(sizeLabel(12), "12 bytes");
});

test("patcher step titles shrink and chips wrap on phone screens", () => {
  const css = readFileSync(new URL("../site.css", import.meta.url), "utf8");
  assert.match(css, /^\.step header \{ display: flex; align-items: center; gap: 12px; flex-wrap: wrap; \}/m);
  assert.match(css, /^\.step header \.step-title \{ flex: 1 1 auto; min-width: 0; font-size: 18px; line-height: 1\.25; \}/m);
  assert.match(css, /@media \(max-width: 480px\) \{\n  \.step header \.step-title \{ flex-basis: calc\(100% - 38px\); \}\n  \.step header \.chip \{ margin-left: 38px; \}\n\}/);
});

test("patching and UPS downloads occupy sibling panes below the shared selectors", () => {
  const html = readFileSync(new URL("../patch.html", import.meta.url), "utf8");
  const panes = [...html.matchAll(/<article class="patch-pane" id="([^"]+)"[^>]*>([^]*?)<\/article>/g)];
  assert.deepEqual(panes.map((match) => match[1]), ["translation", "patch-here", "patch-download"]);
  const [, translation, patching, download] = [null, ...panes.map((match) => match[2])];
  for (const id of ["game", "language", "release", "release-summary", "release-notes-list"]) {
    assert.match(translation, new RegExp(`id="${id}"`));
  }
  assert.doesNotMatch(translation, /class="n"|\bdownload\b|class="step/);
  assert.match(patching, /id="rom-drop"/);
  assert.match(patching, /id="apply"/);
  assert.match(patching, /id="download"/);
  assert.match(patching, /id="result-name"/);
  assert.doesNotMatch(patching, /id="patch-link"/);
  assert.deepEqual([...patching.matchAll(/<header><span class="n"[^>]*>(\d+)<\/span>/g)].map((m) => m[1]), ["1", "1", "2"]);
  assert.match(download, /id="patch-link"[^>]*download/);
  assert.doesNotMatch(download, /class="n"|class="step/);
  assert.match(html, /<section class="patch-options"[^>]*>\s*<article[^]*?<\/article>\s*<article[^]*?<\/article>\s*<\/section>/);
  assert.doesNotMatch(html, /id="release-chip"|id="game-name"|class="eyebrow"/);
  assert.doesNotMatch(html, /\bstyle=|<style\b/);
});

test("patcher placeholders reuse final markup and cannot match another page", () => {
  const html = readFileSync(new URL("../patch.html", import.meta.url), "utf8");
  assert.match(html, /<body class="patcher-page">/);
  assert.match(html, /<p class="summary script-placeholder loading-copy" id="release-summary-placeholder"/);
  assert.match(html, /<div class="field script-placeholder loading-copy" id="release-notes-loading"/);
  assert.match(html, /<span class="small mono" id="rom-expected"[^>]*data-loading/);
  for (const name of ["index.html", "pick.html", "save.html", "guide.html"]) {
    assert.doesNotMatch(readFileSync(new URL(`../${name}`, import.meta.url), "utf8"), /<body class="patcher-page">/, name);
  }
  const css = readFileSync(new URL("../site.css", import.meta.url), "utf8");
  const start = css.indexOf("body.patcher-page .script-placeholder"), end = css.indexOf("body.save-page .script-placeholder", start);
  const rules = css.slice(start, end).split("\n").filter((line) => line.includes("{"));
  assert.ok(rules.length > 0);
  for (const line of rules) assert.match(line.trim(), /^(?:html\[data-scripted\] )?body\.patcher-page /, line);
  assert.doesNotMatch(css.slice(start, end), /min-height|data-reserve/);
});

test("patch choices stack at desktop and phone widths and wrap long download names", () => {
  const css = readFileSync(new URL("../site.css", import.meta.url), "utf8");
  assert.match(css, /\.patcher, \.patch-options, \.steps \{ display: grid; grid-template-columns: minmax\(0, 1fr\); gap: 12px; \}/);
  assert.match(css, /\.patch-pane, \.step \{[^}]*min-width: 0;/);
  assert.match(css, /\.patcher \.btn \{ max-width: 100%; overflow-wrap: anywhere; min-width: 0; \}/);
  assert.match(css, /@media \(max-width: 640px\) \{ \.choice \{ grid-template-columns: 1fr; \} \}/);
});

import { fileOf, identifiedPatch, patchFixture, releaseFixture, xdeltaNames, xdeltaPatch } from "./patch-fixture.mjs";
import { PATCH_ERRORS, patchEngine, patchError, ROM_LIMIT } from "../js/patch-engine.js";
import { digest, reverseRelease } from "../js/patcher-core.js";
import { handlePatchMessage } from "../js/patcher-worker.js";

const noFetch = () => assert.fail("Local patches must not fetch files");

// module format, shown name, file summary, checksum-free, result confirmed
const FORMATS = [
  ["UPS", "UPS", "Format: UPS · Original: 3 bytes · Result: 3 bytes", false, true],
  ["IPS", "IPS", "Format: IPS", true, false],
  ["BPS", "BPS", "Format: BPS · Original: 3 bytes · Result: 3 bytes", false, true],
  ["VCDIFF", "xdelta", "Format: xdelta · Result: 3 bytes", false, true],
  ["APS-GBA", "APS (GBA)", "Format: APS (GBA) · Original: 3 bytes · Result: 3 bytes", false, true],
  ["APS-N64", "APS (N64)", "Format: APS (N64) · Result: 3 bytes", true, false],
  ["PPF", "PPF", "Format: PPF", true, false],
  ["EBP", "EBP", "Format: EBP", true, false],
];
const NOTE = "{format} patches carry no checksum of the result, so the result cannot be confirmed.";
const CONFIRMED = "Result confirmed by the checksum in the patch.";
const UNCONFIRMED = "Result not confirmed: the patch carries no checksum of the whole result.";
// BLAKE3 of the fixture's source and result
const SOURCE_DIGEST = "b177ec1bf26dfb3b7010d473e6d44713b29b765b99c6e60ecbfae742de496543";
const RESULT_DIGEST = "ce0f0bb1026da55f5c550317d92292db6adcbe4a4a07ee51128021c3250f0702";

function russianCatalog() {
  const po = readFileSync(new URL("../i18n/ru.po", import.meta.url), "utf8");
  const value = (block, field) => block.match(new RegExp(`^${field} ("[^\\n]*"(?:\\n"[^\\n]*")*)`, "m"))?.[1]
    .split("\n").map((text) => JSON.parse(text)).join("");
  return Object.fromEntries(po.split("\n\n").map((block) => [value(block, "msgid"), value(block, "msgstr")]));
}

async function messages(data, fetcher = noFetch, origin = "https://site.test") {
  const replies = [], transfers = [];
  await handlePatchMessage(data, (message, transfer) => { replies.push(message); transfers.push(transfer); }, fetcher, origin);
  return { replies, transfers, last: replies.at(-1) };
}

test("a chosen game opens translations and an empty site offers only local patches", () => {
  assert.equal(patchSource(true, { id: "game" }), "translation");
  assert.equal(patchSource(true, null), "own");
  assert.equal(patchSource(true, { id: "game" }, "own"), "own");
  assert.equal(patchSource(true, null, "translation"), "translation");
  assert.equal(patchSource(false, { id: "game" }, "translation"), "own");
  assert.equal(patchSource(false, null), "own");
});

test("own downloads combine the patch stem with the ROM extension", () => {
  for (const [rom, patch, name] of [["game.gba", "translation.ups", "translation.gba"],
    ["game.NDS", "my.patch.bps", "my.patch.NDS"], ["game", "patch.ips", "patch"],
    [".rom", "patch", "patch"], ["game.sfc", ".patch", ".patch.sfc"]]) {
    assert.equal(resultName(rom, patch), name);
  }
});

test("patch facts omit unstated sizes and preserve zero sizes", () => {
  assert.equal(patchSummary(null), "");
  assert.equal(patchSummary({ format: "IPS", sourceSize: null, resultSize: null }), "Format: IPS");
  assert.equal(patchSummary({ format: "IPS", sourceSize: null, resultSize: 0 }), "Format: IPS · Result: 0 bytes");
  assert.equal(patchSummary({ format: "BPS", sourceSize: 3, resultSize: 4 }), "Format: BPS · Original: 3 bytes · Result: 4 bytes");
});

test("every format the module reports has a shown name and a checksum state", () => {
  for (const [format, name, , unchecked] of FORMATS) {
    assert.equal(formatName(format), name);
    assert.equal(lacksChecksums(format), unchecked, format);
    assert.equal(patchSummary({ format, sourceSize: null, resultSize: null }), `Format: ${name}`);
  }
  assert.equal(lacksChecksums(undefined), false);
});

test("a recognised patch shows its chosen file and stated sizes", () => {
  const file = { name: "translation.bps" };
  assert.deepEqual(patchStep(file, { format: "BPS", sourceSize: 3, resultSize: 4 }), {
    file, meta: "Format: BPS · Original: 3 bytes · Result: 4 bytes", problem: "", mode: "done",
  });
});

test("a refused patch shows its reason and leaves the step active without a chosen file", () => {
  for (const error of [patchError({ code: "unknown_format" }),
    patchError({ code: "truncated_patch", offset: 4, needed: 5, available: 2 }),
    patchError({ code: "patch_checksum", expected: 255, actual: 16 })]) {
    assert.deepEqual(patchStep({ name: "invalid.patch" }, null, error), {
      file: null, meta: "", problem: error.message, mode: "active",
    });
  }
});

test("a replacement patch stays unchosen until its own inspection succeeds", () => {
  const first = { name: "first.ups" }, replacement = { name: "replacement.ips" };
  const before = patchStep(first, { format: "UPS", sourceSize: 3, resultSize: 4 });
  assert.deepEqual(patchStep(replacement, null), { file: null, meta: "", problem: "", mode: "active" });
  assert.deepEqual(patchStep(replacement, { format: "IPS", sourceSize: null, resultSize: null }), {
    file: replacement, meta: "Format: IPS", problem: "", mode: "done",
  });
  assert.equal(before.file, first);
});

for (const [format, , summary, , verified] of FORMATS) {
  test(`${format}: the worker inspects and applies local files without downloading`, async () => {
    const { source, result, patch } = patchFixture(format);
    const patchFile = fileOf(patch, "custom.patch");
    const inspected = await messages({ patchFile, inspect: true });
    assert.equal(inspected.replies.length, 1);
    assert.equal(inspected.last.info.format, format);
    assert.equal(inspected.last.info.sourceSize, summary.includes("Original") ? source.length : null);
    assert.equal(inspected.last.info.resultSize, summary.includes("Result") ? result.length : null);
    const applied = await messages({ file: fileOf(source), patchFile });
    assert.equal(applied.replies[0].status, "Applying the patch…");
    assert.deepEqual(new Uint8Array(applied.last.result), result);
    assert.equal(applied.last.size, result.length);
    assert.equal(applied.last.sha1, await digest(result));
    assert.equal(applied.last.verified, verified);
    assert.deepEqual(applied.transfers.at(-1), [applied.last.result]);
  });
}

test("an xdelta result is confirmed by window checksums or by a stated digest", async () => {
  const { source } = patchFixture();
  const verified = async (patch) => (await messages({ file: fileOf(source), patchFile: fileOf(patch) })).last.verified;
  assert.equal(await verified(xdeltaPatch()), true);
  assert.equal(await verified(xdeltaPatch({ checked: false })), false);
  assert.equal(await verified(xdeltaPatch({ checked: false,
    header: xdeltaNames(`out#${RESULT_DIGEST}`, `in#${SOURCE_DIGEST}`) })), true);
});

test("the release worker keeps its status sequence, download policy and result transfer", async () => {
  const { source, result, patch, release } = await releaseFixture();
  const applied = await messages({ file: fileOf(source), release, patchURL: "https://site.test/release.ups" }, (url, options) => {
    assert.equal(url.href, "https://site.test/release.ups");
    assert.deepEqual(options, { credentials: "omit", redirect: "error", cache: "no-store" });
    return new Response(patch);
  });
  assert.deepEqual(applied.replies.slice(0, -1), [
    { status: "ROM verified. Downloading the patch…" }, { status: "Applying the patch and verifying the result…" },
  ]);
  assert.deepEqual(new Uint8Array(applied.last.result), result);
  assert.deepEqual(Object.keys(applied.last), ["result"]);
  assert.deepEqual(applied.transfers.at(-1), [applied.last.result]);
  assert.deepEqual(await reverseRelease(result, patch, release), source);
});

test("a translation release takes a UPS patch alone", async () => {
  const { source, release } = await releaseFixture();
  for (const [format] of FORMATS.filter(([format]) => format !== "UPS")) {
    const { patch } = patchFixture(format);
    const row = { ...release, patch_size: patch.length, patch_sha256: await digest(patch, "SHA-256") };
    const { last } = await messages({ file: fileOf(source), release: row, patchURL: "https://site.test/release.ups" },
      () => new Response(patch));
    assert.equal(last.key, "Not a UPS patch.", format);
    assert.equal(last.result, undefined, format);
  }
});

test("release refusals preserve every declared hash and size check", async () => {
  const { source, patch, release } = await releaseFixture();
  const reasons = {
    source_size: "Wrong ROM size. Choose the original ROM listed above.",
    source_sha1: "Wrong ROM SHA-1: {sha1}. Choose the original ROM listed above.",
    patch_size: "Patch download differs from the release. Reload and try again.",
    patch_sha256: "Patch download differs from the release. Reload and try again.",
    result_size: "UPS output size differs from the release.",
    result_sha1: "Patched ROM differs from the released result.",
  };
  for (const [field, key] of Object.entries(reasons)) {
    const changed = { ...release, [field]: field.endsWith("size") ? release[field] + 1 : "0".repeat(release[field].length) };
    const { last } = await messages({ file: fileOf(source), release: changed, patchURL: "https://site.test/patch.ups" },
      field.startsWith("source") ? noFetch : () => new Response(patch));
    assert.equal(last.key, key, field);
    assert.equal(last.result, undefined);
  }
  const cross = await messages({ file: fileOf(source), release, patchURL: "https://elsewhere.test/patch.ups" });
  assert.equal(cross.last.key, "Patch must come from this site.");
  const failed = await messages({ file: fileOf(source), release, patchURL: "https://site.test/patch.ups" },
    () => new Response(null, { status: 404 }));
  assert.equal(failed.last.key, "Patch download failed ({status}).");
  assert.deepEqual(failed.last.vars, { status: 404 });
});

test("local worker refuses oversized ROMs before reading and returns catalog errors", async () => {
  const { patch } = patchFixture();
  const { last } = await messages({ file: { size: ROM_LIMIT + 1, arrayBuffer: noFetch }, patchFile: fileOf(patch) });
  assert.equal(last.key, "ROM has {size} bytes; the page limit is {limit} bytes.");
  assert.deepEqual(last.vars, { size: ROM_LIMIT + 1, limit: ROM_LIMIT });
  const unknown = await messages({ patchFile: fileOf(new Uint8Array([1, 2, 3])), inspect: true });
  assert.equal(unknown.last.key, PATCH_ERRORS.unknown_format);
});

test("engine error codes have separate translated catalog messages and numeric context", () => {
  const codes = ["unknown_format", "unsupported_revert", "unsupported_compressor", "unsupported_code_table",
    "truncated_patch", "malformed_patch", "patch_checksum", "source_size", "source_checksum", "source_mismatch",
    "source_digest", "already_patched", "already_original", "result_checksum", "result_digest", "limit_exceeded",
    "out_of_bounds", "allocation_failed", "invalid_argument"];
  assert.deepEqual(Object.keys(PATCH_ERRORS).sort(), codes.sort());
  assert.equal(new Set(Object.values(PATCH_ERRORS)).size, codes.length);
  const catalog = russianCatalog();
  for (const key of [NOTE, CONFIRMED, UNCONFIRMED]) assert.ok(catalog[key], key);
  assert.match(catalog[NOTE], /\{format\}/);
  for (const [code, key] of Object.entries(PATCH_ERRORS)) {
    assert.ok(catalog[key], code);
    const failure = patchError({ code, expected: 255, actual: 16, size: 3, limit: 2, offset: 4, needed: 5, available: 6, length: 7, checksum: 32 });
    assert.equal(failure.key, key);
    for (const [, name] of key.matchAll(/\{(\w+)\}/g)) assert.ok(name in failure.vars, `${code}: ${name}`);
  }
  assert.deepEqual(patchError({ code: "patch_checksum", expected: 255, actual: 16 }).vars,
    { expected: "000000ff", actual: "00000010" });
});

test("the added refusals read as fixed texts with their values", () => {
  for (const [error, text] of [
    [{ code: "unknown_format" }, "This is not a UPS, IPS, BPS, xdelta, APS or PPF patch."],
    [{ code: "unsupported_compressor", compressor: 16 }, "This xdelta patch is packed with a method this page cannot read."],
    [{ code: "unsupported_code_table" }, "This xdelta patch carries its own code table, which this page cannot read."],
    [{ code: "source_mismatch", offset: 16, length: 8 }, "The patch was made for another ROM: the data at offset 16 differs."],
    [{ code: "source_digest" }, "The patch was made for another ROM: its BLAKE3 digest differs from the one in the patch."],
    [{ code: "result_digest" }, "The patch is damaged: the result differs from the BLAKE3 digest in the patch."],
    [{ code: "source_checksum", expected: 3452, actual: 16318 },
      "The patch was made for another ROM: expected checksum 00000d7c, got 00003fbe."],
    [{ code: "result_checksum", expected: 1114123, actual: 3604506 },
      "The result does not match the checksum in the patch (expected 0011000b, got 0037001a): the ROM is another one or the patch is damaged."],
    [{ code: "source_size", expected: 255, actual: 16 }, "The patch was made for another ROM: expected 255 bytes, got 16."],
    [{ code: "unsupported_revert" }, "Only UPS patches can recover the original ROM."],
  ]) {
    assert.equal(patchError(Object.assign(new Error("diagnostic"), error)).message, text);
  }
  for (const code of ["source_digest", "result_digest", "unsupported_code_table"]) {
    assert.deepEqual(patchError(Object.assign(new Error("diagnostic"), { code })).vars, {});
  }
});

test("the patch engine instance is reused across calls", async () => {
  assert.equal(await patchEngine(), await patchEngine());
});

test("only the patch page's CSP needs WebAssembly permission", () => {
  const patch = readFileSync(new URL("../patch.html", import.meta.url), "utf8");
  assert.match(patch, /script-src 'self' 'wasm-unsafe-eval';/);
  assert.doesNotMatch(patch, /'unsafe-eval'|'unsafe-inline'/);
  assert.doesNotMatch(readFileSync(new URL("../editor.html", import.meta.url), "utf8"), /Content-Security-Policy/);
});

import { crc32 } from "node:zlib";
import { stubPage } from "./fixture.mjs";

function oversizedPatch(source) {
  function integer(value) {
    const bytes = [];
    while (value >= 128) {
      bytes.push(value & 127);
      value = Math.floor(value / 128) - 1;
    }
    return [...bytes, value | 128];
  }
  const body = [...Buffer.from("UPS1"), ...integer(source.length), ...integer(ROM_LIMIT + 1)];
  const patch = new Uint8Array(body.length + 12);
  patch.set(body);
  const trailer = new DataView(patch.buffer, body.length);
  trailer.setUint32(0, crc32(source), true);
  trailer.setUint32(8, crc32(patch.subarray(0, -4)), true);
  return patch;
}

test("local worker passes the page result limit and preserves engine refusal values", async () => {
  const { source, result, patch } = patchFixture();
  const oversized = await messages({ file: fileOf(source), patchFile: fileOf(oversizedPatch(source)) });
  assert.equal(oversized.last.key, PATCH_ERRORS.limit_exceeded);
  assert.deepEqual(oversized.last.vars, { size: ROM_LIMIT + 1, limit: ROM_LIMIT });
  const already = await messages({ file: fileOf(result), patchFile: fileOf(patch) });
  assert.equal(already.last.key, PATCH_ERRORS.already_patched);
  assert.deepEqual(already.last.vars, { size: result.length, checksum: crc32(result).toString(16).padStart(8, "0") });
});

async function patchPage(context) {
  const page = stubPage(context, "patch", "", readFileSync(new URL("../patch.html", import.meta.url), "utf8"));
  const source = readFileSync(new URL("../js/patch.js", import.meta.url), "utf8")
    .replace(/from "(\.\/[^\"]+)"/g, (_, path) => `from "${new URL(`../js/${path.slice(2)}`, import.meta.url).href}"`)
    .replaceAll("import.meta.url", JSON.stringify(new URL("../js/patch.js", import.meta.url).href))
    .replace(/if \(typeof document !== "undefined"[^\n]+main\(\);/,
      "export { state, chooseSource, checkPatch, checkFile, apply, stopWork, relabel, wire };");
  const patcher = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}#${encodeURIComponent(context.name)}`);
  const workers = [];
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "Worker");
  Object.defineProperty(globalThis, "Worker", { configurable: true, value: class {
    constructor(url, options) {
      assert.ok(url.href.endsWith("/js/patcher-worker.js"));
      assert.deepEqual(options, { type: "module" });
      workers.push(this);
    }
    postMessage(message) { this.message = message; }
    terminate() { this.terminated = true; }
    finish() { return handlePatchMessage(this.message, (data) => this.onmessage({ data }), noFetch); }
  } });
  page.$("download").focus = () => {};
  patcher.wire();
  patcher.chooseSource("own");
  context.after(() => {
    patcher.state.worker?.terminate();
    if (patcher.state.url) URL.revokeObjectURL(patcher.state.url);
    if (descriptor) Object.defineProperty(globalThis, "Worker", descriptor);
    else delete globalThis.Worker;
  });
  return { ...page, ...patcher, worker: () => workers.at(-1) };
}

test("own patch controls share the ROM steps and keep completed sizes and hashes", async (context) => {
  const page = await patchPage(context), { $, state } = page;
  let compact = false;
  $("patch-drop").classList.toggle = (name, value) => { if (name === "compact") compact = value; };
  for (const kind of ["rom", "patch"]) {
    let picked = false;
    $(`${kind}-file`).click = () => { picked = true; };
    $(`${kind}-file-card`).querySelector("button").listeners.click();
    assert.equal(picked, true);
  }
  assert.equal($("translation").hidden, true);
  assert.equal($("source-choice").hidden, true);
  assert.equal($("load-error").hidden, true);
  assert.equal($("rom-file").getAttribute("accept"), null);
  assert.equal($("rom-step-number").textContent, "2");
  assert.equal($("apply-step-number").textContent, "3");
  const { source, patch, result } = patchFixture("IPS");
  page.checkPatch(fileOf(patch, "custom.ips"));
  assert.equal($("apply").disabled, true);
  await page.worker().finish();
  assert.equal($("patch-file-card").hidden, false);
  assert.equal($("patch-file-card").querySelector(".n").textContent, "custom.ips");
  assert.equal($("patch-file-card").querySelector(".m").textContent, "Format: IPS");
  assert.equal($("patch-drop-title").textContent, "Drop another file here");
  assert.equal(compact, true);
  assert.equal($("unverified-note").hidden, false);
  assert.equal($("unverified-note").textContent, NOTE.replace("{format}", "IPS"));
  await page.checkFile(fileOf(source, "original.SFC"));
  assert.equal($("apply").disabled, false);
  page.apply();
  await page.worker().finish();
  assert.equal($("download").download, "custom.SFC");
  assert.equal($("result-name").textContent, "custom.SFC · 3 bytes");
  assert.equal($("result-sha1").textContent, await digest(result));
  assert.equal($("download").hidden, false);
  const url = state.url;
  page.relabel();
  assert.equal(state.url, url);
  assert.equal($("result-name").textContent, "custom.SFC · 3 bytes");
  page.checkPatch(fileOf(patch, "other.ips"));
  assert.equal($("download").hidden, true);
  assert.equal($("patch-file-card").hidden, true);
  assert.equal($("patch-drop-title").textContent, "Drop your patch here");
  assert.equal(compact, false);
  assert.equal(state.result, null);
  await page.worker().finish();
  assert.equal($("patch-file-card").querySelector(".n").textContent, "other.ips");
  assert.equal(compact, true);
});

test("patch inspection refusals stay beside the input and clear on replacement", async (context) => {
  const page = await patchPage(context), { $, state } = page;
  const { patch } = patchFixture();
  const damaged = patch.slice();
  damaged[damaged.length - 1] ^= 1;
  for (const bytes of [new Uint8Array([1, 2, 3]), patch.slice(0, 6), damaged]) {
    page.checkPatch(fileOf(bytes, "invalid.patch"));
    await page.worker().finish();
    assert.equal(state.patchFile, null);
    assert.equal(state.patchInfo, null);
    assert.equal($("patch-file-card").hidden, true);
    assert.equal($("patch-error").hidden, false);
    assert.match($("patch-error").textContent, /not a UPS|damaged/);
    page.relabel();
    assert.equal($("patch-error").hidden, false);
    assert.equal($("status").textContent, "");
    assert.equal($("apply").disabled, true);
    await page.checkFile(fileOf(new Uint8Array([1, 2, 3])));
    assert.equal(state.worker, null);
    assert.equal($("patch-error").hidden, false);
  }
  page.checkPatch(fileOf(patch, "valid.ups"));
  assert.equal($("patch-error").hidden, true);
  await page.worker().finish();
  assert.equal($("patch-file-card").hidden, false);
  assert.equal($("apply").disabled, false);
});

test("Cancel terminates own patch work and ignores a late result before retrying", async (context) => {
  const page = await patchPage(context), { $ } = page;
  const { source, patch } = patchFixture();
  page.checkPatch(fileOf(patch));
  await page.worker().finish();
  await page.checkFile(fileOf(source));
  page.apply();
  const cancelled = page.worker();
  $("cancel").listeners.click();
  assert.equal(cancelled.terminated, true);
  await cancelled.finish();
  assert.equal($("download").hidden, true);
  assert.equal($("apply").disabled, false);
  page.apply();
  await page.worker().finish();
  assert.equal($("download").hidden, false);
});

test("cancelling inspection clears the patch and ignores its late metadata", async (context) => {
  const page = await patchPage(context), { $, state } = page;
  page.checkPatch(fileOf(patchFixture().patch));
  const cancelled = page.worker();
  $("cancel").listeners.click();
  await cancelled.finish();
  assert.equal(cancelled.terminated, true);
  assert.equal(state.patchFile, null);
  assert.equal(state.patchInfo, null);
  assert.equal($("apply").disabled, true);
  assert.equal($("status").textContent, "Cancelled. Choose a patch to continue.");
});

for (const [format, name, summary, unchecked, verified] of FORMATS) {
  test(`${name}: an own patch shows its format, its note and whether the result was confirmed`, async (context) => {
    const page = await patchPage(context), { $ } = page;
    const { source, result, patch } = patchFixture(format);
    page.checkPatch(fileOf(patch, "custom.patch"));
    await page.worker().finish();
    assert.equal($("patch-file-card").querySelector(".m").textContent, summary);
    assert.equal($("unverified-note").hidden, !unchecked);
    if (unchecked) assert.equal($("unverified-note").textContent, NOTE.replace("{format}", name));
    await page.checkFile(fileOf(source, "original.bin"));
    assert.equal($("status").textContent, "");
    page.apply();
    await page.worker().finish();
    assert.equal($("status").textContent, verified ? CONFIRMED : UNCONFIRMED);
    assert.equal($("status").className, "small");
    assert.equal($("download").download, "custom.bin");
    assert.equal($("result-sha1").textContent, await digest(result));
    page.relabel();
    assert.equal($("status").textContent, verified ? CONFIRMED : UNCONFIRMED);
    assert.equal($("unverified-note").hidden, !unchecked);
  });
}

test("the checksum note follows the chosen patch and leaves with the own patch source", async (context) => {
  const page = await patchPage(context), { $ } = page;
  const choose = async (format) => {
    page.checkPatch(fileOf(patchFixture(format).patch, "custom.patch"));
    assert.equal($("unverified-note").hidden, true);
    await page.worker().finish();
  };
  await choose("PPF");
  assert.equal($("unverified-note").textContent, NOTE.replace("{format}", "PPF"));
  await choose("APS-N64");
  assert.equal($("unverified-note").hidden, false);
  assert.equal($("unverified-note").textContent, NOTE.replace("{format}", "APS (N64)"));
  await choose("VCDIFF");
  assert.equal($("unverified-note").hidden, true);
  await choose("EBP");
  assert.equal($("unverified-note").hidden, false);
  page.state.games = [{ id: "game" }];
  page.chooseSource("translation");
  assert.equal($("unverified-note").hidden, true);
});

test("an xdelta patch without checksums gives an unconfirmed result and no note", async (context) => {
  const page = await patchPage(context), { $ } = page;
  const { source } = patchFixture();
  for (const [patch, line] of [[xdeltaPatch({ checked: false }), UNCONFIRMED],
    [xdeltaPatch({ checked: false, header: xdeltaNames(`out#${RESULT_DIGEST}`, `in#${SOURCE_DIGEST}`) }), CONFIRMED]]) {
    page.checkPatch(fileOf(patch, "custom.xdelta"));
    await page.worker().finish();
    assert.equal($("patch-file-card").querySelector(".m").textContent, "Format: xdelta · Result: 3 bytes");
    assert.equal($("unverified-note").hidden, true);
    await page.checkFile(fileOf(source));
    page.apply();
    await page.worker().finish();
    assert.equal($("status").textContent, line);
  }
});

test("the note and the result line follow the site language with the format name unchanged", async (context) => {
  const { fillPage, setCatalog, setUiLanguage } = await import("../js/ui.js");
  context.after(() => { setCatalog(null); setUiLanguage("en"); });
  const page = await patchPage(context), { $ } = page;
  const catalog = russianCatalog();
  const russian = () => { setCatalog(catalog); setUiLanguage("ru"); fillPage(); };
  for (const [format, name, line] of [["APS-N64", "APS (N64)", UNCONFIRMED], ["VCDIFF", "xdelta", CONFIRMED]]) {
    setCatalog(null);
    setUiLanguage("en");
    const { source, patch } = patchFixture(format);
    page.checkPatch(fileOf(patch, "custom.patch"));
    await page.worker().finish();
    await page.checkFile(fileOf(source));
    page.apply();
    await page.worker().finish();
    assert.equal($("status").textContent, line);
    russian();
    page.relabel();
    assert.equal($("status").textContent, catalog[line]);
    assert.equal($("patch-file-card").querySelector(".m").textContent.split(" · ")[0],
      catalog["Format: {format}"].replace("{format}", name));
    if (lacksChecksums(format)) assert.equal($("unverified-note").textContent, catalog[NOTE].replace("{format}", name));
  }
});

test("refused patches name the added reasons beside the patch input", async (context) => {
  const page = await patchPage(context), { $, state } = page;
  for (const [patch, text] of [
    [xdeltaPatch({ header: [1, 16], packed: 1 }), PATCH_ERRORS.unsupported_compressor],
    [xdeltaPatch({ header: [2, 3, 4, 3, 0] }), PATCH_ERRORS.unsupported_code_table],
    [new Uint8Array([1, 2, 3]), "This is not a UPS, IPS, BPS, xdelta, APS or PPF patch."],
  ]) {
    page.checkPatch(fileOf(patch, "custom.xdelta"));
    await page.worker().finish();
    assert.equal(state.patchInfo, null);
    assert.equal($("patch-error").hidden, false);
    assert.equal($("patch-error").textContent, text);
    assert.equal($("unverified-note").hidden, true);
  }
});

test("a wrong ROM or a damaged patch names the added reasons in the result step", async (context) => {
  const page = await patchPage(context), { $ } = page;
  const { source } = patchFixture(), other = new Uint8Array([9, 9, 9]);
  const identified = identifiedPatch(), swapped = identified.source.slice();
  swapped[0x12] ^= 1;
  for (const [patch, rom, text] of [
    [identified.patch, swapped, "The patch was made for another ROM: the data at offset 16 differs."],
    [xdeltaPatch({ header: xdeltaNames(`out#${RESULT_DIGEST}`, `in#${SOURCE_DIGEST}`) }), other, PATCH_ERRORS.source_digest],
    [xdeltaPatch({ header: xdeltaNames(`out#${"0".repeat(64)}`, "in") }), source, PATCH_ERRORS.result_digest],
    [xdeltaPatch(), other, "The result does not match the checksum in the patch (expected 0011000b, got 0037001a): the ROM is another one or the patch is damaged."],
    [patchFixture("APS-GBA").patch, other, "The patch was made for another ROM: expected checksum 00000d7c, got 00003fbe."],
  ]) {
    page.checkPatch(fileOf(patch, "custom.patch"));
    await page.worker().finish();
    await page.checkFile(fileOf(rom));
    page.apply();
    await page.worker().finish();
    assert.equal($("status").textContent, text);
    assert.equal($("status").className, "small note err");
    assert.equal($("download").hidden, true);
  }
  page.checkPatch(fileOf(identified.patch, "custom.aps"));
  await page.worker().finish();
  await page.checkFile(fileOf(identified.source));
  page.apply();
  await page.worker().finish();
  assert.equal($("status").textContent, UNCONFIRMED);
  assert.equal($("result-sha1").textContent, await digest(identified.result));
});
