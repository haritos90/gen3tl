import struct
import unittest

from gen3tl.verify.savefile import checksum, renamed, transliterate
from savefile_support import padded, setUpModule

class SaveTest(unittest.TestCase):


    def test_checksum_folds_words(self):
        self.assertEqual(checksum(struct.pack("<II", 0x00010002, 0x00030004), 8), 0x0004 + 0x0006)

class TransliterationTest(unittest.TestCase):
    def test_scheme(self):
        cases = {"Саша": "Sasha", "Женя": "Zhenya", "Юля": "Yulya", "Щука": "Schuka", "Хрюша": "Khryusha",
                 "Цапля": "Tsaplya", "Чиж": "Chizh", "Йод": "Yod", "Андрей": "Andrey", "Мышь": "Mysh",
                 "Подъезд": "Podezd", "Эля": "Elya", "Ёлка": "Yolka", "бвгдзиклмнопрстуф": "bvgdziklmnoprstuf"}
        for cyr, lat in cases.items():
            self.assertEqual(transliterate(cyr), lat, cyr)

    def test_capitals(self):
        self.assertEqual(transliterate("Ж"), "Zh")          # one capital: a capitalised group
        self.assertEqual(transliterate("Щ"), "Sch")
        self.assertEqual(transliterate("ЖЕНЯ"), "ZHENYA")   # a name wholly in capitals stays in capitals
        self.assertEqual(transliterate("ЩУКА"), "SCHUKA")
        self.assertEqual(transliterate("ЮЛЯ 2"), "YULYA 2")
        self.assertEqual(transliterate("ЖеНя"), "ZheNya")
        self.assertEqual(transliterate("Ёжик"), "Yozhik")

class CyrillicNameTest(unittest.TestCase):


    def test_renamed_fits_any_field_length(self):
        self.assertEqual(renamed(padded(b"ABCD", 7), b"ABCD", b"ABCDEFG"), b"ABCDEFG")     # OT name, 7 bytes
        self.assertEqual(renamed(b"ABCDEFG", b"ABCDEFG", b"XY"), padded(b"XY", 7))
        self.assertEqual(renamed(padded(b"ABC", 8), b"ABC", b"ABCDEFG"), padded(b"ABCDEFG", 8))
