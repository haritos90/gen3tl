import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

from test_roots import TOOLKIT_ROOT

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.catalog import progress as pg  # noqa: E402
from gen3tl.catalog.catalog import load_catalog  # noqa: E402
from gen3tl.catalog.po import PoEntry, write  # noqa: E402
from gen3tl.web import editordata  # noqa: E402
from project_fixture import workspace_project  # noqa: E402


class ProgressTest(unittest.TestCase):
    def test_topics_and_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "po"
            (root / "maps").mkdir(parents=True)
            (root / "tables").mkdir()
            (root / "system").mkdir()
            write(root / "maps" / "town.po", [
                PoEntry("s1", "Hello there!", "Привет!"),                           # approved
                PoEntry("s2", "Go away now.", "Уходи.", flags=["fuzzy"]),           # fuzzy, in a batch
                PoEntry("s3", "Not yet.", "")], {})                                  # untouched
            write(root / "tables" / "species.po", [PoEntry("species:1", "Bulbasaur", "Бульбазавр")], {})
            write(root / "tables" / "items.po", [PoEntry("items:54", "", "")], {})   # an unused slot: left out
            write(root / "system" / "table-1A357DC.po", [PoEntry("s4", "A seed on its back.", "")], {})
            write(root / "system" / "region-0410000.po", [
                PoEntry("s5", "Known Moves", "", extracted=["category: code"]),
                PoEntry("s6", " Pokémon", "", flags=["empty"], extracted=["category: data-table"])], {})
            batches = Path(tmp) / "batches"
            (batches / "b1").mkdir(parents=True)
            (batches / "b1" / "source.json").write_text(json.dumps([{"id": "s2"}, {"id": "s3"}]))
            (batches / "b1" / "status.json").write_text(json.dumps({"approved": False}))
            (batches / "b0").mkdir()
            (batches / "b0" / "source.json").write_text(json.dumps([{"id": "s1"}]))
            (batches / "b0" / "status.json").write_text(json.dumps({"approved": True}))
            files = {"table-1A357DC": "Pokedex entries"}
            rows = {r.topic: r for r in pg.count(root, files, pg.in_review(batches))}
            m = rows["Map dialogue"]
            self.assertEqual((m.approved, m.review, m.total, m.words, m.words_approved), (1, 2, 3, 7, 2))
            self.assertEqual((rows["Species names"].approved, rows["Species names"].total), (1, 1))
            self.assertEqual((rows["Item names"].approved, rows["Item names"].total), (0, 0))
            self.assertEqual((rows["Pokedex entries"].approved, rows["Pokedex entries"].total), (0, 1))
            self.assertEqual(rows[pg.INTERFACE].total, 1)
            self.assertEqual(rows[pg.OTHER].approved, 1)          # an intended empty translation counts
            text = pg.table(list(rows.values()))
            self.assertIn("| **Overall** | 3 | 2 | 7 | 42% |", text)
            drawn = pg.draw(*pg.cells(list(rows.values())))
            lines = drawn.splitlines()
            self.assertTrue(lines[0].startswith("┌") and lines[-1].startswith("└"))
            self.assertEqual(len({len(line) for line in lines}), 1)

    def test_never_shown_left_out(self):
        """Never-shown entries leave every count and get a column."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "po"
            (root / "maps").mkdir(parents=True)
            tag = ["reach: never shown (no map starts the script)"]
            write(root / "maps" / "town.po", [
                PoEntry("s1", "Hello there!", "Привет!"),                           # approved
                PoEntry("s2", "Not yet.", ""),                                        # untouched
                PoEntry("s3", "Old line.", "Старая строка.", extracted=tag),          # tagged, approved
                PoEntry("s4", "Dead line here.", "", extracted=tag)], {})            # tagged, untouched
            batches = Path(tmp) / "batches"
            batches.mkdir()
            rows = {r.topic: r for r in pg.count(root, {}, pg.in_review(batches))}
            m = rows["Map dialogue"]
            self.assertEqual((m.approved, m.total, m.words, m.words_approved, m.never), (1, 2, 4, 2, 2))
            headers, body, overall = pg.cells(list(rows.values()))
            self.assertEqual(headers[-1], "Never shown")
            self.assertEqual(overall, ["Overall", "1", "0", "2", "50%", "50%", "2"])
            self.assertIn("| **Overall** | 1 | 0 | 2 | 50% | 50% | 2 |", pg.table(list(rows.values())))

    def test_measure_agrees_with_the_editor_data(self):
        """Shown entries with English count; never shown and empty slots stay out."""
        study = {"strings": {"s0000500": {"class": "unreachable", "group": "script", "evidence": []},
                             "s0000600": {"class": "possibly reachable", "group": "script", "evidence": []}}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "po"
            write(root / "maps" / "town.po", [
                PoEntry("s0000100", "Hello there!", "Привет!"),                     # approved
                PoEntry("s0000200", "Not yet.", ""),                                  # untranslated
                PoEntry("s0000300", " Pokémon", "", flags=["empty"]),                # empty on purpose: done
                PoEntry("s0000400", "Dead line.", "",
                        extracted=["reach: never shown (review: no caller)"])], {})
            write(root / "script" / "region-0010000.po", [
                PoEntry("s0000500", "Old line.", ""),                                 # unreachable, untagged
                PoEntry("s0000600", "Maybe shown.", "Может быть.")], {})             # possibly reachable
            write(root / "tables" / "items.po", [PoEntry("items:54", "", "")], {})   # nothing to translate
            headers, body, overall = pg.cells(pg.count(root, {}, set(), study))
            self.assertEqual(overall, ["Overall", "3", "0", "4", "75%", "71%", "2"])
            self.assertEqual(pg.cells(pg.count(root, {}, set()))[2][3:], ["5", "60%", "55%", "1"])   # tags only
            rows = [{"status": editordata.status(e), "reach": editordata.reach_data(sid, e.extracted, study)["class"]}
                    for sid, (_, e) in load_catalog(root).items()]
            progress = editordata.progress_of(rows)
        self.assertEqual(progress, {"approved": 3, "never shown": 2, "nothing": 1, "untranslated": 1})
        measured = sum(progress.values()) - progress["never shown"] - progress["nothing"]   # landing.measured
        self.assertEqual((progress["approved"], measured, progress["never shown"]),
                         tuple(int(x) for x in (overall[1], overall[3], overall[6])))

    def test_graphics_row(self):
        """Translate-category graphics count, done once the profile edits them."""
        with tempfile.TemporaryDirectory() as tmp:
            inv = Path(tmp) / "inventory.tsv"
            inv.write_text("id\tscreen\tsource_text\tcategory\n"
                           "G01\ta\tLOGO\tkeep\nG02\tb\tHP\ttranslate\nG03\tc\tSTART\ttranslate\n"
                           "G04\td\tMART\toptional\n", encoding="utf-8")
            prof = {"graphics": {"inventory": str(inv), "edit": [{"id": "G02"}, {"id": "G01"}]}}
            g = pg.graphics_row(prof)
            self.assertEqual((g.topic, g.approved, g.total, g.words), (pg.GRAPHICS, 1, 2, 0))
            headers, body, overall = pg.cells([g])
            self.assertEqual(overall[1:5], ["1", "0", "2", "50%"])
            self.assertIsNone(pg.graphics_row({}))

    def test_graphics_row_counts_a_shared_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            inv = Path(tmp) / "inventory.tsv"
            inv.write_text("id\tscreen\tsource_text\tcategory\trom_offset\tcompressed\tsize_bytes\ttiles\n"
                           "G01\ta\tCREDIT\ttranslate\t0x1000\tno\t64\t2\n"
                           "G02\tb\tPAYOUT\ttranslate\t0x1000\tno\t64\t2\n"
                           "G03\tc\tOTHER\ttranslate\t0x2000\tno\t64\t2\n", encoding="utf-8")
            prof = {"graphics": {"inventory": str(inv), "edit": [{"id": "G01"}]}}
            g = pg.graphics_row(prof)
            self.assertEqual((g.approved, g.total), (2, 3))

    def test_command_reads_the_study_and_lists_the_graphics_apart(self):
        import contextlib
        import io

        from gen3tl.__main__ import main
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = workspace_project(root)
            inv = root / "inventory.tsv"
            inv.write_text("id\tscreen\tsource_text\tcategory\nG01\ta\tHP\ttranslate\n", encoding="utf-8")
            profile.write_text(profile.read_text(encoding="utf-8") + f'\n[graphics]\ninventory = "{inv}"\n',
                               encoding="utf-8")
            write(root / "po" / "maps" / "town.po", [PoEntry("s0000100", "Hello there!", "Привет!"),
                                                     PoEntry("s0000200", "Old line.", "")],
                  {"Project-Id-Version": "game", "Language": "ru"})
            (root / "build").mkdir()
            (root / "build" / "reach.json").write_text(json.dumps(
                {"profile": "game", "rom_sha1": "0" * 40,
                 "strings": {"s0000200": {"class": "unreachable", "group": "script", "evidence": []}}}))
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main(["--project-root", tmp, "--profile", str(profile), "catalog", "progress",
                                       "--po-root", str(root / "po"), "--full"]), 0)
        lines = out.getvalue().splitlines()
        self.assertEqual([re.split(r" {2,}", row) for row in lines], [
            ["topic", "approved (entries)", "translated or in review (entries)", "total (entries)", "done (%)", "words (%)",
             "never (entries)"],
            ["Map dialogue", "1", "0", "1", "100", "100", "1"],
            ["Overall", "1", "0", "1", "100", "100", "1"],
            ["Graphics with text", "0", "0", "1", "0", "-", "0"], ["status", "ready"]])
        column = lines[0].index("approved")
        self.assertTrue(all(row[column:].startswith(("0", "1")) for row in lines[1:-1]))

    def test_command_short_empty_catalog_and_markdown_output(self):
        import contextlib
        import io

        from gen3tl.__main__ import main
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = workspace_project(root)
            args = ["--project-root", tmp, "--profile", str(profile), "catalog", "progress",
                    "--po-root", str(root / "po")]
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main(args), 0)
            self.assertEqual(out.getvalue(), "topic    done (%)\nOverall  -\nstatus       ready\n")
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main([*args, "--markdown"]), 0)
            report = root / "build/progress.md"
            self.assertEqual(out.getvalue(), f"report       {report.resolve()}\nstatus       ready\n")
            self.assertIn("| **Overall** | - |", report.read_text())

    def test_short_columns(self):
        rows = [pg.Row("Map dialogue", total=4, approved=2, words=8, words_approved=4)]
        headers, body, overall = pg.short(*pg.cells(rows))
        self.assertEqual(headers, ["Topic", "Done"])
        self.assertEqual(body, [["Map dialogue", "50%"]])
        self.assertEqual(overall, ["Overall", "50%"])
        self.assertEqual(len(pg.cells(rows)[0]), 7)

    def test_review_uses_catalog_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root / "maps/town.po", [
                PoEntry("draft", "Draft", "Черновик", flags=["fuzzy"]),
                PoEntry("missing", "Missing", "")], {})
            rows = pg.count(root, {})
            self.assertEqual((rows[0].total, rows[0].approved, rows[0].review), (2, 0, 1))


if __name__ == "__main__":
    unittest.main()
