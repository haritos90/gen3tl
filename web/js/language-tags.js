// Shared before module startup and after it.
(() => {
  function canonicalLanguage(code) {
    if (typeof code !== "string" || !code.trim()) return null;
    try {
      return Intl.getCanonicalLocales(code.replaceAll("_", "-"))[0] ?? null;
    } catch {
      return null;
    }
  }

  function matchLanguage(requested, offered, fallback = null) {
    const wanted = canonicalLanguage(requested)?.toLowerCase();
    if (!wanted) return fallback;
    const available = new Map();
    for (const code of offered ?? []) {
      const canonical = canonicalLanguage(code);
      if (canonical && !available.has(canonical.toLowerCase())) available.set(canonical.toLowerCase(), code);
    }
    return available.get(wanted) ?? available.get(wanted.split("-")[0]) ?? fallback;
  }

  function preferredLanguage(offered, stored = null, browser = [], fallback = "en") {
    const remembered = matchLanguage(stored, offered);
    if (remembered) return remembered;
    for (const code of browser ?? []) {
      const matched = matchLanguage(code, offered);
      if (matched) return matched;
    }
    return fallback;
  }

  globalThis.gen3tlLanguages = Object.freeze({ canonicalLanguage, matchLanguage, preferredLanguage });
})();
