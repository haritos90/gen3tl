// Graphics with text edited through Python in the browser.
// The check bundle extracts each graphic from the user's ROM.
// The ROM stays in this tab.

import { fromB64, toB64 } from "./pictures.js";

const GLUE = `
import sys
if "/bundle" not in sys.path:
    sys.path.insert(0, "/bundle")
from gen3tl.web import editorgfx

_pictures = None


def gfx_load(data, rom):
    global _pictures
    _pictures = editorgfx.Pictures(data, None if rom is None else rom.to_bytes())


def gfx_sheet(gid):
    return _pictures.sheet_json(gid)


def gfx_png(gid):
    return _pictures.png_b64(gid)


def gfx_edited(gid, png, areas, tiles):
    return _pictures.edited_json(gid, png, areas, tiles)


def gfx_check(gid, png, name, areas, tiles):
    return _pictures.check_json(gid, png, name, areas, tiles)


def gfx_preview(gid, png, areas, tiles, edited):
    return _pictures.preview_json(gid, png, areas, tiles, edited)
`;

// JS null reaches Python as JsNull, undefined as None
const none = (v) => v ?? undefined;

/** Base64 fields of a result as bytes. */
function decode(res) {
  for (const k of ["pixels", "png", "area_png"]) if (typeof res[k] === "string") res[k] = fromB64(res[k]);
  return res;
}

export class Gfx {
  /** `bundle`: the zip to unpack, unless Check did. */
  constructor(py, bundle = null) {
    this.py = py;
    if (bundle) py.unpackArchive(bundle, "zip", { extractDir: "/bundle" });
    py.runPython(GLUE);
    this.fn = Object.fromEntries(["load", "sheet", "png", "edited", "check", "preview"].map((n) => [n, py.globals.get(`gfx_${n}`)]));
    this.rom = false;
    this.loaded = false;
  }

  /** The editor data's graphics and the ROM (Uint8Array) or null. */
  load(graphics, rom = null) {
    this.loaded = false;
    this.fn.load(JSON.stringify(graphics), none(rom));
    this.rom = Boolean(rom);
    this.loaded = true;
  }

  /** The extracted sheet: {width, height, palette, pixels, ...}. */
  sheet(id) {
    return decode(JSON.parse(this.fn.sheet(id)));
  }

  /** The PNG gfx-extract writes, as bytes. */
  png(id) {
    return fromB64(this.fn.png(id));
  }

  /** An area form over the sheet. */
  edited(id, areaPng = null, areas = null, tiles = null) {
    const png = areaPng ? toB64(areaPng) : undefined;
    return decode(JSON.parse(this.fn.edited(id, png, areas ? JSON.stringify(areas) : undefined, none(tiles))));
  }

  /** gfx-check of an uploaded sheet (bytes). */
  check(id, png, name, areas, tiles = null) {
    return decode(JSON.parse(this.fn.check(id, toB64(png), name, JSON.stringify(areas), none(tiles))));
  }

  /** Screen layouts, composed entirely from local ROM pixels. */
  preview(id, edited = false, areaPng = null, areas = null, tiles = null) {
    return JSON.parse(this.fn.preview(id, areaPng ? toB64(areaPng) : undefined,
      areas ? JSON.stringify(areas) : undefined, none(tiles), edited)).map(decode);
  }
}
