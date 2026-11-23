import unittest
from unittest.mock import patch

from gen3tl.core.charmap import Charmap
from gen3tl.build.font import INK, SHADOW, shadow_end
from gen3tl.lang.langpack import Glyph, LangPack
from gen3tl.check.layout import Limit, Measure, arrow_overflows, check, dropped_breaks, entry_measure, extra_lines, fits, layout, lines_of, paragraphs, shadow_overflow, short_line_ends, split_words, waits, wrap_words, wrapped_dash_lines
from gen3tl.check.render import PALETTE, Canvas, draw_text, line_width, placeholder_px
from gen3tl.lang.language import load as language_pack
from project_fixture import select_language

def setUpModule():
    select_language("ru")

KEEP_WITH_NEXT = frozenset(language_pack("ru").data["wrapping"]["keep_with_next"])

PACK = LangPack.load("ru", "unbound")

CM = PACK.charmap()

M = Measure(lambda b: 6, {"PLAYER": 42})  # 6 px per character for predictable tests

class LayoutTest(unittest.TestCase):
    def test_extended_glyph_uses_font_width_and_renders(self):
        from gen3tl.catalog.catalog import line_widths
        from gen3tl.check.render import GlyphSource, iter_text_bytes
        from gen3tl.build.font import FontTable
        from gen3tl.core.rom import Rom
        m = Measure(lambda b: 10 if b == 0x105 else 6, {"F9 05": 99},
                    styles={"small": lambda b: 8 if b == 0x105 else 5}, fonts={"00": "small"},
                    shadow_end=lambda b: b == 0x105)
        self.assertEqual(m.token_width("{F9 05}", CM), 10)
        self.assertEqual(m.width("{FONT 00}{F9 05}A", CM), 13)
        self.assertEqual(m.end_glyph("{F9 05}", CM), 0x105)
        self.assertEqual(line_widths("{F9 05} ", CM, m.glyph_width), [10])
        self.assertTrue(shadow_overflow("{F9 05}", 9, m, CM))
        raw = CM.encode("{F9 05}{F8 00}")
        self.assertEqual(list(iter_text_bytes(raw)), [("char", 0x105), ("placeholder", b"\xF8\x00")])
        px = [[0] * 16 for _ in range(16)]
        px[4][0] = INK
        glyph = Glyph(0x105, 10, px)
        source = GlyphSource(Rom(bytes(0x8200)), FontTable("f", 0, 0x8000, 512), {0x105: glyph})
        self.assertEqual(line_width(source, CM.encode("{F9 05}")), 10)
        canvas = Canvas(12, 16)
        draw_text(canvas, source, 0, 0, CM.encode("{F9 05}"))
        self.assertEqual(canvas.px[4][0], PALETTE[INK])

    def test_breaks_follow_gen3_convention(self):
        text = " ".join(["слово"] * 30)  # 30 words of 5 letters
        out = layout(text, Limit("dialogue", 120, 2), M, CM)
        self.assertEqual(out.count("\\n"), 1)
        self.assertGreater(out.count("\\l"), 0)
        self.assertEqual(check(out, Limit("dialogue", 120, 2), M, CM), [])

    def test_extra_lines_per_box(self):
        # the second box wraps to 3 lines, English has 2
        english = "Hi!\\pOne line\\nand another."
        ru = layout("Привет!\\p" + " ".join(["слово"] * 9), Limit("dialogue", 120, 2), M, CM)
        self.assertEqual(extra_lines(english, ru), [(2, 3, 2)])
        self.assertEqual(extra_lines(english, "Привет!\\pКоротко."), [])

    def test_fitting_translator_breaks_are_kept(self):
        text = "Привет!\\nКак дела?"
        self.assertEqual(layout(text, Limit("dialogue", 208, 2), M, CM), text)

    def test_overflowing_paragraph_is_rewrapped_per_paragraph(self):
        text = "Короткий абзац.\\p" + " ".join(["длинное"] * 12)
        out = layout(text, Limit("dialogue", 120, 2), M, CM)
        first, second = out.split("\\p")
        self.assertEqual(first, "Короткий абзац.")
        self.assertIn("\\n", second)

    def test_braces_are_never_split(self):
        self.assertEqual(split_words("Цвет {COLOR 06}зелёный{COLOR 08} тут"),
                         ["Цвет", "{COLOR 06}зелёный{COLOR 08}", "тут"])

    def test_placeholder_width_and_colour_codes(self):
        self.assertEqual(M.width("{PLAYER}", CM), 42)
        self.assertEqual(M.width("{COLOR 06}аб{COLOR 08}", CM), 12)

    def test_token_width_is_encoded_once_per_measure(self):
        m = Measure(lambda b: 6, {})
        with patch.object(CM, "encode", wraps=CM.encode) as encode:
            self.assertEqual(m.width("A", CM), 6)
            self.assertEqual(m.width("A", CM), 6)
        self.assertEqual(encode.call_count, 1)

    def test_token_width_cache_keys_its_inputs(self):
        import copy
        narrow = Measure(lambda b: 6, {"PLAYER": 10})
        wide = Measure(lambda b: 6, {"PLAYER": 20})
        self.assertEqual(narrow.width("{PLAYER}", CM), 10)
        self.assertEqual(wide.width("{PLAYER}", CM), 20)
        self.assertEqual(narrow.width("{PLAYER}", CM), 10)
        copied = copy.copy(narrow)
        copied.glyph_width = lambda b: 7
        self.assertEqual(copied.width("A", CM), 7)
        self.assertEqual(narrow.width("A", CM), 6)
        measure = Measure(lambda b: b, {})
        first = Charmap({"Ж": 0x80})
        second = Charmap({"Ж": 0x81})
        self.assertEqual(measure.width("Ж", first), 0x80)
        self.assertEqual(measure.width("Ж", second), 0x81)
        self.assertEqual(measure.width("Ж", first), 0x80)

    def test_sentence_break(self):
        lim = Limit("dialogue", 208, 2, target=196, sentence_break=0.5)
        text = "Это первое предложение. Второе."
        out = layout(text + " " + " ".join(["ещё"] * 20), lim, M, CM)
        self.assertTrue(out.startswith("Это первое предложение.\\n"), out)

    def test_field_and_inplace_limits(self):
        self.assertTrue(any("field overflow" in p for p in check("Очень длинное имя", Limit("field", 300, 1, chars=12), M, CM)))
        self.assertTrue(any("in-place overflow" in p for p in check("Длинно", Limit("single", 300, 1, bytes=3), M, CM)))
        self.assertTrue(any("not encodable" in p for p in check("«ёлочки»", Limit("single", 300, 1), M, CM)))

    def test_spaced_hyphen_never_opens_a_wrapped_line(self):
        # the hyphen stays on its word's line.
        # the word moves down with it on overflow.
        lim = Limit("dialogue", 60, 2)   # 10 characters per line
        text = "аааа бббб - вв"
        out = layout(text, lim, M, CM)
        self.assertEqual(out, "аааа\\nбббб - вв")
        self.assertEqual(wrapped_dash_lines(text, out), [])
        self.assertEqual(check(out, lim, M, CM), [])
        # a hyphen that fits stays at line end
        self.assertEqual(layout("аааа ббб - вв гг", lim, M, CM), "аааа ббб -\\nвв гг")

    def test_spaced_hyphen_may_run_past_the_target_width(self):
        # a fitting word lets its hyphen reach the hard width.
        # past it both move down.
        text = "аааа бббб - вв"   # "аааа бббб" 54 px, with the hyphen 66 px
        self.assertEqual(layout(text, Limit("dialogue", 72, 2, target=60), M, CM), "аааа бббб -\\nвв")
        self.assertEqual(layout(text, Limit("dialogue", 64, 2, target=60), M, CM), "аааа\\nбббб - вв")
        # a word over the target width gains no room
        self.assertEqual(layout("аааа бббббб - в", Limit("dialogue", 80, 2, target=60), M, CM),
                         "аааа\\nбббббб - в")

    def test_no_sentence_break_before_a_spaced_hyphen(self):
        lim = Limit("dialogue", 120, 2, target=120, sentence_break=0.5)
        out = layout("Это было очень. - сказал он ему тогда", lim, M, CM)
        self.assertEqual(out, "Это было очень. -\\nсказал он ему тогда")

    def test_translator_dash_line_is_kept(self):
        # a translator's line opened by the hyphen is intentional
        fits = "Дружба.\\n- Дневной уход"
        self.assertEqual(layout(fits, Limit("dialogue", 208, 2), M, CM), fits)
        self.assertEqual(wrapped_dash_lines(fits, fits), [])
        boxed = "Привет.\\p- Подпись"
        self.assertEqual(layout(boxed, Limit("dialogue", 208, 2), M, CM), boxed)
        self.assertEqual(wrapped_dash_lines(boxed, boxed), [])
        # a re-wrapped paragraph keeps the dash-line break
        text = "аааа бббб вввв\\n- гг"
        out = layout(text, Limit("dialogue", 60, 2), M, CM)
        self.assertEqual(out, "аааа бббб\\nвввв\\l- гг")
        self.assertEqual(wrapped_dash_lines(text, out), [])
        # in a fixed window too
        self.assertEqual(layout(text, Limit("multiline", 60, 4), M, CM), "аааа бббб\\nвввв\\n- гг")

    def test_full_token_name_takes_its_own_width(self):
        # {FD 3D} is wider than the other {FD xx} tokens
        m = Measure(lambda b: 6, {"FD": 60, "FD 3D": 83})
        self.assertEqual(m.width("{FD 3D}", CM), 83)
        self.assertEqual(m.width("{FD 3B}", CM), 60)
        self.assertEqual(m.width("{FD 3D}{FD 3B}", CM), 143)

    def test_dropped_written_breaks_are_reported(self):
        # a line wider than the window re-wraps the box.
        # the re-wrapped text fits, so check() is silent.
        lim = Limit("multiline", 60, 3, source_lines=[3])
        text = "аааа\\nббббб вввв\\nгг"   # line 2: 10 characters, 60 px; then 11 characters
        self.assertEqual(dropped_breaks(text, lim, M, CM), [])
        wide = "аааа\\nббббб ввввв\\nгг"
        probs = dropped_breaks(wide, lim, M, CM)
        self.assertEqual(len(probs), 1)
        self.assertTrue(probs[0].startswith("written line wider than the window: box 1: 66 > 60 px"), probs[0])
        self.assertNotEqual(layout(wide, lim, M, CM), wide)
        self.assertEqual(check(layout(wide, lim, M, CM), lim, M, CM), [])
        # a paragraph without written breaks is wrapped as intended
        self.assertEqual(dropped_breaks("аааа ббббб ввввв гг", lim, M, CM), [])
        self.assertEqual(dropped_breaks(wide, Limit("single", 60, 1), M, CM), [])

    def test_dropped_breaks_show_the_box_the_game_shows(self):
        # reviews judge the re-wrapped breaks
        from gen3tl.check.layout import paragraphs
        lim = Limit("dialogue", 60, 2)
        text = "аа\\nбб\\pаааа ббббб\\nввввв гг ддд"   # box 2, line 2: 12 characters
        shown = paragraphs(layout(text, lim, M, CM))
        self.assertNotEqual(shown[1], paragraphs(text)[1])
        probs = dropped_breaks(text, lim, M, CM)
        self.assertEqual(len(probs), 1)
        self.assertTrue(probs[0].startswith("written line wider than the window: box 2: 72 > 60 px"), probs[0])
        self.assertTrue(probs[0].endswith(f"; the build drops the written breaks of the box and re-wraps it "
                                          f"as {shown[1]!r}"), probs[0])

    def test_written_line_ending_with_a_short_word_warns(self):
        # the break stays; the warning names the word
        self.assertEqual(short_line_ends("Цель клюют и\\nвыхватывают её\\nягоду, если\\nона есть."),
                         ["written line ends with «и»: box 1, line 1; the break stays as written",
                          "written line ends with «если»: box 1, line 3; the break stays as written"])
        self.assertEqual(short_line_ends("Войти в\\nЗал\\pВ одной\\lстроке не"),
                         ["written line ends with «в»: box 1, line 1; the break stays as written"])
        self.assertEqual(short_line_ends("Цель клюют\\nи выхватывают её"), [])   # «и» starts a line
        self.assertEqual(short_line_ends("{COLOR 06}в\\nдом"), [])   # joined to a token: an ordinary word
        lim = Limit("multiline", 118, 4)
        self.assertEqual(layout("Цель и\\nдом", lim, M, CM), "Цель и\\nдом")   # kept as written

    def test_wrapped_dash_line_is_reported(self):
        # A leading hyphen cannot start the wrap.
        probs = wrapped_dash_lines("Привет.\\pаааа бббб - вв", "Привет.\\pаааа бббб\\n- вв")
        self.assertEqual(len(probs), 1)
        self.assertTrue(probs[0].startswith("line starts with a spaced hyphen after an automatic wrap: box 2"),
                        probs[0])
        self.assertEqual(wrapped_dash_lines("аааа бббб\\n- вв", "аааа бббб\\n- вв"), [])
        # each hyphen matches the same hyphen of the source
        self.assertEqual(len(wrapped_dash_lines("- аа\\nбб - вв", "- аа бб\\n- вв")), 1)
        # a hyphen inside a word is not a spaced hyphen
        self.assertEqual(wrapped_dash_lines("аа -бб", "аа\\n-бб"), [])

class PromptArrowTest(unittest.TestCase):
    """A line that waits keeps room for the prompt arrow."""

    LIM = Limit("dialogue", 60, 2, arrow=12)   # 10 characters per line, 8 beside the arrow

    def test_lines_that_wait(self):
        self.assertEqual(waits("а\\nб\\lв", True), [False, True, False])
        self.assertEqual(waits("а\\nб\\lв", False), [False, True, True])
        self.assertEqual(waits("а", True), [False])

    def test_box_without_written_breaks_is_rewrapped(self):
        text = "ааааа ббб\\pвв"   # box 1: 54 px, 48 px beside the arrow
        self.assertEqual(layout(text, self.LIM, M, CM), "ааааа\\nббб\\pвв")
        self.assertEqual(layout(text, Limit("dialogue", 60, 2), M, CM), text)
        # the last box does not wait
        self.assertEqual(layout("вв\\pааааа ббб", self.LIM, M, CM), "вв\\pааааа ббб")
        self.assertEqual(arrow_overflows(layout(text, self.LIM, M, CM), self.LIM, M, CM), [])

    def test_wrapped_line_before_a_scroll_keeps_the_room(self):
        text = "ааа ббб ввв ггггг дд"
        self.assertEqual(layout(text, Limit("dialogue", 60, 2), M, CM), "ааа ббб\\nввв ггггг\\lдд")
        self.assertEqual(layout(text, self.LIM, M, CM), "ааа ббб\\nввв\\lггггг дд")

    def test_written_breaks_stay_and_warn(self):
        text = "аа\\nааааа ббб\\pвв"
        self.assertEqual(layout(text, self.LIM, M, CM), text)
        self.assertEqual(arrow_overflows(text, self.LIM, M, CM),
                         ["line leaves no room for the prompt arrow: box 1: 54 > 48 px: 'ааааа ббб'"])
        self.assertEqual(check(text, self.LIM, M, CM), [])
        self.assertEqual(dropped_breaks(text, self.LIM, M, CM), [])
        self.assertEqual(arrow_overflows(text, Limit("dialogue", 60, 2), M, CM), [])

    def test_entry_check_rejects_a_crowded_line(self):
        from gen3tl.check.entrycheck import entry_problems
        probs = entry_problems("Hi\\nho\\pyo", "аа\\nааааа ббб\\pвв", self.LIM, M, CM)[1]
        self.assertIn("line leaves no room for the prompt arrow: box 1: 54 > 48 px: 'ааааа ббб'", probs)

class CodedExplanationTest(unittest.TestCase):
    """A coded slot source or window name reaches a check message as its English text."""

    def test_slot_sources_and_window_names_read_as_their_text(self):
        from gen3tl.check.entrycheck import instant_print_problems
        from gen3tl.check.provenance import code
        source = code("slot_reasons", source=code("slot_profile"), reasons=[code("slot_no_fill")])
        menu = code("table_window", name="menu")
        lim = Limit("single", 30, None, placeholders={"PLAYER": 42}, slot_sources={"PLAYER": source},
                    consumers=[{"name": menu, "width": 30, "arrow": 0}, {"name": "wide window", "width": 100, "arrow": 0}])
        self.assertEqual(check("а {PLAYER}", lim, M, CM), ["line too wide: 54 > 30 px: 'а {PLAYER}' with {PLAYER} = 42 px "
                                                         "(profile width (no fill known)); window [[layout.table_window]] menu"])
        waiting = Limit("dialogue", 60, 2, consumers=[{"name": menu, "width": 60, "arrow": 12}])
        self.assertEqual(arrow_overflows("аа\\nааааа ббб\\pвв", waiting, M, CM),
                         ["line leaves no room for the prompt arrow: box 1: 54 > 48 px in [[layout.table_window]] menu: "
                          "'ааааа ббб'"])
        instant = Limit("multiline", 100, 2, consumers=[{"name": menu, "width": 100, "arrow": 0, "instant_print": True}] * 2)
        self.assertEqual(instant_print_problems("а\\pб", "а\\pб", instant),
                         ["scroll and page codes are not supported in [[layout.table_window]] menu: "
                          "this window prints instantly and drops the remaining text"])


class RuntimeScrollTest(unittest.TestCase):
    def test_runtime_waits_keep_written_breaks_and_report_arrow_room(self):
        from gen3tl.check.layout import runtime_text, waiting_windows
        lim = Limit("dialogue", 60, 2, arrow=12, newline_scroll_after=1)
        text = "A" * 9 + "\\n" + "B" * 9 + "\\nC\\n" + "D" * 9
        self.assertEqual(layout(text, lim, M, CM), text)
        self.assertEqual(check(text, lim, M, CM), [])
        self.assertEqual(len(arrow_overflows(text, lim, M, CM)), 1)
        self.assertIn("54 > 48", arrow_overflows(text, lim, M, CM)[0])
        self.assertEqual([[bool(c) for c in row] for row in waiting_windows(text, lim)], [[False, True, True, False]])
        self.assertEqual(runtime_text("A\\nB\\pC\\nD\\nE", 1), "A\\nB\\pC\\lD\\lE")
        self.assertEqual(runtime_text("A\\nB", 0), "A\\lB")
        self.assertEqual(runtime_text(text, None), text)

    def test_wrapping_keeps_runtime_newlines_and_arrow_room(self):
        lim = Limit("dialogue", 60, 2, arrow=12, newline_scroll_after=1)
        text = "aaa bbb ccc ddddd ee"
        laid = layout(text, lim, M, CM)
        self.assertNotIn("\\l", laid)
        self.assertGreater(laid.count("\\n"), 1)
        self.assertEqual(check(laid, lim, M, CM), [])
        self.assertEqual(arrow_overflows(laid, lim, M, CM), [])
        self.assertEqual(split_words(laid), split_words(text))

    def test_only_the_scrolling_consumer_binds_an_implicit_wait(self):
        from gen3tl.check.layout import waiting_windows
        lim = Limit("dialogue", 60, 2, newline_scroll_after=1, consumers=[
            {"name": "narrow", "width": 60, "arrow": 12},
            {"name": "scrolling", "width": 70, "arrow": 12, "newline_scroll_after": 1}])
        text = "A\\n" + "B" * 9 + "\\nC\\pD"
        waiting = waiting_windows(text, lim)
        self.assertEqual(waiting[0][1]["name"], "scrolling")
        self.assertEqual(waiting[0][2]["name"], "narrow")
        self.assertEqual(arrow_overflows(text, lim, M, CM), [])
        lim.consumers[1]["arrow"] = 0
        self.assertIsNone(waiting_windows(text, lim)[0][1])

class KeepWithNextTest(unittest.TestCase):
    """A wrapped line does not end with a short preposition or conjunction."""

    LIM = Limit("dialogue", 60, 2)   # 10 characters per line

    def test_short_word_moves_to_the_next_line(self):
        self.assertEqual(layout("аааа ббб в гггг", self.LIM, M, CM), "аааа ббб\\nв гггг")
        self.assertEqual(wrap_words("аааа ббб в гггг", 60, M, CM, keep=0), ["аааа ббб в", "гггг"])
        # Box 2 of s1F8D540: «…{STR_VAR_2} в\nвосторге от вас.»
        lim = Limit("dialogue", 208, 2)
        self.assertEqual(layout("{PLAYER} передаёт: {PLAYER} в восторге от вас.", lim, M, CM),
                         "{PLAYER} передаёт: {PLAYER}\\nв восторге от вас.")

    def test_every_short_word_in_any_case(self):
        self.assertEqual(KEEP_WITH_NEXT, {"в", "к", "с", "у", "о", "и", "а", "не", "ни", "во", "на", "за", "из", "до",
                                          "от", "по", "об", "со", "ко", "но", "да", "для", "при", "без", "над",
                                          "под", "про", "что", "как", "или", "если"})
        for w in sorted(KEEP_WITH_NEXT) + [x.capitalize() for x in KEEP_WITH_NEXT] + ["НЕ", "НИ", "ЕСЛИ"]:
            lim = Limit("dialogue", 6 * len("аааа ббб " + w), 2)   # the word just fits on line 1
            self.assertEqual(layout(f"аааа ббб {w} гг", lim, M, CM), f"аааа ббб\\n{w} гг", w)

    def test_short_words_in_a_row_move_together(self):
        self.assertEqual(layout("аааа и в гггг", self.LIM, M, CM), "аааа\\nи в гггг")

    def test_word_that_does_not_fit_with_the_next_stays(self):
        # «в» and the next word make 12 characters
        self.assertEqual(layout("аааа ббб в гггггггггг", self.LIM, M, CM), "аааа ббб в\\nгггггггггг")
        # a line is never left empty
        self.assertEqual(wrap_words("в гггггггггг", 60, M, CM), ["в", "гггггггггг"])

    def test_joined_words_are_ordinary_words(self):
        # joined by a hyphen, a brace token or punctuation
        self.assertEqual(layout("ааа бб в-в гггг", self.LIM, M, CM), "ааа бб в-в\\nгггг")
        self.assertEqual(layout("ааа ббб {COLOR 06}в гггг", self.LIM, M, CM), "ааа ббб {COLOR 06}в\\nгггг")
        self.assertEqual(layout("аааа бб и, гггг", self.LIM, M, CM), "аааа бб и,\\nгггг")
        self.assertEqual(layout("ааа бб не… гггг", self.LIM, M, CM), "ааа бб не…\\nгггг")
        # a spaced hyphen: the line ends with the hyphen
        self.assertEqual(layout("ааа бб и - гггг", self.LIM, M, CM), "ааа бб и -\\nгггг")

    def test_translator_breaks_are_kept(self):
        text = "Это я и\\nвы."
        self.assertEqual(layout(text, Limit("dialogue", 208, 2), M, CM), text)
        # the break before a dash line stays
        self.assertEqual(layout("аааа ббб в\\n- гггг ддддд", self.LIM, M, CM), "аааа ббб в\\n- гггг\\lддддд")

    def test_no_line_beyond_the_english_box(self):
        text = "аааа ббб в гггг дддд"   # plain: 2 lines; with the move: 3
        plain, moved = "аааа ббб в\\nгггг дддд", "аааа ббб\\nв гггг\\lдддд"
        self.assertEqual(layout(text, Limit("dialogue", 60, 2), M, CM), plain)   # no English line count
        self.assertEqual(layout(text, Limit("dialogue", 60, 2, source_lines=[2]), M, CM), plain)
        self.assertEqual(layout(text, Limit("dialogue", 60, 3, source_lines=[3]), M, CM), moved)
        self.assertEqual(layout(text, Limit("dialogue", 60, 3, source_lines=[3]), M, CM, keep=False), plain)
        # boxes pair with English boxes by position
        self.assertEqual(layout("Привет!\\p" + text, Limit("dialogue", 60, 3, source_lines=[1, 3]), M, CM),
                         "Привет!\\p" + moved)
        self.assertEqual(layout("Привет!\\p" + text, Limit("dialogue", 60, 3, source_lines=[3, 2]), M, CM),
                         "Привет!\\p" + plain)
        self.assertEqual(layout("Привет!\\p" + text, Limit("dialogue", 60, 3, source_lines=[3]), M, CM),
                         "Привет!\\p" + plain)
        # a box with more lines than English gains none
        self.assertEqual(layout(text, Limit("dialogue", 60, 2, source_lines=[1]), M, CM), plain)

    def test_only_the_last_moves_are_undone(self):
        # both moves give 4 lines; undoing one keeps the other
        text = "аааа и бббб в ввввв ггг"
        self.assertEqual(wrap_words(text, 60, M, CM, keep=0), ["аааа и", "бббб в", "ввввв ггг"])
        self.assertEqual(wrap_words(text, 60, M, CM), ["аааа", "и бббб", "в ввввв", "ггг"])
        self.assertEqual(layout(text, Limit("dialogue", 60, 3, source_lines=[3]), M, CM),
                         "аааа\\nи бббб в\\lввввв ггг")
        self.assertEqual(layout(text, Limit("dialogue", 60, 4, source_lines=[4]), M, CM),
                         "аааа\\nи бббб\\lв ввввв\\lггг")

    def test_fixed_window_keeps_its_line_limit(self):
        text = "аааа ббб в гггг дддд"
        self.assertEqual(layout(text, Limit("multiline", 60, 2, source_lines=[2]), M, CM), "аааа ббб в\\nгггг дддд")
        self.assertEqual(layout(text, Limit("multiline", 60, 3, source_lines=[3]), M, CM), "аааа ббб\\nв гггг\\nдддд")
        # the window's line limit binds below the English line count
        lim = Limit("multiline", 60, 2, source_lines=[3])
        self.assertEqual(layout(text, lim, M, CM), "аааа ббб в\\nгггг дддд")
        self.assertEqual(check(layout(text, lim, M, CM), lim, M, CM), [])
        # the spare line goes to the first needing block
        two = text + "\\n\\n" + text
        self.assertEqual(layout(two, Limit("multiline", 60, 6, source_lines=[6]), M, CM),
                         "аааа ббб\\nв гггг\\nдддд\\n\\nаааа ббб в\\nгггг дддд")

class SplitNameTest(unittest.TestCase):
    """A shown break between a noun and its name, or a count and its unit."""

    def test_reported_splits(self):
        from gen3tl.check.layout import split_names
        lim = Limit("dialogue", 120, 2)
        name = "shown break splits «покемона» from the name after it: box 2, line 1"
        self.assertEqual(split_names("Да.\\pДай покемона\\n{COLOR 06}{STR_VAR_1}{COLOR 08}!", lim, M), [name])
        self.assertEqual(split_names("Тренеру\\n{PLAYER}!", lim, M),
                         ["shown break splits «Тренеру» from the name after it: box 1, line 1"])
        self.assertEqual(split_names("Взять ({STR_VAR_2}\\nшт.)?", lim, M),
                         ["shown break splits a count from «шт.»: box 1, line 1"])

    def test_kept_pairs_and_other_tokens(self):
        from gen3tl.check.layout import split_names
        lim = Limit("dialogue", 120, 2)
        for text in ("Дай\\nпокемона {STR_VAR_1}!",     # the pair on one line
                     "Дай покемона,\\n{STR_VAR_1}!",     # punctuation ends the phrase
                     "Сменить покемона\\n{F8 01}",       # a button icon, not a name
                     "Ход покемона\\n{B_BUFF1}!",        # battle buffers keep their formulas
                     "Взять {STR_VAR_2} шт.\\nсейчас?"):
            with self.subTest(text=text):
                self.assertEqual(split_names(text, lim, M), [])
        self.assertEqual(split_names("Дай покемона\\n{STR_VAR_1}!", Limit("battle", 120, 2), M), [])

class AbbreviationTest(unittest.TestCase):
    def test_abbreviations_stay_together_at_every_width(self):
        for phrase in ("т. п.", "т. д.", "т. е.", "Т. Е."):
            for width in range(30, 121, 6):
                for keep in (0, None):
                    with self.subTest(phrase=phrase, width=width, keep=keep):
                        lines = wrap_words("аааа бб " + phrase + " вв", width, M, CM,
                                           sentence_break=0.5, keep=keep)
                        self.assertTrue(any(phrase in line for line in lines), lines)
                        self.assertTrue(all(M.width(line, CM) <= width for line in lines))

    def test_codes_punctuation_and_conjunction_are_preserved(self):
        self.assertEqual(wrap_words("аааа бб {COLOR 06}т. {COLOR 04}п. вв", 60, M, CM),
                         ["аааа бб", "{COLOR 06}т. {COLOR 04}п. вв"])
        self.assertEqual(wrap_words("аааа и т. д. вв", 60, M, CM),
                         ["аааа", "и т. д. вв"])
        self.assertEqual(wrap_words("аааа (т. е.), вв", 66, M, CM),
                         ["аааа", "(т. е.), вв"])
        for opening, closing in (("“", "”"), ("‘", "’")):
            phrase = opening + "т. п." + closing
            self.assertEqual(wrap_words("аааа " + phrase + " вв", 54, M, CM),
                             ["аааа", phrase, "вв"])
        self.assertEqual(wrap_words("аааа т{COLOR 06}. {COLOR 04}п. вв", 54, M, CM),
                         ["аааа", "т{COLOR 06}. {COLOR 04}п. вв"])
        self.assertEqual(wrap_words("аааа т. {COLOR 06} п. вв", 54, M, CM),
                         ["аааа", "т. {COLOR 06} п. вв"])

    def test_other_dotted_words_remain_separate(self):
        self.assertEqual(wrap_words("аааа кот. п. вв", 60, M, CM),
                         ["аааа кот.", "п. вв"])
        self.assertEqual(wrap_words("аааа т. з. вв", 60, M, CM),
                         ["аааа т. з.", "вв"])

    def test_too_narrow_window_reports_intact_abbreviation(self):
        lim = Limit("multiline", 24, 4)
        result = layout("т. п. вв", lim, M, CM)
        self.assertEqual(result, "т. п.\\nвв")
        self.assertTrue(check(result, lim, M, CM))

    def test_written_breaks_stay_unless_rewrap_is_needed(self):
        fitting = "т.\\nп."
        self.assertEqual(layout(fitting, Limit("dialogue", 60, 2), M, CM), fitting)
        self.assertEqual(wrap_words("аааа бб т.\\nп. вв", 60, M, CM),
                         ["аааа бб", "т. п. вв"])

class SolidGlyphs:
    """6 px glyphs inked over their full advance: the drawn extent of a line is its width."""

    def get(self, b):
        return [[INK] * 6 for _ in range(14)], 6

    def width(self, b):
        return 6

class SlotRenderTest(unittest.TestCase):
    """check render draws each placeholder at the width the layout measures it with."""

    PROFILE = Measure(lambda b: 6, {"STR_VAR_1": 60, "STR_VAR_2": 60})
    LIM = Limit("dialogue", 208, 2, placeholders={"STR_VAR_1": 12}, slot_sources={"STR_VAR_1": "number 10"})

    def drawn(self, line: str, slots: dict) -> int:
        """Columns from the line start to the last drawn pixel, plus one."""
        cv = Canvas(400, 16)
        draw_text(cv, SolidGlyphs(), 0, 0, CM.encode(line), slots=slots)
        return max(x for row in cv.px for x, c in enumerate(row) if c != PALETTE["paper"]) + 1

    def test_rendered_line_width_equals_the_layout_width(self):
        # the layout keeps the break within the 12 px slot
        text = "Тренируясь с ним, получишь\\nв {COLOR 06}{STR_VAR_1}{COLOR 08} раз больше значений усилий!"
        laid = layout(text, self.LIM, self.PROFILE, CM)
        self.assertEqual(laid, text)
        me = entry_measure(self.PROFILE, self.LIM)
        slots = placeholder_px(laid, me, CM)
        self.assertEqual(slots, {CM.encode("{STR_VAR_1}", terminate=False): 12})
        line = next(l for par in paragraphs(laid) for l in lines_of(par) if "{STR_VAR_1}" in l)
        self.assertEqual(me.width(line, CM), 192)   # 30 characters and the 12 px slot
        self.assertEqual(self.drawn(line, slots), me.width(line, CM))
        self.assertEqual(line_width(SolidGlyphs(), CM.encode(line), slots=slots), me.width(line, CM))
        # a fixed blank draws 30 px wider than measured
        self.assertEqual(self.drawn(line, {}), me.width(line, CM) + 30)

    def test_placeholder_without_a_slot_width_takes_the_profile_width(self):
        line = "аа {STR_VAR_1} бб {STR_VAR_2} вв"
        me = entry_measure(self.PROFILE, self.LIM)
        slots = placeholder_px(line, me, CM)
        self.assertEqual(slots[CM.encode("{STR_VAR_2}", terminate=False)], 60)
        self.assertEqual(me.width(line, CM), 6 * 10 + 12 + 60)
        self.assertEqual(self.drawn(line, slots), me.width(line, CM))

    def test_slot_is_marked_at_both_ends(self):
        cv = Canvas(40, 16)
        slot = CM.encode("{STR_VAR_1}", terminate=False)
        draw_text(cv, SolidGlyphs(), 0, 0, slot, slots={slot: 12})
        label = PALETTE["label"]
        self.assertEqual([x for x, c in enumerate(cv.px[11]) if c == label], list(range(10)))   # underline
        self.assertEqual([x for x, c in enumerate(cv.px[9]) if c == label], [0, 9])             # end ticks

def inked(g: Glyph) -> Glyph:
    """The glyph with ink in its last column: its shadow there turned to ink."""
    px = [row[:] for row in g.pixels]
    for row in px:
        if row[g.width - 1] == SHADOW:
            row[g.width - 1] = INK
    return Glyph(g.char, g.width, px)

def art_measure(slots: dict, **kw) -> Measure:
    """Pack widths and shadow ends from glyph art; 6 px elsewhere."""
    return Measure(lambda b: slots[b].width if b in slots else 6, {"PLAYER": 42},
                   shadow_end=lambda b: b in slots and shadow_end(slots[b].pixels, slots[b].width), **kw)

class ShadowOverflowTest(unittest.TestCase):
    """A line 1 px too wide that clips only the shadow fits."""

    SLOTS = PACK.glyph_slots()
    YA = CM.encode("я", terminate=False)[0]
    M = art_measure(SLOTS)
    INKED = art_measure({**SLOTS, YA: inked(SLOTS[YA])})
    TEXT = "пустыня"

    def test_art_of_the_last_glyph(self):
        g = self.SLOTS[self.YA]
        self.assertTrue(shadow_end(g.pixels, g.width))
        self.assertIn(SHADOW, [row[g.width - 1] for row in g.pixels])   # shadow, not blank
        self.assertFalse(shadow_end(inked(g).pixels, g.width))
        self.assertFalse(shadow_end(g.pixels, 0))   # no column

    def test_shadow_column_fits_one_pixel_over(self):
        w = self.M.width(self.TEXT, CM)
        self.assertEqual(check(self.TEXT, Limit("single", w - 1, 1), self.M, CM), [])
        self.assertTrue(fits(self.TEXT, w - 1, self.M, CM))
        self.assertTrue(shadow_overflow(self.TEXT, w - 1, self.M, CM))
        self.assertFalse(shadow_overflow(self.TEXT, w, self.M, CM))   # fits without it

    def test_ink_column_fails_one_pixel_over(self):
        w = self.INKED.width(self.TEXT, CM)
        self.assertEqual(check(self.TEXT, Limit("single", w - 1, 1), self.INKED, CM),
                         [f"line too wide: {w} > {w - 1} px: {self.TEXT!r}"])
        self.assertEqual(check(self.TEXT, Limit("single", w, 1), self.INKED, CM), [])

    def test_two_pixels_over_always_fail(self):
        w = self.M.width(self.TEXT, CM)
        self.assertEqual(check(self.TEXT, Limit("single", w - 2, 1), self.M, CM),
                         [f"line too wide: {w} > {w - 2} px: {self.TEXT!r}"])
        self.assertFalse(fits(self.TEXT, w - 2, self.M, CM))

    def test_last_glyph_only(self):
        w = self.M.width(self.TEXT, CM)
        # trailing spaces and colour codes are not shown
        self.assertEqual(check(self.TEXT + " {COLOR 01}", Limit("single", w - 1, 1), self.M, CM), [])
        # a placeholder ends the line: no glyph, no shadow
        line = "Привет, {PLAYER}"
        n = self.M.width(line, CM)
        self.assertEqual(len(check(line, Limit("single", n - 1, 1), self.M, CM)), 1)
        # inked я inside the line, shadow glyph at the end
        line = self.TEXT + "б"
        self.assertEqual(check(line, Limit("single", self.INKED.width(line, CM) - 1, 1), self.INKED, CM), [])
        # a measure without shadow ends
        plain = Measure(self.M.glyph_width, {})
        self.assertEqual(len(check(self.TEXT, Limit("single", w - 1, 1), plain, CM)), 1)

    def test_window_style_and_other_windows(self):
        from gen3tl.check.entrycheck import window_problems
        w = self.M.width(self.TEXT, CM)
        # the style's shadow ends with its widths
        m = Measure(lambda b: 6, {}, styles={"small": self.M.glyph_width}, shadow_end=lambda b: False,
                    shadow_styles={"small": self.M.shadow_end})
        self.assertEqual(check(self.TEXT, Limit("single", w - 1, 1, style="small"), m, CM), [])
        self.assertEqual(check(self.TEXT, Limit("single", w - 1, 1, style="small"),
                               m.with_placeholders({"PLAYER": 30}), CM), [])
        win = lambda px: [{"font": "small", "width": px, "where": "box"}]   # noqa: E731
        self.assertEqual(window_problems(self.TEXT, win(w - 1), {"small": (self.M, CM)}), [])
        self.assertEqual(window_problems(self.TEXT, win(w - 1), {"small": (self.INKED, CM)}),
                         [f"line too wide: {w} > {w - 1} px in box (small)"])
        self.assertEqual(window_problems(self.TEXT, win(w - 2), {"small": (self.M, CM)}),
                         [f"line too wide: {w} > {w - 2} px in box (small)"])

    def test_written_breaks_kept(self):
        # fitting lines stay; no dropped breaks are reported
        text = self.TEXT + "\\n" + self.TEXT
        lim = Limit("dialogue", self.M.width(self.TEXT, CM) - 1, 2)
        self.assertEqual(layout(text, lim, self.M, CM), text)
        self.assertEqual(dropped_breaks(text, lim, self.M, CM), [])
        self.assertEqual(len(check(layout(text, lim, self.INKED, CM), lim, self.INKED, CM)), 2)
        self.assertEqual(len(dropped_breaks(text, lim, self.INKED, CM)), 2)

class ColumnAndFontTest(unittest.TestCase):
    """{CLEAR_TO n} moves text to column n; {FONT xx} sets the style."""

    # 6 px main style, 5 px small; FONT 00 small
    M = Measure(lambda b: 6, {"STR_VAR_1": 60}, styles={"main": lambda b: 6, "small": lambda b: 5},
                shadow_end=lambda b: False, shadow_styles={"main": lambda b: False, "small": lambda b: True},
                fonts={"00": "small", "02": "main"})

    def test_column_moves_the_text_after_it(self):
        self.assertEqual(self.M.width("аб{CLEAR_TO 1E}в", CM), 30 + 6)
        self.assertEqual(self.M.width("абвгд{CLEAR_TO 1E}в", CM), 30 + 6)   # ends at the column
        self.assertEqual(self.M.width("абвгдеж{CLEAR_TO 1E}в", CM), 42 + 6)   # past it: from its end
        self.assertEqual(self.M.width("а{CLEAR_TO 1E}б{CLEAR_TO 3C}в", CM), 60 + 6)
        # a trailing column is not shown
        self.assertEqual(line_width_of("аб{CLEAR_TO 1E}", self.M), 12)
        self.assertEqual(self.M.width("аб{CLEAR_TO 1E}", CM), 30)   # GetStringWidth counts it

    def test_text_past_its_column(self):
        lim = Limit("single", 100, 1)
        self.assertEqual(check("абвгд{CLEAR_TO 1E}в", lim, self.M, CM), [])
        self.assertEqual(check("абвгде{CLEAR_TO 1E}в", lim, self.M, CM),
                         ["line too wide: 36 > 30 px before the column {CLEAR_TO 1E}: 'абвгде'"])
        # each column with the text since the previous one
        self.assertEqual(check("а{CLEAR_TO 1E}бвгдежзий{CLEAR_TO 3C}в", lim, self.M, CM),
                         ["line too wide: 84 > 60 px before the column {CLEAR_TO 3C}: 'бвгдежзий'"])
        # the row past the window too: 102 + 12 > 108
        self.assertEqual(check("абвгдежзийклмнопр{CLEAR_TO 3C}в", Limit("single", 108, 1), self.M, CM),
                         ["line too wide: 102 > 60 px before the column {CLEAR_TO 3C}: 'абвгдежзийклмнопр'"])
        self.assertEqual(len(check("абвгдежзийклмнопр{CLEAR_TO 3C}вг", Limit("single", 108, 1), self.M, CM)), 2)
        # placeholders before a column are named
        self.assertEqual(check("{STR_VAR_1}{CLEAR_TO 1E}в", Limit("single", 100, 1, placeholders={"STR_VAR_1": 36},
                                                                 slot_sources={"STR_VAR_1": "test"}), self.M, CM),
                         ["line too wide: 36 > 30 px before the column {CLEAR_TO 1E}: '{STR_VAR_1}' with "
                          "{STR_VAR_1} = 36 px (test)"])

    def test_font_switch(self):
        self.assertEqual(self.M.width("аб{FONT 00}вг", CM), 12 + 10)
        self.assertEqual(self.M.width("{FONT 00}аб{FONT 02}вг", CM), 10 + 12)
        self.assertEqual(self.M.width("аб{FONT 07}вг", CM), 24)   # unknown font: the style stays
        self.assertEqual(Measure(lambda b: 6, {}).width("аб{FONT 00}вг", CM), 24)   # no fonts
        # price row: name to column, main-font space, small price
        self.assertEqual(self.M.width("абв{CLEAR_TO 1E} {FONT 00}8 БО", CM), 30 + 6 + 20)
        # a small-style entry switches to the main style
        small = entry_measure(self.M, Limit("single", 100, 1, style="small"))
        self.assertEqual(small.width("аб{FONT 02}вг", CM), 10 + 12)
        self.assertEqual(self.M.with_placeholders({"STR_VAR_1": 6}).width("{FONT 00}а{STR_VAR_1}", CM), 5 + 6)

    def test_shadow_of_the_style_the_line_ends_in(self):
        # small glyphs end in shadow here, main ones do not
        line = "аб{FONT 00}вг"   # 22 px, ends small
        self.assertEqual(check(line, Limit("single", 21, 1), self.M, CM), [])
        line = "{FONT 00}аб{FONT 02}вг"   # 22 px, ends main
        self.assertEqual(check(line, Limit("single", 21, 1), self.M, CM),
                         [f"line too wide: 22 > 21 px: {line!r}"])

    def test_wrap_measures_columns(self):
        text = "аб{CLEAR_TO 3C}в где"   # 66 px with the column
        self.assertEqual(layout(text, Limit("dialogue", 70, 2), self.M, CM), "аб{CLEAR_TO 3C}в\\nгде")

def line_width_of(line, m):
    from gen3tl.check.layout import line_width
    return line_width(line, m, CM)

class IconSpacingTest(unittest.TestCase):
    """A printer's letter spacing widens key icons, not glyphs."""

    def test_icon_spacing(self):
        m = Measure(lambda b: 6, {"F8 00": 8, "F8 03": 16, "PLAYER": 42})
        from gen3tl.check.layout import line_width as width
        plain = Limit("dialogue", 208, 2)
        spaced = Limit("dialogue", 208, 2, icon_spacing=1)
        line = "ab {F8 00} {F8 03} {PLAYER}"
        self.assertEqual(width(line, entry_measure(m, plain), CM) + 2, width(line, entry_measure(m, spaced), CM))
        self.assertEqual(entry_measure(m, spaced).ph["PLAYER"], 42)
