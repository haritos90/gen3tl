import { load } from "./patcher/rom-patcher.js";
import { N_, uiError } from "./ui.js";

export const ROM_LIMIT = 128 * 1024 * 1024;
let instance;
export const patchEngine = () => instance ??= load(new URL("./patcher/rom-patcher.wasm", import.meta.url));

export const PATCH_ERRORS = {
  unknown_format: N_("This is not a UPS, IPS, BPS, xdelta, APS or PPF patch."),
  unsupported_revert: N_("Only UPS patches can recover the original ROM."),
  unsupported_compressor: N_("This xdelta patch is packed with a method this page cannot read."),
  unsupported_code_table: N_("This xdelta patch carries its own code table, which this page cannot read."),
  truncated_patch: N_("The patch is damaged: at byte {offset}, {needed} bytes are needed but {available} remain."),
  malformed_patch: N_("The patch is damaged at byte {offset}."),
  patch_checksum: N_("The patch is damaged: expected CRC32 {expected}, got {actual}."),
  source_size: N_("The patch was made for another ROM: expected {expected} bytes, got {actual}."),
  source_checksum: N_("The patch was made for another ROM: expected checksum {expected}, got {actual}."),
  source_mismatch: N_("The patch was made for another ROM: the data at offset {offset} differs."),
  source_digest: N_("The patch was made for another ROM: its BLAKE3 digest differs from the one in the patch."),
  already_patched: N_("This ROM is already patched: {size} bytes, CRC32 {checksum}."),
  already_original: N_("This ROM is already the original: {size} bytes, CRC32 {checksum}."),
  result_checksum: N_("The result does not match the checksum in the patch (expected {expected}, got {actual}): the ROM is another one or the patch is damaged."),
  result_digest: N_("The patch is damaged: the result differs from the BLAKE3 digest in the patch."),
  limit_exceeded: N_("The result would have {size} bytes; the page limit is {limit} bytes."),
  out_of_bounds: N_("The patch is damaged: {length} bytes at offset {offset} exceed {size} bytes."),
  allocation_failed: N_("The browser could not reserve {size} bytes for this patch."),
  invalid_argument: N_("The patch could not run with these inputs. Reload and try again."),
};

export function patchError(error) {
  const vars = Object.fromEntries(Object.entries(error).filter(([, value]) => typeof value === "number"));
  for (const name of ["expected", "actual", "checksum"]) {
    if (name in vars && (error.code?.includes("checksum") || name === "checksum"))
      vars[name] = vars[name].toString(16).padStart(8, "0");
  }
  return uiError(PATCH_ERRORS[error.code] ?? N_("Could not apply this patch."), vars);
}

const RELEASE_ERRORS = {
  unknown_format: N_("Not a UPS patch."),
  patch_checksum: N_("Patch CRC32 mismatch."),
  source_size: N_("Source ROM does not match the UPS patch."),
  source_checksum: N_("Source ROM does not match the UPS patch."),
  already_patched: N_("Source ROM does not match the UPS patch."),
  already_original: N_("Source ROM does not match the UPS patch."),
  result_checksum: N_("Result CRC32 mismatch."),
  limit_exceeded: N_("UPS output size differs from the release."),
  out_of_bounds: N_("UPS record is outside the ROM."),
  truncated_patch: N_("Truncated UPS record."),
  malformed_patch: N_("UPS offset is outside the ROM."),
};

export async function releasePatch(source, patch, expectedSize, reverse = false) {
  try {
    const engine = await patchEngine();
    const info = engine.info(patch);
    if (info.format !== "UPS") throw uiError("Not a UPS patch.");
    if ((reverse ? info.sourceSize : info.resultSize) !== expectedSize || expectedSize > ROM_LIMIT)
      throw uiError("UPS output size differs from the release.");
    return engine[reverse ? "revert" : "apply"](source, patch, ROM_LIMIT).bytes;
  } catch (error) {
    if (error.key) throw error;
    throw RELEASE_ERRORS[error.code] ? uiError(RELEASE_ERRORS[error.code]) : patchError(error);
  }
}
