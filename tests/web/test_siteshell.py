"""Shared page regions are complete, checked and reproducible."""

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from gen3tl.cli.main import main
from gen3tl.web import siteshell


def page(kind="footer"):
    return ('<main>Page content</main>\n<!-- site-shell: masthead -->\nold\n'
            '<!-- /site-shell: masthead -->\n'
            f'<!-- site-shell: {kind} -->\nold\n<!-- /site-shell: {kind} -->\n')


class ShellTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.web = Path(temporary.name)
        shutil.copytree(siteshell.WEB / "fragments", self.web / "fragments")
        self.path = self.web / "index.html"
        self.path.write_text(page(), encoding="utf-8")

    def command(self, *options):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            status = main(["web", "site-shell", "--web", str(self.web), *options])
        return status, output.getvalue(), errors.getvalue()

    def test_sync_check_and_named_drift(self):
        status, output, _ = self.command()
        self.assertEqual(status, 1)
        self.assertIn("index.html: out of sync", output)
        self.assertEqual(self.path.read_text(), page())
        self.assertEqual(self.command("--sync")[0], 0)
        synced = self.path.read_bytes()
        self.assertIn(b'<main>Page content</main>', synced)
        self.assertIn(b'class="mast-controls"', synced)
        self.assertIn(b'class="wrap"', synced)
        self.assertEqual(self.command()[0], 0)
        self.assertEqual(self.command("--sync")[0], 0)
        self.assertEqual(self.path.read_bytes(), synced)
        self.path.write_bytes(synced.replace(b'>gen3tl</a>', b'>Changed</a>'))
        other = self.web / "save.html"
        other.write_text(page(), encoding="utf-8")
        status, output, _ = self.command()
        self.assertEqual(status, 1)
        self.assertEqual(output.count("index.html:"), 1)
        self.assertEqual(output.count("save.html:"), 1)

    def test_missing_malformed_duplicate_and_unmarked_regions_fail(self):
        for bad in ("<main>Unmarked page</main>", page().replace("/site-shell: masthead", "/site-shell: footer"),
                    page() + page(), page() + '<header data-site-mast=""></header>'):
            with self.subTest(bad=bad):
                self.path.write_text(bad, encoding="utf-8")
                other = self.web / "save.html"
                other.write_text(page(), encoding="utf-8")
                status, output, _ = self.command("--sync")
                self.assertEqual(status, 1)
                self.assertIn("index.html:", output)
                self.assertEqual(self.path.read_text(), bad)
                self.assertEqual(other.read_text(), page())

    def test_fragments_reject_unknown_points(self):
        fragment = self.web / "fragments/masthead.html"
        fragment.write_text(fragment.read_text() + "{{misspelled}}", encoding="utf-8")
        status, output, _ = self.command()
        self.assertEqual(status, 1)
        self.assertIn("index.html: masthead: substitution points: misspelled", output)

    def test_check_and_sync_need_no_project_or_rom(self):
        with contextlib.chdir(self.web):
            self.assertEqual(self.command("--sync")[0], 0)
            self.assertEqual(self.command()[0], 0)

    def test_guide_body_and_static_licenses_remain_intact(self):
        source = page("footer-static")
        result = siteshell.rewrite(source, "guide.xx.html", self.web, [])
        self.assertTrue(result.startswith("<main>Page content</main>\n"))
        self.assertIn('href="guide.xx.html" aria-current="page"', result)
        self.assertIn("Content licence", result)

    def test_registry_licenses_are_resolved_escaped_and_validated(self):
        registry = self.web / "registry.json"
        self.assertIsNone(siteshell.registry_licenses(self.web))
        row = {"label_key": '<img src=x> & "Label"', "url": "../terms?x=1&y=2"}
        registry.write_text(json.dumps({"schema": 1, "games": [], "site": {"licenses": [row]}}))
        licenses = siteshell.registry_licenses(self.web)
        self.assertEqual(licenses, [{**row, "url": "terms?x=1&y=2"}])
        result = siteshell.rewrite(page(), "pokedex.html", self.web, licenses)
        self.assertEqual(result.count('href="terms?x=1&amp;y=2"'), 2)
        self.assertIn("&lt;img src=x&gt; &amp; &quot;Label&quot;", result)
        self.assertNotIn("<img", result)
        row["url"] = "javascript:alert(1)"
        registry.write_text(json.dumps({"schema": 1, "games": [], "site": {"licenses": [row]}}))
        with self.assertRaisesRegex(ValueError, "licenses"):
            siteshell.registry_licenses(self.web)


if __name__ == "__main__":
    unittest.main()
