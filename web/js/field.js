// The translation field: breaks and tokens as units.
// Every edit goes through the units; the field renders it.
// A break is inserted only by a key or button.

import { History, enterBreak, erase, isBreak, join, normalize, parse, pasted, splice, tokenKind, typed } from "./text.js";
import { updateChildren } from "./dom.js";
import { N_, t } from "./ui.js";

const ZW = "\u200b"; // caret holder after a break
const COMPOSING = ["insertCompositionText", "insertFromComposition", "deleteCompositionText"];
export const BREAK_MARK = { "\\n": "↵", "\\l": "⤓", "\\p": "¶" };
export const BREAK_NAME = {
  "\\n": N_("line break (\\n)"),
  "\\l": N_("scrolling line break (\\l)"),
  "\\p": N_("new box (\\p)"),
};
const TOKEN_NAME = {
  placeholder: N_("inserted by the game"),
  control: N_("control code, no width"),
  symbol: N_("symbol"),
  unknown: N_("unknown token"),
};

/** Render units into `el`; editable adds caret holders. */
export function renderUnits(el, units, codec, editable = false) {
  const doc = el.ownerDocument;
  const draft = doc.createElement(el.tagName);
  let run = "";
  const flush = () => {
    if (run) draft.append(doc.createTextNode(run));
    run = "";
  };
  units.forEach((u, i) => {
    if (u.length === 1) {
      run += u;
      return;
    }
    flush();
    const s = doc.createElement("span");
    s.contentEditable = "false";
    s.dataset.u = u;
    if (isBreak(u)) {
      s.className = `brk brk-${u[1]}`;
      s.textContent = BREAK_MARK[u];
      s.title = t(BREAK_NAME[u]);
    } else {
      const kind = tokenKind(u, codec);
      s.className = `chip chip-${kind}`;
      s.textContent = u.slice(1, -1);
      s.title = `${u}: ${t(TOKEN_NAME[kind])}`;
    }
    draft.append(s);
    const next = units[i + 1];
    if (editable && isBreak(u) && (next === undefined || next.length > 1)) draft.append(doc.createTextNode(ZW));
  });
  flush();
  updateChildren(el, ...draft.childNodes);
}

const countText = (s) => s.split(ZW).join("").length;

function unitsOf(node) {
  return node.nodeType === 3 ? countText(node.data) : node.dataset?.u ? 1 : 0;
}

/** Unit offset of a DOM position inside `el`. */
export function offsetOf(el, node, off) {
  if (node === el) {
    let n = 0;
    for (let i = 0; i < off; i++) n += unitsOf(el.childNodes[i]);
    return n;
  }
  let child = node;
  while (child && child.parentNode !== el) child = child.parentNode;
  if (!child) return null;
  let n = 0;
  for (let c = el.firstChild; c !== child; c = c.nextSibling) n += unitsOf(c);
  if (child.nodeType === 3) return n + countText(child.data.slice(0, off));
  return n + (off === 0 && node === child.firstChild ? 0 : unitsOf(child));
}

/** DOM position of a unit offset inside `el`. */
export function positionOf(el, offset) {
  let rem = offset;
  const kids = [...el.childNodes];
  for (let i = 0; i < kids.length; i++) {
    const c = kids[i];
    if (c.nodeType === 3) {
      const n = countText(c.data);
      if (rem <= n) {
        let k = 0;
        for (let seen = 0; seen < rem; k++) if (c.data[k] !== ZW) seen++;
        return [c, k];
      }
      rem -= n;
    } else {
      if (rem === 0) return [el, i];
      rem -= unitsOf(c);
    }
  }
  return [el, kids.length];
}

/** Units read back from the DOM (after IME input). */
export function readUnits(el) {
  const out = [];
  const walk = (node) => {
    for (const c of node.childNodes) {
      if (c.nodeType === 3) out.push(...typed(c.data.split(ZW).join("")));
      else if (c.dataset?.u) out.push(c.dataset.u);
      else walk(c);
    }
  };
  walk(el);
  return out;
}

export class Field {
  /** opts: {codec, kind: () => window kind, onChange(text)} */
  constructor(el, opts) {
    this.el = el;
    this.opts = opts;
    this.units = [];
    this.history = new History();
    el.contentEditable = "true";
    el.spellcheck = true;
    el.addEventListener("beforeinput", (e) => this.beforeInput(e));
    el.addEventListener("keydown", (e) => this.keyDown(e));
    el.addEventListener("paste", (e) => this.paste(e));
    el.addEventListener("copy", (e) => this.copy(e, false));
    el.addEventListener("cut", (e) => this.copy(e, true));
    el.addEventListener("drop", (e) => e.preventDefault());
    el.addEventListener("compositionend", () => this.resync());
    el.addEventListener("input", (e) => {
      if (COMPOSING.includes(e.inputType) && !e.isComposing) this.resync();
    });
  }

  setReadOnly(value) {
    this.readOnly = Boolean(value);
    this.el.contentEditable = String(!this.readOnly);
    this.el.setAttribute("aria-readonly", String(this.readOnly));
  }

  get text() {
    return join(this.units);
  }

  /** Show a text; each entry has its own history */
  set(text, fresh = true) {
    if (fresh) this.history.clear();
    this.units = parse(text);
    renderUnits(this.el, this.units, this.opts.codec, true);
  }

  selection() {
    const sel = this.el.ownerDocument.getSelection();
    if (!sel || !sel.rangeCount) return [this.units.length, this.units.length];
    const r = sel.getRangeAt(0);
    if (!this.el.contains(r.startContainer) && r.startContainer !== this.el) {
      return [this.units.length, this.units.length];
    }
    const a = offsetOf(this.el, r.startContainer, r.startOffset) ?? this.units.length;
    const b = offsetOf(this.el, r.endContainer, r.endOffset) ?? a;
    return [Math.min(a, b), Math.max(a, b)];
  }

  setCaret(offset) {
    const sel = this.el.ownerDocument.getSelection();
    const [node, off] = positionOf(this.el, offset);
    sel.collapse(node, off);
  }

  /** Apply fn(units, start, end) -> {units, caret}. */
  change(fn, kind = null) {
    if (this.readOnly) return;
    const [s, e] = this.selection();
    const out = fn(this.units, s, e);
    if (!out) return;
    const res = normalize(out.units, out.caret);
    if (join(res.units) === this.text && res.caret === s) return; // nothing to do
    this.history.record({ units: this.units, caret: s }, kind);
    this.units = res.units;
    renderUnits(this.el, this.units, this.opts.codec, true);
    this.setCaret(res.caret);
    this.opts.onChange(this.text);
  }

  insert(units, kind = null) {
    this.el.focus();
    this.change((u, s, e) => splice(u, s, e, units), kind);
  }

  insertBreak(b) {
    this.insert([b]);
  }

  /** Replace the whole text, as one undo step. */
  replace(text) {
    const units = parse(text);
    this.change(() => ({ units, caret: units.length }));
  }

  /** Enter: next line (text.enterBreak). */
  enter() {
    const [s] = this.selection();
    this.insertBreak(enterBreak(this.units, s, this.opts.kind()));
  }

  restore(state) {
    if (!state || this.readOnly) return;
    this.units = state.units;
    renderUnits(this.el, this.units, this.opts.codec, true);
    this.setCaret(state.caret);
    this.opts.onChange(this.text);
  }

  undo() {
    this.restore(this.history.undo({ units: this.units, caret: this.selection()[0] }));
  }

  redo() {
    this.restore(this.history.redo({ units: this.units, caret: this.selection()[0] }));
  }

  keyDown(e) {
    if (e.isComposing) return;
    const mod = e.ctrlKey || e.metaKey;
    if (e.key === "Enter" && !mod) {
      e.preventDefault();
      if (e.shiftKey) this.insertBreak("\\p");
      else if (e.altKey) this.insertBreak("\\l");
      else this.enter();
    } else if (mod && !e.altKey && e.key.toLowerCase() === "z") {
      e.preventDefault();
      if (e.shiftKey) this.redo();
      else this.undo();
    } else if (mod && !e.altKey && e.key.toLowerCase() === "y") {
      e.preventDefault();
      this.redo();
    }
  }

  beforeInput(e) {
    const t = e.inputType;
    if (this.readOnly) { e.preventDefault(); return; }
    if (COMPOSING.includes(t)) return; // IME: resync at the end
    e.preventDefault();
    const data = e.data ?? e.dataTransfer?.getData("text/plain") ?? "";
    if (t === "insertText" || t === "insertReplacementText") {
      this.change((u, s, en) => splice(u, s, en, typed(data)), data === " " ? "space" : "type");
    } else if (t === "insertParagraph" || t === "insertLineBreak") this.enter();
    else if (t === "historyUndo") this.undo();
    else if (t === "historyRedo") this.redo();
    else if (t.startsWith("delete")) {
      const forward = t.endsWith("Forward");
      const word = t.includes("Word") || t.includes("Line");
      this.change((u, s, en) => erase(u, s, en, forward, word), "delete");
    }
    // other edits (formatting, drops) are refused
  }

  paste(e) {
    e.preventDefault();
    const text = e.clipboardData?.getData("text/plain") ?? "";
    if (text) this.insert(pasted(text));
  }

  copy(e, cut) {
    const [s, en] = this.selection();
    if (s === en) return;
    e.preventDefault();
    e.clipboardData.setData("text/plain", join(this.units.slice(s, en)));
    if (cut) this.change((u, a, b) => erase(u, a, b));
  }

  resync() {
    if (this.readOnly) { this.set(this.text, false); return; }
    const [s] = this.selection();
    const before = this.text;
    this.units = readUnits(this.el);
    renderUnits(this.el, this.units, this.opts.codec, true);
    this.setCaret(Math.min(s, this.units.length));
    if (this.text !== before) this.opts.onChange(this.text);
  }
}
