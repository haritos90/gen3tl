import { effectLabel } from "./movelabels.js";
import { t } from "./ui.js";

/** Declared rules distinguish base power from placeholders. */
export function basePower(record) {
  return record?.power_kind === "computed" ? null : record?.power_base ?? record?.power ?? null;
}

/** Keep every power display consistent. */
export function powerLabel(record) {
  const power = basePower(record);
  return power ? String(power) : "—";
}

export const EFFECT_KEYS = {
  0: "hit", 1: "sleep", 2: "poison_hit", 3: "absorb", 4: "burn_hit", 5: "freeze_hit",
  6: "paralyze_hit", 7: "explosion", 8: "dream_eater", 9: "mirror_move", 10: "attack_up",
  11: "defense_up", 12: "speed_up", 13: "special_attack_up", 14: "special_defense_up",
  15: "accuracy_up", 16: "evasion_up", 17: "always_hit", 18: "attack_down",
  19: "defense_down", 20: "speed_down", 21: "special_attack_down", 22: "special_defense_down",
  23: "accuracy_down", 24: "evasion_down", 25: "haze", 26: "bide", 27: "rampage",
  28: "roar", 29: "multi_hit", 30: "conversion", 31: "flinch_hit", 32: "restore_hp",
  33: "toxic", 34: "pay_day", 35: "light_screen", 36: "tri_attack", 37: "rest",
  38: "0hko", 39: "razor_wind", 40: "super_fang", 41: "dragon_rage", 42: "trap",
  43: "high_critical", 44: "double_hit", 45: "recoil_if_miss", 46: "mist", 47: "focus_energy",
  48: "recoil", 49: "confuse", 50: "attack_up_2", 51: "defense_up_2", 52: "speed_up_2",
  53: "special_attack_up_2", 54: "special_defense_up_2", 55: "accuracy_up_2",
  56: "evasion_up_2", 57: "transform", 58: "attack_down_2", 59: "defense_down_2",
  60: "speed_down_2", 61: "special_attack_down_2", 62: "special_defense_down_2",
  63: "accuracy_down_2", 64: "evasion_down_2", 65: "reflect", 66: "poison", 67: "paralyze",
  68: "attack_down_hit", 69: "defense_down_hit", 70: "speed_down_hit", 71: "special_attack_down_hit",
  72: "special_defense_down_hit", 73: "accuracy_down_hit", 74: "evasion_down_hit",
  75: "sky_attack", 76: "confuse_hit", 77: "blank_77", 78: "blank_78", 79: "substitute",
  80: "recharge", 81: "rage", 82: "mimic", 83: "metronome", 84: "leech_seed",
  85: "splash", 86: "disable", 87: "level_damage", 88: "psywave", 89: "counter",
  90: "encore", 91: "pain_split", 92: "snore", 93: "conversion_2", 94: "lock_on",
  95: "sketch", 96: "speed_up_1_hit", 97: "sleep_talk", 98: "destiny_bond", 99: "flail",
  100: "spite", 101: "false_swipe", 102: "heal_bell", 103: "quick_attack", 104: "triple_kick",
  105: "thief", 106: "mean_look", 107: "nightmare", 108: "minimize", 109: "curse",
  110: "special_attack_up_chance", 111: "protect", 112: "spikes", 113: "foresight",
  114: "perish_song", 115: "sandstorm", 116: "blank_116", 117: "rollout", 118: "swagger",
  119: "fury_cutter", 120: "attract", 121: "blank_121", 122: "present", 123: "blank_123",
  124: "safeguard", 125: "burn_up", 126: "magnitude", 127: "baton_pass", 128: "pursuit",
  129: "rapid_spin", 130: "sonicboom", 131: "unused_83", 132: "morning_sun",
  133: "blank_133", 134: "blank_134", 135: "defense_up_2_hit", 136: "rain_dance",
  137: "sunny_day", 138: "defense_up_hit", 139: "attack_up_hit", 140: "all_stats_up_hit",
  141: "unused_8d", 142: "belly_drum", 143: "psych_up", 144: "mirror_coat", 145: "skull_bash",
  146: "twister", 147: "earthquake", 148: "future_sight", 149: "gust", 150: "blank_150",
  151: "solarbeam", 152: "thunder", 153: "teleport", 154: "beat_up", 155: "semi_invulnerable",
  156: "defense_curl", 157: "blank_157", 158: "fake_out", 159: "uproar", 160: "stockpile",
  161: "spit_up", 162: "swallow", 163: "unused_a3", 164: "hail", 165: "torment",
  166: "flatter", 167: "will_o_wisp", 168: "memento", 169: "blank_169", 170: "focus_punch",
  171: "smellingsalt", 172: "follow_me", 173: "nature_power", 174: "charge", 175: "taunt",
  176: "helping_hand", 177: "trick", 178: "role_play", 179: "wish", 180: "assist",
  181: "ingrain", 182: "superpower", 183: "magic_coat", 184: "recycle", 185: "blank_185",
  186: "brick_break", 187: "yawn", 188: "knock_off", 189: "endeavor", 190: "blank_190",
  191: "skill_swap", 192: "imprison", 193: "refresh", 194: "grudge", 195: "snatch",
  196: "blank_196", 197: "secret_power", 198: "blank_198", 199: "blank_199", 200: "blank_200",
  201: "mud_sport", 202: "blank_202", 203: "blank_203", 204: "overheat", 205: "tickle",
  206: "cosmic_power", 207: "extreme_evoboost", 208: "bulk_up", 209: "bad_poison_hit",
  210: "water_sport", 211: "calm_mind", 212: "dragon_dance", 213: "stat_swap_split",
  214: "blank_214", 215: "blank_215", 216: "blank_216", 217: "blank_217", 218: "me_first",
  219: "eat_berry", 220: "natural_gift", 221: "smack_down", 222: "remove_target_stat_changes",
  223: "relic_song", 224: "blank_224", 225: "blank_225", 226: "set_terrain", 227: "pledge",
  228: "field_effects", 229: "fling", 230: "feint", 231: "attack_blockers", 232: "type_changes",
  233: "heal_target", 234: "topsy_turvy_electrify", 235: "fairy_lock_happy_hour",
  236: "instruct_after_you_quash", 237: "sucker_punch", 238: "ignore_redirection",
  239: "team_effects", 240: "camouflage", 241: "flameburst", 242: "lastresort_skydrop",
  243: "damage_set_terrain", 244: "teatime", 245: "decorate", 246: "poltergeist",
  253: "max_move", 254: "synchronoise",
};

/** Localize effect keys with an English fallback. */
export function effectName(id, key = null) {
  const effect = key ?? EFFECT_KEYS[id];
  const english = effectLabel(effect);
  if (!english) return `Effect ${id}`;
  const label = `page:effect:${effect}`;
  const text = t(label);
  return text === label ? english : text;
}

export const EFFECT_COUNT = Object.keys(EFFECT_KEYS).length;

/** Shared effect ids can select different behavior. */
export function effectKey(record) {
  return record.effect_key ?? EFFECT_KEYS[record.effect] ?? String(record.effect);
}

/** Older saved filters used numeric effect ids. */
export function effectFilterKey(value) {
  return /^\d+$/.test(String(value)) ? EFFECT_KEYS[Number(value)] ?? String(value) : value;
}

/** Compare the displayed behavior, including saved filters. */
export function matchesEffect(record, value) {
  return effectKey(record) === effectFilterKey(value);
}

/** Keep filters aligned with displayed move effects. */
export function resolvedEffects(moves) {
  const keys = new Map(moves.map((row) => [effectKey(row), row]));
  return [...keys].map(([key, row]) => ({ key, name: effectName(row.effect, row.effect_key) }))
    .sort((a, b) => a.name.localeCompare(b.name));
}
