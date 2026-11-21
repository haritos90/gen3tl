/** Load a WebAssembly URL, ArrayBuffer, or Uint8Array. */
export async function load(input) {
  let bytes = input;
  if (typeof input === "string" || input instanceof URL) {
    const response = await fetch(input);
    if (!response.ok) throw new Error(`Cannot load module: HTTP ${response.status}`);
    bytes = await response.arrayBuffer();
  }
  const { instance } = await WebAssembly.instantiate(bytes);
  const wasm = instance.exports;
  const decoder = new TextDecoder();

  function checkBytes(value, name) {
    if (!(value instanceof Uint8Array)) {
      throw Object.assign(new TypeError(`${name} must be a Uint8Array`), { code: "invalid_argument" });
    }
  }

  function allocate(bytes) {
    const pointer = wasm.rp_alloc(bytes.length) >>> 0;
    if (!pointer) {
      throw Object.assign(new Error(`Cannot allocate ${bytes.length} bytes`), {
        code: "allocation_failed", size: bytes.length,
      });
    }
    new Uint8Array(wasm.memory.buffer, pointer, bytes.length).set(bytes);
    return pointer;
  }

  function invoke(operation, rom, patch, limit) {
    const inspect = operation === "info";
    if (!inspect && (!Number.isInteger(limit) || limit < 0 || limit > 0xffffffff)) {
      throw Object.assign(new RangeError("limit must be an unsigned 32-bit integer"), { code: "invalid_argument" });
    }
    checkBytes(patch, "patch");
    if (!inspect) checkBytes(rom, "rom");
    let sourcePointer = 0, patchPointer = 0, responsePointer = 0;
    try {
      if (!inspect) sourcePointer = allocate(rom);
      patchPointer = allocate(patch);
      responsePointer = (inspect
        ? wasm.rp_info(patchPointer, patch.length)
        : wasm[`rp_${operation}`](sourcePointer, rom.length, patchPointer, patch.length, limit)) >>> 0;
      const header = new DataView(wasm.memory.buffer, responsePointer, 16);
      const jsonPointer = header.getUint32(0, true), jsonLength = header.getUint32(4, true);
      const dataPointer = header.getUint32(8, true), dataLength = header.getUint32(12, true);
      const result = JSON.parse(decoder.decode(new Uint8Array(wasm.memory.buffer, jsonPointer, jsonLength)));
      if (result.code) throw Object.assign(new Error(result.message), result);
      if (!inspect) result.bytes = new Uint8Array(wasm.memory.buffer, dataPointer, dataLength).slice();
      return result;
    } finally {
      if (responsePointer) wasm.rp_response_free(responsePointer);
      if (patchPointer) wasm.rp_free(patchPointer, patch.length);
      if (sourcePointer) wasm.rp_free(sourcePointer, rom.length);
    }
  }

  return Object.freeze({
    /** Validate a patch and return its declared sizes, checksums and digests. */
    info(patch) { return invoke("info", null, patch, 0); },
    /** Return { bytes, format, verified }, accepting at most limit bytes. */
    apply(rom, patch, limit) {
      return invoke("apply", rom, patch, limit);
    },
    /** Recover original UPS bytes with the same result shape and limit. */
    revert(patched, patch, limit) {
      return invoke("revert", patched, patch, limit);
    },
  });
}
