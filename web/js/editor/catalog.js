import { tableKey, loadAliases } from "../data.js";
import { gameHash } from "../registry.js";
import { state, DATA } from "./state.js";




/** The hash of an entry: its key is one segment. */
export function entryHash(id) {
  return gameHash(state.profile.id, state.lang.code, id);
}




/** The keys of older inventory ids, loaded once per language. */
export function aliases() {
  if (!state.aliases) state.aliases = loadAliases(new URL("../keys.json", new URL(state.lang.index, DATA())).href);
  return state.aliases;
}




/** The entry an id names; null when none. */
export async function entryKey(id) {
  if (!id || state.byId.has(id)) return id || null;
  const generation = state.generation;
  const key = (await aliases()).get(id) ?? tableKey(id);
  return generation === state.generation && state.byId.has(key) ? key : null;
}
