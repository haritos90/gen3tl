import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from test_pokedexshare_png import rgba_png
from project_fixture import CATALOG_STAGE, SAMPLE
from gen3tl.cli.web import site_assemble
from gen3tl.profile.profile import Profile
from gen3tl.web.pokedexshare import ShareGenerator, generate, png_bytes, png_pixels


def sample_data(data, profile=SAMPLE):
    game = Profile.by_name(SAMPLE)
    dataset_path = f"pokedex/{profile}/pokedex.json"
    sprite = data / f"pokedex/{profile}/sprites/front.png"
    sprite.parent.mkdir(parents=True)
    sprite.write_bytes(png_bytes(2, 2, bytearray((20, 40, 60, 255) * 4)))
    dataset = {
        "format": "gen3tl-pokedex", "schema": 1, "profile": {"id": profile}, "count": 2,
        "types": [{"id": 1, "colour": "#78C850"}],
        "species": [{"slot": slot, "dex": slot, "name": f"species:{slot}", "types": [1],
                     "base_stats": {"hp": 42, "attack": 30, "defense": 38,
                                    "sp_attack": 30, "sp_defense": 38, "speed": 32},
                     "sprite": {"front": "front.png"}} for slot in (1, 2)],
    }
    (data / dataset_path).write_text(json.dumps(dataset), encoding="utf-8")
    manifest = {"bundle": "check.zip", "profiles": [{"id": profile, "name": game["rom"]["name"],
                                                     "pokedex": dataset_path}], "languages": []}
    for code in ("ru", "xx"):
        path = data / profile / code / "texts.json"
        path.parent.mkdir(parents=True)
        manifest["languages"].append({"profile": profile, "code": code,
                                      "index": f"{profile}/{code}/index.json",
                                      "texts": path.relative_to(data).as_posix()})
        texts = {"species:1": ["Sample", "Пример"], "species:2": ["Second", "Второй"],
                 "types:1": ["Grass", "Трава"]}
        if code == "xx":
            texts = {key: [pair[0], ""] for key, pair in texts.items()}
        path.write_text(json.dumps({"schema": 1, "profile": profile, "language": code,
                                    "dataset": dataset_path, "texts": texts, "page": {}},
                                   ensure_ascii=False), encoding="utf-8")
    return manifest


class ShareLanguagesTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.data = self.root / "data"
        self.manifest = sample_data(self.data)
        self.write_manifest()

    def write_manifest(self):
        (self.data / "languages.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def generator(self):
        return ShareGenerator(self.data, self.root / "site", "https://site.test/", SAMPLE)

    def test_untranslated_language_has_no_pages_or_manifest(self):
        generator = self.generator()
        result = generator.generate()
        self.assertEqual(result["share_pages"], {"en": "all", "ru": "all", "xx": []})
        self.assertEqual(result["page_count"], 4)
        self.assertFalse((generator.out / f"share/{SAMPLE}/xx").exists())
        self.assertEqual(list((generator.out / "share").rglob("*.json")), [])

    def test_russian_and_english_pages_change_only_card_encoding(self):
        generator = self.generator()
        generator.generate()
        share = generator.out / "share"
        names = {}
        actual = {}
        for path in (share / "assets").glob("*.png"):
            width, height, rows = png_pixels(path.read_bytes())
            pixels = b"".join(bytes(pixel) for row in rows for pixel in row)
            digest = hashlib.sha256(rgba_png(width, height, pixels)).hexdigest()
            names[path.name] = digest + ".png"
            actual[f"assets/{digest}.png"] = digest
        for path in share.rglob("index.html"):
            document = path.read_bytes()
            for name, original in names.items():
                document = document.replace(name.encode(), original.encode())
            actual[path.relative_to(share).as_posix()] = hashlib.sha256(document).hexdigest()
        expected = json.loads((CATALOG_STAGE / "share-hashes.json").read_text(encoding="utf-8"))
        self.assertEqual(actual, expected)

    def test_explicit_english_translations_do_not_create_pages(self):
        generator = self.generator()
        generator.texts["xx"] = {key: [pair[0], pair[0]] for key, pair in generator.texts["xx"].items()}
        self.assertEqual(generator.generate()["share_pages"], {"en": "all", "ru": "all", "xx": []})

    def test_unrelated_translated_text_does_not_create_pages(self):
        generator = self.generator()
        generator.texts["xx"]["moves:1"] = ["Move", "Translated move"]
        self.assertEqual(generator.generate()["share_pages"], {"en": "all", "ru": "all", "xx": []})

    def test_one_translated_species_keeps_only_its_page(self):
        generator = self.generator()
        generator.texts["xx"]["species:2"][1] = "Translated"
        self.assertEqual(generator.generate()["share_pages"], {"en": "all", "ru": "all", "xx": [2]})
        self.assertEqual([path.parent.name for path in (generator.out / f"share/{SAMPLE}/xx").rglob("index.html")], ["2"])

    def test_translated_stat_labels_keep_language_pages(self):
        generator = self.generator()
        generator.pages["xx"]["Attack"] = ["Attack", "Translated attack"]
        self.assertEqual(generator.generate()["share_pages"], {"en": "all", "ru": "all", "xx": "all"})

    def test_translated_form_label_keeps_only_its_page(self):
        generator = self.generator()
        generator.records[1]["form_name"] = "page:Summer"
        generator.pages["xx"]["Summer"] = ["Summer", "Translated form"]
        self.assertEqual(generator.generate()["share_pages"], {"en": "all", "ru": "all", "xx": [2]})

    def test_translated_link_text_keeps_language_pages(self):
        generator = self.generator()
        generator.site["xx"]["Open the Pokédex"] = "Open translated page"
        self.assertEqual(generator.generate()["share_pages"], {"en": "all", "ru": "all", "xx": "all"})

    def test_page_selection_is_independent_for_each_profile(self):
        other = sample_data(self.data, "second-game")
        path = self.data / "second-game/xx/texts.json"
        texts = json.loads(path.read_text())
        texts["texts"]["species:1"][1] = "Translated"
        path.write_text(json.dumps(texts))
        self.manifest["profiles"] += other["profiles"]
        self.manifest["languages"] += other["languages"]
        self.write_manifest()
        out = self.root / "multiple"
        result = generate(self.data, out, "https://site.test/")
        self.assertEqual([row["share_pages"] for row in result], [{"en": "all", "ru": "all", "xx": []}, {"en": "all", "ru": "all", "xx": [1]}])
        self.assertTrue((out / f"share/{SAMPLE}/ru/species/1/index.html").is_file())
        self.assertTrue((out / "share/second-game/xx/species/1/index.html").is_file())

    def test_assembly_publishes_page_availability_once_in_existing_profile_data(self):
        original = copy.deepcopy(self.manifest)
        args = SimpleNamespace(data=self.data, out=self.root / "assembled", public_base="https://site.test/")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = site_assemble(args)
        self.assertEqual(result, 0, output.getvalue())
        published = json.loads((args.out / "data/languages.json").read_text())
        self.assertEqual(published["profiles"][0].pop("share_pages"), {"en": "all", "ru": "all", "xx": []})
        self.assertEqual(published, original)
        self.assertEqual(json.loads((self.data / "languages.json").read_text()), original)
        self.assertEqual(list((args.out / "share").rglob("*.json")), [])

    def test_regeneration_removes_stale_pages_and_legacy_manifests(self):
        out = self.root / "regenerated"
        share = out / "share"
        stale = share / SAMPLE / "xx/species/1/index.html"
        stale.parent.mkdir(parents=True)
        stale.write_text("outdated page")
        for manifest in (share / "manifest.json", share / SAMPLE / "manifest.json"):
            manifest.write_text("{}")
        generate(self.data, out, "https://site.test/")
        self.assertFalse(stale.exists())
        self.assertEqual(list(share.rglob("*.json")), [])
