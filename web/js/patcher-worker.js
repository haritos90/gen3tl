// Files stay inside this local worker.
import { digest, patchRelease, validateManifest, verifySource } from "./patcher-core.js";
import { patchEngine, patchError, ROM_LIMIT } from "./patch-engine.js";
import { N_, uiError } from "./ui.js";

export async function handlePatchMessage(data, post, fetcher = fetch, origin = globalThis.location?.origin) {
  try {
    const { file, patchFile, release, patchURL } = data;
    if (patchFile) {
      const engine = await patchEngine();
      const patch = new Uint8Array(await patchFile.arrayBuffer());
      if (data.inspect) {
        post({ info: engine.info(patch) });
        return;
      }
      if (file.size > ROM_LIMIT)
        throw uiError("ROM has {size} bytes; the page limit is {limit} bytes.", { size: file.size, limit: ROM_LIMIT });
      const source = new Uint8Array(await file.arrayBuffer());
      post({ status: N_("Applying the patch…") });
      const { bytes, verified } = engine.apply(source, patch, ROM_LIMIT);
      post({ result: bytes.buffer, size: bytes.length, sha1: await digest(bytes), verified }, [bytes.buffer]);
      return;
    }
    validateManifest({ schema: 1, releases: [release] });
    if (file.size !== release.source_size) throw uiError("Wrong ROM size. Choose the original ROM listed above.");
    const source = new Uint8Array(await file.arrayBuffer());
    await verifySource(source, release);
    post({ status: N_("ROM verified. Downloading the patch…") });
    const url = new URL(patchURL);
    if (url.origin !== origin) throw uiError("Patch must come from this site.");
    const response = await fetcher(url, { credentials: "omit", redirect: "error", cache: "no-store" });
    if (!response.ok) throw uiError("Patch download failed ({status}).", { status: response.status });
    const patch = new Uint8Array(await response.arrayBuffer());
    post({ status: N_("Applying the patch and verifying the result…") });
    const result = await patchRelease(source, patch, release);
    post({ result: result.buffer }, [result.buffer]);
  } catch (error) {
    const failure = error.key ? error : patchError(error);
    post({ error: failure.message, key: failure.key, vars: failure.vars });
  }
}

if (typeof self !== "undefined") {
  self.onmessage = ({ data }) => handlePatchMessage(data, (message, transfer) => self.postMessage(message, transfer));
}
