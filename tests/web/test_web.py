import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_roots import SUPPORT_ROOT, TOOLKIT_ROOT
from gen3tl.core.charmap import Charmap
from gen3tl.web.editordata import codec_data
from gen3tl.check.layout import Limit, Measure, entry_measure, line_width, lines_of, paragraphs

ROOT = TOOLKIT_ROOT

WEB = ROOT / "web"

NODE = shutil.which("node")

def have_pyodide() -> bool:
    """Node with the pyodide package (NODE_PATH may name its node_modules)."""
    return NODE is not None and subprocess.run([NODE, "-e", "require.resolve('pyodide')"],
                                               capture_output=True).returncode == 0

ENTRY_WIDTHS = '''
import fs from "node:fs";
import { Codec, entryMeasure } from "%s";
const inp = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const index = { fonts: { main: "main" }, placeholders: { widths: inp.ph, default: inp.fallback } };
const glyphs = { main: { width: (b) => inp.widths[b] } };
const codec = new Codec(inp.codec);
const out = inp.spacings.map((icon_spacing) => {
  const m = entryMeasure(index, glyphs, codec, { icon_spacing });
  return inp.lines.map((line) => m.lineWidth(line));
});
fs.writeFileSync(process.argv[3], JSON.stringify(out));
'''

CHECK = '''
import fs from "node:fs";
import { createRequire } from "node:module";
import { Check, startPython } from "%s";
const require = createRequire(import.meta.url);
const { loadPyodide } = require("pyodide");
const [input, out] = process.argv.slice(2);
const inp = JSON.parse(fs.readFileSync(input, "utf8"));
const check = new Check(await startPython(() => loadPyodide()), new Uint8Array(fs.readFileSync(inp.bundle)));
const latin = Object.fromEntries(Object.entries(inp.latin).map(([s, w]) => [s, new Uint8Array(w)]));
const cells = Object.fromEntries(Object.entries(inp.cells).map(([s, c]) => [s, new Uint8Array(c)]));
check.configure(inp.index);
const results = { approx: inp.items.map((x) => check.run(x.entry, x.text)) };
check.configure(inp.index, latin, cells);
results.exact = inp.items.map((x) => check.run(x.entry, x.text));
fs.writeFileSync(out, JSON.stringify(results));
'''

@unittest.skipUnless(NODE, "no node")
class NodeTest(unittest.TestCase):
    def test_node_tests(self):
        run = subprocess.run([NODE, "--test", str(WEB / "test")], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(run.returncode, 0, run.stdout[-3000:] + run.stderr[-2000:])
        self.assertRegex(run.stdout, r"# fail 0|ℹ fail 0")


    def test_key_icon_spacing_matches_layout(self):
        cm = Charmap()
        widths = [(b * 7) % 9 + 2 for b in range(512)]
        ph = {"F8 00": 8, "F8 02": 12, "F8 03": 16, "PLAYER": 42}
        m = Measure(lambda b: widths[b], ph, 60)
        texts = ["Ab", "{PLAYER}", "{F8 00}", "{F8 02}", "{F8 03}",
                 "A{F8 00}b", "{F8 02}Ab", "Ab{F8 03}", "{F8 00}{F8 02}{F8 03}",
                 "A{F8 00}\\n{F8 02}b{F8 03}", "{PLAYER}{F8 00}A"]
        lines = [l for t in texts for p in paragraphs(t) for l in lines_of(p)]
        spacings = [0, 1, 2]
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "widths.mjs").write_text(ENTRY_WIDTHS % (WEB / "js" / "font.js").as_uri(), encoding="utf-8")
            (tmp / "in.json").write_text(json.dumps({
                "codec": codec_data(), "widths": widths, "ph": ph, "fallback": 60,
                "lines": lines, "spacings": spacings}), encoding="utf-8")
            run = subprocess.run([NODE, str(tmp / "widths.mjs"), str(tmp / "in.json"), str(tmp / "out.json")],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            got = json.loads((tmp / "out.json").read_text(encoding="utf-8"))
        self.assertEqual(len(got), len(spacings))
        for spacing, actual in zip(spacings, got):
            me = entry_measure(m, Limit("dialogue", 208, 2, icon_spacing=spacing))
            self.assertEqual(len(actual), len(lines))
            for line, browser_width in zip(lines, actual):
                with self.subTest(icon_spacing=spacing, line=line):
                    python_width = line_width(line, me, cm)
                    self.assertEqual(browser_width, python_width)
                    self.assertEqual(python_width, line_width(line, m, cm) + spacing * line.count("{F8 "))

def fallback_width(style: dict) -> int:
    """font.js fallbackWidth: the most common pack width, the smaller on a tie."""
    from collections import Counter
    n = Counter(style["pack"].values())
    return min(n, key=lambda px: (-n[px], px)) if n else 6

@unittest.skipUnless(have_pyodide(), "no node with the pyodide package (set NODE_PATH to its node_modules)")
class PageCheckTest(unittest.TestCase):
    """check.js in Pyodide gives the Python Checker's result, without and with ROM widths."""

    def test_same_result(self):
        sys.path.insert(0, str(SUPPORT_ROOT))
        import editordata_support as fx
        from gen3tl.web.editorcheck import Checker, write_bundle
        from gen3tl.web.editordata import glyph_art, split

        class Doc:   # the synthetic document of editordata_support
            pass
        fx.EditorDataTest.setUpClass.__func__(Doc)
        index, _ = split(Doc.doc, "1.2.3", glyph_art(fx.PROF, Doc.packs))
        del index["list"]
        rules = {"optional_tokens": ["{PAUSE 18}"], "forbidden": {" - ": "spaced hyphen"}}
        index["check_rules"] = rules   # check.js reads the rules from the index
        latin = {s: [(b * 5) % 7 + 3 for b in range(512)] for s in index["fonts"]["styles"]}
        # ROM glyph cells: even bytes all ink, odd bytes none
        from gen3tl.build.font import glyph_size
        cells = {}
        for s, f in index["fonts"]["styles"].items():
            n = glyph_size(f["format"])
            cells[s] = [0x55 if (i // n) % 2 == 0 else 0 for i in range(512 * n)]
        items = []
        for e in Doc.doc["entries"]:
            for text in (e["translation"], e["source_text"], (e["translation"] + " - ") * 4 + "Ж\\nб @VERSION@", "Ӂ"):
                items.append({"entry": e, "text": text})
        # each text 1 px wider than a one-line window
        exact = Checker(index, {s: bytes(w) for s, w in latin.items()}, rules, "1.2.3",
                        {s: bytes(c) for s, c in cells.items()})
        over = []
        for text in ("Зелье", "Сумка", "Ивизавр", "дом", "Привет Q", "Привет b", "Ж"):
            w = exact.check({"id": "s0000900", "source_text": "x", "window": None}, text)["lines"][0][0]["width"]
            over.append({"entry": {"id": "s0000900", "source_text": "x", "window": {"kind": "single", "width": w - 1,
                                                                                "lines": 1}}, "text": text})
        self.assertEqual({not exact.check(x["entry"], x["text"])["problems"] for x in over}, {True, False})
        no_cells = Checker(index, {s: bytes(w) for s, w in latin.items()}, rules, "1.2.3")
        self.assertNotEqual([exact.check(x["entry"], x["text"])["problems"] for x in over],
                            [no_cells.check(x["entry"], x["text"])["problems"] for x in over])
        items += over
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_bundle(tmp / "check.zip", rules)
            (tmp / "check.mjs").write_text(CHECK % (WEB / "js" / "check.js").as_uri(), encoding="utf-8")
            (tmp / "in.json").write_text(json.dumps({"index": index, "bundle": str(tmp / "check.zip"), "items": items,
                                                     "latin": latin, "cells": cells}, ensure_ascii=False),
                                         encoding="utf-8")
            run = subprocess.run([NODE, str(tmp / "check.mjs"), str(tmp / "in.json"), str(tmp / "out.json")],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr[-3000:])
            got = json.loads((tmp / "out.json").read_text(encoding="utf-8"))
        fallback = {s: bytes([fallback_width(f)] * max(f["rom"]["count"], 256))
                    for s, f in index["fonts"]["styles"].items()}
        glyphs = {s: bytes(c) for s, c in cells.items()}
        for key, widths, exact, cs in (("approx", fallback, False, None),
                                       ("exact", {s: bytes(w) for s, w in latin.items()}, True, glyphs)):
            checker = Checker(index, widths, rules, "1.2.3", cs)
            self.assertEqual(len(got[key]), len(items))
            for x, res in zip(items, got[key]):
                self.assertEqual({**checker.check(x["entry"], x["text"]), "exact": exact}, res, x["entry"]["id"])
        # the checks found something to compare
        self.assertTrue(any(r["problems"] for r in got["exact"]))
        self.assertNotEqual([r["lines"] for r in got["approx"]], [r["lines"] for r in got["exact"]])

RENAME = '''
import fs from "node:fs";
import { createRequire } from "node:module";
import { Check, startPython } from "%s";
const require = createRequire(import.meta.url);
const { loadPyodide } = require("pyodide");
const [input, out] = process.argv.slice(2);
const inp = JSON.parse(fs.readFileSync(input, "utf8"));
const check = new Check(await startPython(() => loadPyodide()), new Uint8Array(fs.readFileSync(inp.bundle)));
check.configure(inp.index);
fs.writeFileSync(out, JSON.stringify(inp.cases.map((c) => check.rename(c.old, c.new, inp.items, inp.slots,
  c.scope ?? null))));
'''

@unittest.skipUnless(have_pyodide(), "no node with the pyodide package (set NODE_PATH to its node_modules)")
class PageRenameTest(unittest.TestCase):
    """check.js's rename preview in Pyodide gives termedit.preview's report."""

    def test_same_report(self):
        sys.path.insert(0, str(SUPPORT_ROOT))
        import editordata_support as fx
        from gen3tl.lang import termedit
        from gen3tl.web.editorcheck import Checker, write_bundle
        from gen3tl.web.editordata import glyph_art, split, terms_data

        class Doc:
            pass
        fx.EditorDataTest.setUpClass.__func__(Doc)
        data = terms_data(fx.TERMS, Doc.doc, Doc.catalog)
        index, _ = split(Doc.doc, "1.2.3", glyph_art(fx.PROF, Doc.packs), {}, data)
        del index["list"]
        potion, bag = data["terms"][0], data["terms"][1]
        long = "Очень длинное целебное зелье здоровья"
        cases = [{"old": potion, "new": {**potion, "translation": long, "forms": {"nomn.sing": long}}},
                 {"old": bag, "new": {**bag, "gender": "male"}},
                 {"old": potion, "new": {**potion, "notes": "x"}}]
        # a form-only occurrence, left out and included
        drug = {**potion, "translation": "Снадобье", "forms": {"nomn.sing": "Снадобье", "gent.sing": "Снадобья"}}
        cases += [{"old": potion, "new": drug, "scope": {"rows": [], "include": []}},
                  {"old": potion, "new": drug, "scope": {"rows": [], "include": ["s0000500"]}}]
        items = [{"entry": e, "text": "Нет зелья, только Зелье" if e["id"] == "s0000500" else e["translation"]}
                 for e in Doc.doc["entries"]]
        rules = {"optional_tokens": [], "forbidden": {}}
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_bundle(tmp / "check.zip", rules)
            (tmp / "rename.mjs").write_text(RENAME % (WEB / "js" / "check.js").as_uri(), encoding="utf-8")
            (tmp / "in.json").write_text(json.dumps({"index": index, "bundle": str(tmp / "check.zip"), "cases": cases,
                                                     "items": items, "slots": data["slots"]}, ensure_ascii=False),
                                         encoding="utf-8")
            run = subprocess.run([NODE, str(tmp / "rename.mjs"), str(tmp / "in.json"), str(tmp / "out.json")],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr[-3000:])
            got = json.loads((tmp / "out.json").read_text(encoding="utf-8"))
        fallback = {s: bytes([fallback_width(f)] * max(f["rom"]["count"], 256))
                    for s, f in index["fonts"]["styles"].items()}
        checker = Checker(index, fallback, rules, "1.2.3")
        want = [termedit.preview(checker, c["old"], c["new"], items, data["slots"], c.get("scope")) for c in cases]
        self.assertEqual(got, [None if w is None else {**w, "exact": False} for w in want])
        self.assertTrue(want[0]["summary"]["blocked"])   # the report found something to compare
        self.assertIsNone(want[2])
        after = [{e["id"]: e["after"] for e in w["entries"]}["s0000500"] for w in want[3:]]
        self.assertEqual(after, ["Нет зелья, только Снадобье", "Нет Снадобья, только Снадобье"])
