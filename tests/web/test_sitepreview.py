import contextlib
import hashlib
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from gen3tl import TOOLKIT_ROOT
from gen3tl.cli.web import site_assemble
from gen3tl.web import sitecatalog, sitepreview
from gen3tl.web.pokedexshare import png_pixels

WEB = TOOLKIT_ROOT / "web"

class Head(HTMLParser):
    def __init__(self, document):
        super().__init__()
        self.tags = {}
        self.title = ""
        self.in_title = False
        self.feed(document.split("</head>")[0])

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "title":
            self.in_title = True
        key = attrs.get("property", attrs.get("name")) if tag == "meta" else attrs.get("rel") if tag == "link" else None
        if key:
            self.tags.setdefault(key, []).append(attrs.get("content", attrs.get("href")))

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data

class SitePreviewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        (self.data / "languages.json").write_text(json.dumps({"bundle": "check.zip", "profiles": [], "languages": []}))

    def assemble(self, name, base=None):
        out = self.root / name
        args = SimpleNamespace(data=self.data, out=out, public_base=base)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = site_assemble(args)
        self.assertEqual(result, 0, output.getvalue())
        return out

    def test_every_assembled_page_has_absolute_preview_addresses(self):
        images = []
        for index, base in enumerate(("https://site.test", "https://site.test/subpath/")):
            out = self.assemble(f"site-{index}", base)
            prefix = base.rstrip("/") + "/"
            pages = sorted(out.glob("*.html"))
            self.assertEqual([path.name for path in pages], sorted(path.name for path in WEB.glob("*.html")))
            shared = set()
            for path in pages:
                with self.subTest(base=base, page=path.name):
                    head = Head(path.read_text(encoding="utf-8"))
                    row = sitepreview.texts(path.name, WEB)
                    self.assertEqual(head.title, row["title"])
                    for tag in ("og:title", "twitter:title"):
                        self.assertEqual(head.tags[tag], [row["title"]])
                    for tag in ("description", "og:description", "twitter:description"):
                        self.assertEqual(head.tags[tag], [row["description"]])
                    canonical = prefix + ("" if path.name == "index.html" else path.stem)
                    for tag in ("canonical", "og:url"):
                        self.assertEqual(head.tags[tag], [canonical])
                    self.assertEqual(head.tags["og:type"], ["website"])
                    self.assertEqual(head.tags["og:site_name"], ["gen3tl"])
                    self.assertEqual(head.tags["twitter:card"], ["summary_large_image"])
                    image = head.tags["og:image"][0]
                    self.assertTrue(image.startswith(prefix + "share/assets/"))
                    self.assertEqual(head.tags["twitter:image"], [image])
                    shared.add(image.removeprefix(prefix))
                    self.assertEqual(head.tags["og:image:type"], ["image/png"])
                    self.assertEqual(head.tags["og:image:width"], ["1200"])
                    self.assertEqual(head.tags["og:image:height"], ["630"])
            self.assertEqual(len(shared), 1)
            image = out / shared.pop()
            content = image.read_bytes()
            self.assertEqual(content[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(png_pixels(content)[:2], (1200, 630))
            self.assertEqual(image.stem, hashlib.sha256(content).hexdigest())
            images.append(content)
        self.assertEqual(*images)

    def test_without_public_base_has_text_and_no_address_or_image_tags(self):
        out = self.assemble("local")
        for page in out.glob("*.html"):
            with self.subTest(page=page.name):
                head = Head(page.read_text(encoding="utf-8"))
                self.assertTrue(head.title)
                self.assertTrue(head.tags["description"][0])
                self.assertEqual(head.tags["og:title"], [head.title])
                self.assertEqual(head.tags["og:description"], head.tags["description"])
                self.assertEqual(head.tags["twitter:card"], ["summary"])
                self.assertNotIn("canonical", head.tags)
                self.assertNotIn("og:url", head.tags)
                self.assertFalse(any(key.startswith(("og:image", "twitter:image")) for key in head.tags))
                self.assertFalse(any(value.startswith(("https://", "http://", "//"))
                                     for key, values in head.tags.items()
                                     if key in {"description", "canonical"} or key.startswith(("og:", "twitter:"))
                                     for value in values))
        self.assertFalse((out / "share/assets").exists())


    def test_language_specific_guides_use_their_catalog(self):
        for page in WEB.glob("*.html"):
            guide = sitepreview.GUIDE.fullmatch(page.name)
            if not guide or not guide[1]:
                continue
            rows = {entry.msgid: entry.msgstr for entry in sitecatalog.load(WEB / "i18n" / f"{guide[1]}.po")}
            original = sitepreview.definitions(WEB)[page.name.replace(f".{guide[1]}.html", ".html")]
            expected = {key: rows[value] for key, value in original.items()}
            head = Head(sitepreview.rewrite(page.read_text(encoding="utf-8"), page.name, WEB))
            self.assertEqual(head.title, expected["title"])
            self.assertEqual(head.tags["description"], [expected["description"]])

    def test_metadata_escaping_preserves_other_head_markup(self):
        row = {"title": 'Title & "quoted" <text>', "description": 'Description </head><script> & "text".'}
        source = '<html><head><title data-ui="Title">Title</title><meta charset="utf-8"></head><body>Body</body></html>'
        with mock.patch.object(sitepreview, "texts", return_value=row):
            rendered = sitepreview.rewrite(source, "patch.html", WEB, "https://site.test/a&b", "share/assets/image.png")
        head = Head(rendered)
        self.assertEqual(head.title, row["title"])
        self.assertEqual(head.tags["description"], [row["description"]])
        self.assertEqual(head.tags["canonical"], ["https://site.test/a&b/patch"])
        self.assertIn('<title data-ui="Title">', rendered)
        self.assertIn('<meta charset="utf-8">', rendered)
        self.assertNotIn("<script>", rendered)
        self.assertEqual(rendered.split("</head>")[1], "<body>Body</body></html>")

    def test_invalid_bases_and_unlisted_pages_are_rejected(self):
        for base in ("relative", "//site.test", "https:///path", "ftp://site.test", "https://site.test/?q=1",
                     "https://site.test/#x", "https://name:secret@site.test", "https://site.test/\npath"):
            with self.subTest(base=base), self.assertRaises(ValueError):
                sitepreview.public_url(base)
        with self.assertRaisesRegex(ValueError, "missing preview texts"):
            sitepreview.rewrite("<head><title>Other</title></head>", "other.html", WEB)
