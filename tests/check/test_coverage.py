"""Translation coverage of the source, the catalog and a built ROM."""

import unittest
import io
import json
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gen3tl.check import coverage as cov
from gen3tl.core.charmap import Charmap
from gen3tl.catalog.po import PoEntry
from gen3tl.core.rom import Rom, ptr_bytes
from gen3tl.core.terminal import run_status
from gen3tl.cli.check import coverage
from gen3tl.profile.profile import Profile


class CauseTest(unittest.TestCase):
    def test_cli_coverage_names_open_entries_in_the_final_line(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inventory, context, built = root / "inventory.json", root / "context.json", root / "built.gba"
            inventory.write_text(json.dumps({"entries": []}))
            context.write_text("{}")
            built.write_bytes(bytes(0x800))
            args = SimpleNamespace(profile="fixture", rom=str(built), built=str(built),
                                   inventory=str(inventory), context=str(context), po_root=root / "po",
                                   lang="fixture", out=root / "report.md")
            profile = Profile({"maps": {"groups": 0}}, root / "profile.toml")
            entry = PoEntry("entry", "Source text")
            for approved in (False, True):
                entry.msgstr = "Translated text" if approved else ""
                output = io.StringIO()
                with self.subTest(approved=approved), redirect_stdout(output), redirect_stderr(output), \
                        patch.object(Profile, "by_name", return_value=profile), \
                        patch.object(Profile, "load_rom", return_value=Rom.load(built)), \
                        patch("gen3tl.build.release.load", return_value="1.0.0"), \
                        patch("gen3tl.lang.langpack.LangPack.load", return_value=SimpleNamespace(charmap=Charmap)), \
                        patch("gen3tl.catalog.catalog.load_catalog", return_value={"entry": (root / "po", entry)}):
                    status = run_status(lambda report: coverage(args))
                self.assertEqual(status, 0 if approved else 1)
                self.assertTrue(args.out.is_file(), output.getvalue())
                if approved:
                    self.assertNotIn("result       failed:", output.getvalue())
                else:
                    self.assertEqual(output.getvalue().splitlines()[-1],
                                     "result       failed: unresolved coverage: 1 entries")

    def test_untranslated_causes(self):
        never = PoEntry("s1", "Hello there.", extracted=["reach: never shown (reach study)"])
        self.assertEqual(cov.untranslated_cause(never), ("never shown", "never shown (reach study)"))
        self.assertEqual(cov.untranslated_cause(PoEntry("s2", "s", flags=["empty"]))[0], "empty on purpose")
        self.assertEqual(cov.untranslated_cause(PoEntry("s3", ""))[0], "empty source")
        self.assertEqual(cov.untranslated_cause(PoEntry("s4", "{CLEAR 0B}G{CLEAR 06}H"))[0], "no words")
        self.assertEqual(cov.untranslated_cause(PoEntry("s5", "Hello there."))[0], "open: no cause")

    def test_layout_breaks_do_not_count(self):
        self.assertTrue(cov.same_text("Один два\\nтри\\lчетыре", "Один два три четыре"))
        self.assertFalse(cov.same_text("Один\\pдва", "Один два"))


class BuiltTest(unittest.TestCase):
    """Built output through rewritten, kept and long-name references."""

    def setUp(self):
        cm = Charmap()
        self.cm = cm
        rom = bytearray(b"\x00" * 0x1000)
        def put(at, text):
            blob = cm.encode(text)
            rom[at:at + len(blob)] = blob
        put(0x100, "Hello there.")       # the English, left behind
        put(0x200, "Bonjour.")           # the translation, relocated
        rom[0x300:0x304] = ptr_bytes(0x200)   # verified reference, rewritten
        rom[0x310:0x314] = ptr_bytes(0x100)   # unverified reference, kept
        put(0x400, "Potion")             # inline field
        put(0x500, "Super Potion")       # a long name
        rom[0x410:0x414] = ptr_bytes(0x500)   # field turned into a pointer
        rom[0x420] = cm.encode("s")[0]   # unterminated one-byte field
        rom[0x421:0x424] = b"\x21\x22\x23"
        self.rom = Rom(bytes(rom))
        self.prof = {"table": [
            {"name": "items", "base": 0x400, "stride": 16, "count": 2, "field_offset": 0, "field_length": 14,
             "long_name_pointers": True, "long_name_fields": [{"index": 1, "length": 16, "evidence": "probe"}]},
            {"name": "plural", "base": 0x420, "stride": 4, "count": 1, "field_offset": 0, "field_length": 1,
             "terminated": False, "pad": 0xFF}]}
        self.inv = {"entries": [
            {"id": "s0000100", "offset": 0x100, "length": 13, "kind": "pointer", "text": "Hello there.",
             "refs": [{"at": 0x300, "kind": "script:loadpointer"}, {"at": 0x310, "kind": "unverified"}]},
            {"id": "items:0", "offset": 0x400, "length": 14, "kind": "fixed", "text": "Potion", "refs": [],
             "table": "items", "index": 0},
            {"id": "items:1", "offset": 0x410, "length": 14, "kind": "fixed", "text": "Potion+", "refs": [],
             "table": "items", "index": 1},
            {"id": "plural:0", "offset": 0x420, "length": 1, "kind": "fixed", "text": "s", "refs": [],
             "table": "plural", "index": 0},
            {"id": "s0000800", "offset": 0x800, "length": 20, "kind": "pointer", "text": "Then, you can go.",
             "refs": [{"at": 0x900, "kind": "aligned"}]}]}
        f = Path("fixture.po")
        self.cat = {"s0000100": (f, PoEntry("s0000100", "Hello there.", "Bonjour.")),
                    "items:0": (f, PoEntry("items:0", "Potion", "Potion")),
                    "items:1": (f, PoEntry("items:1", "Potion+", "Super Potion")),
                    "plural:0": (f, PoEntry("plural:0", "s"))}

    def test_each_reference_is_classified(self):
        rep = cov.build(self.inv, self.cat, self.rom, self.cm, self.prof, {"strings": {}})
        self.assertEqual(rep.built, {"translation": 3, "English, as the catalog says": 1,
                                     "open: kept reference to the English": 1})
        self.assertEqual([(r[1], r[3]) for r in rep.built_rows], [("s0000100", 0x310)])
        self.assertEqual(rep.untranslated, {"no words": 1})   # the bare suffix "s"
        self.assertEqual([(r[0], r[4]) for r in rep.unexported], [("s0000800", True)])
        self.assertEqual(rep.open, 2)
        text = cov.markdown(rep, "built.gba")
        self.assertIn("Open items: 2.", text)
        self.assertIn("| s0000100 | unverified | 0x310 |", text)

    def test_a_translated_entry_is_expected_in_the_built_output(self):
        approved = cov.build(self.inv, self.cat, self.rom, self.cm, self.prof, {"strings": {}})
        self.cat["s0000100"][1].flags = ["fuzzy"]
        rep = cov.build(self.inv, self.cat, self.rom, self.cm, self.prof, {"strings": {}})
        self.assertEqual(rep.built, approved.built)
        self.assertEqual(rep.built["translation"], 3)
        self.assertEqual(rep.untranslated, approved.untranslated)
        self.assertEqual(rep.open, approved.open)
        self.assertNotIn("| s0000100 | fixture.po |", cov.markdown(rep, "built.gba"))

    def test_a_translated_sign_counts_as_translated(self):
        self.cat["s0000100"][1].flags = ["fuzzy"]
        ctx = {"strings": {"s0000100": {"uses": [{"event": "sign"}]}}}
        rep = cov.build(self.inv, self.cat, self.rom, self.cm, self.prof, ctx)
        self.assertEqual(rep.families["signs"]["translated"], 1)

    def test_never_shown_entries_explain_a_kept_reference(self):
        self.cat["s0000100"][1].msgstr = ""
        self.cat["s0000100"][1].extracted = ["reach: never shown (probe)"]
        rep = cov.build(self.inv, self.cat, self.rom, self.cm, self.prof, {"strings": {}})
        self.assertEqual(rep.untranslated["never shown"], 1)
        self.assertNotIn("open: kept reference to the English", rep.built)


class NotReadTest(unittest.TestCase):
    """Evidence that a kept reference is no pointer."""

    def test_rules(self):
        data = bytearray(b"\x00" * 0x2000)
        data[0x400:0x404] = ptr_bytes(0x100)          # aligned, alone
        data[0x501:0x505] = ptr_bytes(0x100)          # unaligned
        data[0x600:0x604] = ptr_bytes(0x100)          # aligned, a Thumb ldr loads it
        data[0x5F0:0x5F2] = bytes([0x03, 0x48])       # ldr r0, [pc, #12] -> 0x600
        data[0x700:0x704] = ptr_bytes(0x100)          # aligned, a pointer beside it
        data[0x708:0x70C] = ptr_bytes(0x200)
        data[0x801:0x805] = ptr_bytes(0x100)          # unaligned, inside a script command
        rom = Rom(bytes(data))
        commands = [(0x7FF, 0x805)]
        self.assertIn("aligned", cov.not_read(rom, 0x400, commands, {}))
        self.assertIn("unaligned", cov.not_read(rom, 0x501, commands, {}))
        self.assertIsNone(cov.not_read(rom, 0x600, commands, {}))
        self.assertIsNone(cov.not_read(rom, 0x700, commands, {}))
        self.assertIsNone(cov.not_read(rom, 0x801, commands, {}))
        self.assertEqual(cov.not_read(rom, 0x801, commands, {0x801: "declared"}), "declared")
        self.assertIsNone(cov.not_read(rom, 0x501, None, {}))     # no walk, no evidence

    def test_declarations_are_validated(self):
        good = {"coverage": {"unread_reference": [{"at": 0x10, "evidence": "probe"}]}}
        self.assertEqual(cov.unread_rules(good), {0x10: "probe"})
        for bad in ({"at": "0x10", "evidence": "probe"}, {"at": 0x10, "evidence": " "}, {"at": 0x10}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                cov.unread_rules({"coverage": {"unread_reference": [bad]}})

    def test_walked_commands_cover_a_buffer_pointer(self):
        from gen3tl.discover import maps
        cm = Charmap()
        data = bytearray(b"\xff" * 0x1000)
        text = cm.encode("Hi!")
        data[0x900:0x900 + len(text)] = text
        data[0x100:0x107] = b"\x85\x0a" + ptr_bytes(0x900) + b"\x02"   # bufferstring 0x0A; end
        walker = maps.Walker(Rom(bytes(data)), {"maps": {}})
        walker.walk_script(0x100, "", "", "npc", None, None)
        self.assertEqual(walker.command_spans(), [(0x100, 0x106), (0x106, 0x107)])
        self.assertIsNone(cov.not_read(Rom(bytes(data)), 0x102, walker.command_spans(), {}))


if __name__ == "__main__":
    unittest.main()
