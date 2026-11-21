import unittest

from gen3tl.check.entrycheck import entry_problems
import editorcheck_support

class EditorCheckTest(editorcheck_support.EditorCheckTest, unittest.TestCase):


    pass

class UnmeasuredLimitTest(unittest.TestCase):
    """Problems against a limit taken from the English carry a mark."""

    @classmethod
    def setUpClass(cls):
        EditorCheckTest.setUpClass.__func__(cls)   # the same document

    def test_mark(self):
        from gen3tl.check.entrycheck import UNMEASURED, UNMEASURED_LINES
        from gen3tl.check.layout import Limit
        from gen3tl.check.qa import limit_source
        en = {"width": limit_source("source", "the English: its widest line"),
              "lines": limit_source("source", "the English: its line count")}
        text = "Очень длинная строка текста"
        for sources, marked in ((en, True), ({"width": limit_source("measured", "[layout.window_width] s1")}, False),
                                (None, False)):
            lim = Limit("multiline", 60, 1, sources=sources)
            with self.subTest(sources=sources):
                _, probs = entry_problems("Long", "Ааааааааааааа\\nб", lim, self.measure, self.cm)
                self.assertEqual(probs[:2], ["line too wide: 78 > 60 px: 'Ааааааааааааа'" + (UNMEASURED if marked else ""),
                                             "too many lines: 2 > 1" + (UNMEASURED_LINES if marked else "")])
                # warns on width too; the English count is no limit
                self.assertEqual(probs[2].endswith("(warning)" + (UNMEASURED if marked else "")), True)
                self.assertEqual(probs[3], "box 1 wraps to 2 lines, the English box has 1 (warning)")
        # browser check: window sources as the data carries them
        window = {"kind": "single", "width": 60, "lines": 1, "sources": {"width": en["width"]}}
        msgs = [p["message"] for p in self.checker.check({"id": "s0000900", "source_text": "Long", "window": window},
                                                          text)["problems"]]
        self.assertTrue(msgs and all(m.endswith(UNMEASURED) for m in msgs if " > 60 px: " in m), msgs)

    def test_unverified_mark(self):
        from gen3tl.check.entrycheck import UNVERIFIED
        from gen3tl.check.layout import Limit
        from gen3tl.check.qa import limit_source
        guess = {"width": limit_source("profile", "[language policy] window_width.s1: text budget", unverified=True)}
        self.assertEqual(guess["width"]["unverified"], True)
        self.assertNotIn("unverified", limit_source("profile", "[language policy] window_width.s1: text budget"))
        lim = Limit("multiline", 60, 1, sources=guess)
        _, probs = entry_problems("Long", "Ааааааааааааа\\nб", lim, self.measure, self.cm)
        self.assertEqual(probs[:2], ["line too wide: 78 > 60 px: 'Ааааааааааааа'" + UNVERIFIED, "too many lines: 2 > 1"])
        window = {"kind": "single", "width": 60, "lines": 1, "sources": guess}
        msgs = [p["message"] for p in self.checker.check({"id": "s0000900", "source_text": "Long", "window": window},
                                                          "Очень длинная строка текста")["problems"]]
        self.assertTrue(msgs and all(m.endswith(UNVERIFIED) for m in msgs if " > 60 px: " in m), msgs)
