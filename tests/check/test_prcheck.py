import contextlib
from copy import deepcopy
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project_fixture import literal, write_policy
from gen3tl.cli import main as cli
from gen3tl.web import editorsnapshot
from gen3tl.catalog.po import KEY_ONLY, SOURCE_FIELD, PoEntry, read, write
from gen3tl.catalog.source import digest
from gen3tl.profile.profile import Profile
from gen3tl.check.prcheck import StandInGlyphs, rule_changes, rules_report, stand_in
from gen3tl.check.render import read_png_image
from gen3tl.lang import language
from gen3tl.lang.langpack import LangPack

class StandInTest(unittest.TestCase):
    def test_pack_stand_ins_preserve_pixels_and_widths(self):
        aliases = dict(zip("АВЕКМНОРСТХаеорсухЁё", "ABEKMHOPCTXaeopcyxËë"))
        pack = LangPack.load("ru", "unbound")
        for small in (False, True):
            for width in (4, 6, 10):
                with language.use("fr"):
                    glyphs = StandInGlyphs(pack, [width] * 512, small)
                for source, target in aliases.items():
                    with self.subTest(source=source, width=width, small=small):
                        self.assertEqual(glyphs.get(pack.chars[source]), (stand_in(target, width, small), width))

    def test_other_packs_do_not_inherit_stand_ins(self):
        for code in ("es", "de", "fr", "fixture"):
            pack = LangPack(code, chars={"А": 0x15, "Ё": 0x16, "ё": 0x17})
            with self.subTest(code=code), language.use("ru"):
                glyphs = StandInGlyphs(pack, [6] * 512)
                self.assertEqual(glyphs.stand_ins, {})
                for char, byte in pack.chars.items():
                    self.assertEqual(glyphs.get(byte), (stand_in(char, 6), 6))

class PrCheckTest(unittest.TestCase):
    HEADER = {}   # the form of the catalog
    QA = {}       # the typography rules of the policy

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.po = self.root / "translation/po"
        self.entries = [PoEntry("s0000100", "Fit.", "Прежний текст"),
                        PoEntry("s0000200", "Overflow.", "Другой текст")]
        write(self.po / "map.po", self.entries,
              {"Project-Id-Version": "test", "Language": "ru", "Content-Type": "text/plain; charset=UTF-8",
               **self.HEADER})
        data = {
            "rom": {"sha1": "ab" * 20},
            "font": [{"name": "main", "glyphs": 0, "widths": 0x1000, "count": 512}],
            "font_style": {"style": "unbound", "cell_height": 14},
            "layout": {"dialogue_width": 60, "placeholders": {}},
        }
        self.profile = Profile(data, self.root / "test.toml")
        self.profile.path.write_text("\n".join(f"{key} = {literal(value)}" for key, value in data.items()),
                                     encoding="utf-8")
        write_policy(self.root, self.profile, **({"qa": self.QA} if self.QA else {}))
        (self.root / "projects.toml").write_text(
            '[defaults]\nprofile="test"\nlanguage="ru"\n[[project]]\nprofile="test"\nlanguage="ru"\n'
            'root="."\npolicy="policy.toml"\n', encoding="utf-8")
        (self.root / "translation/release.toml").write_text('version="1.2.3"\n', encoding="utf-8")
        # English limit: a dialogue line of the 60 px window
        self.snapshot({entry.msgctxt: [digest(entry.msgid), [], [1], [6 * len(entry.msgid)],
                                       ["dialogue", 60, 2, None, None, None, None]] for entry in self.entries})
        self.git("init", "-q")
        self.git("add", "projects.toml", "test.toml", "policy.toml", "translation")
        self.git("-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-q", "-m", "base")

    def snapshot(self, rows):
        """The measurement snapshot of the catalog with these source rows."""
        catalog = {entry.msgctxt: (self.po / "map.po", entry) for entry in self.entries}
        from gen3tl.check.qa import row_limit
        facts = editorsnapshot.source_rows(catalog, {sid: row_limit(row) for sid, row in rows.items()}, lambda b: 6)
        rows = {sid: row[:5] + [facts[sid][5]] for sid, row in rows.items()}
        from gen3tl.project import Registry
        effective = Registry.load(self.root).projects[0].effective(self.profile)
        inventory = [{"id": entry.msgctxt, "offset": int(entry.msgctxt[1:], 16), "length": 40,
                      "kind": "pointer", "category": "script", "mode": "field", "table": None, "index": None,
                      "flags": [], "refs": [[0x10 + int(entry.msgctxt[1:], 16), "script:loadpointer"]],
                      "source_sha256": rows[entry.msgctxt][0]} for entry in self.entries]
        snapshot = {"schema": editorsnapshot.SCHEMA, "profile": "test", "language": "ru", "rom_sha1": "ab" * 20,
                    "source_sha256": editorsnapshot.source_digest(catalog, self.po, rows), "source": rows,
                    "configuration_sha256": editorsnapshot.configuration(effective),
                    "fonts": {"unbound": {"widths": [6] * 512, "shadows": [False] * 512}},
                    "inventory": inventory, "context": {}, "reach": {}, "notes": {},
                    "records": {"windows": [], "numbers": {}, "keys": {}, "heads": {}, "cursor": [],
                                "code_originals": {}, "fill_fragments": {}, "fill_alphabet": ""}}
        snapshot["facts_sha256"] = editorsnapshot.digest(snapshot)
        editorsnapshot.write(self.root / "translation/editor-inputs.json", snapshot)

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)

    def edit(self, fitting, overflowing):
        path = self.po / "map.po"
        header, entries = read(path)
        entries[0].msgstr = fitting
        entries[1].msgstr = overflowing
        write(path, entries, header)

    def check(self):
        out = self.root / "result"
        with patch.object(Profile, "by_name", return_value=self.profile), \
                patch.object(Profile, "load_rom", side_effect=AssertionError("ROM access forbidden")), \
                contextlib.redirect_stdout(io.StringIO()):
            status = cli.main(["--project-root", str(self.root), "check", "pr", "--base", "HEAD",
                               "--out", str(out)])
        return status, out

    def test_fitting_and_overflowing_changes(self):
        self.edit("Да", "Оченьдлиннаястрокабезпробелов")
        status, out = self.check()
        self.assertEqual(status, 1)
        report = (out / "report.md").read_text(encoding="utf-8")
        self.assertIn("approximate: stand-in glyphs", report)
        self.assertIn("| test/ru | s0000100 |", report)
        self.assertIn("| test/ru | s0000200 |", report)
        self.assertRegex(report, r"s0000100.*\| ok<br>")
        self.assertRegex(report, r"s0000200.*error: line too wide")
        self.assertIn("check po: passed", report)
        self.assertTrue((out / "qa-test-ru.md").is_file())
        images = sorted(out.glob("*.png"))
        self.assertEqual(len(images), 4)
        self.assertEqual({image.name.rsplit("-", 1)[1] for image in images}, {"before.png", "after.png"})
        for image in images:
            decoded = read_png_image(image)
            self.assertEqual((decoded.width % 2, decoded.height % 2), (0, 0))
            self.assertGreater(decoded.width, 160)
        before = (out / "test-ru-001-s0000100-before.png").read_bytes()
        after = (out / "test-ru-001-s0000100-after.png").read_bytes()
        self.assertNotEqual(before, after)

    def test_no_translation_changes(self):
        status, out = self.check()
        self.assertEqual(status, 0)
        report = (out / "report.md").read_text(encoding="utf-8")
        self.assertIn("No translation changes", report)
        self.assertIn("Result: passed; errors: 0", report)
        self.assertEqual(list(out.glob("*.png")), [])

    def test_shared_snapshot_change_rechecks_the_project(self):
        from gen3tl.check import prcheck
        from gen3tl.project import Registry
        project = Registry.load(self.root).projects[0]
        shared = editorsnapshot.paths(project.snapshot)[1]
        shared.write_text(shared.read_text() + "\n")
        with patch.object(prcheck, "_project_checks", return_value=([], 0, "fixture")) as checks:
            status, _ = self.check()
        self.assertEqual(status, 0)
        checks.assert_called_once()

    def test_only_changed_translations_are_listed(self):
        self.edit("Да", "Другой текст")
        status, out = self.check()
        self.assertEqual(status, 0)
        report = (out / "report.md").read_text(encoding="utf-8")
        self.assertIn("s0000100", report)
        self.assertNotIn("| test/ru | s0000200 |", report)
        self.assertEqual(len(list(out.glob("*.png"))), 2)

    def policy_rule(self):
        from test_policyrules import fixture
        from gen3tl.project import Registry
        data = fixture()
        self.profile = Profile({**self.profile.data, "code_replacement": data["code_replacement"],
                                "language_rules": data["language_rules"][:1]}, self.profile.path)
        self.profile.path.write_text("\n".join(f"{key} = {literal(value)}" for key, value in self.profile.data.items()))
        project = Registry.load(self.root).projects[0]
        write_policy(self.root, self.profile, project.language, code_replacements={"units": {"unit": "m"}})
        self.edit("A", "B")
        self.snapshot({entry.msgctxt: [digest(entry.msgid), [], [1], [6 * len(entry.msgid)],
                                       ["dialogue", 60, 2, None, None, None, None]] for entry in self.entries})
        self.git("add", ".")
        self.git("-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-qm", "rule baseline")
        return project

    def test_policy_only_checks_pass_with_current_measurements(self):
        project = self.policy_rule()
        write_policy(self.root, self.profile, project.language, code_replacements={"units": {"unit": "cm"}})
        self.snapshot({entry.msgctxt: [digest(entry.msgid), [], [1], [6 * len(entry.msgid)],
                                       ["dialogue", 60, 2, None, None, None, None]] for entry in self.entries})
        status, out = self.check()
        report = (out / "report.md").read_text()
        self.assertEqual(status, 0, report)
        self.assertIn("Engine language rules", report)
        self.assertIn("| unit | cm | 3 / 4 | 12 / 18 |", report)
        self.assertIn("No translation changes", report)

    def test_policy_only_checks_reject_unsafe_custom_fields(self):
        project = self.policy_rule()
        write_policy(self.root, self.profile, project.language, code_replacements={"units": {"unit": "abcde"}})
        self.snapshot({entry.msgctxt: [digest(entry.msgid), [], [1], [6 * len(entry.msgid)],
                                       ["dialogue", 60, 2, None, None, None, None]] for entry in self.entries})
        status, out = self.check()
        self.assertEqual(status, 1)
        self.assertIn("unit exceeds its terminated byte budget", (out / "report.md").read_text())

class KeyOnlyPrCheckTest(PrCheckTest):
    """The same checks on a key-only catalog without its English source catalog: the snapshot's facts."""
    HEADER = {SOURCE_FIELD: KEY_ONLY}
    QA = {"forbidden": {"  ": {"reason": "double space", "needs_source": False},
                        "\\.\\.\\.": {"reason": "three dots", "needs_source": False},
                        "…\\.": {"reason": "ellipsis with a dot", "needs_source": True}}}

    def test_the_catalog_holds_no_english_and_the_report_says_what_it_leaves_out(self):
        self.assertNotIn("Overflow.", (self.po / "map.po").read_text(encoding="utf-8"))
        self.assertFalse((self.root / "build/catalog.pot").exists())
        self.edit("Да", "Другой текст")
        status, out = self.check()
        self.assertEqual(status, 0)
        self.assertIn("without the English source catalog: skipped typography rules: "
                      "ellipsis with a dot; glossary skipped",
                      (out / "report.md").read_text(encoding="utf-8"))

    def test_independent_typography_runs_without_the_english(self):
        self.assertFalse((self.root / "build/catalog.pot").exists())
        self.edit("Да  с", "Другой текст")   # a double space the rule reads alone
        status, out = self.check()
        self.assertEqual(status, 1)
        report = (out / "report.md").read_text(encoding="utf-8")
        qa_report = (out / "qa-test-ru.md").read_text(encoding="utf-8")
        self.assertIn("typography: double space", qa_report)
        self.assertIn("skipped typography rules: ellipsis with a dot", report)

    def test_placeholders_and_limits_follow_the_snapshot_row(self):
        self.snapshot({"s0000100": [digest("Fit {PLAYER}."), ["{PLAYER}"], [1], [30],
                                    ["dialogue", 60, 2, None, None, None, None]],
                       "s0000200": [digest("Overflow."), [], [1], [54], ["dialogue", 200, 2, None, None, None, None]]})
        self.edit("Да {PLAYER}", "Оченьдлиннаястрокабезпробелов")
        status, out = self.check()
        report = (out / "report.md").read_text(encoding="utf-8")
        self.assertEqual(status, 0)
        self.assertRegex(report, r"s0000100.*\| 2 / 2 \| 60 / 60 \| ok; 1 warnings<br>")   # the row's one-line box
        self.assertRegex(report, r"s0000200.*\| 172 / 200 \| ok<br>")   # the row's 200 px
        self.edit("Да", "Оченьдлиннаястрокабезпробелов")   # the token of the row left out
        status, out = self.check()
        self.assertEqual(status, 1)
        self.assertRegex((out / "report.md").read_text(encoding="utf-8"), r"s0000100.*error: placeholders differ")
        self.assertIn("placeholders differ", (out / "qa-test-ru.md").read_text(encoding="utf-8"))

    def test_unavailable_declared_sources_preserve_offline_checks(self):
        self.profile.data["table"] = [{"name": "names", "base": 0x1000, "stride": 10, "count": 2}]
        self.profile.data["layout"].update(
            placeholders={"PLAYER": 60}, slot_source={"PLAYER": "player"},
            fill_source={"player": {"label": "Player name", "font": "main", "value": {"table": "names"},
                                    "evidence": "Verified name table"}})
        self.profile.path.write_text("\n".join(f"{key} = {literal(value)}"
                                              for key, value in self.profile.data.items()), encoding="utf-8")
        self.test_placeholders_and_limits_follow_the_snapshot_row()

class EngineRuleReportTest(unittest.TestCase):
    def setUp(self):
        from test_languagerules_check import plural_profile, original_values, CM
        self.cm = CM
        self.profile = Profile({**plural_profile(), "rom": {"sha1": "ab" * 20}}, Path("sample.toml"))
        self.catalog = {}
        for operand in self.profile.language_rules[0]["operands"]:
            text = CM.decode(original_values()[operand["id"]] + b"\xff")[0]
            for binding in operand["bindings"]:
                sid = binding["entry"]
                self.catalog[sid] = (Path("entries.po"), PoEntry(sid, text, ""))
        self.catalog["table:names/0"] = (Path("entries.po"), PoEntry("table:names/0", "Berry", "Berry"))

    def reports(self, after, profile=None):
        return rule_changes(self.profile, profile or self.profile, self.catalog, after,
                            context=(self.cm, set(self.cm.decode_table), None))

    def off(self):
        after = deepcopy(self.catalog)
        for operand in self.profile.language_rules[0]["operands"]:
            text = self.cm.decode(bytes(operand["off"]) + b"\xff")[0]
            for binding in operand["bindings"]:
                after[binding["entry"]][1].set_translation(text, "text" if text else "empty")
        return after

    def test_grouped_off_reports_both_routines_and_combined_name_change(self):
        before = deepcopy(self.catalog)
        after = self.off()
        after["table:names/0"][1].msgstr = "Box"
        reports = self.reports(after)
        self.assertEqual(len(reports), 1)
        self.assertEqual(len(reports[0]["operands"]), 10)
        preview = next(row for row in reports[0]["examples"] if row["quantity"] == 2)
        for row in preview["modes"]["original"]["results"]:
            self.assertEqual(row["text"], "Berries")
        for row in preview["modes"]["proposed"]["results"]:
            self.assertEqual(row["text"], "Box")
            self.assertEqual(row["used"], 6)
        text = rules_report(reports)
        for expected in ("Original", "Current", "Proposed", "normal", "trade", "0x40", "Byte budget",
                         "Approve the combined catalog and policy behavior"):
            self.assertIn(expected, text)
        self.assertEqual(self.catalog, before)

    def test_mismatched_copies_and_deleted_inputs_block(self):
        after = self.off()
        after["table:ordinary-trade/0"] = deepcopy(self.catalog["table:ordinary-trade/0"])
        self.assertIn("routine copies differ", rules_report(self.reports(after)))
        del after["table:ordinary-trade/0"]
        self.assertIn("catalog entry unavailable", rules_report(self.reports(after)))

    def test_inherited_inputs_without_source_text_remain_unavailable(self):
        for sid, (_, entry) in self.catalog.items():
            entry.msgid = sid
            entry.keyed = True
        after = deepcopy(self.catalog)
        after["table:names/0"][1].msgstr = "Box"
        text = rules_report(self.reports(after))
        self.assertIn("original text or catalog input unavailable", text)
        self.assertIn("unavailable", text)

    def test_related_host_changes_recheck_the_complete_rule(self):
        self.profile.data["language_rules"][0]["related"] = ["host"]
        self.catalog["host"] = (Path("entries.po"), PoEntry("host", "Received {STR_VAR_1}.", ""))
        after = deepcopy(self.catalog)
        after["host"][1].set_translation("Stored {STR_VAR_1}.", "text")
        text = rules_report(self.reports(after))
        self.assertIn("Stored {STR_VAR_1}.", text)
        self.assertIn("Berries", text)
        self.assertNotIn("pending catalog review", text)
