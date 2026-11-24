import unittest

from gen3tl.discover.context import context_identities
from gen3tl.discover.maps import identity_context

class IdentityTest(unittest.TestCase):
    def test_sources_remain_independent_and_addressee_is_explicit(self):
        from types import SimpleNamespace
        records = {"line": {"uses": [{"map": "2.3", "script": "0x100", "order": 2,
                                       "sprite": 7, "textcolor": 0, "textcolor_gender": "male",
                                       "trainer": {"id": 9, "class": 5, "gender": "female"}}]}}
        config = {"context": {
            "sprite_genders": {"7": {"gender": "female", "evidence": "object table"}},
            "textcolor_genders": {"1": "female"},
            "participants": [{"text": "line", "role": "addressee", "sprite": 8,
                              "textcolor": 1, "in_source": "not-stated", "evidence": "scene trace"}]}}
        context_identities(records, {"line": {"text": "You had one job."}}, config, SimpleNamespace())
        identity = records["line"]["identity"]
        self.assertEqual(identity["schema"], "2.0.0")
        self.assertEqual(identity["speaker"]["in_source"], "not-stated")
        sources = identity["speaker"]["sources"]
        self.assertEqual([(s["source"], s["gender"], s["value"]) for s in sources],
                         [("sprite", "female", "7"), ("trainer", "female", "9 / 5"),
                          ("text colour", "male", "0")])
        self.assertIn("object table", sources[0]["evidence"])
        self.assertEqual([(s["source"], s["gender"]) for s in identity["addressee"]["sources"]],
                         [("sprite", "unknown"), ("text colour", "female")])

    def test_gendered_text_needs_role_evidence(self):
        from types import SimpleNamespace
        self.assertEqual(identity_context("She told me to wait.")["speaker"]["in_source"], "unknown")
        self.assertEqual(identity_context(None)["speaker"]["in_source"], "unknown")
        self.assertEqual(identity_context("I'm a girl.")["speaker"]["in_source"], "stated")
        self.assertEqual(identity_context("{COLOR 04}I'm a girl.")["speaker"]["in_source"], "stated")
        self.assertEqual(identity_context("You are my brother.")["addressee"]["in_source"], "stated")
        self.assertEqual(identity_context("If you are a boy, wait here.")["addressee"]["in_source"], "unknown")
        self.assertEqual(identity_context('She said "I am a girl."')["speaker"]["in_source"], "unknown")
        self.assertEqual(identity_context("I don't think I am a man.")["speaker"]["in_source"], "unknown")
        records = {"line": {"uses": []}}
        config = {"context": {"participants": [{"text": "line", "role": "addressee",
                    "textcolor": 0, "in_source": "stated", "basis": "gendered word", "evidence": "direct address"}]}}
        context_identities(records, {"line": {"text": "Yes, sir!"}}, config, SimpleNamespace())
        self.assertEqual(records["line"]["identity"]["speaker"]["in_source"], "unknown")
        self.assertEqual(records["line"]["identity"]["addressee"]["in_source"], "stated")

    def test_invalid_evidence_is_rejected(self):
        from gen3tl.discover.context import participant_rules
        for rule in ({"text": "missing", "role": "speaker", "evidence": "trace"},
                     {"text": "line", "role": "speaker", "in_source": "stated", "evidence": "trace"},
                     {"text": "line", "role": "speaker", "sprite": -1, "evidence": "trace"}):
            with self.subTest(rule=rule), self.assertRaises(ValueError):
                participant_rules({"context": {"participants": [rule]}}, {"line"})


class WalkTest(unittest.TestCase):
    def test_each_walk_returns_the_commands_it_decodes(self):
        from gen3tl.core.rom import Rom, ptr_bytes
        from gen3tl.discover.maps import Walker
        rom = Rom(bytes([255]) * 0x1000)
        rom.write(0x100, b"\x25\xEA\x00" + b"\x67" + ptr_bytes(0xD00) + b"\x02")   # special, message, end
        rom.write(0x200, b"\x02")
        walker = Walker(rom, {"maps": {}})
        self.assertEqual(walker.walk_script(0x100, "", "", "npc", None, None), {0x100, 0x103, 0x108})
        self.assertEqual(walker.walk_script(0x200, "", "", "npc", None, None), {0x200})
        self.assertEqual((walker.decoded, walker.ctx.scripts), ({0x100, 0x103, 0x108, 0x200}, 2))
