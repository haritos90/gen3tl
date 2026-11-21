import unittest

from gen3tl.core.charmap import Charmap, CharmapError

BASE_CHARMAP = Charmap()

class CodecTest(unittest.TestCase):
    cm = BASE_CHARMAP

    def test_basic_roundtrip(self):
        text = "Hello, {PLAYER}!\\nIt’s {COLOR 06}Pokémon{COLOR 08} time.\\pBye…"
        data = self.cm.encode(text)
        self.assertEqual(data[-1], 0xFF)
        back, end = self.cm.decode(data)
        self.assertEqual(back, text)
        self.assertEqual(end, len(data))

    def test_battle_placeholders(self):
        data = bytes([0xFD, 0x0F, 0x00, 0xFD, 0x14, 0xAB, 0xFF])
        text, _ = self.cm.decode(data, mode="battle")
        self.assertEqual(text, "{B_ATK_NAME_WITH_PREFIX} {B_CURRENT_MOVE}!")
        self.assertEqual(self.cm.encode(text), data)
        # field mode keeps battle placeholders raw and lossless
        text, _ = self.cm.decode(data, mode="field")
        self.assertEqual(text, "{FD 0F} {FD 14}!")
        self.assertEqual(self.cm.encode(text), data)

    def test_every_byte_pair_roundtrips(self):
        for a in range(0xFF):
            for b in range(0xFF):
                data = bytes([a, b, 0x00, 0x00, 0x00, 0xFF])
                try:
                    text, end = self.cm.decode(data)
                except CharmapError:
                    continue  # a control code swallowed the terminator
                self.assertEqual(self.cm.encode(text), data[:end], (a, b, text))

    def test_extra_chars(self):
        cm = Charmap({"Б": 0x37, "А": 0x87})
        self.assertEqual(cm.encode("БАA"), bytes([0x37, 0x87, 0xBB, 0xFF]))
        with self.assertRaises(CharmapError):
            Charmap().encode("Б")

    def test_extra_chars_decode_and_own_their_bytes(self):
        # every letter owns its byte: decoding is unambiguous
        cm = Charmap({"Б": 0x37, "А": 0x87})
        self.assertEqual(cm.decode(bytes([0x37, 0x87, 0xBB, 0xFF])), ("БАA", 4))
        self.assertEqual(Charmap().decode(bytes([0x87, 0xFF])), ("{87}", 2))
        for shared in ({"А": 0xBB}, {"Б": 0x53}, {"Б": 0xF8}, {"Б": 0x37, "В": 0x37}, {"A": 0x87}):
            with self.assertRaises(CharmapError, msg=shared):
                Charmap(shared)

    def test_language_pack_round_trip(self):
        # every byte sequence, pack letters included
        from gen3tl.lang.langpack import LangPack
        cm = LangPack.load("ru").charmap()
        for b in range(0xF7):
            data = bytes([b, 0xFF])
            self.assertEqual(cm.encode(cm.decode(data)[0]), data, hex(b))
        text = "Съешь же ещё этих мягких французских булок, да выпей чаю. ЁЖИК Сара"
        self.assertEqual(cm.decode(cm.encode(text))[0], text)

    def test_errors(self):
        with self.assertRaises(CharmapError):
            self.cm.encode("{NOPE}")
        with self.assertRaises(CharmapError):
            self.cm.encode("{COLOR}")
        with self.assertRaises(CharmapError):
            self.cm.decode(b"\xbb\xbc")
