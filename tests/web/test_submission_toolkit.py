import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from test_roots import TEST_ROOT, TOOLKIT_ROOT
from gen3tl.catalog.po import PoEntry, read, write
from gen3tl.project import Registry
from gen3tl.web import exportenvelope, sitecatalog, submission

ROOT = TOOLKIT_ROOT

SITE_EXPORT = TEST_ROOT / "fixtures" / "site-export.json"

def toolkit_file(path: Path) -> Path:
    """The toolkit file by its working-repository path, or its public path after the split."""
    return path if path.is_file() else path.with_name(path.name.removeprefix("toolkit-"))

def form(export: str = "") -> str:
    """An issue body as the toolkit form renders it."""
    inline = f"```json\n{export}\n```" if export else "_No response_"
    return f"### Editor export\r\n\r\n{inline}\r\n\r\n### Export file\r\n\r\n_No response_"

class ToolkitSiteSubmissionTest(unittest.TestCase):
    """A site catalog submission in a toolkit tree: no projects.toml, web/i18n under the root."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.i18n = self.root / "web" / "i18n"
        self.i18n.mkdir(parents=True)
        self.site = self.i18n / "xx.po"
        write(self.site, [PoEntry("site:Patch", "Patch", "Патч", extracted=["index.html"]),
                          PoEntry("site:Loading {game}…", "Loading {game}…", "", extracted=["js/pick.js"]),
                          PoEntry("site:Home", "Home", "Дом", extracted=["index.html"])],
              {"Project-Id-Version": "site", "Language": "xx", "Content-Type": "text/plain; charset=UTF-8"})
        self.before = self.site.read_bytes()
        hook = patch.object(sitecatalog, "CATALOGS", self.i18n)
        hook.start()
        self.addCleanup(hook.stop)

    def submit(self, export, check=True):
        event = self.root / "event.json"
        event.write_text(json.dumps({"issue": {"number": 5, "body": form(export),
                                               "user": {"login": "contributor"}}}), encoding="utf-8")
        out = self.root / "answer"
        return submission.run(event, out, Registry(self.root, "", "", ()), check), out

    def answer(self, out, name):
        return (out / name).read_text(encoding="utf-8")

    def test_a_site_export_produces_the_toolkit_pull_request_body(self):
        self.assertFalse((self.root / "projects.toml").exists())
        status, out = self.submit(SITE_EXPORT.read_text(encoding="utf-8"), check=True)
        self.assertEqual(status, 0)
        self.assertEqual(self.answer(out, "status"), "valid\n")
        self.assertEqual(self.answer(out, "profile"), "site\n")
        self.assertEqual(self.answer(out, "files"), "web/i18n/xx.po\n")
        pr = self.answer(out, "pr.md")
        for part in ("Site interface texts submitted with the editor in #5.",
                     "- Site catalog: `web/i18n/xx.po`", "Resolves #5"):
            self.assertIn(part, pr)
        self.assertEqual(self.site.read_bytes(), self.before)   # check mode wrote nothing

    def test_applying_the_site_export_writes_the_toolkit_catalog(self):
        status, out = self.submit(SITE_EXPORT.read_text(encoding="utf-8"), check=False)
        self.assertEqual(status, 0)
        self.assertEqual(self.answer(out, "status"), "valid\n")
        _, entries = read(self.site)
        self.assertEqual([(e.msgid, e.msgstr) for e in entries],
                         [("Patch", "Патч"), ("Loading {game}…", "Загрузка {game}…"), ("Home", "Главная")])

    def test_web_import_dry_run_runs_without_a_project(self):
        from gen3tl.__main__ import main
        export = self.root / "site-export.json"
        export.write_text(SITE_EXPORT.read_text(encoding="utf-8"), encoding="utf-8")
        previous = os.getcwd()
        os.chdir(self.root)
        try:
            with contextlib.redirect_stdout(io.StringIO()) as log:
                status = main(["web", "import", str(export), "--dry-run"])
        finally:
            os.chdir(previous)
        self.assertEqual(status, 0, log.getvalue())
        self.assertIn("planned", log.getvalue())

    def test_web_import_of_a_game_export_without_a_project_is_refused(self):
        from gen3tl.__main__ import main
        export = self.root / "game-export.json"
        export.write_text(json.dumps({"profile": "test-1.0", "language": "xx", "entries": []}), encoding="utf-8")
        previous = os.getcwd()
        os.chdir(self.root)
        try:
            with contextlib.redirect_stdout(io.StringIO()) as log:
                status = main(["web", "import", str(export), "--dry-run"])
        finally:
            os.chdir(previous)
        self.assertEqual(status, 1)
        self.assertIn("needs a translation project", log.getvalue())


class ToolkitWorkflowTest(unittest.TestCase):
    def workflow(self):
        path = toolkit_file(ROOT / ".github/workflows/toolkit-submission.yml")
        return path, path.read_text(encoding="utf-8")

    def test_workflow_yaml_parses_and_names_the_script(self):
        _, text = self.workflow()
        self.assertNotIn("\t", text)
        self.assertEqual(set(self.top_fields(text)) >= {"name", "on", "permissions", "concurrency", "jobs"}, True)
        self.assertIn("issues:", text)
        self.assertIn("run: bash .github/scripts/submission.sh", text)
        self.assertIn("PYTHONPATH: src", text)

    def test_a_run_that_skips_keeps_its_own_group(self):
        _, text = self.workflow()
        group = re.search(r"\n  group: >-\n((?:    .*\n)+)", text).group(1)
        condition = re.search(r"\n    if: >-\n((?:      .*\n)+)", text).group(1)
        applies = " ".join(condition.split())
        expected = ("submission-${{ github.event.issue.number }}-${{ " + applies
                    + " && 'apply' || github.run_id }}")
        self.assertEqual(" ".join(group.split()), expected)
        self.assertIn("  cancel-in-progress: true\n", text)

    def test_the_clean_up_deletes_only_submission_branches_of_this_repository(self):
        _, text = self.workflow()
        self.assertIn("  pull_request:\n    types: [closed]\n", text)
        job = text.split("\n  remove:\n", 1)[1]
        condition = " ".join(re.search(r"    if: >-\n((?:      .*\n)+)", job).group(1).split())
        self.assertEqual(condition, "github.event_name == 'pull_request'"
                                    " && github.event.pull_request.head.repo.full_name == github.repository"
                                    " && startsWith(github.event.pull_request.head.ref, 'submission/')")
        self.assertIn("    permissions:\n      contents: write\n", job)
        script = job.split("        run: |\n", 1)[1]
        self.assertNotIn("${{", script)
        self.assertIn('git/refs/heads/${BRANCH}"', script)

    def test_workflow_script_names_the_import_and_site_checks(self):
        script = toolkit_file(ROOT / ".github/scripts/toolkit-submission.sh")
        text = script.read_text(encoding="utf-8")
        for part in ("web import", "web site-catalog --sync", "web/i18n", "assets/lang"):
            self.assertIn(part, text)

    def test_workflow_indentation_is_consistent(self):
        _, text = self.workflow()
        lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
        for line in lines:
            indent = len(line) - len(line.lstrip(" "))
            self.assertEqual(indent % 2, 0, line)

    def top_fields(self, text):
        """Top-level keys of a workflow file, from its indentation."""
        fields = []
        for line in text.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            indent = len(line) - len(line.lstrip(" "))
            if indent == 0 and ":" in line and not line.startswith("-"):
                fields.append(line.split(":", 1)[0].strip())
        return fields

class ToolkitPackWorkflowTest(unittest.TestCase):
    """A pack-only glyph export staged on the submission branch."""

    ART = ("# test art\n"
           "glyph Б width 6\n"
           "______\n______\n______\n#####s\n#sssss\n#s____\n####s_\n#sss#s\n"
           "#s__#s\n#s__#s\n####ss\nsssss_\n______\n______\n")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / "projects.toml").write_text("", encoding="utf-8")   # a toolkit tree
        fonts = self.root / "assets/lang/xx/fonts"
        fonts.mkdir(parents=True)
        (self.root / "assets/lang/xx/charmap.tsv").write_text("char\tbyte\tsource\nБ\t40\tglyph\n",
                                                              encoding="utf-8")
        self.art = fonts / "main.txt"
        self.art.write_text(self.ART, encoding="utf-8")
        self.original = self.art.read_text(encoding="utf-8")

    def export(self):
        """An editor export whose glyph record redraws one shadow pixel away."""
        from gen3tl.web.editorfonts import art_rows, parse_art
        glyph = parse_art(self.art.read_text(encoding="utf-8"), 14, 0)["Б"]
        rows = art_rows(glyph, 0, 14)
        changed = list(rows)
        at = next(i for i, row in enumerate(rows) if "s" in row)
        changed[at] = changed[at][::-1].replace("s", "_", 1)[::-1]   # missing shadows are allowed
        return json.dumps({"profile": "test-1.0", "language": "xx", "entries": [],
                           "glyphs": [{"style": "main", "char": "Б", "width": glyph.width,
                                       "rows": changed, "base": {"width": glyph.width, "rows": rows}}]})

    def write_mocks(self):
        """The python, git and mktemp doubles the workflow runs with."""
        python = self.root / "python"
        python.write_text(f"#!{sys.executable}\n" + textwrap.dedent('''\
            import json
            import sys
            from pathlib import Path

            if len(sys.argv) > 1 and sys.argv[1] == "-":
                sys.argv = ["-"] + sys.argv[2:]
                with Path("commands.jsonl").open("a") as stream:
                    stream.write(json.dumps(["python", *sys.argv]) + "\\n")
                exec(compile(sys.stdin.read(), "-", "exec"), {"__name__": "__main__"})
                raise SystemExit(0)

            from gen3tl.cli import main
            from gen3tl.profile.profile import Profile
            from gen3tl.web import editorfonts
            from gen3tl.lang import langpack

            cwd = Path.cwd()
            fake = Profile({"rom": {"sha1": "ab" * 20},
                            "font": [{"name": "font0", "glyphs": 0, "widths": 0, "count": 256}],
                            "font_style": {"style": "main", "cell_height": 14}},
                           cwd / "test-1.0.toml")
            Profile.by_name = classmethod(lambda cls, name: fake)
            editorfonts.TOOLKIT_ROOT = cwd
            langpack.TOOLKIT_ROOT = cwd
            with Path("commands.jsonl").open("a") as stream:
                stream.write(json.dumps(["python", *sys.argv[1:]]) + "\\n")
            raise SystemExit(main.main(sys.argv[3:]))
            '''))
        python.chmod(0o755)
        git = self.root / "git"
        git.write_text(f"#!{sys.executable}\n" + textwrap.dedent('''\
            import json
            import sys
            from pathlib import Path
            args = sys.argv[1:]
            with Path("commands.jsonl").open("a") as stream:
                stream.write(json.dumps(["git", *args]) + "\\n")
            if args[:2] == ["rev-parse", "--abbrev-ref"]:
                print("main")
            elif args[:1] == ["status"]:
                print(" M assets/lang/xx/fonts/main.txt")
            elif args[:3] == ["checkout", "-q", "-B"]:
                pass
            elif args[0] == "add":
                manifest = Path(args[1].split("=", 1)[1]).read_text()
                assert "assets/lang/xx/fonts/main.txt\\n" in manifest, manifest
                Path("staged-art.txt").write_text(Path("assets/lang/xx/fonts/main.txt").read_text())
            elif "commit" in args:
                pass
            elif args[0] == "checkout":
                pass
            else:
                print("unexpected git args: " + " ".join(args), file=sys.stderr)
                sys.exit(2)
            '''))
        git.chmod(0o755)
        mktemp = self.root / "mktemp"
        mktemp.write_text(f"#!{sys.executable}\n" + textwrap.dedent('''\
            import tempfile
            from pathlib import Path
            Path(".tmp").mkdir(exist_ok=True)
            print(tempfile.mkdtemp(dir=".tmp"))
            '''))
        mktemp.chmod(0o755)

    def run_workflow(self, export):
        event = self.root / "event.json"
        event.write_text(json.dumps({"issue": {"number": 9, "body": form(export),
                                               "user": {"login": "contributor"}}}), encoding="utf-8")
        self.write_mocks()
        script = toolkit_file(ROOT / ".github/scripts/toolkit-submission.sh")
        return subprocess.run(["bash", str(script)], cwd=self.root, capture_output=True, text=True,
                              env={**os.environ, "PYTHONPATH": str(ROOT / "src"),
                                   "PATH": str(self.root) + os.pathsep + os.environ["PATH"],
                                   "PYTHON": str(self.root / "python"), "GITHUB_EVENT_PATH": str(event),
                                   "SUBMISSION_DRY_RUN": "1"})

    def test_a_glyph_export_changes_the_pack_art_on_the_submission_branch(self):
        result = self.run_workflow(self.export())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotEqual(self.art.read_text(encoding="utf-8"), self.original)
        self.assertIn("submission: #9 -> (dry run)", result.stdout)
        self.assertIn("gh pr create", result.stderr)
        commands = [json.loads(line) for line in (self.root / "commands.jsonl").read_text().splitlines()]
        imports = [c for c in commands if c[:3] == ["python", "-m", "gen3tl"] and "web" in c and "import" in c]
        self.assertEqual(len(imports), 2)
        self.assertIn("--dry-run", imports[0])
        self.assertNotIn("--dry-run", imports[1])
        checkout = commands.index(["git", "checkout", "-q", "-B", "submission/9"])
        stage = next(i for i, c in enumerate(commands) if c[:2] == ["git", "add"])
        self.assertLess(checkout, stage)
        self.assertEqual((self.root / "staged-art.txt").read_text(encoding="utf-8"),
                         self.art.read_text(encoding="utf-8"))

    def test_a_versioned_glyph_export_reaches_the_pack_branch(self):
        doc = {**json.loads(self.export()), "version": exportenvelope.VERSION}
        result = self.run_workflow(json.dumps(doc))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotEqual(self.art.read_text(encoding="utf-8"), self.original)
        self.assertIn("gh pr create", result.stderr)

    def test_pack_fallback_cannot_bypass_envelope_validation(self):
        original = json.loads(self.export())
        major, minor, patch_version = map(int, exportenvelope.VERSION.split("."))
        unsupported = f"{major + 1}.0.0"
        cases = [(dict(original, version=unsupported), unsupported, "unsupported major version")]
        for version in (None, f"{major}.{minor + 1}.{patch_version}"):
            doc = {key: value for key, value in original.items() if key != "language"}
            if version:
                doc["version"] = version
            cases.append((doc, version or exportenvelope.VERSION, "missing required field: language"))
        for doc, version, reason in cases:
            with self.subTest(version=version, reason=reason):
                (self.root / "commands.jsonl").unlink(missing_ok=True)
                result = self.run_workflow(json.dumps(doc))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.art.read_text(encoding="utf-8"), self.original)
                self.assertEqual(result.stderr.count("error:"), 1)
                self.assertIn(f'version "{version}"; supported {exportenvelope.VERSION}: {reason}', result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertNotIn("gh pr create", result.stderr)
                commands = [json.loads(line) for line in (self.root / "commands.jsonl").read_text().splitlines()]
                self.assertTrue(any(c[:2] == ["python", "-"] for c in commands))
                self.assertFalse(any("import" in c or c[0] == "git" for c in commands))
