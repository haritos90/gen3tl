// Keep ROM and Pyodide work off the page thread.

import { Check, startPython } from "./check.js";
import { Gfx } from "./gfx.js";
import { joinRows, readRom } from "./gametext.js";
import { checkSiteBuild } from "./ui.js";

function reviveError(data) {
  const error = new Error(data.message);
  if (data.key) error.key = data.key;
  if (data.vars) error.vars = data.vars;
  if (data.patchBase) error.patchBase = data.patchBase;
  return error;
}

export class EditorRuntime {
  constructor() {
    this.worker = null;
    this.local = null;
    this.rom = null;
    this.pending = new Map();
    this.serial = 0;
    this.check = {
      ready: false,
      exact: false,
      englishFn: true,
      renameFn: true,
      configure: (...args) => this.configure(...args),
      run: (entry, text, sources = null, drafts = null) => this.local ? this.local.check.run(entry, text, sources, drafts)
        : this.call("check", { entry, text, sources, drafts }),
      english: (rom, source) => this.local ? this.local.check.english(rom, source) : this.call("english", { source }),
      rename: (...args) => this.local ? this.local.check.rename(...args) : this.call("rename", { args }),
      rulePreview: (...args) => this.local ? this.local.check.rulePreview(...args) : this.call("rulePreview", { args }),
    };
    this.gfx = { rom: false };
    for (const method of ["sheet", "png", "edited", "check", "preview"]) {
      this.gfx[method] = (...args) => this.local ? this.local.gfx[method](...args) : this.call("gfx", { method, args });
    }
  }

  ensure() {
    if (this.local || typeof Worker === "undefined") return false;
    if (!this.worker) {
      this.worker = new Worker(new URL("./editor-worker.js", import.meta.url), { type: "module" });
      this.worker.onmessage = ({ data }) => {
        const pending = this.pending.get(data.id);
        if (!pending) return;
        this.pending.delete(data.id);
        if (data.error) pending.reject(reviveError(data.error));
        else pending.resolve(data.result);
      };
      this.worker.onerror = (event) => {
        checkSiteBuild();
        const error = new Error(event.message || "The editor worker could not run.");
        for (const pending of this.pending.values()) pending.reject(error);
        this.pending.clear();
      };
    }
    return true;
  }

  call(command, data = {}) {
    this.ensure();
    const id = ++this.serial;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.worker.postMessage({ id, command, ...data });
    });
  }

  async start(bundle) {
    if (!this.ensure()) {
      const py = await startPython();
      const response = await fetch(bundle);
      if (!response.ok) throw new Error(`${bundle}: HTTP ${response.status}`);
      const bytes = new Uint8Array(await response.arrayBuffer());
      this.local = { check: new Check(py, bytes), gfx: new Gfx(py) };
      return;
    }
    const result = await this.call("start", { bundle });
    this.check.renameFn = result.rename;
  }

  async readRom(file, game, releases, fetcher, patch) {
    const loaded = this.ensure() ? await this.call("readRom", { file, game, releases, patch })
      : await readRom(file, game, releases, fetcher, patch);
    this.rom = loaded;
    return loaded;
  }

  async join(rows, texts, glossary, fold) {
    if (this.ensure()) return this.call("join", { rows, texts, glossary, fold });
    rows = structuredClone(rows);
    for (let start = 0; start < rows.length; start += 256) {
      joinRows(rows.slice(start, start + 256), texts, glossary, fold);
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
    return rows;
  }

  configure(index, latin, cells, graphics, withRom, draft) {
    this.check.ready = this.check.exact = false;
    if (this.ensure()) {
      return this.call("configure", { index, latin, cells, graphics, withRom, draft }).then((result) => {
        Object.assign(this.check, result);
        this.gfx.rom = result.gfxRom;
        return result;
      });
    }
    if (graphics) this.local.gfx.load(graphics, withRom ? this.rom?.bytes ?? null : null);
    if (!draft) this.local.check.configure(index, latin, cells);
    else this.local.check.ready = false;
    this.gfx.rom = this.local.gfx.rom;
    this.check.ready = this.local.check.ready;
    this.check.exact = this.local.check.exact;
    return { ready: this.local.check.ready, exact: this.local.check.exact, gfxRom: this.gfx.rom };
  }

  close() {
    this.worker?.terminate();
    this.worker = null;
  }
}
