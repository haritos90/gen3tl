"""Inflection provider for this language pack."""

from __future__ import annotations

import re
from gen3tl.lang.termbase import Term, CASES, NUMBERS, WORD

_MORPH = None


def morph():
    global _MORPH
    if _MORPH is None:
        try:
            import pymorphy3
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("pymorphy3 is required: run with .venv/bin/python "
                               "(pip install -r requirements.txt)") from e
        _MORPH = pymorphy3.MorphAnalyzer()
    return _MORPH


def _match_case(src: str, word: str) -> str:
    """Copy capitalisation from src to word, per hyphen-separated part."""
    sp, wp = src.split("-"), word.split("-")
    if len(sp) != len(wp):
        sp, wp = [src], [word]
    out = []
    for a, b in zip(sp, wp):
        if a.isupper() and len(a) > 1:
            out.append(b.upper())
        elif a[:1].isupper():
            out.append(b[:1].upper() + b[1:])
        else:
            out.append(b)
    return "-".join(out)


ANIMATE_CATEGORIES = {"species", "character"}            # names of living beings: animate
INANIMATE_CATEGORIES = {"move", "item", "ability", "type", "nature", "location"}  # names of objects
# other categories (term, proper-noun, trainer-class, speaker) use dictionary animacy
SINGULAR_ONLY = {"character", "location", "proper-noun", "speaker"}
NAME_CATEGORIES = {"species", "character", "speaker"}
# first parts of hyphenated names that stay invariant (attributive elements)
ATTRIBUTIVE = {"чудо", "вольт", "фокус", "шок", "блиц", "психо", "магма", "солнце", "лазер", "мат", "магнит",
               "тело", "динамакс", "сеть", "гнездо", "приманка", "дружба", "кэмп", "прыг", "свист", "драко",
               "гига", "мега", "ультра", "супер", "гипер", "макс", "ки", "z", "g", "м", "топ", "панк", "дельта",
               "икс", "зум", "мах", "турбо", "спринт", "электро", "аква", "пиро", "крио", "гео", "аэро", "био", "нано"}
# loanword prefixes written as a separate word
SEPARATE_PREFIXES = {"вольт", "спринт", "турбо", "мега", "супер", "гипер", "ультра", "макс", "топ", "панк", "блиц",
                     "шок", "мах", "зум", "дельта", "икс", "гига", "кэмп", "прыг", "свист"}
# first parts that decline with the last part
APPOSITIONAL = {"рука", "яйцо", "клык", "девочка", "мальчик", "пушка", "монета", "юноша", "чувак", "рог", "агент",
                "кулак", "нога", "голова", "птица", "женщина", "мужчина", "девушка", "парень"}
VOWEL_END = "оеёиуюэы"
# nouns whose place-name locative is the second locative
LOC2_NOUNS = {"лес", "порт", "сад", "луг", "берег", "мост", "пруд", "рай", "край", "бор", "мыс", "лёд", "снег",
              "ров", "шкаф", "угол", "бок", "дым", "пол", "плот", "холм"}
SIBILANT = "жшчщц"
VELAR_SIB = "гкхжшчщ"
CYR = re.compile(r"[А-Яа-яЁё]")


def _name_forms(word: str, gender: str, animate: bool) -> dict[str, str] | None:
    """Rule-based singular paradigm of a transliterated name."""
    m = re.match(r"^(.*?[А-Яа-яЁё])([^А-Яа-яЁё]*)$", word)
    if not m:
        return None
    core, suffix = m.group(1), m.group(2)
    if "-" in core:  # hyphenated name: every declinable capitalised part declines (Майма-Джуниора, Хо-Оха)
        parts = core.split("-")
        pf = [(_name_forms(x, gender, animate) if x[:1].isupper() or i == len(parts) - 1 else None)
              for i, x in enumerate(parts)]
        if all(x is None for x in pf):
            return None
        return {c: "-".join(pf[i][c] if pf[i] else parts[i] for i in range(len(parts))) + suffix for c in CASES}
    low = core.lower()
    last = low[-1]
    if low.endswith(("ие", "ье")) and len(low) > 4:  # neuter: Перерубание
        stem = core[:-1]
        f = {"nomn": core, "gent": stem + "я", "datv": stem + "ю", "accs": core, "ablt": stem + "ем",
             "loct": stem + ("и" if low.endswith("ие") else "е")}
        return {k: v + suffix for k, v in f.items()}
    if last in VOWEL_END:
        return None
    stem = core[:-1]
    if low.endswith(("ость", "есть")):  # feminine third declension: Несгибаемость
        f = {"nomn": core, "gent": stem + "и", "datv": stem + "и", "accs": core, "ablt": core + "ю", "loct": stem + "и"}
        return {k: v + suffix for k, v in f.items()}
    if low.endswith("ец") and len(low) > 4 and gender != "female":  # fleeting е: Внедренец
        st = core[:-2] + "ц"
        f = {"nomn": core, "gent": st + "а", "datv": st + "у", "accs": st + "а" if animate else core,
             "ablt": st + "ем", "loct": st + "е"}
        return {k: v + suffix for k, v in f.items()}
    if last == "а":
        gen = stem + ("и" if stem[-1:].lower() in VELAR_SIB else "ы")
        ins = stem + ("ей" if stem[-1:].lower() in SIBILANT else "ой")
        f = {"nomn": core, "gent": gen, "datv": stem + "е", "accs": stem + "у", "ablt": ins, "loct": stem + "е"}
    elif last == "я":
        if low.endswith("ия"):
            f = {"nomn": core, "gent": stem + "и", "datv": stem + "и", "accs": stem + "ю", "ablt": stem + "ей", "loct": stem + "и"}
        else:
            f = {"nomn": core, "gent": stem + "и", "datv": stem + "е", "accs": stem + "ю", "ablt": stem + "ей", "loct": stem + "е"}
    elif gender == "female":
        return None  # feminine names ending in a consonant do not decline
    elif last == "й":
        loc = stem + ("и" if low.endswith("ий") else "е")
        f = {"nomn": core, "gent": stem + "я", "datv": stem + "ю", "accs": stem + "я" if animate else core,
             "ablt": stem + "ем", "loct": loc}
    elif last == "ь":
        f = {"nomn": core, "gent": stem + "я", "datv": stem + "ю", "accs": stem + "я" if animate else core,
             "ablt": stem + "ем", "loct": stem + "е"}
    else:
        ins = core + ("ем" if last in SIBILANT else "ом")
        f = {"nomn": core, "gent": core + "а", "datv": core + "у", "accs": core + "а" if animate else core,
             "ablt": ins, "loct": core + "е"}
    return {k: v + suffix for k, v in f.items()}


def _best_parse(m, word: str):
    ps = m.parse(word)
    nom = [p for p in ps if "nomn" in p.tag and p.tag.POS in ("NOUN", "ADJF", "PRTF")]
    low = word.lower()
    if low[-2:] in ("ая", "ое", "ые", "ый", "ой", "ий", "ее", "яя", "ие"):
        order = ("ий", "ой", "ый") if low[-2:] in ("ее", "яя", "ие", "ий") else ("ой", "ый", "ий")
        rank = {low[:-2] + e: i for i, e in enumerate(order)}
        own = sorted((p for p in nom if p.tag.POS in ("ADJF", "PRTF") and p.normal_form in rank),
                     key=lambda p: rank[p.normal_form])
        if own:
            nom = own + [p for p in nom if p not in own]
    return (nom or ps)[0], nom


ADJ_ENDINGS = {  # hard / soft-or-velar stems: case -> (masc, femn, neut, plur)
    "hard": {"nomn": ("ый", "ая", "ое", "ые"), "gent": ("ого", "ой", "ого", "ых"), "datv": ("ому", "ой", "ому", "ым"),
             "ablt": ("ым", "ой", "ым", "ыми"), "loct": ("ом", "ой", "ом", "ых")},
    "soft": {"nomn": ("ий", "яя", "ее", "ие"), "gent": ("его", "ей", "его", "их"), "datv": ("ему", "ей", "ему", "им"),
             "ablt": ("им", "ей", "им", "ими"), "loct": ("ем", "ей", "ем", "их")},
    "sib": {"nomn": ("ий", "ая", "ее", "ие"), "gent": ("его", "ей", "его", "их"), "datv": ("ему", "ей", "ему", "им"),
            "ablt": ("им", "ей", "им", "ими"), "loct": ("ем", "ей", "ем", "их")},
}


def _adj_rule(word: str, tags: set) -> str | None:
    """Rule-based adjective inflection by ending (fallback when the dictionary slips)."""
    tags = {str(t) for t in tags}
    low = word.lower()
    end = low[-2:]
    idx = {"ый": 0, "ой": 0, "ий": 0, "ая": 1, "яя": 1, "ое": 2, "ее": 2, "ые": 3, "ие": 3}.get(end)
    if idx is None:
        return None
    stem = word[:-2]
    if stem[-1:].lower() in "жшчщ":
        kind = "sib"
    elif end in ("яя",) or (end in ("ий", "ее", "ие") and stem[-1:].lower() not in "гкх"):
        kind = "soft"
    else:
        kind = "hard"
    if kind == "hard" and stem[-1:].lower() in "гкх":
        kind = "hard"
    num = 3 if "plur" in tags else ({"masc": 0, "femn": 1, "neut": 2}.get(next((t for t in tags if t in ("masc", "femn", "neut")), ""), idx if idx < 3 else 0))
    case = next((t for t in tags if t in ("nomn", "gent", "datv", "accs", "ablt", "loct")), "nomn")
    if case == "accs":
        if num == 1:
            ending = "ую" if kind == "hard" or kind == "sib" else "юю"
            return stem + ending
        case = "gent" if "anim" in tags and num in (0, 3) else "nomn"
    e = ADJ_ENDINGS[kind][case][num]
    if kind == "hard" and stem[-1:].lower() in "гкх" and e.startswith("ы"):
        e = "и" + e[1:]
    return stem + e


def _inflect_word(m, word: str, tags: set, as_noun: bool):
    """Inflect a dictionary word; hyphenated words decline the last part unless both are nouns."""
    if "-" in word:
        first, _, last = word.rpartition("-")
        if not CYR.search(last):  # Latin or digit suffix: decline the first part (атаки-Z)
            f_first = _inflect_word(m, first, tags, as_noun)
            return None if f_first is None else f"{f_first}-{last}"
        f_last = _inflect_word(m, last, tags, as_noun)
        if f_last is None:
            return None
        if first.lower() in ATTRIBUTIVE or not CYR.search(first) or last.lower().startswith("ягод"):
            return f"{first}-{f_last}"
        p_first = [p for p in m.parse(first) if p.tag.POS == "NOUN" and "nomn" in p.tag and p.is_known]
        if p_first:
            f1 = p_first[0].inflect(tags)
            return f"{_match_case(first, f1.word) if f1 else first}-{f_last}"
        return f"{first}-{f_last}"
    p, nom = _best_parse(m, word)
    cand = [x for x in nom if (x.tag.POS == "NOUN") == as_noun] or nom or [p]
    for x in cand:
        f = x.inflect(tags)
        if f is None:
            continue
        if x.tag.POS in ("ADJF", "PRTF") and len(word) > 4:
            a, b = word.lower().replace("ё", "е"), f.word.replace("ё", "е")
            if a.endswith("ся") and b.endswith("ся"):
                a, b = a[:-2], b[:-2]
            if a[:len(a) - 2] != b[:len(a) - 2]:
                continue  # lemma slip (высший → высочайший): another parse
        return _match_case(word, f.word)
    if not as_noun:
        r = _adj_rule(word, tags)
        if r is not None:
            return _match_case(word, r)
    return None


def inflect_phrase(phrase: str, case: str, number: str, animate: bool = False,
                   names_after_head: bool = False, gender: str = "", descriptor_head: bool = False,
                   prefer_rules: bool = False, default_animate: bool = False, loc2: bool = False) -> str | None:
    """Inflect a noun phrase; None when it has no declinable head."""
    m = morph()
    if " и " in phrase:
        parts = [inflect_phrase(x, case, number, animate, names_after_head, gender, descriptor_head, prefer_rules,
                                default_animate, loc2) for x in phrase.split(" и ")]
        return None if any(x is None for x in parts) else " и ".join(parts)
    words = phrase.split(" ")
    info = []
    for w in words:
        core = w.strip("“”‘’\"'!?,:")
        if not core or core.endswith(".") or not CYR.search(core) or not re.fullmatch(r"[А-Яа-яЁё\-]+", core):
            info.append(None)
            continue
        p, nom = _best_parse(m, core)
        info.append((core, p, nom))
    head = None
    first_i = next((i for i, x in enumerate(info) if x is not None), None)
    if first_i is not None and info[first_i][1].tag.POS == "PREP":
        return None
    for i, x in enumerate(info):
        if x is not None and x[0].lower() in SEPARATE_PREFIXES and any(y is not None for y in info[i + 1:]):
            info[i] = None  # a separate-word prefix (Вольт хлыст) stays invariant
    if descriptor_head and first_i is not None and info[first_i][0][:1].islower():
        head = first_i
    for i, x in enumerate(info):
        if head is not None:
            break
        if x is None:
            continue
        core, p, nom = x
        nouns = [q for q in nom if q.tag.POS == "NOUN"]
        adjs = [q for q in nom if q.tag.POS in ("ADJF", "PRTF")]
        nxt = next((y for y in info[i + 1:] if y is not None), None)
        if adjs and nxt is not None and any(q.tag.POS == "NOUN" for q in nxt[2]):
            continue  # adjective before a noun
        if nouns:
            head = i
            break
    if head is None:
        first = next((i for i, x in enumerate(info) if x is not None), None)
        if first is None:
            return None
        core, p, nom = info[first]
        if p.tag.POS in ("VERB", "INFN", "ADVB", "PREP", "CONJ", "PRCL", "GRND", "PRED", "INTJ", "NUMR"):
            return None
        if all(x is None or any(q.tag.POS in ("ADJF", "PRTF") for q in x[2]) for x in info) and any(info):
            # adjective-only name: masculine paradigm (agrees with характер / тип / талант)
            out = list(words)
            for i, x in enumerate(info):
                if x is None:
                    continue
                own_gender = str(x[1].tag.gender or "masc")
                tags = {case, number} | ({own_gender} if number == "sing" else set())
                if case == "accs" and (own_gender == "masc" or number == "plur"):
                    tags.add("anim" if (animate if animate is not None else default_animate) else "inan")
                f = _inflect_word(m, x[0], tags, as_noun=False)
                if f is None:
                    return None
                out[i] = words[i].replace(x[0], f)
            return " ".join(out)
        if p.is_known:
            return None
        head = first  # unknown first word: a transliterated noun
    core, hp, _ = info[head]
    if number == "sing" and hp.tag.number == "plur" and "nomn" in hp.tag and "Sgtm" not in hp.tag:
        number = "plur"  # a name that is plural in itself keeps its number
    head_tags = {case, number}
    known = hp.is_known or ("-" in core and all(CYR.search(x) is None or _best_parse(m, x)[0].is_known
                                                   for x in core.split("-")[1:]))
    if "-" in core and CYR.search(core.split("-")[-1] or "") is None:
        known = _best_parse(m, core.split("-")[0])[0].is_known
    if known and not hp.is_known:
        hp_last = _best_parse(m, core.split("-")[-1] if CYR.search(core.split("-")[-1]) else core.split("-")[0])[0]
        hp = hp_last
    if animate is None:  # dictionary animacy (terms, titles, classes), from the last hyphen part
        animate = "anim" in hp.tag if hp.is_known else default_animate
    anim = "anim" if animate else "inan"
    if case == "loct" and loc2 and known and hp.normal_form in LOC2_NOUNS:  # в лесу, на лугу
        alt = _inflect_word(m, core, {"loc2", number}, as_noun=True)
        if alt is not None and alt.lower() != core.lower():
            head_tags = {"loc2", number}
    g = "female" if gender == "female" else "male"
    hf = None
    if known or not prefer_rules:
        # dictionary word, or a common-noun compound pymorphy declines by analogy
        hf = _inflect_word(m, core, head_tags, as_noun=True)
        if not known and hf is not None and number == "sing" and case != "nomn" and hf == core \
                and core[-1].lower() not in VOWEL_END and _name_forms(core, g, animate):
            hf = None  # no paradigm for a consonant-final word: use the rules
    if hf is None and number == "sing":
        nf = _name_forms(core, g, animate)
        hf = nf[case] if nf else core
    if hf is None:
        return None
    second_decl = hp.tag.gender == "masc" and core.split("-")[-1][-1:].lower() not in "аяоеё"
    forced = hp.is_known and (("anim" in hp.tag) != bool(animate))
    if case == "accs" and (second_decl or number == "plur") and (forced or not hp.is_known) \
            and (known or not prefer_rules):
        alt = _inflect_word(m, core, {"gent" if animate else "nomn", number}, as_noun=True)
        if alt is not None:
            hf = alt
    out = list(words)
    out[head] = words[head].replace(core, hf)
    gen = str(hp.tag.gender or "masc")
    for i in range(head):
        x = info[i]
        if x is None:
            continue
        tags = {case, number}
        if number == "sing":
            tags.add(gen)
        if case == "accs" and (number == "plur" or gen == "masc"):
            tags.add(anim)
        f = _inflect_word(m, x[0], tags, as_noun=False)
        if f is not None:
            out[i] = words[i].replace(x[0], f)
    if names_after_head and number == "sing" and animate:
        for i in range(head + 1, len(words)):
            x = info[i]
            if x is None or not x[0][:1].isupper() or len(x[0]) < 2:
                break
            q = x[1]
            if q.is_known and "nomn" not in q.tag and not ({"Name", "Surn", "Patr"} & set(str(q.tag).replace(" ", ",").split(","))):
                break  # a genitive dependent (Агент Теней), not an apposed name
            if q.is_known and "gent" in q.tag and "Name" not in str(q.tag):
                break
            nf = _name_forms(x[0], gender, animate)
            if nf:
                out[i] = words[i].replace(x[0], nf[case])
    return " ".join(out)


def generate_forms(t: Term) -> dict[str, str]:
    if t.declension == "manual" and t.forms:
        return dict(t.forms)
    if t.declension == "fixed" or not CYR.search(t.translation):
        return {f"{c}.{n}": t.translation for c in CASES for n in NUMBERS}
    cat = t.category
    animate = True if cat in ANIMATE_CATEGORIES else (False if cat in INANIMATE_CATEGORIES else None)
    gender = (t.gender or "").lower()
    numbers = ["sing"] if cat in SINGULAR_ONLY else NUMBERS
    single_name = cat in NAME_CATEGORIES and " " not in t.translation
    forms: dict[str, str] = {}
    if single_name:
        p, _ = _best_parse(morph(), re.sub(r"[^А-Яа-яЁё\-]", "", t.translation) or t.translation)
        tags = set(str(p.tag).replace(" ", ",").split(","))
        common_noun = (p.is_known and p.tag.POS in ("NOUN", "ADJF") and "nomn" in p.tag and t.translation[:1].isupper()
                       and not tags & {"Name", "Surn", "Patr", "Geox", "Abbr", "Fixd", "Orgn", "Trad"}
                       and (p.tag.POS == "NOUN" or cat == "speaker"))
        if not common_noun:  # a transliterated name, even when a dictionary word
            nf = _name_forms(t.translation, "female" if gender == "female" else "male", animate is not False)
            if nf is None:
                return {f"{c}.{n}": t.translation for c in CASES for n in NUMBERS}
            for c in CASES:
                forms[f"{c}.sing"] = nf[c]
            return forms
    for n in numbers:
        for c in CASES:
            f = inflect_phrase(t.translation, c, n, animate=animate,
                               names_after_head=cat in ("character", "speaker", "proper-noun", "species"),
                               gender=gender, descriptor_head=cat in ("location", "proper-noun"),
                               prefer_rules=cat in NAME_CATEGORIES,
                               default_animate=cat in ("speaker", "trainer-class"),
                               loc2=cat in ("location", "proper-noun"))
            if f:
                forms[f"{c}.{n}"] = f
    if not forms:
        return {f"{c}.{n}": t.translation for c in CASES for n in NUMBERS}
    forms["nomn.sing"] = t.translation
    return forms




INDECLINABLE_END = re.compile(r"[уюиеоэ]$|[аяиуюоеэ]-[А-Яа-я]+$")

def declension_for(category: str, translated: str) -> str:
    if not re.search(r"[А-Яа-яЁё]", translated):
        return "fixed"
    if category in ("species", "character") and INDECLINABLE_END.search(translated.split()[-1]):
        return "fixed"
    return "auto"
