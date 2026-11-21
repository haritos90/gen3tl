import { chosenGame } from "./registry.js";
import { PAGES, toolURL } from "./routes.js";
import { chosenLanguage, onSiteLanguage, startSite, watchSiteBuild } from "./ui.js";

const contexts = new WeakMap();

/** Share the selected project across header links. */
export function setToolContext(game = null, language = null, root = document) {
  contexts.set(root, { game, language });
  for (const link of root.querySelectorAll("[data-tool]")) {
    const tool = link.dataset.tool;
    if (!Object.hasOwn(PAGES, tool)) continue;
    link.setAttribute("href", toolURL(tool, game, language ?? chosenLanguage()));
  }
}

/** Wire page context alongside interface metadata. */
export async function startPage(options = {}) {
  const root = options.root ?? document;
  watchSiteBuild(root, options.fetcher);
  setToolContext(null, null, root);
  const refresh = () => {
    const context = contexts.get(root);
    setToolContext(context?.game, context?.language, root);
  };
  onSiteLanguage(refresh);
  return startSite({ ...options, root, onRegistry: (registry, storage) => {
    if (!registry) return;
    if (contexts.get(root)?.game) return;
    const { game, route } = chosenGame(registry, globalThis.location?.hash ?? "", storage);
    setToolContext(game?.id, route.language, root);
  } });
}
