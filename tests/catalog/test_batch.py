import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from test_roots import TOOLKIT_ROOT

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from project_fixture import write_policy
from gen3tl.catalog.batch import PREFILL_HEADING, approve_batch, export_batch, import_batch, review_state, translated_text  # noqa: E402
from gen3tl.__main__ import main  # noqa: E402
from gen3tl.catalog.catalog import load_catalog  # noqa: E402
from gen3tl.catalog.po import PoEntry, write  # noqa: E402


class BatchTest(unittest.TestCase):
    def test_explicit_states_replace_text_and_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            po, root = tmp / "po", tmp / "batches"
            write(po / "one.po", [PoEntry("ending", "ending", "old")], {})
            directory = export_batch("states", ["ending"], po, {}, root=root)
            for state, text in (("empty", ""), ("original", ""), ("text", "new"), ("empty", "")):
                with self.subTest(state=state):
                    (directory / "translation.json").write_text(json.dumps(
                        {"ending": {"translation": text, "state": state}}))
                    self.assertEqual(import_batch("states", po, root)["changed"], 1)
                    entry = load_catalog(po)["ending"][1]
                    self.assertEqual((entry.msgstr, entry.state, entry.fuzzy), (text, state, True))
                    self.assertEqual(import_batch("states", po, root)["changed"], 0)

    def test_invalid_states_are_checked_before_any_catalog_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            po, root = tmp / "po", tmp / "batches"
            for name in ("first", "last"):
                write(po / f"{name}.po", [PoEntry(name, name, "old")], {})
            directory = export_batch("invalid", ["first", "last"], po, {}, root=root)
            for value in ({"state": "text", "translation": ""}, {"state": "empty", "translation": "x"},
                          {"state": "original", "translation": "x"}, {"state": "off", "translation": ""},
                          {"state": "original"}, [], 1):
                (directory / "translation.json").write_text(json.dumps({"first": "new", "last": value}))
                with self.subTest(value=value), self.assertRaises(ValueError):
                    import_batch("invalid", po, root)
                self.assertTrue(all(e.msgstr == "old" for _, e in load_catalog(po).values()))

    def test_review_text_distinguishes_inherited_off_and_open_drafts(self):
        for value, expected in ((None, None), ("", ""), ("custom", "custom"),
                                ({"translation": "", "state": "empty"}, ""),
                                ({"translation": "", "state": "original"}, "source")):
            with self.subTest(value=value):
                self.assertEqual(translated_text(value, "source"), expected)

    def test_approval_targets_only_the_selected_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            original, target = tmp / "translation/po", tmp / "scratch/po"
            root = tmp / "batches"
            for directory in (original, target):
                write(directory / "maps/town.po", [PoEntry("s1", "Hello", "Text", flags=["fuzzy"])], {})
            batch = export_batch("b1", ["s1"], original, {}, root=root)
            for review in ("accuracy", "proofreading", "editorial"):
                (batch / f"review-{review}-1.json").write_text("[]")
            before = (original / "maps/town.po").read_bytes()
            result = approve_batch("b1", target, root=root)
            self.assertEqual(result["cleared_fuzzy"], 1)
            self.assertFalse(load_catalog(target)["s1"][1].fuzzy)
            self.assertEqual((original / "maps/town.po").read_bytes(), before)
            self.assertTrue(json.loads((batch / "status.json").read_text())["approved"])

    def test_import_retargets_recorded_paths_without_touching_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            original, target = tmp / "translation/po", tmp / "scratch/po"
            root = tmp / "batches"
            for directory in (original, target):
                write(directory / "maps/town.po", [PoEntry("s1", "Hello", "Old", translator=["Keep note"])], {})
            batch = export_batch("b1", ["s1"], original, {}, root=root)
            (batch / "translation.json").write_text(json.dumps({"s1": "New"}))
            before = (original / "maps/town.po").read_bytes()
            result = import_batch("b1", target, root=root)
            entry = load_catalog(target)["s1"][1]
            self.assertEqual(result["changed"], 1)
            self.assertEqual((entry.msgstr, entry.fuzzy, entry.translator), ("New", True, ["Keep note"]))
            self.assertEqual((original / "maps/town.po").read_bytes(), before)

    def test_import_rejects_a_path_outside_the_selected_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            po, root = tmp / "po", tmp / "batches"
            write(po / "a.po", [PoEntry("s1", "Hello", "Old")], {})
            batch = export_batch("b1", ["s1"], po, {}, root=root)
            (batch / "translation.json").write_text(json.dumps({"s1": "New"}))
            source = json.loads((batch / "source.json").read_text())
            source[0]["file"] = "translation/po/../../outside.po"
            (batch / "source.json").write_text(json.dumps(source))
            before = (po / "a.po").read_bytes()
            with self.assertRaisesRegex(ValueError, "escapes the selected catalog"):
                import_batch("b1", po, root=root)
            self.assertEqual((po / "a.po").read_bytes(), before)

    def test_cli_import_targets_another_project_and_explicit_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            first, second = tmp / "first", tmp / "second"
            scratch = second / "scratch/po"
            for directory, language in ((first / "translation/po", "ru"),
                                        (second / "translation/po", "test"), (scratch, "test")):
                write(directory / "maps/town.po", [PoEntry("s1", "Hello", "Old")],
                      {"Project-Id-Version": "game", "Language": language})
            (tmp / "projects.toml").write_text(
                '[defaults]\nprofile="game"\nlanguage="ru"\n'
                '[[project]]\nprofile="game"\nlanguage="ru"\nroot="first"\npolicy="policy.toml"\n'
                '[[project]]\nprofile="game"\nlanguage="test"\nroot="second"\npolicy="policy.toml"\n')
            profile = tmp / "game.toml"
            profile.write_text('[rom]\nsha1="' + "0" * 40 + '"\n')
            from gen3tl.profile.profile import Profile
            write_policy(first, Profile.load(profile))
            write_policy(second, Profile.load(profile), "test")
            batch = export_batch("b1", ["s1"], first / "translation/po", {}, root=second / "translation/batches")
            (batch / "translation.json").write_text(json.dumps({"s1": "New"}))
            original = (first / "translation/po/maps/town.po").read_bytes()
            selected = (second / "translation/po/maps/town.po").read_bytes()
            args = ["--project-root", str(tmp), "--profile", str(profile), "--lang", "test", "catalog", "batch-import", "b1"]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertIsNone(main(args + ["--po-root", str(scratch)]))
            self.assertEqual(load_catalog(scratch)["s1"][1].msgstr, "New")
            self.assertEqual((first / "translation/po/maps/town.po").read_bytes(), original)
            self.assertEqual((second / "translation/po/maps/town.po").read_bytes(), selected)
            source = json.loads((batch / "source.json").read_text())
            source[0]["file"] = "translation/po/maps/town.po"
            (batch / "source.json").write_text(json.dumps(source))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertIsNone(main(args))
            self.assertEqual(load_catalog(second / "translation/po")["s1"][1].msgstr, "New")
            self.assertEqual((first / "translation/po/maps/town.po").read_bytes(), original)
            for review in ("accuracy", "proofreading", "editorial"):
                (batch / f"review-{review}-1.json").write_text("[]")
            approval = ["batch-approve" if arg == "batch-import" else arg for arg in args]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertIsNone(main(approval + ["--po-root", str(scratch)]))
            self.assertFalse(load_catalog(scratch)["s1"][1].fuzzy)
            self.assertTrue(load_catalog(second / "translation/po")["s1"][1].fuzzy)
            self.assertEqual((first / "translation/po/maps/town.po").read_bytes(), original)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertIsNone(main(approval))
            self.assertFalse(load_catalog(second / "translation/po")["s1"][1].fuzzy)
            self.assertEqual((first / "translation/po/maps/town.po").read_bytes(), original)

    def test_export_refuses_an_approved_batch(self):
        # a stale plan must not rewrite an approved batch
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            po = tmp / "po"
            write(po / "a.po", [PoEntry("s1", "Hello!", "")], {})
            root = tmp / "batches"
            d = export_batch("b1", ["s1"], po, {}, None, root=root)
            (d / "status.json").write_text(json.dumps({"name": "b1", "approved": True}))
            before = (d / "source.json").read_text()
            with self.assertRaises(ValueError):
                export_batch("b1", ["s1"], po, {}, None, root=root)
            self.assertEqual((d / "source.json").read_text(), before)
            export_batch("b1", ["s1"], po, {}, None, root=root, force=True)   # explicit override

    def test_export_refuses_a_batch_with_a_translation(self):
        # a batch in review keeps the source of its translation
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            po = tmp / "po"
            write(po / "a.po", [PoEntry("s1", "Hello!", ""), PoEntry("s2", "Bye.", "")], {})
            root = tmp / "batches"
            d = export_batch("b1", ["s1"], po, {}, None, root=root)
            export_batch("b1", ["s1"], po, {}, None, root=root)              # no translation yet: allowed
            (d / "translation.json").write_text(json.dumps({"s1": "Привет!"}, ensure_ascii=False))
            with self.assertRaises(ValueError):
                export_batch("b1", ["s2"], po, {}, None, root=root)
            self.assertEqual([x["id"] for x in json.loads((d / "source.json").read_text())], ["s1"])
            export_batch("b1", ["s1"], po, {}, None, root=root, force=True)

    def test_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            po = tmp / "po"
            write(po / "a.po", [PoEntry("s1", "Hello!", ""), PoEntry("s2", "Bye.", "")], {})
            root = tmp / "batches"
            d = export_batch("b1", ["s1", "s2"], po, {}, None, root=root)
            self.assertEqual(len(json.loads((d / "source.json").read_text())), 2)
            (d / "translation.json").write_text(json.dumps({"s1": "Привет!", "s2": "Пока."}, ensure_ascii=False))
            r = import_batch("b1", po, root=root)
            self.assertEqual(r["changed"], 2)
            cat = load_catalog(po)
            self.assertTrue(cat["s1"][1].fuzzy)
            (d / "translation.json").write_text(json.dumps({"s9": "x"}))
            with self.assertRaises(KeyError):
                import_batch("b1", po, root=root)
            (d / "review-accuracy-1.json").write_text(json.dumps([{"id": "s1", "problem": "p", "fix": "f"}]))
            with self.assertRaises(RuntimeError):
                approve_batch("b1", po, root=root)
            for p in ("accuracy", "proofreading", "editorial"):
                (d / f"review-{p}-2.json").write_text("[]")
            self.assertEqual(review_state("b1", root)["accuracy"], {"round": 2, "findings": 0})
            out = approve_batch("b1", po, root=root)
            self.assertEqual(out["cleared_fuzzy"], 2)
            self.assertFalse(load_catalog(po)["s1"][1].fuzzy)

    def test_empty_translation_sets_the_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            po = tmp / "po"
            write(po / "a.po", [PoEntry("s1", "s", ""), PoEntry("s2", "Bye.", "")], {})
            root = tmp / "batches"
            d = export_batch("b1", ["s1", "s2"], po, {}, None, root=root)
            (d / "translation.json").write_text(json.dumps({"s1": "", "s2": "Пока."}, ensure_ascii=False))
            self.assertEqual(import_batch("b1", po, root=root)["changed"], 2)
            e = load_catalog(po)["s1"][1]
            self.assertEqual((e.msgstr, "empty" in e.flags, e.fuzzy), ("", True, True))
            for p in ("accuracy", "proofreading", "editorial"):
                (d / f"review-{p}-1.json").write_text("[]")
            self.assertEqual(approve_batch("b1", po, root=root)["cleared_fuzzy"], 2)
            self.assertFalse(load_catalog(po)["s1"][1].fuzzy)

    def prefill_catalog(self, po: Path) -> None:
        write(po / "maps" / "a.po", [PoEntry("a1", "Hello!", "Привет!"),
                                     PoEntry("a2", "Thanks.", "Спасибо."),
                                     PoEntry("a3", "Hi.", "Хай.", flags=["fuzzy"])], {})
        write(po / "maps" / "b.po", [PoEntry("b1", "Hello!", "Привет!"),
                                     PoEntry("b2", "Thanks.", "Благодарю."),
                                     PoEntry("b3", "Hello!", "Здорово!", flags=["fuzzy"])], {})
        write(po / "maps" / "c.po", [PoEntry("u1", "Hello!", ""), PoEntry("u2", "Bye.", ""),
                                     PoEntry("u3", "Thanks.", ""), PoEntry("u4", "Hi.", "")], {})

    def test_prefill_fills_one_variant_and_lists_several(self):
        # drafts come from approved entries with equal English.
        # several approved variants leave the entry empty.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            po = tmp / "po"
            self.prefill_catalog(po)
            root = tmp / "batches"
            d = export_batch("b1", ["u1", "u2", "u3", "u4"], po, {}, None, root=root, prefill_drafts=True)
            tr = json.loads((d / "translation.json").read_text(encoding="utf-8"))
            self.assertEqual(tr, {"u1": "Привет!", "u2": None, "u3": None, "u4": None})
            with self.assertRaises(ValueError):                          # null never imports
                import_batch("b1", po, root=root)
            self.assertEqual(json.loads((d / "status.json").read_text())["prefill"], {"filled": 1, "variants": 1})
            notes = (d / "translator-notes.md").read_text(encoding="utf-8")
            self.assertEqual(notes.splitlines()[0], PREFILL_HEADING)
            self.assertIn("- u1 from a1 (maps/a), b1 (maps/b)\n", notes)
            self.assertIn("- u3, English \"Thanks.\":\n", notes)
            self.assertIn('  - "Спасибо." in a2 (maps/a)\n', notes)
            self.assertIn('  - "Благодарю." in b2 (maps/b)\n', notes)
            for word in ("gender", "addressee", "inserted", "fit"):
                self.assertIn(word, notes)
            self.assertNotIn("u2", notes)

    def test_prefill_never_uses_a_fuzzy_translation(self):
        # a fuzzy translation is not approved: no fill, no variant
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            po = tmp / "po"
            self.prefill_catalog(po)
            d = export_batch("b1", ["u1", "u4"], po, {}, None, root=tmp / "batches", prefill_drafts=True)
            tr = json.loads((d / "translation.json").read_text(encoding="utf-8"))
            self.assertEqual(tr, {"u1": "Привет!", "u4": None})   # b3's fuzzy "Здорово!" is no variant
            notes = (d / "translator-notes.md").read_text(encoding="utf-8")
            self.assertNotIn("u4", notes)
            self.assertNotIn("a3", notes)
            self.assertNotIn("b3", notes)
            self.assertIn("### Several approved variants, left empty (0)\n\nNone.\n", notes)

    def test_prefill_notes_keep_the_translator_text_and_the_translation(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            po = tmp / "po"
            self.prefill_catalog(po)
            root = tmp / "batches"
            d = root / "b1"
            d.mkdir(parents=True)
            (d / "translator-notes.md").write_text("Translator: x\n\n## Speakers\n\nText.\n", encoding="utf-8")
            export_batch("b1", ["u1"], po, {}, None, root=root, prefill_drafts=True)
            notes = (d / "translator-notes.md").read_text(encoding="utf-8")
            self.assertTrue(notes.startswith("Translator: x\n\n## Speakers\n\nText.\n\n" + PREFILL_HEADING + "\n"))
            # even --force keeps an existing translation
            (d / "translation.json").write_text(json.dumps({"u1": "Здравствуй!"}, ensure_ascii=False), encoding="utf-8")
            for force in (False, True):
                with self.assertRaises(ValueError):
                    export_batch("b1", ["u1", "u3"], po, {}, None, root=root, force=force, prefill_drafts=True)
            self.assertEqual(json.loads((d / "translation.json").read_text(encoding="utf-8")), {"u1": "Здравствуй!"})
            # without a translation the section is replaced, not duplicated
            (d / "translation.json").unlink()
            (d / "translator-notes.md").write_text(notes + "\n## Candidates\n\nNone.\n", encoding="utf-8")
            export_batch("b1", ["u1", "u3"], po, {}, None, root=root, prefill_drafts=True)
            notes = (d / "translator-notes.md").read_text(encoding="utf-8")
            self.assertEqual(notes.count(PREFILL_HEADING), 1)
            self.assertIn("- u3, English", notes)
            self.assertTrue(notes.startswith("Translator: x\n\n## Speakers\n"))
            self.assertTrue(notes.endswith("\n\n## Candidates\n\nNone.\n"))


if __name__ == "__main__":
    unittest.main()
