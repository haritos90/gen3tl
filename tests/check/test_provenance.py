import json
import unittest

from gen3tl.check import provenance


class ProvenanceTest(unittest.TestCase):
    """An explanation is a code with its arguments; its English text comes from one message table."""

    def test_every_message_takes_its_arguments_in_template_order(self):
        for code, template in provenance.MESSAGES.items():
            names = list(dict.fromkeys(provenance.FIELD.findall(template)))
            args = {name: [provenance.code("slot_no_fill")] if name in provenance.JOINED
                    else provenance.code("english_widest") if name in provenance.NESTED else f"{name} value"
                    for name in reversed(names)}
            with self.subTest(code=code):
                coded = provenance.code(code, **args)
                self.assertEqual(list(coded), ["code", *names])
                self.assertNotIn("{", provenance.formatted(coded))

    def test_nested_explanations_lists_and_numbers_format_as_their_text(self):
        rule = provenance.code("centred", rule=provenance.code(
            "box_lines_via", via=provenance.code("table_window", name="field-messages")), reference="s0123456")
        self.assertEqual(provenance.formatted(rule),
                         "two lines per message box ([[layout.table_window]] field-messages), centred against s0123456")
        self.assertEqual(provenance.formatted(provenance.code("table_field", table="items", bytes=14)),
                         "[[table]] items: a field of 14 bytes with the terminator")
        word = provenance.code("slot_word", id="items:1", text=repr("Зелье"))
        source = provenance.code("slot_reasons", source=provenance.code("slot_record", kind="item", word=word),
                                 reasons=[provenance.code("slot_unfilled", unfilled=1, uses=2),
                                          provenance.code("slot_unnamed", kinds=["nickname", "number"])])
        self.assertEqual(provenance.formatted(source), "item items:1 'Зелье' (1 of 2 walked paths set no value; "
                                                       "a value the scripts do not name: nickname, number)")
        self.assertEqual(provenance.formatted(provenance.code("slot_native", table="names", ids=["s0000001", "s0000002"])),
                         "native table names: s0000001 + s0000002")
        fill = provenance.code("slot_english", fill=provenance.code("slot_digits", digits=3), source_width=18)
        self.assertEqual(provenance.formatted(provenance.code("slot_code_fill", fill=fill)),
                         "number of 3 digits, English 18 px, code fill")
        self.assertEqual(provenance.formatted("measured by hand"), "measured by hand")

    def test_arguments_must_match_the_template(self):
        with self.assertRaisesRegex(ValueError, "differ"):
            provenance.code("table_field", table="items")
        with self.assertRaisesRegex(ValueError, "differ"):
            provenance.code("box_lines", via="extra")
        with self.assertRaises(KeyError):
            provenance.code("measured by hand")

    def test_a_read_back_explanation_takes_template_order_again(self):
        read = {"code": "centred", "reference": "s0123456",
                "rule": {"name": "menu", "where": "the bag", "code": "table_window_where"}}
        canonical = provenance.canonical(read)
        self.assertEqual(canonical, read)
        self.assertEqual(list(canonical), ["code", "rule", "reference"])
        self.assertEqual(list(canonical["rule"]), ["code", "name", "where"])
        reasons = provenance.canonical({"reasons": [{"references": 2, "code": "slot_unwalked"}], "code": "slot_reasons",
                                        "source": {"code": "slot_profile"}})
        self.assertEqual(list(reasons), ["code", "source", "reasons"])
        self.assertEqual(list(reasons["reasons"][0]), ["code", "references"])

    def test_a_check_problem_is_a_code_with_its_notes_after_it(self):
        code, noted = provenance.code, provenance.noted
        wide = code("line_wide_slots_window", width=210, limit=196, text=repr("Привет, {PLAYER}"),
                    slots=[code("slot_item", token="{PLAYER}", width=42, source=code("slot_profile")),
                           code("slot_item", token="{STR_VAR_1}", width=60, source=code(
                               "slot_parts", source=code("slot_unavailable", source=code("slot_context")),
                               parts=[code("slot_part", text="Ян", width=12), code("slot_no_prefix")]))],
                    where=code("table_window", name="menu"))
        problem = noted(noted(wide, "warning"), "unmeasured_width")
        self.assertEqual(provenance.formatted(problem),
                         "line too wide: 210 > 196 px: 'Привет, {PLAYER}' with {PLAYER} = 42 px (profile width); "
                         "{STR_VAR_1} = 60 px (context records; recorded width; sources unavailable; «Ян» 12 px, "
                         "no prefix 0 px); window [[layout.table_window]] menu (warning) "
                         "[unmeasured limit: the English width]")
        self.assertEqual(provenance.unnoted(problem), wide)
        self.assertTrue(provenance.is_warning(problem))
        self.assertFalse(provenance.is_warning(noted(wide, "unmeasured_width")))
        self.assertEqual(provenance.canonical(json.loads(json.dumps(problem, sort_keys=True))), problem)
        self.assertEqual(provenance.formatted(noted(code("line_wide", width=3, limit=2, text="'x'"), "prefix_stripped",
                                                    prefix="'A: '")), "line too wide: 3 > 2 px: 'x' (prefix 'A: ' stripped)")

    def test_an_encoder_error_is_coded_inside_its_problem(self):
        from gen3tl.core.charmap import Charmap, CharmapError
        for text, error in (("a\u2603", code_of("unmapped_character", char=repr("\u2603"), unicode="2603", text=repr("a\u2603"))),
                            ("a\\q", code_of("unknown_escape", escape="\\q", at=1, text=repr("a\\q"))),
                            ("a{", code_of("unclosed_brace", at=1, text=repr("a{"))),
                            ("{}", code_of("empty_token", text=repr("{}"))),
                            ("{CLEAR_TO}", code_of("token_arguments", token="{CLEAR_TO}", n=1, text=repr("{CLEAR_TO}"))),
                            ("{NOPE}", code_of("unknown_token", token="{NOPE}", text=repr("{NOPE}")))):
            with self.subTest(text=text), self.assertRaises(CharmapError) as raised:
                Charmap().encode(text)
            self.assertEqual(raised.exception.coded, error)
            self.assertEqual(str(raised.exception), provenance.formatted(error))
            self.assertEqual(provenance.encoding_problem(raised.exception), code_of("not_encodable", error=error))
        plain = CharmapError("no terminator after 0x10")
        self.assertEqual(provenance.encoding_problem(plain), code_of("not_encodable", error="no terminator after 0x10"))
        self.assertEqual(provenance.formatted(provenance.encoding_problem(plain)), "not encodable: no terminator after 0x10")

    def test_profile_rule_sources_format_as_their_phrase(self):
        for source, text in ((code_of("rule_table_save_link", table="species"), "profile [[table]] species save_link"),
                             (code_of("rule_strings", key="save_link"), "profile [strings] save_link"),
                             (code_of("rule_computed", pointer="0x8123"), "profile [[strings.computed]] 0x8123"),
                             (code_of("rule_code_fill_where", name="fill-20", where="battle text"),
                              "profile [[layout.code_fill]] fill-20: battle text"),
                             (code_of("rule_droppable"), "profile [[qa.droppable]]")):
            self.assertEqual(provenance.formatted(source), text)


def code_of(which, /, **args):
    return provenance.code(which, **args)
