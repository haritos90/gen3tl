from test_roots import SUPPORT_ROOT, TOOLKIT_ROOT
"""Every check message has a site catalog key."""
import dataclasses
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))
sys.path.insert(0, str(SUPPORT_ROOT))

from graphics_support import fixture  # noqa: E402
from gen3tl.build.font import FORMATS  # noqa: E402
from gen3tl.build.graphics import (GraphicsConfig, GraphicsError, append_problem, apply_area_form,  # noqa: E402
                                   check_report, parse_area, pixel_check)
from gen3tl.check.entrycheck import entry_problems  # noqa: E402
from gen3tl.check.layout import Limit, Measure, SpriteMeasure, check, slot_rewraps, wrapped_dash_lines  # noqa: E402
from gen3tl.check.provenance import MESSAGES as TEMPLATES, NOTES  # noqa: E402
from gen3tl.check.render import PngImage, indexed_png  # noqa: E402
from gen3tl.core.charmap import Charmap  # noqa: E402
from gen3tl.lang.langpack import LangPack  # noqa: E402
from gen3tl.lang.termbase import Term, TermMatcher  # noqa: E402
from gen3tl.web import sitecatalog  # noqa: E402
from gen3tl.web.editorcheck import Checker  # noqa: E402
from gen3tl.web.editorgfx import Pictures, graphics_data, read_png  # noqa: E402
from project_fixture import select_language  # noqa: E402

WEB = TOOLKIT_ROOT / "web"
NODE = shutil.which("node")

KEYS = '''
import fs from "node:fs";
import { ENCODING, MESSAGES, NOTES } from "%s";
fs.writeFileSync(process.argv[3], JSON.stringify({ keys: [...MESSAGES, ...ENCODING], notes: NOTES }));
'''

PARSE_FINDINGS = '''
import fs from "node:fs";
import { FINDINGS, REPORT, parseFinding } from "%s";
const lines = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const parsed = lines.map((l) => parseFinding(l.replace(/^FAIL {5}/, ""))?.key ?? null);
fs.writeFileSync(process.argv[3], JSON.stringify({ keys: [...FINDINGS, ...REPORT], parsed }));
'''


def messages() -> list[dict]:
    """One problem of every kind the editor's check gives, from the functions that code them."""
    cm = LangPack.load("ru", "unbound").charmap()
    m = Measure(lambda b: 6, {"PLAYER": 42}, styles={"wide": lambda b: 12})
    out = []

    def entry(text, lim, english="Hi", **kw):
        out.extend(entry_problems(english, text, lim, m, cm, **kw)[1])

    for text in ("ж", "{AB", "a\\q", "{}", "{CLEAR_TO}", "{NOPE}"):
        out.extend(check(text, Limit("single", 100, None), m, Charmap()))
    for text, lim in (("абвг", Limit("field", 100, None, chars=3)), ("абвг", Limit("single", 100, None, bytes=2)),
                      ("абв", Limit("single", 100, None, head="Я")), ("а\\nб", Limit("single", 100, None)),
                      ("а\\nб\\nв", Limit("dialogue", 100, 2)), ("абв", Limit("single", 100, None, keys=2)),
                      ("абвгдежз", Limit("single", 30, None)),
                      ("абв {PLAYER}", Limit("single", 30, None, placeholders={"PLAYER": 42})),
                      ("абвгдежз{CLEAR_TO 05}и", Limit("single", 200, None)),
                      ("{PLAYER}абв{CLEAR_TO 05}и", Limit("single", 200, None, placeholders={"PLAYER": 42})),
                      ("Я: абвгдежз", Limit("single", 30, None, strip=["Я: "])), ("а\\nб", Limit("multiline", 100, 1))):
        out.extend(check(text, lim, m, cm))
    out.extend(wrapped_dash_lines("Привет.\\pаааа бббб - вв", "Привет.\\pаааа бббб\\n- вв"))
    out.extend(slot_rewraps("{PLAYER} а", Limit("dialogue", 60, 2, placeholders={"PLAYER": 100}), m, cm))
    out.extend(slot_rewraps("аааааа", Limit("dialogue", 60, 2, placeholders={"PLAYER": 42}, style="wide"), m, cm))
    entry("", Limit("single", 100, None))
    entry("абв", Limit("single", 100, None), english="{PLAYER}")
    entry("абвгдежз", Limit("single", 30, None, sources={"width": {"kind": "source"}}))
    entry("абвгдежз", Limit("single", 30, None, sources={"width": {"unverified": True}}))
    entry("абвгд\\nежзий", Limit("multiline", 30, 1, sources={"lines": {"kind": "source"}}))
    entry("абвгд", Limit("single", 100, None), windows=[{"font": "small", "width": 20, "where": "the bag"}],
          measures={"small": (m, cm)})
    entry("аааа\\nббббб ввввв\\nгг", Limit("multiline", 60, 3, source_lines=[3]))
    entry("Цель и\\nдом", Limit("multiline", 118, 4))
    entry("Да.\\pДай покемона\\n{COLOR 06}{STR_VAR_1}{COLOR 08}!", Limit("dialogue", 120, 2), english="Hi.\\pHo")
    entry("Взять ({STR_VAR_2}\\nшт.)?", Limit("dialogue", 120, 2))
    entry("аа\\nааааа ббб\\pвв", Limit("dialogue", 60, 2, arrow=12), english="Hi\\nho\\pyo")
    consumers = [{"name": "wide window", "width": 100, "arrow": 12},
                 {"name": "narrow window", "width": 30, "arrow": 12}]
    for text in ("абвгдежз", "абв {PLAYER}"):
        out.extend(check(text, Limit("single", 30, None, placeholders={"PLAYER": 42}, consumers=consumers), m, cm))
    entry("абвг\\pд", Limit("multiline", 30, 2, consumers=consumers), english="Hi\\pHo")
    entry("а\\pб", Limit("multiline", 100, 2, consumers=[
        {"name": "banner window", "width": 100, "arrow": 12, "instant_print": True}]), english="Hi\\pHo")
    entry("а...б", Limit("single", 100, None), forbidden={"\\.\\.\\.": "three dots"})
    centre = {"{CLEAR_TO}": {"window": 100, "x": 0}}
    entry("абв", Limit("single", 100, None), leading=centre)
    entry("{CLEAR_TO 01}абв", Limit("single", 100, None), leading=centre)
    term = Term("potion", "items", "Potion", "Зелье", label="", gender="", forms={}, address="", notes="",
                status="approved")
    entry("Ничего", Limit("single", 100, None), english="A Potion", matcher=TermMatcher([term]))
    pair = {"font": "hud", "width": 30, "where": "the HUD",
            "partner": {"id": "moves:1", "text": "Удар", "width": 24, "species": {"id": "species:1", "text": "Сил"}}}
    entry("абвгд", Limit("single", 100, None), measures={"hud": (SpriteMeasure(6, 256, range(256), shared=1), cm)},
          pair=pair)
    doc = {"language": "ru", "fonts": {"main": "main", "styles": {"main": {"pack": {}, "format": next(iter(FORMATS))}}},
           "placeholders": {"default": 0, "widths": {}}, "charmap": {}, "glossary": {}}
    checker = Checker(doc, {"main": bytes([6] * 256)})
    out.extend(p["message"] for p in checker.check({"id": "x", "source_text": "Hi", "window": None}, "Hi")["problems"])
    return list({json.dumps(p, sort_keys=True): p for p in out}.values())


def shown_codes(problem: dict) -> list[str]:
    """The codes a problem shows with a key of its own: its notes, the problem and its encoder error."""
    if problem["code"] in NOTES:
        return [problem["code"], *shown_codes(problem["message"])]
    error = problem.get("error")
    return [problem["code"], *(shown_codes(error) if isinstance(error, dict) else [])]


def findings() -> list[str]:
    """One finding of every kind, on the synthetic graphics ROM."""
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        rom = fixture(tmp)[0]
        cfg = GraphicsConfig({"inventory": "inv.tsv", "sheet_width": {"T1": 4}}, tmp)
        sheet = cfg.sheet(rom, cfg.graphic("T1"))
        g, pal = sheet.graphic, sheet.palette

        def indexed(px, palette=pal):
            return PngImage(len(px[0]), len(px), [[pal[v % len(pal)] for v in row] for row in px], list(palette), px)

        def rgb(px, at=None, colour=None):
            rows = [[pal[v] for v in row] for row in px]
            if at:
                rows[at[1]][at[0]] = colour
            return PngImage(len(rows[0]), len(rows), rows)

        same = [row[:] for row in sheet.pixels]
        areas = [(8, 8, 8, 8)]
        grown = same + [[0] * sheet.width for _ in range(8)]
        grown[32][8] = 1
        moved = [row[:] for row in same]
        moved[0][0] = (moved[0][0] + 1) % 16
        wide = [row[:] for row in same]
        wide[0][0] = len(pal)
        twice = dataclasses.replace(sheet, palette=list(pal[:15]) + [pal[14]])
        at = next((x, y) for y, row in enumerate(same) for x, v in enumerate(row) if v < 14)
        for check in (pixel_check(sheet, indexed(same), areas, g.tiles - 1),
                      pixel_check(sheet, indexed(same), [(30, 30, 8, 8)]),
                      pixel_check(sheet, indexed(same[:8]), areas),
                      pixel_check(sheet, indexed(same), areas, g.tiles + 1),
                      pixel_check(sheet, indexed(same, pal[:4]), areas),
                      pixel_check(sheet, indexed(same, [(1, 2, 3)] + list(pal[1:])), areas),
                      pixel_check(sheet, indexed(wide, list(pal) + [(9, 9, 9)]), areas),
                      pixel_check(sheet, rgb(same, (0, 0), (1, 2, 3)), areas),
                      pixel_check(twice, rgb(same, at, pal[14]), areas),
                      pixel_check(sheet, indexed(grown), areas, g.tiles + 1),
                      pixel_check(sheet, indexed(moved), areas)):
            out += check.findings[:1]
        out += apply_area_form(sheet, rgb(same), areas)[1]
        form = [[0] * sheet.width for _ in range(sheet.height)]
        form[2][30] = 4
        out += apply_area_form(sheet, indexed(form), areas)[1]
        out += [append_problem(cfg.graphic("T2"), 5), append_problem(g, g.tiles + 1, g.size + 32)]
        for fail in (lambda: cfg.graphic("T9"), lambda: cfg.graphic("T3"), lambda: parse_area("1,2,3")):
            try:
                fail()
            except GraphicsError as e:
                out.append(str(e))
        out += Pictures({"inventory": []}).check("T1", b"", "x.png", [])["findings"]
        out += Pictures(graphics_data({"inventory": "inv.tsv", "sheet_width": {"T1": 4}}, tmp),
                        bytes(rom.data)).edited("T1")["findings"]
        sig = indexed_png(same, pal, 4)[:8]

        def chunk(tag, body):
            return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))

        def header(depth, colour, interlace=0):
            return chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, depth, colour, 0, 0, interlace))
        for data in (b"GIF89a", sig + chunk(b"IEND", b""), sig + header(8, 2, 1), sig + header(8, 3),
                     sig + header(8, 0) + chunk(b"IDAT", zlib.compress(b"\x07\x00")), sig + chunk(b"IHDR", b"\x00")):
            try:
                read_png(data, "x.png")
            except ValueError as e:
                out.append(str(e))
        small = [row[:] for row in same]
        small[9][9] = (small[9][9] + 1) % 16
        out += check_report(cfg, rom, "T1", lambda: indexed(small), areas).lines
        (tmp / "literal").mkdir()
        loose = fixture(tmp / "literal", literal=True)[0]   # a literal block: the edit fits its slot
        out += check_report(GraphicsConfig({"inventory": "inv.tsv", "sheet_width": {"T1": 4}}, tmp / "literal"),
                            loose, "T1", lambda: indexed(small), areas).lines
        out += check_report(cfg, rom, "T1", lambda: indexed(grown), areas, g.tiles + 1).lines
        out += check_report(cfg, rom, "T1", lambda: indexed(same), areas, g.tiles - 1).lines
    return list(dict.fromkeys(out))


def node_parse(script: str, items: list[str]) -> dict:
    """`script` run in node over `items`: what it writes."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "parse.mjs").write_text(script % (WEB / "js" / "checktext.js").as_uri(), encoding="utf-8")
        (tmp / "in.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        run = subprocess.run([NODE, str(tmp / "parse.mjs"), str(tmp / "in.json"), str(tmp / "out.json")],
                             capture_output=True, text=True)
        if run.returncode:
            raise AssertionError(run.stderr)
        return json.loads((tmp / "out.json").read_text(encoding="utf-8"))


def setUpModule():
    select_language("ru")


@unittest.skipUnless(NODE, "no node")
class CheckMessagesTest(unittest.TestCase):
    def test_every_message_kind_has_a_key_and_a_russian_entry(self):
        out = node_parse(KEYS, [])
        used = set()
        for problem in messages():
            for code in shown_codes(problem):
                with self.subTest(code=code):
                    # the page shows a note's key after the problem, the others by their template
                    key = out["notes"][code] if code in NOTES else TEMPLATES[code]
                    self.assertEqual(TEMPLATES[code], f"{{message}} {key}" if code in NOTES else key)
                    self.assertIn(key, [*out["keys"], *out["notes"].values()], "a code the page has no key for")
                    used.add(key)
        keys = [*out["keys"], *out["notes"].values()]
        self.assertEqual(sorted(set(keys) - used), [], "a key no problem of the check has")
        self.assertEqual(len(keys), 44)
        self.assertEqual(sorted(out["notes"]), sorted(NOTES))
        ru = sitecatalog.strings(sitecatalog.load(sitecatalog.CATALOGS / "ru.po"))
        self.assertEqual([k for k in keys if k not in ru], [])

    def test_every_graphics_finding_has_a_key_and_a_russian_entry(self):
        found = findings()
        out = node_parse(PARSE_FINDINGS, found)
        self.assertEqual([f for f, key in zip(found, out["parsed"]) if key is None], [])
        self.assertEqual(sorted(set(out["keys"]) - set(out["parsed"])), [], "a key no finding of the check has")
        self.assertEqual(len(out["keys"]), 30)
        ru = sitecatalog.strings(sitecatalog.load(sitecatalog.CATALOGS / "ru.po"))
        self.assertEqual([k for k in out["keys"] if k not in ru], [])


if __name__ == "__main__":
    unittest.main()
