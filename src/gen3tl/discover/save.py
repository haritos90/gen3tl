"""Header and native-serializer evidence for the flash save layout."""

from __future__ import annotations

import copy

from ..core.charmap import Charmap, CharmapError
from ..core.rom import Rom
from ..core.thumb import decode
from ..profile import schema
from .foundations import evidence
from .table_core import Result
from .table_readers import Readers

SOURCE = "save/1.0.0"


def _record(result, identifier, value, proof, tier="crossref"):
    section = schema.section_of(tuple(identifier.split(".")))
    result.facts.append(schema.Fact(identifier, section, "save", copy.deepcopy(value), tier=tier,
                                    status="verified", source=SOURCE, evidence=proof))


def _issue(result, identifier, sentence, method):
    section = schema.section_of(tuple(identifier.split(".")))
    result.issues.append(schema.Fact(identifier, section, "save", None, status="unresolved",
                                     source=SOURCE, evidence=evidence(sentence, method)))


def _set(result, identifier, value, proof, tier="crossref"):
    path = identifier.split(".")
    parent = result.data
    for name in path[:-1]:
        parent = parent.setdefault(name, {})
    parent[path[-1]] = value
    _record(result, identifier, value, proof, tier)


def _set_fact(result, identifier, value, proof, tier="crossref"):
    path = identifier.split(".")
    parent = result.data
    for name in path[:-1]:
        parent = parent.setdefault(name, {})
    parent[path[-1]] = value
    _record(result, identifier, value, proof, tier)


def _set_fields(result, identifier, value, proof, tier="crossref"):
    path = identifier.split(".")
    parent = result.data
    for name in path[:-1]:
        parent = parent.setdefault(name, {})
    parent[path[-1]] = value

    def record(path, item):
        if isinstance(item, dict):
            for key, child in item.items():
                record(path + "." + schema.key_text(key), child)
        else:
            _record(result, path, item, proof, tier)

    record(identifier, value)


def _instruction(rom, readers, descriptor, mnemonic=None):
    matches = readers.matches.get(descriptor["reader"], ())
    if len(matches) != 1:
        return None
    instruction = decode(rom.data, matches[0] + descriptor["offset"])
    if mnemonic is not None and instruction.mnemonic != mnemonic:
        return None
    return instruction


def _ram_pool(rom, readers, descriptor, config):
    instruction = _instruction(rom, readers, descriptor)
    if instruction is None or instruction.literal is None:
        return None
    value = rom.u32(instruction.literal)
    if not config["ram_base"] <= value < config["ram_base"] + config["ram_size"]:
        return None
    return {"value": value, "pool": instruction.literal, "site": instruction.address,
            "function": descriptor["reader"]}


def _name_pool(rom, readers, descriptor):
    instruction = _instruction(rom, readers, descriptor)
    if instruction is None or instruction.literal is None:
        return None
    pool = instruction.literal
    target = rom.ptr_at(pool)
    if target is None:
        return None
    try:
        text = Charmap().decode(rom.data, target, limit=64)[0]
    except CharmapError:
        return None
    if text != descriptor["text"]:
        return None
    return {"value": pool, "pool": pool, "site": instruction.address,
            "function": descriptor["reader"]}


def _proof(sentence, method, rows, **counts):
    return evidence(sentence, method, [row["pool"] for row in rows],
                    [row["site"] for row in rows], **counts)


def _decoded(rom, readers, descriptor, mnemonic=None):
    instruction = _instruction(rom, readers, descriptor, mnemonic)
    if instruction is None:
        return None
    if instruction.literal is not None:
        value = rom.u32(instruction.literal)
        anchor = instruction.literal
    elif instruction.immediate is not None:
        value = instruction.immediate
        anchor = readers.matches[descriptor["reader"]][0]
    else:
        return None
    return {"value": value, "pool": anchor, "site": instruction.address, "instruction": instruction}


def _rival(rom, result, readers, config, blocks, player, block_size):
    descriptor = config["rival"]
    offset = _decoded(rom, readers, descriptor | {"offset": descriptor["offset"]})
    add = _instruction(rom, readers, descriptor | {"offset": descriptor["add"]}, "add")
    screen = _decoded(rom, readers, descriptor | {"offset": descriptor["screen"]}, "mov")
    if (offset is None or add is None or screen is None or screen["value"] != descriptor["kind"]
            or offset["value"] >= block_size):
        _issue(result, "save.rival", "The rival naming call lacks a valid SaveBlock1 destination.",
               "semantic-naming-call+saveblock1")
        return
    value = {"name": blocks[descriptor["block"]]["ram"] + offset["value"],
             "size": player["size"], "limit": player["limit"]}
    proof = _proof("The rival naming call writes the decoded SaveBlock1 field with the player name shape.",
                   "semantic-naming-call+saveblock1+family-name-shape", [offset, screen],
                   name_limit=player["limit"])
    _set_fact(result, "save.rival", value, proof)


def _box_names(rom, result, readers, config, box_ranges):
    descriptor = config["box_names"]
    instruction = _instruction(rom, readers, descriptor)
    base = rom.ptr_at(instruction.literal) if instruction is not None and instruction.literal is not None else None
    load = {"base": base, "pool": instruction.literal, "site": instruction.address} if base is not None else None
    count = sum(row["count"] for row in box_ranges)
    addresses = []
    if load is not None:
        for index in range(count):
            at = load["base"] + descriptor["pointers"] + index * 4
            if at + 4 > len(rom):
                break
            addresses.append(rom.u32(at))
    valid_ram = all(config["ram_base"] <= address < config["ram_base"] + config["ram_size"]
                    for address in addresses)
    if (load is None or len(addresses) != count or len(set(addresses)) != count or not valid_ram
            or any((right - left) % descriptor["size"] for left, right in zip(addresses, addresses[1:]))):
        _issue(result, "save.box_names", "The native box-name getter lacks one valid pointer per discovered box.",
               "semantic-getter+native-pointer-table")
        return
    value = {"size": descriptor["size"], "limit": descriptor["limit"], "addresses": addresses}
    proof = evidence("The native box-name getter supplies one RAM pointer per box in logical order.",
                     "semantic-getter+native-pointer-table+family-name-shape",
                     [load["base"], load["pool"]], [load["site"]], boxes=count,
                     stride=descriptor["size"])
    _set_fact(result, "save.box_names", value, proof)


def _mail(rom, result, readers, config, blocks, player, block_size):
    descriptor = config["mail"]
    base = _decoded(rom, readers, descriptor | {"offset": descriptor["offset"]})
    left = _decoded(rom, readers, descriptor | {"offset": descriptor["stride"][0]}, "lsl")
    middle = _instruction(rom, readers, descriptor | {"offset": descriptor["stride"][1]}, "add")
    right = _decoded(rom, readers, descriptor | {"offset": descriptor["stride"][2]}, "lsl")
    count = _decoded(rom, readers, descriptor | {"offset": descriptor["count"]}, "cmp")
    record = descriptor["record"]
    author = _decoded(rom, readers, record | {"offset": record["author"]}, "add")
    trainer = _decoded(rom, readers, record | {"offset": record["trainer_id"]}, "add")
    item = _decoded(rom, readers, record | {"offset": record["item"]}, "strh")
    author_count = _decoded(rom, readers, record | {"offset": record["author_count"]}, "cmp")
    rows = [base, left, right, count, author, trainer, item, author_count]
    if (any(row is None for row in rows) or middle is None or base["value"] >= block_size
            or author["value"] + author_count["value"] >= trainer["value"] or trainer["value"] + 4 > item["value"]):
        _issue(result, "save.mail", "Native mail loops or record accesses do not establish a bounded layout.",
               "semantic-mail-loop+record-access")
        return
    stride = ((1 << left["value"]) + 1) * (1 << right["value"])
    value = {"base": blocks["saveblock1"]["ram"] + base["value"], "count": count["value"] + 1,
             "stride": stride, "author": author["value"], "author_size": author_count["value"] + 1,
             "author_limit": player["limit"], "author_pad": descriptor["author_pad"],
             "trainer_id": trainer["value"], "item": item["value"]}
    if value["count"] * stride > block_size - base["value"] or item["value"] + 2 > stride:
        _issue(result, "save.mail", "Decoded mail records exceed their native array or record stride.",
               "semantic-mail-loop+record-access")
        return
    proof = _proof("Native mail loops decode the array base, count, stride and author fields.",
                   "semantic-mail-loop+record-access+family-name-shape", rows,
                   count=value["count"], stride=stride)
    _set_fact(result, "save.mail", value, proof)


def _daycare_mail(rom, result, readers, config, mail):
    descriptor = config["daycare_mail"]
    rows = {name: _decoded(rom, readers, {"reader": descriptor["reader"], "offset": offset}, "add")
            for name, offset in descriptor["fields"].items()}
    if not mail or any(row is None for row in rows.values()):
        _issue(result, "save.daycare_mail", "Daycare storage lacks decoded mail, author or nickname destinations.",
               "semantic-daycare-store+record-offset")
        return
    value = {name: row["value"] for name, row in rows.items()}
    value.update(ot_name_size=descriptor["ot_name_size"], nickname_size=descriptor["nickname_size"])
    if (value["mail"] + mail["stride"] > value["ot_name"]
            or value["ot_name"] + value["ot_name_size"] > value["nickname"]
            or value["nickname"] + value["nickname_size"] > config["daycare"]["stride"]):
        _issue(result, "save.daycare_mail", "Decoded daycare name fields overlap or leave the native record.",
               "semantic-daycare-store+record-offset")
        return
    proof = _proof("Daycare storage decodes nonoverlapping mail, author and nickname destinations.",
                   "semantic-daycare-store+record-offset+family-name-shape", list(rows.values()),
                   stride=mail["stride"])
    _set_fact(result, "save.daycare_mail", value, proof)


def _link_records(rom, result, readers, config, blocks, player, block_size):
    descriptor = config["link_records"]
    base = _decoded(rom, readers, descriptor | {"offset": descriptor["offset"]})
    add = _instruction(rom, readers, descriptor | {"offset": descriptor["add"]}, "add")
    clearer = {"reader": descriptor["clearer"]}
    count = _decoded(rom, readers, clearer | {"offset": descriptor["count"]}, "mov")
    stride = _decoded(rom, readers, clearer | {"offset": descriptor["stride"]}, "add")
    trainer = _decoded(rom, readers, {"reader": descriptor["search"], "offset": descriptor["trainer_id"]}, "ldrh")
    rows = [base, count, stride, trainer]
    if any(row is None for row in rows) or add is None or base["value"] >= block_size:
        _issue(result, "save.link_records", "Native link-record routines lack a bounded base, count or field.",
               "semantic-link-records+record-access")
        return
    value = {"base": blocks["saveblock2"]["ram"] + base["value"], "count": count["value"] + 1,
             "stride": stride["value"], "name_size": player["size"], "limit": player["limit"],
             "trainer_id": trainer["value"]}
    if value["count"] * value["stride"] > block_size - base["value"] or trainer["value"] + 2 > stride["value"]:
        _issue(result, "save.link_records", "Decoded link records exceed their native array or record stride.",
               "semantic-link-records+record-access")
        return
    proof = _proof("Native link-record routines decode the array and opponent name and trainer fields.",
                   "semantic-link-records+record-access+family-name-shape", rows,
                   count=value["count"], stride=value["stride"])
    _set_fact(result, "save.link_records", value, proof)


def _hall_of_fame(rom, result, readers, config, flash):
    descriptor = config["hall_of_fame"]
    sector_rows = [_decoded(rom, readers, {"reader": descriptor["reader"], "offset": offset}, "mov")
                   for offset in descriptor["sectors"]]
    size = _decoded(rom, readers, {"reader": descriptor["reader"], "offset": descriptor["size"]}, "mov")
    shift = _decoded(rom, readers, {"reader": descriptor["reader"], "offset": descriptor["shift"]}, "lsl")
    signature = _decoded(rom, readers, {"reader": descriptor["signature_reader"],
                                       "offset": descriptor["signature"]})
    rows = [*sector_rows, size, shift, signature]
    if any(row is None for row in rows):
        _issue(result, "save.hall_of_fame", "Hall of Fame serialization lacks sector, size or signature evidence.",
               "semantic-hall-serializer+sector-immediates")
        return
    sectors = [row["value"] for row in sector_rows]
    data_size = size["value"] << shift["value"]
    if (sectors != sorted(set(sectors)) or any(sector >= flash["total_sectors"] for sector in sectors)
            or not 0 < data_size <= flash["data_size"]):
        _issue(result, "save.hall_of_fame", "Decoded Hall of Fame sectors or data size leave the flash layout.",
               "semantic-hall-serializer+sector-immediates")
        return
    value = {key: descriptor[key] for key in ("team_size", "mons", "mon_size", "trainer_id", "personality",
             "species", "nickname", "nickname_size", "nickname_limit", "egg_species")}
    value = {"sectors": sectors, "data_size": data_size, "signature": signature["value"], **value}
    if value["team_size"] != value["mons"] * value["mon_size"] or value["nickname"] + value["nickname_size"] > value["mon_size"]:
        _issue(result, "save.hall_of_fame", "The family Hall of Fame record shape is inconsistent.",
               "family-hall-record-shape")
        return
    proof = _proof("Hall of Fame serialization decodes its sectors, data bytes and signature; the family supplies records.",
                   "semantic-hall-serializer+sector-immediates+family-record-shape", rows,
                   sectors=len(sectors), data_size=data_size)
    _set_fact(result, "save.hall_of_fame", value, proof)


def _egg_hatch(result, config, daycare, parasite, parasite_row, daycare_row, saveblock1_size):
    descriptor = config["egg_hatch"]
    parasite_size = sum(size for _, size in config["parasite_sections"])
    if (parasite is None or daycare_row is None
            or config["daycare_offset"] + descriptor["step_from_daycare"] >= saveblock1_size
            or descriptor["eggs_waiting"] >= parasite_size or descriptor["cycles"] >= config["party"]["stride"]):
        _issue(result, "save.egg_hatch", "Egg fields lack valid discovered bases and bounded family offsets.",
               "native-daycare+parasite+family-egg-shape")
        return
    value = {"step_counter": daycare["base"] + descriptor["step_from_daycare"],
             "cycles": descriptor["cycles"],
             "eggs_waiting": parasite["value"] + descriptor["eggs_waiting"]}
    proof = _proof("The discovered daycare and parasite bases bind the family egg-cycle fields.",
                   "native-daycare+parasite+family-egg-shape", [daycare_row, parasite_row],
                   step_from_daycare=descriptor["step_from_daycare"])
    _set_fact(result, "save.egg_hatch", value, proof)


def run(rom: Rom, family, tables: dict, readers=None) -> Result:
    result = Result()
    config = family.anchors["save"]
    readers = Readers(rom, family.readers) if readers is None else readers
    header = family.anchors["identity"]["header_offset"]
    fields = {name: rom.u32(header + offset) for name, offset in config["header"].items()
              if name != "player_name_length"}
    player_name_length = rom.u8(header + config["header"]["player_name_length"])
    flash = config["flash"]
    sector_size, data_size = flash["sector_size"], flash["data_size"]
    saveblock2_size, saveblock1_size = fields["saveblock2_size"], fields["saveblock1_size"]
    valid_header = (0 < saveblock2_size <= data_size and 3 * data_size < saveblock1_size <= 4 * data_size
                    and fields["party_count"] < saveblock1_size and fields["party_offset"] < saveblock1_size
                    and fields["player_name"] < saveblock2_size and fields["trainer_id"] < saveblock2_size)
    if not valid_header:
        _issue(result, "save.header", "Header save sizes or offsets leave their declared native blocks.",
               "rom-header+block-bounds")
        return result
    header_proof = evidence("The ROM header supplies native block sizes and field offsets.",
                            "rom-header+block-bounds", [header + offset for offset in config["header"].values()],
                            [header + offset for offset in config["header"].values()])
    section_sizes = [0] * flash["slot_sectors"]
    section_sizes[0] = saveblock2_size
    for section in config["sections"]["saveblock1"][:-1]:
        section_sizes[section] = data_size
    section_sizes[config["sections"]["saveblock1"][-1]] = saveblock1_size - 3 * data_size
    for section in config["sections"]["storage"][:-1]:
        section_sizes[section] = data_size
    section_sizes[config["sections"]["storage"][-1]] = config["storage_last"]
    values = {
        "size": flash["total_sectors"] * sector_size,
        "sector_size": sector_size,
        "data_size": data_size,
        "signature": flash["signature"],
        "slot_sectors": flash["slot_sectors"],
        "section_sizes": section_sizes,
    }
    for name, value in values.items():
        _set(result, "save." + name, value, header_proof, "shape")

    block_rows = {}
    for name, descriptor in config["block_pools"].items():
        row = _ram_pool(rom, readers, descriptor, config)
        if row is None:
            _issue(result, "save.blocks", "Native save-block address pools are missing, ambiguous or outside EWRAM.",
                   "semantic-serializer+ram-literal")
            return result
        block_rows[name] = row
    block_proof = _proof("One native address-setup function supplies all three save-block bases.",
                         "semantic-serializer+ram-literal", list(block_rows.values()), blocks=len(block_rows))
    blocks = {name: {"ram": row["value"], "sections": config["sections"][name]}
              for name, row in block_rows.items()}
    _set_fields(result, "save.blocks", blocks, block_proof)

    parasite = _ram_pool(rom, readers, config["parasite_pool"], config)
    if parasite is not None:
        value = {"ram": parasite["value"], "sections": config["parasite_sections"]}
        _set_fields(result, "save.parasite", value,
             _proof("The native flash serializer identifies the parasite RAM base; the family shape supplies its slices.",
                    "semantic-flash-writer+family-save-shape", [parasite], slices=len(value["sections"])))
    else:
        _issue(result, "save.parasite", "The native parasite RAM pool is missing or ambiguous.",
               "semantic-flash-writer+ram-literal")

    extra = _ram_pool(rom, readers, config["extra_pool"], config)
    sectors = []
    for offset in config["extra_sector_offsets"]:
        instruction = _instruction(rom, readers, {"reader": config["extra_pool"]["reader"], "offset": offset}, "mov")
        if instruction is None or instruction.immediate is None:
            sectors = []
            break
        sectors.append(instruction.immediate)
    if extra is not None and sectors and sectors == sorted(set(sectors)):
        value = {"ram": extra["value"], "sectors": sectors}
        proof = _proof("The native flash writer supplies the extra RAM base and sector numbers.",
                       "semantic-flash-writer+ram-literal+sector-immediates", [extra], sectors=len(sectors))
        _set_fields(result, "save.extra", value, proof)
    else:
        _issue(result, "save.extra", "The native extra-sector writer lacks a unique RAM base or ordered sector immediates.",
               "semantic-flash-writer+ram-literal+sector-immediates")

    party_shape = config["party"]
    party = {"count": blocks["saveblock1"]["ram"] + fields["party_count"],
             "base": blocks["saveblock1"]["ram"] + fields["party_offset"], **party_shape}
    party_proof = evidence("Header offsets bind the family party shape to the discovered SaveBlock1 base.",
                           "rom-header+saveblock1+family-party-shape",
                           [block_rows["saveblock1"]["pool"], header + config["header"]["party_count"],
                            header + config["header"]["party_offset"]],
                           [block_rows["saveblock1"]["site"]], slots=party_shape["slots"],
                           stride=party_shape["stride"])
    _set_fields(result, "save.party", party, party_proof)
    player = {"name": blocks["saveblock2"]["ram"] + fields["player_name"],
              "size": player_name_length + 1, "limit": player_name_length,
              "trainer_id": blocks["saveblock2"]["ram"] + fields["trainer_id"]}
    player_proof = evidence("Header offsets bind player fields to the discovered SaveBlock2 base.",
                            "rom-header+saveblock2+player-name-length",
                            [block_rows["saveblock2"]["pool"], header + config["header"]["player_name"],
                             header + config["header"]["trainer_id"]], [block_rows["saveblock2"]["site"]],
                            name_limit=player_name_length)
    _set_fact(result, "save.player", player, player_proof)

    daycare = {"base": blocks["saveblock1"]["ram"] + config["daycare_offset"], **config["daycare"]}
    _set_fields(result, "save.daycare", daycare, party_proof)
    _set_fields(result, "save.box_record", config["box_record"],
         evidence("The family compressed-record shape defines native storage fields.",
                  "family-compressed-record-shape", [block_rows["storage"]["pool"]],
                  [block_rows["storage"]["site"]], size=config["box_record"]["size"]), "shape")
    box_ranges = []
    for row in config["box_ranges"]:
        source = blocks.get(row["block"], {}).get("ram")
        if source is None and row["block"] == "extra" and extra is not None:
            source = extra["value"]
        if source is None:
            break
        box_ranges.append({"first": row["first"], "count": row["count"], "ram": source + row["offset"]})
    if len(box_ranges) == len(config["box_ranges"]):
        proof_rows = list(block_rows.values()) + ([extra] if extra else [])
        proof = evidence("Family box ranges bind to discovered save-block and extra-storage bases.",
                         "family-box-ranges+native-block-bases", [row["pool"] for row in proof_rows],
                         [row["site"] for row in proof_rows], ranges=len(box_ranges))
        result.data["save"]["boxes"] = box_ranges
        for row in box_ranges:
            _record(result, f"save.boxes[first={row['first']:#x}]", row, proof)
    else:
        _issue(result, "save.boxes", "A family box range lacks its discovered native RAM base.",
               "family-box-ranges+native-block-bases")

    species = next((row for row in tables.get("table", []) if row.get("name") == "species"), None)
    name_descriptors = config["name_pools"]
    name_rows = {name: _name_pool(rom, readers, descriptor) for name, descriptor in name_descriptors.items()}
    if species is not None and all(name_rows.values()):
        names = {"species_table": species["base"], "species_stride": species["stride"],
                 "species_count": species["count"], "egg_pointer": name_rows["egg"]["value"],
                 "box_text_pointer": name_rows["box"]["value"],
                 "box_name_pointers": {str(config["preset_box"]): name_rows["preset"]["value"]}}
        proof = _proof("The discovered species table and named native text pools supply default save names.",
                       "species-table+semantic-name-pools+decoded-anchors", list(name_rows.values()),
                       names=len(name_rows), species=species["count"])
        _set_fields(result, "save.names", names, proof)
    else:
        _issue(result, "save.names", "The species table or named native text pools are missing or ambiguous.",
               "species-table+semantic-name-pools+decoded-anchors")

    daycare_row = _decoded(rom, readers, {"reader": config["daycare_mail"]["reader"],
                                         "offset": config["daycare_mail"]["fields"]["mail"]}, "add")
    mon_names = config["mon_names"]
    valid_mon_names = (mon_names["personality"] + 4 <= mon_names["ot_id"]
                       and mon_names["ot_id"] + 4 <= mon_names["nickname"]
                       and mon_names["nickname"] + mon_names["nickname_size"] <= mon_names["ot_name"]
                       and mon_names["ot_name"] + mon_names["ot_name_size"] <= config["party"]["stride"])
    if daycare_row is None or not valid_mon_names:
        _issue(result, "save.mon_names", "The native daycare record or family monster-name shape is inconsistent.",
               "semantic-daycare-record+family-mon-name-shape")
    else:
        proof = _proof("The native daycare record anchors the family monster-name field layout.",
                       "semantic-daycare-record+family-mon-name-shape", [daycare_row],
                       nickname_limit=config["mon_names"]["nickname_limit"])
        _set_fact(result, "save.mon_names", mon_names, proof)
    _rival(rom, result, readers, config, blocks, player, saveblock1_size)
    _box_names(rom, result, readers, config, config["box_ranges"])
    _mail(rom, result, readers, config, blocks, player, saveblock1_size)
    _daycare_mail(rom, result, readers, config, result.data["save"].get("mail", {}))
    _link_records(rom, result, readers, config, blocks, player, saveblock2_size)
    _hall_of_fame(rom, result, readers, config, flash)
    _egg_hatch(result, config, daycare, parasite, parasite, daycare_row, saveblock1_size)
    return result
