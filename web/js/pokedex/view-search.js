import { getJson } from "../data.js";
import { decodeDataset } from "../pokedexstore.js";
import { el, keepPosition, setText, updateChildren } from "../dom.js";
import { EntryList } from "../listview.js";
import { DATA_MODES } from "../pokedexdataview/items.js";
import { PokedexDataView } from "../pokedexdataview/data-view.js";
import { createCrossSearch, relabelMatches } from "../pokedexdataview/search.js";
import { showPlaceholder, siteText, t } from "../ui.js";
import { FILTER_SETS, FilterSet } from "../filters.js";
import { MoveDialog, abilityCardContent, moveCardContent } from "../movecard.js";
import { chartTypes } from "../typechart.js";
import { DEX_ROW, modes, RECENT_MODES, inlineLearnsets } from "./species-data.js";
import { displayName, numberedName, speciesRowForms, recordBrowsable, publicRecords, createSpeciesSearch } from "./species-search.js";
import { withTypeCards } from "./view-types.js";
import { withSpeciesLists } from "./view-species.js";
import { withSpeciesCards } from "./view-card.js";

export class PokedexViewCore {
  constructor(host, $) {
    this.host = host;
    this.$ = $;
    this.generation = 0;
    this.searchRevision = 0;
    this.filtered = [];
    this.modeFilters = {};
    this.cur = null;
    this.variant = "front";
    this.lang = "translation";
    this.texts = new Map();
    this.list = new EntryList($("x-list"), $("x-list-space"), (p) => this.model.row(p), DEX_ROW);
    for (const side of ["front", "back"]) $(`x-${side}`).addEventListener("click", () => this.setSide(side));
    $("x-shiny").addEventListener("click", () => this.setShiny(!this.shiny));
    this.filterSets = Object.fromEntries(modes().map((mode) => [mode, new FilterSet(mode, FILTER_SETS[mode] ?? [], {
      container: $("x-filters"),
      onChange: () => this.filterChanged(),
      onReset: () => this.resetFilters(),
      onOpenChange: (open) => this.rememberFiltersOpen(mode, open),
    })]));
    $("x-search").addEventListener("input", () => {
      this.syncSearchClear();
      this.search();
    });
    $("x-search-clear")?.addEventListener("click", () => this.clearSearch());
    $("x-search").addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        this.clearSearch();
        return;
      }
      if (e.key !== "Enter") return;
      e.preventDefault();
      if (this.mode === "species") this.openFirst();
      else if (DATA_MODES.includes(this.mode)) this.dataView.openFirst();
    });
    this.dataView = new PokedexDataView(this, $);
    this.models = {
      species: {
        card: "x-species-card", rowHeight: DEX_ROW, row: (p) => this.row(p),
        filter: () => this.applyFilters(),
        select: (id) => this.record(id == null ? this.cur : Number(id)) ?? this.records[this.filtered[0]],
        render: (record) => this.renderSpecies(record.slot), id: (record) => record.slot,
        position: () => this.filtered.findIndex((i) => this.records[i].slot === this.cur),
        order: () => this.order,
      },
      ...Object.fromEntries(DATA_MODES.map((mode) => [mode, {
        card: "x-data-card", rowHeight: DEX_ROW, row: (p) => this.dataView.row(p),
        filter: () => this.dataView.applyFilters(),
        select: (id) => this.dataView.record(id ?? this.dataView.cur) ?? this.dataView.filtered[0],
        render: (record) => this.dataView.renderDetail(record.id), id: (record) => record.id,
        position: () => this.dataView.filtered.findIndex((row) => row.id === this.dataView.cur),
        order: () => this.dataView.order,
      }])),
      types: {
        card: "x-chart-card", rowHeight: DEX_ROW, row: () => null,
        filter: () => { this.list.setCount(0); setText(this.$("x-count"), ""); },
        select: (id) => {
          const types = chartTypes(this.host.data()?.types ?? []);
          return { id: types.includes(Number(id)) ? Number(id) : types[0] ?? 0 };
        },
        render: (record) => { this.dualType = record.id; this.renderTypes(); }, id: (record) => record.id,
        position: () => -1, order: () => [],
      },
    };
    this.learnsets = null; // Defer learnset loading until cards or filters need it.
    this.mode = "species";
    for (const mode of modes()) {
      $(`x-mode-${mode}`).addEventListener("click", () => this.setMode(mode));
    }
    $("x-type-chart").addEventListener("mouseover", (event) => this.highlightColumn(event));
    $("x-type-chart").addEventListener("mouseleave", () => this.highlightColumn(null));
    this.dualType = null;
    for (const id of ["x-pin", "x-data-pin", "x-types-pin"]) {
      $(id)?.addEventListener("click", () => this.toggleFavourite(this.currentId()));
    }
    $("c-sprite-side")?.addEventListener("click", () => this.setSide(this.side === "front" ? "back" : "front"));
    this.setMode("species");
  }


  setMode(mode) {
    if (this.loaded) return this.goTo({ kind: mode });
    this.activateMode(mode);
  }


  activateMode(mode) {
    const changed = this.mode !== mode;
    if (changed && this.loaded) this.rememberFilters(this.mode);
    this.mode = mode;
    this.model = this.models[mode];
    if (this.$("x-not-listed")) this.$("x-not-listed").hidden = true;
    this.list.row = this.model.rowHeight;
    for (const other of modes()) {
      this.$(`x-mode-${other}`).classList.toggle("on", other === mode);
      this.$(`x-mode-${other}`).setAttribute("aria-pressed", String(other === mode));
    }
    this.searchField(mode);
    for (const card of new Set(Object.values(this.models).map((model) => model.card))) {
      this.$(card).hidden = card !== this.model.card;
    }
    if (DATA_MODES.includes(mode)) this.dataView.setMode(mode);
    if (changed && this.loaded) {
      this.renderFilters(mode);
      this.restoreFilters(mode);
      this.list.setCount(0);
      this.list.render();
    }
  }


  /** Name the shared all-tab search. */
  searchField() {
    const field = this.$("x-search");
    showPlaceholder(field, "Search");
    field.setAttribute("aria-label", siteText("Search"));
  }


  syncSearchClear() {
    const button = this.$("x-search-clear");
    if (button) button.hidden = !this.$("x-search").value;
  }


  clearSearch() {
    this.resetFilters();
    this.$("x-search").focus();
  }


  /** Refilter the current tab as the query changes. */
  search() {
    if (this.mode === "species") {
      this.list.reset();
      this.applyFilters();
      this.renderList();
    } else if (DATA_MODES.includes(this.mode)) {
      this.dataView.search();
    } else if (this.mode === "types") {
      this.rememberFilters();
      this.renderTypes();
    }
  }


  invalidateSearch() {
    this.searchRevision += 1;
    this.speciesCache = null;
    this.crossCache = null;
    this.dataView.entryCache = null;
  }


  searchCacheCurrent(cache) {
    return !!cache && cache.revision === this.searchRevision && cache.texts === this.texts
      && cache.textsSize === this.texts.size && cache.lang === this.lang && cache.catalogs === this.catalogs
      && cache.aliases === this.host.searchAliases;
  }


  searchCacheFields() {
    return { revision: this.searchRevision, texts: this.texts, textsSize: this.texts.size,
      lang: this.lang, catalogs: this.catalogs, aliases: this.host.searchAliases };
  }


  speciesSearch() {
    const data = this.host.data() ?? {};
    const records = this.records, items = data.items ?? [], browsing = data.species_browsing;
    if (!this.searchCacheCurrent(this.speciesCache) || this.speciesCache.records !== records
      || this.speciesCache.items !== items || this.speciesCache.browsing !== browsing) {
      const familyIndex = this.familyIndex();
      const search = createSpeciesSearch(records, (id) => this.text(id), (id) => this.otherNames(id),
        (id) => this.searchAliases(id), true, familyIndex, items, browsing);
      this.speciesCache = { ...this.searchCacheFields(), records, items, browsing, search };
    }
    return this.speciesCache.search;
  }


  crossSearch() {
    const data = this.host.data() ?? {};
    if (!this.searchCacheCurrent(this.crossCache) || this.crossCache.data !== data) {
      const search = createCrossSearch(data, (id) => this.text(id), (id) => this.dataView.source(id), this.records,
        (id) => this.searchAliases(id), (id) => this.otherNames(id));
      this.crossCache = { ...this.searchCacheFields(), data, search };
    }
    return this.crossCache.search;
  }


  /** Open the first matching species on Enter. */
  openFirst() {
    const first = this.records[this.filtered[0]];
    if (first) this.open(first.slot);
    return !!first;
  }


  /** Open routed cards without reloading during history navigation. */
  goTo(want) {
    if (!this.loaded) {
      this.reset(want);
      return this.show();
    }
    const kind = modes().includes(want?.kind) ? want.kind : "species";
    const requested = want?.id ?? (want?.kind && !modes().includes(want.kind) ? want.kind : null);
    const id = requested ?? this.host.memory?.()?.state.modes[kind].last;
    const navigation = this.navigation = (this.navigation ?? 0) + 1;
    this.host.beforeOpen?.();
    this.moveDialog?.close();
    this.activateMode(kind);
    const finish = () => {
      if (navigation !== this.navigation) return;
      this.model.filter();
      if (id != null && !recordBrowsable({ id: Number(id) }, this.host.data()?.[`${kind}_browsing`])) {
        if (kind === "species") this.cur = Number(id);
        else this.dataView.cur = Number(id);
        this.currentRoute = `${kind}/${id}`;
        this.$(this.model.card).hidden = true;
        this.$("x-not-listed").hidden = false;
        this.host.setHash(this.currentRoute);
        this.renderList(true);
        return;
      }
      const card = this.model.select(id);
      if (card) {
        const route = `${kind}/${this.model.id(card)}`;
        if (route !== this.currentRoute) this.$("x-detail").scrollTop = 0;
        this.currentRoute = route;
        this.model.render(card);
        this.host.memory?.()?.open(kind, this.model.id(card));
        this.renderSaved();
        this.host.setHash(route);
      }
      this.renderList(true);
    };
    if (DATA_MODES.includes(kind) && !this.dataView.loaded) {
      return this.dataView.load().then((ok) => { if (ok) finish(); });
    }
    finish();
    return Promise.resolve();
  }


  /** Check whether external learnsets need loading. */
  learnsetsPending() {
    return typeof this.host.data()?.learnsets === "string";
  }


  /** Load external learnsets once and attach their rows. */
  ensureLearnsets() {
    if (this.learnsets) return this.learnsets;
    const data = this.host.data();
    if (!this.learnsetsPending() || !this.host.datasetFile) return Promise.resolve(!this.learnsetsPending());
    const generation = this.generation;
    this.learnsets = getJson(this.host.datasetFile(data.learnsets), this.host.fetcher ?? fetch).then((doc) => {
      if (generation !== this.generation) return false;
      inlineLearnsets(data, decodeDataset(doc));
      this.dataView.learners = null;
      return true;
    }).catch((e) => {
      console.error(e);
      this.learnsets = null;
      return false;
    });
    return this.learnsets;
  }


  /** Refilter species and select the first remaining match. */
  refilter() {
    // Move filters require learnsets before evaluating matches.
    const values = this.filterSets.species.values();
    if ((values.move || values.moveType) && this.learnsetsPending()) {
      this.ensureLearnsets().then((ok) => ok && this.refilter());
      return;
    }
    this.applyFilters();
    const current = this.record();
    if (this.filtered.length && (!current || !this.filtered.some((i) =>
      speciesRowForms(this.records[i], this.records, this.familyIndex()).some((forme) => forme.slot === current.slot)))) {
      this.open(this.records[this.filtered[0]].slot);
    }
    this.renderList();
  }


  /** Collect the data every registry predicate may read. */
  filterContext(mode = this.mode) {
    const data = this.host.data() ?? {};
    return {
      data, mode, types: data.types ?? [], moves: publicRecords(data, "moves"), species: publicRecords(data, "species"),
      abilities: publicRecords(data, "abilities"), items: publicRecords(data, "items"),
      text: (id) => this.text(id),
      source: (id) => this.dataView?.source(id) ?? this.texts.get(id)?.source_text ?? "",
      otherNames: (id) => this.otherNames(id),
      speciesName: (record) => this.speciesName(record),
      speciesNames: (record) => [this.speciesName(record) || this.text(record.name), ...this.otherNames(record.name)],
    };
  }


  /** Build the active mode's block and restore its memory. */
  renderFilters(mode = this.mode) {
    const set = this.filterSets[mode];
    if (!set) return;
    set.open = this.filterOpen(mode);
    set.render(this.filterContext(mode));
  }


  filterChanged() {
    if (!this.loaded) return;
    if (this.mode === "species") this.refilter();
    else if (DATA_MODES.includes(this.mode)) this.dataView.refilter();
    else if (this.mode === "types") this.renderTypes();
  }


  resetFilters() {
    this.$("x-search").value = "";
    this.syncSearchClear();
    this.search();
  }


  modeState(mode = this.mode) {
    return this.host.memory?.()?.state.modes[mode] ?? (this.modeFilters[mode] ??= {});
  }


  filterState(mode = this.mode) {
    const state = this.modeState(mode);
    return state.filters ??= {};
  }


  filterOpen(mode = this.mode) {
    return this.modeState(mode).filtersOpen !== false;
  }


  rememberFiltersOpen(mode, open) {
    const state = this.modeState(mode);
    state.filtersOpen = open;
    this.host.memory?.()?.save();
  }


  restoreFilters(mode = this.mode) {
    const saved = this.filterState(mode);
    this.$("x-search").value = saved.search ?? "";
    this.syncSearchClear();
    this.filterSets[mode]?.restore(saved);
  }


  rememberFilters(mode = this.mode) {
    if (!this.loaded) return;
    const saved = this.filterState(mode);
    saved.search = this.$("x-search").value;
    const entries = this.filterSets[mode]?.entries ?? [];
    for (const key of Object.keys(saved)) {
      if (key !== "search" && !entries.some((entry) => entry.key === key)) delete saved[key];
    }
    const values = this.filterSets[mode]?.values() ?? {};
    for (const entry of entries) {
      if (entry.key in values) saved[entry.key] = values[entry.key];
      else delete saved[entry.key];
    }
    this.host.memory?.()?.save();
    this.host.filtersChanged?.(mode);
  }


  currentId(mode = this.mode) {
    if (mode === "species") return this.cur;
    if (mode === "types") return this.dualType;
    return this.dataView.record(this.dataView.cur)?.id ?? null;
  }


  savedRecord(mode, id) {
    if (mode === "species") return this.record(id);
    if (mode === "types") return chartTypes(this.host.data()?.types ?? []).includes(Number(id)) ? { id: Number(id) } : null;
    return this.dataView.recordFor(mode, id);
  }


  savedName(mode, record) {
    if (mode === "species") return displayName(record, this.records, (id) => this.text(id));
    if (mode === "types") return this.text(`types:${record.id}`) || t("Type {n}", { n: record.id });
    return this.dataView.nameFor(mode, record);
  }


  toggleFavourite(id, mode = this.mode) {
    const memory = this.host.memory?.();
    if (!memory?.available || id == null || !this.savedRecord(mode, id)) return;
    memory.pin(mode, id);
    this.renderSaved();
  }


  savedChip(mode, id, pinned, showName = pinned) {
    const record = this.savedRecord(mode, id);
    const name = this.savedName(mode, record);
    const art = mode === "species" && record.sprite?.front ? el("img", {
      src: this.host.spriteSrc(record.sprite.front), alt: "", loading: "lazy",
    }) : mode === "types" ? this.chip(record.id) : mode === "species" ? null : this.dataView.artFor(mode, record);
    const open = el("button", {
      type: "button", class: "dex-saved-open", title: name, "aria-label": name,
      "aria-current": id === this.currentId(mode) ? "true" : "false",
      "data-entity": mode, "data-id": String(id),
      on: { click: () => this.goTo({ kind: mode, id }) },
    }, art, showName || !art ? el("span", {}, name) : null);
    return el("span", {
      class: `dex-saved-chip${id === this.currentId(mode) ? " cur" : ""}`, "data-key": `${mode}:${id}:${pinned ? "pin" : "recent"}`,
    }, open, pinned ? el("button", {
      type: "button", class: "dex-unpin", title: t("Unpin"), "aria-label": `${t("Unpin")}: ${name}`,
      on: { click: () => this.toggleFavourite(id, mode) },
    }, "×") : null);
  }


  renderSaved() {
    const memory = this.host.memory?.();
    const mode = this.mode;
    const saved = memory?.available && this.loaded ? memory.state.modes[mode] : null;
    const recents = RECENT_MODES.includes(mode);
    const favourites = (saved?.favourites ?? []).filter((id) => this.savedRecord(mode, id));
    const recent = recents ? (saved?.recent ?? []).filter((id) => this.savedRecord(mode, id)) : [];
    const strip = this.$("x-recent");
    if (strip) {
      strip.hidden = !recent.length;
      updateChildren(strip, ...recent.map((id) => this.savedChip(mode, id, false)));
    }
    if (this.$("c-recent")) this.$("c-recent").hidden = !recent.length;
    const current = this.currentId();
    const cardStrip = this.$("x-card-recent");
    if (cardStrip) {
      const ids = saved && recents && mode !== "types" ? [...recent] : [];
      if (saved && recents && mode !== "types" && current != null && this.savedRecord(mode, current)
        && !ids.includes(current)) ids.unshift(current);
      cardStrip.hidden = !ids.length;
      updateChildren(cardStrip, ...ids.map((id) => this.savedChip(mode, id, false, mode !== "species")));
      cardStrip.scrollLeft = 0;
      this.$("c-card-recent").hidden = !ids.length;
    }
    const row = this.$("x-favourites");
    if (row) {
      row.hidden = !favourites.length;
      updateChildren(row, ...(favourites.length ? [el("strong", {}, t("Favourites")),
        el("div", { class: "dex-saved-chips" }, ...favourites.map((id) => this.savedChip(mode, id, true)))] : []));
    }
    const activePin = { species: "x-pin", types: "x-types-pin" }[mode] ?? "x-data-pin";
    for (const id of ["x-pin", "x-data-pin", "x-types-pin"]) {
      const pin = this.$(id);
      if (!pin) continue;
      const pinned = saved?.favourites.includes(current) ?? false;
      pin.hidden = id !== activePin || !memory?.available || !this.loaded;
      pin.disabled = current == null;
      setText(pin, pinned ? "★" : "☆");
      pin.title = pinned ? t("Unpin") : t("Pin");
      pin.setAttribute("aria-label", pin.title);
      pin.setAttribute("aria-pressed", String(pinned));
    }
  }


  /** Switch between English source and catalog translations. */
  setLang(lang) {
    this.lang = lang;
    if (!this.loaded) return;
    return keepPosition(() => this.relabel());
  }


  /** Relabel loaded content without changing the current results. */
  relabel() {
    this.invalidateSearch();
    relabelMatches(this.cross, this);
    this.fillLabels();
    this.renderFilters();
    this.restoreFilters();
    if (this.mode === "species") this.applyFilters();
    this.renderSaved();
    if (this.mode === "species") {
      if (this.cur != null) this.renderSpecies(this.cur, false);
      this.renderList();
    } else if (this.mode === "types") {
      this.renderTypes();
    } else {
      this.dataView.relabel();
    }
  }


  /** Format numbered species names with form labels. */
  speciesName(record) {
    return record ? numberedName(record, this.records, (id) => this.text(id), this.familyIndex()) : "";
  }


  /** Read search metadata belonging to the opened catalog. */
  searchAliases(id) {
    return this.host.searchAliases?.(id) ?? [];
  }


  /** Open the dedicated page for a move. */
  gotoMove(id) {
    return this.goTo({ kind: "moves", id });
  }


  /** Open compact move cards, falling back to their pages. */
  openMove(id) {
    if (!recordBrowsable({ id: Number(id) }, this.host.data()?.moves_browsing)) return this.gotoMove(id);
    const record = (this.host.data()?.moves ?? [])[id];
    this.moveDialog ??= new MoveDialog();
    if (!record || !this.moveDialog.available) return this.gotoMove(id);
    const data = this.host.data();
    const show = () => this.moveDialog.show(moveCardContent(record, {
      text: (x) => this.text(x), types: data.types ?? [], readers: data.move_readers ?? {},
      categoryIcon: (category) => this.dataView.categoryIcon(category),
      link: (item) => this.dataView.itemLink(item), open: (move) => this.gotoMove(move),
      move: (move) => this.moveLink(move), location: (section) => this.dataView.sectionLink(section),
    }));
    show();
    const description = record.description;
    if (description && !this.texts.has(description)) { // Species cards initially load move names without descriptions.
      this.host.entry(this.catalogId(description)).then((entry) => {
        this.texts.set(description, { translation: entry?.translation ?? "", source_text: entry?.source_text ?? "" });
        if (this.moveDialog.node?.open) show();
      }).catch(() => {});
    }
    return Promise.resolve();
  }


  /** Render a move name opening its compact card. */
  moveLink(id) {
    return el("button", {
      type: "button", class: "dex-link", title: `moves:${id}`, on: { click: () => this.openMove(id) },
    }, this.text(`moves:${id}`) || t("Move {n}", { n: id }));
  }


  /** Keep displayed abilities tied to their species. */
  abilityRecord(species, id) {
    const base = (this.host.data()?.abilities ?? []).find((row) => row.id === id);
    if (!base) return null;
    const detail = this.record(species)?.ability_details?.find((row) => row.id === id);
    return { ...base, ...detail };
  }


  /** Share the existing card's modal behavior. */
  async openAbility(species, id) {
    const record = this.abilityRecord(species, id);
    this.moveDialog ??= new MoveDialog();
    if (!record || !this.moveDialog.available) return;
    const generation = this.generation;
    const content = abilityCardContent(record, (key) => this.text(key));
    this.moveDialog.show(content, t("Ability"));
    const missing = [record.name, record.description].filter((key) => key && !this.texts.has(key));
    await Promise.all(missing.map(async (key) => {
      try {
        const entry = await this.host.entry(this.catalogId(key));
        if (generation === this.generation) {
          this.texts.set(key, { translation: entry?.translation ?? "", source_text: entry?.source_text ?? "" });
        }
      } catch {}
    }));
    if (missing.length && generation === this.generation && this.moveDialog.node?.open
        && this.moveDialog.node.contains?.(content)) {
      this.moveDialog.show(abilityCardContent(record, (key) => this.text(key)), t("Ability"));
    }
  }


  /** Native buttons support keyboard and touch activation. */
  abilityLink(species, id) {
    const record = this.abilityRecord(species, id);
    return el("button", {
      type: "button", class: "dex-link dex-ability-link", "aria-haspopup": "dialog",
      disabled: !record, on: { click: () => this.openAbility(species, id) },
    }, this.text(record?.name) || t("Ability {n}", { n: id }));
  }
}

export class PokedexView extends withSpeciesCards(withSpeciesLists(withTypeCards(PokedexViewCore))) {}
