// Preview and edit graphics containing game text.
// Pixels come from local ROMs; validated uploads store area art.

import { FAIL, extractionFailed, findingText, layoutFailed } from "./checktext.js";
import { download, el } from "./dom.js";
import { notesStatements, notesText, renderNoteDrafts, renderNotes } from "./guidance.js";
import { digest } from "./patcher-core.js";
import { N_, errorText, t, uiError, uiLanguage } from "./ui.js";

export const CATEGORIES = ["translate", "optional", "keep"];
// Display labels must preserve stored inventory values.
const CATEGORY_NAMES = { translate: N_("translate"), optional: N_("optional"), keep: N_("keep") };
const categoryName = (c) => (CATEGORY_NAMES[c] ? t(CATEGORY_NAMES[c]) : c);

/** Decode a base64 string into bytes. */
export function fromB64(s) {
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

/** Encode bytes as a base64 string. */
export function toB64(bytes) {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

/** Resolve source, project and uploaded graphic states. */
export function statusOf(row, graphics, uploads = new Map()) {
  return {
    source: Boolean(row.rom_offset),
    project: Boolean(graphics?.edits?.[row.id]),
    uploaded: uploads.has(row.id),
  };
}

/** The English of a graphics row. */
export function englishOf(row, read = null, byDigest = null) {
  const texts = read?.texts ?? null;
  if (!texts) return row.source_text ?? "";
  if (row.source_key != null && typeof texts[row.source_key] === "string") return texts[row.source_key];
  if (row.source_digest != null) {
    const key = byDigest?.get(row.source_digest)
      ?? Object.entries(read.digests ?? {}).find(([, d]) => d === row.source_digest)?.[0];
    if (key != null && typeof texts[key] === "string") return texts[key];
  }
  return row.source_text ?? "";
}

/** Unmigrated graphics retain their legacy reason. */
export function reasonOf(row, index) {
  return notesText(row.note_ids, index?.notes, index?.language) ?? row.reason ?? "";
}

/** Summaries keep statements separate from searchable metadata. */
export function summaryOf(row, index) {
  return notesStatements(row.note_ids, index?.notes, index?.language) ?? row.reason ?? "";
}

/** Filter graphics by search, category and edit status. */
export function filterGraphics(rows, filters, graphics, uploads = new Map(), english = null, index = null) {
  const q = (filters.search || "").trim().toLowerCase();
  const out = [];
  rows.forEach((r, i) => {
    if (filters.category && r.category !== filters.category) return;
    const s = statusOf(r, graphics, uploads);
    const want = filters.status;
    if (want === "edited" && !s.project) return;
    if (want === "uploaded" && !s.uploaded) return;
    if (want === "none" && (s.project || s.uploaded || !s.source)) return;
    if (want === "nosource" && s.source) return;
    if (q && r.id.toLowerCase() !== q &&
      ![english ? english(r) : r.source_text ?? "", r.screen, reasonOf(r, index)]
        .some((t) => (t || "").toLowerCase().includes(q))) return;
    out.push(i);
  });
  return out;
}

/** Parse text-area rectangles with arbitrary separators. */
export function parseAreas(text) {
  if (/[^\d\s,;[\]()-]/.test(text)) throw uiError("text areas take numbers only: x,y,w,h; x,y,w,h");
  const n = (text.match(/-?\d+/g) ?? []).map(Number);
  if (n.length % 4) throw uiError("{n} numbers: each text area is x,y,w,h", { n: n.length });
  const out = [];
  for (let i = 0; i < n.length; i += 4) {
    const [x, y, w, h] = n.slice(i, i + 4);
    if (x < 0 || y < 0 || w <= 0 || h <= 0) throw uiError("text area {area} is empty or negative", { area: `${x},${y},${w},${h}` });
    out.push([x, y, w, h]);
  }
  return out;
}

export const formatAreas = (areas) => (areas ?? []).map((a) => a.join(",")).join("; ");

/** Parse a tile count, preserving empty input. */
export function parseTiles(text) {
  const t = String(text ?? "").trim();
  if (!t) return null;
  if (!/^\d+$/.test(t) || Number(t) < 1) throw uiError("tile count {n} is not a positive integer", { n: t });
  return Number(t);
}

/** Convert palette indices into RGBA pixels. */
export function rgbaOf(pixels, palette) {
  const rgb = palette.map((c) => [1, 3, 5].map((k) => parseInt(c.slice(k, k + 2), 16)));
  const out = new Uint8ClampedArray(pixels.length * 4);
  pixels.forEach((v, i) => {
    const c = rgb[v] ?? [0, 0, 0];
    out.set([c[0], c[1], c[2], 255], i * 4);
  });
  return out;
}

/** Locate tiles appended after the base artwork. */
export function appendedTiles(widthTiles, base, tiles) {
  const out = [];
  for (let k = base; k < (tiles ?? base); k++) out.push([(k % widthTiles) * 8, Math.floor(k / widthTiles) * 8, 8, 8]);
  return out;
}

/** Locate pixels named by validation findings. */
export function findingPixels(findings) {
  return (findings ?? []).flatMap((f) => {
    const m = /^pixel (\d+),(\d+):/.exec(f);
    return m ? [[Number(m[1]), Number(m[2])]] : [];
  });
}

const sameBytes = (a, b) => a.length === b.length && a.every((v, i) => v === b[i]);

/** Draw zoomed graphics with area, tile and finding markers. */
export function drawSheet(canvas, img, palette, zoom, marks = {}) {
  const { width: w, height: h } = img;
  canvas.width = w * zoom;
  canvas.height = h * zoom;
  const off = document.createElement("canvas");
  [off.width, off.height] = [w, h];
  off.getContext("2d").putImageData(new ImageData(rgbaOf(img.pixels, palette), w, h), 0, 0);
  const ctx = canvas.getContext("2d");
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(off, 0, 0, w * zoom, h * zoom);
  const box = ([x, y, bw, bh], colour, dash = []) => {
    ctx.setLineDash(dash);
    ctx.strokeStyle = colour;
    ctx.strokeRect(x * zoom + 0.5, y * zoom + 0.5, bw * zoom - 1, bh * zoom - 1);
  };
  ctx.lineWidth = 1;
  for (const a of marks.areas ?? []) box(a, "#00d5ff");
  for (const t of marks.appended ?? []) box(t, "#ff9d00", [3, 2]);
  for (const [x, y] of marks.pixels ?? []) box([x, y, 1, 1], "#ff1f1f");
}

/** Render graphics using shared ROM and upload access. */
export class PicturesView {
  constructor(host, $) {
    this.host = host;
    this.$ = $;
    this.rows = [];
    this.filtered = [];
    this.cur = null;
    this.zoom = 2;
    this.sheets = new Map();
    this.last = null; // Keep findings visible until another graphic opens.
    this._english = null;
    this._byDigest = null;
  }

  get graphics() {
    return this.host.index()?.graphics ?? null;
  }

  row(id) {
    return this.rows.find((r) => r.id === id) ?? null;
  }

  /** Read uploads, defaulting to empty before language loading. */
  uploadMap() {
    return this.host.uploads()?.map ?? new Map();
  }

  /** The loaded ROM's English and digest index, cached. */
  english() {
    const read = this.host.english?.() ?? null;
    if (!read) return null;
    if (this._english !== read) {
      this._english = read;
      this._byDigest = new Map(Object.entries(read.digests ?? {}).map(([k, d]) => [d, k]));
    }
    return { read, byDigest: this._byDigest };
  }

  /** The English of a graphics row, from the ROM. */
  englishOf(row) {
    const found = this.english();
    return englishOf(row, found?.read ?? null, found?.byDigest ?? null);
  }

  /** Load the selected language's graphics catalog. */
  reset(want = null, select = true) {
    this.host.reference?.(null);
    this.generation = (this.generation ?? 0) + 1;
    this.rows = this.graphics?.inventory ?? [];
    this.sheets.clear();
    this.cur = null;
    this.last = null;
    this._english = null;
    this.applyFilters();
    const first = this.row(want)?.id ?? this.rows[this.filtered[0]]?.id;
    if (select && first) this.open(first);
    else this.renderEmpty();
  }

  /** Refresh graphics after ROM or bundle changes. */
  refresh() {
    this.sheets.clear();
    this._english = null;
    this.renderList();
    if (this.cur) this.renderPictures();
  }

  filters() {
    const $ = this.$;
    return { search: $("p-search").value, category: $("p-category").value, status: $("p-status").value };
  }

  applyFilters() {
    this.filtered = filterGraphics(this.rows, this.filters(), this.graphics, this.uploadMap(), (r) => this.englishOf(r),
      this.host.index());
    this.$("p-count").textContent = this.graphics
      ? t("{shown} of {total} graphics", { shown: this.filtered.length, total: this.rows.length })
      : t("The profile lists no graphics.");
    this.renderList();
  }

  renderList() {
    const uploads = this.uploadMap();
    this.$("p-list").replaceChildren(...this.filtered.map((i) => {
      const r = this.rows[i];
      const s = statusOf(r, this.graphics, uploads);
      return el("div", { class: `g-row${r.id === this.cur ? " sel" : ""}`, role: "option", on: { click: () => this.open(r.id) } },
        el("div", { class: "l1" },
          el("span", { class: "eid", textContent: r.id }),
          el("span", { class: `mark cat-${r.category}`, textContent: categoryName(r.category) }),
          s.project ? el("span", { class: "mark edited", textContent: t("edited") }) : null,
          s.uploaded ? el("span", { class: "mark uploaded", textContent: t("uploaded") }) : null,
          s.source ? null : el("span", { class: "mark warn", textContent: t("no ROM source") }),
          el("span", { class: "muted", textContent: r.screen })),
        el("div", { class: "l2", textContent: this.englishOf(r) }),
        el("div", { class: "l3", textContent: summaryOf(r, this.host.index()) }));
    }));
  }

  step(d) {
    if (!this.filtered.length) return;
    const p = this.filtered.findIndex((i) => this.rows[i].id === this.cur);
    const q = p < 0 ? 0 : Math.min(this.filtered.length - 1, Math.max(0, p + d));
    this.open(this.rows[this.filtered[q]].id);
  }

  renderEmpty() {
    const $ = this.$;
    for (const id of ["p-id", "p-category-badge", "p-status-badge", "p-screen"]) $(id).textContent = "";
    $("p-facts").replaceChildren();
    $("p-project").replaceChildren();
    $("p-sheets").replaceChildren(el("div", { class: "muted", textContent: t("No graphic selected.") }));
    $("p-palette").replaceChildren();
  }

  open(id) {
    const r = this.row(id);
    if (!r) return;
    if (this.cur !== id) this.last = null;
    this.cur = id;
    this.host.setHash(id);
    this.renderList();
    const sel = this.$("p-list").querySelector(".g-row.sel");
    sel?.scrollIntoView?.({ block: "nearest" });
    const $ = this.$;
    $("p-id").textContent = r.id;
    this.host.reference?.(r.id);
    Object.assign($("p-category-badge"), { textContent: categoryName(r.category), className: `badge cat-${r.category}` });
    $("p-screen").textContent = r.screen;
    const up = this.host.uploads().get(id);
    const ed = this.graphics.edits[id];
    $("p-area-input").value = formatAreas(up?.areas ?? ed?.areas ?? []);
    $("p-tiles").value = up?.tiles ?? ed?.tiles ?? "";
    this.renderStatus();
    this.renderFacts(r);
    this.renderProject(r);
    this.renderPictures();
  }

  renderStatus() {
    const s = statusOf(this.row(this.cur), this.graphics, this.uploadMap());
    const text = [s.project && t("edited in the project"), s.uploaded && t("uploaded here")].filter(Boolean).join(", ");
    Object.assign(this.$("p-status-badge"), {
      textContent: text || (s.source ? t("not edited") : t("no ROM source")),
      className: `badge${s.uploaded ? " edited" : s.project ? " approved" : ""}`,
    });
  }

  renderFacts(r) {
    const g = this.graphics;
    const index = this.host.index();
    const notes = renderNotes(r.note_ids, index.notes, index.language, index.research);
    const facts = [
      [N_("English"), this.englishOf(r)], [N_("Category"), r.category && categoryName(r.category)], [N_("Reason"), notes ? null : r.reason],
      [N_("Screen"), r.screen], [N_("Seen on screen"), r.seen_on_screen], [N_("Offset"), r.rom_offset],
      [N_("Compression"), r.compressed], [N_("Decoded bytes"), r.size_bytes], [N_("Tiles"), r.tiles],
      [N_("Palette"), g.palette[r.id] ? t("{palette} (profile)", { palette: g.palette[r.id] }) : r.palette],
      [N_("Pointers"), r.pointer_at], [N_("Tiles per row"), g.sheet_width[r.id] ?? (r.rom_offset ? "16" : null)],
    ];
    const dl = this.$("p-facts");
    dl.replaceChildren();
    for (const [k, v] of facts) if (v) dl.append(el("dt", { textContent: t(k) }), el("dd", { textContent: String(v) }));
    if (notes) dl.append(el("dt", { textContent: t("Notes") }), el("dd", {}, notes));
    const subject = { kind: "graphic", id: r.id };
    const drafts = renderNoteDrafts(subject, subject, index, this.host.noteEdits?.(), this.host.languages?.());
    if (drafts) dl.append(el("dt", { textContent: t("Note drafts") }), el("dd", {}, drafts));
  }

  renderProject(r) {
    const box = this.$("p-project");
    const e = this.graphics.edits[r.id];
    box.replaceChildren();
    if (!e) {
      box.append(el("div", { class: "muted", textContent: t("No edit in the project.") }));
      return;
    }
    const item = (k, v) => box.append(el("div", { class: "ctx-item" }, el("b", { textContent: `${k}: ` }), v));
    if (e.shared) {
      box.append(el("div", { class: "muted", textContent: t("The edit of {id}: one edit covers every text area of this ROM graphic.",
        { id: e.shared }) }));
    }
    item(t("Text areas"), formatAreas(e.areas) || t("none"));
    if (e.tiles) item(t("Tiles"), String(e.tiles));
    if (e.evidence) item(t("Evidence"), e.evidence);
    for (const m of this.graphics.tilemaps.filter((map) => map.graphic === r.id)) {
      const cells = m.cells > 1 ? t("{name}: {n} changed cells", { name: m.name, n: m.cells })
        : t("{name}: {n} changed cell", { name: m.name, n: m.cells });
      item(t("Tilemap"), m.evidence ? `${cells}; ${m.evidence}` : cells);
    }
  }

  /** Explain why graphic pixels cannot be shown. */
  blocked(r) {
    if (!r.rom_offset) return t("The inventory traces no ROM source for this graphic: it has no pixels to show.");
    const st = this.host.gfxState();
    if (st === "loading") return t("Loading the gen3tl bundle in Pyodide…");
    if (st !== "ready") return t("The graphics need the gen3tl bundle in Pyodide, which is unavailable: {state}", { state: st });
    if (!this.host.gfx()?.rom) return "rom";
    return null;
  }

  async sheet(id) {
    if (!this.sheets.has(id)) this.sheets.set(id, Promise.resolve(this.host.gfx().sheet(id)));
    return this.sheets.get(id);
  }

  async renderPictures() {
    const request = this.pictureRequest = (this.pictureRequest ?? 0) + 1;
    const $ = this.$;
    const r = this.row(this.cur);
    const box = $("p-sheets");
    box.replaceChildren();
    $("p-palette").replaceChildren();
    $("p-note").textContent = "";
    const why = this.blocked(r);
    for (const b of ["p-download", "p-download-edited", "p-upload"]) $(b).disabled = Boolean(why);
    this.renderKept();
    if (why === "rom") {
      box.append(el("div", { class: "muted" },
        `${t("Load your ROM to see this graphic: its pixels come from your ROM, which stays in this browser; the editor data holds none.")} `,
        el("button", { type: "button", textContent: t("Load your ROM"), on: { click: () => this.host.loadRom() } })));
      return;
    }
    if (why) {
      box.append(el("div", { class: "muted", textContent: why }));
      return;
    }
    try {
      await this.drawAll(r, box, request);
    } catch (e) {
      if (request !== this.pictureRequest) return;
      console.error(e);
      box.append(el("div", { class: "error", textContent: extractionFailed(e.message || e) }));
    }
  }

  async drawAll(r, box, request = this.pictureRequest) {
    const gfx = this.host.gfx();
    const s = await this.sheet(r.id);
    if (request !== this.pictureRequest) return;
    const show = this.$("p-show-areas").checked;
    const ed = this.graphics.edits[r.id];
    const up = this.host.uploads().get(r.id);
    if (this.graphics.previews?.[r.id]?.length) {
      const screen = el("div", { class: "screen-previews" });
      screen.append(el("h3", { textContent: t("On-screen layout") }));
      const figures = el("div", { class: "sheets" });
      const versions = [];
      const previews = async (title, edited = false, upload = null) => {
        let views;
        try {
          views = await gfx.preview(r.id, edited, upload ? fromB64(upload.png) : null,
            upload?.areas, upload?.tiles);
        } catch (error) {
          figures.append(el("div", { class: "error",
            textContent: layoutFailed(title, error.message || error) }));
          return;
        }
        if (request !== this.pictureRequest) return;
        const group = [];
        for (const img of views) {
          const canvas = el("canvas", { title: `${title}: ${img.label}`, role: "img", "aria-label": `${title}: ${img.label}` });
          drawSheet(canvas, img, img.palette, this.zoom);
          figures.append(el("figure", { class: "sheet" }, canvas,
            el("figcaption", {}, el("b", { textContent: title }), ` · ${img.label}`)));
          group.push(figures.lastElementChild);
        }
        versions.push(group);
      };
      await previews(t("English"));
      if (ed) await previews(t("Project translation"), true);
      if (up) await previews(t("Your upload"), true, up);
      if (request !== this.pictureRequest) return;
      // Keep each scene's comparisons together for review.
      for (let i = 0; i < this.graphics.previews[r.id].length; i++) {
        for (const group of versions) if (group[i]) figures.append(group[i]);
      }
      screen.append(figures);
      box.append(screen, el("h3", { class: "sheet-heading", textContent: t("Tile sheets") }));
    }
    const marks = (areas, tiles) => (show ? { areas, appended: appendedTiles(s.width_tiles, s.tiles, tiles) } : {});
    const figure = (title, img, m, note = "") => {
      const c = el("canvas", { title, role: "img", "aria-label": title });
      drawSheet(c, img, s.palette, this.zoom, m);
      box.append(el("figure", { class: "sheet" }, c,
        el("figcaption", {}, el("b", { textContent: title }), note ? ` ${note}` : "")));
    };
    figure(t("Original"), s, marks(up?.areas ?? ed?.areas ?? [], null),
      t("{width}×{height}, {tiles} tiles", { width: s.width, height: s.height, tiles: s.tiles }));
    this.edited = null;
    if (ed) {
      const res = await gfx.edited(r.id);
      if (request !== this.pictureRequest) return;
      if (res.pixels) figure(t("Project's edit"), res, marks(res.areas, res.tiles), t("{n} pixels changed", { n: res.changed }));
      if (res.findings.length) {
        box.append(el("div", { class: "error", textContent: t("Project's edit: {finding}", { finding: findingText(res.findings[0]) }) }));
      }
      if (res.png) this.edited = res;
    }
    if (up) {
      const res = await gfx.edited(r.id, fromB64(up.png), up.areas, up.tiles);
      if (request !== this.pictureRequest) return;
      if (res.pixels) {
        figure(t("Your upload"), res, marks(res.areas, res.tiles),
          t("{name}, {n} pixels changed", { name: up.name, n: res.changed }));
      }
      if (res.findings.length) {
        box.append(el("div", { class: "error", textContent: t("Your upload: {finding}", { finding: findingText(res.findings[0]) }) }));
      }
      if (res.png) this.edited = res;
    }
    const last = this.last;
    if (last && last.code !== 0 && last.pixels) {
      figure(t("Checked sheet (not kept)"), last, show ? { areas: last.areas, pixels: findingPixels(last.findings) } : {},
        last.name);
    }
    this.$("p-download-edited").disabled = !this.edited;
    this.$("p-note").textContent = t("palette {source}", { source: s.palette_source });
    const swatches = s.palette.slice(0, 16).map((c, i) =>
      el("span", { class: "swatch", title: `${i}: ${c}`, style: `background:${c}` }));
    if (s.palette.length > 16) {
      swatches.push(el("span", { class: "muted", textContent: ` ${t("+{n} colours", { n: s.palette.length - 16 })}` }));
    }
    this.$("p-palette").replaceChildren(...swatches);
  }

  renderKept() {
    const $ = this.$;
    const up = this.host.uploads().get(this.cur);
    const last = this.last;
    const res = $("p-result");
    // Check lines stay English in storage; translate when shown.
    const lines = last?.error ? [`${t(FAIL)}     ${errorText(last.error)}`]
      : (last?.lines ?? up?.lines ?? []).map(findingText);
    res.replaceChildren(...lines.map((l) =>
      el("div", { class: l.startsWith(t(FAIL)) ? "fail" : "", textContent: l })));
    const kept = $("p-kept");
    kept.replaceChildren();
    if (last?.message) kept.append(el("span", { class: last.code === 0 ? "" : "error", textContent: `${t(last.message)} ` }));
    if (up) {
      const locale = uiLanguage() === "en" ? "en-GB" : uiLanguage();
      const when = new Date(up.time).toLocaleString(locale, { dateStyle: "short", timeStyle: "short" });
      kept.append(el("span", { class: "muted", textContent: `${t("Your upload: {name}, checked {when}.", { name: up.name, when })} ` }),
        el("button", { type: "button", textContent: t("Remove upload"), on: { click: () => this.removeUpload() } }));
    }
  }

  async upload(file) {
    const generation = this.generation;
    const id = this.cur;
    const projectPng = this.graphics?.edits?.[id]?.png;
    const previous = this.host.uploads?.()?.get(id);
    const $ = this.$;
    let areas;
    let tiles;
    try {
      areas = parseAreas($("p-area-input").value);
      tiles = parseTiles($("p-tiles").value);
    } catch (e) {
      this.last = { code: 2, error: e, message: N_("Not checked.") };
      this.renderKept();
      return;
    }
    const bytes = new Uint8Array(await file.arrayBuffer());
    if (generation !== this.generation) return;
    $("p-kept").replaceChildren(el("span", { class: "muted", textContent: t("Checking…") }));
    await new Promise((ok) => setTimeout(ok, 30)); // Paint the progress state before running checks.
    if (generation !== this.generation) return;
    const res = await this.host.gfx().check(id, bytes, file.name, areas, tiles);
    if (generation !== this.generation) return;
    res.name = file.name;
    const proj = this.graphics.edits[id] ? await this.host.gfx().edited(id) : null;
    if (generation !== this.generation) return;
    // Keep validation messages translatable on language changes.
    if (res.code !== 0) res.message = N_("Not kept: the sheet fails the check.");
    else if (!res.changed && (res.tiles ?? 0) <= (await this.sheet(id)).tiles) {
      res.message = N_("Not kept: no pixel differs from the original.");
    }
    else if (proj?.pixels && sameBytes(proj.pixels, res.pixels)) res.message = N_("Not kept: the sheet equals the project's edit.");
    else {
      const base = previous ? previous.base : projectPng ? await digest(fromB64(projectPng), "SHA-256") : null;
      if (generation !== this.generation) return;
      this.host.uploads().set(id, { base, areas: res.areas, tiles: res.tiles, png: toB64(res.area_png), name: file.name, lines: res.lines });
      res.message = N_("Kept: included in the export.");
      this.host.changed();
    }
    this.last = res;
    this.renderStatus();
    this.applyFilters();
    this.renderPictures();
  }

  removeUpload() {
    this.host.uploads().remove(this.cur);
    this.last = null;
    this.host.changed();
    this.open(this.cur);
    this.applyFilters();
  }

  /** Relabel the graphics list and open graphic from state. */
  relabel() {
    if (this.generation === undefined) return;
    this.applyFilters();
    const r = this.row(this.cur);
    if (!r) {
      this.renderEmpty();
      return;
    }
    Object.assign(this.$("p-category-badge"), { textContent: categoryName(r.category) });
    this.renderStatus();
    this.renderFacts(r);
    this.renderProject(r);
    this.renderPictures();
  }

  async downloadOriginal() {
    const id = this.cur;
    const bytes = await this.host.gfx().png(id);
    if (id === this.cur) download(bytes, `${id}.png`, "image/png");
  }

  downloadEdited() {
    if (this.edited?.png) download(this.edited.png, `${this.cur}-edited.png`, "image/png");
  }

  wire() {
    const $ = this.$;
    $("p-search").addEventListener("input", () => this.applyFilters());
    for (const id of ["p-category", "p-status"]) $(id).addEventListener("change", () => this.applyFilters());
    $("p-prev").addEventListener("click", () => this.step(-1));
    $("p-next").addEventListener("click", () => this.step(1));
    $("p-zoom").addEventListener("change", (e) => {
      this.zoom = Number(e.target.value);
      this.renderPictures();
    });
    $("p-show-areas").addEventListener("change", () => this.renderPictures());
    $("p-download").addEventListener("click", () => this.downloadOriginal());
    $("p-download-edited").addEventListener("click", () => this.downloadEdited());
    $("p-upload").addEventListener("click", () => $("p-file").click());
    $("p-file").addEventListener("change", (e) => {
      const f = e.target.files[0];
      if (f && this.cur) this.upload(f).catch((x) => {
        console.error(x);
        this.last = { code: 2, error: x, message: N_("Not checked.") };
        this.renderKept();
      });
      e.target.value = "";
    });
    $("p-list").addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        this.step(e.key === "ArrowDown" ? 1 : -1);
      }
    });
  }
}
