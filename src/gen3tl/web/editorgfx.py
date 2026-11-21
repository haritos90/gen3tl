"""The editor's graphics with text pixels, run in the browser with Pyodide."""

from __future__ import annotations

import base64
import json
import struct
import zlib
from dataclasses import asdict
from pathlib import Path

from ..build.graphics import (GraphicsConfig, GraphicsError, Sheet, append_problem, apply_area_form, area_form,
                       area_form_png, area_png, check_report, hex_rgb, inventory_of, inventory_rows, parse_area,
                       pixel_check, png_depth, lz77_decompress, palette_colours, preview_views, Cell)
from ..catalog.source import digest as text_digest
from ..check.render import PngImage, indexed_png, png_image, read_png_image
from ..core.rom import Rom


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def flat(rows: list[list[int]]) -> bytes:
    """Palette indices row by row, one byte each."""
    return bytes(v for row in rows for v in row)


def read_png(data: bytes, name: str) -> PngImage:
    """An uploaded PNG; a broken file is a ValueError naming it."""
    try:
        return png_image(data, name)
    except (ValueError, zlib.error):
        raise
    except Exception as e:   # truncated chunks or rows
        raise ValueError(f"{name}: unreadable PNG ({e})") from None


def graphics_data(section: dict | None, root, release=None, catalog=None, key=lambda sid: sid) -> dict | None:
    """The editor data's graphics from the profile's [graphics] section, None without an inventory."""
    if not section or "inventory" not in section:
        return None
    by_text = {}
    if catalog is not None:
        for sid, (_, e) in catalog.items():
            if not e.keyed:
                by_text.setdefault(e.msgid, []).append(sid)
    inventory = []
    for r in inventory_rows(Path(root) / section["inventory"]):
        row = {k: v for k, v in r.items() if k not in ("image", "source_text")}
        sids = by_text.get(r.get("source_text", ""), [])
        if sids and len(sids) == 1:
            row["source_key"] = key(sids[0])
        else:
            row["source_digest"] = text_digest(r.get("source_text", ""))
        inventory.append(row)
    rows = inventory
    cfg = GraphicsConfig(section, root, inventory_of(rows), release)
    edits = {}
    for e in cfg.edits:
        g = cfg.graphic(e.id)
        if e.label is not None:
            edits[e.id] = {"areas": [list(a) for a in e.areas], "tiles": e.tiles,
                           "evidence": e.evidence, "label": e.label}
            continue
        img = read_png_image(e.png)
        if img.indices is None:
            raise GraphicsError(f"graphic {e.id}: {e.png} is not an indexed PNG; save it with its palette")
        px = img.indices if e.form == "areas" else area_form(img.indices, e.areas, cfg.sheet_width(g), g.tiles,
                                                              e.tiles)
        edits[e.id] = {"areas": [list(a) for a in e.areas], "tiles": e.tiles, "evidence": e.evidence,
                       "png": b64(area_png(px, g.bpp))}
    for gid, holder in cfg.shared_edits(edits).items():
        edits[gid] = {**edits[holder], "shared": holder}
    tilemaps = [{"name": m.name, "graphic": m.graphic, "cells": len(m.cells), "evidence": m.evidence}
                for m in cfg.tilemaps]
    previews = preview_views(Path(root) / section["previews"]) if section.get("previews") else {}
    return {"inventory": rows, "sheet_width": {k: int(v) for k, v in cfg.widths.items()},
            "palette": {k: (v["at"] if isinstance(v, dict) else str(v)) for k, v in cfg.palettes.items()},
            "edits": edits, "tilemaps": tilemaps, "previews": previews,
            "tilemap_edits": [{"offset": m.offset, "width": m.width,
                               "cells": [asdict(c) for c in m.cells]} for m in cfg.tilemaps]}


def _obj(x):
    return json.loads(x) if isinstance(x, str) else x


class Pictures:
    """The graphics of the editor data against the user's ROM."""

    def __init__(self, data, rom: bytes | None = None):
        data = _obj(data)
        section = {"sheet_width": data.get("sheet_width", {}), "palette": data.get("palette", {})}
        self.cfg = GraphicsConfig(section, Path("."), inventory_of(data["inventory"]))
        self.edits = data.get("edits", {})
        self.previews = data.get("previews", {})
        self.tilemaps = data.get("tilemap_edits", [])
        self.rom = Rom(rom) if rom is not None else None
        self._sheets: dict[str, Sheet] = {}

    def sheet(self, gid: str) -> Sheet:
        if self.rom is None:
            raise GraphicsError("no ROM loaded")
        if gid not in self._sheets:
            self._sheets[gid] = self.cfg.sheet(self.rom, self.cfg.graphic(gid))
        return self._sheets[gid]

    def sheet_data(self, gid: str) -> dict:
        s = self.sheet(gid)
        g = s.graphic
        return {"id": gid, "width": s.width, "height": s.height, "width_tiles": s.width_tiles, "tiles": g.tiles,
                "bpp": g.bpp, "palette": [hex_rgb(c) for c in s.palette], "palette_source": s.palette_source,
                "pixels": b64(flat(s.pixels))}

    def png(self, gid: str) -> bytes:
        """The PNG build gfx-extract writes (graphics.write_sheet)."""
        s = self.sheet(gid)
        return indexed_png(s.pixels, s.palette, png_depth(s.palette))

    def edited(self, gid: str, png: bytes | None = None, areas=None, tiles: int | None = None) -> dict:
        """An area form over the sheet, pixel-checked as the build checks it; `png` defaults to the project's edit."""
        s = self.sheet(gid)
        if png is None:
            e = self.edits.get(gid)
            if e is None:
                return {"id": gid, "findings": [f"{gid} has no edit in the project"], "pixels": None}
            areas, tiles = e["areas"], e["tiles"]
            if "label" in e:
                from ..build.graphiclabel import image
                png = area_png(image(s, e["label"], tiles).indices, s.graphic.bpp)
            else:
                png = base64.b64decode(e["png"])
        g = s.graphic
        n = g.tiles if tiles is None else tiles
        out = {"id": gid, "areas": [], "tiles": n, "findings": [], "changed": 0, "pixels": None}
        try:
            areas = [parse_area(a) for a in areas or []]
        except GraphicsError as x:
            return {**out, "findings": [str(x)]}
        out["areas"] = [list(a) for a in areas]
        problem = append_problem(g, n, len(s.data))
        if problem:
            return {**out, "findings": [problem]}
        try:
            img, findings = apply_area_form(s, read_png(png, f"{gid} area form"), areas, n)
        except (ValueError, zlib.error) as x:
            return {**out, "findings": [str(x)]}
        if img is None:
            return {**out, "findings": findings}
        chk = pixel_check(s, img, areas, n)
        rows = chk.indices
        return {**out, "findings": findings + chk.findings, "changed": chk.changed_inside + chk.changed_appended,
                "width": len(rows[0]), "height": len(rows), "pixels": b64(flat(rows)),
                "png": b64(indexed_png(rows, s.palette, png_depth(s.palette)))}

    def check(self, gid: str, png: bytes, name: str, areas, tiles: int | None = None) -> dict:
        """build gfx-check of an uploaded sheet; where it passes, its area form (area_png)."""
        out = {"id": gid, "code": 2, "lines": [], "findings": [], "areas": [], "tiles": None, "changed": 0,
               "pixels": None, "area_png": None}
        if self.rom is None:
            return {**out, "lines": ["FAIL     no ROM loaded"], "findings": ["no ROM loaded"]}
        rep = check_report(self.cfg, self.rom, gid, lambda: read_png(png, name), areas, tiles)
        chk = rep.result
        grown = rep.sheet is not None and rep.tiles != rep.sheet.graphic.tiles   # else no tile count
        out.update(code=rep.code, lines=rep.lines, areas=[list(a) for a in rep.areas],
                   tiles=rep.tiles if grown else None,
                   findings=chk.findings if chk else [line[9:] for line in rep.lines if line.startswith("FAIL")])
        if chk is not None and chk.indices is not None:
            rows = chk.indices
            out.update(width=len(rows[0]), height=len(rows), pixels=b64(flat(rows)),
                       changed=chk.changed_inside + chk.changed_appended)
            if rep.code == 0:
                out["area_png"] = b64(area_form_png(rep.sheet, rows, rep.areas, rep.tiles))
        return out

    def preview(self, gid: str, png: bytes | None = None, areas=None,
                tiles: int | None = None, edited: bool = False) -> list[dict]:
        """Compose screen layouts from local ROM pixels."""
        s = self.sheet(gid)
        pixels = s.pixels
        available_tiles = s.graphic.tiles
        if edited:
            result = self.edited(gid, png, areas, tiles)
            if result["findings"] or result["pixels"] is None:
                raise GraphicsError("; ".join(result["findings"]))
            raw = base64.b64decode(result["pixels"])
            pixels = [list(raw[i:i + s.width]) for i in range(0, len(raw), s.width)]
            available_tiles = result["tiles"]
        views = []
        for view in self.previews.get(gid, []):
            m = view.get("map")
            full_map = m is not None and m.get("full", False)
            if full_map:
                if m["width"] <= 0 or m["size"] <= 0 or m["size"] % (2 * m["width"]):
                    raise GraphicsError(f"{gid}: invalid full tilemap geometry")
                if "origin" in m or "width" in view or "height" in view:
                    raise GraphicsError(f"{gid}: full tilemap cannot specify a crop")
                width, height = m["width"] * 8, m["size"] // (2 * m["width"]) * 8
            else:
                width, height = view["width"], view["height"]
            if not 0 < width <= 1024 or not 0 < height <= 1024:
                raise GraphicsError(f"{gid}: invalid preview dimensions")
            colours = palette_colours(self.rom, view["palette"], s.graphic.bpp)[0] if "palette" in view else s.palette
            palettes = view.get("palettes")
            if palettes is not None:
                if s.graphic.bpp != 4 or not 1 <= len(palettes) <= 16:
                    raise GraphicsError(f"{gid}: invalid preview palette banks")
                colours = [colour for spec in palettes
                           for colour in palette_colours(self.rom, spec, 4)[0]]
            # Transparent areas use a neutral background.
            rows = [[0] * width for _ in range(height)]
            cells = view.get("cells", [])
            if "map" in view:
                m = view["map"]
                raw = lz77_decompress(self.rom.data, m["offset"])[0]
                if len(raw) != m["size"]:
                    raise GraphicsError(f"{gid}: preview tilemap size mismatch")
                entries = list(struct.unpack(f"<{len(raw) // 2}H", raw))
                if edited:
                    for change in self.tilemaps:
                        if change["offset"] != m["offset"]:
                            continue
                        if change["width"] != m["width"]:
                            raise GraphicsError(f"{gid}: preview tilemap width mismatch")
                        for c in change["cells"]:
                            cell = Cell(**c)
                            index = cell.row * m["width"] + cell.column
                            entries[index] = cell.apply(entries[index])
                ox, oy = (0, 0) if full_map else m["origin"]
                cells = []
                for y in range(oy // 8, (oy + height + 7) // 8):
                    for x in range(ox // 8, (ox + width + 7) // 8):
                        index = y * m["width"] + x
                        if not 0 <= x < m["width"] or not 0 <= index < len(entries):
                            raise GraphicsError(f"{gid}: preview outside tilemap")
                        entry = entries[index]
                        cells.append([x * 8 - ox, y * 8 - oy, entry & 1023, (entry >> 10) & 3])
            for cell in cells:
                if len(cell) not in (4, 5):
                    raise GraphicsError(f"{gid}: invalid preview cell")
                x, y, tile, flips = cell[:4]
                bank = cell[4] if len(cell) == 5 else 0
                if not 0 <= bank < (len(palettes) if palettes else 1):
                    raise GraphicsError(f"{gid}: missing preview palette bank {bank}")
                sx, sy = tile % s.width_tiles * 8, tile // s.width_tiles * 8
                if not 0 <= tile < available_tiles:
                    raise GraphicsError(f"{gid}: preview references missing tile {tile}")
                for dy in range(max(0, -y), min(8, height - y)):
                    for dx in range(max(0, -x), min(8, width - x)):
                        value = pixels[sy + (7 - dy if flips & 2 else dy)][sx + (7 - dx if flips & 1 else dx)]
                        if value or not view.get("transparent", False):
                            rows[y + dy][x + dx] = value + bank * 16 if value else 0
            for sx, sy, w, h, x, y in view.get("rects", []):
                if min(sx, sy, x, y) < 0 or sx + w > s.width or sy + h > len(pixels) or x + w > width or y + h > height:
                    raise GraphicsError(f"{gid}: preview rectangle outside image")
                for dy in range(h):
                    rows[y + dy][x:x + w] = pixels[sy + dy][sx:sx + w]
            # colour 0 shows the page background
            background = "#000000" if view.get("background") == "black" else "#20242b"
            palette = [background, *[hex_rgb(c) for c in colours[1:]]]
            views.append({"label": view["label"], "width": width, "height": height,
                          "pixels": b64(flat(rows)), "palette": palette})
        return views

    # JSON for the page
    def sheet_json(self, gid: str) -> str:
        return json.dumps(self.sheet_data(gid))

    def png_b64(self, gid: str) -> str:
        return b64(self.png(gid))

    def edited_json(self, gid: str, png: str | None = None, areas=None, tiles: int | None = None) -> str:
        return json.dumps(self.edited(gid, None if png is None else base64.b64decode(png),
                                      _obj(areas) if areas is not None else None, tiles), ensure_ascii=False)

    def check_json(self, gid: str, png: str, name: str, areas, tiles: int | None = None) -> str:
        return json.dumps(self.check(gid, base64.b64decode(png), name, _obj(areas), tiles), ensure_ascii=False)

    def preview_json(self, gid: str, png: str | None = None, areas=None,
                     tiles: int | None = None, edited: bool = False) -> str:
        return json.dumps(self.preview(gid, None if png is None else base64.b64decode(png),
                                       _obj(areas) if areas is not None else None, tiles, edited))
