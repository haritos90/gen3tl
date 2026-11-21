// The site pages' shared masthead and page bar.
import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { fakeDocument, stubPage } from "./fixture.mjs";
import { prepareHeaders, setLabelSource } from "../js/ui.js";
import { PokedexView } from "../js/pokedex/pokedex.js";

const PAGES = ["pokedex.html", "editor.html", "patch.html", "save.html", "pick.html", "guide.html", "submit.html"];
const page = (name) => readFileSync(new URL(`../${name}`, import.meta.url), "utf8");
const css = readFileSync(new URL("../site.css", import.meta.url), "utf8");
// the contributor guide's translations beside the English source
const TRANSLATED_GUIDES = readdirSync(new URL("..", import.meta.url)).filter((name) => /^guide\.[a-z-]+\.html$/.test(name)).sort();
const SAVE_GUIDES = ["save-guide.html", "save-guide.ru.html"];
const ALL_GUIDES = ["guide.html", ...TRANSLATED_GUIDES, ...SAVE_GUIDES];

function htmlPages(directory, prefix = "") {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const url = new URL(`${entry.name}${entry.isDirectory() ? "/" : ""}`, directory);
    if (entry.isDirectory()) return entry.name === "fragments" ? [] : htmlPages(url, `${prefix}${entry.name}/`);
    return entry.isFile() && entry.name.endsWith(".html") ? [`${prefix}${entry.name}`] : [];
  });
}

const ALL_PAGES = htmlPages(new URL("../", import.meta.url)).sort();

test("every top-level page has static link preview metadata at assembly", () => {
  const root = fileURLToPath(new URL("../../", import.meta.url));
  const localPython = join(root, ".venv/bin/python");
  const python = process.env.PYTHON ?? (existsSync(localPython) ? localPython : "python3");
  const script = `
import json
from gen3tl import TOOLKIT_ROOT
from gen3tl.web.sitepreview import rewrite
web = TOOLKIT_ROOT / "web"
print(json.dumps({path.name: rewrite(path.read_text(encoding="utf-8"), path.name, web,
    "https://site.test/subpath", "share/assets/preview.png") for path in web.glob("*.html")}))
`;
  const result = spawnSync(python, ["-c", script], {
    cwd: root, encoding: "utf8", env: { ...process.env, PYTHONPATH: join(root, "src") },
  });
  assert.equal(result.status, 0, result.stderr || result.error?.message);
  const documents = JSON.parse(result.stdout);
  assert.deepEqual(Object.keys(documents).sort(), ALL_PAGES.filter((name) => !name.includes("/")));
  for (const [name, source] of Object.entries(documents)) {
    const head = source.split("</head>")[0];
    const title = head.match(/<title\b[^>]*>([^<]+)<\/title>/)?.[1];
    assert.ok(title, name);
    for (const key of ["description", "og:type", "og:site_name", "og:title", "og:description", "og:url",
      "og:image", "og:image:type", "og:image:width", "og:image:height", "og:image:alt", "twitter:card",
      "twitter:title", "twitter:description", "twitter:image", "twitter:image:alt"]) {
      assert.equal([...head.matchAll(new RegExp(`<meta (?:name|property)="${key}" content="[^"]+">`, "g"))].length, 1,
        `${name}: ${key}`);
    }
    assert.ok(head.includes(`<meta property="og:title" content="${title}">`), name);
    const destination = name === "index.html" ? "" : name.replace(/\.html$/, "");
    assert.ok(head.includes(`<link rel="canonical" href="https://site.test/subpath/${destination}">`), name);
    assert.match(head, /<meta property="og:image:width" content="1200">/);
    assert.match(head, /<meta property="og:image:height" content="630">/);
    assert.match(head, /<meta property="og:image:type" content="image\/png">/);
    assert.match(head, /<meta name="twitter:card" content="summary_large_image">/);
    assert.equal(source.split("</head>")[1], page(name).split("</head>")[1], name);
  }
});

test("every page has one first-level heading and no skipped heading level", () => {
  for (const name of ALL_PAGES) {
    const levels = [...page(name).matchAll(/<h([1-6])\b[^>]*>/gi)].map((match) => Number(match[1]));
    assert.equal(levels.filter((level) => level === 1).length, 1, name);
    for (let index = 1; index < levels.length; index += 1) {
      assert.ok(levels[index] <= levels[index - 1] + 1,
        `${name}: heading ${index + 1} skips h${levels[index - 1]} to h${levels[index]}`);
    }
  }
  assert.match(page("js/pick.js"), /el\("h2", \{ class: "game-title"/);
});

test("Pokedex headings stay named or hidden through lists, cards, searches and resets", async (context) => {
  const { $ } = stubPage(context, "pokedex");
  context.after(() => setLabelSource(null));
  const headings = [...page("pokedex.html").matchAll(/<h([1-6])\b([^>]*)>([^]*?)<\/h\1>/g)].map(([, level, attrs, text]) => {
    const id = attrs.match(/\bid="([^"]+)"/)?.[1];
    const node = id ? $(id) : document.createElement(`h${level}`);
    node.textContent = text.replace(/<[^>]+>/g, "");
    return node;
  });
  const outline = (state) => {
    const nodes = [...headings, ...$("x-data-content").querySelectorAll("h2")].filter((node) => !node.hidden);
    assert.equal(nodes.filter((node) => node.tagName === "H1").length, 1, state);
    for (const [index, node] of nodes.entries()) {
      assert.ok(node.textContent.trim(), `${state}: empty ${node.tagName} ${node.getAttribute?.("id") ?? ""}`);
      if (index) assert.ok(Number(node.tagName[1]) <= Number(nodes[index - 1].tagName[1]) + 1, state);
    }
  };
  const data = { species: [{ slot: 1, dex: 1, name: "species:1", learnset: [] }],
    trainers: [{ id: 2, name: "trainer_names:2", class: "trainer_classes:1", party: [] }],
    moves: [{ id: 3, name: "moves:3", description: "move_descriptions:3" }] };
  const texts = { "species:1": "Sample species", "trainer_names:2": "Sample trainer",
    "trainer_classes:1": "Sample class", "moves:3": "Sample move" };
  const view = new PokedexView({ data: () => data, load: async () => {}, setHash() {}, spriteSrc: (path) => path,
    entry: async (id) => ({ source_text: texts[id] ?? "", translation: "" }) }, $);
  outline("initial list");
  const loading = view.show();
  outline("loading list");
  await loading;
  outline("loaded list");
  await view.goTo({ kind: "species", id: 1 });
  outline("species card");
  assert.equal($("x-name").hidden, false);
  assert.equal($("x-name").textContent, "#001 Sample species");
  for (const query of ["Sample", "absent", ""]) {
    $("x-search").value = query;
    view.search();
    outline(`species search: ${query}`);
  }
  const trainers = view.goTo({ kind: "trainers", id: 2 });
  outline("loading trainer card");
  await trainers;
  outline("trainer card");
  assert.equal($("x-data-title").hidden, false);
  assert.equal($("x-data-title").textContent, "Sample trainer");
  for (const query of ["Sample", "absent", ""]) {
    $("x-search").value = query;
    view.search();
    outline(`trainer search: ${query}`);
  }
  await view.goTo({ kind: "moves", id: 3 });
  outline("move card");
  assert.ok($("x-data-content").querySelectorAll("h2").length);
  view.texts.set("moves:3", { source_text: " ", translation: "" });
  view.dataView.renderDetail(3);
  outline("unnamed move card");
  view.reset();
  outline("reset list");
  for (const id of ["x-name", "x-data-title"]) {
    assert.equal($(id).hidden, true, id);
    assert.equal($(id).textContent, "", id);
  }
  await view.show(1);
  outline("reopened species card");
  assert.equal($("x-name").hidden, false);
  await view.goTo({ kind: "trainers", id: 2 });
  outline("reopened trainer card");
  assert.equal($("x-data-title").hidden, false);
});

test("static images and canvases have an alternative or are decorative", () => {
  for (const name of ALL_PAGES) {
    for (const match of page(name).matchAll(/<(img|canvas)\b([^>]*)>/gi)) {
      const [, tag, attributes] = match;
      if (tag.toLowerCase() === "img") assert.match(attributes, /\salt="[^"]*"/, name);
      else assert.match(attributes, /\s(?:aria-hidden="true"|role="img"[^>]*aria-label="[^"]+")/, name);
    }
  }
});

test("move tables retain shared columns and visible headings on narrow screens", () => {
  const explorer = page("pokedex-explorer.css");
  assert.match(explorer, /\.c-app \.dex-learn table \{[^}]*width:100%;[^}]*min-width:640px;[^}]*table-layout:fixed;/);
  assert.match(explorer, /\.dex-learn-scroll \{[^}]*max-width:100%;[^}]*overflow-x:auto;/);
  assert.match(explorer, /\.dex-learn-scroll:focus-visible \{[^}]*outline:2px solid/);
  assert.match(explorer, /#x-moves \.dex-learn \.num \{ text-align:right; \}/);
  assert.match(explorer, /\.dex-learn th:first-child \{ width:16%; \}/);
  for (const [column, width] of [[2, 28], [3, 15], [4, 14], [5, 10], [6, 10], [7, 7]]) {
    assert.match(explorer, new RegExp(`\\.dex-learn th:nth-child\\(${column}\\)[^{]*\\{ width:${width}%; \\}`));
  }
  assert.doesNotMatch(explorer, /\.dex-learn (?:thead|tbody|tr|td)\s*\{[^}]*(?:display:(?:grid|block|flex|none)|position:absolute|grid-column|width:)/);
  assert.doesNotMatch(explorer, /data-c-label/);
});

/** Model header children without a layout engine. */
function headerDocument() {
  const doc = fakeDocument(), create = doc.createElement;
  doc.createElement = (tag) => {
    const node = create(tag), attrs = new Map();
    Object.assign(node, {
      setAttribute(key, value) { attrs.set(key, value); },
      getAttribute(key) { return attrs.get(key) ?? null; },
      querySelector(selector) {
        const matches = (child) => selector.startsWith(".") ? child.className.split(" ").includes(selector.slice(1))
          : child.getAttribute(selector.slice(1, -1)) !== null;
        return this.children.find(matches) ?? this.children.map((child) => child.querySelector(selector)).find(Boolean) ?? null;
      },
      remove() {
        if (this.parentNode) this.parentNode.childNodes.splice(this.parentNode.childNodes.indexOf(this), 1);
        this.parentNode = null;
      },
    });
    const append = node.append.bind(node);
    node.append = (...kids) => { kids.forEach((kid) => kid.remove?.()); append(...kids); };
    Object.defineProperty(node, "children", { get: () => node.childNodes.filter((child) => child.nodeType === 1) });
    return node;
  };
  return doc;
}

test("navigation stays centered and the narrow header reserves room for controls", () => {
  assert.match(css, /grid-template-columns: minmax\(12rem, 1fr\) minmax\(0, max-content\) minmax\(12rem, 1fr\)/);
  assert.match(css, /grid-template-columns: minmax\(120px, 1fr\) minmax\(0, max-content\) minmax\(120px, 1fr\)/);
  assert.match(css, /\.tools \{[^}]*justify-self: center/);
  assert.match(css, /\.tools\.compact\.open \.tools-list \{[^}]*left: 50%; transform: translateX\(-50%\)/);
  assert.match(css, /\.lang-menu \.lang-name \{ display: none; \}/);
  const explorer = page("pokedex-explorer.css"), script = page("js/pokedexexplorer.js");
  assert.doesNotMatch(explorer, /grid-template-areas:"home|\.mast \.tools|\.mast \.c-home/);
  assert.doesNotMatch(script, /rail\.append\(brand|class: "brand c-home"|c-mast-title/);
});

test("page trails are removed or hidden before shared initialization", () => {
  for (const name of ["index.html", ...PAGES, ...SAVE_GUIDES].filter((name) => name !== "guide.html")) {
    assert.doesNotMatch(page(name), /class="crumbs"|id="crumb-/);
  }
  assert.match(css, /\.crumbs \{ display: none; \}/);
});

test("fallback navigation keeps a labeled link to the landing page", () => {
  for (const name of PAGES.filter((name) => name !== "guide.html")) {
    assert.match(page(name), /<a href="\.\/" class="brand c-home"[^>]*aria-label="Home"[^>]*>[^]*?<span data-ui="Home"[^>]*>Home<\/span><\/a>/);
  }
});

test("page links use short routes and preserve legacy queries and fragments", () => {
  const doc = headerDocument();
  for (const name of new Set(["index.html", ...PAGES, ...ALL_GUIDES])) {
    const links = [...page(name).matchAll(/<a\b[^>]*href="([^"]+)"/g)].map((match) => {
      const link = doc.createElement("a");
      link.setAttribute("href", match[1]);
      return link;
    });
    const held = name === "guide.html" || TRANSLATED_GUIDES.includes(name);
    if (!held) {
      for (const link of links) assert.doesNotMatch(link.getAttribute("href"), /\.html(?:[?#]|$)/, name);
    }
    prepareHeaders({ baseURI: "https://site.test/sub/guide", querySelectorAll: (s) => s === "a[href]" ? links : [] });
    for (const link of links) assert.doesNotMatch(link.getAttribute("href"), /\.html(?:[?#]|$)/, name);
  }
  const routes = [
    ["index.html?lang=xx#game/xx", "./?lang=xx#game/xx"],
    ["editor.html?mode=edit#game/xx/key", "editor?mode=edit#game/xx/key"],
    ["guide.xx.html#keys", "guide.xx#keys"],
    ["save-guide.xx.html?lang=xx#use", "save-guide.xx?lang=xx#use"],
    ["https://other.test/editor.html#key", "https://other.test/editor.html#key"],
  ];
  const links = routes.map(([href]) => {
    const link = doc.createElement("a");
    link.setAttribute("href", href);
    return link;
  });
  prepareHeaders({ baseURI: "https://site.test/sub/editor.html", querySelectorAll: (s) => s === "a[href]" ? links : [] });
  assert.deepEqual(links.map((link) => link.getAttribute("href")), routes.map(([, target]) => target));
});

test("the brand assets contain bounded self-contained artwork", () => {
  for (const [name, limit] of [["brand.svg", 65536], ["favicon.svg", 8192]]) {
    const svg = readFileSync(new URL(`../${name}`, import.meta.url), "utf8");
    assert.ok(svg.length < limit, `${name} stays within its download budget`);
    assert.ok(/^<svg[^>]*viewBox="0 0 \d+ \d+"/.test(svg), name);
    assert.ok(!/<(?:script|foreignObject)\b|href="https?:/i.test(svg), name);
    const embedded = svg.match(/href="data:image\/png;base64,([A-Za-z0-9+/=]+)"/);
    assert.ok(embedded, `${name} has no external image dependency`);
    const png = Buffer.from(embedded[1], "base64");
    assert.equal(png.subarray(1, 4).toString(), "PNG");
    assert.ok(png.readUInt32BE(16) >= 16 && png.readUInt32BE(20) >= 16);
  }
  assert.match(css, /\.brand \.tile \{[^}]*background: url\("brand\.svg"\) center \/ contain no-repeat;[^}]*box-shadow: none;/);
  const explorer = readFileSync(new URL("../pokedex-explorer.css", import.meta.url), "utf8");
  assert.doesNotMatch(explorer, /#c-rail \.brand/);
  for (const name of ["index.html", ...PAGES, ...TRANSLATED_GUIDES]) {
    assert.match(page(name), /<link rel="icon" type="image\/svg\+xml" href="favicon\.svg">/, name);
  }
});

test("contributor guides preload every font their text can use", () => {
  const expected = [
    "bricolage-grotesque-latin.woff2",
    "ibm-plex-sans-latin.woff2", "ibm-plex-sans-cyrillic.woff2",
    "ibm-plex-mono-400-latin.woff2", "ibm-plex-mono-400-cyrillic.woff2",
    "ibm-plex-mono-500-latin.woff2", "ibm-plex-mono-500-cyrillic.woff2",
  ];
  for (const name of ["guide.html", ...TRANSLATED_GUIDES]) {
    const links = [...page(name).matchAll(/<link rel="preload" href="fonts\/([^"]+)" as="font" type="font\/woff2" crossorigin>/g)]
      .map((match) => match[1]);
    assert.deepEqual(links, expected, name);
  }
});

test("every styled page has marked complete static chrome", () => {
  for (const name of new Set(["index.html", ...PAGES, ...ALL_GUIDES])) {
    const html = page(name);
    assert.equal((html.match(/data-site-mast=/g) ?? []).length, name === "editor.html" ? 2 : 1, name);
    assert.equal((html.match(/data-site-footer=/g) ?? []).length, 1, name);
    for (const className of ["tools-list", "tools-toggle", "lang-menu", "mast-controls", "theme"]) {
      assert.match(html, new RegExp(`class="${className}"`), name);
    }
    assert.equal((html.match(/<!-- site-shell: /g) ?? []).length, name === "editor.html" ? 3 : 2, name);
    assert.equal((html.match(/<!-- \/site-shell: /g) ?? []).length, name === "editor.html" ? 3 : 2, name);
    const footer = html.match(/<footer class="foot"[^>]*>[^]*?<\/footer>/)[0];
    assert.match(footer, /<div class="wrap">/, name);
    if (ALL_GUIDES.includes(name)) {
      assert.deepEqual([...footer.matchAll(/<a href="([^"]+)"/g)].map((m) => m[1]),
        ["license-gen3tl.txt", "fonts/OFL.txt", "https://creativecommons.org/licenses/by-sa/4.0/"], name);
    }
  }
  assert.ok(readFileSync(new URL("../license-gen3tl.txt", import.meta.url), "utf8").startsWith("MIT License"));
});

test("data-driven pages declare their catalog startup deadline", () => {
  for (const name of ["index.html", "pick.html", "patch.html", "save.html"]) {
    assert.match(page(name), /<meta name="site-startup-deadline" content="2500">/, name);
  }
  const css = page("site.css");
  for (const name of ["landing-page", "picker-page", "patcher-page", "save-page"]) {
    assert.match(css, new RegExp(`html\\[data-ui-pending\\] body\\.${name}`), name);
  }
  const theme = page("js/theme.js");
  assert.match(theme, /meta\[name="site-startup-deadline"\]/);
  assert.match(theme, /configured > 0 \? configured : 1500/);
});

test("scripted layout follows the head script in every browser", () => {
  const theme = page("js/theme.js");
  assert.match(theme, /root\.setAttribute\("data-scripted", ""\)/);
  const css = page("site.css");
  assert.doesNotMatch(css, /scripting: enabled/);
  assert.match(css, /html\[data-scripted\] body\.landing-page \.door \{ display: grid; \}/);
  for (const name of ["index.html", "pick.html", "patch.html", "save.html"]) {
    const html = page(name);
    const head = html.slice(0, html.indexOf("</head>"));
    assert.match(head, /<script src="js\/theme\.js"><\/script>/, name);
  }
});

test("static chrome remains reachable without page scripts", () => {
  assert.match(page("editor.html"), /<div id="start" class="start">/);
  assert.match(page("pokedex.html"), /<div id="app" class="app dex">/);
  const css = page("site.css");
  assert.match(css, /@media \(scripting: none\) \{[^]*?\.c-startup \{ display: none !important; \}/);
  assert.match(css, /\.c-app footer\.foot \{ display: block !important; \}/);
});

test("the Pokédex swaps its footer for a menu group of licence links on phones", () => {
  const html = page("pokedex.html"), explorer = page("pokedex-explorer.css");
  assert.match(html, /data-site-menu-licenses=""/);
  assert.equal((html.match(/data-site-footer/g) ?? []).length, 1, "the footer stays for wide screens");
  const phone = explorer.match(/@media \(max-width:760px\) \{([^]*?)\n\}/)[1];
  assert.match(explorer, /\n\.tools-licenses \{ display:none; \}/, "hidden outside phone width");
  assert.match(phone, /\.c-app footer\.foot \{ display:none; \}/, "no footer block on phones");
  assert.match(phone, /\.c-app \.tools-licenses \{ display:contents; \}/, "a row fallback keeps the links reachable");
  assert.match(phone, /\.c-app \.tools\.compact \.tools-licenses \{[^}]*display:grid;[^}]*border-top:1px solid var\(--line-2\)/, "a separated last group in the menu");
  assert.doesNotMatch(explorer, /footer\.foot[^}]*style=/);
});

test("the filters block is one registry-rendered container with the phone stacking rule", () => {
  const html = page("pokedex.html");
  assert.match(html, /<div class="filters dex-filters">\s*<div id="x-favourites" class="dex-favourites" hidden><\/div>\s*<div id="x-filters"><\/div>\s*<\/div>/);
  assert.doesNotMatch(html, /x-advanced|x-species-filters|x-move-advanced|x-trainer-filters|x-filter-clear|id="x-(type-filter|mf-|data-filter|trainer-kind)"/);
  const css = readFileSync(new URL("../site.css", import.meta.url), "utf8");
  assert.match(css, /\.dex-filter-row \{ display: grid; grid-template-columns: 118px minmax\(0, 1fr\);/);
  assert.match(css, /@media \(max-width: 420px\) \{\n  \.dex-filter-row \{ grid-template-columns: minmax\(0, 1fr\);/);
});

test("the editor links to the guide from both top bars and the start screen", () => {
  const html = page("editor.html");
  const masts = [...html.matchAll(/<header class="mast"[^>]*>[^]*?<\/header>/g)].map((m) => m[0]);
  assert.equal(masts.length, 2, "start screen and workspace");
  for (const mast of masts) assert.match(mast, /<a href="guide"[^>]*>Guide<\/a>/);
  const start = html.match(/<div id="start"[^]*?<\/main>/)[0];
  assert.match(start.replace(/<header class="mast"[^>]*>[^]*?<\/header>/, ""), /<a href="guide"[^>]*>contributor guide<\/a>/);
});

test("the guides hold no images or project record ids; their contents resolve", () => {
  for (const name of ALL_GUIDES) {
    const html = page(name);
    assert.doesNotMatch(html, /<(img|canvas|picture|video|object|embed)\b/i, name);
    assert.doesNotMatch(html, /url\(|data:image/i, name);
    assert.doesNotMatch(html, /\b(task|decision|doc|draft)-\d+/i, name);
    const ids = new Set([...html.matchAll(/id="([^"]+)"/g)].map((m) => m[1]));
    const anchors = [...html.matchAll(/href="#([^"]+)"/g)].map((m) => m[1]);
    assert.ok(anchors.length >= (SAVE_GUIDES.includes(name) ? 1 : 10), name);
    assert.deepEqual(anchors.filter((a) => !ids.has(a)), [], name);
  }
});

const decodeGuideText = (text) => text.replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(Number(n)))
  .replace(/&times;/g, "×").replace(/&middot;/g, "·").replace(/&amp;/g, "&").replace(/&quot;/g, '"')
  .replace(/&lt;/g, "<").replace(/&gt;/g, ">");
const guideText = (html) => decodeGuideText(html.replace(/<[^>]+>/g, " ")).replace(/\s+/g, " ").trim();
const guideMain = (html) => html.match(/<main\b[^>]*>([^]*?)<\/main>/)[1];
const guideSections = (html) => [...guideMain(html).matchAll(/<section id="([^"]+)">([^]*?)<\/section>/g)].map((m) => ({
  id: m[1],
  heading: guideText(m[2].match(/<h2[^>]*>([^]*?)<\/h2>/)[1]),
  body: guideText(m[2].replace(/<h2[^>]*>[^]*?<\/h2>/, "")),
}));

test("a translated guide is the English guide's chrome around the same sections in its language", () => {
  assert.ok(TRANSLATED_GUIDES.includes("guide.ru.html"), "the Russian guide");
  const english = page("guide.html");
  const main = (html) => html.slice(html.indexOf("<main"), html.indexOf("</main>"));
  const chrome = (html) => html.replace(main(html), "");
  const shape = (html) => main(html).match(/<[^>]+>/g).map((tag) => tag.replace(/ aria-label="[^"]*"/, "")
    .replace(/href="save-guide\.[a-z-]+\.html"/, 'href="save-guide.html"'));
  for (const name of TRANSLATED_GUIDES) {
    const code = name.split(".")[1];
    const html = page(name);
    assert.match(html, new RegExp(`^<!doctype html>\n<html lang="${code}">`), `${name}: its language`);
    assert.equal(chrome(html).replace(`<html lang="${code}">`, '<html lang="en">')
      .replace(`<a href="${name}" aria-current="page"`, '<a href="guide.html" aria-current="page"'), chrome(english),
    `${name}: the head, masthead and footer of guide.html, its own guide link current`);
    assert.deepEqual(shape(html), shape(english), `${name}: the sections, tables and links of guide.html`);
    assert.match(html, /<script type="module" src="js\/guide.js"><\/script>/, `${name}: the guide follows the site language`);
  }
});

test("a translated guide has translated headings and populated prose in every section", () => {
  const english = new Map(guideSections(page("guide.html")).map((section) => [section.id, section]));
  for (const name of TRANSLATED_GUIDES) {
    const sections = guideSections(page(name));
    assert.deepEqual(sections.map((section) => section.id), [...english.keys()], `${name}: section order`);
    for (const section of sections) {
      const source = english.get(section.id);
      assert.ok(section.heading, `${name}: ${section.id} has a heading`);
      assert.ok(section.body, `${name}: ${section.id} has content`);
      assert.notEqual(section.heading, source.heading, `${name}: ${section.id} heading is translated`);
      assert.notEqual(section.body, source.body, `${name}: ${section.id} content is translated`);
    }
  }
});

test("the Save converter guides have the same six steps and working converter links", () => {
  const english = page("save-guide.html");
  const russian = page("save-guide.ru.html");
  const shape = (html) => guideMain(html).match(/<[^>]+>/g).map((tag) => tag.replace(/ aria-label="[^"]*"/, ""));
  assert.deepEqual(shape(russian), shape(english));
  for (const [name, detected] of [["save-guide.html", /input format is detected automatically/],
    ["save-guide.ru.html", /Формат исходного файла определяется автоматически/]]) {
    const html = page(name);
    assert.match(html, /<body data-guide="save-guide">/);
    assert.deepEqual(guideSections(html).map((section) => section.id), ["use"]);
    assert.equal((guideMain(html).match(/<ol>[^]*?<\/ol>/)[0].match(/<li>/g) ?? []).length, 6, name);
    assert.match(html, /<a href="save">/);
    assert.match(html, detected);
    assert.doesNotMatch(html, /Do I need this|GameShark|Action Replay|checksum|source-format/i);
  }
  assert.match(page("guide.html"), /<a href="save-guide.html">Save converter guide<\/a>/);
  assert.match(page("guide.ru.html"), /<a href="save-guide.ru.html">Руководство по конвертеру сохранений<\/a>/);
});

test("the contributor guides retain current editing, submission and review guidance", () => {
  const required = {
    "guide.html": [
      /key-only/, /Story order/, /File order/, /Pack-only glyphs or keyboard/, /Site-interface texts/,
      /Union Room chat/, /Condition \/ Power/, /Power against this species at its base weight/, /\bTranslated\b/, /\bApproved\b/, /Playtest report/,
    ],
    "guide.ru.html": [
      /только ключи/, /Порядок по сюжету/, /Порядок по файлам/, /Только глифы или клавиатура пакета/,
      /Тексты интерфейса сайта/, /Чат комнаты связи/, /Условие \/ Сила/,
      /Сила против этого вида при его базовом весе/, /Переведено/, /Одобрено/, /Playtest report/, /релиз/,
    ],
  };
  for (const [name, patterns] of Object.entries(required)) {
    const html = page(name);
    for (const pattern of patterns) assert.match(html, pattern, `${name}: ${pattern}`);
    assert.doesNotMatch(html, /id="(?:rom|workspace|entry|pr|prchecks)"|href="#(?:rom|workspace|entry|pr|prchecks)"/);
    assert.doesNotMatch(html, /Choose UPS patch|GameShark|Do I need this|Выбрать UPS-патч/);
  }
  assert.doesNotMatch(page("guide.ru.html"), /выпуск/i, "approved release terminology");
});

test("the tool row is one element: a row where it fits, a menu of the same links where it does not", () => {
  const rule = (selector) => css.match(new RegExp(`\\n${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")} \\{([^}]*)\\}`))?.[1] ?? "";
  assert.match(rule(".tools:not(.compact) .tools-toggle"), /display: none/, "the row hides the toggle");
  assert.match(rule(".tools.compact .tools-list"), /display: none/, "the menu form hides the row");
  assert.match(rule(".tools.compact.open .tools-list"), /position: absolute;[^]*display: grid/, "an open menu drops down");
  assert.match(rule(".tools.compact .tools-list a[aria-current]::before"), /content: "\\2713"/, "the current tool is marked");
  assert.doesNotMatch(css, /overflow-x: auto; scrollbar-width: none; \}\n\.tools::-webkit-scrollbar/, "no sideways scrolling");
});

test("the save tool keeps its title, heading and game name", () => {
  const html = page("save.html");
  assert.match(html, /<title data-ui="Save converter">Save converter<\/title>/);
  assert.match(html, /<h1 class="page-title" data-ui="Save converter">Save converter<\/h1>/);
  assert.match(html, /id="game-name"/);
});

test("one rule sets the masthead's width and gutters: no page overrides it", () => {
  const selectors = [...css.replace(/\/\*[^]*?\*\//g, "").matchAll(/([^{}]+)\{[^{}]*\}/g)]
    .flatMap((m) => m[1].split(",")).map((x) => x.trim()).filter((x) => /\.mast(?:\s|$)/.test(x));
  assert.ok(selectors.includes(".mast"));
  for (const selector of selectors) assert.match(selector, /^\.mast(\s*>\s*\.[a-z-]+)?$/, selector);
  const rule = css.match(/\n\.mast \{([^}]*)\}/)[1];
  assert.match(rule, /grid-template-areas: "brand tools controls"/);
  assert.match(rule, /padding-left: max\(var\(--gutter\)/);
  assert.match(rule, /padding-right: max\(var\(--gutter\)/);
  assert.doesNotMatch(rule, /max-width|margin-inline/, "full width");
  const pagebar = css.match(/\n\.pagebar \{([^}]*)\}/)[1];
  assert.match(pagebar, /max\(var\(--gutter\), env\(safe-area-inset-right, 0px\)\) 7px max\(var\(--gutter\), env\(safe-area-inset-left/,
    "the page bar keeps the masthead's gutters");
});

test("a page bar under the masthead holds the editor's state and actions", () => {
  const app = page("editor.html").match(/<div id="app"[^]*$/)[0];
  const bar = app.match(/<\/header>\s*<!-- \/site-shell: masthead(?:-workspace)? -->\s*<div class="pagebar">([^]*?)\n<\/div>\n<nav class="tabs"/);
  assert.ok(bar, "the page bar follows the workspace masthead");
  const [state, acts] = [bar[1].match(/<div class="state">([^]*?)\n  <\/div>/)[1], bar[1].match(/<div class="acts"[^>]*>([^]*?)\n  <\/div>/)[1]];
  assert.deepEqual([...state.matchAll(/ id="([^"]+)"/g)].map((m) => m[1]),
    ["catalog-language", "rom-state", "check-status", "progress", "progress-label", "progress-more", "progress-bar",
      "progress-pct"]);
  assert.match(state, /<span class="label" id="progress-label" data-ui="Translated entries">Translated entries<\/span>/,
    "the progress names what it measures");
  assert.match(state, /role="meter"[^>]*aria-labelledby="progress-label"/);
  assert.deepEqual([...acts.matchAll(/<button id="([^"]+)"/g)].map((m) => m[1]), ["rom-button", "export", "save-local", "submit"]);
});

test("the Pokédex page bar holds its data language and modes; its own language select is gone", () => {
  const html = page("pokedex.html");
  const bar = html.match(/<\/header>\s*<!-- \/site-shell: masthead(?:-workspace)? -->\s*<div class="pagebar">([^]*?)\n<\/div>\n<div class="notices">/);
  assert.ok(bar, "the page bar follows the masthead");
  for (const id of ["x-search", "data-lang", "data-note", "status", "x-history-back", "x-mode-species", "x-mode-types", "show-list"]) {
    assert.match(bar[1], new RegExp(` id="${id}"`), id);
  }
  assert.deepEqual([...html.matchAll(/<input id="([^"]+)" [^>]*type="search"/g)].map((m) => m[1]), ["x-search", "c-list-search"]);
  assert.doesNotMatch(html, /<select id="language"|dex-lang|id="x-lang"/);
  for (const name of ["index.html", "pick.html", "patch.html", "save.html", "submit.html", "guide.html"]) {
    assert.doesNotMatch(page(name), /class="pagebar"/, `${name} needs no page bar`);
  }
});

test("list views scroll as a document under a compact sticky bar", () => {
  const html = page("pokedex.html"), explorer = page("pokedex-explorer.css");
  assert.match(html, /id="c-list-bar"[^>]*role="region"[^>]*data-ui-label="Filters"/);
  assert.match(html, /id="c-list-toggle"[^>]*aria-expanded="false"[^>]*data-ui="Filters"/);
  assert.match(explorer, /html:has\(body\.c-list:not\(\[data-mode="types"\]\)\) \{ height:auto; overflow:visible;/);
  for (const selector of ["#pokedex.panes.two", "#x-side", "#x-list", ".filters.dex-filters"]) {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    assert.match(explorer, new RegExp(`\\.c-list:not\\(\\[data-mode="types"\\]\\) ${escaped} \\{[^}]*overflow:visible;`));
  }
  assert.match(explorer, /#c-list-bar \{ position:sticky; top:56px;/);
  assert.match(explorer, /#c-list-bar \{ top:52px;/);
  assert.match(explorer, /#c-list-search \{ flex-basis:100%;[^}]*font-size:16px;/);
  assert.match(explorer, /#c-list-clear \{[^}]*position:absolute;[^}]*inset:0 1px 0 auto;/);
  assert.match(explorer, /#c-list-search::-webkit-search-cancel-button \{ display:none; \}/);
  assert.match(html, /<div class="c-list-field">\s*<input id="c-list-search"[^>]*>\s*<button id="c-list-clear" type="button" aria-label="Clear search"[^>]*hidden>/);
  assert.match(explorer, /#c-rail \{ position:fixed;/);
  assert.match(explorer, /#c-rail \.dex-entry-types button \{[^}]*height:64px; padding:4px 1px; font-size:11px; line-height:1\.2;[^}]*overflow-wrap:anywhere;/);
  assert.doesNotMatch(html, /\sstyle=|<style\b/);
});

/** The direct children of the first element opened by `open`. */
function children(html, open) {
  const start = html.indexOf(open);
  assert.ok(start >= 0, `${open} missing`);
  const tags = /<(\/?)([a-z][a-z0-9]*)\b[^>]*>/g;
  tags.lastIndex = start + open.length;
  const found = [];
  let depth = 0;
  for (let m = tags.exec(html); m; m = tags.exec(html)) {
    if (["input", "br", "img", "hr", "meta", "link"].includes(m[2])) continue;
    if (m[1] && depth === 0) return found;
    if (!m[1] && depth === 0) found.push(m[0]);
    depth += m[1] ? -1 : 1;
  }
  assert.fail(`${open} never closes`);
}

test("the species card sets overview, stats and info in one row before the full-width blocks", () => {
  const html = page("pokedex.html");
  const layout = children(html, '<div id="x-species-card"').find((tag) => tag.includes('class="dex-layout"'));
  assert.ok(layout, "the species detail holds the layout");
  const rows = children(html, layout);
  assert.equal(rows[0], '<div class="dex-top">');
  assert.ok(rows.slice(1).every((tag) => /class="block\b/.test(tag)), "the other blocks keep their own rows");
  assert.deepEqual(children(html, '<div class="dex-top">'),
    ['<section class="block dex-overview">', '<section class="block dex-stats">', '<section class="block dex-info">']);
  const block = (open) => html.slice(html.indexOf(open), html.indexOf("</section>", html.indexOf(open)));
  const overview = block('<section class="block dex-overview">');
  assert.match(overview, /id="x-sprite"[^]*id="x-name"[^]*id="x-entry-text"[^]*id="x-types"[^]*id="x-abilities"/);
  for (const id of ["x-category", "x-height", "x-weight"]) assert.doesNotMatch(overview, new RegExp(`id="${id}"`));
  assert.match(block('<section class="block dex-stats">'), /<h2 data-ui="Base stats">[^]*id="x-stats"/);
  const info = block('<section class="block dex-info">');
  assert.match(info, /<h2 data-ui="Training">[^]*id="x-catch-rate"[^]*id="x-catch-open"/);
  const facts = [...info.matchAll(/<div class="dex-info-row"><dt><span class="dex-info-label" data-ui="([^"]+)">\1<\/span><span class="dex-info-leader" aria-hidden="true"><\/span><\/dt><dd id="([^"]+)"><\/dd><\/div>/g)].map((m) => [m[1], m[2]]);
  assert.deepEqual(facts, [
    ["National number", "x-national-number"], ["Category", "x-category"], ["Height (m)", "x-height"], ["Weight (kg)", "x-weight"],
    ["Catch rate", "x-catch-rate"], ["Base experience", "x-base-exp"], ["EV", "x-ev-yield"], ["Growth rate", "x-growth"],
    ["Egg groups", "x-egg-groups"], ["Gender ratio", "x-gender"],
  ]);
});

test("every card shares one header with its category, arrows, number, Back and Close", () => {
  const html = page("pokedex.html");
  const detail = children(html, '<section id="x-detail"');
  assert.equal(detail[0], '<div class="entry-head c-readerbar">');
  assert.equal((html.match(/class="entry-head/g) ?? []).length, 1);
  const header = children(html, detail[0]);
  assert.deepEqual(header.map((tag) => tag.match(/id="([^"]+)"/)[1]), [
    "c-reader-category", "x-prev", "x-next", "x-id", "c-back", "c-close", "x-data-pin", "x-types-pin",
  ]);
  assert.ok(header.every((tag) => !/\btabindex=/.test(tag)));
  for (const [id, label] of [["x-prev", "Previous entry"], ["x-next", "Next entry"]]) {
    assert.match(header.find((tag) => tag.includes(`id="${id}"`)),
      new RegExp(`title="${label}" data-ui-title="${label}" data-ui-site`));
  }
  assert.match(html, /id="c-close"[^>]*data-ui="Close">Close<\/button>/);
  assert.doesNotMatch(html, /id="x-game-id(?:-label)?"/);
});

test("card recents sit below the shared header in one horizontally scrollable row", () => {
  const html = page("pokedex.html"), css = page("pokedex-explorer.css");
  const detail = children(html, '<section id="x-detail"');
  assert.equal(detail[1], '<div id="c-card-recent" hidden>');
  assert.deepEqual(children(html, detail[1]), ['<span data-ui="Recent">',
    '<div id="x-card-recent" class="dex-recent" aria-label="Recent" data-ui-label="Recent" hidden>']);
  assert.match(css, /#c-card-recent \{ display:flex; align-items:center;[^}]*min-width:0;/);
  assert.match(css, /#x-card-recent \{[^}]*flex-wrap:nowrap;[^}]*min-width:0;[^}]*overflow-x:auto; overflow-y:hidden;/);
  assert.match(css, /#x-card-recent \.dex-saved-chip \{ flex:none; height:46px;/);
  assert.match(css, /#x-card-recent \.dex-saved-open \{ min-width:44px; height:44px;/);
  assert.match(css, /\.c-app\[data-mode="types"\] #c-card-recent \{ display:none; \}/);
});

test("the shared card header stays on one line with compact mobile spacing", () => {
  const explorer = page("pokedex-explorer.css");
  const header = explorer.match(/^\.c-app \.c-readerbar \{([^}]+)\}/m)[1];
  assert.match(header, /display:flex;/);
  assert.match(header, /flex-wrap:nowrap;/);
  assert.match(header, /white-space:nowrap;/);
  assert.doesNotMatch(header, /border-bottom/);
  assert.match(explorer, /^\.c-readerbar > \* \{ flex:none; \}/m);
  const mobile = explorer.slice(explorer.indexOf("@media (max-width:760px)"));
  assert.match(mobile, /\.c-readerbar \{ gap:4px;/);
  assert.match(mobile, /\.c-readerbar button \{ min-width:28px;/);
  assert.match(mobile, /\.c-readerbar \.c-close \{ padding-inline:6px;/);
  assert.doesNotMatch(explorer, /\.c-close \{[^}]*margin-left:auto/);
});

test("the species list uses compact columns and wrapping headers on phones", () => {
  const explorer = page("pokedex-explorer.css");
  const mobile = explorer.slice(explorer.indexOf("/* Share column widths"));
  assert.match(mobile, /@media\(max-width:760px\) \{\n \.c-app \{ --c-species-columns:24px minmax\(0,1fr\) minmax\(48px,\.55fr\) 32px; \}/);
  assert.match(mobile, /\.c-app #c-species-header,\.c-app #pokedex \.c-species-row \{ gap:4px; padding-inline:8px; \}/);
  assert.match(mobile, /#c-species-header button \{ white-space:normal; overflow-wrap:anywhere; \}/);
  assert.match(explorer, /^\.c-app \{ --c-species-columns:42px minmax\(150px,1\.4fr\)/m);
});

test("the species list sorts through its header buttons only", () => {
  const css = page("pokedex-explorer.css");
  const script = readFileSync(new URL("../js/pokedexexplorer.js", import.meta.url), "utf8");
  assert.doesNotMatch(css, /c-species-sort/);
  assert.doesNotMatch(script, /c-species-sort/);
  assert.match(script, /"data-sort": key[\s\S]*on: \{ click: \(\) => changeSort\(key, active\) \}/);
  assert.match(script, /\$\("x-list"\)\.before\(sortHeader\)/);
});

test("the species info rows keep the label, leader and value on one flex line", () => {
  const css = readFileSync(new URL("../site.css", import.meta.url), "utf8");
  assert.match(css, /^\.dex-info-row \{ display: flex; align-items: center; min-width: 0;/m);
  assert.match(css, /^\.dex-info-row dt \{ display: flex; flex: 1;[^}]*min-width: 0;/m);
  assert.match(css, /^\.dex-info-leader \{ flex: 1; min-width: 12px;/m);
  assert.match(css, /^\.dex-info-row dd \{ flex-shrink: 0; min-width: 0;/m);
});

test("location item links keep icons beside names and size wide columns to content", () => {
  assert.match(css, /^\.dex-location-data \.dex-item-link \{ display: inline-grid; grid-template-columns: auto minmax\(0, 1fr\); \}/m);
  assert.match(css, /^\.dex-location-items \.dex-item-icon \{ max-width: none; \}/m);
  const wide = css.match(/@media \(min-width: 761px\) \{\n(  \.dex-location-items \.dex-location-data[^]*?)\n\}/)[1];
  assert.match(wide, /table-layout: auto;/);
  assert.match(wide, /th:nth-child\(1\) \{ width: auto; \}/);
  assert.match(wide, /\.dex-location-items \.dex-item-link \{ grid-template-columns: auto max-content; white-space: nowrap; \}/);
});

test("effectiveness uses dataset columns and container widths without wrapping labels", () => {
  const explorer = page("pokedex-explorer.css");
  assert.match(explorer, /#x-defense \{ container:dex-effectiveness \/ inline-size; \}/);
  assert.match(explorer, /\.dex-eff-grid \{ display:grid; grid-template-columns:repeat\(var\(--dex-eff-small\),minmax\(0,1fr\)\); gap:4px 2px;/);
  for (const [width, property] of [[28, "medium"], [55, "count"]]) {
    assert.match(explorer, new RegExp(`@container dex-effectiveness \\(min-width:${width}rem\\) \\{\\s*\\.dex-eff-grid \\{ grid-template-columns:repeat\\(var\\(--dex-eff-${property}\\),minmax\\(0,1fr\\)\\); \\}`));
  }
  const chip = explorer.match(/^\.dex-eff-cell \.chip \{([^}]+)\}/m)[1];
  assert.match(chip, /padding:2px;/);
  assert.match(chip, /font-size:11px;/);
  assert.match(chip, /white-space:nowrap;/);
  assert.doesNotMatch(chip, /overflow:hidden|overflow-wrap:anywhere|text-overflow/);
  const renderer = page("js/pokedex/view-types.js").split("effectivenessBlocks(record) {")[1].split("\n  renderTypes()")[0];
  assert.doesNotMatch(renderer, /\b18\b|style:/);
  assert.doesNotMatch(explorer, /\.dex-eff-grid \{[^}]*repeat\((?:auto-fit|\d)/);
});

test("the species row takes three columns when wide and stacks when narrow", () => {
  const css = readFileSync(new URL("../site.css", import.meta.url), "utf8");
  const rule = (media) => css.match(new RegExp(`@media \\(${media}\\) \\{\\n([^]*?)\\n\\}`))?.[1] ?? "";
  assert.match(rule("min-width: 1440px"), /\.dex-top \{ grid-template-columns: repeat\(3, minmax\(0, 1fr\)\); align-items: stretch; \}/);
  assert.match(rule("max-width: 900px"), /\.dex-top \{ grid-template-columns: minmax\(0, 1fr\); \}/);
  assert.match(css, /^\.dex-top \{ display: grid; grid-template-columns: repeat\(2, minmax\(0, 1fr\)\);/m);
});

test("evolution forks keep grid cells wide and group branches on phones", () => {
  const css = readFileSync(new URL("../site.css", import.meta.url), "utf8");
  assert.match(css, /^\.dex-evo-fork, \.dex-evo-branch \{ display: contents; \}/m);
  const narrow = [...css.matchAll(/@media \(max-width: 760px\) \{\n([^]*?)\n\}/g)]
    .map((match) => match[1]).find((body) => body.includes(".dex-evo-fork.split")) ?? "";
  assert.match(narrow, /\.dex-grid \.dex-evo-fork\.split \{ display: flex; flex-wrap: wrap; justify-content: center; align-items: flex-start; gap: 6px; width: 100%; container-type: inline-size; \}/);
  assert.match(narrow, /> \.dex-evo-branch \{ --per-row: 2; [^}]*flex: 1 1 calc\(\(100% - \(var\(--per-row\) - 1\) \* 6px\) \/ var\(--per-row\) - 1px\); min-width: 0; max-width: calc\(\(100% - \(var\(--per-row\) - 1\) \* 6px\) \/ var\(--per-row\)\);/);
  assert.match(narrow, /@container \(min-width: 450px\) \{ \.dex-grid \.dex-evo-fork\.split:not\(\[data-branches="2"\]\) > \.dex-evo-branch \{ --per-row: 3; \} \}/);
  assert.match(narrow, /@container \(min-width: 620px\) \{ \.dex-grid \.dex-evo-fork\.split:not\(\[data-branches="2"\]\):not\(\[data-branches="3"\]\) > \.dex-evo-branch \{ --per-row: 4; \} \}/);
  assert.doesNotMatch(narrow, /96px/);
  assert.match(narrow, /\.dex-grid \.dex-evo-fork\.split > \.dex-evo-branch \{ --per-row: 2; display: flex; flex-direction: column; align-items: center; gap: 6px;/);
  assert.match(narrow, /\.dex-grid \.dex-evo-fork\.split \.dex-evo-meta:has\(\.dex-ability-box\) \{ min-width: 0; \}/);
});

test("the species card shows its three panels from 1240 px and the mid layout below", () => {
  const explorer = readFileSync(new URL("../pokedex-explorer.css", import.meta.url), "utf8");
  const rule = (media) => explorer.match(new RegExp(`@media \\(${media}\\) \\{\\n([^]*?)\\n\\}`))?.[1] ?? "";
  const three = rule("min-width:1240px");
  assert.match(three, /\.c-app \.dex-top \{ grid-template-columns:minmax\(0,1\.6fr\) minmax\(270px,\.8fr\) minmax\(270px,\.8fr\); align-items:stretch; \}/);
  assert.match(three, /\.c-app \.dex-top > \.dex-overview \{ grid-column:auto; \}/);
  assert.match(three, /\.c-app \.dex-overview \{ grid-template-columns:112px minmax\(0,1fr\); \}/);
  assert.match(rule("min-width:1100px"), /\.c-app \.dex-overview \{ grid-template-columns:112px minmax\(180px,\.7fr\) minmax\(300px,1\.3fr\); \}/);
  assert.match(explorer, /@media \(min-width:761px\) and \(max-width:900px\) \{\n \.c-app \.dex-top \{ grid-template-columns:minmax\(0,1fr\); \}\n\}/);
  const vertical = explorer.match(/@media \(min-width:761px\) and \(max-width:1100px\) \{\n([^]*?)\n\}/)?.[1] ?? "";
  assert.match(vertical, /\.c-app \.dex-grid \{ display:flex; flex-direction:column; align-items:center; width:auto; gap:6px; \}/);
  assert.match(vertical, /\.c-app \.dex-grid \.dex-evo-fork\.split \{ display:flex; flex-wrap:wrap; justify-content:center; align-items:flex-start; gap:6px; width:100%; container-type:inline-size; \}/);
  assert.match(vertical, /> \.dex-evo-branch \{ --per-row:2; [^}]*flex:1 1 calc\(\(100% - \(var\(--per-row\) - 1\) \* 6px\) \/ var\(--per-row\) - 1px\); min-width:0; max-width:calc\(\(100% - \(var\(--per-row\) - 1\) \* 6px\) \/ var\(--per-row\)\);/);
  assert.match(vertical, /@container \(min-width:450px\) \{ \.c-app \.dex-grid \.dex-evo-fork\.split:not\(\[data-branches="2"\]\) > \.dex-evo-branch \{ --per-row:3; \} \}/);
  assert.match(vertical, /@container \(min-width:620px\) \{ \.c-app \.dex-grid \.dex-evo-fork\.split:not\(\[data-branches="2"\]\):not\(\[data-branches="3"\]\) > \.dex-evo-branch \{ --per-row:4; \} \}/);
  assert.doesNotMatch(vertical, /96px/);
  assert.match(vertical, /\.c-app \.dex-grid \.dex-evo-fork\.split > \.dex-evo-branch \{ --per-row:2; display:flex; flex-direction:column; align-items:center; gap:6px;/);
  assert.ok(explorer.indexOf("@media (min-width:1100px)") < explorer.indexOf("@media (min-width:1240px)"));
  assert.doesNotMatch(rule("min-width:1600px"), /dex-top|dex-overview|dex-grid/);
  assert.equal(explorer.match(/\.c-app \.dex-top \{ grid-template-columns:minmax\(0,1\.6fr\)/g).length, 1);
});

test("the Pokédex panes take the window width; its text columns keep a reading measure", () => {
  const css = readFileSync(new URL("../site.css", import.meta.url), "utf8");
  const capped = [...css.replace(/\/\*[^]*?\*\//g, "").matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .filter((m) => /max-width/.test(m[2])).flatMap((m) => m[1].split(",")).map((x) => x.trim())
    .filter((x) => /^(#pokedex|\.panes)(\.[a-z-]+)*$/.test(x));
  assert.deepEqual(capped, []);
  assert.match(css, /^\.dex-entry-text, \.dex-ability-description,[^{]*\{ max-width: 60ch; \}/m);
});

test("Pokédex cards fill their pane and the single-type overview is independent", () => {
  const rules = [...css.replace(/\/\*[^]*?\*\//g, "").matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .map((m) => ({ selectors: m[1].split(",").map((x) => x.trim()), body: m[2] }));
  const container = /(^|\s)(\.detail( > \*)?|\.dex-layout( > \.block)?|\.dex-data-detail|\.dex-data-card|#x-data-content|#x-[a-z]+-detail( > \*)?|#x-chart-card)$/;
  const bounded = rules.filter((r) => /max-width: \d|margin-inline: auto/.test(r.body))
    .flatMap((r) => r.selectors).filter((selector) => container.test(selector));
  assert.deepEqual(bounded, [], "no card or chart container is bounded or centred");
  const measure = rules.filter((r) => /max-width: 60ch/.test(r.body)).flatMap((r) => r.selectors);
  for (const text of [".dex-entry-text", ".dex-ability-description", ".dex-data-about p", "#x-chart-card .block > p"]) {
    assert.ok(measure.includes(text), `${text} keeps 60ch`);
  }
  const html = page("pokedex.html");
  const chart = html.slice(html.indexOf('<div id="x-chart-card"'));
  assert.deepEqual(children(chart, '<div class="dex-layout">'), ['<section class="block">']);
  assert.match(chart.split('<section class="block">')[1], /^\s*<h2 [^>]*>[^<]*<\/h2>\s*<p class="muted small"/);
  const overview = html.slice(html.indexOf('<section id="c-type-overview"'), html.indexOf('<section id="x-side"'));
  assert.match(overview, /id="x-type-chart"/);
  assert.doesNotMatch(chart, /id="x-type-chart"/);
  assert.match(html, /id="x-mode-types"[^>]*data-ui="Type chart" data-ui-site>Type chart<\/button>/);
  assert.match(css, /^#x-chart-card \.dex-layout \{ display: flex; flex-wrap: wrap; \}/m);
  assert.match(css, /^#x-chart-card \.dex-layout > \.block \{ flex: 1 1 auto; min-width: 0; \}/m);
  assert.match(css, /^\.dex-dual-pick \{[^}]*contain: inline-size;/m);
});

test("every mode shares one side pane, list and detail pane", () => {
  const html = page("pokedex.html");
  assert.equal((html.match(/class="pane side"/g) ?? []).length, 1);
  assert.equal((html.match(/class="pane main detail"/g) ?? []).length, 1);
  assert.equal((html.match(/role="listbox"/g) ?? []).length, 1);
  assert.doesNotMatch(html, /id="x-(species-side|species-detail|data-side|data-detail|data-list|data-space|chart)"/);
});

test("the search clear button stays inside a fixed field wrapper at every width", () => {
  const html = page("pokedex.html"), explorer = page("pokedex-explorer.css");
  const wrapper = html.match(/<div class="c-search">([^]*?)<\/div>/)[1];
  assert.match(wrapper, /<input id="x-search"/);
  assert.match(wrapper, /<button id="x-search-clear" type="button"[^>]*aria-label="Clear search"[^>]*data-ui-label="Clear search"[^>]*data-ui-site hidden>/);
  assert.match(wrapper, /<span aria-hidden="true">×<\/span>/);
  assert.doesNotMatch(wrapper, /tabindex=|style=/);
  assert.match(explorer, /\.c-search \{[^}]*position:relative;[^}]*display:flex;[^}]*grid-column:2;[^}]*min-width:0;/);
  assert.match(explorer, /#x-search \{[^}]*padding:10px 48px 10px 16px;/);
  assert.match(explorer, /#x-search-clear \{[^}]*position:absolute;[^}]*inset:0 1px 0 auto;[^}]*width:44px;/);
  assert.match(explorer, /#x-search-clear:focus-visible \{[^}]*outline:2px solid/);
  assert.match(explorer, /#x-search::-webkit-search-cancel-button \{ display:none; \}/);
  assert.match(explorer, /\.c-app:not\(\[data-mode="types"\]\) \.c-search \{ display:none; \}/);
  assert.match(explorer, /#c-list-bar\[hidden\] \{ display:none; \}/);
  const mobile = explorer.slice(explorer.indexOf("@media (max-width:760px)"));
  assert.match(mobile, /\.c-search \{ grid-column:1; grid-row:2; \}/);
  assert.match(mobile, /#x-search \{ height:44px;/);
});

test("all modes share search vocabulary and one registry per mode", () => {
  const html = page("pokedex.html");
  assert.match(html, /id="x-search"[^>]*data-ui-placeholder="Search"[^>]*aria-label="Search"/);
  assert.equal((html.match(/id="x-filters"/g) ?? []).length, 1);
  assert.doesNotMatch(html, /x-mf-clear|Search Pokedex entries|Search name, dex|Search entries, ids|Filter by type, values|x-filter-clear/);
  const js = readFileSync(new URL("../js/filters.js", import.meta.url), "utf8");
  for (const key of ["type", "type2", "ability", "move", "moveType", "stat", "weight", "height", "eggGroup", "evolution"]) {
    assert.match(js, new RegExp(`key: "${key}"`), key);
  }
});

test("the explorer start-up calls no retired table labeller", () => {
  const script = readFileSync(new URL("../js/pokedexexplorer.js", import.meta.url), "utf8");
  assert.doesNotMatch(script, /\blabelTables\b/);
});

test("location pages show no remark about how the data was verified", () => {
  const catalog = readFileSync(new URL("../i18n/ru.po", import.meta.url), "utf8");
  const script = readFileSync(new URL("../js/pokedexdataview/location-view.js", import.meta.url), "utf8");
  for (const text of ["Descriptions are supplied guidance; map positions are verified.",
    "Map connections only; other exits are not verified."]) {
    assert.ok(!script.includes(text));
    assert.ok(!catalog.includes(text));
  }
});

test("the learner grid keeps a condition beside its name and divides the columns", () => {
  assert.match(css, /\.dex-learner-grid \{[^}]*gap: 0;[^}]*clip-path: inset\(0 0 0 1px\);/);
  assert.match(css, /\.dex-learner-grid \.dex-learner \{[^}]*border-left: 1px solid var\(--line\);/);
  assert.match(css, /\.dex-learner \.dex-sub \{ flex: none;/);
  assert.doesNotMatch(css, /\.dex-learner \.dex-sub \{[^}]*margin-left: auto/);
});
