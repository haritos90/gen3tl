// Draft checks make no rendering claims.
// Their messages are keys, shown in the site language.

import { N_ } from "./ui.js";

export const isDraft = (index) => index?.capabilities?.check === "tokens";

/** Blank text alone cannot identify intentional empties. */
export function translationState(entry) {
  return entry.state ?? (entry.translation ? "text" : entry.flags?.includes("empty") ? "empty" : "original");
}

/** Explicit states keep blank edits unambiguous. */
export function draftValue(entry, translation, state = null) {
  state ??= translation ? "text" : translationState(entry) === "empty" ? "empty" : "original";
  if (typeof translation !== "string" || !["original", "empty", "text"].includes(state)
    || (state === "text") !== (translation !== "")) {
    throw new TypeError("invalid translation state");
  }
  return { translation, state };
}

/** Validated source tokens, or unavailable evidence. */
export function sourceTokens(entry) {
  if (typeof entry.source_text === "string") return entry.source_text.match(/\{[^{}]*\}/g) ?? [];
  return Array.isArray(entry.tokens) && entry.tokens.every((value) =>
    typeof value === "string" && /^\{[^{}]*\}$/.exec(value)?.[0] === value) ? entry.tokens : null;
}

/** The placeholder check of a draft catalog. */
export function draftCheck(entry, text, { game = true } = {}) {
  const tokens = (values) => {
    const counts = new Map();
    for (const token of values) counts.set(token, (counts.get(token) ?? 0) + 1);
    return counts;
  };
  const known = sourceTokens(entry);
  const source = tokens(known ?? []), target = tokens(text.match(/\{[^{}]*\}/g) ?? []);
  const problems = [];
  if (known === null) {
    problems.push({ severity: "error", message: N_("Cannot check source placeholders and control codes. Regenerate the editor data.") });
  } else if (text && [...new Set([...source.keys(), ...target.keys()])].some((t) => source.get(t) !== target.get(t))) {
    problems.push({ severity: "error", message: N_("Preserve the source placeholders and control codes.") });
  }
  if (/[{}]/.test(text.replace(/\{[^{}]*\}/g, ""))) {
    problems.push({ severity: "error", message: N_("Unclosed or unmatched control-code braces.") });
  }
  if (/[\x00-\x08\x0B\x0C\x0E-\x1F]/.test(text)) {
    problems.push({ severity: "error", message: N_("Unexpected control character in draft text.") });
  }
  if (game) problems.push({ severity: "warning", message: N_("Game encoding, font and layout are not verified for this catalog.") });
  const errors = problems.filter((p) => p.severity === "error").length;
  return { id: entry.id, text, laid: text, problems, exact: false, draft: true,
    errors, warnings: problems.length - errors,
    lines: text.split("\\p").map((p) => p.split(/\\[nl]/).map((line) => ({ text: line, width: null }))) };
}

export const draftText = (text) => text.replace(/\\[nl]/g, "\n").replaceAll("\\p", "\n\n");
