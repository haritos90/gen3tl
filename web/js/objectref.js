import { KEY_FORMS } from "./data.js";
import { siteRoute } from "./registry.js";

export const OBJECT_KINDS = Object.freeze([
  "text", "term", "family", "picture", "keyboard-page", "chat-row", "font", "glyph", "engine-rule",
]);

const PREFIX = "gen3tl:";
const COMPONENT = /^(?:[A-Za-z0-9_.!~*'():-]|%[0-9A-Fa-f]{2})+$/;
const TEXT_TARGET = /^(?:[A-Za-z0-9_.!~*'():/-]|%[0-9A-Fa-f]{2})+$/;
const BARE_KEY = new RegExp(`^(?:${KEY_FORMS}|site:[\\s\\S]+|s[0-9A-F]{7}|[a-z][a-z0-9_]*:\\d+)$`);
const GLYPH_CODE = /^[0-9A-F]{2,3}$/;

export class ObjectReferenceError extends Error {
  constructor(code, params = {}) {
    super(code);
    this.name = "ObjectReferenceError";
    this.code = code;
    this.params = params;
  }
}

function invalid() {
  throw new ObjectReferenceError("invalid-reference");
}

function encode(value) {
  if (typeof value !== "string" || !value.length) invalid();
  try {
    return encodeURIComponent(value).replace(/%3A/g, ":");
  } catch {
    invalid();
  }
}

function decode(value, text = false) {
  if (!(text ? TEXT_TARGET : COMPONENT).test(value)) invalid();
  try {
    return decodeURIComponent(value);
  } catch {
    invalid();
  }
}

function checked(reference, registry) {
  if (!reference || !OBJECT_KINDS.includes(reference.kind) || !Array.isArray(registry?.games)) invalid();
  const { game, language, kind } = reference;
  encode(game);
  encode(language);
  const project = registry.games.find((item) => item.id === game);
  if (!project) throw new ObjectReferenceError("unknown-game", { game });
  if (!project.languages.includes(language)) throw new ObjectReferenceError("catalog-unavailable", { language });
  if (kind === "glyph") {
    encode(reference.sheet);
    if (typeof reference.code !== "string" || !GLYPH_CODE.test(reference.code)) invalid();
    return { game, language, kind, sheet: reference.sheet, code: reference.code };
  }
  encode(reference.id);
  return { game, language, kind, id: reference.id };
}

export function parseReference(value, registry, workspace = null) {
  if (typeof value !== "string" || !value.length) invalid();
  if (!value.startsWith(PREFIX)) {
    if (!BARE_KEY.test(value)) invalid();
    if (!workspace?.game || !workspace?.language) throw new ObjectReferenceError("workspace-required");
    return checked({ game: workspace.game, language: workspace.language, kind: "text", id: value }, registry);
  }
  // Encoded separators belong to identities.
  const [game, language, kind, ...target] = value.slice(PREFIX.length).split("/");
  if (!OBJECT_KINDS.includes(kind) || !target.length) invalid();
  const reference = { game: decode(game), language: decode(language), kind };
  if (kind === "text") reference.id = decode(target.join("/"), true);
  else if (kind === "glyph") {
    if (target.length !== 2) invalid();
    reference.sheet = decode(target[0]);
    reference.code = decode(target[1]);
  } else {
    if (target.length !== 1) invalid();
    reference.id = decode(target[0]);
  }
  return checked(reference, registry);
}

export function formatReference(reference, registry) {
  const ref = checked(reference, registry);
  const head = `${PREFIX}${encode(ref.game)}/${encode(ref.language)}/${ref.kind}/`;
  if (ref.kind === "glyph") return `${head}${encode(ref.sheet)}/${ref.code}`;
  const id = encode(ref.id);
  return head + (ref.kind === "text" ? id.replace(/%2F/g, "/") : id);
}

export function parseLookup(value, registry, workspace = null) {
  if (typeof value !== "string" || !Array.isArray(registry?.games)) invalid();
  value = value.trim();
  if (value.startsWith(PREFIX) || BARE_KEY.test(value)) return parseReference(value, registry, workspace);
  let hash = value;
  if (!value.startsWith("#")) {
    let url;
    try { url = new URL(value, "https://editor.invalid/"); } catch { invalid(); }
    if (!["https:", "http:"].includes(url.protocol) || !/\/editor(?:\.html)?$/.test(url.pathname)) invalid();
    hash = url.hash;
  }
  if (!hash || /(?:^|\/)\//.test(hash.slice(1))) invalid();
  let first;
  try { [first] = hash.slice(1).split("/").map(decodeURIComponent); } catch { invalid(); }
  const route = siteRoute(hash, registry);
  if (!route.game) {
    if (first) throw new ObjectReferenceError("unknown-game", { game: first });
    invalid();
  }
  const { game, language, tail } = route;
  if (tail[0] === "fonts" && tail[2] === "glyph") {
    if (tail.length !== 4) invalid();
    return checked({ game, language, kind: "glyph", sheet: tail[1], code: tail[3] }, registry);
  }
  if (tail[0] === "keyboards" && ["page", "chat"].includes(tail[1])) {
    if (tail.length !== 3) invalid();
    const kind = tail[1] === "page" ? "keyboard-page" : "chat-row";
    return checked({ game, language, kind, id: tail[2] }, registry);
  }
  const kinds = { terms: "term", families: "family", pictures: "picture", keyboards: "keyboard-page", fonts: "font",
    enginerules: "engine-rule" };
  const kind = kinds[tail[0]] ?? "text";
  if (!tail.length || (kind !== "text" && tail.length !== 2)) invalid();
  const id = kind === "text" ? tail.join("/") : tail[1];
  const reference = checked({ game, language, kind, id }, registry);
  if (kind === "keyboard-page") {
    if (!/^\d+$/.test(id) || !Number.isSafeInteger(Number(id))) invalid();
    return { ...reference, index: Number(id) };
  }
  return reference;
}
