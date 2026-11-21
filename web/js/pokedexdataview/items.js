import { siteText, t, uiLanguage } from "../ui.js";


export const DATA_MODES = ["moves", "items", "trainers", "locations"];

export const POCKETS = { 1: "Items", 2: "Key items", 3: "Poké Balls", 4: "TMs and HMs", 5: "Berries" };

export const ROW = 64;

export const PICKUP_KINDS = ["ground", "hidden", "gift", "exchange"];

export const CATEGORY = ["Physical", "Special", "Status"];

export const TARGET_KEYS = {
  0: "selected", 1: "depends", 2: "user_or_partner", 4: "random",
  8: "both", 16: "user", 32: "foes_and_ally", 64: "opponents_field",
};

export const LEARN_METHODS = { 0: "level_up", 1: "tmhm", 2: "egg", 3: "tutor" };


/** Format declared amounts in the interface language. */
export function itemAmount(record, values, text) {
  return t(record.held_effect_amount?.key ?? record.held_effect_key, {
    n: record.hold_effect_param,
    type: record.held_effect_param_name ? text(record.held_effect_param_name) || record.held_effect_param_name : "",
    ...Object.fromEntries(Object.entries(values ?? {}).map(([hole, value]) => [hole, value.toLocaleString(uiLanguage())])),
  });
}


/** Resolve effect parameters through the selected language. */
export function itemFields(record, text = () => "", pokedollarIcon = null) {
  const priceLabel = (label) => pokedollarIcon ? [label, " ", pokedollarIcon()] : `${label} (${t("Pokédollar")})`;
  const fields = [
    [t("Pocket"), POCKETS[record.pocket] ? t(POCKETS[record.pocket]) : t("Pocket {n}", { n: record.pocket })],
    [priceLabel(siteText("Base buy price")), record.price],
  ];
  if (Number.isInteger(record.sell_price) && record.sell_price >= 0) {
    fields.push([priceLabel(siteText("Base sell price")), record.sell_price]);
  }
  if (record.tm_index != null) fields.push([t("Machine"), t("TM {n}", { n: record.tm_index + 1 })]);
  const amount = record.held_effect_amount;
  if (record.held_effect_key) {
    let effect = itemAmount(record, amount?.values, text);
    const conditions = (amount?.conditions ?? []).map((key) => t(key));
    if (conditions.length && !/[.!?]$/.test(effect)) effect += ".";
    fields.push([t("Held effect"), [effect, ...conditions].join(" ")]);
  }
  if (record.battle_usage_key) fields.push([t("Battle use"), t(record.battle_usage_key)]);
  return fields;
}
