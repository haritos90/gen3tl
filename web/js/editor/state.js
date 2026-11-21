import { browserStorage } from "../store.js";
import { N_ } from "../ui.js";
import { Trail } from "../data.js";
import { JoinedCache } from "../editor-cache.js";
import { EditorRuntime } from "../editor-runtime.js";
import { LocalSave } from "../local.js";




export const $ = (id) => document.getElementById(id);



export const local = new LocalSave();



export const runtime = new EditorRuntime();



export const joinedCache = new JoinedCache();




export const state = {
  storage: browserStorage(),
  registry: null,
  sources: new Map(),
  games: [],
  base: null,
  manifest: null,
  profiles: [],
  profile: null,
  generation: 0,
  romGeneration: 0,
  romLoading: false,
  romProgress: "Verifying the ROM and any patches…",
  romPatch: null,
  configureGeneration: 0,
  checkRequest: 0,
  aliases: null, // the keys of older inventory ids, loaded on demand
  lang: null,
  index: null,
  rows: [],
  byId: new Map(),
  groups: [],
  chunks: null,
  codec: null,
  glyphs: {},
  edits: null,
  settings: null,
  reference: null,
  referenceIndexes: new Map(),
  referenceGeneration: 0,
  filtered: [],
  cur: -1,
  entry: null,
  result: null,
  rom: null, // ROM bytes remain local to this browser.
  textRom: null, // Text extraction uses verified original bytes.
  english: null, // {texts, missing}: the English of the loaded ROM
  ownEnglish: false, // The data ships its English (site, drafts)
  check: null,
  checkStarted: false,
  checkState: "loading",
  pythonState: "loading",
  zoom: 2,
  view: "texts",
  gfx: null,
  gfxState: "loading",
  uploads: null,
  termEdits: null,
  noteEdits: null,
  glyphEdits: null,
  keyboardEdits: null,
  termsData: null,
  pokedex: null,
  pick: { game: null, language: null },
  startReplacing: false,
  checkDetail: "",
  empty: null, // Keep empty-workspace messages translatable after language changes.
  titleLanguage: null,
  notice: null, // Deferred banner messages follow the current language.
  loadError: null,
  objectReferences: new Map(),
  lookupGeneration: 0,
  lookupLoading: false,
};




export const SITE = "site";



export const VIEWS = ["texts", "pictures", "terms", "families", "keyboards", "fonts", "enginerules"];



export const VIEW_NAMES = { texts: N_("Texts"), pictures: N_("Pictures"), terms: N_("Terms"), families: N_("Families"),
  keyboards: N_("Keyboards"), fonts: N_("Fonts"), enginerules: N_("Engine language rules") };



export const trail = new Trail(history, VIEWS);



export const isSite = () => state.profile?.id === SITE;




export const DATA = () => state.base;




export const DOTS = { approved: "approved", fuzzy: "fuzzy", untranslated: "untranslated", nothing: "nothing" };



export const TRANSLATION_STATES = { original: N_("Inherited original"), empty: N_("Off"), text: N_("Custom") };



export const REFERENCE_VIEWS = { text: "texts", term: "terms", family: "families", picture: "pictures",
  "keyboard-page": "keyboards", font: "fonts", "engine-rule": "enginerules" };




export function setObjectReference(kind, id) {
  state.objectReferences.set(kind, id);
  $(`copy-${kind}`).disabled = id === null;
}




// Preserve storage keys so saved drafts remain accessible.
export function remember(key, value) {
  try {
    state.storage?.setItem(`gen3tl-editor:${key}`, value);
  } catch {
    // Unavailable storage leaves drafts in memory only.
  }
}




export function recall(key) {
  try {
    return state.storage?.getItem(`gen3tl-editor:${key}`) ?? null;
  } catch {
    return null;
  }
}
