from gen3tl.data import pokedextrainerivs as iv

def trainer(trainer_id=1, class_id=57):
    return {"id": trainer_id, "class_id": class_id, "ai_flags": 1, "party_flags": 0}

def member(species=19, level=10, spread=0, moves=None):
    return {"index": 0, "species": species, "level": level, "iv": spread, "moves": moves}

def uniform(value, speed=None):
    result = [value] * 6
    if speed is not None:
        result[3] = speed
    return result

PROOF = [{"offset": 0, "size": 4, "sha256": "0" * 64}]

STAGES = {"trainer_level": "adjustment", "held_item_form": "held_item", "minior_core": "final",
          "scaled_evolution": "create"}

def species_rule(rule_id, kind, **fields):
    return {"id": rule_id, "kind": kind, "label": f"{rule_id} label", "status": "known", "native": "confirmed",
            "stage": STAGES[kind], "evidence": "Branch and store bytes", "rom": PROOF, **fields}

def scaled_rule(**fields):
    return species_rule(
        "scaled", "scaled_evolution", modes=[5, 13], evolutions={"offset": 0xA0, "pointer": 0x08, "stride": 16, "entries": 2},
        methods={"level": [4], "other": [5], "item": [7], "friendship": [1]},
        levels={"other": 40, "item": 45, "friendship": 55}, keep_items=[7], boss_classes=[2], custom_moves_flag=1,
        raise_difficulties=[3], unscaled_difficulties=[1], unscaled_sections=[0x89], unscaled_trainers=[9],
        party_level_penalties=[3, 5, 6, 7, 8, 8], raised_level_penalty=2,
        badge_levels={"offset": 0xE0, "stride": 2, "count": 3}, hall_badges=2,
        labels={key: f"{key} label" for key in iv.SCALED_LABELS},
        battles=[{"id": "rematch", "modes": [5], "label": "rematch label", "evidence": "Script bytes"}], **fields)
