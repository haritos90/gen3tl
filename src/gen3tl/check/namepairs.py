"""Names a sprite font draws one after the other for a species (profile [[sprite_font]] pair)."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..build import spritefont
from ..core.charmap import CharmapError
from ..data.pokedexlearnsets import LEVEL_UP, METHODS

FIRST, SECOND = spritefont.PAIR_TABLES


@dataclass
class Name:
    id: str        # inventory id
    text: str      # as shown, with its suffix
    width: int     # px


@dataclass
class Pair:
    species: str   # inventory id of the species name
    species_text: str
    first: Name
    second: Name
    shared: int = 0   # outline columns past the last ink

    @property
    def width(self) -> int:
        return self.first.width + self.second.width + self.shared


@dataclass
class Result:
    font: str
    width: int
    where: str
    suffix: dict[str, str]                                    # table -> suffix
    widest: dict[str, Pair] = field(default_factory=dict)     # species id -> its widest pair
    names: dict[str, Pair] = field(default_factory=dict)      # name id -> its widest pair

    def over(self) -> list[Pair]:
        """The widest pairs of the species past the width, in slot order."""
        return [p for p in self.widest.values() if p.width > self.width]


def species_names(dataset: dict, rule: spritefont.PairRule) -> list[tuple[str, list[int], list[int]]]:
    """(species id, move ids, ability ids) of every species the rule's encounter methods meet."""
    codes = {METHODS.index(n) for n in rule.learnsets}
    met = {e["species"] for e in dataset.get("encounters", []) if e.get("method") in rule.encounters}
    by_slot = {r["slot"]: r for r in dataset.get("species", [])}
    out = []
    for slot in sorted(s for s in met if s in by_slot):
        rec = by_slot[slot]
        rows = list(rec.get("learnset") or [])
        source = by_slot.get(rec.get("learnset_species"))
        if source is not None and source is not rec:
            have = {(r["method"], r["move"]) for r in rows}
            rows += [r for r in source.get("learnset") or []
                     if r["method"] != LEVEL_UP and (r["method"], r["move"]) not in have]
        moves = sorted({r["move"] for r in rows if r["method"] in codes})
        out.append((rec["name"], moves, list(rec.get("abilities") or [])))
    return out


def results(prof, texts: dict[str, str], measures: dict, dataset: dict | None) -> list[Result]:
    """The widest pairs of every sprite font of `prof` with a pair rule and a measure."""
    if dataset is None:
        return []
    out = []
    for font in spritefont.from_profile(prof):
        if font.pair is None or font.name not in measures:
            continue
        m, cm = measures[font.name]
        res = Result(font.name, font.pair.width, font.pair.where, {w.table: w.suffix for w in font.windows})
        ids = {t: {r["id"]: r["name"] for r in dataset.get(t, [])} for t in (FIRST, SECOND)}
        cache: dict[str, Name | None] = {}

        def name(table: str, i: int) -> Name | None:
            sid = ids[table].get(i)
            if sid not in cache:
                text = None if sid is None else texts.get(sid)
                try:
                    shown = None if text is None else text + res.suffix[table]
                    cache[sid] = None if shown is None else Name(sid, shown, m.width(shown, cm))
                except CharmapError:
                    cache[sid] = None
            return cache[sid]

        for species, moves, abilities in species_names(dataset, font.pair):
            firsts = [n for n in (name(FIRST, i) for i in moves) if n]
            seconds = [n for n in (name(SECOND, i) for i in abilities) if n]
            if not firsts or not seconds:
                continue
            text = texts.get(species, species)
            a, b = (max(ns, key=lambda n: n.width) for ns in (firsts, seconds))
            res.widest[species] = Pair(species, text, a, b, m.shared)
            for n in firsts:
                if n.id not in res.names or b.width > res.names[n.id].second.width:
                    res.names[n.id] = Pair(species, text, n, b, m.shared)
            for n in seconds:
                if n.id not in res.names or a.width > res.names[n.id].first.width:
                    res.names[n.id] = Pair(species, text, a, n, m.shared)
        out.append(res)
    return out


def message(res: Result, pair: Pair) -> str:
    """The QA finding of a species whose widest pair runs past the width."""
    return (f"pair too wide: {pair.width} > {res.width} px in {res.where} ({res.font}): "
            f"«{pair.first.text}» {pair.first.id}, {pair.first.width} px, and «{pair.second.text}» "
            f"{pair.second.id}, {pair.second.width} px, outline {pair.shared} px, for «{pair.species_text}»")


def partner(res: Result, sid: str, key=lambda x: x) -> dict | None:
    """The pair data of a name for the editor (entrycheck.pair_problems), ids through `key`."""
    pair = res.names.get(sid)
    if pair is None:
        return None
    own, other = (pair.first, pair.second) if pair.first.id == sid else (pair.second, pair.first)
    table = FIRST if own is pair.first else SECOND
    return {"font": res.font, "width": res.width, "where": res.where, "suffix": res.suffix[table],
            "partner": {"id": key(other.id), "text": other.text, "width": other.width,
                        "species": {"id": key(pair.species), "text": pair.species_text}}}


def dataset(prof) -> dict | None:
    """The tracked Pokédex dataset of the profile with its learnsets, or None."""
    if not any(f.pair for f in spritefont.from_profile(prof)):
        return None
    from ..data.pokedexdata import read
    from ..data.pokedextexts import tracked_dataset
    path = tracked_dataset(prof.name)
    return None if path is None else read(path)
