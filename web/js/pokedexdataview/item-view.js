import { el, updateChildren } from "../dom.js";
import { powerLabel } from "../moveeffects.js";
import { evolutionCondition, itemIcon, typeChip } from "../pokedex/evolutions.js";
import { itemText } from "../pokedex/species-data.js";
import { siteText, t, uiLanguage } from "../ui.js";
import { CATEGORY, itemAmount, itemFields } from "./items.js";
import { itemEvolutions, itemFacts, learnerIndex, learnLabel, learnsetGroups } from "./trainers.js";
import { levels, flow, fieldList } from "./locations.js";
import { moveProperties, moveMechanics, moveFields, moveFacts, nameMeasure, learnerColumnWidth, learnerGroupsOf } from "./moves.js";

export const withItemAndMoveCards = (Base) => class extends Base {

  detail(record) {
    if (this.mode === "trainers") return this.trainerDetail(record);
    if (this.mode === "locations") return this.locationDetail(record);
    if (this.mode === "items") return this.itemDetail(record);
    return this.moveDetail(record);
  }


  /** Render item fields, taught moves and descriptions. */
  itemDetail(record) {
    const list = fieldList(itemFields(record, (id) => this.view.text(id) || this.source(id), () => this.pokedollarIcon()));
    const icon = itemIcon(record.id, [record], (path) => this.host.spriteSrc(path));
    if (icon) list.prepend?.(el("dt", {}, t("Icon")), el("dd", {}, icon));
    const taught = record.taught_move == null ? null : this.data.moves?.[record.taught_move];
    return el("div", { class: "dex-data-detail dex-data-card" },
      el("div", { class: "dex-panels" },
        el("section", { class: "block" }, list),
        el("section", { class: "block dex-data-about" },
          ...flow(this.view.text(record.description)).map((text) =>
            el("p", { title: record.description ?? "" }, text)),
          taught ? el("div", { class: "dex-line" }, el("span", { class: "muted" }, t("Teaches")),
            el("button", {
              type: "button", class: "dex-link", title: `moves:${record.taught_move}`,
              on: { click: () => this.view.openMove(record.taught_move) },
            }, this.view.text(`moves:${record.taught_move}`) || `moves:${record.taught_move}`)) : null)),
      ...this.itemUpgradeBlock(record),
      ...this.itemSourceBlock(record),
      ...this.itemUseBlock(record),
      ...this.itemEvolutionBlock(record),
      el("details", { class: "block dex-record" }, el("summary", {}, t("Technical record")), fieldList(itemFacts(record))));
  }


  /** List every declared level and upgrade location. */
  itemUpgradeBlock(record) {
    const amount = record.held_effect_amount;
    if (!amount?.levels?.length) return [];
    const text = (id) => this.view.text(id) || this.source(id);
    return [el("section", { class: "block dex-item-upgrades" }, el("h2", {}, t("Upgrade levels")),
      fieldList(amount.levels.map((row) => [t("Level {n}", { n: row.level.toLocaleString(uiLanguage()) }),
        itemAmount(record, row.values, text)])),
      el("h2", {}, t("Where to upgrade")),
      el("ul", { class: "dex-source-list" }, ...amount.upgrade_sources.map((source) => el("li", {},
        ...source.map_sections.flatMap((section, index) => index ? [", ", this.sectionLink(section)] : [this.sectionLink(section)]),
        " · ", t(source.key)))))];
  }


  /** Show mining chances and link placed items to locations. */
  itemSourceBlock(record) {
    const sources = record.sources ?? [];
    if (!sources.length) return [];
    return [el("section", { class: "block dex-item-sources" }, el("h2", {}, t("Where to get")),
      el("ul", { class: "dex-source-list" }, ...sources.map((source) => {
        if (source.kind === "exchange") return this.itemUseRow({ ...source, places: [source] });
        if (source.kind === "mining") return el("li", {},
          el("span", { class: "muted" }, t("Mining")), " ",
          el("span", { class: "dex-sub" }, `${source.chance.toFixed(1)}%`));
        const section = source.map_section;
        const id = section == null ? source.map : String(section);
        const name = section == null ? source.map : this.view.text(`mapsec:${section}`) || source.map;
        const label = source.kind === "shop" ? t("Shop") : source.kind === "ground" ? t("On the map")
          : source.kind === "hidden" ? t("Hidden") : source.kind === "gift" ? t("Gift") : source.kind;
        return el("li", { title: source.map },
          el("span", { class: "muted" }, label), " ",
          el("button", {
            type: "button", class: "dex-link", title: source.map,
            on: { click: () => this.view.openLocation(id) },
          }, name),
          source.quantity > 1 ? el("span", { class: "dex-sub" }, ` ${t("×{n}", { n: source.quantity })}`) : null,
          source.choices ? el("span", {}, ` · ${t("Possible rewards")}: `, ...this.itemChoices(source.choices)) : null);
      })))];
  }


  /** Keep reward quantities beside their links. */
  itemChoices(choices) {
    return choices.flatMap((entry, index) => [index ? " / " : null, el("span", {}, this.itemLink(entry.item),
      entry.quantity > 1 ? ` ${t("×{n}", { n: entry.quantity })}` : null)]);
  }


  /** Link both sides of an exchange. */
  itemUseRow(use) {
    const kinds = { exchange: t("Exchange"), upgrade: t("Upgrade"), requirement: t("Required") };
    const count = (quantity) => quantity == null || quantity === 1 ? null
      : t("×{n}", { n: Array.isArray(quantity) ? quantity.join("–") : quantity });
    const counted = (node, quantity) => count(quantity) ? [count(quantity), " ", node] : [node];
    const give = (entry) => entry.item != null ? counted(this.itemLink(entry.item), entry.quantity)
      : entry.move != null ? [t("Teaches"), " ", this.view.moveLink(entry.move)]
        : entry.upgrade != null ? [t("Level +1:"), " ", this.itemLink(entry.upgrade)]
          : entry.money != null ? [String(entry.money), " ", this.pokedollarIcon(), ` (${t("Per item")})`]
            : entry.species != null ? [el("button", {
              type: "button", class: "dex-link", title: `species:${entry.species}`,
              on: { click: () => this.view.openSpecies(entry.species) },
            }, this.view.text(this.view.record(entry.species)?.name) || t("Species {n}", { n: entry.species }))]
              : [t(entry.effect)];
    const joined = (parts, separator) => parts.flatMap((part, index) => index ? [separator, ...part] : part);
    const places = [...new Set(use.places.map((place) => place.map_section))];
    return el("li", {},
      el("span", { class: "muted" }, kinds[use.kind] ?? use.kind), " ",
      ...joined(use.takes.map((entry) => counted(this.itemLink(entry.item), entry.quantity)), " + "),
      " → ", use.choice ? `${t("One of")}: ` : null,
      ...joined(use.gives.map(give), use.choice ? " / " : " + "),
      use.conditions?.length ? el("span", { class: "dex-sub" }, " · ", use.conditions.map((key) => t(key)).join(" · ")) : null,
      places.length ? el("span", { class: "dex-sub" }, " · ", ...joined(places.map((section) => [this.sectionLink(section)]), ", ")) : null,
      use.speaker ? el("span", { class: "dex-sub", title: use.speaker }, ` · ${this.view.text(use.speaker)}`) : null);
  }


  /** List costs, rewards, places and speakers. */
  itemUseBlock(record) {
    const uses = record.uses ?? [];
    if (!uses.length) return [];
    return [el("section", { class: "block dex-item-uses" }, el("h2", {}, t("Uses")),
      el("ul", { class: "dex-source-list" }, ...uses.map((use) => this.itemUseRow(use))))];
  }


  /** Show item-triggered evolutions with their conditions. */
  itemEvolutionBlock(record) {
    const rows = itemEvolutions(record.id, this.data.species ?? []);
    if (!rows.length) return [];
    const text = (id) => this.view.text(id);
    const chip = (species) => el("button", {
      type: "button", class: "dex-link dex-learner", title: species?.name ?? "", disabled: !species,
      on: { click: () => species && this.view.openSpecies(species.slot) },
    }, species?.sprite?.front ? el("img", { src: this.host.spriteSrc(species.sprite.front), alt: "", loading: "lazy" }) : null,
    el("span", { class: "dex-learner-name" }, (species && this.view.speciesName?.(species)) || text(species?.name) || t("Species {n}", { n: species?.slot })));
    return [el("section", { class: "block" }, el("h2", {}, t("Evolutions")),
      el("div", { class: "dex-item-evos" }, ...rows.map(({ source, target, edge }) => el("div", { class: "dex-item-evo" },
        chip(source), el("span", { class: "dex-ability-arrow" }, "→"), chip(target),
        el("span", { class: "dex-sub" }, evolutionCondition(edge, text, this.data.items ?? []))))))];
  }


  /** Show species learning a move and their methods. */
  learnerTable(record) {
    if (this.view.learnsetsPending?.()) { // Load learnsets on first use.
      const generation = this.view.generation;
      const current = () => this.view.mode === "moves" && generation === this.view.generation && this.record(this.cur) === record;
      if (this.learnerFailure?.record === record && this.learnerFailure.generation === generation) {
        return el("p", { class: "muted", role: "status" }, siteText("Could not load the learners."), " ", el("button", {
          type: "button", class: "btn sm", on: { click: () => {
            if (!current()) return;
            this.learnerFailure = null;
            this.renderDetail(this.cur);
          } },
        }, siteText("Retry")));
      }
      this.view.ensureLearnsets().then((ok) => {
        if (!current()) return;
        this.learnerFailure = ok ? null : { record, generation };
        this.renderDetail(this.cur);
      });
      return el("p", { class: "muted", role: "status" }, t("Loading the learners…"));
    }
    this.learners ??= learnerIndex(this.data.species ?? []);
    const rows = this.learners.get(record.id) ?? [];
    if (!rows.length) return el("p", { class: "muted" }, t("No species learns it."));
    const nameOf = (entry) => {
      const species = this.view.record(entry.slot);
      return (species && this.view.speciesName?.(species)) || this.view.text(species?.name) || `species:${entry.slot}`;
    };
    // Size columns for the longest name to prevent clipping.
    const measure = nameMeasure();
    return el("div", { class: "dex-learners" }, ...learnerGroupsOf(rows).map((group) =>
      el("div", { class: "dex-learn" }, el("h3", {}, `${group.title} · ${group.entries.length}`),
        el("div", { class: "dex-learner-grid",
          style: `--learner-col:${learnerColumnWidth(group.entries.map(nameOf), measure,
            group.method === 0 ? group.entries.map(learnLabel) : [])}px` },
        ...group.entries.map((entry) => {
          const species = this.view.record(entry.slot);
          const name = nameOf(entry);
          return el("button", {
            type: "button", class: "dex-link dex-learner", title: species?.name ?? "", disabled: !species,
            on: { click: () => species && this.view.openSpecies(species.slot) },
          }, species?.sprite?.front ? el("img", { src: this.host.spriteSrc(species.sprite.front), alt: "", loading: "lazy" }) : null,
          el("span", { class: "dex-learner-name" }, name),
          group.method === 0 ? el("span", { class: "dex-sub" }, learnLabel(entry)) : null);
        })))));
  }



  /** Render the currency glyph, falling back to its accessible name. */
  pokedollarIcon() {
    const label = t("Pokédollar");
    const icon = this.data.icons?.pokedollar;
    const node = el("span", { "data-key": "pokedollar", title: label, role: "img", "aria-label": label }, label);
    const masks = globalThis.CSS?.supports;
    if (!icon || !masks || (!globalThis.CSS.supports("mask-image", 'url("")')
      && !globalThis.CSS.supports("-webkit-mask-image", 'url("")'))) return node;
    const source = this.host.spriteSrc(icon);
    const mask = (target) => {
      target.className = "dex-pokedollar-icon";
      target.style.cssText = `--icon:url("${source}")`;
      target.replaceChildren();
    };
    this.pokedollarLoaded ??= new Set();
    this.pokedollarPending ??= new Map();
    if (this.pokedollarLoaded.has(source)) {
      mask(node);
      return node;
    }
    const pending = this.pokedollarPending.get(source);
    if (pending) {
      pending.add(node);
      return node;
    }
    const nodes = new Set([node]);
    this.pokedollarPending.set(source, nodes);
    const image = new Image();
    image.onload = () => {
      this.pokedollarLoaded.add(source);
      this.pokedollarPending.delete(source);
      for (const target of nodes) mask(target);
      nodes.clear();
    };
    image.onerror = () => {
      this.pokedollarPending.delete(source);
      for (const target of nodes) {
        target.className = "";
        target.style.cssText = "";
        target.replaceChildren(target.title);
      }
      nodes.clear();
    };
    image.src = source;
    return node;
  }


  /** Render category icon masks, falling back to text labels. */
  categoryIcon(category) {
    const label = CATEGORY[category] ? t(CATEGORY[category]) : String(category);
    const icon = this.data.icons?.split?.[category];
    // Keyed per category: a restyled node keeps its mask image.
    return icon ? el("span", {
      class: `dex-cat-icon dex-cat-${category}`, "data-key": `category-${category}`, title: label, role: "img", "aria-label": label,
      style: `--icon:url("${this.host.spriteSrc(icon)}")`,
    }) : el("span", { "data-key": `category-${category}` }, label);
  }


  moveDetail(record) {
    const text = (id) => this.view.text(id);
    const list = fieldList(moveFields(record, text, { types: this.data.types ?? [], link: (id) => this.itemLink(id) }));
    const type = list.children[1];
    if (type) updateChildren(type, typeChip(record.type, text(`types:${record.type}`), this.data.types ?? []));
    const category = list.children[3];
    const icon = this.categoryIcon(record.damage_category);
    if (category) updateChildren(category, icon, ...(String(icon.className).startsWith("dex-cat-icon") ? [` ${CATEGORY[record.damage_category] ? t(CATEGORY[record.damage_category]) : ""}`] : []));
    const about = el("section", { class: "block dex-data-about" },
      ...flow(text(record.description)).map((part) => el("p", { title: record.description ?? "" }, part)),
      ...moveMechanics(record, { text, types: this.data.types ?? [], move: (id) => this.view.moveLink(id),
        location: (section) => this.sectionLink(section) }),
      el("div", { class: "dex-line" }, el("span", { class: "muted" }, t("Flags")),
        moveProperties(record, this.data.move_readers ?? {}, text, (id) => this.itemLink(id))));
    return el("div", { class: "dex-data-detail dex-data-card" },
      el("div", { class: "dex-panels" }, el("section", { class: "block" }, list), about),
      el("section", { class: "block" }, el("h2", {}, t("Learned by")), this.learnerTable(record)),
      el("details", { class: "block dex-record" }, el("summary", {}, t("Technical record")), fieldList(moveFacts(record))));
  }


  /** Render learned moves with types, categories and values. */
  learnRow(entry, first, machine = false) {
    const move = (this.data.moves ?? [])[entry.move];
    const name = this.view.text(`moves:${entry.move}`) || `moves:${entry.move}`;
    return el("tr", {},
      el("td", { class: machine ? "dex-sub" : "dex-sub num" }, first),
      el("td", {}, el("button", {
        type: "button", class: "dex-link", title: `moves:${entry.move}`,
        on: { click: () => this.view.openMove(entry.move) },
      }, name)),
      el("td", {}, move ? typeChip(move.type, this.view.text(`types:${move.type}`), this.data.types ?? []) : ""),
      el("td", {}, move ? this.categoryIcon(move.damage_category) : ""),
      el("td", { class: "num" }, powerLabel(move)),
      el("td", { class: "num" }, move && move.accuracy ? String(move.accuracy) : "—"),
      el("td", { class: "num" }, move ? String(move.pp) : ""));
  }


  /** Render one species move table per learning method. */
  learnsetTables(record) {
    const groups = learnsetGroups(record, this.data.items ?? []);
    const head = (machine) => el("thead", {}, el("tr", {},
      ...[machine ? t("TM and HM") : t("Level"), t("Move"), t("Type"), t("Category"), t("Pow."), t("Acc."), t("PP")]
        .map((label, i) => el("th", { scope: "col", class: i >= 4 || (i === 0 && !machine) ? "num" : "" }, label))));
    const table = (title, rows, machine = false) => el("div", { class: "dex-learn" },
      el("h3", {}, title),
      el("div", { class: "dex-learn-scroll", role: "region", tabindex: "0", "aria-label": title },
        el("table", { class: "t-forms dex-enc" }, head(machine), el("tbody", {}, ...rows))));
    const out = [];
    if (groups.level_up.length) {
      out.push(table(t("Level up"), groups.level_up.map((entry) => this.learnRow(entry, entry.level ? t("Lv {n}", { n: entry.level }) : t("Start")))));
    }
    if (groups.tmhm.length) {
      out.push(table(t("TM and HM"), groups.tmhm.map((entry) => this.learnRow(entry,
        entry.item != null && itemText(entry.item, (id) => this.view.text(id), this.data.items ?? []) ? this.itemLink(entry.item) : t("TM {n}", { n: entry.index + 1 }), true)), true));
    }
    if (groups.tutor.length) out.push(table(t("Tutor"), groups.tutor.map((entry) => this.learnRow(entry, "—"))));
    if (groups.egg.length) out.push(table(t("Egg"), groups.egg.map((entry) => this.learnRow(entry, "—"))));
    return out;
  }
};
