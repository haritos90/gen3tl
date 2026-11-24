"""Generate static species share pages and content-addressed preview images."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
import hashlib
import html
import json
from pathlib import Path
import re
import shutil
import struct
import zlib

from .. import TOOLKIT_ROOT
from ..data import pokedexdata, pokedexstore, pokedextexts
from ..lang import language as language_metadata
from ..core.lists import append_unique

ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
LANGUAGE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]+)?$")
CATALOG_ID = re.compile(r"^(s[0-9A-F]{7}|[a-z_]+:\d+)(@.+)?$")
COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")
WIDTH = 1200
HEIGHT = 630
FONT_PATH = TOOLKIT_ROOT / "web" / "fonts" / "share-bitmap.json"
SITE_CSS = TOOLKIT_ROOT / "web" / "site.css"
# The species card's stat order, labels and tiers.
STAT_ORDER = ("hp", "attack", "defense", "sp_attack", "sp_defense", "speed")
STAT_LABELS = {"hp": "HP", "attack": "Attack", "defense": "Defense",
               "sp_attack": "Sp. Attack", "sp_defense": "Sp. Defense", "speed": "Speed"}
STAT_MAX = 255
TIER_LABELS = ("worst", "bad", "average", "good", "best")


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def rgba(colour: str) -> tuple[int, int, int, int]:
    digits = colour[1:] if len(colour) == 7 else "".join(char * 2 for char in colour[1:])
    return tuple(int(digits[index:index + 2], 16) for index in (0, 2, 4)) + (255,)


def tier_colours(css: str):
    """Read the stat tier colours the species card uses."""
    found = dict(re.findall(r"--tier-(\d+):\s*(#[0-9a-fA-F]{6})\b", css))
    ink = re.search(r"\.dex-tier\s*\{[^}]*?(?<![\w-])color:\s*(#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?)\b", css)
    colours = [found.get(str(tier)) for tier in range(len(TIER_LABELS))]
    if None in colours or ink is None:
        raise ValueError("Stat tier colours are missing from site.css")
    return [rgba(colour) for colour in colours], rgba(ink.group(1))


def stat_ranks(records):
    """Sort the complete stats of numbered species, as the card ranks them."""
    ranks = {key: [] for key in (*STAT_ORDER, "total")}
    for record in records:
        stats = record.get("base_stats")
        if not record["dex"] > 0 or not stats or any(stats.get(key) is None for key in STAT_ORDER):
            continue
        for key in STAT_ORDER:
            ranks[key].append(stats[key])
        ranks["total"].append(sum(stats[key] for key in STAT_ORDER))
    return {key: sorted(values) for key, values in ranks.items()}


def tier_of(ordered, value):
    """Rank a value by its share below, counting ties halfway."""
    if not ordered:
        return None
    below = bisect_left(ordered, value)
    equal = bisect_right(ordered, value) - below
    return min(len(TIER_LABELS) - 1, int((below + equal / 2) / len(ordered) * len(TIER_LABELS)))


def fragment_of(text: str, rule: str) -> str | None:
    flat = re.sub(r"\\[nl]", " ", text.split("\\p")[0])
    bare = lambda word: re.sub(r"[!.,:;?]+$", "", word)
    code = re.compile(r"\{[^}]*\}")
    if rule == "^":
        words = code.sub(" ", flat).strip().split()
        return bare(words[0]) if words else None
    after = rule.endswith("+")
    name = rule[:-1] if after else rule
    pattern = rf"\{{{re.escape(name)}\}}\s*([^\s{{]+)" if after else rf"(\S+\s?)\{{{re.escape(name)}\}}"
    match = re.search(pattern, flat)
    if not match:
        return None
    return bare(match.group(1)) if after else match.group(0)


def game_label(text, variables=None):
    """Format mapped labels with the page's game placeholders."""
    values = iter(str(value) for value in (variables or {}).values())
    label = text.split("\\p")[0].replace("{PK}{MN}", "Pokémon")
    label = re.sub(r"\{(?:STR_VAR_\d|B_COPY_VAR_\d|B_BUFF\d|B_PLAYER_MON\d_NAME|B_[A-Z_]*NAME[A-Z_]*|F7 0\d)\}",
                   lambda match: next(values, ""), label)
    label = re.sub(r"\{[^}]*\}", "", label)
    label = re.sub(r"\\[nl]", " ", label)
    label = re.sub(r"\s+", " ", label).strip()
    label = re.sub(r"[:!]\s*$", "", label)
    return " ".join([label, *values]).strip()


def png_pixels(data: bytes) -> tuple[int, int, list[list[tuple[int, int, int, int]]]]:
    """Decode the non-interlaced PNG forms used by sprites and the bitmap font."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Invalid PNG signature")
    at = 8
    chunks = {}
    header = None
    while at + 12 <= len(data):
        size = int.from_bytes(data[at:at + 4], "big")
        kind = data[at + 4:at + 8]
        payload = data[at + 8:at + 8 + size]
        if at + 12 + size > len(data) or zlib.crc32(kind + payload) != int.from_bytes(data[at + 8 + size:at + 12 + size], "big"):
            raise ValueError("Invalid PNG chunk")
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", payload)
        elif kind in (b"PLTE", b"tRNS", b"IDAT", b"IEND"):
            chunks.setdefault(kind, []).append(payload)
        at += size + 12
        if kind == b"IEND":
            break
    if header is None:
        raise ValueError("PNG has no header")
    width, height, depth, colour, compression, filtering, interlace = header
    if compression or filtering or interlace or depth not in (1, 2, 4, 8):
        raise ValueError("Unsupported PNG encoding")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(colour)
    if channels is None or (colour != 3 and depth != 8):
        raise ValueError("Unsupported PNG colour format")
    stride = (width * channels * depth + 7) // 8
    packed = zlib.decompress(b"".join(chunks.get(b"IDAT", [])))
    if len(packed) != height * (stride + 1):
        raise ValueError("Invalid PNG pixel data")
    bpp = max(1, (channels * depth + 7) // 8)
    rows = []
    previous = bytearray(stride)
    at = 0
    for _ in range(height):
        method = packed[at]
        current = bytearray(packed[at + 1:at + 1 + stride])
        at += stride + 1
        for x in range(stride):
            left = current[x - bpp] if x >= bpp else 0
            above = previous[x]
            diagonal = previous[x - bpp] if x >= bpp else 0
            if method == 1:
                current[x] = (current[x] + left) & 255
            elif method == 2:
                current[x] = (current[x] + above) & 255
            elif method == 3:
                current[x] = (current[x] + ((left + above) // 2)) & 255
            elif method == 4:
                estimate = left + above - diagonal
                distances = (abs(estimate - left), abs(estimate - above), abs(estimate - diagonal))
                current[x] = (current[x] + (left, above, diagonal)[distances.index(min(distances))]) & 255
            elif method != 0:
                raise ValueError("Unsupported PNG filter")
        rows.append(current)
        previous = current
    palette = chunks.get(b"PLTE", [b""])[0]
    transparency = chunks.get(b"tRNS", [b""])[0]
    pixels = []
    for row in rows:
        values = []
        if depth == 8:
            values = list(row)
        else:
            mask = (1 << depth) - 1
            for byte in row:
                for shift in range(8 - depth, -1, -depth):
                    values.append((byte >> shift) & mask)
        output = []
        for x in range(width):
            if colour == 3:
                index = values[x]
                if index * 3 + 3 > len(palette):
                    raise ValueError("Invalid PNG palette index")
                rgb = tuple(palette[index * 3:index * 3 + 3])
                alpha = transparency[index] if index < len(transparency) else 255
                output.append((*rgb, alpha))
            elif colour == 0:
                gray = values[x]
                output.append((gray, gray, gray, 255))
            elif colour == 2:
                output.append((*values[x * 3:x * 3 + 3], 255))
            elif colour == 4:
                gray, alpha = values[x * 2:x * 2 + 2]
                output.append((gray, gray, gray, alpha))
            else:
                output.append(tuple(values[x * 4:x * 4 + 4]))
        pixels.append(output)
    return width, height, pixels


def png_bytes(width: int, height: int, pixels: bytearray) -> bytes:
    if len(pixels) != width * height * 4:
        raise ValueError("Invalid RGBA buffer")
    def chunk(kind, payload):
        return len(payload).to_bytes(4, "big") + kind + payload + (zlib.crc32(kind + payload) & 0xFFFFFFFF).to_bytes(4, "big")
    samples = memoryview(pixels).cast("I")
    palette = {}
    for sample in samples:
        if sample not in palette:
            palette[sample] = len(palette)
            if len(palette) > 256:
                break
    extra = b""
    depth, colour, level = 8, 6, 6
    stride = width * 4
    if len(palette) <= 256:
        depth = next(bits for bits in (1, 2, 4, 8) if len(palette) <= 1 << bits)
        colour, level = 3, 9
        colours = [struct.pack("=I", sample) for sample in palette]
        extra = chunk(b"PLTE", b"".join(sample[:3] for sample in colours))
        alpha = bytes(sample[3] for sample in colours).rstrip(b"\xff")
        if alpha:
            extra += chunk(b"tRNS", alpha)
        indices = bytes(map(palette.__getitem__, samples))
        stride = (width * depth + 7) // 8
        if depth == 8:
            pixels = indices
        else:
            pixels = bytearray(stride * height)
            for y in range(height):
                for x in range(width):
                    pixels[y * stride + x * depth // 8] |= indices[y * width + x] << (8 - depth - x * depth % 8)
    raw = b"".join(b"\0" + bytes(pixels[y * stride:(y + 1) * stride]) for y in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth, colour, 0, 0, 0)) \
        + extra + chunk(b"IDAT", zlib.compress(raw, level)) + chunk(b"IEND", b"")


def glyph_advances(font):
    """Advance each glyph past its ink, keeping one column apart."""
    cell = font["width"]
    advances = {}
    for char, rows in font["glyphs"].items():
        ink = 0
        for bits in rows:
            ink |= bits
        advances[char] = cell - (ink & -ink).bit_length() + 2 if ink else cell // 3
    return advances


class Canvas:
    def __init__(self, font, advances=None):
        self.pixels = bytearray((244, 241, 232, 255)) * (WIDTH * HEIGHT)
        self.font = font
        self.advances = advances or glyph_advances(font)

    def rectangle(self, x, y, width, height, colour):
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(WIDTH, x + width), min(HEIGHT, y + height)
        row = bytes(colour) * (x1 - x0)
        for yy in range(y0, y1):
            at = (yy * WIDTH + x0) * 4
            self.pixels[at:at + len(row)] = row

    def rounded(self, x, y, width, height, radius, colour):
        for dy in range(height):
            inset = 0
            if dy < radius or dy >= height - radius:
                cy = radius - dy - 1 if dy < radius else dy - (height - radius)
                inset = radius - int((radius * radius - cy * cy) ** 0.5)
            self.rectangle(x + inset, y + dy, width - 2 * inset, 1, colour)

    def circle(self, cx, cy, radius, colour):
        for dy in range(-radius, radius + 1):
            half = int((radius * radius - dy * dy) ** 0.5)
            self.rectangle(cx - half, cy + dy, 2 * half + 1, 1, colour)

    def measure(self, value, scale=1):
        return sum(self.advances.get(char, self.advances["?"]) for char in value) * scale

    def fit(self, value, scale, max_width=None):
        if max_width is not None and self.measure(value, scale) > max_width:
            scale = max(1, max_width // max(1, self.measure(value)))
        return scale

    def text(self, x, y, value, scale, colour, max_width=None, right=False):
        glyphs = self.font["glyphs"]
        cell = self.font["width"]
        scale = self.fit(value, scale, max_width)
        width = self.measure(value, scale)
        if right:
            x -= width
        for char in value:
            rows = glyphs.get(char, glyphs["?"])
            for yy, bits in enumerate(rows):
                for xx in range(cell):
                    if bits & (1 << (cell - xx - 1)):
                        self.rectangle(x + xx * scale, y + yy * scale, scale, scale, colour)
            x += self.advances.get(char, self.advances["?"]) * scale
        return width

    def sprite(self, path, x, y, target):
        width, height, source = png_pixels(Path(path).read_bytes())
        scale = max(1, min(target // width, target // height))
        ox, oy = x + (target - width * scale) // 2, y + (target - height * scale) // 2
        for sy, row in enumerate(source):
            for sx, colour in enumerate(row):
                if colour[3]:
                    self.rectangle(ox + sx * scale, oy + sy * scale, scale, scale, colour)

    def save(self):
        return png_bytes(WIDTH, HEIGHT, self.pixels)


def chip_text(colour):
    red, green, blue = (int(colour[index:index + 2], 16) for index in (1, 3, 5))
    return (17, 17, 17, 255) if 0.299 * red + 0.587 * green + 0.114 * blue > 150 else (255, 255, 255, 255)


class ShareGenerator:
    def __init__(self, data: Path, out: Path, public_base: str, profile: str, application: str | None = None):
        self.data = Path(data).resolve()
        self.out = Path(out).resolve()
        self.profile = profile
        if not ID.fullmatch(profile):
            raise ValueError("Invalid profile id")
        from .sitepreview import public_url
        self.base = public_url(public_base)
        self.application = public_url(application) or "../../../../../"
        self.manifest = read_json(self.data / "languages.json")
        self.profile_row = next((row for row in self.manifest.get("profiles", []) if row.get("id") == profile), None)
        if self.profile_row is None:
            raise ValueError("Unknown share profile")
        self.dataset_path = self.data / self.profile_row["pokedex"]
        self.dataset = pokedexstore.load(self.dataset_path)
        self.validate_dataset()
        self.records = self.dataset["species"]
        self.types = {row["id"]: row for row in self.dataset.get("types", [])}
        self.languages = [row for row in self.manifest.get("languages", []) if row.get("profile") == profile]
        if not self.languages:
            raise ValueError("Profile has no languages")
        self.texts, self.pages, self.site = {}, {}, {}
        for row in self.languages:
            code = row["code"]
            if not LANGUAGE.fullmatch(code) or not row.get("texts"):
                raise ValueError(f"Invalid published text language: {code}")
            document = pokedextexts.read(self.data / row["texts"])
            if (document.get("schema") not in pokedextexts.SCHEMAS or document.get("profile") != profile
                    or document.get("language") != code):
                raise ValueError(f"Text identity mismatch: {code}")
            if document.get("dataset") != self.profile_row["pokedex"]:
                raise ValueError(f"Text dataset mismatch: {code}")
            self.texts[code] = document.get("texts", {})
            self.pages[code] = document.get("page", {})
            site_path = self.data / "site" / f"{code}.json"
            site = read_json(site_path) if site_path.is_file() else {}
            self.site[code] = site.get("strings", {}) if site.get("language") == code else {}
        first = self.languages[0]["code"]
        self.texts["en"] = self.texts[first]
        self.pages["en"] = self.pages[first]
        self.site["en"] = {}
        # key-only data built without the ROM holds no English
        self.english = any(pair and pair[0] for pair in self.texts["en"].values())
        self.font = read_json(FONT_PATH)
        self.advances = glyph_advances(self.font)
        self.tiers, self.tier_ink = tier_colours(SITE_CSS.read_text(encoding="utf-8"))
        self.ranks = stat_ranks(self.records)

    def validate_dataset(self):
        expected = f"pokedex/{self.profile}/pokedex.json"
        if self.dataset.get("format") != pokedexdata.FORMAT or self.dataset.get("schema") not in pokedexdata.SCHEMAS:
            raise ValueError("Unknown Pokédex dataset format")
        if self.profile_row.get("pokedex") != expected or not self.profile_row.get("name"):
            raise ValueError("Dataset is not declared by the profile")
        if self.dataset.get("profile", {}).get("id") != self.profile:
            raise ValueError("Dataset profile identity mismatch")
        records = self.dataset.get("species")
        if not isinstance(records, list) or not records or self.dataset.get("count") != len(records):
            raise ValueError("Invalid species records")
        slots = set()
        for record in records:
            slot = record.get("slot")
            if type(slot) is not int or slot < 1 or slot in slots:
                raise ValueError("Invalid or duplicate species slot")
            slots.add(slot)
            if type(record.get("dex")) is not int or record["dex"] < 0 or not record.get("name"):
                raise ValueError(f"Invalid species record: {slot}")
            if not isinstance(record.get("types"), list):
                raise ValueError(f"Invalid species types: {slot}")
        for row in self.dataset.get("types", []):
            if type(row.get("id")) is not int or not COLOUR.fullmatch(row.get("colour", "")):
                raise ValueError("Invalid type color record")

    def sprite_path(self, record):
        relative = record.get("sprite", {}).get("front")
        if not isinstance(relative, str) or relative.startswith("/") or ".." in Path(relative).parts:
            raise ValueError(f"Invalid sprite path: {record['slot']}")
        root = (self.dataset_path.parent / "sprites").resolve()
        path = (root / relative).resolve()
        if root not in path.parents or not path.is_file():
            raise ValueError(f"Missing sprite: {record['slot']}")
        return path

    def entry(self, language, key):
        pair = self.texts[language].get(key)
        if not pair:
            return ""
        english, translation = (list(pair) + ["", ""])[:2]
        value = english if language == "en" else translation or english
        return (value or "").replace("{PK}{MN}", "Pokémon")

    def ui(self, language, key, variables=None):
        value = self.site.get(language, {}).get(key, key)
        return self.fill(value, variables)

    def label(self, language, key, variables=None):
        mapped = self.dataset.get("labels", {}).get(key)
        if mapped is not None:
            if not CATALOG_ID.fullmatch(mapped):
                return self.fill(mapped, variables)
            catalog_key, separator, rule = mapped.partition("@")
            pair = self.texts[language].get(catalog_key, ["", ""])
            for value in ([pair[0]] if language == "en" else [pair[1], pair[0]]):
                value = fragment_of(value, rule) if value and separator else value
                if value:
                    return game_label(value, variables)
            return self.ui(language, key, variables)
        page = self.pages[language].get(key)
        if isinstance(page, list):
            value = page[0] if language == "en" else page[1] or page[0]
            if value:
                return self.fill(value, variables)
        elif page and language != "en":
            return self.fill(page, variables)
        return self.ui(language, key, variables)

    @staticmethod
    def fill(value, variables):
        return re.sub(r"\{(\w+)\}", lambda match: str((variables or {}).get(match[1], match[0])), value)

    def form_label(self, language, record):
        form_name = record.get("form_name")
        if form_name:
            label = self.label(language, form_name[5:]) if form_name.startswith("page:") else self.entry(language, form_name)
            if label:
                return label
        family = [row for row in self.records if row["dex"] == record["dex"]]
        if not record["dex"] > 0 or len(family) < 2 or record["slot"] == min(row["slot"] for row in family):
            return ""
        for base in self.records:
            if base["slot"] >= record["slot"]:
                continue
            for edge in base.get("evolution") or []:
                if edge.get("target") != record["slot"]:
                    continue
                if edge.get("method") == 253:
                    return self.label(language, "Giga")
                if edge.get("method") == 254 and edge.get("parameter", 0) > 0 and edge.get("kind", "mega") == "mega":
                    item = self.entry(language, f"items:{edge['parameter']}")
                    letter = re.search(r"\s([XY])$", item)
                    return self.label(language, "Mega") + (" " + letter.group(1) if letter else "")
        return self.ui(language, "Form {n}", {"n": record["slot"]})

    def mega_letter(self, language, record):
        family = [row for row in self.records if row["dex"] == record["dex"]]
        if not record["dex"] > 0 or len(family) < 2:
            return None
        for base in self.records:
            if base["slot"] >= record["slot"]:
                continue
            for edge in base.get("evolution") or []:
                if (edge.get("target") == record["slot"] and edge.get("method") == 254
                        and edge.get("parameter", 0) > 0 and edge.get("kind", "mega") == "mega"):
                    item = self.entry(language, f"items:{edge['parameter']}")
                    letter = re.search(r"\s([XY])$", item)
                    return letter.group(1) if letter else ""
        return None

    def display_name(self, language, record):
        label = self.form_label(language, record)
        if label and record.get("form_name_full"):
            return label
        mega = self.mega_letter(language, record)
        if mega is not None:
            name = self.label(language, "Mega {species}", {"species": self.entry(language, record["name"])})
            return name + (" " + mega if mega else "")
        return self.entry(language, record["name"]) + (" " + label if label else "")

    def species(self, language, record):
        types = []
        for type_id in record["types"]:
            if type_id not in self.types:
                raise ValueError(f"Unknown type {type_id} at slot {record['slot']}")
            append_unique(types, type_id)
        rows = [{"id": type_id, "name": self.entry(language, f"types:{type_id}"), "colour": self.types[type_id]["colour"]}
                for type_id in types]
        if not self.english and (not self.entry(language, record["name"]) or any(not row["name"] for row in rows)):
            return None   # no English to fall back on
        if any(not row["name"] for row in rows):
            raise ValueError(f"Missing type name at slot {record['slot']}")
        name = self.display_name(language, record)
        if not name:
            raise ValueError(f"Missing species name at slot {record['slot']}")
        number = f"#{record['slot']:03d}" if record["dex"] > 0 else ""   # placeholders stay unnumbered
        return {"slot": record["slot"], "dex": record["dex"], "number": number, "name": name,
                "types": rows, "stats": self.stat_rows(language, record), "sprite": self.sprite_path(record)}

    def stat_rows(self, language, record):
        stats = record.get("base_stats") or {}
        rows = []
        for key in STAT_ORDER:
            value = stats.get(key)
            if value is None:
                continue
            if type(value) is not int or not 0 <= value <= STAT_MAX:
                raise ValueError(f"Invalid base stat {key} at slot {record['slot']}")
            rows.append({"key": key, "label": self.label(language, STAT_LABELS[key]), "value": value})
        if rows:
            rows.append({"key": "total", "label": self.label(language, "Total"),
                         "value": sum(row["value"] for row in rows)})
        for row in rows:
            tier = tier_of(self.ranks[row["key"]], row["value"])
            if tier is not None:
                row["tier"] = tier
                if row["key"] == "total":
                    row["tier_name"] = self.label(language, TIER_LABELS[tier])
        return rows

    def image(self, species):
        canvas = Canvas(self.font, self.advances)
        ink, muted, accent, panel = (24, 36, 60, 255), (82, 100, 93, 255), (49, 92, 80, 255), (226, 235, 229, 255)
        canvas.rounded(808, 160, 344, 422, 32, panel)
        canvas.circle(980, 371, 156, (248, 250, 246, 255))
        canvas.sprite(species["sprite"], 820, 211, 320)
        canvas.text(48, 16, species["number"] or "#—", 3, accent, 300)
        canvas.text(1152, 27, f"Pokédex · {self.profile_row['name']}", 2, muted, 700, right=True)
        canvas.text(48, 72, species["name"], 4, ink, 1104)
        x = 48
        for row in species["types"]:
            width = canvas.measure(row["name"], 3) + 40
            canvas.rounded(x, 160, width, 54, 27, rgba(row["colour"]))
            canvas.text(x + 20, 153, row["name"], 3, chip_text(row["colour"]), width - 40)
            x += width + 14
        # Labels share one column; values end at a fixed edge.
        labels = min(330, max((canvas.measure(row["label"], 3) for row in species["stats"]), default=0))
        start, end = 48 + labels + 28, 776 - canvas.measure("255", 3) - 24
        for index, row in enumerate(species["stats"]):
            top = 232 + index * 50
            canvas.text(48, top - 6, row["label"], 3, ink, labels)
            canvas.text(776, top - 6, str(row["value"]), 3, ink, right=True)
            colour = self.tiers[row["tier"]] if "tier" in row else accent
            if row["key"] == "total":
                canvas.rectangle(48, top - 1, 728, 2, panel)
                if "tier_name" in row:
                    width = min(end - start, canvas.measure(row["tier_name"], 2) + 32)
                    canvas.rounded(start, top + 7, width, 36, 18, colour)
                    canvas.text(start + 16, top + 4, row["tier_name"], 2, self.tier_ink, width - 32)
                continue
            canvas.rounded(start, top + 14, end - start, 22, 11, panel)
            canvas.rounded(start, top + 14, max(22, round(row["value"] / STAT_MAX * (end - start))), 22, 11, colour)
        canvas.text(1152, 590, "gen3tl", 1, muted, right=True)
        return canvas.save()

    def generate(self):
        share = self.out / "share"
        if (share / self.profile).exists():
            shutil.rmtree(share / self.profile)
        species_by_language = {language: [self.species(language, record) for record in self.records]
                               for language in ["en", *(row["code"] for row in self.languages)]}
        source = self.languages[0]["code"]
        available = {}
        images = {}
        pages = []
        for language, species_rows in species_by_language.items():
            source_language = source if language == "en" else language
            slots = []
            for species, english in zip(species_rows, species_by_language["en"]):
                if species is None:
                    continue
                if (language != "en" and species == english
                        and self.ui(language, "Open the Pokédex") == self.ui(source, "Open the Pokédex")):
                    continue
                slots.append(species["slot"])
                semantic = {"game": self.profile_row["name"], "number": species["number"], "name": species["name"],
                            "types": species["types"], "stats": species["stats"], "tiers": [self.tiers, self.tier_ink],
                            "sprite": sha256(species["sprite"]), "font": sha256(FONT_PATH)}
                key = hashlib.sha256(json.dumps(semantic, ensure_ascii=False, sort_keys=True,
                                                separators=(",", ":")).encode()).hexdigest()
                if key not in images:
                    data = self.image(species)
                    digest = hashlib.sha256(data).hexdigest()
                    images[key] = (digest, data)
                pages.append((language, source_language, species, images[key][0]))
            available[language] = "all" if len(slots) == len(self.records) else sorted(slots)
        for language, source_language, species, image in pages:
            filename = f"{image}.png"
            target = share / "assets" / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_bytes(next(data for digest, data in images.values() if digest == image))
            type_text = " / ".join(row["name"] for row in species["types"])
            stat_text = " · ".join(f"{row['label']} {row['value']}" for row in species["stats"])
            title = " · ".join(filter(None, [f"{species['number']} {species['name']}".strip(), type_text, self.profile_row["name"]]))
            description = " · ".join(filter(None, ["Pokédex " + species["number"] if species["number"] else "Pokédex", type_text,
                                                   stat_text]))
            relative = f"share/{self.profile}/{language}/species/{species['slot']}/"
            canonical = self.base + relative
            image_url = self.base + f"share/assets/{filename}"
            alt = " · ".join(filter(None, [species["number"], species["name"], type_text, stat_text]))
            destination = f"{self.application}pokedex?share=1#{self.profile}/{language}/species/{species['slot']}"
            document = f'''<!doctype html>
<html lang="{esc(language)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{esc(canonical)}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Pokédex · gen3tl">
<meta property="og:locale" content="{esc(self.profile_row.get('source_locale', language) if language == 'en' else language_metadata.load(language).data.get('locale', language))}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{esc(canonical)}">
<meta property="og:image" content="{esc(image_url)}">
<meta property="og:image:type" content="image/png">
<meta property="og:image:width" content="{WIDTH}">
<meta property="og:image:height" content="{HEIGHT}">
<meta property="og:image:alt" content="{esc(alt)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{esc(title)}">
<meta name="twitter:description" content="{esc(description)}">
<meta name="twitter:image" content="{esc(image_url)}">
<meta name="twitter:image:alt" content="{esc(alt)}">
<link rel="stylesheet" href="../../../../../site.css">
<link rel="stylesheet" href="../../../../../share-target.css">
<script src="../../../../../share-target.js" data-destination="{esc(destination)}"></script>
</head>
<body>
<main>
<h1>{esc(title)}</h1>
<p>{esc(description)}</p>
<a id="share-destination" href="{esc(destination)}">{esc(self.ui(source_language, 'Open the Pokédex'))}</a>
<img src="../../../../assets/{filename}" width="{WIDTH}" height="{HEIGHT}" alt="{esc(alt)}">
</main>
</body>
</html>
'''
            path = self.out / relative / "index.html"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(document, encoding="utf-8")
        unique_images = {image for _, _, _, image in pages}
        return {"profile": self.profile, "page_count": len(pages), "image_count": len(unique_images),
                "share_pages": available}


def generate(data: Path, out: Path, public_base: str, profiles=None, application: str | None = None):
    manifest = read_json(Path(data) / "languages.json")
    selected = [row["id"] for row in manifest.get("profiles", []) if row.get("pokedex")]
    if profiles is not None:
        selected = [profile for profile in selected if profile in profiles]
    share = Path(out) / "share"
    if share.exists():
        shutil.rmtree(share)
    return [ShareGenerator(data, out, public_base, profile, application).generate() for profile in selected]
