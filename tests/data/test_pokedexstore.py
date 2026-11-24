"""The stored form of the Pokedex datasets and its decoders."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from gen3tl.data import pokedexdata, pokedexstore, pokedextexts, pokedextrainerivs
from test_roots import TOOLKIT_ROOT

NODE = shutil.which("node")
DECODE = '''
import fs from "node:fs";
const store = await import(process.argv[1]);
process.stdout.write(JSON.stringify(store.decodeDataset(JSON.parse(fs.readFileSync(process.argv[2], "utf8")))));
'''


def page_decoded(path: Path):
    """The document the page decoder (web/js/pokedexstore.js) reads from a stored file."""
    if NODE is None:
        raise unittest.SkipTest("node is unavailable")
    store = TOOLKIT_ROOT / "web" / "js" / "pokedexstore.js"
    result = subprocess.run([NODE, "--input-type=module", "-e", DECODE, store.as_uri(), str(path)],
                            capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def exact(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def species(slot: int) -> dict:
    return {"slot": slot, "name": f"species:{slot}", "offset": f"0x{0x19e0000 + slot * 28:06x}",
            "dex_offset": None if slot == 3 else f"0x{0x1a30000 + slot * 36:06x}",
            "category_offset": None if slot == 3 else f"0x{0x1a30000 + slot * 36:06x}",
            "sprite": {"front": f"sprites/{slot}-front.png", "back": f"sprites/{slot}-back.png"},
            "types": [12, 3] if slot % 2 else [10], "form_name": None, "height": 7, "weight": 6.5,
            "evolution": [{"method": 4, "parameter": 16, "target": slot + 1, "extra": 0}] if slot < 4 else [],
            **({"form_browsing": {"separate": True}} if slot == 4 else {})}


DOC = {
    "format": pokedexdata.FORMAT, "schema": pokedexdata.SCHEMA, "toolkit": "0.0.0",
    "profile": {"id": "test", "name": "Test"}, "missing": [],
    "species": [species(slot) for slot in range(1, 6)],
    "moves": [{"id": n, "name": f"moves:{n}", "flags": True if n == 2 else 1, "script": f"0x{0x16CD3D + 4 * n:07X}",
               "list_keys": ["punching"] if n % 2 else [], "mechanics": None} for n in range(4)],
    "trainers": [{"id": n, "party": [{"index": i, "level": 5 + i, "offset": f"0x{0x7b0000 + 16 * n + 8 * i:06x}",
                                      "abilities": {"0/0": [47], "0/1": [47]}} for i in range(2)]}
                 for n in range(3)],
    "learnsets": {"1": [[0, 33, 1, None, "0x000210"], [0, 45, 3, None, "0x000213"], [1, 264, None, 0, "0x000300"]],
                  "2": [], "3": [[0, 33, 1, None, "0x000410"]]},
    "labels": {"Total": "s0000001", "0": "zero"},
    "map_sections": [{"id": 88, "name": "s0720F5C"}],
    "evs": {"0": [0, 0, 0, 0, 0, 0], "1": {"alternatives": [{"evs": None, "when": None}, {"evs": [1, 2], "when": 3}]}},
}


class StoreTest(unittest.TestCase):
    def test_decoding_gives_back_every_record_in_order_and_type(self):
        stored = pokedexstore.encode(DOC)
        written = json.loads(pokedexstore._json(stored))
        self.assertEqual(exact(pokedexstore.decode(written)), exact(json.loads(pokedexstore._json(DOC))))
        decoded = pokedexstore.decode(written)
        self.assertIs(type(decoded["moves"][2]["flags"]), bool)   # true stays distinct from 1
        self.assertIs(type(decoded["species"][0]["weight"]), float)
        self.assertNotIn("form_browsing", decoded["species"][0])   # an absent field stays absent
        self.assertEqual(decoded["species"][2]["dex_offset"], None)
        decoded["species"][0]["sprite"]["front"] = "changed"   # no record shares a value with another
        decoded["species"][0]["types"].append(0)
        self.assertEqual(decoded["species"][1]["sprite"]["front"], "sprites/2-front.png")
        self.assertEqual(decoded["species"][2]["types"], [12, 3])

    def test_each_repeated_value_is_stored_once_and_defaults_are_absent(self):
        stored = pokedexstore.encode(DOC)
        codec = stored[pokedexstore.CODEC]
        head = codec["species"]
        self.assertEqual(head["keys"], sorted({key for record in DOC["species"] for key in record}))
        self.assertEqual(head["defaults"]["height"], 7)
        self.assertEqual(head["derived"]["name"], {"of": "slot", "text": "species:{}"})
        self.assertEqual(head["derived"]["offset"]["step"], 28)
        text = pokedexstore._json({key: value for key, value in stored.items() if key != pokedexstore.CODEC})
        for value in ('"species:', "sprites/", '"0x', '"height"', '"evolution"', '"punching"'):
            self.assertNotIn(value, text)
        self.assertEqual(codec["learnsets/*"]["columns"], 5)
        self.assertEqual(codec["trainers/party"]["derived"]["offset"]["step"], 8)
        self.assertEqual(stored["labels"], DOC["labels"])   # maps of texts stay as written

    def test_a_document_without_a_codec_is_its_own(self):
        previous = json.loads(pokedexstore._json({**DOC, "schema": 1}))
        self.assertIs(pokedexstore.decode(previous), previous)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pokedex.json"
            path.write_text(json.dumps(previous), encoding="utf-8")
            self.assertEqual(pokedexstore.load(path), previous)
            self.assertEqual(page_decoded(path), previous)

    def test_the_page_decodes_the_stored_form_to_the_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pokedex.json"
            path.write_text(pokedexstore._json(pokedexstore.encode(DOC)), encoding="utf-8")
            self.assertEqual(page_decoded(path), json.loads(pokedexstore._json(DOC)))

    def test_the_page_fixtures_are_what_the_encoders_write(self):
        fixtures = json.loads((TOOLKIT_ROOT / "web/test/pokedexstore-fixtures.json").read_text(encoding="utf-8"))
        for name in ("dataset", "learnsets", "trainer_ivs"):
            self.assertEqual(pokedexstore.encode(fixtures[name]["document"]), fixtures[name]["stored"], name)
        texts = fixtures["texts"]
        own, part = pokedextexts.split(texts["document"])
        self.assertEqual({**own, "source": texts["stored"]["source"]}, texts["stored"])
        self.assertEqual(part, texts["part"])
        self.assertEqual(pokedextexts.decode(texts["stored"], part), texts["document"])

    def test_damaged_files_are_refused(self):
        stored = json.loads(pokedexstore._json(pokedexstore.encode(DOC)))
        stored["species"][0]["zz"] = 1
        with self.assertRaisesRegex(pokedexstore.StoreError, "invalid stored dataset"):
            pokedexstore.decode(stored)
        with self.assertRaisesRegex(pokedexstore.StoreError, "named codec"):
            pokedexstore.encode({**DOC, "codec": {}})

    def test_written_datasets_read_back_as_built(self):
        doc = json.loads(pokedexstore._json({key: value for key, value in DOC.items() if key != "learnsets"}))
        for record in doc["species"]:
            record["learnset"] = [{"species": record["slot"], "move": 33, "method": 0, "index": None, "level": level,
                                   "offset": f"0x{0x210 + 3 * level:06x}"} for level in range(record["slot"])]
        doc["profile_block"] = "b" * 64
        doc["trainer_ivs"] = {"format": pokedextrainerivs.FORMAT, "schema": pokedextrainerivs.DOC_SCHEMA,
                              "profile_block": "b" * 64, "members": [
                                  {"trainer": n, "slot": 0, "contexts": {"0/0": [31] * 6}} for n in range(3)]}
        with tempfile.TemporaryDirectory() as tmp:
            written = pokedexdata.write(Path(tmp), doc)
            stored = {path.name: json.loads(path.read_text(encoding="utf-8")) for path in written}
            self.assertEqual({name: doc["schema"] for name, doc in stored.items()},
                             {"pokedex.json": "2.0.0", "learnsets.json": "2.0.0", "trainer-ivs.json": "4.0.0"})
            self.assertIn(pokedexstore.CODEC, stored["pokedex.json"])
            self.assertIn(pokedexstore.CODEC, stored["trainer-ivs.json"])
            back = pokedexdata.read(written[0])
            self.assertEqual(back["species"], doc["species"])
            self.assertEqual(back["trainer_ivs"], doc["trainer_ivs"])
            self.assertEqual(page_decoded(Path(tmp) / pokedextrainerivs.FILENAME), doc["trainer_ivs"])


if __name__ == "__main__":
    unittest.main()
