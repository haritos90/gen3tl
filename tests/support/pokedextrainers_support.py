import struct

BASE, PARTY = 0x100, 0x400

GROUPS, BANK, ENGINE, ENGINE_BANK = 0xA00, 0xA10, 0xA20, 0xA30

HEADERS, EVENTS, OBJECTS, SCRIPTS = (0xA40, 0xA60), (0xB40, 0xB80), (0xC00, 0xC40), 0xD00

XOR = 0x1234

def block() -> dict:
    return {
        "base": BASE, "stride": 40, "count": 6,
        "name_table": "trainer_names", "class_table": "trainer_classes",
        "evidence": "synthetic trainer headers",
        "fields": {"party_flags": 0, "class": 1, "ai_flags": 28, "party_size": 32, "party": 36},
        "class_names": {"base": 0x800, "stride": 13, "count": 107, "pointer": 0,
                        "evidence": "synthetic long class names"},
        "party": {
            "custom_moves_flag": 1, "held_item_flag": 2,
            "default_stride": 8, "custom_stride": 16,
            "iv": 0, "level": 2, "species": 4, "held_item": 6,
            "moves": 6, "moves_with_item": 8, "move_count": 4,
            "evidence": "synthetic party layouts",
        },
    }

def rom_bytes() -> bytes:
    data = bytearray(0x1000)
    for tid, (flags, size, pointer) in enumerate(((0, 2, PARTY), (1, 1, PARTY + 0x10),
                                                  (2, 1, PARTY + 0x20), (3, 1, PARTY + 0x30),
                                                  (0, 0, 0), (0, 1, PARTY + 0x40))):
        at = BASE + tid * 40
        data[at], data[at + 1], data[at + 32] = flags, tid + 1, size
        struct.pack_into("<I", data, at + 28, tid * 0x1020304)
        if pointer:
            struct.pack_into("<I", data, at + 36, 0x08000000 + pointer)
    struct.pack_into("<I", data, 0x800 + 2 * 13, 0x08000900)
    struct.pack_into("<HHH", data, PARTY, 0, 10, 19)
    struct.pack_into("<HHH", data, PARTY + 8, 0, 10, 161)
    struct.pack_into("<HHH", data, PARTY + 0x10, 32, 41, 809)
    struct.pack_into("<4H", data, PARTY + 0x16, 644, 675, 157, 637)
    struct.pack_into("<HHHH", data, PARTY + 0x20, 12, 61, 969, 142)
    struct.pack_into("<HHHH", data, PARTY + 0x30, 67, 62, 517, 495)
    struct.pack_into("<4H", data, PARTY + 0x38, 89, 350, 433, 68)
    return bytes(data)

def map_rom() -> bytes:
    """Two map headers whose object scripts start trainer battles."""
    data = bytearray(rom_bytes())
    ptr = lambda at, target: struct.pack_into("<I", data, at, 0x08000000 + target)   # noqa: E731
    ptr(GROUPS, BANK)
    ptr(BANK, HEADERS[0])
    ptr(BANK + 4, HEADERS[1])
    struct.pack_into("<I", data, ENGINE, (0x08000000 + ENGINE_BANK) ^ XOR)
    ptr(ENGINE_BANK, HEADERS[1])
    ptr(ENGINE_BANK + 4, HEADERS[0])
    script = SCRIPTS
    for header, events, objects, section, trainers in zip(HEADERS, EVENTS, OBJECTS, (0x5A, 0x5E),
                                                          ((1, 0x4001), (1, 3))):
        ptr(header, 0xB00)
        ptr(header + 4, events)
        data[header + 20] = section
        data[events] = len(trainers)
        ptr(events + 4, objects)
        for index, trainer in enumerate(trainers):
            data[objects + 24 * index] = index + 1
            ptr(objects + 24 * index + 16, script)
            struct.pack_into("<BBHH", data, script, 0x5C, 3, trainer, 0)   # trainerbattle 3: one text
            ptr(script + 6, 0xE00)
            data[script + 10] = 0x02
            script += 0x20
    data[0xE00] = 0xFF
    return bytes(data)

def map_profile(engine: bool = True) -> dict:
    maps = {"groups": GROUPS, "mapsec_names": 0xF00, "mapsec_first": 0, "mapsec_count": 256}
    if engine:
        maps["engine"] = {"groups": ENGINE, "count": 1, "bank_xor": XOR}
    return {"id": "demo", "maps": maps}

def map_block(rooms=("0.1",)) -> dict:
    b = block()
    b["maps"] = {"evidence": "synthetic object scripts"}
    if rooms is not None:
        b["ev_training"] = {"maps": list(rooms), "evidence": "synthetic rebattle room"}
    return b
