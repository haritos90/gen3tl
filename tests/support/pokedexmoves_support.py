BASE = 0x100

DESCRIPTIONS = 0x200

def move_block() -> dict:
    return {
        "base": BASE, "stride": 12, "count": 4, "evidence": "synthetic x12 accessor",
        "fields": {
            "effect": 0, "power": 1, "type": 2, "accuracy": 3, "pp": 4,
            "secondary_chance": 5, "target": 6,
            "priority": {"offset": 7, "signed": True},
            "flags": 8, "z_move_power": 9, "damage_category": 10,
        },
        "descriptions": {"base": DESCRIPTIONS, "stride": 4, "count": 2,
                         "evidence": "synthetic description pointers"},
        "targets": {"0": "selected", "evidence": "synthetic target check"},
        "effects": {"0": "hit", "7": "explosion", "43": "high_critical",
                    "evidence": "synthetic effect dispatch"},
        "flag_bits": {"0": "makes_contact", "1": "protect_affected", "2": "magic_coat_affected",
                      "4": "mirror_move_affected", "5": "kings_rock_affected",
                      "evidence": "synthetic flag checks"},
        "lists": [
            {"key": "punching", "base": 0x380, "evidence": "synthetic list reader"},
            {"key": "sound", "base": 0x390, "evidence": "synthetic list reader"},
        ],
        "readers": [
            {"key": "makes_contact", "abilities": [217, 100], "items": [578],
             "evidence": "synthetic contact guards"},
            {"key": "punching", "abilities": [93], "evidence": "synthetic Iron Fist check"},
            {"key": "sound", "abilities": [43, 218], "items": [709],
             "evidence": "synthetic sound checks"},
        ],
    }

def rom_bytes() -> bytes:
    data = bytearray(0x400)
    rows = [
        [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
        [0, 40, 0, 100, 35, 0, 0, 0, 0x13, 0x64, 0, 0],
        [43, 50, 1, 100, 25, 0, 0, 0, 0x13, 0x64, 0, 0],
        [7, 60, 14, 90, 10, 20, 0, 0xFA, 0x34, 0x12, 2, 0],
    ]
    for move, row in enumerate(rows):
        data[BASE + move * 12:BASE + (move + 1) * 12] = bytes(row)
    data[DESCRIPTIONS:DESCRIPTIONS + 4] = (0x08000300).to_bytes(4, "little")
    data[DESCRIPTIONS + 4:DESCRIPTIONS + 8] = (0x08000340).to_bytes(4, "little")
    data[DESCRIPTIONS + 8:DESCRIPTIONS + 12] = (0x08000380).to_bytes(4, "little")
    data[0x380:0x386] = (1).to_bytes(2, "little") + (2).to_bytes(2, "little") + (0xFEFE).to_bytes(2, "little")
    data[0x390:0x396] = (2).to_bytes(2, "little") + (3).to_bytes(2, "little") + (0xFEFE).to_bytes(2, "little")
    return bytes(data)
