import { el, setText, updateChildren } from "../dom.js";
import { evYield } from "../pokedexdataview/trainers.js";
import { levels, speciesLocations, variantLabel } from "../pokedexdataview/locations.js";
import { t } from "../ui.js";
import { against } from "../typechart.js";
import { evolutionTree, evolutionGrid, evolutionCondition, itemLink, conditionParts, speciesSources, typeChip } from "./evolutions.js";
import { displayName, formSectionRecords, dexNumber, speciesNumber, numberedName, abilityChanges, speciesBrowsable, detailOf, sourceParts } from "./species-search.js";
import { TIER_LABELS, statRows } from "./species-list.js";


/** Preserve paragraph gaps while joining source line breaks. */
export function flatLines(text) {
  const paragraphs = text.split("\\p");
  const out = [];
  paragraphs.forEach((part, i) => {
    out.push(document.createTextNode(part.split(/\\[nl]/).join(" ")));
    if (i + 1 < paragraphs.length) out.push(el("span", { class: "dex-para" }, " "));
  });
  return out;
}

export const withSpeciesCards = (Base) => class extends Base {

  evolutionTarget(slot) {
    const target = this.record(slot);
    const nameId = target?.name ?? `species:${slot}`;
    const name = target ? numberedName(target, this.records, (id) => this.text(id)) : nameId;
    return el("button", {
      type: "button",
      class: `dex-evo-target${this.fallback(nameId) ? " muted" : ""}${target?.slot === this.cur ? " cur" : ""}`,
      title: nameId, disabled: !target,
      ...(target?.slot === this.cur ? { "aria-current": "true" } : {}),
      on: { click: () => target && this.open(slot) },
    },
    target?.sprite?.front ? el("img", {
      src: this.host.spriteSrc(target.sprite.front), alt: "", class: "dex-evo-img", loading: "lazy",
    }) : null,
    el("span", { class: "dex-evo-name" }, name));
  }



  /** Show branch conditions with ability changes beneath. */
  evolutionMeta(source, target, edge) {
    const edges = Array.isArray(edge) ? edge : [edge];
    const method = edges.every((e) => e.method === 253) ? 253 : null;
    const changes = abilityChanges(this.record(source), this.record(target), (id) => this.text(id), method);
    const items = this.host.data()?.items ?? [];
    const joined = edges.length > 1;
    const conditions = edges.map((e) => evolutionCondition(e, (id) => this.text(id), items, joined));
    const condition = conditions.join(` ${t("or")} `);
    const part = (p) => (p.item != null ? itemLink(p.item, (id) => this.text(id), items,
      (path) => this.host.spriteSrc(path), (id) => this.openItem(id))
      : p.move != null ? this.moveLink(p.move) : p.text);
    return el("span", { class: "dex-evo-meta", title: condition },
      ...edges.flatMap((e, i) => [
        ...(i ? [el("span", { class: "dex-evo-or" }, t("or"))] : []),
        el("span", { class: "dex-evo-cond" }, ...conditionParts(e, (id) => this.text(id), items, joined).map(part)),
      ]),
      changes.length ? el("div", { class: "dex-ability-box", title: t("Ability changes") }, ...changes.map((change) =>
        el("div", { class: "dex-ability-change", title: `${change.from?.id ?? ""} ➝ ${change.to?.id ?? ""}`
          + `${change.alternate ? ` or ${change.alternate.id}` : ""}`
          + (change.standIn ? t(" (the first ability stands in for an empty slot)") : "") },
          el("span", { class: `dex-ability-from${change.from ? "" : " muted"}` }, change.from?.text || t("none")),
          change.hidden ? el("span", { class: "dex-ability-tag" }, t("hidden")) : null,
          el("span", { class: "dex-ability-arrow" }, "→"),
          el("span", { class: `dex-ability-to${change.to ? "" : " muted"}` }, change.to?.text || t("none")),
          change.alternate ? ` ${t("or")} ${change.alternate.text}` : null))) : null);
  }



  /** Render evolution stages as columns and branches as rows. */
  evolutionGridElement(grid) {
    const species = new Map(grid.cells.filter((cell) => cell.kind === "species").map((cell) => [cell.slot, cell]));
    const cellElement = (cell) => {
      const node = cell.kind === "species"
        ? this.evolutionTarget(cell.slot)
        : this.evolutionMeta(cell.from, cell.to, cell.edges);
      node.style = `grid-column:${cell.column};grid-row:${cell.row} / span ${cell.span}`;
      return node;
    };
    const forkElement = (source) => {
      const edges = grid.cells.filter((cell) => cell.kind === "edge" && cell.from === source);
      if (!edges.length) return null;
      return el("div", {
        class: `dex-evo-fork${edges.length > 1 ? " split" : ""}`,
        ...(edges.length > 1 ? { "data-branches": edges.length } : {}),
      }, ...edges.map((edge) => el("div", { class: "dex-evo-branch" },
        cellElement(edge), cellElement(species.get(edge.to)), forkElement(edge.to))));
    };
    return el("div", { class: "dex-grid", style: `grid-template-columns:repeat(${grid.columns},max-content)` },
      cellElement(grid.cells[0]), forkElement(grid.cells[0].slot));
  }


  /** Link forms not already represented as Mega evolutions. */
  renderFormes(record) {
    const formes = formSectionRecords(record, this.records, this.familyIndex());
    this.$("x-formes-block").hidden = formes.length < 2;
    updateChildren(this.$("x-formes"), ...(formes.length < 2 ? [] : formes.map((forme) => {
      const current = forme.slot === record.slot;
      return el("button", {
        type: "button", class: `dex-evo-target dex-forme-target${current ? " cur" : ""}`,
        title: forme.name, "data-key": `species:${forme.slot}`,
        ...(current ? { "aria-current": "true" } : {}),
        on: { click: () => this.open(forme.slot) },
      },
      forme.sprite?.front ? el("img", {
        src: this.host.spriteSrc(forme.sprite.front), alt: "", class: "dex-evo-img", loading: "lazy",
      }) : null,
      el("span", { class: "dex-evo-name" }, displayName(forme, this.records, (id) => this.text(id))));
    })));
  }


  open(slot, navigate = true) {
    if (navigate && this.loaded) return this.goTo({ kind: "species", id: slot });
    this.renderSpecies(slot, navigate);
    if (navigate) this.renderList(true);
  }


  renderSpecies(slot, navigate = true) {
    const record = this.record(slot);
    if (!record) return;
    if (!speciesBrowsable(record, this.host.data()?.species_browsing)) return;
    this.cur = slot;
    const d = detailOf(record, (id) => this.text(id), this.host.data()?.abilities ?? []);
    setText(this.$("x-id"), dexNumber(speciesNumber(record)) || "—");
    setText(this.$("x-dex"), record.name);
    setText(this.$("x-name"), numberedName(record, this.records, (id) => this.text(id)));
    this.$("x-name").hidden = !this.$("x-name").textContent.trim();
    this.$("x-name").title = record.name;
    this.$("x-name").classList.toggle("muted", this.fallback(record.name));
    this.renderSprite(record);
    this.renderFormes(record);
    updateChildren(this.$("x-types"), ...(record.types ?? []).filter((t, i, all) => all.indexOf(t) === i).map((t) =>
      typeChip(t, this.text(`types:${t}`), this.host.data()?.types ?? [], this.fallback(`types:${t}`) ? " muted" : "")));
    updateChildren(this.$("x-abilities"), ...d.abilities.map((a) => el("div", { class: "dex-ability" },
      el("div", { class: "dex-ability-name" },
        el("span", { class: `chip${this.fallback(a.id) ? " muted" : ""}`, title: a.id }, a.text),
        ...(a.hidden ? [el("span", { class: "dex-hidden" }, a.regular ? t("Regular / Hidden") : t("hidden"))] : [])),
      el("div", {
        class: `dex-ability-description${this.fallback(a.descriptionId) ? " muted" : ""}`,
        title: a.descriptionId ?? "",
      }, flatLines(a.description)))));
    const stats = this.$("x-stats");
    const rows = statRows(d.stats, this.ranks());
    const tierTitle = (row) => (row.tier == null ? ""
      : t("{tier}: better than {percent}% of species", { tier: t(row.tierName), percent: row.percentile }));
    updateChildren(stats, ...rows.map((row) => el("tr", { class: row.total ? "total" : "" },
      el("td", {}, row.label),
      el("td", { class: "bar" }, row.total
        ? (row.tier == null ? null : el("span", { class: `dex-tier t${row.tier}`, title: tierTitle(row) }, t(TIER_LABELS[row.tier])))
        : el("span", { class: `dex-bar${row.tier == null ? "" : ` t${row.tier}`}`, style: `width:${row.percent}%`, title: tierTitle(row) })),
      el("td", { class: "num" }, String(row.value)))));
    setText(this.$("x-ev-yield"), evYield(record.ev_yield));
    const legend = this.$("x-stats-legend");
    legend.title = t("Each stat ranked against every species of this game");
    updateChildren(legend, ...(rows.length && rows[0].tier != null
      ? TIER_LABELS.map((label, i) => el("span", {}, el("i", { class: `dex-swatch t${i}` }), t(label))) : []));
    setText(this.$("x-height"), d.height);
    setText(this.$("x-weight"), d.weight);
    setText(this.$("x-national-number"), d.dex || "—");
    setText(this.$("x-category"), d.category);
    this.$("x-category").title = record.category ?? "";
    this.$("x-category").classList.toggle("muted", this.fallback(record.category));
    setText(this.$("x-catch-rate"), d.catchRate);
    setText(this.$("x-base-exp"), d.baseExp);
    setText(this.$("x-growth"), d.growth);
    setText(this.$("x-egg-groups"), d.eggGroups);
    setText(this.$("x-gender"), d.gender);
    updateChildren(this.$("x-defense"), ...this.effectivenessBlocks(record));
    const tree = evolutionTree(record, this.records);
    updateChildren(this.$("x-evolutions"), ...(tree ? [this.evolutionGridElement(evolutionGrid(tree))] : []));
    setText(this.$("x-evolutions-empty"), tree == null
      ? t("Not extracted") : tree.branches.length ? "" : t("None"));
    const data = this.host.data();
    const encounters = data?.encounters;
    const found = speciesLocations(encounters ?? [], record.slot, data?.location_details);
    const headings = [t("Location"), t("Area"), t("Method and conditions"), t("Levels"), t("Chance")];
    const blocks = found.length ? [el("div", { class: "dex-location-table" }, el("table", { class: "t-forms dex-enc" },
      el("thead", {}, el("tr", {}, ...headings.map((heading, index) =>
        el("th", { scope: "col", class: index >= 3 ? "num" : "" }, heading)))),
      el("tbody", {}, ...found.map((row) => el("tr", {},
        el("td", {}, el("button", {
          type: "button", class: "dex-link", title: row.name ?? row.id,
          on: { click: () => this.openLocation(row.id) },
        }, this.text(row.name) || t("Area {n}", { n: row.id }))),
        el("td", {}, String(row.area_number)),
        el("td", {}, `${row.method} · ${variantLabel(row)}`),
        el("td", { class: "num" }, levels(row)),
        el("td", { class: "num" }, `${row.chance}%`))))))] : [];
    const sources = speciesSources(data?.sources, record.slot);
    if (sources.length) {
      blocks.push(el("div", { class: "dex-sources" }, el("h3", {}, t("Obtained")),
        el("ul", { class: "dex-source-list" }, ...sources.map((source) => el("li", {
          title: [source.map, source.script, source.offset].filter(Boolean).join(" · "),
        }, ...sourceParts(source, (id) => this.text(id), data?.items ?? [], this.records).map((part) => (part.item != null
          ? itemLink(part.item, (id) => this.text(id), data?.items ?? [],
            (path) => this.host.spriteSrc(path), (id) => this.openItem(id))
          : part.species != null ? el("button", {
            type: "button", class: "dex-link dex-source-species", title: `species:${part.species}`,
            on: { click: () => this.openSpecies(part.species) },
          }, part.text) : part.text)))))));
    }
    updateChildren(this.$("x-locations"), ...blocks);
    setText(this.$("x-locations-empty"), found.length || sources.length ? ""
      : encounters == null || data?.sources == null ? t("Not extracted") : t("Not found in the wild"));
    if (navigate) this.renderCatch(record, found).catch(() => {});
    this.renderMoves(record);
    updateChildren(this.$("x-entry-text"), ...d.entries
      .filter((e) => e.text)
      .map((e) => el("p", { title: e.id, class: this.fallback(e.id) ? "muted" : "" }, flatLines(e.text))));
    const aliases = this.searchAliases(record.name);
    const game = this.host.profile?.()?.name;
    const internalId = game ? t("{game} internal ID", { game }) : t("Slot");
    this.$("x-id").title = t("Internal ID");
    const facts = [
      ...(aliases.length ? [[t("Search aliases"), aliases.join(", ")]] : []),
      [internalId, String(record.slot)], [t("Offset"), record.offset ?? ""],
      [t("Dex offset"), record.dex_offset ?? ""], [t("Name id"), record.name ?? ""],
      [t("Category id"), record.category ?? ""], [t("Category offset"), record.category_offset ?? ""],
      [t("Entry ids"), (record.entry ?? []).join(", ")],
    ].map(([k, v]) => [el("dt", {}, k), el("dd", { class: "mono" }, v)]).flat();
    updateChildren(this.$("x-facts"), ...facts);

  }
};
