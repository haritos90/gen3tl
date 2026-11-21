import sys
import tempfile
import unittest
from pathlib import Path

from test_roots import TOOLKIT_ROOT

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.catalog.batchplan import plan, write as write_plan  # noqa: E402
from gen3tl.catalog.po import PoEntry, write  # noqa: E402


def entries(prefix: str, n: int, words: int = 5, done: int = 0) -> list[PoEntry]:
    return [PoEntry(f"{prefix}{i:04d}", " ".join(["word"] * words) + f" {prefix}{i}", "x" if i < done else "")
            for i in range(n)]


class PlanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        po = self.po = Path(self.tmp.name) / "po"
        write(po / "maps" / "town-a.po", entries("a", 200), {})          # split in two
        write(po / "maps" / "cave-b.po", entries("b", 10), {})           # small: merged
        write(po / "maps" / "route-c.po", entries("c", 70), {})
        write(po / "maps" / "home.po", entries("h", 40, done=5), {})    # first in the story
        write(po / "system" / "region-1.po",
              [PoEntry(f"s{i}", "Same text" if i % 3 == 0 else f"Text {i}") for i in range(30)], {})
        write(po / "tables" / "species.po", [PoEntry("species:0", "Bulbasaur"), PoEntry("species:1", "Missingno")], {})
        self.levels = {"town-a": 20.0, "cave-b": 25.0, "route-c": 26.0}

    def tearDown(self):
        self.tmp.cleanup()

    def run_plan(self, approved=None):
        return plan(self.po, self.levels, ["home"], {"species:0"}, approved or {}, size=150, min_size=60, max_words=10**6)

    def test_coverage_once(self):
        bs = self.run_plan({"pilot": ["a0000", "a0001"]})
        todo = [i for b in bs if b.kind != "done" for i in b.ids]
        self.assertEqual(len(todo), len(set(todo)))
        expected = 198 + 10 + 70 + 35 + 30 + 2
        self.assertEqual(len(todo), expected)
        self.assertNotIn("a0000", todo)
        self.assertEqual([b.name for b in bs if b.kind == "done"], ["pilot"])

    def test_order_split_merge(self):
        bs = [b for b in self.run_plan() if b.group == "maps"]
        names = [b.name for b in bs]
        self.assertEqual(names[0], "maps-home")                     # profile order first
        self.assertTrue(names[1].startswith("maps-town-a-1") and names[2].startswith("maps-town-a-2"))
        self.assertTrue(all(len(b.ids) <= 150 + 30 for b in bs))
        merged = next(b for b in bs if any("cave-b" in f for f in b.files))
        self.assertGreater(len(merged.files), 1)                    # the small cave rides with a neighbour

    def test_tables_fill_and_duplicates_together(self):
        bs = self.run_plan()
        fill = next(b for b in bs if b.kind == "fill")
        self.assertEqual(fill.ids, ["species:0"])
        sysb = [b for b in bs if b.group == "system"]
        holder = [b for b in sysb if "s0" in b.ids]
        self.assertEqual(len(holder), 1)
        self.assertTrue(all(f"s{i}" in holder[0].ids for i in range(0, 30, 3)))

    def test_active_batches_and_empty_entries_are_not_planned(self):
        write(self.po / "maps" / "cave-b.po", entries("b", 10) + [PoEntry("bempty", "s", "", flags=["empty"])], {})
        write(self.po / "tables" / "items.po", [PoEntry("items:54", "", "")], {})   # an unused slot
        bs = plan(self.po, self.levels, ["home"], {"species:0"}, {}, size=150, min_size=60, max_words=10**6,
                  active={"maps-x-1": ["a0005", "a0006"]})
        todo = [i for b in bs if b.kind not in ("done", "active") for i in b.ids]
        self.assertNotIn("a0005", todo)
        self.assertNotIn("bempty", todo)
        self.assertNotIn("items:54", todo)
        self.assertEqual([b.name for b in bs if b.kind == "active"], ["maps-x-1"])

    def test_deferred_entries_are_listed_and_not_planned(self):
        # unreachable entries wait in one deferred batch per group
        bs = plan(self.po, self.levels, ["home"], {"species:0"}, {}, size=150, min_size=60, max_words=10**6,
                  deferred={"a0003", "a0004"})
        todo = [i for b in bs if b.kind not in ("done", "active", "deferred") for i in b.ids]
        self.assertNotIn("a0003", todo)
        self.assertNotIn("a0004", todo)
        held = [b for b in bs if b.kind == "deferred"]
        self.assertEqual([(b.name, b.ids) for b in held], [("deferred-maps", ["a0003", "a0004"])])
        self.assertIsNone(held[0].prefill)

    def test_deferred_system_entries(self):
        # unreachable system strings wait too
        bs = plan(self.po, self.levels, ["home"], {"species:0"}, {}, size=150, min_size=60, max_words=10**6,
                  deferred={"s1", "s2"})
        todo = [i for b in bs if b.kind not in ("done", "active", "deferred") for i in b.ids]
        self.assertFalse({"s1", "s2"} & set(todo))
        self.assertEqual([(b.name, b.ids) for b in bs if b.kind == "deferred"], [("deferred-system", ["s1", "s2"])])

    def test_names_skip_exported_batches(self):
        # town-a splits in two.
        # its first part belongs to an exported batch.
        bs = plan(self.po, self.levels, ["home"], {"species:0"}, {"maps-home": []}, size=150, min_size=60,
                  max_words=10**6, active={"maps-town-a-1": ["a0000"]})
        names = [b.name for b in bs if b.kind not in ("done", "active")]
        self.assertEqual(len(names), len(set(names)))
        self.assertNotIn("maps-town-a-1", names)
        self.assertNotIn("maps-home", names)
        self.assertIn("maps-town-a-2", names)
        self.assertIn("maps-town-a-3", names)
        self.assertIn("maps-home-1", names)                         # a single part past a taken name

    def test_prefill_count(self):
        # "Same text" matches an approved entry.
        # "Text 1" matches only a fuzzy one.
        write(self.po / "system" / "region-2.po",
              [PoEntry("t0", "Same text", "Тот же текст"), PoEntry("t1", "Text 1", "Текст 1", flags=["fuzzy"])], {})
        bs = plan(self.po, self.levels, ["home"], {"species:0"}, {"pilot": ["a0000"]}, size=150, min_size=60,
                  max_words=10**6, active={"maps-x-1": ["a0005"]})
        sysb = [b for b in bs if b.group == "system"]
        self.assertEqual([b.prefill for b in sysb], [10])
        self.assertTrue(all(b.prefill == 0 for b in bs if b.group == "maps"))
        self.assertEqual([b.prefill for b in bs if b.kind in ("done", "active")], [None, None])
        tsv, js = write_plan(bs, Path(self.tmp.name) / "batches")
        rows = [r.split("\t") for r in tsv.read_text(encoding="utf-8").splitlines()]
        col = rows[0].index("prefill")
        self.assertEqual(rows[0][col - 1], "entries")
        self.assertEqual(next(r for r in rows if r[1] == sysb[0].name)[col], "10")
        self.assertEqual(next(r for r in rows if r[1] == "pilot")[col], "")


if __name__ == "__main__":
    unittest.main()
