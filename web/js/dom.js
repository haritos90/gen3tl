// DOM helpers for the pages.

const handlers = new WeakMap();

/** An element with props and children: text only, never HTML. */
export function el(tag, props = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") e.className = v;
    else if (k === "on") {
      handlers.set(e, v);
      for (const [ev, fn] of Object.entries(v)) e.addEventListener(ev, fn);
    }
    else if (k in e) e[k] = v;
    else e.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined) e.append(k);
  return e;
}

/** The style goes through the CSSOM: attribute styles are dropped. */
function setAttribute(node, name, value) {
  if (name === "style" && node.style) node.style.cssText = value;
  else node.setAttribute(name, value);
}

/** Keep existing text nodes and their selections. */
export function setText(node, text) {
  text = String(text ?? "");
  if (node.textContent === text) return;
  if (node.childNodes?.length === 1 && node.firstChild.nodeType === 3) node.firstChild.data = text;
  else node.textContent = text;
}

/** Reuse matching nodes while updating rendered content. */
export function updateChildren(parent, ...children) {
  if (!parent.childNodes || !parent.insertBefore) return parent.replaceChildren(...children);
  const nodes = children.flat().filter((node) => node != null).map((node) =>
    typeof node === "object" ? node : parent.ownerDocument.createTextNode(String(node)));
  const matches = (current, next) => current && current.nodeType === next.nodeType && current.nodeName === next.nodeName
    && (current.nodeType !== 1 || current.getAttribute("data-key") === next.getAttribute("data-key"));
  let current = parent.firstChild;
  for (const next of nodes) {
    if (current === next) { current = current.nextSibling; continue; }
    if (current && !matches(current, next)) {
      let match = current.nextSibling;
      while (match && !matches(match, next)) match = match.nextSibling;
      if (match) {
        while (current !== match) {
          const old = current;
          current = current.nextSibling;
          old.remove();
        }
      } else {
        parent.insertBefore(next, current);
        continue;
      }
    }
    if (!current) {
      parent.insertBefore(next, null);
      continue;
    }
    if (current.nodeType === 3) {
      if (current.data !== next.data) current.data = next.data;
    } else {
      const value = current.tagName === "SELECT" ? current.value : null;
      for (const attr of [...current.attributes]) {
        if (attr.name === "open" && current.tagName === "DETAILS") continue;
        if (!next.hasAttribute(attr.name)) current.removeAttribute(attr.name);
      }
      for (const attr of next.attributes) {
        if (current.getAttribute(attr.name) !== attr.value) setAttribute(current, attr.name, attr.value);
      }
      if (handlers.has(next)) {
        for (const [event, fn] of Object.entries(handlers.get(current) ?? {})) current.removeEventListener(event, fn);
        handlers.set(current, handlers.get(next));
        for (const [event, fn] of Object.entries(handlers.get(next))) current.addEventListener(event, fn);
      }
      updateChildren(current, ...next.childNodes);
      if (value != null) current.value = value;
    }
    current = current.nextSibling;
  }
  while (current) {
    const old = current;
    current = current.nextSibling;
    old.remove();
  }
}

/** Build content offscreen before updating its live nodes. */
export function renderChildren(target, render) {
  const draft = target.cloneNode(false);
  render(draft);
  for (const attr of [...target.attributes]) {
    if (!draft.hasAttribute(attr.name)) target.removeAttribute(attr.name);
  }
  for (const attr of draft.attributes) {
    if (target.getAttribute(attr.name) !== attr.value) setAttribute(target, attr.name, attr.value);
  }
  updateChildren(target, ...draft.childNodes);
}

/** Preserve reading positions across text layout changes. */
export function keepPosition(update, root = globalThis.document) {
  const win = root?.defaultView;
  const scroll = [...(root?.querySelectorAll?.("*") ?? [])].map((node) => [node, node.scrollLeft, node.scrollTop]);
  const x = win?.scrollX, y = win?.scrollY;
  const restore = () => {
    for (const [node, left, top] of scroll) {
      if (node.scrollLeft !== left) node.scrollLeft = left;
      if (node.scrollTop !== top) node.scrollTop = top;
    }
    if (win && (win.scrollX !== x || win.scrollY !== y)) win.scrollTo(x, y);
  };
  try {
    const result = update();
    if (typeof result?.finally === "function") return result.finally(restore);
    restore();
    return result;
  } catch (error) {
    restore();
    throw error;
  }
}

/** Save bytes as a file through a temporary link. */
export function download(bytes, name, type = "application/octet-stream") {
  const a = el("a", { href: URL.createObjectURL(new Blob([bytes], { type })), download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
