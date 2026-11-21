import copy
import sys
import tempfile
import unittest
from pathlib import Path

from test_roots import TOOLKIT_ROOT

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.catalog.batchplan import plan
from gen3tl.catalog.catalog import export, load_catalog
from gen3tl.core.charmap import Charmap
from gen3tl.build.inject import inject
from gen3tl.discover.inventory import build
from gen3tl.catalog.po import PoEntry, read, write
from gen3tl.profile.profile import Profile
from gen3tl.core.rom import Rom, ptr_bytes


CM = Charmap()


class NonTextTest(unittest.TestCase):
    def fixture(self):
        data = bytearray(b"\xff" * 0x400)
        data[0x100:0x108] = b"\x00" * 8
        text = CM.encode("Hello!")
        data[0x108:0x108 + len(text)] = text
        data[0x20:0x24] = ptr_bytes(0x100)
        data[0x24:0x28] = ptr_bytes(0x108)
        data[0x30:0x36] = b"\x0f\x00" + ptr_bytes(0x108)
        prof = Profile({"strings": {"include": [0x100, 0x108]}}, Path("test.toml"))
        return prof, Rom(data)

    def test_excluded_border_preserves_adjacent_text_and_bytes(self):
        prof, rom = self.fixture()
        before = build(prof, rom)
        old = {e["id"]: e for e in before["entries"]}
        self.assertIn("overlap", old["s0000108"]["flags"])
        prof.data["strings"]["exclude"] = ["s0000100", "s0000300"]
        after = build(prof, rom)
        entries = {e["id"]: e for e in after["entries"]}
        self.assertNotIn("s0000100", entries)
        self.assertEqual(after["excluded"], ["s0000100", "s0000300"])
        self.assertNotIn("overlap", entries["s0000108"]["flags"])
        self.assertEqual(entries["s0000108"]["text"], "Hello!")
        out = rom.copy()
        result = inject(out, rom, after, {"s0000100": b"\xff"}, {})
        self.assertEqual(out.data, rom.data)
        self.assertEqual(result.skipped, [("s0000100", "not in inventory")])
        inject(out, rom, after, {"s0000108": CM.encode("Hi!")}, {})
        self.assertEqual(out.read(0x100, 8), rom.read(0x100, 8))
        self.assertEqual(out.ptr_at(0x20), 0x100)
        self.assertEqual(out.read(0x108, 4), CM.encode("Hi!"))

    def test_export_removes_border_group_but_keeps_real_dialogue(self):
        prof, rom = self.fixture()
        inv = build(prof, rom)
        for e in inv["entries"]:
            if e["id"] == "s0000100":
                e.update(id="s07548F8", offset=0x7548F8)
            elif e["id"] == "s0000108":
                e.update(id="s0754900", offset=0x754900)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export(inv, {}, CM, lambda b: 6, root, "test")
            false_file = load_catalog(root)["s07548F8"][0]
            candidate = copy.deepcopy(inv)
            candidate["entries"] = [e for e in candidate["entries"] if e["id"] != "s07548F8"]
            candidate["excluded"] = ["s07548F8"]
            export(candidate, {}, CM, lambda b: 6, root, "test")
            cat = load_catalog(root)
            self.assertNotIn("s07548F8", cat)
            self.assertIn("s0754900", cat)
            self.assertFalse(false_file.exists())
            planned = {sid for batch in plan(root, {}, [], set(), {}) for sid in batch.ids}
            self.assertNotIn("s07548F8", planned)
            self.assertIn("s0754900", planned)

    def test_mixed_obsolete_file_keeps_unlisted_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "system" / "old.po"
            keep = PoEntry("s0000200", "Unknown", "Preserved", flags=["fuzzy"],
                           translator=["Existing note"], extracted=["Existing context"])
            write(path, [PoEntry("s0000100", "Binary"), keep], {"Language": "ru"})
            stats = export({"entries": [], "excluded": ["s0000100"]}, {}, CM,
                           lambda b: 6, root, "test")
            header, entries = read(path)
            self.assertEqual(header["Language"], "ru")
            self.assertEqual(entries, [keep])
            self.assertEqual(stats["stale_files_kept"], [str(path)])

    def test_moved_and_excluded_old_file_is_removed(self):
        prof, rom = self.fixture()
        inv = build(prof, rom)
        inv["entries"] = [e for e in inv["entries"] if e["id"] == "s0000108"]
        inv["excluded"] = ["s0000100"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "system" / "old.po"
            write(old, [PoEntry("s0000100", "Binary"),
                        PoEntry("s0000108", "Hello!", "Approved")], {})
            export(inv, {}, CM, lambda b: 6, root, "test")
            self.assertFalse(old.exists())
            self.assertEqual(load_catalog(root)["s0000108"][1].msgstr, "Approved")

    def test_plan_filters_exclusions_without_rewriting_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root / "system" / "old.po", [PoEntry("s0000100", "Binary"),
                                              PoEntry("s0000108", "Real dialogue")], {})
            approved = {"old": ["s0000100", "unlisted-history"]}
            active = {"review": ["s0000100", "unlisted-active"]}
            original = copy.deepcopy((approved, active))
            batches = plan(root, {}, [], set(), approved, active=active, excluded={"s0000100"})
            self.assertEqual(next(b.ids for b in batches if b.name == "old"), ["unlisted-history"])
            self.assertEqual(next(b.ids for b in batches if b.name == "review"), ["unlisted-active"])
            ids = {sid for b in batches for sid in b.ids}
            self.assertNotIn("s0000100", ids)
            self.assertIn("s0000108", ids)
            self.assertEqual((approved, active), original)


if __name__ == "__main__":
    unittest.main()
