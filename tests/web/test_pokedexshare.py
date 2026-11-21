import contextlib
import hashlib
import html
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from urllib.parse import urljoin, urlsplit

from test_roots import TOOLKIT_ROOT
from gen3tl import __version__
from gen3tl.cli.web import site_assemble
from gen3tl.web.pokedexshare import SITE_CSS, STAT_LABELS, STAT_MAX, STAT_ORDER, TIER_LABELS, ShareGenerator, png_bytes, png_pixels, stat_ranks, tier_colours, tier_of

ROOT = TOOLKIT_ROOT

NODE = shutil.which("node")

PAGE_TIERS = '''
import fs from "node:fs";
import { STAT_LABELS, STAT_ORDER, TIER_LABELS, statRanks, tierOf } from "%s";
const records = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const ranks = statRanks(records);
const tiers = records.map((record) => [...STAT_ORDER, "total"].map((key) => tierOf(ranks[key],
  key === "total" ? STAT_ORDER.reduce((sum, k) => sum + record.base_stats[k], 0) : record.base_stats[key])?.tier ?? null));
fs.writeFileSync(process.argv[3], JSON.stringify({ order: STAT_ORDER, labels: STAT_LABELS, tiers: TIER_LABELS, ranks, values: tiers }));
'''

PAGE_NAMES = '''
import fs from "node:fs";
import { displayName, formLabel, megaLetter, PokedexView } from "%s/pokedex/pokedex.js";
import { textEntry } from "%s/dexpage.js";
import { setCatalog, setLabelSource, setUiLanguage } from "%s/ui.js";
const { dataset, catalogs } = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const result = {};
for (const [language, doc] of Object.entries(catalogs)) {
  const view = Object.create(PokedexView.prototype);
  view.lang = language;
  view.texts = new Map(Object.keys(doc.texts).map((key) => [key, textEntry(doc, key)]));
  view.host = { data: () => dataset, pageLabels: () => doc.page };
  setUiLanguage(language);
  setCatalog(doc.site);
  setLabelSource((key) => view.label(key));
  const text = (key) => view.text(key);
  result[language] = dataset.species.map((record) => ({
    name: displayName(record, dataset.species, text),
    form: formLabel(record, dataset.species, text),
    mega: megaLetter(record, dataset.species, text),
    types: [...new Set(record.types)].map((id) => text(`types:${id}`)),
  }));
}
fs.writeFileSync(process.argv[3], JSON.stringify(result));
'''

def page_names(generator, root):
    script, source, out = root / "names.mjs", root / "names-in.json", root / "names-out.json"
    script.write_text(PAGE_NAMES % (((ROOT / "web/js").as_uri(),) * 3), encoding="utf-8")
    catalogs = {code: {"texts": texts, "page": generator.pages[code], "site": generator.site[code]}
                for code, texts in generator.texts.items()}
    source.write_text(json.dumps({"dataset": generator.dataset, "catalogs": catalogs}), encoding="utf-8")
    run = subprocess.run([NODE, str(script), str(source), str(out)], capture_output=True, text=True)
    if run.returncode:
        raise AssertionError(run.stderr)
    return json.loads(out.read_text(encoding="utf-8"))

class ShareGeneratorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / "data"
        sprite = self.data / "pokedex/game/sprites/sprites/978-front.png"
        sprite.parent.mkdir(parents=True)
        sprite.write_bytes(png_bytes(2, 2, bytearray((20, 40, 60, 255, 0, 0, 0, 0) * 2)))
        dataset = {
            "format": "gen3tl-pokedex", "schema": 1, "profile": {"id": "game"}, "count": 1,
            "species": [{"slot": 978, "dex": 761, "name": "species:978", "types": [12], "evolution": [],
                         "base_stats": {"hp": 42, "attack": 30, "defense": 38, "sp_attack": 30, "sp_defense": 38,
                                        "speed": 32},
                         "sprite": {"front": "sprites/978-front.png"}}],
            "types": [{"id": 12, "colour": "#78C850"}],
        }
        (self.data / "pokedex/game/pokedex.json").write_text(json.dumps(dataset), encoding="utf-8")
        manifest = {
            "profiles": [{"id": "game", "name": "Test game", "pokedex": "pokedex/game/pokedex.json",
                          "source_locale": "en_US"}],
            "languages": [{"profile": "game", "code": "ru", "texts": "game/ru/texts.json"}],
        }
        (self.data / "languages.json").write_text(json.dumps(manifest), encoding="utf-8")
        texts = {
            "schema": 1, "profile": "game", "language": "ru", "dataset": "pokedex/game/pokedex.json",
            "texts": {"species:978": ["Bounsweet", "Баунсвит"], "types:12": ["Grass", "Трава"]}, "page": {},
        }
        (self.data / "game/ru").mkdir(parents=True)
        (self.data / "game/ru/texts.json").write_text(json.dumps(texts, ensure_ascii=False), encoding="utf-8")

    def generate(self, name="site"):
        out = self.root / name
        result = ShareGenerator(self.data, out, "https://site.test", "game").generate()
        return out, result

    def test_reproducible_canonical_pages_and_images(self):
        first, result = self.generate("first")
        second, _ = self.generate("second")
        self.assertEqual(result, {"profile": "game", "page_count": 2, "image_count": 2, "share_pages": {"en": "all", "ru": "all"}})
        hashes = lambda root: {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                               for path in root.rglob("*") if path.is_file()}
        self.assertEqual(hashes(first), hashes(second))
        for language, name, locale in (("en", "Bounsweet", "en_US"), ("ru", "Баунсвит", "ru_RU")):
            page = (first / f"share/game/{language}/species/978/index.html").read_text()
            self.assertNotIn(str(self.root), page)
            self.assertIn(f'<link rel="canonical" href="https://site.test/share/game/{language}/species/978/">', page)
            self.assertIn(f'pokedex?share=1#game/{language}/species/978', page)
            self.assertIn(name, page)
            self.assertIn(f'<meta property="og:locale" content="{locale}">', page)
            self.assertIn('property="og:image:width" content="1200"', page)
            image = first / urlsplit(re.search(r'property="og:image" content="([^"]+)"', page)[1]).path.lstrip("/")
            self.assertEqual(hashlib.sha256(image.read_bytes()).hexdigest(), image.stem)
            self.assertEqual(png_pixels(image.read_bytes())[:2], (1200, 630))

    def test_application_changes_only_the_forward_address(self):
        default, _ = self.generate("default")
        out = self.root / "application"
        ShareGenerator(self.data, out, "https://site.test", "game", "https://app.test/toolkit/").generate()
        for path in default.rglob("*"):
            if not path.is_file():
                continue
            actual = (out / path.relative_to(default)).read_bytes()
            if path.suffix == ".html":
                self.assertIn(b"https://app.test/toolkit/pokedex?share=1#game/", actual)
                actual = actual.replace(b"https://app.test/toolkit/pokedex", b"../../../../../pokedex")
            self.assertEqual(actual, path.read_bytes())

    def test_application_validation_matches_public_base(self):
        for address in ["relative/", "https://", "https://user:secret@site.test", "https://site.test/?q=1", "https://site.test/#card"]:
            for option in ["public_base", "application"]:
                with self.subTest(option=option, address=address), self.assertRaises(ValueError):
                    values = {"public_base": "https://site.test", "application": None, option: address}
                    ShareGenerator(self.data, self.root / "invalid", profile="game", **values)

    def test_assembly_passes_the_application_to_share_pages(self):
        manifest = json.loads((self.data / "languages.json").read_text())
        manifest["bundle"] = "gen3tl-check.zip"
        manifest["languages"][0]["index"] = "game/ru/index.json"
        (self.data / "languages.json").write_text(json.dumps(manifest))
        out = self.root / "assembled"
        with contextlib.redirect_stdout(io.StringIO()):
            result = site_assemble(SimpleNamespace(data=str(self.data), out=str(out), public_base="https://game.test/",
                                                  application="https://app.test/toolkit/"))
        self.assertEqual(result, 0)
        page = (out / "share/game/ru/species/978/index.html").read_text()
        self.assertIn('data-destination="https://app.test/toolkit/pokedex?share=1#game/ru/species/978"', page)
        self.assertIn('property="og:url" content="https://game.test/share/game/ru/species/978/"', page)

    def test_images_and_pages_state_the_base_stats(self):
        site, _ = self.generate("stats")
        stats = "HP 42 · Attack 30 · Defense 38 · Sp. Attack 30 · Sp. Defense 38 · Speed 32 · Total 210"
        for path in (site / "share").rglob("index.html"):
            page = path.read_text()
            description = re.search(r'name="description" content="([^"]+)"', page)[1]
            alt = re.search(r'property="og:image:alt" content="([^"]+)"', page)[1]
            self.assertTrue(description.endswith(stats))
            self.assertTrue(alt.endswith(stats))
            self.assertIn(f"<p>{description}</p>", page)
            self.assertEqual(page.count(f'alt="{alt}"'), 1)
        pixels = png_pixels(next((site / "share/assets").glob("*.png")).read_bytes())[2]
        colours, _ = tier_colours(SITE_CSS.read_text(encoding="utf-8"))
        drawn = {pixel for row in pixels[232:582] for pixel in row[48:776]}
        # One numbered species ranks at the middle tier.
        self.assertIn(colours[2], drawn)
        self.assertFalse({colours[tier] for tier in (0, 1, 3, 4)} & drawn)


    @unittest.skipUnless(NODE, "no node")
    def test_stat_order_labels_and_tiers_match_the_species_card(self):
        records = [{"dex": dex, "base_stats": dict(zip(STAT_ORDER, stats))} for dex, stats in (
            (1, (45, 49, 49, 65, 65, 45)), (2, (60, 62, 63, 80, 80, 60)), (3, (80, 82, 83, 100, 100, 80)),
            (4, (39, 52, 43, 60, 50, 65)), (5, (20, 10, 230, 10, 230, 5)), (6, (255, 10, 10, 75, 135, 55)),
            (7, (45, 49, 49, 65, 65, 45)), (0, (1, 1, 1, 1, 1, 1)), (8, (50, 50, 50, 50, 50, 50)))]
        with tempfile.TemporaryDirectory() as tmp:
            script, source, out = Path(tmp) / "tiers.mjs", Path(tmp) / "in.json", Path(tmp) / "out.json"
            script.write_text(PAGE_TIERS % (ROOT / "web/js/pokedex/pokedex.js").as_uri(), encoding="utf-8")
            source.write_text(json.dumps(records), encoding="utf-8")
            run = subprocess.run([NODE, str(script), str(source), str(out)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            page = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(page["order"], list(STAT_ORDER))
        self.assertEqual(page["labels"], STAT_LABELS)
        self.assertEqual(page["tiers"], list(TIER_LABELS))
        ranks = stat_ranks(records)
        self.assertEqual(page["ranks"], ranks)
        image = [[tier_of(ranks[key], sum(record["base_stats"].values()) if key == "total" else record["base_stats"][key])
                  for key in (*STAT_ORDER, "total")] for record in records]
        self.assertEqual(page["values"], image)
        self.assertEqual(len({tier for row in image for tier in row}), 5)
        script = (ROOT / "web/js/pokedex/species-list.js").read_text(encoding="utf-8")
        self.assertEqual(int(re.search(r"const STAT_MAX = (\d+);", script).group(1)), STAT_MAX)

    def test_tier_colours_come_from_the_species_card_rules(self):
        css = SITE_CSS.read_text(encoding="utf-8")
        colours, ink = tier_colours(css)
        for tier, colour in enumerate(colours):
            self.assertRegex(css, rf"\.dex-bar\.t{tier}\b[^{{]*\{{ background: var\(--tier-{tier}\)")
            self.assertEqual(len(colour), 4)
        self.assertEqual(ink, (17, 17, 17, 255))
        with self.assertRaisesRegex(ValueError, "Stat tier colours"):
            tier_colours(css.replace("--tier-3:", "--tier-x:"))

    def test_share_pages_use_the_shared_theme_tokens(self):
        site, _ = self.generate("themed")
        page = (site / "share/game/en/species/978/index.html").read_text()
        shared = '<link rel="stylesheet" href="../../../../../site.css">'
        overrides = '<link rel="stylesheet" href="../../../../../share-target.css">'
        self.assertIn(shared, page)
        self.assertIn(overrides, page)
        self.assertLess(page.index(shared), page.index(overrides))
        css = (TOOLKIT_ROOT / "web/share-target.css").read_text()
        self.assertNotRegex(css, r"#[0-9a-fA-F]{3,8}\b|\b(?:rgb|hsl)a?\(")
        self.assertNotIn("color-scheme", css)
        for token in ("bg", "fg", "line", "panel", "accent", "on-accent"):
            self.assertIn(f"var(--{token})", css)
        self.assertRegex(css, r"img\s*\{[^}]*width:min\(100%,500px\)")
        self.assertIn("width:min(566px,calc(100% - 48px))", css)

    def test_share_pages_hand_over_from_the_head_before_painting(self):
        site, _ = self.generate("handover")
        page = (site / "share/game/en/species/978/index.html").read_text()
        head, body = page.split("</head>")
        scripts = re.findall(r"<script\b[^>]*>", head)
        self.assertEqual(len(scripts), 1)
        script = scripts[0]
        self.assertNotRegex(script, r"\b(defer|async)\b")
        self.assertIn('src="../../../../../share-target.js"', script)
        self.assertIn('data-destination="../../../../../pokedex?share=1#game/en/species/978"', script)
        self.assertLess(page.index(script), page.index("<body>"))
        self.assertNotIn("<script", body)
        self.assertNotIn("<style", page)
        self.assertNotIn("style=", page)
        self.assertIn("script-src 'self'; style-src 'self'", head)
        self.assertIn('<a id="share-destination" href="../../../../../pokedex?share=1#game/en/species/978">', body)
        self.assertRegex(body, r"<h1>.+</h1>\s*<p>.+</p>\s*<a id=\"share-destination\".+</a>\s*<img src=")

    @unittest.skipUnless(NODE, "no node")
    def test_form_names_use_game_labels_in_images_pages_and_metadata(self):
        generator = ShareGenerator(self.data, self.root / "forms", "https://site.test", "game")
        base = {**generator.records[0], "slot": 1, "name": "species:1", "dex": 1,
                "evolution": [{"target": 2, "method": 254, "parameter": 1},
                              {"target": 3, "method": 254, "parameter": 2},
                              {"target": 4, "method": 254, "parameter": 1, "kind": "primal"},
                              {"target": 5, "method": 253},
                              {"target": 6, "method": 254, "parameter": 1}]}
        generator.records[:] = [base, *({**base, "slot": slot, "evolution": []} for slot in range(2, 9))]
        generator.records[3].update(form_name="page:Primal", form_name_full=True)
        generator.records[5].update(form_name="species:2", form_name_full=True)
        generator.records[6].update(form_name="page:Alternate")
        generator.dataset["labels"] = {"Mega {species}": "s0000001@B_COPY_VAR_3", "Giga": "s0000002@^"}
        for texts in generator.texts.values():
            texts.update({"species:1": ["Sample", "Пример"], "species:2": ["Full form", "Полная форма"],
                          "items:1": ["Stone", "Камень"], "items:2": ["Stone X", "Камень X"],
                          "s0000001": ["Become Mega {B_COPY_VAR_3}!", "Станет Мега-{B_COPY_VAR_3}!"],
                          "s0000002": ["Giga power", "Гига сила"]})
        for page in generator.pages.values():
            page.update({"Primal": ["Ancient Sample", "Древний Пример"], "Alternate": ["Other", "Другой"]})
        expected = page_names(generator, self.root)
        self.assertEqual(expected["ru"][1]["name"], "Мега-Пример")
        self.assertEqual(expected["ru"][2]["name"], "Мега-Пример X")
        with mock.patch.object(generator, "image", wraps=generator.image) as image:
            generator.generate()
        self.assertEqual({call.args[0]["name"] for call in image.call_args_list},
                         {row["name"] for rows in expected.values() for row in rows})
        for path in (generator.out / "share").rglob("index.html"):
            language, _, slot, _ = path.relative_to(generator.out / "share/game").parts
            name = expected[language][int(slot) - 1]["name"]
            page = path.read_text()
            self.assertIn(name, html.unescape(re.search(r"<title>(.+)</title>", page)[1]))
            self.assertIn(name, html.unescape(re.search(r'property="og:image:alt" content="([^"]+)"', page)[1]))
            title = re.search(r"<title>(.+)</title>", page)[1]
            alt = re.search(r'property="og:image:alt" content="([^"]+)"', page)[1]
            for tag in (f'<h1>{title}</h1>', f'property="og:title" content="{title}"',
                        f'name="twitter:title" content="{title}"', f'alt="{alt}"'):
                self.assertIn(tag, page)
        for mapped, pair in (("s0000001@B_COPY_VAR_3", ["Mega {B_COPY_VAR_3}", "No placeholder"]),
                             ("s0000001", ["{COLOR RED}Mega {B_COPY_VAR_3}!\\pMore", ""]),
                             ("Mega-{species}", ["", ""]), (None, ["", ""]),
                             ("s0000001", ["", ""])):
            generator.dataset["labels"]["Mega {species}"] = mapped
            generator.texts["ru"]["s0000001"] = pair
            expected = page_names(generator, self.root)
            for language, rows in expected.items():
                self.assertEqual([generator.display_name(language, record) for record in generator.records],
                                 [row["name"] for row in rows])

    def test_missing_locale_metadata_falls_back_to_language_codes(self):
        manifest_path = self.data / "languages.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["profiles"][0].pop("source_locale")
        manifest["languages"][0]["code"] = "xx"
        manifest_path.write_text(json.dumps(manifest))
        texts_path = self.data / "game/ru/texts.json"
        texts = json.loads(texts_path.read_text())
        texts["language"] = "xx"
        texts_path.write_text(json.dumps(texts))
        site, _ = self.generate("fallback")
        for language in ("en", "xx"):
            page = (site / f"share/game/{language}/species/978/index.html").read_text()
            self.assertIn(f'<meta property="og:locale" content="{language}">', page)

    def test_invalid_identities_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Public base"):
            ShareGenerator(self.data, self.root / "bad", "relative/path", "game")
        with self.assertRaisesRegex(ValueError, "Unknown share profile"):
            ShareGenerator(self.data, self.root / "bad", "https://site.test", "other")
        with self.assertRaisesRegex(ValueError, "Invalid profile id"):
            ShareGenerator(self.data, self.root / "bad", "https://site.test", "../game")

    def test_share_number_is_the_internal_id_in_every_catalog(self):
        generator = ShareGenerator(self.data, self.root / "forms", "https://site.test", "game")
        record = generator.records[0]
        for browsing in (None, {"separate": True}, {"separate": False}):
            if browsing is not None:
                record["form_browsing"] = browsing
            for language in generator.texts:
                with self.subTest(language=language, browsing=browsing):
                    result = generator.species(language, record)
                    self.assertEqual(result["number"], "#978")
                    self.assertEqual(result["dex"], 761)
                    self.assertEqual(result["slot"], 978)
        record["dex"] = 0
        for language in generator.texts:
            self.assertEqual(generator.species(language, record)["number"], "")
        out, _ = self.generate("numbered")
        page = (out / "share/game/ru/species/978/index.html").read_text()
        self.assertIn("<title>#978 Баунсвит", page)

    def test_assembly_versions_generated_share_assets(self):
        manifest_path = self.data / "languages.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["bundle"] = "check.zip"
        for row in manifest["languages"]:
            row["index"] = str(Path(row["texts"]).with_name("index.json"))
        manifest_path.write_text(json.dumps(manifest))
        out = self.root / "assembled"
        args = SimpleNamespace(data=self.data, out=out, public_base="https://site.test/subpath/")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = site_assemble(args)
        self.assertEqual(result, 0, output.getvalue())
        pages = list((out / "share").rglob("index.html"))
        self.assertEqual(len(pages), 2)
        for page in pages:
            document = page.read_text()
            for name in ("share-target.js", "share-target.css"):
                relative = f"../../../../../assets/{__version__}/{name}"
                self.assertIn(relative, document)
                target = Path(urlsplit(urljoin(page.as_uri(), relative)).path)
                self.assertTrue(target.is_file())
            self.assertIn("https://site.test/subpath/share/", document)
