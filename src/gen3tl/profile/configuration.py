"""Authored profile keys excluded from ROM discovery coverage."""

KEYS = {
    "rom": {
        "discovery_shortfall_evidence": "Verification ledger naming missing witnesses; no additional ROM value.",
    },
    "keyboard": {
        "labels": "Selected catalog identities for page labels; ROM bytes do not encode catalog keys.",
        "row_bindings": "Selected language-pack row names mapped to catalog identities.",
        "chat": "Selected presentation grid and style for language-pack chat pages.",
    },
    "keyboard.table": {
        "index": "Selected per-page extraction order for the patch builder, not a stored table field.",
    },
}


def value(identifier, section, item):
    """Remove only explicitly declared configuration, retaining every ROM key."""
    for parent, keys in KEYS.items():
        for key in keys:
            path = parent + "." + key
            if identifier == path or identifier.startswith(path + "."):
                return None
    if isinstance(item, dict):
        return {key: child for key, child in item.items() if key not in KEYS.get(section, {})}
    return item
