// The layout check in the browser: gen3tl in Pyodide.
// Without a ROM the widths are approximate.
// The English of key-only catalogs is read from the ROM.

import { fallbackWidth, sha1Hex } from "./font.js";

export const PYODIDE_VERSION = "314.0.7"; // Tested with this runtime version
export const PYODIDE_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;

const GLUE = `
import json
import sys
if "/bundle" not in sys.path:
    sys.path.insert(0, "/bundle")
from gen3tl.web import editorcheck

_checker = None


def configure(doc, latin, version, cells=None):
    global _checker
    doc = json.loads(doc)
    if "check_rules" in doc:
        rules = doc["check_rules"]
    else:
        with open("/bundle/rules.json", encoding="utf-8") as fh:
            rules = fh.read()
    widths = {k: bytes(v) for k, v in latin.to_py().items()}
    # ROM glyph cells enable exact shadow checks
    glyphs = {k: bytes(v) for k, v in cells.to_py().items()} if cells is not None else None
    _checker = editorcheck.Checker(doc, widths, rules, version, glyphs)


def check(entry, text, sources=None, drafts=None):
    return _checker.check_json(entry, text, sources, drafts)


def rule_preview(identity, entries, drafts, request):
    return _checker.rule_preview_json(identity, entries, drafts, request)


def english(rom, rows):
    # Older bundles lack the reader
    from gen3tl.catalog import source
    data = bytes(rom.to_py()) if hasattr(rom, "to_py") else bytes(rom)
    texts, missing = source.read(data, json.loads(rows))
    digests = {key: source.digest(text) for key, text in texts.items()}
    return json.dumps({"texts": texts, "missing": missing, "digests": digests}, ensure_ascii=False)


def rename(old, new, items, slots, scope=None):
    # Older bundles still check without this import
    from gen3tl.lang import termedit
    if scope is None:
        return termedit.preview_json(_checker, old, new, items, slots)
    return termedit.preview_json(_checker, old, new, items, slots, scope)
`;

/** Pyodide from its CDN, or from `load` (Node tests). */
export async function startPython(load = null) {
  if (load) return load();
  const mod = await import(PYODIDE_URL + "pyodide.mjs");
  return mod.loadPyodide({ indexURL: PYODIDE_URL });
}

/** The fields of the index the Checker reads. */
export function checkDoc(index) {
  const { fonts, charmap, placeholders, glossary } = index;
  return JSON.stringify({ fonts, charmap, placeholders, glossary, language: index.language, check_rules: index.check_rules,
    engine_rules: index.engine_rules, fill_sources: index.fill_sources });
}

/** Latin widths per style; the fallback width without a ROM. */
export function checkWidths(index, latin = null) {
  if (latin) return latin;
  const out = {};
  for (const [style, f] of Object.entries(index.fonts.styles)) {
    out[style] = new Uint8Array(Math.max(f.rom.count, 256)).fill(fallbackWidth(f));
  }
  return out;
}

export class Check {
  constructor(py, bundle) {
    this.py = py;
    py.unpackArchive(bundle, "zip", { extractDir: "/bundle" });
    py.runPython(GLUE);
    this.configureFn = py.globals.get("configure");
    this.checkFn = py.globals.get("check");
    this.rulePreviewFn = py.globals.get("rule_preview");
    this.renameFn = py.globals.get("rename"); // Optional in older check bundles
    this.englishFn = py.globals.get("english");
    this.exact = false;
    this.ready = false;
  }

  /** Set the language and widths; exact with `latin` and `cells`. */
  configure(index, latin = null, cells = null) {
    this.ready = false;
    const args = [checkDoc(index), checkWidths(index, latin), index.version ?? null];
    if (latin && cells) args.push(cells); // Without cells, shadow checks stay approximate
    this.configureFn(...args);
    this.exact = latin !== null;
    this.ready = true;
  }

  /** editorcheck.Checker.check of one entry and text. */
  run(entry, text, sources = null, drafts = null) {
    const out = JSON.parse(this.checkFn(JSON.stringify(entry), text,
      sources === null ? null : JSON.stringify(sources), drafts === null ? null : JSON.stringify(drafts)));
    out.exact = this.exact;
    return out;
  }

  rulePreview(identity, entries, drafts, request) {
    const out = JSON.parse(this.rulePreviewFn(identity, JSON.stringify(entries), JSON.stringify(drafts), JSON.stringify(request)));
    out.exact = this.exact;
    return out;
  }

  /**
   * The term-rename report, or null when nothing changes.
   * Items are entries; slots map word ids to occurrences.
   * Scope selects related rows and included entry ids.
   */
  rename(oldRow, newRow, items, slots = {}, scope = null) {
    const args = [JSON.stringify(oldRow), JSON.stringify(newRow), JSON.stringify(items), JSON.stringify(slots)];
    if (scope) args.push(JSON.stringify(scope));
    const out = JSON.parse(this.renameFn(...args));
    if (out) out.exact = this.exact;
    return out;
  }

  /** The English of the source rows, read from a ROM. */
  english(rom, source) {
    return JSON.parse(this.englishFn(rom, JSON.stringify(source.rows)));
  }
}

const englishCache = new Map(); // ROM SHA-1 -> its English, for the page session

/** Cache English per ROM and source for this page session. */
export async function englishOf(check, rom, source) {
  const key = `${await sha1Hex(rom)}:${source.profile}`;
  if (!englishCache.has(key)) englishCache.set(key, check.english(rom, source));
  return englishCache.get(key);
}

/** A function that runs `fn` after `ms` without calls. */
export function debounce(fn, ms) {
  let t = null;
  const d = (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
  d.cancel = () => clearTimeout(t);
  return d;
}
