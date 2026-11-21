import unittest

from gen3tl.lang.names import language_name


class LanguageNamesTest(unittest.TestCase):
    def test_listed_codes_have_a_name(self):
        for code, name in (("ru", "Russian"), ("de", "German"), ("es", "Spanish"), ("fr", "French"),
                           ("pt-BR", "Portuguese")):
            with self.subTest(code=code):
                self.assertEqual(language_name(code), name)

    def test_an_unlisted_code_names_itself(self):
        self.assertEqual(language_name("xx"), "xx")


if __name__ == "__main__":
    unittest.main()
