import { el, setText, updateChildren } from "../dom.js";
import { siteText, t } from "../ui.js";
import { MoveDialog, itemCardContent } from "../movecard.js";
import { against, cellClass, cellLabel, chartTypes, defenseGroups, dualRows, effectivenessMap, factorLabel, hitLabel, offenseGroups, pairCount, pairCounts } from "../typechart.js";
import { itemIcon, typeChip } from "./evolutions.js";
import { displayName, recordBrowsable } from "./species-search.js";
import { statRanks } from "./species-list.js";

export const withTypeCards = (Base) => class extends Base {

  /** Render species moves, loading learnsets on first use. */
  renderMoves(record) {
    if (record.learnset === undefined && this.learnsetsPending()) {
      const generation = this.generation;
      const current = () => this.mode === "species" && generation === this.generation && this.record(this.cur) === record;
      updateChildren(this.$("x-moves"));
      if (this.moveFailure?.record === record && this.moveFailure.generation === generation) {
        updateChildren(this.$("x-moves-empty"), siteText("Could not load the moves."), " ", el("button", {
          type: "button", class: "btn sm", on: { click: () => {
            if (!current()) return;
            this.moveFailure = null;
            this.renderMoves(record);
          } },
        }, siteText("Retry")));
        return;
      }
      setText(this.$("x-moves-empty"), t("Loading the moves…"));
      this.ensureLearnsets().then((ok) => {
        if (!current()) return;
        this.moveFailure = ok ? null : { record, generation };
        this.renderMoves(record);
      });
      return;
    }
    const tables = record.learnset == null ? [] : this.dataView.learnsetTables(record);
    // Identify the species providing borrowed non-level-up learnsets.
    const source = record.learnset_species != null && record.learnset_species !== record.slot
      ? this.record(record.learnset_species) : null;
    const note = source && (record.learnset ?? []).some((row) => row.source != null)
      ? [el("p", { class: "muted dex-learn-note" },
        t("TM, tutor and egg moves as {species}", { species: this.text(source.name) || this.speciesName?.(source) }))] : [];
    updateChildren(this.$("x-moves"), ...tables, ...note);
    setText(this.$("x-moves-empty"), record.learnset == null ? t("Not extracted") : tables.length ? "" : t("None"));
  }


  /** Cache sorted species stats until data reloads. */
  ranks() {
    const records = this.records;
    if (!this._ranks || this._ranksOf !== records) {
      this._ranks = statRanks(records);
      this._ranksOf = records;
    }
    return this._ranks;
  }


  /** Cache type effectiveness until the dataset reloads. */
  effectiveness() {
    const rows = this.host.data()?.type_effectiveness;
    if (!rows) return null;
    if (!this._effectiveness || this._effectivenessRows !== rows) {
      this._effectiveness = effectivenessMap(rows);
      this._effectivenessRows = rows;
    }
    return this._effectiveness;
  }


  /** Render translated type chips using game colors. */
  chip(id, extra = "") {
    return typeChip(id, this.text(`types:${id}`), this.host.data()?.types ?? [], extra);
  }


  /** Group combined defensive type effectiveness multipliers. */
  defenseRows(record) {
    const map = this.effectiveness();
    if (!map || !(record.types ?? []).length) return [];
    const attackers = chartTypes(this.host.data()?.types ?? []);
    return defenseGroups(map, record.types, attackers).map(({ factor, ids }) => el("div", { class: "dex-def-row" },
      el("span", { class: `dex-def-label dex-def-${cellClass(factor)}` }, factorLabel(factor)),
      el("div", { class: "dex-types" }, ...ids.map((id) => this.chip(id)))));
  }


  /** Show defensive matchups and each species type's attacking matchups. */
  effectivenessBlocks(record) {
    const map = this.effectiveness();
    const types = [...new Set(record.types ?? [])];
    if (!map || !types.length) return [];
    const species = displayName(record, this.records, (id) => this.text(id));
    const all = chartTypes(this.host.data()?.types ?? []);
    if (!all.length) return [];
    const columns = (rows) => {
      while (rows < all.length && all.length % rows) rows++;
      return Math.max(1, all.length / rows);
    };
    const grid = (groups, label) => {
      const factors = new Map(groups.flatMap(({ factor, ids }) => ids.map((id) => [id, factor])));
      const node = el("dl", { class: "dex-eff-grid" }, ...all.map((id) => {
        const factor = factors.get(id) ?? 1;
        return el("div", { class: "dex-eff-cell" },
          el("dt", {}, this.chip(id)),
          el("dd", { class: `dex-eff-value ${cellClass(factor) || "neutral"}`, title: label(factor) }, cellLabel(factor) || "1"));
      }));
      node.style.setProperty("--dex-eff-count", all.length);
      node.style.setProperty("--dex-eff-medium", columns(2));
      node.style.setProperty("--dex-eff-small", columns(3));
      return node;
    };
    const out = [el("h3", { class: "dex-eff-title" }, t("Defense: a move of the listed type hits {species}", { species })),
      grid(defenseGroups(map, types, all), factorLabel)];
    for (const type of types) {
      out.push(el("h3", { class: "dex-eff-title" }, t("Attack: {species} uses a {type}-type move", { species, type: this.text(`types:${type}`) })),
        grid(offenseGroups(map, type, all), hitLabel));
    }
    return out;
  }


  /** Compare every attacking type against every defending type. */
  renderTypes() {
    const map = this.effectiveness();
    const table = this.$("x-type-chart");
    if (!map) {
      updateChildren(table);
      setText(this.$("x-chart-empty"), t("Not extracted"));
      return;
    }
    setText(this.$("x-chart-empty"), "");
    const name = (id) => this.text(`types:${id}`) || t("Type {n}", { n: id });
    const query = this.$("x-search").value.trim().toLowerCase();
    const ids = chartTypes(this.host.data()?.types ?? []).filter((id) => !query
      || [name(id), ...this.otherNames(`types:${id}`)].some((label) => label.toLowerCase().includes(query)));
    this.filterSets?.types?.refresh({}, ids.length, chartTypes(this.host.data()?.types ?? []).length);
    const head = el("thead", {}, el("tr", {},
      el("th", { class: "dex-chart-corner" }, el("span", {}, `${t("Defense")} →`), el("span", {}, `${t("Attack")} ↓`)),
      ...ids.map((id) => el("th", { scope: "col" }, this.chip(id)))));
    const body = el("tbody", {}, ...ids.map((attack) => el("tr", {},
      el("th", { scope: "row" }, this.chip(attack)),
      ...ids.map((defense) => {
        const m = against(map, attack, defense);
        return el("td", { class: cellClass(m), title: `${name(attack)} → ${name(defense)}: ×${m}` }, cellLabel(m));
      }))));
    updateChildren(table, head, body);
    this.renderDual(ids, map);
  }


  /** Choose the routed type or first available type. */
  pickDual(id) {
    return this.goTo({ kind: "types", id });
  }


  /** Compare dual types with combined multipliers and counts. */
  renderDual(ids = chartTypes(this.host.data()?.types ?? []), map = this.effectiveness()) {
    if (!map) return;
    if (!ids.length) {
      this.$("x-dual-pick").replaceChildren();
      this.$("x-dual-chart").replaceChildren();
      return;
    }
    const primary = ids.includes(this.dualType) ? this.dualType : ids[0];
    this._pairs ??= pairCounts(this.records);
    const name = (id) => this.text(`types:${id}`) || t("Type {n}", { n: id });
    updateChildren(this.$("x-dual-pick"), ...ids.map((id) => el("button", {
      type: "button", class: `dex-link dex-pick${id === primary ? " on" : ""}`, "aria-pressed": String(id === primary),
      title: name(id), on: { click: () => this.pickDual(id) },
    }, this.chip(id))));
    const head = el("thead", {}, el("tr", {},
      el("th", { class: "dex-chart-corner" }, el("span", {}, `${t("Attack")} →`), el("span", {}, `${t("Types")} ↓`)),
      el("th", { class: "dex-chart-corner" }, el("span", {}, t("Species"))),
      ...ids.map((id) => el("th", { scope: "col" }, this.chip(id)))));
    const body = el("tbody", {}, ...dualRows(map, primary, ids).map((row) => el("tr", {},
      el("th", { scope: "row" }, el("span", { class: "dex-types" }, this.chip(row.types[0]),
        row.types[1] == null ? el("span", { class: "chip dex-none" }, "—") : this.chip(row.types[1]))),
      el("td", { class: "count" }, String(pairCount(this._pairs, row.types[0], row.types[1] ?? row.types[0]))),
      ...row.cells.map(({ attack, m }) => el("td", {
        class: cellClass(m), title: `${name(attack)} → ${row.types.map(name).join(" / ")}: ×${m}`,
      }, cellLabel(m))))));
    updateChildren(this.$("x-dual-chart"), head, body);
  }


  /** Highlight hovered columns while styles highlight their rows. */
  highlightColumn(event) {
    const table = this.$("x-type-chart");
    for (const marked of table.querySelectorAll?.(".col-hi") ?? []) marked.classList.remove("col-hi");
    const cell = event?.target?.closest?.("td, th");
    if (!cell || !(cell.cellIndex >= 1)) return;
    for (const row of table.rows ?? []) row.cells[cell.cellIndex]?.classList.add("col-hi");
  }


  /** Open the dedicated page for an item. */
  gotoItem(id) {
    return this.goTo({ kind: "items", id });
  }


  /** Open compact item cards, falling back to their pages. */
  openItem(id) {
    const data = this.host.data();
    if (!recordBrowsable({ id: Number(id) }, data?.items_browsing)) return this.gotoItem(id);
    const record = (data?.items ?? []).find((row) => row.id === Number(id));
    this.moveDialog ??= new MoveDialog();
    if (!record || !this.moveDialog.available) return this.gotoItem(id);
    const text = (x) => this.text(x);
    const show = () => this.moveDialog.show(itemCardContent(record, {
      text, icon: itemIcon(record.id, [record], (path) => this.host.spriteSrc(path)),
      pokedollarIcon: () => this.dataView.pokedollarIcon(),
      taught: record.taught_move == null ? null : this.moveLink(record.taught_move),
      evolutions: this.dataView.itemEvolutionBlock(record), open: (item) => this.gotoItem(item),
    }), t("Item card"));
    show();
    const description = record.description;
    if (description && !this.texts.has(description)) { // Species cards initially load item names without descriptions.
      this.host.entry(this.catalogId(description)).then((entry) => {
        this.texts.set(description, { translation: entry?.translation ?? "", source_text: entry?.source_text ?? "" });
        if (this.moveDialog.node?.open) show();
      }).catch(() => {});
    }
    return Promise.resolve();
  }


  /** Open location cards linked from species cards. */
  openLocation(id) {
    return this.goTo({ kind: "locations", id });
  }


  /** Open species cards from other entry kinds. */
  openSpecies(slot) {
    return this.goTo({ kind: "species", id: slot });
  }
};
