import copy
import hashlib
import io
import json
import os
import stat
import struct
import tempfile
import tomllib
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from gen3tl.core.rom import ROM_BASE, Rom
from gen3tl.core.charmap import BASE_CHARS, Charmap
from gen3tl.core.thumb import ThumbError
from gen3tl.build.font import FontTable, encode_glyph
from gen3tl.cli.main import main
from gen3tl.discover import emission, foundations
from gen3tl.families.fingerprints import Fingerprint, find
from gen3tl.profile import schema
from gen3tl.profile.profile import Profile
from project_fixture import CATALOG_STAGE

def words(*values):
    return struct.pack("<" + "H" * len(values), *values)

def call(at, target):
    offset = (target - at - 4) & 0x7FFFFF
    return words(0xF000 | offset >> 12, 0xF800 | (offset >> 1 & 0x7FF))

class FingerprintTest(unittest.TestCase):
    def image(self):
        data = bytearray(b"\xff" * 512)
        data[0x20:0x28] = words(0x4802, 0x280A) + call(0x24, 0x100)
        data[0x28:0x2A] = words(0x4770)
        data[0x2C:0x30] = struct.pack("<I", ROM_BASE + 0x80)
        return data

    def test_relocated_code_literal_targets_and_calls_preserve_hash(self):
        before = self.image()
        fingerprint = Fingerprint.bootstrap(before, 0x20, 0x30, "reader", "width")
        moved = bytearray(b"\xff" * 512)
        moved[0x60:0x68] = words(0x4803, 0x280A) + call(0x64, 0x180)
        moved[0x68:0x6A] = words(0x4770)
        moved[0x70:0x74] = struct.pack("<I", ROM_BASE + 0xA0)
        relocated = Fingerprint.bootstrap(moved, 0x60, 0x70, "reader", "width")
        self.assertEqual(fingerprint.document(), relocated.document())
        self.assertEqual(find(moved, (fingerprint,)), {"reader": (0x60,)})

    def test_character_range_constants_remain_significant(self):
        before = self.image()
        fingerprint = Fingerprint.bootstrap(before, 0x20, 0x30, "reader", "width")
        changed = before.copy()
        changed[0x22:0x24] = words(0x280B)
        self.assertNotEqual(Fingerprint.bootstrap(changed, 0x20, 0x30, "reader", "width").sha256,
                            fingerprint.sha256)
        self.assertEqual(find(changed, (fingerprint,)), {"reader": ()})

    def test_ambiguous_matches_remain_explicit(self):
        data = self.image()
        fingerprint = Fingerprint.bootstrap(data, 0x20, 0x30, "reader", "width")
        data[0x60:0x70] = data[0x20:0x30]
        self.assertEqual(find(data, (fingerprint,)), {"reader": (0x20, 0x60)})

    def test_malformed_fingerprints_and_undefined_code_are_refused(self):
        fingerprint = Fingerprint.bootstrap(self.image(), 0x20, 0x30, "reader", "width")
        self.assertEqual(Fingerprint.read(fingerprint.document()), fingerprint)
        for field, value in (("length", 3), ("sha256", "bad"), ("prefix_count", True), ("entry", [None])):
            bad = copy.deepcopy(fingerprint.document())
            bad[field] = value
            with self.assertRaises(ValueError):
                Fingerprint.read(bad)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            find(self.image(), (fingerprint, fingerprint))
        with self.assertRaises(ThumbError):
            Fingerprint.bootstrap(words(0xDE00), 0, 2, "invalid", "width")

def header(data):
    config = foundations.families()[0].anchors["identity"]
    data[0xA0:0xC0] = bytes(32)
    data[0xA0:0xAC] = b"SYNTHETIC".ljust(12, b"\0")
    data[0xAC:0xB0] = config["game_code"].encode()
    data[0xB0:0xB2] = b"01"
    data[0xB2] = 0x96
    data[0xBD] = (-sum(data[0xA0:0xBD]) - 0x19) & 255
    at = config["header_offset"]
    struct.pack_into("<II", data, at, config["version"], config["language"])
    data[at + 8:at + 40] = config["name"].encode().ljust(32, b"\0")
    for index, offset in enumerate(config["pointer_offsets"]):
        struct.pack_into("<I", data, at + offset, ROM_BASE + 0x600 + index * 4)

class ClassificationTest(unittest.TestCase):
    def fixture(self):
        data = bytearray(b"\xff" * 4096)
        header(data)
        signatures = []
        for index, category in enumerate(("renderer", "width", "script", "naming", "extension")):
            at = 0x200 + index * 0x20
            data[at:at + 6] = words(0xB500, 0x2000 | index, 0xBD00)
            signatures.append(Fingerprint.bootstrap(data, at, at + 6, category, category))
        family = copy.deepcopy(foundations.families()[0])
        family.fingerprints = tuple(signatures)
        return Rom(data), family

    def test_confidence_requires_extension_and_header_not_just_hit_ratio(self):
        rom, family = self.fixture()
        result, _ = foundations.classify(rom, family, foundations.identify(rom))
        self.assertTrue(result["selected"])
        self.assertEqual((result["matched"], result["total"], result["ratio"]), (5, 5, 1))
        rom.data[0x280:0x286] = words(0xB500, 0x2006, 0xBD00)
        family.anchors["identity"]["retail_sha1"] = [rom.sha1()]
        result, _ = foundations.classify(rom, family, foundations.identify(rom))
        self.assertEqual(result["ratio"], 0.8)
        self.assertTrue(result["known_retail_hash"])
        self.assertFalse(result["selected"])
        rom, family = self.fixture()
        rom.put_u32(0x100, 0)
        result, _ = foundations.classify(rom, family, foundations.identify(rom))
        self.assertEqual(result["ratio"], 1)
        self.assertFalse(result["selected"])

    def test_identity_and_codec_use_measured_header_and_anchor_scores(self):
        rom, family = self.fixture()
        self.assertTrue(foundations.identify(rom)["header_checksum_valid"])
        cm, position = Charmap(), 0x800
        for text in family.anchors["codec"]["anchors"] + family.anchors["codec"]["control_anchors"]:
            encoded = cm.encode(text)
            rom.write(position, encoded)
            position += len(encoded) + 4
        selected = foundations.codec(rom, family)
        self.assertTrue(selected["selected"])
        self.assertEqual(selected["candidate"], "gen3-western")
        for anchor in selected["scores"][0]["control_anchors"]:
            rom.write(anchor["at"], bytes(len(cm.encode(anchor["text"]))))
        self.assertFalse(foundations.codec(rom, family)["selected"])
        rom.data[0xB2] = 0
        self.assertFalse(foundations.identify(rom)["fixed_byte_valid"])

    def test_bad_inventory_fails_before_search_or_emission(self):
        rom, family = self.fixture()
        for inventory in ([], {}, {"rom_sha1": "0" * 40, "entries": []}):
            with self.subTest(inventory=inventory), patch.object(foundations, "find") as searched:
                with self.assertRaisesRegex(ValueError, "inventory"):
                    foundations.run(rom, (family,), inventory)
                searched.assert_not_called()

def font_image():
    data = bytearray(b"\xff" * 0x40000)
    family = copy.deepcopy(foundations.families()[0])
    tables, next_table = [], 0x1000
    pixels = [[1] * 16 for _ in range(16)]
    letters = [byte for byte, char in BASE_CHARS.items() if char.isascii() and char.isalpha()]
    for declaration in family.engine["font"]:
        fmt, count = declaration.get("format", "16x16"), declaration["count"]
        size = len(encode_glyph(pixels, fmt))
        glyphs, widths = next_table, next_table + size * count
        data[glyphs:widths] = bytes(size * count)
        data[widths:widths + count] = bytes([5]) * count
        for byte in letters:
            data[glyphs + byte * size:glyphs + (byte + 1) * size] = encode_glyph(pixels, fmt)
        tables.append({"name": declaration["name"], "glyphs": glyphs, "widths": widths,
                       "count": count, "codes": list(declaration["codes"]),
                       **({"format": fmt} if fmt != "16x16" else {})})
        next_table = widths + count + 0x100
    for index, table in enumerate(tables):
        at = 0x300 + index * 0x20
        data[at:at + 8] = words(0x4802, 0x1880, 0x7800, 0x4770)
        struct.pack_into("<I", data, at + 12, ROM_BASE + table["widths"])
    signatures = [Fingerprint.bootstrap(data, 0x300, 0x310, "width", "width")]
    matches = {"width": tuple(0x300 + index * 0x20 for index in range(5))}
    formats = {}
    for name, start, selected, fmt in (("normal", 0x400, tables[:4], "16x16"),
                                       ("small", 0x480, tables[4:], "8x16")):
        pointers = [ROM_BASE + table[key] for table in selected for key in ("glyphs", "widths")]
        pool = start + 0x40
        for index, pointer in enumerate(pointers):
            at = start + index * 2
            immediate = (pool + index * 4 - ((at + 4) & ~3)) // 4
            data[at:at + 2] = words(0x4800 | immediate)
            struct.pack_into("<I", data, pool + index * 4, pointer)
        data[start + len(pointers) * 2:start + len(pointers) * 2 + 2] = words(0x4770)
        signatures.append(Fingerprint.bootstrap(data, start, start + 0x80, name, "renderer"))
        matches[name] = (start,)
        formats[name] = fmt
    functions = [0x380, 0x300, 0x320, 0x320, 0x340, 0x360, 0x2E0]
    data[0x2E0:0x2E2] = words(0x4770)
    for code, function in enumerate(functions):
        struct.pack_into("<II", data, 0x800 + code * 8, code, ROM_BASE + function + 1)
    family.fingerprints, family.formats = tuple(signatures), formats
    return Rom(data), family, matches, tables

class FontsTest(unittest.TestCase):
    def test_old_font_imports_share_the_unchanged_core_implementation(self):
        from gen3tl.build import font as legacy
        from gen3tl.core import font as shared

        for name in ("BACKGROUND", "FORMATS", "GLYPH_BYTES", "INK", "OUTSIDE", "SHADOW", "TILE_ORIGINS",
                     "FontTable", "by_style", "check_copies", "decode_glyph", "encode_glyph", "font_codes",
                     "glyph_size", "shadow_end"):
            self.assertIs(getattr(legacy, name), getattr(shared, name), name)
        self.assertEqual(legacy.write_glyphs.__module__, "gen3tl.build.font")

    def test_dispatch_literals_and_formats_derive_all_five_tables(self):
        rom, family, matches, expected = font_image()
        rows, proofs, issues = foundations.fonts(rom, family, matches)
        self.assertEqual(rows, expected)
        self.assertEqual(len(proofs), 5)
        self.assertEqual(issues, [])
        self.assertTrue(all(proof.evidence.method and proof.evidence.anchors and proof.evidence.code_sites for proof in proofs))
        self.assertTrue(all(dict(proof.evidence.counts)["round_trips"] == 512 for proof in proofs))

    def test_relocated_literal_targets_move_the_derived_tables(self):
        rom, family, matches, expected = font_image()
        moved = rom.copy()
        relocation = {}
        for table in FontTable.from_profile({"font": expected}):
            glyphs, widths = table.all_bytes(rom)
            moved.write(table.glyphs + 0x2000, glyphs)
            moved.write(table.widths + 0x2000, widths)
            relocation[ROM_BASE + table.glyphs] = ROM_BASE + table.glyphs + 0x2000
            relocation[ROM_BASE + table.widths] = ROM_BASE + table.widths + 0x2000
        for signature in family.fingerprints:
            for start in matches[signature.name]:
                for item in foundations.literal_loads(rom.data, start, start + signature.length):
                    if item.value in relocation:
                        moved.put_u32(item.pool, relocation[item.value])
                self.assertEqual(Fingerprint.bootstrap(moved.data, start, start + signature.length,
                                                        signature.name, signature.category).sha256, signature.sha256)
        rows, _, issues = foundations.fonts(moved, family, matches)
        self.assertEqual(issues, [])
        self.assertEqual([(r["glyphs"], r["widths"]) for r in rows],
                         [(r["glyphs"] + 0x2000, r["widths"] + 0x2000) for r in expected])

    def test_latin_parameter_excludes_an_equally_valid_japanese_table(self):
        rom, family, matches, expected = font_image()
        rom.write(0x300, words(0x2901, 0xD103, 0x4804, 0xE002, 0, 0, 0x4803, 0x1880, 0x7800, 0x4770))
        rom.put_u32(0x318, ROM_BASE + expected[1]["widths"])
        rom.put_u32(0x31C, ROM_BASE + expected[0]["widths"])
        family.fingerprints += (Fingerprint.bootstrap(rom.data, 0x300, 0x320, "branch-width", "width"),)
        matches["width"] = matches["width"][1:]
        matches["branch-width"] = (0x300,)
        rows, _, issues = foundations.fonts(rom, family, matches)
        self.assertEqual((rows, issues), (expected, []))

    def test_ambiguous_dispatch_and_corrupt_widths_remain_unresolved(self):
        rom, family, matches, tables = font_image()
        rom.write(0x840, rom.read(0x800, 56))
        rows, _, issues = foundations.fonts(rom, family, matches)
        self.assertEqual(rows, [])
        self.assertEqual(dict(issues[0].evidence.counts)["matched_tables"], 2)
        rom, family, matches, tables = font_image()
        rom.data[tables[0]["widths"] + 0xBB] = 255
        rows, _, issues = foundations.fonts(rom, family, matches)
        self.assertEqual(len(rows), 4)
        self.assertEqual([item.id for item in issues], ["font[name=font0]"])

    def inventory(self, rom, text):
        encoded = Charmap().encode(text)
        rom.write(0x900, encoded)
        return {"rom_sha1": rom.sha1(), "entries": [{"id": "s0000900", "offset": 0x900, "length": len(encoded),
                  "text": text, "kind": "pointer", "mode": "field", "flags": ["unverified"]}]}

    def test_slot_usage_skips_control_arguments_but_counts_unverified_glyphs(self):
        rom, _, _, tables = font_image()
        inventory = self.inventory(rom, "A{COLOR 18}B")
        slots = foundations.free_slots(rom, tables, inventory, 5)
        self.assertEqual(slots["status"], "candidate")
        self.assertIn(0x18, slots["free"])
        self.assertNotIn(0x18, slots["used"])
        inventory = self.inventory(rom, "A{18}B")
        slots = foundations.free_slots(rom, tables, inventory, 5)
        self.assertIn(0x18, slots["used"])
        self.assertNotIn(0x18, slots["free"])
        self.assertIn("not whole-ROM", slots["scope"])

    def test_slot_proof_requires_blank_glyphs_matching_sources_and_every_font(self):
        rom, _, _, tables = font_image()
        rom.data[tables[0]["glyphs"] + 0x18 * 64] = 0x55
        inventory = self.inventory(rom, "A{COLOR 18}B")
        slots = foundations.free_slots(rom, tables, inventory, 5)
        self.assertNotIn(0x18, slots["blank"])
        self.assertNotIn(0x18, slots["free"])
        self.assertEqual(foundations.free_slots(rom, tables, None)["status"], "unresolved")
        self.assertEqual(foundations.free_slots(rom, tables[:4], inventory, 5)["status"], "unresolved")
        inventory["entries"][0]["text"] = "Other"
        with self.assertRaisesRegex(ValueError, "source differs"):
            foundations.free_slots(rom, tables, inventory, 5)

class EmissionTest(unittest.TestCase):
    def result(self):
        rom = Rom(bytearray(0x800))
        return foundations.run(rom, ())

    def test_output_is_reproducible_guarded_and_contains_only_verified_facts(self):
        result = self.result()
        with tempfile.TemporaryDirectory() as temporary:
            first, second = Path(temporary) / "first", Path(temporary) / "second"
            emission.write(result, first, "fixture")
            emission.write(result, second, "fixture")
            for name in ("profile.toml", "discovery.json", "report.md"):
                self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())
            sources = schema.read(first)
            self.assertEqual(json.dumps(sources.header), json.dumps(result.metadata | {"comparison": {"available": False}}))
            self.assertEqual(tomllib.loads((first / "profile.toml").read_text())["discovery"], {"schema": 1})
            self.assertEqual(len(schema.generated_facts(sources)), 3)
            self.assertNotIn("font_slots", Profile.load(first).data)
            self.assertEqual(Profile.load(first)["support"]["stage"], "discovery")
            self.assertIn("unresolved", (first / "report.md").read_text())

    def test_a_sentence_of_several_facts_stands_once_and_loads_in_place(self):
        shared, single = "Header bytes are read directly.", "The input length is measured\ndirectly."
        data = {"rom": {"sha1": "00" * 20, "size": 0x800, "title": "TEST"}}
        facts = [foundations.fact(f"rom.{key}", "identity", value, foundations.evidence(sentence, "header"), "shape")
                 for key, value, sentence in (("sha1", "00" * 20, shared), ("size", 0x800, single),
                                              ("title", "TEST", shared))]
        text = emission.document(data, facts)
        self.assertEqual(text.count(shared), 1)
        self.assertIn('\ntexts = [\n "Header bytes are read directly.",\n]\n\n[discovery]\nschema = 1\n', text)
        self.assertEqual(text.count("sentence = 0 }"), 2)
        self.assertIn('sentence = "The input length is measured\\ndirectly." }', text)
        with tempfile.TemporaryDirectory() as temporary:
            (Path(temporary) / "profile.toml").write_text(text, encoding="utf-8")
            sources = schema.read(temporary)
            self.assertEqual(sources.generated_records, [emission.record(proof) for proof in facts])
            self.assertNotIn("texts", sources.generated)
            loaded = {proof.id: proof.evidence.sentence for proof in schema.generated_facts(sources)}
            self.assertEqual(loaded, {"rom.sha1": shared, "rom.size": single, "rom.title": shared})
        self.assertNotIn("texts", emission.document(data, facts[:2]))

    @unittest.skipUnless(os.name == "posix", "POSIX file modes required")
    def test_replacements_preserve_each_file_mode_despite_umask(self):
        result = self.result()
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            expected = {"profile.toml": 0o644, "discovery.json": 0o640, "report.md": 0o664}
            for name, mode in expected.items():
                path = target / name
                path.write_text("")
                path.chmod(mode)
            previous = os.umask(0o077)
            try:
                emission.write(result, target, "fixture")
            finally:
                os.umask(previous)
            for name, mode in expected.items():
                self.assertEqual(stat.S_IMODE((target / name).stat().st_mode), mode)
                self.assertTrue((target / name).read_text())
            self.assertEqual({path.name for path in target.iterdir()}, set(expected))

    @unittest.skipUnless(os.name == "posix", "POSIX file modes required")
    def test_new_files_use_default_permissions_under_umask(self):
        result = self.result()
        for mask in (0o022, 0o027, 0o077):
            with self.subTest(mask=mask), tempfile.TemporaryDirectory() as temporary:
                target = Path(temporary)
                previous = os.umask(mask)
                try:
                    emission.write(result, target, "fixture")
                finally:
                    os.umask(previous)
                for name in ("profile.toml", "discovery.json", "report.md"):
                    self.assertEqual(stat.S_IMODE((target / name).stat().st_mode), 0o666 & ~mask)

    def test_failed_replacement_keeps_existing_output_and_cleans_staging(self):
        result = self.result()
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            generated = target / "profile.toml"
            generated.write_text("schema = 1\n")
            with patch.object(emission.os, "replace", side_effect=OSError("replacement failed")):
                with self.assertRaisesRegex(OSError, "replacement failed"):
                    emission.write(result, target, "fixture")
            self.assertEqual(generated.read_text(), "schema = 1\n")
            self.assertEqual(list(target.iterdir()), [generated])

    def test_cli_accepts_declared_candidates_and_reports_their_reason(self):
        result = self.result()
        reason = "The anchored copy has no confirmed indexed reader."
        result.issues = [schema.Fact("table[name=demo]", "table", "table", None,
            status="candidate", source="tables/1.0.0", evidence=foundations.evidence(reason, "accessor"))]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "fixture.toml"
            reference.write_text('[rom]\nsha1=' + json.dumps(result.data["rom"]["sha1"])
                                 + '\n[support]\nstage="discovery"\n')
            target = root / "output"
            with patch.object(Profile, "load_rom", return_value=Rom(bytearray(0x800))), \
                    patch.object(foundations, "run", return_value=result), redirect_stdout(io.StringIO()) as output:
                status = main(["--project-root", str(CATALOG_STAGE), "--profile", str(reference),
                               "discover", "run", "--out", str(target)])
            self.assertEqual(status, 0, output.getvalue())
            self.assertIn("candidate    1 facts", output.getvalue())
            self.assertIn("## Candidates", (target / "report.md").read_text())
            self.assertIn("| `table[name=demo]` | table | " + reason + " |", (target / "report.md").read_text())
            self.assertIn(reason, (target / "report.md").read_text())
            self.assertNotIn("table", Profile.load(target).data)

    def test_cli_reports_omissions_and_still_fails_unresolved_tables(self):
        reason = "The declared reader bypasses the retained table loads."
        for unresolved in (False, True):
            with self.subTest(unresolved=unresolved), tempfile.TemporaryDirectory() as temporary:
                result = self.result()
                result.issues = [schema.Fact("table[name=unused]", "table", "table", None,
                    status="omitted", source="tables/1.0.0",
                    evidence=foundations.evidence(reason, "semantic-reader+entry-reachability"))]
                if unresolved:
                    result.issues.append(schema.Fact("table[name=missing]", "table", "table", None,
                        status="unresolved", source="tables/1.0.0",
                        evidence=foundations.evidence("No unique reader found.", "semantic-reader")))
                root = Path(temporary)
                reference = root / "fixture.toml"
                reference.write_text('[rom]\nsha1=' + json.dumps(result.data["rom"]["sha1"])
                                     + '\n[support]\nstage="discovery"\n')
                target = root / "output"
                with patch.object(Profile, "load_rom", return_value=Rom(bytearray(0x800))), \
                        patch.object(foundations, "run", return_value=result), \
                        redirect_stdout(io.StringIO()) as output, patch("sys.stderr", output):
                    status = main(["--project-root", str(CATALOG_STAGE), "--profile", str(reference),
                                   "discover", "run", "--out", str(target)])
                self.assertEqual(status, int(unresolved), output.getvalue())
                self.assertIn("omitted      1 facts", output.getvalue())
                report = (target / "report.md").read_text()
                self.assertIn("## Omitted", report)
                self.assertIn("| `table[name=unused]` | table | " + reason + " |", report)
                self.assertNotIn("table", Profile.load(target).data)
                if unresolved:
                    self.assertEqual(output.getvalue().splitlines()[-1],
                                     "result       failed: unresolved discovery facts: table[name=missing]")

    def test_other_rom_output_and_invalid_evidence_do_not_touch_existing_files(self):
        result = self.result()
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            generated, overlay = target / "profile.toml", target / "overlay.toml"
            generated.write_text('[discovery]\nschema=1\n')
            overlay.write_text('[rom]\nsha1="other"\n')
            with self.assertRaisesRegex(ValueError, "another ROM"):
                emission.write(result, target, "fixture")
            self.assertEqual(generated.read_text(), '[discovery]\nschema=1\n')
            self.assertEqual(overlay.read_text(), '[rom]\nsha1="other"\n')
            overlay.write_text('[rom]\nsha1=' + json.dumps(result.data["rom"]["sha1"]) + '\n')
            invalid = copy.deepcopy(result)
            invalid.facts[0].source = ""
            with self.assertRaises(schema.ProfileError):
                emission.write(invalid, target, "fixture")
            self.assertEqual(generated.read_text(), '[discovery]\nschema=1\n')

    def test_flat_reference_profile_feeds_the_structural_inventory(self):
        result = self.result()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.toml"
            reference.write_text('[rom]\nsha1=' + json.dumps(result.data["rom"]["sha1"]) + '\n'
                                 '[[table]]\nname = "types"\nbase = 4096\nstride = 7\ncount = 18\n')
            output = emission.write(result, root / "output", "fixture", Profile.load(reference))
            structural = output["structural"]
            self.assertEqual((structural["structural_facts"], structural["reproduced"]), (1, 0))
            self.assertEqual(structural["unresolved"][0]["id"], "table[name=types]")
            self.assertEqual(structural["unresolved"][0]["fields"], ["base", "stride", "count"])
            report = (root / "output" / "report.md").read_bytes()
            self.assertNotIn(b"table[name=types]", report)
            reference.write_text(reference.read_text().replace("4096", "8192"))
            (root / "output" / "overlay.toml").write_text(reference.read_text())
            changed = emission.write(result, root / "output", "fixture", Profile.load(reference))
            self.assertEqual(changed["structural"], structural)
            self.assertEqual((root / "output" / "report.md").read_bytes(), report)
            emission.write(result, root / "standalone", "fixture")
            self.assertEqual((root / "standalone" / "report.md").read_bytes(), report)

    def test_ordered_overlay_identity_and_support_are_preserved(self):
        result = self.result()
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            overlay = target / "overlay"
            overlay.mkdir()
            order = overlay / "order.txt"
            order.write_text("identity.toml\nengine.toml\n")
            identity = overlay / "identity.toml"
            identity.write_text('[rom]\nsha1="other"\n')
            engine = overlay / "engine.toml"
            engine.write_text('[support]\nstage="translation"\n')
            with self.assertRaisesRegex(ValueError, "another ROM"):
                emission.write(result, target, "fixture")
            self.assertFalse((target / schema.GENERATED).exists())
            identity.write_text('[rom]\nsha1=' + json.dumps(result.data["rom"]["sha1"]) + '\n')
            before = {path: path.read_bytes() for path in (order, identity, engine)}
            emission.write(result, target, "fixture")
            self.assertEqual({path: path.read_bytes() for path in before}, before)
            self.assertNotIn("support", schema.read(target).generated)
            self.assertEqual(Profile.load(target)["support"], {"stage": "translation"})

    def test_cli_reports_unknown_family_and_uses_explicit_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / "synthetic.gba"
            raw.write_bytes(bytes(0x800))
            profile = root / "fixture.toml"
            profile.write_text('[rom]\npath=' + json.dumps(str(raw)) + '\nsha1="' + hashlib.sha1(raw.read_bytes()).hexdigest()
                               + '"\n[support]\nstage="discovery"\n')
            target = root / "output"
            with redirect_stdout(io.StringIO()) as output, patch("sys.stderr", output):
                status = main(["--project-root", str(CATALOG_STAGE), "--profile", str(profile),
                               "discover", "run", "--out", str(target)])
            self.assertEqual(status, 1)
            self.assertIn("0 verified bindings", output.getvalue())
            self.assertTrue(output.getvalue().splitlines()[-1].startswith(
                "result       failed: unresolved discovery facts: "))
            self.assertTrue((target / "profile.toml").is_file())
            self.assertIn("No string inventory supplied", (target / "report.md").read_text())
            invalid = root / "inventory.json"
            invalid.write_text('{"rom_sha1":"other","entries":[]}')
            with redirect_stdout(io.StringIO()) as output, patch("sys.stderr", output):
                status = main(["--project-root", str(CATALOG_STAGE), "--profile", str(profile), "discover", "run",
                               "--inventory", str(invalid), "--out", str(root / "refused")])
            self.assertEqual(status, 1)
            self.assertIn("inventory", output.getvalue())
            self.assertFalse((root / "refused").exists())
