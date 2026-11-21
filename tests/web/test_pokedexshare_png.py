import struct
import unittest
import zlib

from gen3tl.web.pokedexshare import png_bytes, png_pixels


def rgba_png(width, height, pixels):
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload))
    raw = b''.join(b'\0' + pixels[y * width * 4:(y + 1) * width * 4] for y in range(height))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))


def chunks(data):
    result = {}
    at = 8
    while at < len(data):
        size = int.from_bytes(data[at:at + 4], 'big')
        result[data[at + 4:at + 8]] = data[at + 8:at + 8 + size]
        at += size + 12
    return result


class IndexedPNGTest(unittest.TestCase):
    def assert_round_trip(self, width, height, pixels, data):
        decoded_width, decoded_height, rows = png_pixels(data)
        self.assertEqual((decoded_width, decoded_height), (width, height))
        self.assertEqual(b''.join(bytes(pixel) for row in rows for pixel in row), pixels)

    def test_palette_order_follows_first_occurrence_deterministically(self):
        pixels = bytearray((200, 10, 20, 255, 30, 40, 50, 255, 200, 10, 20, 255, 0, 0, 0, 255))
        data = png_bytes(4, 1, pixels)
        self.assertEqual(data, png_bytes(4, 1, pixels))
        self.assertEqual(chunks(data)[b'PLTE'], bytes((200, 10, 20, 30, 40, 50, 0, 0, 0)))
        self.assert_round_trip(4, 1, pixels, data)

    def test_opaque_palette_has_no_transparency_chunk(self):
        data = png_bytes(1, 1, bytearray((1, 2, 3, 255)))
        self.assertNotIn(b'tRNS', chunks(data))

    def test_transparency_distinguishes_equal_rgb_colours(self):
        pixels = bytearray((1, 2, 3, 255, 1, 2, 3, 0, 1, 2, 3, 127, 4, 5, 6, 255))
        data = png_bytes(2, 2, pixels)
        self.assertEqual(chunks(data)[b'tRNS'], bytes((255, 0, 127)))
        self.assertEqual(len(chunks(data)[b'PLTE']), 12)
        self.assert_round_trip(2, 2, pixels, data)

    def test_smallest_bit_depth_round_trips_padded_rows(self):
        for count, depth in ((1, 1), (2, 1), (3, 2), (4, 2), (5, 4), (16, 4), (17, 8), (256, 8)):
            with self.subTest(colours=count):
                width, height = 19, 17
                pixels = bytearray(channel for index in range(width * height)
                                   for channel in (index % count, 50, 100, 255))
                data = png_bytes(width, height, pixels)
                self.assertEqual(chunks(data)[b'IHDR'][8:10], bytes((depth, 3)))
                self.assert_round_trip(width, height, pixels, data)

    def test_more_than_256_colours_retains_rgba_encoding(self):
        pixels = bytearray(channel for index in range(257) for channel in (index % 256, index // 256, 10, 255))
        data = png_bytes(257, 1, pixels)
        self.assertEqual(data, rgba_png(257, 1, pixels))
        self.assertNotIn(b'PLTE', chunks(data))
        self.assert_round_trip(257, 1, pixels, data)

    def test_indexed_pixels_use_highest_compression(self):
        data = png_bytes(19, 17, bytearray((1, 2, 3, 255)) * (19 * 17))
        compressed = chunks(data)[b'IDAT']
        self.assertEqual(compressed, zlib.compress(zlib.decompress(compressed), 9))
