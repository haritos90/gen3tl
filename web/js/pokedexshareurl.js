const ID = /^[a-zA-Z0-9][a-zA-Z0-9._-]*$/;
const LANGUAGE = /^[a-z]{2,3}(?:-[A-Za-z0-9]+)?$/;

function parts(game, language, slot) {
  if (!ID.test(game) || !LANGUAGE.test(language) || !Number.isSafeInteger(slot) || slot < 1) {
    throw new Error("Invalid species share identity");
  }
  return [game, language, "species", String(slot)].map(encodeURIComponent);
}

export function speciesShareURL(data, game, language, slot, pages = null) {
  const [profile, lang, kind, id] = parts(game, language, slot);
  const has = (code) => pages == null || pages[code] === "all" || !!pages[code]?.includes(slot);
  const fallback = !has(language);
  if (fallback && !has("en")) return null;
  const url = new URL(`../share/${profile}/${fallback ? "en" : lang}/${kind}/${id}/`, data);
  if (fallback) url.searchParams.set("lang", language);
  return url.href;
}

export function speciesCardURL(base, game, language, slot) {
  const url = new URL("pokedex", base);
  url.hash = parts(game, language, slot).join("/");
  return url.href;
}

export function pokedexRouteURL(base, current, hash, species = null) {
  const url = new URL(current, base);
  url.pathname = new URL("pokedex", base).pathname;
  url.hash = hash;
  const shared = species && speciesShareURL(species.data, species.game, species.language, species.slot, species.pages);
  if (!shared) return url.href;
  const target = new URL(shared);
  if (target.origin !== url.origin) return url.href;
  url.searchParams.delete("lang");
  for (const [key, value] of url.searchParams) target.searchParams.append(key, value);
  target.hash = url.hash;
  return target.href;
}
