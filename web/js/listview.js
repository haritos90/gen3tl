import { updateChildren } from "./dom.js";

// The entry list's virtual rows.
// Only rows inside the scroll viewport are in the DOM.
// A hidden view renders nothing until shown() is called.

export const ROW = 58; // px, .row height
const OVERSCAN = 4; // rows above and below the viewport

export function orderRows(rows, indices, chunks = [], maps = {}, order = "relevance") {
  if (order === "file") return indices;
  const categories = chunks.map(({ file }) => {
    const path = file.replace(/^.*?po\//, "");
    if (path.startsWith("maps/")) return 0;
    if (path.startsWith("system/")) return 1;
    if (path.startsWith("tables/")) return 2;
    if (path === "battle.po" || path.startsWith("battle/")) return 4;
    return 3;
  });
  const ranks = indices.map((i) => {
    const row = rows[i], category = categories[row.chunk] ?? 3;
    const map = category === 0 ? maps[chunks[row.chunk].file] ?? Infinity : 0;
    return { i, never: row.reach === "never shown", category, map };
  });
  ranks.sort((a, b) => a.never - b.never || (a.never ? 0 : a.category - b.category || a.map - b.map) || a.i - b.i);
  return ranks.map(({ i }) => i);
}

/** The half-open row range [first, last) the viewport shows. */
export function visibleRange(top, viewport, count, row = ROW, overscan = OVERSCAN) {
  if (!(viewport > 0) || !(count > 0)) return [0, 0];
  // the browser clamps the scroll to the rows that exist
  const start = Math.min(Math.max(0, top), Math.max(0, count * row - viewport));
  const first = Math.max(0, Math.floor(start / row) - overscan);
  const last = Math.min(count, Math.ceil((start + viewport) / row) + overscan);
  return [first, last];
}

/** The rows of a filtered list in the list element. */
export class EntryList {
  constructor(list, space, rowOf, row = ROW) {
    this.list = list;
    this.space = space;
    this.rowOf = rowOf;
    this.row = row;
    this.count = 0;
    this.list.addEventListener("scroll", () => this.render());
    this.onPageScroll = () => this.render();
  }

  usePageScroll(page, insets = () => ({ top: 0, bottom: 0 })) {
    if (this.page !== page) {
      this.page?.removeEventListener("scroll", this.onPageScroll);
      page?.addEventListener("scroll", this.onPageScroll, { passive: true });
    }
    this.page = page;
    this.insets = insets;
  }

  get scrollTop() {
    return this.page ? this.page.scrollY : this.list.scrollTop;
  }

  set scrollTop(top) {
    if (this.page) this.page.scrollTo(this.page.scrollX, top);
    else this.list.scrollTop = top;
  }

  get bounds() {
    if (!this.page) return { top: this.list.scrollTop, height: this.list.clientHeight };
    if (!this.space.getClientRects().length) return { top: 0, height: 0 };
    const rect = this.space.getBoundingClientRect(), inset = this.insets();
    const start = Math.max(rect.top, inset.top);
    const end = Math.min(rect.bottom, this.page.innerHeight - inset.bottom);
    return { top: Math.max(0, start - rect.top), height: Math.max(0, end - start) };
  }

  reset() {
    if (!this.page) this.scrollTop = 0;
    else if (this.space.getClientRects().length) {
      const start = this.scrollTop + this.space.getBoundingClientRect().top - this.insets().top;
      this.scrollTop = Math.max(0, Math.min(this.scrollTop, start));
    }
  }

  /** Every row takes space: the scrollbar spans the catalog. */
  setCount(count) {
    this.count = count;
    this.space.style.height = `${count * this.row}px`;
  }

  /** The viewport height, zero while the Texts view is hidden. */
  get viewport() {
    return this.bounds.height;
  }

  /** Render the viewport rows; nothing while unmeasured. */
  render() {
    const { top, height } = this.bounds;
    const [first, last] = visibleRange(top, height, this.count, this.row);
    if (first === last) {
      // Empty results must clear stale rows
      if (this.count === 0) updateChildren(this.space);
      return [first, last];
    }
    const rows = [];
    for (let p = first; p < last; p++) rows.push(this.rowOf(p));
    updateChildren(this.space, ...rows);
    return [first, last];
  }

  /** After the view shows: scroll to a row, then render. */
  shown(pos = -1) {
    if (pos >= 0) this.scrollTo(pos);
    return this.render();
  }

  /** Scroll the least needed to bring a row into view. */
  scrollTo(pos) {
    if (this.page) {
      if (pos < 0 || !this.space.getClientRects().length) return;
      const rect = this.space.getBoundingClientRect(), inset = this.insets();
      const top = rect.top + pos * this.row;
      const bottom = this.page.innerHeight - inset.bottom;
      if (top < inset.top) this.scrollTop += top - inset.top;
      else if (top + this.row > bottom) this.scrollTop += top + this.row - bottom;
      return;
    }
    const top = this.list.scrollTop, viewport = this.viewport;
    if (!(viewport > 0) || pos < 0) return;
    if (pos * this.row < top) this.list.scrollTop = pos * this.row;
    else if ((pos + 1) * this.row > top + viewport) this.list.scrollTop = (pos + 1) * this.row - viewport;
  }
}

const EDITABLE = new Set(["INPUT", "TEXTAREA", "SELECT"]);

/** The list step of a vim key; 0 with focus. */
export function vimStep(e) {
  if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey || e.isComposing) return 0;
  const t = e.target;
  if (t && (t.isContentEditable || EDITABLE.has(t.tagName))) return 0;
  return e.code === "KeyJ" ? 1 : e.code === "KeyK" ? -1 : 0;
}
