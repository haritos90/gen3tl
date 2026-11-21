from test_roots import TOOLKIT_ROOT
"""Graphics discovery consumers checks and fixtures."""

import sys
from pathlib import Path
import struct
import tempfile
import unittest

sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from gen3tl.build.graphics import lz77_compress
from gen3tl.core.rom import Rom, ptr_bytes
from gen3tl.discover import consumerproof as cp
from gen3tl.discover import graphics as gfx
from graphics_discovery_support import (
    CPUSET, FASTSET, LZV, ProbeAsm, bl_bytes, ldr_literal, probe_palette, probe_rom, words,
)


EQ, NE = 0, 1


def probe_sheet(seed: int) -> bytes:
    return bytes(((seed + k) * 7) & 0xFF for k in range(96))


def probe_candidate(result: dict, offset: int):
    return next(c for c in result["candidates"] if c.offset == offset)


class NegativeTrustTest(unittest.TestCase):
    def assert_rejected(self, rom: Rom, offset: int):
        result = gfx.scan(rom)
        c = next(x for x in result["candidates"] if x.offset == offset)
        self.assertIsNone(c.palette_for(4))
        self.assertFalse(any(p.get("tier") in gfx.ROM_EVIDENCE_TIERS for p in c.palettes))
        row = {**c.to_dict(), "palette": "", "palette_bpp": None,
               "palette_source": "candidate" if c.palettes else "missing",
               "render_modes": c.render_modes()}
        with self.assertRaises(ValueError):
            gfx._candidate_row(row, "keep", "untrusted")

    def test_neg1_arbitrary_bl_with_r2_32_is_not_a_palette(self):
        rom = Rom(b"\x00" * 0x10000)
        rom.write(0x1000, lz77_compress(bytes(range(32))))
        rom.write(0x3000, b"\xab" * 32)
        rom.write(0x640, ptr_bytes(0x3000))
        rom.write(0x644, ptr_bytes(0x1000))
        rom.write(0x600, words(0x480F, 0x2100, 0x2220) + b"\x00\xf0\x00\xf8")
        rom.write(0x60A, words(0x490E))
        self.assert_rejected(rom, 0x1000)

    def test_neg2b_arbitrary_pointer_triple_is_not_a_descriptor_palette(self):
        rom = Rom(b"\x00" * 0x10000)
        rom.write(0x3000, lz77_compress(bytes(range(32))))
        rom.write(0x4000, lz77_compress(bytes(640 * 2)))
        rom.write(0x5000, bytes(range(32)))
        rom.write(0x2000, ptr_bytes(0x3000) + ptr_bytes(0x4000) + ptr_bytes(0x5000))
        self.assert_rejected(rom, 0x3000)
        self.assert_rejected(rom, 0x4000)

    def test_neg3b_adjacent_bytes_plus_far_unverified_copy_is_not_a_palette(self):
        rom = Rom(b"\x00" * 0x10000)
        blob = lz77_compress(bytes(range(32)))
        rom.write(0x1000, blob)
        rom.write(0x1000 + len(blob), bytes(range(32)))
        rom.write(0x200, ptr_bytes(0x1000))
        rom.write(0x6000, words(0x480F, 0x2100, 0x2220) + b"\x00\xf0\x00\xf8")
        rom.write(0x6040, ptr_bytes(0x1000 + len(blob)))
        self.assert_rejected(rom, 0x1000)

    def test_direct_cpu_set_requires_palette_destination_and_unclobbered_source(self):
        for pop in (False, True):
            rom = Rom(b"\x00" * 0x10000)
            rom.write(0x3000, bytes(range(32)))
            rom.write(0x640, ptr_bytes(0x3000))
            code = words(0x480F, *( (0xBC01,) if pop else () ), 0x2100, 0x2220)
            call = 0x608 if pop else 0x606
            rom.write(0x600, code + bl_bytes(call, 0x9000))
            rom.write(0x9000, words(0xDF0B, 0x4770))
            result = gfx.scan(rom)
            self.assertFalse(any(p.get("tier") in gfx.ROM_EVIDENCE_TIERS for p in result["palettes"]))

    def test_unused_third_argument_does_not_associate_graphic(self):
        rom = Rom(b"\x00" * 0x10000)
        rom.write(0x1000, lz77_compress(bytes(range(32))))
        rom.write(0x3000, bytes(range(32)))
        rom.write(0x200, ptr_bytes(0x1000))
        rom.write(0x640, ptr_bytes(0x3000) + ptr_bytes(0x1000))
        code = words(ldr_literal(0x600, 0, 0x640), 0x2100, 0x2220,
                     ldr_literal(0x606, 3, 0x644)) + bl_bytes(0x608, 0x9000)
        rom.write(0x600, code)
        rom.write(0x9000, words(0xDF0B, 0x4770))
        self.assert_rejected(rom, 0x1000)

    def test_branch_exclusive_consumers_do_not_associate(self):
        rom = Rom(b"\x00" * 0x10000)
        rom.write(0x1000, lz77_compress(bytes(range(32))))
        rom.write(0x3000, bytes(range(32)))
        rom.write(0x200, ptr_bytes(0x1000))
        rom.write(0x540, ptr_bytes(0x1000) + ptr_bytes(0x3000))
        beq = 0xD000 | ((0x50E - 0x506) // 2)
        code = words(ldr_literal(0x500, 0, 0x540), beq,
                     ldr_literal(0x504, 0, 0x544), 0x2100, 0x2220)
        rom.write(0x500, code + bl_bytes(0x50A, 0x9000) + bl_bytes(0x50E, 0x9200))
        rom.write(0x9000, words(0xDF0B, 0x4770))
        rom.write(0x9200, words(0xDF11, 0x4770))
        self.assert_rejected(rom, 0x1000)

    def test_unused_descriptor_reads_do_not_prove_fields(self):
        rom = Rom(b"\x00" * 0x10000)
        rom.write(0x3000, lz77_compress(bytes(range(96))))
        entries = [(i % 2) | ((i % 2) << 10) | ((i % 3 == 2) << 11) for i in range(640)]
        rom.write(0x4000, lz77_compress(words(*entries)))
        rom.write(0x5000, bytes(range(32)))
        rom.write(0x2000, ptr_bytes(0x3000) + ptr_bytes(0x4000) + ptr_bytes(0x5000))
        rom.write(0x660, ptr_bytes(0x2000))
        rom.write(0x664, (0x05000000).to_bytes(4, "little"))
        rom.write(0x668, ptr_bytes(0x1234))
        code = words(ldr_literal(0x600, 0, 0x660), 0x00A9, 0x5842, 0x3104, 0x5843,
                     0x3104, 0x5844, ldr_literal(0x60E, 1, 0x664),
                     ldr_literal(0x610, 0, 0x668)) + bl_bytes(0x612, 0x9000)
        rom.write(0x600, code + words(ldr_literal(0x616, 0, 0x668)) + bl_bytes(0x618, 0x9200))
        rom.write(0x9000, words(0xDF0B, 0x4770))
        rom.write(0x9200, words(0xDF11, 0x4770))
        self.assert_rejected(rom, 0x3000)
        self.assert_rejected(rom, 0x4000)

    def test_adjacency_never_promotes_fake_palette_copy(self):
        rom = Rom(b"\x00" * 0x10000)
        blob = lz77_compress(bytes(range(96)))
        palette = 0x1000 + len(blob)
        rom.write(0x1000, blob)
        rom.write(palette, bytes(range(32)))
        rom.write(0x200, ptr_bytes(0x1000))
        rom.write(0x640, ptr_bytes(palette))
        rom.write(0x600, words(0x480F, 0x2100, 0x2220) + bl_bytes(0x606, 0x9000))
        rom.write(0x9000, words(0xDF0B, 0x4770))
        self.assert_rejected(rom, 0x1000)


class ConsumerProofTest(unittest.TestCase):
    def wrapper_rom(self, destination: int, with_dma: bool = False) -> Rom:
        rom = Rom(b"\x00" * 0x10000)
        rom.write(0x3000, bytes(range(32)))
        rom.write(0x640, ptr_bytes(0x3000))
        rom.write(0x600, words(0x480F, 0x21D0, 0x2220) + bl_bytes(0x606, 0x9000))
        rom.write(0x9000, words(0x0049, 0x4B0F, 0x18C9, 0x0852) + bl_bytes(0x9008, 0x9200) + words(0x4770))
        rom.write(0x9040, destination.to_bytes(4, "little"))
        rom.write(0x9200, words(0xDF0B, 0x4770))
        if with_dma:
            rom.write(0x500, words(0x490F, 0x22A0, 0x04D2, 0x480F, 0x6001, 0x6042,
                                   0x490E, 0x6081, 0x4770))
            rom.write(0x540, destination.to_bytes(4, "little"))
            rom.write(0x544, (0x040000D4).to_bytes(4, "little"))
            rom.write(0x548, (0x80000200).to_bytes(4, "little"))
        return rom

    def test_bank_wrapper_proves_hardware_destination_and_count(self):
        rom = self.wrapper_rom(0x05000000)
        records = gfx.palette_loads(rom, gfx.literal_loads(rom))
        record = next(p for p in records if p["spec"] == "raw:0x0003000")
        self.assertEqual((record["bank"], record["tier"]), (13, "consumer"))
        self.assertEqual(record["evidence"]["length"], 32)
        self.assertEqual(record["evidence"]["destination"], 0x050001A0)
        self.assertEqual(record["evidence"]["destination_kind"], "hardware:bg")

    def test_palette_destination_bounds_are_enforced(self):
        rom = self.wrapper_rom(0x05000400)
        self.assertFalse(gfx.palette_loads(rom, gfx.literal_loads(rom)))

    def test_unexecuted_global_dma_does_not_prove_staging(self):
        rom = self.wrapper_rom(0x02000000, with_dma=True)
        transfers = cp.dma_transfers(rom)
        self.assertEqual([(t.source, t.destination, t.length) for t in transfers],
                         [(0x02000000, 0x05000000, 1024)])
        self.assertFalse(gfx.palette_loads(rom, gfx.literal_loads(rom)))
        without_dma = self.wrapper_rom(0x02000000)
        self.assertFalse(gfx.palette_loads(without_dma, gfx.literal_loads(without_dma)))

    def test_relocated_wrapper_preserves_proof(self):
        rom = Rom(b"\x00" * 0x10000)
        rom.write(0x3000, bytes(range(32)))
        rom.write(0xA40, ptr_bytes(0x3000))
        rom.write(0xA00, words(0x480F, 0x21D0, 0x2220) + bl_bytes(0xA06, 0xB000))
        rom.write(0xB000, words(0x0049, 0x4B0F, 0x18C9, 0x0852) +
                  bl_bytes(0xB008, 0xB200) + words(0x4770))
        rom.write(0xB040, (0x05000000).to_bytes(4, "little"))
        rom.write(0xB200, words(0xDF0B, 0x4770))
        records = gfx.palette_loads(rom, gfx.literal_loads(rom))
        record = next(p for p in records if p["spec"] == "raw:0x0003000")
        self.assertEqual(record["evidence"]["call"], 0xA06)
        self.assertEqual(record["evidence"]["destination"], 0x050001A0)
        self.assertEqual(record["bank"], 13)

    def descriptor_rom(self) -> Rom:
        rom = Rom(b"\x00" * 0x10000)
        rom.write(0x3000, lz77_compress(bytes(range(96))))
        entries = [(i % 2) | ((i % 2) << 10) | ((i % 3 == 2) << 11) for i in range(640)]
        rom.write(0x4000, lz77_compress(words(*entries)))
        rom.write(0x5000, bytes(32))
        rom.write(0x2000, ptr_bytes(0x3000) + ptr_bytes(0x4000) + ptr_bytes(0x5000))
        rom.write(0x660, ptr_bytes(0x2000))
        for at, value in ((0x664, 0x06000000), (0x668, 0x06008000), (0x66C, 0x05000000)):
            rom.write(at, value.to_bytes(4, "little"))
        code = (words(ldr_literal(0x600, 4, 0x660), 0x6820,
                      ldr_literal(0x604, 1, 0x664)) + bl_bytes(0x606, 0x9000) +
                words(0x6860, ldr_literal(0x60C, 1, 0x668)) + bl_bytes(0x60E, 0x9000) +
                words(0x68A0, ldr_literal(0x614, 1, 0x66C), 0x2210) +
                bl_bytes(0x618, 0x9200) + words(0x4770))
        rom.write(0x600, code)
        rom.write(0x9000, words(0xDF12, 0x4770))
        rom.write(0x9200, words(0xDF0B, 0x4770))
        return rom

    def test_exact_descriptor_fields_and_monochrome_palette_materialize(self):
        rom = self.descriptor_rom()
        result = gfx.scan(rom)
        sheet = next(c for c in result["candidates"] if c.offset == 0x3000)
        tilemap = next(c for c in result["candidates"] if c.offset == 0x4000)
        palette = sheet.palette_for(4)
        self.assertEqual((palette["spec"], palette["tier"]), ("raw:0x0005000", "descriptor"))
        self.assertTrue(any(m.get("tier") == "descriptor" for m in tilemap.tilemaps))
        row = {**sheet.to_dict(), "palette": palette["spec"], "palette_bpp": 4,
               "palette_source": "rom-evidence", "render_modes": sheet.render_modes()}
        self.assertIn('palette = "raw:0x0005000', gfx._candidate_row(row, "keep", "proved"))
        verified = next(p for p in result["palettes"] if p.get("tier") == "descriptor")
        self.assertEqual(verified["evidence"]["verified"]["fields"], {0: 0, 4: 1, 8: 2})


class ReviewRegressionTest(unittest.TestCase):
    def specs(self, rom: Rom) -> set[str]:
        return {p["spec"] for p in gfx.palette_loads(rom, gfx.literal_loads(rom))}

    def trusted(self, result: dict, offset: int) -> list[dict]:
        return [p for p in probe_candidate(result, offset).palettes if p.get("tier") in gfx.ROM_EVIDENCE_TIERS]

    def materialize(self, rom: Rom, offset: int = 0x1000) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc = gfx.write(gfx.scan(rom), rom, root / "discovery", render=False)
            path = root / "graphics.toml"
            path.write_text('version = "2.0.0"\n', encoding="utf-8")
            gid = f"g{offset:07X}"
            gfx.write_flags(path, [{"id": gid, "category": "translate", "reason": "probe"}], doc, rom)
            return path.read_text(encoding="utf-8")

    def with_sheet(self, build) -> Rom:
        rom = probe_rom()
        rom.write(0x1000, lz77_compress(probe_sheet(9)))
        a = ProbeAsm(rom, 0x600, 0x700)
        build(rom, a)
        a.ldr(0, 0x08001000).ldr(1, 0x06000000).bl(LZV).h(0x4770).done()
        return rom

    def flag_rom(self, build, condition=EQ) -> Rom:
        rom = probe_rom()
        rom.write(0x1000, lz77_compress(probe_sheet(41)))
        rom.write(0x3000, probe_palette(41))
        rom.write(0x3400, probe_palette(42))
        a = ProbeAsm(rom, 0x600, 0x780)
        build(a)
        a.b("good", condition)
        a.ldr(0, 0x08003000).ldr(1, 0x05000000).mov(2, 16).bl(CPUSET).b("join")
        a.label("good").ldr(0, 0x08003400).ldr(1, 0x05000000).mov(2, 16).bl(CPUSET)
        a.label("join").ldr(0, 0x08001000).ldr(1, 0x06000000).bl(LZV).h(0x4770).done()
        return rom

    def test_thumb_flag_preservation_and_arithmetic_controls(self):
        cases = {
            "nothing": None,
            "add_pc": 0xA200,
            "add_sp": 0xAA00,
            "high_add": 0x4480,
            "high_mov": 0x4680,
        }
        for name, halfword in cases.items():
            def compare(a):
                a.mov(3, 0).h(0x2B00)
                if halfword is not None:
                    a.h(halfword)
            self.assertEqual(self.specs(self.flag_rom(compare)), {"raw:0x0003400"}, name)

        def high_compare(a):
            a.mov(3, 0).h(0x2B01).ldr(0, 1).h(0x4680, 0x45C0)
        self.assertEqual(self.specs(self.flag_rom(high_compare)), {"raw:0x0003400"})
        self.assertEqual(self.specs(self.flag_rom(lambda a: a.ldr(3, 0xFFFFFFFF).h(0x3301), 2)),
                         {"raw:0x0003400"})
        self.assertEqual(self.specs(self.flag_rom(lambda a: a.ldr(3, 0x7FFFFFFF).h(0x3301), 6)),
                         {"raw:0x0003400"})
        self.assertEqual(self.specs(self.flag_rom(lambda a: a.ldr(3, 0x40000000).h(0x3301), 6)),
                         {"raw:0x0003000"})
        self.assertEqual(self.specs(self.flag_rom(lambda a: a.mov(3, 128).h(0x065B), 2)),
                         {"raw:0x0003400"})
        self.assertEqual(self.specs(self.flag_rom(lambda a: a.mov(3, 128).h(0x061B), 2)),
                         {"raw:0x0003000"})

        def multiply(a):
            a.mov(3, 1).h(0x3B01).mov(3, 0).mov(4, 0).h(0x4363)
        self.assertEqual(self.specs(self.flag_rom(multiply, 2)),
                         {"raw:0x0003000", "raw:0x0003400"})
        self.assertEqual(self.specs(self.flag_rom(lambda a: a.mov(3, 1).h(0x3B01), 2)),
                         {"raw:0x0003400"})

    def test_deferred_dma3_never_proves_an_immediate_copy(self):
        def dma3(a, source, destination, control):
            a.ldr(0, 0x040000D4).ldr(1, source).h(0x6001)
            a.ldr(1, destination).h(0x6041).ldr(1, control).h(0x6081)

        def transfer_rom(control):
            rom = probe_rom()
            rom.write(0x1000, lz77_compress(probe_sheet(43)))
            rom.write(0x3000, probe_palette(43))
            rom.write(0x3400, probe_palette(44))
            a = ProbeAsm(rom, 0x600, 0x780)
            a.ldr(0, 0x08003000).ldr(1, 0x02000000).mov(2, 16).bl(CPUSET)
            dma3(a, 0x02000000, 0x05000000, control)
            a.ldr(0, 0x08003400).ldr(1, 0x02000000).mov(2, 16).bl(CPUSET)
            a.ldr(0, 0x08001000).ldr(1, 0x06000000).bl(LZV).h(0x4770).done()
            return rom

        for control in (0x94000008, 0xA2000008, 0xA6000008, 0x8A000008):
            self.assertFalse(self.specs(transfer_rom(control)), hex(control))
            with self.assertRaisesRegex(ValueError, "palette"):
                self.materialize(transfer_rom(control))
        self.assertEqual(self.specs(transfer_rom(0x84000008)), {"raw:0x0003000"})
        self.assertIn("raw:0x0003000", self.materialize(transfer_rom(0x84000008)))

    def test_zero_length_copies_and_positive_transfer_controls(self):
        rom = probe_rom()
        rom.write(0x1000, probe_sheet(45))

        def event(swi, control):
            return cp.ConsumerEvent(swi, 0, (), cp.Value.known(0x08001000),
                                    cp.Value.known(0x06000000), cp.Value.known(control))

        for swi, control in ((0x0B, 0), (0x0C, 0), (cp.DMA3_COPY, 0x84000000)):
            self.assertFalse(cp.graphic_proofs(rom, cp.ProofPath((event(swi, control),))), (swi, control))
        for swi, control, length in ((0x0B, 48, 96), (0x0C, 1, 32), (cp.DMA3_COPY, 0x84000008, 32)):
            proofs = cp.graphic_proofs(rom, cp.ProofPath((event(swi, control),)))
            self.assertEqual([proof.length for proof in proofs], [length])

    def bgcnt_rom(self, value=0x1F00, after=None) -> Rom:
        rom = probe_rom()
        rom.write(0x1000, lz77_compress(probe_sheet(46)))
        rom.write(0x2000, lz77_compress(struct.pack("<640H", *(i % 3 for i in range(640)))))
        rom.write(0x3000, probe_palette(46))
        if after == "dropped":
            f = ProbeAsm(rom, 0x9300, 0x9380)
            f.ldr(3, 0x04000008).mov(4, 0).h(0x801C, 0x2D00)
            for index in range(5):
                f.b(f"g{index}", EQ).label(f"g{index}")
            f.h(0x4770).done()
        a = ProbeAsm(rom, 0x600, 0x780)
        a.ldr(0, 0x08003000).ldr(1, 0x05000000).mov(2, 16).bl(CPUSET)
        a.ldr(0, 0x08001000).ldr(1, 0x06000000).bl(LZV)
        a.ldr(0, 0x08002000).ldr(1, 0x0600F800).bl(LZV)
        a.ldr(3, 0x04000008).ldr(4, value).h(0x801C)
        if after == "dropped":
            a.bl(0x9300)
        elif after == "symbolic":
            a.h(0x8035)
        a.h(0x4770).done()
        return rom

    def bgcnt_maps(self, rom) -> list[dict]:
        return [m for m in probe_candidate(gfx.scan(rom), 0x2000).tilemaps
                if m["sheet_offset"] == 0x1000]

    def test_bgcnt_bpp_must_match_tile_sheet(self):
        positive = self.bgcnt_maps(self.bgcnt_rom())
        self.assertTrue(any(m["bpp"] == 4 and m.get("tier") == "consumer" for m in positive))
        for value in (0x1F80, 0x1E00):
            maps = self.bgcnt_maps(self.bgcnt_rom(value))
            self.assertTrue(maps)
            self.assertFalse(any(m.get("tier") in gfx.ROM_EVIDENCE_TIERS for m in maps), hex(value))

    def test_bgcnt_later_unknown_writes_invalidate_configuration(self):
        for after in ("dropped", "symbolic"):
            maps = self.bgcnt_maps(self.bgcnt_rom(after=after))
            self.assertTrue(maps)
            self.assertFalse(any(m.get("tier") in gfx.ROM_EVIDENCE_TIERS for m in maps), after)
        self.assertTrue(any(m.get("tier") == "consumer" for m in self.bgcnt_maps(self.bgcnt_rom())))

    def test_bios_copy_source_alignment_controls(self):
        cases = ((CPUSET, 0x3002, 0x04000008, set()),
                 (CPUSET, 0x3001, 16, set()),
                 (FASTSET, 0x3002, 8, set()),
                 (CPUSET, 0x3000, 0x04000008, {"raw:0x0003000"}),
                 (CPUSET, 0x3000, 16, {"raw:0x0003000"}),
                 (FASTSET, 0x3000, 8, {"raw:0x0003000"}))
        for stub, source, control, expected in cases:
            rom = probe_rom()
            rom.write(0x3000, probe_palette(47) + bytes(4))
            a = ProbeAsm(rom, 0x600, 0x700)
            a.ldr(0, 0x08000000 + source).ldr(1, 0x05000000).ldr(2, control).bl(stub).h(0x4770).done()
            self.assertEqual(self.specs(rom), expected, (stub, source, control))

    def test_global_dma_staging_remains_candidate_only(self):
        def staging_rom(with_dma=False, local=False):
            rom = probe_rom()
            rom.write(0x1000, lz77_compress(probe_sheet(48)))
            rom.write(0x2000, lz77_compress(struct.pack("<640H", *(i % 3 for i in range(640)))))
            rom.write(0x3000, probe_palette(48))
            a = ProbeAsm(rom, 0x600, 0x780)
            a.ldr(0, 0x08003000).ldr(1, 0x02000000).mov(2, 16).bl(CPUSET)
            if local:
                a.ldr(0, 0x02000000).ldr(1, 0x05000000).mov(2, 16).bl(CPUSET)
            a.ldr(0, 0x08001000).ldr(1, 0x06000000).bl(LZV)
            a.ldr(0, 0x08002000).ldr(1, 0x0600F800).bl(LZV).h(0x4770).done()
            if with_dma:
                d = ProbeAsm(rom, 0x5000, 0x5040)
                d.h(0xE7FE).ldr(1, 0x02000000).ldr(0, 0x040000D4).h(0x6001)
                d.ldr(1, 0x05000000).h(0x6041).ldr(1, 0x84000008).h(0x6081, 0x4770).done()
            return rom

        negative = gfx.scan(staging_rom())
        self.assertFalse([p for p in negative["palettes"]
                          if p["evidence"].get("method") == "staging-dma-candidate"])
        self.assertFalse(probe_candidate(negative, 0x2000).tilemaps)

        result = gfx.scan(staging_rom(with_dma=True))
        staged = [p for p in result["palettes"]
                  if p["evidence"].get("method") == "staging-dma-candidate"]
        self.assertTrue(staged)
        self.assertTrue(all(p["tier"] == "candidate" for p in staged))
        self.assertIsNone(probe_candidate(result, 0x1000).palette_for(4))
        maps = probe_candidate(result, 0x2000).tilemaps
        self.assertTrue(maps)
        self.assertTrue(all(m["tier"] == "candidate" for m in maps))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc = gfx.write(result, rom=staging_rom(with_dma=True), directory=root / "index", render=False)
            path = root / "graphics.toml"
            path.write_text('version = "2.0.0"\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "ROM-evidenced palette"):
                gfx.write_flags(path, [{"id": "g0001000", "category": "translate", "reason": "candidate"}],
                                doc, staging_rom(with_dma=True))
            self.assertEqual(path.read_text(encoding="utf-8"), 'version = "2.0.0"\n')

        control = staging_rom(local=True)
        self.assertEqual(self.specs(control), {"raw:0x0003000"})
        self.assertIn("raw:0x0003000", self.materialize(control))

    def test_staged_subrange_origins_and_controls(self):
        for source_offset, expected, rejected in ((0, "raw:0x0003000", "raw:0x0003020"),
                                                  (0x20, "raw:0x0003020", "raw:0x0003000")):
            rom = probe_rom()
            rom.write(0x3000, probe_palette(12) + probe_palette(13))
            a = ProbeAsm(rom, 0x600, 0x700)
            a.ldr(0, 0x08003000).ldr(1, 0x02000000).mov(2, 32).bl(CPUSET)
            a.ldr(0, 0x02000000 + source_offset).ldr(1, 0x05000000).mov(2, 16).bl(CPUSET).h(0x4770).done()
            specs = self.specs(rom)
            self.assertIn(expected, specs)
            self.assertNotIn(rejected, specs)
        rom = probe_rom()
        rom.write(0x3000, lz77_compress(probe_palette(14) + probe_palette(15)))
        wrapper = ProbeAsm(rom, 0x9500, 0x9540)
        wrapper.h(0xB500).ldr(1, 0x02000000).bl(LZV)
        wrapper.ldr(0, 0x02000020).ldr(1, 0x05000000).mov(2, 16).bl(CPUSET).h(0xBD00).done()
        a = ProbeAsm(rom, 0x600, 0x700)
        a.ldr(0, 0x08003000).bl(0x9500).h(0x4770).done()
        self.assertEqual(self.specs(rom), {"lz:0x0003000+0x20"})

    def test_loop_exit_constant_branch_and_program_counter(self):
        rom = probe_rom()
        for index, at in enumerate((0x3000, 0x3020, 0x3040)):
            rom.write(at, probe_palette(5 + index))
        a = ProbeAsm(rom, 0x600, 0x700)
        a.ldr(4, 0x08003000).mov(5, 2).b("check")
        a.label("body").h(0x3420, 0x3D01).label("check").h(0x2D00).b("body", NE)
        a.h(0x1C20).ldr(1, 0x05000000).mov(2, 16).bl(CPUSET).h(0x4770).done()
        self.assertEqual(self.specs(rom), {"raw:0x0003040"})

        rom = probe_rom()
        rom.write(0x3000, probe_palette(8))
        a = ProbeAsm(rom, 0x600, 0x700)
        a.mov(3, 1).h(0x2B00).b("copy", EQ).b("end")
        a.label("copy").ldr(0, 0x08003000).ldr(1, 0x05000000).mov(2, 16).bl(CPUSET)
        a.label("end").h(0x4770).done()
        self.assertFalse(self.specs(rom))

        rom = probe_rom()
        start = cp.consumer_start(rom, 0x600, 0x80)
        stale = 0x08000000 + start + 4
        a = ProbeAsm(rom, 0x600, 0x700)
        a.ldr(5, stale).h(0x4678).ldr(1, 0x05000000).mov(2, 16).bl(CPUSET).h(0x4770).done()
        paths = cp.consumer_paths(rom, start, 0x700)
        sources = {e.source.constant for path in paths for e in path.events}
        self.assertIn(0x08000606, sources)
        self.assertNotIn(stale, sources)

    def test_long_constant_loop_widens_without_stale_origin(self):
        rom = probe_rom()
        rom.write(0x3000, probe_palette(5))
        rom.write(0x3280, probe_palette(25))
        a = ProbeAsm(rom, 0x600, 0x700)
        a.ldr(4, 0x08003000).mov(5, 20).b("check")
        a.label("body").h(0x3420, 0x3D01).label("check").h(0x2D00).b("body", NE)
        a.h(0x1C20).ldr(1, 0x05000000).mov(2, 16).bl(CPUSET).h(0x4770).done()
        paths = cp.consumer_paths(rom, 0x600, 0x700)
        self.assertLessEqual(len(paths), 16)
        self.assertNotIn("raw:0x0003000", self.specs(rom))

    def test_staging_clobbers_and_surviving_control(self):
        def staged(rom, a, clobber):
            rom.write(0x3000, probe_palette(9))
            a.ldr(0, 0x08003000).ldr(1, 0x02000000).mov(2, 16).bl(CPUSET)
            clobber(rom, a)
            a.ldr(0, 0x02000000).ldr(1, 0x05000000).mov(2, 16).bl(CPUSET).h(0x4770).done()

        for name, clobber in (("store", lambda rom, a: a.ldr(6, 0x02000000).h(0x6037)),
                              ("dropped-call", lambda rom, a: a.bl(0x9300))):
            rom = probe_rom()
            if name == "dropped-call":
                rom.write(0x3400, probe_palette(10))
                f = ProbeAsm(rom, 0x9300, 0x9380)
                f.h(0xB500).ldr(0, 0x08003400).ldr(1, 0x02000000).mov(2, 16).bl(CPUSET)
                for index in range(5):
                    f.b(f"g{index}", EQ).label(f"g{index}")
                f.h(0xBD00).done()
            a = ProbeAsm(rom, 0x600, 0x700)
            staged(rom, a, clobber)
            self.assertNotIn("raw:0x0003000", self.specs(rom), name)
        rom = probe_rom()
        a = ProbeAsm(rom, 0x600, 0x700)
        staged(rom, a, lambda rom, a: None)
        self.assertIn("raw:0x0003000", self.specs(rom))

    def test_transfer_modes_lengths_and_local_dma(self):
        def event(control):
            return cp.ConsumerEvent(cp.DMA3_COPY, 0, (), cp.Value.known(0x02000000),
                                    cp.Value.known(0x05000000), cp.Value.known(control))

        for control, expected in ((0x84000008, 32), (0x84000100, 1024), (0x80000400, 2048),
                                  (0x80000000, 0x20000)):
            self.assertEqual(cp._transfer_length(event(control)), expected)
        fast = cp.ConsumerEvent(0x0C, 0, (), cp.Value.known(0x08003000),
                                cp.Value.known(0x06000000), cp.Value.known(1))
        self.assertEqual(cp.event_length(probe_rom(), fast), 32)
        for stub, control in ((CPUSET, 0x01000010), (FASTSET, 0x01000008)):
            rom = probe_rom()
            rom.write(0x3000, probe_palette(19))
            a = ProbeAsm(rom, 0x600, 0x700)
            a.ldr(0, 0x08003000).ldr(1, 0x05000000).ldr(2, control).bl(stub).h(0x4770).done()
            self.assertFalse(self.specs(rom))
        fill = cp.ConsumerEvent(0x0C, 0, (), cp.Value.known(0x08003000),
                                cp.Value.known(0x06000000), cp.Value.known(0x01000010))
        copy = cp.ConsumerEvent(0x0C, 0, (), cp.Value.known(0x08003000),
                                cp.Value.known(0x06000000), cp.Value.known(0x00000010))
        rom = probe_rom()
        rom.write(0x3000, bytes(range(1, 65)))
        self.assertFalse(cp.graphic_proofs(rom, cp.ProofPath((fill,))))
        self.assertTrue(cp.graphic_proofs(rom, cp.ProofPath((copy,))))

        for control in (0x81000010, 0x80200010):
            rom = probe_rom()
            rom.write(0x3000, probe_palette(17))
            a = ProbeAsm(rom, 0x600, 0x700)
            a.ldr(0, 0x08003000).ldr(1, 0x020001A0).mov(2, 16).bl(CPUSET).h(0x4770).done()
            d = ProbeAsm(rom, 0x5000, 0x5040)
            d.h(0xE7FE).ldr(1, 0x02000000).ldr(0, 0x040000D4).h(0x6001)
            d.ldr(1, 0x05000000).h(0x6041).ldr(1, control).h(0x6081, 0x4770).done()
            self.assertFalse(self.specs(rom))

        rom = probe_rom()
        rom.write(0x3000, probe_palette(16))
        a = ProbeAsm(rom, 0x600, 0x700)
        a.ldr(0, 0x08003000).ldr(1, 0x020001A0).mov(2, 16).bl(CPUSET)
        a.ldr(1, 0x02000000).ldr(0, 0x040000D4).h(0x6001)
        a.ldr(1, 0x05000000).h(0x6041).ldr(1, 0x80000200).h(0x6081, 0x4770).done()
        records = gfx.palette_loads(rom, gfx.literal_loads(rom))
        record = next(p for p in records if p["spec"] == "raw:0x0003000")
        self.assertEqual((record["bank"], record["evidence"]["destination"]), (13, 0x050001A0))
        self.assertEqual(record["evidence"]["destination_kind"], "staging:local+bg")

    def test_unaligned_palette_banks_reject(self):
        self.assertIsNone(cp._hardware_palette(cp.Value.known(0x05000010), 32))
        self.assertIsNone(cp._hardware_palette(cp.Value.known(0x05000210), 32))
        self.assertEqual(cp._hardware_palette(cp.Value.known(0x05000020), 32), (1, "bg", 0x05000020))

    def test_region_roles_and_cross_window_banks(self):
        rom = probe_rom()
        rom.write(0x1000, lz77_compress(probe_sheet(2)))
        rom.write(0x3000, probe_palette(20))
        a = ProbeAsm(rom, 0x600, 0x700)
        a.ldr(0, 0x08003000).ldr(1, 0x05000200).mov(2, 16).bl(CPUSET)
        a.ldr(0, 0x08001000).ldr(1, 0x06000000).bl(LZV).h(0x4770).done()
        result = gfx.scan(rom)
        self.assertFalse(self.trusted(result, 0x1000))

        rom = probe_rom()
        rom.write(0x1000, lz77_compress(probe_sheet(3)))
        rom.write(0x1400, lz77_compress(probe_sheet(4)))
        rom.write(0x2000, lz77_compress(struct.pack("<640H", *(i % 3 for i in range(640)))))
        rom.write(0x3000, probe_palette(21))
        a = ProbeAsm(rom, 0x600, 0x780)
        a.ldr(0, 0x08003000).ldr(1, 0x05000000).mov(2, 16).bl(CPUSET)
        a.ldr(0, 0x08001000).ldr(1, 0x06010000).bl(LZV)
        a.ldr(0, 0x08001400).ldr(1, 0x06000000).bl(LZV)
        a.ldr(0, 0x08002000).ldr(1, 0x0600F800).bl(LZV).h(0x4770).done()
        result = gfx.scan(rom)
        self.assertFalse(self.trusted(result, 0x1000))
        tilemap = probe_candidate(result, 0x2000)
        self.assertFalse(any(m["sheet_offset"] == 0x1000 and m.get("tier") in gfx.ROM_EVIDENCE_TIERS
                             for m in tilemap.tilemaps))

        rom = probe_rom()
        rom.write(0x1000, lz77_compress(probe_sheet(5)))
        rom.write(0x3000, probe_palette(22))
        rom.write(0x9400, words(0x0049, 0x4B0F, 0x18C9) + bl_bytes(0x9406, CPUSET) + words(0x4770))
        rom.write(0x9440, (0x05000000).to_bytes(4, "little"))
        a = ProbeAsm(rom, 0x600, 0x780)
        a.ldr(0, 0x08001000).ldr(1, 0x06000000).bl(LZV)
        a.ldr(0, 0x08003000).mov(1, 0).mov(2, 16).b("tail").h(0x4770)
        a.label("f2").ldr(0, 0x08003000).mov(1, 0xD0).mov(2, 16)
        a.label("tail").bl(0x9400).h(0x4770).done()
        result = gfx.scan(rom)
        self.assertEqual({p["bank"] for p in self.trusted(result, 0x1000)}, {0})

    def descriptor_rom(self, obj_palette: bool = False) -> Rom:
        rom = probe_rom()
        rom.write(0x3000, lz77_compress(probe_sheet(6)))
        entries = [(i % 2) | ((i % 2) << 10) | ((i % 3 == 2) << 11) for i in range(640)]
        rom.write(0x4000, lz77_compress(struct.pack("<640H", *entries)))
        rom.write(0x5000, probe_palette(23))
        rom.write(0x2000, ptr_bytes(0x3000) + ptr_bytes(0x4000) + ptr_bytes(0x5000))
        a = ProbeAsm(rom, 0x600, 0x700)
        a.ldr(4, 0x08002000).h(0x6820).ldr(1, 0x06000000).bl(LZV)
        a.h(0x6860).ldr(1, 0x06008000).bl(LZV).h(0x68A0)
        a.ldr(1, 0x05000200 if obj_palette else 0x05000000).mov(2, 16).bl(CPUSET).h(0x4770).done()
        return rom

    def test_descriptor_regions_and_materialization(self):
        result = gfx.scan(self.descriptor_rom())
        self.assertTrue(self.trusted(result, 0x3000))
        result = gfx.scan(self.descriptor_rom(True))
        self.assertFalse(self.trusted(result, 0x3000))
        with self.assertRaisesRegex(ValueError, "palette"):
            self.materialize(self.descriptor_rom(True), 0x3000)
        self.assertIn("raw:0x0005000", self.materialize(self.descriptor_rom(), 0x3000))

    def test_staged_slice_materialization_and_wrong_mode_rejection(self):
        def staged(rom, a):
            rom.write(0x3000, probe_palette(12) + probe_palette(13))
            a.ldr(0, 0x08003000).ldr(1, 0x02000000).mov(2, 32).bl(CPUSET)
            a.ldr(0, 0x02000020).ldr(1, 0x05000000).mov(2, 16).bl(CPUSET)

        self.assertIn("raw:0x0003020", self.materialize(self.with_sheet(staged)))

        def fill(rom, a):
            rom.write(0x3000, probe_palette(19))
            a.ldr(0, 0x08003000).ldr(1, 0x05000000).ldr(2, 0x01000010).bl(CPUSET)

        with self.assertRaisesRegex(ValueError, "palette"):
            self.materialize(self.with_sheet(fill))
