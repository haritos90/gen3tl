import { el, setText, updateChildren } from "../dom.js";
import { DATA_MODES, POCKETS } from "../pokedexdataview/items.js";
import { createCrossSearch, crossRow, listOrder } from "../pokedexdataview/search.js";
import { fillPage, fragmentOf, setLabelSource, t, uiLanguage } from "../ui.js";
import { BALLS, MOON_STONE_ID, familyItems, familyOf, renderCatchCalculator } from "../catchcalc.js";
import { MoveDialog } from "../movecard.js";
import { modes, recordIds, itemKeys } from "./species-data.js";
import { displayName, speciesFamilyIndex, baseSlot, speciesBrowsable, createSpeciesSearch, rowLabel, detailOf } from "./species-search.js";
import { sortSpeciesIndices, CATALOG_ID } from "./species-list.js";

export const withSpeciesLists = (Base) => class extends Base {


  /** Read the displayed sprite side and shiny state. */
  get side() {
    return this.variant.startsWith("back") ? "back" : "front";
  }


  get shiny() {
    return this.variant.endsWith("-shiny");
  }


  /** Choose front or back sprites with optional shiny palettes. */
  setVariant(variant) {
    this.variant = variant;
    for (const side of ["front", "back"]) {
      this.$(`x-${side}`).classList.toggle("on", side === this.side);
      this.$(`x-${side}`).setAttribute("aria-pressed", String(side === this.side));
    }
    this.$("x-shiny").classList.toggle("on", this.shiny);
    this.$("x-shiny").setAttribute("aria-pressed", String(this.shiny));
    if (this.cur != null) this.renderSprite(this.record());
  }


  setSide(side) {
    this.setVariant(`${side}${this.shiny ? "-shiny" : ""}`);
  }


  setShiny(on) {
    this.setVariant(`${this.side}${on ? "-shiny" : ""}`);
  }


  get records() {
    return this.host.data()?.species ?? [];
  }


  familyIndex() {
    const records = this.records;
    if (this.familyCache?.records !== records) this.familyCache = { records, index: speciesFamilyIndex(records) };
    return this.familyCache.index;
  }


  record(slot = this.cur) {
    if (!this.byId) this.byId = new Map(this.records.map((r, i) => [r.slot, i]));
    const i = this.byId.get(slot);
    return i === undefined ? null : this.records[i];
  }


  /** Reset datasets and texts when language or profile changes. */
  reset(want = null) {
    this.generation = (this.generation ?? 0) + 1;
    this.invalidateSearch();
    this.navigation = (this.navigation ?? 0) + 1;
    this.currentRoute = null;
    this.modeFilters = {};
    this.byId = null;
    this.familyCache = null;
    this.cur = null;
    this.filtered = [];
    this.mode = want && modes().includes(want.kind) && want.kind !== "species" ? want.kind : "species";
    this.dualType = want?.kind === "types" && want.id != null ? Number(want.id) : null;
    this.dataView.reset();
    if (DATA_MODES.includes(this.mode)) {
      this.dataView.mode = this.mode;
      this.dataView.want = want.id;
    } else {
      this.dataView.want = null;
    }
    this.want = this.mode === "species"
      ? want?.kind === "species" ? Number(want.id) : want?.kind ? Number(want.kind) : want
      : null;
    this.loaded = false;
    this.$("x-filters").replaceChildren();
    this.renderSaved();
    this.learnsets = null;
    this.moveFailure = null;
    this.texts = new Map();
    this.cross = [];
    this.crossNames = null;
    this.catalogs = null;
    this.catalogsLoad = null;
    if (this.variant !== "front") this.setVariant("front");
    this.list.setCount(0);
    this.list.space.replaceChildren();
    this.$("x-count").textContent = "";
    this.$("x-id").textContent = "";
    this.$("x-dex").textContent = "";
    this.$("x-name").hidden = true;
    this.$("x-name").textContent = "";
    this.$("x-sprite").replaceChildren();
    this.$("x-types").replaceChildren();
    this.$("x-abilities").replaceChildren();
    this.$("x-stats").replaceChildren();
    this.$("x-ev-yield").textContent = "";
    this.$("x-stats-legend").replaceChildren();
    for (const id of ["x-catch-rate", "x-base-exp", "x-growth", "x-egg-groups", "x-gender"]) {
      this.$(id).textContent = "";
    }
    this.$("x-height").textContent = "";
    this.$("x-weight").textContent = "";
    this.$("x-category").textContent = "";
    this.$("x-formes").replaceChildren();
    this.$("x-formes-block").hidden = true;
    this.$("x-evolutions").replaceChildren();
    this.$("x-evolutions-empty").textContent = "";
    this.$("x-locations").replaceChildren();
    this.$("x-locations-empty").textContent = "";
    this.$("x-catch").replaceChildren();
    this.$("x-catch-open").hidden = true;
    this.$("x-moves").replaceChildren();
    this.$("x-moves-empty").textContent = "";
    this.$("x-entry-text").replaceChildren();
    this.$("x-facts").replaceChildren();
    this.$("x-defense").replaceChildren();
    this.$("x-type-chart").replaceChildren();
    this.$("x-chart-empty").textContent = "";
    this.$("x-dual-pick").replaceChildren();
    this.$("x-dual-chart").replaceChildren();
    this._effectiveness = null;
    this._pairs = null;
    this._ranks = null;
    this.setMode(this.mode);
  }


  /** Show source text for draft catalogs. */
  sourceOnly() {
    return this.host.draft?.() ?? false;
  }


  /** Offer a capture calculator initialized with species values. */
  async renderCatch(record, found) {
    const button = this.$("x-catch-open");
    updateChildren(this.$("x-catch"));
    button.hidden = true;
    button.onclick = null;
    const data = this.host.data() ?? {};
    const pocket = Number(Object.keys(POCKETS).find((key) => POCKETS[key] === "Poké Balls"));
    const items = (data.items ?? []).filter((row) => row.pocket === pocket);
    if (!items.length || record.catch_rate == null) return;
    button.hidden = false;
    button.onclick = () => this.openCatch(record, found, items).catch(() => {});
  }


  /** Load ball names before opening the capture popup. */
  async openCatch(record, found, items) {
    const evolutionItems = familyItems(familyOf(record, this.records));
    const catalogItems = this.host.data()?.items ?? [];
    const itemIds = evolutionItems.flatMap((id) => itemKeys(id, catalogItems));
    const generation = this.generation;
    await Promise.all([...items.map((row) => row.name), ...itemIds].map((id) => this.dataView.ensure(id)));
    if (generation !== this.generation || this.cur !== record.slot) return;
    const english = (id) => this.texts.get(id)?.source_text ?? "";
    const order = [...BALLS.keys()];
    const balls = items.map((row) => ({ key: row.id, name: this.text(row.name) || english(row.name) }))
      .filter((ball) => BALLS.has(ball.key)).sort((a, b) => order.indexOf(a.key) - order.indexOf(b.key));
    const lows = found.map((row) => row.min).filter((n) => n > 0);
    const content = el("div", { class: "dex-catch" });
    renderCatchCalculator(content, {
      rate: record.catch_rate, baseHp: record.base_stats?.hp ?? 1, speed: record.base_stats?.speed ?? 0,
      weight: (record.weight ?? 0) / 10, types: [...new Set(record.types ?? [])],
      dex: record.dex, moonFamily: evolutionItems.includes(MOON_STONE_ID),
      level: lows.length ? Math.min(...lows) : 30, balls, locale: this.lang === "en" ? "en" : uiLanguage(),
    });
    this.moveDialog ??= new MoveDialog();
    if (this.moveDialog.available) this.moveDialog.show(content, t("Catch calculator"));
    else updateChildren(this.$("x-catch"), content);
  }


  /** Resolve map-section aliases to their underlying catalog identifiers. */
  catalogId(id) {
    if (!id?.startsWith("mapsec:")) return id;
    const section = Number(id.slice(7));
    return this.host.data()?.map_sections?.find((row) => row.id === section)?.name ?? "";
  }


  /** Keep untranslated objects readable through English source text. */
  text(id) {
    id = this.catalogId(id);
    if (!id) return "";
    const e = this.texts.get(id);
    if (!e) return "";
    if (this.lang !== "en" && e.translation) return e.translation;
    return (e.source_text ?? "").replaceAll("{PK}{MN}", "Pokémon");
  }


  /** Collect source, translated and alias names across search catalogs. */
  otherNames(id) {
    id = this.catalogId(id);
    if (!id) return [];
    const entry = this.texts.get(id);
    const names = [entry?.source_text, entry?.translation];
    for (const catalog of this.catalogs ?? []) names.push(catalog.texts[id]?.[1], ...(catalog.aliases[id] ?? []));
    return [...new Set(names.filter(Boolean))];
  }


  /** Load additional catalogs when a search needs them. */
  ensureCatalogs() {
    if (!this.host.searchTexts) return Promise.resolve(false);
    if (this.catalogsLoad) return this.catalogsLoad;
    const generation = this.generation;
    this.catalogsLoad = Promise.resolve().then(() => this.host.searchTexts()).then((catalogs) => {
      if (generation !== this.generation) return false;
      this.catalogs = catalogs;
      this.invalidateSearch();
      this.research();
      return true;
    }).catch((e) => {
      console.error(e);
      return false;
    });
    return this.catalogsLoad;
  }


  /** Repeat searches after additional names become available. */
  research() {
    if (!this.loaded) return;
    this.renderFilters();
    this.restoreFilters();
    if (this.mode === "species") {
      this.applyFilters();
      this.renderList();
    } else if (DATA_MODES.includes(this.mode) && this.dataView.loaded) {
      this.dataView.applyFilters();
      this.dataView.renderList();
    }
  }


  /**
   * Resolve page labels through mapped game strings.
   * Preserve symbols and fall back to untranslated English.
   */
  label(key) {
    const value = this.host.data()?.labels?.[key];
    if (value == null) { // Unmapped labels use their catalog, then the English key.
      const entry = this.host.pageLabels?.()?.[key];
      const page = Array.isArray(entry) ? (this.lang === "en" ? entry[0] : entry[1] || entry[0])
        : this.lang === "en" ? null : entry;
      return page ? { text: page, game: false } : null;
    }
    if (!CATALOG_ID.test(value)) return { text: value, game: false };
    const [id, rule] = value.split("@");
    const e = this.texts.get(id);
    const pick = (text) => (text && rule ? fragmentOf(text, rule) : text) || "";
    const text = e ? (this.lang === "en" ? pick(e.source_text) : pick(e.translation) || pick(e.source_text)) : "";
    if (!text) return null;
    // Sentence fragments also serve as standalone labels.
    const standalone = ["Flinch chance", "sleep", "paralysis", "burn", "poison"].includes(key);
    return { text, game: true, ...(standalone ? { standalone: true } : {}) };
  }


  /** Resolve labels through view texts and update markup. */
  useLabels() {
    setLabelSource((key) => this.label(key));
    this.fillLabels();
  }


  fillLabels() {
    if (typeof document !== "undefined" && typeof document.querySelectorAll === "function") fillPage();
    this.searchField(this.mode);
  }


  /** Check whether displayed text is untranslated source. */
  fallback(id) {
    id = this.catalogId(id);
    if (!id || this.lang === "en" || !this.sourceOnly()) return false;
    const e = this.texts.get(id);
    return !!e && !e.translation && !!e.source_text;
  }


  async load() {
    const generation = this.generation;
    try {
      await this.host.load();
      if (generation !== this.generation) return false;
    } catch (e) {
      if (generation !== this.generation) return false;
      setText(this.$("x-count"), t("No Pokedex dataset: {error}", { error: e.message || e }));
      return false;
    }
    if (!this.host.data()) {
      setText(this.$("x-count"), t("No Pokedex dataset for this profile."));
      return false;
    }
    const data = this.host.data();
    const items = data.items ?? [];
    const ids = [...new Set([...this.records.flatMap((record) => recordIds(record, items)),
      ...(data.types ?? []).map((row) => `types:${row.id}`),
      ...(data.abilities ?? []).flatMap((row) => [row.name, row.description, ...row.alternate_names ?? []]).filter(Boolean),
      ...(data.map_sections ?? []).map((row) => row.name).filter(Boolean),
      // Learnsets need both move and machine-item names.
      ...(data.moves ?? []).map((row) => row.name).filter(Boolean),
      ...items.filter((row) => row.tm_index != null).map((row) => row.name),
      ...(data.sources ?? []).filter((row) => row.item > 0).flatMap((row) => itemKeys(row.item, items)),
      ...(data.sources ?? []).map((row) => row.original_trainer).filter(Boolean),
      ...(data.sources ?? []).map((row) => row.speaker).filter(Boolean),
      // Move flags list item readers named in the item catalog.
      ...Object.values(data.move_readers ?? {}).flatMap((entry) => (entry?.readers?.items ?? []).flatMap((id) => itemKeys(id, items))),
      // Mapped page labels need their game strings loaded.
      ...Object.values(data.labels ?? {}).filter((value) => typeof value === "string" && CATALOG_ID.test(value))
        .map((value) => value.split("@")[0])])];
    await Promise.all(ids.map(async (id) => {
      try {
        const entry = await this.host.entry(this.catalogId(id));
        if (generation !== this.generation) return;
        this.texts.set(id, { translation: entry?.translation ?? "", source_text: entry?.source_text ?? "" });
      } catch {
        if (generation === this.generation) this.texts.set(id, { translation: "", source_text: "" });
      }
    }));
    this.useLabels();
    this.renderFilters(this.mode);
    this.restoreFilters(this.mode);
    const values = this.filterSets.species.values();
    if ((values.move || values.moveType) && this.learnsetsPending()) {
      await this.ensureLearnsets();
    }
    return generation === this.generation;
  }


  /** Load data before opening the requested card. */
  async show(slot = null) {
    if (!this.loaded) {
      setText(this.$("x-count"), t("Loading the Pokedex…"));
      if (!(await this.load())) return;
      this.loaded = true;
    }
    const kind = this.mode;
    const id = kind === "species" ? slot ?? this.want : kind === "types" ? this.dualType : this.dataView.want;
    this.want = null;
    this.dataView.want = null;
    try { return await this.goTo({ kind, id }); }
    catch (error) { setText(this.$("x-count"), t("Cannot show the species: {error}", { error: error.message })); }
  }


  applyFilters() {
    this.rememberFilters();
    const q = this.$("x-search").value;
    const set = this.filterSets.species;
    const raw = set.values();
    if ((q ?? "").trim() || raw.move) this.ensureCatalogs();
    const context = this.filterContext("species");
    const values = set.prepare(raw, context);
    const search = this.speciesSearch?.() ?? createSpeciesSearch(this.records, (id) => this.text(id), (id) => this.otherNames(id),
      (id) => this.searchAliases(id), true, this.familyIndex(), this.host.data()?.items ?? [], this.host.data()?.species_browsing);
    this.filtered = search.filter(q, (record) => set.matches(record, values, context));
    const ranks = new Map(this.filtered.map((i) => [i, search.rank(this.records[i], q)]));
    const collator = new Intl.Collator(this.lang === "en" ? "en" : uiLanguage(), { numeric: true, sensitivity: "base" });
    const sorted = (indices) => sortSpeciesIndices(indices, this.records, (id) => this.text(id), this.modeState("species").sort,
      this.lang === "en" ? "en" : uiLanguage(), collator, this.familyIndex());
    const strong = this.filtered.filter((i) => ranks.get(i) <= 1);
    this.filtered = [...sorted(strong.filter((i) => ranks.get(i) < 0)),
      ...sorted(strong.filter((i) => ranks.get(i) >= 0)), ...sorted(this.filtered.filter((i) => ranks.get(i) > 1))];
    this.strong = strong.length;
    this.order = listOrder(this.strong, this.filtered.length, []);
    const total = search.filter("").length;
    this.showCount(total);
    this.list.setCount(this.filtered.length);
    this.updateCross(q);
    set.refresh(raw, this.filtered.length, total);
  }


  showCount(total = this.records.filter((record) => speciesBrowsable(record, this.host.data()?.species_browsing)
    && (record.form_browsing?.separate || baseSlot(record, this.records, this.familyIndex()) === record.slot)).length) {
    setText(this.$("x-count"), t("{shown} of {total} species",
      { shown: this.filtered.length.toLocaleString(), total: total.toLocaleString() })
      + (this.lang !== "en" && this.sourceOnly() ? t(" · source shown where untranslated") : ""));
  }


  /** Load cross-category search names on first use. */
  updateCross(query, mode = "species", ownCount = this.filtered.length, strong = this.strong) {
    this.cross = [];
    if (!(query ?? "").trim()) return;
    const generation = this.generation, revision = this.searchRevision;
    this.ensureCrossNames().then(() => {
      if (generation !== this.generation || revision !== this.searchRevision
        || this.mode !== mode || this.$("x-search").value !== query) return;
      const search = this.crossSearch?.() ?? createCrossSearch(this.host.data() ?? {}, (id) => this.text(id),
        (id) => this.dataView.source(id), this.records, (id) => this.searchAliases(id), (id) => this.otherNames(id));
      this.cross = search.matches(query, mode);
      this.order = listOrder(strong ?? ownCount, ownCount, this.cross);
      this.list.setCount(ownCount + this.cross.length);
      this.renderList();
    }).catch(() => {});
  }


  /** Collect searchable names across all entry categories. */
  ensureCrossNames() {
    this.crossNames ??= (async () => {
      const data = this.host.data() ?? {};
      const ids = [...(data.moves ?? []).map((row) => row.name), ...(data.items ?? []).map((row) => row.name),
        ...(data.trainers ?? []).flatMap((row) => [row.name, row.class])].filter((id) => id && !this.texts.has(id));
      await Promise.all([...new Set(ids)].map((id) => this.dataView.ensure(id)));
    })();
    return this.crossNames;
  }


  /** Render filtered positions with Pokédex numbers and names. */
  row(p) {
    const at = this.order?.[p] ?? { own: p };
    if (at.cross != null) {
      return crossRow(this.cross[at.cross], p * this.list.row, p === 0 || this.order[p - 1].cross == null, (want) => this.goTo(want));
    }
    const record = this.records[this.filtered[at.own]];
    const { dex } = rowLabel(record, (id) => this.text(id));
    const name = displayName(record, this.records, (id) => this.text(id));
    return el("div", {
      class: `row${record.slot === this.cur ? " sel" : ""}`,
      role: "option",
      "data-key": `species:${record.slot}`,
      style: `top:${p * this.list.row}px`,
      on: { click: () => this.open(record.slot) },
    },
    el("span", { class: "dex-num" }, dex),
    el("span", {
      class: `dex-name-list${this.fallback(record.name) ? " muted" : ""}`,
      title: record.name,
    }, name));
  }


  /** Move selection relative to the filtered list. */
  step(delta) {
    if (this.mode !== "species") return this.dataView.step(delta);
    if (!this.filtered.length) return;
    const p = this.filtered.findIndex((i) => this.records[i].slot === this.cur);
    const next = this.filtered[(p < 0 ? 0 : p + delta + this.filtered.length) % this.filtered.length];
    this.open(this.records[next].slot);
  }


  renderList(scroll = false) {
    if (!scroll) return this.list.render();
    const own = this.model.position();
    return this.list.shown(own < 0 ? -1 : (this.model.order() ?? []).findIndex((at) => at.own === own));
  }


  /** Resolve the current sprite variant from dataset filenames. */
  renderSprite(record) {
    const variant = this.variant === "front-shiny" ? "shiny"
      : this.variant === "back-shiny" ? "back_shiny"
      : this.variant;
    const path = record.sprite?.[variant];
    const d = detailOf(record, (id) => this.text(id));
    updateChildren(this.$("x-sprite"), ...(path
      ? [el("img", { src: this.host.spriteSrc(path), alt: d.name || "", class: "dex-img" })]
      : []));
  }
};
