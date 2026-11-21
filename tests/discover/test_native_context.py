import struct

from gen3tl.core.rom import Rom,ROM_BASE
from gen3tl.core.charmap import Charmap
from project_fixture import rom_evidence

def fixture(mode='guard'):
    data=bytearray(b'\xff'*0x800)
    def half(at,*words):
        data[at:at+2*len(words)]=struct.pack('<'+'H'*len(words),*words)
    def word(at,val): data[at:at+4]=struct.pack('<I',val)
    def load(at,rd,pool): half(at,0x4800|(rd<<8)|((pool-((at+4)&~3))//4))
    def call(at,target):
        delta=target-at-4
        half(at,0xf000|((delta>>12)&0x7ff),0xf800|((delta>>1)&0x7ff))
    start=0x100
    if mode=='boolean':
        half(start,0x1e59,0x418b)
        source=start+4
    else:
        half(start,0x2b02)
        source=start+2
    load(source,2,0x180)
    half(source+2,0x009b)
    half(source+4,0x5899)
    load(source+6,0,0x184)
    call(source+8,0x300)
    half(source+12,0x4770)
    load(0x140,1,0x188)
    word(0x180,ROM_BASE+0x400)
    word(0x184,0x02000100)
    word(0x188,ROM_BASE+0x580)
    half(0x300,0x4770)
    for i in range(4):
        word(0x400+i*4,ROM_BASE+0x500+i*16)
        encoded=Charmap().encode('Word'+str(i))
        data[0x500+i*16:0x500+i*16+len(encoded)]=encoded
    data[0x580:0x589]=Charmap().encode('Fallback')
    rule={'name':'choices','handler':'handler','analysis':[0,source-start+12],
          'read':source-start+4,'copy':source-start+8,'domain':mode,'bound':0}
    if mode=='boolean': rule['boolean']=0
    return Rom(data),rule,{'handler':start,'copy':0x300},{0x02000100:0}

import copy
import unittest

from gen3tl.discover import native_context as native
from gen3tl.families.fingerprints import Fingerprint


class NativeContextTest(unittest.TestCase):
    def test_guard_count_and_excluded_pointer_tail(self):
        rom, rule, matches, slots = fixture()
        rule['storage'] = 'pointer-run'
        value, sites = native.derive(rom, rule, matches, slots)
        self.assertEqual((value['base'], value['count'], value['exclude']), (0x400, 4, [3]))
        self.assertEqual(value['slot'], 0)
        self.assertIn(0x106, sites)
        rom.write(0x100, struct.pack('<H', 0x2302))
        with self.assertRaises(ValueError):
            native.derive(rom, rule, matches, slots)

    def test_loop_count_and_fallback(self):
        rom, rule, matches, slots = fixture('loop')
        rule['fallback'] = 0x40
        value, _ = native.derive(rom, rule, matches, slots)
        self.assertEqual(value['count'], 2)
        self.assertEqual(value['variants'], [['s0000580']])
        rom.write(0x188, struct.pack('<I', 0))
        with self.assertRaisesRegex(ValueError, 'ROM pointer'):
            native.derive(rom, rule, matches, slots)

    def test_boolean_carry_selector(self):
        rom, rule, matches, slots = fixture('boolean')
        value, _ = native.derive(rom, rule, matches, slots)
        self.assertEqual(value['count'], 2)
        rom.write(0x102, struct.pack('<H', 0x1a5b))
        with self.assertRaises(ValueError):
            native.derive(rom, rule, matches, slots)

    def test_wrong_copy_destination_and_source_are_rejected(self):
        for change in ('callee', 'slot', 'source'):
            rom, rule, matches, slots = fixture()
            if change == 'callee':
                matches['copy'] = 0x302
            elif change == 'slot':
                slots = {0x02000200: 0}
            else:
                rom.write(0x400, struct.pack('<I', 0))
            with self.subTest(change=change), self.assertRaises(ValueError):
                native.derive(rom, rule, matches, slots)

    def test_wrapper_call_must_reach_the_fill(self):
        rom, rule, matches, slots = fixture()
        rule.update(wrapper='wrapper', wrapper_call=0)
        matches['wrapper'] = 0x200
        delta = 0x100 - 0x200 - 4
        rom.write(0x200, struct.pack('<HH', 0xf000 | ((delta >> 12) & 0x7ff),
                                    0xf800 | ((delta >> 1) & 0x7ff)))
        value, _ = native.derive(rom, rule, matches, slots)
        self.assertEqual(value['address'], 0x201)
        rom.write(0x200, struct.pack('<HH', 0xf000, 0xf800))
        with self.assertRaisesRegex(ValueError, 'wrapper'):
            native.derive(rom, rule, matches, slots)

    def test_branch_edges_are_checked_after_fingerprinting(self):
        rom = Rom(bytes.fromhex('002801d00120704702207047') + bytes(20))
        signature = Fingerprint.bootstrap(rom.data, 0, 12, 'branch', 'context').document()
        signature['branches'] = [[2, 8]]
        knowledge = {'functions': [signature]}
        self.assertEqual(native.matched_functions(rom, knowledge), {'branch': 0})
        changed = copy.deepcopy(knowledge)
        changed['functions'][0]['branches'] = [[2, 6]]
        self.assertEqual(native.matched_functions(rom, changed), {})

    def test_buffers_follow_the_copy_destination(self):
        rom, _, _, _ = fixture()
        rom.write(0x200, struct.pack('<HHHHHH', 0x4807, 0x00a4, 0x1824, 0x6820, 0xf000, 0xf87a))
        rom.write(0x220, struct.pack('<I', ROM_BASE + 0x600))
        rom.write(0x600, struct.pack('<III', 0x02000100, 0x02000120, 0x02000140))
        rom.write(0x700, struct.pack('<I', ROM_BASE + 0x201))
        knowledge = {'buffers': {'handler': 'buffer', 'opcode': 0, 'read': 6, 'copy': 8, 'slots': 3}}
        profile = {'script': {'command_table': 0x700}}
        matches = {'buffer': 0x200, 'copy': 0x300}
        slots, _ = native.buffers(rom, profile, knowledge, matches)
        self.assertEqual(slots, {0x02000100: 0, 0x02000120: 1, 0x02000140: 2})
        rom.write(0x604, struct.pack('<I', 0x02000100))
        with self.assertRaisesRegex(ValueError, 'distinct'):
            native.buffers(rom, profile, knowledge, matches)

    def test_special_dispatch_bounds_and_handler_identity(self):
        rom, _, _, _ = fixture()
        rom.write(0x200, struct.pack('<HH', 0x4907, 0x4808))
        rom.write(0x220, struct.pack('<II', ROM_BASE + 0x600, ROM_BASE + 0x610))
        rom.write(0x700, struct.pack('<I', ROM_BASE + 0x201))
        profile = {'script': {'command_table': 0x700}}
        knowledge = {'specials': {'handler': 'special', 'opcode': 0, 'base_load': 0, 'end_load': 2}}
        matches = {'special': 0x200}
        self.assertEqual(native.special_table(rom, profile, knowledge, matches), (0x600, 0x610))
        rom.write(0x224, struct.pack('<I', ROM_BASE + 0x5fc))
        self.assertIsNone(native.special_table(rom, profile, knowledge, matches))
        self.assertIsNone(native.special_table(rom, profile, knowledge, {}))

    def test_prefix_join_keeps_limits_unresolved(self):
        rom, _, _, _ = fixture()
        rom.write(0x200, struct.pack('<HHHHHHHHHH', 0x4c07, 0x4908, 0x1c20,
                                    0xf000, 0xf87b, 0x4907, 0x1c20, 0xf000, 0xf887, 0x4770))
        rom.write(0x220, struct.pack('<III', 0x02000100, ROM_BASE + 0x500, ROM_BASE + 0x580))
        knowledge = {'text_prefixes': [{'name': 'prefix', 'handler': 'formatter',
                     'copy': 6, 'append': 14, 'end': 20, 'shortfall': 'Presentation limits need a consumer proof.'}]}
        matches = {'formatter': 0x200, 'copy': 0x300, 'append': 0x320}
        result = native.prefixes(rom, knowledge, matches)
        self.assertEqual(result.data, {})
        candidate, unresolved = result.issues
        self.assertEqual((candidate.status, unresolved.status), ('candidate', 'unresolved'))
        self.assertEqual(candidate.value, {'name': 'prefix', 'pointer': 0x228,
                                          'source': rom_evidence(0x580, Charmap().encode('Fallback'))})
        self.assertIn('consumer proof', result.metadata['shortfalls'][0]['reason'])
        rom.write(0x20c, struct.pack('<H', 0x2000))
        rejected = native.prefixes(rom, knowledge, matches)
        self.assertEqual(rejected.metadata['prefixes'], [])
        self.assertEqual([item.status for item in rejected.issues], ['unresolved'])

    def test_semantic_shortfalls_never_become_discovered_values(self):
        class Inputs(dict):
            def get(self, key, default=None):
                if key in ('context', 'reach'):
                    raise AssertionError('manual declarations entered discovery')
                return super().get(key, default)
        knowledge = {'functions': [], 'buffers': {'handler': 'absent', 'opcode': 0},
                     'shortfalls': [{'id': 'context.speaker_colours',
                                    'reason': 'Semantic labels need independent evidence.'}]}
        result = native.discover(Rom(bytes(512)), Inputs(), knowledge)
        self.assertEqual((result.data, result.facts), ({}, []))
        self.assertTrue(all(item.status == 'unresolved' and item.tier is None for item in result.issues))
        self.assertEqual(result.issues[0].id, 'context.speaker_colours')
        self.assertIn('overlay-only', result.metadata['runtime_observations'])


if __name__ == '__main__':
    unittest.main()
