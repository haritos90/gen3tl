import { el } from "./dom.js";
import { dexNumber, matchRank, speciesListFields, speciesNumber, speciesSort, STAT_LABELS } from "./pokedex/pokedex.js";
import { crossRow, listOrder } from "./pokedexdataview/pokedexdataview.js";
import { chartTypes } from "./typechart.js";
import { fillPage, siteText, t, uiLanguage } from "./ui.js";

const $ = (id) => document.getElementById(id);
const KINDS = ["species", "moves", "items", "trainers", "locations", "types"];
const LABELS = ["Species", "Moves", "Items", "Trainers", "Locations", "Type chart"];
const PATHS = {
  side: '<path d="M4 8a8 8 0 0 1 14-2l2 2M20 3v5h-5M20 16a8 8 0 0 1-14 2l-2-2M4 21v-5h5"/><circle cx="12" cy="12" r="3"/>',
  pin: '<path d="m15 3 6 6-3 1-4 4-1 4-3-3-6 6 6-6-3-3 4-1 4-4z"/>',
};

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  const attributes = {
    viewBox: "0 0 24 24", width: "24", height: "24", fill: "none", stroke: "currentColor",
    "stroke-width": "1.7", "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", focusable: "false",
  };
  for (const [key, value] of Object.entries(attributes)) svg.setAttribute(key, value);
  svg.innerHTML = PATHS[name];
  return svg;
}

export function configureListScroll(view, root = document, page = window) {
  const find = (id) => root.getElementById(id);
  const bar = find("c-list-bar"), search = find("c-list-search"), toggle = find("c-list-toggle");
  const field = find("x-search"), controls = find("c-list-controls"), clear = find("c-list-clear");
  const insets = () => ({
    top: root.querySelector(".mast").getBoundingClientRect().height + bar.getBoundingClientRect().height,
    bottom: page.innerWidth <= 760 ? find("c-rail").getBoundingClientRect().height : 0,
  });
  const sync = () => {
    bar.hidden = view.mode === "types";
    if (search.value !== field.value) search.value = field.value;
    clear.hidden = !search.value;
    toggle.textContent = t("Filters");
    toggle.setAttribute("aria-controls", `x-filter-block-${view.mode}`);
    toggle.setAttribute("aria-expanded", String(view.filterSets[view.mode]?.open ?? false));
  };
  const activate = view.activateMode.bind(view);
  view.activateMode = (mode) => {
    root.body.dataset.mode = mode;
    view.list.usePageScroll(mode === "types" ? null : page, insets);
    activate(mode);
    sync();
  };
  const renderFilters = view.renderFilters.bind(view);
  view.renderFilters = (...args) => {
    renderFilters(...args);
    const set = view.filterSets[view.mode];
    if (view.mode !== "types" && set?.details) {
      controls.replaceChildren(set.countNode, set.chipsNode, find(`x-filter-reset-${view.mode}`));
    }
    sync();
  };
  const syncSearch = view.syncSearchClear.bind(view);
  view.syncSearchClear = () => { syncSearch(); sync(); };
  search.addEventListener("input", () => {
    field.value = search.value;
    field.dispatchEvent(new page.Event("input", { bubbles: true }));
  });
  clear.addEventListener("click", () => {
    view.clearSearch();
    search.focus();
  });
  search.addEventListener("keydown", (event) => {
    if (!["Enter", "Escape"].includes(event.key)) return;
    event.preventDefault();
    if (event.key === "Escape") view.resetFilters();
    else field.dispatchEvent(new page.KeyboardEvent("keydown", { key: event.key, bubbles: true }));
  });
  toggle.addEventListener("click", () => {
    const set = view.filterSets[view.mode];
    set.open = !set.open;
    set.details.open = set.open;
    view.rememberFiltersOpen(view.mode, set.open);
    sync();
    // Reveal the form from a scrolled list.
    const top = bar.getBoundingClientRect().top;
    if (set.open && page.scrollY > 0) {
      const formTop = set.details.getBoundingClientRect().top - top - bar.getBoundingClientRect().height;
      page.scrollTo(page.scrollX, Math.max(0, page.scrollY + formTop));
    }
    view.list.render();
  });
  for (const set of Object.values(view.filterSets)) {
    const changed = set.host.onOpenChange;
    set.host.onOpenChange = (open) => { changed?.(open); sync(); view.list.render(); };
  }
  const observer = new page.ResizeObserver(() => view.list.render());
  for (const node of [bar, find("x-filters"), root.querySelector(".pagebar"), root.querySelector(".mast")]) observer.observe(node);
  view.list.usePageScroll(view.mode === "types" ? null : page, insets);
  sync();
  return sync;
}

function initialize() {
  if (globalThis.pokedexExplorer?.ready || !globalThis.pokedexPage?.ready) return;
  const { view, state } = globalThis.pokedexPage;
  const rail = el("aside", { id: "c-rail" });
  rail.append(el("div", { class: "c-brand-caption eyebrow", "data-ui": "Pokédex" }, "Pokédex"));
  const navigation = document.querySelector(".dex-entry-types");
  rail.append(navigation);
  rail.append(el("div", { class: "c-rail-game" }, state.profile.name));
  $("app").prepend(rail);
  const syncListBar = configureListScroll(view);
  for (const kind of KINDS) {
    const button = $(`x-mode-${kind}`);
    button.removeAttribute("data-ui");
    button.replaceChildren(el("span", { class: "c-nav-icon", "aria-hidden": "true" }),
      el("span", { "data-ui": LABELS[KINDS.indexOf(kind)], ...(kind === "types" ? { "data-ui-site": "" } : {}) }, LABELS[KINDS.indexOf(kind)]));
  }
  const close = $("c-close");
  const back = $("c-back");
  const readerTitle = $("c-reader-category");
  $("x-detail").tabIndex = -1;
  const typeMenu = el("details", { id: "c-type-menu", hidden: true }, el("summary"));
  $("c-recent").after(typeMenu);
  const mobileTypes = matchMedia("(max-width:760px)");

  const memory = new Map();
  let screen = "list";
  let intent = false;
  let origin = null;
  let returnRecent = null;
  const originalGo = view.goTo.bind(view);
  const heading = () => view.mode === "types" ? siteText("Type chart") : t(LABELS[KINDS.indexOf(view.mode)]);
  const title = $("c-page-title");

  function syncTypeMenu() {
    const active = view.mode === "types" && mobileTypes.matches;
    const query = $("x-search").value.trim();
    const pane = $("x-side");
    typeMenu.hidden = !active;
    typeMenu.dataset.search = String(!!query);
    typeMenu.firstElementChild.textContent = query ? $("x-search").getAttribute("aria-label")
      : `${t("Dual-type chart")} · ${view.cTypes?.length ?? chartTypes(view.host.data()?.types ?? []).length}`;
    if (active && pane.parentElement !== typeMenu) {
      typeMenu.open = screen === "list" && !!query;
      typeMenu.append(pane);
    } else if (!active && pane.parentElement === typeMenu) {
      $("c-type-overview").after(pane);
      typeMenu.open = false;
    }
  }

  function sync() {
    title.dataset.ui = LABELS[KINDS.indexOf(view.mode)];
    title.toggleAttribute("data-ui-site", view.mode === "types");
    title.textContent = heading();
    readerTitle.textContent = heading();
    const species = view.mode === "species" ? view.record(view.cur) : null;
    const id = view.mode === "species" ? null : view.currentId();
    $("x-id").textContent = species ? dexNumber(speciesNumber(species)) || "—"
      : id == null ? "—" : `#${String(id).padStart(3, "0")}`;
    document.body.dataset.mode = view.mode;
    $("c-type-overview").hidden = view.mode !== "types" || screen !== "list";
    $("x-filters").hidden = view.mode === "types";
    for (const kind of KINDS) $(`x-mode-${kind}`).setAttribute("aria-current", view.mode === kind ? "page" : "false");
    close.textContent = t("Close");
    back.setAttribute("aria-label", t("Go back"));
    back.hidden = screen !== "card" || history.state?.cBack !== true;
    syncTypeMenu();
    syncListBar();
  }

  typeMenu.addEventListener("toggle", () => requestAnimationFrame(() => view.list.render()));
  mobileTypes.addEventListener("change", () => {
    sync();
    requestAnimationFrame(() => view.list.render());
  });

  function listState() {
    return { mode: view.mode, top: view.list.scrollTop, key: document.activeElement?.closest(".row")?.dataset.key ?? null };
  }

  function listContext(mode) {
    if (origin?.mode === mode) return origin;
    const saved = memory.get(mode);
    if (saved) return saved;
    const top = history.state?.cMode === mode ? (mode === "types"
      ? history.state?.scroll?.panes?.["x-list"] : history.state?.scroll?.window) ?? 0 : 0;
    return { mode, top, key: null };
  }

  function setScreen(next, restore = false) {
    screen = next;
    const saved = restore ? listContext(view.mode) : null;
    const detailTop = restore ? history.state?.scroll?.panes?.["x-detail"] ?? 0 : 0;
    if (screen === "card") typeMenu.open = false;
    document.body.classList.toggle("c-reading", screen === "card");
    document.body.classList.toggle("c-list", screen === "list");
    $("x-side").inert = screen === "card";
    $("x-detail").inert = screen === "list";
    sync();
    if (screen === "list" && !typeMenu.hidden && $("x-search").value.trim()) typeMenu.open = true;
    requestAnimationFrame(() => {
      if (screen === "list") {
        if (saved?.mode === view.mode) view.list.scrollTop = saved.top;
        view.list.render();
        if (saved) {
          const row = saved.key ? document.querySelector(`#x-list .row[data-key="${CSS.escape(saved.key)}"]`) : null;
          const target = !typeMenu.hidden && !typeMenu.open ? typeMenu.firstElementChild : row ?? $("x-list");
          target.focus({ preventScroll: true });
        }
      } else {
        $("x-detail").scrollTop = detailTop;
        (back.hidden ? close : back).focus({ preventScroll: true });
      }
    });
  }

  function markHistory(cBack = false) {
    history.replaceState({ ...history.state, cScreen: screen, cMode: view.mode, cBack }, "", location.href);
  }

  function openCard(want) {
    intent = true;
    if (screen === "list") origin = listState();
    return view.goTo(want);
  }

  async function closeCard() {
    origin = listContext(view.mode);
    setScreen("list", true);
    markHistory();
    restoreRecent();
  }

  function restoreRecent() {
    if (!returnRecent) return;
    requestAnimationFrame(() => requestAnimationFrame(() => {
      if (screen !== "list" || !returnRecent) return;
      const { kind, id } = returnRecent;
      const button = document.querySelector(`#x-recent [data-entity="${kind}"][data-id="${CSS.escape(String(id))}"]`);
      $("c-recent").open = true;
      button?.focus({ preventScroll: true });
      returnRecent = null;
    }));
  }

  close.addEventListener("click", closeCard);
  back.addEventListener("click", () => {
    if (screen === "card" && history.state?.cBack === true) history.back();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && screen === "card" && !document.querySelector("dialog[open]")) {
      event.preventDefault();
      closeCard();
    }
    const row = event.target.closest?.("#x-list .row");
    if (row && ["Enter", " "].includes(event.key)) {
      event.preventDefault();
      row.click();
    }
  }, true);
  document.addEventListener("click", (event) => {
    const saved = event.target.closest("#x-recent .dex-saved-open");
    if (saved) returnRecent = { kind: saved.dataset.entity, id: saved.dataset.id };
    if (event.target.closest("#x-list .row,#x-favourites .dex-saved-open,#x-recent .dex-saved-open")) {
      intent = true;
      if (screen === "list") origin = { ...listState(), key: event.target.closest(".row")?.dataset.key ?? null };
    }
  }, true);
  view.goTo = async (want) => {
    const activation = intent;
    intent = false;
    const replay = state.navigating;
    const next = replay ? (history.state?.cScreen ?? (want?.id == null ? "list" : "card"))
      : want?.id == null ? "list" : activation || screen === "card" ? "card" : "list";
    const previous = location.hash;
    const previousMode = view.mode;
    const was = screen;
    const sameCard = next === "card" && was === "card" && want?.kind === previousMode
      && String(want.id) === String(view.currentId(previousMode));
    const restore = replay || sameCard && history.state?.cScreen != null;
    if (was === "list") memory.set(view.mode, listState());
    await originalGo(want);
    if (next === "card" && previousMode !== view.mode) origin = listContext(view.mode);
    setScreen(next, restore);
    if (!replay) {
      if (next === "card" && was === "list" && location.hash === previous) {
        history.pushState({ ...history.state, cScreen: "card", cMode: view.mode }, "", location.href);
      }
      const changed = location.hash !== previous;
      markHistory(changed ? next === "card" && was === "card" : history.state?.cBack === true);
    }
    sync();
    if (next === "list") {
      const saved = memory.get(view.mode);
      view.list.scrollTop = saved?.top ?? 0;
      view.list.render();
    }
    if (replay) restoreRecent();
  };

  const oldStep = view.step.bind(view);
  view.step = (delta) => {
    if (view.mode !== "types") return oldStep(delta);
    const ids = view.cTypes ?? [];
    if (!ids.length) return undefined;
    const current = ids.indexOf(view.dualType);
    return view.goTo({ kind: "types", id: ids[(Math.max(0, current) + delta + ids.length) % ids.length] });
  };
  for (const set of Object.values(view.filterSets)) {
    const changed = set.host.onOpenChange;
    set.host.onOpenChange = (open) => {
      if (set.details?.open !== open) {
        set.open = set.details?.open ?? false;
        return;
      }
      changed?.(open);
    };
  }
  const oldSearch = view.search.bind(view);
  view.search = () => {
    if (view.mode === "types") {
      view.rememberFilters();
      view.model.filter();
      view.list.reset();
      syncTypeMenu();
      if (!typeMenu.hidden) typeMenu.open = !!$("x-search").value.trim();
      view.list.render();
      return undefined;
    }
    return oldSearch();
  };
  $("x-search").addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    intent = true;
    if (screen === "list") origin = listState();
    if (view.mode === "types" && view.cTypes?.length) view.goTo({ kind: "types", id: view.cTypes[0] });
  }, true);

  const oldTypes = view.renderTypes.bind(view);
  const typeHeading = el("h2", { class: "c-type-heading" });
  $("x-chart-card").prepend(typeHeading);
  const typeSection = el("section", { class: "block c-type-species" });
  $("x-chart-card").append(typeSection);
  view.renderTypes = () => {
    oldTypes();
    const id = view.dualType;
    const name = view.text(`types:${id}`) || t("Type {n}", { n: id });
    typeHeading.replaceChildren(view.chip(id), el("span", {}, name));
    const records = view.records.filter((record) => record.types?.includes(id));
    const links = records.map((record) => el("button", {
      type: "button", class: "c-type-species-link", on: { click: () => view.openSpecies(record.slot) },
    }, record.sprite?.front ? el("img", { src: view.host.spriteSrc(record.sprite.front), alt: "", loading: "lazy" }) : null,
    el("span", {}, view.speciesName(record))));
    typeSection.replaceChildren(el("h2", {}, name, " · ", t("Species"), ` (${records.length})`),
      el("div", { class: "c-type-species-grid" }, ...links));
  };
  view.models.types = {
    card: "x-chart-card", rowHeight: 64,
    filter: () => {
      const rawQuery = $("x-search").value;
      const query = rawQuery.trim().toLowerCase();
      const names = (id) => [view.text(`types:${id}`), ...view.otherNames(`types:${id}`)];
      view.cTypes = chartTypes(view.host.data()?.types ?? []).filter((id) => !query
        || names(id).some((name) => String(name).toLowerCase().includes(query)));
      view.typeStrong = view.cTypes.filter((id) => matchRank(names(id), query) <= 1).length;
      view.order = listOrder(view.typeStrong, view.cTypes.length, []);
      view.list.setCount(view.cTypes.length);
      $("x-count").textContent = `${t("Dual-type chart")} · ${view.cTypes.length}`;
      view.filterSets.types.refresh({}, view.cTypes.length, chartTypes(view.host.data()?.types ?? []).length);
      view.updateCross(rawQuery, "types", view.cTypes.length, view.typeStrong);
      view.renderTypes();
    },
    row: (p) => {
      const at = view.order?.[p] ?? { own: p };
      if (at.cross != null) {
        return crossRow(view.cross[at.cross], p * 64, p === 0 || view.order[p - 1].cross == null, (want) => view.goTo(want));
      }
      const id = view.cTypes[at.own];
      const swatch = view.chip(id);
      swatch.textContent = "";
      swatch.classList.add("c-type-swatch");
      swatch.setAttribute("aria-hidden", "true");
      return el("div", {
        class: `row${id === view.dualType ? " sel" : ""}`, role: "option", tabIndex: 0,
        "data-key": `types:${id}`, style: `top:${p * 64}px`, on: { click: () => view.goTo({ kind: "types", id }) },
      }, el("span", { class: "dex-num" }, String(id).padStart(2, "0")), swatch,
      el("span", { class: "dex-name-list" }, view.text(`types:${id}`)), el("span", { class: "c-row-arrow", "aria-hidden": "true" }, "↗"));
    },
    select: (id) => ({ id: chartTypes(view.host.data()?.types ?? []).includes(Number(id)) ? Number(id) : view.cTypes?.[0] ?? 0 }),
    render: (record) => { view.dualType = record.id; view.renderTypes(); },
    id: (record) => record.id,
    position: () => view.cTypes?.indexOf(view.dualType) ?? -1,
    order: () => view.order ?? [],
  };

  const columns = () => [
    ["dex", siteText("ID"), t("Internal ID")], ["name", siteText("Name")],
    ["types", t("Types")], ["abilities", siteText("Abilities")], ["hp", t("HP")],
    ["attack", t("Atk")], ["defense", t("Def")], ["sp_attack", siteText("SpA")],
    ["sp_defense", siteText("SpD")], ["speed", siteText("Spe")], ["total", siteText("BST"), siteText("Base stat total")],
  ];
  const columnLabel = ([key, label, full]) => full ?? (STAT_LABELS[key] ? t(STAT_LABELS[key]) : label);
  const sortHeader = el("div", { id: "c-species-header", class: "c-species-grid", role: "group", "aria-label": siteText("Sort entries") });
  $("x-list").before(sortHeader);

  function updateSortControls() {
    const ordering = speciesSort(view.modeState("species").sort);
    const direction = ordering.direction === "asc" ? siteText("Ascending") : siteText("Descending");
    sortHeader.setAttribute("aria-label", siteText("Sort entries"));
    sortHeader.replaceChildren(...columns().map((column) => {
      const [key, label] = column;
      const active = ordering.key === key;
      return el("button", {
        type: "button", class: `c-species-col c-species-${key}`, "data-sort": key,
        title: columnLabel(column), "aria-pressed": String(active),
        "aria-label": active ? `${columnLabel(column)}: ${direction}` : columnLabel(column),
        on: { click: () => changeSort(key, active) },
      }, label, el("span", { "aria-hidden": "true" }, active ? ordering.direction === "asc" ? " ↑" : " ↓" : ""));
    }));
  }

  function changeSort(key, toggle = false) {
    const saved = view.modeState("species");
    const previous = speciesSort(saved.sort);
    saved.sort = speciesSort({ key, direction: toggle && previous.direction === "asc" ? "desc" : "asc" });
    view.host.memory?.()?.save();
    view.model.filter();
    view.list.reset();
    view.list.render();
    const focusedColumn = document.activeElement?.closest("#c-species-header button")?.dataset.sort;
    updateSortControls();
    if (focusedColumn) sortHeader.querySelector(`[data-sort="${focusedColumn}"]`)?.focus();
  }

  function speciesColumns(row, record) {
    const fields = speciesListFields(record, view.records, (id) => view.text(id));
    row.classList.add("c-species-grid", "c-species-row");
    row.replaceChildren(...columns().map(([key]) => {
      const cell = el("span", { class: `c-species-cell c-species-${key}` });
      if (key === "name") {
        if (record.sprite?.front) cell.append(el("img", { class: "c-row-thumb", src: view.host.spriteSrc(record.sprite.front), alt: "", loading: "lazy" }));
        cell.append(el("span", { class: "dex-name-list" }, fields.name));
      } else if (key === "types") {
        cell.append(...[...new Set(record.types ?? [])].map((type) => view.chip(type)));
      } else cell.textContent = fields[key] ?? "—";
      cell.title = fields[key] == null ? "" : String(fields[key]);
      return cell;
    }));
  }

  const oldRow = view.list.rowOf;
  view.list.rowOf = (p) => {
    const row = oldRow(p);
    if (!row) return row;
    row.tabIndex = 0;
    row.setAttribute("aria-selected", String(row.classList.contains("sel")));
    const [kind, id] = String(row.dataset.key).split(":");
    if (view.mode === "species" && kind === "species" && !row.classList.contains("cross")) {
      speciesColumns(row, view.record(Number(id)));
      return row;
    }
    if (kind === "species") {
      const record = view.record(Number(id));
      if (record?.sprite?.front) {
        row.insertBefore(el("img", { class: "c-row-thumb", src: view.host.spriteSrc(record.sprite.front), alt: "", loading: "lazy" }),
          row.querySelector(".dex-name-list"));
      }
      if (record?.types?.length) row.append(el("span", { class: "c-row-types" }, ...[...new Set(record.types)].map((type) => view.chip(type))));
    }
    if (!row.querySelector(".c-row-arrow")) row.append(el("span", { class: "c-row-arrow", "aria-hidden": "true" }, "↗"));
    return row;
  };

  function syncControls() {
    const side = $("c-sprite-side");
    side.replaceChildren(icon("side"));
    side.title = `${t("Sprite side")}: ${t(view.side === "front" ? "Front" : "Back")}`;
    side.setAttribute("aria-label", side.title);
    side.setAttribute("aria-pressed", String(view.side === "back"));
    $("x-shiny").textContent = view.shiny ? "★" : "☆";
    $("x-shiny").setAttribute("aria-label", t("Shiny colours"));
    $("x-pin").replaceChildren(icon("pin"));
  }

  function units(record) {
    for (const [id, key, unit] of [["x-height", "height", "meter"], ["x-weight", "weight", "kilogram"]]) {
      if (record?.[key] != null) {
        $(id).textContent = new Intl.NumberFormat(view.lang === "en" ? "en" : uiLanguage(), {
          style: "unit", unit, unitDisplay: "short", maximumFractionDigits: 1,
        }).format(record[key] / 10);
      }
    }
  }

  const nativeVariant = view.setVariant.bind(view);
  view.setVariant = (...args) => {
    const result = nativeVariant(...args);
    syncControls();
    return result;
  };
  const nativeSaved = view.renderSaved.bind(view);
  view.renderSaved = () => {
    nativeSaved();
    syncControls();
  };
  const nativeSpecies = view.renderSpecies.bind(view);
  view.renderSpecies = (slot, ...args) => {
    const result = nativeSpecies(slot, ...args);
    units(view.record(slot));
    syncControls();
    return result;
  };
  const nativeMoves = view.renderMoves.bind(view);
  view.renderMoves = (...args) => {
    const result = nativeMoves(...args);
    for (const group of $("x-moves").querySelectorAll(":scope > .dex-learn")) {
      if (group.tagName === "DETAILS") continue;
      const disclosure = el("details", { class: "dex-learn c-move-group" });
      const heading = group.querySelector("h3");
      disclosure.append(el("summary", {}, heading?.textContent ?? ""));
      heading?.remove();
      disclosure.append(...group.childNodes);
      group.replaceWith(disclosure);
    }
    return result;
  };
  const nativeLanguage = view.setLang.bind(view);
  view.setLang = (...args) => {
    const result = nativeLanguage(...args);
    sync();
    syncControls();
    units(view.record(view.cur));
    updateSortControls();
    if (view.mode === "types") {
      view.model.filter();
      view.list.render();
    }
    return result;
  };
  const oldFilters = view.filterOpen.bind(view);
  view.filterOpen = (mode) => {
    const saved = state.memory?.state.modes[mode];
    if (saved && !memory.has(mode)) saved.filtersOpen = false;
    return oldFilters(mode);
  };
  if (view.mode === "types") {
    view.model = view.models.types;
    view.model.filter();
  }
  for (const kind of KINDS) {
    view.filterSets[kind].open = false;
    const saved = state.memory?.state.modes[kind];
    if (saved) saved.filtersOpen = false;
  }
  view.renderFilters();
  view.restoreFilters();
  view.model.filter();
  sync();
  setScreen(history.state?.cScreen ?? (state.requestedEntry?.id == null ? "list" : "card"), history.state?.cScreen != null);
  markHistory(history.state?.cBack === true);
  fillPage();
  view.list.render();
  updateSortControls();
  syncControls();
  units(view.record(view.cur));
  globalThis.pokedexExplorer = {
    ready: true, refined: true, view, state,
    get screen() { return screen; },
    open: openCard, close: closeCard,
  };
  window.dispatchEvent(new Event("pokedex-explorer-ready"));
  window.dispatchEvent(new Event("pokedex-explorer-refined"));
}

if (typeof window !== "undefined") {
  window.addEventListener("pokedex-ready", initialize);
  initialize();
}
