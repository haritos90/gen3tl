import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from itertools import product
from pathlib import Path
from unittest.mock import patch

from gen3tl.cli import main as cli
from gen3tl.web import editordata, editorsnapshot as snapshot, snapshotstore
from gen3tl.check import provenance, qa
from gen3tl.check.layout import Limit
from gen3tl.catalog.catalog import load_catalog
from gen3tl.build.font import FontTable
from gen3tl.lang import termbase
from gen3tl.catalog.po import PoEntry, write
from gen3tl.catalog.source import digest
from gen3tl.profile.profile import Profile
from gen3tl.core.rom import Rom
from gen3tl.core.charmap import Charmap
from project_fixture import literal, with_profile_notes, write_policy
from editorstore_support import decoded
from editordata_support import CTX, INV, PO, PROF, REACH, TERMS

class SourceNamesTest(unittest.TestCase):
    def rows(self, texts, terms):
        catalog = {f"s{i:07X}": (Path("fixture.po"), PoEntry(f"s{i:07X}", text))
                   for i, text in enumerate(texts)}
        limits = {sid: Limit("dialogue", 240, 2) for sid in catalog}
        cm = Charmap(dict(zip("'ΣσςЖжſıİK\u0307",
                              (0x18, 0x1F, 0x2F, 0x30, 0x31, 0x32, 0x33, 0x37, 0x38, 0x39, 0x3A))))
        with patch.object(snapshot, "Charmap", return_value=cm):
            rows = snapshot.source_rows(catalog, limits, lambda _: 6, terms=terms)
        return {entry.msgid: rows[sid][5]["names"] for sid, (_, entry) in catalog.items()}

    def test_ascii_letter_boundaries_inside_longer_words(self):
        terms = [termbase.Term("light", "term", "Light", "Beam"),
                 termbase.Term("symbol", "term", "Σ", "Sigma")]
        rows = self.rows(["2Light3", "ЖLightЖ", "xLight", "Lightx", "ſLight", "ΣΣ"], terms)
        self.assertEqual(rows["2Light3"], ["light"])
        self.assertEqual(rows["ЖLightЖ"], ["light"])
        for text in ("xLight", "Lightx", "ſLight"):
            self.assertEqual(rows[text], [], text)
        self.assertEqual(rows["ΣΣ"], ["symbol"])

    def test_index_matches_all_patterns_across_spellings_and_contexts(self):
        sources = ["Light", "LIGHT", "King's Rock", "King’s Rock", "Blue Stone", "Blue Bird",
                   "X-ray", "Unit2", "2nd", "E2", "C++", "(Light)", "123", "", " ",
                   "Σ", "Ж", "naïve", "éon", "Berry", "Run", "Go", "Stone", "e", "y",
                   "Key", "Ink", "Sky", "Blue\\nStone", "Blue\tStone", "Flare"]
        terms = [termbase.Term(f"term:{i}", "term", text, text) for i, text in enumerate(sources)]
        terms.extend([termbase.Term("sense~2", "term", "Nova", "Nova"),
                      termbase.Term("sense~1", "term", "Flare", "Flare"),
                      termbase.Term("sense~3", "term", "", "Empty"),
                      termbase.Term("mixed", "term", "Light", "Beam"),
                      termbase.Term("mixed~1", "term", "123", "Number")])
        variants = [text for text in sources if text.strip() and "\t" not in text]
        variants += ["stones", "stoning", "berries", "berried", "berrying", "running", "runned",
                     "goes", "going", "Blue\\pStones", "Blue  Stone", "Blue\\lBird",
                     "Key", "ınk", "İNK", "ſky", "σ", "ς", "naïving", "EON", "Nova", "flares",
                     "ring", "lightning", "Blue Bird and Blue Stone", "ink and sky and key"]
        texts = dict.fromkeys(prefix + text + suffix for text, prefix, suffix in product(
            variants, ("", "x", "2", "Ж", "ſ", "("), ("", "x", "2", "Ж", ")")))
        texts.update((termbase._english_text(text).upper(), None) for text in variants)
        texts.update((termbase._english_text(text).lower(), None) for text in variants)
        senses = termbase.Senses(terms)
        patterns = [(term.id, termbase.english_pattern(tuple(senses.source_text.get(term.id.split("~")[0], []))
                                                      or ((term.source_text,) if term.source_text else ())))
                    for term in terms]
        for text, actual in self.rows(texts, terms).items():
            expected = [tid for tid, pattern in patterns
                        if pattern is not None and pattern.search(termbase._english_text(text))]
            with self.subTest(text=text):
                self.assertEqual(actual, expected)

class SnapshotStorageTest(unittest.TestCase):
    def test_shared_records_changes_and_new_languages_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a, b, c = [root / f"{name}.snapshot.json" for name in ("aa", "bb", "cc")]
            first = {"inventory": [{"id": "entry", "refs": [1, 2]}],
                     "source": {"a/b~c": ["digest", {"width": 20, "terms": []}]},
                     "language": "aa", "extra": {"only": 1}, "null": None}
            second = copy.deepcopy(first)
            second["language"] = "bb"
            second["source"]["a/b~c"][1] = {"width": 30, "terms": ["term"]}
            second["extra"] = {}
            snapshot.write(a, first)
            snapshot.write(b, second)
            shared = json.loads((root / "shared.json").read_text())
            self.assertEqual(shared["data"]["inventory"], first["inventory"])
            changes = json.loads(b.read_text())["changes"]
            self.assertNotIn("/inventory", changes)
            self.assertEqual(changes["/source/a~1b~0c/1/width"], 30)
            self.assertEqual(snapshot.load(a), first)
            self.assertEqual(snapshot.load(b), second)
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            snapshot.write(c, dict(first, language="cc"))
            self.assertEqual(snapshot.load(c), dict(first, language="cc"))
            self.assertEqual(snapshot.load(a), first)
            self.assertEqual(snapshot.load(b), second)
            snapshotstore.remove(c)
            self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, before)
            snapshot.write(a, first)
            self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, before)
            first["inventory"][0]["refs"].append(3)
            snapshot.write(a, first)
            self.assertEqual(snapshot.load(a), first)
            self.assertEqual(snapshot.load(b), second)

    def test_storage_is_independent_of_generation_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs = {"aa": {"rows": {"one": [1, 2], "two": [3, None]}},
                    "bb": {"rows": {"one": [1, 4], "three": [5]}},
                    "cc": {"rows": {"one": [1, 6], "two": [7, None]}}}
            for folder, codes in (("forward", list(docs)), ("reverse", list(reversed(docs)))):
                for code in codes:
                    snapshot.write(root / folder / f"{code}.snapshot.json", docs[code])
            self.assertEqual({p.name: p.read_bytes() for p in (root / "forward").iterdir()},
                             {p.name: p.read_bytes() for p in (root / "reverse").iterdir()})

    def test_old_storage_and_invalid_changes_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "aa.snapshot.json"
            snapshot.write(path, {"rows": [1]})
            path.write_text(json.dumps({"rows": [1]}))
            with self.assertRaisesRegex(ValueError, "storage format differs"):
                snapshot.load(path)
            for pointer in ("rows", "/rows/4", "/rows/-1", "/missing/value"):
                path.write_text(json.dumps({"format": snapshotstore.FORMAT, "changes": {pointer: 2}}))
                with self.subTest(pointer=pointer), self.assertRaisesRegex(ValueError, "invalid snapshot pointer"):
                    snapshot.load(path)
            with self.assertRaisesRegex(ValueError, "reserved"):
                snapshot.write(path.with_name("shared.json"), {})

class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.po = self.root / "translation/po"
        for name, entries in PO.items():
            write(self.po / name, copy.deepcopy(entries), {"Project-Id-Version": "test", "Language": "ru"})
        self.catalog = load_catalog(self.po)
        self.prof = Profile(copy.deepcopy(PROF.data), self.root / "test.toml")
        self.rom = Rom(bytes(0x14000))
        for font in FontTable.from_profile(self.prof):
            self.rom.data[font.widths:font.widths + font.count] = bytes([6]) * font.count
        self.rom.data[0xF000:0xF004] = (0x0800F100).to_bytes(4, "little")
        self.rom.data[0xF100:0xF104] = (0x08000100).to_bytes(4, "little")
        self.rom.data[0xF104:0xF108] = (1234).to_bytes(4, "little")
        self.prof.data["layout"]["record_window"] = [{
            "name": "record", "table": 0xF000, "count": 1, "width": 77, "lines": 2,
            "texts": [{"path": [0], "number": {"path": [4], "slots": ["STR_VAR_1"]}}]}]
        self.prof.data["rom"]["sha1"] = self.rom.sha1()
        self.identity = {"profile": self.prof.name, "rom_sha1": self.prof.sha1}
        self.inv = {**copy.deepcopy(INV), **self.identity}
        self.inv["entries"][0]["refs"].append({"at": 0xF100, "kind": "table"})
        self.ctx = {**copy.deepcopy(CTX), **self.identity}
        self.reach = {**copy.deepcopy(REACH), **self.identity}
        self.doc = snapshot.extract(self.prof, "ru", self.rom, self.inv, self.ctx, self.reach,
                                    self.catalog, self.po)

    def validate(self, doc=None):
        snapshot.validate(self.doc if doc is None else doc, self.prof, "ru", self.catalog, self.po)


    def test_stored_facts_name_rules_by_code_and_omit_defaults(self):
        path = self.root / "aa.snapshot.json"
        snapshot.write(path, self.doc)
        stored = json.loads(path.with_name("shared.json").read_text())
        rows = stored["tables"]["limit_sources"]
        self.assertTrue(rows and all(isinstance(row["rule"], dict) and row["rule"]["code"] in provenance.MESSAGES
                                     for row in rows))
        text = path.with_name("shared.json").read_text()
        for phrase in ("the English: ", "measured in game", '"table":null', '"flags":[]', '"trainer_role":null'):
            self.assertNotIn(phrase, text)
        self.assertFalse(any("source_sha256" in row for row in stored["data"]["inventory"]))   # the source rows hold them
        self.assertEqual(snapshot.load(path), json.loads(json.dumps(self.doc)))
        loaded = [source[1] for row in snapshot.load(path)["source"].values() for source in row[5]["sources"].values()]
        self.assertTrue(loaded and all(rule["code"] in provenance.MESSAGES for rule in loaded))   # read back as codes

    def test_limit_sources_are_independent_of_the_write_order(self):
        docs = {code: dict(self.doc, language=code) for code in ("de", "es", "fr", "ru")}
        for folder, codes in (("forward", list(docs)), ("reverse", list(reversed(docs)))):
            for code in codes:
                snapshot.write(self.root / folder / f"{code}.snapshot.json", docs[code])
        shared = json.loads((self.root / "forward/shared.json").read_text())
        self.assertGreater(len(shared["tables"]["limit_sources"]), 1)
        self.assertEqual({p.name: p.read_bytes() for p in (self.root / "forward").iterdir()},
                         {p.name: p.read_bytes() for p in (self.root / "reverse").iterdir()})

    def test_shared_fact_edits_fail_the_existing_checksum(self):
        path = self.root / "aa.snapshot.json"
        snapshot.write(path, self.doc)
        shared = path.with_name("shared.json")
        doc = json.loads(shared.read_text())
        doc["data"]["fonts"][next(iter(doc["data"]["fonts"]))]["widths"][0] += 1
        shared.write_text(json.dumps(doc))
        with self.assertRaisesRegex(ValueError, "facts checksum differs"):
            self.validate(snapshot.load(path))


    def test_status_changes_require_regeneration_when_projected_text_changes(self):
        self.prof.data["layout"]["record_window"][0]["evidence"] = "Measured window."
        original = copy.deepcopy(self.prof.data)
        self.prof.data = with_profile_notes(original, ("unreviewed", "accepted"))
        self.doc = snapshot.extract(self.prof, "ru", self.rom, self.inv, self.ctx, self.reach,
                                    self.catalog, self.po)
        self.prof.data = with_profile_notes(original, ("accepted", "accepted"))
        self.validate()
        self.prof.data = with_profile_notes(original, ("superseded", "accepted"))
        with self.assertRaisesRegex(ValueError, "configuration_sha256 differs"):
            self.validate()

    def test_non_catalog_inventory_keeps_offsets_and_digests_without_text(self):
        sid = self.inv["entries"][0]["id"]
        del self.catalog[sid]
        doc = snapshot.extract(self.prof, "ru", self.rom, self.inv, self.ctx, self.reach,
                               self.catalog, self.po)
        row = doc["inventory"][0]
        self.assertEqual(row["offset"], self.inv["entries"][0]["offset"])
        self.assertEqual(row["source_sha256"], digest(self.inv["entries"][0]["text"]))
        self.assertNotIn("text", row)
        inv, _, _, _ = snapshot.inputs(doc, self.catalog, self.prof)
        self.assertEqual(inv["entries"][0]["text"], "")


    def test_inventory_digest_must_match_catalog_source_facts(self):
        for value in ("invalid", digest("Another source")):
            doc = copy.deepcopy(self.doc)
            doc["inventory"][0]["source_sha256"] = value
            doc["facts_sha256"] = snapshot.digest({k: v for k, v in doc.items() if k != "facts_sha256"})
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "digest"):
                self.validate(doc)


    def test_identity_source_paths_and_configuration_are_bound(self):
        for key in ("profile", "language", "rom_sha1", "source_sha256", "configuration_sha256", "schema"):
            with self.subTest(key=key):
                changed = copy.deepcopy(self.doc)
                changed[key] = "wrong"
                with self.assertRaisesRegex(ValueError, key):
                    self.validate(changed)
        sid = next(iter(self.catalog))
        path, entry = self.catalog[sid]
        self.catalog[sid] = (path.with_name("moved.po"), entry)
        with self.assertRaisesRegex(ValueError, "source_sha256"):
            self.validate()
        self.catalog[sid] = (path, entry)
        entry.msgid += " changed"
        with self.assertRaisesRegex(ValueError, "source_sha256"):
            self.validate()

    def test_source_rows_carry_the_facts_of_the_english_and_bind_it_by_digest(self):
        path, entry = self.catalog["s0000100"]
        measure, cm, _ = snapshot.measures(self.prof, "ru", self.doc["fonts"])
        inv, ctx, _, facts = snapshot.inputs(self.doc, self.catalog, self.prof)
        lim = qa.compute_limits(self.prof, inv, self.catalog, measure, cm, ctx, english_slots=True,
                                facts=facts)["s0000100"]
        self.assertEqual(self.doc["source"]["s0000100"][:5],
                         [digest(entry.msgid), ["{PLAYER}", "{STR_VAR_1}"], [1], [17 * 6],
                          [lim.kind, lim.width, lim.lines, lim.chars, lim.bytes, lim.keys, lim.style]])
        self.assertEqual(set(self.doc["source"]), set(self.catalog))
        english = entry.msgid
        entry.msgid += " changed"
        with self.assertRaisesRegex(ValueError, "the English of 1 entries differs from the snapshot digests: s0000100"):
            self.validate()
        # a key-only entry is bound by the digest
        entry.msgid, entry.keyed = entry.msgctxt, True
        self.validate()
        entry.msgid, entry.keyed = english, False

    def test_corrupt_and_unknown_facts_are_rejected(self):
        for mutate in (lambda d: d.update(pixels="original artwork"),
                       lambda d: d["fonts"]["unbound"].update(pixels=[]),
                       lambda d: d["fonts"]["unbound"]["shadows"].__setitem__(0, 1),
                       lambda d: d["fonts"]["unbound"]["widths"].__setitem__(0, 7)):
            changed = copy.deepcopy(self.doc)
            mutate(changed)
            with self.assertRaises(ValueError):
                self.validate(changed)

    def test_context_names_carry_catalog_keys_without_game_text(self):
        from gen3tl.catalog.keys import Aliases
        aliases = Aliases([("table:map_sections/1", None, "s0000900"),
                           ("table:trainer_names/1", None, "s0000A00")])
        for sid, (_, entry) in self.catalog.items():
            entry.msgctxt = aliases.key(sid)
        doc = snapshot.extract(self.prof, "ru", self.rom, self.inv, self.ctx, self.reach,
                               self.catalog, self.po)
        ctx = doc["source"]["s0000100"][5]["context"]
        self.assertEqual(ctx["maps"], ["table:map_sections/1"])
        self.assertEqual(ctx["speakers"], [
            {"name": None, "gender": "female", "source": "text colour"},
            {"name": None, "gender": "female", "source": "label"},
            {"name": "table:trainer_names/1", "gender": "female", "source": "trainer"},
        ])
        self.assertEqual(doc["source"]["s0000200"][5]["context"]["maps"], ["table:map_sections/1"])
        for name in ("Bellin Town", "Ann", "Sally", "Lass"):
            self.assertNotIn(name, json.dumps(doc))


    def test_keyed_limits_recalculate_slots_centres_and_prefixes_after_edits(self):
        from gen3tl.catalog.po import key_only
        self.prof.data["layout"]["record_window"][0]["texts"][0].pop("number")
        self.prof.data["layout"]["window_width"].pop("s0000300")
        self.prof.data["layout"]["table_window"] = [
            {"base": 0x310, "count": 1, "stride": 4, "width": 76,
             "centre": {"reference": "s0000400", "step": 6}},
            {"base": 0x510, "count": 1, "stride": 4, "width": 80,
             "strip": [{"reference": "s0000200", "chars": 5}]}]
        doc = snapshot.extract(self.prof, "ru", self.rom, self.inv, self.ctx, self.reach, self.catalog, self.po)
        measure, cm, _ = snapshot.measures(self.prof, "ru", doc["fonts"])
        for changes in ({"s0000200": "Суперзелье", "s0000300": "Сум"},
                        {"s0000200": "Зелье", "s0000400": "Микстура"},
                        {"s0000200": "", "s0000300": "", "s0000400": ""}):
            with self.subTest(changes=changes):
                for sid, text in changes.items():
                    self.catalog[sid][1].msgstr = text
                keyed = {sid: (p, key_only(e)) for sid, (p, e) in self.catalog.items()}
                snapshot.validate(doc, self.prof, "ru", keyed, self.po)
                offline = snapshot.limits(doc, keyed, self.prof)
                live = qa.compute_limits(self.prof, self.inv, self.catalog, measure, cm, self.ctx, rom=self.rom)
                translations = {e.msgstr for _, e in self.catalog.values() if e.msgstr}
                for sid in keyed:
                    self.assertEqual(editordata.window(sid, offline[sid], "unbound", translations=translations),
                                     editordata.window(sid, live[sid], "unbound", translations=translations), sid)


    def test_extraction_refuses_unidentified_analyses_and_changed_sources(self):
        self.ctx.pop("profile")
        with self.assertRaisesRegex(ValueError, "context.*identity"):
            snapshot.extract(self.prof, "ru", self.rom, self.inv, self.ctx, self.reach, self.catalog, self.po)
        self.ctx.update(self.identity)
        self.catalog["s0000200"][1].msgid = "Changed source"
        with self.assertRaisesRegex(ValueError, "source differs"):
            snapshot.extract(self.prof, "ru", self.rom, self.inv, self.ctx, self.reach, self.catalog, self.po)

    def test_cli_builds_without_rom_or_build_analyses(self):
        self.prof.path.write_text("\n".join(f"{key} = {literal(value)}" for key, value in self.prof.data.items()))
        write_policy(self.root, self.prof)
        (self.root / "projects.toml").write_text(
            '[defaults]\nprofile="test"\nlanguage="ru"\n[[project]]\nprofile="test"\nlanguage="ru"\n'
            'root="."\npolicy="policy.toml"\n')
        from gen3tl.project import Registry
        selected = Registry.load(self.root).projects[0].effective(self.prof)
        self.doc = snapshot.extract(selected, "ru", self.rom, self.inv, self.ctx, self.reach, self.catalog, self.po)
        snapshot.write(self.root / "translation/editor-inputs.json", self.doc)
        (self.root / "translation/release.toml").write_text('version="1.2.3"\n')
        self.assertFalse((self.root / "build").exists())
        self.assertFalse((self.root / "rom").exists())
        args = ["--project-root", str(self.root), "--profile", str(self.prof.path),
                "web", "data", "--no-rom"]
        with patch.object(Profile, "load_rom", side_effect=AssertionError("ROM access forbidden")):
            with redirect_stdout(io.StringIO()):
                self.assertIsNone(cli.main(args))
                self.assertIsNone(cli.main(args + ["--split"]))
                staged = self.root / "build/stage/view"
                self.assertIsNone(cli.main(args + ["--split", "--language-only", "--out", str(staged)]))
        doc = json.loads((self.root / "build/editor/ru.json").read_text())
        self.assertEqual(doc["version"], "1.2.3")
        self.assertEqual(len(doc["entries"]), len(self.catalog))
        index = json.loads((self.root / "build/editor/test/ru/index.json").read_text())
        self.assertEqual(index["version"], "1.2.3")
        self.assertEqual(json.loads((staged / "index.json").read_text()), index)
        self.assertEqual(sorted(staged.parent.iterdir()), [staged.parent / "shared", staged])


    def test_missing_original_fragments_preserve_keyed_widths(self):
        self.prof.data["layout"].update(
            fill_source={"player": {"label": "Player name", "font": "font0",
                                    "value": {"entries": ["species:1"]}, "evidence": "Verified field"}},
            slot_source={"PLAYER": "player"})
        doc = snapshot.extract(self.prof, "ru", self.rom, self.inv, self.ctx, self.reach, self.catalog, self.po)
        doc["records"]["fill_fragments"] = {}
        for _, entry in self.catalog.values():
            entry.keyed = True
            entry.msgid = entry.msgctxt
        doc["facts_sha256"] = snapshot.digest({k: v for k, v in doc.items() if k != "facts_sha256"})
        snapshot.validate(doc, self.prof, "ru", self.catalog, self.po)
        limits = snapshot.limits(doc, self.catalog, self.prof)
        detail = limits["s0000100"].slot_details["PLAYER"]
        self.assertEqual((detail["width"], detail["state"]), (42, "recorded"))
        self.assertIn("original measurement unavailable", detail["unavailable"])

    def test_key_only_catalogs_build_from_the_snapshot_facts(self):
        from test_languagerules import fixture
        declared = fixture()
        self.prof.data["table"].extend(declared["table"])
        self.prof.data["language_rules"] = declared["language_rules"]
        # Missing English must preserve every editor field.
        self.prof.data["layout"]["window_width"].pop("s0000300")
        self.prof.data["layout"]["table_window"] = [
            {"name": "centred", "base": 0x310, "count": 1, "stride": 4, "width": 76,
             "centre": {"reference": "s0000400", "step": 6}},
            {"name": "prefix", "base": 0x510, "count": 1, "stride": 4, "width": 80,
             "strip": [{"reference": "s0000200", "chars": 5}]}]
        self.prof.data["layout"]["record_window"][0]["texts"][0].pop("number")
        self.prof.path.write_text("\n".join(f"{key} = {literal(value)}" for key, value in self.prof.data.items()))
        write_policy(self.root, self.prof)
        (self.root / "projects.toml").write_text(
            '[defaults]\nprofile="test"\nlanguage="ru"\n[[project]]\nprofile="test"\nlanguage="ru"\n'
            'root="."\npolicy="policy.toml"\n')
        from gen3tl.catalog.po import KEY_ONLY, SOURCE_FIELD, read
        from gen3tl.lang import termbase
        from gen3tl.project import Registry
        selected = Registry.load(self.root).projects[0].effective(self.prof)
        termbase.save(self.root / "translation/glossary.tsv", TERMS)
        doc = snapshot.extract(selected, "ru", self.rom, self.inv, self.ctx, self.reach, self.catalog, self.po, TERMS)
        snapshot.write(self.root / "translation/editor-inputs.json", doc)
        (self.root / "translation/release.toml").write_text('version="1.2.3"\n')
        args = ["--project-root", str(self.root), "--profile", str(self.prof.path), "web", "data", "--split"]
        (self.root / "build").mkdir()
        for name, data in (("inventory", self.inv), ("context", self.ctx), ("reach", self.reach)):
            (self.root / "build" / f"{name}.json").write_text(json.dumps(data))

        def build(name, offline=True):
            out = io.StringIO()
            with patch.object(Profile, "load_rom", return_value=self.rom,
                              side_effect=AssertionError("ROM access forbidden") if offline else None), \
                    redirect_stdout(out):
                self.assertIsNone(cli.main(args + (["--no-rom"] if offline else [])
                                           + ["--out", str(self.root / name / "ru")]))
            lang = self.root / name / "ru"
            data = decoded(lang)
            return (data["index"], {e["id"]: e for c in data["chunks"] for e in c["entries"]}, out.getvalue(), data)
        index, english, _, original = build("source_text", offline=False)
        for path in sorted(self.po.rglob("*.po")):
            header, entries = read(path)
            write(path, entries, {**header, SOURCE_FIELD: KEY_ONLY})
        self.assertNotIn("Take this", "".join(p.read_text() for p in self.po.rglob("*.po")))
        keyed, facts, said, lang = build("keyed")
        self.assertIn("english      snapshot facts", said)
        for sid, e in facts.items():
            with self.subTest(sid=sid):
                self.assertEqual(e, english[sid])
                self.assertEqual(e["source_sha256"], doc["source"][sid][0])
        for row in index["list"]:
            row["problems"] = [p for p in row["problems"]
                               if provenance.unnoted(p["message"])["code"] != "glossary_missing"]
        self.assertEqual(keyed, index)
        self.assertEqual(keyed["engine_rules"]["rules"][0]["id"], "plural")
        self.assertEqual(len(keyed["engine_rules"]["rules"][0]["operands"][0]["bindings"]), 2)
        self.assertEqual(lang["terms"], original["terms"])
        self.assertEqual(facts["s0000200"]["terms"], ["item:potion"])
        self.assertEqual(facts["s0000100"]["context"]["words"],
                         [{"slot": "{STR_VAR_1}", "id": "s0000200", "translation": "Зелье"}])
        self.assertEqual(facts["s0000100"]["context"]["maps"], ["s0000900"])
        self.assertEqual(facts["s0000200"]["context"]["maps"], ["s0000900"])
        self.assertIn({"name": "s0000A00", "gender": "female", "source": "trainer"},
                      facts["s0000100"]["context"]["speakers"])
        self.assertEqual(facts["s0000300"]["window"]["width"], 70)
        self.assertEqual(facts["s0000500"]["window"]["strip"], ["Зелье"])
        self.assertEqual(facts["s0000100"]["tokens"], ["{PLAYER}", "{STR_VAR_1}"])
        self.assertEqual([(r["id"], r["status"]) for r in keyed["list"]],
                         [(r["id"], r["status"]) for r in index["list"]])
        # the site holds no game line; check runs on tokens
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["--project-root", str(self.root), "--profile", str(self.prof.path), "web",
                                       "assemble", "--data", str(self.root / "keyed"), "--out",
                                       str(self.root / "site")]), 0)
        data = self.root / "site" / "data"
        self.assertTrue((data / "ru" / "index.json").is_file())
        text = "".join(p.read_text(encoding="utf-8") for p in data.rglob("*.json"))
        for line in ("Take this", "Ivysaur", "Bellin Town", "Ann", "Sally", "Lass"):
            self.assertNotIn(line, text)
        checker = snapshot.checker(keyed, doc["fonts"], {}, "1.2.3")
        result = checker.check(facts["s0000100"], "Привет!")
        self.assertEqual(result["problems"][0]["message"], provenance.code("placeholders_differ"))
        self.assertEqual(result["unavailable"], ["typography", "glossary"])
