"""Gen 3 text codec in decomp notation (pokefirered/pokeemerald names)."""

from __future__ import annotations

import re

EOS = 0xFF
NEWLINE = 0xFE
PLACEHOLDER = 0xFD
CTRL = 0xFC
PARAGRAPH = 0xFB
SCROLL = 0xFA

# byte -> character, Western Gen 3 table
BASE_CHARS: dict[int, str] = {
    0x00: " ",
    0x01: "À", 0x02: "Á", 0x03: "Â", 0x04: "Ç", 0x05: "È", 0x06: "É", 0x07: "Ê",
    0x08: "Ë", 0x09: "Ì", 0x0B: "Î", 0x0C: "Ï", 0x0D: "Ò", 0x0E: "Ó", 0x0F: "Ô",
    0x10: "Œ", 0x11: "Ù", 0x12: "Ú", 0x13: "Û", 0x14: "Ñ", 0x15: "ß", 0x16: "à",
    0x17: "á", 0x19: "ç", 0x1A: "è", 0x1B: "é", 0x1C: "ê", 0x1D: "ë", 0x1E: "ì",
    0x20: "î", 0x21: "ï", 0x22: "ò", 0x23: "ó", 0x24: "ô", 0x25: "œ", 0x26: "ù",
    0x27: "ú", 0x28: "û", 0x29: "ñ", 0x2A: "º", 0x2B: "ª", 0x2D: "&", 0x2E: "+",
    0x35: "=", 0x36: ";", 0x51: "¿", 0x52: "¡", 0x5A: "Í", 0x5B: "%", 0x5C: "(",
    0x5D: ")", 0x68: "â", 0x6F: "í", 0x85: "<", 0x86: ">",
    0xAB: "!", 0xAC: "?", 0xAD: ".", 0xAE: "-", 0xAF: "·", 0xB0: "…", 0xB1: "“",
    0xB2: "”", 0xB3: "‘", 0xB4: "’", 0xB5: "♂", 0xB6: "♀", 0xB7: "¥", 0xB8: ",",
    0xB9: "×", 0xBA: "/", 0xEF: "▶", 0xF0: ":", 0xF1: "Ä", 0xF2: "Ö", 0xF3: "Ü",
    0xF4: "ä", 0xF5: "ö", 0xF6: "ü",
}
for _i in range(10):
    BASE_CHARS[0xA1 + _i] = chr(ord("0") + _i)
for _i in range(26):
    BASE_CHARS[0xBB + _i] = chr(ord("A") + _i)
    BASE_CHARS[0xD5 + _i] = chr(ord("a") + _i)

# byte -> token name for single-byte symbols without a Unicode character
NAMED_BYTES: dict[int, str] = {
    0x2C: "SUPER_ER", 0x34: "LV", 0x53: "PK", 0x54: "MN",
    0x55: "POKEBLOCK_PO", 0x56: "POKEBLOCK_KE", 0x57: "POKEBLOCK_BL",
    0x58: "POKEBLOCK_OC", 0x59: "POKEBLOCK_K",
    0x79: "UP_ARROW", 0x7A: "DOWN_ARROW", 0x7B: "LEFT_ARROW", 0x7C: "RIGHT_ARROW",
    0x84: "SUPER_E", 0xA0: "SUPER_RE",
}

# 0xFC control codes: code -> (name, argument byte count)
CTRL_CODES: dict[int, tuple[str, int]] = {
    0x00: ("CTRL_00", 0),
    0x01: ("COLOR", 1),
    0x02: ("HIGHLIGHT", 1),
    0x03: ("SHADOW", 1),
    0x04: ("COLOR_HIGHLIGHT_SHADOW", 3),
    0x05: ("PALETTE", 1),
    0x06: ("FONT", 1),
    0x07: ("RESET_FONT", 0),
    0x08: ("PAUSE", 1),
    0x09: ("PAUSE_UNTIL_PRESS", 0),
    0x0A: ("WAIT_SE", 0),
    0x0B: ("PLAY_BGM", 2),
    0x0C: ("ESCAPE", 1),
    0x0D: ("SHIFT_RIGHT", 1),
    0x0E: ("SHIFT_DOWN", 1),
    0x0F: ("FILL_WINDOW", 0),
    0x10: ("PLAY_SE", 2),
    0x11: ("CLEAR", 1),
    0x12: ("SKIP", 1),
    0x13: ("CLEAR_TO", 1),
    0x14: ("MIN_LETTER_SPACING", 1),
    0x15: ("JPN", 0),
    0x16: ("ENG", 0),
    0x17: ("PAUSE_MUSIC", 0),
    0x18: ("RESUME_MUSIC", 0),
}

# 0xFD placeholders in overworld text
FIELD_PLACEHOLDERS: dict[int, str] = {
    0x01: "PLAYER", 0x02: "STR_VAR_1", 0x03: "STR_VAR_2", 0x04: "STR_VAR_3",
    0x05: "KUN", 0x06: "RIVAL",
}

# 0xFD placeholders in battle messages (B_TXT_* in the decompilations)
BATTLE_PLACEHOLDERS: dict[int, str] = {
    0x00: "B_BUFF1", 0x01: "B_BUFF2", 0x02: "B_COPY_VAR_1", 0x03: "B_COPY_VAR_2",
    0x04: "B_COPY_VAR_3", 0x05: "B_PLAYER_MON1_NAME", 0x06: "B_OPPONENT_MON1_NAME",
    0x07: "B_PLAYER_MON2_NAME", 0x08: "B_OPPONENT_MON2_NAME",
    0x09: "B_LINK_PLAYER_MON1_NAME", 0x0A: "B_LINK_OPPONENT_MON1_NAME",
    0x0B: "B_LINK_PLAYER_MON2_NAME", 0x0C: "B_LINK_OPPONENT_MON2_NAME",
    0x0D: "B_ATK_NAME_WITH_PREFIX_MON1", 0x0E: "B_ATK_PARTNER_NAME",
    0x0F: "B_ATK_NAME_WITH_PREFIX", 0x10: "B_DEF_NAME_WITH_PREFIX",
    0x11: "B_EFF_NAME_WITH_PREFIX", 0x12: "B_ACTIVE_NAME_WITH_PREFIX",
    0x13: "B_SCR_ACTIVE_NAME_WITH_PREFIX", 0x14: "B_CURRENT_MOVE",
    0x15: "B_LAST_MOVE", 0x16: "B_LAST_ITEM", 0x17: "B_LAST_ABILITY",
    0x18: "B_ATK_ABILITY", 0x19: "B_DEF_ABILITY", 0x1A: "B_SCR_ACTIVE_ABILITY",
    0x1B: "B_EFF_ABILITY", 0x1C: "B_TRAINER1_CLASS", 0x1D: "B_TRAINER1_NAME",
    0x1E: "B_LINK_PLAYER_NAME", 0x1F: "B_LINK_PARTNER_NAME",
    0x20: "B_LINK_OPPONENT1_NAME", 0x21: "B_LINK_OPPONENT2_NAME",
    0x22: "B_LINK_SCR_TRAINER_NAME", 0x23: "B_PLAYER_NAME",
    0x24: "B_TRAINER1_LOSE_TEXT", 0x25: "B_TRAINER1_WIN_TEXT", 0x26: "B_26",
    0x27: "B_PC_CREATOR_NAME", 0x28: "B_ATK_PREFIX1", 0x29: "B_DEF_PREFIX1",
    0x2A: "B_ATK_PREFIX2", 0x2B: "B_DEF_PREFIX2", 0x2C: "B_ATK_PREFIX3",
    0x2D: "B_DEF_PREFIX3",
}

PREFIXED_ARG = {0xF7, 0xF8, 0xF9}  # one argument byte each

MODES = {"field": FIELD_PLACEHOLDERS, "battle": BATTLE_PLACEHOLDERS}

_HEX2 = re.compile(r"^[0-9A-F]{2}$")


# the encoder's errors: one template per code
ERRORS = {
    "unknown_escape": "unknown escape {escape} at {at} in {text}",
    "unclosed_brace": "unclosed brace at {at} in {text}",
    "unmapped_character": "character {char} (U+{unicode}) has no byte, in {text}",
    "empty_token": "empty token in {text}",
    "token_arguments": "{token} needs {n} hex argument(s), in {text}",
    "unknown_token": "unknown token {token} in {text}",
}
FIELD = re.compile(r"\{(\w+)\}")


class CharmapError(ValueError):
    """A codec error; `coded` is its code with its arguments, where ERRORS has one."""

    def __init__(self, message: str, coded: dict | None = None):
        super().__init__(message)
        self.coded = coded


def _error(name: str, **args) -> CharmapError:
    """The encoder error `name` with its arguments, in the order of its template."""
    template = ERRORS[name]
    return CharmapError(FIELD.sub(lambda m: str(args[m[1]]), template),
                        {"code": name, **{field: args[field] for field in FIELD.findall(template)}})


class Charmap:
    """Encoder/decoder for one character set (base table plus a language pack)."""

    def __init__(self, extra_chars: dict[str, int] | None = None):
        self.decode_table = dict(BASE_CHARS)
        self.encode_table: dict[str, int] = {c: b for b, c in BASE_CHARS.items()}
        if extra_chars:
            for ch, b in extra_chars.items():
                if len(ch) != 1:
                    raise CharmapError(f"language pack entry {ch!r} is not one character")
                # one character per byte
                if ch in self.encode_table or b in self.decode_table or b in NAMED_BYTES or b >= min(PREFIXED_ARG):
                    raise CharmapError(f"language pack entry {ch!r} on byte {b:02X}, which another character reads")
                self.encode_table[ch] = b
                self.decode_table[b] = ch
        self.named_to_byte = {n: b for b, n in NAMED_BYTES.items()}
        self.ctrl_by_name = {n: (c, k) for c, (n, k) in CTRL_CODES.items()}
        self.placeholder_by_name: dict[str, int] = {}
        for table in MODES.values():
            for b, n in table.items():
                self.placeholder_by_name[n] = b

    def decode(self, data: bytes | bytearray, start: int = 0, mode: str = "field",
               limit: int | None = None) -> tuple[str, int]:
        """Decode from `start` up to the 0xFF terminator."""
        names = MODES[mode]
        out: list[str] = []
        i = start
        end = len(data) if limit is None else min(len(data), start + limit)
        while i < end:
            b = data[i]
            if b == EOS:
                return "".join(out), i + 1
            if b == NEWLINE:
                out.append("\\n"); i += 1; continue
            if b == SCROLL:
                out.append("\\l"); i += 1; continue
            if b == PARAGRAPH:
                out.append("\\p"); i += 1; continue
            if b == CTRL:
                if i + 1 >= end:
                    break
                code = data[i + 1]
                if code in CTRL_CODES:
                    name, nargs = CTRL_CODES[code]
                    args = data[i + 2:i + 2 + nargs]
                    if len(args) < nargs:
                        break
                    out.append("{" + " ".join([name] + [f"{a:02X}" for a in args]) + "}")
                    i += 2 + nargs
                else:
                    out.append(f"{{FC {code:02X}}}")
                    i += 2
                continue
            if b == PLACEHOLDER:
                if i + 1 >= end:
                    break
                arg = data[i + 1]
                out.append("{" + names[arg] + "}" if arg in names else f"{{FD {arg:02X}}}")
                i += 2
                continue
            if b in PREFIXED_ARG:
                if i + 1 >= end:
                    break
                out.append(f"{{{b:02X} {data[i + 1]:02X}}}")
                i += 2
                continue
            if b in self.decode_table:
                out.append(self.decode_table[b])
            elif b in NAMED_BYTES:
                out.append("{" + NAMED_BYTES[b] + "}")
            else:
                out.append(f"{{{b:02X}}}")
            i += 1
        raise CharmapError(f"no terminator after {start:#x}")

    def encode(self, text: str, terminate: bool = True) -> bytes:
        out = bytearray()
        i = 0
        n = len(text)
        while i < n:
            c = text[i]
            if c == "\\":
                nxt = text[i + 1:i + 2]
                if nxt == "n":
                    out.append(NEWLINE)
                elif nxt == "l":
                    out.append(SCROLL)
                elif nxt == "p":
                    out.append(PARAGRAPH)
                else:
                    raise _error("unknown_escape", escape=f"\\{nxt}", at=i, text=repr(text))
                i += 2
                continue
            if c == "{":
                j = text.find("}", i)
                if j < 0:
                    raise _error("unclosed_brace", at=i, text=repr(text))
                out += self._encode_token(text[i + 1:j], text)
                i = j + 1
                continue
            b = self.encode_table.get(c)
            if b is None:
                raise _error("unmapped_character", char=repr(c), unicode=f"{ord(c):04X}", text=repr(text))
            out.append(b)
            i += 1
        if terminate:
            out.append(EOS)
        return bytes(out)

    def _encode_token(self, tok: str, text: str) -> bytes:
        parts = tok.split()
        if not parts:
            raise _error("empty_token", text=repr(text))
        head, args = parts[0], parts[1:]
        if len(parts) == 1 and _HEX2.match(head):
            return bytes([int(head, 16)])
        if head in ("FC", "FD", "F7", "F8", "F9") and all(_HEX2.match(a) for a in args):
            return bytes([int(head, 16)] + [int(a, 16) for a in args])
        if head in self.ctrl_by_name:
            code, nargs = self.ctrl_by_name[head]
            if len(args) != nargs or not all(_HEX2.match(a) for a in args):
                raise _error("token_arguments", token=f"{{{tok}}}", n=nargs, text=repr(text))
            return bytes([CTRL, code] + [int(a, 16) for a in args])
        if not args and head in self.placeholder_by_name:
            return bytes([PLACEHOLDER, self.placeholder_by_name[head]])
        if not args and head in self.named_to_byte:
            return bytes([self.named_to_byte[head]])
        raise _error("unknown_token", token=f"{{{tok}}}", text=repr(text))

    def can_encode(self, text: str) -> bool:
        try:
            self.encode(text)
            return True
        except CharmapError:
            return False
