import base64
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from graphics_support import HEAD, fixture
from gen3tl.profile.profile import Profile
from project_fixture import write_policy
from gen3tl.web import editorcheck
from gen3tl.__main__ import main
from gen3tl.web.editorgfx import Pictures, graphics_data
from gen3tl.build.graphics import GREY, GraphicsConfig, GraphicsError, apply_area_form, area_form_png, area_png, hex_rgb, inject_graphics, pixel_check
from gen3tl.build.inject import FreeSpace
from gen3tl.check.render import png_image, read_png_image, write_indexed_png, write_png

def flat(rows) -> bytes:
    return bytes(v for row in rows for v in row)

def edited_t1(sheet, tiles: int | None = None):
    """T1 with text pixels in area 8,8,8,8 and optional appended tiles."""
    wt = sheet.width_tiles
    n = tiles or sheet.graphic.tiles
    px = [row[:] for row in sheet.pixels] + [[0] * sheet.width for _ in range(-(-n // wt) * 8 - sheet.height)]
    for y in range(9, 15):
        px[y][10] = 5
        px[y][13] = 6
    for k in range(sheet.graphic.tiles, n):
        dx, dy = (k % wt) * 8, (k // wt) * 8
        for r in range(8):
            px[dy + r][dx:dx + 8] = sheet.pixels[r][0:8]
    return px

AREAS = [[8, 8, 8, 8]]

class Fixture(unittest.TestCase):
    """A synthetic ROM (graphics_support.fixture), its config and a profile file for the CLI."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmpdir.name)
        self.base, self.lz_data, self.raw_data, self.slot = fixture(self.tmp)
        self.section = {"inventory": "inv.tsv", "sheet_width": {"T1": 4}}
        self.cfg = GraphicsConfig(self.section, self.tmp)
        self.sheet = self.cfg.sheet(self.base, self.cfg.graphic("T1"))
        self.rom_path = self.tmp / "base.gba"
        self.base.save(self.rom_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def profile(self, edits: str = "") -> Path:
        import tomllib

        from project_fixture import literal
        prof = self.tmp / "p.toml"
        translated = tomllib.loads(edits).get("graphics", {}).get("edit", [])
        facts = ""
        for entry in translated:
            entry["png"] = str(Path(entry["png"]).relative_to(self.tmp))
            metadata = {"evidence": entry.pop("evidence", "fixture")}
            if "tiles" in entry:
                metadata["max_tiles"] = entry["tiles"]
            facts += f'\n[graphics.edit_facts.{entry["id"]}]\n'
            facts += "\n".join(f"{k} = {literal(v)}" for k, v in metadata.items()) + "\n"
        prof.write_text(f'[rom]\nname = "t"\npath = "{self.rom_path}"\nsha1 = "{self.base.sha1()}"\n\n'
                        f'[graphics]\ninventory = "{self.tmp / "inv.tsv"}"\n\n[graphics.sheet_width]\nT1 = 4\n'
                        + facts, encoding="utf-8")
        write_policy(self.tmp, Profile.load(prof), graphics={"edit": translated})
        return prof

    def cli(self, prof: Path, *args) -> tuple[int, list[str]]:
        (self.tmp / "projects.toml").write_text('[defaults]\nprofile = "p"\nlanguage = "ru"\n[[project]]\nprofile = "p"\nlanguage = "ru"\nroot = "."\npolicy = "policy.toml"\n', encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = main(["--project-root", str(self.tmp), "--profile", str(prof), *args])
        return code, out.getvalue().splitlines()

    def data(self, **extra) -> dict:
        return graphics_data(dict(self.section, **extra), self.tmp)

class AreaFormTest(Fixture):
    def test_only_the_areas_with_a_grey_palette(self):
        px = edited_t1(self.sheet)
        img = png_image(area_form_png(self.sheet, px, AREAS))
        self.assertEqual(img.palette, GREY)
        self.assertNotEqual(self.sheet.palette, GREY)
        for y, row in enumerate(img.indices):
            for x, v in enumerate(row):
                inside = 8 <= x < 16 and 8 <= y < 16
                self.assertEqual(v, px[y][x] if inside else 0, (x, y))

    def test_laid_over_the_sheet_gives_the_edit_back(self):
        px = edited_t1(self.sheet)
        back, findings = apply_area_form(self.sheet, png_image(area_form_png(self.sheet, px, AREAS)), AREAS)
        self.assertEqual(findings, [])
        self.assertEqual((back.indices, back.palette), (px, self.sheet.palette))
        chk = pixel_check(self.sheet, back, AREAS)
        self.assertEqual((chk.findings, chk.changed_inside), ([], 12))

    def test_appended_tiles(self):
        px = edited_t1(self.sheet, 17)
        img = png_image(area_form_png(self.sheet, px, AREAS, 17))
        self.assertEqual((img.width, img.height), (32, 40))
        self.assertEqual([row[0:8] for row in img.indices[32:40]], [row[0:8] for row in self.sheet.pixels[0:8]])
        self.assertEqual(flat(row[8:] for row in img.indices[32:40]), bytes(24 * 8))   # past the 17th tile
        back, findings = apply_area_form(self.sheet, img, AREAS, 17)
        self.assertEqual((findings, back.indices), ([], px))

    def test_findings(self):
        px = edited_t1(self.sheet)
        form = [row[:] for row in png_image(area_form_png(self.sheet, px, AREAS)).indices]
        form[2][30] = 4
        bad = self.tmp / "bad.png"
        write_indexed_png(bad, form, GREY, 4)
        back, findings = apply_area_form(self.sheet, read_png_image(bad), AREAS)
        self.assertEqual(findings, ["pixel 30,2: index 4 outside the text areas of the area form"])
        self.assertEqual(back.indices[2][30], self.sheet.pixels[2][30])
        write_indexed_png(bad, form[:8], GREY, 4)
        self.assertEqual(apply_area_form(self.sheet, read_png_image(bad), AREAS),
                         (None, ["dimensions 32x8 differ from the original 32x32"]))
        write_png(bad, [[(0, 0, 0)] * 32 for _ in range(32)])
        self.assertEqual(apply_area_form(self.sheet, read_png_image(bad), AREAS),
                         (None, ["the area form is not an indexed PNG"]))
        with self.assertRaisesRegex(GraphicsError, "index 16 does not fit a 4bpp graphic"):
            area_png([[16]], 4)

class BuildTest(Fixture):
    """The build writes the same bytes from the area form as from the whole sheet."""

    def build(self, edits) -> tuple[bytes, list]:
        cfg = GraphicsConfig(dict(self.section, edit=edits), self.tmp)
        rom = self.base.copy()
        res = inject_graphics(rom, self.base, cfg, FreeSpace.scan(bytes(self.base.data)))
        self.assertEqual(res.failures, [])
        return bytes(rom.data), [(p.id, p.mode, p.new, p.tiles, p.changed_pixels) for p in res.placements]

    def test_same_bytes(self):
        t2 = self.cfg.sheet(self.base, self.cfg.graphic("T2"))
        px2 = [row[:] for row in t2.pixels]
        px2[3][3] = 9
        for tiles in (None, 17):
            px1 = edited_t1(self.sheet, tiles)
            write_indexed_png(self.tmp / "t1.png", px1, self.sheet.palette, 4)
            write_indexed_png(self.tmp / "t2.png", px2, t2.palette, 4)
            (self.tmp / "t1a.png").write_bytes(area_form_png(self.sheet, px1, AREAS, tiles))
            (self.tmp / "t2a.png").write_bytes(area_form_png(t2, px2, [[0, 0, 8, 8]]))
            extra = {} if tiles is None else {"tiles": tiles, "evidence": "test"}
            sheets = [dict({"id": "T1", "png": "t1.png", "areas": AREAS}, **extra),
                      {"id": "T2", "png": "t2.png", "areas": [[0, 0, 8, 8]]}]
            forms = [dict(e, png=e["png"].replace(".png", "a.png"), form="areas") for e in sheets]
            with self.subTest(tiles=tiles):
                a, b = self.build(sheets), self.build(forms)
                self.assertEqual(a, b)
                self.assertNotEqual(a[0], bytes(self.base.data))

    def test_area_form_findings_fail_the_build(self):
        form = [[0] * 32 for _ in range(32)]
        form[0][0] = 3
        write_indexed_png(self.tmp / "t1a.png", form, GREY, 4)
        cfg = GraphicsConfig(dict(self.section, edit=[{"id": "T1", "png": "t1a.png", "areas": AREAS,
                                                       "form": "areas"}]), self.tmp)
        rom = self.base.copy()
        res = inject_graphics(rom, self.base, cfg, FreeSpace.scan(bytes(self.base.data)))
        self.assertEqual(res.failures, ["graphic T1: area form: 1 finding(s), first: "
                                        "pixel 0,0: index 3 outside the text areas of the area form"])
        self.assertEqual(rom.data, self.base.data)
        with self.assertRaisesRegex(GraphicsError, "form 'diff' is not one of sheet, areas"):
            GraphicsConfig(dict(self.section, edit=[{"id": "T1", "png": "x", "areas": [], "form": "diff"}]), self.tmp)

class GfxAreasCommandTest(Fixture):
    def test_writes_the_area_form(self):
        write_indexed_png(self.tmp / "t1.png", edited_t1(self.sheet, 17), self.sheet.palette, 4)
        prof = self.profile(f'[[graphics.edit]]\nid = "T1"\npng = "{self.tmp / "t1.png"}"\nareas = [[8, 8, 8, 8]]\n'
                            'tiles = 17\nevidence = "test"\n')
        code, out = self.cli(prof, "build", "gfx-areas", "T1", "--out", str(self.tmp / "areas"))
        self.assertEqual(code, 0, out)
        form = self.tmp / "areas" / "T1.png"
        self.assertEqual(form.read_bytes(), area_form_png(self.sheet, edited_t1(self.sheet, 17), AREAS, 17))
        self.assertIn("form         areas", out)
        # the converted entry builds, and is not converted twice
        prof = self.profile(f'[[graphics.edit]]\nid = "T1"\npng = "{form}"\nform = "areas"\nareas = [[8, 8, 8, 8]]\n'
                            'tiles = 17\nevidence = "test"\n')
        self.assertEqual(self.cli(prof, "build", "gfx-areas", "T1", "--out", str(self.tmp / "again")),
                         (1, ["graphic      T1", "error        area form"]))
        self.assertEqual(self.cli(prof, "build", "gfx-areas", "T2", "--out", str(self.tmp / "again")),
                         (1, ["graphic      T2", "error        missing graphics edit"]))
        self.assertFalse((self.tmp / "again").exists())

    def test_refuses_a_sheet_that_fails_the_check(self):
        px = edited_t1(self.sheet)
        px[0][0] = 7
        write_indexed_png(self.tmp / "t1.png", px, self.sheet.palette, 4)
        prof = self.profile(f'[[graphics.edit]]\nid = "T1"\npng = "{self.tmp / "t1.png"}"\nareas = [[8, 8, 8, 8]]\n')
        code, out = self.cli(prof, "build", "gfx-areas", "T1", "--out", str(self.tmp / "areas"))
        self.assertEqual((code, out), (1, ["graphic      T1", "findings     1 findings",
                                           "error        pixel 0,0: index 1 -> 7 outside the text areas"]))
        self.assertFalse((self.tmp / "areas").exists())

class GraphicsDataTest(Fixture):
    def test_none_without_an_inventory(self):
        self.assertIsNone(graphics_data(None, self.tmp))
        self.assertIsNone(graphics_data({"sheet_width": {}}, self.tmp))

    def test_catalogue_without_rom_colour_or_art(self):
        px = edited_t1(self.sheet, 17)
        write_indexed_png(self.tmp / "t1.png", px, self.sheet.palette, 4)
        t2 = self.cfg.sheet(self.base, self.cfg.graphic("T2"))
        (self.tmp / "t2a.png").write_bytes(area_form_png(t2, t2.pixels, [[0, 0, 8, 8]]))
        data = self.data(palette={"T2": {"at": "raw:0x0004000", "evidence": "test"}},
                         edit=[{"id": "T1", "png": "t1.png", "areas": AREAS, "tiles": 17, "evidence": "e"},
                               {"id": "T2", "png": "t2a.png", "areas": [[0, 0, 8, 8]], "form": "areas"}],
                         tilemap=[{"name": "map", "offset": 0x6000, "size": 64, "width": 32, "pointers": [0x208],
                                   "graphic": "T1", "cells": [{"row": 0, "column": 1, "tile": 16}]}])
        data = json.loads(json.dumps(data))   # plain JSON
        self.assertEqual(set(data), {"inventory", "sheet_width", "palette", "edits", "tilemaps",
                                     "previews", "tilemap_edits"})
        self.assertEqual([r["id"] for r in data["inventory"]], ["T1", "T2", "T3"])
        self.assertEqual(set(data["inventory"][0]), set(HEAD) - {"image", "source_text"} | {"source_digest"})
        self.assertEqual((data["sheet_width"], data["palette"]), ({"T1": 4}, {"T2": "raw:0x0004000"}))
        self.assertEqual(data["tilemaps"], [{"name": "map", "graphic": "T1", "cells": 1, "evidence": ""}])
        e = data["edits"]["T1"]
        self.assertEqual((e["areas"], e["tiles"], e["evidence"]), (AREAS, 17, "e"))
        img = png_image(base64.b64decode(e["png"]))
        self.assertEqual(img.palette, GREY)
        self.assertEqual(img.indices, png_image(area_form_png(self.sheet, px, AREAS, 17)).indices)
        self.assertEqual(png_image(base64.b64decode(data["edits"]["T2"]["png"])).palette, GREY)


    def test_a_shared_edit_shows_under_every_translate_row_of_the_graphic(self):
        inventory = self.tmp / "inv.tsv"
        t1 = inventory.read_text(encoding="utf-8").split("\n")[1].split("\t")
        shared = [["T4", "s", "SECOND LABEL", "translate", "the same graphic as T1"],
                  ["T5", "s", "MARK", "keep", "the same graphic as T1"],
                  ["T6", "s", "WIDE LABEL", "translate", "the same graphic as T1, 16 tiles a row"]]
        with inventory.open("a", encoding="utf-8") as stream:
            for row in shared:
                stream.write("\t".join(row + t1[5:]) + "\n")
        write_indexed_png(self.tmp / "t1.png", edited_t1(self.sheet), self.sheet.palette, 4)
        data = json.loads(json.dumps(self.data(sheet_width={"T1": 4, "T4": 4},
                                               edit=[{"id": "T1", "png": "t1.png", "areas": AREAS}])))
        self.assertEqual(list(data["edits"]), ["T1", "T4"], "not a keep row, another layout or another graphic")
        self.assertEqual(data["edits"]["T4"], {**data["edits"]["T1"], "shared": "T1"})
        view = {"width": 8, "height": 8, "rects": [[8, 8, 8, 8, 0, 0]]}
        data["previews"] = {"T1": [{**view, "label": "first label"}], "T4": [{**view, "label": "second label"}]}
        pics = Pictures(data, bytes(self.base.data))
        self.assertEqual(pics.edited("T4"), {**pics.edited("T1"), "id": "T4"})
        [first], [second] = pics.preview("T1", edited=True), pics.preview("T4", edited=True)
        self.assertEqual((second["label"], second["pixels"]), ("second label", first["pixels"]), "its own view")
        self.assertNotEqual(second["pixels"], pics.preview("T4")[0]["pixels"], "the edited pixels")
        self.assertEqual(pics.edited("T5")["findings"], ["T5 has no edit in the project"])


class PicturesTest(Fixture):
    """Pictures of the editor data: the sheets of build gfx-extract, the output of build gfx-check."""

    def setUp(self):
        super().setUp()
        write_indexed_png(self.tmp / "t1.png", edited_t1(self.sheet), self.sheet.palette, 4)
        self.graphics = self.data(edit=[{"id": "T1", "png": "t1.png", "areas": AREAS}])
        self.pics = Pictures(json.dumps(self.graphics), bytes(self.base.data))
        self.prof = self.profile()

    def test_sheets_as_gfx_extract(self):
        code, _ = self.cli(self.prof, "build", "gfx-extract", "T1", "T2", "--out", str(self.tmp / "x"))
        self.assertEqual(code, 0)
        for gid in ("T1", "T2"):
            png = (self.tmp / "x" / f"{gid}.png").read_bytes()
            self.assertEqual(self.pics.png(gid), png)
            self.assertEqual(base64.b64decode(self.pics.png_b64(gid)), png)
            s = json.loads(self.pics.sheet_json(gid))
            img = png_image(png)
            self.assertEqual((s["width"], s["height"]), (img.width, img.height))
            self.assertEqual(base64.b64decode(s["pixels"]), flat(img.indices))
            self.assertEqual(s["palette"], [hex_rgb(c) for c in img.palette])
        self.assertEqual(json.loads(self.pics.sheet_json("T2"))["palette_source"], "grey (palette not located)")
        with self.assertRaisesRegex(GraphicsError, "no ROM source"):
            self.pics.sheet("T3")

    def test_without_a_rom(self):
        pics = Pictures(self.graphics)
        with self.assertRaisesRegex(GraphicsError, "no ROM loaded"):
            pics.sheet("T1")
        self.assertEqual(pics.check("T1", b"", "x.png", AREAS)["lines"], ["FAIL     no ROM loaded"])

    def check_both(self, png: Path, areas, tiles=None) -> dict:
        """Browser and terminal reports retain the same findings."""
        args = ["build", "gfx-check", "T1", str(png)] + ["--area", *[",".join(map(str, a)) for a in areas]] * bool(areas)
        code, lines = self.cli(self.prof, *args, *(["--tiles", str(tiles)] if tiles else []))
        res = json.loads(self.pics.check_json("T1", base64.b64encode(png.read_bytes()).decode(), png.name,
                                              json.dumps(areas), tiles))
        self.assertEqual(res["code"], code)
        self.assertIn("verified     " + ("passed" if code == 0 else "failed"), lines)
        expected = [message.removeprefix("FAIL     ") for message in res["lines"] if message.startswith("FAIL     ")]
        actual = [message.split(None, 1)[1] for message in lines if message.startswith("error ")]
        self.assertEqual(actual, expected)
        return res

    def test_check_as_gfx_check(self):
        for tiles in (None, 17):
            px = edited_t1(self.sheet, tiles)
            write_indexed_png(self.tmp / "up.png", px, self.sheet.palette, 4)
            res = self.check_both(self.tmp / "up.png", AREAS, tiles)
            self.assertEqual((res["code"], res["tiles"]), (0, tiles))
            self.assertEqual(base64.b64decode(res["pixels"]), flat(px))
            form = base64.b64decode(res["area_png"])
            self.assertEqual(form, area_form_png(self.sheet, px, AREAS, tiles))
            back = self.pics.edited("T1", form, AREAS, tiles)
            self.assertEqual((back["findings"], base64.b64decode(back["pixels"])), ([], flat(px)))
        px = edited_t1(self.sheet)
        px[31][0] = 2
        write_indexed_png(self.tmp / "bad.png", px, self.sheet.palette, 4)
        res = self.check_both(self.tmp / "bad.png", AREAS)
        self.assertEqual((res["code"], res["area_png"]), (1, None))
        self.assertEqual(res["findings"], ["pixel 0,31: index 1 -> 2 outside the text areas"])
        self.assertEqual(self.check_both(self.tmp / "bad.png", AREAS, 5)["code"], 2)   # below the original count
        res = self.pics.check("T1", b"\x89PNG\r\n\x1a\n\x00\x00", "junk.png", AREAS)   # truncated
        self.assertEqual((res["code"], res["pixels"]), (2, None))
        self.assertRegex(res["lines"][0], r"^FAIL     junk.png: unreadable PNG")
        res = self.pics.check("T1", b"not a png", "x.png", AREAS)
        self.assertEqual((res["code"], res["lines"]), (2, ["FAIL     x.png: not a PNG file"]))

    def test_project_edit(self):
        res = json.loads(self.pics.edited_json("T1"))
        self.assertEqual((res["findings"], res["changed"], res["areas"], res["tiles"]), ([], 12, AREAS, 16))
        self.assertEqual(base64.b64decode(res["pixels"]), flat(edited_t1(self.sheet)))
        self.assertEqual(png_image(base64.b64decode(res["png"])).palette[:16], self.sheet.palette)
        self.assertEqual(self.pics.edited("T2")["findings"], ["T2 has no edit in the project"])
        self.assertEqual(self.pics.edited("T1", self.pics.png("T1"), ["1,2"], None)["findings"],
                         ["text area '1,2' is not x,y,w,h"])

class BundleTest(unittest.TestCase):
    def test_editorgfx_alone(self):
        self.assertIn("web/editorgfx.py", editorcheck.BUNDLE)
        with tempfile.TemporaryDirectory() as tmp:
            editorcheck.write_bundle(Path(tmp) / "b.zip", {})
            with zipfile.ZipFile(Path(tmp) / "b.zip") as z:
                z.extractall(tmp)
            code = ("import sys; sys.path.insert(0, sys.argv[1]); import gen3tl.web.editorgfx as g; "
                    "print(g.__file__.startswith(sys.argv[1]))")
            run = subprocess.run([sys.executable, "-I", "-c", code, tmp], capture_output=True, text=True)
            self.assertEqual((run.stdout.split(), run.stderr), (["True"], ""))
