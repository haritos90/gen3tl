// Guide pages follow the site language.
import { startGuide } from "./ui.js";
import { startPage } from "./site-shell.js";

await startGuide({ start: startPage, family: document.body.dataset.guide ?? "guide" });
