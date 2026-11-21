import { el, updateChildren, setText } from "../dom.js";
import { t, formatNumber } from "../ui.js";
import { filterRows, mapsOf } from "../data.js";
import { orderRows } from "../listview.js";
import { option } from "./controls.js";
import { prepareRow } from "./game-text.js";
import { $, state } from "./state.js";
import { entries, openRow, renderList, setView } from "./texts.js";




export function applyFilters() {
  if (!state.edits) return;
  const selected = filters();
  if (selected.problems === "warnings") state.rows.forEach(prepareRow);
  state.filtered = orderRows(state.rows, filterRows(state.rows, selected, state.edits.map, state.groups),
    state.index?.chunks, state.index?.map_order, $("list-sort").value);
  const n = state.filtered.length;
  showListCount();
  entries.setCount(n);
  renderList();
}




export function fillFilters() {
  const file = $("f-file");
  const kept = [file.value, $("f-map").value];
  const files = [el("option", { value: "", textContent: t("All files") })];
  for (const g of state.groups) {
    const og = el("optgroup", { label: g.group });
    const n = g.files.reduce((s, f) => s + f.entries, 0);
    og.append(el("option", { value: `g:${g.group}`, textContent: t("All of {group} ({n})", { group: g.group, n }) }));
    for (const f of g.files) og.append(el("option", { value: `c:${f.chunk}`, textContent: `${f.name} (${f.entries})` }));
    files.push(og);
  }
  updateChildren(file, ...files);
  const map = $("f-map");
  updateChildren(map, el("option", { value: "", textContent: t("All maps") }),
    ...mapsOf(state.rows).map((m) => {
      const name = state.rows[state.byId.get(m)];
      return el("option", { value: m, textContent: name?.source_text || name?.translation || m });
    }));
  map.hidden = map.options.length < 2; // Game data names no maps
  for (const [select, value] of [[file, kept[0]], [map, kept[1]]]) { // Language changes must preserve the selected option.
    if ([...select.querySelectorAll("option")].some((o) => o.value === value)) select.value = value;
  }
}




export function filters() {
  const f = $("f-file").value;
  return {
    status: $("f-status").value,
    problems: $("f-problems").value,
    group: f.startsWith("g:") ? f.slice(2) : "",
    chunk: f.startsWith("c:") ? f.slice(2) : "",
    map: $("f-map").value,
    kind: $("f-kind").value,
    reach: $("f-reach").value,
    terms: $("f-terms").value,
    search: $("search").value,
  };
}




/** List the entries progress leaves out, with their reach evidence. */
export function listNeverShown() {
  if (!state.edits) return;
  $("f-reach").value = "never shown";
  setView("texts");
  applyFilters();
}




export function relabelFilters() {
  setText($("f-file").options[0], t("All files"));
  setText($("f-map").options[0], t("All maps"));
  for (const group of state.groups) {
    const option = [...$("f-file").options].find((node) => node.value === `g:${group.group}`);
    if (option) setText(option, t("All of {group} ({n})", {
      group: group.group, n: group.files.reduce((sum, file) => sum + file.entries, 0),
    }));
  }
}




export function showListCount() {
  setText($("count"), t("{n} of {total} entries", {
    n: formatNumber(state.filtered.length), total: formatNumber(state.rows.length),
  }));
}




export function sortList() {
  const top = $("list").scrollTop;
  applyFilters();
  $("list").scrollTop = top;
  renderList();
}




export function step(d) {
  if (!state.filtered.length) return;
  const p = state.filtered.indexOf(state.cur);
  const q = p < 0 ? 0 : Math.min(state.filtered.length - 1, Math.max(0, p + d));
  openRow(state.filtered[q]);
}
