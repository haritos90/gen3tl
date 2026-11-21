// Script metadata selects consistent site font fallbacks.
const LETTER = /[\p{L}\p{M}]/u;
const GENERIC = new Set(["system-ui", "-apple-system", "ui-monospace", "sans-serif", "monospace"]);
const installations = new WeakMap();

export const FONT_MANIFEST_URL = new URL("../fonts/coverage.json", import.meta.url);

const inRanges = (code, ranges) => ranges.some((range) => {
  const [first, last = first] = range.split("-").map((value) => Number.parseInt(value, 16));
  return code >= first && code <= last;
});

const family = (name) => GENERIC.has(name) ? name : JSON.stringify(name);

/** The fallback stack of one script and typography role. */
export function fontStack(manifest, role, scriptId) {
  const script = manifest.scripts.find((item) => item.id === scriptId);
  const settings = manifest.roles[role];
  if (!script || !settings) return "";
  const bundled = script.subsets[role]?.length ? [settings.family] : [];
  const fallback = script.fallbacks?.[role] ?? settings.fallback;
  return [...bundled, ...fallback].map(family).join(", ");
}

/** The script whose coverage determines the whole element's stack. */
export function fontScript(text, manifest, role) {
  const found = [];
  for (const character of text) {
    if (!LETTER.test(character)) continue;
    const code = character.codePointAt(0);
    const script = manifest.scripts.find((item) => inRanges(code, item.ranges));
    if (script && !found.includes(script)) found.push(script);
  }
  return (found.find((script) => !script.subsets[role]?.length) ?? found[0])?.id ?? null;
}

/** CSS rules generated entirely from script coverage metadata. */
export function fontRules(manifest) {
  const defaults = Object.keys(manifest.roles).map((role) => {
    const script = manifest.scripts.find((item) => item.subsets[role]?.length) ?? manifest.scripts[0];
    return `--font-${role}: ${fontStack(manifest, role, script.id)};`;
  });
  const rules = [`:root { ${defaults.join(" ")} }`];
  for (const [role, settings] of Object.entries(manifest.roles)) {
    if (!settings.selector) continue;
    for (const script of manifest.scripts) {
      const selector = settings.selector.split(",")
        .map((part) => `${part.trim()}[data-font-script="${script.id}"]`).join(", ");
      rules.push(`${selector} { font-family: ${fontStack(manifest, role, script.id)}; }`);
    }
  }
  return rules;
}

/** Mark headings and mono elements with their determining script. */
export function refreshSiteFonts(root, manifest) {
  for (const [role, settings] of Object.entries(manifest.roles)) {
    if (!settings.selector) continue;
    for (const node of root.querySelectorAll?.(settings.selector) ?? []) {
      const script = fontScript(node.textContent, manifest, role);
      if (script) node.setAttribute("data-font-script", script);
      else node.removeAttribute("data-font-script");
    }
  }
}

/** Add generated rules to the existing same-origin stylesheet. */
export function installFontRules(document, rules) {
  const sheet = [...(document.styleSheets ?? [])].find((item) => item.href
    && new URL(item.href, document.baseURI).pathname.endsWith("/site.css"));
  if (!sheet) return false;
  for (const rule of rules) sheet.insertRule(rule, sheet.cssRules.length);
  return true;
}

/** Load script metadata and follow later text changes. */
export function installSiteFonts(root = globalThis.document, fetcher = fetch) {
  if (!root?.querySelectorAll) return Promise.resolve(false);
  if (installations.has(root)) return installations.get(root);
  const loading = (async () => {
    try {
      const response = await fetcher(FONT_MANIFEST_URL.href, { credentials: "omit", cache: "no-cache" });
      if (!response?.ok) return false;
      const manifest = await response.json();
      const document = root.ownerDocument ?? root;
      installFontRules(document, fontRules(manifest));
      refreshSiteFonts(root, manifest);
      const Observer = document.defaultView?.MutationObserver;
      if (Observer && document.body) {
        const observer = new Observer(() => refreshSiteFonts(root, manifest));
        observer.observe(document.body, { childList: true, subtree: true, characterData: true });
      }
      return true;
    } catch {
      return false;
    }
  })();
  installations.set(root, loading);
  return loading;
}
