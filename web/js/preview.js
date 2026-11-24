// Preview text boxes at game-pixel resolution.
// Unknown glyphs use stand-ins; overflow receives red tint.

import { INK, SHADOW, runs } from "./font.js";
import { boxes } from "./text.js";
import { standIn, standInName } from "./standin.js";
import { N_, t } from "./ui.js";
import { el } from "./dom.js";
import { arrowConsumer, arrowRoom, runtimeText, waitingWindows } from "./limits.js";
import { limitText, whereText } from "./checktext.js";

const RULE_STATES = [["original", N_("Original")], ["current", N_("Current")], ["proposed", N_("Proposed")]];

/** Byte counts include writes after early terminators. */
export function rulePreview(report, rule) {
  const count = (row) => el("p", { class: "small", textContent: row.used == null ? t("Text unavailable")
    : t("Encoded bytes: {used}/{limit}", { used: row.used, limit: row.limit ?? t("Unverified limit") }) });
  const problems = (rows) => rows.map((message) => el("p", { class: "err", textContent: message }));
  return el("div", { class: "er-comparison" }, RULE_STATES.map(([mode, title]) => {
    const value = report[mode];
    return el("section", { class: "er-state", "data-key": mode }, el("h3", { textContent: t(title) }),
      value.unavailable.length ? el("p", { class: "muted", textContent: t("Original text unavailable without a ROM") }) : null,
      value.fields.map((row) => el("div", { class: "er-value" },
        el("span", { class: "eid", textContent: row.entry }),
        el("p", { class: "er-text", textContent: row.text === "" ? t("Off") : row.text ?? t("Text unavailable") }),
        count(row), problems(row.problems))),
      value.results.map((row) => el("div", { class: "er-result" },
        el("strong", { textContent: row.routine ?? t("Generated text") }),
        el("p", { class: "er-text", textContent: row.text == null ? t("Text unavailable")
          : `${row.input} → ${rule.output === "empty" ? row.input + row.text : row.text}` }),
        rule.output === "empty" && row.text != null ? el("p", { class: "er-text",
          textContent: t("Generated ending: {text}", { text: row.text || t("Off") }) }) : null,
        count(row), problems(row.problems))), problems(value.problems));
  }));
}

const LINE = 16;
const PAD_X = 8;
const PAD_Y = 4;
const GAP = 6;

// Preview colors approximate the game's palettes.
const FRAMES = {
  message: { fill: "#f8f8f8", outer: "#384868", inner: "#a0b8d8" },
  sign: { fill: "#f0e8d0", outer: "#6a4a28", inner: "#c8a878" },
  battle: { fill: "#384050", outer: "#d0d0d8", inner: "#687088" },
  panel: { fill: "#f8f8f8", outer: "#9898a0", inner: null },
  strip: { fill: "#f8f8f8", outer: "#b8b8c0", inner: null, dotted: true },
};
const INKS = {
  plain: { [INK]: "#505058", [SHADOW]: "#d0d0c8", slot: "#787878" },
  male: { [INK]: "#3050c8", [SHADOW]: "#a8c0f0", slot: "#787878" },
  female: { [INK]: "#d02020", [SHADOW]: "#f8b8b8", slot: "#787878" },
  battle: { [INK]: "#f8f8f8", [SHADOW]: "#686078", slot: "#c8c8c8" },
};
const OVER = "rgba(232, 32, 32, 0.22)";

/** Choose a frame for the window and context. */
export function frameOf(kind, box) {
  if (kind === "battle") return "battle";
  if (kind === "dialogue") return box === "sign" ? "sign" : "message";
  if (kind === "multiline") return "panel";
  return "strip";
}

/**
 * Calculate box geometry without drawing the preview.
 * One-pixel overflow fits when only shadow is clipped.
 * Waiting lines reserve room for prompt arrows.
 */
export function layoutBoxes(text, window, measure, box = null, shadow = null) {
  const kind = window?.kind ?? "dialogue";
  const frame = frameOf(kind, box);
  const limit = window?.width ?? 0;
  const parts = boxes(text);
  const consumer = arrowConsumer(window);
  const roomForArrow = arrowRoom(window);
  const arrow = roomForArrow == null ? 0 : consumer?.arrow ?? window.arrow;
  const arrowLimit = consumer?.width ?? limit;
  const waiting = waitingWindows(text, window);
  const printed = runtimeText(text, window).split("\\p");
  const frames = [];
  let y = 0;
  let widest = limit;
  const out = parts.map((lines, bi) => {
    const ls = lines.map((l, li) => {
      const rs = runs(l, measure);
      let w;
      try {
        w = measure.lineWidth(l);
      } catch {
        w = rs.reduce((s, r) => s + (r.width || 0), 0);
      }
      const waiter = waiting[bi]?.[li];
      const waits = Boolean(waiter);
      const lineArrow = waiter?.arrow ?? 0, boundary = waiter?.width ?? limit;
      widest = Math.max(widest, w + lineArrow);
      const fits = w === limit + 1 && shadow !== null && shadow(l);
      return { text: l, width: w, runs: rs, over: limit > 0 && window?.keys == null && w > limit && !fits,
        waits, arrow: lineArrow, arrowLimit: boundary, consumer: waiter?.name,
        crowded: waits && w > boundary - lineArrow };
    });
    const room = kind === "single" || kind === "field" ? 1 : window?.lines ?? ls.length;
    const rows = Math.max(ls.length, room);
    const b = { y, rows, room, lines: ls };
    if (window?.newline_scroll_after != null) {
      const seps = [...printed[bi].matchAll(/\\[nl]/g)].map((m) => m[0]);
      let visible = [];
      for (let li = 0; li < ls.length; li++) {
        visible.push(ls[li]);
        if (seps[li] === "\\l" || li === ls.length - 1) {
          const shown = visible.map((line, i) => i < visible.length - 1 ? { ...line, waits: false } : line);
          const count = Math.max(room, shown.length);
          frames.push({ y, rows: count, room, lines: shown });
          y += count * LINE + 2 * PAD_Y + GAP;
          visible = visible.slice(1);
        }
      }
    } else {
      frames.push(b);
      y += rows * LINE + 2 * PAD_Y + GAP;
    }
    return b;
  });
  return { frame, limit, arrow, arrowLimit, consumer: consumer?.name,
    width: widest + 2 * PAD_X, height: Math.max(0, y - GAP), boxes: out, frames,
    runtimeScroll: window?.newline_scroll_after != null };
}

function rect(g, x, y, w, h, color) {
  g.fillStyle = color;
  g.fillRect(x, y, w, h);
}

function drawFrame(g, f, x, y, w, h) {
  rect(g, x + 1, y + 1, w - 2, h - 2, f.fill);
  if (f.dotted) {
    for (let i = x; i < x + w; i += 2) [rect(g, i, y, 1, 1, f.outer), rect(g, i, y + h - 1, 1, 1, f.outer)];
    for (let j = y; j < y + h; j += 2) [rect(g, x, j, 1, 1, f.outer), rect(g, x + w - 1, j, 1, 1, f.outer)];
    return;
  }
  // Leave corner pixels empty for rounded frames.
  rect(g, x + 1, y, w - 2, 1, f.outer);
  rect(g, x + 1, y + h - 1, w - 2, 1, f.outer);
  rect(g, x, y + 1, 1, h - 2, f.outer);
  rect(g, x + w - 1, y + 1, 1, h - 2, f.outer);
  if (f.inner) {
    rect(g, x + 1, y + 1, w - 2, 1, f.inner);
    rect(g, x + 1, y + h - 2, w - 2, 1, f.inner);
    rect(g, x + 1, y + 1, 1, h - 2, f.inner);
    rect(g, x + w - 2, y + 1, 1, h - 2, f.inner);
  }
}

function drawPixels(g, px, x, y, width, colors) {
  for (let gy = 0; gy < 16; gy++) {
    for (let gx = 0; gx < Math.min(width, 16); gx++) {
      const v = px[gy * 16 + gx];
      if (v === INK || v === SHADOW) rect(g, x + gx, y + gy, 1, 1, colors[v]);
    }
  }
}

// The prompt arrow: a downward triangle of the arrow's width.
function drawArrow(g, x, y, width, color) {
  const rows = Math.ceil(width / 2);
  for (let i = 0; i < rows; i++) rect(g, x + i, y + 3 + i, width - 2 * i, 1, color);
}

function drawSlot(g, x, y, width, color) {
  const n = Math.max(0, width - 2);
  if (!n) return;
  rect(g, x, y + 11, n, 1, color);
  rect(g, x, y + 9, 1, 2, color);
  rect(g, x + n - 1, y + 9, 1, 2, color);
}

/**
 * Draw the preview and report unknown glyphs.
 * Font switches select matching styles for subsequent runs.
 */
export function drawPreview(canvas, text, opts) {
  const geo = layoutBoxes(text, opts.window, opts.measure, opts.box, opts.shadow ?? null);
  const crowded = crowdedOf(geo, text, opts.window?.kind);
  const crowdedBoxes = new Set(crowded.map((b) => b.box));
  const over = geo.boxes.some((b) => b.lines.some((l) => l.over));
  canvas.width = Math.max(1, geo.width);
  canvas.height = Math.max(1, geo.height);
  canvas.style.width = `${canvas.width * opts.zoom}px`;
  canvas.style.height = `${canvas.height * opts.zoom}px`;
  const g = canvas.getContext("2d");
  g.clearRect(0, 0, canvas.width, canvas.height);
  const f = FRAMES[geo.frame];
  const colors = geo.frame === "battle" ? INKS.battle : INKS[opts.speaker] ?? INKS.plain;
  let unknown = 0;
  const cache = new Map();
  for (const [bi, b] of geo.frames.entries()) {
    const h = b.rows * LINE + 2 * PAD_Y;
    drawFrame(g, f, 0, b.y, geo.width, h);
    if (over && geo.limit > 0 && geo.width > geo.limit + 2 * PAD_X) {
      rect(g, PAD_X + geo.limit, b.y + 1, geo.width - geo.limit - PAD_X - 1, h - 2, OVER);
    }
    if (crowdedBoxes.has(bi + 1)) rect(g, 1, b.y + PAD_Y + b.room * LINE, geo.width - 2, (b.rows - b.room) * LINE, OVER);
    b.lines.forEach((l, i) => {
      let x = PAD_X;
      const y = b.y + PAD_Y + i * LINE;
      if (l.over && geo.limit > 0) rect(g, PAD_X + geo.limit, y, l.width - geo.limit, LINE, OVER);
      for (const r of l.runs) {
        if (r.kind === "slot") drawSlot(g, x, y, r.width, colors.slot);
        else if (r.kind === "glyph") {
          const source = (r.style && opts.styles?.[r.style]) || opts.glyphs;
          const gl = source.get(r.byte);
          if (gl.pixels) drawPixels(g, gl.pixels, x, y, r.width, colors);
          if (!gl.exact) {
            unknown++;
            if (!gl.pixels) {
              const name = standInName(r.text, r.byte, opts.measure.codec);
              const key = `${source.format}:${r.width}:${name}`;
              if (!cache.has(key)) cache.set(key, standIn(name, r.width, source.format === "8x16", opts.measure.codec.standIns));
              drawPixels(g, cache.get(key), x, y, r.width, colors);
            }
          }
        } else if (r.kind === "bad") {
          rect(g, x, y + 2, r.width - 1, 11, "#e02020");
        }
        x += r.width || 0;
      }
      if (l.waits) {
        drawArrow(g, PAD_X + l.width, y, l.arrow, opts.arrowColor ?? colors[INK]);
        if (l.crowded) rect(g, PAD_X + l.arrowLimit, y, l.width + l.arrow - l.arrowLimit, LINE, OVER);
      }
    });
    if (geo.limit > 0) {
      for (let j = b.y + 1; j < b.y + h - 1; j += 3) rect(g, PAD_X + geo.limit, j, 1, 2, "#e02020");
    }
  }
  return { ...geo, unknown, clipped: clippedOf(geo), crowded,
    arrowCut: arrowCutOf(geo) };
}

/** Find waiting lines that leave the prompt arrow no room. */
export function arrowCutOf(geo) {
  if (!(geo.arrow > 0)) return [];
  const out = [];
  geo.boxes.forEach((b, i) => b.lines.forEach((l, j) => {
    if (l.crowded) out.push({ box: i + 1, line: j + 1, text: l.text, width: l.width, room: l.arrowLimit - l.arrow,
      cut: l.width + l.arrow - l.arrowLimit });
  }));
  return out;
}

/** Find lines extending past the window width. */
export function clippedOf(geo) {
  if (!(geo.limit > 0)) return [];
  const out = [];
  geo.boxes.forEach((b, i) => b.lines.forEach((l, j) => {
    if (l.over) out.push({ box: i + 1, line: j + 1, text: l.text, width: l.width, clipped: l.width - geo.limit });
  }));
  return out;
}

/**
 * Find boxes exceeding the visible line count.
 * Scroll breaks let message boxes scroll instead.
 */
export function crowdedOf(geo, text = null, kind = null) {
  if (geo.runtimeScroll) return geo.frames.flatMap((b, i) => b.rows > b.room
    ? [{ box: i + 1, rows: b.lines.length, room: b.room }] : []);
  const pars = text !== null && (kind === "dialogue" || kind === "battle") ? text.split("\\p") : [];
  return geo.boxes.flatMap((b, i) => (b.rows > b.room && !(pars[i] ?? "").includes("\\l")
    ? [{ box: i + 1, rows: b.lines.length, room: b.room }] : []));
}

/** Describe how to fix preview layout problems. */
export function fixNote(geo) {
  const clipped = geo.clipped ?? clippedOf(geo);
  const crowded = geo.crowded ?? crowdedOf(geo);
  const cut = (geo.arrowCut ?? arrowCutOf(geo)).filter((c) => c.width <= geo.limit);
  const many = geo.boxes.length > 1;
  const notes = [];
  if (clipped.length) {
    const c = clipped.reduce((a, b) => (b.clipped > a.clipped ? b : a));
    const vars = { line: c.line, box: c.box, clipped: c.clipped, limit: geo.limit, n: clipped.length - 1 };
    notes.push(many
      ? t("Line {line} of box {box} is {clipped} px wider than the {limit} px window; the game cuts the marked part. Shorten the line or move a line break.", vars)
      : t("Line {line} is {clipped} px wider than the {limit} px window; the game cuts the marked part. Shorten the line or move a line break.", vars));
    if (clipped.length === 2) notes.push(t("{n} more line runs over too.", vars));
    else if (clipped.length > 2) notes.push(t("{n} more lines run over too.", vars));
  }
  for (const c of cut) {
    const vars = { ...c, limit: geo.limit, arrow: geo.boxes[c.box - 1].lines[c.line - 1].arrow };
    notes.push(many
      ? t("Line {line} of box {box} is {width} px; a line before a scroll or a new box may take {room} px, because the game draws the {arrow} px arrow that waits for a button right after it, and the window cuts {cut} px of the arrow. Move a word to the next line.", vars)
      : t("Line {line} is {width} px; a line before a scroll or a new box may take {room} px, because the game draws the {arrow} px arrow that waits for a button right after it, and the window cuts {cut} px of the arrow. Move a word to the next line.", vars));
    if (geo.consumer) notes.push(t("Window: {name}.", { name: whereText(limitText(geo.consumer)) }));
  }
  for (const c of crowded) {
    notes.push(many
      ? t("Box {box} has {rows} lines; the window shows {room}. Move text to a new box or shorten it.", c)
      : t("The text has {rows} lines; the window shows {room}. Move text to a new box or shorten it.", c));
  }
  return notes.join(" ");
}

// Rewrapped boxes retain their original check messages.
const DROPPED = /^written line wider than the window: box (\d+): (\d+) > (\d+) px: (['"])(.*?)\4; the build drops/;

/** Explain automatic rewrapping caused by an overlong written line. */
export function droppedNote(problems) {
  const m = (problems ?? []).map((p) => DROPPED.exec(p.message)).find(Boolean);
  if (!m) return "";
  const [, box, width, limit, , line] = m;
  return t("Your line breaks in box {box} are not used: the line “{line}” is {width} px, wider than the {limit} px window, so the game wraps the box anew. Shorten that line or move its break to keep your breaks.",
    { box, line, width, limit });
}
