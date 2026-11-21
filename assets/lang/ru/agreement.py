"""Agreement review provider for this language pack."""

from __future__ import annotations

import re
from gen3tl.lang.termbase import Term, Senses, USE, FORM_ONLY, find_forms, strip_codes

# grammar

ADJ_NOM = ("ый", "ий", "ой", "ая", "яя", "ое", "ее", "ые", "ие")
ADJ_GEN = ("ого", "его", "ой", "ей", "ых", "их")
ROW_GENDER = {"male": "masc", "female": "femn"}
DECLENSION = {"1": "1st declension", "2": "2nd declension", "3": "3rd declension", "adj": "adjectival",
              "fixed": "indeclinable"}
GENDER = {"masc": "masculine", "femn": "feminine", "neut": "neuter"}


def _letters(word: str) -> str:
    return re.sub(r"[^а-яё]", "", word.lower().split("-")[-1])


def grammar(forms: dict[str, str], gender: str = "", declension: str = "", morph=None) -> dict[str, str]:
    """Gender, number, declension and animacy of a term from its forms."""
    nom, gen = forms.get("nomn.sing", ""), forms.get("gent.sing", "")
    out = {"gender": "", "number": "sing", "declension": "", "animacy": ""}
    a, b = nom.split(" "), gen.split(" ")
    change = [i for i in range(len(a)) if len(a) == len(b) and a[i].lower() != b[i].lower()]
    nouns = [i for i in change if not (a[i].lower().endswith(ADJ_NOM) and b[i].lower().endswith(ADJ_GEN))]
    head = (nouns or change or [0])[0]
    hn = _letters(a[head])
    hg = _letters(b[head]) if head < len(b) else ""
    if declension == "fixed" or not change:
        out["declension"] = "fixed"
    elif head not in nouns:   # declines as an adjective
        out["declension"] = "adj"
        g = {"ый": "masc", "ий": "masc", "ой": "masc", "ая": "femn", "яя": "femn", "ое": "neut", "ее": "neut"}
        out["gender"] = g.get(hn[-2:], "")
        if hn[-2:] in ("ые", "ие"):
            out["number"] = "plur"
    elif hn[-1:] in ("а", "я"):
        out["declension"], out["gender"] = "1", "femn"
    elif hn[-1:] == "ь" and hg.endswith("и"):
        out["declension"], out["gender"] = "3", "femn"
    elif hn[-1:] in ("о", "е", "ё"):
        out["declension"], out["gender"] = "2", "neut"
    elif hn:
        out["declension"], out["gender"] = "2", "masc"
    if morph is not None and out["declension"] != "adj" and hn:
        ps = [p for p in morph.parse(hn) if p.tag.POS == "NOUN" and "nomn" in p.tag and p.is_known]
        if ps and ps[0].tag.gender in GENDER:
            out["gender"] = str(ps[0].tag.gender)   # a typed grammeme refuses "plur"
        if ps and "Pltm" in ps[0].tag:
            out["number"] = "plur"
    if out["declension"] != "fixed" and nom and forms.get("nomn.plur") == nom:
        out["number"] = "plur"
    if gender in ROW_GENDER:
        out["gender"] = ROW_GENDER[gender]
    elif gender == "plural":
        out["number"] = "plur"
    ns, np_, gp, ap = nom, forms.get("nomn.plur"), forms.get("gent.plur"), forms.get("accs.plur")
    if np_ and gp and ap and np_ != gp:
        out["animacy"] = "anim" if ap == gp else "inan"
    elif out["gender"] == "masc" and forms.get("accs.sing") and gen and ns != gen:
        out["animacy"] = "anim" if forms["accs.sing"] == gen else "inan"
    return out


def grammar_changes(old: dict, new: dict) -> list[dict]:
    """The properties that differ where both are known."""
    out = []
    for k in ("gender", "number", "declension", "animacy"):
        if k == "gender" and "plur" in (old["number"], new["number"]):
            continue
        if old[k] and new[k] and old[k] != new[k]:
            out.append({"property": k, "old": old[k], "new": new[k]})
    return out


def describe(g: dict) -> str:
    bits = ["plural" if g["number"] == "plur" else GENDER.get(g["gender"], "gender unknown"),
            DECLENSION.get(g["declension"], "declension unknown")]
    if g["animacy"]:
        bits.append("animate" if g["animacy"] == "anim" else "inanimate")
    return ", ".join(bits)


# agreement

ADJ_ENDS = {   # case -> gender or plur -> endings of an agreeing adjective
    "nomn": {"masc": ("ый", "ий", "ой"), "femn": ("ая", "яя"), "neut": ("ое", "ее"), "plur": ("ые", "ие")},
    "gent": {"masc": ("ого", "его"), "femn": ("ой", "ей"), "neut": ("ого", "его"), "plur": ("ых", "их")},
    "datv": {"masc": ("ому", "ему"), "femn": ("ой", "ей"), "neut": ("ому", "ему"), "plur": ("ым", "им")},
    "accs": {"femn": ("ую", "юю"), "neut": ("ое", "ее")},   # masc, plur: by animacy
    "ablt": {"masc": ("ым", "им"), "femn": ("ой", "ей", "ою", "ею"), "neut": ("ым", "им"), "plur": ("ыми", "ими")},
    "loct": {"masc": ("ом", "ем"), "femn": ("ой", "ей"), "neut": ("ом", "ем"), "plur": ("ых", "их")},
}
PRONOUNS = {   # pronouns and determiners showing gender or number, any case
    "masc": set("он его него ему нему им ним нём этот тот весь сам один свой мой твой наш ваш чей".split()),
    "femn": set("она её ее неё нее ей ней ею нею эта эту этой та ту той вся всю всей сама саму самой одна одну "
                "одной своя свою своей моя мою моей твоя твою твоей наша нашу нашей ваша вашу вашей чья чью "
                "чьей".split()),
    "neut": set("оно его него ему нему им ним нём это то всё само одно своё моё твоё наше ваше чьё".split()),
    "plur": set("они их них им ним ими ними эти этих те тех все всех сами одни свои мои твои наши ваши "
                "чьи".split()),
}
PERSONAL = set("он она оно они его её ее него неё нее ему ей нему ней им ими ним ними нём ею нею их них".split())
KOTOR = {"masc": {"ый", "ого", "ому", "ым", "ом"}, "femn": {"ая", "ой", "ую", "ою"},
         "neut": {"ое", "ого", "ому", "ым", "ом"}, "plur": {"ые", "ых", "ым", "ыми"}}
PAST = {"masc": ("л", "лся"), "femn": ("ла", "лась"), "neut": ("ло", "лось"), "plur": ("ли", "лись")}
PRES = {   # the present tense shows the number only
    "sing": ("ет", "ит", "ёт"),
    "plur": ("ут", "ют", "ат", "ят"),
}
PRES_MIN = 4   # letters: «нет» ends like a present-tense singular
SHORT = {   # short participles
    "masc": ("ен", "ён", "ан", "ян", "ыт", "ит", "ут", "ят"),
    "femn": ("ена", "ёна", "ана", "яна", "ыта", "ита", "ута", "ята"),
    "neut": ("ено", "ёно", "ано", "яно", "ыто", "ито", "уто", "ято"),
    "plur": ("ены", "ёны", "аны", "яны", "ыты", "иты", "уты", "яты"),
}
SHORT_ADJ = {
    "masc": set("готов рад должен нужен полон велик мал жив виден".split()),
    "femn": set("готова рада должна нужна полна велика мала жива видна".split()),
    "neut": set("готово радо должно нужно полно велико мало живо видно".split()),
    "plur": set("готовы рады должны нужны полны велики малы живы видны".split()),
}
AGREEING = {"ADJF", "ADJS", "PRTF", "PRTS", "VERB", "NPRO"}
SENTENCE_END = re.compile(r"[.!?…¶]+")
WORDS = re.compile(r"[А-Яа-яЁё]+")


def _ends(w: str, endings) -> bool:
    return any(w.endswith(e) and len(w) > len(e) for e in endings)


def _adj(case: str, g: str, animacy: str) -> tuple:
    if case == "accs" and g in ("masc", "plur"):
        case = "gent" if animacy == "anim" else "nomn"
    return ADJ_ENDS[case].get(g, ())


def _number(a: str) -> str:
    """The number `a` names: the plural itself, else the singular."""
    return "plur" if a == "plur" else "sing"


def _bare(w: str) -> str:
    """The word without its reflexive postfix (ждётся -> ждёт)."""
    return w[:-2] if w.endswith(("ся", "сь")) else w


def _pres(w: str, number: str) -> bool:
    """Whether `w` ends like a present-tense verb of that number."""
    return len(w) >= PRES_MIN and any(w.endswith(e) for e in PRES[number])


def _agrees(w: str, case: str, a: str, b: str, anim_a: str, anim_b: str) -> bool:
    """Whether `w` shows value `a` and not `b` in agreement with a term."""
    if _ends(w, _adj(case, a, anim_a)) and not _ends(w, _adj(case, b, anim_b)):
        return True
    if a == b:
        return False
    if w in PRONOUNS[a] and w not in PRONOUNS[b]:
        return True
    if w.startswith("котор") and w[5:] in KOTOR[a] and w[5:] not in KOTOR[b]:
        return True
    if case != "nomn":
        return False
    bare = _bare(w)
    # a present-tense verb agrees in number only
    if _pres(bare, _number(a)) and not _pres(bare, _number(b)):
        return True
    return any(_ends(bare, t[a]) and not _ends(bare, t[b]) for t in (PAST, SHORT)) or \
        (w in SHORT_ADJ[a] and w not in SHORT_ADJ[b])


def _confirmed(w: str, a: str, morph) -> bool:
    """Whether a parse of `w` shows gender or number."""
    return any((p.tag.POS in AGREEING or "Apro" in p.tag)
               and (a in p.tag or ("pres" in p.tag and _number(a) in p.tag)) for p in morph.parse(w))


def agreement(text: str, term: Term, old: dict, new: dict, morph=None, english: str | None = None,
              senses: Senses | None = None, include: bool = False) -> list[dict]:
    """The occurrences of `term` in `text` whose agreement may change."""
    marked = text.replace("\\p", "¶")
    plain = strip_codes(marked)
    hits = find_forms(marked, term)
    if english is not None:   # the renamed occurrences only
        renamed = (senses or Senses([term])).uses(marked, term, english)
        hits = [(k, m, p) for k, m, p, kind in renamed if kind == USE or (kind == FORM_ONLY and include)]
    spans = [(pos, pos + len(match)) for _, match, pos in hits]
    bounds = sorted({0, len(plain)} | {m.end() for m in SENTENCE_END.finditer(plain)})
    anim = bool(old["animacy"] and new["animacy"] and old["animacy"] != new["animacy"])
    out = []
    for key, match, pos in hits:
        case, _, num = key.partition(".")
        if case not in ADJ_ENDS:   # the label: a nominative
            case, num = "nomn", "sing"
        a = "plur" if "plur" in (num, old["number"]) else old["gender"]
        b = "plur" if "plur" in (num, new["number"]) else new["gender"]
        if not a or not b or (a == b and not anim):
            continue
        start = max(x for x in bounds if x <= pos)
        end = min((x for x in bounds if x >= pos + len(match)), default=len(plain))
        after = min((x for x in bounds if x > end), default=len(plain))
        words = []
        for m in WORDS.finditer(plain, start, after):
            if any(s < m.end() and m.start() < e for s, e in spans):
                continue
            w = m.group().lower()
            if m.start() >= end:   # the next sentence: personal pronouns
                hit = a != b and w in PERSONAL and w in PRONOUNS[a] and w not in PRONOUNS[b]
            else:
                hit = _agrees(w, case, a, b, old["animacy"], new["animacy"])
            if hit and (morph is None or _confirmed(w, a, morph)) and m.group() not in words:
                words.append(m.group())
        if words:
            out.append({"term": term.id, "form": key, "match": match, "words": words})
    return out
