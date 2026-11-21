import { readFile } from "node:fs/promises";
import { crc32 } from "node:zlib";
import { patchEngine } from "../js/patch-engine.js";
import { digest } from "../js/patcher-core.js";

const fetcher = globalThis.fetch;
try {
  globalThis.fetch = async (url, options) => url.href === new URL("../js/patcher/rom-patcher.wasm", import.meta.url).href
    ? new Response(await readFile(url)) : fetcher(url, options);
  await patchEngine();
} finally {
  globalThis.fetch = fetcher;
}

export const fileOf = (bytes, name = "input.gba") => ({
  name, size: bytes.length, arrayBuffer: async () => bytes.slice().buffer,
});

const ascii = (text) => [...Buffer.from(text)];
const u16 = (value) => [value & 255, value >>> 8];
const u32 = (value) => [...u16(value & 65535), ...u16(value >>> 16)];

function adler32(bytes) {
  let low = 1, high = 0;
  for (const byte of bytes) {
    low = (low + byte) % 65521;
    high = (high + low) % 65521;
  }
  return [high >>> 8, high & 255, low >>> 8, low & 255];
}

function crc16(bytes) {
  let crc = 0xffff;
  for (const byte of bytes) {
    crc ^= byte << 8;
    for (let bit = 0; bit < 8; bit++) crc = ((crc << 1) ^ (crc & 0x8000 ? 0x1021 : 0)) & 0xffff;
  }
  return crc;
}

/** One VCDIFF window: copy two source bytes, add a 7. */
export function xdeltaPatch({ header = [0], checked = true, packed = 0 } = {}) {
  const target = [1, 2, 7], sections = [[7], [19, 2, 2], [0]];
  const delta = [target.length, packed, ...sections.map((section) => section.length),
    ...(checked ? adler32(target) : []), ...sections.flat()];
  return new Uint8Array([0xd6, 0xc3, 0xc4, 0, ...header, checked ? 5 : 1, 3, 0, delta.length, ...delta]);
}

/** An xdelta header that names the files and their digests. */
export const xdeltaNames = (result, source) => {
  const text = ascii(`${result}//${source}/`);
  return [4, ...(text.length > 127 ? [128 | (text.length >>> 7)] : []), text.length & 127, ...text];
};

/** An APS patch for N64 that identifies a 64-byte image. */
export function identifiedPatch() {
  const source = Uint8Array.from({ length: 64 }, (_, index) => index), result = source.slice();
  result[2] = 88;
  const patch = new Uint8Array([...ascii("APS10"), 1, 0, ...Array(50).fill(32), 1, ...source.subarray(0x3c, 0x3f),
    ...source.subarray(0x10, 0x18), 0, 0, 0, 0, 0, ...u32(64), ...u32(2), 1, 88]);
  return { source, result, patch };
}

export function patchFixture(format = "UPS") {
  const source = new Uint8Array([1, 2, 3]), result = new Uint8Array([1, 2, 7]);
  const ips = [...ascii("PATCH"), 0, 0, 2, 0, 1, 7, ...ascii("EOF")];
  const block = (bytes) => {
    const whole = new Uint8Array(0x10000);
    whole.set(bytes);
    return whole;
  };
  const plain = {
    IPS: () => ips,
    EBP: () => [...ips, ...ascii('{"patcher":"EBPatcher","title":"Example"}')],
    PPF: () => [...ascii("PPF10"), 0, ...Array(50).fill(32), ...u32(2), 1, 7],
    "APS-N64": () => [...ascii("APS10"), 0, 0, ...Array(50).fill(32), ...u32(result.length), ...u32(2), 1, 7],
    "APS-GBA": () => {
      const before = block(source), after = block(result);
      return [...ascii("APS1"), ...u32(source.length), ...u32(result.length), ...u32(0),
        ...u16(crc16(before)), ...u16(crc16(after)), ...before.map((byte, at) => byte ^ after[at])];
    },
    VCDIFF: () => xdeltaPatch(),
  }[format];
  if (plain) return { source, result, patch: new Uint8Array(plain()) };
  const body = format === "BPS" ? [...ascii("BPS1"), 131, 131, 128, 132, 129, 7]
    : [...ascii("UPS1"), 131, 131, 130, 4, 0];
  const patch = new Uint8Array(body.length + 12);
  patch.set(body);
  const trailer = new DataView(patch.buffer, body.length);
  trailer.setUint32(0, crc32(source), true);
  trailer.setUint32(4, crc32(result), true);
  trailer.setUint32(8, crc32(patch.subarray(0, -4)), true);
  return { source, result, patch };
}

export async function releaseFixture() {
  const fixture = patchFixture();
  const { source, result, patch } = fixture;
  return { ...fixture, release: { profile: "example", profile_name: "Example", language: "xx", version: "1.0.0",
    source_size: source.length, source_sha1: await digest(source), result_size: result.length, result_sha1: await digest(result),
    patch: "example/xx/1.0.0.ups", patch_size: patch.length, patch_sha256: await digest(patch, "SHA-256"),
    output: "example-xx.gba" } };
}
