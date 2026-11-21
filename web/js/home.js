// The landing page: the registry, then each game's data.
import { setToolContext, startPage } from "./site-shell.js";
import { collapse, landingModel, landingRedirect, loadSources, render, renderCopy, renderLoading } from "./landing.js";
import { loadRegistry, rememberGame, siteRoute } from "./registry.js";
import { installSiteFonts } from "./site-fonts.js";
import { errorText, onSiteLanguage, t } from "./ui.js";

const $ = (id) => document.getElementById(id);

let shown = null; // what the page draws again after a language switch

function showError(e) {
  $("games-error").textContent = t("The game list could not be loaded: {error}", { error: errorText(e) });
  $("games-error").hidden = false;
  $("games-list").hidden = true;
}

onSiteLanguage(() => shown?.());
window.addEventListener("hashchange", () => shown?.());
const registryPromise = loadRegistry(new URL("data/registry.json", document.baseURI).href);
await startPage({ reveal: () => {} });
try {
  const registry = await registryPromise;
  const redirect = landingRedirect(registry, location.hash);
  if (redirect) location.replace(redirect);
  else {
    renderLoading(registry, $);
    if (!registry.games.length) document.body.classList.add("no-games");
    renderCopy(landingModel(registry, new Map(), location.hash).current, $);
    await installSiteFonts(document);
    const fontText = document.body.textContent;
    await Promise.all([document.fonts?.load('400 15px "IBM Plex Sans"', fontText),
      document.fonts?.load('600 30px "Bricolage Grotesque"', fontText)]);
    await document.fonts?.ready;
    document.siteStartup?.reveal();
    const sources = await loadSources(registry);
    shown = () => {
      $("games-error").hidden = !registry.skipped.length;
      $("games-error").textContent = registry.skipped.length ? t("A game could not be loaded. Reload to try again.") : "";
      const model = landingModel(registry, sources, location.hash);
      if (model.current) rememberGame(model.current.id);
      setToolContext(model.current?.id, siteRoute(location.hash, registry).language);
      render(model, $, new URLSearchParams(location.search).get("unavailable"));
    };
    shown();
  }
} catch (e) {
  console.error(e);
  shown = () => showError(e);
  shown();
} finally {
  document.siteStartup?.reveal();
  $("games-loading").hidden = true;
  if ($("games-empty").hidden) $("games-placeholders").hidden = true;
  else collapse($("games-placeholders"));
}
