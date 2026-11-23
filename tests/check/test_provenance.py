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
