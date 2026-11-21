"""Type effectiveness as raw engine multipliers, and the colour of each type from its icon."""

from __future__ import annotations

from ..core.notes import has_prose

MULTIPLIERS = {0, 1, 5, 10, 20}


def check(block: dict | None) -> list[str]:
    """Required evidence for a complete square type chart."""
    if not block:
        return ["block"]
    return [key for key in ("base", "count", "evidence") if key not in block]


def build(block: dict, data: bytes) -> list[dict]:
    """Read attack-major rows, rejecting unknown values and short tables."""
    problems = check(block)
    if problems:
        raise ValueError("invalid type-effectiveness configuration: " + ", ".join(problems))
    base, count = int(block["base"]), int(block["count"])
    if not 0 < count <= 256 or base < 0 or base + count * count > len(data):
        raise ValueError("type-effectiveness table exceeds ROM")
    records = []
    for attack in range(count):
        for defense in range(count):
            multiplier = data[base + attack * count + defense]
            if multiplier not in MULTIPLIERS:
                raise ValueError(f"invalid multiplier {multiplier} for {attack} -> {defense}")
            records.append({"attack": attack, "defense": defense, "multiplier": multiplier})
    return records


ICON_KEYS = ("sheet", "tiles", "tiles_per_row", "palette", "table", "count")


def check_icons(block: dict | None) -> list[str]:
    """Problems that keep a [pokedex.type_icons] block from giving colours."""
    if not block:
        return ["block"]
    problems = [key for key in ICON_KEYS if not isinstance(block.get(key), int)]
    if not has_prose(block.get("evidence", "")):
        problems.append("evidence")
    return problems


def _colour(value: int) -> tuple[int, int, int]:
    r, g, b = value & 31, (value >> 5) & 31, (value >> 10) & 31
    return ((r << 3) | (r >> 2), (g << 3) | (g >> 2), (b << 3) | (b >> 2))


def colours(block: dict, data: bytes, type_count: int) -> list[dict]:
    """The icon offset and fill colour of every type that has a 32 x 12 icon."""
    problems = check_icons(block)
    if problems:
        raise ValueError("invalid type icon configuration: " + ", ".join(problems))
    sheet, tiles, per_row = int(block["sheet"]), int(block["tiles"]), int(block["tiles_per_row"])
    if sheet + tiles * 32 > len(data) or int(block["palette"]) + 32 > len(data):
        raise ValueError("type icons: sheet or palette exceeds ROM")
    palette = [_colour(int.from_bytes(data[int(block["palette"]) + 2 * i:int(block["palette"]) + 2 * i + 2], "little"))
               for i in range(16)]
    brightness = [sum(c) for c in palette]
    skip = {0, brightness.index(min(brightness[1:])), brightness.index(max(brightness[1:]))}

    def pixel(x: int, y: int) -> int:
        tile = (y // 8) * per_row + x // 8
        if tile >= tiles:
            raise ValueError("type icons: an icon lies outside the sheet")
        byte = data[sheet + tile * 32 + (y % 8) * 4 + (x % 8) // 2]
        return byte & 15 if x % 2 == 0 else byte >> 4

    out = []
    for type_id in range(type_count):
        icon = type_id + 1
        if icon >= int(block["count"]):
            continue
        at = int(block["table"]) + 4 * icon
        width, height, offset = data[at], data[at + 1], int.from_bytes(data[at + 2:at + 4], "little")
        if (width, height) != (32, 12):
            continue
        x0, y0 = (offset % per_row) * 8, (offset // per_row) * 8
        counts: dict[int, int] = {}
        for y in range(y0, y0 + height):
            for x in range(x0, x0 + width):
                index = pixel(x, y)
                if index not in skip:
                    counts[index] = counts.get(index, 0) + 1
        if not counts:
            continue
        total = sum(counts.values())
        colour = tuple((sum(palette[index][channel] * count for index, count in counts.items()) + total // 2)
                       // total for channel in range(3))
        out.append({"id": type_id, "icon": f"0x{offset:04x}", "colour": "#%02x%02x%02x" % colour})
    return out
