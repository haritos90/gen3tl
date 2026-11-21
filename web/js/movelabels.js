// English labels for the proven move keys.

export const TARGET_LABELS = {
  selected: "Selected target",
  depends: "Depends on the move",
  user_or_partner: "User or ally",
  random: "Random opponent",
  both: "Both opponents",
  user: "User",
  foes_and_ally: "All others",
  opponents_field: "Opponents' side",
};

export const FLAG_LABELS = {
  makes_contact: "Makes contact",
  protect_affected: "Affected by Protect",
  magic_coat_affected: "Affected by Magic Coat",
  snatch_affected: "Affected by Snatch",
  mirror_move_affected: "Affected by Mirror Move",
  kings_rock_affected: "Affected by King's Rock",
  triage_affected: "Affected by Triage",
};

export const MOVE_LIST_LABELS = {
  reckless: "Reckless-boosted",
  punching: "Punching",
  sheer_force: "Sheer Force-boosted",
  pulse_aura: "Pulse and aura",
  biting: "Biting",
  dance: "Dance",
  powder: "Powder",
  sound: "Sound",
  ballistic: "Ball and bomb",
  me_first_banned: "Banned from Me First",
  gravity_banned: "Banned by Gravity",
  substitute_bypass: "Bypasses substitutes",
  type_change_exception: "Type-change exception",
  metronome_banned: "Banned from Metronome",
  sky_battle_banned: "Banned from Sky Battles",
  parental_bond_banned: "Banned from Parental Bond",
  quiet_bgm: "Quiet background music",
};

const EFFECT_LABELS = {
  "0hko": "One-hit KO",
  restore_hp: "Restore HP",
  sonicboom: "Sonic Boom",
  solarbeam: "Solar Beam",
  smellingsalt: "Smelling Salts",
  extreme_evoboost: "Extreme EvoBoost",
  bad_poison_hit: "Badly poison hit",
  quick_attack: "Quick Attack",
  dragon_dance: "Dragon Dance",
};

export function targetName(key) {
  return TARGET_LABELS[key] ?? null;
}

export function flagName(key) {
  return FLAG_LABELS[key] ?? null;
}

export function moveListName(key) {
  return MOVE_LIST_LABELS[key] ?? null;
}

export function effectLabel(key) {
  if (!key) return null;
  if (EFFECT_LABELS[key]) return EFFECT_LABELS[key];
  const words = key.replaceAll("_", " ");
  return words[0].toUpperCase() + words.slice(1);
}
