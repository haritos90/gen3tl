import { getJson } from "../data.js";
import { decodeDataset } from "../pokedexstore.js";
import { el, setText, updateChildren } from "../dom.js";
import { effectName } from "../moveeffects.js";
import { itemIcon } from "../pokedex/evolutions.js";
import { itemKeys } from "../pokedex/species-data.js";
import { publicRecords } from "../pokedex/species-search.js";
import { t } from "../ui.js";
import { DATA_MODES, ROW, PICKUP_KINDS } from "./items.js";
import { listOrder, crossRow, relabelMatches, createEntrySearch } from "./search.js";
import { inlineTrainerIvs, INVERSE_BATTLE_MAPS, INVERSE_BATTLE_SIGN, trainerName, trainerGroups } from "./trainers.js";
import { COUNT_LABELS, dataSectionGroups } from "./locations.js";
import { withItemAndMoveCards } from "./item-view.js";
import { withTrainerCards } from "./trainer-view.js";
import { withLocationCards } from "./location-view.js";

export class PokedexDataViewCore {
  constructor(view, $) {
    this.view = view;
    this.host = view.host;
    this.$ = $;
    this.mode = "moves";
    this.filtered = [];
    this.cur = null;
    this.loaded = false;
    this.expandedPartyMembers = new Set();
  }


  get list() {
    return this.view.list;
  }


  /** Follow the search query and open the first match. */
  search() {
    this.list.reset();
    this.applyFilters();
    if (this.filtered.length) this.open(this.filtered[0].id);
    this.renderList(); // Empty results must also refresh the list.
  }


  /** Open the first remaining record on Enter. */
  openFirst() {
    const first = this.filtered[0];
    if (first) this.open(first.id);
    return !!first;
  }


  /** Refilter changed criteria and select a remaining record. */
  refilter() {
    this.applyFilters();
    if (this.filtered.length && !this.filtered.some((row) => row.id === this.cur)) this.open(this.filtered[0].id);
    this.renderList();
  }


  /** Read source text for language-independent grouping and search. */
  source(id) {
    return this.view.texts?.get(id)?.source_text ?? "";
  }


  reset() {
    this.itemSpotObserver?.disconnect();
    this.itemSpotObserver = null;
    this.loaded = false;
    this.cur = null;
    this.want = null;
    this.filtered = [];
    this.groups = null;
    this.entryCache = null;
    this.learners = null;
    this.learnerFailure = null;
    this.difficulty = null;
    this.expandedPartyMembers.clear();
    this.list.setCount(0);
    this.list.space.replaceChildren();
    this.$("x-count").textContent = "";
    this.$("x-data-title").hidden = true;
    this.$("x-data-title").textContent = "";
    this.$("x-data-id").textContent = "";
    this.$("x-data-content").replaceChildren();
  }


  get data() {
    return this.host.data() ?? {};
  }


  get records() {
    if (this.mode === "items" || this.mode === "moves") {
      this.groups ??= publicRecords(this.data, this.mode);
      return this.groups;
    }
    if (this.mode === "trainers") {
      const label = (id) => this.view.text(id) || this.source(id) || id;
      this.groups ??= trainerGroups(this.data.trainers ?? [], (id) => this.source(id))
        .sort((a, b) => (a.average_level ?? Infinity) - (b.average_level ?? Infinity)
          || label(a.class).localeCompare(label(b.class))
          || trainerName(a, (id) => this.view.text(id), (id) => this.source(id))
            .localeCompare(trainerName(b, (id) => this.view.text(id), (id) => this.source(id))));
      return this.groups;
    }
    if (this.mode === "locations") {
      const label = (row) => this.view.text(row.name) || t("Area {n}", { n: row.id });
      this.groups ??= dataSectionGroups(this.data)
        .sort((a, b) => label(a).localeCompare(label(b), undefined, { numeric: true }));
      return this.groups;
    }
    return this.data.moves ?? [];
  }


  /** Find records, grouping trainer identifiers with their battles. */
  record(id = this.cur) {
    if (id == null) return null;
    const key = this.mode === "locations" ? id : Number(id);
    if (this.mode === "trainers") {
      return this.records.find((group) => group.battles.some((battle) => battle.id === key)) ?? null;
    }
    if (this.mode === "locations") { // Area links remain valid through their parent sections.
      return this.records.find((group) => group.id === key || group.areas.some((area) => area.id === key)) ?? null;
    }
    return this.records.find((row) => row.id === key) ?? null;
  }


  recordFor(mode, id) {
    if (id == null) return null;
    const key = mode === "locations" ? id : Number(id);
    if (mode === "trainers") {
      return trainerGroups(this.data.trainers ?? [], (name) => this.source(name))
        .find((group) => group.battles.some((battle) => battle.id === key)) ?? null;
    }
    if (mode === "locations") {
      return dataSectionGroups(this.data)
        .find((group) => group.id === key || group.areas.some((area) => area.id === key)) ?? null;
    }
    return (this.data[mode] ?? []).find((row) => row.id === key) ?? null;
  }


  nameFor(mode, record) {
    if (mode === "trainers") return trainerName(record, (id) => this.view.text(id) || this.source(id), (id) => this.source(id));
    return this.view.text(record.name) || this.source(record.name)
      || (mode === "locations" ? t("Area {n}", { n: record.id }) : record.name);
  }


  artFor(mode, record) {
    if (mode === "items") return itemIcon(record.id, [record], (path) => this.host.spriteSrc(path));
    if (mode === "trainers") return this.trainerSprite(record.battles[0]);
    if (mode === "locations") return record.areas.length ? this.areaImage(record.areas[0]) : null;
    return this.categoryIcon(record.damage_category);
  }


  async ensure(id) {
    if (!id || this.view.texts.has(id)) return;
    const entry = await this.host.entry(this.view.catalogId(id));
    this.view.texts.set(id, { translation: entry?.translation ?? "", source_text: entry?.source_text ?? "" });
  }


  async loadTrainerIvs() {
    const data = this.data, generation = this.view.generation;
    const current = () => this.data === data && this.view.generation === generation;
    this.trainerIvsFailed = false;
    try {
      if (data.trainerIvMembers instanceof Map) return;
      if (typeof data.trainer_ivs === "object" && data.trainer_ivs !== null) {
        inlineTrainerIvs(data, data.trainer_ivs);
        return;
      }
      if (typeof data.trainer_ivs !== "string" || !this.host.datasetFile) {
        this.trainerIvsFailed = true;
        return;
      }
      const doc = decodeDataset(await getJson(this.host.datasetFile(data.trainer_ivs), this.host.fetcher ?? fetch));
      if (!current()) return;
      inlineTrainerIvs(data, doc);
    } catch {
      if (!current()) return;
      data.trainerIvMembers = undefined;
      if (typeof data.trainer_ivs === "object") data.trainer_ivs = null;
      this.trainerIvsFailed = true;
    }
  }


  async load() {
    const generation = this.view.generation, mode = this.mode, navigation = this.view.navigation;
    const ids = [];
    if (this.mode === "moves") {
      for (const row of this.data.moves ?? []) ids.push(row.name, row.description);
      for (const row of this.data.species ?? []) ids.push(row.name);
      for (const entry of Object.values(this.data.move_readers ?? {})) {
        for (const item of entry?.readers?.items ?? []) ids.push(...itemKeys(item, this.data.items ?? []));
      }
    } else if (this.mode === "items") {
      for (const row of this.data.items ?? []) {
        ids.push(row.name, row.description, row.taught_move == null ? null : `moves:${row.taught_move}`);
        for (const use of row.uses ?? []) {
          ids.push(use.speaker, ...use.places.map((place) => this.view.catalogId(`mapsec:${place.map_section}`)),
            ...use.gives.map((entry) => entry.move == null ? null : `moves:${entry.move}`));
        }
      }
    } else if (this.mode === "trainers") {
      await this.loadTrainerIvs();
      if (generation !== this.view.generation || mode !== this.mode || navigation !== this.view.navigation) {
        return false;
      }
      for (const row of this.data.trainers ?? []) {
        ids.push(row.name, row.class, ...(row.map_section ?? []).map((section) => this.view.catalogId(`mapsec:${section}`)));
        if ((row.map ?? []).some((map) => INVERSE_BATTLE_MAPS.has(map))) ids.push(INVERSE_BATTLE_SIGN);
        for (const mon of row.party) {
          const species = this.view.record(mon.species);
          ids.push(species?.name, ...(mon.item ? itemKeys(mon.item, this.data.items ?? []) : []), ...(mon.moves ?? []).map((id) => `moves:${id}`));
        }
      }
    } else {
      for (const row of this.data.encounters ?? []) {
        ids.push(this.view.record(row.species)?.name, this.view.catalogId(`mapsec:${row.map_section}`));
      }
      for (const row of this.data.items ?? []) {
        const sources = (row.sources ?? []).filter((source) => PICKUP_KINDS.includes(source.kind));
        if (!sources.length) continue;
        ids.push(row.name, row.taught_move == null ? null : `moves:${row.taught_move}`);
        for (const source of sources) {
          ids.push(this.view.catalogId(`mapsec:${source.map_section}`),
            ...(source.related_sections ?? []).map((section) => this.view.catalogId(`mapsec:${section}`)));
        }
      }
      for (const row of this.data.trainers ?? []) {
        if (row.map_section?.length) ids.push(row.name, row.class);
      }
      for (const row of this.data.location_details ?? []) {
        ids.push(this.view.catalogId(`mapsec:${row.map_section}`),
          ...row.exits.map((exit) => this.view.catalogId(`mapsec:${exit.to_section}`)));
      }
      for (const row of this.data.location_maps ?? []) {
        ids.push(this.view.catalogId(`mapsec:${row.map_section}`));
        if (row.parent_section != null) ids.push(this.view.catalogId(`mapsec:${row.parent_section}`));
      }
    }
    await Promise.all([...new Set(ids.filter(Boolean))].map((id) => this.ensure(id)));
    if (generation !== this.view.generation || mode !== this.mode || navigation !== this.view.navigation) return false;
    this.loaded = true;
    this.groups = null;
    if (this.loaded) {
      this.view.renderFilters?.(this.mode);
      this.view.restoreFilters?.(this.mode);
    }
    if (this.loaded && this.mode === "moves" && this.view.filterSets?.moves.values().learner
      && this.view.learnsetsPending?.()) await this.view.ensureLearnsets();
    return this.loaded;
  }


  /** Relabel the list, filters and open card. */
  relabel() {
    if (!this.loaded) return;
    this.groups = null;
    this.entryCache = null;
    relabelMatches(this.cross, this.view);
    this.view.renderFilters(this.mode);
    this.view.restoreFilters(this.mode);
    this.applyFilters();
    if (this.cur != null && this.record(this.cur)) this.renderDetail(this.cur);
    this.renderList();
  }


  setMode(mode) {
    if (this.mode === mode) return;
    this.mode = mode;
    this.loaded = false;
    this.cur = null;
    this.filtered = [];
    this.cross = [];
    this.groups = null;
    this.entryCache = null;
  }


  entrySearch() {
    const records = this.records;
    const current = this.view.searchCacheCurrent?.(this.entryCache) ?? false;
    if (!current || this.entryCache.mode !== this.mode || this.entryCache.records !== records) {
      const search = createEntrySearch(records, (id) => this.view.text(id), this.mode,
        (id) => this.view.otherNames(id), (id) => this.source(id));
      this.entryCache = { ...(this.view.searchCacheFields?.() ?? {}), mode: this.mode, records, search };
    }
    return this.entryCache.search;
  }


  applyFilters() {
    this.view.rememberFilters?.(this.mode);
    const set = this.view.filterSets?.[this.mode];
    const query = this.$("x-search").value;
    if (query.trim() || set?.values().learner) this.view.ensureCatalogs?.();
    if (this.mode === "moves" && set?.values().learner && this.view.learnsetsPending?.()) {
      const generation = this.view.generation;
      this.view.ensureLearnsets().then((ok) => {
        if (ok && this.mode === "moves" && generation === this.view.generation) this.refilter();
      });
    }
    let records = this.records;
    if (set) {
      const context = this.view.filterContext(this.mode);
      const values = set.prepare(set.values(), context);
      records = records.filter((record) => set.matches(record, values, context));
    }
    const search = this.entrySearch();
    this.filtered = search.ranked(query, records);
    this.strong = this.filtered.filter((row) => search.rank(row, query) <= 1).length;
    this.order = listOrder(this.strong, this.filtered.length, []);
    this.showCount();
    this.list.setCount(this.filtered.length);
    this.updateCross(query);
    set?.refresh(set.values(), this.filtered.length, this.records.length);
  }


  showCount() {
    setText(this.$("x-count"), t(COUNT_LABELS[this.mode] ?? `{shown} of {total} ${this.mode}`,
      { shown: this.filtered.length.toLocaleString(), total: this.records.length.toLocaleString() }));
  }


  /** Load cross-category names on first search. */
  updateCross(query) {
    this.cross = [];
    if (!(query ?? "").trim()) return;
    const generation = this.view.generation, mode = this.mode, revision = this.view.searchRevision;
    this.view.ensureCrossNames().then(() => {
      if (generation !== this.view.generation || revision !== this.view.searchRevision
        || this.view.mode !== mode || this.mode !== mode || this.$("x-search").value !== query) return;
      this.cross = this.view.crossSearch().matches(query, mode);
      this.order = listOrder(this.strong ?? this.filtered.length, this.filtered.length, this.cross);
      this.list.setCount(this.filtered.length + this.cross.length);
      this.renderList();
    }).catch(() => {});
  }


  row(p) {
    const at = this.order?.[p] ?? { own: p };
    if (at.cross != null) {
      return crossRow(this.cross[at.cross], p * ROW, p === 0 || this.order[p - 1].cross == null, (want) => this.view.goTo(want));
    }
    const record = this.filtered[at.own];
    const name = this.mode === "trainers" ? trainerName(record, (id) => this.view.text(id), (id) => this.source(id))
      : this.view.text(record.name) || (this.mode === "locations" ? t("Area {n}", { n: record.id }) : "");
    const icon = this.mode === "items" ? itemIcon(record.id, [record], (path) => this.host.spriteSrc(path))
      : this.mode === "trainers" ? this.trainerSprite(record.battles[0]) : null;
    const sub = this.mode === "moves" ? effectName(record.effect, record.effect_key)
      : this.mode === "trainers" ? this.view.text(record.class)
      : this.mode === "locations" && record.areas.length > 1 ? t("{n} areas", { n: record.areas.length }) : "";
    const location = this.mode === "trainers" ? this.trainerPlaces(record, true) : null;
    return el("div", {
      class: `row${record.id === this.cur ? " sel" : ""}`, role: "option",
      "data-key": `${this.mode}:${record.id}`,
      style: `top:${p * ROW}px`, on: { click: () => this.open(record.id) },
    }, el("span", { class: "dex-num" }, String(record.id)), icon,
    el("span", { class: "dex-name-list", title: record.name ?? record.id }, name,
      sub ? el("span", { class: "dex-sub" }, ` · ${sub}`) : null,
      location ? el("span", { class: "dex-sub dex-trainer-location" }, location) : null));
  }


  renderList(scroll = false) {
    return this.view.renderList(scroll);
  }


  step(delta) {
    if (!DATA_MODES.includes(this.view.mode) || !this.filtered.length) return;
    const at = this.filtered.findIndex((row) => row.id === this.cur);
    this.open(this.filtered[(at + delta + this.filtered.length) % this.filtered.length].id);
  }


  open(id, navigate = true) {
    if (navigate) return this.view.goTo({ kind: this.mode, id });
    return this.renderDetail(id);
  }


  renderDetail(id) {
    const record = this.record(id);
    if (!record) return;
    this.cur = id;
    setText(this.$("x-data-id"), "");
    setText(this.$("x-data-title"), this.mode === "trainers"
      ? trainerName(record, (key) => this.view.text(key), (key) => this.source(key))
      : this.view.text(record.name) || (this.mode === "locations" ? t("Area {n}", { n: record.id }) : ""));
    this.$("x-data-title").hidden = !this.$("x-data-title").textContent.trim();
    const content = this.detail(record);
    if (this.mode === "items") content.setAttribute("aria-label", t("Item card"));
    content.setAttribute("data-key", `${this.mode}:${record.id}`);
    updateChildren(this.$("x-data-content"), content);
    this.observeItemSpots(this.$("x-data-content").querySelector?.(".dex-location-items"));
  }
}

export class PokedexDataView extends withLocationCards(withTrainerCards(withItemAndMoveCards(PokedexDataViewCore))) {}
