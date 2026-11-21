// Release identity checks shared by UI and worker.
import { uiError } from "./ui.js";
import { releasePatch } from "./patch-engine.js";

const keys = ["profile", "profile_name", "language", "version", "source_sha1", "source_size",
  "result_sha1", "result_size", "patch", "patch_sha256", "patch_size", "output"].sort();
const withNames = [...keys, "names", "names_sha256"].sort();
const withNotes = [...keys, "notes", "notes_sha256"].sort();
const withNamesAndNotes = [...withNames, "notes", "notes_sha256"].sort();
export function validateManifest(doc) {
  if (!doc || doc.schema !== 1 || Object.keys(doc).sort().join() !== "releases,schema" ||
      !Array.isArray(doc.releases)) throw uiError("Invalid release list.");
  const seen = new Set();
  const profiles = new Map();
  for (const row of doc.releases) {
    const fields = Object.keys(row || {}).sort().join();
    if (!row || ![keys.join(), withNames.join(), withNotes.join(), withNamesAndNotes.join()].includes(fields))
      throw uiError("Invalid release fields.");
    if ("names" in row && (row.names !== `${row.profile}/${row.language}/${row.version}.names.json` ||
        typeof row.names_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(row.names_sha256)))
      throw uiError("Invalid release name tables.");
    if ("notes" in row && (row.notes !== `${row.profile}/${row.language}/${row.version}.notes.json` ||
        typeof row.notes_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(row.notes_sha256)))
      throw uiError("Invalid release notes.");
    for (const key of ["profile", "language"])
      if (typeof row[key] !== "string" || !/^[a-zA-Z0-9][a-zA-Z0-9._-]*$/.test(row[key]))
        throw uiError("Invalid release identity.");
    if (typeof row.profile_name !== "string" || !row.profile_name.trim() ||
        typeof row.version !== "string" || !/^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/.test(row.version))
      throw uiError("Invalid release version or name.");
    for (const [key, size] of [["source_sha1", 40], ["result_sha1", 40], ["patch_sha256", 64]])
      if (typeof row[key] !== "string" || !(new RegExp(`^[0-9a-f]{${size}}$`)).test(row[key]))
        throw uiError("Invalid release hash.");
    for (const key of ["source_size", "result_size", "patch_size"])
      if (!Number.isSafeInteger(row[key]) || row[key] <= 0 || row[key] > 128 * 1024 * 1024)
        throw uiError("Invalid release size.");
    if (row.patch !== `${row.profile}/${row.language}/${row.version}.ups` || typeof row.output !== "string" ||
        !/^[^<>:"/\\|?*\x00-\x1f\x7f]+\.gba$/.test(row.output) || !row.output.slice(0, -4).trim())
      throw uiError("Invalid release filename.");
    const id = JSON.stringify([row.profile, row.language, row.version]);
    if (seen.has(id)) throw uiError("Duplicate release identity.");
    seen.add(id);
    const source = JSON.stringify([row.profile_name, row.source_sha1, row.source_size]);
    if (profiles.has(row.profile) && profiles.get(row.profile) !== source)
      throw uiError("Inconsistent base identity for one release profile.");
    profiles.set(row.profile, source);
  }
  return doc.releases;
}

export async function digest(bytes, algorithm = "SHA-1") {
  if (!globalThis.crypto?.subtle) throw uiError("Open this page over HTTPS or localhost to verify files.");
  return [...new Uint8Array(await crypto.subtle.digest(algorithm, bytes))]
    .map(n => n.toString(16).padStart(2, "0")).join("");
}

export async function verifySource(source, release) {
  if (source.length !== release.source_size) throw uiError("Wrong ROM size. Choose the original ROM listed above.");
  const actual = await digest(source);
  if (actual !== release.source_sha1) {
    throw uiError("Wrong ROM SHA-1: {sha1}. Choose the original ROM listed above.", { sha1: actual });
  }
  return actual;
}

async function verifyPatch(patch, release) {
  if (patch.length !== release.patch_size || await digest(patch, "SHA-256") !== release.patch_sha256)
    throw uiError("Patch download differs from the release. Reload and try again.");
}

export async function patchRelease(source, patch, release) {
  validateManifest({ schema: 1, releases: [release] });
  await verifySource(source, release);
  await verifyPatch(patch, release);
  const result = await releasePatch(source, patch, release.result_size);
  if (await digest(result) !== release.result_sha1) throw uiError("Patched ROM differs from the released result.");
  return result;
}

export async function reverseRelease(result, patch, release) {
  validateManifest({ schema: 1, releases: [release] });
  if (result.length !== release.result_size || await digest(result) !== release.result_sha1)
    throw uiError("Patched ROM differs from the released result.");
  await verifyPatch(patch, release);
  const source = await releasePatch(result, patch, release.source_size, true);
  if (await digest(source) !== release.source_sha1) throw uiError("Recovered ROM differs from the original.");
  return source;
}
