// ROM bytes and the check bundle stay in this worker.

import { Check, startPython } from "./check.js";
import { Gfx } from "./gfx.js";
import { joinRows, readRom } from "./gametext.js";

let check = null;
let gfx = null;
let rom = null;

const commands = {
  async start({ bundle }) {
    const [py, response] = await Promise.all([startPython(), fetch(bundle)]);
    if (!response.ok) throw new Error(`${bundle}: HTTP ${response.status}`);
    const bytes = new Uint8Array(await response.arrayBuffer());
    check = new Check(py, bytes);
    gfx = new Gfx(py);
    return { rename: Boolean(check.renameFn) };
  },
  async readRom({ file, game, releases, patch }) {
    rom = await readRom(file, game, releases, fetch, patch);
    return rom;
  },
  join({ rows, texts, glossary, fold }) {
    return joinRows(rows, texts, glossary, fold);
  },
  configure({ index, latin, cells, graphics, withRom, draft }) {
    if (graphics) gfx.load(graphics, withRom ? rom?.bytes ?? null : null);
    if (!draft) check.configure(index, latin, cells);
    return { ready: !draft && check.ready, exact: !draft && check.exact, gfxRom: gfx.rom };
  },
  english({ source }) {
    return check.english(rom.bytes, source);
  },
  check({ entry, text, sources, drafts }) {
    return check.run(entry, text, sources, drafts);
  },
  rename({ args }) {
    return check.rename(...args);
  },
  rulePreview({ args }) {
    return check.rulePreview(...args);
  },
  gfx({ method, args }) {
    return gfx[method](...args);
  },
};

self.onmessage = async ({ data }) => {
  try {
    const result = await commands[data.command](data);
    self.postMessage({ id: data.id, result });
  } catch (error) {
    self.postMessage({ id: data.id, error: {
      message: error.message || String(error), key: error.key, vars: error.vars, patchBase: error.patchBase,
    } });
  }
};
