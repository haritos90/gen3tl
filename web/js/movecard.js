// A compact move card in a dialog.
// A click on a move name opens it.

import { el } from "./dom.js";
import { effectName, powerLabel } from "./moveeffects.js";
import { typeChip } from "./pokedex/pokedex.js";
import { CATEGORY, flinchFields, itemFields, moveMechanics, moveProperties, moveTarget, powerFormula } from "./pokedexdataview/pokedexdataview.js";
import { t } from "./ui.js";

/** The card's content for a move record. */
export function moveCardContent(record, { text = () => "", types = [], readers = {}, categoryIcon = null, link, open, move, location } = {}) {
  const figure = (label, value) => el("div", { class: "dex-figure" },
    el("b", {}, value), el("span", {}, label));
  const category = CATEGORY[record.damage_category] ? t(CATEGORY[record.damage_category]) : String(record.damage_category);
  const chance = !(record.flinch_chance > 0) && record.secondary_chance > 0 ? ` · ${record.secondary_chance}%` : "";
  return el("div", { class: "dex-move-card-body" },
    el("div", { class: "dex-move-card-head" },
      el("h3", {}, text(`moves:${record.id}`) || `moves:${record.id}`),
      typeChip(record.type, text(`types:${record.type}`), types),
      categoryIcon ? el("span", { class: "dex-move-card-category", title: category }, categoryIcon(record.damage_category), ` ${category}`)
        : el("span", { class: "dex-move-card-category" }, category)),
    el("div", { class: "dex-figures" },
      figure(t("Power"), powerLabel(record)),
      figure(t("PP"), String(record.pp)),
      figure(t("Accuracy"), record.accuracy ? String(record.accuracy) : "—")),
    ...(text(record.description) ? [el("p", { class: "dex-move-card-text" }, text(record.description).split(/\\[nlp]/).join(" "))] : []),
    el("dl", { class: "kv dex-move-card-kv" },
      ...(record.power_formula ? [el("dt", {}, t("Power")), el("dd", {}, powerFormula(record, { text, types, link }))] : []),
      el("dt", {}, t("Target")), el("dd", {}, moveTarget(record)),
      el("dt", {}, t("Effect")), el("dd", {}, `${effectName(record.effect, record.effect_key)}${chance}`),
      ...flinchFields(record).flatMap(([label, value]) => [el("dt", {}, label), el("dd", {}, value)])),
    ...moveMechanics(record, { text, types, move, location }),
    el("div", { class: "dex-line" }, el("span", { class: "muted" }, t("Flags")), moveProperties(record, readers, text, link)),
    el("div", { class: "dex-move-card-foot" }, el("button", {
      type: "button", class: "dex-link", on: { click: () => open?.(record.id) },
    }, `${t("Open in Pokédex")} →`)));
}

/** The item card's content for an item record. */
export function itemCardContent(record, { text = () => "", icon = null, pokedollarIcon = null, taught = null, evolutions = [], open } = {}) {
  const description = text(record.description);
  return el("div", { class: "dex-move-card-body" },
    el("div", { class: "dex-move-card-head" }, icon, el("h3", {}, text(record.name) || `items:${record.id}`)),
    el("dl", { class: "kv dex-move-card-kv" }, ...itemFields(record, text, pokedollarIcon).flatMap(([key, value]) =>
      [el("dt", {}, key), el("dd", {}, typeof value === "object" ? value : String(value))])),
    ...(taught ? [el("div", { class: "dex-line" }, el("span", { class: "muted" }, t("Teaches")), taught)] : []),
    ...(description ? [el("p", { class: "dex-move-card-text" }, description.split(/\\[nlp]/).join(" "))] : []),
    ...evolutions,
    el("div", { class: "dex-move-card-foot" }, el("button", {
      type: "button", class: "dex-link", on: { click: () => open?.(record.id) },
    }, `${t("Open in Pokédex")} →`)));
}

/** Show the species-specific ability name and description. */
export function abilityCardContent(record, text = () => "") {
  const description = text(record.description);
  return el("div", { class: "dex-move-card-body dex-ability-card" },
    el("div", { class: "dex-move-card-head" }, el("h3", {}, text(record.name) || t("Ability {n}", { n: record.id }))),
    el("p", { class: "dex-move-card-text" }, description.split(/\\[nlp]/).join(" ")));
}

/** A dialog hosting one card at a time. */
export class MoveDialog {
  constructor(doc = globalThis.document) {
    this.doc = doc;
    this.node = null;
    this.returnFocus = null;
  }

  /** Whether the page can host a dialog. */
  get available() {
    return !!(this.doc?.body?.append && typeof this.doc.createElement === "function");
  }

  element(label = t("Move card")) {
    if (this.node) {
      this.node.setAttribute?.("aria-label", label);
      return this.node;
    }
    this.node = el("dialog", { class: "dex-dialog", "aria-label": label });
    this.node.addEventListener?.("click", (event) => { if (event.target === this.node) this.close(); });
    this.node.addEventListener?.("cancel", (event) => { event.preventDefault(); this.close(); });
    this.doc.body.append(this.node);
    return this.node;
  }

  show(content, label = t("Move card")) {
    const dialog = this.element(label);
    dialog.replaceChildren(el("button", {
      type: "button", class: "dex-dialog-close", "aria-label": t("Close"), title: t("Close"), on: { click: () => this.close() },
    }, "×"), content);
    if (!dialog.open) {
      this.returnFocus = this.doc.activeElement;
      dialog.showModal?.();
    }
  }

  close() {
    if (this.node?.open) {
      this.node.close?.();
      this.returnFocus?.focus?.();
      this.returnFocus = null;
    }
  }
}
