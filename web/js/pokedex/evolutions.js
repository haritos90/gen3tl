import { el } from "../dom.js";
import { t } from "../ui.js";
import { against } from "../typechart.js";
import { itemText, itemName } from "./species-data.js";


export const FORM_CHANGE_METHODS = new Set([253, 254]);


/** Identify forward transformations from base to higher-slot forms. */
export function forward(edge, source) {
  return !FORM_CHANGE_METHODS.has(edge.method) || edge.target > source;
}


/**
 * Build evolution branches from the earliest ancestor.
 * Show normal evolutions first and omit reverse transformations.
 * Place each slot once, combining conditions for matching targets.
 */
export function evolutionTree(record, records) {
  if (record.evolution == null) return null;
  const bySlot = new Map(records.map((r) => [r.slot, r]));
  const parents = (slot) => records
    .filter((source) => (source.evolution ?? []).some((edge) => edge.target === slot && forward(edge, source.slot)))
    .map((source) => source.slot).sort((a, b) => a - b);
  let root = record.slot;
  const seen = new Set([root]);
  for (let parent = parents(root).find((slot) => !seen.has(slot)); parent !== undefined;
    parent = parents(root).find((slot) => !seen.has(slot))) {
    seen.add(parent);
    root = parent;
  }
  const placed = new Set();
  const node = (slot) => {
    placed.add(slot);
    const edges = [...(bySlot.get(slot)?.evolution ?? [])]
      .sort((a, b) => FORM_CHANGE_METHODS.has(a.method) - FORM_CHANGE_METHODS.has(b.method));
    const branches = [];
    for (const edge of edges) {
      if (!forward(edge, slot)) continue;
      const same = branches.find((branch) => branch.node.slot === edge.target);
      if (same) {
        same.edges.push(edge);
        continue;
      }
      if (placed.has(edge.target)) continue;
      branches.push({ edge, edges: [edge], node: node(edge.target) });
    }
    return { slot, branches };
  };
  return node(root);
}


/**
 * Place evolution stages in columns and leaves in rows.
 * Conditions precede targets; ancestors span their subtrees' rows.
 */
export function evolutionGrid(tree) {
  const rowsOf = (node) => (node.branches.length ? node.branches.reduce((n, b) => n + rowsOf(b.node), 0) : 1);
  const cells = [];
  let columns = 1;
  const place = (node, depth, row) => {
    cells.push({ kind: "species", slot: node.slot, column: 2 * depth + 1, row, span: rowsOf(node) });
    columns = Math.max(columns, 2 * depth + 1);
    let at = row;
    for (const branch of node.branches) {
      const span = rowsOf(branch.node);
      cells.push({ kind: "edge", from: node.slot, to: branch.node.slot, edges: branch.edges ?? [branch.edge],
        column: 2 * depth + 2, row: at, span });
      place(branch.node, depth + 1, at);
      at += span;
    }
  };
  place(tree, 0, 1);
  return { columns, rows: rowsOf(tree), cells };
}


/** Trace earliest-ancestor evolution paths, excluding form transformations. */
export function evolutionPaths(record, records) {
  if (record.evolution == null) return null;
  const parents = (slot) => records.flatMap((source) => (source.evolution ?? [])
    .filter((edge) => edge.target === slot && !FORM_CHANGE_METHODS.has(edge.method))
    .map((edge) => ({ ...edge, source: source.slot })));
  const walk = (slot, tail) => {
    if (tail.some((step) => step.slot === slot)) return [];
    const current = { slot, via: null };
    const incoming = parents(slot);
    if (!incoming.length) return [[current, ...tail]];
    return incoming.flatMap((edge) => walk(edge.source, [{ ...current, via: edge }, ...tail]));
  };
  const paths = walk(record.slot, []).filter((path) => path.length > 1);
  return [...new Map(paths.map((path) => [path.map((step) => step.slot).join(","), path])).values()];
}


/** Describe conditions using the engine's evolution method meanings. */
export function evolutionCondition(edge, text = () => "", items = [], joined = false) {
  const { method: m, parameter: p, extra: x } = edge;
  const item = (id) => itemName(id, text, items);
  const move = (id) => text(`moves:${id}`) || t("Move {n}", { n: id });
  const type = (id) => text(`types:${id}`) || t("Type {n}", { n: id });
  const species = (id) => text(`species:${id}`) || t("Species {n}", { n: id });
  switch (m) {
    case 1: return t("Friendship");
    case 2: return t("Friendship during the day");
    case 3: return t("Friendship at night");
    case 4: return t("Level {n}", { n: p });
    case 5: return t("Trade");
    case 6: return t("Trade holding {item}", { item: item(p) });
    case 7: return joined ? t("Use {item}", { item: item(p) }) : item(p);
    case 8: return t("Level {n}, Attack > Defense", { n: p });
    case 9: return t("Level {n}, Attack = Defense", { n: p });
    case 10: return t("Level {n}, Attack < Defense", { n: p });
    case 11: return t("Level {n}, Silcoon personality", { n: p });
    case 12: return t("Level {n}, Cascoon personality", { n: p });
    case 13: return t("Level {n}, Ninjask", { n: p });
    case 14: return t("Level {n}, Shedinja", { n: p });
    case 15: return t("Beauty {n}", { n: p });
    case 16: return t("Level {n} in rain or fog", { n: p });
    case 17: return t("Knows a {type} move", { type: type(p) }) + (x ? t(" with friendship") : "");
    case 18: return t("Level {n} with a {type}-type in the party", { n: p, type: type(x) });
    case 19: return t("At {place}", { place: text(`mapsec:${p}`) || t("map section {n}", { n: p }) });
    case 20: return t("Level {n} (male)", { n: p });
    case 21: return t("Level {n} (female)", { n: p });
    case 22: return t("Level {n} at night", { n: p });
    case 23: return t("Level {n} during the day", { n: p });
    case 24: return t("Level up holding {item} at night", { item: item(p) });
    case 25: return t("Level up holding {item} during the day", { item: item(p) });
    case 26: return t("Knows {move}", { move: move(p) });
    case 27: return t("With {species} in the party", { species: species(p) });
    case 28: return t("Level {n}, {from}:00–{to}:00", { n: p, from: x >> 8, to: x & 255 });
    case 29: return t("Flag {n} set", { n: p });
    case 30: return t("Land 3 critical hits in one battle");
    case 31: return t("Level {n}, high-key nature", { n: p });
    case 32: return t("Level {n}, low-key nature", { n: p });
    case 33: return t("Take {damage} damage at {place}", { damage: 49, place: text(`mapsec:${x}`) || t("map section {n}", { n: x }) });
    case 34: return edge.tile
      ? t("Use {item} on {tile}", { item: item(p), tile: t(edge.tile) })
      : t("Use {item} at tile behavior {n}", { item: item(p), n: x });
    case 35: return t("Level {n} holding {item}", { n: p, item: item(x) });
    case 36: return t("Use {item} while holding {item2}", { item: item(p), item2: item(x) });
    case 37: return t("Knows {move} (male)", { move: move(p) });
    case 38: return t("Knows {move} (female)", { move: move(p) });
    case 39: return t("Use {item} at night", { item: item(p) });
    case 253: return t("Giga");
    case 254:
      if (p && edge.parameter_kind === "move") return `${t("Mega")} · ${t("Knows {move}", { move: move(p) })}`;
      if (p && edge.kind === "primal") return t("Primal Reversion · {item}", { item: item(p) });
      if (p && edge.kind === "ultra") return t("Ultra Burst · {item}", { item: item(p) });
      return p ? t("Mega · {item}", { item: item(p) }) : t("Mega");
    default: return t("Method {m} · Parameter {p} · Extra {x}", { m, p, x });
  }
}


export function* formEvidence(record, records, index = null) {
  if (index) {
    yield* index.incoming.get(record.slot) ?? [];
    return;
  }
  for (const base of records) {
    if (base.slot >= record.slot) continue;
    for (const edge of base.evolution ?? []) if (edge.target === record.slot) yield { source: base.slot, edge };
  }
}


/** Resolve declared labels before generic evolution evidence. */
export function formLabel(record, records, text = () => "", index = null) {
  if (record.form_name) {
    const label = record.form_name.startsWith("page:") ? t(record.form_name.slice(5)) : text(record.form_name);
    if (label) return label;
  }
  const slots = index ? index.dexSlots.get(record.dex) : records.filter((r) => r.dex === record.dex).map((r) => r.slot);
  if (!(record.dex > 0) || (slots?.length ?? 0) < 2) return "";
  if (record.slot === Math.min(...slots)) return "";
  for (const { source, edge } of formEvidence(record, records, index)) {
    if (source >= record.slot) continue;
    if (edge.method === 253) return t("Giga");
    if (edge.method === 254 && edge.parameter > 0 && (!edge.kind || edge.kind === "mega")) {
      const letter = text(`items:${edge.parameter}`).match(/\s([XY])$/); // Stone suffixes distinguish otherwise identical Mega form labels.
      return letter ? `${t("Mega")} ${letter[1]}` : t("Mega");
    }
  }
  return t("Form {n}", { n: record.slot });
}


/** Collect evolution item identifiers according to method. */
export function edgeItems(edge) {
  const items = [];
  if ([6, 7, 24, 25, 34, 36, 39, 254].includes(edge.method) && edge.parameter > 0 && edge.parameter_kind !== "move") {
    items.push(edge.parameter);
  }
  if ([35, 36].includes(edge.method) && edge.extra > 0) items.push(edge.extra);
  return items;
}


/** Locate item icons when the dataset provides them. */
export function itemIcon(id, items = [], spriteSrc = (p) => p) {
  const icon = items.find((row) => row.id === id)?.icon;
  return icon ? el("img", { class: "dex-item-icon", alt: "", loading: "lazy", src: spriteSrc(icon) }) : null;
}


/** Link an item icon and name to its page. */
export function itemLink(id, text = () => "", items = [], spriteSrc = (p) => p, open = () => {}) {
  return el("button", {
    type: "button", class: "dex-link dex-item-link", title: `items:${id}`, on: { click: () => open(id) },
  }, itemIcon(id, items, spriteSrc), itemText(id, text, items) || `${t("Item")} ${id}`);
}


export const ITEM_MARK = "\u0000";


/** Split conditions into text, item and move links. */
export function conditionParts(edge, text = () => "", items = [], joined = false) {
  const declared = (id) => items.find((item) => item.name === id);
  const linked = (id) => id.startsWith("items:") || id.startsWith("moves:") || !!declared(id);
  const marked = evolutionCondition(edge, (id) => (linked(id) ? `${ITEM_MARK}${id}${ITEM_MARK}` : text(id)), items, joined);
  return marked.split(ITEM_MARK).filter(Boolean).map((part) => {
    if (part.startsWith("items:")) {
      const id = Number(part.slice(6));
      return { item: id, text: itemName(id, text, items) };
    }
    if (part.startsWith("moves:")) {
      const id = Number(part.slice(6));
      return { move: id, text: text(part) || t("Move {n}", { n: id }) };
    }
    const item = declared(part);
    return item ? { item: item.id, text: text(part) || t("Item {n}", { n: item.id }) } : { text: part };
  });
}


/** Find scripted species sources, excluding variable-species commands. */
export function speciesSources(sources, slot) {
  return (sources ?? []).filter((source) => source.species != null && source.species === slot);
}



/** Choose readable text against a chip's background. */
export function chipText(colour) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(colour.slice(i, i + 2), 16));
  return (0.299 * r + 0.587 * g + 0.114 * b) > 150 ? "#111" : "#fff";
}


/** Render translated type names using available game colors. */
export function typeChip(id, text, colours = [], extra = "") {
  const colour = colours.find((row) => row.id === id)?.colour;
  return el("span", {
    class: `chip ptype${extra}`, title: `types:${id}`,
    ...(colour ? { style: `--type:${colour};--type-fg:${chipText(colour)}` } : {}),
  }, text || t("Type {n}", { n: id }));
}
