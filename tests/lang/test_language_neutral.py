import ast
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from test_roots import TOOLKIT_ROOT
from gen3tl.cli import lang as cli_lang, main as cli
from gen3tl.web import editorcheck
from gen3tl.lang import language, termbase, termedit
from gen3tl.catalog.catalog import load_catalog
from gen3tl.check.layout import abbreviation_pair, keeps_with_next
from gen3tl.catalog.po import PoEntry, write
from gen3tl.profile.profile import Profile
from gen3tl.verify.savefile import transliterate
from project_fixture import select_language, write_policy

ROOT = TOOLKIT_ROOT

def setUpModule():
    select_language("ru")

class NeutralCoreTest(unittest.TestCase):
    def test_executable_core_has_no_target_language_policy(self):
        findings = []
        for path in sorted((ROOT / "src/gen3tl").rglob("*.py")):
            tree = ast.parse(path.read_text())
            docs = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr)
                    and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)}
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
                    if re.search(r"(?i)\b(?:russian|cyrillic|ru|french|fr|german|de|spanish)\b|[А-Яа-яЁё]", node.value):
                        findings.append((path.name, node.lineno, node.value[:80]))
                name = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ""
                if name in ("ru", "russian", "cyrillic") or name.startswith(("ru_", "russian_")):
                    findings.append((path.name, node.lineno, name))
        self.assertEqual(findings, [])


    def test_save_link_fields_are_shared(self):
        from gen3tl.lang.language import save_link_keys
        for code in ("es", "de", "fr", "ru"):
            with self.subTest(code=code), language.use(code):
                self.assertEqual(save_link_keys(), ("save_link", "save_link_defaults", "save_link_letters"))
                self.assertEqual(save_link_keys(code), save_link_keys())

    def test_manual_default_constructs_every_command_parser(self):
        with patch.object(language, "default", return_value=language.load("fr")), redirect_stdout(io.StringIO()):
            for group, commands in cli.parser()[1].items():
                for command in commands:
                    with self.subTest(command=command), self.assertRaises(SystemExit) as raised:
                        cli.main([group, command, "--lang=fr", "--help"])
                    self.assertEqual(raised.exception.code, 0)

    def test_manual_pack_preserves_forms_without_analyzer(self):
        with language.use("fr"), patch.dict(sys.modules, {"pymorphy3": None}):
            term = termbase.Term("item:berry", "item", "Berry", "baie",
                                 forms={"nomn.sing": "baie", "nomn.plur": "baies"})
            self.assertEqual(termbase.generate_forms(term), term.forms)
            self.assertIsNone(termbase.morph())
            self.assertEqual(termbase.find_forms("baies baiesx ébaies", term), [("nomn.plur", "baies", 0)])
            self.assertFalse(keeps_with_next("в"))
            self.assertFalse(abbreviation_pair("т.", "п."))
            self.assertEqual(transliterate("Женя"), "Женя")
        self.assertTrue(keeps_with_next("в"))
        self.assertTrue(abbreviation_pair("т.", "п."))
        self.assertEqual(transliterate("Женя"), "Zhenya")

    def test_glossary_columns_are_shared(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "glossary.tsv"
            term = termbase.Term("t", "term", "Source", "Target")
            with language.use("ru"):
                termbase.save(path, [term])
                self.assertIn("\ttranslation\t", path.read_text().splitlines()[0])
                self.assertIn("translation", termedit.values(term))
                self.assertEqual(termbase.load(path)[0].translation, "Target")
            with language.use("fr"):
                termbase.save(path, [term])
                self.assertIn("\ttranslation\t", path.read_text().splitlines()[0])
                self.assertIn("translation", termedit.values(term))
                self.assertNotIn("russian", termedit.values(term))
                self.assertEqual(termbase.load(path)[0].translation, "Target")

    def test_term_aliases_are_refused(self):
        with self.assertRaises(TypeError):
            termbase.Term(id="t", category="term", source_text="Source", russian="Target")
        term = termbase.Term("t", "term", "Source", "Target")
        with self.assertRaises(AttributeError):
            getattr(term, "russian")
        _, _, refused = termedit.apply_changes([term], [{"id": "t",
            "base": {"russian": "Target"}, "changes": {"russian": "Changed"}}])
        self.assertEqual(refused, ["t: russian cannot be edited"])

    def test_selection_scopes_keyword_calls_and_exceptions(self):
        @language.code_selected
        def build(lang):
            self.assertEqual(language.current().code, lang)
            raise RuntimeError("fixture")

        @language.profile_selected
        def check(prof):
            return language.current().code

        from types import SimpleNamespace
        with language.use("ru"):
            self.assertEqual(check(prof=SimpleNamespace(language="fr")), "fr")
            with self.assertRaises(RuntimeError):
                build(lang="fr")
            self.assertEqual(language.current().code, "ru")

    def test_manual_pack_does_not_inherit_another_pack(self):
        for code in ("es", "de", "fr"):
            with self.subTest(code=code), language.use(code):
                self.assertIsNone(termbase.morph())
                term = termbase.Term("t", "term", "Berry", "fruit", forms={"nomn.plur": "fruits"})
                self.assertEqual(termbase.generate_forms(term), term.all_forms())
                self.assertFalse(keeps_with_next("в"))

    def test_metadata_rejects_mismatched_or_unsafe_providers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "fr"
            pack.mkdir()
            (root / "outside.py").write_text("raise AssertionError('loaded')")
            cases = [[], {"code": "de"}, {"unknown": "value"}, {"locale": 1}, {"locale": "en"},
                     {"locale": "english_US"}, {"provider": "../outside.py"}, {"agreement": "language.json"},
                     {"fold": {"ab": "c"}}, {"legacy": {"a": "x", "b": "x"}},
                     {"stand_ins": "../outside.json"}, {"stand_ins": "missing.json"},
                     {"save": {"letter_pattern": 1}}, {"save": {"transliteration": {"ab": "c"}}},
                     {"pseudo": {"source": "ab", "target": "a"}}, {"stand_ins": "../outside.py"}, {"stand_ins": None}, {"stand_ins": []}]
            for case in cases:
                with self.subTest(case=case):
                    data = {"schema": "2.0.0", "code": "fr", **case} if isinstance(case, dict) else case
                    (pack / "language.json").write_text(json.dumps(data))
                    language.load.cache_clear()
                    with self.assertRaises(ValueError):
                        language.load("fr", root)

    def test_stand_ins_load_declared_pack_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "fixture"
            pack.mkdir()
            (pack / "language.json").write_text(json.dumps({
                "schema": "2.0.0", "code": "fixture", "stand_ins": "standins.json"}))
            table = pack / "standins.json"
            table.write_text(json.dumps({"ø": "o"}))
            selected = language.load("fixture", root)
            self.assertEqual(selected.stand_ins(), {"ø": "o"})
            self.assertEqual(language.load("missing", root).stand_ins(), {})
            for invalid in ([], {"ab": "a"}, {"a": "bc"}, {"a": 1}, {"a": []}, {"": "a"}):
                with self.subTest(invalid=invalid):
                    table.write_text(json.dumps(invalid))
                    with self.assertRaisesRegex(ValueError, "invalid language stand-ins"):
                        selected.stand_ins()

    def test_bundle_carries_rules_without_project_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "check.zip"
            editorcheck.write_bundle(bundle, {})
            with zipfile.ZipFile(bundle) as archive:
                archive.extractall(root)
            script = ('from gen3tl.lang import language; from gen3tl.check import layout; '
                      'assert language.current().code == ""; '
                      'assert not layout.keeps_with_next("в"); '
                      'selected = language.load("ru"); '
                      'assert selected.agreement_provider() is not None; '
                      'assert selected.stand_ins()["Ё"] == "Ë"; '
                      'assert language.load("fr").stand_ins() == {}; '
                      'assert language.load("fr").provider() is None')
            result = subprocess.run([sys.executable, "-c", script], cwd=root,
                                    env={"PYTHONPATH": str(root)}, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)

class ManualCommandsTest(unittest.TestCase):
    def test_forms_index_and_rename_use_selected_glossary_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = root / "fixture.toml"
            profile.write_text('[rom]\nsha1 = "' + "0" * 40 + '"\n')
            write_policy(root, Profile.load(profile), "fr")
            (root / "projects.toml").write_text('[defaults]\nprofile="fixture"\nlanguage="fr"\n'
                '[[project]]\nprofile="fixture"\nlanguage="fr"\nroot="."\npolicy="policy.toml"\n')
            catalog = root / "translation/po"
            write(catalog / "a.po", [PoEntry("s1", "Berries", "Des baies.")],
                  {"Project-Id-Version": "fixture", "Language": "fr"})
            glossary, replacement = root / "translation/glossary.tsv", root / "replacement.tsv"
            with language.use("fr"):
                termbase.save(glossary, [termbase.Term("item:berry", "item", "Berry", "baie",
                    forms={"nomn.sing": "baie", "nomn.plur": "baies"})])
                termbase.save(replacement, [termbase.Term("item:berry", "item", "Berry", "fruit",
                    forms={"nomn.sing": "fruit", "nomn.plur": "fruits"})])
            prefix = ["--profile", str(profile), "--project-root", str(root)]
            with redirect_stdout(io.StringIO()), patch.dict(sys.modules, {"pymorphy3": None}):
                cli.main(prefix + ["lang", "forms", "--lang", "fr", "--all"])
                cli.main(prefix + ["--lang", "fr", "lang", "index"])
                before = (root / "translation/term-index.tsv").read_text()
                self.assertIn("nomn.plur", before)
                original = {p: p.read_bytes() for p in (glossary, catalog / "a.po")}
                with language.use("fr"):
                    valid = termbase.load(replacement)[0]
                    for forms, english in (({"nomn.sing": "fruit"}, "Berry"), (valid.forms, "stale"),
                                           ({"nomn.sing": "baie", "nomn.plur": "baies"}, "Berry"),
                                           ({"nomn.sing": "fruit", "nomn.plur": ""}, "Berry")):
                        invalid = termbase.Term("item:berry", "item", english, "fruit", forms=forms)
                        termbase.save(replacement, [invalid])
                        self.assertEqual(cli.main(prefix + ["lang", "rename", "--lang=fr", "item:berry", "fruit", "--apply",
                                                            "--replacement-glossary", str(replacement)]), 1)
                        self.assertEqual({p: p.read_bytes() for p in original}, original)
                    termbase.save(replacement, [valid])
                with patch.object(termbase, "generate_forms", side_effect=AssertionError("rename generated forms")), \
                     patch.object(cli_lang, "_rename_checks", return_value=({}, {}, {})):
                    result = cli.main(prefix + ["lang", "rename", "--lang=fr", "item:berry", "fruit", "--apply",
                        "--replacement-glossary", str(replacement)])
                self.assertEqual(result, 0)
            self.assertEqual(load_catalog(catalog)["s1"][1].msgstr, "Des fruits.")
            self.assertFalse(load_catalog(catalog)["s1"][1].fuzzy)   # approved before the rename
            self.assertIn("fruits", (root / "translation/term-index.tsv").read_text())
            with language.use("fr"):
                self.assertEqual(termbase.load(glossary)[0].forms["nomn.plur"], "fruits")
