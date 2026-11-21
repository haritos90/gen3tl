// A rough capture estimate using FireRed ball rules.
import { el } from "./dom.js";
import { t } from "./ui.js";

/** Ball rules by stable item id. */
export const BALLS = new Map([
  [4, { name: "Poké Ball", mul: 1 }],
  [3, { name: "Great Ball", mul: 1.5 }],
  [2, { name: "Ultra Ball", mul: 2 }],
  [1, { name: "Master Ball", master: true }],
  [5, { name: "Safari Ball", mul: 1.5 }],
  [629, { name: "Sport Ball", mul: 1.5 }],
  [6, { name: "Net Ball", mul: 3.5, auto: "net" }],
  [7, { name: "Dive Ball", mul: 3.5, ask: "dive" }],
  [8, { name: "Nest Ball", rule: "nest" }],
  [9, { name: "Repeat Ball", mul: 3.5, ask: "repeat" }],
  [10, { name: "Timer Ball", rule: "timer" }],
  [60, { name: "Dusk Ball", mul: 3, ask: "dusk" }],
  [62, { name: "Quick Ball", mul: 5, ask: "quick", fullHp: true }],
  [622, { name: "Fast Ball", mul: 4, auto: "fast" }],
  [623, { name: "Level Ball", rule: "level" }],
  [624, { name: "Lure Ball", mul: 5, ask: "lure" }],
  [625, { name: "Heavy Ball", rule: "heavy" }],
  [626, { name: "Love Ball", mul: 8, ask: "love" }],
  [628, { name: "Moon Ball", mul: 4, auto: "moon" }],
  [630, { name: "Beast Ball", rule: "beast" }],
  [631, { name: "Dream Ball", mul: 4, auto: "asleep" }],
  [11, { name: "Luxury Ball", mul: 1 }],
  [12, { name: "Premier Ball", mul: 1 }],
  [61, { name: "Heal Ball", mul: 1 }],
  [53, { name: "Cherish Ball", mul: 1 }],
  [52, { name: "Park Ball", mul: 1 }],
  [627, { name: "Friend Ball", mul: 1 }],
]);
export const MOON_STONE_ID = 94;
const NET_BALL_TYPES = new Set([6, 11]);
/** The question a ball asks before its multiplier counts. */
export const CONDITION_LABELS = {
  dusk: "Night or cave", quick: "First turn", dive: "Surfing or fishing", lure: "Fishing",
  repeat: "Already caught", love: "Same species, opposite gender",
};
export const STATUS_LABELS = { none: "None", sleep: "sleep", paralysis: "paralysis", burn: "burn", poison: "poison" };
const STATUS_BONUS = { none: 1, sleep: 2.5, paralysis: 1.5, burn: 1.5, poison: 1.5 };
const ULTRA_BEASTS = new Set([793, 794, 795, 796, 797, 798, 799, 803, 804, 805, 806]);
const ITEM_METHOD = 7; // evolution by using an item

/** Max HP at a level, average IVs, no EVs. */
export function maxHp(baseHp, level) {
  if (!(baseHp > 1)) return 1;
  return Math.floor((2 * baseHp + 15) * level / 100) + level + 10;
}

/** Multiplier and rate addend of a ball for a throw. */
export function ballFactor(ball, facts) {
  const spec = BALLS.get(ball) ?? { mul: 1 };
  if (spec.master) return { mul: 1, add: 0, master: true };
  if (spec.ask) return { mul: facts.condition ? spec.mul : 1, add: 0 };
  switch (spec.auto) {
    case "net": return { mul: (facts.types ?? []).some((id) => NET_BALL_TYPES.has(id)) ? spec.mul : 1, add: 0 };
    case "fast": return { mul: (facts.speed ?? 0) >= 100 ? spec.mul : 1, add: 0 };
    case "moon": return { mul: facts.moonFamily ? spec.mul : 1, add: 0 };
    case "asleep": return { mul: facts.status === "sleep" ? spec.mul : 1, add: 0 };
    default: break;
  }
  switch (spec.rule) {
    case "nest": return { mul: facts.level < 30 ? (41 - facts.level) / 10 : 1, add: 0 };
    case "timer": return { mul: Math.min(4, 1 + 0.3 * (Math.max(1, facts.turn ?? 1) - 1)), add: 0 };
    case "level": {
      const ratio = (facts.yourLevel ?? 0) / Math.max(1, facts.level ?? 1);
      return { mul: ratio >= 4 ? 8 : ratio >= 2 ? 4 : ratio > 1 ? 2 : 1, add: 0 };
    }
    case "heavy": {
      const kg = facts.weight ?? 0;
      return { mul: 1, add: kg < 100 ? -20 : kg < 200 ? 0 : kg < 300 ? 20 : 30 };
    }
    case "beast": return { mul: ULTRA_BEASTS.has(facts.dex) ? 5 : 0.1, add: 0 };
    default: return { mul: spec.mul ?? 1, add: 0 };
  }
}

/** The chance of one throw: FireRed odds with status bonus. */
export function catchChance({ rate, hp, hpMax, mul = 1, add = 0, status = "none", master = false }) {
  if (master) return 1;
  const r = Math.max(1, Math.min(255, (rate ?? 0) + add));
  const m = Math.max(1, hpMax ?? 1);
  const h = Math.max(1, Math.min(m, hp ?? m));
  let a = Math.floor((3 * m - 2 * h) * r * mul / (3 * m));
  a = Math.floor(a * (STATUS_BONUS[status] ?? 1));
  return Math.max(0, Math.min(1, a / 255));
}

/** Every record joined to this one by evolution edges. */
export function familyOf(record, records) {
  const bySlot = new Map(records.map((r) => [r.slot, r]));
  const into = new Map();
  for (const r of records) {
    for (const edge of r.evolution ?? []) into.set(edge.target, [...(into.get(edge.target) ?? []), r.slot]);
  }
  const seen = new Set([record.slot]);
  const queue = [record.slot];
  while (queue.length) {
    const slot = queue.shift();
    const next = [...(bySlot.get(slot)?.evolution ?? []).map((edge) => edge.target), ...(into.get(slot) ?? [])];
    for (const other of next) if (!seen.has(other)) { seen.add(other); queue.push(other); }
  }
  return [...seen].map((slot) => bySlot.get(slot)).filter(Boolean);
}

/** The items the family's evolutions use, as item ids. */
export function familyItems(family) {
  return [...new Set(family.flatMap((r) => (r.evolution ?? [])
    .filter((edge) => edge.method === ITEM_METHOD).map((edge) => edge.parameter)))];
}

function fmt(n, locale) {
  return Number(n).toLocaleString(locale, { maximumFractionDigits: 1 });
}

// the last throw's settings survive a change of species
const state = { ball: 4, status: "none", hp: 100, condition: false, turn: 1, yourLevel: 50 };

/** Render the calculator from the species facts in `ctx`. */
export function renderCatchCalculator(root, ctx) {
  root.replaceChildren();
  if (ctx.rate == null || !ctx.balls?.length) return;
  if (!ctx.balls.some((b) => b.key === state.ball)) state.ball = ctx.balls[0].key;
  const ballSelect = el("select", { id: "x-catch-ball" },
    ...ctx.balls.map((b) => el("option", { value: b.key, selected: b.key === state.ball }, b.name)));
  const condition = el("input", { type: "checkbox", id: "x-catch-cond", checked: state.condition });
  const conditionText = el("span");
  const conditionRow = el("div", { class: "dex-catch-check" }, el("span"), el("label", {}, condition, conditionText));
  const turn = el("input", { type: "number", id: "x-catch-turn", min: 1, max: 99, value: state.turn, inputmode: "numeric" });
  const turnRow = el("label", {}, el("span", {}, t("Turn")), turn);
  const yours = el("input", { type: "number", id: "x-catch-yours", min: 1, max: 100, value: state.yourLevel, inputmode: "numeric" });
  const yoursRow = el("label", {}, el("span", {}, t("Your Pokémon's level")), yours);
  const hp = el("input", { type: "range", id: "x-catch-hp", min: 0, max: 100, value: state.hp });
  const bar = el("i");
  const hpBar = el("span", { class: "dex-hpbar" }, bar);
  const hpValue = el("output", { class: "mono" });
  const status = el("select", { id: "x-catch-status" },
    ...Object.entries(STATUS_LABELS).map(([key, label]) => el("option", { value: key, selected: key === state.status }, t(label))));
  const level = el("input", { type: "number", id: "x-catch-level", min: 1, max: 100, value: ctx.level ?? 30, inputmode: "numeric" });
  const chance = el("b", { id: "x-catch-chance" });
  const throws = el("b", { id: "x-catch-throws" });
  const note = el("span", { id: "x-catch-note", class: "muted" });
  const update = () => {
    const spec = BALLS.get(state.ball) ?? {};
    conditionRow.hidden = !spec.ask;
    if (spec.ask) conditionText.textContent = t(CONDITION_LABELS[spec.ask]);
    turnRow.hidden = spec.rule !== "timer";
    yoursRow.hidden = spec.rule !== "level";
    const fullHp = Boolean(spec.fullHp && state.condition); // the first turn: nothing hit it yet
    if (fullHp) state.hp = 100;
    hp.disabled = fullHp;
    hp.value = state.hp;
    const lvl = Math.min(100, Math.max(1, Number(level.value) || ctx.level || 30));
    const hpMax = maxHp(ctx.baseHp, lvl);
    const current = Math.max(1, Math.round(hpMax * state.hp / 100));
    const frac = current / hpMax;
    bar.style.width = `${Math.max(1, Math.round(100 * frac))}%`;
    hpBar.className = `dex-hpbar${frac > 0.5 ? "" : frac > 0.2 ? " mid" : " low"}`;
    hpValue.textContent = `${current} / ${hpMax}`;
    const facts = { level: lvl, yourLevel: Number(yours.value) || 0, turn: Number(turn.value) || 1, condition: state.condition,
      status: state.status, types: ctx.types, speed: ctx.speed, weight: ctx.weight, dex: ctx.dex, moonFamily: ctx.moonFamily };
    const factor = ballFactor(state.ball, facts);
    const p = catchChance({ rate: ctx.rate, hp: current, hpMax, ...factor, status: state.status });
    chance.textContent = `${fmt(p * 100, ctx.locale)}%`;
    throws.textContent = p > 0 ? `≈ ${Math.max(1, Math.round(1 / p))}` : "—";
    note.textContent = factor.master ? "" : [t("×{n}", { n: fmt(factor.mul, ctx.locale) }),
      factor.add ? t("{n} / 255", { n: Math.max(1, Math.min(255, ctx.rate + factor.add)) }) : ""].filter(Boolean).join(" · ");
  };
  ballSelect.addEventListener("change", () => { state.ball = Number(ballSelect.value); update(); });
  condition.addEventListener("change", () => { state.condition = condition.checked; update(); });
  hp.addEventListener("input", () => { state.hp = Number(hp.value); update(); });
  status.addEventListener("change", () => { state.status = status.value; update(); });
  turn.addEventListener("input", () => { state.turn = Number(turn.value) || 1; update(); });
  yours.addEventListener("input", () => { state.yourLevel = Number(yours.value) || 0; update(); });
  level.addEventListener("input", update);
  root.append(
    el("h3", { title: t("An estimate: the HP bar stands in for the exact HP, IVs are averaged, no critical capture") },
      t("Catch calculator")),
    el("div", { class: "dex-catch-grid" },
      el("label", {}, el("span", {}, t("Poké Balls")), ballSelect),
      conditionRow, turnRow, yoursRow,
      el("label", {}, el("span", {}, t("HP")), el("span", { class: "dex-catch-hp" }, hp, hpBar, hpValue)),
      el("label", {}, el("span", {}, t("Status")), status),
      el("label", {}, el("span", {}, t("Lv")), level)),
    el("p", { class: "dex-catch-result" }, el("span", {}, t("Chance per throw")), " ", chance, el("span", { class: "dex-sep" }, " · "),
      el("span", {}, t("Throws on average")), " ", throws),
    el("p", { class: "dex-catch-note" }, note));
  update();
}
