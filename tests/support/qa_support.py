from gen3tl.lang.langpack import LangPack

PACK = LangPack.load("ru", "unbound")

def entry(sid, text, flags=()):
    return {"id": sid, "offset": int(sid[1:], 16), "length": len(text) + 1, "kind": "pointer",
            "category": "script", "mode": "field", "text": text, "table": None, "index": None,
            "refs": [{"at": 0x10, "kind": "script:loadpointer"}], "flags": list(flags)}

def scripted(sid, text, kind="script:message"):
    e = entry(sid, text)
    e["refs"] = [{"at": 0x10, "kind": kind}]
    return e
