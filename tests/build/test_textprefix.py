import copy
import struct
import unittest
from pathlib import Path

from gen3tl.build import textprefix
from gen3tl.build.inject import inject, verify
from gen3tl.core.rom import Rom
from gen3tl.core.notes import BoundProse, has_prose
from gen3tl.profile.profile import Profile
from gen3tl.web.editorsnapshot import configuration
from project_fixture import rom_evidence


class TextPrefixTest(unittest.TestCase):
    def setUp(self):
        self.raw = b'\xa1\xa2\xa3\xa4\xa5\xff'
        self.fact = dict(name='number', pointer=16, source=rom_evidence(64, self.raw),
                         evidence='A dedicated pointer appends this prefix.',
                         limits=dict(table='items', where='list', original_width=71,
                                     original_chars=12, width=82, chars=17))
        self.data = dict(text_prefixes=[self.fact],
                         table=[dict(name='items', windows=[dict(where='list', indices=[1, 2],
                                                               width=71, chars=12, evidence='Measured.')])],
                         layout=dict(window_width={'items:1': 71, 'items:2': 71}))
        raw = bytearray(512)
        struct.pack_into('<I', raw, 16, 0x08000040)
        struct.pack_into('<I', raw, 20, 0x08000040)
        raw[64:70] = self.raw
        self.base = Rom(raw)

    def test_default_and_language_copies_keep_original_geometry(self):
        other = copy.deepcopy(self.data)
        textprefix.configure(other, [])
        self.assertEqual(other['selected_text_prefixes'], [])
        self.assertEqual(other['layout'], self.data['layout'])
        chosen = copy.deepcopy(self.data)
        textprefix.configure(chosen, ['number'])
        self.assertEqual(chosen['layout']['window_width']['items:1'], 82)
        self.assertEqual(chosen['table'][0]['windows'][0]['chars'], 17)
        self.assertEqual(self.data['layout']['window_width']['items:1'], 71)
        profile = Profile(chosen, Path('test.toml'))
        restored = textprefix.original_limits(profile)
        self.assertEqual(restored['layout']['window_width']['items:1'], 71)
        self.assertEqual(restored['selected_text_prefixes'], [])
        self.assertEqual(profile['layout']['window_width']['items:1'], 82)
        old = configuration(profile)
        profile['selected_text_prefixes'][0]['pointer'] = 24
        self.assertNotEqual(configuration(profile), old)

    def test_joined_evidence_preserves_bindings_and_omits_empty_parts(self):
        from project_fixture import with_profile_notes

        for window_text in ('', 'Measured.\nVerified window.'):
            for prefix_bound in (False, True):
                with self.subTest(window=window_text, prefix_bound=prefix_bound):
                    data = copy.deepcopy(self.data)
                    window = data['table'][0]['windows'][0]
                    window['evidence'] = window_text
                    if window_text:
                        data['table'][0]['windows'][0] = with_profile_notes(window)
                    if prefix_bound:
                        data['text_prefixes'][0] = with_profile_notes(data['text_prefixes'][0])
                    textprefix.configure(data, ['number'])
                    evidence = data['table'][0]['windows'][0]['evidence']
                    self.assertEqual(evidence, ' '.join(filter(None, [window_text, self.fact['evidence']])))
                    if window_text or prefix_bound:
                        self.assertIsInstance(evidence, BoundProse)
                        self.assertTrue(has_prose(evidence, single_line=True))

    def test_empty_unbound_prefix_evidence_is_refused(self):
        for value in ('', BoundProse('')):
            with self.subTest(value=value):
                self.fact['evidence'] = value
                with self.assertRaisesRegex(ValueError, 'missing evidence'):
                    textprefix.configure(self.data, ['number'])

    def test_only_dedicated_pointer_changes_and_guard_detects_tampering(self):
        changed = self.base.copy()
        result = inject(changed, self.base, {'entries': []}, {}, {}, text_prefixes=[self.fact])
        self.assertEqual(verify(changed, self.base, result, {}, set()), [])
        self.assertEqual(changed.u32(16), 0x08000045)
        self.assertEqual(changed.u32(20), self.base.u32(20))
        self.assertEqual(changed.read(64, 6), self.raw)
        self.assertEqual(result.allowed, set(range(16, 20)))
        changed.write(16, (0x08000044).to_bytes(4, 'little'))
        self.assertTrue(any('text prefix' in s for s in verify(changed, self.base, result, {}, set())))

    def test_invalid_source_pointer_and_overlap_are_rejected_atomically(self):
        for mutation in ('source', 'pointer', 'overlap', 'unaligned'):
            with self.subTest(mutation=mutation):
                changed = self.base.copy()
                fact = copy.deepcopy(self.fact)
                occupied = set()
                if mutation == 'source':
                    changed.write(64, b'\x00')
                elif mutation == 'pointer':
                    changed.write(16, b'\x00' * 4)
                elif mutation == 'overlap':
                    occupied.add(17)
                else:
                    fact['pointer'] = 17
                before = bytes(changed.data)
                with self.assertRaises(ValueError):
                    textprefix.apply(self.base, changed, [fact], occupied)
                self.assertEqual(bytes(changed.data), before)

    def test_stale_geometry_and_unknown_selection_are_rejected(self):
        for mode in ('unknown', 'duplicate', 'width', 'entry'):
            with self.subTest(mode=mode):
                data = copy.deepcopy(self.data)
                selected = ['number']
                if mode == 'unknown': selected = ['unknown']
                elif mode == 'duplicate': selected *= 2
                elif mode == 'width': data['table'][0]['windows'][0]['width'] = 72
                else: data['layout']['window_width']['items:2'] = 72
                with self.assertRaises(ValueError):
                    textprefix.configure(data, selected)

    def test_malformed_source_evidence_is_rejected(self):
        for source in (64, {}, rom_evidence(64, b'\xff'),
                       {**self.fact['source'], 'size': 0},
                       {**self.fact['source'], 'offset': -1},
                       {**self.fact['source'], 'sha256': 'invalid'}):
            with self.subTest(source=source):
                data = copy.deepcopy(self.data)
                data['text_prefixes'][0]['source'] = source
                with self.assertRaises(ValueError):
                    textprefix.configure(data, ['number'])

    def test_changed_base_source_and_mismatched_evidence_are_rejected(self):
        for mutation in ('base', 'digest', 'size', 'offset'):
            with self.subTest(mutation=mutation):
                base, changed = self.base.copy(), self.base.copy()
                fact = copy.deepcopy(self.fact)
                if mutation == 'base':
                    base.write(64, b'\x00')
                elif mutation == 'digest':
                    fact['source']['sha256'] = '0' * 64
                elif mutation == 'size':
                    fact['source']['size'] += 1
                else:
                    fact['source']['offset'] += 1
                before = bytes(changed.data)
                with self.assertRaises(ValueError):
                    textprefix.apply(base, changed, [fact], set())
                self.assertEqual(bytes(changed.data), before)

    def test_matching_evidence_still_requires_a_terminated_prefix(self):
        for raw in (b'\xff', b'\xa1\xa2', b'\xa1\xff\xa2\xff'):
            with self.subTest(raw=raw):
                base = self.base.copy()
                base.write(64, raw)
                changed = base.copy()
                fact = {**self.fact, 'source': rom_evidence(64, raw)}
                with self.assertRaisesRegex(ValueError, 'invalid terminated prefix'):
                    textprefix.apply(base, changed, [fact], set())
                self.assertEqual(changed.data, base.data)
