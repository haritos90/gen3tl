import unittest

from gen3tl.check import provenance

class ProvenanceTest(unittest.TestCase):
    """A provenance text is a code with its arguments; the text comes from one message table."""

    def test_every_message_reads_back_as_its_code(self):
        for code, template in provenance.MESSAGES.items():
            names = provenance.FIELD.findall(template)
            args = {name: {"code": "english_widest"} if name in provenance.NESTED else f"{name} value"
                    for name in names}
            coded = {"code": code, **args}
            with self.subTest(code=code):
                text = provenance.formatted(coded)
                self.assertNotIn("{", text)
                self.assertEqual(provenance.formatted(provenance.coded(text)), text)

    def test_nested_rules_and_numbers_keep_their_arguments(self):
        text = provenance.text("centred", reference="s0123456", rule=provenance.text(
            "box_lines_via", via=provenance.text("table_window", name="field-messages")))
        self.assertEqual(text, "two lines per message box ([[layout.table_window]] field-messages), centred against s0123456")
        self.assertEqual(provenance.coded(text), {"code": "centred", "rule": {"code": "box_lines_via", "via": {
            "code": "table_window", "name": "field-messages"}}, "reference": "s0123456"})
        self.assertEqual(provenance.coded("[[table]] items: a field of 14 bytes with the terminator"),
                         {"code": "table_field", "table": "items", "bytes": 14})
        self.assertEqual(provenance.coded("[[strings.interior]] 0x1A2B: read from byte 3, in place in its 012 bytes"),
                         {"code": "interior", "pointer": "0x1A2B", "offset": 3, "bytes": "012"})

    def test_a_text_without_a_message_is_refused(self):
        with self.assertRaisesRegex(ValueError, "without a message code"):
            provenance.coded("measured by hand")
