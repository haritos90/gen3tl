export const PAGES = { patch: "patch", editor: "editor", save: "save", pokedex: "pokedex" };

/** Keep route segments independently encoded. */
export function gameHash(game, language = null, ...tail) {
  return `#${[game, language, ...tail].filter((x) => x !== null && x !== undefined && x !== "")
    .map(encodeURIComponent).join("/")}`;
}

/** Return to the requested tool after selection. */
export const pickerURL = (next) => `pick${next ? `?next=${encodeURIComponent(next)}` : ""}`;

/** Preserve context across tools; omit unrelated workspace details. */
export function toolURL(tool, game = null, language = null, ...tail) {
  if (!game || (game === "site" && !["editor", "submit"].includes(tool))) return pickerURL(tool);
  return `${PAGES[tool] ?? tool}${gameHash(game, language, ...tail)}`;
}
